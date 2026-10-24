#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared four-level notification policy for AIB.

This module deliberately contains no desktop, tmux, ledger, or live-inbox
side effects.  Classification and action planning are pure; callers inject a
path only when they explicitly load rules, persist switches, append the small
policy audit record, or count an inbox projection.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path


LEVELS = ("red", "yellow", "green", "blue")
RULES_PATH = Path(__file__).with_name("notify-rules.json")
_MAX_JSON_BYTES = 1024 * 1024
_AI_KINDS = {"ai", "agent", "main", "sub_agent", "sub-agent"}
_RULE_FIELDS = {"always", "signal_type", "kind", "actor_kind", "text",
                "needs_action"}
_RULE_MATCHES = {"exact", "regex", "truthy"}


class RulesError(RuntimeError):
    """The notification rules are unsafe or violate their schema."""


class StateError(RuntimeError):
    """Notification state or audit storage failed closed."""


def _strict_object(error_type, label):
    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise error_type("%s has duplicate JSON key: %s" % (label, key))
            result[key] = value
        return result
    return hook


def _open_owned_regular(path, error_type, label, *, exact_mode=None,
                        forbid_write_bits=False):
    """Open once, then validate and read that same descriptor.

    O_NONBLOCK prevents a caller-selected FIFO from hanging before the regular
    file check.  O_NOFOLLOW keeps a symlink from being exchanged between a
    pathname check and open.
    """
    if not hasattr(os, "O_NOFOLLOW"):
        raise error_type("%s loader requires O_NOFOLLOW support" % label)
    flags = (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as err:
        raise error_type("%s is unreadable: %s (%s)" % (label, path, err))
    try:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISREG(info.st_mode):
            raise error_type("%s must be a regular non-symlink file: %s" %
                             (label, path))
        if info.st_uid != os.getuid():
            raise error_type("%s owner does not match the current user: %s" %
                             (label, path))
        if exact_mode is not None and mode != exact_mode:
            raise error_type("%s mode must be %04o: %s" %
                             (label, exact_mode, path))
        if forbid_write_bits and mode & 0o022:
            raise error_type("%s must not be group/world writable: %s" %
                             (label, path))
        if info.st_nlink != 1:
            raise error_type("%s must have exactly one filesystem link: %s" %
                             (label, path))
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_json_descriptor(descriptor, path, error_type, label):
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            raw = handle.read(_MAX_JSON_BYTES + 1)
        if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
            raise error_type("%s exceeds %d bytes: %s" %
                             (label, _MAX_JSON_BYTES, path))
        return json.loads(raw, object_pairs_hook=_strict_object(error_type, label))
    except error_type:
        raise
    except (OSError, UnicodeError, ValueError) as err:
        raise error_type("%s is not valid UTF-8 JSON: %s (%s)" %
                         (label, path, err))
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_rules(data):
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "level_order", "rules", "actions"}:
        raise RulesError("notification rules have an invalid top-level shape")
    if data["schema_version"] != "1":
        raise RulesError("unsupported notification rules schema: %r" %
                         data["schema_version"])
    if (not isinstance(data["level_order"], list)
            or tuple(data["level_order"]) != LEVELS):
        raise RulesError("notification level_order must be red,yellow,green,blue")

    rules = data["rules"]
    if not isinstance(rules, list) or not rules:
        raise RulesError("notification rules list must be non-empty")
    rule_ids = set()
    levels_seen = set()
    for index, rule in enumerate(rules):
        where = "rules[%d]" % index
        if not isinstance(rule, dict) or set(rule) != {
                "id", "level", "field", "match", "values"}:
            raise RulesError("%s has an invalid shape" % where)
        rule_id = rule["id"]
        if (not _is_nonempty_string(rule_id)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", rule_id)
                or rule_id in rule_ids):
            raise RulesError("%s has an invalid or duplicate id" % where)
        rule_ids.add(rule_id)
        if rule["level"] not in LEVELS:
            raise RulesError("%s has an invalid level" % where)
        levels_seen.add(rule["level"])
        if rule["field"] not in _RULE_FIELDS:
            raise RulesError("%s has an invalid field" % where)
        if rule["match"] not in _RULE_MATCHES:
            raise RulesError("%s has an invalid match operator" % where)
        values = rule["values"]
        if (not isinstance(values, list)
                or any(not _is_nonempty_string(value) for value in values)
                or len(values) != len(set(values))):
            raise RulesError("%s values must be unique non-empty strings" % where)
        if rule["match"] in {"exact", "regex"} and not values:
            raise RulesError("%s requires at least one match value" % where)
        if rule["match"] == "truthy" and values:
            raise RulesError("%s truthy rules must not have values" % where)
        if rule["match"] == "regex":
            for pattern in values:
                try:
                    re.compile(pattern, re.IGNORECASE)
                except re.error as err:
                    raise RulesError("%s has invalid regex %r (%s)" %
                                     (where, pattern, err))
    if levels_seen != set(LEVELS):
        raise RulesError("notification rules must cover all four levels")

    actions = data["actions"]
    if not isinstance(actions, dict) or set(actions) != set(LEVELS):
        raise RulesError("notification actions must define all four levels")
    for level in LEVELS:
        action = actions[level]
        if not isinstance(action, dict) or set(action) != {
                "system_notification", "sound", "flash", "duration_ms",
                "flash_background", "flash_foreground"}:
            raise RulesError("notification action has invalid shape: %s" % level)
        if type(action["system_notification"]) is not bool:
            raise RulesError("system_notification must be boolean: %s" % level)
        if action["sound"] is not None and not _is_nonempty_string(action["sound"]):
            raise RulesError("sound must be null or a non-empty string: %s" % level)
        if type(action["flash"]) is not bool:
            raise RulesError("flash must be boolean: %s" % level)
        duration = action["duration_ms"]
        if type(duration) is not int or duration < 0 or duration > 60000:
            raise RulesError("duration_ms is invalid: %s" % level)
        for colour_key in ("flash_background", "flash_foreground"):
            colour = action[colour_key]
            if action["flash"]:
                if (isinstance(colour, bool) or not isinstance(colour, int)
                        or colour < 0 or colour > 255):
                    raise RulesError("%s must be an xterm colour: %s" %
                                     (colour_key, level))
            elif colour is not None:
                raise RulesError("%s must be null when flash is disabled: %s" %
                                 (colour_key, level))
    return data


def _pinned_rescue_bytes(path, error_type, label):
    """同 theme 的活路:只救「位元組==release 對釘」的 packaged 檔。"""
    import sys as _sys
    _src = str(Path(__file__).resolve().parents[1])
    if _src not in _sys.path:
        _sys.path.insert(0, _src)
    from dataguard.dataguard import load_verified_bytes
    repo_root = Path(__file__).resolve().parents[2]
    try:
        rel = str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        rel = "__custom__"
    data_bytes, _receipt = load_verified_bytes(
        path, pin_key=rel, error_type=error_type, label=label, force_pin=True)
    return data_bytes


def load_rules(path=RULES_PATH):
    """Load a strict, owner-controlled rules file without following symlinks."""
    path = Path(path)
    try:
        descriptor = _open_owned_regular(path, RulesError, "notification rules",
                                         forbid_write_bits=True)
    except RulesError as err:
        if "group/world writable" not in str(err):
            raise
        raw = _pinned_rescue_bytes(path, RulesError, "notification rules")
        try:
            data = json.loads(raw.decode("utf-8"),
                              object_pairs_hook=_strict_object(
                                  RulesError, "notification rules"))
        except (UnicodeError, ValueError) as err2:
            raise RulesError("notification rules is not valid UTF-8 JSON: "
                             "%s (%s)" % (path, err2))
        return _validate_rules(data)
    data = _read_json_descriptor(descriptor, path, RulesError,
                                 "notification rules")
    return _validate_rules(data)


def _text_parts(record):
    parts = []

    def add(value):
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                if isinstance(child, str):
                    parts.append(child)

    add(record.get("text"))
    add(record.get("subject"))
    body = record.get("body")
    if isinstance(body, dict):
        add(body.get("summary"))
        add(body.get("subject"))
        add(body.get("text"))
        add(body.get("message"))
    return "\n".join(parts)


def _actor_kind(record):
    actor = record.get("actor")
    value = actor.get("kind") if isinstance(actor, dict) else None
    value = value or record.get("sender_kind") or ""
    return str(value).strip().lower().replace("-", "_")


def _signal_type(record):
    value = record.get("signal_type") or record.get("type") or ""
    return str(value).strip().lower()


def _truthy(value):
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return False


def _facts(record):
    return {
        "always": True,
        "signal_type": _signal_type(record),
        "kind": str(record.get("kind") or "").strip().lower(),
        "actor_kind": _actor_kind(record),
        "text": _text_parts(record),
        "needs_action": _truthy(record.get("needs_action"))
                        or _truthy(record.get("requires_action")),
    }


def _rule_matches(rule, facts):
    value = facts[rule["field"]]
    operation = rule["match"]
    if operation == "truthy":
        return bool(value)
    if operation == "exact":
        token = str(value).casefold()
        return any(token == candidate.casefold() for candidate in rule["values"])
    text = str(value)
    return any(re.search(pattern, text, re.IGNORECASE)
               for pattern in rule["values"])


def _is_ai_message(record, facts):
    actor_kind = facts["actor_kind"]
    if actor_kind not in {kind.replace("-", "_") for kind in _AI_KINDS}:
        return False
    kind = facts["kind"]
    message_shape = (kind.startswith("message.") or "recipient" in record
                     or "sender_kind" in record)
    if not message_shape:
        return False
    recipient = record.get("recipient")
    if isinstance(recipient, dict) and recipient.get("kind"):
        recipient_kind = str(recipient["kind"]).strip().lower().replace("-", "_")
        return recipient_kind in {kind.replace("-", "_") for kind in _AI_KINDS}
    return True


def _requested_level(record):
    value = record.get("requested_level")
    if value is None:
        value = record.get("notify_level")
    return value.strip().lower() if isinstance(value, str) else value


def _escalation_reason(record):
    value = record.get("escalation_reason")
    if value is None:
        value = record.get("notify_reason")
    return value


def classify(record, rules=None):
    """Classify one structured record using red > yellow > green > blue.

    Routine AI-to-AI messages are locked blue.  The only escape is an explicit
    red/yellow request with a non-blank escalation_reason.  The returned audit
    fields tell the caller when to append a policy audit record; the classifier
    itself remains side-effect free.
    """
    if not isinstance(record, dict):
        raise RulesError("notification record must be an object")
    rules = load_rules() if rules is None else _validate_rules(rules)
    facts = _facts(record)
    matches = [rule for rule in rules["rules"] if _rule_matches(rule, facts)]
    rank = {level: index for index, level in enumerate(LEVELS)}
    detected = min((rule["level"] for rule in matches), key=rank.get,
                   default="blue")
    requested = _requested_level(record)
    valid_request = requested in LEVELS
    reason_codes = [rule["id"] for rule in matches]
    audit_required = False
    audit_code = None

    if _is_ai_message(record, facts):
        if requested in {"red", "yellow"}:
            audit_required = True
            if not _is_nonempty_string(_escalation_reason(record)):
                return {
                    "level": "blue",
                    "reason_codes": ["ai_routine_blue_lock"],
                    "rule_ids": ["blue.default"],
                    "audit_required": True,
                    "audit_code": "missing_escalation_reason",
                    "requested_level": requested,
                }
            audit_code = "ai_structured_escalation"
            level = min((detected, requested), key=rank.get)
            reason_codes.append("ai_structured_escalation")
        else:
            if requested is not None and not valid_request:
                audit_required = True
                audit_code = "invalid_requested_level"
            return {
                "level": "blue",
                "reason_codes": ["ai_routine_blue_lock"],
                "rule_ids": ["blue.default"],
                "audit_required": audit_required,
                "audit_code": audit_code,
                "requested_level": requested,
            }
    else:
        level = detected
        if valid_request:
            level = min((level, requested), key=rank.get)
            reason_codes.append("structured_level_request")
        elif requested is not None:
            audit_required = True
            audit_code = "invalid_requested_level"

    ordered_codes = []
    for code in reason_codes:
        if code not in ordered_codes:
            ordered_codes.append(code)
    return {
        "level": level,
        "reason_codes": ordered_codes,
        "rule_ids": [rule["id"] for rule in matches],
        "audit_required": audit_required,
        "audit_code": audit_code,
        "requested_level": requested,
    }


def default_state():
    return {
        "schema_version": "1",
        "enabled": True,
        "levels": {level: True for level in LEVELS},
    }


def _validate_state(data):
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "enabled", "levels"}:
        raise StateError("notification state has an invalid top-level shape")
    if data["schema_version"] != "1" or type(data["enabled"]) is not bool:
        raise StateError("notification state has an invalid schema or total switch")
    levels = data["levels"]
    if (not isinstance(levels, dict) or set(levels) != set(LEVELS)
            or any(type(levels[level]) is not bool for level in LEVELS)):
        raise StateError("notification state must contain four boolean level switches")
    return {
        "schema_version": "1",
        "enabled": data["enabled"],
        "levels": {level: levels[level] for level in LEVELS},
    }


def _write_state_atomic(path, state):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as err:
        raise StateError("notification state directory is unavailable: %s" % err)
    descriptor = -1
    temp_path = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
        temp_path = Path(temp_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(state, handle, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
        temp_path = None
        try:
            directory = os.open(str(path.parent), os.O_RDONLY
                                | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # The file is already atomically replaced.  Some filesystems do
            # not permit directory fsync; do not rewrite or weaken its mode.
            pass
    except OSError as err:
        raise StateError("notification state write failed: %s (%s)" % (path, err))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


class StateStore:
    def __init__(self, path):
        self.path = Path(path)

    def show(self):
        try:
            descriptor = _open_owned_regular(
                self.path, StateError, "notification state", exact_mode=0o600)
        except StateError:
            try:
                exists = self.path.lexists()
            except AttributeError:
                exists = os.path.lexists(str(self.path))
            if not exists:
                return default_state()
            raise
        data = _read_json_descriptor(descriptor, self.path, StateError,
                                     "notification state")
        return _validate_state(data)

    def toggle(self, level=None):
        state = self.show()
        if level is None:
            state["enabled"] = not state["enabled"]
        else:
            if level not in LEVELS:
                raise StateError("unknown notification level: %s" % level)
            state["levels"][level] = not state["levels"][level]
        # Recheck an existing pathname immediately before replacement.  A
        # symlink or unsafe file is never silently normalized by a toggle.
        if os.path.lexists(str(self.path)):
            self.show()
        _write_state_atomic(self.path, state)
        return state


def plan_actions(classification, state, rules=None):
    if not isinstance(classification, dict):
        raise StateError("classification must be an object")
    level = classification.get("level")
    if level not in LEVELS:
        raise StateError("classification has an unknown notification level")
    state = _validate_state(state)
    rules = load_rules() if rules is None else _validate_rules(rules)
    enabled = state["enabled"] and state["levels"][level]
    action = dict(rules["actions"][level])
    if not enabled:
        action.update({
            "system_notification": False,
            "sound": None,
            "flash": False,
            "duration_ms": 0,
        })
    return {"level": level, "enabled": enabled, **action}


def _audit_descriptor(path):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as err:
        raise StateError("notification audit directory is unavailable: %s" % err)
    flags = (os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))
    try:
        descriptor = os.open(str(path), flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            descriptor = os.open(str(path), flags)
        except OSError as err:
            raise StateError("notification audit is unavailable: %s (%s)" %
                             (path, err))
    except OSError as err:
        raise StateError("notification audit is unavailable: %s (%s)" %
                         (path, err))
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise StateError("notification audit must be an owner-only regular file")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def append_audit(path, record, classification, now_ms=None):
    """Append a metadata-only audit line; raw subject/body text is never kept."""
    if not isinstance(record, dict) or not isinstance(classification, dict):
        raise StateError("notification audit inputs must be objects")
    actor = record.get("actor") if isinstance(record.get("actor"), dict) else {}
    reason = _escalation_reason(record)
    reason_hash = (hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()
                   if _is_nonempty_string(reason) else None)
    item = {
        "schema_version": "1",
        "timestamp_ms": int(time.time() * 1000) if now_ms is None else int(now_ms),
        "message_id": record.get("message_id") or record.get("id"),
        "actor_ai_id": actor.get("ai_id") or record.get("sender_ai_id"),
        "actor_kind": _actor_kind(record) or None,
        "requested_level": _requested_level(record),
        "resolved_level": classification.get("level"),
        "audit_code": classification.get("audit_code"),
        "reason_codes": list(classification.get("reason_codes") or []),
        "escalation_reason_present": _is_nonempty_string(_escalation_reason(record)),
        "escalation_reason_sha256": reason_hash,
    }
    raw = (json.dumps(item, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = _audit_descriptor(path)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("zero-byte audit append")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as err:
        raise StateError("notification audit append failed: %s (%s)" % (path, err))
    finally:
        os.close(descriptor)
    return item


def audit_report(path):
    """Summarize escalation accounting without exposing message/reason text."""
    path = Path(path)
    if not os.path.lexists(str(path)):
        return {"schema_version": "1", "total": 0, "actors": []}
    descriptor = _open_owned_regular(
        path, StateError, "notification audit", exact_mode=0o600)
    actors = {}
    total = 0
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            for line_no, raw in enumerate(handle, 1):
                if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
                    raise StateError("notification audit line is too large: %d" %
                                     line_no)
                try:
                    item = json.loads(
                        raw, object_pairs_hook=_strict_object(
                            StateError, "notification audit line"))
                except (UnicodeError, ValueError) as err:
                    raise StateError("notification audit line %d is invalid: %s" %
                                     (line_no, err))
                if not isinstance(item, dict):
                    raise StateError("notification audit line %d is not an object" %
                                     line_no)
                actor = item.get("actor_ai_id") or "<unknown>"
                if not isinstance(actor, str):
                    raise StateError("notification audit actor is invalid: %d" %
                                     line_no)
                level = item.get("resolved_level")
                if level not in LEVELS:
                    raise StateError("notification audit level is invalid: %d" %
                                     line_no)
                entry = actors.setdefault(actor, {
                    "actor_ai_id": actor, "total": 0, "red": 0,
                    "yellow": 0, "green": 0, "blue": 0,
                    "policy_violations": 0,
                })
                entry["total"] += 1
                entry[level] += 1
                if item.get("audit_code") in {
                        "missing_escalation_reason", "invalid_requested_level"}:
                    entry["policy_violations"] += 1
                total += 1
    except StateError:
        raise
    except (OSError, UnicodeError) as err:
        raise StateError("notification audit read failed: %s (%s)" % (path, err))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    rows = sorted(actors.values(), key=lambda item: (
        -item["total"], item["actor_ai_id"]))
    return {"schema_version": "1", "total": total, "actors": rows}


def _read_frontmatter(path):
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    flags = (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            raw = handle.read(_MAX_JSON_BYTES + 1)
    except (OSError, UnicodeError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
        return None
    lines = raw.splitlines()
    if not lines or lines[0].lstrip("\ufeff") != "---":
        return None
    result = {}
    closed = False
    for line in lines[1:]:
        if line == "---":
            closed = True
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in result:
            return None
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result if closed else None


def count_inbox(path, rules=None):
    """Count unread Markdown frontmatter by the shared classifier."""
    rules = load_rules() if rules is None else _validate_rules(rules)
    counts = {level: 0 for level in LEVELS}
    root = Path(path)
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return counts
    for entry in entries:
        if entry.suffix != ".md":
            continue
        frontmatter = _read_frontmatter(entry)
        if not frontmatter or frontmatter.get("status", "").casefold() != "unread":
            continue
        record = {
            "kind": "message.inbound",
            "message_id": frontmatter.get("id") or entry.name,
            "actor": {
                "kind": frontmatter.get("sender_kind", "system"),
                "ai_id": frontmatter.get("sender_ai_id"),
            },
            "text": frontmatter.get("subject", ""),
        }
        if "notify_level" in frontmatter:
            record["requested_level"] = frontmatter["notify_level"]
        if "notify_reason" in frontmatter:
            record["escalation_reason"] = frontmatter["notify_reason"]
        level = classify(record, rules)["level"]
        counts[level] += 1
    return counts


def _main(argv):
    parser = argparse.ArgumentParser(description="AIB shared notification policy")
    sub = parser.add_subparsers(dest="command", required=True)
    count = sub.add_parser("count-inbox")
    # ★ 2026-08-21 舊載體廢止(工單 §二·A·4 裁定):
    #   原本 default=舊載體收件匣。裁定是「拿掉 default 改必填」,
    #   而不是換一個新的預設值——**預設值就是這顆病的形狀**:它讓
    #   「呼叫端忘了給路徑」看起來像成功(改動前實測:不給路徑
    #   rc=0 且回 {"red":0,"yellow":0,"green":0,"blue":0},與「真的沒有未讀」
    #   逐字相同)。同族於 count_inbox 本身:不存在的路徑與空目錄也回同一個值。
    count.add_argument("path")
    count.add_argument("--rules", default=str(RULES_PATH))
    classify_cmd = sub.add_parser("classify")
    classify_cmd.add_argument("--rules", default=str(RULES_PATH))
    audit_cmd = sub.add_parser("audit-report")
    audit_cmd.add_argument("path")
    for command in ("show-state", "toggle-state"):
        state_cmd = sub.add_parser(command)
        state_cmd.add_argument("path", nargs="?")
        state_cmd.add_argument("level", nargs="?", choices=LEVELS)
        state_cmd.add_argument("--state", dest="state_path")
        state_cmd.add_argument("--level", dest="level_option", choices=LEVELS)
    args = parser.parse_args(argv)
    try:
        if args.command == "count-inbox":
            result = count_inbox(args.path, load_rules(args.rules))
        elif args.command == "classify":
            result = classify(json.load(sys.stdin), load_rules(args.rules))
        elif args.command == "audit-report":
            result = audit_report(args.path)
        else:
            state_path = args.state_path or args.path
            if not state_path:
                raise StateError("%s requires a state path" % args.command)
            if args.state_path and args.path:
                raise StateError("state path must be supplied exactly once")
            if args.level and args.level_option:
                raise StateError("notification level must be supplied exactly once")
            level = args.level_option or args.level
            if args.command == "show-state" and level is not None:
                raise StateError("show-state does not accept a notification level")
            store = StateStore(state_path)
            result = (store.show() if args.command == "show-state"
                      else store.toggle(level))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")))
    except (RulesError, StateError, OSError, ValueError) as err:
        print(str(err), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
