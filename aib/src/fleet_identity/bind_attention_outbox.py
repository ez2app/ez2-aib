# SPDX-License-Identifier: Apache-2.0
"""D17-C1 durable reminder outbox.

契約:`tests/test-d17-c1-outbox-v2-second-party.py`(qa/d17c1-v2-arms-0902 = 7fad2c8d)。
C0 收據 schema 從 C0 產品 import,不得在本檔再寫一份字面;本檔 REQUEST /
RESULT / RECEIPT 與 C0 收據同代(v2)。v1 C0 收據具名拒絕。

本模組只管**投遞狀態**:logical reminder / delivery attempt / acknowledgement /
notification preference / clock floor / outbox receipt。它**沒有**綁定效果
(`effect_count` 永遠 0),不碰 transport、tmux、mouse、bind effect。

三把鍵互異(01c):
  source_key   = H(identity[SOURCE_SCOPE] + source)          一個上游事件一把
  logical_key  = H(identity[LOGICAL_SCOPE] + mode_generation + evaluator)  一個提醒一把(通道不入鍵)
  delivery_key = H(reminder_id + attempt_generation)            一次投遞嘗試一把

read_reminder 是**投影式**(只複製具名欄位),新欄位預設不可見——站在安全側。
所有失效都是封閉理由,不外露 traceback。
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import sqlite3
import stat

try:
    from .bind_attention_adapters import (
        MODES,
        RECEIPT_SCHEMA as C0_RECEIPT_SCHEMA,
    )
except ImportError:  # loaded as a loose file, not a package member
    from bind_attention_adapters import (
        MODES,
        RECEIPT_SCHEMA as C0_RECEIPT_SCHEMA,
    )

REQUEST_SCHEMA = "ez2-aib/d17-reminder-outbox-request/v2"
RESULT_SCHEMA = "ez2-aib/d17-reminder-outbox-result/v2"
RECEIPT_SCHEMA = "ez2-aib/d17-reminder-outbox-receipt/v2"
BN_RECEIPT_SCHEMA = "ez2-aib/d17-notification-authority-receipt/v1"

DB_DIR = "d17"
DB_NAME = "reminder-outbox.sqlite3"
LOCK_NAME = DB_NAME + ".lock"

STATES = frozenset({
    "ready", "leased", "transport_delivered", "recipient_acknowledged",
    "retryable_zero_delivery", "delivery_unknown", "suppressed", "snoozed",
    "expired", "cancelled",
})
CLAIMABLE_STATES = frozenset({"ready", "retryable_zero_delivery", "leased", "snoozed"})
CHANNELS = frozenset({"aib_dm", "ui"})
SOURCE_KINDS = frozenset({"ctx_observation", "work_item", "content", "aib_dm_delivery",
                          "user_prompt_submit"})
POLICIES = frozenset({"muted", "normal"})
BN_OPERATIONS = frozenset({"set_notification_policy", "snooze_notifications"})

IDENTITY_STRING_KEYS = frozenset({
    "cabin_id", "fleet_id", "fleet_member_id", "lease_id", "project_id",
    "principal_id", "seat_id", "session_uuid", "seat_record_sha256", "socket",
    "window_id", "target_fingerprint", "plan_sha256",
})
IDENTITY_INT_KEYS = frozenset({"fleet_generation", "lease_generation",
                               "project_generation", "pid"})
IDENTITY_KEYS = IDENTITY_STRING_KEYS | IDENTITY_INT_KEYS
IDENTITY_HEX_KEYS = frozenset({"seat_record_sha256", "target_fingerprint", "plan_sha256"})
SOURCE_SCOPE_KEYS = (
    "cabin_id", "fleet_id", "fleet_generation", "fleet_member_id",
    "lease_id", "lease_generation", "project_id", "project_generation",
    "seat_id", "session_uuid",
)
LOGICAL_SCOPE_KEYS = (
    "cabin_id", "fleet_id", "fleet_generation", "fleet_member_id",
    "lease_generation", "project_id", "project_generation", "seat_id",
    "session_uuid", "target_fingerprint",
)
# route 與 identity 重疊、必須逐字相等的欄(02f 五種漂移 + 其餘身分欄)
ROUTE_IDENTITY_KEYS = (
    "cabin_id", "fleet_id", "fleet_generation", "fleet_member_id", "lease_id",
    "lease_generation", "project_id", "project_generation", "seat_id",
    "session_uuid", "seat_record_sha256", "socket", "window_id", "pid",
)
ROUTE_KEYS = frozenset(ROUTE_IDENTITY_KEYS) | {"route_generation", "route_attestation_sha256"}
# BN authority scope 的封閉鍵集合(05f 逐鍵缺一即拒)
AUTHORITY_SCOPE_KEYS = frozenset({
    "cabin_id", "fleet_id", "fleet_generation", "fleet_member_id", "lease_id",
    "lease_generation", "project_id", "project_generation", "principal_id",
    "seat_id", "session_uuid", "target_fingerprint", "plan_sha256",
    "persona_uid", "root_sha256", "grant_id", "grant_generation",
    "confirmation_id", "confirmation_generation", "notification_policy_id",
})
# preference 的 scope 指紋只取與 identity 重疊的欄:persona 不入鍵、不落地(05f)
PREFERENCE_SCOPE_KEYS = (
    "cabin_id", "fleet_id", "fleet_generation", "fleet_member_id", "lease_id",
    "lease_generation", "project_id", "project_generation", "principal_id",
    "seat_id", "session_uuid", "target_fingerprint", "plan_sha256",
)
BN_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "operation", "scope", "notification_generation",
    "policy", "snooze_until_ms", "effect_count", "receipt_sha256",
})
C0_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "operation", "reason", "identity_sha256",
    "source_event_id_sha256", "evaluator_receipt_id", "evaluator_receipt_sha256",
    "mode", "mode_generation", "target_fingerprint",
    "reminder_count", "effect_count",
})
FORBIDDEN_REQUEST_KEYS = frozenset({"raw_content", "rendered_body", "brain", "persona",
                                    "content", "body", "title", "prompt"})
REQUEST_KEYS = {
    "reserve_reminder": {"schema", "operation", "identity", "source", "c0_receipt",
                         "mode_generation", "channel_candidates"},
    "claim_delivery": {"schema", "operation", "reminder_id", "selected_channel",
                       "lease_token", "lease_generation", "lease_until_ms"},
    "finish_delivery": {"schema", "operation", "reminder_id", "lease_token",
                        "lease_generation", "route_generation", "transport"},
    "acknowledge_delivery": {"schema", "operation", "reminder_id", "cabin_id",
                             "session_uuid", "route_generation"},
    "read_reminder": {"schema", "operation", "reminder_id"},
    "apply_notification_policy_receipt": {"schema", "operation", "identity",
                                          "authority_receipt"},
}
REMINDER_DATA_KEYS = (
    "reminder_id", "source_key_sha256", "logical_key_sha256", "state",
    "selected_channel", "attempt_generation", "lease_generation",
    "lease_until_ms", "route_generation", "mode_generation",
    "notification_generation", "created_ms", "updated_ms",
)

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class _Refused(Exception):
    def __init__(self, reason, rc=1, state=None):
        super().__init__(reason)
        self.reason = reason
        self.rc = rc
        self.state = state


# ── 純函式工具 ─────────────────────────────────────────────────────────
def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _exact_int(value, minimum=None) -> bool:
    return (type(value) is int
            and (minimum is None or value >= minimum))


def _hex64(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _a_receipt_id(value) -> bool:
    return (isinstance(value, str) and value.startswith("d17-")
            and len(value) == 36
            and all(c in "0123456789abcdef" for c in value[4:]))


def _plain(value) -> bool:
    return isinstance(value, str) and bool(value) and "\x00" not in value


def _result(operation, reason, rc, *, reminder_id=None, state=None,
            data=None, receipt=None):
    return {
        "schema": RESULT_SCHEMA, "ok": rc == 0, "rc": rc, "reason": reason,
        "operation": operation, "reminder_id": reminder_id, "state": state,
        "effect_count": 0, "data": data, "receipt": receipt,
    }


# ── 請求形狀 ─────────────────────────────────────────────────────────────
def _check_request(request, operation):
    if not isinstance(request, dict):
        raise _Refused("request_invalid", 2)
    if request.get("schema") != REQUEST_SCHEMA or request.get("operation") != operation:
        raise _Refused("request_invalid", 2)
    if set(request) & FORBIDDEN_REQUEST_KEYS:
        raise _Refused("request_invalid", 2)
    if set(request) != REQUEST_KEYS[operation]:
        raise _Refused("request_invalid", 2)


def _check_identity(identity):
    if not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS:
        raise _Refused("request_invalid", 2)
    for key in IDENTITY_STRING_KEYS:
        if not _plain(identity[key]):
            raise _Refused("request_invalid", 2)
    for key in IDENTITY_INT_KEYS:
        if not _exact_int(identity[key], 0):
            raise _Refused("request_invalid", 2)
    for key in IDENTITY_HEX_KEYS:
        if not _hex64(identity[key]):
            raise _Refused("request_invalid", 2)


def _check_c0_receipt(receipt, identity, source, mode_generation):
    if not isinstance(receipt, dict) or set(receipt) != C0_RECEIPT_KEYS:
        raise _Refused("request_invalid", 2)
    if receipt["schema"] != C0_RECEIPT_SCHEMA:
        raise _Refused("request_invalid", 2)
    if receipt["operation"] != "evaluate_ctx_source":
        raise _Refused("request_invalid", 2)
    if not _hex64(receipt["receipt_id"]) \
            or not _hex64(receipt["identity_sha256"]) \
            or not _hex64(receipt["source_event_id_sha256"]) \
            or not _hex64(receipt["evaluator_receipt_sha256"]) \
            or not _a_receipt_id(receipt["evaluator_receipt_id"]):
        raise _Refused("request_invalid", 2)
    # C0 hashes the resolver-private context, not C1's public identity.
    if receipt["source_event_id_sha256"] != source["source_event_id_sha256"]:
        raise _Refused("request_invalid", 2)
    if receipt.get("target_fingerprint") != identity.get("target_fingerprint") \
            or not _hex64(receipt.get("target_fingerprint")):
        raise _Refused("request_invalid", 2)
    if receipt.get("mode") not in MODES:
        raise _Refused("request_invalid", 2)
    if receipt.get("mode_generation") != mode_generation \
            or not _exact_int(receipt.get("mode_generation"), 0):
        raise _Refused("request_invalid", 2)
    count = receipt["reminder_count"]
    if not _exact_int(count, 0) or count > 1:
        raise _Refused("request_invalid", 2)
    if not _exact_int(receipt["effect_count"]) or receipt["effect_count"] != 0:
        raise _Refused("request_invalid", 2)
    if not _plain(receipt["reason"]):
        raise _Refused("request_invalid", 2)
    # reason 與 count 要同向:count=1 只能是 reminder_emitted;count=0 不得自稱 emitted
    if (count == 1) != (receipt["reason"] == "reminder_emitted"):
        raise _Refused("request_invalid", 2)
    return count


# ── 檔案系統邊界 ─────────────────────────────────────────────────────────
def _uid():
    return os.geteuid() if hasattr(os, "geteuid") else None


def _safe_dir(path, *, must_exist):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if must_exist:
            raise _Refused("filesystem_refused", 2)
        return False
    except OSError:
        raise _Refused("filesystem_refused", 2)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise _Refused("filesystem_refused", 2)
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise _Refused("filesystem_refused", 2)
    uid = _uid()
    if uid is not None and info.st_uid != uid:
        raise _Refused("filesystem_refused", 2)
    return True


def _safe_file(path, *, missing_ok):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise _Refused("filesystem_refused", 2)
    except OSError:
        raise _Refused("filesystem_refused", 2)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise _Refused("filesystem_refused", 2)
    if info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
        raise _Refused("filesystem_refused", 2)
    uid = _uid()
    if uid is not None and info.st_uid != uid:
        raise _Refused("filesystem_refused", 2)
    return (info.st_dev, info.st_ino)


def _db_paths(state_root):
    root = os.fspath(state_root)
    if not isinstance(root, str) or not os.path.isabs(root):
        raise _Refused("filesystem_refused", 2)
    root = os.path.normpath(root)
    parent = os.path.join(root, DB_DIR)
    db = os.path.join(parent, DB_NAME)
    return root, parent, db, os.path.join(parent, LOCK_NAME)


def _check_tree(state_root, *, create):
    root, parent, db, lock = _db_paths(state_root)
    _safe_dir(root, must_exist=True)
    if not _safe_dir(parent, must_exist=False):
        if not create:
            raise _Refused("filesystem_refused", 2)
        try:
            os.mkdir(parent, 0o700)
        except FileExistsError:
            # ★ 兩個 writer 同時發現目錄不在、同時 mkdir:第二個吃到 EEXIST。
            #   這是本樹 bind_attention 記過的同一個競賽(ENOENT/EEXIST 只接受 EEXIST),
            #   我在這裡又寫了一次——把它收成 filesystem_refused 讓 01g 2/10 紅。
            #   EEXIST 視為「別人建好了」,下面重驗它是不是安全的目錄。
            pass
        except OSError:
            raise _Refused("filesystem_refused", 2)
        _safe_dir(parent, must_exist=True)
    # ★ db 各副檔的檢查**移到 flock 之後**(見 _writer):另一個 writer 交易中的
    #   -journal 由 sqlite 以預設 umask 建(0644),在鎖前檢查會把後到者判成
    #   filesystem_refused —— 這是 01g 修掉 mkdir 競賽後仍 1/30 紅的第二個根。
    _safe_file(lock, missing_ok=True)
    return root, parent, db, lock


def _check_db_files(db):
    for suffix in ("", "-wal", "-shm"):
        _safe_file(db + suffix, missing_ok=True)


def _db_identity(db):
    return _safe_file(db, missing_ok=True)


@contextlib.contextmanager
def _writer(state_root, now_ms):
    """flock + BEGIN IMMEDIATE。yield (con, effective_now)。"""
    if not _exact_int(now_ms):
        raise _Refused("request_invalid", 2)
    root, parent, db, lock = _check_tree(state_root, create=True)
    lock_fd = None
    con = None
    try:
        try:
            lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC, 0o600)
        except OSError:
            raise _Refused("filesystem_refused", 2)
        _safe_file(lock, missing_ok=False)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _check_db_files(db)
        existed = _db_identity(db) is not None
        try:
            con = sqlite3.connect(db, isolation_level=None, timeout=30)
        except sqlite3.Error:
            raise _Refused("filesystem_refused", 2)
        if not existed:
            try:
                os.chmod(db, 0o600)
            except OSError:
                raise _Refused("filesystem_refused", 2)
        _safe_file(db, missing_ok=False)
        con.row_factory = sqlite3.Row
        try:
            _tables(con)
            con.execute("BEGIN IMMEDIATE")
            floor = _floor(con)
            effective_now = max(now_ms, floor)
            if effective_now != floor:
                con.execute(
                    "INSERT INTO d17_clock_floor(id, floor_ms) VALUES(1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET floor_ms=excluded.floor_ms",
                    (effective_now,))
            yield con, effective_now
            con.execute("COMMIT")
        except _Refused:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if con is not None and con.in_transaction:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise _Refused("filesystem_refused", 2)
    finally:
        if con is not None:
            con.close()
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)


@contextlib.contextmanager
def _reader(state_root):
    """唯讀:不建目錄、不建檔、不動 floor、不寫收據。"""
    root, parent, db, lock = _db_paths(state_root)
    _safe_dir(root, must_exist=True)
    _safe_dir(parent, must_exist=True)
    for suffix in ("", "-wal", "-shm"):
        _safe_file(db + suffix, missing_ok=(suffix != ""))
    _safe_file(lock, missing_ok=True)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, isolation_level=None)
    except sqlite3.Error:
        raise _Refused("filesystem_refused", 2)
    con.row_factory = sqlite3.Row
    try:
        try:
            con.execute("SELECT 1 FROM d17_logical_reminders LIMIT 0")
        except sqlite3.Error:
            raise _Refused("filesystem_refused", 2)
        yield con
    finally:
        con.close()


def _tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS d17_logical_reminders(
      reminder_id TEXT PRIMARY KEY,
      logical_key_sha256 TEXT NOT NULL UNIQUE,
      source_key_sha256 TEXT NOT NULL,
      identity_json TEXT NOT NULL,
      evaluator_receipt_id TEXT NOT NULL,
      state TEXT NOT NULL,
      selected_channel TEXT,
      attempt_generation INTEGER NOT NULL DEFAULT 0,
      lease_token_sha256 TEXT,
      lease_generation INTEGER NOT NULL DEFAULT 0,
      lease_until_ms INTEGER,
      route_generation INTEGER,
      mode_generation INTEGER NOT NULL,
      notification_generation INTEGER NOT NULL DEFAULT 0,
      snooze_until_ms INTEGER,
      created_ms INTEGER NOT NULL,
      updated_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_delivery_attempts(
      delivery_key_sha256 TEXT PRIMARY KEY,
      reminder_id TEXT NOT NULL,
      attempt_generation INTEGER NOT NULL,
      selected_channel TEXT NOT NULL,
      lease_token_sha256 TEXT NOT NULL,
      lease_generation INTEGER NOT NULL,
      lease_until_ms INTEGER NOT NULL,
      route_generation INTEGER NOT NULL,
      terminal_rc INTEGER,
      terminal_state TEXT,
      created_ms INTEGER NOT NULL,
      updated_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_reminder_acknowledgements(
      reminder_id TEXT PRIMARY KEY,
      route_generation INTEGER NOT NULL,
      acknowledged_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_notification_preferences(
      scope_sha256 TEXT PRIMARY KEY,
      notification_generation INTEGER NOT NULL,
      policy TEXT NOT NULL,
      snooze_until_ms INTEGER,
      updated_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_clock_floor(
      id INTEGER PRIMARY KEY CHECK(id = 1),
      floor_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS d17_outbox_receipts(
      receipt_id TEXT PRIMARY KEY,
      receipt_sha256 TEXT NOT NULL,
      operation TEXT NOT NULL,
      reason TEXT NOT NULL,
      reminder_id TEXT,
      state TEXT,
      effect_count INTEGER NOT NULL,
      created_ms INTEGER NOT NULL
    );
    """)


def _floor(con):
    row = con.execute("SELECT floor_ms FROM d17_clock_floor WHERE id=1").fetchone()
    return int(row["floor_ms"]) if row else 0


def _receipt(con, operation, reason, reminder_id, state, now_ms):
    body = {
        "schema": RECEIPT_SCHEMA, "operation": operation, "reason": reason,
        "reminder_id": reminder_id, "state": state, "effect_count": 0,
    }
    body["receipt_id"] = _digest({"outbox_receipt": body, "now_ms": now_ms,
                                  "seq": con.execute(
                                      "SELECT count(*) FROM d17_outbox_receipts"
                                  ).fetchone()[0]})
    body["receipt_sha256"] = _digest(body)
    con.execute(
        "INSERT INTO d17_outbox_receipts(receipt_id, receipt_sha256, operation, reason, "
        "reminder_id, state, effect_count, created_ms) VALUES(?,?,?,?,?,?,0,?)",
        (body["receipt_id"], body["receipt_sha256"], operation, reason,
         reminder_id, state, now_ms))
    return body


def _row(con, reminder_id):
    if not _hex64(reminder_id):
        raise _Refused("request_invalid", 2)
    row = con.execute("SELECT * FROM d17_logical_reminders WHERE reminder_id=?",
                      (reminder_id,)).fetchone()
    if row is None:
        raise _Refused("reminder_not_found", 1)
    return row


def _data(row):
    return {
        "reminder_id": row["reminder_id"],
        "source_key_sha256": row["source_key_sha256"],
        "logical_key_sha256": row["logical_key_sha256"],
        "state": row["state"],
        "selected_channel": row["selected_channel"],
        "attempt_generation": int(row["attempt_generation"]),
        "lease_generation": int(row["lease_generation"]),
        "lease_until_ms": (None if row["lease_until_ms"] is None
                           else int(row["lease_until_ms"])),
        "route_generation": (None if row["route_generation"] is None
                             else int(row["route_generation"])),
        "mode_generation": int(row["mode_generation"]),
        "notification_generation": int(row["notification_generation"]),
        "created_ms": int(row["created_ms"]),
        "updated_ms": int(row["updated_ms"]),
    }


def _preference_scope_sha(identity_like):
    return _digest({key: identity_like[key] for key in PREFERENCE_SCOPE_KEYS})


def _preference(con, identity):
    return con.execute(
        "SELECT * FROM d17_notification_preferences WHERE scope_sha256=?",
        (_preference_scope_sha(identity),)).fetchone()


def _run(operation, request, state_root, now_ms, body):
    """統一出口:封閉理由、零 traceback、失敗時帶當前 state。"""
    try:
        _check_request(request, operation)
        return body()
    except _Refused as refused:
        return _result(operation, refused.reason, refused.rc, state=refused.state,
                       reminder_id=(request.get("reminder_id")
                                    if isinstance(request, dict)
                                    and _hex64(request.get("reminder_id")) else None))
    except Exception:  # noqa: BLE001 - 任何未預期例外都收斂成封閉理由
        return _result(operation, "filesystem_refused", 2)


def _check_route(route, identity):
    if not isinstance(route, dict) or set(route) != ROUTE_KEYS:
        raise _Refused("route_stale", 2)
    if not _exact_int(route["route_generation"], 0) \
            or not _hex64(route["route_attestation_sha256"]):
        raise _Refused("route_stale", 2)
    for key in ROUTE_IDENTITY_KEYS:
        if route[key] != identity[key]:
            raise _Refused("route_stale", 2)
    return int(route["route_generation"])


def _resolve_route(resolver, identity, stage, db, identity_before):
    if not callable(resolver):
        raise _Refused("request_invalid", 2)
    route = resolver(dict(identity), stage)
    # 06f:callback 期間資料庫被換掉 → 拒(用 dev/ino 對)
    if _db_identity(db) != identity_before:
        raise _Refused("filesystem_refused", 2)
    return _check_route(route, identity)


# ── 六個公開操作 ─────────────────────────────────────────────────────────
def reserve_reminder(request, *, state_root, now_ms):
    operation = "reserve_reminder"

    def body():
        identity = request.get("identity")
        _check_identity(identity)
        source = request.get("source")
        if (not isinstance(source, dict) or set(source) != {"source_kind", "source_event_id_sha256"}
                or not _plain(source["source_kind"])
                or not _hex64(source["source_event_id_sha256"])):
            raise _Refused("request_invalid", 2)
        count = _check_c0_receipt(
            request.get("c0_receipt"), identity, source,
            request.get("mode_generation"))
        if not _exact_int(request.get("mode_generation"), 0):
            raise _Refused("request_invalid", 2)
        channels = request.get("channel_candidates")
        if (not isinstance(channels, list) or not channels
                or any(not isinstance(c, str) or c not in CHANNELS for c in channels)
                or len(set(channels)) != len(channels)):
            raise _Refused("request_invalid", 2)
        c0 = request["c0_receipt"]
        source_key = _digest({**{k: identity[k] for k in SOURCE_SCOPE_KEYS}, **source})
        logical_key = _digest({
            "scope": {k: identity[k] for k in LOGICAL_SCOPE_KEYS},
            "mode_generation": request["mode_generation"],
            "evaluator_receipt_id": c0["evaluator_receipt_id"],
        })
        reminder_id = _digest({"reminder": logical_key})
        with _writer(state_root, now_ms) as (con, now):
            if count == 0:
                receipt = _receipt(con, operation, "reminder_not_requested", None, None, now)
                return _result(operation, "reminder_not_requested", 0, receipt=receipt)
            row = con.execute("SELECT * FROM d17_logical_reminders WHERE logical_key_sha256=?",
                              (logical_key,)).fetchone()
            if row is not None:
                receipt = _receipt(con, operation, "reminder_reused",
                                   row["reminder_id"], row["state"], now)
                return _result(operation, "reminder_reused", 0,
                               reminder_id=row["reminder_id"], state=row["state"],
                               data=_data(row), receipt=receipt)
            pref = _preference(con, identity)
            notification_generation = int(pref["notification_generation"]) if pref else 0
            con.execute(
                "INSERT INTO d17_logical_reminders(reminder_id, logical_key_sha256, "
                "source_key_sha256, identity_json, evaluator_receipt_id, state, "
                "mode_generation, notification_generation, created_ms, updated_ms) "
                "VALUES(?,?,?,?,?,'reserved',?,?,?,?)",
                (reminder_id, logical_key, source_key,
                 _canonical(identity).decode("utf-8"), c0["evaluator_receipt_id"],
                 request["mode_generation"], notification_generation, now, now))
            if pref is not None and pref["policy"] == "muted":
                con.execute("UPDATE d17_logical_reminders SET state=? WHERE reminder_id=?",
                            ("suppressed", reminder_id))
            elif pref is not None and pref["snooze_until_ms"] is not None \
                    and int(pref["snooze_until_ms"]) > now:
                con.execute("UPDATE d17_logical_reminders SET state=?, snooze_until_ms=? "
                            "WHERE reminder_id=?",
                            ("snoozed", int(pref["snooze_until_ms"]), reminder_id))
            else:
                con.execute("UPDATE d17_logical_reminders SET state='ready' "
                            "WHERE reminder_id=?", (reminder_id,))
            row = _row(con, reminder_id)
            receipt = _receipt(con, operation, "reminder_reserved", reminder_id, row["state"], now)
            return _result(operation, "reminder_reserved", 0, reminder_id=reminder_id,
                           state=row["state"], data=_data(row), receipt=receipt)

    return _run(operation, request, state_root, now_ms, body)


def claim_delivery(request, *, state_root, now_ms, route_resolver):
    operation = "claim_delivery"

    def body():
        reminder_id = request.get("reminder_id")
        channel = request.get("selected_channel")
        token = request.get("lease_token")
        generation = request.get("lease_generation")
        until = request.get("lease_until_ms")
        if (not _hex64(reminder_id) or channel not in CHANNELS or not _plain(token)
                or not _exact_int(generation, 1) or not _exact_int(until)):
            raise _Refused("request_invalid", 2)
        with _writer(state_root, now_ms) as (con, now):
            root, parent, db, lock = _db_paths(state_root)
            row = _row(con, reminder_id)
            state = row["state"]
            if state == "leased":
                if row["lease_until_ms"] is not None and int(row["lease_until_ms"]) > now:
                    raise _Refused("lease_active", 1, state)
            elif state == "snoozed":
                if row["snooze_until_ms"] is not None and int(row["snooze_until_ms"]) > now:
                    raise _Refused("state_invalid", 1, state)
            elif state not in ("ready", "retryable_zero_delivery"):
                raise _Refused("state_invalid", 1, state)
            if until <= now or generation <= int(row["lease_generation"]):
                raise _Refused("lease_invalid", 1, state)
            identity = json.loads(row["identity_json"])
            route_generation = _resolve_route(
                route_resolver, identity, "before_claim", db, _db_identity(db))
            attempt = int(row["attempt_generation"]) + 1
            delivery_key = _digest({"delivery": reminder_id, "attempt": attempt})
            con.execute(
                "INSERT INTO d17_delivery_attempts(delivery_key_sha256, reminder_id, "
                "attempt_generation, selected_channel, lease_token_sha256, lease_generation, "
                "lease_until_ms, route_generation, created_ms, updated_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (delivery_key, reminder_id, attempt, channel, _digest({"token": token}),
                 generation, until, route_generation, now, now))
            con.execute(
                "UPDATE d17_logical_reminders SET state=?, selected_channel=?, "
                "attempt_generation=?, lease_token_sha256=?, lease_generation=?, "
                "lease_until_ms=?, route_generation=?, updated_ms=? WHERE reminder_id=?",
                ("leased", channel, attempt, _digest({"token": token}), generation, until,
                 route_generation, now, reminder_id))
            reason = "delivery_claimed" if attempt == 1 else "delivery_retry_claimed"
            row = _row(con, reminder_id)
            receipt = _receipt(con, operation, reason, reminder_id, "leased", now)
            return _result(operation, reason, 0, reminder_id=reminder_id, state="leased",
                           data=_data(row), receipt=receipt)

    return _run(operation, request, state_root, now_ms, body)


def finish_delivery(request, *, state_root, now_ms, route_resolver):
    operation = "finish_delivery"

    def body():
        reminder_id = request.get("reminder_id")
        token = request.get("lease_token")
        generation = request.get("lease_generation")
        route_generation = request.get("route_generation")
        transport = request.get("transport")
        if (not _hex64(reminder_id) or not _plain(token) or not _exact_int(generation, 1)
                or not _exact_int(route_generation, 0) or not isinstance(transport, dict)
                or set(transport) != {"rc", "receipt"} or not _exact_int(transport["rc"])):
            raise _Refused("request_invalid", 2)
        with _writer(state_root, now_ms) as (con, now):
            root, parent, db, lock = _db_paths(state_root)
            row = _row(con, reminder_id)
            state = row["state"]
            if state != "leased":
                raise _Refused("state_invalid", 1, state)
            if (row["lease_token_sha256"] != _digest({"token": token})
                    or int(row["lease_generation"]) != generation):
                raise _Refused("lease_invalid", 1, state)
            if row["route_generation"] is None or int(row["route_generation"]) != route_generation:
                raise _Refused("route_stale", 2, state)
            identity = json.loads(row["identity_json"])
            try:
                _resolve_route(route_resolver, identity, "before_transport", db,
                               _db_identity(db))
            except _Refused as refused:
                if refused.reason == "route_stale":
                    # 漂移 = 零送出:退回 ready,attempt 不封口
                    con.execute("UPDATE d17_logical_reminders SET state='ready', "
                                "lease_token_sha256=NULL, lease_until_ms=NULL, updated_ms=? "
                                "WHERE reminder_id=?", (now, reminder_id))
                    receipt = _receipt(con, operation, "route_stale", reminder_id, "ready", now)
                    return _result(operation, "route_stale", 2, reminder_id=reminder_id,
                                   state="ready", data=_data(_row(con, reminder_id)),
                                   receipt=receipt)
                raise
            rc = transport["rc"]
            receipt_doc = transport["receipt"]
            new_state = "delivery_unknown"
            reason = "delivery_unknown"
            if isinstance(receipt_doc, dict):
                status = receipt_doc.get("status")
                appended = receipt_doc.get("delivery_appended")
                if rc == 0 and status == "delivered" and appended is True:
                    new_state, reason = "transport_delivered", "transport_delivered"
                elif rc == 2 and status == "zero_delivery" and appended is False:
                    new_state, reason = "retryable_zero_delivery", "transport_zero_delivery"
            attempt = int(row["attempt_generation"])
            con.execute(
                "UPDATE d17_delivery_attempts SET terminal_rc=?, terminal_state=?, updated_ms=? "
                "WHERE reminder_id=? AND attempt_generation=?",
                (rc, new_state, now, reminder_id, attempt))
            con.execute(
                "UPDATE d17_logical_reminders SET state=?, lease_token_sha256=NULL, "
                "lease_until_ms=NULL, updated_ms=? WHERE reminder_id=?",
                (new_state, now, reminder_id))
            row = _row(con, reminder_id)
            receipt = _receipt(con, operation, reason, reminder_id, new_state, now)
            return _result(operation, reason, 0, reminder_id=reminder_id, state=new_state,
                           data=_data(row), receipt=receipt)

    return _run(operation, request, state_root, now_ms, body)


def acknowledge_delivery(request, *, state_root, now_ms):
    operation = "acknowledge_delivery"

    def body():
        reminder_id = request.get("reminder_id")
        if not _hex64(reminder_id) or not _plain(request.get("cabin_id")) \
                or not _plain(request.get("session_uuid")) \
                or not _exact_int(request.get("route_generation"), 0):
            raise _Refused("request_invalid", 2)
        with _writer(state_root, now_ms) as (con, now):
            try:
                row = _row(con, reminder_id)
            except _Refused:
                raise _Refused("acknowledgement_invalid", 1)
            identity = json.loads(row["identity_json"])
            if (row["state"] != "transport_delivered"
                    or request["cabin_id"] != identity["cabin_id"]
                    or request["session_uuid"] != identity["session_uuid"]
                    or row["route_generation"] is None
                    or int(row["route_generation"]) != request["route_generation"]):
                raise _Refused("acknowledgement_invalid", 1, row["state"])
            con.execute(
                "INSERT INTO d17_reminder_acknowledgements(reminder_id, route_generation, "
                "acknowledged_ms) VALUES(?,?,?)",
                (reminder_id, request["route_generation"], now))
            con.execute("UPDATE d17_logical_reminders SET state=?, updated_ms=? "
                        "WHERE reminder_id=?", ("recipient_acknowledged", now, reminder_id))
            row = _row(con, reminder_id)
            receipt = _receipt(con, operation, "recipient_acknowledged", reminder_id,
                               "recipient_acknowledged", now)
            return _result(operation, "recipient_acknowledged", 0, reminder_id=reminder_id,
                           state="recipient_acknowledged", data=_data(row), receipt=receipt)

    return _run(operation, request, state_root, now_ms, body)


def read_reminder(request, *, state_root, now_ms):
    operation = "read_reminder"

    def body():
        reminder_id = request.get("reminder_id")
        if not _hex64(reminder_id):
            raise _Refused("request_invalid", 2)
        if not _exact_int(now_ms):
            raise _Refused("request_invalid", 2)
        with _reader(state_root) as con:
            row = _row(con, reminder_id)
            # 投影式:只複製具名欄位;新欄位預設不可見
            return _result(operation, "reminder_read", 0, reminder_id=reminder_id,
                           state=row["state"], data=_data(row), receipt=None)

    return _run(operation, request, state_root, now_ms, body)


def apply_notification_policy_receipt(request, *, state_root, now_ms,
                                      notification_receipt_verifier):
    operation = "apply_notification_policy_receipt"

    def body():
        identity = request.get("identity")
        _check_identity(identity)
        receipt = request.get("authority_receipt")
        if not callable(notification_receipt_verifier):
            raise _Refused("request_invalid", 2)
        # verifier 恰呼叫一次,而且在任何檔案系統存取之前
        verified = notification_receipt_verifier(receipt)
        if not isinstance(verified, dict) or set(verified) != BN_RECEIPT_KEYS:
            raise _Refused("notification_receipt_invalid", 2)
        if verified["schema"] != BN_RECEIPT_SCHEMA or not _hex64(verified["receipt_id"]) \
                or verified["operation"] not in BN_OPERATIONS \
                or not _exact_int(verified["notification_generation"], 0) \
                or verified["policy"] not in POLICIES \
                or not (verified["snooze_until_ms"] is None
                        or _exact_int(verified["snooze_until_ms"])) \
                or not _exact_int(verified["effect_count"]) or verified["effect_count"] != 0:
            raise _Refused("notification_receipt_invalid", 2)
        scope = verified["scope"]
        if not isinstance(scope, dict) or set(scope) != AUTHORITY_SCOPE_KEYS:
            raise _Refused("notification_receipt_invalid", 2)
        for key in PREFERENCE_SCOPE_KEYS:
            if scope[key] != identity[key]:
                raise _Refused("notification_receipt_invalid", 2)
        unsigned = {k: v for k, v in verified.items() if k != "receipt_sha256"}
        if verified["receipt_sha256"] != _digest(unsigned):
            raise _Refused("notification_receipt_invalid", 2)
        scope_sha = _preference_scope_sha(scope)
        with _writer(state_root, now_ms) as (con, now):
            existing = con.execute(
                "SELECT * FROM d17_notification_preferences WHERE scope_sha256=?",
                (scope_sha,)).fetchone()
            if existing is not None and int(existing["notification_generation"]) \
                    > int(verified["notification_generation"]):
                raise _Refused("notification_receipt_invalid", 2)
            snooze = verified["snooze_until_ms"]
            policy = verified["policy"]
            if verified["operation"] == "snooze_notifications" and existing is not None \
                    and policy == "normal":
                policy = existing["policy"] if existing["policy"] == "muted" else "normal"
            con.execute(
                "INSERT INTO d17_notification_preferences(scope_sha256, notification_generation, "
                "policy, snooze_until_ms, updated_ms) VALUES(?,?,?,?,?) "
                "ON CONFLICT(scope_sha256) DO UPDATE SET notification_generation=excluded."
                "notification_generation, policy=excluded.policy, "
                "snooze_until_ms=excluded.snooze_until_ms, updated_ms=excluded.updated_ms",
                (scope_sha, verified["notification_generation"], policy, snooze, now))
            data = {
                "scope_sha256": scope_sha,
                "notification_generation": int(verified["notification_generation"]),
                "policy": policy, "snooze_until_ms": snooze, "updated_ms": now,
            }
            receipt_row = _receipt(con, operation, "notification_policy_applied", None, None, now)
            return _result(operation, "notification_policy_applied", 0, data=data,
                           receipt=receipt_row)

    return _run(operation, request, state_root, now_ms, body)
