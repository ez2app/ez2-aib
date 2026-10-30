#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-sign-approve 也收授權單 LICENSE-<名>-<席>-<MMDD>.sh(10-30 Andy 23:5x 選 1:Ultra 授權走同一個 Touch ID 彈窗)。

L1 讀授權單:PLAN / EXPIRES / BIND=this + aib-license.py sign-entitlement ⇒ job(kind license)
L2 缺 / 壞任一樣(方案不在五級、BIND 不是 this、到期沒時區、沒走 sign-entitlement、檔名格式不對)⇒ 跳過、不彈窗
L3 視窗字由核心組:名稱、方案、到期都在;類別 = 授權簽發(高風險、不開時間窗)
L4 按過 ⇒ 跑暫存複本、紀錄寫狀態根 pending/sign-logs;不查 GitHub 分支頭
L5 取消 ⇒ 不跑;鎖被拿著 ⇒ 不跑
L6 SIGN 腳本照舊(同一支 main 兩種都收)
刀(落下要紅):K1 拿掉 BIND 檢查 ⇒ L2;K2 license_sign 改成可開窗 ⇒ L3;K3 拿掉 sign-entitlement 檢查 ⇒ L2
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tools" / "release"))

import approve_gate as AG  # noqa: E402
import sign_approve as SA  # noqa: E402
import touchid_approve as TA  # noqa: E402

EXP = "2027-10-17T05:36:46+00:00"
LIC = """#!/bin/bash
PLAN="${PLAN:-ultra}"
EXPIRES="%s"
BIND=this
python3 "$W/pk/tools/license/aib-license.py" sign-entitlement "$W/home/license.json" --device "$DID"
echo "✓ 已換上"
""" % EXP
NAME = "LICENSE-plan-ultra-aeb40-1030.sh"


class FakeRun:
    def __init__(self, rc=0, out="✓ 已換上\n"):
        self.calls, self.rc, self.out = [], rc, out

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[0] == "bash":
            self.ran_text = Path(argv[1]).read_text(encoding="utf-8")
        return mock.Mock(returncode=self.rc if argv[0] == "bash" else 0, stdout=self.out, stderr="")

    def bash(self):
        return [c for c in self.calls if c[0] == "bash"]


def no_head(repo, branch):
    raise AssertionError("授權單不該查 GitHub 分支頭")


class Box(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-signlic-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "EZ2_AIB_LOCALE": "en", "HOME": str(self.tmp)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def write(self, text=LIC, name=NAME):
        f = self.tmp / name
        f.write_text(text, encoding="utf-8")
        return f

    def job(self):
        job, why = SA.plan(self.write())
        self.assertIsNone(why)
        return job

    def test_l1_plan(self):
        j = self.job()
        self.assertEqual((j["kind"], j["name"], j["plan"], j["expires"]), ("license", "plan-ultra", "ultra", EXP))

    def test_l2_bad_license_scripts(self):
        bad = {
            "plan": LIC.replace("ultra}", "platinum}"),
            "bind": LIC.replace("BIND=this", "BIND=any"),
            "nobind": LIC.replace("BIND=this\n", ""),
            "tz": LIC.replace(EXP, "2027-10-17T05:36:46"),
            "noexp": LIC.replace('EXPIRES="%s"\n' % EXP, ""),
            "notsigned": LIC.replace("sign-entitlement", "issue-entitlement"),
        }
        for k, text in bad.items():
            with self.subTest(k):
                self.assertEqual(SA.plan(self.write(text)), (None, "no_lic_vars"))
        for name in ("LICENSE-plan-ultra.sh", "LICENSE-plan-ultra-zzzzz-1030.sh", "LICENSE-Plan-aeb40-1030.sh"):
            with self.subTest(name):
                self.assertEqual(SA.plan(self.write(name=name)), (None, "bad_name"))

    def test_l2_main_skips_without_window(self):
        asked = []
        rc = SA.main([str(self.write(LIC.replace("BIND=this", "BIND=all")))], env=self.env, platform="darwin",
                     available=lambda: True, run=FakeRun(), touchid=lambda t: asked.append(t) or {"ok": True})
        self.assertEqual(rc, 1)
        self.assertEqual(asked, [])

    def test_l3_window_text_and_category(self):
        asked = []
        SA.one(self.job(), env=self.env, run=FakeRun(), head_fn=no_head,
               touchid=lambda t: asked.append(t) or {"ok": False, "why": "cancel"})
        r = asked[0]
        for part in ("plan-ultra", "ultra", EXP, "this Mac"):
            self.assertIn(part, r)
        self.assertEqual(r.category, "license_sign")
        self.assertTrue(r.high_risk)
        self.assertEqual(AG.CATEGORIES["license_sign"], {"high_risk": True, "max_window": 0})

    def test_l4_runs_copy_and_logs(self):
        run, asked = FakeRun(), []
        ok, line = SA.one(self.job(), env=self.env, run=run, head_fn=no_head,
                          touchid=lambda t: asked.append(t) or {"ok": True})
        self.assertTrue(ok, line)
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(run.bash()), 1)
        self.assertNotEqual(Path(run.bash()[0][1]).parent, self.tmp, "要跑暫存複本")
        self.assertEqual(run.ran_text, LIC)
        logs = list((self.state / "pending" / "sign-logs").glob("LICENSE-plan-ultra-ultra-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertFalse(Path(run.bash()[0][1]).exists(), "暫存複本跑完要清掉")

    def test_l5_cancel_and_busy(self):
        run = FakeRun()
        ok, _ = SA.one(self.job(), env=self.env, run=run, head_fn=no_head,
                       touchid=lambda t: {"ok": False, "why": "cancel"})
        self.assertFalse(ok)
        with TA.one_at_a_time(AG.lock_path(self.state)):
            ok, _ = SA.one(self.job(), env=self.env, run=run, head_fn=no_head, touchid=lambda t: {"ok": True})
        self.assertFalse(ok)
        self.assertEqual(run.bash(), [])

    def test_l6_sign_script_still_works(self):
        f = self.write("#!/bin/bash\nBRANCH=release/demo-0.1.2\nPIN_SHA=abc1234\n"
                       "git clone git@github.com:ez2app/aib-plugins.git \"$T\"\n", "SIGN-demo-0.1.2-aeb40-1030.sh")
        job, why = SA.plan(f)
        self.assertIsNone(why)
        self.assertNotIn("kind", job)
        asked = []
        SA.one(job, env=self.env, run=FakeRun(), head_fn=lambda r, b: "abc1234" + "0" * 33,
               touchid=lambda t: asked.append(t) or {"ok": False, "why": "cancel"})
        self.assertEqual(asked[0].category, "release_sign")


if __name__ == "__main__":
    unittest.main()
