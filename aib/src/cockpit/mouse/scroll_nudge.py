# SPDX-License-Identifier: Apache-2.0
"""把滾輪留下的一筆筆方向加總，只改一次捲動位置。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "render"))

import right_fold_ctl  # noqa: E402

# 一次最多 10 步（30 列）。拖太快留下的幾百筆不能一次把清單甩到底。
MAX_STEPS = 10


def drain(state_dir):
    qdir = os.path.join(state_dir, ".scroll-wheel.q")
    if not os.path.isdir(qdir):
        return 0
    # 一次只吃 10 筆。剩下的留著，下一拍再走，不把長滑的距離丟掉。
    names = sorted(name for name in os.listdir(qdir) if name.startswith("e-"))
    net = 0
    for name in names[:MAX_STEPS]:
        path = os.path.join(qdir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                direction = fh.read().strip()
            os.remove(path)
        except OSError:
            continue
        if direction == "down":
            net += 1
        elif direction == "up":
            net -= 1
    if net == 0:
        return 0
    direction = "down" if net > 0 else "up"
    right_fold_ctl.apply(
        state_dir,
        {"action": "fold-scroll", "dir": direction, "rows": abs(net) * 3})
    return 0


def main(argv):
    if len(argv) == 2:
        drain(argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
