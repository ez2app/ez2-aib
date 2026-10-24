#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat:機房「主機散熱監控區」的核心資料(頁面與動畫在艦長室外掛)。

  aib room heat [--json]              一次快照(頁面每 5 秒讀):等級 / 溫度 / 負載 / 記憶體 + 行程按「誰的」分組
  aib room heat history [--json]      最近 1 小時,每 10 秒一點(快照順手記;狀態根 room/heat-history.json,0600)
  aib room heat crashes [--json] [--days N]   當機雷達:系統當機報告按「程式 + 例外 + 訊息前 40 字」歸類

  aib room heat release <候選編號>… | --all-safe [--auto] [--dry-run] [--json]   釋放殘留(room_heat_release.py,白名單)

快照的 candidates / held 由 room_heat_release.scan 算;這支本身只讀。
沿用 machine_status(量測、「量不到 ≠ 0」)與 cooldown(四級),不重寫門檻。
隱私:輸出不放指令列、不放 cwd 或檔案全路徑;程式名只留檔名。當機報告只讀開頭的機器欄位,
不讀堆疊、不輸出訊息原文(只拿來歸類),不上傳。detail 代碼是契約,人話放 hint。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

HISTORY_STEP_S = 10
HISTORY_MAX = 360                    # 1 小時
CRASH_DIR = os.path.expanduser("~/Library/Logs/DiagnosticReports")
CRASH_READ_MAX = 32 * 1024           # 每份報告最多讀開頭 32 KB(機器欄位都在前面,堆疊在後面)
CRASH_DAYS = 7
CRASH_FILES_MAX = 300
GROUPS = ("seat", "aib_test", "reviewer", "headless_browser", "sandbox", "aib_core", "system", "other_app")
SYSTEM_NAMES = frozenset(("kernel_task", "WindowServer", "launchd", "mds", "mds_stores", "mdworker", "mdworker_shared",
                          "spotlightknowledged", "corespotlightd", "backupd", "logd", "syslogd", "coreaudiod",
                          "bluetoothd", "airportd", "softwareupdated", "fseventsd", "distnoted", "cfprefsd",
                          "spindump", "ReportCrash", "syspolicyd", "trustd", "configd", "powerd", "opendirectoryd"))
CLI_RE = re.compile(r"(^|/)(claude|codex|agy|antigravity|copilot|grok)(\s|$)")
TEST_RE = re.compile(r"(\baib(\.py)?\s+test\b|tools/verify/(runner|test_entry)\.py|/aib-test-|EZ2_AIB_STATE_ROOT=)")
REVIEWER_RE = re.compile(r"/scratchpad/")
SANDBOX_RE = re.compile(r"(aib-e2e-|e2e[-_]sandbox|sandbox-world)")
CORE_RE = re.compile(r"(src/cockpit/|/bin/aib(\s|$)|aib/cli\.py)")
NAME_BAD_RE = re.compile(r"[^\w.\-+ ()]")            # 程式名 / 當機欄位只留這些字;其餘(含控制字元、路徑斜線)換成 ?
CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# 已知當機原因:簽名 ⇒ 代碼。不在表上的一律 unknown,只顯示,不猜原因。
KNOWN_CRASHES = (
    # (代碼, 程式名規則, 例外種類, 訊息規則, 修在哪一版)
    ("aib_test_guarded_fd_dup", re.compile(r"^[Pp]ython"), "EXC_GUARD", re.compile(r"DUP on file descriptor"), "0.1.52"),
)


def T(key, **params):
    try:
        import i18n_lib
        v = i18n_lib.t("aib.heat." + key, params or None, locale=i18n_lib.resolve_locale())
        if v and not v.startswith("!!"):
            return v
    except Exception:  # noqa: BLE001
        pass
    return key


def _run(cmd, timeout=10):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL).stdout


def state_dir(environ=None):
    import seats_dir_lib
    root, _src = seats_dir_lib.state_root(environ)
    return os.path.join(root, "room")


def safe_name(s, n=40):
    """外流的程式名 / 欄位:去控制字元與路徑字元、限長(惡意當機報告或程式名不能把跳脫序列 / 路徑帶到終端或頁面)。"""
    return NAME_BAD_RE.sub("?", CTRL_RE.sub("", str(s or "")))[:n]


def safe_read(path, limit):
    """只讀一般檔(不跟 symlink、FIFO / 裝置不讀 ⇒ 不會卡住);讀不了 ⇒ None。"""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, limit).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)


# ---------- 量測 ----------

def measure(sampler=None):
    """machine_status 一輪 + cooldown 四級。回 dict(每格帶 state;量不到照實 unmeasured,值是 None)。"""
    import machine_status as ms
    import cooldown
    try:
        cells, took = (sampler or ms.sample)()
        level, _reasons = cooldown.level_of(cells)
    except Exception:  # noqa: BLE001 —— 量測壞了照樣出分組,不讓頁面空白
        return {"level": "unmeasured", "detail": "measure_failed", "took_s": 0.0, "cells": {}}
    out = {"level": level, "took_s": round(took, 3), "cells": {}}
    for c in cells:
        if not isinstance(c, dict):
            continue
        key = c.get("key") or c.get("label")
        out["cells"][key] = {"value": c.get("value"), "state": c.get("state"), "text": c.get("text")}
    return out


# ---------- 行程分組 ----------

def list_procs(run=None):
    """[(pid, ppid, uid, cpu, rss_kb, 程式名, 指令列)];ps 跑不了 ⇒ None。指令列只拿來分組,不往外送。
    程式名另跑一次 `ps -o ucomm=`(只有執行檔名、不帶路徑);macOS 的 comm 不在最後一欄會被截成路徑前 16 字,
    取 basename 會變成 Go / Ce / 空字串,還可能帶出資料夾名(#317 審查 A1 / B1)。"""
    try:
        out = (run or _run)(["ps", "-axo", "pid=,ppid=,uid=,pcpu=,rss=,args="])
        names_out = (run or _run)(["ps", "-axo", "pid=,ucomm="])
    except Exception:  # noqa: BLE001
        return None
    names = {}
    for line in (names_out or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+(.*?)\s*$", line)
        if m:
            names[int(m.group(1))] = safe_name(m.group(2), 32)
    rows = []
    for line in (out or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s?(.*)$", line)
        if m:
            pid = int(m.group(1))
            rows.append((pid, int(m.group(2)), int(m.group(3)), float(m.group(4)), int(m.group(5)),
                         names.get(pid, ""), m.group(6)))
    return rows


def _is_browser_automation(name, args):
    import chrome_orphans
    low = args.lower()
    return chrome_orphans.is_automation_main(args) or (
        "--type=" in args and any(h in low.split(" --", 1)[0] for h in ("chrome", "chromium", "headless_shell"))
        and any(m in args for m in ("--headless", "--enable-automation", "--remote-debugging")))


def classify(rows, uid=None, owner_of=None):
    """每個行程歸一組。順序有意義:瀏覽器 / 審查員 / 測試先於席位(席位起的無頭 Chrome 算瀏覽器,主人記那一席)。
    回 {pid: (組, 主人)};主人只拿來顯示(席碼可偽造,不拿來判能不能收)。"""
    uid = os.getuid() if uid is None else uid
    by_pid = {r[0]: r for r in rows}
    seat_root = {}                                     # pid ⇒ 它往上第一個 AI CLI 行程

    def cli_ancestor(pid):
        seen = set()
        p = pid
        while p in by_pid and p not in seen and p > 1:
            seen.add(p)
            if CLI_RE.search(by_pid[p][6].split(" --", 1)[0]):
                return p
            p = by_pid[p][1]
        return None

    def chain_args(pid, depth=6):
        out, p = [], pid
        for _ in range(depth):
            if p not in by_pid or p <= 1:
                break
            out.append(by_pid[p][6])
            p = by_pid[p][1]
        return out

    owners = {}

    def seat_of(cli_pid):
        if cli_pid is None:
            return ""
        if cli_pid not in owners:
            try:
                seat, _pane = (owner_of or _owner_of)(cli_pid)
            except Exception:  # noqa: BLE001
                seat = None
            owners[cli_pid] = seat or "?"
        return owners[cli_pid]

    out = {}
    for pid, _ppid, puid, _cpu, _rss, name, args in rows:
        if puid != uid:                                # 別的帳號(含 root):只留系統白名單裡的名字
            out[pid] = ("system", name if name in SYSTEM_NAMES else "")
            continue
        if name in SYSTEM_NAMES:
            out[pid] = ("system", name)
            continue
        chain = chain_args(pid)
        cli = seat_root.setdefault(pid, cli_ancestor(pid))
        if _is_browser_automation(name, args):
            out[pid] = ("headless_browser", seat_of(cli))
        elif any(REVIEWER_RE.search(a) for a in chain):
            out[pid] = ("reviewer", seat_of(cli))
        elif any(TEST_RE.search(a) for a in chain):
            out[pid] = ("aib_test", seat_of(cli))
        elif any(SANDBOX_RE.search(a) for a in chain):
            out[pid] = ("sandbox", seat_of(cli))
        elif cli is not None:
            out[pid] = ("seat", seat_of(cli))
        elif CORE_RE.search(args):
            out[pid] = ("aib_core", "")
        else:
            out[pid] = ("other_app", name)
    return out


def _owner_of(pid):
    import chrome_orphans
    return chrome_orphans.owner_of(pid)


def groups(rows, cls, top_apps=5):
    """合計成 [{group, owner, cpu, mem_mb, count}];其他 App / 系統只留前幾名(按 CPU),其餘併成一筆。"""
    acc = {}
    for pid, _ppid, _uid, cpu, rss, _name, _args in rows:
        g, owner = cls.get(pid, ("other_app", ""))
        k = (g, owner)
        a = acc.setdefault(k, {"group": g, "owner": owner, "cpu": 0.0, "mem_mb": 0.0, "count": 0})
        a["cpu"] += cpu
        a["mem_mb"] += rss / 1024.0
        a["count"] += 1
    rest = {}
    out = []
    for g in GROUPS:
        items = sorted((v for (gg, _o), v in acc.items() if gg == g), key=lambda v: -v["cpu"])
        if g in ("other_app", "system"):
            for v in items[top_apps:]:
                r = rest.setdefault(g, {"group": g, "owner": "*", "cpu": 0.0, "mem_mb": 0.0, "count": 0})
                for f in ("cpu", "mem_mb", "count"):
                    r[f] += v[f]
            items = items[:top_apps] + ([rest[g]] if g in rest else [])
        out += items
    for v in out:
        v["cpu"] = round(v["cpu"], 1)
        v["mem_mb"] = round(v["mem_mb"], 1)
    return out


# ---------- 歷史 ----------

def history_path(environ=None):
    return os.path.join(state_dir(environ), "heat-history.json")


def _num(cells, key):
    c = cells.get(key) or {}
    v = c.get("value")
    return v if isinstance(v, (int, float)) and c.get("state") != "unmeasured" else None


HISTORY_KEYS = ("temp", "load1", "mem_free", "swap_mb")
LEVELS = ("ok", "warn", "hot", "critical", "unmeasured")


def read_history(environ=None, now=None):
    """只收白名單欄位、數字或 None、level 在四級內、t 不在未來(檔被換成別的東西 ⇒ 不外流、也不會卡住歷史)。"""
    now = time.time() if now is None else now
    raw = safe_read(history_path(environ), 4 * 1024 * 1024)
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        return []
    pts = d.get("points") if isinstance(d, dict) else None
    out = []
    for p in pts if isinstance(pts, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("t"), (int, float)) or isinstance(p.get("t"), bool):
            continue
        if p["t"] > now + 60:
            continue
        q = {"t": float(p["t"]), "level": p.get("level") if p.get("level") in LEVELS else None}
        for k in HISTORY_KEYS:
            v = p.get(k)
            q[k] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        out.append(q)
    return out[-HISTORY_MAX:]


def record_history(m, now=None, environ=None):
    """快照順手記一點;離上一點不到 10 秒就不記。環形:只留最近 360 點。原子寫入、0600。回是否有寫。"""
    now = time.time() if now is None else now
    pts = [p for p in read_history(environ, now=now) if p["t"] <= now]     # 時鐘倒退:比現在新的點丟掉,照常記
    if pts and 0 <= now - pts[-1]["t"] < HISTORY_STEP_S:
        return False
    cells = m.get("cells", {})
    pts.append({"t": round(now, 1), "level": m.get("level"), "temp": _num(cells, "cpu_temp"),
                "load1": _num(cells, "load1"), "mem_free": _num(cells, "mem"), "swap_mb": _num(cells, "swap")})
    pts = pts[-HISTORY_MAX:]
    path = history_path(environ)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        os.chmod(os.path.dirname(path), 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".heat-history.", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": "aib-heat-history/v1", "step_s": HISTORY_STEP_S, "points": pts}, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


# ---------- 當機雷達 ----------

def _field(body, key):
    m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(key), body)
    return m.group(1) if m else ""


def _obj(body, key):
    m = re.search(r'"%s"\s*:\s*\{([^{}]*)\}' % re.escape(key), body)
    return m.group(1) if m else ""


def read_crash(path):
    """一份 .ips → {proc, parent, type, signal, ts, msg40}(msg40 只拿來歸類,不往外送);讀不了 ⇒ None。"""
    head = safe_read(path, CRASH_READ_MAX)
    if head is None:
        return None
    first, _, body = head.partition("\n")
    body = body.split('"threads"', 1)[0]                # 堆疊從 threads 開始,之後不看
    try:
        meta = json.loads(first)
    except ValueError:
        return None
    if not isinstance(meta, dict) or str(meta.get("bug_type") or "309") != "309":
        return None                                    # 只收當機(309);Jetsam 之類不算
    exc = _obj(body, "exception")
    proc = _field(body, "procName") or str(meta.get("app_name") or meta.get("name") or "")
    if not proc:
        return None

    def token(s):
        m = re.match(r"[A-Z][A-Z0-9_]{0,31}", s or "")
        return m.group(0) if m else "?"
    ts = str(meta.get("timestamp") or "")
    return {"proc": safe_name(os.path.basename(proc)), "parent": safe_name(os.path.basename(_field(body, "parentProc"))),
            "type": token(_field(exc, "type")), "signal": token(_field(exc, "signal")),
            "ts": ts[:32] if re.match(r"^[0-9 :.+\-]{10,32}$", ts[:32]) else "",
            "msg40": _field(exc, "message").strip()[:40]}


def known_code(c):
    for code, proc_re, typ, msg_re, fixed in KNOWN_CRASHES:
        if proc_re.search(c["proc"]) and c["type"] == typ and msg_re.search(c["msg40"]):
            return code, fixed
    return "unknown", ""


def crashes(days=CRASH_DAYS, now=None, crash_dir=None):
    """→ {state, classes:[{code, fixed_in, proc, parent, type, signal, count, last}], files};
    資料夾讀不到 ⇒ state unmeasured(跟「沒有當機」分開)。"""
    now = time.time() if now is None else now
    d = crash_dir or CRASH_DIR
    if not os.path.isdir(d):
        return {"state": "unmeasured", "detail": "crash_dir_missing", "classes": [], "files": 0}
    try:
        names = glob.glob(os.path.join(d, "*.ips"))
    except OSError:
        return {"state": "unmeasured", "detail": "crash_dir_unreadable", "classes": [], "files": 0}
    stamped = []
    for p in names:                                    # 逐檔 stat:中途被系統輪替刪掉的那份略過,不整區量不到
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and now - st.st_mtime <= days * 86400:
            stamped.append((st.st_mtime, p))
    files = [p for _m, p in sorted(stamped, reverse=True)[:CRASH_FILES_MAX]]
    acc = {}
    for p in files:
        c = read_crash(p)
        if not c:
            continue
        sig = hashlib.sha256(("%s|%s|%s" % (c["proc"], c["type"], c["msg40"])).encode("utf-8")).hexdigest()[:12]
        code, fixed = known_code(c)
        a = acc.setdefault(sig, {"id": sig, "code": code, "fixed_in": fixed, "proc": c["proc"], "parent": c["parent"],
                                 "type": c["type"], "signal": c["signal"], "count": 0, "last": ""})
        a["count"] += 1
        if c["ts"] > a["last"]:
            a["last"] = c["ts"]
    classes = sorted(acc.values(), key=lambda a: (a["last"], a["count"]), reverse=True)
    for a in classes:
        a["hint"] = T("crash." + a["code"], fixed=a["fixed_in"])
    return {"state": "ok", "classes": classes, "files": len(files), "days": days}


# ---------- 快照 ----------

def snapshot(sampler=None, run=None, owner_of=None, now=None, environ=None, record=True, scan_fn=None):
    m = measure(sampler)
    rows = list_procs(run)
    cands, held = [], []
    if rows is None:
        grp = {"state": "unmeasured", "detail": "ps_failed", "items": []}
    else:
        grp = {"state": "ok", "items": groups(rows, classify(rows, owner_of=owner_of))}
        try:
            import room_heat_release as RR
            c, h = (scan_fn or RR.scan)(rows=rows, owner_of=owner_of)
            cands, held = [RR.public(x) for x in (c or [])], list(h or [])
        except Exception:  # noqa: BLE001 —— 候選算不出來不擋快照
            cands, held = [], []
    snap = {"schema": "aib-heat/v1", "t": round(time.time() if now is None else now, 1), "level": m["level"],
            "took_s": m["took_s"], "cells": m["cells"], "groups": grp, "candidates": cands, "held": held}
    if record:
        try:
            snap["history_recorded"] = record_history(m, now=now, environ=environ)
        except OSError:
            snap["history_recorded"] = False
    return snap


def _print_snapshot(s, out):
    try:
        import cooldown
        lv = "%s %s" % (cooldown.MARK.get(s["level"], ""), cooldown.LABEL[s["level"]])
    except Exception:  # noqa: BLE001
        lv = s["level"]
    out.write(T("snap.level", level=lv) + "\n")
    for k, c in s["cells"].items():
        out.write("  %-14s %s\n" % (k, c.get("text")))
    for g in s["groups"].get("items", [])[:12]:
        out.write("  %-17s %-14s CPU %5.1f%%  %7.1f MB  ×%d\n" % (T("group." + g["group"]), g["owner"] or "-",
                                                                  g["cpu"], g["mem_mb"], g["count"]))


def main(argv=None, out=None):
    out = out or sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    sub = argv.pop(0) if argv and argv[0] in ("history", "crashes", "release") else "snap"
    ap = argparse.ArgumentParser(prog="aib room heat" + ("" if sub == "snap" else " " + sub))
    ap.add_argument("--json", action="store_true")
    if sub == "crashes":
        ap.add_argument("--days", type=int, default=CRASH_DAYS)
    if sub == "release":
        ap.add_argument("ids", nargs="*")
        ap.add_argument("--all-safe", action="store_true")
        ap.add_argument("--auto", action="store_true")
        ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if sub == "release":
        import room_heat_release as RR
        if bool(a.ids) == bool(a.all_safe):
            out.write(T("release.usage") + "\n")
            return 2
        r = RR.release(ids=a.ids, all_safe=a.all_safe, dry_run=a.dry_run, auto=a.auto)
        if a.json:
            json.dump(r, out, ensure_ascii=False)
            out.write("\n")
        else:
            for x in r["results"]:
                out.write("  %s %s\n" % (x["id"], T("release." + x["detail"])))
            if not r["results"]:
                out.write(T("release.none") + "\n")
        return 0 if r["ok"] else 1
    if sub == "history":
        pts = read_history()
        if a.json:
            json.dump({"schema": "aib-heat-history/v1", "step_s": HISTORY_STEP_S, "points": pts}, out, ensure_ascii=False)
            out.write("\n")
        else:
            out.write(T("history.count", n=len(pts)) + "\n")
        return 0
    if sub == "crashes":
        r = crashes(days=max(1, min(a.days, 90)))
        if a.json:
            json.dump(r, out, ensure_ascii=False)
            out.write("\n")
        elif r["state"] != "ok":
            out.write(T("crash.unmeasured") + "\n")
        else:
            out.write(T("crash.summary", n=len(r["classes"]), files=r["files"], days=r["days"]) + "\n")
            for c in r["classes"]:
                out.write("  ×%-3d %-14s %-10s %-8s %s\n" % (c["count"], c["proc"], c["type"], c["signal"], c["hint"]))
        return 0
    s = snapshot()
    if a.json:
        json.dump(s, out, ensure_ascii=False)
        out.write("\n")
    else:
        _print_snapshot(s, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
