#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib dm:目標席的 Claude 輸入框停在「History n/m」時不當成草稿(10-14 交接單 K8;@977e3 21:05 真的碰到)。

同一目錄的 Claude 席共用輸入歷史:按了 ↑ 之後框頂線變成 `─── History 100/100 ───`,框裡是**別席的舊訊息**(不是草稿)。
以前 composer_state 判 draft(或多行那筆判 unknown)⇒ aib dm 擋下(stage=blocked),唯一解法是 tmux send-keys Down 按 40 次。
樣本:tests/fixtures/claude-history/ 是 10-14 22:3x 在 Pro 上用真的 Claude Code v2.1.299 擷的畫面(capture-pane -e,
跳脫碼與框線原樣),框裡的舊訊息與路徑換成中性文字。
C1 單行那筆(History 100/100):--modal-aware 印 history;預設(舊字彙)仍印 draft(相容線,同 browse)
C2 多行那筆(History 99/100,提示符那行已不在尾端 8 行內):也印 history,不是 unknown
C3 回到空框的真畫面:照舊 suggestion(對照)
C4 擋下理由:講清楚是 History 模式、不是草稿、怎麼解(按 ↓ 回到空框),可安全重送
C5 退出 History(真 tmux + 假 TUI,要按 3 次 ↓ 才回空框):只按 ↓、按到離開就停(3 次)、回 0
C6 畫面在動(有人正在操作):一次都不按,回非 0
C7 怎麼按都不離開:按到上限就停,回非 0、說原因
C8 dm-ctl 認得引擎的 block_kind=history(分到 history-mode,不是 foreign-draft)
刀(複本):K1 拿掉框頂線判斷 ⇒ C1、C2 紅;K2 退出不檢查畫面靜止 ⇒ C6 紅;K3 dm-ctl 不認 history ⇒ C8 紅
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
FIX = ROOT / "tests" / "fixtures" / "claude-history"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402

TMUX = shutil.which("tmux")
STATE = COCKPIT / "composer_state.py"
HELPER = COCKPIT / "composer_history.py"
DM_CTL = COCKPIT / "dm-ctl.sh"

# 假 TUI:先畫 History 畫面;每收到一個 ↓ 計一次,滿 N 次就清畫面改畫空框。mode=busy 每 0.2 秒重畫一個計數(有人在動)。
FAKE = r'''
import os, sys, termios, time, tty, select
hist, empty, need, mode, log = sys.argv[1:6]
need = int(need)
fd = sys.stdin.fileno()
tty.setraw(fd)
def draw(path, extra=""):
    sys.stdout.write("\x1b[2J\x1b[H" + open(path, encoding="utf-8").read().replace("\n", "\r\n") + extra)
    sys.stdout.flush()
draw(hist)
downs, buf, tick = 0, b"", 0
while True:
    r, _, _ = select.select([fd], [], [], 0.2)
    if mode == "busy" and not r:
        tick += 1
        draw(hist, "\r\ntick %d" % tick)
        continue
    if not r:
        continue
    buf += os.read(fd, 64)
    while b"\x1b[B" in buf:
        buf = buf.replace(b"\x1b[B", b"", 1)
        downs += 1
        open(log, "a").write("down\n")
        if mode == "normal" and downs >= need:
            draw(empty)
    if buf.strip(b"\x1b[B"):
        open(log, "a").write("other %r\n" % buf)
        buf = b""
'''


class Detect(unittest.TestCase):
    def state(self, name, *extra):
        p = subprocess.run([sys.executable, str(STATE), "--stdin", "--provider", "claude", *extra],
                           input=(FIX / name).read_text(encoding="utf-8"), capture_output=True, text=True)
        return p.stdout.strip()

    def test_c1_single_line_entry(self):
        self.assertEqual(self.state("history-100-of-100.txt", "--modal-aware"), "history")
        self.assertEqual(self.state("history-100-of-100.txt"), "draft")

    def test_c2_multiline_entry(self):
        self.assertEqual(self.state("history-99-of-100-multiline.txt", "--modal-aware"), "history")

    def test_c3_real_empty_box_unchanged(self):
        self.assertEqual(self.state("after-return-empty.txt", "--modal-aware"), "suggestion")

    def test_c4_block_reason(self):
        import composer_state as cs
        why = cs.send_block_reason("history") or ""
        self.assertIn("History", why)
        self.assertIn("不是草稿", why)
        self.assertIn("↓", why)
        self.assertIn("可安全重送", why)


@unittest.skipUnless(TMUX, "needs tmux")
class Leave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-hist-", dir=aib_tmp_root())
        self.sock = tempfile.mkdtemp(prefix="at.", dir="/tmp")
        self.env = dict(os.environ, TMUX_TMPDIR=self.sock)
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.log = os.path.join(self.tmp, "keys.log")
        self.fake = os.path.join(self.tmp, "fake_tui.py")
        Path(self.fake).write_text(FAKE, encoding="utf-8")

    def tearDown(self):
        subprocess.run([TMUX, "-L", "h", "kill-server"], env=self.env, capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.sock, ignore_errors=True)

    def start(self, need, mode="normal"):
        cmd = "%s %s %s %s %d %s %s" % (sys.executable, self.fake, FIX / "history-99-of-100-multiline.txt",
                                        FIX / "after-return-empty.txt", need, mode, self.log)
        subprocess.run([TMUX, "-L", "h", "new-session", "-d", "-x", "120", "-y", "45", "-s", "s", cmd],
                       env=self.env, check=True)
        time.sleep(1.0)

    def leave(self, *extra):
        p = subprocess.run([sys.executable, str(HELPER), "leave", "--socket", "h", "--pane", "s:0.0",
                            "--provider", "claude", "--tmux", TMUX, *extra],
                           env=self.env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def keys(self):
        try:
            return Path(self.log).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def test_c5_leaves_after_three_downs(self):
        self.start(3)
        rc, out = self.leave()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.keys(), ["down"] * 3, out)

    def test_c6_moving_screen_not_touched(self):
        self.start(3, mode="busy")
        rc, out = self.leave()
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.keys(), [], out)

    def test_c7_stuck_stops_at_cap(self):
        self.start(1000)
        rc, out = self.leave("--max", "5")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.keys(), ["down"] * 5, out)
        self.assertIn("History", out)


class DmCtl(unittest.TestCase):
    def test_c8_block_kind_history(self):
        src = DM_CTL.read_text(encoding="utf-8")
        start = src.index("_engine_block_kind() {")
        end = src.index("\n}\n", start) + 3
        tmp = tempfile.mkdtemp(prefix="aib-hist-ctl-", dir=aib_tmp_root())
        try:
            log = os.path.join(tmp, "eng.log")
            Path(log).write_text("aib-dm: block_kind=history\n", encoding="utf-8")
            p = subprocess.run(["bash", "-c", src[start:end] + '\n_engine_block_kind "$1"', "x", log],
                               capture_output=True, text=True)
            self.assertEqual(p.stdout.strip(), "history-mode", p.stdout + p.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
