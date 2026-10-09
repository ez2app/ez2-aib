#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""薄殼:`ai-task-zone.py` 這個名字在大綱與交件單裡,而可 import 的正本是旁邊的 `ai_task_zone.py`
(連字號的檔名 import 不了)。這裡只轉呼叫,零邏輯 —— 兩個入口一份碼。"""
import pathlib
import runpy
import sys

if __name__ == "__main__":
    sys.exit(runpy.run_path(str(pathlib.Path(__file__).resolve().with_name("ai_task_zone.py")),
                            run_name="__main__"))
