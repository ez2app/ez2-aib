#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Permanent fleet members and process-safe seat leases."""

import json

try:
    from . import authority_store as auth
except ImportError:  # pragma: no cover
    import authority_store as auth


_PROOF_KEYS = frozenset((
    "schema", "approval_id", "authority_proof", "owner_approval",
    "main_approval",
))
_REQUEST_KEYS = {
    "register_manifest": frozenset((
        "operation", "expected_generation", "manifest",
    )),
    "claim_lease": frozenset((
        "operation", "fleet_id", "fleet_member_id", "principal_id",
        "lease_id", "expected_generation", "lease_until_ms",
    )),
    "release_lease": frozenset((
        "operation", "fleet_id", "fleet_member_id", "principal_id",
        "lease_id", "expected_generation",
    )),
}

MODEL_POLICY_SCHEMA = "ez2-aib/fleet-model-policy/v1"
_MODEL_POLICY_KEYS = frozenset(("schema", "assignments"))
_MODEL_ASSIGNMENT_KEYS = frozenset((
    "fleet_member_id", "provider", "requested_model",
    "allowed_effective_models",
))


def _tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS fleet_manifests(
      fleet_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL,
      manifest_json TEXT NOT NULL,
      owner_id TEXT NOT NULL,
      updated_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS fleet_member_leases(
      fleet_id TEXT NOT NULL,
      fleet_member_id TEXT NOT NULL,
      lease_id TEXT NOT NULL,
      principal_id TEXT NOT NULL,
      principal_seat_id TEXT NOT NULL,
      principal_session_uuid TEXT NOT NULL,
      generation INTEGER NOT NULL,
      lease_until_ms INTEGER NOT NULL,
      status TEXT NOT NULL,
      updated_ms INTEGER NOT NULL,
      PRIMARY KEY(fleet_id,fleet_member_id)
    );
    """)


def _verify_owner(authority_path, request, now_ms, fleet_id):
    try:
        return auth.verify_owner_approval(authority_path, request, now_ms,
                                          action="fleet.register", subject_id=fleet_id)
    except auth.AuthorityError as err:
        if err.reason == "p1a_authority_proof_missing":
            raise auth.AuthorityError("fleet_owner_approval_missing", rc=1)
        if err.reason in ("p1a_authority_request_hash_mismatch",
                          "p1a_authority_proof_invalid"):
            raise auth.AuthorityError("fleet_owner_approval_mismatch", rc=1)
        raise


def _model_policy(value, members):
    """Validate the owner-approved model choice for every provider/member.

    The manifest registration request is already bound to an owner approval.
    Keeping this policy inside those exact approved bytes prevents a seat,
    caller or provider default from silently choosing the binding model.
    """
    if not isinstance(value, dict) or set(value) != _MODEL_POLICY_KEYS \
            or value.get("schema") != MODEL_POLICY_SCHEMA:
        raise auth.AuthorityError("fleet_model_policy_invalid")
    assignments = value.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise auth.AuthorityError("fleet_model_policy_invalid")
    expected = set()
    for member in members:
        providers = [member["preferred_provider"]] + list(
            member["provider_fallbacks"])
        if len(providers) != len(set(providers)):
            raise auth.AuthorityError("fleet_model_policy_invalid")
        expected.update((member["fleet_member_id"], provider)
                        for provider in providers)
    observed = set()
    copied = []
    for row in assignments:
        if not isinstance(row, dict) or set(row) != _MODEL_ASSIGNMENT_KEYS:
            raise auth.AuthorityError("fleet_model_policy_invalid")
        for key in ("fleet_member_id", "provider", "requested_model"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise auth.AuthorityError("fleet_model_policy_invalid")
        allowed = row.get("allowed_effective_models")
        if not isinstance(allowed, list) or not allowed \
                or not all(isinstance(item, str) and item for item in allowed) \
                or len(allowed) != len(set(allowed)) \
                or row["requested_model"] not in allowed:
            raise auth.AuthorityError("fleet_model_policy_invalid")
        identity = (row["fleet_member_id"], row["provider"])
        if identity in observed:
            raise auth.AuthorityError("fleet_model_policy_ambiguous")
        observed.add(identity)
        copied.append(dict(row, allowed_effective_models=list(allowed)))
    if observed != expected:
        raise auth.AuthorityError("fleet_model_policy_incomplete")
    return {"schema": MODEL_POLICY_SCHEMA, "assignments": copied}


def model_assignment(manifest, fleet_member_id, provider):
    """Return one exact approved assignment; ambiguity is always an error."""
    if not isinstance(manifest, dict) or not isinstance(
            manifest.get("members"), list):
        raise auth.AuthorityError("fleet_model_policy_invalid", rc=1)
    policy = _model_policy(manifest.get("model_policy"), manifest["members"])
    matches = [row for row in policy["assignments"]
               if row["fleet_member_id"] == fleet_member_id
               and row["provider"] == provider]
    if len(matches) != 1:
        raise auth.AuthorityError(
            "fleet_model_policy_ambiguous" if matches
            else "fleet_model_policy_assignment_missing", rc=1)
    return matches[0]


def validate_model_policy(value, members):
    """Public closed-policy validator used by frozen launch-plan consumers."""
    return _model_policy(value, members)


def _manifest(value):
    required = {"fleet_id", "project_id", "captain_member_id",
                "manager_member_id", "members", "model_policy"}
    if not isinstance(value, dict) or set(value) != required:
        raise auth.AuthorityError("fleet_manifest_invalid")
    for key in required - {"members", "model_policy"}:
        if not isinstance(value.get(key), str) or not value[key]:
            raise auth.AuthorityError("fleet_manifest_invalid")
    members = value.get("members")
    if not isinstance(members, list) or not members:
        raise auth.AuthorityError("fleet_manifest_invalid")
    required_member = {"fleet_member_id", "role_code", "seat_name",
                       "preferred_provider", "provider_fallbacks", "persona_uid",
                       "persona_version", "memory_binding_ref", "memory_scope_id",
                       "task_lane"}
    seen_member = set()
    seen_name = set()
    copied = []
    for row in members:
        if not isinstance(row, dict) or set(row) != required_member:
            raise auth.AuthorityError("fleet_manifest_invalid")
        for key in required_member - {"provider_fallbacks"}:
            if not isinstance(row.get(key), str) or not row[key]:
                raise auth.AuthorityError("fleet_manifest_invalid")
        if not isinstance(row.get("provider_fallbacks"), list) \
                or not all(isinstance(x, str) and x for x in row["provider_fallbacks"]):
            raise auth.AuthorityError("fleet_manifest_invalid")
        if row["fleet_member_id"] in seen_member or row["seat_name"] in seen_name:
            raise auth.AuthorityError("fleet_manifest_ambiguous")
        if row["fleet_member_id"] in (row["role_code"], row["seat_name"]):
            raise auth.AuthorityError("fleet_member_identity_not_permanent")
        seen_member.add(row["fleet_member_id"])
        seen_name.add(row["seat_name"])
        copied.append(dict(row, provider_fallbacks=list(row["provider_fallbacks"])))
    policy = _model_policy(value.get("model_policy"), copied)
    return dict(value, members=copied, model_policy=policy)


def _register(con, authority_path, request, now_ms):
    manifest = _manifest(request.get("manifest"))
    fleet_id = manifest["fleet_id"]
    verified = _verify_owner(authority_path, request, now_ms, fleet_id)
    expected = request.get("expected_generation")
    if expected != 0:
        raise auth.AuthorityError("fleet_generation_conflict", rc=1)
    with auth.immediate(con):
        row = con.execute("SELECT generation FROM fleet_manifests WHERE fleet_id=?",
                          (fleet_id,)).fetchone()
        if row:
            raise auth.AuthorityError("fleet_manifest_exists", rc=1)
        auth.consume_proof(con, verified, now_ms)
        stored = dict(manifest, generation=1)
        con.execute("""INSERT INTO fleet_manifests
                       (fleet_id,project_id,generation,status,manifest_json,owner_id,updated_ms)
                       VALUES(?,?,?,?,?,?,?)""",
                    (fleet_id, manifest["project_id"], 1, "active",
                     auth.canonical_bytes(stored).decode("utf-8"),
                     verified["authority_id"], int(now_ms)))
        receipt = auth.append_event(con, "fleet", fleet_id, 1, "registered",
                                    {"manifest_hash": auth.sha256_hex(
                                        auth.canonical_bytes(stored))},
                                    now_ms, verified)
    return auth.result_ok(reason="fleet_manifest_registered", manifest=stored,
                          receipt=receipt)


def _authority_principal(authority_path, principal_id):
    registry = auth.load_bootstrap_authority(authority_path)
    row = registry["principals"].get(principal_id)
    if not isinstance(row, dict) or row.get("status") != "active" \
            or row.get("kind") != "main_ai" \
            or not isinstance(row.get("seat_id"), str) \
            or not isinstance(row.get("session_uuid"), str):
        raise auth.AuthorityError("fleet_member_principal_invalid", rc=1)
    return row


def _load_manifest(con, fleet_id):
    row = con.execute("SELECT * FROM fleet_manifests WHERE fleet_id=?",
                      (fleet_id,)).fetchone()
    if row is None or row["status"] != "active":
        raise auth.AuthorityError("fleet_manifest_unknown", rc=1)
    manifest = json.loads(row["manifest_json"])
    return row, manifest


def _member(manifest, member_id):
    for row in manifest.get("members", []):
        if row.get("fleet_member_id") == member_id:
            return row
    raise auth.AuthorityError("fleet_member_unknown", rc=1)


def _lease_doc(row):
    return {"fleet_id": row["fleet_id"],
            "fleet_member_id": row["fleet_member_id"],
            "lease_id": row["lease_id"], "principal_id": row["principal_id"],
            "seat_id": row["principal_seat_id"],
            "session_uuid": row["principal_session_uuid"],
            "generation": int(row["generation"]),
            "lease_until_ms": int(row["lease_until_ms"]),
            "status": row["status"]}


def _owner_receipt(row):
    return {"schema": "ez2-aib/fleet-current-owner-receipt/v1",
            "fleet_id": row["fleet_id"],
            "fleet_member_id": row["fleet_member_id"],
            "lease_id": row["lease_id"], "principal_id": row["principal_id"],
            "lease_generation": int(row["generation"]),
            "lease_until_ms": int(row["lease_until_ms"])}


def _claim(con, authority_path, request, now_ms):
    fleet_id = request.get("fleet_id")
    member_id = request.get("fleet_member_id")
    principal_id = request.get("principal_id")
    principal = _authority_principal(authority_path, principal_id)
    if not all(isinstance(request.get(k), str) and request[k]
               for k in ("fleet_id", "fleet_member_id", "lease_id")):
        raise auth.AuthorityError("fleet_lease_request_invalid")
    until = request.get("lease_until_ms")
    if not isinstance(until, int) or until <= int(now_ms) or until - int(now_ms) > 24 * 3600 * 1000:
        raise auth.AuthorityError("fleet_lease_expiry_invalid")
    with auth.immediate(con):
        manifest_row, manifest = _load_manifest(con, fleet_id)
        _member(manifest, member_id)
        current = int(manifest_row["generation"])
        expected = request.get("expected_generation")
        if not isinstance(expected, int) or expected < 0:
            raise auth.AuthorityError("fleet_expected_generation_invalid")
        lease = con.execute("""SELECT * FROM fleet_member_leases
                               WHERE fleet_id=? AND fleet_member_id=?""",
                            (fleet_id, member_id)).fetchone()
        active = lease is not None and lease["status"] == "active" \
            and int(lease["lease_until_ms"]) > int(now_ms)
        # expected=current-1 with an active lease is the normal losing side of
        # a concurrent claim.  Older values are an actual rollback attempt.
        if expected < current - (1 if active else 0):
            raise auth.AuthorityError("fleet_generation_rollback", rc=1)
        if active:
            if lease["principal_id"] == principal_id and lease["lease_id"] == request["lease_id"]:
                return auth.result_ok(reason="fleet_member_lease_acquired",
                                      lease=_lease_doc(lease),
                                      manifest_generation=current,
                                      idempotent=True)
            err = auth.AuthorityError("fleet_member_lease_conflict", rc=1)
            err.current_owner_receipt = _owner_receipt(lease)
            raise err
        if expected < current:
            raise auth.AuthorityError("fleet_generation_rollback", rc=1)
        if expected > current:
            raise auth.AuthorityError("fleet_generation_conflict", rc=1)
        generation = current + 1
        con.execute("""INSERT INTO fleet_member_leases
                       (fleet_id,fleet_member_id,lease_id,principal_id,principal_seat_id,
                        principal_session_uuid,generation,lease_until_ms,status,updated_ms)
                       VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(fleet_id,fleet_member_id) DO UPDATE SET
                         lease_id=excluded.lease_id,principal_id=excluded.principal_id,
                         principal_seat_id=excluded.principal_seat_id,
                         principal_session_uuid=excluded.principal_session_uuid,
                         generation=excluded.generation,lease_until_ms=excluded.lease_until_ms,
                         status=excluded.status,updated_ms=excluded.updated_ms""",
                    (fleet_id, member_id, request["lease_id"], principal_id,
                     principal["seat_id"], principal["session_uuid"], generation,
                     until, "active", int(now_ms)))
        manifest["generation"] = generation
        con.execute("UPDATE fleet_manifests SET generation=?,manifest_json=?,updated_ms=? WHERE fleet_id=?",
                    (generation, auth.canonical_bytes(manifest).decode("utf-8"),
                     int(now_ms), fleet_id))
        lease = con.execute("SELECT * FROM fleet_member_leases WHERE fleet_id=? AND fleet_member_id=?",
                            (fleet_id, member_id)).fetchone()
    return auth.result_ok(reason="fleet_member_lease_acquired", lease=_lease_doc(lease),
                          manifest_generation=generation)


def _release(con, authority_path, request, now_ms):
    principal_id = request.get("principal_id")
    _authority_principal(authority_path, principal_id)
    fleet_id = request.get("fleet_id")
    member_id = request.get("fleet_member_id")
    with auth.immediate(con):
        manifest_row, manifest = _load_manifest(con, fleet_id)
        _member(manifest, member_id)
        current = int(manifest_row["generation"])
        expected = request.get("expected_generation")
        if expected < current:
            raise auth.AuthorityError("fleet_generation_rollback", rc=1)
        if expected > current:
            raise auth.AuthorityError("fleet_generation_conflict", rc=1)
        lease = con.execute("SELECT * FROM fleet_member_leases WHERE fleet_id=? AND fleet_member_id=?",
                            (fleet_id, member_id)).fetchone()
        if lease is None or lease["status"] != "active":
            raise auth.AuthorityError("fleet_member_lease_missing", rc=1)
        if lease["principal_id"] != principal_id or lease["lease_id"] != request.get("lease_id"):
            raise auth.AuthorityError("fleet_member_lease_owner_mismatch", rc=1)
        generation = current + 1
        con.execute("""UPDATE fleet_member_leases SET status='released',generation=?,updated_ms=?
                       WHERE fleet_id=? AND fleet_member_id=?""",
                    (generation, int(now_ms), fleet_id, member_id))
        manifest["generation"] = generation
        con.execute("UPDATE fleet_manifests SET generation=?,manifest_json=?,updated_ms=? WHERE fleet_id=?",
                    (generation, auth.canonical_bytes(manifest).decode("utf-8"),
                     int(now_ms), fleet_id))
    return auth.result_ok(reason="fleet_member_lease_released",
                          manifest_generation=generation)


def execute(state_root, authority_path, request, now_ms):
    con = None
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("fleet_request_invalid")
        operation = request.get("operation")
        if operation in _REQUEST_KEYS \
                and set(request) - (_REQUEST_KEYS[operation] | _PROOF_KEYS):
            raise auth.AuthorityError("fleet_request_invalid", rc=1)
        con = auth.open_db(state_root)
        _tables(con)
        if operation == "register_manifest":
            return _register(con, authority_path, request, now_ms)
        if operation == "claim_lease":
            return _claim(con, authority_path, request, now_ms)
        if operation == "release_lease":
            return _release(con, authority_path, request, now_ms)
        raise auth.AuthorityError("fleet_operation_unknown")
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        if hasattr(err, "current_owner_receipt"):
            result["current_owner_receipt"] = err.current_owner_receipt
        return result
    except Exception:
        return auth.result_error(auth.AuthorityError("fleet_internal_error"))
    finally:
        if con is not None:
            con.close()
