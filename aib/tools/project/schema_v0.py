#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""PLAN-TREE-SCHEMA v0 validator (shared by show / import dry-run).

Frozen fields: uid, parent_uid, status, owner, alias, version, layer.
layer enum: outline|major|subitem|task (總綱/大項/子項/Task).
status must be a key of tools/report/status-enum.json → marks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED = ("uid", "parent_uid", "status", "owner", "alias", "version", "layer")

LAYERS = ("outline", "major", "subitem", "task")
# child layer → required parent layer (None = parent_uid must be null)
LAYER_PARENT_LAYER = {
    "outline": None,
    "major": "outline",
    "subitem": "major",
    "task": "subitem",
}

# tools/project/schema_v0.py → repo root
_SCHEMA_FILE = Path(__file__).resolve()


def repo_root_from_script(script_file: str | Path | None = None) -> Path:
    """Locate repo root from a tools/project/*.py path (parents[2])."""
    base = Path(script_file).resolve() if script_file else _SCHEMA_FILE
    return base.parents[2]


def status_enum_path(repo_root: Path) -> Path:
    return Path(repo_root) / "tools" / "report" / "status-enum.json"


def load_status_marks(repo_root: Path) -> set[str]:
    path = status_enum_path(repo_root)
    if not path.is_file():
        raise FileNotFoundError(f"status-enum missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    marks = data.get("marks")
    if not isinstance(marks, dict) or not marks:
        raise ValueError(f"status-enum marks missing/empty: {path}")
    return set(marks.keys())


class SchemaError(ValueError):
    """One or more PLAN-TREE-SCHEMA v0 violations."""


def validate_node(node: Any, *, marks: set[str], line_no: int | None = None) -> list[str]:
    """Return list of violation messages (empty = ok). Does not raise."""
    loc = f"line {line_no}" if line_no is not None else "node"
    errs: list[str] = []
    if not isinstance(node, dict):
        return [f"{loc}: expected JSON object, got {type(node).__name__}"]

    for key in REQUIRED:
        if key not in node:
            errs.append(f"{loc}: missing required field `{key}`")

    uid = node.get("uid")
    if "uid" in node and not (isinstance(uid, str) and uid.strip()):
        errs.append(f"{loc}: `uid` must be a non-empty string")

    if "parent_uid" in node:
        pu = node["parent_uid"]
        if pu is not None and not isinstance(pu, str):
            errs.append(f"{loc}: `parent_uid` must be string or null")

    if "status" in node:
        st = node["status"]
        if not isinstance(st, str):
            errs.append(f"{loc}: `status` must be string")
        elif st not in marks:
            errs.append(
                f"{loc}: `status`={st!r} not in status-enum marks "
                f"({', '.join(sorted(marks))})"
            )

    if "owner" in node and not isinstance(node["owner"], str):
        errs.append(f"{loc}: `owner` must be string")

    if "alias" in node:
        al = node["alias"]
        if not isinstance(al, list) or not all(isinstance(x, str) for x in al):
            errs.append(f"{loc}: `alias` must be string[]")

    if "version" in node:
        ver = node["version"]
        if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
            errs.append(f"{loc}: `version` must be int ≥ 1")

    layer = node.get("layer")
    if "layer" in node:
        if not isinstance(layer, str) or layer not in LAYERS:
            errs.append(
                f"{loc}: `layer` must be one of {', '.join(LAYERS)} (got {layer!r})"
            )
        else:
            pu = node.get("parent_uid")
            if layer == "task":
                if pu is None or (isinstance(pu, str) and not pu.strip()):
                    errs.append(
                        f"{loc}: layer=task requires non-null parent_uid "
                        "(Task must hang under a subitem)"
                    )
            if layer == "outline" and pu is not None:
                errs.append(f"{loc}: layer=outline requires parent_uid=null")

    return errs


def validate_nodes(
    nodes: list[Any], *, marks: set[str], check_parent_refs: bool = True
) -> list[str]:
    """Validate a list of nodes; check parent refs + layer parent chain (四層)."""
    errs: list[str] = []
    uids: list[str] = []
    seen: set[str] = set()
    by_uid: dict[str, dict] = {}
    for i, node in enumerate(nodes, start=1):
        errs.extend(validate_node(node, marks=marks, line_no=i))
        if isinstance(node, dict) and isinstance(node.get("uid"), str):
            uid = node["uid"]
            if uid in seen:
                errs.append(f"line {i}: duplicate uid {uid!r}")
            seen.add(uid)
            uids.append(uid)
            by_uid[uid] = node

    if check_parent_refs:
        uid_set = set(uids)
        for i, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                continue
            pu = node.get("parent_uid")
            if pu is None:
                continue
            if isinstance(pu, str) and pu not in uid_set:
                errs.append(f"line {i}: parent_uid {pu!r} not in this file")
                continue
            # layer parent must match 四層 chain
            child_layer = node.get("layer")
            if child_layer not in LAYER_PARENT_LAYER:
                continue
            expected_parent_layer = LAYER_PARENT_LAYER[child_layer]
            parent = by_uid.get(pu) if isinstance(pu, str) else None
            if parent is None:
                continue
            parent_layer = parent.get("layer")
            if expected_parent_layer is None:
                continue
            if parent_layer != expected_parent_layer:
                errs.append(
                    f"line {i}: layer={child_layer!r} parent must be "
                    f"layer={expected_parent_layer!r} "
                    f"(got parent {pu!r} layer={parent_layer!r}); "
                    "three-layer / skip-subitem trees are invalid"
                )
    return errs


def load_nodes_jsonl(path: Path, *, marks: set[str]) -> tuple[list[dict], list[str]]:
    """Load nodes.jsonl. Returns (nodes, errors)."""
    text = path.read_text(encoding="utf-8")
    nodes: list[dict] = []
    errs: list[str] = []
    if not text.strip():
        return [], ["empty nodes.jsonl"]

    for i, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("//"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            errs.append(f"line {i}: bad JSON ({e})")
            continue
        nodes.append(obj)

    errs.extend(validate_nodes(nodes, marks=marks))
    return nodes, errs


def node_to_jsonl(node: dict) -> str:
    return json.dumps(node, ensure_ascii=False, separators=(",", ":"))
