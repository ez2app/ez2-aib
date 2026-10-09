# SPDX-License-Identifier: Apache-2.0
"""Smoke test for aib: run, reuse, re-run on change, and never reuse a red result.

Standard library only: python -m unittest discover -s tests -v
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


class Smoke(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-smoke-"))
        self.proj = self.td / "proj"
        (self.proj / "tests").mkdir(parents=True)
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.proj / "tests" / "test_calc.py").write_text(
            "import sys, unittest\nsys.path.insert(0, '.')\nimport calc\n\n"
            "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        (self.proj / "tests" / "test_red.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_red(self):\n        self.fail('always red')\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_", "AIB_")) and k not in ("PYTHONPATH", "BASH_ENV")}
        self.env.update({"HOME": str(self.td / "home"), "XDG_CACHE_HOME": str(self.td / "cache"), "EZ2_AIB_LOCALE": "en",
                         "LANG": "en_US.UTF-8", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
        (self.td / "home").mkdir()
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git"] + a, cwd=self.proj, env=self.env, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _aib_test(self, *tests):
        p = subprocess.run(AIB + ["test"] + list(tests), cwd=self.proj, env=self.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=600)
        m = re.search(r"reused (\d+) / ran (\d+)", p.stdout + p.stderr)
        self.assertIsNotNone(m, (p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
        return p.returncode, int(m.group(1)), int(m.group(2))

    def test_run_reuse_rerun(self):
        self.assertEqual(self._aib_test("tests/test_calc.py"), (0, 0, 1), "first run: runs")
        self.assertEqual(self._aib_test("tests/test_calc.py"), (0, 1, 0), "nothing changed: reused")
        with open(self.proj / "calc.py", "a", encoding="utf-8") as fh:
            fh.write("# changed\n")
        self.assertEqual(self._aib_test("tests/test_calc.py"), (0, 0, 1), "a file it read changed: runs again")

    def test_red_is_never_reused(self):
        for _ in range(2):
            rc, reused, ran = self._aib_test("tests/test_red.py")
            self.assertNotEqual(rc, 0)
            self.assertEqual((reused, ran), (0, 1))

    def test_cli_usage(self):
        p = subprocess.run(AIB, capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("aib test", p.stdout)


if __name__ == "__main__":
    unittest.main()
