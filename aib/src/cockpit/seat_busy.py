#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""seat_busy.py —— 「這一席正在輸出」的提示字(2026-10-10 the maintainer 第 4 項)。

為什麼:macOS 27 Terminal 2.15 在「有組字區 + 收到新畫面」時會當掉/視窗不見(見 pane-tick.sh 組字停火一段)。
AI 席正在串流回答時,它的畫面每 0.1 秒重畫,是曝險最高的時刻,而停火只管得到座艙面板、管不到席本身的輸出。
所以在訊息輸入區的抬頭上提醒一句:這一席正在輸出,先別打中文——人看到就等它停。

怎麼量:activity_probe.activity_age(provider, session_uuid)= 該席對話檔最後一次變動距今幾秒(claude 看 transcript
mtime、codex 看 rollout、…)。小於 BUSY_S 秒就算「輸出中」。這是 pane-hub-frame 每秒一拍裡的一次 stat,零 fork。
失效側一律**不提示**:席碼對不到席位檔、探針回 None、任何例外 ⇒ 空字串(多一句假提醒比少一句真提醒糟:
提醒多了人就不看了)。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

BUSY_S = 3.0
NOTICE = "✍ 輸出中,先別打中文"


def seat_record(code, seats_dir):
    """席碼(五碼)→ 席位檔內容;找不到回 None。用 seat_address.uuid_tail5,不自己切尾五碼。"""
    if not code or not seats_dir:
        return None
    try:
        from seat_address import uuid_tail5
    except Exception:
        return None
    for p in sorted(glob.glob(os.path.join(seats_dir, "w*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("exited_at"):
            continue
        if uuid_tail5(rec.get("session_uuid") or "") == code:
            return rec
    return None


def busy_notice(code, seats_dir, now=None, age_fn=None, busy_s=BUSY_S):
    """回提示字或空字串。age_fn(provider, uuid, now) 可注入(測試);預設 activity_probe.activity_age。"""
    try:
        rec = seat_record(code, seats_dir)
        if not rec:
            return ""
        if age_fn is None:
            import activity_probe
            age_fn = activity_probe.activity_age
        now = time.time() if now is None else now
        age = age_fn(rec.get("provider") or "", rec.get("session_uuid") or "", now)
        if age is None:
            return ""
        return NOTICE if float(age) < float(busy_s) else ""
    except Exception:  # noqa: BLE001 提示字不得讓面板死
        return ""
