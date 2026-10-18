#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""收件端照寄件方的 aib-author 標出誰寫的(開發文件第 35 節第 2 點;10-19 實機測抓到:Pro 按 Touch ID
代主人寄的信,Pro 13 / Mini 收到後跟 AI 寄的一模一樣)。只進外框說明行,不進抬頭括號。

核心(一定跑,只用標準庫):
A1 frame:human / ai_draft / ai_auto 四種語言各多一句「寄件方標示」;沒帶 / 不認得的值 ⇒ 跟以前一字不差
A2 檔案契約:inbox 收 author 三種值、其他值整筆拒;queue_fields 帶出 author
A3 收件程式 → 佇列 → 外框:import_once 的 global 帶 author;build_from_global 的外框有那一句;沒帶的舊列不多欄
連 Nostr(要 AIB_TEST_NOSTR_VENV;沒有就跳過):
A4 同主人兩席,寄件席帶 aib-author=human 寄一則 ⇒ 收件席佇列那封 global.author = human;帶亂值 ⇒ 不帶
刀(複本):K1 frame 不看 author ⇒ A1 紅;K2 收件不讀 aib-author ⇒ A4 紅(有 nostr-sdk 才跑)
"""
import importlib.util
import json
import subprocess
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
import dm_global_header as GH  # noqa: E402
import dm_header as HD  # noqa: E402
import dm_queue as Q  # noqa: E402
import global_inbox as GI  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_al", str(HERE / "test-chat-recv-d07f9-1016.py"))
RB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RB)
HAVE_NOSTR, NOSTR_VENV = RB.HAVE_NOSTR, RB.NOSTR_VENV
SEAT = "4345c"

# 用寄件席自己的金鑰寄一則帶 aib-author 的一般信(模擬 aib chat send --as-human 那條)
INJECT = ("import nostr_sdk as N,sys,asyncio,json\n"
          "sys.path.insert(0,sys.argv[1])\n"
          "import nostr_keys as NK\n"
          "a=json.loads(sys.argv[2])\n"
          "k=N.Keys.parse(NK.load_secret(a['key']))\n"
          "t=[N.Tag.parse(v) for v in (['aib-v','1'],['aib-type','dm'],['aib-msgid',a['msgid']],"
          "['aib-from',a['from']],['aib-to',a['to']],['aib-author',a['author']])]\n"
          "ev=N.nip17_make_private_msg(k,N.PublicKey.parse(a['to_npub']),a['text'],rumor_extra_tags=t)\n"
          "async def m():\n c=N.Client();await c.add_relay(N.RelayUrl.parse(a['relay']));await c.connect()\n"
          " await c.send_event(ev);await c.shutdown()\n"
          "asyncio.run(m())\n")


def inbox_rec(**extra):
    rec = {"v": 1, "msgid": GF.new_msgid(), "received_at": int(time.time()), "to_cabin": "5a97", "to_seat": SEAT,
           "from_npub": NK.npub(NK.pubkey_hex("%064x" % 21)),
           "from_addr": NK.npub(NK.pubkey_hex("%064x" % 22)) + "/931e/bbbbb",
           "verified": True, "class": "friend", "content": "外面的信", "display_name": "Bob"}
    rec.update(extra)
    return rec


class Frame(unittest.TestCase):
    def test_a1_frame_author_line(self):
        want = {"zh-TW": ("主人本人寄的", "AI 擬稿", "AI 自動寄出"),
                "en": ("written by the owner", "drafted by AI", "sent automatically by AI"),
                "ja": ("主人本人が送信", "AI の下書き", "AI が自動送信"),
                "ko": ("주인 본인이 보냄", "AI 초안", "AI 자동 발송")}
        for lang, words in want.items():
            plain = GH.frame("abcde", lang=lang)
            self.assertEqual(GH.frame("abcde", lang=lang, author=None), plain)
            self.assertEqual(GH.frame("abcde", lang=lang, author="boss"), plain, "不認得的值不加字")
            for author, w in zip(("human", "ai_draft", "ai_auto"), words):
                f = GH.frame("abcde", lang=lang, author=author)
                self.assertIn(w, f, (lang, author))
                self.assertIn("abcde", f)
        both = GH.frame("abcde", country="JP", country_status="mismatch", tz_country="TW", author="human")
        self.assertLess(both.index("主人本人寄的"), both.index("國別"), "標示在國別那句前面")


class Contract(unittest.TestCase):
    def test_a2_inbox_author(self):
        for a in GF.AUTHORS:
            self.assertEqual(GF.queue_fields(inbox_rec(author=a))["author"], a)
        self.assertIsNone(GF.queue_fields(inbox_rec())["author"])
        with self.assertRaises(GF.ContractError):
            GF.check_inbox(inbox_rec(author="boss"))


class Pipeline(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-al-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "AIB_DM_ADDR": "100.114.152.121",
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self.root = self.td / "bridge"
        GF.ensure_dirs(self.root)

    def imp(self, **extra):
        GF.write_inbox(self.root, inbox_rec(**extra))
        out = GI.import_once(self.root, self.env)
        self.assertEqual(out[0]["result"], "queued", out)
        return Q._load_item(SEAT, out[0]["id"], self.env)

    def test_a3_inbox_to_frame(self):
        it = self.imp(author="human")
        self.assertEqual(it["global"]["author"], "human")
        _head, frame = HD.build_from_global(SEAT, it["id"], "@4345c(收件席)", when=1792045800, env=self.env)
        self.assertIn("寄件方標示:主人本人寄的", frame)
        old = self.imp()
        self.assertNotIn("author", old["global"], "沒帶的不多一欄")
        _head, frame = HD.build_from_global(SEAT, old["id"], "@4345c(收件席)", when=1792045800, env=self.env)
        self.assertNotIn("寄件方標示", frame)


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    # 只借收件測試的沙盒工具,不繼承它的 N1–N5(不然會重跑一遍)
    _box, setUp, items = RB.Network._box, RB.Network.setUp, RB.Network.items

    def inject(self, b, src, dst, author, text):
        ent = {e["seat5"]: e for e in NK.read_map(b.env())["seats"]}
        arg = {"key": ent[src]["key"], "msgid": GF.new_msgid(), "text": text, "relay": self.relay.url, "author": author,
               "from": "%s/6ddf/%s" % (b.owner, src), "to": "%s/6ddf/%s" % (b.owner, dst), "to_npub": ent[dst]["npub"]}
        r = subprocess.run([str(Path(NOSTR_VENV) / "bin" / "python"), "-c", INJECT, str(REPO / "src" / "cockpit"),
                            json.dumps(arg)], env=b.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a4_author_tag_reaches_queue(self):
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True, True])
        self.inject(a, x, y, "human", "本人寫的")
        self.inject(a, x, y, "boss", "亂標的")

        def got():
            return {i.get("msg"): (i.get("global") or {}) for i in self.items(a, y)}
        self.assertTrue(a.wait_for(lambda: {"本人寫的", "亂標的"} <= set(got()), 30), got())
        self.assertEqual(got()["本人寫的"].get("author"), "human")
        self.assertNotIn("author", got()["亂標的"], "不認得的值不轉")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_frame_ignores_author(self):
        self._knife("src/cockpit/dm_global_header.py", '        note = L["a_" + author] + note\n', "        pass\n",
                    "Frame.test_a1_frame_author_line")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_recv_ignores_author_tag(self):
        self._knife("src/cockpit/chat_nostr.py", ', ("aib-author", "author")):', "):",
                    "Network.test_a4_author_tag_reaches_queue")


if __name__ == "__main__":
    unittest.main()
