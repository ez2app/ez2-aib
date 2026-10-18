#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib chat world --json:同一位主人有多個艙 ⇒ 新的(fresh、最近回報)排前面。

10-19 Andy 實機:Pro 13 的主人在 world 裡有舊艙 436f(13 小時前,fresh=false)排在現用艙 5a97(81 秒前)前面,
艦長室只看第一格 ⇒ 明明在線卻顯示不在線。舊艙分不出是關掉的艙還是睡著的另一台,所以不刪,只排序。

W1 舊艙按鍵名排在前面也一樣:cabins 第一格是 fresh 的現用艙;舊艙還在(machine 狀態照給)
W2 兩個都 fresh ⇒ 最近回報的在前;都過期 ⇒ 也照最近的在前
刀(複本):K1 拿掉排序 ⇒ W1 紅
"""
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

OWNER = NK.npub(NK.pubkey_hex("%064x" % 0x3101))


class World(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-wff-")
        self.addCleanup(td.cleanup)
        home = Path(td.name) / "home"
        home.mkdir()
        self.root = Path(td.name) / "bridge"
        self.env = {"HOME": str(home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW"}
        GF.ensure_dirs(self.root)
        CR.add_friend(OWNER, "Pro 13", env=self.env)
        self.now = time.time()

    def cabins(self):
        return [(c["cabin"], c["fresh"]) for c in CB.world_payload(self.env, now=self.now)["cabins"]]

    def test_w1_fresh_cabin_first(self):
        # 436f 鍵名排在 5a97 前面(實機就是這樣)
        GF.note_world(self.root, OWNER, "436f", [{"seat": "7fb66", "state": "online"}], now=self.now - 13 * 3600)
        GF.note_world(self.root, OWNER, "5a97", [{"seat": "4345c", "state": "idle"}], now=self.now - 81)
        self.assertEqual(self.cabins(), [("5a97", True), ("436f", False)])

    def test_w2_newest_first(self):
        GF.note_world(self.root, OWNER, "aaaa", [{"seat": "aaaa1", "state": "idle"}], now=self.now - 300)
        GF.note_world(self.root, OWNER, "bbbb", [{"seat": "bbbb1", "state": "online"}], now=self.now - 60)
        self.assertEqual([c for c, _f in self.cabins()], ["bbbb", "aaaa"])
        GF.note_world(self.root, OWNER, "aaaa", [{"seat": "aaaa1", "state": "idle"}], now=self.now - 9 * 3600)
        GF.note_world(self.root, OWNER, "bbbb", [{"seat": "bbbb1", "state": "idle"}], now=self.now - 8 * 3600)
        self.assertEqual(self.cabins(), [("bbbb", False), ("aaaa", False)])


class Knife(unittest.TestCase):
    def test_k1_no_sort(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py",
                        '    cabins.sort(key=lambda x: (not x["fresh"], -int(x["presence_at"] or 0)))\n', "",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "World.test_w1_fresh_cabin_first"],
                            cwd=str(root), max_procs=60, timeout=200)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
