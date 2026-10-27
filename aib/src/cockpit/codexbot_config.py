#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Persist and verify the one-time Codex desktop bridge binding."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from codexbot_bridge import BridgeError, validate_target
from codexbot_server import ServerError, ensure_server


MANAGED_KEYS = {
    "AIB_CODEXBOT_THREAD_ID",
    "AIB_CODEXAPP_THREAD_ID",
    "CODEX_THREAD_ID",
    "AIB_CODEXBOT_ENDPOINT",
    "AIB_CODEXAPP_ENDPOINT",
}


def _preserved_lines(path: Path) -> list[str]:
    if not path.exists():
        return ["# CodexBot desktop bridge binding"]
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        if key not in MANAGED_KEYS:
            kept.append(line)
    return kept


def _write_binding(path: Path, thread_id: str, endpoint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = _preserved_lines(path)
    if lines and lines[-1]:
        lines.append("")
    lines.extend([
        "# Codex Desktop binding; managed by `aib bot connect codexbot`.",
        f"AIB_CODEXBOT_THREAD_ID={thread_id}",
        f"AIB_CODEXBOT_ENDPOINT={endpoint}",
    ])
    fd, temporary = tempfile.mkstemp(prefix=".codexbot.env.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aib bot connect codexbot")
    parser.add_argument("--thread", required=True, help="loaded Codex desktop thread UUID")
    parser.add_argument("--endpoint", default="", help="local App Server endpoint; omitted = AIB managed Unix socket")
    parser.add_argument("--file", default=os.environ.get("AIB_CODEXBOT_SECRETS_FILE", ""))
    args = parser.parse_args(argv)
    target = Path(args.file).expanduser() if args.file else Path.home() / ".ez2-aib" / "secrets" / "codexbot.env"
    try:
        endpoint = args.endpoint or ensure_server()
        validate_target(args.thread, endpoint)
    except (BridgeError, ServerError) as exc:
        print(f"aib bot connect: {exc}", file=sys.stderr)
        return 2

    bridge = Path(__file__).with_name("codexbot_bridge.py")
    checked = subprocess.run(
        [sys.executable, str(bridge), "--thread-id", args.thread,
         "--endpoint", endpoint, "--check"],
        text=True, capture_output=True,
    )
    if checked.returncode != 0:
        detail = checked.stderr.strip().splitlines()
        print("aib bot connect: Codex 桌面連線檢查失敗；未寫入設定。", file=sys.stderr)
        if detail:
            print(detail[-1], file=sys.stderr)
        return checked.returncode or 2

    _write_binding(target, args.thread, endpoint)
    print(f"DotsBot 已連結：thread={args.thread}")
    print(f"設定已寫入 {target}；現在可由右鍵另開 AI -> DotsBot。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
