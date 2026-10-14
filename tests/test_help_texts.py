# SPDX-License-Identifier: Apache-2.0
"""`aib help`, `aib newrole --help` and `aib newproj --help` are usable by a new user.

New-user walkthrough (10-14) item 11: `aib help` said there is no such command; `newrole --help` (all Chinese, even
in English) never said the persona text needs `## 職責` / `## 紅線` / `## 知識域`, so the first try failed with
PERSONA_LINT_FAIL; `newproj --help` showed internal seat codes. Standard library only.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
INTERNAL = re.compile(r"f643|77324|麻瓜|\ba seat[;/]")


class HelpTexts(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-helptexts-"))
        (self.td / "home").mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def aib(self, locale, *args, **extra):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td / "home"), "EZ2_AIB_LOCALE": locale}
        env.update(extra)
        return subprocess.run(AIB + list(args), cwd=self.td, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def test_aib_help_is_aib_dash_dash_help(self):
        a, b = self.aib("en", "help"), self.aib("en", "--help")
        self.assertIn("aib test", a.stdout)
        self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout))

    def test_newrole_help_names_the_three_sections(self):
        # Every heading must sit whole on one line in every locale, at a narrow terminal too: a heading split across
        # two lines (argparse wraps at spaces, and the space inside "## 職責" is the only one in CJK text) gets copied
        # wrong and fails the lint again.
        for loc in ("en", "zh-TW", "ja", "ko"):
            for cols in ("60", "80"):
                with self.subTest(locale=loc, columns=cols):
                    r = self.aib(loc, "newrole", "--help", COLUMNS=cols)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    lines = r.stdout.splitlines()
                    with_marks = [line.strip() for line in lines if "##" in line]
                    # Exactly three lines carry "##", and each starts with one whole heading.
                    self.assertEqual(len(with_marks), 3, r.stdout)
                    for heading, line in zip(("## 職責", "## 紅線", "## 知識域"), with_marks):
                        self.assertTrue(line == heading or line.startswith(heading + " "), (heading, line))
                    if loc == "en":   # English wraps at spaces, so nothing should overflow the terminal width
                        self.assertLessEqual(max(len(line) for line in lines), int(cols), r.stdout)
                    self.assertIsNone(INTERNAL.search(r.stdout), r.stdout)
        en = self.aib("en", "newrole", "--help").stdout
        self.assertIn("Create a persona card", en)
        self.assertNotIn("角色名稱", en)

    def test_newproj_help_has_no_internal_seat_codes(self):
        for loc in ("en", "zh-TW", "ja", "ko"):
            with self.subTest(locale=loc):
                r = self.aib(loc, "newproj", "--help")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIsNone(INTERNAL.search(r.stdout), r.stdout)
        self.assertNotIn("新開專案", self.aib("en", "newproj", "--help").stdout)

    def test_skill_examples_have_no_internal_codes(self):
        for skill in ("aib-newproj", "aib-newrole"):
            text = (ROOT / "aib" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertIsNone(INTERNAL.search(text), skill)


if __name__ == "__main__":
    unittest.main()
