#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Idempotent invalidation of every consumer of a revoked P1A source."""

from __future__ import annotations

import hashlib
import json

from . import authority_store as auth
from . import binding_store as bs
from . import native_root


def _source(con, request):
    kind = request.get("subject_kind")
    subject_id = request.get("subject_id")
    if kind == "native_root":
        path = request.get("source_receipt_path")
        try:
            receipt = native_root._read_receipt(path)
        except auth.AuthorityError:
            raise auth.AuthorityError("revocation_source_receipt_invalid", rc=1)
        row = con.execute(
            "SELECT * FROM active_native_roots WHERE native_root_id=?",
            (subject_id,),
        ).fetchone()
        if row is None or row["receipt_path"] != path \
                or row["status"] != "revoked" \
                or receipt.get("status") != "revoked" \
                or receipt.get("native_root_id") != subject_id \
                or receipt.get("generation") != int(row["receipt_generation"]) \
                or receipt.get("receipt_sha256") != row["receipt_sha256"]:
            raise auth.AuthorityError("revocation_source_not_revoked", rc=1)
        return kind, subject_id, int(row["receipt_generation"])
    if kind == "project":
        row = con.execute("SELECT * FROM projects WHERE project_id=?",
                          (subject_id,)).fetchone()
        generation = request.get("source_generation")
        if row is None or row["status"] != "revoked" \
                or not isinstance(generation, int) \
                or generation != int(row["generation"]):
            raise auth.AuthorityError("revocation_source_not_revoked", rc=1)
        return kind, subject_id, generation
    raise auth.AuthorityError("revocation_subject_kind_unknown")


def _empty():
    return {"bindings": [], "read_receipts": [], "recall_caches": [],
            "prompt_caches": [], "rag_indexes": [], "continuity": []}


def _reconcile(state_root, request, now_ms):
    con = auth.open_db(state_root)
    try:
        bs.tables(con)
        with auth.immediate(con):
            kind, subject_id, source_generation = _source(con, request)
            fanout_key = hashlib.sha256(bs.canonical({
                "subject_kind": kind, "subject_id": subject_id,
                "source_generation": source_generation,
            })).hexdigest()
            prior = con.execute(
                "SELECT * FROM revocation_fanouts WHERE fanout_key=?",
                (fanout_key,),
            ).fetchone()
            if prior is not None:
                receipt = bs.json_load(
                    prior["receipt_json"], "revocation_fanout_receipt_tampered")
                body = bs.verify_receipt(
                    state_root, receipt, "revocation_fanout_receipt_tampered")
                try:
                    stored_invalidated = json.loads(prior["invalidated_json"])
                except (TypeError, ValueError):
                    raise auth.AuthorityError(
                        "revocation_fanout_receipt_tampered", rc=1)
                if body.get("fanout_key") != fanout_key \
                        or body.get("invalidated") != stored_invalidated \
                        or body.get("fanout_generation") != int(
                            prior["fanout_generation"]):
                    raise auth.AuthorityError(
                        "revocation_fanout_receipt_tampered", rc=1)
                return auth.result_ok(
                    reason="revocation_fanout_completed",
                    fanout_generation=int(prior["fanout_generation"]),
                    invalidated=stored_invalidated,
                    already_reconciled=True)
            invalidated = _empty()
            bindings = con.execute(
                "SELECT * FROM active_bindings ORDER BY binding_id").fetchall()
            selected = []
            for row in bindings:
                binding, _receipt = bs.verified_active_binding(
                    state_root, con, row, "revocation_binding_state_invalid")
                matches = (kind == "native_root" and
                           binding.get("native_root_id") == subject_id) or \
                          (kind == "project" and
                           binding.get("project_id") == subject_id)
                if matches:
                    selected.append(row["binding_id"])
            invalidated["bindings"] = sorted(selected)
            if selected:
                marks = ",".join("?" for _ in selected)
                con.execute(
                    "UPDATE active_bindings SET status='source_revoked',"
                    "updated_at_ms=? WHERE binding_id IN (%s)" % marks,
                    (int(now_ms), *selected),
                )
                reads = con.execute(
                    "SELECT read_receipt_id FROM onboarding_read_receipts "
                    "WHERE binding_id IN (%s) ORDER BY read_receipt_id" % marks,
                    tuple(selected),
                ).fetchall()
                read_ids = [row["read_receipt_id"] for row in reads]
                invalidated["read_receipts"] = read_ids
                if read_ids:
                    read_marks = ",".join("?" for _ in read_ids)
                    con.execute(
                        "UPDATE onboarding_read_receipts SET status='revoked' "
                        "WHERE read_receipt_id IN (%s)" % read_marks,
                        tuple(read_ids),
                    )
                    consumers = con.execute(
                        """SELECT consumer_kind,consumer_id FROM
                           onboarding_consumers WHERE read_receipt_id IN (%s)
                           ORDER BY consumer_kind,consumer_id""" % read_marks,
                        tuple(read_ids),
                    ).fetchall()
                    con.execute(
                        "UPDATE onboarding_consumers SET status='revoked' "
                        "WHERE read_receipt_id IN (%s)" % read_marks,
                        tuple(read_ids),
                    )
                    destinations = {
                        "recall_cache": "recall_caches",
                        "prompt_cache": "prompt_caches",
                        "rag_index": "rag_indexes",
                        "continuity": "continuity",
                    }
                    for consumer in consumers:
                        target = destinations.get(consumer["consumer_kind"])
                        if target is None:
                            raise auth.AuthorityError(
                                "revocation_consumer_kind_unknown", rc=1)
                        invalidated[target].append(consumer["consumer_id"])
            prepared = con.execute(
                "SELECT * FROM bind_plans WHERE capability_status='prepared'"
            ).fetchall()
            for plan_row in prepared:
                plan = bs.json_load(
                    plan_row["plan_json"], "revocation_bind_plan_invalid")
                matches = (kind == "native_root" and
                           plan.get("native_root_id") == subject_id) or \
                          (kind == "project" and
                           plan.get("project_id") == subject_id)
                if matches:
                    con.execute(
                        "UPDATE bind_plans SET capability_status='abandoned' "
                        "WHERE plan_sha256=?", (plan_row["plan_sha256"],),
                    )
                    con.execute(
                        "UPDATE bind_transactions SET status='abandoned',"
                        "updated_at_ms=? WHERE capability_id=? AND status='prepared'",
                        (int(now_ms), plan_row["capability_id"]),
                    )
            for key in invalidated:
                invalidated[key] = sorted(invalidated[key])
            head = con.execute(
                "SELECT MAX(fanout_generation) AS generation FROM "
                "revocation_fanouts WHERE subject_kind=? AND subject_id=?",
                (kind, subject_id),
            ).fetchone()
            generation = int(head["generation"] or 0) + 1
            fanout_receipt = bs.seal_receipt(state_root, {
                "schema": "ez2-aib/revocation-fanout-receipt/v1",
                "fanout_key": fanout_key,
                "fanout_generation": generation,
                "subject_kind": kind,
                "subject_id": subject_id,
                "source_generation": source_generation,
                "invalidated": invalidated,
                "created_at_ms": int(now_ms),
            })
            con.execute(
                """INSERT INTO revocation_fanouts
                   (fanout_key,fanout_generation,subject_kind,subject_id,
                    source_generation,invalidated_json,receipt_json,created_at_ms)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (fanout_key, generation, kind, subject_id, source_generation,
                 auth.canonical_bytes(invalidated).decode("utf-8"),
                 auth.canonical_bytes(fanout_receipt).decode("utf-8"),
                 int(now_ms)),
            )
        return auth.result_ok(reason="revocation_fanout_completed",
                              fanout_generation=generation,
                              invalidated=invalidated,
                              already_reconciled=False)
    finally:
        con.close()


def execute(state_root, authority_path, request, now_ms):
    del authority_path
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("revocation_fanout_request_invalid")
        if request.get("operation") != "reconcile":
            raise auth.AuthorityError("revocation_fanout_operation_unknown")
        expected = ({"operation", "subject_kind", "subject_id",
                     "source_receipt_path"}
                    if request.get("subject_kind") == "native_root" else
                    {"operation", "subject_kind", "subject_id",
                     "source_generation"})
        if set(request) != expected:
            raise auth.AuthorityError("revocation_fanout_request_invalid")
        return _reconcile(state_root, request, now_ms)
    except auth.AuthorityError as err:
        return auth.result_error(err)
    except Exception:
        return auth.result_error(auth.AuthorityError(
            "revocation_fanout_internal_error"))
