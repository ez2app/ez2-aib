#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Crash-safe, single-use application of a frozen P1A binding plan."""

from __future__ import annotations

import json

try:
    from . import authority_store as auth
    from . import binding_store as bs
    from . import bind_plan
    from . import runtime_model_observation
except ImportError:  # loaded outside the package (D17-D arm)
    from fleet_identity import authority_store as auth
    from fleet_identity import binding_store as bs
    from fleet_identity import bind_plan
    from fleet_identity import runtime_model_observation


REQUEST_KEYS = {
    "prepare": frozenset(("operation", "plan", "capability")),
    "apply": frozenset(("operation", "plan", "capability")),
    "reconcile": frozenset((
        "operation", "transaction_id", "plan", "capability",
    )),
    "resolve": frozenset((
        "operation", "project_id", "fleet_id", "fleet_member_id",
    )),
}


def _transaction_doc(row):
    return {
        "transaction_id": row["transaction_id"],
        "plan_sha256": row["plan_sha256"],
        "capability_id": row["capability_id"],
        "status": row["status"],
        "binding_id": row["binding_id"],
        "prepared_at_ms": int(row["prepared_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
    }


def _store_plan(con, plan, capability, status, now_ms):
    con.execute(
        """INSERT INTO bind_plans
           (plan_sha256,plan_json,capability_id,capability_hash,
            capability_status,expires_at_ms,created_at_ms)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(plan_sha256) DO UPDATE SET
             capability_status=excluded.capability_status""",
        (plan["plan_sha256"], auth.canonical_bytes(plan).decode("utf-8"),
         capability["capability_id"],
         auth.sha256_hex(auth.canonical_bytes(capability).decode("utf-8")),
         status, int(plan["expires_at_ms"]), int(now_ms)),
    )


def _recover_committed(state_root, con, plan, capability):
    """Recover an already committed terminal result after response loss."""
    if not isinstance(plan, dict) or not isinstance(plan.get("plan_sha256"), str):
        return None
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if bs.hash_value(body) != plan["plan_sha256"]:
        return None
    try:
        cap = bs.verify_capability(
            state_root, capability, bind_plan.CAPABILITY_KEYS,
            "bind_capability_tampered")
    except auth.AuthorityError:
        return None
    row = con.execute(
        "SELECT * FROM bind_transactions WHERE capability_id=?",
        (cap["capability_id"],),
    ).fetchone()
    if row is None or row["status"] != "committed":
        return None
    if row["plan_json"] != auth.canonical_bytes(plan).decode("utf-8") \
            or row["capability_json"] != auth.canonical_bytes(
                capability).decode("utf-8") or not row["result_json"]:
        raise auth.AuthorityError("bind_terminal_result_tampered", rc=1)
    result = bs.json_load(row["result_json"], "bind_terminal_result_tampered")
    history, stored_binding = bs.binding_from_receipt(
        state_root, con, result.get("receipt"), "bind_terminal_result_tampered")
    if stored_binding != result.get("binding") \
            or history["binding_id"] != row["binding_id"] \
            or result.get("transaction_id") != row["transaction_id"]:
        raise auth.AuthorityError("bind_terminal_result_tampered", rc=1)
    return auth.result_ok(reason="bind_apply_recovered", **result)


def _prepare(state_root, authority_path, request, now_ms):
    plan = request.get("plan")
    capability = request.get("capability")
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        with auth.immediate(con):
            row, cap = bind_plan.verify_plan(
                state_root, authority_path, con, plan, capability, now_ms,
                allow_prepared=True)
            existing = con.execute(
                "SELECT * FROM bind_transactions WHERE capability_id=?",
                (cap["capability_id"],),
            ).fetchone()
            if existing is not None:
                if existing["status"] == "abandoned":
                    raise auth.AuthorityError("bind_capability_abandoned", rc=1)
                if existing["status"] == "committed":
                    raise auth.AuthorityError("bind_capability_replayed", rc=1)
                return auth.result_ok(
                    reason="bind_apply_prepared",
                    transaction=_transaction_doc(existing), idempotent=True)
            transaction_id = bs.new_id("bind-tx")
            _store_plan(con, plan, capability, "prepared", now_ms)
            con.execute(
                """INSERT INTO bind_transactions
                   (transaction_id,plan_sha256,capability_id,status,binding_id,
                    plan_json,capability_json,result_json,prepared_at_ms,
                    updated_at_ms)
                   VALUES(?,?,?,'prepared',NULL,?,?,NULL,?,?)""",
                (transaction_id, plan["plan_sha256"], cap["capability_id"],
                 auth.canonical_bytes(plan).decode("utf-8"),
                 auth.canonical_bytes(capability).decode("utf-8"),
                 int(now_ms), int(now_ms)),
            )
            prepared = con.execute(
                "SELECT * FROM bind_transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
        return auth.result_ok(reason="bind_apply_prepared",
                              transaction=_transaction_doc(prepared))
    finally:
        con.close()


def _apply_on_connection(con, state_root, authority_path, request, now_ms):
    """Apply on the caller's open writer transaction.

    Returns ``(result, observation_fd)``. The caller releases the session
    lock. This function must not open a database, begin IMMEDIATE, or
    ``executescript`` (the last would commit the caller's transaction).
    """
    plan = request.get("plan")
    capability = request.get("capability")
    recovered = _recover_committed(state_root, con, plan, capability)
    if recovered is not None:
        return recovered, None
    plan_row, cap = bind_plan.verify_plan(
        state_root, authority_path, con, plan, capability, now_ms,
        allow_prepared=True)
    observation_fd = runtime_model_observation.acquire_session_lock(
        state_root, plan["session_uuid"], exclusive=False,
        create=False)
    try:
        plan_row, cap = bind_plan.verify_plan(
            state_root, authority_path, con, plan, capability, now_ms,
            allow_prepared=True, observation_lock_held=True)
        transaction = con.execute(
            "SELECT * FROM bind_transactions WHERE capability_id=?",
            (cap["capability_id"],),
        ).fetchone()
        if transaction is not None and transaction["status"] == "abandoned":
            raise auth.AuthorityError("bind_capability_abandoned", rc=1)
        if transaction is not None and transaction["status"] == "committed":
            raise auth.AuthorityError("bind_capability_replayed", rc=1)
        current = bs.current_binding(
            con, plan["project_id"], plan["fleet_id"],
            plan["fleet_member_id"])
        if current is not None and current["status"] == "active":
            raise auth.AuthorityError("bind_binding_already_active", rc=1)
        generation = int(current["generation"]) + 1 if current else 1
        transaction_id = transaction["transaction_id"] if transaction \
            else bs.new_id("bind-tx")
        binding = {
            "schema": "ez2-aib/active-seat-binding/v1",
            "binding_id": bs.new_id("binding"),
            "generation": generation,
            "status": "active",
            "project_id": plan["project_id"],
            "fleet_id": plan["fleet_id"],
            "fleet_member_id": plan["fleet_member_id"],
            "principal_id": plan["principal_id"],
            "seat_id": plan["seat_id"],
            "session_uuid": plan["session_uuid"],
            "provider": plan["provider"],
            "requested_model": plan["requested_model"],
            "effective_model": plan["effective_model"],
            "model_observation": dict(plan["model_observation"]),
            "model_policy_sha256": plan["model_policy_sha256"],
            "model_assignment_sha256": plan[
                "model_assignment_sha256"],
            "fleet_member_sha256": plan["fleet_member_sha256"],
            "role_code": plan["role_code"],
            "task_lane": plan["task_lane"],
            "persona_uid": plan["persona_uid"],
            "persona_sha256": plan["persona_sha256"],
            "persona_source_path": plan["persona_source_path"],
            "native_root_id": plan["native_root_id"],
            "native_root_receipt_path": plan["native_root_receipt_path"],
            "native_root_receipt_sha256": plan[
                "native_root_receipt_sha256"],
            "root_path": plan["root_path"],
            "root_sha256": plan["root_sha256"],
            "checkpoint_path": plan["checkpoint_path"],
            "checkpoint_relpath": plan["checkpoint_relpath"],
            "checkpoint_sha256": plan["checkpoint_sha256"],
            "checkpoint_generation": plan["checkpoint_generation"],
            "memory_scope_id": plan["memory_scope_id"],
            "memory_store_ref": plan["memory_store_ref"],
            "memory_schema_version": plan["memory_schema_version"],
            "lease_id": plan["lease_id"],
            "lease_generation": plan["lease_generation"],
            "generations": dict(plan["generations"]),
            "task_resume": False,
            "plan_sha256": plan["plan_sha256"],
            "bound_at_ms": int(now_ms),
        }
        receipt = bs.store_binding(con, state_root, binding, now_ms)
        terminal = {
            "binding": binding,
            "receipt": receipt,
            "transaction_id": transaction_id,
            "task_resume": False,
        }
        terminal_json = auth.canonical_bytes(terminal).decode("utf-8")
        if transaction is None:
            _store_plan(con, plan, capability, "consumed", now_ms)
            con.execute(
                """INSERT INTO bind_transactions
                   (transaction_id,plan_sha256,capability_id,status,
                    binding_id,plan_json,capability_json,result_json,
                    prepared_at_ms,updated_at_ms)
                   VALUES(?,?,?,'committed',?,?,?,?,?,?)""",
                (transaction_id, plan["plan_sha256"], cap["capability_id"],
                 binding["binding_id"],
                 auth.canonical_bytes(plan).decode("utf-8"),
                 auth.canonical_bytes(capability).decode("utf-8"),
                 terminal_json, int(now_ms), int(now_ms)),
            )
        else:
            con.execute(
                """UPDATE bind_transactions SET status='committed',
                   binding_id=?,result_json=?,updated_at_ms=?
                   WHERE transaction_id=?""",
                (binding["binding_id"], terminal_json, int(now_ms),
                 transaction_id),
            )
        con.execute(
            "UPDATE bind_plans SET capability_status='consumed' "
            "WHERE plan_sha256=?", (plan["plan_sha256"],))
        return auth.result_ok(reason="bind_applied", **terminal), observation_fd
    except Exception:
        runtime_model_observation.release_session_lock(observation_fd)
        raise


def apply_in_transaction(con, state_root, authority_path, request, now_ms):
    """Same contract as ``_apply``, on the caller's IMMEDIATE transaction.

    D17-D ``apply_authorized`` is the paired caller: consume and apply
    must share one writer lock. This entry must not open another
    connection or begin its own transaction.
    """
    if not getattr(con, "in_transaction", False):
        raise auth.AuthorityError("caller_transaction_required", rc=2)
    result, observation_fd = _apply_on_connection(
        con, state_root, authority_path, request, now_ms)
    if observation_fd is not None:
        runtime_model_observation.release_session_lock(observation_fd)
    return result


def _apply(state_root, authority_path, request, now_ms):
    con = auth.open_db(state_root)
    observation_fd = None
    try:
        bs.tables(con)
        with auth.immediate(con):
            result, observation_fd = _apply_on_connection(
                con, state_root, authority_path, request, now_ms)
        return result
    finally:
        if observation_fd is not None:
            runtime_model_observation.release_session_lock(observation_fd)
        con.close()


def _reconcile(state_root, authority_path, request, now_ms):
    transaction_id = request.get("transaction_id")
    if request.get("plan") is None or request.get("capability") is None:
        raise auth.AuthorityError("bind_reconcile_proof_missing", rc=1)
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        with auth.immediate(con):
            _plan_row, cap = bind_plan.verify_plan(
                state_root, authority_path, con, request.get("plan"),
                request.get("capability"), now_ms, allow_prepared=True)
            row = con.execute(
                "SELECT * FROM bind_transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                raise auth.AuthorityError("bind_transaction_not_found", rc=1)
            if row["capability_id"] != cap["capability_id"] \
                    or row["plan_json"] != auth.canonical_bytes(
                        request["plan"]).decode("utf-8") \
                    or row["capability_json"] != auth.canonical_bytes(
                        request["capability"]).decode("utf-8"):
                raise auth.AuthorityError("bind_reconcile_proof_mismatch", rc=1)
            if row["status"] == "prepared":
                con.execute(
                    """UPDATE bind_transactions SET status='abandoned',
                       updated_at_ms=? WHERE transaction_id=?""",
                    (int(now_ms), transaction_id),
                )
                con.execute(
                    "UPDATE bind_plans SET capability_status='abandoned' "
                    "WHERE capability_id=?", (row["capability_id"],))
            elif row["status"] == "committed":
                raise auth.AuthorityError("bind_transaction_already_committed",
                                          rc=1)
            current = con.execute(
                "SELECT * FROM bind_transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
        return auth.result_ok(reason="bind_apply_reconciled_no_effect",
                              transaction=_transaction_doc(current))
    finally:
        con.close()


def _resolve(state_root, request, now_ms):
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        row = bs.current_binding(
            con, request.get("project_id"), request.get("fleet_id"),
            request.get("fleet_member_id"))
        if row is None:
            raise auth.AuthorityError("bind_binding_not_found", rc=1)
        if row["status"] == "source_revoked":
            raise auth.AuthorityError("binding_source_revoked", rc=1)
        if row["status"] != "active":
            raise auth.AuthorityError("bind_binding_inactive", rc=1)
        binding, receipt = bs.verified_active_binding(state_root, con, row)
        bs.validate_binding_sources(
            state_root, con, binding, now_ms,
            source_reason="binding_source_revoked",
            lease_reason="binding_member_lease_stale")
        return auth.result_ok(reason="bind_binding_resolved", binding=binding,
                              receipt=receipt)
    finally:
        con.close()


def execute(state_root, authority_path, request, now_ms):
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("bind_apply_request_invalid")
        operation = request.get("operation")
        if operation in REQUEST_KEYS and set(request) - REQUEST_KEYS[operation]:
            raise auth.AuthorityError("bind_apply_request_invalid")
        if operation == "prepare":
            return _prepare(state_root, authority_path, request, now_ms)
        if operation == "apply":
            return _apply(state_root, authority_path, request, now_ms)
        if operation == "reconcile":
            return _reconcile(state_root, authority_path, request, now_ms)
        if operation == "resolve":
            return _resolve(state_root, request, now_ms)
        raise auth.AuthorityError("bind_apply_operation_unknown")
    except auth.AuthorityError as err:
        return auth.result_error(err)
    except Exception:
        return auth.result_error(auth.AuthorityError("bind_apply_internal_error"))
