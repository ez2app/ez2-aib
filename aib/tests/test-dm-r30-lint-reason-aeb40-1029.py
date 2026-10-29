#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 被 R-30 lint 擋 ⇒ 收據講 r30-lint 與該改的字(10-29 開艙說明 e 張;@aeb40 一天被擋 6 封都只看到 engine-rc-3)。

R1 _engine_block_kind:引擎 log 有咽喉的 {"code":"r30_lint"} ⇒ r30-lint(刀:拿掉那一行 ⇒ 紅)
R2 r30_lint.py 真跑圈號 ⇒ 輸出行的形狀對得上 dm-ctl 抓位置用的 grep(lint 改格式時這格紅,不是收據靜默變空)
R3 dm-ctl 的 r30-lint 出口在兩幀比對之前、rc 2、不自動重送(同一段字再送一樣擋)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DM_CTL = ROOT / "src" / "cockpit" / "dm-ctl.sh"
LINT = ROOT / "src" / "messaging" / "r30_lint.py"
GREP = r"^[[:space:]]+[0-9]+:[0-9]+ "


def _fn(src, name):
    start = src.index(name + "() {")
    return src[start:src.index("\n}\n", start) + 3]


class R30Reason(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeb40-r30-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = DM_CTL.read_text(encoding="utf-8")

    def test_r1_block_kind(self):
        log = os.path.join(self.tmp, "eng.log")
        Path(log).write_text('R-30 lint 擋下 1 處:\n  1:3 ① 圈號/圈圈字(U+2460)  -> 建議:1.\n'
                             '{"decision":"deny","code":"r30_lint","reason":"R-30 lint 擋下,訊息未送出"}\n', encoding="utf-8")
        p = subprocess.run(["bash", "-c", _fn(self.src, "_engine_block_kind") + '\n_engine_block_kind "$1"', "x", log],
                           capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "r30-lint", p.stdout + p.stderr)

    def test_r2_lint_output_matches_grep(self):
        p = subprocess.run([sys.executable, "-B", str(LINT), "--text", "好 ①② 好"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0, "圈號要被擋")
        log = os.path.join(self.tmp, "lint.log")
        Path(log).write_text(p.stdout + p.stderr, encoding="utf-8")
        g = subprocess.run(["bash", "-c", 'grep -E "$1" "$2" | sed -E "s/^[[:space:]]+//" | head -5 | paste -sd ";" -',
                            "x", GREP, log], capture_output=True, text=True)
        self.assertIn("U+2460", g.stdout, p.stdout + p.stderr)
        self.assertIn(";", g.stdout, "兩處都要列出")

    def test_r3_exit_before_frame_check_no_resend(self):
        i = self.src.index('if [ "$_AE_BLOCK_KIND" = "r30-lint" ]; then')
        block = self.src[i:self.src.index("\n    fi\n", i)]
        self.assertLess(i, self.src.index('if [ "$_AE_RESID" = "0" ]; then'), "要在兩幀比對之前(那邊會變 rc 4)")
        self.assertIn("emit_json 2 r30-lint", block)
        self.assertNotIn("_auto_resend", block, "同一段字再送一樣擋,不准自動重送")
        self.assertIn(GREP, block, "抓位置的 grep 要跟 R2 驗的同一條")
        self.assertTrue(re.search(r"exit 2\s*$", block), block)


if __name__ == "__main__":
    unittest.main()
