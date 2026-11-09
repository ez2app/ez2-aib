#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""背景整理端到端秒數(#552 G4,043dd 11-09):「第 2 封入列 → 整理稿寫出」先量再談上限放寬。
全在暫存狀態根 + 暫存 HOME 跑。

L1 餵 3 封(ts 100 / 160 / 400)、寫出在 460 ⇒ 一列 latency_s = 300(從第 2 早那封算)。
L2 只餵 1 封 ⇒ 不寫。
L3 latency_stats:p50 / p95 / max 照最近秩;--since 濾掉舊列;CLI triage-status --latency 印同一份。
刀(複本 + run_guarded):K1 改從最早那封算 ⇒ L1 紅;K2 p95 拿成 p50 ⇒ L3 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_queue as Q  # noqa: E402
import dm_triage_headless as H  # noqa: E402

SEAT = "aaaaa"


class L(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-dm-lat-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def rows(self, seat=SEAT):
        p = Q.seat_dir(seat, self.env) / H.LATENCY_LOG
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_l1_from_second_enqueued(self):
        r = H.record_latency(SEAT, [{"ts": 400}, {"ts": 100}, {"ts": 160}], 460, self.env)
        self.assertEqual(r["latency_s"], 300)
        self.assertEqual(r["t_second"], 160)
        self.assertEqual([x["latency_s"] for x in self.rows()], [300])

    def test_l2_single_item_not_logged(self):
        self.assertIsNone(H.record_latency(SEAT, [{"ts": 100}], 460, self.env))
        self.assertEqual(self.rows(), [])

    def test_l3_stats_and_cli(self):
        for i, lat in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90, 1000]):
            H.record_latency(SEAT, [{"ts": 0}, {"ts": 1000 + i}], 1000 + i + lat, self.env)
        H.record_latency("bbbbb", [{"ts": 0}, {"ts": 5}], 10, self.env)
        st = H.latency_stats(self.env, seat=SEAT)
        self.assertEqual((st["n"], st["p50"], st["p95"], st["max"]), (10, 50, 1000, 1000))
        allst = H.latency_stats(self.env)
        self.assertEqual(allst["n"], 11)
        self.assertEqual(allst["seats"], {SEAT: 10, "bbbbb": 1})
        self.assertEqual(H.latency_stats(self.env, seat=SEAT, since=1050)["n"], 6)   # at = 1000+i+lat:1054 / 1065 / 1076 / 1087 / 1098 / 2009
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = Q.main(["triage-status", "--latency", "--seat", SEAT])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue().strip().splitlines()[0])
        self.assertEqual(out["p95"], 1000)


KNIVES = [
    ("K1 從最早那封算", "src/cockpit/dm_triage_headless.py",
     '"t_second": ts[1], "latency_s": int(at) - ts[1],',
     '"t_second": ts[1], "latency_s": int(at) - ts[0],',
     "L.test_l1_from_second_enqueued"),
    ("K2 p95 拿成 p50", "src/cockpit/dm_triage_headless.py",
     '"p95": _pct(vals, 95),',
     '"p95": _pct(vals, 50),',
     "L.test_l3_stats_and_cli"),
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
