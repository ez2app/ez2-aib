# SPDX-License-Identifier: Apache-2.0
"""Cockpit install scripts report a missing install guard in the user's language (and still refuse with rc 6).

Standard library only: python -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ["mouse/mouse-ctl.sh", "nav/ai-nav-ctl.sh", "help/help-overlay.sh", "title/title-beat.sh",
           "nav/view-nav-ctl.sh", "nav/region-nav-ctl.sh", "keys/install-keys.sh", "nav/border-hints-ctl.sh",
           "nav/captains-ctl.sh"]


class InstallGuardI18n(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = Path(tempfile.mkdtemp(prefix="aib-guard-"))
        shutil.copytree(ROOT / "aib", cls.td / "aib", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        (cls.td / "aib" / "src" / "cockpit" / "install-guard.sh").unlink()
        (cls.td / "home").mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def install(self, script, locale):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td / "home"), "EZ2_AIB_LOCALE": locale,
               "EZ2_AIHUB_SOCKET": "test-hub", "EZ2_AIB_INSTANCE_HOME": str(self.td / "home")}
        return subprocess.run(["bash", str(self.td / "aib" / "src" / "cockpit" / script), "--install"], env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    def test_guard_missing_refused_in_locale(self):
        for script in SCRIPTS:
            name = script.split("/")[1]
            with self.subTest(script=script):
                r = self.install(script, "en")
                self.assertEqual(r.returncode, 6, r.stderr)
                self.assertIn("%s: install guard file is missing" % name, r.stderr)
                self.assertNotIn("缺陷十九", r.stderr)
                r = self.install(script, "zh-TW")
                self.assertEqual(r.returncode, 6, r.stderr)
                self.assertIn("%s: 缺陷十九守衛檔缺席" % name, r.stderr)


if __name__ == "__main__":
    unittest.main()
