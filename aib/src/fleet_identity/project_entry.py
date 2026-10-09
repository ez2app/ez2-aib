#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Resolve a registered project and enter the existing P1A bind/recall flow.

This module is deliberately an orchestrator, not another authority source.
Display names and aliases may select an already registered project, but they
can never create one.  All identity, lease, persona and native-root decisions
remain with the existing P1A modules and their durable receipts.
"""

from __future__ import annotations

import json
import unicodedata

from . import authority_store as auth
from . import bind_apply
from . import bind_plan
from . import fleet_members
from . import onboarding_recall
from . import runtime_model_observation


REQUEST_SCHEMA = "ez2-aib/project-entry-request/v1"
FLEET_RESTORE_PLAN_SCHEMA = "ez2-aib/fleet-restore-launch-plan/v1"
REQUEST_KEYS = frozenset((
    "schema", "operation", "project_query", "requested_role",
    "principal_id", "seat_id", "session_uuid", "cwd", "provider",
    "model", "fleet_size", "task_resume", "expires_at_ms",
))


def _normalize_identity(value):
    """Normalize an entire identity without turning substring similarity in.

    NFKC and case-folding make user-visible aliases predictable while joining
    whitespace only removes display formatting.  The caller still compares
    the complete resulting string for equality.
    """
    if not isinstance(value, str):
        raise auth.AuthorityError("project_entry_query_invalid")
    normalized = " ".join(
        unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized:
        raise auth.AuthorityError("project_entry_query_invalid")
    return normalized


def _validate_request(request, now_ms):
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise auth.AuthorityError("project_entry_request_invalid")
    if request.get("schema") != REQUEST_SCHEMA \
            or request.get("operation") != "resolve_bind_or_onboard":
        raise auth.AuthorityError("project_entry_request_invalid")
    for key in ("requested_role", "principal_id", "seat_id",
                "session_uuid", "cwd", "provider", "model"):
        if not isinstance(request.get(key), str) or not request[key]:
            raise auth.AuthorityError("project_entry_request_invalid")
    if request.get("fleet_size") != 5 or request.get("task_resume") is not False:
        raise auth.AuthorityError("project_entry_request_invalid", rc=1)
    expires = request.get("expires_at_ms")
    if not isinstance(now_ms, int) or not isinstance(expires, int) \
            or expires <= now_ms or expires - now_ms > 5 * 60 * 1000:
        raise auth.AuthorityError("project_entry_expiry_invalid")
    return _normalize_identity(request.get("project_query"))


def _main_principal(authority_path, request):
    registry = auth.load_bootstrap_authority(authority_path)
    row = registry["principals"].get(request["principal_id"])
    if not isinstance(row, dict) or row.get("kind") != "main_ai" \
            or row.get("status") != "active":
        raise auth.AuthorityError("project_entry_main_ai_required", rc=1)
    if row.get("seat_id") != request["seat_id"] \
            or row.get("session_uuid") != request["session_uuid"]:
        raise auth.AuthorityError(
            "project_entry_principal_identity_mismatch", rc=1)


def _registered_matches(con, normalized_query):
    rows = con.execute(
        "SELECT * FROM projects WHERE status='active' ORDER BY project_id"
    ).fetchall()
    matches = []
    for row in rows:
        try:
            project = json.loads(row["project_json"])
        except (TypeError, ValueError):
            raise auth.AuthorityError("project_entry_registry_invalid", rc=1)
        names = [project.get("display_name")] + list(
            project.get("aliases", []))
        normalized_names = {
            _normalize_identity(name) for name in names
            if isinstance(name, str) and name.strip()
        }
        if normalized_query in normalized_names:
            matches.append((row, project))
    return matches


def _manifest(con, project):
    row = con.execute(
        "SELECT * FROM fleet_manifests WHERE fleet_id=?",
        (project.get("fleet_id"),),
    ).fetchone()
    if row is None or row["status"] != "active" \
            or row["project_id"] != project.get("project_id"):
        raise auth.AuthorityError("project_entry_fleet_unresolved", rc=1)
    try:
        manifest = json.loads(row["manifest_json"])
    except (TypeError, ValueError):
        raise auth.AuthorityError("project_entry_fleet_invalid", rc=1)
    if manifest.get("fleet_id") != project.get("fleet_id") \
            or manifest.get("project_id") != project.get("project_id"):
        raise auth.AuthorityError("project_entry_fleet_unresolved", rc=1)
    return manifest


def _member_for_role(manifest, role):
    wanted = _normalize_identity(role)
    matches = []
    for member in manifest.get("members", []):
        identities = {
            _normalize_identity(member.get("role_code")),
            _normalize_identity(member.get("seat_name")),
        }
        if wanted in identities:
            matches.append(member)
    if len(matches) != 1:
        raise auth.AuthorityError(
            "project_entry_member_ambiguous" if matches
            else "project_entry_member_unresolved", rc=1)
    return matches[0]


def _launch_plan(project, manifest, fleet_size):
    members = manifest.get("members")
    if not isinstance(members, list) or len(members) != fleet_size:
        raise auth.AuthorityError("project_entry_fleet_size_mismatch", rc=1)
    seats = []
    roles = set()
    for member in members:
        role = member.get("role_code")
        provider = member.get("preferred_provider")
        if not isinstance(role, str) or not role \
                or not isinstance(provider, str) or not provider \
                or role in roles:
            raise auth.AuthorityError("project_entry_fleet_invalid", rc=1)
        roles.add(role)
        assignment = fleet_members.model_assignment(
            manifest, member.get("fleet_member_id"), provider)
        seats.append({
            "kind": "main_ai",
            "role": role,
            "provider": provider,
            "preferred_provider": provider,
            "provider_fallbacks": list(member.get("provider_fallbacks", [])),
            "requested_model": assignment["requested_model"],
            "allowed_effective_models": list(
                assignment["allowed_effective_models"]),
            "fleet_member_id": member.get("fleet_member_id"),
            "persona_uid": member.get("persona_uid"),
            "memory_binding_ref": member.get("memory_binding_ref"),
        })
    if "root" not in roles:
        raise auth.AuthorityError("project_entry_root_member_missing", rc=1)
    model_policy = manifest.get("model_policy")
    return {
        "schema": FLEET_RESTORE_PLAN_SCHEMA,
        "new_instance": True,
        "dry_run": False,
        "seat_count": fleet_size,
        "project_id": project["project_id"],
        "fleet_id": manifest["fleet_id"],
        "model_policy": model_policy,
        "model_policy_sha256": auth.sha256_hex(
            auth.canonical_bytes(model_policy)),
        "seats": seats,
        # Producing this plan does not itself open a window or seat.
        "state_effect_count": 0,
    }


def _candidate_plans(state_root, authority_path, con, request, project,
                     manifest, member, now_ms):
    assignment = fleet_members.model_assignment(
        manifest, member["fleet_member_id"], request["provider"])
    # ``request.model`` is retained as a compatibility input name, but its
    # semantics are requested_model only.  Runtime truth comes exclusively
    # from the session-bound observation store.
    if request["model"] != assignment["requested_model"]:
        raise auth.AuthorityError(
            "project_entry_requested_model_mismatch", rc=1)
    try:
        observation = runtime_model_observation.current(
            state_root, request["session_uuid"], request["provider"], now_ms)
    except auth.AuthorityError as err:
        # Refresh from the provider-owned session artifact only when the
        # repo-owned head is absent/stale or its referenced artifact has moved
        # on.  A current trusted observation needs no broad home-directory
        # discovery (and remains hermetic when produced by a process-bound
        # collector with an explicit artifact path).
        if err.reason not in {
                "runtime_model_observation_missing",
                "runtime_model_observation_stale",
                "runtime_model_source_not_latest",
        }:
            raise
        try:
            runtime_model_observation.collect(
                state_root, request["provider"], request["session_uuid"],
                now_ms)
        except auth.AuthorityError as collect_err:
            if collect_err.reason != "runtime_model_artifact_missing":
                raise
        observation = runtime_model_observation.current(
            state_root, request["session_uuid"], request["provider"], now_ms)
    effective_model = observation["effective_model"]
    if effective_model not in assignment["allowed_effective_models"]:
        raise auth.AuthorityError(
            "project_entry_effective_model_not_permitted", rc=1)
    rows = con.execute(
        """SELECT * FROM active_native_roots
           WHERE status='active' ORDER BY native_root_id"""
    ).fetchall()
    candidates = []
    failures = []
    for root in rows:
        plan_request = {
            "operation": "create",
            "project_id": project["project_id"],
            "fleet_id": manifest["fleet_id"],
            "fleet_member_id": member["fleet_member_id"],
            "principal_id": request["principal_id"],
            "seat_id": request["seat_id"],
            "session_uuid": request["session_uuid"],
            "cwd": request["cwd"],
            "provider": request["provider"],
            "requested_model": assignment["requested_model"],
            "effective_model": effective_model,
            "model_observation": observation,
            "persona_uid": member["persona_uid"],
            "persona_sha256": member["persona_version"],
            "native_root_id": root["native_root_id"],
            "native_root_receipt_path": root["receipt_path"],
            "root_generation": int(root["root_generation"]),
            "checkpoint_relpath": "checkpoints/%s.json" %
                                  member["fleet_member_id"],
            "task_resume": False,
            "expires_at_ms": request["expires_at_ms"],
        }
        result = bind_plan.execute(
            state_root, authority_path, plan_request, now_ms)
        if result.get("ok"):
            candidates.append(result)
        else:
            failures.append({
                "native_root_id": root["native_root_id"],
                "reason": result.get("reason", "bind_plan_failed"),
            })
    return candidates, failures


def _onboarding_result(match_count=0):
    return auth.result_ok(
        reason="project_entry_onboarding_required",
        match_count=match_count,
        effect_count=0,
        formal_writes=0,
        requires_owner_registration=True,
    )


def _execute(state_root, authority_path, request, now_ms):
    normalized_query = _validate_request(request, now_ms)
    _main_principal(authority_path, request)

    con = auth.open_db(state_root, readonly=True)
    try:
        matches = _registered_matches(con, normalized_query)
        if not matches:
            return _onboarding_result()
        if len(matches) > 1:
            return auth.result_error(auth.AuthorityError(
                "project_entry_ambiguous", rc=1)) | {
                    "match_count": len(matches),
                    "candidate_project_ids": [
                        project["project_id"] for _row, project in matches],
                    "effect_count": 0,
                    "formal_writes": 0,
                }
        project_row, project = matches[0]
        project = dict(project, project_id=project_row["project_id"])
        manifest = _manifest(con, project)
        member = _member_for_role(manifest, request["requested_role"])
        launch_plan = _launch_plan(project, manifest, request["fleet_size"])
        candidates, failures = _candidate_plans(
            state_root, authority_path, con, request, project, manifest,
            member, now_ms)
    finally:
        con.close()

    if not candidates:
        return auth.result_error(auth.AuthorityError(
            "project_entry_binding_sources_unresolved", rc=1)) | {
                "match_count": 1,
                "effect_count": 0,
                "source_failures": failures,
            }
    if len(candidates) > 1:
        return auth.result_error(auth.AuthorityError(
            "project_entry_native_root_ambiguous", rc=1)) | {
                "match_count": 1,
                "root_match_count": len(candidates),
                "effect_count": 0,
            }

    planned = candidates[0]
    applied = bind_apply.execute(state_root, authority_path, {
        "operation": "apply",
        "plan": planned["plan"],
        "capability": planned["capability"],
    }, now_ms)
    if not applied.get("ok"):
        return dict(applied, entry_stage="bind_apply", match_count=1)

    recalled = onboarding_recall.execute(state_root, authority_path, {
        "operation": "read",
        "binding_receipt": applied["receipt"],
        "session_uuid": request["session_uuid"],
        "provider": request["provider"],
        "requested_model": planned["plan"]["requested_model"],
        "effective_model": planned["plan"]["effective_model"],
    }, now_ms)
    if not recalled.get("ok"):
        return dict(
            recalled, entry_stage="onboarding_recall", match_count=1,
            binding=applied["binding"],
            binding_receipt=applied["receipt"],
        )

    return auth.result_ok(
        reason="project_entry_bound_existing_fleet",
        match_count=1,
        project_id=project["project_id"],
        fleet_id=manifest["fleet_id"],
        fleet_member_id=member["fleet_member_id"],
        launch_plan=launch_plan,
        binding=applied["binding"],
        binding_receipt=applied["receipt"],
        read_receipt=recalled["receipt"],
        consumer_refs=recalled["consumer_refs"],
        task_resume=False,
        formal_writes=0,
    )


def execute(state_root, authority_path, request, now_ms):
    try:
        return _execute(state_root, authority_path, request, now_ms)
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        if err.reason.startswith("project_entry_"):
            result.setdefault("effect_count", 0)
        return result
    except Exception:
        return auth.result_error(
            auth.AuthorityError("project_entry_internal_error"))
