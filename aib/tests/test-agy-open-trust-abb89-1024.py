#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開 agy 席不再卡在信任畫面(10-24 @abb89,右鍵另開 AI 實測;Andy「卡選項的新開席都要能自動選成功」)。

事故:右鍵 → 另開 AI → agy → dev1,席停在「Do you trust the contents of this project?」,開席單打不進去;
menu-pick 也沒接手 —— 它的初篩只認「1. 2.」這種有編號的選項,agy 的是游標選單。
另外 agy:dgrs 落在 plain 啟動,窗名寫 dgrs、實際每個工具都要人按。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))

import agy_trust  # noqa: E402
import menu_pick  # noqa: E402

AGY_TRUST_SCREEN = """Accessing workspace:
/Users/someone/project
Do you trust the contents of this project?
Antigravity CLI requires permission to read, edit, and execute files here.
> Yes, I trust this folder
  No, exit
  ↑/↓ Navigate · enter Confirm
Gemini 3.8 Flash (Medium) | 💧 Ez2 CTX 1% [💧─────────] 14k/1M
"""

IDLE_SCREENS = {
    "codex": "• OK-codex\n  Worked for 1s • 3:48 AM\n› Ask Codex to do anything\n  ? for shortcuts        95% context left\n",
    "claude": "⏺ OK-claude\n✻ Cooked for 1s\n─────\n❯ \n─────\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n",
    "agy": "• 已就緒\n> \n  Gemini 3.8 Flash (Medium) | 💧 Ez2 CTX 1%\n",
}


class AgyTrust(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "gemini"
        (self.home / "antigravity-cli").mkdir(parents=True)
        self.cfg = self.home / "antigravity-cli" / "settings.json"
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.env = {"GEMINI_CLI_HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.tmp)}

    def tearDown(self):
        self._tmp.cleanup()

    def test_adds_folder_and_keeps_other_settings(self):
        self.cfg.write_text(json.dumps({"model": "m", "trustedWorkspaces": ["/a"], "permissions": {"allow": ["x"]}}))
        self.assertEqual(agy_trust.ensure(self.proj, environ=self.env)[0], "added")
        d = json.loads(self.cfg.read_text())
        self.assertEqual(d["trustedWorkspaces"], ["/a", str(self.proj.resolve())])
        self.assertEqual(d["model"], "m")
        self.assertEqual(d["permissions"], {"allow": ["x"]})
        self.assertEqual(agy_trust.ensure(self.proj, environ=self.env)[0], "already")
        self.assertEqual(len(json.loads(self.cfg.read_text())["trustedWorkspaces"]), 2)

    def test_missing_settings_file_is_created_in_existing_home(self):
        self.assertEqual(agy_trust.ensure(self.proj, environ=self.env)[0], "added")
        self.assertEqual(json.loads(self.cfg.read_text()), {"trustedWorkspaces": [str(self.proj.resolve())]})

    def test_never_clobbers_unreadable_settings(self):
        for bad in ("{not json", "[1, 2]", json.dumps({"trustedWorkspaces": "oops"})):
            self.cfg.write_text(bad)
            self.assertEqual(agy_trust.ensure(self.proj, environ=self.env)[0], "skip", bad)
            self.assertEqual(self.cfg.read_text(), bad)

    def test_no_agy_home_and_off_switch(self):
        env = {"GEMINI_CLI_HOME": str(self.tmp / "nothing")}
        self.assertEqual(agy_trust.ensure(self.proj, environ=env)[0], "skip")
        self.assertFalse((self.tmp / "nothing").exists())
        self.assertEqual(agy_trust.ensure(self.proj, environ=dict(self.env, AIB_AGY_TRUST="0"))[0], "off")
        self.assertFalse(self.cfg.exists())


class LaunchWiring(unittest.TestCase):
    def test_agy_dgrs_skips_permissions_and_trusts_before_launch(self):
        src = (COCKPIT / "provider-launch.sh").read_text(encoding="utf-8")
        self.assertIn("agy:dgrs)    ARGS=(--dangerously-skip-permissions)", src)
        self.assertIn("copilot:dgrs) ARGS=(--allow-all)", src)
        self.assertIn('agy_trust.py" ensure "$PROJECT_DIR"', src)
        self.assertLess(src.index('agy_trust.py" ensure'), src.index('cd "$PROJECT_DIR" || exit 2'))


class CursorMenuSuspect(unittest.TestCase):
    def test_agy_trust_screen_is_a_menu(self):
        self.assertTrue(menu_pick.suspect(AGY_TRUST_SCREEN, "agy"))

    def test_idle_prompts_are_not_menus(self):
        for provider, screen in IDLE_SCREENS.items():
            self.assertFalse(menu_pick.suspect(screen, provider), provider)

    def test_nav_hint_far_above_bottom_is_not_a_menu(self):
        screen = AGY_TRUST_SCREEN + "\n".join("回報第 %d 行" % i for i in range(12)) + "\n> \n"
        self.assertFalse(menu_pick.suspect(screen, "agy"))


if __name__ == "__main__":
    unittest.main()
