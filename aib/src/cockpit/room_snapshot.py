#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""空間快照:讓好友走進你的島與艦長室(開發文件第 34 節 v2:島的戶外 + 建築內部)。只用標準庫。

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


ISLAND_MAX = 64              # 島最大 64 × 64 格(@74e8b 11:59)
_KEY_RE = __import__("re").compile(r"^[a-z0-9_]{1,24}$")      # kind、res 的名稱(艦長室定義意思,核心只驗型別)
RES_MAX = 50


def island_path(env=None):
    import chat_roster as CR
    return CR._chat_home(env) / "island.json"


def _read_json(path):
    try:
        raw = path.read_text(encoding="utf-8")
        x = json.loads(raw) if len(raw.encode("utf-8")) <= RAW_MAX else {}
    except (OSError, ValueError):
        x = {}
    return scrub(x if isinstance(x, dict) else {}) or {}


def _int_in(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def clean_island(x):
    """island.json(艦長室寫)⇒ 驗過的島:size ≤ 64×64 格;建築 x,y 要在島內;島主只收名字與 emoji / 背景字(不收圖檔)。
    建築的 kind / lv / building{start,done}、島的 age / res{名稱: 數量} 由艦長室定義意思,核心只驗型別與範圍
    (kind 與名稱 [a-z0-9_]{1,24}、lv / age 0–99、數量非負整數、名稱最多 50 個),不合的欄位當沒有。"""
    size = x.get("size") if isinstance(x.get("size"), dict) else {}
    w = size.get("w") if _int_in(size.get("w"), 1, ISLAND_MAX) else 16
    h = size.get("h") if _int_in(size.get("h"), 1, ISLAND_MAX) else 16
    owner = x.get("owner") if isinstance(x.get("owner"), dict) else {}
    av = owner.get("avatar") if isinstance(owner.get("avatar"), dict) else {}
    out = {"name": x.get("name") if isinstance(x.get("name"), str) else None, "size": {"w": w, "h": h},
           "owner": {"name": owner.get("name") if isinstance(owner.get("name"), str) else None,
                     "avatar": {k: av.get(k) for k in ("emoji", "bg") if isinstance(av.get(k), str)}},
           "terrain": x.get("terrain") if isinstance(x.get("terrain"), (dict, list)) else None,
           "buildings": [], "decor": x.get("decor") if isinstance(x.get("decor"), list) else [],
           "age": x.get("age") if _int_in(x.get("age"), 0, 99) else None,
           "res": {k: v for k, v in list((x.get("res") if isinstance(x.get("res"), dict) else {}).items())[:RES_MAX]
                   if isinstance(k, str) and _KEY_RE.match(k) and _int_in(v, 0, 10 ** 12)}}
    for b in x.get("buildings") or []:
        if isinstance(b, dict) and isinstance(b.get("cabin"), str) and _int_in(b.get("x"), 0, w - 1) and _int_in(b.get("y"), 0, h - 1):
            row = {"cabin": b["cabin"], "x": b["x"], "y": b["y"],
                   "style": b.get("style") if isinstance(b.get("style"), str) else None,
                   "kind": b.get("kind") if isinstance(b.get("kind"), str) and _KEY_RE.match(b["kind"]) else None,
                   "lv": b.get("lv") if _int_in(b.get("lv"), 0, 99) else None}
            bd = b.get("building") if isinstance(b.get("building"), dict) else None
            if bd and _int_in(bd.get("start"), 0, 10 ** 11) and _int_in(bd.get("done"), 0, 10 ** 11):
                row["building"] = {"start": bd["start"], "done": bd["done"]}
            out["buildings"].append(row)
    return out


def build(env=None, now=None, with_rooms=False, with_status=False, with_state=True):
    """組自己的空間快照(第 34 節 v2):{v, island, people, rooms}。
    island = 島的戶外(island.json);people = 島上走動的人 = 公開席(席名、emoji、AI 家、角色、state、at);
    rooms 只在 room 等級才帶(room.json 的艦名 / 樓層 / Lv / layout + 核心補的席);now / step 只在在線等級 status 才帶。
    沒有公開席 ⇒ None。"""
    import chat_roster as CR
    import nostr_keys as NK
    import seat_now as SN
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
    pres = {r["seat"]: r for r in SP.presence([s["seat5"] for s in live], with_status=True, state_root=home)}
    people = []
    for s in live:
        p = pres.get(s["seat5"]) or {}
        rec = SN.read(s["seat5"], state_root=home) or {}
        people.append({"seat": s["seat5"], "cabin": s.get("cabin") or "main", "name": s.get("name"),
                       "emoji": _emoji_of(home, s.get("persona_uid")), "provider": s.get("provider"), "role": s.get("role"),
                       "state": p.get("state") if with_state else None,
                       "at": rec.get("at") if isinstance(rec.get("at"), int) and with_state else None})
    snap = {"v": 2, "island": clean_island(_read_json(island_path(env))), "people": people, "rooms": None}
    if with_rooms:
        mine = _read_json(room_path(env))
        extra = mine.get("seats") if isinstance(mine.get("seats"), dict) else {}
        seats = []
        for s, ppl in zip(live, people):
            e = extra.get(s["seat5"]) if isinstance(extra.get(s["seat5"]), dict) else {}
            p = pres.get(s["seat5"]) or {}
            seats.append(dict(ppl, now=p.get("now") if with_status else None, step=p.get("step") if with_status else None,
                              floor=e.get("floor"), lv=e.get("lv") if isinstance(e.get("lv"), int) else None,
                              stars=e.get("stars") if isinstance(e.get("stars"), int) else None))
        ship = dict(mine.get("ship")) if isinstance(mine.get("ship"), dict) else {}
        cap = ship.get("captain")
        if isinstance(cap, dict):          # @74e8b 11:09:艦長(真人)那一格 {seat, name, avatar};席要是公開的,不然不帶席碼
            cap = {k: cap.get(k) for k in ("seat", "name", "avatar") if isinstance(cap.get(k), str)}
            if cap.get("seat") not in {x["seat"] for x in seats}:
                cap.pop("seat", None)
            ship["captain"] = cap
        elif "captain" in ship:
            ship.pop("captain")
        snap["rooms"] = {"ship": ship, "cabins": mine.get("cabins") if isinstance(mine.get("cabins"), list) else [],
                         "seats": seats, "layout": mine.get("layout") if isinstance(mine.get("layout"), (dict, list)) else None,
                         "layout_omitted": False}
    return snap


def encode(snap):
    """快照 ⇒ 傳輸字串(zlib + base64);超過 WIRE_MAX 先丟 rooms.layout、再丟 island.decor,還超過回 None。"""
    def enc(x):
        return base64.b64encode(zlib.compress(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8"), 9)).decode("ascii")
    s = enc(snap)
    if len(s) <= WIRE_MAX:
        return s
    snap = json.loads(json.dumps(snap))
    if snap.get("rooms"):
        snap["rooms"]["layout"], snap["rooms"]["layout_omitted"] = None, True
        s = enc(snap)
        if len(s) <= WIRE_MAX:
            return s
    if snap.get("island"):
        snap["island"]["decor"], snap["island"]["decor_omitted"] = [], True
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
    if not isinstance(snap, dict) or snap.get("v") != 2 or not isinstance(snap.get("people"), list):
        return None
    return scrub(snap)
