#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席兩回執字面與缺一標紅判準。

設計:docs/DESIGN-persona-autoload-0926.md §10.6。
AI 要回兩句:「已載入人格 <uid>」「已接手 <專案> 從 <時間>」;
人格那句另認人格包(src/identity/persona_pack.py)實際要求的「人格已載入 <人格檔 sha256 前 8 碼>」,
兩種寫法任一句在即算已載入(艦長 09-27 00:28 B r1 修:人格包要的那句本檔原本認不得 ⇒ 照做的 AI 永遠被標紅)。
少一句右欄標紅。本檔純判準,不碰右欄渲染活路。
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

PERSONA_ACK_PREFIX = "已載入人格 "
# 人格包的那句:詞序不同、值是人格檔指紋(小寫十六進位 8 碼)不是 uid。只認這個形狀,不放寬成任意字。
PACK_PERSONA_ACK_PREFIX = "人格已載入 "
HANDOFF_ACK_PREFIX = "已接手 "
HANDOFF_ACK_FROM = " 從 "

# 允許尾端標點／空白;uid／專案名鬆綁(含中文專案名)。
_PERSONA_ACK_RE = re.compile(
    r"^已載入人格\s+(\S.*?)\s*$",
)
_PACK_PERSONA_ACK_RE = re.compile(r"^人格已載入\s+([0-9a-f]{8})\s*$")
_HANDOFF_ACK_RE = re.compile(
    r"^已接手\s+(.+?)\s+從\s+(\S.*?)\s*$",
)


def persona_ack_literal(persona_uid: str) -> str:
    uid = (persona_uid or "").strip()
    if not uid:
        raise ValueError("persona_uid 不可空")
    return PERSONA_ACK_PREFIX + uid


def handoff_ack_literal(project: str, from_time: str) -> str:
    proj = (project or "").strip()
    when = (from_time or "").strip()
    if not proj:
        raise ValueError("專案名／uid 不可空")
    if not when:
        raise ValueError("接手起點時間不可空")
    return HANDOFF_ACK_PREFIX + proj + HANDOFF_ACK_FROM + when


def parse_persona_ack(line: str) -> Optional[str]:
    m = _PERSONA_ACK_RE.match((line or "").strip())
    return m.group(1).strip() if m else None


def parse_pack_persona_ack(line: str) -> Optional[str]:
    """人格包那句 ⇒ 回 8 碼指紋;形狀不對(非 8 碼小寫十六進位)⇒ None。"""
    m = _PACK_PERSONA_ACK_RE.match((line or "").strip())
    return m.group(1) if m else None


def parse_handoff_ack(line: str) -> Optional[tuple[str, str]]:
    m = _HANDOFF_ACK_RE.match((line or "").strip())
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def judge_open_acks(
    lines: list[str] | tuple[str, ...] | None,
    *,
    expect_persona_uid: str = "",
    expect_project: str = "",
    expect_persona_sha8: str = "",
) -> dict:
    """純判準:兩句是否齊、缺哪一句、是否該標紅。

    回傳:
      ok: bool                 —— 兩句都在(且若有 expect_* 則對得上)
      red: bool                —— 缺一或對不上 ⇒ True(右欄應標紅)
      has_persona_ack: bool
      has_handoff_ack: bool
      persona_uid: str|None    —— 「已載入人格 <uid>」那句的值
      persona_sha8: str|None   —— 人格包「人格已載入 <sha8>」那句的值(兩句值的種類不同,不互比)
      project: str|None
      from_time: str|None
      missing: list[str]       —— 'persona' / 'handoff' / 'persona_mismatch' / ...
    """
    bag = [((ln or "").strip()) for ln in (lines or []) if (ln or "").strip()]
    persona_uid = None
    persona_sha8 = None
    project = None
    from_time = None
    for ln in bag:
        if persona_uid is None:
            persona_uid = parse_persona_ack(ln)
        if persona_sha8 is None:
            persona_sha8 = parse_pack_persona_ack(ln)
        if project is None:
            got = parse_handoff_ack(ln)
            if got:
                project, from_time = got

    missing: list[str] = []
    has_p = persona_uid is not None or persona_sha8 is not None
    has_h = project is not None
    if not has_p:
        missing.append("persona")
    if not has_h:
        missing.append("handoff")

    exp_p = (expect_persona_uid or "").strip()
    exp_proj = (expect_project or "").strip()
    exp_sha8 = (expect_persona_sha8 or "").strip()
    # uid 只跟 uid 比、指紋只跟指紋比;回的是另一種就不比(比不了,不當成對不上)。
    if persona_uid is not None and exp_p and persona_uid != exp_p:
        missing.append("persona_mismatch")
    if persona_sha8 is not None and exp_sha8 and persona_sha8 != exp_sha8:
        missing.append("persona_sha8_mismatch")
    if has_h and exp_proj and project != exp_proj:
        missing.append("project_mismatch")

    ok = not missing
    return {
        "ok": ok,
        "red": not ok,
        "has_persona_ack": has_p,
        "has_handoff_ack": has_h,
        "persona_uid": persona_uid,
        "persona_sha8": persona_sha8,
        "project": project,
        "from_time": from_time,
        "missing": missing,
    }


def is_red(result: Mapping) -> bool:
    """缺一標紅的捷徑。"""
    return bool(result.get("red"))


# ── 開席投遞狀態(右欄紅訊號半成品;活路綁定待艙) ─────────────────
# delivered=false / 「未送」⇒ 右欄應標紅。本檔只給欄位與判準,不碰 pane-right。

DELIVERY_NOT_SENT = "未送"
OPEN_ACK_KEYS = (
    "delivered",
    "asked_at",
    "answered_at",
    "persona_ack",
    "handoff_ack",
    "project_uid",
    "signal",
)


def empty_delivery_state() -> dict:
    """開席接手投遞狀態初值:未送 ⇒ 右欄紅訊號。"""
    return {
        "delivered": False,
        "asked_at": "",
        "answered_at": "",
        "persona_ack": False,
        "handoff_ack": False,
        "project_uid": "",
        "signal": DELIVERY_NOT_SENT,
    }


def mark_pack_delivered(
    state: Mapping | None,
    *,
    project_uid: str = "",
    asked_at: str = "",
) -> dict:
    """接手包已寫進開場說明後呼叫。delivered=True;兩回執仍未收到。"""
    out = empty_delivery_state()
    if isinstance(state, Mapping):
        out.update({k: state.get(k, out[k]) for k in OPEN_ACK_KEYS if k in state})
    out["delivered"] = True
    out["project_uid"] = (project_uid or out.get("project_uid") or "").strip()
    out["asked_at"] = (asked_at or out.get("asked_at") or "").strip()
    out["signal"] = ""  # 已送,等回執;缺回執由 judge 標紅
    return out


def mark_pack_missing(state: Mapping | None = None) -> dict:
    """開席未送接手包 ⇒ delivered=false + 「未送」訊號。"""
    out = empty_delivery_state()
    if isinstance(state, Mapping):
        out.update({k: state.get(k, out[k]) for k in OPEN_ACK_KEYS if k in state})
    out["delivered"] = False
    out["signal"] = DELIVERY_NOT_SENT
    return out


def apply_ack_lines(
    state: Mapping | None,
    lines: list[str] | tuple[str, ...] | None,
    *,
    expect_persona_uid: str = "",
    expect_project: str = "",
    answered_at: str = "",
    expect_persona_sha8: str = "",
) -> dict:
    """把 AI 回執線餵進狀態;缺一 ⇒ red / signal 對齊右欄。"""
    base = empty_delivery_state()
    if isinstance(state, Mapping):
        base.update({k: state.get(k, base[k]) for k in OPEN_ACK_KEYS if k in state})
    judged = judge_open_acks(
        lines,
        expect_persona_uid=expect_persona_uid,
        expect_project=expect_project or str(base.get("project_uid") or ""),
        expect_persona_sha8=expect_persona_sha8,
    )
    base["persona_ack"] = bool(judged.get("has_persona_ack"))
    base["handoff_ack"] = bool(judged.get("has_handoff_ack"))
    if answered_at and judged.get("ok"):
        base["answered_at"] = answered_at
    if not base.get("delivered"):
        base["signal"] = DELIVERY_NOT_SENT
    elif judged.get("red"):
        base["signal"] = "缺回執"
    else:
        base["signal"] = ""
    base["judge"] = judged
    base["red"] = (not base.get("delivered")) or bool(judged.get("red"))
    return base


def right_col_red_signal(state: Mapping | None) -> dict:
    """右欄消費者可讀的精簡訊號。右欄活路待艙綁定 —— 本函式只產資料。"""
    st = state if isinstance(state, Mapping) else empty_delivery_state()
    delivered = bool(st.get("delivered"))
    signal = str(st.get("signal") or (DELIVERY_NOT_SENT if not delivered else ""))
    red = (not delivered) or bool(st.get("red")) or signal == DELIVERY_NOT_SENT
    return {
        "red": red,
        "delivered": delivered,
        "signal": signal,
        "asked_at": st.get("asked_at") or "",
        "answered_at": st.get("answered_at") or "",
    }
