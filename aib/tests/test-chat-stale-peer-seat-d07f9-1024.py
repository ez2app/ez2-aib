#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""在線 / 名片 / 空間快照寄給對方時,聯絡人快照記的是舊席(10-24 一台會員機:對方 10-20 換艙,名冊只剩新席;
Pro 的快照還記著舊席 ⇒ resolve 一直 no_seat、在線四天沒送到,兩邊都看不出來)。

P1 快照第一席查不到 ⇒ 改抓名冊第一席、查到就用;快照 seats 整份換成名冊目前公開的席(舊席拿掉)
P2 快照那席查得到 ⇒ 不抓名冊(不多連網)
P3 名冊也沒有可用的席 ⇒ 照舊回失敗、快照不動
(要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網:resolve 與名冊都換成假的)
S1 一席一把(10-24 Pro:@cfd04 換過人格,同一席三把 active ⇒ 名冊重複席、別台整份拒收):同一席先綁人格 A、再換 B ⇒
   sending_seats 只給 B 那把;名冊這一席只出現一次、npub = B;A 的金鑰仍 active(收件照認、人格換回來照用)
S2 人格換回 A ⇒ sending_seats 換回 A 那把(不產新的)
S3 名冊組完的席碼不重複(就算對照表有兩把同席、同人格的 active,也只取第一把)
S4 手機配對給的 npub 跟名冊同一把:換過人格的席 ⇒ 配對 QR 給 B 那把,不給舊人格 A(@abb89 #297 r1)
刀(複本):K1 拿掉改抓名冊 ⇒ P1 紅;K2 sending_seats 不比人格 ⇒ S1 紅;K4 配對改回挑同席第一把 ⇒ S4 紅
"""
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
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import dm_global_files as GF
import nostr_keys as NK
from pathlib import Path
root, case = Path(sys.argv[2]), sys.argv[3]
owner = NK.npub(NK.pubkey_hex("%064x" % 0x4101))
OLD, NEW = owner + "/5a97/4345c", owner + "/754c/fdcf5"
GF.note_peer(root, owner, "B", "TW", {"cabin": "5a97", "seat": "4345c", "npub": "npub1x", "name": None, "role": "root"}, 100)
good = {OLD} if case == "p2" else ({NEW} if case == "p1" else set())
calls = {"roster": 0, "resolve": []}

async def fake_resolve(addr):
    calls["resolve"].append(addr)
    if addr in good:
        cab, seat = addr.split("/")[1:]
        return 0, {"cabin": cab, "seat": {"seat": seat, "npub": "npub1" + seat}, "roster_at": 200,
                   "roster_seats": [{"cabin": cab, "seat": seat, "npub": "npub1" + seat}], "dm_relays": []}
    return 2, {"why": "no_seat"}

async def fake_roster(o, relays, r):
    calls["roster"] += 1
    seats = [] if case == "p3" else [{"seat": "fdcf5"}]
    return 0, {"cabins": [{"cabin": "754c", "seats": seats}]}, 200

CN._owner_roster = fake_roster
s = object.__new__(CN.Sender)
s.root, s.cache = root, {}
s._resolve = fake_resolve
rc, res = asyncio.run(s._first_seat(owner))
print(json.dumps({"rc": rc, "seat": (res.get("seat") or {}).get("seat"), "calls": calls,
                  "snap": [x["seat"] for x in GF.read_peers(root)[owner]["seats"]]}))
'''


sys.path.insert(0, str(REPO / "src" / "cockpit"))
import chat_roster as CR  # noqa: E402
import nostr_keys as NK  # noqa: E402


class OneKeyPerSeat(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-onekey-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td / "home")}
        NK.ensure_owner(self.env)
        CR.set_profile("A", "TW", env=self.env)
        CR.set_public("cfd04", True, env=self.env)

    def live(self, uid):
        return [{"seat5": "cfd04", "cabin": "6ddf", "persona_uid": uid, "name": None, "role": "root"}]

    def test_s1_s2_current_persona_only(self):
        NK.sync_seats(self.live("aaaa11"), self.env)
        npub_a = NK.sending_seats(self.env, self.live("aaaa11"))[0]["npub"]
        NK.sync_seats(self.live("bbbb22"), self.env)
        act = [e for e in NK.read_map(self.env)["seats"] if e["status"] == "active" and e["seat5"] == "cfd04"]
        self.assertEqual(sorted(e["persona_uid"] for e in act), ["aaaa11", "bbbb22"])      # A 那把沒封存
        got = NK.sending_seats(self.env, self.live("bbbb22"))
        self.assertEqual([e["persona_uid"] for e in got], ["bbbb22"])
        content, dm = CR.build(self.env, live=self.live("bbbb22"))
        seats = [x for c in content["cabins"] for x in c["seats"]]
        self.assertEqual([(x["seat"], x["npub"]) for x in seats], [("cfd04", got[0]["npub"])])
        self.assertEqual([d["key"] for d in dm], ["bbbb22"])
        NK.sync_seats(self.live("aaaa11"), self.env)
        self.assertEqual(NK.sending_seats(self.env, self.live("aaaa11"))[0]["npub"], npub_a)
        self.assertEqual(len([e for e in NK.read_map(self.env)["seats"] if e["seat5"] == "cfd04"]), 2)

    def test_s3_no_duplicate_seat(self):
        NK.sync_seats(self.live("aaaa11"), self.env)
        m = NK.read_map(self.env)
        m["seats"].append(dict(m["seats"][0], key="aaaa11-x", npub=NK.npub(NK.pubkey_hex("%064x" % 0x4201))))
        NK._save_map(m, self.env)
        content, _dm = CR.build(self.env, live=self.live("aaaa11"))
        self.assertEqual([x["seat"] for c in content["cabins"] for x in c["seats"]], ["cfd04"])

    def test_s4_pair_uses_current_persona(self):
        import chat_pair as CP
        NK.sync_seats(self.live("aaaa11"), self.env)
        NK.sync_seats(self.live("bbbb22"), self.env)
        CR.set_profile("Andy", "TW", self.env)
        CR.set_public("cfd04", True, self.env)
        want = NK.sending_seats(self.env, self.live("bbbb22"))[0]["npub"]
        old = next(e["npub"] for e in NK.read_map(self.env)["seats"] if e["persona_uid"] == "aaaa11")
        self.assertNotEqual(want, old)
        self.assertEqual(CP._seat_npub(self.env, live=self.live("bbbb22")), ("cfd04", want))
        self.assertEqual(CP._seat_npub(self.env, "cfd04", live=self.live("bbbb22")), ("cfd04", want))


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Stale(unittest.TestCase):
    def probe(self, case, repo=REPO):
        td = Path(tempfile.mkdtemp(prefix="aib-stale-seat-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(Path(repo) / "src" / "cockpit"), str(td / "root"), case],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_p1_stale_falls_back_to_roster(self):
        d = self.probe("p1")
        self.assertEqual((d["rc"], d["seat"]), (0, "fdcf5"))
        self.assertEqual(d["calls"]["roster"], 1)
        self.assertEqual(d["snap"], ["fdcf5"])            # 舊席 4345c 從快照拿掉

    def test_p2_good_snapshot_no_roster(self):
        d = self.probe("p2")
        self.assertEqual((d["rc"], d["seat"], d["calls"]["roster"]), (0, "4345c", 0))

    def test_p3_roster_empty_keeps_failure(self):
        d = self.probe("p3")
        self.assertNotEqual(d["rc"], 0)
        self.assertEqual(d["snap"], ["4345c"])


class Knife(unittest.TestCase):
    def test_k2_any_persona(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py", " and want[s5] == e.get(\"persona_uid\"):\n", ":\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "OneKeyPerSeat"], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_k4_pair_first_active(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_pair.py", "    for e in NK.sending_seats(env, live):\n",
                        "    for e in [x for x in NK.read_map(env)[\"seats\"] if x.get(\"status\") == \"active\"]:\n",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "OneKeyPerSeat.test_s4_pair_uses_current_persona"],
                            cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_fallback(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", "        fresh = await self._first_seat_addr(owner, roster=True)\n",
                        "        fresh = None\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Stale.test_p1_stale_falls_back_to_roster"],
                            cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
