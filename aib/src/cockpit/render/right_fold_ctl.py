#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""274 右欄收合/捲動的狀態寫入端(mouse-ctl 叫;採集端下一幀讀)。

    right_fold_ctl.py --state-dir DIR --action '{"action":"fold-toggle","key":"8ba9b","epoch":N}'
    right_fold_ctl.py --state-dir DIR --action '{"action":"fold-expand-all"|"fold-collapse-all","epoch":N}'
    right_fold_ctl.py --state-dir DIR --action '{"action":"fold-scroll","dir":"up"|"down","rows":3,"epoch":N}'

只動兩個檔:<state>/right-fold.json、<state>/right-scroll.json(原子寫,走 right_fold_api)。
(e)2:action 帶 epoch 且與 <state>/ai-rowmap.json 裡的 epoch 不同 ⇒ 不動、印一行 stale(畫面已經換過那一幀)。
expand-all / collapse-all 的鍵集合 = 現在 rowmap 上的 fold 鍵(渲染器與這裡同源)。
結束碼恆 0(滑鼠路徑不得因狀態檔壞掉而炸);stdout 一行說做了什麼。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import right_fold_api as api  # noqa: E402

SCROLL_ROWS_DEFAULT = 3


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def rowmap_fold_keys(rowmap):
    keys = []
    for entry in rowmap or []:
        key = entry.get("fold") if isinstance(entry, dict) else None
        if isinstance(key, str) and key not in keys:
            keys.append(key)
    return keys


def rowmap_epoch(rowmap):
    for entry in rowmap or []:
        if isinstance(entry, dict) and entry.get("epoch") is not None:
            try:
                return int(entry["epoch"])
            except (TypeError, ValueError):
                return None
    return None


def apply(state_dir, action):
    """回 (changed: bool, note: str)。純函式以外只寫兩個檔。"""
    kind = str(action.get("action") or "")
    fold_path = os.path.join(state_dir, "right-fold.json")
    scroll_path = os.path.join(state_dir, "right-scroll.json")
    rowmap = _read_json(os.path.join(state_dir, "ai-rowmap.json")) or []
    if action.get("epoch") is not None:
        seen = rowmap_epoch(rowmap)
        try:
            mine = int(action.get("epoch"))
        except (TypeError, ValueError):
            mine = None
        if seen is not None and mine is not None and seen != mine:
            return False, "stale epoch %s != %s (frame changed, no-op)" % (mine, seen)
    expanded = api.fold_state.load(fold_path)
    if kind == "fold-toggle":
        key = str(action.get("key") or "")
        if not key:
            return False, "fold-toggle without key"
        api.fold_state.save(fold_path, api.fold_state.toggle(expanded, key))
        return True, "toggle %s" % key
    if kind in ("fold-expand-all", "fold-collapse-all"):
        keys = rowmap_fold_keys(rowmap)
        api.fold_state.save(fold_path, {k: True for k in keys} if kind == "fold-expand-all" else {})
        return True, "%s (%d keys)" % (kind, len(keys))
    if kind == "fold-scroll":
        direction = str(action.get("dir") or "")
        try:
            rows = int(action.get("rows") or SCROLL_ROWS_DEFAULT)
        except (TypeError, ValueError):
            rows = SCROLL_ROWS_DEFAULT
        if direction not in ("up", "down") or rows < 1:
            return False, "fold-scroll needs dir up|down"
        state = _read_json(scroll_path) or {}
        tab = str(action.get("tab") or "")
        if not tab:
            try:
                with open(os.path.join(state_dir, "right-tab"), encoding="utf-8") as fh:
                    raw = fh.read().strip()
            except OSError:
                raw = ""
            # 右欄狀態檔 1 = 第 2 頁 AI用量。沒寫或其他值是 1.AI。
            tab = {"1": "usage", "3": "left", "4": "room"}.get(raw, "ai")  # 右欄狀態檔:1=2.AI用量、3=3.離艙(09-30)、其他=1.AI
        current = api.viewport.tab_offset(state, tab)
        limit = None
        limits = _read_json(os.path.join(state_dir, ".right-scroll-limit.json"))
        if isinstance(limits, dict) and tab in limits:
            try:
                limit = max(0, int(limits[tab]))
            except (TypeError, ValueError):
                limit = None
        # 已經到頂或到底就不要寫檔。滑一下會連送上百次，空寫也會把畫面卡住。
        if direction == "up" and current <= 0:
            return False, "scroll %s already top" % tab
        if limit is not None and direction == "down" and current >= limit:
            return False, "scroll %s already bottom" % tab
        offset = max(0, current - rows) if direction == "up" else current + rows
        if limit is not None and offset > limit:
            offset = limit
        if offset == current:
            return False, "scroll %s unchanged" % tab
        api._atomic_write(scroll_path, api.viewport.with_tab(state, tab, offset))
        if tab == "ai":
            return True, "scroll %s %d -> %d" % (direction, current, offset)
        return True, "scroll %s %s %d -> %d" % (tab, direction, current, offset)
    if kind == "fold-scroll-to":
        try:
            offset = int(action.get("offset"))
        except (TypeError, ValueError):
            return False, "fold-scroll-to needs offset"
        state = _read_json(scroll_path) or {}
        tab = str(action.get("tab") or "")
        if not tab:
            try:
                with open(os.path.join(state_dir, "right-tab"), encoding="utf-8") as fh:
                    raw = fh.read().strip()
            except OSError:
                raw = ""
            tab = {"1": "usage", "3": "left", "4": "room"}.get(raw, "ai")  # 右欄狀態檔:1=2.AI用量、3=3.離艙(09-30)、其他=1.AI
        current = api.viewport.tab_offset(state, tab)
        limit = None
        limits = _read_json(os.path.join(state_dir, ".right-scroll-limit.json"))
        if isinstance(limits, dict) and tab in limits:
            try:
                limit = max(0, int(limits[tab]))
            except (TypeError, ValueError):
                limit = None
        if offset < 0:
            offset = 0
        if limit is not None and offset > limit:
            offset = limit
        if offset == current:
            return False, "scroll %s unchanged" % tab
        api._atomic_write(scroll_path, api.viewport.with_tab(state, tab, offset))
        return True, "scroll %s to %d" % (tab, offset)
    return False, "unknown action %r" % kind


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--action", required=True)
    args = parser.parse_args(argv)
    try:
        action = json.loads(args.action)
    except ValueError:
        print("right-fold: bad action json")
        return 0
    if not isinstance(action, dict):
        print("right-fold: action not an object")
        return 0
    try:
        _changed, note = apply(args.state_dir, action)
    except Exception as exc:                     # noqa: BLE001  滑鼠路徑不得炸
        note = "error %r" % (exc,)
    print("right-fold: " + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
