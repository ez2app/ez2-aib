#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fit streamed pane content without letting it scroll over fixed tab rows."""

import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "render"))
from render_lib import trunc  # noqa: E402


def positive_int(name, default):
    try:
        value = int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


width = max(1, positive_int("EZ2_PANE_W", 80) - 1)
height = positive_int("EZ2_PANE_H", 10000)
try:
    offset = max(0, int(os.environ.get("EZ2_H_OFFSET") or 0))
except (TypeError, ValueError):
    offset = 0
rows = max(0, height - offset - 1)

OVERFLOW_MARK = "⋯"

# ★★ 截斷本身沒問題(視覺裁切不丟資料,rowmap 仍完整);**沒有痕跡才是問題**。
#
# 原本是 `if emitted >= rows: continue` —— 超出高度的行直接丟掉,零提示。
# 一席一行的年代那大約只吃掉最後一兩席;三行制之後一席佔四行,28 行高的
# 面板從**第 8 席**起整席消失,而畫面看起來完全正常(2026-08-16 實測:
# 6 席以內完整、7 席被切掉第二三行、8 席起整席不見)。
#
# 這正是本 repo 記過的那個形狀:`render_ai_list` 曾有 `[:6]` 上限,真機 9 窗
# 被**靜默**丟棄,測試 test_nine_seats_all_rendered_no_silent_drop 就是為它
# 而寫的。同一個病換一個變數——這次是高度不是切片。
#
# 所以這裡不改變「會截斷」這件事(要顯示多少席是產品決定,不是本檔的),
# 只保證**截斷會留下痕跡**:最後一行換成「還有 N 行未顯示」。
# 「假資料比缺資料毒」——而一個看起來完整的殘缺清單,正是假資料。
lines = [raw.rstrip("\r\n") for raw in sys.stdin]

if rows <= 0:
    pass
elif len(lines) <= rows:
    for line in lines:
        sys.stdout.write(trunc(line, width) + "\n")
else:
    # 留最後一行給告示;rows==1 時整個面板就只剩告示,那也比安靜騙人好。
    keep = max(0, rows - 1)
    for line in lines[:keep]:
        sys.stdout.write(trunc(line, width) + "\n")
    hidden = len(lines) - keep
    sys.stdout.write(
        trunc(" %s 還有 %d 行未顯示" % (OVERFLOW_MARK, hidden), width) + "\n")
