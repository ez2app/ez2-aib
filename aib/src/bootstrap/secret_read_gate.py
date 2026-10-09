#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""金鑰讀取開關:算力不足就中斷;可設時段;預設用 LINE Quick Reply 提醒。

對齊 a server 既有 LINE Quick Reply 形狀(continue/cancel 兩顆,postback 只帶
opaque_decision_ref 與 choice_ref,不帶金鑰值)。真發送走可注入的 send,
測試不碰活 LINE。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aib_secrets as S

POLICY_SCHEMA = "aib-secret-read-policy/v1"
GRANTS_SCHEMA = "aib-secret-read-grants/v1"
TOKEN_SCHEMA = "aib-token-balance/v1"
POLICY_NAME = "read-policy.json"
GRANTS_NAME = "read-grants.json"
REMINDERS_NAME = "reminders.jsonl"

DISABLED = "disabled"
TOKEN_SHORT = "token_short"
NEEDS_CONFIRM = "needs_confirm"
OUTSIDE_WINDOW = "outside_window"

CHANNELS = ("line", "whatsapp", "telegram", "messenger")
DEFAULT_CHANNEL = "line"

DEFAULT_POLICY = {
    "schema": POLICY_SCHEMA,
    "enabled": True,
    "channel": DEFAULT_CHANNEL,
    "on_token_short": "interrupt",
    "defaults": {"window": None, "confirm_every_seconds": 0},
    "keys": {},
}


def _now(now):
    if now is None:
        return int(time.time())
    return int(now)


def _policy_path(env):
    return S.secrets_dir(env) / POLICY_NAME


def load_policy(env=None):
    path = _policy_path(env)
    if not path.is_file():
        return dict(DEFAULT_POLICY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_POLICY)
    if not isinstance(data, dict) or data.get("schema") != POLICY_SCHEMA:
        return dict(DEFAULT_POLICY)
    out = dict(DEFAULT_POLICY)
    out.update({k: data[k] for k in DEFAULT_POLICY if k in data})
    if not isinstance(out.get("keys"), dict):
        out["keys"] = {}
    if out.get("channel") not in CHANNELS:
        out["channel"] = DEFAULT_CHANNEL
    return out


def key_policy(name, policy=None, env=None):
    policy = policy if policy is not None else load_policy(env)
    row = dict(policy.get("defaults") or {})
    extra = (policy.get("keys") or {}).get(name) or {}
    if isinstance(extra, dict):
        row.update(extra)
    return row


def tokens_ok(env=None):
    """算力是否夠。檔不存在=計費尚未接上,不擋讀取。檔在且 ok=false 才中斷。"""
    d = S.secrets_dir(env)
    path = d.parent / "billing" / "tokens.json"
    if not path.is_file():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict) or data.get("schema") != TOKEN_SCHEMA:
        return True
    return data.get("ok") is True


def _in_window(window, now):
    if not window or not isinstance(window, dict):
        return True
    start = window.get("start") or "00:00"
    end = window.get("end") or "24:00"
    tzname = window.get("tz") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    local = datetime.fromtimestamp(_now(now), tz)
    hm = local.hour * 60 + local.minute

    def minutes(hhmm):
        if hhmm in ("24:00", "24:00:00"):
            return 24 * 60
        parts = str(hhmm).split(":")
        return int(parts[0]) * 60 + int(parts[1])

    a, b = minutes(start), minutes(end)
    if a == b:
        return True
    if a < b:
        return a <= hm < b
    return hm >= a or hm < b


def _grants_path(env):
    return S.secrets_dir(env) / GRANTS_NAME


def last_grant(name, env=None):
    path = _grants_path(env)
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    grants = data.get("grants") if isinstance(data, dict) else None
    if not isinstance(grants, dict):
        return 0
    try:
        return int(grants.get(name) or 0)
    except (TypeError, ValueError):
        return 0


def record_grant(name, env=None, now=None):
    path = _grants_path(env)
    data = {"schema": GRANTS_SCHEMA, "grants": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("grants"), dict):
                data["grants"] = dict(loaded["grants"])
        except (OSError, ValueError):
            pass
    data["grants"][name] = _now(now)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, S.FILE_MODE)
    os.replace(tmp, path)


def _decision_ref(name):
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:22]
    return "di_secret_read_%s" % digest


def line_quick_reply_payload(kind, name, now=None, expires_in=3600):
    """a server 同形:text + quickReply continue/cancel。資料面不含金鑰值。"""
    prompts = {
        TOKEN_SHORT: "Ez2 AIB 算力不足,已暫停讀取金鑰。請稍後或加購後再試。",
        DISABLED: "金鑰讀取已關閉。",
        NEEDS_CONFIRM: "有程式要讀取金鑰 %s。允許這一次?" % name,
        OUTSIDE_WINDOW: "現在不在允許讀取 %s 的時段。" % name,
    }
    text = prompts.get(kind, prompts[NEEDS_CONFIRM])
    expires_at = _now(now) + int(expires_in)
    ref = _decision_ref(name)
    items = []
    for semantic, label, cref in (
            ("continue", "繼續", "ch_continue"),
            ("cancel", "取消", "ch_cancel")):
        data = json.dumps(
            {"opaque_decision_ref": ref, "choice_ref": cref},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        items.append({
            "type": "action",
            "action": {
                "type": "postback",
                "label": label,
                "data": data,
                "displayText": label,
            },
        })
    return {
        "type": "text",
        "text": text,
        "quickReply": {"items": items},
        "expires_at": expires_at,
        "channel": "line",
        "kind": kind,
        "name": name,
    }


def emit_reminder(kind, name, env=None, send=None, now=None):
    policy = load_policy(env)
    channel = policy.get("channel") or DEFAULT_CHANNEL
    payload = line_quick_reply_payload(kind, name, now=now)
    if channel != "line":
        payload["channel"] = channel
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if name in blob and payload.get("text") and name in payload["text"]:
        # 名字可以出現在說明;值不行。呼叫端另驗值。
        pass
    log = S.secrets_dir(env) / REMINDERS_NAME
    rec = {
        "at": _now(now),
        "kind": kind,
        "name": name,
        "channel": channel,
        "opaque_decision_ref": _decision_ref(name),
    }
    if S.secrets_dir(env).is_dir():
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            os.chmod(log, S.FILE_MODE)
        except OSError:
            pass
    if send is not None:
        send(payload)
    return payload


def apply_choice(name, choice_ref, env=None, now=None):
    """LINE 點了繼續才記一筆授權;取消不發金鑰。"""
    if choice_ref not in ("ch_continue", "continue"):
        return False
    record_grant(name, env=env, now=now)
    return True


def assert_read_allowed(name, env=None, now=None, token_check=None, send=None):
    """允許就回 None;否則先提醒再丟 SecretError。永不回金鑰值。"""
    policy = load_policy(env)
    if policy.get("enabled") is not True:
        emit_reminder(DISABLED, name, env=env, send=send, now=now)
        raise S.SecretError(DISABLED, name, "switch off")
    kp = key_policy(name, policy, env)
    if kp.get("enabled") is False:
        emit_reminder(DISABLED, name, env=env, send=send, now=now)
        raise S.SecretError(DISABLED, name, "key off")
    ok = tokens_ok(env) if token_check is None else bool(token_check())
    if not ok:
        emit_reminder(TOKEN_SHORT, name, env=env, send=send, now=now)
        raise S.SecretError(TOKEN_SHORT, name, "interrupt")
    if not _in_window(kp.get("window"), now):
        emit_reminder(OUTSIDE_WINDOW, name, env=env, send=send, now=now)
        raise S.SecretError(OUTSIDE_WINDOW, name)
    every = int(kp.get("confirm_every_seconds") or 0)
    if every > 0:
        granted = last_grant(name, env)
        if granted <= 0 or (_now(now) - granted) >= every:
            emit_reminder(NEEDS_CONFIRM, name, env=env, send=send, now=now)
            raise S.SecretError(NEEDS_CONFIRM, name)
    return None
