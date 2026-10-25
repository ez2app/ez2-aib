#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審區一個指令收 / 結(Andy 10-25 20:1x 裁示 A + B,@abb89 派工):

  take  --id <q_>  [--wait "等誰 / 等什麼"]  印全文(長訊直接印落檔原文)、標已讀、記下這次看到的 rev
  close --id <q_>  --evidence <PR 網址 / commit / 檔 / 自己寄出的回信 q_ id> | --why "<理由>"
  wait  --id <q_>  "等誰 / 等什麼"            進行中的信補寫 / 改寫在等什麼(空字串 = 清掉)

底線(跟 read / done 同一套,這裡只是包起來):
- 狀態一律走 dm_queue._transition(TRANSITIONS、收件席本人、全球陌生信不准讀、evidence 都照舊查);
  read → done 分兩筆寫,各自比 rev(自查現值,不用人抄)。
- 長訊要讀到全文才算讀:落檔讀不到 ⇒ 不標已讀(relay_unreadable)。
- take 記下當時的 rev;close 時信若在那之後被改過(rev 不同)⇒ 拒,請重新 take。
- 沒 evidence / why 不准結;不會刪信;hold_human 照 read 的規則(收件席本人可讀,不代人放行)。
"""
from __future__ import annotations

import json
import os
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dm_queue as Q  # noqa: E402
import dm_relay as DR  # noqa: E402

UNREAD = ("queued", "delivered", Q.HOLD_STATUS)
CLOSED = ("done", "archived", "deleted")


def _text_of(item, env=None):
    """(全文, 落檔路徑或 None);落檔讀不到 ⇒ (None, path)。
    #380 r1 M4:路徑只認長訊目錄裡的 RELAY-dm-*.txt(dm_relay.relay_file_ok,#370 那道);
    不然全球 / 別的通訊軟體寄件人寫「長訊已落檔(…),請讀:<任意檔>」就能讓收件席把本機檔讀進對話。"""
    msg = item.get("msg") or ""
    path = DR.relay_path_of(msg)
    if not path:
        return msg, None
    if not DR.relay_file_ok(path, env):
        return None, path
    return Q._onboard_relay_text(path), path


def _summary(item, text, relay_path):
    return {"id": item["id"], "rev": Q.item_rev(item), "status": item.get("status"),
            "from": item.get("sender_label") or "未知", "kind": Q.normalize_kind(item.get("kind")),
            "when": item.get("when") or Q.taipei_stamp(item.get("ts")), "relay_path": relay_path,
            "wait": (item.get("wait") or {}).get("text") if isinstance(item.get("wait"), dict) else None,
            "chars": len(text) if isinstance(text, str) else None}


def _note(seat, item_id, env, now, **fields):
    """在信檔記收件席自己的註記(taken_rev / touched_at / wait);不加 rev(rev 是寄件端內容的修訂號)。"""
    it = Q._load_item(seat, item_id, env)
    if not it:
        return None
    it.update(fields)
    Q._write_item(seat, it, env)
    return it


_INVISIBLE = {0x200b, 0x200c, 0x200d, 0x200e, 0x200f, 0x2060, 0xfeff} | set(range(0x202a, 0x202f)) | set(range(0x2066, 0x206a))


def _clean_wait(text):
    """等什麼:剝控制字元 / 零寬 / 方向控制字元(#380 r1 L4:會被貼進提醒行與交接事實),空白壓成一格、限長。"""
    s = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c
                for c in str(text or "") if ord(c) not in _INVISIBLE and unicodedata.category(c) != "Cf")
    return " ".join(s.split())[:Q.WAIT_MAX_CHARS]


def take(seat, item_id, actor, wait=None, env=None, now=None):
    now = int(time.time() if now is None else now)
    cls, ident = Q.parse_actor(actor)
    if cls != "seat" or ident != seat:       # #380 r1 M3:已讀信不經 mark_read,這裡要自己查是收件席本人
        return {"ok": False, "reason": "actor", "message": Q.TRANSITION_REASON["actor"]}
    item = Q._load_item(seat, item_id, env)
    if not item:
        return {"ok": False, "reason": "not_found", "message": Q.TRANSITION_REASON["not_found"]}
    text, relay_path = _text_of(item, env)
    if text is None:
        return {"ok": False, "reason": "relay_unreadable", "relay_path": relay_path,
                "message": "長訊落檔讀不到(不存在 / 不是一般檔 / 太大),沒有標已讀"}
    seen = Q.item_rev(item)
    status = item.get("status")
    if status in UNREAD:
        r = Q.mark_read(seat, item_id, actor, env=env, now=now, if_rev=seen, no_rev_check=False)
        if not r.get("ok"):
            return {k: v for k, v in r.items() if k != "item"}
    elif status in CLOSED:
        out = _summary(item, text, relay_path)
        out.update(ok=True, reason="already_closed", text=text)
        return out
    elif status != "read":
        return {"ok": False, "reason": "transition", "status": status, "message": Q.TRANSITION_REASON["transition"]}
    fields = {"taken_rev": seen, "touched_at": now}
    if wait is not None and _clean_wait(wait):
        fields["wait"] = {"text": _clean_wait(wait), "at": now}
    cur = Q._load_item(seat, item_id, env) or item
    if cur.get("status") == "read":          # 讀完就被系統備存的知會信不再寫註記
        cur = _note(seat, item_id, env, now, **fields) or cur
    out = _summary(cur, text, relay_path)
    out.update(ok=True, reason="ok", text=text)
    if cur.get("status") == "read":
        out["next"] = {"close": "close --id %s --evidence <PR 網址 / commit / 檔 / 回信 q_ id> 或 --why <理由>" % item_id}
        if not Q.is_dispatch(cur):   # 派工信:回信不算做完
            out["next"]["reply_and_close"] = "aib dm <位址> --close %s \"…\"(回信送達才結這封)" % item_id
        if not out.get("wait"):
            out["next"]["wait"] = "還沒做完 ⇒ wait --id %s \"等誰 / 等什麼\"(超過 4 小時沒寫會被提醒)" % item_id
    return out


def close(seat, item_id, actor, evidence=None, why=None, env=None, now=None):
    now = int(time.time() if now is None else now)
    if not (evidence or (isinstance(why, str) and why.strip())):
        return {"ok": False, "reason": "evidence", "message": Q.TRANSITION_REASON["evidence"]}
    item = Q._load_item(seat, item_id, env)
    if not item:
        return {"ok": False, "reason": "not_found", "message": Q.TRANSITION_REASON["not_found"]}
    cur = Q.item_rev(item)
    status = item.get("status")
    text, relay_path = _text_of(item, env)
    if status in UNREAD:
        if text is None:          # 沒讀到全文不能算讀過,更不能結
            return {"ok": False, "reason": "relay_unreadable", "relay_path": relay_path,
                    "message": "長訊落檔讀不到,沒有標已讀也沒有結"}
        r = Q.mark_read(seat, item_id, actor, env=env, now=now, if_rev=cur, no_rev_check=False)
        if not r.get("ok"):
            return {k: v for k, v in r.items() if k != "item"}
    elif status == "read":
        taken = item.get("taken_rev")
        if taken is not None and int(taken) != cur:
            return {"ok": False, "reason": "rev_mismatch", "rev": cur, "taken_rev": taken,
                    "message": "這封在你 take 之後被改過(rev %s → %s),請先重新 take 看新內容" % (taken, cur)}
    else:
        return {"ok": False, "reason": "transition", "status": status, "message": Q.TRANSITION_REASON["transition"]}
    if (Q._load_item(seat, item_id, env) or {}).get("status") != "read":
        return {"ok": True, "reason": "already_closed", "id": item_id,
                "message": "讀過就被系統備存了(知會信),不用再結"}
    r = Q.mark_done(seat, item_id, actor, evidence, env=env, now=now, if_rev=cur, no_rev_check=False, why=why)
    out = {k: v for k, v in r.items() if k != "item"}
    out["id"] = item_id
    if status in UNREAD and r.get("ok"):
        out["text"] = text                  # 一步讀 + 結:全文照樣印出來(讀全文才算讀)
        out["relay_path"] = relay_path
    return out


def set_wait(seat, item_id, actor, text, env=None, now=None):
    now = int(time.time() if now is None else now)
    cls, ident = Q.parse_actor(actor)
    # 0.1.61:整理子代理(agent:<收件席>/<id>)也能替留下的信寫等什麼(標記不是防線,同 _transition)
    if not ((cls == "seat" and ident == seat) or (cls == "agent" and ident.split("/", 1)[0] == seat)):
        return {"ok": False, "reason": "actor", "message": Q.TRANSITION_REASON["actor"]}
    item = Q._load_item(seat, item_id, env)
    if not item:
        return {"ok": False, "reason": "not_found", "message": Q.TRANSITION_REASON["not_found"]}
    if item.get("status") != "read":
        return {"ok": False, "reason": "transition", "status": item.get("status"),
                "message": "只有進行中(已讀未結)的信能寫等什麼"}
    t = _clean_wait(text)
    _note(seat, item_id, env, now, wait=({"text": t, "at": now} if t else None), touched_at=now)
    Q._audit(seat, env, ts=now, id=item_id, to="wait", actor=actor, ok=True, reason="ok", wait=t or None)
    return {"ok": True, "reason": "ok", "id": item_id, "wait": t or None}


def emit(out, stream=None):
    """第一行 JSON(不含全文,方便 head -1 / 程式讀);有全文 ⇒ 接一段原文給人和 AI 看。"""
    stream = stream or sys.stdout
    text = out.get("text")
    head = {k: v for k, v in out.items() if k != "text"}
    stream.write(json.dumps(head, ensure_ascii=False) + "\n")
    if isinstance(text, str):
        stream.write("----- 全文 -----\n" + text.rstrip("\n") + "\n----- 完 -----\n")
