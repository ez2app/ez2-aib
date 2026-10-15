#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""遇事不決:把「該問誰／該自己決／該去量」從腦子裡的判斷變成會回非零 rc 的路由。

## 為什麼要有這支

2026-09-09 一天之內用了十次「遇事不決」的判斷,每次靠記憶。靠記憶的規則會漂移,
而漂移的方向是**往下走**:能不問人就不問、能不開會就不開 —— 因為那比較快。
the maintainer 09-09 12:1x 常設令「卡住要選的不再等真人,改 AI 合議」拿掉了「等人」這個煞車,
★ 而那條令自己的咬人處是:**拿掉「等 the maintainer」之後,沒有東西會自己叫停。**
這支就是那個叫停的東西。

## 五道閘(順序不可換,前面的成立就不看後面)

  閘 0  這是「決定」還是「沒量」?  有機器可查的事實能定案 ⇒ MEASURE
        ★ 今天 A3 與 E2 都長得像決定(「要不要派?」),答案是一條 grep。
  閘 1  是不是真人保留格?          任一成立 ⇒ HUMAN
        (a) 不可逆且動使用者自己的資料  (b) 是政策不是接線
        (c) 完成條件本身逐字要真人      (d) 他保留的格
  閘 2  主導席自己定得了嗎?        有分辨器 且 決錯只影響一包 ⇒ CHAIR
  閘 3  其餘                        ⇒ COUNCIL
        特別是:影響規格前提、跨包、或**主導席自己是當事人**
  閘 4  決完之後誰會叫?            載體要有消費者,否則 rc=6
  閘 5  停止線畫了嗎?              COUNCIL/CHAIR 要寫「什麼情況要回頭找真人」,否則 rc=7

## 橫向鐵律:未量不得當成已量

所有 fact 必須**顯式**給 true/false,或**顯式**標 unmeasured。漏給 ⇒ rc=3。
★ 理由:今天最貴的錯法是「量不到」與「答案是否」同形。
★ 而 **任何一道閘**的 unmeasured 事實都**往上路由**(至少 COUNCIL)並且**印出來**——
  含閘 0:「還沒去看這是不是一條指令能定案」不等於「這不是量測題」。
  往上是保守方向,但保守不得是靜默的:靜默的保守與沒判斷在畫面上逐字相同。

## rc 契約

  0   路由成功
  2   用法錯
  3   有 fact 既沒給值也沒標 unmeasured(未量被當已量)
  4   宣稱 machine_answerable 卻給不出指令
  5   HUMAN 路線但選項沒有逐字後果(<2 個選項,或有選項缺 consequence)
  6   決定沒有載體,或載體沒有消費者
  7   COUNCIL/CHAIR 但沒畫停止線
"""

import os
import argparse
import json
import pathlib
import sys
import time

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 大綱 §610 的十三條地基逐字列著「i18n 是地基」,
#   而 2026-09-09 實測:tools/ 下 66 支印中文給人看的工具,只有 1 支真的走它。
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

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)


RC_OK = 0
RC_USAGE = 2
RC_UNDECLARED_FACT = 3
RC_NO_COMMAND = 4
RC_OPTIONS_WITHOUT_CONSEQUENCE = 5
RC_CARRIER_WITHOUT_CONSUMER = 6
RC_NO_STOP_LINE = 7


def _route_log_path():
    configured = os.environ.get("AIB_ROUTE_LOG")
    if configured:
        return pathlib.Path(configured).expanduser()
    aib_home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    return pathlib.Path(aib_home) / "logs" / "decide-route-runs.jsonl"


def record_route(out, rc):
    """留下 Stop hook 可消費的 JSONL;留痕失敗不得改寫路由 rc。"""
    path = _route_log_path()
    row = {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "route": out.get("route"),
        "rc": rc,
        "via": "decide-route.py",
    }
    if out.get("id") is not None:
        row["id"] = out.get("id")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as e:
        sys.stderr.write(T("aib.route.log_unwritable", {"path": path, "err": e}))

# 閘 1 的四個事實。★ 名字要說得出「為什麼只有真人能決」,不是只說「重要」。
HUMAN_FACTS = (
    "irreversible_user_data",      # 不可逆且動使用者自己的資料/筆記庫
    "policy_not_wiring",           # 是政策不是接線(既有測試明文放行的那種)
    "completion_requires_human",   # 完成條件本身逐字寫著要真人
    "human_reserved_cell",         # 他保留的格(例如推 main)
)
# 閘 2 的兩個事實,兩個都要 true 才輪得到 CHAIR。
CHAIR_FACTS = (
    "has_discriminator",           # 有量得到的分辨器
    "blast_radius_one_package",    # 決錯了只影響一包
)
# 閘 3 的加權事實:成立就不准走 CHAIR(主導席是當事人時不能自己判自己)。
COUNCIL_FORCING_FACTS = (
    "chair_is_party",
    "affects_spec_premise",
)
ALL_FACTS = ("machine_answerable",) + HUMAN_FACTS + CHAIR_FACTS + COUNCIL_FORCING_FACTS

UNMEASURED = "unmeasured"

# ★ 虛無值:欄位填了字,而那個字逐字說「沒有」。
#   2026-09-09 壓測自己這支工具時抓到:carrier_consumer 填「沒有東西讀檔名」照樣 rc=0。
#   非空檢查不是閘,是欄位。
#   ★★ 落空端要具名:這只是**形狀檢查不是證明**。填「tools/x.py 會讀」而 tools/x.py
#      根本不讀它,這支工具看不出來 —— 那一格要靠交件時的實跑,不在這裡。
NIHILISTIC = {"", "-", "—", "無", "沒有", "none", "n/a", "na", "tbd", "待定", "未定",
              "不需要", "不用", "沒", "no", "nil", "null", "todo", "?", "??"}


def _is_nihilistic(text):
    t = str(text or "").strip().lower()
    if t in NIHILISTIC:
        return True
    # 「沒有東西讀…」「沒有人會…」這種:開頭就否定
    return t.startswith(("沒有", "無人", "不會有", "no one", "nothing"))


def _fact(case, name):
    """回 (value, why)。value ∈ {True, False, "unmeasured"}。
    ★ 缺欄不回 False —— 缺欄是 rc=3,不是「否」。"""
    facts = case.get("facts")
    if not isinstance(facts, dict) or name not in facts:
        return None, None
    raw = facts[name]
    if isinstance(raw, dict):
        v = raw.get("value")
        why = raw.get("why") or raw.get("command") or ""
    else:
        v, why = raw, ""
    if v is UNMEASURED or v == UNMEASURED:
        return UNMEASURED, why
    if isinstance(v, bool):
        return v, why
    return None, why


def route(case):
    """回 (rc, result_dict)。純函式,不碰檔案 —— 臂要能直接餵字典。"""
    missing = [n for n in ALL_FACTS if _fact(case, n)[0] is None]
    if missing:
        return RC_UNDECLARED_FACT, {
            "route": None,
            "error": "有 fact 既沒給值也沒標 unmeasured",
            "missing_facts": sorted(missing),
            "why_this_is_red": "未量與『答案是否』同形;缺欄一律不得預設為 false",
        }

    notes = []
    escalated = []

    # ── 閘 0:這是決定還是沒量 ──────────────────────────────
    ma, ma_cmd = _fact(case, "machine_answerable")
    # ★★ 2026-09-09 `9bb62` 的第二方案例抓到:閘 0 的未量原本被靜默吞掉。
    #   本檔標題寫的是「**橫向**鐵律:未量不得當成已量」,而細則只寫了閘 1 ——
    #   規則寫成橫向的、實作只蓋一道閘,而標題讀起來像全都涵蓋了。
    #   「還沒去看這是不是一條指令能定案」不等於「這不是量測題」。
    unmeasured_gate0 = ["machine_answerable"] if ma == UNMEASURED else []
    if ma is True:
        if not str(ma_cmd).strip():
            return RC_NO_COMMAND, {
                "route": None,
                "error": "宣稱 machine_answerable 卻給不出指令",
                "why_this_is_red": "『這可以量』而給不出怎麼量,與沒量過同形",
            }
        return _finish(case, "MEASURE", ["閘 0:有機器可查的事實能定案 —— 去量,不要開會也不要問人"],
                       escalated, needs_stop_line=False)

    # ── 閘 1:真人保留格 ───────────────────────────────────
    hit_human, unmeasured_human = [], []
    for n in HUMAN_FACTS:
        v, why = _fact(case, n)
        if v is True:
            hit_human.append({"fact": n, "why": why})
        elif v == UNMEASURED:
            unmeasured_human.append(n)
    if hit_human:
        notes.append("閘 1:命中真人保留格 " + ",".join(h["fact"] for h in hit_human))
        return _finish(case, "HUMAN", notes, escalated, human_hits=hit_human, needs_stop_line=False)

    # ── 閘 2:主導席自己定 ─────────────────────────────────
    forcing = [n for n in COUNCIL_FORCING_FACTS if _fact(case, n)[0] is True]
    unmeasured_forcing = [n for n in COUNCIL_FORCING_FACTS if _fact(case, n)[0] == UNMEASURED]
    chair_ok = all(_fact(case, n)[0] is True for n in CHAIR_FACTS)
    unmeasured_chair = [n for n in CHAIR_FACTS if _fact(case, n)[0] == UNMEASURED]

    # ★ 未量一律往上路由,而且要印出來 —— 保守不得是靜默的。
    if unmeasured_gate0:
        escalated += unmeasured_gate0
    if unmeasured_human:
        escalated += unmeasured_human
    if unmeasured_forcing:
        escalated += unmeasured_forcing
    if unmeasured_chair:
        escalated += unmeasured_chair

    if chair_ok and not forcing and not escalated:
        notes.append("閘 2:有分辨器且決錯只影響一包 —— 主導席自己定")
        return _finish(case, "CHAIR", notes, escalated, needs_stop_line=True)

    # ── 閘 3:其餘走合議 ───────────────────────────────────
    if forcing:
        notes.append("閘 3:" + ",".join(forcing) + " 成立 ⇒ 不准走 CHAIR")
    elif not chair_ok:
        notes.append("閘 3:閘 2 兩個條件沒有同時成立")
    if escalated:
        notes.append("★ 有未量欄,已往上路由(保守方向),未量欄:" + ",".join(sorted(set(escalated))))
    return _finish(case, "COUNCIL", notes, escalated, needs_stop_line=True)


def _finish(case, r, notes, escalated, human_hits=None, needs_stop_line=True):
    out = {
        "id": case.get("id"),
        "route": r,
        "why": notes,
        "escalated_due_to_unmeasured": sorted(set(escalated)),
    }
    if human_hits:
        out["human_gate_hits"] = human_hits

    # ── 閘 4:載體與消費者 ─────────────────────────────────
    carrier = str(case.get("carrier") or "").strip()
    consumer = str(case.get("carrier_consumer") or "").strip()
    if _is_nihilistic(carrier) or _is_nihilistic(consumer):
        out["error"] = "載體或消費者是空的/虛無值(填了字而那個字逐字說沒有)"
        out["why_this_is_red"] = ("FIX-20260909-036 的形狀:凍結寫進宣告而全庫沒有東西讀它 —— "
                                  "『寫在檔案裡』比『寫在聊天裡』好,但仍然沒有一格會叫")
        return RC_CARRIER_WITHOUT_CONSUMER, out
    out["carrier"] = carrier
    out["carrier_consumer"] = consumer
    out["carrier_consumer_caveat"] = ("★ 這只是形狀檢查不是證明:填一個看起來像消費者的名字而它其實不讀,"
                                      "本工具看不出來 —— 那一格要靠交件時的實跑")

    # ── HUMAN 的硬要求:選項要有逐字後果 ────────────────────
    if r == "HUMAN":
        opts = case.get("options") or []
        bad = [o for o in opts if not str((o or {}).get("consequence") or "").strip()]
        if len(opts) < 2 or bad:
            out["error"] = "HUMAN 路線但選項沒有逐字後果"
            out["why_this_is_red"] = ("兩個字的問題會得到兩個字的答案:2026-09-09『可讀』被展開成 "
                                      "ai_writable=true;把後果逐字列出再讓他選")
            out["options_missing_consequence"] = [(o or {}).get("label") for o in bad]
            return RC_OPTIONS_WITHOUT_CONSEQUENCE, out
        out["options"] = [{"label": o.get("label"), "consequence": o.get("consequence")} for o in opts]

    # ── CHAIR 的硬要求:分辨器要寫出來 ──────────────────────
    if r == "CHAIR":
        out["discriminator"] = str(case.get("discriminator") or "").strip()
        if _is_nihilistic(out["discriminator"]):
            out["error"] = "CHAIR 路線但沒寫下分辨器"
            out["why_this_is_red"] = "沒有分辨器的『我自己決』與拍腦袋在紀錄上逐字相同"
            return RC_NO_STOP_LINE, out

    # ── 閘 5:停止線 ──────────────────────────────────────
    if needs_stop_line:
        stop = str(case.get("stop_line") or "").strip()
        if _is_nihilistic(stop):
            out["error"] = "沒畫停止線"
            out["why_this_is_red"] = ("常設令拿掉了『等 the maintainer』這個煞車 ⇒ "
                                      "沒有東西會自己叫停;要寫下什麼情況要回頭找真人")
            return RC_NO_STOP_LINE, out
        out["stop_line"] = stop
    return RC_OK, out


def cmd_route(args):
    try:
        case = json.loads(pathlib.Path(args.case_file).read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(T("aib.route.bad_case_file", {"err": e}))
        return RC_USAGE
    rc, out = route(case)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    record_route(out, rc)
    return rc


def cmd_backtest(args):
    """拿今天的真實實例回測:工具的路由要與當時實際走的那條相同。
    ★ 母體不是我發明的,是 2026-09-09 那一天真的發生過的十件。"""
    cases = json.loads(pathlib.Path(args.cases_file).read_text(encoding="utf-8"))
    rows, bad = [], []
    for c in cases:
        expected = c.pop("expected_route", None)
        expected_rc = c.pop("expected_rc", RC_OK)
        rc, out = route(c)
        ok = (out.get("route") == expected) and (rc == expected_rc)
        rows.append({"id": c.get("id"), "expected": expected, "got": out.get("route"),
                     "rc": rc, "expected_rc": expected_rc, "ok": ok})
        if not ok:
            bad.append(c.get("id"))
    print(json.dumps({"cases": len(rows), "mismatched": bad, "rows": rows},
                     ensure_ascii=False, indent=1))
    return RC_OK if not bad else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="遇事不決:路由到 MEASURE / HUMAN / CHAIR / COUNCIL")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route", help="路由一件事")
    r.add_argument("--case-file", required=True)
    r.set_defaults(fn=cmd_route)
    b = sub.add_parser("backtest", help="拿真實實例回測路由")
    b.add_argument("--cases-file", required=True)
    b.set_defaults(fn=cmd_backtest)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
