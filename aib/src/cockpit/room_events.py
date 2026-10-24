#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長室事件(第二波 #6,10-21):待審區收到「給本人的信」⇒ 記一筆事件,給艦長室 BB Call / 角標用。

哪些算「給本人的信」:佇列建立時就是待審、而且原因是
  - human_reply:回「主人本人從艦長室寄的」那封的線上來信(10-19 Andy 選 1,只在艦長室看)
  - im_room:Telegram 來信(第 39 節第一步,只在艦長室看)
陌生來信的待審(global_stranger)**不算**:那是等主人放行給 AI 的,不是寫給本人的信。

記什麼:只記「有一封、誰寄的(通訊錄鍵)、在哪一席的佇列、佇列 id、原因」。**不記內容、不記對方自取的名字**
(名字是注入面;艦長室照 contact_key 查通訊錄自己顯示)。
另一種(10-23):aib_updated —— aib 自動更新完成(auto_update.py),只記 from / to 兩個版號,給 BB Call 說「aib 已自動更新到 X」。
檔:<狀態根>/state/room-events.jsonl,只追加,id 從 1 遞增;`aib chat events [--since <id>]` 讀。
事件記不進去(磁碟滿、鎖拿不到)不擋收信:enqueue 那邊包著 try,信照樣進待審。

這不是防線:同帳號 AI 讀得到、刪得掉這個檔(GUIDE-same-user-ai-threat-model);它只是給艦長室的通知,
信本身還在佇列裡,事件丟了角標照 `pending`(直接數佇列)還是對的。
"""
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

V = 1
KIND_HUMAN_MAIL = "human_mail"
KIND_AIB_UPDATED = "aib_updated"         # 10-23 自動更新(auto_update.py):只帶版號 from / to,BB Call 顯示「aib 已自動更新到 X」
KINDS = (KIND_HUMAN_MAIL, KIND_AIB_UPDATED)
WHYS = ("human_reply", "im_room")        # 對應 dm_queue.HOLD_WHY 的鍵
FILE = "room-events.jsonl"
KEEP = 2000                               # 讀的時候最多看最後這麼多筆(檔只追加;舊的留著當稽核)
READ_MAX = 500


def path(env=None):
    import dm_queue as DQ
    return DQ.state_root(env) / "state" / FILE


def mail_why(item):
    """佇列項是不是「給本人的信」:回 human_reply / im_room,不是回 None。"""
    import dm_queue as DQ
    if not isinstance(item, dict) or item.get("status") != DQ.HOLD_STATUS:
        return None
    why = item.get("hold_why")
    for key in WHYS:
        if why == DQ.HOLD_WHY[key]:
            return key
    return None


def contact_of(item):
    """(net, contact_key, relation):全球信 ⇒ ("nostr", "global:<主人 npub>", relation);Telegram ⇒ ("telegram", "telegram:<id>", relation)。
    認不出 ⇒ (None, None, None)(事件照記,艦長室顯示「未知寄件人」)。"""
    g, im = item.get("global"), item.get("im")
    if isinstance(g, dict) and g.get("addr"):
        import dm_global_files as GF
        owner = (GF.parse_address(str(g["addr"])) or {}).get("owner")
        if owner:
            return "nostr", "global:" + owner, g.get("relation")
    if isinstance(im, dict) and im.get("net") and im.get("id"):
        return str(im["net"]), "%s:%s" % (im["net"], im["id"]), im.get("relation")
    return None, None, None


def _parse(ln):
    """一行 ⇒ (事件, id);壞行、不是我們的事件、id 不是正整數 ⇒ (None, 0)。讀與配號用同一把尺。"""
    try:
        ev = json.loads(ln)
        eid = int(ev.get("id") or 0)
    except (ValueError, TypeError, AttributeError):
        return None, 0
    if not isinstance(ev, dict) or ev.get("kind") not in KINDS or eid <= 0:
        return None, 0
    return ev, eid


def _last_id(fh):
    fh.seek(0)
    last = 0
    for ln in fh:
        last = max(last, _parse(ln)[1])
    return last


def note_human_mail(seat, item, env=None, now=None):
    """佇列建立了一封「給本人的信」⇒ 追加一筆事件;不是這種信 ⇒ 不記、回 None。回記下的事件。"""
    why = mail_why(item)
    if why is None:
        return None
    net, key, rel = contact_of(item)
    now = int(time.time() if now is None else now)
    ev = {"v": V, "id": 0, "at": now, "kind": KIND_HUMAN_MAIL, "seat": seat, "queue_id": item.get("id"),
          "why": why, "net": net, "contact_key": key, "relation": rel}
    return _append(ev, env)


def _append(ev, env=None):
    """配號並追加一筆(同一把鎖);回記下的事件。"""
    p = path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            ev["id"] = _last_id(fh) + 1
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    return ev


VERSION_OK = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")


def note_aib_updated(old, new, env=None, now=None):
    """aib 自動更新完成(auto_update.check)⇒ 追加一筆 aib_updated;只記版號(不像版號的字不記)。回事件或 None。"""
    old, new = str(old or ""), str(new or "")
    if not VERSION_OK.fullmatch(new) or (old and not VERSION_OK.fullmatch(old)):
        return None
    now = int(time.time() if now is None else now)
    return _append({"v": V, "id": 0, "at": now, "kind": KIND_AIB_UPDATED, "from": old or None, "to": new}, env)


def _since(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def read(env=None, since=0, limit=READ_MAX):
    """id > since 的事件,舊在前,最多 limit 筆(超過就給最新的那 limit 筆)。壞行跳過。"""
    since = _since(since)
    limit = max(1, min(int(limit or READ_MAX), READ_MAX))
    try:
        lines = path(env).read_text(encoding="utf-8").splitlines()[-KEEP:]
    except OSError:
        return [], 0
    rows, last = [], 0
    for ln in lines:
        ev, eid = _parse(ln)
        if ev is None:
            continue
        last = max(last, eid)
        if eid > since:
            rows.append(ev)
    return rows[-limit:], last


def pending_count(env=None, now=None, seat=None):
    """現在還躺在待審區、沒被本人處理掉的「給本人的信」有幾封(角標用;直接數佇列,不靠事件檔)。
    seat 給了只數那一席。"""
    import dm_queue as DQ
    now = time.time() if now is None else now
    root = DQ.queue_root(env)
    seats = [seat] if seat else []
    if not seats:
        try:
            seats = sorted(p.name for p in root.iterdir() if p.is_dir() and DQ.SEAT_RE.fullmatch(p.name))
        except OSError:
            seats = []
    n = 0
    for s in seats:
        for it in DQ._all_items(s, env=env):
            if mail_why(it) and not it.get("expired") and not DQ._expired(it, now):
                n += 1
    return n


def payload(env=None, since=0, limit=READ_MAX, now=None):
    """aib chat events --json:{"v", "at", "since", "last_id", "pending", "events": [...]}。
    頁面記住 last_id,下次帶 --since;events 空、pending > 0 = 以前就有信還沒看。"""
    now = int(time.time() if now is None else now)
    since = _since(since)
    rows, last = read(env, since=since, limit=limit)
    return {"v": V, "at": now, "since": since, "last_id": last, "pending": pending_count(env, now),
            "events": rows}
