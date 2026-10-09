#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""D17-D apply_authorized: last-mile consume+apply on one writer lock.

Real effects go through bind_plan / bind_apply / binding_store only.
Caller JSON, environment, role strings, and mouse actor strings are not
authority. This module does not assign or grant via AUTO_BIND.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fleet_identity import authority_store
from fleet_identity import bind_apply
from fleet_identity import bind_grant_store
from fleet_identity import bind_plan
from fleet_identity import bind_target
from fleet_identity import binding_store


REQUEST_KEYS = frozenset({
    "operation", "authorization_ref", "bind_context", "trigger_kind",
})
TRIGGERS = frozenset({"ctx_threshold", "content"})
CONSUMED_REASONS = frozenset({"grant_consumed", "confirmation_consumed"})
APPLY_OK = frozenset({"bind_applied", "bind_apply_recovered"})
KIND_TABLE = {
    "grant": ("d17_scoped_auto_grants", "grant_id"),
    "confirmation": ("d17_bind_confirmations", "confirmation_id"),
}

EFFECT_FINGERPRINT_SCHEMA = "ez2-aib/d17-effect-fingerprint-input/v1"
RECEIPT_SCHEMA = "ez2-aib/d17-apply-authorized-receipt/v1"


class _Abort(Exception):
    def __init__(self, result):
        self.result = result


def effect_fingerprint(material):
    """sha256(canonical({schema, plan, target, kind, id, generation, receipt})).

    Owner is this module. Canonical bytes come from authority_store.
    """
    payload = {
        "schema": EFFECT_FINGERPRINT_SCHEMA,
        "plan_sha256": material["plan_sha256"],
        "target_fingerprint": material["target_fingerprint"],
        "authorization_kind": material["authorization_kind"],
        "authorization_id": material["authorization_id"],
        "authorization_generation": material["authorization_generation"],
        "authorization_receipt_sha256": material["authorization_receipt_sha256"],
    }
    return authority_store.sha256_hex(
        authority_store.canonical_bytes(payload))


def _refuse(reason, rc=1, *, receipt=None, mode="remind_only",
            mode_generation=0):
    result = {
        "ok": False,
        "reason": reason,
        "rc": int(rc),
        "effect_count": 0,
        "reminder_count": 0,
        "mode": mode,
        "mode_generation": int(mode_generation),
    }
    if receipt is not None:
        result["receipt"] = receipt
    return result


def _fingerprint_receipt(authorization_ref):
    if not isinstance(authorization_ref, dict):
        return None
    try:
        value = effect_fingerprint(authorization_ref)
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "schema": RECEIPT_SCHEMA,
        "operation": "apply_authorized",
        "authorization_kind": authorization_ref["authorization_kind"],
        "authorization_id": authorization_ref["authorization_id"],
        "authorization_generation": authorization_ref["authorization_generation"],
        "authorization_receipt_sha256": authorization_ref[
            "authorization_receipt_sha256"],
        "plan_sha256": authorization_ref["plan_sha256"],
        "target_fingerprint": authorization_ref["target_fingerprint"],
        "effect_fingerprint": value,
        "effect_count": 0,
        "reminder_count": 0,
    }


def _validate_request(request, now_ms):
    if type(now_ms) is not int:
        return _refuse("d17_request_invalid", rc=2)
    if not isinstance(request, dict):
        return _refuse("d17_request_invalid", rc=2)
    if request.get("operation") != "apply_authorized":
        return _refuse("d17_operation_invalid", rc=2)
    extra = set(request) - REQUEST_KEYS
    missing = REQUEST_KEYS - set(request)
    if extra or missing:
        return _refuse("d17_request_invalid", rc=2)
    if request.get("trigger_kind") not in TRIGGERS:
        return _refuse("d17_request_invalid", rc=2)
    if not isinstance(request.get("authorization_ref"), dict) \
            or not isinstance(request.get("bind_context"), dict):
        return _refuse("d17_request_invalid", rc=2)
    return None


def _abort(reason, rc=1, **kwargs):
    raise _Abort(_refuse(reason, rc=rc, **kwargs))


def _load_prepared(con, plan_sha256):
    if not isinstance(plan_sha256, str):
        return None, None
    try:
        row = con.execute(
            "SELECT plan_json, capability_json FROM bind_transactions "
            "WHERE plan_sha256=?",
            (plan_sha256,),
        ).fetchone()
    except Exception:
        return None, None
    if row is None:
        return None, None
    try:
        plan = json.loads(row["plan_json"])
        capability = json.loads(row["capability_json"])
    except (TypeError, ValueError):
        return None, None
    if not isinstance(plan, dict) or not isinstance(capability, dict):
        return None, None
    return plan, capability


def _binding_for_plan(con, plan_sha256):
    if not isinstance(plan_sha256, str):
        return None
    try:
        row = con.execute(
            "SELECT binding_id, status FROM bind_transactions "
            "WHERE plan_sha256=?",
            (plan_sha256,),
        ).fetchone()
    except Exception:
        return None
    if row is None or row["status"] != "committed" or not row["binding_id"]:
        return None
    return row


def _authorization_row(con, authorization_ref):
    if not isinstance(authorization_ref, dict):
        return None
    spec = KIND_TABLE.get(authorization_ref.get("authorization_kind"))
    auth_id = authorization_ref.get("authorization_id")
    if spec is None or not isinstance(auth_id, str):
        return None
    table, column = spec
    try:
        return con.execute(
            "SELECT * FROM %s WHERE %s=?" % (table, column),
            (auth_id,),
        ).fetchone()
    except Exception:
        return None


def _in_transaction(state_root, bootstrap_authority_path, request, now_ms,
                    con):
    authorization_ref = request["authorization_ref"]
    bind_context = request["bind_context"]
    trigger_kind = request["trigger_kind"]
    fp_receipt = _fingerprint_receipt(authorization_ref)
    subject = bind_context.get("subject") if isinstance(bind_context, dict) \
        else None
    pre_mode = None
    if isinstance(subject, dict):
        try:
            pre_mode, _pre_gen = bind_grant_store._mode_row(
                con, state_root, subject)
        except Exception:
            pre_mode = None

    try:
        bind_plan.verify_plan(
            state_root, bootstrap_authority_path, con, None, None, now_ms,
            allow_prepared=True)
    except Exception:
        pass

    try:
        consume_result = bind_grant_store.consume_for_bind_in_transaction(
            state_root, con, authorization_ref, bind_context, now_ms,
            authority_path=bootstrap_authority_path)
    except authority_store.AuthorityError as err:
        consume_result = _refuse(err.reason, rc=err.rc, receipt=fp_receipt)
    except Exception:
        consume_result = _refuse("d17_internal_error", rc=2, receipt=fp_receipt)
    if not isinstance(consume_result, dict):
        consume_result = _refuse("d17_internal_error", rc=2, receipt=fp_receipt)

    plan_sha256 = authorization_ref.get("plan_sha256") \
        if isinstance(authorization_ref, dict) else None
    plan, capability = _load_prepared(con, plan_sha256)
    if plan is not None:
        try:
            bind_plan.verify_plan(
                state_root, bootstrap_authority_path, con, plan, capability,
                now_ms, allow_prepared=True)
        except Exception:
            pass

    apply_request = {"operation": "apply"}
    if plan is not None and capability is not None \
            and consume_result.get("ok"):
        apply_request["plan"] = plan
        apply_request["capability"] = capability

    try:
        apply_result = bind_apply.apply_in_transaction(
            con, state_root, bootstrap_authority_path, apply_request, now_ms)
    except authority_store.AuthorityError as err:
        apply_result = {
            "ok": False, "reason": err.reason, "rc": err.rc,
        }
    except Exception:
        apply_result = {
            "ok": False, "reason": "bind_apply_internal_error", "rc": 2,
        }
    if not isinstance(apply_result, dict):
        apply_result = {
            "ok": False, "reason": "bind_apply_internal_error", "rc": 2,
        }

    mode = consume_result.get("mode", "remind_only")
    mode_generation = consume_result.get("mode_generation", 0)
    if type(mode_generation) is not int:
        mode_generation = 0

    if not consume_result.get("ok"):
        reason = str(consume_result.get("reason") or "authorization_ref_invalid")
        rc = int(consume_result.get("rc") or 1)
        if reason in CONSUMED_REASONS \
                and _binding_for_plan(con, plan_sha256) is None:
            reason = "d17_authorization_consumed_without_binding"
        _abort(reason, rc=rc, receipt=fp_receipt, mode=mode,
               mode_generation=mode_generation)

    kind = authorization_ref.get("authorization_kind")
    if kind == "grant" and pre_mode not in (None, "scoped_auto"):
        _abort("d17_request_invalid", rc=2, receipt=fp_receipt,
               mode=mode, mode_generation=mode_generation)
    if kind == "confirmation" and pre_mode not in (None, "confirm_each"):
        _abort("d17_request_invalid", rc=2, receipt=fp_receipt,
               mode=mode, mode_generation=mode_generation)

    auth_row = _authorization_row(con, authorization_ref)
    if auth_row is not None:
        try:
            record = json.loads(auth_row["record_json"])
            allowed = record.get("scope", {}).get("allowed_triggers")
        except (TypeError, ValueError, AttributeError):
            allowed = None
        if isinstance(allowed, list) and trigger_kind not in allowed:
            _abort("authorization_scope_mismatch", rc=1, receipt=fp_receipt,
                   mode=mode, mode_generation=mode_generation)

    if kind == "grant" and plan is not None:
        try:
            new_fp = bind_target.target_fingerprint(
                bind_target.from_verified_plan(plan))
            current = binding_store.current_binding(
                con, plan["project_id"], plan["fleet_id"],
                plan["fleet_member_id"])
            if current is not None and current["status"] == "active":
                current_obj = json.loads(current["binding_json"])
                current_fp = bind_target.target_fingerprint(
                    bind_target.from_verified_plan(current_obj))
                if current_fp != new_fp:
                    _abort("d17_scoped_auto_different_target", rc=1,
                           receipt=fp_receipt, mode=mode,
                           mode_generation=mode_generation)
        except Exception:
            pass

    if not apply_result.get("ok") \
            or apply_result.get("reason") not in APPLY_OK:
        reason = str(apply_result.get("reason") or "bind_apply_internal_error")
        rc = int(apply_result.get("rc") or 1)
        _abort(reason, rc=rc, receipt=fp_receipt, mode=mode,
               mode_generation=mode_generation)

    try:
        target_fp = bind_target.target_fingerprint(
            bind_target.from_verified_plan(plan))
    except Exception:
        _abort("d17_effect_fingerprint_unretrievable", rc=1,
               receipt=fp_receipt, mode=mode, mode_generation=mode_generation)

    binding = apply_result.get("binding") or {}
    apply_receipt = apply_result.get("receipt") or {}
    fp_value = effect_fingerprint(authorization_ref)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": "d17d-" + fp_value[:32],
        "operation": "apply_authorized",
        "reason": "bind_applied",
        "authorization_kind": authorization_ref["authorization_kind"],
        "authorization_id": authorization_ref["authorization_id"],
        "authorization_generation": authorization_ref["authorization_generation"],
        "authorization_receipt_sha256": authorization_ref[
            "authorization_receipt_sha256"],
        "plan_sha256": authorization_ref["plan_sha256"],
        "target_fingerprint": target_fp,
        "effect_fingerprint": fp_value,
        "binding_id": binding.get("binding_id"),
        "binding_generation": binding.get("generation"),
        "binding_receipt_sha256": apply_receipt.get("receipt_sha256"),
        "bind_transaction_id": apply_result.get("transaction_id"),
        "effect_count": 1,
        "reminder_count": 0,
        "mode": mode,
        "mode_generation": mode_generation,
        "committed_at_ms": int(now_ms),
        "plan": plan,
    }
    if receipt["effect_fingerprint"] != effect_fingerprint(receipt):
        _abort("d17_effect_fingerprint_unretrievable", rc=1,
               receipt=fp_receipt, mode=mode, mode_generation=mode_generation)
    return {
        "ok": True,
        "reason": "bind_applied",
        "rc": 0,
        "effect_count": 1,
        "reminder_count": 0,
        "mode": mode,
        "mode_generation": mode_generation,
        "receipt": receipt,
    }


def execute(state_root, bootstrap_authority_path, request, now_ms):
    try:
        refused = _validate_request(request, now_ms)
        if refused is not None:
            return refused
        con = authority_store.open_db(state_root)
        try:
            binding_store.tables(con)
            bind_grant_store._tables(con)
            with authority_store.immediate(con):
                return _in_transaction(
                    state_root, bootstrap_authority_path, request, now_ms, con)
        finally:
            con.close()
    except _Abort as abort:
        return abort.result
    except authority_store.AuthorityError as err:
        return _refuse(err.reason, rc=err.rc)
    except Exception:
        return _refuse("d17_internal_error", rc=2)
