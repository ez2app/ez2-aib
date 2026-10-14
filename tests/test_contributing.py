# SPDX-License-Identifier: Apache-2.0
"""CONTRIBUTING (all four languages) says this repository is the source of truth and how to run the tests.

It used to call this repository a mirror whose PRs get ported to a private repository and closed; since
2026-10-14 the public repository is the only source of truth. Standard library only.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ["CONTRIBUTING.md", "CONTRIBUTING.zh-TW.md", "CONTRIBUTING.ja.md", "CONTRIBUTING.ko.md"]
STALE = re.compile(r"mirror|ported|private repository|鏡像|移植|私有倉|ミラー|移植され|非公開のリポジトリ|미러|비공개 저장소|옮겨", re.I)


class Contributing(unittest.TestCase):
    def test_source_of_truth_and_test_command(self):
        for name in FILES:
            with self.subTest(file=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIsNone(STALE.search(text), STALE.search(text) and STALE.search(text).group(0))
                self.assertIn("python3 -m unittest discover -s tests -v", text)
                self.assertIn("`main`", text)


if __name__ == "__main__":
    unittest.main()
