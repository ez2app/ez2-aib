#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""手機遠端指揮的本機連線(aib seat remote link):本機只往外連 ez2.page,長輪詢拿手機下的指令、照做、回條子。

- 只在 remote.json 打開時跑(remote_grant.remote_cfg;打開要本機 Touch ID);每輪重讀，關了就停。
- 網址固定 https://ez2.page/api/v1/remote/*,憑證 = aib page bind 存的助理憑證(Bearer);不從環境變數換網址(測試從參數注入)。
- 指令四種:
  unlock   = 一定要帶伺服器簽的解鎖單(seat_act.do_unlock grant=…;驗簽在 remote_grant)
  input    = 只在視窗開著時送(window_only ⇒ 沒開回 locked,不在 Mac 上彈 Touch ID)
  stop     = 同上，視窗開著才停
  unlocked = 只讀剩幾秒
  pending  = 讀那一席現在有沒有在問(黃框題目 + 選項 + ask_id);視窗開著才給
  answer   = 回答黃框：每次都要一張回答單(kind=seat-answer,綁 ask_id + choice + 自填字雜湊;@abb89 10-21 裁「視窗不能替 answer 免驗」)
  transcript = 讀那一席的對話(手機看回覆用):也要視窗開著;遮敏感字(seat_panel redact)、每則截 2000 字、一次最多 40 則
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
OPS = ("unlock", "input", "stop", "unlocked", "transcript", "pending", "answer")
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
    if not isinstance(seat, str) or not SEAT_RE.fullmatch(seat):
        return {"ok": False, "why": "bad-seat"}
    if op == "unlock":
        if not isinstance(msg.get("grant"), dict):
            return {"ok": False, "action": "unlock", "seat": seat, "why": "grant-required"}
        res, _rc = SA.do_unlock(seat, None, env=env, grant=msg["grant"])
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
        left = SA.do_unlocked(seat, env=env)[0].get("unlock_left_s") or 0
        if left <= 0:
            return {"ok": False, "action": op, "seat": seat, "why": "locked"}
        import seat_panel
        if op == "pending":
            res, _rc = seat_panel.pending(seat, state_root=None if env is None else env.get("EZ2_AIB_STATE_ROOT"), env=env)
            return res
        off = msg.get("offset")
        sess = msg.get("session")
        res, _rc = seat_panel.transcript(seat, offset=off if type(off) is int and off >= 0 else 0, limit=TRANSCRIPT_LIMIT, clip=TRANSCRIPT_CLIP,
                                         do_redact=True, state_root=None if env is None else env.get("EZ2_AIB_STATE_ROOT"),
                                         session=sess if isinstance(sess, str) and len(sess) <= 64 else None)
    else:
        res, _rc = SA.do_unlocked(seat, env=env)
    return res


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
