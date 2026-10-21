#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib summary template(PROPOSAL-gaps v3 第 2 件 B):印一份過得了格式檢查的最小總回報輸入,不用撞 lint 猜欄位。

M1 template 印出來的 JSON 跟 TEMPLATE 一樣;--out 寫檔、stdout 不印
M2 範本真的過:交給 tools/report/aib-report.py 渲染(含 report-lint)rc 0、沒警告;EZ2_AIB_HOME 指暫存,不寫真庫
M3 aib 入口有接 template
刀(複本):K1 範本少了 footer ⇒ M2 紅
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src" / "cockpit", ROOT / "tests"):
    sys.path.insert(0, str(_p))
import aib_summary  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


class Template(unittest.TestCase):
    def test_m1_print_and_out(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(aib_summary.main(["template"]), 0)
        self.assertEqual(json.loads(buf.getvalue()), aib_summary.TEMPLATE)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t.json"
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(aib_summary.main(["template", "--out", str(out)]), 0)
            self.assertEqual(buf.getvalue(), "")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), aib_summary.TEMPLATE)

    def test_m2_passes_lint(self):
        with tempfile.TemporaryDirectory(prefix="aib-sumtpl-") as td:
            f = Path(td) / "t.json"
            aib_summary.template(str(f))
            env = dict(os.environ, EZ2_AIB_HOME=str(Path(td) / "home"), EZ2_AIB_STATE_ROOT=str(Path(td) / "home"),
                       TMPDIR=td, EZ2_AIB_SWEEP_PASSES="0", EZ2_CTX_DIR=str(Path(td) / "ctx"))
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "report" / "aib-report.py"), str(f)], cwd=str(ROOT),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
            both = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, both[-1500:])
            self.assertIn("硬擋 0、警告 0", both, both[-1500:])

    def test_m3_wired(self):
        src = (ROOT / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        self.assertIn("aib summary write|status|template", src)
        self.assertIn('t = sub.add_parser("template")', (ROOT / "src" / "cockpit" / "aib_summary.py").read_text(encoding="utf-8"))


class Knife(unittest.TestCase):
    def test_k1_no_footer(self):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/aib_summary.py", '    "footer": {"dispatched": 0,', '    "footer_x": {"dispatched": 0,',
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Template.test_m2_passes_lint"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
