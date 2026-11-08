#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""knife_copy 的複本要帶 tests/ 底下的資料夾(043dd 11-08,a63ab 大綱第 2 版 P2-7)。

以前複本的 tests/ 只抄 *.py:讀 tests/fixtures/ 的測試(#519 的舊版 fixture)在複本裡找不到檔 ⇒ 刀「咬到」的其實是缺檔。
全在暫存目錄做一棵小樹,不碰真樹。

C1 tests/fixtures/ 整個帶進複本(含子資料夾),內容一樣
C2 呼叫端沒指定的 test-*.py 照舊不帶;輔助檔(非 test-*)照舊帶
C3 tests/__pycache__ 不帶
C4 錨點不是恰好一次 ⇒ AssertionError,不落刀(照舊)
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402


class C(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        r = self.root = Path(self.td.name) / "aib"
        (r / "src").mkdir(parents=True)
        (r / "src" / "a.py").write_text("X = 1\n", encoding="utf-8")
        t = r / "tests"
        (t / "fixtures" / "old-ref").mkdir(parents=True)
        (t / "fixtures" / "old-ref" / "a.py@abc.txt").write_bytes(b"X = 0\r\n")
        (t / "fixtures" / "top.json").write_text("{}", encoding="utf-8")
        (t / "__pycache__").mkdir()
        (t / "__pycache__" / "junk.pyc").write_bytes(b"x")
        (t / "helper.py").write_text("", encoding="utf-8")
        (t / "test-mine.py").write_text("", encoding="utf-8")
        (t / "test-other.py").write_text("", encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def test_c1_fixtures_come_along(self):
        with knife_copy(self.root, "src/a.py", "X = 1", "X = 2", tests=("test-mine.py",)) as cp:
            got = cp / "tests" / "fixtures" / "old-ref" / "a.py@abc.txt"
            self.assertEqual(got.read_bytes(), b"X = 0\r\n")
            self.assertTrue((cp / "tests" / "fixtures" / "top.json").is_file())
            self.assertEqual((cp / "src" / "a.py").read_text(encoding="utf-8"), "X = 2\n")
        self.assertEqual((self.root / "src" / "a.py").read_text(encoding="utf-8"), "X = 1\n")

    def test_c2_only_named_tests_and_helpers(self):
        with knife_copy(self.root, "src/a.py", "X = 1", "X = 2", tests=("test-mine.py",)) as cp:
            names = sorted(p.name for p in (cp / "tests").iterdir())
        self.assertEqual(names, ["fixtures", "helper.py", "test-mine.py"])

    def test_c3_no_pycache(self):
        with knife_copy(self.root, "src/a.py", "X = 1", "X = 2") as cp:
            self.assertFalse((cp / "tests" / "__pycache__").exists())

    def test_c4_anchor_not_once_refuses(self):
        with self.assertRaises(AssertionError):
            with knife_copy(self.root, "src/a.py", "X = 9", "X = 2"):
                pass


if __name__ == "__main__":
    unittest.main()
