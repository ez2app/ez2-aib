#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""綠點 = 一台一顆加密心跳(10-24 Andy:「總不能有 1 萬個好友發一萬次的在不在吧」,選「照這個做」)。

H1 ensure_beat 一台一把、再叫不換;beat_share 第 1 代、再叫不換;rotate ⇒ 第 2 代、舊的搬進 retired/
H2 note_beat_key:壞 npub / 壞鑰 / gen 不是正整數 ⇒ 不收;舊一代晚到不蓋新的;一位主人最多 10 台;只收 wss:// 中繼站
H3 beat-given:同一代累加;換代整份重來
H4 request_refresh 帶 ping:不看 30 秒間隔、記 ping;ping 不是 npub ⇒ 不收;不帶 ping 的刷新會清掉 ping
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網:中繼站換成假的)
H5 push_beat 只發一筆(kind 30078、d=aib-beat、作者 = 心跳鑰、內文 nip44 加密看不到席碼)⇒ poll_beats 用名片給的鑰解開 ⇒
   world.json 有那位好友、world_payload online=true;鑰不對 / 不是好友 ⇒ 不寫
H6 push_presence:好友有心跳、也拿到我這一代的鑰、等級 online ⇒ 不再一位一封;等級 status ⇒ 照寄;ping 那一位 ⇒ 照寄帶 aib-ask
H7 給過鑰的人不再是好友 ⇒ 換一代、給過名單歸零;沒人離開 ⇒ 不換
刀(複本):K1 不換代 ⇒ H7 紅;K2 不跳過有心跳的好友 ⇒ H6 紅
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

A = NK.npub(NK.pubkey_hex("%064x" % 0xB101))
M = NK.npub(NK.pubkey_hex("%064x" % 0xB102))

PROBE = r'''
import asyncio, json, sys, importlib.util
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import dm_global_files as GF
import nostr_keys as NK
from pathlib import Path
root, case = Path(sys.argv[2]), sys.argv[3]
OWNER = NK.npub(NK.pubkey_hex("%064x" % 0xB201))
F = NK.npub(NK.pubkey_hex("%064x" % 0xB202))
G = NK.npub(NK.pubkey_hex("%064x" % 0xB203))
seats = [{"seat5": "cfd04", "cabin": "6ddf", "key": "k1", "npub": "npub1me", "status": "active"}]
friends = {"friends": [{"owner": F}], "mine": []}
levels = {F: "online"}
_real_map = NK.read_map
CN.NK.read_map = lambda *a, **k: dict(_real_map(*a, **k), owner={"npub": OWNER})
CN.NK.sending_seats = lambda *a, **k: seats
CN.CR.read_profile = lambda *a, **k: {"public": ["cfd04"]}
CN.CR.read_friends = lambda env=None, kind="friends": friends.get(kind, [])
CN.CR.share_level = lambda o, *a, **k: levels.get(o, "off")
CN.CR.space_level = lambda *a, **k: "none"
CN.CR.is_blocked = lambda o, *a, **k: False
CN.CR.is_friend = lambda o, *a, **k: any(f["owner"] == o for f in friends["friends"])
CN.CR.is_mine = lambda o, *a, **k: False
CN.CR.relays = lambda *a, **k: ["wss://r.example"]
CN.CP.presence_power = lambda *a, **k: None
import seat_presence as SP
SP.presence = lambda ss, **k: [{"seat": s, "state": "online"} for s in ss]
published, made = [], []
class Out: success = True
class Cli:
    async def send_event(self, ev):
        published.append(ev)
        return Out()
    async def shutdown(self): pass
async def fake_client(relays): return Cli()
CN._client = fake_client
real_keys = CN._keys
CN._keys = lambda stem: real_keys(stem) if stem.startswith("beat") else None
CN.N.nip17_make_private_msg = lambda keys, pk, body, rumor_extra_tags=(): made.append([t.to_vec() for t in rumor_extra_tags]) or "gw"
s = object.__new__(CN.Sender)
s.root, s.cache = root, {}
async def noop(*a, **k): return None
s._push_card = noop
s._push_room = noop
async def first_seat(o): return 0, {"seat": {"npub": NK.npub(NK.pubkey_hex("%064x" % 0xB204))}, "dm_relays": []}
s._first_seat = first_seat
out = {}
NK.ensure_beat()
gen, stem = NK.beat_share()
GF.note_beat_given(root, gen, [F])
if case == "beat":
    out["ok"] = asyncio.run(s.push_beat(now=1000))
    ev = published[-1]
    out["kind"] = ev.kind().as_u16()
    out["author_is_beat"] = ev.author().to_bech32() == NK.read_map()["beat"]["npub"]
    out["leak"] = "cfd04" in ev.content()
    async def fetch_all(relays, flt): return [ev]
    CN._fetch_all = fetch_all
    import time
    good = NK.load_secret(stem)
    GF.note_beat_key(root, F, ev.author().to_bech32(), "%064x" % 0x1234 if sys.argv[4] == "wrong" else good, 1)
    if sys.argv[4] == "stranger":
        friends["friends"] = []
    out["got"] = asyncio.run(s.poll_beats(now=time.time()))
    out["world"] = sorted(GF.read_world(root))
elif case == "skip":
    GF.note_beat_key(root, F, G, "%064x" % 0x99, 1)
    out["online"] = asyncio.run(s.push_presence(now=1000))
    levels[F] = "status"
    s.p_last = {}
    out["status"] = asyncio.run(s.push_presence(now=2000))
    levels[F] = "online"
    s.p_last = {}
    s.ask_due, s.ping_owner = True, F
    out["ping"] = asyncio.run(s.push_presence(now=3000))
    out["ask_tag"] = ["aib-ask", "1"] in made[-1]
elif case == "rotate":
    s._rotate_beat_if_needed([F])
    out["same"] = GF.read_beat_given(root)[0]
    friends["friends"] = []
    s._rotate_beat_if_needed([])
    g2, given2 = GF.read_beat_given(root)
    out["after"] = [g2, sorted(given2), NK.beat_share()[0]]
print(json.dumps(out))
'''


class Keys(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-beat-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib")}

    def test_h1_keys(self):
        b = NK.ensure_beat(self.env)
        self.assertEqual(NK.ensure_beat(self.env), b)
        self.assertEqual(NK.beat_share(self.env), (1, "beat-share-1"))
        self.assertEqual(NK.beat_share(self.env), (1, "beat-share-1"))
        self.assertEqual(NK.beat_share(self.env, rotate=True), (2, "beat-share-2"))
        kd = NK.keys_dir(self.env)
        self.assertFalse((kd / "beat-share-1.key").exists())
        self.assertEqual(len(list((kd / "retired").glob("beat-share-1.*.key"))), 1)
        self.assertTrue((kd / "beat-share-2.key").is_file())

    def test_h2_note_key(self):
        root = self.td / "r"
        k = "%064x" % 7
        self.assertFalse(GF.note_beat_key(root, "npub1bad", M, k, 1))
        self.assertFalse(GF.note_beat_key(root, A, M, "zz", 1))
        self.assertFalse(GF.note_beat_key(root, A, M, k, 0))
        self.assertFalse(GF.note_beat_key(root, A, M, k, True))
        self.assertTrue(GF.note_beat_key(root, A, M, k, 2, ["wss://ok.example", "http://bad", 5], now=10))
        self.assertFalse(GF.note_beat_key(root, A, M, "%064x" % 8, 1, now=11))       # 舊一代晚到
        self.assertEqual(GF.read_beat_keys(root), [(A, M, k, ["wss://ok.example"])])
        for i in range(12):
            GF.note_beat_key(root, A, NK.npub(NK.pubkey_hex("%064x" % (0xC000 + i))), k, 1, now=100 + i)
        self.assertEqual(len(GF.read_beat_keys(root)), 10)

    def test_h3_given(self):
        root = self.td / "r"
        self.assertEqual(GF.read_beat_given(root), (0, set()))
        GF.note_beat_given(root, 1, [A])
        GF.note_beat_given(root, 1, [M])
        self.assertEqual(GF.read_beat_given(root), (1, {A, M}))
        GF.note_beat_given(root, 2, [])
        self.assertEqual(GF.read_beat_given(root), (2, set()))

    def test_h4_ping(self):
        root = self.td / "r"
        self.assertTrue(GF.request_refresh(root, now=1000))
        self.assertFalse(GF.request_refresh(root, now=1001))
        self.assertTrue(GF.request_refresh(root, now=1002, ping=A))
        self.assertEqual(GF.read_refresh(root), {"requested_at": 1002, "ping": A})
        self.assertFalse(GF.request_refresh(root, now=1003, ping="npub1bad"))
        self.assertTrue(GF.request_refresh(root, now=2000))
        self.assertNotIn("ping", GF.read_refresh(root))


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Worker(unittest.TestCase):
    def probe(self, case, sub="-"):
        td = Path(tempfile.mkdtemp(prefix="aib-beat-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(td / "root"), case, sub], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_h5_beat_roundtrip(self):
        d = self.probe("beat", "good")
        self.assertEqual((d["ok"], d["kind"], d["author_is_beat"], d["leak"], d["got"]), (True, 30078, True, False, 1))
        self.assertEqual(len(d["world"]), 1)
        self.assertTrue(d["world"][0].endswith("/6ddf"))
        for sub in ("wrong", "stranger"):
            d = self.probe("beat", sub)
            self.assertEqual((d["got"], d["world"]), (0, []), sub)

    def test_h6_skip_dm_for_beaters(self):
        d = self.probe("skip")
        self.assertEqual((d["online"], d["status"], d["ping"], d["ask_tag"]), (0, 1, 1, True))

    def test_h7_rotate(self):
        d = self.probe("rotate")
        self.assertEqual(d["same"], 1)
        self.assertEqual(d["after"], [2, [], 2])


class Knife(unittest.TestCase):
    def _run(self, anchor, repl, which):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", anchor, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_rotate(self):
        self._run("        if given - ok:\n", "        if False:\n", "Worker.test_h7_rotate")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_no_skip(self):
        self._run('            if lvl == "online" and tgt in beaters and tgt in given and not asking:\n',
                  "            if False:\n", "Worker.test_h6_skip_dm_for_beaters")


if __name__ == "__main__":
    unittest.main()
