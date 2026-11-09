#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Go 套件全部 t.Skip 不能當綠(11-09 §G,1ced7;f1fae 定稿派)。

以前 aib 跑 `go test ./pkg` 沒帶 -v:輸出只有一行 `ok pkg`,全部 t.Skip 也是 rc 0 ⇒ 被當成綠。
現在 runner._go_command 帶 -v,test_entry._other_lang_cells 數頂層「--- PASS / FAIL / SKIP」:
全部 SKIP ⇒ 判「沒驗」(rc 4);部分 SKIP ⇒ 照綠、列出跳過格數;一行都讀不到 ⇒ 不判(照原 rc)。

G1 全 SKIP 的 -v 輸出 ⇒ (2, 2) ⇒ _mark_all_skipped 判沒驗
G2 一過一跳 ⇒ (2, 1) ⇒ 照綠
G3 子測試(有縮排)不算頂層:PASS 的父測試底下子測試 SKIP ⇒ (1, 0)
G4 沒帶 -v 的舊輸出(只有 ok 行)⇒ None(不猜)
G5 _go_command 帶 -v
G6(要 go)套件兩支都 t.Skip ⇒ aib test rc 4、印「all 2 cells skipped」
G7(要 go)一過一跳 ⇒ rc 0、印「1 cells skipped」
G8(要 go)同一份再跑一次:先證快取已熱(直接跑同一條 go test -v 有 (cached)),aib 再跑 reused=False、仍是沒驗
刀(複本 + run_guarded):K1 拿掉 -v ⇒ G5 紅;K2 子測試也算(縮排照收)⇒ G3 紅;K3 不數 SKIP ⇒ G1 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "verify"))
import runner  # noqa: E402
import test_entry as TE  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

GO = shutil.which("go") or ("/usr/local/go/bin/go" if os.path.exists("/usr/local/go/bin/go") else None)

ALL_SKIP_V = ("=== RUN   TestA\n    a_test.go:5: needs a database\n--- SKIP: TestA (0.00s)\n"
              "=== RUN   TestB\n    b_test.go:5: needs a database\n--- SKIP: TestB (0.00s)\nPASS\nok  \texample.com/x/calc\t0.2s\n")
MIXED_V = "=== RUN   TestA\n--- PASS: TestA (0.00s)\n=== RUN   TestB\n--- SKIP: TestB (0.00s)\nPASS\nok  \texample.com/x/calc\t0.2s\n"
SUB_V = ("=== RUN   TestP\n=== RUN   TestP/one\n    p_test.go:9: later\n    --- SKIP: TestP/one (0.00s)\n"
         "--- PASS: TestP (0.00s)\nPASS\nok  \texample.com/x/calc\t0.2s\n")
OLD = "ok  \texample.com/x/calc\t0.2s\n"

SKIP_A = 'package calc\n\nimport "testing"\n\nfunc TestA(t *testing.T) { t.Skip("needs a database") }\n'
SKIP_B = 'package calc\n\nimport "testing"\n\nfunc TestB(t *testing.T) { t.Skip("needs a database") }\n'
PASS_B = 'package calc\n\nimport "testing"\n\nfunc TestB(t *testing.T) { if Add(2, 2) != 4 { t.Fatal("bad") } }\n'


def row(out):
    return {"name": "calc/a_test.go", "rc": 0, "output": out}


class Parse(unittest.TestCase):
    def test_g1_all_skip_is_unverified(self):
        self.assertEqual(TE._other_lang_cells(ALL_SKIP_V, "calc/a_test.go"), (2, 2))
        r = TE._mark_all_skipped(row(ALL_SKIP_V))
        self.assertTrue(r.get("unverified"), r)
        self.assertIsNone(r["rc"])

    def test_g2_some_skip_stays_green(self):
        self.assertEqual(TE._other_lang_cells(MIXED_V, "calc/a_test.go"), (2, 1))
        r = TE._mark_all_skipped(row(MIXED_V))
        self.assertFalse(r.get("unverified"), r)
        self.assertEqual(r["rc"], 0)

    def test_g3_subtests_are_not_top_level(self):
        self.assertEqual(TE._other_lang_cells(SUB_V, "calc/a_test.go"), (1, 0))

    def test_g4_old_output_is_not_guessed(self):
        self.assertIsNone(TE._other_lang_cells(OLD, "calc/a_test.go"))
        r = TE._mark_all_skipped(row(OLD))
        self.assertEqual(r["rc"], 0)

    def test_g5_go_test_runs_verbose(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "calc").mkdir()
            cmd = runner._go_command(repo, repo / "calc" / "a_test.go")
        i = cmd.index("test")
        self.assertEqual(cmd[i + 1:], ["-v", "./calc"], cmd)


@unittest.skipUnless(GO, "沒有 go")
class RealGo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "calc").mkdir(parents=True)
        (self.repo / "go.mod").write_text("module example.com/x\n\ngo 1.21\n", encoding="utf-8")
        (self.repo / "calc" / "calc.go").write_text("package calc\n\nfunc Add(a, b int) int { return a + b }\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE", "AIB_TEST_JOBS")}
        self.env.update({"HOME": str(Path(self.td.name) / "home"), "EZ2_AIB_LOCALE": "en", "GIT_AUTHOR_NAME": "t",
                         "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        (Path(self.td.name) / "home").mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _put(self, a, b):
        (self.repo / "calc" / "a_test.go").write_text(a, encoding="utf-8")
        (self.repo / "calc" / "b_test.go").write_text(b, encoding="utf-8")
        for x in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.repo)] + x, check=True, capture_output=True, env=self.env, stdin=subprocess.DEVNULL)

    def _aib(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(self.repo),
                               *extra, "calc/a_test.go", "calc/b_test.go"],
                              cwd=str(self.repo), env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)

    def test_g6_all_skip_package_is_not_green(self):
        self._put(SKIP_A, SKIP_B)
        p = self._aib()
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 4, out[-2000:])
        self.assertIn("all 2 cells skipped", out, out[-2000:])

    def test_g7_some_skip_is_green_and_says_so(self):
        self._put(SKIP_A, PASS_B)
        p = self._aib()
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-2000:])
        self.assertIn("1 cells skipped", out, out[-2000:])

    def test_g8_go_cache_replay_is_still_unverified(self):
        """bb7dc #581:要證明第二次真的是 Go 快取重播,不只是兩次都 rc 4。
        1) aib 跑第一次(rc 4);2) 用 aib 同一份 GOCACHE(runner 在這個環境問 `go env GOCACHE`)直接跑同一條
        `go test -v ./calc` ⇒ 輸出要有 "(cached)",證明快取已熱;3) aib --json 再跑 ⇒ reused 是 False
        (aib 這邊真的叫了 go,不是自己沿用),rc 仍 4、列在 unverified。"""
        self._put(SKIP_A, SKIP_B)
        first = self._aib()
        self.assertEqual(first.returncode, 4, (first.stdout + first.stderr)[-2000:])
        cache = subprocess.run([GO, "env", "GOCACHE"], capture_output=True, text=True, env=self.env,
                               stdin=subprocess.DEVNULL, timeout=60).stdout.strip()
        self.assertTrue(cache, "go env GOCACHE 讀不到")
        direct = subprocess.run(["env", "GOCACHE=" + cache, GO, "test", "-v", "./calc"], cwd=str(self.repo),
                                capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("(cached)", direct.stdout, direct.stdout + direct.stderr)
        self.assertEqual(direct.stdout.count("--- SKIP: "), 2, direct.stdout)
        again = self._aib("--json")
        self.assertEqual(again.returncode, 4, (again.stdout + again.stderr)[-2000:])
        data = json.loads(again.stdout.strip().splitlines()[-1])
        self.assertTrue(data["tests"], data)
        self.assertTrue(all(t["reused"] is False for t in data["tests"]), data["tests"])
        names = {u["name"] for u in data["unverified"]}
        self.assertTrue(names & {"calc/a_test.go", "calc/b_test.go"}, data)


KNIVES = [
    ("K1 拿掉 -v", "tools/verify/runner.py",
     '[go, "test", "-v", pkg]', '[go, "test", pkg]', "Parse.test_g5_go_test_runs_verbose"),
    ("K2 子測試也算", "tools/verify/test_entry.py",
     'top = re.findall(r"^--- (PASS|FAIL|SKIP): ", text, re.M)', 'top = re.findall(r"^\\s*--- (PASS|FAIL|SKIP): ", text, re.M)',
     "Parse.test_g3_subtests_are_not_top_level"),
    ("K3 不數 SKIP", "tools/verify/test_entry.py",
     'return len(top), top.count("SKIP")', 'return len(top), 0', "Parse.test_g1_all_skip_is_unverified"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=60, timeout=240)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out[-3000:]))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out[-3000:]))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out[-3000:]))


if __name__ == "__main__":
    unittest.main()
