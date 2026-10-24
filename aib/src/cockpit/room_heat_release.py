#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 的「釋放」:只收沒人用的殘留(白名單),不碰活席、真人的 App、系統行程、別的帳號、不認得的。

  aib room heat release <候選編號>… [--dry-run] [--json]
  aib room heat release --all-safe [--auto] [--dry-run] [--json]

白名單(這一版兩種):
  chrome_orphan  帶真的自動化旗標(--headless / --enable-automation / --remote-debugging-pipe)的瀏覽器主行程 + 父行程 1
                 + --user-data-dir realpath 後在暫存根底下;--app= 視窗、艦長室的設定檔不算(真人開著的)
  test_orphan    父行程 1、跑的腳本本身 realpath 後在暫存根底下、而且是 tests/test-*.py|js;
                 它的子孫也要各自的腳本在暫存根底下才一起收
  兩種都要活過 MIN_AGE_S(剛起的可能還有人在等);沙盒世界(SANDBOX_RE)這一版不收。
  判斷一律把指令列拆成引數、realpath 後比前綴,不做整串子字串比對(#319 r3:/x/tmp/、/tmp/../、引數分開都繞得過)。
一律不收(只顯示在 held):行程或子孫有 ssh / scp / rsync、正在對主機動手的 aib 指令、開著 *.lock 檔 —— 殺在中間主機會半套。

安全:
  - 候選編號 = 種類 + pid + 開始時間(微秒)+ uid 的雜湊;釋放前重掃一次,編號對不上 ⇒ candidate_changed,不殺。
  - 每個 pid 送訊號前再比一次 uid + 開始時間(防 pid 重用);只殺列出的 pid,不用名字比對、不整個行程群組殺。
  - 不殺自己與自己的祖先行程。
  - 每次釋放寫一筆 room/heat-released.jsonl(時間、種類、主人、省下多少、手動 / 自動),不寫指令列。
  - --auto(付費自動釋放用):10 分鐘最多收 AUTO_MAX_PER_WINDOW 個,超過 ⇒ auto_limit 停手。
錯誤代碼:bad_candidate candidate_changed not_releasable release_failed partial auto_limit log_failed(detail 是契約)。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import hashlib
import json
import math
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
MIN_AGE_S = 120
ID_RE = re.compile(r"^[0-9a-f]{16}$")
HOST_RE = re.compile(r"(^|[\s/])(ssh|scp|rsync|sftp)(\s|$|\.)|\bhostguard\b|\bsecret\s+ask\b|--push\b")
INTERP_RE = re.compile(r"^(python(\d+(\.\d+)*)?|Python|node)$")     # argv[0] 只認直譯器(tmux / caffeinate / 常駐程式不算)
PY_VALUE_OPTS = ("-W", "-X")                                        # python 帶值的選項(值在下一個引數)
NODE_VALUE_OPTS = ("-r", "--require", "--import", "--loader", "--experimental-loader")
INLINE_CODE_OPTS = ("-m", "-c", "-e", "-p", "--eval", "--print")  # 不是跑檔案 ⇒ 判不出是什麼 ⇒ 不收
TEST_SCRIPT_RE = re.compile(r"/tests/test[-_][\w.-]*\.(py|js|mjs)$")
SCRIPT_RE = re.compile(r"\.(py|js|mjs|cjs|sh)$")
AUTOMATION_FLAGS = ("--headless", "--enable-automation", "--remote-debugging-pipe")


def _system_temp():
    """系統給這個帳號的暫存夾:macOS 直接問 libc confstr(_CS_DARWIN_USER_TEMP_DIR = 65537),不經 TMPDIR
    (CPython 的 os.confstr_names 沒有這個名字,#319 r5);其他平台只用固定的 /var/tmp。"""
    if sys.platform != "darwin":
        return ["/var/tmp"]
    try:
        buf = ctypes.create_string_buffer(1024)
        n = ctypes.CDLL(None).confstr(65537, buf, ctypes.sizeof(buf))
        return [buf.value.decode()] if 0 < n <= ctypes.sizeof(buf) and buf.value else []
    except (OSError, AttributeError, UnicodeDecodeError):
        return []


def temp_roots(extra=()):
    """暫存根(realpath、結尾帶 /):系統暫存夾、/tmp、/private/tmp。不讀任何環境變數 —— 連 TMPDIR 也不讀
    (tempfile.gettempdir 會讀,設錯一個就把白名單放大,#319 r3 / r4 B-M2);測試用 extra 或 scan(roots=) 注入;
    太淺的(/、家目錄、家目錄的上層、只有一層)一律丟掉。"""
    home = os.path.realpath(os.path.expanduser("~"))
    out = set()
    for r in [*_system_temp(), "/tmp", "/private/tmp", *extra]:
        if not r:
            continue
        rp = os.path.realpath(r)
        if rp.count("/") < 2 or rp == home or home.startswith(rp.rstrip("/") + "/"):
            continue
        out.add(rp.rstrip("/") + "/")
    return sorted(out)


def _under(path, roots):
    """path realpath 後在某個暫存根底下(前綴比對,不是子字串)。"""
    if not path or not path.startswith("/"):
        return False
    rp = os.path.realpath(path)
    return any(rp.startswith(r) for r in roots)


def _argv(args):
    """指令列拆成引數;--x=值 取等號後面。帶空白的路徑會被拆開 ⇒ 判不過 ⇒ 不收(往不收那邊錯)。"""
    out = []
    for tok in (args or "").split():
        if tok.startswith("-") and "=" in tok:
            tok = tok.split("=", 1)[1]
        out.append(tok)
    return out


def _script(args):
    """跑的腳本 = 直譯器(python / node)後面第一個不是選項的位置引數;argv[0] 不是直譯器、
    或用 -m / -c / -e 跑、或第一個位置引數不像腳本 ⇒ ""(不收)。node 的 --require 等的值不算腳本
    (#319 r4:tmux server 的 argv 帶著它 exec 的腳本路徑、父行程也是 1;idle_reaper 踩過同一個坑)。"""
    toks = (args or "").split()
    if not toks or not INTERP_RE.match(os.path.basename(toks[0])):
        return ""
    skip = PY_VALUE_OPTS + NODE_VALUE_OPTS
    i = 1
    while i < len(toks):
        t = toks[i]
        if t.split("=", 1)[0] in INLINE_CODE_OPTS:
            return ""
        if t in skip:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t if t.startswith("/") and SCRIPT_RE.search(t) else ""
    return ""


def _is_test_orphan(args, roots):
    sc = _script(args)
    return bool(sc) and _under(sc, roots) and bool(TEST_SCRIPT_RE.search(os.path.realpath(sc)))


def _is_chrome_orphan(args, roots):
    import chrome_orphans
    if not chrome_orphans.is_automation_main(args):
        return False
    toks = args.split()
    keys = [t.split("=", 1)[0].lstrip("-") for t in toks if t.startswith("-")]
    if "app" in keys or not any(t.split("=", 1)[0] in AUTOMATION_FLAGS for t in toks):
        return False                                   # --app / -app(Chromium 單破折號也收)= 真人的視窗
    udds = [t.split("=", 1)[1] for t in toks if t.split("=", 1)[0] in ("--user-data-dir", "-user-data-dir") and "=" in t]
    if len(udds) != 1 or "user-data-dir" in keys and keys.count("user-data-dir") != 1:
        return False                                   # Chrome 用最後一個;不只一個 / 空白分隔取不到值 ⇒ 不收
    udd = udds[0]
    return _under(udd, roots) and "/captain-room/" not in os.path.realpath(udd) + "/"


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


def _lsof(args, run=None):
    return (run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=5,
                                             stdin=subprocess.DEVNULL).stdout))(["lsof", "-n", "-P"] + args)


def _detached_output(pid, run=None):
    """stdout / stderr 導到一般檔案(nohup … > log &)⇒ 是有人刻意丟背景、還在等結果的,不是殘留(#319 r5 A1)。
    回 True / False;lsof 跑不了 ⇒ None(量不到)。"""
    try:
        out = _lsof(["-a", "-p", str(pid), "-d", "1,2", "-Ft"], run)
    except Exception:  # noqa: BLE001
        return None
    return "tREG" in (out or "").split()


def _holds_lock(pids, run=None):
    """開著 *.lock 檔?lsof 跑不了 ⇒ None(量不到,呼叫端一樣不收,但代碼分開)。"""
    try:
        out = _lsof(["-Fn", "-p", ",".join(str(p) for p in pids)], run)
    except Exception:  # noqa: BLE001
        return None
    return any(ln.startswith("n") and ln.rstrip().endswith(".lock") for ln in (out or "").splitlines())


def scan(rows=None, run=None, owner_of=None, lsof=None, now=None, roots=None, min_age_s=MIN_AGE_S, detached=None):
    """→ (candidates, held)。rows 照 room_heat.list_procs 的格式;owner 只拿來顯示;roots 只給測試注入。"""
    import room_heat
    import chrome_orphans
    rows = room_heat.list_procs(run) if rows is None else rows
    if rows is None:
        return None, None
    now = time.time() if now is None else now
    me = os.getuid()
    roots = temp_roots() if roots is None else roots
    by = {r[0]: r for r in rows}
    kids = _children(rows)
    protected = _protected()
    cands, held = [], []
    for pid, ppid, uid, _cpu, _rss, _name, args in rows:
        if uid != me or ppid != 1 or pid in protected:
            continue
        if room_heat.SANDBOX_RE.search(args):          # 沙盒世界要另外判有沒有人在用,下一張
            continue
        kind = "chrome_orphan" if _is_chrome_orphan(args, roots) else ("test_orphan" if _is_test_orphan(args, roots) else None)
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
            held.append({"kind": kind, "owner": owner, "pid": pid, "why": "host_operation"})
            continue
        lk = (lsof or _holds_lock)(everyone)
        if lk is not False:                            # True = 開著鎖;None = lsof 跑不了,量不到也不收
            held.append({"kind": kind, "owner": owner, "pid": pid, "why": "holds_lock" if lk else "lsof_unavailable"})
            continue
        if kind == "test_orphan":
            dt = (detached or _detached_output)(pid)
            if dt is not False:
                held.append({"kind": kind, "owner": owner, "pid": pid,
                             "why": "detached_output" if dt else "lsof_unavailable"})
                continue
        if kind == "chrome_orphan":
            pids = [pid]                       # 主行程收了,它的 helper 跟著結束
        else:                                   # 子孫要各自符合判法(同帳號、指令列在暫存根底下)才一起收
            pids = [pid] + [p for p in desc if p in by and by[p][2] == me and p not in protected
                            and _under(_script(by[p][6]), roots) and not room_heat.SANDBOX_RE.search(by[p][6])]
        ident = identity(pid)
        if not ident or ident[0] != me or ident[1] != 1 or now - ident[3] < min_age_s:
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
    """回 (killed, failed)。每個 pid 送訊號前都再比一次身分:已經不在 ⇒ 不算失敗也不算收;
    還在但 uid / 開始時間對不上(pid 重用)⇒ 不碰、算 failed。killed 只算我們送過訊號而且結束了的。"""
    signaled, failed = [], 0
    for p in c["pids"]:
        ident = identity(p)
        if ident is None:
            continue
        if ident[0] != me or ident[2] != c["starts"][str(p)]:
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
    still = []
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
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _auto_used(now, environ=None):
    """最近 10 分鐘自動收了幾個。壞行逐行跳過(不讓一行壞掉就把額度歸零);n_pids 夾在 0 以上;
    檔案存在卻讀不了 ⇒ 回額度上限(往不收那邊錯)。"""
    try:
        fh = open(released_path(environ), encoding="utf-8")
    except FileNotFoundError:
        return 0
    except OSError:
        return AUTO_MAX_PER_WINDOW
    used = 0
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
                t, n = float(r.get("t") or 0), float(r.get("n_pids") or 0)
                if r.get("mode") != "auto" or not math.isfinite(t):
                    continue
                if now - t <= AUTO_WINDOW_S:            # 未來的點也算(時鐘往回調不重置額度)
                    used += AUTO_MAX_PER_WINDOW if not math.isfinite(n) else max(0, int(n))
            except (ValueError, TypeError, AttributeError, RecursionError, OverflowError):
                continue
    return used


class _AutoLock:
    """--auto 的「讀額度 → 殺 → 寫紀錄」用 flock 包起來,兩支同時跑不會各算一次額度。"""
    def __init__(self, environ=None):
        self.path = released_path(environ) + ".guard"
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except OSError:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def release(ids=(), all_safe=False, dry_run=False, auto=False, scan_fn=None, wait_s=TERM_WAIT_S, now=None):
    """→ {ok, results:[{id, detail, kind?, freed_cpu?, freed_mem_mb?}], detail?}。"""
    if auto and not dry_run:
        lock = _AutoLock()
        try:
            lock.__enter__()                           # 只有取鎖這一步的 OSError 算 log_failed;殺完之後的不藏(B-L3)
        except OSError:
            return {"ok": False, "detail": "log_failed", "results": []}
        try:
            return _release(ids, all_safe, dry_run, auto, scan_fn, wait_s, now)
        finally:
            lock.__exit__()
    return _release(ids, all_safe, dry_run, auto, scan_fn, wait_s, now)


def _release(ids, all_safe, dry_run, auto, scan_fn, wait_s, now):
    now = time.time() if now is None else now
    ids = list(dict.fromkeys(ids))                     # 同一個編號只算一次(B-L1)
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
            results.append({"id": c["id"], "detail": "dry_run", "kind": c["kind"], "n_pids": len(c["pids"])})
            continue
        if auto:                                       # 殺之前先記一筆預留:記不進帳就不殺(不然額度永遠是滿的)
            try:
                _log({"t": round(time.time(), 1), "kind": c["kind"], "n_pids": len(c["pids"]), "mode": "auto",
                      "result": "pending"})
            except OSError:
                results.append({"id": c["id"], "detail": "log_failed"})
                break
        killed, failed = _kill_one(c, me, wait_s=wait_s)
        if killed == 0 and failed == 0:                # 重掃之後、送訊號之前自己結束了:沒收到東西,不報省下多少
            detail = "candidate_changed"
        else:
            detail = "ok" if failed == 0 else ("partial" if killed else "release_failed")
        if budget is not None:
            budget -= len(c["pids"])                   # 跟帳本的預留一致(B-L4)
        r = {"id": c["id"], "detail": detail, "kind": c["kind"]}
        if detail == "ok":
            r.update(freed_cpu=c["cpu"], freed_mem_mb=c["mem_mb"])
        results.append(r)
        try:
            _log({"t": round(time.time(), 1), "kind": c["kind"], "owner": c["owner"], "cpu": c["cpu"],
                  "mem_mb": c["mem_mb"], "n_pids": killed, "mode": "auto_done" if auto else "manual", "result": detail})
        except OSError:                                # 自動模式的額度已經用預留那筆算過;結果記不了 ⇒ 停手
            r["log"] = "log_failed"                    # 掛在同一筆上(頁面用 id 當鍵,不出兩筆)
            if auto:
                break
    out = {"ok": all(r["detail"] in ("ok", "dry_run") and "log" not in r for r in results), "results": results}
    first = next((r.get("log") if r["detail"] in ("ok", "dry_run") else r["detail"]
                  for r in results if r["detail"] not in ("ok", "dry_run") or "log" in r), None)
    if first:
        out["detail"] = first                          # 總層一律放第一個不是 ok 的代碼(B-L2)
    return out
