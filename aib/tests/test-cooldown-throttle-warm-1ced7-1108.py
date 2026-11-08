# SPDX-License-Identifier: Apache-2.0
"""11-08 cooldown 過熱門檻重校(Andy 准、f1fae 有條件同意;與 PROPOSAL-12 共用常數)。

熱節流 hot 20 → 40;20–39 = warm:理由照記、cooldown.log 每 10 分鐘最多一列,不升級(不寫 nogo、不擋開席、
不叫艦長);reap(收自動化瀏覽器)仍只看 CPU 晶粒溫度 ≥ 90,不放寬。門檻只在 heat_limits 一處。
行程內呼叫,HOME / 狀態根都指暫存,不碰真 ~/.ez2-aib。
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COCKPIT = ROOT / "aib" / "src" / "cockpit"


def cells(kt, cpu=None, load1=2.0):
    out = [{"key": "kernel_task", "label": "熱節流", "value": kt, "state": "ok", "text": "%s%%" % kt},
           {"key": "load1", "label": "load1", "value": load1, "state": "ok", "text": str(load1)}]
    if cpu is not None:
        out.append({"key": "cpu_temp", "label": "CPU 溫度", "value": cpu, "state": "ok", "text": "%s°C" % cpu})
    return out


class ThrottleWarm(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-cool-warm-"))
        self._env = dict(os.environ)
        os.environ.update({"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "EZ2_AIB_LOCALE": "zh-TW"})
        sys.path.insert(0, str(COCKPIT))
        for m in ("cooldown", "machine_status", "heat_limits", "room_heat"):
            sys.modules.pop(m, None)
        import cooldown
        import heat_limits
        import machine_status
        self.C, self.H, self.MS = cooldown, heat_limits, machine_status

    def tearDown(self):
        sys.path.remove(str(COCKPIT))
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.td, ignore_errors=True)

    def test_boundaries(self):
        want = {19.9: ("ok", False), 20.0: ("ok", True), 39.9: ("ok", True), 40.0: ("hot", False), 55.0: ("hot", False)}
        for kt, (lv, warm) in want.items():
            with self.subTest(kt=kt):
                got, reasons = self.C.level_of(cells(kt))
                self.assertEqual(got, lv)
                self.assertEqual(any("warm" in r for r in reasons), warm, reasons)
                self.assertEqual(self.C.throttle_warm_value(cells(kt)) is not None, warm)

    def test_machine_status_cell_warn_between(self):
        def top(kt):
            out = "PID COMMAND %%CPU\n0    kernel_task  0.0\n0    kernel_task  %.1f\n" % kt
            return lambda argv, timeout=None: (0, out)
        for kt, st in ((10.0, "ok"), (25.0, "warn"), (40.0, "hot")):
            with self.subTest(kt=kt):
                self.assertEqual(self.MS.kernel_task_cpu(runner=top(kt))["state"], st)

    def test_warm_logged_at_most_every_ten_minutes(self):
        flag = str(self.td / "state" / "heavy-work-nogo")
        os.makedirs(os.path.dirname(flag))
        log = self.td / ".ez2-aib" / "logs" / "cooldown.log"
        t0 = 2_000_000_000.0
        self.assertTrue(self.C.log_throttle_warm(cells(30.0), flag, now=t0))
        os.utime(flag + ".warm-logged", (t0, t0))
        self.assertFalse(self.C.log_throttle_warm(cells(30.0), flag, now=t0 + 599))
        self.assertTrue(self.C.log_throttle_warm(cells(30.0), flag, now=t0 + 601))
        self.assertFalse(self.C.log_throttle_warm(cells(45.0), flag, now=t0 + 5000))   # hot 不是 warm
        self.assertFalse(self.C.log_throttle_warm(cells(5.0), flag, now=t0 + 9000))
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual([r["action"] for r in rows], ["throttle_warm", "throttle_warm"])
        self.assertEqual(rows[0]["value"], 30.0)
        self.assertEqual((rows[0]["warm"], rows[0]["hot"]), (20.0, 40.0))

    def test_reap_still_only_cpu_critical(self):
        lv, _ = self.C.thermal_state(cells(55.0, cpu=89.9))
        self.assertNotEqual(lv, self.C.CRIT)
        lv, _ = self.C.thermal_state(cells(5.0, cpu=90.0))
        self.assertEqual(lv, self.C.CRIT)

    def test_single_source(self):
        self.assertEqual(self.MS.THRESHOLDS["kernel_task_hot"], self.H.THROTTLE_HOT_PCT)
        self.assertEqual(self.MS.THRESHOLDS["kernel_task_warm"], self.H.THROTTLE_WARM_PCT)
        self.assertEqual(self.C.CPU_CRIT_C, self.H.CPU_CRIT_C)
        spec = importlib.util.spec_from_file_location("host_lease_warm_t",
                                                      ROOT / "aib" / "tools" / "governance" / "host_lease.py")
        hl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hl)
        self.assertEqual(hl.SHORT_THROTTLE_MAX, self.H.THROTTLE_HOT_PCT)
        self.assertEqual(hl.SHORT_CPU_CRIT_C, self.H.CPU_CRIT_C)
        self.assertEqual(hl.SHORT_LOAD1_MAX, self.H.SHORT_LOAD1_MAX)

    def test_seat_open_blocks_only_at_hot(self):
        import room_heat
        sys.path.insert(0, str(ROOT / "aib" / "tools" / "project"))
        try:
            import seat_bootstrap
        finally:
            sys.path.pop(0)
        for kt, block in ((30.0, False), (45.0, True)):
            with self.subTest(kt=kt):
                m = lambda kt=kt: room_heat.measure(sampler=lambda: (cells(kt), 0.1))
                self.assertEqual(seat_bootstrap.heat_state(measure=m)["block"], block)


if __name__ == "__main__":
    unittest.main()
