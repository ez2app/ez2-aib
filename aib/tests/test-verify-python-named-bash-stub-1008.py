#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""名字叫 python3、其實是 bash 寫的替身(測試用 EZ2_PY3 指過去的假 python3)不當 Python 子行程(10-08,我們倉 46 支只卡這類)。

以前:argv0 叫 python3 ⇒ 判成 Python 子行程 ⇒ 期待 Python 的 inherit,替身卻以 bash 身分回報 ⇒ 整列量不到(nvidia-cli 1<3)。
S1 Python Popen 直接執行替身 ⇒ 量得到;替身讀的檔進鑰匙(改了就不沿用)
S2 bash 裡執行替身 ⇒ 量得到
S3 真的 Python 子行程沒回報(洗掉環境)⇒ 照樣量不到(不准把漏追藏起來)
刀(複本):K1 拿掉 bash 那條 ⇒ S2 紅(Python Popen 直接執行不會誤判:鉤子當下讀得到替身的 #!)
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

MAKE_STUB = '''import atexit, os, shutil, tempfile
_td = tempfile.mkdtemp(prefix="fakebin-")          # 跟真的測試一樣:替身放暫存、測完就刪 ⇒ 分析時讀不到它的 #! 那一行
atexit.register(shutil.rmtree, _td, True)
stub = os.path.join(_td, "python3")
with open(stub, "w") as fh:
    fh.write("#!/bin/bash\\ncat \\"%s/data.txt\\"\\n" % os.getcwd())
os.chmod(stub, 0o755)
'''
TESTS = {
    "t_popen.py": MAKE_STUB + '''import subprocess
p = subprocess.run([stub], capture_output=True, text=True)
assert p.returncode == 0 and "D" in p.stdout, (p.returncode, p.stdout, p.stderr)
''',
    "t_bash.py": '''import atexit, os, shutil, subprocess, tempfile
_td = tempfile.mkdtemp(prefix="fakepy-")          # 跟 nvidia-cli 同形狀:替身放暫存(測完就刪)、全新環境、PATH 最前面
atexit.register(shutil.rmtree, _td, True)
os.makedirs(os.path.join(_td, "bin"))
stub = os.path.join(_td, "bin", "python3")
with open(stub, "w") as fh:
    fh.write("#!/bin/bash\\nprintf 'stub:%s' \\"$*\\"\\n")
os.chmod(stub, 0o755)
env = {"PATH": "%s:/usr/bin:/bin" % os.path.dirname(stub), "HOME": _td, "PY3": stub}
p = subprocess.run(["/bin/bash", "run.sh"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
assert p.returncode == 0 and "got:stub:-c x" in p.stdout, (p.returncode, p.stdout, p.stderr)
''',
    "t_real_untraced.py": '''import subprocess, sys
p = subprocess.run([sys.executable, "-I", "-c", "print(open('data.txt').read())"], capture_output=True, text=True)   # -I:不載任何鉤子
assert p.returncode == 0
''',
}


class Stub(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "data.txt").write_text("D\n", encoding="utf-8")
        (self.repo / "run.sh").write_text('#!/bin/bash\nR="$("$PY3" -c x)"\necho "got:$R"\n', encoding="utf-8")
        for n, body in TESTS.items():
            (self.repo / "tests" / n).write_text(body, encoding="utf-8")
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
        return row

    def test_s1_popen_stub_measured(self):
        row = self._run("t_popen.py")
        self.assertIs(row["measured"], True, row["why"])
        (self.repo / "data.txt").write_text("D2\n", encoding="utf-8")
        self.assertIsNone(self._keep(lambda: st.reuse_row(self.repo, "tests/t_popen.py", row, environ=self._env())),
                          "替身讀的檔改了 ⇒ 一定重跑")

    def test_s2_bash_stub_measured(self):
        row = self._run("t_bash.py")
        self.assertIs(row["measured"], True, row["why"])

    def test_s3_real_untraced_python_still_unmeasured(self):
        row = self._run("t_real_untraced.py")
        self.assertIsNot(row["measured"], True, row["why"])


class Knives(unittest.TestCase):
    def test_k1_no_bash_rule_reds_s2(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/shadow_tracer.py",
                        '            if kind == "python" and w0.startswith("/") and w0 in bash_argv0s:\n',
                        "            if False:\n", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Stub.test_s2_bash_stub_measured"], cwd=str(root),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_s2_bash_stub_measured", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
