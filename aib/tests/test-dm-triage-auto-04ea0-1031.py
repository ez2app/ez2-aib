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
G1 守門放行:長工作 / 真人親打 10 分內 / 背景整理在跑 / 同批有在處理;dm 外框進來的不算真人(T4 補格)
G2 同批沒動作 ⇒ 照擋;triage_auto=off ⇒ 新放行條件全部不看
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


class G1Release(Tmp):
    def tr(self, entries):
        p = os.path.join(self.tmp.name, "t.jsonl")
        _transcript(p, entries)
        return p

    def test_long_work(self):
        self.assertEqual(G.release_why("aaaaa", HITS, NOW, env=self.env, long_work=True), "long-work")
        self.assertIsNone(G.release_why("aaaaa", HITS, NOW, env=self.env, long_work=False))

    def test_long_work_reads_structured_lease(self):
        lease = {"owner": "@aaaaa", "what": "推 @aaaaa 的東西", "pid": os.getpid(), "expiry_epoch": NOW + 600}
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(lease), encoding="utf-8")
        self.assertTrue(Q._seat_long_work("aaaaa", self.env, NOW))
        self.assertFalse(Q._seat_long_work("bbbbb", self.env, NOW))                 # what 字串有 @aaaaa 也不算
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(dict(lease, owner="@bbbbb")), encoding="utf-8")
        self.assertFalse(Q._seat_long_work("aaaaa", self.env, NOW))
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(dict(lease, expiry_epoch=NOW - 1)), encoding="utf-8")
        self.assertFalse(Q._seat_long_work("aaaaa", self.env, NOW))

    def test_human_turn_only_typed(self):
        human = self.tr([(NOW - 120, "好，那就照這樣做")])
        self.assertEqual(G.release_why("aaaaa", HITS, NOW, env=self.env, transcript=human, long_work=False), "human-turn")
        old = self.tr([(NOW - G.HUMAN_RECENT_S - 5, "好，那就照這樣做")])
        self.assertIsNone(G.release_why("aaaaa", HITS, NOW, env=self.env, transcript=old, long_work=False))
        dm = self.tr([(NOW - 9000, "很久以前真人講的"),
                      (NOW - 60, "IP:100.1.1.1/559f/艦長@34396 >> IP:100.1.1.1/559f/工程師@aaaaa 10-31 12:00 收到"),
                      (NOW - 30, [{"type": "tool_result", "content": "x"}])])
        self.assertIsNone(G.release_why("aaaaa", HITS, NOW, env=self.env, transcript=dm, long_work=False),
                          "別席 dm 進來不算真人親打")
        cap = self.tr([(NOW - 60, "IP:100.1.1.1/559f/x@34396 >> y 這樣改〔艦長室〕")])
        self.assertEqual(G.release_why("aaaaa", HITS, NOW, env=self.env, transcript=cap, long_work=False), "human-turn")

    def test_headless_running(self):
        d = Q.seat_dir("aaaaa", self.env)
        d.mkdir(parents=True, exist_ok=True)
        (d / Q.TRIAGE_AGENT_LOCK_FILE).write_text(json.dumps({"by": "agent:aaaaa/headless-1", "until": NOW + 60}))
        self.assertEqual(G.release_why("aaaaa", HITS, NOW, env=self.env, long_work=False), "headless-running")

    def test_same_batch_working_vs_idle(self):
        G._remember_block("aaaaa", HITS, NOW, self.env)
        self.assertIsNone(G.release_why("aaaaa", HITS, NOW + 60, env=self.env, long_work=False), "沒動作 ⇒ 照擋")
        self.assertEqual(G.release_why("aaaaa", HITS[1:], NOW + 60, env=self.env, long_work=False), "seat-working")
        self.assertIsNone(G.release_why("aaaaa", HITS[1:], NOW + G.SAME_BATCH_S + 1, env=self.env, long_work=False))


class G2Check(Tmp):
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
