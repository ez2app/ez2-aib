#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib dm 的舊頭碼閘接線(席位接續 MVP-1,DECISION-seat-continuity v2 §1 #1;Andy 11-12 00:39 准)。

判斷在 Rust(crates/aib-stalehead,自己的測試);這裡只驗 dm-ctl.sh 怎麼接它,用假的 aib-stalehead:
H1 閘回 3(stale_head)⇒ rc 2、stage=preflight、一個字都沒貼、stderr 有現頭;閘收到的就是正文。
H2 閘回 0 ⇒ 照常投遞;它印的提醒照樣到 stderr。
H3 AIB_STALEHEAD=off ⇒ 不叫閘、照常投遞。
H4 閘壞了(rc 不是 0 / 3)⇒ 具名吵一行、照送(v2:查不到只提醒)。
H5 多目標 --to:閘只在父行程跑一次,不是每席一次。
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELI = "test-dm-reliability-abb89-1015.py"
_spec = importlib.util.spec_from_file_location("dm_reliability_box_sh", str(HERE / RELI))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

NEW = "e8e61c1cbff858bf6d9b10fafc47c08acded6739"


def setUpModule():
    R.setUpModule()


def tearDownModule():
    R.tearDownModule()


class Gate(unittest.TestCase):
    def fake(self, b, rc, err):
        """假閘:正文存到 seen-<n>,stderr 印 err,回 rc。"""
        f = b.dir / "fake-stalehead"
        f.write_text(
            "#!/bin/bash\n"
            '[ "$1" = dm ] || exit 9\n'
            'n=$(ls "%s"/seen-* 2>/dev/null | wc -l | tr -d " ")\n'
            'cat > "%s/seen-$n"\n'
            'echo \'{"blocked":%s}\'\n'
            "printf '%%s\\n' %s >&2\n"
            "exit %d\n" % (b.dir, b.dir, "true" if rc == 3 else "false",
                           "'" + err + "'", rc))
        os.chmod(f, 0o755)
        return str(f)

    def seen(self, b):
        return sorted(p.read_text() for p in b.dir.glob("seen-*"))

    def test_h1_stale_head_is_held_back(self):
        b = R.Box(self)
        g = self.fake(b, 3, "stale_head: c908ce78 是 #685 的舊 commit,現頭 " + NEW)
        p = b.run(["@abcde", "#685 請審，頭 c908ce78"], AIB_STALEHEAD=g)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("stale_head", p.stderr)
        self.assertIn(NEW, p.stderr)
        self.assertIn("stage=preflight", R.last_line(p))
        self.assertEqual(b.exec_calls(), [])
        self.assertEqual(self.seen(b), ["#685 請審，頭 c908ce78"])

    def test_h2_pass_delivers_and_shows_reminders(self):
        b = R.Box(self)
        g = self.fake(b, 0, "stale-head 提醒: 0badc0de 在 GitHub 上找不到")
        p = b.run(["@abcde", "本機頭 0badc0de"], AIB_STALEHEAD=g)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(b.exec_calls()), 1)
        self.assertIn("找不到", p.stderr)

    def test_h3_off_does_not_call_the_gate(self):
        b = R.Box(self)
        self.fake(b, 3, "x")
        p = b.run(["@abcde", "頭 c908ce78"], AIB_STALEHEAD="off")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(b.exec_calls()), 1)
        self.assertEqual(self.seen(b), [])

    def test_h4_a_broken_gate_is_named_and_does_not_block(self):
        b = R.Box(self)
        g = self.fake(b, 1, "boom")
        p = b.run(["@abcde", "頭 c908ce78"], AIB_STALEHEAD=g)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(b.exec_calls()), 1)
        self.assertIn("stale-head 閘沒能回答(rc=1)", p.stderr)

    def test_h5_multi_target_checks_once(self):
        b = R.Box(self, seats=((2, R.SEAT_UUID), (3, "11111111-2222-3333-4444-5555555fedcb")))
        g = self.fake(b, 0, "")
        b.run(["--to", "@abcde,@fedcb", "頭 c908ce78"], AIB_STALEHEAD=g)
        self.assertEqual(len(self.seen(b)), 1, self.seen(b))


if __name__ == "__main__":
    unittest.main()
