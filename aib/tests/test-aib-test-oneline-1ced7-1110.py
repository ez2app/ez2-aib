"""aib test 省時摘要預設一行 — 1ced7 11-09 23:1x 台北。

Andy 原話:「這個實在太繞口了,直接用簡單的幾行字,告訴用戶少多少時間就好」;選了「一行版」:
  有省到:⏱ aib 這次幫你省了 1 分 20 秒(3 分 → 1 分 40 秒)
  沒省到:⏱ 這次沒省到時間(2 支都要重跑:讀過的檔改了)
  第一次跑:⏱ 第一次跑,下次開始省時間
細節(🧮 樹狀、💡 建議)只在 aib test --verbose。

O1 short_dur:秒 / 分秒 / 小時分。
O2 oneline 四種情況;省得太少(未過 SAVED_MIN 門檻)只說沒省到,不報小數字。
O3 預設模式印出、記進說明檔(tree / required)的都只有那一行;--verbose 照舊整棵樹。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")
import test_entry as te  # noqa: E402


def vs(without, with_, ran=2, reused=0):
    return {"without_s": without, "with_s": with_, "saved_s": max(0.0, without - with_), "ran": ran,
            "reused": reused, "unknown": 0}


class O1ShortDur(unittest.TestCase):
    def test_units(self):
        self.assertEqual(te.short_dur(4.94), "4.9 秒")
        self.assertEqual(te.short_dur(80), "1 分 20 秒")
        self.assertEqual(te.short_dur(180), "3 分")
        self.assertEqual(te.short_dur(100), "1 分 40 秒")
        self.assertEqual(te.short_dur(7500), "2 小時 5 分")
        self.assertEqual(te.short_dur(7200), "2 小時")
        self.assertEqual(te.short_dur(-3), "0.0 秒")


class O2OneLine(unittest.TestCase):
    def test_saved(self):
        self.assertEqual(te.oneline(vs(180, 100, ran=2, reused=18), [{"without_s": 1}]),
                         ["⏱ aib 這次幫你省了 1 分 20 秒(3 分 → 1 分 40 秒)"])

    def test_nosave_all_reran_names_the_main_cause(self):
        # 截圖那一輪:2 支都實跑,4.9 秒 vs 11.1 秒
        self.assertEqual(te.oneline(vs(4.9, 11.1, ran=2, reused=0), [{"without_s": 1}], ["它讀過的檔內容變了(2)"]),
                         ["⏱ 這次沒省到時間(2 支都要重跑:它讀過的檔內容變了)"])

    def test_nosave_one_test_says_it_without_all(self):
        self.assertEqual(te.oneline(vs(0.4, 8.3, ran=1, reused=0), [{"without_s": 1}], ["第一次跑,沒有上次紀錄(1)"]),
                         ["⏱ 這次沒省到時間(要重跑:第一次跑,沒有上次紀錄)"])

    def test_nosave_partly_reused_has_no_cause(self):
        self.assertEqual(te.oneline(vs(10, 12, ran=1, reused=3), [{"without_s": 1}], ["x(1)"]), ["⏱ 這次沒省到時間"])

    def test_small_saving_is_not_reported_as_a_number(self):
        # 省 10 秒 / 省 10%:都沒過門檻(相加估算的誤差就這麼大)⇒ 不報數字
        self.assertEqual(te.oneline(vs(100, 90, reused=1), [{"without_s": 1}]), ["⏱ 這次沒省到時間"])
        self.assertEqual(te.oneline(vs(1000, 900, reused=1), [{"without_s": 1}]), ["⏱ 這次沒省到時間"])

    def test_first_run(self):
        self.assertEqual(te.oneline(vs(50, 60, reused=0), []), ["⏱ 第一次跑,下次開始省時間"])

    def test_nothing_to_compare(self):
        self.assertEqual(te.oneline(vs(0, 1), []), [])

    def test_every_locale_has_the_keys(self):
        import json
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("one_saved", "one_nosave", "one_nosave_all", "one_nosave_one", "one_first", "one_s", "one_m", "one_ms",
                      "one_h", "one_hm"):
                self.assertIn("aib.test." + k, d, (loc, k))


class O3DefaultPrintsOneLine(unittest.TestCase):
    """main() 的收尾:預設只把一行交給印出與說明檔;--verbose 才是整棵樹。讀原始碼確認分流(main 太大不好整支跑)。"""

    def test_main_routes_by_verbose(self):
        src = (REPO / "tools" / "verify" / "test_entry.py").read_text(encoding="utf-8")
        self.assertIn('ap.add_argument("--verbose", action="store_true")', src)
        block = src[src.index("        if a.verbose:\n            show, need = lines + advice, req"):]
        block = block[:block.index("next_actions=_LAST_ADVICE")]
        self.assertIn("show = oneline(vs, before, causes) if lines else []", block)
        self.assertIn("need, extra = list(show), []", block)
        self.assertIn("tree=show or None, required=need or None", block)


if __name__ == "__main__":
    unittest.main()
