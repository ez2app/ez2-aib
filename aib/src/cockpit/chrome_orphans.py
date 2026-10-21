#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib chrome orphans [--all] [--json]:列出沒人收的自動化 Chrome,和它是哪一席起的(PROPOSAL-gaps v3 第 7 件 D,@9f808 提)。

起因:截圖 / 測試起的無頭 Chrome 沒收乾淨 ⇒ 機器變熱、browser-arm-lock 一直被佔,推 PR 連四次失敗,卻查不到是誰的。
只列、不殺(收掉是起它的那一席自己的事;熱到 critical 時 cooldown reap 另有規則)。

判法:
  - 瀏覽器主行程(沒有 --type=),而且帶自動化旗標(跟 cooldown.AUTOMATION_MARKS 同一份;使用者自己開的瀏覽器一個都不帶)。
  - 孤兒 = 父行程是 1(起它的那支已經結束)。--all 連還有父行程的一起列。
  - 主人:讀行程環境(macOS `ps -E`,只看得到同帳號的行程)裡的 AIB_RESUME_SESSION_UUID / AIB_REVIVE_SESSION_UUID,
    末五碼 = 席碼;沒有 ⇒ 看 TMUX_PANE;都沒有 ⇒ "?"。環境裡其他變數不印、不存(可能有 token)。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from cooldown import AUTOMATION_MARKS, BROWSER_HINTS  # noqa: E402

SEAT_VARS = ("AIB_RESUME_SESSION_UUID", "AIB_REVIVE_SESSION_UUID")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PANE_RE = re.compile(r"^%\d{1,5}$")


def T(key, **params):
    try:
        import i18n_lib
        v = i18n_lib.t("aib.chrome." + key, params or None, locale=i18n_lib.resolve_locale())
        if v and not v.startswith("!!"):
            return v
    except Exception:  # noqa: BLE001
        pass
    return key


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL).stdout


def list_procs(run=None):
    """[(pid, ppid, etime, command)];ps 跑不了 ⇒ None(跟「沒有」分開)。"""
    try:
        out = (run or _run)(["ps", "-axo", "pid=,ppid=,etime=,command="])
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for line in (out or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
        if m:
            rows.append((int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)))
    return rows


def is_automation_main(cmd):
    low = cmd.lower()
    if "--type=" in cmd or not any(h in low for h in BROWSER_HINTS):
        return False
    first = cmd.split(" --", 1)[0].lower()
    if "helper" in first or "crashpad" in first:
        return False
    return any(m in cmd for m in AUTOMATION_MARKS)


def user_data_dir(cmd):
    m = re.search(r"--user-data-dir=(\S+)", cmd)
    return m.group(1) if m else None


def owner_of(pid, run=None):
    """從行程環境找席碼;只取那幾個變數,其他不碰。回 (seat 或 None, pane 或 None)。"""
    try:
        out = (run or _run)(["ps", "-E", "-p", str(pid), "-o", "command="])
    except Exception:  # noqa: BLE001
        return None, None
    seat = pane = None
    for tok in (out or "").split():
        k, _, v = tok.partition("=")
        if k in SEAT_VARS and seat is None and UUID_RE.match(v):
            seat = v[-5:]
        elif k == "TMUX_PANE" and PANE_RE.match(v):
            pane = v
    return seat, pane


def scan(all_=False, run=None):
    rows = list_procs(run)
    if rows is None:
        return None
    out = []
    for pid, ppid, etime, cmd in rows:
        if not is_automation_main(cmd):
            continue
        orphan = ppid == 1
        if not (orphan or all_):
            continue
        seat, pane = owner_of(pid, run)
        out.append({"pid": pid, "ppid": ppid, "orphan": orphan, "age": etime, "user_data_dir": user_data_dir(cmd),
                    "seat": seat, "pane": pane, "headless": "--headless" in cmd})
    return out


def main(argv=None, out=None, run=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib chrome", description=T("help"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("orphans", help=T("help_orphans"))
    o.add_argument("--all", action="store_true", help=T("help_all"))
    o.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = scan(all_=a.all, run=run)
    if rows is None:
        if a.json:
            out.write(json.dumps({"v": 1, "ok": False, "why": "ps-failed"}) + "\n")
        else:
            out.write(T("ps_failed") + "\n")
        return 1
    if a.json:
        out.write(json.dumps({"v": 1, "ok": True, "rows": rows}, ensure_ascii=False) + "\n")
        return 0
    if not rows:
        out.write(T("none") + "\n")
        return 0
    for r in rows:
        who = ("@" + r["seat"]) if r["seat"] else (r["pane"] or "?")
        out.write(T("row", pid=r["pid"], age=r["age"], who=who, dir=r["user_data_dir"] or "-",
                    kind=T("orphan") if r["orphan"] else T("parent", ppid=r["ppid"])) + "\n")
    out.write(T("hint") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
