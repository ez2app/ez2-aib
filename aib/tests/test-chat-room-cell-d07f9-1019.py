#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""好友參觀快照的 rooms.seats[i].cell(這席在哪一區;區塊 key <世界>:<x>:<y>:<副本>,格式 @570a9 10-19 定版)。

C1 build:合 ^[a-z0-9-]{1,24}:[0-9]{1,3}:[0-9]{1,3}:(s[0-9]{1,3}|-)$ 且沒副本號(「-」)才帶;沒有就沒這鍵
C2 build:廣場副本(:s1)、格式不合、不是字串 ⇒ 都不進快照(在哪個副本只給要跟的朋友)
C3 收端(aib chat room --json)再驗一次:好友寄來的 cell 帶副本或不合照樣拿掉
刀(複本):K1 送端不擋副本 ⇒ C2 紅;K2 收端 cell 不驗 ⇒ C3 紅
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("room_office_pc", str(HERE / "test-chat-room-office-d07f9-1019.py"))
OF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OF)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 132))


class Box(OF.Box):
    def test_c1_cell(self):
        a, b, _ = self.codes
        r = self.snap({a: {"cell": "office-6ddf:0:0:-"}, b: {}}, None)["rooms"]
        self.assertEqual({s["seat"]: s.get("cell") for s in r["seats"]}, {a: "office-6ddf:0:0:-", b: None})
        self.assertNotIn("cell", [s for s in r["seats"] if s["seat"] == b][0], "沒區塊就沒這鍵")

    def test_c2_no_shard_no_junk(self):
        a, b, _ = self.codes
        r = self.snap({a: {"cell": "plaza:3:2:s1"}, b: {"cell": "Office:0:0:-"}}, None)["rooms"]
        self.assertEqual([s.get("cell") for s in r["seats"]], [None, None])
        for bad in ({"cell": 7}, {"cell": "isl-42:0:0"}, {"cell": "isl-42:1000:0:-"}, {"cell": "x" * 25 + ":0:0:-"}):
            self.assertIsNone(self.snap({a: bad}, None)["rooms"]["seats"][0].get("cell"), bad)
        self.assertNotIn("plaza", json.dumps(r))

    def test_c3_receive_side(self):
        b = self.b
        GF.ensure_dirs(b.root)
        b.cli("friend", "add", FRIEND)
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [], "rooms": {
            "ship": {"name": "Bob 號"}, "cabins": [], "layout": None, "layout_omitted": False, "meet": [],
            "seats": [{"seat": "bbbb1", "cell": "isl-42:0:0:-"}, {"seat": "bbbb2", "cell": "plaza:0:1:s3"},
                      {"seat": "bbbb3", "cell": "營收:0:0:-"}]}})
        got = json.loads(b.cli("room", FRIEND, "--json").stdout)
        self.assertEqual([s.get("cell") for s in got["seats"]], ["isl-42:0:0:-", None, None])
        self.assertNotIn("plaza", json.dumps(got))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-room-office-d07f9-1019.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_send_shard_allowed(self):
        self._knife("src/cockpit/room_snapshot.py", ' and v.endswith(":-") else None', " else None",
                    "Box.test_c2_no_shard_no_junk")

    def test_k2_recv_cell_unchecked(self):
        self._knife("src/cockpit/chat_bridge.py", '        if "cell" in s and not RS.clean_cell(s.get("cell")):',
                    '        if False:', "Box.test_c3_receive_side")


if __name__ == "__main__":
    unittest.main()
