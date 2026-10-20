#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""好友參觀快照 rooms.decor.titles(收藏稱號;@74e8b 10-19):只准 plant / furn / lamp / wall / drink / desk / fun。

T1 build:合格的 titles 去重後帶上;沒寫 / 空陣列 ⇒ 沒這鍵
T2 build:titles 夾別的字串 / 不是陣列 ⇒ 只丟 titles,ssr / sr 照帶
T3 收端(aib chat room --json)再驗一次:好友寄來的 titles 不合 ⇒ 只丟 titles
刀(複本):K1 titles 不驗白名單 ⇒ T2 紅
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

_spec = importlib.util.spec_from_file_location("room_decor_pt", str(HERE / "test-chat-room-decor-d07f9-1019.py"))
DE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DE)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 134))


class Titles(DE.Box):
    def test_t1_titles(self):
        r = self.decor({"ssr": ["piano"], "sr": 2, "titles": ["lamp", "fun", "lamp"]})
        self.assertEqual(r["decor"], {"ssr": ["piano"], "sr": 2, "titles": ["lamp", "fun"]})
        self.assertNotIn("titles", self.decor({"ssr": [], "sr": 0})["decor"])
        self.assertNotIn("titles", self.decor({"ssr": [], "sr": 0, "titles": []})["decor"])

    def test_t2_bad_titles_only_dropped(self):
        for bad in (["lamp", "營收 120 萬"], ["Lamp"], "lamp", [1], {"lamp": 1}):
            d = self.decor({"ssr": ["cuckoo"], "sr": 5, "titles": bad})["decor"]
            self.assertEqual(d, {"ssr": ["cuckoo"], "sr": 5}, bad)

    def test_t3_receive_side(self):
        b = self.b
        GF.ensure_dirs(b.root)
        b.cli("friend", "add", FRIEND)
        rooms = {"ship": {"name": "Bob 號"}, "cabins": [], "layout": None, "layout_omitted": False, "meet": [], "seats": []}
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [],
                                       "rooms": dict(rooms, decor={"ssr": ["train"], "sr": 1, "titles": ["desk", "營收"]})})
        got = json.loads(b.cli("room", FRIEND, "--json").stdout)
        self.assertEqual(got["decor"], {"ssr": ["train"], "sr": 1})
        self.assertNotIn("營收", json.dumps(got, ensure_ascii=False))


class Knife(unittest.TestCase):
    def test_k1_titles_unchecked(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/room_snapshot.py",
                        " and all(isinstance(t, str) and t in DECOR_TITLES for t in titles):", ":",
                        tests=(name, "test-chat-room-decor-d07f9-1019.py", "test-chat-room-office-d07f9-1019.py",
                               "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Titles.test_t2_bad_titles_only_dropped"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
