#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""送達時間不漏記(#552 G2,043dd 11-09):queued → read 跳過 delivered,原本 delivered_at 永遠是空的
(11-09 09:1x 全艙只讀查詢:已讀 / 已結的列約 1/3 沒有)。全在暫存狀態根 + 暫存 HOME 跑。

D1 排隊中直接被收件席讀 ⇒ delivered_at = 讀的時間、delivered_via = read。
D2 已送達再讀 ⇒ delivered_at 不動、沒有 delivered_via。
D3 G2 之前讀過的舊列(已讀、沒 delivered_at)結案 ⇒ delivered_at = read_at。
D4 本人在收件匣看待審信(human)⇒ 不補(那不是送進席位)。
D5 doctor dm_latency:讀時補的送達時間(delivered_via=read)不算送達樣本(#552 r1 L1)。
D6 狀態列 chart_dm:delivered_via=read 的等待從入列(ts)算,不是 0 分(#552 r1 L1)。
刀(複本 + run_guarded):K1 拿掉讀時補寫 ⇒ D1 紅;K2 拿掉結案補寫 ⇒ D3 紅;K3 doctor 不排除 ⇒ D5 紅;
K4 chart_dm 用補的送達時間 ⇒ D6 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
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
sys.path.insert(0, str(ROOT / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_queue as Q  # noqa: E402

SEAT = "aaaaa"
ME = "seat:" + SEAT
NOW = 1792930000


class D(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-dm-dlv-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def rec(self, msg, status):
        if status == "queued":
            return Q.enqueue(SEAT, msg, "agent:offhub", env=self.env, now=NOW)["id"]
        return Q.record(SEAT, msg, "agent:offhub", status=status, env=self.env, now=NOW,
                        rc=(3 if status != "delivered" else None))["id"]

    def item(self, iid):
        return Q._load_item(SEAT, iid, self.env)

    def test_d1_queued_read_fills(self):
        iid = self.rec("排隊中", "queued")
        self.assertIsNone(self.item(iid).get("delivered_at"))
        r = Q._transition(SEAT, iid, "read", ME, env=self.env, now=NOW + 60)
        self.assertTrue(r["ok"], r)
        it = self.item(iid)
        self.assertEqual(it.get("delivered_at"), NOW + 60)
        self.assertEqual(it.get("delivered_via"), "read")

    def test_d2_delivered_read_keeps(self):
        iid = self.rec("已送達", "delivered")
        before = self.item(iid).get("delivered_at")
        self.assertTrue(Q._transition(SEAT, iid, "read", ME, env=self.env, now=NOW + 60)["ok"])
        it = self.item(iid)
        self.assertEqual(it.get("delivered_at"), before)
        self.assertIsNone(it.get("delivered_via"))

    def test_d3_old_read_row_done_fills(self):
        iid = self.rec("舊列", "queued")
        it = self.item(iid)
        it.update(status="read", read_at=NOW + 30, delivered_at=None)
        Q._write_item(SEAT, it, self.env)
        r = Q._transition(SEAT, iid, "done", ME, env=self.env, now=NOW + 90, why="回過了")
        self.assertTrue(r["ok"], r)
        it = self.item(iid)
        self.assertEqual(it.get("delivered_at"), NOW + 30)
        self.assertEqual(it.get("delivered_via"), "read")

    def test_d4_human_reads_hold_no_fill(self):
        iid = self.rec("待審", Q.HOLD_STATUS)
        r = Q._transition(SEAT, iid, "read", "human:andy", env=self.env, now=NOW + 60)
        self.assertTrue(r["ok"], r)
        self.assertIsNone(self.item(iid).get("delivered_at"))

    def test_d5_doctor_skips_via_read(self):
        import aib_doctor
        q = self.rec("排隊中被讀", "queued")
        self.assertTrue(Q._transition(SEAT, q, "read", ME, env=self.env, now=NOW + 600)["ok"])
        dv = self.rec("先送達", "delivered")
        self.assertTrue(Q._transition(SEAT, dv, "read", ME, env=self.env, now=NOW + 600)["ok"])
        self.assertEqual(self.item(q).get("delivered_via"), "read")
        r = aib_doctor.check_dm_latency(self.d / "state", ROOT, env=self.env, now=NOW + 900)
        self.assertIn("樣本=1/2", r["detail"], r)

    def test_d6_chart_dm_wait_from_ts(self):
        import seat_screen
        iid = self.rec("排隊中被讀", "queued")
        self.assertTrue(Q._transition(SEAT, iid, "read", ME, env=self.env, now=NOW + 600)["ok"])
        self.assertEqual(self.item(iid).get("delivered_at"), NOW + 600)
        _n, speed, waits = seat_screen.chart_dm(str(self.d / "state"), SEAT, NOW + 900)
        self.assertEqual(waits, [10.0], (speed, waits))


KNIVES = [
    ("K1 拿掉讀時補寫", "src/cockpit/dm_queue.py",
     'and frm != "done" and cls != "human":',
     'and False:',
     "D.test_d1_queued_read_fills"),
    ("K2 拿掉結案補寫", "src/cockpit/dm_queue.py",
     '            item["delivered_at"] = item["read_at"]',
     '            pass',
     "D.test_d3_old_read_row_done_fills"),
    ("K3 doctor 不排除讀時補的", "src/bootstrap/aib_doctor.py",
     '              and i.get("delivered_via") != "read"]',
     '              ]',
     "D.test_d5_doctor_skips_via_read"),
    ("K4 chart_dm 用補的送達時間", "src/cockpit/seat_screen.py",
     'base = d.get("ts") if d.get("delivered_via") == "read" else (d.get("delivered_at") or d.get("ts"))',
     'base = d.get("delivered_at") or d.get("ts")',
     "D.test_d6_chart_dm_wait_from_ts"),
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
