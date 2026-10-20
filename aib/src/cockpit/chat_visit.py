#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開放艦長室給好友(第二波 #1,10-21;NOTE-member-ux A1 / A2)。只用標準庫。

會員痛點:互開艦長室要兩台各打一次 CLI、還要貼 npub(`aib chat space <npub> room` + `aib chat share <npub> status`)。
這一支把它合成一個動作,外加「參觀請求 / 同意」兩則主人層級的訊息,艦長室「世界」頁點好友就能:
  - `aib chat open <好友>`   = 一鍵開放:share → status、space → room,並送 op=open 告訴對方「我開放了艦長室給你」
  - `aib chat visit <好友>`  = 送參觀請求(op=request);對方那邊收件 ⇒ visits.json 記 pending,頁面顯示「他想來參觀」
  - `aib chat visit accept|reject <好友>` = 一鍵同意(= open)/ 拒絕(op=reject)
  - `aib chat visits --json` = 給頁面:收到的請求、我送出的請求、我開給誰、誰開給我(A2 那張「這位好友能看到什麼」)

訊息走 Nostr 主人層級私訊(aib-type=visit,同掛單那條 _send_owner_msg):body JSON {"op": request|open|reject, "nonce", "at"}。
收件只收驗得到名冊的好友 / 我的機器(轉接程式驗簽 + 這裡再驗一次好友關係);陌生、封鎖 ⇒ 靜默丟。
同一位好友的 request 一小時最多一則(多的靜默);open / reject 不限。

帳:<AIB_CHAT_HOME>/visits.json(0600)
  {"v":1, "in": {<主人>: {"at","nonce","status": pending|accepted|rejected}},
          "out": {<主人>: {"at","nonce","status": sent|opened|rejected}},
          "open_from": {<主人>: at}}      # 對方送 open 來 = 他開放了艦長室給我
待送:<bridge root>/visit-out/<nonce>.json {"to","at","msg"},轉接程式 push_visits 送;送不出留著重試,EXPIRE_S 丟。

同帳號威脅模型(GUIDE-same-user-ai-threat-model):這不是防線 —— 開放艦長室給好友的信任等級跟現有的
`aib chat space <npub> room` 一樣(本機 CLI 就能做,沒有本人證明);同帳號 AI 本來就能開。要讓「開放」只有本人能做,
得在這個入口接 seat_act.prove(Touch ID),列入下一版給 @abb89 裁。
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

V = 1
OPS = ("request", "open", "reject")
NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
QUEUE = "visit-out"
EXPIRE_S = float(os.environ.get("AIB_CHAT_VISIT_EXPIRE") or 86400)     # 對方睡了也等一天
REQUEST_EVERY_S = 3600
IN_STATES = ("pending", "accepted", "rejected")
OUT_STATES = ("sent", "opened", "rejected")


class VisitError(Exception):
    pass


def _home(env=None):
    import chat_roster as CR
    return CR._chat_home(env)


def path(env=None):
    return _home(env) / "visits.json"


@contextmanager
def _locked(env=None):
    d = _home(env)
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / "visits.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _empty():
    return {"v": V, "in": {}, "out": {}, "open_from": {}}


def read(env=None):
    try:
        rec = json.loads(path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(rec, dict) or rec.get("v") != V:
        return _empty()
    out = _empty()
    for k in ("in", "out", "open_from"):
        v = rec.get(k)
        if isinstance(v, dict):
            out[k] = v
    return out


def _save(rec, env=None):
    p = path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(p))


def _friend(owner, env):
    """好友或我的機器、沒封鎖 ⇒ 主人 npub;否則 None。"""
    import chat_roster as CR
    o = CR.friend_owner(owner or "")
    if not o or CR.is_blocked(o, env) or not (CR.is_friend(o, env) or CR.is_mine(o, env)):
        return None
    return o


def _enqueue(root, owner, op, nonce, now):
    q = Path(root) / QUEUE
    q.mkdir(parents=True, exist_ok=True)
    tmp = q / (nonce + ".tmp")
    tmp.write_text(json.dumps({"to": owner, "at": now, "msg": {"op": op, "nonce": nonce, "at": now}}), encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(q / (nonce + ".json")))


def queued(root):
    """visit-out 裡待送的 [(path, rec)];壞檔刪掉。"""
    out = []
    for p in sorted((Path(root) / QUEUE).glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and isinstance(rec.get("msg"), dict) and rec["msg"].get("op") in OPS:
                out.append((p, rec))
                continue
        except (OSError, ValueError):
            pass
        p.unlink(missing_ok=True)
    return out


def request(env, root, owner, now=None):
    """送參觀請求。回 out 那一筆。不是好友 ⇒ VisitError("not_friend")。"""
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o:
        raise VisitError("not_friend")
    nonce = secrets.token_hex(8)
    with _locked(env):
        rec = read(env)
        rec["out"][o] = {"at": now, "nonce": nonce, "status": "sent"}
        _save(rec, env)
    _enqueue(root, o, "request", nonce, now)
    return dict(rec["out"][o], owner=o)


def open_to(env, root, owner, now=None):
    """一鍵開放:share status + space room,並送 open 給對方;他有 pending 請求就一併標 accepted。回 {owner, share, space, answered}。"""
    import chat_roster as CR
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o:
        raise VisitError("not_friend")
    CR.set_share("status", o, env)
    CR.set_space("room", o, env)
    answered = False
    with _locked(env):
        rec = read(env)
        cur = rec["in"].get(o)
        nonce = None
        if isinstance(cur, dict) and cur.get("status") == "pending":
            cur["status"] = "accepted"
            cur["answered_at"] = now
            nonce = cur.get("nonce") if NONCE_RE.match(str(cur.get("nonce") or "")) else None
            answered = True
        _save(rec, env)
    _enqueue(root, o, "open", nonce or secrets.token_hex(8), now)
    return {"owner": o, "share": CR.share_level(o, env), "space": CR.space_level(o, env), "answered": answered}


def reject(env, root, owner, now=None):
    """拒絕對方的參觀請求(沒請求也能送,對方那邊只會把 sent 改成 rejected)。"""
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o:
        raise VisitError("not_friend")
    with _locked(env):
        rec = read(env)
        cur = rec["in"].get(o)
        nonce = None
        if isinstance(cur, dict) and cur.get("status") == "pending":
            cur["status"] = "rejected"
            cur["answered_at"] = now
            nonce = cur.get("nonce") if NONCE_RE.match(str(cur.get("nonce") or "")) else None
        _save(rec, env)
    _enqueue(root, o, "reject", nonce or secrets.token_hex(8), now)
    return {"owner": o, "status": "rejected"}


def parse(content):
    """對方送來的 body ⇒ {"op","nonce","at"} 或 None。"""
    try:
        m = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(m, dict) or m.get("op") not in OPS or not NONCE_RE.match(str(m.get("nonce") or "")):
        return None
    try:
        at = int(m.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    return {"op": m["op"], "nonce": m["nonce"], "at": at}


def on_incoming(env, owner, msg, now=None):
    """收到對方的 visit 訊息 ⇒ 記帳。回 "pending" / "opened" / "rejected" / "quiet"(限量)/ None(不是好友或壞訊息)。
    轉接程式已驗過簽與名冊;這裡再驗一次好友關係(刀測錨)。"""
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o or not isinstance(msg, dict) or msg.get("op") not in OPS:
        return None
    with _locked(env):
        rec = read(env)
        if msg["op"] == "request":
            cur = rec["in"].get(o)
            if isinstance(cur, dict) and cur.get("status") == "pending" and now - int(cur.get("at") or 0) < REQUEST_EVERY_S:
                return "quiet"
            rec["in"][o] = {"at": now, "nonce": msg["nonce"], "status": "pending"}
            _save(rec, env)
            return "pending"
        if msg["op"] == "open":
            rec["open_from"][o] = now
            cur = rec["out"].get(o)
            if isinstance(cur, dict) and cur.get("status") == "sent":
                cur["status"] = "opened"
                cur["answered_at"] = now
            _save(rec, env)
            return "opened"
        cur = rec["out"].get(o)
        if isinstance(cur, dict) and cur.get("status") == "sent":
            cur["status"] = "rejected"
            cur["answered_at"] = now
        _save(rec, env)
        return "rejected"


def payload(env=None, now=None):
    """aib chat visits --json:頁面用。open_to = 我對每位好友 / 我的機器實際開到哪(share + space,一張表)。"""
    import chat_roster as CR
    now = int(time.time() if now is None else now)
    rec = read(env)
    owners = [f["owner"] for f in CR.read_friends(env, "mine")] + [f["owner"] for f in CR.read_friends(env)]
    names = {f["owner"]: f.get("name") for f in CR.read_friends(env, "mine") + CR.read_friends(env)}
    open_to = {o: {"share": CR.share_level(o, env), "space": CR.space_level(o, env),
                   "room": CR.space_level(o, env) == "room"} for o in dict.fromkeys(owners)}
    incoming = [dict(v, owner=o, name=names.get(o)) for o, v in rec["in"].items() if isinstance(v, dict)]
    outgoing = [dict(v, owner=o, name=names.get(o)) for o, v in rec["out"].items() if isinstance(v, dict)]
    incoming.sort(key=lambda r: -int(r.get("at") or 0))
    outgoing.sort(key=lambda r: -int(r.get("at") or 0))
    return {"v": V, "at": now, "incoming": incoming, "outgoing": outgoing, "open_to": open_to,
            "open_from": {o: int(t) for o, t in rec["open_from"].items() if o in open_to},
            "pending": sum(1 for r in incoming if r.get("status") == "pending")}
