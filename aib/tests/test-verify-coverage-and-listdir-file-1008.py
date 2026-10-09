#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""pytest-cov(addopts 帶 --cov)的專案能沿用(10-08 實測:以前每次必重跑)。兩個原因、兩處修:

C1 coverage 的資料檔(.coverage、.coverage.<主機>.pid<n>.<亂碼>)每跑一次就變 ⇒ 不是輸入;第二輪沿用
C2 設定檔 .coveragerc 是真的輸入 ⇒ 改了就不沿用(不准一起排掉)
C3 對一個「檔案」os.listdir(NotADirectoryError,coverage 找虛擬環境設定時會這樣)⇒ 記成「是檔案」不是「不存在」;第二輪沿用
C4 is_coverage_data:.coverage / .coverage.x ⇒ 是;.coveragerc / .coveragerc.toml / coverage.xml ⇒ 不是
刀(複本):K1 資料檔照算輸入 ⇒ C1 紅;K2 列檔案照記不存在 ⇒ C3 紅
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))
import shadow_tracer as st  # noqa: E402

COV = '''import os, uuid
for n in os.listdir("."):
    if n.startswith(".coverage") and not n.startswith(".coveragerc"):
        open(n).read()
with open(".coverage", "w") as fh:
    fh.write(uuid.uuid4().hex)
with open(".coverage.host.pid1.%s" % uuid.uuid4().hex[:8], "w") as fh:
    fh.write("x")
if os.path.exists(".coveragerc"):
    open(".coveragerc").read()
'''
LISTFILE = '''import os
try:
    os.listdir("calc.py")
except NotADirectoryError:
    pass
assert open("calc.py").read()
'''


class Rules(unittest.TestCase):
    def test_c4_names(self):
        for n in (".coverage", ".coverage.vm.pid12.XEMBOqpx"):
            self.assertTrue(st.is_coverage_data(n), n)
        for n in (".coveragerc", ".coveragerc.toml", "coverage.xml", "coverage"):
            self.assertFalse(st.is_coverage_data(n), n)


class Reuse(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calc.py").write_text("X = 1\n", encoding="utf-8")
        (self.repo / ".coveragerc").write_text("[run]\nbranch = True\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".coverage\n.coverage.*\n", encoding="utf-8")
        (self.repo / "tests" / "t_cov.py").write_text(COV, encoding="utf-8")
        (self.repo / "tests" / "t_listfile.py").write_text(LISTFILE, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.repo)] + a, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
        self.trace_dir = Path(self.td.name) / "traces"
        self.trace_dir.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _env(self):
        return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}

    def _keep(self, fn):
        old = os.environ.get("EZ2_VERIFY_TRACE_DIR")
        os.environ["EZ2_VERIFY_TRACE_DIR"] = str(self.trace_dir)
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("EZ2_VERIFY_TRACE_DIR", None)
            else:
                os.environ["EZ2_VERIFY_TRACE_DIR"] = old

    def _run(self, name):
        row = self._keep(lambda: st.run_one(self.repo, "tests/" + name, environ=self._env(), timeout=60))
        self.assertEqual(row["rc"], 0, row.get("tail"))
        self.assertIs(row["measured"], True, row["why"])
        return row

    def _reuse(self, name, row):
        return self._keep(lambda: st.reuse_row(self.repo, "tests/" + name, row, environ=self._env()))

    def test_c1_coverage_data_not_input(self):
        row = self._run("t_cov.py")
        self._run("t_cov.py")                     # 第二輪又寫了新的資料檔(就像 pytest-cov)
        self.assertIsNotNone(self._reuse("t_cov.py", row), "只有 coverage 資料檔變了,應該沿用")

    def test_c2_coveragerc_still_input(self):
        row = self._run("t_cov.py")
        (self.repo / ".coveragerc").write_text("[run]\nbranch = False\n", encoding="utf-8")
        self.assertIsNone(self._reuse("t_cov.py", row), ".coveragerc 改了一定要重跑")

    def test_c3_listdir_on_file_reuses(self):
        row = self._run("t_listfile.py")
        self.assertFalse([i for i in row["inputs"] if str(i).startswith("missing:") and "calc.py" in str(i)], row["inputs"])
        self.assertIsNotNone(self._reuse("t_listfile.py", row))
        (self.repo / "calc.py").write_text("X = 2\n", encoding="utf-8")
        self.assertIsNone(self._reuse("t_listfile.py", row), "calc.py 改了一定要重跑")


class Knives(unittest.TestCase):
    def _red(self, rel, needle, repl, cell):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), cell], cwd=str(root), env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn(cell.split(".")[-1], set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])

    def test_k1_coverage_counted_reds_c1(self):
        self._red("tools/verify/shadow_tracer.py",
                  '    return base == ".coverage" or (base.startswith(".coverage.") and not base.startswith(".coveragerc"))\n',
                  "    return False\n", "Reuse.test_c1_coverage_data_not_input")

    def test_k2_listdir_file_missing_reds_c3(self):
        self._red("tools/verify/sitecustomize_audit.py", "                    _m = _real_stat(path).st_mode\n",
                  "                    raise OSError()\n", "Reuse.test_c3_listdir_on_file_reuses")


if __name__ == "__main__":
    unittest.main()
