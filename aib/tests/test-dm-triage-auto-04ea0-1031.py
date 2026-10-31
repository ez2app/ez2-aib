#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理「門檻到就必定觸發」(Andy 10-31 15:3x;SPEC-dm-triage-auto v2,@04ea0 接 @fd787)。
治具:純函式直接 import;整拍(triage_tick)沿用 test-dm-hold-notify-abb89-1015.py 的沙箱(抄進暫存根、假 tmux),
起背景行程一律注入假的,不起真 AI、不連網、不碰 ~/.ez2-aib。

A1 開關:預設 draft;off / draft / default 可切;on 這一版不收(切不進去、檔裡寫 on 也當沒有)
(1a「叫醒沒人接 ⇒ 自動起背景整理」整段移到下一張(#466 Codex r2 拆張);這一張不起任何新的自動路徑。)
H1 忙滿 30 分那條(0.1.62 既有)也照開關:draft ⇒ spawn 帶 draft;off ⇒ 不帶(舊行為)。
   子程序套用前重讀開關:途中切成 draft ⇒ 只擬不結;以 draft 起、途中切 off ⇒ 這輪不套用(mode-off);讀不到 ⇒ 當 draft
D1 只擬不結:apply(dry) 不改任何信,驗得過的標 would
D2 席位 15 分內自己讀過的信不餵(T14)
G1 守門放行:長工作(租約 pid_start + aib now 忙碌且主項目是長工作;pid 重用 / 缺 pid_start / 一般工作反例)/
   真人親打 10 分內(抬頭交 dm_header 判;別席 dm 引用艦長室字樣不算、艦長室網頁 / 真人抬頭算)/ 背景整理在跑;
   對話檔讀不到 = 判斷不了
G3 同批限擋:含 rev;子集合 + 擋下後本席真的處理過才放;新信 / 新 rev / 自然過期 / 過 30 分照擋
   「本席處理過」看 audit.jsonl:actor 正好 seat:<本席>、成功、轉到 read/done/archived、擋下之後、同一版
   (背景整理 agent / system / human / 別席 / 舊版 / 擋下前 / 失敗的都不算;#466 Codex r2 M2)
G2 判斷不了 ⇒ 放行留痕 FAILOPEN;triage_auto=off ⇒ 新放行條件全部不看
G4 讀錯不吞(#466 Codex r2 M4):整理鎖 / 同批紀錄 / audit / 租約 / aib now 檔在卻讀不到或壞檔 ⇒ Undetermined
   ⇒ check 放行留 FAILOPEN;檔不存在 = 正常(照常判)
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


class H1Mode(unittest.TestCase):
    def test_busy_path_spawn_follows_switch(self):
        out = HN.QBox(self).py(SETUP + (
            "H.should_start = lambda *a, **k: (True, 'headless')\n"
            "p = Q.triage_path(%r, env); p.parent.mkdir(parents=True, exist_ok=True)\n"
            "Q._triage_headless_maybe(%r, {}, p, env, t, {'provider': 'claude'})\n"
            "Q.triage_auto_switch('off', env=env)\n"
            "Q._triage_headless_maybe(%r, {}, p, env, t + 1, {'provider': 'claude'})\n"
            "print(json.dumps(spawned))\n") % (SEAT, SEAT, SEAT))
        self.assertEqual([x[2] for x in out], [True, False], "預設 draft ⇒ 帶 draft;off ⇒ 舊行為:%r" % out)

    def test_run_rereads_switch_before_apply(self):
        out = HN.QBox(self).py(SETUP + (
            "M = 'https://github.com/ez2app/ez2-aib/pull/1'\n"
            "def ask_switch(to):\n"
            "    def ask(brand, prompt):\n"
            "        Q.triage_auto_switch(to, env=env)\n"
            "        ids = [i for i in __import__('re').findall(r'q_[0-9a-f]{16}', prompt)]\n"
            "        return json.dumps([{'id': i, 'action': 'close', 'evidence': M} for i in dict.fromkeys(ids)]), 'ok'\n"
            "    return ask\n"
            "st = lambda i: (Q._load_item(%r, i, env) or {}).get('status')\n"
            "Q.triage_auto_switch('off', env=env)\n"
            "a = put('PR 合了 ' + M, t)\n"
            "r1 = H.run(%r, 'claude', env=env, now=t + 100, ask=ask_switch('draft'), gh=lambda u: True)\n"
            "s1 = st(a)\n"
            "r2 = H.run(%r, 'claude', env=env, now=t + 200, ask=ask_switch('off'), gh=lambda u: True, dry=True)\n"
            "s2 = st(a)\n"
            "r3 = H.run(%r, 'claude', env=env, now=t + 300, ask=ask_switch('off'), gh=lambda u: True)\n"
            "print(json.dumps({'r1': [r1.get('dry'), r1.get('reason')], 's1': s1, 'r2': r2.get('reason'), 's2': s2,\n"
            "                  'r3': [r3.get('dry'), r3.get('reason')], 's3': st(a)}))\n") % (SEAT, SEAT, SEAT, SEAT))
        self.assertEqual(out["r1"], [True, "ok"], "途中切 draft ⇒ 只擬不結:%r" % out)
        self.assertEqual(out["s1"], "delivered", out)
        self.assertEqual(out["r2"], "mode-off", "以 draft 起、途中切 off ⇒ 不套用:%r" % out)
        self.assertEqual(out["s2"], "delivered", out)
        self.assertEqual(out["r3"], [False, "ok"], out)
        self.assertEqual(out["s3"], "done", "off 起、off 套 = 舊行為:%r" % out)

    def test_mode_unreadable_is_draft(self):
        with mock.patch.object(Q, "triage_auto_mode", side_effect=OSError("x")):
            self.assertEqual(H._mode("aaaaa", None), "draft")


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
        self.assertEqual(m.call_args[0][1:3], (["q_a@1"], NOW))
        self.assertIsNone(self.rw([H3[1], dict(H3[2], rev=3)], NOW + 60, True)[0], "剩下的有新 rev ⇒ 照擋")
        self.assertIsNone(self.rw(H3[1:] + [{"id": "q_d", "rev": 1}], NOW + 60, True)[0], "有新信 ⇒ 照擋")
        self.assertIsNone(self.rw(H3[1:], NOW + G.SAME_BATCH_S + 1, True)[0], "過了 30 分 ⇒ 照擋")

    def test_handled_since_reads_audit(self):
        d = Q.seat_dir("aaaaa", self.env)
        d.mkdir(parents=True, exist_ok=True)
        rows = [
            {"ts": NOW + 10, "id": "q_a", "to": "read", "actor": "seat:aaaaa", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_b", "to": "read", "actor": "agent:aaaaa/headless-1", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_c", "to": "read", "actor": "system", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_d", "to": "done", "actor": "human:hub", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_e", "to": "read", "ok": True, "rev": 1},
            {"ts": NOW - 10, "id": "q_f", "to": "read", "actor": "seat:aaaaa", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_g", "to": "read", "actor": "seat:aaaaa", "ok": False, "rev": 1},
            {"ts": NOW + 10, "id": "q_h", "to": "read", "actor": "seat:aaaaa", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_i", "to": "done", "actor": "seat:aaaaa", "ok": True, "rev": 1},
            {"ts": NOW + 10, "id": "q_j", "to": "read", "actor": "seat:bbbbb", "ok": True, "rev": 1},
        ]
        (d / Q.AUDIT_FILE).write_text("半行壞掉的\n" + "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        hs = lambda *pairs: G._handled_since("aaaaa", list(pairs), NOW, self.env)  # noqa: E731
        self.assertTrue(hs("q_a@1"))
        for p in ("q_b@1", "q_c@1", "q_d@1", "q_e@1", "q_f@1", "q_g@1", "q_j@1", "q_x@1"):
            self.assertFalse(hs(p), p)
        self.assertFalse(hs("q_h@2"), "擋下時是 rev 2,本席讀的是 rev 1 ⇒ 不算")
        self.assertTrue(hs("q_i@1"))
        self.assertTrue(hs("q_b@1", "q_a@1"), "任一封本席處理過就算")
        (d / Q.AUDIT_FILE).unlink()
        self.assertFalse(hs("q_a@1"), "沒有 audit = 沒處理過")


class G4ReadErrors(Tmp):
    def d(self):
        d = Q.seat_dir("aaaaa", self.env)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rw(self, **kw):
        kw.setdefault("long_work", False)
        return G.release_why("aaaaa", HITS3, NOW, env=self.env, **kw)

    def test_lock_and_state_unreadable(self):
        for name in (Q.TRIAGE_AGENT_LOCK_FILE, G.GATE_STATE_FILE):
            with self.subTest(name=name):
                p = self.d() / name
                p.mkdir()                                     # 檔位上是目錄 ⇒ 讀取拋 IsADirectoryError
                with self.assertRaises(G.Undetermined):
                    self.rw()
                p.rmdir()
                p.write_text("{壞", encoding="utf-8")
                with self.assertRaises(G.Undetermined):
                    self.rw()
                p.unlink()
        self.assertIsNone(self.rw(), "都不存在 = 正常照判")

    def test_lock_permission_error(self):
        p = self.d() / Q.TRIAGE_AGENT_LOCK_FILE
        p.write_text("{}", encoding="utf-8")
        real = Path.read_text

        def boom(self_, *a, **k):
            if str(self_) == str(p):
                raise PermissionError(13, "denied")
            return real(self_, *a, **k)
        with mock.patch.object(Path, "read_text", boom):
            with self.assertRaises(G.Undetermined):
                self.rw()

    def test_audit_unreadable(self):
        G._remember_block("aaaaa", H3, NOW, self.env)
        (self.d() / Q.AUDIT_FILE).mkdir()
        with self.assertRaises(G.Undetermined):
            G.release_why("aaaaa", H3[1:], NOW + 60, env=self.env, long_work=False)

    def lease(self, body):
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(body, encoding="utf-8")

    def test_long_work_unreadable(self):
        good = json.dumps({"owner": "@aaaaa", "pid": os.getpid(), "pid_start": LEASE_START, "expiry_epoch": NOW + 600})
        rec = {"item": "推送 #72", "state": "busy"}
        lw = lambda **k: Q._seat_long_work("aaaaa", self.env, NOW, **k)  # noqa: E731
        self.assertFalse(lw(proc_start=lambda p: LEASE_START, now_rec=rec), "沒有租約檔 = 沒長工作")
        self.lease("{壞")
        with self.assertRaises(Q.LongWorkUndetermined):
            lw(proc_start=lambda p: LEASE_START, now_rec=rec)
        self.lease("[]")
        with self.assertRaises(Q.LongWorkUndetermined):
            lw(proc_start=lambda p: LEASE_START, now_rec=rec)
        self.lease(good)
        with self.assertRaises(Q.LongWorkUndetermined):
            lw(proc_start=lambda p: None, now_rec=rec)                     # ps 量不到
        import seat_now
        nd = Path(seat_now.now_dir(self.env["EZ2_AIB_STATE_ROOT"]))
        nd.mkdir(parents=True, exist_ok=True)
        self.assertFalse(lw(proc_start=lambda p: LEASE_START), "沒跑過 aib now = 不知道在長工作")
        (nd / "aaaaa.json").write_text("{壞", encoding="utf-8")
        with self.assertRaises(Q.LongWorkUndetermined):
            lw(proc_start=lambda p: LEASE_START)
        (nd / "aaaaa.json").write_text(json.dumps(rec), encoding="utf-8")
        self.assertTrue(lw(proc_start=lambda p: LEASE_START))

    def test_check_fails_open_on_undetermined(self):
        rows = [{"id": "q_a", "status": "delivered", "sender_seat": "zzzzz", "ts": NOW - 600, "rev": 1}]
        (self.d() / G.GATE_STATE_FILE).write_text("{壞", encoding="utf-8")
        notes = []
        payload = {"errors": [], "tabs": {"waiting": {"rows": rows}, "read": {"rows": []}}}
        with mock.patch.object(Q, "tabs_payload", return_value=payload), \
                mock.patch.object(Q, "_seat_long_work", return_value=False):
            blk, _m = G.check("aaaaa", lambda key, **kw: key, notes.append, now=NOW, env=self.env)
        self.assertFalse(blk)
        self.assertTrue(any("FAILOPEN" in n and "json:" in n for n in notes), notes)


HITS3 = [{"id": "q_a", "rev": 1}, {"id": "q_b", "rev": 1}]


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
