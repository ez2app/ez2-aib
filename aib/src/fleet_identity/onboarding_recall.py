#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exact-byte persona and native-checkpoint recall receipts per active seat."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import authority_store as auth
from . import binding_store as bs


CONSUMER_KINDS = (
    "recall_cache", "prompt_cache", "rag_index", "continuity",
)
REQUEST_KEYS = {
    "read": frozenset((
        "operation", "binding_receipt", "session_uuid", "provider",
        "requested_model", "effective_model",
    )),
    "verify": frozenset(("operation", "receipt")),
    "consume": frozenset((
        "operation", "consumer_kind", "consumer_id", "receipt",
    )),
}


def _source_status(con, binding):
    project = con.execute(
        "SELECT status FROM projects WHERE project_id=?",
        (binding["project_id"],),
    ).fetchone()
    if project is None or project["status"] != "active":
        return "project"
    root = con.execute(
        "SELECT status FROM active_native_roots WHERE native_root_id=?",
        (binding["native_root_id"],),
    ).fetchone()
    if root is None or root["status"] != "active":
        return "native_root"
    return None


def _current_binding(state_root, con, binding_receipt, now_ms):
    _history, binding = bs.binding_from_receipt(
        state_root, con, binding_receipt, "onboarding_binding_receipt_tampered")
    current = bs.current_binding(
        con, binding["project_id"], binding["fleet_id"],
        binding["fleet_member_id"])
    if current is None:
        raise auth.AuthorityError("onboarding_binding_missing", rc=1)
    if current["status"] == "source_revoked":
        raise auth.AuthorityError("onboarding_source_revoked_pending_fanout",
                                  rc=1)
    if current["binding_id"] != binding["binding_id"] \
            or int(current["generation"]) != int(binding["generation"]):
        raise auth.AuthorityError("onboarding_binding_holder_fenced", rc=1)
    if current["status"] != "active":
        raise auth.AuthorityError("onboarding_binding_inactive", rc=1)
    current_binding, _receipt = bs.verified_active_binding(
        state_root, con, current, "onboarding_binding_state_invalid")
    if current_binding != binding:
        raise auth.AuthorityError("onboarding_binding_state_invalid", rc=1)
    _exact_bytes(binding)
    bs.validate_binding_sources(
        state_root, con, binding, now_ms,
        source_reason="onboarding_source_revoked_pending_fanout",
        lease_reason="onboarding_member_lease_stale")
    return binding


def _exact_bytes(binding):
    persona_raw, persona_sha = bs.file_fingerprint(
        binding["persona_source_path"], "onboarding_persona_unreadable")
    if persona_sha != binding["persona_sha256"]:
        raise auth.AuthorityError("onboarding_persona_bytes_stale", rc=1)
    checkpoint_raw, checkpoint_sha = bs.file_fingerprint(
        binding["checkpoint_path"], "onboarding_checkpoint_unreadable")
    if checkpoint_sha != binding["checkpoint_sha256"]:
        raise auth.AuthorityError("onboarding_checkpoint_bytes_stale", rc=1)
    try:
        checkpoint = json.loads(checkpoint_raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise auth.AuthorityError("onboarding_checkpoint_bytes_stale", rc=1)
    if not isinstance(checkpoint, dict) \
            or checkpoint.get("generation") != binding["checkpoint_generation"] \
            or checkpoint.get("project_id") != binding["project_id"] \
            or checkpoint.get("fleet_member_id") != binding["fleet_member_id"]:
        raise auth.AuthorityError("onboarding_checkpoint_bytes_stale", rc=1)
    return persona_raw, checkpoint_raw


def _read(state_root, request, now_ms):
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        with auth.immediate(con):
            binding = _current_binding(
                state_root, con, request.get("binding_receipt"), now_ms)
            if request.get("session_uuid") != binding["session_uuid"]:
                raise auth.AuthorityError("onboarding_session_mismatch", rc=1)
            if request.get("provider") != binding["provider"]:
                raise auth.AuthorityError("onboarding_provider_mismatch", rc=1)
            if request.get("requested_model") != binding["requested_model"]:
                raise auth.AuthorityError(
                    "onboarding_requested_model_mismatch", rc=1)
            if request.get("effective_model") != binding["effective_model"]:
                raise auth.AuthorityError(
                    "onboarding_effective_model_mismatch", rc=1)
            if _source_status(con, binding) is not None:
                raise auth.AuthorityError(
                    "onboarding_source_revoked_pending_fanout", rc=1)
            persona_raw, checkpoint_raw = _exact_bytes(binding)
            read_id = bs.new_id("read")
            receipt = bs.seal_receipt(state_root, {
                "schema": "ez2-aib/onboarding-read-receipt/v1",
                "read_receipt_id": read_id,
                "binding_id": binding["binding_id"],
                "binding_generation": binding["generation"],
                "binding_sha256": auth.sha256_hex(
                    auth.canonical_bytes(binding)),
                "project_id": binding["project_id"],
                "fleet_id": binding["fleet_id"],
                "fleet_member_id": binding["fleet_member_id"],
                "native_root_id": binding["native_root_id"],
                "session_uuid": binding["session_uuid"],
                "provider": binding["provider"],
                "requested_model": binding["requested_model"],
                "effective_model": binding["effective_model"],
                "model_observation_sha256": binding["model_observation"][
                    "observation_sha256"],
                "model_policy_sha256": binding["model_policy_sha256"],
                "model_assignment_sha256": binding[
                    "model_assignment_sha256"],
                "fleet_member_sha256": binding["fleet_member_sha256"],
                "role_code": binding["role_code"],
                "task_lane": binding["task_lane"],
                "persona_uid": binding["persona_uid"],
                "persona_source_path": binding["persona_source_path"],
                "persona_sha256": hashlib.sha256(persona_raw).hexdigest(),
                "persona_bytes_length": len(persona_raw),
                "checkpoint_path": binding["checkpoint_path"],
                "checkpoint_sha256": hashlib.sha256(
                    checkpoint_raw).hexdigest(),
                "checkpoint_bytes_length": len(checkpoint_raw),
                "checkpoint_generation": binding["checkpoint_generation"],
                "native_root_receipt_sha256": binding[
                    "native_root_receipt_sha256"],
                "root_sha256": binding["root_sha256"],
                "task_resume": False,
                "read_at_ms": int(now_ms),
                "formal_approval": False,
            })
            con.execute(
                """INSERT INTO onboarding_read_receipts
                   (read_receipt_id,binding_id,binding_generation,project_id,
                    native_root_id,status,receipt_json,created_at_ms)
                   VALUES(?,?,?,?,?,'active',?,?)""",
                (read_id, binding["binding_id"], binding["generation"],
                 binding["project_id"], binding["native_root_id"],
                 auth.canonical_bytes(receipt).decode("utf-8"), int(now_ms)),
            )
            consumer_refs = {}
            for kind in CONSUMER_KINDS:
                consumer_id = bs.new_id(kind.replace("_", "-"))
                consumer_refs[kind] = consumer_id
                con.execute(
                    """INSERT INTO onboarding_consumers
                       (consumer_kind,consumer_id,read_receipt_id,binding_id,
                        status,created_at_ms) VALUES(?,?,?,?,'active',?)""",
                    (kind, consumer_id, read_id, binding["binding_id"],
                     int(now_ms)),
                )
        return auth.result_ok(reason="onboarding_recall_read",
                              receipt=receipt, consumer_refs=consumer_refs,
                              task_resume=False)
    finally:
        con.close()


def _receipt_row(state_root, con, receipt):
    bs.verify_receipt(state_root, receipt,
                      "onboarding_read_receipt_tampered")
    row = con.execute(
        "SELECT * FROM onboarding_read_receipts WHERE read_receipt_id=?",
        (receipt.get("read_receipt_id"),),
    ).fetchone()
    if row is None or row["receipt_json"] != auth.canonical_bytes(receipt).decode(
            "utf-8"):
        raise auth.AuthorityError("onboarding_read_receipt_tampered", rc=1)
    return row


def _verify(state_root, con, receipt, now_ms):
    row = _receipt_row(state_root, con, receipt)
    history = con.execute(
        "SELECT * FROM binding_history WHERE binding_id=?",
        (row["binding_id"],),
    ).fetchone()
    if history is None:
        raise auth.AuthorityError("onboarding_binding_missing", rc=1)
    binding = bs.json_load(history["binding_json"],
                           "onboarding_binding_state_invalid")
    current = bs.current_binding(
        con, binding["project_id"], binding["fleet_id"],
        binding["fleet_member_id"])
    if current is None:
        raise auth.AuthorityError("onboarding_binding_missing", rc=1)
    if current["status"] == "source_revoked":
        raise auth.AuthorityError("onboarding_read_receipt_revoked", rc=1)
    if current["binding_id"] != binding["binding_id"] \
            or int(current["generation"]) != int(row["binding_generation"]):
        raise auth.AuthorityError(
            "onboarding_read_receipt_holder_fenced", rc=1)
    current_binding, _binding_receipt = bs.verified_active_binding(
        state_root, con, current, "onboarding_binding_state_invalid")
    binding_sha256 = auth.sha256_hex(auth.canonical_bytes(current_binding))
    if current_binding != binding \
            or receipt.get("binding_sha256") != binding_sha256:
        raise auth.AuthorityError("onboarding_binding_state_invalid", rc=1)
    if _source_status(con, binding) is not None:
        raise auth.AuthorityError(
            "onboarding_source_revoked_pending_fanout", rc=1)
    if row["status"] == "revoked":
        raise auth.AuthorityError("onboarding_read_receipt_revoked", rc=1)
    _exact_bytes(binding)
    bs.validate_binding_sources(
        state_root, con, binding, now_ms,
        source_reason="onboarding_read_receipt_revoked",
        lease_reason="onboarding_member_lease_stale")
    _exact_bytes(binding)
    return row, binding


def _verify_operation(state_root, request, now_ms):
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        _row, binding = _verify(
            state_root, con, request.get("receipt"), now_ms)
        return auth.result_ok(reason="onboarding_read_receipt_verified",
                              binding_id=binding["binding_id"],
                              persona_sha256=binding["persona_sha256"])
    finally:
        con.close()


def _consume(state_root, request, now_ms):
    kind = request.get("consumer_kind")
    consumer_id = request.get("consumer_id")
    if kind not in CONSUMER_KINDS or not isinstance(consumer_id, str):
        raise auth.AuthorityError("onboarding_consumer_unknown", rc=1)
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        row = con.execute(
            """SELECT * FROM onboarding_consumers WHERE consumer_kind=?
               AND consumer_id=?""", (kind, consumer_id),
        ).fetchone()
        if row is None:
            raise auth.AuthorityError("onboarding_consumer_unknown", rc=1)
        if row["status"] == "revoked":
            err = auth.AuthorityError("onboarding_consumer_revoked", rc=1)
            err.consumer_kind = kind
            raise err
        receipt_row = _receipt_row(state_root, con, request.get("receipt"))
        if receipt_row["read_receipt_id"] != row["read_receipt_id"]:
            raise auth.AuthorityError("onboarding_consumer_receipt_mismatch",
                                      rc=1)
        _verified, binding = _verify(
            state_root, con, request.get("receipt"), now_ms)
        return auth.result_ok(reason="onboarding_consumer_read",
                              consumer_kind=kind, consumer_id=consumer_id,
                              persona_sha256=binding["persona_sha256"])
    finally:
        con.close()


def execute(state_root, authority_path, request, now_ms):
    del authority_path
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("onboarding_request_invalid")
        operation = request.get("operation")
        if operation in REQUEST_KEYS and set(request) - REQUEST_KEYS[operation]:
            raise auth.AuthorityError("onboarding_request_invalid")
        if operation == "read":
            return _read(state_root, request, now_ms)
        if operation == "verify":
            return _verify_operation(state_root, request, now_ms)
        if operation == "consume":
            return _consume(state_root, request, now_ms)
        raise auth.AuthorityError("onboarding_operation_unknown")
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        if hasattr(err, "consumer_kind"):
            result["consumer_kind"] = err.consumer_kind
        return result
    except Exception:
        return auth.result_error(auth.AuthorityError("onboarding_internal_error"))
