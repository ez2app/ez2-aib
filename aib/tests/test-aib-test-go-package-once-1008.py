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
import json
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
# 11-09 bb7dc #565:被 build tag 排除的測試檔 ⇒ go test 回「[no test files]」、rc 0 ⇒ 這支「沒驗」(真 Go 走得到)
NEVER = '//go:build never\n\npackage calc\n\nimport "testing"\n\nfunc TestN%d(t *testing.T) {}\n'
# 三條路(追蹤 / 小批 / 自檢失敗退回)直接呼叫 run_tests,印每列 + ran + rc;退回那條把自檢結果先設成失敗
PATHS = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import test_entry
mode, repo, tests = sys.argv[2], sys.argv[3], sys.argv[4:]
if mode == "fallback":
    test_entry._GATE["res"] = {"ok": False, "stage": "setup", "reason": "forced by test"}
res = test_entry.run_tests(repo, tests, trace=(mode != "small"))
rows = [[r["name"], r.get("rc"), bool(r.get("unverified")), r.get("same_package")] for r in res["tests"]]
print(json.dumps({"state": res["state"], "ran": res["ran"], "rc": res["rc"], "rows": rows}))
'''
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
        self.env.update({"AIB_TEST_TRACE_MIN": "0",   # 11-09 #565:o1–o3 驗的是追蹤那條(兩支也要追蹤);小批另有 o5
                         "HOME": str(Path(self.td.name) / "home"), "EZ2_AIB_LOCALE": "en", "GIT_AUTHOR_NAME": "t",
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

    def test_o5_small_batch_also_runs_a_package_once(self):
        """11-09 #565:不到門檻(小批、不開追蹤)也要同一個 Go 套件只跑一次,紅綠跟那次一樣。"""
        self.env.pop("AIB_TEST_TRACE_MIN")
        (self.repo / "calc" / "b_test.go").write_text(BAD, encoding="utf-8")
        self._commit()
        p = self._aib("calc/a_test.go", "calc/b_test.go")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out[-2000:])
        self.assertIn("2 tests: 0 green, 2 red", p.stdout, out[-2000:])
        self.assertIn("same Go package", out, out[-2000:])
        self.assertNotIn("Shadow resume: enabled", out, out[-2000:])
        self.assertEqual(len(re.findall(r"^--- .*calc/\w+_test\.go", p.stderr, re.M)), 1, p.stderr[-3000:])

    def test_o6_traced_and_small_behave_the_same(self):
        """11-09 a63ab:同一批(一綠一紅、同一個 Go 套件)開追蹤跑一次、不開追蹤跑一次 ⇒ 結束碼、合計那行、
        「同一個 Go 套件」列、go 失敗輸出印幾次都一樣。規則只寫一處(GoOnce),兩條路不准分岔。"""
        (self.repo / "calc" / "b_test.go").write_text(BAD, encoding="utf-8")
        self._commit()

        def look(trace_min):
            self.env["AIB_TEST_TRACE_MIN"] = trace_min
            p = self._aib("calc/a_test.go", "calc/b_test.go")
            out = p.stdout + p.stderr
            total = re.findall(r"\d+ tests: \d+ green, \d+ red", p.stdout)
            return (p.returncode, total, len(re.findall(r"same Go package", out)),
                    len(re.findall(r"^--- .*calc/\w+_test\.go", p.stderr, re.M))), out

        traced, out_t = look("0")
        small, out_s = look("100")
        self.assertIn("Shadow resume: enabled", out_t, out_t[-1500:])
        self.assertNotIn("Shadow resume: enabled", out_s, out_s[-1500:])
        self.assertEqual(traced, small, "開追蹤:\n%s\n---- 不開:\n%s" % (out_t[-1500:], out_s[-1500:]))
        self.assertEqual(traced[0], 1)

    def test_o7_unverified_package_counts_once_on_every_path(self):
        """11-09 bb7dc #565:同一套件兩檔都被 build tag 排除(真 go test:[no test files])⇒ 兩列都「沒驗」、第二列是同套件;
        追蹤 / 小批 / 退回三條路:逐列一樣、實跑 = 1(只起了一次 go test,不是 0、不是負數)、整批 rc 4(沒驗不能當綠)。"""
        for i, f in enumerate(("a_test.go", "b_test.go")):
            (self.repo / "calc" / f).write_text(NEVER % i, encoding="utf-8")
        self._commit()
        names = ["calc/a_test.go", "calc/b_test.go"]
        got = {}
        for mode in ("traced", "small", "fallback"):
            p = subprocess.run([sys.executable, "-c", PATHS, str(ROOT / "tools" / "verify"), mode, str(self.repo)] + names,
                               cwd=str(self.repo), env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               timeout=600)
            line = (p.stdout.strip().splitlines() or ["{}"])[-1]
            self.assertEqual(p.returncode, 0, mode + ":" + p.stdout[-1500:] + p.stderr[-1500:])
            got[mode] = json.loads(line)
        want_rows = [["calc/a_test.go", None, True, None], ["calc/b_test.go", None, True, "calc/a_test.go"]]
        for mode, r in got.items():
            self.assertEqual((r["rows"], r["ran"], r["rc"]), (want_rows, 1, 4), "%s: %s" % (mode, r))
        self.assertEqual([got[m]["state"] for m in ("traced", "small", "fallback")], ["enabled", "small", "fallback"], got)


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
        self._red("tools/verify/test_entry.py", "\n            row = go_once.same(name)\n", "\n            row = None\n",
                  "GoOnce.test_o1_one_run_per_package")

    @unittest.skipUnless(GO, "沒有 go")
    def test_k2_no_grouping_reds_o3(self):
        self._red("tools/verify/test_entry.py", '        units.setdefault(runner.go_package_key(t) or ("\\0" + t), []).append(t)\n',
                  '        units.setdefault("\\0" + t, []).append(t)\n', "GoOnce.test_o3_sharded_same_package_same_shard")

    @unittest.skipUnless(GO, "沒有 go")
    def test_k3_small_batch_without_grouping_reds_o5(self):
        self._red("tools/verify/test_entry.py", "\n                row = go_once.same(name)\n", "\n                row = None\n",
                  "GoOnce.test_o5_small_batch_also_runs_a_package_once")

    @unittest.skipUnless(GO, "沒有 go")
    def test_k4_small_path_diverges_reds_o6(self):
        # 只有不追蹤那條不合併 ⇒ 兩條路行為不一樣 ⇒ o6 必紅
        self._red("tools/verify/test_entry.py", "\n                row = go_once.same(name)\n", "\n                row = None\n",
                  "GoOnce.test_o6_traced_and_small_behave_the_same")

    @unittest.skipUnless(GO, "沒有 go")
    def test_k5_shared_rule_broken_reds_o1(self):
        # 共用規則本身壞掉(永遠不沿用)⇒ 追蹤那條的 o1 紅(o5 同理)
        self._red("tools/verify/test_entry.py", "        if first is None:\n            return None\n", "        return None\n",
                  "GoOnce.test_o1_one_run_per_package")

    @unittest.skipUnless(GO, "沒有 go")
    def test_k6_old_ran_formula_reds_o7(self):
        # bb7dc 推導的那個公式:首列沒驗時同套件列被扣兩次 ⇒ 小批 / 退回 ran = -1
        self._red("tools/verify/test_entry.py", '"ran": executed, "reused": 0,',
                  '"ran": len(results) - len(unv) - sum(1 for r in results if r.get("same_package")), "reused": 0,',
                  "GoOnce.test_o7_unverified_package_counts_once_on_every_path")


if __name__ == "__main__":
    unittest.main()
