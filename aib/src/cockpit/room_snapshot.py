#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""房間快照:讓好友走進你的艦長室(開發文件第 34 節)。只用標準庫。

- 艦長室寫 ~/.ez2-aib/chat/room.json(艦名 / 招牌 / 艦長一句話、樓層與風格、每席樓層與 Lv、裝潢 layout);
- 這裡加上核心自己知道的每個**公開席**(席名、人格 emoji、AI 家、角色、📌),不信 room.json 自報;
- 任何層級的 ctx / unread / ip / host / hostname / content / body / msg 一律濾掉,字串清控制字元與長度;
- 傳送前 zlib + base64,壓完 ≤ WIRE_MAX(NIP-44 明文 64 KB、gift wrap 再包一層);超過拿掉 layout 標 layout_omitted。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import zlib
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FORBIDDEN = {"ctx", "unread", "ip", "host", "hostname", "content", "body", "msg"}
RAW_MAX = 64 * 1024          # room.json / 收到的快照原文上限
WIRE_MAX = 40 * 1024         # 壓縮 + base64 後上限
STR_MAX = 200
DEPTH_MAX = 12


def room_path(env=None):
    import chat_roster as CR
    return CR._chat_home(env) / "room.json"


def scrub(x, depth=0):
    """遞迴拿掉禁止欄位、清字串;太深的整段丟掉。"""
    if depth > DEPTH_MAX:
        return None
    if isinstance(x, dict):
        return {str(k)[:64]: scrub(v, depth + 1) for k, v in x.items() if str(k).lower() not in FORBIDDEN}
    if isinstance(x, list):
        return [scrub(v, depth + 1) for v in x[:500]]
    if isinstance(x, str):
        return "".join(ch for ch in x if ord(ch) >= 32)[:STR_MAX]
    if isinstance(x, (int, float, bool)) or x is None:
        return x
    return None


def _emoji_of(home, persona_uid):
    try:
        cards = json.loads((Path(home) / "identity" / "cards.json").read_text(encoding="utf-8")).get("cards") or {}
    except (OSError, ValueError, AttributeError):
        return None
    e = (cards.get(persona_uid) or {}).get("emoji")
    return e if isinstance(e, str) and 0 < len(e) <= 8 else None


def build(env=None, now=None):
    """組自己的房間快照(還沒壓)。沒有公開席 ⇒ None。"""
    import chat_roster as CR
    import nostr_keys as NK
    import seat_presence as SP
    env = os.environ if env is None else env
    prof = CR.read_profile(env) or {}
    pub = set(prof.get("public") or [])
    m = NK.read_map(env)
    active = {e["seat5"] for e in m["seats"] if e.get("status") == "active"}
    home = env.get("EZ2_AIB_STATE_ROOT") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".ez2-aib")
    live = [s for s in NK.live_seats(env=env) if s["seat5"] in pub and s["seat5"] in active]
    if not live:
        return None
    try:
        raw = room_path(env).read_text(encoding="utf-8")
        mine = json.loads(raw) if len(raw.encode("utf-8")) <= RAW_MAX else {}
    except (OSError, ValueError):
        mine = {}
    mine = scrub(mine if isinstance(mine, dict) else {}) or {}
    extra = mine.get("seats") if isinstance(mine.get("seats"), dict) else {}
    pres = {r["seat"]: r for r in SP.presence([s["seat5"] for s in live], with_status=True, state_root=home)}
    seats = []
    for s in live:
        e = extra.get(s["seat5"]) if isinstance(extra.get(s["seat5"]), dict) else {}
        p = pres.get(s["seat5"]) or {}
        seats.append({"seat": s["seat5"], "cabin": s.get("cabin") or "main", "name": s.get("name"),
                      "emoji": _emoji_of(home, s.get("persona_uid")), "provider": s.get("provider"), "role": s.get("role"),
                      "state": p.get("state"), "now": p.get("now"), "step": p.get("step"),
                      "floor": e.get("floor"), "lv": e.get("lv") if isinstance(e.get("lv"), int) else None,
                      "stars": e.get("stars") if isinstance(e.get("stars"), int) else None})
    ship = dict(mine.get("ship")) if isinstance(mine.get("ship"), dict) else {}
    cap = ship.get("captain")
    if isinstance(cap, dict):          # @74e8b 11:09:艦長(真人)那一格 {seat, name, avatar};席要是公開的,不然不帶席碼
        cap = {k: cap.get(k) for k in ("seat", "name", "avatar") if isinstance(cap.get(k), str)}
        if cap.get("seat") not in {x["seat"] for x in seats}:
            cap.pop("seat", None)
        ship["captain"] = cap
    elif "captain" in ship:
        ship.pop("captain")
    snap = {"v": 1, "ship": ship,
            "cabins": mine.get("cabins") if isinstance(mine.get("cabins"), list) else [],
            "seats": seats, "layout": mine.get("layout") if isinstance(mine.get("layout"), (dict, list)) else None,
            "layout_omitted": False}
    return snap


def encode(snap):
    """快照 ⇒ 傳輸字串(zlib + base64);超過 WIRE_MAX 先拿掉 layout,還超過回 None。"""
    def enc(x):
        return base64.b64encode(zlib.compress(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8"), 9)).decode("ascii")
    s = enc(snap)
    if len(s) <= WIRE_MAX:
        return s
    snap = dict(snap, layout=None, layout_omitted=True)
    s = enc(snap)
    return s if len(s) <= WIRE_MAX else None


def decode(text):
    """收到的傳輸字串 ⇒ 驗過、清過的快照;不合回 None。"""
    try:
        raw = zlib.decompressobj().decompress(base64.b64decode(text, validate=True), RAW_MAX + 1)
        if len(raw) > RAW_MAX:
            return None
        snap = json.loads(raw.decode("utf-8"))
    except (ValueError, zlib.error, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(snap, dict) or snap.get("v") != 1 or not isinstance(snap.get("seats"), list):
        return None
    return scrub(snap)
