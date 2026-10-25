#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat stack:機房「散熱」那一排元件插槽的資料(RULING-room-heat-local-opt 8.1,第 4 張;頁面 @74e8b)。

  aib room heat stack [--json]     每個散熱元件:裝了沒、哪一版、修在哪版、現在的燈、最近一次的數字

只讀,不叫任何外部指令、不取樣:數字全從既有紀錄來(狀態根的 room/ 與 logs/、aib 自己的 VERSION、
已裝外掛清單、skill 檔、Claude 設定的 Stop hook)。沒量過的欄位一律 null,不畫假數字(8.1)。
契約(S3):id / kind / state / code / unit 是代碼,人話走字典 aib.room.heat.stack.<id>.title / .hint(四語)。
  state:on 有裝在跑 / update_available 裝的版號 < fixed_in(或設定可改善)/ missing 沒裝 /
         error 最近一次失敗(看 code)/ unmeasured 量不到(≠ 0)/ unsupported 平台不支援
  code:version_unreadable hooks_incomplete memguard_failed no_record unsupported_platform(只在 error / unmeasured / unsupported 才有)
  unit:cores ms mb count per_s
S1 generated_at 用本機時區 offset,另給 generated_ts(秒數);程式比較一律用秒數。
S2 paid 只是旗標:不放價錢、試用、購買入口。
S4 installed 讀實際裝的版號(aib VERSION、aib_ext 清單、skill 檔);讀不到 ⇒ unmeasured,不是 missing。
隱私(D4):只出 id / 版號 / 數字 / 代碼,不出路徑、指令列、token。
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

AIB_ROOT = os.path.dirname(os.path.dirname(HERE))
WEEK_S = 7 * 86400
STALE_S = 600
LOG_READ_MAX = 2 * 1024 * 1024
FOOTER_GATE_RE = re.compile(r"footer[-_]gate")
SKILL_DIRS = ("~/.claude/skills", "~/.ez2-aib/skills", "~/.codex/skills")
SKILL_VER_RE = re.compile(r"^version:\s*([0-9]+(?:\.[0-9]+){0,3})\s*$", re.M)

# (id, kind, fixed_in, paid);順序 = 頁面插槽順序(契約,加在最後面)
ITEMS = (
    ("memguard", "core", "0.1.54", False),
    ("test_memcap", "core", "0.1.54", False),
    ("right_tabs", "core", "0.1.54", False),
    ("statusline", "core", "0.1.55", False),
    ("hook_footer_gate", "setting", None, False),
    ("room_heat", "core", "0.1.53", False),
    ("heat_release", "core", "0.1.54", False),
    ("heat_auto_release", "plugin", None, True),
    ("heat_unseen", "core", "0.1.56", False),
    ("heat_advice", "core", "0.1.56", False),
    ("heat_known", "core", "0.1.56", False),
    ("ez2-local-perf", "skill", None, False),
)
IDS = tuple(i[0] for i in ITEMS)
STATES = ("on", "update_available", "missing", "error", "unmeasured", "unsupported")
UNITS = ("cores", "ms", "mb", "count", "per_s")
KNOWN_OF = {"right_tabs": "right_tabs_poll", "statusline": "statusline_cost"}
MODULE_OF = {"memguard": "memguard.py", "room_heat": "room_heat.py", "heat_release": "room_heat_release.py",
             "heat_unseen": "room_heat_unseen.py", "heat_advice": "room_heat_advice.py", "heat_known": "room_heat_known.py"}


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except ValueError:
        return None


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def iso(ts):
    """本機時區 offset 的 ISO 8601(S1:不寫死 +08:00)。"""
    if not _finite(ts):
        return None
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _jsonl(path, limit=LOG_READ_MAX):
    """尾讀一份 jsonl(最多 limit 位元組);每行是 dict 才收。讀不了 ⇒ None(≠ 空)。"""
    import room_heat
    try:
        size = os.stat(path).st_size
    except OSError:
        return None
    raw = room_heat.safe_read(path, size) if size <= limit else None
    if raw is None and size > limit:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.lseek(fd, size - limit, 0)
                raw = os.read(fd, limit).decode("utf-8", "replace").split("\n", 1)[-1]
            finally:
                os.close(fd)
        except OSError:
            return None
    if raw is None:
        return None
    rows = []
    for line in raw.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def _result(metric, unit, before=None, after=None, value=None):
    clean = lambda v: round(float(v), 3) if _finite(v) else None   # noqa: E731
    return {"metric": metric, "unit": unit, "before": clean(before), "after": clean(after), "value": clean(value)}


# ---------- 各元件的紀錄 ----------

def _memguard(environ, now=None):
    import seats_dir_lib
    root, _src = seats_dir_lib.state_root(environ)
    rows = _jsonl(os.path.join(root, "logs", "memguard.jsonl"))
    if rows is None:
        return None, None, None                        # 沒有紀錄檔 = 沒收過也沒失敗過,但量不到「跑了沒」⇒ value null
    ts = [r.get("ts") for r in rows if _finite(r.get("ts"))]
    now = time.time() if now is None else now
    kills = sum(1 for r in rows if r.get("action") in ("mem_terminated", "mem_killed") and _finite(r.get("ts")) and now - r["ts"] <= WEEK_S)
    failed = any(r.get("action") == "mem_guard_failed" and _finite(r.get("ts")) and now - r["ts"] <= WEEK_S for r in rows[-20:])
    return _result("kills_7d", "count", value=kills), max(ts) if ts else None, "memguard_failed" if failed else None


def _history(environ, now=None):
    import room_heat
    try:
        pts = room_heat.read_history(environ=environ, now=now)
    except Exception:  # noqa: BLE001
        return []
    return pts or []


def _released(environ, auto, now=None):
    import room_heat_release as RR
    rows = _jsonl(RR.released_path(environ))
    if rows is None:
        return None, None
    now = time.time() if now is None else now
    mine = [r for r in rows if (r.get("mode") == "auto") == auto and _finite(r.get("t"))]
    n = sum(1 for r in mine if now - r["t"] <= WEEK_S)
    return _result("auto_released_7d" if auto else "released_7d", "count", value=n), max((r["t"] for r in mine), default=None)


def _known(environ, installed):
    import room_heat_known as KN
    try:
        store = KN.load(environ)
    except Exception:  # noqa: BLE001
        return {}, None
    fixed = {k[0]: k[3] for k in KN.KNOWN_HOT}
    floor = {k[0]: k[2] for k in KN.KNOWN_HOT}
    out, improved = {}, 0
    for code, rec in store.items():
        st = KN.status_of(fixed.get(code), installed, rec, floor.get(code, 0.0))
        improved += st == "improved"
        after = rec.get("after") if st in ("improved", "not_improved", "fixed") else None
        out[code] = (_result("cpu", "cores", before=rec.get("before"), after=after), rec.get("t"))
    return out, (_result("improved", "count", value=improved) if store else None)


def _advice(environ):
    import room_heat
    import room_heat_advice as AD
    raw = room_heat.safe_read(os.path.join(room_heat.state_dir(environ), AD.CACHE_FILE), 1024 * 1024)
    try:
        c = json.loads(raw) if raw else None
    except ValueError:
        c = None
    if not isinstance(c, dict) or c.get("schema") != "aib-heat-advice/v1" or not isinstance(c.get("items"), list):
        return None, None
    return _result("open_advice", "count", value=len(c["items"])), c.get("t") if _finite(c.get("t")) else None


def _footer_gate(claude_home):
    """Stop hook 裡還有 footer-gate ⇒ update_available;都讀得懂且沒有 ⇒ on;有看不懂的來源 ⇒ unmeasured。"""
    import room_heat_advice as AD
    try:
        timeouts, labels, complete = AD.hook_sources(claude_home)
    except Exception:  # noqa: BLE001
        return "unmeasured", "hooks_incomplete"
    if any(FOOTER_GATE_RE.search(k) for k in list(timeouts) + list(labels)):
        return "update_available", None
    return ("on", None) if complete else ("unmeasured", "hooks_incomplete")


def _plugin_version(name, rows_fn=None):
    try:
        import aib_ext
        rows = (rows_fn or aib_ext.installed)()
    except Exception:  # noqa: BLE001
        return "unreadable"
    for r in rows:
        if r.get("name") == name:
            return r.get("version") if _ver(r.get("version")) else "unreadable"
    return None


def _skill_version(name, aib, home=None, aib_root=AIB_ROOT):
    """找 skill 檔:有 version: 就用;aib 自己帶的(在 aib 的 skills/ 底下)沒寫版號 ⇒ 算 aib 的版號;
    別處找到但沒版號 ⇒ "unreadable";哪裡都沒有 ⇒ None。"""
    import room_heat
    own = os.path.join(aib_root, "skills", name, "SKILL.md")
    dirs = [os.path.join(os.path.expanduser(d) if home is None else d.replace("~", home, 1), name, "SKILL.md") for d in SKILL_DIRS]
    found = None
    for p in [own] + dirs:
        body = room_heat.safe_read(p, 64 * 1024)
        if body is None:
            continue
        m = SKILL_VER_RE.search(body.split("\n---", 1)[0])
        if m:
            return m.group(1)
        found = found or ("own" if p == own else "other")
    if found == "own":
        return aib
    return "unreadable" if found else None


# ---------- 組起來 ----------

def _item(iid, kind, fixed, paid):
    key = iid.replace("-", "_")                       # 字典鍵不准有「-」(i18n_lib);id 照舊是契約
    return {"id": iid, "kind": kind, "title_key": "aib.room.heat.stack.%s.title" % key,
            "hint_key": "aib.room.heat.stack.%s.hint" % key, "installed": None, "fixed_in": fixed,
            "state": "missing", "code": None, "last_run_at": None, "paid": paid, "result": None}


def _versioned(it, installed):
    """有版號 ⇒ 跟 fixed_in 比;讀不到 ⇒ unmeasured version_unreadable(S4)。"""
    if installed == "unreadable":
        it.update(state="unmeasured", code="version_unreadable")
        return
    it["installed"] = installed
    if installed is None:
        return
    fv, iv = _ver(it["fixed_in"]), _ver(installed)
    it["state"] = "update_available" if fv and iv and iv < fv else "on"


def stack(environ=None, now=None, platform=None, claude_home=None, aib=None, cockpit=HERE, plugins_fn=None, home=None):
    import room_heat_known as KN
    now = time.time() if now is None else now
    plat = platform or sys.platform
    aib = aib if aib is not None else (KN.installed_version() or "unreadable")
    claude_home = claude_home or os.path.expanduser("~/.claude")
    hist = _history(environ, now)
    last_pt = hist[-1] if hist else {}
    known, improved = _known(environ, None if aib == "unreadable" else aib)
    items = []
    for iid, kind, fixed, paid in ITEMS:
        it = _item(iid, kind, fixed, paid)
        items.append(it)
        if plat != "darwin" and not plat.startswith("linux"):
            it.update(state="unsupported", code="unsupported_platform")
            continue
        if kind == "core":
            present = os.path.isfile(os.path.join(cockpit, MODULE_OF[iid])) if iid in MODULE_OF else True
            if iid == "test_memcap":
                present = "AIB_TEST_MEM_CAP_MB" in (_read_small(os.path.join(os.path.dirname(os.path.dirname(cockpit)),
                                                                             "tools", "verify", "runner.py")) or "")
            _versioned(it, aib if present else None)
        if iid == "memguard" and it["installed"]:
            res, last, code = _memguard(environ, now)
            it.update(result=res, last_run_at=iso(last))
            if code:
                it.update(state="error", code=code)
        elif iid in KNOWN_OF and it["installed"]:
            res, last = known.get(KNOWN_OF[iid], (None, None))
            it.update(result=res, last_run_at=iso(last))
        elif iid == "hook_footer_gate":
            st, code = _footer_gate(claude_home)
            it.update(state=st, code=code)
        elif iid == "room_heat" and it["installed"]:
            it["last_run_at"] = iso(last_pt.get("t"))
        elif iid == "heat_release" and it["installed"]:
            res, last = _released(environ, auto=False, now=now)
            it.update(result=res, last_run_at=iso(last))
        elif iid == "heat_auto_release":
            _versioned(it, _plugin_version("captain-room", plugins_fn))
            if it["installed"]:                        # 裝了外掛不代表開關開著:有自動收過的紀錄才算在跑
                res, last = _released(environ, auto=True, now=now)
                it.update(result=res, last_run_at=iso(last))
                if last is None:
                    it.update(state="unmeasured", code="no_record")
        elif iid == "heat_unseen" and it["installed"]:
            v = last_pt.get("unseen_cpu")
            it.update(result=_result("unseen_cores", "cores", value=v) if _finite(v) else None,
                      last_run_at=iso(last_pt.get("t")) if _finite(v) else None)
        elif iid == "heat_advice" and it["installed"]:
            res, last = _advice(environ)
            it.update(result=res, last_run_at=iso(last))
        elif iid == "heat_known" and it["installed"]:
            ts = [t for _r, t in known.values() if _finite(t)]
            it.update(result=improved, last_run_at=iso(max(ts)) if ts else None)
        elif kind == "skill":
            _versioned(it, _skill_version(iid, None if aib == "unreadable" else aib, home))
    stale = not _finite(last_pt.get("t")) or now - last_pt["t"] > STALE_S
    return {"v": 1, "schema": "aib-heat-stack/v1", "generated_at": iso(now), "generated_ts": int(now), "stale": stale,
            "aib": None if aib == "unreadable" else aib, "items": items}


def _read_small(path):
    import room_heat
    return room_heat.safe_read(path, 512 * 1024)


def main(argv, out=None):
    import argparse
    import room_heat as RH
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib room heat stack")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    s = stack()
    if a.json:
        json.dump(s, out, ensure_ascii=False)
        out.write("\n")
        return 0
    out.write(RH.T("stack.header", aib=s["aib"] or "?") + ("  " + RH.T("stack.stale") if s["stale"] else "") + "\n")
    for it in s["items"]:
        r = it["result"] or {}
        num = next((("%s→%s" % (r["before"], r["after"])) if k == "ba" else str(r["value"])
                    for k, ok in (("ba", r.get("before") is not None), ("v", r.get("value") is not None)) if ok), "—")
        out.write("  %-18s %-16s %-8s %-8s %s%s\n" % (it["id"], RH.T("stack.state." + it["state"]), it["installed"] or "—",
                                                     it["fixed_in"] or "—", num, ("  " + it["code"]) if it["code"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
