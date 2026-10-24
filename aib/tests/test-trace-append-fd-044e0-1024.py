#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 追蹤鉤子的寫法(10-24 @044e0 本機效能):每個行程留一個 O_APPEND fd、每筆一次 write,不再每筆開關檔。
以前每筆 open("a") → 寫 → 關,APFS 上實際落盤約 76 倍(1000 筆 131 KB ⇒ 9.9 MB);一天 3500 支次測試 ⇒ 1.1 TB。
F1 Python 接頭:2000 筆事件,行程實際寫入 < 追蹤檔 5 倍(只在 macOS 量得到 diskio;其他平台只驗事件都在)
F2 Python 接頭:測試自己關掉追蹤 fd、同一個號碼開自己的檔 ⇒ 追蹤行不會寫進它的檔,之後的事件照樣進追蹤檔
F3 Node 接頭(有 node 才跑):事件都在、實際寫入 < 追蹤檔 5 倍(macOS)
刀(複本 + run_guarded):K1 Python 改回每筆開關檔 ⇒ F1 紅;K2 拿掉 fstat 身分比對 ⇒ F2 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

VERIFY = ROOT / "tools" / "verify"
MAC = sys.platform == "darwin"

# 子行程自己量自己的 diskio(proc_pid_rusage v2),印成最後一行 JSON
DISKIO = textwrap.dedent("""
    import ctypes, json, os, sys
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

MANY_OPENS = DISKIO + textwrap.dedent("""
    d = sys.argv[1]
    for i in range(2000):
        p = os.path.join(d, "f%d" % (i % 50))
        open(p, "rb").close()
    print(json.dumps({"w": diskio()}))
""")

FD_REUSE = textwrap.dedent("""
    import json, os, sys
    import sitecustomize_audit as A
    d = sys.argv[1]
    open(os.path.join(d, "f0"), "rb").close()          # 確保追蹤 fd 已經開著
    fd = A._TRACE_FD[1]
    assert fd is not None, "trace fd not cached"
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


def trace_env(trace, home):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "EZ2_VERIFY_TRACE": str(trace),
           "PYTHONPATH": str(VERIFY), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
    return env


class Trace(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        for i in range(50):
            Path(self.d, "f%d" % i).write_bytes(b"x" * 100)
        self.trace = Path(self.d, "trace.jsonl")

    def run_py(self, code):
        script = Path(self.d, "s.py")
        script.write_text(code)
        p = subprocess.run([sys.executable, str(script), self.d], env=trace_env(self.trace, self.d),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def events(self):
        return [json.loads(l) for l in self.trace.read_text().splitlines() if l.strip()]

    def test_f1_python_no_amplification(self):
        out = self.run_py(MANY_OPENS)
        evs = self.events()
        opens = [e for e in evs if e.get("ch") == "open" and e.get("path", "").startswith(os.path.realpath(self.d))
                 or e.get("ch") == "open" and e.get("path", "").startswith(self.d)]
        self.assertGreaterEqual(len(opens), 50, "追蹤事件不見了")
        if MAC and out["w"] is not None:
            size = self.trace.stat().st_size
            self.assertLess(out["w"], 5 * size + 256 * 1024,
                            "追蹤寫入放大:行程寫 %d bytes,追蹤檔 %d bytes" % (out["w"], size))

    def test_f2_fd_reuse_never_writes_into_test_file(self):
        self.run_py(FD_REUSE)
        mine = Path(self.d, "mine.txt").read_text()
        self.assertEqual(mine, "MINE\n", "追蹤行寫進了測試自己的檔:%r" % mine[:200])
        paths = [e.get("path", "") for e in self.events() if e.get("ch") == "open"]
        self.assertTrue(any(p.endswith("/f1") for p in paths), "fd 被重用之後的事件沒有進追蹤檔")

    @unittest.skipUnless(shutil.which("node"), "沒有 node")
    def test_f3_node_no_amplification(self):
        js = Path(self.d, "s.js")
        js.write_text(textwrap.dedent("""
            const fs = require('fs'), path = require('path');
            for (let i = 0; i < 2000; i++) fs.readFileSync(path.join(process.argv[2], 'f' + (i % 50)));
        """))
        env = trace_env(self.trace, self.d)
        env["NODE_OPTIONS"] = "--require %s" % (VERIFY / "node-shim" / "audit.cjs")
        wrap = Path(self.d, "w.py")
        wrap.write_text(DISKIO + textwrap.dedent("""
            import subprocess
            p = subprocess.Popen(["node", sys.argv[2], sys.argv[1]])
            # 量 node 子行程:輪詢它的 diskio 直到結束
            import time
            lib = ctypes.CDLL("/usr/lib/libproc.dylib") if sys.platform == "darwin" else None
            best = 0
            while p.poll() is None:
                if lib is not None:
                    class RU(ctypes.Structure):
                        _fields_ = [("u", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in "abcdefghijklmnop"] + \\
                                   [("r", ctypes.c_uint64), ("w", ctypes.c_uint64)]
                    r = RU()
                    if lib.proc_pid_rusage(p.pid, 2, ctypes.byref(r)) == 0:
                        best = max(best, r.w)
                time.sleep(0.01)
            print(json.dumps({"rc": p.returncode, "w": best if lib is not None else None}))
        """))
        env.pop("PYTHONPATH")
        p = subprocess.run([sys.executable, str(wrap), self.d, str(js)], env=env, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(out["rc"], 0, p.stderr)
        evs = self.events()
        self.assertTrue(any(e.get("ch") == "inherit" for e in evs), "node 接頭沒寫 inherit")
        self.assertGreaterEqual(len([e for e in evs if e.get("ch") == "open"]), 50)
        if MAC and out["w"]:
            size = self.trace.stat().st_size
            self.assertLess(out["w"], 5 * size + 256 * 1024,
                            "node 追蹤寫入放大:%d vs 追蹤檔 %d" % (out["w"], size))


KNIVES = [
    ("K1", '''        with _TRACE_LOCK:
            fd = _trace_fd(path)
            while data:
                data = data[os.write(fd, data):]''',
     '''        with open(path, "a", encoding="utf-8") as fh:
            fh.write(data.decode("utf-8"))''', "Trace.test_f1_python_no_amplification"),
    ("K2", '''            if (st.st_dev, st.st_ino) == c[2]:
                return c[1]''', '''            return c[1]''', "Trace.test_f2_fd_reuse_never_writes_into_test_file"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            if tag == "K1" and not MAC:
                continue
            with self.subTest(tag):
                with knife_copy(ROOT, "tools/verify/sitecustomize_audit.py", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s%s" % (tag, r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
