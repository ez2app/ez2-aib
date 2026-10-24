#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 追蹤鉤子的寫法(10-24 @044e0 本機效能):每個行程留一個 O_APPEND fd、每筆一次 write,不再每筆開關檔。
以前每筆 open("a") → 寫 → 關,APFS 每次關檔把那一塊重寫一次(每筆多寫約 10 KB);一天 3500 支次測試 ⇒ 1.1 TB。
F1 Python:2000 筆事件,子行程開追蹤檔 ≤ 2 次(跨平台,第二個 audit hook 數);macOS 另驗實際寫入 < 追蹤檔 5 倍
F2 Python:測試關掉追蹤 fd、同號碼開自己的檔 ⇒ 追蹤行不會寫進它的檔,之後的事件照樣進追蹤檔
F2b Python:同號碼被唯讀開回**同一個**追蹤檔(身分對得上但不能寫)⇒ 事件不遺失(#323 r2 L2)
F4 Python fork:3 條執行緒狂打事件且每次寫追蹤慢 50 ms、主執行緒 fork 30 次立刻 os._exit ⇒ 沒有子行程卡住、
   30 筆 exit 都在(r2 H1)
F5 Python daemonize:closerange 後 open(devnull)+ dup2 到 1/2 ⇒ devnull 拿到 0、程式輸出不進追蹤檔(r2 H2)
F6 Python 追蹤內容跟舊寫法(每筆開關檔)去掉 pid 後逐行相同(r2 L5)
F9 Python 追蹤檔寫不進(RLIMIT_FSIZE ⇒ EFBIG)⇒ 300 筆事件之後指向追蹤檔的 fd ≤ 1 個(不每筆漏一個,r3 M1)
F10 Python 一開始 fd 0 就關著 ⇒ 2000 筆事件開追蹤檔 ≤ 2 次,而且追蹤檔不佔 0 號(r3 裁 1)
F3 Node:事件都在、開追蹤檔 ≤ 2 次(預載計數器包 fs.openSync / appendFileSync,跨平台)
F7 Node:關掉追蹤 fd、同號碼開自己的檔 ⇒ 自己的檔沒被寫進、之後事件照樣進追蹤檔(r2 M1)
F8 Node 沒有 registerHooks ⇒ 退回 module.register(hooks.mjs),ESM import 的檔照樣記 open(r2 M1;
   Node 沒有 module.register 了就 skip);hooks.mjs 的寫入程式跟 audit.cjs 同一份,低號碼 / EBADF 靠 F11 / F12 代表
F11 Node 關掉追蹤 fd 與 0 號後觸發重開 ⇒ 追蹤檔不佔 0 號、接著開的 /dev/null 拿到 0(r3 L1)
F12 Node 追蹤檔寫不進(ulimit -f + 忽略 SIGXFSZ)⇒ 指向追蹤檔的 fd ≤ 1 個(r3 M1)
刀(複本 + run_guarded,斷言「FAIL: <目標>」且沒有 ERROR):K1 改回每筆開關檔 ⇒ F1 紅;K2 拿掉身分比對 ⇒ F2 紅;
KH1 加回鎖 ⇒ F4 紅;KH2 低號碼也快取 ⇒ F5 紅;KL2 寫失敗不重開 ⇒ F2b 紅;KN1 Node 改回 appendFileSync ⇒ F3 紅;
KN2 Node 拿掉身分比對 ⇒ F7 紅;KE / KEN 寫失敗一律重開(Python / Node)⇒ F9 / F12 紅;
KLO / KLON 低號碼不搬高 ⇒ F10 / F11 紅;K0 對照刀(只改註解)⇒ 目標照樣綠
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

VERIFY = ROOT / "tools" / "verify"
MAC = sys.platform == "darwin"
NODE = shutil.which("node")

# 子行程:數自己開追蹤檔幾次(第二個 audit hook),macOS 另量 diskio(proc_pid_rusage v2);最後一行印 JSON
COUNTER = textwrap.dedent("""
    import ctypes, json, os, sys
    _TP = os.environ["EZ2_VERIFY_TRACE"]
    _N = [0]
    def _count(ev, args):
        if ev == "open" and args and isinstance(args[0], str) and os.path.abspath(args[0]) == _TP:
            _N[0] += 1
    sys.addaudithook(_count)
    def diskio():
        if sys.platform != "darwin":
            return None
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        class RU(ctypes.Structure):
            _fields_ = [("u", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in "abcdefghijklmnop"] + \\
                       [("r", ctypes.c_uint64), ("w", ctypes.c_uint64)]
        r = RU()
        lib.proc_pid_rusage(os.getpid(), 2, ctypes.byref(r))
        return r.w
""")

MANY_OPENS = COUNTER + textwrap.dedent("""
    d = sys.argv[1]
    for i in range(2000):
        open(os.path.join(d, "f%d" % (i % 50)), "rb").close()
    print(json.dumps({"w": diskio(), "opens": _N[0]}))
""")

FD_REUSE = textwrap.dedent("""
    import json, os, sys
    import sitecustomize_audit as A
    d = sys.argv[1]
    open(os.path.join(d, "f0"), "rb").close()          # 確保追蹤 fd 已經開著
    fd = A._TRACE_FD[0][1]
    own = os.path.join(d, "mine.txt")
    os.close(fd)
    nfd = os.open(own, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if nfd != fd:
        os.dup2(nfd, fd)
        os.close(nfd)
    open(os.path.join(d, "f1"), "rb").close()          # 這筆事件不准寫進 mine.txt
    os.write(fd, b"MINE\\n")
    os.close(fd)
    print(json.dumps({"fd": fd}))
""")

FD_READONLY_SAME = textwrap.dedent("""
    import json, os, sys
    import sitecustomize_audit as A
    d = sys.argv[1]
    open(os.path.join(d, "f0"), "rb").close()
    fd = A._TRACE_FD[0][1]
    os.close(fd)
    nfd = os.open(os.environ["EZ2_VERIFY_TRACE"], os.O_RDONLY)   # 同一個追蹤檔、唯讀:身分對得上但寫會 EBADF
    if nfd != fd:
        os.dup2(nfd, fd)
        os.close(nfd)
    for i in range(5):
        open(os.path.join(d, "g%d" % i), "rb").close()     # 這 5 筆不准遺失
    print(json.dumps({"fd": fd}))
""")

FORK_STORM = textwrap.dedent("""
    import json, os, sys, threading, time
    import warnings
    warnings.simplefilter("ignore", DeprecationWarning)    # 「多執行緒 fork」警告:這格就是故意這樣做
    # 讓狂打事件的執行緒每次寫追蹤都慢 50 ms(模擬磁碟慢)⇒ fork 當下幾乎一定有執行緒正在寫;
    #   子行程的主執行緒不慢。有鎖的寫法 ⇒ 子行程拿不到鎖、永久卡住;沒鎖 ⇒ 照常寫完結束。
    _real_write = os.write
    SLOW = set()
    def _slow_write(fd, data):
        if threading.get_ident() in SLOW:
            time.sleep(0.05)
        return _real_write(fd, data)
    os.write = _slow_write
    stop = [False]
    def spam():
        SLOW.add(threading.get_ident())
        while not stop[0]:
            open(os.path.join(sys.argv[1], "f0"), "rb").close()
    ts = [threading.Thread(target=spam, daemon=True) for _ in range(3)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    kids = []
    for _ in range(30):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        kids.append(pid)
        time.sleep(0.01)
    deadline = time.time() + 10
    left = set(kids)
    while left and time.time() < deadline:
        for p in list(left):
            if os.waitpid(p, os.WNOHANG)[0]:
                left.discard(p)
        time.sleep(0.02)
    for p in left:
        os.kill(p, 9)
        os.waitpid(p, 0)
    stop[0] = True
    print(json.dumps({"kids": kids, "stuck": sorted(left)}))
""")

DAEMONIZE = textwrap.dedent("""
    import os, sys
    out = sys.argv[2]
    open(os.path.join(sys.argv[1], "f0"), "rb").close()   # 先讓追蹤 fd 開起來(快取在 3 號以上)
    os.closerange(0, 256)                                  # 經典 daemonize:全關
    fd = os.open(os.devnull, os.O_RDWR)                    # 稽核事件先觸發 ⇒ 鉤子重開追蹤檔
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.write(1, b"HELLO-STDOUT\\n")
    os.write(2, b"HELLO-STDERR\\n")
    open(os.path.join(sys.argv[1], "f1"), "rb").close()
    w = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(w, str(fd).encode())
    os.close(w)
""")

FD_COUNT = textwrap.dedent("""
    def trace_fds():
        st = os.stat(os.environ["EZ2_VERIFY_TRACE"])
        n = []
        for fd in range(0, 1024):
            try:
                s = os.fstat(fd)
            except OSError:
                continue
            if (s.st_dev, s.st_ino) == (st.st_dev, st.st_ino):
                n.append(fd)
        return n
""")

EFBIG = "import json, os, resource, signal, sys\n" + FD_COUNT + textwrap.dedent("""
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    d = sys.argv[1]
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (4096, hard))
    for i in range(300):
        open(os.path.join(d, "f%d" % (i % 50)), "rb").close()
    print(json.dumps({"fds": trace_fds(), "size": os.path.getsize(os.environ["EZ2_VERIFY_TRACE"])}))
""")

STDIN_CLOSED = COUNTER + FD_COUNT + textwrap.dedent("""
    d = sys.argv[1]
    for i in range(2000):
        open(os.path.join(d, "f%d" % (i % 50)), "rb").close()
    print(json.dumps({"opens": _N[0], "fds": trace_fds()}))
""")

DETERMINISTIC = textwrap.dedent("""
    import os, sys
    d = sys.argv[1]
    for i in range(30):
        open(os.path.join(d, "f%d" % i), "rb").close()
    os.listdir(d)
    for i in range(5):
        try:
            os.stat(os.path.join(d, "missing%d" % i))
        except OSError:
            pass
""")

OLD_WRITER = '''def _trace_write(path, data):
    with open(path, "ab") as fh:
        fh.write(data)


def _trace_write_new(path, data):'''


def trace_env(trace, home, pypath=VERIFY):
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "EZ2_VERIFY_TRACE": str(trace),
            "PYTHONPATH": str(pypath), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}


def kill_quiet(pids):
    for p in pids or ():
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


class Trace(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        for i in range(50):
            Path(self.d, "f%d" % i).write_bytes(b"x" * 100)
        for i in range(5):
            Path(self.d, "g%d" % i).write_bytes(b"g")
        self.trace = Path(self.d, "trace.jsonl")

    def run_py(self, code, *extra, pypath=VERIFY, trace=None):
        script = Path(self.d, "s.py")
        script.write_text(code)
        p = subprocess.run([sys.executable, str(script), self.d, *extra],
                           env=trace_env(trace or self.trace, self.d, pypath), capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        last = p.stdout.strip().splitlines()
        return json.loads(last[-1]) if last else None

    def events(self, trace=None):
        return [json.loads(l) for l in (trace or self.trace).read_text().splitlines() if l.strip()]

    def opened(self, name, trace=None):
        return [e for e in self.events(trace) if e.get("ch") == "open" and e.get("path", "").endswith("/" + name)]

    def test_f1_python_no_amplification(self):
        out = self.run_py(MANY_OPENS)
        self.assertGreaterEqual(len([e for e in self.events() if e.get("ch") == "open"]), 50, "追蹤事件不見了")
        self.assertLessEqual(out["opens"], 2, "子行程開了追蹤檔 %d 次(每筆開關檔?)" % out["opens"])
        if MAC and out["w"] is not None:
            size = self.trace.stat().st_size
            self.assertLess(out["w"], 5 * size + 256 * 1024,
                            "追蹤寫入放大:行程寫 %d bytes,追蹤檔 %d bytes" % (out["w"], size))

    def test_f2_fd_reuse_never_writes_into_test_file(self):
        self.run_py(FD_REUSE)
        mine = Path(self.d, "mine.txt").read_text()
        self.assertEqual(mine, "MINE\n", "追蹤行寫進了測試自己的檔:%r" % mine[:200])
        self.assertTrue(self.opened("f1"), "fd 被重用之後的事件沒有進追蹤檔")

    def test_f2b_readonly_same_trace_file_not_lost(self):
        self.run_py(FD_READONLY_SAME)
        for i in range(5):
            self.assertTrue(self.opened("g%d" % i), "g%d 的事件遺失(唯讀同號碼之後寫失敗被吞)" % i)

    @unittest.skipUnless(hasattr(os, "fork"), "沒有 fork")
    def test_f4_fork_with_threads_never_hangs(self):
        out = self.run_py(FORK_STORM)
        self.assertEqual(out["stuck"], [], "fork 出來的子行程卡在追蹤鉤子:%d/30" % len(out["stuck"]))
        exits = {e["pid"] for e in self.events() if e.get("ch") == "exit"}
        self.assertTrue(set(out["kids"]) <= exits, "有子行程的 exit 紀錄不見:%s" % sorted(set(out["kids"]) - exits))

    def test_f5_daemonize_devnull_keeps_fd0(self):
        res = Path(self.d, "devnull_fd.txt")
        self.run_py(DAEMONIZE, str(res))
        self.assertEqual(res.read_text(), "0", "devnull 沒拿到 0 號:追蹤鉤子佔住了低號碼")
        text = self.trace.read_text()
        self.assertNotIn("HELLO-", text, "程式的 stdout / stderr 被接到追蹤檔")
        self.assertTrue(self.opened("f1"), "daemonize 之後的事件沒進追蹤檔")

    @unittest.skipUnless(hasattr(signal, "SIGXFSZ"), "沒有 RLIMIT_FSIZE / SIGXFSZ")
    def test_f9_write_errors_do_not_leak_fds(self):
        out = self.run_py(EFBIG)
        self.assertLessEqual(out["size"], 4096)
        self.assertLessEqual(len(out["fds"]), 1, "追蹤檔寫不進時漏 fd:%d 個" % len(out["fds"]))

    def test_f10_stdin_closed_from_start(self):
        script = Path(self.d, "s.py")
        script.write_text(STDIN_CLOSED)
        env = trace_env(self.trace, self.d)
        p = subprocess.run(["/bin/sh", "-c", 'exec 0<&- "$0" "$@"', sys.executable, str(script), self.d],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertLessEqual(out["opens"], 2, "0 號關著時退回每筆開關檔:%d 次" % out["opens"])
        self.assertNotIn(0, out["fds"], "追蹤檔佔住了 0 號")

    def test_f6_same_content_as_old_writer(self):
        old_dir = Path(self.d, "oldverify")
        shutil.copytree(VERIFY, old_dir, ignore=shutil.ignore_patterns("__pycache__", "node-shim", "bash-shim"))
        src = (old_dir / "sitecustomize_audit.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("def _trace_write(path, data):"), 1)
        (old_dir / "sitecustomize_audit.py").write_text(src.replace("def _trace_write(path, data):", OLD_WRITER),
                                                        encoding="utf-8")
        tdir = tempfile.mkdtemp()                       # 追蹤檔放資料夾外面:兩次列目錄看到的要一樣
        self.addCleanup(shutil.rmtree, tdir, True)
        old_trace, new_trace = Path(tdir, "old.jsonl"), Path(tdir, "new.jsonl")
        self.run_py(DETERMINISTIC, pypath=old_dir, trace=old_trace)
        self.run_py(DETERMINISTIC, trace=new_trace)

        def norm(trace):
            out = []
            for e in self.events(trace):
                if e.get("ch") == "t0":                       # 開頭那筆帶 /tmp 目錄列表,別席同時跑會一直變(r3 M2)
                    continue
                for k in ("pid", "ns", "mtime_ns", "tmp"):    # 時間:兩次一定不同
                    e.pop(k, None)
                for k in list(e):
                    if isinstance(e[k], str):
                        e[k] = e[k].replace(str(old_dir), "<V>").replace(str(VERIFY), "<V>")
                        e[k] = e[k].replace(str(old_trace), "<T>").replace(str(new_trace), "<T>")
                out.append(json.dumps(e, sort_keys=True))
            return [l for l in out if "<V>" not in l]   # 追蹤器自己的檔(兩邊路徑不同)不比
        self.assertEqual(norm(old_trace), norm(new_trace))


NODE_COUNTER = textwrap.dedent("""
    const fs = require('fs'), path = require('path');
    const T = path.resolve(process.env.EZ2_VERIFY_TRACE), OUT = process.env.COUNT_OUT;
    let n = 0;
    const same = p => { try { return typeof p === 'string' && path.resolve(p) === T; } catch (e) { return false; } };
    const o1 = fs.openSync, o2 = fs.appendFileSync, w = fs.writeFileSync;
    fs.openSync = function (p, ...a) { if (same(p)) n++; return o1.call(this, p, ...a); };
    fs.appendFileSync = function (p, ...a) { if (same(p)) n++; return o2.call(this, p, ...a); };
    process.on('exit', () => { w(OUT, String(n)); });
""")

NODE_FD_REUSE = textwrap.dedent("""
    const fs = require('fs'), path = require('path'), { Worker } = require('worker_threads');
    const d = process.argv[2], T = process.env.EZ2_VERIFY_TRACE, mine = path.join(d, 'mine.txt');
    // 主執行緒的 openSync 會先觸發鉤子(鉤子先重開、拿走空出來的號碼);鉤子只裝在主執行緒 ⇒ 用 worker 開,
    //   模擬「別的路(子行程管線、socket…)拿到同一個號碼」。worker 先起好,關 fd 到 worker 開檔之間主執行緒不碰 fs。
    const w = new Worker("const fs=require('fs'),{parentPort}=require('worker_threads');" +
                         "parentPort.postMessage('ready');parentPort.on('message',p=>parentPort.postMessage(fs.openSync(p,'w')))",
                         { eval: true });
    w.once('message', () => {
      fs.readFileSync(path.join(d, 'f0'));
      const ts = fs.statSync(T);
      let fd = -1;
      for (let i = 3; i < 256; i++) {
        try { const s = fs.fstatSync(i); if (s.dev === ts.dev && s.ino === ts.ino) { fd = i; break; } } catch (e) {}
      }
      if (fd < 0) { console.error('trace fd not found'); process.exit(5); }
      fs.closeSync(fd);
      w.once('message', nfd => {
        if (nfd !== fd) { console.error('fd not reused: ' + nfd + ' vs ' + fd); process.exit(4); }
        fs.readFileSync(path.join(d, 'f1'));        // 這筆事件不准寫進 mine.txt
        fs.writeSync(nfd, 'MINE\\n');
        fs.closeSync(nfd);
        w.terminate();
      });
      w.postMessage(mine);
    });
""")


NODE_LOW_FD = textwrap.dedent("""
    const fs = require('fs'), path = require('path');
    const d = process.argv[2], T = process.env.EZ2_VERIFY_TRACE;
    fs.readFileSync(path.join(d, 'f0'));
    const ts = fs.statSync(T);
    for (let i = 3; i < 256; i++) {
      try { const s = fs.fstatSync(i); if (s.dev === ts.dev && s.ino === ts.ino) { fs.closeSync(i); break; } } catch (e) {}
    }
    fs.closeSync(0);
    fs.readFileSync(path.join(d, 'f1'));       // 觸發重開:這時最低空號是 0
    const nfd = fs.openSync('/dev/null', 'r');
    let zero = null;
    try { const s = fs.fstatSync(0); zero = (s.dev === ts.dev && s.ino === ts.ino) ? 'trace' : 'other'; } catch (e) {}
    console.log(JSON.stringify({ devnull: nfd, zero }));
""")

NODE_EFBIG = textwrap.dedent("""
    const fs = require('fs'), path = require('path');
    const d = process.argv[2], T = process.env.EZ2_VERIFY_TRACE;
    for (let i = 0; i < 300; i++) fs.readFileSync(path.join(d, 'f' + (i % 50)));
    const ts = fs.statSync(T);
    const fds = [];
    for (let i = 0; i < 1024; i++) {
      try { const s = fs.fstatSync(i); if (s.dev === ts.dev && s.ino === ts.ino) fds.push(i); } catch (e) {}
    }
    console.log(JSON.stringify({ fds, size: ts.size }));
""")


@unittest.skipUnless(NODE, "沒有 node")
class NodeTrace(Trace):
    def run_node(self, script, *pre, env_extra=None):
        js = Path(self.d, "s.js")
        js.write_text(script)
        env = trace_env(self.trace, self.d)
        env.pop("PYTHONPATH")
        req = []
        for p in pre:
            req += ["--require", str(p)]
        req += ["--require", str(VERIFY / "node-shim" / "audit.cjs")]
        env.update(env_extra or {})
        p = subprocess.run([NODE, *req, str(js), self.d], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def test_f3_node_no_amplification(self):
        counter = Path(self.d, "counter.cjs")
        counter.write_text(NODE_COUNTER)
        out = Path(self.d, "count.txt")
        self.run_node(textwrap.dedent("""
            const fs = require('fs'), path = require('path');
            for (let i = 0; i < 2000; i++) fs.readFileSync(path.join(process.argv[2], 'f' + (i % 50)));
        """), counter, env_extra={"COUNT_OUT": str(out)})
        evs = self.events()
        self.assertTrue(any(e.get("ch") == "inherit" for e in evs), "node 接頭沒寫 inherit")
        self.assertGreaterEqual(len([e for e in evs if e.get("ch") == "open"]), 50)
        self.assertLessEqual(int(out.read_text()), 2, "node 開了追蹤檔 %s 次" % out.read_text())

    def test_f7_node_fd_reuse_never_writes_into_test_file(self):
        self.run_node(NODE_FD_REUSE)
        self.assertEqual(Path(self.d, "mine.txt").read_text(), "MINE\n", "node 追蹤行寫進了測試自己的檔")
        self.assertTrue(self.opened("f1"), "fd 被重用之後 node 的事件沒有進追蹤檔")

    def test_f11_node_low_fd_moved_high(self):
        p = self.run_node(NODE_LOW_FD)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(out["devnull"], 0, "追蹤檔佔住了 0 號:%s" % out)
        self.assertEqual(out["zero"], "other")
        self.assertTrue(self.opened("f1"), "重開之後的事件沒進追蹤檔")

    def test_f12_node_write_errors_do_not_leak_fds(self):
        js = Path(self.d, "s.js")
        js.write_text(NODE_EFBIG)
        self.trace.write_bytes(b"")
        env = trace_env(self.trace, self.d)
        env.pop("PYTHONPATH")
        p = subprocess.run(["/bin/bash", "-c", "trap '' XFSZ; ulimit -f 4; exec \"$0\" \"$@\"", NODE, "--require",
                            str(VERIFY / "node-shim" / "audit.cjs"), str(js), self.d],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertLessEqual(out["size"], 4096)
        self.assertLessEqual(len(out["fds"]), 1, "node 追蹤檔寫不進時漏 fd:%s" % out["fds"])

    def test_f8_node_module_register_hooks_mjs(self):
        probe = subprocess.run([NODE, "-p", "typeof require('module').register"], capture_output=True, text=True,
                               timeout=30)
        if probe.stdout.strip() != "function":
            self.skipTest("這版 Node 沒有 module.register(退路本來就走不到)")
        Path(self.d, "nohooks.cjs").write_text("require('module').registerHooks = undefined;\n")
        Path(self.d, "m.mjs").write_text("export const x = 1;\n")
        Path(self.d, "s.mjs").write_text("import { x } from './m.mjs';\nif (x !== 1) process.exit(3);\n")
        env = trace_env(self.trace, self.d)
        env.pop("PYTHONPATH")
        p = subprocess.run([NODE, "--require", str(Path(self.d, "nohooks.cjs")), "--require",
                            str(VERIFY / "node-shim" / "audit.cjs"), str(Path(self.d, "s.mjs"))],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        hits = [e for e in self.opened("m.mjs") if e.get("via") == "node"]
        self.assertTrue(hits, "退回 hooks.mjs 時 ESM import 沒記 open\n" + p.stderr[-2000:])

    # NodeTrace 繼承 Trace 只為了共用 setUp / events;Python 那幾格不重跑
    test_f1_python_no_amplification = test_f2_fd_reuse_never_writes_into_test_file = None
    test_f2b_readonly_same_trace_file_not_lost = test_f4_fork_with_threads_never_hangs = None
    test_f5_daemonize_devnull_keeps_fd0 = test_f6_same_content_as_old_writer = None
    test_f9_write_errors_do_not_leak_fds = test_f10_stdin_closed_from_start = None


PY = "tools/verify/sitecustomize_audit.py"
CJS = "tools/verify/node-shim/audit.cjs"
KNIVES = [
    ("K0", PY, "#   改成每個行程留一個 O_APPEND 的 fd", "#   (對照刀)改成每個行程留一個 O_APPEND 的 fd",
     "Trace.test_f1_python_no_amplification", True),
    ("K1", PY, "def _trace_write(path, data):", OLD_WRITER, "Trace.test_f1_python_no_amplification", False),
    ("K2", PY, "    if same and c[0] == path:\n        return c[1]", "    if c[0] == path:\n        return c[1]",
     "Trace.test_f2_fd_reuse_never_writes_into_test_file", False),
    ("KH1", PY, "def _trace_write(path, data):\n",
     "from _thread import allocate_lock as _AL\n_KL = _AL()\n\n\ndef _trace_write(path, data):\n    with _KL:\n"
     "        return _trace_write0(path, data)\n\n\ndef _trace_write0(path, data):\n",
     "Trace.test_f4_fork_with_threads_never_hangs", False),
    ("KLO", PY, "        while fd <= 2:\n", "        while False:\n", "Trace.test_f10_stdin_closed_from_start", False),
    ("KLO5", PY, "        while fd <= 2:\n", "        while False:\n", "Trace.test_f5_daemonize_devnull_keeps_fd0", False),
    ("KE", PY, "            if e.errno != _EBADF:\n                raise                    # fd 還是我們的:保留快取,這筆丟掉\n",
     "", "Trace.test_f9_write_errors_do_not_leak_fds", False),
    ("KEN", CJS, "        if (e.code !== 'EBADF') throw e;", "",
     "NodeTrace.test_f12_node_write_errors_do_not_leak_fds", False),
    ("KLON", CJS, "      while (fd <= 2) {", "      while (false) {", "NodeTrace.test_f11_node_low_fd_moved_high", False),
    ("KL2", PY, "            _TRACE_FD[0] = None          # 號碼被拿走", "            raise\n            # 號碼被拿走",
     "Trace.test_f2b_readonly_same_trace_file_not_lost", False),
    ("KN1", CJS, "    try { writeTrace(Buffer.from(s + '\\n')); }", "    try { O.appendFileSync(TRACE, s + '\\n'); }",
     "NodeTrace.test_f3_node_no_amplification", False),
    ("KN2", CJS, "if (st.dev + ':' + st.ino !== traceId) traceFd = null;", "",
     "NodeTrace.test_f7_node_fd_reuse_never_writes_into_test_file", False),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target, stays_green in KNIVES:
            if rel == CJS and not NODE:
                continue
            if tag == "KH1" and not hasattr(os, "fork"):
                continue
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=80, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                if stays_green:
                    self.assertEqual(r.returncode, 0, "%s 對照刀不該紅\n%s" % (tag, out))
                    continue
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
