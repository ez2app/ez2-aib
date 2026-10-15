#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席預設(the maintainer 10-15 兩則):「之後只要建席位沒有特別說的情況下預設在同一個 aib 艙」
「預設新開新從原有的角色上選,不另開新角色」。

R1 aib <引擎> <模式> <沒定義的角色> ⇒ rc 2,用你的語言列出已定義角色;dev2(同家族)放行。
R2 roles.py:已定義 = 四語字典有 aib.role.* 名稱的;AIB_ALLOW_CUSTOM_ROLE=1 / EZ2_AIB_CAPTAIN_ROLES 例外。
R3 newrole / newproj 的 --new-seat:角色 = --role(要定義過),沒給 = dev;不再拿人格代號或專案標題當角色。
C1 人在艙的窗格裡、沒寫開在哪一艙 ⇒ 開在本艙並印一行說明(原本是擋下)。
刀:拿掉 aib 入口的 roles.py 檢查 ⇒ R1 紅;拿掉本艙預設 ⇒ C1 紅。
沙盒 HOME、--dry-run,不碰真艙。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
AIB = ROOT / "src" / "cockpit" / "aib"
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src" / "identity"))
import roles as R  # noqa: E402

BASE_ROLES = {"root", "pm", "dev", "design", "qa", "monitor", "advisor"}


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name) / "home"
        self.home.mkdir()

    def aib(self, *args, **env):
        e = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "LANG": "en_US.UTF-8",
             "EZ2_AIB_LOCALE": "zh-TW"}
        e.update(env)
        return subprocess.run(["bash", str(AIB), *args], capture_output=True, text=True, timeout=120,
                              env=e, stdin=subprocess.DEVNULL)


class Roles(Base):
    def test_R1_undefined_role_refused_with_list_family_allowed(self):
        p = self.aib("claude", "safe", "zzzrole", "--dry-run")
        self.assertEqual(p.returncode, 2, p.stderr[-400:])
        self.assertIn("角色「zzzrole」沒有定義", p.stderr)
        self.assertIn("dev(工程師)", p.stderr, "要列出可用角色與名稱")
        p = self.aib("claude", "safe", "dev2", "--dry-run")
        self.assertNotIn("沒有定義", p.stderr, "dev2 是 dev 家族")

    def test_R2_roles_single_source_and_escape_hatches(self):
        self.assertTrue(BASE_ROLES <= set(R.defined_roles()), R.defined_roles())
        self.assertTrue(R.is_defined("dev", env={}))
        self.assertTrue(R.is_defined("root2", env={}))
        self.assertFalse(R.is_defined("zzz-custom", env={}))
        self.assertTrue(R.is_defined("zzz-custom", env={"AIB_ALLOW_CUSTOM_ROLE": "1"}))
        self.assertTrue(R.is_defined("chief", env={"EZ2_AIB_CAPTAIN_ROLES": "root chief"}))

    def test_R3_new_seat_role_defaults_to_dev_and_must_be_defined(self):
        import create_and_bind_persona as CBP
        self.assertEqual(CBP.new_seat_role_or_die(""), ("dev", ""))
        self.assertEqual(CBP.new_seat_role_or_die("qa"), ("qa", ""))
        role, err = CBP.new_seat_role_or_die("globaldm")
        self.assertTrue(err and "globaldm" in err, "人格代號不能當角色")
        src = (ROOT / "tools" / "project" / "new_project.py").read_text(encoding="utf-8")
        self.assertIn("CBP.new_seat_role_or_die(", src, "newproj --new-seat 要走同一個判準")
        self.assertNotIn('CBP.seat_role_of(args.title, "project")', src, "不再拿專案標題當角色")


class Cabin(Base):
    def test_C1_in_cabin_pane_defaults_to_this_cabin(self):
        inst = "20990101-000000-abcd"
        (self.home / ".ez2-aib" / "instances" / inst).mkdir(parents=True)
        p = self.aib("claude", "safe", "dev", "--dry-run", TMUX="/private/tmp/tmux-501/aihub-%s,99999,0" % inst)
        self.assertIn("開在本艙 %s" % inst, p.stderr, p.stderr[-600:])
        self.assertIn("hub=aihub-%s" % inst, p.stderr)
        self.assertNotIn("先擋下", p.stderr)


if __name__ == "__main__":
    unittest.main()
