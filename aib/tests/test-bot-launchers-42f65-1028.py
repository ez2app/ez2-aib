#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""bot 收信入口由 aib 自己裝(10-28 Andy,必要功能)。全部在暫存 HOME / 暫存放置資料夾,不碰真的 ~/.local/bin。

L1 新安裝:三支入口(aib-grokbot-mail / aib-claudebot-mail / aib-musebot-mail)都建好、0755、指向這份安裝的 <bot>_mailbox.py、跑得起來
L2 可重跑:第二次全部 ok、不改檔
L3 舊入口是指向失效路徑(舊倉 tools/…)的連結 ⇒ 換成 aib 的入口
L4 不是 aib 寫的一般檔案(例:手放的一行 exec)⇒ 先備份再換;aib 寫的但指向別處 ⇒ 重寫
L5 AIB_BOT_LAUNCHERS=0 ⇒ 什麼都不寫
L6 更新:aib update 的收尾(用假安裝程式跑 cli._update 產出的那段 bash)與自動更新 run_core_update 都會建 / 修入口
L7 新安裝:aib init(pip / pipx 首次安裝後那一步)會建;install.sh 一行安裝在 KEEP≠1 那段會跑 ensure
L8 開席補救:bot-thread-menu 的 _aib_bot_launchers_ensure 會建;試跑不動;connect 綁好後也會補
L9 提示文字是新倉真的存在的指令(aib bot install-launchers),不再叫人跑 tools/aib-install-path
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]          # = aib/
COCKPIT = ROOT / "src" / "cockpit"
TOOL = COCKPIT / "bot_launchers.py"
sys.path.insert(0, str(COCKPIT))
import bot_launchers as bl  # noqa: E402

NAMES = ("aib-grokbot-mail", "aib-claudebot-mail", "aib-musebot-mail")


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-launchers-"))
        self.home = self.td / "home"
        self.home.mkdir()
        self.bin = self.home / ".local" / "bin"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
        self.env.update({"HOME": str(self.home), "EZ2_AIB_LOCALE": "zh-TW", "PYTHONDONTWRITEBYTECODE": "1",
                         "XDG_CACHE_HOME": str(self.td / "cache")})

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def run_tool(self, *args, **extra):
        return subprocess.run([sys.executable, str(TOOL)] + list(args), env=dict(self.env, **extra),
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def assertGood(self, name):
        p = self.bin / name
        self.assertTrue(p.is_file() and not p.is_symlink(), name)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o755, name)
        text = p.read_text()
        self.assertIn(bl.MARK, text)
        self.assertIn(str(COCKPIT / bl.LAUNCHERS[name]), text)
        self.assertEqual(bl.state(name, self.env)[0], "ok")

    def assertAllGood(self):
        for n in NAMES:
            self.assertGood(n)


class Fresh(Base):
    def test_l1_fresh_and_runs(self):
        r = self.run_tool("ensure")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAllGood()
        for n in NAMES:
            self.assertIn("已建立收信入口", r.stdout)
            h = subprocess.run([str(self.bin / n), "--help"], env=self.env, capture_output=True, text=True, timeout=60)
            self.assertEqual(h.returncode, 0, n + h.stderr)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), sorted(NAMES), "不留暫檔")

    def test_l2_idempotent(self):
        self.run_tool("ensure")
        before = {n: ((self.bin / n).stat().st_mtime_ns, (self.bin / n).read_text()) for n in NAMES}
        r = self.run_tool("ensure", "--quiet")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        after = {n: ((self.bin / n).stat().st_mtime_ns, (self.bin / n).read_text()) for n in NAMES}
        self.assertEqual(before, after)
        self.assertEqual(self.run_tool("status").returncode, 0)

    def test_l5_off(self):
        r = self.run_tool("ensure", AIB_BOT_LAUNCHERS="0")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.bin.exists())

    def test_path_bin_override(self):
        other = self.td / "elsewhere"
        r = self.run_tool("ensure", "--only", "aib-grokbot-mail", AIB_PATH_BIN=str(other))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p.name for p in other.iterdir()], ["aib-grokbot-mail"])
        self.assertFalse(self.bin.exists())


class Stale(Base):
    def test_l3_dead_symlink_from_old_repo(self):
        self.bin.mkdir(parents=True)
        (self.bin / "aib-grokbot-mail").symlink_to(self.td / "ez2-ai-b" / "tools" / "aib-grokbot-mail")
        self.assertEqual(bl.state("aib-grokbot-mail", self.env)[0], "stale")
        r = self.run_tool("ensure")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("已改成指向目前這份 aib", r.stdout)
        self.assertIn("symlink", r.stdout)
        self.assertAllGood()

    def test_l4_foreign_file_backed_up(self):
        self.bin.mkdir(parents=True)
        hand = "#!/bin/sh\nexec python3 /somewhere/old/grokbot_mailbox.py \"$@\"\n"
        (self.bin / "aib-grokbot-mail").write_text(hand)
        (self.bin / "aib-grokbot-mail").chmod(0o755)
        r = self.run_tool("ensure")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGood("aib-grokbot-mail")
        backups = list((self.home / ".ez2-aib" / "backup" / "launchers").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), hand)

    def test_l4b_aib_written_but_old_target(self):
        self.bin.mkdir(parents=True)
        old = bl.desired("aib-musebot-mail").replace(str(COCKPIT), str(self.td / "old-checkout"))
        (self.bin / "aib-musebot-mail").write_text(old)
        (self.bin / "aib-musebot-mail").chmod(0o755)
        self.assertEqual(bl.state("aib-musebot-mail", self.env)[0], "stale")
        self.run_tool("ensure")
        self.assertGood("aib-musebot-mail")
        self.assertFalse((self.home / ".ez2-aib" / "backup").exists(), "aib 自己寫的不用備份")

    def test_l4d_repair_only_keeps_other_live_install(self):
        # 開席 / connect 補救:指向另一份還在的 aib ⇒ 不搶;那份不見了 ⇒ 修。安裝 / 更新(不帶 --repair-only)⇒ 一律改成這份
        other = self.td / "other-aib" / "src" / "cockpit"
        other.mkdir(parents=True)
        (other / "grokbot_mailbox.py").write_text("print('other')\n")
        self.bin.mkdir(parents=True)
        f = self.bin / "aib-grokbot-mail"
        f.write_text(bl.desired("aib-grokbot-mail").replace(str(COCKPIT), str(other)))
        f.chmod(0o755)
        before = f.read_text()
        r = self.run_tool("ensure", "--quiet", "--repair-only", "--only", "aib-grokbot-mail")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(f.read_text(), before)
        (other / "grokbot_mailbox.py").unlink()
        self.run_tool("ensure", "--quiet", "--repair-only", "--only", "aib-grokbot-mail")
        self.assertGood("aib-grokbot-mail")
        (other / "grokbot_mailbox.py").write_text("print('other')\n")
        f.write_text(before)
        self.run_tool("ensure", "--quiet", "--only", "aib-grokbot-mail")
        self.assertGood("aib-grokbot-mail")

    def test_l4c_missing_python_and_not_executable(self):
        self.bin.mkdir(parents=True)
        (self.bin / "aib-claudebot-mail").write_text(bl.desired("aib-claudebot-mail", python=str(self.td / "gone-python")))
        (self.bin / "aib-claudebot-mail").chmod(0o755)
        self.assertEqual(bl.state("aib-claudebot-mail", self.env)[0], "stale")
        (self.bin / "aib-grokbot-mail").write_text(bl.desired("aib-grokbot-mail"))
        (self.bin / "aib-grokbot-mail").chmod(0o644)
        self.assertEqual(bl.state("aib-grokbot-mail", self.env)[0], "stale")
        self.run_tool("ensure")
        self.assertAllGood()


class Update(Base):
    def test_l6_aib_update_post_step(self):
        sys.path.insert(0, str(ROOT.parent))
        import aib.cli as cli
        captured = {}

        def fake_execv(path, argv):
            captured["argv"] = argv
            raise SystemExit(0)
        real_isdir = os.path.isdir
        with mock.patch.object(cli.os, "execv", fake_execv), \
                mock.patch.object(cli.os.path, "isdir", lambda p: True if p.endswith(".git") else real_isdir(p)), \
                mock.patch.dict(os.environ, {}, clear=False):
            with self.assertRaises(SystemExit):
                cli._update([])
            upd_env = {k: os.environ[k] for k in ("AIB_UPDATE_PY", "AIB_UPDATE_LAUNCHERS")}
        self.assertEqual(upd_env["AIB_UPDATE_LAUNCHERS"], str(COCKPIT / "bot_launchers.py"))
        installer = self.td / "install.sh"
        installer.write_text("# %s\nexit 0\n" % cli.PUBLIC_MARK)
        argv = list(captured["argv"])
        argv[3] = "file://" + str(installer)        # bash -c <script> <urls> <mark> …
        env = dict(self.env, AIB_UPDATE_HOOK=str(self.td / "none"), AIB_UPDATE_BRIDGE=str(self.td / "none"),
                   AIB_UPDATE_ROOM=str(self.td / "none"), **upd_env)
        r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAllGood()
        # 失敗的安裝程式 ⇒ 不跑收尾
        shutil.rmtree(self.bin)
        installer.write_text("# %s\nexit 7\n" % cli.PUBLIC_MARK)
        r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 7)
        self.assertFalse(self.bin.exists())

    def test_l6_auto_update_post_step(self):
        import auto_update
        ran = []

        def fake_run(argv, fh, env, cwd, timeout):
            ran.append(argv)
            if argv[0] == "/bin/bash":
                return 0
            if argv[1].endswith("bot_launchers.py"):
                return subprocess.run(argv, env=dict(self.env), stdout=fh, stderr=fh, timeout=60).returncode
            return 0
        root = ROOT.parent
        with mock.patch.object(auto_update, "installer_at", lambda r, ref: "exit 0"), \
                mock.patch.object(auto_update, "_open_log", lambda env=None: None):
            rc = auto_update.run_core_update(dict(self.env, EZ2_AIB_STATE_ROOT=str(self.td / "st")), root=root,
                                             ref="v0", run=fake_run)
        self.assertEqual(rc, 0)
        self.assertTrue(any(a[1].endswith("bot_launchers.py") and a[2] == "ensure" for a in ran if len(a) > 2))
        self.assertAllGood()


class Install(Base):
    def test_l7_aib_init(self):
        r = subprocess.run([sys.executable, str(ROOT / "src" / "bootstrap" / "aib_init.py"), "--json",
                            "--state-root", str(self.home / ".ez2-aib")], env=self.env, capture_output=True,
                           text=True, timeout=300, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("launchers", json.loads(r.stdout))
        self.assertAllGood()

    def test_l7_install_sh_runs_ensure(self):
        src = (ROOT.parent / "install.sh").read_text(encoding="utf-8")
        i = src.index('if [ "${KEEP:-0}" != 1 ]; then')
        j = src.index("bot_launchers.py", i)
        k = src.index('"$abin" init', i)
        self.assertLess(j, k)
        self.assertIn('python3 "$_bl" ensure --quiet', src[j:k])
        self.assertNotIn("bot_launchers", src[:i])


class SeatOpen(Base):
    def _menu(self, extra=""):
        script = 'source "%s"; HERE="%s"; T_PY3="%s"; %s _aib_bot_launchers_ensure aib-grokbot-mail' % (
            COCKPIT / "bot-thread-menu.sh", COCKPIT, sys.executable, extra)
        return subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)

    def test_l8_menu_dry_does_nothing(self):
        r = self._menu("DRY=1;")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.bin.exists())

    def test_l8_menu_creates(self):
        r = self._menu()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGood("aib-grokbot-mail")
        self.assertFalse((self.bin / "aib-claudebot-mail").exists(), "只補這一家")

    def test_l8_preflights_call_it(self):
        src = (COCKPIT / "bot-thread-menu.sh").read_text(encoding="utf-8")
        for fn, name in (("aib_grokbot_agent_preflight", "aib-grokbot-mail"),
                         ("aib_claudebot_session_preflight", "aib-claudebot-mail"),
                         ("aib_musebot_preflight", "aib-musebot-mail")):
            body = src[src.index(fn + "() {"):]
            self.assertIn("_aib_bot_launchers_ensure %s" % name, body.split("\n}\n", 1)[0])


class Hints(unittest.TestCase):
    def test_l9_no_dead_command(self):
        for f in ("bot_ready.py",):
            src = (COCKPIT / f).read_text(encoding="utf-8")
            self.assertNotIn("跑 bash tools/aib-install-path", src)
        import bot_ready
        self.assertIn("aib bot install-launchers", bot_ready._hint_mail("/x/aib-grokbot-mail"))
        aib = (COCKPIT / "aib").read_text(encoding="utf-8")
        self.assertIn('"install-launchers"', aib)
        self.assertIn("bot_launchers.py", aib)


if __name__ == "__main__":
    unittest.main()
