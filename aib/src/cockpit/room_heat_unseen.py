#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 看不到的那一半(10-25 @044e0,RULING-room-heat-local-opt 第 1 張)。

  cpu_total()   全機 CPU(macOS host_statistics / Linux /proc/stat 的 tick 差);快照每次叫,不 fork
  churn()       `aib room heat churn [--seconds N] [--json]`:取樣 N 秒(預設 10、上限 30)內新開的短命行程,
                歸到「取樣開始時就在的祖先」⇒ 組別 / 席碼 / 程式檔名;只在按「找熱源」或 CLI 時跑(D1)

為什麼要有:ps 只看得到快照當下活著的行程;狀態列、右欄、hook、測試子行程一閃即逝,
10-25 實測全機 6.95 核裡 ≥2.6 核是它們,主機房卻顯示「沒什麼在跑」。
churn 取樣本身約 0.1–0.2 核 ⇒ 核心節流 60 秒一次,第二次回上次結果 + throttled(D1)。
非 macOS / Linux ⇒ unsupported_platform;不叫任何平台專用指令(D2)。
隱私(D4 / E3):只出組別、席碼、程式檔名;不出指令列、cwd、路徑。只對自己帳號的直譯器 / shell 讀 argv,
而且只留腳本檔名(不然 pane-right-tabs.sh 這類只會顯示 bash,已知熱點表也認不出來);別的帳號一律「?」。
detail 代碼是契約,人話放 hint。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import json
import math
import os
import re
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TICKS_FILE = "cpu-ticks.json"
TICKS_MAX_AGE_S = 120            # 上一次 tick 太舊(或沒有)⇒ 當場量 1 秒
TICKS_FALLBACK_S = 1.0           # macOS 的 tick 約每 1 秒才更新一次(實測間隔最長 0.97 秒),0.5 秒常讀到同一個值
CHURN_FILE = "churn-last.json"
CHURN_LOCK = "churn.lock"
CHURN_DEFAULT_S = 10
CHURN_MAX_S = 30
CHURN_THROTTLE_S = 60
CHURN_TICK_S = 0.02
CHURN_TOP = 10
SHELLS = frozenset(("sh", "bash", "zsh", "dash", "ksh", "fish"))
UNKNOWN = "?"


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _room_dir(environ=None):
    import room_heat
    return room_heat.state_dir(environ)


def _read_json(path):
    import room_heat
    raw = room_heat.safe_read(path, 256 * 1024)
    try:
        d = json.loads(raw) if raw else None
    except (ValueError, RecursionError):
        return None
    return d if isinstance(d, dict) else None


def _write_json(path, obj):
    """原子寫入、0600;room 被換成 symlink / 別人的夾 ⇒ 不寫(跟 heat-history 同一套)。回是否有寫。"""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, allow_nan=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


# ---------- 全機 CPU ----------

_HOST = None


def _mac_ticks():
    global _HOST
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.dylib")
    if _HOST is None:
        libc.mach_host_self.restype = ctypes.c_uint32
        _HOST = libc.mach_host_self()               # 只拿一次(每叫一次多一個 port 參照)
    ticks = (ctypes.c_uint32 * 4)()                  # user, system, idle, nice
    count = ctypes.c_uint32(4)
    if libc.host_statistics(ctypes.c_uint32(_HOST), 3, ticks, ctypes.byref(count)) != 0:   # HOST_CPU_LOAD_INFO
        return None
    busy = ticks[0] + ticks[1] + ticks[3]
    return busy, busy + ticks[2]


def _linux_ticks(path="/proc/stat"):
    with open(path, encoding="ascii", errors="replace") as fh:
        f = fh.readline().split()
    if not f or f[0] != "cpu":
        return None
    v = [int(x) for x in f[1:9]] + [0] * max(0, 9 - len(f))
    user, nice, system, idle, iowait, irq, softirq, steal = v[:8]
    busy = user + nice + system + irq + softirq + steal
    return busy, busy + idle + iowait, steal


def read_ticks(platform=None):
    """(忙的 tick, 全部 tick[, steal]) 累計值;量不到 ⇒ None。"""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            return _mac_ticks()
        if platform.startswith("linux"):
            return _linux_ticks()
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _ps_seconds(t):
    """ps 的 time 欄:macOS「83:01.80」(分可 > 59)、Linux「01:23:45」/「2-01:23:45」⇒ 秒;讀不懂 ⇒ None。"""
    try:
        days, _, rest = t.rpartition("-")
        parts = [float(x) for x in rest.split(":")]
    except ValueError:
        return None
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec + (int(days) * 86400 if days else 0)


def proc_times(run=None):
    """{pid: 累計 CPU 秒}(所有帳號;ps 是 setuid,libproc 讀不到 root 的行程)。跑不了 ⇒ None。"""
    import room_heat
    try:
        out = (run or room_heat._run)(["ps", "-axo", "pid=,time="])
    except Exception:  # noqa: BLE001
        return None
    got = {}
    for line in (out or "").splitlines():
        f = line.split()
        if len(f) == 2 and f[0].isdigit():
            v = _ps_seconds(f[1])
            if v is not None:
                got[int(f[0])] = v
    return got or None


def seen_cores(prev, cur, window):
    """兩次快照之間,活著的行程用掉的 CPU(核)。這段時間新開還活著的 ⇒ 整段都算(它在 ps 看得到);
    pid 被重用(數字變小)⇒ 照新的算。開了又結束的不在兩次之中 ⇒ 正是「看不到的熱」。
    「新的」累計時間超過時間窗 2 倍 ⇒ 不可能是剛開的,是上一次 ps 漏列(實測偶爾一次冒出 48 核)⇒ 不算。"""
    used = 0.0
    for pid, c in cur.items():
        p = prev.get(pid)
        if p is not None and c >= p:
            used += c - p
        elif c <= window * 2:
            used += c
    return used / window


def cpu_total(ticks_fn=None, now=None, environ=None, sleep=time.sleep, ncpu=None, times_fn=None):
    """→ {state, cores, pct, busy_cores, window_s, seen_cores?, steal_pct?};量不到 ⇒ state unmeasured(不是 0)。
    跟上一次快照存下的 tick 比(頁面每 5 秒讀一次 ⇒ 視窗約 5 秒);沒有或太舊 ⇒ 當場量 1 秒。
    同一個時間窗順便存每個行程的累計 CPU(ps time),seen_cores 才跟 busy_cores 對得上(審查 M1:
    ps 的 pcpu 是 macOS 約 1 分鐘衰減 / Linux 一輩子平均,拿來減會把 unseen 夾成假的 0)。"""
    ticks_fn = ticks_fn or read_ticks
    times_fn = times_fn or proc_times
    now = time.time() if now is None else now
    ncpu = ncpu or os.cpu_count()
    cur = ticks_fn()
    if not cur or not ncpu:
        return {"state": "unmeasured", "detail": "cpu_ticks_unavailable"}
    cur_t = times_fn()
    path = os.path.join(_room_dir(environ), TICKS_FILE)
    prev = _read_json(path) or {}
    pb, pt, pts = prev.get("busy"), prev.get("total"), prev.get("t")
    pprocs = prev.get("procs") if isinstance(prev.get("procs"), dict) else None
    if (_finite(pb) and _finite(pt) and _finite(pts) and 1 <= now - pts <= TICKS_MAX_AGE_S
            and cur[1] > pt and cur[0] >= pb):
        base = (pb, pt, prev["steal"]) if _finite(prev.get("steal")) else (pb, pt)
        window = now - pts
        base_t = {int(k): v for k, v in pprocs.items() if str(k).isdigit() and _finite(v)} if pprocs else None
    else:                                            # 第一次 / 太舊 / 計數器歸零(重開機)
        base, window, base_t = cur, TICKS_FALLBACK_S, cur_t
        sleep(TICKS_FALLBACK_S)
        now += TICKS_FALLBACK_S
        cur = ticks_fn()
        if cur and cur[1] <= base[1]:                # 剛好卡在更新邊界 ⇒ 再等半秒一次
            sleep(TICKS_FALLBACK_S / 2)
            now += TICKS_FALLBACK_S / 2
            window += TICKS_FALLBACK_S / 2
            cur = ticks_fn()
        cur_t = times_fn()
        if not cur or cur[1] <= base[1]:
            return {"state": "unmeasured", "detail": "cpu_ticks_unavailable"}
    try:
        rec = {"schema": "aib-heat-cputicks/v1", "t": round(now, 3), "busy": cur[0], "total": cur[1]}
        if len(cur) > 2:
            rec["steal"] = cur[2]
        if cur_t:
            rec["procs"] = {str(k): round(v, 2) for k, v in cur_t.items()}
        _write_json(path, rec)
    except (OSError, ValueError):
        pass                                         # 存不了只是下次多量 1 秒
    span = float(cur[1] - base[1])
    frac = max(0.0, min(1.0, (cur[0] - base[0]) / span))
    out = {"state": "ok", "cores": ncpu, "pct": round(frac * 100, 1), "busy_cores": round(frac * ncpu, 2),
           "window_s": round(window, 1)}
    if len(cur) > 2 and len(base) > 2:               # Linux 虛擬機被宿主拿走的(steal)另列;busy 已含
        out["steal_pct"] = round(max(0.0, (cur[2] - base[2]) / span) * 100, 1)
    if cur_t and base_t:
        out["seen_cores"] = round(seen_cores(base_t, cur_t, window), 2)
    return out


def unseen(total):
    """看不到的熱 = 全機在忙的核 − 同一時間窗內活著的行程用掉的核。
    量不到任一邊 ⇒ unmeasured;看得到的比在忙的還多一截(時間窗對不上)⇒ unmeasured window_mismatch,不回 0。"""
    if total.get("state") != "ok":
        return {"state": "unmeasured", "detail": "cpu_ticks_unavailable"}
    seen = total.get("seen_cores")
    if not _finite(seen):
        return {"state": "unmeasured", "detail": "ps_failed"}
    busy = total["busy_cores"]
    if seen > busy + max(0.3, busy * 0.15):
        return {"state": "unmeasured", "detail": "window_mismatch", "seen_cores": seen}
    return {"state": "ok", "cores": round(max(0.0, busy - seen), 2), "seen_cores": round(seen, 2)}


# ---------- 找熱源(短命行程) ----------

_TIMEBASE = None


def _mac_cpu(pid):
    """(ppid, 名字, CPU 秒, uid) 或 None。rusage 的時間單位是 mach 絕對時間(Apple Silicon ≠ 奈秒)。"""
    global _TIMEBASE
    import memguard as M
    if _TIMEBASE is None:
        class TB(ctypes.Structure):
            _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
        tb = TB()
        ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.dylib").mach_timebase_info(ctypes.byref(tb))
        _TIMEBASE = tb.numer / float(tb.denom or 1) / 1e9
    lib = M._libproc()
    bi = M._BsdInfo()
    if lib.proc_pidinfo(int(pid), 3, ctypes.c_uint64(0), ctypes.byref(bi), ctypes.sizeof(bi)) != ctypes.sizeof(bi):
        return None
    if bi.pbi_pid != pid:
        return None
    ru = M._RusageV0()
    if lib.proc_pid_rusage(int(pid), 0, ctypes.byref(ru)) != 0:
        return None
    return bi.pbi_ppid, (bi.pbi_name or bi.pbi_comm).decode("utf-8", "replace"), \
        (ru.ri_user_time + ru.ri_system_time) * _TIMEBASE, bi.pbi_uid


_CLK = None


def _linux_cpu(pid):
    global _CLK
    if _CLK is None:
        _CLK = float(os.sysconf("SC_CLK_TCK") or 100)
    try:
        uid = os.stat("/proc/%d" % pid).st_uid
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        name = raw[raw.index("(") + 1:raw.rindex(")")]
        f = raw[raw.rindex(")") + 2:].split()
        return int(f[1]), name, (int(f[11]) + int(f[12])) / _CLK, uid
    except (ValueError, IndexError):
        return None


SCRIPT_NAME_RE = re.compile(r"^(?=.{1,40}$)[\w.\-]+\.(sh|bash|zsh|py|js)$")    # @ee4f8 E3 裁定 a / e
INLINE_SHORT_RE = {"perl": re.compile(r"^-[a-zA-Z]*[eE]"), "ruby": re.compile(r"^-[a-zA-Z]*e")}   # 裁定 d:-ne / -lne
PHP_INLINE = frozenset(("-r", "-B", "-R", "-F", "-E"))
MODULE_RE = re.compile(r"^[\w.]{1,64}$")
SHELL_VALUE_OPTS = frozenset(("-o", "+o", "-O", "+O", "--rcfile", "--init-file"))
SHELL_SAFE_LONG = frozenset(("--login", "--norc", "--noprofile", "--posix", "--noediting", "--restricted"))


def _shell_script(args):
    """shell 的腳本引數;遇到會讀指令字串 / stdin / 互動的旗標(短旗標群含 c、s 或 i:-c、-ec、-lc、-s、-i…)、
    `--`、不認得的長旗標 ⇒ None(審查 H1:`sh -ec "psql postgres://user:pw@db"` 曾被當成腳本名;@ee4f8 裁定 b / c)。"""
    it = iter(args[1:])
    for a in it:
        if a == "--" or a == "-":
            return None
        if a in SHELL_VALUE_OPTS:
            next(it, None)                           # 連值一起跳過
            continue
        if a.startswith("--"):
            if a in SHELL_SAFE_LONG:
                continue
            return None
        if a[:1] in "-+" and len(a) > 1:
            if any(ch in a[1:] for ch in "csi"):
                return None
            continue
        return a
    return None


def _script_name(path):
    """只收「沒有空白、檔名整串像腳本」的;其他一律不收(寧可只顯示直譯器名,也不帶出可能是秘密的字)。"""
    if not path or any(c.isspace() for c in path):
        return None
    b = os.path.basename(path)
    return b if SCRIPT_NAME_RE.match(b) else None


def _inline_flag(base, args):
    """perl / ruby 合寫的 -e(-ne、-lne)、php 的 -r / -B / -R / -F / -E:碰到第一個非選項之前出現 ⇒ True(裁定 d)。"""
    kind = next((k for k in ("perl", "ruby", "php") if base.lower().startswith(k)), None)
    if not kind:
        return False
    for a in args[1:]:
        if a == "--" or not a.startswith("-"):
            return False
        if (kind == "php" and a in PHP_INLINE) or (kind != "php" and INLINE_SHORT_RE[kind].match(a)):
            return True
    return False


def program_of(name, args):
    """短命行程的「程式檔名」:shell / 直譯器跑的腳本 ⇒ 腳本檔名;-c / inline / 看不懂 ⇒ 直譯器名;其餘 ⇒ 執行檔名。
    argv 只在這裡看一眼取腳本檔名,不存、不輸出(E3 例外,@ee4f8 10-25 06:19 有條件准:a–f)。"""
    import memguard as M
    base = os.path.basename(name or "")
    if args:
        if base in SHELLS:
            return _script_name(_shell_script(args)) or base
        if _inline_flag(base, args):
            return base
        kind, val = M.script_of(base, args)
        if kind == "script":
            return _script_name(val) or base
        if kind == "module" and val and MODULE_RE.match(val):
            return val
    return base


def _platform_fns(platform):
    import memguard as M
    if platform == "darwin":
        return M._mac_pids, _mac_cpu, M.cmd_args
    if platform.startswith("linux"):
        return M._linux_pids, _linux_cpu, M.cmd_args
    return None


def sample(seconds, pids_fn, cpu_fn, args_fn, clock=time.monotonic, sleep=time.sleep, tick=CHURN_TICK_S, uid=None):
    """取樣 seconds 秒 → (base 集合, known {pid: (ppid, 名字)}, born [pid], cpu {pid: 最後看到的 CPU 秒}, prog {pid: 程式})。
    E3:別的帳號(含 root)的新行程只記父子關係與 CPU,程式名記成 UNKNOWN、不讀 argv。"""
    uid = os.getuid() if uid is None else uid
    known, base = {}, set()
    for p in pids_fn():
        i = cpu_fn(p)
        if i:
            known[p] = i[:2]
            base.add(p)
    born, cpu, prog = [], {}, {}
    t0 = clock()
    while clock() - t0 < seconds:
        for p in pids_fn():
            if p in base:
                continue
            i = cpu_fn(p)
            if not i:
                continue
            if p not in known or (p in prog and known[p][1] != i[1]):
                if p not in known:                   # fork 完、exec 前看到的是父行程的名字 ⇒ 名字變了就重記程式名
                    born.append(p)
                known[p] = i[:2]
                if len(i) > 3 and i[3] != uid:
                    prog[p] = UNKNOWN
                else:
                    base_name = os.path.basename(i[1])
                    wants = base_name in SHELLS or base_name.startswith(("python", "Python", "node", "php", "ruby", "perl"))
                    args = args_fn(p) if wants else None
                    if wants:                        # 讀完 argv 再核一次 uid / 名字:中間 pid 被重用就不算(@ee4f8 L3)
                        j = cpu_fn(p)
                        if not j or j[1] != i[1] or (len(j) > 3 and j[3] != uid):
                            args = None
                    prog[p] = program_of(i[1], args)
            cpu[p] = i[2]
        sleep(tick)
    return base, known, born, cpu, prog


def attribute(base, known, born, cpu, prog, cls, seconds, top=CHURN_TOP):
    """每支新行程往上找第一個「取樣開始就在」的祖先 ⇒ 那個祖先的組別 / 席碼 + 最上層新行程的程式名。"""
    import room_heat
    acc = {}
    for p in born:
        cur, root = p, None
        for _ in range(64):
            pp = known.get(cur, (0, ""))[0]
            if pp in base or pp in (0, 1) or pp not in known:
                root = pp
                break
            cur = pp
        g, owner = cls.get(root, ("other_app", "")) if root not in (None, 0, 1) else ("other_app", "")
        if prog.get(cur) == UNKNOWN:                 # E3:別的帳號 ⇒ 未知,不出名字
            g = "system"
        if g in ("other_app", "system"):
            owner = ""                               # 其他 App / 系統:主人欄只會是程式名,這裡已有 program
        owner = room_heat.safe_name(owner, 12)       # 現跑結果也洗(@ee4f8 L2;讀快取那條本來就洗)
        program = room_heat.safe_name(prog.get(cur) or known.get(cur, (0, ""))[1], 40)
        a = acc.setdefault((g, owner, program), {"group": g, "owner": owner, "program": program, "count": 0, "cpu_s": 0.0})
        a["count"] += 1
        a["cpu_s"] += cpu.get(p, 0.0)
    items = sorted(acc.values(), key=lambda a: (-a["cpu_s"], -a["count"]))[:top]
    out = []
    for a in items:
        out.append({"group": a["group"], "owner": a["owner"], "program": a["program"], "count": a["count"],
                    "births_per_s": round(a["count"] / seconds, 2), "cpu_cores_lower_bound": round(a["cpu_s"] / seconds, 3)})
    return out


def _clean_cached(d):
    """上一次結果是同帳號可改的檔:只放回白名單欄位,名字再洗一次。"""
    import room_heat
    r = d.get("result") if isinstance(d, dict) else None
    if not isinstance(r, dict) or r.get("state") != "ok":
        return None
    top = []
    for x in r.get("top") if isinstance(r.get("top"), list) else []:
        if not isinstance(x, dict) or x.get("group") not in room_heat.GROUPS:
            continue
        top.append({"group": x["group"], "owner": room_heat.safe_name(x.get("owner"), 12),
                    "program": room_heat.safe_name(x.get("program"), 40),
                    **{k: x[k] if _finite(x.get(k)) else 0 for k in ("count", "births_per_s", "cpu_cores_lower_bound")}})
    out = {"schema": "aib-heat-churn/v1", "state": "ok", "top": top}
    for k in ("t", "seconds", "births", "births_per_s", "cpu_cores_lower_bound"):
        out[k] = r[k] if _finite(r.get(k)) else None
    return out


def churn(seconds=CHURN_DEFAULT_S, now=None, environ=None, platform=None, fns=None, rows=None, cls=None,
          clock=time.monotonic, sleep=time.sleep):
    """→ {schema, state, seconds, births, births_per_s, cpu_cores_lower_bound, top:[…], throttled}。
    60 秒內第二次 ⇒ 上次結果 + throttled;正在跑 ⇒ state busy;平台不支援 ⇒ unsupported。"""
    import room_heat
    now = time.time() if now is None else now
    seconds = max(1, min(int(seconds), CHURN_MAX_S))
    platform = platform or sys.platform
    fns = fns or _platform_fns(platform)
    if not fns:
        return {"schema": "aib-heat-churn/v1", "state": "unsupported", "detail": "unsupported_platform"}
    fail = {"schema": "aib-heat-churn/v1", "state": "error", "detail": "churn_sample_failed"}
    d = _room_dir(environ)
    last_path = os.path.join(d, CHURN_FILE)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        st = os.lstat(d)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():     # room 被換成 symlink / 別人的夾(審查 L6)
            return fail
        lock = os.open(os.path.join(d, CHURN_LOCK), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return fail
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"schema": "aib-heat-churn/v1", "state": "busy", "detail": "churn_running", "throttled": True}
        # 正在跑 = 有人拿著鎖(上面就回 busy);拿得到鎖卻只有半份紀錄 = 上一次被砍,照常重跑(審查 L1)
        last = _read_json(last_path) or {}
        started = last.get("started_at")
        cached = _clean_cached(last) if _finite(started) and 0 <= now - started < CHURN_THROTTLE_S else None
        if cached is not None:
            cached.update(throttled=True, detail="throttled", cached_at=round(started, 1),
                          retry_after_s=int(CHURN_THROTTLE_S - (now - started)) + 1)
            return cached
        try:
            if rows is None:
                rows = room_heat.list_procs() or []
            if cls is None:
                cls = room_heat.classify(rows) if rows else {}
            base, known, born, cpu, prog = sample(seconds, *fns, clock=clock, sleep=sleep)
        except Exception:  # noqa: BLE001 —— 取樣壞了照實回 error,不回空清單假裝「沒熱源」
            base = None
        if not base:                                 # 失敗不寫紀錄 ⇒ 不佔節流,下次按得動
            return fail
        top = attribute(base, known, born, cpu, prog, cls, seconds)
        total_cpu = sum(cpu.get(p, 0.0) for p in born)
        r = {"schema": "aib-heat-churn/v1", "state": "ok", "t": round(now, 1), "seconds": seconds,
             "births": len(born), "births_per_s": round(len(born) / seconds, 2),
             "cpu_cores_lower_bound": round(total_cpu / seconds, 3), "top": top, "throttled": False}
        try:
            _write_json(last_path, {"schema": "aib-heat-churn-last/v1", "started_at": round(now, 1), "result": r})
        except (OSError, ValueError):
            pass
        return r
    finally:
        os.close(lock)                               # 關 fd 就放鎖
