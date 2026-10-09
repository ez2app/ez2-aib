#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""check_task_trailer — parse commit `Task:` trailer (§10.17 / §108.324).

Exit 0 when message has a valid Task trailer; non-zero otherwise.
`--warn-only` always exits 0 but prints a warning on missing Task.

Only formal uids accepted: `t.[A-Za-z0-9._-]+`
§ / # aliases (`Task: §108.323`, `Task: #323`) are REJECTED.

Does NOT rewrite history / amend. Check-only.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import Iterable

_TASK_LINE = re.compile(
    r"^Task:\s*(?P<val>\S.*?)\s*$",
    re.MULTILINE,
)
# Formal uid only — no § / # aliases
_VALID_VAL = re.compile(r"^t\.[A-Za-z0-9._-]+$")


def parse_task_trailers(message: str) -> list[str]:
    """Return list of Task trailer values (may be empty)."""
    if not message:
        return []
    out: list[str] = []
    for m in _TASK_LINE.finditer(message):
        val = m.group("val").strip()
        if val:
            out.append(val)
    return out


def is_valid_task_value(val: str) -> bool:
    return bool(_VALID_VAL.match(val.strip()))


def has_valid_task(message: str) -> bool:
    return any(is_valid_task_value(v) for v in parse_task_trailers(message))


def message_from_rev(rev: str) -> str:
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%B", rev],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git log failed for {rev!r}")
    return proc.stdout


def _read_stdin() -> str:
    return sys.stdin.read()


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Check commit message for valid Task: trailer (§108.324)."
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--message",
        "-m",
        help="Commit message text to check",
    )
    src.add_argument(
        "--rev",
        help="git rev; read message via `git log -1 --format=%%B <rev>`",
    )
    src.add_argument(
        "--stdin",
        action="store_true",
        help="Read commit message from stdin",
    )
    p.add_argument(
        "--warn-only",
        action="store_true",
        help="Always exit 0; print warning when Task missing/invalid",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    if args.message is not None:
        message = args.message
    elif args.rev is not None:
        try:
            message = message_from_rev(args.rev)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 0 if args.warn_only else 2
    elif args.stdin or not sys.stdin.isatty():
        message = _read_stdin()
    else:
        p.error("provide --message, --rev, --stdin, or pipe a message on stdin")

    tasks = parse_task_trailers(message)
    valid = [t for t in tasks if is_valid_task_value(t)]
    if valid:
        print(f"ok: Task {', '.join(valid)}")
        return 0

    if tasks:
        msg = (
            f"invalid Task trailer(s): {', '.join(tasks)} "
            "(only formal uid t.[A-Za-z0-9._-]+ accepted; §/# aliases rejected)"
        )
    else:
        msg = "missing Task: trailer"

    if args.warn_only:
        print(f"warn: {msg}", file=sys.stderr)
        return 0
    print(msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
