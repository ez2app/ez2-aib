# SPDX-License-Identifier: Apache-2.0
"""D17 shared target owner —— target scope 與 authority scope 的唯一擁有者。

裁定來源:0378c 09-01 06:16 / 06:29 / 07:03(target/B-P1A scope 契約)。
本模組是 A(bind_attention)、B(bind_grant_store)與兩份 binding JSON schema
共同的 target 真相來源;它們**不得**各自再寫一份 SCOPE_KEYS 或 target hash。

三軸互不折疊:
  target             target_fingerprint(target)       只有七件事實會動它
  authority scope    authority_scope_sha256(scope)    事實 或 allowed_triggers
  authorization      plan.plan_sha256                 plan 任一位元組(永不進 target)

純函式:不碰資料庫、不碰狀態、不碰檔案、不碰環境變數、不碰時鐘與亂數。
"""

from __future__ import annotations

import hashlib
import json

TARGET_SCHEMA = "ez2-aib/d17-bind-target/v1"
TARGET_INPUT_SCHEMA = "ez2-aib/d17-bind-target-fingerprint-input/v1"
SCOPE_INPUT_SCHEMA = "ez2-aib/d17-bind-authority-scope-hash-input/v1"
MEMREF_INPUT_SCHEMA = "ez2-aib/memory-store-ref-fingerprint-input/v1"

BEHAVIOUR = "d17_target_behaviour_mismatch"
MISMATCH = "target_fingerprint_mismatch"

STRING_KEYS = frozenset({
    "schema", "project_id", "fleet_id", "fleet_member_id",
    "fleet_member_sha256", "lease_id", "persona_uid", "persona_sha256",
    "native_root_id", "native_root_receipt_sha256", "root_sha256",
    "checkpoint_relpath", "checkpoint_sha256", "memory_scope_id",
    "memory_store_ref_sha256",
    # ★ 對著真產物寫:project_registry 的 memory_schema_version 是字串
    #   (例 "ez2-native-memory/v1"),不是整數。第一版我照想像寫成 int,
    #   真 plan 一進來就在 validate_target 被自己擋掉。
    "memory_schema_version",
})
INT_KEYS = frozenset({
    "project_generation", "fleet_generation", "lease_generation",
    "persona_catalog_generation", "native_root_receipt_generation",
    "root_generation", "checkpoint_generation",
})
TARGET_KEYS = STRING_KEYS | INT_KEYS
SHA_KEYS = frozenset({
    "fleet_member_sha256", "persona_sha256", "native_root_receipt_sha256",
    "root_sha256", "checkpoint_sha256", "memory_store_ref_sha256",
})
SCOPE_KEYS = frozenset((TARGET_KEYS - {"schema"}) | {"allowed_triggers"})
# BN 的 notification scope:06:16 第 1 條「notification scope 同步改 member SHA」。
NOTIFICATION_SCOPE_KEYS = frozenset({
    "cabin_id", "cabin_generation", "fleet_id", "fleet_generation",
    "fleet_member_id", "fleet_member_sha256", "lease_id",
    "lease_generation", "project_id", "project_generation", "seat_id",
    "session_uuid",
})
# 07:03 第 2 條:prepared plan 權威查詢的六個具名拒絕,封閉集合,只在這裡定義。
PREPARED_REFUSALS = frozenset({
    "prepared_plan_authority_missing",   # bootstrap authority 未給:fail-closed
    "prepared_plan_not_found",
    "prepared_plan_not_unique",
    "prepared_plan_row_mismatch",
    "prepared_plan_status_invalid",
    "prepared_plan_source_drift",
    "prepared_capability_expired",
})
LEGAL_TRIGGERS = (["ctx_threshold"], ["content"], ["ctx_threshold", "content"])
ORDERING_KEYS = INT_KEYS
HEX = frozenset("0123456789abcdef")
B_PATHS = ("stage", "confirm", "grant", "consume")


class TargetError(Exception):
    pass


def _red(detail):
    raise TargetError("%s: %s" % (BEHAVIOUR, detail))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


WILDCARD_CHARS = frozenset("*?[]")


def _plain_string(value):
    """B 原本的字串圍欄:非空、不含萬用字元、不含 NUL。owner 接手後不得比它鬆。"""
    return (isinstance(value, str) and bool(value)
            and not any(char in value for char in WILDCARD_CHARS)
            and "\x00" not in value)


def _is_hex64(value):
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= HEX)


def _is_exact_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def memory_store_ref_sha256(ref):
    if not isinstance(ref, str) or not ref:
        _red("memory_store_ref 不是非空字串")
    return _sha({"schema": MEMREF_INPUT_SCHEMA, "memory_store_ref": ref})


def from_verified_plan(plan):
    if not isinstance(plan, dict):
        _red("plan 不是 dict")
    gens = plan.get("generations")
    if not isinstance(gens, dict):
        _red("plan.generations 不是 dict")
    if gens.get("native_root") != plan.get("root_generation"):
        _red("generations.native_root 與 plan.root_generation 不等")
    target = {
        "schema": TARGET_SCHEMA,
        "project_id": plan["project_id"],
        "project_generation": gens["project"],
        "fleet_id": plan["fleet_id"],
        "fleet_generation": gens["fleet"],
        "fleet_member_id": plan["fleet_member_id"],
        "fleet_member_sha256": plan["fleet_member_sha256"],
        "lease_id": plan["lease_id"],
        "lease_generation": plan["lease_generation"],
        "persona_uid": plan["persona_uid"],
        "persona_sha256": plan["persona_sha256"],
        "persona_catalog_generation": gens["persona_catalog"],
        "native_root_id": plan["native_root_id"],
        "native_root_receipt_generation": gens["active_root_receipt"],
        "native_root_receipt_sha256": plan["native_root_receipt_sha256"],
        "root_sha256": plan["root_sha256"],
        "root_generation": gens["native_root"],
        "checkpoint_relpath": plan["checkpoint_relpath"],
        "checkpoint_generation": plan["checkpoint_generation"],
        "checkpoint_sha256": plan["checkpoint_sha256"],
        "memory_scope_id": plan["memory_scope_id"],
        "memory_store_ref_sha256": memory_store_ref_sha256(
            plan["memory_store_ref"]),
        "memory_schema_version": plan["memory_schema_version"],
    }
    validate_target(target)
    return target


def validate_target(target):
    if not isinstance(target, dict):
        _red("target 不是 dict")
    got = set(target)
    if got - TARGET_KEYS:
        _red("target 有多餘欄位 %s" % sorted(got - TARGET_KEYS))
    if TARGET_KEYS - got:
        _red("target 缺欄位 %s" % sorted(TARGET_KEYS - got))
    if target["schema"] != TARGET_SCHEMA:
        _red("target schema 不是 %s" % TARGET_SCHEMA)
    for key in sorted(STRING_KEYS):
        if not _plain_string(target[key]):
            _red("target.%s 不是非空、無萬用字元、無 NUL 的字串" % key)
    for key in sorted(INT_KEYS):
        if not _is_exact_int(target[key]) or target[key] < 0:
            _red("target.%s 不是非負 exact int" % key)
    for key in sorted(SHA_KEYS):
        if not _is_hex64(target[key]):
            _red("target.%s 不是 lower hex64" % key)
    return True


def validate_authority_scope(scope):
    if not isinstance(scope, dict):
        _red("authority scope 不是 dict")
    got = set(scope)
    if got != SCOPE_KEYS:
        _red("authority scope 欄位集合不符,多 %s 缺 %s"
             % (sorted(got - SCOPE_KEYS), sorted(SCOPE_KEYS - got)))
    triggers = scope["allowed_triggers"]
    if not isinstance(triggers, list) or list(triggers) not in [
            list(item) for item in LEGAL_TRIGGERS]:
        _red("allowed_triggers 不是三個合法 exact array 之一")
    probe = dict(scope)
    del probe["allowed_triggers"]
    probe["schema"] = TARGET_SCHEMA
    validate_target(probe)
    return True


def target_from_scope(scope):
    validate_authority_scope(scope)
    target = dict(scope)
    del target["allowed_triggers"]
    target["schema"] = TARGET_SCHEMA
    return target


def target_fingerprint(target):
    validate_target(target)
    return _sha({"schema": TARGET_INPUT_SCHEMA, "target": target})


def fingerprint_from_scope(scope):
    return target_fingerprint(target_from_scope(scope))


def authority_scope_sha256(scope):
    validate_authority_scope(scope)
    return _sha({"schema": SCOPE_INPUT_SCHEMA, "authority_scope": scope})


def require_fingerprint(value):
    """只做形狀。合法 lower hex64 一律原樣回傳。"""
    if not isinstance(value, str) or isinstance(value, bool):
        _red("fingerprint 不是字串")
    if not value:
        _red("fingerprint 是空字串")
    if len(value) != 64:
        _red("fingerprint 長度 %d 不是 64" % len(value))
    if not set(value) <= HEX:
        _red("fingerprint 不是 lower hex")
    return value


def verify_target_for(path, plan, request):
    """四路各自重算。合法但內容不同的 64hex 在這裡才被判定。"""
    if path not in B_PATHS:
        _red("未知的 B 路徑 %r" % (path,))
    supplied = require_fingerprint(request.get("target_fingerprint"))
    expected = target_fingerprint(from_verified_plan(plan))
    if supplied != expected:
        raise TargetError("%s: %s 於 %s 路;期待 %s 實得 %s"
                          % (BEHAVIOUR, MISMATCH, path, expected, supplied))
    return expected


def is_stale_against(field, observed, expected):
    if field in ORDERING_KEYS:
        if not _is_exact_int(observed) or not _is_exact_int(expected):
            _red("ordering 軸的 %s 收到非 exact int" % field)
        return observed < expected
    if field in SHA_KEYS:
        return observed != expected
    _red("未知欄位 %r 不在任何語意軸上" % (field,))


def store_known_fingerprint(value):
    """A 只原樣存已知 64hex,不再雜湊一次。"""
    return require_fingerprint(value)
