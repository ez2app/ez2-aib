#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""計畫樹展開渲染（Wave2 包 E｜展開與底色）。

for_task: expand-evening-e-0927 / DISPATCH-1857a 包 E

開關：環境變數 AIB_PROJECT_EXPAND=1 才啟用；未設／非 1 ⇒ 關閉（回空字串）。

行為摘要：
  - free tier：只印大項名一層（depth 0／根層 title／alias／uid）。
  - paid（single｜all）：真實遞迴最多四層（depth 0..3）。F 四層遞迴已到位，
    不再以內縮佔位模擬更深層；超出四層的更深節點不印。
  - 預設展開「本席那條路」：owner／seat_id 對齊的節點及其祖先展開；
    其他分支只顯示到與本席路徑的交叉點（根層仍可見）。
  - display=False（預設／資料層）：**零 ANSI**；寫檔／JSON 用這條。
  - display=True（顯示層）：本席行可加席位底色 ANSI。

不改 show.py；節點讀取重用 schema_v0.load_nodes_jsonl。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_RENDER_DIR = _REPO / "src" / "cockpit" / "render"
_SRC = _REPO / "src"

for p in (_HERE, str(_RENDER_DIR), str(_SRC)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from schema_v0 import (  # noqa: E402
    load_nodes_jsonl,
    load_status_marks,
    repo_root_from_script,
)

from seat_colors import paint_seat_bg, strip_ansi  # noqa: E402

EXPAND_ENV = "AIB_PROJECT_EXPAND"
PAID_TIERS = frozenset(("single", "all"))
DISPLAY_DEPTH_CAP = 4  # paid 顯示層數上限（含深度 0，即 depth 0..3）


def expand_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """AIB_PROJECT_EXPAND=1 才 True；未設／空／其他值皆 False。"""
    e = os.environ if env is None else env
    raw = e.get(EXPAND_ENV)
    if raw is None:
        return False
    return str(raw).strip() == "1"


def _seat_matches(owner: Any, seat_id: str | None) -> bool:
    if not seat_id:
        return False
    if not isinstance(owner, str):
        return False
    o = owner.strip()
    s = str(seat_id).strip()
    if not o or not s:
        return False
    if o == s:
        return True
    # @seatA ↔ seatA ↔ A
    o_norm = o[1:] if o.startswith("@") else o
    s_norm = s[1:] if s.startswith("@") else s
    if o_norm.lower() == s_norm.lower():
        return True
    if o_norm.lower() == s_norm.lower().removeprefix("seat"):
        return True
    if s_norm.lower() == o_norm.lower().removeprefix("seat"):
        return True
    # provider substring (owner="claude", seat="claude-1")
    if o_norm.lower() in s_norm.lower() or s_norm.lower() in o_norm.lower():
        return True
    return False


def _node_label(node: dict) -> str:
    title = node.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    aliases = node.get("alias") or []
    if isinstance(aliases, list):
        for a in aliases:
            if isinstance(a, str) and a.strip():
                return a.strip()
    uid = node.get("uid")
    if isinstance(uid, str) and uid.strip():
        return uid.strip()
    return "?"


def _build_index(nodes: Sequence[dict]):
    by_uid: dict[str, dict] = {}
    by_parent: dict[Any, list[dict]] = defaultdict(list)
    for n in nodes:
        if not isinstance(n, dict):
            continue
        uid = n.get("uid")
        if not isinstance(uid, str) or not uid:
            continue
        by_uid[uid] = n
        by_parent[n.get("parent_uid")].append(n)
    for kids in by_parent.values():
        kids.sort(key=lambda x: str(x.get("uid", "")))
    return by_uid, by_parent


def _ancestors(uid: str, by_uid: Mapping[str, dict]) -> list[str]:
    """uid 本身 + 祖先鏈（根在前、自己在後）。"""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = uid
    while cur and cur in by_uid and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        parent = by_uid[cur].get("parent_uid")
        cur = parent if isinstance(parent, str) else None
    chain.reverse()
    return chain


def _seat_path_uids(nodes: Sequence[dict], seat_id: str | None) -> set[str]:
    """本席路徑：owner 匹配節點 ∪ 其祖先。"""
    by_uid, _ = _build_index(nodes)
    path: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if not _seat_matches(n.get("owner"), seat_id):
            continue
        uid = n.get("uid")
        if not isinstance(uid, str):
            continue
        for a in _ancestors(uid, by_uid):
            path.add(a)
    return path


def _is_paid(tier: str | None) -> bool:
    if not tier:
        return False
    return str(tier).strip().lower() in PAID_TIERS


def render_expand_tree(
    nodes: Sequence[dict] | Path | str,
    *,
    seat_id: str | None = None,
    entitlement_tier: str | None = None,
    display: bool = False,
    env: Optional[Mapping[str, str]] = None,
    marks: Optional[set[str]] = None,
) -> str:
    """渲染展開樹字串。

    nodes：list[dict] 或 jsonl path。
    display=False ⇒ 保證輸出無 \\x1b；display=True 時本席行可帶底色。
    開關關閉時回傳 ""。
    """
    if not expand_enabled(env):
        return ""

    loaded: list[dict]
    if isinstance(nodes, (str, Path)):
        path = Path(nodes).expanduser()
        repo = repo_root_from_script(__file__)
        m = marks if marks is not None else load_status_marks(repo)
        loaded, errs = load_nodes_jsonl(path, marks=m)
        if errs:
            raise ValueError("nodes load errors: " + "; ".join(errs[:5]))
    else:
        loaded = [n for n in nodes if isinstance(n, dict)]

    paid = _is_paid(entitlement_tier)
    seat_path = _seat_path_uids(loaded, seat_id) if seat_id else set()
    by_uid, by_parent = _build_index(loaded)

    lines: list[str] = []

    def emit(depth: int, text: str, *, is_seat_row: bool) -> None:
        indent = "  " * depth
        raw = f"{indent}{text}"
        if display and is_seat_row and seat_id:
            lines.append(paint_seat_bg(raw, seat_id))
        else:
            lines.append(raw)

    def walk(parent: Any, depth: int, on_seat_path: bool) -> None:
        kids = by_parent.get(parent, [])
        if not kids:
            return
        for n in kids:
            uid = n.get("uid")
            if not isinstance(uid, str):
                continue
            on_path = (not seat_path) or (uid in seat_path)
            # 非本席路徑：只在根層（depth 0）露出交叉點；更深的旁支收合。
            if seat_path and not on_path and depth > 0:
                continue
            is_seat = _seat_matches(n.get("owner"), seat_id)
            label = _node_label(n)
            owner = n.get("owner") or ""
            extra = f" — {owner}" if owner else ""
            emit(depth, f"{label}{extra}", is_seat_row=is_seat)

            if not paid:
                # free：只印大項一層，不往下走
                continue

            # paid：真實遞迴至 DISPLAY_DEPTH_CAP（F 四層遞迴已到位）
            next_depth = depth + 1
            if next_depth < DISPLAY_DEPTH_CAP:
                walk(uid, next_depth, on_path)

    walk(None, 0, True)

    text = "\n".join(lines)
    if lines:
        text += "\n"

    if not display:
        # 資料層硬保證零 ANSI
        text = strip_ansi(text)
        if "\x1b" in text:
            text = text.replace("\x1b", "")
    return text


def _resolve_tier(cli_tier: str | None, env: Mapping[str, str]) -> str:
    if cli_tier:
        return cli_tier.strip().lower()
    try:
        from entitlement import get_entitlement  # lazy; needs src on path

        info = get_entitlement(env)
        return str(info.get("tier") or "free")
    except Exception:
        return "free"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AIB project expand tree renderer (Wave2 E)")
    ap.add_argument("--nodes", required=True, help="path to nodes.jsonl")
    ap.add_argument("--seat", default="", help="seat id / owner to highlight & expand")
    ap.add_argument(
        "--display",
        action="store_true",
        help="顯示層：本席行可帶底色 ANSI（預設資料層零 ANSI）",
    )
    ap.add_argument(
        "--tier",
        default="",
        help="override entitlement tier (free|single|all); default=get_entitlement()",
    )
    ap.add_argument(
        "--repo-root",
        default="",
        help="repo root for status-enum (default: autodect from script)",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    env = dict(os.environ)
    if not expand_enabled(env):
        print(
            f"{EXPAND_ENV} is not 1; expand disabled (set {EXPAND_ENV}=1 to enable)",
            file=sys.stderr,
        )
        return 0

    repo = Path(args.repo_root).resolve() if args.repo_root else repo_root_from_script(__file__)
    marks = load_status_marks(repo)
    tier = _resolve_tier(args.tier or None, env)
    try:
        out = render_expand_tree(
            Path(args.nodes),
            seat_id=args.seat or None,
            entitlement_tier=tier,
            display=bool(args.display),
            env=env,
            marks=marks,
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
