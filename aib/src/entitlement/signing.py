#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""License signatures: what is signed, and how a signature is checked against the trusted keys.

Two license shapes exist in aib, both use the same keys:
  1. license.json (entitlement tier):  {"tier": ..., ..., "signature": "ed25519:<base64 of 64 bytes>"}
     The signed bytes are the JSON of every other field, keys sorted, no spaces, UTF-8.
  2. EZ2_LICENSE (plan, basic/advanced):  a compact JWS  <b64url header>.<b64url payload>.<b64url signature>
     with header {"alg":"EdDSA"}; the signed bytes are "<header>.<payload>" (RFC 7515 / RFC 8037).

verify_* return "ok" | "invalid" | "unavailable" and never raise. Nothing here prints or logs license content.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Mapping, Optional

from . import ed25519, keys

OK, INVALID, UNAVAILABLE = "ok", "invalid", "unavailable"
PREFIX = "ed25519:"


def trusted_keys(key_list: Optional[tuple] = None) -> list:
    out = []
    for k in (keys.TRUSTED_PUBLIC_KEYS if key_list is None else key_list):
        if isinstance(k, str) and k.startswith(PREFIX):
            try:
                raw = bytes.fromhex(k[len(PREFIX):])
            except ValueError:
                continue
            if len(raw) == 32:
                out.append(raw)
    return out


def canonical(obj: Mapping[str, Any]) -> bytes:
    """The bytes a license.json signature covers: every field except "signature"."""
    body = {k: v for k, v in obj.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64any(text: str) -> bytes:
    t = text.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(t + "=" * (-len(t) % 4), validate=True)


def _any_key(msg: bytes, sig: bytes, key_list) -> bool:
    return any(ed25519.verify(k, msg, sig) for k in trusted_keys(key_list))


def verify_object(obj: Mapping[str, Any], key_list: Optional[tuple] = None, draft: Optional[bool] = None) -> str:
    sig = obj.get("signature") if isinstance(obj, Mapping) else None
    if sig is None:
        return UNAVAILABLE
    if not isinstance(sig, str):
        return INVALID
    if not sig.strip():
        return UNAVAILABLE
    if sig.startswith(PREFIX):
        try:
            raw = _b64any(sig[len(PREFIX):])
        except (ValueError, TypeError):
            return INVALID
        return OK if _any_key(canonical(obj), raw, key_list) else INVALID
    # 舊草稿的簽章(任意字串):過渡期照舊放行,過渡結束(DRAFT_ACCEPT_ANY = False)就不認
    return OK if (keys.DRAFT_ACCEPT_ANY if draft is None else draft) else INVALID


def verify_jws(blob: str, key_list: Optional[tuple] = None, draft: Optional[bool] = None) -> str:
    """EZ2_LICENSE 那種三段式。過渡期:不是 EdDSA 簽的照舊放行(以前完全不驗);過渡結束就一律要真簽章。"""
    if not isinstance(blob, str) or not blob.strip():
        return UNAVAILABLE
    parts = blob.strip().split(".")
    real = False
    if len(parts) == 3:
        try:
            header = json.loads(_b64any(parts[0]).decode("utf-8"))
            real = isinstance(header, dict) and header.get("alg") == "EdDSA"
        except (ValueError, TypeError, UnicodeDecodeError):
            real = False
    if real:
        try:
            sig = _b64any(parts[2])
        except (ValueError, TypeError):
            return INVALID
        return OK if _any_key(("%s.%s" % (parts[0], parts[1])).encode("ascii", "replace"), sig, key_list) else INVALID
    return OK if (keys.DRAFT_ACCEPT_ANY if draft is None else draft) else INVALID


def enforced() -> bool:
    """真的擋得住自己寫的授權嗎:過渡結束、而且至少有一把信任的公鑰。"""
    return (not keys.DRAFT_ACCEPT_ANY) and bool(trusted_keys())
