#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話端到端(第二層測試):chat_e2e_world.py up ⇒ check ⇒ down。

本機假中繼站 + 假證明站 + 五位假主人(好友 Bob、陌生 Cara、申請 Dan、封鎖 Eve),Andy-demo 這一端看到的
通訊錄、世界、申請、拒收、佇列、寄件端 rc 一次驗完(17 項)。要 AIB_TEST_NOSTR_VENV(aib test 會清 AIB_* ⇒ 全套裡跳過)。
刀(複本):K1 好友判斷永遠不中 ⇒ Bob 的信被拒收、名片 / 在線狀態被丟 ⇒ check 紅。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

VENV = os.environ.get("AIB_TEST_NOSTR_VENV")
TOOL = HERE / "chat_e2e_world.py"


@unittest.skipUnless(VENV, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class World(unittest.TestCase):
    def test_e1_up_check_down(self):
        d = tempfile.mkdtemp(prefix="aib-e2e-")
        try:
            r = subprocess.run([sys.executable, str(TOOL), "up", "--dir", d, "--venv", VENV], capture_output=True, text=True,
                               timeout=400)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([sys.executable, str(TOOL), "check", "--dir", d], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("全部通過", r.stdout)
        finally:
            subprocess.run([sys.executable, str(TOOL), "down", "--dir", d, "--rm"], capture_output=True, timeout=60)


@unittest.skipUnless(VENV, "沒有 AIB_TEST_NOSTR_VENV")
class Knife(unittest.TestCase):
    def test_k1_presence_from_anyone(self):
        """好友判斷永遠不中 ⇒ 整個端到端要紅(Bob 被當陌生人)。"""
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_roster.py",
                        '    return bool(owner) and any(f["owner"] == owner for f in read_friends(env, kind))',
                        "    return False", tests=(name, "chat_e2e_world.py", "aib_fake_relay.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "World.test_e1_up_check_down"],
                            cwd=str(root), max_procs=200, timeout=600)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
