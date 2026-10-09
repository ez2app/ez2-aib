#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Closed product callpoint for project intent, status and entry planning.

The natural-language parser is deliberately small.  It recognizes only the
two complete owner-facing utterance shapes frozen by the product outline.  It
does not search substrings, score similarity or infer project identity.

``status`` opens the existing authority database immutable/read-only and
reports registered project/fleet/member references.  ``develop`` delegates
every already-launched main seat to ``project_entry`` and journals each
bind/recall result.  It never opens tmux, creates a seat, resumes a task or
invents another binding authority.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
import unicodedata
from pathlib import Path

from . import authority_store as auth
from . import project_entry
from . import ctx_scheduler
from . import fleet_restore


REQUEST_SCHEMA = "ez2-aib/project-callpoint-request/v1"
_REQUEST_KEYS = {
    "interpret": frozenset(("schema", "operation", "text")),
    "status": frozenset(("schema", "operation", "project_query")),
    "develop": frozenset((
        "schema", "operation", "project_query", "requested_role",
        "principal_id", "seat_id", "session_uuid", "cwd", "provider",
        "model", "expires_at_ms",
    )),
}
_STATUS_RE = re.compile(r"(?P<project>.+?)[ \t]*專案如何\??\Z")
_DEVELOP_RE = re.compile(r"(?P<project>.+?)[ \t]+開發\Z")


class _ClosedArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        del message
        raise auth.AuthorityError("project_cli_request_invalid", rc=2)


def _result_error(reason, rc=1, **fields):
    value = {"ok": False, "reason": str(reason), "rc": int(rc)}
    value.update(fields)
    return value


def _validate_request(request):
    if not isinstance(request, dict):
        raise auth.AuthorityError("project_callpoint_request_invalid")
    operation = request.get("operation")
    keys = _REQUEST_KEYS.get(operation)
    if request.get("schema") != REQUEST_SCHEMA or keys is None \
            or set(request) != keys:
        raise auth.AuthorityError("project_callpoint_request_invalid")
    return operation


def _text(value):
    if not isinstance(value, str):
        raise auth.AuthorityError("project_intent_text_invalid")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise auth.AuthorityError("project_intent_text_invalid")
    return normalized


def _parse_intent(value):
    normalized = _text(value)
    for intent, pattern in (
            ("read_status", _STATUS_RE),
            ("develop_plan", _DEVELOP_RE)):
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        project_query = match.group("project").strip()
        if project_query:
            return auth.result_ok(
                reason="project_intent_interpreted",
                receipt={
                    "schema": "ez2-aib/project-intent-receipt/v1",
                    "intent": intent,
                    "project_query": project_query,
                    "task_resume": False,
                    "fleet_launch_applied": False,
                    "effect_count": 0,
                },
                effect_count=0,
                formal_writes=0,
            )
    return _result_error(
        "project_intent_unrecognized", rc=1, effect_count=0,
        formal_writes=0)


def _project_matches(con, query):
    normalized = project_entry._normalize_identity(query)
    return project_entry._registered_matches(con, normalized)


def _active_binding_refs(con, project_id, fleet_id):
    try:
        rows = con.execute(
            """SELECT fleet_member_id,binding_id,generation,status
               FROM active_bindings WHERE project_id=? AND fleet_id=?
               ORDER BY fleet_member_id""",
            (project_id, fleet_id),
        ).fetchall()
    except Exception as exc:
        # A missing/corrupt authority table is not equivalent to no binding.
        raise auth.AuthorityError(
            "project_status_binding_registry_unavailable", rc=1) from exc
    return {
        row["fleet_member_id"]: {
            "binding_id": row["binding_id"],
            "generation": int(row["generation"]),
            "status": row["status"],
        }
        for row in rows
    }


def _status(state_root, query):
    if not isinstance(state_root, (str, os.PathLike)) \
            or not Path(state_root).is_absolute():
        raise auth.AuthorityError("project_status_state_root_invalid")
    con = auth.open_db(state_root, readonly=True)
    try:
        matches = _project_matches(con, query)
        if not matches:
            return _result_error(
                "project_status_not_registered", rc=1, match_count=0,
                effect_count=0, formal_writes=0)
        if len(matches) != 1:
            return _result_error(
                "project_status_ambiguous", rc=1,
                match_count=len(matches),
                candidate_project_ids=[
                    project["project_id"] for _row, project in matches],
                effect_count=0, formal_writes=0)
        project_row, project = matches[0]
        project = dict(project, project_id=project_row["project_id"])
        manifest = project_entry._manifest(con, project)
        binding_refs = _active_binding_refs(
            con, project["project_id"], manifest["fleet_id"])

        missing = []
        members = manifest.get("members")
        if not isinstance(members, list):
            raise auth.AuthorityError("project_status_fleet_invalid", rc=1)
        if len(members) != 5:
            missing.append({
                "kind": "fleet_member_count",
                "expected": 5,
                "observed": len(members),
            })
        output_members = []
        seen_roles = set()
        for index, member in enumerate(members):
            member_id = member.get("fleet_member_id")
            role = member.get("role_code")
            for field in (
                    "fleet_member_id", "role_code", "seat_name",
                    "preferred_provider", "persona_uid", "persona_version",
                    "memory_binding_ref", "memory_scope_id"):
                if not isinstance(member.get(field), str) or not member[field]:
                    missing.append({
                        "kind": "member_field",
                        "member_index": index,
                        "fleet_member_id": member_id,
                        "field": field,
                    })
            if role in seen_roles:
                missing.append({
                    "kind": "duplicate_role",
                    "fleet_member_id": member_id,
                    "role_code": role,
                })
            seen_roles.add(role)
            active = binding_refs.get(member_id)
            if active is None:
                missing.append({
                    "kind": "active_seat_binding",
                    "fleet_member_id": member_id,
                })
            output_members.append({
                "kind": "main_ai",
                "fleet_member_id": member_id,
                "role_code": role,
                "seat_name": member.get("seat_name"),
                "preferred_provider": member.get("preferred_provider"),
                "persona_ref": {
                    "persona_uid": member.get("persona_uid"),
                    "persona_version": member.get("persona_version"),
                },
                "native_binding_ref": {
                    "memory_binding_ref": member.get("memory_binding_ref"),
                    "memory_scope_id": member.get("memory_scope_id"),
                },
                "active_seat_binding_ref": active,
            })
        if "root" not in seen_roles:
            missing.append({"kind": "required_role", "role_code": "root"})

        receipt = {
            "schema": "ez2-aib/project-status-receipt/v1",
            "intent": "read_status",
            "match_kind": "exact_registered_identity",
            "project": {
                "project_id": project["project_id"],
                "generation": int(project_row["generation"]),
                "display_name": project.get("display_name"),
                "aliases": list(project.get("aliases", [])),
                "outline_ref": project.get("outline_ref"),
                "fleet_id": project.get("fleet_id"),
                "memory_scope_id": project.get("memory_scope_id"),
                "memory_schema_version": project.get(
                    "memory_schema_version"),
                "memory_store_ref": project.get("memory_store_ref"),
            },
            "fleet": {
                "fleet_id": manifest["fleet_id"],
                "generation": int(manifest.get("generation", 0)),
                "captain_member_id": manifest.get("captain_member_id"),
                "manager_member_id": manifest.get("manager_member_id"),
                "member_count": len(output_members),
            },
            "members": output_members,
            "missing": missing,
            "task_resume": False,
            "fleet_launch_applied": False,
            "effect_count": 0,
            "formal_writes": 0,
        }
        return auth.result_ok(
            reason="project_status_resolved", receipt=receipt,
            effect_count=0, formal_writes=0)
    finally:
        con.close()


def _canonical_five_main_plan(value):
    return fleet_restore._valid_plan(value)


def _develop(state_root, authority_path, request, now_ms):
    if not isinstance(state_root, (str, os.PathLike)) \
            or not Path(state_root).is_absolute():
        raise auth.AuthorityError("project_develop_state_root_invalid")
    if not isinstance(authority_path, (str, os.PathLike)) \
            or not Path(authority_path).is_absolute():
        raise auth.AuthorityError("project_develop_authority_path_invalid")
    for key in (
            "project_query", "requested_role", "principal_id", "seat_id",
            "session_uuid", "cwd", "provider", "model"):
        if not isinstance(request.get(key), str) or not request[key]:
            raise auth.AuthorityError("project_develop_request_invalid")
    if not Path(request["cwd"]).is_absolute() \
            or not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise auth.AuthorityError("project_develop_request_invalid")
    expires = request.get("expires_at_ms")
    if not isinstance(expires, int) or isinstance(expires, bool):
        raise auth.AuthorityError("project_develop_request_invalid")

    restored = fleet_restore.consume(
        Path(state_root), Path(authority_path), request, now_ms)
    if restored is not None:
        return restored

    # Preserve the typed onboarding/ambiguous result when no unique
    # registered project can produce a restore plan.
    entry_request = fleet_restore._entry_request(request, {
        "role": request["requested_role"],
        "provider": request["provider"],
    }, {
        "principal_id": request["principal_id"],
        "seat_id": request["seat_id"],
        "session_uuid": request["session_uuid"],
        "provider": request["provider"],
        "requested_model": request["model"],
        "cwd": request["cwd"],
    })
    entry_receipt = project_entry.execute(
        Path(state_root), Path(authority_path), entry_request, now_ms)
    plan = entry_receipt.get("launch_plan")
    if entry_receipt.get("ok") and plan is not None \
            and not _canonical_five_main_plan(plan):
        return _result_error(
            "project_develop_launch_plan_invalid", rc=1,
            entry_receipt=entry_receipt, task_resume=False,
            fleet_launch_applied=False, formal_writes=0)
    return {
        "ok": bool(entry_receipt.get("ok")),
        "reason": str(entry_receipt.get(
            "reason", "project_entry_internal_error")),
        "rc": int(entry_receipt.get("rc", 2)),
        "schema": "ez2-aib/project-develop-receipt/v1",
        "intent": "develop_plan",
        # Exact nested value: callers can verify the existing P1A receipt
        # without this callpoint rewriting or laundering any of its fields.
        "entry_receipt": entry_receipt,
        "launch_plan": plan,
        "task_resume": False,
        "fleet_launch_applied": False,
        "formal_writes": 0,
    }


def execute(state_root, authority_path, request, now_ms):
    """Execute one closed request and always return a typed result."""
    try:
        operation = _validate_request(request)
        if operation == "interpret":
            return _parse_intent(request["text"])
        if operation == "status":
            return _status(state_root, request["project_query"])
        if operation == "develop":
            return _develop(state_root, authority_path, request, now_ms)
        raise auth.AuthorityError("project_callpoint_operation_unknown")
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        result.setdefault("effect_count", 0)
        result.setdefault("formal_writes", 0)
        return result
    except Exception:
        return _result_error(
            "project_callpoint_internal_error", rc=2,
            effect_count=0, formal_writes=0)


def _emit(result):
    stream = sys.stdout if result.get("ok") else sys.stderr
    stream.write(json.dumps(
        result, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False) + "\n")
    return int(result.get("rc", 2))


def _parser():
    parser = _ClosedArgumentParser(prog="aib project")
    sub = parser.add_subparsers(
        dest="command", required=True, parser_class=_ClosedArgumentParser)
    interpret = sub.add_parser("interpret")
    interpret.add_argument("--text", required=True)
    status = sub.add_parser("status")
    status.add_argument("--state-root", required=True)
    status.add_argument("--project", required=True)
    develop = sub.add_parser("develop")
    develop.add_argument("--state-root", required=True)
    develop.add_argument("--authority", required=True)
    develop.add_argument("--project", required=True)
    develop.add_argument("--requested-role", required=True)
    develop.add_argument("--principal-id", required=True)
    develop.add_argument("--seat-id", required=True)
    develop.add_argument("--session-uuid", required=True)
    develop.add_argument("--cwd", required=True)
    develop.add_argument("--provider", required=True)
    develop.add_argument("--model", required=True)
    develop.add_argument("--expires-at-ms", required=True, type=int)
    schedule = sub.add_parser("ctx-schedule")
    schedule.add_argument("--state-root", required=True)
    schedule.add_argument("--authority", required=True)
    schedule.add_argument("--request", required=True)
    schedule.add_argument("--now-ms", required=True, type=int)
    return parser


def _main(argv=None):
    try:
        args = _parser().parse_args(argv)
        now_ms = int(time.time() * 1000)
        if args.command == "interpret":
            request = {
                "schema": REQUEST_SCHEMA,
                "operation": "interpret",
                "text": args.text,
            }
            return _emit(execute(None, None, request, now_ms))
        if args.command == "status":
            request = {
                "schema": REQUEST_SCHEMA,
                "operation": "status",
                "project_query": args.project,
            }
            return _emit(execute(
                Path(args.state_root), None, request, now_ms))
        if args.command == "develop":
            request = {
                "schema": REQUEST_SCHEMA,
                "operation": "develop",
                "project_query": args.project,
                "requested_role": args.requested_role,
                "principal_id": args.principal_id,
                "seat_id": args.seat_id,
                "session_uuid": args.session_uuid,
                "cwd": args.cwd,
                "provider": args.provider,
                "model": args.model,
                "expires_at_ms": args.expires_at_ms,
            }
            return _emit(execute(
                Path(args.state_root), Path(args.authority), request, now_ms))
        if args.command == "ctx-schedule":
            try:
                request_path = Path(args.request)
                info = request_path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                        or request_path.is_symlink():
                    raise OSError("unsafe request file")
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                return _emit(_result_error(
                    "ctx_scheduler_request_invalid", rc=2))
            return _emit(ctx_scheduler.execute(
                Path(args.state_root), Path(args.authority), request,
                args.now_ms))
        raise auth.AuthorityError("project_cli_request_invalid")
    except auth.AuthorityError as err:
        return _emit(auth.result_error(err))
    except KeyboardInterrupt:
        return _emit(_result_error("project_callpoint_interrupted", rc=1))


if __name__ == "__main__":
    raise SystemExit(_main())
