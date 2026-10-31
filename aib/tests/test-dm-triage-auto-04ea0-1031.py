#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理「門檻到就必定觸發」(Andy 10-31 15:3x;SPEC-dm-triage-auto v2,@04ea0 接 @fd787)。
治具:純函式直接 import;整拍(triage_tick)沿用 test-dm-hold-notify-abb89-1015.py 的沙箱(抄進暫存根、假 tmux),
起背景行程一律注入假的,不起真 AI、不連網、不碰 ~/.ez2-aib。

A1 開關:預設 draft;off / draft / default 可切;on 這一版不收(切不進去、檔裡寫 on 也當沒有)
S1 should_start_auto:可起 / 子代理登記中 / load 高(帶下次時間)/ 引擎不支援(grok、digest)/ 同批 / 每小時 2 次 / 全機 2 個
S2 batch_key:順序無關;rev 變 = 新鍵(T7 零結案不重起要有新信)
S3 全機計數只算 headless 登記且沒過期的
T1 整拍:叫醒 → ACK 過了沒人接 ⇒ 起背景整理(draft=True)一次,不再塞「再叫一次」;同批不再起(T9 一次)
T6 triage_auto=off ⇒ 走原本的再叫(不起背景整理)
T11 起不了 ⇒ triage.json 記 deferred + 原因 + 下次時間
D1 只擬不結:apply(dry) 不改任何信,驗得過的標 would
D2 席位 15 分內自己讀過的信不餵(T14)
G1 守門放行:長工作(租約 pid_start + aib now 忙碌且主項目是長工作;pid 重用 / 缺 pid_start / 一般工作反例)/
   真人親打 10 分內(抬頭交 dm_header 判;別席 dm 引用艦長室字樣不算、艦長室網頁 / 真人抬頭算)/ 背景整理在跑;
   對話檔讀不到 = 判斷不了
G3 同批限擋:含 rev;子集合 + 擋下後本席真的處理過才放;新信 / 新 rev / 自然過期 / 過 30 分照擋
G2 判斷不了 ⇒ 放行留痕 FAILOPEN;triage_auto=off ⇒ 新放行條件全部不看
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src" / "policy" / "hooks"))
import dm_queue as Q  # noqa: E402
import dm_triage as TR  # noqa: E402
import dm_triage_headless as H  # noqa: E402
import dm_pending_gate as G  # noqa: E402

_spec = importlib.util.spec_from_file_location("dmhold_fixture", HERE / "test-dm-hold-notify-abb89-1015.py")
HN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HN)
SEAT = HN.SEAT
NOW = 1_800_000_000


def setUpModule():
    HN.setUpModule()


def tearDownModule():
    HN.tearDownModule()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        td = os.path.realpath(self.tmp.name)
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=td, EZ2_AIB_HOME=td, HOME=td,
                        AIB_HOST_LEASE_FILE=os.path.join(td, "lease.json"))
        Q.queue_root(self.env).mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.tmp.cleanup)


class A1Switch(Tmp):
    def test_default_and_switch(self):
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("draft", "default"))
        self.assertEqual(Q.triage_auto_switch("off", env=self.env), ("off", "fleet"))
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("off", "fleet"))
        self.assertEqual(Q.triage_auto_switch("draft", seat="aaaaa", env=self.env), ("draft", "seat"))
        self.assertEqual(Q.triage_auto_mode("bbbbb", self.env), ("off", "fleet"))
        self.assertEqual(Q.triage_auto_switch("default", seat="aaaaa", env=self.env), ("off", "fleet"))
        with self.assertRaises(ValueError):
            Q.triage_auto_switch("on", env=self.env)

    def test_on_in_file_ignored(self):
        p = Q.triage_auto_seat_path("aaaaa", self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": Q.TRIAGE_AUTO_SCHEMA, "mode": "on"}), encoding="utf-8")
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("draft", "default"))


class S1ShouldStart(unittest.TestCase):
    def go(self, rec=None, lock=None, load1=0.0, brand="claude", mode="agent", key="k1", live=0):
        return H.should_start_auto(rec or {}, NOW, lock, load1, brand, mode, key, live)

    def test_reasons(self):
        self.assertEqual(self.go()[:2], (True, "headless"))
        self.assertEqual(self.go(lock={"by": "agent:x/sub", "at": NOW - 5, "until": NOW + 600})[1], "agent-working")
        ok, why, nxt = self.go(load1=13.0)
        self.assertEqual((ok, why, nxt), (False, "load-high", NOW + 60))
        self.assertEqual(self.go(brand="grok")[1], "engine-unsupported")
        self.assertEqual(self.go(mode="digest")[1], "engine-unsupported")
        self.assertEqual(self.go(rec={"headless_key": "k1"})[1], "same-batch")
        self.assertEqual(self.go(rec={"headless_key": "k0"})[1], "headless")
        ok, why, nxt = self.go(rec={"headless_log": [NOW - 3000, NOW - 100]})
        self.assertEqual((ok, why, nxt), (False, "per-hour-cap", NOW - 3000 + 3600))
        self.assertEqual(self.go(rec={"headless_log": [NOW - 4000, NOW - 100]})[1], "headless")
        self.assertEqual(self.go(live=2)[1], "global-cap")


class S2Key(unittest.TestCase):
    def test_order_and_rev(self):
        a = H.batch_key([("q_1", 1), ("q_2", 3)])
        self.assertEqual(a, H.batch_key([("q_2", 3), ("q_1", 1)]))
        self.assertNotEqual(a, H.batch_key([("q_1", 2), ("q_2", 3)]))
        self.assertNotEqual(a, H.batch_key([("q_1", 1), ("q_2", 3), ("q_3", 1)]))


class S3Global(Tmp):
    def test_counts_live_headless_only(self):
        def lock(seat, by, until):
            d = Q.seat_dir(seat, self.env)
            d.mkdir(parents=True, exist_ok=True)
            (d / Q.TRIAGE_AGENT_LOCK_FILE).write_text(json.dumps({"by": by, "until": until}), encoding="utf-8")
        lock("aaaaa", "agent:aaaaa/headless-120000", NOW + 100)
        lock("bbbbb", "agent:bbbbb/headless-120000", NOW - 1)
        lock("ccccc", "agent:ccccc/sub", NOW + 100)
        lock("ddddd", "agent:ddddd/headless-120100", NOW + 100)
        self.assertEqual(H.live_headless_count(self.env, NOW), 2)


SETUP = (
    "Q.LOAD_FN = lambda: (0.0, 0.0, 0.0)\n"
    "import dm_triage_headless as H\n"
    "def put(msg, now, seat=%r, provider='claude'):\n"
    "    return Q.record(seat, msg, '@77777', status='delivered', rc=0, env=env, now=now,\n"
    "                    sender_kind='ai', wid='@2', provider=provider, hub='aihub-t1')['id']\n"
    "spawned = []\n"
    "def _sp(seat, brand, env, draft=False): spawned.append([seat, brand, draft])\n"
    "Q.SPAWN_HEADLESS = _sp\n"
    "delivered = []\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=lambda s, r: (False, 'idle'),\n"
    "                         deliver_fn=lambda it: delivered.append(1) or 0, force=True)\n"
    "def tri(seat=%r):\n"
    "    return Q.TR.load(Q.triage_path(seat, env)) or {}\n"
    "t = int(__import__('time').time())\n"
    "A = Q.TR.ACK_S\n"
) % (SEAT, SEAT)


def _whys(out):
    return [str((r or {}).get("why")) for r in (out or [])]


class T1Tick(unittest.TestCase):
    def test_no_pickup_starts_headless_draft_once(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "r1 = tick(t + 10); w1 = tri().get('woke_at')\n"
            "r2 = tick(t + 10 + A - 1)\n"
            "r3 = tick(t + 10 + A + 1); n3 = len(spawned)\n"
            "r4 = tick(t + 10 + A + 61); n4 = len(spawned)\n"
            "print(json.dumps({'r': [[x.get('why') for x in (r or [])] for r in (r1, r2, r3, r4)],\n"
            "                  'w1': w1, 'n': [n3, n4], 'sp': spawned, 'h': tri().get('headless')}))\n"))
        self.assertIsNotNone(out["w1"], out)
        self.assertIn("headless-started", out["r"][2], out)
        self.assertEqual(out["n"], [1, 1], "ACK 過了起一次、同批不再起:%r" % out)
        self.assertEqual(out["sp"][0], [SEAT, "claude", True], out)
        self.assertEqual(out["h"]["state"], "deferred", out)          # 第二拍:同一批 ⇒ deferred same-batch
        self.assertEqual(out["h"]["why"], "same-batch", out)

    def test_off_keeps_rewake(self):
        out = HN.QBox(self).py(SETUP + (
            "Q.triage_auto_switch('off', env=env)\n"
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); r = tick(t + 10 + A + 1)\n"
            "print(json.dumps({'r': [x.get('why') for x in (r or [])], 'sp': spawned}))\n"))
        self.assertEqual(out["sp"], [], out)
        self.assertNotIn("headless-started", out["r"], out)

    def test_deferred_recorded(self):
        out = HN.QBox(self).py(SETUP + (
            "H.BRANDS = ('codex',)\n"                 # 這一席登記的是 claude;把可起的家縮掉 ⇒ engine-unsupported
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); r = tick(t + 10 + A + 1)\n"
            "print(json.dumps({'r': [x.get('why') for x in (r or [])], 'sp': spawned, 'h': tri().get('headless')}))\n"))
        self.assertEqual(out["sp"], [], out)
        self.assertIn("headless-deferred:engine-unsupported", out["r"], out)
        self.assertEqual((out["h"]["state"], out["h"]["why"]), ("deferred", "engine-unsupported"), out)


class D1Dry(unittest.TestCase):
    def test_dry_changes_nothing(self):
        out = HN.QBox(self).py(SETUP + (
            "import dm_triage_agent as TA\n"
            "a = put('PR 合了通知 https://github.com/ez2app/ez2-aib/pull/1', t)\n"
            "b = put('第二件:另一個題目要你決定', t)\n"
            "Q.mark_read(%r, b, 'seat:%s', env=env, now=t + 1)\n"
            "before = {i: (Q._load_item(%r, i, env) or {}).get('status') for i in (a, b)}\n"
            "dec = [{'id': a, 'action': 'close', 'evidence': 'https://github.com/ez2app/ez2-aib/pull/1', 'wait': '', 'draft': '', 'why': ''},\n"
            "       {'id': b, 'action': 'wait', 'evidence': '', 'wait': 'PR #2', 'draft': '', 'why': ''}]\n"
            "res = H.apply(%r, dec, 'agent:%s/headless-1', env=env, now=t + 5, gh=lambda u: True, dry=True)\n"
            "after = {i: (Q._load_item(%r, i, env) or {}).get('status') for i in (a, b)}\n"
            "w = (Q._load_item(%r, b, env) or {}).get('wait')\n"
            "print(json.dumps({'before': before, 'after': after, 'res': res, 'w': w}))\n"
        ) % (SEAT, SEAT, SEAT, SEAT, SEAT, SEAT, SEAT))
        self.assertEqual(out["before"], out["after"], out)
        self.assertFalse(out["w"], out)
        self.assertTrue(all(r.get("would") and not r.get("applied") for r in out["res"]), out)


class D2SeatRead(unittest.TestCase):
    def test_recent_seat_read_skipped(self):
        it = {"read_at": NOW - 60, "read_by": "seat:aaaaa"}
        self.assertTrue(H._seat_read_recent(it, NOW))
        self.assertFalse(H._seat_read_recent(dict(it, read_at=NOW - H.SEAT_READ_SKIP_S - 1), NOW))
        self.assertFalse(H._seat_read_recent(dict(it, read_by="system"), NOW))
        self.assertFalse(H._seat_read_recent(dict(it, read_by="agent:aaaaa/headless-1"), NOW))
        self.assertFalse(H._seat_read_recent({}, NOW))


def _transcript(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for when, content in entries:
            ts = datetime.datetime.fromtimestamp(when, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            f.write(json.dumps({"type": "user", "timestamp": ts, "message": {"role": "user", "content": content}},
                               ensure_ascii=False) + "\n")


HITS = [{"id": "q_a"}, {"id": "q_b"}]


import dm_header as DH  # noqa: E402


def _hdr(code, name, when=NOW):
    return DH.build(DH.party(code, name, addr="100.1.1.1", cabin="559f"),
                    DH.party("aaaaa", "工程師", addr="100.1.1.1", cabin="559f"), when)


LEASE_START = "Sat Oct 31 21:00:00 2026"


class G1Release(Tmp):
    def tr(self, entries):
        p = os.path.join(self.tmp.name, "t.jsonl")
        _transcript(p, entries)
        return p

    def rw(self, hits=HITS, now=NOW, **kw):
        kw.setdefault("long_work", False)
        return G.release_why("aaaaa", hits, now, env=self.env, **kw)

    def test_long_work(self):
        self.assertEqual(self.rw(long_work=True), "long-work")
        self.assertIsNone(self.rw())

    def lease(self, **over):
        d = {"owner": "@aaaaa", "what": "推 @aaaaa 的東西", "pid": os.getpid(), "pid_start": LEASE_START,
             "expiry_epoch": NOW + 600}
        d.update(over)
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(d), encoding="utf-8")

    def lw(self, seat="aaaaa", start=LEASE_START, rec=None):
        rec = {"item": "推送 ez2-ai #72(全套)", "state": "busy"} if rec is None else rec
        return Q._seat_long_work(seat, self.env, NOW, proc_start=lambda pid: start, now_rec=rec)

    def test_long_work_reads_structured_lease(self):
        self.lease()
        self.assertTrue(self.lw())
        self.assertFalse(self.lw(seat="bbbbb"))                                     # what 字串有 @aaaaa 也不算
        self.assertFalse(self.lw(start="Sat Oct 31 09:00:00 2026"), "pid 重用(起始時刻對不上)")
        self.assertFalse(self.lw(rec={"item": "寫 SPEC v2", "state": "busy"}), "一般工作不算長工作")
        self.assertFalse(self.lw(rec={"item": "推送 #72", "state": "reply"}), "等回應不算忙")
        self.assertFalse(self.lw(rec={}), "沒有 aib now 紀錄")
        self.lease(pid_start=None)
        self.assertFalse(self.lw(), "租約沒記 pid_start 不算")
        self.lease(owner="@bbbbb")
        self.assertFalse(self.lw())
        self.lease(expiry_epoch=NOW - 1)
        self.assertFalse(self.lw())

    def test_human_turn_by_header(self):
        self.assertEqual(self.rw(transcript=self.tr([(NOW - 120, "好，那就照這樣做")])), "human-turn")
        self.assertIsNone(self.rw(transcript=self.tr([(NOW - G.HUMAN_RECENT_S - 5, "好，那就照這樣做")])))
        dm = self.tr([(NOW - 9000, "很久以前真人講的"),
                      (NOW - 60, _hdr("34396", "Ez2 主機艦長") + " 收到,照這樣改〔艦長室〕"),
                      (NOW - 30, [{"type": "tool_result", "content": "x"}])])
        self.assertIsNone(self.rw(transcript=dm), "別席 dm(正文引用艦長室字樣也一樣)不算真人親打")
        room = self.tr([(NOW - 60, _hdr(DH.ROOM_CODE, DH.ROOM_NAME) + " 這樣改")])
        self.assertEqual(self.rw(transcript=room), "human-turn", "艦長室網頁寄的算")
        hub = self.tr([(NOW - 60, _hdr(DH.HUMAN_CODE, DH.HUMAN_NAME) + " 好")])
        self.assertEqual(self.rw(transcript=hub), "human-turn")
        sysm = self.tr([(NOW - 60, "<system-reminder>x</system-reminder>")])
        self.assertIsNone(self.rw(transcript=sysm))

    def test_transcript_unreadable_undetermined(self):
        got = self.rw(transcript=os.path.join(self.tmp.name, "nope.jsonl"))
        self.assertTrue(str(got).startswith(G.UNDETERMINED), got)

    def test_headless_running(self):
        d = Q.seat_dir("aaaaa", self.env)
        d.mkdir(parents=True, exist_ok=True)
        (d / Q.TRIAGE_AGENT_LOCK_FILE).write_text(json.dumps({"by": "agent:aaaaa/headless-1", "until": NOW + 60}))
        self.assertEqual(self.rw(), "headless-running")


H3 = [{"id": "q_a", "rev": 1}, {"id": "q_b", "rev": 1}, {"id": "q_c", "rev": 2}]


class G3SameBatch(Tmp):
    def rw(self, hits, now, handled):
        with mock.patch.object(G, "_handled_since", return_value=handled) as m:
            got = G.release_why("aaaaa", hits, now, env=self.env, long_work=False)
        return got, m

    def test_rules(self):
        G._remember_block("aaaaa", H3, NOW, self.env)
        self.assertIsNone(self.rw(H3, NOW + 60, True)[0], "同一批沒動作 ⇒ 照擋")
        self.assertIsNone(self.rw(H3[1:], NOW + 60, False)[0], "離開的那封不是本席處理的(過期 / 別人收走)⇒ 照擋")
        got, m = self.rw(H3[1:], NOW + 60, True)
        self.assertEqual(got, "seat-working")
        self.assertIsNotNone(m.call_args, "要核對本席有沒有處理")
        self.assertEqual(m.call_args[0][1:3], (["q_a"], NOW))
        self.assertIsNone(self.rw([H3[1], dict(H3[2], rev=3)], NOW + 60, True)[0], "剩下的有新 rev ⇒ 照擋")
        self.assertIsNone(self.rw(H3[1:] + [{"id": "q_d", "rev": 1}], NOW + 60, True)[0], "有新信 ⇒ 照擋")
        self.assertIsNone(self.rw(H3[1:], NOW + G.SAME_BATCH_S + 1, True)[0], "過了 30 分 ⇒ 照擋")

    def test_handled_since_reads_items(self):
        items = {"q_a": ({"status": "read", "read_at": NOW + 10, "read_by": "seat:aaaaa"}, "ok"),
                 "q_b": ({"status": "read", "read_at": NOW - 10, "read_by": "seat:aaaaa"}, "ok"),
                 "q_c": ({"status": "read", "read_at": NOW + 10, "read_by": "system"}, "ok"),
                 "q_d": ({"status": "expired_now", "read_at": NOW + 10}, "ok"),
                 "q_e": ({"status": "done", "read_at": NOW - 99, "done_at": NOW + 5, "read_by": "seat:aaaaa"}, "ok")}
        with mock.patch.object(Q, "_load_item_raw", side_effect=lambda s, i, e=None: items.get(i, (None, "missing"))):
            self.assertTrue(G._handled_since("aaaaa", ["q_a"], NOW, self.env))
            self.assertFalse(G._handled_since("aaaaa", ["q_b", "q_c", "q_d", "q_x"], NOW, self.env))
            self.assertTrue(G._handled_since("aaaaa", ["q_e"], NOW, self.env))


class G2Check(Tmp):
    def test_undetermined_fails_open_with_note(self):
        rows = [{"id": "q_a", "status": "delivered", "sender_seat": "zzzzz", "ts": NOW - 600, "rev": 1}]
        T = lambda key, **kw: key  # noqa: E731
        notes = []
        payload = {"errors": [], "tabs": {"waiting": {"rows": rows}, "read": {"rows": []}}}
        with mock.patch.object(Q, "tabs_payload", return_value=payload), \
                mock.patch.object(G, "release_why", side_effect=RuntimeError("x")):
            blk, _m = G.check("aaaaa", T, notes.append, now=NOW, env=self.env)
        self.assertFalse(blk)
        self.assertTrue(any("FAILOPEN" in n and "RuntimeError" in n for n in notes), notes)

    def test_off_ignores_releases(self):
        rows = [{"id": "q_a", "status": "delivered", "sender_seat": "zzzzz", "ts": NOW - 600, "rev": 1}]
        T = lambda key, **kw: key  # noqa: E731
        notes = []
        payload = {"errors": [], "tabs": {"waiting": {"rows": rows}, "read": {"rows": []}}}
        with mock.patch.object(Q, "tabs_payload", return_value=payload), \
                mock.patch.object(G, "release_why", return_value="long-work"):
            blk, _m = G.check("aaaaa", T, notes.append, now=NOW, env=self.env)
            self.assertFalse(blk)
            Q.triage_auto_switch("off", env=self.env)
            blk, _m = G.check("aaaaa", T, notes.append, now=NOW, env=self.env)
            self.assertTrue(blk)


if __name__ == "__main__":
    unittest.main()
