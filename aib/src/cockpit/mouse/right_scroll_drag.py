# SPDX-License-Identifier: Apache-2.0
"""拖捲軸時一直留著。游標的列寫在 .scroll-drag.y，這裡改捲動位置。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "render"))

import hit_test  # noqa: E402
import right_fold_ctl  # noqa: E402


def apply_y(state_dir, y):
    rowmap = hit_test._load(os.path.join(state_dir, "ai-rowmap.json"))
    if not isinstance(rowmap, list):
        rowmap = []
    limit = 0
    limits = hit_test._load(os.path.join(state_dir, ".right-scroll-limit.json"))
    tab = "ai"
    try:
        with open(os.path.join(state_dir, "right-tab"), encoding="utf-8") as fh:
            tab = {"1": "usage", "3": "left"}.get(fh.read().strip(), "ai")  # 右欄狀態檔:1=2.AI用量、3=3.離艙(09-30)、其他=1.AI
    except OSError:
        pass
    if isinstance(limits, dict) and tab in limits:
        try:
            limit = max(0, int(limits[tab]))
        except (TypeError, ValueError):
            limit = 0
    target = hit_test.scrollbar_target_offset(rowmap, y, limit)
    if target is None:
        return False
    changed, _note = right_fold_ctl.apply(
        state_dir, {"action": "fold-scroll-to", "offset": target})
    return changed


def follow(state_dir):
    last = None
    quiet = 0
    while True:
        grab = os.path.isfile(os.path.join(state_dir, "scrollbar-grab"))
        y = None
        try:
            with open(os.path.join(state_dir, ".scroll-drag.y"), encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw.isdigit():
                y = int(raw)
        except OSError:
            y = None
        if y is not None and y != last:
            apply_y(state_dir, y)
            last = y
            quiet = 0
        elif not grab:
            quiet += 1
            if quiet >= 3:
                return 0
        time.sleep(0.02)


def main(argv):
    if len(argv) != 2:
        return 0
    return follow(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
