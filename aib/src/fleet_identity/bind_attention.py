#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""D17-A bind-attention evaluator and its content-free receipt state.

This first stage only evaluates local inputs and records its own durable
decisions.  It deliberately has no adapter, outbox, authority producer, or
binding callpoint, so every binding ``effect_count`` remains zero.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
for _module_dir in (_REPO_ROOT / "src" / "fleet_identity",
                    _REPO_ROOT / "src" / "rotation"):
    if str(_module_dir) not in sys.path:
        sys.path.insert(0, str(_module_dir))

import authority_store  # noqa: E402
import bind_target  # noqa: E402
import lifecycle  # noqa: E402


RECEIPT_SCHEMA = "ez2-d17-receipt-v1"
CLASSIFICATION_SCHEMA = "ez2-aib/bind-relevance-classification/v1"
CLASSIFIER_VERSION = "d17-v1"
ZERO_SHA256 = "0" * 64

# ★ 下游(C0)只能用這一組欄位問「那一列 receipt 真的在我的帳上嗎」。
#   封閉集合:多一個欄位或少一個欄位都是 query_invalid,因為「呼叫者可以
#   自帶 state_root」正是這支驗證器要擋的那件事。
DURABLE_QUERY_SCHEMA = "ez2-d17-durable-receipt-query/v1"
DURABLE_QUERY_KEYS = frozenset({
    "schema", "receipt_id", "receipt_sha256", "operation", "session_uuid",
    "principal_id", "seat_id", "cwd", "target_fingerprint",
    "mode_generation", "source_kind", "source_event_id_sha256",
})
_QUERY_MATCH_KEYS = tuple(sorted(DURABLE_QUERY_KEYS - {"schema"}))
_QUERY_TEXT_KEYS = tuple(sorted(
    DURABLE_QUERY_KEYS - {"schema", "mode_generation"}))
_QUERY_HEX_KEYS = (
    "receipt_sha256", "target_fingerprint", "source_event_id_sha256")

# 每一列 receipt 都必須帶的欄位。這是**下界**不是等式:evaluate_ctx 與
# evaluate_content 的證據欄位本來就不同(ctx 有 used_pct、content 有
# classification),用等式會把其中一種合法收據判成偽造。
_RECEIPT_REQUIRED_KEYS = frozenset({
    "schema", "receipt_id", "receipt_sha256", "previous_receipt_sha256",
    "operation", "reason", "trigger_kind", "source_kind",
    "source_event_id_sha256", "transport_key_sha256", "session_uuid",
    "principal_id", "seat_id", "cwd", "mode", "mode_generation",
    "target_fingerprint", "grant_id", "confirmation_id",
    "effect_fingerprint", "reminder_count", "effect_count",
    "issued_at_ms", "committed_at_ms",
})

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^d17-[0-9a-f]{32}$")

CTX_USED_THRESHOLD = 10
CONTENT_COOLDOWN_MS = 30 * 60 * 1000
GRANT_TTL_MAX_MS = 24 * 60 * 60 * 1000

MODES = ("remind_only", "confirm_each", "scoped_auto")
SOURCE_KINDS = ("user_prompt_submit", "aib_dm_delivery", "work_item")
TRIGGER_KIND = {
    "evaluate_ctx": "ctx_threshold",
    "evaluate_content": "content",
    "set_mode": "human_action",
    "confirm": "human_action",
    "grant": "human_action",
    "revoke": "human_action",
}

_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_MAX_LEDGER_BYTES = 16 * 1024 * 1024


class _StateError(Exception):
    """The evaluator cannot safely read or append its private state."""


def _canonical_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _exact_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_mode(_rows=None):
    # A has no authenticated producer and therefore never persists a mode.
    return "remind_only"


def _base_result(reason, *, ok, triggered=False, reminder_count=0,
                 mode="remind_only", receipt=None):
    return {
        "ok": bool(ok),
        "reason": reason,
        "rc": 0 if ok else 2,
        "mode": mode if mode in MODES else "remind_only",
        "triggered": bool(triggered),
        "reminder_count": int(reminder_count),
        "effect_count": 0,
        "receipt": receipt,
    }


def _identity_resolved(request):
    if not isinstance(request, dict):
        return False
    for key in ("session_uuid", "principal_id", "seat_id", "cwd"):
        value = request.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return os.path.isabs(request["cwd"])


def _supplied_target(request):
    """取得這一次評估要用的 target —— 只接受呼叫者給的那一個。

    ★ 0378c 09-01 06:55 R2 第 1 條:supplied 的 exact target 必須**原樣**進 receipt。
      舊碼把它加前綴再雜湊一次,於是 receipt 存的是一個與 shared owner 算出來的
      exact target 毫無關係的值,下游永遠比不上。
    ★ 09-01 V2-A:舊碼還留了一條「缺 target 就自己用 identity 推導一個」的退路。
      那條退路讓 A 在沒有 shared owner 的情況下**照樣產出一個看起來合法的
      64hex**,於是「target 沒給」與「target 給對了」兩種輸入產出同形狀的綠。
      現在整支推導函式不存在:形狀不合就是具名拒絕,沒有第二個入口。
    """
    return bind_target.store_known_fingerprint(request.get("target_fingerprint"))


def _transport_key(request, content_sha256=None):
    source_event_id = request.get("source_event_id")
    if not isinstance(source_event_id, str) or not source_event_id:
        return None
    # The content hash detects an upstream id incorrectly reused for a
    # different payload without persisting the payload itself.
    body = {
        "operation": request.get("operation"),
        "session_uuid": request.get("session_uuid"),
        "principal_id": request.get("principal_id"),
        "seat_id": request.get("seat_id"),
        "cwd": request.get("cwd"),
        "source_kind": request.get("source_kind"),
        "source_event_id": source_event_id,
        "content_sha256": content_sha256,
    }
    return _sha256(_canonical_bytes(body))


def _receipt_sha(receipt):
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    return _sha256(_canonical_bytes(body))


def _read_all(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _StateError("receipt ledger is not a private regular file")
    if info.st_size > _MAX_LEDGER_BYTES:
        raise _StateError("receipt ledger is too large")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    remaining = info.st_size
    while remaining:
        chunk = os.read(fd, min(remaining, 1 << 16))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _parse_rows(blob):
    if not blob:
        return []
    if not blob.endswith(b"\n"):
        raise _StateError("receipt ledger has a partial tail")
    rows = []
    previous = ZERO_SHA256
    try:
        for raw in blob.splitlines():
            if not raw:
                raise _StateError("receipt ledger has an empty row")
            row = json.loads(raw.decode("utf-8"))
            if not isinstance(row, dict):
                raise _StateError("receipt row is not an object")
            if row.get("schema") != RECEIPT_SCHEMA:
                raise _StateError("receipt schema mismatch")
            if row.get("previous_receipt_sha256") != previous:
                raise _StateError("receipt chain is not linear")
            if row.get("receipt_sha256") != _receipt_sha(row):
                raise _StateError("receipt hash mismatch")
            previous = row["receipt_sha256"]
            rows.append(row)
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise _StateError("receipt ledger is unreadable")
    return rows


class _Store:
    def __init__(self, root_fd, d17_fd, lock_fd, ledger_fd):
        self.root_fd = root_fd
        self.d17_fd = d17_fd
        self.lock_fd = lock_fd
        self.ledger_fd = ledger_fd
        self.rows = _parse_rows(_read_all(ledger_fd))

    def append(self, receipt):
        line = _canonical_bytes(receipt) + b"\n"
        os.lseek(self.ledger_fd, 0, os.SEEK_END)
        written = os.write(self.ledger_fd, line)
        if written != len(line):
            raise _StateError("receipt append was partial")
        os.fsync(self.ledger_fd)
        try:
            os.fsync(self.d17_fd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
        self.rows.append(receipt)


def _open_private_file(dir_fd, name, flags, mode=0o600):
    open_flags = flags | _O_NOFOLLOW | _O_CLOEXEC
    if open_flags & os.O_CREAT:
        # macOS can transiently return ENOENT when two processes concurrently
        # use one O_CREAT openat() for the same absent file.  Split creation
        # from opening so EEXIST is the only accepted race outcome; ENOENT on
        # the second, non-creating open remains a real missing/replaced path.
        try:
            created_fd = os.open(
                name, open_flags | os.O_EXCL, mode, dir_fd=dir_fd)
        except FileExistsError:
            pass
        else:
            os.close(created_fd)
        open_flags &= ~os.O_CREAT

    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    fd = os.open(name, open_flags, mode, dir_fd=dir_fd)
    try:
        opened = os.fstat(fd)
        after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        expected_identity = (opened.st_dev, opened.st_ino)
        safe = all(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == mode
            and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
            and (info.st_dev, info.st_ino) == expected_identity
            for info in (before, opened, after)
        )
        if not safe:
            raise _StateError(
                "private state file changed or has unsafe metadata")
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def _locked_store(state_root):
    root_path = os.path.abspath(os.fspath(state_root))
    if os.path.lexists(root_path):
        info = os.lstat(root_path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _StateError("state root is not a physical directory")
    else:
        try:
            os.mkdir(root_path, 0o700)
        except OSError as exc:
            raise _StateError("state root cannot be created") from exc

    root_fd = os.open(root_path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC)
    d17_fd = lock_fd = ledger_fd = None
    try:
        try:
            os.mkdir("d17", 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        d17_fd = os.open(
            "d17", os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
            dir_fd=root_fd,
        )
        lock_fd = _open_private_file(
            d17_fd, "state.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ledger_fd = _open_private_file(
            d17_fd, "receipts.jsonl", os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        yield _Store(root_fd, d17_fd, lock_fd, ledger_fd)
    except OSError as exc:
        raise _StateError("D17 state is unavailable") from exc
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        for fd in (ledger_fd, lock_fd, d17_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _classification(content):
    blob = content.encode("utf-8")
    lower = content.casefold()
    domain_terms = (
        "人格", "persona", "角色卡", "第二大腦", "native brain",
        "brain root", "persona binding",
    )
    action_terms = ("綁定", "恢復", "切換", "連接", "使用", "bind", "connect")
    discussion_terms = ("討論", "設計", "結構", "review", "discuss", "design")
    negations = ("不要", "不想", "不得", "勿", "do not", "don't", "not bind")

    has_domain = any(term in lower for term in domain_terms)
    has_action = any(term in lower for term in action_terms)
    is_discussion = any(term in lower for term in discussion_terms)
    is_negated = any(term in lower for term in negations)

    if "```" in content or "auto_bind=" in lower:
        cls, reason, rules = "uncertain", "content_is_data", ["d17.code_or_config_data"]
    elif "引用" in content or "他說" in content or "「" in content \
            or "quoted" in lower or "he said" in lower:
        cls, reason, rules = (
            "uncertain", "content_quoted_or_uncertain", ["d17.quoted_or_uncertain"])
    elif is_negated and not is_discussion:
        cls, reason, rules = "uncertain", "content_negated", ["d17.negated"]
    elif has_domain and has_action and not is_discussion and not is_negated:
        cls, reason, rules = (
            "explicit_bind_intent", "content_explicit", ["d17.explicit_action_target"])
    elif has_domain:
        cls, reason, rules = (
            "domain_relevance", "content_domain_relevance", ["d17.domain_terms"])
    else:
        cls, reason, rules = "not_relevant", "content_not_relevant", ["d17.no_domain_match"]

    return ({
        "schema": CLASSIFICATION_SCHEMA,
        "classifier_version": CLASSIFIER_VERSION,
        "class": cls,
        "rule_ids": rules,
        "input_sha256": _sha256(blob),
        "input_length": len(blob),
        "raw_content_persisted": False,
    }, reason)


def _prior_event_receipt(rows, transport_key):
    if transport_key is None:
        return None
    for row in rows:
        if row.get("transport_key_sha256") == transport_key:
            return row
    return None


def _ctx_decision(rows, request, now_ms, target):
    session_uuid = request.get("session_uuid")
    observation = lifecycle.read_machine_ctx(
        session_uuid,
        ctx_dir=request.get("ctx_dir"),
        now_ms=now_ms,
    )
    generation = request.get("mode_generation", 0)
    if not _exact_int(generation) or generation < 0:
        generation = 0
    tuple_key = _sha256(_canonical_bytes({
        "session_uuid": session_uuid,
        "target_fingerprint": target,
        "mode_generation": generation,
    }))
    extra = {
        "target_fingerprint": target,
        "mode_generation": generation,
        "ctx_tuple_sha256": tuple_key,
    }
    if observation is None:
        reason = "machine_ctx_required" if request.get("screen_text") else "ctx_unavailable"
        return _base_result(reason, ok=True), extra
    used_pct = observation.get("pct")
    extra.update({
        "used_pct": used_pct,
        "observed_ms": observation.get("observed_ms"),
        "machine_truth_ceiling": "same_uid_machine_observation",
        # ★ source/stale 是「這個 pct 憑什麼可以驅動提醒」的兩個前提。舊碼拿它們
        #   判完就丟,收據上只留下結論,於是事後沒有人分得出「機器量到且新鮮」
        #   與「呼叫者自報」——兩者在帳上逐字相同。判準要跟著證據一起落帳。
        "source": observation.get("source"),
        "stale": observation.get("stale"),
    })
    if observation.get("stale") is not False or observation.get("source") != "machine":
        return _base_result("ctx_stale", ok=True), extra
    if not _exact_int(used_pct) or used_pct < CTX_USED_THRESHOLD:
        return _base_result("ctx_below_threshold", ok=True), extra
    if any(row.get("ctx_tuple_sha256") == tuple_key
           and row.get("reminder_count") == 1 for row in rows):
        return _base_result("dedup_suppressed", ok=True), extra
    return _base_result(
        "reminder_emitted", ok=True, triggered=True, reminder_count=1), extra


def _content_decision(rows, request, now_ms, target):
    source_kind = request.get("source_kind")
    if source_kind not in SOURCE_KINDS:
        return _base_result("content_source_invalid", ok=False), {}
    content = request.get("content")
    if not isinstance(content, str):
        return _base_result("content_not_relevant", ok=True), {}

    classification, reason = _classification(content)
    generation = request.get("mode_generation", 0)
    if not _exact_int(generation) or generation < 0:
        generation = 0
    cooldown_key = _sha256(_canonical_bytes({
        "session_uuid": request.get("session_uuid"),
        "target_fingerprint": target,
        "classifier_version": CLASSIFIER_VERSION,
        "class": classification["class"],
        "mode_generation": generation,
    }))
    # Receipt and classification have different frozen schemas.  Flatten the
    # classification evidence except its schema, and retain the complete
    # classification under its named sub-object.
    extra = {key: value for key, value in classification.items()
             if key != "schema"}
    extra.update({
        "classification": dict(classification),
        "target_fingerprint": target,
        "mode_generation": generation,
        "content_cooldown_sha256": cooldown_key,
    })
    remindable = reason in ("content_explicit", "content_domain_relevance")
    if remindable:
        prior = [row for row in rows
                 if row.get("content_cooldown_sha256") == cooldown_key
                 and row.get("reminder_count") == 1]
        if prior:
            last_ms = max(row.get("committed_at_ms", 0) for row in prior
                          if _exact_int(row.get("committed_at_ms", 0)))
            if now_ms < last_ms or now_ms - last_ms < CONTENT_COOLDOWN_MS:
                return _base_result("dedup_suppressed", ok=True), extra
        return _base_result(reason, ok=True, triggered=True, reminder_count=1), extra
    return _base_result(reason, ok=True), extra


def _grant_shape_valid(request, now_ms):
    grant = request.get("grant")
    if not isinstance(grant, dict):
        return False
    scope = grant.get("scope")
    if not isinstance(scope, dict):
        return False
    for key in ("project_id", "persona_uid", "native_root_id"):
        value = scope.get(key)
        if not isinstance(value, str) or not value or value == "*":
            return False
    allowed = scope.get("allowed_trigger_kinds")
    if not isinstance(allowed, list) or not allowed \
            or not all(isinstance(item, str) and item for item in allowed):
        return False
    expires = grant.get("expires_at_ms")
    if not _exact_int(expires) or expires <= now_ms \
            or expires - now_ms > GRANT_TTL_MAX_MS:
        return False
    max_effects = grant.get("max_effects")
    return _exact_int(max_effects) and max_effects == 1


def _authority_decision(authority_path, request, now_ms):
    operation = request.get("operation")
    verified = False
    try:
        authority_store.verify_owner_approval(authority_path, request, now_ms)
        verified = True
    except Exception:  # The A-stage result never exposes verifier internals.
        verified = False

    if operation == "set_mode" and request.get("mode") not in (
            "remind_only", "confirm_each"):
        return _base_result("mode_config_invalid", ok=False), {}
    if operation == "grant" and not _grant_shape_valid(request, now_ms):
        return _base_result("grant_scope_mismatch", ok=False), {}

    # Even a valid verifier proof is only a necessary condition.  This stage
    # has no authenticated-human producer, so it must not create capability.
    del verified
    return _base_result("owner_authority_unavailable", ok=False), {}


def _decision(rows, authority_path, request, now_ms):
    operation = request.get("operation")
    if not _identity_resolved(request):
        return _base_result("identity_unresolved", ok=False), {}
    if operation in ("evaluate_ctx", "evaluate_content"):
        # 評估路一律要有一個形狀合法的 supplied target,缺就是缺,不補。
        try:
            target = _supplied_target(request)
        except bind_target.TargetError:
            return _base_result("target_fingerprint_invalid", ok=False), {}
        if operation == "evaluate_ctx":
            return _ctx_decision(rows, request, now_ms, target)
        return _content_decision(rows, request, now_ms, target)
    supplied = request.get("target_fingerprint")
    if supplied is not None:
        try:
            bind_target.require_fingerprint(supplied)
        except bind_target.TargetError:
            return _base_result("target_fingerprint_invalid", ok=False), {}
    if operation in ("set_mode", "confirm", "grant", "revoke"):
        return _authority_decision(authority_path, request, now_ms)
    return _base_result("bind_refused", ok=False), {}


def _new_receipt(rows, request, result, now_ms, transport_key, extra):
    previous = rows[-1]["receipt_sha256"] if rows else ZERO_SHA256
    operation = request.get("operation")
    trigger_kind = TRIGGER_KIND.get(operation, "human_action")
    source_event_id = request.get("source_event_id")
    source_hash = (_sha256(source_event_id)
                   if isinstance(source_event_id, str) and source_event_id else None)
    identity = {
        "session_uuid": request.get("session_uuid"),
        "principal_id": request.get("principal_id"),
        "seat_id": request.get("seat_id"),
    }
    rid_material = {
        "previous": previous,
        "operation": operation,
        "reason": result["reason"],
        "transport_key": transport_key,
        "identity": identity,
        "now_ms": now_ms,
        "ordinal": len(rows),
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": "d17-" + _sha256(_canonical_bytes(rid_material))[:32],
        "previous_receipt_sha256": previous,
        "operation": operation,
        "reason": result["reason"],
        "trigger_kind": trigger_kind,
        "source_kind": request.get("source_kind") or (
            "machine_ctx" if operation == "evaluate_ctx" else "human_action"),
        "source_event_id_sha256": source_hash,
        "transport_key_sha256": transport_key,
        "session_uuid": identity["session_uuid"],
        "principal_id": identity["principal_id"],
        "seat_id": identity["seat_id"],
        # ★ cwd 是 target 的七件事實之一,也是 durable query 的封閉欄位之一。
        #   舊收據不帶它,於是下游要驗「這一列是不是這個工作目錄下發生的」時
        #   只能自己另外推一個 cwd —— 那就等於讓消費者自己出考卷。
        "cwd": request.get("cwd"),
        "mode": result["mode"],
        "mode_generation": 0,
        # 每一列都留這一格,讓收據形狀不隨 operation 分岔;評估路會由 extra
        # 覆寫成 supplied target,授權路沒有 target 就誠實留 None。
        "target_fingerprint": None,
        "grant_id": None,
        "confirmation_id": None,
        "effect_fingerprint": ZERO_SHA256,
        "reminder_count": result["reminder_count"],
        "effect_count": result["effect_count"],
        "issued_at_ms": now_ms,
        "committed_at_ms": now_ms,
    }
    receipt.update(extra)
    receipt["receipt_sha256"] = _receipt_sha(receipt)
    return receipt


def _status(state_root, request):
    if not _identity_resolved(request):
        return _base_result("identity_unresolved", ok=False, receipt=None)
    path = os.path.abspath(os.fspath(state_root))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return _base_result("status_unbound", ok=True, receipt=None)
    except OSError:
        return _base_result("status_state_unreadable", ok=False, receipt=None)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return _base_result("status_state_unreadable", ok=False, receipt=None)
    return _base_result("status_unbound", ok=True, receipt=None)


def execute(state_root: Path, authority_path: Path, request: dict,
            now_ms: int) -> dict:
    """Evaluate one D17-A request and durably commit its terminal receipt.

    The function has no binding side effects.  Non-status operations serialize
    through an on-disk lock, which makes transport dedup and receipt chaining
    durable across process restarts and concurrent writers.
    """
    if not isinstance(request, dict):
        request = {}
    if not _exact_int(now_ms):
        now_ms = 0
    if request.get("operation") == "status":
        return _status(state_root, request)

    content_sha = None
    if request.get("operation") == "evaluate_content" \
            and isinstance(request.get("content"), str):
        content_sha = _sha256(request["content"].encode("utf-8"))
    transport_key = _transport_key(request, content_sha)

    try:
        with _locked_store(state_root) as store:
            prior = _prior_event_receipt(store.rows, transport_key)
            if prior is not None:
                result = _base_result(
                    "deduplicated", ok=True, triggered=False,
                    reminder_count=0, mode=prior.get("mode", _safe_mode()),
                    receipt=dict(prior),
                )
                return result

            result, extra = _decision(store.rows, authority_path, request, now_ms)
            receipt = _new_receipt(
                store.rows, request, result, now_ms, transport_key, extra)
            store.append(receipt)
            result["receipt"] = dict(receipt)
            return result
    except _StateError:
        # A broken state boundary cannot safely receive the required receipt.
        # Return a loud, closed result instead of following a link or claiming
        # an atomic rollback that did not happen.
        return _base_result("bind_refused", ok=False, receipt=None)


# ---------------------------------------------------------------------------
# Descriptor-only, no-create durable receipt reader
# ---------------------------------------------------------------------------
#
# 這支是**讀者**,不是 execute 的第二個入口。它與寫入路刻意不共用 _locked_store:
# 寫入路的職責裡有「不存在就建立」,而這裡的整個重點就是**打不開就是拒絕**。
# 共用會讓「查詢一筆不存在的收據」順手長出一個空帳本,然後下一次查詢就綠了。
#
# 三條紅線:
#   1. descriptor-only —— 每個路徑元件只 open 一次,拿到 fd 之後只對 fd 操作。
#      中途有人把路徑換掉,我手上的 fd 仍指向原本那個 inode,答案不變。
#   2. no-create —— 全程 O_RDONLY,沒有任何一個 flag 帶 O_CREAT。
#   3. 非阻塞 —— 鎖拿不到就具名回報 lock_busy,不排隊等別人放手。

_VERIFY_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
_VERIFY_FILE_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC


class DurableReceiptError(Exception):
    """具名的、只讀的拒絕。``reason`` 是給下游逐字轉述的那個字串。"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _refuse(reason):
    raise DurableReceiptError(reason)


def _verify_private_dir(info):
    return (stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == os.getuid())


def _verify_private_file(info):
    return (stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1
            and info.st_uid == os.getuid())


def _check_durable_query(query):
    """封閉查詢:欄位集合、型別、形狀都要對,才准去碰檔案系統。"""
    if not isinstance(query, dict) or set(query) != DURABLE_QUERY_KEYS \
            or query.get("schema") != DURABLE_QUERY_SCHEMA:
        _refuse("d17_receipt_query_invalid")
    generation = query["mode_generation"]
    if not _exact_int(generation) or generation < 0:
        _refuse("d17_receipt_query_invalid")
    for key in _QUERY_TEXT_KEYS:
        value = query[key]
        if not isinstance(value, str) or not value:
            _refuse("d17_receipt_query_invalid")
    if _RECEIPT_ID_RE.fullmatch(query["receipt_id"]) is None:
        _refuse("d17_receipt_query_invalid")
    for key in _QUERY_HEX_KEYS:
        if _HEX64_RE.fullmatch(query[key]) is None:
            _refuse("d17_receipt_query_invalid")
    if not os.path.isabs(query["cwd"]):
        _refuse("d17_receipt_query_invalid")


def _descend_to_state_root(state_root):
    """從 ``/`` 起逐段 open,每段只開一次,回傳 state root 的 fd。

    走到底之前不對任何路徑做 stat:先 stat 再 open 的話,兩次系統呼叫之間
    那個名字可以被換成別的東西,而我會用第一次的判斷去信第二次的檔案。
    """
    raw = os.fspath(state_root)
    if not isinstance(raw, str) or not raw or not os.path.isabs(raw) \
            or os.path.normpath(raw) != raw:
        _refuse("d17_receipt_state_path_invalid")
    names = Path(raw).parts[1:]
    current = os.open("/", _VERIFY_DIR_FLAGS)
    try:
        last = len(names) - 1
        for index, name in enumerate(names):
            try:
                child = os.open(name, _VERIFY_DIR_FLAGS, dir_fd=current)
            except FileNotFoundError:
                _refuse("d17_receipt_state_missing")
            except OSError:
                # symlink(O_NOFOLLOW 下的 ELOOP)、非目錄、權限不足都到這裡。
                _refuse("d17_receipt_ancestor_unsafe")
            os.close(current)
            current = child
            info = os.fstat(current)
            if not stat.S_ISDIR(info.st_mode):
                _refuse("d17_receipt_ancestor_unsafe")
            if index == last and not _verify_private_dir(info):
                _refuse("d17_receipt_state_unsafe")
        return current
    except BaseException:
        os.close(current)
        raise


def _open_state_child(parent_fd, name, flags, missing_reason, unsafe_reason,
                      want_dir):
    """在已開啟的目錄 fd 下開一個子項,並用 fstat(而非 path stat)驗它。"""
    try:
        child = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        _refuse(missing_reason)
    except OSError:
        _refuse(unsafe_reason)
    try:
        info = os.fstat(child)
        privileged = _verify_private_dir(info) if want_dir \
            else _verify_private_file(info)
        if not privileged:
            _refuse(unsafe_reason)
    except BaseException:
        os.close(child)
        raise
    return child, info


def _read_pinned(fd, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = os.read(fd, min(remaining, 1 << 16))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_ledger_under_shared_lock(state_root):
    """回傳帳本的完整位元組。任何一段打不開/不私有都是具名拒絕,且不建立東西。"""
    root_fd = d17_fd = lock_fd = ledger_fd = None
    try:
        root_fd = _descend_to_state_root(state_root)
        d17_fd, _ = _open_state_child(
            root_fd, "d17", _VERIFY_DIR_FLAGS,
            "d17_receipt_d17_missing", "d17_receipt_d17_unsafe", True)
        lock_fd, _ = _open_state_child(
            d17_fd, "state.lock", _VERIFY_FILE_FLAGS,
            "d17_receipt_lock_missing", "d17_receipt_lock_unsafe", False)
        try:
            # 共享鎖、非阻塞:別人正在寫的時候我要回報「現在忙」,不是站在
            # 那裡等到他寫完 —— 等待會把「忙」偽裝成「慢」。
            fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            _refuse("d17_receipt_lock_busy")
        try:
            ledger_fd, before = _open_state_child(
                d17_fd, "receipts.jsonl", _VERIFY_FILE_FLAGS,
                "d17_receipt_ledger_missing", "d17_receipt_ledger_unsafe",
                False)
            if before.st_size > _MAX_LEDGER_BYTES:
                _refuse("d17_receipt_ledger_too_large")
            blob = _read_pinned(ledger_fd, before.st_size)
            # 讀完再問一次同一個 fd:如果它在我讀的過程中變成另一個 inode,
            # 我手上這串位元組就不是我以為的那個檔案的內容。
            after = os.fstat(ledger_fd)
            if (after.st_dev, after.st_ino, after.st_nlink, after.st_size) != \
                    (before.st_dev, before.st_ino, before.st_nlink,
                     before.st_size):
                _refuse("d17_receipt_ledger_unsafe")
            return blob
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        for fd in (ledger_fd, lock_fd, d17_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _verify_ledger_chain(blob):
    """逐列驗:canonical bytes、schema 鍵集下界、previous 串接、receipt 重算。

    整本都要走完才回答。只驗到「查到的那一列」就停下來,等於讓偽造者把壞列
    藏在後面 —— 那條鏈斷掉之後,前面那一列的可信度也一起沒了。
    """
    if not blob or not blob.endswith(b"\n"):
        _refuse("d17_receipt_ledger_partial")
    rows = []
    previous = ZERO_SHA256
    for raw in blob.splitlines(keepends=True):
        try:
            row = json.loads(raw.decode("utf-8"))
            canonical = _canonical_bytes(row) if isinstance(row, dict) else None
        except (UnicodeError, ValueError, TypeError):
            # 讀不成 JSON 的一列,連「它是不是 canonical」都問不出來 —— 它
            # 一定不是我寫出去的那個形狀,歸同一個具名拒絕。
            _refuse("d17_receipt_ledger_noncanonical")
        if canonical is None or canonical + b"\n" != raw:
            _refuse("d17_receipt_ledger_noncanonical")
        if row.get("schema") != RECEIPT_SCHEMA \
                or not _RECEIPT_REQUIRED_KEYS <= set(row):
            _refuse("d17_receipt_schema_invalid")
        if row.get("previous_receipt_sha256") != previous:
            _refuse("d17_receipt_previous_invalid")
        if row.get("receipt_sha256") != _receipt_sha(row):
            _refuse("d17_receipt_hash_invalid")
        previous = row["receipt_sha256"]
        rows.append(row)
    return rows


def verify_durable_receipt(state_root: Path, query: dict) -> dict:
    """在 ``state_root`` 的帳本裡證明 ``query`` 指的那一列真的存在且沒被動過。

    成功回傳那一列的深拷貝;任何失敗都 raise :class:`DurableReceiptError`,
    ``.reason`` 是具名字串。這支函式不建立、不修改、不刪除任何東西,也不讀
    環境變數、時鐘或亂數。

    再開條件(艦長 a seat 09-01 裁定要逐字寫下,不得因為現在是綠的就省略)
    ------------------------------------------------------------------
    1. **鍵集用下界(superset)不是等式,而目前沒有任何一格守著它。**
       二方臂 ``test-d17-a-c0-real-integration-second-party.py`` 的參考實作
       ``reference_verify_durable_receipt`` 用的是 ``set(row) != 鍵集`` 的
       **等式**。這裡刻意不照抄:那個鍵集是 ``evaluate_content`` 收據的形狀,
       而 ``evaluate_ctx`` 收據多了 used_pct/observed_ms/source/stale、少了
       classification 那一組。照抄等式會把合法的 ctx 收據判成偽造,混合帳本
       第一列就炸。**能守住這個選擇的那一格是臂的 test_03b,它現在被 V2-C0
       擋住跑不到**,所以本檔對「下界夠不夠緊」這件事目前是零覆蓋。C0 合入後
       第一件事就是回來確認 test_03b 綠,在那之前不要當它已被驗證。

    2. **07b 這一格,產品與臂的參考實作刻意分歧,以臂為準。**
       一列 JSON 解析失敗時,參考實作回 ``d17_receipt_ledger_json_invalid``,
       本檔回 ``d17_receipt_ledger_noncanonical``。理由是臂的 test_07b 寫入
       ``indent=2`` 的多行 JSON 並斷言 ``noncanonical``——解析不出來的一列
       連「它是不是 canonical」都問不出來,歸同一個具名拒絕。臂是可執行規格,
       參考實作不是;兩者衝突時以臂為準。若日後有人要把兩個名字分開,要先改臂。

    3. ★ **07n 那一格的通過依賴 fd 配置,fd 配置改變時它會以「假綠」形式失效。**
       臂的 test_07n 變造的是「同一個 fd 的第 2 次 fstat」。本檔在讀完之後對
       ledger fd 再 fstat 一次比對 (dev,ino,nlink,size),計數對得上是因為
       root/d17/lock 三個 fd 全程持有、ledger 必然拿到全新的 fd 編號。
       **這是推算出來的,不是量出來的。** 若日後有人改動開檔順序、提早關掉
       上層 fd、或在中間插入別的 open,ledger 可能重用一個已被 fstat 過的
       編號,於是變造落在錯的時點——那時這一格會**照樣是綠的**,而它守的
       性質已經沒人守。改動這支函式的開檔順序時要重新量 fstat 計數,不能只看
       臂有沒有紅。

    以下 4-6 由**獨立非作者審者**在第三把刀量到(判定放行,但這三件要登記):

    4. **``d17_receipt_previous_invalid`` 一個名字吃掉三種語意不同的攻擊。**
       實測「改鏈結」「刪整列」「兩列對調」三種竄改**都**回這同一個名字;
       五種壞法只映出 3 個名字。⇒ 下游**分辨不出「有人刪了一列」**,而刪列
       正是收據帳本最該被點名的那一種。要分開就得各自具名,不是加註解。
       ★ 附帶更正一個很容易寫錯的心智模型:**「鏈斷但每列自身雜湊仍對」在本
       產品上不可能發生**。``_receipt_sha`` 只排除 ``receipt_sha256``,
       ``previous_receipt_sha256`` **有進**雜湊本體,所以動了鏈結就同時動了
       該列自身的雜湊;會拿到 ``previous_invalid`` 而不是 ``hash_invalid``
       純粹是檢查順序(866 行在 868 行之前)。這一格守的性質比字面弱一層。

    5. ★ **尾端截斷偵測不到——用竄改藏不住,用刪除藏得住。**
       砍掉尾列之後:查一列仍存在的收據 → **不拒絕、成功回傳**;查被砍掉的
       那一列 → ``d17_receipt_not_found``,與「這筆本來就沒發過」**逐字不可
       分辨**。原因是這條鏈只保證**前綴一致**,沒有長度承諾、尾錨或計數承諾。
       ★ 這一項要對照 ``_verify_ledger_chain`` 自己的 docstring 讀:那裡寫
       「只驗到查到的那一列就停下來,等於讓偽造者把壞列藏在後面」——作者明確
       在乎「藏在後面」這件事,而實測結果是**竄改藏不住、刪除藏得住**。
       要關掉它需要尾錨或列數承諾,本顆沒有做。

    6. **這條鏈沒有金鑰:它證的是內部一致性,不是真實性。**
       用本產品自己的演算法重算整條鏈、附加一列 ``principal_id: p-ATTACKER``
       → **不拒絕、成功回傳那一列偽造收據**。這是無金鑰雜湊鏈的固有性質,
       **不是本顆的缺陷**:真正擋寫入的是 0700/0600 的目錄與檔案模式加 uid
       檢查,鏈只負責「這本帳自己前後對得上」。上面 1-3 項沒有任何一項講到
       這件事,所以在這裡具名寫下:**任何拿這支函式的回傳值當「這筆收據是真
       的」的下游,都讀錯了它的保證**;它只說「在這本帳的內部,這一列一致」。

    審者**沒有**量到的(不得因為第三把刀過了就當這些被涵蓋)
    ----------------------------------------------------------
    - 下列理由名審者**沒有逼它們開火**:``ancestor_unsafe``、``ledger_unsafe``、
      ``lock_unsafe``、``ledger_too_large``、``state_path_invalid``、
      ``duplicate``、``query_mismatch``。名字之間是否還有別的收斂(像第 4 項
      那種一名吃多義),**沒量**,不是量到沒有。
    - 審者用的帳本是 **5 列同質**(全是 ``evaluate_ctx`` 形狀)。
      **混合帳本(ctx 與 content 兩種鍵集並存)沒有跑過**,而那正是上面第 1 項
      (鍵集用下界)擔心的場景 ⇒ **第 1 項仍然為真**,第三把刀沒有涵蓋它。
    """
    _check_durable_query(query)
    rows = _verify_ledger_chain(_read_ledger_under_shared_lock(state_root))
    matches = [row for row in rows
               if row.get("receipt_id") == query["receipt_id"]]
    if len(matches) > 1:
        _refuse("d17_receipt_duplicate")
    if not matches:
        _refuse("d17_receipt_not_found")
    row = matches[0]
    for key in _QUERY_MATCH_KEYS:
        if row.get(key) != query[key]:
            _refuse("d17_receipt_query_mismatch")
    return copy.deepcopy(row)
