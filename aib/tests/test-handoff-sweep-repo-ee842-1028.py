#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接的專案清查跟 collect 量同一個倉(10-28 @aeb40 裁 A)。

以前交接第三步 run_sweep 起 project_sweep.py 不帶 --repo,清查默默改量 aib 程式碼那棵樹 ——
跟 collect 量的「這一席的倉」是兩個來源;測試裡還會 `git worktree list` 掃到真家目錄底下的工作樹。
全在暫存:狀態根 / HOME 是暫存、git 倉是暫存建的,不碰真 ~/.ez2-aib、不假設 Mac。

S1 run_sweep:有倉 ⇒ --repo <那個倉>;量不到 ⇒ --no-repo(不默默改量程式碼樹)。
S2 Sweep(no_repo):分支 / 工作樹 / 推包那幾段不跑 git,記成沒量到。
S3 Sweep(repo=席的倉):git 量那個倉;機器租約工具照舊從 aib 程式碼樹拿。
S4 推包標籤：倉根底下沒有 tools/ 也照算真身分(以前從 <倉>/tools/verify 載 reuse_index,倉根載不到就退回路徑)。
S5 collect 把量到的倉(git 頂層)交給 run_sweep;倉量不到 ⇒ 交空字串。
刀:K1 run_sweep 改回不帶 --repo ⇒ S1 紅;K2 拿掉 no_repo 的提早返回 ⇒ S2 紅;
   K3 租約工具改回 self.repo ⇒ S3 紅;K4 _ship_tag 改回從 <倉>/tools/verify 載 ⇒ S4 紅;
   K5 collect 不把倉交給 run_sweep ⇒ S5 紅。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402

for _d in ("src/cockpit", "src/identity", "src/memory", "src/messaging"):
    sys.path.insert(0, str(ROOT / _d))
import aib_handoff as H  # noqa: E402
import project_sweep as PSW  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(*args, cwd=None):
    p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=60, env=dict(os.environ, **GIT_ENV))
    if p.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout


class _Done:
    def __init__(self, rc=0, stdout=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, ""


class SweepRepo(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="sweep-repo-", dir=aib_tmp_root()))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.state = self.td / "state"
        self.state.mkdir()
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                                           "HOME": str(self.td / "home")})
        env.start()
        self.addCleanup(env.stop)
        (self.td / "home").mkdir()

    def _repo(self):
        repo = self.td / "proj"
        git("init", "-q", "-b", "main", str(repo))
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        git("add", "a.txt", cwd=repo)
        git("commit", "-q", "-m", "one", cwd=repo)
        git("remote", "add", "origin", "https://example.invalid/proj.git", cwd=repo)
        (repo / "sub").mkdir()
        return repo

    def _sweep_argv(self, **kw):
        seen = []

        def run(argv, **_kw):
            seen.append(list(argv))
            return _Done(0, json.dumps({"items": []}))
        with mock.patch.object(H.subprocess, "run", side_effect=run):
            H.run_sweep("abcde", "proj:1111111111111111", **kw)
        self.assertEqual(len(seen), 1, seen)
        return seen[0]

    def test_s1_run_sweep_passes_seat_repo(self):
        argv = self._sweep_argv(repo="/some/seat/repo")
        self.assertEqual(argv[argv.index("--repo") + 1], "/some/seat/repo")
        self.assertNotIn("--no-repo", argv)
        argv = self._sweep_argv(repo="")
        self.assertIn("--no-repo", argv)
        self.assertNotIn("--repo", argv, "量不到倉不准默默改量程式碼樹")
        self.assertIn("--no-repo", self._sweep_argv(), "沒給倉 = 量不到倉")

    def _calls(self, sweep, step):
        calls = []

        def run(argv, cwd=None, timeout=None):
            calls.append(list(argv))
            return _Done(0, "")
        with mock.patch.object(PSW, "_run", side_effect=run):
            step(sweep)
        return calls

    def test_s2_no_repo_skips_git(self):
        sw = PSW.Sweep("proj:1111111111111111", root=str(self.state), no_repo=True)
        (self.state / "ship").mkdir()
        calls = self._calls(sw, PSW.Sweep.branches_and_worktrees) + self._calls(sw, PSW.Sweep.ship_state)
        self.assertFalse([c for c in calls if c and c[0] == "git"], calls)
        self.assertTrue(any("量不到倉" in m for m in sw.missed), sw.missed)
        self.assertEqual(len([m for m in sw.missed if "量不到倉" in m]), 2, sw.missed)

    def test_s3_git_on_seat_repo_tools_from_code(self):
        repo = self._repo()
        sw = PSW.Sweep("proj:1111111111111111", root=str(self.state), repo=str(repo))
        calls = self._calls(sw, PSW.Sweep.branches_and_worktrees)
        gits = [c for c in calls if c and c[0] == "git"]
        self.assertTrue(gits)
        self.assertTrue(all(c[1:3] == ["-C", str(repo)] for c in gits), gits)
        lease = self._calls(sw, PSW.Sweep.lease)
        tool = str(PSW.REPO_DEFAULT / "tools" / "governance" / "host_lease.py")
        if Path(tool).is_file():
            self.assertTrue(lease and lease[0][1] == tool, lease)
        else:
            self.assertEqual(lease, [], "程式碼樹沒有租約工具 ⇒ 不跑")
            self.assertIn("機器租約", sw.missed)

    def test_s4_ship_tag_from_repo_root(self):
        repo = self._repo()
        code = "\n".join([
            "import sys",
            "sys.path.insert(0, %r)" % str(ROOT / "src" / "cockpit"),
            "import project_sweep as P",
            "print(P._ship_tag(sys.argv[1]))",
        ])
        tags = []
        for where in (repo, repo / "sub"):
            out = subprocess.run([sys.executable, "-c", code, str(where)], capture_output=True, text=True,
                                 timeout=60, env=dict(os.environ, **GIT_ENV))
            self.assertEqual(out.returncode, 0, out.stderr)
            tags.append(out.stdout.strip())
        self.assertEqual(tags[0], tags[1], "倉根與子目錄是同一個倉,標籤要一樣(倉根不能退回路徑)")
        path_tag = __import__("hashlib").sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]
        self.assertNotEqual(tags[0], path_tag, "倉根算出來的是路徑標籤 ⇒ reuse_index 沒載到")

    def test_s5_collect_hands_its_repo_to_sweep(self):
        repo = self._repo()
        got = []
        seat = {"code": "abcde", "cabin": "", "session_uuid": "", "project_uid": "proj:1111111111111111",
                "persona_uid": "", "record": {}}
        with mock.patch.object(H, "run_sweep", side_effect=lambda code, uid, home, repo="": got.append(repo) or {}), \
                mock.patch.object(H, "binding_state", return_value={"problem": "ok"}):
            H.collect(seat, repo=str(repo / "sub"), home=str(self.state))
            H.collect(seat, repo=str(self.td / "not-a-repo"), home=str(self.state))
        self.assertEqual(Path(got[0]).resolve(), repo.resolve(), "交給清查的是 collect 量到的 git 頂層")
        self.assertEqual(got[1], "", "倉量不到 ⇒ 交空字串(清查帶 --no-repo)")


if __name__ == "__main__":
    unittest.main()
