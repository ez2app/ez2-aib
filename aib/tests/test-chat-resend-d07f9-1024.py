#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對方上線就補寄還沒到的信(10-24 Andy「很像使用市面上的通訊 App 一樣絲滑簡易」;#303 第 1 輪審查後重寫)。

B1 keep_for_resend:只留一般信(op=send)、主人 / 收件席 / 寄件席 npub 要合格;放 resend/<主人>/、檔 0600;已留不重寫
B2 resend_due:對方在線 ⇒ 過了間隔才回、照原寄出順序、一輪最多 RESEND_PER_ROUND 封;別人的不動;已在 outbox /
   sending 不回;note_resent 次數 +1、重算間隔;過期(expires)的刪
B3 對方那台已回 queued / delivered / read / 拒收 ⇒ 刪;補滿 RESEND_MAX 次或留超過 RESEND_KEEP_S ⇒ 刪;
   drop_resend 刪不再是好友的整夾、10-24 版平放檔
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網)
B4 收到好友的在線狀態 ⇒ 寄件端記下要補寄這位(resend_owners)
B5 Sender.resend(B-H2 / B-M6 / A-L8):不寫回條、不改 sent/、不另發給自己那份、帶 aib-sent-at;對方換席 / 我換人格 ⇒
   不送、只寫稽核;送完次數 +1
B6 在線狀態 / 名片 / 空間不佔收件去重名額(A-M6),一般信照佔
刀(複本):K1 resend_due 不看回條 ⇒ B3 紅;K2 不看間隔 ⇒ B2 紅;K3 不照寄出順序 ⇒ B2 紅;K4 補寄寫回條 ⇒ B5 紅;
K5 補寄也發給自己 ⇒ B5 紅;K6 不比寄件 npub ⇒ B5 紅
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
import dm_global_files as GF
import nostr_keys as NK
from pathlib import Path
root = Path(sys.argv[2])
case = sys.argv[3]
F = NK.npub(NK.pubkey_hex("%064x" % 0xA103))
CN.CR.is_blocked = lambda o, *a, **k: False
CN.CR.is_friend = lambda o, *a, **k: o == F
CN.CR.is_mine = lambda o, *a, **k: False
s = object.__new__(CN.Sender)
s.root, s.cache = root, {}
r = object.__new__(CN.Receiver)
r.root, r.sender = root, s
out = {}
class Rumor:
    def created_at(self):                                       # #318 起在線狀態記 rumor 的寄出時間
        class T:
            def as_secs(self): return 1000
        return T()
    def content(self): return json.dumps({"v": 1, "seats": [{"seat": "f0001", "cabin": "c1", "state": "online"}]})
    def id(self): return None
if case == "presence":
    async def resolve(addr): return 0, {"seat": {"npub": "npub1f"}}
    s._resolve = resolve
    asyncio.run(r.on_presence(Rumor(), {"aib-from": F + "/c1/f0001"}, "npub1f"))
    out["due"] = sorted(getattr(s, "resend_owners", set())) == [F]
elif case == "resend":
    SEAT = NK.npub(NK.pubkey_hex("%064x" % 0xA104))
    SEAT2 = NK.npub(NK.pubkey_hex("%064x" % 0xA105))
    ME = NK.npub(NK.pubkey_hex("%064x" % 0xA106))
    entry = {"seat5": "d07f9", "npub": ME, "key": "k"}
    CN.NK.sending_seats = lambda *a, **k: [entry]
    CN.NK.read_map = lambda *a, **k: {"owner": {"npub": "npub1me"}}
    class K:
        def public_key(self): return self
        def to_bech32(self): return ME
    CN._keys = lambda stem: K()
    made, sends = [], []
    CN.N.nip17_make_private_msg = lambda keys, pk, body, rumor_extra_tags=(): made.append(
        [t.to_vec() for t in rumor_extra_tags]) or "gw"
    async def send_to(target, own, gw, mine):
        sends.append([target, own, gw, mine])
        return ["wss://x"]
    CN._send_to = send_to
    seat = {"v": [SEAT]}
    async def resolve(addr): return 0, {"seat": {"npub": seat["v"][0]}, "dm_relays": ["wss://x"]}
    s._resolve = resolve
    rec = {"v": GF.VERSION, "msgid": "a" * 32, "created_at": 1000, "op": "send",
           "from": {"cabin": "6ddf", "seat": "d07f9", "attested": True}, "to": F + "/c1/f0001", "content": "hi"}
    GF.ensure_dirs(root)
    (root / "sent").mkdir(exist_ok=True)
    (root / "sent" / ("a" * 32 + ".json")).write_text('{"to": "%s", "at": 1}' % SEAT)
    GF.keep_for_resend(root, rec, F, SEAT, ME, now=1000)
    cur = json.loads((root / "resend" / F / ("a" * 32 + ".json")).read_text())
    out["ok"] = asyncio.run(s.resend(cur))
    out["receipts"] = GF.read_receipts(root, "a" * 32)
    out["sent"] = (root / "sent" / ("a" * 32 + ".json")).read_text()
    out["mine"] = sends[-1][3]
    out["own"] = sends[-1][1]
    out["sent_at"] = ["aib-sent-at", "1000"] in made[-1]
    out["n"] = json.loads((root / "resend" / F / ("a" * 32 + ".json")).read_text())["n"]
    seat["v"] = [SEAT2]
    out["peer_changed"] = asyncio.run(s.resend(cur))
    seat["v"] = [SEAT]
    entry["npub"] = SEAT2
    out["sender_changed"] = asyncio.run(s.resend(cur))
    out["sends"] = len(sends)
elif case == "dedupe":
    seen = set()
    class Led:
        def seen(self, k): return k in seen
        def mark(self, k): seen.add(k)
    r.led = Led()
    got = []
    async def on_presence(*a): got.append("presence")
    async def on_card(*a): got.append("card")
    r.on_presence, r.on_card = on_presence, on_card
    for kind in ("presence", "card"):
        asyncio.run(r.on_dm(Rumor(), {"aib-type": kind, "aib-msgid": "b" * 32}, "npub1f", None))
    out["got"] = got
    out["seen"] = sorted(seen)
print(json.dumps(out))
'''


def rec(msgid, to_owner, op="send"):
    return {"v": GF.VERSION, "msgid": msgid, "created_at": 1000, "op": op,
            "from": {"cabin": "6ddf", "seat": "d07f9", "attested": True}, "to": to_owner + "/c1/f0001", "content": "hi"}


SEAT = NK.npub(NK.pubkey_hex("%064x" % 0xA201))
ME = NK.npub(NK.pubkey_hex("%064x" % 0xA202))


def keep(td, msgid, owner, now=1000, **kw):
    return GF.keep_for_resend(td, dict(rec(msgid, owner), **kw), owner, SEAT, ME, now=now)


class Resend(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-resend-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        GF.ensure_dirs(self.td)

    def ids(self, rows):
        return [c["rec"]["msgid"] for c in rows]

    def test_b1_keep(self):
        self.assertIsNone(GF.keep_for_resend(self.td, rec("a" * 32, A, op="request"), A, SEAT, ME))
        self.assertIsNone(GF.keep_for_resend(self.td, rec("a" * 32, A), "npub1bad", SEAT, ME))
        self.assertIsNone(GF.keep_for_resend(self.td, rec("a" * 32, A), A, "npub1bad", ME))
        self.assertIsNone(GF.keep_for_resend(self.td, rec("a" * 32, A), A, SEAT, None))
        p = keep(self.td, "a" * 32, A)
        self.assertEqual(p, self.td / "resend" / A / ("a" * 32 + ".json"))
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        keep(self.td, "a" * 32, A, now=5000)                                  # 已留不重寫(at 不重設)
        cur = json.loads(p.read_text())
        self.assertEqual((cur["at"], cur["to"], cur["from"]), (1000, SEAT, ME))

    def test_b2_due(self):
        keep(self.td, "c" * 32, A, now=1003)
        keep(self.td, "a" * 32, A, now=1001)
        keep(self.td, "b" * 32, A, now=1002)
        keep(self.td, "d" * 32, B, now=1000)
        self.assertEqual(GF.resend_due(self.td, A, now=1001 + GF.RESEND_GAP_S - 1), [])
        self.assertEqual(self.ids(GF.resend_due(self.td, A, now=1003 + GF.RESEND_GAP_S)), ["a" * 32, "b" * 32, "c" * 32])
        self.assertEqual(self.ids(GF.resend_due(self.td, A, now=1003 + GF.RESEND_GAP_S, limit=2)), ["a" * 32, "b" * 32])
        self.assertFalse((self.td / "outbox" / ("a" * 32 + ".json")).exists())      # 不再放回 outbox
        GF.note_resent(self.td, A, "a" * 32, now=2000)
        self.assertNotIn("a" * 32, self.ids(GF.resend_due(self.td, A, now=2000 + GF.RESEND_GAP_S - 1)))
        self.assertIn("a" * 32, self.ids(GF.resend_due(self.td, A, now=2000 + GF.RESEND_GAP_S)))
        self.assertEqual(json.loads((self.td / "resend" / A / ("a" * 32 + ".json")).read_text())["n"], 1)
        GF.write_outbox(self.td, rec("b" * 32, A))
        self.assertNotIn("b" * 32, self.ids(GF.resend_due(self.td, A, now=9000)))   # 已在 outbox
        self.assertEqual(self.ids(GF.resend_due(self.td, B, now=9000)), ["d" * 32])
        for i in range(GF.RESEND_PER_ROUND + 5):
            keep(self.td, "%032x" % (0xE000 + i), B, now=1000 + i)
        self.assertEqual(len(GF.resend_due(self.td, B, now=9000)), GF.RESEND_PER_ROUND)
        keep(self.td, "f" * 32, A, expires=1500)
        GF.resend_due(self.td, A, now=1600 + GF.RESEND_GAP_S)
        self.assertFalse((self.td / "resend" / A / ("f" * 32 + ".json")).exists())   # 過期的刪

    def test_b3_drop(self):
        for i, st in enumerate(("queued", "delivered", "read", "refused")):
            m = "%032x" % (i + 1)
            keep(self.td, m, A)
            GF.append_receipt(self.td, m, {"v": 1, "at": 1001, "stage": st})
        keep(self.td, "e" * 32, A)
        GF.append_receipt(self.td, "e" * 32, {"v": 1, "at": 1001, "stage": "relayed", "detail": "1"})
        self.assertEqual(self.ids(GF.resend_due(self.td, A, now=5000)), ["e" * 32])
        self.assertEqual(sorted(p.stem for p in (self.td / "resend" / A).glob("*.json")), ["e" * 32])
        p = self.td / "resend" / A / ("e" * 32 + ".json")
        cur = json.loads(p.read_text())
        cur["n"] = GF.RESEND_MAX
        p.write_text(json.dumps(cur))
        self.assertEqual(GF.resend_due(self.td, A, now=9000), [])
        self.assertFalse(p.exists())
        keep(self.td, "f" * 32, A)
        self.assertEqual(GF.resend_due(self.td, A, now=1000 + GF.RESEND_KEEP_S + 1), [])
        self.assertFalse((self.td / "resend" / A / ("f" * 32 + ".json")).exists())
        keep(self.td, "1" * 32, A)
        keep(self.td, "2" * 32, B)
        (self.td / "resend" / ("3" * 32 + ".json")).write_text("{}")             # 10-24 版平放檔
        GF.drop_resend(self.td, lambda o: o == A)
        self.assertEqual(sorted(p.name for p in (self.td / "resend").iterdir()), [A])


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Wire(unittest.TestCase):
    def probe(self, case):
        td = Path(tempfile.mkdtemp(prefix="aib-resend-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(td / "root"), case], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_b4_presence_marks_owner(self):
        self.assertEqual(self.probe("presence"), {"due": True})

    def test_b5_resend_path(self):
        d = self.probe("resend")
        self.assertTrue(d["ok"])
        self.assertEqual(d["receipts"], [], "補寄不准寫回條(會蓋掉之後的 delivered)")
        self.assertIn('"at": 1}', d["sent"], "補寄不准改 sent/")
        self.assertIsNone(d["mine"], "補寄不另發給自己那份")
        self.assertEqual(d["own"], [])
        self.assertTrue(d["sent_at"])
        self.assertEqual(d["n"], 1)
        self.assertEqual((d["peer_changed"], d["sender_changed"], d["sends"]), (False, False, 1))

    def test_b6_dedupe_slots(self):
        d = self.probe("dedupe")
        self.assertEqual(d["got"], ["presence", "card"])
        self.assertEqual(d["seen"], [])


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
        self._run('        if now - int_or0(cur.get("last")) < RESEND_GAP_S:', '        if False:', "Resend.test_b2_due")

    def test_k3_no_order(self):
        self._run('    rows.sort(key=lambda c: (int_or0(c.get("at")), c["rec"]["msgid"]))',
                  '    rows.sort(key=lambda c: c["rec"]["msgid"], reverse=True)', "Resend.test_b2_due")

    def _cn(self, anchor, repl, which):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", anchor, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k4_resend_writes_receipt(self):
        self._cn("        GF.note_resent(self.root, owner, msgid)\n",
                 "        GF.note_resent(self.root, owner, msgid)\n        self._rcpt(msgid, owner, \"relayed\", \"1\")\n",
                 "Wire.test_b5_resend_path")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k5_resend_self_copy(self):
        self._cn('            ok = await _send_to(res["dm_relays"], [], gw, None)\n',
                 '            ok = await _send_to(res["dm_relays"], CR.relays(), gw, gw)\n', "Wire.test_b5_resend_path")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k6_no_sender_check(self):
        self._cn(' or entry.get("npub") != cur.get("from"):', ':', "Wire.test_b5_resend_path")


if __name__ == "__main__":
    unittest.main()
