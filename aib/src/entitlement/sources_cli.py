#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-persona-sources CLI：來源開|關／狀態。

for_task: evening-D-0926 / DESIGN §4.4
aib persona 來源|狀態|source|status 經 tools/entitlement/aib_persona_sources.py 轉呼本檔(10-15 開源版補上入口)。
"""
from __future__ import annotations

import sys
from typing import List

from .sources import (
    MSG_BAD_ACTION,
    _t,
    format_status,
    parse_source_argv,
    set_source,
)

_ON = frozenset(("開", "on", "ON", "true", "1"))
_OFF = frozenset(("關", "off", "OFF", "false", "0"))


def _usage() -> str:
    return (
        "usage: aib persona 來源 <provider> 開|關\n"
        "       aib persona source <provider> on|off\n"
        "       aib persona 狀態|status\n"
    )


def cmd_status(_rest: List[str]) -> int:
    sys.stdout.write(format_status())
    return 0


def cmd_source(rest: List[str]) -> int:
    if len(rest) < 2:
        sys.stderr.write(_t("aib.persona_sources.usage", MSG_BAD_ACTION) + "\n")
        return 2
    provider = rest[0]
    action = rest[1]
    if action in _ON:
        enabled = True
    elif action in _OFF:
        enabled = False
    else:
        sys.stderr.write(_t("aib.persona_sources.usage", MSG_BAD_ACTION) + "\n")
        return 2
    ok, msg, rc = set_source(provider, enabled)
    stream = sys.stdout if ok else sys.stderr
    stream.write(msg + "\n")
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
