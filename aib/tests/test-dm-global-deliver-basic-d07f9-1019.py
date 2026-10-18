#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球來信不受佇列授權限制(Andy 10-19 裁)。

10-19 實機(Pro / Mini / Pro 13,三台都是基本版):全球信一律先排佇列,但佇列是進階功能 ⇒ 排空器
admit 不准就整個不跑,全球信永遠停在 queued、AI 框收不到。

G1 沒開佇列 + 有記號:排空器只投全球來信(照樣先問忙不忙);本機寄件人的 queued 不動
G2 沒有記號 ⇒ 排空器不跑(右欄每拍不白跑);note_global_pending ⇒ drain_wanted 為真;投完記號自己拿掉
G3 收件席忙 ⇒ 不投、記號留著(下一拍再試)
G4 收件程式(global_inbox.import_once)排進一封 queued 全球信 ⇒ 留記號;待審(陌生)的不留
刀(複本):K1 next_to_drain 不濾全球 ⇒ G1 紅;K2 收件不留記號 ⇒ G4 紅
全程只寫暫存狀態根,不碰真佇列。
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import dm_global_files as GF  # noqa: E402
import dm_queue as Q  # noqa: E402
import global_inbox as GI  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NPUB = "npub1" + "p" * 58
ADDR = "npub1" + "q" * 58 + "/6ddf/abb89"
SEAT = "4345c"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-gdb-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        cabin = self.td / "instances" / "20261018-120332-5a97"
        cabin.mkdir(parents=True)
        # 基本版:沒有進階授權、艙裡沒有佇列開關檔(實機三台都是這樣)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.td), "HOME": str(self.td),
                    "EZ2_AIB_INSTANCE_HOME": str(cabin), "EZ2_AIB_INSTANCE": cabin.name}
        self.assertFalse(Q.admit(self.env)[0], "前提:這個艙沒開佇列")
        self.delivered = []

    def put_global(self, text="全球你好"):
        g = {"addr": ADDR, "shown": "Andy", "verified": True, "same_name": False, "msgid": GF.new_msgid()}
        return Q.enqueue(SEAT, text, "Andy", sender_kind="external_seat", sender_origin="nostr:" + NPUB,
                         global_info=g, env=self.env)

    def put_local(self):
        return Q.enqueue(SEAT, "本機的信", "@abb89", sender_kind="seat", sender_seat="abb89", env=self.env)

    def drain(self, busy=False):
        return Q.drain_once(env=self.env, hold_fn=lambda it: (busy, "busy" if busy else "idle"),
                            deliver_fn=lambda it: self.delivered.append(it["id"]) or 0)

    def status(self, iid):
        return Q._load_item(SEAT, iid, self.env)["status"]


class Drain(Base):
    def test_g1_global_only_when_queue_off(self):
        loc = self.put_local()
        glo = self.put_global()
        Q.note_global_pending(self.env)
        sent = self.drain()
        self.assertEqual([i["id"] for i in sent], [glo["id"]], "只投全球來信")
        self.assertEqual(self.delivered, [glo["id"]])
        self.assertEqual(self.status(loc["id"]), "queued", "本機寄件人的信不受影響")

    def test_g2_marker_gates_and_clears(self):
        glo = self.put_global()
        self.assertFalse(Q.drain_wanted(self.env))
        self.assertEqual(self.drain(), [], "沒記號 ⇒ 不跑")
        self.assertEqual(self.status(glo["id"]), "queued")
        Q.note_global_pending(self.env)
        self.assertTrue(Q.drain_wanted(self.env))
        self.assertEqual(len(self.drain()), 1)
        self.drain()                                      # 下一拍:沒信在等 ⇒ 記號拿掉
        self.assertFalse(Q.global_pending_path(self.env).exists())
        self.assertFalse(Q.drain_wanted(self.env))

    def test_g3_busy_keeps_marker(self):
        glo = self.put_global()
        Q.note_global_pending(self.env)
        self.assertEqual(self.drain(busy=True), [])
        self.assertEqual(self.status(glo["id"]), "queued")
        self.assertTrue(Q.global_pending_path(self.env).exists(), "還有信在等 ⇒ 記號留著")


class Inbox(Base):
    def rec(self, verified, cls):
        return {"v": 1, "msgid": GF.new_msgid(), "received_at": int(time.time()), "to_cabin": "5a97", "to_seat": SEAT,
                "from_npub": NK.npub(NK.pubkey_hex("%064x" % 21)),
                "from_addr": NK.npub(NK.pubkey_hex("%064x" % 22)) + "/931e/bbbbb",
                "verified": verified, "class": cls, "content": "外面的信", "display_name": "Bob"}

    def test_g4_import_marks_pending(self):
        root = self.td / "bridge"
        GF.ensure_dirs(root)
        GF.write_inbox(root, self.rec(False, "stranger"))
        GI.import_once(root, self.env)
        self.assertFalse(Q.global_pending_path(self.env).exists(), "待審的陌生信不留記號")
        GF.write_inbox(root, self.rec(True, "friend"))
        out = GI.import_once(root, self.env)
        self.assertEqual(out[0]["result"], "queued", out)
        self.assertTrue(Q.global_pending_path(self.env).exists())


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=200)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_global_filter(self):
        self._knife("src/cockpit/dm_queue.py", "             and (not global_only or _is_global_item(i))]", "]",
                    "Drain.test_g1_global_only_when_queue_off")

    def test_k2_inbox_no_marker(self):
        self._knife("src/cockpit/global_inbox.py", "            DQ.note_global_pending(env)", "            pass",
                    "Inbox.test_g4_import_marks_pending")


if __name__ == "__main__":
    unittest.main()
