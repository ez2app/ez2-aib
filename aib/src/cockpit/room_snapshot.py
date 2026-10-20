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
RAW_MAX = 64 * 1024          # room.json 原文上限
SPACE_RAW_MAX = 256 * 1024   # island.json / 收到的空間快照原文上限(包 K 最壞實測約 80 KB:200 棟帶 look + 500 擺設 + 64×64 地形)
RLE_MAX = 16 * 1024          # terrain.grid.rle 這一個字串可以比 STR_MAX 長(第 36.1 節)
WIRE_MAX = 40 * 1024         # 壓縮 + base64 後上限
STR_MAX = 200
DEPTH_MAX = 12


def room_path(env=None):
    import chat_roster as CR
    return CR._chat_home(env) / "room.json"


def scrub(x, depth=0, key=None):
    """遞迴拿掉禁止欄位、清字串;太深的整段丟掉。地形 rle 那一個字串上限是 RLE_MAX,其他 STR_MAX。"""
    if depth > DEPTH_MAX:
        return None
    if isinstance(x, dict):
        return {str(k)[:64]: scrub(v, depth + 1, str(k)) for k, v in x.items() if str(k).lower() not in FORBIDDEN}
    if isinstance(x, list):
        return [scrub(v, depth + 1) for v in x[:500]]
    if isinstance(x, str):
        return "".join(ch for ch in x if ord(ch) >= 32)[:RLE_MAX if key == "rle" else STR_MAX]
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
_KEY_RE = __import__("re").compile(r"^[a-z0-9_]{1,24}\Z")      # kind、res 的名稱(艦長室定義意思,核心只驗型別);\Z 不是 $:$ 會讓結尾帶換行的值過
_STYLE_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{0,23}\Z")  # 風格包 id 規則(@74e8b 14:50);島 style、look 的各欄共用
_HEX_RE = __import__("re").compile(r"^#[0-9a-fA-F]{6}\Z")
_OFFER_ID_RE = __import__("re").compile(r"^[0-9a-f]{8}\Z")
_RLE_RE = __import__("re").compile(r"([gswdrtfh])([1-9][0-9]{0,3})?")
RES_MAX = 50
BUILDINGS_MAX, DECOR_MAX, PETS_MAX, OFFERS_MAX = 200, 500, 24, 20   # 第 36.1 節


def offers_host_path(env=None):
    import chat_roster as CR
    return CR._chat_home(env) / "offers-host"


def offers_host(env=None):
    """這台是不是掛單機(第 36.2 節):同一主人只有一台掛單,其他台轉送島時不帶 offers、收到買單回 not_host。"""
    return offers_host_path(env).is_file()


def set_offers_host(on, env=None):
    p = offers_host_path(env)
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("1\n", encoding="utf-8")
    elif p.exists():
        p.unlink()
    return offers_host(env)


def has_k_fields(isl):
    """島帶了包 K 的欄位 ⇒ 快照要標 v3(舊版只認 v2,整份不收,不會存成截斷的半份;第 36.4 節)。"""
    t = isl.get("terrain") if isinstance(isl.get("terrain"), dict) else {}
    return bool(isl.get("pets") or isl.get("offers") or t.get("grid") or any("look" in b for b in isl.get("buildings") or []))


def island_path(env=None):
    import chat_roster as CR
    return CR._chat_home(env) / "island.json"


def _read_json(path, limit=RAW_MAX):
    try:
        raw = path.read_text(encoding="utf-8")
        x = json.loads(raw) if len(raw.encode("utf-8")) <= limit else {}
    except (OSError, ValueError):
        x = {}
    return scrub(x if isinstance(x, dict) else {}) or {}


def _int_in(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _id(v):
    return v if isinstance(v, str) and _STYLE_RE.match(v) else None


def clean_grid(g, w, h):
    """terrain.grid = {"v":1, "rle":…}:代碼 [gswdrtfh] + 可省略次數 1–4096,展開格數要剛好 w × h;不合回 None(艦長室退回 sea + seed)。"""
    if not isinstance(g, dict) or g.get("v") != 1 or not isinstance(g.get("rle"), str) or not 0 < len(g["rle"]) <= RLE_MAX:
        return None
    pos = cells = 0
    for m in _RLE_RE.finditer(g["rle"]):
        if m.start() != pos:
            return None
        n = int(m.group(2) or 1)
        if n > 4096:
            return None
        cells += n
        pos = m.end()
    return {"v": 1, "rle": g["rle"]} if pos == len(g["rle"]) and cells == w * h else None


def clean_look(x):
    """建築外觀 look(@570a9 17:20):base / wall / roof / sign 用 id 規則(不寫死列舉);c 三色只收 #rrggbb(存小寫)。
    不是物件或 base 不合 ⇒ None(艦長室退回該風格包的現成外觀)。招牌只收圖示 id,不收文字。"""
    if not isinstance(x, dict) or not _id(x.get("base")):
        return None
    out = {"base": x["base"]}
    for k in ("wall", "roof", "sign"):
        if _id(x.get(k)):
            out[k] = x[k]
    c = x.get("c") if isinstance(x.get("c"), dict) else {}
    out["c"] = {k: c[k].lower() for k in ("wall", "roof", "accent") if isinstance(c.get(k), str) and _HEX_RE.match(c[k])}
    return out


def clean_offers(v):
    """掛單(第 36.2 節):[{id 8 hex, item, n 1–999 上架總量, unit 1–1e6 單價, at}],最多 20 筆;不合的那筆當沒有、id 重複只留第一筆。"""
    out, seen = [], set()
    for o in (v if isinstance(v, list) else [])[:OFFERS_MAX]:
        if isinstance(o, dict) and isinstance(o.get("id"), str) and _OFFER_ID_RE.match(o["id"]) and o["id"] not in seen \
                and isinstance(o.get("item"), str) and _KEY_RE.match(o["item"]) and _int_in(o.get("n"), 1, 999) \
                and _int_in(o.get("unit"), 1, 10 ** 6):
            seen.add(o["id"])
            out.append({"id": o["id"], "item": o["item"], "n": o["n"], "unit": o["unit"],
                        "at": o.get("at") if _int_in(o.get("at"), 0, 10 ** 11) else None})
    return out


SKY_KEYS = ("season", "weather", "time", "fest", "realm", "src")


def clean_sky(v):
    """島的天色 sky = {season, weather, time, fest, realm, src}(艦長室定義意思,核心只驗型別):每項 [a-z0-9_]{1,24},
    不合的那項當沒有;只收這 6 個鍵,其他鍵(例如艦長本機的座標)一律不帶出去。全空 ⇒ None。"""
    if not isinstance(v, dict):
        return None
    out = {k: v[k] for k in SKY_KEYS if isinstance(v.get(k), str) and _KEY_RE.match(v[k])}
    return out or None


SEAT_LOOK_RE = __import__("re").compile(r"^av[0-9]{1,2}:[0-9a-z]{1,128}\Z")   # 席的外觀(紙娃娃;跟建築的 clean_look 不同):版本前綴 + 一欄一字;每個字代表什麼由艦長室定義


def clean_seat_look(v):
    """席的外觀字串(艦長室定義意思,核心只驗型別):合 SEAT_LOOK_RE 才帶,不合 ⇒ None。"""
    return v if isinstance(v, str) and SEAT_LOOK_RE.match(v) else None


USE_RE = __import__("re").compile(r"^[a-z][0-9]{1,4}\Z")   # 新辦公室(@74e8b 10-19):這席正站在哪個版面物件前(物件 id,例 m13)
SEAT_CODE_RE = __import__("re").compile(r"^[0-9a-z]{5}\Z")
CELL_RE = __import__("re").compile(r"^[a-z0-9-]{1,24}:[0-9]{1,3}:[0-9]{1,3}:(s[0-9]{1,3}|-)\Z")   # 區塊 key <世界>:<x>:<y>:<副本>
MEET_GROUPS_MAX, MEET_SIZE_MAX = 8, 16
DECOR_TYPE_RE = __import__("re").compile(r"^[a-z]{1,24}\Z")
DECOR_SSR_MAX, DECOR_SR_MAX = 12, 999
DECOR_TITLES = ("plant", "furn", "lamp", "wall", "drink", "desk", "fun")   # 收藏稱號(@74e8b 10-19),只准這 7 個


def clean_use(v):
    """版面物件 id:合 USE_RE 才帶;不帶原因、不帶數字(艦長室定義物件是什麼)。不合 ⇒ None。"""
    return v if isinstance(v, str) and USE_RE.match(v) else None


def clean_cell(v):
    """這席在哪一區:合 CELL_RE 且沒有副本號(「-」)才帶 —— 好友快照只放主人自己的島 / 辦公室;
    在廣場哪個副本只給要跟過去的朋友,不進快照。不合 ⇒ None。"""
    return v if isinstance(v, str) and CELL_RE.match(v) and v.endswith(":-") else None


def clean_decor(v):
    """收藏擺設摘要 {"ssr": [type…], "sr": int, "titles": [稱號…]}(@74e8b 10-19):ssr 小寫字母 1–24 字、去重後最多 12 個;
    sr 0–999 整數;ssr / sr 任何一處不合 ⇒ None(整個不帶)。titles 只准 DECOR_TITLES 裡的、去重;不合只丟 titles。"""
    if not isinstance(v, dict):
        return None
    ssr, sr = v.get("ssr"), v.get("sr")
    if not isinstance(ssr, list) or not all(isinstance(t, str) and DECOR_TYPE_RE.match(t) for t in ssr):
        return None
    ssr = list(dict.fromkeys(ssr))
    if len(ssr) > DECOR_SSR_MAX or type(sr) is not int or not 0 <= sr <= DECOR_SR_MAX:
        return None
    out = {"ssr": ssr, "sr": sr}
    titles = v.get("titles")
    if isinstance(titles, list) and titles and all(isinstance(t, str) and t in DECOR_TITLES for t in titles):
        out["titles"] = list(dict.fromkeys(titles))
    return out


def clean_meet(v, seat_codes):
    """正在開會的幾組 [[席碼, …], …]:只收 seat_codes 裡的(公開席)、每組去重後 ≥ 2 人;最多 8 組、每組 16 人。"""
    out = []
    for g in v if isinstance(v, list) else []:
        if not isinstance(g, list):
            continue
        grp = []
        for c in g:
            if isinstance(c, str) and SEAT_CODE_RE.match(c) and c in seat_codes and c not in grp:
                grp.append(c)
        if len(grp) >= 2:
            out.append(grp[:MEET_SIZE_MAX])
        if len(out) >= MEET_GROUPS_MAX:
            break
    return out


def attach_looks(people, room_seats):
    """room.json 的 seats[席碼].look(艦長室寫的)⇒ 合格的才放進 people[i]["look"];沒有 / 不合 ⇒ 不出現 look 鍵。"""
    room_seats = room_seats if isinstance(room_seats, dict) else {}
    for p in people:
        e = room_seats.get(p.get("seat"))
        look = clean_seat_look(e.get("look")) if isinstance(e, dict) else None
        if look:
            p["look"] = look
        else:
            p.pop("look", None)
    return people


def problems(x):
    """艦長室存檔前要知道的「不合格」(第 36.3 節 check):超過上限、地形格子不合。不合格的欄位送出時當沒有 / 只取前面。"""
    out = []
    for k, cap in (("buildings", BUILDINGS_MAX), ("decor", DECOR_MAX), ("pets", PETS_MAX), ("offers", OFFERS_MAX)):
        if isinstance(x.get(k), list) and len(x[k]) > cap:
            out.append({"field": k, "why": "too_many", "n": len(x[k]), "max": cap})
    t = x.get("terrain") if isinstance(x.get("terrain"), dict) else {}
    if "grid" in t:
        isl = clean_island(x)
        if clean_grid(t.get("grid"), isl["size"]["w"], isl["size"]["h"]) is None:
            out.append({"field": "terrain.grid", "why": "bad"})
    return out


def clean_island(x, with_offers=True):
    """island.json(艦長室寫)⇒ 驗過的島:size ≤ 64×64 格;建築 x,y 要在島內;島主只收名字與 emoji / 背景字(不收圖檔)。
    建築的 kind / lv / building{start,done}、島的 age / res{名稱: 數量} 由艦長室定義意思,核心只驗型別與範圍
    (kind 與名稱 [a-z0-9_]{1,24}、lv / age 0–99、數量非負整數、名稱最多 50 個),不合的欄位當沒有。
    島的 style = 風格包 id [a-z0-9][a-z0-9_-]{0,23};空、沒有或不合 ⇒ None(基本風格,收件端沒有那個包也退回基本)。
    包 K(第 36.1 節):pets ≤ 24、terrain.grid、建築 look、建築 ≤ 200 / 擺設 ≤ 500(超過只取前面)、offers(with_offers=False ⇒ 不帶)。
    sky(島的天色:季節、天氣、時間…)照 clean_sky 驗;不合或沒有 ⇒ 不帶,收件端自己算。"""
    size = x.get("size") if isinstance(x.get("size"), dict) else {}
    w = size.get("w") if _int_in(size.get("w"), 1, ISLAND_MAX) else 16
    h = size.get("h") if _int_in(size.get("h"), 1, ISLAND_MAX) else 16
    owner = x.get("owner") if isinstance(x.get("owner"), dict) else {}
    av = owner.get("avatar") if isinstance(owner.get("avatar"), dict) else {}
    out = {"name": x.get("name") if isinstance(x.get("name"), str) else None, "size": {"w": w, "h": h},
           "style": x.get("style") if isinstance(x.get("style"), str) and _STYLE_RE.match(x["style"]) else None,
           "owner": {"name": owner.get("name") if isinstance(owner.get("name"), str) else None,
                     "avatar": {k: av.get(k) for k in ("emoji", "bg") if isinstance(av.get(k), str)}},
           "terrain": x.get("terrain") if isinstance(x.get("terrain"), (dict, list)) else None,
           "buildings": [], "decor": x.get("decor")[:DECOR_MAX] if isinstance(x.get("decor"), list) else [],
           "age": x.get("age") if _int_in(x.get("age"), 0, 99) else None,
           "res": {k: v for k, v in list((x.get("res") if isinstance(x.get("res"), dict) else {}).items())[:RES_MAX]
                   if isinstance(k, str) and _KEY_RE.match(k) and _int_in(v, 0, 10 ** 12)},
           "pets": [{"kind": p["kind"], "lv": p["lv"]} for p in (x.get("pets") if isinstance(x.get("pets"), list) else [])[:PETS_MAX]
                    if isinstance(p, dict) and isinstance(p.get("kind"), str) and _KEY_RE.match(p["kind"]) and _int_in(p.get("lv"), 1, 99)]}
    if isinstance(out["terrain"], dict) and "grid" in out["terrain"]:
        g = clean_grid(out["terrain"]["grid"], w, h)
        out["terrain"] = {k: v for k, v in out["terrain"].items() if k != "grid"}
        if g:
            out["terrain"]["grid"] = g
    sky = clean_sky(x.get("sky"))
    if sky:
        out["sky"] = sky
    if with_offers:
        out["offers"] = clean_offers(x.get("offers"))
    bl = x.get("buildings") if isinstance(x.get("buildings"), list) else []
    for b in bl[:BUILDINGS_MAX]:
        if isinstance(b, dict) and isinstance(b.get("cabin"), str) and _int_in(b.get("x"), 0, w - 1) and _int_in(b.get("y"), 0, h - 1):
            row = {"cabin": b["cabin"], "x": b["x"], "y": b["y"],
                   "style": b.get("style") if isinstance(b.get("style"), str) else None,
                   "kind": b.get("kind") if isinstance(b.get("kind"), str) and _KEY_RE.match(b["kind"]) else None,
                   "lv": b.get("lv") if _int_in(b.get("lv"), 0, 99) else None}
            bd = b.get("building") if isinstance(b.get("building"), dict) else None
            if bd and _int_in(bd.get("start"), 0, 10 ** 11) and _int_in(bd.get("done"), 0, 10 ** 11):
                row["building"] = {"start": bd["start"], "done": bd["done"]}
            lk = clean_look(b.get("look"))
            if lk:
                row["look"] = lk
            out["buildings"].append(row)
    return out


def build(env=None, now=None, with_rooms=False, with_status=False, with_state=True, with_offers=None, island=None, room=None):
    """組自己的空間快照(第 34 節 v2):{v, island, people, rooms}。
    island = 島的戶外(island.json);people = 島上走動的人 = 公開席(席名、emoji、AI 家、角色、state、at);
    rooms 只在 room 等級才帶(room.json 的艦名 / 樓層 / Lv / layout + 核心補的席);now / step 只在在線等級 status 才帶。
    island / room 給了(dict)就用它代替讀檔(aib chat space check 量還沒存的檔)。沒有公開席 ⇒ None。"""
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
    mine = (scrub(room) or {}) if isinstance(room, dict) else _read_json(room_path(env))
    attach_looks(people, mine.get("seats") if isinstance(mine, dict) else None)   # 島上走動的人也穿得出自己的樣子(好友看得到)
    isl = clean_island(scrub(island) if isinstance(island, dict) else _read_json(island_path(env), SPACE_RAW_MAX),
                       with_offers=offers_host(env) if with_offers is None else with_offers)
    snap = {"v": 3 if has_k_fields(isl) else 2, "island": isl, "people": people, "rooms": None}
    if with_rooms:
        extra = mine.get("seats") if isinstance(mine.get("seats"), dict) else {}
        seats = []
        for s, ppl in zip(live, people):
            e = extra.get(s["seat5"]) if isinstance(extra.get(s["seat5"]), dict) else {}
            p = pres.get(s["seat5"]) or {}
            seats.append(dict(ppl, now=p.get("now") if with_status else None, step=p.get("step") if with_status else None,
                              floor=e.get("floor"), lv=e.get("lv") if isinstance(e.get("lv"), int) else None,
                              stars=e.get("stars") if isinstance(e.get("stars"), int) else None))
            if clean_use(e.get("use")):        # 新辦公室:只帶物件 id,沒在用就沒這鍵
                seats[-1]["use"] = e["use"]
            if clean_cell(e.get("cell")):      # 在哪一區(只限沒副本的島 / 辦公室區塊)
                seats[-1]["cell"] = e["cell"]
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
                         "layout_omitted": False,
                         "meet": clean_meet(mine.get("meet"), {x["seat"] for x in seats})}
        if clean_decor(mine.get("decor")):    # 收藏擺設摘要:不合就整個不帶
            snap["rooms"]["decor"] = clean_decor(mine.get("decor"))
    return snap


def _enc(x):
    return base64.b64encode(zlib.compress(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8"), 9)).decode("ascii")


def encode_info(snap):
    """快照 ⇒ (傳輸字串或 None, 完整那份的大小, 丟了什麼)。超過 WIRE_MAX 只丟 rooms.layout(標 layout_omitted);
    島主親手擺的 decor 不丟(@570a9 15:43);還超過 ⇒ (None, 大小, "too_big"),這一則不送,由呼叫端記 last_error。"""
    s = _enc(snap)
    full = len(s)
    if full <= WIRE_MAX:
        return s, full, None
    if snap.get("rooms") and snap["rooms"].get("layout") is not None:
        snap = json.loads(json.dumps(snap))
        snap["rooms"]["layout"], snap["rooms"]["layout_omitted"] = None, True
        s = _enc(snap)
        if len(s) <= WIRE_MAX:
            return s, full, "layout"
    return None, full, "too_big"


def encode(snap):
    return encode_info(snap)[0]


def decode(text):
    """收到的傳輸字串 ⇒ 驗過、清過的快照;不合回 None。"""
    try:
        raw = zlib.decompressobj().decompress(base64.b64decode(text, validate=True), SPACE_RAW_MAX + 1)
        if len(raw) > SPACE_RAW_MAX:
            return None
        snap = json.loads(raw.decode("utf-8"))
    except (ValueError, zlib.error, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(snap, dict) or snap.get("v") not in (2, 3) or not isinstance(snap.get("people"), list):
        return None
    snap = scrub(snap)
    for p in snap["people"]:                            # 收件端自己再驗一次外觀:不合就拿掉,不影響其他欄位
        if isinstance(p, dict) and "look" in p and not clean_seat_look(p["look"]):
            p.pop("look")
    if isinstance(snap.get("island"), dict):            # 收件端自己再驗一次島(第 36.4 節),不只靠艦長室
        omitted = snap["island"].get("decor_omitted") is True     # 0.1.16 以前的寄件端會丟 decor 並標記
        snap["island"] = clean_island(snap["island"])
        if omitted:
            snap["island"]["decor_omitted"] = True
    return snap
