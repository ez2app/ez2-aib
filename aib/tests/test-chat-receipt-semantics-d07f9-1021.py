#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""回條語意(PROPOSAL-gaps v3 第 1 + 2 + 4 件,Andy 10-21 02:4x 准):

T1 held 帶原因碼:回本人那封 ⇒ delivery held reason=human_reply;陌生 ⇒ stranger。
   契約:reason 只收該狀態的白名單(held ⇔ HELD_REASONS、refused ⇔ REFUSE_TEXT);days 只給 still_waiting 1–30;by 只給 read=human
T2 給本人的信留 7 天:第 1、2 天各送一張 still_waiting(days=1 / 2),同一天不重送;滿 7 天 ⇒ expired(hold_7d)+ delivery expired;
   陌生信照舊 24 小時 expired(hold_24h)、不送 still_waiting;沒有 global 的(Telegram)不送
T3 本人已讀:human 讀待審那封 ⇒ delivery read by=human;AI 席位用 --by human: ⇒ 拒(actor)、不寫回條;席自己讀全球待審照舊拒
T4 寄件端 recent:寄出那列帶最後一張回條(stage / days / by / 白名單 detail)
T4b 本機的 relayed 比對方 queued 晚寫(10-24 跨客戶驗收)⇒ 仍顯示對方那張;只有本機的 ⇒ 照顯示 relayed
連 venv(nostr-sdk):
T5 收:_apply_receipt 照白名單收 held 原因 / still_waiting days / 本人已讀 by;亂值不收;舊版不認得的階段略過
T6 送:send_receipt 帶 aib-reason(held 白名單)/ aib-days / aib-read-by;不在白名單的不帶;pump 把本人已讀跟一般已讀分開送
T7(10-25 一鍵對話第 3 條)hop:none / relay / peer / stopped;relayed 停超過 10 分鐘(綠點同一套)或補了 unconfirmed ⇒ hint peer_timeout
   + 四語 hint_text;stage 代碼不改;對方 queued 過、後補 unconfirmed ⇒ 仍算 peer
刀(複本):K1 still_waiting 不記天數 ⇒ T2 紅(每輪重送);K2 human 讀不擋席位 ⇒ T3 紅;K3 回條照寫入順序取最後一張 ⇒ T4b 紅;
   K4 relayed 當對方收到 ⇒ T7 紅
"""
import importlib.util
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
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import dm_global_files as GF  # noqa: E402
import dm_queue as Q  # noqa: E402
import global_inbox as GI  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEAT = "cc203"
VENV_PY = Path(os.environ.get("AIB_TEST_NOSTR_VENV") or "/nonexistent") / "bin" / "python"
HAVE_VENV = VENV_PY.is_file()


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-rcpt-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.root = self.td / "bridge"
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "AIB_DM_ADDR": "100.114.152.121",
                    "EZ2_AIB_LOCALE": "zh-TW", "AIB_CHAT_BRIDGE_DIR": str(self.root)}
        GF.ensure_dirs(self.root)
        (self.root / "sent").mkdir(exist_ok=True)
        self.seat_gate(False)

    def seat_gate(self, seat):
        Q.HUMAN_ACTOR_GATE["caller_is_seat"] = lambda env: seat
        self.addCleanup(Q.HUMAN_ACTOR_GATE.__setitem__, "caller_is_seat", None)

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

    def deliv(self, item):
        return GF.read_delivery(self.root, item["global"]["msgid"])

    def age(self, item, secs):
        it = Q._load_item(SEAT, item["id"], self.env)
        it["held_at"] = int(time.time()) - secs
        Q._write_item(SEAT, it, self.env)
        return it


class Core(Base):
    def test_t1_held_reason(self):
        it = self.imp(reply_to=self.sent("human"))
        self.assertEqual([(d["state"], d.get("reason")) for d in self.deliv(it)], [("held", "human_reply")])
        it = self.imp(verified=False, cls="stranger")
        self.assertEqual([(d["state"], d.get("reason")) for d in self.deliv(it)], [("held", "stranger")])
        it = self.imp()
        self.assertEqual([(d["state"], d.get("reason")) for d in self.deliv(it)], [("queued", None)])
        ok = {"v": 1, "at": 1}
        for rec in (dict(ok, state="held", reason="human_reply"), dict(ok, state="refused", reason="not_friend"),
                    dict(ok, state="still_waiting", days=3), dict(ok, state="read", by="human")):
            GF.check_delivery(rec)
        for bad in (dict(ok, state="held", reason="not_friend"), dict(ok, state="refused", reason="human_reply"),
                    dict(ok, state="held", days=2), dict(ok, state="still_waiting", days=0),
                    dict(ok, state="still_waiting", days=31), dict(ok, state="still_waiting", days=True),
                    dict(ok, state="delivered", by="human"), dict(ok, state="read", by="ai")):
            with self.assertRaises(GF.ContractError, msg=bad):
                GF.check_delivery(bad)
        GF.check_receipt({"v": 1, "at": 1, "stage": "still_waiting", "days": 2})
        with self.assertRaises(GF.ContractError):
            GF.check_receipt({"v": 1, "at": 1, "stage": "read", "by": "bot"})

    def test_t2_seven_days_and_still_waiting(self):
        it = self.imp(reply_to=self.sent("human"))
        Q.expire_sweep(SEAT, env=self.env)
        self.assertEqual([d["state"] for d in self.deliv(it)], ["held"], "不到一天不送")
        self.age(it, 86400 + 60)
        Q.expire_sweep(SEAT, env=self.env)
        Q.expire_sweep(SEAT, env=self.env)
        self.assertEqual([(d["state"], d.get("days")) for d in self.deliv(it)], [("held", None), ("still_waiting", 1)],
                         "同一天只送一張")
        self.age(it, 2 * 86400 + 60)
        Q.expire_sweep(SEAT, env=self.env)
        self.assertEqual(self.deliv(it)[-1], dict(self.deliv(it)[-1], state="still_waiting", days=2))
        cur = Q._load_item(SEAT, it["id"], self.env)
        self.assertFalse(cur.get("expired"), "兩天還沒過期(24 小時那條不適用給本人的信)")
        self.age(it, 7 * 86400 + 60)
        Q.expire_sweep(SEAT, env=self.env)
        cur = Q._load_item(SEAT, it["id"], self.env)
        self.assertEqual((cur.get("expired"), cur.get("expired_why")), (True, Q.EXPIRED_WHY_HOLD_7D))
        self.assertEqual(self.deliv(it)[-1]["state"], "expired")
        Q.expire_sweep(SEAT, env=self.env)
        self.assertEqual(self.deliv(it)[-1]["state"], "expired", "過期後不再送 still_waiting")
        st = self.imp(verified=False, cls="stranger")
        self.age(st, 86400 + 60)
        Q.expire_sweep(SEAT, env=self.env)
        cur = Q._load_item(SEAT, st["id"], self.env)
        self.assertEqual(cur.get("expired_why"), Q.EXPIRED_WHY_HOLD_24H)
        self.assertEqual([d["state"] for d in self.deliv(st)], ["held", "expired"], "陌生信不送 still_waiting")
        tg = Q.enqueue(SEAT, "tg 來信", "Telegram:Bob", sender_kind="external_im", sender_origin="telegram:1", env=self.env,
                       status=Q.HOLD_STATUS, hold_why=Q.IM_ROOM_WHY,
                       im_info={"net": "telegram", "id": "1", "shown": "Bob", "relation": "friend"})
        self.age(tg, 3 * 86400)
        rows = Q.expire_sweep(SEAT, env=self.env)["rows"]
        self.assertNotIn(tg["id"], [r["id"] for r in rows], "Telegram 沒有 global:不送、3 天也不過期")

    def test_t3_human_read(self):
        it = self.imp(reply_to=self.sent("human"))
        r = Q.mark_read(SEAT, it["id"], "seat:" + SEAT, env=self.env, no_rev_check=True)
        self.assertEqual(r["reason"], "actor", "席自己讀全球待審照舊拒")
        self.seat_gate(True)
        r = Q.mark_read(SEAT, it["id"], "human:andy", env=self.env, no_rev_check=True)
        self.assertEqual((r["ok"], r["reason"]), (False, "actor"), "AI 席位冒充本人 ⇒ 拒")
        self.assertEqual([d["state"] for d in self.deliv(it)], ["held"])
        self.seat_gate(False)
        r = Q.mark_read(SEAT, it["id"], "human:andy", env=self.env, no_rev_check=True)
        self.assertTrue(r["ok"], r)
        last = self.deliv(it)[-1]
        self.assertEqual((last["state"], last.get("by")), ("read", "human"))
        self.assertEqual(set(last), {"v", "at", "state", "by"}, "只帶已讀 + 時間,不帶本人做了什麼")

    def test_t4_recent_shows_receipt(self):
        import chat_bridge as CB
        mid = GF.new_msgid()
        for r in ({"v": 1, "at": 10, "stage": "held", "detail": "human_reply"},
                  {"v": 1, "at": 20, "stage": "still_waiting", "days": 3}):
            GF.append_receipt(self.root, mid, r)
        self.assertEqual(CB.last_receipt(self.root, mid), {"stage": "still_waiting", "at": 20, "days": 3, "hop": "peer"})
        GF.append_receipt(self.root, mid, {"v": 1, "at": 30, "stage": "read", "by": "human"})
        self.assertEqual(CB.last_receipt(self.root, mid), {"stage": "read", "at": 30, "by": "human", "hop": "peer"})
        mid2 = GF.new_msgid()
        GF.append_receipt(self.root, mid2, {"v": 1, "at": 5, "stage": "refused", "detail": "<script>"})
        self.assertNotIn("detail", CB.last_receipt(self.root, mid2), "不在白名單的 detail 不帶出去")
        self.assertIsNone(CB.last_receipt(self.root, GF.new_msgid()))
        self.assertIsNone(CB.last_receipt(self.root, "../x"))


    def test_t4b_local_relayed_written_late(self):
        import chat_bridge as CB
        mid = GF.new_msgid()
        GF.append_receipt(self.root, mid, {"v": 1, "at": 198, "stage": "queued"})
        GF.append_receipt(self.root, mid, {"v": 1, "at": 203, "stage": "relayed", "detail": "2"})
        self.assertEqual(CB.last_receipt(self.root, mid), {"stage": "queued", "at": 198, "hop": "peer"})
        GF.append_receipt(self.root, mid, {"v": 1, "at": 230, "stage": "read"})
        self.assertEqual(CB.last_receipt(self.root, mid)["stage"], "read")
        only = GF.new_msgid()
        GF.append_receipt(self.root, only, {"v": 1, "at": 5, "stage": "relayed", "detail": "1"})
        self.assertEqual(CB.last_receipt(self.root, only)["stage"], "relayed")
    def test_t7_two_hops(self):
        """10-25 一鍵對話第 3 條:分得出「中繼站收到」和「對方收到」;停在中繼站超過 10 分鐘(跟綠點同一套)才提示"""
        import chat_bridge as CB
        mid = GF.new_msgid()
        GF.append_receipt(self.root, mid, {"v": 1, "at": 1000, "stage": "resolved"})
        self.assertEqual(CB.last_receipt(self.root, mid, now=1001)["hop"], "none")
        GF.append_receipt(self.root, mid, {"v": 1, "at": 1002, "stage": "relayed", "detail": "2"})
        r = CB.last_receipt(self.root, mid, now=1002 + GF.BEAT_GREEN_S - 1)
        self.assertEqual((r["stage"], r["hop"]), ("relayed", "relay"), "relayed 不能當送到")
        self.assertNotIn("hint", r, "還沒滿 10 分鐘不提示")
        r = CB.last_receipt(self.root, mid, now=1002 + GF.BEAT_GREEN_S)
        self.assertEqual(r["hint"], "peer_timeout")
        self.assertIn("加成好友", r["hint_text"])
        self.assertEqual(r["stage"], "relayed", "stage 代碼不改")
        GF.append_receipt(self.root, mid, {"v": 1, "at": 1500, "stage": "delivered"})
        r = CB.last_receipt(self.root, mid, now=99999)
        self.assertEqual((r["hop"], "hint" in r), ("peer", False))
        for st, hop in (("refused", "stopped"), ("expired", "stopped"), ("failed", "stopped"), ("held", "peer"),
                        ("queued", "peer"), ("still_waiting", "peer"), ("read", "peer")):
            self.assertEqual(CB.receipt_hop(st), hop, st)
        un = GF.new_msgid()
        GF.append_receipt(self.root, un, {"v": 1, "at": 5, "stage": "relayed", "detail": "1"})
        GF.append_receipt(self.root, un, {"v": 1, "at": 90000, "stage": "unconfirmed"})
        r = CB.last_receipt(self.root, un, now=90001)
        self.assertEqual((r["hop"], r["hint"]), ("relay", "peer_timeout"))
        q = GF.new_msgid()
        GF.append_receipt(self.root, q, {"v": 1, "at": 5, "stage": "queued"})
        GF.append_receipt(self.root, q, {"v": 1, "at": 90000, "stage": "unconfirmed", "detail": "queued"})
        r = CB.last_receipt(self.root, q, now=90001)
        self.assertEqual((r["hop"], "hint" in r), ("peer", False), "對方 queued 過 ⇒ 對方是收下了")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            self.assertTrue(d.get("aib.chat.hop_peer_timeout"), lang)


WIRE = r'''
import asyncio, json, sys, tempfile, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import dm_global_files as GF
import nostr_sdk as N
out = {}
td = Path(tempfile.mkdtemp())
GF.ensure_dirs(td)
(td / "sent").mkdir(exist_ok=True)
frm = N.Keys.generate().public_key().to_bech32()
rcv = object.__new__(CN.Receiver)
rcv.root, rcv.orphans = td, []
def apply(stage, tags):
    mid = GF.new_msgid()
    (td / "sent" / (mid + ".json")).write_text(json.dumps({"v": 1, "to": frm, "at": 1, "op": "send"}))
    rcv.on_receipt(dict(tags, **{"aib-receipt": stage}), frm, [mid])
    return GF.read_receipts(td, mid)
out["held"] = apply("held", {"aib-reason": "human_reply"})
out["held_bad"] = apply("held", {"aib-reason": "evil"})
out["held_refuse_code"] = apply("held", {"aib-reason": "not_friend"})
out["wait"] = apply("still_waiting", {"aib-days": "3"})
out["wait_bad"] = apply("still_waiting", {"aib-days": "99"})
out["read_h"] = apply("read", {"aib-read-by": "human"})
out["read_x"] = apply("read", {"aib-read-by": "bot"})
out["unknown"] = apply("teleported", {})
sent_tags = []
class Led:
    def received(self, m): return {"aib": True, "from": frm, "key": "k", "lang": "zh-TW"}
class Snd:
    async def _resolve(self, f): return 0, {"dm_relays": []}
class Out: success = True
class C:
    async def send_event(self, gw): return Out()
    async def shutdown(self): pass
async def client(r): return C()
CN._client = client
CN._keys = lambda k: N.Keys.generate()
real = CN.N
def mk(keys, pk, text, rumor_extra_tags=None):
    sent_tags.append([t.to_vec() for t in rumor_extra_tags])
    return None
CN.N = types.SimpleNamespace(nip17_make_private_msg=mk, PublicKey=real.PublicKey, Tag=real.Tag)
CN.audit = lambda *a, **k: None
rcv.led, rcv.sender = Led(), Snd()
m = GF.new_msgid()
async def go():
    await rcv.send_receipt(m, "held", reason="human_reply")
    await rcv.send_receipt(m, "held", reason="not_friend")
    await rcv.send_receipt(m, "refused", reason="human_reply")
    await rcv.send_receipt(m, "still_waiting", days=2)
    await rcv.send_receipt(m, "still_waiting", days=99)
    await rcv.send_receipt(m, "read", by="human")
    await rcv.send_receipt(m, "read", by="ai")
asyncio.run(go())
out["sent"] = [[t for t in tags if t[0] in ("aib-receipt", "aib-reason", "aib-days", "aib-read-by")] for tags in sent_tags]
print(json.dumps(out))
import shutil
shutil.rmtree(td, ignore_errors=True)
'''


@unittest.skipUnless(HAVE_VENV, "沒有 AIB_TEST_NOSTR_VENV")
class Wire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="aib-rcpt-wire-") as home:
            r = subprocess.run([str(VENV_PY), "-c", WIRE, str(COCKPIT)], capture_output=True, text=True, timeout=120,
                               env={"PATH": "/usr/bin:/bin", "HOME": home, "EZ2_AIB_STATE_ROOT": home})
        if r.returncode != 0:
            raise AssertionError(r.stdout + r.stderr)
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def strip(self, recs):
        return [{k: v for k, v in r.items() if k != "at"} for r in recs]

    def test_t5_receive(self):
        o = self.out
        self.assertEqual(self.strip(o["held"]), [{"v": 1, "stage": "held", "detail": "human_reply"}])
        self.assertEqual(self.strip(o["held_bad"]), [{"v": 1, "stage": "held"}], "未知值當沒帶")
        self.assertEqual(self.strip(o["held_refuse_code"]), [{"v": 1, "stage": "held"}], "拒收碼不算 held 原因")
        self.assertEqual(self.strip(o["wait"]), [{"v": 1, "stage": "still_waiting", "days": 3}])
        self.assertEqual(self.strip(o["wait_bad"]), [{"v": 1, "stage": "still_waiting"}])
        self.assertEqual(self.strip(o["read_h"]), [{"v": 1, "stage": "read", "by": "human"}])
        self.assertEqual(self.strip(o["read_x"]), [{"v": 1, "stage": "read"}])
        self.assertEqual(o["unknown"], [], "不認得的階段略過")

    def test_t6_send_tags(self):
        self.assertEqual(self.out["sent"], [
            [["aib-receipt", "held"], ["aib-reason", "human_reply"]],
            [["aib-receipt", "held"]],
            [["aib-receipt", "refused"]],
            [["aib-receipt", "still_waiting"], ["aib-days", "2"]],
            [["aib-receipt", "still_waiting"]],
            [["aib-receipt", "read"], ["aib-read-by", "human"]],
            [["aib-receipt", "read"]],
        ])

    def test_t6b_pump_separates_human_read(self):
        src = (COCKPIT / "chat_nostr.py").read_text(encoding="utf-8")
        i = src.index("async def pump_delivery")
        body = src[i:src.index("def _queue_read", i)]
        self.assertLess(body.index('r.get("by") == "human"'), body.index("self._queue_read(msgid)"),
                        "本人已讀先分出去,不進 AI 已讀那一批")


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell, rel="src/cockpit/dm_queue.py"):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_still_waiting_not_remembered(self):
        self._knife('                    item["still_waiting_days"] = days        # 先記再送',
                    '                    pass        # 先記再送', "Core.test_t2_seven_days_and_still_waiting")

    def test_k2_human_read_not_gated(self):
        self._knife('    if to == "read" and cls == "human" and _human_from_seat("human:x", env):',
                    '    if False:', "Core.test_t3_human_read")

    def test_k3_receipt_by_write_order(self):
        self._knife("    r = (remote or recs)[-1]", "    r = recs[-1]", "Core.test_t4b_local_relayed_written_late",
                    rel="src/cockpit/chat_bridge.py")

    def test_k4_relayed_counts_as_peer(self):
        self._knife('    if stage in ("relayed", "unconfirmed"):\n        return "relay"\n',
                    '    if stage in ("relayed", "unconfirmed"):\n        return "peer"\n',
                    "Core.test_t7_two_hops", rel="src/cockpit/chat_bridge.py")


if __name__ == "__main__":
    unittest.main()
