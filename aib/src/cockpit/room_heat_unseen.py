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
隱私(D4):只出組別、席碼、程式檔名;不出指令列、cwd、路徑。detail 代碼是契約,人話放 hint。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import json
import math
import os
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TICKS_FILE = "cpu-ticks.json"
TICKS_MAX_AGE_S = 120            # 上一次 tick 太舊(或沒有)⇒ 當場量 0.5 秒
TICKS_FALLBACK_S = 0.5
CHURN_FILE = "churn-last.json"
CHURN_LOCK = "churn.lock"
CHURN_DEFAULT_S = 10
CHURN_MAX_S = 30
CHURN_THROTTLE_S = 60
CHURN_TICK_S = 0.02
CHURN_TOP = 10
SHELLS = frozenset(("sh", "bash", "zsh", "dash", "ksh", "fish"))


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
    return busy, busy + idle + iowait


def read_ticks(platform=None):
    """(忙的 tick, 全部 tick) 累計值;量不到 ⇒ None。"""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            return _mac_ticks()
        if platform.startswith("linux"):
            return _linux_ticks()
    except (OSError, ValueError, AttributeError):
        return None
    return None


def cpu_total(ticks_fn=None, now=None, environ=None, sleep=time.sleep, ncpu=None):
    """→ {state, cores, pct, busy_cores, window_s};量不到 ⇒ state unmeasured(不是 0)。
    跟上一次快照存下的 tick 比(頁面每 5 秒讀一次 ⇒ 視窗約 5 秒);沒有或太舊 ⇒ 當場量 0.5 秒。"""
    ticks_fn = ticks_fn or read_ticks
    now = time.time() if now is None else now
    ncpu = ncpu or os.cpu_count()
    cur = ticks_fn()
    if not cur or not ncpu:
        return {"state": "unmeasured", "detail": "cpu_ticks_unavailable"}
    path = os.path.join(_room_dir(environ), TICKS_FILE)
    prev = _read_json(path) or {}
    pb, pt, pts = prev.get("busy"), prev.get("total"), prev.get("t")
    if (_finite(pb) and _finite(pt) and _finite(pts) and 1 <= now - pts <= TICKS_MAX_AGE_S
            and cur[1] > pt and cur[0] >= pb):
        base, window = (pb, pt), now - pts
    else:                                            # 第一次 / 太舊 / 計數器歸零(重開機)
        base, window = cur, TICKS_FALLBACK_S
        sleep(TICKS_FALLBACK_S)
        now += TICKS_FALLBACK_S
        cur = ticks_fn()
        if not cur or cur[1] <= base[1]:
            return {"state": "unmeasured", "detail": "cpu_ticks_unavailable"}
    try:
        _write_json(path, {"schema": "aib-heat-cputicks/v1", "t": round(now, 3), "busy": cur[0], "total": cur[1]})
    except (OSError, ValueError):
        pass                                         # 存不了只是下次多量 0.5 秒
    frac = max(0.0, min(1.0, (cur[0] - base[0]) / float(cur[1] - base[1])))
    return {"state": "ok", "cores": ncpu, "pct": round(frac * 100, 1), "busy_cores": round(frac * ncpu, 2),
            "window_s": round(window, 1)}


def unseen(total, rows):
    """看不到的熱 = 全機在忙的核 − ps 看得到的(活著的行程)合計。量不到任一邊 ⇒ unmeasured。"""
    if total.get("state") != "ok":
        return {"state": "unmeasured", "detail": "cpu_ticks_unavailable"}
    if rows is None:
        return {"state": "unmeasured", "detail": "ps_failed"}
    seen = sum(r[3] for r in rows) / 100.0
    return {"state": "ok", "cores": round(max(0.0, total["busy_cores"] - seen), 2), "seen_cores": round(seen, 2)}


# ---------- 找熱源(短命行程) ----------

_TIMEBASE = None


def _mac_cpu(pid):
    """(ppid, 名字, CPU 秒) 或 None。rusage 的時間單位是 mach 絕對時間(Apple Silicon ≠ 奈秒)。"""
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
        (ru.ri_user_time + ru.ri_system_time) * _TIMEBASE


_CLK = None


def _linux_cpu(pid):
    global _CLK
    if _CLK is None:
        _CLK = float(os.sysconf("SC_CLK_TCK") or 100)
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        name = raw[raw.index("(") + 1:raw.rindex(")")]
        f = raw[raw.rindex(")") + 2:].split()
        return int(f[1]), name, (int(f[11]) + int(f[12])) / _CLK
    except (ValueError, IndexError):
        return None


def program_of(name, args):
    """短命行程的「程式檔名」:shell / 直譯器跑的腳本 ⇒ 腳本檔名;-c / inline ⇒ 直譯器名;其餘 ⇒ 執行檔名。"""
    import memguard as M
    base = os.path.basename(name or "")
    if args:
        if base in SHELLS:
            for a in args[1:]:
                if a == "-c":
                    break
                if not a.startswith("-"):
                    return os.path.basename(a)
        else:
            kind, val = M.script_of(base, args)
            if kind == "script" and val:
                return os.path.basename(val)
            if kind == "module" and val:
                return val
    return base


def _platform_fns(platform):
    import memguard as M
    if platform == "darwin":
        return M._mac_pids, _mac_cpu, M.cmd_args
    if platform.startswith("linux"):
        return M._linux_pids, _linux_cpu, M.cmd_args
    return None


def sample(seconds, pids_fn, cpu_fn, args_fn, clock=time.monotonic, sleep=time.sleep, tick=CHURN_TICK_S):
    """取樣 seconds 秒 → (base 集合, known {pid: (ppid, 名字)}, born [pid], cpu {pid: 最後看到的 CPU 秒}, prog {pid: 程式})。"""
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
            if p not in known:
                known[p] = i[:2]
                born.append(p)
                prog[p] = program_of(i[1], args_fn(p) if os.path.basename(i[1]) in SHELLS
                                     or os.path.basename(i[1]).startswith(("python", "Python", "node", "php", "ruby", "perl"))
                                     else None)
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
        if g in ("other_app", "system"):
            owner = ""                               # 其他 App / 系統:主人欄只會是程式名,這裡已有 program
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
    d = _room_dir(environ)
    os.makedirs(d, mode=0o700, exist_ok=True)
    last_path = os.path.join(d, CHURN_FILE)
    try:
        lock = os.open(os.path.join(d, CHURN_LOCK), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return {"schema": "aib-heat-churn/v1", "state": "error", "detail": "churn_sample_failed"}
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"schema": "aib-heat-churn/v1", "state": "busy", "detail": "churn_running", "throttled": True}
        last = _read_json(last_path) or {}
        started = last.get("started_at")
        if _finite(started) and 0 <= now - started < CHURN_THROTTLE_S:
            cached = _clean_cached(last)
            if cached is None:
                return {"schema": "aib-heat-churn/v1", "state": "busy", "detail": "churn_running", "throttled": True,
                        "retry_after_s": int(CHURN_THROTTLE_S - (now - started)) + 1}
            cached.update(throttled=True, detail="throttled", retry_after_s=int(CHURN_THROTTLE_S - (now - started)) + 1)
            return cached
        _write_json(last_path, {"schema": "aib-heat-churn-last/v1", "started_at": round(now, 1), "result": None})
        try:
            if rows is None:
                rows = room_heat.list_procs() or []
            if cls is None:
                cls = room_heat.classify(rows) if rows else {}
            base, known, born, cpu, prog = sample(seconds, *fns, clock=clock, sleep=sleep)
        except Exception:  # noqa: BLE001 —— 取樣壞了照實回 error,不回空清單假裝「沒熱源」
            base = None
        if not base:                                 # 失敗不佔節流:清掉「正在跑」的記號,下次按得動
            try:
                os.unlink(last_path)
            except OSError:
                pass
            return {"schema": "aib-heat-churn/v1", "state": "error", "detail": "churn_sample_failed"}
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
