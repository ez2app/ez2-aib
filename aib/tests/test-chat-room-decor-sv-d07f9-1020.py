#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""好友參觀快照 rooms.decor.sv(旅行收藏;@74e8b 10-20,排 0.1.27)。
sv = {"n", "m", "sets": 0–999 整數, "show": [^i_sv_[a-z0-9_]{1,40}$ 去重 ≤ 6], "cont": [asia/europe/americas/mea/oceania 去重]}

V1 build:合格的 sv 帶上(show / cont 去重、多的鍵不帶);沒寫 ⇒ 沒這鍵
V2 build:sv 任何一處不合 ⇒ 只丟 sv,ssr / sr / titles 照帶
V3 收端(aib chat room --json)再驗一次:好友寄來的 sv 不合 ⇒ 只丟 sv
刀(複本):K1 show 不驗 id ⇒ V2 紅;K2 cont 不驗白名單 ⇒ V2 紅
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

_spec = importlib.util.spec_from_file_location("room_decor_sv", str(HERE / "test-chat-room-decor-d07f9-1019.py"))
DE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DE)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 135))
SV = {"n": 12, "m": 80, "sets": 2, "show": ["i_sv_tokyo_tower", "i_sv_paris_1", "i_sv_tokyo_tower"],
      "cont": ["asia", "europe", "asia"]}
SV_OK = {"n": 12, "m": 80, "sets": 2, "show": ["i_sv_tokyo_tower", "i_sv_paris_1"], "cont": ["asia", "europe"]}
BASE = {"ssr": ["piano"], "sr": 2, "titles": ["lamp"]}
BAD = [
    "x", [], {"n": 1, "m": 2, "sets": 0, "show": []},                                   # 缺 cont
    dict(SV_OK, n=True), dict(SV_OK, m=1000), dict(SV_OK, sets=-1), dict(SV_OK, n="3"),
    dict(SV_OK, show=["營收 120 萬"]), dict(SV_OK, show=["i_sv_"]), dict(SV_OK, show=["I_SV_A"]),
    dict(SV_OK, show=["i_sv_a b"]), dict(SV_OK, show=["i_sv_" + "a" * 41]), dict(SV_OK, show="i_sv_a"),
    dict(SV_OK, show=["i_sv_%d" % i for i in range(7)]),
    dict(SV_OK, cont=["africa"]), dict(SV_OK, cont=["Asia"]), dict(SV_OK, cont="asia"), dict(SV_OK, cont=[1]),
]


class Sv(DE.Box):
    def test_v1_sv(self):
        d = self.decor(dict(BASE, sv=dict(SV, extra="營收")))["decor"]
        self.assertEqual(d, dict(BASE, sv=SV_OK))
        self.assertNotIn("sv", self.decor(BASE)["decor"])
        six = dict(SV_OK, show=["i_sv_%d" % i for i in range(6)] + ["i_sv_0"], cont=[])
        self.assertEqual(self.decor(dict(BASE, sv=six))["decor"]["sv"]["show"], ["i_sv_%d" % i for i in range(6)])

    def test_v2_bad_sv_only_dropped(self):
        for bad in BAD:
            d = self.decor(dict(BASE, sv=bad))["decor"]
            self.assertEqual(d, BASE, bad)

    def test_v3_receive_side(self):
        b = self.b
        GF.ensure_dirs(b.root)
        b.cli("friend", "add", FRIEND)
        rooms = {"ship": {"name": "Bob 號"}, "cabins": [], "layout": None, "layout_omitted": False, "meet": [], "seats": []}
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [],
                                       "rooms": dict(rooms, decor=dict(BASE, sv=dict(SV_OK, show=["營收"])))})
        got = json.loads(b.cli("room", FRIEND, "--json").stdout)
        self.assertEqual(got["decor"], BASE)
        self.assertNotIn("營收", json.dumps(got, ensure_ascii=False))
        GF.write_room(b.root, FRIEND, {"v": 2, "island": {"name": "Bob 島"}, "people": [],
                                       "rooms": dict(rooms, decor=dict(BASE, sv=SV))})
        self.assertEqual(json.loads(b.cli("room", FRIEND, "--json").stdout)["decor"], dict(BASE, sv=SV_OK))


class Knife(unittest.TestCase):
    def _knife(self, needle, repl):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/room_snapshot.py", needle, repl,
                        tests=(name, "test-chat-room-decor-d07f9-1019.py", "test-chat-room-office-d07f9-1019.py",
                               "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Sv.test_v2_bad_sv_only_dropped"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_show_unchecked(self):
        self._knife("isinstance(t, str) and DECOR_SV_ID_RE.match(t) for t in show", "True for t in show")

    def test_k2_cont_unchecked(self):
        self._knife("isinstance(t, str) and t in DECOR_SV_CONT for t in cont", "True for t in cont")


if __name__ == "__main__":
    unittest.main()
