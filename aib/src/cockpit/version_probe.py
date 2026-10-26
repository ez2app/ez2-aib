#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Small standalone probe, also sent over explicitly requested, verified SSH."""
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import time
from pathlib import Path

VER = re.compile(r"\d+\.\d+\.\d+(?:[-+.][A-Za-z0-9.-]+)?\Z")


def version(value):
    return value if isinstance(value, str) and VER.fullmatch(value) else None


def load(path):
    try:
        path = Path(path)
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            return None
        with path.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return None
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def run(argv):
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=5, env=env)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def collect(home=None, state_root=None, launcher=None):
    home = Path(home) if home is not None else Path.home()
    state = Path(state_root) if state_root is not None else Path(
        os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or home / ".ez2-aib")
    cmd = Path(launcher) if launcher is not None else home / ".local/bin/aib"
    if not cmd.exists() and launcher is None:
        found = shutil.which("aib")
        if found:
            cmd = Path(found)
    core = dict(installed_version=None, source="unmeasured", git_sha=None)
    resolved = cmd.resolve()
    root = resolved.parent.parent
    try:
        text = (root / "aib/VERSION").read_text(encoding="utf-8").strip()
        core["installed_version"] = version(text)
        if core["installed_version"]:
            core["source"] = "installed-version-file"
    except (OSError, UnicodeError):
        pass
    if not core["installed_version"] and cmd.is_file():
        shown = run([str(cmd), "--version"])
        match = re.fullmatch(r"Ez2 AIB v(\S+)", shown or "")
        if match:
            core["installed_version"] = version(match.group(1))
            core["source"] = "installed-cli" if core["installed_version"] else "unmeasured"
    if (root / ".git").exists():
        sha = run(["git", "-C", str(root), "rev-parse", "HEAD"])
        if sha and re.fullmatch(r"[0-9a-f]{40}", sha):
            core["git_sha"] = sha
    plugins = {}
    try:
        paths = sorted((state / "plugins").glob("*/aib-ext.json"))[:200]
    except OSError:
        paths = []
    for path in paths:
        m = load(path)
        if m and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", path.parent.name):
            plugins[path.parent.name] = dict(installed_version=version(m.get("version")),
                                            source="installed-manifest")
    room = dict(state="not-recorded", reported_start_version=None, source="unmeasured",
                installed_version=plugins.get("captain-room", {}).get("installed_version"),
                versions_match=None, health="unmeasured", runtime_core_version=None)
    srv = load(state / "state/captain-room/server.json")
    if srv:
        pid = srv.get("pid")
        script = state / "plugins/captain-room/captain_room.py"
        # A live PID alone is not the recorded server: it may have been reused.
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            room["state"] = "invalid-record"
        else:
            text = run(["ps", "-p", str(pid), "-o", "command="])
            if text is None:
                room["state"] = "stopped-or-unreadable"
            else:
                try:
                    argv = shlex.split(text)
                except ValueError:
                    argv = []
                identity = False
                if str(script) in argv:
                    pos = argv.index(str(script))
                    interpreter = bool(argv and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(argv[0]).name, re.I))
                    identity = pos + 1 < len(argv) and argv[pos + 1] == "serve" and (
                        pos == 0 or (interpreter and all(a in ("-u", "-B", "-I") for a in argv[1:pos])))
                if not identity:
                    room["state"] = "stale-record"
                else:
                    room.update(state="process-verified", source="pid-verified-startup-record",
                                reported_start_version=version(srv.get("room_version")))
                    a, b = room["reported_start_version"], room["installed_version"]
                    room["versions_match"] = a == b if a and b else None
    return dict(schema="aib.version-probe/v1", observed_at=int(time.time()),
                core=core, plugins=plugins, room=room)


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
