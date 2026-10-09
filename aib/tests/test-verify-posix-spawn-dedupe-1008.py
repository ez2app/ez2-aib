#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Popen 走 posix_spawn 時同一個子行程只記一筆(10-08,tools/verify/sitecustomize_audit.py)。

10-08 推第 35 包時 test-verify-popen-inject-v6-1006 在雲端 Linux 紅:Python 3.13 起 Linux 上的 Popen 改用 posix_spawn
(glibc 有 closefrom)⇒ 稽核事件先有 subprocess.Popen、再有 os.posix_spawn,同一個子行程記成兩筆 argv ⇒ 分析端期望
兩個子行程的 inherit,只看到一個 ⇒ 整列判「量不到」(python-child-no-inherit:2<3),每次都得實跑。Mac 預設不走這條路。
P1 Popen 強制走 posix_spawn(各平台都能測)⇒ 一個子行程一筆 argv、整列量得到
P2 Popen 之外直接呼叫 os.posix_spawn ⇒ argv 照記(去重只在 Popen 裡面、argv 一樣時)
P3 測試的子行程再用 Popen 起孫行程(那一層沒有 Popen 包裝)⇒ 照樣只記一筆(10-08 晚 b18b 5<6 實測)
刀(複本):K1 不去重 ⇒ P1、P3 紅
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

CHILD = '''import os
print("TRACE=" + str(bool(os.environ.get("EZ2_VERIFY_TRACE"))))
open("data.txt").read()
'''
TESTS = {
    "t_popen_spawn.py": '''import subprocess, sys
subprocess._USE_POSIX_SPAWN = True
p = subprocess.run([sys.executable, "child.py"], capture_output=True, text=True, close_fds=False)
print(p.stdout, end=""); assert p.returncode == 0, p.stderr
''',
    "t_nested.py": '''import subprocess, sys
p = subprocess.run([sys.executable, "mid.py"], capture_output=True, text=True, cwd=".")
print(p.stdout, end=""); assert p.returncode == 0, p.stderr
''',
    "t_direct_spawn.py": '''import os, sys
pid = os.posix_spawn(sys.executable, [sys.executable, "child.py"], os.environ)
_, st = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(st) == 0
''',
}


class Dedupe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._td.name) / "repo"
        (cls.repo / "tests").mkdir(parents=True)
        (cls.repo / "child.py").write_text(CHILD, encoding="utf-8")
        (cls.repo / "data.txt").write_text("d\n", encoding="utf-8")
        (cls.repo / "mid.py").write_text("import subprocess, sys\nsubprocess._USE_POSIX_SPAWN = True\n"
                                         "p = subprocess.run([sys.executable, 'child.py'], capture_output=True, text=True, close_fds=False)\n"
                                         "print(p.stdout, end='')\nassert p.returncode == 0, p.stderr\n", encoding="utf-8")
        for n, body in TESTS.items():
            (cls.repo / "tests" / n).write_text(body, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(cls.repo)] + a, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
        cls.trace_dir = Path(cls._td.name) / "traces"
        cls.trace_dir.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _run(self, name):
        old = os.environ.get("EZ2_VERIFY_TRACE_DIR")
        os.environ["EZ2_VERIFY_TRACE_DIR"] = str(self.trace_dir)
        try:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            row = st.run_one(self.repo, "tests/" + name, environ=env, timeout=60)
        finally:
            if old is None:
                os.environ.pop("EZ2_VERIFY_TRACE_DIR", None)
            else:
                os.environ["EZ2_VERIFY_TRACE_DIR"] = old
        self.assertEqual(row["rc"], 0, row.get("tail"))
        return row

    def test_p1_popen_via_posix_spawn_counted_once(self):
        row = self._run("t_popen_spawn.py")
        self.assertFalse(any(w.startswith("python-child") for w in row["why"]), row["why"])
        self.assertIs(row["measured"], True, row["why"])
        self.assertTrue(any("data.txt" in str(i) for i in row["inputs"]), "子行程讀的檔要進鑰匙")

    def test_p3_grandchild_counted_once(self):
        row = self._run("t_nested.py")
        self.assertFalse(any(w.startswith("python-child") for w in row["why"]), row["why"])
        self.assertIs(row["measured"], True, row["why"])

    def test_p2_direct_posix_spawn_still_recorded(self):
        row = self._run("t_direct_spawn.py")
        self.assertTrue(any(str(i).startswith("argv:") and "child.py" in str(i) for i in row["inputs"]), row["inputs"])
        # 直接 posix_spawn 交 os.environ(不是 dict)⇒ 分析端本來就保守判量不到;這裡只管「沒被去重吃掉」


class Knives(unittest.TestCase):
    def test_k1_no_dedupe_reds_p1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/sitecustomize_audit.py",
                        "_after_popen is not None and _after_popen == argv:\n", "False:\n", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Dedupe.test_p1_popen_via_posix_spawn_counted_once"],
                               cwd=str(root), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_p1_popen_via_posix_spawn_counted_once",
                      set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
