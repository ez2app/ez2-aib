#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""全球來信的核心匯入器(開發文件第 8.1 節第 6 步、第 22.2 節;第 1 期 1.7 收件)。

轉接程式(venv)把解好、驗好、分好類的來信寫進 <bridge root>/inbox/<msgid>.json;這一支(只用標準庫)把它排進
收件席的 dm 佇列,然後刪檔。**身分規則只在核心**:sender_kind 由 dm_global_files.queue_fields 決定
(external_seat / owner_paired,永遠不是 human);未驗證一律當陌生;陌生 ⇒ 建立時就是待審(hold_human)、
優先級降一般 —— 不是先排隊再轉待審(那一刻排空器可能已領走貼進框)。
排好後寫一筆 delivery:queued(帶排第幾)或 held,轉接程式照它寄回條。
收件席不在(佇列名不合、席碼對不上)⇒ delivery refused、檔搬 bad/,不猜投到別席。
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import dm_global_files as GF  # noqa: E402
import dm_queue as DQ  # noqa: E402


def _refuse(root, msgid, path, why):
    try:
        GF.append_delivery(root, msgid, {"v": 1, "at": int(time.time()), "state": "refused"})
    except Exception:   # noqa: BLE001
        pass
    GF._move_bad(root, path)
    return {"msgid": msgid, "result": "refused", "why": why}


def import_once(root=None, env=None):
    """匯入 inbox 裡全部待處理的來信。回每一則的結果清單。"""
    root = Path(root) if root else GF.default_root(env)
    out = []
    for msgid, rec, path in GF.pending(root, "inbox"):
        try:
            if rec.get("class") == "paired":                     # @abb89 F3:撤銷之後才匯入的,一律降成陌生(待審)
                import chat_pair as CP
                if not CP.is_paired(rec.get("from_npub"), env):
                    rec = dict(rec, **{"class": "stranger", "verified": False})
            qf = GF.queue_fields(rec)
        except GF.ContractError as err:
            out.append(_refuse(root, msgid, path, "contract:%s" % err))
            continue
        seat = rec["to_seat"]
        if not DQ.SEAT_RE.fullmatch(seat):
            out.append(_refuse(root, msgid, path, "bad_seat"))
            continue
        g = {"addr": qf["sender_addr"], "shown": qf["shown_name"], "verified": qf["verified"],
             "same_name": qf["same_name"], "msgid": msgid, "country": qf["country"],
             "country_status": qf["country_status"], "tz_country": qf["tz_country"], "relation": qf["class"]}
        if qf["author"]:
            g["author"] = qf["author"]
        if isinstance(rec.get("sent_at"), int) and not isinstance(rec["sent_at"], bool):
            g["sent_at"] = rec["sent_at"]             # #303 r3 B-L5:寄件端寄出時間(補寄 = 原寄出時間);佇列 sent_at 仍是收到時間
        # 10-19 Andy 選 1:回「主人本人從艦長室寄的」那封 ⇒ 只進艦長室,不進 AI 框(建立時就待審、不叫 AI)。
        #   只防「自動塞給 AI」;同帳號 AI 讀得到佇列檔、刪得掉寄出帳,這不是防線
        human_reply = GF.sent_author(root, rec.get("reply_to")) == "human"
        if human_reply:
            g["human_reply"] = True
        if rec.get("echo") is True:
            g["echo_reply"] = True                     # 艦長室才看得到;AI 分組也排除(dm_triage)
        echo = rec.get("echo") is True                  # 10-25 #306 審 B-M1:回音小幫手的自動回覆 ⇒ 只進艦長室、不叫 AI
        kind = rec.get("kind") if rec.get("kind") in DQ.KINDS else None
        thread = rec.get("thread") if isinstance(rec.get("thread"), str) and DQ.THREAD_RE.fullmatch(rec["thread"]) else None
        try:
            item = DQ.enqueue(seat, rec["content"], qf["shown_name"], sender_kind=qf["sender_kind"],
                              priority=qf["priority"], env=env, sender_origin=qf["sender_origin"],
                              thread=thread, kind=kind, expires=rec.get("expires"), global_info=g,
                              status=DQ.HOLD_STATUS if human_reply or echo else qf["status"],
                              hold_why=DQ.GLOBAL_STRANGER_WHY if qf["status"] == DQ.HOLD_STATUS or not (human_reply or echo)
                              else (DQ.HUMAN_REPLY_WHY if human_reply else DQ.ECHO_REPLY_WHY))
        except (ValueError, OSError) as err:
            out.append(_refuse(root, msgid, path, "enqueue:%s" % type(err).__name__))
            continue
        if not item.get("global"):
            # 抬頭資料沒過佇列那一關(_global_or_none 整欄 None)⇒ 這則已排進去但會被當成未知寄件人;
            #   改待審讓人看,不讓它以「看起來正常」的樣子貼進框
            if item.get("status") != DQ.HOLD_STATUS:
                DQ.hold_item(seat, item["id"], DQ.GLOBAL_STRANGER_WHY, env=env)
        state = "held" if item.get("status") == DQ.HOLD_STATUS else "queued"
        # 10-21 回條語意:held 帶原因碼(只放碼;寄件端只拿來顯示,不拿來判放行 / 權限 / 過期)
        reason = None
        if state == "held":
            reason = {"human_reply": "human_reply", "echo_reply": "echo_reply"}.get(DQ.hold_why_key(item), "stranger")
        DQ.global_delivery(item, state, seat, env, reason=reason)
        if state == "queued":
            DQ.note_global_pending(env)        # 沒開佇列的艙,排空器看這個記號才去投(全球信不受授權限制)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        out.append({"msgid": msgid, "result": state, "seat": seat, "id": item["id"]})
    return out


if __name__ == "__main__":
    print(json.dumps(import_once(), ensure_ascii=False))
