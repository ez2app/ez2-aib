#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Owner-governed project registry with CAS, revocation and legacy import."""

import json
import os
import stat
import subprocess
from pathlib import Path

try:
    from . import authority_store as auth
except ImportError:  # pragma: no cover - direct script compatibility
    import authority_store as auth


REQUEST_SCHEMA = "ez2-aib/project-registry-request/v1"
PROJECT_KEYS = frozenset((
    "project_id", "display_name", "aliases", "repo_remotes", "repo_roots",
    "outline_ref", "fleet_id", "memory_scope_id", "memory_schema_version",
    "memory_store_ref", "status",
))
_PROOF_KEYS = frozenset((
    "schema", "approval_id", "authority_proof", "owner_approval",
    "main_approval",
))
_REQUEST_KEYS = {
    "create": frozenset(("operation", "expected_generation", "project")),
    "import_legacy_cwd": frozenset((
        "operation", "expected_generation", "legacy_cwd", "project",
    )),
    "update": frozenset((
        "operation", "project_id", "expected_generation", "patch",
    )),
    "revoke": frozenset((
        "operation", "project_id", "expected_generation", "reason_code",
    )),
    "resolve": frozenset(("operation", "project_id")),
}


def _tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS projects(
      project_id TEXT PRIMARY KEY,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL,
      project_json TEXT NOT NULL,
      owner_id TEXT NOT NULL,
      material_hash TEXT NOT NULL,
      updated_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS project_repo_roots(
      project_id TEXT NOT NULL,
      root_path TEXT NOT NULL,
      root_realpath TEXT NOT NULL,
      root_dev INTEGER NOT NULL,
      root_ino INTEGER NOT NULL,
      project_generation INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','revoked')),
      PRIMARY KEY(project_id,root_path)
    );
    """)


def _has_symlink_component(path):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _pin_repo_roots(project):
    pins = []
    for raw in project["repo_roots"]:
        if not os.path.isabs(raw) or os.path.normpath(raw) != raw:
            raise auth.AuthorityError("project_repo_root_invalid")
        path = Path(raw)
        if _has_symlink_component(path):
            raise auth.AuthorityError("project_repo_root_symlink", rc=1)
        try:
            real = path.resolve(strict=True)
            info = path.lstat()
        except (OSError, RuntimeError):
            raise auth.AuthorityError("project_repo_root_invalid", rc=1)
        if not stat.S_ISDIR(info.st_mode) or str(real) != raw:
            raise auth.AuthorityError("project_repo_root_invalid", rc=1)
        pins.append((raw, str(real), int(info.st_dev), int(info.st_ino)))
    return pins


def _store_repo_roots(con, project_id, generation, pins, status="active"):
    con.execute("DELETE FROM project_repo_roots WHERE project_id=?",
                (project_id,))
    for root_path, realpath, dev, ino in pins:
        con.execute(
            """INSERT INTO project_repo_roots
               (project_id,root_path,root_realpath,root_dev,root_ino,
                project_generation,status) VALUES(?,?,?,?,?,?,?)""",
            (project_id, root_path, realpath, dev, ino, int(generation), status),
        )


def _project(value):
    if not isinstance(value, dict) or set(value) != PROJECT_KEYS:
        raise auth.AuthorityError("project_shape_invalid")
    for key in ("project_id", "display_name", "outline_ref", "fleet_id",
                "memory_scope_id", "memory_schema_version", "memory_store_ref"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise auth.AuthorityError("project_shape_invalid")
    if value.get("status") != "active":
        raise auth.AuthorityError("project_shape_invalid")
    for key in ("aliases", "repo_remotes", "repo_roots"):
        rows = value.get(key)
        if not isinstance(rows, list) or not rows \
                or not all(isinstance(x, str) and x for x in rows) \
                or len(rows) != len(set(rows)):
            raise auth.AuthorityError("project_shape_invalid")
    out = dict(value)
    out["aliases"] = list(value["aliases"])
    out["repo_remotes"] = list(value["repo_remotes"])
    out["repo_roots"] = list(value["repo_roots"])
    return out


def _row_project(row):
    value = json.loads(row["project_json"])
    value["generation"] = int(row["generation"])
    value["status"] = row["status"]
    return value


def _verify_owner(authority_path, request, now_ms, action, subject):
    try:
        return auth.verify_owner_approval(authority_path, request, now_ms,
                                          action=action, subject_id=subject)
    except auth.AuthorityError as err:
        if err.reason == "p1a_authority_proof_missing":
            raise auth.AuthorityError("project_owner_approval_missing", rc=1)
        if err.reason in ("p1a_authority_request_hash_mismatch",
                          "p1a_authority_proof_invalid"):
            raise auth.AuthorityError("project_owner_approval_mismatch", rc=1)
        raise


def _cas(current, expected):
    if not isinstance(expected, int) or expected < 0:
        raise auth.AuthorityError("project_expected_generation_invalid")
    if expected < current:
        raise auth.AuthorityError("project_generation_rollback", rc=1)
    if expected > current:
        raise auth.AuthorityError("project_generation_conflict", rc=1)


def _legacy_evidence(cwd, project):
    raw = str(cwd)
    if not os.path.isabs(raw):
        raise auth.AuthorityError("legacy_project_identity_unproven", rc=1)
    real = os.path.realpath(raw)
    if os.path.normpath(raw) != raw or not os.path.isdir(real):
        raise auth.AuthorityError("legacy_project_identity_unproven", rc=1)
    manifest = Path(real) / ".ez2-project.json"
    try:
        manifest_raw = manifest.read_bytes()
        doc = json.loads(manifest_raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise auth.AuthorityError("legacy_project_identity_unproven", rc=1)
    if not isinstance(doc, dict) or doc.get("schema") != "ez2-project-manifest/v1" \
            or doc.get("project_id") != project["project_id"] \
            or doc.get("outline_ref") != project["outline_ref"]:
        raise auth.AuthorityError("legacy_project_identity_unproven", rc=1)
    try:
        proc = subprocess.run(
            ["git", "-C", real, "remote", "get-url", "--all", "--push", "--all"],
            capture_output=True, text=True, timeout=10,
            env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"},
        )
        # Older git does not accept --all after --push in every order.  Use a
        # deterministic fallback that still performs no network operation.
        if proc.returncode != 0:
            proc = subprocess.run(["git", "-C", real, "remote", "-v"],
                                  capture_output=True, text=True, timeout=10,
                                  env={"PATH": os.environ.get("PATH", ""),
                                       "LC_ALL": "C"})
            remotes = []
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    fields = line.split()
                    if len(fields) >= 2:
                        remotes.append(fields[1])
        else:
            remotes = [line for line in proc.stdout.splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        raise auth.AuthorityError("legacy_project_identity_unproven", rc=1)
    remotes = sorted(set(remotes))
    declared = set(project["repo_remotes"])
    if not remotes or not (set(remotes) & declared):
        raise auth.AuthorityError("legacy_project_identity_unproven", rc=1)
    if set(remotes) - declared:
        raise auth.AuthorityError("legacy_project_ambiguous", rc=1)
    return {"cwd_realpath": real,
            "manifest_sha256": auth.sha256_hex(manifest_raw),
            "observed_remotes": remotes,
            "project_id": project["project_id"]}


def _create(con, authority_path, request, now_ms, legacy=False):
    project = _project(request.get("project"))
    project_id = project["project_id"]
    action = "project.import_legacy" if legacy else "project.create"
    verified = _verify_owner(authority_path, request, now_ms, action, project_id)
    evidence = _legacy_evidence(request.get("legacy_cwd"), project) if legacy else None
    if evidence and evidence["cwd_realpath"] not in project["repo_roots"]:
        project["repo_roots"] = sorted(project["repo_roots"] +
                                         [evidence["cwd_realpath"]])
    root_pins = _pin_repo_roots(project)
    with auth.immediate(con):
        row = con.execute("SELECT * FROM projects WHERE project_id=?",
                          (project_id,)).fetchone()
        current = int(row["generation"]) if row else 0
        _cas(current, request.get("expected_generation"))
        if row is not None:
            raise auth.AuthorityError("project_already_registered", rc=1)
        auth.consume_proof(con, verified, now_ms)
        material_hash = auth.sha256_hex(auth.canonical_bytes(project))
        con.execute("""INSERT INTO projects
                       (project_id,generation,status,project_json,owner_id,material_hash,updated_ms)
                       VALUES(?,?,?,?,?,?,?)""",
                    (project_id, 1, "active", auth.canonical_bytes(project).decode("utf-8"),
                     verified["authority_id"], material_hash, int(now_ms)))
        _store_repo_roots(con, project_id, 1, root_pins)
        receipt = auth.append_event(con, "project", project_id, 1,
                                    "legacy_import" if legacy else "created",
                                    {"project_hash": material_hash,
                                     "request_sha256": auth.canonical_request_hash(request),
                                     "legacy_evidence": evidence}, now_ms, verified)
    receipt.update({"project_id": project_id,
                    "request_sha256": auth.canonical_request_hash(request),
                    "owner_id": verified["authority_id"]})
    if evidence:
        receipt.update(evidence)
    return auth.result_ok(
        reason="legacy_project_imported" if legacy else "project_registry_created",
        project=dict(project, generation=1), receipt=receipt)


def _update(con, authority_path, request, now_ms, revoke=False):
    project_id = request.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise auth.AuthorityError("project_id_invalid")
    action = "project.revoke" if revoke else "project.update"
    verified = _verify_owner(authority_path, request, now_ms, action, project_id)
    with auth.immediate(con):
        row = con.execute("SELECT * FROM projects WHERE project_id=?",
                          (project_id,)).fetchone()
        if row is None:
            raise auth.AuthorityError("project_not_registered", rc=1)
        current = int(row["generation"])
        _cas(current, request.get("expected_generation"))
        if row["status"] == "revoked":
            raise auth.AuthorityError("project_revoked", rc=1)
        project = json.loads(row["project_json"])
        if revoke:
            if not isinstance(request.get("reason_code"), str) or not request["reason_code"]:
                raise auth.AuthorityError("project_revocation_reason_missing")
            status = "revoked"
            material = {"project": project, "reason_code": request["reason_code"]}
            root_pins = None
        else:
            patch = request.get("patch")
            mutable = {"display_name", "aliases", "repo_remotes", "repo_roots",
                       "outline_ref", "memory_store_ref", "memory_schema_version"}
            if not isinstance(patch, dict) or not patch or set(patch) - mutable:
                raise auth.AuthorityError("project_patch_invalid")
            project.update(patch)
            project = _project(project)
            root_pins = _pin_repo_roots(project)
            status = "active"
            material = {"project": project}
        auth.consume_proof(con, verified, now_ms)
        generation = current + 1
        material_hash = auth.sha256_hex(auth.canonical_bytes(material))
        con.execute("""UPDATE projects SET generation=?,status=?,project_json=?,
                       owner_id=?,material_hash=?,updated_ms=? WHERE project_id=?""",
                    (generation, status, auth.canonical_bytes(project).decode("utf-8"),
                     verified["authority_id"], material_hash, int(now_ms), project_id))
        if revoke:
            con.execute(
                "UPDATE project_repo_roots SET status='revoked',"
                "project_generation=? WHERE project_id=?",
                (generation, project_id),
            )
        else:
            _store_repo_roots(con, project_id, generation, root_pins)
        receipt = auth.append_event(con, "project", project_id, generation,
                                    "revoked" if revoke else "updated",
                                    material, now_ms, verified)
    return auth.result_ok(
        reason="project_registry_revoked" if revoke else "project_registry_updated",
        project=dict(project, generation=generation, status=status), receipt=receipt)


def _resolve(con, request):
    project_id = request.get("project_id")
    row = con.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise auth.AuthorityError("project_not_registered", rc=1)
    if row["status"] == "revoked":
        raise auth.AuthorityError("project_revoked", rc=1)
    return auth.result_ok(reason="project_registry_resolved", project=_row_project(row))


def execute(state_root, authority_path, request, now_ms):
    con = None
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("project_request_invalid")
        operation = request.get("operation")
        if operation in _REQUEST_KEYS \
                and set(request) - (_REQUEST_KEYS[operation] | _PROOF_KEYS):
            raise auth.AuthorityError("project_request_invalid", rc=1)
        con = auth.open_db(state_root)
        _tables(con)
        if operation == "create":
            return _create(con, authority_path, request, now_ms)
        if operation == "import_legacy_cwd":
            return _create(con, authority_path, request, now_ms, legacy=True)
        if operation == "update":
            return _update(con, authority_path, request, now_ms)
        if operation == "revoke":
            return _update(con, authority_path, request, now_ms, revoke=True)
        if operation == "resolve":
            return _resolve(con, request)
        raise auth.AuthorityError("project_operation_unknown")
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        if err.reason in ("project_generation_rollback", "project_generation_conflict") and con:
            row = con.execute("SELECT generation FROM projects WHERE project_id=?",
                              (request.get("project_id") or
                               (request.get("project") or {}).get("project_id"),)).fetchone()
            if row:
                result["current_generation"] = int(row["generation"])
        return result
    except Exception:
        return auth.result_error(auth.AuthorityError("project_internal_error"))
    finally:
        if con is not None:
            con.close()
