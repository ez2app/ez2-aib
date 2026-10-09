# SPDX-License-Identifier: Apache-2.0
"""指揮艙共用純函數(E2.1)。

demo 期間 CJK 寬度計算與大綱解析被複製貼上在四支腳本裡;遷入正本時抽到
這裡成為唯一實作,shell 腳本的 heredoc python 以 PYTHONPATH 匯入
(cockpit-env.sh 已設好)。全部是純函數 —— 不讀環境、不做 I/O,
因此 tests/test-cockpit-smoke.py 可以直接測。
"""

import re
import unicodedata

# ★ EH.17 甲:末五碼取法收進 seat_address.uuid_tail5 一處。本檔原本自己算,
#   而三處算法**不等價**(見該函式 docstring)。同目錄兄弟模組,不動 sys.path。
import os as _os_eh17
import sys as _sys_eh17
_HERE_EH17 = _os_eh17.path.dirname(_os_eh17.path.abspath(__file__))
if _HERE_EH17 not in _sys_eh17.path:
    _sys_eh17.path.insert(0, _HERE_EH17)
from seat_address import uuid_tail5  # noqa: E402
from collections import Counter


# -- Session provider normalization ---------------------------------------


def canonical_provider(record):
    """Normalize registry provider evidence to the AIB hub selector."""
    aliases = {
        "claude": "claude",
        "codex": "codex",
        "gemini": "gemini",
        "agy": "agy",
        "antigravity": "agy",
        "copilot": "copilot",
        "grok": "grok",
        "grokbot": "grokbot",
        "claudebot": "claudebot",
        "musebot": "musebot",
        "sand": "sand",
        "codexbot": "codexbot",
        "codexapp": "codexbot",  # 2026-09-18 改名；舊席相容
    }

    def normalize(value):
        value = str(value or "").strip().lower()
        for prefix, provider in aliases.items():
            if (value == prefix or value.startswith(prefix + "-")
                    or value.startswith(prefix + ":")):
                return provider
        return ""

    ai_id = str(record.get("ai_id") or "")
    sig = str(record.get("sig") or "")
    sig_engine = sig.rsplit("/", 1)[1] if "/" in sig else ""
    for value in (record.get("provider"), ai_id.split(":", 1)[0],
                  record.get("engine"), sig_engine):
        provider = normalize(value)
        if provider:
            return provider
    return ""


def session_display_label(record):
    """Display only ``role@<UUID last five>``; the full sig stays in data."""
    sig = str(record.get("sig") or "").split("/", 1)[0]
    uuid = str(record.get("session_uuid") or "")
    role = str(record.get("role") or "").strip()
    if not role:
        role = sig.split("@", 1)[0] or "?"
    return "%s@%s" % (role, uuid_tail5(uuid) or "-----")


def mark_colliding_labels(labels):
    """Keep the five-character rule but make duplicate display IDs explicit."""
    labels = list(labels)
    counts = Counter(labels)
    return [label + "!" if counts[label] > 1 else label for label in labels]

# -- CJK 對齊(無框線字元版面的地基) -------------------------------------


def cjk_w(s):
    """顯示寬度:全形/寬字算 2 格,其餘 1 格。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, width):
    """右補空白到指定顯示寬度(CJK 安全,對齊表格欄)。"""
    return s + " " * max(0, width - cjk_w(s))


def trunc(s, width):
    """截斷到顯示寬度並補 '…';絕不把一個全形字切成半格。"""
    out, used = "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if used + cw > width - 1:
            return out + "…"
        out += c
        used += cw
    return out


# -- Theme contrast -------------------------------------------------------


def xterm256_rgb(index):
    """Return the canonical RGB tuple for an xterm 256-colour index."""
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 255:
        raise ValueError("xterm colour index must be an integer from 0 to 255")
    basic = (
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    )
    if index < 16:
        return basic[index]
    if index < 232:
        value = index - 16
        levels = (0, 95, 135, 175, 215, 255)
        return (levels[value // 36], levels[(value % 36) // 6],
                levels[value % 6])
    grey = 8 + (index - 232) * 10
    return grey, grey, grey


def _relative_luminance(rgb):
    def linear(channel):
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(left, right):
    light, dark = sorted((_relative_luminance(left), _relative_luminance(right)),
                         reverse=True)
    return (light + 0.05) / (dark + 0.05)


def panel_contrast_sgr(background):
    """Choose readable panel text for a themed xterm background.

    The approved gold background (130) needs bold black, while the three dark
    region colours need light grey.  Compare the real xterm RGB contrast rather
    than maintaining another hand-written bright/dark allowlist.
    """
    bg = xterm256_rgb(background)
    # The terminal palette uses light grey 252 rather than full white. A 0.16
    # luminance boundary keeps rose 95 and blue 24 in the dark family while
    # classifying approved gold 130 as bright, matching the eye review.
    if _relative_luminance(bg) >= 0.16:
        return "\x1b[1;38;5;16m"
    return "\x1b[38;5;252m"


# -- 大綱解析(戰情室機器可讀源) ------------------------------------------
# 行格式:`[編號] approved|draft p優先級 | 標題`,縮排兩格一層,散文行忽略

OUTLINE_LINE = re.compile(
    r"^(\s*)\[([\w.]+)\]\s+(approved|draft)\s+(p\d)\s+\|\s+(.+)$")


def parse_outline_lines(lines):
    """解析大綱文字行 -> 條目 list(dict:depth/id/approval/priority/title/leaf)。

    葉子判定:下一個條目的深度沒有更深,就是葉子(demo 同款演算法)。
    """
    items = []
    for raw in lines:
        m = OUTLINE_LINE.match(raw.rstrip())
        if m:
            items.append({
                "depth": len(m.group(1)) // 2,
                "id": m.group(2),
                "approval": m.group(3),
                "priority": m.group(4),
                "title": m.group(5),
            })
    for i, it in enumerate(items):
        nxt = items[i + 1]["depth"] if i + 1 < len(items) else 0
        it["leaf"] = nxt <= it["depth"]
    return items


def parse_outline_file(path):
    with open(path, encoding="utf-8") as fh:
        return parse_outline_lines(fh)


def claimable_leaves(items):
    """可開工集合:approved 且是葉子(HANDOFF「開工四步」:只做 approved 無人認領的葉子)。"""
    return [it for it in items if it["leaf"] and it["approval"] == "approved"]


# -- 面板狀態機(底部 tab / 大腦樹開合 / 戰情室深度) ----------------------


def next_tab(cur, n_tabs=4):
    """底部 tab 循環:0→1→…→n-1→0;任何非法輸入回 0(demo 同款容錯)。"""
    try:
        d = int(str(cur).strip())
    except (ValueError, TypeError):
        return 0
    if not 0 <= d < n_tabs:
        return 0
    return (d + 1) % n_tabs


def toggle_brain_depth(cur):
    """大腦樹開合:2→1,其餘(含非法)→2。"""
    return 1 if str(cur).strip() == "2" else 2


def next_view_depth(cur, direction, lo=1, hi=3):
    """戰情室展開深度:more=+1、其他=-1,夾在 [lo, hi];非法輸入從 hi 起算。"""
    try:
        d = int(str(cur).strip())
    except (ValueError, TypeError):
        d = hi
    if not lo <= d <= hi:
        d = hi
    d = d + 1 if direction == "more" else d - 1
    return max(lo, min(hi, d))
