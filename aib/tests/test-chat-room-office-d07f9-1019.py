#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""好友參觀快照的新辦公室欄位(@74e8b 10-19 列的兩個;其他都不送)。

O1 build:rooms.seats[i].use 只帶合 ^[a-z][0-9]{1,4}$ 的物件 id;rooms.meet 只留公開席、每組 ≥ 2、去重
O2 build:room.json 夾的 state / tired / item / unread / ctx、use 夾文字或數字、meet 夾沒公開的席 ⇒ 都不進快照
O3 收端(aib chat room --json)再驗一次:好友寄來的 use / meet 不合照樣拿掉
刀(複本):K1 送端 use 不驗 ⇒ O2 紅;K2 收端 meet 不驗 ⇒ O3 紅
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
import room_snapshot as RS  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_po", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 131))


class Box(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        b = self.b
        self.codes = [b.seat("20261019-000000-6ddf", "w%d" % (i + 2), "00000000-0000-0000-0000-0000000%05d" % i,
                             "s%05d" % i, "席%d" % i) for i in range(3)]
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        b.cli("publish", self.codes[0])
        b.cli("publish", self.codes[1])                      # codes[2] 沒公開

    def snap(self, seats, meet):
        (self.b.chat / "room.json").write_text(json.dumps({"v": 1, "ship": {"name": "Ez2 號"}, "seats": seats, "meet": meet}))
        return RS.build(self.b.env(), with_rooms=True, with_status=True)

    def test_o1_fields(self):
        a, b, c = self.codes
        r = self.snap({a: {"use": "m13"}, b: {}}, [[a, b], [a, a, b, c]])["rooms"]
        self.assertEqual({s["seat"]: s.get("use") for s in r["seats"]}, {a: "m13", b: None})
        self.assertNotIn("use", [s for s in r["seats"] if s["seat"] == b][0], "沒在用就沒這鍵")
        self.assertEqual(r["meet"], [[a, b], [a, b]])
        self.assertEqual(self.snap({}, None)["rooms"]["meet"], [], "沒人開會 = []")

    def test_o2_nothing_else(self):
        a, b, c = self.codes
        seats = {a: {"use": "咖啡機 85%", "state": "busy", "tired": 3, "item": "測試機", "unread": 4, "ctx": 90},
                 b: {"use": "m13x"}}
        r = self.snap(seats, [[a, c], [c, "zzzzz"], "ab", [a, 5]])["rooms"]
        self.assertEqual([s.get("use") for s in r["seats"]], [None, None], "use 不合 ⇒ 不帶")
        self.assertEqual(r["meet"], [], "沒公開的席、不是席碼、單人一組都不留")
        blob = json.dumps(r, ensure_ascii=False)
        for bad in ("咖啡機", "85%", '"tired"', '"item"', '"unread"', '"ctx"', "測試機", c):
            self.assertNotIn(bad, blob, bad)

    def test_o3_receive_side(self):
        b = self.b
        GF.ensure_dirs(b.root)
        b.cli("friend", "add", FRIEND)
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [], "rooms": {
            "ship": {"name": "Bob 號"}, "cabins": [], "layout": None, "layout_omitted": False,
            "seats": [{"seat": "bbbb1", "use": "m2"}, {"seat": "bbbb2", "use": "營收 120 萬"}],
            "meet": [["bbbb1", "bbbb2"], ["bbbb1", "zzzz9"], ["bbbb1"]]}})
        got = json.loads(b.cli("room", FRIEND, "--json").stdout)
        self.assertEqual([s.get("use") for s in got["seats"]], ["m2", None])
        self.assertEqual(got["meet"], [["bbbb1", "bbbb2"]])
        self.assertNotIn("營收", json.dumps(got, ensure_ascii=False))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_send_use_unchecked(self):
        self._knife("src/cockpit/room_snapshot.py", "            if clean_use(e.get(\"use\")):", "            if e.get(\"use\"):",
                    "Box.test_o2_nothing_else")

    def test_k2_recv_meet_unchecked(self):
        self._knife("src/cockpit/chat_bridge.py", '"meet": RS.clean_meet(r.get("meet"), {s.get("seat") for s in seats})}',
                    '"meet": r.get("meet") or []}', "Box.test_o3_receive_side")


if __name__ == "__main__":
    unittest.main()
