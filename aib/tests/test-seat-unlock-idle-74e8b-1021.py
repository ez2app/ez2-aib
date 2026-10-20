#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""解鎖改成閒置倒數 + 解鎖時選分鐘(10-21 維護者:「解鎖下指令時沒有時限，真人不下指令之後才倒數結束」)。

(GUIDE-same-user-ai-threat-model 檢查表:防線仍是 Touch ID;免重驗視窗是本機檔、只防誤操作,說明裡照實寫)
U1 閒置倒數：視窗內送出一次 ⇒ until 推回 now + idle_s(快到期也一樣);送失敗不推
U2 --unlock-min 3 ⇒ Touch ID 那句帶「3 分鐘」、記錄 idle_s = 180;超出範圍夾到 0…60
U3 aib seat unlock:彈一次 Touch ID ⇒ 之後打字不彈;開著時再 unlock 不彈;lock ⇒ 下次打字又彈
U4 unlock 擋席位呼叫;Touch ID 取消 / 不能用 ⇒ 拒、沒寫視窗;0 分鐘 ⇒ 拒
U5 過期的視窗不會被推回來(refresh 回 0、下次照彈)
U6 入口:aib seat lock 回 ok;aib seat unlock 在 AIB_TOUCHID=0 ⇒ rc 3
刀(複本):K1 送出後不推 until ⇒ U1 紅;K2 unlock 不驗 Touch ID ⇒ U4 紅
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("seat_act_base_1020", HERE / "test-seat-act-d07f9-1020.py")
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
CODE, PROMPT = BASE.CODE, BASE.PROMPT


class Idle(BASE.Box):
    def rec(self):
        return json.loads((self.state / "panel-unlock" / (CODE + ".json")).read_text(encoding="utf-8"))

    def age(self, left):
        p = self.state / "panel-unlock" / (CODE + ".json")
        r = json.loads(p.read_text(encoding="utf-8"))
        r["until"] = int(time.time()) + left
        p.write_text(json.dumps(r))

    def test_u1_slide(self):
        res, rc = self.act(self.SA.do_input, CODE, "一", screen=PROMPT)
        self.assertEqual((rc, res["unlock_left_s"]), (0, 600), res)
        self.age(5)                                        # 只剩 5 秒
        res, rc = self.act(self.SA.do_input, CODE, "二", screen=PROMPT)
        self.assertEqual((rc, res["via"]), (0, "unlock-window"), res)
        self.assertGreaterEqual(self.rec()["until"], int(time.time()) + 590, "送出一次 ⇒ 閒置倒數重來")
        self.assertEqual(res["unlock_left_s"], 600)
        self.assertEqual(len(self.asked), 1)
        self.age(5)
        res, rc = self.act(self.SA.do_input, CODE, "三", screen=BASE.THINKING)   # 沒送出 ⇒ 不推
        self.assertNotEqual(rc, 0)
        self.assertLess(self.rec()["until"], int(time.time()) + 10)

    def test_u2_minutes(self):
        res, rc = self.act(self.SA.do_input, CODE, "嗨", screen=PROMPT, minutes=3)
        self.assertEqual(rc, 0, res)
        self.assertIn("3 分鐘", self.asked[0])
        self.assertEqual((self.rec()["idle_s"], res["unlock_left_s"]), (180, 180))
        self.assertEqual(self.SA.clamp_minutes(999), 60)
        self.assertEqual(self.SA.clamp_minutes(-4), 0)
        self.assertIsNone(self.SA.clamp_minutes("x"))

    def test_u3_unlock_lock(self):
        res, rc = self.act(self.SA.do_unlock, CODE, 15)
        self.assertEqual((rc, res["ok"], res["unlock_left_s"], res["minutes"]), (0, True, 900, 15), res)
        self.assertIn("15 分鐘", self.asked[0])
        res, rc = self.act(self.SA.do_input, CODE, "嗨", screen=PROMPT)
        self.assertEqual((rc, res["via"]), (0, "unlock-window"))
        res, rc = self.act(self.SA.do_unlock, CODE, 5)
        self.assertEqual((rc, res["via"], res["unlock_left_s"]), (0, "unlock-window", 300))
        self.assertEqual(len(self.asked), 1, "開著時不重彈")
        res, rc = self.SA.do_lock(CODE)
        self.assertEqual((rc, res["was_open"]), (0, True))
        self.act(self.SA.do_input, CODE, "再一句", screen=PROMPT)
        self.assertEqual(len(self.asked), 2, "上鎖後又要 Touch ID")

    def test_u4_unlock_gates(self):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID="x")
        res, rc = self.act(self.SA.do_unlock, CODE, 10, env=env)
        self.assertEqual((rc, res["why"]), (3, "caller-is-seat"))
        for tid in (self.tid(False, "cancel"), self.tid(False, "timeout")):
            res, rc = self.act(self.SA.do_unlock, CODE, 10, touchid=tid)
            self.assertEqual(rc, 3, res)
            self.assertTrue(res["why"].startswith("touchid_"))
        res, rc = self.act(self.SA.do_unlock, CODE, 10, touchid_available=lambda: False)
        self.assertEqual((rc, res["why"]), (3, "touchid_unavailable"))
        self.assertFalse((self.state / "panel-unlock" / (CODE + ".json")).exists(), "沒過就不能有視窗")
        res, rc = self.act(self.SA.do_unlock, CODE, 0)
        self.assertEqual((rc, res["why"]), (2, "zero-minutes"))

    def test_u5_expired_not_revived(self):
        self.act(self.SA.do_input, CODE, "一", screen=PROMPT)
        self.age(-1)
        self.assertEqual(self.SA.refresh_unlock(CODE), 0)
        self.act(self.SA.do_input, CODE, "二", screen=PROMPT)
        self.assertEqual(len(self.asked), 2)

    def test_u6_cli(self):
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "lock", CODE, "--json"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-300:])
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1])["action"], "lock")
        env = dict(os.environ, AIB_TOUCHID="0")
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "unlock", CODE, "--min", "5", "--json"], capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr[-300:])
        self.assertTrue(json.loads(p.stdout.strip().splitlines()[-1])["why"].startswith("touchid"))
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertIn("{min}", d["aib.seat.panel.touchid_unlock"])
            self.assertIn("{seat}", d["aib.seat.panel.touchid_unlock"])
            self.assertIn("{min}", d["aib.seat.panel.touchid_unlock_tail"])


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py", needle, repl, tests=(name, "test-seat-act-d07f9-1020.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_slide(self):
        self._knife("        left = refresh_unlock(code, env) or left", "        left = left", "Idle.test_u1_slide")

    def test_k2_unlock_no_touchid(self):
        self._knife('        out["via"], why = prove(reason, env, touchid, touchid_available, platform)\n        if why:\n            out["why"] = why\n            log_action(code, "unlock"',
                    '        out["via"], why = "touchid", None\n        if why:\n            out["why"] = why\n            log_action(code, "unlock"', "Idle.test_u4_unlock_gates")


if __name__ == "__main__":
    unittest.main()
