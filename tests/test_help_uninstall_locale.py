# SPDX-License-Identifier: Apache-2.0
"""Help and uninstall speak the user's language; install.sh documents the same URL as the README.

New-user walkthrough (10-14) item 12: `aib --help-cockpit` printed all four languages, preceded by a seat-directory
notice nobody asked for; `aib uninstall` was English-only under zh-TW; install.sh's header and the README gave
different install URLs. Standard library only: python -m unittest discover -s tests -v
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
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


class HelpUninstallLocale(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="aib-help-"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def aib(self, locale, *args):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "EZ2_AIB_LOCALE": locale}
        # cwd = HOME: the case that used to print the seat-directory notice before the help text.
        return subprocess.run(AIB + list(args), cwd=self.home, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def test_help_cockpit_one_language_no_cwd_notice(self):
        r = self.aib("en", "--help-cockpit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Usage:", r.stdout)
        self.assertIsNone(CJK.search(r.stdout + r.stderr), r.stdout + r.stderr)
        r = self.aib("zh-TW", "--help-cockpit")
        self.assertIn("用法", r.stdout)
        self.assertNotIn("Usage:", r.stdout)
        self.assertNotIn("席位目錄", r.stdout + r.stderr)

    def test_uninstall_lists_in_locale(self):
        r = self.aib("zh-TW", "uninstall")  # no --yes: lists only, removes nothing
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("什麼都還沒刪", r.stdout)
        self.assertNotIn("nothing was removed", r.stdout)
        r = self.aib("en", "uninstall")
        self.assertIn("nothing was removed; run `aib uninstall --yes` to do it", r.stdout)
        self.assertTrue((ROOT / "bin" / "aib").exists())

    def test_install_header_url_matches_readme(self):
        readme = re.findall(r"curl -fsSL (\S+/install\.sh) \| bash", (ROOT / "README.md").read_text(encoding="utf-8"))
        header = re.findall(r"curl -fsSL (\S+/install\.sh) \| bash", "".join(
            (ROOT / "install.sh").read_text(encoding="utf-8").splitlines(True)[:10]))
        self.assertTrue(readme and header)
        self.assertEqual(set(header), set(readme))


if __name__ == "__main__":
    unittest.main()
