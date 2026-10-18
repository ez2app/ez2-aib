#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""回「主人本人從艦長室寄的」那封的線上來信 ⇒ 只進艦長室,不進 AI 框(Andy 10-19 01:4x 選 1)。

10-19 實機:Andy 從艦長室按 Touch ID 寄給 Pro 13,Pro 13 的回信送進了艦長席 cc203 的 AI 框。
做法:寄出帳記 author;回信帶 aib-reply-to 指到 author=human 那封 ⇒ 建立時就待審(hold_human,
原因 human_reply)、global.human_reply=True;待審提示、分組都不算它(系統不叫 AI 去讀)。
**這只防「自動塞給 AI」**:同帳號 AI 讀得到佇列檔、刪得掉寄出帳;回信沒帶 reply-to(對方用 aib dm 直接寄)⇒ 照一般信。

核心(一定跑,只用標準庫):
H1 sent_author:記了 human / ai_auto 照回;沒這封、舊帳沒記、msgid 不像、亂值 ⇒ None
H2 import_once:回 human 那封 ⇒ hold_human + human_reply;回 ai_auto 那封 / 沒帶 reply_to ⇒ 照舊 queued、沒這欄
H3 不叫 AI:分組(eligible_rows)、待審提示(_hold_ids_now)、hold 徽章都不算它;排空器不投
H4 陌生人回 human 那封 ⇒ 照陌生待審(原因不換)
連 Nostr(要 AIB_TEST_NOSTR_VENV;沒有就跳過):
H5 x 以 author=human 寄給 y ⇒ x 的寄出帳有 author;y 帶 reply_to 回 x ⇒ x 那封 hold_human + human_reply
刀(複本):K1 收件不看寄出帳 ⇒ H2 紅;K2 分組不濾 ⇒ H3 紅;K3 寄出帳不記 author ⇒ H5 紅(有 nostr-sdk 才跑)
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
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import dm_global_files as GF  # noqa: E402
import dm_queue as Q  # noqa: E402
import dm_triage as TR  # noqa: E402
import global_inbox as GI  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_hr", str(HERE / "test-chat-recv-d07f9-1016.py"))
RB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RB)
HAVE_NOSTR = RB.HAVE_NOSTR
SEAT = "cc203"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-hr-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "AIB_DM_ADDR": "100.114.152.121",
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self.root = self.td / "bridge"
        GF.ensure_dirs(self.root)
        (self.root / "sent").mkdir(exist_ok=True)

    def sent(self, author=None):
        mid = GF.new_msgid()
        rec = {"v": 1, "to": "npub1x", "at": int(time.time()), "op": "send", "owner": "npub1y"}
        if author is not None:
            rec["author"] = author
        (self.root / "sent" / (mid + ".json")).write_text(json.dumps(rec), encoding="utf-8")
        return mid

    def imp(self, verified=True, cls="friend", **extra):
        rec = {"v": 1, "msgid": GF.new_msgid(), "received_at": int(time.time()), "to_cabin": "6ddf", "to_seat": SEAT,
               "from_npub": NK.npub(NK.pubkey_hex("%064x" % 31)),
               "from_addr": NK.npub(NK.pubkey_hex("%064x" % 32)) + "/5a97/4345c",
               "verified": verified, "class": cls, "content": "Pro 13 收到", "display_name": "Mac Pro 13"}
        rec.update(extra)
        GF.write_inbox(self.root, rec)
        out = GI.import_once(self.root, self.env)
        return Q._load_item(SEAT, out[0]["id"], self.env)


class Core(Base):
    def test_h1_sent_author(self):
        self.assertEqual(GF.sent_author(self.root, self.sent("human")), "human")
        self.assertEqual(GF.sent_author(self.root, self.sent("ai_auto")), "ai_auto")
        self.assertIsNone(GF.sent_author(self.root, self.sent()), "舊帳沒記")
        self.assertIsNone(GF.sent_author(self.root, self.sent("boss")), "亂值")
        self.assertIsNone(GF.sent_author(self.root, GF.new_msgid()), "沒這封")
        for bad in (None, "", "../x", 5):
            self.assertIsNone(GF.sent_author(self.root, bad), repr(bad))

    def test_h2_reply_to_human_held(self):
        it = self.imp(reply_to=self.sent("human"))
        self.assertEqual((it["status"], it["hold_why"]), (Q.HOLD_STATUS, Q.HUMAN_REPLY_WHY))
        self.assertIs(it["global"]["human_reply"], True)
        for extra in ({"reply_to": self.sent("ai_auto")}, {}):
            it = self.imp(**extra)
            self.assertEqual(it["status"], "queued", extra)
            self.assertNotIn("human_reply", it["global"], extra)

    def test_h3_not_shown_to_ai(self):
        held = self.imp(reply_to=self.sent("human"))
        plain = self.imp()
        rows = Q.tabs_payload(SEAT, env=self.env)["tabs"]["waiting"]["rows"]
        self.assertEqual({r["id"] for r in rows}, {held["id"], plain["id"]}, "待審框(人看的)兩封都在")
        self.assertEqual([r["id"] for r in TR.eligible_rows(rows)], [plain["id"]], "分組不算它")
        self.assertEqual(Q._hold_ids_now(SEAT, self.env, int(time.time())), [], "待審提示不算它")
        self.assertEqual(Q.brief_payload(SEAT, env=self.env)["hold"], 0)
        nxt = Q.next_to_drain(SEAT, env=self.env)
        self.assertEqual((nxt or {}).get("id"), plain["id"], "排空器只拿一般那封")

    def test_h4_stranger_stays_stranger(self):
        it = self.imp(verified=False, cls="stranger", reply_to=self.sent("human"))
        self.assertEqual((it["status"], it["hold_why"]), (Q.HOLD_STATUS, Q.GLOBAL_STRANGER_WHY))


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    # 只借收件測試的沙盒工具,不繼承它的 N1–N5(不然會重跑一遍)
    _box, setUp, items = RB.Network._box, RB.Network.setUp, RB.Network.items

    def test_h5_round_trip(self):
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "艦長席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "對方")], [True, True])
        out = RB.outbox_rec("%s/6ddf/%s" % (a.owner, y), x, content="測試請回話", author="human")
        res = GF.send_and_wait(a.root, out, wait=20)
        self.assertEqual(res["stage"], "queued", res)
        self.assertEqual(GF.sent_author(a.root, out["msgid"]), "human", "寄出帳記了 author")
        back = RB.outbox_rec("%s/6ddf/%s" % (a.owner, x), y, content="收到", reply_to=out["msgid"])
        res = GF.send_and_wait(a.root, back, wait=20)
        self.assertEqual(res["stage"], "held", res)
        it = [i for i in self.items(a, x) if i.get("msg") == "收到"][0]
        self.assertEqual((it["status"], it["hold_why"], it["global"].get("human_reply")),
                         (Q.HOLD_STATUS, Q.HUMAN_REPLY_WHY, True))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_inbox_ignores_sent(self):
        self._knife("src/cockpit/global_inbox.py", 'human_reply = GF.sent_author(root, rec.get("reply_to")) == "human"',
                    "human_reply = False", "Core.test_h2_reply_to_human_held")

    def test_k2_triage_no_filter(self):
        self._knife("src/cockpit/dm_triage.py",
                    '            and not (isinstance(r.get("global"), dict) and r["global"].get("human_reply") is True)]', "]",
                    "Core.test_h3_not_shown_to_ai")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_sent_no_author(self):
        self._knife("src/cockpit/chat_nostr.py", '            sent["author"] = rec["author"]', "            pass",
                    "Network.test_h5_round_trip")


if __name__ == "__main__":
    unittest.main()
