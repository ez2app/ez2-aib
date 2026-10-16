#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話包 H(開發文件第 28.6 第三站實測):aib chat relays test <站>。

H1 不合格的站位址 ⇒ rc 2、什麼都不連;沒裝 nostr-sdk ⇒ rc 2 照實說
連 Nostr(要 AIB_TEST_NOSTR_VENV;**只連本機假中繼站**):
H2 本機假站 ⇒ rc 0,印出連線 / 確認 / 查詢毫秒;站只收到**一則** kind 20001(拋棄式金鑰、不帶我們的任何金鑰)
H3 連不上的埠 ⇒ rc 2「不能用」
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src" / "cockpit"))
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_ph", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)


class Probe(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)

    def test_h1_bad_url_and_no_venv(self):
        for bad in ("http://relay.example.com", "relay.example.com", "ws://relay.example.com"):
            r = self.b.cli("relays", "test", bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("wss://", r.stdout)
        r = self.b.cli("relays", "test", "wss://relay.example.com")
        self.assertEqual(r.returncode, 2)
        self.assertIn("nostr-sdk", r.stdout)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_h2_h3_fake_relay(self):
        from aib_fake_relay import FakeRelay
        self.b.chat.mkdir(parents=True, exist_ok=True)
        os.symlink(SB.NOSTR_VENV, str(self.b.chat / "venv"))
        self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.b.cli("prepare", "--keys-only", "--quiet")
        mine = {e["npub"] for e in NK.read_map(self.b.env())["seats"]} | {NK.read_map(self.b.env())["owner"]["npub"]}
        with FakeRelay() as relay:
            r = self.b.cli("relays", "test", relay.url)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("確認", r.stdout)
            self.assertEqual([e.get("kind") for e in relay.received], [20001], "只准發一則 ephemeral")
            authors = {NK.npub(e["pubkey"]) for e in relay.received}
            self.assertFalse(authors & mine, "不准用我們的任何金鑰")
            rec = json.loads(self.b.cli("relays", "test", relay.url, "--json").stdout)
            self.assertTrue(rec["ok"] and isinstance(rec["ok_ms"], int))
        r = self.b.cli("relays", "test", "ws://127.0.0.1:1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("不能用", r.stdout)


if __name__ == "__main__":
    unittest.main()
