# SPDX-License-Identifier: Apache-2.0
"""Filter + order aib PROVIDERS for 另開 AI engine menu.

Hides in_new_ai_menu=false, status retired/alias-only.
Orders: native → bot → api_key, with __SEP__ between non-empty groups.
"""
from __future__ import annotations

import json
import sys

SEP = "__SEP__"
GROUP_ORDER = ("native", "bot", "api_key")
BOT_IDS = frozenset({"sand", "grokbot", "codexbot", "claudebot", "musebot"})


def _rows(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    rows = data.get("providers") if isinstance(data, dict) else data
    return [r for r in (rows or []) if isinstance(r, dict)]


def _meta(rows):
    by = {}
    for r in rows:
        rid = r.get("id")
        if isinstance(rid, str) and rid:
            by[rid] = r
    return by


def _group_of(pid: str, row: dict | None) -> str:
    if row and row.get("menu_group") in GROUP_ORDER:
        return row["menu_group"]
    if pid in BOT_IDS:
        return "bot"
    if row and row.get("auth") == "api_key":
        return "api_key"
    return "native"


def _hidden(row: dict | None) -> bool:
    if not row:
        return False
    if row.get("in_new_ai_menu") is False:
        return True
    if row.get("status") in ("retired", "alias-only"):
        return True
    return False


def order_providers(raw: list[str], path: str) -> list[str]:
    meta = _meta(_rows(path))
    visible = [p for p in raw if p and not _hidden(meta.get(p))]
    buckets = {g: [] for g in GROUP_ORDER}
    for p in visible:
        buckets[_group_of(p, meta.get(p))].append(p)
    out: list[str] = []
    for g in GROUP_ORDER:
        if not buckets[g]:
            continue
        if out:
            out.append(SEP)
        out.extend(buckets[g])
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: filter_new_ai_providers.py <providers-string> <providers.json>", file=sys.stderr)
        return 2
    raw = argv[1].split()
    path = argv[2]
    try:
        print(" ".join(order_providers(raw, path)))
    except Exception:
        # fail-open: keep raw order, still drop nothing on hard error
        print(" ".join(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
