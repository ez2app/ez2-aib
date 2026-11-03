#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""風暴偵測全機一輪只跑一支 log show(11-03 andymini 看到 5 支以上同跑,每支約 28% CPU)。全在 tmp 沙盒,不跑真的 log show。

C1 五艙同一刻 ⇒ 只跑一次,其他回 shared;C2 過了間隔換誰都能跑;C3 差 2 秒內不算剛跑過;
C4 別席跑的時候(鎖被占)不疊第二支,也不寫記號;C5 五條執行緒同時搶,只跑一次;
C6 跑到一半炸掉,鎖要放掉、別席馬上能重試;C7 記號檔在未來(時鐘跳過)不算剛跑過;
C8 鎖開不了照跑(不能丟偵測);C9 Guard 的風暴那條真的走這道;C10 量不到不算跑過。
刀(複本):K1 不看記號;K2 不上鎖;K3 開不了就不跑;K4 Guard 直接呼叫 storm_once;K5 量不到也記跑過。每刀要指定測試 FAIL 才算,匯入失敗不算。
"""
import fcntl
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = pathlib.Path(os.environ.get("CABIN_GUARD_TEST_SRC") or ROOT / "src" / "cockpit")
_TMP = tempfile.mkdtemp(prefix="cabin-log-once-")
os.environ["EZ2_AIB_STATE_ROOT"] = _TMP
sys.path.insert(0, str(SRC))
sys.dont_write_bytecode = True
import cabin_guard as cg  # noqa: E402


class Once(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=_TMP)
        self.path = os.path.join(self.d, "state", "probe.stamp")
        self.ran = 0

    def run_probe(self):
        self.ran += 1
        return "quiet", 0

    def tick(self, now):
        return cg.storm_tick(self.run_probe, path=self.path, now=now)

    def test_c1_five_cabins_one_probe(self):
        got = [self.tick(1000 + i * 0.1) for i in range(5)]
        self.assertEqual(self.ran, 1)
        self.assertEqual(got[0], ("quiet", 0))
        self.assertEqual(got[1:], [("shared", 0)] * 4)

    def test_c2_next_window_runs_again(self):
        self.tick(1000)
        self.tick(1000 + cg.STORM_CHECK_S)
        self.assertEqual(self.ran, 2)

    def test_c3_slack_lets_next_seat_take_over(self):
        self.tick(1000)
        self.assertEqual(self.tick(1000 + cg.STORM_CHECK_S - cg.STORM_PROBE_SLACK_S - 1), ("shared", 0))
        self.assertEqual(self.ran, 1)
        self.tick(1000 + cg.STORM_CHECK_S - cg.STORM_PROBE_SLACK_S)
        self.assertEqual(self.ran, 2)

    def test_c4_running_elsewhere_no_second_probe(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path + ".lock", "a") as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            self.assertEqual(self.tick(1000), ("shared", 0))
        self.assertEqual(self.ran, 0)
        self.assertFalse(os.path.exists(self.path), "沒跑就不准寫記號,不然下一席會被擋")
        self.tick(1001)
        self.assertEqual(self.ran, 1)

    def test_c5_threads_race(self):
        gate = threading.Barrier(5)
        count = []

        def slow():
            count.append(1)
            time.sleep(0.2)
            return "quiet", 0

        def worker():
            gate.wait()
            cg.storm_tick(slow, path=self.path, now=1000)

        ts = [threading.Thread(target=worker) for _ in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(count), 1)

    def test_c6_crash_releases_lock(self):
        def boom():
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            cg.storm_tick(boom, path=self.path, now=1000)
        self.tick(1001)
        self.assertEqual(self.ran, 1, "炸掉不算跑過,別席下一拍就要能重試")

    def test_c10_unmeasurable_lets_next_seat_retry(self):
        cg.storm_tick(lambda: ("unmeasurable", 0), path=self.path, now=1000)
        self.tick(1001)
        self.assertEqual(self.ran, 1)

    def test_c7_future_stamp_not_fresh(self):
        os.makedirs(os.path.dirname(self.path))
        pathlib.Path(self.path).write_text("99999", encoding="utf-8")
        self.tick(1000)
        self.assertEqual(self.ran, 1)

    def test_c8_cannot_open_lock_still_probes(self):
        blocker = os.path.join(self.d, "file")
        pathlib.Path(blocker).write_text("x", encoding="utf-8")
        self.assertEqual(cg.storm_tick(self.run_probe, path=os.path.join(blocker, "probe.stamp"), now=1000), ("quiet", 0))
        self.assertEqual(self.ran, 1)

    def test_c9_guard_goes_through_slot(self):
        calls = []
        g = cg.Guard("s", "c", os.path.join(self.d, "g.log"), notify=lambda *a, **k: None)
        with mock.patch.object(cg, "STORM_PROBE_STAMP", self.path), \
                mock.patch.object(cg, "storm_once", lambda **k: calls.append(k) or ("quiet", 0)):
            self.assertEqual(g._storm(), ("quiet", 0))
            self.assertEqual(g._storm(), ("shared", 0))
        self.assertEqual(len(calls), 1)


KNIVES = {
    "K1": ('if _stamp_fresh(path, window_s - STORM_PROBE_SLACK_S + 60, now):', 'if False:', "test_c1_five_cabins_one_probe"),
    "K2": ('fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)', 'pass', "test_c4_running_elsewhere_no_second_probe"),
    "K3": ('    except OSError:\n        return run()\n    try:\n        try:\n', '    except OSError:\n        return "shared", 0\n    try:\n        try:\n', "test_c8_cannot_open_lock_still_probes"),
    "K5": ('if out[0] != "unmeasurable":', 'if True:', "test_c10_unmeasurable_lets_next_seat_retry"),
    "K4": ('self._storm = lambda: storm_tick(lambda: storm_once(', 'self._storm = lambda: (lambda f: f())(lambda: storm_once(', "test_c9_guard_goes_through_slot"),
}


class Knives(unittest.TestCase):
    def test_knives_bite(self):
        if os.environ.get("CABIN_GUARD_TEST_SRC"):
            self.skipTest("刀的複本裡不再跑刀")
        src = (ROOT / "src" / "cockpit" / "cabin_guard.py").read_text(encoding="utf-8")
        for name, (a, b, want) in KNIVES.items():
            with self.subTest(name):
                self.assertEqual(src.count(a), 1, "錨點要剛好一處")
                d = tempfile.mkdtemp(dir=_TMP)
                pathlib.Path(d, "cabin_guard.py").write_text(src.replace(a, b), encoding="utf-8")
                p = subprocess.run([sys.executable, "-B", __file__, "Once." + want], capture_output=True, text=True, timeout=120,
                                   env=dict(os.environ, CABIN_GUARD_TEST_SRC=d, PYTHONPATH=str(SRC)))
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("FAIL: " + want, p.stderr, "要指定測試的斷言紅,匯入失敗不算")


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
