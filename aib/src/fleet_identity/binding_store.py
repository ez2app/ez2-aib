#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared durable state and exact-byte helpers for the P1A binding pipeline.

This module is deliberately internal.  Public callers use the five Wave1
``execute`` entry points.  Local capabilities are authenticated only to stop
accidental/tampered replay inside this state root; they are explicitly not
owner or Main approval receipts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from pathlib import Path

from . import authority_store as auth


CAPABILITY_DOMAIN = b"ez2-aib/p1a-local-capability/v1\0"
RECEIPT_DOMAIN = b"ez2-aib/p1a-local-receipt/v1\0"


def tables(con):
    con.executescript("""
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
    CREATE TABLE IF NOT EXISTS bind_transactions(
      transaction_id TEXT PRIMARY KEY,
      plan_sha256 TEXT NOT NULL,
      capability_id TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL CHECK(status IN ('prepared','committed','abandoned')),
      binding_id TEXT,
      plan_json TEXT NOT NULL,
      capability_json TEXT NOT NULL,
      result_json TEXT,
      prepared_at_ms INTEGER NOT NULL,
      updated_at_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS active_bindings(
      project_id TEXT NOT NULL,
      fleet_id TEXT NOT NULL,
      fleet_member_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','source_revoked','inactive')),
      binding_id TEXT NOT NULL UNIQUE,
      binding_json TEXT NOT NULL,
      receipt_json TEXT NOT NULL,
      updated_at_ms INTEGER NOT NULL,
      PRIMARY KEY(project_id,fleet_id,fleet_member_id)
    );
    CREATE TABLE IF NOT EXISTS binding_history(
      binding_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      fleet_id TEXT NOT NULL,
      fleet_member_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','source_revoked','inactive')),
      binding_json TEXT NOT NULL,
      receipt_json TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS onboarding_read_receipts(
      read_receipt_id TEXT PRIMARY KEY,
      binding_id TEXT NOT NULL,
      binding_generation INTEGER NOT NULL,
      project_id TEXT NOT NULL,
      native_root_id TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','revoked')),
      receipt_json TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS onboarding_consumers(
      consumer_kind TEXT NOT NULL,
      consumer_id TEXT NOT NULL,
      read_receipt_id TEXT NOT NULL,
      binding_id TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','revoked')),
      created_at_ms INTEGER NOT NULL,
      PRIMARY KEY(consumer_kind,consumer_id)
    );
    CREATE TABLE IF NOT EXISTS handoff_capabilities(
      capability_id TEXT PRIMARY KEY,
      capability_hash TEXT NOT NULL,
      binding_id TEXT NOT NULL,
      binding_generation INTEGER NOT NULL,
      policy_sha256 TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('issued','consumed','revoked')),
      expires_at_ms INTEGER NOT NULL,
      created_at_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS revocation_fanouts(
      fanout_key TEXT PRIMARY KEY,
      fanout_generation INTEGER NOT NULL,
      subject_kind TEXT NOT NULL,
      subject_id TEXT NOT NULL,
      source_generation INTEGER NOT NULL,
      invalidated_json TEXT NOT NULL,
      receipt_json TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL
    );
    """)


def key_path(state_root):
    return auth.binding_key_path(state_root)


def local_key(state_root):
    path = key_path(state_root)
    if path.is_symlink() or not path.is_file():
        raise auth.AuthorityError("bind_local_key_unavailable")
    try:
        value = path.read_bytes()
    except OSError:
        raise auth.AuthorityError("bind_local_key_unavailable")
    if len(value) != 32:
        raise auth.AuthorityError("bind_local_key_invalid")
    return value


def canonical(value):
    return auth.canonical_bytes(value) + b"\n"


def hash_value(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_fingerprint(path, reason):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise auth.AuthorityError(reason, rc=1)
    return raw, hashlib.sha256(raw).hexdigest()


def new_id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex)


def sign_capability(state_root, body):
    value = dict(body)
    value["signature"] = hmac.new(
        local_key(state_root), CAPABILITY_DOMAIN + canonical(body),
        hashlib.sha256).hexdigest()
    return value


def verify_capability(state_root, value, required_keys, reason):
    if not isinstance(value, dict) or set(value) != set(required_keys) | {"signature"}:
        raise auth.AuthorityError(reason, rc=1)
    supplied = value.get("signature")
    if not isinstance(supplied, str):
        raise auth.AuthorityError(reason, rc=1)
    body = {name: value[name] for name in required_keys}
    expected = hmac.new(
        local_key(state_root), CAPABILITY_DOMAIN + canonical(body),
        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise auth.AuthorityError(reason, rc=1)
    return body


def seal_receipt(state_root, body):
    value = dict(body)
    value["receipt_sha256"] = hmac.new(
        local_key(state_root), RECEIPT_DOMAIN + canonical(body),
        hashlib.sha256).hexdigest()
    return value


def verify_receipt(state_root, value, reason="bind_receipt_tampered"):
    if not isinstance(value, dict) or not isinstance(value.get("receipt_sha256"), str):
        raise auth.AuthorityError(reason, rc=1)
    body = {name: item for name, item in value.items()
            if name != "receipt_sha256"}
    expected = hmac.new(
        local_key(state_root), RECEIPT_DOMAIN + canonical(body),
        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(value["receipt_sha256"], expected):
        raise auth.AuthorityError(reason, rc=1)
    return body


def json_load(raw, reason):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise auth.AuthorityError(reason, rc=1)
    if not isinstance(value, dict):
        raise auth.AuthorityError(reason, rc=1)
    return value


def current_binding(con, project_id, fleet_id, member_id):
    return con.execute(
        """SELECT * FROM active_bindings WHERE project_id=? AND fleet_id=?
           AND fleet_member_id=?""", (project_id, fleet_id, member_id),
    ).fetchone()


def binding_from_receipt(state_root, con, receipt,
                         tamper_reason="bind_receipt_tampered"):
    verify_receipt(state_root, receipt, tamper_reason)
    binding_id = receipt.get("binding_id")
    row = con.execute(
        "SELECT * FROM binding_history WHERE binding_id=?", (binding_id,),
    ).fetchone()
    if row is None or row["receipt_json"] != auth.canonical_bytes(receipt).decode(
            "utf-8"):
        raise auth.AuthorityError(tamper_reason, rc=1)
    binding = json_load(row["binding_json"], tamper_reason)
    if receipt.get("binding_sha256") != auth.sha256_hex(
            auth.canonical_bytes(binding)):
        raise auth.AuthorityError(tamper_reason, rc=1)
    return row, binding


def verified_active_binding(state_root, con, row,
                            tamper_reason="bind_binding_state_tampered"):
    """Verify the active cache row against its sealed append-only history."""
    if row is None:
        raise auth.AuthorityError("bind_binding_not_found", rc=1)
    binding = json_load(row["binding_json"], tamper_reason)
    receipt = json_load(row["receipt_json"], tamper_reason)
    verify_receipt(state_root, receipt, tamper_reason)
    history = con.execute(
        "SELECT * FROM binding_history WHERE binding_id=?", (row["binding_id"],),
    ).fetchone()
    if history is None \
            or history["binding_json"] != row["binding_json"] \
            or history["receipt_json"] != row["receipt_json"] \
            or history["project_id"] != row["project_id"] \
            or history["fleet_id"] != row["fleet_id"] \
            or history["fleet_member_id"] != row["fleet_member_id"] \
            or int(history["generation"]) != int(row["generation"]) \
            or binding.get("binding_id") != row["binding_id"] \
            or int(binding.get("generation", -1)) != int(row["generation"]) \
            or receipt.get("binding_id") != row["binding_id"] \
            or int(receipt.get("binding_generation", -1)) != int(row["generation"]) \
            or receipt.get("binding_sha256") != auth.sha256_hex(
                auth.canonical_bytes(binding)):
        raise auth.AuthorityError(tamper_reason, rc=1)
    return binding, receipt


def validate_binding_sources(state_root, con, binding, now_ms, *, source_reason,
                             lease_reason):
    """Fence every effect/read on current project, root and member truth."""
    project = con.execute(
        "SELECT * FROM projects WHERE project_id=?", (binding["project_id"],),
    ).fetchone()
    if project is None or project["status"] != "active" \
            or int(project["generation"]) != int(
                binding["generations"]["project"]):
        raise auth.AuthorityError(source_reason, rc=1)
    project_doc = json_load(project["project_json"], source_reason)
    if project_doc.get("fleet_id") != binding["fleet_id"] \
            or project_doc.get("memory_scope_id") != \
            binding["memory_scope_id"] \
            or project_doc.get("memory_store_ref") != \
            binding["memory_store_ref"] \
            or project_doc.get("memory_schema_version") != \
            binding["memory_schema_version"]:
        raise auth.AuthorityError(source_reason, rc=1)
    root = con.execute(
        "SELECT * FROM active_native_roots WHERE native_root_id=?",
        (binding["native_root_id"],),
    ).fetchone()
    if root is None or root["status"] != "active" \
            or int(root["receipt_generation"]) != int(
                binding["generations"]["active_root_receipt"]) \
            or int(root["root_generation"]) != int(
                binding["generations"]["native_root"]) \
            or int(root["expires_at_ms"]) <= int(now_ms) \
            or root["receipt_path"] != binding["native_root_receipt_path"] \
            or root["receipt_sha256"] != \
            binding.get("native_root_receipt_sha256") \
            or root["root_path"] != binding["root_path"] \
            or root["root_sha256"] != binding.get("root_sha256"):
        raise auth.AuthorityError(source_reason, rc=1)
    from . import native_root
    try:
        root_receipt = native_root._read_receipt(root["receipt_path"])
        current_root_sha256 = native_root._root_hash(Path(root["root_path"]))
    except auth.AuthorityError as err:
        raise auth.AuthorityError(source_reason, rc=1) from err
    if root_receipt.get("receipt_sha256") != root["receipt_sha256"] \
            or root_receipt.get("root_sha256") != root["root_sha256"] \
            or current_root_sha256 != root["root_sha256"]:
        raise auth.AuthorityError(source_reason, rc=1)
    # ★★ C2.6(裁定 RULING-C2a,cap `a seat` 04:2x;判準 097fff9f §5):
    #   **writer liveness 的判準要共用,不是每條路各判一次。**
    #   在此之前:`bind_plan` 用 `native_root._live_writer` 擋得住換版後的 root,
    #   而 `onboarding_recall` 走的是本函式這一組 source 檢查 —— 它查了狀態、世代、
    #   到期、路徑、sha,**就是沒問「簽這張收據的 writer 還活著嗎」**。
    #   於是 root 換版之後 bind 紅而 recall 綠:**同一份 binding,兩條路兩個答案。**
    #   ★ qa `a seat` 的反思逐字:三處各自綠會掩蓋它們之間沒有共用 liveness 判準。
    #
    #   修在這裡而不是修 recall 一處的理由:本函式今天有 **4 個呼叫點、跨 3 個檔**
    #   (`bind_apply.py:310` / `onboarding_recall.py:66` / `onboarding_recall.py:228`
    #    / `seat_continuity.py:50`),
    #   ★ 更正(`a seat` 08-30 01:38 打回,cap 05:2x 裁定落碼):本行原寫「五個消費者」。
    #     `git grep validate_binding_sources -- src/` 回**五行**,而其中
    #     `binding_store.py:257` 是 **def 定義**不是呼叫點 —— 我把 grep 的行數當成呼叫點數。
    #   ★★ 而**正確的清單當時就寫在錯的數旁邊**:括號裡列的正好是 4。
    #     **聚合值與它自己的枚舉在同一行對不上,而沒有任何東西會因此變紅。**
    #     數是 grep 給的,清單是自己列的,我信了 grep 那個。
    #     ⇒ 判準:`grep` 的行數不是呼叫點數;要嘛排掉定義行,要嘛報「檔 × 呼叫」兩個維度,
    #     不要報一個壓成一維、又沒有跟旁邊枚舉對過的數。
    #   補在 recall 只會讓下一條路再犯一次 —— **而那一次一樣不會有人發現**。
    #   ★ 用的是 `native_root._live_writer` **本人**,不是照它再寫一份近似判斷:
    #     兩份判準會在某一天各自回不同的答案,而那一天沒有人在看。
    try:
        current_liveness = native_root._live_writer(
            root["liveness_path"], root["producer_id"],
            int(root["producer_generation"]), now_ms)
    except auth.AuthorityError as err:
        raise auth.AuthorityError(source_reason, rc=1) from err
    if current_liveness != root_receipt.get("liveness_sha256"):
        raise auth.AuthorityError(source_reason, rc=1)
    fleet = con.execute(
        "SELECT * FROM fleet_manifests WHERE fleet_id=?", (binding["fleet_id"],),
    ).fetchone()
    lease = con.execute(
        """SELECT * FROM fleet_member_leases WHERE fleet_id=?
           AND fleet_member_id=?""",
        (binding["fleet_id"], binding["fleet_member_id"]),
    ).fetchone()
    if lease is None or lease["status"] != "active" \
            or lease["lease_id"] != binding["lease_id"] \
            or int(lease["generation"]) != int(binding["lease_generation"]) \
            or lease["principal_id"] != binding["principal_id"] \
            or lease["principal_seat_id"] != binding["seat_id"] \
            or lease["principal_session_uuid"] != binding["session_uuid"] \
            or int(lease["lease_until_ms"]) <= int(now_ms):
        raise auth.AuthorityError(lease_reason, rc=1)
    if fleet is None or fleet["status"] != "active" \
            or int(fleet["generation"]) != int(
                binding["generations"]["fleet"]) \
            or fleet["project_id"] != binding["project_id"]:
        raise auth.AuthorityError(source_reason, rc=1)
    from . import fleet_members
    manifest = json_load(fleet["manifest_json"], source_reason)
    member_matches = [
        member for member in manifest.get("members", [])
        if isinstance(member, dict)
        and member.get("fleet_member_id") == binding["fleet_member_id"]
    ]
    if len(member_matches) != 1:
        raise auth.AuthorityError(source_reason, rc=1)
    member = member_matches[0]
    try:
        assignment = fleet_members.model_assignment(
            manifest, binding["fleet_member_id"], binding["provider"])
    except auth.AuthorityError as err:
        raise auth.AuthorityError(source_reason, rc=1) from err
    if binding.get("model_policy_sha256") != auth.sha256_hex(
                auth.canonical_bytes(manifest["model_policy"])) \
            or binding.get("model_assignment_sha256") != auth.sha256_hex(
                auth.canonical_bytes(assignment)) \
            or binding.get("requested_model") != assignment["requested_model"] \
            or binding.get("effective_model") not in \
            assignment["allowed_effective_models"] \
            or binding.get("fleet_member_sha256") != auth.sha256_hex(
                auth.canonical_bytes(member)) \
            or binding.get("role_code") != member.get("role_code") \
            or binding.get("task_lane") != member.get("task_lane") \
            or binding.get("persona_uid") != member.get("persona_uid") \
            or binding.get("persona_sha256") != member.get("persona_version") \
            or binding.get("memory_scope_id") != member.get("memory_scope_id"):
        raise auth.AuthorityError(source_reason, rc=1)
    persona = con.execute(
        "SELECT * FROM published_personas WHERE persona_uid=?",
        (binding["persona_uid"],),
    ).fetchone()
    if persona is None or persona["lifecycle"] != "published" \
            or int(persona["catalog_generation"]) != int(
                binding["generations"]["persona_catalog"]) \
            or persona["persona_sha256"] != binding["persona_sha256"] \
            or persona["source_path"] != binding["persona_source_path"]:
        raise auth.AuthorityError(source_reason, rc=1)
    _persona_raw, persona_sha256 = file_fingerprint(
        binding["persona_source_path"], source_reason)
    if persona_sha256 != binding["persona_sha256"]:
        raise auth.AuthorityError(source_reason, rc=1)
    checkpoint_raw, checkpoint_sha256 = file_fingerprint(
        binding["checkpoint_path"], source_reason)
    try:
        checkpoint = json.loads(checkpoint_raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise auth.AuthorityError(source_reason, rc=1) from exc
    if checkpoint_sha256 != binding["checkpoint_sha256"] \
            or checkpoint.get("schema") != "ez2-native-checkpoint/v1" \
            or checkpoint.get("project_id") != binding["project_id"] \
            or checkpoint.get("fleet_member_id") != \
            binding["fleet_member_id"] \
            or checkpoint.get("generation") != \
            binding["checkpoint_generation"]:
        raise auth.AuthorityError(source_reason, rc=1)
    # Keep the immutable observation used by the binding, but also fence every
    # later effect/read on the session's current *fresh* runtime head.  A
    # periodic same-model refresh is allowed to advance the head without
    # rewriting old evidence.  A stale head or any effective-model change
    # invalidates the active binding until it is explicitly rebound.
    from . import runtime_model_observation
    observation_lock = None
    try:
        observation_lock = runtime_model_observation.acquire_session_lock(
            state_root, binding.get("session_uuid"), exclusive=False,
            create=False)
        observation = runtime_model_observation.verify(
            state_root, binding.get("model_observation"),
            binding.get("session_uuid"), binding.get("provider"), now_ms,
            require_fresh=False)
        current_observation = runtime_model_observation.current_unlocked(
            state_root, binding.get("session_uuid"),
            binding.get("provider"), now_ms, require_fresh=True)
    except auth.AuthorityError as err:
        raise auth.AuthorityError(
            "binding_model_observation_invalid", rc=1) from err
    finally:
        if observation_lock is not None:
            runtime_model_observation.release_session_lock(observation_lock)
    if observation["effective_model"] != binding.get("effective_model") \
            or current_observation["effective_model"] != \
            binding.get("effective_model") \
            or current_observation["observed_at_ms"] < \
            observation["observed_at_ms"]:
        raise auth.AuthorityError(
            "binding_model_observation_invalid", rc=1)
    return binding


def store_binding(con, state_root, binding, now_ms):
    receipt = seal_receipt(state_root, {
        "schema": "ez2-aib/seat-binding-receipt/v1",
        "binding_id": binding["binding_id"],
        "binding_generation": binding["generation"],
        "binding_sha256": auth.sha256_hex(auth.canonical_bytes(binding)),
        "project_id": binding["project_id"],
        "fleet_id": binding["fleet_id"],
        "fleet_member_id": binding["fleet_member_id"],
        "principal_id": binding["principal_id"],
        "seat_id": binding["seat_id"],
        "session_uuid": binding["session_uuid"],
        "provider": binding["provider"],
        "requested_model": binding["requested_model"],
        "effective_model": binding["effective_model"],
        "model_observation_sha256":
            binding["model_observation"]["observation_sha256"],
        "model_policy_sha256": binding["model_policy_sha256"],
        "model_assignment_sha256": binding["model_assignment_sha256"],
        "fleet_member_sha256": binding["fleet_member_sha256"],
        "role_code": binding["role_code"],
        "task_lane": binding["task_lane"],
        "persona_uid": binding["persona_uid"],
        "persona_sha256": binding["persona_sha256"],
        "native_root_id": binding["native_root_id"],
        "native_root_receipt_generation":
            binding["generations"]["active_root_receipt"],
        "native_root_receipt_sha256": binding[
            "native_root_receipt_sha256"],
        "root_sha256": binding["root_sha256"],
        "created_at_ms": int(now_ms),
        "formal_approval": False,
    })
    binding_json = auth.canonical_bytes(binding).decode("utf-8")
    receipt_json = auth.canonical_bytes(receipt).decode("utf-8")
    con.execute(
        """INSERT INTO binding_history
           (binding_id,project_id,fleet_id,fleet_member_id,generation,status,
            binding_json,receipt_json,created_at_ms)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (binding["binding_id"], binding["project_id"], binding["fleet_id"],
         binding["fleet_member_id"], binding["generation"], binding["status"],
         binding_json, receipt_json, int(now_ms)),
    )
    con.execute(
        """INSERT INTO active_bindings
           (project_id,fleet_id,fleet_member_id,generation,status,binding_id,
            binding_json,receipt_json,updated_at_ms)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id,fleet_id,fleet_member_id) DO UPDATE SET
             generation=excluded.generation,status=excluded.status,
             binding_id=excluded.binding_id,binding_json=excluded.binding_json,
             receipt_json=excluded.receipt_json,updated_at_ms=excluded.updated_at_ms""",
        (binding["project_id"], binding["fleet_id"],
         binding["fleet_member_id"], binding["generation"], binding["status"],
         binding["binding_id"], binding_json, receipt_json, int(now_ms)),
    )
    return receipt
