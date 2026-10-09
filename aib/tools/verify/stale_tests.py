#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""很久沒被實跑的測試(10-07,the maintainer「能修的都修」)。

推前檢查只挑「這次改動會影響到的」測試;沒被任何改動碰到的測試可以好幾週都沒人跑。
10-07 補跑才發現 5 支一直紅、14 支從來沒在這台跑過 —— 主線上的紅藏在「沒被挑到」裡。
這裡列出:這個專案的每一支測試,最近一次在紀錄池裡的實跑紀錄早於 N 天前、或根本沒有紀錄。
(沿用不算實跑:沿用不會寫新紀錄,所以紀錄池裡最新一列的時間就是最後一次真的跑。)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def last_runs(pool):
    """{測試: 最近一次實跑的 time_utc 字串}。讀不到 / 壞行 ⇒ 跳過那一行。"""
    out = {}
    try:
        fh = open(pool, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("test") or not r.get("time_utc"):
                continue
            if "archived-history" in (r.get("why") or []) or "quarantined-bad-row" in (r.get("why") or []):
                continue                       # 封存標記與隔離墓碑不是實跑
            t, when = str(r["test"]), str(r["time_utc"])
            if when > out.get(t, ""):
                out[t] = when
    return out


def stale(repo, days=7, pool=None, now=None):
    """[(測試, 最後實跑 time_utc 或 None)],沒有紀錄的排前面,其餘由舊到新(這台缺條件跑不了的不列,見 split)。"""
    return split(repo, days, pool, now)[0]


def split(repo, days=7, pool=None, now=None):
    """(很久沒跑的, 這台缺條件跑不了的 [(測試, [缺的條件])])。"""
    import pushgate
    if pool is None:
        import runner
        pool = runner._pool_file(Path(repo))
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = last_runs(pool)
    rows = [(t, seen.get(t)) for t in pushgate.all_tests(repo) if seen.get(t) is None or seen[t] < cutoff]
    cant = unverifiable_here(repo, [t for t, _ in rows])
    gone = set(t for t, _ in cant)
    rows = [r for r in rows if r[0] not in gone]
    return sorted(rows, key=lambda x: (x[1] is not None, x[1] or "", x[0])), cant


def unverifiable_here(repo, tests):
    """[(測試, [缺的條件])]:檔頭宣告的條件(aib: needs …)這台不符合 ⇒ 這台永遠跑不了,不算「很久沒跑」,
    另列「要在有條件的機器上跑」(10-07:不然 aib ship stale 的提醒永遠消不掉)。"""
    import test_entry
    out = []
    for t in tests:
        try:
            unmet = test_entry._unmet_needs(Path(repo), t)
        except Exception:  # noqa: BLE001 - 判不出 ⇒ 當成可以跑(照舊列在很久沒跑)
            unmet = []
        if unmet:
            out.append((t, unmet))
    return out
