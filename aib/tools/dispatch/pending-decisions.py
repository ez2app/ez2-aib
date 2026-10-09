#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待真人決策登記簿 —— 讓「等你」變成一張會自己冒出來的清單。

the maintainer 2026-09-09 13:5x:「每固定週期,應該顯示待真人決策。」

★ 為什麼需要它(今天的實例,不是理論):
  「43 顆合不合入 main」「過期滿額政策」這幾格,我是**在他問的時候才講**。
  沒有人問的時候,它們安靜地躺著 —— 而**安靜地等,與沒有東西在等,長得一樣**。
  ⇒ 等待要有自己的載體,而且要會自己冒出來,不能靠對話碰巧提到。

★ 每一筆都必須答得出三件,否則登記不進去:
  (1) 這是什麼決定 —— 一句話
  (2) **為什麼只有真人能決定** —— 答不出來的,就不該掛在他身上(rc=3)
  (3) 它擋著什麼 —— 空的話這筆等待不痛不癢,那也要說出來

★ 等待時間有基線:昨天「交件到裁定 2430 秒」那一課的同一把尺。
  超過 `--warn-after` 就標星,讓「等很久」與「剛掛上去」分得開。

★ 這支檔**不做決定**,只讓等待可見。它也不催 —— 催人是通知的事,
  而通知有節流;這裡只負責「問我的時候答得出來,不問也會定期冒出來」。
"""

import argparse
import datetime
import json
import os
import pathlib
import sys

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   ★★ 2026-09-09 23:2x 現查發現的事:本檔要的 26 個鍵**早就做好了**
#   (顆 19e10dea「全部改白話並加名詞註」,四語齊全、而且已經是白話版),
#   而本檔一個都沒用 —— 它照樣印自己硬編的 30 句。
#   ⇒ 翻譯做了、產品沒接。使用者看到的是硬編那句,白話版躺在 JSON 裡沒人叫。
#   ★ 我差一點又加一批語意重複的新鍵(而且格式用 %(x)s,既有的是 {x})——
#     動手前要先問「這個東西是不是已經有正本了」,本庫今天已為此付過兩次。
# ★★ 順序是承重的(33f17 2026-09-09 23:3x 抓到):連續兩次 insert(0) 會讓
#   **後 insert 的排前面** ⇒ 主庫會贏本樹,而 i18n_lib 的字典根綁 __file__
#   ⇒ 載到主庫那份就讀主庫的 i18n/*.json,本樹的新鍵不存在。
#   ★ 我這裡沒中是**運氣**:主庫那條本來就在 sys.path,第二次 insert 被跳過。
#   ⇒ 本樹用 insert(0)、fallback 用 append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)
import re
import time

CODE_RE = re.compile(r"[A-G][0-9]+[a-z]?")

# ★ 併行性與優先序**要算,不要用散文寫** —— 散文只有人會讀,而且會過期。
#   依賴圖的正本是大綱,解析器已經在 outline-progress.py 裡,直接 import 它,
#   不在這裡重寫第二份圖(今天為了「造第二個載體」付過學費)。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "outline_progress", pathlib.Path(__file__).resolve().parent / "outline-progress.py")
    outline_progress = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(outline_progress)
except Exception:                       # ★ 讀不到就說出口,不靜默退成「沒有依賴圖」
    outline_progress = None

RC_OK = 0
RC_USAGE = 2
RC_NO_REASON = 3          # 說不出「為什麼只有真人能決定」
RC_HAS_PENDING = 4        # list --exit-nonzero-if-any 用:有待決事項

DEFAULT_WARN_AFTER_S = 2430   # ★ 昨天那一課的基線,不另發明數字


def store() -> pathlib.Path:
    root = os.environ.get("EZ2_AIB_HOME") or (pathlib.Path.home() / ".ez2-aib")
    d = pathlib.Path(root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "pending-human-decisions.json"


def load():
    p = store()
    if not p.exists():
        return {"schema": "aib-pending-human-decisions/v1", "items": []}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError) as e:
        # ★ 讀不動不是「沒有待決事項」——那兩者的處置相反。
        sys.stderr.write(T("aib.pending.err_store_unreadable", {"err": e}))
        sys.exit(RC_USAGE)


def save(d):
    store().write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def cmd_add(args) -> int:
    if not (args.only_human or "").strip():
        sys.stderr.write(
            "pending-decisions: 沒寫「為什麼只有真人能決定」,不准登記。\n"
            "  ★ 答不出來的,就不該掛在他身上 —— 那是我在把自己的判斷外包。\n")
        return RC_NO_REASON
    d = load()
    if any(i["id"] == args.id for i in d["items"] if not i.get("resolved_at")):
        sys.stderr.write(T("aib.pending.already_open2", {"id": args.id}))
        return RC_USAGE
    d["items"].append({
        "id": args.id,
        "what": args.what,
        "only_human_because": args.only_human,
        # ★ 空的話這筆等待不痛不癢,也要說出來 —— 不痛不癢的等待會稀釋整張清單。
        "blocks": args.blocks or "(沒有東西被它擋著)",
        "note": args.note,
        "opened_at": now_iso(),
        "opened_epoch": time.time(),
        "resolved_at": None,
        "decision": None,
    })
    save(d)
    print(json.dumps({"added": args.id}, ensure_ascii=False))
    return RC_OK


def cmd_resolve(args) -> int:
    d = load()
    for i in d["items"]:
        if i["id"] == args.id and not i.get("resolved_at"):
            i["resolved_at"] = now_iso()
            i["waited_s"] = int(time.time() - i["opened_epoch"])
            i["decision"] = args.decision
            save(d)
            print(json.dumps({"resolved": args.id, "waited_s": i["waited_s"],
                              "decision": args.decision}, ensure_ascii=False))
            return RC_OK
    sys.stderr.write(T("aib.pending.not_found2", {"id": args.id}))
    return RC_USAGE


def open_items(d):
    return [i for i in d["items"] if not i.get("resolved_at")]


def _outline_graph(spec):
    """回 (deps, kinds) —— 讀不到就回 (None, None),而不是空圖。
    ★ 空圖的意思是「沒有依賴」,那與「我讀不到依賴」相反。"""
    if outline_progress is None or not spec or not pathlib.Path(spec).is_file():
        return None, None
    md = pathlib.Path(spec).read_text()
    outline, board = outline_progress.parse(md)
    marks = {l.strip(): m for l, m in board}
    deps = {}
    for code, _ in outline:
        deps[code] = outline_progress.deps_of(md, code) or []
    kinds = outline_progress.classify(outline, marks, md)
    return deps, kinds


def _unlocked_by(codes, deps, kinds):
    """假設 codes 這些項不再擋人,能直接放行幾項、連鎖放行幾項。
    ★ 這是**算出來的**,不是我判斷的。"""
    if deps is None:
        return None
    freed = set(codes)
    direct = {c for c, ds in deps.items()
              if c not in freed and set(ds) & freed}
    # 連鎖:把 direct 也當成解開,再往下滾一次不動點
    closure, frontier = set(direct), set(direct)
    while frontier:
        nxt = {c for c, ds in deps.items()
               if c not in closure and c not in freed and set(ds) & (freed | closure)}
        frontier = nxt - closure
        closure |= nxt
    return {"direct": sorted(direct), "chain": sorted(closure)}


def cmd_list(args) -> int:
    d = load()
    items = open_items(d)
    now = time.time()
    deps, kinds = _outline_graph(args.spec)
    rows = []
    for i in items:
        waited = int(now - i["opened_epoch"])
        # ★ 第一版用 `c[0].isalpha()` 挑大綱碼 —— 而 Python 的 isalpha() 對**中文回 True**,
        #   於是自由文字「所有已完成的工作都還沒進 main」被當成一個大綱碼。
        #   過濾的意圖對,欄位沒有分辨力(本庫記過的同一族)。⇒ 用碼自己的形狀。
        codes = [c.strip() for c in str(i.get("blocks") or "").split(",")
                 if CODE_RE.fullmatch(c.strip())]
        rows.append(dict(i, waited_s=waited, over_baseline=waited > args.warn_after,
                         block_codes=codes, unlocks=_unlocked_by(codes, deps, kinds)))

    # ── 併行性:影響集合不交集 ⇒ 可以同時回答 ──
    groups, note = [], None
    if deps is None:
        note = "讀不到大綱依賴圖 —— 併行性與優先序**算不出來**,這不是「都可以併行」"
    else:
        remaining = list(rows)
        while remaining:
            head = remaining.pop(0)
            grp = [head]
            hs = set(head["unlocks"]["chain"]) | set(head["block_codes"])
            for r in list(remaining):
                rs = set(r["unlocks"]["chain"]) | set(r["block_codes"])
                if not (hs & rs):
                    grp.append(r); remaining.remove(r); hs |= rs
            groups.append(grp)

    if args.json:
        print(json.dumps({"open": len(rows), "warn_after_s": args.warn_after,
                          "items": rows,
                          "parallel_groups": [[r["id"] for r in g] for g in groups],
                          "graph_note": note}, ensure_ascii=False, indent=2))
        return RC_HAS_PENDING if (args.exit_nonzero_if_any and rows) else RC_OK

    if not rows:
        print(T("aib.pending.none", {"path": str(store())}))
        return RC_OK

    print(T("aib.pending.title", {"n": len(rows)}))
    for r in rows:
        star = "★ " if r["over_baseline"] else "  "
        print("%s%-16s %s  %s" % (star, r["id"],
                                  T("aib.pending.waited", {"sec": r["waited_s"]}), r["what"]))
        print("%18s %s" % ("", T("aib.pending.only_human",
                                         {"why": r["only_human_because"]})))
        if r["unlocks"] is None:
            print("%18s %s" % ("", T("aib.pending.unlocks_unknown")))
        else:
            print("%18s %s" % ("", T("aib.pending.unlocks", {
                "d": len(r["unlocks"]["direct"]), "dl": r["unlocks"]["direct"] or "",
                "c": len(r["unlocks"]["chain"]), "cl": r["unlocks"]["chain"] or ""})))

    # ── 底部四問,全部由上面的數字導出 ────────────────────
    print("\n" + T("aib.pending.h_parallel"))
    if note:
        print("  ★ %s" % note)
    else:
        for n, g in enumerate(groups, 1):
            print("  " + T("aib.pending.group",
                            {"n": n, "ids": "、".join(r["id"] for r in g)}))
        # ★ 這裡第一版印反了:我把「不交集⇒可併行」的放進**同一組**,
        #   所以「只有一組」的意思是**全部可併行**,而我卻印成「互相牽動」。
        #   顯示與計算相反,而兩者都很像正常 —— 這種反轉只有把定義寫在旁邊才看得出來。
        if len(groups) == 1 and len(rows) > 1:
            print("  " + T("aib.pending.all_parallel"))
        elif len(groups) > 1:
            print("  " + T("aib.pending.cross_group"))
        # ★ 空洞的真要標出來:影響集合是空的,跟誰都不交集。
        trivial = [r["id"] for r in rows if not (set(r["unlocks"]["chain"]) | set(r["block_codes"]))]
        if trivial:
            print("  " + T("aib.pending.vacuous_parallel",
                            {"ids": "、".join(trivial)}))

    print("\n" + T("aib.pending.h_tradeoff"))
    print("  " + T("aib.pending.pro_parallel",
                    {"sec": max((r["waited_s"] for r in rows), default=0)}))
    print("  " + T("aib.pending.con_parallel"))
    print("  " + T("aib.pending.pro_serial"))
    print("  " + T("aib.pending.con_serial"))

    print("\n" + T("aib.pending.h_maximize"))
    if deps is None:
        print("  " + T("aib.pending.no_graph"))
    else:
        ranked = sorted(rows, key=lambda r: (-len(r["unlocks"]["chain"]),
                                             -len(r["unlocks"]["direct"]), r["id"]))
        for r in ranked:
            print("  " + T("aib.pending.rank_row",
                           {"id": r["id"], "c": len(r["unlocks"]["chain"])}))
        print("  " + T("aib.pending.rank_hint"))

    print("\n" + T("aib.pending.h_must"))
    if deps is None:
        print("  " + T("aib.pending.no_graph"))
    else:
        top = ranked[0]
        zero = [r["id"] for r in rows if not r["unlocks"]["chain"]]
        print("  " + T("aib.pending.top",
                        {"id": top["id"], "c": len(top["unlocks"]["chain"])}))
        if zero:
            print("  " + T("aib.pending.zero_unlock", {"ids": "、".join(zero)}))
            print("  " + T("aib.pending.zero_unlock_note"))
    if args.exit_nonzero_if_any and rows:
        return RC_HAS_PENDING
    return RC_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="待真人決策登記簿")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--id", required=True)
    p.add_argument("--what", required=True)
    p.add_argument("--only-human", required=True, help="為什麼只有真人能決定")
    p.add_argument("--blocks", help="它擋著哪些大綱項,逗號分隔(例:C2,C4)")
    p.add_argument("--note", help="人看的補充")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("resolve")
    p.add_argument("--id", required=True)
    p.add_argument("--decision", required=True)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.add_argument("--warn-after", type=int, default=DEFAULT_WARN_AFTER_S)
    p.add_argument("--exit-nonzero-if-any", action="store_true")
    p.add_argument("--spec", default=str(pathlib.Path(__file__).resolve().parents[2]
                                         / "docs" / "spec"
                                         / "USECASE-CATEGORY-AND-VAULT-ENTITLEMENT-v1.md"),
                   help="大綱規格檔;併行性與優先序從它的依賴圖算出來")
    p.set_defaults(func=cmd_list)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
