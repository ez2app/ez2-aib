#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""判斷對方那台電腦是不是在睡 + 撐著不睡 + 寄件提示 + 24 小時未確認 + 自己的機器版本落後
(10-18 Andy「全做,而且要能判斷該電腦休眠」;起因:Mac Pro 13 閒置 1 分鐘就睡、womp 0)。

S1 讀 pmset:` sleep  1 (sleep prevented by powerd)` ⇒ 1;'AC Power' / 'Battery Power'
S2 這台:會睡(sleep 1、沒撐)⇒ sleeps True;接電源 + caffeinate 活著 ⇒ False;用電池就算撐著也 True;非 Mac ⇒ None
S3 睡醒偵測:兩輪之間牆上時鐘跳超過 90 秒 ⇒ 記一段 {from, to}、last_wake;沒跳 ⇒ 不記
S4 看別人:beat 1.5 倍 + 2 分鐘內收到過 ⇒ awake;過了而對方說會睡 ⇒ asleep(since = 最後收到);說不會睡 ⇒ offline;
   沒有 power 欄(舊版)⇒ unknown;同一位主人有一台醒著 ⇒ awake;收到別人的 power 欄只留認得的欄位與型別
S5 寄件結果:對方在睡、信還沒送到 ⇒ detail 補「在睡(… 起沒動靜),信在中繼站等」;已送到 ⇒ 不補;離線 ⇒ 補「離線」
S6 交出超過 24 小時還沒 delivered / read / 拒收 ⇒ 補一張 unconfirmed(只補一次);有 delivered 的不補;還沒滿 24 小時不補
S7 world / contacts 每位帶 machine;status:這台會睡 ⇒ 提醒;自己的其他機器版本比這台舊 ⇒ 提醒(好友不提醒)
S8 撐著不睡:Mac + 設定 on ⇒ 起 caffeinate -s -w <自己>、記 pid、kept_awake;停掉 ⇒ 不再 kept_awake;設定 off ⇒ 不起
N1(要 AIB_TEST_NOSTR_VENV;借 pack-g 的兩台好友沙盒)A 的在線狀態經假中繼站送到 B:world.json 那一格帶 power(beat、sleeps…),
   B 的 world --json 那位 machine = awake
S9(10-25 Mac Pro 13)從沒收過對方收下的回條(queued / held / delivered / read)⇒ 不推斷離線,改說「可能還沒把你加成好友」
   + aib chat connect;睡 / 離線仍當可能之一;別人的回條不算;對方回 queued 不補;收下過 ⇒ 照舊推斷
刀(複本):K1 太久沒收到也當醒著 ⇒ S4 紅;K2 寄件結果不補對方狀態 ⇒ S5 紅;K3 一律當收下過 ⇒ S9 紅
"""
import importlib.util
import json
import os
import shutil
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
import chat_power as CP  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

OWNER = NK.npub(NK.pubkey_hex("%064x" % 0x7001))
PMSET_G = " standby              1\n powernap             1\n sleep                1 (sleep prevented by powerd)\n displaysleep 30\n"
NOW = 1_800_000_000


def fake_pmset(sleep="1", ac=True):
    def get(*args):
        if args == ("-g",):
            return PMSET_G.replace("sleep                1", "sleep                %s" % sleep)
        return "Now drawing from '%s'\n" % ("AC Power" if ac else "Battery Power")
    return get


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-sleep-")
        self.root = Path(self.td.name) / "bridge"
        GF.ensure_dirs(self.root)
        self.home = Path(self.td.name) / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW"}

    def tearDown(self):
        self.td.cleanup()


class Self(Box):
    def test_s1_parse(self):
        self.assertEqual(CP.parse_sleep_min(PMSET_G), 1)
        self.assertEqual(CP.parse_sleep_min(" sleep 0\n"), 0)
        self.assertIsNone(CP.parse_sleep_min("nothing"))
        self.assertIs(CP.parse_ac("Now drawing from 'AC Power'"), True)
        self.assertIs(CP.parse_ac("Now drawing from 'Battery Power'"), False)
        self.assertIsNone(CP.parse_ac(""))

    def test_s2_power_info(self):
        self.assertIs(CP.power_info(self.root, "darwin", fake_pmset())["sleeps"], True)
        self.assertIs(CP.power_info(self.root, "darwin", fake_pmset(sleep="0"))["sleeps"], False)
        GF.write_json(str(CP.keep_path(self.root)), {"pid": os.getpid()})
        info = CP.power_info(self.root, "darwin", fake_pmset())
        self.assertEqual((info["sleeps"], info["kept_awake"]), (False, True), "接電源 + 撐著 ⇒ 不會睡")
        self.assertIs(CP.power_info(self.root, "darwin", fake_pmset(ac=False))["sleeps"], True, "用電池撐不住")
        self.assertIsNone(CP.power_info(self.root, "linux")["sleeps"], "非 Mac 不亂講")

    def test_s3_sleep_watch(self):
        w = CP.SleepWatch(self.root)
        self.assertFalse(w.tick(NOW))
        self.assertFalse(w.tick(NOW + 30))
        self.assertTrue(w.tick(NOW + 30 + 3600))
        rec = CP.read_power(self.root)
        self.assertEqual((rec["spans"][-1], rec["last_wake"]), ({"from": NOW + 30, "to": NOW + 3630}, NOW + 3630))
        self.assertFalse(w.tick(NOW + 3640))


class Others(Box):
    def cell(self, age, power):
        c = {"owner": OWNER, "cabin": "5a97", "presence_at": NOW - age, "seats": []}
        if power is not None:
            c["power"] = power
        return c

    def test_s4_machine_state(self):
        sleeps = {"sleeps": True, "sleep_min": 1, "ac": True, "kept_awake": False, "last_wake": NOW - 100, "beat": 900}
        st = CP.machine_state(self.cell(60, sleeps), NOW)
        self.assertEqual((st["state"], st["since"]), ("awake", NOW - 100))
        st = CP.machine_state(self.cell(3600, sleeps), NOW)
        self.assertEqual((st["state"], st["since"]), ("asleep", NOW - 3600))
        st = CP.machine_state(self.cell(3600, dict(sleeps, sleeps=False, kept_awake=True)), NOW)
        self.assertEqual(st["state"], "offline", "說自己不會睡卻沒動靜 ⇒ 不正常")
        self.assertEqual(CP.machine_state(self.cell(3600, None), NOW)["state"], "unknown", "舊版沒有 power 欄")
        self.assertEqual(CP.owner_state([self.cell(3600, sleeps), self.cell(30, sleeps)], NOW)["state"], "awake")
        self.assertEqual(CP.clean_power({"sleeps": "yes", "sleep_min": -3, "beat": 0, "x": 1}),
                         {"sleeps": None, "ac": None, "kept_awake": None, "sleep_min": None, "last_wake": None, "beat": None})

    def test_s5_send_hint(self):
        GF.note_world(self.root, OWNER, "5a97", [], now=NOW - 3600,
                      power={"sleeps": True, "sleep_min": 1, "ac": True, "kept_awake": False, "last_wake": None, "beat": 900})
        rec = {"op": "send", "to": "%s/5a97/4345c" % OWNER}
        self.sent_to(OWNER, "%032x" % 9, ["relayed", "delivered"])    # 以前收過對方收下的回條 ⇒ 才推斷睡 / 離線
        res = GF._with_peer(self.root, rec, {"rc": 4, "stage": "relayed", "pos": None, "detail": "已交給轉接程式", "msgid": "m"}, NOW)
        self.assertIn("在睡", res["detail"])
        self.assertEqual(res["peer"]["state"], "asleep")
        res = GF._with_peer(self.root, rec, {"rc": 0, "stage": "delivered", "pos": None, "detail": "", "msgid": "m"}, NOW)
        self.assertNotIn("在睡", res["detail"], "已送到就不必說")
        GF.note_world(self.root, OWNER, "5a97", [], now=NOW - 3600,
                      power={"sleeps": False, "sleep_min": 1, "ac": True, "kept_awake": True, "last_wake": None, "beat": 900})
        res = GF._with_peer(self.root, rec, {"rc": 4, "stage": "relayed", "pos": None, "detail": "", "msgid": "m"}, NOW)
        self.assertIn("離線", res["detail"])

    def sent_to(self, owner, mid, stages, at=NOW - 7200):
        GF.write_json(str(self.root / "sent" / (mid + ".json")), {"v": 1, "to": "x", "at": at, "owner": owner})
        for st in stages:
            GF.append_receipt(self.root, mid, {"v": 1, "at": at, "stage": st})

    def test_s9_not_friend_is_not_offline(self):
        """10-25 Mac Pro 13:對方其實沒加我(拒收、不回條),這邊卻寫「對方離線」⇒ 人跑去查轉接程式"""
        GF.note_world(self.root, OWNER, "5a97", [], now=NOW - 3600,
                      power={"sleeps": False, "sleep_min": 1, "ac": True, "kept_awake": True, "last_wake": None, "beat": 900})
        rec = {"op": "send", "to": "%s/5a97/4345c" % OWNER}
        self.sent_to(OWNER, "%032x" % 11, ["relayed"])
        self.sent_to(OWNER, "%032x" % 12, ["relayed", "unconfirmed"])
        other = NK.npub(NK.pubkey_hex("%064x" % 0x7002))
        self.sent_to(other, "%032x" % 13, ["relayed", "delivered"])     # 別人收下的不算
        res = GF._with_peer(self.root, rec, {"rc": 4, "stage": "relayed", "pos": None, "detail": "已交給轉接程式", "msgid": "m"}, NOW)
        self.assertNotIn("對方那台電腦離線", res["detail"])
        self.assertIn("還沒把你加成好友", res["detail"])
        self.assertIn("aib chat connect", res["detail"])
        self.assertIn("不在線", res["detail"], "睡 / 離線仍當可能之一講")
        self.assertEqual((res["peer"]["why"], res["peer"]["accepted"]), ("not_confirmed", False))
        self.assertTrue(res["detail"].startswith("已交給轉接程式;"), "原本的 detail 不改")
        res = GF._with_peer(self.root, {"op": "send", "to": "%s/9999/aaaaa" % other},
                            {"rc": 4, "stage": "relayed", "pos": None, "detail": "", "msgid": "m"}, NOW)
        self.assertNotIn("加成好友", res["detail"])
        stranger = NK.npub(NK.pubkey_hex("%064x" % 0x7003))             # 沒任何狀態也沒回條
        res = GF._with_peer(self.root, {"op": "send", "to": "%s/9999/bbbbb" % stranger},
                            {"rc": 4, "stage": "relayed", "pos": None, "detail": "", "msgid": "m"}, NOW)
        self.assertIn("還沒把你加成好友", res["detail"])
        self.assertIn("沒收到對方任何狀態", res["detail"])
        res = GF._with_peer(self.root, {"op": "send", "to": "%s/9999/bbbbb" % stranger},
                            {"rc": 4, "stage": "queued", "pos": None, "detail": "", "msgid": "m"}, NOW)
        self.assertNotIn("加成好友", res["detail"], "對方回 queued = 收下了")
        for st in ("queued", "held", "read"):
            o = NK.npub(NK.pubkey_hex("%064x" % (0x7100 + len(st))))
            self.sent_to(o, "%032x" % (0x100 + len(st)), ["relayed", st])
            self.assertTrue(GF.peer_ever_accepted(self.root, o), st)
        self.sent_to(OWNER, "%032x" % 14, ["relayed", "delivered"])
        res = GF._with_peer(self.root, rec, {"rc": 4, "stage": "relayed", "pos": None, "detail": "", "msgid": "m"}, NOW)
        self.assertIn("對方那台電腦離線", res["detail"], "收下過 ⇒ 照舊推斷離線")
        self.assertNotIn("加成好友", res["detail"])

    def test_s6_unconfirmed(self):
        def sent(mid, at, stages):
            GF.write_json(str(self.root / "sent" / (mid + ".json")), {"v": 1, "to": "x", "at": at})
            for st in stages:
                GF.append_receipt(self.root, mid, {"v": 1, "at": at, "stage": st})
        a, b, c, d = (("%032x" % i) for i in (1, 2, 3, 4))
        sent(a, NOW - 90000, ["relayed"])
        sent(b, NOW - 90000, ["relayed", "queued"])
        sent(c, NOW - 90000, ["relayed", "delivered"])
        sent(d, NOW - 100, ["relayed"])
        self.assertEqual(GF.mark_unconfirmed(self.root, now=NOW), 2)
        self.assertEqual([r["stage"] for r in GF.read_receipts(self.root, b)][-1], "unconfirmed")
        self.assertEqual(GF.read_receipts(self.root, b)[-1].get("detail"), "queued")
        self.assertEqual(GF.mark_unconfirmed(self.root, now=NOW + 700), 0, "只補一次")

    def test_s7_payloads_and_status(self):
        CR.add_friend(OWNER, "Mac Pro 13", env=self.env, kind="mine")
        GF.note_world(self.root, OWNER, "5a97", [{"seat": "4345c", "state": "idle"}], now=int(time.time()) - 3600,
                      power={"sleeps": True, "sleep_min": 1, "ac": True, "kept_awake": False, "last_wake": None, "beat": 900})
        GF.note_peer(self.root, OWNER, "Mac Pro 13", "TW", claims={"nip05": None, "aib": "0.1.18"})
        rows = {r["owner"]: r for r in CB.contacts_payload(self.env)["contacts"]}
        self.assertEqual(rows[OWNER]["machine"]["state"], "asleep")
        cab = next(c for c in CB.world_payload(self.env)["cabins"] if c["owner"] == OWNER)
        self.assertEqual(cab["machine"]["state"], "asleep")
        orig = CP.power_info
        CP.power_info = lambda root: {"sleeps": True, "sleep_min": 1, "ac": True, "kept_awake": False}
        try:
            lines = CB.power_lines(self.env)
        finally:
            CP.power_info = orig
        self.assertTrue(any("1 分鐘就睡" in x for x in lines), lines)
        self.assertTrue(any("Mac Pro 13" in x and "0.1.18" in x for x in lines), "自己的機器版本落後要提醒")


@unittest.skipUnless(sys.platform == "darwin" and os.path.exists(CP.CAFFEINATE), "只在 Mac")
class Keep(Box):
    def test_s8_keep_awake(self):
        home = self.home / ".ez2-aib" / "chat"
        p = CP.start_keep_awake(self.root, home)
        self.addCleanup(CP.stop_keep_awake, self.root, p)
        self.assertIsNotNone(p)
        self.assertTrue(CP.kept_awake(self.root))
        CP.stop_keep_awake(self.root, p)
        self.assertFalse(CP.kept_awake(self.root))
        CP.set_awake(home, False)
        self.assertIsNone(CP.start_keep_awake(self.root, home), "設定 off ⇒ 不起")


_spec = importlib.util.spec_from_file_location("pack_g_t", str(HERE / "test-chat-pack-g-d07f9-1016.py"))
PG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PG)


@unittest.skipUnless(PG.SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(PG.Network):
    """只跑這一格;pack-g 自己那幾格不在這裡重跑。"""
    test_n1_friends_see_presence = test_n2_not_friend_dropped = None

    def test_n1_power_travels(self):
        a, x, b, y = self.pair()
        self.assertTrue(b.wait_for(lambda: a.owner in self.world(b), 40), GF.read_world(b.root))
        cell = next(c for c in GF.read_world(b.root).values() if c["owner"] == a.owner)
        self.assertIn("power", cell, "在線狀態要帶電源欄")
        self.assertIsInstance(cell["power"]["beat"], int, cell["power"])
        self.assertEqual(self.world(b)[a.owner]["machine"]["state"], "awake")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name, "test-chat-pack-g-d07f9-1016.py", "test-chat-bridge-d07f9-1015.py",
                                                        "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300,
                            env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_stale_is_awake(self):
        self._knife("src/cockpit/chat_power.py", "    if now - at <= beat * 1.5 + 120:\n", "    if True:\n",
                    "Others.test_s4_machine_state")

    def test_k3_always_accepted(self):
        self._knife("src/cockpit/dm_global_files.py", "        st[\"accepted\"] = peer_ever_accepted(root, owner)\n",
                    "        st[\"accepted\"] = True\n", "Others.test_s9_not_friend_is_not_offline")

    def test_k2_no_hint(self):
        self._knife("src/cockpit/dm_global_files.py", "    res[\"peer\"] = st\n", "    return res\n",
                    "Others.test_s5_send_hint")


if __name__ == "__main__":
    unittest.main()
