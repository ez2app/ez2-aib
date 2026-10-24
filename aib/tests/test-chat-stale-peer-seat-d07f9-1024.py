#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""在線 / 名片 / 空間快照寄給對方時,聯絡人快照記的是舊席(10-24 一台會員機:對方 10-20 換艙,名冊只剩新席;
Pro 的快照還記著舊席 ⇒ resolve 一直 no_seat、在線四天沒送到,兩邊都看不出來)。

P1 快照第一席查不到 ⇒ 改抓名冊第一席、查到就用;快照 seats 整份換成名冊目前公開的席(舊席拿掉)
P2 快照那席查得到 ⇒ 不抓名冊(不多連網)
P3 名冊也沒有可用的席 ⇒ 照舊回失敗、快照不動
P4 快照那席是網路錯(不是 no_seat / no_dm_relays)⇒ 不改抓名冊、快照不動(@abb89 #297 r1)
(要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網:resolve 與名冊都換成假的)
S1 一席一把(10-24 Pro:@cfd04 換過人格,同一席三把 active ⇒ 名冊重複席、別台整份拒收):同一席先綁人格 A、再換 B ⇒
   sending_seats 只給 B 那把;名冊這一席只出現一次、npub = B;A 的金鑰仍 active(收件照認、人格換回來照用)
S2 人格換回 A ⇒ sending_seats 換回 A 那把(不產新的)
S3 名冊組完的席碼不重複(就算對照表有兩把同席、同人格的 active,也只取第一把)
S5 同一人格綁兩個活席 ⇒ 兩席各一把、都在名冊
S6 人格封存後那席還綁同一人格 ⇒ 封存的鑰不出現在寄件 / 名冊
S7 真人寄件選席:現在的人格還沒有鑰 ⇒ None(不拿舊人格那把)
N1 換人格後真的寄一封(兩台、本機假中繼站)⇒ 對方用名冊驗得過、收下(queued);寄件用舊人格那把就會被當陌生拒收
N2 換人格後在線狀態也用新那把 ⇒ 好友看得到我在線(@abb89 #297 r1 m08)
S4 手機配對給的 npub 跟名冊同一把:換過人格的席 ⇒ 配對 QR 給 B 那把,不給舊人格 A(@abb89 #297 r1)
刀(複本):K1 拿掉改抓名冊 ⇒ P1 紅;K2 sending_seats 不比人格 ⇒ S1 紅;K4 配對改回挑同席第一把 ⇒ S4 紅;K5 去重看人格 ⇒ S5 紅;K6 封存的也算 ⇒ S6 紅;K7 真人寄件挑任一把 active ⇒ S7 紅;K8 寄件挑同席第一把 ⇒ N1 紅;K9 在線挑同席第一把 ⇒ N2 紅;K10 網路錯也改抓名冊 ⇒ P4 紅
被 SIGTERM(外層 timeout)⇒ 先跑清理收掉轉接程式再走(#308 L1)
E1 / E2 秒數環境變數壞值退回預設、0 / 負數夾到 1 秒;值寫壞 chat_nostr 照 import 得起來(#308 後續 r2)
"""
import json
import os
import shutil
import signal
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
_LIVE = []                                                          # 跑到一半的 SendAfterSwitch(有開轉接程式)


def _on_term(signum, frame):
    """外層 timeout 送 SIGTERM ⇒ 先跑各格的清理(Box.close 收 chat_bridge / chat_nostr)再走,不留孤兒(@abb89 #308 L1)。"""
    for t in list(_LIVE):
        try:
            t.doCleanups()
        except Exception:   # noqa: BLE001
            pass
    os._exit(128 + signum)
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
    return 2, {"why": "timeout" if case == "p4" else "no_seat"}

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

    def test_s5_two_seats_same_persona(self):
        # 同一人格綁兩個活席(@abb89 #297 r1 m02):兩席都要各有一把、都在名冊(去重是看席,不是看人格)
        live = self.live("aaaa11") + [{"seat5": "dd0e5", "cabin": "6ddf", "persona_uid": "aaaa11", "name": None, "role": "dev"}]
        CR.set_public("dd0e5", True, env=self.env)
        NK.sync_seats(live, self.env)
        got = sorted(e["seat5"] for e in NK.sending_seats(self.env, live))
        self.assertEqual(got, ["cfd04", "dd0e5"])
        content, _dm = CR.build(self.env, live=live)
        self.assertEqual(sorted(x["seat"] for c in content["cabins"] for x in c["seats"]), ["cfd04", "dd0e5"])

    def test_s6_retired_never_sends(self):
        # 人格封存後那一席還綁著同一人格(@abb89 #297 r1 m04):封存的鑰不准出現在寄件 / 名冊
        NK.sync_seats(self.live("aaaa11"), self.env)
        self.assertEqual(NK.retire_persona("aaaa11", self.env), 1)
        self.assertEqual(NK.sending_seats(self.env, self.live("aaaa11")), [])
        content, _dm = CR.build(self.env, live=self.live("aaaa11"))
        self.assertEqual([x for c in content["cabins"] for x in c["seats"]], [])

    def test_s7_human_pick_needs_current_key(self):
        # 真人寄件選席(chat_human.pick_seat,@abb89 #297 r1 m09):現在的人格(B)還沒有鑰 ⇒ None,不拿舊人格 A 那把代簽
        import chat_human as CH
        NK.sync_seats(self.live("aaaa11"), self.env)
        orig = NK.live_seats
        self.addCleanup(setattr, NK, "live_seats", orig)
        NK.live_seats = lambda home=None, env=None: self.live("bbbb22")
        self.assertIsNone(CH.pick_seat("6ddf", self.env))
        NK.live_seats = lambda home=None, env=None: self.live("aaaa11")
        self.assertEqual(CH.pick_seat("6ddf", self.env), "cfd04")

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
        for k in ("EZ2_AIB_HOME", "EZ2_AIB_SECRETS_DIR", "AIB_NOSTR_MAP",          # @abb89 #308:外面帶進來的不准漏進子行程
                  "AIB_CHAT_HOME", "AIB_CHAT_BRIDGE_DIR", "AIB_CHAT_RELAYS"):
            env.pop(k, None)
        r = subprocess.run([VPY, "-c", PROBE, str(Path(repo) / "src" / "cockpit"), str(td / "root"), case],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_p1_stale_falls_back_to_roster(self):
        d = self.probe("p1")
        self.assertEqual((d["rc"], d["seat"]), (0, "fdcf5"))
        self.assertEqual(d["calls"]["roster"], 1)
        self.assertEqual(d["snap"], ["fdcf5"])            # 舊席 4345c 從快照拿掉

    def test_p4_network_error_no_roster(self):
        d = self.probe("p4")
        self.assertEqual(d["rc"], 2)
        self.assertEqual(d["calls"]["roster"], 0, "網路錯不是席不在 ⇒ 不改抓名冊(等下一輪)")
        self.assertEqual(d["snap"], ["4345c"])

    def test_p2_good_snapshot_no_roster(self):
        d = self.probe("p2")
        self.assertEqual((d["rc"], d["seat"], d["calls"]["roster"]), (0, "4345c", 0))

    def test_p3_roster_empty_keeps_failure(self):
        d = self.probe("p3")
        self.assertNotEqual(d["rc"], 0)
        self.assertEqual(d["snap"], ["4345c"])


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class SendAfterSwitch(unittest.TestCase):
    """@abb89 #297 r1 m07:寄件(chat_nostr Sender)要用名冊那一把;改回「同席第一把 active」⇒ 對方驗不過、當陌生拒收。"""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("chat_recv_switch", str(HERE / "test-chat-recv-d07f9-1016.py"))
        self.RT = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.RT)
        self.RT.Network.setUp(self)
        _LIVE.append(self)                                          # 被 SIGTERM 也要收掉兩台轉接程式(#308 L1)
        self.addCleanup(_LIVE.remove, self)

    _box = property(lambda self: self.RT.Network._box.__get__(self))

    box_extra = {"AIB_CHAT_PRESENCE_TICK": "1", "AIB_CHAT_PRESENCE_GAP": "1", "AIB_CHAT_PRESENCE_EVERY": "3",
                 "AIB_CHAT_PUBLISH_EVERY": "2", "AIB_CHAT_RESUB_EVERY": "2"}   # 名冊重發 60 秒、重新訂閱 30 秒 ⇒ 2 秒(#308 L1:整支縮短)

    def _switched(self):
        """Andy 那一席換人格、名冊換成新那把之後 ⇒ (a, x, b, y);Bob 這時才開(沒有舊名冊快取),把 Andy 加好友。"""
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        old = [e["npub"] for e in NK.sending_seats(a.env()) if e["seat5"] == x]
        disp = a.home / ".ez2-aib" / "identity" / "seat-display.json"
        cur = json.loads(disp.read_text())
        cur["display"][x]["persona_uid"] = "zzzz99"                     # 這一席換人格
        disp.write_text(json.dumps(cur))
        new = lambda: [e["npub"] for e in NK.sending_seats(a.env()) if e["seat5"] == x]  # noqa: E731
        self.assertTrue(a.wait_for(lambda: new() and new() != old, 30), (old, new()))
        self.assertTrue(a.wait_for(lambda: any(new()[0] in json.dumps(e) for e in self.relay.events), 30), "名冊沒換成新那把")
        act = [e for e in NK.read_map(a.env())["seats"] if e["seat5"] == x and e["status"] == "active"]
        self.assertEqual(len(act), 2, "舊人格那把還 active(換回來照用)⇒ 才分得出「同席第一把」")
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)
        return a, x, b, y

    def test_n1_send_with_current_persona_key(self):
        SB, GF = self.RT.SB, self.RT.GF
        a, x, b, y = self._switched()
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)
        self.assertEqual(res["stage"], "queued", res)

    def test_n2_presence_with_current_persona_key(self):
        # @abb89 #297 r1 m08:在線狀態也要用名冊那一把;用舊人格那把 ⇒ 好友驗不過、看不到我在線
        a, x, b, y = self._switched()
        self.assertEqual(a.cli("friend", "add", "%s/6ddf/%s" % (b.owner, y)).returncode, 0)
        world = lambda: {c["owner"]: c for c in json.loads(b.cli("world", "--json").stdout)["cabins"]}  # noqa: E731
        self.assertTrue(b.wait_for(lambda: (world().get(a.owner) or {}).get("presence_at"), 40), world())


class EnvSecs(unittest.TestCase):
    """#308 後續 r2(@abb89):秒數環境變數壞值 / 0 不准讓轉接程式起不來或節流失效。"""

    def test_e1_bad_or_zero(self):
        sys.path.insert(0, str(REPO / "src" / "cockpit"))
        import dm_global_files as GF
        f = lambda v: GF.env_secs("X", 60, {"X": v} if v is not None else {})   # noqa: E731
        self.assertEqual([f(v) for v in (None, "", "abc", "nan", "inf", "-5", "0", "0.2", "2", "90")],
                         [60.0, 60.0, 60.0, 60.0, 60.0, 1.0, 1.0, 1.0, 2.0, 90.0])
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import chat_bridge as CB, "
                            "dm_global_files as GF; print(GF.env_secs('AIB_CHAT_PUBLISH_EVERY', CB.PUBLISH_EVERY))",
                            str(REPO / "src" / "cockpit")], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, AIB_CHAT_PUBLISH_EVERY="abc", AIB_CHAT_RESUB_EVERY="abc"))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "60.0"), r.stderr)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(chat_nostr 只在 venv 裡 import 得到)")
    def test_e2_chat_nostr_imports_with_bad_value(self):
        for raw, want in (("abc", "30.0"), ("0", "1.0")):
            r = subprocess.run([VPY, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import chat_nostr as CN; print(CN.RESUB_EVERY)",
                                str(REPO / "src" / "cockpit")], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, AIB_CHAT_RESUB_EVERY=raw))
            self.assertEqual((r.returncode, r.stdout.strip()), (0, want), raw + ":" + r.stderr)


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

    def _cut(self, rel, needle, rep, cell, timeout=120):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, rep, tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=timeout)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k5_dedupe_by_persona(self):          # m02
        self._cut("src/cockpit/nostr_keys.py",
                  'and s5 not in out and s5 in want and want[s5] == e.get("persona_uid"):\n            out[s5] = e',
                  'and e.get("persona_uid") not in out and s5 in want and want[s5] == e.get("persona_uid"):\n            out[e.get("persona_uid")] = e',
                  "OneKeyPerSeat.test_s5_two_seats_same_persona")

    def test_k6_retired_counts(self):             # m04
        self._cut("src/cockpit/nostr_keys.py", '        if e.get("status") == "active" and s5 not in out', "        if s5 not in out",
                  "OneKeyPerSeat.test_s6_retired_never_sends")

    def test_k7_human_any_active(self):           # m09
        self._cut("src/cockpit/chat_human.py", 'keyed = {e["seat5"] for e in NK.sending_seats(env)}',
                  'keyed = {e["seat5"] for e in NK.read_map(env)["seats"] if e.get("status") == "active"}',
                  "OneKeyPerSeat.test_s7_human_pick_needs_current_key")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k9_presence_first_active(self):      # m08
        self._cut("src/cockpit/chat_nostr.py",
                  'mine_seats = [e for e in NK.sending_seats() if e["seat5"] in pub]',
                  'mine_seats = [e for e in NK.read_map()["seats"] if e.get("status") == "active" and e["seat5"] in pub]',
                  "SendAfterSwitch.test_n2_presence_with_current_persona_key", timeout=300)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k8_sender_first_active(self):        # m07
        self._cut("src/cockpit/chat_nostr.py",
                  'entry = next((e for e in NK.sending_seats() if e["seat5"] == frm["seat"]), None)',
                  'entry = next((e for e in NK.read_map()["seats"] if e.get("status") == "active" and e["seat5"] == frm["seat"]), None)',
                  "SendAfterSwitch.test_n1_send_with_current_persona_key", timeout=300)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_fallback(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", "        fresh = await self._first_seat_addr(owner, roster=True)\n",
                        "        fresh = None\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Stale.test_p1_stale_falls_back_to_roster"],
                            cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k10_network_error_falls_back(self):  # #308 L2
        self._cut("src/cockpit/chat_nostr.py",
                  '        if rc == 0 or (addr and res.get("why") not in ("no_seat", "no_dm_relays")):\n',
                  "        if rc == 0:\n", "Stale.test_p4_network_error_no_roster")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _on_term)
    unittest.main()
