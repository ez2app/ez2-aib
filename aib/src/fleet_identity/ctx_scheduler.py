#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Durable CTX scheduling forcepoint for one active seat binding.

Thresholds and machine observations remain owned by rotation.ctx_policy and
rotation.lifecycle.  Exclusive task ownership remains owned by the E1 work
ledger.  This module persists only once-per-stage directives, scheduler CAS,
clock floor and request idempotency.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys

_ROTATION_DIR = Path(__file__).resolve().parents[1] / "rotation"
if str(_ROTATION_DIR) not in sys.path:
    sys.path.insert(0, str(_ROTATION_DIR))
import ctx_policy  # noqa: E402
import lifecycle  # noqa: E402

from . import authority_store as auth
from . import seat_continuity


REQUEST_SCHEMA = "ez2-aib/ctx-scheduler-request/v1"
STATE_SCHEMA = "ez2-aib/ctx-scheduler-state/v1"
DIRECTIVE_SCHEMA = "ez2-aib/ctx-scheduler-directive/v1"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_BASE_KEYS = frozenset((
    "schema", "operation", "binding_receipt", "session_uuid", "ctx_dir",
    "ctx_policy_path", "ctx_policy_sha256", "ctx_policy_generation",
    "expected_scheduler_generation", "idempotency_key",
))
_REQUEST_KEYS = {
    "tick": _BASE_KEYS,
    "admit_work": _BASE_KEYS | {"work_request"},
    "status": frozenset(("schema", "operation", "binding_receipt")),
}


class SchedulerError(Exception):
    def __init__(self, reason, rc=1):
        self.reason = reason
        self.rc = rc
        super().__init__(reason)


def _ok(reason, **fields):
    return {"ok": True, "reason": reason, "rc": 0, **fields}


def _error(reason, rc=1, **fields):
    return {"ok": False, "reason": reason, "rc": rc, **fields}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _request_sha(request):
    return hashlib.sha256(_canonical(request)).hexdigest()


def _state_root(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or path.is_symlink():
        raise SchedulerError("ctx_scheduler_state_root_invalid", rc=2)
    return path


def _database_path(root):
    return root / "ctx-scheduler" / "scheduler.sqlite3"


def _open_db(root):
    path = _database_path(root)
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.parent.is_symlink() or path.is_symlink():
            raise OSError("scheduler state path is a symlink")
        con = sqlite3.connect(str(path), timeout=15.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=15000")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS scheduler_bindings(
          binding_id TEXT PRIMARY KEY,
          scheduler_generation INTEGER NOT NULL,
          clock_floor_ms INTEGER NOT NULL,
          state_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scheduler_requests(
          binding_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          request_sha256 TEXT NOT NULL,
          result_json TEXT NOT NULL,
          PRIMARY KEY(binding_id,idempotency_key)
        );
        """)
        os.chmod(path, 0o600)
        return con
    except (OSError, sqlite3.Error) as exc:
        raise SchedulerError("ctx_scheduler_state_unavailable", rc=2) from exc


def _default_state():
    return {
        "schema": STATE_SCHEMA,
        "scheduler_generation": 0,
        "clock_floor_ms": 0,
        "emitted_stages": [],
        "directives": {},
        "last_directive": None,
    }


def _load_state(con, binding_id):
    row = con.execute(
        "SELECT * FROM scheduler_bindings WHERE binding_id=?", (binding_id,)
    ).fetchone()
    if row is None:
        return _default_state()
    try:
        value = json.loads(row["state_json"])
    except ValueError as exc:
        raise SchedulerError("ctx_scheduler_state_invalid", rc=2) from exc
    if not isinstance(value, dict) or value.get("schema") != STATE_SCHEMA \
            or int(row["scheduler_generation"]) != value.get(
                "scheduler_generation") \
            or int(row["clock_floor_ms"]) != value.get("clock_floor_ms"):
        raise SchedulerError("ctx_scheduler_state_invalid", rc=2)
    return value


def _store_state(con, binding_id, state):
    payload = _canonical(state).decode("utf-8")
    con.execute(
        """INSERT INTO scheduler_bindings
           (binding_id,scheduler_generation,clock_floor_ms,state_json)
           VALUES(?,?,?,?)
           ON CONFLICT(binding_id) DO UPDATE SET
             scheduler_generation=excluded.scheduler_generation,
             clock_floor_ms=excluded.clock_floor_ms,
             state_json=excluded.state_json""",
        (binding_id, state["scheduler_generation"],
         state["clock_floor_ms"], payload),
    )


def _validate_request(request, now_ms):
    if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0 \
            or not isinstance(request, dict):
        raise SchedulerError("ctx_scheduler_request_invalid", rc=2)
    operation = request.get("operation")
    expected = _REQUEST_KEYS.get(operation)
    if request.get("schema") != REQUEST_SCHEMA or expected is None \
            or set(request) != expected:
        raise SchedulerError("ctx_scheduler_request_invalid", rc=2)
    if operation == "status":
        return operation
    for name in ("session_uuid", "ctx_dir", "ctx_policy_path",
                 "ctx_policy_sha256", "ctx_policy_generation",
                 "idempotency_key"):
        if not isinstance(request.get(name), str) or not request[name]:
            raise SchedulerError("ctx_scheduler_request_invalid", rc=2)
    if not Path(request["ctx_dir"]).is_absolute() \
            or not Path(request["ctx_policy_path"]).is_absolute() \
            or not _SHA_RE.fullmatch(request["ctx_policy_sha256"]) \
            or not _KEY_RE.fullmatch(request["idempotency_key"]):
        raise SchedulerError("ctx_scheduler_request_invalid", rc=2)
    generation = request.get("expected_scheduler_generation")
    if not isinstance(generation, int) or isinstance(generation, bool) \
            or generation < 0:
        raise SchedulerError("ctx_scheduler_request_invalid", rc=2)
    if operation == "admit_work" and not isinstance(
            request.get("work_request"), dict):
        raise SchedulerError("ctx_scheduler_request_invalid", rc=2)
    return operation


def _binding(state_root, request, now_ms):
    result = seat_continuity.execute(
        state_root, None,
        {"operation": "verify_holder",
         "binding_receipt": request.get("binding_receipt")}, now_ms)
    if not result.get("ok"):
        raise SchedulerError(result.get("reason", "seat_binding_holder_fenced"),
                             rc=int(result.get("rc", 1)))
    binding = result.get("binding")
    if not isinstance(binding, dict) or not binding.get("binding_id"):
        raise SchedulerError("ctx_scheduler_binding_invalid", rc=2)
    return binding


def _policy(request):
    path = Path(request["ctx_policy_path"])
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SchedulerError("ctx_policy_invalid", rc=1) from exc
    if digest != request["ctx_policy_sha256"]:
        raise SchedulerError("ctx_policy_stale", rc=1)
    if not isinstance(document, dict) \
            or str(document.get("schema_version")) \
            != request["ctx_policy_generation"]:
        raise SchedulerError("ctx_policy_generation_stale", rc=1)
    try:
        stages = ctx_policy.stages_descending(str(path))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise SchedulerError("ctx_policy_invalid", rc=1) from exc
    return digest, str(document["schema_version"]), stages


def _observation(request, now_ms, stages):
    observed = lifecycle.read_machine_ctx(
        request["session_uuid"], ctx_dir=request["ctx_dir"], now_ms=now_ms)
    if observed is None:
        return {
            "rotation_mode": "manual_only", "ctx_state": "unknown",
            "observed_pct": None, "stale_reason": "missing", "stage": None,
        }
    if observed.get("stale"):
        return {
            "rotation_mode": "manual_only", "ctx_state": "stale",
            "observed_pct": observed.get("pct"),
            "stale_reason": observed.get("stale_reason"), "stage": None,
        }
    pct = observed["pct"]
    stage = next((name for floor, name in stages if pct >= floor), None)
    if stage is None:
        raise SchedulerError("ctx_policy_invalid", rc=1)
    return {
        "rotation_mode": "auto", "ctx_state": "fresh",
        "ctx_source": observed.get("source"), "observed_pct": pct,
        "observed_at_ms": observed.get("observed_ms"), "stage": stage,
        "stale_reason": None,
    }


def _directive_id(binding_id, policy_sha, policy_generation, stage):
    digest = hashlib.sha256(_canonical({
        "binding_id": binding_id, "policy_sha256": policy_sha,
        "policy_generation": policy_generation, "stage": stage,
    })).hexdigest()
    return "ctxd_" + digest


def _mint_handoff(state_root, authority_path, request, binding, observation,
                  policy_sha, now_ms):
    provider = binding.get("provider")
    result = seat_continuity.execute(state_root, authority_path, {
        "operation": "evaluate_ctx",
        "binding_receipt": request["binding_receipt"],
        "ctx_observation": {
            "source_kind": "provider_machine",
            "used_pct": observation["observed_pct"],
            "observed_at_ms": observation["observed_at_ms"],
            "session_uuid": request["session_uuid"],
            "provider": provider,
        },
        "ctx_policy_path": request["ctx_policy_path"],
        "ctx_policy_sha256": policy_sha,
    }, now_ms)
    if not result.get("ok") or not isinstance(
            result.get("handoff_capability"), dict):
        raise SchedulerError(
            result.get("reason", "ctx_handoff_capability_unavailable"),
            rc=int(result.get("rc", 1)))
    return result["handoff_capability"]


def _make_directive(state_root, authority_path, request, binding, observation,
                    policy_sha, policy_generation, now_ms):
    stage = observation["stage"]
    directive = {
        "schema": DIRECTIVE_SCHEMA,
        "directive_id": _directive_id(
            binding["binding_id"], policy_sha, policy_generation, stage),
        "stage": stage,
        "action": lifecycle.STAGE_ACTIONS.get(stage),
        "policy_sha256": policy_sha,
        "policy_generation": policy_generation,
        "observed_pct": observation["observed_pct"],
        "issued_at_ms": int(now_ms),
    }
    # 10-15 CTX 單表(policy 只剩各席 7 格):派工閘讀表上「交接提醒」那格(80)以上就不派新工;
    #   「要求交接」(85)起發交接憑證(seat_continuity 同讀那一格)。B 的共治(75)/ 顧問停止(96)沒有格 ⇒ 不再做。
    if stage in ("handoff_remind", "handoff_now", "red_line"):
        directive["accept_new_work"] = False
    if stage in ("handoff_now", "red_line"):
        directive["handoff_capability"] = _mint_handoff(
            state_root, authority_path, request, binding, observation,
            policy_sha, now_ms)
    if not directive.get("action"):
        raise SchedulerError("ctx_scheduler_action_missing", rc=2)
    return directive


def _last_json(text):
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _work_call(work_request, now_ms):
    operation = work_request.get("operation")
    common = [
        "--tasks-dir", str(work_request.get("tasks_dir", "")),
        "--ledger", str(work_request.get("ledger", "")),
        "--task-id", str(work_request.get("task_id", "")),
    ]
    if operation == "claim":
        argv = [
            "claim", *common, "--owner", str(work_request.get("owner", "")),
            "--idempotency-key", str(work_request.get("idempotency_key", "")),
            "--expected-lease-generation",
            str(work_request.get("expected_lease_generation", "")),
            "--now-ms", str(now_ms), "--ttl-ms",
            str(work_request.get("ttl_ms", "")),
        ]
    elif operation == "state":
        argv = ["state", *common, "--now-ms", str(now_ms)]
    else:
        raise SchedulerError("ctx_scheduler_work_request_invalid", rc=2)
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-m", "src.work_ledger.callpoint", *argv],
        cwd=str(repo), env=env, text=True, capture_output=True, timeout=30)
    value = _last_json(result.stdout + "\n" + result.stderr)
    if not value:
        raise SchedulerError("ctx_scheduler_work_callpoint_failed", rc=2)
    return result.returncode, value


def _work_state(work_request, now_ms):
    state_request = dict(work_request, operation="state")
    rc, value = _work_call(state_request, now_ms)
    if rc != 0 or value.get("reason") != "work_item_task_state":
        raise SchedulerError(
            value.get("reason", "ctx_scheduler_work_state_unavailable"),
            rc=rc or 1)
    return value


def _admit_work(work_request, observation, now_ms):
    if not isinstance(work_request, dict) \
            or work_request.get("operation") not in ("claim", "continue"):
        raise SchedulerError("ctx_scheduler_work_request_invalid", rc=2)
    stage = observation["stage"]
    if work_request["operation"] == "continue":
        state = _work_state(work_request, now_ms)
        active = state.get("active_lease")
        exact = isinstance(active, dict) \
            and active.get("owner") == work_request.get("owner") \
            and active.get("lease_id") == work_request.get("lease_id") \
            and active.get("lease_generation") == work_request.get(
                "expected_lease_generation")
        if not exact:
            return _error(
                "ctx_scheduler_current_work_mismatch", rc=1,
                current_work_allowed=False)
        return _ok(
            "ctx_scheduler_current_work_allowed", current_work_allowed=True,
            new_work_allowed=False, work_lease_result=state)
    if stage in ("handoff_remind", "handoff_now", "red_line"):   # 派工閘 = 表上 80 那格以上
        return _error(
            "ctx_scheduler_new_work_blocked", rc=1,
            new_work_allowed=False)
    state = _work_state(work_request, now_ms)
    active = state.get("active_lease")
    rc, value = _work_call(work_request, now_ms)
    if rc != 0:
        return _error(
            value.get("reason", "ctx_scheduler_work_claim_failed"),
            rc=rc, new_work_allowed=False, work_lease_result=value)
    return _ok(
        "ctx_scheduler_new_work_allowed", new_work_allowed=True,
        work_lease_result=value)


def _status_result(state):
    return _ok(
        "ctx_scheduler_state",
        scheduler_generation=state["scheduler_generation"],
        clock_floor_ms=state["clock_floor_ms"],
        emitted_stages=list(state["emitted_stages"]),
        last_directive=state.get("last_directive"),
        truth_source="durable_ctx_scheduler_state")


def execute(state_root, authority_path, request, now_ms):
    """Execute one closed scheduler request and return a typed receipt."""
    try:
        operation = _validate_request(request, now_ms)
        root = _state_root(state_root)
        binding = _binding(root, request, now_ms)
        binding_id = binding["binding_id"]
        con = _open_db(root)
        try:
            con.execute("BEGIN IMMEDIATE")
            state = _load_state(con, binding_id)
            if operation == "status":
                con.execute("COMMIT")
                return _status_result(state)

            request_sha = _request_sha(request)
            prior = con.execute(
                """SELECT request_sha256,result_json FROM scheduler_requests
                   WHERE binding_id=? AND idempotency_key=?""",
                (binding_id, request["idempotency_key"]),
            ).fetchone()
            if prior is not None:
                if prior["request_sha256"] != request_sha:
                    con.execute("ROLLBACK")
                    return _error("ctx_scheduler_idempotency_conflict", rc=1)
                result = json.loads(prior["result_json"])
                result["ok"] = True
                result["reason"] = "ctx_scheduler_inert_replay"
                result["rc"] = 0
                con.execute("COMMIT")
                return result

            policy_sha, policy_generation, stages = _policy(request)
            if now_ms < state["clock_floor_ms"]:
                con.execute("ROLLBACK")
                return _error("ctx_scheduler_clock_rollback", rc=1)
            if request["expected_scheduler_generation"] \
                    != state["scheduler_generation"]:
                con.execute("ROLLBACK")
                return _error(
                    "ctx_scheduler_generation_stale", rc=1,
                    scheduler_generation=state["scheduler_generation"])
            observation = _observation(request, now_ms, stages)
            state["clock_floor_ms"] = max(state["clock_floor_ms"], now_ms)

            if observation["rotation_mode"] == "manual_only":
                result = _ok(
                    "ctx_scheduler_manual_only", **observation,
                    directive=None,
                    scheduler_generation=state["scheduler_generation"])
            else:
                stage = observation["stage"]
                directive = state["directives"].get(stage)
                emitted_now = False
                if stage != "ok" and directive is None:
                    directive = _make_directive(
                        root, authority_path, request, binding, observation,
                        policy_sha, policy_generation, now_ms)
                    state["scheduler_generation"] += 1
                    state["emitted_stages"].append(stage)
                    state["directives"][stage] = directive
                    state["last_directive"] = directive
                    emitted_now = True

                if operation == "admit_work":
                    result = _admit_work(
                        request["work_request"], observation, now_ms)
                    result.update({
                        "stage": stage, "ctx_state": "fresh",
                        "ctx_source": observation.get("ctx_source"),
                        "observed_pct": observation["observed_pct"],
                        "scheduler_generation": state["scheduler_generation"],
                        "directive": directive,
                    })
                elif stage == "ok":
                    result = _ok(
                        "ctx_scheduler_no_directive", **observation,
                        directive=None,
                        scheduler_generation=state["scheduler_generation"])
                elif emitted_now:
                    result = _ok(
                        "ctx_scheduler_directive_emitted", **observation,
                        directive=directive,
                        scheduler_generation=state["scheduler_generation"])
                else:
                    result = _ok(
                        "ctx_scheduler_stage_already_emitted", **observation,
                        directive=directive,
                        scheduler_generation=state["scheduler_generation"])

            _store_state(con, binding_id, state)
            con.execute(
                """INSERT INTO scheduler_requests
                   (binding_id,idempotency_key,request_sha256,result_json)
                   VALUES(?,?,?,?)""",
                (binding_id, request["idempotency_key"], request_sha,
                 _canonical(result).decode("utf-8")),
            )
            con.execute("COMMIT")
            return result
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()
    except SchedulerError as error:
        return _error(error.reason, rc=error.rc)
    except Exception:
        return _error("ctx_scheduler_internal_error", rc=2)
