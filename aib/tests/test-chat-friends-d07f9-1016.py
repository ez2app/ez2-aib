#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談:預設開 + 好友制(維護者 10-16 12:1x 裁示:
「1、不問,之後可以關;2、加入好友之後才能有已讀,不然拒收;3、已讀字樣照舊」)。

核心(一定跑,只用標準庫):
A1 開艙的 auto:沒開過、主人也沒關過 ⇒ 寫開關檔(標 auto);不連網、不發名冊、不設名字
A2 主人 aib chat off 過 ⇒ 留 off-by-owner.json ⇒ auto 不再打開;aib chat on ⇒ 拿掉記號
A3 AIB_CHAT_AUTO=0 或 AIB_CHAT_PREINSTALL=0 ⇒ auto 什麼都不做
A4 cockpit-full.sh 在「有開才開 sys.chat 窗」之前先跑 auto
F1 好友名單:主人 npub 或全球位址都收(位址取主人)、同人再加 = 改名、加自己 / 看不懂 ⇒ rc 2;檔案 0600;移除
F2 status --json 帶好友數與「主人關過」;拒收原因碼 ⇒ 寄件端一句話
連 Nostr(要 AIB_TEST_NOSTR_VENV;兩個沙盒 = 兩位主人,本機假中繼站):
N1 Bob 把 Andy 加為好友 ⇒ Andy 寄的進 Bob 佇列(queued,class friend);讀了 ⇒ Andy 收到「已讀」
N2 好友移除 ⇒ 之後寄的拒收(rc 2、原因「只收好友」),佇列沒多
刀(複本):K1 auto 不看 off 記號 ⇒ A2 紅;K2 好友比對永遠不中 ⇒ N1 紅;K3 陌生不拒收(退回待審)⇒ N2 紅
"""
import importlib.util
import json
import os
import sys
import time
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

_spec = importlib.util.spec_from_file_location("chat_recv_f", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
OTHER = NK.npub(NK.pubkey_hex("%064x" % 41))


class Base(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)

    def cli(self, *args, **extra):
        return self.b.cli(*args, **extra)

    def switch(self):
        try:
            return json.loads((self.b.chat / "enabled.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


class Auto(Base):
    def test_a1_auto_turns_on(self):
        r = self.cli("auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sw = self.switch()
        self.assertTrue(sw and sw.get("on") and sw.get("auto"), sw)
        self.assertEqual(oct((self.b.chat / "enabled.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.cli("enabled").returncode, 0)
        self.assertFalse((self.b.chat / "profile.json").exists(), "auto 不准替主人設名字(名字會公開)")
        self.assertFalse((self.b.root / "heartbeat.json").exists())
        self.assertIn("自動打開", r.stdout)
        self.assertEqual(self.cli("auto", "--quiet").stdout, "")

    def test_a2_off_is_remembered(self):
        self.cli("auto")
        self.assertEqual(self.cli("off").returncode, 0)
        self.assertTrue((self.b.chat / "off-by-owner.json").exists())
        self.assertEqual(self.cli("auto").returncode, 0)
        self.assertIsNone(self.switch(), "主人關過,開艙不准再自動打開")
        r = self.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.b.chat / "off-by-owner.json").exists(), "aib chat on 要拿掉記號")
        self.cli("off")
        self.cli("auto")
        self.assertIsNone(self.switch())

    def test_a3_env_opt_out(self):
        for k in ("AIB_CHAT_AUTO", "AIB_CHAT_PREINSTALL"):
            r = self.cli("auto", **{k: "0"})
            self.assertEqual(r.returncode, 0)
            self.assertIsNone(self.switch(), k)

    def test_a4_cockpit_runs_auto_before_window(self):
        cf = (COCKPIT / "cockpit-full.sh").read_text(encoding="utf-8")
        auto = cf.find('chat_bridge.py" auto --quiet')
        win = cf.find('chat_bridge.py" enabled')
        self.assertGreater(auto, 0)
        self.assertGreater(win, auto)


class Friends(Base):
    def setUp(self):
        super().setUp()
        self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(self.cli("prepare", "--keys-only", "--quiet").returncode, 0)
        self.me = NK.read_map(self.b.env())["owner"]["npub"]

    def friends(self):
        return json.loads(self.cli("friends", "--json").stdout)["friends"]

    def test_f1_add_remove(self):
        self.assertIn("還沒有好友", self.cli("friends").stdout)
        r = self.cli("friend", "add", OTHER, "--name", "Bob")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([(f["owner"], f["name"]) for f in self.friends()], [(OTHER, "Bob")])
        self.assertEqual(oct((self.b.chat / "friends.json").stat().st_mode & 0o777), "0o600")
        r = self.cli("friend", "add", "%s/931e/bbbb2" % OTHER, "--name", "Bobby")      # 位址 ⇒ 取主人;同人 = 改名
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([(f["owner"], f["name"]) for f in self.friends()], [(OTHER, "Bobby")])
        for bad in (self.me, "npub1xyz", "hello", "%s/931e" % OTHER):
            r = self.cli("friend", "add", bad)
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(len(self.friends()), 1)
        self.assertEqual(self.cli("friend", "remove", OTHER).returncode, 0)
        self.assertEqual(self.friends(), [])
        self.assertIn("沒有", self.cli("friend", "remove", OTHER).stdout)

    def test_f2_status_and_refuse_text(self):
        self.cli("friend", "add", OTHER)
        self.cli("off")
        rec = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual((rec["friends"], rec["off_by_owner"]), (1, True))
        self.assertIn("好友:1 位", self.cli("status").stdout)
        rc, st, _pos, detail = GF.outcome([{"stage": "refused", "detail": "not_friend"}])
        self.assertEqual((rc, st), (2, "refused"))
        self.assertIn("好友", detail)
        self.assertIn("名冊", GF.outcome([{"stage": "refused", "detail": "unverified"}])[3])
        self.assertEqual(GF.outcome([{"stage": "refused", "detail": "其他"}])[3], "其他")


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    box_extra = {"AIB_CHAT_READ_BATCH": "2"}
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items
    final = RT.Network.final

    def pair(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        return a, x, b, y

    def test_n1_friend_accepted_and_read(self):
        a, x, b, y = self.pair()
        r = b.cli("friend", "add", "%s/6ddf/%s" % (a.owner, x), "--name", "Andy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rec = SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        it = self.items(b, y)[0]
        self.assertEqual((it["status"], it["global"]["verified"]), ("queued", True))
        for to, who in (("claimed", "system"), ("delivered", "system"), ("read", "seat:" + y)):
            self.assertTrue(self.DQ._transition(y, it["id"], to, who, env=b.qenv)["ok"], to)
        self.assertTrue(self.final(a, rec["msgid"], "read", 30), GF.read_receipts(a.root, rec["msgid"]))

    def test_n2_unfriended_refused(self):
        a, x, b, y = self.pair()
        b.cli("friend", "add", a.owner)
        self.assertEqual(GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)["rc"], 0)
        b.cli("friend", "remove", a.owner)
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)
        self.assertEqual((res["rc"], res["stage"]), (2, "refused"), res)
        self.assertIn("好友", res["detail"])
        time.sleep(2)
        self.assertEqual(len(self.items(b, y)), 1, "移出好友後寄的不准進佇列")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_auto_ignores_off(self):
        self._knife("src/cockpit/chat_bridge.py", "    if is_enabled(env) or off_marker(env).exists():",
                    "    if is_enabled(env):", "Auto.test_a2_off_is_remembered")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_friend_never_matches(self):
        self._knife("src/cockpit/chat_roster.py", '    return bool(owner) and any(f["owner"] == owner for f in read_friends(env, kind))',
                    "    return False", "Network.test_n1_friend_accepted_and_read")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_stranger_not_refused(self):
        self._knife("src/cockpit/chat_nostr.py", '        if rec["class"] == "stranger":\n            # 維護者',
                    '        if False:\n            # 維護者', "Network.test_n2_unfriended_refused")


if __name__ == "__main__":
    unittest.main()
