# SPDX-License-Identifier: Apache-2.0
"""Fail-closed provider child-agent activity collection for the cockpit.

Only Codex currently exposes a stable, user-displayable child thread UUID.
Claude's temporary ``tasks`` directory mixes background shell commands with
agent work, while its provider-native agentId is internal and is not a UUID.
It must never be presented as one.
"""

from datetime import datetime, timezone
import glob
import json
import os
from pathlib import Path
import re
import stat


UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
LIFECYCLE = {
    "task_started": "working",
    "task_complete": "idle",
    "task_aborted": "idle",
    "turn_aborted": "idle",
    "interrupted": "idle",
}


def _epoch(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _safe_unique_rollout(root, thread_uuid):
    """Return one owned regular rollout or None; ambiguity is rejection."""
    if UUID_RE.fullmatch(str(thread_uuid or "")) is None:
        return None
    pattern = os.path.join(str(root), "**", "rollout-*-%s.jsonl" % thread_uuid)
    candidates = []
    for raw in glob.glob(pattern, recursive=True):
        path = Path(raw)
        try:
            info = path.lstat()
        except OSError:
            continue
        if (path.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & stat.S_IWOTH):
            continue
        candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def _snapshot_lines(path):
    """Read a newline-complete snapshot; partial append is fail-closed."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw or not raw.endswith(b"\n"):
        return None
    try:
        return raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _json_lines(path):
    lines = _snapshot_lines(path)
    if lines is None:
        return None
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        records.append(record)
    return records


def _session_meta(records):
    if not records or records[0].get("type") != "session_meta":
        return None
    payload = records[0].get("payload")
    return payload if isinstance(payload, dict) else None


def _parent_candidates(records):
    found = {}
    rejected = set()
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if (record.get("type") != "event_msg"
                or payload.get("type") != "sub_agent_activity"):
            continue
        child = str(payload.get("agent_thread_id") or "")
        agent_path = str(payload.get("agent_path") or "")
        if UUID_RE.fullmatch(child) is None or not agent_path.startswith("/"):
            continue
        previous = found.get(child)
        if previous is not None and previous != agent_path:
            rejected.add(child)
            continue
        found[child] = agent_path
    for child in rejected:
        found.pop(child, None)
    return found


def _verified_child(records, child_uuid, parent_uuid, expected_path, now):
    meta = _session_meta(records)
    if meta is None:
        return None
    source = meta.get("source")
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return None
    spawn = subagent.get("thread_spawn")
    if not isinstance(spawn, dict):
        return None
    if (meta.get("id") != child_uuid
            or meta.get("parent_thread_id") != parent_uuid
            or meta.get("thread_source") != "subagent"
            or meta.get("agent_path") != expected_path
            or spawn.get("parent_thread_id") != parent_uuid
            or spawn.get("agent_path") != expected_path):
        return None

    last_epoch = None
    last_lifecycle = None
    for record in records:
        observed = _epoch(record.get("timestamp"))
        if observed is not None:
            last_epoch = observed
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type") if record.get("type") == "event_msg" else None
        if kind in LIFECYCLE:
            last_lifecycle = LIFECYCLE[kind]
    if last_epoch is None or last_lifecycle is None:
        return None
    age_minutes = int(max(0, now - last_epoch) // 60)
    activity = "offline" if age_minutes >= 60 else last_lifecycle
    return {
        "uuid": child_uuid,
        "age_minutes": age_minutes,
        "activity": activity,
    }


def collect_codex_subagents(parent_uuid, sessions_root, now=None):
    """Return verified direct Codex children without names/prompts/content."""
    if UUID_RE.fullmatch(str(parent_uuid or "")) is None:
        return []
    try:
        now = float(now) if now is not None else datetime.now(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return []
    root = Path(sessions_root)
    parent_path = _safe_unique_rollout(root, parent_uuid)
    if parent_path is None:
        return []
    parent_records = _json_lines(parent_path)
    parent_meta = _session_meta(parent_records)
    if parent_meta is None or parent_meta.get("id") != parent_uuid:
        return []

    children = []
    for child_uuid, expected_path in _parent_candidates(parent_records).items():
        child_path = _safe_unique_rollout(root, child_uuid)
        if child_path is None:
            continue
        records = _json_lines(child_path)
        if records is None:
            continue
        child = _verified_child(
            records, child_uuid, parent_uuid, expected_path, now,
        )
        if child is not None:
            children.append(child)
    rank = {"working": 0, "idle": 1, "offline": 2}
    children.sort(key=lambda child: (
        rank.get(child["activity"], 9), child["age_minutes"], child["uuid"],
    ))
    return children
