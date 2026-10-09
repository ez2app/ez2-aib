#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""駕駛艙外掛的檔案在哪(10-08 the maintainer:訊息待審做成收費外掛,不放在免費程式裡)。

    python3 addon_path.py <外掛名> <檔名>     # 印出路徑;找不到 ⇒ 什麼都不印、結束碼 1

找法跟 tools/verify/aib_plugins.py 一樣:倉外 ~/.ez2-aib/plugins/<名>(EZ2_AIB_HOME 可改)優先,再找倉裡 plugins/<名>。
只回「有沒有裝」;准不准開由功能開關判(feature_policy),兩件事分開。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def find(name, filename):
    home = Path(os.environ.get("EZ2_AIB_HOME") or (Path.home() / ".ez2-aib"))
    for base in (home / "plugins" / name, ROOT / "plugins" / name):
        p = base / filename
        if p.is_file():
            return p
    return None


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(2)
    got = find(sys.argv[1], sys.argv[2])
    if got is None:
        sys.exit(1)
    print(got)
