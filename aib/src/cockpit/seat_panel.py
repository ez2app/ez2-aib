#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對話面板核心(10-20 Andy「對話面板放核心免費」;@abb89 SPEC-chat-panel 第 1 節 C2 / C3;格式 SPEC-chat-panel-formats v0.5):

  aib seat transcript <席碼> [--since <id> | --before <id> | --id <id> | --offset <bytes>] [--limit N] [--clip N]
                             [--with-thinking] [--with-sidechain] [--redact] [--json]
  aib seat pending <席碼> [--json]

只讀:那一席的 Claude Code 對話紀錄 jsonl(席位檔 session_uuid → activity_probe.claude_transcript_paths)+ tmux 畫面最後幾行。
不寫任何檔、不送任何鍵(C1 / C4 / C5 另一支,要 Touch ID)。
事件:{id, ts, kind, text, meta};kind = user | dm | hook | human_shell | assistant | tool_call | tool_result | ask | system | thinking。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import activity_probe  # noqa: E402
import seat_address  # noqa: E402
import seats_dir_lib  # noqa: E402

V = 1
LIMIT_DEFAULT = 500
CODE_RE = re.compile(r"^[a-z0-9]{5}$")
DM_LINE = re.compile(r"^IP:(\S+)/(\S+)/([^@\n]+)@([a-z0-9]{5}|aib|offhub) >> IP:(\S+)/(\S+)/([^@\n]+)@([a-z0-9]{5}) "
                     r"(\d\d-\d\d \d\d:\d\d)\(台北時間\) ?(.*)$")
RELAY_NOTICE = re.compile(r"長訊已落檔\((\d+) 位元組[^)]*\),請讀:(/\S+)")
AGE_LINE = re.compile(r"^本則發出於 .*已過 .*$")
PASTED = re.compile(r"<pasted_content id=\"?([A-Za-z0-9_-]+)\"?>(.*?)</pasted_content(?: id=\"?\1\"?)?>", re.S)
BASH_IN = re.compile(r"<bash-input>(.*?)</bash-input>", re.S)
BASH_OUT = re.compile(r"<bash-stdout>(.*?)</bash-stdout>", re.S)
BASH_ERR = re.compile(r"<bash-stderr>(.*?)</bash-stderr>", re.S)
MID_TURN = re.compile(r"The user sent a new message while you were working:\s*\n(.*?)(?:\n\s*This is how Claude Code surfaces|</system-reminder>|\Z)", re.S)
SYSTEM_MARKS = ("<task-notification>", "<local-command-caveat>", "<command-name>", "[SYSTEM NOTIFICATION", "<system-reminder>")
HOOK_PREFIX = ("Stop hook feedback:", "UserPromptSubmit hook", "SessionStart hook", "PreToolUse hook", "PostToolUse hook")
NOTICE_PATTERNS = (("usage_reset", re.compile(r"usage limit has reset", re.I)),
                   ("login_ok", re.compile(r"<local-command-stdout>\s*Login successful")),
                   ("login_needed", re.compile(r"Use the url below to sign in|disabled Claude subscription access", re.I)))
ALLOWED_RELAY_ROOTS = ()   # 填在 _relay_roots():狀態根、~/.ez2-aib、pending


# ── 席位 ──

def seat_records(state_root=None):
    root = state_root or seats_dir_lib.state_root()[0]
    for f in sorted(glob.glob(os.path.join(root, "instances", "*", "seats", "w*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and not rec.get("retired"):
            rec["_file"] = f
            yield rec


def find_seat(code, state_root=None):
    """席碼 ⇒ 席位檔(非退役、恰好一席);找不到 None。"""
    if not isinstance(code, str) or not CODE_RE.match(code):
        return None
    hits = [r for r in seat_records(state_root) if seat_address.code_of(r) == code]
    if not hits:
        return None
    hits.sort(key=lambda r: float(r.get("started_epoch") or 0), reverse=True)
    return hits[0]


def transcript_path(rec):
    """席位檔 ⇒ 最新的那份 jsonl(壓縮 / 接回後換檔也跟著);沒有 ⇒ None。"""
    sess = str(rec.get("session_uuid") or "").strip().lower()
    if not sess:
        return None
    cands = []
    pd = activity_probe.project_dir_from_cwd(rec.get("cwd"))
    if pd:
        cands.append(os.path.join(pd, sess + ".jsonl"))
    cands += activity_probe.claude_transcript_paths(sess)
    seen, best = set(), None
    for p in cands:
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        if best is None or os.path.getmtime(p) > os.path.getmtime(best):
            best = p
    return best


# ── 紀錄 → 事件 ──

def _ts(rec):
    t = rec.get("timestamp")
    if isinstance(t, str):
        try:
            from datetime import datetime, timezone
            return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _text_of(content):
    """content 字串或 content[] 的 text 段 ⇒ 純文字;圖片 ⇒ 「[圖片 …]」。回 (text, images)。"""
    if isinstance(content, str):
        return content, 0
    parts, images = [], 0
    for x in content if isinstance(content, list) else []:
        if not isinstance(x, dict):
            continue
        if x.get("type") == "text":
            parts.append(str(x.get("text") or ""))
        elif x.get("type") == "image":
            images += 1
            parts.append("[圖片 %s]" % ((x.get("source") or {}).get("media_type") or "image"))
    return "\n".join(parts), images


def _relay_roots():
    roots = [os.path.expanduser("~/.ez2-aib")]
    try:
        roots.append(seats_dir_lib.state_root()[0])
    except Exception:  # noqa: BLE001
        pass
    return tuple(os.path.realpath(r) for r in roots)


def _read_relay(path, limit=256 * 1024):
    """長訊落檔只讀狀態根 / ~/.ez2-aib 底下的檔;別處一律不讀(路徑來自對方的信)。"""
    try:
        real = os.path.realpath(path)
        if not any(real == r or real.startswith(r + os.sep) for r in _relay_roots()):
            return None
        with open(real, "rb") as fh:
            data = fh.read(limit + 1)
        return data[:limit].decode("utf-8", "replace")
    except OSError:
        return None


def _dm_events(text, base_id, ts):
    """一則 user 訊息裡的 dm 行 ⇒ 多筆 kind=dm;不是 dm 的行回 (events, rest_text)。"""
    events, rest, age = [], [], None
    for ln in text.splitlines():
        m = DM_LINE.match(ln.strip())
        if not m:
            if AGE_LINE.match(ln.strip()):
                age = ln.strip()
            else:
                rest.append(ln)
            continue
        body = m.group(10)
        meta = {"from": m.group(4), "from_label": "%s/%s@%s" % (m.group(2), m.group(3), m.group(4)),
                "to": m.group(8), "ts_taipei": m.group(9), "queue_ids": [],
                "relay_path": None, "relay_text": None, "relay_bytes": None}
        rm = RELAY_NOTICE.search(body)
        if rm:
            meta["notice"] = body
            meta["relay_path"] = rm.group(2)
            meta["relay_bytes"] = int(rm.group(1))
            rt = _read_relay(rm.group(2))
            meta["relay_text"] = rt
            body = rt if rt is not None else body
        if age:
            meta["age_line"] = age
        events.append({"id": "%s#%d" % (base_id, len(events) + 1), "ts": ts, "kind": "dm", "text": body, "meta": meta})
    return events, "\n".join(rest).strip()


def _classify_user(text):
    s = text.lstrip()
    if s.startswith(HOOK_PREFIX):
        kind = "hook"
        name = s.split(":", 1)[0].lower()
        return kind, {"hook": "stop" if "stop" in name else ("userpromptsubmit" if "prompt" in name else ("sessionstart" if "session" in name else name[:24]))}
    if "<bash-input>" in s:
        return "human_shell", {}
    if any(mark in s[:200] for mark in SYSTEM_MARKS):
        sub = "task-notification" if "<task-notification>" in s else ("local-command" if "<command-name>" in s or "<local-command" in s else "reminder")
        return "system", {"subtype": sub}
    return "user", {}


def _notices(text):
    return [name for name, rx in NOTICE_PATTERNS if rx.search(text or "")]


def events_from_jsonl(path, with_thinking=False, with_sidechain=False, offset=0):
    """整份(或從 offset 起)⇒ (events, pending_ask_ids, queue_ops, end_offset, session_ids)。"""
    events, queue_ops, sessions = [], 0, []
    tool_calls, answered = {}, set()
    with open(path, "rb") as fh:
        if offset:
            fh.seek(offset)
        while True:
            line = fh.readline()
            if not line:
                end = fh.tell()
                break
            try:
                rec = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            t = rec.get("type")
            sid = rec.get("sessionId")
            if sid and sid not in sessions:
                sessions.append(sid)
            if t == "queue-operation":
                queue_ops += 1                      # 從最後一筆 user / assistant 之後算起 = 現在排隊中幾則
                continue
            if t not in ("user", "assistant", "system"):
                continue
            if t in ("user", "assistant"):
                queue_ops = 0
            if rec.get("isSidechain") and not with_sidechain:
                continue
            ts = _ts(rec)
            uid = str(rec.get("uuid") or hashlib.sha1(line).hexdigest()[:12])
            side = {"sidechain": True} if rec.get("isSidechain") else {}
            if t == "system":
                sub = rec.get("subtype")
                if sub == "compact_boundary":
                    events.append({"id": uid, "ts": ts, "kind": "system", "text": "對話已壓縮", "meta": dict(side, subtype=sub)})
                elif sub == "informational":
                    events.append({"id": uid, "ts": ts, "kind": "system", "text": str(rec.get("content") or ""), "meta": dict(side, subtype=sub)})
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if t == "user":
                if rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
                    continue
                if rec.get("isMeta"):
                    mt = _text_of(content)[0]
                    if not (mt.lstrip().startswith(HOOK_PREFIX) or _notices(mt)):
                        continue                        # isMeta = 系統塞的;只有 hook 回饋與通知(額度恢復等,也是 isMeta)要給人看
                    if not mt.lstrip().startswith(HOOK_PREFIX):
                        events.append({"id": uid, "ts": ts, "kind": "system", "text": mt, "meta": dict(side, subtype="notice", notices=_notices(mt))})
                        continue
                # tool_result 段
                for x in content if isinstance(content, list) else []:
                    if isinstance(x, dict) and x.get("type") == "tool_result":
                        tid = str(x.get("tool_use_id") or "")
                        answered.add(tid)
                        body = x.get("content")
                        btext, _ = _text_of(body) if isinstance(body, list) else (str(body or ""), 0)
                        events.append({"id": tid + ":r", "ts": ts, "kind": "tool_result", "text": btext,
                                       "meta": dict(side, tool=tool_calls.get(tid), is_error=bool(x.get("is_error")), bytes=len(btext.encode("utf-8")))})
                        mm = MID_TURN.search(btext)
                        if mm:
                            events.append({"id": tid + ":mid", "ts": ts, "kind": "user", "text": mm.group(1).strip(), "meta": dict(side, mid_turn=True)})
                text, images = _text_of(content)
                if not text.strip():
                    continue
                dms, rest = _dm_events(text, uid, ts)
                events.extend(dms)
                if not rest:
                    continue
                kind, meta = _classify_user(rest)
                meta.update(side)
                if images:
                    meta["images"] = images
                pasted = [{"id": pid, "bytes": len(body.encode("utf-8"))} for pid, body in PASTED.findall(rest)]
                if pasted:
                    meta["pasted"] = pasted
                if kind == "human_shell":
                    bi = BASH_IN.search(rest)
                    meta["stdout"] = (BASH_OUT.search(rest) or [None, ""])[1] if BASH_OUT.search(rest) else ""
                    meta["stderr"] = (BASH_ERR.search(rest) or [None, ""])[1] if BASH_ERR.search(rest) else ""
                    rest = bi.group(1).strip() if bi else rest
                notes = _notices(rest)
                if notes:
                    meta["notices"] = notes
                events.append({"id": uid, "ts": ts, "kind": kind, "text": rest, "meta": meta})
                continue
            # assistant
            n_text = 0
            for x in content if isinstance(content, list) else [{"type": "text", "text": content}]:
                if not isinstance(x, dict):
                    continue
                xt = x.get("type")
                if xt == "text":
                    n_text += 1
                    txt = str(x.get("text") or "")
                    if not txt.strip():
                        continue
                    events.append({"id": uid if n_text == 1 else "%s#%d" % (uid, n_text), "ts": ts, "kind": "assistant", "text": txt,
                                   "meta": dict(side, model=msg.get("model"), turn=(rec.get("turnPosition") or {}).get("turnIndex"))})
                elif xt == "tool_use":
                    tid = str(x.get("id") or uid)
                    name = str(x.get("name") or "")
                    inp = x.get("input") if isinstance(x.get("input"), dict) else {}
                    tool_calls[tid] = name
                    summary = str(inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("prompt") or inp.get("question") or name)[:80]
                    events.append({"id": tid, "ts": ts, "kind": "tool_call", "text": summary, "meta": dict(side, tool=name, input=inp, summary=summary)})
                    if name == "AskUserQuestion":
                        qs = []
                        for q in inp.get("questions") or []:
                            if isinstance(q, dict):
                                qs.append({"header": q.get("header"), "question": q.get("question"), "multi": bool(q.get("multiSelect")),
                                           "options": [{"label": o.get("label"), "description": o.get("description"), "preview": o.get("preview")}
                                                       for o in q.get("options") or [] if isinstance(o, dict)]})
                        events.append({"id": tid + ":ask", "ts": ts, "kind": "ask", "text": "\n".join(q.get("question") or "" for q in qs),
                                       "meta": dict(side, tool_use_id=tid, questions=qs)})
                elif xt == "thinking" and with_thinking:
                    th = str(x.get("thinking") or "")
                    if th.strip():
                        events.append({"id": uid + ":think", "ts": ts, "kind": "thinking", "text": th, "meta": dict(side)})
    pending = [e for e in events if e["kind"] == "ask" and e["meta"]["tool_use_id"] not in answered]
    return events, pending, queue_ops, end, sessions


def redact(text):
    """外送用:長 hex / email / IP / 類金鑰字串遮掉。"""
    s = re.sub(r"\b[0-9a-f]{32,}\b", "<hex>", text or "")
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", s)
    s = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", s)
    s = re.sub(r"\b(?:sk|ghp|xox[abp]|AKIA|npub|nsec)[A-Za-z0-9_-]{8,}", "<secret>", s)
    return s


def clip_events(events, clip):
    if not clip:
        return events
    out = []
    for e in events:
        if len(e["text"]) > clip:
            e = dict(e, text=e["text"][:clip], meta=dict(e["meta"], clipped=True, bytes=e["meta"].get("bytes", len(e["text"].encode("utf-8")))))
        out.append(e)
    return out


FOLLOW_WAIT_DEFAULT, FOLLOW_WAIT_MAX, FOLLOW_POLL_S = 20, 60, 0.5


def _wait_for_growth(rec, path, offset, wait, sleep=time.sleep, clock=time.time):
    """--follow:等到紀錄檔長過 offset、或那一席換了紀錄檔(壓縮 / 接回),或 wait 秒到。回 (path_now, grew)。
    只看 stat(不重讀檔),每 FOLLOW_POLL_S 看一次。"""
    end = clock() + max(0, min(FOLLOW_WAIT_MAX, wait))
    while True:
        try:
            if os.path.getsize(path) > offset:
                return path, True
        except OSError:
            return path, True                      # 檔案不見了 ⇒ 讓呼叫端重讀、回 reset
        now_path = transcript_path(rec)
        if now_path and now_path != path:
            return now_path, True
        if clock() >= end:
            return path, False
        sleep(FOLLOW_POLL_S)


def transcript(code, since=None, before=None, only_id=None, offset=0, limit=LIMIT_DEFAULT, clip=0,
               with_thinking=False, with_sidechain=False, do_redact=False, state_root=None, follow=False, wait=FOLLOW_WAIT_DEFAULT,
               session=None, sleep=time.sleep, clock=time.time):
    rec = find_seat(code, state_root)
    if rec is None:
        return {"v": V, "ok": False, "why": "no-seat", "seat": code}, 2
    path = transcript_path(rec)
    if path is None:
        return {"v": V, "ok": False, "why": "no-transcript", "seat": code, "session": rec.get("session_uuid")}, 4
    rotated = False
    cur_sess = str(rec.get("session_uuid") or "").lower()
    if offset and not (since or before or only_id):
        # 頁面帶上一輪的 session + last_offset 來:席換了對話(壓縮 / 接回 / /clear)或檔比 offset 還短 ⇒ 從頭讀 + reset
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if (session and session.lower() != cur_sess) or offset > size:
            offset, rotated = 0, True
    if follow and offset and not (since or before or only_id):
        # 長輪詢(v0.6):頁面帶上一輪的 last_offset 來等,有新東西才回;換檔 ⇒ 從頭讀並 reset
        path2, _grew = _wait_for_growth(rec, path, offset, wait, sleep=sleep, clock=clock)
        if path2 != path:
            path, offset, rotated = path2, 0, True
    events, pending, _q, end, sessions = events_from_jsonl(path, with_thinking, with_sidechain, offset if offset and not (since or before or only_id) else 0)
    reset, since_lost = rotated, False
    if only_id:
        events = [e for e in events if e["id"] == only_id]
    elif since:
        idx = next((i for i, e in enumerate(events) if e["id"] == since), None)
        if idx is None:
            reset, since_lost = True, True
        else:
            events = events[idx + 1:]
    elif before:
        idx = next((i for i, e in enumerate(events) if e["id"] == before), None)
        events = events[:idx] if idx is not None else []
    truncated = False
    if limit and len(events) > limit:
        truncated = True
        events = events[:limit] if since else events[-limit:]
    if do_redact:
        events = [dict(e, text=redact(e["text"])) for e in events]
    events = clip_events(events, clip)
    out = {"v": V, "ok": True, "seat": code, "session": str(rec.get("session_uuid") or ""), "file": path, "at": int(time.time()),
           "events": events, "last_id": events[-1]["id"] if events else (since or None), "last_offset": end,
           "truncated": truncated, "reset": reset, "since_lost": since_lost, "sessions_in_file": sessions}
    return out, 0


# ── C3 pending ──

def _screen(rec, tmux="tmux", lines=40):
    sock, wid = rec.get("socket"), rec.get("window_id")
    if not sock or not wid or not re.match(r"^aihub(?:-[0-9A-Za-z-]+)?$", str(sock)) or not re.match(r"^@\d+$", str(wid)):
        return None
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        p = subprocess.run([tmux, "-L", sock, "capture-pane", "-p", "-t", wid, "-S", "-%d" % lines], capture_output=True,
                           text=True, env=env, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def pending(code, state_root=None, screen=None, env=None):
    import seat_ask_patterns as SP
    rec = find_seat(code, state_root)
    if rec is None:
        return {"v": V, "ok": False, "why": "no-seat", "seat": code}, 2
    provider = str(rec.get("provider") or "claude")
    out = {"v": V, "ok": True, "seat": code, "ask": None, "busy": "unknown", "queued": 0, "at_prompt": None, "hold_count": None, "now": None}
    path = transcript_path(rec)
    if path:
        try:
            events, pend, queue_ops, _end, _s = events_from_jsonl(path)
        except OSError:
            events, pend, queue_ops = [], [], 0
        out["queued"] = queue_ops_after_last_turn(events, queue_ops)
        if pend:
            e = pend[-1]
            qs = e["meta"]["questions"]
            first = qs[0] if qs else {}
            out["ask"] = {"id": e["id"], "source": "transcript", "type": "question", "prompt": e["text"],
                          "options": [{"key": str(i + 1), "label": o.get("label"), "description": o.get("description"), "preview": o.get("preview")}
                                      for i, o in enumerate(first.get("options") or [])],
                          "multi": bool(first.get("multi")), "questions": qs, "since": int(e["ts"] or 0) or None}
    scr = screen if screen is not None else _screen(rec)
    if scr is not None:
        if out["ask"] is None:
            pa = SP.permission_ask(scr, provider)
            if pa:
                out["ask"] = {"id": SP.ask_id(pa), "source": "screen", "type": "permission", "prompt": pa["prompt"],
                              "options": pa["options"], "multi": False, "since": None}
        out["busy"] = SP.busy_state(scr, provider)
        out["at_prompt"] = SP.at_prompt(scr, provider)
    if out["ask"]:
        out["busy"] = "waiting"
        out["at_prompt"] = False
    try:
        import dm_queue as DQ
        out["hold_count"] = sum(1 for it in DQ.list_items(code, env) if it.get("status") == DQ.HOLD_STATUS)
    except Exception:  # noqa: BLE001
        out["hold_count"] = None
    try:
        import seat_now
        now = seat_now.read(code, state_root=state_root)
        out["now"] = {k: now.get(k) for k in ("item", "state", "pct", "steps_done", "steps_total", "at")} if now else None
    except Exception:  # noqa: BLE001
        out["now"] = None
    return out, 0


def queue_ops_after_last_turn(events, queue_ops):
    """最後一筆 user / assistant 之後的 queue-operation 筆數 = 排隊中 n 則(events_from_jsonl 已經在那時歸零)。"""
    return int(queue_ops)


# ── CLI ──

def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(prog="aib seat", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("transcript")
    t.add_argument("code")
    t.add_argument("--since")
    t.add_argument("--before")
    t.add_argument("--id", dest="only_id")
    t.add_argument("--offset", type=int, default=0)
    t.add_argument("--limit", type=int, default=LIMIT_DEFAULT)
    t.add_argument("--clip", type=int, default=0)
    t.add_argument("--with-thinking", action="store_true")
    t.add_argument("--with-sidechain", action="store_true")
    t.add_argument("--redact", action="store_true")
    t.add_argument("--follow", action="store_true", help="帶 --offset 時:等到有新事件(或 --wait 秒)才回")
    t.add_argument("--wait", type=int, default=FOLLOW_WAIT_DEFAULT)
    t.add_argument("--session", help="上一輪回傳的 session;跟現在不同 ⇒ 從頭讀並 reset")
    t.add_argument("--json", action="store_true")
    p = sub.add_parser("pending")
    p.add_argument("code")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.cmd == "transcript":
        res, rc = transcript(a.code, since=a.since, before=a.before, only_id=a.only_id, offset=a.offset, limit=a.limit, clip=a.clip,
                             with_thinking=a.with_thinking, with_sidechain=a.with_sidechain, do_redact=a.redact,
                             follow=a.follow, wait=a.wait, session=a.session)
        if a.json or not res.get("ok"):
            out.write(json.dumps(res, ensure_ascii=False) + "\n")
        else:
            for e in res["events"]:
                out.write("[%s] %s\n" % (e["kind"], e["text"].replace("\n", " ")[:160]))
        return rc
    if a.cmd == "pending":
        res, rc = pending(a.code)
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return rc
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
