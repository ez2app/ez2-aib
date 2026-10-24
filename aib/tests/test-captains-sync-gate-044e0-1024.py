#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄的艦長列同步閘(10-24 @044e0 本機效能):captains-ctl --sync 一次約 50 支行程,以前每幀(5 秒)都跑。
現在只在席位檔 / 席位夾比上次同步新,或滿保底秒數才跑。從 pane-right-tabs.sh 截出閘那一段,用真的 bash 驗。
C1 沒有記號檔 ⇒ 該跑;剛蓋章、沒變 ⇒ 不跑
C2 某個席位檔比記號新 ⇒ 該跑
C3 新開一席(席位夾多一個檔,夾的 mtime 變新)⇒ 該跑;席位被收(檔搬走)⇒ 該跑
C4 滿保底秒數 ⇒ 該跑(窗被關掉這種席位檔看不到的變化靠它)
C5 量不到席位夾(EZ2_AIB_INSTANCE_HOME 空 / 不存在)⇒ 照舊每幀跑
C6 主迴圈真的先問閘才起 captains-ctl,且起之前先蓋章
刀(複本 + run_guarded):K1 拿掉逐檔比對 ⇒ C2 紅;K2 拿掉保底秒數 ⇒ C4 紅
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SCRIPT = ROOT / "src" / "cockpit" / "pane-right-tabs.sh"
BASH = os.environ.get("AIB_TEST_BASH") or shutil.which("bash") or "/bin/bash"


def gate_block():
    s = SCRIPT.read_text(encoding="utf-8")
    i = s.index('CAPTAINS_MARK="')
    j = s.index("\nwhile true; do", i)
    return s[i:j]


class Gate(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.home = self.d / "inst"
        (self.home / "seats").mkdir(parents=True)
        (self.home / "seats" / "w4.json").write_text("{}")
        self.state = self.d / "cockpit"
        self.state.mkdir()
        self.mark = self.state / ".captains-sync.mark"
        old = time.time() - 100
        for p in (self.home / "seats" / "w4.json", self.home / "seats"):
            os.utime(p, (old, old))

    def due(self, last_s="$SECONDS", home=None, max_s="30"):
        """跑一次閘;last_s = 上次同步時的 SECONDS(預設 = 現在,即剛同步過)。"""
        prog = gate_block() + "\nCAPTAINS_LAST_S=%s\ncaptains_sync_due && echo DUE || echo SKIP\n" % last_s
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "EZ2_AIB_INSTANCE_HOME": str(self.home) if home is None else home,
               "EZ2_COCKPIT_STATE_DIR": str(self.state), "EZ2_CAPTAINS_SYNC_MAX_S": max_s}
        p = subprocess.run([BASH, "-c", prog], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def stamp(self):
        self.mark.write_text("")

    def touch_new(self, p):
        t = time.time() + 5
        os.utime(p, (t, t))

    def test_c1_first_then_quiet(self):
        self.assertEqual(self.due(), "DUE")
        self.stamp()
        self.assertEqual(self.due(), "SKIP")

    def test_c2_seat_file_changed(self):
        self.stamp()
        self.touch_new(self.home / "seats" / "w4.json")
        self.assertEqual(self.due(), "DUE")

    def test_c3_seat_added_or_retired(self):
        self.stamp()
        (self.home / "seats" / "w9.json").write_text("{}")
        self.touch_new(self.home / "seats")
        self.assertEqual(self.due(), "DUE")
        self.stamp()
        old = time.time() - 100
        for p in self.home.joinpath("seats").iterdir():
            os.utime(p, (old, old))
        os.utime(self.home / "seats", (old, old))
        self.assertEqual(self.due(), "SKIP")
        (self.home / "seats" / "w9.json").unlink()
        self.touch_new(self.home / "seats")
        self.assertEqual(self.due(), "DUE")

    def test_c4_max_interval(self):
        self.stamp()
        self.assertEqual(self.due(last_s="$((SECONDS - 31))"), "DUE")
        self.assertEqual(self.due(last_s="$((SECONDS - 5))"), "SKIP")

    def test_c5_unmeasurable_runs_every_frame(self):
        self.stamp()
        self.assertEqual(self.due(home=""), "DUE")
        self.assertEqual(self.due(home=str(self.d / "nope")), "DUE")

    def test_c6_loop_uses_gate_and_stamps_first(self):
        s = SCRIPT.read_text(encoding="utf-8")
        i = s.index("elif captains_sync_due; then")
        seg = s[i:i + 600]
        self.assertLess(seg.index(': > "$CAPTAINS_MARK"'), seg.index("captains-ctl.sh"))
        self.assertEqual(s.count("captains-ctl.sh}"), 1, "迴圈裡不該還有別條不經過閘的 captains-ctl 呼叫")


KNIVES = [
    ("K1", '''  for f in "$d"/w*.json; do
    [ -e "$f" ] || continue
    [ "$f" -nt "$CAPTAINS_MARK" ] && return 0
  done
''', "", "Gate.test_c2_seat_file_changed"),
    ("K2", '''  [ $((SECONDS - CAPTAINS_LAST_S)) -ge "$CAPTAINS_SYNC_MAX_S" ] && return 0
''', "", "Gate.test_c4_max_interval"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, "src/cockpit/pane-right-tabs.sh", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s%s" % (tag, r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
