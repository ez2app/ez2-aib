#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""seat_id 發號——全庫唯一發號函式(EG.3;a seat 主刀 / dev4 二審)。

## 病(艦外 23:31 派工包)

發號點 `aib` write_seat_record 的 _seed():`sha256(窗|開席秒)[:5]`,零檢查。
5 hex = 2^20 ≈ 1.05e6 個號;生日碰撞 1000 席 ≈ 37.9%。撞號不報錯,後果=
兩席共用 seat_id:seat_who 定址、人格綁定、rotation 帳全把兩席當一席——
**帳不會壞,帳會開始說謊**,而且沒有任何一端會發現。

## 方(艦長裁:重derive/報錯,絕不靜默共用)

發號前掃登記簿**全域**(主艙 `seats/` + `instances/*/seats/`)——seat_id
是跨艙鍵(seat_who 就是全域找的),只查本艙=留下跨艙撞號的半套修法。
候選撞號 → 加鹽(retry 計數)重算,確定性、可測;窮盡 max_tries →
raise SeatIdCollisionError——寧可不發號,也不共用。

★ retry=0 的公式與舊版逐位元組相同:既有席位重跑 _seed 時(檔壞掉重種)
  在無撞號情況下拿回同一個號,冪等語意不變。

## 誠實留痕

重derive 過的席,呼叫端應寫 `seat_id_retry=n` 進席位檔——撞過號是事實;
不留痕,下一個查「這席的號為何 ≠ sha256(窗|時戳)」的人會判定檔案被篡改。

## 掃描的極限(講清楚,不假裝沒有)

壞 JSON / 讀不到的檔=看不到的號,無法對其防撞;登記簿掃描與發號寫入
之間也沒有全域鎖。且要說精確(dev4 二審校正):sha256(wid|now)[:5] 對
**同窗號+同秒**是確定性相同——兩艙都有 @5 且同一秒開席、兩邊掃描都在
對方寫入前完成時,拿到的是**同一個號,必然**,不是運氣不好;這個角落
的撞率取決於「同窗號+同秒開席」的機率,不是 1/2^20。把艙別摻進 seed
可以修掉它,但會破壞上面那條「retry=0 與舊公式逐位元組相同」的冪等
紅線——真取捨,留給後續刀裁。本刀把 37.9% 的靜默共用降到「壞檔+
同窗同秒跨艙」的角落案例,且留有 seat_who 的 ambiguous 偵測兜底——
不是宣稱零碰撞。
"""
import hashlib
import json
import os

MAX_TRIES = 64


class SeatIdCollisionError(RuntimeError):
    """發號窮盡仍撞——絕不回傳 taken 中的號,寧可讓開席端看見錯誤。"""


def registry_root(seats_dir):
    """seats_dir → 登記簿根。認兩種正式形狀:
    `<root>/seats` 與 `<root>/instances/<N>/seats`(aib_seats_dir 的兩個出口)。
    其他自訂形狀(EZ2_AIB_INSTANCE_HOME 指去任意處)退回其上層。"""
    d = os.path.abspath(seats_dir)
    parent = os.path.dirname(d)
    gp = os.path.dirname(parent)
    if os.path.basename(gp) == "instances":
        return os.path.dirname(gp)
    return parent


def taken_seat_ids(seats_dir):
    """全域已用號集合(lowercase)。壞檔跳過(見模組首「掃描的極限」)。"""
    root = registry_root(seats_dir)
    dirs = [os.path.join(root, "seats")]
    inst = os.path.join(root, "instances")
    if os.path.isdir(inst):
        for n in sorted(os.listdir(inst)):
            dirs.append(os.path.join(inst, n, "seats"))
    if os.path.abspath(seats_dir) not in {os.path.abspath(x) for x in dirs}:
        dirs.append(seats_dir)      # 自訂形狀也至少要看自己這份
    taken = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            sid = str(((rec if isinstance(rec, dict) else {})
                       .get("seat_id")) or "").strip().lower()
            if sid:
                taken.add(sid)
    return taken


def derive_seat_id(wid, now, taken, max_tries=MAX_TRIES):
    """回 (seat_id, retry)。retry=0 公式=舊版逐位元組同(冪等);撞號 →
    salt=retry 重算;窮盡 → SeatIdCollisionError。絕不回 taken 中的號。"""
    for n in range(max_tries):
        base = ("%s|%d" % (wid, now) if n == 0
                else "%s|%d|retry%d" % (wid, now, n))
        sid = hashlib.sha256(base.encode()).hexdigest()[:5]
        if sid not in taken:
            return sid, n
    raise SeatIdCollisionError(
        "seat_id 發號窮盡 %d 次仍撞(wid=%s now=%d taken=%d 號)——"
        "不靜默共用,請人工查登記簿" % (max_tries, wid, now, len(taken)))
