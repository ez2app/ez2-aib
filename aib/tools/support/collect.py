#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""S1 support collector: pack a de-identified bug/suggestion bundle.

Does not send to LINE. --confirm writes a local pending file only.
Must-miss: leftover key-like tokens abort the write.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PENDING = Path.home() / ".ez2-aib" / "pending"

KEYISH = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,}|api[_-]?key\s*[:=]\s*\S+|"
    r"BEGIN (RSA |OPENSSH )?PRIVATE KEY)",
    re.I,
)
ABS_HOME = re.compile(re.escape(str(Path.home())))


def redact(text):
    text = ABS_HOME.sub("~", text or "")
    text = KEYISH.sub("[redacted]", text)
    return text


def leftover_secrets(text):
    return bool(KEYISH.search(text or ""))


def _run(cmd, cwd=None):
    try:
        p = subprocess.run(
            cmd, cwd=cwd or str(REPO), capture_output=True, text=True, timeout=20)
        return (p.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def collect(environ=None, repo=None):
    environ = os.environ if environ is None else environ
    repo = Path(repo or REPO)
    version = ""
    vp = repo / "VERSION"
    if vp.is_file():
        version = vp.read_text(encoding="utf-8").strip()
    sha = _run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"])
    cabin = environ.get("EZ2_AIB_INSTANCE") or environ.get("EZ2_TMUX_SESSION") or ""
    seat = environ.get("EZ2_AIB_SEAT_LABEL") or ""
    if not seat:
        uuid = environ.get("EZ2_AIB_SESSION_UUID") or ""
        seat = uuid[-5:] if len(uuid) >= 5 else ""
    log_tail = ""
    logp = environ.get("EZ2_SUPPORT_LOG")
    if logp and Path(logp).is_file():
        lines = Path(logp).read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-40:])
    pane = environ.get("EZ2_SUPPORT_PANE_CAPTURE") or ""
    bundle = {
        "kind": "ez2-aib-support-v1",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": version,
        "git_sha": sha,
        "cabin": cabin,
        "seat_label": seat,
        "log_tail": redact(log_tail),
        "pane_capture": redact(pane),
        "sender": "ai",
    }
    raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True)
    if leftover_secrets(raw):
        raise SystemExit("collect: secret-like token remains after redact")
    return json.loads(raw)


def write_pending(bundle, sender="ai"):
    PENDING.mkdir(parents=True, exist_ok=True)
    bundle = dict(bundle)
    bundle["sender"] = sender
    name = "support-%s-%s.json" % (
        bundle.get("seat_label") or "unknown",
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    )
    path = PENDING / name
    text = json.dumps(bundle, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if leftover_secrets(text):
        raise SystemExit("collect: refuse to write; secret-like token remains")
    path.write_text(text, encoding="utf-8")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="support-collect")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--confirm", action="store_true",
                    help="write local pending file; still does not send")
    ap.add_argument("--sender", choices=("ai", "human"), default="ai")
    args = ap.parse_args(argv)
    bundle = collect(repo=args.repo)
    if args.confirm:
        path = write_pending(bundle, sender=args.sender)
        print(str(path))
        return 0
    print(json.dumps(bundle, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
