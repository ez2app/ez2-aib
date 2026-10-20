#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""升版後轉接程式自動重啟(10-20 @abb89 派、會員優化清單 #14)。
起因:10-20 兩台各升三次版,每次都要人手動找 pid 殺,不然轉接程式還跑舊碼。

U1 aib chat restart:status.json 記的 pid 真的是 chat_bridge.py run ⇒ 送 SIGTERM、等它停、rc 0
U2 pid 對不上(別的程式回收了那個號碼)⇒ rc 2、那個程式不碰
U3 沒在跑 ⇒ rc 0、什麼都不做
U4 aib update 的腳本:升級成功後用升級後那份 chat_bridge.py restart(攔 execv 看指令與環境;真下載另有 install 測試)
刀(複本):K1 不送 SIGTERM ⇒ U1 紅;K2 update 腳本少了 restart ⇒ U4 紅
"""
import importlib.util
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

FAKE_BRIDGE = "import signal, sys, time\nsignal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\nwhile True:\n    time.sleep(0.2)\n"


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-restart-")
        self.tmp = Path(self.td.name)
        self.root = self.tmp / "bridge"
        GF.ensure_dirs(self.root)
        (self.tmp / "home").mkdir()
        self.env = {"HOME": str(self.tmp / "home"), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW",
                    "EZ2_AIB_STATE_ROOT": str(self.tmp / "home" / ".ez2-aib")}
        self.procs = []
        self._hub = CB.hub_socket
        CB.hub_socket = lambda env: None        # 不在艙裡:不碰 tmux

    def tearDown(self):
        CB.hub_socket = self._hub
        for p in self.procs:                     # 只收自己起的那幾個
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
        self.td.cleanup()

    def spawn(self, argv):
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        threading.Thread(target=p.wait, daemon=True).start()   # 真實世界是 sh 迴圈 / tmux 收屍;這裡自己收,不然殭屍還答 kill 0
        time.sleep(0.3)
        return p

    def fake_bridge(self):
        script = self.tmp / "chat_bridge.py"
        script.write_text(FAKE_BRIDGE, encoding="utf-8")
        return self.spawn([sys.executable, str(script), "run"])

    def restart(self):
        buf = io.StringIO()
        rc = CB.restart_bridge(self.env, buf, wait_s=5)
        return rc, buf.getvalue()


class Restart(Box):
    def test_u1_restart_real_bridge(self):
        p = self.fake_bridge()
        CB.write_status(self.root, state="published", pid=p.pid, instance=None, since=int(time.time()), at=int(time.time()))
        rc, out = self.restart()
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(p.poll(), "轉接程式應該已經停了")
        self.assertIn(str(p.pid), out)

    def test_u2_pid_mismatch_untouched(self):
        p = self.spawn([sys.executable, "-c", "import time\ntime.sleep(60)"])
        CB.write_status(self.root, state="published", pid=p.pid, instance=None, since=int(time.time()), at=int(time.time()))
        rc, out = self.restart()
        self.assertEqual(rc, 2, out)
        self.assertIsNone(p.poll(), "不是轉接程式的 pid 不准碰")

    def test_u3_not_running(self):
        rc, out = self.restart()
        self.assertEqual(rc, 0)
        self.assertIn("沒在跑", out)
        CB.write_status(self.root, state="stopped", pid=os.getpid(), instance=None, since=None, at=int(time.time()))
        self.assertEqual(self.restart()[0], 0)


class Update(unittest.TestCase):
    def test_u4_update_script_restarts_bridge(self):
        sys.path.insert(0, str(REPO.parent))
        spec = importlib.util.spec_from_file_location("aib_cli_under_test", str(REPO / "cli.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        seen = {}

        def fake_execv(prog, argv):
            seen["argv"] = argv
            seen["bridge"] = os.environ.get("AIB_UPDATE_BRIDGE")
            raise SystemExit(0)
        with mock.patch.object(cli.os, "execv", fake_execv), mock.patch.object(cli.os.path, "isdir", lambda p: True), \
                mock.patch.dict(os.environ, {}, clear=False):
            with self.assertRaises(SystemExit):
                cli._update([])
        script = seen["argv"][2]
        self.assertIn('"$AIB_UPDATE_BRIDGE" restart', script)
        self.assertIn('[ "$rc" -ne 0 ] ||', script.split("restart")[0].rsplit(";", 2)[-2], "升級失敗就不重啟")
        self.assertTrue(seen["bridge"] and seen["bridge"].endswith("src/cockpit/chat_bridge.py") and os.path.isfile(seen["bridge"]))
        self.assertLess(script.index("ensure-all"), script.index("restart"), "先裝 hook 再重啟")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_sigterm(self):
        self._knife("src/cockpit/chat_bridge.py", "    os.kill(pid, signal.SIGTERM)\n    end = time.time() + wait_s\n",
                    "    end = time.time() + wait_s\n", "Restart.test_u1_restart_real_bridge")

    def test_k2_update_without_restart(self):
        self._knife("cli.py", '"$AIB_UPDATE_BRIDGE" restart >&2 || true; ', "", "Update.test_u4_update_script_restarts_bridge")


if __name__ == "__main__":
    unittest.main()
