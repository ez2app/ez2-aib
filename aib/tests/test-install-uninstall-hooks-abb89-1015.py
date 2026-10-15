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


# ───────────────────────────────────────────────────────────── C6
_NO_TOMLLIB = """
import sys, runpy
sys.modules['tomllib'] = None          # 模擬 Python 3.9:import tomllib ⇒ ImportError
sys.argv = ['codex_hook_trust.py', 'ensure']
runpy.run_path(%r, run_name='__main__')
"""


class C6CodexTrustOldPython(TmpCase):
    def _env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AIB_", "EZ2_AIB", "CODEX_", "LC_", "LANG"))}
        env.update(HOME=str(self.tmp), CODEX_HOME=str(self.tmp / ".codex"), EZ2_AIB_STATE_ROOT=str(self.tmp / "state"),
                   EZ2_AIB_LOCALE="en", LANG="C")
        return env

    def _check(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr + r.stdout)
        self.assertIn("3.11", r.stdout)
        self.assertEqual(len([ln for ln in r.stdout.splitlines() if ln.strip()]), 1, r.stdout)

    def test_without_tomllib_one_line_no_traceback(self):
        script = str(SRC / "cockpit" / "codex_hook_trust.py")
        r = subprocess.run([sys.executable, "-c", _NO_TOMLLIB % script], capture_output=True, text=True,
                           env=self._env(), timeout=60)
        self._check(r)

    @unittest.skipUnless(os.path.exists("/usr/bin/python3"), "no system python3")
    def test_real_system_python(self):
        v = subprocess.run(["/usr/bin/python3", "-c", "import sys; print(sys.version_info >= (3, 11))"],
                           capture_output=True, text=True, timeout=60).stdout.strip()
        if v != "False":
            self.skipTest("system python3 already has tomllib")
        r = subprocess.run(["/usr/bin/python3", str(SRC / "cockpit" / "codex_hook_trust.py"), "ensure"],
                           capture_output=True, text=True, env=self._env(), timeout=60)
        self._check(r)

    def test_message_in_all_locales(self):
        for loc, tr in I18N.items():
            self.assertIn("{ver}", tr["codex_trust.result.old_python"], loc)


# ───────────────────────────────────────────────────────────── C9
class C9DoctorNotices(TmpCase):
    def setUp(self):
        super().setUp()
        import aib_doctor
        self.D = aib_doctor

    def test_login_is_note_not_failure(self):
        self.assertEqual(self.D.check_provider_login("claude")["status"], self.D.NOTE)

    def test_not_in_cabin_is_note(self):
        self.assertEqual(self.D.check_cabin_env(str(ROOT), env={})["status"], self.D.NOTE)

    def test_rc_ignores_notes(self):
        import io
        import contextlib
        D = self.D
        orig = D.run_all
        self.addCleanup(setattr, D, "run_all", orig)
        for rows, want in (([D._result("a", D.OK, ""), D._result("b", D.NOTE, "")], 0),
                           ([D._result("a", D.OK, ""), D._result("b", D.MANUAL, "")], 1),
                           ([D._result("a", D.FAIL, "")], 1)):
            D.run_all = lambda *a, _r=rows, **k: _r
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(D.main(["--state-root", str(self.tmp)]), want, rows)

    def _fake_repo(self):
        repo = self.tmp / "repo"
        hooks = repo / "src" / "policy" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "release-tag-gate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (hooks / "install-hooks.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        git(repo, "init", "-q")
        return repo

    def test_release_gate_skipped_for_normal_users(self):
        r = self.D.check_release_gate_hook(str(self._fake_repo()))
        self.assertEqual(r["status"], self.D.NOTE, r)

    def test_release_gate_still_checked_when_in_use(self):
        repo = self._fake_repo()
        (repo / ".git" / "hooks").mkdir(exist_ok=True)
        (repo / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
        r = self.D.check_release_gate_hook(str(repo))
        self.assertNotEqual(r["status"], self.D.NOTE, r)

    def test_no_private_repo_in_user_text(self):
        src = (SRC / "bootstrap" / "aib_doctor.py").read_text(encoding="utf-8")
        self.assertNotIn("沒有 ez2-ai", src)


# ───────────────────────────────────────────────────────────── C13
class C13UpdateHelpAndPipxName(TmpCase):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT.parent))
        import importlib
        self.cli = importlib.import_module("aib.cli")
        orig_execv = os.execv
        self.addCleanup(setattr, os, "execv", orig_execv)

        def boom(*a, **k):
            raise AssertionError("execv called: %r" % (a,))
        os.execv = boom

    def test_update_help_has_no_side_effects(self):
        import io
        import contextlib
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp)
        self.addCleanup(os.environ.__setitem__, "HOME", old_home)
        for flag in ("--help", "-h"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.cli._update([flag])
            self.assertEqual(rc, 0)
            self.assertIn("usage: aib update", buf.getvalue())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_pipx_hint_uses_package_name(self):
        import io
        import contextlib
        pkg = self.tmp / "site-packages" / "aib"
        pkg.mkdir(parents=True)
        orig = self.cli.HERE
        self.addCleanup(setattr, self.cli, "HERE", orig)
        self.cli.HERE = str(pkg)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = self.cli._update([])
        self.assertEqual(rc, 2)
        self.assertIn("pipx upgrade ez2-aib", err.getvalue())
        self.assertNotIn("pipx upgrade aib", err.getvalue())


# ───────────────────────────────────────────────────────────── menu-pick 要自己開(Andy 10-15 裁)
class MenuPickOptIn(TmpCase):
    def setUp(self):
        super().setUp()
        for k, v in (("HOME", str(self.tmp)), ("EZ2_AIB_STATE_ROOT", str(self.tmp / "state")),
                     ("CLAUDE_CONFIG_DIR", None), ("AIB_MENU_PICK", None), ("EZ2_AIB_LOCALE", "en")):
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.__setitem__(k, old) if old is not None
                            else os.environ.pop(k, None))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import menu_pick
        self.mp = menu_pick
        self.settings = self.tmp / ".claude" / "settings.json"

    def _main(self, *argv):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = self.mp.main(list(argv))
        return rc, buf.getvalue()

    def test_new_user_off_by_default(self):
        mp = self.mp
        self.assertFalse(mp.enabled())
        self.assertEqual(mp.ensure(), {"all": "off"})
        for f in (mp.ensure_codex, mp.ensure_agy, mp.ensure_copilot):
            self.assertEqual(f(), "off")
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.tmp / ".codex").exists())
        self.assertEqual(mp.once(seats_dir=str(self.tmp / "none")), [])
        self.assertFalse(mp.trigger(code="abcde", spawn=lambda cmd: self.fail("spawned")))
        self.assertEqual(mp.codex_permission({"tool_name": "Bash"}, known={"abcde"}), "")
        rc, out = self._main("status")
        self.assertIn("off", out)

    def test_on_installs_off_stops(self):
        rc, out = self._main("on")
        self.assertEqual(rc, 0)
        self.assertTrue(self.mp.enabled())
        self.assertIn("menu_pick.py", self.settings.read_text(encoding="utf-8"))
        rc, out = self._main("off")
        self.assertEqual(rc, 0)
        self.assertFalse(self.mp.enabled())
        self.assertIn("menu_pick.py", self.settings.read_text(encoding="utf-8"))     # 掛點留著,只是不做事
        self.assertFalse(self.mp.trigger(code="abcde", spawn=lambda cmd: self.fail("spawned")))

    def test_existing_hook_keeps_working(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"hooks": {"Notification": [{"matcher": "permission_prompt", "hooks": [
            {"type": "command", "command": "test -f /x/src/cockpit/menu_pick.py && python3 /x/src/cockpit/menu_pick.py trigger"}]}]}}),
            encoding="utf-8")
        self.assertTrue(self.mp.enabled())
        self.assertEqual(self.mp.switch_state(), "on")
        self._main("off")
        self.assertFalse(self.mp.enabled())        # 自己關了就是關了,不會再被當成舊裝的打開

    def test_existing_usage_log_keeps_working(self):
        d = self.tmp / "state" / "state" / "menu-pick"
        d.mkdir(parents=True)
        (d / "log.jsonl").write_text("{}\n", encoding="utf-8")
        self.assertTrue(self.mp.enabled())

    def test_env_zero_wins(self):
        self._main("on")
        os.environ["AIB_MENU_PICK"] = "0"
        self.assertFalse(self.mp.enabled())


# ───────────────────────────────────────────────────────────── 桌面捷徑:先問、名字跟語言(Andy 10-15 裁)
class DesktopShortcutAsks(TmpCase):
    def setUp(self):
        super().setUp()
        for k, v in (("EZ2_AIB_DESKTOP_DIR", None), ("EZ2_AIB_LOCALE", "en"), ("CLAUDE_CONFIG_DIR", None)):
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.__setitem__(k, old) if old is not None
                            else os.environ.pop(k, None))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import desktop_shortcut
        self.ds = desktop_shortcut
        self.home = self.tmp / "home"
        self.root = self.home / ".ez2-aib"
        self.root.mkdir(parents=True, mode=0o700)
        self.desk = self.home / "Desktop"
        self.desk.mkdir()
        self.env = {"EZ2_AIB_DESKTOP_DIR": str(self.desk)}

    def _links(self):
        return sorted(p.name for p in self.desk.iterdir() if p.is_symlink())

    def test_no_one_to_ask_skips_with_note(self):
        items = self.ds.ensure_desktop_hub(self.root, env=self.env, ask=None)
        self.assertEqual(self._links(), [])
        self.assertTrue(any("aib init" in (i.get("detail") or "") for i in items), items)

    def test_yes_creates_localized_name(self):
        asked = []
        for loc, name in (("en", "AIB Second Brain"), ("ja", "AIB セカンドブレイン"), ("ko", "AIB 세컨드 브레인"),
                          ("zh-TW", "AIB 第二大腦")):
            os.environ["EZ2_AIB_LOCALE"] = loc
            for p in list(self.desk.iterdir()):
                p.unlink()
            self.ds.ensure_desktop_hub(self.root, env=self.env, ask=lambda q: asked.append(q) or True)
            self.assertEqual(self._links(), [name], loc)
            self.assertIn(name, asked[-1])

    def test_no_creates_nothing(self):
        self.ds.ensure_desktop_hub(self.root, env=self.env, ask=lambda q: False)
        self.assertEqual(self._links(), [])

    def test_existing_shortcut_any_name_not_asked_again(self):
        self.ds.ensure_desktop_hub(self.root, env=self.env, ask=lambda q: False)      # 先建好 user/ 入口
        os.symlink(os.path.join("..", ".ez2-aib", "user"), str(self.desk / "AIB 第二大腦"))
        self.ds.ensure_desktop_hub(self.root, env=self.env, ask=lambda q: self.fail("asked again"))
        self.assertEqual(self._links(), ["AIB 第二大腦"])

    def test_uninstall_finds_localized_shortcut(self):
        import uninstall
        self.ds.ensure_desktop_hub(self.root, env=self.env, ask=lambda q: True)
        _rw, rm = uninstall.traces(self.home)
        self.assertIn(self.desk / "AIB Second Brain", [p for p, _ in rm])

    def test_init_non_interactive_does_not_create(self):
        # aib init 會裝各家 hook(10-15 ensure-all):CLAUDE_CONFIG_DIR / CODEX_HOME 不准帶進去(會繞過暫存 HOME)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_AIB", "LC_", "LANG"))
               and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        env.update(HOME=str(self.home), EZ2_AIB_STATE_ROOT=str(self.root), EZ2_AIB_DESKTOP_DIR=str(self.desk),
                   EZ2_AIB_LOCALE="en", LANG="C")
        r = subprocess.run([sys.executable, str(SRC / "bootstrap" / "aib_init.py")], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(self._links(), [], r.stdout + r.stderr)
        self.assertIn("no one to ask", r.stdout)

    def test_tty_answer_parsing(self):
        import builtins
        import aib_init
        orig = builtins.input
        self.addCleanup(setattr, builtins, "input", orig)
        for ans, want in (("y", True), ("YES", True), ("はい", True), ("", False), ("n", False)):
            builtins.input = lambda q, _a=ans: _a
            self.assertEqual(aib_init.ask_tty("? "), want, ans)


if __name__ == "__main__":
    unittest.main()
