#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""主人手機配對(開發文件第 33 節 v2.1:33.5 / 33.5a Andy 10-18 裁、33.6 @abb89 17:59 審)。

P1 指令 / 討論:開頭 gm(大小寫、後面接空格或冒號再接內容)/ 指令 / ! / ! ⇒ 指令(去掉開頭字);只打「gm」、「gmail…」、其他 ⇒ 討論
P2 防重播:rumor 時間差 > 10 分鐘 ⇒ skew;同一個事件 id 第二次 ⇒ replay;7 天前的 id 清掉
P3 配對碼:錯碼 / 過期 ⇒ 不收;對的碼 ⇒ 只記成候選,**不配**
P4 最多 3 支(第 4 支 too_many);revoke 用 npub 頭 10 碼或 all
P5 指令:start 要本人證明(沒過 ⇒ 不開始);confirm 第二窗字是「把手機 npub1…尾 配成主人手機」(npub 頭尾在最前面),
   本人取消 ⇒ 不配;按了 ⇒ 配上;revoke 沒過本人證明 ⇒ 不撤
P6 外框:手機的指令 ⇒「主人從手機下令(已驗簽)」+ 白名單界線;討論 ⇒「不是指令,也不是核准」
N1(要 AIB_TEST_NOSTR_VENV;本機假中繼站 + 拋棄式手機金鑰)手機傳配對碼 ⇒ 候選、還沒配;confirm 按了 ⇒ 配上;
   手機傳「gm 跑測試」⇒ 收件匣 class paired、kind order、內容「跑測試」、指令帳有一筆、手機收到「收到指令」;
   傳「好啊」⇒ kind other;沒配對的手機傳的 ⇒ 不進收件匣(陌生拒收)
刀(複本):K1 手機傳對碼就自動配 ⇒ N1 紅;K2 不擋重播 ⇒ P2 紅;K3 不看時間差 ⇒ P2 紅;K4 手機的話一律當指令 ⇒ N1 紅
"""
import importlib.util
import io
import json
import os
import subprocess
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
import chat_pair as CP  # noqa: E402
import dm_global_header as GH  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("roster_t", str(HERE / "test-chat-roster-d07f9-1015.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)

PHONE_SK = "%064x" % 0x9001
PHONE = NK.npub(NK.pubkey_hex(PHONE_SK))


def npub_of(i):
    return NK.npub(NK.pubkey_hex("%064x" % (0x9100 + i)))


class Core(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-phone-")
        self.root = Path(self.td.name) / "bridge"
        self.root.mkdir()
        self.env = {"HOME": str(Path(self.td.name) / "home"), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW"}

    def tearDown(self):
        self.td.cleanup()

    def test_p1_classify(self):
        for text, want in (("gm 跑一次測試", ("command", "跑一次測試")), ("GM:寫文件", ("command", "寫文件")),
                           ("gm:開 PR", ("command", "開 PR")), ("指令:看進度", ("command", "看進度")),
                           ("指令 看進度", ("command", "看進度")), ("!跑", ("command", "跑")), ("!跑", ("command", "跑")),
                           ("gm", ("chat", "gm")), ("gm ", ("chat", "gm ")), ("gmail 是什麼", ("chat", "gmail 是什麼")),
                           ("好啊", ("chat", "好啊")), ("!", ("chat", "!"))):
            self.assertEqual(CP.classify(text), want, text)

    def test_p2_replay(self):
        now = 1_800_000_000
        self.assertEqual(CP.accept_event(self.root, "e1", now - 601, now=now), "skew")
        self.assertEqual(CP.accept_event(self.root, "e1", now + 601, now=now), "skew")
        self.assertIsNone(CP.accept_event(self.root, "e1", now - 30, now=now))
        self.assertEqual(CP.accept_event(self.root, "e1", now - 30, now=now + 5), "replay", "同一則不准處理兩次")
        self.assertIsNone(CP.accept_event(self.root, "e2", now, now=now))
        self.assertIsNone(CP.accept_event(self.root, "e3", now + 8 * 86400, now=now + 8 * 86400))
        self.assertNotIn("e1", json.loads(CP.seen_path(self.root).read_text()), "7 天前的清掉")

    def test_p3_code_is_only_candidate(self):
        code = CP.new_code()
        CP.start_pending(self.root, code, "aaaa1")
        self.assertFalse(CP.offer_code(self.root, PHONE, "wrong" + code[5:]))
        self.assertFalse(CP.offer_code(self.root, PHONE, code, now=time.time() + CP.PAIR_WINDOW_S + 5), "過期")
        self.assertTrue(CP.offer_code(self.root, PHONE, " " + code.upper() + " "))
        self.assertEqual([c["npub"] for c in CP.read_pending(self.root)["candidates"]], [PHONE])
        self.assertFalse(CP.is_paired(PHONE, self.env), "傳對碼只是候選,不准自動配")

    def test_p4_limit_and_revoke(self):
        for i in range(3):
            CP.add_phone(npub_of(i), self.env)
        with self.assertRaises(CP.PairError) as cm:
            CP.add_phone(npub_of(9), self.env)
        self.assertEqual(str(cm.exception), "too_many")
        self.assertEqual(CP.revoke(npub_of(0)[:12], self.env), [npub_of(0)])
        with self.assertRaises(CP.PairError):
            CP.revoke(npub_of(0)[:5], self.env)               # 太短不認
        self.assertEqual(len(CP.revoke("all", self.env)), 2)
        self.assertEqual(CP.read_paired(self.env), [])

    def test_p6_frames(self):
        cmd = GH.frame("abcde", lang="zh-TW", paired=True, command=True)
        chat = GH.frame("abcde", lang="zh-TW", paired=True)
        self.assertIn("主人從手機下令", cmd)
        self.assertIn("Touch ID", cmd)
        self.assertIn("不是指令,也不是核准", chat)
        for lang in ("en", "ja", "ko"):
            self.assertNotEqual(GH.frame("abcde", lang=lang, paired=True, command=True), GH.frame("abcde", lang=lang, paired=True))


class Cli(unittest.TestCase):
    def setUp(self):
        self.b = RT.Box()
        self.addCleanup(self.b.close)
        self.seat = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        self.assertEqual(self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.b.cli("publish", self.seat)
        self.env = dict(self.b.env(), AIB_PAIR_POLL="0.05")
        self.asked = []
        self.answer = None
        self.addCleanup(setattr, CP, "PROVE", None)

        def prove(reason, env, root=None):
            self.asked.append(reason)
            return self.answer(reason) if callable(self.answer) else self.answer
        CP.PROVE = prove

    def run_cli(self, *argv):
        import chat_bridge as CB
        buf = io.StringIO()
        rc = CB.main(list(argv), env=self.env, out=buf)
        return rc, buf.getvalue()

    def test_p5_cli(self):
        self.answer = "touchid_cancel"
        rc, txt = self.run_cli("pair", "start", "--no-wait")
        self.assertEqual(rc, 2, txt)
        self.assertIsNone(CP.read_pending(self.b.root), "沒過本人證明就不開始")
        self.answer = None
        rc, txt = self.run_cli("pair", "start", "--no-wait", "--json")
        self.assertEqual(rc, 0, txt)
        code = json.loads(txt.strip().splitlines()[-1])["code"]
        evil = npub_of(7)
        CP.offer_code(self.b.root, evil, code)                  # 讀到配對碼的 AI 自己產金鑰搶先送
        CP.offer_code(self.b.root, PHONE, code)
        self.answer = lambda reason: None if CP.short(PHONE) in reason else "touchid_cancel"   # 本人只認自己手機上那串
        rc, txt = self.run_cli("pair", "confirm")
        self.assertEqual(rc, 0, txt)
        self.assertTrue(self.asked[-1].startswith("把手機 " + CP.short(PHONE)), self.asked[-1])
        self.assertTrue(any(CP.short(evil) in r for r in self.asked), "搶先的那支也要請本人看過")
        self.assertEqual([p["npub"] for p in CP.read_paired(self.env)], [PHONE], "本人沒按的那支不准配")
        self.answer = "touchid_cancel"
        rc, txt = self.run_cli("pair", "revoke", "all")
        self.assertEqual(rc, 2)
        self.assertTrue(CP.is_paired(PHONE, self.env), "沒過本人證明就不撤")
        self.answer = None
        rc, txt = self.run_cli("pair", "revoke", "all")
        self.assertEqual(rc, 0, txt)
        self.assertFalse(CP.is_paired(PHONE, self.env))


_PHONE_SEND = r"""
import sys, json, asyncio, datetime, nostr_sdk as N
a = json.load(sys.stdin)
keys = N.Keys.parse(a["sk"])
async def main():
    c = N.Client(); await c.add_relay(N.RelayUrl.parse(a["relay"])); await c.connect()
    if a.get("dm_relays"):
        await c.send_event(N.EventBuilder(N.Kind(10050), "").tags([N.Tag.parse(["relay", a["relay"]])]).finalize(keys))
    out = []
    for text in a.get("texts") or []:
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(a["to"]), text)
        r = await c.send_event(gw)
        out.append(bool(r.success))
    if a.get("fetch"):
        evs = await c.fetch_events(N.ReqTarget.auto([N.Filter().kind(N.Kind(1059)).pubkey(keys.public_key())]),
                                   datetime.timedelta(seconds=4))
        texts = []
        for e in evs:
            try:
                texts.append(N.UnwrappedGift.from_gift_wrap(keys, e).rumor().content())
            except Exception:
                pass
        out = texts
    await c.shutdown()
    print(json.dumps(out, ensure_ascii=False))
asyncio.run(main())
"""


@unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(Cli):
    """真的走一遍:拋棄式手機金鑰經本機假中繼站傳 NIP-17 給這台的公開席。"""
    test_p5_cli = None

    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.relay = FakeRelay()
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        self.b = RT.Box([self.relay.url])
        self.addCleanup(self.b.close)
        self.b.link_venv()
        self.seat = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        self.assertEqual(self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.b.cli("publish", self.seat)
        self.env = dict(self.b.env(), AIB_PAIR_POLL="0.05")
        self.asked, self.answer = [], None
        self.addCleanup(setattr, CP, "PROVE", None)
        CP.PROVE = lambda reason, env, root=None: (self.asked.append(reason), self.answer)[1]
        self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: self.b.status().get("state") == "published", 40), self.b.status())
        self.seat_npub = next(e["npub"] for e in NK.read_map(self.b.env())["seats"] if e["seat5"] == self.seat)

    def phone(self, sk, texts=(), fetch=False, dm_relays=False):
        py = Path(RT.NOSTR_VENV) / "bin" / "python"
        r = subprocess.run([str(py), "-c", _PHONE_SEND], input=json.dumps({"sk": sk, "relay": self.relay.url, "to": self.seat_npub,
                                                                              "texts": list(texts), "fetch": fetch,
                                                                              "dm_relays": dm_relays}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def inbox(self):
        return [json.loads(p.read_text()) for p in sorted((self.b.root / "inbox").glob("*.json"))]

    def test_n1_phone_flow(self):
        rc, txt = self.run_cli("pair", "start", "--no-wait", "--json")
        self.assertEqual(rc, 0, txt)
        code = json.loads(txt.strip().splitlines()[-1])["code"]
        self.phone(PHONE_SK, [code], dm_relays=True)
        self.assertTrue(self.b.wait_for(lambda: (CP.read_pending(self.b.root) or {}).get("candidates"), 20), "候選沒進來")
        # 看到候選之後再等 3 秒都沒被配上才算(自動配的寫法是先記候選、緊接著寫 paired.json,只看一眼會剛好落在兩次寫之間)
        self.assertFalse(self.b.wait_for(lambda: CP.is_paired(PHONE, self.env), 3), "傳對碼不准自動配")
        rc, txt = self.run_cli("pair", "confirm")
        self.assertEqual(rc, 0, txt)
        self.assertTrue(CP.is_paired(PHONE, self.env))
        self.phone(PHONE_SK, ["gm 跑一次測試"])
        self.assertTrue(self.b.wait_for(lambda: any(r.get("class") == "paired" for r in self.inbox()), 20), self.inbox())
        got = [r for r in self.inbox() if r.get("class") == "paired"][0]
        self.assertEqual((got["kind"], got["content"], got["verified"]), ("order", "跑一次測試", True))
        self.assertEqual(CP.last_command(self.b.root)["head"], "跑一次測試")
        self.assertTrue(self.b.wait_for(lambda: any("收到指令" in t for t in self.phone(PHONE_SK, fetch=True)), 20),
                        "手機要收到「收到指令」")
        self.phone(PHONE_SK, ["好啊"])
        self.assertTrue(self.b.wait_for(lambda: any(r.get("content") == "好啊" for r in self.inbox()), 20), self.inbox())
        self.assertEqual([r for r in self.inbox() if r.get("content") == "好啊"][0]["kind"], "other", "討論不是指令")
        self.phone("%064x" % 0x9555, ["gm 刪掉全部"])
        time.sleep(3)
        self.assertFalse(any(r.get("content") in ("刪掉全部", "gm 刪掉全部") for r in self.inbox()), "沒配對的手機不准進來")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name, "test-chat-roster-d07f9-1015.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300,
                            env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        if r.returncode == 0 and "skipped=" in (r.stdout + r.stderr):
            self.skipTest("被砍的那格跳過了,刀沒得驗")
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    @unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_auto_pair(self):
        self._knife("src/cockpit/chat_nostr.py", "            if CP.offer_code(self.root, from_npub, content):\n",
                    "            if CP.offer_code(self.root, from_npub, content) and CP.add_phone(from_npub) is None:\n",
                    "Network.test_n1_phone_flow")

    def test_k2_no_replay_guard(self):
        self._knife("src/cockpit/chat_pair.py", "        if event_id in seen:\n            return \"replay\"\n", "",
                    "Core.test_p2_replay")

    def test_k3_no_skew_guard(self):
        self._knife("src/cockpit/chat_pair.py", "or abs(now - created_at) > SKEW_S:", "or False:", "Core.test_p2_replay")

    @unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k4_all_commands(self):
        self._knife("src/cockpit/chat_nostr.py", 'kind="order" if kind == "command" else "other")', 'kind="order")',
                    "Network.test_n1_phone_flow")


if __name__ == "__main__":
    unittest.main()
