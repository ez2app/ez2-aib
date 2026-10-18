#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""轉接程式健康與自己重起、開通後提醒備份(開發文件第 37.3、37.4 節;10-18 fishmac 單艙:轉接程式停了沒人重起)。

S1 health:沒開 ⇒ off;開著沒當班 ⇒ not_running;當班沒心跳 ⇒ no_relay(還沒發名冊)/ stale_heartbeat(已發);工人全部斷線 ⇒ no_relay
S2 status --json 帶 healthy / why / backup;開著沒當班時文字版有一句警告
S3 sys.chat 窗:轉接程式被停掉(SIGTERM)⇒ 窗格不死、幾秒內自己起一支新的;aib chat off ⇒ 迴圈結束、窗格死
S4 備份提醒:aib chat on 沒備份過 ⇒ 提醒;開通滿 7 天、有主人金鑰、沒備份 ⇒ due;備份後 ⇒ 不再提醒(只記時間,不記檔名)
刀(複本):K1 窗的指令不包迴圈 ⇒ S3 紅
"""
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_bridge as CB  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_hl", str(HERE / "test-chat-bridge-d07f9-1015.py"))
BT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BT)


class Pure(unittest.TestCase):
    def test_s1_health(self):
        base = {"enabled": True, "running": True, "heartbeat": True, "state": "published", "worker": None}
        self.assertEqual(CB.health(dict(base, enabled=False)), ("off", None))
        self.assertEqual(CB.health(dict(base, running=False)), (False, "not_running"))
        self.assertEqual(CB.health(dict(base, heartbeat=False)), (False, "stale_heartbeat"))
        self.assertEqual(CB.health(dict(base, heartbeat=False, state="no_relays")), (False, "no_relay"))
        self.assertEqual(CB.health(dict(base, worker={"offline": True})), (False, "no_relay"))
        self.assertEqual(CB.health(base), (True, None))
        self.assertIn("while :", CB.window_command({}), "窗裡要包迴圈")


class Status(BT.Base):
    def test_s2_status(self):
        self.turn_on()
        rec = json.loads(self.b.cli("status", "--json").stdout)
        self.assertEqual((rec["healthy"], rec["why"]), (False, "not_running"))
        self.assertIn("backup", rec)
        self.assertIn("⚠", self.b.cli("status").stdout)
        self.b.cli("off")
        rec = json.loads(self.b.cli("status", "--json").stdout)
        self.assertEqual(rec["healthy"], "off")

    def test_s4_backup(self):
        r = self.b.cli("on", "--no-venv", "--name", "Tester", "--country", "TW")
        self.assertIn("aib chat backup", r.stdout, "開通就提醒一次")
        env = self.b.env()
        self.assertFalse(CB.backup_state(env)["due"], "剛開通還不用一直提醒")
        old = time.time() - 8 * 86400
        os.utime(CB.switch_path(env), (old, old))
        self.assertTrue(CB.backup_state(env)["due"], "滿 7 天沒備份 ⇒ 提醒")
        self.assertIn("aib chat backup", self.b.cli("status").stdout)
        CB.write_json(str(CB.backup_state_path(env)), {"v": 1, "at": int(time.time())})
        st = CB.backup_state(env)
        self.assertEqual((st["due"], bool(st["at"])), (False, True))
        self.assertNotIn("aib chat backup", self.b.cli("status").stdout)


class Window(BT.Window):
    def pid_of(self):
        return (self.b.status() or {}).get("pid")

    def test_s3_respawn(self):
        self.turn_on()
        self.assertEqual(self.b.cli("ensure-window", "--socket", self.sock).returncode, 0)
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("state") == "no_relays", 30), "沒當班")
        first = self.pid_of()
        os.kill(first, signal.SIGTERM)                        # 像 10-18 那樣只停轉接程式那一支
        self.assertTrue(self.b.wait_for(lambda: self.pid_of() not in (None, first) and CB.pid_alive(self.pid_of()), 30),
                        "停掉後要自己起一支新的(單艙沒有別人接手)")
        self.assertEqual(self.chat_windows()[0][-1], "0", "窗格不准死")
        self.b.cli("off")
        self.assertTrue(self.b.wait_for(lambda: self.chat_windows() and self.chat_windows()[0][-1] == "1", 30),
                        "aib chat off ⇒ 迴圈結束、窗格死")


class Knife(unittest.TestCase):
    # 被砍的那格要 tmux;CI 的 macOS 沒有 tmux ⇒ 那格跳過、rc 0,刀會被誤判「沒砍出紅」(10-18 run 39072757007)。
    # ⇒ 刀跟它砍的那格同一個跳過條件;萬一那格還是跳過(條件以後變了),刀也算跳過,不算過。
    @unittest.skipUnless(shutil.which("tmux"), "沒有 tmux(被砍的 Window.test_s3_respawn 也會跳過)")
    def test_k1_no_loop(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py",
                        '    return "exec sh -c %s" % shlex.quote("while :; do %s run; %s enabled >/dev/null 2>&1 || exit 0; sleep %d; done"\n'
                        '                                         % (me, me, RESTART_GAP))\n',
                        '    return "exec %s run" % me\n',
                        tests=(name, "test-chat-bridge-d07f9-1015.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Window.test_s3_respawn"], cwd=str(root),
                            max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        if r.returncode == 0 and "skipped=" in (r.stdout + r.stderr):
            self.skipTest("被砍的那格跳過了,刀沒得驗")
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
