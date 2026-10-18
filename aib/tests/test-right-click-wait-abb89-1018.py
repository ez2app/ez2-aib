#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄重繪空窗的點擊(10-18 Andy:「點了不會切換,要點兩下」)。

pane-right-ai.sh 每幀撤 marker 再掛回(畫面與 rowmap 不錯配);空窗裡點到的那一下原本落到 MouseUp1Pane 的 else = 白點。
修法:右欄(@aib_role=right)marker 空著時交給 click-wait.sh,等 marker 掛回(最多 1.5 秒)再照新 rowmap 交 mouse-ctl --click。

W1 marker 先空 3 次再掛回 ai-list ⇒ 轉給 mouse-ctl --click ai-list x y session --role right(參數照傳)
W2 marker 一直空 ⇒ 等完放掉這一下:不叫 mouse-ctl、rc 0
W3 marker 是右欄以外的值(例如 tabs)⇒ 不接
W4 mouse-ctl --install 的 MouseUp1Pane:右欄空窗那一支叫 click-wait.sh,條件看 @aib_role=right 且 @aib_pane 空
K1 刀:click-wait 不等(只看一次)⇒ W1 紅
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit" / "mouse"


def _write_exec(p, text):
    p.write_text(text, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


class ClickWait(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-clickwait-"))
        self.mouse = self.tmp / "mouse"
        self.mouse.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.log"
        self.seq = self.tmp / "seq"
        # 假 mouse-ctl:只記參數
        _write_exec(self.mouse / "mouse-ctl.sh", '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s\n' % self.calls)
        # 假 tmux:show-options 依序吐 seq 檔的每一行(吐完就重複最後一行)
        _write_exec(self.bin / "tmux", '#!/bin/bash\nn=$(cat %s.n 2>/dev/null || echo 0); n=$((n+1)); echo $n > %s.n\n'
                    'line=$(sed -n "${n}p" %s); [ -n "$line" ] || line=$(tail -1 %s)\n'
                    '[ "$line" = "-" ] && line=""\nprintf "%%s\\n" "$line"\n' % (self.seq, self.seq, self.seq, self.seq))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_wait(self, seq, script=None, tries="30"):
        self.seq.write_text("\n".join(seq) + "\n", encoding="utf-8")
        dst = self.mouse / "click-wait.sh"
        dst.write_text(script if script is not None else (SRC / "click-wait.sh").read_text(encoding="utf-8"), encoding="utf-8")
        env = dict(os.environ, PATH="%s:%s" % (self.bin, os.environ.get("PATH", "")), AIB_CLICK_WAIT_TRIES=tries)
        env.pop("TMUX", None)
        r = subprocess.run(["bash", str(dst), "%6", "12", "7", "aib-s1"], capture_output=True, text=True, timeout=30,
                           env=env, stdin=subprocess.DEVNULL)
        calls = self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []
        return r, calls

    def test_w1_waits_then_clicks(self):
        r, calls = self.run_wait(["-", "-", "-", "ai-list"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["--click ai-list 12 7 aib-s1 --role right"])

    def test_w2_gives_up(self):
        r, calls = self.run_wait(["-"], tries="5")
        self.assertEqual((r.returncode, calls), (0, []))
        self.assertIn("沒掛回", r.stderr)

    def test_w3_other_marker_ignored(self):
        r, calls = self.run_wait(["tabs"], tries="3")
        self.assertEqual((r.returncode, calls), (0, []))

    def test_w4_install_binding(self):
        txt = (SRC / "mouse-ctl.sh").read_text(encoding="utf-8")
        m = re.search(r"RIGHT_GAP='([^']*)'", txt)
        self.assertIsNotNone(m)
        self.assertIn("#{==:#{@aib_role},right}", m.group(1))
        self.assertIn("#{==:#{@aib_pane},}", m.group(1))
        self.assertIn("click-wait.sh", txt[txt.index("CLICK_WAIT_Q '%q'"):txt.index("bind-key -n MouseUp1Pane")])
        self.assertIn("$CLICK_WAIT_Q", txt[txt.index("RIGHT_GAP_COMMAND="):txt.index("bind-key -n MouseUp1Pane")])
        up = txt[txt.index("bind-key -n MouseUp1Pane"):txt.index("COPY_CMD=\"\"")]
        self.assertIn("$RIGHT_GAP", up)
        self.assertIn("$RIGHT_GAP_COMMAND", up)

    def test_k1_knife_no_wait(self):
        src = (SRC / "click-wait.sh").read_text(encoding="utf-8")
        cut = src.replace('while [ "$i" -lt "$tries" ]; do', 'while [ "$i" -lt 1 ]; do')
        self.assertNotEqual(src, cut, "刀沒落下(找不到等待迴圈)")
        r, calls = self.run_wait(["-", "-", "-", "ai-list"], script=cut)
        self.assertEqual(calls, [], "刀沒砍出紅:不等也點到了")


if __name__ == "__main__":
    unittest.main()
