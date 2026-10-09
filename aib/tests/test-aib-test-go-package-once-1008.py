#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""同一個 Go 套件一批只跑一次 go test(10-08,pflag 實測:32 個測試檔同一套件 ⇒ 以前跑 32 次,9.5 秒 → 1.4 秒)。

go test 以套件為單位:一個套件裡有幾個 _test.go,以前 aib 就整包跑幾次。
O1 一個套件兩個測試檔 ⇒ 實跑 1 次,兩支都有結果、都綠;第二支標「同一個 Go 套件」
O2 套件裡有一支會紅 ⇒ 兩支都紅(同一個 go test),紅的輸出只印一次
O3 分份同時跑(--jobs 2)⇒ 同一套件分到同一份,照樣只跑 1 次
O4 go_package_key:_test.go ⇒ 資料夾;倉根 ⇒ ".";不是 Go ⇒ None
刀(複本):K1 不沿用同套件的結果 ⇒ O1 紅;K2 分份不照套件分組 ⇒ O3 紅
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
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))
import runner  # noqa: E402

# 測試環境的 PATH 是白名單(可能沒有 go);執行器自己也會找這個固定位置(runner._go_command)
GO = shutil.which("go") or ("/usr/local/go/bin/go" if os.path.exists("/usr/local/go/bin/go") else None)

A = 'package calc\n\nimport "testing"\n\nfunc TestA(t *testing.T) { if Add(1, 2) != 3 { t.Fatal("bad") } }\n'
B = 'package calc\n\nimport "testing"\n\nfunc TestB(t *testing.T) { if Add(2, 2) != 4 { t.Fatal("bad") } }\n'
BAD = 'package calc\n\nimport "testing"\n\nfunc TestBad(t *testing.T) { t.Fatal("GO-RED-MARKER") }\n'


class Key(unittest.TestCase):
    def test_o4_key(self):
        self.assertEqual(runner.go_package_key("pkg/sub/a_test.go"), "pkg/sub")
        self.assertEqual(runner.go_package_key("a_test.go"), ".")
        self.assertIsNone(runner.go_package_key("tests/test_a.py"))


@unittest.skipUnless(GO, "沒有 go")
class GoOnce(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "calc").mkdir(parents=True)
        (self.repo / "go.mod").write_text("module example.com/x\n\ngo 1.21\n", encoding="utf-8")
        (self.repo / "calc" / "calc.go").write_text("package calc\n\nfunc Add(a, b int) int { return a + b }\n", encoding="utf-8")
        (self.repo / "calc" / "a_test.go").write_text(A, encoding="utf-8")
        (self.repo / "calc" / "b_test.go").write_text(B, encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE", "AIB_TEST_JOBS")}
        self.env.update({"HOME": str(Path(self.td.name) / "home"), "EZ2_AIB_LOCALE": "en", "GIT_AUTHOR_NAME": "t",
                         "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        (Path(self.td.name) / "home").mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _commit(self):
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.repo)] + a, check=True, capture_output=True, env=self.env, stdin=subprocess.DEVNULL)

    def _aib(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(self.repo), *args],
                              cwd=str(self.repo), env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)

    def test_o1_one_run_per_package(self):
        self._commit()
        p = self._aib("calc/a_test.go", "calc/b_test.go")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-2000:])
        self.assertIn("2 tests: 2 green, 0 red", p.stdout, out[-2000:])
        self.assertIn("ran 1", out, out[-2000:])
        self.assertIn("same Go package", out)

    def test_o2_red_package_both_red_output_once(self):
        (self.repo / "calc" / "b_test.go").write_text(BAD, encoding="utf-8")
        self._commit()
        p = self._aib("calc/a_test.go", "calc/b_test.go")
        self.assertEqual(p.returncode, 1)
        self.assertIn("2 tests: 0 green, 2 red", p.stdout, (p.stdout + p.stderr)[-2000:])
        self.assertEqual(p.stderr.count("GO-RED-MARKER") >= 1 and len(re.findall(r"^--- .*calc/\w+_test\.go", p.stderr, re.M)), 1,
                         p.stderr[-3000:])

    def test_o3_sharded_same_package_same_shard(self):
        for i in range(3):
            (self.repo / "calc" / ("c%d_test.go" % i)).write_text(
                'package calc\n\nimport "testing"\n\nfunc TestC%d(t *testing.T) {}\n' % i, encoding="utf-8")
        self._commit()
        p = self._aib("--all", "--jobs", "2")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-2000:])
        self.assertIn("ran 1", out, out[-2000:])


class Knives(unittest.TestCase):
    def _red(self, rel, needle, repl, cell):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), cell], cwd=str(root), env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
        self.assertIn(cell.split(".")[-1], set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])

    @unittest.skipUnless(GO, "沒有 go")
    def test_k1_no_reuse_reds_o1(self):
        self._red("tools/verify/test_entry.py", "            if gk is not None and gk in go_done:\n", "            if False:\n",
                  "GoOnce.test_o1_one_run_per_package")

    @unittest.skipUnless(GO, "沒有 go")
    def test_k2_no_grouping_reds_o3(self):
        self._red("tools/verify/test_entry.py", '        units.setdefault(runner.go_package_key(t) or ("\\0" + t), []).append(t)\n',
                  '        units.setdefault("\\0" + t, []).append(t)\n', "GoOnce.test_o3_sharded_same_package_same_shard")


if __name__ == "__main__":
    unittest.main()
