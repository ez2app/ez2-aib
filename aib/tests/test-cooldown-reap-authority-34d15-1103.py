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
A14 多面板:鎖被占就退路、併發寫不掉樣本、慢的不蓋別人新樣本;A15 熱壓力讀不到 + 取樣接不上 ⇒ 退回瞬間值,照收
A16 兩筆中間的空白不算連續;A17 壞歷史當算不出;A18 退路也認平均 ≥ 門檻
A19 記不下原因照收、原因印 stderr;A20 收到一半被砍(只有 reap_begin)也查得到;A21 紅燈那行的彙總
刀(複本):K1 回到看瞬間值;K2 熱壓力不算;K3 沒歷史就不收;K4 拿掉先記原因;K5 拿掉鎖;K6 拿掉 no_signal;
K7 筆數不看;K8 壞檔當空;K9 只算收完那行。每刀要指定測試 FAIL 才算,匯入失敗不算。
"""
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
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


class FailureModes(Base):
    def hist(self):
        return os.path.join(self.state, "cooldown-temp-history.json")

    def test_a14a_lock_held_means_no_history(self):
        import fcntl
        with open(self.hist() + ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            with mock.patch.object(C, "HISTORY_LOCK_WAIT_S", 0.1):
                self.assertIsNone(C._record_sample(self.hist(), 100.0, 80.0))

    def test_a14b_concurrent_writers_keep_samples(self):
        code = ("import sys; sys.path.insert(0, %r); import cooldown as C\n"
                "for i in range(30): assert C._record_sample(%r, 1000.0 + i, 80.0) is not None\n"
                ) % (str(ROOT / "src" / "cockpit"), self.hist())
        ps = [subprocess.Popen([sys.executable, "-B", "-c", code]) for _ in range(4)]
        self.assertEqual([p.wait() for p in ps], [0, 0, 0, 0])
        self.assertEqual(len(C._load_history(self.hist())), 120)

    def test_a14c_slow_writer_keeps_newer_low_sample(self):
        C._record_sample(self.hist(), 1000.0, 91.0)
        C._record_sample(self.hist(), 1040.0, 70.0)
        rows = C._record_sample(self.hist(), 1020.0, 91.0)
        self.assertEqual([r[1] for r in rows], [91.0, 91.0, 70.0])
        self.assertEqual(C._held_for(rows, 88.0), 0.0)

    def test_a15_gap_without_pressure_falls_back(self):
        self.pressure = None
        self.step(95.0, 91.0, dt=SUSTAIN_GAP + 1)
        self.assertEqual(len(self.reaps), 1)
        self.assertEqual(self.reaps[0]["why"], "no_signal")

    def test_a15b_gap_with_pressure_zero_restarts(self):
        self.step(95.0, 91.0, dt=SUSTAIN_GAP + 1)
        self.step(95.0, 91.0, dt=SUSTAIN_GAP + 1)
        self.assertEqual(self.reaps, [])

    def test_a16_two_samples_across_pause_not_sustained(self):
        self.step(95.0, 91.0)
        self.step(95.0, 91.0, dt=SUSTAIN_GAP - 1)
        self.assertEqual(self.reaps, [], "兩筆之間的空白不算證據")
        self.step(95.0, 91.0, dt=30)
        self.assertEqual(len(self.reaps), 1)

    def test_a17_corrupt_history_is_no_history(self):
        pathlib.Path(self.hist()).write_text("{broken", encoding="utf-8")
        self.step(91.0, 80.0)
        self.assertEqual(len(self.reaps), 1)
        self.assertEqual(self.reaps[0]["why"], "no_history")
        self.assertEqual(len(C._load_history(self.hist())), 1, "壞檔換掉,下一輪重算")

    def test_a18_fallback_honours_avg(self):
        with mock.patch.object(C, "_save_history", lambda *a: False):
            self.step(89.0, 88.5)
        self.assertEqual(len(self.reaps), 1)


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
        self.assertEqual(seen[:1], [["reap_begin"]], "動手前原因已落檔")
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

    PS = "11 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless=new --user-data-dir=/tmp/my-prof\n"

    def test_a19_log_failure_still_kills_and_prints_reason(self):
        killed, err = [], io.StringIO()
        with mock.patch.object(C, "log", lambda e: False), mock.patch("sys.stderr", err):
            C.reap(runner=lambda cmd: self.PS, killer=killed.append, reason={"why": "pressure"})
        self.assertEqual(killed, [11])
        self.assertIn("pressure", err.getvalue())

    def test_a20_orphan_begin_is_reported(self):
        def killer(pid):
            raise SystemExit(9)
        with self.assertRaises(SystemExit):
            C.reap(runner=lambda cmd: self.PS, killer=killer, reason={"why": "pressure"})
        got = C.reaped_since(60)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["incomplete"])
        self.assertEqual(got[0]["killed"], 1)
        self.assertEqual(got[0]["reason"], {"why": "pressure"})

    def test_a20b_interleaved_panels_not_double_counted(self):
        at = "2026-11-03T12:00:00"
        rows = [{"action": "reap_begin", "by": 1, "at": at, "pids": [11]}, {"action": "reap_begin", "by": 2, "at": at, "pids": [12]},
                {"action": "reap", "by": 1, "at": at, "killed": 1, "pids": [11]}, {"action": "reap", "by": 2, "at": at, "killed": 1, "pids": [12]}]
        f = os.path.join(self.tmp.name, "x.log")
        pathlib.Path(f).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        now = time.mktime(time.strptime(at, "%Y-%m-%dT%H:%M:%S")) + 5
        got = C.reaped_since(60, path=f, now=now)
        self.assertEqual(len(got), 2)
        self.assertFalse(any(d.get("incomplete") for d in got))

    def test_a21_summary(self):
        self.assertIsNone(C.reaped_summary(60))
        C.reap(runner=lambda cmd: self.PS, killer=lambda pid: None, reason={"why": "pressure"})
        self.assertEqual(C.reaped_summary(60), {"batches": 1, "n": 1})
        src = (ROOT / "tools" / "verify" / "test_entry.py").read_text(encoding="utf-8")
        self.assertIn("cooldown.reaped_summary(", src)

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
            self.assertIn("FAIL: " + target.split(".")[1], r.stderr, "要這支測試斷言紅,匯入失敗不算刀咬到")

    def test_knives(self):
        if os.environ.get("AIB_NO_KNIVES"):
            self.skipTest("刀內不再跑刀")
        self.run_knife('if auth_lv == CRIT:\n        out["reap"]', 'if thermal_lv == CRIT:\n        out["reap"]', "Authority.test_a1_spike_not_reaped")
        self.run_knife('    if pressure:\n        return CRIT,', '    if False:\n        return CRIT,', "Authority.test_a5_pressure_reaps_now")
        self.run_knife('''        return _instant_authority(cells, value, info, "no_history")''', '''        return OK, [], dict(info, why="no_history")''', "Authority.test_a7_no_history_falls_back_to_instant")
        self.run_knife('''    if targets:
        begin = {"action": "reap_begin"''', '''    if False:
        begin = {"action": "reap_begin"''', "Reason.test_a10_reason_logged_before_kill")
        self.run_knife('fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)', 'pass', "FailureModes.test_a14a_lock_held_means_no_history")
        self.run_knife('if pressure is None and (len(rows) < 2', 'if False and (len(rows) < 2', "FailureModes.test_a15_gap_without_pressure_falls_back")
        self.run_knife('if n >= SUSTAIN_MIN_SAMPLES else 0.0', 'if n >= 1 else 0.0', "FailureModes.test_a16_two_samples_across_pause_not_sustained")
        self.run_knife("""    except (OSError, ValueError, TypeError):
        return None
""", """    except (OSError, ValueError, TypeError):
        return []
""", "FailureModes.test_a17_corrupt_history_is_no_history")
        self.run_knife('out.extend(orphan(d) for d in open_begin.values())', 'pass', "Reason.test_a20_orphan_begin_is_reported")


if __name__ == "__main__":
    unittest.main(verbosity=1)
