#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開席前先查那一家的命令列工具有沒有裝(10-14 公開版新用戶走查第 2 條)。

以前:claude 沒裝,`aib claude safe dev` 照樣開出整個駕駛艙,席位當場死,訊息寫「這是 ED.4 的前置條件:provider 是
訂閱 OAuth CLI」,最後還印 `claude --resume <uuid>` 叫人接回一個根本沒裝的工具;--dry-run 也照樣 rc 0。
M1 沒裝 claude:--dry-run ⇒ 照印計畫、rc 0,另印一行「還沒裝 Claude Code」提醒(10-15 整合 #43 #48 時 @abb89 定:dry-run 不開席就不擋)
M2 沒裝 claude:真開 ⇒ rc 6、同一句;一個 tmux 伺服器都沒起
M3 有裝(PATH 上放一支假的 claude):--dry-run 照舊印計畫、rc 0(不能擋到有裝的人)
M4 codex 只有 ez2-codex(本機別名的真身)也算有裝
M5 provider-launch.sh 自己找不到 CLI:rc 6、說怎麼裝、沒有 ED.4 / OAuth 字樣
M6 開席那一行:provider 結束碼 6(沒裝)時不跑 activity_probe --resume-command(不印 --resume)
刀(複本):K1 拿掉開席前檢查 ⇒ M1(提醒)、M2 紅;K2 檢查不認 ez2-codex ⇒ M4 紅;K3 拿掉 rc 6 跳過 ⇒ M6 紅
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

CLI = ROOT / "cli.py"
LAUNCH = ROOT / "src" / "cockpit" / "provider-launch.sh"
TOOLS = ("python3", "git", "tmux", "bash", "env", "uname", "date", "mktemp", "sed", "awk", "grep", "tr", "cat",
         "head", "tail", "cut", "dirname", "basename", "id", "ps", "sort", "wc", "mkdir", "rm", "ln", "readlink")


def real_tool(name):
    """PATH 上第一支「不是墊片」的 name。
    在 `aib test`(影子續跑追蹤器)底下,PATH 最前面是 tools/verify/bash-shim/,裡面的 bash/cat/head/tail 是
    `exec "$(dirname "$0")/_shim" …` 的墊片:連結進我們自己的 bin 後 dirname 變成這個 bin,找不到 _shim(rc 126)。
    ⇒ 跳過旁邊有 _shim 的那一支,連真的工具。"""
    for d in os.environ.get("PATH", os.defpath).split(os.pathsep):
        p = os.path.join(d or ".", name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            if os.path.exists(os.path.join(os.path.dirname(os.path.realpath(p)), "_shim")):
                continue
            return p
    return None


class MissingCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-seat-cli-", dir=aib_tmp_root())
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.tmp, "proj")
        self.bin = os.path.join(self.tmp, "bin")
        self.sock = tempfile.mkdtemp(prefix="at.", dir="/tmp")   # tmux socket 路徑有長度上限,不放深層暫存
        for d in (self.home, self.proj, self.bin):
            os.makedirs(d)
        for name in TOOLS:   # 只放基本工具;刻意不放任何一家 AI 的指令
            real = real_tool(name)
            if real:
                os.symlink(real, os.path.join(self.bin, name))
        subprocess.run(["git", "init", "-q", self.proj], check=True, capture_output=True)
        self.env = {"HOME": self.home, "PATH": self.bin + ":/usr/bin:/bin", "TERM": "xterm-256color",
                    "LANG": "zh_TW.UTF-8", "EZ2_AIB_LOCALE": "zh-TW", "TMUX_TMPDIR": self.sock,
                    "TMPDIR": self.tmp + "/", "AIB_NO_ATTACH": "1", "USER": os.environ.get("USER", "u"),
                    "EZ2_AIB_HOME": os.path.join(self.home, ".ez2-aib"),
                    "EZ2_COCKPIT_STATE_DIR": os.path.join(self.home, ".ez2-aib", "cockpit")}

    def tearDown(self):
        for s in Path(self.sock).glob("tmux-*/*"):
            subprocess.run(["tmux", "-S", str(s), "kill-server"], capture_output=True, env=self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.sock, ignore_errors=True)

    def stub(self, name):
        p = Path(self.bin, name)
        p.write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
        p.chmod(0o755)

    def aib(self, *a):
        p = subprocess.run([sys.executable, str(CLI), *a], env=self.env, cwd=self.proj, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=180)
        return p.returncode, p.stdout + p.stderr

    def assert_missing_msg(self, out, name, login):
        self.assertIn("還沒裝 %s" % name, out)
        self.assertIn("install.sh | bash -s -- --tools", out)
        self.assertIn(login, out)
        self.assertNotIn("ED.4", out)
        self.assertNotIn("OAuth", out)

    def sockets(self):
        return [s for s in Path(self.sock).glob("tmux-*/*")]

    def test_m1_dry_run_previews_with_reminder(self):
        # 10-15 整合時 @abb89 定(配合 #43 #48 的 dry-run 測試):--dry-run 不開任何席 ⇒ 不因沒裝而拒絕;照印計畫、rc 0,stderr 多一行「還沒裝」提醒。
        rc, out = self.aib("claude", "safe", "dev", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("還沒裝 Claude Code", out)
        self.assertIn("respawn-pane", out)
        self.assertNotIn("ED.4", out)

    def test_m2_real_open_blocks(self):
        rc, out = self.aib("claude", "safe", "dev")
        self.assertEqual(rc, 6, out)
        self.assert_missing_msg(out, "Claude Code", "claude")
        self.assertEqual(self.sockets(), [], out)

    def test_m3_installed_still_opens(self):
        self.stub("claude")
        rc, out = self.aib("claude", "safe", "dev", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("還沒裝", out)
        self.assertIn("respawn-pane", out)

    def test_m4_codex_alias_counts(self):
        self.stub("ez2-codex")
        rc, out = self.aib("codex", "safe", "dev", "--dry-run")
        self.assertNotIn("還沒裝", out)
        self.assertEqual(rc, 0, out)

    def test_m5_provider_launch_message(self):
        p = subprocess.run(["bash", str(LAUNCH), "grok", "safe", "dev", self.proj], env=self.env, cwd=self.proj,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 6, out)
        self.assert_missing_msg(out, "Grok", "grok login")

    def test_m6_no_resume_hint_when_missing(self):
        self.stub("claude")
        rc, out = self.aib("claude", "safe", "dev", "--dry-run")
        self.assertEqual(rc, 0, out)
        line = next(l for l in out.splitlines() if "respawn-pane" in l)
        guard = line.find("aib_provider_rc\\\"\\ =\\ 6\\ \\]\\ \\|\\|")
        probe = line.find("activity_probe.py")
        self.assertGreater(guard, -1, line)
        self.assertLess(guard, probe, line)


if __name__ == "__main__":
    unittest.main()
