#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""回我剛寄的信當好友收(10-24 回音小幫手真站實測:對方秒回比「寄出成功 ⇒ 記成好友」還早到 ⇒ 被當陌生拒收)。

假中繼站排不出那個先後,這裡用「寄出後把對方移出好友」模擬「還沒記成好友」:
R1 Andy 寄給 Bob 後移掉 Bob;Bob 帶 reply_to = 那封回信 ⇒ Andy 收下(queued)
R2 不帶 reply_to ⇒ 照舊拒收(not_friend)
R3 reply_to 那封超過 10 分鐘(寄出帳時間往前調)⇒ 拒收
刀(複本):K1 拿掉這條 ⇒ R1 紅
"""
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_fast", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def _sent_then_unfriend(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)
        rec = SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual(res["stage"], "queued", res)
        self.assertTrue(a.wait_for(lambda: b.owner in a.cli("friends", "--json").stdout, 10))
        self.assertEqual(a.cli("friend", "remove", b.owner).returncode, 0)
        return a, x, b, y, rec["msgid"]

    def test_r1_r2_reply(self):
        a, x, b, y, mid = self._sent_then_unfriend()
        to = "%s/6ddf/%s" % (a.owner, x)
        res = GF.send_and_wait(b.root, SB.outbox_rec(to, y, reply_to=mid), wait=20)
        self.assertEqual(res["stage"], "queued", res)
        res = GF.send_and_wait(b.root, SB.outbox_rec(to, y), wait=20)
        self.assertEqual(res["stage"], "refused", res)

    def test_r3_old_reply(self):
        a, x, b, y, mid = self._sent_then_unfriend()
        p = a.root / "sent" / (mid + ".json")
        cur = json.loads(p.read_text())
        cur["at"] = int(time.time()) - 700
        p.write_text(json.dumps(cur))
        res = GF.send_and_wait(b.root, SB.outbox_rec("%s/6ddf/%s" % (a.owner, x), y, reply_to=mid), wait=20)
        self.assertEqual(res["stage"], "refused", res)


class Knife(unittest.TestCase):
    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_reply_rule(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py",
                        'if rec["class"] == "stranger" and rec["verified"] and self._replies_my_sent(',
                        'if False and self._replies_my_sent(',
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Network.test_r1_r2_reply"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
