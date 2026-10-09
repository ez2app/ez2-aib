#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Durably consume a five-main-seat project launch plan.

This module does not create windows, principals, personas or native roots.
It only accepts main-seat identity from the current, already-authenticated
project request or from self-reported seat records cross-checked against the
active local principal registry.  Seat records never become verified identity.
Every member still enters through :mod:`project_entry`, so the existing
principal, persona, native-root, bind and recall checks remain the authority
boundary.

The journal is a recovery record, not another authority source.  A completed
member is replayed from its exact bind/read receipts and is never rebound.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

# 紅2(2026-08-27):席位目錄不自己拼——委派 src/cockpit/seats_dir_lib(EH.2/ED.1
# 的單一真相源,順位 HUB 導出 > 艙家 > 狀態根)。範本是
# `src/cockpit/seat_onboard.py:seats_dir` 的 docstring:那支的函式體也只有委派。
# 跨包載法沿用同一包裡 `ctx_scheduler.py` 的既有寫法(插 sys.path 再 import)。
_COCKPIT_DIR = Path(__file__).resolve().parents[1] / "cockpit"
if str(_COCKPIT_DIR) not in sys.path:
    sys.path.insert(0, str(_COCKPIT_DIR))
import seats_dir_lib  # noqa: E402

from . import authority_store as auth
from . import bind_apply
from . import fleet_members
from . import onboarding_recall
from . import project_entry
from . import runtime_model_observation


JOURNAL_SCHEMA = "ez2-aib/fleet-restore-journal/v1"
SEAT_RECEIPT_SCHEMA = "ez2-aib/main-seat-launch-receipt/v1"
_PLAN_KEYS = frozenset((
    "schema", "new_instance", "dry_run", "seat_count", "project_id",
    "fleet_id", "model_policy", "model_policy_sha256", "seats",
    "state_effect_count",
))
_PLAN_SEAT_KEYS = frozenset((
    "kind", "role", "provider", "preferred_provider",
    "provider_fallbacks", "requested_model", "allowed_effective_models",
    "fleet_member_id", "persona_uid", "memory_binding_ref",
))


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _journal_path(state_root, project_id, fleet_id):
    material = auth.canonical_bytes({
        "project_id": project_id,
        "fleet_id": fleet_id,
    })
    name = "restore-%s.json" % _sha256(material)[:24]
    return Path(state_root) / "fleet-restores" / name


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise auth.AuthorityError("fleet_restore_journal_unsafe", rc=1)
    raw = auth.canonical_bytes(value) + b"\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent),
                prefix=".%s." % path.name, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _read_journal(path, project_id, fleet_id):
    path = Path(path)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise auth.AuthorityError("fleet_restore_journal_unsafe", rc=1)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise auth.AuthorityError(
            "fleet_restore_journal_unreadable", rc=1) from exc
    if not isinstance(value, dict) \
            or value.get("schema") != JOURNAL_SCHEMA \
            or value.get("project_id") != project_id \
            or value.get("fleet_id") != fleet_id \
            or value.get("status") not in ("partial", "complete") \
            or not isinstance(value.get("plan"), dict) \
            or not isinstance(value.get("member_receipts"), list):
        raise auth.AuthorityError("fleet_restore_journal_invalid", rc=1)
    return value


def _registered_plan(state_root, project_query):
    """Resolve immutable project/fleet truth without applying a binding."""
    con = auth.open_db(state_root, readonly=True)
    try:
        matches = project_entry._registered_matches(
            con, project_entry._normalize_identity(project_query))
        if len(matches) != 1:
            return None
        project_row, project = matches[0]
        project = dict(project, project_id=project_row["project_id"])
        manifest = project_entry._manifest(con, project)
        return project_entry._launch_plan(project, manifest, 5)
    finally:
        con.close()


def _valid_plan(value, project_id=None, fleet_id=None):
    if not isinstance(value, dict) or set(value) != _PLAN_KEYS \
            or value.get("schema") != \
            project_entry.FLEET_RESTORE_PLAN_SCHEMA \
            or value.get("seat_count") != 5 \
            or value.get("new_instance") is not True \
            or value.get("dry_run") is not False:
        return False
    if project_id is not None and value.get("project_id") != project_id:
        return False
    if fleet_id is not None and value.get("fleet_id") != fleet_id:
        return False
    seats = value.get("seats")
    if not isinstance(seats, list) or len(seats) != 5:
        return False
    roles = []
    members = []
    policy_members = []
    for seat in seats:
        if not isinstance(seat, dict) or set(seat) != _PLAN_SEAT_KEYS \
                or seat.get("kind") != "main_ai":
            return False
        for key in ("role", "provider", "preferred_provider",
                    "fleet_member_id", "requested_model"):
            if not isinstance(seat.get(key), str) or not seat[key]:
                return False
        fallbacks = seat.get("provider_fallbacks")
        allowed = seat.get("allowed_effective_models")
        if not isinstance(fallbacks, list) \
                or not all(isinstance(item, str) and item for item in fallbacks) \
                or not isinstance(allowed, list) or not allowed \
                or not all(isinstance(item, str) and item for item in allowed) \
                or seat["provider"] not in (
                    [seat["preferred_provider"]] + fallbacks):
            return False
        roles.append(seat["role"])
        members.append(seat["fleet_member_id"])
        policy_members.append({
            "fleet_member_id": seat["fleet_member_id"],
            "preferred_provider": seat["preferred_provider"],
            "provider_fallbacks": fallbacks,
        })
    try:
        policy = fleet_members.validate_model_policy(
            value.get("model_policy"), policy_members)
    except auth.AuthorityError:
        return False
    if value.get("model_policy_sha256") != auth.sha256_hex(
            auth.canonical_bytes(policy)):
        return False
    for seat in seats:
        matches = [row for row in policy["assignments"]
                   if row["fleet_member_id"] == seat["fleet_member_id"]
                   and row["provider"] == seat["provider"]]
        if len(matches) != 1 \
                or seat["requested_model"] != matches[0]["requested_model"] \
                or seat["allowed_effective_models"] != \
                matches[0]["allowed_effective_models"]:
            return False
    return "root" in roles and len(set(roles)) == 5 \
        and len(set(members)) == 5


def _same_logical_plan(left, right):
    """Compare permanent fleet identity while allowing provider rotation."""
    if not _valid_plan(left) or not _valid_plan(right):
        return False
    if left.get("project_id") != right.get("project_id") \
            or left.get("fleet_id") != right.get("fleet_id") \
            or left.get("model_policy_sha256") != \
            right.get("model_policy_sha256"):
        return False
    keys = ("role", "fleet_member_id", "persona_uid",
            "memory_binding_ref", "preferred_provider", "provider_fallbacks")
    left_rows = [{key: row.get(key) for key in keys}
                 for row in left["seats"]]
    right_rows = [{key: row.get(key) for key in keys}
                  for row in right["seats"]]
    return left_rows == right_rows


def _request_is_current_main(authority_path, request, now_ms):
    project_entry._main_principal(authority_path, request)
    expires = request.get("expires_at_ms")
    if not isinstance(expires, int) or isinstance(expires, bool) \
            or expires <= now_ms or expires - now_ms > 5 * 60 * 1000:
        raise auth.AuthorityError("project_entry_expiry_invalid", rc=1)


def _principal_for_seat(authority_path, seat_id, session_uuid,
                        stated_principal=None):
    registry = auth.load_bootstrap_authority(authority_path)
    matches = []
    for principal_id, row in registry.get("principals", {}).items():
        if isinstance(row, dict) and row.get("kind") == "main_ai" \
                and row.get("status") == "active" \
                and row.get("seat_id") == seat_id \
                and row.get("session_uuid") == session_uuid:
            matches.append(principal_id)
    if len(matches) != 1:
        raise auth.AuthorityError(
            "fleet_restore_principal_ambiguous" if matches
            else "fleet_restore_principal_missing", rc=1)
    if stated_principal is not None and stated_principal != matches[0]:
        raise auth.AuthorityError("fleet_restore_principal_mismatch", rc=1)
    return matches[0]


def _seats_dir(state_root=None):
    """本**艙**的席位登記簿目錄;問不出是哪一艙時回 None(不退主艙)。

    ## 為什麼這裡不能只看艙家目錄

    2026-08-21 instance 16 開五席,`seat-onboard` 印出來的 seat id **五席全錯**:
    殘檔與活檔同檔名同窗號,只差一層目錄——`~/.ez2-aib/seats/w4.json` 是
    08-14 的死席,`~/.ez2-aib/instances/16/seats/w4.json` 才是活席。
    退主艙拿到的是一份**格式完全正確的錯答案**:不報錯、不變紅。
    ★★ **主艙不是回退來源,是衝突**;而今天的失效形狀是**回退成功了,
    而成功不會叫**。完整實測見 `src/cockpit/seat_onboard.py:seats_dir`。

    本函式讀的是 main-seat 的自述席位檔,雖然它們**永遠不會變成已驗證身分**
    (仍要走 `project_entry`),讀錯艙仍會把**別艙的**角色/席位當成本艙的
    launch 收據來源。所以解析順位交給單一真相源,而兩個明示來源互相矛盾時
    fail-closed —— 挑一邊就是猜。

    ## 為什麼要帶 state_root(2026-09-14,🧞 a seat,main push 被擋的八支之一)

    `execute()` 收到**明示的** state_root:journal、authority db、runtime
    observation 全在它底下;唯獨這裡以前不帶根,席位登記簿由環境(HUB /
    EZ2_AIB_INSTANCE_HOME / EZ2_AIB_STATE_ROOT)另外導出 ⇒ **同一次呼叫兩個根**。
    測試把 state_root 指到治具,呼叫端的殼卻帶著艙變數(任何席位殼都帶)⇒
    預檢讀到真艙 `instances/<艙>/seats/w2.json`(真 root 席)⇒ 第一個角色就對不上,
    `failed_role=root`;而在沒有艙變數的殼裡同一支測試綠 —— 答案取決於誰在哪個殼跑它。
    形狀與檔頭那一件同族:那件防的是「主艙 vs 本艙」,這件是「明示根 vs 環境根」。
    修法:環境說的**狀態根**(`seats_dir_lib.state_root()`:EZ2_AIB_STATE_ROOT >
    EZ2_AIB_HOME > ~/.ez2-aib)必須就是呼叫端給的那個根,環境裡的艙證據(HUB /
    EZ2_AIB_INSTANCE_HOME / EZ2_SEATS_DIR)才算數;不是 ⇒ 環境在講**另一個世界**,
    對這一次呼叫沒有艙證據 ⇒ None ⇒ 沒有列 ⇒ seat_missing(保守側,不是 conflict:
    conflict 是兩個來源對**同一個**世界各說各話)。
    ★ 不能用「艙家目錄要在根底下」當判準:tests/test-p1-fleet-restore-forcepoint 把
    EZ2_AIB_INSTANCE_HOME 放在根的**旁邊**(`base/instances/2` vs `base/state`)而那是
    合法用法——它明說了 EZ2_AIB_STATE_ROOT=base/state,環境與呼叫端講的是同一個世界。
    production 不變:呼叫端的根就是環境的根(~/.ez2-aib),艙在它底下。
    """
    if state_root is not None:
        env_root, _root_src = seats_dir_lib.state_root()
        try:
            same_root = Path(env_root).expanduser().resolve() == Path(state_root).resolve()
        except (OSError, ValueError):
            same_root = False
        if not same_root:
            return None
    explicit = os.environ.get("EZ2_SEATS_DIR")
    derived, _source, conflict = seats_dir_lib.cabin_seats_dir_with_source()
    if conflict:
        raise auth.AuthorityError(
            "fleet_restore_cabin_source_mismatch", rc=1)
    if explicit and derived:
        if Path(explicit).resolve() != Path(derived).resolve():
            raise auth.AuthorityError(
                "fleet_restore_cabin_source_mismatch", rc=1)
    if explicit:
        return Path(explicit)
    if derived:
        return Path(derived)
    return None


def _seat_rows(state_root=None):
    directory = _seats_dir(state_root)
    if directory is None or not directory.is_absolute() \
            or not directory.is_dir() or directory.is_symlink():
        return {}
    rows = {}
    for path in sorted(directory.glob("w*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            row = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        role = row.get("role") or row.get("window_name")
        if not isinstance(role, str) or not role:
            continue
        # Duplicate role files are ambiguous cabin truth, not "last wins".
        if role in rows:
            rows[role] = None
            continue
        rows[role] = (path, raw, row)
    return rows


def _plan_model_assignment(plan, member_id, provider):
    assignments = plan.get("model_policy", {}).get("assignments", [])
    matches = [row for row in assignments if isinstance(row, dict)
               and row.get("fleet_member_id") == member_id
               and row.get("provider") == provider]
    if len(matches) != 1:
        raise auth.AuthorityError(
            "fleet_restore_model_policy_ambiguous" if matches
            else "fleet_restore_model_policy_assignment_missing", rc=1)
    return matches[0]


def _runtime_observation(state_root, session_uuid, provider, now_ms, *,
                         require_fresh=True):
    reason_map = {
        "runtime_model_observation_missing":
            "fleet_restore_model_observation_missing",
        "runtime_model_observation_stale":
            "fleet_restore_model_observation_stale",
        "runtime_model_observation_session_mismatch":
            "fleet_restore_model_observation_session_mismatch",
        "runtime_model_observation_provider_mismatch":
            "fleet_restore_model_observation_provider_mismatch",
        "runtime_model_observation_source_hash_mismatch":
            "fleet_restore_model_observation_source_hash_mismatch",
        "runtime_model_observation_hash_mismatch":
            "fleet_restore_model_observation_hash_mismatch",
        "runtime_model_observation_ambiguous":
            "fleet_restore_model_observation_ambiguous",
        "runtime_model_artifact_ambiguous":
            "fleet_restore_model_observation_ambiguous",
        "runtime_model_collector_unsupported_agy":
            "fleet_restore_model_observation_provider_unsupported",
        "runtime_model_collector_provider_unsupported":
            "fleet_restore_model_observation_provider_unsupported",
    }
    try:
        try:
            return runtime_model_observation.current(
                state_root, session_uuid, provider, now_ms,
                require_fresh=require_fresh)
        except auth.AuthorityError as current_err:
            if current_err.reason not in {
                    "runtime_model_observation_missing",
                    "runtime_model_observation_stale",
                    "runtime_model_source_not_latest",
            }:
                raise
        try:
            runtime_model_observation.collect(
                state_root, provider, session_uuid, now_ms)
        except auth.AuthorityError as collect_err:
            # A trusted collector may have run out of band with an exact
            # lsof/process-bound artifact path.  Missing default discovery is
            # therefore allowed to fall through, but the stored observation
            # must still prove its own artifact below.  Every other collector
            # error is an observed contradiction and stays fail-closed.
            if collect_err.reason != "runtime_model_artifact_missing":
                raise
        return runtime_model_observation.current(
            state_root, session_uuid, provider, now_ms,
            require_fresh=require_fresh)
    except auth.AuthorityError as err:
        raise auth.AuthorityError(
            reason_map.get(err.reason, "fleet_restore_model_observation_invalid"),
            rc=1) from err


def _committed_runtime_observation(state_root, launch, session_uuid, provider,
                                   now_ms):
    """Revalidate, but never replace, evidence of an existing binding."""
    observation = launch.get("model_observation") \
        if isinstance(launch, dict) else None
    try:
        verified = runtime_model_observation.verify(
            state_root, observation, session_uuid, provider, now_ms,
            require_fresh=False)
    except auth.AuthorityError as err:
        raise auth.AuthorityError(
            "fleet_restore_completed_model_observation_invalid", rc=1) from err
    if launch.get("model_observation_sha256") != \
            verified["observation_sha256"]:
        raise auth.AuthorityError(
            "fleet_restore_completed_model_observation_invalid", rc=1)
    newest = _runtime_observation(
        state_root, session_uuid, provider, now_ms, require_fresh=True)
    if newest["effective_model"] != verified["effective_model"]:
        raise auth.AuthorityError(
            "fleet_restore_completed_effective_model_changed", rc=1)
    return verified


def _seat_identity(role, member, request, rows, authority_path, state_root,
                   plan, now_ms, committed_launch=None):
    observed = rows.get(role)
    if observed is None and role in rows:
        raise auth.AuthorityError("fleet_restore_seat_ambiguous", rc=1)
    if observed is not None:
        path, raw, row = observed
        if row.get("schema") != "aib-seat/v1" \
                or row.get("trust") != "self_reported":
            raise auth.AuthorityError("fleet_restore_seat_invalid", rc=1)
        seat_id = row.get("seat_id")
        session_uuid = row.get("session_uuid")
        provider = row.get("provider")
        cwd = row.get("cwd")
        if not all(isinstance(value, str) and value for value in (
                seat_id, session_uuid, provider, cwd)) \
                or not Path(cwd).is_absolute():
            raise auth.AuthorityError("fleet_restore_seat_invalid", rc=1)
        principal_id = _principal_for_seat(
            authority_path, seat_id, session_uuid, row.get("principal_id"))
        if role == request.get("requested_role") and any((
                principal_id != request.get("principal_id"),
                seat_id != request.get("seat_id"),
                session_uuid != request.get("session_uuid"),
                provider != request.get("provider"),
        )):
            raise auth.AuthorityError(
                "fleet_restore_request_seat_mismatch", rc=1)
        # A seat records only the launch request.  ``model`` or
        # ``effective_model`` here would be an untrusted seat upgrading itself
        # into runtime evidence, so reject those legacy/forged shapes.
        forbidden_model_keys = [
            key for key in row
            if isinstance(key, str) and "model" in key.casefold()
            and key != "model_requested"
        ]
        if forbidden_model_keys:
            raise auth.AuthorityError(
                "fleet_restore_seat_model_claim_forbidden", rc=1)
        requested_model = row.get("model_requested")
        if not isinstance(requested_model, str) or not requested_model:
            raise auth.AuthorityError(
                "fleet_restore_seat_requested_model_missing", rc=1)
        assignment = _plan_model_assignment(
            plan, member["fleet_member_id"], provider)
        if requested_model != assignment["requested_model"]:
            raise auth.AuthorityError(
                "fleet_restore_seat_requested_model_mismatch", rc=1)
        if role == request.get("requested_role") \
                and request.get("model") != requested_model:
            raise auth.AuthorityError(
                "fleet_restore_request_model_mismatch", rc=1)
        observation = _committed_runtime_observation(
            state_root, committed_launch, session_uuid, provider, now_ms) \
            if committed_launch is not None else _runtime_observation(
                state_root, session_uuid, provider, now_ms)
        effective_model = observation["effective_model"]
        if effective_model not in assignment["allowed_effective_models"]:
            raise auth.AuthorityError(
                "fleet_restore_effective_model_not_permitted", rc=1)
        receipt = {
            "schema": SEAT_RECEIPT_SCHEMA,
            "source": "seat_record_plus_active_principal",
            "source_path": str(path.resolve()),
            "source_sha256": _sha256(raw),
            "trust": "self_reported",
            "authority_grade": "local_active_principal",
            "role": role,
            "principal_id": principal_id,
            "seat_id": seat_id,
            "session_uuid": session_uuid,
            "provider": provider,
            "requested_model": requested_model,
            "allowed_effective_models": list(
                assignment["allowed_effective_models"]),
            "effective_model": effective_model,
            "model_observation": observation,
            "model_observation_sha256": observation[
                "observation_sha256"],
            "cwd": str(Path(cwd).resolve()),
        }
        return receipt

    # Once a cabin seat directory is present, the caller must exist in that
    # same directory.  Falling back to request identity while consuming the
    # other four roles from a different cabin would create a mixed fleet.
    if rows:
        raise auth.AuthorityError("fleet_restore_seat_missing", rc=1)

    if role == request.get("requested_role"):
        assignment = _plan_model_assignment(
            plan, member["fleet_member_id"], request["provider"])
        if request.get("model") != assignment["requested_model"]:
            raise auth.AuthorityError(
                "fleet_restore_request_model_mismatch", rc=1)
        observation = _committed_runtime_observation(
            state_root, committed_launch, request["session_uuid"],
            request["provider"], now_ms) \
            if committed_launch is not None else _runtime_observation(
                state_root, request["session_uuid"], request["provider"],
                now_ms)
        effective_model = observation["effective_model"]
        if effective_model not in assignment["allowed_effective_models"]:
            raise auth.AuthorityError(
                "fleet_restore_effective_model_not_permitted", rc=1)
        receipt = {
            "schema": SEAT_RECEIPT_SCHEMA,
            "source": "authenticated_project_request",
            "trust": "self_reported",
            "authority_grade": "local_active_principal",
            "role": role,
            "principal_id": request["principal_id"],
            "seat_id": request["seat_id"],
            "session_uuid": request["session_uuid"],
            "provider": request["provider"],
            "requested_model": assignment["requested_model"],
            "allowed_effective_models": list(
                assignment["allowed_effective_models"]),
            "effective_model": effective_model,
            "model_observation": observation,
            "model_observation_sha256": observation[
                "observation_sha256"],
            "cwd": str(Path(request["cwd"]).resolve()),
        }
        return receipt
    raise auth.AuthorityError("fleet_restore_seat_missing", rc=1)


def _entry_request(request, member, launch):
    return {
        "schema": project_entry.REQUEST_SCHEMA,
        "operation": "resolve_bind_or_onboard",
        "project_query": request["project_query"],
        "requested_role": member["role"],
        "principal_id": launch["principal_id"],
        "seat_id": launch["seat_id"],
        "session_uuid": launch["session_uuid"],
        "cwd": launch["cwd"],
        "provider": launch["provider"],
        # Compatibility input name; project_entry treats this as requested.
        "model": launch["requested_model"],
        "fleet_size": 5,
        "task_resume": False,
        "expires_at_ms": request["expires_at_ms"],
    }


def _effect_count(entry):
    explicit = entry.get("state_effect_count", entry.get("effect_count"))
    derived = 2 if isinstance(entry.get("binding_receipt"), dict) \
        and isinstance(entry.get("read_receipt"), dict) else 0
    if isinstance(explicit, int) and not isinstance(explicit, bool) \
            and explicit >= 0:
        return max(explicit, derived)
    # project_entry applies one binding and records one native read receipt.
    return derived


def _member_receipt(member, launch, entry):
    return {
        "fleet_member_id": member["fleet_member_id"],
        "role": member["role"],
        "provider": launch["provider"],
        "seat_launch_receipt": launch,
        "binding_receipt": entry["binding_receipt"],
        "read_receipt": entry["read_receipt"],
        "state_effect_count": _effect_count(entry),
        # Keep the exact authority result for later audit/replay.
        "entry_receipt": entry,
    }


def _recover_existing_member(state_root, authority_path, request, member,
                             launch, plan, now_ms):
    """Recover an effect committed before its restore journal write.

    ``bind_apply.resolve`` and ``onboarding_recall.verify`` re-check the
    existing P1A authority.  A holder mismatch is a conflict, never a reason
    to overwrite the active binding.
    """
    resolved = bind_apply.execute(state_root, authority_path, {
        "operation": "resolve",
        "project_id": plan["project_id"],
        "fleet_id": plan["fleet_id"],
        "fleet_member_id": member["fleet_member_id"],
    }, now_ms)
    if not resolved.get("ok"):
        if resolved.get("reason") == "bind_binding_not_found":
            return None
        raise auth.AuthorityError(
            resolved.get("reason", "fleet_restore_binding_resolve_failed"),
            rc=int(resolved.get("rc", 1)))
    binding = resolved["binding"]
    expected = {
        "principal_id": launch["principal_id"],
        "seat_id": launch["seat_id"],
        "session_uuid": launch["session_uuid"],
        "provider": launch["provider"],
        "requested_model": launch["requested_model"],
        "effective_model": launch["effective_model"],
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise auth.AuthorityError(
            "fleet_restore_existing_holder_conflict", rc=1)
    try:
        committed_observation = runtime_model_observation.verify(
            state_root, binding.get("model_observation"),
            launch["session_uuid"], launch["provider"], now_ms,
            require_fresh=False)
    except auth.AuthorityError as err:
        raise auth.AuthorityError(
            "fleet_restore_existing_model_observation_invalid", rc=1) from err

    # The bind effect may have committed immediately before a crash, while
    # its restore-journal append did not.  Preflight legitimately observes a
    # newer same-model runtime refresh in that window.  Preserve the binding's
    # immutable evidence in the recovered journal receipt; resolve above has
    # already required the current head to be fresh and the same effective
    # model, so this never rewinds or invents runtime identity.
    launch["model_observation"] = committed_observation
    launch["model_observation_sha256"] = committed_observation[
        "observation_sha256"]

    con = auth.open_db(state_root, readonly=True)
    try:
        rows = con.execute(
            """SELECT receipt_json FROM onboarding_read_receipts
               WHERE binding_id=? AND status='active'
               ORDER BY created_at_ms""",
            (binding["binding_id"],),
        ).fetchall()
    finally:
        con.close()
    if len(rows) > 1:
        raise auth.AuthorityError(
            "fleet_restore_read_receipt_ambiguous", rc=1)
    if rows:
        try:
            read_receipt = json.loads(rows[0]["receipt_json"])
        except (TypeError, ValueError) as exc:
            raise auth.AuthorityError(
                "fleet_restore_read_receipt_invalid", rc=1) from exc
        verified = onboarding_recall.execute(state_root, authority_path, {
            "operation": "verify", "receipt": read_receipt,
        }, now_ms)
        if not verified.get("ok"):
            raise auth.AuthorityError(
                verified.get("reason", "fleet_restore_read_verify_failed"),
                rc=int(verified.get("rc", 1)))
    else:
        recalled = onboarding_recall.execute(state_root, authority_path, {
            "operation": "read",
            "binding_receipt": resolved["receipt"],
            "session_uuid": launch["session_uuid"],
            "provider": launch["provider"],
            "requested_model": launch["requested_model"],
            "effective_model": launch["effective_model"],
        }, now_ms)
        if not recalled.get("ok"):
            raise auth.AuthorityError(
                recalled.get("reason", "fleet_restore_recall_failed"),
                rc=int(recalled.get("rc", 1)))
        read_receipt = recalled["receipt"]
    return auth.result_ok(
        reason="fleet_restore_member_recovered",
        project_id=plan["project_id"], fleet_id=plan["fleet_id"],
        fleet_member_id=member["fleet_member_id"], launch_plan=plan,
        binding=binding, binding_receipt=resolved["receipt"],
        read_receipt=read_receipt, task_resume=False,
        state_effect_count=2, current_state_effect_count=0)


def _validate_entry_identity(entry, member, launch, plan):
    if not isinstance(entry, dict) or entry.get("ok") is not True \
            or entry.get("project_id") != plan["project_id"] \
            or entry.get("fleet_id") != plan["fleet_id"] \
            or entry.get("fleet_member_id") != member["fleet_member_id"] \
            or entry.get("task_resume") is not False \
            or entry.get("formal_writes", 0) != 0:
        raise auth.AuthorityError("fleet_restore_entry_identity_invalid", rc=1)
    returned_plan = entry.get("launch_plan")
    if not _same_logical_plan(returned_plan, plan):
        raise auth.AuthorityError("fleet_restore_entry_plan_mismatch", rc=1)
    binding = entry.get("binding")
    binding_receipt = entry.get("binding_receipt")
    read_receipt = entry.get("read_receipt")
    if not all(isinstance(value, dict) for value in (
            binding, binding_receipt, read_receipt)):
        raise auth.AuthorityError("fleet_restore_entry_identity_invalid", rc=1)
    assignment = _plan_model_assignment(
        plan, member["fleet_member_id"], launch["provider"])
    expected = {
        "project_id": plan["project_id"],
        "fleet_id": plan["fleet_id"],
        "fleet_member_id": member["fleet_member_id"],
        "principal_id": launch["principal_id"],
        "seat_id": launch["seat_id"],
        "session_uuid": launch["session_uuid"],
        "provider": launch["provider"],
        "requested_model": launch["requested_model"],
        "effective_model": launch["effective_model"],
        "model_policy_sha256": plan["model_policy_sha256"],
        "model_assignment_sha256": auth.sha256_hex(
            auth.canonical_bytes(assignment)),
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise auth.AuthorityError("fleet_restore_entry_identity_invalid", rc=1)
    observation = binding.get("model_observation")
    if not isinstance(observation, dict) \
            or observation.get("observation_sha256") != \
            launch.get("model_observation_sha256"):
        raise auth.AuthorityError("fleet_restore_entry_identity_invalid", rc=1)
    for receipt in (binding_receipt, read_receipt):
        for key in ("project_id", "fleet_id", "fleet_member_id",
                    "session_uuid", "provider", "requested_model",
                    "effective_model", "model_policy_sha256",
                    "model_assignment_sha256"):
            if receipt.get(key) != expected[key]:
                raise auth.AuthorityError(
                    "fleet_restore_entry_identity_invalid", rc=1)
        if receipt.get("model_observation_sha256") != \
                launch.get("model_observation_sha256"):
            raise auth.AuthorityError(
                "fleet_restore_entry_identity_invalid", rc=1)
    for key in ("principal_id", "seat_id"):
        if binding_receipt.get(key) != expected[key]:
            raise auth.AuthorityError(
                "fleet_restore_entry_identity_invalid", rc=1)
    binding_id = binding.get("binding_id")
    if not isinstance(binding_id, str) or not binding_id \
            or binding_receipt.get("binding_id") != binding_id \
            or read_receipt.get("binding_id") != binding_id:
        raise auth.AuthorityError("fleet_restore_entry_identity_invalid", rc=1)


def _verify_completed_receipts(state_root, authority_path, request, journal,
                               launches, now_ms):
    plan = journal["plan"]
    completed = journal.get("completed_roles")
    receipts = journal.get("member_receipts")
    expected_roles = [row.get("role") for row in plan["seats"]]
    if not isinstance(completed, list) \
            or completed != expected_roles[:len(completed)] \
            or len(receipts) != len(completed):
        raise auth.AuthorityError("fleet_restore_journal_tampered", rc=1)
    for index, role in enumerate(completed):
        member = plan["seats"][index]
        stored = receipts[index]
        launch = launches.get(role)
        if not isinstance(stored, dict) or not isinstance(launch, dict) \
                or stored.get("role") != role \
                or stored.get("fleet_member_id") != \
                member.get("fleet_member_id") \
                or stored.get("provider") != launch.get("provider"):
            raise auth.AuthorityError(
                "fleet_restore_journal_tampered", rc=1)
        old_launch = stored.get("seat_launch_receipt")
        identity_keys = (
            "role", "principal_id", "seat_id", "session_uuid", "provider",
            "requested_model", "effective_model", "cwd", "trust",
            "authority_grade",
        )
        if not isinstance(old_launch, dict) or any(
                old_launch.get(key) != launch.get(key)
                for key in identity_keys):
            raise auth.AuthorityError(
                "fleet_restore_journal_holder_stale", rc=1)
        try:
            runtime_model_observation.verify(
                state_root, old_launch.get("model_observation"),
                old_launch.get("session_uuid"), old_launch.get("provider"),
                now_ms, require_fresh=False)
        except auth.AuthorityError as err:
            raise auth.AuthorityError(
                "fleet_restore_journal_model_observation_stale", rc=1) \
                from err
        if old_launch.get("model_observation_sha256") != \
                old_launch["model_observation"].get("observation_sha256"):
            raise auth.AuthorityError(
                "fleet_restore_journal_tampered", rc=1)
        entry = stored.get("entry_receipt")
        _validate_entry_identity(entry, member, old_launch, plan)
        resolved = bind_apply.execute(state_root, authority_path, {
            "operation": "resolve",
            "project_id": plan["project_id"],
            "fleet_id": plan["fleet_id"],
            "fleet_member_id": member["fleet_member_id"],
        }, now_ms)
        if not resolved.get("ok"):
            raise auth.AuthorityError(
                "fleet_restore_journal_binding_stale", rc=1)
        if stored.get("binding_receipt") != resolved.get("receipt") \
                or entry.get("binding") != resolved.get("binding") \
                or entry.get("binding_receipt") != resolved.get("receipt"):
            raise auth.AuthorityError(
                "fleet_restore_journal_tampered", rc=1)
        con = auth.open_db(state_root, readonly=True)
        try:
            rows = con.execute(
                """SELECT receipt_json FROM onboarding_read_receipts
                   WHERE binding_id=? AND status='active'
                   ORDER BY created_at_ms""",
                (resolved["binding"]["binding_id"],),
            ).fetchall()
        finally:
            con.close()
        if len(rows) != 1:
            raise auth.AuthorityError(
                "fleet_restore_journal_read_stale", rc=1)
        try:
            current_read = json.loads(rows[0]["receipt_json"])
        except (TypeError, ValueError) as exc:
            raise auth.AuthorityError(
                "fleet_restore_journal_tampered", rc=1) from exc
        if stored.get("read_receipt") != current_read \
                or entry.get("read_receipt") != current_read:
            raise auth.AuthorityError(
                "fleet_restore_journal_tampered", rc=1)
        verified = onboarding_recall.execute(state_root, authority_path, {
            "operation": "verify", "receipt": current_read,
        }, now_ms)
        if not verified.get("ok"):
            raise auth.AuthorityError(
                "fleet_restore_journal_read_stale", rc=1)


def _result(journal, path, *, ok, reason, rc, current_effects=0,
            failure=None):
    members = list(journal["member_receipts"])
    total_effects = sum(
        row.get("state_effect_count", 0) for row in members
        if isinstance(row, dict))
    by_role = {
        row.get("role"): row for row in members if isinstance(row, dict)
    }
    requested = journal.get("requested_role")
    entry = by_role.get(requested, {}).get("entry_receipt")
    value = {
        "ok": bool(ok),
        "reason": reason,
        "rc": int(rc),
        "schema": "ez2-aib/project-develop-receipt/v1",
        "intent": "develop_apply",
        "entry_receipt": entry,
        "launch_plan": journal["plan"],
        "fleet_launch_applied": bool(ok),
        "member_receipts": members,
        "completed_roles": list(journal["completed_roles"]),
        "restore_receipt_path": str(path),
        "state_effect_count": total_effects,
        "current_state_effect_count": int(current_effects),
        # P1A bind/recall effects are durable local state, not owner/Main
        # formal approval writes.
        "formal_writes": 0,
        "task_resume": False,
        "task_resume_reason": "task_resume_not_requested",
        "task_resume_receipt": None,
    }
    if failure:
        value.update(failure)
    return value


def _consume_locked(state_root, authority_path, request, now_ms):
    """Apply every missing main-seat member and persist each success."""
    # Authentication and expiry are preconditions to every read/write path.
    # In particular, a missing-seat request must never let an unauthenticated
    # caller reserve the durable journal key.
    _request_is_current_main(authority_path, request, now_ms)
    preliminary = _registered_plan(state_root, request["project_query"])
    if preliminary is None:
        return None
    if not _valid_plan(preliminary):
        raise auth.AuthorityError("fleet_restore_plan_invalid", rc=1)
    requested_matches = [
        row for row in preliminary["seats"]
        if row.get("role") == request.get("requested_role")
    ]
    if len(requested_matches) != 1:
        raise auth.AuthorityError(
            "fleet_restore_requested_role_unresolved", rc=1)
    project_id = preliminary["project_id"]
    fleet_id = preliminary["fleet_id"]
    path = _journal_path(state_root, project_id, fleet_id)
    journal = _read_journal(path, project_id, fleet_id)
    if journal is not None:
        if not _valid_plan(journal.get("plan"), project_id, fleet_id) \
                or not _same_logical_plan(journal["plan"], preliminary):
            raise auth.AuthorityError("fleet_restore_journal_invalid", rc=1)
    rows = _seat_rows(state_root)
    current_effects = 0

    if journal is None:
        journal = {
            "schema": JOURNAL_SCHEMA,
            "project_id": project_id,
            "fleet_id": fleet_id,
            "requested_role": request["requested_role"],
            "status": "partial",
            "plan": preliminary,
            "completed_roles": [],
            "failed_role": None,
            "failure_reason": None,
            "member_receipts": [],
            "updated_at_ms": int(now_ms),
        }
    else:
        # Any currently authenticated member may advance the same fleet
        # transaction.  The requested role is only the caller whose exact
        # entry receipt should be projected in this response.
        journal["requested_role"] = request["requested_role"]

    plan = journal["plan"]
    if not isinstance(plan.get("seats"), list):
        raise auth.AuthorityError("fleet_restore_journal_invalid", rc=1)

    # Preflight all five identities before the first binding effect.  A
    # duplicate/missing holder discovered at member five cannot be allowed to
    # leave members one through four bound under an invalid fleet plan.
    completed_launches = {}
    completed_roles = journal.get("completed_roles")
    stored_receipts = journal.get("member_receipts")
    if not isinstance(completed_roles, list) \
            or not isinstance(stored_receipts, list) \
            or len(stored_receipts) != len(completed_roles):
        raise auth.AuthorityError("fleet_restore_journal_tampered", rc=1)
    for index, completed_role in enumerate(completed_roles):
        stored = stored_receipts[index]
        launch = stored.get("seat_launch_receipt") \
            if isinstance(stored, dict) else None
        if not isinstance(completed_role, str) or not isinstance(launch, dict) \
                or stored.get("role") != completed_role:
            raise auth.AuthorityError("fleet_restore_journal_tampered", rc=1)
        completed_launches[completed_role] = launch
    launches = {}
    for member in plan["seats"]:
        role = member.get("role")
        try:
            launches[role] = _seat_identity(
                role, member, request, rows, authority_path,
                state_root, plan, now_ms,
                committed_launch=completed_launches.get(role))
        except auth.AuthorityError as err:
            journal.update({
                "status": "partial",
                "failed_role": role,
                "failure_reason": err.reason,
                "updated_at_ms": int(now_ms),
            })
            _atomic_json(path, journal)
            return _result(
                journal, path, ok=False, reason="fleet_restore_partial", rc=1,
                current_effects=0,
                failure={"failed_role": role,
                         "failure_reason": err.reason})
    for key in ("principal_id", "seat_id", "session_uuid"):
        values = [launches[row["role"]].get(key) for row in plan["seats"]]
        if len(values) != 5 or len(set(values)) != 5:
            journal.update({
                "status": "partial",
                "failed_role": None,
                "failure_reason": "fleet_restore_identity_not_unique",
                "updated_at_ms": int(now_ms),
            })
            _atomic_json(path, journal)
            return _result(
                journal, path, ok=False, reason="fleet_restore_partial", rc=1,
                current_effects=0,
                failure={
                    "failed_role": None,
                    "failure_reason": "fleet_restore_identity_not_unique",
                })

    _verify_completed_receipts(
        Path(state_root), Path(authority_path), request, journal,
        launches, now_ms)
    completed = set(journal["completed_roles"])

    index = 0
    while index < len(plan["seats"]):
        member = plan["seats"][index]
        role = member.get("role")
        if role in completed:
            index += 1
            continue
        launch = launches[role]
        entry_member = dict(member)
        # Provider rotation is a property of the current seat, not a change to
        # the permanent fleet_member_id.  project_entry still verifies that
        # the current provider is declared by the manifest.
        entry_member["provider"] = launch["provider"]
        entry_member["requested_model"] = launch["requested_model"]
        entry_member["allowed_effective_models"] = list(
            launch["allowed_effective_models"])
        try:
            entry = _recover_existing_member(
                Path(state_root), Path(authority_path), request,
                entry_member, launch, plan, now_ms)
        except auth.AuthorityError as err:
            journal.update({
                "status": "partial",
                "failed_role": role,
                "failure_reason": err.reason,
                "updated_at_ms": int(now_ms),
            })
            _atomic_json(path, journal)
            return _result(
                journal, path, ok=False, reason="fleet_restore_partial", rc=1,
                current_effects=current_effects,
                failure={"failed_role": role,
                         "failure_reason": err.reason})
        if entry is None:
            entry = project_entry.execute(
                Path(state_root), Path(authority_path),
                _entry_request(request, entry_member, launch), now_ms)
        returned_plan = entry.get("launch_plan")
        if not entry.get("ok"):
            journal.update({
                "status": "partial",
                "failed_role": role,
                "failure_reason": entry.get(
                    "reason", "project_entry_internal_error"),
                "updated_at_ms": int(now_ms),
            })
            _atomic_json(path, journal)
            return _result(
                journal, path, ok=False, reason="fleet_restore_partial", rc=1,
                current_effects=current_effects,
                failure={
                    "failed_role": role,
                    "failure_reason": journal["failure_reason"],
                    "failed_entry_receipt": entry,
                })
        try:
            if not _valid_plan(returned_plan, project_id, fleet_id) \
                    or not _same_logical_plan(returned_plan, preliminary):
                raise auth.AuthorityError(
                    "fleet_restore_entry_plan_mismatch", rc=1)
            _validate_entry_identity(entry, entry_member, launch, plan)
        except auth.AuthorityError as err:
            # project_entry reported success, so its bind/read effects may
            # already exist even though the returned envelope is unusable.
            # Keep that uncertainty visible and let the next run reconcile
            # from the authoritative stores; never collapse it to rc=2 with
            # an invented zero-effect claim.
            observed_effects = _effect_count(entry)
            current_effects += observed_effects
            journal.update({
                "status": "partial",
                "failed_role": role,
                "failure_reason": err.reason,
                "reconciliation_required": True,
                "updated_at_ms": int(now_ms),
            })
            _atomic_json(path, journal)
            return _result(
                journal, path, ok=False, reason="fleet_restore_partial", rc=1,
                current_effects=current_effects,
                failure={
                    "failed_role": role,
                    "failure_reason": err.reason,
                    "reconciliation_required": True,
                    "uncommitted_effect_count": observed_effects,
                    "failed_entry_receipt": entry,
                })

        # Persist the observed provider without changing logical member
        # identity.  This makes provider replacement visible in the receipt.
        journal["plan"]["seats"][index]["provider"] = launch["provider"]
        journal["plan"]["seats"][index]["requested_model"] = \
            launch["requested_model"]
        journal["plan"]["seats"][index]["allowed_effective_models"] = list(
            launch["allowed_effective_models"])
        plan = journal["plan"]
        receipt = _member_receipt(entry_member, launch, entry)
        journal["member_receipts"].append(receipt)
        journal["completed_roles"].append(role)
        completed.add(role)
        effects = receipt["state_effect_count"]
        current_effects += entry.get("current_state_effect_count", effects)
        journal.update({
            "status": "partial",
            "failed_role": None,
            "failure_reason": None,
            "reconciliation_required": False,
            "updated_at_ms": int(now_ms),
        })
        # The durability boundary is per member.  A later member cannot make
        # an earlier successful binding run again.
        _atomic_json(path, journal)
        index += 1

    expected_roles = [row.get("role") for row in journal["plan"]["seats"]]
    if journal["completed_roles"] != expected_roles:
        raise auth.AuthorityError("fleet_restore_journal_order_invalid", rc=1)
    journal.update({
        "status": "complete",
        "failed_role": None,
        "failure_reason": None,
        "reconciliation_required": False,
        "updated_at_ms": int(now_ms),
    })
    _atomic_json(path, journal)
    return _result(
        journal, path, ok=True, reason="fleet_restore_complete", rc=0,
        current_effects=current_effects)


def consume(state_root, authority_path, request, now_ms):
    """Serialize one fleet restore without treating the lock as authority."""
    _request_is_current_main(authority_path, request, now_ms)
    preliminary = _registered_plan(state_root, request["project_query"])
    if preliminary is None:
        return None
    if not _valid_plan(preliminary):
        raise auth.AuthorityError("fleet_restore_plan_invalid", rc=1)
    if sum(row.get("role") == request.get("requested_role")
           for row in preliminary["seats"]) != 1:
        raise auth.AuthorityError(
            "fleet_restore_requested_role_unresolved", rc=1)
    journal_path = _journal_path(
        state_root, preliminary["project_id"], preliminary["fleet_id"])
    lock_path = journal_path.with_suffix(".lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_real = Path(state_root).resolve()
    parent_real = lock_path.parent.resolve()
    if lock_path.parent.is_symlink() or lock_path.is_symlink() \
            or state_real not in parent_real.parents:
        raise auth.AuthorityError("fleet_restore_journal_unsafe", rc=1)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(lock_path), flags, 0o600)
    except OSError as exc:
        raise auth.AuthorityError(
            "fleet_restore_journal_unsafe", rc=1) from exc
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _consume_locked(
            state_root, authority_path, request, now_ms)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
