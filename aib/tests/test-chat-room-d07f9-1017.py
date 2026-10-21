#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""空間快照:走進好友的島與艦長室(開發文件第 34 節 v2;包 J)。核心一定跑(只用標準庫);端到端在 test-chat-e2e-world。

J1 scrub:任何層級的 ctx / unread / ip / host / hostname / content / body / msg 都拿掉;字串清控制字元與長度
J2 encode / decode:超過上限先丟 rooms.layout、再丟 island.decor;decode 壞資料 / 舊版 / 解壓炸彈 ⇒ None
J3 build:people 只列公開的席、名字角色由核心補;island 驗格子(≤ 64×64、建築在島內、島主只收名字與 emoji / 背景字);
   rooms 只在 with_rooms;now / step 只在 with_status;state / at 只在 with_state;禁止欄位不進快照
J4 空間分享:預設 island、room 只能逐位開(--default room ⇒ rc 2)、陌生人 none;在線梯子回到 off / online / status
J5 aib chat island / room:不是好友 ⇒ rc 1;好友只有 island ⇒ room 沒有;有 rooms ⇒ room 列艦名與席
刀(複本):K1 不濾禁止欄位 ⇒ J1 紅;K2 build 不看公開 ⇒ J3 紅
"""
import base64
import importlib.util
import json
import sys
import time
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
import room_snapshot as RS  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_pj", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 121))
OTHER = NK.npub(NK.pubkey_hex("%064x" % 122))


class Pure(unittest.TestCase):
    def test_j1_scrub(self):
        x = RS.scrub({"ship": {"name": "號\x1b[31m", "IP": "1.2.3.4"}, "cabins": [{"host": "mbp", "floors": [{"id": "1F"}]}],
                      "seats": {"a": {"ctx": 9, "Unread": 2, "lv": 3, "deep": {"content": "秘密", "ok": 1}}},
                      "msg": "x", "body": "y", "hostname": "z", "layout": {"t": "a" * 500}})
        blob = json.dumps(x, ensure_ascii=False)
        for bad in ('"IP"', '"host"', '"ctx"', '"Unread"', '"content"', '"msg"', '"body"', '"hostname"', "秘密", "\x1b"):
            self.assertNotIn(bad, blob, bad)
        self.assertEqual(x["seats"]["a"]["lv"], 3)
        self.assertEqual(len(x["layout"]["t"]), RS.STR_MAX)

    def test_j2_encode_decode(self):
        import os
        small = {"v": 2, "island": {"decor": [1]}, "people": [{"seat": "abcde"}],
                 "rooms": {"seats": [], "layout": {"a": 1}, "layout_omitted": False}}
        self.assertEqual(RS.decode(RS.encode(small))["rooms"]["layout"], {"a": 1})
        noise = lambda: [os.urandom(8).hex() for _ in range(6000)]
        got = RS.decode(RS.encode(dict(small, rooms=dict(small["rooms"], layout={"k": noise()}))))
        self.assertEqual((got["rooms"]["layout"], got["rooms"]["layout_omitted"], got["island"]["decor"]), (None, True, [1]),
                         "先丟艦長室的 layout")
        wire, full, over = RS.encode_info(dict(small, island={"decor": noise()}, rooms=dict(small["rooms"], layout={"k": noise()})))
        self.assertEqual((wire, over), (None, "too_big"), "第 36.3 節:島主親手擺的 decor 不丟;還太大就整則不送")
        self.assertGreater(full, RS.WIRE_MAX)
        self.assertIsNone(RS.decode("not base64!!"))
        self.assertIsNone(RS.decode(base64.b64encode(zlib.compress(b'{"v":1,"seats":[]}')).decode()), "舊版房間快照不收")
        bomb = base64.b64encode(zlib.compress(json.dumps({"v": 2, "people": [], "x": "0" * (RS.SPACE_RAW_MAX + 10)}).encode())).decode()
        self.assertIsNone(RS.decode(bomb), "解開超過上限的不收")


class Box(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        b = self.b
        self.codes = [b.seat("20261017-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "艦長"),
                      b.seat("20261017-000000-6ddf", "w3", "00000000-0000-0000-0000-00000000bbb2", "yyyy22", "工程席")]
        seats = b.home / ".ez2-aib" / "instances" / "20261017-000000-6ddf" / "seats"
        for w, role, prov in (("w2", "root", "claude"), ("w3", "dev", "codex")):
            rec = json.loads((seats / ("%s.json" % w)).read_text())
            rec.update(role=role, provider=prov)
            (seats / ("%s.json" % w)).write_text(json.dumps(rec))
        ident = b.home / ".ez2-aib" / "identity"
        (ident / "cards.json").write_text(json.dumps({"cards": {"xxxx11": {"emoji": "👑"}, "yyyy22": {"emoji": "🛠️"}}}))
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        b.cli("publish", self.codes[0])                       # 只公開艦長席

    def test_j3_build(self):
        (self.b.chat / "room.json").write_text(json.dumps({
            "v": 1, "ship": {"name": "Ez2 號", "ip": "10.0.0.1",
                             "captain": {"seat": self.codes[0], "name": "Andy", "avatar": "🧑‍✈️", "ip": "x"}},
            "seats": {self.codes[0]: {"floor": "3F", "lv": 9, "stars": 5, "name": "假名", "role": "假角色", "ctx": 90},
                      self.codes[1]: {"floor": "1F", "lv": 1}}}))
        (self.b.chat / "island.json").write_text(json.dumps({
            "v": 1, "name": "Andy 島", "style": "taiwan", "size": {"w": 200, "h": 10}, "owner": {"name": "Andy", "avatar": {"emoji": "🧑", "bg": "#123", "img": "x.png"}},
            "age": 3, "res": {"wood": 12, "Bad Name": 1, "stone": -1},
            "buildings": [{"cabin": "6ddf", "x": 3, "y": 4, "style": "塔", "kind": "hall", "lv": 2,
                           "building": {"start": 100, "done": 200}},
                          {"cabin": "6ddf", "x": 5, "y": 5, "kind": "BAD!", "lv": 500, "building": {"start": "x"}},
                          {"cabin": "zzzz", "x": 99, "y": 1}],
            "decor": [{"kind": "樹", "x": 1, "y": 1, "host": "mbp"}], "terrain": {"sea": True}}))
        snap = RS.build(self.b.env(), with_rooms=True, with_status=True)
        self.assertEqual([p["seat"] for p in snap["people"]], [self.codes[0]], "沒公開的席不准出現")
        p = snap["people"][0]
        self.assertEqual((p["name"], p["role"], p["provider"], p["emoji"]), ("艦長", "root", "claude", "👑"), "名字角色由核心補")
        isl = snap["island"]
        self.assertEqual((isl["name"], isl["size"], isl["owner"]["avatar"]), ("Andy 島", {"w": 16, "h": 10}, {"emoji": "🧑", "bg": "#123"}),
                         "島最大 64 格;島主不收圖檔")
        self.assertEqual([b["cabin"] for b in isl["buildings"]], ["6ddf", "6ddf"], "建築要在島內")
        b0, b1 = isl["buildings"]
        self.assertEqual((b0["kind"], b0["lv"], b0["building"]), ("hall", 2, {"start": 100, "done": 200}))
        self.assertEqual((b1["kind"], b1["lv"], "building" in b1), (None, None, False), "型別 / 範圍不合 ⇒ 當沒有")
        self.assertEqual((isl["age"], isl["res"], isl["style"]), (3, {"wood": 12}, "taiwan"))
        for bad in ("", "Taiwan", "-x", "a" * 25, "台灣", 3):
            self.assertIsNone(RS.clean_island({"style": bad})["style"], "風格包 id 不合 ⇒ 基本風格:%r" % (bad,))
        self.assertEqual(RS.clean_island({"style": "jp_v2-a"})["style"], "jp_v2-a")
        self.assertIsNone(RS.clean_island({})["style"], "沒有 style ⇒ 基本風格")
        r = snap["rooms"]
        self.assertEqual((r["seats"][0]["floor"], r["seats"][0]["lv"], r["ship"]["captain"]["seat"]), ("3F", 9, self.codes[0]))
        blob = json.dumps(snap, ensure_ascii=False)
        for bad in ("10.0.0.1", '"ctx"', '"host"', "x.png"):
            self.assertNotIn(bad, blob, bad)
        lite = RS.build(self.b.env(), with_rooms=False, with_status=False, with_state=False)
        self.assertIsNone(lite["rooms"], "island 等級不帶建築內部")
        self.assertEqual((lite["people"][0]["state"], lite["people"][0]["at"]), (None, None), "在線 off ⇒ 不帶 state")

    def test_j4_space(self):
        b = self.b
        b.cli("friend", "add", FRIEND)
        b.cli("mine", "add", OTHER)
        self.assertEqual(json.loads(b.cli("space", "--json").stdout)["peers"], {OTHER: "island", FRIEND: "island"}, "預設 island")
        self.assertEqual(b.cli("space", "--default", "room").returncode, 2, "room 不准當預設(門之前就擋,不彈指紋)")
        import chat_roster as CR
        CR.set_space("room", FRIEND, b.env())              # 10-21:改分享等級要本人 Touch ID(門在 CLI),測試直接走函式
        self.assertEqual(json.loads(b.cli("space", "--json").stdout)["peers"][FRIEND], "room")
        self.assertEqual(b.cli("share", FRIEND, "room").returncode, 2, "在線梯子沒有 room 了")
        stranger = NK.npub(NK.pubkey_hex("%064x" % 123))
        self.assertEqual(CR.space_level(stranger, b.env()), "none")

    def test_j5_island_room_cli(self):
        b = self.b
        GF.ensure_dirs(b.root)
        snap = {"v": 2, "island": {"name": "Bob 島", "size": {"w": 8, "h": 8}, "owner": {"name": "Bob"}, "buildings": []},
                "people": [{"seat": "bbbb2", "emoji": "🧭", "name": "Bob 艦長", "state": "online"}], "rooms": None}
        GF.write_room(b.root, FRIEND, snap)
        self.assertEqual(b.cli("island", FRIEND, "--json").returncode, 1, "不是好友 ⇒ 看不到")
        b.cli("friend", "add", FRIEND)
        r = b.cli("island", FRIEND)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Bob 島", r.stdout)
        self.assertIn("🧭", r.stdout)
        self.assertFalse(json.loads(b.cli("island", FRIEND, "--json").stdout)["has_room"])
        self.assertEqual(b.cli("room", FRIEND, "--json").returncode, 1, "只有 island ⇒ 沒有艦長室內部")
        GF.write_room(b.root, FRIEND, dict(snap, rooms={"ship": {"name": "Bob 號"}, "cabins": [], "seats": [{"seat": "bbbb2"}],
                                                        "layout": None, "layout_omitted": False}))
        self.assertIn("Bob 號", b.cli("room", FRIEND).stdout)
        rec = json.loads((b.root / "spaces" / (FRIEND + ".json")).read_text())
        rec["at"] = int(time.time()) - 8 * 86400
        (b.root / "spaces" / (FRIEND + ".json")).write_text(json.dumps(rec))
        self.assertFalse(json.loads(b.cli("island", FRIEND, "--json").stdout)["fresh"], "7 天沒更新 ⇒ 不新鮮")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/room_snapshot.py", needle, replacement,
                        tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_filter(self):
        self._knife(" if str(k).lower() not in FORBIDDEN}", "}", "Pure.test_j1_scrub")

    def test_k2_not_only_published(self):
        self._knife(' if s["seat5"] in pub and s["seat5"] in active]', ' if s["seat5"] in active]', "Box.test_j3_build")


if __name__ == "__main__":
    unittest.main()
