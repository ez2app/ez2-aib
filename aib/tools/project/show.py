#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib project show — read projects/<uid>/nodes.jsonl → human tree / --json / --report.

Supports:
  --depth N   root depth = 0; only print/include nodes at depth <= N
  --mine      filter to Task leaves owned by seat; keep full parent chain
  --owner / AIB_SEAT / EZ2_AIB_SEAT
  progress    per-node {done,total,ratio} from descendant layer=task leaves
              (not_doing excluded from total)
  --flags-json PATH  optional events for seat-B flag counts; missing → 0
  --report    emit normalized total-report JSON (F aib-report validate /
              progress:auto). Mutually exclusive with --json.

Exit non-zero with stderr on missing file / bad JSON / schema / broken --mine chain.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
import importlib.util

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from schema_v0 import (  # noqa: E402
    load_nodes_jsonl,
    load_status_marks,
    repo_root_from_script,
    status_enum_path,
)
from flags import flags_report  # noqa: E402


def _die(msg: str, code: int = 1) -> int:
    print(msg, file=sys.stderr)
    return code


def resolve_nodes_path(args: argparse.Namespace, repo_root: Path) -> Path | None:
    if args.nodes:
        return Path(args.nodes).expanduser().resolve()
    if not args.project_uid:
        return None
    return (repo_root / "projects" / args.project_uid / "nodes.jsonl").resolve()


def resolve_seat(args: argparse.Namespace) -> str | None:
    if getattr(args, "owner", None):
        return str(args.owner).strip() or None
    for key in ("AIB_SEAT", "EZ2_AIB_SEAT"):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return None


def node_depth_map(nodes: list[dict]) -> dict[str, int]:
    """Root (parent_uid=null) depth = 0."""
    by_uid = {n["uid"]: n for n in nodes if isinstance(n.get("uid"), str)}
    depths: dict[str, int] = {}

    def depth_of(uid: str, stack: set[str]) -> int:
        if uid in depths:
            return depths[uid]
        if uid in stack:
            return 0  # cycle guard
        n = by_uid.get(uid)
        if n is None:
            return 0
        pu = n.get("parent_uid")
        if pu is None:
            depths[uid] = 0
            return 0
        if not isinstance(pu, str) or pu not in by_uid:
            depths[uid] = 0
            return 0
        stack.add(uid)
        d = depth_of(pu, stack) + 1
        stack.discard(uid)
        depths[uid] = d
        return d

    for uid in by_uid:
        depth_of(uid, set())
    return depths


def compute_progress(nodes: list[dict]) -> dict[str, dict]:
    """Per-uid progress from descendant layer=task leaves.

    not_doing is excluded from total (and thus from done).
    A task leaf's own progress reflects itself only.
    """
    by_uid = {n["uid"]: n for n in nodes if isinstance(n.get("uid"), str)}
    children: dict[str | None, list[str]] = defaultdict(list)
    for n in nodes:
        uid = n.get("uid")
        if not isinstance(uid, str):
            continue
        children[n.get("parent_uid")].append(uid)

    descendants_cache: dict[str, list[str]] = {}

    def all_descendants(uid: str) -> list[str]:
        if uid in descendants_cache:
            return descendants_cache[uid]
        out: list[str] = []
        for kid in children.get(uid, []):
            out.append(kid)
            out.extend(all_descendants(kid))
        descendants_cache[uid] = out
        return out

    progress: dict[str, dict] = {}
    for uid, n in by_uid.items():
        if n.get("layer") == "task":
            # leaf self
            st = n.get("status")
            if st == "not_doing":
                done, total = 0, 0
            else:
                total = 1
                done = 1 if st == "done" else 0
        else:
            leaves = [
                by_uid[d]
                for d in all_descendants(uid)
                if by_uid.get(d, {}).get("layer") == "task"
            ]
            countable = [t for t in leaves if t.get("status") != "not_doing"]
            total = len(countable)
            done = sum(1 for t in countable if t.get("status") == "done")
        ratio = (done / total) if total else 0.0
        progress[uid] = {"done": done, "total": total, "ratio": ratio}
    return progress


def filter_mine(
    nodes: list[dict], seat: str
) -> tuple[list[dict], list[str]]:
    """Keep Task leaves owned by seat + full ancestor chain to root.

    Returns (filtered_nodes, errors). Errors non-empty ⇒ caller must exit non-zero.
    """
    by_uid = {n["uid"]: n for n in nodes if isinstance(n.get("uid"), str)}
    mine_tasks = [
        n
        for n in nodes
        if n.get("layer") == "task" and (n.get("owner") or "") == seat
    ]
    keep: set[str] = set()
    errs: list[str] = []
    for t in mine_tasks:
        uid = t["uid"]
        keep.add(uid)
        cur = t.get("parent_uid")
        seen: set[str] = set()
        while cur is not None:
            if not isinstance(cur, str) or not cur.strip():
                errs.append(
                    f"--mine: task {uid!r} parent chain broken "
                    f"(bad parent_uid={cur!r})"
                )
                break
            if cur in seen:
                errs.append(f"--mine: cycle in parent chain at {cur!r} (from {uid})")
                break
            seen.add(cur)
            parent = by_uid.get(cur)
            if parent is None:
                errs.append(
                    f"--mine: task {uid!r} parent chain broken "
                    f"(missing parent {cur!r})"
                )
                break
            keep.add(cur)
            cur = parent.get("parent_uid")
    filtered = [n for n in nodes if n.get("uid") in keep]
    return filtered, errs


def apply_depth(nodes: list[dict], max_depth: int) -> list[dict]:
    depths = node_depth_map(nodes)
    return [n for n in nodes if depths.get(n.get("uid", ""), 0) <= max_depth]


def build_tree_lines(
    nodes: list[dict],
    marks_symbols: dict[str, str],
    progress: dict[str, dict],
    max_depth: int | None = None,
) -> list[str]:
    by_parent: dict[str | None, list[dict]] = defaultdict(list)
    for n in nodes:
        by_parent[n.get("parent_uid")].append(n)

    for kids in by_parent.values():
        kids.sort(key=lambda x: str(x.get("uid", "")))

    lines: list[str] = []

    def walk(uid: str | None, depth: int) -> None:
        if max_depth is not None and depth > max_depth:
            return
        for n in by_parent.get(uid, []):
            st = n.get("status", "")
            mark = marks_symbols.get(st, "?")
            aliases = n.get("alias") or []
            alias_s = " ".join(aliases) if aliases else ""
            owner = n.get("owner") or ""
            title = n.get("title") or ""
            layer = n.get("layer") or ""
            indent = "  " * depth
            bits = [f"{indent}{mark} {n.get('uid', '?')}"]
            if layer:
                bits.append(f"<{layer}>")
            if alias_s:
                bits.append(f"[{alias_s}]")
            if title:
                bits.append(title)
            if owner:
                bits.append(f"— {owner}")
            prog = progress.get(n.get("uid", ""), {})
            if prog:
                bits.append(
                    f"[{prog.get('done', 0)}/{prog.get('total', 0)}"
                    f"={prog.get('ratio', 0):.0%}]"
                )
            bits.append(f"(v{n.get('version', '?')})")
            lines.append(" ".join(bits))
            if max_depth is None or depth < max_depth:
                walk(n.get("uid"), depth + 1)

    walk(None, 0)
    rooted: set[str] = set()

    def collect(uid: str | None) -> None:
        for n in by_parent.get(uid, []):
            rooted.add(n["uid"])
            collect(n["uid"])

    collect(None)
    orphans = [n for n in nodes if n.get("uid") not in rooted]
    for n in orphans:
        st = n.get("status", "")
        mark = marks_symbols.get(st, "?")
        aliases = " ".join(n.get("alias") or [])
        lines.append(
            f"{mark} {n.get('uid')} [{aliases}] (orphan parent={n.get('parent_uid')!r})"
        )
    return lines



def load_aib_report_mod(repo_root: Path):
    """Import tools/report/aib-report.py (reuse validate / progress:auto)."""
    report_py = Path(repo_root) / "tools" / "report" / "aib-report.py"
    if not report_py.is_file():
        raise FileNotFoundError(f"aib-report missing: {report_py}")
    spec = importlib.util.spec_from_file_location(
        "aib_report_show_f2", str(report_py)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load aib-report: {report_py}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _node_title(n: dict) -> str:
    title = n.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    alias = n.get("alias") or []
    if isinstance(alias, list):
        for a in alias:
            if isinstance(a, str) and a.strip():
                return a.strip()
    uid = n.get("uid")
    return str(uid) if uid else "（無題）"


def _node_evidence(n: dict) -> tuple[str, str | None]:
    """Return (evidence, evidence_command). Missing → F-safe default (no digits)."""
    ev = n.get("evidence")
    cmd = n.get("evidence_command")
    if isinstance(ev, list):
        parts = [str(x).strip() for x in ev if str(x).strip()]
        ev = "；".join(parts) if parts else None
    if isinstance(ev, str) and ev.strip():
        evidence = ev.strip()
    else:
        evidence = "尚未"
    if isinstance(cmd, str) and cmd.strip():
        return evidence, cmd.strip()
    return evidence, None


def _optional_human_quote(n: dict) -> str | None:
    for key in ("human_quote", "approval_event_uid"):
        val = n.get(key)
        if isinstance(val, str) and val.strip().startswith("evt:"):
            return val.strip()
    return None


def _optional_seat(n: dict, seat_re) -> str | None:
    owner = n.get("owner")
    if isinstance(owner, str) and owner.strip() and seat_re.fullmatch(owner.strip()):
        return owner.strip()
    seat = n.get("seat")
    if isinstance(seat, str) and seat.strip() and seat_re.fullmatch(seat.strip()):
        return seat.strip()
    return None


def _optional_task_uid(n: dict) -> str | None:
    tu = n.get("task_uid")
    if isinstance(tu, str) and tu.strip():
        return tu.strip()
    # plan-tree task leaves: uid is the canonical task id
    if n.get("layer") == "task":
        uid = n.get("uid")
        if isinstance(uid, str) and uid.strip():
            return uid.strip()
    return None


def _build_children_index(nodes: list[dict]) -> dict[str | None, list[dict]]:
    children: dict[str | None, list[dict]] = defaultdict(list)
    for n in nodes:
        uid = n.get("uid")
        if not isinstance(uid, str):
            continue
        children[n.get("parent_uid")].append(n)
    return children


def _report_node_from_plan(
    n: dict,
    report_id: str,
    children_raw: list[dict],
    *,
    seat_re,
) -> dict:
    """Build one aib-report tree node; children already converted."""
    node: dict = {
        "id": report_id,
        "title": _node_title(n),
    }
    aq = _optional_human_quote(n)
    if aq is not None:
        node["human_quote"] = aq
    drift = n.get("drift")
    if isinstance(drift, str) and drift.strip():
        node["drift"] = drift.strip()
    seat = _optional_seat(n, seat_re)
    if seat is not None:
        node["seat"] = seat
    task_uid = _optional_task_uid(n)
    if task_uid is not None:
        node["task_uid"] = task_uid

    if children_raw:
        node["children"] = children_raw
        st = n.get("status")
        if isinstance(st, str) and st:
            node["status"] = st
        return node

    # Leaf: status + evidence required by aib-report
    st = n.get("status")
    if not isinstance(st, str) or not st:
        st = "pending"
    node["status"] = st
    evidence, cmd = _node_evidence(n)
    node["evidence"] = evidence
    if cmd is not None:
        node["evidence_command"] = cmd
    return node


def nodes_to_report_payload(nodes: list[dict], *, seat_re) -> dict:
    """Convert PLAN-TREE nodes → raw aib-report payload (pre-validate).

    Outline becomes report title; majors are tree roots (總綱省略、大項當頂).
    Report ids are synthetic M1 / M1.1 / M1.1.1 so parent-prefix rules hold
    even when plan task uids skip the subitem segment (t.aib.108.323 under
    t.aib.108.3.323). Real plan uid goes to task_uid on task leaves.
    """
    children = _build_children_index(nodes)

    outlines = [n for n in nodes if n.get("layer") == "outline"]
    if outlines:
        title = _node_title(outlines[0])
        major_parent_uids = {o["uid"] for o in outlines}
        majors_ordered = [
            n for n in nodes
            if n.get("layer") == "major" and n.get("parent_uid") in major_parent_uids
        ]
    else:
        roots = [n for n in nodes if n.get("parent_uid") is None]
        title = _node_title(roots[0]) if roots else "（未命名計畫樹）"
        # no outline: prefer layer=major; else treat roots as majors
        majors_ordered = [n for n in nodes if n.get("layer") == "major"]
        if not majors_ordered:
            majors_ordered = list(roots)

    def convert(n: dict, report_id: str) -> dict:
        kids_plan = children.get(n["uid"], [])
        kid_nodes = []
        for i, kid in enumerate(kids_plan, start=1):
            kid_id = f"{report_id}.{i}"
            kid_nodes.append(convert(kid, kid_id))
        return _report_node_from_plan(
            n, report_id, kid_nodes, seat_re=seat_re
        )

    tree = []
    for i, major in enumerate(majors_ordered, start=1):
        tree.append(convert(major, f"M{i}"))

    if not tree:
        raise ValueError(
            "show --report: no major nodes to build report tree "
            "(need layer=major under outline, or a non-empty filtered tree)"
        )

    return {
        "title": title,
        "tree": tree,
        "progress_text": "（由計畫樹自動彙總）",
        "stars": 3,
        "progress": "auto",
        "summary": "（由 aib project show --report 自 nodes 彙出）",
        "reflection": "（無）",
        "footer": {
            "dispatched": 0,
            "executed": "show --report",
            "human_decision": "不需回應",
            "waiting_update": "無",
            "next": "無",
            "patrol_minutes": 0,
            "patrol": "無",
        },
    }


def emit_normalized_report_json(nodes: list[dict], repo_root: Path) -> str:
    """nodes → report payload → aib-report.validate → JSON text (+ trailing \n)."""
    mod = load_aib_report_mod(repo_root)
    raw = nodes_to_report_payload(nodes, seat_re=mod.SEAT_RE)
    try:
        data = mod.validate(raw)
    except mod.InputError as exc:
        raise ValueError(f"show --report: aib-report validate failed: {exc}") from exc
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def attach_progress(nodes: list[dict], progress: dict[str, dict]) -> list[dict]:
    out = []
    for n in nodes:
        nn = dict(n)
        nn["progress"] = progress.get(
            n.get("uid", ""), {"done": 0, "total": 0, "ratio": 0.0}
        )
        out.append(nn)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Show plan-tree nodes (PLAN-TREE-SCHEMA v0)."
    )
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repo root (default: locate from this script)",
    )
    ap.add_argument(
        "--project-uid",
        default=None,
        help="read projects/<project_uid>/nodes.jsonl",
    )
    ap.add_argument(
        "--nodes",
        type=Path,
        default=None,
        help="explicit nodes.jsonl path (overrides --project-uid)",
    )
    out_g = ap.add_mutually_exclusive_group()
    out_g.add_argument(
        "--json",
        action="store_true",
        help="print JSON object {nodes, orphan_count, …} (mutex with --report)",
    )
    out_g.add_argument(
        "--report",
        action="store_true",
        help=(
            "emit normalized total-report JSON via aib-report validate / "
            "progress:auto (mutex with --json; Wave2 F2)"
        ),
    )
    ap.add_argument(
        "--depth",
        type=int,
        default=None,
        help="only include nodes at depth <= N (root depth = 0)",
    )
    ap.add_argument(
        "--mine",
        action="store_true",
        help="filter to Task leaves owned by seat + parent chain to root",
    )
    ap.add_argument(
        "--owner",
        default=None,
        help="seat owner for --mine (else AIB_SEAT / EZ2_AIB_SEAT)",
    )
    ap.add_argument(
        "--flags-json",
        type=Path,
        default=None,
        help="optional events JSON for flag counts; missing/absent → counts 0",
    )
    ap.add_argument(
        "--writeset",
        nargs="*",
        default=None,
        help="writeset paths for out_of_writeset counting (with --flags-json)",
    )
    args = ap.parse_args(argv)

    repo_root = (
        args.repo_root.resolve()
        if args.repo_root
        else repo_root_from_script(__file__)
    )

    nodes_path = resolve_nodes_path(args, repo_root)
    if nodes_path is None:
        return _die(
            "show.py: need --nodes PATH or --project-uid UID "
            "(reads projects/<uid>/nodes.jsonl under --repo-root)"
        )

    if not nodes_path.is_file():
        return _die(f"show.py: nodes file not found: {nodes_path}")

    try:
        marks = load_status_marks(repo_root)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return _die(f"show.py: cannot load status-enum: {e}")

    nodes, errs = load_nodes_jsonl(nodes_path, marks=marks)
    if errs:
        for e in errs:
            print(f"show.py: schema/parse error: {e}", file=sys.stderr)
        return 1

    if args.mine:
        seat = resolve_seat(args)
        if not seat:
            return _die(
                "show.py: --mine requires --owner or env AIB_SEAT / EZ2_AIB_SEAT"
            )
        nodes, mine_errs = filter_mine(nodes, seat)
        if mine_errs:
            for e in mine_errs:
                print(f"show.py: {e}", file=sys.stderr)
            return 1

    if args.depth is not None:
        if args.depth < 0:
            return _die("show.py: --depth must be >= 0 (root = 0)")
        nodes = apply_depth(nodes, args.depth)

    progress = compute_progress(nodes)
    nodes_with_prog = attach_progress(nodes, progress)

    # Flag counts for seat B
    flags_path = args.flags_json
    if flags_path is not None and not Path(flags_path).expanduser().is_file():
        # explicit path missing → counts 0 (documented interface)
        flag_counts = {
            "orphan": 0,
            "out_of_writeset": 0,
            "detour_unreturned": 0,
        }
    elif flags_path is not None:
        flag_counts = flags_report(
            events_path=flags_path, writeset=args.writeset
        )
    else:
        flag_counts = {
            "orphan": 0,
            "out_of_writeset": 0,
            "detour_unreturned": 0,
        }

    if getattr(args, "report", False):
        try:
            sys.stdout.write(emit_normalized_report_json(nodes, repo_root))
        except (ValueError, FileNotFoundError, ImportError, OSError) as e:
            return _die(str(e))
        return 0

    if args.json:
        payload = {
            "nodes": nodes_with_prog,
            "orphan_count": flag_counts["orphan"],
            "out_of_writeset_count": flag_counts["out_of_writeset"],
            "detour_unreturned_count": flag_counts["detour_unreturned"],
            "flags": flag_counts,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    enum_path = status_enum_path(repo_root)
    symbols = json.loads(enum_path.read_text(encoding="utf-8")).get("marks", {})
    for line in build_tree_lines(
        nodes, symbols, progress, max_depth=args.depth
    ):
        print(line)
    print(
        f"# flags: orphan={flag_counts['orphan']} "
        f"out_of_writeset={flag_counts['out_of_writeset']} "
        f"detour_unreturned={flag_counts['detour_unreturned']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
