#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""電源一行把「充電上限停在 80%」誤報成「充電頭太弱」(10-28 22:24 Andy 在 Pro 看到,@aeb40 派 PWR)。
實況:pmset -g batt `80%; AC attached; not charging`;pmset -g ac `Wattage = 140W`(不是 `Wattage (W) = N`)
⇒ 舊版讀不到瓦數印「? W」,又把 not charging 一律講成充電頭太弱。

W1 parse_watts 兩種格式都認:`Wattage (W) = 15`(舊 / 第三方頭)、`Wattage = 140W`(Apple 原廠 / Intel 也見過);沒接 ⇒ 0
W2 parse_not_charging_reason 讀 ioreg ChargerData 的 NotChargingReason;沒這欄 / 空 ⇒ None
W3 power_info:接電沒在充才問 ioreg;充電中 / 用電池 / 非 Mac 都不問;非 Mac 照舊全 None
L1 140 W + 80% + not charging ⇒「充電暫停」,沒有 ⚠、沒有「太弱」
L2 瓦數讀不到 + 80% + ioreg 讀不到 ⇒「沒在充」,不說太弱
L3 15 W + 31% ⇒ ⚠ 充電頭太弱(10-20 那顆手機頭照舊警告)
L4 瓦數讀不到 + 80% + ioreg 有原因 ⇒ 充電暫停
L5 15 W + 80% + ioreg 有原因 ⇒ 仍是太弱(瓦數確實低優先)
L6 140 W + 40% 沒在充 ⇒ ⚠ 沒在充,不說太弱、不說暫停
L7 非 Mac(ac None)⇒ 不印電源行
E1 從 pmset / ioreg 文字一路到那一行(Pro 實況)⇒ 充電暫停
D1 四語字典新兩條都在、參數名一致
刀(複本):K1 parse_watts 回到只認 `(W)` ⇒ W1 紅;K2 拿掉暫停分支 ⇒ L1 紅;K3 讀不到瓦數也說太弱 ⇒ L2 紅;
          K4 power_info 不讀 ioreg ⇒ E1 紅
"""
import json
import os
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_power as CP  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

PMSET_G = " sleep                1 (sleep prevented by powerd)\n"
BATT_80 = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=21823587)\t80%; AC attached; not charging present: true\n"
BATT_CHG = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=21823587)\t55%; charging; 1:02 remaining present: true\n"
BATT_DIS = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=21823587)\t47%; discharging; (no estimate) present: true\n"
AC_140 = " Wattage = 140W\n Current = 4700mA\n Voltage = 28000mV\n"
AC_85_INTEL = "Adapter details:\n AdapterID = 0x7000\n Wattage = 85W\n"
AC_15 = " Wattage (W) = 15\n Current (A) = 3\n"
IOREG_PAUSED = ('+-o AppleSmartBattery  <class AppleSmartBattery>\n'
                '    "ExternalConnected" = Yes\n    "IsCharging" = No\n    "FullyCharged" = No\n'
                '    "ChargerData" = {"ChargingCurrent"=0,"NotChargingReason"=4096,"ChargingVoltage"=13050}\n')
IOREG_NO_FIELD = '+-o AppleSmartBattery\n    "IsCharging" = No\n'


def fake(batt, ac, io=""):
    calls = []

    def pm(*args):
        calls.append(("pmset",) + args)
        return {("-g",): PMSET_G, ("-g", "batt"): batt, ("-g", "ac"): ac}.get(args, "")

    def ior():
        calls.append(("ioreg",))
        return io
    return pm, ior, calls


def line(**kw):
    base = {"ac": True, "charging": False, "pct": None, "watts": None, "not_charging_reason": None}
    base.update(kw)
    out = CB.power_supply_lines(base)
    return out[0] if out else ""


class Parse(unittest.TestCase):
    def test_w1_watts_both_formats(self):
        self.assertEqual(CP.parse_watts(AC_15), 15)
        self.assertEqual(CP.parse_watts(AC_140), 140)
        self.assertEqual(CP.parse_watts(AC_85_INTEL), 85)
        self.assertEqual(CP.parse_watts("No adapter attached.\n"), 0)
        self.assertIsNone(CP.parse_watts("garbage"))

    def test_w2_reason(self):
        self.assertEqual(CP.parse_not_charging_reason(IOREG_PAUSED), 4096)
        self.assertEqual(CP.parse_not_charging_reason('"NotChargingReason" = 0'), 0)
        self.assertIsNone(CP.parse_not_charging_reason(IOREG_NO_FIELD))
        self.assertIsNone(CP.parse_not_charging_reason(""))

    def test_w3_power_info_asks_ioreg_only_when_needed(self):
        with tempfile.TemporaryDirectory(prefix="aib-pwr-") as td:
            pm, io, calls = fake(BATT_80, AC_140, IOREG_PAUSED)
            info = CP.power_info(td, platform="darwin", pmset=pm, ioreg=io)
            self.assertEqual((info["ac"], info["charging"], info["pct"], info["watts"], info["not_charging_reason"]),
                             (True, False, 80, 140, 4096))
            for batt, ac in ((BATT_CHG, AC_140), (BATT_DIS, "No adapter attached.\n")):
                pm, io, calls = fake(batt, ac, IOREG_PAUSED)
                info = CP.power_info(td, platform="darwin", pmset=pm, ioreg=io)
                self.assertIsNone(info["not_charging_reason"])
                self.assertNotIn(("ioreg",), calls, "沒在『接電沒在充』就不用問 ioreg")
            pm, io, calls = fake(BATT_80, AC_140, IOREG_PAUSED)
            info = CP.power_info(td, platform="linux", pmset=pm, ioreg=io)
            self.assertEqual(calls, [], "非 Mac 不跑 pmset / ioreg")
            self.assertEqual((info["ac"], info["watts"], info["not_charging_reason"]), (None, None, None))


class Lines(unittest.TestCase):
    def setUp(self):
        self._loc = os.environ.get("EZ2_AIB_LOCALE")
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"

    def tearDown(self):
        if self._loc is None:
            os.environ.pop("EZ2_AIB_LOCALE", None)
        else:
            os.environ["EZ2_AIB_LOCALE"] = self._loc

    def test_l1_charge_limit_is_paused(self):
        x = line(pct=80, watts=140)
        self.assertIn("充電暫停", x)
        self.assertIn("140 W", x)
        self.assertFalse(x.startswith("⚠"), x)
        self.assertNotIn("太弱", x)

    def test_l2_unknown_watts_not_weak(self):
        x = line(pct=80, watts=None)
        self.assertIn("沒在充", x)
        self.assertNotIn("太弱", x)
        self.assertNotIn("暫停", x)

    def test_l3_weak_charger_still_warned(self):
        x = line(pct=31, watts=15)
        self.assertTrue(x.startswith("⚠") and "15 W" in x and "太弱" in x, x)

    def test_l4_ioreg_reason_means_paused(self):
        x = line(pct=80, watts=None, not_charging_reason=4096)
        self.assertIn("充電暫停", x)

    def test_l5_low_watts_wins_over_reason(self):
        x = line(pct=80, watts=15, not_charging_reason=4096)
        self.assertIn("太弱", x)

    def test_l6_enough_watts_low_battery(self):
        x = line(pct=40, watts=140)
        self.assertTrue(x.startswith("⚠") and "沒在充" in x, x)
        self.assertNotIn("太弱", x)
        self.assertNotIn("暫停", x)

    def test_l7_non_mac_silent(self):
        self.assertEqual(CB.power_supply_lines({"ac": None}), [])

    def test_e1_pro_reality_end_to_end(self):
        with tempfile.TemporaryDirectory(prefix="aib-pwr-") as td:
            pm, io, _calls = fake(BATT_80, "Adapter details:\n (unreadable)\n", IOREG_PAUSED)
            x = CB.power_supply_lines(CP.power_info(td, platform="darwin", pmset=pm, ioreg=io))[0]
        self.assertIn("充電暫停", x)
        self.assertNotIn("太弱", x)


class Dict(unittest.TestCase):
    def test_d1_four_locales(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("aib.chat.status_power_paused", "aib.chat.status_power_idle"):
                self.assertIn("{watts}", d[k], (loc, k))
                self.assertIn("{pct}", d[k], (loc, k))


def _knife(test, path, old, new, case):
    name = Path(__file__).name
    with knife_copy(REPO, path, old, new, tests=(name,)) as root:
        r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
    test.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
    test.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


class Knife(unittest.TestCase):
    def test_k1_watts_old_regex(self):
        _knife(self, "src/cockpit/chat_power.py", r'Wattage\s*(?:\(W\))?\s*=', r'Wattage\s*\(W\)\s*=',
               "Parse.test_w1_watts_both_formats")

    def test_k2_no_paused_branch(self):
        _knife(self, "src/cockpit/chat_bridge.py", "        if high and (", "        if False and (",
               "Lines.test_l1_charge_limit_is_paused")

    def test_k3_unknown_watts_called_weak(self):
        _knife(self, "src/cockpit/chat_bridge.py", "        if known_w and watts < POWER_WEAK_W:\n",
               "        if not known_w or watts < POWER_WEAK_W:\n", "Lines.test_l2_unknown_watts_not_weak")

    def test_k4_power_info_skips_ioreg(self):
        _knife(self, "src/cockpit/chat_power.py", "    reason = parse_not_charging_reason((ioreg or _ioreg)()) if ac",
               "    reason = None if ac", "Lines.test_e1_pro_reality_end_to_end")


if __name__ == "__main__":
    unittest.main()
