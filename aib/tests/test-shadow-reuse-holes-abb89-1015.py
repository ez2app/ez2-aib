#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影子續跑的量測洞(10-15 abb89):量不到讀了哪些檔 ⇒ 永遠沿用不到。只補「確定量得到」的形,其餘照舊量不到。

F4 git:沿用池的專案身分(resume_store.project_key,10-13)對本倉問兩條唯讀查詢 —— `rev-parse --is-shallow-repository`、
   `log --max-parents=0 --format=… HEAD`,以前不在白名單 ⇒ 每支碰到沿用池的測試都 child-untracked:git。
   G1 兩條都分類成唯讀查詢(gitq:判沿用時重跑同一條、比結果)
   G2 實跑:只做這兩條查詢的測試量得到;沒改東西 ⇒ 沿用得到
   G3 安全:倉變淺複製(.git/shallow)⇒ 查詢結果變 ⇒ 不准沿用
   G4 安全:併進另一條根歷史 ⇒ 根提交清單變 ⇒ 不准沿用
F2 bash 3.2:macOS /bin/bash 沒有 BASH_XTRACEFD,一律量不到。`bash -c '<字串>'` 的字串若只是一條內建命令、只展開普通變數
   (不開檔、不跑命令、不 glob)⇒ 它什麼都沒讀 ⇒ 不算量不到。字串由鉤子從 BASH_EXECUTION_STRING 記下(cstr)。
   B1 分析端:版本探測那種字串放行;會讀檔 / 命令替換 / 導向 / glob / 管線 / 沒 cstr ⇒ 照舊量不到
   B2 實跑(只在 /bin/bash < 4.1 的機器):版本探測量得到;改了測試讀的檔 ⇒ 不准沿用
   B3 安全(同上):`/bin/bash -c 'cat data.txt'` 照舊量不到;環境帶同名函式(BASH_FUNC_echo%%)⇒ 照舊量不到
F1 tmux:「自己的、沒開伺服器」的 tmux 呼叫本來就不算外部,但 socket 目錄要「這份追蹤有人在底下寫過檔」——
   tempfile.mkdtemp() 建的目錄沒有寫檔事件 ⇒ 誤判外部。起跑快照(t0)裡沒有、跑的期間才出生的暫存目錄也算自己的。
   T1 分析端:跑的期間才出生的目錄 ⇒ 自己的;起跑前就在 ⇒ 照舊不算
   T2 實跑(有 tmux 才驗):mkdtemp 的 socket、沒開伺服器 ⇒ 不算外部
   T3 安全:同一個 socket 開了伺服器 ⇒ 照舊外部;socket 目錄起跑前就在 ⇒ 照舊外部
F3 python 子行程:追蹤器自己問「這支 python 的版本 / 有沒有 pytest」(shadow_tracer.python_id)用洗掉鉤子的環境起子行程。
   在被追蹤的行程裡(巢狀跑 aib test、算環境指紋)⇒ 每支都 python-child-no-sitecustomize。改成在被追蹤時帶上
   py-site(只載稽核模組,不動 sys.path)⇒ 子行程的讀取記進同一份追蹤、問到的答案不變。
   P1 實跑:被追蹤的測試呼叫 python_id ⇒ 量得到
   P2 安全:探測讀的 site-packages(放在倉裡的 venv)多了一個 pytest ⇒ 不准沿用
刀(複本):K4 拿掉 git 白名單 ⇒ G2 紅;K2 拿掉 cstr 放行 ⇒ B2 紅(或 B1 紅);K1 拿掉出生規則 ⇒ T1 紅;K3 拿掉 py-site ⇒ P1 紅
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
VERIFY = ROOT / "tools" / "verify"
sys.path.insert(0, str(VERIFY))
sys.path.insert(0, str(ROOT / "tests"))
import shadow_tracer as st  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _bash_traceable(bash):
    try:
        out = subprocess.run([bash, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=10).stdout.split()
        return (int(out[0]), int(out[1])) >= (4, 1)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


OLD_SYS_BASH = os.path.exists("/bin/bash") and not _bash_traceable("/bin/bash")
HAS_TMUX = bool(shutil.which("tmux"))


def _clean_env():
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}


class _Fixture(unittest.TestCase):
    """暫存倉 + tests/ 底下的小測試;run_one / reuse_row 跟 aib test 同一套。"""
    TESTS = {}

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(os.path.realpath(self.td.name)) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "data.txt").write_text("D\n", encoding="utf-8")
        for n, body in self.TESTS.items():
            (self.repo / "tests" / n).write_text(body, encoding="utf-8")
        self._git("init", "-q")
        self._git("remote", "add", "origin", "https://github.com/example/fixture.git")
        self._git("add", "-A")
        self._git("commit", "-qm", "x")
        self.trace_dir = Path(self.td.name) / "traces"
        self.trace_dir.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _git(self, *a):
        return subprocess.run(["git", "-C", str(self.repo)] + list(a), check=True, capture_output=True, text=True,
                              env=dict(os.environ, **GIT_ENV), stdin=subprocess.DEVNULL).stdout.strip()

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

    def _run(self, name, env=None):
        row = self._keep(lambda: st.run_one(self.repo, "tests/" + name, environ=env or _clean_env(), timeout=120))
        self.assertEqual(row["rc"], 0, row.get("tail"))
        return row

    def _reuse(self, name, row, env=None):
        return self._keep(lambda: st.reuse_row(self.repo, "tests/" + name, row, environ=env or _clean_env()))


# ── F4 git ─────────────────────────────────────────────────────────────────────────────────────────────
GIT_T = '''import subprocess, os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for a in (["rev-parse", "--is-shallow-repository"], ["log", "--max-parents=0", "--format=%ct %H", "HEAD"]):
    p = subprocess.run(["git", "-C", root] + a, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    print(p.stdout)
'''


class GitQueries(_Fixture):
    TESTS = {"t_git.py": GIT_T}

    def test_g1_classified_as_read_only_query(self):
        for a in (["rev-parse", "--is-shallow-repository"], ["log", "--max-parents=0", "--format=%ct %H", "HEAD"]):
            argv = ["git", "-C", str(self.repo)] + a
            kind, _d = st.classify_child({"argv": argv, "cwd": str(self.repo)}, repo=self.repo)
            self.assertEqual(kind, "githead", argv)
            self.assertTrue(st.git_query_ok(argv))
        # 白名單沒有放寬到別的:--max-parents 以外的未知選項照舊量不到
        kind, _d = st.classify_child({"argv": ["git", "-C", str(self.repo), "log", "--walk-reflogs", "HEAD"],
                                      "cwd": str(self.repo)}, repo=self.repo)
        self.assertEqual(kind, "untracked")

    def test_g2_measured_and_reused_when_unchanged(self):
        row = self._run("t_git.py")
        self.assertIs(row["measured"], True, row["why"])
        self.assertTrue(any(i.startswith("gitq:") for i in row["inputs"]), row["inputs"])
        self.assertIsNotNone(self._reuse("t_git.py", row), "什麼都沒改 ⇒ 要沿用得到")

    def test_g3_shallow_change_blocks_reuse(self):
        row = self._run("t_git.py")
        (self.repo / ".git" / "shallow").write_text(self._git("rev-parse", "HEAD") + "\n", encoding="utf-8")
        self.assertEqual(self._git("rev-parse", "--is-shallow-repository"), "true")
        self.assertIsNone(self._reuse("t_git.py", row), "倉變淺複製 ⇒ 查詢結果變了 ⇒ 一定重跑")

    def test_g4_new_root_blocks_reuse(self):
        row = self._run("t_git.py")
        main = self._git("rev-parse", "--abbrev-ref", "HEAD")
        self._git("checkout", "-q", "--orphan", "other")
        self._git("commit", "-q", "--allow-empty", "-m", "other root")
        self._git("checkout", "-q", "-f", main)
        self._git("merge", "-q", "--allow-unrelated-histories", "-m", "m", "other")
        self.assertIsNone(self._reuse("t_git.py", row), "根提交多一個 ⇒ 查詢結果變了 ⇒ 一定重跑")


# ── 刀(複本)─────────────────────────────────────────────────────────────────────────────────────────
def _knife(rel, needle, replacement, cell):
    from aib_knife_copy import knife_copy
    name = Path(__file__).name
    with knife_copy(ROOT, rel, needle, replacement, tests=(name,)) as root:
        p = subprocess.run([sys.executable, str(root / "tests" / name), cell], cwd=str(root), env=_clean_env(),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
    return set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:]


class Knives(unittest.TestCase):
    def test_k4_no_git_whitelist_reds_g2(self):
        red, err = _knife("tools/verify/shadow_tracer.py", '"--show-toplevel", "--is-inside-work-tree", "--is-shallow-repository")',
                          '"--show-toplevel", "--is-inside-work-tree")', "GitQueries.test_g2_measured_and_reused_when_unchanged")
        self.assertIn("test_g2_measured_and_reused_when_unchanged", red, err)


if __name__ == "__main__":
    unittest.main()
