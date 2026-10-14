#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""新專案跑 aib ship 不噴 traceback(10-14 公開版新用戶走查第 1 條)。

以前:0 個提交 ⇒ `ship check` 在 policy_start 的 git log 噴 RuntimeError;規則起點是根提交 ⇒ first_parent 取 `根^..HEAD`
噴 RuntimeError;沒有 origin ⇒ `ship package` 在 git fetch 噴 RuntimeError。新用戶第一次試幾乎一定踩到。
N1 0 個提交:check ⇒ rc 2、說「還沒有任何提交」、沒有 Traceback
N2 規則起點是根提交(只有一顆):check ⇒ rc 0、算到 1 顆、沒有 Traceback
N3 規則起點不是根(第二顆才加 .aib/ship.toml):check ⇒ rc 0、算到 1 顆(原本就會的,不能弄壞)
N4 有提交、沒有 origin:package --preview ⇒ rc 2、說沒有遠端 origin、沒有 Traceback
N5 0 個提交:package --preview ⇒ rc 2、說「還沒有任何提交」、沒有 Traceback
刀(複本):K1 first_parent 改回 `since^..rev` ⇒ N2 紅;K2 拿掉沒提交檢查 ⇒ N1 紅;K3 拿掉 origin 檢查 ⇒ N4 紅
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402

SHIP = ROOT / "tools" / "release" / "ship.py"
TOML = "[plugins]\n"


class NewRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-ship-new-repo-", dir=aib_tmp_root())
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "proj")
        os.makedirs(self.home)
        os.makedirs(self.repo)
        self.env = dict(os.environ, HOME=self.home, EZ2_AIB_HOME=os.path.join(self.home, ".ez2-aib"),
                        EZ2_AIB_STATE_ROOT=os.path.join(self.home, ".ez2-aib"),
                        EZ2_COCKPIT_STATE_DIR=os.path.join(self.home, ".ez2-aib", "cockpit"),
                        EZ2_AIB_LOCALE="zh-TW", GIT_CONFIG_NOSYSTEM="1", AIB_SHIP_NOTIFY="0",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        for k in ("TMUX", "TMUX_PANE", "EZ2_AIB_INSTANCE_HOME"):
            self.env.pop(k, None)
        self.git("init", "-q", "-b", "main")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *a):
        subprocess.run(["git", "-C", self.repo, *a], check=True, env=self.env, stdin=subprocess.DEVNULL,
                       capture_output=True)

    def commit(self, path, text, msg):
        p = Path(self.repo, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def ship(self, *a):
        p = subprocess.run([sys.executable, str(SHIP), "--repo", self.repo, *a], env=self.env, cwd=self.repo,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        out = p.stdout + p.stderr
        self.assertNotIn("Traceback", out, out)
        return p.returncode, out

    def test_n1_check_no_commits(self):
        rc, out = self.ship("check")
        self.assertEqual(rc, 2, out)
        self.assertIn("還沒有任何提交", out)

    def test_n2_check_start_is_root(self):
        self.commit(".aib/ship.toml", TOML, "init")
        rc, out = self.ship("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("形狀檢查:1 顆提交,違規 0", out)

    def test_n3_check_start_not_root(self):
        self.commit("README.md", "hi\n", "first")
        self.commit(".aib/ship.toml", TOML, "add ship.toml")
        rc, out = self.ship("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("形狀檢查:1 顆提交,違規 0", out)

    def test_n4_package_no_origin(self):
        self.commit(".aib/ship.toml", TOML, "init")
        rc, out = self.ship("package", "--preview")
        self.assertEqual(rc, 2, out)
        self.assertIn("origin", out)

    def test_n5_package_no_commits(self):
        rc, out = self.ship("package", "--preview")
        self.assertEqual(rc, 2, out)
        self.assertIn("還沒有任何提交", out)


if __name__ == "__main__":
    unittest.main()
