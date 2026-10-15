#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""dm-ctl.sh 再入深度上限 + 測試子行程看門狗(10-15 18:4x 事故後兩道防線;@abb89 19:02 事故通報要求)。

事故:刀測拿掉一層防護,dm-ctl「推導寄件人」那一輪無限再入,兩個沙盒各 fork 上千支,整台 Pro 行程表滿約 8 分鐘。

D1 深度已到上限(AIB_DM_DEPTH=8 ⇒ 這一層是 9)⇒ rc 2、說原因、RESULT reason=depth、一個字都沒貼。
D2 上限內(7 ⇒ 8)照常投遞。
D3 壞值(空、x、-5)當 0 照常投遞;超長數字(會溢位)直接當超過上限 ⇒ rc 2。
D4 AIB_DM_DEPTH_MAX 可調低(測試與除錯用),壞值退回 8。
G1 看門狗:子行程那一組超過上限 ⇒ 整組 SIGKILL、tripped=procs;組長先走、留下的孤兒也清掉。
K1 刀測(經看門狗跑):拿掉深度判斷 ⇒ D1 變紅。
"""
import importlib.util
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import group_size, run_guarded  # noqa: E402

RELI = "test-dm-reliability-abb89-1015.py"
_spec = importlib.util.spec_from_file_location("dm_reliability_box", str(HERE / RELI))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def setUpModule():
    R.setUpModule()


def tearDownModule():
    R.tearDownModule()


class Depth(unittest.TestCase):
    def run_at(self, depth, **env):
        b = R.Box(self)
        p = b.run(["@abcde", "深度測試"], AIB_DM_DEPTH=depth, timeout=60, **env)
        return b, p

    def test_d1_over_limit_stops(self):
        b, p = self.run_at("8")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("再入深度 9 超過上限 8", p.stderr)
        self.assertEqual(R.last_line(p), "RESULT stage=abort id=- relay=- thread=- reason=depth")
        self.assertEqual(b.exec_calls(), [])

    def test_d2_within_limit_delivers(self):
        b, p = self.run_at("7")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(b.exec_calls()), 1)

    def test_d3_bad_values(self):
        for v in ("", "x", "-5"):
            b, p = self.run_at(v)
            self.assertEqual(p.returncode, 0, "%r %s" % (v, p.stdout + p.stderr))
        b, p = self.run_at("9" * 25)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(b.exec_calls(), [])

    def test_d4_max_override(self):
        b, p = self.run_at("2", AIB_DM_DEPTH_MAX="2")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("超過上限 2", p.stderr)
        b, p = self.run_at("7", AIB_DM_DEPTH_MAX="abc")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class Guard(unittest.TestCase):
    def test_g1_runaway_group_killed(self):
        # 一直往下開 sleep 的小炸彈(有上限 400 支,萬一看門狗沒動手也不會拖垮機器)
        bomb = "for i in $(seq 1 400); do sleep 30 & sleep 0.01; done; wait"
        t0 = time.time()
        r = run_guarded(["bash", "-c", bomb], max_procs=25, timeout=60, poll=0.05)
        self.assertEqual(r.tripped, "procs")
        self.assertGreater(r.peak, 25)
        self.assertLess(time.time() - t0, 30)
        time.sleep(0.3)
        self.assertEqual(group_size(r.pgid), 0)                          # 那一組一支不剩

    def test_g2_orphans_cleaned(self):
        # 組長馬上結束、留一支孤兒在同一組 ⇒ 也要清掉
        t0 = time.time()
        r = run_guarded(["bash", "-c", "sleep 30 >/dev/null 2>&1 & echo started"], max_procs=50, timeout=30)
        self.assertLess(time.time() - t0, 10)          # 不是等孤兒自己睡完
        self.assertIsNone(r.tripped)
        self.assertIn("started", r.stdout)
        time.sleep(0.3)
        self.assertEqual(group_size(r.pgid), 0)


class Knife(unittest.TestCase):
    def test_k1_no_depth_check(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm-ctl.sh", 'if [ "$AIB_DM_DEPTH" -gt "${AIB_DM_DEPTH_MAX:-8}" ]; then',
                        "if false; then", tests=(name, RELI)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Depth.test_d1_over_limit_stops"],
                            cwd=str(root), max_procs=150, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
