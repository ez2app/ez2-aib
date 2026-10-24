#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 已知熱點表(10-25 @044e0,RULING-room-heat-local-opt 第 3 張)。

仿當機雷達的 KNOWN_CRASHES:找熱源(churn)的每一列拿去比簽名 ⇒ 代碼 + 修在哪版;
同一個代碼每次 churn 都記一筆「當時裝的版號 + 每秒新開幾支」,換版後數字降下來 ⇒ 頁面標「已改善」。

  status:update_available  裝的版號 < fixed_in(換版就會修)
          improved          換到 fixed_in 以後,每秒支數 ≤ 換版前的 7 成
          not_improved      換到 fixed_in 以後,數字沒降(修法沒打中,要回報)
          fixed             換到 fixed_in 以後,但沒有換版前的紀錄可比
          unscheduled       還沒排進任何一版

簽名只比程式檔名(組別只拿來顯示;同一支狀態列腳本可能掛在席位底下、也可能認不出主人)。
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

# (代碼, 程式檔名規則, 每秒支數門檻, 修在哪一版或 None)
KNOWN_HOT = (
    ("right_tabs_poll", re.compile(r"^pane-right-tabs\.sh$"), 10.0, "0.1.54"),      # #324 / #325
    ("statusline_cost", re.compile(r"^(statusline-ez2-[\w.\-]+\.sh|seat_now\.py)$"), 2.0, "0.1.55"),   # #326 / #330
    ("cabin_guard_logshow_dup", re.compile(r"^log$"), 0.5, None),
    ("boards_seat_info", re.compile(r"^seat-info\.py$"), 0.05, None),
)
CODES = tuple(k[0] for k in KNOWN_HOT)
STATUSES = ("update_available", "improved", "not_improved", "fixed", "unscheduled")


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


def match(item, tracked=()):
    """門檻只用在第一次標;已經有換版前紀錄的代碼(tracked)不看門檻,修好後數字掉到門檻下也要記到。"""
    for code, prog_re, floor, fixed in KNOWN_HOT:
        if prog_re.search(item.get("program") or "") and ((item.get("births_per_s") or 0) >= floor or code in tracked):
            return code, fixed
    return None, None


def status_of(fixed, installed, rec):
    if fixed is None:
        return "unscheduled"
    iv, fv = _ver(installed), _ver(fixed)
    if iv is None or fv is None or iv < fv:
        return "update_available"
    before, after = rec.get("before"), rec.get("after")
    if before is None or after is None:
        return "fixed"
    return "improved" if after <= before * IMPROVED_RATIO else "not_improved"


def _num(v):
    import room_heat_unseen as RU
    return float(v) if RU._finite(v) and v >= 0 else None


def load(environ=None):
    """{代碼: {before, before_ver, after, after_ver, t}};只收表上的代碼與數字 / 版號字串。"""
    import room_heat
    import room_heat_unseen as RU
    d = RU._read_json(os.path.join(room_heat.state_dir(environ), STORE_FILE)) or {}
    out = {}
    codes = d.get("codes") if isinstance(d.get("codes"), dict) else {}
    for code, rec in codes.items():
        if code not in CODES or not isinstance(rec, dict):
            continue
        out[code] = {"before": _num(rec.get("before")), "after": _num(rec.get("after")), "t": _num(rec.get("t")),
                     "before_ver": rec.get("before_ver") if _ver(rec.get("before_ver")) else None,
                     "after_ver": rec.get("after_ver") if _ver(rec.get("after_ver")) else None}
    return out


def annotate(result, installed=None, now=None, environ=None, record=True, full=None):
    """在 churn 結果的每列加 known(有中簽名才加),整份加 known_hot 清單;順手記這次的數字。回 result。
    full = 這次取樣的全部列(預設只有 top);換版後某個已知代碼整次都沒出現 ⇒ 記 0(真的不再開了)。"""
    import room_heat
    import room_heat_unseen as RU
    now = time.time() if now is None else now
    installed = installed if installed is not None else installed_version()
    store = load(environ)
    tracked = {c for c, rec in store.items() if rec.get("before") is not None}
    fixed_of = dict((k[0], k[3]) for k in KNOWN_HOT)
    seen = {}
    for it in (full if full is not None else result.get("top") or []):
        code, _fixed = match(it, tracked)
        if code:
            seen[code] = seen.get(code, 0.0) + float(it.get("births_per_s") or 0)
    if full is not None and result.get("state", "ok") == "ok":
        for code in tracked - set(seen):
            if fixed_of[code] and _ver(installed) and _ver(installed) >= _ver(fixed_of[code]):
                seen[code] = 0.0
    for it in result.get("top") or []:
        code, fixed = match(it, tracked)
        if code:
            it["known"] = {"code": code, "fixed_in": fixed}
    for code, rate in seen.items():
        fixed = fixed_of[code]
        rec = store.setdefault(code, {"before": None, "after": None, "before_ver": None, "after_ver": None, "t": None})
        iv, fv = _ver(installed), _ver(fixed) if fixed else None
        if fv is not None and iv is not None and iv >= fv:
            rec.update(after=round(rate, 2), after_ver=installed)
        else:
            rec.update(before=round(rate, 2), before_ver=installed)
        rec["t"] = round(now, 1)
    for it in result.get("top") or []:
        k = it.get("known")
        if k:
            k["status"] = status_of(k["fixed_in"], installed, store.get(k["code"], {}))
    result["known_hot"] = [{"code": c, "fixed_in": fixed_of[c], "status": status_of(fixed_of[c], installed, store[c]),
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
