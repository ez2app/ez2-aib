#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Immutable, session-bound runtime model observations for P1A bindings.

Seat records describe how a provider was launched.  They are not evidence of
which model the provider actually attached to the session.  This module keeps
that second fact in a separate repo-owned store.  Repo-owned adapters read an
exact provider JSONL artifact, bind its session anchor to its latest real model
event, and retain only closed provenance (path, line indexes and line hashes).
Transcript/prompt payload bytes never enter the runtime source store.

This module intentionally does not infer a model from a process name, seat
label, request argument or provider default.  If no provider adapter has
produced a source event, the only honest result is a named fail-closed error.
"""

from __future__ import annotations

import hashlib
import fcntl
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from . import authority_store as auth


SOURCE_SCHEMA = "ez2-aib/provider-runtime-model-source/v1"
OBSERVATION_SCHEMA = "ez2-aib/session-model-observation/v1"
SOURCE_REF_SCHEMA = "ez2-aib/runtime-model-source-ref/v1"
ADAPTER_REF_SCHEMA = "ez2-aib/runtime-model-adapter-ref/v1"
ARTIFACT_REF_SCHEMA = "ez2-aib/runtime-model-artifact-ref/v1"
LINE_REF_SCHEMA = "ez2-aib/runtime-model-line-ref/v1"
MAX_FRESHNESS_MS = 120_000

_SOURCE_KEYS = frozenset((
    "schema", "session_uuid", "provider", "effective_model",
    "observed_at_ms", "adapter", "artifact",
))
_ADAPTER_REF_KEYS = frozenset(("schema", "id"))
_ARTIFACT_REF_KEYS = frozenset((
    "schema", "path", "session_event", "model_event", "model_context",
))
_LINE_REF_KEYS = frozenset(("schema", "line_index", "line_sha256"))
_SOURCE_REF_KEYS = frozenset(("schema", "kind", "path", "sha256"))
_OBSERVATION_KEYS = frozenset((
    "schema", "session_uuid", "provider", "effective_model", "source",
    "observed_at_ms", "expires_at_ms", "recorded_at_ms",
    "observation_path", "observation_sha256",
))
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:/-]*$")
_ADAPTER_IDS = {
    "claude": "claude-transcript-assistant-message/v1",
    "codex": "codex-rollout-turn-context/v1",
    "copilot": "copilot-events-main-model-event/v1",
}


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _regular_bytes(path, reason):
    """Read one regular single-link file without a final-component race."""
    path = Path(path)
    descriptor = None
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) \
                or before.st_nlink != 1:
            raise OSError("unsafe runtime observation file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(path), flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 \
                or (opened.st_dev, opened.st_ino) != \
                (before.st_dev, before.st_ino):
            raise OSError("runtime observation file changed while opening")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = path.lstat()
        if stat.S_ISLNK(after.st_mode) or (after.st_dev, after.st_ino) != \
                (opened.st_dev, opened.st_ino):
            raise OSError("runtime observation file changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise auth.AuthorityError(reason, rc=1) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _line_ref(value):
    if not isinstance(value, dict) or set(value) != _LINE_REF_KEYS \
            or value.get("schema") != LINE_REF_SCHEMA \
            or not isinstance(value.get("line_index"), int) \
            or isinstance(value["line_index"], bool) \
            or value["line_index"] < 0 \
            or not _SHA_RE.fullmatch(str(value.get("line_sha256", ""))):
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    return value


def _artifact_ref(value):
    if not isinstance(value, dict) or set(value) != _ARTIFACT_REF_KEYS \
            or value.get("schema") != ARTIFACT_REF_SCHEMA \
            or not isinstance(value.get("path"), str) \
            or not value["path"] or not Path(value["path"]).is_absolute() \
            or os.path.normpath(value["path"]) != value["path"]:
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    _line_ref(value.get("session_event"))
    _line_ref(value.get("model_event"))
    _line_ref(value.get("model_context"))
    return value


def _source_document(raw):
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise auth.AuthorityError(
            "runtime_model_source_invalid", rc=1) from exc
    if not isinstance(value, dict) or set(value) != _SOURCE_KEYS \
            or value.get("schema") != SOURCE_SCHEMA:
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    if not _UUID_RE.fullmatch(str(value.get("session_uuid", ""))):
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    provider = value.get("provider")
    if provider not in _ADAPTER_IDS \
            or not isinstance(value.get("effective_model"), str) \
            or not _MODEL_RE.fullmatch(value["effective_model"]):
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    observed = value.get("observed_at_ms")
    if not isinstance(observed, int) or isinstance(observed, bool) \
            or observed < 1:
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    adapter = value.get("adapter")
    if not isinstance(adapter, dict) or set(adapter) != _ADAPTER_REF_KEYS \
            or adapter.get("schema") != ADAPTER_REF_SCHEMA \
            or adapter.get("id") != _ADAPTER_IDS[provider]:
        raise auth.AuthorityError("runtime_model_source_invalid", rc=1)
    _artifact_ref(value.get("artifact"))
    return value


def _artifact_error(reason, exc=None):
    err = auth.AuthorityError(reason, rc=1)
    if exc is None:
        raise err
    raise err from exc


def _canonical_artifact_path(value):
    if not isinstance(value, (str, os.PathLike)):
        _artifact_error("runtime_model_artifact_unsafe")
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        _artifact_error("runtime_model_artifact_unsafe")
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except (OSError, RuntimeError) as exc:
        _artifact_error("runtime_model_artifact_unreadable", exc)
    if resolved != path or stat.S_ISLNK(info.st_mode) \
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _artifact_error("runtime_model_artifact_unsafe")
    return path


def _scan_jsonl(path, visitor, *, stop_after=None, stable_snapshot=True):
    """Visit complete JSONL records from one safely opened artifact.

    Collection requires an unchanged size/mtime snapshot so the selected event
    was really the tail at one instant.  Exact provenance verification may stop
    at the referenced indexes; later append-only records do not rewrite old
    evidence.
    """
    path = _canonical_artifact_path(path)
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(path), flags)
        before = os.fstat(descriptor)
        named = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 \
                or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("artifact changed while opening")
        remaining = int(before.st_size)
        line_index = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while remaining > 0:
                raw = stream.readline(remaining)
                if not raw:
                    break
                remaining -= len(raw)
                # A provider may currently be appending its final JSON object.
                # It is not evidence until the newline closes the record.
                if not raw.endswith(b"\n"):
                    if stable_snapshot:
                        _artifact_error("runtime_model_artifact_changed")
                    break
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError) as exc:
                    _artifact_error("runtime_model_artifact_malformed", exc)
                if not isinstance(value, dict):
                    _artifact_error("runtime_model_artifact_malformed")
                visitor(line_index, raw, value)
                if stop_after is not None and line_index >= stop_after:
                    break
                line_index += 1
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        if (after_fd.st_dev, after_fd.st_ino) != \
                (after_path.st_dev, after_path.st_ino):
            raise OSError("artifact path changed while reading")
        if stable_snapshot and (
                after_fd.st_size != before.st_size
                or getattr(after_fd, "st_mtime_ns", None) !=
                getattr(before, "st_mtime_ns", None)):
            _artifact_error("runtime_model_artifact_changed")
    except auth.AuthorityError:
        raise
    except OSError as exc:
        _artifact_error("runtime_model_artifact_unreadable", exc)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return path


def _timestamp_ms(value):
    if not isinstance(value, str) or not value:
        _artifact_error("runtime_model_artifact_malformed")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        _artifact_error("runtime_model_artifact_malformed", exc)
    if parsed.tzinfo is None:
        _artifact_error("runtime_model_artifact_malformed")
    observed = int(parsed.astimezone(timezone.utc).timestamp() * 1000)
    if observed < 1:
        _artifact_error("runtime_model_artifact_malformed")
    return observed


def _new_line_ref(index, raw):
    return {
        "schema": LINE_REF_SCHEMA,
        "line_index": int(index),
        "line_sha256": _sha256(raw),
    }


def _model_value(value):
    if not isinstance(value, str) or not _MODEL_RE.fullmatch(value):
        _artifact_error("runtime_model_artifact_malformed")
    return value


def _candidate(path, provider, session_ref, model_ref, model, observed_at_ms,
               *, context_ref=None):
    return {
        "provider": provider,
        "effective_model": model,
        "observed_at_ms": int(observed_at_ms),
        "adapter": {
            "schema": ADAPTER_REF_SCHEMA,
            "id": _ADAPTER_IDS[provider],
        },
        "artifact": {
            "schema": ARTIFACT_REF_SCHEMA,
            "path": str(path),
            "session_event": session_ref,
            "model_event": model_ref,
            "model_context": context_ref or model_ref,
        },
    }


def _latest_model_event(events):
    if not events:
        _artifact_error("runtime_model_artifact_model_event_missing")
    previous = None
    for event in events:
        if previous is not None and event[4] < previous:
            _artifact_error("runtime_model_artifact_malformed")
        previous = event[4]
    return events[-1]


def _codex_candidate(path, session_uuid):
    anchors = []
    models = []

    def visit(index, raw, value):
        event_type = value.get("type")
        if event_type == "session_meta":
            payload = value.get("payload")
            if not isinstance(payload, dict) \
                    or not _UUID_RE.fullmatch(str(payload.get("session_id", ""))):
                _artifact_error("runtime_model_artifact_malformed")
            anchors.append((payload["session_id"], _new_line_ref(index, raw)))
        elif event_type == "turn_context":
            payload = value.get("payload")
            if not isinstance(payload, dict):
                _artifact_error("runtime_model_artifact_malformed")
            models.append((
                index, _new_line_ref(index, raw),
                _new_line_ref(index, raw),
                _model_value(payload.get("model")),
                _timestamp_ms(value.get("timestamp")),
            ))

    path = _scan_jsonl(path, visit)
    if len(anchors) != 1:
        _artifact_error(
            "runtime_model_artifact_ambiguous" if anchors
            else "runtime_model_artifact_malformed")
    if anchors[0][1]["line_index"] != 0:
        _artifact_error("runtime_model_artifact_malformed")
    if anchors[0][0] != session_uuid:
        _artifact_error("runtime_model_artifact_session_mismatch")
    _index, model_ref, context_ref, model, observed = _latest_model_event(models)
    return _candidate(
        path, "codex", anchors[0][1], model_ref, model, observed,
        context_ref=context_ref)


def _claude_candidate(path, session_uuid):
    path = _canonical_artifact_path(path)
    if path.name != session_uuid + ".jsonl":
        _artifact_error("runtime_model_artifact_session_mismatch")
    models = []

    def visit(index, raw, value):
        if value.get("type") != "assistant":
            return
        message = value.get("message")
        if not isinstance(message, dict):
            _artifact_error("runtime_model_artifact_malformed")
        model = message.get("model")
        if model == "<synthetic>":
            return
        if message.get("type") != "message" or value.get("isSidechain") is True:
            return
        session_id = value.get("sessionId")
        if not _UUID_RE.fullmatch(str(session_id or "")):
            _artifact_error("runtime_model_artifact_malformed")
        if session_id != session_uuid:
            _artifact_error("runtime_model_artifact_session_mismatch")
        models.append((
            index, _new_line_ref(index, raw), _new_line_ref(index, raw),
            _model_value(model),
            _timestamp_ms(value.get("timestamp")),
        ))

    path = _scan_jsonl(path, visit)
    _index, model_ref, context_ref, model, observed = _latest_model_event(models)
    return _candidate(
        path, "claude", model_ref, model_ref, model, observed,
        context_ref=context_ref)


def _copilot_candidate(path, session_uuid):
    path = _canonical_artifact_path(path)
    if path.name != "events.jsonl" or path.parent.name != session_uuid:
        _artifact_error("runtime_model_artifact_session_mismatch")
    anchors = []
    models = []
    interactions = {}

    def visit(index, raw, value):
        event_type = value.get("type")
        data = value.get("data")
        if event_type == "session.start":
            if not isinstance(data, dict) \
                    or not _UUID_RE.fullmatch(str(data.get("sessionId", ""))):
                _artifact_error("runtime_model_artifact_malformed")
            anchors.append((data["sessionId"], _new_line_ref(index, raw)))
        elif event_type == "user.message":
            if not isinstance(data, dict):
                _artifact_error("runtime_model_artifact_malformed")
            interaction_id = data.get("interactionId")
            if isinstance(interaction_id, str) and interaction_id:
                if interaction_id in interactions:
                    _artifact_error("runtime_model_artifact_ambiguous")
                interactions[interaction_id] = (
                    _new_line_ref(index, raw),
                    data.get("parentAgentTaskId") not in (None, "")
                    or value.get("parentAgentTaskId") not in (None, ""),
                )
        elif event_type in ("assistant.message", "session.model_change"):
            if not isinstance(data, dict):
                _artifact_error("runtime_model_artifact_malformed")
            if data.get("parentAgentTaskId") not in (None, "") \
                    or value.get("parentAgentTaskId") not in (None, ""):
                return
            model = data.get("model") if event_type == "assistant.message" \
                else data.get("newModel")
            if event_type == "assistant.message":
                interaction_id = data.get("interactionId")
                if isinstance(interaction_id, str) and interaction_id:
                    context = interactions.get(interaction_id)
                    if context is None:
                        _artifact_error("runtime_model_artifact_malformed")
                    if context[1]:
                        return
                    context_ref = context[0]
                else:
                    context_ref = _new_line_ref(index, raw)
            else:
                context_ref = _new_line_ref(index, raw)
            models.append((
                index, _new_line_ref(index, raw),
                context_ref,
                _model_value(model),
                _timestamp_ms(value.get("timestamp")),
            ))

    path = _scan_jsonl(path, visit)
    if len(anchors) != 1:
        _artifact_error(
            "runtime_model_artifact_ambiguous" if anchors
            else "runtime_model_artifact_malformed")
    if anchors[0][1]["line_index"] != 0:
        _artifact_error("runtime_model_artifact_malformed")
    if anchors[0][0] != session_uuid:
        _artifact_error("runtime_model_artifact_session_mismatch")
    _index, model_ref, context_ref, model, observed = _latest_model_event(models)
    return _candidate(
        path, "copilot", anchors[0][1], model_ref, model, observed,
        context_ref=context_ref)


def _artifact_candidate(provider, path, session_uuid):
    if provider == "codex":
        return _codex_candidate(path, session_uuid)
    if provider == "claude":
        return _claude_candidate(path, session_uuid)
    if provider == "copilot":
        return _copilot_candidate(path, session_uuid)
    if provider == "agy":
        _artifact_error("runtime_model_collector_unsupported_agy")
    _artifact_error("runtime_model_collector_provider_unsupported")


def _codex_anchor_session(path):
    """Read only the required first-line session anchor for broad discovery."""
    found = []

    def visit(index, raw, value):
        del index, raw
        payload = value.get("payload")
        if value.get("type") != "session_meta" \
                or not isinstance(payload, dict) \
                or not _UUID_RE.fullmatch(str(payload.get("session_id", ""))):
            _artifact_error("runtime_model_artifact_malformed")
        found.append(payload["session_id"])

    _scan_jsonl(path, visit, stop_after=0, stable_snapshot=False)
    if len(found) != 1:
        _artifact_error("runtime_model_artifact_malformed")
    return found[0]


def _referenced_lines(artifact):
    refs = {
        artifact["session_event"]["line_index"]: artifact["session_event"],
        artifact["model_event"]["line_index"]: artifact["model_event"],
        artifact["model_context"]["line_index"]: artifact["model_context"],
    }
    captured = {}

    def visit(index, raw, value):
        if index in refs:
            if _sha256(raw) != refs[index]["line_sha256"]:
                _artifact_error("runtime_model_source_provenance_mismatch")
            captured[index] = value

    _scan_jsonl(
        artifact["path"], visit, stop_after=max(refs), stable_snapshot=False)
    if set(captured) != set(refs):
        _artifact_error("runtime_model_source_provenance_mismatch")
    return (
        captured[artifact["session_event"]["line_index"]],
        captured[artifact["model_event"]["line_index"]],
        captured[artifact["model_context"]["line_index"]],
    )


def _verify_exact_provenance(source):
    provider = source["provider"]
    session_uuid = source["session_uuid"]
    artifact = source["artifact"]
    path = _canonical_artifact_path(artifact["path"])
    session_event, model_event, model_context = _referenced_lines(artifact)
    if provider == "codex":
        payload = session_event.get("payload")
        model_payload = model_event.get("payload")
        if artifact["session_event"]["line_index"] != 0 \
                or artifact["model_context"] != artifact["model_event"] \
                or session_event.get("type") != "session_meta" \
                or not isinstance(payload, dict) \
                or payload.get("session_id") != session_uuid \
                or model_event.get("type") != "turn_context" \
                or not isinstance(model_payload, dict):
            _artifact_error("runtime_model_source_provenance_mismatch")
        model = model_payload.get("model")
    elif provider == "claude":
        message = model_event.get("message")
        if artifact["session_event"] != artifact["model_event"] \
                or artifact["model_context"] != artifact["model_event"] \
                or path.name != session_uuid + ".jsonl" \
                or model_event.get("type") != "assistant" \
                or model_event.get("sessionId") != session_uuid \
                or model_event.get("isSidechain") is True \
                or not isinstance(message, dict) \
                or message.get("type") != "message" \
                or message.get("model") == "<synthetic>":
            _artifact_error("runtime_model_source_provenance_mismatch")
        model = message.get("model")
    elif provider == "copilot":
        data = session_event.get("data")
        model_data = model_event.get("data")
        model_event_type = model_event.get("type")
        if path.name != "events.jsonl" or path.parent.name != session_uuid \
                or artifact["session_event"]["line_index"] != 0 \
                or session_event.get("type") != "session.start" \
                or not isinstance(data, dict) \
                or data.get("sessionId") != session_uuid \
                or model_event_type not in (
                    "assistant.message", "session.model_change") \
                or not isinstance(model_data, dict) \
                or model_data.get("parentAgentTaskId") not in (None, "") \
                or model_event.get("parentAgentTaskId") not in (None, ""):
            _artifact_error("runtime_model_source_provenance_mismatch")
        if model_event_type == "assistant.message":
            context_data = model_context.get("data")
            interaction_id = model_data.get("interactionId")
            if isinstance(interaction_id, str) and interaction_id:
                if artifact["model_context"]["line_index"] >= \
                        artifact["model_event"]["line_index"] \
                        or model_context.get("type") != "user.message" \
                        or not isinstance(context_data, dict) \
                        or context_data.get("interactionId") != interaction_id \
                        or context_data.get("parentAgentTaskId") not in (None, "") \
                        or model_context.get("parentAgentTaskId") not in (None, ""):
                    _artifact_error("runtime_model_source_provenance_mismatch")
            elif artifact["model_context"] != artifact["model_event"]:
                _artifact_error("runtime_model_source_provenance_mismatch")
            model = model_data.get("model")
        else:
            if artifact["model_context"] != artifact["model_event"]:
                _artifact_error("runtime_model_source_provenance_mismatch")
            model = model_data.get("newModel")
    else:  # The closed source validator normally catches this first.
        _artifact_error("runtime_model_source_provenance_mismatch")
    try:
        checked_model = _model_value(model)
        observed = _timestamp_ms(model_event.get("timestamp"))
    except auth.AuthorityError as exc:
        _artifact_error("runtime_model_source_provenance_mismatch", exc)
    if checked_model != source["effective_model"] \
            or observed != source["observed_at_ms"]:
        _artifact_error("runtime_model_source_provenance_mismatch")
    return source


def _verify_latest_provenance(source):
    candidate = _artifact_candidate(
        source["provider"], source["artifact"]["path"],
        source["session_uuid"])
    expected = {
        "provider": source["provider"],
        "effective_model": source["effective_model"],
        "observed_at_ms": source["observed_at_ms"],
        "adapter": source["adapter"],
        "artifact": source["artifact"],
    }
    if candidate != expected:
        _artifact_error("runtime_model_source_not_latest")
    return source


def _source_for_candidate(session_uuid, candidate):
    return {
        "schema": SOURCE_SCHEMA,
        "session_uuid": session_uuid,
        "provider": candidate["provider"],
        "effective_model": candidate["effective_model"],
        "observed_at_ms": candidate["observed_at_ms"],
        "adapter": candidate["adapter"],
        "artifact": candidate["artifact"],
    }


def _default_artifact_paths(provider, session_uuid):
    home = Path.home()
    if provider == "claude":
        projects = _discovery_directory(home / ".claude" / "projects")
        return [] if projects is None else sorted(projects.glob(
            "*/%s.jsonl" % session_uuid))
    if provider == "codex":
        codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
        sessions = _discovery_directory(codex_home / "sessions")
        return [] if sessions is None else sorted(
            sessions.rglob("rollout-*.jsonl"))
    if provider == "copilot":
        root = home / ".copilot" / "session-state"
        return [root / session_uuid / "events.jsonl"]
    if provider == "agy":
        _artifact_error("runtime_model_collector_unsupported_agy")
    _artifact_error("runtime_model_collector_provider_unsupported")


def _discovery_directory(value):
    path = Path(os.path.abspath(os.path.expanduser(str(value))))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        _artifact_error("runtime_model_artifact_unreadable", exc)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        _artifact_error("runtime_model_artifact_unreadable", exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) \
            or resolved != path:
        _artifact_error("runtime_model_artifact_unsafe")
    return path


def _artifact_path_list(value):
    if isinstance(value, (str, os.PathLike)):
        rows = [Path(value)]
    else:
        try:
            rows = [Path(item) for item in value]
        except (TypeError, ValueError):
            _artifact_error("runtime_model_artifact_unreadable")
    unique = []
    seen = set()
    for row in rows:
        key = str(row)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _atomic_create(path, raw, reason):
    """Create immutable bytes once; an exact replay is idempotent."""
    path = Path(path)
    try:
        parent_info = path.parent.lstat()
        if path.parent.is_symlink() or not stat.S_ISDIR(parent_info.st_mode) \
                or path.parent.resolve() != path.parent:
            raise OSError("unsafe runtime observation parent")
    except (OSError, RuntimeError) as exc:
        raise auth.AuthorityError(reason, rc=1) from exc
    if path.is_symlink():
        raise auth.AuthorityError(reason, rc=1)
    if path.exists():
        if _regular_bytes(path, reason) != raw:
            raise auth.AuthorityError(reason, rc=1)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent), prefix=".%s." % path.name,
                delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(str(temporary), str(path))
        except FileExistsError:
            if _regular_bytes(path, reason) != raw:
                raise auth.AuthorityError(reason, rc=1)
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _safe_state_root(state_root):
    root = Path(os.path.abspath(str(state_root)))
    try:
        info = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(info.st_mode) \
                or root.resolve() != root:
            raise OSError("unsafe runtime observation state root")
    except (OSError, RuntimeError) as exc:
        raise auth.AuthorityError("runtime_model_store_unsafe", rc=1) from exc
    return root


def _store_dir(state_root, name, *, create=False):
    root = _safe_state_root(state_root)
    path = root / name
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise auth.AuthorityError(
                "runtime_model_store_unsafe", rc=1) from exc
    if path.exists() or path.is_symlink():
        try:
            info = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(info.st_mode) \
                    or path.resolve() != path:
                raise OSError("unsafe runtime observation store")
        except (OSError, RuntimeError) as exc:
            raise auth.AuthorityError(
                "runtime_model_store_unsafe", rc=1) from exc
    return path


def _source_store(state_root, *, create=False):
    return _store_dir(state_root, "runtime-model-sources", create=create)


def _observation_store(state_root, *, create=False):
    return _store_dir(
        state_root, "runtime-model-observations", create=create)


def _observation_session_dir(state_root, session_uuid, *, create=False):
    store = _observation_store(state_root, create=create)
    path = store / session_uuid
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise auth.AuthorityError(
                "runtime_model_store_unsafe", rc=1) from exc
    if path.exists() or path.is_symlink():
        try:
            info = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(info.st_mode) \
                    or path.resolve() != path:
                raise OSError("unsafe runtime observation session store")
        except (OSError, RuntimeError) as exc:
            raise auth.AuthorityError(
                "runtime_model_store_unsafe", rc=1) from exc
    return path


def acquire_session_lock(state_root, session_uuid, *, exclusive, create=False):
    """Fence one session head across filesystem observation and DB effects."""
    if not _UUID_RE.fullmatch(str(session_uuid or "")):
        raise auth.AuthorityError("runtime_model_observation_session_invalid")
    directory = _observation_session_dir(
        state_root, session_uuid, create=create)
    if not directory.is_dir() or directory.is_symlink():
        raise auth.AuthorityError("runtime_model_observation_missing", rc=1)
    lock_path = directory / ".head.lock"
    # A copied/corrupt observation directory may predate the lock file.  Open
    # it safely here so diagnostics can still name the bad session/provider
    # document; recorders and binding writers then share this same fence.
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(lock_path), flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("unsafe runtime observation lock")
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return fd
    except OSError as exc:
        try:
            os.close(fd)
        except (NameError, OSError):
            pass
        raise auth.AuthorityError("runtime_model_store_unsafe", rc=1) from exc


def release_session_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _record_locked(state_root, source_raw, source_doc, now_ms):
    source_sha = _sha256(source_raw)
    stored_source = _source_store(
        state_root, create=True) / (source_sha + ".json")
    _atomic_create(
        stored_source, source_raw, "runtime_model_source_store_conflict")

    session_uuid = source_doc["session_uuid"]
    observed_at = int(source_doc["observed_at_ms"])
    observation_path = _observation_session_dir(
        state_root, session_uuid, create=True) / (
        "%d-%s.json" % (observed_at, source_sha[:16]))
    if observation_path.exists() or observation_path.is_symlink():
        raw = _regular_bytes(
            observation_path, "runtime_model_observation_store_conflict")
        try:
            existing = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise auth.AuthorityError(
                "runtime_model_observation_store_conflict", rc=1) from exc
        return verify(
            state_root, existing, session_uuid, source_doc["provider"],
            now_ms, require_fresh=False)
    body = {
        "schema": OBSERVATION_SCHEMA,
        "session_uuid": session_uuid,
        "provider": source_doc["provider"],
        "effective_model": source_doc["effective_model"],
        "source": {
            "schema": SOURCE_REF_SCHEMA,
            "kind": "provider_session_runtime",
            "path": str(stored_source),
            "sha256": source_sha,
        },
        "observed_at_ms": observed_at,
        "expires_at_ms": observed_at + MAX_FRESHNESS_MS,
        "recorded_at_ms": int(now_ms),
        "observation_path": str(observation_path),
    }
    observation = dict(body, observation_sha256=_sha256(
        auth.canonical_bytes(body)))
    _atomic_create(
        observation_path, auth.canonical_bytes(observation) + b"\n",
        "runtime_model_observation_store_conflict")
    return observation


def _record_source_raw(state_root, source_raw, now_ms):
    if not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise auth.AuthorityError("runtime_model_observation_time_invalid")
    source_doc = _source_document(source_raw)
    if source_doc["observed_at_ms"] > now_ms:
        raise auth.AuthorityError("runtime_model_observation_from_future", rc=1)
    # A low-level source document is accepted only if its exact referenced
    # provider event is still the latest real event in that session artifact.
    # This prevents callers from laundering a launch request or an older line
    # into runtime truth by merely wrapping it in the closed schema.
    _verify_latest_provenance(source_doc)
    fd = acquire_session_lock(
        state_root, source_doc["session_uuid"], exclusive=True, create=True)
    try:
        return _record_locked(state_root, source_raw, source_doc, now_ms)
    finally:
        release_session_lock(fd)


def record(state_root, source_path, now_ms):
    """Record one closed, production-adapter source document.

    The source document itself is not authority: its adapter/path/line hashes
    are re-derived from the provider artifact before any observation is stored.
    Production callers normally use :func:`collect`; this entry point exists so
    a separately scheduled repo adapter can durably submit the same closed
    source bytes and replay them idempotently.
    """
    source_raw = _regular_bytes(
        source_path, "runtime_model_source_unreadable")
    return _record_source_raw(state_root, source_raw, now_ms)


def collect(state_root, provider, session_uuid, now_ms, artifact_paths=None):
    """Collect the latest exact runtime-model event for one provider session.

    ``artifact_paths`` should be the lsof/process-bound candidate set when the
    caller has it.  With no explicit set the adapter uses provider-owned default
    locations.  More than one matching artifact is an ambiguity, never a
    newest-mtime guess.  AGY currently has an empty presence lock and opaque
    conversation blobs, so it is deliberately unsupported until a reliable
    session-bound model event exists.
    """
    if not _UUID_RE.fullmatch(str(session_uuid or "")):
        raise auth.AuthorityError("runtime_model_observation_session_invalid")
    if not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise auth.AuthorityError("runtime_model_observation_time_invalid")
    if provider == "agy":
        _artifact_error("runtime_model_collector_unsupported_agy")
    if provider not in _ADAPTER_IDS:
        _artifact_error("runtime_model_collector_provider_unsupported")
    explicit = artifact_paths is not None
    paths = _artifact_path_list(
        artifact_paths if explicit
        else _default_artifact_paths(provider, session_uuid))
    if not paths:
        _artifact_error("runtime_model_artifact_missing")
    candidates = []
    for path in paths:
        if not explicit and provider == "codex":
            try:
                if _codex_anchor_session(path) != session_uuid:
                    continue
            except auth.AuthorityError:
                # An old/unrelated rollout that cannot prove this session is
                # not allowed to poison discovery.  Once the exact anchor
                # matches, the full parser below is strictly fail-closed.
                continue
        try:
            candidates.append(_artifact_candidate(provider, path, session_uuid))
        except auth.AuthorityError as err:
            # Broad default Codex discovery necessarily sees other sessions.
            # Explicit process/lsof candidates are exact and must never hide a
            # mismatch by falling through to a different file.
            if not explicit \
                    and err.reason == "runtime_model_artifact_session_mismatch":
                continue
            raise
    if not candidates:
        _artifact_error("runtime_model_artifact_missing")
    if len(candidates) != 1:
        _artifact_error("runtime_model_artifact_ambiguous")
    source = _source_for_candidate(session_uuid, candidates[0])
    return _record_source_raw(
        state_root, auth.canonical_bytes(source) + b"\n", now_ms)


def verify(state_root, observation, session_uuid, provider, now_ms, *,
           require_fresh=True, require_observation_file=True):
    """Verify an embedded observation against both immutable stored files."""
    if not isinstance(observation, dict) or set(observation) != _OBSERVATION_KEYS \
            or observation.get("schema") != OBSERVATION_SCHEMA:
        raise auth.AuthorityError("runtime_model_observation_invalid", rc=1)
    supplied_hash = observation.get("observation_sha256")
    body = {key: value for key, value in observation.items()
            if key != "observation_sha256"}
    if not isinstance(supplied_hash, str) or not _SHA_RE.fullmatch(supplied_hash) \
            or _sha256(auth.canonical_bytes(body)) != supplied_hash:
        raise auth.AuthorityError("runtime_model_observation_hash_mismatch", rc=1)
    if observation.get("session_uuid") != session_uuid:
        raise auth.AuthorityError("runtime_model_observation_session_mismatch", rc=1)
    if observation.get("provider") != provider:
        raise auth.AuthorityError("runtime_model_observation_provider_mismatch", rc=1)
    if not isinstance(observation.get("effective_model"), str) \
            or not observation["effective_model"]:
        raise auth.AuthorityError("runtime_model_observation_invalid", rc=1)
    for key in ("observed_at_ms", "expires_at_ms", "recorded_at_ms"):
        if not isinstance(observation.get(key), int) \
                or isinstance(observation[key], bool):
            raise auth.AuthorityError("runtime_model_observation_invalid", rc=1)
    if observation["expires_at_ms"] != \
            observation["observed_at_ms"] + MAX_FRESHNESS_MS \
            or observation["recorded_at_ms"] < observation["observed_at_ms"]:
        raise auth.AuthorityError("runtime_model_observation_invalid", rc=1)
    if require_fresh and (observation["observed_at_ms"] > int(now_ms) \
                          or observation["expires_at_ms"] <= int(now_ms)):
        raise auth.AuthorityError("runtime_model_observation_stale", rc=1)

    source = observation.get("source")
    if not isinstance(source, dict) or set(source) != _SOURCE_REF_KEYS \
            or source.get("schema") != SOURCE_REF_SCHEMA \
            or source.get("kind") != "provider_session_runtime" \
            or not _SHA_RE.fullmatch(str(source.get("sha256", ""))):
        raise auth.AuthorityError("runtime_model_observation_source_invalid", rc=1)
    source_path = Path(str(source.get("path", "")))
    expected_source = _source_store(state_root) / (source["sha256"] + ".json")
    try:
        if source_path.resolve() != expected_source.resolve() \
                or source_path != expected_source:
            raise OSError("source escaped repo-owned store")
    except (OSError, RuntimeError) as exc:
        raise auth.AuthorityError(
            "runtime_model_observation_source_invalid", rc=1) from exc
    source_raw = _regular_bytes(
        source_path, "runtime_model_observation_source_unreadable")
    if _sha256(source_raw) != source["sha256"]:
        raise auth.AuthorityError(
            "runtime_model_observation_source_hash_mismatch", rc=1)
    source_doc = _source_document(source_raw)
    expected_source_fields = {
        "session_uuid": observation["session_uuid"],
        "provider": observation["provider"],
        "effective_model": observation["effective_model"],
        "observed_at_ms": observation["observed_at_ms"],
    }
    if any(source_doc.get(key) != value
           for key, value in expected_source_fields.items()):
        raise auth.AuthorityError(
            "runtime_model_observation_source_mismatch", rc=1)
    _verify_exact_provenance(source_doc)

    observation_path = Path(str(observation.get("observation_path", "")))
    expected_parent = _observation_session_dir(state_root, session_uuid)
    try:
        if observation_path.parent.resolve() != expected_parent.resolve() \
                or observation_path.parent != expected_parent:
            raise OSError("observation escaped repo-owned store")
    except (OSError, RuntimeError) as exc:
        raise auth.AuthorityError(
            "runtime_model_observation_path_invalid", rc=1) from exc
    if require_observation_file:
        observation_raw = _regular_bytes(
            observation_path, "runtime_model_observation_unreadable")
        if observation_raw != auth.canonical_bytes(observation) + b"\n":
            raise auth.AuthorityError(
                "runtime_model_observation_hash_mismatch", rc=1)
    return observation


def current_unlocked(state_root, session_uuid, provider, now_ms, *,
                     require_fresh=True):
    """Read a session head while the caller holds its shared/exclusive lock."""
    directory = _observation_session_dir(state_root, session_uuid)
    if not directory.is_dir() or directory.is_symlink():
        raise auth.AuthorityError("runtime_model_observation_missing", rc=1)
    observations = []
    for path in sorted(directory.glob("*.json")):
        raw = _regular_bytes(path, "runtime_model_observation_unreadable")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise auth.AuthorityError(
                "runtime_model_observation_invalid", rc=1) from exc
        verified = verify(
            state_root, value, session_uuid, provider, now_ms,
            require_fresh=False)
        if verified["observed_at_ms"] <= int(now_ms) \
                and (not require_fresh
                     or verified["expires_at_ms"] > int(now_ms)):
            observations.append(verified)
    if not observations:
        raise auth.AuthorityError("runtime_model_observation_stale", rc=1)
    newest = max(row["observed_at_ms"] for row in observations)
    matches = [row for row in observations if row["observed_at_ms"] == newest]
    hashes = {row["observation_sha256"] for row in matches}
    if len(hashes) != 1:
        raise auth.AuthorityError("runtime_model_observation_ambiguous", rc=1)
    selected = matches[0]
    # The observation store head must also be the artifact's current model
    # event.  Otherwise a provider-side model change could sit uncollected for
    # up to MAX_FRESHNESS_MS while a still-fresh older observation authorizes a
    # new binding.  Historical ``verify(..., require_fresh=False)`` remains
    # valid for committed receipts; only a new/current decision needs latest.
    source_raw = _regular_bytes(
        selected["source"]["path"],
        "runtime_model_observation_source_unreadable")
    _verify_latest_provenance(_source_document(source_raw))
    return selected


def current(state_root, session_uuid, provider, now_ms, *, require_fresh=True):
    """Return the unique newest observation for one provider session.

    New binding effects use the default fresh-only view.  Consumers that are
    revalidating an already committed receipt may request the newest immutable
    historical observation so expiry alone does not rewrite old evidence.
    """
    if not isinstance(provider, str) or not provider:
        raise auth.AuthorityError("runtime_model_observation_provider_invalid")
    fd = acquire_session_lock(
        state_root, session_uuid, exclusive=False, create=False)
    try:
        return current_unlocked(
            state_root, session_uuid, provider, now_ms,
            require_fresh=require_fresh)
    finally:
        release_session_lock(fd)
