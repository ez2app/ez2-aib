#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談改善包 D(開發文件第 27.7、27.9 節):自我檢查、自訂中繼站。

核心(一定跑):
L1 aib chat relays:沒主人資料 ⇒ rc 2;預設兩站;add wss:// 成功、非 wss ⇒ rc 2;最多 6 站;remove 最後一站 ⇒ rc 2;
   reset 回預設;名冊 dm_relays 跟著變;AIB_CHAT_RELAYS 仍優先
T1 selftest 新機器:rc 1,開關 / 名字 / nostr-sdk 都是 ✗ 並給指令;--json 每項有 item/ok
連 Nostr(要 AIB_TEST_NOSTR_VENV;**只連本機假中繼站**:預設的公開站在起轉接程式前就移掉):
T2 selftest 在設好的機器上:全過 rc 0(含公開席從中繼站查得到、10050 在)
R1 轉接程式跑著時加第二個中繼站 ⇒ 工人重連,狀態裡看得到新站連上;名冊重發到新站
刀(複本):K1 relays 收非 wss ⇒ L1 紅;K2 換站不重連 ⇒ R1 紅
"""
import importlib.util
import json
import os
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

_spec = importlib.util.spec_from_file_location("chat_recv_pd", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
DEFAULTS = ["wss://relay.primal.net", "wss://nos.lol", "wss://offchain.pub"]


def relays_of(b):
    return [r["url"] for r in json.loads(b.cli("relays", "--json").stdout)["relays"]]


class Relays(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)

    def test_l1_relays(self):
        b = self.b
        self.assertEqual(b.cli("relays", "add", "wss://relay.example.com").returncode, 2, "沒主人資料不准存")
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.assertEqual(relays_of(b), DEFAULTS)
        self.assertEqual(b.cli("relays", "add", "wss://relay.example.com/").returncode, 0)
        self.assertEqual(relays_of(b), DEFAULTS + ["wss://relay.example.com"])
        for bad in ("ws://relay.example.com", "https://x.com", "relay.example.com"):
            self.assertEqual(b.cli("relays", "add", bad).returncode, 2, bad)
        for i in range(6 - len(DEFAULTS) - 1):             # 補滿到 6 站(預設幾站跟著 DEFAULTS)
            self.assertEqual(b.cli("relays", "add", "wss://r%d.example.com" % i).returncode, 0)
        r = b.cli("relays", "add", "wss://r9.example.com")
        self.assertEqual(r.returncode, 2, "最多 6 站")
        self.assertIn("6", r.stdout)
        b.cli("publish", "xxxx11")
        b.cli("roster", "--json")
        import chat_roster as CR
        content, _ = CR.build(b.env())
        self.assertIn("wss://r1.example.com", content["dm_relays"])
        self.assertEqual(b.cli("relays", "reset").returncode, 0)
        self.assertEqual(relays_of(b), DEFAULTS)
        for r in DEFAULTS[:-1]:                              # 拿到只剩最後一站
            b.cli("relays", "remove", r)
        self.assertEqual(b.cli("relays", "remove", DEFAULTS[-1]).returncode, 2, "至少留一站")
        self.assertEqual(relays_of(b), [DEFAULTS[-1]])
        self.assertEqual(CR.relays(b.env(AIB_CHAT_RELAYS="ws://127.0.0.1:9")), ["ws://127.0.0.1:9"], "環境變數優先")

    def test_t1_selftest_fresh(self):
        r = self.b.cli("selftest")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("✗ 線上對話:未開通", r.stdout)
        self.assertIn("aib chat name", r.stdout)
        self.assertIn("aib chat prepare", r.stdout)
        rows = json.loads(self.b.cli("selftest", "--json").stdout)
        self.assertTrue(all("item" in x and "ok" in x for x in rows))


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_t2_selftest_all_ok(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        ok = a.wait_for(lambda: (a.status().get("state") == "published"
                                 and all(v == "CONNECTED" for v in (GF_status(a).get("relays") or {"x": 0}).values())), 40)
        self.assertTrue(ok, GF_status(a))
        a.wait_for(lambda: (GF_status(a).get("clock") or {}).get("offset") is not None, 10)
        r = a.cli("selftest")
        self.assertIn("✓ @%s 從公開站查得到" % x, r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout)
        bad = [ln for ln in r.stdout.splitlines() if ln.startswith("✗")]
        self.assertEqual(bad, [], r.stdout)

    def test_r1_add_relay_reconnects(self):
        from aib_fake_relay import FakeRelay
        second = FakeRelay().start()
        self.addCleanup(second.stop)
        b = SB.Box([])                                   # 不用環境變數:中繼站走主人資料(aib chat relays)
        self.addCleanup(b.close)
        b.chat.mkdir(parents=True, exist_ok=True)
        os.symlink(SB.NOSTR_VENV, str(b.chat / "venv"))
        y = b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Bob", "--country", "TW").returncode, 0)
        self.assertEqual(b.cli("relays", "add", self.relay.url).returncode, 0)
        for u in DEFAULTS:                               # 起轉接程式前先拿掉公開站:自動測試不准連公開站
            self.assertEqual(b.cli("relays", "remove", u).returncode, 0)
        self.assertEqual(relays_of(b), [self.relay.url])
        b.cli("publish", y)
        b.start_run()
        self.assertTrue(b.wait_for(lambda: (GF_status(b).get("relays") or {}).get(self.relay.url) == "CONNECTED", 40),
                        GF_status(b))
        self.assertEqual(b.cli("relays", "add", second.url).returncode, 0)
        self.assertTrue(b.wait_for(lambda: (GF_status(b).get("relays") or {}).get(second.url) == "CONNECTED", 60),
                        GF_status(b))
        self.assertTrue(b.wait_for(lambda: any(e.get("kind") == 30078 for e in second.events), 90), "名冊要重發到新站")


def GF_status(b):
    try:
        return json.loads((b.root / "worker-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_relay_any_scheme(self):
        self._knife("src/cockpit/chat_roster.py", "        if not _relay_ok(url):\n            raise RosterError(\"bad_relay\")\n",
                    "", "Relays.test_l1_relays")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_no_reconnect(self):
        self._knife("src/cockpit/chat_bridge.py", '        if _WORKER["relays"] is not None and _WORKER["relays"] != cur:',
                    "        if False:", "Network.test_r1_add_relay_reconnects")


if __name__ == "__main__":
    unittest.main()
