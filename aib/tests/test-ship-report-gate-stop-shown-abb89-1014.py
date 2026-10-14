#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影子續跑說明在畫面上出現兩次(the maintainer 10-14 21:00 截圖;Mini 席 21:03 派)。

災情:Claude 的 Stop 守門(ship_report_gate.check 經 aib-now-gate.py stop)擋下時,擋的訊息本身就含整段說明
⇒ 人在「Stop hook error」區看到一次;AI 照指示又在回覆裡原樣貼一次 ⇒ 兩次,今天幾乎每回合。
同一個對話開的背景子代理跑的 aib test,說明也記成本席 ⇒ 守門要本席貼別人跑的數字。

修法(the maintainer 10-13 要的是「人一定看得到」,工具自己印就達成):Claude Stop 不再擋,改由 hook 用 systemMessage
顯示一次並記 reported(how=hook_shown);AI 已經自己貼了 ⇒ 照記、不再顯示;PostToolUse 已經顯示過 ⇒ 照記、不再顯示;
說明第一行標「哪棵樹跑的」。沒有 Claude Stop 的各家走的 `check --seat` CLI 不變(仍 rc 2 提醒)。

S1–S6 走 aib-now-gate.py stop 真入口(暫存狀態根、AIB_NOW_GATE=0 只關 now 那段、AIB_NOW_GATE_CODE 指定席碼)。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "src" / "policy" / "hooks"
NOW_GATE = HOOKS / "aib-now-gate.py"
GATE = HOOKS / "ship_report_gate.py"
SEAT = "abc12"
LINES = ["── 影子續跑說明 ──", "沿用 3 支、挑掉不跑 0 支、實跑 2 支", "沒有 aib 一支一支跑:約 5 分鐘",
         "這次實際:2 分鐘", "這次省了約 3 分鐘", "主要重跑原因:改了 x.py(2)"]
REQUIRED = LINES[1:4]


class StopShown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "state-root"
        self.explain = self.root / "state" / "shadow-explain"
        self.explain.mkdir(parents=True)
        self.ship = base / "ship"
        self.ship.mkdir()
        self.env = dict(os.environ)
        self.env.update({"HOME": str(base), "EZ2_AIB_STATE_ROOT": str(self.root), "AIB_SHIP_DIR": str(self.ship),
                         "AIB_NOW_GATE": "0", "AIB_NOW_GATE_CODE": SEAT})
        self.env.pop("AIB_SHIP_REPORT_GATE", None)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, **extra):
        d = {"at": time.time(), "kind": "aib test", "reported": False, "pusher": SEAT,
             "savings": LINES, "required": REQUIRED}
        d.update(extra)
        f = self.explain / ("%s.json" % SEAT)
        f.write_text(json.dumps(d, ensure_ascii=False) + "\n", encoding="utf-8")
        return f

    def stop(self, reply, **payload):
        p = {"last_assistant_message": reply, "session_id": "s1"}
        p.update(payload)
        return subprocess.run([sys.executable, str(NOW_GATE), "stop"], input=json.dumps(p, ensure_ascii=False),
                              capture_output=True, text=True, timeout=60, env=self.env)

    def shown(self, proc):
        out = [json.loads(l) for l in proc.stdout.splitlines() if l.strip().startswith("{")]
        return "\n".join(str(o.get("systemMessage") or "") for o in out)

    def rec(self, f):
        return json.loads(f.read_text(encoding="utf-8"))

    def test_S1_unreported_is_shown_by_hook_not_blocked(self):
        f = self.put()
        p = self.stop("做完了,測試全綠。")
        self.assertEqual(p.returncode, 0, "Stop 不再擋(擋的訊息含整段 ⇒ 人看到第一次):%r" % p.stderr[-300:])
        msg = self.shown(p)
        for ln in REQUIRED:
            self.assertIn(ln, msg, "hook 自己用 systemMessage 顯示那段")
        d = self.rec(f)
        self.assertIs(d["reported"], True)
        self.assertEqual(d.get("reported_how"), "hook_shown")

    def test_S2_ai_already_pasted_no_second_show(self):
        f = self.put()
        p = self.stop("測試全綠。\n" + "\n".join(LINES))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.shown(p), "", "AI 已經貼了 ⇒ 不再顯示第二次")
        self.assertIs(self.rec(f)["reported"], True)

    def test_S3_already_shown_by_post_tool_no_second_show(self):
        f = self.put(shown_at=time.time() - 30)
        p = self.stop("做完了。")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.shown(p), "", "PostToolUse 已經顯示過 ⇒ Stop 不再顯示")
        d = self.rec(f)
        self.assertIs(d["reported"], True)
        self.assertEqual(d.get("reported_how"), "tool_shown")

    def test_S4_same_segment_never_twice(self):
        self.put()
        first = self.shown(self.stop("一"))
        second = self.shown(self.stop("二", stop_hook_active=True))
        self.assertIn(REQUIRED[0], first)
        self.assertEqual(second, "", "同一段只顯示一次")

    def test_S5_first_line_says_which_tree(self):
        self.put(tree="/Users/x/wt-other-seat")
        msg = self.shown(self.stop("做完了。"))
        self.assertIn("wt-other-seat", msg.splitlines()[0], "第一行標哪棵樹跑的:%r" % msg[:200])

    def test_S6_cli_check_for_other_providers_unchanged(self):
        """對照:沒有 Claude Stop 的各家走 check --seat,漏了照舊 rc 2 提醒(不是這次要拿掉的那條)。"""
        self.put()
        p = subprocess.run([sys.executable, str(GATE), "check", "--seat", SEAT], input="做完了。",
                           capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(p.returncode, 2, p.stdout[-300:])
        self.assertIn(REQUIRED[0], p.stdout)


class TreeRecorded(unittest.TestCase):
    def test_T1_explain_record_keeps_tree(self):
        sys.path.insert(0, str(REPO / "tools" / "verify"))
        import shadow_explain as SE
        with tempfile.TemporaryDirectory() as d:
            f = SE.record("aib test", LINES, REQUIRED, seat=SEAT, ship_dir=d)
            self.assertIsNotNone(f)
            self.assertEqual(json.loads(Path(f).read_text(encoding="utf-8")).get("tree"), os.getcwd())


if __name__ == "__main__":
    unittest.main()
