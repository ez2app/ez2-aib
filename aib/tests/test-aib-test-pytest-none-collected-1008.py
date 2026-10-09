#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""pytest 結束碼 5(一支都沒收集到,全部被 -m / addopts 排除)⇒「沒驗」,不是紅(10-08,packaging 實測 31 支被當紅 20 支)。

N1 _mark_all_skipped:rc=5 而且最後一行是 pytest 的「N deselected」/「no tests ran」⇒ rc=None、unverified、needs 說明
N2 rc=5 但不是 pytest 那一行(程式自己 exit(5))⇒ 照樣紅;「1 failed, 3 deselected」也不算
N3 真跑(有 pytest 才跑):addopts 排除 slow、檔裡只有 slow ⇒ aib test 結束碼 4(沒驗)、不是 1
刀(複本):K1 拿掉 rc=5 那條 ⇒ N1 紅
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
import test_entry as E  # noqa: E402


class Mark(unittest.TestCase):
    def test_n1_pytest_none_collected_is_unverified(self):
        for tail in ("============ 3 deselected in 0.20s ============", "no tests ran in 0.01s"):
            r = E._mark_all_skipped({"name": "t", "rc": 5, "output": "collected 3 items / 3 deselected\n\n" + tail + "\n"})
            self.assertIsNone(r["rc"], tail)
            self.assertTrue(r["unverified"])
            self.assertEqual(r["needs"], [E.T("need_none_collected")])

    def test_n2_other_rc5_stays_red(self):
        for out in ("custom tool failed\n", "==== 1 failed, 3 deselected in 0.2s ====\n", ""):
            r = E._mark_all_skipped({"name": "t", "rc": 5, "output": out})
            self.assertEqual(r["rc"], 5, out)
            self.assertFalse(r.get("unverified"))


@unittest.skipUnless(shutil.which("pytest") or subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True,
                                                               stdin=subprocess.DEVNULL).returncode == 0, "沒有 pytest")
class Real(unittest.TestCase):
    def test_n3_deselected_file_is_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "tests").mkdir(parents=True)
            (repo / "pytest.ini").write_text("[pytest]\naddopts = -m 'not slow'\nmarkers = slow\n", encoding="utf-8")
            (repo / "tests" / "test_slow.py").write_text("import pytest\n\n@pytest.mark.slow\ndef test_x():\n    assert True\n",
                                                         encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            env.update({"HOME": str(Path(td) / "home"), "EZ2_AIB_LOCALE": "en", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            (Path(td) / "home").mkdir()
            for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
                subprocess.run(["git", "-C", str(repo)] + a, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
            p = subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(repo),
                                "tests/test_slow.py"], cwd=str(repo), env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.returncode, 4, (p.stdout + p.stderr)[-2000:])
            self.assertIn("pytest collected nothing", p.stdout + p.stderr)


class Knives(unittest.TestCase):
    def test_k1_no_rc5_rule_reds_n1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/test_entry.py", "    if r.get(\"rc\") == PYTEST_NONE_RC and not r.get(\"unverified\"):\n",
                        "    if False:\n", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Mark.test_n1_pytest_none_collected_is_unverified"],
                               cwd=str(root), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_n1_pytest_none_collected_is_unverified", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
