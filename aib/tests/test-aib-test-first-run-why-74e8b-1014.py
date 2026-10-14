#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「主要重跑原因」分清楚:第一次跑(還沒有紀錄)/ 紀錄池真的讀壞了(附原因)(10-14 公開版新用戶走查)。

以前:新機器第一次 `aib test`,收尾印「主要重跑原因:紀錄池讀不了:,這次全跑(修:…reuse_index.py quarantine --apply)」——
冒號後面是空的,還叫第一次用的人去修壞列。原因:rerun_causes 只拿 why 去翻,不帶 detail ⇒ 認不出 no-rows-files
(= 還沒有紀錄),真的讀壞時原因也是空白。
F1 新 HOME、新專案第一次 aib test:原因是「還沒有任何紀錄,第一次跑」,不提紀錄池讀不了、不叫人 quarantine
F2 跑過一次後把紀錄池弄壞一行再跑:原因是「紀錄池讀不了:bad-jsonl:…」,冒號後面不是空的
F3 rerun_causes:同原因同類合併計數(兩支都是第一次 ⇒ 一句、(2))
刀(複本):K1 rerun_causes 不帶 detail ⇒ F1、F2 紅;K2 結果列不記 detail ⇒ F1、F2 紅
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
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
from aib_tmp_root import aib_tmp_root  # noqa: E402
try:  # 載 test_entry 會碰到機器租約;開源版沒有這支治具
    import aib_host_lease_fixture  # noqa: E402,F401
except ImportError:
    pass

CLI = ROOT / "cli.py"
TEST = 'import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertTrue(True)\n' \
       'if __name__ == "__main__": unittest.main()\n'


class FirstRunWhy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-first-why-", dir=aib_tmp_root())
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "tests"))
        Path(self.proj, "tests", "test_a.py").write_text(TEST, encoding="utf-8")
        Path(self.proj, "tests", "test_b.py").write_text(TEST, encoding="utf-8")
        g = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": self.home, "PATH": os.environ.get("PATH", "")}
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git", "-C", self.proj, *cmd], env=g, check=True, capture_output=True)
        py = os.path.dirname(sys.executable)
        self.env = {"HOME": self.home, "PATH": "%s:/usr/bin:/bin:%s" % (py, os.path.dirname(shutil.which("git"))),
                    "LANG": "zh_TW.UTF-8", "EZ2_AIB_LOCALE": "zh-TW", "TMPDIR": self.tmp + "/"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def aib_test(self):
        p = subprocess.run([sys.executable, str(CLI), "test", "tests/test_a.py", "tests/test_b.py"], env=self.env,
                           cwd=self.proj, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        out = p.stdout + p.stderr
        line = next((l for l in out.splitlines() if l.startswith("主要重跑原因")), None)
        self.assertIsNotNone(line, out)
        return p.returncode, line, out

    def test_f1_first_run(self):
        rc, line, out = self.aib_test()
        self.assertEqual(rc, 0, out)
        self.assertIn("還沒有任何紀錄", line)
        self.assertNotIn("讀不了", line)
        self.assertNotIn("quarantine", line)

    def test_f2_damaged_pool_names_reason(self):
        self.aib_test()
        rows = sorted(Path(self.home, ".ez2-aib", "verify-shadow").glob("verify-*.rows.jsonl"))
        self.assertTrue(rows, "no pool file after the first run")
        with open(rows[0], "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        _rc, line, out = self.aib_test()
        m = re.search(r"紀錄池讀不了:([^,]*),", line)
        self.assertIsNotNone(m, line)
        self.assertTrue(m.group(1).strip(), "empty reason: " + line)
        self.assertIn("bad-jsonl", m.group(1))

    def test_f3_grouped(self):
        import test_entry as te
        rows = [{"why": "index-degraded", "why_detail": "no-rows-files"},
                {"why": "index-degraded", "why_detail": "no-rows-files"}]
        got = te.rerun_causes(rows)
        self.assertEqual(len(got), 1, got)
        self.assertIn("還沒有任何紀錄", got[0])
        self.assertTrue(got[0].endswith("(2)"), got)


if __name__ == "__main__":
    unittest.main()
