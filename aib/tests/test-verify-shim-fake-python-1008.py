#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""墊片(bash-shim/_shim)算雜湊不用 PATH 上的 python3(10-08,實測無限遞迴炸出 6000 多個行程)。

測試把假的 python3(bash 寫的、裡面又 cat)放在 PATH 最前面 ⇒ 以前:墊片 → 假 python3 → cat → 墊片 … 無限遞迴。
現在兩層:用追蹤器給的真 python(EZ2_VERIFY_PY);墊片自己起的 python 底下再碰到墊片(EZ2_VERIFY_IN_SHIM)就不記。
F1 PATH 上有假 python3、替身裡 cat ⇒ 正常結束、量得到,cat 讀的檔進鑰匙(改了就不沿用)
F2 防重入:EZ2_VERIFY_IN_SHIM=1 底下執行墊片 ⇒ 一筆都不記,照樣執行真的命令
刀(複本;每把只砍一層,另一層還在,不會真的遞迴):K1 改回 PATH 上的 python3 ⇒ F1 紅;K2 拿掉防重入 ⇒ F2 紅
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "tools" / "verify" / "bash-shim"


def _bash_traceable(bash):
    """aib 追蹤 bash 要 4.1 以上(BASH_XTRACEFD);macOS 內建 /bin/bash 是 3.2 ⇒ 照設計判量不到(bash-version-no-xtrace)。"""
    try:
        out = subprocess.run([bash, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=10).stdout.split()
        return (int(out[0]), int(out[1])) >= (4, 1)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


SYS_BASH_OK = _bash_traceable("/bin/bash")     # run.sh 用 /bin/bash 起
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))
import shadow_tracer as st  # noqa: E402

TEST = '''import atexit, os, shutil, subprocess, tempfile
_td = tempfile.mkdtemp(prefix="fakepy-")
atexit.register(shutil.rmtree, _td, True)
os.makedirs(os.path.join(_td, "bin"))
stub = os.path.join(_td, "bin", "python3")
with open(stub, "w") as fh:
    fh.write("#!/bin/bash\\ncat \\"%s/data.txt\\"\\n" % os.getcwd())
os.chmod(stub, 0o755)
env = {"PATH": "%s:/usr/bin:/bin" % os.path.dirname(stub), "HOME": _td, "PY3": stub}
p = subprocess.run(["/bin/bash", "run.sh"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
assert p.returncode == 0 and "D" in p.stdout, (p.returncode, p.stdout, p.stderr)
'''


class FakePython(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "data.txt").write_text("D\n", encoding="utf-8")
        (self.repo / "run.sh").write_text('#!/bin/bash\n"$PY3" -c x\n', encoding="utf-8")
        (self.repo / "tests" / "t_fake.py").write_text(TEST, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        # 關掉 git 背景自動維護(gc / maintenance 會在 commit 後脫離背景寫 .git,10-25 CI tearDown 撞上 Directory not empty)
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", str(self.repo)] + a,
                           check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
        self.trace_dir = Path(self.td.name) / "traces"
        self.trace_dir.mkdir()

    def tearDown(self):
        try:
            self.td.cleanup()
        except OSError:          # 清理撞上還在寫的背景程式:等一下再收，收不乾淨也不判紅(測的不是清理)
            time.sleep(0.5)
            shutil.rmtree(self.td.name, ignore_errors=True)

    def _env(self):
        return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE", "EZ2_VERIFY_PY")}

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

    @unittest.skipUnless(SYS_BASH_OK, "這台的 bash 低於 4.1(macOS 內建 /bin/bash 是 3.2):aib 追蹤不了 bash,照設計判量不到;這格只在 bash ≥ 4.1 驗")
    def test_f1_fake_python_on_path(self):
        row = self._keep(lambda: st.run_one(self.repo, "tests/t_fake.py", environ=self._env(), timeout=90))
        self.assertEqual(row["rc"], 0, row.get("tail"))
        self.assertIs(row["measured"], True, row["why"])
        self.assertTrue(any("data.txt" in str(i) for i in row["inputs"]), "cat 讀的檔要進鑰匙:%s" % row["inputs"][:20])
        (self.repo / "data.txt").write_text("D2\n", encoding="utf-8")
        self.assertIsNone(self._keep(lambda: st.reuse_row(self.repo, "tests/t_fake.py", row, environ=self._env())))

    def test_f2_reentry_guard_records_nothing(self):
        trace = Path(self.td.name) / "t.jsonl"
        trace.write_text("", encoding="utf-8")
        env = dict(self._env(), EZ2_VERIFY_TRACE=str(trace), EZ2_VERIFY_IN_SHIM="1", EZ2_VERIFY_PY=sys.executable)
        p = subprocess.run([str(SHIM / "cat"), str(self.repo / "data.txt")], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual((p.returncode, p.stdout), (0, "D\n"), p.stderr)
        self.assertEqual(trace.read_text(encoding="utf-8"), "", "防重入底下不准記")


class Knives(unittest.TestCase):
    def _red(self, needle, repl, cell):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/bash-shim/_shim", needle, repl, tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), cell], cwd=str(root), env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn(cell.split(".")[-1], set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])

    @unittest.skipUnless(SYS_BASH_OK, "這台的 bash 低於 4.1(macOS 內建 /bin/bash 是 3.2):aib 追蹤不了 bash,照設計判量不到;這格只在 bash ≥ 4.1 驗")
    def test_k1_path_python_reds_f1(self):
        self._red('EZ2_VERIFY_IN_SHIM=1 "${EZ2_VERIFY_PY:-python3}" -', 'EZ2_VERIFY_IN_SHIM=1 python3 -',
                  "FakePython.test_f1_fake_python_on_path")

    def test_k2_no_guard_reds_f2(self):
        self._red(' && [ -z "${EZ2_VERIFY_IN_SHIM:-}" ]; then', '; then', "FakePython.test_f2_reentry_guard_records_nothing")


if __name__ == "__main__":
    unittest.main()
