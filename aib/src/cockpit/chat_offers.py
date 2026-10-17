#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""好友之間的掛單(開發文件第 36.2 節)。只用標準庫;意思由艦長室定義,核心只管「驗、記帳、回覆」。

- 掛單在 island.json 的 offers(room_snapshot.clean_offers 驗);**n 是上架總量,上架後艦長室不改 n**,剩多少由這裡算:
  left = n − trades.jsonl 裡這個 id 已成交(賣方 ok)的數量。
- 同一主人只有一台掛單機(room_snapshot.offers_host);其他台收到買單回 not_host。
- 帳 <AIB_CHAT_HOME>/trades.jsonl(0600、只追加):同一 (role, nonce) 以最後一筆為準。核心不碰餘額。
- 買方:送出記 pending;EXPIRE_S 沒回 ⇒ expired;expired 之後才到的 ok 記 late(艦長室照常入帳)。
- 賣方:同一 nonce 只回同一結果(冪等);每位對方 RATE_S 內最多 RATE_N 筆,超過回一次 rate、之後靜默丟。
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
EXPIRE_S = float(os.environ.get("AIB_CHAT_OFFER_EXPIRE") or 600)
RATE_N, RATE_S = 10, 60
WHY = ("gone", "not_enough", "price_changed", "not_host", "rate", "bad")
QUEUE = "offer-out"          # <bridge root>/offer-out/<nonce>.json:CLI 寫、工人送(買單是主人層級,不走席位的 outbox)


def _home(env=None):
    import chat_roster as CR
    return CR._chat_home(env)


def trades_path(env=None):
    return _home(env) / "trades.jsonl"


@contextmanager
def _locked(env=None):
    d = _home(env)
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / "trades.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_trades(env=None):
    try:
        lines = trades_path(env).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("role") in ("buyer", "seller") and isinstance(r.get("nonce"), str):
            out.append(r)
    return out


def _append(env, rec):
    p = trades_path(env)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return rec


def latest(rows):
    """(role, nonce) ⇒ 最後一筆。"""
    out = {}
    for r in rows:
        out[(r["role"], r["nonce"])] = r
    return out


def sold(rows, oid):
    return sum(int(r.get("n") or 0) for r in latest(rows).values()
               if r["role"] == "seller" and r.get("id") == oid and r.get("status") == "ok")


def my_offers(env=None):
    """這台是掛單機才有掛單;不是 ⇒ []。"""
    import room_snapshot as RS
    if not RS.offers_host(env):
        return []
    return RS.clean_island(RS._read_json(RS.island_path(env), RS.SPACE_RAW_MAX)).get("offers") or []


def expire(env=None, now=None):
    """買方 pending 超過 EXPIRE_S ⇒ 記 expired。回這次記了幾筆。"""
    now = time.time() if now is None else now
    n = 0
    with _locked(env):
        for r in list(latest(read_trades(env)).values()):
            if r["role"] == "buyer" and r.get("status") == "pending" and now - int(r.get("at") or 0) >= EXPIRE_S:
                _append(env, dict(r, at=int(now), status="expired"))
                n += 1
    return n


def offer_list(env=None, now=None):
    """aib chat offer list --json:我的掛單(sold / left)+ 帳(每筆交易取最後狀態,新的在前)。"""
    expire(env, now)
    rows = read_trades(env)
    offers = [dict(o, sold=sold(rows, o["id"]), left=max(0, o["n"] - sold(rows, o["id"]))) for o in my_offers(env)]
    import room_snapshot as RS
    trades = sorted(latest(rows).values(), key=lambda r: -int(r.get("at") or 0))
    return {"v": 1, "host": RS.offers_host(env), "offers": offers, "trades": trades}


def _reply(rec, why=None):
    out = {"op": "ok" if rec.get("status") == "ok" else "reject", "id": rec.get("id"), "nonce": rec["nonce"],
           "n": rec.get("n"), "unit": rec.get("unit")}
    if out["op"] == "reject":
        out["why"] = why or rec.get("why") or "bad"
    else:
        out["item"] = rec.get("item")
    return out


def parse(content):
    """收到的訊息內文 ⇒ dict;不合回 None。"""
    try:
        m = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("op") not in ("buy", "ok", "reject") or not isinstance(m.get("nonce"), str) \
            or not NONCE_RE.match(m["nonce"]):
        return None
    return m


def seller_decide(env, peer, msg, now=None):
    """賣方收到買單 ⇒ 要回的訊息(dict);None = 靜默丟(限速第二次以後)。整段在帳本鎖底下(同時兩位搶最後一份不會超賣)。"""
    import room_snapshot as RS
    now = int(time.time() if now is None else now)
    with _locked(env):
        rows = read_trades(env)
        prev = latest(rows).get(("seller", msg["nonce"]))
        if prev and prev.get("peer") == peer:
            return _reply(prev)                                   # 冪等:同一 nonce 回同一結果
        recent = [r for r in rows if r["role"] == "seller" and r.get("peer") == peer and now - int(r.get("at") or 0) < RATE_S]
        base = {"at": now, "peer": peer, "role": "seller", "nonce": msg["nonce"], "id": msg.get("id"),
                "n": msg.get("n"), "unit": msg.get("unit")}
        if len(recent) >= RATE_N:
            if any(r.get("why") == "rate" for r in recent):
                return None
            return _reply(_append(env, dict(base, status="reject", why="rate")))
        n, unit = msg.get("n"), msg.get("unit")
        if not (RS._int_in(n, 1, 999) and RS._int_in(unit, 1, 10 ** 6) and isinstance(msg.get("id"), str)):
            return _reply(_append(env, dict(base, status="reject", why="bad")))
        if not RS.offers_host(env):
            return _reply(_append(env, dict(base, status="reject", why="not_host")))
        o = next((x for x in my_offers(env) if x["id"] == msg["id"]), None)
        left = o["n"] - sold(rows, o["id"]) if o else 0
        why = "gone" if not o or left <= 0 else "price_changed" if unit != o["unit"] else "not_enough" if n > left else None
        if why:
            return _reply(_append(env, dict(base, status="reject", why=why, item=o["item"] if o else None)))
        return _reply(_append(env, dict(base, status="ok", item=o["item"], total=n * unit)))


def buyer_new(env, root, peer, oid, n, unit, item=None, now=None):
    """買方下單:記 pending、放進 offer-out 給工人送。回帳那一筆。"""
    now = int(time.time() if now is None else now)
    rec = {"at": now, "peer": peer, "role": "buyer", "nonce": secrets.token_hex(8), "id": oid, "item": item,
           "n": n, "unit": unit, "total": n * unit, "status": "pending"}
    q = Path(root) / QUEUE
    q.mkdir(parents=True, exist_ok=True)
    with _locked(env):
        _append(env, rec)
    tmp = q / (rec["nonce"] + ".tmp")
    tmp.write_text(json.dumps({"to": peer, "at": now, "msg": {"op": "buy", "id": oid, "n": n, "unit": unit,
                                                                "nonce": rec["nonce"]}}), encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(q / (rec["nonce"] + ".json")))
    return rec


def buyer_reply(env, peer, msg, now=None):
    """買方收到回覆 ⇒ 更新帳:pending ⇒ ok / reject;expired ⇒ ok 記 late(艦長室照常入帳)、reject 不改。回新那筆或 None。"""
    now = int(time.time() if now is None else now)
    with _locked(env):
        prev = latest(read_trades(env)).get(("buyer", msg["nonce"]))
        if not prev or prev.get("peer") != peer or prev.get("status") not in ("pending", "expired"):
            return None
        if prev["status"] == "expired" and msg["op"] != "ok":
            return None
        status = ("late" if prev["status"] == "expired" else "ok") if msg["op"] == "ok" else "reject"
        rec = dict(prev, at=now, status=status, item=msg.get("item") or prev.get("item"))
        if status == "reject":
            rec["why"] = msg.get("why") if msg.get("why") in WHY else "bad"
        return _append(env, rec)


def queued(root):
    """offer-out 裡待送的買單 [(path, rec)];壞檔刪掉。"""
    out = []
    for p in sorted((Path(root) / QUEUE).glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and isinstance(rec.get("msg"), dict):
                out.append((p, rec))
                continue
        except (OSError, ValueError):
            pass
        p.unlink(missing_ok=True)
    return out
