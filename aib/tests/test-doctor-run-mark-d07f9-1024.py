#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib doctor 從指令列跑完記最後一次(state/run-marks/doctor.json = {at, rc};動力室 R10 讀;@abb89 10-24 03:2x 准)。暫存狀態根。

M1 main 跑完 ⇒ 檔只有 at / rc、rc 跟 main 回的一樣;再跑一次是覆寫、不累積
M2 寫不進去(run-marks 是檔不是夾)⇒ doctor 照樣回原本的 rc、不炸
M3 run_all 給別支呼叫 ⇒ 不記
M4 文字輸出(不帶 --json)也照記 {at, rc}(10-24 突變審:迴圈變數曾叫 mark)
刀(複本):K1 main 不記 ⇒ M1 紅
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "bootstrap", HERE):
    sys.path.insert(0, str(_p))
import aib_doctor as D  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


def _fake(status):
    return lambda *a, **k: [{"id": "x", "status": status, "detail": "d", "remedy": ""}]


class Mark(unittest.TestCase):
    def setUp(self):
        self.real = D.run_all

    def tearDown(self):
        D.run_all = self.real

    def _main(self, root, status):
        D.run_all = _fake(status)
        with redirect_stdout(StringIO()):
            return D.main(["--state-root", root, "--json"])

    def test_m1_mark(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "state" / "run-marks" / "doctor.json"
            self.assertEqual(self._main(root, D.FAIL), 1)
            got = json.loads(p.read_text())
            self.assertEqual(sorted(got), ["at", "rc"])
            self.assertEqual(got["rc"], 1)
            self.assertEqual(self._main(root, D.OK), 0)
            self.assertEqual(json.loads(p.read_text())["rc"], 0)
            self.assertEqual(os.listdir(p.parent), ["doctor.json"])

    def test_m2_unwritable(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "state").mkdir()
            (Path(root) / "state" / "run-marks").write_text("x")
            self.assertEqual(self._main(root, D.OK), 0)

    def test_m4_text_output(self):
        with tempfile.TemporaryDirectory() as root:
            D.run_all = _fake(D.FAIL)
            with redirect_stdout(StringIO()):
                self.assertEqual(D.main(["--state-root", root]), 1)
            got = json.loads((Path(root) / "state" / "run-marks" / "doctor.json").read_text())
            self.assertEqual(got["rc"], 1)

    def test_m3_run_all_no_mark(self):
        with tempfile.TemporaryDirectory() as root:
            D.run_all = _fake(D.OK)
            D.run_all(root, str(REPO), [])
            self.assertFalse((Path(root) / "state" / "run-marks").exists())


class Knife(unittest.TestCase):
    def test_k1_no_mark(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/bootstrap/aib_doctor.py", "    _mark_run(args.state_root, rc)\n", "", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Mark.test_m1_mark"], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
