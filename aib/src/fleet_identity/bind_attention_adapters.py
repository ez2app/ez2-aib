# SPDX-License-Identifier: Apache-2.0
"""Resolve one D17 source to a canonical evaluator request.

This C0 boundary is deliberately stateless.  It resolves public identity,
resolver-private cwd/mode state, and the shared bind-target, invokes an
injected evaluator, then proves the durable A row before returning a
content-free public result.  Reminder policy and binding effects belong to
other stages and are not implemented here.

V2 splits caller-public identity from resolver-private cwd/mode/target.
The caller may not supply those private fields; C0 must refuse them before
it calls A.  ``verify_result`` reruns the trusted resolvers and the durable
verifier and must not re-evaluate A.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re

try:
    from .bind_attention import (
        DURABLE_QUERY_KEYS,
        DURABLE_QUERY_SCHEMA,
        MODES,
        RECEIPT_SCHEMA as EVALUATOR_RECEIPT_SCHEMA,
    )
except ImportError:  # loaded as a loose file, not a package member
    from bind_attention import (
        DURABLE_QUERY_KEYS,
        DURABLE_QUERY_SCHEMA,
        MODES,
        RECEIPT_SCHEMA as EVALUATOR_RECEIPT_SCHEMA,
    )


REQUEST_SCHEMA = "ez2-aib/d17-adapter-request/v2"
RESULT_SCHEMA = "ez2-aib/d17-adapter-result/v2"
RECEIPT_SCHEMA = "ez2-aib/d17-adapter-resolver-receipt/v2"
VERIFIED_SCHEMA = "ez2-aib/d17-adapter-verified-result/v2"
RESOLVED_SCHEMA = "ez2-aib/d17-resolved-context/v2"
TARGET_SCHEMA = "ez2-aib/d17-bind-target/v1"

_OPERATIONS = {
    "resolve_capability",
    "evaluate_ctx_source",
    "evaluate_content_source",
    "status",
}
_CONTENT_SOURCES = {
    "user_prompt_submit",
    "aib_dm_delivery",
    "work_item",
}
_SOURCE_KINDS = {"ctx_observation", *_CONTENT_SOURCES}
_PUBLIC_IDENTITY_KEYS = {
    "cabin_id",
    "fleet_id",
    "fleet_generation",
    "fleet_member_id",
    "lease_id",
    "lease_generation",
    "project_id",
    "project_generation",
    "principal_id",
    "seat_id",
    "session_uuid",
    "provider",
    "role",
    "seat_record_sha256",
    "socket",
    "window_id",
    "pid",
    "plan_sha256",
}
_PRIVATE_SELECTOR_KEYS = {
    "cwd",
    "mode",
    "mode_generation",
    "target_fingerprint",
}
_INTEGER_IDENTITY_KEYS = {
    "fleet_generation",
    "lease_generation",
    "project_generation",
    "pid",
}
_HASH_IDENTITY_KEYS = {
    "seat_record_sha256",
    "plan_sha256",
}
_RESOLVED_KEYS = {
    "schema",
    "identity",
    "cwd",
    "mode",
    "mode_generation",
}
_TARGET_VIEW_KEYS = {
    "schema",
    "target_fingerprint",
    "canonical_facts_sha256",
}
_CAPABILITY_KEYS = {
    "ctx_reader",
    "ctx_producer_contract",
    "content_contract",
    "route",
    "evaluator",
    "delivery_channels",
    "human_action",
}
_CAPABILITY_ENUMS = {
    "ctx_reader": {"available", "unavailable"},
    "ctx_producer_contract": {
        "versioned",
        "unversioned_live",
        "unsupported",
    },
    "content_contract": {"versioned", "unavailable", "unsupported"},
    "route": {"exact", "stale", "ambiguous", "unavailable"},
    "evaluator": {"accepted_exact", "unavailable"},
    "human_action": {"accepted_exact", "unavailable"},
}
_A_OPERATION = {
    "evaluate_ctx_source": "evaluate_ctx",
    "evaluate_content_source": "evaluate_content",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_A_RECEIPT_ID = re.compile(r"^d17-[0-9a-f]{32}$")


def _exact_int(value):
    return type(value) is int


def _canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value):
    raw = value if isinstance(value, bytes) else _canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _identity_valid(value):
    if not isinstance(value, dict) or set(value) != _PUBLIC_IDENTITY_KEYS:
        return False
    if not _PRIVATE_SELECTOR_KEYS.isdisjoint(value):
        return False
    for key in _INTEGER_IDENTITY_KEYS:
        if not _exact_int(value[key]):
            return False
    for key in _PUBLIC_IDENTITY_KEYS - _INTEGER_IDENTITY_KEYS:
        if not _nonempty_string(value[key]):
            return False
    return all(_HEX64.fullmatch(value[key]) is not None
               for key in _HASH_IDENTITY_KEYS)


def _capability_valid(value):
    if not isinstance(value, dict) or set(value) != _CAPABILITY_KEYS:
        return False
    for key, choices in _CAPABILITY_ENUMS.items():
        candidate = value[key]
        if not isinstance(candidate, str) or candidate not in choices:
            return False
    channels = value["delivery_channels"]
    return (
        isinstance(channels, list)
        and all(isinstance(item, str) for item in channels)
        and len(channels) == len(set(channels))
        and set(channels).issubset({"aib_dm", "ui"})
    )


def _physical_abs_path(value):
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        return False
    if os.path.normpath(value) != value:
        return False
    try:
        return os.path.realpath(value) == value
    except OSError:
        return False


def _source_projection(source):
    if source is None:
        return None
    event = source["source_event_id"]
    event_hash = hashlib.sha256(event.encode("utf-8")).hexdigest() \
        if _nonempty_string(event) else None
    return {
        "source_kind": source["source_kind"],
        "source_event_id_sha256": event_hash,
    }


def _request_valid(request):
    if not isinstance(request, dict):
        return False
    operation = request.get("operation")
    if not isinstance(operation, str) \
            or request.get("schema") != REQUEST_SCHEMA \
            or operation not in _OPERATIONS:
        return False
    common = {"schema", "operation", "selector", "source", "now_ms"}
    expected = set(common)
    if operation == "evaluate_ctx_source":
        expected.add("ctx_dir")
    elif operation == "evaluate_content_source":
        expected.add("content")
    if set(request) != expected:
        return False
    if not _exact_int(request["now_ms"]) or request["now_ms"] < 0:
        return False
    if not _identity_valid(request["selector"]):
        return False
    if not _PRIVATE_SELECTOR_KEYS.isdisjoint(request):
        return False

    source = request["source"]
    if operation == "status":
        return source is None
    if not isinstance(source, dict) \
            or set(source) != {"source_kind", "source_event_id"}:
        return False
    source_kind = source["source_kind"]
    if not isinstance(source_kind, str) or source_kind not in _SOURCE_KINDS:
        return False
    if operation == "resolve_capability":
        return source["source_event_id"] is None
    if source["source_event_id"] is not None \
            and not isinstance(source["source_event_id"], str):
        return False
    if operation == "evaluate_ctx_source":
        return (
            source["source_kind"] == "ctx_observation"
            and _nonempty_string(request["ctx_dir"])
            and request["ctx_dir"].startswith("/")
        )
    return (
        source["source_kind"] in _CONTENT_SOURCES
        and isinstance(request["content"], str)
    )


def _request_operation(request):
    if isinstance(request, dict) and isinstance(request.get("operation"), str):
        return request["operation"]
    return ""


def _resolved_context_reason(resolved, selector):
    if not isinstance(resolved, dict) or set(resolved) != _RESOLVED_KEYS \
            or resolved.get("schema") != RESOLVED_SCHEMA:
        return "resolved_context_invalid"
    identity = resolved.get("identity")
    if not _identity_valid(identity):
        return "resolved_context_invalid"
    if not _physical_abs_path(resolved.get("cwd")):
        return "resolved_context_invalid"
    if resolved.get("mode") not in MODES:
        return "resolved_context_invalid"
    generation = resolved.get("mode_generation")
    if not _exact_int(generation) or generation < 0:
        return "resolved_context_invalid"
    if identity != selector:
        return "route_stale"
    return None


def _resolve_target(bind_target_resolver, resolved):
    if not callable(bind_target_resolver):
        return None, "target_fingerprint_invalid"
    try:
        view = bind_target_resolver(copy.deepcopy(resolved))
    except (TypeError, ValueError):
        return None, "target_fingerprint_invalid"
    if not isinstance(view, dict) or set(view) != _TARGET_VIEW_KEYS \
            or view.get("schema") != TARGET_SCHEMA:
        return None, "target_fingerprint_invalid"
    fingerprint = view.get("target_fingerprint")
    if not isinstance(fingerprint, str) \
            or _HEX64.fullmatch(fingerprint) is None:
        return None, "target_fingerprint_invalid"
    facts = view.get("canonical_facts_sha256")
    if not isinstance(facts, str) or _HEX64.fullmatch(facts) is None:
        return None, "target_fingerprint_invalid"
    return fingerprint, None


def _c0_receipt(result, resolved):
    projection = result.get("evaluator_receipt")
    source = result.get("source")
    source_hash = source.get("source_event_id_sha256") \
        if isinstance(source, dict) else None
    evaluator_id = None
    evaluator_hash = None
    if isinstance(projection, dict):
        evaluator_id = projection.get("receipt_id")
        evaluator_hash = projection.get("receipt_sha256")
    body = {
        "schema": RECEIPT_SCHEMA,
        "operation": result.get("operation"),
        "reason": result.get("reason"),
        "identity_sha256": _digest(resolved) if resolved is not None else None,
        "source_event_id_sha256": source_hash,
        "evaluator_receipt_id": evaluator_id,
        "evaluator_receipt_sha256": evaluator_hash,
        "mode": result.get("mode"),
        "mode_generation": result.get("mode_generation"),
        "target_fingerprint": result.get("target_fingerprint"),
        "reminder_count": result.get("reminder_count"),
        "effect_count": result.get("effect_count", 0),
    }
    return {"receipt_id": _digest(body), **body}


def _result(operation, reason, *, rc, identity=None, capability=None,
            source=None, evaluator_projection=None, reminder_count=0,
            resolved=None, mode=None, mode_generation=None,
            target_fingerprint=None):
    result = {
        "schema": RESULT_SCHEMA,
        "ok": rc == 0,
        "reason": reason,
        "rc": rc,
        "operation": operation,
        "reminder_count": reminder_count,
        "effect_count": 0,
        "identity": copy.deepcopy(identity),
        "mode": mode,
        "mode_generation": mode_generation,
        "target_fingerprint": target_fingerprint,
        "capability": copy.deepcopy(capability),
        "source": copy.deepcopy(source),
        "evaluator_receipt": copy.deepcopy(evaluator_projection),
        "receipt": None,
    }
    result["receipt"] = _c0_receipt(result, resolved)
    return result


def _closed(operation, reason, *, identity=None, capability=None, source=None,
            resolved=None, mode=None, mode_generation=None,
            target_fingerprint=None):
    return _result(
        operation,
        reason,
        rc=2,
        identity=identity,
        capability=capability,
        source=source,
        resolved=resolved,
        mode=mode,
        mode_generation=mode_generation,
        target_fingerprint=target_fingerprint,
    )


def _verified_closed(reason):
    return {
        "ok": False,
        "rc": 2,
        "reason": reason,
        "evaluator_receipt": None,
    }


def _contains_private_value(value, content):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in {
                    "body", "content", "excerpt", "message", "message_body",
                    "prompt", "raw_content", "raw_prompt", "text"}:
                return True
            if _contains_private_value(item, content):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_private_value(item, content) for item in value)
    if isinstance(value, str):
        if value == content:
            return True
        return len(content.encode("utf-8")) >= 16 and content in value
    return False


def _receipt_hash_valid(receipt):
    supplied = receipt.get("receipt_sha256")
    if not isinstance(supplied, str) or _HEX64.fullmatch(supplied) is None:
        return False
    body = dict(receipt)
    del body["receipt_sha256"]
    try:
        return _digest(body) == supplied
    except (TypeError, ValueError):
        return False


def _durable_query_from_receipt(receipt):
    missing = DURABLE_QUERY_KEYS - {"schema"} - set(receipt)
    if missing:
        return None
    query = {"schema": DURABLE_QUERY_SCHEMA}
    for key in DURABLE_QUERY_KEYS - {"schema"}:
        query[key] = receipt[key]
    return query


def _durable_query_from_result(request, result, resolved, target):
    projection = result.get("evaluator_receipt")
    source = result.get("source")
    identity = resolved.get("identity") if isinstance(resolved, dict) else None
    if not isinstance(projection, dict) or not isinstance(source, dict) \
            or not isinstance(identity, dict):
        return None
    a_operation = _A_OPERATION.get(request.get("operation"))
    request_source = request.get("source") if isinstance(request, dict) else None
    source_kind = request_source.get("source_kind") \
        if isinstance(request_source, dict) else None
    return {
        "schema": DURABLE_QUERY_SCHEMA,
        "receipt_id": projection.get("receipt_id"),
        "receipt_sha256": projection.get("receipt_sha256"),
        "operation": a_operation,
        "session_uuid": identity.get("session_uuid"),
        "principal_id": identity.get("principal_id"),
        "seat_id": identity.get("seat_id"),
        "cwd": resolved.get("cwd"),
        "target_fingerprint": target,
        "mode_generation": resolved.get("mode_generation"),
        "source_kind": source_kind,
        "source_event_id_sha256": source.get("source_event_id_sha256"),
    }


def _call_durable_verifier(durable_verifier, query):
    if not callable(durable_verifier):
        return None, "durable_verifier_unavailable"
    try:
        row = durable_verifier(copy.deepcopy(query))
    except Exception as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, str) and reason:
            return None, reason
        return None, "durable_receipt_invalid"
    if not isinstance(row, dict):
        return None, "durable_receipt_invalid"
    return row, None


def _evaluator_terminal_valid(value, content):
    if not isinstance(value, dict):
        return False
    required = {"ok", "reason", "rc", "reminder_count", "effect_count", "receipt"}
    if not required.issubset(value):
        return False
    if type(value["ok"]) is not bool \
            or not _exact_int(value["rc"]) \
            or not _exact_int(value["reminder_count"]) \
            or not _exact_int(value["effect_count"]):
        return False
    reason = value["reason"]
    if not isinstance(reason, str):
        return False
    if value["ok"] is not (value["rc"] == 0) \
            or value["rc"] != 0 \
            or value["reminder_count"] not in {0, 1} \
            or value["effect_count"] != 0:
        return False
    receipt = value["receipt"]
    if not isinstance(receipt, dict) or _contains_private_value(value, content):
        return False
    receipt_id = receipt.get("receipt_id")
    # Transport replay returns the winner's durable row.  The wrapper's
    # reminder_count/reason may differ from that row; C0 must not collapse
    # those two layers.
    if receipt.get("schema") != EVALUATOR_RECEIPT_SCHEMA \
            or not isinstance(receipt_id, str) \
            or _A_RECEIPT_ID.fullmatch(receipt_id) is None \
            or not _receipt_hash_valid(receipt) \
            or not _exact_int(receipt.get("reminder_count")) \
            or receipt.get("reminder_count") not in {0, 1} \
            or receipt.get("effect_count") != 0:
        return False
    return _durable_query_from_receipt(receipt) is not None


def _projection_valid(value):
    if not isinstance(value, dict) \
            or set(value) != {"receipt_id", "receipt_sha256", "reason"}:
        return False
    receipt_id = value.get("receipt_id")
    receipt_hash = value.get("receipt_sha256")
    reason = value.get("reason")
    return (
        isinstance(receipt_id, str)
        and _A_RECEIPT_ID.fullmatch(receipt_id) is not None
        and isinstance(receipt_hash, str)
        and _HEX64.fullmatch(receipt_hash) is not None
        and isinstance(reason, str)
    )


def _result_shape_valid(result):
    if not isinstance(result, dict):
        return False
    expected = {
        "schema", "ok", "reason", "rc", "operation", "reminder_count",
        "effect_count", "identity", "mode", "mode_generation",
        "target_fingerprint", "capability", "source", "evaluator_receipt",
        "receipt",
    }
    if set(result) != expected or result.get("schema") != RESULT_SCHEMA:
        return False
    if type(result.get("ok")) is not bool \
            or not _exact_int(result.get("rc")) \
            or not _exact_int(result.get("reminder_count")) \
            or not _exact_int(result.get("effect_count")) \
            or result["effect_count"] != 0:
        return False
    if not _identity_valid(result.get("identity")):
        return False
    if result.get("mode") not in MODES:
        return False
    if not _exact_int(result.get("mode_generation")) \
            or result["mode_generation"] < 0:
        return False
    target = result.get("target_fingerprint")
    if not isinstance(target, str) or _HEX64.fullmatch(target) is None:
        return False
    source = result.get("source")
    if not isinstance(source, dict) \
            or set(source) != {"source_kind", "source_event_id_sha256"}:
        return False
    if not isinstance(result.get("receipt"), dict):
        return False
    return True


def _route_failure(capability):
    state = capability["route"]
    if state == "stale":
        return "route_stale"
    if state == "ambiguous":
        return "route_ambiguous"
    if state == "unavailable":
        return "route_unavailable"
    return None


def _evaluator_request(operation, request, identity, resolved, source, target):
    common = {
        "operation": _A_OPERATION[operation],
        "session_uuid": identity["session_uuid"],
        "principal_id": identity["principal_id"],
        "seat_id": identity["seat_id"],
        "cwd": resolved["cwd"],
        "mode_generation": resolved["mode_generation"],
        "source_kind": source["source_kind"],
        "source_event_id": source["source_event_id"],
        "target_fingerprint": target,
    }
    if operation == "evaluate_ctx_source":
        common["ctx_dir"] = request["ctx_dir"]
    else:
        common["content"] = request["content"]
    return common


def execute(request, *, identity_resolver, capability_resolver, evaluator,
            bind_target_resolver, durable_verifier):
    """Resolve one closed C0 v2 request and return a content-free receipt."""

    operation = _request_operation(request)
    if not _request_valid(request):
        return _closed(operation, "request_invalid")
    if not callable(durable_verifier):
        return _closed(operation, "durable_verifier_unavailable")

    source = copy.deepcopy(request["source"])
    source_view = _source_projection(source)
    if operation.startswith("evaluate_") \
            and source_view["source_event_id_sha256"] is None:
        return _closed(operation, "source_event_id_unavailable", source=source_view)

    selector = copy.deepcopy(request["selector"])
    resolved = identity_resolver(copy.deepcopy(selector))
    context_reason = _resolved_context_reason(resolved, selector)
    if context_reason is not None:
        return _closed(operation, context_reason, source=source_view)
    resolved = copy.deepcopy(resolved)
    identity = copy.deepcopy(resolved["identity"])
    mode = resolved["mode"]
    mode_generation = resolved["mode_generation"]

    resolved_capability = capability_resolver(
        copy.deepcopy(identity),
        copy.deepcopy(source),
    )
    if not _capability_valid(resolved_capability):
        return _closed(
            operation,
            "provider_capability_unavailable",
            identity=identity,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
        )
    capability = copy.deepcopy(resolved_capability)

    route_reason = _route_failure(capability)
    if route_reason is not None:
        return _closed(
            operation,
            route_reason,
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
        )

    target, target_reason = _resolve_target(bind_target_resolver, resolved)
    if target_reason is not None:
        return _closed(
            operation,
            target_reason,
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
        )

    if operation == "resolve_capability":
        return _result(
            operation,
            "capability_resolved",
            rc=0,
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
            target_fingerprint=target,
        )
    if operation == "status":
        return _result(
            operation,
            "status_resolved",
            rc=0,
            identity=identity,
            capability=capability,
            source=None,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
            target_fingerprint=target,
        )

    if operation == "evaluate_ctx_source":
        if capability["ctx_reader"] != "available" \
                or capability["ctx_producer_contract"] != "versioned":
            return _closed(
                operation,
                "provider_capability_unavailable",
                identity=identity,
                capability=capability,
                source=source_view,
                resolved=resolved,
                mode=mode,
                mode_generation=mode_generation,
                target_fingerprint=target,
            )
        content = ""
    else:
        contract = capability["content_contract"]
        if contract == "unavailable":
            return _closed(
                operation,
                "content_trigger_unavailable",
                identity=identity,
                capability=capability,
                source=source_view,
                resolved=resolved,
                mode=mode,
                mode_generation=mode_generation,
                target_fingerprint=target,
            )
        if contract != "versioned":
            return _closed(
                operation,
                "provider_capability_unavailable",
                identity=identity,
                capability=capability,
                source=source_view,
                resolved=resolved,
                mode=mode,
                mode_generation=mode_generation,
                target_fingerprint=target,
            )
        content = request["content"]

    if capability["evaluator"] != "accepted_exact":
        return _closed(
            operation,
            "evaluator_unavailable",
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
            target_fingerprint=target,
        )

    evaluated = evaluator(
        copy.deepcopy(_evaluator_request(
            operation, request, identity, resolved, source, target)))
    if not _evaluator_terminal_valid(evaluated, content):
        return _closed(
            operation,
            "evaluator_result_invalid",
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
            target_fingerprint=target,
        )

    query = _durable_query_from_receipt(evaluated["receipt"])
    row, verify_reason = _call_durable_verifier(durable_verifier, query)
    if verify_reason is not None:
        return _closed(
            operation,
            verify_reason,
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
            target_fingerprint=target,
        )
    if row != evaluated["receipt"]:
        return _closed(
            operation,
            "durable_receipt_invalid",
            identity=identity,
            capability=capability,
            source=source_view,
            resolved=resolved,
            mode=mode,
            mode_generation=mode_generation,
            target_fingerprint=target,
        )

    projection = {
        "receipt_id": row["receipt_id"],
        "receipt_sha256": row["receipt_sha256"],
        "reason": row["reason"],
    }
    return _result(
        operation,
        evaluated["reason"],
        rc=0,
        identity=identity,
        capability=capability,
        source=source_view,
        evaluator_projection=projection,
        reminder_count=evaluated["reminder_count"],
        resolved=resolved,
        mode=mode,
        mode_generation=mode_generation,
        target_fingerprint=target,
    )


def verify_result(request, result, *, identity_resolver,
                  bind_target_resolver, durable_verifier):
    """Rerun trusted resolvers and the durable verifier; never re-evaluate A."""

    if not _request_valid(request):
        return _verified_closed("request_invalid")
    if not callable(durable_verifier):
        return _verified_closed("durable_verifier_unavailable")
    if not _result_shape_valid(result) or not _projection_valid(
            result.get("evaluator_receipt")):
        return _verified_closed("verified_result_mismatch")

    selector = copy.deepcopy(request["selector"])
    resolved = identity_resolver(copy.deepcopy(selector))
    context_reason = _resolved_context_reason(resolved, selector)
    if context_reason is not None:
        if context_reason == "route_stale":
            return _verified_closed("verified_route_stale")
        return _verified_closed(context_reason)
    resolved = copy.deepcopy(resolved)

    if resolved["mode"] != result.get("mode") \
            or resolved["mode_generation"] != result.get("mode_generation"):
        return _verified_closed("verified_route_stale")

    target, target_reason = _resolve_target(bind_target_resolver, resolved)
    if target_reason is not None:
        return _verified_closed(target_reason)
    if target != result.get("target_fingerprint"):
        return _verified_closed("verified_target_stale")

    if result.get("identity") != resolved["identity"]:
        return _verified_closed("verified_result_mismatch")
    expected_receipt = _c0_receipt(result, resolved)
    if result.get("receipt") != expected_receipt:
        return _verified_closed("verified_result_mismatch")

    query = _durable_query_from_result(request, result, resolved, target)
    if query is None or set(query) != set(DURABLE_QUERY_KEYS):
        return _verified_closed("verified_result_mismatch")
    row, verify_reason = _call_durable_verifier(durable_verifier, query)
    if verify_reason is not None:
        return _verified_closed(verify_reason)

    projection = result["evaluator_receipt"]
    if row.get("receipt_id") != projection["receipt_id"] \
            or row.get("receipt_sha256") != projection["receipt_sha256"] \
            or row.get("reason") != projection["reason"]:
        return _verified_closed("verified_result_mismatch")

    return {
        "schema": VERIFIED_SCHEMA,
        "ok": True,
        "rc": 0,
        "reason": result["reason"],
        "reminder_count": result["reminder_count"],
        "effect_count": result["effect_count"],
        "identity": copy.deepcopy(result["identity"]),
        "identity_sha256": _digest(resolved),
        "mode": result["mode"],
        "mode_generation": result["mode_generation"],
        "target_fingerprint": result["target_fingerprint"],
        "source": copy.deepcopy(result["source"]),
        "evaluator_receipt": copy.deepcopy(result["evaluator_receipt"]),
        "c0_receipt_id": result["receipt"]["receipt_id"],
    }
