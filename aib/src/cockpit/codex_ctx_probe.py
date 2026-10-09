# SPDX-License-Identifier: Apache-2.0
"""Refresh Codex gauges even when the native CLI does not run a statusline hook.

Use the shipped Codex adapter for identity checks and usage semantics. Never
substitute the seat's requested model for an observed turn_context model.
"""
import json
import os
from pathlib import Path
import re
import subprocess

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_ADAPTER = Path(__file__).resolve().parents[2] / "deploy/statusline/codex/statusline-ez2-ctx.js"


def refresh(uuid, *, ctx_dir=None, codex_home=None):
    if not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
        return None
    dest = Path(ctx_dir or os.environ.get("EZ2_CTX_DIR") or Path.home() / ".ez2/ctx")
    if not dest.is_absolute():
        return None
    sessions = (os.environ.get("EZ2_CODEX_SESSIONS_DIR")
                or os.environ.get("EZ2_CODEX_SESSIONS_ROOT"))
    home = Path(codex_home or (str(Path(sessions).parent) if sessions else "")
                or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    # The existing adapter expects the native <codex-home>/sessions layout.
    if not home.is_absolute() or (not codex_home and sessions
                                 and Path(sessions) != home / "sessions"):
        return None
    try:
        result = subprocess.run(
            ["node", str(_ADAPTER), "--probe", "--json", "--session", uuid,
             "--codex-home", str(home), "--out-dir", str(dest)],
            capture_output=True, text=True, timeout=1.0, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        record = json.loads(result.stdout)
        return record.get("model") if record.get("session_id") == uuid else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
