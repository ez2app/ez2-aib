#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib chat recent --json(新艦長室視訊螢幕 / 收發室要的;@74e8b 10-19)。只讀 audit,不含內容。

R1 最近 N 分鐘的線上收(寄件席 npub ⇒ 主人)、線上寄(位址 ⇒ 主人,同一封只留最後階段)、Telegram 收寄
R2 太舊的、認不出主人的、presence / space 這類不是私訊的都不列;輸出沒有內容欄
刀(複本):K1 寄出不去重 ⇒ R1 紅
"""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

OWNER = NK.npub(NK.pubkey_hex("%064x" % 0x3201))
SEATN = NK.npub(NK.pubkey_hex("%064x" % 0x3202))
STRANGE = NK.npub(NK.pubkey_hex("%064x" % 0x3203))


class Recent(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-recent-")
        self.addCleanup(td.cleanup)
        home = Path(td.name) / "home"
        home.mkdir()
        self.root = Path(td.name) / "bridge"
        self.env = {"HOME": str(home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW"}
        GF.ensure_dirs(self.root)
        GF.note_peer(self.root, OWNER, "Pro 13", "TW", seat={"cabin": "5a97", "seat": "4345c", "npub": SEATN})
        self.now = time.time()
        rows = [(-30, "in", "m1", SEATN, "inbox"),
                (-20, "out", "m2", OWNER + "/5a97/4345c", "relayed"),
                (-19, "out", "m2", OWNER + "/5a97/4345c", "failed"),
                (-15, "tg-in", "-", "telegram:70001", "queued_room"),
                (-10, "tg-out", "-", "telegram:70001", "sent"),
                (-3600, "in", "m0", SEATN, "inbox"),
                (-5, "in", "m3", STRANGE, "inbox"),
                (-5, "presence-out", "-", OWNER, "online"),
                (-5, "space-in", "-", SEATN, "room")]
        (self.root / "audit").mkdir(exist_ok=True)
        with open(self.root / "audit" / (time.strftime("%Y%m%d") + ".jsonl"), "w", encoding="utf-8") as f:
            for dt, d, mid, peer, res in rows:
                f.write(json.dumps({"v": 1, "at": int(self.now + dt), "dir": d, "msgid": mid, "peer": peer,
                                    "result": res}) + "\n")

    def test_r1_rows(self):
        got = [(r["dir"], r["net"], r["contact_key"], r["result"])
               for r in CB.recent_payload(self.env, now=self.now, minutes=10)["rows"]]
        self.assertEqual(got, [("in", "nostr", "global:" + OWNER, "inbox"),
                               ("out", "nostr", "global:" + OWNER, "failed"),
                               ("in", "telegram", "telegram:70001", "queued_room"),
                               ("out", "telegram", "telegram:70001", "sent")])

    def test_r2_no_content_keys(self):
        for r in CB.recent_payload(self.env, now=self.now, minutes=10)["rows"]:
            self.assertEqual(set(r), {"at", "dir", "net", "contact_key", "result"})


class Knife(unittest.TestCase):
    def test_k1_no_dedupe(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", "                    out_last[r.get(\"msgid\")] = ",
                        "                    out_last[len(out_last)] = ", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Recent.test_r1_rows"],
                            cwd=str(root), max_procs=60, timeout=200)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
