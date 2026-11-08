#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""11-08 DECISION-20 PR-B:推包(tools/release/ship.py)跑 aib test 的三處改走共用收整組工具 proc_group.run_group。

守:
S1 ship.py 裡不再有 timeout=7200 的 subprocess.run;reds_on / _rerun_selected / rerun_same 都經 _run_aib_test
R1 逾時:整組收掉(孫行程也不在)、rc 124(不是 0)、印出收掉的結果
R2 正常結束但留下背景行程:rc 照原樣(0),留下的行程收掉並印出
R3 起不來:rc 127
M1 收尾的話照實說:只有 stopped 才說「已收掉」;unverified(還在跑 / 身分證不了 / Windows 只殺直接子行程)
   說「沒能確認收乾淨」並附原因與還在跑的 pid;兩種都不改 rc(bb7dc #517 審)
G1 rerun_same 紅燈優先:逾時 / 起不來 / 跑壞 / rc≠0 列不出紅 ⇒ 全部算還是紅(不會被當偶發放行);
   rc 1 列得出紅 ⇒ 只回那些;rc 0 ⇒ 空;沒驗的那支算還是紅
G2 reds_on 逾時 ⇒ 一支都不算「主線也紅」,證據檔照寫(rc 124)
G3 _rerun_selected 逾時 ⇒ rc 124,呼叫端(預檢 / 疊主線)照紅擋
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tools" / "release"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
try:  # ship.py 會拿機器租約 ⇒ 載 ship 的測試進 24d 母體;開源版沒有這支治具
    import aib_host_lease_fixture  # noqa: E402,F401
except ImportError:
    pass
import ship  # noqa: E402

SRC = (ROOT / "tools" / "release" / "ship.py").read_text(encoding="utf-8")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_gone(pid, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def func_src(name):
    i = SRC.index("\ndef %s(" % name)
    j = SRC.find("\ndef ", i + 1)
    return SRC[i:j]


class Static(unittest.TestCase):
    def test_s1_no_raw_7200_run_left(self):
        self.assertNotIn("timeout=7200", SRC)
        for fn in ("reds_on", "_rerun_selected", "rerun_same"):
            with self.subTest(fn=fn):
                body = func_src(fn)
                self.assertIn("_run_aib_test(", body)
                self.assertNotIn("subprocess.run(", body)
        self.assertIn("proc_group.run_group(", func_src("_run_aib_test"))


@unittest.skipUnless(hasattr(os, "killpg"), "process groups are POSIX-only")
class RealGroup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ship-rg-")
        self.pidf = os.path.join(self.d, "gc.pid")
        self._old = ship.AIB_TEST_TIMEOUT_S

    def tearDown(self):
        ship.AIB_TEST_TIMEOUT_S = self._old
        try:  # 萬一刀測讓孫行程活下來:只收自己寫下的那一個 pid
            pid = int(Path(self.pidf).read_text())
            if alive(pid):
                os.kill(pid, 9)
        except (OSError, ValueError):
            pass
        shutil.rmtree(self.d, ignore_errors=True)

    def gc_pid(self):
        return int(Path(self.pidf).read_text().strip())

    def test_r1_timeout_stops_whole_group_rc_124(self):
        ship.AIB_TEST_TIMEOUT_S = 1
        cmd = ["/bin/sh", "-c", 'sleep 60 & echo $! > "$1"; echo "✗ 紅 tests/test-a.py"; wait', "sh", self.pidf]
        rc, out = ship._run_aib_test(cmd, self.d)
        self.assertEqual(rc, ship.RC_TIMEOUT)
        self.assertNotEqual(rc, 0)
        self.assertTrue(wait_gone(self.gc_pid()), "孫行程(sleep 60)要跟著整組收掉")
        self.assertIn("tests/test-a.py", out)

    def test_r2_normal_exit_stops_leftovers_rc_kept(self):
        cmd = ["/bin/sh", "-c", 'sleep 60 & echo $! > "$1"; echo done', "sh", self.pidf]
        rc, out = ship._run_aib_test(cmd, self.d)
        self.assertEqual(rc, 0)
        self.assertIn("done", out)
        self.assertTrue(wait_gone(self.gc_pid()), "正常結束後留下的背景行程也要收")

    def test_r2b_red_rc_kept(self):
        rc, _ = ship._run_aib_test(["/bin/sh", "-c", "exit 1"], self.d)
        self.assertEqual(rc, 1)

    def test_r3_cannot_start_rc_127(self):
        rc, out = ship._run_aib_test([os.path.join(self.d, "no-such-binary")], self.d)
        self.assertEqual(rc, ship.RC_NOSTART)
        self.assertIn("no-such-binary", out)


class Messages(unittest.TestCase):
    """注入 run_group 的結果,看 _run_aib_test 印什麼、回什麼 rc。"""

    def setUp(self):
        import proc_group
        self.pg, self._rg = proc_group, proc_group.run_group

    def tearDown(self):
        self.pg.run_group = self._rg

    def run_with(self, result):
        self.pg.run_group = lambda *a, **k: result
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc, _ = ship._run_aib_test(["x"], "/")
        return rc, buf.getvalue()

    def R(self, rc, timed_out=False, stop=None):
        return self.pg.Result(rc, "", timed_out=timed_out, stop=stop)

    def test_m1_timeout_stopped(self):
        rc, msg = self.run_with(self.R(None, True, self.pg.StopReport("stopped")))
        self.assertEqual(rc, ship.RC_TIMEOUT)
        self.assertIn("已收掉", msg)
        self.assertNotIn("沒能確認", msg)

    def test_m1_timeout_unverified_names_why_and_pids(self):
        for why, pids in (("members_still_running", [111, 222]), ("leader_identity_changed", []),
                          ("windows_direct_child_only", [])):
            with self.subTest(why=why):
                rc, msg = self.run_with(self.R(None, True, self.pg.StopReport("unverified", why, survivors=pids)))
                self.assertEqual(rc, ship.RC_TIMEOUT)
                self.assertIn("沒能確認", msg)
                self.assertNotIn("已收掉", msg)
                self.assertIn(why, msg)
                for pid in pids:
                    self.assertIn(str(pid), msg)

    def test_m1_leftovers(self):
        rc, msg = self.run_with(self.R(1, stop=self.pg.StopReport("stopped")))
        self.assertEqual((rc, "已收掉" in msg, "沒能確認" in msg), (1, True, False))
        rc, msg = self.run_with(self.R(0, stop=self.pg.StopReport("unverified", "windows_leftovers_not_checked")))
        self.assertEqual(rc, 0, "收尾沒驗證不改 rc")
        self.assertIn("沒能確認", msg)
        self.assertIn("windows_leftovers_not_checked", msg)
        self.assertNotIn("已收掉", msg)
        rc, msg = self.run_with(self.R(0))
        self.assertEqual((rc, msg), (0, ""))

    def test_m1_four_locales_differ(self):
        import i18n_lib
        for loc in ("zh-TW", "en", "ja", "ko"):
            with self.subTest(loc=loc):
                p = {"s": 1, "rc": 0, "why": "w", "pids": "1"}
                for k in ("aib_test_timeout", "aib_test_leftovers"):
                    a = i18n_lib.t("aib.ship." + k, p, locale=loc)
                    b = i18n_lib.t("aib.ship." + k + "_unverified", p, locale=loc)
                    self.assertNotEqual(a, b)
                    self.assertNotIn("aib.ship.", a + b, "缺字典")
                    self.assertIn("w", b)


class RedFirst(unittest.TestCase):
    def setUp(self):
        self._run = ship._run_aib_test

    def tearDown(self):
        ship._run_aib_test = self._run

    def fake(self, rc, out):
        ship._run_aib_test = lambda cmd, cwd: (rc, out)

    def test_g1_rerun_same(self):
        prof = type("P", (), {"gate_mode": "aib"})()
        tests = ["tests/test-a.py", "tests/test-b.py"]
        cases = [
            (ship.RC_TIMEOUT, "✗ 紅 tests/test-a.py", set(tests)),   # 逾時:b 沒證明變綠
            (ship.RC_NOSTART, "", set(tests)),
            (2, "", set(tests)),                                      # 跑壞
            (1, "", set(tests)),                                      # rc≠0 卻列不出紅
            (1, "  └ ✗ 紅 tests/test-a.py(rc=1)", {"tests/test-a.py"}),
            (0, "", set()),
        ]
        for rc, out, want in cases:
            with self.subTest(rc=rc, out=out):
                self.fake(rc, out)
                self.assertEqual(ship.rerun_same("/nonexistent", tests, prof), want)
        unv = ship.i18n_lib.t("aib.test.row_unverified", {"test": "tests/test-b.py", "what": "x"}, locale=ship.LOC)
        self.fake(0, unv + "\n")
        self.assertEqual(ship.rerun_same("/nonexistent", tests, prof), {"tests/test-b.py"}, "沒驗 ≠ 變綠")

    def test_g2_reds_on_timeout_exempts_nothing(self):
        seen = {}
        old_git, old_ev = ship.git, ship.write_base_evidence
        ship.git = lambda *a, **k: ""
        ship.write_base_evidence = lambda repo, base, wt, tests, rc, out: seen.update(rc=rc)
        try:
            prof = type("P", (), {"gate_mode": "aib"})()
            self.fake(ship.RC_TIMEOUT, "  └ ✗ 紅 tests/test-a.py(rc=1)")
            self.assertEqual(ship.reds_on("/nonexistent", "abc", ["tests/test-a.py"], prof=prof), set())
            self.assertEqual(seen.get("rc"), ship.RC_TIMEOUT)
            self.fake(1, "  └ ✗ 紅 tests/test-a.py(rc=1)")
            self.assertEqual(ship.reds_on("/nonexistent", "abc", ["tests/test-a.py"], prof=prof), {"tests/test-a.py"})
        finally:
            ship.git, ship.write_base_evidence = old_git, old_ev

    def test_g3_rerun_selected_timeout_is_red(self):
        self.fake(ship.RC_TIMEOUT, "")
        rc, reds = ship._rerun_selected("/nonexistent", ["tests/test-a.py"], None)
        self.assertEqual(rc, ship.RC_TIMEOUT)
        self.assertNotIn(rc, ship.PREFLIGHT_SKIP_RC, "預檢不能把逾時當「沒跑成、照走」")


if __name__ == "__main__":
    unittest.main()
