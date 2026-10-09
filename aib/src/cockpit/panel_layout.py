#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""左欄面板版面:對話區 / 待審區 可獨立開關、可授權、可堆疊。

產品意圖(2026-09-20):兩塊之後要用授權或 token 分開賣,所以
「開不開」= feature_policy.admit(dialog_panel|pending_panel) ∧ 使用者開關。
關掉 = 不畫、不佔高、不留空白列(與功能區三件同一句 off_means)。

狀態檔(艙 state 根下 panel-layout.json,schema aib-panel-layout/v1):
  dialog_open / pending_open   使用者想不想看到(預設 True;授權不過仍關)
  stack_mode                   all_top | split | all_bottom
  order                        dialog_above | pending_above  (誰在上;split 或同側堆疊時用)
  dialog_height                只允許 1|3|5|10 ([≡N],預設 1)
  realtime_height              回應顯示區只允許 30|40|50 ([≡N],預設 40)
  input_height                 訊息輸入區只允許 1|3|5|10 ([≡N],預設 5)
  realtime_open                中帶回應顯示區(預設 True;夹在私訊對話與待審之間)
  input_open                   與 realtime_open 成對(同開同關;存檔時強制同步)

本檔只回答狀態與有效可見性,不畫圓角框(畫在 render / pane 殼)。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import atomic_json as AJ  # noqa: E402
import feature_policy as FP  # noqa: E402

SCHEMA = "aib-panel-layout/v1"
STACK_MODES = ("all_top", "split", "all_bottom")
ORDERS = ("dialog_above", "pending_above")
PANEL_HEIGHTS = (1, 3, 5, 10)  # 對話／輸入／待審（舊階）
DIALOG_HEIGHTS = PANEL_HEIGHTS  # 別名：舊測試／呼叫仍可用
REALTIME_HEIGHTS = (30, 40, 50)  # 回應顯示區專用
HEIGHT_MIN, HEIGHT_MAX = 1, 10
REALTIME_HEIGHT_MIN, REALTIME_HEIGHT_MAX = 30, 50
DEFAULT_DIALOG_HEIGHT = 1
DEFAULT_REALTIME_HEIGHT = 40
DEFAULT_INPUT_HEIGHT = 5
INPUT_HUB_HARD_CAP = 10  # 打字框實高上限（偏好最高 ≡10）


def _state_root(env=None):
    env = env or os.environ
    # 與 feature_policy 同艙根:有艙家用艙家,否則明寫狀態根
    path = FP.cabin_policy_path("panel-layout.json", env=env)
    return path


def default_layout():
    return {
        "schema": SCHEMA,
        "dialog_open": True,
        "pending_open": True,
        "realtime_open": True,
        "input_open": True,
        "stack_mode": "split",
        "order": "dialog_above",
        "dialog_height": DEFAULT_DIALOG_HEIGHT,
        "realtime_height": DEFAULT_REALTIME_HEIGHT,
        "input_height": DEFAULT_INPUT_HEIGHT,
        # the maintainer 09-30 16:5x「[回應+輸入] [+] 又被叫回來了,把他關掉」:回應+輸入關著時,原生席會留一條
        #   「重開回應區」的細框(need_reopen)。這個鍵關掉 = 連那條細框也不長;預設開(舊行為不變)。
        "reopen_strip": True,
    }


def _clamp_height(n, default=None, choices=None):
    """吸附到 choices（預設 PANEL_HEIGHTS 1/3/5/10）；非法值回 default。"""
    if default is None:
        default = DEFAULT_DIALOG_HEIGHT
    if choices is None:
        choices = PANEL_HEIGHTS
    choices = tuple(choices)
    try:
        v = int(n)
    except (TypeError, ValueError):
        return default
    if v in choices:
        return v
    lo, hi = choices[0], choices[-1]
    if v < lo:
        return lo
    if v > hi:
        return hi
    lower = [h for h in choices if h <= v]
    return lower[-1] if lower else default


def _clamp_realtime_height(n, default=None):
    if default is None:
        default = DEFAULT_REALTIME_HEIGHT
    return _clamp_height(n, default, choices=REALTIME_HEIGHTS)


def load_layout(env=None):
    path = _state_root(env)
    base = default_layout()
    if path is None:
        return base
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    for k in ("dialog_open", "pending_open", "realtime_open"):
        if k in raw:
            out[k] = bool(raw[k])
    # 回應顯示 + 訊息輸入成對：以 realtime_open 為準；舊檔缺 input_open 時跟它走
    if "input_open" in raw and "realtime_open" not in raw:
        out["realtime_open"] = bool(raw["input_open"])
    out["input_open"] = bool(out.get("realtime_open", True))
    if raw.get("stack_mode") in STACK_MODES:
        out["stack_mode"] = raw["stack_mode"]
    if raw.get("order") in ORDERS:
        out["order"] = raw["order"]
    if "dialog_height" in raw:
        out["dialog_height"] = _clamp_height(raw["dialog_height"], DEFAULT_DIALOG_HEIGHT)
    if "realtime_height" in raw:
        out["realtime_height"] = _clamp_realtime_height(raw["realtime_height"], DEFAULT_REALTIME_HEIGHT)
    else:
        out["realtime_height"] = DEFAULT_REALTIME_HEIGHT
    if "input_height" in raw:
        out["input_height"] = _clamp_height(raw["input_height"], DEFAULT_INPUT_HEIGHT)
    else:
        out["input_height"] = DEFAULT_INPUT_HEIGHT
    if "reopen_strip" in raw:
        out["reopen_strip"] = bool(raw["reopen_strip"])
    out["schema"] = SCHEMA
    return out


def save_layout(layout, env=None):
    path = _state_root(env)
    if path is None:
        raise OSError("no cabin home for panel-layout")
    cur = default_layout()
    if isinstance(layout, dict):
        cur.update({k: layout[k] for k in cur if k in layout})
    cur["dialog_height"] = _clamp_height(cur.get("dialog_height"), DEFAULT_DIALOG_HEIGHT)
    cur["realtime_height"] = _clamp_realtime_height(cur.get("realtime_height"), DEFAULT_REALTIME_HEIGHT)
    cur["input_height"] = _clamp_height(cur.get("input_height"), DEFAULT_INPUT_HEIGHT)
    if cur["stack_mode"] not in STACK_MODES:
        cur["stack_mode"] = "split"
    if cur["order"] not in ORDERS:
        cur["order"] = "dialog_above"
    cur["input_open"] = bool(cur.get("realtime_open", True))
    cur["schema"] = SCHEMA
    AJ.write_json(path, cur)
    return cur


def _providers_path():
    return Path(__file__).resolve().parent / "providers.json"


def provider_record(provider_id):
    """讀 providers.json 一筆；找不到回 {}。"""
    pid = (provider_id or "").strip().lower()
    if not pid:
        return {}
    try:
        raw = json.loads(_providers_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("providers") if isinstance(raw, dict) else None
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and str(it.get("id") or "").lower() == pid:
                return it
    elif isinstance(raw, dict):
        rec = raw.get(pid)
        if isinstance(rec, dict):
            return rec
    return {}


def hub_io_may_close(provider_id):
    """只有 OAuth／原生席 (menu_group=native) 可關回應+輸入成對面板。

    回傳: True=可關, False=不可關, None=provider 未知（不裁決）。
    api_key／bot／未知 id → False。空 provider → None。
    """
    pid = (provider_id or "").strip()
    if not pid:
        return None
    rec = provider_record(pid)
    if not rec:
        return False
    if str(rec.get("auth") or "").lower() == "api_key":
        return False
    return str(rec.get("menu_group") or "").lower() == "native"


def hub_io_force_open(provider_id):
    """已知非 native 席 → 強制開。未知／空 provider → 不強制。"""
    may = hub_io_may_close(provider_id)
    if may is None:
        return False
    return not may


def set_open(panel, opened, env=None, provider_id=None):
    """panel in dialog|pending|realtime|input|hub_io。

    realtime／input／hub_io 成對寫同一旗標。關 hub_io 時若已知 provider 非 native，
    回傳 layout 不變（呼叫端可看 effective_open 仍為開）。
    """
    if panel in ("realtime", "input", "hub_io"):
        if not opened and hub_io_force_open(provider_id):
            return load_layout(env)
        lay = load_layout(env)
        lay["realtime_open"] = bool(opened)
        lay["input_open"] = bool(opened)
        return save_layout(lay, env)
    key = {"dialog": "dialog_open", "pending": "pending_open"}.get(panel)
    if not key:
        raise ValueError("unknown panel %r" % panel)
    lay = load_layout(env)
    lay[key] = bool(opened)
    return save_layout(lay, env)


def toggle_hub_io(env=None, provider_id=None):
    """OAuth 席：成對開關回應顯示+訊息輸入。已知非 native → 強制開且不關。"""
    if hub_io_force_open(provider_id):
        lay = load_layout(env)
        lay["realtime_open"] = True
        lay["input_open"] = True
        return save_layout(lay, env)
    on, _ = effective_open("realtime", env=env, provider_id=provider_id)
    return set_open("hub_io", not on, env=env, provider_id=provider_id)


def set_stack_mode(mode, env=None):
    if mode not in STACK_MODES:
        raise ValueError("bad stack_mode %r" % mode)
    lay = load_layout(env)
    lay["stack_mode"] = mode
    return save_layout(lay, env)


def swap_order(env=None):
    lay = load_layout(env)
    lay["order"] = ("pending_above" if lay.get("order") == "dialog_above"
                    else "dialog_above")
    return save_layout(lay, env)


def set_dialog_height(n, env=None):
    lay = load_layout(env)
    lay["dialog_height"] = _clamp_height(n, DEFAULT_DIALOG_HEIGHT)
    return save_layout(lay, env)


def cycle_dialog_height(env=None):
    """點對話區 [≡N]：1→3→5→10→1。"""
    return _cycle_panel_height("dialog_height", DEFAULT_DIALOG_HEIGHT, env=env)


def set_realtime_height(n, env=None):
    lay = load_layout(env)
    lay["realtime_height"] = _clamp_realtime_height(n, DEFAULT_REALTIME_HEIGHT)
    return save_layout(lay, env)


def cycle_realtime_height(env=None):
    """點回應顯示區 [≡N]：30→40→50→30。"""
    return _cycle_panel_height("realtime_height", DEFAULT_REALTIME_HEIGHT, env=env, choices=REALTIME_HEIGHTS)



def effective_input_hub_rows(want, window_height, review_floor=12, frame_floor=8):
    """輸入區 [≡N] 的實作列數：不得把待審／frame 壓扁。

    偏好仍存 input_height；實際 hub -y 用這個夾過的值。
    """
    try:
        w = int(want)
    except (TypeError, ValueError):
        w = DEFAULT_INPUT_HEIGHT
    w = _clamp_height(w, DEFAULT_INPUT_HEIGHT)
    try:
        wh = int(window_height)
    except (TypeError, ValueError):
        wh = 0
    if wh <= 0:
        return w
    # 左欄 = frame + hub + review（pads 與 hub 同高，不另佔）
    max_hub = max(1, wh - int(review_floor) - int(frame_floor))
    return max(1, min(w, max_hub, INPUT_HUB_HARD_CAP))


def effective_hub_io_hub_rows(want, window_height, env=None, provider_id=None, review_floor=12, frame_floor=8):
    """輸入區 chrome 佔 hub 的列數（[≡N] 那塊）。

    成對關閉時回 0——這是「輸入 chrome 列」，不是 CLI 可視列。
    關閉後 CLI 該多大，看 hub_io_closed_pane_heights。
    """
    on, _ = effective_open("hub_io", env=env, provider_id=provider_id)
    if not on:
        return 0
    return effective_input_hub_rows(want, window_height, review_floor=review_floor, frame_floor=frame_floor)


def hub_io_closed_pane_heights(window_height, paint_lines, review_height=0, hub_min=8, review_floor=4):
    """關 hub-io 時 outer：frame／CLI hub 目標列數。

    與 pane-hub-frame.sh _apply_input_height（n<=0 分支）對齊：
    frame ≈ paint 列（私訊+[+]），其餘給 hub，hub 至少 hub_min。
    **必留 review_floor**（預設 4＝空匣最小框），實際高度以 ≡N+框為準；勿再用 12 把 ≡1 撐出透桌布空隙。
    不改 layout 的 dialog_height 偏好。
    回 (frame_h, hub_h)。
    """
    try:
        wh = int(window_height)
    except (TypeError, ValueError):
        wh = 0
    try:
        paint = int(paint_lines)
    except (TypeError, ValueError):
        paint = 1
    try:
        rev = int(review_height)
    except (TypeError, ValueError):
        rev = 0
    try:
        hmin = int(hub_min)
    except (TypeError, ValueError):
        hmin = 8
    try:
        rfloor = int(review_floor)
    except (TypeError, ValueError):
        rfloor = 12
    if paint < 1:
        paint = 1
    if rev < 0:
        rev = 0
    if hmin < 1:
        hmin = 1
    if rfloor < 1:
        rfloor = 1
    rev = max(rev, rfloor)
    if wh <= 0:
        return paint, hmin
    max_frame = wh - rev - hmin
    if max_frame < 1:
        max_frame = 1
    frame_h = paint if paint <= max_frame else max_frame
    hub_h = wh - rev - frame_h
    if hub_h < hmin:
        hub_h = hmin
    return frame_h, hub_h

def hub_io_open_pane_heights(
    window_height,
    hub_rows,
    review_height=12,
    dialog_need=10,
    review_floor=4,
    hub_min=1,
):
    """開 hub-io 時 outer：frame／hub／review 目標列數。

    優先序：
    1) 私訊對話區：frame 至少 dialog_need（完整依 dialog_height 畫出）
    2) 訊息待審區：review 維持 review_height（不低於 review_floor；預設 4）
    3) 回應+輸入：吃掉剩餘（hub≈≡N；frame 內私訊以下的列給回應區）

    窗太矮時：先縮 hub，再把 review 往 floor 收；不改 dialog_height 偏好。
    回 (frame_h, hub_h, review_h)。
    """
    try:
        wh = int(window_height)
    except (TypeError, ValueError):
        wh = 0
    try:
        hub = int(hub_rows)
    except (TypeError, ValueError):
        hub = 1
    try:
        rev = int(review_height)
    except (TypeError, ValueError):
        rev = 12
    try:
        need = int(dialog_need)
    except (TypeError, ValueError):
        need = 10
    try:
        rfloor = int(review_floor)
    except (TypeError, ValueError):
        rfloor = 12
    try:
        hmin = int(hub_min)
    except (TypeError, ValueError):
        hmin = 1
    if hub < 1:
        hub = 1
    if hmin < 1:
        hmin = 1
    if need < 1:
        need = 1
    if rfloor < 1:
        rfloor = 1
    rev = max(rev, rfloor)
    if wh <= 0:
        return need, max(hub, hmin), rev
    hub = max(hub, hmin)
    frame = wh - hub - rev
    if frame < need:
        deficit = need - frame
        hub = max(hmin, hub - deficit)
        frame = wh - hub - rev
    if frame < need and rev > rfloor:
        steal = min(rev - rfloor, need - frame)
        rev -= steal
        frame = wh - hub - rev
    if frame < 1:
        frame = 1
    if hub < hmin:
        hub = hmin
    if wh > 0 and frame + hub + rev > wh:
        rev = max(rfloor, min(rev, wh - need - hmin))
        hub = max(hmin, wh - need - rev)
        frame = max(1, wh - hub - rev)
        if frame < need:
            frame = min(need, max(1, wh - hmin - rfloor))
            hub = max(hmin, wh - frame - rfloor)
            rev = max(rfloor, wh - frame - hub)
    return int(frame), int(hub), int(rev)

def dialog_frame_need(dialog_height):
    """私訊區在 hub-frame 內完整畫出所需列數（選單+圓框）。"""
    try:
        dh = int(dialog_height)
    except (TypeError, ValueError):
        dh = 1
    if dh < 1:
        dh = 1
    return 2 + 2 + dh

def refresh_hub_frame(env=None, runner=None):
    """★ review r2 m9:改完版面檔之後叫 pane-hub-frame.sh --ensure,讓頂框現在就照新設定長/收
    (只寫檔的話,頂框迴圈 --paint 回 3 時保留上一幀,細框一直掛在畫面上)。
    只在艙裡(EZ2_TMUX_SESSION 有值)才叫;回 --ensure 的 rc,沒叫/叫不起來回 None。runner 給尺注入。"""
    e = os.environ if env is None else env
    if not str(e.get("EZ2_TMUX_SESSION") or "").strip():
        return None
    argv = ["bash", str(_HERE / "pane-hub-frame.sh"), "--ensure"]
    try:
        if runner is not None:
            return runner(argv, dict(e))
        import subprocess
        return subprocess.run(argv, env=dict(e), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20).returncode
    except Exception:  # noqa: BLE001 —— 畫面刷新失敗不擋設定已寫進檔
        return None


def reopen_strip_on(env=None, layout=None):
    """使用者要不要那條「重開回應區」細框。只有明寫 reopen_strip=false 才算關(舊檔缺鍵=開)。

    ★ _need_frame(長不長框)、--paint(畫不畫那列)、_apply_input_height(關態框高 +1)
      三處**都問這一個**;只有一處問的話,框不長但高度仍多留一列、或關了照畫。
    """
    lay = layout if isinstance(layout, dict) else load_layout(env)
    return lay.get("reopen_strip", True) is not False


def hub_io_closed_frame_need(dialog_height, env=None, layout=None):
    """回應+輸入關著時 hub-frame 要留的列數:私訊區完整列 + (細框開著才 +1)。"""
    return dialog_frame_need(dialog_height) + (1 if reopen_strip_on(env, layout) else 0)


def set_input_height(n, env=None):
    lay = load_layout(env)
    lay["input_height"] = _clamp_height(n, DEFAULT_INPUT_HEIGHT)
    return save_layout(lay, env)


def cycle_input_height(env=None):
    """點訊息輸入區 [≡N]：1→3→5→10→1。"""
    return _cycle_panel_height("input_height", DEFAULT_INPUT_HEIGHT, env=env)


def _cycle_panel_height(key, default, env=None, choices=None):
    if choices is None:
        choices = PANEL_HEIGHTS
    choices = tuple(choices)
    lay = load_layout(env)
    cur = _clamp_height(lay.get(key), default, choices=choices)
    try:
        i = choices.index(cur)
    except ValueError:
        i = 0
    lay[key] = choices[(i + 1) % len(choices)]
    return save_layout(lay, env)


def entitled(panel, env=None):
    """授權(+登記簿開關)過了嗎。panel: dialog|pending。"""
    feat = {"dialog": "dialog_panel", "pending": "pending_panel"}.get(panel)
    if not feat:
        return False, "unknown_panel"
    return FP.admit(feat, env=env)


def effective_open(panel, env=None, provider_id=None):
    """最終要不要畫:授權 ∧ 使用者開。

    realtime／input／hub_io：成對；非 native 席強制開（api_key／bot 不可關）。
    realtime 暫不走路 feature_policy。
    """
    lay = load_layout(env)
    if panel in ("realtime", "input", "hub_io"):
        if hub_io_force_open(provider_id):
            return True, "non_native_forced"
        on = bool(lay.get("realtime_open", True))
        return on, ("ok" if on else "user_closed")
    ok, why = entitled(panel, env=env)
    if not ok:
        return False, why
    key = "dialog_open" if panel == "dialog" else "pending_open"
    if not lay.get(key, True):
        return False, "user_closed"
    return True, "ok"


def stack_slots(env=None):
    """回傳左欄由上到下的面板 id 列表(不含 realtime)。

    回應顯示區由呼叫端畫在私訊對話與待審之間。
    關閉或未授權的面板不出現(不留空槽)。
    """
    lay = load_layout(env)
    show = []
    for name in ("dialog", "pending"):
        on, _ = effective_open(name, env=env)
        if on:
            show.append(name)
    if not show:
        return []
    if lay["order"] == "pending_above":
        show = list(reversed(show))
    mode = lay["stack_mode"]
    # 位置模式目前以「順序 + 標記」交給 layout 殼解讀;
    # 這裡回傳 (mode, ordered_ids) 讓殼決定貼上/貼下/對分。
    return {
        "mode": mode,
        "panels": show,
        "dialog_height": lay["dialog_height"],
        "realtime_height": lay.get("realtime_height", DEFAULT_REALTIME_HEIGHT),
        "input_height": lay.get("input_height", DEFAULT_INPUT_HEIGHT),
    }


def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="panel_layout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p = sub.add_parser("set-open")
    p.add_argument("panel", choices=("dialog", "pending", "realtime", "input", "hub_io"))
    p.add_argument("--provider", default="", help="focus seat provider id (hub_io gate)")
    p.add_argument("value", choices=("on", "off"))
    p = sub.add_parser("stack")
    p.add_argument("mode", choices=STACK_MODES)
    sub.add_parser("swap")
    p = sub.add_parser("dialog-height")
    p.add_argument("n", type=int)
    sub.add_parser("cycle-dialog-height")
    p = sub.add_parser("realtime-height")
    p.add_argument("n", type=int)
    sub.add_parser("cycle-realtime-height")
    p = sub.add_parser("input-height")
    p.add_argument("n", type=int)
    sub.add_parser("cycle-input-height")
    p = sub.add_parser("toggle-hub-io")
    p.add_argument("--provider", default="")
    sub.add_parser("slots")
    p = sub.add_parser("reopen-strip")
    p.add_argument("value", choices=("on", "off"))
    args = ap.parse_args(argv)
    if args.cmd == "show":
        print(json.dumps(load_layout(), ensure_ascii=False, indent=2))
    elif args.cmd == "set-open":
        print(json.dumps(set_open(args.panel, args.value == "on", provider_id=getattr(args, "provider", None) or None), ensure_ascii=False))
    elif args.cmd == "reopen-strip":
        lay = load_layout()
        lay["reopen_strip"] = args.value == "on"
        save_layout(lay)
        refresh_hub_frame()
        print(json.dumps(load_layout(), ensure_ascii=False))
    elif args.cmd == "stack":
        print(json.dumps(set_stack_mode(args.mode), ensure_ascii=False))
    elif args.cmd == "swap":
        print(json.dumps(swap_order(), ensure_ascii=False))
    elif args.cmd == "dialog-height":
        print(json.dumps(set_dialog_height(args.n), ensure_ascii=False))
    elif args.cmd == "cycle-dialog-height":
        print(json.dumps(cycle_dialog_height(), ensure_ascii=False))
    elif args.cmd == "realtime-height":
        print(json.dumps(set_realtime_height(args.n), ensure_ascii=False))
    elif args.cmd == "cycle-realtime-height":
        print(json.dumps(cycle_realtime_height(), ensure_ascii=False))
    elif args.cmd == "input-height":
        print(json.dumps(set_input_height(args.n), ensure_ascii=False))
    elif args.cmd == "cycle-input-height":
        print(json.dumps(cycle_input_height(), ensure_ascii=False))
    elif args.cmd == "toggle-hub-io":
        print(json.dumps(toggle_hub_io(provider_id=args.provider or None), ensure_ascii=False))
    elif args.cmd == "slots":
        print(json.dumps(stack_slots(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
