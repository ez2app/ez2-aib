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
  - 這一輪在回真人:對話檔最後一則**真人親打**的輸入在 HUMAN_RECENT_S 內;抬頭交 dm_header.parse 判,
    席位 / 系統寄的不算(@34d15 21:45),真人 / 艦長室網頁寄的算。
  - 背景整理正在跑:那一席 triage-agent.json 由 headless 登記且沒過期。
  - 同批限擋:上次擋的那批(含 rev)SAME_BATCH_S 內,現在這批沒有新信 / 新 rev,而且擋下之後本席真的讀 / 結過
    其中幾封 ⇒ 不每回合重擋;沒動作、信自然過期、有新信 ⇒ 照擋。
  - 判斷不了(對話檔讀不到、判斷出錯)⇒ 放行並留痕 DMGATE FAILOPEN(同本支其他失效方向)。
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
UNDETERMINED = "undetermined"   # 判斷不了(讀不到 / 出錯)⇒ 呼叫端放行並留痕(失效方向同本支其他地方)


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
    """真人親打?抬頭一律交 dm_header.parse 判(#466 Codex r1 M1:不另寫 regex、不認正文裡的字樣):
      - 認得出抬頭:寄件者是真人(the maintainer@hub)或艦長室網頁(艦長室@room)⇒ 是;席位 / 系統 / 其他 ⇒ 不是
      - 認不出抬頭:系統提醒(< 開頭)不是;其他 = 終端機直接打的 ⇒ 是
    注意:同帳號 AI 能 send-keys 打出任何字,這裡只用來避免誤擋,不是防線。"""
    if not isinstance(text, str) or not text.strip():
        return False
    import dm_header
    h = dm_header.parse(text.lstrip())
    if h is not None:
        code = (h.get("sender") or {}).get("code") or ""
        return h.get("sender_kind") == "human" or code == dm_header.ROOM_CODE
    return not text.lstrip().startswith("<")


def _tail_lines(path, size_max):
    """檔尾最多 size_max 位元組 → (行, 第一行是不是被切到的半行)。開檔 / 讀檔錯誤往外拋。"""
    with open(path, "rb") as f:
        f.seek(0, 2)
        n = f.tell()
        start = max(0, n - int(size_max))
        cut = False
        if start > 0:
            f.seek(start - 1)
            cut = f.read(1) != b"\n"         # 前一個位元組不是換行 ⇒ 第一行是半行
        f.seek(start)
        raw = f.read(int(size_max)).decode("utf-8", "replace")
    return raw.splitlines(), cut


def last_human_at(transcript, size_max=TRANSCRIPT_TAIL_BYTES):
    """對話檔尾段最後一則真人親打的時刻(epoch)。沒給路徑 / 尾段沒有真人親打 ⇒ None(不是在回真人);
    給了路徑卻讀不到 ⇒ UNDETERMINED(判斷不了,#466 Codex r1 M4)。"""
    import datetime
    import json
    if not transcript:
        return None
    try:
        lines, _cut = _tail_lines(transcript, int(size_max))
    except (OSError, ValueError):
        return UNDETERMINED
    for idx in range(len(lines) - 1, -1, -1):
        ln = lines[idx]
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            if idx == 0 and _cut:
                continue                  # 尾段讀取切到的第一行(半行)
            return UNDETERMINED           # #466 Codex r3 M4:壞行不是「沒有真人」
        if not isinstance(d, dict):
            return UNDETERMINED
        txt = _user_text(d)
        if txt is None:
            continue
        if not is_human_typed(txt):
            continue
        try:
            return datetime.datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return UNDETERMINED
    return None


class Undetermined(Exception):
    """判斷要用的檔在、但讀不到 / 壞了(#466 Codex r2 M4)。release_why 往外拋,check 收成 UNDETERMINED 放行留痕。
    只有「檔不存在」才當沒有(那是正常狀態)。"""


def _read_json(path):
    """不存在 ⇒ None;讀不到(權限 / I/O)或不是 JSON 物件 ⇒ Undetermined。"""
    import json
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Undetermined("read:%s:%s" % (path.name, exc.__class__.__name__))
    try:
        d = json.loads(raw)
    except ValueError:
        raise Undetermined("json:%s" % path.name)
    if not isinstance(d, dict):
        raise Undetermined("shape:%s" % path.name)
    return d


def _headless_running(seat, env, now):
    import dm_queue
    lk = _read_json(dm_queue.seat_dir(seat, env) / dm_queue.TRIAGE_AGENT_LOCK_FILE)
    if lk is None:
        return False
    try:
        return "/headless-" in str(lk.get("by") or "") and int(now) < int(lk.get("until") or 0)
    except (TypeError, ValueError):
        raise Undetermined("shape:" + dm_queue.TRIAGE_AGENT_LOCK_FILE)


def _gate_state(seat, env):
    import dm_queue
    p = dm_queue.seat_dir(seat, env) / GATE_STATE_FILE
    d = _read_json(p)
    if d is None:
        return p, {}
    at, pairs = d.get("at"), d.get("pairs")
    # #466 Codex r3 M4:欄位壞了不能轉成 0 / 空(那樣同批限擋就當沒記過、照擋)
    if type(at) is not int or not isinstance(pairs, list) or not all(isinstance(x, str) and "@" in x for x in pairs):
        raise Undetermined("shape:" + GATE_STATE_FILE)
    return p, d


def _pairs(hits):
    return sorted("%s@%s" % (r.get("id"), r.get("rev")) for r in hits)


AUDIT_TAIL_BYTES = 1024 * 1024       # 同批限擋只看 SAME_BATCH_S 內的動作,讀 audit 尾段就夠


def _handled_since(seat, pairs, since, env):
    """上次擋下之後,本席親手處理過其中任一封?(#466 Codex r2 M2)
    看佇列 audit.jsonl 的成功轉移:actor 正好是 seat:<本席>(不是 system / 背景整理 agent / human / 缺 actor)、
    轉到 read / done / archived、時刻在擋下之後、修訂號 = 擋下時那一版。audit 在但讀不到 ⇒ Undetermined。
    --by 是字面守衛,同帳號擋不住同帳號;這裡只用來「不誤擋正在做事的席」,不是防線。"""
    import json
    import dm_queue
    want = {}
    for x in pairs:
        iid, _sep, rev = x.rpartition("@")
        want[iid] = rev
    p = dm_queue.seat_dir(seat, env) / dm_queue.AUDIT_FILE
    try:
        lines, cut = _tail_lines(p, AUDIT_TAIL_BYTES)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise Undetermined("read:audit:%s" % exc.__class__.__name__)
    me = "seat:" + str(seat)
    for idx, ln in enumerate(lines):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            if idx == 0 and cut:
                continue                      # 尾段讀取切到的第一行(半行)
            raise Undetermined("json:audit")  # #466 Codex r3 M4:壞行可能正是本席那一筆,判斷不了
        if not isinstance(r, dict):
            raise Undetermined("shape:audit")
        if r.get("ok") is not True or r.get("actor") != me:
            continue
        if r.get("to") not in ("read", "done", "archived") or r.get("id") not in want:
            continue
        if type(r.get("ts")) is not int:
            raise Undetermined("fields:audit")
        if r["ts"] >= int(since) and str(r.get("rev")) == want[r["id"]]:
            return True
    return False


def release_why(seat, hits, now, env=None, transcript=None, long_work=None):
    """→ 放行理由字串 / None(照擋)/ UNDETERMINED 開頭的字串(判斷不了,呼叫端放行留痕)。long_work 可注入(測試)。"""
    import dm_queue
    lw = dm_queue._seat_long_work(seat, env, now) if long_work is None else long_work
    if lw:
        return "long-work"
    at = last_human_at(transcript)
    if at == UNDETERMINED:
        return UNDETERMINED + ":transcript"
    if at is not None and 0 <= int(now) - at < HUMAN_RECENT_S:
        return "human-turn"
    if _headless_running(seat, env, now):
        return "headless-running"
    # 同批限擋(#466 Codex r1 M2):記的是含 rev 的那批 + 擋下時刻;現在這批要是那批的子集合(沒有新信、沒有新 rev),
    #   而且擋下之後本席真的處理過離開的那幾封,才放行。舊信自然過期 / 被別人收走不算本席處理。
    _p, st = _gate_state(seat, env)
    last_at = st.get("at") or 0             # _gate_state 已驗形狀(壞了就拋 Undetermined)
    prev = set(st.get("pairs") or [])
    cur = set(_pairs(hits))
    if prev and 0 <= int(now) - last_at < SAME_BATCH_S and cur <= prev and (prev - cur):
        if _handled_since(seat, sorted(prev - cur), last_at, env):
            return "seat-working"
    return None


def _remember_block(seat, hits, now, env):
    """記下這次擋的那批(含 rev)與擋下時刻。同一批(或它的子集合)還在 SAME_BATCH_S 內不改起點。寫不進去不影響擋。"""
    try:
        import atomic_json as AJ
        p, st = _gate_state(seat, env)
        pairs = _pairs(hits)
        try:
            fresh = (0 <= int(now) - int(st.get("at") or 0) < SAME_BATCH_S
                     and set(st.get("pairs") or []) >= set(pairs))
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        AJ.write_json(str(p), {"at": int(now), "pairs": pairs})
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
        mode, src = dm_queue.triage_auto_mode(seat, env)
    except Exception as exc:  # noqa: BLE001 —— #466 Codex r3 M4:開關判斷不了 ⇒ 放行留痕,不退回舊擋法
        mode, src = "draft", "unreadable:%s" % exc.__class__.__name__
    if dm_queue.triage_auto_unreadable(src):
        note("DMGATE FAILOPEN @%s %s:switch:%s n=%d" % (seat, UNDETERMINED, src, len(hits)))
        return False, ""
    if mode != "off":
        try:
            why = release_why(seat, hits, now, env=env, transcript=transcript)
        except Exception as exc:  # noqa: BLE001 —— 判斷不了 ⇒ 放行留痕(#466 Codex r1 M4;同本支讀不到佇列的失效方向)
            why = "%s:%s" % (UNDETERMINED, exc if isinstance(exc, Undetermined) else exc.__class__.__name__)
        if why and why.startswith(UNDETERMINED):
            note("DMGATE FAILOPEN @%s %s n=%d" % (seat, why, len(hits)))
            return False, ""
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
