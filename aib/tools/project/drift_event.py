#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""drift_event — 偏離單七欄型別 + validate + DRIFT draft render (§10.17 / §108.324).

CLI default: dry-run to stdout. `--write` writes to ~/.ez2-aib/pending/
(or $AIB_PENDING). Filename suggestion: DRIFT-<short>-draft.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REASON_KINDS = frozenset(
    {"推進中發現", "必要前置", "bug", "裁定改變", "外部變動"}
)
DISPOSITIONS = frozenset(
    {"納入計畫成新格", "暫時繞路後回歸", "取消原格", "取代原格"}
)
REQUIRED_FIELDS = (
    "planned_uid",
    "actual",
    "reason_kind",
    "impacts",
    "disposition",
    "approved_by",
    "returned",
)


class DriftValidationError(ValueError):
    """One or more 偏離單欄位 violations."""


@dataclass
class DriftEvent:
    """偏離單七欄 (§10.17 三)."""

    planned_uid: str
    actual: str
    reason_kind: str
    impacts: list[str]
    disposition: str
    approved_by: str
    returned: bool | str = False
    # optional extras for draft naming / audit (not part of the seven)
    short_id: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # drop None optionals from dict view
        return {k: v for k, v in d.items() if v is not None or k in REQUIRED_FIELDS}


def validate_drift_event(obj: Any) -> list[str]:
    """Return list of violation messages (empty = ok). Does not raise."""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return [f"expected object, got {type(obj).__name__}"]

    for key in REQUIRED_FIELDS:
        if key not in obj:
            errs.append(f"missing required field `{key}`")

    pu = obj.get("planned_uid")
    if "planned_uid" in obj and not (isinstance(pu, str) and pu.strip()):
        errs.append("`planned_uid` must be a non-empty string")

    actual = obj.get("actual")
    if "actual" in obj and not (isinstance(actual, str) and actual.strip()):
        errs.append("`actual` must be a non-empty string")

    rk = obj.get("reason_kind")
    if "reason_kind" in obj:
        if not isinstance(rk, str) or rk not in REASON_KINDS:
            errs.append(
                f"`reason_kind` must be one of {sorted(REASON_KINDS)}; got {rk!r}"
            )

    impacts = obj.get("impacts")
    if "impacts" in obj:
        if not isinstance(impacts, list) or not all(
            isinstance(x, str) for x in impacts
        ):
            errs.append("`impacts` must be string[]")

    disp = obj.get("disposition")
    if "disposition" in obj:
        if not isinstance(disp, str) or disp not in DISPOSITIONS:
            errs.append(
                f"`disposition` must be one of {sorted(DISPOSITIONS)}; got {disp!r}"
            )

    ab = obj.get("approved_by")
    if "approved_by" in obj and not isinstance(ab, str):
        errs.append("`approved_by` must be string")

    if "returned" in obj:
        ret = obj["returned"]
        if not isinstance(ret, (bool, str)):
            errs.append("`returned` must be bool or string (e.g. 未)")

    return errs


def parse_drift_event(obj: dict[str, Any]) -> DriftEvent:
    errs = validate_drift_event(obj)
    if errs:
        raise DriftValidationError("; ".join(errs))
    return DriftEvent(
        planned_uid=str(obj["planned_uid"]).strip(),
        actual=str(obj["actual"]).strip(),
        reason_kind=str(obj["reason_kind"]),
        impacts=list(obj["impacts"]),
        disposition=str(obj["disposition"]),
        approved_by=str(obj["approved_by"]),
        returned=obj.get("returned", False),
        short_id=obj.get("short_id"),
        notes=obj.get("notes"),
    )


def _short_slug(event: DriftEvent) -> str:
    if event.short_id and str(event.short_id).strip():
        raw = str(event.short_id).strip()
    else:
        raw = event.planned_uid
    # strip § and dots for filename friendliness
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return (slug or "unknown")[:32]


def render_drift_draft(event: DriftEvent | dict[str, Any]) -> str:
    """Render markdown DRIFT draft body (title line included)."""
    if isinstance(event, dict):
        event = parse_drift_event(event)
    slug = _short_slug(event)
    title = f"DRIFT-{slug}-draft.md"
    returned_s = event.returned if isinstance(event.returned, str) else (
        "是" if event.returned else "未"
    )
    impacts_lines = (
        "\n".join(f"- {x}" for x in event.impacts) if event.impacts else "- （無）"
    )
    notes = event.notes or ""
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    body = f"""# {title}

> 偏離單草稿（§10.17 七欄）· generated {now}
> 本檔由 tools/project/drift_event.py 產生；正式 DRIFT-* 待匯入流程接上。

| 欄 | 值 |
|---|---|
| planned_uid | `{event.planned_uid}` |
| actual | {event.actual} |
| reason_kind | {event.reason_kind} |
| disposition | {event.disposition} |
| approved_by | {event.approved_by} |
| returned | {returned_s} |

## impacts
{impacts_lines}
"""
    if notes:
        body += f"\n## notes\n{notes}\n"
    return body


def pending_dir() -> Path:
    env = os.environ.get("AIB_PENDING")
    if env:
        return Path(env).expanduser()
    return Path("~/.ez2-aib/pending").expanduser()


def draft_filename(event: DriftEvent) -> str:
    return f"DRIFT-{_short_slug(event)}-draft.md"


def write_draft(event: DriftEvent, *, dest_dir: Path | None = None) -> Path:
    dest = dest_dir or pending_dir()
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / draft_filename(event)
    path.write_text(render_drift_draft(event), encoding="utf-8")
    return path


def _load_event(args: argparse.Namespace) -> DriftEvent:
    if args.event:
        obj = json.loads(args.event)
    elif args.event_json == "-":
        obj = json.loads(sys.stdin.read())
    elif args.event_json:
        obj = json.loads(
            Path(args.event_json).expanduser().read_text(encoding="utf-8")
        )
    else:
        raise SystemExit("need --event or --event-json")
    return parse_drift_event(obj)


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="偏離單七欄 validate + DRIFT draft (§108.324)."
    )
    p.add_argument("--event", help="inline JSON for drift event")
    p.add_argument(
        "--event-json",
        help="path to JSON file, or - for stdin",
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="write draft under ~/.ez2-aib/pending/ (or $AIB_PENDING); default dry-run",
    )
    p.add_argument(
        "--pending-dir",
        help="override pending directory (also respects AIB_PENDING)",
    )
    p.add_argument(
        "--validate-only",
        action="store_true",
        help="only validate; print ok / errors; no draft",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    try:
        event = _load_event(args)
    except DriftValidationError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.validate_only:
        print("ok")
        return 0

    draft = render_drift_draft(event)
    if args.write:
        dest = Path(args.pending_dir).expanduser() if args.pending_dir else None
        path = write_draft(event, dest_dir=dest)
        print(f"wrote {path}", file=sys.stderr)
        print(draft)
        return 0

    # dry-run
    print(draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
