#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""V1 planner: read fingerprint rows, print must-run / would-skip / external.

V1 never changes the run list. would-skip is forbidden when the row is
unmeasured, external, or the input fingerprint shrank.

## P2 加的那一層(2026-09-26,設計稿 §3 第 3 步、§7 第 1 條)

V1 那一段(`plan_one` / `vs_run` / `apply_plan` / `summarize` 與 `--current/--previous`)
**一個位元組都沒改**:`tools/verify/shadow_tracer.py` 與 `tests/test-verify-planner.py`
都在吃它,改它等於同時動兩個消費者。

P2 在旁邊加一層 `plan_changed()`,吃 `tools/verify/select_changed.py` 挑出來的影響集
與 `tools/verify/reuse_index.py` 的沿用判斷,吐出**要跑哪些、沿用哪些、抽查哪些、各自理由**。
本層**不跑任何測試** —— 執行器不在本包(派工單第 4 條)。

### 抽查(設計稿 §7 第 1 條的兜底)

可沿用的裡面隨機抽一成以內照跑。三件寫死在這裡,因為三件都踩過:

1. **抽樣數 `n = max(1, ceil(池 × 比例))`,池非空就至少抽一支。**
   `floor` 會讓 10 支以下的池抽到 0 —— 而「抽查 0 支、全部一致」與「抽查過了」
   在收據上逐字相同。一個永遠不開火的安全網,在修好之前跟真的一樣。
2. **種子是算出來的、寫進輸出的**:`sha256(tree_sha|pct|池的排序)`。
   同一棵樹重跑抽同一批(可複現、可覆核);換一棵樹抽不同批(才叫抽查)。
   用 `random.seed()` 的全域狀態會被呼叫端的其他亂數用掉,所以用自己的 `Random` 實例。
3. **抽查不一致的處置是回報值,不是印一行**:`sample_verdict()` 回
   `{"consistent": False, "action": "invalidate-pool-fallback-strict", …}`,
   呼叫端據此作廢沿用池、退回嚴格。只印警告的話,收據仍會寫「沿用 31 支」。

### 永遠必跑(不看指紋)

`previous-red`(上一輪紅)與 `test-modified`(這次改到它自己)由 `reuse_index.decide`
判;本層**再擋一次**,並且是在 `decide` 回 `reuse` 之後擋 —— 兩道不同來源。
09-26 落刀驗過(刀在 `tests/test-verify-p2-planner.py` 裡逐格內聯、兩臂都跑):
拿掉「改到它自己」那一道 ⇒ `test_20_changed_test_never_reused` 轉紅;
拿掉 `ALWAYS_RUN_REASONS` 那一道 ⇒ `test_21_always_run_reasons_never_reused` 轉紅。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

# ── 畫面文字走四語字典(tests/test-tools-i18n-ratchet.py:tools/**/*.py 的中文字面不准流到輸出口)──
# ★ 順序是承重的(照 consumer_scope.py 的同一條):本樹 insert(0)、fallback append。
_COCKPIT = Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT))
_COCKPIT_FALLBACK = Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_COCKPIT_FALLBACK) not in sys.path:
    sys.path.append(str(_COCKPIT_FALLBACK))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)


VERDICTS = ("must-run", "would-skip", "external")

# 抽查比例:設計稿 §2 說 5–10%,預設 5%。
SAMPLE_PCT_DEFAULT = 5.0
SAMPLE_PCT_MIN = 5.0
SAMPLE_PCT_MAX = 10.0


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def fingerprint_shrink(current, previous):
    """Input fingerprint may only grow. Missing previous items => shrink."""
    cur = set(current.get("inputs") or [])
    prev = set(previous.get("inputs") or [])
    return bool(prev - cur)


def plan_one(row, previous):
    if row.get("external"):
        return "external"
    if row.get("measured") is not True:
        return "must-run"
    if not previous:
        return "must-run"
    if fingerprint_shrink(row, previous):
        return "must-run"
    if previous.get("measured") is True and not previous.get("external"):
        if previous.get("key") == row.get("key"):
            return "would-skip"
    return "must-run"


def vs_run(row, previous, verdict):
    if verdict == "would-skip":
        if previous is None:
            return "plan-wrong"
        if int(row.get("rc") or 0) != 0:
            return "plan-wrong"
        if int(row.get("rc") or 0) != int(previous.get("rc") or 0):
            return "rc-diff"
        if (row.get("tail") or "") != (previous.get("tail") or ""):
            return "tail-diff"
        return "match"
    if (
        verdict == "must-run"
        and previous is not None
        and previous.get("measured") is True
        and not row.get("external")
        and row.get("measured") is True
        and not fingerprint_shrink(row, previous)
        and previous.get("key") == row.get("key")
        and int(row.get("rc") or 0) == int(previous.get("rc") or 0)
        and (row.get("tail") or "") == (previous.get("tail") or "")
    ):
        return "plan-wrong"
    return "match"


def apply_plan(current_rows, previous_rows):
    prev_map = {r.get("test"): r for r in previous_rows}
    out = []
    for row in current_rows:
        prev = prev_map.get(row.get("test"))
        verdict = plan_one(row, prev)
        row = dict(row)
        row["verdict"] = verdict
        row["reused"] = False
        row["vs_run"] = vs_run(row, prev, verdict)
        out.append(row)
    return out


def summarize(rows):
    counts = {
        "must-run": 0,
        "would-skip": 0,
        "external": 0,
        "unmeasured": 0,
        "plan-wrong": 0,
        "total": len(rows),
    }
    for row in rows:
        v = row.get("verdict")
        if v in counts:
            counts[v] += 1
        if row.get("measured") is not True:
            counts["unmeasured"] += 1
        if row.get("vs_run") == "plan-wrong":
            counts["plan-wrong"] += 1
    return counts


# ── P2:影響集 × 沿用 × 抽查 ──────────────────────────────────────────────────

#: 不看指紋、一律必跑的理由。這組字串是**契約**:`reuse_index.decide` 回的理由碼
#: 與本層自己判的都用同一組名字,收據與回報才對得上同一件事。
ALWAYS_RUN_REASONS = ("previous-red", "previous-timeout", "test-modified",
                      "previous-rc-absent")


# ── 排序(設計稿 §6.1「沒有 an internal service 時的固定規則」;a seat 09-26 指派)──────────────
#
# **鐵則:排序只影響順序,不影響跑不跑、不影響算不算綠。**
# 有沒有排序,結論逐支相同;差別只在「第一個紅出現在第幾分鐘」。
# 所以本段任何一條算不出來,都只是名次往後,不會讓一支測試消失。
#
# 名次由小到大(a seat 09-26 的順序,加上他補的識別字那一條):
#   0 previous-red     上一輪紅過或這次改到它自己
#   1 reverse-hit      影子續跑量到它讀過改動檔
#   2 hunk-identifier  命中改動區塊裡被定義的名字
#   3 direct-static    引用完整路徑,或直接 import
#   4 bounded-name     只命中帶邊界的檔名
#   5 indirect         尺引用尺 / 一律跑清單 / 掃全庫的閘
# 第三欄是字典鍵,說明文字在 i18n/{zh-TW,en,ja,ko}.json,輸出時才翻。
ORDER_RULES = (
    (0, "previous-red", "aib.vp2.order.previous_red"),
    (1, "reverse-hit", "aib.vp2.order.reverse_hit"),
    (2, "hunk-identifier", "aib.vp2.order.hunk_identifier"),
    (3, "direct-static", "aib.vp2.order.direct_static"),
    (4, "bounded-name", "aib.vp2.order.bounded_name"),
    (5, "indirect", "aib.vp2.order.indirect"),
)
#: 直接命中:測試原始碼寫著改動檔本身(不是「帶邊界的檔名」那種寬的)。
DIRECT_HIT_RULES = ("r0-changed-test", "r1-path-literal", "r3-import")
_ORDER_REASONS_RED = ("previous-red", "previous-timeout", "test-modified",
                      "previous-rc-absent")


def order_rank(row):
    """一列(plan_changed 的 rows 元素)→ (名次, 理由)。

    ★ 每一支都要有理由:沒有理由的名次沒有人能覆核,而「排錯了」與「排對了」
      在收據上都只是一個順序。
    """
    rules = {w.get("rule") for w in (row.get("why") or [])}
    if row.get("reason") in _ORDER_REASONS_RED:
        return 0, "previous-red"
    if "r8-measured-reverse" in rules:
        return 1, "reverse-hit"
    if (row.get("signals") or {}).get("hunk_identifiers"):
        return 2, "hunk-identifier"
    if rules & {"r1-path-literal", "r3-import", "r0-changed-test"}:
        return 3, "direct-static"
    if "r2-basename-bounded" in rules:
        return 4, "bounded-name"
    return 5, "indirect"


def order_rows(rows, durations=None):
    """把 rows 排成跑單順序。回新的 list(不改原來的)。

    同名次內按**歷史耗時由短到長**(設計稿 §6.1 第四條):短的先跑,
    同樣的等待時間內看到比較多結果。
    ★ 沒有耗時歷史的排在**有歷史的後面**,並在理由裡寫 `duration:unknown` ——
      沒有歷史當成 0 會讓沒跑過的一律插隊,而那與「它真的很快」同形。
    """
    durations = durations or {}
    out = []
    for row in rows:
        rank, why = order_rank(row)
        d = durations.get(row.get("test"))
        known = isinstance(d, (int, float)) and not isinstance(d, bool)
        row = dict(row)
        row["order_rank"] = rank
        row["order_why"] = why if known else (why + "|duration:unknown")
        out.append((rank, 0 if known else 1, d if known else 0.0,
                    row.get("test") or "", row))
    out.sort(key=lambda t: t[:4])
    return [t[4] for t in out]


def sample_seed(tree_sha, pct, pool):
    """抽樣種子:同一棵樹重跑抽同一批,換樹抽不同批。算得出來、寫得進收據。"""
    raw = "%s|%s|%s" % (tree_sha or "", pct, "\n".join(sorted(pool)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def pick_sample(pool, pct=SAMPLE_PCT_DEFAULT, tree_sha="", seed=None):
    """從可沿用的池裡抽 pct%(向上取整,池非空至少 1 支)。回 (抽到的, 種子)。

    ★ 向上取整 + 至少 1:`floor` 會讓 10 支以下的池抽到 0,而抽查 0 支的收據
      與抽查過了逐字相同(第 69 項:空殼會讓等產品的格子轉綠)。
    """
    pool = sorted(pool)
    if not pool:
        return [], seed or sample_seed(tree_sha, pct, pool)
    pct = max(0.0, float(pct))
    n = min(len(pool), max(1, int(math.ceil(len(pool) * pct / 100.0))))
    seed = seed or sample_seed(tree_sha, pct, pool)
    rnd = random.Random(seed)          # 不用全域 random:呼叫端的其他亂數會吃掉狀態
    return sorted(rnd.sample(pool, n)), seed


def plan_changed(selected, decisions, changed=(), pct=SAMPLE_PCT_DEFAULT,
                 tree_sha="", seed=None, durations=None):
    """影響集 + 沿用判斷 → 跑單。

    selected  : select_changed.select() 的 `selected` 欄(每項有 test 與 why)
    decisions : {test: (verdict, reason, detail)} —— reuse_index.Index.decide 的輸出
    changed   : 這次改動的檔(用來再擋「改到它自己」與「直接命中」兩道)

    回 {"must_run": [...], "reuse": [...], "sample": [...], "rows": [...], "counts": {...}}
    每一項都帶 `why`(為什麼被挑進來)與 `reason`(為什麼跑/為什麼沿用)。
    """
    changed = set(changed or ())
    rows = []
    for item in selected:
        test = item["test"] if isinstance(item, dict) else str(item)
        why = item.get("why", []) if isinstance(item, dict) else []
        verdict, reason, detail = decisions.get(
            test, ("must-run", "no-decision", ""))
        # ★ 第二道:不看指紋一律必跑的那幾條,在 decide 說 reuse 之後再擋一次。
        #   兩道不同來源(一道讀上一輪的列、一道讀這次的改動清單)。
        if verdict == "reuse" and test in changed:
            verdict, reason, detail = "must-run", "test-modified", test
        if verdict == "reuse" and reason in ALWAYS_RUN_REASONS:
            verdict = "must-run"
        # ★ 第三道(a seat 09-26 p2 第 2 輪審出,艦長 a seat 裁重大):
        #   測試原始碼**直接寫著**這次改到的檔(完整路徑字面 / import / 就是它自己),
        #   而 decide 只看上一輪**量到的** inputs —— 上一輪量不全時(09-13 實測 40 列
        #   measured 裡 24 列是假量到;本包反查覆蓋 0.8%)會判 fingerprint-match 而整支不跑。
        #   靜態挑選存在的理由就是補追蹤器的盲區,沿用層不得把它的結論靜靜蓋掉。
        #   只收三條「直接」規則;r2 帶邊界檔名太寬(一支 aib 就 157 支),收了等於沒有沿用,
        #   它的漏由抽查與每晚全跑兜底。
        # ★★ 第二版(a seat 第二次審出):第一版自己用「via 在 changed 裡」推導,
        #   改到被 source 的殼時 via 是展開出來的那支 ⇒ 不觸發。現在只讀 select_changed
        #   標好的 direct 旗標 —— 知道 changed 與展開全貌的只有那一端。
        #   舊格式的理由沒有 direct 欄 ⇒ **保守**:直接三條一律當直接命中(寧可多跑)。
        #   不回頭用 via 推導,那會變回兩個來源。
        if verdict == "reuse":
            direct = sorted({w.get("via") or "?" for w in why
                             if (w.get("direct") if "direct" in w
                                 else w.get("rule") in DIRECT_HIT_RULES)})
            if direct:
                verdict, reason, detail = ("must-run", "static-direct-hit",
                                           "、".join(direct[:3]))
        signals = (item.get("signals") or {}) if isinstance(item, dict) else {}
        rows.append({"test": test, "verdict": verdict, "reason": reason,
                     "detail": detail, "why": why, "signals": signals})
    rows = order_rows(rows, durations)
    pool = [r["test"] for r in rows if r["verdict"] == "reuse"]
    sample, used_seed = pick_sample(pool, pct=pct, tree_sha=tree_sha, seed=seed)
    sample_set = set(sample)
    for r in rows:
        r["sampled"] = r["test"] in sample_set
    # ★ `must_run` 保持**排序後的順序**,不是 sorted():它是跑單,順序就是產出。
    must = [r["test"] for r in rows if r["verdict"] == "must-run"]
    reasons, ranks = {}, {}
    for r in rows:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        k = "%d-%s" % (r["order_rank"], r["order_why"].split("|")[0])
        ranks[k] = ranks.get(k, 0) + 1
    return {
        "rows": rows,
        "must_run": must,
        "reuse": sorted(pool),
        "sample": sample,
        "sample_seed": used_seed,
        # ★ 種子沒吃到樹 ⇒ **每一顆都抽同一批**,而抽查看起來照常在跑。
        #   一個永遠問同幾支的抽查,在修好之前跟真的一樣。所以寫出來。
        "sample_seed_basis": ("tree-sha" if tree_sha and tree_sha != "unknown"
                              else "no-tree-sha:same-sample-every-commit"),
        "sample_pct": pct,
        # 跑單:照排序後的順序,必跑與抽查混在一起(抽到的那幾支也照名次跑)
        "to_execute": [r["test"] for r in rows
                       if r["verdict"] == "must-run" or r["sampled"]],
        "order": [{"test": r["test"], "rank": r["order_rank"],
                   "why": r["order_why"]} for r in rows],
        "order_rules": [{"rank": a, "name": b, "means": T(c)}
                        for a, b, c in ORDER_RULES],
        "counts": {
            "selected": len(rows),
            "must_run": len(must),
            "order_ranks": ranks,
            "reuse": len(pool),
            "sample": len(sample),
            "reasons": reasons,
        },
    }


def sample_verdict(sampled, fresh_rows, previous_rows):
    """抽查判讀:抽到的那幾支真跑之後,結果與上一輪逐支比。

    回 {"consistent": bool, "checked": N, "mismatch": [...], "action": str}
    不一致 ⇒ action = "invalidate-pool-fallback-strict"(作廢沿用池、退回嚴格)。

    ★ 回報值不是印一行:只印警告的話,收據仍會寫「沿用 N 支」,而那 N 支已經不可信。
    ★ 抽到的支數是 0 ⇒ consistent=False、action="no-sample",**不是** True。
      「沒抽到所以沒有不一致」與「抽了而且一致」是兩件事(一個觀測值兩個來源)。
    """
    fresh = {r.get("test"): r for r in fresh_rows}
    prev = {r.get("test"): r for r in previous_rows}
    sampled = sorted(set(sampled))
    if not sampled:
        return {"consistent": False, "checked": 0, "mismatch": [],
                "action": "no-sample",
                "why": "抽查支數 0:沒抽到不等於一致"}
    mismatch = []
    for t in sampled:
        now, was = fresh.get(t), prev.get(t)
        if now is None:
            mismatch.append({"test": t, "kind": "not-run"})
            continue
        if was is None:
            mismatch.append({"test": t, "kind": "no-previous"})
            continue
        # ★ 缺 rc 不得被 `or 0` 當成綠:「沒有這一欄」與「rc 是 0」是兩件事,
        #   而抽查正是要把它們分開(沒有證據被讀成證據顯示沒問題)。
        if not isinstance(now.get("rc"), int) or isinstance(now.get("rc"), bool) \
                or not isinstance(was.get("rc"), int) or isinstance(was.get("rc"), bool):
            mismatch.append({"test": t, "kind": "rc-absent",
                             "now": now.get("rc"), "was": was.get("rc")})
        elif int(now["rc"]) != int(was["rc"]):
            mismatch.append({"test": t, "kind": "rc-diff",
                             "now": now.get("rc"), "was": was.get("rc")})
        elif (now.get("tail") or "") != (was.get("tail") or ""):
            mismatch.append({"test": t, "kind": "tail-diff"})
    return {
        "consistent": not mismatch,
        "checked": len(sampled),
        "mismatch": mismatch,
        "action": "ok" if not mismatch else "invalidate-pool-fallback-strict",
    }


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="planner")
    # V1 介面(shadow_tracer 與 test-verify-planner.py 在吃):--current/--previous
    ap.add_argument("--current", default="")
    ap.add_argument("--previous", default="")
    # P2 介面:吃 select_changed 的 JSON + reuse_index 的 JSON
    ap.add_argument("--selected", default="",
                    help=T("aib.vp2.help.selected"))
    ap.add_argument("--decisions", default="",
                    help=T("aib.vp2.help.decisions"))
    ap.add_argument("--changed", default="", help=T("aib.vp2.help.changed_plain"))
    ap.add_argument("--sample-pct", type=float, default=SAMPLE_PCT_DEFAULT)
    ap.add_argument("--tree-sha", default="")
    args = ap.parse_args(argv)

    if args.selected:
        sel = _load_json(args.selected)
        dec_raw = _load_json(args.decisions) if args.decisions else {"decisions": []}
        decisions = {d["test"]: (d.get("verdict", "must-run"),
                                 d.get("reason", ""), d.get("detail", ""))
                     for d in dec_raw.get("decisions", [])}
        changed = []
        if args.changed:
            changed = [l.strip() for l in open(args.changed, encoding="utf-8")
                       if l.strip()]
        elif isinstance(sel, dict):
            changed = sel.get("changed") or []
        out = plan_changed(sel.get("selected", []), decisions, changed=changed,
                           pct=args.sample_pct,
                           # select_changed 的輸出自己帶 tree_sha;--tree-sha 只是覆寫
                           tree_sha=args.tree_sha or sel.get("tree_sha", ""))
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0

    if not args.current:
        print(T("aib.vp2.err.need_mode"), file=sys.stderr)
        return 2
    current = load_rows(args.current)
    previous = load_rows(args.previous) if args.previous else []
    planned = apply_plan(current, previous)
    for row in planned:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
