#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開 agy / grok 席不再卡在信任畫面(10-24 @abb89,右鍵另開 AI 實測;Andy「卡選項的新開席都要能自動選成功」)。

事故:右鍵 → 另開 AI → agy → dev1,席停在「Do you trust the contents of this project?」,開席單打不進去;
menu-pick 也沒接手 —— 它的初篩只認「1. 2.」這種有編號的選項,agy 的是游標選單。
另外 agy:dgrs 落在 plain 啟動,窗名寫 dgrs、實際每個工具都要人按。
grok 1.0.67 也加了資料夾信任(~/.grok/trusted_folders.toml,問的是 git 根目錄),同一天第五家踩到。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))

import folder_trust  # noqa: E402
try:
    import tomllib  # noqa: E402
except ImportError:          # Mac 系統 Python 3.9:grok 那條在正式程式也是跳過
    tomllib = None
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
        self.assertEqual(folder_trust.ensure("agy", self.proj, environ=self.env)[0], "added")
        d = json.loads(self.cfg.read_text())
        self.assertEqual(d["trustedWorkspaces"], ["/a", str(self.proj.resolve())])
        self.assertEqual(d["model"], "m")
        self.assertEqual(d["permissions"], {"allow": ["x"]})
        self.assertEqual(folder_trust.ensure("agy", self.proj, environ=self.env)[0], "already")
        self.assertEqual(len(json.loads(self.cfg.read_text())["trustedWorkspaces"]), 2)

    def test_missing_settings_file_is_created_in_existing_home(self):
        self.assertEqual(folder_trust.ensure("agy", self.proj, environ=self.env)[0], "added")
        self.assertEqual(json.loads(self.cfg.read_text()), {"trustedWorkspaces": [str(self.proj.resolve())]})

    def test_never_clobbers_unreadable_settings(self):
        for bad in ("{not json", "[1, 2]", json.dumps({"trustedWorkspaces": "oops"})):
            self.cfg.write_text(bad)
            self.assertEqual(folder_trust.ensure("agy", self.proj, environ=self.env)[0], "skip", bad)
            self.assertEqual(self.cfg.read_text(), bad)

    def test_no_agy_home_and_off_switch(self):
        env = {"GEMINI_CLI_HOME": str(self.tmp / "nothing")}
        self.assertEqual(folder_trust.ensure("agy", self.proj, environ=env)[0], "skip")
        self.assertFalse((self.tmp / "nothing").exists())
        self.assertEqual(folder_trust.ensure("agy", self.proj, environ=dict(self.env, AIB_FOLDER_TRUST="0"))[0], "off")
        self.assertFalse(self.cfg.exists())


@unittest.skipIf(tomllib is None, "沒有 tomllib(Python < 3.11)")
class GrokTrust(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "grok"
        self.home.mkdir()
        self.cfg = self.home / "trusted_folders.toml"
        self.repo = self.tmp / "repo"
        (self.repo / "sub").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, timeout=30)
        self.env = {"GROK_HOME": str(self.home)}

    def tearDown(self):
        self._tmp.cleanup()

    def test_trusts_git_root_and_keeps_existing_entries(self):
        self.cfg.write_text('[folders."/a"]\ntrusted = true\ndecided_at = 1\n')
        r = folder_trust.ensure("grok", self.repo / "sub", environ=self.env)
        self.assertEqual(r, ("added", str(self.repo.resolve())))
        d = tomllib.loads(self.cfg.read_text())["folders"]
        self.assertIs(d["/a"]["trusted"], True)
        self.assertIs(d[str(self.repo.resolve())]["trusted"], True)
        self.assertEqual(folder_trust.ensure("grok", self.repo, environ=self.env)[0], "already")

    def test_human_declined_is_respected(self):
        root = str(self.repo.resolve())
        body = '[folders."%s"]\ntrusted = false\ndecided_at = 1\n' % root
        self.cfg.write_text(body)
        self.assertEqual(folder_trust.ensure("grok", self.repo, environ=self.env), ("skip", "human_declined"))
        self.assertEqual(self.cfg.read_text(), body)

    def test_unreadable_toml_untouched_and_no_home(self):
        self.cfg.write_text("[folders\n")
        self.assertEqual(folder_trust.ensure("grok", self.repo, environ=self.env)[0], "skip")
        self.assertEqual(self.cfg.read_text(), "[folders\n")
        env = {"GROK_HOME": str(self.tmp / "nothing")}
        self.assertEqual(folder_trust.ensure("grok", self.repo, environ=env)[0], "skip")
        self.assertFalse((self.tmp / "nothing").exists())

    def test_non_git_folder_uses_itself(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(folder_trust.ensure("grok", plain, environ=self.env), ("added", str(plain.resolve())))


class LaunchWiring(unittest.TestCase):
    def test_agy_dgrs_skips_permissions_and_trusts_before_launch(self):
        src = (COCKPIT / "provider-launch.sh").read_text(encoding="utf-8")
        self.assertIn("agy:dgrs)    ARGS=(--dangerously-skip-permissions)", src)
        self.assertIn("copilot:dgrs) ARGS=(--allow-all)", src)
        self.assertIn('folder_trust.py" ensure "$PROVIDER" "$PROJECT_DIR" </dev/null >/dev/null || true', src)
        self.assertIn('[ "$PROVIDER" = "grok" ]; } && [ -f "$HERE/folder_trust.py" ]', src)
        self.assertLess(src.index('folder_trust.py" ensure'), src.index('cd "$PROJECT_DIR" || exit 2'))


class CursorMenuSuspect(unittest.TestCase):
    def test_agy_trust_screen_is_a_menu(self):
        self.assertTrue(menu_pick.suspect(AGY_TRUST_SCREEN, "agy"))

    def test_idle_prompts_are_not_menus(self):
        for provider, screen in IDLE_SCREENS.items():
            self.assertFalse(menu_pick.suspect(screen, provider), provider)

    def test_ai_talking_about_menus_is_not_a_menu(self):
        # 審查第 1 位實測的三個誤判:回顯使用者那句話(❯ › >)+ AI 回覆裡提到選單操作
        screens = {
            "codex": "› agy 卡在信任畫面了嗎\n• 對,它底部是\n↑/↓ Navigate · enter Confirm\n我會改 suspect。\n"
                     "› Ask Codex to do anything\n  ? for shortcuts\n",
            "claude": "❯ 為什麼 agy 卡住\n⏺ 它在等你按\n↑/↓ Navigate 再 enter Confirm\n─────\n❯ \n─────\n"
                      "  ⏵⏵ bypass permissions on\n",
            "agy": "> 修好了嗎?\n• 已修好\n用 ↑/↓ 選\n> \nGemini 3.8 Flash\n",
        }
        for provider, screen in screens.items():
            self.assertFalse(menu_pick.suspect(screen, provider), provider)

    def test_cursor_in_middle_still_a_menu(self):
        screen = ("Pick a theme?\n  Light\n> Dark\n  System\n  ↑/↓ to move · enter to select\n")
        self.assertTrue(menu_pick.suspect(screen, "agy"))

    def test_nav_hint_far_above_bottom_is_not_a_menu(self):
        screen = AGY_TRUST_SCREEN + "\n".join("回報第 %d 行" % i for i in range(12)) + "\n> \n"
        self.assertFalse(menu_pick.suspect(screen, "agy"))


class BridgeSystemNotice(unittest.TestCase):
    def test_system_notice_is_system_card_not_unverified_seat(self):
        import sand_bridge
        kind, label = sand_bridge._dm_card("IP:100.1.1.1/6ddf/系統@aib >> IP:100.1.1.1/6ddf/未綁@6d76f "
                                           "10-24 04:24(台北時間) 長訊已落檔")
        self.assertEqual((kind, label), ("system", "AIB 系統"))
        kind, _ = sand_bridge._dm_card("IP:100.1.1.1/6ddf/dev@abb89 >> IP:100.1.1.1/6ddf/dev1@6d76f "
                                       "10-24 04:24(台北時間) hi")
        self.assertEqual(kind, "dm")


if __name__ == "__main__":
    unittest.main()
