#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""275(b):首頁八格的 AIB 風格表。順序與字句釘在 ez2-ai 15880337 的八格示範。"""
from __future__ import annotations

from types import MappingProxyType

# (風格編號, theme_id, 風格名, 人設, 首頁那句)。shape_id 是產品的另一個欄位,
# 但不是風格身份的一部分;八格都以 rounded 作預設,既有頁的 shape 不被載入器改寫。
_ROWS = (
    ("S1", "nebula", "深空", "創作者", "預設 · 星雲極光。音樂/影片/AI 分身的舞台。"),
    ("S2", "mono", "純粹", "作品集", "白盒子。社群列+滿版作品圖,內容就是主角。"),
    ("S3", "dawn", "曙光", "講師", "暖色舞台。活動報名+電子報,把聽眾變學員。"),
    ("S4", "forest", "森海", "店家", "沉穩墨綠。商品架+LINE 訂位,店家的門口。"),
    ("S5", "rosegold", "玫瑰金", "美妝", "美妝、婚宴、質感手作的暖粉金。"),
    ("S6", "starlight", "星光", "作家", "作家、職人的暖銀奶油色。"),
    ("S7", "pop", "繽紛", "樂團", "樂團、表演者的高飽和舞台。"),
    ("S8", "ocean", "海洋", "潛旅", "潛旅、戶外教練的深海藍。"),
)

STYLE_ORDER = tuple(row[0] for row in _ROWS)
STYLES = MappingProxyType({sid: MappingProxyType({
    "id": sid, "theme_id": theme, "name": name, "persona": persona,
    "homepage_line": line, "default_shape": "rounded"
}) for sid, theme, name, persona, line in _ROWS})
THEME_TO_STYLE = MappingProxyType({row[1]: row[0] for row in _ROWS})
if len(STYLES) != 8 or len(THEME_TO_STYLE) != 8:
    raise RuntimeError("八風格表有重複編號或 theme_id")


def for_theme(theme_id):
    """由 theme_id 找到一張風格資料;未知值由呼叫端具名處理。"""
    if not isinstance(theme_id, str):
        return None
    style_id = THEME_TO_STYLE.get(theme_id)
    return STYLES.get(style_id) if style_id else None
