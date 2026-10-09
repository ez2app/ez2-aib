# SPDX-License-Identifier: Apache-2.0
"""Truth-source progress calculation for the cockpit status line.

Task cards are a live scheduling projection.  They are useful for counting
work in progress, but missing cards and vocabulary drift must not change the
project denominator or manufacture completion.  The denominator is approved
outline leaves; delivery comes from explicit contract state or a conventional
Git subject naming the node.
"""

import glob
import json
import re
import subprocess
from pathlib import Path


NODE_IN_SUBJECT = re.compile(
    r"^(?:feat|fix|chore|refactor|test|docs)(?:\([^)]*\))?:.*?"
    r"\b([A-Z][A-Z0-9]{0,3}\.\d+(?:\.\d+)*)\b",
    re.MULTILINE,
)


def progress_snapshot(items, contracts=None, git_subjects="", tasks=None):
    contracts = contracts or {}
    tasks = tasks or {}
    leaves = [item for item in items if item.get("leaf")]
    approved = {item["id"] for item in leaves
                if item.get("approval") == "approved"}
    drafts = sum(1 for item in leaves if item.get("approval") == "draft")

    delivered = {node for node, state in contracts.items()
                 if state in ("done", "verified")}
    verified = {node for node, state in contracts.items()
                if state == "verified"}
    delivered.update(NODE_IN_SUBJECT.findall(git_subjects or ""))

    running_states = {"claimed", "in_progress", "draft-ready", "draft_ready"}
    running = sum(1 for node, state in tasks.items()
                  if node in approved and state in running_states)
    return {
        "delivered": len(delivered & approved),
        "verified": len(verified & approved),
        "running": running,
        "drafts": drafts,
        "total": len(approved),
    }


def load_contracts(repo):
    states = {}
    for name in glob.glob(str(Path(repo) / "acceptance" / "*.json")):
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            node = data.get("node_id")
            state = (data.get("status") or {}).get("state")
            if node and state:
                states[node] = state
        except (OSError, ValueError, TypeError):
            continue
    return states


def load_git_subjects(repo, limit=400):
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "log", "--format=%s", "-%d" % limit],
            capture_output=True, text=True, timeout=5,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def load_tasks(tasks_dir):
    tasks = {}
    for name in glob.glob(str(Path(tasks_dir) / "*.json")) if tasks_dir else []:
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            node = data.get("outline")
            if node:
                tasks[node] = data.get("status")
        except (OSError, ValueError, TypeError):
            continue
    return tasks
