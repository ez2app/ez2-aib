#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理子代理:triage-plan(Andy 10-25 21:2x / 21:4x 裁示,@abb89 派工,排 0.1.61,@044e0)。
全在暫存狀態根 + 暫存 HOME 跑(行程內直接叫 dm_queue / dm_triage_agent);不送真 dm、不碰 ~/.ez2-aib。

T1 門檻計數:待審 / 進行中各自算;hold_human 與已寫「等什麼」的不算;任一 ≥2 ⇒ over_threshold
T2 建議:通知 ⇒ close_notice;同寄件鍵同 PR 有較新一輪(新一輪還在排隊、沒送達 ⇒ 還沒被自動結)⇒ close_superseded;
   hold_human 一律 human(就算是舊一輪);正文帶 PR 網址 ⇒ human + evidence_hint;分組照 PR 號 / thread / 寄件人
T3 唯讀:plan 前後每封信的狀態與 rev 都沒變;CLI triage-plan 印同一份 JSON
刀(複本 + run_guarded):K1 hold_human 也算進門檻 ⇒ T1 紅;K2 寫了等什麼仍算 ⇒ T1 紅;
K3 hold_human 也給 close_superseded ⇒ T2 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
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


KNIVES = [
    ("K1", "src/cockpit/dm_triage_agent.py", "        if st == Q.HOLD_STATUS:\n            counts[\"hold_human\"] += 1\n",
     "        if False:\n            counts[\"hold_human\"] += 1\n", "Counts.test_t1_threshold_counts"),
    ("K2", "src/cockpit/dm_triage_agent.py", "        elif _has_wait(it):\n            counts[\"with_wait\"] += 1\n",
     "        elif False:\n            counts[\"with_wait\"] += 1\n", "Counts.test_t1_threshold_counts"),
    ("K3", "src/cockpit/dm_triage_agent.py", "        if st == Q.HOLD_STATUS:                 # 等真人審的信",
     "        if False:                 # 等真人審的信", "Suggest.test_t2_suggestions_and_groups"),
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
