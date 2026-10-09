#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""第 70 項 P6 後半:全艙順位一份正本(state/turn-order.json)。

九病第 9 病:順位只送一席、別席仍記舊的(C1.3 插隊只告訴開發 A,主管 13:35 仍記 24→53b→68→69)。
修法不是再廣播一次,是**一份檔**:艦長 `turn set`,任務區「等誰」、待審框頂列、順位廣播都讀這一份;改一次三處同步。
住哪:與佇列開關同一套判準(feature_policy.cabin_policy_path:艙家 > 明寫狀態根 > None),永不退回 ~/.ez2-aib。

CLI:
  turn_order.py set  --by <actor> <席碼…>        寫檔(全量取代;帶 set_by / set_at / rev)
  turn_order.py show [--seat S]                  印 JSON;--seat 多帶 position(1 起)/ None
  turn_order.py broadcast-text                   給 dm 用的順位正文(機器產,人不手打)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bootstrap"))
import atomic_json as AJ  # noqa: E402
import feature_policy as FP  # noqa: E402

SCHEMA = "aib-turn-order/v1"
FILE = "turn-order.json"
SEAT_RE = re.compile(r"^[0-9a-z]{4,8}$")


def path(env=None):
    return FP.cabin_policy_path(FILE, env)


def load(env=None):
    """回 (rec|None, why)。why ∈ ok / no_home / missing / unreadable。壞檔要具名,不當空(病 3)。"""
    p = path(env)
    if p is None:
        return None, "no_home"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return None, "missing"
    except ValueError:
        return None, "unreadable"
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("order"), list):
        return None, "unreadable"
    order = [s for s in data["order"] if isinstance(s, str) and SEAT_RE.fullmatch(s)]
    return {"schema": SCHEMA, "order": order, "set_by": data.get("set_by"), "set_at": data.get("set_at"),
            "rev": int(data.get("rev") or 0)}, "ok"


def set_order(seats, by, env=None, now=None):
    """全量取代。席碼要合形、不重複;by 必填(留痕:誰改的)。回新 rec。"""
    seats = [s.strip().lower() for s in seats]
    if not seats or any(not SEAT_RE.fullmatch(s) for s in seats) or len(set(seats)) != len(seats):
        raise ValueError("seats")
    if not isinstance(by, str) or not by.strip():
        raise ValueError("by")
    p = path(env)
    if p is None:
        raise OSError("turn_order: 沒有艙家,寫不了順位")
    prev, _why = load(env)
    rec = {"schema": SCHEMA, "order": seats, "set_by": by.strip(),
           "set_at": int(time.time() if now is None else now), "rev": (prev["rev"] if prev else 0) + 1}
    p.parent.mkdir(parents=True, exist_ok=True)
    AJ.write_json(str(p), rec)
    return rec


def position(seat, env=None):
    """回 (position|None, rec|None, why)。position 從 1 起;不在順位裡 = None。"""
    rec, why = load(env)
    if not rec:
        return None, None, why
    seat = (seat or "").strip().lower()
    return (rec["order"].index(seat) + 1 if seat in rec["order"] else None), rec, "ok"


def broadcast_text(env=None):
    rec, why = load(env)
    if not rec:
        return "順位正本讀不到:%s" % why
    when = time.strftime("%m-%d %H:%M", time.localtime(rec["set_at"])) if rec.get("set_at") else "?"
    return "順位(正本 rev %d,%s 於 %s 設):%s" % (rec["rev"], rec.get("set_by") or "?", when, " → ".join("@" + s for s in rec["order"]))


def main(argv=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(prog="turn_order.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("set"); st.add_argument("--by", required=True); st.add_argument("seats", nargs="+")
    sh = sub.add_parser("show"); sh.add_argument("--seat", default=None)
    sub.add_parser("broadcast-text")
    args = ap.parse_args(argv)
    if args.cmd == "set":
        try:
            rec = set_order(args.seats, args.by, env=env)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False)); return 2
        print(json.dumps({"ok": True, **rec}, ensure_ascii=False, sort_keys=True)); return 0
    if args.cmd == "show":
        rec, why = load(env)
        out = {"ok": bool(rec), "why": None if rec else why, "path": str(path(env)) if path(env) else None}
        if rec:
            out.update(rec)
        if args.seat:
            out["seat"] = args.seat; out["position"] = position(args.seat, env)[0]
        print(json.dumps(out, ensure_ascii=False, sort_keys=True)); return 0 if rec else 3
    if args.cmd == "broadcast-text":
        print(broadcast_text(env)); return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
