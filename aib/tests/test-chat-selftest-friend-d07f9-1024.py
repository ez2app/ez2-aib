#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib chat selftest 抽一位好友查一次(10-24 清單第 7 條:fishmac 看 Andy 離線四天,兩邊 selftest 都全綠)。

F1 沒有好友 ⇒ 不列 friend 這一項
F2 有兩位好友 ⇒ 挑最近有往來的那位,用快照記的第一席查;查得到 ⇒ ✓
F3 查不到 ⇒ ✗、why 帶原因 + hint(名冊重複席那句);封鎖的不抽
F4 連線程式沒在跑 / 快照沒有席 ⇒ –(略過),不連網
(不連網:_run_nostr 換成假的)
刀(複本):K1 不挑最近有往來的 ⇒ F2 紅
"""
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
COCKPIT = HERE.parent / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_selftest_friend", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

A = NK.npub(NK.pubkey_hex("%064x" % 0x6101))
B = NK.npub(NK.pubkey_hex("%064x" % 0x6102))


class Friend(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-stfriend-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib")}
        self.root = CB.bridge_root(self.env)
        self.calls = []
        self.answer = (0, {"ok": True})
        orig = CB._run_nostr
        self.addCleanup(setattr, CB, "_run_nostr", orig)
        CB._run_nostr = lambda op, root, env, timeout=0, extra=(): (self.calls.append(list(extra)), self.answer)[1]

    def peer(self, owner, name, seat, at):
        GF.note_peer(self.root, owner, name, "TW", {"cabin": "754c", "seat": seat, "npub": "npub1x", "name": None,
                                                    "role": "root"} if seat else None, now=at)

    def test_f1_no_friends(self):
        self.assertEqual(CB._selftest_friend(self.env, "npub1me", True), [])

    def test_f2_f3_pick_recent(self):
        CR.add_friend(A, env=self.env)
        CR.add_friend(B, env=self.env)
        self.peer(A, "Ann", "aaaa1", 100)
        self.peer(B, "Bob", "bbbb2", 200)
        self.assertEqual(CB._selftest_friend(self.env, "npub1me", True), [("friend", True, {"name": "Bob", "why": "-"})])
        self.assertEqual(self.calls, [[B + "/754c/bbbb2"]])
        self.answer = (2, {"ok": False, "why": "no_seat", "hint": GF.DUPLICATE_SEAT_HINT})
        CR.add_friend(B, env=self.env, kind="blocks")
        (_k, ok, p), = CB._selftest_friend(self.env, "npub1me", True)
        self.assertFalse(ok)
        self.assertTrue(p["why"].startswith("no_seat · "), p)
        self.assertEqual(p["name"], "Ann")                          # 封鎖的 Bob 不抽(封鎖時已從好友名單拿掉)

    def test_f4_skip(self):
        CR.add_friend(A, env=self.env)
        self.peer(A, "Ann", "aaaa1", 100)
        self.assertEqual(CB._selftest_friend(self.env, "npub1me", False), [("friend", None, {"name": "Ann"})])
        CR.add_friend(B, env=self.env)
        self.peer(B, "Bob", None, 300)                              # 快照沒有席
        self.assertEqual(CB._selftest_friend(self.env, "npub1me", True), [("friend", None, {"name": "Bob"})])
        self.assertEqual(self.calls, [])


class Knife(unittest.TestCase):
    def test_k1_not_most_recent(self):
        name = Path(__file__).name
        with knife_copy(HERE.parent, "src/cockpit/chat_bridge.py", "    pick = max(friends, key=", "    pick = min(friends, key=",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Friend"], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
