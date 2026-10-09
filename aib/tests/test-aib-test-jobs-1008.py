#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib test --all 分份同時跑(10-08;attrs 實測 37.7 秒 → 15.1 秒,click / attrs 第一次比 pytest 慢 2~4 倍的主因)。

J1 預設份數:--all ⇒ min(4, 核心數, 支數);不是 --all、--json、--resume(推前檢查自己分份)⇒ 1;AIB_TEST_JOBS 指定;--jobs 指定
J2 真跑:4 支(1 支紅)分 2 份 ⇒ 每支都有結果、照原本順序、紅的照紅(結束碼 1、輸出印出來)、只一份總結;
   第二輪綠的沿用(紀錄有寫進去)、紅的照跑;開頭「影子續跑」那行只印一次
J3 有一份沒交回結果 ⇒ 不准當綠(結束碼 1)
刀(複本):K1 少一份也當全綠 ⇒ J3 紅
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))
import test_entry as E  # noqa: E402

GREEN = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(open('src.py').read(), 'X = 1\\n')\n\n" \
        "if __name__ == '__main__':\n    unittest.main()\n"
RED = "import unittest\n\nclass T(unittest.TestCase):\n    def test_red(self):\n        self.fail('RED-MARKER')\n\n" \
      "if __name__ == '__main__':\n    unittest.main()\n"


class DefaultJobs(unittest.TestCase):
    def _a(self, **kw):
        return SimpleNamespace(**dict({"all": True, "json": False, "resume": None}, **kw))

    def test_j1_defaults(self):
        old = os.environ.pop("AIB_TEST_JOBS", None)
        try:
            want = max(1, min(E.JOBS_MAX, os.cpu_count() or 1, 10))
            self.assertEqual(E._default_jobs(self._a(), ["t"] * 10), want)
            self.assertEqual(E._default_jobs(self._a(), ["t"]), 1)
            self.assertEqual(E._default_jobs(self._a(all=False), ["t"] * 10), 1)
            self.assertEqual(E._default_jobs(self._a(json=True), ["t"] * 10), 1)
            self.assertEqual(E._default_jobs(self._a(resume="pushgate"), ["t"] * 10), 1)
            os.environ["AIB_TEST_JOBS"] = "3"
            self.assertEqual(E._default_jobs(self._a(all=False), ["t"] * 10), 3)
        finally:
            os.environ.pop("AIB_TEST_JOBS", None)
            if old is not None:
                os.environ["AIB_TEST_JOBS"] = old


class Sharded(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "src.py").write_text("X = 1\n", encoding="utf-8")
        for n in ("test_a.py", "test_b.py", "test_c.py"):
            (self.repo / "tests" / n).write_text(GREEN, encoding="utf-8")
        (self.repo / "tests" / "test_red.py").write_text(RED, encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE", "AIB_TEST_JOBS", "AIB_TEST_SHARD")}
        self.env.update({"HOME": str(Path(self.td.name) / "home"), "EZ2_AIB_LOCALE": "en", "GIT_AUTHOR_NAME": "t",
                         "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        (Path(self.td.name) / "home").mkdir()
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.repo)] + a, check=True, capture_output=True, env=self.env, stdin=subprocess.DEVNULL)

    def tearDown(self):
        self.td.cleanup()

    def _aib(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(self.repo), *args],
                              cwd=str(self.repo), env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)

    def test_j2_two_shards(self):
        p = self._aib("--all", "--jobs", "2")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out[-2000:])
        self.assertEqual(len(re.findall(r"^4 tests: 3 green, 1 red", p.stdout, re.M)), 1, p.stdout[-2000:])
        self.assertIn("RED-MARKER", out)
        self.assertEqual(out.count("Shadow resume: loaded and enabled"), 1, out[-3000:])
        rows = re.findall(r"^[·✓] (tests/test_\w+\.py)", p.stdout, re.M)
        self.assertEqual(rows, ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_red.py"], p.stdout[-2000:])
        p2 = self._aib("--all", "--jobs", "2")
        self.assertIn("reused 3 / ran 1", p2.stdout + p2.stderr, (p2.stdout + p2.stderr)[-2000:])
        self.assertEqual(p2.returncode, 1)

    def test_j3_lost_shard_is_not_green(self):
        class Gone:
            def __init__(self, *a, **k):
                pass

            def wait(self):
                return 0
        orig = E.subprocess.Popen
        E.subprocess.Popen = Gone
        try:
            a = SimpleNamespace(strict=False, fresh=False, timeout=None)
            res = E.run_sharded(self.repo, ["tests/test_a.py", "tests/test_b.py"], 2, a)
        finally:
            E.subprocess.Popen = orig
        self.assertEqual(res["rc"], 1, res)
        self.assertEqual(res["tests"], [])


class Knives(unittest.TestCase):
    def test_k1_lost_counted_green_reds_j3(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/test_entry.py", "    lost = len(tests) < len(names)", "    lost = False",
                        tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Sharded.test_j3_lost_shard_is_not_green"],
                               cwd=str(root), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_j3_lost_shard_is_not_green", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
