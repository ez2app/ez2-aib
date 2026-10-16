#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談先備好(維護者 10-16:安裝時就裝好 Nostr 的程式、第一艦長開席就有身分),但**不上線**。

P1 aib chat prepare --keys-only:主人金鑰與已綁席的席金鑰產生了;開關檔、心跳、名冊、主人資料都沒有(沒上線、沒公開任何東西)
P2 AIB_CHAT_PREINSTALL=0 ⇒ 什麼都不做
P3 venv 已在(有 nostr-sdk)⇒ prepare 說已備好、不重裝;沒有 venv 也沒網路 ⇒ 說沒裝成、rc 0(不擋安裝)
P4 安裝與開艙有接上:install.sh 在 aib init 之後呼叫 chat prepare(失敗只記一行);cockpit-full.sh 背景 prepare --keys-only
刀(複本):K1 prepare 順手開開關 ⇒ P1 紅;K2 不理 AIB_CHAT_PREINSTALL ⇒ P2 紅
"""
import importlib.util
import json
import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_box2", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)


class Prepare(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)

    def test_p1_keys_only_stays_offline(self):
        x = self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "第一艦長")
        r = self.b.cli("prepare", "--keys-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((self.b.home / ".ez2-aib" / "identity" / "nostr-map.json").read_text())
        self.assertTrue(m["owner"]["npub"].startswith("npub1"))
        self.assertEqual([e["seat5"] for e in m["seats"] if e["status"] == "active"], [x])
        self.assertIn("aib chat on --name", r.stdout)
        for p in (self.b.chat / "enabled.json", self.b.chat / "profile.json", self.b.root / "heartbeat.json",
                  self.b.root / "roster" / "outgoing.json"):
            self.assertFalse(p.exists(), "prepare 不准上線或公開:%s" % p)
        self.assertEqual(self.b.cli("enabled").returncode, 1)

    def test_p2_preinstall_off(self):
        r = self.b.cli("prepare", "--keys-only", AIB_CHAT_PREINSTALL="0")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.b.home / ".ez2-aib" / "identity" / "nostr-map.json").exists())

    def test_p3_venv(self):
        if SB.HAVE_NOSTR:
            self.b.chat.mkdir(parents=True, exist_ok=True)
            os.symlink(SB.NOSTR_VENV, str(self.b.chat / "venv"))
            r = self.b.cli("prepare")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("nostr-sdk 已備好", r.stdout)
        # 沒網路:pip 指到不存在的索引 ⇒ 沒裝成,rc 0
        b2 = SB.Box()
        self.addCleanup(b2.close)
        r = b2.cli("prepare", PIP_INDEX_URL="http://127.0.0.1:9/simple", PIP_NO_CACHE_DIR="1", PIP_RETRIES="0",
                   PIP_TIMEOUT="2", PIP_FIND_LINKS="", PIP_NO_INDEX="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("沒裝成", r.stdout)
        self.assertTrue((b2.home / ".ez2-aib" / "identity" / "nostr-map.json").exists(), "沒裝成也要有本機金鑰")

    def test_p4_wired(self):
        inst = (REPO.parent / "install.sh").read_text(encoding="utf-8")
        i, j = inst.find('"$abin" init'), inst.find('"$abin" chat prepare')
        self.assertTrue(0 < i < j, "install.sh 要在 aib init 之後 chat prepare")
        self.assertIn('note+=("線上對話的 nostr-sdk 沒裝成', inst)
        cf = (REPO / "src" / "cockpit" / "cockpit-full.sh").read_text(encoding="utf-8")
        self.assertRegex(cf, re.compile(r'chat_bridge\.py" prepare --keys-only --quiet .*&'))


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", needle, replacement,
                        tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_prepare_turns_on(self):
        self._knife('''    acts, key_err = sync_keys(env)
    if key_err:
        out.write(T("keys_error", err=key_err) + "\\n")
    elif not a.quiet or acts:''', '''    write_json(str(switch_path(env)), {"v": VERSION, "on": True, "at": 0})
    acts, key_err = sync_keys(env)
    if key_err:
        out.write(T("keys_error", err=key_err) + "\\n")
    elif not a.quiet or acts:''', "Prepare.test_p1_keys_only_stays_offline")

    def test_k2_ignore_preinstall(self):
        self._knife('''    if (env.get("AIB_CHAT_PREINSTALL") or "1") == "0":''', "    if False:", "Prepare.test_p2_preinstall_off")


if __name__ == "__main__":
    unittest.main()
