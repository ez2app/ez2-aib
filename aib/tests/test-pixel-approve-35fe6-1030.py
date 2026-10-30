#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-plugins #320 核心半邊(@aeb40 10-30 交 @35fe6):像素工坊真生圖放行走 approve_gate 一次一按。

P1 hours 只收 1–24 整數:bool / 字串 / 浮點 / 0 / 25 / None ⇒ bad_hours,不問 ready()、不彈窗
P2 不是 Mac ⇒ unavailable、不彈窗(CI 有 Linux;平台注入)
P3 AIB_TOUCHID=0 ⇒ off、不彈窗;叫不出生物辨識 ⇒ unavailable
P4 按過 ⇒ ok;彈窗那句是登記的 site / 類別 pixel_real_gen,字裡看得到 hours(en / zh-TW)
P5 取消 / 失敗 / 怪回傳 ⇒ refused;整台鎖被佔 ⇒ busy(都不算放行)
P6 類別表:pixel_real_gen 高風險、max_window 0、不開時間窗;四語類別名 + 彈窗句都在
刀:K1 拿掉 hours 檢查 ⇒ P1 紅;K2 改成 high_risk False ⇒ P6 紅;K3 拿掉 ready() ⇒ P2 / P3 紅
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))

import approve_gate as AG  # noqa: E402
import pixel_approve as PA  # noqa: E402
import touchid_approve as TA  # noqa: E402


class Env(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aibtest-pixel-approve-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                    "EZ2_AIB_LOCALE": "en", "HOME": str(self.root)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.seen, self.ready_calls = [], []

    def touch(self, res=None):
        def fake(r):
            self.seen.append(r)
            return {"ok": True} if res is None else res
        return fake

    def avail(self, ok=True):
        def fake():
            self.ready_calls.append(1)
            return ok
        return fake

    def ask(self, hours, platform="darwin", env=None, res=None, ok=True):
        return PA.ask(hours, env=dict(self.env, **(env or {})), platform=platform,
                      touchid=self.touch(res), touchid_available=self.avail(ok))


class P1Hours(Env):
    def test_bad_hours_never_reach_ready_or_window(self):
        for h in (True, False, "3", 3.0, 0, -1, 25, None, 10 ** 9):
            self.assertEqual(self.ask(h), {"ok": False, "why": "bad_hours"}, repr(h))
        self.assertEqual(self.seen, [], "壞的 hours 不該彈窗")
        self.assertEqual(self.ready_calls, [], "壞的 hours 在 ready() 之前就擋")

    def test_edges_ok(self):
        for h in (1, 24):
            self.assertEqual(self.ask(h), {"ok": True, "why": "ok"}, h)


class P2P3Ready(Env):
    def test_not_mac_unavailable(self):
        self.assertEqual(self.ask(3, platform="linux"), {"ok": False, "why": "unavailable"})
        self.assertEqual(self.seen, [])

    def test_touchid_off_and_missing(self):
        self.assertEqual(self.ask(3, env={"AIB_TOUCHID": "0"}), {"ok": False, "why": "off"})
        self.assertEqual(self.ask(3, ok=False), {"ok": False, "why": "unavailable"})
        self.assertEqual(self.seen, [])


class P4Window(Env):
    def test_ok_and_sentence(self):
        self.assertEqual(self.ask(7), {"ok": True, "why": "ok"})
        (r,) = self.seen
        self.assertEqual((r.site, r.category), ("pixel_approve.real_gen", "pixel_real_gen"))
        self.assertIn("7 h", str(r))
        self.assertTrue(AG.lock_path(self.root).parent.is_dir(), "鎖放在 EZ2_AIB_STATE_ROOT")

    def test_zh_tw_shows_hours(self):
        self.ask(12, env={"EZ2_AIB_LOCALE": "zh-TW"})
        self.assertIn("12 小時", str(self.seen[-1]))


class P5Refused(Env):
    def test_refused(self):
        for res in ({"ok": False, "why": "cancel"}, {"ok": "yes"}, {}, "ok"):
            self.assertEqual(self.ask(3, res=res), {"ok": False, "why": "refused"}, res)

    def test_busy(self):
        with TA.one_at_a_time(AG.lock_path(self.root)) as held:
            self.assertTrue(held.held)
            self.assertEqual(self.ask(3), {"ok": False, "why": "busy"})
        self.assertEqual(self.seen, [])


class P6Table(unittest.TestCase):
    def test_high_risk_no_window(self):
        spec = AG.CATEGORIES["pixel_real_gen"]
        self.assertEqual((spec["high_risk"], spec["max_window"]), (True, 0))
        self.assertEqual(AG.SITES["pixel_approve.real_gen"]["cat"], "pixel_real_gen")
        self.assertEqual(AG.SITES["pixel_approve.real_gen"]["facts"], {"hours": AG.FULL})

    def test_four_locales(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertTrue(d.get("aib.approve_gate.cat.pixel_real_gen"), loc)
            self.assertIn("{hours}", d.get("aib.pixel.touchid_real_gen", ""), loc)


if __name__ == "__main__":
    unittest.main()
