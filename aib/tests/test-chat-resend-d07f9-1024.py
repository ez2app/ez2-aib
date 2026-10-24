#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對方上線就補寄還沒到的信(10-24 Andy「很像使用市面上的通訊 App 一樣絲滑簡易」)。

B1 keep_for_resend:只留一般信(op=send)、主人 npub 要合格;檔 0600
B2 resend_due:對方在線 ⇒ 過了間隔才放回 outbox(同一個 msgid)、次數 +1;間隔內不再放;別人的信不動;
   已在 outbox / sending 不重放
B3 對方那台已回 queued / delivered / read / 拒收 ⇒ 刪掉不補;補滿 RESEND_MAX 次或留超過 7 天 ⇒ 刪掉
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網)
B4 收到好友的在線狀態 ⇒ 寄件端記下要補寄這位(resend_owners)
刀(複本):K1 resend_due 不看回條 ⇒ B3 紅;K2 不看間隔 ⇒ B2 紅
"""
import json
import os
import shutil
import stat
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
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

A = NK.npub(NK.pubkey_hex("%064x" % 0xA101))
B = NK.npub(NK.pubkey_hex("%064x" % 0xA102))

PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import nostr_keys as NK
from pathlib import Path
root = Path(sys.argv[2])
F = NK.npub(NK.pubkey_hex("%064x" % 0xA103))
CN.CR.is_blocked = lambda o, *a, **k: False
CN.CR.is_friend = lambda o, *a, **k: o == F
CN.CR.is_mine = lambda o, *a, **k: False
s = object.__new__(CN.Sender)
s.root, s.cache = root, {}
async def resolve(addr): return 0, {"seat": {"npub": "npub1f"}}
s._resolve = resolve
r = object.__new__(CN.Receiver)
r.root, r.sender = root, s
class Rumor:
    def content(self): return json.dumps({"v": 1, "seats": [{"seat": "f0001", "cabin": "c1", "state": "online"}]})
asyncio.run(r.on_presence(Rumor(), {"aib-from": F + "/c1/f0001"}, "npub1f"))
print(json.dumps({"due": sorted(getattr(s, "resend_owners", set())) == [F]}))
'''


def rec(msgid, to_owner, op="send"):
    return {"v": GF.VERSION, "msgid": msgid, "created_at": 1000, "op": op,
            "from": {"cabin": "6ddf", "seat": "d07f9", "attested": True}, "to": to_owner + "/c1/f0001", "content": "hi"}


class Resend(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-resend-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        GF.ensure_dirs(self.td)

    def test_b1_keep(self):
        self.assertIsNone(GF.keep_for_resend(self.td, rec("a" * 32, A, op="request"), A))
        self.assertIsNone(GF.keep_for_resend(self.td, rec("a" * 32, A), "npub1bad"))
        p = GF.keep_for_resend(self.td, rec("a" * 32, A), A, now=1000)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_b2_due(self):
        GF.keep_for_resend(self.td, rec("a" * 32, A), A, now=1000)
        GF.keep_for_resend(self.td, rec("b" * 32, B), B, now=1000)
        self.assertEqual(GF.resend_due(self.td, A, now=1000 + GF.RESEND_GAP_S - 1), [])
        self.assertEqual(GF.resend_due(self.td, A, now=1000 + GF.RESEND_GAP_S), ["a" * 32])
        self.assertEqual(json.loads((self.td / "outbox" / ("a" * 32 + ".json")).read_text())["msgid"], "a" * 32)
        self.assertEqual(GF.resend_due(self.td, A, now=1000 + 2 * GF.RESEND_GAP_S), [])        # 還在 outbox
        GF.claim(self.td, "a" * 32)
        (self.td / "sending" / ("a" * 32 + ".json")).unlink()
        self.assertEqual(GF.resend_due(self.td, A, now=1000 + GF.RESEND_GAP_S + 10), [])        # 間隔內
        self.assertEqual(json.loads((self.td / "resend" / ("a" * 32 + ".json")).read_text())["n"], 1)
        self.assertTrue((self.td / "resend" / ("b" * 32 + ".json")).exists())
        self.assertFalse((self.td / "outbox" / ("b" * 32 + ".json")).exists())

    def test_b3_drop(self):
        for i, st in enumerate(("queued", "delivered", "read", "refused")):
            m = "%032x" % (i + 1)
            GF.keep_for_resend(self.td, rec(m, A), A, now=1000)
            GF.append_receipt(self.td, m, {"v": 1, "at": 1001, "stage": st})
        GF.keep_for_resend(self.td, rec("e" * 32, A), A, now=1000)
        GF.append_receipt(self.td, "e" * 32, {"v": 1, "at": 1001, "stage": "relayed", "detail": "1"})
        self.assertEqual(GF.resend_due(self.td, A, now=5000), ["e" * 32])
        self.assertEqual(sorted(p.stem for p in (self.td / "resend").glob("*.json")), ["e" * 32])
        p = self.td / "resend" / ("e" * 32 + ".json")
        cur = json.loads(p.read_text())
        cur["n"] = GF.RESEND_MAX
        p.write_text(json.dumps(cur))
        self.assertEqual(GF.resend_due(self.td, A, now=9000), [])
        self.assertFalse(p.exists())
        GF.keep_for_resend(self.td, rec("f" * 32, A), A, now=1000)
        self.assertEqual(GF.resend_due(self.td, A, now=1000 + GF.RESEND_KEEP_S + 1), [])
        self.assertFalse((self.td / "resend" / ("f" * 32 + ".json")).exists())


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Wire(unittest.TestCase):
    def test_b4_presence_marks_owner(self):
        td = Path(tempfile.mkdtemp(prefix="aib-resend-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(td / "root")], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), {"due": True})


class Knife(unittest.TestCase):
    def _run(self, anchor, repl, which):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_global_files.py", anchor, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_ignore_receipts(self):
        self._run("        if stages & RESEND_DONE or now", "        if now", "Resend.test_b3_drop")

    def test_k2_no_gap(self):
        self._run('        if cur.get("owner") != owner or now - int(cur.get("last") or 0) < RESEND_GAP_S:',
                  '        if cur.get("owner") != owner:', "Resend.test_b2_due")


if __name__ == "__main__":
    unittest.main()
