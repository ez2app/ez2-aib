#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 已知熱點表(10-25 @044e0,RULING-room-heat-local-opt 第 3 張)。

仿當機雷達的 KNOWN_CRASHES:找熱源(churn)的每一列拿去比簽名 ⇒ 代碼 + 修在哪版;
同一個代碼每次 churn 都記一筆「當時裝的版號 + 每秒新開幾支」,換版後數字降下來 ⇒ 頁面標「已改善」。

  status:update_available  裝的版號 < fixed_in(換版就會修)
          improved          換到 fixed_in 以後,低於門檻、且 ≤ 換版前的 7 成
          not_improved      換到 fixed_in 以後,還在門檻以上或數字沒降(修法沒打中 / 舊迴圈還在跑舊腳本,要回報)
          fixed             換到 fixed_in 以後低於門檻,但沒有換版前的紀錄可比
          unmeasured        換到 fixed_in 以後還沒量過(不猜「已修」,審查 H1)
          unknown           讀不到裝的版號(不猜「有新版」,審查 M6)
          unscheduled       還沒排進任何一版

簽名比程式檔名,少數加組別(log 這種通用名字只認 aib 自己起的,審查 L9);
門檻比「同一代碼這次全部列加總」(4 席各 3 支 / 秒 = 12,審查 L10)。
換版前的數字留最近 9 次取中位數(只留最後一筆會被閒時的低值蓋掉,審查 M5);
換版後整次沒看到 ⇒ 只有在它的上層程式(tmux / claude)這次確定開著才記 0(沒開艙不算改善)。
紀錄檔 room/known-hot.json(0600),只存代碼 / 版號 / 數字;讀回時代碼不在表上的丟掉。
"""
from __future__ import annotations

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

STORE_FILE = "known-hot.json"
IMPROVED_RATIO = 0.7
BEFORE_KEEP = 9

# (代碼, 程式檔名規則, 每秒支數門檻, 修在哪一版或 None, 限哪些組別或 None, 上層程式名規則或 None)
KNOWN_HOT = (
    ("right_tabs_poll", re.compile(r"^pane-right-tabs\.sh$"), 10.0, "0.1.54", None, re.compile(r"^tmux")),  # #324 / #325
    ("statusline_cost", re.compile(r"^(statusline-ez2-[\w.\-]+\.sh|seat_now\.py)$"), 2.0, "0.1.55", None,
     re.compile(r"^claude$")),                                                                        # #326 / #330
    ("cabin_guard_logshow_dup", re.compile(r"^log$"), 0.5, None, ("aib_core",), None),
    ("boards_seat_info", re.compile(r"^seat-info\.py$"), 0.05, None, None, None),
)
CODES = tuple(k[0] for k in KNOWN_HOT)
STATUSES = ("update_available", "improved", "not_improved", "fixed", "unmeasured", "unknown", "unscheduled")


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return None


def installed_version(path=None):
    """aib 自己的 VERSION(src/cockpit 往上兩層);讀不到 ⇒ None。"""
    path = path or os.path.join(os.path.dirname(os.path.dirname(HERE)), "VERSION")
    try:
        with open(path, encoding="utf-8") as fh:
            v = fh.read(64).strip()
    except OSError:
        return None
    return v if _ver(v) else None


def code_of(item):
    """這一列像哪個已知代碼(不看門檻);組別條件不合 ⇒ None。"""
    for code, prog_re, _floor, _fixed, groups, _parent in KNOWN_HOT:
        if prog_re.search(item.get("program") or "") and (groups is None or item.get("group") in groups):
            return code
    return None


def match(items, tracked=()):
    """→ {代碼: 這次全部列加總的每秒支數};加總 ≥ 門檻才算(審查 L10)。
    已經有換版前紀錄的代碼(tracked)不看門檻,修好後數字掉到門檻下也要記到。"""
    sums = {}
    for it in items:
        code = code_of(it)
        if code:
            sums[code] = sums.get(code, 0.0) + float(it.get("births_per_s") or 0)
    floor = {k[0]: k[2] for k in KNOWN_HOT}
    return {c: v for c, v in sums.items() if v >= floor[c] or c in tracked}


def status_of(fixed, installed, rec, floor=0.0):
    if fixed is None:
        return "unscheduled"
    iv, fv = _ver(installed), _ver(fixed)
    if iv is None or fv is None:
        return "unknown"
    if iv < fv:
        return "update_available"
    before, after = rec.get("before"), rec.get("after")
    if after is None or _ver(rec.get("after_ver")) is None or _ver(rec.get("after_ver")) < fv:
        return "unmeasured"
    if after >= floor:                                   # 換了版還在門檻以上 = 沒修到(審查 H1)
        return "not_improved"
    if before is None:
        return "fixed"
    return "improved" if after <= before * IMPROVED_RATIO else "not_improved"


def _num(v):
    import room_heat_unseen as RU
    return float(v) if RU._finite(v) and v >= 0 else None


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0)


def load(environ=None):
    """{代碼: {before, befores, before_ver, after, after_ver, t}};只收表上的代碼與數字 / 版號字串。
    before = befores(最近 9 次換版前的數字)的中位數。"""
    import room_heat
    import room_heat_unseen as RU
    d = RU._read_json(os.path.join(room_heat.state_dir(environ), STORE_FILE)) or {}
    out = {}
    codes = d.get("codes") if isinstance(d.get("codes"), dict) else {}
    for code, rec in codes.items():
        if code not in CODES or not isinstance(rec, dict):
            continue
        bs = rec.get("befores") if isinstance(rec.get("befores"), list) else [rec.get("before")]   # 舊格式只有一筆
        bs = [x for x in (_num(b) for b in bs[-BEFORE_KEEP:]) if x is not None]
        out[code] = {"before": _median(bs), "befores": bs, "after": _num(rec.get("after")), "t": _num(rec.get("t")),
                     "before_ver": rec.get("before_ver") if _ver(rec.get("before_ver")) else None,
                     "after_ver": rec.get("after_ver") if _ver(rec.get("after_ver")) else None}
    return out


def annotate(result, installed=None, now=None, environ=None, record=True, full=None, alive=None):
    """在 churn 結果的每列加 known(有中簽名才加),整份加 known_hot 清單;順手記這次的數字。回 result。
    full = 這次取樣的全部列(預設只有 top);alive = 這次開著的程式名(basename)。
    換版後某個已知代碼整次都沒出現、且它的上層程式確定開著 ⇒ 記 0(真的不再開了)。"""
    import room_heat
    import room_heat_unseen as RU
    now = time.time() if now is None else now
    installed = installed if installed is not None else installed_version()
    store = load(environ)
    tracked = {c for c, rec in store.items() if rec.get("before") is not None}
    fixed_of = {k[0]: k[3] for k in KNOWN_HOT}
    floor_of = {k[0]: k[2] for k in KNOWN_HOT}
    parent_of = {k[0]: k[5] for k in KNOWN_HOT}
    iv = _ver(installed)
    seen = match(full if full is not None else result.get("top") or [], tracked)
    if full is not None and alive and result.get("state", "ok") == "ok":
        for code in tracked - set(seen):
            par = parent_of[code]
            if (fixed_of[code] and iv and iv >= _ver(fixed_of[code]) and par is not None
                    and any(par.search(n or "") for n in alive)):
                seen[code] = 0.0
    for it in result.get("top") or []:
        code = code_of(it)
        if code and code in seen:
            it["known"] = {"code": code, "fixed_in": fixed_of[code]}
    for code, rate in seen.items():
        fixed = fixed_of[code]
        rec = store.setdefault(code, {"before": None, "befores": [], "after": None, "before_ver": None,
                                      "after_ver": None, "t": None})
        fv = _ver(fixed) if fixed else None
        if fv is not None and iv is not None and iv >= fv:
            rec.update(after=round(rate, 2), after_ver=installed)
        elif iv is not None:                            # 讀不到版號 ⇒ 不知道算換版前還是後,不記
            rec["befores"] = (rec.get("befores") or [])[-(BEFORE_KEEP - 1):] + [round(rate, 2)]
            rec.update(before=_median(rec["befores"]), before_ver=installed)
        rec["t"] = round(now, 1)
    for it in result.get("top") or []:
        k = it.get("known")
        if k:
            k["status"] = status_of(k["fixed_in"], installed, store.get(k["code"], {}), floor_of[k["code"]])
    result["known_hot"] = [{"code": c, "fixed_in": fixed_of[c],
                            "status": status_of(fixed_of[c], installed, store[c], floor_of[c]),
                            "before": store[c]["before"], "after": store[c]["after"]}
                           for c in CODES if c in store]
    result["aib"] = installed
    if record and seen:
        try:
            RU._write_json(os.path.join(room_heat.state_dir(environ), STORE_FILE),
                           {"schema": "aib-heat-known/v1", "codes": store})
        except (OSError, ValueError):
            pass
    return result
