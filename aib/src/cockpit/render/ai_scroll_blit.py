# SPDX-License-Identifier: Apache-2.0
"""1.AI 只按目前的捲動位置重切已畫好的清單。不重查席位。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from render_lib import fit_usage_viewport  # noqa: E402
from right_fold_api import viewport  # noqa: E402


def main():
    state_dir = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    if not state_dir:
        return 2
    try:
        with open(os.path.join(state_dir, ".right-page-ai.json"), encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        return 2
    width = int(os.environ.get("EZ2_PANE_W") or 0) - 1
    pane_h = int(os.environ.get("EZ2_PANE_H") or 0)
    row_off = int(os.environ.get("EZ2_ROW_OFFSET") or 0)
    if width < 2 or int(cache.get("width") or 0) != width:
        return 2
    if int(cache.get("pane_h") or -1) != pane_h or int(cache.get("row_offset") or -1) != row_off:
        return 2
    raw = cache.get("raw_lines")
    raw_map = cache.get("raw_rowmap")
    if not isinstance(raw, list) or not raw or not isinstance(raw_map, list):
        return 2
    total = pane_h - row_off - 1
    if total < 1:
        return 2
    try:
        with open(os.path.join(state_dir, "right-scroll.json"), encoding="utf-8") as fh:
            scroll = json.load(fh)
    except (OSError, ValueError):
        scroll = {}
    offset = viewport.tab_offset(scroll, "ai")
    lines, rowmap = fit_usage_viewport(
        raw, raw_map, header_rows=0, total_rows=total, offset=offset, width=width,
        bar_arrows=True, arrow_tab="ai")
    if row_off:
        for row in rowmap:
            if isinstance(row, dict) and isinstance(row.get("line"), int):
                row["line"] += row_off
    tmp = os.path.join(state_dir, ".ai-rowmap.blit.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rowmap, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(state_dir, "ai-rowmap.json"))
    except OSError:
        return 2
    for ln in lines:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
