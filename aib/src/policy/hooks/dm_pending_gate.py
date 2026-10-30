#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm_pending_gate —— 待審框有別人寄來、還沒結案的信,回合結束前擋下(@557fe 提案,Andy 10-30 17:2x
「如何不讓你跳過處理待審區?」)。

10-30 量到的事:一席待審框堆了 7 封排隊信(含真人裁示、main CI 紅燈)沒看到;另一席 32 封讀了沒結案。
長訊落檔、排隊信不會跳進輸入框,只躺在待審框;「每輪掃佇列」寫在記憶裡靠記性,一忙就漏。
照 10-24「從源頭禁止,不靠事後砍」,改成程式擋,掛在 aib-now-gate 的 Stop(同一條 hook,不另加 settings 條目)。

## 判準(只認佇列檔的機器事實)
待審頁(tabs_payload 的 waiting)裡,符合全部條件的才算:
  - status 是 queued / claimed / delivered / hold_human,而且沒過期;
  - 寄件者不是這一席自己;
  - 不是真人寄的 hold_human(那條另有規矩:走 RELAY 給真人,AI 結不了),也不是全球陌生來信 / IM;
  - 進這一席佇列(ts,本機時鐘;sent_at 是對方時鐘,dm_queue 規定不拿來判先後)超過 GRACE_S(3 分鐘)—— 剛到的信讓它先貼進框。
待審頁本身就只放沒結案的(--done 之後去 done 頁),所以「沒結案」不用另判。貼進輸入框但沒讀沒結案的(delivered)也算 ——
10-30 那 32 封就是這種。read 過的進「進行中」頁,那邊另有 4 小時沒動的提醒,本支不管。
純聊天回合也擋:待審是別人在等,跟這一回合有沒有做事無關。

## 失效方向(跟 aib-now-gate 同一套)
  - 讀不到佇列 / 認不出席 / 任何例外 ⇒ 放行,留痕(呼叫端的 now-gate.log)。
  - stop_hook_active ⇒ 呼叫端不叫本支(每回合最多擋一次)。
  - 逃生門 AIB_DM_GATE_REASON="理由" ⇒ 放行並留痕;AIB_DM_GATE=0 ⇒ 整支關(治具)。
  - 要擋的席 = aib-now-gate 的 gate-seats.txt(同一份名單,呼叫端判)。

## 它擋不住什麼
  - 結案時寫的證據對不對:dm_queue 自己要求證據,本支只看有沒有結案。
  - Codex / Grok 等沒有 Stop hook 的席。
"""
from __future__ import annotations

import os
import re

GRACE_S = 180
SHOW_MAX = 5
TRIAGE_AT = 2
LIVE_STATUSES = ("queued", "claimed", "delivered", "hold_human")
_RELAY_RE = re.compile(r"(/\S*RELAY-\S+?\.txt)")


def pending_rows(rows, seat, now):
    """純函式:從待審頁的列挑出「要這一席處理、還沒結案、超過寬限」的列,照寄出時刻舊的在前。"""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        st = r.get("status")
        if st not in LIVE_STATUSES or r.get("expired_now"):
            continue
        if (r.get("sender_seat") or "") == seat:
            continue
        if r.get("global") or r.get("im"):
            continue
        if st == "hold_human" and r.get("sender_kind") == "human":
            continue
        try:
            ts = int(r.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts <= 0:
            continue   # 量不到時刻 ⇒ 不算(寧可漏擋一封,不誤擋)
        if int(now) - ts < GRACE_S:
            continue
        out.append(r)
    out.sort(key=lambda r: (int(r.get("ts") or 0), r.get("id") or ""))
    return out


def _line(r):
    msg = " ".join(str(r.get("msg") or "").split())
    m = _RELAY_RE.search(msg)
    preview = msg[:60] + ("…" if len(msg) > 60 else "")
    sender = r.get("sender_seat") and "@%s" % r["sender_seat"] or (r.get("sender") or "?")
    s = "  %s rev=%s %s %s:%s" % (r.get("id"), r.get("rev"), r.get("when") or "", sender, preview)
    if m:
        s += "\n      → %s" % m.group(1)
    return s


def check(seat, T, note, now=None, env=None):
    """回 (should_block, message)。seat 已由呼叫端認好、也確定在要擋的名單裡。
    T = 四語取字(aib-now-gate.T);note = 留痕函式。"""
    e = os.environ if env is None else env
    if e.get("AIB_DM_GATE") == "0":
        return False, ""
    reason = (e.get("AIB_DM_GATE_REASON") or "").strip()
    if reason:
        note("DMGATE BYPASS @%s %s" % (seat, reason))
        return False, ""
    import time
    import dm_queue
    now = int(time.time() if now is None else now)
    rows = dm_queue.tabs_payload(seat, env=env, now=now)["tabs"]["waiting"]["rows"]
    hits = pending_rows(rows, seat, now)
    if not hits:
        return False, ""
    note("DMGATE BLOCK @%s n=%d ids=%s" % (seat, len(hits), ",".join(r.get("id") or "" for r in hits[:SHOW_MAX])))
    lines = "\n".join(_line(r) for r in hits[:SHOW_MAX])
    more = T("aib.dmgate.more", n=len(hits) - SHOW_MAX) if len(hits) > SHOW_MAX else ""
    triage = T("aib.dmgate.triage", n=len(hits)) if len(hits) >= TRIAGE_AT else ""
    msg = T("aib.dmgate.block", code=seat, n=len(hits), lines=lines, more=more, triage=triage)
    return True, msg
