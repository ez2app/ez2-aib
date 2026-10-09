#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Crash-safe authority state for D17 persona and brain binding.

This module stores reminder modes, one-shot confirmations, narrowly scoped
automatic-bind grants, and authenticated notification preferences. It
performs no binding or reminder delivery itself: every public result has
``effect_count=0`` and ``reminder_count=0``. A future binding caller may spend
an authorization only while already holding the P1A SQLite write transaction.

Human mutations require both the existing P1A owner proof verifier and a
trusted approval consumer operating on that same transaction. Bootstrap
authority, UI gestures, environment values, and social-store receipts do not
upgrade into D17 authority.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import tempfile
from pathlib import Path

from fleet_identity import authority_store
from fleet_identity import bind_plan
from fleet_identity import bind_target


# 10-04:/private/tmp 只有 macOS 有;Linux 上寫死它 ⇒ mkdtemp/NamedTemporaryFile 一律 FileNotFoundError(雲端老紅實踩)。
#   與 tools/verify/runner.py 的 TMP_ROOT 同一個挑法:有 /private/tmp 就用它(Mac 行為不變),沒有就 /tmp。
_PRIVATE_TMP = "/private/tmp" if os.path.isdir("/private/tmp") else "/tmp"


SUBJECT_KEYS = {"principal_id", "seat_id", "session_uuid"}
SCOPE_KEYS = bind_target.SCOPE_KEYS
NOTIFICATION_SCOPE_KEYS = bind_target.NOTIFICATION_SCOPE_KEYS
GRANT_KEYS = {
    "grant_id", "subject", "scope", "plan_sha256", "target_fingerprint",
    "not_before_ms", "expires_at_ms", "max_effects",
}
PENDING_KEYS = {
    "pending_id", "subject", "scope", "plan_sha256", "target_fingerprint",
    "trigger_kind", "created_at_ms", "expires_at_ms",
}
AUTH_KEYS = {"human_receipt", "authority_proof"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NOTIFICATION_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
MODES = {"remind_only", "confirm_each", "scoped_auto"}
DAY_MS = 24 * 60 * 60 * 1000
TEN_MINUTES_MS = 10 * 60 * 1000
HUMAN_RECEIPT_KEYS = {"schema", "receipt_id", "receipt_sha256"}
WILDCARD_CHARS = frozenset("*?[]")
RECORD_SPECS = {
    "d17_pending_bind_plans": {
        "domain": "d17-pending-v1",
        "id_column": "pending_id",
        "id_key": "pending_id",
        "statuses": {"pending", "confirmed", "expired", "superseded"},
    },
    "d17_scoped_auto_grants": {
        "domain": "d17-grant-v1",
        "id_column": "grant_id",
        "id_key": "grant_id",
        "statuses": {"active", "consumed", "revoked", "expired"},
    },
    "d17_bind_confirmations": {
        "domain": "d17-confirmation-v1",
        "id_column": "confirmation_id",
        "id_key": "confirmation_id",
        "statuses": {"active", "consumed", "revoked", "expired"},
    },
}
NOTIFICATION_POLICY_KEYS = {
    "policy_id", "subject", "notification_scope", "generation", "muted",
    "snoozed_until_ms", "last_reminder_id",
    "last_evaluator_receipt_sha256", "authority_grade",
}
NOTIFICATION_OPERATIONS = {
    "snooze_notifications", "set_notification_policy",
}
HUMAN_MUTATION_OPERATIONS = {
    "set_mode", "confirm", "grant", "revoke",
} | NOTIFICATION_OPERATIONS


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value):
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _fp(subject):
    return _sha(subject)


def _plain_string(value):
    return (isinstance(value, str) and bool(value)
            and not any(char in value for char in WILDCARD_CHARS)
            and "\x00" not in value)


def _entity_id(value):
    return _plain_string(value) and 3 <= len(value) <= 128


def _notification_id(value):
    return isinstance(value, str) and bool(NOTIFICATION_ID_RE.fullmatch(value))


def _strict_int(value, *, minimum=0):
    return type(value) is int and value >= minimum


def _base(reason, *, ok=False, rc=1, mode="remind_only", generation=0,
          receipt=None, **extra):
    result = {
        "ok": bool(ok), "reason": reason, "rc": int(rc), "mode": mode,
        "mode_generation": int(generation), "triggered": False,
        "reminder_count": 0, "effect_count": 0, "receipt": receipt,
    }
    result.update(extra)
    return result


def _safe_subject(value):
    return (isinstance(value, dict) and set(value) == SUBJECT_KEYS
            and all(_plain_string(value[key]) for key in SUBJECT_KEYS))


def _safe_scope(value):
    """authority scope 的形狀判定委派給 shared owner。

    ★ 06:16 第 5 條 / 07:03 第 6 條:B 不得自己再寫一份 scope 判定。
      合法但不同的 64hex 的**內容**真偽在 _prepared_target 重算後才判,
      這裡只判形狀與封閉集合。
    """
    try:
        return bool(bind_target.validate_authority_scope(value))
    except bind_target.TargetError:
        return False


def _prepared_target(con, state_root, bootstrap_authority_path, plan_sha256,
                     now_ms):
    """從同一個 P1A DB 的 prepared bind_transactions 讀回 exact plan,重算 target。

    ★ 0378c 09-01 07:03 第 1/2 條:**B 不接受 caller-supplied plan 作權威。**
      caller 給什麼就信什麼,等於讓請求方自己宣告自己的授權範圍。
      順序固定:row 唯一性 → status → plan/cap 逐位元組對 row → verify_plan
      (allow_prepared=True,含 source drift 與 cap 到期)→ owner 重算 target。
      任何一步不成立都在**任何** row/event/mode mutation 之前具名拒絕。
    回傳 (target, fingerprint);拒絕時 raise AuthorityError(reason ∈ PREPARED_REFUSALS)。
    """
    if bootstrap_authority_path is None:
        raise authority_store.AuthorityError("prepared_plan_authority_missing")
    has_tx = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bind_transactions'"
    ).fetchone()
    rows = [] if has_tx is None else con.execute(
        "SELECT * FROM bind_transactions WHERE plan_sha256=?", (plan_sha256,)
    ).fetchall()
    if not rows:
        raise authority_store.AuthorityError("prepared_plan_not_found")
    if len(rows) > 1:
        raise authority_store.AuthorityError("prepared_plan_not_unique")
    row = rows[0]
    if row["status"] != "prepared":
        raise authority_store.AuthorityError("prepared_plan_status_invalid")
    try:
        plan = json.loads(row["plan_json"])
        capability = json.loads(row["capability_json"])
    except (TypeError, ValueError):
        raise authority_store.AuthorityError("prepared_plan_row_mismatch")
    if (not isinstance(plan, dict) or not isinstance(capability, dict)
            or plan.get("plan_sha256") != row["plan_sha256"]
            or capability.get("capability_id") != row["capability_id"]
            or authority_store.canonical_bytes(plan).decode("utf-8") != row["plan_json"]
            or authority_store.canonical_bytes(capability).decode("utf-8")
            != row["capability_json"]):
        raise authority_store.AuthorityError("prepared_plan_row_mismatch")
    try:
        bind_plan.verify_plan(state_root, bootstrap_authority_path, con, plan,
                              capability, now_ms, allow_prepared=True)
    except authority_store.AuthorityError as err:
        reason = ("prepared_capability_expired"
                  if err.reason == "bind_capability_expired"
                  else "prepared_plan_source_drift")
        raise authority_store.AuthorityError(reason) from err
    try:
        target = bind_target.from_verified_plan(plan)
        fingerprint = bind_target.target_fingerprint(target)
    except bind_target.TargetError as err:
        raise authority_store.AuthorityError("prepared_plan_source_drift") from err
    return target, fingerprint


def _require_prepared_match(con, state_root, bootstrap_authority_path,
                            plan_sha256, supplied_fingerprint, now_ms):
    """四路共用:重算後與 supplied 比;不同就是 target_fingerprint_mismatch。"""
    _target, fingerprint = _prepared_target(
        con, state_root, bootstrap_authority_path, plan_sha256, now_ms)
    supplied = bind_target.require_fingerprint(supplied_fingerprint)
    if supplied != fingerprint:
        raise authority_store.AuthorityError("target_fingerprint_mismatch")
    return fingerprint


def _safe_notification_scope(value, subject):
    if not isinstance(value, dict) or set(value) != NOTIFICATION_SCOPE_KEYS:
        return False
    string_keys = {
        "cabin_id", "fleet_id", "fleet_member_id", "lease_id", "project_id",
        "seat_id", "session_uuid",
    }
    generation_keys = {
        "cabin_generation", "fleet_generation",
        "lease_generation", "project_generation",
    }
    return (
        _safe_subject(subject)
        and all(_plain_string(value[key]) for key in string_keys)
        and all(_strict_int(value[key]) for key in generation_keys)
        and isinstance(value["fleet_member_sha256"], str)
        and bool(HEX64.fullmatch(value["fleet_member_sha256"]))
        and value["seat_id"] == subject["seat_id"]
        and value["session_uuid"] == subject["session_uuid"]
        and bool(UUID_RE.fullmatch(value["session_uuid"]))
    )


def _safe_human_receipt(value):
    return (
        isinstance(value, dict)
        and set(value) == HUMAN_RECEIPT_KEYS
        and value.get("schema") == "ez2-aib/d17-human-approval/v1"
        and _plain_string(value.get("receipt_id"))
        and isinstance(value.get("receipt_sha256"), str)
        and bool(HEX64.fullmatch(value["receipt_sha256"]))
    )


def _safe_regular(path, *, missing_ok=False):
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return bool(missing_ok)
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == os.getuid())


def _file_identity(path, *, missing_ok=False):
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise authority_store.AuthorityError("d17_state_path_unsafe")
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid()):
        raise authority_store.AuthorityError("d17_state_path_unsafe")
    return (info.st_dev, info.st_ino, info.st_nlink, info.st_uid,
            stat.S_IFMT(info.st_mode))


def _directory_identity(path, *, missing_ok=False):
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise authority_store.AuthorityError("d17_state_path_unsafe")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise authority_store.AuthorityError("d17_state_path_unsafe")
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode))


def _read_single_link(path, *, exact_size=None):
    path = Path(path)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        before = _file_identity(path)
        descriptor = os.open(str(path), flags)
        try:
            opened = os.fstat(descriptor)
            opened_identity = (
                opened.st_dev, opened.st_ino, opened.st_nlink, opened.st_uid,
                stat.S_IFMT(opened.st_mode),
            )
            if opened_identity != before:
                raise authority_store.AuthorityError("d17_state_path_unsafe")
            chunks = []
            while True:
                chunk = os.read(descriptor, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if exact_size is not None and sum(map(len, chunks)) > exact_size:
                    raise authority_store.AuthorityError("d17_state_path_unsafe")
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_nlink, after.st_uid,
             stat.S_IFMT(after.st_mode)) != before
            or _file_identity(path) != before
        ):
            raise authority_store.AuthorityError("d17_state_path_unsafe")
        raw = b"".join(chunks)
        if exact_size is not None and len(raw) != exact_size:
            raise authority_store.AuthorityError("d17_state_path_unsafe")
        return raw
    except authority_store.AuthorityError:
        raise
    except OSError:
        raise authority_store.AuthorityError("d17_state_path_unsafe")


def _paths_safe(state_root, *, authority_path=None, missing_db_ok=False):
    root = Path(state_root)
    if not root.is_absolute() or root.resolve(strict=False) != root:
        return False
    try:
        _directory_identity(root.parent)
    except authority_store.AuthorityError:
        return False
    for item in (root, root / "fleet"):
        if os.path.lexists(item):
            info = os.lstat(item)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                return False
    db = authority_store.state_db_path(root)
    key = authority_store.binding_key_path(root)
    if os.path.lexists(db) and not _safe_regular(db):
        return False
    if not os.path.lexists(db) and not missing_db_ok:
        return False
    if os.path.lexists(key) and not _safe_regular(key):
        return False
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if os.path.lexists(side) and not _safe_regular(side):
            return False
    if authority_path is not None and not _safe_regular(authority_path):
        return False
    return True


def _open_path_snapshot(state_root, authority_path=None):
    """Pin the regular files whose names SQLite or authority checks will use."""
    db = authority_store.state_db_path(state_root)
    paths = {
        "parent_dir": Path(state_root).parent,
        "root_dir": Path(state_root),
        "fleet_dir": Path(state_root) / "fleet",
        "db": db,
        "key": authority_store.binding_key_path(state_root),
        "wal": Path(str(db) + "-wal"),
        "shm": Path(str(db) + "-shm"),
    }
    if authority_path is not None:
        paths["authority"] = Path(authority_path)
    identities = {}
    for name, path in paths.items():
        reader = _directory_identity if name.endswith("_dir") else _file_identity
        identities[name] = reader(path, missing_ok=True)
    return paths, identities


def _verify_open_path_snapshot(paths, identities):
    """Reject preflight/open aliases while allowing SQLite's new sidecars."""
    for name, path in paths.items():
        reader = _directory_identity if name.endswith("_dir") else _file_identity
        current = reader(path, missing_ok=True)
        before = identities[name]
        if name in {
            "parent_dir", "root_dir", "fleet_dir", "db", "key", "authority"
        } and current is None:
            raise authority_store.AuthorityError("d17_state_path_unsafe")
        if before is not None and current != before:
            raise authority_store.AuthorityError("d17_state_path_unsafe")
    current_identities = {}
    for name, path in paths.items():
        reader = _directory_identity if name.endswith("_dir") else _file_identity
        current_identities[name] = reader(path, missing_ok=True)
    return current_identities


def _connection_matches_state(con, state_root):
    """Prove that a caller-owned transaction is on this authority database."""
    try:
        rows = con.execute("PRAGMA database_list").fetchall()
        main_rows = [row for row in rows if row[1] == "main"]
        if len(main_rows) != 1 or not main_rows[0][2]:
            return False
        opened = Path(main_rows[0][2]).resolve(strict=False)
        expected = authority_store.state_db_path(state_root).resolve(strict=False)
        return opened == expected and _file_identity(expected) is not None
    except (authority_store.AuthorityError, OSError, sqlite3.Error):
        return False


@contextlib.contextmanager
def _guarded_immediate(con, paths, identities):
    """Use the P1A writer seam and pin every authority pathname to commit."""
    with authority_store.immediate(con):
        yield
        committed_identities = _verify_open_path_snapshot(paths, identities)
    _verify_open_path_snapshot(paths, committed_identities)


def _rollback_consume_savepoint(con):
    try:
        con.execute("ROLLBACK TO SAVEPOINT d17_bind_authority_consume")
    finally:
        con.execute("RELEASE SAVEPOINT d17_bind_authority_consume")


def _read_connection(state_root):
    db = authority_store.state_db_path(state_root)
    source_paths = [db]
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db) + suffix)
        if os.path.lexists(sidecar):
            source_paths.append(sidecar)
    try:
        first = {path.name: _read_single_link(path) for path in source_paths}
        second_paths = [db]
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(db) + suffix)
            if os.path.lexists(sidecar):
                second_paths.append(sidecar)
        second = {path.name: _read_single_link(path) for path in second_paths}
        if first != second:
            raise authority_store.AuthorityError("d17_state_path_unsafe")
        workspace = tempfile.TemporaryDirectory(
            prefix="ez2-d17-status-", dir=_PRIVATE_TMP
        )
        copy_root = Path(workspace.name)
        for name, raw in first.items():
            destination = copy_root / name
            descriptor = os.open(
                str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(descriptor)
        copied_db = copy_root / db.name
        con = sqlite3.connect(
            copied_db.as_uri() + "?mode=ro", uri=True,
            isolation_level=None, timeout=15.0,
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con, workspace
    except authority_store.AuthorityError:
        raise
    except (OSError, sqlite3.Error):
        try:
            workspace.cleanup()
        except (NameError, OSError):
            pass
        raise authority_store.AuthorityError("d17_state_path_unsafe")


def _tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS d17_mode_state(
      subject_fingerprint TEXT PRIMARY KEY,
      mode TEXT NOT NULL,
      generation INTEGER NOT NULL,
      state_json TEXT NOT NULL,
      state_sha256 TEXT NOT NULL,
      local_seal TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_pending_bind_plans(
      pending_id TEXT PRIMARY KEY,
      subject_fingerprint TEXT NOT NULL,
      mode_generation INTEGER NOT NULL,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL,
      expires_at_ms INTEGER NOT NULL,
      record_json TEXT NOT NULL,
      record_sha256 TEXT NOT NULL,
      local_seal TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_scoped_auto_grants(
      grant_id TEXT PRIMARY KEY,
      subject_fingerprint TEXT NOT NULL,
      mode_generation INTEGER NOT NULL,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL,
      expires_at_ms INTEGER NOT NULL,
      max_effects INTEGER NOT NULL CHECK(max_effects=1),
      effects_used INTEGER NOT NULL CHECK(effects_used IN (0,1)),
      receipt_sha256 TEXT NOT NULL,
      record_json TEXT NOT NULL,
      record_sha256 TEXT NOT NULL,
      local_seal TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS d17_one_active_grant
      ON d17_scoped_auto_grants(subject_fingerprint)
      WHERE status='active';
    CREATE TABLE IF NOT EXISTS d17_bind_confirmations(
      confirmation_id TEXT PRIMARY KEY,
      pending_id TEXT NOT NULL UNIQUE,
      subject_fingerprint TEXT NOT NULL,
      mode_generation INTEGER NOT NULL,
      generation INTEGER NOT NULL,
      status TEXT NOT NULL,
      expires_at_ms INTEGER NOT NULL,
      max_effects INTEGER NOT NULL CHECK(max_effects=1),
      effects_used INTEGER NOT NULL CHECK(effects_used IN (0,1)),
      receipt_sha256 TEXT NOT NULL,
      record_json TEXT NOT NULL,
      record_sha256 TEXT NOT NULL,
      local_seal TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_authorization_events(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      request_sha256 TEXT NOT NULL UNIQUE,
      stream TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      previous_sha256 TEXT,
      event_json TEXT NOT NULL,
      event_sha256 TEXT NOT NULL,
      result_json TEXT NOT NULL,
      local_seal TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_clock_floor(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      max_seen_ms INTEGER NOT NULL,
      generation INTEGER NOT NULL,
      state_json TEXT NOT NULL,
      state_sha256 TEXT NOT NULL,
      local_seal TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_notification_policies(
      policy_id TEXT PRIMARY KEY,
      subject_fingerprint TEXT NOT NULL,
      generation INTEGER NOT NULL,
      muted INTEGER NOT NULL CHECK(muted IN (0,1)),
      snoozed_until_ms INTEGER,
      record_json TEXT NOT NULL,
      record_sha256 TEXT NOT NULL,
      local_seal TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_notification_policy_events(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      request_sha256 TEXT NOT NULL UNIQUE,
      source_event_id TEXT UNIQUE,
      operation TEXT NOT NULL,
      policy_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      event_json TEXT NOT NULL,
      event_sha256 TEXT NOT NULL,
      result_json TEXT NOT NULL,
      local_seal TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL
    );
    """)


def _key(state_root):
    return _read_single_link(
        authority_store.binding_key_path(state_root), exact_size=32
    )


def _seal(state_root, domain, *parts):
    payload = domain.encode("ascii") + b"\x00" + b"\x00".join(
        part.encode("utf-8") if isinstance(part, str) else bytes(part)
        for part in parts
    )
    return hmac.new(_key(state_root), payload, hashlib.sha256).hexdigest()


def _mode_row(con, state_root, subject):
    fingerprint = _fp(subject)
    row = con.execute(
        "SELECT * FROM d17_mode_state WHERE subject_fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        return "remind_only", 0
    state_hash = _sha(row["state_json"].encode("utf-8"))
    expected = _seal(state_root, "d17-mode-v1", row["state_json"], state_hash)
    if row["state_sha256"] != state_hash or not hmac.compare_digest(
            row["local_seal"], expected):
        raise authority_store.AuthorityError("d17_state_tampered")
    value = _canonical_json(row["state_json"], "d17_state_tampered")
    if value != {
        "generation": row["generation"], "mode": row["mode"],
        "subject": subject, "subject_fingerprint": fingerprint,
    }:
        raise authority_store.AuthorityError("d17_state_tampered")
    if row["mode"] not in MODES or type(row["generation"]) is not int:
        raise authority_store.AuthorityError("d17_state_tampered")
    return row["mode"], int(row["generation"])


def _write_mode(con, state_root, subject, mode, generation):
    fingerprint = _fp(subject)
    value = {
        "generation": int(generation), "mode": mode, "subject": subject,
        "subject_fingerprint": fingerprint,
    }
    text = _canonical(value).decode("utf-8")
    value_hash = _sha(text.encode("utf-8"))
    seal = _seal(state_root, "d17-mode-v1", text, value_hash)
    con.execute("""
      INSERT INTO d17_mode_state
      (subject_fingerprint,mode,generation,state_json,state_sha256,local_seal)
      VALUES(?,?,?,?,?,?)
      ON CONFLICT(subject_fingerprint) DO UPDATE SET
        mode=excluded.mode,generation=excluded.generation,
        state_json=excluded.state_json,state_sha256=excluded.state_sha256,
        local_seal=excluded.local_seal
    """, (fingerprint, mode, int(generation), text, value_hash, seal))


def _record_parts(state_root, domain, value):
    text = _canonical(value).decode("utf-8")
    value_hash = _sha(text.encode("utf-8"))
    return text, value_hash, _seal(state_root, domain, text, value_hash)


def _canonical_json(text, reason):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise authority_store.AuthorityError(reason)
    if _canonical(value).decode("utf-8") != text:
        raise authority_store.AuthorityError(reason)
    return value


def _notification_policy_value(state_root, row):
    value_hash = _sha(row["record_json"].encode("utf-8"))
    expected = _seal(
        state_root, "d17-notification-policy-v1", row["record_json"],
        value_hash,
    )
    value = _canonical_json(
        row["record_json"], "d17_notification_state_tampered"
    )
    if (
        row["record_sha256"] != value_hash
        or not hmac.compare_digest(row["local_seal"], expected)
        or set(value) != NOTIFICATION_POLICY_KEYS
        or value.get("policy_id") != row["policy_id"]
        or not _notification_id(value.get("policy_id"))
        or not _safe_subject(value.get("subject"))
        or _fp(value["subject"]) != row["subject_fingerprint"]
        or not _safe_notification_scope(
            value.get("notification_scope"), value["subject"]
        )
        or value.get("generation") != row["generation"]
        or not _strict_int(row["generation"], minimum=1)
        or type(value.get("muted")) is not bool
        or int(value["muted"]) != row["muted"]
        or row["muted"] not in (0, 1)
        or value.get("snoozed_until_ms") != row["snoozed_until_ms"]
        or (
            value["snoozed_until_ms"] is not None
            and not _strict_int(value["snoozed_until_ms"], minimum=1)
        )
        or value.get("authority_grade") != "authenticated_owner_action"
    ):
        raise authority_store.AuthorityError(
            "d17_notification_state_tampered"
        )
    source = (
        value.get("last_reminder_id"),
        value.get("last_evaluator_receipt_sha256"),
    )
    if source != (None, None) and not (
        _notification_id(source[0])
        and isinstance(source[1], str)
        and bool(HEX64.fullmatch(source[1]))
    ):
        raise authority_store.AuthorityError(
            "d17_notification_state_tampered"
        )
    return value


def _notification_policy_row(con, state_root, policy_id):
    row = con.execute(
        "SELECT * FROM d17_notification_policies WHERE policy_id=?",
        (policy_id,),
    ).fetchone()
    return None if row is None else _notification_policy_value(state_root, row)


def _write_notification_policy(con, state_root, policy):
    text, value_hash, seal = _record_parts(
        state_root, "d17-notification-policy-v1", policy
    )
    con.execute("""
      INSERT INTO d17_notification_policies
      (policy_id,subject_fingerprint,generation,muted,snoozed_until_ms,
       record_json,record_sha256,local_seal)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(policy_id) DO UPDATE SET
        subject_fingerprint=excluded.subject_fingerprint,
        generation=excluded.generation,muted=excluded.muted,
        snoozed_until_ms=excluded.snoozed_until_ms,
        record_json=excluded.record_json,record_sha256=excluded.record_sha256,
        local_seal=excluded.local_seal
    """, (
        policy["policy_id"], _fp(policy["subject"]), policy["generation"],
        int(policy["muted"]), policy["snoozed_until_ms"], text, value_hash,
        seal,
    ))


def _append_notification_event(con, state_root, request_hash, operation,
                               policy_id, generation, source_event_id, result,
                               now_ms):
    event = {
        "schema": "ez2-aib/d17-notification-policy-event/v1",
        "request_sha256": request_hash,
        "operation": operation,
        "policy_id": policy_id,
        "generation": int(generation),
        "source_event_id": source_event_id,
        "created_at_ms": int(now_ms),
    }
    event_text = _canonical(event).decode("utf-8")
    event_hash = _sha(event_text.encode("utf-8"))
    result_text = _canonical(result).decode("utf-8")
    seal = _seal(
        state_root, "d17-notification-event-v1", event_text, event_hash,
        result_text,
    )
    con.execute("""
      INSERT INTO d17_notification_policy_events
      (request_sha256,source_event_id,operation,policy_id,generation,event_json,
       event_sha256,result_json,local_seal,created_at_ms)
      VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (
        request_hash, source_event_id, operation, policy_id, int(generation),
        event_text, event_hash, result_text, seal, int(now_ms),
    ))


def _record_value(state_root, table, row):
    spec = RECORD_SPECS[table]
    value_hash = _sha(row["record_json"].encode("utf-8"))
    expected = _seal(
        state_root, spec["domain"], row["record_json"], value_hash
    )
    if (
        row["record_sha256"] != value_hash
        or not hmac.compare_digest(row["local_seal"], expected)
    ):
        raise authority_store.AuthorityError("d17_state_tampered")
    value = _canonical_json(row["record_json"], "d17_state_tampered")
    common = (
        value.get(spec["id_key"]) == row[spec["id_column"]]
        and _safe_subject(value.get("subject"))
        and _fp(value["subject"]) == row["subject_fingerprint"]
        and value.get("mode_generation") == row["mode_generation"]
        and value.get("generation") == row["generation"]
        and value.get("status") == row["status"]
        and value.get("expires_at_ms") == row["expires_at_ms"]
        and row["status"] in spec["statuses"]
        and _strict_int(row["mode_generation"])
        and _strict_int(row["generation"], minimum=1)
        and _strict_int(row["expires_at_ms"])
    )
    if not common:
        raise authority_store.AuthorityError("d17_state_tampered")
    if table == "d17_pending_bind_plans":
        expected_keys = PENDING_KEYS | {
            "generation", "mode_generation", "status",
        }
    elif table == "d17_scoped_auto_grants":
        expected_keys = GRANT_KEYS | {
            "generation", "mode_generation", "status", "effects_used",
            "authority_grade", "receipt_sha256",
        }
    else:
        expected_keys = {
            "confirmation_id", "pending_id", "subject", "scope",
            "plan_sha256", "target_fingerprint", "expires_at_ms",
            "generation", "mode_generation", "status", "max_effects",
            "effects_used", "authority_grade", "receipt_sha256",
        }
    if set(value) != expected_keys:
        raise authority_store.AuthorityError("d17_state_tampered")
    if table != "d17_pending_bind_plans":
        if (
            value.get("max_effects") != 1
            or type(value.get("max_effects")) is not int
            or value.get("effects_used") not in (0, 1)
            or type(value.get("effects_used")) is not int
            or value.get("effects_used") != row["effects_used"]
            or value.get("max_effects") != row["max_effects"]
            or value.get("receipt_sha256") != row["receipt_sha256"]
            or not HEX64.fullmatch(str(value.get("receipt_sha256")))
            or value.get("authority_grade") != "authenticated_owner_action"
        ):
            raise authority_store.AuthorityError("d17_state_tampered")
    return value


def _transition_record(con, state_root, table, entity_id, status, *,
                       effects_used=None):
    spec = RECORD_SPECS[table]
    row = con.execute(
        "SELECT * FROM %s WHERE %s=?" % (table, spec["id_column"]),
        (entity_id,),
    ).fetchone()
    if row is None:
        raise authority_store.AuthorityError("d17_state_tampered")
    value = _record_value(state_root, table, row)
    value["status"] = status
    value["generation"] = int(row["generation"]) + 1
    if effects_used is not None:
        value["effects_used"] = int(effects_used)
    text, value_hash, seal = _record_parts(state_root, spec["domain"], value)
    if table == "d17_pending_bind_plans":
        con.execute(
            "UPDATE d17_pending_bind_plans SET generation=?,status=?,"
            "record_json=?,record_sha256=?,local_seal=? WHERE pending_id=?",
            (value["generation"], status, text, value_hash, seal, entity_id),
        )
    else:
        con.execute(
            "UPDATE %s SET generation=?,status=?,effects_used=?,"
            "record_json=?,record_sha256=?,local_seal=? WHERE %s=?"
            % (table, spec["id_column"]),
            (value["generation"], status, value["effects_used"], text,
             value_hash, seal, entity_id),
        )
    return value


def _validate_integrity(con, state_root, *, include_notifications=False):
    for row in con.execute("SELECT * FROM d17_mode_state"):
        value_hash = _sha(row["state_json"].encode("utf-8"))
        expected = _seal(state_root, "d17-mode-v1", row["state_json"], value_hash)
        value = _canonical_json(row["state_json"], "d17_state_tampered")
        if (
            row["state_sha256"] != value_hash
            or not hmac.compare_digest(row["local_seal"], expected)
            or not _safe_subject(value.get("subject"))
            or value != {
                "generation": row["generation"], "mode": row["mode"],
                "subject": value["subject"],
                "subject_fingerprint": row["subject_fingerprint"],
            }
            or _fp(value["subject"]) != row["subject_fingerprint"]
            or row["mode"] not in MODES
            or not _strict_int(row["generation"])
        ):
            raise authority_store.AuthorityError("d17_state_tampered")
    for table in RECORD_SPECS:
        for row in con.execute("SELECT * FROM %s" % table):
            _record_value(state_root, table, row)
    notification_tables = set()
    if include_notifications:
        notification_tables = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('d17_notification_policies',"
                "'d17_notification_policy_events')"
            )
        }
    if notification_tables and notification_tables != {
        "d17_notification_policies", "d17_notification_policy_events",
    }:
        raise authority_store.AuthorityError(
            "d17_notification_state_tampered"
        )
    if notification_tables:
        notification_policies = {}
        for row in con.execute("SELECT * FROM d17_notification_policies"):
            notification_policies[row["policy_id"]] = (
                _notification_policy_value(state_root, row)
            )
        notification_history = {}
        for row in con.execute(
                "SELECT * FROM d17_notification_policy_events ORDER BY seq"):
            event_hash = _sha(row["event_json"].encode("utf-8"))
            expected = _seal(
                state_root, "d17-notification-event-v1", row["event_json"],
                event_hash, row["result_json"],
            )
            event = _canonical_json(
                row["event_json"], "d17_notification_state_tampered"
            )
            result = _canonical_json(
                row["result_json"], "d17_notification_state_tampered"
            )
            source_event_id = row["source_event_id"]
            source_valid = (
                source_event_id is None or _notification_id(source_event_id)
            )
            exact = event == {
                "schema": "ez2-aib/d17-notification-policy-event/v1",
                "request_sha256": row["request_sha256"],
                "operation": row["operation"],
                "policy_id": row["policy_id"],
                "generation": row["generation"],
                "source_event_id": source_event_id,
                "created_at_ms": row["created_at_ms"],
            }
            projection = result.get("notification_policy", {}) \
                if isinstance(result, dict) else {}
            authorization_event = con.execute(
                "SELECT stream,entity_id,generation,result_json "
                "FROM d17_authorization_events WHERE request_sha256=?",
                (row["request_sha256"],),
            ).fetchone()
            if (
                row["event_sha256"] != event_hash
                or not hmac.compare_digest(row["local_seal"], expected)
                or not exact
                or row["operation"] not in NOTIFICATION_OPERATIONS
                or not _notification_id(row["policy_id"])
                or not _strict_int(row["generation"], minimum=1)
                or not _strict_int(row["created_at_ms"])
                or not isinstance(row["request_sha256"], str)
                or not HEX64.fullmatch(row["request_sha256"])
                or not source_valid
                or not isinstance(result, dict)
                or result.get("effect_count") != 0
                or type(result.get("effect_count")) is not int
                or result.get("reminder_count") != 0
                or type(result.get("reminder_count")) is not int
                or result.get("triggered") is not False
                or result.get("ok") is not True
                or result.get("rc") != 0
                or projection.get("policy_id") != row["policy_id"]
                or projection.get("generation") != row["generation"]
                or authorization_event is None
                or authorization_event["stream"] != row["operation"]
                or authorization_event["entity_id"] != row["policy_id"]
                or authorization_event["generation"] != row["generation"]
                or authorization_event["result_json"] != row["result_json"]
                or (
                    row["operation"] == "snooze_notifications"
                    and source_event_id is None
                )
                or (
                    row["operation"] == "set_notification_policy"
                    and source_event_id is not None
                )
            ):
                raise authority_store.AuthorityError(
                    "d17_notification_state_tampered"
                )
            notification_history.setdefault(row["policy_id"], []).append(
                (row["generation"], projection)
            )
        if set(notification_history) != set(notification_policies):
            raise authority_store.AuthorityError(
                "d17_notification_state_tampered"
            )
        for policy_id, policy in notification_policies.items():
            history = notification_history[policy_id]
            if (
                [generation for generation, _projection in history]
                != list(range(1, policy["generation"] + 1))
                or history[-1][1] != policy
            ):
                raise authority_store.AuthorityError(
                    "d17_notification_state_tampered"
                )
    for table, live_status in (
        ("d17_pending_bind_plans", "pending"),
        ("d17_bind_confirmations", "active"),
    ):
        for row in con.execute(
            "SELECT * FROM %s WHERE status=?" % table, (live_status,)
        ):
            value = _record_value(state_root, table, row)
            mode, mode_generation = _mode_row(
                con, state_root, value["subject"]
            )
            if (
                mode != "confirm_each"
                or mode_generation != row["mode_generation"]
            ):
                raise authority_store.AuthorityError("d17_state_tampered")
    for row in con.execute(
        "SELECT * FROM d17_scoped_auto_grants WHERE status='active'"
    ):
        value = _record_value(
            state_root, "d17_scoped_auto_grants", row
        )
        mode, mode_generation = _mode_row(con, state_root, value["subject"])
        if (
            mode != "scoped_auto"
            or mode_generation != row["mode_generation"] + 1
        ):
            raise authority_store.AuthorityError("d17_state_tampered")
    for row in con.execute(
        "SELECT * FROM d17_mode_state WHERE mode='scoped_auto'"
    ):
        active = con.execute(
            "SELECT COUNT(*) FROM d17_scoped_auto_grants "
            "WHERE subject_fingerprint=? AND status='active'",
            (row["subject_fingerprint"],),
        ).fetchone()[0]
        if active != 1:
            raise authority_store.AuthorityError("d17_state_tampered")
    clock = con.execute(
        "SELECT * FROM d17_clock_floor WHERE singleton=1"
    ).fetchone()
    if clock is not None:
        value_hash = _sha(clock["state_json"].encode("utf-8"))
        expected = _seal(
            state_root, "d17-clock-v1", clock["state_json"], value_hash
        )
        value = _canonical_json(clock["state_json"], "d17_state_tampered")
        if (
            clock["state_sha256"] != value_hash
            or not hmac.compare_digest(clock["local_seal"], expected)
            or value != {
                "max_seen_ms": clock["max_seen_ms"],
                "generation": clock["generation"],
            }
            or not _strict_int(clock["max_seen_ms"])
            or not _strict_int(clock["generation"], minimum=1)
        ):
            raise authority_store.AuthorityError("d17_state_tampered")
    previous = None
    for row in con.execute("SELECT * FROM d17_authorization_events ORDER BY seq"):
        event_hash = _sha(row["event_json"].encode("utf-8"))
        expected = _seal(state_root, "d17-event-v1", row["event_json"],
                         event_hash, row["result_json"])
        event_value = _canonical_json(
            row["event_json"], "d17_event_tampered"
        )
        _canonical_json(row["result_json"], "d17_event_tampered")
        columns_match = isinstance(event_value, dict) and (
            event_value.get("request_sha256") == row["request_sha256"]
            and event_value.get("operation") == row["stream"]
            and event_value.get("entity_id") == row["entity_id"]
            and event_value.get("generation") == row["generation"]
            and event_value.get("previous_sha256") == row["previous_sha256"]
        )
        if (row["event_sha256"] != event_hash
                or row["previous_sha256"] != previous
                or not columns_match
                or not hmac.compare_digest(row["local_seal"], expected)):
            raise authority_store.AuthorityError("d17_event_tampered")
        previous = event_hash


def _clock(con, state_root, now_ms):
    row = con.execute("SELECT * FROM d17_clock_floor WHERE singleton=1").fetchone()
    floor = int(row["max_seen_ms"]) if row else int(now_ms)
    effective = max(int(now_ms), floor)
    generation = (int(row["generation"]) + 1) if row else 1
    value = {"max_seen_ms": effective, "generation": generation}
    text, value_hash, seal = _record_parts(state_root, "d17-clock-v1", value)
    con.execute("""
      INSERT INTO d17_clock_floor
      (singleton,max_seen_ms,generation,state_json,state_sha256,local_seal)
      VALUES(1,?,?,?,?,?)
      ON CONFLICT(singleton) DO UPDATE SET
        max_seen_ms=excluded.max_seen_ms,generation=excluded.generation,
        state_json=excluded.state_json,state_sha256=excluded.state_sha256,
        local_seal=excluded.local_seal
    """, (effective, generation, text, value_hash, seal))
    return effective, int(now_ms) < floor


def _request_hash(request):
    return authority_store.canonical_request_hash(request)


def _recover(con, request_hash):
    row = con.execute(
        "SELECT result_json FROM d17_authorization_events WHERE request_sha256=?",
        (request_hash,),
    ).fetchone()
    return json.loads(row["result_json"]) if row else None


def _receipt(operation, reason, entity_id, generation, request_hash, now_ms):
    body = {
        "schema": "ez2-aib/d17-authority-receipt/v1",
        "receipt_id": "d17b-" + request_hash[:24],
        "operation": operation, "reason": reason, "entity_id": entity_id,
        "generation": int(generation), "request_sha256": request_hash,
        "created_at_ms": int(now_ms), "effect_count": 0,
    }
    return dict(body, receipt_sha256=_sha(body))


def _append_event(con, state_root, request_hash, operation, reason, entity_id,
                  generation, result, now_ms):
    head = con.execute(
        "SELECT event_sha256 FROM d17_authorization_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    previous = head["event_sha256"] if head else None
    event = {
        "schema": "ez2-aib/d17-authority-event/v1",
        "operation": operation, "reason": reason, "entity_id": entity_id,
        "generation": int(generation), "request_sha256": request_hash,
        "previous_sha256": previous, "created_at_ms": int(now_ms),
    }
    event_text = _canonical(event).decode("utf-8")
    event_hash = _sha(event_text.encode("utf-8"))
    result_text = _canonical(result).decode("utf-8")
    seal = _seal(state_root, "d17-event-v1", event_text, event_hash, result_text)
    con.execute("""
      INSERT INTO d17_authorization_events
      (request_sha256,stream,entity_id,generation,previous_sha256,event_json,
       event_sha256,result_json,local_seal,created_at_ms)
      VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (request_hash, operation, entity_id, int(generation), previous,
          event_text, event_hash, result_text, seal, int(now_ms)))


def _owner_result(con, state_root, authority_path, request, now_ms,
                  approval_consumer, action, subject, entity_id,
                  mode_generation, entity_generation, plan_sha=None,
                  target_fp=None, scope=None):
    if approval_consumer is None:
        raise authority_store.AuthorityError("human_approval_consumer_missing")
    if getattr(approval_consumer, "authority_grade", None) \
            != "authenticated_human_action":
        raise authority_store.AuthorityError("human_approval_consumer_untrusted")
    if not _safe_human_receipt(request.get("human_receipt")):
        raise authority_store.AuthorityError("human_approval_receipt_invalid")
    authority_identity = _file_identity(authority_path)
    verified = authority_store.verify_owner_approval(
        authority_path, request, now_ms, required_kind="owner", action=action,
        subject_id=_fp(subject),
    )
    if _file_identity(authority_path) != authority_identity:
        raise authority_store.AuthorityError("d17_state_path_unsafe")
    if verified.get("authority_grade") == "bootstrap_registry":
        raise authority_store.AuthorityError("owner_authority_bootstrap_forbidden")
    expected = {
        "schema": "ez2-aib/d17-human-approval-binding/v1",
        "audience": "ez2-aib/d17-bind-authority/v1",
        "action": action,
        "request_sha256": _request_hash(request),
        "authority_id": verified["authority_id"],
        "authority_generation": verified["generation"],
        "subject_fingerprint": _fp(subject),
        "entity_id": entity_id,
        "mode_generation": int(mode_generation),
        "entity_generation": int(entity_generation),
        "plan_sha256": plan_sha,
        "target_fingerprint": target_fp,
        "scope_sha256": _sha(scope) if scope is not None else None,
    }
    authority_store.consume_proof(con, verified, now_ms)
    try:
        consumed = approval_consumer.consume_in_transaction(
            request["human_receipt"], expected, con=con
        )
    except Exception as error:
        raise authority_store.AuthorityError(
            "human_approval_consume_failed", str(type(error).__name__)
        )
    required = {
        "status": "consumed",
        "receipt_id": request["human_receipt"]["receipt_id"],
        "receipt_sha256": request["human_receipt"]["receipt_sha256"],
        "authority_grade": "authenticated_human_action",
    }
    if consumed != required:
        raise authority_store.AuthorityError("human_approval_consumer_untrusted")
    return verified, consumed


def _request_keys(operation):
    proof = {"authority_proof", "owner_approval", "main_approval", "approval_id"}
    if operation == "status":
        return {"operation", "subject"}
    if operation == "stage_pending":
        return {"operation", "pending", "expected_mode_generation"}
    if operation == "set_mode":
        return {"operation", "subject", "mode", "expected_mode_generation",
                "human_receipt"} | proof
    if operation == "confirm":
        return {"operation", "confirmation_id", "pending_id", "subject",
                "scope", "plan_sha256", "target_fingerprint", "expires_at_ms",
                "expected_mode_generation", "expected_generation",
                "human_receipt"} | proof
    if operation == "grant":
        return {"operation", "grant", "expected_mode_generation",
                "expected_generation", "human_receipt"} | proof
    if operation == "revoke":
        return {"operation", "grant_id", "subject", "reason_code",
                "expected_mode_generation", "expected_generation",
                "human_receipt"} | proof
    if operation == "expire_due":
        return {"operation"}
    if operation == "snooze_notifications":
        return {
            "operation", "policy_id", "reminder_id",
            "evaluator_receipt_sha256", "subject", "notification_scope",
            "until_ms", "expected_generation", "human_receipt",
        } | proof
    if operation == "set_notification_policy":
        return {
            "operation", "policy_id", "subject", "notification_scope",
            "muted", "expected_generation", "human_receipt",
        } | proof
    return None


def _closed_request(request):
    if not isinstance(request, dict) or not isinstance(request.get("operation"), str):
        return False
    allowed = _request_keys(request["operation"])
    if allowed is None:
        return None
    keys = set(request)
    if request["operation"] in HUMAN_MUTATION_OPERATIONS:
        proof_keys = keys & {"authority_proof", "owner_approval", "main_approval",
                             "approval_id"}
        if len(proof_keys) != 1:
            return False
        required = allowed - {"authority_proof", "owner_approval", "main_approval",
                              "approval_id"}
        return keys == required | proof_keys
    return keys == allowed


def _status(state_root, subject):
    if not _safe_subject(subject):
        return _base("d17_request_invalid", rc=2)
    root = Path(state_root)
    db = authority_store.state_db_path(root)
    if not os.path.lexists(root) or not os.path.lexists(db):
        return _base("status_unbound", ok=True, rc=0)
    if not _paths_safe(root, missing_db_ok=False):
        return _base("d17_state_path_unsafe", rc=2)
    try:
        guard_paths, guard_identities = _open_path_snapshot(root)
        con, workspace = _read_connection(root)
        try:
            guard_identities = _verify_open_path_snapshot(
                guard_paths, guard_identities
            )
            table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='d17_mode_state'"
            ).fetchone()
            if not table:
                return _base("status_unbound", ok=True, rc=0)
            _key(root)
            _validate_integrity(con, root)
            mode, generation = _mode_row(con, root, subject)
            _verify_open_path_snapshot(guard_paths, guard_identities)
            return _base("status_ok", ok=True, rc=0, mode=mode,
                         generation=generation)
        finally:
            con.close()
            workspace.cleanup()
    except authority_store.AuthorityError as error:
        return _base(error.reason, rc=error.rc)
    except Exception:
        return _base("d17_state_unreadable", rc=2)


def _execute_notification(con, state_root, authority_path, request,
                          effective_now, approval_consumer, request_hash):
    operation = request["operation"]
    subject = request.get("subject")
    scope = request.get("notification_scope")
    policy_id = request.get("policy_id")
    expected_generation = request.get("expected_generation")
    valid = (
        _safe_subject(subject)
        and _safe_notification_scope(scope, subject)
        and _notification_id(policy_id)
        and _strict_int(expected_generation)
        and _safe_human_receipt(request.get("human_receipt"))
    )
    if operation == "snooze_notifications":
        valid = valid and (
            _notification_id(request.get("reminder_id"))
            and isinstance(request.get("evaluator_receipt_sha256"), str)
            and bool(HEX64.fullmatch(request["evaluator_receipt_sha256"]))
        )
    else:
        valid = valid and type(request.get("muted")) is bool
    if not valid:
        return _base(
            "notification_scope_invalid", rc=1, notification_policy=None
        )

    current_policy = _notification_policy_row(
        con, state_root, policy_id
    )
    current_generation = (
        current_policy["generation"] if current_policy is not None else 0
    )
    if current_policy is not None and (
        current_policy["subject"] != subject
        or current_policy["notification_scope"] != scope
    ):
        mode, mode_generation = _mode_row(con, state_root, subject)
        return _base(
            "notification_scope_mismatch", rc=1, mode=mode,
            generation=mode_generation, notification_policy=current_policy,
        )
    if expected_generation != current_generation:
        reason = (
            "notification_generation_rollback"
            if expected_generation < current_generation
            else "notification_generation_conflict"
        )
        mode, mode_generation = _mode_row(con, state_root, subject)
        return _base(
            reason, rc=1, mode=mode, generation=mode_generation,
            notification_policy=current_policy,
        )

    source_event_id = request.get("reminder_id")
    if source_event_id is not None:
        seen = con.execute(
            "SELECT request_sha256 FROM d17_notification_policy_events "
            "WHERE source_event_id=?", (source_event_id,)
        ).fetchone()
        if seen is not None and seen["request_sha256"] != request_hash:
            mode, mode_generation = _mode_row(con, state_root, subject)
            return _base(
                "notification_source_event_conflict", rc=1, mode=mode,
                generation=mode_generation,
                notification_policy=current_policy,
            )

    if operation == "snooze_notifications":
        until_ms = request.get("until_ms")
        if (
            not _strict_int(until_ms, minimum=1)
            or until_ms <= effective_now
            or until_ms - effective_now > DAY_MS
        ):
            mode, mode_generation = _mode_row(con, state_root, subject)
            return _base(
                "notification_snooze_ttl_invalid", rc=1, mode=mode,
                generation=mode_generation,
                notification_policy=current_policy,
            )

    action = (
        "d17.notifications.snooze"
        if operation == "snooze_notifications"
        else "d17.notifications.policy.set"
    )
    _owner_result(
        con, state_root, authority_path, request, effective_now,
        approval_consumer, action, subject, policy_id, 0,
        expected_generation, scope=scope,
    )
    if current_policy is None:
        policy = {
            "policy_id": policy_id,
            "subject": subject,
            "notification_scope": scope,
            "generation": 0,
            "muted": False,
            "snoozed_until_ms": None,
            "last_reminder_id": None,
            "last_evaluator_receipt_sha256": None,
            "authority_grade": "authenticated_owner_action",
        }
    else:
        policy = dict(current_policy)
    policy["generation"] = current_generation + 1
    if operation == "snooze_notifications":
        policy["snoozed_until_ms"] = request["until_ms"]
        policy["last_reminder_id"] = request["reminder_id"]
        policy["last_evaluator_receipt_sha256"] = request[
            "evaluator_receipt_sha256"
        ]
        reason = "notifications_snoozed"
    else:
        policy["muted"] = request["muted"]
        reason = (
            "notification_policy_muted" if request["muted"]
            else "notification_policy_unmuted"
        )
    _write_notification_policy(con, state_root, policy)
    mode, mode_generation = _mode_row(con, state_root, subject)
    receipt = _receipt(
        operation, reason, policy_id, policy["generation"], request_hash,
        effective_now,
    )
    result = _base(
        reason, ok=True, rc=0, mode=mode, generation=mode_generation,
        receipt=receipt, notification_policy=policy,
    )
    _append_notification_event(
        con, state_root, request_hash, operation, policy_id,
        policy["generation"], source_event_id, result, effective_now,
    )
    _append_event(
        con, state_root, request_hash, operation, reason, policy_id,
        policy["generation"], result, effective_now,
    )
    return result


def execute(state_root: Path, authority_path: Path, request: dict, now_ms: int,
            *, approval_consumer=None, bootstrap_authority_path=None) -> dict:
    """`authority_path` 是 HMAC owner authority(人類授權);
    `bootstrap_authority_path` 是 P1A bootstrap authority(verify_plan 用)。
    兩者格式互斥(authority_store.load_authority 嚴格),不能共用一檔。
    stage/confirm/grant 缺後者一律 fail-closed:prepared_plan_authority_missing。"""
    if not _strict_int(now_ms):
        return _base("d17_request_invalid", rc=2)
    closed = _closed_request(request)
    if closed is None:
        return _base("d17_operation_invalid", rc=2)
    if not closed:
        return _base("d17_request_invalid", rc=2)
    operation = request["operation"]
    if operation == "status":
        return _status(state_root, request["subject"])
    if operation == "expire_due" and not os.path.lexists(
            authority_store.state_db_path(state_root)):
        return _base("nothing_due", ok=True, rc=0)
    if operation in HUMAN_MUTATION_OPERATIONS:
        if approval_consumer is None:
            return _base("human_approval_consumer_missing", rc=2)
        if not _paths_safe(state_root, authority_path=authority_path,
                           missing_db_ok=True):
            return _base("d17_state_path_unsafe", rc=2)
    elif not _paths_safe(state_root, missing_db_ok=True):
        return _base("d17_state_path_unsafe", rc=2)

    try:
        guarded_authority = (
            authority_path
            if operation in HUMAN_MUTATION_OPERATIONS
            else None
        )
        guard_paths, guard_identities = _open_path_snapshot(
            state_root, guarded_authority
        )
        con = authority_store.open_db(state_root)
        try:
            guard_identities = _verify_open_path_snapshot(
                guard_paths, guard_identities
            )
            # sqlite3.executescript() commits an open transaction.  Schema
            # setup must therefore finish before the caller-owned writer
            # transaction begins; proof and human receipt consumption remain
            # inside the single BEGIN IMMEDIATE below.
            _tables(con)
            guard_identities = _verify_open_path_snapshot(
                guard_paths, guard_identities
            )
            with _guarded_immediate(con, guard_paths, guard_identities):
                _validate_integrity(
                    con, state_root,
                    include_notifications=operation in NOTIFICATION_OPERATIONS,
                )
                effective_now, rolled_back = _clock(con, state_root, now_ms)
                req_hash = _request_hash(request)
                recovered = _recover(con, req_hash)
                if recovered is not None:
                    return recovered

                if operation in NOTIFICATION_OPERATIONS:
                    return _execute_notification(
                        con, state_root, authority_path, request,
                        effective_now, approval_consumer, req_hash,
                    )

                if operation == "stage_pending":
                    pending = request["pending"]
                    if not isinstance(pending, dict) or set(pending) != PENDING_KEYS \
                            or not _strict_int(request["expected_mode_generation"]) \
                            or not _entity_id(pending.get("pending_id")) \
                            or not _safe_subject(pending.get("subject")) \
                            or not _safe_scope(pending.get("scope")) \
                            or not HEX64.fullmatch(str(pending.get("plan_sha256"))) \
                            or not HEX64.fullmatch(str(pending.get("target_fingerprint"))) \
                            or pending.get("trigger_kind") not in {"ctx_threshold", "content"} \
                            or not _strict_int(pending.get("created_at_ms")):
                        return _base("pending_invalid", rc=1)
                    try:
                        _require_prepared_match(
                            con, state_root, bootstrap_authority_path,
                            pending["plan_sha256"], pending["target_fingerprint"],
                            effective_now)
                    except authority_store.AuthorityError as err:
                        return _base(err.reason, rc=1)
                    mode, generation = _mode_row(con, state_root, pending["subject"])
                    if mode != "confirm_each":
                        return _base("pending_requires_confirm_each", rc=1,
                                     mode=mode, generation=generation)
                    if request["expected_mode_generation"] != generation:
                        reason = ("mode_generation_rollback"
                                  if request["expected_mode_generation"] < generation
                                  else "mode_generation_conflict")
                        return _base(reason, rc=1,
                                     mode=mode, generation=generation)
                    if (type(pending["expires_at_ms"]) is not int
                            or pending["expires_at_ms"] <= effective_now
                            or pending["expires_at_ms"] - effective_now > TEN_MINUTES_MS):
                        return _base("pending_ttl_exceeded", rc=1, mode=mode,
                                     generation=generation)
                    record = dict(pending, status="pending", generation=1,
                                  mode_generation=generation)
                    text, record_hash, seal = _record_parts(
                        state_root, "d17-pending-v1", record
                    )
                    try:
                        con.execute("""
                          INSERT INTO d17_pending_bind_plans
                          (pending_id,subject_fingerprint,mode_generation,generation,
                           status,expires_at_ms,record_json,record_sha256,local_seal)
                          VALUES(?,?,?,?,?,?,?,?,?)
                        """, (pending["pending_id"], _fp(pending["subject"]),
                              generation, 1, "pending", pending["expires_at_ms"],
                              text, record_hash, seal))
                    except sqlite3.IntegrityError:
                        return _base("pending_replay_conflict", rc=1, mode=mode,
                                     generation=generation)
                    receipt = _receipt(operation, "pending_created",
                                       pending["pending_id"], 1, req_hash, effective_now)
                    result = _base("pending_created", ok=True, rc=0, mode=mode,
                                   generation=generation, receipt=receipt,
                                   pending=record)
                    _append_event(con, state_root, req_hash, operation,
                                  "pending_created", pending["pending_id"], 1,
                                  result, effective_now)
                    return result

                if operation == "set_mode":
                    subject = request["subject"]
                    if (not _safe_subject(subject) or request["mode"] not in MODES
                            or not _strict_int(
                                request["expected_mode_generation"]
                            )
                            or not _safe_human_receipt(
                                request.get("human_receipt")
                            )):
                        return _base("mode_config_invalid", rc=1)
                    mode, generation = _mode_row(con, state_root, subject)
                    if request["mode"] == "scoped_auto":
                        return _base("scoped_auto_requires_grant", rc=1,
                                     mode=mode, generation=generation)
                    if request["expected_mode_generation"] != generation:
                        reason = ("mode_generation_rollback"
                                  if request["expected_mode_generation"] < generation
                                  else "mode_generation_conflict")
                        return _base(reason, rc=1,
                                     mode=mode, generation=generation)
                    verified, _human = _owner_result(
                        con, state_root, authority_path, request, effective_now,
                        approval_consumer, "d17.mode.set", subject, _fp(subject),
                        generation, 0,
                    )
                    new_generation = generation + 1
                    _write_mode(con, state_root, subject, request["mode"], new_generation)
                    fp = _fp(subject)
                    for row in con.execute(
                        "SELECT pending_id FROM d17_pending_bind_plans "
                        "WHERE subject_fingerprint=? AND status='pending'", (fp,)
                    ).fetchall():
                        _transition_record(
                            con, state_root, "d17_pending_bind_plans",
                            row["pending_id"], "superseded",
                        )
                    for table, column in (
                        ("d17_bind_confirmations", "confirmation_id"),
                        ("d17_scoped_auto_grants", "grant_id"),
                    ):
                        rows = con.execute(
                            "SELECT %s FROM %s WHERE subject_fingerprint=? "
                            "AND status='active'" % (column, table), (fp,)
                        ).fetchall()
                        for row in rows:
                            _transition_record(
                                con, state_root, table, row[column], "revoked"
                            )
                    receipt = _receipt(operation, "mode_set", fp,
                                       new_generation, req_hash, effective_now)
                    result = _base("mode_set", ok=True, rc=0,
                                   mode=request["mode"], generation=new_generation,
                                   receipt=receipt)
                    _append_event(con, state_root, req_hash, operation, "mode_set",
                                  fp, new_generation, result, effective_now)
                    return result

                if operation == "confirm":
                    subject = request["subject"]
                    if (not _safe_subject(subject)
                            or not _safe_scope(request["scope"])
                            or not _entity_id(request["confirmation_id"])
                            or not _entity_id(request["pending_id"])
                            or not HEX64.fullmatch(str(request["plan_sha256"]))
                            or not HEX64.fullmatch(
                                str(request["target_fingerprint"])
                            )
                            or not _strict_int(
                                request["expected_mode_generation"]
                            )
                            or not _strict_int(request["expected_generation"])
                            or not _safe_human_receipt(
                                request.get("human_receipt")
                            )):
                        return _base("confirmation_invalid", rc=1)
                    try:
                        _require_prepared_match(
                            con, state_root, bootstrap_authority_path,
                            request["plan_sha256"], request["target_fingerprint"],
                            effective_now)
                    except authority_store.AuthorityError as err:
                        return _base(err.reason, rc=1)
                    mode, generation = _mode_row(con, state_root, subject)
                    if request["expected_mode_generation"] != generation:
                        return _base("mode_generation_conflict", rc=1,
                                     mode=mode, generation=generation)
                    existing = con.execute(
                        "SELECT * FROM d17_bind_confirmations WHERE confirmation_id=?",
                        (request["confirmation_id"],),
                    ).fetchone()
                    entity_generation = int(existing["generation"]) if existing else 0
                    if request["expected_generation"] != entity_generation:
                        reason = ("confirmation_generation_rollback"
                                  if request["expected_generation"] < entity_generation
                                  else "confirmation_generation_conflict")
                        return _base(reason, rc=1, mode=mode,
                                     generation=generation)
                    if (type(request["expires_at_ms"]) is not int
                            or request["expires_at_ms"] <= effective_now
                            or request["expires_at_ms"] - effective_now > TEN_MINUTES_MS):
                        return _base("confirmation_ttl_exceeded", rc=1,
                                     mode=mode, generation=generation)
                    pending = con.execute(
                        "SELECT * FROM d17_pending_bind_plans WHERE pending_id=?",
                        (request["pending_id"],),
                    ).fetchone()
                    if pending is None or pending["status"] != "pending":
                        return _base("pending_exact_binding_mismatch", rc=1,
                                     mode=mode, generation=generation)
                    if pending["expires_at_ms"] <= effective_now:
                        _transition_record(
                            con, state_root, "d17_pending_bind_plans",
                            request["pending_id"], "expired",
                        )
                        return _base("pending_expired", rc=1,
                                     mode=mode, generation=generation)
                    pending_value = json.loads(pending["record_json"])
                    exact = (
                        pending_value["subject"] == subject
                        and pending_value["scope"] == request["scope"]
                        and pending_value["plan_sha256"] == request["plan_sha256"]
                        and pending_value["target_fingerprint"] == request["target_fingerprint"]
                        and pending["mode_generation"] == generation
                    )
                    if not exact:
                        return _base("pending_exact_binding_mismatch", rc=1,
                                     mode=mode, generation=generation)
                    if existing:
                        return _base("confirmation_replay_conflict", rc=1,
                                     mode=mode, generation=generation)
                    _owner_result(
                        con, state_root, authority_path, request, effective_now,
                        approval_consumer, "d17.confirm", subject,
                        request["confirmation_id"], generation, 0,
                        request["plan_sha256"], request["target_fingerprint"],
                        request["scope"],
                    )
                    receipt = _receipt(operation, "confirmation_issued",
                                       request["confirmation_id"], 1, req_hash,
                                       effective_now)
                    record = {
                        "confirmation_id": request["confirmation_id"],
                        "pending_id": request["pending_id"], "subject": subject,
                        "scope": request["scope"],
                        "plan_sha256": request["plan_sha256"],
                        "target_fingerprint": request["target_fingerprint"],
                        "expires_at_ms": request["expires_at_ms"],
                        "generation": 1, "mode_generation": generation,
                        "status": "active", "max_effects": 1, "effects_used": 0,
                        "authority_grade": "authenticated_owner_action",
                        "receipt_sha256": receipt["receipt_sha256"],
                    }
                    text, record_hash, seal = _record_parts(
                        state_root, "d17-confirmation-v1", record
                    )
                    con.execute("""
                      INSERT INTO d17_bind_confirmations
                      (confirmation_id,pending_id,subject_fingerprint,mode_generation,
                       generation,status,expires_at_ms,max_effects,effects_used,
                       receipt_sha256,record_json,record_sha256,local_seal)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (request["confirmation_id"], request["pending_id"],
                          _fp(subject), generation, 1, "active",
                          request["expires_at_ms"], 1, 0,
                          receipt["receipt_sha256"], text, record_hash, seal))
                    _transition_record(
                        con, state_root, "d17_pending_bind_plans",
                        request["pending_id"], "confirmed",
                    )
                    result = _base("confirmation_issued", ok=True, rc=0,
                                   mode=mode, generation=generation,
                                   receipt=receipt, confirmation=record)
                    _append_event(con, state_root, req_hash, operation,
                                  "confirmation_issued", request["confirmation_id"],
                                  1, result, effective_now)
                    return result

                if operation == "grant":
                    grant = request["grant"]
                    if (not isinstance(grant, dict) or set(grant) != GRANT_KEYS
                            or not _entity_id(grant.get("grant_id"))
                            or not _safe_subject(grant.get("subject"))
                            or not _safe_scope(grant.get("scope"))
                            or not HEX64.fullmatch(str(grant.get("plan_sha256")))
                            or not HEX64.fullmatch(str(grant.get("target_fingerprint")))
                            or not _strict_int(
                                request["expected_mode_generation"]
                            )
                            or not _strict_int(request["expected_generation"])
                            or not _safe_human_receipt(
                                request.get("human_receipt")
                            )):
                        return _base("grant_scope_invalid", rc=1)
                    try:
                        _require_prepared_match(
                            con, state_root, bootstrap_authority_path,
                            grant["plan_sha256"], grant["target_fingerprint"],
                            effective_now)
                    except authority_store.AuthorityError as err:
                        return _base(err.reason, rc=1)
                    subject = grant["subject"]
                    mode, generation = _mode_row(con, state_root, subject)
                    if request["expected_mode_generation"] != generation:
                        return _base("mode_generation_conflict", rc=1,
                                     mode=mode, generation=generation)
                    existing = con.execute(
                        "SELECT generation FROM d17_scoped_auto_grants WHERE grant_id=?",
                        (grant["grant_id"],),
                    ).fetchone()
                    entity_generation = int(existing["generation"]) if existing else 0
                    if request["expected_generation"] != entity_generation:
                        reason = ("grant_generation_rollback"
                                  if request["expected_generation"] < entity_generation
                                  else "grant_generation_conflict")
                        return _base(reason, rc=1, mode=mode,
                                     generation=generation)
                    if existing:
                        return _base("grant_replay_conflict", rc=1,
                                     mode=mode, generation=generation)
                    if grant["max_effects"] != 1 or type(grant["max_effects"]) is not int:
                        return _base("grant_max_effects_invalid", rc=1,
                                     mode=mode, generation=generation)
                    if type(grant["not_before_ms"]) is not int \
                            or grant["not_before_ms"] > effective_now:
                        return _base("grant_not_before_invalid", rc=1,
                                     mode=mode, generation=generation)
                    if (type(grant["expires_at_ms"]) is not int
                            or grant["expires_at_ms"] <= effective_now
                            or grant["expires_at_ms"] - effective_now > DAY_MS):
                        return _base("grant_ttl_exceeded", rc=1,
                                     mode=mode, generation=generation)
                    _owner_result(
                        con, state_root, authority_path, request, effective_now,
                        approval_consumer, "d17.grant", subject, grant["grant_id"],
                        generation, 0, grant["plan_sha256"],
                        grant["target_fingerprint"], grant["scope"],
                    )
                    receipt = _receipt(operation, "grant_issued", grant["grant_id"],
                                       1, req_hash, effective_now)
                    record = dict(grant, generation=1,
                                  mode_generation=generation, status="active",
                                  effects_used=0,
                                  authority_grade="authenticated_owner_action",
                                  receipt_sha256=receipt["receipt_sha256"])
                    text, record_hash, seal = _record_parts(
                        state_root, "d17-grant-v1", record
                    )
                    try:
                        con.execute("""
                          INSERT INTO d17_scoped_auto_grants
                          (grant_id,subject_fingerprint,mode_generation,generation,status,
                           expires_at_ms,max_effects,effects_used,receipt_sha256,
                           record_json,record_sha256,local_seal)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (grant["grant_id"], _fp(subject), generation, 1,
                              "active", grant["expires_at_ms"], 1, 0,
                              receipt["receipt_sha256"], text, record_hash, seal))
                    except sqlite3.IntegrityError:
                        raise authority_store.AuthorityError("grant_generation_conflict")
                    fp = _fp(subject)
                    for pending_row in con.execute(
                        "SELECT pending_id FROM d17_pending_bind_plans "
                        "WHERE subject_fingerprint=? AND status='pending'", (fp,)
                    ).fetchall():
                        _transition_record(
                            con, state_root, "d17_pending_bind_plans",
                            pending_row["pending_id"], "superseded",
                        )
                    for confirmation_row in con.execute(
                        "SELECT confirmation_id FROM d17_bind_confirmations "
                        "WHERE subject_fingerprint=? AND status='active'", (fp,)
                    ).fetchall():
                        _transition_record(
                            con, state_root, "d17_bind_confirmations",
                            confirmation_row["confirmation_id"], "revoked",
                        )
                    _write_mode(con, state_root, subject, "scoped_auto", generation + 1)
                    result = _base("grant_issued", ok=True, rc=0,
                                   mode="scoped_auto", generation=generation + 1,
                                   receipt=receipt, grant=record)
                    _append_event(con, state_root, req_hash, operation,
                                  "grant_issued", grant["grant_id"], 1,
                                  result, effective_now)
                    return result

                if operation == "revoke":
                    subject = request["subject"]
                    if (not _safe_subject(subject)
                            or not _entity_id(request["grant_id"])
                            or not _plain_string(request["reason_code"])
                            or not _strict_int(
                                request["expected_mode_generation"]
                            )
                            or not _strict_int(request["expected_generation"])
                            or not _safe_human_receipt(
                                request.get("human_receipt")
                            )):
                        return _base("d17_request_invalid", rc=2)
                    mode, generation = _mode_row(con, state_root, subject)
                    if request["expected_mode_generation"] != generation:
                        return _base("mode_generation_conflict", rc=1,
                                     mode=mode, generation=generation)
                    row = con.execute(
                        "SELECT * FROM d17_scoped_auto_grants WHERE grant_id=?",
                        (request["grant_id"],),
                    ).fetchone()
                    if row is not None and row["subject_fingerprint"] != _fp(subject):
                        return _base("grant_subject_mismatch", rc=1,
                                     mode=mode, generation=generation)
                    if row is not None and row["status"] != "active":
                        return _base("grant_" + row["status"], rc=1,
                                     mode=mode, generation=generation)
                    current_entity_generation = int(row["generation"]) if row else 0
                    if request["expected_generation"] != current_entity_generation:
                        reason = ("grant_generation_rollback"
                                  if request["expected_generation"] < current_entity_generation
                                  else "grant_generation_conflict")
                        return _base(reason, rc=1,
                                     mode=mode, generation=generation)
                    _owner_result(
                        con, state_root, authority_path, request, effective_now,
                        approval_consumer, "d17.grant.revoke", subject,
                        request["grant_id"], generation,
                        current_entity_generation,
                    )
                    if row is None:
                        reason = "grant_revoke_absent"
                        new_mode, new_mode_generation = mode, generation
                        entity_generation = 1
                    else:
                        transitioned = _transition_record(
                            con, state_root, "d17_scoped_auto_grants",
                            request["grant_id"], "revoked",
                        )
                        reason = "grant_revoked"
                        new_mode, new_mode_generation = "remind_only", generation + 1
                        entity_generation = transitioned["generation"]
                        _write_mode(con, state_root, subject, new_mode,
                                    new_mode_generation)
                    receipt = _receipt(operation, reason, request["grant_id"],
                                       entity_generation, req_hash, effective_now)
                    result = _base(reason, ok=True, rc=0, mode=new_mode,
                                   generation=new_mode_generation, receipt=receipt)
                    _append_event(con, state_root, req_hash, operation, reason,
                                  request["grant_id"], entity_generation,
                                  result, effective_now)
                    return result

                if operation == "expire_due":
                    expired = 0
                    expired_grant_subjects = set()
                    for table, id_column, live_status in (
                        ("d17_pending_bind_plans", "pending_id", "pending"),
                        ("d17_bind_confirmations", "confirmation_id", "active"),
                        ("d17_scoped_auto_grants", "grant_id", "active"),
                    ):
                        rows = con.execute(
                            "SELECT %s,subject_fingerprint FROM %s "
                            "WHERE status=? AND expires_at_ms<=?"
                            % (id_column, table),
                            (live_status, effective_now),
                        ).fetchall()
                        for row in rows:
                            _transition_record(
                                con, state_root, table, row[id_column], "expired"
                            )
                            expired += 1
                            if table == "d17_scoped_auto_grants":
                                expired_grant_subjects.add(
                                    row["subject_fingerprint"]
                                )
                    for subject_fingerprint in expired_grant_subjects:
                        mode_row = con.execute(
                            "SELECT state_json,generation FROM d17_mode_state "
                            "WHERE subject_fingerprint=?",
                            (subject_fingerprint,),
                        ).fetchone()
                        if mode_row:
                            subject = json.loads(mode_row["state_json"])["subject"]
                            mode, current_generation = _mode_row(
                                con, state_root, subject
                            )
                            if mode == "scoped_auto":
                                _write_mode(
                                    con, state_root, subject, "remind_only",
                                    current_generation + 1,
                                )
                    reason = "authorizations_expired" if expired else (
                        "clock_floor_blocked" if rolled_back else "nothing_due"
                    )
                    return _base(reason, ok=True, rc=0)
        finally:
            con.close()
    except authority_store.AuthorityError as error:
        reason = error.reason
        if reason == "p1a_state_db_unavailable" \
                and not _paths_safe(state_root, authority_path=(
                    authority_path if operation in HUMAN_MUTATION_OPERATIONS
                    else None), missing_db_ok=False):
            reason = "d17_state_path_unsafe"
        if reason == "p1a_authority_proof_replay":
            reason = "owner_authority_replay"
        return _base(reason, rc=error.rc)
    except Exception:
        return _base("d17_internal_error", rc=2)


def consume_for_bind_in_transaction(state_root, con, authorization_ref,
                                    bind_context, now_ms, *, authority_path):
    """`authority_path`(必填,無預設)= P1A bootstrap authority;
    07:03 第 3 條:在 caller transaction 內用同一 prepared plan 重驗,
    缺它不得沿 legacy 形狀繼續。這也是未來 D apply_authorized 的接點。"""
    if authority_path is None:
        return _base("prepared_plan_authority_missing", rc=2)
    if not getattr(con, "in_transaction", False):
        return _base("caller_transaction_required", rc=2)
    if not _strict_int(now_ms):
        return _base("d17_request_invalid", rc=2)
    savepoint_active = False
    try:
        con.execute("SAVEPOINT d17_bind_authority_consume")
        savepoint_active = True
        if (not _paths_safe(state_root, missing_db_ok=False)
                or not _connection_matches_state(con, state_root)):
            return _base("d17_state_path_unsafe", rc=2)
        _validate_integrity(con, state_root)
        if not isinstance(authorization_ref, dict) or set(authorization_ref) != {
            "authorization_kind", "authorization_id", "authorization_generation",
            "authorization_receipt_sha256", "plan_sha256", "target_fingerprint",
            "max_effects",
        }:
            return _base("authorization_ref_invalid", rc=2)
        if (
            authorization_ref.get("authorization_kind")
            not in {"grant", "confirmation"}
            or not _entity_id(authorization_ref.get("authorization_id"))
            or not _strict_int(
                authorization_ref.get("authorization_generation"), minimum=1
            )
            or not isinstance(
                authorization_ref.get("authorization_receipt_sha256"), str
            )
            or not HEX64.fullmatch(
                authorization_ref["authorization_receipt_sha256"]
            )
            or not isinstance(authorization_ref.get("plan_sha256"), str)
            or not HEX64.fullmatch(authorization_ref["plan_sha256"])
            or not isinstance(
                authorization_ref.get("target_fingerprint"), str
            )
            or not HEX64.fullmatch(authorization_ref["target_fingerprint"])
            or type(authorization_ref.get("max_effects")) is not int
            or authorization_ref["max_effects"] != 1
        ):
            return _base("authorization_ref_invalid", rc=2)
        if not isinstance(bind_context, dict) or set(bind_context) != {
            "subject", "scope", "plan_sha256", "target_fingerprint",
        } or not _safe_subject(bind_context["subject"]) \
                or not _safe_scope(bind_context["scope"]) \
                or not isinstance(bind_context["plan_sha256"], str) \
                or not HEX64.fullmatch(bind_context["plan_sha256"]) \
                or not isinstance(bind_context["target_fingerprint"], str) \
                or not HEX64.fullmatch(bind_context["target_fingerprint"]):
            return _base("bind_context_invalid", rc=2)
        try:
            _require_prepared_match(
                con, state_root, authority_path,
                bind_context["plan_sha256"], bind_context["target_fingerprint"],
                now_ms)
        except authority_store.AuthorityError as err:
            return _base(err.reason, rc=1)
        effective_now, _rolled_back = _clock(con, state_root, now_ms)
        kind = authorization_ref["authorization_kind"]
        table, id_column = (
            ("d17_scoped_auto_grants", "grant_id") if kind == "grant"
            else ("d17_bind_confirmations", "confirmation_id")
            if kind == "confirmation" else (None, None)
        )
        if table is None:
            return _base("authorization_kind_invalid", rc=2)
        row = con.execute(
            "SELECT * FROM %s WHERE %s=?" % (table, id_column),
            (authorization_ref["authorization_id"],),
        ).fetchone()
        if row is None:
            return _base("authorization_unknown", rc=1)
        record = json.loads(row["record_json"])
        mode, generation = _mode_row(con, state_root, bind_context["subject"])
        if row["status"] != "active":
            return _base(kind + "_" + row["status"], rc=1,
                         mode=mode, generation=generation)
        if row["expires_at_ms"] <= effective_now:
            _transition_record(
                con, state_root, table,
                authorization_ref["authorization_id"], "expired",
            )
            if kind == "grant" and mode == "scoped_auto":
                generation += 1
                mode = "remind_only"
                _write_mode(
                    con, state_root, bind_context["subject"], mode, generation
                )
            return _base(kind + "_expired", rc=1,
                         mode=mode, generation=generation)
        exact = (
            authorization_ref["authorization_generation"] == row["generation"]
            and authorization_ref["authorization_receipt_sha256"] == row["receipt_sha256"]
            and authorization_ref["plan_sha256"] == record["plan_sha256"]
            and authorization_ref["target_fingerprint"] == record["target_fingerprint"]
            and authorization_ref["max_effects"] == 1
            and bind_context["subject"] == record["subject"]
            and bind_context["scope"] == record["scope"]
            and bind_context["plan_sha256"] == record["plan_sha256"]
            and bind_context["target_fingerprint"] == record["target_fingerprint"]
        )
        if not exact:
            return _base("authorization_scope_mismatch", rc=1,
                         mode=mode, generation=generation)
        if row["effects_used"] != 0:
            return _base(kind + "_consumed", rc=1,
                         mode=mode, generation=generation)
        _transition_record(
            con, state_root, table, authorization_ref["authorization_id"],
            "consumed", effects_used=1,
        )
        if kind == "grant" and mode == "scoped_auto":
            generation += 1
            mode = "remind_only"
            _write_mode(con, state_root, bind_context["subject"], mode, generation)
        return _base("authorization_consumed", ok=True, rc=0,
                     mode=mode, generation=generation)
    except authority_store.AuthorityError as error:
        if savepoint_active:
            savepoint_active = False
            _rollback_consume_savepoint(con)
        return _base(error.reason, rc=error.rc)
    except Exception:
        if savepoint_active:
            savepoint_active = False
            _rollback_consume_savepoint(con)
        return _base("d17_internal_error", rc=2)
    finally:
        if savepoint_active:
            con.execute("RELEASE SAVEPOINT d17_bind_authority_consume")
