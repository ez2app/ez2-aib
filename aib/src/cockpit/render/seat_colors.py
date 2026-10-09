#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位底色色表（Wave2 包 E｜展開與底色）。

for_task: expand-evening-e-0927 / DISPATCH-1857a 包 E

純函式、無 I/O、無副作用。顯示層可拿 seat_bg_ansi；資料層必須用
strip_ansi／display=False 路徑，禁止把 ANSI 寫進檔／JSON。

狀態色（紅／黃／綠）保留給 status marks；席位底色必須與它們色相距離
≥ MIN_HUE_DISTANCE_DEG，由 assert_seat_colors_safe() 守門。
"""
from __future__ import annotations

import colorsys
import hashlib
import re
from typing import Iterable, Mapping, Sequence, Tuple

RGB = Tuple[int, int, int]

# 狀態色相（degrees 0–360）。紅≈0、黃≈60、綠≈120。
STATUS_HUES: Mapping[str, float] = {
    "red": 0.0,
    "yellow": 60.0,
    "green": 120.0,
}

# 席位色與 STATUS 紅黃綠的最小色相距離（度）。寫進模組方便測與還原刀。
MIN_HUE_DISTANCE_DEG: float = 40.0

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
)

# 已知席／provider／角色 → RGB 底色（刻意避開紅黃綠色相帶）。
# 色相落在藍／青／紫／洋紅／靛；飽和與明度適中，終端底色可讀。
_KNOWN_SEAT_RGB: dict[str, RGB] = {
    # providers / engines
    "claude": (70, 110, 200),       # blue
    "codex": (90, 90, 140),         # slate-indigo
    "gemini": (80, 60, 200),        # indigo
    "agy": (80, 60, 200),
    "copilot": (0, 100, 180),       # deep azure
    "grok": (120, 80, 180),         # violet (bg; CLI 前景可無色)
    "deepseek": (60, 80, 220),      # royal blue
    "kiosapi": (40, 140, 160),      # teal-cyan
    "nvidia": (50, 100, 160),       # steel blue (NOT nvidia green)
    "teamo": (30, 140, 200),        # sky
    "teamoclaude": (120, 70, 210),  # violet
    "openrouter": (40, 130, 150),   # teal
    "typesafe": (50, 140, 160),
    "openrouterjev": (45, 125, 155),
    "sand": (100, 90, 160),
    "codexbot": (50, 130, 150),
    "grokbot": (150, 70, 200),      # magenta-violet
    "musebot": (91, 141, 239),       # Meta blue #5B8DEF
    # common seat / role ids
    "seatA": (65, 105, 195),
    "seatB": (95, 75, 185),
    "seatC": (55, 125, 175),
    "seatD": (110, 85, 190),
    "seatE": (75, 95, 205),
    "seatF": (85, 70, 195),
    "@seatA": (65, 105, 195),
    "@seatB": (95, 75, 185),
    "@seatC": (55, 125, 175),
    "@seatD": (110, 85, 190),
    "@seatE": (75, 95, 205),
    "@seatF": (85, 70, 195),
    "A": (65, 105, 195),
    "B": (95, 75, 185),
    "C": (55, 125, 175),
    "D": (110, 85, 190),
    "E": (75, 95, 205),
    "F": (85, 70, 195),
}

# 未知席穩定 hash 用的色盤（全部已通過與 RYG 的距離檢查）。
_PALETTE: Sequence[RGB] = (
    (70, 110, 200),
    (90, 90, 140),
    (80, 60, 200),
    (0, 100, 180),
    (120, 80, 180),
    (60, 80, 220),
    (40, 140, 160),
    (50, 100, 160),
    (30, 140, 200),
    (120, 70, 210),
    (150, 70, 200),
    (55, 125, 175),
    (100, 90, 160),
    (75, 95, 205),
)


def strip_ansi(s: str) -> str:
    """去掉所有 ANSI／OSC 跳脫碼，留下純文字。"""
    if not s:
        return s
    return _ANSI_RE.sub("", s)


def rgb_to_hue(rgb: RGB) -> float:
    """RGB (0–255) → hue degrees [0, 360)."""
    r, g, b = (max(0, min(255, int(c))) / 255.0 for c in rgb)
    h, _s, _v = colorsys.rgb_to_hsv(r, g, b)
    return (h * 360.0) % 360.0


def hue_distance(c1: RGB | float, c2: RGB | float) -> float:
    """兩色（或兩 hue 度數）的最短色相距離，回傳 [0, 180]。"""
    if isinstance(c1, (tuple, list)):
        h1 = rgb_to_hue((int(c1[0]), int(c1[1]), int(c1[2])))
    else:
        h1 = float(c1) % 360.0
    if isinstance(c2, (tuple, list)):
        h2 = rgb_to_hue((int(c2[0]), int(c2[1]), int(c2[2])))
    else:
        h2 = float(c2) % 360.0
    d = abs(h1 - h2) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _normalize_seat_id(seat_id: str | None) -> str:
    if seat_id is None:
        return ""
    s = str(seat_id).strip()
    return s


def _hash_to_palette(seat_id: str) -> RGB:
    digest = hashlib.sha256(seat_id.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % len(_PALETTE)
    return _PALETTE[idx]


def seat_bg_color(seat_id: str | None) -> RGB:
    """席位底色 RGB。已知表命中用表；未知席穩定 hash 到色盤。"""
    key = _normalize_seat_id(seat_id)
    if not key:
        return _PALETTE[0]
    # exact → lower → strip @
    if key in _KNOWN_SEAT_RGB:
        return _KNOWN_SEAT_RGB[key]
    low = key.lower()
    if low in _KNOWN_SEAT_RGB:
        return _KNOWN_SEAT_RGB[low]
    if low.startswith("@") and low[1:] in _KNOWN_SEAT_RGB:
        return _KNOWN_SEAT_RGB[low[1:]]
    # provider substring (e.g. "claude-1", "seat-claude")
    for name, rgb in _KNOWN_SEAT_RGB.items():
        if name.startswith("@") or len(name) <= 1:
            continue
        if name.lower() in low:
            return rgb
    return _hash_to_palette(key)


def seat_bg_ansi(seat_id: str | None) -> str:
    """24-bit SGR 背景碼（不含 reset）。顯示層專用。"""
    r, g, b = seat_bg_color(seat_id)
    return f"\x1b[48;2;{r};{g};{b}m"


def paint_seat_bg(text: str, seat_id: str | None) -> str:
    """替整行上席位底色並 reset。資料層勿呼叫。"""
    return f"{seat_bg_ansi(seat_id)}{text}\x1b[0m"


def iter_known_seat_colors() -> Iterable[tuple[str, RGB]]:
    """已知席色表（測試／稽核用）。"""
    return ((k, v) for k, v in sorted(_KNOWN_SEAT_RGB.items()))


def assert_seat_colors_safe(
    *,
    min_distance: float | None = None,
    status_hues: Mapping[str, float] | None = None,
) -> None:
    """每個已知席色與 STATUS 紅黃綠色相距離 ≥ 閾值；否則 raise AssertionError。

    還原刀：把某 seat 色改成純紅，或把本函式改成 pass／把閾值檢查拿掉 ⇒ 測紅。
    """
    thresh = float(MIN_HUE_DISTANCE_DEG if min_distance is None else min_distance)
    hues = STATUS_HUES if status_hues is None else status_hues
    offenders: list[str] = []
    for name, rgb in _KNOWN_SEAT_RGB.items():
        for status_name, status_hue in hues.items():
            dist = hue_distance(rgb, status_hue)
            if dist < thresh:
                offenders.append(
                    f"{name}={rgb} vs {status_name}(hue={status_hue}) dist={dist:.1f}<{thresh}"
                )
    for i, rgb in enumerate(_PALETTE):
        for status_name, status_hue in hues.items():
            dist = hue_distance(rgb, status_hue)
            if dist < thresh:
                offenders.append(
                    f"palette[{i}]={rgb} vs {status_name}(hue={status_hue}) dist={dist:.1f}<{thresh}"
                )
    if offenders:
        raise AssertionError(
            "seat colors too close to status R/Y/G:\n  " + "\n  ".join(offenders)
        )


# Import-time 自檢：色表本身不合格就立刻失敗（開發期）。
# 測也可呼叫 assert_seat_colors_safe() 再驗一次。
assert_seat_colors_safe()
