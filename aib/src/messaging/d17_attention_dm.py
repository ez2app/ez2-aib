# SPDX-License-Identifier: Apache-2.0
"""D17-C2a outbound AIB-DM adapter.

Consumes a C1 reminder and delivery claim, re-resolves cabin/fleet/seat/session
immediately before the injected transport, maps each transport item receipt
independently, and finishes C1 durability before returning.  Inbound content
activation stays unavailable.

Oracle red lines (same as C3; 2026-09-02 captain):

* This adapter does not touch the auto-bind switch and does not carry a second
  copy of that switch. Binding effects stay in the bind-plan / bind-apply /
  binding-store stages; this file never imports or calls them.
* Plain JSON, process environment, and role strings are not authority. The
  only capability is the C1 reminder row (origin, internal capability digest,
  channel, scope, route). A caller capability hint cannot replace that row.
* ``effect_count`` is always 0. rc=0 is transport delivery only, never read
  and never owner approval.

Schema constants are public so the second-party arm can import them when the
C2a product is paired in.  ``REMINDER_SCHEMA`` is the unpublished C1 row
schema (C1 currently exposes request/result/receipt/BN only); reopen when C1
publishes a row constant.
"""

from __future__ import annotations

import hashlib
import json

REQUEST_SCHEMA = "ez2-aib/d17-aib-dm-adapter-request/v1"
RESULT_SCHEMA = "ez2-aib/d17-aib-dm-adapter-receipt/v1"
REMINDER_SCHEMA = "ez2-aib/d17-reminder-outbox-row/v1"
TRANSPORT_SCHEMA = "ez2-aib/d17-aib-dm-transport-item-receipt/v1"
INTERNAL_CAPABILITY_SCHEMA = "ez2-aib/d17-internal-reminder-capability/v1"

SCOPE_KEYS = (
    "cabin_id", "fleet_id", "fleet_generation", "fleet_member_id",
    "lease_id", "lease_generation", "project_id", "project_generation",
    "seat_id", "session_uuid", "seat_record_sha256",
)
MATCH_CABIN_FLEET_KEYS = ("cabin_id", "fleet_id")
MATCH_SESSION_KEYS = ("session_uuid",)
MATCH_OTHER_SCOPE_KEYS = (
    "fleet_generation", "fleet_member_id", "lease_id", "lease_generation",
    "project_id", "project_generation", "seat_id", "seat_record_sha256",
)
ROUTE_KEYS = (
    "socket", "window_id", "pid", "route_generation",
    "route_attestation_sha256",
)
ITEM_KEYS = {
    "schema", "reminder_id", "delivery_id", "route_attestation_sha256",
    "payload_sha256", "payload_length", "rc_contract", "stage",
    "append_count", "verified_delivery", "verified_zero_append",
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _exact_int(value):
    return type(value) is int


def _exact_str(value):
    return type(value) is str and bool(value)


def _capability_sha(row):
    return _digest({
        "schema": INTERNAL_CAPABILITY_SCHEMA,
        "reminder_id": row.get("reminder_id"),
        "evaluator_receipt_sha256": row.get("evaluator_receipt_sha256"),
        "channel": "aib_dm",
    })


def _valid_scope(scope):
    if not isinstance(scope, dict) or set(scope) != set(SCOPE_KEYS):
        return False
    for key in SCOPE_KEYS:
        value = scope.get(key)
        if key.endswith("_generation"):
            if not _exact_int(value) or value < 0:
                return False
        elif not _exact_str(value):
            return False
    return True


def _valid_route(route):
    if not isinstance(route, dict) or set(route) != set(ROUTE_KEYS):
        return False
    for key in ROUTE_KEYS:
        value = route.get(key)
        if key in {"pid", "route_generation"}:
            if not _exact_int(value) or value < 0:
                return False
        elif not _exact_str(value):
            return False
    return True


def _valid_reminder(row):
    if not isinstance(row, dict):
        return False
    required = {
        "schema", "origin", "reminder_id", "evaluator_receipt_sha256",
        "internal_capability_sha256", "channel", "state", "template_id",
        "template_fields", "scope", "route", "target_fingerprint",
        "mode_generation", "delivery_id", "attempt_generation",
        "lease_token", "outcome",
    }
    if set(row) != required:
        return False
    if row.get("schema") != REMINDER_SCHEMA or row.get("origin") != "d17_c1_outbox":
        return False
    if row.get("channel") != "aib_dm" or row.get("state") not in {
            "ready", "retryable_zero_delivery", "transport_delivered",
            "delivery_unknown"}:
        return False
    if not _exact_str(row.get("reminder_id")):
        return False
    if row.get("internal_capability_sha256") != _capability_sha(row):
        return False
    if not _valid_scope(row.get("scope")) or not _valid_route(row.get("route")):
        return False
    if not _exact_int(row.get("mode_generation")):
        return False
    return isinstance(row.get("template_fields"), dict)


def _route_matches(expected_scope, expected_route, actual):
    if not isinstance(actual, dict) or set(actual) != {"scope", "route"}:
        return False
    current_scope = actual.get("scope")
    current_route = actual.get("route")
    if not _valid_scope(current_scope) or not _valid_route(current_route):
        return False
    match_scope_keys = (MATCH_CABIN_FLEET_KEYS + MATCH_SESSION_KEYS
                        + MATCH_OTHER_SCOPE_KEYS)
    return all(current_scope[key] == expected_scope[key] for key in match_scope_keys) \
        and all(current_route[key] == expected_route[key] for key in ROUTE_KEYS)


def _item(reminder_id, delivery_id, *, rc, status, reason,
          transport_rc, retry_allowed, fallback_allowed,
          payload_sha256, payload_length):
    return {
        "reminder_id": reminder_id,
        "delivery_id": delivery_id,
        "rc": rc,
        "status": status,
        "reason": reason,
        "transport_rc": transport_rc,
        "retry_allowed": retry_allowed,
        "fallback_allowed": fallback_allowed,
        "acknowledged": False,
        "approval": False,
        "effect_count": 0,
        "payload_sha256": payload_sha256,
        "payload_length": payload_length,
        "durable": True,
    }


def _top(operation, outputs, *, status=None, reason=None):
    rc = 0 if all(item["rc"] == 0 for item in outputs) else 2
    if status is None:
        status = "complete" if rc == 0 else "incomplete"
    if reason is None:
        reason = "delivery_complete" if rc == 0 else "delivery_incomplete"
    body = {
        "schema": RESULT_SCHEMA,
        "operation": operation,
        "rc": rc,
        "status": status,
        "reason": reason,
        "effect_count": 0,
        "reminder_count": len(outputs),
        "results": outputs,
    }
    body["receipt_sha256"] = _digest(body)
    return body


def _closed(operation, reason):
    body = {
        "schema": RESULT_SCHEMA,
        "operation": operation,
        "rc": 2,
        "status": "capability_unavailable",
        "reason": reason,
        "effect_count": 0,
        "reminder_count": 0,
        "results": [],
    }
    body["receipt_sha256"] = _digest(body)
    return body


def _interpret(prepared, receipt):
    claim = prepared["claim"]
    reminder_id = prepared["reminder_id"]
    delivery_id = claim["delivery_id"]
    payload_sha = prepared["payload_sha256"]
    payload_length = prepared["payload_length"]
    unknown = lambda why, trc=None: _item(
        reminder_id, delivery_id, rc=2, status="delivery_unknown",
        reason=why, transport_rc=trc, retry_allowed=False,
        fallback_allowed=False, payload_sha256=payload_sha,
        payload_length=payload_length)
    if not isinstance(receipt, dict) or set(receipt) != ITEM_KEYS:
        return unknown("transport_receipt_malformed")
    transport_rc = receipt.get("rc_contract")
    if not _exact_int(transport_rc) or transport_rc not in {0, 2, 3, 4}:
        return unknown("transport_receipt_malformed")
    exact = (
        receipt.get("schema") == TRANSPORT_SCHEMA
        and receipt.get("reminder_id") == reminder_id
        and receipt.get("delivery_id") == delivery_id
        and receipt.get("route_attestation_sha256")
            == prepared["route"]["route_attestation_sha256"]
        and receipt.get("payload_sha256") == payload_sha
        and receipt.get("payload_length") == payload_length
        and _exact_int(receipt.get("payload_length"))
    )
    if not exact or not _exact_int(receipt.get("append_count")):
        return unknown("transport_receipt_contradiction", transport_rc)
    if transport_rc == 0:
        if (receipt.get("stage") == "delivered"
                and receipt.get("append_count") == 1
                and receipt.get("verified_delivery") is True
                and receipt.get("verified_zero_append") is False):
            return _item(
                reminder_id, delivery_id, rc=0,
                status="transport_delivered", reason="transport_delivered",
                transport_rc=0, retry_allowed=False, fallback_allowed=False,
                payload_sha256=payload_sha, payload_length=payload_length)
        return unknown("transport_receipt_contradiction", 0)
    if transport_rc == 2:
        if (receipt.get("append_count") == 0
                and receipt.get("verified_zero_append") is True
                and receipt.get("verified_delivery") is False):
            return _item(
                reminder_id, delivery_id, rc=2,
                status="retryable_zero_delivery", reason="verified_zero_delivery",
                transport_rc=2, retry_allowed=True, fallback_allowed=True,
                payload_sha256=payload_sha, payload_length=payload_length)
        return unknown("transport_receipt_contradiction", 2)
    return unknown("transport_outcome_unknown", transport_rc)


def _finish(outbox, prepared, outcome, now_ms, *, reconciled=False):
    receipt = outbox.finish_delivery(
        reminder_id=prepared["reminder_id"],
        delivery_id=prepared["claim"]["delivery_id"],
        lease_token=prepared["claim"]["lease_token"],
        outcome=outcome,
        now_ms=now_ms,
        reconciled=reconciled,
    )
    if not isinstance(receipt, dict) or receipt.get("durable") is not True:
        raise RuntimeError("outbox_finish_not_durable")


def _validate_request(request):
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        return None
    operation = request.get("operation")
    if operation == "observe_inbound":
        if set(request) != {"schema", "operation", "source_event_id_sha256", "body"}:
            return None
        return operation
    if operation not in {"deliver", "reconcile"}:
        return None
    if set(request) != {
            "schema", "operation", "reminder_ids", "worker_id", "now_ms",
            "capability_hint"}:
        return None
    ids = request.get("reminder_ids")
    if (not isinstance(ids, list) or not ids
            or any(not _exact_str(value) for value in ids)
            or len(ids) != len(set(ids))):
        return None
    if operation == "reconcile" and len(ids) != 1:
        return None
    if not _exact_str(request.get("worker_id")) or not _exact_int(request.get("now_ms")):
        return None
    return operation


def execute(request, *, outbox, route_resolver, transport, renderer):
    operation = _validate_request(request)
    if operation is None:
        return _closed("", "request_invalid")
    if operation == "observe_inbound":
        return _closed(operation, "inbound_content_activation_unavailable")

    now_ms = request["now_ms"]
    outputs = []
    prepared = []
    for reminder_id in request["reminder_ids"]:
        row = outbox.read_reminder(reminder_id=reminder_id)
        if not _valid_reminder(row):
            outputs.append(_item(
                reminder_id, "", rc=2, status="capability_unavailable",
                reason="internal_reminder_capability_unavailable",
                transport_rc=None, retry_allowed=False, fallback_allowed=False,
                payload_sha256=None, payload_length=0))
            continue
        if operation == "reconcile":
            outcome = row.get("outcome")
            if row.get("state") != "delivery_unknown" or not isinstance(outcome, dict):
                outputs.append(_item(
                    reminder_id, row["delivery_id"], rc=2,
                    status="delivery_unknown", reason="reconcile_unavailable",
                    transport_rc=None, retry_allowed=False, fallback_allowed=False,
                    payload_sha256=None, payload_length=0))
                continue
            actual = route_resolver(scope=row["scope"])
            if not _route_matches(row["scope"], row["route"], actual):
                outputs.append(outcome)
                continue
            receipt = transport.query_delivery(
                delivery_id=row["delivery_id"], reminder_id=reminder_id,
                route=row["route"])
            pseudo = {
                "reminder_id": reminder_id,
                "claim": {
                    "delivery_id": row["delivery_id"],
                    "lease_token": row["lease_token"],
                },
                "route": row["route"],
                "payload_sha256": outcome.get("payload_sha256"),
                "payload_length": outcome.get("payload_length"),
            }
            reconciled = _interpret(pseudo, receipt)
            if reconciled["status"] == "transport_delivered":
                _finish(outbox, pseudo, reconciled, now_ms, reconciled=True)
                outputs.append(reconciled)
            else:
                outputs.append(outcome)
            continue

        claim = outbox.claim_delivery(
            reminder_id=reminder_id, channel="aib_dm",
            worker_id=request["worker_id"], now_ms=now_ms,
            retry_unknown=False)
        if not isinstance(claim, dict):
            outputs.append(_item(
                reminder_id, "", rc=2, status="capability_unavailable",
                reason="delivery_claim_unavailable", transport_rc=None,
                retry_allowed=False, fallback_allowed=False,
                payload_sha256=None, payload_length=0))
            continue
        if claim.get("kind") == "existing":
            outputs.append(claim["outcome"])
            continue
        if claim.get("kind") != "claimed":
            outputs.append(_item(
                reminder_id, "", rc=2, status="capability_unavailable",
                reason="delivery_claim_unavailable", transport_rc=None,
                retry_allowed=False, fallback_allowed=False,
                payload_sha256=None, payload_length=0))
            continue
        claimed = claim.get("reminder")
        if not isinstance(claimed, dict):
            outputs.append(_item(
                reminder_id, claim.get("delivery_id", ""), rc=2,
                status="delivery_unknown", reason="delivery_claim_invalid",
                transport_rc=None, retry_allowed=False, fallback_allowed=False,
                payload_sha256=None, payload_length=0))
            continue
        actual = route_resolver(scope=claimed["scope"])
        if not _route_matches(claimed["scope"], claimed["route"], actual):
            outcome = _item(
                reminder_id, claim["delivery_id"], rc=2,
                status="route_stale", reason="route_stale", transport_rc=None,
                retry_allowed=False, fallback_allowed=False,
                payload_sha256=None, payload_length=0)
            entry = {"reminder_id": reminder_id, "claim": claim}
            _finish(outbox, entry, outcome, now_ms)
            outputs.append(outcome)
            continue
        body = renderer(
            template_id=claimed["template_id"], fields=claimed["template_fields"])
        if not isinstance(body, str):
            outcome = _item(
                reminder_id, claim["delivery_id"], rc=2,
                status="delivery_unknown", reason="render_failed",
                transport_rc=None, retry_allowed=False, fallback_allowed=False,
                payload_sha256=None, payload_length=0)
            entry = {"reminder_id": reminder_id, "claim": claim}
            _finish(outbox, entry, outcome, now_ms)
            outputs.append(outcome)
            continue
        blob = body.encode("utf-8")
        entry = {
            "reminder_id": reminder_id,
            "claim": claim,
            "route": claimed["route"],
            "scope": claimed["scope"],
            "body": body,
            "payload_sha256": hashlib.sha256(blob).hexdigest(),
            "payload_length": len(blob),
        }
        prepared.append(entry)

    if prepared:
        transport_items = [{
            "reminder_id": entry["reminder_id"],
            "delivery_id": entry["claim"]["delivery_id"],
            "scope": entry["scope"],
            "route": entry["route"],
            "body": entry["body"],
            "payload_sha256": entry["payload_sha256"],
            "payload_length": entry["payload_length"],
            "source_kind": "d17_reminder_delivery",
            "suppress_content_activation": True,
        } for entry in prepared]
        try:
            transport_result = transport.send_batch(items=transport_items)
        except Exception:
            transport_result = None
        if isinstance(transport_result, dict):
            receipts = transport_result.get("receipts")
        else:
            receipts = None
        if not isinstance(receipts, list):
            receipts = []
        receipt_by_id = {
            item.get("delivery_id"): item for item in receipts
            if isinstance(item, dict)
        }
        for entry in prepared:
            receipt = receipt_by_id.get(entry["claim"]["delivery_id"])
            outcome = _interpret(entry, receipt)
            _finish(outbox, entry, outcome, now_ms)
            outputs.append(outcome)

    return _top(operation, outputs)
