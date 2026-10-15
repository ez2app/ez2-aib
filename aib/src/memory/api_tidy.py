#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""API 金鑰席的自動精煉(DESIGN-memory-tidy-1013 §8b;the maintainer 10-13 14:4x「API 席和 Bot 也要能自動更新」「照 A,每 20 輪」)。

API 席(openrouter / deepseek / nvidia / typesafe 的 REPL)的模型只有 aib_dm 工具,不能自己跑 aib memory tidy ⇒
由 REPL 程式代跑:每 EVERY 輪(量不到用量 %,照 the maintainer 裁的 A)——
  collect(機器量,出草稿)→ 草稿當一則訊息給這一席的模型填(只帶這場對話最後幾則,畫面不印)→ apply --defer-big → 回一行提醒。
草稿與模型的回答都不進這場對話的 messages / history(不污染對話)。任何錯 ⇒ 回一行失敗原因,REPL 照常。

  tick(session_id)  每輪回完呼叫一次;到第 20、40、… 輪回 True
  turn_end(session_id)  每輪回完呼叫一次:CTX 用量表(auto_distill_hook.outer_turn)
  run(ask, seat="") ask(prompt) -> 模型回的字;回要印在畫面的一行
  recent(messages)  ask 要帶的那幾則(system + 最後 RECENT_MESSAGES 則)

省 token(a seat 10-13 14:41):只送可以改的四段(本席列、筆記、人格、綁定檢查)+ 機器事實,不送席位記憶(apply skip_context);
模型只回四段,程式放回原草稿(不要它逐字照抄幾千字的機器事實,審查 10-13);
每次記 prompt / 回答字數到 <狀態根>/logs/auto-distill.jsonl。
模型填壞了(格式、證據、永久段)⇒ apply 擋下、這次丟掉、不重試、不擋下一則。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "cockpit", HERE.parent / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

EVERY = 20
RECENT_MESSAGES = 6        # 只帶這場對話最後幾則(整場重送很貴;a seat 10-13 14:41 API 席照 token 算錢)

PROMPT = ("[AIB 自動精煉] 請替這一席整理記憶。下面「可以改的四段」各有現在的內容與說明;「機器事實」只供參考,不要回。\n"
          "只回下面四段,格式照抄:一行 <<row>>,接本席列;一行 <<note>>,接進度筆記(這輪沒變就留空);"
          "一行 <<persona>>,接人格卡新增(每條附〔證據:「the maintainer 原話」〕,原話只能從機器事實裡照抄 ≥8 字;沒有就留空);一行 <<fit>>,接綁定檢查(照那段的格式填合 / 不合與建議)。不要加別的話。\n\n"
          "=== 可以改的四段 ===\n%s\n\n=== 機器事實 ===\n%s")
FILL_KEYS = ("row", "note", "persona", "fit")
_REPLY_RE = re.compile(r"^<<(row|note|persona|fit)>>\s*$", re.M)


def _state_path(code):
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0]) / "state" / "auto-distill" / ("api-%s.json" % code)


def _say(key, params, fallback):
    """畫面上印給人看的那一行走 i18n(the maintainer 10-13 語言規則);字典壞了 ⇒ 退回英文一行,不擋 REPL。"""
    try:
        import i18n_lib
        out = i18n_lib.t(key, params, locale=i18n_lib.resolve_locale())
        return fallback if out.startswith("!!") else out
    except Exception:  # noqa: BLE001
        return fallback


def ship_segment(code=None, gate=None):
    """第 2 層影子續跑說明(the maintainer 10-13 19:5x;a seat 20:08 派):這一席有待回報的說明 ⇒ 回那段(程式直接印在回覆下面,
    不靠模型抄),同時記成已回報(--mark)。沒有 / a seat 那支不在 / 任何錯 ⇒ ""。"""
    try:
        import auto_distill_hook as adh
        if not adh.ship_pending():                     # 每則回覆都會走到:沒有任何待回報紀錄就連席碼都不算(審查第 8 條)
            return ""
        if code is None:
            import seat_now
            code = seat_now.self_code()
        if not code:
            return ""
        return adh.ship_append(code, gate)
    except Exception:  # noqa: BLE001
        return ""


def turn_end(session_id):
    """10-15(維護者「每一家都要由程式觸發」):API 席沒有 hook 機制 ⇒ REPL 每一輪回完代呼叫 CTX 用量表那條 stop 路
    (auto_distill_hook.outer_turn:同一張表、aib dm 給自己、同格只一次)。API 席今天量不到用量(gauge pct 一律 None)
    ⇒ 照實不觸發;精煉仍由上面 tick / run 每 EVERY 輪代跑。任何錯 ⇒ 安靜略過。"""
    try:
        import auto_distill_hook as adh
        return adh.outer_turn(session_id)
    except Exception:  # noqa: BLE001
        return ""


def tick(session_id):
    """這一席又回完一輪 ⇒ 計數 +1;到 EVERY 的倍數回 True。換對話(session 不同)從 0 算。"""
    try:
        from seat_address import uuid_tail5
        code = uuid_tail5(session_id or "")
        if not code or os.environ.get("AIB_AUTO_DISTILL") == "0":
            return False
        p = _state_path(code)
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        if st.get("session") != session_id:
            st = {"session": session_id, "turns": 0}
        st["turns"] = int(st.get("turns") or 0) + 1
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, p)
        return st["turns"] % EVERY == 0
    except Exception:  # noqa: BLE001 —— 計數壞了不能讓 REPL 掛
        return False


def recent(messages):
    sysm = [m for m in messages[:1] if m.get("role") == "system"]
    return sysm + [m for m in messages if m.get("role") != "system"][-RECENT_MESSAGES:]


def sections(draft):
    """草稿 ⇒ {段名: (標題行結束位置, 段尾位置)}(用 memory_tidy.SEC_RE,不自己認格式)。"""
    import memory_tidy
    marks = list(memory_tidy.SEC_RE.finditer(draft))
    return {mk.group(1): (mk.end(), marks[i + 1].start() if i + 1 < len(marks) else len(draft))
            for i, mk in enumerate(marks)}


def build_prompt(draft):
    """只送可以改的四段(含現有內容與說明)+ 機器事實;席位記憶整段不送(a seat 草稿要短)。"""
    sec = sections(draft)
    if not all(k in sec for k in FILL_KEYS + ("facts",)):
        raise ValueError("草稿格式不認得")
    editable = "\n".join("<<%s>>%s" % (k, draft[sec[k][0]:sec[k][1]].rstrip()) for k in FILL_KEYS)
    facts = draft[sec["facts"][0]:sec["facts"][1]].strip()
    return PROMPT % (editable, facts)


def rebuild(draft, reply):
    """模型回的四段放回原草稿(標題、nonce、機器事實、席位記憶原樣);回的格式不對 ⇒ ValueError(這次丟掉)。"""
    got, marks = {}, list(_REPLY_RE.finditer(reply or ""))
    for i, mk in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(reply)
        got[mk.group(1)] = reply[mk.end():end].strip("\n")
    if "row" not in got:
        raise ValueError("模型沒回 <<row>> 那段")
    sec = sections(draft)
    out, last = [], 0
    for name, (start, end) in sorted(sec.items(), key=lambda kv: kv[1][0]):
        out.append(draft[last:start])
        out.append(("\n" + got[name] + "\n\n") if name in got else draft[start:end])
        last = end
    out.append(draft[last:])
    return "".join(out)


def _log(rec):
    try:
        import seats_dir_lib
        import time
        d = Path(seats_dir_lib.state_root()[0]) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "auto-distill.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(rec, at=int(time.time())), ensure_ascii=False) + "\n")
    except OSError:
        pass


def _strip_fence(text):
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", text or "", re.S)
    return m.group(1) if m else (text or "")


def run(ask, seat=""):
    """→ 要印在畫面上的一行。ask 要是「不帶工具的單次呼叫」(審查:帶 aib_dm / aib_local 會讓填草稿的那一輪去寄信、改檔)。
    Ctrl-C 也接住(不然 REPL 整個結束)。"""
    try:
        import memory_tidy
        import auto_distill_hook as adh
        draft = memory_tidy.collect(seat or "")
        prompt = build_prompt(draft)
        with contextlib.redirect_stdout(io.StringIO()):          # 不印到畫面
            reply = ask(prompt) or ""
        _log({"action": "api-tidy", "prompt_chars": len(prompt), "reply_chars": len(reply)})
        rec = memory_tidy.apply(rebuild(draft, _strip_fence(reply)), seat or "", defer_big=True, skip_context=True)
        return "[AIB] " + adh.T("auto_distill.notice_api", adh.notice_params(rec), None)
    except KeyboardInterrupt:
        _log({"action": "api-tidy-interrupted"})
        return _say("auto_distill.api_interrupted", {"n": EVERY}, "[AIB] auto-distill interrupted (retry in %d turns)" % EVERY)
    except Exception as exc:  # noqa: BLE001 —— 填壞、網路錯:這次丟掉、不重試
        _log({"action": "api-tidy-failed", "why": str(exc)[:200]})
        why = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
        return _say("auto_distill.api_failed", {"n": EVERY, "why": why},
                    "[AIB] auto-distill failed (retry in %d turns): %s" % (EVERY, why))


def text_of(reply):
    """nvidia completion_message 回的 message(dict 或字串)⇒ 文字。"""
    if isinstance(reply, dict):
        return str(reply.get("content") or "")
    return str(reply or "")
