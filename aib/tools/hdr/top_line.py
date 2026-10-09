#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""每席頂行五欄渲染(純函式)。

設計:docs/DESIGN-persona-autoload-0926.md §10.14。
固定順序:
  開始時刻 │ 主任務 │ the maintainer「同意」 │ 回報 👔@席 │ 偏離N·無主N

窄寬降級(永遠不截時刻與同意欄):
  1. 一行 ≤ width → 一行
  2. 不足 → 旗標移第二行
  3. 再不足 → 截主任務名稱加「…」
"""
from __future__ import annotations

import unicodedata
from typing import Any, Mapping, Optional, Sequence, Union

SEP = " │ "
DEFAULT_WIDTH = 100
TASK_NAME_MAX_CELLS = 56
ELLIPSIS = "…"

# 狀態符(可擴;未知退成 ·)
# 鍵／符必須與 tools/report/status-enum.json marks 一致(DESIGN §10.14／§10.12)。
# 不在此處讀檔(純函式、無 I/O);改 enum 時同步改這裡,測會對帳。
STATUS_GLYPH = {
    "done": "✅",
    "in_progress": "🔄",
    "pending": "🟡",
    "partial": "🟡",
    "blocked": "🔴",
    "not_doing": "⛔",
    "waiting_prereq": "⌛",
}


def cells(text: str) -> int:
    """顯示格數:CJK／全形算 2。"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        for ch in (text or "")
    )


def truncate_cells(text: str, max_cells: int) -> str:
    if max_cells <= 0:
        return ""
    if cells(text) <= max_cells:
        return text
    if max_cells <= cells(ELLIPSIS):
        return ELLIPSIS[:max_cells]
    budget = max_cells - cells(ELLIPSIS)
    out = []
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ELLIPSIS


def format_started_at(raw: str, *, now_year: Optional[int] = None) -> str:
    """席檔 task_started_at → `MM-DD HH:MM`(跨年才加年份)。空 → 量不到。"""
    text = (raw or "").strip()
    if not text:
        return "量不到"
    # ISO → MM-DD HH:MM
    if len(text) >= 16 and text[4] == "-" and text[10] in " T":
        year = text[0:4]
        mmdd = text[5:10]
        hhmm = text[11:16]
        if now_year is not None and year.isdigit() and int(year) != now_year:
            return "%s-%s %s" % (year, mmdd, hhmm)
        return "%s %s" % (mmdd, hhmm)
    # 已是 MM-DD HH:Mx
    if len(text) >= 11 and text[2] == "-" and text[5] == " ":
        return text[:11] if len(text) == 11 else text[:16]
    return truncate_cells(text, 16)


def format_task_cell(
    *,
    status: str = "",
    alias: str = "",
    name: str = "",
    name_max: int = TASK_NAME_MAX_CELLS,
) -> str:
    """狀態符 + 別名 + 名稱。未派 → 主任務:無(未派)。"""
    name = (name or "").strip()
    alias = (alias or "").strip()
    if not name and not alias:
        return "主任務:無(未派)"
    glyph = STATUS_GLYPH.get((status or "").strip(), "·")
    shown = truncate_cells(name or alias, name_max)
    if alias and name and alias != name:
        return "%s %s %s" % (glyph, alias, shown)
    if alias and not name:
        return "%s %s" % (glyph, truncate_cells(alias, name_max))
    return "%s %s" % (glyph, shown)


def format_approval_cell(
    *,
    approval_event_uid: str = "",
    human_hhmm: str = "",
    is_human: bool = False,
    scope_changed: bool = False,
) -> str:
    """真人同意欄。fail-closed。

    - 無 approval_event_uid ⇒ 未核准
    - is_human 預設 False;uid 無 meta／is_human 非明確 True ⇒ 未核准(永不印「同意」)
    - 明確 is_human=False 且有 hhmm ⇒ 艦長派・未經 the maintainer
    """
    uid = (approval_event_uid or "").strip()
    if not uid:
        return "未核准"
    # ★ fail-closed:只有明確 True 才印「同意」
    if is_human is not True:
        if not (human_hhmm or "").strip():
            return "未核准"
        return "艦長派・未經 the maintainer"
    hhmm = (human_hhmm or "").strip() or "??:??"
    base = "the maintainer %s「同意」" % hhmm
    if scope_changed:
        return base + "⚠ 範圍已變"
    return base


def format_report_cell(code: str) -> str:
    """回報 👔@席碼。字面固定「回報」。code 空 → 回報 👔@?"""
    code = (code or "").strip() or "?"
    if code.startswith("@"):
        code = code[1:]
    return "回報 👔@%s" % code


def format_flags_cell(*, drift_n: int = 0, orphan_n: int = 0) -> str:
    """偏離 N · 無主 N;都 0 也印。"""
    return "偏離 %d · 無主 %d" % (int(drift_n), int(orphan_n))


def render_top_line(
    *,
    started_at: str = "",
    task_status: str = "",
    task_alias: str = "",
    task_name: str = "",
    approval_event_uid: str = "",
    human_hhmm: str = "",
    is_human_approval: bool = False,
    scope_changed: bool = False,
    report_code: str = "",
    drift_n: int = 0,
    orphan_n: int = 0,
    width: int = DEFAULT_WIDTH,
    now_year: Optional[int] = None,
) -> str:
    """渲染頂行;必要時回兩行(旗標降級)。純函式。"""
    width = max(20, int(width))
    col1 = format_started_at(started_at, now_year=now_year)
    col3 = format_approval_cell(
        approval_event_uid=approval_event_uid,
        human_hhmm=human_hhmm,
        is_human=is_human_approval,
        scope_changed=scope_changed,
    )
    col4 = format_report_cell(report_code)
    col5 = format_flags_cell(drift_n=drift_n, orphan_n=orphan_n)

    def build(name_max: int, with_flags: bool) -> str:
        col2 = format_task_cell(
            status=task_status, alias=task_alias, name=task_name, name_max=name_max
        )
        cols = [col1, col2, col3, col4]
        if with_flags:
            cols.append(col5)
        return SEP.join(cols)

    # 1) 試一行全五欄、名稱滿額
    line = build(TASK_NAME_MAX_CELLS, True)
    if cells(line) <= width:
        return line

    # 2) 旗標移第二行
    main = build(TASK_NAME_MAX_CELLS, False)
    if cells(main) <= width:
        return main + "\n" + col5

    # 3) 再截名稱:留給時刻／同意／回報與分隔的預算
    fixed = cells(col1) + cells(col3) + cells(col4) + cells(SEP) * 3
    name_budget = max(4, width - fixed - 4)  # 4 ≈ 符＋空白餘裕
    # format_task_cell 內還有符／別名,這裡用較小 name_max 逼截斷
    for name_max in (name_budget, max(4, name_budget // 2), 4):
        main = build(name_max, False)
        if cells(main) <= width:
            return main + "\n" + col5

    # 最後手段:硬截整行(仍保時刻與同意不被單獨拆掉——已在 main 內)
    return truncate_cells(main, width) + "\n" + col5


def render_top_line_from_fields(
    fields: Mapping[str, str],
    *,
    task_meta: Optional[Mapping[str, str]] = None,
    approval_meta: Optional[Mapping[str, Any]] = None,
    report_code: str = "",
    drift_n: int = 0,
    orphan_n: int = 0,
    width: int = DEFAULT_WIDTH,
    now_year: Optional[int] = None,
) -> str:
    """便利入口:席檔四欄 + 計畫樹／核准展開資訊。"""
    meta = task_meta or {}
    appr = approval_meta or {}
    return render_top_line(
        started_at=str(fields.get("task_started_at", "")),
        task_status=str(meta.get("status", "")),
        task_alias=str(meta.get("alias", "")),
        task_name=str(meta.get("name", "")),
        approval_event_uid=str(fields.get("approval_event_uid", "")),
        human_hhmm=str(appr.get("hhmm", "")),
        is_human_approval=(appr.get("is_human", False) is True),
        scope_changed=bool(appr.get("scope_changed", False)),
        report_code=report_code,
        drift_n=drift_n,
        orphan_n=orphan_n,
        width=width,
        now_year=now_year,
    )
