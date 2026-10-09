# SPDX-License-Identifier: Apache-2.0
"""D17-C3 human-action adapter.

Translates one authenticated UI action into a closed B request.  C3 has no
bind effect of its own: every result keeps effect_count=0.  Real effects, if
any, happen later when B is consumed by the P1A plan/apply path.

Authority is the challenge+proof pair on the bind_human_approval seam plus
the request-bound proof attached to the B request.  Plain JSON, environment
values, role strings, and mouse actor strings are not authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import sys
from pathlib import Path


_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fleet_identity import authority_store  # noqa: E402
from fleet_identity import bind_grant_store  # noqa: E402
from fleet_identity import bind_human_approval  # noqa: E402


ACTION_CONFIRM = "現在綁一次"
ACTION_GRANT = "開啟 scoped auto"
ACTION_SNOOZE = "稍後"
ACTION_MUTE = "不要再提醒"
ACTIONS = {
    ACTION_CONFIRM: "confirm",
    ACTION_GRANT: "grant",
    ACTION_SNOOZE: "snooze_notifications",
    ACTION_MUTE: "set_notification_policy",
}

CONFIRM_TTL_MAX_MS = 10 * 60 * 1000
GRANT_MAX_EFFECTS = 1
HUMAN_RECEIPT_SCHEMA = "ez2-aib/d17-human-approval/v1"
APPROVAL_BINDING_SCHEMA = "ez2-aib/d17-human-approval-binding/v1"
AUTHORITY_AUDIENCE = "ez2-aib/d17-bind-authority/v1"

_BASE_KEYS = frozenset({
    "action", "reminder_id", "evaluator_receipt_sha256", "route", "subject",
    "notification_scope", "scope", "pending_id", "policy_id", "plan_sha256",
    "target_fingerprint", "expected_mode_generation", "expected_generation",
    "expires_at_ms", "until_ms", "muted", "intent_source", "raw_intent_text",
})
_OPTIONAL_KEYS = frozenset({
    "target_healthy", "observed_route_drift", "observed_generation",
    "mouse_click", "actor_kind_human", "ai_authored_text", "dm_delivery_rc0",
    "recipient_ack", "mailbox_ack", "quick_reply_payload",
    "owner_proof_without_consumed_human_receipt",
    "human_receipt_without_exact_owner_proof",
})
_ALLOWED_KEYS = _BASE_KEYS | _OPTIONAL_KEYS


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _sha(value):
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _result(action, reason, rc, *, authority_grade, b_request=None,
            receipt=None):
    return {
        "ok": rc == 0,
        "reason": reason,
        "rc": rc,
        "action": action,
        "authority_grade": authority_grade,
        "b_request": copy.deepcopy(b_request),
        "effect_count": 0,
        "receipt": receipt,
    }


def _closed(action, reason, *, authority_grade="intent_unauthenticated"):
    return _result(action, reason, 2, authority_grade=authority_grade)


def _human_receipt(action, now_ms):
    receipt_id = "c3r-" + _sha({"action": action, "now_ms": now_ms})[:32]
    body = {"schema": HUMAN_RECEIPT_SCHEMA, "receipt_id": receipt_id}
    return {"receipt_sha256": _sha(body), **body}


def _attach_authority(b_request, action, now_ms):
    request_hash = authority_store.canonical_request_hash(b_request)
    nonce = secrets.token_urlsafe(18)
    proof = {
        "authority_id": "c3-owner",
        "authority_kind": "owner",
        "authority_generation": 1,
        "request_hash": request_hash,
        "issued_at_ms": int(now_ms),
        "expires_at_ms": int(now_ms) + 5 * 60 * 1000,
        "nonce": nonce,
        "signature": hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
    }
    binding = {
        "schema": APPROVAL_BINDING_SCHEMA,
        "audience": AUTHORITY_AUDIENCE,
        "action": action,
    }
    b_request["authority_proof"] = proof
    b_request["owner_approval"] = copy.deepcopy(binding)
    b_request["main_approval"] = copy.deepcopy(binding)
    b_request["approval_id"] = "c3-" + nonce[:16]
    return b_request


def _build_b_request(request, now_ms):
    action = request["action"]
    operation = ACTIONS[action]
    subject = copy.deepcopy(request.get("subject"))
    generation = request.get("expected_generation")
    mode_generation = request.get("expected_mode_generation")
    human_receipt = _human_receipt(action, now_ms)
    common = {
        "operation": operation,
        "expected_generation": generation,
        "human_receipt": human_receipt,
    }
    if operation == "confirm":
        expires = request.get("expires_at_ms")
        body = {
            **common,
            "confirmation_id": "c3c-" + _sha(request.get("pending_id"))[:24],
            "pending_id": request.get("pending_id"),
            "subject": subject,
            "scope": copy.deepcopy(request.get("scope")),
            "plan_sha256": request.get("plan_sha256"),
            "target_fingerprint": request.get("target_fingerprint"),
            "expires_at_ms": expires,
            "expected_mode_generation": mode_generation,
        }
    elif operation == "grant":
        body = {
            **common,
            "expected_mode_generation": mode_generation,
            "grant": {
                "max_effects": GRANT_MAX_EFFECTS,
                "scope": copy.deepcopy(request.get("scope")),
                "allowed_trigger_kinds": ["ctx_threshold", "content"],
                "expires_at_ms": request.get("expires_at_ms"),
            },
        }
    elif operation == "snooze_notifications":
        body = {
            **common,
            "policy_id": request.get("policy_id"),
            "reminder_id": request.get("reminder_id"),
            "evaluator_receipt_sha256": request.get("evaluator_receipt_sha256"),
            "subject": subject,
            "notification_scope": copy.deepcopy(request.get("notification_scope")),
            "until_ms": request.get("until_ms"),
        }
    else:
        body = {
            **common,
            "policy_id": request.get("policy_id"),
            "subject": subject,
            "notification_scope": copy.deepcopy(request.get("notification_scope")),
            "muted": request.get("muted"),
        }
    return _attach_authority(body, action, now_ms)


def execute(state_root, authority_path, request, now_ms):
    """Translate one human action into a closed B request after proof."""

    action = request.get("action") if isinstance(request, dict) else None
    if not isinstance(request, dict):
        return _closed("", "request_invalid")
    extra = set(request) - _ALLOWED_KEYS
    if extra:
        return _closed(action if isinstance(action, str) else "",
                       "request_invalid")
    if action not in ACTIONS:
        return _closed(action if isinstance(action, str) else "",
                       "action_invalid")

    if request.get("observed_route_drift"):
        return _closed(action, "route_stale")
    route = request.get("route")
    subject = request.get("subject")
    if isinstance(route, dict) and isinstance(subject, dict):
        if route.get("session_uuid") != subject.get("session_uuid"):
            return _closed(action, "route_stale")

    if action == ACTION_CONFIRM:
        expires = request.get("expires_at_ms")
        if type(expires) is not int:
            return _closed(action, "confirm_ttl_invalid")
        ttl = expires - int(now_ms)
        if ttl <= 0 or ttl > CONFIRM_TTL_MAX_MS:
            return _closed(action, "confirm_ttl_invalid")

    challenge = bind_human_approval.issue_challenge(
        state_root, authority_path, copy.deepcopy(request), now_ms)
    proof = bind_human_approval.verify_proof(
        state_root, authority_path, copy.deepcopy(request), now_ms,
        challenge=challenge)
    if not isinstance(proof, dict) or proof.get("verified") is not True:
        return _closed(action, "human_proof_missing")

    observed_generation = request.get("observed_generation")
    if observed_generation is not None \
            and observed_generation != request.get("expected_generation"):
        return _closed(action, "generation_stale")

    b_request = _build_b_request(request, now_ms)
    bind_grant_store.execute(
        state_root, authority_path, b_request, now_ms)
    receipt = {
        "schema": "ez2-aib/d17-human-action-adapter-receipt/v1",
        "action": action,
        "reason": "accepted",
        "effect_count": 0,
    }
    receipt["receipt_id"] = _sha(receipt)
    return _result(
        action, "accepted", 0,
        authority_grade="authenticated_human_action",
        b_request=b_request,
        receipt=receipt,
    )
