#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Dry-run: parse docs/EZ2-AIB-DEV-OUTLINE.md §108.3 table → four-layer nodes.jsonl.

Layers: outline › major › subitem › task
  t.aib.108 (outline) → t.aib.108.3 (major) → t.aib.108.3.{NNN} (subitem)
    → t.aib.108.{NNN} (task)

Writes to stdout or ~/.ez2-aib/pending/ only by default.
Optional --write-nodes PATH for temp tests only (refuses projects/ unless
--allow-projects).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from schema_v0 import (  # noqa: E402
    load_status_marks,
    node_to_jsonl,
    repo_root_from_script,
    validate_nodes,
)

DEFAULT_OUTLINE = "docs/EZ2-AIB-DEV-OUTLINE.md"
DEFAULT_PENDING_NAME = "DRYRUN-nodes-108-3-A-0926.jsonl"
SECTION_108_3 = "### 108.3"
RE_ROW_START = re.compile(r"^\|\s*(\d{3})\b")
RE_BOLD_TITLE = re.compile(r"\*\*([^*]+)\*\*")
STATUS_MAP = [
    (re.compile(r"⛔|not_doing|不做"), "not_doing"),
    (re.compile(r"🔴|blocked|卡住|阻"), "blocked"),
    (re.compile(r"⌛|waiting_prereq|等前置"), "waiting_prereq"),
    (re.compile(r"🔄|in_progress|進行"), "in_progress"),
    (re.compile(r"✅|done|完成|已做"), "done"),
    (re.compile(r"partial|部分"), "partial"),
    (re.compile(r"🟡|pending|未派|候"), "pending"),
]


def _die(msg: str, code: int = 1) -> int:
    print(msg, file=sys.stderr)
    return code


def map_status(cell: str) -> str:
    for pat, key in STATUS_MAP:
        if pat.search(cell):
            return key
    return "pending"


def extract_title(item_cell: str) -> str:
    m = RE_BOLD_TITLE.search(item_cell)
    if m:
        return m.group(1).strip()
    cut = re.split(r"[：:]", item_cell, maxsplit=1)[0]
    return cut.strip()[:80]


def parse_108_3_rows(text: str) -> list[dict]:
    """Parse §108.3 markdown table rows into {num, title, status, owner}."""
    idx = text.find(SECTION_108_3)
    if idx < 0:
        raise ValueError(f"section {SECTION_108_3!r} not found in outline")
    rest = text[idx:]
    end_m = re.search(r"\n#{2,3}\s", rest[1:])
    block = rest[: end_m.start() + 1] if end_m else rest

    rows: list[dict] = []
    for line in block.splitlines():
        if not RE_ROW_START.match(line):
            continue
        body = line.strip()
        if body.startswith("|"):
            body = body[1:]
        if body.endswith("|"):
            body = body[:-1]
        cols = [c.strip() for c in body.split(" | ")]
        if len(cols) < 3:
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3:
            continue
        num_cell = cols[0]
        mnum = re.match(r"(\d{3})", num_cell.strip())
        if not mnum:
            continue
        num = mnum.group(1)
        item_cell = cols[1] if len(cols) > 1 else ""
        status_cell = cols[2] if len(cols) > 2 else ""
        owner_cell = cols[3] if len(cols) > 3 else ""
        rows.append(
            {
                "num": num,
                "title": extract_title(item_cell),
                "status": map_status(status_cell),
                "owner": owner_cell.strip(),
                "item_raw": item_cell,
            }
        )
    return rows


def build_nodes(rows: list[dict]) -> list[dict]:
    """Four-layer: outline → major → subitem → task."""
    root = {
        "uid": "t.aib.108",
        "parent_uid": None,
        "status": "in_progress",
        "owner": "",
        "alias": ["§108", "人格／專案定案主軸"],
        "version": 1,
        "layer": "outline",
        "title": "人格／專案定案主軸（§108）",
    }
    section = {
        "uid": "t.aib.108.3",
        "parent_uid": "t.aib.108",
        "status": "pending",
        "owner": "",
        "alias": ["§108.3", "新項目"],
        "version": 1,
        "layer": "major",
        "title": "新項目（登記）",
    }
    nodes: list[dict] = [root, section]
    for r in rows:
        num = r["num"]
        sub_uid = f"t.aib.108.3.{num}"
        task_uid = f"t.aib.108.{num}"
        nodes.append(
            {
                "uid": sub_uid,
                "parent_uid": "t.aib.108.3",
                "status": "pending",
                "owner": "",
                "alias": [f"§108.3.{num}", f"子項 {num}"],
                "version": 1,
                "layer": "subitem",
                "title": f"子項 {num}（{r['title'][:40]}）",
            }
        )
        aliases = [f"§108.{num}", f"項 {num}", f"#{num}"]
        nodes.append(
            {
                "uid": task_uid,
                "parent_uid": sub_uid,
                "status": r["status"],
                "owner": r["owner"] if r["owner"] else "",
                "alias": aliases,
                "version": 1,
                "layer": "task",
                "title": r["title"],
            }
        )
    return nodes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Dry-run import §108.3 outline → four-layer nodes.jsonl "
            "(no projects/ write by default)."
        )
    )
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repo root (default: locate from this script)",
    )
    ap.add_argument(
        "--outline",
        type=Path,
        default=None,
        help=f"outline md (default: <repo>/{DEFAULT_OUTLINE})",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "write jsonl here (default: stdout). "
            f"Use --pending for ~/.ez2-aib/pending/{DEFAULT_PENDING_NAME}"
        ),
    )
    ap.add_argument(
        "--pending",
        action="store_true",
        help=f"write to ~/.ez2-aib/pending/{DEFAULT_PENDING_NAME}",
    )
    ap.add_argument(
        "--write-nodes",
        type=Path,
        default=None,
        help="test-only: write nodes.jsonl to PATH (refuses projects/ unless --allow-projects)",
    )
    ap.add_argument(
        "--allow-projects",
        action="store_true",
        help="with --write-nodes, allow writing under projects/ (dangerous; tests only)",
    )
    args = ap.parse_args(argv)

    repo_root = (
        args.repo_root.resolve()
        if args.repo_root
        else repo_root_from_script(__file__)
    )
    outline = (
        args.outline.expanduser().resolve()
        if args.outline
        else (repo_root / DEFAULT_OUTLINE).resolve()
    )
    if not outline.is_file():
        return _die(f"import_outline_dry_run: outline not found: {outline}")

    try:
        marks = load_status_marks(repo_root)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return _die(f"import_outline_dry_run: cannot load status-enum: {e}")

    try:
        rows = parse_108_3_rows(outline.read_text(encoding="utf-8"))
    except ValueError as e:
        return _die(f"import_outline_dry_run: {e}")

    if not rows:
        return _die("import_outline_dry_run: no §108.3 table rows parsed")

    nodes = build_nodes(rows)
    errs = validate_nodes(nodes, marks=marks)
    if errs:
        for e in errs:
            print(f"import_outline_dry_run: schema error: {e}", file=sys.stderr)
        return 1

    blob = json.dumps(nodes, ensure_ascii=False)
    for needle in ("§108.323", "§108.324", "§108.330"):
        if needle not in blob:
            return _die(
                f"import_outline_dry_run: expected alias {needle} missing from dry-run"
            )
    for layer in ("outline", "major", "subitem", "task"):
        if f'"layer": "{layer}"' not in blob and f'"layer":"{layer}"' not in blob:
            # also check via node scan
            if not any(n.get("layer") == layer for n in nodes):
                return _die(f"import_outline_dry_run: missing layer={layer}")

    body = "\n".join(node_to_jsonl(n) for n in nodes) + "\n"

    targets = [x for x in (args.pending, args.out, args.write_nodes) if x]
    if args.pending and args.out:
        return _die("import_outline_dry_run: use only one of --pending / --out")

    def _write(out: Path) -> int:
        try:
            rel = out.relative_to(repo_root)
            if rel.parts and rel.parts[0] == "projects" and not args.allow_projects:
                return _die(
                    f"import_outline_dry_run: refused to write under projects/: {out}"
                )
        except ValueError:
            pass
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"wrote {out} ({len(nodes)} nodes)", file=sys.stderr)
        return 0

    if args.write_nodes:
        return _write(args.write_nodes.expanduser().resolve())

    if args.pending:
        out = Path.home() / ".ez2-aib" / "pending" / DEFAULT_PENDING_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"wrote {out} ({len(nodes)} nodes)", file=sys.stderr)
        return 0

    if args.out:
        return _write(args.out.expanduser().resolve())

    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
