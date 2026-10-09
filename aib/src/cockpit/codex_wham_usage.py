#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""One-shot ChatGPT wham/usage probe for a Codex auth.json."""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional

WHAM_URL = "https://chatgpt.com/backend-api/wham/usage"
TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _b64json(seg: str) -> dict[str, Any]:
    pad = "=" * ((4 - len(seg) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(seg + pad))


def chatgpt_account_id(auth: dict[str, Any]) -> str:
    toks = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    for key in ("account_id", "chatgpt_account_id"):
        val = auth.get(key) or toks.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    idt = str(toks.get("id_token") or "")
    parts = idt.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = _b64json(parts[1])
    except Exception:
        return ""
    authz = payload.get("https://api.openai.com/auth") or {}
    if isinstance(authz, dict):
        aid = authz.get("chatgpt_account_id") or authz.get("account_id")
        if isinstance(aid, str):
            return aid.strip()
    aid = payload.get("chatgpt_account_id")
    return aid.strip() if isinstance(aid, str) else ""


def _window_label(seconds: Any) -> str:
    try:
        sec = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if sec <= 0:
        return ""
    if sec <= 6 * 3600:
        return "5h"
    if sec <= 8 * 86400:
        return "7d"
    return "30d"


def rows_from_wham_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    rl = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
    rows: list[dict[str, Any]] = []
    for key in ("primary_window", "secondary_window"):
        win = rl.get(key)
        if not isinstance(win, dict):
            continue
        label = _window_label(win.get("limit_window_seconds"))
        if not label:
            continue
        try:
            pct = int(round(float(win.get("used_percent"))))
        except (TypeError, ValueError):
            continue
        rows.append({"window": label, "used_pct": max(0, min(100, pct))})
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["window"] in seen:
            continue
        seen.add(row["window"])
        out.append(row)
    return out



def refresh_auth_file(auth_path: str) -> dict[str, Any]:
    path = Path(auth_path)
    auth = json.loads(path.read_text(encoding="utf-8"))
    toks = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    rt = toks.get("refresh_token")
    if not rt:
        raise RuntimeError("no_refresh_token")
    client_id = str(auth.get("client_id") or DEFAULT_CLIENT_ID)
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": client_id,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
        tok = json.loads(resp.read().decode())
    for k in ("access_token", "refresh_token", "id_token"):
        if tok.get(k):
            toks[k] = tok[k]
    auth["tokens"] = toks
    path.write_text(json.dumps(auth, indent=2) + "\n", encoding="utf-8")
    return auth



WHAM_RESET_CREDITS_URL = (
    "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
)
WHAM_RESET_CREDITS_CONSUME_URL = (
    "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume"
)

_RESET_TITLE_ZH = {
    "Full reset (Weekly + 5 hr)": "完整重置（每周+5小時）",
    "Full reset (Weekly + 5h)": "完整重置（每周+5小時）",
    "Full reset (weekly + 5 hr)": "完整重置（每周+5小時）",
}


def localize_reset_title(title: Optional[str]) -> str:
    if not title:
        return "重置"
    t = str(title).strip()
    return _RESET_TITLE_ZH.get(t, t)


def format_expires_taipei(expires_at: Optional[str]) -> Optional[str]:
    if not expires_at or not isinstance(expires_at, str):
        return None
    raw = expires_at.strip()
    if not raw:
        return None
    try:
        from datetime import datetime, timezone, timedelta
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(timezone(timedelta(hours=8)))
        return local.strftime("%m/%d %H:%M 到期")
    except Exception:
        return None


def parse_reset_credits_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    block = payload.get("rate_limit_reset_credits")
    if not isinstance(block, dict):
        block = payload.get("rate_limit_reset_credit")
    out: dict[str, Any] = {"available_count": 0, "credits": []}
    if not isinstance(block, dict):
        return out
    for key in ("available_count", "applicable_available_count"):
        val = block.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = int(val)
    return out


def parse_reset_credits_list(payload: Mapping[str, Any]) -> dict[str, Any]:
    credits_in = payload.get("credits") if isinstance(payload.get("credits"), list) else []
    available = payload.get("available_count")
    if not isinstance(available, (int, float)) or isinstance(available, bool):
        available = None
    parsed = []
    for item in credits_in:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if not isinstance(cid, str) or not cid.strip():
            continue
        status = item.get("status")
        status_s = str(status).strip().lower() if status is not None else ""
        title_raw = item.get("title") if isinstance(item.get("title"), str) else ""
        expires = item.get("expires_at") if isinstance(item.get("expires_at"), str) else None
        entry = {
            "id": cid.strip(),
            "title": localize_reset_title(title_raw),
            "title_raw": title_raw,
            "expires_at": expires,
            "expires_label": format_expires_taipei(expires),
            "status": status_s or "unknown",
        }
        parsed.append(entry)
    available_only = [c for c in parsed if c["status"] == "available"]
    if available is None:
        available = len(available_only)
    else:
        available = int(available)
    return {"available_count": available, "credits": available_only}


def fetch_wham_rows(auth_path: str, try_refresh: bool = True) -> dict[str, Any]:
    path = Path(auth_path)
    if not path.is_file():
        return {"ok": False, "rows": [], "error": "missing_auth"}
    auth = json.loads(path.read_text(encoding="utf-8"))

    def _once(a: dict[str, Any]) -> dict[str, Any]:
        at = (a.get("tokens") or {}).get("access_token")
        aid = chatgpt_account_id(a)
        if not at or not aid:
            return {"ok": False, "rows": [], "error": "missing_token_or_account"}
        req = urllib.request.Request(
            WHAM_URL,
            headers={
                "Authorization": "Bearer %s" % at,
                "ChatGPT-Account-Id": aid,
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
            body = json.loads(resp.read().decode())
        if not isinstance(body, dict):
            return {"ok": False, "rows": [], "error": "bad_body"}
        summary = parse_reset_credits_summary(body)
        return {
            "ok": True,
            "rows": rows_from_wham_body(body),
            "email": body.get("email") or "",
            "plan_type": body.get("plan_type") or "",
            "reset_credits": summary,
        }

    try:
        return _once(auth)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:400]
        except Exception:
            pass
        revoked = exc.code == 401 and (
            "token_revoked" in detail
            or "refresh_token_invalidated" in detail
            or "invalidated" in detail
        )
        if try_refresh and exc.code in (401, 403):
            try:
                auth2 = refresh_auth_file(str(path))
                return _once(auth2)
            except Exception as exc2:
                err2 = str(exc2)
                revoked2 = (
                    "refresh_token_invalidated" in err2
                    or "session has ended" in err2.lower()
                )
                return {
                    "ok": False,
                    "rows": [],
                    "error": "refresh_failed:%s" % err2[:160],
                    "revoked": bool(revoked or revoked2),
                }
        return {
            "ok": False,
            "rows": [],
            "error": "http_%s:%s" % (exc.code, detail[:120]),
            "revoked": bool(revoked),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rows": [], "error": str(exc)[:160]}


def _wham_headers(access_token: str, account_id: str) -> dict[str, str]:
    h = {
        "Authorization": "Bearer %s" % access_token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }
    if account_id:
        h["ChatGPT-Account-Id"] = account_id
    return h


def fetch_reset_credits_list(auth_path: str, try_refresh: bool = True) -> dict[str, Any]:
    path = Path(auth_path)
    if not path.is_file():
        return {"ok": False, "available_count": 0, "credits": [], "error": "missing_auth"}
    auth = json.loads(path.read_text(encoding="utf-8"))

    def _once(a: dict[str, Any]) -> dict[str, Any]:
        at = (a.get("tokens") or {}).get("access_token")
        aid = chatgpt_account_id(a)
        if not at or not aid:
            return {"ok": False, "available_count": 0, "credits": [], "error": "missing_token_or_account"}
        req = urllib.request.Request(
            WHAM_RESET_CREDITS_URL,
            headers=_wham_headers(str(at), str(aid)),
        )
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
            body = json.loads(resp.read().decode())
        if not isinstance(body, dict):
            return {"ok": False, "available_count": 0, "credits": [], "error": "bad_body"}
        parsed = parse_reset_credits_list(body)
        return {"ok": True, **parsed}

    try:
        return _once(auth)
    except urllib.error.HTTPError as exc:
        if try_refresh and exc.code in (401, 403):
            try:
                return _once(refresh_auth_file(str(path)))
            except Exception as exc2:  # noqa: BLE001
                return {"ok": False, "available_count": 0, "credits": [], "error": "refresh_failed:%s" % str(exc2)[:120]}
        return {"ok": False, "available_count": 0, "credits": [], "error": "http_%s" % exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "available_count": 0, "credits": [], "error": str(exc)[:160]}


def redeem_reset_credit(auth_path: str, credit_id: str, redeem_request_id: Optional[str] = None) -> dict[str, Any]:
    import uuid
    path = Path(auth_path)
    if not path.is_file():
        return {"ok": False, "error": "missing_auth", "detail": "找不到 auth.json"}
    if not isinstance(credit_id, str) or not credit_id.strip():
        return {"ok": False, "error": "no_credit", "detail": "沒有可用的重置額度"}
    auth = json.loads(path.read_text(encoding="utf-8"))
    at = (auth.get("tokens") or {}).get("access_token")
    aid = chatgpt_account_id(auth)
    if not at or not aid:
        return {"ok": False, "error": "missing_token_or_account", "detail": "登入資料不完整"}
    rid = redeem_request_id or str(uuid.uuid4())
    body = json.dumps({"redeem_request_id": rid, "credit_id": credit_id.strip()}).encode("utf-8")
    headers = _wham_headers(str(at), str(aid))
    headers["Content-Type"] = "application/json"

    def _post(a_token: str, a_aid: str) -> dict[str, Any]:
        h = _wham_headers(a_token, a_aid)
        h["Content-Type"] = "application/json"
        req = urllib.request.Request(
            WHAM_RESET_CREDITS_CONSUME_URL, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            payload = json.loads(resp.read().decode())
            status = getattr(resp, "status", 200) or 200
        if not isinstance(payload, dict):
            return {"ok": False, "error": "bad_body", "detail": "回傳格式不對"}
        code = payload.get("code")
        ok = isinstance(code, str) and code in ("reset", "already_redeemed")
        return {
            "ok": ok,
            "code": code if isinstance(code, str) else ("http_%s" % status),
            "payload": payload,
            "redeem_request_id": rid,
            "detail": ("已使用重置" if ok else ("使用重置失敗" + (": %s" % code if code else ""))),
        }

    try:
        return _post(str(at), str(aid))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            pass
        if exc.code in (404, 405):
            return {"ok": False, "error": "api_missing", "detail": "API 未接上", "http_status": exc.code}
        if exc.code in (401, 403):
            try:
                auth2 = refresh_auth_file(str(path))
                at2 = (auth2.get("tokens") or {}).get("access_token")
                aid2 = chatgpt_account_id(auth2)
                return _post(str(at2), str(aid2))
            except Exception as exc2:  # noqa: BLE001
                return {"ok": False, "error": "token_expired", "detail": "登入已失效：%s" % str(exc2)[:120], "http_status": exc.code}
        return {
            "ok": False,
            "error": "redeem_fail",
            "detail": "使用重置失敗（HTTP %s）%s" % (exc.code, (": " + detail[:80] if detail else "")),
            "http_status": exc.code,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "redeem_fail", "detail": "使用重置失敗：%s" % str(exc)[:120]}


def enrich_with_reset_credits(got: dict[str, Any], auth_path: str) -> dict[str, Any]:
    """On successful wham probe, fill credits list (titles + expiry)."""
    if not isinstance(got, dict):
        return got
    empty = {"available_count": 0, "credits": []}
    if not got.get("ok"):
        got.setdefault("reset_credits", empty)
        return got
    listed = fetch_reset_credits_list(auth_path, try_refresh=False)
    if listed.get("ok"):
        got["reset_credits"] = {
            "available_count": int(listed.get("available_count") or 0),
            "credits": list(listed.get("credits") or []),
        }
    else:
        # keep summary-only from usage body if present
        rc = got.get("reset_credits") if isinstance(got.get("reset_credits"), dict) else empty
        got["reset_credits"] = {
            "available_count": int(rc.get("available_count") or 0),
            "credits": list(rc.get("credits") or []),
        }
        if listed.get("error"):
            got["reset_credits_error"] = str(listed.get("error"))[:120]
    return got
