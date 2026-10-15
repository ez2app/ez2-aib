#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Machine-observed CTX handoff for the same permanent fleet member."""

from __future__ import annotations

import hashlib
import json

from . import authority_store as auth
from . import binding_store as bs
from . import fleet_members
from . import runtime_model_observation


def _ctx_policy():
    import os
    import sys
    rot = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rotation")
    if rot not in sys.path:
        sys.path.insert(0, rot)
    import ctx_policy
    return ctx_policy


HANDOFF_KEYS = frozenset((
    "schema", "capability_id", "binding_id", "binding_generation",
    "policy_sha256", "observation_sha256", "issued_at_ms", "expires_at_ms",
    "formal_approval",
))
REQUEST_KEYS = {
    "evaluate_ctx": frozenset((
        "operation", "binding_receipt", "ctx_observation", "ctx_policy_path",
        "ctx_policy_sha256",
    )),
    "handoff": frozenset((
        "operation", "binding_receipt", "handoff_capability", "ctx_policy_path",
        "ctx_policy_sha256", "successor",
    )),
    "verify_holder": frozenset(("operation", "binding_receipt")),
}


def _active_from_receipt(state_root, con, receipt, now_ms):
    _history, binding = bs.binding_from_receipt(
        state_root, con, receipt, "seat_binding_receipt_tampered")
    current = bs.current_binding(
        con, binding["project_id"], binding["fleet_id"],
        binding["fleet_member_id"])
    if current is None:
        raise auth.AuthorityError("seat_binding_not_found", rc=1)
    if current["binding_id"] != binding["binding_id"] \
            or int(current["generation"]) != int(binding["generation"]):
        raise auth.AuthorityError("seat_binding_holder_fenced", rc=1)
    if current["status"] != "active":
        raise auth.AuthorityError("seat_binding_source_revoked", rc=1)
    current_binding, _receipt = bs.verified_active_binding(
        state_root, con, current, "seat_binding_state_tampered")
    if current_binding != binding:
        raise auth.AuthorityError("seat_binding_state_tampered", rc=1)
    bs.validate_binding_sources(
        state_root, con, binding, now_ms,
        source_reason="seat_binding_source_revoked",
        lease_reason="seat_current_lease_stale")
    return binding


def _policy(path, supplied_hash):
    raw, digest = bs.file_fingerprint(path, "ctx_policy_unreadable")
    if digest != supplied_hash:
        raise auth.AuthorityError("ctx_policy_stale", rc=1)
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise auth.AuthorityError("ctx_policy_invalid", rc=1)
    # 10-15 CTX 單表:B 的 stages.must_handoff(90)刪了 ⇒ 交接憑證讀表上「要求交接」那一格(85)。
    try:
        threshold = _ctx_policy().trigger_pct("handoff_now", path)
    except (OSError, KeyError, ValueError, TypeError):
        raise auth.AuthorityError("ctx_policy_invalid", rc=1)
    if isinstance(threshold, bool) or not isinstance(threshold, int) \
            or not 0 <= threshold <= 100:
        raise auth.AuthorityError("ctx_policy_invalid", rc=1)
    return policy, digest, threshold


def _evaluate(state_root, request, now_ms):
    observation = request.get("ctx_observation")
    if not isinstance(observation, dict) \
            or set(observation) != {
                "source_kind", "used_pct", "observed_at_ms", "session_uuid",
                "provider",
            } or observation.get("source_kind") != "provider_machine" \
            or not isinstance(observation.get("observed_at_ms"), int) \
            or observation["observed_at_ms"] > int(now_ms) + 30_000 \
            or observation["observed_at_ms"] < int(now_ms) - 30_000:
        raise auth.AuthorityError("ctx_observation_unverifiable", rc=1)
    used = observation.get("used_pct")
    if isinstance(used, bool) or not isinstance(used, int) or not 0 <= used <= 100:
        raise auth.AuthorityError("ctx_observation_invalid")
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        with auth.immediate(con):
            binding = _active_from_receipt(
                state_root, con, request.get("binding_receipt"), now_ms)
            if observation.get("session_uuid") != binding["session_uuid"] \
                    or observation.get("provider") != binding["provider"]:
                raise auth.AuthorityError("ctx_observation_holder_mismatch",
                                          rc=1)
            _policy_doc, policy_hash, threshold = _policy(
                request.get("ctx_policy_path"),
                request.get("ctx_policy_sha256"))
            if used < threshold:
                return auth.result_ok(
                    reason="ctx_handoff_not_required", observed_pct=used,
                    threshold=threshold, task_resume=False)
            body = {
                "schema": "ez2-aib/ctx-handoff-capability/v1",
                "capability_id": bs.new_id("handoff-cap"),
                "binding_id": binding["binding_id"],
                "binding_generation": binding["generation"],
                "policy_sha256": policy_hash,
                "observation_sha256": hashlib.sha256(
                    bs.canonical(observation)).hexdigest(),
                "issued_at_ms": int(now_ms),
                "expires_at_ms": int(now_ms) + 60_000,
                "formal_approval": False,
            }
            capability = bs.sign_capability(state_root, body)
            con.execute(
                """INSERT INTO handoff_capabilities
                   (capability_id,capability_hash,binding_id,binding_generation,
                    policy_sha256,status,expires_at_ms,created_at_ms)
                   VALUES(?,?,?,?,?,'issued',?,?)""",
                (body["capability_id"],
                 auth.sha256_hex(auth.canonical_bytes(capability)),
                 body["binding_id"], body["binding_generation"], policy_hash,
                 body["expires_at_ms"], int(now_ms)),
            )
        return auth.result_ok(reason="ctx_handoff_required", observed_pct=used,
                              threshold=threshold,
                              handoff_capability=capability,
                              task_resume=False)
    finally:
        con.close()


def _successor(authority_path, value):
    required = {"principal_id", "seat_id", "session_uuid", "provider",
                "requested_model", "model_observation", "lease_id",
                "lease_until_ms"}
    if not isinstance(value, dict) or set(value) != required:
        raise auth.AuthorityError("seat_successor_invalid")
    registry = auth.load_bootstrap_authority(authority_path)
    principal = registry["principals"].get(value["principal_id"])
    if not isinstance(principal, dict) or principal.get("kind") != "main_ai":
        raise auth.AuthorityError("seat_successor_subagent_forbidden", rc=1)
    if principal.get("status") != "active" \
            or principal.get("seat_id") != value["seat_id"] \
            or principal.get("session_uuid") != value["session_uuid"]:
        raise auth.AuthorityError("seat_successor_identity_mismatch", rc=1)
    if not all(isinstance(value.get(name), str) and value[name]
               for name in required - {"lease_until_ms",
                                        "model_observation"}) \
            or not isinstance(value.get("model_observation"), dict):
        raise auth.AuthorityError("seat_successor_invalid")
    return value


def _handoff(state_root, authority_path, request, now_ms):
    successor = _successor(authority_path, request.get("successor"))
    lease_until = successor.get("lease_until_ms")
    if isinstance(lease_until, bool) or not isinstance(lease_until, int) \
            or lease_until <= int(now_ms) \
            or lease_until - int(now_ms) > 24 * 3600 * 1000:
        raise auth.AuthorityError("seat_successor_lease_invalid")
    capability = request.get("handoff_capability")
    cap = bs.verify_capability(
        state_root, capability, HANDOFF_KEYS, "ctx_handoff_capability_tampered")
    if cap["formal_approval"] is not False \
            or cap["expires_at_ms"] <= int(now_ms):
        raise auth.AuthorityError("ctx_handoff_capability_tampered", rc=1)
    _doc, current_policy_hash, _threshold = _policy(
        request.get("ctx_policy_path"), request.get("ctx_policy_sha256"))
    if current_policy_hash != cap["policy_sha256"]:
        raise auth.AuthorityError("ctx_policy_stale", rc=1)
    con = auth.open_db(state_root)
    observation_fd = None
    try:
        observation_fd = runtime_model_observation.acquire_session_lock(
            state_root, successor["session_uuid"], exclusive=False,
            create=False)
        bs.tables(con)
        with auth.immediate(con):
            binding = _active_from_receipt(
                state_root, con, request.get("binding_receipt"), now_ms)
            if cap["binding_id"] != binding["binding_id"] \
                    or cap["binding_generation"] != binding["generation"]:
                raise auth.AuthorityError("ctx_handoff_capability_stale", rc=1)
            cap_row = con.execute(
                "SELECT * FROM handoff_capabilities WHERE capability_id=?",
                (cap["capability_id"],),
            ).fetchone()
            if cap_row is None or cap_row["capability_hash"] != auth.sha256_hex(
                    auth.canonical_bytes(capability)):
                raise auth.AuthorityError("ctx_handoff_capability_tampered", rc=1)
            if cap_row["status"] != "issued":
                raise auth.AuthorityError("ctx_handoff_capability_replayed", rc=1)
            fleet = con.execute(
                "SELECT * FROM fleet_manifests WHERE fleet_id=?",
                (binding["fleet_id"],),
            ).fetchone()
            if fleet is None or fleet["status"] != "active":
                raise auth.AuthorityError("seat_fleet_state_invalid", rc=1)
            manifest = bs.json_load(fleet["manifest_json"],
                                    "seat_fleet_state_invalid")
            member = next((item for item in manifest.get("members", [])
                           if item.get("fleet_member_id") ==
                           binding["fleet_member_id"]), None)
            if member is None:
                raise auth.AuthorityError("seat_member_missing", rc=1)
            if successor["provider"] not in (
                    [member.get("preferred_provider")] +
                    list(member.get("provider_fallbacks", []))):
                raise auth.AuthorityError("seat_successor_provider_forbidden",
                                          rc=1)
            assignment = fleet_members.model_assignment(
                manifest, binding["fleet_member_id"], successor["provider"])
            if successor["requested_model"] != assignment["requested_model"]:
                raise auth.AuthorityError(
                    "seat_successor_requested_model_forbidden", rc=1)
            observation = runtime_model_observation.verify(
                state_root, successor["model_observation"],
                successor["session_uuid"], successor["provider"], now_ms)
            newest = runtime_model_observation.current_unlocked(
                state_root, successor["session_uuid"],
                successor["provider"], now_ms)
            if newest["observation_sha256"] != \
                    observation["observation_sha256"]:
                raise auth.AuthorityError(
                    "seat_successor_model_observation_not_current", rc=1)
            effective_model = observation["effective_model"]
            if effective_model not in assignment["allowed_effective_models"]:
                raise auth.AuthorityError(
                    "seat_successor_effective_model_forbidden", rc=1)
            lease = con.execute(
                """SELECT * FROM fleet_member_leases WHERE fleet_id=?
                   AND fleet_member_id=?""",
                (binding["fleet_id"], binding["fleet_member_id"]),
            ).fetchone()
            if lease is None or lease["status"] != "active" \
                    or lease["principal_id"] != binding["principal_id"] \
                    or lease["lease_id"] != binding["lease_id"]:
                raise auth.AuthorityError("seat_current_lease_stale", rc=1)
            fleet_generation = int(fleet["generation"]) + 1
            con.execute(
                """UPDATE fleet_member_leases SET lease_id=?,principal_id=?,
                   principal_seat_id=?,principal_session_uuid=?,generation=?,
                   lease_until_ms=?,status='active',updated_ms=?
                   WHERE fleet_id=? AND fleet_member_id=?""",
                (successor["lease_id"], successor["principal_id"],
                 successor["seat_id"], successor["session_uuid"],
                 fleet_generation, lease_until, int(now_ms),
                 binding["fleet_id"], binding["fleet_member_id"]),
            )
            manifest["generation"] = fleet_generation
            con.execute(
                """UPDATE fleet_manifests SET generation=?,manifest_json=?,
                   updated_ms=? WHERE fleet_id=?""",
                (fleet_generation,
                 auth.canonical_bytes(manifest).decode("utf-8"), int(now_ms),
                 binding["fleet_id"]),
            )
            next_binding = dict(binding)
            next_binding.update({
                "binding_id": bs.new_id("binding"),
                "generation": int(binding["generation"]) + 1,
                "principal_id": successor["principal_id"],
                "seat_id": successor["seat_id"],
                "session_uuid": successor["session_uuid"],
                "provider": successor["provider"],
                "requested_model": successor["requested_model"],
                "effective_model": effective_model,
                "model_observation": observation,
                "model_policy_sha256": auth.sha256_hex(
                    auth.canonical_bytes(manifest["model_policy"])),
                "model_assignment_sha256": auth.sha256_hex(
                    auth.canonical_bytes(assignment)),
                "lease_id": successor["lease_id"],
                "lease_generation": fleet_generation,
                "task_resume": False,
                "bound_at_ms": int(now_ms),
                "handoff_from_binding_id": binding["binding_id"],
            })
            next_binding["generations"] = dict(binding["generations"])
            next_binding["generations"]["fleet"] = fleet_generation
            receipt = bs.store_binding(con, state_root, next_binding, now_ms)
            con.execute(
                "UPDATE handoff_capabilities SET status='consumed' "
                "WHERE capability_id=?", (cap["capability_id"],))
        return auth.result_ok(reason="seat_handoff_completed",
                              binding=next_binding, receipt=receipt,
                              task_resume=False)
    finally:
        if observation_fd is not None:
            runtime_model_observation.release_session_lock(observation_fd)
        con.close()


def _verify_holder(state_root, request, now_ms):
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        binding = _active_from_receipt(
            state_root, con, request.get("binding_receipt"), now_ms)
        return auth.result_ok(reason="seat_binding_holder_verified",
                              binding=binding)
    finally:
        con.close()


def execute(state_root, authority_path, request, now_ms):
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("seat_continuity_request_invalid")
        operation = request.get("operation")
        if operation in REQUEST_KEYS and set(request) - REQUEST_KEYS[operation]:
            raise auth.AuthorityError("seat_continuity_request_invalid")
        if operation == "evaluate_ctx":
            return _evaluate(state_root, request, now_ms)
        if operation == "handoff":
            return _handoff(state_root, authority_path, request, now_ms)
        if operation == "verify_holder":
            return _verify_holder(state_root, request, now_ms)
        raise auth.AuthorityError("seat_continuity_operation_unknown")
    except auth.AuthorityError as err:
        return auth.result_error(err)
    except Exception:
        return auth.result_error(auth.AuthorityError(
            "seat_continuity_internal_error"))
