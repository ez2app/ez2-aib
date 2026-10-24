#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""大綱 -> 任務 生成器(三層樹)。

E2.1 遷入(解析改 cockpit_lib,大綱與任務板路徑改 env)。

只把「approved 且沒有子項的葉子條目」轉成任務;draft 一律不碰。冪等。

後端(--board,env EZ2_TASK_BOARD):
  sandbox  沙盒 JSON 板(指揮艙現行讀的板;行為與 E2.1 版逐字相同)。預設,也是唯一的後端。

--only <軸>(可逗號多值)只處理指定軸/條目:E3=整軸(E3.1、E3.2…),E2.8=單條。

rc:0=完成 / 2=用法錯或未知 board。

測試:tests/test-outline-to-tasks.py(過濾)+ tests/test-cockpit-smoke.py(沙盒冪等)。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cockpit_lib import parse_outline_file  # noqa: E402

OUTLINE = os.environ.get(
    "EZ2_OUTLINE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "docs", "EZ2-AIB-DEV-OUTLINE.md"))
# ED.1(2026-08-22):預設不再寫死家目錄——cockpit-env.sh 把 EZ2_TASKS_DIR 放在
#   $EZ2_AIB_INSTANCE_HOME/cockpit/sandbox-tasks。ED.1 (e):這條鏈改由
#   seats_dir_lib.tasks_dir() 單源提供(同結果,多加一層 EZ2_COCKPIT_STATE_DIR),
#   不在第四支檔再抄一份 env 鏈。
from seats_dir_lib import tasks_dir  # noqa: E402
TASKS = tasks_dir()

def match_only(item_id, only):
    """--only 過濾:E3 涵蓋 E3 與 E3.*;E2.8 恰指該條。only 為 None=不過濾。"""
    if only is None:
        return True
    return any(item_id == o or item_id.startswith(o + ".") for o in only)


def run_sandbox(items):
    """E2.1 原行為,逐字不動(smoke 測試鎖此輸出)。"""
    os.makedirs(TASKS, exist_ok=True)
    made, skipped = [], []
    for it in items:
        if not it["leaf"]:
            continue
        path = os.path.join(TASKS, f"T-{it['id']}.json")
        if it["approval"] != "approved":
            skipped.append((it["id"], "draft:候拍板"))
            continue
        if os.path.exists(path):
            skipped.append((it["id"], "已有任務"))
            continue
        task = {"id": f"T-{it['id']}", "outline": it["id"], "title": it["title"],
                "priority": it["priority"], "status": "open", "assignee": None,
                "worktree": None, "updated_at": int(time.time())}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        made.append(it["id"])
    print(f"生成 {len(made)} 筆: {','.join(made) or '-'}")
    for i, why in skipped:
        print(f"跳過 {i}: {why}")
    return 0


def main(argv):
    board = os.environ.get("EZ2_TASK_BOARD", "sandbox")
    only = None
    it = iter(argv[1:])
    for a in it:
        if a == "--board":
            board = next(it, "") or ""
        elif a == "--only":
            spec = next(it, "") or ""
            only = [x for x in spec.split(",") if x]
            if not only:
                print("--only 不可為空,例:--only E3 或 --only E3,E7.0", file=sys.stderr)
                return 2
        else:
            print("usage: outline-to-tasks.py [--board sandbox] [--only 軸,...]", file=sys.stderr)
            return 2
    items = [x for x in parse_outline_file(OUTLINE) if match_only(x["id"], only)]
    if only:
        print(f"--only {','.join(only)}:過濾後 {len(items)} 筆候選")
    if board == "sandbox":
        return run_sandbox(items)
    print(f"未知 board: {board}", file=sys.stderr)
    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv))
