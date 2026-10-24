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
刀(複本):K1 take_invite 不扣次數 ⇒ I1 紅;K2 on_request 不看碼對不對 ⇒ I4 紅
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
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

    def test_i3_join_bad(self):
        other = NK.npub(NK.pubkey_hex("%064x" % 0x8101))
        for args in (("invite", "use", other + "/6ddf/cfd04"), ("invite", "use", other + "/6ddf/cfd04", "short"),
                     ("invite", "use", "x", "AAAAAAAAAA"), ("invite", "use", "aib:join?to=%s/6ddf/cfd04&code=bad" % other)):
            self.assertEqual(self.cli(*args)[0], 2, args)
        self.assertEqual(CR.read_friends(self.env), [])
        self.assertEqual(CB.JOIN_URI_RE.match("aib:join?to=%s/6ddf/cfd04&code=AAAAAAAAAA" % other).group(2), "AAAAAAAAAA")


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
        with knife_copy(REPO, rel, anchor, repl, tests=(name,)) as root:
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


if __name__ == "__main__":
    unittest.main()
