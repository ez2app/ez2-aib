#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""重開艙後公開席自動接回(第二波 #6 之 NOTE #23;10-21 @abb89 裁「公開跟人格走、名冊格式不改」)。

10-20 Pro 13 實測:重開艙席碼全換(session_uuid 末五碼),金鑰跟人格走所以 npub 不變,但 profile.public 存的是舊席碼
⇒ 名冊不列新席、好友看到舊席「睡著」、寄到舊位址沒人收,要人手動 aib chat publish。
做法:nostr_keys.sync_seats 回 ("moved", 人格, 新席, 舊席)⇒ chat_roster.swap_public(舊, 新);名冊照既有雜湊重發。

P1 swap_public:public 有舊席 ⇒ 換成新席、回 True;沒有舊席 / 同席 / 壞席碼 / 沒 profile ⇒ 不動、False
P2 重開艙:公開席 A(人格 x)關掉,同人格在新艙出現為 B ⇒ sync_keys 回 moved + public_moved、public=[B]、名冊只列 B(npub 同一把)
P3 不是搬家:新人格的席出現(created)⇒ public 不動;A 還活著又多一席同人格(created_extra)⇒ public 不動
刀(複本):K1 sync_keys 不叫 swap_public ⇒ P2 紅
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import chat_roster as CR  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_pf", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
INST1 = "20261021-000000-6ddf"
INST2 = "20261021-010000-754c"
UUID_A = "00000000-0000-0000-0000-00000000aaa1"
UUID_B = "00000000-0000-0000-0000-00000000bbb2"
UUID_C = "00000000-0000-0000-0000-00000000ccc3"


def _load_cb():
    spec = importlib.util.spec_from_file_location("chat_bridge_pf", str(COCKPIT / "chat_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Box(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        self.env = self.b.env()
        self.a = self.b.seat(INST1, "w2", UUID_A, "xxxx11", "艦長")
        self.assertEqual(self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.assertEqual(self.b.cli("publish", self.a).returncode, 0)
        self.CB = _load_cb()

    def public(self):
        return list((CR.read_profile(self.env) or {}).get("public") or [])

    def roster_seats(self):
        content, _dm = CR.build(self.env)
        return [(s["seat"], s["npub"]) for c in content["cabins"] for s in c["seats"]]

    def test_p1_swap_public(self):
        self.assertEqual(self.public(), [self.a])
        self.assertFalse(CR.swap_public("zzzz9", "yyyy8", self.env), "舊席不在 public")
        self.assertFalse(CR.swap_public(self.a, self.a, self.env), "同席")
        self.assertFalse(CR.swap_public(self.a, "bad!", self.env), "壞席碼")
        self.assertEqual(self.public(), [self.a])
        self.assertTrue(CR.swap_public(self.a, "yyyy8", self.env))
        self.assertEqual(self.public(), ["yyyy8"])
        self.assertFalse(CR.swap_public("yyyy8", "zzzz9", {"HOME": str(self.b.td / "nowhere")}), "沒 profile")

    def test_p2_reopen_cabin_moves_public(self):
        old = self.roster_seats()
        self.assertEqual([s for s, _n in old], [self.a])
        npub_a = old[0][1]
        (self.b.home / ".ez2-aib" / "instances" / INST1 / "seats" / "w2.json").unlink()      # 艙關了
        b = self.b.seat(INST2, "w2", UUID_B, "xxxx11", "艦長")                                 # 同人格在新艙重開
        acts, err = self.CB.sync_keys(self.env)
        self.assertIsNone(err)
        kinds = {(x[0], x[2], x[3]) for x in acts if x[0] in ("moved", "public_moved")}
        self.assertEqual(kinds, {("moved", b, self.a), ("public_moved", b, self.a)}, acts)
        self.assertEqual(self.public(), [b], "公開跟人格走,不用人再 publish")
        self.assertEqual(self.roster_seats(), [(b, npub_a)], "名冊列新席、npub 同一把")

    def test_p3_not_a_move(self):
        c = self.b.seat(INST1, "w3", UUID_C, "yyyy22", "新人格")
        acts, err = self.CB.sync_keys(self.env)
        self.assertIsNone(err)
        self.assertIn(("created", "yyyy22", c), acts)
        self.assertEqual(self.public(), [self.a])
        d = self.b.seat(INST2, "w2", UUID_B, "xxxx11", "艦長分身")      # A 還活著,同人格多一席 ⇒ 第二把,不是搬家
        acts, _err = self.CB.sync_keys(self.env)
        self.assertIn(("created_extra", "xxxx11", d), acts)
        self.assertNotIn("public_moved", {x[0] for x in acts})
        self.assertEqual(self.public(), [self.a])


class Knife(unittest.TestCase):
    def test_k1_no_swap(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py",
                        '        if act[0] == "moved" and len(act) >= 4 and CR.swap_public(act[3], act[2], env):',
                        '        if False:', tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Box.test_p2_reopen_cabin_moves_public"],
                            cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
