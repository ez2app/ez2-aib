#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""交接檢查 enabled_at 不准比真的時刻晚(10-05)。

推前檢查裡 test-handoff-flow-abb89-1015 的 E1 偶發紅:「啟用之後開始的一輪沒收據 ⇒ 照樣抓」沒抓到。
根因:enabled_at 存成 "%.3f"(四捨五入到毫秒)。進位時存下的比真的晚,緊接著記下的那一輪開始點
(同一毫秒內)就「早於啟用」而被跳過 —— 漏報一次交接缺漏。單獨跑大多綠,看那一下落在毫秒的哪半邊。

F1 時鐘停在 …0006 秒(四捨五入會進位的那半邊):存下與回傳的 enabled_at 都不晚於那個時刻
F2 同一時刻開始的一輪不算「啟用之前」:prev_start == 那一刻 ⇒ check_previous 照樣報缺漏
刀(複本):K1 改回四捨五入 ⇒ F1 紅
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tests"))
import aib_handoff as H  # noqa: E402

T0 = 1790000000.0006
ENV = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}


class Floor(unittest.TestCase):
    def test_f1_enabled_at_is_never_later_than_now(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(H.time, "time", return_value=T0):
            got = H.enabled_at(td)
            stored = float((H.handoff_dir(td) / "enabled_at").read_text(encoding="utf-8"))
        self.assertLessEqual(got, T0)
        self.assertLessEqual(stored, T0)
        self.assertGreater(stored, T0 - 0.002, "只往下取到毫秒,不是隨便一個更早的時間")

    def test_f2_round_started_at_that_moment_is_not_before_enabling(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(H.time, "time", return_value=T0):
            H.enabled_at(td)
            self.assertFalse(T0 < H.enabled_at(td), "那一刻開始的一輪 ⇒ 不得被當成啟用之前")


class Knives(unittest.TestCase):
    def test_k1_rounding_reds_f1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/aib_handoff.py", "    now = math.floor(time.time() * 1000) / 1000\n",
                        "    now = time.time()\n", tests=(name,)) as root:
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Floor.test_f1_enabled_at_is_never_later_than_now"],
                               cwd=str(root), env=dict(ENV, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=600)
        red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
        self.assertIn("test_f1_enabled_at_is_never_later_than_now", red, p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
