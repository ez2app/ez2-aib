#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Format Codex reset-credit lines for AI用量 panel (國三白話).

Omit the whole block when available_count == 0.
Redeem button only for the active (主登入) account;
click opens https://chatgpt.com/#settings/Usage (reset UI lives there).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


REDEEM_ACTION = "usage-redeem-reset"
REDEEM_LABEL = "使用重置"


def should_show_reset_block(reset_credits: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(reset_credits, dict):
        return False
    n = reset_credits.get("available_count")
    if isinstance(n, (int, float)) and not isinstance(n, bool) and int(n) > 0:
        return True
    credits = reset_credits.get("credits")
    if isinstance(credits, list) and any(
        isinstance(c, dict) and str(c.get("status", "")).lower() == "available"
        for c in credits
    ):
        return True
    return False


def format_reset_lines(
    reset_credits: Mapping[str, Any],
    *,
    is_active: bool,
    indent: str = "  ",
) -> list[str]:
    """Return plain text lines (no ANSI). Empty list if nothing to show."""
    if not should_show_reset_block(reset_credits):
        return []
    n = reset_credits.get("available_count")
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        credits = reset_credits.get("credits") or []
        n = len(credits) if isinstance(credits, list) else 0
    n = int(n)
    head = "%s重置額度：還有 %d 次" % (indent, n)
    if is_active and n > 0:
        head = "%s  [%s]" % (head, REDEEM_LABEL)
    lines = [head]
    credits = reset_credits.get("credits") if isinstance(reset_credits.get("credits"), list) else []
    for c in credits:
        if not isinstance(c, dict):
            continue
        if str(c.get("status", "")).lower() not in ("available", ""):
            if str(c.get("status", "")).lower() != "available":
                continue
        title = c.get("title") or c.get("title_raw") or "重置"
        exp = c.get("expires_label") or ""
        if exp:
            lines.append("%s· %s · %s" % (indent, title, exp))
        else:
            lines.append("%s· %s" % (indent, title))
    return lines


def _display_width(s: str) -> int:
    """Terminal cell width (CJK=2). Keep local to avoid circular import with render_lib."""
    n = 0
    for ch in s:
        o = ord(ch)
        # rough match of render_lib.char_width for BMP CJK / fullwidth
        if o == 0 or (0x00 <= o <= 0x1F) or o == 0x7F:
            continue
        if (
            0x1100 <= o <= 0x115F
            or 0x2329 <= o <= 0x232A
            or 0x2E80 <= o <= 0xA4CF
            or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF
            or 0xFE10 <= o <= 0xFE19
            or 0xFE30 <= o <= 0xFE6F
            or 0xFF00 <= o <= 0xFF60
            or 0xFFE0 <= o <= 0xFFE6
            or 0x20000 <= o <= 0x2FFFD
            or 0x30000 <= o <= 0x3FFFD
        ):
            n += 2
        else:
            n += 1
    return n


def redeem_link_span(line_text: str, indent: str = "  ") -> Optional[dict]:
    """Rowmap link for the 「使用重置」 button, or None.

    start/len are *display columns* (same unit as mouse hit_test), not Python char indices.
    """
    needle = "[%s]" % REDEEM_LABEL
    idx = line_text.find(needle)
    if idx < 0:
        return None
    start = _display_width(line_text[:idx])
    return {
        "col": start,
        "len": _display_width(needle),
        "action": REDEEM_ACTION,
        "label": REDEEM_LABEL,
    }


def pick_credit_to_redeem(reset_credits: Mapping[str, Any]) -> Optional[dict]:
    """First available credit; None if none."""
    credits = reset_credits.get("credits") if isinstance(reset_credits, dict) else None
    if not isinstance(credits, list):
        return None
    for c in credits:
        if isinstance(c, dict) and str(c.get("status", "")).lower() == "available" and c.get("id"):
            return c
    return None


pick_credit_to_redeem = pick_credit_to_redeem
