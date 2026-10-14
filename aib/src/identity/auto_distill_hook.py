#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""依用量自動精煉(Claude 席 UserPromptSubmit hook;DESIGN-memory-tidy-1013 §8b,the maintainer 10-13 13:5x 裁)。

the maintainer:「原 ctx %數 … 從 1x% 開始的」「照這套做,在 10% 之後要提醒已自動精煉對話到人格與第二大腦(專案)中,
然後大改存到 85% 再給我按」。

  python3 auto_distill_hook.py prompt                 UserPromptSubmit:stdin 是 hook JSON,stdout 進對話
  python3 auto_distill_hook.py stop                   非 Claude 席「一輪結束」hook(Codex / Copilot / Grok / agy):
                                                      跨門檻 ⇒ 背景 aib dm 給自己這一席「請整理記憶」(the maintainer 10-13 14:1x「照這樣接」)
  python3 auto_distill_hook.py ensure [--settings P]  開 Claude 席前(provider-launch.sh):已裝不動、沒裝才裝(先備份)
  python3 auto_distill_hook.py ensure-grok [--dir D]  開 Grok 席前:~/.grok/hooks/aib-auto-distill.json(Claude 格式的 Stop)

每一則訊息都會跑(a seat 10-13 13:56):
  - 正常路徑只量一次 gauge 就回;沒跨門檻、沒有待發的提醒 ⇒ stdout 為空。
  - 剛跨過 30 / 50 / 70(數字取 persona-collect tick.DEFAULT_THRESHOLDS,不另寫)⇒ 輸出一行,
    叫 AI 這則回完後跑 aib memory tidy(--defer-big:第一艦長卡大變動存到 85% 交接再按)。
    距上次精煉沒有新的真人訊息 ⇒ 不叫(省用量),門檻照記。同一門檻同一個對話只一次。
  - 精煉收據出現後的下一則真人訊息 ⇒ 輸出一行,叫 AI 在回覆最後一行告訴 the maintainer 已自動精煉(數字讀收據,不由 AI 自報)。
  - 85 / 95 不在這裡做(交接提醒 ctx-handoff-nudge.sh 照舊)。
  - 任何錯 ⇒ rc 0、無輸出(不能擋人講話)。
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
for _p in (SRC / "cockpit", SRC / "persona-collect", SRC / "memory"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

MARKER = "auto_distill_hook.py"
FRESH_S = 600                       # 沿用 ctx-handoff-nudge.sh 的 10 分鐘新鮮
HOOK_TIMEOUT_S = 10
_SID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


INBOX_SHOW_MAX = 5


def inbox_block(code, run=None, locale=None):
    """§8d(the maintainer 10-13 17:0x 訊息待審區自動處理):呼叫 a seat 的 `dm_queue.py stage-tidy --seat <席> --json`
    (先做完機器清理、遵守開關),need_ai 不空 ⇒ 回一段接在整理提醒後面的字;沒這支指令 / 開關關 / 錯 ⇒ ""。"""
    import subprocess
    dq = SRC / "cockpit" / "dm_queue.py"
    if not dq.is_file():
        return ""
    base = [sys.executable, str(dq), "stage-tidy", "--seat", code, "--json"]
    try:
        # --mark-woke:讓 a seat 的 triage_tick 冷卻把這次也算進去(同一刻不叫兩次;那個紀錄檔只歸他寫);
        #   他的指令還不認得這個參數(argparse rc 2)⇒ 退回不帶再叫一次
        r = (run or subprocess.run)(base + ["--mark-woke"], capture_output=True, text=True, timeout=20,
                                    stdin=subprocess.DEVNULL)
        if r.returncode == 2:
            r = (run or subprocess.run)(base, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        doc = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    except Exception:  # noqa: BLE001
        return ""
    need = doc.get("need_ai") or []
    if not doc.get("enabled") or not need:
        return ""
    items = ";".join("%s(%s)%s" % (x.get("id", "?"), x.get("from") or "?", (x.get("summary") or "")[:60])
                     for x in need[:INBOX_SHOW_MAX])
    more = len(need) - INBOX_SHOW_MAX + int(doc.get("todo") or 0)
    return T("auto_distill.inbox", {"n": len(need) + int(doc.get("todo") or 0), "items": items,
                                    "more": max(0, more)}, locale)


def with_inbox(text, code, locale=None):
    """整理提醒 + 待審區那段(一席只被叫一次)。"""
    extra = inbox_block(code, locale=locale)
    return text + ("\n" + extra if extra else "")


AUTO_THRESHOLDS = (30, 50, 70, 85, 90)


def thresholds():
    """自動精煉(靜默)的門檻。the maintainer 10-13:17:3x「把10%取消好了」;17:5x「同意提早到80%,然後85%、90% 做靜默更新」
    ⇒ 30 / 50 / 70 / 85 / 90 靜默;80 是交接流程(ctx-handoff-nudge.sh);95 紅線照舊。
    跟 persona-collect tick.DEFAULT_THRESHOLDS(10/30/50/70/85/95)分家了 —— 那份是人格收集的,不是這裡的。"""
    return AUTO_THRESHOLDS


def state_root():
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def state_path(code):
    return state_root() / "state" / "auto-distill" / ("%s.json" % code)


def _read_json(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            v = json.load(fh)
        return v if isinstance(v, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(p, doc):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _log(rec):
    try:
        d = state_root() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        rec = dict(rec, at=int(time.time()))
        with open(d / "auto-distill.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def gauge_pct(sid, now=None):
    """`$EZ2_CTX_DIR/<sid>.pct` 的已用 %;過期(> 10 分)、缺檔、符號連結、壞檔 ⇒ None。"""
    g = Path(os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")) / ("%s.pct" % sid)
    if g.is_symlink() or not g.is_file():
        return None
    doc = _read_json(g, {})
    pct = doc.get("pct")
    try:
        obs = datetime.datetime.strptime(str(doc["observed_at"])[:19], "%Y-%m-%dT%H:%M:%S") \
            .replace(tzinfo=datetime.timezone.utc).timestamp()
    except (KeyError, ValueError):
        return None
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or (now or time.time()) - obs > FRESH_S:
        return None
    return int(pct)


def is_human(code, prompt):
    """跟 aib memory tidy 收 the maintainer 原話同一把篩子(dm 標頭、通知、hook 回饋等機器字樣擋掉);篩子壞了 ⇒ 當不是。"""
    try:
        import human_filter
        ok, _m = human_filter.is_human({"human_bound": True, "code": code}, {"text": prompt}, skip_ledger=True)
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False


def lang_of(text):
    """回覆語言:先看這則真人訊息(the maintainer 10-13 13:38),判不出才用 i18n 語系。"""
    body = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    if re.search(r"[가-힣]", body):
        return "ko"
    if re.search(r"[぀-ヿ]", body):
        return "ja"
    if len(re.findall(r"[一-鿿]", body)) >= 2:
        return "zh-TW"
    if len(re.findall(r"[A-Za-z]{2,}", body)) >= 3:
        return "en"
    return None


def _installed(what, path, extra=""):
    """開席時印給人看的那一行(stderr)走 i18n;extra = 要接在後面的一句(Codex 要信任、原檔備份)。"""
    try:
        line = T("auto_distill.installed", {"what": what, "path": str(path)}, None)
    except Exception:  # noqa: BLE001 —— 字典壞了也不能讓開席出錯
        line = "auto-distill installed: %s (%s)" % (what, path)
    return "%s;%s" % (line, extra) if extra else line


def T(key, params, locale):
    import i18n_lib
    return i18n_lib.t(key, params, locale=locale or i18n_lib.resolve_locale())


def tidy_receipt(code):
    try:
        import memory_tidy
        return memory_tidy.last_receipt(code) or {}
    except Exception:  # noqa: BLE001
        return {}


def notice_params(rec):
    files = rec.get("files") or {}
    per = files.get("persona") or {}
    ok = ("ok", "partial")
    return {
        "persona": int(per.get("added") or 0) if per.get("status") in ok else 0,
        "deferred": int(per.get("added") or 0) if per.get("status") == "waiting" else 0,
        "card": 1 if (files.get("card") or {}).get("status") in ok else 0,
        "note": 1 if (files.get("note") or {}).get("status") in ok else 0,
    }


def foreign_host(environ=None):
    """Grok 也會跑 ~/.claude/settings.json 的 hook(相容模式),但它的 UserPromptSubmit 輸出進不了模型 ——
    在那裡記門檻只會把門檻用掉(Grok 走 stop 那條)。"""
    environ = os.environ if environ is None else environ
    return bool(environ.get("GROK_SESSION_ID"))


def is_aib_seat(sid):
    """這個對話是不是 AIB 的席(席位檔登記的 session_uuid)。ClaudeBot 綁的本機 Claude 對話也會跑這支 hook,
    但它不是 AIB 席 —— 那邊由 claudebot_hook 的 Stop 用綁定的那一席來做(bot_turn)。量不到 ⇒ 當是(舊行為)。"""
    try:
        import seat_now
        recs = seat_now._seat_records()
    except Exception:  # noqa: BLE001
        return True
    return any(str(r.get("session_uuid") or "").lower() == sid.lower() for r in recs)


def on_prompt(payload, now=None):
    """→ 要印進對話的那一行(或空字串)。"""
    import seat_address
    if foreign_host():
        return ""
    sid = str(payload.get("session_id") or "")
    if not _SID_RE.match(sid):
        return ""
    code = seat_address.uuid_tail5(sid)
    if not code or not is_aib_seat(sid):
        return ""
    prompt = str(payload.get("prompt") or "")
    sp = state_path(code)
    st = _read_json(sp, {})
    if st.get("session") != sid:                       # 換對話(/clear、新席)⇒ 門檻重算
        st = {"session": sid, "fired": [], "human": 0, "receipt_at": "", "pending": False, "notice_for": ""}
    before = json.dumps(st, sort_keys=True)
    human = is_human(code, prompt)
    rec = tidy_receipt(code)
    if rec.get("at") and rec.get("at") != st.get("receipt_at"):   # 又整理過一次 ⇒ 新真人訊息從零算
        st["receipt_at"], st["human"] = rec.get("at"), 0
        if st.get("pending"):                          # 是被叫去做的那次 ⇒ 下一則真人訊息提醒 the maintainer
            st["notice_for"], st["pending"] = rec.get("at"), False
    if human:
        st["human"] = int(st.get("human") or 0) + 1
    lines = []
    if human and st.get("notice_for"):
        lines.append(T("auto_distill.notice", notice_params(rec), lang_of(prompt)))
        st["notice_for"] = ""
    pct = gauge_pct(sid, now)
    newly = [t for t in thresholds() if pct is not None and pct >= t and t not in (st.get("fired") or [])]
    if newly:
        if int(st.get("human") or 0) == 0:                # 上次精煉後(或開對話後)沒有真人原話 ⇒ 不叫 AI 白做;
            _log({"seat": code, "pct": pct, "fired": newly, "action": "skip-nothing-new"})   # 門檻不算用掉
        else:
            st["fired"] = sorted(set(st.get("fired") or []) | set(newly))
            lines.append(with_inbox(T("auto_distill.instruct", {"pct": pct}, lang_of(prompt) if human else None),
                                    code, lang_of(prompt) if human else None))
            st["pending"] = True
            _log({"seat": code, "pct": pct, "fired": newly, "action": "instruct"})
    out = "\n".join(lines)
    changed = json.dumps(st, sort_keys=True) != before
    if changed:
        _write_json(sp, st)
    return out


# ---------------------------------------------------------------- 非 Claude 席:一輪結束
def self_seat():
    """這一席的席位檔(用 TMUX_PANE 認窗,四家都通);認不出 ⇒ None。"""
    import seat_who
    wid, sock = seat_who.self_pane_facts()
    return seat_who.seat_record_for_window(wid, sock)


def dm_allowed():
    """the maintainer 10-13(在 a seat 窗裡同意):tidy 還沒進主線前「寄信先關」⇒ 只記到點(due),不寄 dm。
    AIB_AUTO_DISTILL_DM=0 照舊可以手動關。"""
    if os.environ.get("AIB_AUTO_DISTILL_DM") == "0":
        return False
    return (SRC / "memory" / "memory_tidy.py").is_file()


IDLE_QUIET_S = 120      # 畫面連續這麼久沒變 + 輸入框空 = 閒下來(the maintainer 10-13 17:0x「閒下來才寄」)
IDLE_POLL_S = 15
IDLE_GIVE_UP_S = 1800   # 等不到就留著 due,下一輪結束再起一支


def due_path(code):
    return state_root() / "state" / "auto-distill" / "due" / ("%s.json" % code)


def _waiter_path(code, kind="tidy"):
    """等閒才寄的那支各用各的記號:tidy = 整理提醒(成功要刪 due);ship = 影子續跑說明漏貼提醒(不碰 due)。"""
    return state_root() / "state" / "auto-distill" / "due" / ("%s.waiter" % code if kind == "tidy" else "%s.%s.waiter" % (code, kind))


def waiter_alive(code, kind="tidy"):
    p = _waiter_path(code, kind)
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def pane_idle_state(socket, wid, provider, capture=None):
    """→ (畫面雜湊, 有人在打字)。抓不到 ⇒ (None, True)(當忙,不寄)。"""
    import hashlib
    import subprocess
    try:
        out = capture() if capture else subprocess.run(
            ["tmux", "-L", socket, "capture-pane", "-p", "-e", "-t", wid], capture_output=True, text=True,
            timeout=10, stdin=subprocess.DEVNULL).stdout
    except Exception:  # noqa: BLE001
        return None, True
    typing = False
    try:
        import composer_state as cs
        st, _t = cs.composer_state(out, provider=provider)
        typing = st == cs.DRAFT
    except Exception:  # noqa: BLE001
        pass
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out or "")
    plain = re.sub(r"\d+(m|s|ms)\b|\d+:\d+(:\d+)?|\d+%", "", plain)   # 計時、時鐘、用量 % 會自己跳,不算有動
    return hashlib.sha256(plain.encode("utf-8", "replace")).hexdigest()[:16], typing


def wait_idle_then_send(target, socket, wid, provider, text, code, now=time.time, sleep=time.sleep, capture=None,
                        send=None, kind="tidy"):
    """背景:畫面連 IDLE_QUIET_S 秒沒變、輸入框空 ⇒ aib dm;成功就刪 due。回 True = 寄出。"""
    import subprocess
    marker = _waiter_path(code, kind)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    start, last_hash, quiet_since = now(), None, now()
    try:
        while now() - start < IDLE_GIVE_UP_S:
            h, typing = pane_idle_state(socket, wid, provider, capture)
            t = now()
            if h is None or typing or h != last_hash:
                last_hash, quiet_since = h, t
            elif t - quiet_since >= IDLE_QUIET_S:
                if kind == "ship" and not ship_pending(code):
                    _log({"seat": code, "action": "ship-dm-skipped", "why": "already-reported"})
                    return True                         # 等的這段時間它已經貼了(check 記成已回報)⇒ 不寄過時的提醒
                ok = (send or (lambda: subprocess.run(["aib", "dm", target, text], capture_output=True, timeout=60,
                                                      stdin=subprocess.DEVNULL).returncode == 0))()
                if ok:
                    if kind == "tidy":                 # 影子續跑那種不是整理提醒 ⇒ 不刪整理的到點紀錄
                        try:
                            due_path(code).unlink()
                        except OSError:
                            pass
                    _log({"seat": code, "action": "dm-idle", "kind": kind, "waited_s": int(t - start)})
                    return True
                quiet_since = t
            sleep(IDLE_POLL_S)
        _log({"seat": code, "action": "dm-gave-up", "waited_s": int(now() - start)})
        return False
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def write_due(code, sid, pct, newly, at=None):
    try:
        _write_json(state_root() / "state" / "auto-distill" / "due" / ("%s.json" % code),
                    {"session": sid, "pct": pct, "thresholds": newly, "at": int(at or time.time())})
    except OSError:
        pass


def deliver(cabin, code, text, runner=None, rec=None, kind="tidy"):
    """背景起一支「等閒下來才寄」(the maintainer 10-13 17:0x);hook 不等。同一席同時只一支。"""
    target = "%s/%s" % (str(cabin).rsplit("-", 1)[-1][-4:], code)
    rec = rec or {}
    cmd = [sys.executable, str(Path(__file__).resolve()), "wait-send", target, str(rec.get("socket") or ""),
           str(rec.get("window_id") or ""), str(rec.get("provider") or ""), code, text, kind]
    if runner:
        return runner(cmd)
    if waiter_alive(code, kind):
        return None
    import subprocess
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return None


NOT_A_TURN = {"shutdown", "channel_closed", "user_canceled", "user_cancelled", "user_interrupt", "canceled",
              "cancelled", "interrupted", "error", "max_turns", "permission_rejected", "no_progress"}
PROBES = {"grok": "grok_ctx_probe", "copilot": "copilot_ctx_probe", "codex": "codex_ctx_probe"}


def real_turn_end(payload):
    """各家「一輪結束」欄位不同(a seat 10-13 盤點):Grok reason、Copilot stopReason、agy terminationReason(+ fullyIdle)。
    收尾、取消、出錯不算;stop hook 自己續的那輪不算。"""
    if payload.get("stopHookActive") or payload.get("stop_hook_active"):
        return False
    if payload.get("fullyIdle") is False:
        return False
    if payload.get("subagentType") or payload.get("subagent_type"):    # 子代理收尾不是這一席的一輪(a seat 閘 C)
        return False
    r = str(payload.get("reason") or payload.get("stopReason") or payload.get("terminationReason") or "").strip().lower()
    # Grok 一定帶 reason,只認 end_turn(a seat;shutdown 已在 NOT_A_TURN)
    if "reason" in payload:
        return r == "end_turn"
    # Codex 的 Stop 沒有任何 reason 欄位(session_id / turn_id / stop_hook_active / last_assistant_message)⇒ 空 = 算一輪
    return r not in NOT_A_TURN


def seat_for_payload(payload):
    """先用 TMUX_PANE 認窗;認不出(grok -p / copilot -p / agy -p 的子行程常沒有)⇒ 用 payload 的對話編號對席位檔。"""
    rec = None
    try:
        rec = self_seat()
    except Exception:  # noqa: BLE001
        rec = None
    if rec:
        return rec
    sid = str(payload.get("sessionId") or payload.get("session_id") or payload.get("conversationId") or "").lower()
    if not sid:
        return None
    try:
        import seat_now
        hits = [r for r in seat_now._seat_records() if str(r.get("session_uuid") or "").lower() == sid]
    except Exception:  # noqa: BLE001
        return None
    return hits[0] if len(hits) == 1 else None


def refresh_gauge(provider, sid):
    """一輪剛結束、用量檔還沒更新 ⇒ 先叫那家的探針刷一次(失敗保持現況)。"""
    mod = PROBES.get(provider)
    if not mod:
        return
    try:
        __import__(mod).refresh(sid)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 第 2 層:影子續跑說明漏貼(a seat ship_report_gate)
# the maintainer 10-13 19:5x「如果開,所有 AI 都必要列出」;a seat 20:08 派:Claude 以外各家一輪結束的掛點呼叫 a seat 的
#   ship_report_gate.py check / segment(它那支不在 ⇒ 安靜略過,兩包誰先上主線都不壞)。
SHIP_GATE = SRC / "policy" / "hooks" / "ship_report_gate.py"
# Codex / Claude:last_assistant_message;Grok:lastAssistantMessage(a seat 10-13 探針 probe-grok.jsonl,end_turn 才有)。
#   不認 response:那是 Copilot subagentStop 的欄位,不是這一輪的回覆(a seat 10-13)。
REPLY_KEYS = ("last_assistant_message", "lastAssistantMessage")
TRANSCRIPT_TAIL_BYTES = 512 * 1024


def _copilot_reply(path):
    """Copilot agentStop 沒帶原文,但 transcriptPath = ~/.copilot/session-state/<id>/events.jsonl(一行一個事件);
    這一輪回覆 = 往回最後一筆 type=assistant.message 的 data.content(a seat 10-13 實測)。只讀檔尾。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TRANSCRIPT_TAIL_BYTES))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "assistant.message":
            text = (ev.get("data") or {}).get("content")
            return text if isinstance(text, str) and text.strip() else None
    return None


def reply_of(payload):
    """這一輪 AI 交回的原文;讀不到 ⇒ None(交給第 3 層巡查讀窗格)。"""
    for k in REPLY_KEYS:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    tp = str(payload.get("transcriptPath") or "")
    if tp.endswith("events.jsonl") and os.path.isfile(tp):
        return _copilot_reply(tp)
    return None


SHIP_GATE_TIMEOUT_S = 3      # 各家 hook 自己的逾時多半 10 秒,這支不能吃光(審查第 5 條)


SHIP_MAX_AGE_S = 86400       # 同 a seat ship_report_gate.MAX_AGE_S


def _ship_dir():
    """同 a seat ship_report_gate.ship_dir。推包紀錄夾是整台一份、不隨艙變 ⇒ 單獨一支讀,不跟狀態根在同一個函式裡
    (test-cabin-read-set / single-source-names 把「讀到艙名字的函式裡的每個環境變數」都算艙解析路徑)。"""
    return os.environ.get("AIB_SHIP_DIR") or os.path.join(os.path.expanduser("~"), ".ez2-aib", "ship")


def ship_pending(code=None, now=None):
    """程序內先看一眼:有沒有「還沒回報、24 小時內」的紀錄(code 給了就只看推的人是這一席的)。條件照 a seat
    ship_report_gate.pending(兩處檔、reported is False、pusher、savings ≥ 2 行、at 在 24 小時內)。
    審查第二輪:只看「有沒有檔」沒用 —— 說明檔新的蓋舊的、從不刪,跑過一次 aib test 之後就永遠有檔。"""
    import glob
    ship_dir = _ship_dir()
    root = os.environ.get("EZ2_AIB_STATE_ROOT") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    now = time.time() if now is None else now
    for f in (glob.glob(os.path.join(ship_dir, "last-push-*.json"))
              + glob.glob(os.path.join(root, "state", "shadow-explain", "*.json"))):
        d = _read_json(Path(f), None)
        if not isinstance(d, dict) or d.get("reported") is not False:
            continue
        if code is not None and d.get("pusher") != code:
            continue
        if not isinstance(d.get("savings"), list) or len(d["savings"]) < 2:
            continue
        try:
            if now - float(d.get("at") or 0) > SHIP_MAX_AGE_S:
                continue
        except (TypeError, ValueError):
            continue
        return True
    return False


def ship_gate(argv, text=None, run=None):
    """叫 a seat 那支 → (rc, stdout)。那支不在 / 這一席沒有待回報紀錄 / 逾時 / 任何錯 ⇒ (0, "")(hook 不能擋那一席)。"""
    argv = list(argv)
    code = argv[argv.index("--seat") + 1] if "--seat" in argv[:-1] else None
    if not SHIP_GATE.is_file() or not ship_pending(code):
        return 0, ""
    import subprocess
    try:
        r = (run or subprocess.run)([sys.executable, str(SHIP_GATE)] + list(argv), input=text or "",
                                    capture_output=True, text=True, timeout=SHIP_GATE_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return 0, ""
    return r.returncode, (r.stdout or "").strip()


def _told_path(code):
    return state_root() / "state" / "auto-distill" / ("ship-told-%s.json" % code)


def ship_told_once(code, text, now=None):
    """同一段提醒只給一次(審查第 1、2 條:模型沒逐字貼就一直寄 / 一直續跑)。第一次 ⇒ 記下、回 True;之後 ⇒ False。
    之後還漏,交給 a seat 第 3 層巡查(它連兩次漏會通知 the maintainer)。記號 24 小時作廢(同紀錄的期限):
    兩次不同的 aib test 文字剛好一樣(例如兩次 --fresh 跑同樣支數)時,隔天那次照樣提醒(審查第二輪)。"""
    import hashlib
    now = time.time() if now is None else now
    h = hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12]
    p = _told_path(code)
    seen = _read_json(p, {})
    seen = seen if isinstance(seen, dict) else {}
    seen = {k: v for k, v in seen.items() if isinstance(v, (int, float)) and now - v <= SHIP_MAX_AGE_S}
    if h in seen:
        return False
    seen[h] = now
    _write_json(p, dict(sorted(seen.items(), key=lambda kv: kv[1])[-20:]))
    return True


def ship_report_stop(payload, rec, code, runner=None, gate=None):
    """Codex / Grok / Copilot 一輪結束:有回覆原文才問(check 沒原文會當空回覆判漏);漏了 ⇒ 等畫面閒才 dm 那一席。
    沒原文 ⇒ 不問,交給 a seat 第 3 層巡查(讀窗格)。→ 寄出的字或 ""。"""
    reply = reply_of(payload)
    if not reply:
        return ""
    waiting = not runner and waiter_alive(code, "ship")   # 上一段提醒還在等畫面閒
    rc, out = (gate or ship_gate)(["check", "--seat", code], reply)   # 照樣比對:這一輪貼了要算(審查第三輪)
    if waiting or rc != 2 or not out or not ship_told_once(code, out):
        return ""                                       # 等待中不再寄、也不記成提醒過(不然就吞掉了)
    deliver(rec.get("cabin_id") or "", code, out, runner, rec, kind="ship")
    _log({"seat": code, "action": "ship-report-missed", "provider": rec.get("provider")})
    return out


def ship_append(code, gate=None):
    """程式代補(API REPL、CodexBot / GrokBot / MuseBot 橋:回覆就畫在那一席畫面上):這一席有待回報的說明 ⇒
    回那段(呼叫端直接印在回覆下面)並記成已回報(--mark)。沒有 / 那支不在 / 任何錯 ⇒ ""。"""
    if not code:
        return ""
    try:
        _rc, out = (gate or ship_gate)(["segment", "--seat", code, "--mark"])
    except Exception:  # noqa: BLE001
        return ""
    return out


def ship_report_agy(code, st, gate=None):
    """agy 叫模型之前:有待回報的說明、而且這一段還沒提醒過 ⇒ 回要放進這一輪的提醒(同一段只提醒一次;
    貼了沒由 a seat 第 3 層巡查讀窗格判)。→ 字或 ""。"""
    rc, seg = (gate or ship_gate)(["segment", "--seat", code])
    if not seg:
        return ""
    import hashlib
    h = hashlib.sha1(seg.encode("utf-8")).hexdigest()[:12]
    if h in (st.get("ship_told") or []):
        return ""
    st["ship_told"] = (list(st.get("ship_told") or []) + [h])[-20:]
    import i18n_lib
    return T("auto_distill.ship_report", {"text": seg}, i18n_lib.resolve_locale())


def on_agy_pre(payload, now=None, seat=None, refresh=None, gate=None):
    """agy:Stop 實測從不觸發(a seat 10-13 16:53)⇒ 改掛 PreInvocation(每次叫模型前),用 ephemeralMessage 把指示
    放進這一輪(只在這一輪有效,不留在對話裡)。門檻同一套、同一門檻只一次。→ 要印的 JSON 字串(沒事 = "{}")。"""
    import seat_address
    rec = seat if seat is not None else seat_for_payload(payload)
    if not rec or rec.get("provider") != "agy":
        return "{}"
    sid = str(rec.get("session_uuid") or "")
    code = seat_address.uuid_tail5(sid)
    if not code or not _SID_RE.match(sid):
        return "{}"
    sp = state_path(code)
    st = _read_json(sp, {})
    if st.get("session") != sid:
        st = {"session": sid, "fired": []}
    texts = []
    try:
        ship = ship_report_agy(code, st, gate)          # 影子續跑說明:不看門檻,有待回報就提醒一次
    except Exception as exc:  # noqa: BLE001 —— 出錯不能連整理提醒一起沒了(審查第 6 條)
        _log({"seat": code, "action": "error", "mode": "agy-ship", "why": str(exc)[:200]})
        ship = ""
    pct = gauge_pct(sid, now)
    newly = [t for t in thresholds() if pct is not None and pct >= t and t not in (st.get("fired") or [])]
    if newly:
        st["fired"] = sorted(set(st.get("fired") or []) | set(newly))
        import i18n_lib
        tail = str(rec.get("cabin_id") or "").rsplit("-", 1)[-1][-4:]
        texts.append(with_inbox(T("auto_distill.instruct_bot", {"seat": "%s/%s" % (tail, code), "why": "%s%%" % pct},
                                  i18n_lib.resolve_locale()), code))
        _log({"seat": code, "pct": pct, "fired": newly, "action": "agy-inject"})
    if ship:
        texts.append(ship)
        _log({"seat": code, "action": "agy-ship-report"})
    if not texts:
        return "{}"
    _write_json(sp, st)
    return json.dumps({"injectSteps": [{"ephemeralMessage": t} for t in texts]}, ensure_ascii=False)


def on_stop(payload, now=None, seat=None, runner=None, refresh=None, gate=None):
    """→ 送出的 dm 內文(或空字串)。"""
    import seat_address
    if not real_turn_end(payload):
        return ""
    rec = seat if seat is not None else seat_for_payload(payload)
    if not rec or rec.get("provider") == "claude":         # Claude 席走 prompt 那條
        return ""
    sid = str(rec.get("session_uuid") or "")
    code = seat_address.uuid_tail5(sid)
    if not code or not _SID_RE.match(sid):
        return ""
    try:
        ship_report_stop(payload, rec, code, runner, gate)   # 第 2 層影子續跑說明;另一件事,不影響下面的整理提醒
    except Exception as exc:  # noqa: BLE001
        _log({"seat": code, "action": "error", "mode": "ship-report", "why": str(exc)[:200]})
    (refresh or refresh_gauge)(rec.get("provider"), sid)
    sp = state_path(code)
    st = _read_json(sp, {})
    if st.get("session") != sid:
        st = {"session": sid, "fired": []}
    pct = gauge_pct(sid, now)
    newly = [t for t in thresholds() if pct is not None and pct >= t and t not in (st.get("fired") or [])]
    import i18n_lib
    pending = _read_json(due_path(code), {})
    if not newly:
        # 上次等不到閒 ⇒ 再起一支。只看「等的那支不在」不夠:它剛起、還沒寫 .waiter(或測試注入 runner 根本沒起)
        # 就會被當成放棄 ⇒ 同一門檻寄兩封(a seat 10-13 跑出 test_refresh_then_threshold_dm 紅)。要過了放棄時間才算。
        t = now or time.time()
        if pending.get("session") == sid and dm_allowed() and not waiter_alive(code) \
                and t - float(pending.get("at") or 0) >= IDLE_GIVE_UP_S:
            text = T("auto_distill.instruct_dm", {"pct": pending.get("pct")}, i18n_lib.resolve_locale())
            write_due(code, sid, pending.get("pct"), pending.get("thresholds") or [], at=t)
            deliver(rec.get("cabin_id") or "", code, text, runner, rec)
            return text
        return ""
    st["fired"] = sorted(set(st.get("fired") or []) | set(newly))
    _write_json(sp, st)
    text = with_inbox(T("auto_distill.instruct_dm", {"pct": pct}, i18n_lib.resolve_locale()), code)
    write_due(code, sid, pct, newly)
    if not dm_allowed():
        _log({"seat": code, "pct": pct, "fired": newly, "action": "due-only", "provider": rec.get("provider")})
        return ""
    deliver(rec.get("cabin_id") or "", code, text, runner, rec)
    _log({"seat": code, "pct": pct, "fired": newly, "action": "dm", "provider": rec.get("provider")})
    return text


BOT_EVERY = 20       # 量不到用量 % 的 Bot:每 20 輪(the maintainer 10-13 14:4x 照 A)


def bot_turn(code, cabin, gauge_sid=None, now=None):
    """Bot 一輪結束時呼叫(claudebot_hook on_stop、codexbot / grokbot / musebot 橋)。
    gauge_sid 有新鮮用量 ⇒ 照 30/50/70;沒有 ⇒ 每 BOT_EVERY 輪。→ 要交給那個 Bot 的指示(或空字串)。"""
    if not code or os.environ.get("AIB_AUTO_DISTILL") == "0":
        return ""
    sp = state_path("bot-%s" % code)
    st = _read_json(sp, {})
    st["turns"] = int(st.get("turns") or 0) + 1
    pct = gauge_pct(gauge_sid, now) if gauge_sid and _SID_RE.match(gauge_sid) else None
    due, why = False, ""
    if pct is not None:
        if st.get("gauge_sid") != gauge_sid:
            st["gauge_sid"], st["fired"] = gauge_sid, []
        newly = [t for t in thresholds() if pct >= t and t not in (st.get("fired") or [])]
        if newly:
            st["fired"] = sorted(set(st.get("fired") or []) | set(newly))
            due, why = True, "pct %s" % pct
    elif st["turns"] % BOT_EVERY == 0:
        due, why = True, "turn %d" % st["turns"]
    _write_json(sp, st)
    if not due:
        return ""
    import i18n_lib
    tail = str(cabin or "").rsplit("-", 1)[-1][-4:]
    if not tail:                                       # 沒艙位碼 ⇒ --seat 會被拒;不送(審查:橋洗掉了 EZ2_AIB_INSTANCE)
        _log({"seat": code, "action": "bot-no-cabin", "why": why})
        return ""
    target = "%s/%s" % (tail, code)
    _log({"seat": code, "action": "bot", "why": why})
    return with_inbox(T("auto_distill.instruct_bot", {"seat": target, "why": why}, i18n_lib.resolve_locale()), code)


COPILOT_HOOK_NAME = "aib-auto-distill.json"
AGY_HOOK_NAME = "aib-auto-distill"


def _stop_cmd(script, extra=""):
    q = shlex.quote(script)
    return "test -f %s || exit 0; python3 %s stop%s; exit 0" % (q, q, extra)   # 一律 0:Copilot hook 非 0 = fail-closed(a seat 16:45)


def ensure_copilot(hooks_dir=None, script=None):
    """~/.copilot/hooks/aib-auto-distill.json(a seat / a seat 10-13 實測:version 1、事件 agentStop、欄位 bash + timeoutSec;
    使用者層不用重新信任,新開的對話才吃得到)。"""
    if os.environ.get("AIB_AUTO_DISTILL") == "0":
        return "off", "AIB_AUTO_DISTILL=0"
    script = script or canonical_script()
    if not script or not os.path.isfile(script):
        return "skip", "正本樹沒有 %s" % MARKER
    want = {"version": 1, "hooks": {"agentStop": [{"type": "command", "bash": _stop_cmd(script),
                                                  "timeoutSec": HOOK_TIMEOUT_S}]}}
    p = Path(hooks_dir or os.path.expanduser("~/.copilot/hooks")) / COPILOT_HOOK_NAME
    if _read_json(p, None) == want:
        return "ok", "已裝"
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p, want)
    return "installed", _installed("Copilot", p)


def ensure_agy(path=None, script=None):
    """~/.gemini/config/hooks.json 裡具名 aib-auto-distill 的 PreInvocation(Stop 實測不觸發;平的 handler 清單;
    別人的具名 hook 不動、先備份)。"""
    if os.environ.get("AIB_AUTO_DISTILL") == "0":
        return "off", "AIB_AUTO_DISTILL=0"
    script = script or canonical_script()
    if not script or not os.path.isfile(script):
        return "skip", "正本樹沒有 %s" % MARKER
    p = Path(path or os.path.expanduser("~/.gemini/config/hooks.json"))
    cfg = _read_json(p, {}) if p.exists() else {}
    if not isinstance(cfg, dict):
        return "skip", "hooks.json 格式不認得"
    q = shlex.quote(script)
    want = {"PreInvocation": [{"type": "command", "timeout": HOOK_TIMEOUT_S,
                               "command": "test -f %s || { echo '{}'; exit 0; }; exec python3 %s agy-pre" % (q, q)}]}
    if cfg.get(AGY_HOOK_NAME) == want:
        return "ok", "已裝"
    if p.exists():
        shutil.copy2(p, str(p) + ".bak-auto-distill-%s" % time.strftime("%Y%m%d-%H%M%S"))
    cfg[AGY_HOOK_NAME] = want
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p, cfg)
    return "installed", _installed("agy", p)


def ensure_codex(path=None, script=None):
    """~/.codex/hooks.json 的 Stop 加一組(別人的不動、先備份)。新 hook 要 the maintainer 在 Codex /hooks 信任一次才會跑
    (a seat 10-13 17:27 查證:trusted_hash 記在 ~/.codex/config.toml,定義改了要重審)。"""
    if os.environ.get("AIB_AUTO_DISTILL") == "0":
        return "off", "AIB_AUTO_DISTILL=0"
    script = script or canonical_script()
    if not script or not os.path.isfile(script):
        return "skip", "正本樹沒有 %s" % MARKER
    group = {"hooks": [{"type": "command", "command": _stop_cmd(script), "timeout": HOOK_TIMEOUT_S,
                        "statusMessage": "AIB auto-distill"}]}
    p = Path(path or os.path.expanduser("~/.codex/hooks.json"))
    cfg = _read_json(p, {}) if p.exists() else {}
    hooks = cfg.setdefault("hooks", {}) if isinstance(cfg, dict) else None
    if not isinstance(hooks, dict):
        return "skip", "hooks.json 格式不認得"
    groups = hooks.get("Stop") or []
    if group in groups:
        return "ok", "已裝"
    if p.exists():
        shutil.copy2(p, str(p) + ".bak-auto-distill-%s" % time.strftime("%Y%m%d-%H%M%S"))
    hooks["Stop"] = [g for g in groups if MARKER not in json.dumps(g)] + [group]
    _write_json(p, cfg)
    return "installed", _installed("Codex", p, T("auto_distill.trust_codex", {}, None))


REVIEW_GATE_MAX = 2          # 同一張交接收據最多擋兩次(不卡死)


def on_review_gate(payload, now=None):
    """§8e.7(the maintainer 10-13 17:5x「Claude 席沒做回顧擋結束」照預設):這一輪交接 verify 前 8 項都過、還沒做遺漏回顧
    ⇒ Claude Stop 回 block + 回顧指示。stop hook 自己續的那輪照擋(回顧沒做完),但同一張收據最多擋兩次。→ JSON 字串或 ""。"""
    import seat_address
    sid = str(payload.get("session_id") or "")
    if not _SID_RE.match(sid) or foreign_host() or not is_aib_seat(sid):
        return ""
    code = seat_address.uuid_tail5(sid)
    try:
        sys.path.append(str(SRC / "cockpit"))
        import aib_handoff
        import handoff_review
        rec = aib_handoff._read_json(aib_handoff.receipt_path(code)) or {}
        start = aib_handoff.current_round_start(code)
    except Exception:  # noqa: BLE001
        return ""
    if not rec or float(rec.get("ts") or 0) < start:
        return ""
    try:                                               # 當下重驗(收據裡的是第 3 步那時的;之後補好了也要認;三輪審查 4)
        import handoff_check
        items = handoff_check.verify(code, review=False)
    except Exception:  # noqa: BLE001
        return ""
    if len(items) < 8 or not all(ok for _n, ok, _d in items[:8]):
        return ""                                      # 前 8 項還沒全過:那是補缺項的事,不是回顧
    if handoff_review.done_this_round(code, start):
        return ""
    gp = state_path("review-gate-%s" % code)
    g = _read_json(gp, {})
    if g.get("receipt_ts") != rec.get("ts"):
        g = {"receipt_ts": rec.get("ts"), "blocks": 0}
    if g["blocks"] >= REVIEW_GATE_MAX:
        return ""
    g["blocks"] += 1
    _write_json(gp, g)
    _log({"seat": code, "action": "review-gate-block", "n": g["blocks"]})
    return json.dumps({"decision": "block", "reason": T("aib.handoff.verify.review_next", {}, None)}, ensure_ascii=False)


GROK_HOOK_NAME = "aib-auto-distill.json"


def ensure_grok(hooks_dir=None):
    """~/.grok/hooks/aib-auto-distill.json(全域、免信任;Claude 格式的 Stop)。內容一樣就不動。"""
    if os.environ.get("AIB_AUTO_DISTILL") == "0":
        return "off", "AIB_AUTO_DISTILL=0"
    try:
        script = canonical_script()
        if not script or not os.path.isfile(script):
            return "skip", "正本樹沒有 %s" % MARKER
        q = shlex.quote(script)
        want = {"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": HOOK_TIMEOUT_S,
                                                "command": "test -f %s || exit 0; exec python3 %s stop" % (q, q)}]}]}}
        d = Path(hooks_dir or os.path.expanduser("~/.grok/hooks"))
        p = d / GROK_HOOK_NAME
        if _read_json(p, None) == want:
            return "ok", "已裝"
        d.mkdir(parents=True, exist_ok=True)
        _write_json(p, want)
    except Exception as exc:  # noqa: BLE001
        return "skip", "裝不上:%s" % exc
    return "installed", _installed("Grok", p)


# ---------------------------------------------------------------- ensure(沿用 aib-now-gate 的讀寫與正本判斷)
DEFAULT_SETTINGS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                                "settings.json")


def _now_gate():
    spec = importlib.util.spec_from_file_location("aib_now_gate", str(SRC / "policy" / "hooks" / "aib-now-gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def canonical_script(gate=None):
    gate = gate or _now_gate()
    p = gate.canonical_script()
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(p))), "identity", MARKER) if p else None


def hook_entry(script):
    q = shlex.quote(script)
    return {"type": "command", "command": "test -f %s || exit 0; exec python3 %s prompt" % (q, q),
            "timeout": HOOK_TIMEOUT_S}


def _is_ours(h):
    return isinstance(h, dict) and MARKER in str(h.get("command") or "")


def ensure(path=DEFAULT_SETTINGS):
    """回 (做了什麼, 說明);任何問題都不丟例外 —— 開席不能因為它失敗。AIB_AUTO_DISTILL=0 關。"""
    if os.environ.get("AIB_AUTO_DISTILL") == "0":
        return "off", "AIB_AUTO_DISTILL=0"
    try:
        gate = _now_gate()
        script = canonical_script(gate)
        if not script or not os.path.isfile(script):
            return "skip", "正本樹沒有 %s" % MARKER
        before = gate._stamp_of(path)
        cfg = gate._load(path)
        hooks = cfg.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            return "skip", "hooks 不是物件"
        want = hook_entry(script)
        groups = hooks.get("UserPromptSubmit") or []
        mine = [h for g in groups if isinstance(g, dict) for h in g.get("hooks") or [] if _is_ours(h)]
        stop_mine = [h for g in hooks.get("Stop") or [] if isinstance(g, dict) for h in g.get("hooks") or [] if _is_ours(h)]
        if mine == [want] and len(stop_mine) == 1 and "review-gate" in str(stop_mine[0].get("command")):
            return "ok", "已裝"
        kept = []
        for g in groups:
            if not isinstance(g, dict):
                kept.append(g)
                continue
            hs = [h for h in g.get("hooks") or [] if not _is_ours(h)]
            if hs:
                kept.append(dict(g, hooks=hs))
        kept.append({"hooks": [want]})
        hooks["UserPromptSubmit"] = kept
        q = shlex.quote(script)                         # §8e.7 回顧擋結束(Claude Stop)
        stop_hook = {"type": "command", "command": "test -f %s || exit 0; exec python3 %s review-gate" % (q, q),
                     "timeout": HOOK_TIMEOUT_S}
        stops = [g for g in hooks.get("Stop") or [] if not (isinstance(g, dict) and
                 any(_is_ours(h) for h in g.get("hooks") or []))]
        hooks["Stop"] = stops + [{"hooks": [stop_hook]}]
        if gate._stamp_of(path) != before:
            return "skip", "%s 剛被改過" % path
        backup = gate._save(path, cfg)
    except Exception as exc:  # noqa: BLE001
        return "skip", "裝不上:%s" % exc
    return "installed", _installed("Claude", path, T("auto_distill.backup", {"path": backup}, None) if backup else "")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else ""
    if mode == "prompt":
        try:
            line = on_prompt(json.loads(sys.stdin.read() or "{}"))
            if line:
                print(line)
        except Exception as exc:  # noqa: BLE001 —— 不能擋人講話
            _log({"action": "error", "why": str(exc)[:200]})
        return 0
    if mode == "wait-send" and len(argv) >= 7:
        try:
            wait_idle_then_send(argv[1], argv[2], argv[3], argv[4], argv[6], argv[5],
                                kind=argv[7] if len(argv) >= 8 else "tidy")
        except Exception as exc:  # noqa: BLE001
            _log({"action": "error", "mode": "wait-send", "why": str(exc)[:200]})
        return 0
    if mode == "review-gate":
        try:
            out = on_review_gate(json.loads(sys.stdin.read() or "{}"))
        except Exception as exc:  # noqa: BLE001 —— 擋不了就放行
            _log({"action": "error", "mode": "review-gate", "why": str(exc)[:200]})
            out = ""
        if out:
            print(out)
        return 0
    if mode == "agy-pre":
        try:
            print(on_agy_pre(json.loads(sys.stdin.read() or "{}")))
        except Exception as exc:  # noqa: BLE001 —— 不能擋模型呼叫
            _log({"action": "error", "mode": "agy-pre", "why": str(exc)[:200]})
            print("{}")
        return 0
    if mode == "stop":
        try:
            on_stop(json.loads(sys.stdin.read() or "{}"))
        except Exception as exc:  # noqa: BLE001 —— 不能擋一輪結束
            _log({"action": "error", "mode": "stop", "why": str(exc)[:200]})
        if "--agy" in argv:
            print('{"decision": "allow"}')           # agy 的 Stop 要一段 JSON;continue 會卡住這一輪(a seat 10-13)
        return 0
    if mode in ("ensure-copilot", "ensure-agy", "ensure-codex"):
        did, why = {"ensure-copilot": ensure_copilot, "ensure-agy": ensure_agy, "ensure-codex": ensure_codex}[mode]()
        if did == "installed":
            print(why, file=sys.stderr)
        return 0
    if mode == "ensure-grok":
        did, why = ensure_grok(argv[argv.index("--dir") + 1] if "--dir" in argv[:-1] else None)
        if did == "installed":
            print(why, file=sys.stderr)
        return 0
    if mode == "ensure":
        path = argv[argv.index("--settings") + 1] if "--settings" in argv[:-1] else DEFAULT_SETTINGS
        did, why = ensure(path)
        if did == "installed":
            print(why, file=sys.stderr)
        return 0
    print(T("auto_distill.usage", {}, None), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
