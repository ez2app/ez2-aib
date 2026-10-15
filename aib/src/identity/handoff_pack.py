#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""接手包渲染:從專案四層(＋人格指紋)產出純文字包。

設計:docs/DESIGN-persona-autoload-0926.md §10.5–10.6。
開席送兩包之一;85% CTX 那次同時產。本檔純函式,可含 CTX 預算截斷。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from project_card import (  # noqa: E402  同目錄相對慣例:呼叫端把 identity 放進 path
    LAYER_ZH,
    validate_project_card,
    validate_project_uid,
)

HEADER = "[AIB 接手包 v1]"
DEFAULT_CTX_BUDGET = 3500  # UTF-8 bytes;低於人格包 4000,留給兩句回執


def _as_lines(items: Sequence[Any], bullet: str = "- ") -> list[str]:
    out = []
    for it in items or []:
        if isinstance(it, Mapping):
            text = str(it.get("text") or it.get("uid") or it).strip()
        else:
            text = str(it).strip()
        if text:
            out.append(bullet + text)
    return out


def _section(title: str, body_lines: Sequence[str]) -> str:
    if not body_lines:
        return "## %s\n(空)" % title
    return "## %s\n%s" % (title, "\n".join(body_lines))


def render_handoff_pack(
    project_card: Mapping[str, Any],
    *,
    persona_uid: str = "",
    persona_version: str = "",
    last_event_at: str = "",
    ctx_budget_bytes: int = DEFAULT_CTX_BUDGET,
    extra_notes: Optional[Sequence[str]] = None,
    memory_notes: Optional[Sequence[str]] = None,
) -> str:
    """把專案卡四層壓成一段純文字接手包。

    memory_notes(10-15 H1):`aib memory write --about <專案 uid>` 寫的筆記指標(種類 · 標題 · uid · 怎麼讀),
    接在專案記憶段後面;以前接手包只看卡上的 memory 層,席位看到「記憶空」而交接筆記其實在。

    超過 ctx_budget_bytes(UTF-8)時由下往上丟:extra_notes → 記憶最舊 →
    工作狀態 pending → 憲法目標;紅線與專案 uid／人格指紋最後才動。
    """
    card = validate_project_card(project_card)
    uid = card["project_uid"]
    layers = card["layers"]
    constitution = layers["constitution"]
    memory = layers["memory"]
    work = layers["work_state"]

    persona = (persona_uid or "").strip() or "(未標)"
    pver = (persona_version or "").strip() or "?"
    last_at = (last_event_at or "").strip() or "(無歷程)"

    head = [
        HEADER,
        "專案: %s" % (card.get("title") or uid),
        "project_uid: %s" % uid,
        "用人格: %s v%s" % (persona, pver),
    ]
    # 09-30 a seat:專案記了對應的資料夾(aib newproj --folder)就寫出來 —— 專案的筆記放那裡。
    #   沒記就不印這一行(舊卡、選單建的卡都沒有這兩欄),其餘每一行一個字都不變。
    if str(card.get("folder") or "").strip():
        head.append("資料夾: %s(%s)" % (str(card["folder"]).strip(), str(card.get("route_key") or "").strip() or "?"))
    head += [
        "最後一筆: %s" % last_at,
        "現在在哪: %s" % (work.get("now") or "(未填)"),
        "待誰: %s" % (", ".join(work.get("owners") or []) or "(未填)"),
        "下一步: %s" % (work.get("next_step") or "(未填)"),
    ]

    parts = [
        "\n".join(head),
        _section(LAYER_ZH["constitution"] + "・紅線",
                 _as_lines(constitution.get("red_lines") or [])),
        _section(LAYER_ZH["constitution"] + "・目標",
                 _as_lines(constitution.get("goals") or [])),
        _section(LAYER_ZH["constitution"] + "・the maintainer 裁定",
                 _as_lines(constitution.get("human_rulings") or [])),
        _section(LAYER_ZH["work_state"] + "・待裁",
                 _as_lines(work.get("pending") or [])),
        _section(LAYER_ZH["memory"],
                 _as_lines(memory.get("entries") or [])),
    ]
    if memory_notes:
        parts.append(_section(LAYER_ZH["memory"] + "・aib memory(最新;全文用 aib memory show --uid <uid>)",
                              _as_lines(list(memory_notes))))
    if extra_notes:
        parts.append(_section("附註", _as_lines(list(extra_notes))))

    text = "\n\n".join(parts).strip() + "\n"
    return _truncate_to_budget(text, max(64, int(ctx_budget_bytes)))


def _truncate_to_budget(text: str, budget: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    # 保頭:HEADER + 專案列;其餘從尾截並加標記
    marker = "\n…(接手包已截至 CTX 預算 %dB)\n" % budget
    keep = budget - len(marker.encode("utf-8"))
    if keep < 32:
        return marker.lstrip()
    cut = raw[:keep].decode("utf-8", errors="ignore").rstrip()
    # 避免截在半行:退到上一個換行
    nl = cut.rfind("\n")
    if nl >= 32:
        cut = cut[:nl]
    return cut + marker


def handoff_mentions_project(text: str, project_uid: str) -> bool:
    """尺用:渲染結果是否帶此 project_uid。"""
    try:
        uid = validate_project_uid(project_uid)
    except Exception:
        return False
    return uid in (text or "")



def build_handoff_for_seat(
    project_card: Mapping[str, Any] | None,
    *,
    persona_uid: str = "",
    persona_version: str = "",
    last_event_at: str = "",
    memory_notes: Optional[Sequence[str]] = None,
) -> str:
    """開席投遞用:卡缺 ⇒ 空字串(呼叫端記 delivered=false)。"""
    if not project_card:
        return ""
    try:
        return render_handoff_pack(
            project_card,
            persona_uid=persona_uid,
            persona_version=persona_version,
            last_event_at=last_event_at,
            memory_notes=memory_notes,
        )
    except Exception:
        return ""
