#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-persona-sources CLI：來源開|關／狀態。

for_task: evening-D-0926 / DESIGN §4.4
可由 aib persona 來源|狀態 轉呼（入口由 a seat 接）；本包裝腳本。
"""
from __future__ import annotations

import sys
from typing import List

from .sources import (
    MSG_BAD_ACTION,
    MSG_PAID_REQUIRED,
    format_status,
    parse_source_argv,
    set_source,
)

_ON = frozenset(("開", "on", "ON", "true", "1"))
_OFF = frozenset(("關", "off", "OFF", "false", "0"))


def _usage() -> str:
    return (
        "usage: aib-persona-sources 來源 <provider> 開|關\n"
        "       aib-persona-sources source <provider> on|off\n"
        "       aib-persona-sources 狀態|status\n"
        "（aib 入口待 @00000 接：aib persona 來源|狀態）\n"
    )


def cmd_status(_rest: List[str]) -> int:
    sys.stdout.write(format_status())
    return 0


def cmd_source(rest: List[str]) -> int:
    if len(rest) < 2:
        sys.stderr.write(MSG_BAD_ACTION + "\n")
        return 2
    provider = rest[0]
    action = rest[1]
    if action in _ON:
        enabled = True
    elif action in _OFF:
        enabled = False
    else:
        sys.stderr.write(MSG_BAD_ACTION + "\n")
        return 2
    ok, msg, rc = set_source(provider, enabled)
    stream = sys.stdout if ok else sys.stderr
    stream.write(msg + "\n")
    # 拒開時附設計稿長句，方便審核對照（短句已在 msg）
    if not ok and msg == MSG_PAID_REQUIRED:
        # msg 已是「付費版可開」；不再重複印長句到 stderr 造成兩行混淆
        pass
    return rc


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = parse_source_argv(args)
    if cmd == "help":
        sys.stdout.write(_usage())
        return 0 if args and args[0] in ("-h", "--help", "help") else 2
    if cmd == "status":
        return cmd_status(rest or [])
    if cmd == "source":
        return cmd_source(rest or [])
    sys.stderr.write(_usage())
    sys.stderr.write(f"unknown: {args[0] if args else ''}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
