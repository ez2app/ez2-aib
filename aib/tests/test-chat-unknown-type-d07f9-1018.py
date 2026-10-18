#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""收件端遇到不認得的 aib-type ⇒ 不進 AI 框(10-17 Mini 的島快照 aib-type=space 被 Pro 上還沒重開的舊轉接程式當一般信,
排進 @d07f9 待審框 q_2a25da08249e9f98 等三封)。

核心(一定跑,只用標準庫):
T1 inbound_kind:認得的型別照原樣;沒有 aib-type(非 aib 寄件人)⇒ dm;不認得的 ⇒ unknown
連 Nostr(要 AIB_TEST_NOSTR_VENV;沒有就跳過):
T2 同一位主人的兩席:寄一則 aib-type=future-x、一則 aib-type=dm ⇒ 收件席佇列只有 dm 那則;audit 記 unknown-type:future-x
刀(複本):K1 inbound_kind 不認得也照回 ⇒ T1 紅;K2 on_dm 拿掉 unknown 那段 ⇒ T2 紅(有 nostr-sdk 才跑)
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_ut", str(HERE / "test-chat-recv-d07f9-1016.py"))
RB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RB)
HAVE_NOSTR, NOSTR_VENV = RB.HAVE_NOSTR, RB.NOSTR_VENV

# 用寄件席自己的金鑰,帶指定 aib-type 寄一則私訊(模擬比這台新的版本)
INJECT = ("import nostr_sdk as N,sys,asyncio,json,os\n"
          "sys.path.insert(0,sys.argv[1])\n"
          "import nostr_keys as NK\n"
          "a=json.loads(sys.argv[2])\n"
          "k=N.Keys.parse(NK.load_secret(a['key']))\n"
          "t=[N.Tag.parse(v) for v in (['aib-v','1'],['aib-type',a['typ']],['aib-msgid',a['msgid']],"
          "['aib-from',a['from']],['aib-to',a['to']])]\n"
          "ev=N.nip17_make_private_msg(k,N.PublicKey.parse(a['to_npub']),a['text'],rumor_extra_tags=t)\n"
          "async def m():\n c=N.Client();await c.add_relay(N.RelayUrl.parse(a['relay']));await c.connect()\n"
          " await c.send_event(ev);await c.shutdown()\n"
          "asyncio.run(m())\n")


class Pure(unittest.TestCase):
    def test_t1_inbound_kind(self):
        for typ in ("dm", "friend-request", "presence", "card", "space", "offer"):
            self.assertEqual(GF.inbound_kind({"aib-v": "1", "aib-type": typ}), typ)
        self.assertEqual(GF.inbound_kind({}), "dm", "非 aib 寄件人照舊當一般信")
        self.assertEqual(GF.inbound_kind({"aib-v": "1", "aib-type": "future-x"}), "unknown")
        self.assertEqual(GF.inbound_kind({"aib-type": ""}), "unknown")


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    # 只借收件測試的沙盒工具,不繼承它的 N1–N5(不然會重跑一遍)
    _box, setUp, items = RB.Network._box, RB.Network.setUp, RB.Network.items

    def inject(self, b, src, dst, typ, text):
        m = NK.read_map(b.env())
        ent = {e["seat5"]: e for e in m["seats"]}
        msgid = GF.new_msgid()
        arg = {"key": ent[src]["key"], "typ": typ, "msgid": msgid, "text": text, "relay": self.relay.url,
               "from": "%s/6ddf/%s" % (b.owner, src), "to": "%s/6ddf/%s" % (b.owner, dst), "to_npub": ent[dst]["npub"]}
        r = subprocess.run([str(Path(NOSTR_VENV) / "bin" / "python"), "-c", INJECT, str(REPO / "src" / "cockpit"),
                            json.dumps(arg)], env=b.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return msgid

    def audit_rows(self, b):
        rows = []
        for p in sorted((b.root / "audit").glob("*.jsonl")) if (b.root / "audit").is_dir() else []:
            rows += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        return rows

    def test_t2_unknown_type_not_queued(self):
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True, True])
        fut = self.inject(a, x, y, "future-x", "新版才有的東西")
        ok = self.inject(a, x, y, "dm", "一般信")
        self.assertTrue(a.wait_for(lambda: any(i.get("global", {}).get("msgid") == ok or "一般信" in (i.get("msg") or "")
                                               for i in self.items(a, y)), 30), "對照組:一般信要進佇列(注入路徑有效)")
        self.assertTrue(a.wait_for(lambda: any(r["msgid"] == fut for r in self.audit_rows(a)), 10))
        msgs = [i.get("msg") or "" for i in self.items(a, y)]
        self.assertFalse(any("新版才有的東西" in s for s in msgs), msgs)
        row = [r for r in self.audit_rows(a) if r["msgid"] == fut][0]
        self.assertEqual((row["dir"], row["result"]), ("in", "unknown-type:future-x"))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_kind_passthrough(self):
        self._knife("src/cockpit/dm_global_files.py", 'return typ if typ in INBOUND_TYPES else "unknown"', "return typ",
                    "Pure.test_t1_inbound_kind")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_on_dm_no_unknown_branch(self):
        self._knife("src/cockpit/chat_nostr.py", '        if kind == "unknown":\n', '        if False:\n',
                    "Network.test_t2_unknown_type_not_queued")


if __name__ == "__main__":
    unittest.main()
