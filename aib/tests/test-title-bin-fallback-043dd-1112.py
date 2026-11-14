#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右窗格標題心跳選 aib-title 與壞掉時退回 title-beat.sh(11-12 @043dd,a63ab 審 aib-title L1 / N4)。
從 pane-right-tabs.sh 截出「選二進位 + title_beat_launch」那段，用真的 bash 照窗格的方式連起 6 幀
(上一幀結束才起下一幀),替身二進位 / 替身 title-beat.sh 各自記一行到同一個檔。
H1 二進位每次被 -9 殺(rc 137)⇒ 前 3 幀走二進位，第 4 幀起走 title-beat.sh;fallback 記錄有 rc=137
H2 二進位每次 rc 1(取樣失敗，兩路一樣)⇒ 6 幀都走二進位，不退回
H3 二進位壞兩次好一次輪流 ⇒ 連續次數歸零，不退回
H4 AIB_TITLE_BIN 給相對路徑 ⇒ 不認，全走 title-beat.sh
H5 AIB_TITLE_BEAT 有設 ⇒ 就算 ~/.ez2-aib/bin 有 aib-title 也走 title-beat.sh
H6 沒設 AIB_TITLE_BIN ⇒ 找到 ~/.ez2-aib/bin/aib-title 就用
H7 二進位第 1 幀把自己刪掉 ⇒ 第 2 幀起走 title-beat.sh(127 不計，靠每幀的 -x 檢查;a63ab 增量審 L1)
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = Path(os.environ.get("AIB_TITLE_FALLBACK_SCRIPT") or ROOT / "src" / "cockpit" / "pane-right-tabs.sh")
BASH = os.environ.get("AIB_TEST_BASH") or shutil.which("bash") or "/bin/bash"

DRIVER = r'''
TITLE_BEAT_PID=""
for _i in 1 2 3 4 5 6; do
  title_beat_launch
  while kill -0 "$TITLE_BEAT_PID" 2>/dev/null; do sleep 0.05; done
done
wait "$TITLE_BEAT_PID" 2>/dev/null
echo "END bin=[$TITLE_BIN] fails=$TITLE_BIN_FAILS"
'''


def block():
    s = SCRIPT.read_text(encoding="utf-8")
    i = s.index('TITLE_BIN=""\nif [ -z "${AIB_TITLE_BEAT:-}" ]; then')
    end = "# title_beat_launch end\n"
    j = s.index(end, i) + len(end)
    return s[i:j]


def exe(p, body):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    p.chmod(0o755)
    return p


class TitleBinFallback(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.t = Path(self.td.name)
        self.log = self.t / "calls.log"
        self.state = self.t / "state"
        self.state.mkdir()
        self.cockpit = self.t / "cockpit"
        exe(self.cockpit / "title" / "title-beat.sh", 'echo bash >> "$CALL_LOG"\n')
        self.bins = self.t / "bins"

    def tearDown(self):
        self.td.cleanup()

    def run_pane(self, extra_env):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.t / "home"),
            "EZ2_COCKPIT_DIR": str(self.cockpit),
            "EZ2_COCKPIT_STATE_DIR": str(self.state),
            "CALL_LOG": str(self.log),
        }
        env.update(extra_env)
        r = subprocess.run(
            [BASH, "-c", block() + DRIVER],
            cwd=self.t,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.log.read_text().split() if self.log.exists() else []
        return calls, r.stdout, r.stderr

    def fallback_log(self):
        f = self.state / "aib-title-fallback.log"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def test_h1_killed_binary_falls_back_after_three(self):
        b = exe(self.bins / "aib-title", 'echo bin >> "$CALL_LOG"\nkill -9 $$\n')
        calls, out, err = self.run_pane({"AIB_TITLE_BIN": str(b)})
        self.assertEqual(calls, ["bin"] * 3 + ["bash"] * 3, f"FAIL: H1 fallback {calls} {out} {err}")
        self.assertIn("END bin=[]", out, "FAIL: H1 bin_cleared")
        self.assertIn("rc=137", self.fallback_log(), "FAIL: H1 fallback_log")
        self.assertIn("rc=137", err, "FAIL: H1 stderr_line")

    def test_h2_sampler_failure_does_not_fall_back(self):
        b = exe(self.bins / "aib-title", 'echo bin >> "$CALL_LOG"\nexit 1\n')
        calls, out, _ = self.run_pane({"AIB_TITLE_BIN": str(b)})
        self.assertEqual(calls, ["bin"] * 6, f"FAIL: H2 no_fallback_on_rc1 {calls}")
        self.assertEqual(self.fallback_log(), "", "FAIL: H2 no_log")

    def test_h3_a_success_resets_the_count(self):
        cnt = self.t / "cnt"
        cnt.write_text("0")
        b = exe(
            self.bins / "aib-title",
            f'echo bin >> "$CALL_LOG"\nn=$(($(cat {cnt}) + 1)); echo $n > {cnt}\n'
            '[ $((n % 3)) -eq 0 ] && exit 0\nkill -9 $$\n',
        )
        calls, out, _ = self.run_pane({"AIB_TITLE_BIN": str(b)})
        self.assertEqual(calls, ["bin"] * 6, f"FAIL: H3 reset {calls} {out}")

    def test_h4_relative_bin_is_ignored(self):
        exe(self.bins / "aib-title", 'echo bin >> "$CALL_LOG"\n')
        calls, _, _ = self.run_pane({"AIB_TITLE_BIN": "bins/aib-title"})
        self.assertEqual(calls, ["bash"] * 6, f"FAIL: H4 relative {calls}")

    def test_h5_title_beat_override_wins(self):
        exe(self.t / "home" / ".ez2-aib" / "bin" / "aib-title", 'echo bin >> "$CALL_LOG"\n')
        stub = str(self.cockpit / "title" / "title-beat.sh")
        calls, _, _ = self.run_pane({"AIB_TITLE_BEAT": stub})
        self.assertEqual(calls, ["bash"] * 6, f"FAIL: H5 override {calls}")

    def test_h6_default_location_is_used(self):
        exe(self.t / "home" / ".ez2-aib" / "bin" / "aib-title", 'echo bin >> "$CALL_LOG"\n')
        calls, out, _ = self.run_pane({})
        self.assertEqual(calls, ["bin"] * 6, f"FAIL: H6 default {calls} {out}")

    def test_h7_removed_binary_falls_back_next_frame(self):
        b = exe(self.bins / "aib-title", 'rm -f "$0"\necho bin >> "$CALL_LOG"\n')
        calls, out, _ = self.run_pane({"AIB_TITLE_BIN": str(b)})
        self.assertEqual(calls, ["bin"] + ["bash"] * 5, f"FAIL: H7 removed {calls} {out}")
        self.assertIn("END bin=[]", out, "FAIL: H7 bin_cleared")


if __name__ == "__main__":
    unittest.main(verbosity=2)
