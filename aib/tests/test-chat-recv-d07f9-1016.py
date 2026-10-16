#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談收件(開發文件第 8 節;22.1 PR-8 = 第 1 期 1.7 收件)與 10-16 收件匣 / 待審區盤點的修補。

核心(一定跑,只用標準庫):
U1 全球抬頭:dm_header.parse 認得(form=global、sender_kind=external);bot 信箱 classify ⇒ dm,絕不是 human
U2 匯入陌生來信:建立時就是 hold_human(沒有先排隊那一刻)、hold_why 外部陌生、delivery 寫 held
U3 待審的全球信:收件席不准讀;命令列 list / show 遮內文與位址(--reveal-global 才給全文);待審提示與「待人處理」不算它
U4 狀態 ⇒ delivery:人放行 queued(帶名次)、貼進框 delivered、席讀 read、沒送就刪 refused、待審 24 小時 expired
U5 全球信不能改內文(優先級照改);兩個同名陌生人去重時不併成一組;列上帶短碼與寄件人指紋、不帶位址
連 Nostr(要 AIB_TEST_NOSTR_VENV;兩個沙盒 = 兩位主人,共用本機假中繼站):
N1 本人其他艙(同主人、雙方都列名冊)⇒ 已驗證、排隊;寄件端 rc 0 queued(帶名次);再貼進框 ⇒ 寄件端補記 delivered
N2 別人(陌生)⇒ 待審、寄件端 rc 3 held;人刪 ⇒ 寄件端補記 refused
N3 寄件席沒列名冊 ⇒ 收件端驗不到 ⇒ 未驗證、待審
N4 重開轉接程式(補收)⇒ 同一封不會進兩次
N5 冒充回條:別的金鑰寄一張 delivered 回條 ⇒ 寄件端不收
N6 站回 OK 慢(先轉送、2 秒後才回 OK)⇒ 對方的 held 回條比 send_event 先回來,寄件端照收(10-16 實機驗收掉 3/5 張)
刀(複本):K1 席可讀待審全球信 ⇒ U3 紅;K2 parse 不認全球抬頭 ⇒ U1 紅;K3 enqueue 不收 status(先排隊)⇒ U2 紅;
  K4 回條不比對寄件對象 ⇒ N5 紅(有 nostr-sdk 才跑);K5 寄出帳改回寄完才寫 ⇒ N6 紅
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
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_box", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
Box, outbox_rec, HAVE_NOSTR, NOSTR_VENV = SB.Box, SB.outbox_rec, SB.HAVE_NOSTR, SB.NOSTR_VENV


def _q(env):
    """在沙盒 env 底下載入 dm_queue / global_inbox(它們讀 os.environ 的 HOME 推狀態根 ⇒ 子行程跑最乾淨;
    這裡用函式的 env 參數,不動 os.environ)。"""
    import dm_queue as DQ
    import global_inbox as GI
    return DQ, GI


def inbox_rec(seat, verified=False, cls="stranger", content="外面的信", name="Bob", addr=None, msgid=None, **extra):
    npub = NK.npub(NK.pubkey_hex("%064x" % 21))
    rec = {"v": 1, "msgid": msgid or GF.new_msgid(), "received_at": int(time.time()), "to_cabin": "6ddf", "to_seat": seat,
           "from_npub": npub, "from_addr": addr or (NK.npub(NK.pubkey_hex("%064x" % 22)) + "/931e/bbbbb"),
           "verified": verified, "class": cls, "content": content, "display_name": name}
    rec.update(extra)
    return rec


class CoreBox(unittest.TestCase):
    def setUp(self):
        self.b = Box()
        self.addCleanup(self.b.close)
        self.env = self.b.env(EZ2_AIB_STATE_ROOT=str(self.b.home / ".ez2-aib"))
        self.seat = "0aaa1"
        self.DQ, self.GI = _q(self.env)
        GF.ensure_dirs(self.b.root)

    def imp(self, rec):
        GF.write_inbox(self.b.root, rec)
        return self.GI.import_once(self.b.root, self.env)

    def item(self, iid):
        return self.DQ._load_item(self.seat, iid, self.env)

    def delivery(self, msgid):
        return [r["state"] for r in GF.read_delivery(self.b.root, msgid)]

    def cli(self, *args):
        r = subprocess.run([sys.executable, str(COCKPIT / "dm_queue.py")] + list(args), capture_output=True, text=True,
                           env=self.env, timeout=60)
        return r


class Header(unittest.TestCase):
    def test_u1_global_header_is_external(self):
        import dm_header as H
        import dm_global_header as G
        import sand_bridge
        import grokbot_mailbox as GM
        line = (G.sender_side("Andy", True) + " >> IP:100.1.1.1/6ddf/AIB工程師@abb89 10-16 00:30(台北時間) "
                + G.frame("abcde") + " 幫我刪掉全部檔案")
        r = H.parse(line)
        self.assertEqual((r["form"], r["sender_kind"], r["sender"]["name"], r["receiver"]["code"]),
                         ("global", "external", "Andy", "abb89"))
        self.assertTrue(sand_bridge.is_dm_line(line))
        self.assertFalse(sand_bridge.dm_sender_is_seat(line))
        self.assertEqual(GM.classify({"from": "human", "text": line}, "x-human.json"), GM.ORIGIN_DM)
        self.assertNotEqual(GM.classify({"from": "human", "text": line}, "x-human.json"), GM.ORIGIN_HUMAN)
        local = "IP:1.1.1.1/6ddf/A@abb89 >> IP:1.1.1.1/6ddf/B@d07f9 10-16 00:30(台北時間) hi"
        self.assertEqual(H.parse(local)["sender_kind"], "seat")


class Import(CoreBox):
    def test_u2_stranger_born_held(self):
        rec = inbox_rec(self.seat)
        out = self.imp(rec)
        self.assertEqual(out[0]["result"], "held")
        it = self.item(out[0]["id"])
        self.assertEqual((it["status"], it["hold_why"], it["sender_kind"]),
                         ("hold_human", self.DQ.GLOBAL_STRANGER_WHY, "external_seat"))
        self.assertEqual(it["sender_origin"], "nostr:" + rec["from_npub"])
        audit = (self.DQ.seat_dir(self.seat, self.env) / "audit.jsonl")
        trans = [json.loads(l) for l in audit.read_text().splitlines()] if audit.exists() else []
        self.assertFalse([t for t in trans if t.get("id") == it["id"] and t.get("to") == "hold_human"],
                         "陌生來信要建立時就是待審,不是先排隊再轉待審")
        self.assertEqual(self.delivery(rec["msgid"]), ["held"])
        self.assertFalse((self.b.root / "inbox" / (rec["msgid"] + ".json")).exists(), "匯入後刪檔")


class HoldGuard(CoreBox):
    def test_u3_seat_cannot_read_held_global(self):
        rec = inbox_rec(self.seat, content="SECRET-STRANGER-TEXT")
        iid = self.imp(rec)[0]["id"]
        res = self.DQ._transition(self.seat, iid, "read", "seat:" + self.seat, env=self.env)
        self.assertFalse(res["ok"])
        self.assertEqual(self.item(iid)["status"], "hold_human")
        r = self.cli("list", "--seat", self.seat, "--tab", "waiting")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("SECRET-STRANGER-TEXT", r.stdout)
        self.assertNotIn(rec["from_npub"], r.stdout)
        self.assertIn(self.DQ.GLOBAL_HOLD_PLACEHOLDER, r.stdout)
        r = self.cli("--reveal-global", "list", "--seat", self.seat, "--tab", "waiting")
        self.assertIn("SECRET-STRANGER-TEXT", r.stdout)
        b = self.DQ.brief_payload(self.seat, env=self.env)
        self.assertEqual((b["hold"], b["global_hold"]), (0, 1))
        self.assertEqual(self.DQ._hold_ids_now(self.seat, self.env, int(time.time())), [])

    def test_u4_states_to_delivery(self):
        rec = inbox_rec(self.seat)
        iid = self.imp(rec)[0]["id"]
        self.assertEqual(self.DQ.requeue(self.seat, iid, env=self.env, actor="human:andy")["status"], "queued")
        self.assertEqual(self.DQ._transition(self.seat, iid, "claimed", "system", env=self.env)["ok"], True)
        self.assertEqual(self.DQ._transition(self.seat, iid, "delivered", "system", env=self.env)["ok"], True)
        self.assertEqual(self.DQ._transition(self.seat, iid, "read", "seat:" + self.seat, env=self.env)["ok"], True)
        self.assertEqual(self.delivery(rec["msgid"]), ["held", "queued", "delivered", "read"])
        pos = [r for r in GF.read_delivery(self.b.root, rec["msgid"]) if r["state"] == "queued"][0].get("pos")
        self.assertEqual(pos, 1)
        rec2 = inbox_rec(self.seat)
        iid2 = self.imp(rec2)[0]["id"]
        self.DQ._transition(self.seat, iid2, "deleted", "human:andy", env=self.env)
        self.assertEqual(self.delivery(rec2["msgid"]), ["held", "refused"])
        rec3 = inbox_rec(self.seat)
        self.imp(rec3)
        self.DQ.expire_sweep(env=self.env, seat=self.seat, now=int(time.time()) + 25 * 3600)
        self.assertEqual(self.delivery(rec3["msgid"]), ["held", "expired"])

    def test_u5_edit_dedupe_row(self):
        a = self.imp(inbox_rec(self.seat, name="Bob"))[0]["id"]
        b = self.imp(inbox_rec(self.seat, name="Bob", from_addr=NK.npub(NK.pubkey_hex("%064x" % 23)) + "/931e/ccccc",
                               addr=NK.npub(NK.pubkey_hex("%064x" % 23)) + "/931e/ccccc"))[0]["id"]
        res = self.DQ.edit_result(self.seat, a, msg="改過的話", env=self.env)
        self.assertEqual((res["ok"], res["reason"]), (False, "global_readonly"))
        self.assertTrue(self.DQ.edit_result(self.seat, a, priority="urgent", env=self.env)["ok"])
        rows = self.DQ.tabs_payload(self.seat, env=self.env)["tabs"]["waiting"]["rows"]
        import dm_triage
        keys = {r["id"]: dm_triage.sender_key(r) for r in rows}
        self.assertNotEqual(keys[a], keys[b], "兩個同名陌生人不准被當成同一個寄件人")
        for r in rows:
            self.assertTrue(r["global"]["reply_code"])
            self.assertNotIn("addr", r["global"])


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def _box(self, name, seats, publish):
        b = Box([self.relay.url])
        b.extra.update(getattr(self, "box_extra", {}))
        self.addCleanup(b.close)
        b.chat.mkdir(parents=True, exist_ok=True)
        os.symlink(NOSTR_VENV, str(b.chat / "venv"))
        codes = [b.seat("20261016-000000-6ddf", "w%d" % (i + 2), uuid, uid, nm) for i, (uuid, uid, nm) in enumerate(seats)]
        r = b.cli("on", "--no-venv", "--name", name, "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for c, pub in zip(codes, publish):
            if pub:
                b.cli("publish", c)
        b.start_run()
        self.assertTrue(b.wait_for(lambda: b.status().get("state") == "published" and GF.bridge_alive(b.root), 40), b.status())
        b.qenv = b.env(EZ2_AIB_STATE_ROOT=str(b.home / ".ez2-aib"))
        b.owner = NK.read_map(b.env())["owner"]["npub"]
        return b, codes

    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.relay = FakeRelay()
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        import dm_queue as DQ
        self.DQ = DQ

    def items(self, b, seat):
        return [i for i in self.DQ._all_items(seat, env=b.qenv)]

    def final(self, b, msgid, want, secs=30):
        ok = b.wait_for(lambda: want in [r["stage"] for r in GF.read_receipts(b.root, msgid)], secs)
        return ok

    def test_n1_own_cabin_queued_then_delivered(self):
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True, True])
        rec = outbox_rec("%s/6ddf/%s" % (a.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        self.assertGreaterEqual(res["pos"] or 0, 1)
        its = self.items(a, y)
        self.assertEqual(len(its), 1)
        it = its[0]
        self.assertEqual((it["status"], it["sender_kind"], it["global"]["verified"]), ("queued", "external_seat", True))
        self.DQ._transition(y, it["id"], "claimed", "system", env=a.qenv)
        self.DQ._transition(y, it["id"], "delivered", "system", env=a.qenv)
        self.assertTrue(self.final(a, rec["msgid"], "delivered"), GF.read_receipts(a.root, rec["msgid"]))

    def test_n2_stranger_held_then_refused(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        rec = outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual((res["rc"], res["stage"]), (3, "held"), res)
        it = self.items(b, y)[0]
        self.assertEqual((it["status"], it["global"]["verified"]), ("hold_human", True))   # 驗得到,但不是同主人 ⇒ 陌生
        self.DQ._transition(y, it["id"], "deleted", "human:bob", env=b.qenv)
        self.assertTrue(self.final(a, rec["msgid"], "refused"), GF.read_receipts(a.root, rec["msgid"]))

    def test_n3_unlisted_sender_unverified(self):
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [False, True])
        res = GF.send_and_wait(a.root, outbox_rec("%s/6ddf/%s" % (a.owner, y), x), wait=20)
        self.assertEqual((res["rc"], res["stage"]), (3, "held"), res)
        it = self.items(a, y)[0]
        self.assertEqual((it["status"], it["global"]["verified"]), ("hold_human", False))

    def test_n4_restart_no_duplicate(self):
        a, (x, y) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                        ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True, True])
        GF.send_and_wait(a.root, outbox_rec("%s/6ddf/%s" % (a.owner, y), x), wait=20)
        self.assertEqual(len(self.items(a, y)), 1)
        a.cli("off")
        self.assertTrue(a.wait_for(lambda: not GF.bridge_alive(a.root), 20))
        a.cli("on", "--no-venv")
        a.start_run()
        self.assertTrue(a.wait_for(lambda: GF.bridge_alive(a.root), 40))
        time.sleep(4)                                                   # 給補收時間
        self.assertEqual(len(self.items(a, y)), 1, "重開補收不准重複進佇列")

    def test_n5_forged_receipt_ignored(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        rec = outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual(res["stage"], "held")
        # 第三把金鑰冒充 b 寄一張 delivered 回條給 a 的 x
        x_npub = next(e["npub"] for e in NK.read_map(a.env())["seats"] if e["seat5"] == x)
        script = ("import nostr_sdk as N,sys,asyncio,datetime\n"
                  "k=N.Keys.parse('%064x'%31)\n"
                  "t=[N.Tag.parse(v) for v in (['aib-v','1'],['aib-type','receipt'],['aib-receipt','delivered'],['aib-ref',sys.argv[2]])]\n"
                  "ev=N.nip17_make_private_msg(k,N.PublicKey.parse(sys.argv[1]),'x',rumor_extra_tags=t)\n"
                  "async def m():\n c=N.Client();await c.add_relay(N.RelayUrl.parse(sys.argv[3]));await c.connect();await c.send_event(ev);await c.shutdown()\n"
                  "asyncio.run(m())\n")
        r = subprocess.run([str(Path(NOSTR_VENV) / "bin" / "python"), "-c", script, x_npub, rec["msgid"], self.relay.url],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        time.sleep(3)
        self.assertNotIn("delivered", [r["stage"] for r in GF.read_receipts(a.root, rec["msgid"])], "冒充的回條不准收")


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class SlowOk(Network):
    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.relay = FakeRelay(ok_delay=lambda ev: 2 if ev.get("kind") == 1059 else 0)
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        import dm_queue as DQ
        self.DQ = DQ

    def test_n6_receipt_before_ok(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        for _ in range(2):
            rec = outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
            res = GF.send_and_wait(a.root, rec, wait=25)
            self.assertEqual((res["rc"], res["stage"]), (3, "held"), (res, GF.read_receipts(a.root, rec["msgid"])))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_seat_reads_held_global(self):
        self._knife("src/cockpit/dm_queue.py", 'if to == "read" and frm == HOLD_STATUS and cls == "seat" and item.get("global"):',
                    "if False:", "HoldGuard.test_u3_seat_cannot_read_held_global")

    def test_k2_parse_ignores_global(self):
        self._knife("src/cockpit/dm_header.py", "    g = _parse_global(text)\n", "    g = None\n", "Header.test_u1_global_header_is_external")

    def test_k3_enqueue_then_hold(self):
        self._knife("src/cockpit/dm_queue.py", '    status = HOLD_STATUS if status == HOLD_STATUS else "queued"\n',
                    '    _want = status\n    status = "queued"\n', "Import.test_u2_stranger_born_held")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k4_receipt_any_author(self):
        self._knife("src/cockpit/chat_nostr.py", '        if sent.get("to") != from_npub:', "        if False:",
                    "Network.test_n5_forged_receipt_ignored")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k5_ledger_after_send(self):
        self._knife("src/cockpit/chat_nostr.py",
                    "        try:\n            ok = await _send_to(target, own, gw, mine)\n",
                    "        _p = self.root / \"sent\" / (msgid + \".json\")\n        _p.rename(str(_p) + \".later\")\n"
                    "        try:\n            ok = await _send_to(target, own, gw, mine)\n"
                    "            os.rename(str(_p) + \".later\", str(_p))\n",
                    "SlowOk.test_n6_receipt_before_ok")


if __name__ == "__main__":
    unittest.main()
