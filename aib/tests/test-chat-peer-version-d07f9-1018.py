#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""版本混用說清楚(開發文件第 37.5 節):名冊上的 aib_version ⇒ 通訊錄 / world 帶對方版本與它收不到的功能。

V1 功能表:0.1.15 ⇒ 全缺;0.1.16 ⇒ 缺 space_v3 / offers / human_author;0.1.21 ⇒ 不缺;0.1.9 ⇒ 全缺(數字比,不是字串比);
   版本不知道 / 不合格("0.1"、"v0.1.20"、數字、超長)⇒ None(不亂講對方缺什麼)
V2 驗名冊:合格版本留下;不合格的整欄當 None(名冊照用)
V3 聯絡人快照記對方版本;contacts --json 與 world 每位帶 aib + missing(假主人報 0.1.16 ⇒ missing 含 space_v3);
   沒報版本的 ⇒ aib / missing 都是 None
V4(要 AIB_TEST_NOSTR_VENV)lookup 自己的名冊 ⇒ aib = 這一版
V5 多台合併(@abb89 10-18 11:53):每個艙帶宣告它那台的 aib_version(主人名冊 0.1.21、機器名冊 0.1.16 ⇒ 各取各的)
V6(要 AIB_TEST_NOSTR_VENV)主人名冊 = 這一版、清單上另一台報 0.1.16 ⇒ lookup 那台的席 aib = 0.1.16、主人那台的席 = 這一版
刀(複本):K1 版本用字串比 ⇒ V1 紅(0.1.9 被當成比 0.1.16 新);K2 resolve 只取主人名冊的版本 ⇒ V6 紅
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
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("roster_t", str(HERE / "test-chat-roster-d07f9-1015.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
_spec = importlib.util.spec_from_file_location("devices_t", str(HERE / "test-chat-devices-k1-d07f9-1018.py"))
DT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DT)
import chat_devices as DV  # noqa: E402
_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

ALL = ["room", "space_v3", "offers", "human_author"]
OLD = NK.npub(NK.pubkey_hex("%064x" % 0x3001))
NOVER = NK.npub(NK.pubkey_hex("%064x" % 0x3002))


class Core(unittest.TestCase):
    def test_v1_features(self):
        self.assertEqual(CR.missing_features("0.1.15"), ALL)
        self.assertEqual(CR.missing_features("0.1.16"), ["space_v3", "offers", "human_author"])
        self.assertEqual(CR.missing_features("0.1.18"), ["human_author"])
        self.assertEqual(CR.missing_features("0.1.21"), [])
        self.assertEqual(CR.missing_features("0.2.0"), [])
        self.assertEqual(CR.missing_features("0.1.9"), ALL, "要數字比:0.1.9 比 0.1.16 舊")
        for bad in (None, "0.1", "v0.1.20", 120, "0.1.20" + "9" * 20, "0.1.20\n"):
            self.assertIsNone(CR.missing_features(bad), repr(bad))
            self.assertIsNone(CR.clean_version(bad), repr(bad))

    def test_v2_parse(self):
        owner = NK.npub(NK.pubkey_hex("%064x" % 0x3003))
        base = {"v": 1, "owner": owner, "display_name": "A", "country": "TW", "envelope": [1],
                "dm_relays": ["wss://relay.example"], "cabins": []}
        self.assertEqual(CR.parse(dict(base, aib_version="0.1.19"), owner)["aib_version"], "0.1.19")
        self.assertIsNone(CR.parse(dict(base, aib_version="<b>0.1</b>"), owner)["aib_version"])
        self.assertIsNone(CR.parse(dict(base), owner)["aib_version"])


class Merge(unittest.TestCase):
    def test_v5_per_cabin_version(self):
        owner, dev = DT.OWNER, DT.A2
        base = dict(DT.roster(owner, [("6ddf", [("aaaa1", DT.npub_of(DT.SK[10]))])]), aib_version="0.1.21")
        d2 = dict(DT.roster(owner, [("2282", [("bbbb2", DT.npub_of(DT.SK[11]))])]), aib_version="0.1.16")
        merged, _w = DV.merge(owner, base, [(dev, d2)])
        self.assertEqual(DV.find(merged, "2282", "bbbb2")[1]["aib_version"], "0.1.16", "席在那台 ⇒ 那台的版本")
        self.assertEqual(DV.find(merged, "6ddf", "aaaa1")[1]["aib_version"], "0.1.21")
        self.assertEqual(merged["aib_version"], "0.1.21", "整份的仍是主人名冊那份")


class Contacts(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-peerver-")
        home = Path(self.td.name) / "home"
        home.mkdir()
        self.root = Path(self.td.name) / "bridge"
        self.env = {"HOME": str(home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW"}
        GF.ensure_dirs(self.root)

    def tearDown(self):
        self.td.cleanup()

    def test_v3_contacts_and_world(self):
        for o in (OLD, NOVER):
            CR.add_friend(o, "舊版" if o == OLD else "沒報", env=self.env)
            GF.note_world(self.root, o, "6ddf", [{"seat": "aaaa1", "state": "idle"}])
        GF.note_peer(self.root, OLD, "舊版", "TW", claims={"nip05": None, "aib": "0.1.16"})
        GF.note_peer(self.root, NOVER, "沒報", "TW", claims={"nip05": None})
        rows = {r["owner"]: r for r in CB.contacts_payload(self.env)["contacts"]}
        self.assertEqual(rows[OLD]["aib"], "0.1.16")
        self.assertIn("space_v3", rows[OLD]["missing"])
        self.assertEqual((rows[NOVER]["aib"], rows[NOVER]["missing"]), (None, None), "沒報版本不亂講")
        GF.note_peer(self.root, OLD, claims={"nip05": None, "aib": "0.1.21"})
        rows = {r["owner"]: r for r in CB.contacts_payload(self.env)["contacts"]}
        self.assertEqual((rows[OLD]["aib"], rows[OLD]["missing"]), ("0.1.21", []), "升級後跟著更新")
        world = {c["owner"]: c for c in CB.world_payload(self.env, now=time.time())["cabins"]}
        self.assertEqual((world[OLD]["aib"], world[OLD]["missing"]), ("0.1.21", []))
        self.assertEqual((world[NOVER]["aib"], world[NOVER]["missing"]), (None, None))


@unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def test_v4_lookup_has_version(self):
        from aib_fake_relay import FakeRelay
        with FakeRelay() as relay:
            b = RT.Box([relay.url])
            self.addCleanup(b.close)
            b.link_venv()
            seat = b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
            self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
            b.cli("publish", seat)
            owner = NK.read_map(b.env())["owner"]["npub"]
            b.start_run()
            self.assertTrue(b.wait_for(lambda: b.status().get("state") == "published", 40), b.status())
            r = b.cli("lookup", "%s/6ddf/%s" % (owner, seat))
            rec = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(rec.get("aib"), (REPO / "VERSION").read_text().strip(), rec)


@unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class MultiNet(DT.Network):
    """借 K1 的沙盒(A1 發名冊、主人金鑰簽清單、A2 發機器名冊);只跑這一格,K1 那幾格不在這裡重跑。"""
    test_n1_merge_device_roster = test_n2_revoke_and_replay = test_n3_unlisted_signer = test_n4_cabin_clash = None

    def test_v6_seat_device_version(self):
        evs = self.device_roster()
        content = json.loads(evs[0]["content"])
        content["aib_version"] = "0.1.16"
        evs[0]["content"] = json.dumps(content)
        self.push([self.sign_list([DT.dev(self.dev, "A2")], 1)] + evs)
        rc, rec = self.lookup("%s/a2c2/dddd2" % self.owner)
        self.assertEqual((rc, rec.get("aib")), (0, "0.1.16"), "席在 A2 ⇒ 取 A2 的版本:%s" % rec)
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.seat))
        self.assertEqual((rc, rec.get("aib")), (0, (REPO / "VERSION").read_text().strip()), rec)


class Knife(unittest.TestCase):
    def test_k1_string_compare(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_roster.py",
                        '    have = tuple(int(x) for x in v.split("."))\n'
                        '    return [name for name, need in FEATURES if have < tuple(int(x) for x in need.split("."))]\n',
                        '    return [name for name, need in FEATURES if v < need]\n',
                        tests=(name, "test-chat-roster-d07f9-1015.py", "test-chat-devices-k1-d07f9-1018.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Core.test_v1_features"], cwd=str(root),
                            max_procs=80, timeout=300, env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    @unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(被砍的 MultiNet 也會跳過)")
    def test_k2_head_version_only(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py",
                        '                   aib=cab.get("aib_version") or rec.get("aib_version"))',
                        '                   aib=rec.get("aib_version"))',
                        tests=(name, "test-chat-roster-d07f9-1015.py", "test-chat-devices-k1-d07f9-1018.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "MultiNet.test_v6_seat_device_version"],
                            cwd=str(root), max_procs=80, timeout=300, env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        if r.returncode == 0 and "skipped=" in (r.stdout + r.stderr):
            self.skipTest("被砍的那格跳過了,刀沒得驗")
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
