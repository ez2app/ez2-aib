#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Closed authority proof and crash-safe SQLite helpers for P1A.

This module does not decide who the maintainer or Ez2 Main is.  It verifies a pinned,
explicit authority registry supplied by the caller.  A normal environment
variable, seat file, role string, LINE identity, or self-report is never an
authority source.
"""

import contextlib
import hashlib
import hmac
import json
import os
import re
import sqlite3
from pathlib import Path


AUTHORITY_SCHEMA = "ez2-aib/p1a-authority/v1"
BOOTSTRAP_AUTHORITY_SCHEMA = "ez2-aib/p1a-bootstrap-authority/v1"
PROOF_DOMAIN = "ez2-aib/p1a-authority-proof/v1"
DB_SCHEMA = "ez2-aib/fleet-authority-db/v1"
ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
PROOF_KEYS = frozenset((
    "authority_id", "authority_kind", "authority_generation", "request_hash",
    "issued_at_ms", "expires_at_ms", "nonce", "signature",
))
AUTHORITY_ENTRY_KEYS = frozenset((
    "authority_id", "kind", "generation", "status", "hmac_key_hex",
))


class AuthorityError(Exception):
    """Typed refusal.  Callers return ``reason`` and never expose internals."""

    def __init__(self, reason, detail=None, rc=2):
        self.reason = str(reason)
        self.detail = str(detail) if detail else None
        self.rc = int(rc)
        super().__init__(self.reason)


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _without_proof(request):
    if not isinstance(request, dict):
        raise AuthorityError("p1a_request_invalid")
    return {k: v for k, v in request.items()
            if k not in ("authority_proof", "owner_approval", "main_approval")}


def canonical_request_hash(request):
    """Hash the exact request except its self-referential proof wrapper."""
    try:
        body = dict(_without_proof(request))
        body.pop("approval_id", None)
        # The newline is part of the frozen bootstrap authority contract.  It
        # also makes hashes equal to the bytes written by the JSON helpers.
        return sha256_hex(canonical_bytes(body) + b"\n")
    except (TypeError, ValueError):
        raise AuthorityError("p1a_request_not_canonical")


def proof_payload(proof):
    body = {k: proof[k] for k in (
        "authority_id", "authority_kind", "authority_generation", "request_hash",
        "issued_at_ms", "expires_at_ms", "nonce",
    )}
    return PROOF_DOMAIN.encode("ascii") + b"\x00" + canonical_bytes(body)


def _read_json(path, reason):
    try:
        raw = Path(path).read_bytes()
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise AuthorityError(reason)


def load_authority(path):
    doc = _read_json(path, "p1a_authority_unreadable")
    if not isinstance(doc, dict) or doc.get("schema") != AUTHORITY_SCHEMA:
        raise AuthorityError("p1a_authority_schema_invalid")
    allowed = {"schema", "generation", "authorities", "native_root_policy"}
    if set(doc) - allowed:
        raise AuthorityError("p1a_authority_schema_invalid")
    if not isinstance(doc.get("generation"), int) or doc["generation"] < 1:
        raise AuthorityError("p1a_authority_schema_invalid")
    rows = doc.get("authorities")
    if not isinstance(rows, list) or not rows:
        raise AuthorityError("p1a_authority_schema_invalid")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != AUTHORITY_ENTRY_KEYS:
            raise AuthorityError("p1a_authority_schema_invalid")
        aid = row.get("authority_id")
        if not isinstance(aid, str) or not ID_RE.fullmatch(aid) or aid in out:
            raise AuthorityError("p1a_authority_schema_invalid")
        if row.get("kind") not in ("owner", "main"):
            raise AuthorityError("p1a_authority_schema_invalid")
        if row.get("status") not in ("active", "revoked"):
            raise AuthorityError("p1a_authority_schema_invalid")
        if not isinstance(row.get("generation"), int) or row["generation"] < 1:
            raise AuthorityError("p1a_authority_schema_invalid")
        if not isinstance(row.get("hmac_key_hex"), str) \
                or not HEX64_RE.fullmatch(row["hmac_key_hex"]):
            raise AuthorityError("p1a_authority_schema_invalid")
        out[aid] = dict(row)
    policy = doc.get("native_root_policy")
    if policy is not None:
        if not isinstance(policy, dict) or set(policy) != {"allowed_roots", "max_ttl_ms"}:
            raise AuthorityError("p1a_authority_schema_invalid")
        if not isinstance(policy.get("allowed_roots"), list) \
                or not policy["allowed_roots"] \
                or not all(isinstance(p, str) and os.path.isabs(p)
                           for p in policy["allowed_roots"]):
            raise AuthorityError("p1a_authority_schema_invalid")
        if not isinstance(policy.get("max_ttl_ms"), int) or policy["max_ttl_ms"] < 1000:
            raise AuthorityError("p1a_authority_schema_invalid")
    return {"schema": doc["schema"], "generation": doc["generation"],
            "authorities": out, "native_root_policy": policy}


def load_bootstrap_authority(path):
    """Load the closed P1A bootstrap registry used before Main IPC exists.

    This registry is not upgraded into cryptographic owner authority.  It is a
    pinned local source whose exact approval row is request-bound; callers and
    receipts retain ``authority_grade=bootstrap_registry``.
    """
    doc = _read_json(path, "p1a_authority_unreadable")
    required = {"schema", "generation", "owners", "approvals", "principals",
                "main_producers", "native_root_parents"}
    if not isinstance(doc, dict) or set(doc) != required \
            or doc.get("schema") != BOOTSTRAP_AUTHORITY_SCHEMA:
        raise AuthorityError("p1a_authority_schema_invalid")
    if not isinstance(doc.get("generation"), int) or doc["generation"] < 1:
        raise AuthorityError("p1a_authority_schema_invalid")
    for name in ("owners", "approvals", "principals", "main_producers"):
        if not isinstance(doc.get(name), dict):
            raise AuthorityError("p1a_authority_schema_invalid")
    parents = doc.get("native_root_parents")
    if not isinstance(parents, list) or not parents \
            or not all(isinstance(p, str) and os.path.isabs(p) for p in parents):
        raise AuthorityError("p1a_authority_schema_invalid")
    return doc


def _request_proof(request):
    candidates = [request.get(k) for k in
                  ("authority_proof", "owner_approval", "main_approval")
                  if request.get(k) is not None]
    if len(candidates) != 1:
        raise AuthorityError("p1a_authority_proof_missing")
    proof = candidates[0]
    if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
        raise AuthorityError("p1a_authority_proof_invalid")
    return proof


def verify_owner_approval(authority_path, request, now_ms, required_kind="owner",
                          action=None, subject_id=None):
    """Verify exact request binding, authority generation, time and HMAC."""
    raw = _read_json(authority_path, "p1a_authority_unreadable")
    if raw.get("schema") == BOOTSTRAP_AUTHORITY_SCHEMA:
        if required_kind != "owner":
            raise AuthorityError("p1a_authority_wrong_kind")
        registry = load_bootstrap_authority(authority_path)
        approval_id = request.get("approval_id") if isinstance(request, dict) else None
        if not approval_id:
            raise AuthorityError("p1a_authority_proof_missing")
        approval = registry["approvals"].get(approval_id)
        if not isinstance(approval, dict):
            raise AuthorityError("p1a_authority_proof_invalid")
        owner_id = approval.get("owner_id")
        owner = registry["owners"].get(owner_id)
        if not isinstance(owner, dict) or owner.get("status") != "active":
            raise AuthorityError("p1a_authority_revoked")
        if approval.get("status") != "approved":
            raise AuthorityError("p1a_authority_proof_invalid")
        if action is not None and approval.get("action") != action:
            raise AuthorityError("p1a_authority_request_hash_mismatch")
        if subject_id is not None and approval.get("subject_id") != subject_id:
            raise AuthorityError("p1a_authority_request_hash_mismatch")
        if approval.get("request_sha256") != canonical_request_hash(request):
            raise AuthorityError("p1a_authority_request_hash_mismatch")
        if approval.get("expected_generation") != request.get("expected_generation"):
            raise AuthorityError("p1a_authority_request_hash_mismatch")
        issued = approval.get("issued_at_ms")
        expires = approval.get("expires_at_ms")
        if not isinstance(issued, int) or not isinstance(expires, int):
            raise AuthorityError("p1a_authority_proof_invalid")
        if issued > int(now_ms) + 30_000:
            raise AuthorityError("p1a_authority_proof_from_future")
        if expires <= int(now_ms):
            raise AuthorityError("p1a_authority_proof_expired")
        if not isinstance(owner.get("generation"), int) \
                or owner["generation"] < 1:
            raise AuthorityError("p1a_authority_schema_invalid")
        proof_digest = sha256_hex(canonical_bytes(approval))
        return {"authority_id": owner_id, "kind": "owner",
                "generation": int(owner.get("generation", 0)),
                "registry_generation": registry["generation"],
                "proof_digest": proof_digest, "nonce": approval_id,
                "authority_grade": "bootstrap_registry", "approval": approval}

    registry = load_authority(authority_path)
    proof = _request_proof(request)
    aid = proof.get("authority_id")
    row = registry["authorities"].get(aid)
    if row is None or row.get("kind") != required_kind:
        raise AuthorityError("p1a_authority_wrong_kind")
    if row.get("status") != "active":
        raise AuthorityError("p1a_authority_revoked")
    if proof.get("authority_kind") != required_kind:
        raise AuthorityError("p1a_authority_wrong_kind")
    if proof.get("authority_generation") != row.get("generation"):
        raise AuthorityError("p1a_authority_generation_stale")
    if proof.get("request_hash") != canonical_request_hash(request):
        raise AuthorityError("p1a_authority_request_hash_mismatch")
    for key in ("issued_at_ms", "expires_at_ms", "authority_generation"):
        if not isinstance(proof.get(key), int):
            raise AuthorityError("p1a_authority_proof_invalid")
    if proof["issued_at_ms"] > int(now_ms) + 30_000:
        raise AuthorityError("p1a_authority_proof_from_future")
    if proof["expires_at_ms"] <= int(now_ms):
        raise AuthorityError("p1a_authority_proof_expired")
    if proof["expires_at_ms"] - proof["issued_at_ms"] > 15 * 60 * 1000:
        raise AuthorityError("p1a_authority_proof_ttl_exceeded")
    if not isinstance(proof.get("nonce"), str) or not NONCE_RE.fullmatch(proof["nonce"]):
        raise AuthorityError("p1a_authority_proof_invalid")
    sig = proof.get("signature")
    if not isinstance(sig, str) or not HEX64_RE.fullmatch(sig):
        raise AuthorityError("p1a_authority_proof_invalid")
    expected = hmac.new(bytes.fromhex(row["hmac_key_hex"]), proof_payload(proof),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise AuthorityError("p1a_authority_signature_invalid")
    return {"authority_id": aid, "kind": required_kind,
            "generation": row["generation"], "registry_generation": registry["generation"],
            "proof_digest": sha256_hex(canonical_bytes(proof)), "nonce": proof["nonce"],
            "native_root_policy": registry.get("native_root_policy")}


def state_db_path(state_root):
    return Path(state_root) / "fleet" / "authority.sqlite3"


def binding_key_path(state_root):
    return Path(state_root) / "fleet" / "bind-local.key"


def _ensure_binding_key(state_root):
    """Create the local sealing key only while initializing a new state DB.

    Read-only bind planning must never create this key.  Once the state DB
    exists, a missing or symlinked key is an integrity failure rather than a
    reason to silently mint a new identity.
    """
    path = binding_key_path(state_root)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise AuthorityError("bind_local_key_invalid")
        return
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    try:
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(os.urandom(32))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(str(temporary), str(path))
        os.chmod(str(path), 0o600)
        temporary.unlink(missing_ok=True)
    except FileExistsError:
        temporary.unlink(missing_ok=True)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise AuthorityError("bind_local_key_unavailable")


def open_db(state_root, *, readonly=False):
    root = Path(state_root)
    if not root.is_absolute():
        raise AuthorityError("p1a_state_root_not_absolute")
    db_path = state_db_path(root)
    if readonly:
        if not db_path.is_file() or db_path.is_symlink():
            raise AuthorityError("p1a_state_db_unavailable")
        try:
            con = sqlite3.connect(
                db_path.resolve().as_uri() + "?mode=ro&immutable=1",
                                  uri=True, timeout=15.0,
                                  isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=15000")
            return con
        except sqlite3.Error:
            raise AuthorityError("p1a_state_db_unavailable")
    existed = db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(str(db_path.parent), 0o700)
        if db_path.is_symlink():
            raise AuthorityError("p1a_state_db_unavailable")
        if not existed:
            _ensure_binding_key(root)
        elif not binding_key_path(root).is_file() \
                or binding_key_path(root).is_symlink():
            raise AuthorityError("bind_local_key_unavailable")
        con = sqlite3.connect(str(db_path), timeout=15.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=15000")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS fleet_meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS authority_nonces(
          proof_digest TEXT PRIMARY KEY,
          authority_id TEXT NOT NULL,
          nonce TEXT NOT NULL,
          consumed_at_ms INTEGER NOT NULL,
          UNIQUE(authority_id, nonce)
        );
        CREATE TABLE IF NOT EXISTS authority_events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          stream TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          generation INTEGER NOT NULL,
          kind TEXT NOT NULL,
          material_hash TEXT NOT NULL,
          prev_hash TEXT,
          receipt_json TEXT NOT NULL,
          created_ms INTEGER NOT NULL,
          UNIQUE(stream, entity_id, generation)
        );
        CREATE TABLE IF NOT EXISTS bind_plans(
          plan_sha256 TEXT PRIMARY KEY,
          plan_json TEXT NOT NULL,
          capability_id TEXT NOT NULL UNIQUE,
          capability_hash TEXT NOT NULL,
          capability_status TEXT NOT NULL
            CHECK(capability_status IN ('issued','prepared','consumed','abandoned')),
          expires_at_ms INTEGER NOT NULL,
          created_at_ms INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_bindings(
          project_id TEXT NOT NULL,
          fleet_id TEXT NOT NULL,
          fleet_member_id TEXT NOT NULL,
          generation INTEGER NOT NULL,
          status TEXT NOT NULL
            CHECK(status IN ('active','source_revoked','inactive')),
          binding_id TEXT NOT NULL UNIQUE,
          binding_json TEXT NOT NULL,
          receipt_json TEXT NOT NULL,
          updated_at_ms INTEGER NOT NULL,
          PRIMARY KEY(project_id,fleet_id,fleet_member_id)
        );
        """)
        con.execute("INSERT OR IGNORE INTO fleet_meta(key,value) VALUES('schema',?)",
                    (DB_SCHEMA,))
        if not db_path.exists():
            raise AuthorityError("p1a_state_db_unavailable")
        os.chmod(str(db_path), 0o600)
        return con
    except (OSError, sqlite3.Error):
        raise AuthorityError("p1a_state_db_unavailable")


@contextlib.contextmanager
def immediate(con):
    try:
        con.execute("BEGIN IMMEDIATE")
        yield
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def consume_proof(con, verified, now_ms):
    try:
        con.execute("""INSERT INTO authority_nonces
                       (proof_digest,authority_id,nonce,consumed_at_ms)
                       VALUES(?,?,?,?)""",
                    (verified["proof_digest"], verified["authority_id"],
                     verified["nonce"], int(now_ms)))
    except sqlite3.IntegrityError:
        raise AuthorityError("p1a_authority_proof_replay")


def event_head(con, stream, entity_id):
    row = con.execute("""SELECT generation,material_hash FROM authority_events
                         WHERE stream=? AND entity_id=? ORDER BY generation DESC LIMIT 1""",
                      (stream, entity_id)).fetchone()
    return (int(row["generation"]), row["material_hash"]) if row else (0, None)


def append_event(con, stream, entity_id, generation, kind, material, created_ms,
                 authority):
    current, prev_hash = event_head(con, stream, entity_id)
    if int(generation) != current + 1:
        raise AuthorityError("p1a_event_generation_non_monotonic")
    body = {
        "schema": "ez2-aib/p1a-authority-event/v1",
        "stream": stream, "entity_id": entity_id, "generation": int(generation),
        "kind": kind, "material": material, "prev_hash": prev_hash,
        "created_ms": int(created_ms),
        "authority": {k: authority[k] for k in
                      ("authority_id", "kind", "generation", "registry_generation",
                       "proof_digest")},
    }
    material_hash = sha256_hex(canonical_bytes(body))
    receipt = dict(body, material_hash=material_hash)
    con.execute("""INSERT INTO authority_events
                   (stream,entity_id,generation,kind,material_hash,prev_hash,receipt_json,created_ms)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (stream, entity_id, int(generation), kind, material_hash, prev_hash,
                 canonical_bytes(receipt).decode("utf-8"), int(created_ms)))
    return receipt


def row_dict(row):
    return dict(row) if row is not None else None


def result_ok(**values):
    return dict({"ok": True, "reason": "ok", "rc": 0}, **values)


def result_error(err):
    if isinstance(err, AuthorityError):
        out = {"ok": False, "reason": err.reason, "rc": err.rc}
        if err.detail:
            out["detail"] = err.detail
        return out
    return {"ok": False, "reason": "p1a_internal_error", "rc": 2}
