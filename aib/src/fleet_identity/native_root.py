#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Active native-second-brain root receipts issued only by a live Ez2 Main.

The receipt path is stable and its bytes are self-hashed.  The database is the
authoritative generation/status ledger; a copied, edited, expired, rolled-back
or revoked receipt therefore cannot silently become the active root.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

try:
    from . import authority_store as auth
except ImportError:  # pragma: no cover
    import authority_store as auth


ROOT_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
RECEIPT_SCHEMA = "ez2-aib/active-native-root-receipt/v1"
REQUEST_SCHEMA = "ez2-aib/active-root-request/v1"
_REQUEST_KEYS = {
    "issue": frozenset((
        "operation", "native_root_id", "root_path", "root_generation",
        "expected_generation", "producer_id", "producer_generation",
        "liveness_path", "issued_at_ms", "expires_at_ms", "schema",
    )),
    "verify": frozenset(("operation", "receipt_path")),
    "renew": frozenset((
        "operation", "receipt_path", "expected_generation", "producer_id",
        "producer_generation", "liveness_path", "expires_at_ms",
    )),
    "revoke": frozenset((
        "operation", "receipt_path", "expected_generation", "producer_id",
        "producer_generation", "liveness_path", "reason_code",
    )),
}


def _tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS active_native_roots(
      native_root_id TEXT PRIMARY KEY,
      receipt_generation INTEGER NOT NULL,
      root_generation INTEGER NOT NULL,
      root_path TEXT NOT NULL,
      root_sha256 TEXT NOT NULL,
      status TEXT NOT NULL,
      producer_id TEXT NOT NULL,
      producer_generation INTEGER NOT NULL,
      liveness_path TEXT NOT NULL,
      issued_at_ms INTEGER NOT NULL,
      expires_at_ms INTEGER NOT NULL,
      receipt_path TEXT NOT NULL,
      receipt_sha256 TEXT NOT NULL,
      reason_code TEXT,
      updated_ms INTEGER NOT NULL
    );
    """)


def _trusted_producer(registry, producer_id, producer_generation):
    row = registry["main_producers"].get(producer_id)
    if not isinstance(row, dict) or row.get("kind") != "ez2_main" \
            or row.get("status") != "active" \
            or row.get("generation") != producer_generation:
        raise auth.AuthorityError("active_root_issuer_untrusted", rc=1)
    return row


def _live_writer(path, producer_id, producer_generation, now_ms):
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise auth.AuthorityError("active_root_writer_not_live", rc=1)
    required = {"schema", "producer_id", "producer_generation", "status",
                "observed_at_ms", "valid_until_ms", "nonce"}
    if not isinstance(doc, dict) or set(doc) != required \
            or doc.get("schema") != "ez2-main/writer-liveness/v1" \
            or doc.get("producer_id") != producer_id \
            or doc.get("producer_generation") != producer_generation \
            or doc.get("status") != "live" \
            or not isinstance(doc.get("observed_at_ms"), int) \
            or not isinstance(doc.get("valid_until_ms"), int) \
            or doc["observed_at_ms"] > int(now_ms) \
            or doc["valid_until_ms"] <= int(now_ms):
        raise auth.AuthorityError("active_root_writer_not_live", rc=1)
    return auth.sha256_hex(raw)


def _has_symlink_component(path):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _validated_root(raw_path, registry):
    if not isinstance(raw_path, str) or not os.path.isabs(raw_path):
        raise auth.AuthorityError("active_root_path_outside_authority", rc=1)
    # Reject textual aliases before resolving them.  Otherwise a request and
    # receipt can name different strings for the same directory.
    if os.path.normpath(raw_path) != raw_path:
        raise auth.AuthorityError("active_root_path_alias", rc=1)
    path = Path(raw_path)
    if _has_symlink_component(path):
        raise auth.AuthorityError("active_root_path_symlink", rc=1)
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise auth.AuthorityError("active_root_path_outside_authority", rc=1)
    if not real.is_dir():
        raise auth.AuthorityError("active_root_path_outside_authority", rc=1)
    allowed = False
    for parent_text in registry["native_root_parents"]:
        parent = Path(parent_text).resolve(strict=False)
        if real == parent or parent in real.parents:
            allowed = True
            break
    if not allowed:
        raise auth.AuthorityError("active_root_path_outside_authority", rc=1)
    return real


def _root_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        try:
            info = path.lstat()
        except OSError:
            raise auth.AuthorityError("active_root_source_unreadable", rc=1)
        if stat.S_ISLNK(info.st_mode):
            raise auth.AuthorityError("active_root_path_symlink", rc=1)
        if stat.S_ISDIR(info.st_mode):
            kind = b"d"
            content = b""
        elif stat.S_ISREG(info.st_mode):
            kind = b"f"
            try:
                content = path.read_bytes()
            except OSError:
                raise auth.AuthorityError("active_root_source_unreadable", rc=1)
        else:
            raise auth.AuthorityError("active_root_source_unsupported", rc=1)
        digest.update(kind + b"\0" + rel.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _receipt_hash(receipt):
    body = {key: value for key, value in receipt.items()
            if key != "receipt_sha256"}
    return auth.sha256_hex(auth.canonical_bytes(body) + b"\n")


def _receipt_path(state_root, native_root_id):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", native_root_id)
    return Path(state_root) / "fleet" / "native-root-receipts" / (safe + ".json")


def _atomic_write(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(str(path.parent), 0o700)
    data = auth.canonical_bytes(receipt) + b"\n"
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    try:
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        os.chmod(str(path), 0o600)
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise auth.AuthorityError("active_root_receipt_write_failed")


def _read_receipt(path):
    try:
        raw = Path(path).read_bytes()
        receipt = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise auth.AuthorityError("active_root_receipt_tampered", rc=1)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA \
            or not isinstance(receipt.get("receipt_sha256"), str) \
            or receipt["receipt_sha256"] != _receipt_hash(receipt):
        raise auth.AuthorityError("active_root_receipt_tampered", rc=1)
    return receipt


def _row(con, native_root_id):
    return con.execute("SELECT * FROM active_native_roots WHERE native_root_id=?",
                       (native_root_id,)).fetchone()


def _cas(row, expected):
    current = int(row["receipt_generation"]) if row else 0
    if not isinstance(expected, int) or expected != current:
        err = auth.AuthorityError("active_root_receipt_generation_conflict", rc=1)
        err.current_generation = current
        raise err
    return current


def _make_receipt(native_root_id, generation, root_generation, root_path,
                  root_sha256, status, producer_id, producer_generation,
                  issued_at_ms, expires_at_ms, liveness_sha256, reason_code=None):
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "native_root_id": native_root_id,
        "generation": int(generation),
        "root_generation": int(root_generation),
        "root_path": str(root_path),
        "root_sha256": root_sha256,
        "status": status,
        "producer_id": producer_id,
        "producer_generation": int(producer_generation),
        "issued_at_ms": int(issued_at_ms),
        "expires_at_ms": int(expires_at_ms),
        "liveness_sha256": liveness_sha256,
    }
    if reason_code is not None:
        receipt["reason_code"] = reason_code
    receipt["receipt_sha256"] = _receipt_hash(receipt)
    return receipt


def _store(con, state_root, receipt, liveness_path, now_ms):
    path = _receipt_path(state_root, receipt["native_root_id"])
    _atomic_write(path, receipt)
    con.execute("""INSERT INTO active_native_roots
        (native_root_id,receipt_generation,root_generation,root_path,root_sha256,
         status,producer_id,producer_generation,liveness_path,issued_at_ms,expires_at_ms,
         receipt_path,receipt_sha256,reason_code,updated_ms)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(native_root_id) DO UPDATE SET
          receipt_generation=excluded.receipt_generation,
          root_generation=excluded.root_generation,root_path=excluded.root_path,
          root_sha256=excluded.root_sha256,status=excluded.status,
          producer_id=excluded.producer_id,
          producer_generation=excluded.producer_generation,
          liveness_path=excluded.liveness_path,
          issued_at_ms=excluded.issued_at_ms,expires_at_ms=excluded.expires_at_ms,
          receipt_path=excluded.receipt_path,
          receipt_sha256=excluded.receipt_sha256,
          reason_code=excluded.reason_code,updated_ms=excluded.updated_ms""",
        (receipt["native_root_id"], receipt["generation"],
         receipt["root_generation"], receipt["root_path"],
         receipt["root_sha256"], receipt["status"], receipt["producer_id"],
         receipt["producer_generation"], str(liveness_path), receipt["issued_at_ms"],
         receipt["expires_at_ms"], str(path), receipt["receipt_sha256"],
         receipt.get("reason_code"), int(now_ms)))
    return path


def _issue(con, state_root, authority_path, request, now_ms):
    registry = auth.load_bootstrap_authority(authority_path)
    native_root_id = request.get("native_root_id")
    if not isinstance(native_root_id, str) or not ROOT_ID_RE.fullmatch(native_root_id):
        raise auth.AuthorityError("active_root_id_invalid")
    producer_id = request.get("producer_id")
    producer_generation = request.get("producer_generation")
    _trusted_producer(registry, producer_id, producer_generation)
    liveness_hash = _live_writer(request.get("liveness_path"), producer_id,
                                 producer_generation, now_ms)
    root = _validated_root(request.get("root_path"), registry)
    root_generation = request.get("root_generation")
    expires = request.get("expires_at_ms")
    issued = request.get("issued_at_ms")
    if not isinstance(root_generation, int) or root_generation < 1:
        raise auth.AuthorityError("active_root_generation_invalid")
    if not isinstance(issued, int) or not isinstance(expires, int) \
            or issued > int(now_ms) or expires <= int(now_ms):
        raise auth.AuthorityError("active_root_expiry_invalid")
    root_sha256 = _root_hash(root)
    with auth.immediate(con):
        row = _row(con, native_root_id)
        current = _cas(row, request.get("expected_generation"))
        if row is not None and root_generation < int(row["root_generation"]):
            raise auth.AuthorityError("active_root_generation_rollback", rc=1)
        receipt = _make_receipt(
            native_root_id, current + 1, root_generation, root, root_sha256,
            "active", producer_id, producer_generation, issued, expires,
            liveness_hash,
        )
        path = _store(con, state_root, receipt, request.get("liveness_path"),
                      now_ms)
    return auth.result_ok(reason="active_root_issued", receipt=receipt,
                          receipt_path=str(path))


def _load_current(con, receipt_path):
    receipt = _read_receipt(receipt_path)
    row = _row(con, receipt.get("native_root_id"))
    if row is None or str(Path(receipt_path)) != row["receipt_path"] \
            or receipt.get("generation") != int(row["receipt_generation"]) \
            or receipt.get("receipt_sha256") != row["receipt_sha256"] \
            or receipt.get("root_path") != row["root_path"]:
        raise auth.AuthorityError("active_root_receipt_tampered", rc=1)
    return row, receipt


def _verify(con, request, now_ms):
    row, receipt = _load_current(con, request.get("receipt_path"))
    if row["status"] == "revoked" or receipt.get("status") == "revoked":
        raise auth.AuthorityError("active_root_revoked", rc=1)
    if int(row["expires_at_ms"]) <= int(now_ms):
        raise auth.AuthorityError("active_root_expired", rc=1)
    current_liveness = _live_writer(
        row["liveness_path"], row["producer_id"],
        int(row["producer_generation"]), now_ms)
    if current_liveness != receipt.get("liveness_sha256"):
        raise auth.AuthorityError("active_root_writer_not_live", rc=1)
    return auth.result_ok(reason="active_root_verified", receipt=receipt,
                          receipt_path=row["receipt_path"])


def _renew_or_revoke(con, state_root, authority_path, request, now_ms, revoke):
    row, old = _load_current(con, request.get("receipt_path"))
    registry = auth.load_bootstrap_authority(authority_path)
    producer_id = request.get("producer_id")
    producer_generation = request.get("producer_generation")
    _trusted_producer(registry, producer_id, producer_generation)
    liveness_hash = _live_writer(request.get("liveness_path"), producer_id,
                                 producer_generation, now_ms)
    with auth.immediate(con):
        # Re-read after BEGIN IMMEDIATE so concurrent writers cannot pass CAS.
        row = _row(con, old["native_root_id"])
        current = _cas(row, request.get("expected_generation"))
        if row["status"] == "revoked":
            raise auth.AuthorityError("active_root_revoked", rc=1)
        if revoke:
            reason_code = request.get("reason_code")
            if not isinstance(reason_code, str) or not reason_code:
                raise auth.AuthorityError("active_root_revocation_reason_missing")
            status = "revoked"
            expires = int(row["expires_at_ms"])
        else:
            reason_code = None
            status = "active"
            expires = request.get("expires_at_ms")
            if not isinstance(expires, int) or expires <= int(now_ms):
                raise auth.AuthorityError("active_root_expiry_invalid")
        receipt = _make_receipt(
            old["native_root_id"], current + 1, int(row["root_generation"]),
            row["root_path"], row["root_sha256"], status, producer_id,
            producer_generation, int(now_ms), expires, liveness_hash,
            reason_code=reason_code,
        )
        path = _store(con, state_root, receipt, request.get("liveness_path"),
                      now_ms)
    return auth.result_ok(
        reason="active_root_revoked" if revoke else "active_root_renewed",
        receipt=receipt, receipt_path=str(path),
    )


def execute(state_root, authority_path, request, now_ms):
    con = None
    try:
        if not isinstance(request, dict):
            raise auth.AuthorityError("active_root_request_invalid")
        operation = request.get("operation")
        if operation in _REQUEST_KEYS \
                and set(request) - _REQUEST_KEYS[operation]:
            raise auth.AuthorityError("active_root_request_invalid", rc=1)
        con = auth.open_db(state_root)
        _tables(con)
        if operation == "issue":
            return _issue(con, state_root, authority_path, request, now_ms)
        if operation == "verify":
            return _verify(con, request, now_ms)
        if operation == "renew":
            return _renew_or_revoke(con, state_root, authority_path, request,
                                    now_ms, False)
        if operation == "revoke":
            return _renew_or_revoke(con, state_root, authority_path, request,
                                    now_ms, True)
        raise auth.AuthorityError("active_root_operation_unknown")
    except auth.AuthorityError as err:
        result = auth.result_error(err)
        if hasattr(err, "current_generation"):
            result["current_generation"] = err.current_generation
        return result
    except Exception:
        return auth.result_error(auth.AuthorityError("active_root_internal_error"))
    finally:
        if con is not None:
            con.close()
