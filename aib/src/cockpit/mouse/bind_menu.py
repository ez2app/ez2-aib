#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""綁定選單的**清單來源**(L2-03 / D-3)。mouse-ctl.sh 只負責畫選單與回呼,
清單從哪來、怎麼過濾,全在這裡(python 讀 JSON,不在 bash 裡解 JSON)。

    bind_menu.py persona-cats --cards F                → 一行一個 category
    bind_menu.py persona-cards --cards F --category C  → uid\\tlabel(該 category 的既有卡)
    bind_menu.py brain-modules --contract SPEC         → 一行一個 module(C/P/F/G/A…)
    bind_menu.py brain-routes --contract SPEC --module M → routeKey\\tlabel(可寫且非敏感)

紅線:
- 人格清單=**cards.json v2 既有卡**(uid-keyed;由 card_registry.py 唯讀掃角色庫生成)。
  選單絕不造卡:「[新建人格]」項只印 SOP 入口指引(mouse-ctl 端),不叫任何寫入器。
- 大腦清單=路由契約裡 `aiWritable` 且 dataLevel ∉ {敏感,機密} 的 route
  (`route_register.check_against_contract` 同一判準,不另抄一份規則)。
- 顯示字串進 tmux display-menu 前消毒:去控制字元、去 `'`(run-shell 單引號包裹)、
  限長;uid/routeKey 形狀不合直接不列(fail-closed,不是列一個點不動的項)。
- 任何來源讀不到 → 印空、rc 0:選單「沒有項目」比「炸掉整個艙」好(E2.13 恆 exit 0 精神);
  但 stderr 會說為什麼(給 mouse-errors.log)。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
for _p in ("memory", "persona_route"):
    sys.path.insert(0, str(_SRC / _p))

from route_register import check_against_contract, load_contract  # noqa: E402

UID_RE = re.compile(r"[0-9a-z]{6}")
ROUTE_RE = re.compile(r"[a-z][a-z0-9._-]{0,127}")
UNCAT = "(未分類)"


def _safe(text, limit=40):
    text = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in str(text or ""))
    text = text.replace("'", "").replace("\t", " ")
    return " ".join(text.split())[:limit]


def _load_cards(path):
    try:
        with open(path, "rb") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as err:
        print("bind-menu: cards 讀不到 %s(%s)——先跑 card_registry.py scan" % (path, err), file=sys.stderr)
        return {}
    cards = doc.get("cards") if isinstance(doc, dict) else None
    if not isinstance(cards, dict):
        print("bind-menu: cards.json 形狀不對", file=sys.stderr)
        return {}
    out = {}
    for uid, e in cards.items():
        if not isinstance(e, dict) or not UID_RE.fullmatch(str(uid)) or e.get("uid") != uid:
            continue
        out[uid] = e
    return out


def persona_cats(cards):
    cats = sorted({_safe(e.get("category") or UNCAT, 24) for e in cards.values()})
    return [c for c in cats if c]


def persona_cards(cards, category):
    rows = []
    for uid, e in sorted(cards.items(), key=lambda kv: (_safe(kv[1].get("display_name")), kv[0])):
        cat = _safe(e.get("category") or UNCAT, 24)
        if cat != category:
            continue
        label = "%s %s (%s)" % (_safe(e.get("emoji") or "", 4), _safe(e.get("display_name"), 24), uid)
        rows.append((uid, " ".join(label.split())))
    return rows


def _writable_routes(contract):
    out = []
    for r in (contract or {}).get("routes", []):
        rk = r.get("routeKey")
        if not isinstance(rk, str) or not ROUTE_RE.fullmatch(rk):
            continue
        ok, _code, _detail = check_against_contract(rk, contract)
        if ok:
            out.append(r)
    return out


def brain_modules(contract):
    return sorted({_safe(r.get("module") or "?", 4) for r in _writable_routes(contract)})


def brain_routes(contract, module):
    rows = []
    for r in _writable_routes(contract):
        if _safe(r.get("module") or "?", 4) != module:
            continue
        folder = _safe(r.get("folder"), 60)
        tail = folder.rsplit("/", 1)[-1] if folder else ""
        try:   # 10-15 盤點 U10:uid 版面顯示 labels.json 的標題,不顯示 fld_…
            import route_register
            tail = _safe(route_register.route_label(r["routeKey"], contract=contract), 60) or tail
        except Exception:  # noqa: BLE001
            pass
        rows.append((r["routeKey"], "%s · %s" % (r["routeKey"], tail) if tail else r["routeKey"]))
    return sorted(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bind-menu")
    ap.add_argument("what", choices=["persona-cats", "persona-cards", "brain-modules", "brain-routes"])
    ap.add_argument("--cards")
    ap.add_argument("--contract")
    ap.add_argument("--category")
    ap.add_argument("--category-hex")
    ap.add_argument("--module")
    args = ap.parse_args(argv)
    if args.what.startswith("persona"):
        cards = _load_cards(args.cards or "")
        if args.what == "persona-cats":
            for c in persona_cats(cards):
                print("%s\t%s" % (c.encode("utf-8").hex(), c))
        else:
            cat = args.category or ""
            if args.category_hex:
                try:
                    cat = bytes.fromhex(args.category_hex).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return 0
            for uid, label in persona_cards(cards, cat):
                print("%s\t%s" % (uid, label))
        return 0
    contract = load_contract(args.contract or os.environ.get("EZ2_ROUTE_CONTRACT_SPEC")
                             or os.environ.get("EZ2_ROUTE_REGISTRY"))
    if contract is None:
        print("bind-menu: 路由契約讀不到(--contract / EZ2_ROUTE_CONTRACT_SPEC)", file=sys.stderr)
        return 0
    if args.what == "brain-modules":
        for m in brain_modules(contract):
            print(m)
    else:
        for rk, label in brain_routes(contract, args.module or ""):
            print("%s\t%s" % (rk, label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
