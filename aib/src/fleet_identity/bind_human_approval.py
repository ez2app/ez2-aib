# SPDX-License-Identifier: Apache-2.0
"""C3 challenge / proof seam.

Issues and verifies an authenticated human-action challenge.  A proof is
accepted only when all of these hold:

- challenge_id matches a challenge this module issued
- the challenge has not expired
- the challenge has not already been consumed (one-time)
- the proof is bound to this exact request (hash of the request without
  raw intent text)

UI gestures, role strings, environment values, and actor labels are not
authority.  This module does not mint owner HMAC keys and does not apply
bindings.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from pathlib import Path


KIND = "main_native_person_device"
CHALLENGE_TTL_MS = 5 * 60 * 1000
_STORE_DIR = "d17"
_STORE_NAME = "c3-challenges.json"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _sha(value):
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def request_binding_hash(request):
    """Bind a proof to one request without persisting raw intent text."""
    if not isinstance(request, dict):
        return None
    body = {key: value for key, value in request.items()
            if key != "raw_intent_text"}
    try:
        return _sha(body)
    except (TypeError, ValueError):
        return None


def _store_path(state_root):
    return Path(state_root) / _STORE_DIR / _STORE_NAME


def _load_store(state_root):
    path = _store_path(state_root)
    try:
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return {"challenges": {}}
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("challenges"), dict):
        return None
    return doc


def _save_store(state_root, doc):
    directory = Path(state_root) / _STORE_DIR
    directory.mkdir(mode=0o700, exist_ok=True)
    path = _store_path(state_root)
    payload = _canonical(doc) + b"\n"
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    try:
        os.chmod(str(directory), 0o700)
        os.chmod(str(path), 0o600)
        if hasattr(os, "geteuid"):
            info = os.stat(str(directory))
            if stat.S_IMODE(info.st_mode) != 0o700:
                pass
    except OSError:
        pass


def _rejected(reason):
    return {"verified": False, "reason": reason}


def issue_challenge(state_root, authority_path, request, now_ms, **_kwargs):
    binding = request_binding_hash(request)
    if binding is None or type(now_ms) is not int:
        return {
            "challenge_id": None,
            "kind": KIND,
            "reason": "challenge_request_invalid",
        }
    challenge_id = "c3c-" + secrets.token_urlsafe(18)
    expires_at_ms = int(now_ms) + CHALLENGE_TTL_MS
    doc = _load_store(state_root)
    if doc is None:
        return {
            "challenge_id": None,
            "kind": KIND,
            "reason": "challenge_store_unreadable",
        }
    doc["challenges"][challenge_id] = {
        "challenge_id": challenge_id,
        "request_hash": binding,
        "expires_at_ms": expires_at_ms,
        "consumed": False,
    }
    _save_store(state_root, doc)
    return {
        "challenge_id": challenge_id,
        "kind": KIND,
        "expires_at_ms": expires_at_ms,
        "request_hash": binding,
    }


def verify_proof(state_root, authority_path, request, now_ms,
                 challenge=None, proof=None, **_kwargs):
    if type(now_ms) is not int:
        return _rejected("proof_now_invalid")
    binding = request_binding_hash(request)
    if binding is None:
        return _rejected("proof_request_invalid")

    challenge_id = None
    if isinstance(challenge, dict):
        challenge_id = challenge.get("challenge_id")
    if challenge_id is None and isinstance(proof, dict):
        challenge_id = proof.get("challenge_id")
    if not isinstance(challenge_id, str) or not challenge_id:
        return _rejected("challenge_id_missing")

    doc = _load_store(state_root)
    if doc is None:
        return _rejected("challenge_store_unreadable")
    row = doc["challenges"].get(challenge_id)
    if not isinstance(row, dict):
        return _rejected("challenge_id_mismatch")
    if row.get("consumed") is True:
        return _rejected("challenge_already_consumed")
    expires = row.get("expires_at_ms")
    if type(expires) is not int or int(now_ms) >= expires:
        return _rejected("challenge_expired")
    if row.get("request_hash") != binding:
        return _rejected("proof_request_mismatch")

    row["consumed"] = True
    doc["challenges"][challenge_id] = row
    _save_store(state_root, doc)
    return {
        "proof_id": "c3p-" + _sha(challenge_id)[:24],
        "verified": True,
        "challenge_id": challenge_id,
    }
