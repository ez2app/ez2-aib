#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""AI用量 unlock sub-tabs (用量 | Codex) — pure helpers.

Product decision 2026-09-22 (the maintainer):
- Hidden door: tap header title ≥3× within 3s → unlock only (no subtab change).
- After unlock: reveal sub-tab strip under tz/datetime (NOT a 3rd top-level right tab).
- Default subtab after unlock remains ``usage`` (label 用量); do not auto-enter Codex.
- When locked: do not draw any sub-tab row.
- Overview (用量): only active / currently-in-use binding usage.
- Codex sub-tab: multi-AI rebind/manage UI.
- Monetization gate: unlocked ⇒ failover B (auto account switch) may run; locked ⇒ refuse.

Display-width for CJK hit spans follows the cabin rowmap lesson (east-asian width = 2).
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

SUBTAB_USAGE = "usage"
SUBTAB_CODEX = "codex"
VALID_SUBTABS = (SUBTAB_USAGE, SUBTAB_CODEX)

# Display labels (terminal-simple cabin style)
LABEL_USAGE = "用量"
LABEL_CODEX = "Codex"

# Mouse / rowmap action
SUBTAB_ACTION = "usage-subtab"

# Future engines: list-driven; for now hardcode usage + codex when unlocked.
ENGINE_SUBTABS: Tuple[Tuple[str, str], ...] = (
    (SUBTAB_USAGE, LABEL_USAGE),
    (SUBTAB_CODEX, LABEL_CODEX),
)


def normalize_subtab(value: Any, default: str = SUBTAB_USAGE) -> str:
    """Return a valid subtab id; unknown → default."""
    if value is None:
        return default
    s = str(value).strip().lower()
    # accept Chinese label aliases
    if s in ("用量", "overview", "main"):
        return SUBTAB_USAGE
    if s in VALID_SUBTABS:
        return s
    return default


def failover_enabled(unlocked: Any) -> bool:
    """PLAN failover B may auto-switch only when usage-accounts feature is unlocked."""
    return bool(unlocked)


def ensure_store_defaults(doc: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Ensure unlocked/subtab keys exist; do not flip unlocked; do not change subtab on unlock."""
    if "unlocked" not in doc:
        doc["unlocked"] = False
    if "subtab" not in doc:
        doc["subtab"] = SUBTAB_USAGE
    else:
        doc["subtab"] = normalize_subtab(doc.get("subtab"))
    return doc


def accounts_sig(doc: Mapping[str, Any] | None) -> str:
    """帳號資料指紋。只換子頁不算資料變了，預載的另一頁還能直接用。"""
    if not isinstance(doc, Mapping):
        return ""
    import hashlib
    import json
    kept = {k: v for k, v in doc.items() if k != "subtab"}
    blob = json.dumps(kept, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def set_subtab(doc: MutableMapping[str, Any], subtab: str) -> str:
    """Set subtab on store doc; returns normalized value. Does not require unlocked."""
    ensure_store_defaults(doc)
    doc["subtab"] = normalize_subtab(subtab)
    return doc["subtab"]


def on_unlock(doc: MutableMapping[str, Any]) -> None:
    """Mark unlocked. MUST NOT change subtab (stay on 用量 / current)."""
    ensure_store_defaults(doc)
    # capture current before any mutation other than unlocked
    _keep = normalize_subtab(doc.get("subtab"))
    doc["unlocked"] = True
    doc["subtab"] = _keep


def panel_subtab_fields(doc: Mapping[str, Any]) -> dict:
    """Fields to merge into panel_payload."""
    unlocked = bool(doc.get("unlocked"))
    subtab = normalize_subtab(doc.get("subtab"))
    return {
        "unlocked": unlocked,
        "subtab": subtab,
        "failover_enabled": failover_enabled(unlocked),
        "subtabs": [
            {"id": sid, "label": label, "active": (sid == subtab)}
            for sid, label in ENGINE_SUBTABS
        ]
        if unlocked
        else [],
    }


def display_width(text: str) -> int:
    """Terminal display width: CJK / fullwidth ≈ 2, ASCII ≈ 1 (rowmap lesson)."""
    w = 0
    for ch in text:
        o = ord(ch)
        # rough east-asian / fullwidth
        if (
            o >= 0x1100
            and (
                o <= 0x115F
                or 0x2E80 <= o <= 0xA4CF
                or 0xAC00 <= o <= 0xD7A3
                or 0xF900 <= o <= 0xFAFF
                or 0xFE10 <= o <= 0xFE19
                or 0xFE30 <= o <= 0xFE6F
                or 0xFF00 <= o <= 0xFF60
                or 0xFFE0 <= o <= 0xFFE6
                or 0x1F300 <= o <= 0x1F64F
                or 0x1F900 <= o <= 0x1F9FF
            )
        ):
            w += 2
        else:
            w += 1
    return w


def format_subtab_row(
    *,
    unlocked: bool,
    active_subtab: str,
    sep: str = " | ",
) -> Optional[str]:
    """Plain text sub-tab strip, or None when locked (do not draw stubs)."""
    if not unlocked:
        return None
    active = normalize_subtab(active_subtab)
    parts: List[str] = []
    for sid, label in ENGINE_SUBTABS:
        if sid == active:
            parts.append("[%s]" % label)
        else:
            parts.append(label)
    # 與下方引擎列同樣左縮 1 格，對齊圖示欄
    return " " + sep.join(parts)


def subtab_link_spans(
    row_text: str,
    *,
    unlocked: bool,
    active_subtab: str,
    sep: str = " | ",
) -> List[dict]:
    """Rowmap hit spans for each sub-tab label (display-width aware cols).

    ``col`` / ``len`` are **display** columns (CJK width=2), matching cabin rowmap.
    """
    if not unlocked or not row_text:
        return []
    spans: List[dict] = []
    # Rebuild expected tokens to locate in row_text
    active = normalize_subtab(active_subtab)
    cursor = 0  # display col
    first = True
    for sid, label in ENGINE_SUBTABS:
        if not first:
            # advance past sep in display width
            cursor += display_width(sep)
        first = False
        token = "[%s]" % label if sid == active else label
        # find token starting at current text offset roughly — scan by display
        # We trust format_subtab_row layout: locate token string in row_text
        idx = row_text.find(token)
        if idx < 0:
            # fallback: advance by token display width anyway
            cursor += display_width(token)
            continue
        # compute display col of idx
        col = display_width(row_text[:idx])
        length = display_width(token)
        spans.append(
            {
                "action": SUBTAB_ACTION,
                "subtab": sid,
                "arg": sid,
                "label": label,
                "col": col,
                "len": length,
            }
        )
        cursor = col + length
    return spans


def should_emit_accounts_panel(*, unlocked: bool, subtab: str) -> bool:
    """Accounts/rebind panel only on Codex sub-tab while unlocked."""
    return bool(unlocked) and normalize_subtab(subtab) == SUBTAB_CODEX


def should_emit_overview(*, unlocked: bool, subtab: str) -> bool:
    """Overview rows when locked OR subtab==usage."""
    if not unlocked:
        return True
    return normalize_subtab(subtab) == SUBTAB_USAGE


def filter_active_codex_only(
    accounts: Sequence[Mapping[str, Any]],
    *,
    active_id: Any = None,
) -> List[Mapping[str, Any]]:
    """Overview: only the active / currently-in-use binding (not all multi-account bars)."""
    if not accounts:
        return []
    flagged = [
        a for a in accounts
        if isinstance(a, Mapping) and (a.get("is_active") or a.get("active"))
    ]
    if flagged:
        return flagged
    aid = str(active_id) if active_id not in (None, "") else None
    if aid:
        by_id = [
            a for a in accounts
            if isinstance(a, Mapping)
            and (
                str(a.get("id") or "") == aid
                or str(a.get("account_id") or "") == aid
            )
        ]
        if by_id:
            return by_id
    for a in accounts:
        if isinstance(a, Mapping) and (
            a.get("is_desktop") or a.get("primary") or a.get("desktop")
        ):
            return [a]
    return []


def guard_auto_failover(*, unlocked: bool, reason: str = "auto-switch") -> Tuple[bool, str]:
    """Return (allowed, message). Locked ⇒ refuse auto-switch (PLAN B gate)."""
    if failover_enabled(unlocked):
        return True, "ok"
    return (
        False,
        "failover B refused: usage-accounts feature locked (unlock AI用量 header first); reason=%s"
        % reason,
    )
