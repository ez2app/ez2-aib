#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""加好友邀請(10-24 Andy「全照做」清單第 11 條):A 產碼 ⇒ B invite use ⇒ A 的轉接程式自動同意、雙方互為好友。

I1 create_invite / take_invite:碼 10 碼 base32;用一次就沒;過期不收;亂碼不收;只留 20 組
I2 aib chat invite --json:沒有公開席 ⇒ rc 2;有 ⇒ addr / code / uri(aib:join?to=…&code=…)/ command
I3 aib chat invite use:格式不對 ⇒ rc 2、不加好友;整串 uri 也認得(invite use 要寄信,寄件那段在 I4 / 端到端驗)
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網)
I4 on_request:驗得到的陌生人帶 invite:<對得上的碼> ⇒ 加成好友(source=invite)、回 delivered、不進申請清單;
   碼用過 / 亂碼 ⇒ 照舊進申請清單、回 held;封鎖的人帶對的碼 ⇒ 不加好友
10-24 晚 Andy「位址太長、照 10 碼做」⇒ 碼放中繼站(d=aib-invite:<PBKDF2 雜湊>),對方只打碼:
I5 碼的寫法:小寫、中間一個 - 或空白、0/1/8 打成 O/I/B 都認;不像碼 ⇒ None;雜湊 32 位十六進位、碼不同雜湊不同
I6 _publish_invite(換掉 _run_nostr):中繼站上已有同一個雜湊 ⇒ 換一組再發;沒 venv ⇒ 不放(碼照樣能配位址用)
I7 _resolve_invite:兩個簽章者 ⇒ clash;最新那筆撤了 / 過期 ⇒ used;簽章者不是位址主人 ⇒ wrong_signer;
   正常 ⇒ 位址 + 印對方名字(控制字元清掉);invite use <只有碼> 查不到 ⇒ rc 2、不加好友
E1 本機假中繼站 + 兩台真轉接程式:Andy invite ⇒ Bob invite use <碼(小寫)> ⇒ 兩邊互為好友;
   中繼站上那筆換成 revoked;同一組碼再用 ⇒ rc 2
刀(複本):K1 take_invite 不扣次數 ⇒ I1 紅;K2 on_request 不看碼對不對 ⇒ I4 紅;K3 不擋撞號 ⇒ I7 紅
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_invite", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import chat_roster as CR
import dm_global_files as GF
import nostr_keys as NK
from pathlib import Path
root, case = Path(sys.argv[2]), sys.argv[3]
B = NK.npub(NK.pubkey_hex("%064x" % 0x8102))
code = GF.create_invite(root)["code"]
if case == "used":
    GF.take_invite(root, code)
if case == "blocked":
    CR.add_friend(B, kind="blocks")
note = "invite:" + ("A" * 10 if case == "junk" else code)
r = object.__new__(CN.Receiver)
r.root = root
sent = []
async def send_receipt(msgid, stage, **k):
    sent.append(stage)
r.send_receipt = send_receipt
rec = {"class": "stranger", "verified": True, "content": note, "display_name": "Bob", "country": "TW",
       "from_addr": B + "/754c/bbbb2"}
peer = {"seat": {"npub": "npub1x", "seat": "bbbb2"}, "cabin": "754c", "display_name": "Bob"}
asyncio.run(r.on_request(rec, {"owner": B, "cabin": "754c", "seat": "bbbb2"}, peer, "m" * 32, "npub1x"))
fr = CR.read_friends()
print(json.dumps({"sent": sent, "friends": [(f["owner"] == B, f.get("source")) for f in fr],
                  "requests": len(GF.read_requests(root))}))
'''


class Store(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-invite-"))
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_i1_take_once(self):
        inv = GF.create_invite(self.td, now=1000)
        self.assertRegex(inv["code"], r"^[A-Z2-7]{10}$")
        self.assertEqual((inv["expires_at"], inv["uses"]), (1000 + GF.INVITE_TTL_S, 1))
        self.assertFalse(GF.take_invite(self.td, "AAAAAAAAAA", now=1001))
        self.assertFalse(GF.take_invite(self.td, "bad", now=1001))
        self.assertTrue(GF.take_invite(self.td, inv["code"], now=1001))
        self.assertFalse(GF.take_invite(self.td, inv["code"], now=1002))            # 用過了
        old = GF.create_invite(self.td, now=1000)
        self.assertFalse(GF.take_invite(self.td, old["code"], now=1000 + GF.INVITE_TTL_S))   # 過期
        for i in range(25):
            GF.create_invite(self.td, now=2000 + i)
        rows = json.loads((self.td / "invites.json").read_text())["invites"]
        self.assertEqual(len(rows), 20)


class Cli(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-invite-cli-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib")}

    def cli(self, *args):
        buf = io.StringIO()
        rc = CB.main(list(args), env=self.env, out=buf)
        return rc, buf.getvalue()

    def test_i2_invite(self):
        self.assertEqual(self.cli("invite", "--json")[0], 2)
        owner = NK.ensure_owner(self.env)
        CR.set_profile("A", "TW", self.env)
        CR.set_public("cfd04", True, self.env)
        orig = NK.sending_seats
        self.addCleanup(setattr, NK, "sending_seats", orig)
        NK.sending_seats = lambda env=None, live=None: [{"seat5": "cfd04", "cabin": "6ddf", "npub": "npub1a", "key": "k"}]
        rc, out = self.cli("invite", "--json")
        self.assertEqual(rc, 0, out)
        d = json.loads(out)
        self.assertEqual(d["addr"], owner + "/6ddf/cfd04")
        self.assertEqual(d["uri"], "aib:join?to=%s&code=%s" % (d["addr"], d["code"]))
        self.assertEqual(d["command"], "aib chat invite use %s %s" % (d["addr"], d["code"]))
        self.assertTrue(GF.take_invite(CB.bridge_root(self.env), d["code"]))
        self.assertIsNone(d["short"], "沒 venv ⇒ 沒放中繼站,不給只打碼的指令")

    def test_i3_join_bad(self):
        other = NK.npub(NK.pubkey_hex("%064x" % 0x8101))
        for args in (("invite", "use", other + "/6ddf/cfd04"), ("invite", "use", other + "/6ddf/cfd04", "short"),
                     ("invite", "use", "x", "AAAAAAAAAA"), ("invite", "use", "aib:join?to=%s/6ddf/cfd04&code=bad" % other)):
            self.assertEqual(self.cli(*args)[0], 2, args)
        self.assertEqual(CR.read_friends(self.env), [])
        self.assertEqual(CB.JOIN_URI_RE.match("aib:join?to=%s/6ddf/cfd04&code=AAAAAAAAAA" % other).group(2), "AAAAAAAAAA")


A1 = NK.npub(NK.pubkey_hex("%064x" % 0x8201))
C1 = NK.npub(NK.pubkey_hex("%064x" % 0x8203))


class Short(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-invite-s-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib"), "EZ2_AIB_LOCALE": "zh-TW"}
        spec = importlib.util.spec_from_file_location("chat_bridge_invite_s", str(COCKPIT / "chat_bridge.py"))
        self.CB = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.CB)
        self.calls, self.fetches = [], []
        self.addr = A1 + "/6ddf/abcde"
        self.look = {"ok": True, "owner": A1, "device": A1, "display_name": "Bob\x1b[31m", "country": "TW"}

        def fake(cmd, root, env, timeout=60, extra=(), stdin=None):
            self.calls.append(cmd)
            if cmd == "invite-fetch":
                return 0, {"ok": True, "hits": self.fetches.pop(0) if self.fetches else []}
            if cmd == "invite-publish":
                self.published = stdin
                return 0, {"ok": True}
            return 0, self.look
        self.CB._run_nostr = fake

    def body(self, author=None, at=1, **kw):
        b = {"v": 1, "addr": self.addr, "exp": int(time.time()) + 600}
        b.update(kw)
        return {"author": author or A1, "at": at, "body": b}

    def test_i5_code_shapes(self):
        c = GF.create_invite(self.td)["code"]
        self.assertEqual(GF.invite_norm(GF.invite_fmt(c)), c)
        self.assertEqual(GF.invite_norm(" abcde 1o8z2 "), "ABCDEIOBZ2", "小寫、空白、1/0/8")
        for bad in ("", "ABCDE-FGHI", "ABCDE-FGHIJK", "ABCDE--FGHIJ", "ABCDE-FGHI9", None, A1):
            self.assertIsNone(GF.invite_norm(bad), bad)
        self.assertRegex(GF.invite_hash("ABCDEFGHIJ"), r"^[0-9a-f]{32}$")
        self.assertNotEqual(GF.invite_hash("ABCDEFGHIJ"), GF.invite_hash("ABCDEFGHIK"))

    def test_i6_publish_precheck(self):
        root = self.td / "bridge"
        GF.ensure_dirs(root)
        self.fetches = [[self.body(author=C1)]]                    # 第一組撞了
        inv, short = self.CB._publish_invite(root, self.env, self.addr)
        self.assertTrue(short)
        self.assertEqual(self.calls, ["invite-fetch", "invite-fetch", "invite-publish"], "撞了要換一組再查再發")
        self.assertEqual(self.published["hash"], GF.invite_hash(inv["code"]))
        self.assertEqual(self.published["addr"], self.addr)
        self.CB._run_nostr = lambda *a, **k: (None, {})
        inv, short = self.CB._publish_invite(root, self.env, self.addr)
        self.assertFalse(short, "沒 venv ⇒ 不放")
        self.assertTrue(GF.take_invite(root, inv["code"]), "碼照樣記在本機")

    def test_i7_resolve(self):
        res = lambda: self.CB._resolve_invite("ABCDEFGHIJ", self.env, io.StringIO())  # noqa: E731
        self.assertEqual(res(), (None, "not_found"))
        self.fetches = [[self.body(), self.body(author=C1, at=2)]]
        self.assertEqual(res(), (None, "clash"), "同一組碼兩個簽章者 ⇒ 不猜")
        self.fetches = [[self.body(at=1), {"author": A1, "at": 2, "body": {"v": 1, "revoked": True}}]]
        self.assertEqual(res(), (None, "used"))
        self.fetches = [[self.body(exp=int(time.time()) - 1)]]
        self.assertEqual(res(), (None, "used"), "過期")
        self.fetches = [[self.body()]]
        self.look = dict(self.look, owner=C1, device=C1)
        self.assertEqual(res(), (None, "wrong_signer"))
        self.look = dict(self.look, owner=A1, device=A1)
        self.fetches = [[self.body()]]
        buf = io.StringIO()
        self.assertEqual(self.CB._resolve_invite("ABCDEFGHIJ", self.env, buf), (self.addr, None))
        self.assertIn("Bob", buf.getvalue())
        self.assertNotIn("\x1b", buf.getvalue(), "對方名字要清控制字元")
        out = io.StringIO()
        self.assertEqual(self.CB.main(["invite", "use", "abcde-fghij"], env=self.env, out=out), 2)
        self.assertIn("找不到", out.getvalue())
        self.assertEqual(CR.read_friends(self.env), [])


_rspec = importlib.util.spec_from_file_location("chat_recv_invite", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(RT)


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_e1_code_only(self):
        a, _x = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, _y = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        r = a.cli("invite", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(d["short"], d)
        code = GF.invite_fmt(d["code"]).lower()
        r = b.cli("invite", "use", code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Andy", r.stdout)
        self.assertTrue(CR.is_friend(a.owner, b.env()))
        self.assertTrue(a.wait_for(lambda: CR.is_friend(b.owner, a.env()), 30), "Andy 那邊沒自動加 Bob")
        h = GF.invite_hash(d["code"])
        revoked = lambda: any(e["kind"] == 30078 and ["d", "aib-invite:" + h] in [t[:2] for t in e["tags"]]  # noqa: E731
                              and json.loads(e["content"]).get("revoked") for e in self.relay.events)
        self.assertTrue(a.wait_for(revoked, 20), "用掉的碼要在中繼站撤掉")
        r = b.cli("invite", "use", code)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Accept(unittest.TestCase):
    def probe(self, case):
        td = Path(tempfile.mkdtemp(prefix="aib-invite-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(td / "root"), case], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_i4_auto_accept(self):
        self.assertEqual(self.probe("ok"), {"sent": ["delivered"], "friends": [[True, "invite"]], "requests": 0})
        for case in ("used", "junk"):
            self.assertEqual(self.probe(case), {"sent": ["held"], "friends": [], "requests": 1}, case)
        self.assertEqual(self.probe("blocked")["friends"], [])


class Knife(unittest.TestCase):
    def _run(self, rel, anchor, repl, which):
        name = Path(__file__).name
        with knife_copy(REPO, rel, anchor, repl,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_no_decrement(self):
        self._run("src/cockpit/dm_global_files.py", '                r["uses"] = int(r["uses"]) - 1\n', "                pass\n",
                  "Store")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_any_code(self):
        self._run("src/cockpit/chat_nostr.py", "and GF.take_invite(self.root, m.group(1)):", "and True:", "Accept")

    def test_k3_no_clash_check(self):
        self._run("src/cockpit/chat_bridge.py", '    if len({x.get("author") for x in hits}) > 1:\n', "    if False:\n",
                  "Short.test_i7_resolve")


if __name__ == "__main__":
    unittest.main()
