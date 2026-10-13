#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""活 AI 席上的危險動作硬擋(2026-09-20)。

根因(實測 cabin 270a / nvidia @3):
  09:02:25 窗裡出現 ^C → nvidia_cli 吃 KeyboardInterrupt 後乾淨 exit 0
  → pane_dead → deepseek→nvidia DM 被擋。

本檔是**單一否決表**:自動化路徑要送鍵 / 殺窗 / 塞控制字元進 DM 前必須過這關。
預設**絕對不通過**;只有明確的人為覆寫旗標才放行(並寫 stderr 留痕)。

★ 不是建議、不是 warn-and-continue:guard_* 回非 0 / raise,呼叫端必須停。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterable, Sequence

# ── 否決表(具名,加一列要寫為什麼危險)────────────────────────────────
# tmux send-keys 名稱或字面。
DENIED_SEND_KEYS = frozenset({
    "C-c",      # SIGINT: nvidia/deepseek 等 line-CLI 直接結束(本次事故)
    "C-d",      # EOF: 同上,input() 丟 EOFError 後 exit
    "C-z",      # SIGTSTP: 席掛起,看起來像死
    "C-\\",   # SIGQUIT
    "C-x",      # 部分 CLI 前綴鍵,避免誤觸退出序
})

# argv 子字串(tmux 子命令)。
DENIED_TMUX_SUBCOMMANDS = frozenset({
    "kill-pane",
    "kill-window",
    "kill-session",
})

# DM 正文禁止的 C0(保留 \t \n \r)。
_C0_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_OVERRIDE_ENV = "AIB_ALLOW_DANGEROUS_SEAT_ACTIONS"


def override_armed() -> bool:
    return os.environ.get(_OVERRIDE_ENV, "") == "1"


def _deny(msg: str) -> int:
    sys.stderr.write("dangerous_seat_actions: DENY %s\n" % msg)
    if override_armed():
        sys.stderr.write(
            "dangerous_seat_actions: override %s=1 — 放行但已留痕\n" % _OVERRIDE_ENV
        )
        return 0
    return 2


def guard_send_keys(keys: Sequence[str]) -> int:
    """keys = send-keys 在 -t 目標之後的參數。含否決鍵 → 2;通過 → 0。"""
    for k in keys:
        if not k:
            continue
        # 字面 Ctrl 字元
        if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in k):
            return _deny("send-keys 含 C0 控制字元 %r" % k)
        if k in DENIED_SEND_KEYS or k.upper() in {x.upper() for x in DENIED_SEND_KEYS}:
            return _deny("send-keys 否決鍵 %s" % k)
    return 0


def guard_tmux_argv(argv: Sequence[str]) -> int:
    """整段 tmux argv(可含 -L socket)。否決殺窗/殺 pane 等。"""
    if not argv:
        return 0
    # 找子命令:跳過選項
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        # -L name / -t target 吃下一個
        if argv[i] in ("-L", "-S", "-t", "-s", "-f") and i + 1 < len(argv):
            i += 2
            continue
        i += 1
    sub = argv[i] if i < len(argv) else ""
    if sub in DENIED_TMUX_SUBCOMMANDS:
        return _deny("tmux 子命令否決 %s" % sub)
    if sub == "send-keys":
        # send-keys [-t target] [--] keys...
        rest = list(argv[i + 1 :])
        if rest and rest[0] == "-t" and len(rest) >= 2:
            rest = rest[2:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        return guard_send_keys(rest)
    return 0


def guard_dm_body(text: str) -> int:
    """DM 正文不得夾帶會被终端吃成按鍵的 C0。"""
    if text is None:
        return 0
    if _C0_RE.search(text):
        return _deny("DM 正文含 C0 控制字元(會被终端當按鍵,可殺席)")
    return 0


def denied_send_keys() -> Iterable[str]:
    return sorted(DENIED_SEND_KEYS)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI:
      guard-send-keys [--] KEY...
      guard-tmux -- ARGV...
      guard-dm-body  (stdin)
      list
    """
    av = list(sys.argv[1:] if argv is None else argv)
    if not av or av[0] in ("-h", "--help"):
        sys.stdout.write(
            "usage: dangerous_seat_actions.py "
            "guard-send-keys|guard-tmux|guard-dm-body|list\n"
        )
        return 0
    cmd = av[0]
    if cmd == "list":
        for k in denied_send_keys():
            sys.stdout.write("%s\n" % k)
        for s in sorted(DENIED_TMUX_SUBCOMMANDS):
            sys.stdout.write("tmux:%s\n" % s)
        return 0
    if cmd == "guard-send-keys":
        keys = av[1:]
        if keys and keys[0] == "--":
            keys = keys[1:]
        return guard_send_keys(keys)
    if cmd == "guard-tmux":
        args = av[1:]
        if args and args[0] == "--":
            args = args[1:]
        return guard_tmux_argv(args)
    if cmd == "guard-dm-body":
        return guard_dm_body(sys.stdin.read())
    sys.stderr.write("unknown cmd %s\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
