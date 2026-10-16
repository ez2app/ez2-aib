#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談改善包 A(開發文件第 27.1–27.3 節;Andy 10-16 13:3x「依序開始執行」)。

核心(一定跑):
M0 aib chat mine add/remove/列表:跟好友分開存(mine.json 0600);加自己 / 看不懂 ⇒ rc 2;keys 印出「在另一台跑」那一行
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
P1 27.1 開艙答「要」、沒設名字 ⇒ 不起寄件工人、沒有心跳(不連中繼站);設了名字與國別 ⇒ 工人起來、有心跳
M1 27.2 B 把 A 綁成「我的其他機器」(沒加好友)⇒ A 寄的收下(queued),不會被當陌生拒收;B 的好友名單沒多
S1 27.3 A 寄給 B(B 先加 A 好友)⇒ A 的好友名單自動多 B(標寄過信);B 回信 ⇒ A 收下
刀(複本):K1 沒名字也起工人 ⇒ P1 紅;K2 mine 不當本人 ⇒ M1 紅;K3 寄出不記對方 ⇒ S1 紅
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_pa", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
_fspec = importlib.util.spec_from_file_location("chat_friends_pa", str(HERE / "test-chat-friends-d07f9-1016.py"))
FT = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(FT)
OTHER = NK.npub(NK.pubkey_hex("%064x" % 43))


class Mine(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(self.b.cli("prepare", "--keys-only", "--quiet").returncode, 0)
        self.me = NK.read_map(self.b.env())["owner"]["npub"]

    def test_m0_mine_list(self):
        self.assertIn("還沒綁定", self.b.cli("mine").stdout)
        r = self.b.cli("mine", "add", "%s/931e/bbbb2" % OTHER, "--name", "Mini")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads(self.b.cli("mine", "--json").stdout)["mine"]
        self.assertEqual([(m["owner"], m["name"]) for m in got], [(OTHER, "Mini")])
        self.assertEqual(oct((self.b.chat / "mine.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual(json.loads(self.b.cli("friends", "--json").stdout)["friends"], [], "mine 跟好友分開存")
        for bad in (self.me, "hello"):
            self.assertEqual(self.b.cli("mine", "add", bad).returncode, 2, bad)
        self.assertEqual(self.b.cli("mine", "add").returncode, 2)
        self.assertIn("aib chat mine add " + self.me, self.b.cli("keys").stdout)
        self.assertEqual(json.loads(self.b.cli("status", "--json").stdout)["mine"], 1)
        self.assertEqual(self.b.cli("mine", "remove", OTHER).returncode, 0)
        self.assertEqual(json.loads(self.b.cli("mine", "--json").stdout)["mine"], [])


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items

    def pair(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        return a, x, b, y

    def test_p1_no_profile_no_relays(self):
        b = SB.Box([self.relay.url])
        self.addCleanup(b.close)
        b.chat.mkdir(parents=True, exist_ok=True)
        os.symlink(SB.NOSTR_VENV, str(b.chat / "venv"))
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        rc, out = FT.run_tty(b, ["auto"], "y")                        # 開艙時人在終端機答「要」(問一次,14:3x)
        self.assertEqual(rc, 0, out)
        b.start_run()
        self.assertTrue(b.wait_for(lambda: b.status().get("state") == "no_profile", 30), b.status())
        b.wait_for(lambda: False, 3)                                   # 多跑幾輪
        st = b.status()
        self.assertIsNone(st.get("worker_pid"), "沒設名字不准起寄件工人(會連公開站)")
        self.assertFalse(GF.bridge_alive(b.root))
        self.assertEqual(self.relay.received, [], "沒設名字不准連中繼站發任何東西")
        self.assertEqual(b.cli("name", "Andy").returncode, 0)
        self.assertEqual(b.cli("country", "TW").returncode, 0)
        self.assertTrue(b.wait_for(lambda: b.status().get("worker_pid") and GF.bridge_alive(b.root), 60), b.status())

    def test_m1_mine_is_own(self):
        a, x, b, y = self.pair()
        self.assertEqual(b.cli("mine", "add", a.owner).returncode, 0)
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        self.assertEqual(self.items(b, y)[0]["status"], "queued")
        self.assertEqual(json.loads(b.cli("friends", "--json").stdout)["friends"], [])

    def test_s1_sent_means_accept_reply(self):
        a, x, b, y = self.pair()
        b.cli("friend", "add", a.owner)
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)
        self.assertEqual(res["rc"], 0, res)
        self.assertTrue(a.wait_for(lambda: any(f["owner"] == b.owner and f.get("source") == "sent"
                                              for f in json.loads(a.cli("friends", "--json").stdout)["friends"]), 10))
        self.assertIn("寄過信自動加", a.cli("friends").stdout)
        back = GF.send_and_wait(b.root, SB.outbox_rec("%s/6ddf/%s" % (a.owner, x), y), wait=20)
        self.assertEqual((back["rc"], back["stage"]), (0, "queued"), back)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py",
                               "test-chat-friends-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_worker_without_profile(self):
        self._knife("src/cockpit/chat_bridge.py", '    if key_err or state == "no_profile":', "    if key_err:",
                    "Network.test_p1_no_profile_no_relays")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_mine_not_own(self):
        self._knife("src/cockpit/chat_nostr.py", ' or CR.is_mine(parsed["owner"]):', ":",
                    "Network.test_m1_mine_is_own")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_sent_not_remembered(self):
        self._knife("src/cockpit/chat_nostr.py", "            self._remember_peer(rec[\"to\"], res)\n", "",
                    "Network.test_s1_sent_means_accept_reply")


if __name__ == "__main__":
    unittest.main()
