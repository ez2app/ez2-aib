# SPDX-License-Identifier: Apache-2.0
"""拖或滾一下：同一支程式改位置並重切畫面，不再連開三支。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "mouse"))

import ai_scroll_blit  # noqa: E402
import right_scroll_drag  # noqa: E402
import scroll_nudge  # noqa: E402
import usage_scroll_blit  # noqa: E402


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def step(state_dir):
    changed = False
    grab = os.path.join(state_dir, "scrollbar-grab")
    ypath = os.path.join(state_dir, ".scroll-drag.y")
    if os.path.isfile(grab) and os.path.isfile(ypath):
        try:
            with open(ypath, encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw.isdigit() and right_scroll_drag.apply_y(state_dir, int(raw)):
                changed = True
        except OSError:
            pass
    scroll = os.path.join(state_dir, "right-scroll.json")
    qdir = os.path.join(state_dir, ".scroll-wheel.q")
    queued = []
    if os.path.isdir(qdir):
        queued = [name for name in os.listdir(qdir) if name.startswith("e-")]
    before = _mtime(scroll)
    scroll_nudge.drain(state_dir)
    if _mtime(scroll) != before:
        changed = True
    else:
        # 已經到頂或到底。這一拍之前排隊的丟掉。這拍之後新來的留下。
        for name in queued:
            try:
                os.remove(os.path.join(qdir, name))
            except OSError:
                pass
    return changed


def main(argv):
    state_dir = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    if not state_dir or not step(state_dir):
        return 3
    kind = argv[1] if len(argv) > 1 else "usage"
    if kind == "ai":
        return ai_scroll_blit.main()
    return usage_scroll_blit.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
