#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""License ↔ machine binding (all fields below are inside the signed license, so a customer cannot edit them).

Three kinds of license:
  unbound      no "devices", no "activation_url"      → works on any machine (the original behaviour)
  offline      "devices": [device ids]                → works only on those machines; the issuer re-signs to change them
  online       "activation_url" (+ "max_devices": n)  → each machine activates once; the server hands back an
               activation token signed by an activation key (keys.TRUSTED_ACTIVATION_KEYS) for this license and
               this device, valid until its "expires_at"; activating again renews it. The server refuses a new
               machine once n are active; "deactivate" frees a slot (e.g. before switching computers).

check(license_obj, env) → (ok, reason). Fails closed: unreadable machine id, missing or bad token ⇒ not ok.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from . import device, keys, signing
from .store import resolve_home

ACTIVATION_FILE = "activation.json"


def activation_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return resolve_home(env) / ACTIVATION_FILE


def _parse_time(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def token_ok(token: Any, license_id: str, did: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    if not isinstance(token, dict):
        return False, "not-activated"
    if signing.verify_object(token, key_list=keys.TRUSTED_ACTIVATION_KEYS, draft=False) != signing.OK:
        return False, "activation-invalid"
    if token.get("license_id") != license_id or token.get("device_id") != did:
        return False, "activation-other-device"
    exp = _parse_time(token.get("expires_at"))
    if exp is None or (now or datetime.now(timezone.utc)) > exp:
        return False, "activation-expired"
    return True, "activated"


def check(obj: Mapping[str, Any], env: Optional[Mapping[str, str]] = None, raw_machine: Optional[str] = None,
          now: Optional[datetime] = None) -> Tuple[bool, str]:
    devices = obj.get("devices")
    url = obj.get("activation_url")
    if devices is None and not url:
        return True, "unbound"
    lid = obj.get("license_id")
    if not isinstance(lid, str) or not lid:
        return False, "binding-without-license-id"
    did = device.device_id(lid, raw_machine)
    if did is None:
        return False, "no-machine-id"
    if devices is not None:
        if isinstance(devices, list) and did in devices:
            return True, "bound"
        return False, "device-not-bound"
    try:
        token = json.loads(activation_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "not-activated"
    return token_ok(token, lid, did, now)
