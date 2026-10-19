#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""好友參觀快照的 rooms.decor(收藏擺設摘要 {"ssr": [type…], "sr": int};@74e8b 10-19)。

D1 build:合格就帶 {"ssr", "sr"} 兩鍵(ssr 去重);沒寫 decor ⇒ 沒這鍵
D2 build:ssr 夾大寫 / 數字 / 超過 24 字 / 去重後超過 12 個、sr 不是 0–999 整數(含 true、字串、1000)⇒ 整個不帶;多的鍵不帶
D3 收端(aib chat room --json)再驗一次:好友寄來的 decor 不合 ⇒ None
刀(複本):K1 送端不驗 ssr ⇒ D2 紅;K2 收端不驗 ⇒ D3 紅
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

_spec = importlib.util.spec_from_file_location("room_office_pd", str(HERE / "test-chat-room-office-d07f9-1019.py"))
OF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OF)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 133))


class Box(OF.Box):
    def decor(self, d):
        (self.b.chat / "room.json").write_text(json.dumps({"v": 1, "ship": {"name": "Ez2 號"}, "decor": d}))
        return __import__("room_snapshot").build(self.b.env(), with_rooms=True, with_status=True)["rooms"]

    def test_d1_decor(self):
        r = self.decor({"ssr": ["lamp", "clock", "lamp"], "sr": 7, "note": "營收"})
        self.assertEqual(r["decor"], {"ssr": ["lamp", "clock"], "sr": 7})
        self.assertNotIn("營收", json.dumps(r, ensure_ascii=False))
        self.assertNotIn("decor", self.snap({}, None)["rooms"], "沒寫 decor 就沒這鍵")

    def test_d2_bad_drops_all(self):
        bad = [{"ssr": ["Lamp"], "sr": 1}, {"ssr": ["lamp2"], "sr": 1}, {"ssr": ["a" * 25], "sr": 1},
               {"ssr": ["t" + chr(97 + i) for i in range(13)], "sr": 1}, {"ssr": ["lamp"], "sr": True},
               {"ssr": ["lamp"], "sr": "3"}, {"ssr": ["lamp"], "sr": 1000}, {"ssr": ["lamp"], "sr": -1},
               {"ssr": "lamp", "sr": 1}, {"sr": 1}, ["lamp"]]
        for d in bad:
            self.assertNotIn("decor", self.decor(d), d)
        self.assertEqual(self.decor({"ssr": ["t" + chr(97 + i) for i in range(12)] + ["ta"], "sr": 999})["decor"]["sr"],
                         999, "去重後 12 個、sr 999 還合格")

    def test_d3_receive_side(self):
        b = self.b
        GF.ensure_dirs(b.root)
        b.cli("friend", "add", FRIEND)
        rooms = {"ship": {"name": "Bob 號"}, "cabins": [], "layout": None, "layout_omitted": False, "meet": [], "seats": []}
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [],
                                       "rooms": dict(rooms, decor={"ssr": ["lamp"], "sr": 3, "x": 1})})
        self.assertEqual(json.loads(b.cli("room", FRIEND, "--json").stdout)["decor"], {"ssr": ["lamp"], "sr": 3})
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [],
                                       "rooms": dict(rooms, decor={"ssr": ["營收 120 萬"], "sr": 3})})
        got = json.loads(b.cli("room", FRIEND, "--json").stdout)
        self.assertIsNone(got["decor"])
        self.assertNotIn("營收", json.dumps(got, ensure_ascii=False))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-room-office-d07f9-1019.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_send_ssr_unchecked(self):
        self._knife("src/cockpit/room_snapshot.py", " and DECOR_TYPE_RE.match(t) for t in ssr)", " for t in ssr)",
                    "Box.test_d2_bad_drops_all")

    def test_k2_recv_unchecked(self):
        self._knife("src/cockpit/chat_bridge.py", '"decor": RS.clean_decor(r.get("decor")),', '"decor": r.get("decor"),',
                    "Box.test_d3_receive_side")


if __name__ == "__main__":
    unittest.main()
