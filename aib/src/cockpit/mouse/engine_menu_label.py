#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""tmux display-menu name styles for 另開 AI engine rows.

Uses providers.json menu_label for display casing (id stays lowercase).
Colour from render_lib ENGINE_LETTER_COLORS so badge and menu stay in sync.
Usage: engine_menu_label.py <provider> [<provider> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COCKPIT = HERE.parent
RENDER = COCKPIT / "render"
if str(RENDER) not in sys.path:
    sys.path.insert(0, str(RENDER))

from render_lib import ENGINE_LETTER_COLORS  # noqa: E402

_LABEL_CACHE: dict[str, str] | None = None


def _menu_labels() -> dict[str, str]:
    global _LABEL_CACHE
    if _LABEL_CACHE is not None:
        return _LABEL_CACHE
    path = COCKPIT / "providers.json"
    out: dict[str, str] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("providers") if isinstance(data, dict) else data
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            lab = r.get("menu_label")
            if isinstance(rid, str) and isinstance(lab, str) and lab.strip():
                out[rid] = lab.strip()
    except Exception:
        out = {}
    _LABEL_CACHE = out
    return out


def display_name(provider: str) -> str:
    name = (provider or "").strip()
    if not name:
        return ""
    return _menu_labels().get(name) or name


def style_label(provider: str) -> str:
    name = (provider or "").strip()
    if not name:
        return ""
    text = display_name(name)
    cols = ENGINE_LETTER_COLORS.get(name) or ()
    if not cols:
        return text
    c256 = cols[0][1]
    return "#[fg=colour%d]%s#[default]" % (c256, text)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: engine_menu_label.py <provider>...", file=sys.stderr)
        return 2
    for raw in argv[1:]:
        print(style_label(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
