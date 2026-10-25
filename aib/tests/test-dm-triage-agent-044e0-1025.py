#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理子代理:triage-plan(Andy 10-25 21:2x / 21:4x 裁示,@abb89 派工,排 0.1.61,@044e0)。
全在暫存狀態根 + 暫存 HOME 跑(行程內直接叫 dm_queue / dm_triage_agent);不送真 dm、不碰 ~/.ez2-aib。

T1 門檻計數:待審 / 進行中各自算;hold_human 與已寫「等什麼」的不算;任一 ≥2 ⇒ over_threshold
T2 建議:通知 ⇒ close_notice;同寄件鍵同 PR 有較新一輪(新一輪還在排隊、沒送達 ⇒ 還沒被自動結)⇒ close_superseded;
   hold_human 一律 human(就算是舊一輪);正文帶 PR 網址 ⇒ human + evidence_hint;分組照 PR 號 / thread / 寄件人
T3 唯讀:plan 前後每封信的狀態與 rev 都沒變;CLI triage-plan 印同一份 JSON
T4 整理子代理身分 agent:<席>/<id>:能讀、能結自己那一席;別席的信、hold_human 放行、刪、回復都不准;格式不對 = actor_missing
T5 consolidate:每封 done、evidence 指整理檔、記 consolidated_into、hold 跳過;整理檔不存在 ⇒ 全拒;
   reopen:done → read、清掉 done_*;沒理由 / 子代理 / 別席 ⇒ 拒;CLI consolidate / reopen
T6 triage-lock:登記後別的子代理 held、本人可續 / 放掉、60 分鐘過期就讓;壞檔當沒登記;CLI rc 0 / 3
刀(複本 + run_guarded):K1 hold_human 也算進門檻 ⇒ T1 紅;K2 寫了等什麼仍算 ⇒ T1 紅;
K3 hold_human 也給 close_superseded ⇒ T2 紅;
K4 拿掉子代理的收件席前綴檢查 ⇒ T4 紅;K5 回復不查理由 ⇒ T5 紅;K6 過期不讓 ⇒ T6 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_queue as Q  # noqa: E402
import dm_take as TK  # noqa: E402
import dm_triage_agent as TA  # noqa: E402

SEAT = "aaaaa"
ME = "seat:" + SEAT
NOW = 1792930000


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-dm-triage-agent-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def rec(self, msg, now=NOW, **kw):
        kw.setdefault("sender_seat", "bbbbb")
        kw.setdefault("sender_source", "pane_pid_chain")
        return Q.record(SEAT, msg, "@bbbbb", env=self.env, now=now, **kw)

    def queued(self, msg, now=NOW, **kw):
        return Q.enqueue(SEAT, msg, "@bbbbb", sender_seat="bbbbb", sender_source="pane_pid_chain",
                         env=self.env, now=now, **kw)

    def hold(self, msg, now=NOW, **kw):
        kw.setdefault("sender_seat", "bbbbb")
        kw.setdefault("sender_source", "pane_pid_chain")
        return Q.record(SEAT, msg, "@bbbbb", status=Q.HOLD_STATUS, env=self.env, now=now, **kw)

    def row(self, plan, iid):
        return next(r for r in plan["rows"] if r["id"] == iid)


class Counts(Base):
    def test_t1_threshold_counts(self):
        a = self.rec("@bbbbb:事情 A")
        p = TA.plan(SEAT, env=self.env, now=NOW)
        self.assertEqual((p["counts"]["countable_waiting"], p["over_threshold"]), (1, False))
        self.hold("@bbbbb:陌生來信", now=NOW + 1)
        p = TA.plan(SEAT, env=self.env, now=NOW)
        self.assertEqual((p["counts"]["waiting"], p["counts"]["hold_human"], p["counts"]["countable_waiting"]),
                         (2, 1, 1))
        self.assertFalse(p["over_threshold"])                     # hold_human 不算
        b = self.rec("@bbbbb:事情 B", now=NOW + 2)
        p = TA.plan(SEAT, env=self.env, now=NOW)
        self.assertEqual((p["counts"]["countable_waiting"], p["over_threshold"]), (2, True))
        TK.take(SEAT, a["id"], ME, wait="等 @ccccc 回", env=self.env, now=NOW + 3)
        TK.take(SEAT, b["id"], ME, env=self.env, now=NOW + 3)
        p = TA.plan(SEAT, env=self.env, now=NOW)
        self.assertEqual((p["counts"]["in_progress"], p["counts"]["with_wait"], p["counts"]["countable_in_progress"]),
                         (2, 1, 1))
        self.assertFalse(p["over_threshold"])                     # 寫了等什麼的不算
        self.assertEqual(self.row(p, a["id"]).get("excluded"), "waiting_on")


class Suggest(Base):
    def test_t2_suggestions_and_groups(self):
        old = self.rec("@bbbbb #348 r1 審查:兩個 Medium", now=NOW)
        new = self.queued("@bbbbb #348 r2 審查:都修了", now=NOW + 10)   # 送達才會自動結;還在排隊 ⇒ 計畫建議結
        note = self.rec("收到", now=NOW + 20)
        url = self.rec("@bbbbb 請看 https://github.com/ez2app/ez2-aib/pull/360 頭碼 03c443e", now=NOW + 30)
        held_old = self.hold("@bbbbb #351 r1 審查", now=NOW + 40)
        self.rec("@bbbbb #351 r2 審查", now=NOW + 50)
        thr = self.rec("@bbbbb:另一件", now=NOW + 60, thread="6ddf/x1")
        p = TA.plan(SEAT, env=self.env, now=NOW)
        r = self.row(p, old["id"])
        self.assertEqual((r["suggest"], r.get("superseded_by")), ("close_superseded", new["id"]))
        self.assertEqual(self.row(p, new["id"])["suggest"], "human")
        self.assertEqual(self.row(p, note["id"])["suggest"], "close_notice")
        r = self.row(p, url["id"])
        self.assertEqual((r["suggest"], r.get("evidence_hint")),
                         ("human", ["https://github.com/ez2app/ez2-aib/pull/360"]))
        self.assertEqual(self.row(p, held_old["id"])["suggest"], "human")   # hold 不建議動
        groups = {g["key"]: g["ids"] for g in p["groups"]}
        self.assertEqual(groups["pr:#348"], [old["id"], new["id"]])
        self.assertEqual(groups["thread:6ddf/x1"], [thr["id"]])
        self.assertIn("from:@bbbbb", groups)


class ReadOnly(Base):
    def test_t3_read_only_and_cli(self):
        ids = [self.rec("@bbbbb #9 r%d" % n, now=NOW + n)["id"] for n in (1, 2)] + [self.hold("x")["id"]]
        before = {i: (Q._load_item(SEAT, i, self.env)["status"], Q.item_rev(Q._load_item(SEAT, i, self.env)))
                  for i in ids}
        p = TA.plan(SEAT, env=self.env, now=NOW)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["triage-plan", "--seat", SEAT, "--json"], env=self.env)
        self.assertEqual(rc, 0)
        cli = json.loads(out.getvalue())
        self.assertEqual([r["id"] for r in cli["rows"]], [r["id"] for r in p["rows"]])
        after = {i: (Q._load_item(SEAT, i, self.env)["status"], Q.item_rev(Q._load_item(SEAT, i, self.env)))
                 for i in ids}
        self.assertEqual(before, after)


AGENT = "agent:%s/tri-1" % SEAT


class Agent(Base):
    def test_t4_agent_actor_bounds(self):
        a = self.rec("@bbbbb:事情 A")
        self.assertTrue(Q.mark_read(SEAT, a["id"], AGENT, env=self.env, now=NOW + 1)["ok"])
        f = self.d / "proof.md"
        f.write_text("x", encoding="utf-8")
        self.assertTrue(Q.mark_done(SEAT, a["id"], AGENT, str(f), env=self.env, now=NOW + 2)["ok"])
        b = self.rec("@bbbbb:事情 B", now=NOW + 3)
        self.assertEqual(Q.mark_read(SEAT, b["id"], "agent:ccccc/tri-1", env=self.env, now=NOW + 4)["reason"], "actor")
        h = self.hold("@bbbbb:等真人", now=NOW + 5)
        self.assertEqual(Q.mark_read(SEAT, h["id"], AGENT, env=self.env, now=NOW + 6)["reason"], "actor")
        self.assertEqual(Q._transition(SEAT, b["id"], "deleted", AGENT, env=self.env, now=NOW + 7)["reason"], "actor")
        self.assertEqual(Q._transition(SEAT, a["id"], "read", AGENT, env=self.env, now=NOW + 8,
                                       why="reopen:結錯")["reason"], "actor")
        self.assertEqual(Q.mark_read(SEAT, b["id"], "agent:" + SEAT, env=self.env, now=NOW + 9)["reason"], "actor_missing")
        rows = [r for r in Q.audit_rows(SEAT, self.env) if r.get("id") == a["id"] and r.get("ok")]
        self.assertEqual([r.get("actor") for r in rows][-2:], [AGENT, AGENT])


class Consolidate(Base):
    def test_t5_consolidate_and_reopen(self):
        ids = [self.rec("@bbbbb:同類 %d" % n, now=NOW + n)["id"] for n in (1, 2)]
        TK.take(SEAT, ids[1], ME, env=self.env, now=NOW + 3)
        h = self.hold("@bbbbb:等真人", now=NOW + 4)
        f = self.d / "DMTRIAGE-aaaaa-x.md"
        bad = TA.consolidate(SEAT, ids, str(self.d / "nope.md"), AGENT, env=self.env, now=NOW + 5)
        self.assertEqual(bad["reason"], "evidence")
        self.assertEqual(Q._load_item(SEAT, ids[0], self.env)["status"], "delivered")
        f.write_text("整理", encoding="utf-8")
        r = TA.consolidate(SEAT, ids + [h["id"]], str(f), AGENT, env=self.env, now=NOW + 6)
        self.assertFalse(r["ok"])                                  # hold 那封跳過 ⇒ 整體不算全成
        self.assertEqual([(x["id"], x["ok"]) for x in r["results"]], [(ids[0], True), (ids[1], True), (h["id"], False)])
        for i in ids:
            it = Q._load_item(SEAT, i, self.env)
            self.assertEqual((it["status"], it["done_evidence"], it["consolidated_into"]), ("done", str(f), str(f)))
        self.assertEqual(Q._load_item(SEAT, h["id"], self.env)["status"], Q.HOLD_STATUS)
        self.assertEqual(TA.reopen(SEAT, ids[0], ME, "", env=self.env, now=NOW + 7)["reason"], "transition")
        self.assertEqual(TA.reopen(SEAT, ids[0], AGENT, "結錯", env=self.env, now=NOW + 7)["reason"], "actor")
        self.assertEqual(TA.reopen(SEAT, ids[0], "seat:ccccc", "結錯", env=self.env, now=NOW + 7)["reason"], "actor")
        self.assertEqual(Q._transition(SEAT, ids[0], "read", ME, env=self.env, now=NOW + 7, why="reopen:")["reason"],
                         "transition")
        r = TA.reopen(SEAT, ids[0], ME, "其實還沒做完", env=self.env, now=NOW + 8)
        self.assertTrue(r["ok"], r)
        it = Q._load_item(SEAT, ids[0], self.env)
        self.assertEqual((it["status"], it.get("done_at"), it.get("consolidated_into"), it["reopen_why"]),
                         ("read", None, None, "reopen:其實還沒做完"))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["reopen", "--seat", SEAT, "--id", ids[1], "--why", "再看一次"], env=self.env)
        self.assertEqual((rc, Q._load_item(SEAT, ids[1], self.env)["status"]), (0, "read"))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["consolidate", "--seat", SEAT, "--ids", ",".join(ids), "--file", str(f), "--by", AGENT],
                        env=self.env)
        self.assertEqual((rc, json.loads(out.getvalue())["ok"]), (0, True))


class Lock(Base):
    def test_t6_triage_lock(self):
        b = "agent:%s/tri-2" % SEAT
        self.assertTrue(TA.agent_lock(SEAT, AGENT, env=self.env, now=NOW)["ok"])
        r = TA.agent_lock(SEAT, b, env=self.env, now=NOW + 60)
        self.assertEqual((r["ok"], r["reason"], r["held"]["by"]), (False, "held", AGENT))
        self.assertEqual(TA.agent_lock(SEAT, b, env=self.env, now=NOW + 60, release=True)["reason"], "held")
        self.assertTrue(TA.agent_lock(SEAT, AGENT, env=self.env, now=NOW + 120)["ok"])        # 本人續
        self.assertEqual(TA.agent_lock(SEAT, AGENT, env=self.env, now=NOW + 130, release=True)["reason"], "released")
        self.assertIsNone(TA.agent_lock(SEAT, b, env=self.env, now=NOW + 131, status=True)["held"])
        self.assertTrue(TA.agent_lock(SEAT, AGENT, env=self.env, now=NOW + 200)["ok"])
        self.assertTrue(TA.agent_lock(SEAT, b, env=self.env, now=NOW + 200 + TA.AGENT_LOCK_TTL_S)["ok"])  # 過期就讓
        (Q.seat_dir(SEAT, self.env) / TA.AGENT_LOCK_FILE).write_text("{壞", encoding="utf-8")
        self.assertTrue(TA.agent_lock(SEAT, AGENT, env=self.env, now=NOW + 9000)["ok"])
        (Q.seat_dir(SEAT, self.env) / TA.AGENT_LOCK_FILE).unlink()          # CLI 用真時間,從空的開始驗
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(Q.main(["triage-lock", "--seat", SEAT, "--by", AGENT], env=self.env), 0)
            self.assertEqual(Q.main(["triage-lock", "--seat", SEAT, "--by", b], env=self.env), 3)
            self.assertEqual(Q.main(["triage-lock", "--seat", SEAT, "--status"], env=self.env), 0)


KNIVES = [
    ("K1", "src/cockpit/dm_triage_agent.py", "        if st == Q.HOLD_STATUS:\n            counts[\"hold_human\"] += 1\n",
     "        if False:\n            counts[\"hold_human\"] += 1\n", "Counts.test_t1_threshold_counts"),
    ("K2", "src/cockpit/dm_triage_agent.py", "        elif _has_wait(it):\n            counts[\"with_wait\"] += 1\n",
     "        elif False:\n            counts[\"with_wait\"] += 1\n", "Counts.test_t1_threshold_counts"),
    ("K3", "src/cockpit/dm_triage_agent.py", "        if st == Q.HOLD_STATUS:                 # 等真人審的信",
     "        if False:                 # 等真人審的信", "Suggest.test_t2_suggestions_and_groups"),
    ("K4", "src/cockpit/dm_queue.py", "    if cls == \"agent\" and ident.split(\"/\", 1)[0] != seat:\n",
     "    if False:\n", "Agent.test_t4_agent_actor_bounds"),
    ("K5", "src/cockpit/dm_queue.py", "    if frm == \"done\" and to == \"read\" and not (isinstance(why, str) and why.startswith(REOPEN_WHY_PREFIX)\n",
     "    if False and not (isinstance(why, str) and why.startswith(REOPEN_WHY_PREFIX)\n", "Consolidate.test_t5_consolidate_and_reopen"),
    ("K6", "src/cockpit/dm_triage_agent.py", "        live = bool(cur) and now < int(cur.get(\"until\") or 0)\n",
     "        live = bool(cur)\n", "Lock.test_t6_triage_lock"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
