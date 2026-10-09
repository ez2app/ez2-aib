#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""hypothesis 的範例資料庫(專案根 .hypothesis/)不算測試的輸入(10-08,attrs 實測)。

hypothesis 每跑一次就在 .hypothesis/ 寫新的範例、再讀回來 ⇒ 以前每輪輸入都變 ⇒ 用 hypothesis 的測試永遠沿用不了
(attrs:什麼都沒改,3 支每次重跑)。hypothesis 只在測試失敗時記下失敗例,紅的本來就不沿用 ⇒ 不算輸入不會藏紅。
H1 只有 .hypothesis/ 變了(新範例檔、目錄名單變長)⇒ 第二輪可以沿用
H2 測試讀的程式碼改了 ⇒ 照樣不沿用(不准藏紅)
H3 鉤子端與分析端同一組名字(目錄名單雜湊兩邊對得上)
刀(複本):K1 分析端拿掉 .hypothesis ⇒ H1 紅
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

TEST = '''import os, uuid
src = open("src.py").read()
os.makedirs(".hypothesis/examples", exist_ok=True)
with open(os.path.join(".hypothesis/examples", uuid.uuid4().hex), "w") as fh:
    fh.write(uuid.uuid4().hex)
seen = 0
for name in sorted(os.listdir(".hypothesis/examples")):
    seen += len(open(os.path.join(".hypothesis/examples", name)).read())
assert "VALUE = 1" in src
print("OK", seen > 0)
'''


class HypothesisState(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "tests" / "t_hyp.py").write_text(TEST, encoding="utf-8")
        (self.repo / ".gitignore").write_text(".hypothesis/\n", encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.repo)] + a, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
        self.trace_dir = Path(self.td.name) / "traces"
        self.trace_dir.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _env(self):
        return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}

    def _with_trace_dir(self, fn):
        old = os.environ.get("EZ2_VERIFY_TRACE_DIR")
        os.environ["EZ2_VERIFY_TRACE_DIR"] = str(self.trace_dir)
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("EZ2_VERIFY_TRACE_DIR", None)
            else:
                os.environ["EZ2_VERIFY_TRACE_DIR"] = old

    def _run(self):
        row = self._with_trace_dir(lambda: st.run_one(self.repo, "tests/t_hyp.py", environ=self._env(), timeout=60))
        self.assertEqual(row["rc"], 0, row.get("tail"))
        self.assertIs(row["measured"], True, row["why"])
        return row

    def _reuse(self, row):
        return self._with_trace_dir(lambda: st.reuse_row(self.repo, "tests/t_hyp.py", row, environ=self._env()))

    def test_h1_only_hypothesis_changed_reuses(self):
        row = self._run()
        self.assertFalse([i for i in row["inputs"] if ".hypothesis" in str(i)], "不該有 .hypothesis 底下的輸入")
        self._run()      # 第二輪又寫了一個新範例檔(就像 hypothesis)
        self.assertIsNotNone(self._reuse(row), "只有 .hypothesis/ 變了,應該沿用")

    def test_h2_source_change_still_reruns(self):
        row = self._run()
        (self.repo / "src.py").write_text("VALUE = 1\n# changed\n", encoding="utf-8")
        self.assertIsNone(self._reuse(row), "程式碼改了一定要重跑")

    def test_h3_hook_and_analysis_share_names(self):
        self.assertIn(".hypothesis", st.DERIVED_DIRNAMES)
        import ast
        hook = (ROOT / "tools" / "verify" / "sitecustomize_audit.py").read_text(encoding="utf-8")
        hook_dirs = next(ast.literal_eval(n.value) for n in ast.parse(hook).body
                         if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TOOL_CACHE_DIRS")
        self.assertEqual(tuple(hook_dirs), st.TOOL_CACHE_DIRS, "鉤子端與分析端同一組")
        self.assertIn("_DERIVED_DIRS = (\"__pycache__\", \".git\", \"node_modules\") + TOOL_CACHE_DIRS", hook)
        self.assertIn("and n not in TOOL_CACHE_DIRS", hook)
        self.assertIn("_PROBE_SKIP_PARTS = frozenset((\"__pycache__\", \".git\") + TOOL_CACHE_DIRS)", hook)
        node = (ROOT / "tools" / "verify" / "node-shim" / "audit.cjs").read_text(encoding="utf-8")
        for d in st.TOOL_CACHE_DIRS:
            self.assertIn("'%s'" % d, node)


class Knives(unittest.TestCase):
    def test_k1_analysis_without_hypothesis_reds_h1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/shadow_tracer.py", 'DERIVED_DIRNAMES = ("__pycache__", ".git") + TOOL_CACHE_DIRS\n',
                        'DERIVED_DIRNAMES = ("__pycache__", ".git")\n', tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "HypothesisState.test_h1_only_hypothesis_changed_reuses"],
                               cwd=str(root), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_h1_only_hypothesis_changed_reuses", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
