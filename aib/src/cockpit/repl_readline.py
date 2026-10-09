# SPDX-License-Identifier: Apache-2.0
"""Enable line editing for API-key seat CLIs that use builtin input().

必要程式邏輯：每個新 API-key 席 CLI 在 import 時呼叫 enable_repl_readline()
（見 docs/SOP-add-api-key-or-bot-provider-0920.md）。
Gate: tests/test-repl-readline-required.py。

Without importing readline/editline, Unix cooked tty still accepts bytes but does
not interpret arrow keys (they land as literal ESC[D / ESC[C) and backspace
erases one byte — which corrupts UTF-8 Chinese and looks like stray spaces.
"""
from __future__ import annotations

_ENABLED = False


def enable_repl_readline() -> bool:
    """Import readline and bind common cursor keys. Idempotent. Returns True if armed."""
    global _ENABLED
    if _ENABLED:
        return True
    try:
        import readline  # noqa: F401  # side effect: hooks PyOS_Readline
    except ImportError:
        return False
    # editline (macOS) + GNU readline: CSI and SS3 cursor keys
    for spec in (
        r'"\e[D": backward-char',
        r'"\e[C": forward-char',
        r'"\e[A": previous-history',
        r'"\e[B": next-history',
        r'"\eOD": backward-char',
        r'"\eOC": forward-char',
        r'"\eOA": previous-history',
        r'"\eOB": next-history',
        r'"\e[3~": delete-char',
    ):
        try:
            readline.parse_and_bind(spec)
        except Exception:
            pass
    _ENABLED = True
    return True
