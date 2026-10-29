#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib chat status 回報電源(10-20 @abb89 派、會員優化清單 #16 核心這半):電池 / 外接 / 瓦數 / 在不在充。
起因:Pro 13 接了 15 W 手機充電頭 ⇒ pmset 說 'AC Power' 但 not charging,電池照樣往下掉,會員看不出來。

P1 讀 pmset:`31%; charging;` ⇒ charging True、pct 31;`not charging` / `discharging` ⇒ False;`Wattage (W) = 15` ⇒ 15;
   `No adapter attached.` ⇒ 0;讀不到 ⇒ None
P2 power_info 多三欄 charging / pct / watts;用電池時不叫 pmset -g ac(watts 0);非 Mac 三欄都 None
P3 status 那幾行:接電 + 充電中 ⇒ 「電源:外接 65 W,電池 31%」;接電 + 沒在充 ⇒ ⚠ 帶瓦數;用電池 ⇒ 「電池 31%,沒接電」;
   ac 不明(非 Mac)⇒ 不印;原本的「會睡 / 撐著」那行照舊
P4 四語字典三條都在,參數名一致
刀(複本):K1 沒在充不警告(10-28 起砍 chat_power.charge_state)⇒ P3 紅
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_power as CP  # noqa: E402
import dm_global_files as GF  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

PMSET_G = " standby              1\n sleep                1 (sleep prevented by powerd)\n displaysleep 30\n"
BATT_CHG = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=7274595)\t31%; charging; (no estimate) present: true\n"
BATT_NOT = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=7274595)\t31%; AC attached; not charging present: true\n"
BATT_DIS = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=7274595)\t47%; discharging; (no estimate) present: true\n"
AC_15 = " Wattage (W) = 15\n Current (A) = 3\n"
AC_65 = " Wattage (W) = 65\n"
AC_NONE = "No adapter attached.\n"


def fake_pmset(batt, ac):
    calls = []

    def get(*args):
        calls.append(args)
        if args == ("-g",):
            return PMSET_G
        if args == ("-g", "batt"):
            return batt
        if args == ("-g", "ac"):
            return ac
        return ""
    get.calls = calls
    return get


class Parse(unittest.TestCase):
    def test_p1_parse(self):
        self.assertIs(CP.parse_charging(BATT_CHG), True)
        self.assertIs(CP.parse_charging(BATT_NOT), False)
        self.assertIs(CP.parse_charging(BATT_DIS), False)
        self.assertIsNone(CP.parse_charging("Now drawing from 'AC Power'\n"))
        self.assertEqual(CP.parse_pct(BATT_CHG), 31)
        self.assertIsNone(CP.parse_pct(""))
        self.assertEqual(CP.parse_watts(AC_15), 15)
        self.assertEqual(CP.parse_watts(AC_NONE), 0)
        self.assertIsNone(CP.parse_watts("garbage"))

    def test_p2_power_info(self):
        with tempfile.TemporaryDirectory(prefix="aib-power-") as td:
            pm = fake_pmset(BATT_NOT, AC_15)
            info = CP.power_info(td, platform="darwin", pmset=pm)
            self.assertEqual((info["ac"], info["charging"], info["pct"], info["watts"]), (True, False, 31, 15))
            pm = fake_pmset(BATT_DIS, AC_NONE)
            info = CP.power_info(td, platform="darwin", pmset=pm)
            self.assertEqual((info["ac"], info["charging"], info["pct"], info["watts"]), (False, False, 47, 0))
            self.assertNotIn(("-g", "ac"), pm.calls, "用電池時不用問充電頭")
            info = CP.power_info(td, platform="linux", pmset=pm)
            self.assertEqual((info["charging"], info["pct"], info["watts"]), (None, None, None))


class Status(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-power-")
        self.root = Path(self.td.name) / "bridge"
        GF.ensure_dirs(self.root)
        self.home = Path(self.td.name) / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "EZ2_AIB_LOCALE": "zh-TW",
                    "EZ2_AIB_STATE_ROOT": str(self.home / ".ez2-aib")}

    def tearDown(self):
        self.td.cleanup()

    def lines(self, **info):
        base = {"sleeps": True, "sleep_min": 1, "ac": None, "kept_awake": False, "charging": None, "pct": None, "watts": None}
        base.update(info)
        orig = CP.power_info
        CP.power_info = lambda root: base
        try:
            return CB.power_lines(self.env)
        finally:
            CP.power_info = orig

    def test_p3_lines(self):
        ok = self.lines(ac=True, charging=True, pct=31, watts=65)
        self.assertTrue(any("外接 65 W" in x and "31%" in x and not x.startswith("⚠") for x in ok), ok)
        bad = self.lines(ac=True, charging=False, pct=31, watts=15)
        self.assertTrue(any(x.startswith("⚠") and "15 W" in x and "沒在充" in x for x in bad), bad)
        batt = self.lines(ac=False, charging=False, pct=47, watts=0)
        self.assertTrue(any("電池 47%" in x and "沒接電" in x for x in batt), batt)
        none = self.lines(ac=None)
        self.assertFalse(any(x.startswith("電源:") or x.startswith("⚠ 電源:") for x in none), none)
        self.assertTrue(any("1 分鐘" in x for x in none), "原本那行照舊")

    def test_p5_json_and_friends(self):
        """status --json 的 power、好友在線狀態 machine 那格(@74e8b 頁面讀的欄位名)。"""
        rec = CB.status_record(self.env)
        self.assertEqual(set(rec["power"]) >= {"ac", "charging", "pct", "watts", "sleeps", "sleep_min", "kept_awake"}, True, rec["power"])
        now = 1_800_000_000
        cell = {"presence_at": now - 10, "power": {"sleeps": True, "sleep_min": 1, "ac": True, "kept_awake": False, "beat": 900,
                                                    "charging": False, "pct": 31, "watts": 15, "extra": "x"}}
        m = CP.machine_state(cell, now)
        self.assertEqual((m["state"], m["ac"], m["charging"], m["pct"], m["watts"]), ("awake", True, False, 31, 15))
        self.assertNotIn("extra", m)
        bad = CP.clean_power({"charging": "yes", "pct": 150, "watts": -1, "sleeps": True})
        self.assertEqual((bad["charging"], bad["pct"], bad["watts"], bad["sleeps"]), (None, None, None, True))

    def test_p4_dict(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertIn("{watts}", d["aib.chat.status_power_ac"], loc)
            self.assertIn("{pct}", d["aib.chat.status_power_ac"], loc)
            self.assertIn("{watts}", d["aib.chat.status_power_not_charging"], loc)
            self.assertIn("{pct}", d["aib.chat.status_power_on_battery"], loc)


class Knife(unittest.TestCase):
    def test_k1_not_charging_silent(self):
        name = Path(__file__).name
        # 10-28:判法搬到 chat_power.charge_state(艦長室頁面同讀);刀改成「沒在充也當充電中」
        with knife_copy(REPO, "src/cockpit/chat_power.py", '    if info.get("charging") is not False:\n', "    if True:\n",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Status.test_p3_lines"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
