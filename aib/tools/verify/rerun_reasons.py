#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「每次都得重跑」的原因排行(10-07,給所有 aib 用戶;`aib ship why`)。

aib 對每支測試只說「這次沿用 / 這次要跑、為什麼」;使用者看不到「整個專案卡在哪」。
這裡看紀錄池裡這個專案每支測試最新一列,把「下次一定還要重跑」的依原因分組,算支數與每輪花掉的秒數
(那支上次實跑的秒數),大的排前面。一支可以同時有好幾個原因,每組各算一次。
不會重跑的(量得到、上次綠)不列。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PYCHILD = ("python-child-no-sitecustomize", "python-child-no-inherit")
MARKERS = ("archived-history", "quarantined-bad-row")


def latest_rows(pool):
    """{測試: 最新一列};封存標記、隔離墓碑、作廢列、壞行不算。"""
    best = {}
    try:
        fh = open(pool, encoding="utf-8", errors="replace")
    except OSError:
        return {}
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("test") or "rc" not in r or r.get("void"):
                continue
            if any(m in (r.get("why") or []) for m in MARKERS):
                continue
            t = str(r["test"])
            if str(r.get("time_utc") or "") >= str((best.get(t) or {}).get("time_utc") or ""):
                best[t] = r
    return best


def reasons(row):
    """一列 ⇒ [(類別, 細節)];空 = 下次可以沿用。類別:red / skipped / external / no_exit / child / pychild / native / tmparg / nohook / native_read / other。"""
    rc = row.get("rc")
    if rc == 5 or row.get("unverified"):
        return [("skipped", "")]
    if rc != 0:
        return [("red", "")]
    out = []
    why = [str(w) for w in (row.get("why") or [])]
    if row.get("external") or any(w.split(":")[0] == "external" for w in why):
        out.append(("external", ""))
    if row.get("measured") is not True:
        for w in why:
            cat, _, detail = w.partition(":")
            if cat == "external":
                continue
            if cat == "no-exit-record":
                item = ("no_exit", "")
            elif cat == "child-untracked":
                item = ("child", detail.split(":")[0])
            elif cat in PYCHILD:
                item = ("pychild", "")
            elif cat.startswith("node-native"):
                item = ("native", "")
            elif cat == "argv-tmp-path-without-content":
                item = ("tmparg", "")
            elif cat == "no-hook":
                item = ("nohook", "")
            elif cat == "native-read-unrecorded":
                item = ("native_read", detail)
            else:
                item = ("other", cat)
            if item not in out:
                out.append(item)
        if not out:
            out.append(("other", "unmeasured"))
    return out


def summarize(tests, rows):
    """回 (分組清單, 每次重跑的支數, 那些的秒數, 全部的秒數)。分組 = {cat, detail, tests, seconds},秒數大的在前。"""
    groups = {}
    rerun, rerun_s, all_s = set(), 0.0, 0.0
    for t in tests:
        r = rows.get(t)
        if r is None:
            continue
        sec = float(r.get("duration_s") or 0.0)
        all_s += sec
        rs = reasons(r)
        if not rs:
            continue
        rerun.add(t)
        rerun_s += sec
        for cat, detail in rs:
            g = groups.setdefault((cat, detail), {"cat": cat, "detail": detail, "tests": [], "seconds": 0.0})
            g["tests"].append(t)
            g["seconds"] += sec
    order = sorted(groups.values(), key=lambda g: (-g["seconds"], -len(g["tests"]), g["cat"], g["detail"]))
    return order, len(rerun), rerun_s, all_s
