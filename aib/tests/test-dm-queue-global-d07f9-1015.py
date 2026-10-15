#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""佇列接上全球來信(開發文件 v0.6 第 22.3 節 PR-1 接線;第 8.1、9 節)。

Q1 入列:external_seat + nostr:<npub> 來源 + global 欄(顯示名稱、驗證、短碼)存得下、讀得回。
Q2 不是 human:本機寄件人(含 human)帶 global 也不收;全球種類永遠不會變 human。
Q3 全球種類一定要配 nostr: 來源,不然降 unknown、不存 global。
Q4 global 任何一欄不合 ⇒ 整欄不存(名字含 / 或 >>、msgid、位址、短碼)。
Q5 回信短碼:同一席不重複。
Q6 by_reply_code 與 CLI `by-reply-code`:找到印 addr/msgid,找不到 rc 4。
Q7 來源欄:nostr:<npub> 收、壞的拒、xhost 照舊。
Q8 檔案契約 queue_fields() 的輸出可以直接餵 enqueue(兩邊格式對得上)。
K1–K2 刀測:拿掉「全球種類配 nostr: 來源」/ 拿掉「只有全球種類才收 global」⇒ 對應格變紅。
全程只寫暫存狀態根(EZ2_AIB_STATE_ROOT),不碰真佇列。
"""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "tests"))
import dm_global_files as G  # noqa: E402
import dm_queue as Q  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

NPUB = "npub1" + "p" * 58
ADDR = "npub1" + "q" * 58 + "/6ddf/abb89"
SEAT = "8f366"


def ginfo(**over):
    g = {"addr": ADDR, "shown": "Andy", "verified": True, "same_name": False, "msgid": G.new_msgid()}
    g.update(over)
    return g


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-qg-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name}

    def put(self, kind="external_seat", origin="nostr:" + NPUB, g=None, **kw):
        return Q.enqueue(SEAT, "全球你好", "Andy", sender_kind=kind, sender_origin=origin,
                         global_info=ginfo() if g is None else g, env=self.env, **kw)


class Queue(Base):
    def test_q1_enqueue_and_reload(self):
        item = self.put()
        self.assertEqual(item["sender_kind"], "external_seat")
        self.assertEqual(item["sender_origin"], "nostr:" + NPUB)
        g = item["global"]
        self.assertEqual((g["addr"], g["shown"], g["verified"], g["same_name"]), (ADDR, "Andy", True, False))
        self.assertRegex(g["reply_code"], r"^[a-z2-9]{5}$")
        back = [i for i in Q.list_items(SEAT, env=self.env) if i["id"] == item["id"]][0]
        self.assertEqual(back["global"], g)
        self.assertEqual(back["sender_kind"], "external_seat")
        self.assertEqual(self.put(kind="owner_paired")["sender_kind"], "owner_paired")

    def test_q2_local_senders_never_get_global(self):
        for kind in ("seat", "seat_claimed", "human", "system", "unknown"):
            item = Q.enqueue(SEAT, "x", "@abb89", sender_kind=kind, sender_seat="abb89", human_id="andy",
                             sender_origin="nostr:" + NPUB, global_info=ginfo(), env=self.env)
            self.assertNotIn("global", item, kind)
        for kind in Q.GLOBAL_SENDER_KINDS:
            self.assertNotEqual(self.put(kind=kind)["sender_kind"], "human")
        self.assertNotIn("human", Q.GLOBAL_SENDER_KINDS)

    def test_q3_global_kind_needs_nostr_origin(self):
        for origin in (None, "", "xhost:100.64.0.1", "abb89", "nostr:junk"):
            item = self.put(origin=origin)
            self.assertEqual(item["sender_kind"], "unknown", origin)
            self.assertNotIn("global", item, origin)

    def test_q4_bad_global_dropped_whole(self):
        for over in (dict(shown="A/B"), dict(shown="Andy >> x"), dict(shown=""), dict(msgid="nope"),
                     dict(addr="6ddf/abb89"), dict(reply_code="ABCDE"), dict(verified="yes")):
            item = self.put(g=ginfo(**over))
            self.assertNotIn("global", item, over)
            self.assertEqual(item["sender_kind"], "external_seat")   # 種類照留,只是沒有全球抬頭資料
        g = ginfo()
        del g["addr"]
        self.assertNotIn("global", self.put(g=g))

    def test_q5_reply_codes_unique_per_seat(self):
        m = G.new_msgid()
        first = G.reply_code(m)
        self.put(g=ginfo(reply_code=first))            # 先佔掉 m 會拿到的碼
        item = self.put(g=ginfo(msgid=m))
        self.assertNotEqual(item["global"]["reply_code"], first)
        codes = [self.put()["global"]["reply_code"] for _ in range(20)]
        self.assertEqual(len(set(codes)), 20)

    def test_q6_by_reply_code_and_cli(self):
        item = self.put()
        code = item["global"]["reply_code"]
        self.assertEqual(Q.by_reply_code(SEAT, code, env=self.env)["id"], item["id"])
        self.assertIsNone(Q.by_reply_code(SEAT, "zzzzz", env=self.env))
        self.assertIsNone(Q.by_reply_code(SEAT, "../x", env=self.env))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = Q.main(["by-reply-code", "--seat", SEAT, "--code", code], env=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "addr=%s msgid=%s id=%s" % (ADDR, item["global"]["msgid"], item["id"]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(Q.main(["by-reply-code", "--seat", SEAT, "--code", "zzzzz"], env=self.env), 4)

    def test_q7_origin(self):
        self.assertEqual(Q._origin_or_none("nostr:" + NPUB), "nostr:" + NPUB)
        self.assertEqual(Q._origin_or_none("xhost:100.64.0.1"), "xhost:100.64.0.1")
        for bad in ("nostr:", "nostr:npub1short", "nostr:" + NPUB + "x", "nostr:NPUB1" + "p" * 58):
            self.assertIsNone(Q._origin_or_none(bad), bad)

    def test_q8_contract_feeds_enqueue(self):
        rec = {"v": 1, "msgid": G.new_msgid(), "received_at": 1, "to_cabin": "931e", "to_seat": SEAT,
               "from_npub": NPUB, "from_addr": ADDR, "display_name": "Andy", "verified": True,
               "class": "friend", "content": "你好", "name_collision": True}
        f = G.queue_fields(rec)
        item = Q.enqueue(SEAT, rec["content"], f["shown_name"], sender_kind=f["sender_kind"],
                         sender_origin=f["sender_origin"], priority=f["priority"], env=self.env,
                         global_info={"addr": f["sender_addr"], "shown": f["shown_name"], "verified": f["verified"],
                                      "same_name": f["same_name"], "msgid": rec["msgid"]})
        self.assertEqual(item["sender_kind"], "external_seat")
        self.assertEqual(item["global"]["shown"], "Andy")
        self.assertTrue(item["global"]["same_name"])


class Knife(unittest.TestCase):
    def _red(self, needle, cut, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_queue.py", needle, cut, tests=(name,)) as root:
            r = subprocess.run([sys.executable, str(root / "tests" / name), cell],
                               cwd=str(root), capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_k1_global_kind_without_nostr_origin(self):
        self._red("    if sk in GLOBAL_SENDER_KINDS and not NOSTR_ORIGIN_RE.fullmatch(str(sender_origin or \"\").strip()):",
                  "    if False:", "Queue.test_q3_global_kind_needs_nostr_origin")

    def test_k2_local_sender_with_global(self):
        self._red("    if global_info is not None and sk in GLOBAL_SENDER_KINDS:", "    if global_info is not None:",
                  "Queue.test_q2_local_senders_never_get_global")


if __name__ == "__main__":
    unittest.main()
