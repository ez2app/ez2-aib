#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""影子續跑說明 v2.1 的「💡 下一次這樣做會更省」與「📋 上一輪的建議有沒有照做」(the maintainer 10-14 19:5x,經 a seat 派工;
示意 ~/.ez2-aib/pending/MOCK-shadow-explain-v2-8f366-1014.md)。

- build(ctx):依這一輪的數字自動算建議,每條有編號 N1、N2…、估省多少、怎麼驗;寫進
  <狀態根>/state/shadow-explain/<席碼>.json 的 next_actions(shadow_explain.record)。
- judge(prev, ctx):下一輪跑完逐條判 照做了 / 沒照做(原因)/ 不適用 / 要人判。能自動判的自動判;
  判不了的照實寫「要人判」,不猜。
- 純函式(ctx 是 dict),好測;文字一律走 aib.advice.* 四語字典。

ctx 欄位:lease_wait_s、lease_why(big/small/inherited/nested/gave_up/unavailable)、ahead(排在誰後面,字串或 None)、
tests(aib test 的結果列:name、reused、unverified、why、why_detail、elapsed_s)、no_reuse(這一輪帶 --fresh / --strict /
AIB_TEST_NO_REUSE=1 ⇒ 該旗標的名字,否則 None)。
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src" / "cockpit"))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()   # 語系單一來源(同 shadow_explain / test_entry):環境變數之外還有設定檔層

QUEUE_MIN_S = 600          # 排隊超過 10 分鐘才算值得改的事
UNMEASURED_MIN = 10        # 「上次量不到讀了哪些檔」至少這麼多支才提
HOT_MIN = 10               # 一個檔害這麼多支重跑才算熱檔
HOT_TOP = 3
UNVERIFIED_SHOW = 5
CHANGED_WHYS = ("input-fp-changed", "input-changed")


def T(key, params=None):
    return i18n_lib.t("aib.advice." + key, params, locale=LOC)


def _ran(tests):
    return [r for r in tests or () if not r.get("reused") and not r.get("unverified")]


def _secs(rows):
    return sum(float(r.get("elapsed_s") or 0) for r in rows if isinstance(r.get("elapsed_s"), (int, float)))


def _paths(detail):
    """input-fp-changed / input-changed 的細節「kind:路徑、kind:路徑…+N」→ 路徑清單(沒有路徑的項略過)。"""
    out = []
    for it in str(detail or "").split("…+")[0].split("、"):
        it = it.strip()
        if not it:
            continue
        kind, sep, rest = it.partition(":")
        path = rest if sep and re.fullmatch(r"[a-z_]+", kind) else it
        path = path.split(":")[0] if ":" in path and not path.startswith("/") else path
        while path.startswith("./"):      # 追蹤器記 ./src/x.py、git diff 記 src/x.py:同一個檔(審查)
            path = path[2:]
        if not ("/" in path or "." in path):       # argv:… 這種沒有路徑的項不是檔
            continue
        if path and path not in out:
            out.append(path)
    return out


def hot_files(tests):
    """害最多支重跑的檔:[(路徑, 支數, 那幾支的結果列)],支數 ≥ HOT_MIN,最多 HOT_TOP 個。"""
    hits = collections.defaultdict(list)
    for r in _ran(tests):
        if r.get("why") in CHANGED_WHYS:
            for p in _paths(r.get("why_detail")):
                hits[p].append(r)
    top = sorted(hits.items(), key=lambda kv: -len(kv[1]))
    return [(p, len(rows), rows) for p, rows in top if len(rows) >= HOT_MIN][:HOT_TOP]


def build(ctx, dur):
    """回 next_actions 清單(可 JSON)。每條:id、kind、text、est(估省多少,已翻好)、verify(怎麼驗)、params(下一輪判用)。"""
    tests = ctx.get("tests") or ()
    acts = []

    def add(kind, params, est):
        n = "N%d" % (len(acts) + 1)
        acts.append({"id": n, "kind": kind, "params": params, "text": T(kind, params), "est": est,
                     "verify": T(kind + "_verify", params)})

    wait = float(ctx.get("lease_wait_s") or 0)
    if wait >= QUEUE_MIN_S:
        ahead = ctx.get("ahead") or T("ahead_unknown")
        add("queue", {"t": dur(wait), "ahead": ahead, "wait_s": round(wait, 1)}, T("est_time", {"t": dur(wait)}))
    um = [r for r in _ran(tests) if r.get("why") == "unmeasured"]
    if len(um) >= UNMEASURED_MIN:
        add("unmeasured", {"n": len(um)}, T("est_reuse", {"n": len(um), "t": dur(_secs(um))}))
    hot = hot_files(tests)
    if hot:
        what = T("sep").join(T("hot_item", {"path": p, "n": n}) for p, n, _r in hot)
        rows = list({id(r): r for _p, _n, rs in hot for r in rs}.values())    # 同一支讀了好幾個熱檔只算一次
        add("hot", {"what": what, "paths": [p for p, _n, _r in hot]}, T("est_time", {"t": dur(_secs(rows))}))
    unv = [r.get("name") for r in tests or () if r.get("unverified")]
    if unv:
        more = len(unv) - UNVERIFIED_SHOW
        add("unverified", {"n": len(unv), "names": unv,
                           "list": "、".join(unv[:UNVERIFIED_SHOW]) + (" …(+%d)" % more if more > 0 else "")},
            T("est_none"))
    return acts


def judge(prev, ctx, dur):
    """上一輪的 next_actions 逐條判 → [(id, status, reason)];status ∈ done / not_done / na / manual。"""
    out = []
    tests = ctx.get("tests") or ()
    for a in prev or ():
        if not isinstance(a, dict):
            continue
        kind, p, aid = a.get("kind"), a.get("params") or {}, str(a.get("id") or "?")
        if kind == "queue":
            wait = float(ctx.get("lease_wait_s") or 0)
            if ctx.get("lease_why") in ("small", "inherited", "nested"):
                out.append((aid, "na", T("j_queue_na")))
            elif wait < QUEUE_MIN_S:
                out.append((aid, "done", T("j_queue_done", {"t": dur(wait)})))
            else:
                out.append((aid, "not_done", T("j_queue_not", {"t": dur(wait),
                                                               "ahead": ctx.get("ahead") or T("ahead_unknown")})))
        elif kind == "unmeasured":
            flag = ctx.get("no_reuse")
            if flag:
                out.append((aid, "not_done", T("j_unmeasured_not", {"flag": flag})))
            else:
                n = sum(1 for r in _ran(tests) if r.get("why") == "unmeasured")
                out.append((aid, "done", T("j_unmeasured_done", {"n": n})))
        elif kind == "hot":
            out.append((aid, "manual", T("j_hot_manual")))
        elif kind == "unverified":
            names = set(p.get("names") or ())
            again = [r.get("name") for r in tests if r.get("unverified") and r.get("name") in names]
            if again:
                out.append((aid, "not_done", T("j_unverified_not", {"n": len(again)})))
            else:
                out.append((aid, "done", T("j_unverified_done")))
        else:
            out.append((aid, "manual", T("j_unknown_kind", {"kind": kind or "?"})))
    return out


def judge_lines(results):
    """📋 上一輪的建議逐條檢查(掛在 🧮 樹狀最下面);沒有上一輪建議 ⇒ []。"""
    if not results:
        return []
    out = [T("judge_head", {"n": len(results)})]
    for aid, status, why in results:
        out.append(T("judge_row", {"id": aid, "status": T("st_" + status), "why": why}))
    return out


def advice_lines(acts):
    """💡 下一次這樣做會更省;沒有建議 ⇒ 一行「這一輪看不出可以再省的地方」(一定印,AI 才不會以為漏了)。"""
    if not acts:
        return [T("head_none")]
    out = [T("head")]
    for a in acts:
        out.append(T("row", {"id": a["id"], "text": a["text"], "est": a["est"]}))
        out.append(T("row_verify", {"verify": a["verify"]}))
    return out
