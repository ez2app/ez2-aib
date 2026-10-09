#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""焦點感知框內視圖切換(E2.16)。

一句話:每個可切框(左/右/底)頭上有一條「視圖標籤列」,三種入口殊途同歸切
**同一份** per-pane 視圖狀態——滑鼠點標籤、選中該框按 tab 循環、Alt 全域快鍵。
判定源永遠是渲染當下寫下的落點檔 view-spans-<pane>.json(與 E2.13 tab-spans、
E2.15 ai-rowmap 同一範式:畫面與選取判定同源,鍵鼠一致)。

為何三入口能一致:它們都只是「改同一個 view-state」的不同觸發,共讀同一份
spans(視圖環順序)+同一份 state(當前視圖)。所以滑鼠點到的、tab 轉到的、
Alt 切到的,永遠是同一個視圖,不會分裂(這是正確性,不是巧合)。

落點檔 view-spans-<pane>.json(渲染標籤列時寫,一列一標籤,x 半開區間):
  [{"view": "persona", "label": "人格", "start": 0, "end": 6},
   {"view": "brain",   "label": "專案樹", "start": 8, "end": 16}, ...]
狀態 view-state.json:{"left": "brain", "bottom": "tab2", ...}(pane -> 當前 view)。

動作(與 E2.13 hit_test 家族同構,回 dict):
  {"action": "set-view", "pane": P, "view": V}   切到某視圖(點擊直達 or tab 轉到)
  {"action": "noop", "reason": ...}              <2 視圖 / 命中不到 / 未知向:不動

x 半開區間命中語意與 hit_test.hit_tabs 完全一致(start<=x<end);test 交叉驗證
兩者對同一組 span 給相同邊界行為,確保沒長出第二套命中規則(反重造)。

CLI(真實 exit code:0=成功,2=用法/來源錯):
  view_switch.py hit  --spans S.json --state-dir DIR --pane left --x N
  view_switch.py tab  --spans S.json --state-dir DIR --pane left [--reverse]
  view_switch.py peek --state-dir DIR --pane left
"""

import fcntl
import json
import os
import sys
from pathlib import Path


def build_bar(views, current, gap=2):
    """視圖清單 -> (border-status 顯示字串, spans 落點檔)。**渲染與命中同一函數**:
    畫面看到的標籤 x 位置,就是點擊命中判定用的 start/end,絕不可能對不上。

    views=[{"view","label"}];current=當前視圖(高亮)。回:
      {"bar": "[人格]  專案樹  艦隊", "spans": [{view,label,start,end}, ...]}
    當前標籤以中括號包(border-status 逐格上色不保證,中括號是保底可辨標記);
    active `[label]` 與 inactive ` label ` 同寬,標籤欄位對齊。x 以字元數計。
    """
    bar_parts, spans, x = [], [], 0
    for i, v in enumerate(views or []):
        label = str(v.get("label", v.get("view", "?")))
        active = current is not None and str(v.get("view")) == str(current)
        cell = ("[%s]" if active else " %s ") % label
        start = x
        bar_parts.append(cell)
        x += len(cell)
        spans.append({"view": str(v.get("view")), "label": label,
                      "start": start, "end": x})
        if i < len(views or []) - 1:
            bar_parts.append(" " * gap)
            x += gap
    return {"bar": "".join(bar_parts), "spans": spans}


def view_ring(spans):
    """spans -> 視圖環(按 start 升序的 view 名,去重保序)。tab 循環的順序源。"""
    seen, ring = set(), []
    for s in sorted(spans or [], key=lambda s: s.get("start", 0)):
        if not isinstance(s, dict):
            continue
        v = s.get("view")
        if v and str(v) not in seen:
            seen.add(str(v))
            ring.append(str(v))
    return ring


def view_hit(spans, x, pane):
    """滑鼠點標籤列 -> set-view。x 半開區間命中(start<=x<end),同 hit_tabs 語意。"""
    try:
        x = int(x)
    except (TypeError, ValueError):
        return {"action": "noop", "reason": "bad_x"}
    for s in spans or []:
        if not isinstance(s, dict) or not s.get("view"):
            continue
        if s.get("start", 0) <= x < s.get("end", 0):
            return {"action": "set-view", "pane": pane, "view": str(s["view"])}
    return {"action": "noop", "reason": "no_label_hit"}


def view_cycle(spans, current, pane, reverse=False):
    """選中該框按 tab -> 視圖環循環下一個(reverse=shift-tab 反向)。"""
    ring = view_ring(spans)
    if len(ring) < 2:
        # 單視圖或無視圖:不動(承 E2.13「判不出就不跳」)
        return {"action": "noop", "reason": "ring_too_small:%d" % len(ring)}
    step = -1 if reverse else 1
    if current and str(current) in ring:
        nxt = ring[(ring.index(str(current)) + step) % len(ring)]
    else:
        # 當前視圖不明:正向從第一個、反向從最後一個進(自然進入點)
        nxt = ring[0] if not reverse else ring[-1]
    return {"action": "set-view", "pane": pane, "view": nxt}


# -- CLI 薄層(讀 spans 同源檔 + view-state;純函數不碰檔)--------------------

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None  # 落點檔缺/壞:當沒 spans,純函數自會 noop,不炸


def _state_path(state_dir):
    return Path(state_dir) / "view-state.json"


def _read_state(state_dir, pane):
    data = _load(_state_path(state_dir))
    if isinstance(data, dict) and isinstance(data.get(pane), str):
        return data[pane]
    return None


def _transition_state(state_dir, pane, compute):
    """鎖內 read -> compute -> write(codex 審中2:同 pane 並發 cycle 原本
    在鎖外讀 current,兩次操作可算出同一 next,遺失一次轉移)。

    compute 收目前值回 action dict;action['action'] != 'set-view' 時不寫。"""
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    p = _state_path(state_dir)
    lock_path = Path(str(p) + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = _load(p)
        if not isinstance(data, dict):
            data = {}
        current = data.get(pane) if isinstance(data.get(pane), str) else None
        action = compute(current)
        if action.get("action") == "set-view":
            data[pane] = action["view"]
            tmp = Path(str(p) + ".tmp.%d" % os.getpid())
            payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, p)
        return action


def _write_state(state_dir, pane, view):
    """鎖內 read-modify-write + 原子替換(codex 審 cockpit 域中級)。

    原本不同 pane 同時切換會互相覆蓋(lost update),寫入中斷還會留下
    半個 JSON。鎖檔與狀態檔分開,避免持鎖者與讀者搶同一個 inode。"""
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    p = _state_path(state_dir)
    lock_path = Path(str(p) + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = _load(p)
        if not isinstance(data, dict):
            data = {}
        data[pane] = view
        tmp = Path(str(p) + ".tmp.%d" % os.getpid())
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)


def _emit(action):
    sys.stdout.write(json.dumps(action, ensure_ascii=False, sort_keys=True) + "\n")


def _parse(argv):
    opts, flags, i = {}, set(), 0
    while i < len(argv):
        a = argv[i]
        if a == "--reverse":
            flags.add("reverse")
            i += 1
        elif a.startswith("--") and i + 1 < len(argv):
            opts[a.lstrip("-")] = argv[i + 1]
            i += 2
        else:
            i += 1
    return opts, flags


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: view_switch.py hit|tab|peek ...", file=sys.stderr)
        return 2
    cmd, (opts, flags) = argv[0], _parse(argv[1:])
    state_dir = opts.get("state-dir")
    pane = opts.get("pane")
    if state_dir is None or pane is None:
        print("需要 --state-dir 與 --pane", file=sys.stderr)
        return 2

    if cmd == "peek":
        _emit({"action": "peek", "pane": pane,
               "view": _read_state(state_dir, pane)})
        return 0

    if cmd == "bar":
        # 渲染標籤列並落 spans 檔(渲染即命中同源):--views '[{view,label},...]'
        try:
            views = json.loads(opts.get("views") or "[]")
        except ValueError:
            print("bar 需要合法 --views JSON", file=sys.stderr)
            return 2
        current = _read_state(state_dir, pane)
        # 當前視圖不在清單(首次)-> 取第一個為預設,寫回
        ring = [str(v.get("view")) for v in views if v.get("view")]
        if current not in ring:
            current = ring[0] if ring else None
            if current is not None:
                _write_state(state_dir, pane, current)
        built = build_bar(views, current)
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        (Path(state_dir) / ("view-spans-%s.json" % pane)).write_text(
            json.dumps(built["spans"], ensure_ascii=False), encoding="utf-8")
        sys.stdout.write(built["bar"] + "\n")
        return 0

    if cmd in ("hit", "tab"):
        if not opts.get("spans"):
            print("%s 需要 --spans" % cmd, file=sys.stderr)
            return 2
        spans = _load(opts["spans"])
        if cmd == "hit":
            if opts.get("x") is None:
                print("hit 需要 --x", file=sys.stderr)
                return 2
            action = view_hit(spans, opts["x"], pane)
            if action["action"] == "set-view":
                _write_state(state_dir, pane, action["view"])
        else:
            # cycle 的讀與寫必須在同一把鎖內,否則並發會算出同一個 next
            action = _transition_state(
                state_dir, pane,
                lambda current: view_cycle(spans, current, pane,
                                           reverse="reverse" in flags))
        _emit(action)
        return 0

    print("unknown command: %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
