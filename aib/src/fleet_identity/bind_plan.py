#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a single compatible, non-authorizing persona/brain binding plan."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import authority_store as auth
from . import binding_store as bs
from . import fleet_members
from . import native_root
from . import runtime_model_observation


CAPABILITY_KEYS = frozenset((
    "schema", "capability_id", "plan_sha256", "issuer_principal_id",
    "issued_at_ms", "expires_at_ms", "formal_approval", "authority_grade",
))
CREATE_REQUEST_KEYS = frozenset((
    "operation", "project_id", "fleet_id", "fleet_member_id", "principal_id",
    "seat_id", "session_uuid", "cwd", "provider", "requested_model",
    "effective_model", "model_observation", "persona_uid",
    "persona_sha256", "native_root_id", "native_root_receipt_path",
    "root_generation", "checkpoint_relpath", "task_resume", "expires_at_ms",
))
VERIFY_REQUEST_KEYS = frozenset(("operation", "plan", "capability"))


def _principal(authority_path, principal_id, seat_id, session_uuid):
    registry = auth.load_bootstrap_authority(authority_path)
    row = registry["principals"].get(principal_id)
    if not isinstance(row, dict) or row.get("kind") != "main_ai" \
            or row.get("status") != "active":
        raise auth.AuthorityError("bind_principal_not_main_ai", rc=1)
    if row.get("seat_id") != seat_id or row.get("session_uuid") != session_uuid:
        raise auth.AuthorityError("bind_principal_identity_mismatch", rc=1)
    return row


def _project(con, project_id):
    row = con.execute("SELECT * FROM projects WHERE project_id=?",
                      (project_id,)).fetchone()
    if row is None:
        raise auth.AuthorityError("bind_project_unresolved", rc=1)
    if row["status"] != "active":
        raise auth.AuthorityError("bind_plan_project_revoked", rc=1)
    try:
        project = json.loads(row["project_json"])
    except (TypeError, ValueError):
        raise auth.AuthorityError("bind_project_state_invalid", rc=1)
    return row, project


def _cwd(con, project_id, project, cwd):
    if not isinstance(cwd, str) or not os.path.isabs(cwd) \
            or os.path.normpath(cwd) != cwd:
        raise auth.AuthorityError("bind_project_cwd_mismatch", rc=1)
    candidate_path = Path(cwd)
    try:
        candidate = candidate_path.resolve(strict=True)
        candidate_path.lstat()
    except (OSError, RuntimeError):
        raise auth.AuthorityError("bind_project_cwd_mismatch", rc=1)
    if candidate_path.is_symlink() or str(candidate) != cwd:
        raise auth.AuthorityError("bind_project_cwd_mismatch", rc=1)
    pins = con.execute(
        """SELECT * FROM project_repo_roots WHERE project_id=?
           AND status='active' ORDER BY root_path""", (project_id,),
    ).fetchall()
    if len(pins) != len(project.get("repo_roots", [])):
        raise auth.AuthorityError("bind_project_cwd_mismatch", rc=1)
    for pin in pins:
        raw = pin["root_path"]
        try:
            root_path = Path(raw)
            if root_path.is_symlink():
                continue
            root = root_path.resolve(strict=True)
            info = root_path.lstat()
        except (OSError, RuntimeError):
            continue
        if str(root) != pin["root_realpath"] \
                or int(info.st_dev) != int(pin["root_dev"]) \
                or int(info.st_ino) != int(pin["root_ino"]):
            continue
        if candidate == root or root in candidate.parents:
            relative = candidate.relative_to(root)
            cursor = root
            safe = True
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    safe = False
                    break
            if not safe:
                continue
            return str(candidate)
    raise auth.AuthorityError("bind_project_cwd_mismatch", rc=1)


def _fleet_member(con, fleet_id, member_id, principal_id, now_ms, *,
                  stale_reason="bind_member_lease_stale"):
    row = con.execute("SELECT * FROM fleet_manifests WHERE fleet_id=?",
                      (fleet_id,)).fetchone()
    if row is None or row["status"] != "active":
        raise auth.AuthorityError("bind_fleet_unresolved", rc=1)
    manifest = bs.json_load(row["manifest_json"], "bind_fleet_state_invalid")
    member = next((item for item in manifest.get("members", [])
                   if item.get("fleet_member_id") == member_id), None)
    if member is None:
        raise auth.AuthorityError("bind_member_identity_unresolved", rc=1)
    lease = con.execute(
        """SELECT * FROM fleet_member_leases WHERE fleet_id=?
           AND fleet_member_id=?""", (fleet_id, member_id),
    ).fetchone()
    if lease is None or lease["status"] != "active" \
            or int(lease["lease_until_ms"]) <= int(now_ms) \
            or lease["principal_id"] != principal_id:
        raise auth.AuthorityError(stale_reason, rc=1)
    return row, manifest, member, lease


def _persona(con, uid, digest, *, stale_reason="bind_persona_bytes_stale"):
    row = con.execute("SELECT * FROM published_personas WHERE persona_uid=?",
                      (uid,)).fetchone()
    if row is None or row["lifecycle"] != "published":
        raise auth.AuthorityError("bind_persona_not_published", rc=1)
    if row["persona_sha256"] != digest:
        raise auth.AuthorityError("bind_persona_sha_mismatch", rc=1)
    _raw, current = bs.file_fingerprint(
        row["source_path"], "bind_persona_bytes_unreadable")
    if current != row["persona_sha256"]:
        raise auth.AuthorityError(stale_reason, rc=1)
    return row


def _checkpoint(root_path, relpath, project_id, member_id):
    if not isinstance(relpath, str) or not relpath or os.path.isabs(relpath) \
            or os.path.normpath(relpath) != relpath \
            or relpath.startswith("../"):
        raise auth.AuthorityError("bind_checkpoint_path_invalid", rc=1)
    root = Path(root_path)
    target = root / relpath
    try:
        resolved = target.resolve(strict=True)
        root_real = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise auth.AuthorityError("bind_checkpoint_unreadable", rc=1)
    if root_real not in resolved.parents or target.is_symlink():
        raise auth.AuthorityError("bind_checkpoint_path_invalid", rc=1)
    raw, digest = bs.file_fingerprint(resolved, "bind_checkpoint_unreadable")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise auth.AuthorityError("bind_checkpoint_invalid", rc=1)
    if not isinstance(doc, dict) \
            or doc.get("schema") != "ez2-native-checkpoint/v1" \
            or doc.get("project_id") != project_id \
            or doc.get("fleet_member_id") != member_id \
            or not isinstance(doc.get("generation"), int) \
            or doc["generation"] < 1:
        raise auth.AuthorityError("bind_checkpoint_identity_mismatch", rc=1)
    return str(resolved), raw, digest, int(doc["generation"])


def _root(con, receipt_path, root_id, root_generation, now_ms):
    if not isinstance(receipt_path, str):
        raise auth.AuthorityError("bind_active_root_not_current", rc=1)
    try:
        receipt = native_root._read_receipt(receipt_path)
    except auth.AuthorityError:
        raise auth.AuthorityError("bind_active_root_not_current", rc=1)
    row = con.execute(
        "SELECT * FROM active_native_roots WHERE native_root_id=?", (root_id,),
    ).fetchone()
    if row is None or row["receipt_path"] != receipt_path \
            or receipt.get("native_root_id") != root_id \
            or receipt.get("generation") != int(row["receipt_generation"]) \
            or receipt.get("receipt_sha256") != row["receipt_sha256"]:
        raise auth.AuthorityError("bind_active_root_not_current", rc=1)
    if row["status"] != "active" or receipt.get("status") != "active":
        raise auth.AuthorityError("bind_plan_root_revoked", rc=1)
    if int(row["expires_at_ms"]) <= int(now_ms):
        raise auth.AuthorityError("bind_plan_root_expired", rc=1)
    try:
        current_liveness = native_root._live_writer(
            row["liveness_path"], row["producer_id"],
            int(row["producer_generation"]), now_ms)
    except auth.AuthorityError:
        raise auth.AuthorityError("bind_plan_root_writer_not_live", rc=1)
    if current_liveness != receipt.get("liveness_sha256"):
        raise auth.AuthorityError("bind_plan_root_writer_not_live", rc=1)
    if root_generation != int(row["root_generation"]):
        raise auth.AuthorityError("bind_active_root_generation_mismatch", rc=1)
    try:
        current_root_hash = native_root._root_hash(Path(row["root_path"]))
    except auth.AuthorityError:
        raise auth.AuthorityError("bind_plan_root_bytes_stale", rc=1)
    if current_root_hash != row["root_sha256"]:
        raise auth.AuthorityError("bind_plan_root_bytes_stale", rc=1)
    return row, receipt


def inspect_sources(state_root, con, authority_path, values, now_ms, *,
                    plan=False, observation_lock_held=False):
    """Read and validate the complete binding read-set.

    ``values`` is a create request when ``plan=False`` and a frozen plan when
    true.  The returned rows are used by both plan creation and atomic apply.
    """
    project_id = values.get("project_id")
    fleet_id = values.get("fleet_id")
    member_id = values.get("fleet_member_id")
    principal_id = values.get("principal_id")
    seat_id = values.get("seat_id")
    session_uuid = values.get("session_uuid")
    _principal(authority_path, principal_id, seat_id, session_uuid)
    project_row, project = _project(con, project_id)
    if project.get("fleet_id") != fleet_id:
        raise auth.AuthorityError("bind_project_fleet_mismatch", rc=1)
    cwd = _cwd(con, project_id, project, values.get("cwd"))
    fleet_row, manifest, member, lease = _fleet_member(
        con, fleet_id, member_id, principal_id, now_ms,
        stale_reason=("bind_plan_member_lease_stale" if plan
                      else "bind_member_lease_stale"))
    if manifest.get("project_id") != project_id \
            or fleet_row["project_id"] != project_id:
        raise auth.AuthorityError("bind_project_fleet_mismatch", rc=1)
    if lease["principal_seat_id"] != seat_id \
            or lease["principal_session_uuid"] != session_uuid:
        raise auth.AuthorityError("bind_member_lease_stale", rc=1)
    provider = values.get("provider")
    allowed = [member.get("preferred_provider")] + list(
        member.get("provider_fallbacks", []))
    if provider not in allowed:
        raise auth.AuthorityError("bind_provider_not_permitted", rc=1)
    assignment = fleet_members.model_assignment(manifest, member_id, provider)
    policy_sha256 = auth.sha256_hex(
        auth.canonical_bytes(manifest["model_policy"]))
    assignment_sha256 = auth.sha256_hex(auth.canonical_bytes(assignment))
    requested_model = values.get("requested_model")
    effective_model = values.get("effective_model")
    if requested_model != assignment["requested_model"]:
        raise auth.AuthorityError("bind_requested_model_not_permitted", rc=1)
    if not isinstance(effective_model, str) or not effective_model \
            or effective_model not in assignment["allowed_effective_models"]:
        raise auth.AuthorityError("bind_effective_model_not_permitted", rc=1)
    observation = runtime_model_observation.verify(
        state_root,
        values.get("model_observation"), session_uuid, provider, now_ms)
    if observation["effective_model"] != effective_model:
        raise auth.AuthorityError("bind_model_observation_mismatch", rc=1)
    newest = (runtime_model_observation.current_unlocked
              if observation_lock_held
              else runtime_model_observation.current)(
                  state_root, session_uuid, provider, now_ms)
    if newest["observation_sha256"] != observation["observation_sha256"]:
        raise auth.AuthorityError("bind_model_observation_not_current", rc=1)
    persona_uid = values.get("persona_uid")
    persona_sha = values.get("persona_sha256")
    if persona_uid != member.get("persona_uid"):
        raise auth.AuthorityError("bind_persona_not_published", rc=1)
    if persona_sha != member.get("persona_version"):
        raise auth.AuthorityError("bind_persona_sha_mismatch", rc=1)
    persona_row = _persona(
        con, persona_uid, persona_sha,
        stale_reason=("bind_plan_persona_bytes_stale" if plan
                      else "bind_persona_bytes_stale"))
    root_row, root_receipt = _root(
        con, values.get("native_root_receipt_path"),
        values.get("native_root_id"), values.get("root_generation"), now_ms)
    if project.get("memory_scope_id") != member.get("memory_scope_id"):
        raise auth.AuthorityError("bind_memory_scope_mismatch", rc=1)
    checkpoint_path, checkpoint_raw, checkpoint_sha, checkpoint_generation = \
        _checkpoint(root_row["root_path"], values.get("checkpoint_relpath"),
                    project_id, member_id)
    if values.get("task_resume") is not False:
        raise auth.AuthorityError("bind_task_resume_forbidden", rc=1)
    facts = {
        "project_row": project_row, "project": project,
        "fleet_row": fleet_row, "manifest": manifest, "member": member,
        "lease": lease, "persona_row": persona_row, "root_row": root_row,
        "root_receipt": root_receipt, "checkpoint_path": checkpoint_path,
        "checkpoint_raw": checkpoint_raw,
        "checkpoint_sha256": checkpoint_sha,
        "checkpoint_generation": checkpoint_generation, "cwd": cwd,
        "model_assignment": assignment, "model_observation": observation,
        "model_policy_sha256": policy_sha256,
        "model_assignment_sha256": assignment_sha256,
        "fleet_member_sha256": auth.sha256_hex(
            auth.canonical_bytes(member)),
    }
    if plan:
        generations = values.get("generations")
        expected = {
            "project": int(project_row["generation"]),
            "fleet": int(fleet_row["generation"]),
            "persona_catalog": int(persona_row["catalog_generation"]),
            "active_root_receipt": int(root_row["receipt_generation"]),
            "native_root": int(root_row["root_generation"]),
        }
        if not isinstance(generations, dict):
            raise auth.AuthorityError("bind_plan_tampered", rc=1)
        if generations.get("project") != expected["project"]:
            raise auth.AuthorityError("bind_plan_project_generation_stale", rc=1)
        if generations.get("fleet") != expected["fleet"] \
                or values.get("lease_generation") != int(lease["generation"]) \
                or values.get("lease_id") != lease["lease_id"]:
            raise auth.AuthorityError("bind_plan_member_lease_stale", rc=1)
        if generations.get("persona_catalog") != expected["persona_catalog"]:
            raise auth.AuthorityError("bind_plan_persona_generation_stale", rc=1)
        if generations.get("active_root_receipt") != \
                expected["active_root_receipt"]:
            raise auth.AuthorityError("bind_plan_root_generation_stale", rc=1)
        if generations.get("native_root") != expected["native_root"]:
            raise auth.AuthorityError("bind_plan_root_generation_stale", rc=1)
        if values.get("checkpoint_sha256") != checkpoint_sha:
            raise auth.AuthorityError("bind_plan_root_bytes_stale", rc=1)
        if values.get("persona_source_path") != persona_row["source_path"]:
            raise auth.AuthorityError("bind_plan_persona_bytes_stale", rc=1)
        if values.get("checkpoint_path") != checkpoint_path:
            raise auth.AuthorityError("bind_plan_root_bytes_stale", rc=1)
        if values.get("model_policy_sha256") != policy_sha256 \
                or values.get("model_assignment_sha256") != assignment_sha256:
            raise auth.AuthorityError("bind_plan_model_policy_stale", rc=1)
        if values.get("fleet_member_sha256") != auth.sha256_hex(
                auth.canonical_bytes(member)) \
                or values.get("role_code") != member.get("role_code") \
                or values.get("task_lane") != member.get("task_lane"):
            raise auth.AuthorityError("bind_plan_member_state_stale", rc=1)
        if values.get("native_root_receipt_sha256") != \
                root_row["receipt_sha256"] \
                or values.get("root_sha256") != root_row["root_sha256"]:
            raise auth.AuthorityError("bind_plan_root_bytes_stale", rc=1)
    return facts


def _create(state_root, authority_path, request, now_ms):
    expires = request.get("expires_at_ms")
    if not isinstance(expires, int) or expires <= int(now_ms) \
            or expires - int(now_ms) > 5 * 60 * 1000:
        raise auth.AuthorityError("bind_plan_expiry_invalid")
    con = auth.open_db(state_root, readonly=True)
    try:
        facts = inspect_sources(
            state_root, con, authority_path, request, now_ms)
        plan = {
                "schema": "ez2-aib/persona-brain-bind-plan/v1",
                "project_id": request["project_id"],
                "fleet_id": request["fleet_id"],
                "fleet_member_id": request["fleet_member_id"],
                "principal_id": request["principal_id"],
                "seat_id": request["seat_id"],
                "session_uuid": request["session_uuid"],
                "cwd": facts["cwd"],
                "provider": request["provider"],
                "requested_model": request["requested_model"],
                "effective_model": request["effective_model"],
                "model_observation": facts["model_observation"],
                "model_policy_sha256": facts["model_policy_sha256"],
                "model_assignment_sha256": facts[
                    "model_assignment_sha256"],
                "fleet_member_sha256": facts["fleet_member_sha256"],
                "role_code": facts["member"]["role_code"],
                "task_lane": facts["member"]["task_lane"],
                "persona_uid": request["persona_uid"],
                "persona_sha256": request["persona_sha256"],
                "persona_source_path": facts["persona_row"]["source_path"],
                "native_root_id": request["native_root_id"],
                "native_root_receipt_path": request["native_root_receipt_path"],
                "native_root_receipt_sha256": facts["root_row"][
                    "receipt_sha256"],
                "root_path": facts["root_row"]["root_path"],
                "root_sha256": facts["root_row"]["root_sha256"],
                "root_generation": request["root_generation"],
                "checkpoint_relpath": request["checkpoint_relpath"],
                "checkpoint_path": facts["checkpoint_path"],
                "checkpoint_sha256": facts["checkpoint_sha256"],
                "checkpoint_generation": facts["checkpoint_generation"],
                "memory_scope_id": facts["project"]["memory_scope_id"],
                "memory_store_ref": facts["project"]["memory_store_ref"],
                "memory_schema_version": facts["project"]["memory_schema_version"],
                "lease_id": facts["lease"]["lease_id"],
                "lease_generation": int(facts["lease"]["generation"]),
                "lease_until_ms": int(facts["lease"]["lease_until_ms"]),
                "generations": {
                    "project": int(facts["project_row"]["generation"]),
                    "fleet": int(facts["fleet_row"]["generation"]),
                    "persona_catalog": int(
                        facts["persona_row"]["catalog_generation"]),
                    "active_root_receipt": int(
                        facts["root_row"]["receipt_generation"]),
                    "native_root": int(facts["root_row"]["root_generation"]),
                },
                "task_resume": False,
                "issued_at_ms": int(now_ms),
                "expires_at_ms": expires,
                "truth_ceiling": "bootstrap_registry",
        }
        plan["plan_sha256"] = bs.hash_value(plan)
        capability_body = {
                "schema": "ez2-aib/local-bind-capability/v1",
                "capability_id": bs.new_id("bind-cap"),
                "plan_sha256": plan["plan_sha256"],
                "issuer_principal_id": request["principal_id"],
                "issued_at_ms": int(now_ms), "expires_at_ms": expires,
                "formal_approval": False,
                "authority_grade": "local_bind_capability",
        }
        capability = bs.sign_capability(state_root, capability_body)
        return auth.result_ok(reason="bind_plan_created", plan=plan,
                              capability=capability)
    finally:
        con.close()


def verify_plan(state_root, authority_path, con, plan, capability, now_ms,
                *, allow_prepared=False, observation_lock_held=False):
    if not isinstance(plan, dict) or not isinstance(plan.get("plan_sha256"), str):
        raise auth.AuthorityError("bind_plan_tampered", rc=1)
    body = {name: value for name, value in plan.items() if name != "plan_sha256"}
    if bs.hash_value(body) != plan["plan_sha256"]:
        raise auth.AuthorityError("bind_plan_tampered", rc=1)
    cap = bs.verify_capability(
        state_root, capability, CAPABILITY_KEYS, "bind_capability_tampered")
    if cap["formal_approval"] is not False \
            or cap["authority_grade"] != "local_bind_capability" \
            or cap["plan_sha256"] != plan["plan_sha256"] \
            or cap["issuer_principal_id"] != plan.get("principal_id"):
        raise auth.AuthorityError("bind_capability_tampered", rc=1)
    if not isinstance(cap["expires_at_ms"], int) \
            or cap["expires_at_ms"] <= int(now_ms) \
            or cap["expires_at_ms"] != plan.get("expires_at_ms"):
        raise auth.AuthorityError("bind_capability_expired", rc=1)
    has_plans = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bind_plans'"
    ).fetchone()
    row = None if has_plans is None else con.execute(
        "SELECT * FROM bind_plans WHERE plan_sha256=?",
        (plan["plan_sha256"],),
    ).fetchone()
    if row is not None:
        if row["plan_json"] != auth.canonical_bytes(plan).decode("utf-8") \
                or row["capability_id"] != cap["capability_id"] \
                or row["capability_hash"] != auth.sha256_hex(
                    auth.canonical_bytes(capability).decode("utf-8")):
            raise auth.AuthorityError("bind_capability_tampered", rc=1)
        if row["capability_status"] == "consumed":
            raise auth.AuthorityError("bind_capability_replayed", rc=1)
        if row["capability_status"] == "abandoned":
            raise auth.AuthorityError("bind_capability_abandoned", rc=1)
        if row["capability_status"] == "prepared" and not allow_prepared:
            raise auth.AuthorityError("bind_capability_prepared", rc=1)
    inspect_sources(
        state_root, con, authority_path, plan, now_ms, plan=True,
        observation_lock_held=observation_lock_held)
    return row, cap


def execute(state_root, authority_path, request, now_ms):
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("bind_plan_request_invalid")
        operation = request.get("operation")
        if operation == "create":
            if set(request) - CREATE_REQUEST_KEYS:
                raise auth.AuthorityError("bind_plan_request_invalid")
            return _create(state_root, authority_path, request, now_ms)
        if operation == "verify":
            if set(request) - VERIFY_REQUEST_KEYS:
                raise auth.AuthorityError("bind_plan_request_invalid")
            con = auth.open_db(state_root, readonly=True)
            try:
                verify_plan(state_root, authority_path, con,
                            request.get("plan"), request.get("capability"),
                            now_ms, allow_prepared=True)
                return auth.result_ok(reason="bind_plan_verified")
            finally:
                con.close()
        raise auth.AuthorityError("bind_plan_operation_unknown")
    except auth.AuthorityError as err:
        return auth.result_error(err)
    except Exception:
        return auth.result_error(auth.AuthorityError("bind_plan_internal_error"))
