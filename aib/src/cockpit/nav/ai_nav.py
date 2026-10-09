#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ai-hub 方向鍵選 AI(E2.15)。

純函數:給「上一次畫面畫了什麼」的落點檔(ai-rowmap.json,與 E2.13 滑鼠**同一份**)
+ 目前選中的 AI + 一個方向鍵,回傳要做的動作。不碰 tmux、不讀時鐘 —— 選取判定與
滑鼠命中判定讀同一份 rowmap,所以鍵盤選到的第 N 行 AI 與滑鼠點到的第 N 行 AI
必是同一個(鍵鼠一致,這是 E2.15 的靈魂,test-ai-nav.py 交叉驗證 hit_test 保之)。

方向語意(中欄選擇/跳轉):
  up / down     移動選取游標在 AI 行之間(循環);action=select,不跳轉
  right / enter 跳轉到當前選中的 AI;action=focus-hub(與 hit_test 同構,由 ctl 走
                同一條 select-window)
  left / escape 退出選取模式;action=cancel
判不出來(空 rowmap / 未知方向 / 右跳但無選中)一律 action=noop —— 寧可沒反應,
也不跳去使用者沒要去的地方(承 E2.13 passthrough 精神)。

為何不用裸方向鍵、也不用 M-方向鍵:
  裸 ↑↓←→ 中欄真 AI 對話要用,全域攔截會吃掉;M-Up/Down/Left/Right 四鍵已被
  E2.5 page_switch+view_depth 全數佔用。故走 tmux key-table:M-a 進 ai-nav 模式,
  該模式內裸方向鍵只在 key-table 有效,離開即回歸原義(綁定表 nav-bindings.json)。

CLI(真實 exit code:0=成功,2=用法/來源錯):
  ai_nav.py move  --rowmap R.json --state-dir DIR --dir up|down
  ai_nav.py focus --rowmap R.json --state-dir DIR        # 讀選中 -> focus-hub action
  ai_nav.py cancel --state-dir DIR                       # 清選取
  ai_nav.py peek  --state-dir DIR                        # 印目前選中(除錯)
選取狀態存 <state-dir>/ai-selection.json {"window": "..."}。
"""

import json
import sys
from pathlib import Path

NOOP = {"action": "noop"}
_MOVE_DIRS = ("up", "down")


def ai_windows(rowmap):
    """rowmap=[{line,window}] -> 去重保序(按 line 升序)的 window 列表。"""
    seen, rows = set(), []
    for r in sorted(rowmap or [], key=lambda r: r.get("line", 0)):
        if not isinstance(r, dict):
            continue
        w = r.get("window")
        if w and str(w) not in seen:
            seen.add(str(w))
            rows.append(str(w))
    return rows


def select_move(rowmap, current, direction):
    """核心純函數:同一份 rowmap 決定選取,與 hit_test 讀同源(鍵鼠一致)。"""
    if direction == "left" or direction == "escape":
        return {"action": "cancel"}
    if direction == "right" or direction == "enter":
        rows = ai_windows(rowmap)
        if current and str(current) in rows:
            # 與 hit_test.hit_ai_rows 輸出同構:{"action":"focus-hub","window":...}
            return {"action": "focus-hub", "window": str(current)}
        return {"action": "noop", "reason": "no_current_selection"}
    if direction not in _MOVE_DIRS:
        return {"action": "noop", "reason": "unknown_direction:%s" % direction}
    rows = ai_windows(rowmap)
    if not rows:
        return {"action": "noop", "reason": "no_ai_rows"}
    if current and str(current) in rows:
        i = rows.index(str(current))
        step = 1 if direction == "down" else -1
        nxt = rows[(i + step) % len(rows)]
    else:
        # 無有效選中:down 從第一個進、up 從最後一個進(自然進入點)
        nxt = rows[0] if direction == "down" else rows[-1]
    return {"action": "select", "window": nxt}


# -- CLI 薄層(讀 rowmap 同源檔 + selection 狀態;純函數不碰檔)----------------

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None  # 落點檔缺/壞:當沒 rowmap,select_move 自會 noop,不炸


def _selection_path(state_dir):
    return Path(state_dir) / "ai-selection.json"


def _read_selection(state_dir):
    data = _load(_selection_path(state_dir))
    if isinstance(data, dict) and data.get("window"):
        return str(data["window"])
    return None


def _write_selection(state_dir, window):
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    _selection_path(state_dir).write_text(
        json.dumps({"window": window}, ensure_ascii=False), encoding="utf-8")


def _clear_selection(state_dir):
    p = _selection_path(state_dir)
    if p.exists():
        p.unlink()


def _emit(action):
    sys.stdout.write(json.dumps(action, ensure_ascii=False, sort_keys=True) + "\n")


def _parse(argv):
    opts = {}
    i = 0
    while i < len(argv) - 1:
        if argv[i] in ("--rowmap", "--state-dir", "--dir"):
            opts[argv[i].lstrip("-")] = argv[i + 1]
            i += 2
        else:
            i += 1
    return opts


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: ai_nav.py move|focus|cancel|peek ...", file=sys.stderr)
        return 2
    cmd, rest = argv[0], _parse(argv[1:])
    state_dir = rest.get("state-dir")
    if state_dir is None:
        print("需要 --state-dir", file=sys.stderr)
        return 2

    if cmd == "move":
        direction = rest.get("dir")
        if direction not in _MOVE_DIRS:
            print("move 需要 --dir up|down", file=sys.stderr)
            return 2
        if not rest.get("rowmap"):
            print("move 需要 --rowmap", file=sys.stderr)
            return 2
        rowmap = _load(rest["rowmap"])
        action = select_move(rowmap, _read_selection(state_dir), direction)
        if action["action"] == "select":
            _write_selection(state_dir, action["window"])
        _emit(action)
        return 0

    if cmd == "focus":
        if not rest.get("rowmap"):
            print("focus 需要 --rowmap", file=sys.stderr)
            return 2
        rowmap = _load(rest["rowmap"])
        _emit(select_move(rowmap, _read_selection(state_dir), "enter"))
        return 0

    if cmd == "cancel":
        _clear_selection(state_dir)
        _emit({"action": "cancel"})
        return 0

    if cmd == "peek":
        _emit({"action": "peek", "window": _read_selection(state_dir)})
        return 0

    print("unknown command: %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
