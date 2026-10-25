#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理子代理的核心支援(Andy 10-25 21:2x / 21:4x 裁示,@abb89 派工,排 0.1.61)。

規則:席位待審 ≥2 或進行中 ≥2(不算 hold_human、不算已寫「等什麼」的;門檻與算法照 dm_triage / #374)⇒ 開 1 個整理子代理;
子代理只結案 + 擬稿,寄信 / 刪信 / 合併 / 發版 / 簽章一律不准。

  plan(seat)  唯讀:把待審 + 進行中的信分組(PR 號 / thread / 寄件人),每封標建議
              close_notice     通知類(kind=notice 或 dm_triage 的通知表)
              close_superseded 同寄件鍵、同一批 PR 已有較新一輪(跟新一輪自動結同一個判準)
              human            其餘;正文帶 PR 網址的附 evidence_hint(只提示,要查證才結)
              hold_human 一律 human、不建議動。

底線:本檔不改任何信的狀態;信件內容是資料,字句不算授權。
"""
from __future__ import annotations

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dm_queue as Q  # noqa: E402
import dm_triage as TR  # noqa: E402

# 門檻與「哪些算」只有一份:dm_triage(#374)的 TRIAGE_MIN_UNREAD(未讀 > 它)/ INPROG_MIN(進行中 ≥ 它)、
# unread_ids / inprog_ids(排除 hold_human、已寫等什麼、線上回音、別的通訊軟體來信);本檔只拿來用。
WAITING_STATUSES = ("queued", "claimed", "delivered", Q.HOLD_STATUS)
LIVE_STATUSES = WAITING_STATUSES + ("read",)
_PR_URL_RE = re.compile(r"https://github\.com/ez2app/[A-Za-z0-9_.-]+/pull/\d+")


def _has_wait(item):
    w = item.get("wait")
    return isinstance(w, dict) and bool(str(w.get("text") or "").strip())


def _group_key(item, prs):
    if prs:
        return "pr:#%s" % prs[0]
    if item.get("thread"):
        return "thread:%s" % item["thread"]
    return "from:%s" % (item.get("sender_label") or "未知")


def plan(seat, env=None, now=None):
    now = int(time.time() if now is None else now)
    live = [it for it in Q._all_items(seat, env=env) if it.get("status") in LIVE_STATUSES]
    texts = {it["id"]: Q._full_text(it, env) for it in live}
    keys = {it["id"]: Q._sender_key(it) for it in live}
    rounds = {it["id"]: Q._rounds(texts[it["id"]] or "") for it in live}
    rows, groups = [], {}
    proj = [Q._row(it, seat, now) for it in live]
    unread = set(TR.unread_ids(proj))
    inprog = set(TR.inprog_ids(proj))
    counts = {"waiting": 0, "in_progress": 0, "countable_waiting": 0, "countable_in_progress": 0,
              "hold_human": 0, "with_wait": 0}
    for it in sorted(live, key=lambda x: (int(x.get("ts") or 0), x["id"])):
        st = it.get("status")
        text = texts[it["id"]]
        body = text if isinstance(text, str) else (it.get("msg") or "")
        prs = sorted(set(Q._PR_NUM_RE.findall(body)), key=int)
        row = {"id": it["id"], "status": st, "kind": Q.normalize_kind(it.get("kind")),
               "from": it.get("sender_label") or "未知", "when": it.get("when") or Q.taipei_stamp(it.get("ts")),
               "prs": ["#" + p for p in prs], "thread": it.get("thread"), "rev": Q.item_rev(it),
               "relay_unreadable": text is None}
        if _has_wait(it):
            row["wait"] = str(it["wait"]["text"])
        if st == "read":
            counts["in_progress"] += 1
        else:
            counts["waiting"] += 1
        if st == Q.HOLD_STATUS:
            counts["hold_human"] += 1
            row["excluded"] = "hold_human"
        elif _has_wait(it):
            counts["with_wait"] += 1
            row["excluded"] = "waiting_on"
        elif it["id"] in (inprog if st == "read" else unread):
            counts["countable_in_progress" if st == "read" else "countable_waiting"] += 1
        else:
            row["excluded"] = "not_for_ai"          # 線上回音 / 別的通訊軟體來信 / 過期:只給艦長室
        newer = next((o["id"] for o in live if o["id"] != it["id"] and o.get("status") != Q.HOLD_STATUS
                      and Q.is_older_round(it, keys[o["id"]], rounds[o["id"]], env)), None)
        if st == Q.HOLD_STATUS:                 # 等真人審的信:不建議任何自動動作
            row["suggest"] = "human"
        elif row["kind"] == "notice" or TR.is_notice(it.get("msg")):
            row["suggest"] = "close_notice"
        elif newer:
            row["suggest"], row["superseded_by"] = "close_superseded", newer
        else:
            row["suggest"] = "human"
            urls = sorted(set(_PR_URL_RE.findall(body)))
            if urls:
                row["evidence_hint"] = urls
        rows.append(row)
        groups.setdefault(_group_key(it, prs), []).append(it["id"])
    over = counts["countable_waiting"] > TR.TRIAGE_MIN_UNREAD or counts["countable_in_progress"] >= TR.INPROG_MIN
    return {"ok": True, "seat": seat, "at": now,
            "threshold": {"waiting_gt": TR.TRIAGE_MIN_UNREAD, "in_progress_ge": TR.INPROG_MIN}, "over_threshold": over,
            "counts": counts, "groups": [{"key": k, "ids": v} for k, v in groups.items()], "rows": rows}


def consolidate(seat, ids, file_path, actor, env=None, now=None):
    """同類幾封合成一份「重新整理」(pending/DMTRIAGE-<席碼>-<時間>.md 之類的檔):每封 → done,evidence 指那份檔。
    原信不刪(可用 reopen 回復);hold_human / 已結的跳過並回原因;每封各自比 rev(自查現值)。"""
    now = int(time.time() if now is None else now)
    if Q.evidence_kind(seat, file_path, env) != "file":
        return {"ok": False, "reason": "evidence", "message": "整理檔要是存在的一般檔:%s" % file_path}
    out = []
    for item_id in ids:
        it = Q._load_item(seat, item_id, env)
        if not it:
            out.append({"id": item_id, "ok": False, "reason": "not_found"})
            continue
        st = it.get("status")
        if st not in ("queued", "delivered", "read"):
            out.append({"id": item_id, "ok": False, "reason": "skipped", "status": st})
            continue
        if st != "read":
            r = Q.mark_read(seat, item_id, actor, env=env, now=now, if_rev=Q.item_rev(it), no_rev_check=False)
            if not r.get("ok"):
                out.append({"id": item_id, "ok": False, "reason": r.get("reason")})
                continue
        cur = Q._load_item(seat, item_id, env) or it
        if cur.get("status") != "read":            # 知會信讀過就被系統備存了
            out.append({"id": item_id, "ok": True, "reason": "already_closed"})
            continue
        r = Q.mark_done(seat, item_id, actor, file_path, env=env, now=now, if_rev=Q.item_rev(cur), no_rev_check=False)
        if r.get("ok"):
            done = Q._load_item(seat, item_id, env)
            done["consolidated_into"] = str(file_path)
            Q._write_item(seat, done, env)
        out.append({"id": item_id, "ok": bool(r.get("ok")), "reason": r.get("reason")})
    Q._audit(seat, env, ts=now, id="-", to="consolidate", actor=actor, ok=all(o["ok"] for o in out) if out else False,
             reason="ok", file=str(file_path), ids=[o["id"] for o in out if o["ok"]])
    return {"ok": bool(out) and all(o["ok"] for o in out), "file": str(file_path), "results": out}


def reopen(seat, item_id, actor, why, env=None, now=None):
    """結錯了回復:done → read(只限收件席本人 / human;理由寫在 why,前面自動補 reopen:)。"""
    text = str(why or "").strip()
    if not text:
        return {"ok": False, "reason": "transition", "message": "回復要寫理由"}
    it = Q._load_item(seat, item_id, env)
    if not it:
        return {"ok": False, "reason": "not_found", "message": Q.TRANSITION_REASON["not_found"]}
    r = Q._transition(seat, item_id, "read", actor, env=env, now=now, why=Q.REOPEN_WHY_PREFIX + text,
                      if_rev=Q.item_rev(it), no_rev_check=False, expect_status="done")
    return {k: v for k, v in r.items() if k != "item"}
