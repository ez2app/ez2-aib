#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""手機遠端指揮的本機連線(aib seat remote link):本機只往外連 ez2.page,長輪詢拿手機下的指令、照做、回條子。

- 只在 remote.json 打開時跑(remote_grant.remote_cfg;打開要本機 Touch ID);每輪重讀，關了就停。
- 網址固定 https://ez2.page/api/v1/remote/*,憑證 = aib page bind 存的助理憑證(Bearer);不從環境變數換網址(測試從參數注入)。
- 指令四種:
  unlock   = 一定要帶伺服器簽的解鎖單(seat_act.do_unlock grant=…;驗簽在 remote_grant)
  input    = 只在視窗開著時送(window_only ⇒ 沒開回 locked,不在 Mac 上彈 Touch ID);視窗要是解鎖單開的(@abb89 10-21:本人在 Mac 上 Touch ID 開的不算)
  stop     = 同上，解鎖單開的視窗開著才停
  lock     = 立刻上鎖(收權限方向，不用單子)
  unlocked = 只讀解鎖單開的視窗剩幾秒
  pending  = 讀那一席現在有沒有在問(remote_grant.ask_view:題目 + 選項 + ask_id、截過就標 truncated);解鎖單開的視窗開著才給
  answer   = 回答黃框(手機對顯示的題目正文 + 選項算 q_sha256 放進單子;核心對真題目驗;截過的題目只能在電腦上答):每次都要一張回答單(kind=seat-answer,綁 ask_id + choice + 自填字雜湊;@abb89 10-21 裁「視窗不能替 answer 免驗」)
  seats    = 這台有哪些席(手機選席用):只給席碼 / 角色 / 廠商 / 視窗剩幾秒;不給專案名、路徑、session
  transcript = 讀那一席的對話(手機看回覆用):也要視窗開著;遮敏感字(seat_panel redact)、每則截 2000 字、一次最多 40 則;
             不給本機路徑、工具輸入 / 結果、thinking(slim_transcript)
- 伺服器回的東西是資料不是指令：只認上面四種 op、席碼格式、字數上限;其餘一律回 bad-op。
- 憑證不印、不寫檔、不進 argv。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

BASE = "https://ez2.page"
PREFIX = "/api/v1/remote"
USER_AGENT = "ez2-aib-remote/1"
POLL_S = 25
MAX_BODY = 96 * 1024
SEAT_RE = re.compile(r"[0-9a-z]{5}")
ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
OPS = ("unlock", "lock", "input", "stop", "unlocked", "transcript", "pending", "answer", "seats")
ROLE_RE = re.compile(r"[\w .:/-]{0,40}")
TRANSCRIPT_LIMIT = 40
TRANSCRIPT_CLIP = 2000


def http(method, url, token, body=None, timeout=POLL_S + 10):
    """(status, dict|None);連不上 ⇒ (0, None)"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"accept": "application/json", "user-agent": USER_AGENT, "authorization": "Bearer " + token}
    if data is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener().open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(MAX_BODY + 1)
    except (urllib.error.URLError, OSError):
        return 0, None
    if len(raw) > MAX_BODY:
        return status, None
    try:
        d = json.loads(raw.decode("utf-8")) if raw.strip() else None
    except ValueError:
        d = None
    return status, d if isinstance(d, dict) else None


def target(env=None):
    """((token, 機器指紋), None) 或 (None, why)。只吃綁到這台的那一頁。"""
    e = dict(os.environ if env is None else env)
    if str(HERE / "page") not in sys.path:
        sys.path.insert(0, str(HERE / "page"))
    try:
        import store
        rec = store.only_token(e)
    except Exception:   # 綁了好幾頁 / 讀不到 ⇒ 不猜
        return None, "bind-ambiguous"
    if not rec:
        return None, "not-bound"
    import remote_grant
    fp, _accts = remote_grant.this_machine(e)
    tok = rec.get("assistant_token")
    if not fp or remote_grant.machine_fp(rec.get("machine_id")) != fp or not isinstance(tok, str) or not tok:
        return None, "not-bound"
    return (tok, fp), None


def handle(msg, env=None, act=None):
    """一則伺服器訊息 ⇒ 核心回條(dict)"""
    import seat_act
    SA = act or seat_act
    op, seat = msg.get("op"), msg.get("seat")
    if op not in OPS:
        return {"ok": False, "why": "bad-op"}
    if op == "seats":
        return seats(env, SA)
    if not isinstance(seat, str) or not SEAT_RE.fullmatch(seat):
        return {"ok": False, "why": "bad-seat"}
    if op == "unlock":
        if not isinstance(msg.get("grant"), dict):
            return {"ok": False, "action": "unlock", "seat": seat, "why": "grant-required"}
        res, _rc = SA.do_unlock(seat, None, env=env, grant=msg["grant"])
    elif op == "lock":
        res, _rc = SA.do_lock(seat, env=env)
    elif op == "input":
        res, _rc = SA.do_input(seat, msg.get("text"), env=env, window_only=True)
    elif op == "stop":
        res, _rc = SA.do_stop(seat, hard=msg.get("hard") is True, env=env, window_only=True)
    elif op == "answer":
        if not isinstance(msg.get("grant"), dict):
            return {"ok": False, "action": "answer", "seat": seat, "why": "grant-required"}
        choice, ask_id, text = msg.get("choice"), msg.get("ask_id"), msg.get("text")
        if not isinstance(choice, str) or not isinstance(ask_id, str) or (text is not None and not isinstance(text, str)):
            return {"ok": False, "action": "answer", "seat": seat, "why": "bad-choice"}
        res, _rc = SA.do_answer(seat, choice, ask_id=ask_id, free_text=text, env=env, grant=msg["grant"])
    elif op in ("transcript", "pending"):
        if SA.remote_left(seat, env) <= 0:   # 只認解鎖單開的視窗(本人在 Mac 上開的不算)
            return {"ok": False, "action": op, "seat": seat, "why": "locked"}
        import remote_grant
        import seat_panel
        if op == "pending":
            res, _rc = seat_panel.pending(seat, state_root=None if env is None else env.get("EZ2_AIB_STATE_ROOT"), env=env)
            ask = res.get("ask") if isinstance(res.get("ask"), dict) else None
            return {"ok": res.get("ok") is True, "action": "pending", "seat": seat, "at_prompt": res.get("at_prompt") is True, "busy": res.get("busy"),
                    "ask": None if not ask else remote_grant.ask_view(ask)}
        off = msg.get("offset")
        sess = msg.get("session")
        res, _rc = seat_panel.transcript(seat, offset=off if type(off) is int and off >= 0 else 0, limit=TRANSCRIPT_LIMIT, clip=TRANSCRIPT_CLIP,
                                         do_redact=True, state_root=None if env is None else env.get("EZ2_AIB_STATE_ROOT"),
                                         session=sess if isinstance(sess, str) and len(sess) <= 64 else None)
        res = slim_transcript(res)
    else:
        res = {"ok": True, "action": "unlocked", "seat": seat, "unlock_left_s": SA.remote_left(seat, env)}   # 手機看到的 = 解鎖單開的視窗
    return res


PHONE_KINDS = ("user", "assistant", "ask", "tool_call", "system", "dm")


def slim_transcript(res):
    """出本機前瘦身：不給本機路徑(file)、工具輸入 / 結果(meta、tool_result)、thinking;每則只留 id / ts / kind / text"""
    if not res.get("ok"):
        return {k: res.get(k) for k in ("ok", "why", "seat")}
    ev = [{"id": e.get("id"), "ts": e.get("ts"), "kind": e.get("kind"), "text": e.get("text")} for e in res.get("events") or [] if e.get("kind") in PHONE_KINDS]
    return {"ok": True, "action": "transcript", "seat": res.get("seat"), "session": res.get("session"), "events": ev,
            "last_id": res.get("last_id"), "last_offset": res.get("last_offset"), "truncated": res.get("truncated"), "reset": res.get("reset")}


def seats(env=None, SA=None):
    import seat_address
    import seat_panel
    e = env or {}
    rows = []
    for r in seat_panel.seat_records(e.get("EZ2_AIB_STATE_ROOT")):
        code = seat_address.code_of(r)
        if not isinstance(code, str) or not SEAT_RE.fullmatch(code) or any(x["seat"] == code for x in rows):
            continue
        role = str(r.get("role") or "")
        rows.append({"seat": code, "role": role if ROLE_RE.fullmatch(role) else "", "provider": str(r.get("provider") or "")[:16],
                     "unlock_left_s": SA.remote_left(code, env)})
    return {"ok": True, "action": "seats", "seats": rows[:40]}


def link(token, machine, env=None, base=BASE, call=http, once=False, sleep=time.sleep, act=None, log=None):
    """長輪詢主迴圈;回 (summary, rc)。base / call / sleep 只給測試注入。"""
    import remote_grant
    done, errs = 0, 0
    while True:
        if not remote_grant.remote_cfg(env)["on"]:
            return {"ok": False, "action": "remote-link", "why": "remote-off", "handled": done}, 3
        status, msg = call("GET", "%s%s/next?machine=%s" % (base, PREFIX, machine), token)
        if status == 200 and msg and ID_RE.fullmatch(str(msg.get("id") or "")):
            errs = 0
            res = handle(msg, env, act)
            call("POST", "%s%s/reply" % (base, PREFIX), token, {"id": msg["id"], "machine": machine, "result": res})
            done += 1
            if log:
                log({"id": msg["id"], "op": msg.get("op"), "seat": msg.get("seat"), "ok": res.get("ok"), "why": res.get("why")})
        elif status == 204:
            errs = 0
        elif status in (401, 403):
            return {"ok": False, "action": "remote-link", "why": "unauthorized", "handled": done}, 3
        else:
            errs += 1
            sleep(min(60, 2 ** min(errs, 6)))
        if once:
            return {"ok": True, "action": "remote-link", "handled": done, "status": status}, 0


# ── 背景跑(aib seat remote on 一鍵：開關 + 起連線;off 一起停)──

def _paths(env=None):
    import remote_grant
    d = remote_grant.state_root(env) / "panel-unlock"
    return d / "remote-link.pid", remote_grant.state_root(env) / "logs" / "remote-link.log"


def _is_link(pid, ps=None):
    """pid 還活著、而且真的是 aib seat remote link(防 pid 被別的程式重用 ⇒ 誤殺)"""
    import subprocess
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    try:
        cmd = (ps or (lambda p: subprocess.run(["ps", "-o", "command=", "-p", str(p)], capture_output=True, text=True, timeout=5).stdout))(pid)
    except Exception:   # ps 不能用 ⇒ 當作不是，寧可不殺
        return False
    return "seat_act.py" in cmd and "remote" in cmd and "link" in cmd


def running(env=None, ps=None):
    pidf, _log = _paths(env)
    try:
        pid = int(pidf.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if _is_link(pid, ps) else None


def start_bg(env=None, spawn=None, ps=None):
    """((pid, None) | (None, why))。已在跑 ⇒ 回原本那支;還沒綁 ez2.page ⇒ 不起"""
    import subprocess
    pid = running(env, ps)
    if pid:
        return pid, None
    _t, why = target(env)
    if why:
        return None, why
    pidf, log = _paths(env)
    pidf.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    e = dict(os.environ if env is None else env)
    with open(log, "ab") as fh:
        p = (spawn or subprocess.Popen)([sys.executable, str(HERE / "seat_act.py"), "remote", "link"], stdin=subprocess.DEVNULL, stdout=fh,
                                         stderr=subprocess.STDOUT, start_new_session=True, env=e, cwd=str(HERE))
    pidf.write_text(str(p.pid), encoding="utf-8")
    return p.pid, None


def stop_bg(env=None, ps=None, kill=None):
    import signal
    pidf, _log = _paths(env)
    pid = running(env, ps)
    if pid:
        (kill or os.kill)(pid, signal.SIGTERM)
    try:
        pidf.unlink()
    except OSError:
        pass
    return pid


def main_link(once=False, out=sys.stdout, env=None):
    import remote_grant
    if not remote_grant.remote_cfg(env)["on"]:
        return {"ok": False, "action": "remote-link", "why": "remote-off"}, 3
    t, why = target(env)
    if why:
        return {"ok": False, "action": "remote-link", "why": why}, 3

    def log(row):
        out.write(json.dumps(dict(row, at=int(time.time())), ensure_ascii=False) + "\n")
        out.flush()
    return link(t[0], t[1], env=env, once=once, log=log)
