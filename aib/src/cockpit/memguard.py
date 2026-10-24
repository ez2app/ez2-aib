#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""memguard.py —— 單支直譯器行程記憶體暴衝守門(2026-10-24 Andy 派 @044e0 負責本機效能 / 過熱 / 卡死)。

為什麼要有這支(10-24 20:34 實案,forceReset panic 快照為證):
  一席審查子代理做「稀疏大檔」重現:建 2^50 位元組的空洞檔,讓受測程式去讀。有一條路徑沒有馬上丟 MemoryError,
  而是一路長大 —— 一支 Python 26 秒 CPU 吃到 50 GB、另一支 5.4 GB。16 GB 機器剩 14 MB 可用、壓縮區 3.4 GB,
  launchd 開不出行程(114 個 xpcproxy 卡住),畫面全凍,只能長按電源。macOS 不執行 RLIMIT_AS / RLIMIT_DATA,
  沒有辦法事前替別席的指令設上限 ⇒ 只能在機器層每秒看一次、超過就收。

判法(刻意窄,寧可漏收不可錯殺):
  - 只看**自己這個 uid** 的行程;只看直譯器(python* / node / php* / ruby / perl),而且看的是**直譯器真正跑的腳本**
    (精確 argv:macOS KERN_PROCARGS2 / Linux /proc,只認直譯器自己的選項):
      dev     內嵌碼(python -c / -Bc、node -e、`-` 或沒腳本 = 讀 stdin)或腳本 realpath 在暫存根 / scratchpad /
              tests/test-* ⇒ dev 上限(相對路徑用**目標行程**的 cwd 解,解不出或不是檔 ⇒ 不判 dev)
      general python 的其他腳本 / -m 模組 ⇒ 一般上限;multiprocessing 子行程沿用父行程的分級
      不看    node / php / ruby / perl 的其他腳本(Codex 席 `node …/codex`、GitHub runner `RunnerService.js` 在這);
              腳本名 codex / claude / RunnerService.js 不管怎樣都不看。
    Claude CLI、瀏覽器、Terminal、tmux、系統行程本來就不是直譯器,一律不看:它們大是正常的,收了會掉人的上下文。
  - 單支 phys_footprint(macOS;Linux 用 VmRSS)≥ 門檻才收。門檻預設 = max(4 GB, 實體記憶體 35%)(16 GB 機 ≈ 5.6 GB),
    EZ2_MEMGUARD_LIMIT_MB 可改,但壓不到 1 GB 以下(1 GB 以下是正常工作量)。
  - dev 上限 = aib test 的 AIB_TEST_MEM_CAP_MB(預設 2 GB)。超過 dev 上限才讀一次 argv(不 fork),之後沿用。
  - 連續 CONFIRM_TICKS 拍都超過才收(一拍的尖峰不算)。

源頭那一層(10-24 Andy「從源頭禁止，開發時就要知道不能超大」):TreeCap 給 aib test 用 —— tools/verify/runner.py
  每支測試起一條看門執行緒,0.25 秒看一次整棵行程樹,任何一支超過 AIB_TEST_MEM_CAP_MB(預設 2048,0 = 關)
  就整棵收、rc 137、輸出帶 memory_cap 與「改用小上限驗」的說明;開席鐵則第 6 條同步寫給每一席。
  - 不收自己與自己的祖先。
  - 送訊號前再比一次 uid + 開始時間(防 pid 重用);只殺那一個 pid,不整個行程群組殺。TERM → 等 TERM_WAIT_S → 還在才 KILL。

開關:EZ2_MEMGUARD=0 不起;EZ2_MEMGUARD_DRY_RUN=1 只留痕不殺。
放在哪:idle_reaper.py watch 啟動時起一條 daemon 執行緒(start_thread),TICK_S 秒一拍、零 fork(只在真的要收時
  才 exec 一次 ps 拿指令列頭、一次通知)。N 個艙都起執行緒,只有拿到 <狀態根>/locks/memguard.lock 的那條在掃,
  其他每 LOCK_RETRY_S 秒再搶一次(拿鎖的那艙關了,別艙接手)。
留痕:每次收(或 dry-run 該收)寫一行 JSON 到 <狀態根>/logs/memguard.jsonl:時間、pid、名字、腳本檔名、
  記憶體、門檻、動作。不寫整條指令列(引數裡可能有金鑰)。

CLI(aib memguard …):
  scan [--json]           列出現在看得到的直譯器行程與記憶體(不殺)
  watch [--dry-run]       前景常駐(除錯用;平常跟著 idle watch 跑)
  log [-n N]              看最近 N 筆收過的紀錄
rc:0 正常 / 2 用法錯
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

TICK_S = 1.0
CONFIRM_TICKS = 2
TERM_WAIT_S = 3.0
LOCK_RETRY_S = 30.0
FLOOR_MB = 1024
DEFAULT_MIN_MB = 4096
DEFAULT_FRACTION = 0.35
# 分級看「直譯器真正跑的那支腳本」,不看整條指令列的子字串(#322 r1:Codex 席行程名是 node、`codex exec -c …` 的 -c
#   是 codex 的旗標,不是直譯器的)。直譯器自己的選項才算:內嵌碼(-c / -e …)或腳本 realpath 在暫存根 / scratchpad /
#   tests/test-* ⇒ dev;python 其他 ⇒ general;node / php / ruby / perl 其他 ⇒ 不看(Codex、GitHub runner 都在這)。
#   #322 r2:python 短選項可合寫(-Bc / -uc / -mMOD / -Wignore)⇒ 逐字元掃;`-` 與沒有腳本(stdin / heredoc,AI 重現
#   最常用,20:34 實案就這類)⇒ 內嵌;認不得的值寧可判不看 / general,不偏 dev(相對路徑要真的是檔才算)。
INLINE_OPTS = {"python": {"-c"}, "node": {"-e", "--eval", "-p", "--print"}, "php": {"-r"},
               "ruby": {"-e"}, "perl": {"-e", "-E"}}
VALUE_OPTS = {"python": {"-W", "-X", "--check-hash-based-pycs"},
              "node": {"-r", "--require", "--import", "--loader", "--experimental-loader", "--conditions", "-C",
                       "--env-file", "--inspect-port", "--debug-port", "--title", "--input-type", "--stack-size",
                       "--max-old-space-size", "--openssl-config", "--redirect-warnings", "--icu-data-dir",
                       "--diagnostic-dir", "--report-dir", "--heapsnapshot-signal", "--secure-heap"},
              "php": {"-d", "-c", "-z"}, "ruby": {"-I", "-r", "-C"}, "perl": {"-I", "-M", "-m"}}
PY_SHORT_VALUE = set("WX")          # python 短選項:這幾個吃值(黏在後面或下一個引數)
NO_SCRIPT_INLINE = {"python", "node"}   # 沒腳本 = 讀 stdin(php-cgi / perl 沒腳本另有用途,不算)
MP_CHILD_RE = re.compile(r"^\s*from multiprocessing[. ]")
NEVER_SCRIPTS = {"codex", "claude", "RunnerService.js"}       # 活席 / 本機 runner:不管多大都不收
TEST_SCRIPT_RE = re.compile(r"/tests?/test[-_][^/]*$")
INTERP_RE = re.compile(r"^(python[0-9.]*|node|php[0-9.]*|php-cgi|ruby[0-9.]*|perl[0-9.]*)$", re.I)


def state_root(env=None):
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("EZ2_AIB_STATE_ROOT") or "~/.ez2-aib")


# ── 量:行程表(macOS libproc / Linux /proc),一律不 fork ─────────────────────────────────────────────
class _BsdInfo(ctypes.Structure):
    _fields_ = [("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32), ("pbi_xstatus", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_uint32), ("pbi_ppid", ctypes.c_uint32),
                ("pbi_uid", ctypes.c_uint32), ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
                ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32),
                ("rfu_1", ctypes.c_uint32), ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
                ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32), ("pbi_pjobc", ctypes.c_uint32),
                ("e_tdev", ctypes.c_uint32), ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
                ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64)]


class _RusageV0(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
        "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime")]


_LIBPROC = None


def _libproc():
    global _LIBPROC
    if _LIBPROC is None:
        _LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
    return _LIBPROC


def _mac_info(pid):
    """(uid, ppid, 名字, 開始時間字串, 記憶體位元組) 或 None。"""
    lib = _libproc()
    bi = _BsdInfo()
    if lib.proc_pidinfo(int(pid), 3, ctypes.c_uint64(0), ctypes.byref(bi), ctypes.sizeof(bi)) != ctypes.sizeof(bi):
        return None
    if bi.pbi_pid != pid:
        return None
    ru = _RusageV0()
    if lib.proc_pid_rusage(int(pid), 0, ctypes.byref(ru)) != 0:
        return None
    name = (bi.pbi_name or bi.pbi_comm).decode("utf-8", "replace")
    return (bi.pbi_uid, bi.pbi_ppid, name, "%d.%06d" % (bi.pbi_start_tvsec, bi.pbi_start_tvusec), ru.ri_phys_footprint)


def _mac_pids():
    lib = _libproc()
    n = lib.proc_listallpids(None, 0)
    if n <= 0:
        return []
    buf = (ctypes.c_int * (n + 64))()
    n = lib.proc_listallpids(buf, ctypes.sizeof(buf))
    return [p for p in buf[:max(0, n)] if p > 0]


def _linux_info(pid):
    try:
        with open("/proc/%d/status" % pid, encoding="utf-8", errors="replace") as fh:
            st = fh.read()
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as fh:
            sf = fh.read()
    except OSError:
        return None
    m = {k: v.strip() for k, v in (ln.split(":", 1) for ln in st.splitlines() if ":" in ln)}
    try:
        uid = int(m["Uid"].split()[1])          # 有效 uid,跟 macOS pbi_uid 同一個意思
        ppid = int(m["PPid"])
        rss = int(m.get("VmRSS", "0 kB").split()[0]) * 1024
        start = sf.rsplit(")", 1)[1].split()[19]
    except (KeyError, ValueError, IndexError):
        return None
    return (uid, ppid, m.get("Name", ""), start, rss)


def _linux_pids():
    try:
        return [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return []


def proc_info(pid):
    try:
        return _mac_info(pid) if sys.platform == "darwin" else _linux_info(pid)
    except Exception:  # noqa: BLE001 量不到 = 不收
        return None


def list_pids():
    try:
        return _mac_pids() if sys.platform == "darwin" else _linux_pids()
    except Exception:  # noqa: BLE001
        return []


def mem_total_bytes():
    try:
        if sys.platform == "darwin":
            v = ctypes.c_uint64(0)
            sz = ctypes.c_size_t(8)
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            if libc.sysctlbyname(b"hw.memsize", ctypes.byref(v), ctypes.byref(sz), None, 0) == 0:
                return int(v.value)
        else:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:  # noqa: BLE001
        pass
    return 16 * 2 ** 30


def _env_int(raw):
    """只收 ASCII 十進位(「²」過 isdigit 但 int() 會丟 —— #322 r2 L4);其他回 None。"""
    raw = (raw or "").strip()
    if not (raw.isascii() and raw.isdecimal()):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def limit_mb(env=None, total=None):
    """門檻(MB):環境變數優先但不低於 FLOOR_MB;沒設 ⇒ max(DEFAULT_MIN_MB, 實體記憶體 × DEFAULT_FRACTION)。"""
    env = os.environ if env is None else env
    n = _env_int(env.get("EZ2_MEMGUARD_LIMIT_MB"))
    if n is not None:
        return max(FLOOR_MB, n)
    total = mem_total_bytes() if total is None else total
    return max(DEFAULT_MIN_MB, int(total * DEFAULT_FRACTION / 2 ** 20))


def ancestors(pid=None, info=proc_info, limit=64):
    out, cur = set(), os.getpid() if pid is None else pid
    for _ in range(limit):
        out.add(cur)
        i = info(cur)
        if not i or i[1] in (0, cur):
            break
        cur = i[1]
    return out


def snapshot(*, uid=None, pids=None, info=proc_info):
    """[(pid, ppid, 名字, 開始時間, MB)] —— 只含自己 uid 的直譯器行程。pids 給了就只看那幾個(測試用)。"""
    uid = os.getuid() if uid is None else uid
    rows = []
    for pid in (list_pids() if pids is None else pids):
        i = info(pid)
        if not i or i[0] != uid or not INTERP_RE.match(i[2] or ""):
            continue
        rows.append((pid, i[1], i[2], i[3], i[4] // 2 ** 20))
    return rows


def cmd_args(pid):
    """精確的 argv(macOS sysctl KERN_PROCARGS2 / Linux /proc/<pid>/cmdline,不 fork、不拆空白);讀不到回 []。"""
    try:
        if sys.platform != "darwin":
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                raw = fh.read(1 << 20)
            if raw.endswith(b"\0"):
                raw = raw[:-1]
            return [a.decode("utf-8", "replace") for a in raw.split(b"\0")] if raw else []
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))           # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return []
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:]
        rest = rest[rest.index(b"\0"):].lstrip(b"\0")         # 跳過 exec 路徑與補齊的 NUL;之後的空引數保留
        return [a.decode("utf-8", "replace") for a in rest.split(b"\0")[:argc]]
    except Exception:  # noqa: BLE001 讀不到 = 不收
        return []


class _VnodePathInfo(ctypes.Structure):
    # struct proc_vnodepathinfo:pvi_cdir / pvi_rdir 各是 vnode_info(152 位元組)+ 路徑(MAXPATHLEN 1024)
    _fields_ = [("cdir_vi", ctypes.c_uint8 * 152), ("cdir_path", ctypes.c_char * 1024),
                ("rdir_vi", ctypes.c_uint8 * 152), ("rdir_path", ctypes.c_char * 1024)]


def proc_cwd(pid):
    """目標行程的工作目錄(相對路徑腳本要用它解,不能用守門自己的 cwd —— #322 r2 M1);讀不到回 None。"""
    try:
        if sys.platform != "darwin":
            return os.readlink("/proc/%d/cwd" % pid)
        vi = _VnodePathInfo()
        n = _libproc().proc_pidinfo(int(pid), 9, ctypes.c_uint64(0), ctypes.byref(vi), ctypes.sizeof(vi))
        if n != ctypes.sizeof(vi) or not vi.cdir_path:
            return None
        return vi.cdir_path.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _kind(name):
    n = (name or "").lower()
    for k in ("python", "node", "php", "ruby", "perl"):
        if n.startswith(k):
            return k
    return None


def _python_short(a, it):
    """python 合寫短選項 a(如 -Bc、-mfoo、-Wignore):回 ('inline', None) / ('module', 模組) / None(只是旗標)。"""
    for i, ch in enumerate(a[1:], 1):
        if ch == "c":
            return "inline", a[i + 1:] or next(it, "")
        if ch == "m":
            return "module", a[i + 1:] or next(it, "")
        if ch in PY_SHORT_VALUE:
            if not a[i + 1:]:
                next(it, None)
            return None
    return None


def script_of(name, args):
    """('inline', 碼或 None) / ('module', 模組) / ('script', 路徑) / (None, None)。只認直譯器自己的選項。"""
    kind = _kind(name)
    if not kind:
        return None, None
    it = iter(args[1:])
    for a in it:
        if a in INLINE_OPTS.get(kind, ()):
            return "inline", next(it, "")
        if a in VALUE_OPTS.get(kind, ()):
            next(it, None)
            continue
        if a == "--":
            a = next(it, None)
            if a is None:
                break
            return ("inline", None) if a == "-" else ("script", a)
        if a == "-":
            return "inline", None
        if kind == "python" and a.startswith("-") and not a.startswith("--"):
            got = _python_short(a, it)
            if got:
                return got
            continue
        if kind == "node" and a.startswith("-") and not a.startswith("--") and len(a) > 2 and a[-1] in "ep":
            return "inline", next(it, "")              # node 合寫(-pe)
        if a.startswith("-"):
            continue
        return "script", a
    if kind in NO_SCRIPT_INLINE and args:
        return "inline", None
    return None, None


def _temp_roots():
    roots = {"/tmp", "/private/tmp", "/var/folders", "/private/var/folders"}
    try:
        import tempfile
        roots.add(os.path.realpath(tempfile.gettempdir()))
    except Exception:  # noqa: BLE001
        pass
    return tuple(r.rstrip("/") + "/" for r in roots)


def classify(name, args, cwd=None):
    """'dev'(上限 = dev)/ 'general'(上限 = 一般,只有 python)/ 'inherit'(multiprocessing 子行程,沿用父行程)/
    None(不看)。相對路徑腳本用目標行程的 cwd 解;cwd 不知道 ⇒ 不判 dev。"""
    kind = _kind(name)
    how, what = script_of(name, args)
    if kind == "python" and ("--multiprocessing-fork" in args or (how == "inline" and MP_CHILD_RE.match(what or ""))):
        return "inherit"
    if how == "script" and what:
        if os.path.basename(what) in NEVER_SCRIPTS:
            return None
        # 絕對路徑照字面看(重現腳本跑完常被刪);相對路徑要用目標 cwd 解得出、而且真的是檔(帶值選項的值不算)
        path = what if os.path.isabs(what) else (os.path.join(cwd, what) if cwd else None)
        if path and (os.path.isabs(what) or os.path.isfile(path)):
            real = os.path.realpath(path)
            if real.startswith(_temp_roots()) or "/scratchpad/" in real or TEST_SCRIPT_RE.search(real):
                return "dev"
    elif how == "inline":
        return "dev"
    return "general" if kind == "python" else None


def is_dev(args, name="python"):
    return classify(name, args) == "dev"


def script_hint(pid, args=None, name="python"):
    """收之前留個線索:直譯器跑的腳本檔名 / -c / -m 模組(不留整條,引數可能有金鑰)。"""
    how, what = script_of(name, cmd_args(pid) if args is None else args)
    if how == "script" and what:
        return os.path.basename(what)[:80]
    return {"inline": "(inline)", "module": "-m %s" % (what or "")[:60]}.get(how, "")


def notify(text):
    """盡力而為,失敗不吵(跟 cabin_guard 同一條通知路)。"""
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import cabin_guard  # noqa: E402
        cabin_guard.do_notify(text)
    except Exception:  # noqa: BLE001
        pass


def _log(row, root=None):
    d = os.path.join(root or state_root(), "logs")
    try:
        os.makedirs(d, exist_ok=True)
        fd = os.open(os.path.join(d, "memguard.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                     0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def kill_one(pid, start, *, info=proc_info, sleep=time.sleep, kill=os.kill, uid=None):
    """TERM → 等 → KILL;每次送訊號前比 uid + 開始時間。
    動作碼(帶 mem_ 前綴,不跟 room heat release #319 的 detail 代碼撞名):
    mem_terminated(TERM 後結束)/ mem_killed(補 KILL)/ mem_gone(送訊號前已結束)/
    mem_pid_reused(開始時間對不上,不送訊號)/ mem_signal_failed / mem_dry_run。"""
    uid = os.getuid() if uid is None else uid

    def same():
        i = info(pid)
        return bool(i) and i[0] == uid and i[3] == start

    if not same():
        return "mem_pid_reused"
    try:
        kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "mem_gone"
    except OSError:
        return "mem_signal_failed"
    waited = 0.0
    while waited < TERM_WAIT_S:
        sleep(0.2)
        waited += 0.2
        if not same():
            return "mem_terminated"
    try:
        kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "mem_terminated"
    except OSError:
        return "mem_signal_failed"
    return "mem_killed"


class Guard:
    def __init__(self, *, env=None, dry_run=None, limit=None, pids=None, info=proc_info, kill=os.kill,
                 sleep=time.sleep, notify_fn=notify, args_fn=cmd_args, root=None, uid=None, dev_limit=None,
                 cwd_fn=proc_cwd):
        env = os.environ if env is None else env
        self.dry_run = (env.get("EZ2_MEMGUARD_DRY_RUN") == "1") if dry_run is None else dry_run
        self.limit = limit_mb(env) if limit is None else limit
        self.pids, self.info, self.kill, self.sleep = pids, info, kill, sleep
        self.notify_fn, self.args_fn, self.root, self.uid, self.cwd_fn = notify_fn, args_fn, root, uid, cwd_fn
        self.dev_limit = min(self.limit, test_cap_mb(env) or self.limit) if dev_limit is None else dev_limit
        self.args = {}          # (pid, start) -> 指令列(超過 dev 上限才問一次,之後沿用)
        self.tiers = {}         # (pid, start) -> 分級(同上,問一次沿用)
        self.over = {}          # (pid, start) -> 連續超過幾拍
        self.protect = ancestors(info=info)

    def tick(self, now=None):
        """一拍:回這拍收了(或 dry-run 該收)的 [row]。"""
        now = time.time() if now is None else now
        rows, seen, acted = snapshot(uid=self.uid, pids=self.pids, info=self.info), set(), []
        for pid, ppid, name, start, mb in rows:
            if pid in self.protect or mb < self.dev_limit:
                continue
            key = (pid, start)
            if key not in self.tiers:
                self.args[key] = self.args_fn(pid)
                self.tiers[key] = self.tier_of(pid, name, self.args[key])
            tier = self.tiers[key]
            if tier is None:
                continue
            lim = self.dev_limit if tier == "dev" else self.limit
            if mb < lim:
                continue
            seen.add(key)
            self.over[key] = self.over.get(key, 0) + 1
            if self.over[key] < CONFIRM_TICKS:
                continue
            row = {"ts": int(now), "pid": pid, "ppid": ppid, "name": name,
                   "script": script_hint(pid, self.args.get(key) or [name], name), "mb": mb, "limit_mb": lim,
                   "tier": tier}
            if self.dry_run:
                row["action"] = "mem_dry_run"
            else:
                row["action"] = kill_one(pid, start, info=self.info, sleep=self.sleep, kill=self.kill, uid=self.uid)
            _log(row, self.root)
            acted.append(row)
            seen.discard(key)
            self.over.pop(key, None)
            if row["action"] in ("mem_terminated", "mem_killed", "mem_dry_run"):
                self.notify_fn("記憶體守門:%s %s(pid %d)吃到 %.1f GB,已%s" % (
                    name, row["script"] or "", pid, mb / 1024.0, "標記(dry-run)" if self.dry_run else "停止"))
        for k in list(self.over):
            if k not in seen:
                del self.over[k]
        alive = {(r[0], r[3]) for r in rows}
        for k in list(self.args):
            if k not in alive:
                del self.args[k]
                self.tiers.pop(k, None)
        return acted

    def tier_of(self, pid, name, args, depth=0):
        """classify + 目標行程 cwd;multiprocessing 子行程(spawn_main / resource_tracker)沿用父行程的分級 ——
        ProcessPoolExecutor / uvicorn workers / DataLoader 不能因為長得像 -c 就掉到 dev 上限(#322 r2 M2)。"""
        tier = classify(name, args, self.cwd_fn(pid) if self.cwd_fn else None)
        if tier != "inherit":
            return tier
        i = self.info(pid)
        if depth < 8 and i and i[1] > 1:
            pi = self.info(i[1])
            if pi and pi[0] == (os.getuid() if self.uid is None else self.uid) and _kind(pi[2]):
                return self.tier_of(i[1], pi[2], self.args_fn(i[1]), depth + 1)
        return "general"

    def run(self, max_ticks=None, tick_s=TICK_S):
        n = 0
        while max_ticks is None or n < max_ticks:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 守門不得因一拍失敗而死
                print("memguard: 本拍失敗(仍存活):%s" % exc, file=sys.stderr)
            n += 1
            self.sleep(tick_s)


# ── 開發當下就擋(10-24 Andy「從源頭禁止，開發時就要知道不能超大」):aib test 每支測試一個看門執行緒 ──────────
TEST_CAP_DEFAULT_MB = 2048
TEST_CAP_TICK_S = 0.25


def test_cap_mb(env=None):
    """aib test 每支測試(含子孫)單一行程的上限;AIB_TEST_MEM_CAP_MB 可改,不低於 256 MB,0 = 關。"""
    env = os.environ if env is None else env
    n = _env_int(env.get("AIB_TEST_MEM_CAP_MB"))
    if n == 0:
        return 0
    return max(256, n) if n is not None else TEST_CAP_DEFAULT_MB


def descendants(root, table):
    """table: {pid: (ppid, …)};回 root 的所有子孫(不含 root 自己)。"""
    kids = {}
    for pid, row in table.items():
        kids.setdefault(row[0], []).append(pid)
    out, stack = [], list(kids.get(root, []))
    while stack:
        p = stack.pop()
        if p in out:
            continue
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def tree_table(uid=None, info=proc_info):
    """{pid: (ppid, 名字, 開始時間, MB)} —— 自己 uid 的**所有**行程(測試的子孫不只直譯器)。"""
    uid = os.getuid() if uid is None else uid
    t = {}
    for pid in list_pids():
        i = info(pid)
        if i and i[0] == uid:
            t[pid] = (i[1], i[2], i[3], i[4] // 2 ** 20)
    return t


def kill_tree(root, root_start, *, table=tree_table, info=proc_info, kill=os.kill):
    """整棵由葉往根 SIGKILL(根先死的話子孫會變孤兒繼續長);每一支送訊號前再比一次開始時間(防 pid 重用)。
    根的開始時間對不上 ⇒ 什麼都不收。回收掉的 pid 清單。給 TreeCap 與 aib test 逾時共用。"""
    t = table()
    if root not in t or (root_start is not None and t[root][2] != root_start):
        return []
    tree = [root] + descendants(root, t)
    done = []
    for p in reversed(tree):
        i = info(p)
        if not i or i[3] != t[p][2]:
            continue
        try:
            kill(p, signal.SIGKILL)
            done.append(p)
        except OSError:
            pass
    return done


def start_of(pid, info=proc_info):
    i = info(pid)
    return i[3] if i else None


class TreeCap:
    """看一棵行程樹(根 = 測試行程):任何一支超過 cap_mb ⇒ 整棵由葉往根收,記下是誰、多大。
    用法:tc = TreeCap(pid, cap); tc.start(); …等測試結束…; tc.stop(); tc.hit ⇒ None 或 {name, pid, mb, cap_mb}。
    建立時記下根的開始時間;之後每拍與每次送訊號前都比(根被回收、pid 被重用就不動手 —— #322 r2 L3)。"""

    def __init__(self, root_pid, cap_mb, *, tick_s=TEST_CAP_TICK_S, table=tree_table, kill=os.kill, info=proc_info,
                 root_start=None):
        self.root, self.cap, self.tick_s, self.table, self.kill, self.info = (
            root_pid, cap_mb, tick_s, table, kill, info)
        self.root_start = start_of(root_pid, info) if root_start is None else root_start
        self.hit = None
        self._stop = threading.Event()
        self._t = None

    def check_once(self):
        t = self.table()
        if self.root not in t or self.root_start is None or t[self.root][2] != self.root_start:
            return None
        tree = [self.root] + descendants(self.root, t)
        big = max(tree, key=lambda p: t[p][3])
        if t[big][3] < self.cap:
            return None
        self.hit = {"pid": big, "name": t[big][1], "mb": t[big][3], "cap_mb": self.cap}
        kill_tree(self.root, self.root_start, table=lambda: t, info=self.info, kill=self.kill)
        return self.hit

    def _run(self):
        while not self._stop.wait(self.tick_s):
            try:
                if self.check_once():
                    return
            except Exception:  # noqa: BLE001 看門失敗不影響測試本身
                pass

    def start(self):
        if self.cap > 0 and self.root_start is not None:
            self._t = threading.Thread(target=self._run, name="aib-test-memcap", daemon=True)
            self._t.start()
        return self

    def stop(self):
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=2)


def _lock_path(root=None):
    return os.path.join(root or state_root(), "locks", "memguard.lock")


def try_lock(root=None):
    """拿到回 fd(留著不關 = 一直持有),拿不到回 None。"""
    p = _lock_path(root)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _thread_main(dry_run, root=None):
    """拿鎖 → 掃。起不來(Guard() 拋出)⇒ 關掉鎖讓別艙接手、留痕,不無聲停(#322 r2 L4)。"""
    while True:
        fd = try_lock(root)
        if fd is not None:
            break
        time.sleep(LOCK_RETRY_S)
    try:
        Guard(dry_run=dry_run, root=root).run()
    except Exception as exc:  # noqa: BLE001
        _log({"ts": int(time.time()), "action": "mem_guard_failed", "error": type(exc).__name__}, root)
        print("memguard: 守門停了:%s" % exc, file=sys.stderr)
    finally:
        os.close(fd)


def start_thread(env=None):
    """給 idle_reaper watch 用;起不來回 (False, 為什麼),不拋。"""
    env = os.environ if env is None else env
    if env.get("EZ2_MEMGUARD", "1") == "0":
        return False, "EZ2_MEMGUARD=0"
    dry = env.get("EZ2_MEMGUARD_DRY_RUN") == "1"
    t = threading.Thread(target=_thread_main, args=(dry,), name="memguard", daemon=True)
    t.start()
    return True, "memguard 起了(門檻 %d MB,%s)" % (limit_mb(env), "dry-run" if dry else "live")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib memguard", description="單支直譯器行程記憶體暴衝守門")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan")
    s.add_argument("--json", action="store_true")
    w = sub.add_parser("watch")
    w.add_argument("--dry-run", action="store_true")
    lg = sub.add_parser("log")
    lg.add_argument("-n", type=int, default=20)
    a = ap.parse_args(argv)
    if a.cmd in (None, "scan"):
        lim = limit_mb()
        rows = sorted(snapshot(), key=lambda r: -r[4])
        if getattr(a, "json", False):
            print(json.dumps({"limit_mb": lim, "procs": [
                {"pid": r[0], "ppid": r[1], "name": r[2], "mb": r[4]} for r in rows]}, ensure_ascii=False))
        else:
            print("門檻 %d MB;直譯器行程 %d 支" % (lim, len(rows)))
            for r in rows[:15]:
                print("%7d MB  pid %-6d %s" % (r[4], r[0], r[2]))
        return 0
    if a.cmd == "watch":
        if try_lock() is None:
            print("memguard: 別的守門正在跑(%s 被佔住),本支不重複掃" % _lock_path(), file=sys.stderr)
            return 0
        g = Guard(dry_run=True if a.dry_run else None)
        print("memguard: 門檻 %d MB,%s" % (g.limit, "dry-run" if g.dry_run else "live"), file=sys.stderr)
        g.run()
        return 0
    if a.cmd == "log":
        p = os.path.join(state_root(), "logs", "memguard.jsonl")
        try:
            with open(p, encoding="utf-8") as fh:
                lines = fh.readlines()[-max(1, a.n):]
        except OSError:
            lines = []
        sys.stdout.write("".join(lines) if lines else "(沒有紀錄)\n")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
