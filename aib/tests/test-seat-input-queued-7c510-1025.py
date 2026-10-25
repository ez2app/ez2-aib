#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib seat input --queued(艦長室排隊背景送;@abb89 10-25 規格):只看本機解鎖窗 unlock_left,窗關 ⇒ locked、不彈 Touch ID。
(GUIDE-same-user-ai-threat-model:這是收緊 —— 排隊送的永遠不彈 Touch ID、不開窗、不延長窗;防線仍是 Touch ID,解鎖窗是本機檔只防誤操作)

Q1 沒窗 / 窗過期 + queued ⇒ rc 3 why=locked、Touch ID 0 次、沒送鍵、沒開窗;回 hint(人話);log 帶 queued
Q2 窗內 queued 送 ⇒ 送出、via=unlock-window、until 不變(不延長);非 queued 照舊延長
Q3 本人 Touch ID 開的窗也算(看 unlock_left,不是 window_only 的 remote_left)
Q4 CLI --queued 接到 do_input(queued=True);四語 queued_locked / help_queued 都在
刀(複本):K1 拿掉 queued 擋窗 ⇒ Q1 紅;K2 拿掉 queued 不延長 ⇒ Q2 紅
"""
import importlib.util
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("seat_act_base", str(HERE / "test-seat-act-d07f9-1020.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

CODE, PROMPT = base.CODE, base.PROMPT


class Queued(base.Box):
    def window(self, until_in, idle_s=600, via="touchid"):
        p = self.state / "panel-unlock" / (CODE + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        until = int(time.time()) + until_in
        p.write_text(json.dumps({"v": 1, "seat": CODE, "until": until, "idle_s": idle_s, "at": int(time.time()), "via": via}))
        return p, until

    def until(self):
        return json.loads((self.state / "panel-unlock" / (CODE + ".json")).read_text())["until"]

    def test_q1_locked_no_touchid(self):
        res, rc = self.act(self.SA.do_input, CODE, "排隊的字", screen=PROMPT, queued=True)
        self.assertEqual((rc, res["ok"], res["why"]), (3, False, "locked"), res)
        self.assertTrue(res.get("hint"), res)
        self.window(-5)
        res, rc = self.act(self.SA.do_input, CODE, "排隊的字", screen=PROMPT, queued=True)
        self.assertEqual((rc, res["why"]), (3, "locked"), res)
        self.assertEqual(self.asked, [], "排隊送的永遠不彈 Touch ID")
        self.assertEqual(self.keys(), [], "窗關就一個鍵都不送")
        self.assertEqual(self.SA.unlock_left(CODE), 0, "不准順手開窗")
        log = (self.state / "logs" / "seat-panel-actions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"queued": true', log)

    def test_q2_inside_window_no_extend(self):
        _p, until = self.window(100)
        res, rc = self.act(self.SA.do_input, CODE, "排隊的字", screen=PROMPT, queued=True)
        self.assertEqual((rc, res["ok"], res["via"]), (0, True, "unlock-window"), res)
        self.assertEqual(self.asked, [])
        self.assertTrue(any("send-keys -t @3 -l 排隊的字" in k for k in self.keys()), self.keys())
        self.assertEqual(self.until(), until, "排隊送的不延長窗")
        log = (self.state / "logs" / "seat-panel-actions.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(json.loads(log[-1]).get("queued") is True, log[-1])
        res, rc = self.act(self.SA.do_input, CODE, "真人親打", screen=PROMPT)
        self.assertEqual((rc, res["via"]), (0, "unlock-window"), res)
        self.assertGreater(self.until(), until + 400, "真人親下的照舊延長")
        self.assertNotIn("queued", json.loads((self.state / "logs" / "seat-panel-actions.jsonl").read_text().splitlines()[-1]))

    def test_q3_touchid_window_counts(self):
        self.window(100, via="touchid")
        res, rc = self.act(self.SA.do_input, CODE, "x", screen=PROMPT, queued=True)
        self.assertEqual((rc, res["via"]), (0, "unlock-window"), res)
        self.window(100, via="remote-grant")
        res, rc = self.act(self.SA.do_input, CODE, "y", screen=PROMPT, queued=True)
        self.assertEqual(rc, 0, res)
        self.assertEqual(self.asked, [])

    def test_q4_cli_and_dict(self):
        with mock.patch.object(self.SA, "do_input", return_value=({"ok": True}, 0)) as m:
            rc = self.SA.main(["input", CODE, "--text", "hi", "--queued"], out=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertIs(m.call_args.kwargs.get("queued"), True)
        with mock.patch.object(self.SA, "do_input", return_value=({"ok": True}, 0)) as m:
            self.SA.main(["input", CODE, "--text", "hi"], out=io.StringIO())
        self.assertIs(m.call_args.kwargs.get("queued"), False)
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertIn("{seat}", d["aib.seat.panel.queued_locked"], loc)
            self.assertTrue(d["aib.seat.panel.help_queued"], loc)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py", needle, repl,
                        tests=(name, "test-seat-act-d07f9-1020.py", "test-seat-panel-d07f9-1020.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_queued_not_gated(self):
        self._knife("    elif queued:\n", "    elif False:\n", "Queued.test_q1_locked_no_touchid")

    def test_k2_queued_extends(self):
        self._knife(' and not queued:\n        left = refresh_unlock', ':\n        left = refresh_unlock', "Queued.test_q2_inside_window_no_extend")


if __name__ == "__main__":
    unittest.main()
