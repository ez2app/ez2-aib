#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 的「釋放」:只收沒人用的殘留(白名單),不碰活席、真人的 App、系統行程、別的帳號、不認得的。

  aib room heat release <候選編號>… [--dry-run] [--json]
  aib room heat release --all-safe [--auto] [--dry-run] [--json]

白名單(這一版兩種):
  chrome_orphan  自動化旗標的瀏覽器主行程 + 父行程 1 + --user-data-dir 在暫存根底下(真人用除錯埠開的 Chrome 不算)
  test_orphan    父行程 1、指令列帶暫存根底下的測試 / 審查員暫存路徑;它的子孫也要各自符合判法才一起收
一律不收(只顯示在 held):行程或子孫有 ssh / scp / rsync、正在對主機動手的 aib 指令、開著 *.lock 檔 —— 殺在中間主機會半套。

安全:
  - 候選編號 = 種類 + pid + 開始時間(微秒)+ uid 的雜湊;釋放前重掃一次,編號對不上 ⇒ candidate_changed,不殺。
  - 每個 pid 送訊號前再比一次 uid + 開始時間(防 pid 重用);只殺列出的 pid,不用名字比對、不整個行程群組殺。
  - 不殺自己與自己的祖先行程。
  - 每次釋放寫一筆 room/heat-released.jsonl(時間、種類、主人、省下多少、手動 / 自動),不寫指令列。
  - --auto(付費自動釋放用):10 分鐘最多收 AUTO_MAX_PER_WINDOW 個,超過 ⇒ auto_limit 停手。
錯誤代碼:bad_candidate candidate_changed not_releasable release_failed partial auto_limit(detail 是契約)。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TERM_WAIT_S = 5.0
AUTO_WINDOW_S = 600
AUTO_MAX_PER_WINDOW = 20
ID_RE = re.compile(r"^[0-9a-f]{16}$")
HOST_RE = re.compile(r"(^|[\s/])(ssh|scp|rsync|sftp)(\s|$|\.)|\bhostguard\b|\bsecret\s+ask\b|--push\b")
TEST_MARK_RE = re.compile(r"/tests/test-[^\s/]*\.py|/scratchpad/|/aib-[a-z0-9-]+/")


def temp_roots():
    """暫存根:系統暫存夾(含 realpath)、/tmp、/private/tmp。可用 AIB_HEAT_TEMP_ROOTS 加(冒號分隔,測試用)。"""
    roots = {tempfile.gettempdir(), "/tmp", "/private/tmp"}
    roots |= {os.path.realpath(r) for r in list(roots)}
    roots |= {r for r in os.environ.get("AIB_HEAT_TEMP_ROOTS", "").split(":") if r}
    return sorted(r.rstrip("/") + "/" for r in roots if r)


def _under_temp(s, roots=None):
    return any(r in s for r in (roots or temp_roots()))


# ---------- 行程身分(uid + 開始時間到微秒)----------

class _BsdInfo(ctypes.Structure):
    _fields_ = [("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32), ("pbi_xstatus", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_uint32), ("pbi_ppid", ctypes.c_uint32),
                ("pbi_uid", ctypes.c_uint32), ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
                ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32),
                ("rfu_1", ctypes.c_uint32), ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
                ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32), ("pbi_pjobc", ctypes.c_uint32),
                ("e_tdev", ctypes.c_uint32), ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
                ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64)]


_LIBPROC = None


def _libproc():
    global _LIBPROC
    if _LIBPROC is None:
        path = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        _LIBPROC = ctypes.CDLL(path, use_errno=True)
    return _LIBPROC


def identity(pid):
    """(uid, ppid, 開始時間字串, 開始秒) 或 None(行程不在 / 讀不到)。macOS 用 proc_pidinfo 到微秒;Linux 讀 /proc。"""
    if sys.platform == "darwin":
        info = _BsdInfo()
        try:
            n = _libproc().proc_pidinfo(int(pid), 3, ctypes.c_uint64(0), ctypes.byref(info), ctypes.sizeof(info))
        except (OSError, AttributeError):
            return None
        if n != ctypes.sizeof(info) or info.pbi_pid != pid:
            return None
        return (info.pbi_uid, info.pbi_ppid, "%d.%06d" % (info.pbi_start_tvsec, info.pbi_start_tvusec),
                float(info.pbi_start_tvsec))
    try:
        with open("/proc/%d/stat" % pid) as fh:
            stat = fh.read()
        fields = stat.rsplit(")", 1)[1].split()
        ppid, ticks = int(fields[1]), int(fields[19])
        uid = os.stat("/proc/%d" % pid).st_uid
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as fh:
            boot = time.time() - float(fh.read().split()[0])
        return uid, ppid, "t%d" % ticks, boot + ticks / float(hz)
    except (OSError, ValueError, IndexError):
        return None


def _protected():
    """自己與祖先(不准殺)。"""
    out, p = set(), os.getpid()
    for _ in range(64):
        out.add(p)
        ident = identity(p)
        if not ident or ident[1] in (0, 1) or ident[1] in out:
            break
        p = ident[1]
    return out


# ---------- 候選 ----------

def _children(rows):
    kids = {}
    for r in rows:
        kids.setdefault(r[1], []).append(r[0])
    return kids


def _descendants(pid, kids, limit=500):
    out, stack = [], list(kids.get(pid, []))
    while stack and len(out) < limit:
        p = stack.pop()
        if p in out:
            continue
        out.append(p)
        stack += kids.get(p, [])
    return out


def _holds_lock(pids, run=None):
    """開著 *.lock 檔?lsof 跑不了 ⇒ 當作有(往不收那邊錯)。"""
    try:
        out = (run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=5,
                                                stdin=subprocess.DEVNULL).stdout))(
            ["lsof", "-n", "-P", "-Fn", "-p", ",".join(str(p) for p in pids)])
    except Exception:  # noqa: BLE001
        return True
    return any(ln.startswith("n") and ln.rstrip().endswith(".lock") for ln in (out or "").splitlines())


def scan(rows=None, run=None, owner_of=None, lsof=None, now=None):
    """→ (candidates, held)。rows 照 room_heat.list_procs 的格式;owner 只拿來顯示。"""
    import room_heat
    import chrome_orphans
    rows = room_heat.list_procs(run) if rows is None else rows
    if rows is None:
        return None, None
    now = time.time() if now is None else now
    me = os.getuid()
    roots = temp_roots()
    by = {r[0]: r for r in rows}
    kids = _children(rows)
    protected = _protected()
    cands, held = [], []
    for pid, ppid, uid, _cpu, _rss, _name, args in rows:
        if uid != me or ppid != 1 or pid in protected:
            continue
        kind = None
        if chrome_orphans.is_automation_main(args):
            udd = chrome_orphans.user_data_dir(args) or ""
            if udd and _under_temp(udd if udd.endswith("/") else udd + "/", roots):
                kind = "chrome_orphan"
        elif _under_temp(args, roots) and TEST_MARK_RE.search(args):
            kind = "test_orphan"
        if not kind:
            continue
        desc = _descendants(pid, kids)
        everyone = [pid] + desc
        owner = ""
        try:
            seat, _pane = (owner_of or chrome_orphans.owner_of)(pid)
            owner = seat or ""
        except Exception:  # noqa: BLE001
            pass
        if any(HOST_RE.search(by[p][6]) for p in everyone if p in by):
            held.append({"kind": kind, "owner": owner, "why": "host_operation"})
            continue
        if (lsof or _holds_lock)(everyone):
            held.append({"kind": kind, "owner": owner, "why": "holds_lock"})
            continue
        if kind == "chrome_orphan":
            pids = [pid]                       # 主行程收了,它的 helper 跟著結束
        else:                                   # 子孫要各自符合判法(同帳號、指令列在暫存根底下)才一起收
            pids = [pid] + [p for p in desc if p in by and by[p][2] == me and p not in protected
                            and _under_temp(by[p][6], roots)]
        ident = identity(pid)
        if not ident or ident[0] != me or ident[1] != 1:
            continue
        idents = {p: identity(p) for p in pids}
        pids = [p for p in pids if idents.get(p) and idents[p][0] == me]
        cid = hashlib.sha256(("%s|%d|%s|%d" % (kind, pid, ident[2], me)).encode()).hexdigest()[:16]
        cands.append({"id": cid, "kind": kind, "owner": owner, "pids": pids,
                      "starts": {str(p): idents[p][2] for p in pids},
                      "cpu": round(sum(by[p][3] for p in pids if p in by), 1),
                      "mem_mb": round(sum(by[p][4] for p in pids if p in by) / 1024.0, 1),
                      "age_s": int(max(0, now - ident[3])), "why": kind})
    return cands, held


def public(c):
    """給頁面 / 快照的那份:不帶 starts(內部比對用)。"""
    return {k: v for k, v in c.items() if k != "starts"}


# ---------- 釋放 ----------

def _same(pid, start, me):
    ident = identity(pid)
    return bool(ident) and ident[0] == me and ident[2] == start


def _kill_one(c, me, wait_s=TERM_WAIT_S):
    """回 (killed, failed)。每個 pid 送訊號前都再比一次身分;對不上的不碰、算 failed。killed 只算我們送過訊號而且結束了的。"""
    signaled, failed = [], 0
    for p in c["pids"]:
        if not _same(p, c["starts"][str(p)], me):
            failed += 1
            continue
        try:
            os.kill(p, signal.SIGTERM)
            signaled.append(p)
        except ProcessLookupError:
            pass
        except PermissionError:
            failed += 1
    deadline = time.monotonic() + wait_s
    alive = list(signaled)
    while alive and time.monotonic() < deadline:
        alive = [p for p in alive if _same(p, c["starts"][str(p)], me)]
        if alive:
            time.sleep(0.1)
    for p in alive:
        if _same(p, c["starts"][str(p)], me):
            try:
                os.kill(p, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    for _ in range(20):
        still = [p for p in signaled if _same(p, c["starts"][str(p)], me)]
        if not still:
            break
        time.sleep(0.05)
    return len(signaled) - len(still), failed + len(still)


def released_path(environ=None):
    import room_heat
    return os.path.join(room_heat.state_dir(environ), "heat-released.jsonl")


def _log(rec, environ=None):
    path = released_path(environ)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _auto_used(now, environ=None):
    try:
        with open(released_path(environ), encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
    except (OSError, ValueError):
        return 0
    return sum(int(r.get("n_pids") or 0) for r in rows
               if r.get("mode") == "auto" and now - float(r.get("t") or 0) <= AUTO_WINDOW_S)


def release(ids=(), all_safe=False, dry_run=False, auto=False, scan_fn=None, wait_s=TERM_WAIT_S, now=None):
    """→ {ok, results:[{id, detail, kind?, freed_cpu?, freed_mem_mb?}], detail?}。"""
    now = time.time() if now is None else now
    bad = [i for i in ids if not ID_RE.match(i or "")]
    if bad:
        return {"ok": False, "detail": "bad_candidate", "results": [{"id": i[:32], "detail": "bad_candidate"} for i in bad]}
    cands, _held = (scan_fn or scan)()
    if cands is None:
        return {"ok": False, "detail": "release_failed", "results": []}
    by = {c["id"]: c for c in cands}
    picked = list(cands) if all_safe else [by.get(i) or {"id": i, "missing": True} for i in ids]
    me = os.getuid()
    results = []
    budget = AUTO_MAX_PER_WINDOW - _auto_used(now) if auto else None
    for c in picked:
        if c.get("missing"):
            results.append({"id": c["id"], "detail": "candidate_changed"})
            continue
        if c["kind"] not in ("chrome_orphan", "test_orphan"):
            results.append({"id": c["id"], "detail": "not_releasable"})
            continue
        if budget is not None and budget < len(c["pids"]):
            results.append({"id": c["id"], "detail": "auto_limit"})
            break
        if dry_run:
            results.append({"id": c["id"], "detail": "dry_run", "kind": c["kind"], "pids": len(c["pids"])})
            continue
        killed, failed = _kill_one(c, me, wait_s=wait_s)
        detail = "ok" if failed == 0 else ("partial" if killed else "release_failed")
        if budget is not None:
            budget -= killed
        r = {"id": c["id"], "detail": detail, "kind": c["kind"], "freed_cpu": c["cpu"], "freed_mem_mb": c["mem_mb"]}
        results.append(r)
        try:
            _log({"t": round(time.time(), 1), "kind": c["kind"], "owner": c["owner"], "cpu": c["cpu"],
                  "mem_mb": c["mem_mb"], "n_pids": killed, "mode": "auto" if auto else "manual", "result": detail})
        except OSError:
            pass
    ok = all(r["detail"] in ("ok", "dry_run") for r in results)
    return {"ok": ok, "results": results}
