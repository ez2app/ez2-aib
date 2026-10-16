#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話的在線狀態(開發文件第 30.3 節):本機公開席 ⇒ 給好友看的四選一狀態。只讀、不碰 tmux。

狀態怎麼量(跟艦長室同一套精神,但只用開源核心已有的檔):
  - 心跳(seat_now 的 <席>.beat:狀態列 / 底框定時重畫才會跳)距今 < ALIVE_S
      且 📌 狀態是 busy / reply / decide ⇒ online;是 idle(aib now --clear)或沒回報 ⇒ idle
  - 心跳 ALIVE_S–AWAY_S ⇒ away;更久、沒心跳、席已不在 ⇒ asleep
只在對方分享等級是 status 時才帶 📌 那一句(now)與步數(step);那一句只給人看。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

STATES = ("online", "idle", "away", "asleep")
ALIVE_S = 180
AWAY_S = 1800
NOW_MAX = 80


def clean_now(text):
    t = "".join(" " if ch in "\r\n\t" else ch for ch in str(text or "") if ch in "\r\n\t" or ord(ch) >= 32)
    return t.strip()[:NOW_MAX] or None


def seat_state(code, *, state_root=None, now=None, live=None):
    import seat_now as SN
    now = time.time() if now is None else now
    if live is not None and code not in live:
        return "asleep"
    age = SN.beat_age(code, state_root=state_root, now=now)
    if age is None or age[0] >= AWAY_S:
        return "asleep"
    if age[0] >= ALIVE_S:
        return "away"
    rec = SN.read(code, state_root=state_root) or {}
    return "online" if rec.get("state") in ("busy", "reply", "decide") else "idle"


def presence(codes, *, with_status=False, state_root=None, now=None, live=None):
    """[{seat, state[, now, step]}](codes 照順序)。"""
    import seat_now as SN
    out = []
    for code in codes:
        row = {"seat": code, "state": seat_state(code, state_root=state_root, now=now, live=live)}
        if with_status:
            rec = SN.read(code, state_root=state_root) or {}
            row["now"] = clean_now(rec.get("item"))
            d, t = rec.get("steps_done"), rec.get("steps_total")
            row["step"] = "%d/%d" % (d, t) if isinstance(d, int) and isinstance(t, int) and t > 0 else None
        out.append(row)
    return out
