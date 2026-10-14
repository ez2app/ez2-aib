#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ClaudeBot hook:Claude Code(Claude app 的本機 session 或終端機 claude)↔ AIB 席(設計頁 docs/CLAUDEBOT-DESKTOP.md)。

掛在 ~/.claude/settings.json(`python3 claudebot_hook.py install`),一支檔依第一個參數分流:

    session-start  SessionStart:綁著的話,把待收的信交給 Claude(stdout = 對話上下文)
    prompt         UserPromptSubmit:the maintainer 在 app 打的那句 post 到 AIB 席(the maintainer(app)>);待收的信一起交給 Claude
    stop           Stop:這一回合的文字回覆 post 到 AIB 席(claudebot>);inbox 還有值得叫醒的信 ⇒ exit 2 不讓它停,把信交給它
    doorbell       Stop / SessionStart 的 async + asyncRewake:背景盯著本席 inbox,有信 ⇒ 收下、exit 2 叫醒閒著的 Claude

★ 全域 hook 會跑在**每一條**本機 Claude session 上(包括 AIB 自己開的 claude 席)⇒ 第一步一律查綁定,
  沒綁就 exit 0、零輸出、零寫檔。任何意外一律放行(exit 0)並留痕:守門程式壞掉不能把全艦隊的 Claude 卡死。
★ 回覆由 hook 從 transcript 取,不靠 Claude 自己記得回信(GrokBot 真機有沒照做的時候)。
★ 只 post 文字回覆,不 post 工具動作(the maintainer 09-26 待裁第 4 題預設)。
"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

EVENTS = ("session-start", "prompt", "stop", "doorbell")
LOG_NAME = "claudebot-hook.jsonl"
MARK_POSTED = "claude-last-posted"     # 席目錄:上次 post 到的 assistant 項目 uuid(防同一段回兩次)
DOORBELL_FILE = "claude-doorbell"      # 席目錄:現役門鈴的 pid + 一次性記號(新門鈴頂替舊的)
ENV_DOORBELL_S = "AIB_CLAUDEBOT_DOORBELL_S"
DOORBELL_S = 3300.0                    # 安裝時 async hook 的 timeout 給 3600,門鈴自己先退
DOORBELL_POLL_S = 0.5
DOORBELL_FIRST_WAIT_S = 1.5            # 讓同步 Stop hook 先收(兩個同時在 Stop 起跑)
DOORBELL_SEAT_CHECK_S = 5.0
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024
HOOK_TIMEOUT_S = {"session-start": 10, "prompt": 10, "stop": 15, "doorbell": 3600}
STOP_FLUSH_WAIT_S = 2.4          # Stop 時等 transcript 把最後一段寫完(AIB_CLAUDEBOT_STOP_WAIT_S 覆寫)
STOP_FLUSH_POLL_S = 0.3


def _stop_flush_wait() -> float:
    try:
        return max(0.0, float(os.environ.get("AIB_CLAUDEBOT_STOP_WAIT_S", STOP_FLUSH_WAIT_S)))
    except ValueError:
        return STOP_FLUSH_WAIT_S


def _state_root() -> Path:
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def log(event: str, session_id: str, action: str, **extra) -> None:
    """只落動作與長度,不落對話內容。寫不進去不擋。"""
    try:
        d = _state_root() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        row = {"ts": round(time.time(), 3), "event": event, "session": (session_id or "")[:8], "action": action}
        row.update(extra)
        with (d / LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def maybe_bound(session_id: str) -> bool:
    """快篩:這條 session 有沒有被任何 claudebot 席綁過(席開時建鎖檔)。沒有 ⇒ 不用 import 其餘模組。"""
    import claudebot_threads
    if not claudebot_threads.valid_session_id(session_id):
        return False
    try:
        return claudebot_threads.lock_path(session_id, _state_root()).exists()
    except ValueError:
        return False


def bound_seat(session_id: str) -> bool:
    """現在真的有一隻活 claudebot 席綁著這條 session。

    鎖檔在席關掉後還留著(flock 的檔不刪)⇒ 只看鎖檔,曾經綁過的對話會在之後每一句都跑進處理、寫失敗紀錄。
    所以快篩過了還要確認活席在;不在一樣零輸出、零寫檔。"""
    if not maybe_bound(session_id):
        return False
    if in_aib_seat_pane():
        # 10-07:這支 hook 跑在 AIB 席位窗格裡(終端機 claude 席)⇒ 這條對話是 AIB 自己的席,不是 claudebot 綁的 app 對話。
        #   就算有人把它綁上去(舊版選單列得出來),也不收信、不回信,免得兩席串線。只有確認綁過才走到這裡,沒綁的零成本。
        log("guard", session_id, "skip-aib-seat-pane")
        return False
    import claudebot_mailbox as cm
    return cm.seat(session_id) is not None


def in_aib_seat_pane() -> bool:
    """hook 的行程是不是在 AIB 的席位窗格裡:席位窗格都在 `tmux -L aihub-<艙>` 伺服器上,TMUX 變數第一段就是那個 socket 路徑。
    (EZ2_AIB_INSTANCE 開席器會洗掉,不能拿來判;TMUX 是 tmux 自己給的。)"""
    sock = (os.environ.get("TMUX") or "").split(",", 1)[0]
    return Path(sock).name.startswith("aihub-") if sock else False


# ── transcript:取這一回合的文字回覆 ──────────────────────────────────────────

def _read_tail_entries(path: Path) -> list[dict]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > TRANSCRIPT_TAIL_BYTES:
                fh.seek(size - TRANSCRIPT_TAIL_BYTES)
                fh.readline()   # 丟掉被切開的半行
            raw = fh.read()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _is_real_prompt(entry: dict) -> bool:
    """真人打的那一句(不是工具結果、不是系統塞的)。"""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def _assistant_text(entry: dict) -> str:
    if entry.get("type") != "assistant":
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def reply_after_last_prompt(entries: list[dict]) -> bool:
    """這一回合(最後一句真人 prompt 之後)已經有 assistant 文字落檔了嗎。沒有 ⇒ Stop 來早了,要等。"""
    start = 0
    for i, e in enumerate(entries):
        if _is_real_prompt(e):
            start = i + 1
    return any(_assistant_text(e).strip() for e in entries[start:])


def new_reply(entries: list[dict], last_posted: str) -> tuple[str, str]:
    """(這次要 post 的文字, 最後一個 assistant 項目的 uuid)。

    起點:上次 post 到的 uuid 之後;找不到那個 uuid(第一次、或被截掉)⇒ 最後一句真人 prompt 之後。
    只收 assistant 的 text block;工具呼叫、工具結果不收。"""
    start = None
    if last_posted:
        for i, e in enumerate(entries):
            if e.get("uuid") == last_posted:
                start = i + 1
    if start is None:
        start = 0
        for i, e in enumerate(entries):
            if _is_real_prompt(e):
                start = i + 1
    parts, last_uuid = [], ""
    for e in entries[start:]:
        if e.get("type") != "assistant":
            continue
        t = _assistant_text(e).strip()
        if e.get("uuid"):
            last_uuid = e["uuid"]
        if t:
            parts.append(t)
    return "\n\n".join(parts), last_uuid


# ── 各事件 ──────────────────────────────────────────────────────────────────

TITLE_MARK = "claude-titled"


UNBOUND_TITLE_FMT = "%s ( 未綁定AIB席位 )"   # 與 bot_seat_name.UNBOUND_TEXT 同字(codexbot/grokbot 關席收尾同一句)


def bound_title(project: str, code: str) -> str:
    """綁定後的標題:「項目名稱 ( 🔗 @席碼 )」,與 codexbot / grokbot 同一支 bot_seat_name.with_seat_tag。
    10-10 a seat(待修清單第 20 件,the maintainer「第 20 件改,三家統一」):以前 claudebot 寫「項目名稱 @席碼」,
    三家格式不一,在各 app 搜席碼容易漏。席碼形狀不合 with_seat_tag(4–8 位小寫英數)⇒ 退回舊寫法,不讓標題整個不寫。"""
    try:
        import bot_seat_name
        return bot_seat_name.with_seat_tag(project, code, fallback="aib")
    except (ImportError, ValueError):
        return "%s @%s" % (project, code)


def release_title(sid: str, state_dir: Path, transcript_path: str = "") -> bool:
    """10-09 a seat(the maintainer 令「關席時標題改回未綁定,一起做」):把 ensure_title 寫的「項目名稱 ( 🔗 @席碼 )」改回
    「項目名稱 ( 未綁定AIB席位 )」。codexbot、grokbot 關席都會改回,claudebot 以前不改 ⇒ 關席後標題一直掛著死席碼。
    只在這一席真的替它改過標題時做(state_dir/claude-titled 有席碼);改完把標記的席碼清空,下次再綁同一席也會重寫。
    transcript 找不到(雲端 session、檔被刪)就不做。由收尾小幫手(bot_closer close_claudebot)與席上 /unbind 呼叫。"""
    mark = state_dir / TITLE_MARK
    try:
        parts = mark.read_text(encoding="utf-8").strip().split("\t")
        project, code = parts[0], parts[1]
        src = parts[2] if len(parts) > 2 else "dir"
    except (OSError, IndexError):
        return False
    if not code:
        return False
    tp = Path(transcript_path) if transcript_path else None
    if not tp or not tp.exists():
        import claudebot_threads
        tp = claudebot_threads.transcript_path(sid)
    if not tp or not tp.exists():
        return False
    title = UNBOUND_TITLE_FMT % (project or "aib")
    try:
        with tp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}, ensure_ascii=False) + "\n")
            fh.write(json.dumps({"type": "agent-name", "agentName": title, "sessionId": sid}, ensure_ascii=False) + "\n")
        side = tp.parent / sid
        side.mkdir(parents=True, exist_ok=True)
        (side / "custom-title.json").write_text(json.dumps({"customTitle": title}, ensure_ascii=False), encoding="utf-8")
        mark.write_text("%s\t\t%s\n" % (project, src), encoding="utf-8")
        log("title", sid, "unbound", chars=len(title))
        return True
    except OSError as exc:
        log("title", sid, "unbind-title-failed", err=type(exc).__name__)
        return False


def _seat_project(code) -> str:
    try:
        import claudebot_threads
        return claudebot_threads.seat_project_label(str(code or ""))
    except Exception:  # noqa: BLE001 —— 查不到就退回資料夾名,不擋 hook
        return ""


DESKTOP_ENTRYPOINT = "claude-desktop"


def _is_desktop_session(transcript_path) -> bool:
    """這條對話是不是 Claude 桌面 app(Code 頁)開的:transcript 前幾行的 entrypoint。"""
    try:
        with Path(transcript_path).open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 40:
                    break
                if '"entrypoint"' in line:
                    try:
                        return json.loads(line).get("entrypoint") == DESKTOP_ENTRYPOINT
                    except ValueError:
                        continue
    except OSError:
        return False
    return False


def desktop_title_note(title: str) -> str:
    """交給桌面 app 裡的 Claude 的那一句(10-09 a seat 建議、a seat 實作):app 不讀 AIB 寫進 transcript 的 custom-title,
    但 app 給 Claude 一個改自己標題的工具(ccd_session_mgmt.set_session_title)。開頭是 [AIB ClaudeBot] ⇒ is_injected_prompt 認得。"""
    return ("[AIB ClaudeBot] 這條對話綁在 AIB 席。請用 app 的改標題工具(ccd_session_mgmt 的 set_session_title,session_id=\"self\")"
            "把這條對話的標題改成「%s」;改好不用回報、不用 dm,接著照常回答。\n" % title)


def ensure_title(sid: str, code: str, cwd: str, transcript_path: str, state_dir: Path, project: str = "",
                 want_note: bool = False):
    """綁定後把對話標題改成「項目名稱 ( 🔗 @席碼 )」(the maintainer 09-27 15:2x;三家 bot 同一規則 #366;10-10 起與另兩家同格式,見 bound_title)。
    /rename 的落點(09-27 實測):transcript 尾巴兩行 custom-title / agent-name + <project>/<sid>/custom-title.json。
    同一個標題只寫一次(state_dir/claude-titled 記上次寫的標題);換綁到別的席(席碼變了)就再寫一次(r1 L4 後半)。
    transcript 不在就不做。項目名稱 v1 = 工作目錄名。"""
    if not code:
        return False
    mark = state_dir / TITLE_MARK
    # r5' E1(a seat 19:56):Claude Code 的 cwd 會跟著 cd 走 ⇒ 項目名稱只在第一次定下來,之後只比席碼。
    #   標記檔記「項目名稱\t席碼」;席碼同就不動,席碼變(換綁)才用原項目名稱重寫。
    # 項目名稱:席綁的專案(project,由呼叫端從 seat-display 查;雲端由中繼帶過來)> 第一次的資料夾名。
    #   綁的專案變了 ⇒ 標題跟著改;資料夾名只在第一次定(cwd 會跟著 cd 走)。
    bound = (project or "").strip()
    project = bound or (Path(cwd).name if cwd else "") or "aib"
    try:
        parts = mark.read_text(encoding="utf-8").strip().split("\t")
        prev_project, prev_code = parts[0], parts[1]
        prev_src = parts[2] if len(parts) > 2 else "dir"
        if prev_code == code and (not bound or prev_project == bound) and (bound or prev_src != "bound"):
            return False
        if not bound and prev_src != "bound":
            project = prev_project or project      # 資料夾名只在第一次定
        # r11 低:上次是綁的專案、現在解除綁定 ⇒ 退回(這次的)資料夾名,不留舊專案
    except (OSError, ValueError, IndexError):
        pass
    title = bound_title(project, code)
    tp = Path(transcript_path) if transcript_path else None
    if not tp or not tp.exists():
        return False
    if _is_desktop_session(tp):
        # 10-09:桌面 app 的對話寫 transcript 沒用(app 每輪自己寫標題)⇒ 請 Claude 用 app 的工具改。
        #   只在交得到 Claude 的那一步(prompt 事件 stdout)做;Stop 的 stdout 不進上下文,這裡記了標記句子卻沒送到。
        if not want_note:
            return False
        try:
            mark.write_text("%s\t%s\t%s\n" % (project, code, "bound" if bound else "dir"), encoding="utf-8")
        except OSError:
            return False
        log("title", sid, "asked-desktop", chars=len(title))
        return desktop_title_note(title)
    try:
        with tp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}, ensure_ascii=False) + "\n")
            fh.write(json.dumps({"type": "agent-name", "agentName": title, "sessionId": sid}, ensure_ascii=False) + "\n")
        side = tp.parent / sid
        side.mkdir(parents=True, exist_ok=True)
        (side / "custom-title.json").write_text(json.dumps({"customTitle": title}, ensure_ascii=False), encoding="utf-8")
        mark.write_text("%s\t%s\t%s\n" % (project, code, "bound" if bound else "dir"), encoding="utf-8")
        log("title", sid, "renamed", chars=len(title))
        return True
    except OSError as exc:
        log("title", sid, "rename-failed", err=type(exc).__name__)
        return False


def _deliver(session_id: str) -> str:
    """收下 inbox 的信,回交給 Claude 的那一段;沒信回空字串。"""
    import claudebot_mailbox as cm
    rc, msgs, code = cm.pull(session_id)
    if rc != cm.RC_OK or not msgs:
        return ""
    found = cm.seat(session_id)
    text = cm.render(msgs, code, session_id, cm.token_of(session_id),
                     rules=cm.rules_for(found[0] if found else None, session_id))   # 10-09:同一條對話規則只送一次完整版
    n = cm.mark_queue_read(code, msgs)   # 10-11 第 19 件:交到 Claude 手上 ⇒ dm 佇列那一筆標已讀
    if n:
        log("deliver", session_id, "queue-read", n=n)
    return text


def _reinject(session_id: str, source: str) -> str:
    """the maintainer 09-30 19:5x:對話被壓縮/清除後,把這一席的基因+人格包補回(bridge_persona 組字);其他 source ⇒ ""。"""
    if source not in ("compact", "clear"):
        return ""
    import claudebot_mailbox as cm
    # ★ review r3 m6:人格包那一層壞掉(模組載不進來、組字丟例外)只丟人格這一段 —— 否則例外一路冒到 run_hook,
    #   on_session_start 後面那段收信(_deliver)也跟著不跑。同 review r1 m7「人格包送不出去不能讓橋停」。
    try:
        import bridge_persona
        found = cm.seat(session_id)
        return bridge_persona.reinject_for_claude_session(found[1], source) if found else ""
    except Exception as exc:  # noqa: BLE001 —— 人格包壞掉只丟人格包
        log("session-start", session_id, "persona-reinject-error", err=type(exc).__name__)
        return ""


def on_session_start(payload: dict) -> int:
    sid = payload.get("session_id") or ""
    if str(payload.get("source") or "") in ("compact", "clear"):
        # 10-09:壓縮/清除後 Claude 不記得規則 ⇒ 下一封(含這一次 _deliver)給完整版
        import claudebot_mailbox as cm
        found = cm.seat(sid)
        cm.forget_rules_sent(found[0] if found else None)
    pack = _reinject(sid, str(payload.get("source") or ""))
    if pack:
        sys.stdout.write(pack + "\n")
        log("session-start", sid, "persona-reinjected", chars=len(pack))
    text = _deliver(sid)
    if text:
        sys.stdout.write(text)
        log("session-start", sid, "delivered", chars=len(text))
    return 0


INJECTED_PREFIXES = ("<task-notification", "<system-reminder", "<local-command", "<command-name",
                     "[AIB ClaudeBot]", "規則(AIB 定的")   # 系統/hook 塞進來的,不是 the maintainer 打的(具名標籤,裸 < 不算:a seat 04:16)


def is_injected_prompt(prompt: str) -> bool:
    """門鈴 exit 2 的那段、task-notification、system-reminder 會以 prompt 事件再進來一次;
    它們不是 the maintainer 在 app 打的,不能 post 成 the maintainer(app)>(真機 09-27 03:56 抓到:整段規則文被貼成 the maintainer 的話)。"""
    head = prompt.lstrip()[:40]
    return any(head.startswith(x) for x in INJECTED_PREFIXES)


def on_prompt(payload: dict) -> int:
    import claudebot_mailbox as cm
    sid = payload.get("session_id") or ""
    found = cm.seat(sid)
    if found:
        note = ensure_title(sid, str(found[1].get("code") or ""), str(payload.get("cwd") or ""), str(payload.get("transcript_path") or ""), found[0],
                            _seat_project(found[1].get("code")), want_note=True)
        if isinstance(note, str) and note:
            sys.stdout.write(note)
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.strip() and is_injected_prompt(prompt):
        log("prompt", sid, "skipped-injected", chars=len(prompt))   # 留痕:被判成注入的那條路徑不能零 log
    elif isinstance(prompt, str) and prompt.strip():
        # 10-07:開頭是 [AIB] 的是 AIB 替你送進來的(代開的第一句、門鈴過期叫醒),席上標成「AIB 系統」不標成你
        src = "system" if prompt.lstrip().startswith("[AIB]") else "app"
        rc, _code = cm.post(sid, prompt, role="user", source=src, token=cm.token_of(sid))
        log("prompt", sid, "posted-user" if rc == cm.RC_OK else "post-failed", rc=rc, chars=len(prompt))
    text = _deliver(sid)
    if text:
        sys.stdout.write(text)
        log("prompt", sid, "delivered", chars=len(text))
    return 0


def post_reply(payload: dict, seat_dir: Path, post_fn=None) -> None:
    """post_fn(text) -> rc;沒給就走本機信箱。雲端版給中繼的投遞函式,其餘(transcript、標記)兩邊同一份。"""
    import claudebot_mailbox as cm
    sid = payload.get("session_id") or ""
    if post_fn is None:
        post_fn = lambda text: cm.post(sid, text, role="claude", source="aib", token=cm.token_of(sid))[0]
    mark = seat_dir / MARK_POSTED
    try:
        last = mark.read_text(encoding="utf-8").strip()
    except OSError:
        last = ""
    tp = payload.get("transcript_path")
    entries = _read_tail_entries(Path(tp)) if isinstance(tp, str) and tp else []
    text, last_uuid = new_reply(entries, last)
    # Stop 事件到的時候,這一回合最後那段 assistant 文字可能還沒落到 transcript(U7,真機 09-27 03:55/03:56
    # 兩次都是這樣:手動重跑同一份 transcript 就 post 得出來)。短暫重讀,上限遠小於 Stop 的 15 秒逾時。
    # 真機 04:05 再抓到一形:Stop 時 transcript 有「上一輪漏的舊段」但這一輪那段還沒到 ⇒ 只等「空」不夠,
    # 要等到尾端不再長(連續兩次重讀一樣)才取。
    deadline = time.monotonic() + _stop_flush_wait()
    settled = 0
    while entries and time.monotonic() < deadline:
        if reply_after_last_prompt(entries) and settled >= 1:
            break
        time.sleep(STOP_FLUSH_POLL_S)
        entries2 = _read_tail_entries(Path(tp))
        text2, last_uuid2 = new_reply(entries2, last)
        grew = (last_uuid2 != last_uuid) or (text2 != text)
        entries, text, last_uuid = entries2, text2, last_uuid2
        settled = 0 if grew else settled + 1
    if entries and not text.strip():
        lam = payload.get("last_assistant_message")
        if isinstance(lam, str) and lam.strip():
            import hashlib
            text = lam
            last_uuid = "sha1:" + hashlib.sha1(text.encode("utf-8")).hexdigest()
    if not entries:
        # transcript 讀不到:退回 Stop 輸入附的最後一段(新版 Claude Code 才有這個欄位)
        #   沒有 uuid 可記 ⇒ 用內容雜湊當記號,同一段不會因 Stop 連發而 post 兩次
        import hashlib
        lam = payload.get("last_assistant_message")
        text = lam if isinstance(lam, str) else ""
        last_uuid = ("sha1:" + hashlib.sha1(text.encode("utf-8")).hexdigest()) if text.strip() else ""
    if last_uuid and last_uuid == last:
        return
    # 10-09 真機(a seat 16:03):兩支 Stop hook 相隔 51 毫秒各 post 一次同一段 —— claim_once 的鍵帶 transcript 大小,
    #   Claude Code 還在寫 transcript,兩份看到的大小不同 ⇒ 鍵不同、都放行;claude-last-posted 又是 post 完才寫。
    #   這裡再用「這一段的 uuid(或內容雜湊)」搶一次記號:同一段 CLAIM_WINDOW_S 內只 post 一份。
    if text.strip() and not claim_once("stop-post", last_uuid or text, now=time.time()):
        log("stop", sid, "skipped-duplicate-post", chars=len(text))
        return
    if text.strip():
        rc = post_fn(text)
        log("stop", sid, "posted-reply" if rc == cm.RC_OK else "post-failed", rc=rc, chars=len(text))
        if rc != cm.RC_OK:
            # r1-fix N1(a seat 18:58):送失敗(中繼暫時連不上、沒綁席)不寫「已送」標記,下一次 Stop 再送;
            #   寫了就永遠不補送。10-09:同理把「stop-post」記號放掉,不然 5 秒內的重送會被當成重複
            release_claim("stop-post", last_uuid or text)
            return
    if last_uuid:
        tmp = seat_dir / (MARK_POSTED + ".%d.tmp" % os.getpid())
        tmp.write_text(last_uuid + "\n", encoding="utf-8")
        os.replace(tmp, mark)


def on_stop(payload: dict) -> int:
    import claudebot_mailbox as cm
    sid = payload.get("session_id") or ""
    found = cm.seat(sid)
    if not found:
        return 0
    ensure_title(sid, str(found[1].get("code") or ""), str(payload.get("cwd") or ""), str(payload.get("transcript_path") or ""), found[0],
                     _seat_project(found[1].get("code")))
    post_reply(payload, found[0])
    if payload.get("stop_hook_active"):
        # 已經是被 hook 擋下來續跑的那一輪:不再擋(避免迴圈);之後來的信交給門鈴。
        #   影子續跑說明照樣比對一次:這一輪就是叫它貼的那一輪,貼了要記成已回報(審查第 1 條),結果不擋
        _ship_report(sid, found[1], payload, passive=True)
        return 0
    if not cm.pending_wake(sid):
        return 2 if _ship_report(sid, found[1], payload) else _auto_distill(sid, found[1])
    text = _deliver(sid)                               # 等著送的信先送(審查第 9 條),影子續跑提醒排後面
    if not text:
        return 2 if _ship_report(sid, found[1], payload) else _auto_distill(sid, found[1])
    _ship_report(sid, found[1], payload, passive=True)   # 送信這一輪的回覆也比對(貼了要算;審查第二輪)
    sys.stderr.write(text)
    log("stop", sid, "delivered-continue", chars=len(text))
    return 2


def _ship_report(sid, rec, payload, passive=False) -> bool:
    """10-13 a seat 第 2 層影子續跑說明(the maintainer 19:5x「所有 AI 都必要列出」):ClaudeBot 的回覆在 Claude app 裡,
    程式補不進去 ⇒ 跟 Claude 席同一招:回覆漏了那段 ⇒ rc 2 續一輪叫它原樣貼上(a seat ship_report_gate check)。
    要有這一輪原文(新版 Claude Code 的 Stop 帶 last_assistant_message);沒有 / 任何錯 ⇒ 放行。"""
    try:
        here = Path(__file__).resolve().parent
        if str(here.parent / "identity") not in sys.path:
            sys.path.append(str(here.parent / "identity"))
        import auto_distill_hook as adh
        reply = adh.reply_of(payload)
        code = str(rec.get("code") or "")
        if not reply or not code:
            return False
        rc, out = adh.ship_gate(["check", "--seat", code], reply)   # 貼了 ⇒ check 自己記成已回報
        if passive or rc != 2 or not out or not adh.ship_told_once(code, out):
            return False                               # 同一段只叫它續跑一次;之後交給第 3 層巡查
    except Exception:  # noqa: BLE001
        return False
    sys.stderr.write(out)
    log("stop", sid, "ship-report-continue", chars=len(out))
    return True


def _auto_distill(sid, rec) -> int:
    """10-13 a seat(the maintainer「Bot 也要能自動更新」):綁定的 Claude 對話用量跨 10/30/50/70(量不到就每 20 輪)⇒
    用同一招(rc 2 續一輪)叫它替這一席整理記憶(src/identity/auto_distill_hook.py bot_turn)。任何錯 ⇒ 放行。"""
    try:
        here = Path(__file__).resolve().parent
        if str(here.parent / "identity") not in sys.path:
            sys.path.append(str(here.parent / "identity"))
        import auto_distill_hook as adh
        import grokbot_mailbox as gm
        cabin = str(rec.get("cabin") or "") or gm.own_cabin(str(rec.get("session") or ""), status=rec)
        text = adh.bot_turn(str(rec.get("code") or ""), cabin, gauge_sid=sid)
    except Exception:  # noqa: BLE001
        return 0
    if not text:
        return 0
    sys.stderr.write(text)
    log("stop", sid, "auto-distill-continue", chars=len(text))
    return 2


def _doorbell_limit() -> float:
    try:
        return float(os.environ.get(ENV_DOORBELL_S) or DOORBELL_S)
    except ValueError:
        return DOORBELL_S


def on_doorbell(payload: dict) -> int:
    """背景盯信箱。同一席只留最新的一隻門鈴:每隻起跑時寫記號,記號被換掉的就自己退。"""
    import claudebot_mailbox as cm
    sid = payload.get("session_id") or ""
    found = cm.seat(sid)
    if not found:
        return 0
    seat_dir = found[0]
    me = "%d %s" % (os.getpid(), secrets.token_hex(8))
    bell = seat_dir / DOORBELL_FILE
    tmp = seat_dir / (DOORBELL_FILE + ".%d.tmp" % os.getpid())
    tmp.write_text(me + "\n", encoding="utf-8")
    os.replace(tmp, bell)
    log("doorbell", sid, "armed")
    t0 = time.monotonic()
    limit = _doorbell_limit()
    time.sleep(min(DOORBELL_FIRST_WAIT_S, max(0.0, limit)))
    last_seat_check = time.monotonic()
    while time.monotonic() - t0 < limit:
        try:
            if bell.read_text(encoding="utf-8").strip() != me:
                log("doorbell", sid, "superseded")
                return 0
        except OSError:
            return 0
        if time.monotonic() - last_seat_check >= DOORBELL_SEAT_CHECK_S:
            last_seat_check = time.monotonic()
            if not cm.seat(sid):
                log("doorbell", sid, "seat-gone")
                return 0
        if cm.pending_wake(sid, seat_dir=seat_dir):
            text = _deliver(sid)
            if text:
                sys.stderr.write(text)
                log("doorbell", sid, "rang", chars=len(text))
                return 2
        time.sleep(DOORBELL_POLL_S)
    log("doorbell", sid, "expired")
    return 0


# ── 雲端後端:同一支 hook,拿到中繼位址+通行碼(雲端環境變數)就走 HTTP,不碰本機信箱 ──────────
# 沒有這兩個變數 ⇒ 完全不進這一段(本機席、別人 clone 倉庫的 session 都不受影響)。
ENV_RELAY_URL = "AIB_CLAUDEBOT_RELAY_URL"
ENV_RELAY_TOKEN = "AIB_CLAUDEBOT_RELAY_TOKEN"
RELAY_TIMEOUT_S = 4.0            # 每次呼叫短逾時,再受 run_hook 的事件預算截短(C2);失敗就放行
RELAY_BODY_MAX = 512 * 1024      # 回應體上限(對方滴資料也滴不出去)
RELAY_LONG_POLL_S = 25
RELAY_ERR_BACKOFF_S = 5.0
RELAY_UNBOUND_WAIT_S = 10.0     # 沒綁席時門鈴的重問間隔


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def relay_url_ok(url: str) -> bool:
    """C4(艦長 09-27):通行碼跟著每個請求走,遠端只准 https;http 只准 loopback(本機尺與本機中繼)。"""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.scheme == "https":
        return bool(u.hostname)
    if u.scheme == "http":
        return (u.hostname or "") in LOOPBACK_HOSTS
    return False


def relay_cfg() -> tuple[str, str]:
    url = (os.environ.get(ENV_RELAY_URL) or "").strip().rstrip("/")
    tok = (os.environ.get(ENV_RELAY_TOKEN) or "").strip()
    return (url, tok) if (url and tok and relay_url_ok(url)) else ("", "")


# C2(艦長 09-27):hook 的總時長要壓在宿主期限內(以前 register 3 次 × 6 秒 + 取信 6 秒 = 28 秒,宿主 10–25 秒就砍)。
#   run_hook 依事件設一個截止時刻;每次對中繼的呼叫只拿剩下的時間,剩不到 0.3 秒就直接當連不上放行。
HOOK_BUDGET_MARGIN_S = 2.0
_DEADLINE: float | None = None


def set_budget(event: str) -> None:
    global _DEADLINE
    limit = HOOK_TIMEOUT_S.get(event)
    _DEADLINE = None if not limit or limit >= 600 else time.monotonic() + max(1.0, limit - HOOK_BUDGET_MARGIN_S)


def budget_left() -> float:
    return float("inf") if _DEADLINE is None else _DEADLINE - time.monotonic()


def _relay(method: str, path: str, body: dict | None = None, timeout: float = RELAY_TIMEOUT_S) -> tuple[int, dict]:
    """回 (http 狀態, JSON);連不上回 (0, {})。永不拋例外。"""
    import urllib.request
    import urllib.error
    url, tok = relay_cfg()
    if not url:
        return 0, {}
    left = budget_left()
    if left < 0.3:
        return 0, {}                      # C2:預算用完就當連不上,放行
    timeout = max(0.3, min(timeout, left))
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={"X-AIB-Token": tok, "Content-Type": "application/json"})
    result: list = []

    def work():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                result.append((r.status, json.loads(r.read(RELAY_BODY_MAX).decode("utf-8") or "{}")))
        except urllib.error.HTTPError as e:
            try:
                result.append((e.code, json.loads(e.read(RELAY_BODY_MAX).decode("utf-8") or "{}")))
            except Exception:  # noqa: BLE001
                result.append((e.code, {}))
        except Exception:  # noqa: BLE001 — 逾時、DNS、代理 403 都算連不上
            result.append((0, {}))

    # r7 F3:urlopen 的 timeout 是單次 socket 動作的逾時,對方慢慢滴資料(trickle)照樣拖過宿主期限;
    #   整個請求丟到子執行緒,用絕對時限 join;超時就當連不上放行,執行緒隨程序結束。
    import threading
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive() or not result:
        return 0, {}
    st, obj = result[0]
    return st, (obj if isinstance(obj, dict) else {})


def remote_state_dir(sid: str) -> Path:
    """雲端沙盒沒有 ~/.ez2-aib:標記檔放 TMPDIR,絕不進倉庫。"""
    d = Path(os.environ.get("TMPDIR") or "/tmp") / "aib-claudebot" / sid[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ack(sid: str, obj: dict) -> None:
    """r7 F2:信拿到手就 ack;沒 ack 的中繼會在 ACK_TIMEOUT_S 後退回 inbox 重送。失敗不管(頂多重送一次)。"""
    cid = obj.get("claim") if isinstance(obj, dict) else None
    if cid and obj.get("messages"):
        _relay("POST", "/v1/sessions/%s/ack" % sid, {"claim": str(cid)}, timeout=2.0)


def _remote_pull(sid: str, wait: int = 0) -> tuple[int, str]:
    """(狀態, rendered);404 = 沒綁席。"""
    st, obj = _relay("GET", "/v1/sessions/%s/inbox?wait=%d&ack=1" % (sid, max(0, min(RELAY_LONG_POLL_S, wait))),
                     timeout=RELAY_TIMEOUT_S + wait)
    if st == 200 and obj.get("messages"):
        _ack(sid, obj)
        return st, str(obj.get("rendered") or "")
    return st, ""


def _remote_post(sid: str, text: str, role: str, source: str) -> int:
    st, _ = _relay("POST", "/v1/sessions/%s/outbox" % sid, {"text": text, "role": role, "source": source})
    return 0 if st == 200 else (st or 1)


def _git_branch() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True,
                              timeout=5, stdin=subprocess.DEVNULL).stdout.strip()[:120]
    except Exception:  # noqa: BLE001
        return ""


RELAY_REGISTER_TRIES = 3         # 雲端容器剛起網路可能還沒就緒(09-27 真機:第一次報到連不上)
RELAY_REGISTER_GAP_S = 2.0


def _remote_register(sid: str, payload: dict | None = None, tries: int = RELAY_REGISTER_TRIES) -> tuple[int, dict]:
    body = {"cwd": (payload or {}).get("cwd") or os.getcwd(), "branch": _git_branch(),
            "remote_env": os.environ.get("CLAUDE_CODE_ENTRYPOINT") or ""}
    st, obj = 0, {}
    for i in range(max(1, tries)):
        st, obj = _relay("POST", "/v1/sessions/%s/register" % sid, body)
        if st == 200:
            return st, obj
        if st in (401, 429):
            return st, obj                 # 碼錯/被擋:重試沒用
        if i + 1 < tries:
            if budget_left() < RELAY_REGISTER_GAP_S + 0.5:
                break                     # C2:剩的時間不夠再等一輪就別等
            time.sleep(RELAY_REGISTER_GAP_S)
    return st, obj


def remote_session_start(payload: dict) -> int:
    sid = payload.get("session_id") or ""
    st, obj = _remote_register(sid, payload)
    log("session-start", sid, "relay-register", status=st, bound=bool(obj.get("bound")))
    if st != 200 or not obj.get("bound"):
        return 0
    st, text = _remote_pull(sid, 0)
    if text:
        sys.stdout.write(text)
        log("session-start", sid, "delivered", chars=len(text))
    return 0


def remote_prompt(payload: dict) -> int:
    sid = payload.get("session_id") or ""
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.strip() and is_injected_prompt(prompt):
        log("prompt", sid, "skipped-injected", chars=len(prompt))
    elif isinstance(prompt, str) and prompt.strip():
        st0, obj0 = _relay("GET", "/v1/sessions/%s/inbox?wait=0&ack=1" % sid)
        if st0 == 200:
            _ack(sid, obj0)
        if st0 == 200 and obj0.get("code"):
            ensure_title(sid, str(obj0.get("code")), str(payload.get("cwd") or os.getcwd()), str(payload.get("transcript_path") or ""), remote_state_dir(sid),
                         str(obj0.get("project") or ""))
            if obj0.get("messages"):
                sys.stdout.write(str(obj0.get("rendered") or ""))
                log("prompt", sid, "delivered", chars=len(str(obj0.get("rendered") or "")))
        rc = _remote_post(sid, prompt, "user", "app")
        if rc == 404:
            # 沒綁席(或開場報到沒成功,中繼不認得這條):補報到一次,讓 Mac 端選單看得到;這句照樣不送
            _remote_register(sid, payload, tries=1)
            log("prompt", sid, "unbound-registered")
            return 0
        log("prompt", sid, "posted-user" if rc == 0 else "post-failed", rc=rc, chars=len(prompt))
    st, text = _remote_pull(sid, 0)
    if text:
        sys.stdout.write(text)
        log("prompt", sid, "delivered", chars=len(text))
    return 0


def remote_stop(payload: dict) -> int:
    """順序跟本機 on_stop 一樣:先回信、再判「是不是續跑那一輪」、最後才取信。
    r1 H1(a seat 09-27 15:55):舊版先 GET inbox(中繼端 = 取走)再看 stop_hook_active 放行 ⇒ 信取走了卻沒交給 Claude。
    續跑那一輪一律不碰信箱,信留在中繼給門鈴或下一句。"""
    sid = payload.get("session_id") or ""
    state = remote_state_dir(sid)
    post_reply(payload, state, post_fn=lambda text: _remote_post(sid, text, "claude", "cloud"))
    if payload.get("stop_hook_active"):
        return 0
    st, obj = _relay("GET", "/v1/sessions/%s/inbox?wait=0&ack=1" % sid)
    if st != 200:
        return 0                            # 沒綁席(404)/ 中繼連不上(0):放行
    _ack(sid, obj)
    if obj.get("code"):
        ensure_title(sid, str(obj.get("code")), str(payload.get("cwd") or os.getcwd()), str(payload.get("transcript_path") or ""), state,
                     str(obj.get("project") or ""))
    if obj.get("messages"):
        pending = str(obj.get("rendered") or "")
        sys.stderr.write(pending)
        log("stop", sid, "delivered-continue", chars=len(pending))
        return 2
    return 0


def remote_doorbell(payload: dict) -> int:
    """背景對中繼長輪詢;有信才 exit 2,絕不空響(09-27 探針教訓);同一 session 只留最新一隻。"""
    sid = payload.get("session_id") or ""
    state = remote_state_dir(sid)
    me = "%d %s" % (os.getpid(), secrets.token_hex(8))
    bell = state / DOORBELL_FILE
    tmp = state / (DOORBELL_FILE + ".%d.tmp" % os.getpid())
    tmp.write_text(me + "\n", encoding="utf-8")
    os.replace(tmp, bell)
    log("doorbell", sid, "armed", backend="relay")
    t0 = time.monotonic()
    limit = _doorbell_limit()
    time.sleep(min(DOORBELL_FIRST_WAIT_S, max(0.0, limit)))
    while time.monotonic() - t0 < limit:
        try:
            if bell.read_text(encoding="utf-8").strip() != me:
                log("doorbell", sid, "superseded")
                return 0
        except OSError:
            return 0
        wait = int(max(1, min(RELAY_LONG_POLL_S, limit - (time.monotonic() - t0))))
        st, text = _remote_pull(sid, wait)
        if st == 404:
            # 還沒綁席(開場先於 Mac 端開席)或席關了:不退出,隔 10 秒再問,直到綁上或門鈴到期
            _remote_register(sid, payload, tries=1)
            time.sleep(RELAY_UNBOUND_WAIT_S)
            continue
        if st == 0:
            time.sleep(RELAY_ERR_BACKOFF_S)   # 中繼暫時連不上:退避後再試,不放棄也不空響
            continue
        if text:
            sys.stderr.write(text)
            log("doorbell", sid, "rang", chars=len(text), backend="relay")
            return 2
    log("doorbell", sid, "expired")
    return 0


REMOTE_HANDLERS = {"session-start": remote_session_start, "prompt": remote_prompt,
                   "stop": remote_stop, "doorbell": remote_doorbell}

HANDLERS = {"session-start": on_session_start, "prompt": on_prompt, "stop": on_stop, "doorbell": on_doorbell}


CLAIM_DIR = "claudebot-bindings/hook-claims"
CLAIM_WINDOW_S = 5.0


def _claim_key(event: str, raw: str, size: str = "") -> str:
    import hashlib
    return hashlib.sha256((event + "\0" + size + "\0" + (raw or "")).encode("utf-8", "replace")).hexdigest()[:32]


def release_claim(event: str, raw: str) -> None:
    """搶到記號但事情沒做成(post 失敗)⇒ 把記號放掉,下一次 Stop 才能重送(test-claudebot-relay H3:中繼掛掉時要補送)。"""
    try:
        (_state_root() / CLAIM_DIR / _claim_key(event, raw)).unlink()
    except OSError:
        pass


def claim_once(event: str, raw: str, now: float | None = None, transcript=None) -> bool:
    """同一個事件、同一份 stdin 在 CLAIM_WINDOW_S 秒內只處理一次。回 True = 這一份搶到了,照做。

    10-07 root a seat 真機實踩(a seat 綁 adfbd9a3):全域 ~/.claude/settings.json 與倉庫層 ~/ez2-aib/.claude/settings.json
    各裝了一套 claudebot hook,對話開在倉庫資料夾 ⇒ 兩套同時觸發,the maintainer 那句與 Claude 的回覆都在席上出現兩次
    (claude-last-posted 擋不住:兩支 Stop 同時讀到舊記號)。兩份 hook 拿到的 stdin 逐位元組相同,以它的雜湊搶一個記號檔:
    O_EXCL 建得出來的那一份照做,另一份放行零輸出。記號超過時窗就視為舊的(使用者真的連打兩次同一句,第二句照收)。
    只在確認綁定之後才走到這裡 ⇒ 沒綁的對話仍然零寫檔。"""
    import hashlib
    now = time.time() if now is None else now
    d = _state_root() / CLAIM_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return True                                  # 記號夾建不起來就照做(重複比漏收好)
    # 鍵加上 transcript 當下的大小:Stop 每一輪的 stdin 幾乎一樣,只看 stdin 會把正常的下一輪也擋掉;
    #   重複觸發的兩份是同一刻、同一個大小,下一輪對話已經變長 ⇒ 分得開。
    size = ""
    if isinstance(transcript, str) and transcript:
        try:
            size = str(Path(transcript).stat().st_size)
        except OSError:
            size = ""
    key = _claim_key(event, raw, size)
    path = d / key
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            if int(now) % 50 == 0:
                _prune_claims(d, now)
            return True
        except FileExistsError:
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue                             # 剛好被清掉 ⇒ 再搶一次
            if age < CLAIM_WINDOW_S:
                return False
            try:
                path.unlink()                        # 舊記號:刪掉再用 O_EXCL 搶,兩份同時遇到舊檔也只有一份搶得到
            except OSError:
                pass
        except OSError:
            return True
    return False


def _prune_claims(d: Path, now: float) -> None:
    for f in d.iterdir():
        try:
            if now - f.stat().st_mtime > 600:
                f.unlink()
        except OSError:
            continue


def run_hook(event: str, raw: str) -> int:
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        # A malformed payload has no trustworthy session_id, so we cannot
        # establish that it belongs to a bound ClaudeBot session. Keep the
        # global hook side-effect-free for this unidentifiable session.
        return 0
    if not isinstance(payload, dict):
        return 0
    sid = payload.get("session_id")
    set_budget(event)
    try:
        if relay_cfg()[0]:
            # 雲端後端:有中繼位址+通行碼才走;sid 不合法一樣放行
            import claudebot_threads
            if not isinstance(sid, str) or not claudebot_threads.valid_session_id(sid):
                return 0
            return REMOTE_HANDLERS[event](payload)
        if not isinstance(sid, str) or not bound_seat(sid):
            return 0                               # 沒綁(或綁過但席已關):零輸出、零寫檔
        if event != "doorbell" and not claim_once(event, raw, transcript=payload.get("transcript_path")):
            # 門鈴不走這裡:它本來就「新的頂掉舊的」(DOORBELL_FILE),重複的那支會自己 superseded 退場
            log(event, sid, "skip-duplicate")
            return 0
        return HANDLERS[event](payload)
    except Exception as exc:   # noqa: BLE001 — 放行並留痕,絕不卡住 Claude
        log(event, sid if isinstance(sid, str) else "", "error", err=type(exc).__name__, detail=str(exc)[:200])
        return 0


# ── 安裝 / 移除(只動自己那幾條,不碰 claimtruth 等別人的 hook)──────────────────

MARKER = "claudebot_hook.py"
REPO_SCRIPT = "src/cockpit/claudebot_hook.py"   # --repo 安裝時寫進倉庫層 settings 的相對路徑(雲端 cwd = 倉庫根)


def settings_path() -> Path:
    cfg = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return (Path(cfg).expanduser() if cfg else Path.home() / ".claude") / "settings.json"


def hook_command(event: str, python: str | None = None, script: str | None = None) -> str:
    """★ exit 2 在 Claude Code 是「擋下」:而 python 找不到腳本檔時正好回 2。
    直接寫 `python3 <路徑>` 的話,repo 搬家或工作樹刪掉 ⇒ 每一條本機 Claude 對話的每一句都被擋。
    所以先 `test -f` 不在就放行(exit 0),在才 exec。"""
    # 10-07:python 路徑也要先驗。install 時寫死的是當下的 sys.executable(例 Homebrew 的 python3.14),
    #   之後 brew 升級把它換掉 ⇒ exec 失敗 rc 127,每一條本機對話每一句都跳 hook 錯誤。不在就放行,同 test -f 那條理由。
    #   只驗絕對路徑:倉庫層安裝(--repo,雲端用)給的是裸 `python3`,交給 PATH 找,test -x 判不了它。
    raw_py = python or sys.executable
    py = shlex.quote(raw_py)
    sc = shlex.quote(script or str(Path(__file__).resolve()))
    guard_py = ("test -x %s || exit 0; " % py) if os.path.isabs(raw_py) else ""
    return "test -f %s || exit 0; %sexec %s %s %s" % (sc, guard_py, py, sc, event)


def our_entries(python=None, script=None) -> dict:
    def cmd(ev, **kw):
        d = {"type": "command", "command": hook_command(ev, python, script), "timeout": HOOK_TIMEOUT_S[ev]}
        d.update(kw)
        return d
    bell = cmd("doorbell", **{"async": True, "asyncRewake": True})
    return {
        "SessionStart": [{"hooks": [cmd("session-start"), dict(bell)]}],
        "UserPromptSubmit": [{"hooks": [cmd("prompt")]}],
        "Stop": [{"hooks": [cmd("stop"), dict(bell)]}],
    }


def _is_ours(h) -> bool:
    return isinstance(h, dict) and MARKER in str(h.get("command") or "")


def strip_ours(cfg: dict) -> dict:
    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict):
        return cfg
    for ev in list(hooks):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"] if not _is_ours(h)]
                if not inner:
                    continue
                g = dict(g, hooks=inner)
            kept.append(g)
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if not hooks:
        cfg.pop("hooks", None)
    return cfg


def add_ours(cfg: dict, python=None, script=None) -> dict:
    cfg = strip_ours(cfg)
    hooks = cfg.setdefault("hooks", {})
    for ev, groups in our_entries(python, script).items():
        hooks.setdefault(ev, [])
        hooks[ev].extend(groups)
    return cfg


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))   # 壞 JSON ⇒ 丟例外,呼叫端拒絕覆寫
    if not isinstance(data, dict):
        raise ValueError("settings.json 頂層不是物件")
    return data


def _write_settings(path: Path, cfg: dict) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak-claudebot-%d" % int(time.time()))
        backup.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".claudebot.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def installed(cfg: dict) -> list[str]:
    out = []
    for ev, groups in (cfg.get("hooks") or {}).items():
        for g in groups if isinstance(groups, list) else []:
            for h in (g.get("hooks") if isinstance(g, dict) else None) or []:
                if _is_ours(h):
                    out.append("%s: %s" % (ev, str(h.get("command")).rsplit(" ", 1)[-1]))
    return out


def stale_commands(cfg: dict) -> list[str]:
    """已裝的指令裡會壞的:python 是絕對路徑但檔已不在(brew 升級後),或還是沒先驗 python 的舊寫法。回人話,一條一句。"""
    out = []
    for ev, groups in (cfg.get("hooks") or {}).items():
        for g in groups if isinstance(groups, list) else []:
            for h in (g.get("hooks") if isinstance(g, dict) else None) or []:
                if not _is_ours(h):
                    continue
                cmd = str(h.get("command") or "")
                tail = cmd.split("exec ", 1)[-1]
                try:
                    py = shlex.split(tail)[0]
                except (ValueError, IndexError):
                    continue
                if not os.path.isabs(py):
                    continue
                if not os.access(py, os.X_OK):
                    out.append("%s:python 不在了(%s),這條 hook 每一句都會失敗;重跑 install" % (ev, py))
                elif "test -x" not in cmd:
                    out.append("%s:舊寫法(沒先驗 python 路徑,升級 python 後會壞);重跑 install 換成新寫法" % ev)
    return out


def cli(argv: list[str]) -> int:
    import argparse
    if argv and argv[0] in EVENTS:
        # hook 呼叫:不經 argparse(argparse 出錯回 2 = 在 Claude Code 裡變成「擋下」)
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
        return run_hook(argv[0], raw)
    if argv and argv[0] not in ("install", "uninstall", "status") and not argv[0].startswith("-"):
        # 不認得的事件名(例如舊版裝的 hook 指到改過名的事件):回 1(非擋下的錯),不是 argparse 的 2
        print("claudebot_hook.py: 不認得 %r" % argv[0], file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser(prog="claudebot_hook.py",
                                 description="ClaudeBot hook(給 Claude Code 呼叫)與安裝工具")
    ap.add_argument("action", choices=("install", "uninstall", "status"))
    ap.add_argument("--settings", default=None, help="預設 ~/.claude/settings.json(或 $CLAUDE_CONFIG_DIR)")
    ap.add_argument("--python", default=None, help="hook 用哪個 python3(預設就是跑安裝的這個)")
    ap.add_argument("--repo", default=None, metavar="DIR",
                    help="改裝倉庫層 DIR/.claude/settings.json(雲端 session 只吃這層):命令寫相對路徑 python3 src/cockpit/claudebot_hook.py,"
                         "沒中繼變數的環境一律放行零輸出;已有的別家 hook 不動")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    repo_script = repo_python = None
    if args.repo:
        repo = Path(args.repo).expanduser().resolve()
        if not (repo / REPO_SCRIPT).is_file():
            print("claudebot: %s 裡沒有 %s(要在 AIB 倉庫的根目錄跑)" % (repo, REPO_SCRIPT), file=sys.stderr)
            return 2
        path = repo / ".claude" / "settings.json"
        repo_script, repo_python = REPO_SCRIPT, (args.python or "python3")
    else:
        path = Path(args.settings).expanduser() if args.settings else settings_path()
    try:
        cfg = _load_settings(path)
    except (OSError, ValueError) as exc:
        print("claudebot: 讀不懂 %s(%s),不動它" % (path, exc), file=sys.stderr)
        return 2
    if args.action == "status":
        rows = installed(cfg)
        print("claudebot hook:%s(%s)" % ("已安裝 %d 條" % len(rows) if rows else "沒安裝", path))
        for r in rows:
            print("  " + r)
        for w in stale_commands(cfg):
            print("  ⚠ " + w)
        return 0 if rows else 1
    new = add_ours(cfg, repo_python or args.python, repo_script) if args.action == "install" else strip_ours(cfg)
    if args.dry_run:
        print(json.dumps(new, ensure_ascii=False, indent=2))
        return 0
    backup = _write_settings(path, new)
    print("claudebot: 已%s hook(%s)%s" % ("安裝" if args.action == "install" else "移除", path,
                                       ";原檔備份在 %s" % backup if backup else ""))
    if args.action == "install":
        print("commit 進倉庫、雲端開對話時選含這份設定的分支,雲端 session 才吃得到。" if args.repo else
              "已經開著的 Claude 對話要重開(或在 /hooks 看一下)才會載入新 hook。")
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
