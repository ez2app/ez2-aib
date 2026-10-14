# SPDX-License-Identifier: Apache-2.0
"""The cockpit's `menu` window (menu auto-pick, role sys.menu) says what it is when it starts.

6ddf walkthrough K11 (10-14): every cabin opens an extra window named `menu`; it was blank, so users could not tell
what it was or whether it should be there. Runs the real watcher in a temp HOME and stops it after the first lines.
Standard library only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / "aib" / "src" / "cockpit" / "menu_pick.py"


class MenuWindowExplains(unittest.TestCase):
    def watch_banner(self, locale):
        td = Path(tempfile.mkdtemp(prefix="aib-menu-"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(td), "EZ2_AIB_LOCALE": locale,
               "EZ2_AIB_INSTANCE_HOME": str(td / ".ez2-aib" / "instances" / "x"), "EZ2_AIB_STATE_ROOT": str(td / ".ez2-aib"),
               "EZ2_AIHUB_SOCKET": "aib-test-none"}
        p = subprocess.Popen([sys.executable, str(MENU), "watch", "--interval", "3"], cwd=td, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
        try:
            deadline, out = time.time() + 20, ""
            while time.time() < deadline and "AIB_MENU_PICK=0" not in out:
                line = p.stdout.readline()
                if not line:
                    break
                out += line
            return out
        finally:
            p.kill()
            p.wait(timeout=10)
            p.stdout.close()
            shutil.rmtree(td, ignore_errors=True)

    def test_banner_in_english(self):
        out = self.watch_banner("en")
        self.assertIn("menu auto-pick", out)
        self.assertIn("Every 3 s", out)
        self.assertIn("aib menu-pick log", out)
        self.assertIn("AIB_MENU_PICK=0", out)

    def test_banner_follows_locale(self):
        self.assertIn("選單自動選擇", self.watch_banner("zh-TW"))


if __name__ == "__main__":
    unittest.main()
