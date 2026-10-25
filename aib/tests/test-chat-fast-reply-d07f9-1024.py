#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""回我剛寄的信當好友收(10-24 回音小幫手真站實測:對方秒回比「寄出成功 ⇒ 記成好友」還早到 ⇒ 被當陌生拒收)。

#306 第 1 輪審查(A-L11):只放寬「那封還在寄」(寄出帳寫了、寄完才記好友)的那段;寄完之後還是陌生人 = 被刪 / 封鎖 ⇒ 不放寬;
回信要回到原寄件席。
U1 (venv 單元)_replies_my_sent:還在寄 + 簽的是我寄給的那席 + 回到原寄件席 + 10 分內 ⇒ True;
   寄完了(sending/ 沒了)/ 換席 / 別人簽 / 超過 10 分 / 不是 msgid ⇒ False
R1 (假中繼站)Andy 寄給 Bob、寄完後移掉 Bob;Bob 帶 reply_to = 那封回信 ⇒ 拒收(移除過的不放寬)
R2 不帶 reply_to ⇒ 照舊拒收(not_friend)
刀(複本):K1 拿掉這條 ⇒ U1 紅;K2 不看「還在寄」⇒ R1 紅;K3 不看原寄件席 ⇒ U1 紅
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
VPY = str(Path(os.environ.get("AIB_TEST_NOSTR_VENV") or "/nonexistent") / "bin" / "python")

PROBE = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
from pathlib import Path
root = Path(sys.argv[2])
r = object.__new__(CN.Receiver)
r.root = root
import dm_global_files as GF
MID, NP = GF.new_msgid(), "npub1bob"
def put(**kw):
    (root / "sent").mkdir(exist_ok=True)
    (root / "sent" / (MID + ".json")).write_text(json.dumps(dict({"v": 1, "to": NP, "at": int(time.time()), "op": "send",
                                                                  "seat": "xxxx11"}, **kw)))
(root / "sending").mkdir()
(root / "sending" / (MID + ".json")).write_text("{}")
out = {}
put()
out["ok"] = r._replies_my_sent(MID, NP, "xxxx11")
out["other_seat"] = r._replies_my_sent(MID, NP, "zzzz99")
out["other_npub"] = r._replies_my_sent(MID, "npub1eve", "xxxx11")
out["bad_ref"] = r._replies_my_sent("../x", NP, "xxxx11")
put(at=int(time.time()) - 700)
out["old"] = r._replies_my_sent(MID, NP, "xxxx11")
put()
(root / "sending" / (MID + ".json")).unlink()
out["done"] = r._replies_my_sent(MID, NP, "xxxx11")
print(json.dumps(out))
"""


def probe():
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([VPY, "-c", PROBE, str(REPO / "src" / "cockpit"), td], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
class Unit(unittest.TestCase):
    def test_u1_window(self):
        self.assertEqual(probe(), {"ok": True, "other_seat": False, "other_npub": False, "bad_ref": False,
                                   "old": False, "done": False})


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
        self.assertEqual(json.loads((a.root / "sent" / (mid + ".json")).read_text())["seat"], x, "寄出帳記寄件席")
        to = "%s/6ddf/%s" % (a.owner, x)
        rec = SB.outbox_rec(to, y, reply_to=mid)
        GF.send_and_wait(b.root, rec, wait=20)
        stages = lambda: [r["stage"] for r in GF.read_receipts(b.root, rec["msgid"])]  # noqa: E731
        self.assertTrue(b.wait_for(lambda: "refused" in stages(), 30), "寄完後移除過 ⇒ 不放寬 %r" % (stages(),))
        rec = SB.outbox_rec(to, y)
        GF.send_and_wait(b.root, rec, wait=20)
        # 同一寄件人的拒收回條有限量(第 27.4 節)⇒ 看 Andy 的稽核行
        seen = lambda: "".join(p.read_text() for p in (a.root / "audit").glob("*.jsonl"))  # noqa: E731
        self.assertTrue(a.wait_for(lambda: '"msgid": "%s"' % rec["msgid"] in seen(), 30))
        self.assertIn('"msgid": "%s", "peer": ' % rec["msgid"], seen())
        line = [ln for ln in seen().splitlines() if rec["msgid"] in ln and '"dir": "in"' in ln]
        self.assertTrue(line and "refused:not_friend" in line[0], line)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, which):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", needle, repl,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_reply_rule(self):
        self._knife("    def _replies_my_sent(self, ref, from_npub, to_seat):\n",
                    "    def _replies_my_sent(self, ref, from_npub, to_seat):\n        return False\n", "Unit.test_u1_window")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_ignore_sending(self):
        self._knife(' or not (self.root / "sending" / (ref + ".json")).exists():', ":", "Network.test_r1_r2_reply")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_ignore_seat(self):
        self._knife(' and sent.get("seat") == to_seat and ', " and ", "Unit.test_u1_window")


if __name__ == "__main__":
    unittest.main()
