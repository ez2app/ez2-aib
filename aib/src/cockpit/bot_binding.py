#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""「這一席綁哪條對話」只認一份:讀的人一律走 bound_id(待修清單第 15 件第 1 步;the maintainer 10-11「照提案做第 1 步」)。

兩個地方都記著綁定:
  · 席位檔 instances/<艙>/seats/w<N>.json —— 開席那一刻寫(claudebot claude_session_id、codexbot desktop_thread_id;
    grokbot/musebot 沒這欄)。
  · 橋的 bridge/<席 uuid>/status.json —— 橋活著時一直更新(席上 /bind、/unbind 改的是這裡)。
以前讀的人各挑一個讀 ⇒ 換綁後右欄印舊的、艙重開找不到綁定(10-09 a seat)。
判準:status.json 有這一家的綁定欄(值是 None 也算 = 解綁了)就信它;沒有狀態檔或沒這欄 ⇒ 退席位檔那一欄;都沒有 ⇒ 空。
本檔只讀,不改任何寫入。提案:~/.ez2-aib/pending/PROPOSAL-bot-binding-single-source-9fec7-1011.md。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# 席位檔裡記綁定的欄位(aib 開席時寫);grokbot/musebot 只寫在橋的 status.json
SEAT_FIELD = {"codexbot": "desktop_thread_id", "claudebot": "claude_session_id"}
# 橋的 status.json 裡記綁定的欄位:信箱橋三家(sand_bridge)寫 agent_id,codexbot 橋寫 desktop_thread_id
STATUS_FIELD = {"claudebot": "agent_id", "grokbot": "agent_id", "musebot": "agent_id", "codexbot": "desktop_thread_id"}


def _state_root(base=None) -> Path:
    if base:
        return Path(base)
    return Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib").expanduser()


def _safe_uuid(session_uuid) -> bool:
    return isinstance(session_uuid, str) and bool(session_uuid) and "/" not in session_uuid and ".." not in session_uuid


def read_status(session_uuid, base=None):
    """橋的 status.json(dict);讀不到、不是 dict、uuid 形狀怪 ⇒ None。"""
    if not _safe_uuid(session_uuid):
        return None
    try:
        d = json.loads((_state_root(base) / "bridge" / session_uuid / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def bound_id(provider, session_uuid, seat_rec=None, base=None, status=None) -> tuple[str, str]:
    """這一席現在綁的 id 與來源:(id, "status" | "seat" | "")。id 空 = 沒綁或量不到。
    status 可由呼叫端先讀好傳進來(省一次讀檔);None ⇒ 自己讀。不認得的 provider ⇒ ("", "")。"""
    sfield = STATUS_FIELD.get(provider or "")
    if not sfield:
        return "", ""
    st = status if isinstance(status, dict) else read_status(session_uuid, base)
    if isinstance(st, dict) and sfield in st:
        v = st.get(sfield)
        return (v if isinstance(v, str) else ""), "status"
    field = SEAT_FIELD.get(provider or "")
    v = seat_rec.get(field) if field and isinstance(seat_rec, dict) else None
    if isinstance(v, str) and v:
        return v, "seat"
    return "", ""
