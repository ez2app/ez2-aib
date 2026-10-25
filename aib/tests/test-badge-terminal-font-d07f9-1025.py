#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""AI 符號:字型檔裝了,但 Apple Terminal 設定檔字型畫不出 ⇒ 老實退字母(10-25 Mac Pro 13 / @ae6a1;Andy「預設優化」)。

B1 外層終端機:EZ2_AIB_OUTER_TERM 優先;在 tmux 裡讀 tmux 全域 TERM_PROGRAM;不在 tmux 讀自己的(tmux 本身不算)
B2 Apple Terminal 設定檔字型畫不出 ⇒ icon_available 全 False、missing_reason=terminal_font、理由寫設定檔與字型名
B3 畫得出 / 不是 Apple Terminal / 非 Mac / 關掉檢查 / 查詢失敗 / 回傳形狀不對 ⇒ 維持只看字型檔(不誤判)
B4 字型檔本身就缺 ⇒ missing_reason=font_missing(先講字型檔);非 TTY / letters ⇒ 不查終端機
B5 渲染器:missing_reason=terminal_font ⇒ fallback_reason 照名字記;不認得的名字退回 font_missing
B6 aib doctor:畫得出 ⇒ ok;終端機字型畫不出 ⇒ note + 設定檔名 + 怎麼改;字型檔缺 ⇒ note + 怎麼裝;設 letters ⇒ ok;四語字典
B7 偏好檔 / 字型夾沒改 ⇒ 不重查:行程內(採集端 5 秒一輪)+ 狀態根檔案(狀態列每次刷新是新行程)
B8(#359 r1)查不到也記(只叫一次 osascript)、偏好檔變了才重查、壞快取不採用、命中快取連 tmux 都不叫
刀(複本):K1 不查終端機字型 ⇒ B2 紅;K2 查詢失敗當畫不出 ⇒ B3 紅;K3 失敗不記快取 ⇒ B8 紅
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "render", REPO / "src" / "bootstrap", HERE):
    sys.path.insert(0, str(_p))
import aib_doctor as D  # noqa: E402
import engine_badge_mode as M  # noqa: E402
import render_lib as R  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ICONS = R.ENGINE_ICONS


def term(covered, profile="Basic", font="SFMono-Regular", calls=None, rc=0, raw=None):
    """假 osascript:回每個字碼 covered(True / False)。calls 收到 argv。"""
    def run(argv):
        if calls is not None:
            calls.append(argv)
        if argv[:1] == ["tmux"]:
            return 0, "TERM_PROGRAM=Apple_Terminal\n"
        n = len(argv) - 5
        out = raw if raw is not None else json.dumps(
            [{"key": "Default Window Settings", "profile": profile, "font": font, "covered": [covered] * n}])
        return rc, out
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-badge-")
        self.font = os.path.join(self.td.name, "X-Regular.ttf")
        self.env = {"HOME": self.td.name, "EZ2_AIB_OUTER_TERM": "Apple_Terminal", "EZ2_AIB_LOCALE": "zh-TW"}
        self.all_cps = {ord(c) for c in ICONS.values()}

    def tearDown(self):
        self.td.cleanup()

    def inputs(self, runner, env=None, have_font=True, **kw):
        """字型檔:have_font ⇒ 假裝字型檔什麼字都有"""
        paths = [self.font] if have_font else []
        orig = M.ttf_codepoints
        M.ttf_codepoints = lambda p: set(self.all_cps)
        try:
            return M.badge_inputs(self.env if env is None else env, icons=ICONS, tty=kw.pop("tty", True), paths=paths,
                                  runner=runner, platform=kw.pop("platform", "darwin"))
        finally:
            M.ttf_codepoints = orig


class Detect(Base):
    def test_b1_outer_terminal(self):
        self.assertEqual(M.outer_terminal({"EZ2_AIB_OUTER_TERM": "iTerm.app", "TMUX": "x"}), "iTerm.app")
        seen = []
        self.assertEqual(M.outer_terminal({"TMUX": "/tmp/x", "TERM_PROGRAM": "tmux"}, term(True, calls=seen)), "Apple_Terminal")
        self.assertEqual(seen[0], ["tmux", "show-environment", "TERM_PROGRAM"], "先看這個 session(之後從別的終端機接上)")
        g = lambda a: (0, "TERM_PROGRAM=iTerm.app\n") if "-g" in a else (1, "")   # noqa: E731
        self.assertEqual(M.outer_terminal({"TMUX": "/tmp/x"}, g), "iTerm.app", "session 沒有 ⇒ 退全域")
        self.assertEqual(M.outer_terminal({"TMUX": "/tmp/x"}, lambda a: (1, "")), None)
        self.assertEqual(M.outer_terminal({"TMUX": "/tmp/x"}, lambda a: (0, "-TERM_PROGRAM\n")), None)
        self.assertEqual(M.outer_terminal({"TERM_PROGRAM": "WezTerm"}), "WezTerm")
        self.assertIsNone(M.outer_terminal({"TERM_PROGRAM": "tmux"}))
        self.assertIsNone(M.outer_terminal({}))

    def test_b2_terminal_font_cannot_draw(self):
        try:
            b = self.inputs(term(False))
            self.assertEqual(b["mode"], "both")
            self.assertFalse(any(b["icon_available"].values()), b)
            self.assertEqual(b["missing_reason"], "terminal_font")
            self.assertIn("Basic", b["reason"])
            self.assertIn("SFMono-Regular", b["reason"])
            self.assertIn("U+EC82", b["reason"])
            self.assertNotIn("字型缺字", b["reason"], "字型檔沒缺就不講字型檔")
        except Exception as exc:  # noqa: BLE001  刀砍出來的要是 AssertionError
            self.fail("%s: %s" % (type(exc).__name__, exc))

    def test_b3_no_false_alarm(self):
        try:
            for runner, env, plat, why in (
                    (term(True), None, "darwin", "畫得出"),
                    (term(False), dict(self.env, EZ2_AIB_OUTER_TERM="iTerm.app"), "darwin", "iTerm2 有備用字型"),
                    (term(False), None, "linux", "非 Mac"),
                    (term(False), dict(self.env, EZ2_AIB_TERM_FONT_CHECK="0"), "darwin", "關掉檢查"),
                    (term(False, rc=1), None, "darwin", "osascript 失敗"),
                    (term(False, raw="not json"), None, "darwin", "回傳不是 JSON"),
                    (term(False, raw="[]"), None, "darwin", "沒有設定檔"),
                    (term(False, raw='[{"covered": [false]}]'), None, "darwin", "字碼數對不上")):
                b = self.inputs(runner, env=env, platform=plat)
                self.assertTrue(all(b["icon_available"].values()), why)
                self.assertIsNone(b["missing_reason"], why)
                self.assertEqual(b["reason"], "", why)
        except Exception as exc:  # noqa: BLE001
            self.fail("%s: %s" % (type(exc).__name__, exc))

    def test_b4_font_file_first_and_no_probe_when_not_needed(self):
        calls = []
        b = self.inputs(term(False, calls=calls), have_font=False)
        self.assertEqual(b["missing_reason"], "font_missing")
        self.assertFalse(any(b["icon_available"].values()))
        self.assertEqual(calls, [], "字型檔都沒有就不用問終端機")
        b = self.inputs(term(False, calls=calls), tty=False)
        self.assertEqual((b["mode"], calls), ("letters", []), "非即時 TTY 鎖字母、不查")
        b = self.inputs(term(False, calls=calls), env=dict(self.env, EZ2_AIB_ENGINE_BADGE="letters"))
        self.assertEqual((b["mode"], calls), ("letters", []))

    def test_b5_renderer_names_reason(self):
        cl = "claude"
        r = R.resolve_engine_badge(cl, mode="both", icon_available=False, missing_reason="terminal_font")
        self.assertEqual((r["mode"], r["fallback_reason"]), ("letters", "terminal_font"))
        r = R.resolve_engine_badge(cl, mode="both", icon_available=False, missing_reason="<x>")
        self.assertEqual(r["fallback_reason"], "font_missing")
        r = R.resolve_engine_badge(cl, mode="both", icon_available=False)
        self.assertEqual(r["fallback_reason"], "font_missing")
        r = R.resolve_engine_badge(cl, mode="both", icon_available=True, missing_reason="terminal_font")
        self.assertIsNone(r["fallback_reason"])

    def test_b8_failure_cached_and_bad_cache_ignored(self):
        """#359 r1 M:查不到(rows 空)也記,不然狀態列每次刷新都叫 osascript;L2 壞快取不採用"""
        calls = []

        def empty(argv):
            calls.append(argv)
            return (0, "TERM_PROGRAM=Apple_Terminal\n") if argv[0] == "tmux" else (0, "[]")
        old_run, M._run = M._run, empty
        M._term_cache.clear()
        st = os.path.join(self.td.name, "st8")
        env = dict(self.env, EZ2_AIB_STATE_ROOT=st)
        try:
            for _ in range(5):
                M._term_cache.clear()                      # 每次都像新行程(狀態列)
                self.assertIsNone(M.terminal_font_check({0xEC82}, env, platform="darwin")["ok"])
            self.assertEqual(len([c for c in calls if c[0] == "osascript"]), 1, "失敗也記 ⇒ 只查一次")
            prefs = os.path.join(self.td.name, "Library", "Preferences")
            os.makedirs(prefs, exist_ok=True)
            with open(os.path.join(prefs, "com.apple.Terminal.plist"), "w") as fh:
                fh.write("x")                              # 偏好檔變了 ⇒ 重查
            M._term_cache.clear()
            M.terminal_font_check({0xEC82}, env, platform="darwin")
            self.assertEqual(len([c for c in calls if c[0] == "osascript"]), 2)
            cache = os.path.join(st, "state", M.TERM_CACHE_FILE)
            rec = json.load(open(cache))
            rec["res"] = {"ok": False}                     # 少欄位的壞快取
            json.dump(rec, open(cache, "w"))
            M._term_cache.clear()
            res = M.terminal_font_check({0xEC82}, env, platform="darwin")
            self.assertEqual(len([c for c in calls if c[0] == "osascript"]), 3, "壞快取不採用、重查")
            self.assertIsNone(res["ok"])
            n = len(calls)
            M._term_cache.clear()
            M.terminal_font_check({0xEC82}, env, platform="darwin")
            self.assertEqual(len(calls), n, "命中快取連 tmux 都不叫")
        finally:
            M._run = old_run
            M._term_cache.clear()

    def test_b7_cached_in_process(self):
        calls = []

        def real_like(argv):
            calls.append(argv)
            return term(False)(argv)
        old_run, M._run = M._run, real_like
        M._term_cache.clear()
        try:
            env = dict(self.env, EZ2_AIB_STATE_ROOT=os.path.join(self.td.name, "st"))
            a = M.terminal_font_check({0xEC82}, env, platform="darwin")
            b = M.terminal_font_check({0xEC82}, env, platform="darwin")
            self.assertEqual((a["ok"], b["ok"]), (False, False))
            self.assertEqual(len(calls), 1, "偏好檔 / 字型夾沒改 ⇒ 不重查")
            M._term_cache.clear()
            c = M.terminal_font_check({0xEC82}, env, platform="darwin")
            self.assertEqual((len(calls), c["ok"]), (1, False), "新行程(行程內快取空)讀檔案快取")
            self.assertTrue(os.path.isfile(os.path.join(self.td.name, "st", "state", M.TERM_CACHE_FILE)))
            os.remove(os.path.join(self.td.name, "st", "state", M.TERM_CACHE_FILE))
            M._term_cache.clear()
            M.terminal_font_check({0xEC82}, env, platform="darwin")
            self.assertEqual(len(calls), 2, "兩層快取都沒有 ⇒ 重查")
        finally:
            M._run = old_run
            M._term_cache.clear()


class Doctor(Base):
    def doc(self, runner, env=None, have_font=True):
        orig_paths, orig_cps = M.font_paths, M.ttf_codepoints
        M.font_paths = lambda e=None: [self.font] if have_font else []
        M.ttf_codepoints = lambda p: set(self.all_cps)
        try:
            return D.check_badge_font(REPO, env=self.env if env is None else env, runner=runner, platform="darwin")
        finally:
            M.font_paths, M.ttf_codepoints = orig_paths, orig_cps

    def test_b6_doctor(self):
        r = self.doc(term(True))
        self.assertEqual(r["status"], "ok", r)
        r = self.doc(term(False, profile="Pro", font="Menlo-Regular"))
        self.assertEqual(r["status"], "note")
        self.assertIn("Pro", r["detail"])
        self.assertIn("Menlo-Regular", r["detail"])
        self.assertIn("「Pro」", r["remedy"])
        self.assertIn("JetBrainsMono Nerd Font", r["remedy"])
        r = self.doc(term(False), have_font=False)
        self.assertEqual(r["status"], "note")
        self.assertIn("v3.5.1", r["remedy"])
        self.assertIn("SHA-256", r["remedy"])
        r = self.doc(term(False), env=dict(self.env, EZ2_AIB_ENGINE_BADGE="letters"))
        self.assertEqual(r["status"], "ok")
        keys = ("ok", "letters", "error", "missing", "missing_remedy", "terminal", "terminal_remedy")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertTrue(d.get("aib.doctor.check.badge_font." + k), (lang, k))
            for k in ("terminal", "terminal_remedy"):
                self.assertIn("{profile}", d["aib.doctor.check.badge_font." + k], (lang, k))


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/render/engine_badge_mode.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=40,
                            timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_terminal_not_checked(self):
        self._knife('        if tf["ok"] is False:\n', '        if False:\n', "Detect.test_b2_terminal_font_cannot_draw")

    def test_k2_failure_counts_as_cannot_draw(self):
        self._knife('    if not isinstance(rows, list) or not rows:\n        return {"ok": None}\n',
                    '    if not isinstance(rows, list) or not rows:\n        return {"ok": False, "missing": sorted(cps), '
                    '"profile": "", "font": ""}\n', "Detect.test_b3_no_false_alarm")


class Knife2(Knife):
    def test_k3_failure_not_cached(self):
        self._knife("    res = _probe(cps, e, runner)\n    if runner is None:\n",
                    "    res = _probe(cps, e, runner)\n    if runner is None and res[\"ok\"] is not None:\n",
                    "Detect.test_b8_failure_cached_and_bad_cache_ignored")

    test_k1_terminal_not_checked = None
    test_k2_failure_counts_as_cannot_draw = None


if __name__ == "__main__":
    unittest.main()
