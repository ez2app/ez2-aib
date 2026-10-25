#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""回音小幫手(10-24 Andy 選「官方回音小幫手」:常開、只回一句、不接 AI、不存內容)。

C1 aib chat echo on / off / --json:echo.json 開關(0600);不帶 = 看現在
C2 開著 ⇒ status --json echo:true、文字版醒目一行;selftest --json 有 echo 一列(ok null,不算錯)(#306 審 A-M10)
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
E1 小幫手開著;Andy(不是好友)寄一封 ⇒ Andy 收到 queued 回條 + 一封「收到了」(沒帶語言 ⇒ 照國別回中文)
   Andy 那邊那封待審、原因 echo_reply(只進艦長室,不交給 AI;#306 審 B-M1)
   小幫手那台:沒有 inbox、沒把 Andy 記成好友 / 通訊錄、沒留補寄檔、回音沒寫 sent/ 與回條(B-M3);稽核 echo,不含內容
E2 10 秒內再寄一封 ⇒ 小幫手靜默(稽核 echo:quiet),Andy 只收到一封
E3 小幫手關掉 ⇒ 照舊拒收陌生人(refused not_friend)
E4 小幫手開著、寄件人是好友 ⇒ 照一般流程進佇列,不回音(A-M10)
E5 小幫手開著、陌生人送好友申請 ⇒ 照一般申請流程(不回 delivered、不回音;A-M10 / B-L4)
刀(複本):K1 拿掉 echo 分支 ⇒ E1 紅;K2 回信不帶 echo 旗標 ⇒ E1 紅;K3 好友也回音 ⇒ E4 紅;
K4 回音走一般寄件 ⇒ E1 紅(寫了 sent/);K5 收件端不把回音那句擋在艦長室 ⇒ E1 紅
"""
import importlib.util
import json
import sys
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

_spec = importlib.util.spec_from_file_location("chat_recv_echo", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB


def audit_in(b):
    out = []
    for p in sorted((b.root / "audit").glob("*.jsonl")):
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("dir") == "in":
                out.append(r)
    return out


class Cli(unittest.TestCase):
    def test_c1_switch(self):
        b = SB.Box()
        self.addCleanup(b.close)
        self.assertEqual(json.loads(b.cli("echo", "--json").stdout), {"echo": False})
        self.assertEqual(b.cli("echo", "on").returncode, 0)
        self.assertTrue(GF.echo_on(b.root))
        self.assertIn("收到了", b.cli("echo").stdout)
        self.assertEqual((b.root / "echo.json").stat().st_mode & 0o777, 0o600)
        st = json.loads(b.cli("status", "--json").stdout)
        self.assertIs(st["echo"], True)
        self.assertIn("⚠ 回音小幫手開著", b.cli("status").stdout)
        rows = json.loads(b.cli("selftest", "--json").stdout)
        self.assertIn({"item": "echo", "ok": None}, rows)
        self.assertEqual(b.cli("echo", "off").returncode, 0)
        self.assertIs(json.loads(b.cli("status", "--json").stdout)["echo"], False)
        self.assertNotIn("回音小幫手", b.cli("status").stdout)
        self.assertEqual(json.loads(b.cli("echo", "--json").stdout), {"echo": False})


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items

    def pair(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        h, (y,) = self._box("Helper", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "小幫手")], [True])
        return a, x, h, y

    def test_e1_e2_echo(self):
        a, x, h, y = self.pair()
        self.assertEqual(h.cli("echo", "on").returncode, 0)
        rec = SB.outbox_rec("%s/6ddf/%s" % (h.owner, y), x, content="測試內容 zq9")
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        got = lambda: [i for i in self.items(a, x) if "收到了" in json.dumps(i, ensure_ascii=False)]  # noqa: E731
        self.assertTrue(a.wait_for(lambda: got(), 20), self.items(a, x))
        self.assertIn("收到了,線路是通的", got()[0]["msg"], "沒帶語言 ⇒ 照對方國別(TW)回中文")
        self.assertEqual((got()[0]["status"], self.DQ.hold_why_key(got()[0])), (self.DQ.HOLD_STATUS, "echo_reply"),
                         "回音那句只進艦長室、不交給 AI")
        self.assertEqual([p.name for p in (h.root / "sent").glob("*.json")] if (h.root / "sent").exists() else [], [],
                         "回音不寫寄出帳")
        self.assertEqual([p.name for p in (h.root / "receipts").glob("*.json")] if (h.root / "receipts").exists() else [],
                         [], "回音不寫回條")
        self.assertEqual(list((h.root / "inbox").glob("*.json")), [], "小幫手不存內容")
        self.assertEqual(self.items(h, y), [], "小幫手不進佇列 / AI")
        self.assertEqual(json.loads(h.cli("friends", "--json").stdout)["friends"], [], "小幫手不把人記成好友")
        self.assertFalse(list((h.root / "resend").glob("*.json")) if (h.root / "resend").exists() else [], "回音不補寄")
        self.assertTrue(any(r["result"] == "echo" for r in audit_in(h)), audit_in(h))
        self.assertNotIn("zq9", "".join(p.read_text() for p in (h.root / "audit").glob("*.jsonl")))
        # E2:10 秒內第二封 ⇒ 靜默
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (h.owner, y), x), wait=5)
        self.assertTrue(h.wait_for(lambda: any(r["result"] == "echo:quiet" for r in audit_in(h)), 15), audit_in(h))
        a.wait_for(lambda: False, 3)
        self.assertEqual(len(got()), 1, "限流內只回一封")

    def test_e4_friend_normal(self):
        a, x, h, y = self.pair()
        self.assertEqual(h.cli("friend", "add", a.owner).returncode, 0)
        self.assertEqual(h.cli("echo", "on").returncode, 0)
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (h.owner, y), x, content="好友來信 k7"), wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        self.assertTrue(h.wait_for(lambda: any("k7" in json.dumps(i, ensure_ascii=False) for i in self.items(h, y)), 15),
                        self.items(h, y))
        self.assertFalse(any(r["result"].startswith("echo") for r in audit_in(h)), audit_in(h))

    def test_e5_request_normal(self):
        a, x, h, y = self.pair()
        self.assertEqual(h.cli("echo", "on").returncode, 0)
        rec = SB.outbox_rec("%s/6ddf/%s" % (h.owner, y), x)
        rec["op"] = "request"
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertNotEqual(res["stage"], "delivered", res)
        h.wait_for(lambda: False, 3)
        self.assertFalse(any(r["result"].startswith("echo") for r in audit_in(h)), audit_in(h))
        self.assertEqual(json.loads(h.cli("friends", "--json").stdout)["friends"], [])

    def test_e3_off_refuses(self):
        a, x, h, y = self.pair()
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (h.owner, y), x), wait=20)
        self.assertEqual(res["stage"], "refused", res)
        self.assertIn("好友", res["detail"])
        self.assertFalse(any(r["result"].startswith("echo") for r in audit_in(h)), audit_in(h))


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Network.test_e1_e2_echo"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_echo_branch(self):
        self._knife('        if rec["class"] == "stranger" and GF.echo_on(self.root):', "        if False:")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_reply_without_flag(self):
        self._knife('"op": "send", "echo": True,', '"op": "send",')

    def _knife_t(self, needle, replacement, test, path="src/cockpit/chat_nostr.py"):
        name = Path(__file__).name
        with knife_copy(REPO, path, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), test], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_friend_echoed(self):
        self._knife_t('        if rec["class"] == "stranger" and GF.echo_on(self.root):',
                      '        if rec["class"] != "own" and GF.echo_on(self.root):', "Network.test_e4_friend_normal")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k4_echo_normal_send(self):
        self._knife_t('        if rec.get("echo") and frm.get("attested"):', "        if False:", "Network.test_e1_e2_echo")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k5_echo_to_ai(self):
        self._knife_t('        echo = rec.get("echo") is True', "        echo = False", "Network.test_e1_e2_echo",
                      path="src/cockpit/global_inbox.py")


if __name__ == "__main__":
    unittest.main()
