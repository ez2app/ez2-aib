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
   world.json 有那位好友、記下要補寄給他;鑰不對 / 不是好友 ⇒ 不寫
H6 push_presence:好友有心跳、也拿到我這一代的鑰、等級 online ⇒ 不再一位一封;等級 status ⇒ 照寄;ping 那一位 ⇒ 照寄帶 aib-ask
H7 給過鑰的人不再是好友 ⇒ 換一代、給過名單歸零;沒人離開 ⇒ 不換
#303 第 1 輪審查補(10-25):
H8 心跳簽章鑰跟共用鑰同一代(A-H1):換代 npub 跟著換、舊鑰進 retired/;10-24 版 beat.key 也收;retired 超過 30 天刪
H9 心跳鑰一位一檔(B-H1:500 位好友照收);同一個心跳 npub 兩位宣告都列出(r2 A-M4,誰算數看 H15);drop_beat_keys 刪整檔
H15 (venv)心跳加密內文帶 owner:別的好友宣告 V 的心跳 npub(連鑰都抄了)⇒ 只算 V 的、V 照樣綠點;
    只有冒名那位宣告 ⇒ 誰都不算(#303 r2 A-M4)
H10 world 一次寫多格、500 位好友寫得進去(超過上限丟最舊的)
H11 同一位 ping 10 秒一次、換一位不等(A-M5);public_relay_ok 擋本機 / 內網 / link-local / 單字主機名
H12 (venv)好友名片給的站只查那位好友、內網站不連、我的站查全部(A-M2);壞網址不進心跳鑰(A-M3);
    同一個 npub 被兩位宣告 ⇒ 誰的名片站都不查它、只在我的站查(#303 r3 A-Low)
H13 (venv)點開對話框只問那一位、整面刷新才問舊版;有人問我 ⇒ 有心跳也回(B-M3)
H14 (venv)心跳的 presence_at 用本機收到時間、sent_at 記原值(B-M5);換代後 push_beat 用新簽章鑰
刀(複本):K1 不換代 ⇒ H7 紅;K2 不跳過有心跳的好友 ⇒ H6 紅;K3 好友站查全部人 ⇒ H12 紅;K4 ping 也問舊版 ⇒ H13 紅;
K5 不回問我的人 ⇒ H13 紅;K6 簽章鑰不換代 ⇒ H8 紅;K7 不比內文 owner ⇒ H15 紅;K8 撞號 npub 照查名片站 ⇒ H12 紅
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
who = [OWNER]
CN.NK.read_map = lambda *a, **k: dict(_real_map(*a, **k), owner={"npub": who[0]})
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
CN.N.nip17_make_private_msg = lambda keys, pk, body, rumor_extra_tags=(): made.append([t.to_vec() for t in rumor_extra_tags] + [["to", cur[-1]]]) or "gw"
s = object.__new__(CN.Sender)
s.root, s.cache = root, {}
async def noop(*a, **k): return None
s._push_card = noop
s.cache["x"] = 1
s._push_room = noop
cur = [None]
async def first_seat(o):
    cur.append(o)
    return 0, {"seat": {"npub": NK.npub(NK.pubkey_hex("%064x" % 0xB204))}, "dm_relays": []}
s._first_seat = first_seat
out = {}
NK.ensure_beat()
gen, stem = NK.beat_share()
GF.note_beat_given(root, gen, [F])
if case == "beat":
    who[0] = F                                     # 這筆心跳當成好友 F 那台發的
    out["ok"] = asyncio.run(s.push_beat(now=1000))
    who[0] = OWNER
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
    if sys.argv[4] in ("claim", "claim_only"):     # 好友 G 宣告 F 的心跳 npub、連鑰都抄
        friends["friends"] = [{"owner": F}, {"owner": G}]
        if sys.argv[4] == "claim_only":
            GF.drop_beat_keys(root, lambda o: o != F)
        GF.note_beat_key(root, G, ev.author().to_bech32(), good, 1)
    out["got"] = asyncio.run(s.poll_beats(now=time.time()))
    out["world"] = sorted(GF.read_world(root))
    out["resend"] = sorted(getattr(s, "resend_owners", set())) == [F]
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
elif case == "plan":
    R = NK.npub(NK.pubkey_hex("%064x" % 0xB204))
    friends["friends"] = [{"owner": F}, {"owner": G}]
    GF.note_beat_key(root, F, NK.npub(NK.pubkey_hex("%064x" % 0xB301)), "%064x" % 1, 1,
                     ["wss://friend.example", "wss://10.0.0.1", "wss://r.example", "wss://intranet"])
    GF.note_beat_key(root, G, NK.npub(NK.pubkey_hex("%064x" % 0xB302)), "%064x" % 2, 1, ["wss://g.example"])
    calls = []
    async def fetch_all(relays, flt):
        calls.append([relays, sorted(json.loads(flt.as_json()).get("authors") or [])])
        return []
    CN._fetch_all = fetch_all
    asyncio.run(s.poll_beats(now=5000))
    hexes = {NK.npub(NK.pubkey_hex("%064x" % 0xB301)): "f", NK.npub(NK.pubkey_hex("%064x" % 0xB302)): "g"}
    tohex = {N.PublicKey.parse(n).to_hex(): v for n, v in hexes.items()} if False else None
    import nostr_sdk as NS
    names = {NS.PublicKey.parse(n).to_hex(): v for n, v in hexes.items()}
    out["calls"] = sorted([c[0], sorted(names[a] for a in c[1])] for c in calls)
    out["good"] = CN._good_relays(["wss://a b", "wss://ok.example", 5, "http://x"])
elif case == "plan_contested":
    friends["friends"] = [{"owner": F}, {"owner": G}]
    V = NK.npub(NK.pubkey_hex("%064x" % 0xB301))
    GF.note_beat_key(root, F, V, "%064x" % 1, 1, ["wss://friend.example"])
    GF.note_beat_key(root, G, V, "%064x" % 1, 1, ["wss://g.example"])        # G 抄 F 的心跳 npub 宣告成自己的
    GF.note_beat_key(root, G, NK.npub(NK.pubkey_hex("%064x" % 0xB302)), "%064x" % 2, 1, ["wss://g.example"])
    calls = []
    async def fetch_all(relays, flt):
        calls.append([relays, sorted(json.loads(flt.as_json()).get("authors") or [])])
        return []
    CN._fetch_all = fetch_all
    asyncio.run(s.poll_beats(now=5000))
    import nostr_sdk as NS
    names = {NS.PublicKey.parse(n).to_hex(): v for n, v in {V: "f", NK.npub(NK.pubkey_hex("%064x" % 0xB302)): "g"}.items()}
    out["calls"] = sorted([c[0], sorted(names[a] for a in c[1])] for c in calls)
elif case == "ask":
    friends["friends"] = [{"owner": F}, {"owner": G}]
    levels[G] = "online"
    GF.note_beat_key(root, F, NK.npub(NK.pubkey_hex("%064x" % 0xB301)), "%064x" % 1, 1)
    def asked():
        return sorted(("f" if ["to", F] in m else "g") for m in made if ["aib-ask", "1"] in m)
    s.ask_due, s.ping_owner = True, F
    s.p_last = {}
    made.clear()
    asyncio.run(s.push_presence(now=1000))
    out["ping_asks"] = asked()
    out["ping_cache_kept"] = "x" in s.cache
    s.ask_due, s.ping_owner = True, None
    s.p_last = {}
    made.clear()
    s.cache["x"] = 1
    asyncio.run(s.push_presence(now=2000))
    out["refresh_asks"] = asked()
    out["refresh_cache_cleared"] = "x" not in s.cache
    levels[G] = "off"
    s.p_last = {F: (None, 2999)}
    s.answer_to = {F}
    made.clear()
    out["answer_sent"] = asyncio.run(s.push_presence(now=3000))
    out["answer_left"] = sorted(s.answer_to)
elif case == "cells":
    cells = s._beat_cells(F, {"seats": [{"seat": "f0001", "cabin": "c1", "state": "online"}]}, 5200, 5000)
    out["cells"] = [[c[3], c[5]] for c in cells]
elif case == "signrot":
    a1 = NK.ensure_beat()
    s._rotate_beat_if_needed([F])
    friends["friends"] = []
    s._rotate_beat_if_needed([])
    a2 = NK.ensure_beat()
    out["changed"] = a1 != a2
    asyncio.run(s.push_beat(now=1000))
    out["author_new"] = published[-1].author().to_bech32() == a2
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

    def test_h8_sign_rotates(self):
        kd = NK.keys_dir(self.env)
        NK._mkdir_private(kd)
        NK._write_key("beat", self.env)                                  # 10-24 版的 beat.key
        n1, stem1 = NK.beat_sign(self.env, now=1000)
        self.assertEqual(stem1, "beat-sign-1")
        self.assertEqual(len(list((kd / "retired").glob("beat.1000.key"))), 1)
        self.assertEqual(NK.beat_sign(self.env, now=1001), (n1, stem1))
        NK.beat_share(self.env, now=2000, rotate=True)
        n2, stem2 = NK.beat_sign(self.env, now=2000)
        self.assertEqual(stem2, "beat-sign-2")
        self.assertNotEqual(n2, n1)
        self.assertFalse((kd / "beat-sign-1.key").exists())
        self.assertEqual(len(list((kd / "retired").glob("beat-sign-1.2000.key"))), 1)
        self.assertEqual(NK.ensure_beat(self.env), n2)
        NK.beat_share(self.env, now=2000 + NK.BEAT_RETIRED_KEEP_S + 1, rotate=True)   # 30 天前退下的清掉
        self.assertEqual(sorted(p.name for p in (kd / "retired").glob("beat*")),
                         ["beat-share-2.%d.key" % (2000 + NK.BEAT_RETIRED_KEEP_S + 1)])

    def test_h9_per_owner_and_claims(self):
        root = self.td / "r"
        k = "%064x" % 7
        for i in range(500):
            o = NK.npub(NK.pubkey_hex("%064x" % (0xD000 + i)))
            self.assertTrue(GF.note_beat_key(root, o, NK.npub(NK.pubkey_hex("%064x" % (0xE000 + i))), k, 1))
        self.assertEqual(len(GF.read_beat_keys(root)), 500)
        self.assertTrue((root / "beats" / (A + ".json")).parent.is_dir())
        GF.note_beat_key(root, A, M, k, 1)
        GF.note_beat_key(root, NK.npub(NK.pubkey_hex("%064x" % 0xD000)), M, k, 1)   # 別人宣告同一個心跳 npub
        self.assertEqual(sum(1 for _o, n, _k, _r in GF.read_beat_keys(root) if n == M), 2, "都列出,工人解開再比 owner")
        GF.drop_beat_keys(root, lambda o: o == A)
        self.assertEqual([o for o, *_r in GF.read_beat_keys(root)], [A])
        self.assertEqual(sorted(p.name for p in (root / "beats").iterdir()), [A + ".json", A + ".json.lock"])

    def test_h10_world_many(self):
        root = self.td / "r"
        rows = [(NK.npub(NK.pubkey_hex("%064x" % (0xD000 + i))), "c1",
                 [{"seat": "f0001", "state": "online", "now": None, "step": None}], 1000 + i, None, 1000 + i)
                for i in range(500)]
        GF.note_world_many(root, rows)
        w = GF.read_world(root)
        self.assertGreater(len(w), 50)
        self.assertLess(len(w), 500)                                     # 超過上限丟最舊的,寫得進去
        self.assertIn("%s/c1" % rows[-1][0], w)
        self.assertNotIn("%s/c1" % rows[0][0], w)

    def test_h11_ping_gap_and_relays(self):
        root = self.td / "r"
        self.assertTrue(GF.request_refresh(root, now=1000, ping=A))
        self.assertFalse(GF.request_refresh(root, now=1000 + GF.PING_GAP_S - 1, ping=A))
        self.assertTrue(GF.request_refresh(root, now=1000 + GF.PING_GAP_S - 1, ping=M))
        self.assertTrue(GF.request_refresh(root, now=1000 + 2 * GF.PING_GAP_S, ping=M))
        import chat_roster as CR
        for u in ("wss://relay.example", "wss://relay.example:443/x", "wss://8.8.8.8"):
            self.assertTrue(CR.public_relay_ok(u), u)
        for u in ("wss://localhost", "wss://127.0.0.1", "wss://10.1.2.3", "wss://192.168.1.120", "wss://169.254.1.1",
                  "wss://[::1]", "wss://intranet", "wss://nas.local", "ws://relay.example", "wss://a b", "wss://x.internal"):
            self.assertFalse(CR.public_relay_ok(u), u)

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
        self.assertTrue(d["resend"])                           # 心跳在線 ⇒ 補寄還沒到的信
        for sub in ("wrong", "stranger"):
            d = self.probe("beat", sub)
            self.assertEqual((d["got"], d["world"]), (0, []), sub)

    def test_h15_owner_bound(self):
        d = self.probe("beat", "claim")
        self.assertEqual((d["got"], len(d["world"])), (1, 1))
        self.assertTrue(d["world"][0].startswith(NK.npub(NK.pubkey_hex("%064x" % 0xB202)) + "/"), d["world"])
        self.assertTrue(d["resend"])
        d = self.probe("beat", "claim_only")
        self.assertEqual((d["got"], d["world"]), (0, []))

    def test_h6_skip_dm_for_beaters(self):
        d = self.probe("skip")
        self.assertEqual((d["online"], d["status"], d["ping"], d["ask_tag"]), (0, 1, 1, True))

    def test_h12_friend_relays(self):
        d = self.probe("plan")
        self.assertEqual(d["calls"], [[["wss://friend.example"], ["f"]], [["wss://g.example"], ["g"]],
                                      [["wss://r.example"], ["f", "g"]]])
        self.assertEqual(d["good"], ["wss://ok.example"])
        d = self.probe("plan_contested")                   # #303 r3 A-Low:被兩位宣告的 npub 只在我的站查
        self.assertEqual(d["calls"], [[["wss://g.example"], ["g"]], [["wss://r.example"], ["f", "g"]]])

    def test_h13_ask_scope(self):
        d = self.probe("ask")
        self.assertEqual((d["ping_asks"], d["ping_cache_kept"]), (["f"], True))
        self.assertEqual((d["refresh_asks"], d["refresh_cache_cleared"]), (["g"], True))   # 只問沒有心跳的 G
        self.assertEqual((d["answer_sent"], d["answer_left"]), (1, []))

    def test_h14_cells_and_sign(self):
        self.assertEqual(self.probe("cells")["cells"], [[5000, 5200]])
        d = self.probe("signrot")
        self.assertEqual((d["changed"], d["author_new"]), (True, True))

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
        self._run('            if lvl == "online" and tgt in beaters and tgt in given and not asking and tgt not in answer:\n',
                  "            if False:\n", "Worker.test_h6_skip_dm_for_beaters")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_friend_relay_all(self):
        self._run("        plan += [([r], sorted(ns)) for r, ns in sorted(extra.items())[:BEAT_EXTRA_RELAYS]]",
                  "        plan += [([r], sorted(by_npub)) for r, ns in sorted(extra.items())[:BEAT_EXTRA_RELAYS]]",
                  "Worker.test_h12_friend_relays")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k8_contested_card_relays(self):
        self._run("            if len(by_npub[n]) > 1:\n", "            if False:\n", "Worker.test_h12_friend_relays")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k4_ping_asks_old(self):
        self._run("        elif ping:\n            ask_to = {ping}\n", "        elif False:\n            ask_to = {ping}\n",
                  "Worker.test_h13_ask_scope")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k5_no_answer(self):
        self._run("            if prev and not asking and tgt not in answer \\\n",
                  "            if prev and not asking \\\n", "Worker.test_h13_ask_scope")

    def test_k6_sign_not_rotated(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py", '    stem = "beat-sign-%d" % gen\n', '    stem = "beat-sign-1"\n',
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Keys.test_h8_sign_rotates"], cwd=str(root),
                            max_procs=60, timeout=120)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k7_owner_unchecked(self):
        self._run('                if isinstance(b, dict) and b.get("owner") == o:\n',
                  "                if isinstance(b, dict):\n", "Worker.test_h15_owner_bound")


if __name__ == "__main__":
    unittest.main()
