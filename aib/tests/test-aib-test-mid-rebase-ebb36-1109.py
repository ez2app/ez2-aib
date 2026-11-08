#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 遇到樹在 rebase / 合併衝突半路 ⇒ 不跑,回 RC_MID_GIT_OP(6)+ 下一步(b117,ebb36 11-09)。

半套的樹跑出來的綠 / 紅不代表任何一個提交,還會寫進沿用紀錄;以前照跑。全部治具在暫存目錄、用真的 git 造狀態,
不碰真租約、真池(EZ2_AIB_STATE_ROOT / HOME 都指到暫存)。

M1 mid_git_op:真的 rebase / merge / cherry-pick / revert 衝突各認得出操作名;乾淨的樹 ⇒ None
M2 只剩沒解完的衝突(git stash pop 衝突,沒有 MERGE_HEAD)⇒ "conflict"
M3 工作樹(.git 是檔案)和倉裡的子目錄(aib/ 這種子專案)一樣認得出(路徑用 git rev-parse --git-path 解)
M4 量不到不擋:不是 git 倉、git 叫不起來 ⇒ None;bisect 半路不算(git bisect run aib test 是正當用法)
M5 main:半路 ⇒ rc 6、印下一步、不排隊不跑;--allow-mid-rebase ⇒ 照跑;--dry 不擋(只列不跑)
M6 字:四語兩個鍵都在、帶 {what} / {path} 並教 --allow-mid-rebase;usage 列出這個旗標

刀:tests/knives/test-mid-rebase.json(本檔 --knives 逐把換上重跑,每把都要讓本檔紅,印 N/M)。
"""
import contextlib
import io
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
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))

GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_EDITOR": "true",
           "GIT_TERMINAL_PROMPT": "0"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ebb36-midrebase-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = dict(GIT_ENV, HOME=self.tmp, EZ2_AIB_STATE_ROOT=str(Path(self.tmp) / "state"),
                   AIB_HOST_LEASE_FILE=str(Path(self.tmp) / "host-lease.json"),
                   EZ2_VERIFY_SHADOW_DIR=str(Path(self.tmp) / "shadow"))
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        (Path(self.tmp) / "shadow").mkdir()
        import test_entry
        self.te = test_entry
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test-x.py").write_text("print(1)\n", encoding="utf-8")
        (self.repo / "f.txt").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", *args], cwd=str(cwd or self.repo), check=check, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)

    def commit_f(self, text, msg, cwd=None):
        (Path(cwd or self.repo) / "f.txt").write_text(text, encoding="utf-8")
        self.git("commit", "-qam", msg, cwd=cwd)

    def two_branches(self):
        """main 與 side 都改 f.txt 同一行 ⇒ 合 / 重放一定衝突。"""
        self.git("checkout", "-q", "-b", "side")
        self.commit_f("side\n", "side")
        self.git("checkout", "-q", "main")
        self.commit_f("main\n", "main")


class M1Ops(_Base):
    def test_m1_clean_tree_is_none(self):
        self.assertIsNone(self.te.mid_git_op(self.repo))

    def test_m1_rebase_conflict(self):
        self.two_branches()
        self.git("checkout", "-q", "side")
        self.assertNotEqual(self.git("rebase", "main", check=False).returncode, 0, "治具:rebase 要停在衝突")
        op, where = self.te.mid_git_op(self.repo)
        self.assertEqual(op, "rebase")
        self.assertIn("rebase-", where)

    def test_m1_rebase_stopped_after_resolve_still_blocks(self):
        # 衝突解完、git add 了但還沒 --continue:沒有 unmerged,仍在 rebase 半路
        self.two_branches()
        self.git("checkout", "-q", "side")
        self.git("rebase", "main", check=False)
        (self.repo / "f.txt").write_text("resolved\n", encoding="utf-8")
        self.git("add", "f.txt")
        self.assertEqual(self.git("ls-files", "--unmerged").stdout, "")
        self.assertEqual(self.te.mid_git_op(self.repo)[0], "rebase")

    def test_m1_apply_style_rebase(self):
        self.two_branches()
        self.git("checkout", "-q", "side")
        self.git("rebase", "--apply", "main", check=False)
        self.assertEqual(self.te.mid_git_op(self.repo)[0], "rebase")

    def test_m1_merge_conflict(self):
        self.two_branches()
        self.git("merge", "side", check=False)
        self.assertEqual(self.te.mid_git_op(self.repo)[0], "merge")

    def test_m1_cherry_pick_conflict(self):
        self.two_branches()
        self.git("cherry-pick", "side", check=False)
        self.assertEqual(self.te.mid_git_op(self.repo)[0], "cherry-pick")

    def test_m1_revert_conflict(self):
        self.commit_f("one\n", "one")
        first = self.git("rev-parse", "HEAD").stdout.strip()
        self.commit_f("two\n", "two")
        self.git("revert", "--no-edit", first, check=False)
        self.assertEqual(self.te.mid_git_op(self.repo)[0], "revert")

    def test_m1_after_abort_runs_again(self):
        self.two_branches()
        self.git("merge", "side", check=False)
        self.git("merge", "--abort")
        self.assertIsNone(self.te.mid_git_op(self.repo))


class M2Unmerged(_Base):
    def test_m2_stash_pop_conflict_is_conflict(self):
        (self.repo / "f.txt").write_text("stashed\n", encoding="utf-8")
        self.git("stash", "-q")
        self.commit_f("committed\n", "c")
        self.assertNotEqual(self.git("stash", "pop", check=False).returncode, 0, "治具:stash pop 要衝突")
        self.assertFalse((self.repo / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.te.mid_git_op(self.repo), ("conflict", None))


class M3Layouts(_Base):
    def test_m3_worktree(self):
        self.two_branches()
        wt = Path(self.tmp) / "wt"
        self.git("worktree", "add", "-q", "-b", "wtb", str(wt), "side")
        self.assertTrue((wt / ".git").is_file(), "治具:工作樹的 .git 是檔案")
        self.git("rebase", "main", cwd=wt, check=False)
        self.assertEqual(self.te.mid_git_op(wt)[0], "rebase")
        self.assertIsNone(self.te.mid_git_op(self.repo), "主樹沒在 rebase,不能被工作樹連累")

    def test_m3_subdirectory(self):
        sub = self.repo / "aib"
        sub.mkdir()
        (sub / "x.txt").write_text("x\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "sub")
        self.two_branches()
        self.git("merge", "side", check=False)
        self.assertEqual(self.te.mid_git_op(sub)[0], "merge")


class M4Unmeasured(_Base):
    def test_m4_not_a_repo(self):
        plain = Path(self.tmp) / "plain"
        plain.mkdir()
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": self.tmp}):
            self.assertIsNone(self.te.mid_git_op(plain))

    def test_m4_git_missing(self):
        with mock.patch.object(self.te, "_git_out", side_effect=FileNotFoundError("git")):
            self.assertIsNone(self.te.mid_git_op(self.repo))

    def test_m4_bisect_is_not_blocked(self):
        self.commit_f("one\n", "one")
        self.commit_f("two\n", "two")
        self.git("bisect", "start", "HEAD", "HEAD~2")
        self.assertTrue((self.repo / ".git" / "BISECT_LOG").exists(), "治具:bisect 半路")
        self.assertIsNone(self.te.mid_git_op(self.repo))


class M5Main(_Base):
    def _main(self, argv=()):
        ran, queued = [], []
        env = {k: v for k, v in os.environ.items() if k != "AIB_TEST_TMPDIR"}
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.te.reuse_index, "auto_compact", return_value=[]), \
                mock.patch.object(self.te, "_lease_now", return_value=(None, None)), \
                mock.patch.object(self.te, "_ci_or_fixture_home", return_value=True), \
                mock.patch.object(self.te, "exclusive_block", return_value=None), \
                mock.patch.object(self.te, "big_batch_lease",
                                  side_effect=lambda *a, **k: queued.append(1) or {"why": "small", "run_id": None,
                                                                                   "waited": 0}), \
                mock.patch.object(self.te, "release_batch_lease"), \
                mock.patch.object(self.te, "_main_run", side_effect=lambda *a, **k: ran.append(1) or 0), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = self.te.main(["--repo", str(self.repo)] + list(argv) + ["tests/test-x.py"])
        return rc, ran, queued, err.getvalue() + out.getvalue()

    def test_m5_mid_rebase_not_run(self):
        self.two_branches()
        self.git("checkout", "-q", "side")
        self.git("rebase", "main", check=False)
        rc, ran, queued, text = self._main()
        self.assertEqual(rc, self.te.RC_MID_GIT_OP)
        self.assertEqual(rc, 6)
        self.assertEqual((ran, queued), ([], []), "半路不准跑、也不准去排租約")
        self.assertIn("--allow-mid-rebase", text)
        self.assertIn("rebase", text)

    def test_m5_conflict_wording(self):
        (self.repo / "f.txt").write_text("stashed\n", encoding="utf-8")
        self.git("stash", "-q")
        self.commit_f("committed\n", "c")
        self.git("stash", "pop", check=False)
        rc, ran, _, text = self._main()
        self.assertEqual((rc, ran), (6, []))
        self.assertIn("--unmerged", text)

    def test_m5_allow_flag_runs(self):
        self.two_branches()
        self.git("merge", "side", check=False)
        rc, ran, _, _ = self._main(["--allow-mid-rebase"])
        self.assertEqual((rc, ran), (0, [1]))

    def test_m5_clean_runs(self):
        rc, ran, _, _ = self._main()
        self.assertEqual((rc, ran), (0, [1]))

    def test_m5_dry_is_not_blocked(self):
        self.two_branches()
        self.git("merge", "side", check=False)
        rc, ran, _, _ = self._main(["--dry"])
        self.assertEqual((rc, ran), (0, []))


class M6Words(unittest.TestCase):
    def test_m6_four_languages(self):
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for key in ("aib.test.mid_git_op", "aib.test.mid_git_conflict"):
                msg = d[key]
                self.assertIn("--allow-mid-rebase", msg, (lang, key))
                self.assertIn("{path}", msg, (lang, key))
            self.assertIn("{what}", d["aib.test.mid_git_op"], lang)
            self.assertIn("--allow-mid-rebase", d["aib.test.usage"], lang)


def run_knives():
    """逐把換上 tests/knives/test-mid-rebase.json 的刀、重跑本檔;每把都要讓本檔紅。寫回原文(不靠 mtime)。"""
    repo = ROOT.parent
    spec = json.loads((repo / "tests" / "knives" / "test-mid-rebase.json").read_text(encoding="utf-8"))
    bit = 0
    for k in spec["knives"]:
        f = repo / k["file"]
        src = f.read_text(encoding="utf-8")
        if src.count(k["old"]) != 1:
            print("刀落不下 %s(錨點出現 %d 次)" % (k["name"], src.count(k["old"])))
            continue
        try:
            f.write_text(src.replace(k["old"], k["new"]), encoding="utf-8")
            r = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=600)
        finally:
            f.write_text(src, encoding="utf-8")
        red = r.returncode != 0
        bit += red
        failed = sorted({ln.split()[1] for ln in r.stderr.splitlines() if ln.startswith(("FAIL:", "ERROR:"))})
        print("%s %s → %s" % ("咬到" if red else "沒咬到", k["name"], " ".join(failed) or "-"))
    print("刀 通過 %d/%d" % (bit, len(spec["knives"])))
    return 0 if bit == len(spec["knives"]) else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--knives"]:
        sys.exit(run_knives())
    unittest.main()
