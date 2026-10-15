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


# ───────────────────────────────────────────────────────────── C3
class C3GateOptIn(TmpCase):
    CODE = "abcde"

    def _run_stop(self, locale="en"):
        tp = self.tmp / "t.jsonl"
        rows = [{"type": "user", "message": {"role": "user", "content": "do it"}, "timestamp": "2026-10-15T00:00:00Z"},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}]
        tp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AIB_NOW_GATE", "EZ2_AIB", "LC_", "LANG"))}
        env.update(HOME=str(self.tmp), AIB_NOW_GATE_CODE=self.CODE, EZ2_AIB_STATE_ROOT=str(self.tmp / "state"),
                   EZ2_AIB_LOCALE=locale, LANG="C")
        r = subprocess.run([sys.executable, str(SRC / "policy" / "hooks" / "aib-now-gate.py"), "stop"],
                           input=json.dumps({"transcript_path": str(tp)}), capture_output=True, text=True, env=env,
                           timeout=60)
        return r

    def _gate_list(self, text):
        d = self.tmp / "state" / "state" / "seat-now"
        d.mkdir(parents=True, exist_ok=True)
        (d / "gate-seats.txt").write_text(text, encoding="utf-8")

    def test_no_list_never_blocks(self):
        r = self._run_stop()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_listed_seat_blocks_without_maintainer_wording(self):
        self._gate_list("%s\n" % self.CODE)
        r = self._run_stop()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("aib now", r.stderr)
        self.assertNotIn("maintainer", r.stderr)

    def test_star_blocks_other_seat_list_does_not(self):
        self._gate_list("zzzzz\n")
        self.assertEqual(self._run_stop().returncode, 0)
        self._gate_list("*\n")
        self.assertEqual(self._run_stop().returncode, 2)

    def test_texts_localized_no_maintainer(self):
        bad = ("maintainer", "維護者", "メンテナー", "관리자", "10-12", "trial list", "試跑", "試行", "시범")
        for loc, tr in I18N.items():
            for key in ("aib.nowgate.block", "aib.nowgate.start_hard", "aib.nowgate.start_soft"):
                self.assertIn(key, tr, (loc, key))
                for w in bad:
                    self.assertNotIn(w, tr[key], (loc, key, w))


# ───────────────────────────────────────────────────────────── C4
class C4UninstallKeepsUnsavedWork(TmpCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.co = self.home / "ez2-aib"
        (self.co / "aib").mkdir(parents=True)
        (self.co / "bin").mkdir()
        (self.co / "aib" / "VERSION").write_text("0.1.6\n", encoding="utf-8")
        (self.co / "bin" / "aib").write_text("#!/bin/sh\n", encoding="utf-8")
        git(self.co, "init", "-q", "-b", "main")
        git(self.co, "add", "-A")
        git(self.co, "commit", "-q", "-m", "init")
        git(self.co, "remote", "add", "origin", "https://github.com/ez2app/ez2-aib.git")
        git(self.co, "update-ref", "refs/remotes/origin/main", "HEAD")

    def _run(self, *argv):
        import io
        import contextlib
        import uninstall
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = uninstall.main(list(argv), home=str(self.home), package_dir=str(self.co / "aib"))
        return rc, buf.getvalue()

    def test_clean_clone_is_removed(self):
        rc, out = self._run("--yes")
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.co.exists(), out)

    def _assert_kept(self, words):
        rc, out = self._run("--yes")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.co.is_dir(), out)
        self.assertIn("--discard-unsaved-work", out)
        self.assertIn(words, out)

    def test_untracked_file_kept(self):
        (self.co / "notes.txt").write_text("mine\n", encoding="utf-8")
        self._assert_kept("untracked")

    def test_modified_file_kept(self):
        (self.co / "aib" / "VERSION").write_text("0.1.7\n", encoding="utf-8")
        self._assert_kept("uncommitted")

    def test_stash_kept(self):
        (self.co / "aib" / "VERSION").write_text("0.1.7\n", encoding="utf-8")
        git(self.co, "stash", "push", "-q", "-m", "t-abb89-1015")
        self._assert_kept("stash")

    def test_unpushed_branch_kept(self):
        git(self.co, "checkout", "-q", "-b", "fix/x")
        (self.co / "aib" / "x.py").write_text("x = 1\n", encoding="utf-8")
        git(self.co, "add", "-A")
        git(self.co, "commit", "-q", "-m", "x")
        git(self.co, "checkout", "-q", "main")
        self._assert_kept("not on any remote")

    def test_other_worktree_kept(self):
        git(self.co, "worktree", "add", "-q", str(self.tmp / "wt"), "-b", "wt", "origin/main")
        git(self.co, "update-ref", "refs/remotes/origin/wt", "HEAD")
        self._assert_kept("worktree")

    def test_explicit_flag_removes_anyway(self):
        (self.co / "notes.txt").write_text("mine\n", encoding="utf-8")
        rc, out = self._run("--yes", "--discard-unsaved-work")
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.co.exists(), out)


# ───────────────────────────────────────────────────────────── C5
def _cmd(rel, mode):
    return "test -f '/opt/x/aib/src/%s' || exit 0; exec python3 '/opt/x/aib/src/%s' %s" % (rel, rel, mode)


class C5UninstallCleansTraces(TmpCase):
    def setUp(self):
        super().setUp()
        for k in ("CLAUDE_CONFIG_DIR", "EZ2_AIB_DESKTOP_DIR"):
            old = os.environ.pop(k, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, k, old)
        h = self.home = self.tmp / "home"
        self.pkg = self.tmp / "venv" / "site-packages" / "aib"
        self.pkg.mkdir(parents=True)
        mine_stop = {"hooks": [{"type": "command", "command": "echo mine"}]}
        self.settings = {
            "model": "x",
            "hooks": {
                "Stop": [mine_stop, {"hooks": [{"type": "command", "command": _cmd("policy/hooks/aib-now-gate.py", "stop")}]},
                         {"hooks": [{"type": "command", "command": _cmd("cockpit/claudebot_hook.py", "stop")}]}],
                "SessionStart": [{"hooks": [{"type": "command", "command": _cmd("policy/hooks/aib-now-gate.py", "session-start")}]}],
                "Notification": [{"matcher": "permission_prompt",
                                  "hooks": [{"type": "command", "command": _cmd("cockpit/menu_pick.py", "trigger")}]}],
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _cmd("identity/auto_distill_hook.py", "prompt")},
                                                {"type": "command", "command": "echo also-mine"}]}],
            }}
        self._w(h / ".claude" / "settings.json", self.settings)
        for b in ("settings.json.bak-now-gate-20261015-000000-1", "settings.json.bak-claudebot-1", "settings.json.mybackup"):
            (h / ".claude" / b).write_text("{}", encoding="utf-8")
        self._w(h / ".codex" / "hooks.json", {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": _cmd("identity/auto_distill_hook.py", "stop")}]},
            {"hooks": [{"type": "command", "command": "echo codex-mine"}]}]}})
        self._w(h / ".grok" / "hooks" / "aib-menu-pick.json", {"hooks": {"Notification": [
            {"matcher": "permission_prompt", "hooks": [{"type": "command", "command": _cmd("cockpit/menu_pick.py", "trigger")}]}]}})
        self._w(h / ".grok" / "hooks" / "mine.json", {"hooks": {"Stop": [mine_stop]}})
        self._w(h / ".copilot" / "hooks" / "aib-auto-distill.json", {"version": 1, "hooks": {"agentStop": [
            {"type": "command", "bash": _cmd("identity/auto_distill_hook.py", "stop"), "timeoutSec": 5}]}})
        self._w(h / ".gemini" / "config" / "hooks.json", {
            "aib-menu-pick": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": _cmd("cockpit/menu_pick.py", "trigger")}]}]},
            "mine": {"PreToolUse": []}})
        (h / ".ez2-aib" / "user").mkdir(parents=True)
        self._w(h / ".ez2-aib" / "ext-state" / "foo.json", {"enabled": True})
        desk = h / "Desktop"
        desk.mkdir()
        os.symlink(os.path.join("..", ".ez2-aib", "user"), str(desk / "AIB 第二大腦"))
        os.symlink("/opt/elsewhere", str(desk / "Notes"))

    @staticmethod
    def _w(path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run(self, *argv):
        import io
        import contextlib
        import uninstall
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = uninstall.main(list(argv), home=str(self.home), package_dir=str(self.pkg))
        return rc, buf.getvalue()

    def test_list_only_touches_nothing(self):
        before = (self.home / ".claude" / "settings.json").read_text(encoding="utf-8")
        rc, out = self._run()
        self.assertEqual(rc, 0)
        for word in ("settings.json", "hooks.json", "aib-menu-pick.json", "bak-now-gate", "Desktop", "aib ext remove foo"):
            self.assertIn(word, out)
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), before)
        self.assertTrue(os.path.islink(str(self.home / "Desktop" / "AIB 第二大腦")))

    def test_yes_removes_only_aib_things(self):
        h = self.home
        rc, out = self._run("--yes")
        self.assertEqual(rc, 0, out)
        cfg = json.loads((h / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["model"], "x")
        self.assertEqual(cfg["hooks"], {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                                        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo also-mine"}]}]})
        self.assertFalse((h / ".claude" / "settings.json.bak-now-gate-20261015-000000-1").exists())
        self.assertFalse((h / ".claude" / "settings.json.bak-claudebot-1").exists())
        self.assertTrue((h / ".claude" / "settings.json.mybackup").exists())
        codex = json.loads((h / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(codex, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo codex-mine"}]}]}})
        self.assertFalse((h / ".grok" / "hooks" / "aib-menu-pick.json").exists())
        self.assertTrue((h / ".grok" / "hooks" / "mine.json").exists())
        self.assertFalse((h / ".copilot" / "hooks" / "aib-auto-distill.json").exists())
        self.assertEqual(json.loads((h / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8")),
                         {"mine": {"PreToolUse": []}})
        self.assertFalse(os.path.lexists(str(h / "Desktop" / "AIB 第二大腦")))
        self.assertTrue(os.path.islink(str(h / "Desktop" / "Notes")))
        self.assertTrue((h / ".ez2-aib" / "user").is_dir())          # 沒加 --all:紀錄留著

    def test_other_aib_keeps_hooks(self):
        link = self.home / ".local" / "bin" / "aib"
        link.parent.mkdir(parents=True)
        link.write_text("#!/bin/sh\n", encoding="utf-8")
        before = (self.home / ".claude" / "settings.json").read_text(encoding="utf-8")
        rc, out = self._run("--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), before)
        self.assertTrue(os.path.islink(str(self.home / "Desktop" / "AIB 第二大腦")))


if __name__ == "__main__":
    unittest.main()
