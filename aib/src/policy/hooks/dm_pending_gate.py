#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm_pending_gate —— 待審框有別人寄來、還沒結案的信,回合結束前擋下(@557fe 提案,Andy 10-30 17:2x
「如何不讓你跳過處理待審區?」)。

10-30 量到的事:一席待審框堆了 7 封排隊信(含真人裁示、main CI 紅燈)沒看到;另一席 32 封讀了沒結案。
長訊落檔、排隊信不會跳進輸入框,只躺在待審框;「每輪掃佇列」寫在記憶裡靠記性,一忙就漏。
照 10-24「從源頭禁止,不靠事後砍」,改成程式擋,掛在 aib-now-gate 的 Stop(同一條 hook,不另加 settings 條目)。

## 判準(只認佇列檔的機器事實)
待審頁(tabs_payload 的 waiting)裡,符合全部條件的才算:
  - status 是 queued / delivered / hold_human,而且沒過期(claimed = 排空器投遞中,席位結不了,不算);
  - 寄件者不是這一席自己;
  - 不是真人寄的 hold_human(那條另有規矩:走 RELAY 給真人,AI 結不了),也不是全球陌生來信 / IM;
  - 進這一席佇列(ts,本機時鐘;sent_at 是對方時鐘,dm_queue 規定不拿來判先後)超過 GRACE_S(3 分鐘)—— 剛到的信讓它先貼進框。
待審頁本身就只放沒結案的(--done 之後去 done 頁),所以「沒結案」不用另判。貼進輸入框但沒讀沒結案的(delivered)也算 ——
10-30 那 32 封就是這種。read 過的進「進行中」頁,那邊另有 4 小時沒動的提醒,本支不管。
純聊天回合也擋:待審是別人在等,跟這一回合有沒有做事無關。

## 失效方向(跟 aib-now-gate 同一套)
  - 讀不到佇列 / 認不出席 / 任何例外 ⇒ 放行,留痕(呼叫端的 now-gate.log)。
  - 佇列有檔讀不了(tabs_payload 的 errors)⇒ 放行,回一句不含路徑的原因(呼叫端用 systemMessage 顯示)。
  - stop_hook_active ⇒ 呼叫端不叫本支(每回合最多擋一次)。
  - 逃生門 AIB_DM_GATE_REASON="理由" ⇒ 放行並留痕;AIB_DM_GATE=0 ⇒ 整支關(治具)。
  - 要擋的席 = aib-now-gate 的 gate-seats.txt(同一份名單,呼叫端判)。

## 放行條件(10-31 SPEC-dm-triage-auto v2 §3;擋的是「沒人看」,不是「沒開子代理」)
triage_auto 不是 off 時,下面任一成立 ⇒ 放行並留痕(off ⇒ 照上面原判準):
  - 長工作中:機器租約持有人是本席、pid 活著、沒過期(dm_queue._seat_long_work,比結構化欄位不比字串)。
  - 這一輪在回真人:對話檔最後一則**真人親打**的輸入在 HUMAN_RECENT_S 內(dm / relay 注入的不算,@34d15 21:45)。
  - 背景整理正在跑:那一席 triage-agent.json 由 headless 登記且沒過期。
  - 同批限擋:上次擋過的那批在 SAME_BATCH_S 內有任一封已被處理(離開待審)⇒ 本席在處理,不每回合重擋;
    沒動作 ⇒ 照擋;過了 SAME_BATCH_S 照擋。
完成定義照實寫:必定的是「門檻到了一定有人(席位或背景整理)在看,或有寫明的理由」,是有例外的約束。

## 它擋不住什麼
  - 結案時寫的證據對不對:dm_queue 自己要求證據,本支只看有沒有結案。
  - Codex / Grok 等沒有 Stop hook 的席。
"""
from __future__ import annotations

import os

GRACE_S = 180
SHOW_MAX = 3
TRIAGE_AT = 2
# claimed 不算(Codex r1 M1):排空器正在投遞,dm_queue 不准席位把 claimed 結案 —— 擋了也結不掉;卡住的 claim 由租約回收
LIVE_STATUSES = ("queued", "delivered", "hold_human")
_FOREIGN_KINDS = ("external_seat", "owner_paired", "external_im")   # 全球 / 配對 / IM:不是席位用 dm-queue 結得了的
HUMAN_RECENT_S = 600            # 真人親打 10 分鐘內 ⇒ 先讓真人拿到答案
SAME_BATCH_S = 1800             # 同批限擋 30 分(@34d15 10-31 21:45 Q2 照建議)
TRANSCRIPT_TAIL_BYTES = 256 * 1024
GATE_STATE_FILE = "dm-gate.json"
# dm / relay 注入輸入框的開頭(aib dm 外框:「IP:<ip>/<艙>/<名>@<席> >> IP:…」或「(@xxxxx → @yyyyy)」)
_RELAY_HEAD_RE = __import__("re").compile(r"^\s*(IP:\S+\s.*>>|[\(（\[【]\s*@?[0-9a-z]{5}\s*(→|->))")
_HUMAN_MARK = "〔艦長室〕"


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
        # @fd787 旁審 L1:只有 sender_kind 的舊列也要認(同 dm_queue._is_global_item 與 IM 判準),不然擋了又結不了
        if r.get("global") or r.get("im") or r.get("sender_kind") in _FOREIGN_KINDS:
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


def _user_text(entry):
    """對話檔一列 ⇒ 使用者親打的字(tool_result / 非 user 列回 None)。"""
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return None
    msg = entry.get("message") or {}
    c = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [x.get("text") for x in c if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str)]
        if parts and not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return "\n".join(parts)
    return None


def is_human_typed(text):
    """真人親打?dm / relay 外框開頭的不算;系統提醒(<system-reminder> 開頭)也不算;艦長室標記算。"""
    if not isinstance(text, str) or not text.strip():
        return False
    if _HUMAN_MARK in text:
        return True
    t = text.lstrip()
    if t.startswith("<"):
        return False
    return not _RELAY_HEAD_RE.match(t)


def last_human_at(transcript, size_max=TRANSCRIPT_TAIL_BYTES):
    """對話檔尾段最後一則真人親打的時刻(epoch)。讀不到 / 沒有 ⇒ None(呼叫端當「不是在回真人」,照擋)。"""
    import datetime
    import json
    if not transcript:
        return None
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - int(size_max)))
            raw = f.read(int(size_max)).decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    for ln in reversed(raw.splitlines()):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        txt = _user_text(d)
        if txt is None:
            continue
        if not is_human_typed(txt):
            continue
        try:
            return datetime.datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None
    return None


def _headless_running(seat, env, now):
    import json
    import dm_queue
    try:
        lk = json.loads((dm_queue.seat_dir(seat, env) / dm_queue.TRIAGE_AGENT_LOCK_FILE).read_text(encoding="utf-8"))
        return "/headless-" in str(lk.get("by") or "") and int(now) < int(lk.get("until") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _gate_state(seat, env):
    import json
    import dm_queue
    p = dm_queue.seat_dir(seat, env) / GATE_STATE_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return p, (d if isinstance(d, dict) else {})
    except (OSError, ValueError):
        return p, {}


def release_why(seat, hits, now, env=None, transcript=None, long_work=None):
    """純度高的判斷 → 放行理由字串;None = 照擋。long_work 可注入(測試)。"""
    import dm_queue
    lw = dm_queue._seat_long_work(seat, env, now) if long_work is None else long_work
    if lw:
        return "long-work"
    at = last_human_at(transcript)
    if at is not None and 0 <= int(now) - at < HUMAN_RECENT_S:
        return "human-turn"
    if _headless_running(seat, env, now):
        return "headless-running"
    _p, st = _gate_state(seat, env)
    try:
        last_at = int(st.get("at") or 0)
    except (TypeError, ValueError):
        last_at = 0
    prev = set(str(i) for i in (st.get("ids") or []) if isinstance(i, str))
    cur = set(str(r.get("id")) for r in hits)
    if prev and 0 <= int(now) - last_at < SAME_BATCH_S and (prev - cur):
        return "seat-working"
    return None


def _remember_block(seat, hits, now, env):
    """記下這次擋的那批(同批限擋用)。同一批還在 SAME_BATCH_S 內不改起點。寫不進去不影響擋。"""
    try:
        import atomic_json as AJ
        p, st = _gate_state(seat, env)
        ids = sorted(str(r.get("id")) for r in hits)
        try:
            fresh = 0 <= int(now) - int(st.get("at") or 0) < SAME_BATCH_S and set(st.get("ids") or []) >= set(ids)
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        AJ.write_json(str(p), {"at": int(now), "ids": ids})
    except Exception:  # noqa: BLE001
        pass


def check(seat, T, note, now=None, env=None, transcript=None):
    """回 (should_block, message)。seat 已由呼叫端認好、也確定在要擋的名單裡。
    T = 四語取字(aib-now-gate.T);note = 留痕函式;transcript = 這一席的對話檔(判「在回真人」用)。"""
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
    payload = dm_queue.tabs_payload(seat, env=env, now=now)
    errors = payload.get("errors") or []
    if errors:
        # Codex r1 M2:佇列有檔讀不了 ⇒ 判斷不完整,這回合放行(fail-open),給席位一句不含路徑的原因
        note("DMGATE FAILOPEN @%s errors=%d" % (seat, len(errors)))
        return False, T("aib.dmgate.degraded", code=seat, n=len(errors))
    tabs = payload["tabs"]
    hits = pending_rows(tabs["waiting"]["rows"], seat, now)
    if not hits:
        return False, ""
    try:
        auto_on = dm_queue.triage_auto_mode(seat, env)[0] != "off"
    except Exception:  # noqa: BLE001
        auto_on = False
    if auto_on:
        try:
            why = release_why(seat, hits, now, env=env, transcript=transcript)
        except Exception as exc:  # noqa: BLE001 —— 放行判斷壞了 ⇒ 照原判準擋(不因新功能放水)
            why = None
            note("DMGATE RELEASE-ERR @%s %s" % (seat, exc.__class__.__name__))
        if why:
            note("DMGATE RELEASE @%s why=%s n=%d" % (seat, why, len(hits)))
            return False, ""
        _remember_block(seat, hits, now, env)
    note("DMGATE BLOCK @%s n=%d ids=%s" % (seat, len(hits), ",".join(r.get("id") or "" for r in hits[:SHOW_MAX])))
    # Codex r1 M3:只列 id / rev(不帶內文、不帶 RELAY 路徑),詳細內容照 list 指令看
    ids = ", ".join("%s(rev %s)" % (r.get("id"), r.get("rev")) for r in hits[:SHOW_MAX])
    if len(hits) > SHOW_MAX:
        ids += " …+%d" % (len(hits) - SHOW_MAX)
    extra = T("aib.dmgate.triage") if len(hits) >= TRIAGE_AT else ""
    # @557fe 10-30 18:2x:進行中(read 過、沒結案、沒寫在等什麼)不擋,但擋下時一起列總數,看得到
    inprog = sum(1 for r in tabs.get("read", {}).get("rows") or [] if not r.get("wait") and r.get("read_by") != "system")
    if inprog:
        extra += T("aib.dmgate.inprog", n=inprog)
    return True, T("aib.dmgate.block", code=seat, n=len(hits), ids=ids, extra=extra)
