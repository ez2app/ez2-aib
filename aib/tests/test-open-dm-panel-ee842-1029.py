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
D1 四語七句都在;用法那行有 [dm]
(10-29 #412 R1)
P2 批次 <N>sits dm / new dm / --dm ⇒ dry-run 印 dm=on;exec seats_batch 之前先跑 open_dm_panel --pref-only
A10 auto + 外掛沒裝 / 非進階 ⇒ 安靜(訊息空);on 才說(M2)
A11 auto + 讀不懂的 dm-queue / feature-policy(壞 JSON、"false" 字串、型別錯)⇒ 不寫、檔不變、列 kept_off;on 照開(M4 / L1)
A12 壞的整台偏好檔五種(壞 JSON、[1]、schema 錯、on:"true"、是目錄)⇒ pref_off、艙裡不寫(L2)
A13 總時限:apply 卡住 ⇒ main 在時限內回 0、印一行(M3)
A13 也驗逾時那行走四語字典(R2 L-a),不是寫死英文
D2 同一個東西四語用同一個詞:ja 承認待ちエリア、ko 검토 대기 영역;ja 不夾「艙」(R2 附帶)
A14 --pref-only:只記偏好、不碰艙,訊息是 batch 那句;off 刪掉偏好檔;state/ 是 0700(L3 / L4)
刀(複本):K1 拿掉最後一個字 dm 的辨認 ⇒ P1 紅;K2 auto 也硬開 ⇒ A3 紅;K3 不看整台偏好 ⇒ A4 紅
  K4 auto 把讀不懂當沒設 ⇒ A11 紅;K5 auto 也印外掛 / 方案那句 ⇒ A10 紅;K6 批次不先記偏好 ⇒ P2 紅;K7 不設時限 ⇒ A13 紅
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

KEYS = ("on", "auto", "batch", "off", "not_advanced", "plugin_missing", "no_cabin_home", "timeout")


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
        self.assertFalse(ODP.pref_path(self.env).exists(), "off 刪掉偏好檔,之後開席不再起 python")
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

    def test_a10_auto_quiet(self):
        ODP.write_pref(True, self.env)
        r = self.apply("auto", present=False)
        self.assertEqual(r["why"], "plugin_missing")
        self.assertEqual(ODP.message(r), "", "auto 沒裝外掛要安靜")
        self.plan = "basic"
        r = self.apply("auto")
        self.assertEqual((r["why"], ODP.message(r)), ("not_advanced", ""), "auto 非進階要安靜")
        self.assertIn("進階方案", ODP.message(self.apply("on")))
        self.plan = "advanced"
        self.assertIn("pending-review", ODP.message(self.apply("on", present=False)))

    def test_a11_auto_leaves_unreadable(self):
        ODP.write_pref(True, self.env)
        cases = (('{"schema": "aib-dm-queue-policy/v1", "enabled": "false"}',
                  '{"schema": "aib-feature-policy/v1", "features": {"pending_panel": "no", "task_zone": true}}'),
                 ("{壞", "{壞"),
                 ('{"schema": "aib-dm-queue-policy/v1"}', '{"schema": "aib-feature-policy/v1", "features": [1]}'))
        for dq, fp in cases:
            (self.cabin / "state" / "dm-queue-policy.json").write_text(dq, encoding="utf-8")
            (self.cabin / "state" / "feature-policy.json").write_text(fp, encoding="utf-8")
            r = self.apply("auto")
            self.assertEqual(r["opened"], [], (dq, fp))
            self.assertEqual(set(r["kept_off"]), {"dm_queue", "pending_panel"}, (dq, fp))
            self.assertEqual((self.cabin / "state" / "dm-queue-policy.json").read_text(encoding="utf-8"), dq)
            self.assertEqual((self.cabin / "state" / "feature-policy.json").read_text(encoding="utf-8"), fp)
            self.assertEqual(ODP.message(r), "")
        r = self.apply("on")
        self.assertEqual(set(r["opened"]), {"dm_queue", "pending_panel"}, "on = 真人明講,照開")
        self.assertIs(self.f("dm-queue-policy.json")["enabled"], True)

    def test_a11b_auto_ok_file_without_key(self):
        ODP.write_pref(True, self.env)
        self.put("feature-policy.json", {"schema": "aib-feature-policy/v1", "features": {"task_zone": True}})
        r = self.apply("auto")
        self.assertIn("pending_panel", r["opened"], "檔讀得懂但沒這個鍵 = 沒設過")
        self.assertIs(self.f("feature-policy.json")["features"]["task_zone"], True, "其他功能不動")

    def test_a12_bad_pref_files(self):
        p = ODP.pref_path(self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        for raw in ("{壞", "[1]", '{"schema": "x", "on": true}', '{"schema": "%s", "on": "true"}' % ODP.SCHEMA, None):
            if p.is_dir():
                p.rmdir()
            elif p.exists():
                p.unlink()
            if raw is None:
                p.mkdir()
            else:
                p.write_text(raw, encoding="utf-8")
            r = self.apply("auto")
            self.assertEqual(r["why"], "pref_off", raw)
            self.assertEqual(sorted(os.listdir(self.cabin / "state")), [], raw)

    def test_a13_time_limit(self):
        import time as _t
        orig_apply, orig_lim = ODP.apply, ODP.TIME_LIMIT_S
        ODP.apply = lambda *a, **k: _t.sleep(5)
        ODP.TIME_LIMIT_S = 0.3
        try:
            import contextlib
            import io
            err = io.StringIO()
            t0 = _t.monotonic()
            with contextlib.redirect_stderr(err):
                self.assertEqual(ODP.main(["auto"]), 0)
            self.assertLess(_t.monotonic() - t0, 2.0, "要在時限內放手")
            self.assertIn("待審區這一步超過 0.3 秒", err.getvalue(), "逾時那行走字典(zh-TW)")
        finally:
            ODP.apply, ODP.TIME_LIMIT_S = orig_apply, orig_lim

    def test_a14_pref_only(self):
        r = ODP.apply("on", env=self.env, plugin_present=True, pref_only=True)
        self.assertEqual(r["why"], "pref_only")
        self.assertTrue(ODP.read_pref(self.env))
        self.assertEqual(sorted(os.listdir(self.cabin / "state")), [], "批次只記偏好、不碰艙")
        self.assertIn("這批每一席", ODP.message(r))
        self.assertEqual(ODP.pref_path(self.env).parent.stat().st_mode & 0o777, 0o700)


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

    def test_p2_batch(self, aib=AIB):
        for args in (("claude", "safe", "dev", "2sits", "dm"), ("claude", "safe", "dev", "2sits", "new", "dm"),
                     ("claude", "safe", "dev", "2sits", "--dm")):
            p = self.dry(*args, aib=aib)
            self.assertEqual(p.returncode, 0, (args, p.stderr[-600:]))
            self.assertIn("aib: (dry-run) dm=on (batch", p.stderr, args)
        src = Path(aib).read_text(encoding="utf-8")
        i = src.index('"$HERE/open_dm_panel.py" "$OPEN_DM" --pref-only')
        self.assertLess(i, src.index('exec bash "$HERE/seats_batch.sh" "${BATCH_COUNT}sits"'), "exec seats_batch 之前先記偏好")


class Dict(unittest.TestCase):
    def test_d1(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in KEYS:
                self.assertTrue(d.get("aib.dmopen." + k), (loc, k))
            self.assertIn("[dm]", d["aib.help.usage"], loc)

    def test_d2_same_term(self):
        for loc, term in (("ja", "承認待ちエリア"), ("ko", "검토 대기 영역")):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("on", "auto", "batch", "off", "not_advanced", "plugin_missing", "no_cabin_home", "timeout"):
                self.assertIn(term, d["aib.dmopen." + k], (loc, k))
            if loc == "ja":
                self.assertFalse(any("艙" in d["aib.dmopen." + k] for k in KEYS), "ja 不夾中文「艙」")


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
        _knife(self, "src/cockpit/open_dm_panel.py", "    elif force or unset:\n",
               "    elif True:\n", "Cabin.test_a3_auto_respects_explicit_off")

    def test_k4_auto_overwrites_unreadable(self):
        _knife(self, "src/cockpit/open_dm_panel.py", '    elif force or q_how == "missing":\n',
               '    elif force or q_how != "ok":\n', "Cabin.test_a11_auto_leaves_unreadable")

    def test_k5_auto_talks(self):
        _knife(self, "src/cockpit/open_dm_panel.py", '        return T(why) if mode == "on" else ""',
               "        return T(why)", "Cabin.test_a10_auto_quiet")

    def test_k6_batch_skips_pref(self):
        _knife(self, "src/cockpit/aib", '    "$T_PY3" "$HERE/open_dm_panel.py" "$OPEN_DM" --pref-only </dev/null || true\n',
               "    :\n", "Args.test_p2_batch")

    def test_k7_no_time_limit(self):
        _knife(self, "src/cockpit/open_dm_panel.py", "            signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT_S)\n",
               "            pass\n", "Cabin.test_a13_time_limit")

    def test_k3_ignores_pref(self):
        _knife(self, "src/cockpit/open_dm_panel.py", '    if not out["pref"]:\n', "    if False:\n",
               "Cabin.test_a4_auto_pref_off_writes_nothing")


if __name__ == "__main__":
    unittest.main()
