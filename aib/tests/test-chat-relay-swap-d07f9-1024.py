#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""中繼站自動換(10-24 Andy 選):一站連續 30 分鐘連不上、別站連得上 ⇒ 換備用站;24 小時後換回再試。

S1 連不上未滿 30 分 ⇒ 不換;滿 30 分、別站連得上 ⇒ 換成第一個備用站,relays() 回換過的清單
S2 全部連不上(多半是自己斷網)⇒ 不換
S3 主人自己設的清單(aib chat relays add)⇒ 不換
S4 換過滿 24 小時 ⇒ relays() 回原站
S5 換上的備用站也掛了 ⇒ 同一個原站改指下一個備用站;備用站用完 ⇒ 不再換
S6 中間連上過一次 ⇒ 從頭算
連 Nostr(要 AIB_TEST_NOSTR_VENV;兩個本機假中繼站 + 一個關著的埠):
N1 開著的站 + 關著的站 ⇒ 幾秒後(測試把門檻調短)換成備用站,名冊發到備用站;aib chat relays --json 列出 swaps
刀(複本):K1 全斷也換 ⇒ S2 紅;K2 不看主人自訂 ⇒ S3 紅;K3 轉接程式不檢查 ⇒ N1 紅
"""
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_swap", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
A, B, C = CR.DEFAULT_RELAYS
X, Y = "wss://backup-x.example", "wss://backup-y.example"
UP, DOWN = "CONNECTED", "DISCONNECTED"
T0 = __import__("time").time().__int__() - 20000


class Unit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.env = {"HOME": self.td.name, "AIB_CHAT_BACKUP_RELAYS": "%s,%s" % (X, Y)}

    def tick(self, seen, t):
        return CR.check_relay_swap(seen, self.env, now=T0 + t)   # t = 測試裡的相對秒數(換過的站 24 小時內才算數)

    def test_s1_swap_after_30min(self):
        seen = {A: UP, B: UP, C: DOWN}
        self.assertEqual(self.tick(seen, 1000), [])
        self.assertEqual(self.tick(seen, 1000 + 1799), [])
        self.assertEqual(self.tick(seen, 1000 + 1800), [(C, X)])
        self.assertEqual(CR.relays(self.env), [A, B, X])

    def test_s2_all_down_no_swap(self):
        seen = {A: DOWN, B: DOWN, C: DOWN}
        self.tick(seen, 1000)
        self.assertEqual(self.tick(seen, 1000 + 99999), [])
        self.assertEqual(CR.relays(self.env), [A, B, C])

    def test_s3_custom_list_untouched(self):
        self.assertEqual(CR.set_profile("Andy", "TW", self.env) is None or True, True)
        CR.set_relays("add", "wss://mine.example", self.env)
        cur = CR.relays(self.env)
        seen = dict.fromkeys(cur, UP)
        seen[cur[0]] = DOWN
        self.tick(seen, 1000)
        self.assertEqual(self.tick(seen, 1000 + 99999), [])
        self.assertEqual(CR.relays(self.env), cur)

    def test_s4_expires(self):
        seen = {A: UP, B: UP, C: DOWN}
        self.tick(seen, 1000)
        self.tick(seen, 3000)
        import time
        self.assertEqual(CR.relays(self.env), [A, B, X])
        p = Path(self.td.name) / ".ez2-aib" / "chat" / "relay-swap.json"
        cur = json.loads(p.read_text())
        cur["swaps"][C]["at"] = int(time.time()) - 86401
        p.write_text(json.dumps(cur))
        self.assertEqual(CR.relays(self.env), [A, B, C])

    def test_s5_backup_dies_too(self):
        self.tick({A: UP, B: UP, C: DOWN}, 1000)
        self.assertEqual(self.tick({A: UP, B: UP, C: DOWN}, 3000), [(C, X)])
        self.tick({A: UP, B: UP, X: DOWN}, 4000)
        self.assertEqual(self.tick({A: UP, B: UP, X: DOWN}, 6000), [(X, Y)])
        self.assertEqual(CR.relays(self.env), [A, B, Y])
        self.assertEqual(list(CR.read_swaps(self.env)), [C], "還是記在原站底下(24 小時後換回原站)")
        self.tick({A: UP, B: UP, Y: DOWN}, 7000)
        self.assertEqual(self.tick({A: UP, B: UP, Y: DOWN}, 9000), [], "備用站用完 ⇒ 不再換")

    def test_s6_reconnect_resets(self):
        self.tick({A: UP, B: UP, C: DOWN}, 1000)
        self.tick({A: UP, B: UP, C: UP}, 2000)
        self.tick({A: UP, B: UP, C: DOWN}, 2500)
        self.assertEqual(self.tick({A: UP, B: UP, C: DOWN}, 1000 + 1800), [], "中間連上過 ⇒ 從 2500 重算")


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.r1, self.r2 = FakeRelay(), FakeRelay()
        for r in (self.r1, self.r2):
            r.__enter__()
            self.addCleanup(r.__exit__, None, None, None)

    def test_n1_swap_live(self):
        dead = "ws://127.0.0.1:%d" % _closed_port()
        b = SB.Box([self.r1.url, dead])
        b.extra.update({"AIB_CHAT_BACKUP_RELAYS": self.r2.url, "AIB_CHAT_SWAP_AFTER": "3"})
        self.addCleanup(b.close)
        b.chat.mkdir(parents=True, exist_ok=True)
        import os
        os.symlink(SB.NOSTR_VENV, str(b.chat / "venv"))
        code = b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        b.cli("publish", code)
        b.start_run()
        self.assertTrue(b.wait_for(lambda: CR.read_swaps(b.env()), 60), (b.status(), (b.root / "worker-status.json").read_text()
                                                                         if (b.root / "worker-status.json").exists() else None))
        self.assertEqual(CR.read_swaps(b.env())[dead]["to"], self.r2.url)
        self.assertTrue(b.wait_for(lambda: any(e["kind"] == 30000 or e["kind"] == 10050 or e["kind"] >= 30000
                                               for e in self.r2.events), 60), self.r2.events[-3:])
        got = json.loads(b.cli("relays", "--json").stdout)
        self.assertEqual([s["to"] for s in got["swaps"]], [self.r2.url])
        self.assertIn(self.r2.url, [r["url"] for r in got["relays"]])
        self.assertNotIn(dead, [r["url"] for r in got["relays"]])


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, rep, cell, nostr=False):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, rep,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_swap_when_all_down(self):
        self._knife("src/cockpit/chat_roster.py", '    if not any(seen.get(u) == "CONNECTED" for u in cur):\n        return []',
                    "    if False:\n        return []", "Unit.test_s2_all_down_no_swap")

    def test_k2_ignore_custom(self):
        self._knife("src/cockpit/chat_roster.py",
                    '    if not isinstance(seen, dict) or (read_profile(env) or {}).get("relays") and not env.get("AIB_CHAT_RELAYS"):',
                    "    if not isinstance(seen, dict):", "Unit.test_s3_custom_list_untouched")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_bridge_no_check(self):
        self._knife("src/cockpit/chat_bridge.py", "        _relay_swap_tick(root, env, now)\n", "        pass\n",
                    "Network.test_n1_swap_live")


if __name__ == "__main__":
    unittest.main()
