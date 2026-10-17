#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""真人核准的「tty 屬登入使用者」那一條(10-17 the maintainer 在普通終端機跑 aib newproj --approve 被擋 tty_owner)。

macOS 的 ttyname(/dev/tty 的 fd) 回 "/dev/tty"(root 擁有),不是真的 /dev/ttysNNN ⇒ 以前每一台 Mac 都不過。
T1 在真的偽終端機裡(script 開一個 pty)跑 probe_tty ⇒ 路徑不是 "/dev/tty"、擁有者 = 自己
T2 沒有控制終端機(本測試行程本身,stdin 不是終端機)⇒ (None, None, 名),照舊不過
T3 judge:tty 三件對得上、但在 hub 裡或鏈上有 AI ⇒ 照樣不是真人(這次修正不放寬另外兩條)
刀(複本):K1 改回只用 /dev/tty 那個 fd 的名字(_stdio_tty 永遠拿不到)⇒ macOS 上 T1 紅

10-18:修正本身是 @abb89 #119(0.1.16,probe_tty 回 /dev/tty 時改用 stdin/stdout/stderr 的終端機名);
這支只補「在真的偽終端機裡跑」那一層測試與刀(原本隨 #123 送,#123 與 #119 重複已關)。
"""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
import human_attest  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

THIS = Path(__file__).name
SCRIPT = shutil.which("script")
PROBE = "import sys; sys.path.insert(0, %r); import human_attest as h; p, o, me = h.probe_tty(); print('RESULT', p, o, me)"


def probe_in_pty(cockpit):
    """用 script 開偽終端機跑 probe_tty。macOS:script -q /dev/null cmd…;Linux:script -qc 'cmd' /dev/null。"""
    cmd = [sys.executable, "-c", PROBE % str(cockpit)]
    if sys.platform == "darwin":
        argv = [SCRIPT, "-q", "/dev/null", *cmd]
    else:
        import shlex
        argv = [SCRIPT, "-qc", " ".join(shlex.quote(c) for c in cmd), "/dev/null"]
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    line = next((ln for ln in r.stdout.replace("\r", "").splitlines() if "RESULT" in ln), "")
    return line.split("RESULT", 1)[1].split() if line else []


class Tty(unittest.TestCase):
    @unittest.skipUnless(SCRIPT, "沒有 script 指令,開不了偽終端機")
    def test_T1_real_device_in_pty(self):
        got = probe_in_pty(COCKPIT)
        self.assertEqual(len(got), 3, got)
        path, owner, me = got
        self.assertNotEqual(path, "/dev/tty", "要拿到真的終端機裝置,不是 /dev/tty 本身")
        self.assertTrue(path.startswith("/dev/"), path)
        self.assertEqual(owner, me, "偽終端機屬於開它的使用者")

    def test_T2_no_controlling_tty(self):
        r = subprocess.run([sys.executable, "-c", PROBE % str(COCKPIT)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60, start_new_session=True)
        parts = r.stdout.split("RESULT", 1)[1].split()
        self.assertEqual(parts[:2], ["None", "None"], "沒有控制終端機 ⇒ 照舊拿不到 tty")

    def test_T3_other_conditions_still_hold(self):
        tty = ("/dev/ttys018", "andy", "andy")
        chain = [(100, "zsh"), (99, "login"), (98, "Terminal")]
        clean = human_attest.judge(chain, tty, set(), env={})
        self.assertNotIn("tty_owner", clean["missing"])
        hub = human_attest.judge(chain, tty, set(), env={"TMUX": "/tmp/tmux-501/x,1,0"})
        self.assertIn("in_hub", hub["missing"])
        self.assertFalse(hub["human"])
        ai = human_attest.judge([(100, "zsh"), (99, "claude"), (98, "Terminal")], tty, set(), env={})
        self.assertIn("session", ai["missing"])
        self.assertFalse(ai["human"])


class Knife(unittest.TestCase):
    @unittest.skipUnless(SCRIPT, "沒有 script 指令")
    def test_K1_back_to_dev_tty_name_turns_T1_red(self):
        needle = '                if name and name != "/dev/tty":'
        with knife_copy(REPO, "src/cockpit/human_attest.py", needle, "                if False:", tests=(THIS,)) as copy_root:
            r = run_guarded([sys.executable, str(copy_root / "tests" / THIS), "Tty.test_T1_real_device_in_pty"],
                            cwd=str(copy_root), timeout=120)
        if sys.platform == "darwin":
            self.assertNotEqual(r.returncode, 0, "只用 /dev/tty 的名字時 macOS 上 T1 應該紅:\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
