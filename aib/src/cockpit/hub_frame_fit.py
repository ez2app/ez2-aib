# SPDX-License-Identifier: Apache-2.0
# hub-frame 實畫必須 ≤ pane_height，否則 rowmap 的 line 會掉到可視區外，[≡] 點了沒反應。
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def paint_budget(pane_h: int, win_h: int = 0) -> int:
    """實畫列數上限：永遠以 pane_height 為硬頂（不是 window_height）。"""
    try:
        fh = int(pane_h or 0)
    except (TypeError, ValueError):
        fh = 0
    if fh > 0:
        return fh
    try:
        wh = int(win_h or 0)
    except (TypeError, ValueError):
        wh = 0
    return max(5, wh - 13) if wh else 0


def _cycle_lines(rowmap: List[dict]) -> set:
    out = set()
    for r in rowmap or []:
        if not isinstance(r, dict):
            continue
        try:
            ln = int(r.get("line") or 0)
        except (TypeError, ValueError):
            continue
        for link in r.get("links") or []:
            if not isinstance(link, dict):
                continue
            act = str(link.get("action") or "")
            if "visible-cycle" in act or act == "hub-io-toggle":
                out.add(ln)
                break
    return out


def clamp_paint(
    lines: List[str], rowmap: List[dict], pane_h: int
) -> Tuple[List[str], List[dict]]:
    """裁到 pane 高：先丟沒有 [≡] 熱區的中間列，盡量保住 dialog/realtime/input 的 cycle 列與 hub-io-toggle（含 [+] 重開條）。"""
    try:
        fh = int(pane_h or 0)
    except (TypeError, ValueError):
        fh = 0
    if fh <= 0 or len(lines) <= fh:
        return lines, rowmap

    protect = _cycle_lines(rowmap)
    keep = [True] * len(lines)
    # 從尾端往前丟非保護列（通常是 realtime 正文）
    for i in range(len(lines) - 1, -1, -1):
        if sum(keep) <= fh:
            break
        if i not in protect:
            keep[i] = False
    # 若仍超高（保護列太多），再從中段保護列開刀，但永遠留至少一列 dialog/input cycle
    if sum(keep) > fh:
        dialog = {i for i in protect if any(
            "dialog-visible-cycle" in str((L or {}).get("action") or "")
            for r in rowmap if isinstance(r, dict) and int(r.get("line") or -1) == i
            for L in (r.get("links") or [])
        )}
        input_ = {i for i in protect if any(
            "input-visible-cycle" in str((L or {}).get("action") or "")
            for r in rowmap if isinstance(r, dict) and int(r.get("line") or -1) == i
            for L in (r.get("links") or [])
        )}
        for i in range(len(lines) - 1, -1, -1):
            if sum(keep) <= fh:
                break
            if keep[i] and i in protect and i not in dialog and i not in input_:
                keep[i] = False

    new_lines: List[str] = []
    remap: Dict[int, int] = {}
    for i, line in enumerate(lines):
        if not keep[i]:
            continue
        remap[i] = len(new_lines)
        new_lines.append(line)

    new_map: List[dict] = []
    for r in rowmap or []:
        if not isinstance(r, dict):
            continue
        try:
            ln = int(r.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if ln not in remap:
            continue
        rr = dict(r)
        rr["line"] = remap[ln]
        new_map.append(rr)
    return new_lines, new_map
