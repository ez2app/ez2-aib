#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""bot 席現況:一支指令看清每個 bot 席現在的狀態。

獨立指令。接進 `aib bot status` 的那一行不在本檔寫集。
狀態根走 seats_dir_lib.state_root();治具靠 EZ2_AIB_STATE_ROOT。

未量的欄印 UNMEASURED,不准印成 0 或空白。
pid 死了但席位檔沒有 exit_reason ⇒ 標 DEAD_REGISTERED,不默默跳過。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402
from seat_address import uuid_tail5  # noqa: E402

UNMEASURED = "量不到"
DEAD_REGISTERED = "登記說活、行程已死"
BOT_PROVIDERS = ("codexbot", "grokbot", "claudebot", "musebot")
BRIDGE_MODES = ("queue", "writer")

COLUMNS = (
    ("code", "席碼"),
    ("provider", "provider"),
    ("window", "窗號"),
    ("mode", "mode"),
    ("state", "state"),
    ("holder", "持有"),
    ("last_activity", "最後活動"),
    ("last_dm_receipt", "最後dm收據"),
    ("waiting_approval", "等核准"),
)


def pid_alive(pid) -> bool:
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        os.kill(n, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def iter_bot_seats(root, provider="all"):
    """席位登記:<狀態根>/instances/*/seats/w*.json。只收 codexbot/grokbot/claudebot/musebot。"""
    pattern = os.path.join(root, "instances", "*", "seats", "w*.json")
    want = BOT_PROVIDERS if provider == "all" else (provider,)
    out = []
    for path in sorted(glob.glob(pattern)):
        rec = load_json(path)
        if rec is None:
            continue
        prov = rec.get("provider")
        if prov not in want:
            continue
        out.append(rec)
    return out


def read_status(root, session_uuid):
    if not session_uuid:
        return None
    return load_json(os.path.join(root, "bridge", session_uuid, "status.json"))


def code_of_seat(seat, status):
    uuid = seat.get("session_uuid")
    code = uuid_tail5(uuid) if isinstance(uuid, str) else None
    if code:
        return code
    if isinstance(status, dict):
        # B15:不退回席位檔的舊五碼欄位(第二真相源);只認橋自己寫的 code。
        val = status.get("code")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return UNMEASURED


def window_of(seat):
    wid = seat.get("window_id")
    if isinstance(wid, str) and wid.strip():
        return wid.strip()
    return UNMEASURED


def mode_of(status):
    if not isinstance(status, dict):
        return UNMEASURED
    mode = status.get("mode")
    if mode in BRIDGE_MODES:
        return mode
    return UNMEASURED


def holder_of(status, root):
    if not isinstance(status, dict):
        return UNMEASURED
    if status.get("holding") is True:
        return "AIB"
    mode = status.get("mode")
    if mode == "writer":
        return "AIB"
    if mode == "queue":
        return "桌面"
    thread = status.get("desktop_thread_id")
    if isinstance(thread, str) and thread:
        lock = os.path.join(root, "codexbot-bindings", thread + ".lock")
        if os.path.isfile(lock):
            return "AIB"
    return UNMEASURED


def state_of(seat, status):
    pid = seat.get("pid")
    has_pid = pid not in (None, "")
    dead = has_pid and not pid_alive(pid)
    exit_reason = seat.get("exit_reason")
    if dead and not exit_reason:
        return DEAD_REGISTERED
    if isinstance(status, dict):
        st = status.get("state")
        if isinstance(st, str) and st.strip():
            # 10-11 a seat(DIAG §9 #14):收尾小幫手中途被殺 ⇒ 停在 closing、pid 是死掉的小幫手 ⇒ 實際已停
            if st.strip() == "closing" and status.get("pid") not in (None, "") and not pid_alive(status.get("pid")):
                return "stopped"
            return st.strip()
    if isinstance(exit_reason, str) and exit_reason.strip():
        return exit_reason.strip()
    return UNMEASURED


def format_activity(epoch, now):
    if epoch in (None, ""):
        return UNMEASURED
    try:
        n = int(now - float(epoch))
    except (TypeError, ValueError):
        return UNMEASURED
    if n < 0:
        n = 0
    if n < 60:
        return "%d秒前" % n
    if n < 3600:
        return "%d分鐘前" % (n // 60)
    if n < 86400:
        return "%d小時前" % (n // 3600)
    return "%d天前" % (n // 86400)


def last_activity_of(seat, status, now):
    epoch = None
    if isinstance(status, dict):
        for key in ("updated_epoch", "last_reply_epoch"):
            val = status.get(key)
            if isinstance(val, (int, float)):
                epoch = val if epoch is None else max(epoch, val)
    if epoch is None:
        val = seat.get("last_used_epoch")
        if isinstance(val, (int, float)):
            epoch = val
    return format_activity(epoch, now)


def last_dm_receipt_of(root, session_uuid):
    if not session_uuid:
        return UNMEASURED
    folder = os.path.join(root, "bridge", session_uuid, "outbox")
    if not os.path.isdir(folder):
        return UNMEASURED
    try:
        names = [n for n in os.listdir(folder)
                 if n.endswith(".json") and not n.startswith(".")]
    except OSError:
        return UNMEASURED
    if not names:
        return UNMEASURED
    names.sort(key=lambda n: os.path.getmtime(os.path.join(folder, n)))
    return names[-1]


def waiting_approval_of(status, root, session_uuid):
    if isinstance(status, dict):
        flag = status.get("waiting_on_approval")
        if flag is True:
            return "是"
        if flag is False:
            return "否"
        flags = status.get("activeFlags")
        if isinstance(flags, list) and "waitingOnApproval" in flags:
            return "是"
    if not session_uuid:
        return UNMEASURED
    path = os.path.join(root, "bridge", session_uuid, "approvals.jsonl")
    if not os.path.isfile(path):
        return UNMEASURED
    last = None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
    except OSError:
        return UNMEASURED
    if last is None:
        return "否"
    try:
        rec = json.loads(last)
    except ValueError:
        return UNMEASURED
    if not isinstance(rec, dict):
        return UNMEASURED
    if rec.get("waiting") is True or rec.get("waiting_on_approval") is True:
        return "是"
    req = rec.get("request")
    if isinstance(req, dict) and "id" in req:
        return "是"
    return "否"


def row_for(seat, root, now):
    uuid = seat.get("session_uuid") if isinstance(seat.get("session_uuid"), str) else ""
    status = read_status(root, uuid)
    return {
        "code": code_of_seat(seat, status),
        "provider": seat.get("provider") if seat.get("provider") in BOT_PROVIDERS else UNMEASURED,
        "window": window_of(seat),
        "mode": mode_of(status),
        "state": state_of(seat, status),
        "holder": holder_of(status, root),
        "last_activity": last_activity_of(seat, status, now),
        "last_dm_receipt": last_dm_receipt_of(root, uuid),
        "waiting_approval": waiting_approval_of(status, root, uuid),
    }


def collect_rows(root, provider="all", now=None):
    if now is None:
        now = time.time()
    rows = [row_for(seat, root, now) for seat in iter_bot_seats(root, provider)]
    rows.sort(key=lambda r: (r.get("window") or "", r.get("code") or ""))
    return rows


def format_table(rows):
    header = "\t".join(label for _key, label in COLUMNS)
    lines = [header]
    for row in rows:
        lines.append("\t".join(str(row.get(key) or UNMEASURED) for key, _label in COLUMNS))
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="bot_status.py")
    ap.add_argument("--provider", choices=("codexbot", "grokbot", "claudebot", "musebot", "all"), default="all")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    root = seats_dir_lib.state_root()[0]
    rows = collect_rows(root, args.provider)
    if args.as_json:
        sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(format_table(rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
