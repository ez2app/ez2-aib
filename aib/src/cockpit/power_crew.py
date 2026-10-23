#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長動力室「艦員檔案」:為什麼用 AIB 的排名(契約 contracts/power.json crew_receipt;SPEC-power-room §13)。

  aib power why [--json]                         讀
  aib power why set <代碼,代碼,…> [--other 一句] [--json]   存(逗號順序 = 排名;重複的只留第一次)
  aib power why clear [--json]                   清掉(第一畫面會再出現)

10-24 02:4x Andy:「一開始最主要的地方，讓他選為什麼要用 AIB?多選，但要排序」。
- 只存本機 state/power-crew.json、不上傳(要不要匿名回傳統計另問 Andy)。
- 用途:任務板 / 補給站 / 問 AI 依排名排序 —— 只影響顯示順序,不是權限。
- 「其他」自填一句:去控制字元、最多 60 字;檔是同帳號寫得到的,讀的時候照資料看。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402

# 順序 = 定案頁板 0 的選項順序
WHY = ("memory", "multi_ai", "coding", "phone", "sites", "art", "room", "save", "other")
OTHER_MAX = 60


def _path():
    return os.path.join(seats_dir_lib.state_root()[0], "state", "power-crew.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(obj):
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def _other(v):
    if not isinstance(v, str):
        return None
    v = "".join(c for c in v if ord(c) >= 32 and ord(c) != 127 and not 0x80 <= ord(c) < 0xa0).strip()
    return v[:OTHER_MAX] or None


def _rank(items):
    out = []
    for k in items if isinstance(items, list) else []:
        if k in WHY and k not in out:
            out.append(k)
    return out


def why():
    """{ok, detail, why_rank, other, at};沒設過 ⇒ why_rank [](頁面就先出第一畫面)。"""
    d = _load()
    rank = _rank(d.get("why_rank"))
    at = d.get("at")
    return {"ok": True, "detail": "power_why", "why_rank": rank,
            "other": _other(d.get("other")) if "other" in rank else None,
            "at": at if isinstance(at, int) and not isinstance(at, bool) else None}


def set_why(items, other=None, now=None):
    """回 (回條, rc)。items = 代碼清單(順序 = 排名)。不認得的代碼 / 空 ⇒ rc 2,檔不動。"""
    items = [x.strip() for x in items if isinstance(x, str) and x.strip()]
    bad = [x for x in items if x not in WHY]
    if bad or not items:
        r = why()
        r.update(ok=False, detail="power_why_bad")
        return r, 2
    rank = _rank(items)
    d = _load()
    d["why_rank"] = rank
    d["other"] = _other(other) if "other" in rank else None
    d["at"] = int(time.time() if now is None else now)
    _save(d)
    r = why()
    r["detail"] = "power_why_set"
    return r, 0


def clear():
    d = _load()
    for k in ("why_rank", "other", "at"):
        d.pop(k, None)
    _save(d)
    r = why()
    r["detail"] = "power_why_cleared"
    return r, 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="aib power why")
    p.add_argument("cmd", nargs="?", choices=("set", "clear"))
    p.add_argument("items", nargs="?")
    p.add_argument("--other")
    p.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv[1:] if argv and argv[0] == "why" else argv)
    except SystemExit:
        return 2
    if a.cmd == "set":
        r, rc = set_why((a.items or "").split(","), a.other)
    elif a.cmd == "clear":
        r, rc = clear()
    else:
        r, rc = why(), 0
    print(json.dumps(r, ensure_ascii=False) if a.json else json.dumps(r, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
