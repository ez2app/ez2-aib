#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""首封秒數(10-24 Andy 第 8 條「做，只存本機」):開通到第一封對話的秒數 + 卡在哪一步。

F1 note_first 只記第一次(再記不動);壞值(字串、布林、不認得的鍵)讀出來當沒有
F2 first_chat:沒開 ⇒ off;ready 沒過 ⇒ 第一格(bridge / public / contact);都過沒寄 ⇒ no_sent;
   寄了沒回條 ⇒ no_delivered;有回條 ⇒ stuck None、秒數 = 跟開通的差;開著但沒記開通時間 ⇒ stuck None
F3 note_first_on:已經有 sent / inbox / receipts 紀錄的舊用戶不補記開通時間(#303 審 B-L10)
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
N1 Andy 寄給 Bob(互為好友)⇒ Andy 的 first.json 有 sent、delivered;Bob 的有 in;內容沒進 first.json
   status --json 的 first.delivered_s 有值、文字版印首封那一行
刀(複本):K1 拿掉「收到回條記 delivered」⇒ N1 紅
"""
import importlib.util
import json
import sys
import tempfile
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

_spec = importlib.util.spec_from_file_location("chat_recv_first", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
_cb = importlib.util.spec_from_file_location("chat_bridge_first", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_cb)
_cb.loader.exec_module(CB)
OK = {"bridge": True, "public": True, "contact": True}


class Unit(unittest.TestCase):
    def test_f1_first_only(self):
        with tempfile.TemporaryDirectory() as d:
            GF.note_first(d, "on", now=100)
            GF.note_first(d, "on", now=200)
            GF.note_first(d, "nope", now=300)
            self.assertEqual(GF.read_first(d), {"on": 100})
            (Path(d) / "first.json").write_text(json.dumps({"on": "100", "sent": True, "in": 5, "x": 1}))
            self.assertEqual(GF.read_first(d), {"in": 5})
            (Path(d) / "first.json").write_text("[1]")
            self.assertEqual(GF.read_first(d), {})

    def test_f3_old_user_no_on(self):
        with tempfile.TemporaryDirectory() as d:
            GF.note_first_on(d, now=100)
            self.assertEqual(GF.read_first(d), {"on": 100})
        for sub in ("sent", "inbox", "receipts"):
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / sub).mkdir()
                (Path(d) / sub / "m1.json").write_text("{}")
                GF.note_first_on(d, now=100)
                self.assertEqual(GF.read_first(d), {}, sub)

    def test_f2_stuck(self):
        self.assertEqual(CB.first_chat({}, OK, False)["stuck"], "off")
        self.assertIsNone(CB.first_chat({}, OK, True)["stuck"], "開著但沒記開通時間(升級前的舊用戶)⇒ 不判卡住(#303 審 B-L10)")
        for miss in ("bridge", "public", "contact"):
            self.assertEqual(CB.first_chat({"on": 10}, dict(OK, **{miss: False}))["stuck"], miss)
        self.assertEqual(CB.first_chat({"on": 10}, OK)["stuck"], "no_sent")
        self.assertEqual(CB.first_chat({"on": 10, "sent": 15}, OK)["stuck"], "no_delivered")
        got = CB.first_chat({"on": 10, "sent": 15, "delivered": 18}, dict(OK, contact=False))
        self.assertEqual((got["stuck"], got["sent_s"], got["delivered_s"], got["in_s"]), (None, 5, 8, None))
        self.assertEqual(CB.first_chat({"on": 10, "delivered": 5}, OK)["delivered_s"], 0, "時鐘倒退不出負數")


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_n1_first_message(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        GF.note_first(a.root, "on", now=1)
        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)
        rec = SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        self.assertTrue(a.wait_for(lambda: "delivered" in GF.read_first(a.root), 10), GF.read_first(a.root))
        self.assertIn("sent", GF.read_first(a.root))
        self.assertTrue(b.wait_for(lambda: "in" in GF.read_first(b.root), 10), GF.read_first(b.root))
        raw = (a.root / "first.json").read_text() + (b.root / "first.json").read_text()
        self.assertNotIn(rec["content"], raw, "不准記內容")
        self.assertNotIn(y, raw)
        st = json.loads(a.cli("status", "--json").stdout)
        self.assertIsNotNone(st["first"]["delivered_s"], st["first"])
        self.assertIn("首封送達", a.cli("status").stdout)


class Knife(unittest.TestCase):
    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_delivered(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", '            GF.note_first(self.root, "delivered")\n', "",
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Network.test_n1_first_message"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
