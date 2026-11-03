#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""cooldown 收行程的權限(11-03 andymini:單一感測器瞬間 ≥ 90°C 就收光正在跑的測試)。全在 tmp 沙盒,kill / 熱壓力都注入。

A1 瞬間最大值 ≥ 90、平均低 ⇒ 面板等級仍是 critical,但不收、no-go 不亮,留 reap_skipped
A2 平均連續 60 秒 ≥ 90 ⇒ 收,紀錄帶原因;A3 只撐 30 秒不收;A4 中間掉下來 / 取樣斷太久重算
A5 熱壓力非 0 ⇒ 當場收,溫度低也一樣(真熱不放掉);A6 熱壓力 0 / 量不到 ⇒ 不因此收
A7 歷史寫不進去 ⇒ 退回瞬間值,照收;A8 明細沒平均 ⇒ 用最大值算持續
A9 平均連續 60 秒 ≥ 85 ⇒ no-go 亮、不收
A10 動手前先落 reap_begin(原因、設定檔夾),收完那行也帶;aib cooldown reaped 看得到
A11 load 再高都拿不到收行程的權限
刀(複本):K1 回到看瞬間值 ⇒ A1 紅;K2 熱壓力不算 ⇒ A5 紅;K3 沒歷史就不收 ⇒ A7 紅;K4 拿掉先記原因 ⇒ A10 紅
"""
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import cooldown as C  # noqa: E402


def cells(mx, avg=None, load=1.0):
    return [{"key": "load1", "value": load, "state": "ok"},
            {"key": "cpu_temp", "value": mx, "state": "ok", "detail": "" if avg is None else "max=%.2f avg=%.2f n=14" % (mx, avg)}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        inst = "t-inst"
        self.state = os.path.join(self.home, ".ez2-aib", "instances", inst, "cockpit")
        os.makedirs(self.state)
        env = {"HOME": self.home, "EZ2_AIB_INSTANCE": inst, "EZ2_COCKPIT_STATE_DIR": self.state, "EZ2_AIB_STATE_ROOT": os.path.join(self.home, ".ez2-aib")}
        p = mock.patch.dict(os.environ, env)
        p.start(); self.addCleanup(p.stop)
        self.pressure = 0
        self.reaps = []
        self.t = 1000000.0
        real_auth = C.heat_authority
        for name, val in (
            ("thermal_pressure", lambda runner=None: self.pressure),
            ("reap", lambda dry_run=False, reason=None, **k: (self.reaps.append(reason), {"scanned": 1, "matched": 1, "killed": 1})[1]),
            ("heat_authority", lambda c, now=None, path=None, pressure=None: real_auth(c, now=self.t, path=path, pressure=pressure)),
            ("plan", lambda *a, **k: ([], [], [])),
            ("write_notice", lambda *a, **k: None),
            ("notify_captain", lambda *a, **k: False),
        ):
            q = mock.patch.object(C, name, val)
            q.start(); self.addCleanup(q.stop)

    def step(self, mx, avg=None, dt=20, load=1.0):
        self.t += dt
        return C.apply(cells(mx, avg, load), dry_run=False)

    def logged(self):
        try:
            with open(C.log_path(), encoding="utf-8") as fh:
                return [json.loads(x) for x in fh]
        except OSError:
            return []

    def nogo(self):
        return os.path.exists(os.path.join(self.state, "heavy-work-nogo"))


class Authority(Base):
    def test_a1_spike_not_reaped(self):
        for _ in range(6):
            r = self.step(91.9, 80.0)
        self.assertEqual(r["level"], C.CRIT, "面板照舊顯示瞬間值")
        self.assertEqual(self.reaps, [])
        self.assertFalse(self.nogo())
        self.assertTrue(any(x["action"] == "reap_skipped" for x in self.logged()))

    def test_a2_sustained_reaped_with_reason(self):
        for _ in range(4):
            self.step(95.0, 91.0)
        self.assertEqual(len(self.reaps), 1)
        self.assertEqual(self.reaps[0]["why"], "sustained")
        self.assertEqual(self.reaps[0]["cpu_avg"], 91.0)
        self.assertTrue(self.nogo())

    def test_a3_short_not_reaped(self):
        for _ in range(2):
            self.step(95.0, 91.0, dt=15)
        self.assertEqual(self.reaps, [])

    def test_a4_broken_streak_restarts(self):
        for a in (91, 91, 70, 91, 91):
            self.step(95.0, float(a))
        self.assertEqual(self.reaps, [], "中間掉下來")
        self.step(95.0, 91.0, dt=SUSTAIN_GAP + 1)
        self.step(95.0, 91.0, dt=SUSTAIN_GAP + 1)
        self.assertEqual(self.reaps, [], "取樣斷太久不算連續")

    def test_a5_pressure_reaps_now(self):
        self.pressure = 2
        self.step(60.0, 55.0)
        self.assertEqual(len(self.reaps), 1)
        self.assertEqual(self.reaps[0]["why"], "pressure")

    def test_a6_pressure_zero_or_none(self):
        for p in (0, None):
            self.pressure = p
            self.step(60.0, 55.0)
        self.assertEqual(self.reaps, [])

    def test_a7_no_history_falls_back_to_instant(self):
        with mock.patch.object(C, "_save_history", lambda *a: False):
            self.step(91.0, 80.0)
        self.assertEqual(len(self.reaps), 1, "真熱不放掉:算不出持續值就看瞬間值")
        self.assertEqual(self.reaps[0]["why"], "no_history")

    def test_a8_no_avg_uses_max(self):
        for _ in range(4):
            self.step(91.0, None)
        self.assertEqual(len(self.reaps), 1)

    def test_a9_nogo_at_85_without_reap(self):
        for _ in range(4):
            r = self.step(88.0, 86.0)
        self.assertEqual(self.reaps, [])
        self.assertTrue(self.nogo())
        self.assertEqual(r["act_level"], C.HOT)

    def test_a11_load_never_reaps(self):
        for _ in range(4):
            r = self.step(60.0, 55.0, load=40.0)
        self.assertEqual(r["level"], C.CRIT)
        self.assertEqual(self.reaps, [])
        self.assertTrue(self.nogo(), "load 高 no-go 照舊")


SUSTAIN_GAP = C.SUSTAIN_GAP_S


class Reason(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"HOME": self.tmp.name})
        p.start(); self.addCleanup(p.stop)

    def test_a10_reason_logged_before_kill(self):
        ps = "11 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless=new --user-data-dir=/tmp/my-prof\n"
        ps += "12 /Applications/Google Chrome Helper --type=renderer --headless --user-data-dir=/tmp/my-prof\n"
        seen = []
        def killer(pid):
            seen.append([json.loads(x)["action"] for x in pathlib.Path(C.log_path()).read_text(encoding="utf-8").splitlines()])
        reason = {"why": "sustained", "reasons": ["CPU avg 91.0°C ≥ 90 for 60s"]}
        C.reap(runner=lambda cmd: ps, killer=killer, reason=reason)
        self.assertEqual(seen[0], ["reap_begin"], "動手前原因已落檔")
        rows = [json.loads(x) for x in pathlib.Path(C.log_path()).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["profiles"], ["/tmp/my-prof"])
        self.assertEqual(rows[1]["reason"], reason)
        got = C.reaped_since(60)
        self.assertEqual(len(got), 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(C.main(["reaped", "--since", "60"]), 0)
        self.assertIn("/tmp/my-prof", buf.getvalue())
        self.assertIn("91.0", buf.getvalue())

    def test_a12_pressure_parsing(self):
        self.assertEqual(C.thermal_pressure(lambda c: "com.apple.system.thermalpressurelevel 2\n"), 2)
        self.assertEqual(C.thermal_pressure(lambda c: "com.apple.system.thermalpressurelevel 0\n"), 0)
        self.assertIsNone(C.thermal_pressure(lambda c: ""))
        self.assertIsNone(C.thermal_pressure(lambda c: "error x"))
        def boom(c):
            raise OSError("no notifyutil")
        self.assertIsNone(C.thermal_pressure(boom))

    def test_a13_i18n(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / (loc + ".json")).read_text(encoding="utf-8"))
            for k in ("aib.cool.cli.reaped_usage", "aib.cool.cli.reaped_none", "aib.cool.cli.reaped_row", "aib.test.cooldown_reaped"):
                self.assertTrue(d.get(k), (loc, k))


class Knives(unittest.TestCase):
    """複本上改一刀,該紅的要紅。"""
    def run_knife(self, old, new, target):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["cp", "-R", str(ROOT / "src"), d], check=True)
            subprocess.run(["cp", "-R", str(ROOT / "i18n"), d], check=True)
            subprocess.run(["cp", "-R", str(ROOT / "tools"), d], check=True)
            os.makedirs(os.path.join(d, "tests"))
            me = os.path.join(d, "tests", os.path.basename(__file__))
            src = pathlib.Path(__file__).read_text(encoding="utf-8")
            pathlib.Path(me).write_text(src, encoding="utf-8")
            f = os.path.join(d, "src", "cockpit", "cooldown.py")
            s = pathlib.Path(f).read_text(encoding="utf-8")
            self.assertEqual(s.count(old), 1, old)
            pathlib.Path(f).write_text(s.replace(old, new), encoding="utf-8")
            r = subprocess.run([sys.executable, "-B", me, target], capture_output=True, text=True, env=dict(os.environ, AIB_NO_KNIVES="1"))
            self.assertNotEqual(r.returncode, 0, target + "\n" + r.stderr[-600:])

    def test_knives(self):
        if os.environ.get("AIB_NO_KNIVES"):
            self.skipTest("刀內不再跑刀")
        self.run_knife('if auth_lv == CRIT:\n        out["reap"]', 'if thermal_lv == CRIT:\n        out["reap"]', "Authority.test_a1_spike_not_reaped")
        self.run_knife('    if pressure:\n        return CRIT,', '    if False:\n        return CRIT,', "Authority.test_a5_pressure_reaps_now")
        self.run_knife('''        lv, reasons = thermal_state(cells)
        return lv, reasons, dict(info, why="no_history")''', '''        return OK, [], dict(info, why="no_history")''', "Authority.test_a7_no_history_falls_back_to_instant")
        self.run_knife('''    if targets:
        log({"action": "reap_begin"''', '''    if False:
        log({"action": "reap_begin"''', "Reason.test_a10_reason_logged_before_kill")


if __name__ == "__main__":
    unittest.main(verbosity=1)
