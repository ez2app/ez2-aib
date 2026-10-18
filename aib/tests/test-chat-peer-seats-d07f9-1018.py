#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""通訊錄的席位跟著名冊汰換(10-18 @74e8b 回報、Andy 實測:Mac Pro 13 開新艙 5a97、隱藏舊席 7fb66,
Pro 通訊錄還是只有 7fb66 而且排第一 ⇒ 艦長室一直寄到死席)。

P1 聯絡人快照:拿到不比快照舊的名冊(帶 roster_seats)⇒ seats 整份換成名冊目前公開的席、順序照名冊、死席拿掉;
   比快照舊的名冊 ⇒ seats 不動、roster_at 不倒退;沒帶名冊席位(只有這一席)⇒ 照舊只補這一席
P2(要 AIB_TEST_NOSTR_VENV)名冊從 A 席換成 B 席 ⇒ aib chat contacts --refresh <主人> ⇒ 快照只剩 B
   (通訊錄每一列的 seats 就是快照的 seats;自己不能加成好友,這裡直接看快照)
刀(複本):K1 不換整份、照舊只補 ⇒ P1 紅
"""
import importlib.util
import json
import os
import sys
import tempfile
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

_spec = importlib.util.spec_from_file_location("roster_t", str(HERE / "test-chat-roster-d07f9-1015.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)

OWNER = NK.npub(NK.pubkey_hex("%064x" % 0x6001))


def seat(cabin, code, i):
    return {"cabin": cabin, "seat": code, "npub": NK.npub(NK.pubkey_hex("%064x" % (0x6100 + i))), "name": "S%d" % i, "role": "dev"}


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-peerseats-")
        self.root = Path(self.td.name)
        GF.ensure_dirs(self.root)

    def tearDown(self):
        self.td.cleanup()

    def seats(self):
        return [(x["cabin"], x["seat"]) for x in GF.read_peers(self.root)[OWNER]["seats"]]

    def test_p1_replace_on_newer_roster(self):
        old, new, other = seat("436f", "7fb66", 1), seat("5a97", "4345c", 2), seat("5a97", "aaaa2", 3)
        GF.note_peer(self.root, OWNER, "Mac Pro 13", "TW", old, 1000, direction="out")
        self.assertEqual(self.seats(), [("436f", "7fb66")])
        GF.note_peer(self.root, OWNER, "Mac Pro 13", "TW", new, 2000, direction="out",
                     claims={"nip05": None, "roster_seats": [new, other]})
        self.assertEqual(self.seats(), [("5a97", "4345c"), ("5a97", "aaaa2")], "死席拿掉、順序照名冊")
        GF.note_peer(self.root, OWNER, None, None, old, 1500, claims={"nip05": None, "roster_seats": [old]})
        self.assertEqual(self.seats(), [("5a97", "4345c"), ("5a97", "aaaa2")], "舊名冊不准把死席加回來")
        self.assertEqual(GF.read_peers(self.root)[OWNER]["roster_at"], 2000, "roster_at 不倒退")
        GF.note_peer(self.root, OWNER, None, None, seat("5a97", "bbbb3", 4), None, direction="in")
        self.assertEqual(self.seats()[-1], ("5a97", "bbbb3"), "沒帶名冊席位 ⇒ 照舊補這一席")


@unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def test_p2_refresh(self):
        from aib_fake_relay import FakeRelay
        with FakeRelay() as relay:
            b = RT.Box([relay.url])
            self.addCleanup(b.close)
            b.link_venv()
            a = b.seat("20261015-000000-436f", "w2", "00000000-0000-0000-0000-00000007fb66", "aaaa11", "舊席")
            c = b.seat("20261015-000000-5a97", "w2", "00000000-0000-0000-0000-00000004345c", "cccc33", "新席")
            self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
            b.cli("publish", a)
            env = b.env()
            owner = NK.read_map(env)["owner"]["npub"]
            b.start_run()
            self.assertTrue(b.wait_for(lambda: b.status().get("state") == "published", 40), b.status())
            r = b.cli("contacts", "--refresh", owner, "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual([x["seat"] for x in GF.read_peers(b.root)[owner]["seats"]], [a])
            first = json.loads((b.root / "roster" / "published.json").read_text())["hash"]
            b.cli("hide", a)
            b.cli("publish", c)
            self.assertTrue(b.wait_for(lambda: json.loads((b.root / "roster" / "published.json").read_text()).get("hash")
                                       not in (None, first), 80), "新名冊沒發出去")   # 轉接程式兩次發名冊至少隔 60 秒
            time.sleep(1.1)                                    # 名冊時間以秒計:新的那份要比上一份晚
            r = b.cli("contacts", "--refresh", owner)
            self.assertIn("1", r.stdout, r.stdout + r.stderr)
            self.assertEqual([x["seat"] for x in GF.read_peers(b.root)[owner]["seats"]], [c], "死席要拿掉")


class Knife(unittest.TestCase):
    def test_k1_append_only(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_global_files.py", "        if fresh:\n", "        if False:\n",
                        tests=(name, "test-chat-roster-d07f9-1015.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Snapshot.test_p1_replace_on_newer_roster"],
                            cwd=str(root), max_procs=80, timeout=300, env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
