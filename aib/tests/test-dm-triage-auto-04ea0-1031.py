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
G5 形狀不吞(#466 Codex r3 M4):transcript / audit 壞行(只容許尾段切到的第一行)、同批紀錄 at / pairs 壞、
   aib now 是 [] / 欄位型別錯 ⇒ Undetermined;check 層都是 FAILOPEN;開關讀錯 ⇒ check FAILOPEN
G6 型別先驗(#466 Codex r4):整理鎖 until 不是數字 / by 不是字串;租約 owner / host / pid_start 型別錯、pid / expiry_epoch
   不是整數;aib now 的 state / item 明寫 null 或型別錯;開關檔不是 UTF-8 ⇒ 都是判斷不了(各一刀 K28–K31;r5 補 by=null、host=null 反例)
H2 開關讀錯整條路徑(#466 Codex r3 H):席位檔壞 + 全艦 off ⇒ 忙滿 30 分那條帶 draft、子程序不結案
H3 off ⇒ 回 0.1.62 餵信(不套「15 分內讀過不餵」);draft 照跳過(#466 Codex r3 M)
Knife(本檔最後):每個守衛一刀,落在樹的複本上(knife_copy)、看門狗跑(run_guarded),只跑目標那一格,
   要「那一格 FAIL」才算咬到(炸成 ERROR 不算)。單跑刀:python3 -B tests/<本檔> Knife
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
        Q.triage_auto_switch("off", env=self.env)
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("draft", "seat:unreadable:mode"),
                         "on 不收:當 draft(不是退到全艦 off)")

    def test_unreadable_seat_is_draft_not_fleet(self):
        """#466 Codex r3 H:席位檔讀錯 / 壞檔不能退到全艦 off(那樣會直接結案)。"""
        Q.triage_auto_switch("off", env=self.env)
        p = Q.triage_auto_seat_path("aaaaa", self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.mkdir()                                            # 讀取拋 IsADirectoryError(OSError)
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("draft", "seat:unreadable:read:IsADirectoryError"))
        p.rmdir()
        for body in ("{壞", "[]", json.dumps({"schema": "x", "mode": "off"})):
            p.write_text(body, encoding="utf-8")
            mode, src = Q.triage_auto_mode("aaaaa", self.env)
            self.assertEqual(mode, "draft", body)
            self.assertTrue(Q.triage_auto_unreadable(src), (body, src))
        p.unlink()
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("off", "fleet"), "不存在才退到全艦")
        fp = Q.triage_auto_fleet_path(self.env)
        fp.write_text("{壞", encoding="utf-8")
        self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("draft", "fleet:unreadable:json"))

    def test_permission_error_inner(self):
        """錯誤注入在讀檔那層(Path.read_text),不是外層 mock。"""
        Q.triage_auto_switch("off", env=self.env)
        p = Q.triage_auto_seat_path("aaaaa", self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": Q.TRIAGE_AUTO_SCHEMA, "mode": "off"}), encoding="utf-8")
        real = Path.read_text

        def boom(self_, *a, **k):
            if str(self_) == str(p):
                raise PermissionError(13, "denied")
            return real(self_, *a, **k)
        with mock.patch.object(Path, "read_text", boom):
            self.assertEqual(Q.triage_auto_mode("aaaaa", self.env), ("draft", "seat:unreadable:read:PermissionError"))
            self.assertEqual(H._mode("aaaaa", self.env), "draft")


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
HOST = __import__("socket").gethostname()


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
        d = {"owner": "@aaaaa", "what": "推 @aaaaa 的東西", "host": HOST, "pid": os.getpid(), "pid_start": LEASE_START,
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
        (d / Q.AUDIT_FILE).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
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
        good = json.dumps({"owner": "@aaaaa", "host": HOST, "pid": os.getpid(), "pid_start": LEASE_START, "expiry_epoch": NOW + 600})
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


class G5Shapes(Tmp):
    def d(self):
        d = Q.seat_dir("aaaaa", self.env)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_transcript_bad_lines(self):
        p = os.path.join(self.tmp.name, "t.jsonl")
        Path(p).write_text("{bad json\n", encoding="utf-8")
        self.assertEqual(G.last_human_at(p), G.UNDETERMINED, "整檔壞")
        Path(p).write_text("[1,2]\n", encoding="utf-8")
        self.assertEqual(G.last_human_at(p), G.UNDETERMINED, "不是物件")
        _transcript(p, [(NOW - 60, "好")])
        good = Path(p).read_text(encoding="utf-8")
        Path(p).write_text("x" * 50 + "\n" + good, encoding="utf-8")
        self.assertAlmostEqual(G.last_human_at(p), NOW - 60, delta=1, msg="由新往舊找:較新的真人行先找到,舊壞行不影響")
        _transcript(p, [(NOW - 60, "<system-reminder>x</system-reminder>")])
        sysl = Path(p).read_text(encoding="utf-8")
        Path(p).write_text("x" * 50 + "\n" + sysl, encoding="utf-8")
        self.assertEqual(G.last_human_at(p), G.UNDETERMINED, "往回找到沒被切到的壞行(還沒找到真人)⇒ 判斷不了")
        n = len(sysl.encode("utf-8"))
        self.assertIsNone(G.last_human_at(p, size_max=n + 10), "尾段切到的半行可以跳過(沒有真人 = None,不是判斷不了)")
        Path(p).write_text(good + "{壞\n", encoding="utf-8")
        self.assertEqual(G.last_human_at(p), G.UNDETERMINED, "中間 / 最後的壞行")

    def test_audit_bad_lines(self):
        row = json.dumps({"ts": NOW + 10, "id": "q_a", "to": "read", "actor": "seat:aaaaa", "ok": True, "rev": 1})
        ap = self.d() / Q.AUDIT_FILE
        for body in ("{bad\n", "[]\n", "{bad\n" + row + "\n",
                     json.dumps({"ts": "x", "id": "q_a", "to": "read", "actor": "seat:aaaaa", "ok": True, "rev": 1}) + "\n"):
            with self.subTest(body=body[:20]):
                ap.write_text(body, encoding="utf-8")
                with self.assertRaises(G.Undetermined):
                    G._handled_since("aaaaa", ["q_a@1"], NOW, self.env)
        ap.write_text("y" * 40 + "\n" + row + "\n", encoding="utf-8")
        with mock.patch.object(G, "AUDIT_TAIL_BYTES", len(row) + 10):
            self.assertTrue(G._handled_since("aaaaa", ["q_a@1"], NOW, self.env), "尾段切到的半行可以跳過")

    def test_gate_state_fields(self):
        sp = self.d() / G.GATE_STATE_FILE
        for body in ({"at": "broken", "pairs": []}, {"at": NOW, "pairs": "q_a@1"}, {"at": NOW, "pairs": [1]},
                     {"pairs": ["q_a@1"]}):
            with self.subTest(body=body):
                sp.write_text(json.dumps(body), encoding="utf-8")
                with self.assertRaises(G.Undetermined):
                    G.release_why("aaaaa", HITS3, NOW, env=self.env, long_work=False)

    def test_now_shape(self):
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(
            {"owner": "@aaaaa", "host": HOST, "pid": os.getpid(), "pid_start": LEASE_START, "expiry_epoch": NOW + 600}), encoding="utf-8")
        for rec in ([], "推送", {"item": ["推送"], "state": "busy"}, {"item": "推送", "state": 1}):
            with self.subTest(rec=rec):
                with self.assertRaises(Q.LongWorkUndetermined):
                    Q._seat_long_work("aaaaa", self.env, NOW, proc_start=lambda p: LEASE_START, now_rec=rec)

    def check(self, **patches):
        rows = [{"id": "q_a", "status": "delivered", "sender_seat": "zzzzz", "ts": NOW - 600, "rev": 1}]
        notes = []
        payload = {"errors": [], "tabs": {"waiting": {"rows": rows}, "read": {"rows": []}}}
        with mock.patch.object(Q, "tabs_payload", return_value=payload), \
                mock.patch.object(Q, "_seat_long_work", return_value=False):
            blk, _m = G.check("aaaaa", lambda key, **kw: key, notes.append, now=NOW, env=self.env)
        return blk, notes

    def test_check_failopen_each(self):
        sp = self.d() / G.GATE_STATE_FILE
        sp.write_text(json.dumps({"at": "broken", "pairs": []}), encoding="utf-8")
        blk, notes = self.check()
        self.assertFalse(blk)
        self.assertTrue(any("FAILOPEN" in n for n in notes), notes)
        sp.unlink()
        ap = self.d() / Q.AUDIT_FILE
        G._remember_block("aaaaa", [{"id": "q_a", "rev": 1}, {"id": "q_z", "rev": 1}], NOW - 60, self.env)
        ap.write_text("{bad\n", encoding="utf-8")
        blk, notes = self.check()
        self.assertFalse(blk)
        self.assertTrue(any("FAILOPEN" in n and "audit" in n for n in notes), notes)

    def test_check_switch_unreadable_failopen(self):
        self.d()
        p = Q.triage_auto_seat_path("aaaaa", self.env)
        p.write_text("{壞", encoding="utf-8")
        blk, notes = self.check()
        self.assertFalse(blk)
        self.assertTrue(any("FAILOPEN" in n and "switch" in n for n in notes), notes)
        p.unlink()
        with mock.patch.object(Q, "triage_auto_mode", side_effect=OSError("x")):
            blk, notes = self.check()
        self.assertFalse(blk, "開關例外也是放行留痕,不退回舊擋法")
        self.assertTrue(any("FAILOPEN" in n for n in notes), notes)
        Q.triage_auto_switch("off", env=self.env)
        blk, notes = self.check()
        self.assertTrue(blk, "off 照原判準擋")


class G6Types(Tmp):
    def d(self):
        d = Q.seat_dir("aaaaa", self.env)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_lock_types(self):
        lp = self.d() / Q.TRIAGE_AGENT_LOCK_FILE
        for lk in ({"by": "agent:aaaaa/headless-1", "until": None}, {"by": "agent:aaaaa/headless-1", "until": str(NOW + 60)},
                   {"by": "agent:aaaaa/sub", "until": None}, {"by": "seat:aaaaa"}, {"by": 5, "until": NOW + 60},
                   {"by": "agent:aaaaa/headless-1", "until": True}, {"by": None, "until": NOW + 60},
                   {"until": NOW + 60}):
            with self.subTest(lk=lk):
                lp.write_text(json.dumps(lk), encoding="utf-8")
                with self.assertRaises(G.Undetermined):
                    G._headless_running("aaaaa", self.env, NOW)
        lp.write_text(json.dumps({"by": "agent:aaaaa/sub", "until": NOW + 60}), encoding="utf-8")
        self.assertFalse(G._headless_running("aaaaa", self.env, NOW))
        lp.write_text(json.dumps({"by": "agent:aaaaa/headless-1", "until": NOW + 60}), encoding="utf-8")
        self.assertTrue(G._headless_running("aaaaa", self.env, NOW))

    def test_lease_types(self):
        good = {"owner": "@aaaaa", "host": HOST, "pid": os.getpid(), "pid_start": LEASE_START, "expiry_epoch": NOW + 600}
        rec = {"item": "推送 #72", "state": "busy"}
        for k, v in (("owner", 17), ("expiry_epoch", None), ("expiry_epoch", "9999999999"), ("pid_start", 17),
                     ("pid", "1"), ("pid", True), ("host", 3), ("host", None)):
            with self.subTest(k=k, v=v):
                Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(dict(good, **{k: v})), encoding="utf-8")
                with self.assertRaises(Q.LongWorkUndetermined):
                    Q._seat_long_work("aaaaa", self.env, NOW, proc_start=lambda p: LEASE_START, now_rec=rec)
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(good), encoding="utf-8")
        self.assertTrue(Q._seat_long_work("aaaaa", self.env, NOW, proc_start=lambda p: LEASE_START, now_rec=rec))

    def test_now_null_fields(self):
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text(json.dumps(
            {"owner": "@aaaaa", "host": HOST, "pid": os.getpid(), "pid_start": LEASE_START, "expiry_epoch": NOW + 600}), encoding="utf-8")
        lw = lambda rec: Q._seat_long_work("aaaaa", self.env, NOW, proc_start=lambda p: LEASE_START, now_rec=rec)  # noqa: E731
        for rec in ({"item": "推送", "state": None}, {"item": None, "state": "busy"}):
            with self.subTest(rec=rec):
                with self.assertRaises(Q.LongWorkUndetermined):
                    lw(rec)
        self.assertTrue(lw({"item": "推送"}), "沒有 state 欄 = 舊紀錄,當忙")
        self.assertFalse(lw({"idle": True, "at": NOW}), "閒置紀錄沒有 item / state 欄,正常")

    def test_switch_not_utf8(self):
        p = Q.triage_auto_seat_path("aaaaa", self.env)
        self.d()
        p.write_bytes(b"\xff\xfe{")
        try:
            got = Q.triage_auto_mode("aaaaa", self.env)
        except UnicodeDecodeError as exc:
            self.fail("UnicodeDecodeError 逸出 triage_auto_mode:%r" % exc)
        self.assertEqual(got, ("draft", "seat:unreadable:read:UnicodeDecodeError"))


class H2SwitchUnreadable(unittest.TestCase):
    def test_busy_path_and_run(self):
        out = HN.QBox(self).py(SETUP + (
            "Q.triage_auto_switch('off', env=env)\n"
            "sp = Q.triage_auto_seat_path(%r, env); sp.parent.mkdir(parents=True, exist_ok=True); sp.write_text('{壞')\n"
            "H.should_start = lambda *a, **k: (True, 'headless')\n"
            "p = Q.triage_path(%r, env)\n"
            "Q._triage_headless_maybe(%r, {}, p, env, t, {'provider': 'claude'})\n"
            "M = 'https://github.com/ez2app/ez2-aib/pull/1'\n"
            "a = put('PR 合了 ' + M, t)\n"
            "def ask(brand, prompt):\n"
            "    return json.dumps([{'id': a, 'action': 'close', 'evidence': M}]), 'ok'\n"
            "r = H.run(%r, 'claude', env=env, now=t + 100, ask=ask, gh=lambda u: True)\n"
            "print(json.dumps({'sp': spawned, 'dry': r.get('dry'), 'st': (Q._load_item(%r, a, env) or {}).get('status')}))\n"
        ) % (SEAT, SEAT, SEAT, SEAT, SEAT))
        self.assertEqual([x[2] for x in out["sp"]], [True], out)
        self.assertEqual(out["dry"], True, out)
        self.assertEqual(out["st"], "delivered", "席位開關壞 + 全艦 off ⇒ 不結案:%r" % out)


class H3OffFeed(unittest.TestCase):
    def test_off_feeds_recent_read(self):
        out = HN.QBox(self).py(SETUP + (
            "M = 'https://github.com/ez2app/ez2-aib/pull/1'\n"
            "a = put('PR 合了 ' + M, t)\n"
            "Q.mark_read(%r, a, 'seat:%s', env=env, now=t + 70)\n"
            "asked = []\n"
            "def ask(brand, prompt):\n"
            "    asked.append(1); return json.dumps([{'id': a, 'action': 'leave'}]), 'ok'\n"
            "r1 = H.run(%r, 'claude', env=env, now=t + 100, ask=ask, gh=lambda u: True)\n"
            "Q.triage_auto_switch('off', env=env)\n"
            "r2 = H.run(%r, 'claude', env=env, now=t + 101, ask=ask, gh=lambda u: True)\n"
            "print(json.dumps({'r1': r1.get('reason'), 'r2': r2.get('reason'), 'asked': len(asked)}))\n"
        ) % (SEAT, SEAT, SEAT, SEAT))
        self.assertEqual(out["r1"], "seat-reading", "draft:本席剛讀過 ⇒ 不餵:%r" % out)
        self.assertEqual(out["r2"], "ok", "off:舊行為照餵:%r" % out)
        self.assertEqual(out["asked"], 1, out)


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


KNIVES = [
    ('K1', 'src/cockpit/dm_queue.py', 'draft=triage_auto_mode(seat, env)[0] == "draft")',
     'draft=False)', 'H1Mode.test_busy_path_spawn_follows_switch'),
    ('K2', 'src/cockpit/dm_triage_headless.py', '        if dry and mode == "off":\n            return {"ok": False, "reason": "mode-off"}\n',
     '', 'H1Mode.test_run_rereads_switch_before_apply'),
    ('K3', 'src/cockpit/dm_triage_headless.py', 'dry = bool(dry) or mode == "draft"',
     'dry = bool(dry)', 'H1Mode.test_run_rereads_switch_before_apply'),
    ('K4', 'src/cockpit/dm_triage_headless.py', '        return "draft"\n\n\ndef run(',
     '        return "off"\n\n\ndef run(', 'H1Mode.test_mode_unreadable_is_draft'),
    ('K5', 'src/cockpit/dm_queue.py', '        return TRIAGE_AUTO_DEFAULT, "%s:%s" % (layer, v)',
     '        return None', 'A1Switch.test_unreadable_seat_is_draft_not_fleet'),
    ('K6', 'src/cockpit/dm_queue.py', '        return "%s:read:%s" % (TRIAGE_AUTO_UNREADABLE, exc.__class__.__name__)',
     '        return None', 'A1Switch.test_permission_error_inner'),
    ('K7', 'src/policy/hooks/dm_pending_gate.py', '    if dm_queue.triage_auto_unreadable(src):',
     '    if False:', 'G5Shapes.test_check_switch_unreadable_failopen'),
    ('K8', 'src/policy/hooks/dm_pending_gate.py', 'r.get("ok") is not True or r.get("actor") != me:',
     'r.get("ok") is not True:', 'G3SameBatch.test_handled_since_reads_audit'),
    ('K9', 'src/policy/hooks/dm_pending_gate.py', ' and str(r.get("rev")) == want[r["id"]]:',
     ':', 'G3SameBatch.test_handled_since_reads_audit'),
    ('K10', 'src/policy/hooks/dm_pending_gate.py', 'if r["ts"] >= int(since) and',
     'if', 'G3SameBatch.test_handled_since_reads_audit'),
    ('K11', 'src/policy/hooks/dm_pending_gate.py', '        raise Undetermined("read:%s:%s" % (path.name, exc.__class__.__name__))',
     '        return None', 'G4ReadErrors.test_lock_permission_error'),
    ('K12', 'src/policy/hooks/dm_pending_gate.py', '        raise Undetermined("json:%s" % path.name)',
     '        return None', 'G4ReadErrors.test_lock_and_state_unreadable'),
    ('K13', 'src/policy/hooks/dm_pending_gate.py', '        raise Undetermined("read:audit:%s" % exc.__class__.__name__)',
     '        return False', 'G4ReadErrors.test_audit_unreadable'),
    ('K14', 'src/policy/hooks/dm_pending_gate.py', '            raise Undetermined("json:audit")',
     '            continue', 'G5Shapes.test_audit_bad_lines'),
    ('K15', 'src/policy/hooks/dm_pending_gate.py', '        raise Undetermined("shape:" + GATE_STATE_FILE)\n    return p, d',
     '        return p, {}\n    return p, d', 'G5Shapes.test_gate_state_fields'),
    ('K16', 'src/policy/hooks/dm_pending_gate.py', '            return UNDETERMINED           # #466 Codex r3 M4',
     '            continue  # ', 'G5Shapes.test_transcript_bad_lines'),
    ('K17', 'src/cockpit/dm_queue.py', '        raise LongWorkUndetermined("lease:%s" % exc.__class__.__name__)',
     '        return False', 'G4ReadErrors.test_long_work_unreadable'),
    ('K18', 'src/cockpit/dm_queue.py', '        raise LongWorkUndetermined("ps")',
     '        return False', 'G4ReadErrors.test_long_work_unreadable'),
    ('K19', 'src/cockpit/dm_queue.py', '            raise LongWorkUndetermined("now:%s" % exc.__class__.__name__)',
     '            return False', 'G4ReadErrors.test_long_work_unreadable'),
    ('K20', 'src/cockpit/dm_queue.py', '        raise LongWorkUndetermined("now:shape")',
     '        return False', 'G5Shapes.test_now_shape'),
    ('K21', 'src/policy/hooks/dm_pending_gate.py', '        if why and why.startswith(UNDETERMINED):',
     '        if False:', 'G4ReadErrors.test_check_fails_open_on_undetermined'),
    ('K22', 'src/cockpit/dm_queue.py', '    if owner != "@" + str(seat):\n        return False',
     '    if False:\n        return False', 'G1Release.test_long_work_reads_structured_lease'),
    ('K23', 'src/policy/hooks/dm_pending_gate.py', 'and cur <= prev and (prev - cur):',
     'and (prev - cur):', 'G3SameBatch.test_rules'),
    ('K24', 'src/cockpit/dm_triage_headless.py', '            if ok and dry:\n                res["would"] = True\n            elif ok:',
     '            if ok:', 'D1Dry.test_dry_changes_nothing'),
    ('K25', 'src/cockpit/dm_triage_headless.py', '        if not dry and _mode(seat, env) == "off":',
     '        if False:', 'H3OffFeed.test_off_feeds_recent_read'),
    ('K26', 'src/policy/hooks/dm_pending_gate.py', 'return h.get("sender_kind") == "human" or code == dm_header.ROOM_CODE',
     'return True', 'G1Release.test_human_turn_by_header'),
    ('K27', 'src/policy/hooks/dm_pending_gate.py', '    if mode != "off":',
     '    if True:', 'G2Check.test_off_ignores_releases'),
    ('K28', 'src/policy/hooks/dm_pending_gate.py', '    if not isinstance(by, str) or isinstance(until, bool) or not isinstance(until, (int, float)):\n        raise Undetermined("shape:" + dm_queue.TRIAGE_AGENT_LOCK_FILE)   # agent_lock 一定寫 by(字串)\n    return "/headless-" in by and int(now) < until\n',
     '    return "/headless-" in str(by or "") and int(now) < int(until or 0)   # 刀:回 r3 寫法\n', 'G6Types.test_lock_types'),
    ('K29', 'src/cockpit/dm_queue.py', '    if (not isinstance(owner, str) or not isinstance(host, str) or not _is_int(pid)\n            or not _is_int(exp) or (pid_start is not None and not isinstance(pid_start, str))):\n        raise LongWorkUndetermined("lease:fields")\n',
     '    owner, exp, pid = str(owner), int(exp or 0), int(pid or 0)   # 刀:回 r3 的寬鬆轉型\n', 'G6Types.test_lease_types'),
    ('K30', 'src/cockpit/dm_queue.py', '    if ("state" in now_rec and not isinstance(st, str)) or ("item" in now_rec and not isinstance(item, str)):',
     '    if (st is not None and not isinstance(st, str)) or (item is not None and not isinstance(item, str)):', 'G6Types.test_now_null_fields'),
    ('K31', 'src/cockpit/dm_queue.py', '    except (OSError, UnicodeDecodeError) as exc:      # #466 Codex r4 L',
     '    except OSError as exc:      # ', 'G6Types.test_switch_not_utf8'),
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
