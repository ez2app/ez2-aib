#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談「已讀」(開發文件第 10 節;第 1 期 1.8)。

規則:收件席讀了(read_at)或直接結案(done)才算已讀;系統代標的不算;還在待審的讀不到也就不送。
同一寄件人、同一收件席,AIB_CHAT_READ_BATCH 秒(預設 30)內讀的合成一張「已讀」回條(多個 aib-ref);不經 AI。
寄件端:每個 aib-ref 都要是「我寄給這個 npub 的信」才記 read。

核心(一定跑):
R1 席讀 ⇒ delivery read;delivered 直接 done ⇒ 也是 read;系統代標(通知組)⇒ 不寫
R2 ClaudeBot 交信:全球信照「全球抬頭 + 外框短碼 + 正文」標已讀;短碼不對不標
連 Nostr(要 AIB_TEST_NOSTR_VENV):
R3 同主人 X 連寄兩則給 Y,都送進框後 Y 讀兩則 ⇒ X 兩則都記 read,而且 Y 只寄出一張已讀回條(一批一次)
刀(複本):K1 拿掉 done ⇒ read 的對應 ⇒ R1 紅;K2 已讀不合併(一則一張)⇒ R3 紅(有 nostr-sdk 才跑)
"""
import importlib.util
import json
import subprocess
import sys
import time
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
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_t", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB


class Core(RT.CoreBox):
    def delivered_item(self, **kw):
        rec = RT.inbox_rec(self.seat, verified=True, cls="own", **kw)
        iid = self.imp(rec)[0]["id"]
        self.DQ._transition(self.seat, iid, "claimed", "system", env=self.env)
        self.DQ._transition(self.seat, iid, "delivered", "system", env=self.env)
        return rec, iid

    def test_r1_read_and_done(self):
        rec, iid = self.delivered_item()
        self.DQ._transition(self.seat, iid, "read", "seat:" + self.seat, env=self.env)
        self.assertEqual(self.delivery(rec["msgid"])[-1], "read")
        rec2, iid2 = self.delivered_item()
        r = self.DQ._transition(self.seat, iid2, "done", "seat:" + self.seat, env=self.env, why="回過了")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.delivery(rec2["msgid"])[-1], "read")
        rec3, iid3 = self.delivered_item()
        self.DQ._transition(self.seat, iid3, "read", "system", env=self.env, why=self.DQ.AUTO_READ_WHY)
        self.assertNotIn("read", self.delivery(rec3["msgid"]), "系統代標不算已讀")

    def test_r2_claudebot_marks_global_read(self):
        import claudebot_mailbox as CB
        import dm_global_header as G
        rec, iid = self.delivered_item(content="這是一封全球來信")
        code = self.item(iid)["global"]["reply_code"]
        head = G.sender_side("Bob", True) + " >> IP:100.1.1.1/6ddf/收件席@%s 10-16 01:30(台北時間) " % self.seat
        wrong = head + G.frame("zzzzz") + " 這是一封全球來信"
        self.assertEqual(CB.mark_queue_read(self.seat, [{"text": wrong}], env=self.env), 0)
        right = head + G.frame(code) + " 這是一封全球來信"
        self.assertEqual(CB.mark_queue_read(self.seat, [{"text": right}], env=self.env), 1)
        self.assertEqual(self.item(iid)["status"], "read")
        self.assertEqual(self.delivery(rec["msgid"])[-1], "read")


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
class Network(unittest.TestCase):
    box_extra = {"AIB_CHAT_READ_BATCH": "2"}      # 測試把「一批」縮成 2 秒
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items
    final = RT.Network.final

    def test_r3_read_batched(self):
        from aib_fake_relay import RelayClient
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True, True])
        recs = []
        for n in (1, 2):
            rec = SB.outbox_rec("%s/6ddf/%s" % (a.owner, y), x, content="第 %d 則" % n)
            res = GF.send_and_wait(a.root, rec, wait=20)
            self.assertEqual(res["stage"], "queued", res)
            recs.append(rec)
        for it in self.items(a, y):
            self.DQ._transition(y, it["id"], "claimed", "system", env=a.qenv)
            self.DQ._transition(y, it["id"], "delivered", "system", env=a.qenv)
        for it in self.items(a, y):
            self.DQ._transition(y, it["id"], "read", "seat:" + y, env=a.qenv)
        for rec in recs:
            self.assertTrue(self.final(a, rec["msgid"], "read", 40), GF.read_receipts(a.root, rec["msgid"]))
        # 數 Y 寄給 X 的回條:同一批讀的只准一張「已讀」
        x_npub = next(e["npub"] for e in NK.read_map(a.env())["seats"] if e["seat5"] == x)
        c = RelayClient(self.relay.url)
        c.send(["REQ", "r", {"kinds": [1059], "#p": [NK.npub_to_hex(x_npub)]}])
        wraps = c.recv_until_eose("r")
        x_stem = next(e["key"] for e in NK.read_map(a.env())["seats"] if e["seat5"] == x)
        req = json.dumps({"secret": NK.load_secret(x_stem, a.env()), "events": wraps})
        r = subprocess.run([str(Path(SB.NOSTR_VENV) / "bin" / "python"), "-c", SB.UNWRAP], input=req,
                           capture_output=True, text=True, timeout=60)
        got = json.loads(r.stdout)
        reads = [g for g in got if ["aib-receipt", "read"] in g.get("tags", [])]
        self.assertEqual(len(reads), 1, "同一批讀的要合成一張已讀回條")
        self.assertEqual(sorted(t[1] for t in reads[0]["tags"] if t[0] == "aib-ref"), sorted(r["msgid"] for r in recs))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_done_not_read(self):
        self._knife("src/cockpit/dm_queue.py", '    if to == "done" and frm == "delivered" and cls == "seat":',
                    "    if False:", "Core.test_r1_read_and_done")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_no_batching(self):
        self._knife("src/cockpit/chat_nostr.py", '            ok = await self.send_receipt(b["refs"][0], "read", refs=b["refs"])',
                    '            ok = all([await self.send_receipt(r, "read", refs=[r]) for r in b["refs"]])',
                    "Network.test_r3_read_batched")


if __name__ == "__main__":
    unittest.main()
