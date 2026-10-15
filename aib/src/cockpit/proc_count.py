#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""行程數快滿預警(G11;@abb89 10-15 22:56 派工、維護者准)。

10-15 18:4x 事故:一支刀測無限再入,兩千多個 bash 把行程表塞到 2650/2666,右欄自己先掛、沒人預警。
⇒ 這一支量「這個使用者現在有幾支行程 / 上限」,超過 80% 就讓右欄頂端與巡檢講出來,並列出行程數最多的前幾名指令。

上限:macOS 讀 sysctl kern.maxprocperuid(fork 失敗就是撞這個;算的是同一個 real uid 的行程)。
     Linux 讀 RLIMIT_NPROC(無上限就沒有預警)。
★ 量的時候不 fork:行程表快滿時多開一支 `ps` 本身就可能失敗,還會跟事故搶最後幾格。
  macOS 用 libproc(proc_listpids / proc_name)與 sysctlbyname,都是 ctypes 直接呼叫;Linux 讀 /proc。
  兩條都走不通才退回一次 `ps`(source 標 "ps",呼叫端看得到是退路)。
  實測(Pro,410 支):libproc 一輪 < 1 毫秒。

    python3 proc_count.py [--json] [--top N] [--threshold 0.8]
    check() → {"count", "limit", "ratio", "warn", "threshold", "source", "top": [[指令, 支數], …]}
"""
import argparse
import collections
import ctypes
import ctypes.util
import json
import os
import subprocess
import sys

THRESHOLD = 0.8
TOP_N = 3
PROC_RUID_ONLY = 5          # <sys/proc_info.h>:依 real uid 篩(kern.maxprocperuid 算的就是 real uid)
_NAME_MAX = 256

_libproc = None
_libc = None


def _load():
    global _libproc, _libc
    if sys.platform != "darwin":
        return False
    if _libproc is None:
        try:
            _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
            _libc = ctypes.CDLL(None, use_errno=True)
        except OSError:
            _libproc = False
    return bool(_libproc)


# ── 上限 ──

def limit():
    """(上限, 來源);量不到回 (None, 原因)。"""
    if _load():
        v, sz = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        if _libc.sysctlbyname(b"kern.maxprocperuid", ctypes.byref(v), ctypes.byref(sz), None, 0) == 0 and v.value > 0:
            return v.value, "sysctl"
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_NPROC)[0]
        if soft != resource.RLIM_INFINITY and soft > 0:
            return int(soft), "rlimit"
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    return None, "unknown"


# ── 這個使用者的行程 ──

def _darwin_pids(uid):
    n = _libproc.proc_listpids(PROC_RUID_ONLY, uid, None, 0)
    if n <= 0:
        return None
    buf = (ctypes.c_int * (n // ctypes.sizeof(ctypes.c_int) + 256))()   # 量與取之間會多開幾支:多留空間
    n = _libproc.proc_listpids(PROC_RUID_ONLY, uid, buf, ctypes.sizeof(buf))
    if n <= 0:
        return None
    return [p for p in buf[: n // ctypes.sizeof(ctypes.c_int)] if p > 0]


def _darwin_name(pid, nb):
    k = _libproc.proc_name(pid, nb, _NAME_MAX)
    return (nb.value.decode("utf-8", "replace").strip() or None) if k > 0 else None   # 名字最多 32 字,截斷處會留空白


def _linux_procs(uid):
    try:
        ents = os.listdir("/proc")
    except OSError:
        return None
    out = []
    for e in ents:
        if not e.isdigit():
            continue
        try:
            st = os.stat("/proc/" + e)
            if st.st_uid != uid:
                continue
            with open("/proc/%s/comm" % e, encoding="utf-8", errors="replace") as f:
                out.append(f.read().strip() or None)
        except OSError:
            continue        # 量的當下結束了
    return out


def _ps_procs(uid):
    try:
        r = subprocess.run(["ps", "-U", str(uid), "-o", "comm="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [os.path.basename(x.strip()) for x in r.stdout.splitlines() if x.strip()]


def user_processes(uid=None, names=True, allow_fork=True):
    """(行程名清單, 來源)。names=False 只要支數時,macOS 不逐支問名字。量不到回 (None, "unknown")。
    allow_fork=False:右欄每幾秒畫一次,不准退回 `ps`(快滿時那一支 fork 正是壓垮的最後一根)。"""
    uid = os.getuid() if uid is None else uid
    if _load():
        pids = _darwin_pids(uid)
        if pids is not None:
            if not names:
                return [None] * len(pids), "libproc"
            nb = ctypes.create_string_buffer(_NAME_MAX)
            return [_darwin_name(p, nb) for p in pids], "libproc"
    if sys.platform.startswith("linux"):
        got = _linux_procs(uid)
        if got is not None:
            return got, "proc"
    got = _ps_procs(uid) if allow_fork else None
    if got is not None:
        return got, "ps"
    return None, "unknown"


def threshold_from_env(env=None):
    """AIB_PROC_WARN_PCT(1–100)可調門檻;沒設或不合法 ⇒ 80%。"""
    env = os.environ if env is None else env
    try:
        pct = float(env.get("AIB_PROC_WARN_PCT") or "")
    except ValueError:
        return THRESHOLD
    return pct / 100.0 if 0 < pct <= 100 else THRESHOLD


def check(threshold=None, top_n=TOP_N, uid=None, lim=None, allow_fork=True):
    """threshold 沒給 ⇒ threshold_from_env()。lim 給測試用:(上限, 來源)。"""
    threshold = threshold_from_env() if threshold is None else threshold
    lim_v, lim_src = limit() if lim is None else lim
    procs, src = user_processes(uid, names=top_n > 0, allow_fork=allow_fork)
    rec = {"count": None, "limit": lim_v, "limit_source": lim_src, "ratio": None, "warn": False,
           "threshold": threshold, "source": src, "top": []}
    if procs is None:
        return rec
    rec["count"] = len(procs)
    if top_n > 0:
        c = collections.Counter(n or "?" for n in procs)
        rec["top"] = [[n, k] for n, k in sorted(c.items(), key=lambda x: (-x[1], x[0]))[:top_n]]
    if lim_v:
        rec["ratio"] = round(len(procs) / float(lim_v), 4)
        rec["warn"] = rec["ratio"] >= threshold
    return rec


def T(key, **params):
    try:
        import i18n_lib
        return i18n_lib.t("aib.proc_count." + key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:   # noqa: BLE001  字典壞了也要講得出話
        return "aib.proc_count." + key + (" " + json.dumps(params, ensure_ascii=False) if params else "")


def top_text(top):
    return T("top_sep").join(T("top_item", name=n, n=k) for n, k in top)


def warn_line(rec):
    """右欄頂端那一行;沒超過門檻回空字串。"""
    if not rec.get("warn"):
        return ""
    return T("warn", count=rec["count"], limit=rec["limit"], pct=int(round(rec["ratio"] * 100)), top=top_text(rec["top"]))


def report_lines(rec):
    """巡檢用:一行總量(超過門檻加警告字)+ 一行前幾名。"""
    if rec["count"] is None:
        return [T("unknown")]
    if rec["limit"]:
        head = T("report_warn" if rec["warn"] else "report_ok", count=rec["count"], limit=rec["limit"],
                 pct=int(round(rec["ratio"] * 100)), threshold=int(round(rec["threshold"] * 100)))
    else:
        head = T("report_no_limit", count=rec["count"])
    return [head, T("report_top", top=top_text(rec["top"]))]


def main(argv=None):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="proc_count", description="per-user process count vs kern.maxprocperuid")
    p.add_argument("--json", action="store_true")
    p.add_argument("--top", type=int, default=TOP_N)
    p.add_argument("--threshold", type=float, default=None, help="0–1; default AIB_PROC_WARN_PCT or 0.8")
    p.add_argument("--warn-line", action="store_true", help="print only the right-column warning line (empty if fine)")
    a = p.parse_args(argv)
    rec = check(a.threshold, a.top)
    if a.json:
        print(json.dumps(rec, ensure_ascii=False))
    elif a.warn_line:
        line = warn_line(rec)
        if line:
            print(line)
    else:
        print("\n".join(report_lines(rec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
