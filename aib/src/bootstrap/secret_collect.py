#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""缺金鑰時用社群發填入需求;用戶由 LINE 回傳值;加密寫回對應檔。

發送通道:LINE / Telegram / WhatsApp / Facebook Messenger,預設 LINE。
填入通道固定 LINE(the maintainer:用戶由 LINE 填入)。真發送可注入,測試不碰活線路。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import aib_secrets as S
import secret_crypto as C

POLICY_SCHEMA = "aib-secret-collect-policy/v1"
PENDING_SCHEMA = "aib-secret-collect-pending/v1"
POLICY_NAME = "collect-policy.json"
PENDING_NAME = "collect-pending.json"
REQUESTS_NAME = "collect-requests.jsonl"

NEEDS_FILL = "needs_fill"
CHANNELS = ("line", "telegram", "whatsapp", "messenger")
CHANNEL_ALIASES = {"fb": "messenger", "facebook": "messenger"}
DEFAULT_CHANNEL = "line"
FILL_CHANNEL = "line"

DEFAULT_POLICY = {
    "schema": POLICY_SCHEMA,
    "enabled": True,
    "channel": DEFAULT_CHANNEL,
    "fill_channel": FILL_CHANNEL,
}


def _now(now):
    return int(time.time() if now is None else now)


def _policy_path(env):
    return S.secrets_dir(env) / POLICY_NAME


def _pending_path(env):
    return S.secrets_dir(env) / PENDING_NAME


def normalize_channel(value):
    text = (value or "").strip().lower()
    text = CHANNEL_ALIASES.get(text, text)
    return text if text in CHANNELS else DEFAULT_CHANNEL


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
    if isinstance(data.get("enabled"), bool):
        out["enabled"] = data["enabled"]
    out["channel"] = normalize_channel(data.get("channel"))
    out["fill_channel"] = FILL_CHANNEL
    return out


def write_policy(channel=None, enabled=None, env=None):
    policy = load_policy(env)
    if channel is not None:
        policy["channel"] = normalize_channel(channel)
    if enabled is not None:
        policy["enabled"] = bool(enabled)
    policy["schema"] = POLICY_SCHEMA
    policy["fill_channel"] = FILL_CHANNEL
    path = _policy_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, S.FILE_MODE)
    os.replace(tmp, path)
    return policy


def _purpose(name):
    for _rel, _plat, _cat, _cmt, keys in S.LAYOUT:
        for key, purpose, _cons, _feat, _fb in keys:
            if key == name:
                return purpose
    if name.startswith("LINE_") or "LINE_" in name:
        return "LINE"
    return name


def _fill_ref(name):
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:22]
    return "di_secret_fill_%s" % digest


def collect_payload(name, env=None, now=None):
    policy = load_policy(env)
    channel = policy.get("channel") or DEFAULT_CHANNEL
    purpose = _purpose(name)
    text = (
        "Ez2 AIB 需要填入金鑰 %s(%s)。請用 LINE 把值傳回來,不要傳給別人。"
        % (name, purpose)
    )
    ref = _fill_ref(name)
    items = [{
        "type": "action",
        "action": {
            "type": "postback",
            "label": "稍後再說",
            "data": json.dumps(
                {"opaque_decision_ref": ref, "choice_ref": "ch_cancel"},
                ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            "displayText": "稍後再說",
        },
    }]
    payload = {
        "type": "text",
        "text": text,
        "quickReply": {"items": items} if channel == "line" else None,
        "channel": channel,
        "fill_channel": FILL_CHANNEL,
        "kind": NEEDS_FILL,
        "name": name,
        "opaque_decision_ref": ref,
        "at": _now(now),
    }
    if payload["quickReply"] is None:
        del payload["quickReply"]
    return payload


def _load_pending(env):
    path = _pending_path(env)
    if not path.is_file():
        return {"schema": PENDING_SCHEMA, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": PENDING_SCHEMA, "items": []}
    if not isinstance(data, dict) or data.get("schema") != PENDING_SCHEMA:
        return {"schema": PENDING_SCHEMA, "items": []}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    clean = []
    for row in items:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            clean.append(row)
    return {"schema": PENDING_SCHEMA, "items": clean}


def _save_pending(data, env):
    path = _pending_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, S.FILE_MODE)
    os.replace(tmp, path)


def request_fill(name, env=None, send=None, now=None):
    """發填入需求。回傳 payload;永不帶金鑰值。"""
    policy = load_policy(env)
    if policy.get("enabled") is not True:
        return None
    if S.file_for_name(name) is None:
        return None
    payload = collect_payload(name, env=env, now=now)
    pending = _load_pending(env)
    items = [row for row in pending["items"] if row.get("name") != name]
    items.append({
        "name": name,
        "ref": payload["opaque_decision_ref"],
        "channel": payload["channel"],
        "at": payload["at"],
    })
    pending["items"] = items
    d = S.secrets_dir(env)
    if d.is_dir():
        _save_pending(pending, env)
        log = d / REQUESTS_NAME
        rec = {
            "at": payload["at"],
            "kind": NEEDS_FILL,
            "name": name,
            "channel": payload["channel"],
            "fill_channel": FILL_CHANNEL,
            "opaque_decision_ref": payload["opaque_decision_ref"],
        }
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            os.chmod(log, S.FILE_MODE)
        except OSError:
            pass
    if send is not None:
        send(payload)
    return payload


def cancel_fill(name, env=None):
    pending = _load_pending(env)
    pending["items"] = [row for row in pending["items"]
                        if row.get("name") != name]
    if S.secrets_dir(env).is_dir():
        _save_pending(pending, env)
    return True


def apply_choice(name, choice_ref, env=None):
    if choice_ref in ("ch_cancel", "cancel"):
        cancel_fill(name, env=env)
        return False
    return False


def _set_encrypted_line(path, name, envelope):
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    replaced = False
    out = []
    for ln in lines:
        if ln.startswith(name + "=") and not replaced:
            out.append("%s=%s" % (name, envelope))
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append("%s=%s" % (name, envelope))
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, S.FILE_MODE)
    try:
        os.write(fd, body.encode("utf-8"))
        os.fchmod(fd, S.FILE_MODE)
    finally:
        os.close(fd)


def put_secret(name, plaintext, env=None):
    """加密寫入對應檔。成功回 True。明文不得進 log。"""
    if not S.valid_name(name):
        raise S.SecretError("bad_name", repr(name)[:40], "bad name")
    rel = S.file_for_name(name)
    if rel is None:
        raise S.SecretError(S.MISSING_FILE, name, "unknown name")
    if name in S.SE_ONLY_NAMES:      # 母鑰只進 Secure Enclave;檔案(含 LINE 補填)一律拒(@abb89 審 #222)
        raise S.SecretError("se_only", name, "Secure Enclave only")
    if S.is_remote_only(name):  # 只在伺服器上用:本機不存(aib secret ask … --remote-only)
        raise S.SecretError("remote_only", name, "server only")
    if not isinstance(plaintext, str) or not plaintext.strip():
        raise S.SecretError(S.MISSING_LINE, name, "empty fill")
    d = S.secrets_dir(env)
    S._refuse_vault(d)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), S.DIR_MODE)
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), S.DIR_MODE)
    envelope = C.encrypt_value(plaintext.strip(), name, d)
    _set_encrypted_line(path, name, envelope)
    cancel_fill(name, env=env)
    return True


def apply_line_text(text, env=None):
    """LINE 回傳的下一則文字填最舊一筆待填。回填入的 NAME 或 None。"""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if not value:
        return None
    pending = _load_pending(env)
    if not pending["items"]:
        return None
    row = pending["items"][0]
    name = row.get("name")
    if not name:
        return None
    put_secret(name, value, env=env)
    return name
