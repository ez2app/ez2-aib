#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib seat attach <席碼>(對話面板 P5「開終端機」鈕):Terminal 開新視窗附到那一席的 tmux;只看不打字、不經 Touch ID。
AT1 指令只由核心組:tmux -L <socket> attach -t aihub \\; select-window -t <@N>;socket / 窗號有怪字元 ⇒ no-pane 不開
AT2 Mac ⇒ 叫 osascript(假的,記下 JXA)且 doScript 的字串就是那條指令;非 Mac ⇒ no-terminal 但 command 照給(人可以自己貼)
AT3 席位呼叫 ⇒ caller-is-seat;席碼不合 / 找不到 ⇒ no-seat;bash 入口接到
刀(複本):K1 socket 不驗字元 ⇒ AT1 紅
"""
import json
import os
import subprocess
import sys
import tempfile
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

SESS = "11111111-2222-4333-8444-5555550ab12"
CODE = "0ab12"


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-attach-")
        self.tmp = Path(self.td.name)
        self.home = self.tmp / "home"
        self.state = self.home / ".ez2-aib"
        self.inst = self.state / "instances" / "20261020-000000-ab12" / "seats"
        self.inst.mkdir(parents=True)
        self.seat(socket="aihub-20261020-000000-ab12", window_id="@3")
        self.envp = mock.patch.dict(os.environ, {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_LOCALE": "zh-TW",
                                                 "EZ2_AIB_ROOT": str(REPO)})
        self.envp.start()
        for k in ("TMUX", "TMUX_PANE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "AIB_RESUME_SESSION_UUID"):
            os.environ.pop(k, None)
        for m in ("seat_attach", "seat_act", "seat_panel", "seats_dir_lib", "activity_probe"):
            sys.modules.pop(m, None)
        import seat_attach
        self.SAT = seat_attach
        self.calls = []

    def tearDown(self):
        self.envp.stop()
        self.td.cleanup()

    def seat(self, **over):
        rec = {"schema": "aib-seat/v1", "session_uuid": SESS, "cwd": str(self.tmp), "provider": "claude", "started_epoch": 1}
        rec.update(over)
        (self.inst / "w3.json").write_text(json.dumps(rec))

    def fake_run(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    def attach(self, **kw):
        kw.setdefault("env", dict(os.environ))
        kw.setdefault("platform", "darwin")
        kw.setdefault("run", self.fake_run)
        kw.setdefault("osascript", sys.executable)   # 只要「存在」就好,真的呼叫被 fake_run 接走
        return self.SAT.do_attach(CODE, **kw)


class Attach(Box):
    def test_at1_command_and_bad_socket(self):
        res, rc = self.attach()
        self.assertEqual((rc, res["ok"]), (0, True), res)
        self.assertEqual(res["command"], "tmux -L aihub-20261020-000000-ab12 attach -t aihub \\; select-window -t @3")
        for bad in ({"socket": "aihub-x; rm -rf ~"}, {"socket": "../evil"}, {"window_id": "@3 && echo"}, {"window_id": "lobby"}):
            self.seat(**bad)
            res, rc = self.attach()
            self.assertEqual((rc, res["why"]), (4, "no-pane"), (bad, res))
        self.assertEqual(len(self.calls), 1, "壞的一個都沒開")

    def test_at2_terminal(self):
        res, rc = self.attach()
        argv = self.calls[-1]
        self.assertEqual(argv[:3], [sys.executable, "-l", "JavaScript"])
        self.assertIn(json.dumps(res["command"]), argv[-1], argv[-1])
        self.assertIn('Application("Terminal")', argv[-1])
        res, rc = self.attach(platform="linux")
        self.assertEqual((rc, res["why"]), (4, "no-terminal"))
        self.assertTrue(res["command"].startswith("tmux -L"), "非 Mac 也給人可以自己貼的指令")
        log = (self.state / "logs" / "seat-panel-actions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"action": "attach"', log)

    def test_at3_gates_and_entry(self):
        res, rc = self.attach(env=dict(os.environ, CLAUDE_CODE_SESSION_ID="x"))
        self.assertEqual((rc, res["why"]), (3, "caller-is-seat"))
        res, rc = self.SAT.do_attach("zzzzz", env=dict(os.environ), platform="darwin", run=self.fake_run, osascript=sys.executable)
        self.assertEqual((rc, res["why"]), (2, "no-seat"))
        res, rc = self.SAT.do_attach("a/b", env=dict(os.environ), platform="darwin", run=self.fake_run, osascript=sys.executable)
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "attach", "zzzzz", "--json"], capture_output=True, text=True,
                           timeout=120, env=dict(os.environ), stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 2, p.stderr[-300:])
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1])["action"], "attach")


class Knife(unittest.TestCase):
    def test_k1_socket_unchecked(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py",
                        '    if not re.match(r"^aihub(?:-[0-9A-Za-z-]+)?$", sock) or not re.match(r"^@\\d+$", wid):\n        return None, None\n',
                        '    if False:\n        return None, None\n', tests=(name, "test-seat-act-d07f9-1020.py", "test-seat-panel-d07f9-1020.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Attach.test_at1_command_and_bad_socket"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
