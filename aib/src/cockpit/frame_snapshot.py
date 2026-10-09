#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""第 37 項:待審框「一幀 = 一份身分快照」(§89.7.1 接口一規則 1/3/4)。

每一幀畫面寫一份 `pending-snapshot.json`:
    cabin / seat / seat_id / session_uuid / epoch / painted_at / changed_at
- `epoch` 只在**落點檔內容真的變了**才 +1(光澤每 20ms 刷抬頭、正文沒變,不算一幀);
  否則每次重畫都是新幀,確認框永遠過期。
- `painted_at` = 這一次量測(重畫)的時刻;`changed_at` = epoch 上一次跳動的時刻。
- 命中判定讀它:艙不符 ⇒ 不執行(規則 3);剛重畫過(changed_at 距現在 < 寬限)⇒ 要求重看(規則 4 的點擊層形狀:
  你看到的是上一幀,落點檔已經是下一幀)。確認回呼帶 epoch 留痕;內容是否被改仍由 rev 擋(第 36 項)。

純函式 + 一個原子寫檔;不讀 tmux、不讀時鐘(now 由呼叫端傳)。
"""
import json
import os
import time

SNAPSHOT_SCHEMA = "aib-pending-snapshot/v1"
SNAPSHOT_NAME = "pending-snapshot.json"
ROWMAP_NAME = "pending-rowmap.json"
# 落點檔剛換過、人眼還來不及看到:這段時間內的點擊視為對上一幀(要求重看)。
STALE_GRACE_S = 0.4


def read_snapshot(state_dir):
    try:
        with open(os.path.join(state_dir or "", SNAPSHOT_NAME), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SNAPSHOT_SCHEMA:
        return None
    return data


def next_snapshot(prev, ident, changed, now=None):
    """算下一份快照(純函式)。changed=這幀落點檔內容與上一幀不同。"""
    now = float(time.time() if now is None else now)
    prev = prev if isinstance(prev, dict) else {}
    ident = ident if isinstance(ident, dict) else {}
    same_seat = str(prev.get("seat") or "") == str(ident.get("seat") or "")
    try:
        epoch = int(prev.get("epoch") or 0)
    except (TypeError, ValueError):
        epoch = 0
    try:
        changed_at = float(prev.get("changed_at") or 0.0)
    except (TypeError, ValueError):
        changed_at = 0.0
    if changed or not same_seat or epoch <= 0:
        epoch += 1
        changed_at = now
    return {
        "schema": SNAPSHOT_SCHEMA,
        "cabin": str(ident.get("cabin") or ""),
        "seat": str(ident.get("seat") or ""),
        "seat_id": str(ident.get("seat_id") or ""),
        "session_uuid": str(ident.get("session_uuid") or ""),
        "epoch": epoch,
        "painted_at": now,
        "changed_at": changed_at,
    }


def _atomic_write(path, text):
    tmp = "%s.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def publish(state_dir, ident, rowmap, now=None):
    """寫落點檔 + 快照;回寫出去的快照。rowmap 空 ⇒ 兩份都刪、回 None。

    epoch 的判準是 rowmap 序列化後**逐位元組**與上一份不同。
    """
    if not state_dir:
        return None
    rm_path = os.path.join(state_dir, ROWMAP_NAME)
    snap_path = os.path.join(state_dir, SNAPSHOT_NAME)
    if not rowmap:
        for p in (rm_path, snap_path):
            try:
                os.remove(p)
            except OSError:
                pass
        return None
    payload = json.dumps(rowmap, ensure_ascii=False)
    try:
        with open(rm_path, encoding="utf-8") as fh:
            before = fh.read()
    except OSError:
        before = None
    changed = before != payload
    snap = next_snapshot(read_snapshot(state_dir), ident, changed, now=now)
    _atomic_write(rm_path, payload)
    _atomic_write(snap_path, json.dumps(snap, ensure_ascii=False))
    return snap


def frame_guard(snapshot, cabin=None, now=None, grace=STALE_GRACE_S):
    """命中前的兩道門(純函式)。回 None = 放行;否則回 {"action":"stale-frame","reason","message"}。

    snapshot 缺 ⇒ 放行(舊落點檔沒有快照;產品現在每幀都寫,缺只會是舊治具)。
    cabin 給了且快照的 cabin 也有值而不同 ⇒ 跨艙,不執行(規則 3:同名五碼是格式正確的錯身份)。
    changed_at 距 now 不到 grace ⇒ 你看到的是上一幀(規則 4 的點擊層)。
    """
    if not isinstance(snapshot, dict):
        return None
    snap_cabin = str(snapshot.get("cabin") or "")
    if cabin and snap_cabin and snap_cabin != str(cabin):
        return {"action": "stale-frame", "reason": "cross_cabin",
                "message": "這一幀是別的艙畫的(%s),不執行" % snap_cabin}
    try:
        changed_at = float(snapshot.get("changed_at") or 0.0)
    except (TypeError, ValueError):
        changed_at = 0.0
    now = float(time.time() if now is None else now)
    if changed_at and 0 <= (now - changed_at) < float(grace):
        return {"action": "stale-frame", "reason": "just_repainted",
                "message": "畫面剛重畫,請重看再點"}
    return None
