#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""一鍵綁定的核心:不阻塞的 `aib page bind --start / --poll / --confirm / --cancel --json`(0.1.37)。

契約:pending/CONTRACT-bind-start-poll-74e8b-1021.md(@74e8b 起草、@abb89 14:5x 審),加 @abb89 的四條:
  1. start 帶出伺服器發的 confirm_digits(兩位**字串**,前導 0 留著);它只走 stdout 給呼叫端畫在畫面上,
     **不寫進狀態檔、不進 log、不進 qr_url / liff_url**。伺服器沒給兩位數 ⇒ start 回 ok:false(艦長室不開卡片)。
  2. poll 不彈 Touch ID:伺服器說綁好了 ⇒ state=confirm_local + slug + token_expires_at(憑證到期,不是配對碼到期;@74e8b);本人在頁面按了才叫 --confirm(這一步才彈)。
  3. 「24 小時內只問一次」只限同機同帳號、紀錄要 Secure Enclave 簽 —— 這版**沒做免問**,每次 confirm 都彈 Touch ID
     (比契約嚴、不比契約鬆;免問那段要 SE 簽 + 驗簽,另開一張)。
  4. state 多 cancelled;poll 到 expires_at 為止。

狀態檔 <狀態根>/auth/page-bind/bind-pending.json(0600、目錄 0700;不跟憑證同目錄,list_tokens 會把它當一頁):
  {"v":1, "state":"waiting|confirm_local", "ticket", "expires_at", "machine", "seat_label", "started_at",
   "record"(只在 confirm_local:伺服器回的 bound 記錄,本人按了才搬進正式憑證檔)}
  配對碼、確認數字都不寫進來。ticket 一次性:拿到 bound / expired / declined / cancelled 就刪檔。
  剩餘風險(誠實講):confirm_local 那段 record 裡有 assistant_token,同帳號 AI 讀得到 —— 但它本來就讀得到正式憑證檔,
  不是新開的洞;Touch ID 擋的是「AI 自己把配對完成、本人不知道」。

已經有一張沒過期的 ⇒ **重開一張**(契約寫「回同一張」;但確認數字不准落檔,回同一張就沒有數字可顯示)。舊 ticket 丟掉,
伺服器那張自己過期。
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import atomic_json
import store
from api import ApiError, TransportError

V = 1
PENDING_NAME = "bind-pending.json"
CONFIRM_WINDOW_S = 600              # 伺服器說綁好之後,本人要在 10 分鐘內按確認;過了就作廢
DIGITS_RE = re.compile(r"^[0-9]{2}$")
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{3,31}$")
OLD_SERVER_NO_MACHINE = "page_api_seat_label_invalid"     # 跟 #232 同一個判斷:只有這個碼才退回不帶 machine
QR_HOSTS = ("ez2.page",)
LIFF_HOSTS = ("liff.line.me",)
MANAGE_URL = "https://ez2.page/manage/aib"
STATES = ("none", "waiting", "confirm_local", "bound", "expired", "declined", "cancelled")


def _now(clock=None):
    return (clock or (lambda: datetime.now(timezone.utc)))()


def _iso(text):
    try:
        v = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def days_left(expires_at, clock=None):
    """還剩幾天(無條件進位;過期 ⇒ 0;解不出 ⇒ None)。充電座燈號:≤7 黃、0 紅。"""
    exp = _iso(expires_at)
    if exp is None:
        return None
    sec = (exp - _now(clock)).total_seconds()
    return 0 if sec <= 0 else int(math.ceil(sec / 86400.0))


def _host_ok(url, hosts):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    h = (u.hostname or "").lower()
    return u.scheme == "https" and not u.username and not u.password and any(h == x or h.endswith("." + x) for x in hosts)


def _has_digits(url, digits):
    """網址的 query / fragment 裡有沒有那兩位數當成一個值(防伺服器把數字塞進 QR)。"""
    if not (isinstance(digits, str) and digits):
        return False
    u = urlparse(url)
    return re.search(r"(?:^|[=&/])%s(?:$|[&#/])" % re.escape(digits), (u.query or "") + "&" + (u.fragment or "")) is not None


def qr_url_of(started, code):
    """QR 用伺服器給的 confirm_link(@9f808:不要自己拼 <slug>.ez2.page);舊伺服器 ⇒ confirm_url;都沒有 ⇒ /manage/aib?code=。
    只放 https 且主機在 ez2.page / *.ez2.page;確認數字絕不進來。"""
    digits = started.get("confirm_digits")
    for key in ("confirm_link", "confirm_url"):
        url = started.get(key)
        if isinstance(url, str) and _host_ok(url, QR_HOSTS):
            if _has_digits(url, digits):
                continue                    # 伺服器把數字放進網址了 ⇒ 不用這條
            if key == "confirm_url" and "code=" not in (urlparse(url).query or ""):
                return url + ("&" if "?" in url else "?") + "code=" + code
            return url
    return MANAGE_URL + "?code=" + code


def pending_path(env=None):
    """不放憑證目錄:store.list_tokens 會把那裡每個 .json 當成一頁(放進去 ⇒ only_token 變「綁了 2 頁」)。"""
    root, _src = store.state_root(env)
    return os.path.join(root, "auth", "page-bind", PENDING_NAME)


def read_pending(env=None):
    try:
        with open(pending_path(env), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("state") not in ("waiting", "confirm_local") or not isinstance(d.get("ticket"), str):
        return None
    return d


def _write_pending(d, env=None):
    path = pending_path(env)
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    atomic_json.write_json(path, d)
    os.chmod(path, 0o600)


def clear_pending(env=None):
    try:
        os.unlink(pending_path(env))
        return True
    except FileNotFoundError:
        return False


def bound_here(env=None, clock=None):
    """綁到這台的那一頁 ⇒ {"slug", "bind_expires_at", "days_left"};沒綁 / 綁好幾頁不猜 ⇒ None。給 remote status 用。"""
    try:                                    # 只讀:status 不准順手建 machine.json
        directory, _src = store.token_dir(env)
        with open(os.path.join(directory, "machine.json"), encoding="utf-8") as fh:
            mid = json.load(fh).get("id")
        recs = [r for r in store.list_tokens(env) if mid and r.get("machine_id") == mid]
    except Exception:  # noqa: BLE001  讀不到 = 不知道,不是錯
        return None
    if len(recs) != 1:
        return None
    r = recs[0]
    return {"slug": r.get("slug") or r.get("page_id"), "bind_expires_at": r.get("expires_at"),
            "days_left": days_left(r.get("expires_at"), clock)}


def _fail(why, **kw):
    return dict({"ok": False, "why": why}, **kw)


def start(api, seat_label, env=None, clock=None):
    """回 (結果 dict, rc)。結果裡的 confirm_digits 只給呼叫端顯示,這裡不寫檔、不記 log。"""
    if bound_here(env, clock):
        return _fail("already-bound"), 1
    if read_pending(env):
        clear_pending(env)            # 重開一張(數字不落檔,沒辦法回同一張)
    machine = store.machine_identity(env)
    try:
        try:
            started = api.bind_start(seat_label, machine)
        except ApiError as exc:
            if exc.code != OLD_SERVER_NO_MACHINE:
                raise
            started, machine = api.bind_start(seat_label), None
    except ApiError as exc:
        return _fail(_api_why(exc)), 1
    except TransportError:
        return _fail("page-api-unavailable"), 1
    code = started.get("pair_code") or ""
    ticket = started.get("bind_ticket") or ""
    exp = _iso(started.get("expires_at"))
    digits = started.get("confirm_digits")
    if not isinstance(code, str) or not CODE_RE.match(code) or not isinstance(ticket, str) or not ticket or exp is None:
        return _fail("bad-server-reply"), 1
    if not isinstance(digits, str) or not DIGITS_RE.match(digits):
        return _fail("no-confirm-digits"), 1          # 舊伺服器 ⇒ 艦長室不開卡片;終端機 `aib page bind` 照舊流程
    _write_pending({"v": V, "state": "waiting", "ticket": ticket, "expires_at": exp.isoformat(), "machine": machine,
                    "seat_label": seat_label, "started_at": _now(clock).isoformat()}, env)
    res = {"ok": True, "state": "waiting", "code": code, "confirm_digits": digits, "qr_url": qr_url_of(started, code),
           "expires_at": exp.isoformat(), "machine_name": (machine or {}).get("name") or ""}
    liff = started.get("liff_url")
    if isinstance(liff, str) and _host_ok(liff, LIFF_HOSTS) and not _has_digits(liff, digits):
        res["liff_url"] = liff
    return res, 0


def _api_why(exc):
    return {"page_api_token_limit_reached": "token-limit", "page_api_rate_limited": "rate-limited",
            "page_api_bind_busy": "page-busy", "page_api_seat_label_invalid": "seat-label-invalid"}.get(exc.code) or (exc.code or "page-api-error")


def poll(api, env=None, clock=None):
    """回 (結果, rc)。不彈 Touch ID;伺服器說綁好了 ⇒ 存成 confirm_local 等本人按。"""
    p = read_pending(env)
    if not p:
        return {"ok": True, "state": "none"}, 0
    now = _now(clock)
    if p["state"] == "confirm_local":
        at = _iso(p.get("confirmable_at"))
        if at is None or (now - at).total_seconds() > CONFIRM_WINDOW_S:
            clear_pending(env)
            return {"ok": True, "state": "expired", "why": "confirm-timeout", "manage_url": MANAGE_URL}, 0
        rec = p.get("record") or {}
        return {"ok": True, "state": "confirm_local", "slug": rec.get("slug") or rec.get("page_id"),
                "token_expires_at": rec.get("expires_at")}, 0
    exp = _iso(p.get("expires_at"))
    if exp is None or now >= exp:
        clear_pending(env)
        return {"ok": True, "state": "expired"}, 0
    try:
        polled = api.bind_poll(p["ticket"])
    except ApiError as exc:
        if exc.code in ("page_api_bind_expired", "page_api_bind_ticket_unknown"):   # 410:過期 / 被取走 / 確認數字錯 3 次
            clear_pending(env)
            return {"ok": True, "state": "expired", "why": exc.code}, 0
        return _fail(_api_why(exc), state="waiting"), 1
    except TransportError:
        return _fail("page-api-unavailable", state="waiting"), 1
    status = polled.get("status")
    if status == "pending":
        return {"ok": True, "state": "waiting", "expires_at": p["expires_at"]}, 0
    if status == "declined":
        clear_pending(env)
        return {"ok": True, "state": "declined"}, 0
    if status != "bound":
        return _fail("bad-server-reply", state="waiting"), 1
    machine = p.get("machine") or {}
    if machine and polled.get("machine_id") not in (None, machine.get("id")):
        clear_pending(env)
        return _fail("wrong-machine", state="expired", manage_url=MANAGE_URL), 1
    for key in ("assistant_token", "tenant_id", "page_id"):
        if not polled.get(key):
            clear_pending(env)
            return _fail("bad-server-reply", state="expired"), 1
    p.update(state="confirm_local", record=dict(polled), confirmable_at=now.isoformat())
    _write_pending(p, env)
    return {"ok": True, "state": "confirm_local", "slug": polled.get("slug") or polled.get("page_id"),
            "token_expires_at": polled.get("expires_at")}, 0


def confirm(env=None, clock=None, prove=None, caller_is_seat=None, reason_fn=None):
    """本人按「綁」之後:席位呼叫 ⇒ rc 2(不動);Touch ID 拒 ⇒ declined + 作廢;過了 ⇒ 寫正式憑證、回 bound。"""
    import seat_act as SA
    e = dict(os.environ if env is None else env)
    p = read_pending(e)
    if not p:
        return {"ok": True, "state": "none"}, 0
    if p["state"] != "confirm_local":
        return _fail("not-ready", state="waiting"), 1
    at = _iso(p.get("confirmable_at"))
    if at is None or (_now(clock) - at).total_seconds() > CONFIRM_WINDOW_S:
        clear_pending(e)
        return {"ok": True, "state": "expired", "why": "confirm-timeout", "manage_url": MANAGE_URL}, 0
    if (caller_is_seat or SA.caller_is_seat)(e):
        return _fail("seat_caller", state="confirm_local"), 2
    rec = dict(p.get("record") or {})
    slug = rec.get("slug") or rec.get("page_id") or ""
    reason = (reason_fn or _reason)(slug)
    _via, why = (prove or SA.prove)(reason, e)
    if why:
        clear_pending(e)
        return {"ok": True, "state": "declined", "why": why, "manage_url": MANAGE_URL}, 0
    rec["bound_at"] = _now(clock).isoformat()
    rec["seat_label"] = p.get("seat_label") or ""
    store.save_token(rec, e)
    clear_pending(e)
    return {"ok": True, "state": "bound", "slug": slug, "expires_at": rec.get("expires_at"),
            "days_left": days_left(rec.get("expires_at"), clock)}, 0


def _reason(slug):
    import approve_gate as AG        # 統一入口:類別 page_bind(高風險)、slug 清過截 40
    return AG.reason("page_bind.bind", {"slug": slug})


def cancel(env=None):
    """作廢本機這張。伺服器已經說綁好了(confirm_local)才取消 ⇒ 那把憑證已發出、會佔一個名額到過期,
    帶 manage_url 讓頁面叫人去後台解除(@abb89 審 #233 ①)。"""
    p = read_pending(env)
    clear_pending(env)
    res = {"ok": True, "state": "cancelled"}
    if p and p.get("state") == "confirm_local":
        res["manage_url"] = MANAGE_URL
    return res, 0
