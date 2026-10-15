#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""新用戶安裝 / 移除 / Claude hook / doctor 一串修正(10-15 稽核 C2 C3 C4 C5 C6 C9 C13)。

C2 git 安裝:hook 算正本樹路徑要接上公開倉的 aib/ 那一層;pipx(不在 git 裡)= 本支就是正本;安裝訊息不寫維護者用語
全部在暫存 HOME / 假 git 倉裡跑,不碰真的 ~/.claude、~/.ez2-aib、桌面、~/ez2-aib。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # <倉>/aib
SRC = ROOT / "src"
for _p in (SRC / "cockpit", SRC / "identity", SRC / "bootstrap", ROOT / "tools" / "support", ROOT / "tests"):
    sys.path.insert(0, str(_p))
from aib_tmp_root import aib_tmp_root  # noqa: E402

I18N = {loc: json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
        for loc in ("en", "zh-TW", "ja", "ko")}


def load_gate():
    spec = importlib.util.spec_from_file_location("aib_now_gate_t", str(SRC / "policy" / "hooks" / "aib-now-gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, check=True).stdout


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="abb89-1015-", dir=aib_tmp_root())))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)


# ───────────────────────────────────────────────────────────── C2
class C2CanonicalPath(TmpCase):
    def _repo(self, layout_prefix):
        top = self.tmp / "repo"
        f = top / layout_prefix / "src" / "policy" / "hooks" / "aib-now-gate.py"
        f.parent.mkdir(parents=True)
        f.write_text("# x\n", encoding="utf-8")
        git(top, "init", "-q", "-b", "main")
        git(top, "add", "-A")
        git(top, "commit", "-q", "-m", "init")
        return top, f

    def test_public_layout_from_linked_worktree(self):
        import canonical_tree
        top, f = self._repo("aib")
        wt = self.tmp / "wt"
        git(top, "worktree", "add", "-q", str(wt), "-b", "side")
        got = canonical_tree.canonical_path(str(wt / "aib" / "src" / "policy" / "hooks" / "aib-now-gate.py"))
        self.assertEqual(os.path.realpath(got), str(f))
        gate = load_gate()
        self.assertEqual(os.path.realpath(gate.canonical_script(repo_root=str(wt / "aib"))), str(f))

    def test_public_layout_from_main_tree(self):
        import canonical_tree
        top, f = self._repo("aib")
        self.assertEqual(os.path.realpath(canonical_tree.canonical_path(str(f))), str(f))

    def test_flat_layout_still_works(self):
        import canonical_tree
        top, f = self._repo("")
        self.assertEqual(os.path.realpath(canonical_tree.canonical_path(str(f))), str(f))

    def test_not_in_git_is_itself(self):
        import canonical_tree
        f = self.tmp / "site-packages" / "aib" / "src" / "cockpit" / "menu_pick.py"
        f.parent.mkdir(parents=True)
        f.write_text("# x\n", encoding="utf-8")
        self.assertEqual(canonical_tree.canonical_path(str(f)), str(f))

    def test_real_hooks_resolve_to_existing_files(self):
        """這份程式自己的三支 hook:算出來的正本路徑要真的有那支檔(舊寫法少一層 aib/ ⇒ 不存在 ⇒ 永遠裝不上)。"""
        import menu_pick
        import auto_distill_hook
        gate = load_gate()
        for p in (gate.canonical_script(), menu_pick.canonical_self(), auto_distill_hook.canonical_script(gate)):
            self.assertTrue(p and os.path.isfile(p), p)

    def test_install_message_has_no_jargon(self):
        for loc, bad in (("en", ("canonical", "merge")), ("zh-TW", ("正本", "合併")), ("ja", ("正本", "マージ")),
                         ("ko", ("정본", "병합"))):
            for key in ("aib.nowgate.no_script", "menu_pick.skip.no_script"):
                for word in bad:
                    self.assertNotIn(word, I18N[loc][key], (loc, key))


if __name__ == "__main__":
    unittest.main()
