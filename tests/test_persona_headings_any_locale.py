# SPDX-License-Identifier: Apache-2.0
"""`aib newrole` accepts the three required persona headings in any of the four languages.

New-user walkthrough (10-14): an English user had to type `## 職責` / `## 紅線` / `## 知識域`. Now `## Duties`,
`## Red lines`, `## Knowledge` (and the ja / ko words from aib/i18n) work too. The card is still written with the
Chinese headings, because everything that reads cards afterwards (packing keeps red lines first, the seat screen,
bindme, persona checks …) looks for those. Standard library only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
EN = "Reviews code.\n\n## Duties\nReview diffs.\n\n## Red lines:\nNever approve untested code.\n\n## Knowledge\nPython.\n"
JA = "コードを見ます。\n\n## 役割\n差分を見る。\n\n## 禁止事項\n未テストは承認しない。\n\n## 知識範囲\nPython。\n"
KO = "코드를 봅니다.\n\n## 역할\n차이를 봅니다.\n\n## 금지선\n테스트 안 된 코드는 승인하지 않음.\n\n## 지식 범위\nPython.\n"


class PersonaHeadingsAnyLocale(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-headings-"))
        (self.td / "home").mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def newrole(self, name, prompt, *extra):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td / "home"), "EZ2_AIB_LOCALE": "en"}
        return subprocess.run(AIB + ["newrole", "--name", name, "--prompt", prompt] + list(extra), cwd=self.td,
                              env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)

    def test_english_headings_written_as_the_canonical_ones(self):
        r = self.newrole("Reviewer", EN)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        card = next((self.td / "home" / ".ez2-aib" / "vault").rglob("角色.md")).read_text(encoding="utf-8")
        for heading in ("## 職責", "## 紅線", "## 知識域"):
            self.assertIn(heading + "\n", card)
        self.assertNotIn("## Duties", card)
        self.assertIn("Never approve untested code.", card)

    def test_japanese_and_korean_headings(self):
        for name, prompt in (("JA", JA), ("KO", KO)):
            with self.subTest(name=name):
                r = self.newrole(name, prompt, "--dry-run")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_section_still_refused(self):
        r = self.newrole("Partial", "x\n\n## Duties\na\n\n## Knowledge\nc\n", "--dry-run")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("PERSONA_LINT_FAIL", r.stdout + r.stderr)

    def test_only_whole_level_two_heading_lines_change(self):
        sys.path.insert(0, str(ROOT / "aib" / "src" / "identity"))
        import create_and_bind_persona as cbp
        text = "## Duties of care\nx\n### Duties\ny\nDuties\n## Duties\nz"
        self.assertEqual(cbp.normalize_headings(text), "## Duties of care\nx\n### Duties\ny\nDuties\n## 職責\nz")


if __name__ == "__main__":
    unittest.main()
