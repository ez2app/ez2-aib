#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理「門檻到就必定觸發」第三張:1a 自動啟動(SPEC-dm-triage-auto v2 §2;#466 拆出來的那半 + #466 r2 移過來的題)。
治具:純函式直接 import(暫存狀態根);整拍(triage_tick)沿用 test-dm-hold-notify-abb89-1015.py 的沙箱,
起背景行程一律注入假的,不起真 AI、不連網、不碰 ~/.ez2-aib。

T1 叫過、ACK 窗過了、沒人接 ⇒ 起背景整理一次(draft、帶 actor / slot);同一批不再起
T1b ACK 從本席上一次 Stop 起算(不是叫醒時刻)
T2 回合中(最後一次 UserPromptSubmit 比 Stop 晚)⇒ deferred seat-busy;aib now 更新也把窗往後推
T3 每小時上限(預設 2;triage-quota 設定可改)⇒ deferred per-hour-cap + next_at
T5 引擎不在 claude / codex ⇒ deferred engine-unsupported;T6 off ⇒ 不走自動那條(照舊再叫)
M3 build_record 換批次保留 headless_log / headless_key / headless / woke_revs(r2 M)
M2 全機名額:持鎖 + O_EXCL,滿了回 None;過期 / 持有人死了回收;只放自己那一格(r2 M)
M2b 整理鎖:讀-改-寫持 flock(拿不到 ⇒ held);登記的 pid 不在 ⇒ 登記作廢可重登(r2 M)
M4 自動那條也過叫醒閘(每日上限)+ 記憶體護欄(r2 M)
M5 spawn 失敗 ⇒ 不記去重鍵、名額放回、鎖放掉、deferred spawn-failed + next_at;下一拍照起(r2 M)
M6 看過 / 叫醒 / 餵信都認 (id, rev):同一封出新版 ⇒ 不算看過、算新的(r2 M)
L  每小時上限接獨立設定檔(每席 > 全艦 > 預設;壞檔 ⇒ 預設,不退下一層)(r2 L)
TM 回合邊界:mark_turn / ack_base
R1(#468 Codex r1):M1 持有人活著名額不因 TTL 回收(硬上限才收)/ M2 補記 pid 只動本次有效的鎖 /
   M3 背景行程死了叫醒判斷不再當 agent-working / M4 父程序預登記不吃掉上一輪看過的 / M5 帳記不下來不 spawn
R2(#468 Codex r2 M4):a 這輪 AI 失敗 / 提前返回也保留上一輪看過的;b 同一 id 留本輪處理的新版
Knife(本檔最後):每個守衛一刀,knife_copy + run_guarded,只跑目標那一格,要「那一格 FAIL」(ERROR 不算)。
   單跑刀:python3 -B tests/<本檔> Knife
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
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import dm_queue as Q  # noqa: E402
import dm_triage as TR  # noqa: E402
import dm_triage_agent as TA  # noqa: E402
import dm_triage_headless as H  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

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


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# 整拍:兩封未讀 ⇒ 第一拍叫醒;之後 tick(t + …) 看自動那條
SETUP = (
    "Q.LOAD_FN = lambda: (0.0, 0.0, 0.0)\n"
    "import dm_triage_headless as H\n"
    "import dm_triage_agent as TA\n"
    "H.MEM_FN = lambda: 50.0\n"
    "def put(msg, now, seat=%r, provider='claude'):\n"
    "    return Q.record(seat, msg, '@77777', status='delivered', rc=0, env=env, now=now,\n"
    "                    sender_kind='ai', sender_seat='77777', sender_source='pane_pid_chain',\n"
    "                    wid='@2', provider=provider, hub='aihub-t1')['id']\n"
    "spawned = []\n"
    "class _P: pid = None\n"
    "def _sp(seat, brand, env, draft=False, actor=None, slot=None):\n"
    "    spawned.append([seat, brand, draft, actor, slot]); return _P()\n"
    "Q.SPAWN_HEADLESS = _sp\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=lambda s, r: (False, 'idle'),\n"
    "                         deliver_fn=lambda it: 0, force=True)\n"
    "def tri(seat=%r):\n"
    "    return Q.TR.load(Q.triage_path(seat, env)) or {}\n"
    "def whys(out):\n"
    "    return [str((r or {}).get('why')) for r in (out or [])]\n"
    "t = int(__import__('time').time())\n"
    "A = Q.TR.ACK_S\n"
    "def two(provider='claude'):\n"
    "    put('第一件:要你看一下', t - 50, provider=provider)\n"
    "    put('第二件:另一個題目', t - 40, provider=provider)\n"
    "    return whys(tick(t))\n"
) % (SEAT, SEAT)


class T1AutoStart(unittest.TestCase):
    def test_starts_once_after_ack(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "w1 = whys(tick(t + A - 5))\n"
            "w2 = whys(tick(t + A + 5))\n"
            "n2 = len(spawned)\n"
            "w3 = whys(tick(t + A + 70))\n"
            "print(json.dumps({'w0': w0, 'w1': w1, 'w2': w2, 'w3': w3, 'n2': n2, 'sp': spawned,\n"
            "                  'h': tri().get('headless'), 'key': tri().get('headless_key'),\n"
            "                  'lock': Q._triage_lock(%r, env)}))\n") % SEAT)
        self.assertFalse(any(w.startswith("headless") for w in out["w0"]), "第一拍是叫醒,不是起背景整理:%r" % out)
        self.assertNotIn("headless-started", out["w1"], "ACK 窗內不起:%r" % out)
        self.assertIn("headless-started", out["w2"], "ACK 窗過了沒人接 ⇒ 起:%r" % out)
        self.assertEqual(out["n2"], 1, out)
        sp = out["sp"][0]
        self.assertEqual((sp[0], sp[1], sp[2]), (SEAT, "claude", True), "只擬不結:%r" % out)
        self.assertTrue(str(sp[3]).startswith("agent:%s/headless-auto-" % SEAT), out)
        self.assertRegex(str(sp[4]), r"^slot-[0-9]\.json$")
        self.assertEqual(len(out["sp"]), 1, "鎖還在 / 同一批 ⇒ 不再起:%r" % out)
        self.assertEqual((out["h"] or {}).get("state"), "started", out)
        self.assertTrue(out["key"], out)
        self.assertEqual((out["lock"] or {}).get("by"), sp[3], "先登記好鎖再起:%r" % out)

    def test_ack_counts_from_stop(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "Q.mark_turn(%r, 'prompt', env=env, now=t + 10)\n"
            "Q.mark_turn(%r, 'stop', env=env, now=t + 250)\n"
            "w1 = whys(tick(t + A + 5))\n"
            "w2 = whys(tick(t + 250 + A + 5))\n"
            "print(json.dumps({'w1': w1, 'w2': w2, 'n': len(spawned)}))\n") % (SEAT, SEAT))
        self.assertNotIn("headless-started", out["w1"], "從 Stop(t+250)起算還沒滿 300 秒:%r" % out)
        self.assertIn("headless-started", out["w2"], out)
        self.assertEqual(out["n"], 1, out)


class T2SeatBusy(unittest.TestCase):
    def test_in_turn_defers(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "Q.mark_turn(%r, 'stop', env=env, now=t - 100)\n"
            "Q.mark_turn(%r, 'prompt', env=env, now=t - 90)\n"
            "w1 = whys(tick(t + A + 5))\n"
            "print(json.dumps({'w1': w1, 'n': len(spawned), 'h': tri().get('headless')}))\n") % (SEAT, SEAT))
        self.assertEqual(out["w1"], ["headless-deferred:seat-busy"], out)
        self.assertEqual(out["n"], 0, out)
        self.assertEqual((out["h"] or {}).get("why"), "seat-busy", out)
        self.assertIsInstance((out["h"] or {}).get("next_at"), int, out)

    def test_now_update_pushes_window(self):
        out = HN.QBox(self).py(SETUP + (
            "import seat_now\n"
            "w0 = two()\n"
            "seat_now.write(%r, '別的工作', 1, 3, state_root=env.get('EZ2_AIB_STATE_ROOT') or None, now=t + 200)\n"
            "w1 = whys(tick(t + A + 5))\n"
            "print(json.dumps({'w1': w1, 'n': len(spawned)}))\n") % SEAT)
        self.assertNotIn("headless-started", out["w1"], "aib now 在窗內更新 = 有在動 ⇒ 窗往後推:%r" % out)
        self.assertEqual(out["n"], 0, out)


class T3PerHour(unittest.TestCase):
    def test_per_hour_cap_and_setting(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "p = Q.triage_path(%r, env); r = tri(); r['headless_log'] = [t - 600, t - 300]; Q.TR.write(p, r)\n"
            "w1 = whys(tick(t + A + 5))\n"
            "h1 = tri().get('headless')\n"
            "Q.triage_quota_set(3, seat=%r, env=env)\n"
            "w2 = whys(tick(t + A + 10))\n"
            "print(json.dumps({'w1': w1, 'h1': h1, 'w2': w2, 'n': len(spawned)}))\n") % (SEAT, SEAT))
        self.assertEqual(out["w1"], ["headless-deferred:per-hour-cap"], out)
        self.assertGreater(out["h1"]["next_at"], out["h1"]["at"], out)
        self.assertIn("headless-started", out["w2"], "設定改成 3 ⇒ 第 3 次起得來:%r" % out)
        self.assertEqual(out["n"], 1, out)

    def test_pure_cap(self):
        rec = {"headless_log": [NOW - 100, NOW - 50]}
        ok, why, nxt = H.should_start_auto(rec, NOW, None, 0.0, "claude", "agent", "k", per_hour=2)
        self.assertEqual((ok, why, nxt), (False, "per-hour-cap", NOW - 100 + 3600))
        ok, why, _n = H.should_start_auto(rec, NOW, None, 0.0, "claude", "agent", "k", per_hour=3)
        self.assertEqual((ok, why), (True, "headless"))


class T5Engine(unittest.TestCase):
    def test_unsupported_engine_deferred(self):
        out = HN.QBox(self).py(SETUP + (
            "Q._seat_records_all_cabins = lambda *a, **k: []\n"   # 登記表查不到 ⇒ 用信上的 provider(grok)
            "w0 = two(provider='grok')\n"
            "w1 = whys(tick(t + A + 5))\n"
            "print(json.dumps({'w1': w1, 'n': len(spawned)}))\n"))
        self.assertEqual(out["w1"], ["headless-deferred:engine-unsupported"], out)
        self.assertEqual(out["n"], 0, out)


class T6Off(unittest.TestCase):
    def test_off_keeps_old_rewake(self):
        out = HN.QBox(self).py(SETUP + (
            "Q.triage_auto_switch('off', env=env)\n"
            "w0 = two()\n"
            "w1 = whys(tick(t + Q.TR.ACK_S_OFF + 5))\n"
            "print(json.dumps({'w1': w1, 'n': len(spawned)}))\n"))
        self.assertEqual(out["n"], 0, "off ⇒ 不走自動那條:%r" % out)
        self.assertFalse(any(w.startswith("headless") for w in out["w1"]), out)


class M3Record(unittest.TestCase):
    def test_build_record_keeps_headless(self):
        prev = {"headless_log": [NOW - 10], "headless_key": "abc", "headless": {"state": "started"},
                "woke_revs": {"q_a": 1}}
        rec = TR.build_record(SEAT, [{"id": "q_b", "status": "delivered", "rev": 1, "ts": NOW}], prev=prev, now=NOW)
        self.assertEqual((rec.get("headless_log"), rec.get("headless_key"), rec.get("headless"), rec.get("woke_revs")),
                         ([NOW - 10], "abc", {"state": "started"}, {"q_a": 1}))


class M2Slots(Tmp):
    def test_reserve_full_and_release_own(self):
        a = H.reserve_slot("aaaaa", "k1", env=self.env, now=NOW)
        b = H.reserve_slot("bbbbb", "k2", env=self.env, now=NOW)
        c = H.reserve_slot("ccccc", "k3", env=self.env, now=NOW)
        self.assertTrue(a and b and a != b)
        self.assertIsNone(c, "全機名額 2 ⇒ 第三個拿不到")
        self.assertFalse(H.release_slot(a, "zzzzz", env=self.env), "別席的名額放不掉")
        self.assertFalse(H.release_slot("../x", "aaaaa", env=self.env))
        self.assertTrue(H.release_slot(a, "aaaaa", env=self.env))
        self.assertIsNotNone(H.reserve_slot("ccccc", "k3", env=self.env, now=NOW))

    def test_stale_and_dead_recycled(self):
        a = H.reserve_slot("aaaaa", "k1", env=self.env, now=NOW)
        b = H.reserve_slot("bbbbb", "k2", env=self.env, now=NOW)
        self.assertIsNone(H.reserve_slot("ccccc", "k3", env=self.env, now=NOW + 1))
        late = NOW + H.SLOT_TTL_S + 1
        p = Q.queue_root(self.env) / H.SLOT_DIR / b
        rec = json.loads(p.read_text())
        rec.update({"until": late + 9999, "pid": _dead_pid()})
        p.write_text(json.dumps(rec))
        got = H.reserve_slot("ccccc", "k3", env=self.env, now=late)
        got2 = H.reserve_slot("ddddd", "k4", env=self.env, now=late)
        self.assertEqual(sorted([str(got), str(got2)]), sorted([a, b]), "過期那格 + 持有人死了那格都回收")


class M2bLock(Tmp):
    def test_mutex_busy_returns_held(self):
        H_ = TA._Mutex(Q.seat_dir("aaaaa", self.env) / TA.AGENT_LOCK_MUTEX)
        old = TA.AGENT_LOCK_WAIT_S
        TA.AGENT_LOCK_WAIT_S = 0.2
        try:
            with H_:
                r = TA.agent_lock("aaaaa", "agent:aaaaa/x", env=self.env, now=NOW)
        finally:
            TA.AGENT_LOCK_WAIT_S = old
        self.assertEqual((r["ok"], r["reason"]), (False, "held"), r)
        self.assertFalse((Q.seat_dir("aaaaa", self.env) / TA.AGENT_LOCK_FILE).exists(), "拿不到 flock ⇒ 不寫")

    def test_dead_holder_can_be_replaced(self):
        r = TA.agent_lock("aaaaa", "agent:aaaaa/headless-auto-1", env=self.env, now=NOW, pid=_dead_pid())
        self.assertTrue(r["ok"], r)
        r2 = TA.agent_lock("aaaaa", "agent:aaaaa/sub", env=self.env, now=NOW + 1)
        self.assertTrue(r2["ok"], "持有的背景行程已不在 ⇒ 可重登:%r" % r2)
        r3 = TA.agent_lock("aaaaa", "agent:aaaaa/other", env=self.env, now=NOW + 2)
        self.assertFalse(r3["ok"], "沒記 pid 的子代理登記照 until:%r" % r3)


class M4Guards(unittest.TestCase):
    def test_daily_cap_and_mem(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "p = Q.triage_path(%r, env); r = tri(); r['wake_log'] = [t - 60 * i for i in range(Q.WAKE_DAILY_MAX)]; Q.TR.write(p, r)\n"
            "w1 = whys(tick(t + A + 5))\n"
            "h1 = tri().get('headless')\n"
            "r = tri(); r['wake_log'] = []; r.pop('headless', None); Q.TR.write(p, r)\n"
            "H.MEM_FN = lambda: 5.0\n"
            "w2 = whys(tick(t + A + 6))\n"
            "print(json.dumps({'w1': w1, 'h1': h1, 'w2': w2, 'n': len(spawned)}))\n") % SEAT)
        self.assertEqual(out["w1"], ["headless-deferred:daily-max"], out)
        self.assertIsInstance(out["h1"]["next_at"], int, out)
        self.assertEqual(out["w2"], ["headless-deferred:mem-low"], out)
        self.assertEqual(out["n"], 0, out)


class M5SpawnFail(unittest.TestCase):
    def test_spawn_fail_no_key_slot_released(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "def bad(*a, **k): raise OSError('boom')\n"
            "Q.SPAWN_HEADLESS = bad\n"
            "w1 = whys(tick(t + A + 5))\n"
            "r1 = tri()\n"
            "slots = sorted(x.name for x in (Q.queue_root(env) / H.SLOT_DIR).glob('slot-*.json'))\n"
            "lk = TA.agent_lock(%r, 'x', env=env, now=t + A + 5, status=True)\n"
            "Q.SPAWN_HEADLESS = _sp\n"
            "w2 = whys(tick(t + A + 6))\n"
            "print(json.dumps({'w1': w1, 'key1': r1.get('headless_key'), 'h1': r1.get('headless'), 'slots': slots,\n"
            "                  'held': lk.get('held'), 'w2': w2, 'n': len(spawned)}))\n") % SEAT)
        self.assertEqual(out["w1"], ["headless-deferred:spawn-failed"], out)
        self.assertIsNone(out["key1"], "spawn 失敗不記去重鍵:%r" % out)
        self.assertIsInstance(out["h1"]["next_at"], int, out)
        self.assertEqual(out["slots"], [], "名額放回:%r" % out)
        self.assertIsNone(out["held"], "鎖放掉:%r" % out)
        self.assertIn("headless-started", out["w2"], "下一拍照起(同一批):%r" % out)
        self.assertEqual(out["n"], 1, out)


class M6Rev(Tmp):
    def test_handled_needs_same_rev(self):
        self.assertEqual(TR.handled_ids({"q_a@1"}, ["q_a"], {"q_a": 1}), {"q_a"})
        self.assertEqual(TR.handled_ids({"q_a@1"}, ["q_a"], {"q_a": 2}), set(), "出新版 ⇒ 不算看過")
        self.assertEqual(TR.handled_ids({"q_a"}, ["q_a"], {"q_a": 2}), {"q_a"}, "舊格式只比 id")

    def test_wake_decision_rev(self):
        lock = {"until": 0, "released_at": NOW, "handled_ids": ["q_a@1", "q_b@1"]}
        rec = {"unread_ids": ["q_a", "q_b"], "revs": {"q_a": 1, "q_b": 1}}
        self.assertEqual(TR.wake_decision(rec, NOW + 10, lock)[1], "all-handled")
        rec["revs"] = {"q_a": 2, "q_b": 1}
        self.assertEqual(TR.wake_decision(rec, NOW + 10, lock)[1], "over-threshold", "新版本 ⇒ 要叫")

    def test_woke_revs_fresh(self):
        base = {"unread_ids": ["q_a", "q_b"], "woke_ids": ["q_a", "q_b"], "woke_at": NOW,
                "woke_revs": {"q_a": 1, "q_b": 1}, "revs": {"q_a": 1, "q_b": 1}}
        self.assertEqual(TR.wake_decision(dict(base), NOW + 400, None, ack_s=300)[1], "rewake")
        bumped = dict(base, revs={"q_a": 2, "q_b": 1})
        self.assertEqual(TR.wake_decision(bumped, NOW + 400, None, ack_s=300)[1], "over-threshold",
                         "叫過的那封出新版 = 新的一次叫")

    def test_run_skips_seen_pair(self):
        it = Q.record("aaaaa", "一件事", "@77777", status="delivered", rc=0, env=self.env, now=NOW,
                      sender_kind="ai", sender_seat="77777", sender_source="pane_pid_chain", wid="@2",
                      provider="claude", hub="aihub-t1")
        iid, rev = it["id"], Q._load_item("aaaaa", it["id"], self.env)["rev"]
        lockp = Q.seat_dir("aaaaa", self.env) / TA.AGENT_LOCK_FILE
        fed = []

        def ask(brand, prompt):
            fed.append(iid in prompt)
            return json.dumps([{"id": iid, "action": "leave"}]), "ok"

        lockp.write_text(json.dumps({"by": "x", "until": 0, "released_at": NOW, "handled_ids": ["%s@%s" % (iid, rev)]}))
        r1 = H.run("aaaaa", "claude", env=self.env, now=NOW + 10, ask=ask, dry=True)
        lockp.write_text(json.dumps({"by": "x", "until": 0, "released_at": NOW, "handled_ids": ["%s@%s" % (iid, rev + 7)]}))
        r2 = H.run("aaaaa", "claude", env=self.env, now=NOW + 20, ask=ask, dry=True)
        self.assertEqual(r1.get("reason"), "nothing", "同一封同一版看過 ⇒ 不再餵:%r" % r1)
        self.assertEqual((r2.get("reason"), fed), ("ok", [True]), "版本不同 ⇒ 照餵:%r %r" % (r2, fed))


class LQuota(Tmp):
    def test_layers(self):
        self.assertEqual(Q.triage_quota("aaaaa", self.env), (H.HEADLESS_PER_HOUR, "default"))
        Q.triage_quota_set(1, seat=None, env=self.env)
        self.assertEqual(Q.triage_quota("aaaaa", self.env), (1, "fleet"))
        Q.triage_quota_set(4, seat="aaaaa", env=self.env)
        self.assertEqual(Q.triage_quota("aaaaa", self.env), (4, "seat"))
        Q.triage_quota_seat_path("aaaaa", self.env).write_text("{bad")
        n, src = Q.triage_quota("aaaaa", self.env)
        self.assertEqual(n, H.HEADLESS_PER_HOUR, "壞檔 ⇒ 預設,不退到全艦的 1")
        self.assertIn("seat:unreadable", src)
        Q.triage_quota_seat_path("aaaaa", self.env).write_text(json.dumps(
            {"schema": Q.TRIAGE_QUOTA_SCHEMA, "per_hour": H.HEADLESS_PER_HOUR_MAX + 1}))
        self.assertEqual(Q.triage_quota("aaaaa", self.env)[0], H.HEADLESS_PER_HOUR, "超過上限 ⇒ 預設")
        with self.assertRaises(ValueError):
            Q.triage_quota_set(H.HEADLESS_PER_HOUR_MAX + 1, seat="aaaaa", env=self.env)


class TMTurn(Tmp):
    def test_mark_and_base(self):
        self.assertEqual(Q.ack_base("aaaaa", self.env), (False, None))
        Q.mark_turn("aaaaa", "prompt", env=self.env, now=NOW)
        self.assertEqual(Q.ack_base("aaaaa", self.env), (True, None), "prompt 之後還沒 Stop ⇒ 回合中")
        Q.mark_turn("aaaaa", "stop", env=self.env, now=NOW + 30)
        self.assertEqual(Q.ack_base("aaaaa", self.env), (False, NOW + 30))
        self.assertFalse(Q.mark_turn("../x", "stop", env=self.env))
        self.assertFalse(Q.mark_turn("aaaaa", "other", env=self.env))
        Q.turn_path("aaaaa", self.env).write_text(json.dumps({"prompt_at": True, "stop_at": "x"}))
        self.assertEqual(Q.ack_base("aaaaa", self.env), (False, None), "型別不對 ⇒ 不算回合中、退回叫醒時刻")


class R1Codex(unittest.TestCase):
    def test_m1_alive_holder_keeps_slot(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, EZ2_AIB_STATE_ROOT=td, EZ2_AIB_HOME=td, HOME=td)
            a = H.reserve_slot("aaaaa", "k1", env=env, now=NOW)
            b = H.reserve_slot("bbbbb", "k2", env=env, now=NOW)
            self.assertTrue(H.slot_set_pid(a, os.getpid(), env=env))
            later = NOW + H.SLOT_TTL_S + 5
            got = H.reserve_slot("ccccc", "k3", env=env, now=later)
            self.assertEqual(got, b, "沒記 pid 的那格過期回收;記了 pid、還活著的那格不收:%r" % got)
            self.assertIsNone(H.reserve_slot("ddddd", "k4", env=env, now=later), "還活著的那格不因 TTL 被搶")
            self.assertEqual(H.reserve_slot("ddddd", "k4", env=env, now=NOW + H.SLOT_MAX_S + 1), a, "硬上限到了才收")

    def test_m2_set_pid_only_live_own_lock(self):
        out = HN.QBox(self).py(SETUP + (
            "class _Q: pid = os.getpid()\n"
            "def child(seat, brand, env, draft=False, actor=None, slot=None):\n"
            "    TA.agent_lock(seat, actor, env=env, now=t + A + 5, release=True, handled=[])\n"   # 子程序已跑完放鎖
            "    spawned.append(actor); return _Q()\n"
            "Q.SPAWN_HEADLESS = child\n"
            "w0 = two()\n"
            "w1 = whys(tick(t + A + 5))\n"
            "lk = Q._triage_lock(%r, env)\n"
            "print(json.dumps({'w1': w1, 'until': lk.get('until'), 'pid': lk.get('pid')}))\n") % SEAT)
        self.assertIn("headless-started", out["w1"], out)
        self.assertEqual((out["until"], out["pid"]), (0, None), "子程序放掉的鎖不被父程序重建 / 重登:%r" % out)

    def test_m3_dead_holder_not_agent_working(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "w1 = whys(tick(t + A + 5))\n"
            "dead = __import__('subprocess').Popen([sys.executable, '-c', 'pass']); dead.wait()\n"
            "p = Q.seat_dir(%r, env) / TA.AGENT_LOCK_FILE; lk = json.loads(p.read_text()); lk['pid'] = dead.pid\n"
            "p.write_text(json.dumps(lk))\n"
            "w2 = whys(tick(t + A + 30))\n"
            "print(json.dumps({'w1': w1, 'w2': w2}))\n") % SEAT)
        self.assertIn("headless-started", out["w1"], out)
        self.assertNotEqual(out["w2"], ["agent-working"], "背景行程死了 ⇒ 不再當有人在做:%r" % out)
        self.assertEqual(out["w2"], ["headless-deferred:same-batch"], out)

    def test_m4_preregister_keeps_seen(self):
        out = HN.QBox(self).py(SETUP + (
            "fed = []\n"
            "def ask(brand, prompt):\n"
            "    ids = __import__('re').findall(r'q_[0-9a-f]{16}', prompt); fed.extend(ids)\n"
            "    return json.dumps([{'id': i, 'action': 'leave'} for i in dict.fromkeys(ids)]), 'ok'\n"
            "def child(seat, brand, env, draft=False, actor=None, slot=None):\n"
            "    H.run(seat, brand, env=env, now=t + A + 5, ask=ask, dry=True, actor=actor, slot=slot)\n"
            "    spawned.append(actor); return _P()\n"
            "Q.SPAWN_HEADLESS = child\n"
            "a = put('第一件:要你看一下', t - 50); b = put('第二件:另一個題目', t - 40)\n"
            "ra = Q._load_item(%r, a, env)['rev']\n"
            "p = Q.seat_dir(%r, env) / TA.AGENT_LOCK_FILE\n"
            "p.write_text(json.dumps({'by': 'agent:x/sub', 'at': t - 100, 'until': 0, 'released_at': t - 60,\n"
            "                         'handled_ids': [a + '@' + str(ra)]}))\n"
            "c = put('第三件', t - 30)\n"
            "w0 = whys(tick(t))\n"
            "w1 = whys(tick(t + A + 5))\n"
            "print(json.dumps({'w1': w1, 'a_fed': a in fed, 'b_fed': b in fed, 'n': len(spawned)}))\n") % (SEAT, SEAT))
        self.assertIn("headless-started", out["w1"], out)
        self.assertFalse(out["a_fed"], "上一輪看過的 A@rev 不再餵(父程序預登記不吃掉看過集合):%r" % out)
        self.assertTrue(out["b_fed"], out)

    def test_m5_unrecorded_no_spawn(self):
        out = HN.QBox(self).py(SETUP + (
            "w0 = two()\n"
            "real = Q.TR.write\n"
            "Q.TR.write = lambda p, r: False if (r.get('headless') or {}).get('state') == 'started' else real(p, r)\n"
            "w1 = whys(tick(t + A + 5))\n"
            "slots = sorted(x.name for x in (Q.queue_root(env) / H.SLOT_DIR).glob('slot-*.json'))\n"
            "lk = TA.agent_lock(%r, 'x', env=env, now=t + A + 5, status=True)\n"
            "print(json.dumps({'w1': w1, 'n': len(spawned), 'slots': slots, 'held': lk.get('held'),\n"
            "                  'key': tri().get('headless_key')}))\n") % SEAT)
        self.assertEqual(out["w1"], ["headless-deferred:state-unwritable"], out)
        self.assertEqual((out["n"], out["slots"], out["held"], out["key"]), (0, [], None, None),
                         "帳記不下來 ⇒ 不起、名額放回、鎖還原:%r" % out)


class R2Seen(Tmp):
    def _put(self, msg, now):
        return Q.record("aaaaa", msg, "@77777", status="delivered", rc=0, env=self.env, now=now, sender_kind="ai",
                        sender_seat="77777", sender_source="pane_pid_chain", wid="@2", provider="claude",
                        hub="aihub-t1")["id"]

    def _lock(self):
        return json.loads((Q.seat_dir("aaaaa", self.env) / TA.AGENT_LOCK_FILE).read_text())

    def test_a_failure_keeps_previous_seen(self):
        a = self._put("第一件", NOW)
        ra = Q._load_item("aaaaa", a, self.env)["rev"]
        (Q.seat_dir("aaaaa", self.env) / TA.AGENT_LOCK_FILE).write_text(json.dumps(
            {"by": "x", "until": 0, "released_at": NOW, "handled_ids": ["%s@%s" % (a, ra)]}))
        self._put("第二件", NOW + 5)
        r = H.run("aaaaa", "claude", env=self.env, now=NOW + 10, ask=lambda b, p: (None, "ai-failed"), dry=True)
        self.assertEqual(r.get("reason"), "ai-failed", r)
        self.assertIn("%s@%s" % (a, ra), self._lock().get("handled_ids") or [], "AI 失敗也不清掉上一輪的 A@rev")

    def test_b_newer_rev_wins(self):
        a = self._put("第一件", NOW)
        ra = Q._load_item("aaaaa", a, self.env)["rev"]
        TA.agent_lock("aaaaa", "agent:aaaaa/h", env=self.env, now=NOW + 1)
        TA.agent_lock("aaaaa", "agent:aaaaa/h", env=self.env, now=NOW + 2, release=True,
                      handled=["%s@%d" % (a, ra + 1), "%s@%d" % (a, ra)])
        self.assertEqual(self._lock()["handled_ids"], ["%s@%d" % (a, ra + 1)], "同一 id 留新版,舊版不蓋掉")


KNIVES = [
    ("A1", "src/cockpit/dm_queue.py", '    if not tidy_on and auto_on and why in ("rewake", "triage-stalled"):',
     '    if False:', "T1AutoStart.test_starts_once_after_ack"),
    ("A2", "src/cockpit/dm_queue.py", "        in_turn, ack_at = ack_base(seat, env) if auto_on else (False, None)",
     "        in_turn, ack_at = (False, None)", "T1AutoStart.test_ack_counts_from_stop"),
    ("A3", "src/cockpit/dm_queue.py", "        if in_turn:\n            r2 = dict(rec)",
     "        if False:\n            r2 = dict(rec)", "T2SeatBusy.test_in_turn_defers"),
    ("A4", "src/cockpit/dm_queue.py", "    marks = [v for v in (s_at, n_at) if v is not None]",
     "    marks = [v for v in (s_at,) if v is not None]", "T2SeatBusy.test_now_update_pushes_window"),
    ("A5", "src/cockpit/dm_triage_headless.py", "    if len(hour) >= int(per_hour):", "    if False:",
     "T3PerHour.test_pure_cap"),
    ("A6", "src/cockpit/dm_queue.py", "                                           per_hour=triage_quota(seat, env)[0], mem_free=H.MEM_FN())",
     "                                           mem_free=H.MEM_FN())", "T3PerHour.test_per_hour_cap_and_setting"),
    ("A7", "src/cockpit/dm_triage.py", '              "headless_log", "headless_key", "headless", "woke_revs"):',
     '              ):', "M3Record.test_build_record_keeps_headless"),
    ("A8", "src/cockpit/dm_triage_headless.py", "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)",
     "os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)", "M2Slots.test_reserve_full_and_release_own"),
    ("A9", "src/cockpit/dm_triage_headless.py", "            if p.exists() and _slot_stale(p, now):",
     "            if False:", "M2Slots.test_stale_and_dead_recycled"),
    ("A10", "src/cockpit/dm_triage_headless.py", "        if isinstance(cur, dict) and cur.get(\"seat\") not in (None, seat):",
     "        if False:", "M2Slots.test_reserve_full_and_release_own"),
    ("A11", "src/cockpit/dm_triage_agent.py", "        if mx.busy and not status:", "        if False:",
     "M2bLock.test_mutex_busy_returns_held"),
    ("A12", "src/cockpit/dm_triage_agent.py", "    if live and holder_dead(cur):", "    if False:",
     "M2bLock.test_dead_holder_can_be_replaced"),
    ("A13", "src/cockpit/dm_queue.py", "    if not gate_ok:\n        day = sorted(", "    if False:\n        day = sorted(",
     "M4Guards.test_daily_cap_and_mem"),
    ("A14", "src/cockpit/dm_triage_headless.py", "    if mem_free is not None and float(mem_free) < MEM_FREE_MIN_PCT:",
     "    if False:", "M4Guards.test_daily_cap_and_mem"),
    ("A15", "src/cockpit/dm_queue.py", "        H.release_slot(slot, seat, env)\n        return defer(\"spawn-failed\"",
     "        return defer(\"spawn-failed\"", "M5SpawnFail.test_spawn_fail_no_key_slot_released"),
    ("A16", "src/cockpit/dm_triage.py", "        if i in handled or (i in revs and pair(i, revs[i]) in handled):",
     "        if i in handled or any(str(h).split(\"@\")[0] == i for h in handled):", "M6Rev.test_handled_needs_same_rev"),
    ("A17", "src/cockpit/dm_triage.py",
     "        fresh |= {i for i in cur if i in wrevs and i in crevs and wrevs[i] != crevs[i]}",
     "        pass", "M6Rev.test_woke_revs_fresh"),
    ("A18", "src/cockpit/dm_triage_headless.py",
     '            rows = [r for r in rows if not TR.handled_ids(seen, [r["id"]], {r["id"]: r.get("rev")})]',
     "            rows = rows", "M6Rev.test_run_skips_seen_pair"),
    ("A19", "src/cockpit/dm_queue.py", '            return H.HEADLESS_PER_HOUR, "%s:%s" % (layer, v)',
     "            continue", "LQuota.test_layers"),
    ("R1", "src/cockpit/dm_triage_headless.py",
     "        return not isinstance(at, int) or isinstance(at, bool) or now >= at + SLOT_MAX_S\n    return now >= until",
     "        return now >= until\n    return now >= until", "R1Codex.test_m1_alive_holder_keeps_slot"),
    ("R2", "src/cockpit/dm_queue.py", "        TA.agent_lock_set_pid(seat, actor, pid, env=env)",
     "        TA.agent_lock(seat, actor, env=env, now=now, pid=pid)", "R1Codex.test_m2_set_pid_only_live_own_lock"),
    ("R3", "src/cockpit/dm_queue.py", "        normal, why, rewake = TR.wake_decision(rec, now, _live_triage_lock(seat, env), _load1(),",
     "        normal, why, rewake = TR.wake_decision(rec, now, _triage_lock(seat, env), _load1(),",
     "R1Codex.test_m3_dead_holder_not_agent_working"),
    ("R4", "src/cockpit/dm_triage_agent.py", '    for k in ("handled_ids", "released_at"):\n        if k in cur:\n            rec[k] = cur[k]',
     "    pass", "R1Codex.test_m4_preregister_keeps_seen"),
    ("R5", "src/cockpit/dm_queue.py", "    if not TR.write(path, r2):\n        TA.agent_lock_restore(",
     "    if not (TR.write(path, r2) or True):\n        TA.agent_lock_restore(", "R1Codex.test_m5_unrecorded_no_spawn"),
    ("R6", "src/cockpit/dm_triage_headless.py", '    handled = sorted(h for h in seen if "@" in str(h))\n    try:',
     "    handled = []\n    try:", "R2Seen.test_a_failure_keeps_previous_seen"),
    ("R7", "src/cockpit/dm_triage_agent.py", "                if i in given and not (old is not None",
     "                if False and not (old is not None", "R2Seen.test_b_newer_rev_wins"),
    ("A20", "src/cockpit/dm_queue.py", "    in_turn = p_at is not None and (s_at is None or p_at > s_at)",
     "    in_turn = False", "TMTurn.test_mark_and_base"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name, "test-dm-hold-notify-abb89-1015.py",
                                                                "test-dm-reliability-abb89-1015.py")) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=60, timeout=300)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
