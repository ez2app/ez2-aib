#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對話面板核心 C1 / C4 / C5(aib seat input / answer / stop):本人動作一律 Touch ID,AI 席位呼叫擋,送鍵前先看畫面。
(GUIDE-same-user-ai-threat-model 檢查表:防線 = Touch ID;免重驗視窗是本機檔、只防誤操作,說明裡照實寫)

假 tmux(PATH 上的腳本,把 send-keys / load-buffer / paste-buffer 寫進記錄檔)、假 Touch ID(注入函式)、畫面注入。
I1 input 在提示列:Touch ID 那句含席碼 + 前 40 字 ⇒ 過 ⇒ send-keys -l 文字 + Enter;多行走 load-buffer + paste-buffer -p;動作有 log
I2 不在提示列(思考中 / less)⇒ 拒 not-at-prompt,一個鍵都沒送、Touch ID 沒彈
I3 席位正在問(權限框 / 選擇題)⇒ 拒 asking 帶 ask_id
I4 Touch ID 取消 / 逾時 / 不能用 / AIB_TOUCHID=0 / 非 Mac ⇒ 一律拒,沒送鍵
I5 免重驗視窗:過一次後 10 分鐘內第二次不彈;視窗 0 分鐘 ⇒ 每次彈;answer / stop 不吃視窗
I6 呼叫者是席位(帶 CLAUDE_CODE_SESSION_ID / TMUX_PANE)⇒ 拒 caller-is-seat
I7 控制字元 / 空字串 / 太長 ⇒ 拒,沒送鍵
A1 answer 權限框:yes ⇒ 送第一個 key;no ⇒ 最後一個;3 ⇒ 3;--ask-id 對不上 ⇒ stale 不送;沒在問 ⇒ no-ask
A2 answer 選擇題:2 ⇒ 送 2 + Enter;--text 自填 ⇒ 打字 + Enter
S1 stop:Esc;--hard ⇒ C-c;回「現在在跑什麼」
T1 四語字典四條都在;bash 入口接到 seat_act
刀(複本):K1 不驗 Touch ID ⇒ I4 紅;K2 不擋席位呼叫 ⇒ I6 紅;K3 不看提示列 ⇒ I2 紅
"""
import io
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
FAKE_TMUX = "#!/bin/bash\necho \"$@\" >> \"$FAKE_TMUX_LOG\"\nexit 0\n"
PROMPT = "⏺ 好了。\n╭────╮\n│ ❯  │\n╰────╯\n"
THINKING = "  ⠧ Thinking… 6.4s  esc to interrupt\n"
PERMISSION = "  Do you want to proceed?\n❯ 1. Yes\n  2. Yes, and don't ask again\n  3. No, and tell Claude what to do differently (esc)\n"


def rec(t, content, uuid):
    return json.dumps({"type": t, "uuid": uuid, "timestamp": "2026-10-20T10:00:00.000Z", "sessionId": SESS, "isSidechain": False,
                       "message": {"role": "user" if t == "user" else "assistant", "content": content}}, ensure_ascii=False)


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-act-")
        self.tmp = Path(self.td.name)
        self.home = self.tmp / "home"
        self.state = self.home / ".ez2-aib"
        self.cwd = self.tmp / "work"
        self.cwd.mkdir(parents=True)
        inst = self.state / "instances" / "20261020-000000-ab12" / "seats"
        inst.mkdir(parents=True)
        (inst / "w3.json").write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": SESS, "cwd": str(self.cwd), "provider": "claude",
                                                   "socket": "aihub-20261020-000000-ab12", "window_id": "@3", "started_epoch": 1}))
        import activity_probe
        self.proj = self.home / ".claude" / "projects" / activity_probe.escape_cwd(str(self.cwd))
        self.proj.mkdir(parents=True)
        self.jsonl = self.proj / (SESS + ".jsonl")
        self.jsonl.write_text(rec("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}], "a1") + "\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "tmux").write_text(FAKE_TMUX)
        (self.bin / "tmux").chmod(0o755)
        self.log = self.tmp / "tmux.log"
        self.env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_LOCALE": "zh-TW", "EZ2_AIB_ROOT": str(REPO),
                    "FAKE_TMUX_LOG": str(self.log), "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}
        self.envp = mock.patch.dict(os.environ, self.env, clear=False)
        self.envp.start()
        for k in ("TMUX", "TMUX_PANE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "AIB_RESUME_SESSION_UUID", "AIB_TOUCHID"):
            os.environ.pop(k, None)
        for m in ("seat_act", "seat_panel", "seats_dir_lib", "activity_probe", "seat_now"):
            sys.modules.pop(m, None)
        import seat_act
        self.SA = seat_act
        self.asked = []

    def tearDown(self):
        self.envp.stop()
        self.td.cleanup()

    def tid(self, ok=True, why="ok"):
        def ask(reason):
            self.asked.append(reason)
            return {"ok": ok, "why": why}
        return ask

    def keys(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def act(self, fn, *a, **kw):
        kw.setdefault("touchid", self.tid())
        kw.setdefault("touchid_available", lambda: True)
        kw.setdefault("platform", "darwin")
        kw.setdefault("env", dict(os.environ))
        return fn(*a, **kw)


class Input(Box):
    def test_i1_input(self):
        res, rc = self.act(self.SA.do_input, CODE, "你好,幫我看一下 x", screen=PROMPT)
        self.assertEqual((rc, res["ok"], res["via"]), (0, True, "touchid"), res)
        self.assertEqual(len(self.asked), 1)
        self.assertIn("@" + CODE, self.asked[0])
        self.assertIn("你好,幫我看一下 x", self.asked[0])
        self.assertEqual(res["touchid"]["prompt"], self.asked[0])
        ks = self.keys()
        self.assertTrue(any("send-keys -t @3 -l 你好,幫我看一下 x" in k for k in ks), ks)
        self.assertTrue(ks[-1].endswith("send-keys -t @3 Enter"), ks)
        log = (self.state / "logs" / "seat-panel-actions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"action": "input"', log)
        self.log.unlink()
        res, rc = self.act(self.SA.do_input, CODE, "第一行\n第二行", screen=PROMPT)
        self.assertEqual(rc, 0, res)
        ks = self.keys()
        self.assertTrue(any("load-buffer" in k for k in ks) and any("paste-buffer -p" in k for k in ks), ks)

    def test_i2_not_at_prompt(self):
        for scr in (THINKING, "(END)\n"):
            res, rc = self.act(self.SA.do_input, CODE, "hi", screen=scr)
            self.assertEqual((rc, res["why"]), (2, "not-at-prompt"), res)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.keys(), [])

    def test_i3_asking(self):
        res, rc = self.act(self.SA.do_input, CODE, "hi", screen=PERMISSION)
        self.assertEqual((rc, res["why"]), (2, "asking"))
        self.assertTrue(res["ask_id"].startswith("screen:"))
        self.assertEqual(self.keys(), [])

    def test_i4_touchid_refusals(self):
        for kw in ({"touchid": self.tid(False, "cancel")}, {"touchid": self.tid(False, "timeout")}, {"touchid_available": lambda: False},
                   {"platform": "linux"}, {"env": dict(os.environ, AIB_TOUCHID="0")}):
            res, rc = self.act(self.SA.do_input, CODE, "hi", screen=PROMPT, **kw)
            self.assertEqual(rc, 3, (kw, res))
            self.assertTrue(str(res["why"]).startswith("touchid_"), res)
        self.assertEqual(self.keys(), [], "拒了就一個鍵都不送")

    def test_i5_unlock_window(self):
        res, rc = self.act(self.SA.do_input, CODE, "一", screen=PROMPT)
        self.assertEqual(rc, 0)
        res, rc = self.act(self.SA.do_input, CODE, "二", screen=PROMPT)
        self.assertEqual((rc, res["via"]), (0, "unlock-window"), res)
        self.assertEqual(len(self.asked), 1, "10 分鐘內第二次不彈")
        self.assertGreater(res["unlock_left_s"], 0)
        (self.state / "panel-unlock" / "window.json").write_text(json.dumps({"minutes": 0}))
        self.SA.grant_unlock(CODE, minutes=0)
        res, rc = self.act(self.SA.do_input, CODE, "三", screen=PROMPT)
        self.assertEqual(len(self.asked), 2, "視窗 0 ⇒ 每次彈")
        res, rc = self.act(self.SA.do_input, CODE, "四", screen=PROMPT)
        self.assertEqual(len(self.asked), 3)
        (self.state / "panel-unlock" / "window.json").unlink()
        self.act(self.SA.do_input, CODE, "五", screen=PROMPT)
        n = len(self.asked)
        self.act(self.SA.do_stop, CODE)
        self.act(self.SA.do_answer, CODE, "yes", screen=PERMISSION)
        self.assertEqual(len(self.asked), n + 2, "answer / stop 每次都驗")

    def test_i6_caller_is_seat(self):
        for extra in ({"CLAUDE_CODE_SESSION_ID": "x"}, {"CODEX_THREAD_ID": "y"}, {"TMUX_PANE": "%9", "TMUX": "/tmp/x,1,0"}):
            res, rc = self.act(self.SA.do_input, CODE, "hi", screen=PROMPT, env=dict(os.environ, **extra))
            self.assertEqual((rc, res["why"]), (3, "caller-is-seat"), (extra, res))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.keys(), [])

    def test_i7_bad_text(self):
        for bad in ("", "   ", "a\x1b[31mb", "x\x00y", "z" * (64 * 1024 + 1)):
            res, rc = self.act(self.SA.do_input, CODE, bad, screen=PROMPT)
            self.assertEqual(rc, 2, repr(bad[:10]))
        self.assertEqual(self.keys(), [])


class Answer(Box):
    def test_a1_permission(self):
        for choice, want in (("yes", "1"), ("no", "3"), ("3", "3")):
            self.log.unlink(missing_ok=True)
            res, rc = self.act(self.SA.do_answer, CODE, choice, screen=PERMISSION)
            self.assertEqual((rc, res["keys"]), (0, [want]), res)
            self.assertTrue(self.keys()[-1].endswith("send-keys -t @3 " + want), self.keys())
        self.log.unlink(missing_ok=True)
        res, rc = self.act(self.SA.do_answer, CODE, "yes", ask_id="screen:deadbeef", screen=PERMISSION)
        self.assertEqual((rc, res["why"]), (2, "stale"))
        res, rc = self.act(self.SA.do_answer, CODE, "yes", screen=PROMPT)
        self.assertEqual((rc, res["why"]), (2, "no-ask"))
        res, rc = self.act(self.SA.do_answer, CODE, "7", screen=PERMISSION)
        self.assertEqual((rc, res["why"]), (2, "bad-choice"))
        self.assertEqual(self.keys(), [])

    def test_a2_question(self):
        q = {"questions": [{"header": "h", "question": "走哪條?", "options": [{"label": "A"}, {"label": "B"}]}]}
        with self.jsonl.open("a") as fh:
            fh.write(rec("assistant", [{"type": "tool_use", "id": "ask9", "name": "AskUserQuestion", "input": q}], "a2") + "\n")
        res, rc = self.act(self.SA.do_answer, CODE, "2", screen=PROMPT)
        self.assertEqual((rc, res["keys"], res["answered"]), (0, ["2", "Enter"], "ask9:ask"), res)
        self.log.unlink()
        res, rc = self.act(self.SA.do_answer, CODE, "other", free_text="我自己寫", screen=PROMPT)
        self.assertEqual(rc, 0, res)
        self.assertTrue(any("send-keys -t @3 -l 我自己寫" in k for k in self.keys()), self.keys())


class Stop(Box):
    def test_s1_stop(self):
        res, rc = self.act(self.SA.do_stop, CODE)
        self.assertEqual((rc, res["hard"]), (0, False), res)
        self.assertTrue(self.keys()[-1].endswith("send-keys -t @3 Escape"))
        self.assertEqual(res["doing"]["kind"], "tool_call")
        self.assertIn("Esc", self.asked[-1])
        res, rc = self.act(self.SA.do_stop, CODE, hard=True)
        self.assertTrue(self.keys()[-1].endswith("send-keys -t @3 C-c"))
        self.assertIn("Ctrl-C", self.asked[-1])


class Entry(Box):
    def test_t1_dict_and_cli(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("touchid_input", "touchid_answer", "touchid_stop", "touchid_stop_hard"):
                self.assertIn("{seat}", d["aib.seat.panel." + k], (loc, k))
        env = dict(os.environ)
        env["AIB_TOUCHID"] = "0"       # 入口測試:不彈真的 Touch ID,預期被拒 rc 3
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "input", CODE, "--text", "hi"], capture_output=True, text=True,
                           timeout=120, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr[-300:])   # 沒畫面可讀 ⇒ not-at-prompt(假 tmux 回空)在 Touch ID 之前
        d = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(d["action"], "input")


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py", needle, repl, tests=(name, "test-seat-panel-d07f9-1020.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_touchid(self):
        self._knife('        out["via"], why = prove(reason, env, touchid, touchid_available, platform)\n        if why:\n            out["why"] = why\n            log_action(code, "input"',
                    '        out["via"], why = "touchid", None\n        if why:\n            out["why"] = why\n            log_action(code, "input"', "Input.test_i4_touchid_refusals")

    def test_k2_no_caller_gate(self):
        self._knife("    if any(e.get(k) for k in SESSION_ENVS):\n        return True\n", "    if False:\n        return True\n", "Input.test_i6_caller_is_seat")

    def test_k3_no_prompt_gate(self):
        self._knife('    if pend.get("at_prompt") is not True:\n', '    if False:\n', "Input.test_i2_not_at_prompt")


if __name__ == "__main__":
    unittest.main()
