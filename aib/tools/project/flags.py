#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Three plan-drift flags (§10.17 / §108.324) — real implementations.

1. orphan            — no for_task_uid / invalid Task uid (not t.[A-Za-z0-9._-]+)
2. out_of_writeset   — changed_paths outside writeset; dir prefix supported
                       (e.g. tools/project/ covers tools/project/flags.py);
                       writeset missing → None/unknown
3. detour_unreturned — disposition=暫時繞路後回歸 ∧ returned 未歸
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# Disposition value that means "temporary detour then return"
DETOUR_DISPOSITION = "暫時繞路後回歸"

# returned field: false / "未" / "no" / 0 → not returned
_NOT_RETURNED = {False, "未", "no", "false", "0", 0, "False", "NO"}

# Formal Task uid only (same as check_task_trailer; no §/# aliases)
_VALID_TASK_UID = re.compile(r"^t\.[A-Za-z0-9._-]+$")


def orphan(for_task_uid: str | None) -> bool:
    """無主：沒有 for_task_uid，或不是正式 Task uid（t.…）。"""
    if for_task_uid is None:
        return True
    if not isinstance(for_task_uid, str):
        return True
    val = for_task_uid.strip()
    if not val:
        return True
    return _VALID_TASK_UID.match(val) is None


def _normalize_writeset_entry(entry: str) -> str:
    """Normalize a writeset path; keep trailing slash for dir-prefix intent."""
    s = str(entry).replace("\\", "/").strip()
    return s


def _path_covered(path: str, writeset_entry: str) -> bool:
    """True if path equals entry or sits under a directory-prefix entry."""
    p = str(path).replace("\\", "/").lstrip("./")
    w = _normalize_writeset_entry(writeset_entry).lstrip("./")
    if not w:
        return False
    if p == w.rstrip("/"):
        return True
    if w.endswith("/"):
        return p.startswith(w) or p.startswith(w.rstrip("/"))
    # bare directory name without slash still covers children
    return p == w or p.startswith(w + "/")


def out_of_writeset(
    changed_paths: Sequence[str] | None,
    writeset: Sequence[str] | None,
) -> bool | None:
    """寫集外：路徑比對，支援目錄前綴.

    Returns:
      True  — at least one changed path is outside writeset
      False — all changed paths are inside writeset (or no changes)
      None  — writeset missing / unknown → caller treats as unknown
    """
    if writeset is None:
        return None
    if changed_paths is None:
        changed_paths = []
    ws = list(writeset)
    for path in changed_paths:
        if not any(_path_covered(str(path), w) for w in ws):
            return True
    return False


def detour_unreturned(event: dict[str, Any] | None) -> bool:
    """繞路未歸位：disposition=暫時繞路後回歸 ∧ returned ∈ {false,未,…}."""
    if not isinstance(event, dict):
        return False
    if event.get("disposition") != DETOUR_DISPOSITION:
        return False
    returned = event.get("returned", False)
    return returned in _NOT_RETURNED


def count_flags(
    events: Sequence[dict[str, Any]] | None,
    *,
    writeset: Sequence[str] | None = None,
) -> dict[str, int]:
    """Aggregate flag counts for seat B.

    Returns stable keys: orphan / out_of_writeset / detour_unreturned.
    When writeset is None, out_of_writeset paths that would be unknown are
    NOT counted (only explicit True bumps the counter).
    """
    counts = {"orphan": 0, "out_of_writeset": 0, "detour_unreturned": 0}
    if not events:
        return counts
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if orphan(ev.get("for_task_uid")):
            counts["orphan"] += 1
        oow = out_of_writeset(ev.get("changed_paths"), writeset)
        if oow is True:
            counts["out_of_writeset"] += 1
        if detour_unreturned(ev):
            counts["detour_unreturned"] += 1
    return counts


def flags_report(
    events: Sequence[dict[str, Any]] | None = None,
    *,
    writeset: Sequence[str] | None = None,
    events_path: str | Path | None = None,
) -> dict[str, int]:
    """Load optional events JSON (list or {events:[…]}) then count_flags."""
    loaded: list[dict[str, Any]] = []
    if events_path is not None:
        path = Path(events_path).expanduser()
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                loaded = [x for x in raw if isinstance(x, dict)]
            elif isinstance(raw, dict):
                inner = raw.get("events", raw.get("items", []))
                if isinstance(inner, list):
                    loaded = [x for x in inner if isinstance(x, dict)]
    if events:
        loaded.extend(e for e in events if isinstance(e, dict))
    return count_flags(loaded, writeset=writeset)


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Plan-drift flag helpers (§108.324).")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("orphan", help="orphan(for_task_uid) → bool")
    o.add_argument("--for-task-uid", default=None)

    w = sub.add_parser("out-of-writeset", help="out_of_writeset (dir prefix ok)")
    w.add_argument(
        "--changed",
        nargs="*",
        default=[],
        help="changed paths",
    )
    w.add_argument(
        "--writeset",
        nargs="*",
        default=None,
        help="allowed writeset paths; omit entirely → unknown/None",
    )
    w.add_argument(
        "--writeset-missing",
        action="store_true",
        help="force writeset=None (unknown)",
    )

    d = sub.add_parser("detour-unreturned", help="detour_unreturned(event)")
    d.add_argument(
        "--event-json",
        help="path to drift event JSON (or - for stdin)",
    )
    d.add_argument(
        "--event",
        help="inline JSON string for the event",
    )

    c = sub.add_parser("count", help="count_flags → JSON for seat B")
    c.add_argument(
        "--events-json",
        help="path to events JSON list (or {events:[…]})",
    )
    c.add_argument(
        "--writeset",
        nargs="*",
        default=None,
        help="writeset paths for out_of_writeset; omit → unknown (not counted)",
    )
    c.add_argument(
        "--writeset-missing",
        action="store_true",
        help="force writeset=None",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "orphan":
        print("true" if orphan(args.for_task_uid) else "false")
        return 0

    if args.cmd == "out-of-writeset":
        ws = None if args.writeset_missing else args.writeset
        if not args.writeset_missing and args.writeset is None:
            ws = None
        result = out_of_writeset(args.changed, ws)
        if result is None:
            print("unknown")
        else:
            print("true" if result else "false")
        return 0

    if args.cmd == "detour-unreturned":
        raw: str
        if args.event is not None:
            raw = args.event
        elif args.event_json == "-":
            raw = sys.stdin.read()
        elif args.event_json:
            raw = Path(args.event_json).expanduser().read_text(encoding="utf-8")
        else:
            print("need --event or --event-json", file=sys.stderr)
            return 2
        event = json.loads(raw)
        print("true" if detour_unreturned(event) else "false")
        return 0

    if args.cmd == "count":
        ws = None if args.writeset_missing else args.writeset
        report = flags_report(
            events_path=args.events_json,
            writeset=ws,
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
