#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Published-persona catalog; directory discovery is deliberately forbidden."""

import json
from pathlib import Path

try:
    from . import authority_store as auth
except ImportError:  # pragma: no cover
    import authority_store as auth


_PROOF_KEYS = frozenset((
    "schema", "approval_id", "authority_proof", "owner_approval",
    "main_approval",
))
_REQUEST_KEYS = {
    "import": frozenset((
        "operation", "expected_generation", "source_kind", "source_path",
        "source_sha256", "persona_uid", "lifecycle",
    )),
    "resolve": frozenset(("operation", "persona_uid")),
    "list": frozenset(("operation",)),
    "scan_directory": frozenset((
        "operation", "source_path", "expected_generation",
    )),
}


def _tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS published_personas(
      persona_uid TEXT PRIMARY KEY,
      persona_sha256 TEXT NOT NULL,
      lifecycle TEXT NOT NULL,
      source_path TEXT NOT NULL,
      catalog_generation INTEGER NOT NULL,
      owner_id TEXT NOT NULL,
      card_json TEXT NOT NULL,
      receipt_json TEXT NOT NULL,
      imported_ms INTEGER NOT NULL
    );
    INSERT OR IGNORE INTO fleet_meta(key,value) VALUES('persona_catalog_generation','0');
    """)


def _verify_owner(authority_path, request, now_ms, persona_uid):
    try:
        return auth.verify_owner_approval(authority_path, request, now_ms,
                                          action="persona.publish",
                                          subject_id=persona_uid)
    except auth.AuthorityError as err:
        if err.reason == "p1a_authority_proof_missing":
            raise auth.AuthorityError("persona_owner_approval_missing", rc=1)
        if err.reason in ("p1a_authority_request_hash_mismatch",
                          "p1a_authority_proof_invalid"):
            raise auth.AuthorityError("persona_owner_approval_mismatch", rc=1)
        raise


def _catalog_generation(con):
    row = con.execute("SELECT value FROM fleet_meta WHERE key='persona_catalog_generation'").fetchone()
    return int(row["value"])


def _import(con, authority_path, request, now_ms):
    uid = request.get("persona_uid")
    if not isinstance(uid, str) or not uid:
        raise auth.AuthorityError("persona_uid_invalid")
    verified = _verify_owner(authority_path, request, now_ms, uid)
    if request.get("source_kind") != "owner_approved_formal_card":
        raise auth.AuthorityError("persona_publish_proof_missing", rc=1)
    if request.get("lifecycle") != "published":
        raise auth.AuthorityError("persona_lifecycle_not_published", rc=1)
    source = request.get("source_path")
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise auth.AuthorityError("persona_source_unreadable", rc=1)
    try:
        raw = Path(source).read_bytes()
    except OSError:
        raise auth.AuthorityError("persona_source_unreadable", rc=1)
    digest = auth.sha256_hex(raw)
    if digest != request.get("source_sha256"):
        raise auth.AuthorityError("persona_source_hash_mismatch", rc=1)
    try:
        card = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise auth.AuthorityError("persona_source_invalid", rc=1)
    if not isinstance(card, dict) or card.get("schema") != "ez2-expert-card/v1" \
            or card.get("persona_uid") != uid:
        raise auth.AuthorityError("persona_source_identity_mismatch", rc=1)
    if card.get("lifecycle") != "published":
        raise auth.AuthorityError("persona_lifecycle_not_published", rc=1)
    expected = request.get("expected_generation")
    with auth.immediate(con):
        current = _catalog_generation(con)
        if not isinstance(expected, int) or expected < 0:
            raise auth.AuthorityError("persona_catalog_generation_invalid")
        if expected < current:
            raise auth.AuthorityError("persona_catalog_rollback", rc=1)
        if expected > current:
            raise auth.AuthorityError("persona_catalog_generation_conflict", rc=1)
        if con.execute("SELECT 1 FROM published_personas WHERE persona_uid=?", (uid,)).fetchone():
            raise auth.AuthorityError("persona_already_published", rc=1)
        auth.consume_proof(con, verified, now_ms)
        generation = current + 1
        persona = {"persona_uid": uid, "persona_sha256": digest,
                   "lifecycle": "published", "source_path": source,
                   "catalog_generation": generation,
                   "provenance_head": card.get("provenance_head")}
        receipt = {"schema": "ez2-aib/published-persona-import-receipt/v1",
                   "persona_uid": uid, "persona_sha256": digest,
                   "catalog_generation": generation,
                   "owner_id": verified["authority_id"],
                   "request_sha256": auth.canonical_request_hash(request),
                   "authority_grade": verified.get("authority_grade", "cryptographic"),
                   "imported_at_ms": int(now_ms)}
        con.execute("""INSERT INTO published_personas
                       (persona_uid,persona_sha256,lifecycle,source_path,catalog_generation,
                        owner_id,card_json,receipt_json,imported_ms)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (uid, digest, "published", source, generation,
                     verified["authority_id"], auth.canonical_bytes(card).decode("utf-8"),
                     auth.canonical_bytes(receipt).decode("utf-8"), int(now_ms)))
        con.execute("UPDATE fleet_meta SET value=? WHERE key='persona_catalog_generation'",
                    (str(generation),))
    return auth.result_ok(reason="persona_catalog_imported", persona=persona,
                          receipt=receipt, catalog_generation=generation)


def _resolve(con, uid):
    row = con.execute("SELECT * FROM published_personas WHERE persona_uid=?", (uid,)).fetchone()
    if row is None or row["lifecycle"] != "published":
        raise auth.AuthorityError("persona_not_published", rc=1)
    return {"persona_uid": row["persona_uid"],
            "persona_sha256": row["persona_sha256"],
            "lifecycle": row["lifecycle"],
            "source_path": row["source_path"],
            "catalog_generation": int(row["catalog_generation"])}


def execute(state_root, authority_path, request, now_ms):
    con = None
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("persona_request_invalid")
        operation = request.get("operation")
        if operation in _REQUEST_KEYS \
                and set(request) - (_REQUEST_KEYS[operation] | _PROOF_KEYS):
            raise auth.AuthorityError("persona_request_invalid", rc=1)
        con = auth.open_db(state_root)
        _tables(con)
        if operation == "scan_directory":
            raise auth.AuthorityError("persona_publish_proof_missing", rc=1)
        if operation == "import":
            return _import(con, authority_path, request, now_ms)
        if operation == "resolve":
            return auth.result_ok(reason="persona_catalog_resolved",
                                  persona=_resolve(con, request.get("persona_uid")))
        if operation == "list":
            rows = con.execute("SELECT persona_uid FROM published_personas WHERE lifecycle='published' ORDER BY persona_uid").fetchall()
            return auth.result_ok(reason="persona_catalog_listed",
                                  personas=[_resolve(con, row["persona_uid"]) for row in rows],
                                  catalog_generation=_catalog_generation(con))
        raise auth.AuthorityError("persona_operation_unknown")
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        if err.reason in ("persona_catalog_rollback", "persona_catalog_generation_conflict") and con:
            result["current_generation"] = _catalog_generation(con)
        return result
    except Exception:
        return auth.result_error(auth.AuthorityError("persona_internal_error"))
    finally:
        if con is not None:
            con.close()
