#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""輸入框判定:游標蓋住空框提示字的第一個字(10-24 跨客戶驗收，新 macOS 帳號第一次開的 claude 席)。

畫面實錄(capture-pane -e):`❯ ESC[7mT ESC[0;2mry ESC[0m ESC[2m"write ...`
舊判準:第一個字 T 在反白裡、不是 dim ⇒ DRAFT ⇒ 全球來信 / 開席單排隊不投，要會員先打一個字。
C1 實錄那一行 ⇒ SUGGESTION(不擋投遞)
C2 游標在第一個字、後面是真字(不是 dim)⇒ DRAFT(真稿照擋)
C3 游標字 + 只有 1 個 dim 字 ⇒ DRAFT(太短不放)
C4 沒有游標、第一個字不是 dim ⇒ DRAFT;整句 dim ⇒ SUGGESTION(舊行為不變)
C5 游標在中間的字(不是第一個)⇒ DRAFT
刀:K1 拿掉「第一個字在反白裡先不算」⇒ C1 紅
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import composer_state as CS  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

E = "\x1b"
REAL = (E + "[39m❯\xa0" + E + "[7mT" + E + "[0;2mry" + E + "[0m " + E + "[2m\"write" + E + "[0m " + E + "[2ma" + E + "[0m "
        + E + "[2mtest" + E + "[0m " + E + "[2mfor" + E + "[0m " + E + "[2m<filepath>\"" + E + "[0m")


class Cursor(unittest.TestCase):
    def test_c1_real_capture_is_suggestion(self):
        self.assertEqual(CS.classify_composer(REAL, provider="claude"), CS.SUGGESTION)

    def test_c2_cursor_on_real_draft(self):
        line = E + "[39m❯\xa0" + E + "[7mH" + E + "[0mello world"
        self.assertEqual(CS.classify_composer(line, provider="claude"), CS.DRAFT)

    def test_c3_too_short(self):
        line = E + "[39m❯\xa0" + E + "[7mT" + E + "[0;2mr" + E + "[0m"
        self.assertEqual(CS.classify_composer(line, provider="claude"), CS.DRAFT)

    def test_c4_old_behaviour(self):
        self.assertEqual(CS.classify_composer(E + "[39m❯\xa0ZZ", provider="claude"), CS.DRAFT)
        self.assertEqual(CS.classify_composer(E + "[39m❯\xa0" + E + "[2mTry this" + E + "[0m", provider="claude"),
                         CS.SUGGESTION)

    def test_c5_cursor_not_first(self):
        line = E + "[39m❯\xa0" + E + "[2mTr" + E + "[0;7my" + E + "[0;2m again" + E + "[0m"
        self.assertEqual(CS.classify_composer(line, provider="claude"), CS.DRAFT)


class Knife(unittest.TestCase):
    def test_k1_cursor_char_counts(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/composer_state.py", "                if seen == 1 and rev:\n",
                        "                if False:\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Cursor.test_c1_real_capture_is_suggestion"],
                            cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
