#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開席加 dm ⇒ 打開訊息待審區,整台記住(10-29 Andy 選 A + 「整個 aib 之後新開的每一席都要開著」)。
起因:待審區要 dm-queue-policy / feature pending_panel / panel-layout pending_open 三件都開;新艙沒檔 = 關,每艙都得各開一次。

P1 開席參數:最後一個字 dm(第 4 個字以後)/ --dm ⇒ on;--no-dm ⇒ off;不寫 ⇒ 不印;角色就叫 dm(只有 3 個字)不算
A1 on:記整台偏好;這一艙 dm 佇列、pending_panel 打開,明關過的 pending_open 也打開
A2 auto + 偏好開 + 新艙(沒檔)⇒ 自動打開,訊息是 auto 那句
A3 auto + 偏好開 + 這艙明關過 ⇒ 不硬開(kept_off),檔不變
A4 auto + 偏好沒開 ⇒ 什麼都不寫
A5 off ⇒ 偏好改關,不碰艙
A6 方案不是進階 ⇒ 艙不寫,說 not_advanced(on 仍記偏好,升級後下一次開席生效)
A7 沒裝 pending-review ⇒ 開關照寫,說 plugin_missing
A8 main 不管怎樣都回 0(開席不能因為這一步倒)
A9 記憶檔位置 = <狀態根>/state/open-dm-panel.json,跟 aib 腳本「沒記憶檔就不起 python」看的是同一個
D1 四語六句都在;用法那行有 [dm]
刀(複本):K1 拿掉最後一個字 dm 的辨認 ⇒ P1 紅;K2 auto 也硬開 ⇒ A3 紅;K3 不看整台偏好 ⇒ A4 紅
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
AIB = COCKPIT / "aib"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import open_dm_panel as ODP  # noqa: E402

KEYS = ("on", "auto", "off", "not_advanced", "plugin_missing", "no_cabin_home")


class Cabin(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-dmopen-")
        self.addCleanup(self.td.cleanup)
        st = Path(self.td.name) / "st"
        self.cabin = st / "instances" / "20261029-010000-abcd"
        (self.cabin / "state").mkdir(parents=True)
        self.env = {"HOME": self.td.name, "EZ2_AIB_STATE_ROOT": str(st), "EZ2_AIB_INSTANCE": self.cabin.name,
                    "EZ2_AIB_INSTANCE_HOME": str(self.cabin), "EZ2_AIB_LOCALE": "zh-TW"}
        self.plan = "advanced"
        orig = ODP.LP.license_plan
        ODP.LP.license_plan = lambda env=None: self.plan
        self.addCleanup(setattr, ODP.LP, "license_plan", orig)
        self._loc = os.environ.get("EZ2_AIB_LOCALE")
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
        self.addCleanup(self._restore_locale)

    def _restore_locale(self):
        if self._loc is None:
            os.environ.pop("EZ2_AIB_LOCALE", None)
        else:
            os.environ["EZ2_AIB_LOCALE"] = self._loc

    def f(self, name):
        p = self.cabin / "state" / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, name, data):
        (self.cabin / "state" / name).write_text(json.dumps(data), encoding="utf-8")

    def apply(self, mode, present=True):
        return ODP.apply(mode, env=self.env, plugin_present=present)

    def test_a1_on(self):
        self.put("panel-layout.json", {"schema": "aib-panel-layout/v1", "pending_open": False})
        r = self.apply("on")
        self.assertEqual((r["why"], r["pref"]), ("ok", True))
        self.assertTrue(ODP.read_pref(self.env))
        self.assertIs(self.f("dm-queue-policy.json")["enabled"], True)
        self.assertIs(self.f("feature-policy.json")["features"]["pending_panel"], True)
        self.assertIs(self.f("panel-layout.json")["pending_open"], True)
        self.assertIn("自動打開", ODP.message(r))

    def test_a2_auto_new_cabin(self):
        ODP.write_pref(True, self.env)
        r = self.apply("auto")
        self.assertEqual(set(r["opened"]), {"dm_queue", "pending_panel"})
        self.assertIs(self.f("dm-queue-policy.json")["enabled"], True)
        self.assertIs(self.f("feature-policy.json")["features"]["pending_panel"], True)
        self.assertIn("你設過 dm", ODP.message(r))

    def test_a3_auto_respects_explicit_off(self):
        ODP.write_pref(True, self.env)
        self.put("dm-queue-policy.json", {"schema": "aib-dm-queue-policy/v1", "enabled": False})
        self.put("feature-policy.json", {"schema": "aib-feature-policy/v1", "features": {"pending_panel": False}})
        self.put("panel-layout.json", {"schema": "aib-panel-layout/v1", "pending_open": False})
        r = self.apply("auto")
        self.assertEqual(r["opened"], [])
        self.assertEqual(set(r["kept_off"]), {"dm_queue", "pending_panel", "pending_open"})
        self.assertIs(self.f("dm-queue-policy.json")["enabled"], False)
        self.assertIs(self.f("feature-policy.json")["features"]["pending_panel"], False)
        self.assertIs(self.f("panel-layout.json")["pending_open"], False)
        self.assertEqual(ODP.message(r), "")

    def test_a4_auto_pref_off_writes_nothing(self):
        r = self.apply("auto")
        self.assertEqual(r["why"], "pref_off")
        self.assertEqual(sorted(os.listdir(self.cabin / "state")), [])
        self.assertEqual(ODP.message(r), "")

    def test_a5_off(self):
        ODP.write_pref(True, self.env)
        r = self.apply("off")
        self.assertFalse(ODP.read_pref(self.env))
        self.assertEqual(sorted(os.listdir(self.cabin / "state")), [])
        self.assertIn("已取消", ODP.message(r))

    def test_a6_not_advanced(self):
        self.plan = "basic"
        r = self.apply("on")
        self.assertEqual(r["why"], "not_advanced")
        self.assertTrue(ODP.read_pref(self.env), "偏好照記,升級後下一次開席生效")
        self.assertEqual(sorted(os.listdir(self.cabin / "state")), [])
        self.assertIn("進階方案", ODP.message(r))

    def test_a7_plugin_missing(self):
        r = self.apply("on", present=False)
        self.assertEqual(r["why"], "plugin_missing")
        self.assertIs(self.f("feature-policy.json")["features"]["pending_panel"], True)
        self.assertIn("pending-review", ODP.message(r))

    def test_a8_main_never_fails(self):
        orig = os.environ.copy()
        try:
            os.environ.update(self.env)
            self.assertEqual(ODP.main(["bogus"]), 0)
            self.assertEqual(ODP.main(["off"]), 0)
        finally:
            os.environ.clear()
            os.environ.update(orig)

    def test_a9_pref_path_matches_shell_gate(self):
        self.assertEqual(ODP.pref_path(self.env), Path(self.env["EZ2_AIB_STATE_ROOT"]) / "state" / "open-dm-panel.json")
        self.assertIn('[ -f "$(aib_cabin_state_root)/state/open-dm-panel.json" ]', AIB.read_text(encoding="utf-8"))


class Args(unittest.TestCase):
    def dry(self, *args, aib=AIB):
        with tempfile.TemporaryDirectory(prefix="aib-dmopen-home-") as home:
            e = {"PATH": os.environ.get("PATH", ""), "HOME": home, "LANG": "en_US.UTF-8", "EZ2_AIB_LOCALE": "zh-TW"}
            p = subprocess.run(["bash", str(aib), *args, "--dry-run"], capture_output=True, text=True, timeout=120,
                               env=e, stdin=subprocess.DEVNULL)
        return p

    def test_p1_args(self, aib=AIB):
        for args, want in ((("claude", "safe", "dev", "dm"), "dm=on"), (("claude", "safe", "dev", "new", "dm"), "dm=on"),
                           (("claude", "safe", "dev", "--dm"), "dm=on"), (("claude", "safe", "dev", "--no-dm"), "dm=off")):
            p = self.dry(*args, aib=aib)
            self.assertEqual(p.returncode, 0, (args, p.stderr[-600:]))
            self.assertIn("aib: (dry-run) %s" % want, p.stderr, args)
        p = self.dry("claude", "safe", "dev", aib=aib)
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        self.assertNotIn("dm=", p.stderr)


class Dict(unittest.TestCase):
    def test_d1(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in KEYS:
                self.assertTrue(d.get("aib.dmopen." + k), (loc, k))
            self.assertIn("[dm]", d["aib.help.usage"], loc)


def _knife(test, path, old, new, case):
    name = Path(__file__).name
    with knife_copy(REPO, path, old, new, tests=(name,)) as root:
        r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=80, timeout=300)
    test.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
    test.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout[-400:] + r.stderr[-400:])


class Knife(unittest.TestCase):
    def test_k1_no_trailing_dm(self):
        _knife(self, "src/cockpit/aib", '    OPEN_DM=on\n    unset "POS[${#POS[@]}-1]"\n', '    :\n', "Args.test_p1_args")

    def test_k2_auto_forces(self):
        _knife(self, "src/cockpit/open_dm_panel.py", '    if force or q is None or not isinstance(q.get("enabled"), bool):\n',
               "    if True:\n", "Cabin.test_a3_auto_respects_explicit_off")

    def test_k3_ignores_pref(self):
        _knife(self, "src/cockpit/open_dm_panel.py", '    if not out["pref"]:\n', "    if False:\n",
               "Cabin.test_a4_auto_pref_off_writes_nothing")


if __name__ == "__main__":
    unittest.main()
