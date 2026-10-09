#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""私訊對話區（dialog-lines）：只記真人發出的 DM。

契約：
  - 寫入端：dm-ctl 在 HUMAN_ID 有值且投遞 delivered 時呼叫 append_human_dm。
  - AI→AI / 未證真人 的 DM 一律不寫。
  - 讀取端：pane-hub-frame 再過濾一次，舊檔若混入非真人列也會被丟掉。
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

DIALOG_FILE = "dialog-lines"
MAX_LINES = 500
# 2026/09/20 14:41:05 真人human → a seat  內容（舊格式仍可讀）
_LINE_RE = re.compile(
    r"^(?:"
    r"\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}"  # 一般人看得懂
    r"|\d{4}/\d{2}/\d{2}/\d{2}/\d{2}/\d{2}"      # 舊：全斜線
    r"|\d{1,2}:\d{2}"                                # 舊：僅時分
    r")\s+真人\S+\s+→\s+\S+"
)
_PREFIX_RE = re.compile(r"^真人[^:：]+[:：]\s*")


def dialog_path(cockpit_dir: str | Path) -> Path:
    return Path(cockpit_dir) / DIALOG_FILE


def is_human_dialog_line(line: str) -> bool:
    s = (line or "").rstrip("\n")
    if not s.strip():
        return False
    return bool(_LINE_RE.match(s))


def _strip_human_prefix(body: str) -> str:
    return _PREFIX_RE.sub("", (body or "").strip())


def format_human_dm(human_id: str, to_label: str, body: str, ts: float | None = None) -> str:
    hid = re.sub(r"[^A-Za-z0-9._-]", "", str(human_id or "").strip()) or "human"
    to = (to_label or "?").strip() or "?"
    if not to.startswith("@") and re.fullmatch(r"[0-9a-fA-F]{5}", to):
        to = "@" + to
    msg = _strip_human_prefix(body)
    if not msg:
        msg = "（空訊）"
    when = time.localtime(ts if ts is not None else time.time())
    stamp = time.strftime("%Y/%m/%d %H:%M:%S", when)
    return f"{stamp} 真人{hid} → {to}  {msg}"


def append_human_dm(cockpit_dir, human_id, to_label, body, ts=None) -> Path:
    """追加一列真人 DM；回傳 dialog-lines 路徑。"""
    line = format_human_dm(human_id, to_label, body, ts=ts)
    path = dialog_path(cockpit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []
    existing = [ln for ln in existing if is_human_dialog_line(ln)]
    existing.append(line)
    if len(existing) > MAX_LINES:
        existing = existing[-MAX_LINES:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(existing) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_human_dialog_lines(cockpit_dir) -> list[str]:
    path = dialog_path(cockpit_dir)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln for ln in raw if is_human_dialog_line(ln)]


def _cli(argv=None):
    ap = argparse.ArgumentParser(prog="dialog_log")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("append")
    p.add_argument("--cockpit", required=True)
    p.add_argument("--human-id", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--body-file")
    sub.add_parser("read").add_argument("--cockpit", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "append":
        body = args.body
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        path = append_human_dm(args.cockpit, args.human_id, args.to, body)
        print(path)
    elif args.cmd == "read":
        for ln in read_human_dialog_lines(args.cockpit):
            print(ln)


if __name__ == "__main__":
    _cli()
