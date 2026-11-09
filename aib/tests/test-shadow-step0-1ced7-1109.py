"""影子續跑第 0 步(止血)— 1ced7 11-09,a63ab 派、Andy 准(PLAN-shadow-reuse-rust-a63ab-1109 §3)。

S1 一次跑不到 TRACE_MIN(100)支 ⇒ 不跑自檢、不起追蹤器、不寫紀錄池,每支一般實跑;line1 講清楚為什麼、怎麼強制開。
   AIB_TEST_TRACE_MIN 可改(0 = 一律追蹤);--trace 這一輪強制;--resume 一律追蹤;分份時看整批不看每一份。
S2 🔁 那行把實跑和沿用的秒數分開寫;沿用那邊標估算。
S3 省時那一行只在「省 ≥30 秒且 ≥20%」時印,而且標估算;沒到就不印(相加估算的誤差就有這麼大)。
S4 帳本(test-stats)每列帶 without_kind=sum_estimate;📈 / 📊 / 💳 的「沒有 aib」都標相加估算,📈 不再說「共少等」。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")
import test_entry as te  # noqa: E402

_spec = importlib.util.spec_from_file_location("entry0929", REPO / "tests" / "test-aib-test-entry-0929.py")
e0929 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e0929)


def ns(**kw):
    base = {"trace": False, "no_trace": False, "resume": None}
    base.update(kw)
    return argparse.Namespace(**base)


class S1TraceWanted(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop("AIB_TEST_TRACE_MIN", None)

    def tearDown(self):
        os.environ.pop("AIB_TEST_TRACE_MIN", None)
        if self._old is not None:
            os.environ["AIB_TEST_TRACE_MIN"] = self._old

    def test_threshold_is_100(self):
        self.assertEqual(te.TRACE_MIN, 100)
        self.assertFalse(te.trace_wanted(ns(), 1))
        self.assertFalse(te.trace_wanted(ns(), 99))
        self.assertTrue(te.trace_wanted(ns(), 100))

    def test_env_overrides_and_zero_means_always(self):
        os.environ["AIB_TEST_TRACE_MIN"] = "0"
        self.assertTrue(te.trace_wanted(ns(), 1))
        os.environ["AIB_TEST_TRACE_MIN"] = "5"
        self.assertFalse(te.trace_wanted(ns(), 4))
        self.assertTrue(te.trace_wanted(ns(), 5))

    def test_flags(self):
        self.assertTrue(te.trace_wanted(ns(trace=True), 1), "--trace 強制開")
        self.assertTrue(te.trace_wanted(ns(resume="b1"), 1), "--resume 要追蹤才接得回")
        self.assertFalse(te.trace_wanted(ns(no_trace=True, trace=True), 500), "分份子行程聽主行程的 --no-trace")


class S1EndToEnd(unittest.TestCase):
    """真的跑 test_entry.py(0929 的假倉庫):預設小批不追蹤、沒留紀錄;--trace / AIB_TEST_TRACE_MIN=0 照舊追蹤。"""

    def run_entry(self, td, fx, *args, **env_extra):
        env = e0929._env(td, **env_extra)
        env.pop("AIB_TEST_TRACE_MIN", None)
        env.update(env_extra)
        return subprocess.run([sys.executable, str(fx / e0929.ENTRY_REL), *args], cwd=str(fx), env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)

    def test_small_batch_runs_plain_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = e0929._fixture(td)
            p = self.run_entry(td, fx, "tests/test-ok.py")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn(te.T("line1_small", {"n": 1, "min": 100}), p.stdout, p.stdout)
            self.assertIn("小批不追蹤", p.stderr)
            self.assertNotIn("🧮", p.stdout, "小批沒追蹤 ⇒ 不印沒有 aib / 有 aib 的比較")
            self.assertEqual(e0929._rows(td / "shadow"), [], "小批不寫紀錄池")

    def test_small_red_is_still_red(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = e0929._fixture(td)
            p = self.run_entry(td, fx, "tests/test-ok.py", "tests/test-red.py")
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertIn(te.T("line1_small", {"n": 2, "min": 100}), p.stdout)

    def test_trace_flag_and_env_keep_shadow_resume(self):
        for how in ("flag", "env"):
            with self.subTest(how=how), tempfile.TemporaryDirectory() as t:
                td = Path(t)
                fx = e0929._fixture(td)
                args = ("--trace", "tests/test-ok.py") if how == "flag" else ("tests/test-ok.py",)
                extra = {} if how == "flag" else {"AIB_TEST_TRACE_MIN": "1"}
                p = self.run_entry(td, fx, *args, **extra)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn(e0929.ENABLED_0_1, p.stdout, p.stdout)
                self.assertTrue(e0929._rows(td / "shadow"), "追蹤了就要留紀錄")

    def test_shards_follow_the_whole_batch(self):
        """--jobs 2:兩份都不追蹤 ⇒ 合起來是 small、rc 0(不是 kept<ran 的 short / rc 3);--trace ⇒ 兩份都追蹤。"""
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = e0929._fixture(td)
            p = self.run_entry(td, fx, "--jobs", "2", "tests/test-ok.py", "tests/test-pace.py")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("影子續跑:這次沒開", p.stdout, p.stdout)
            self.assertEqual(e0929._rows(td / "shadow"), [])
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = e0929._fixture(td)
            p = self.run_entry(td, fx, "--jobs", "2", "--trace", "tests/test-ok.py", "tests/test-pace.py")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("已啟用且確認運作", p.stdout, p.stdout)
            self.assertTrue(e0929._rows(td / "shadow"), "--trace 傳到每一份 ⇒ 要留紀錄")


def row(name, reused, s):
    return {"name": name, "rc": 0, "reused": reused, ("prev_duration_s" if reused else "elapsed_s"): s}


class S2S3S4Lines(unittest.TestCase):
    def vs(self, ran_s, reused_s, wall):
        tests = [row("a", False, ran_s)] + ([row("b", True, reused_s)] if reused_s else [])
        return te.versus(tests, wall)

    def test_s2_ran_and_reused_seconds_are_separate(self):
        v = self.vs(12.0, 50.0, 13.0)
        self.assertEqual((v["ran_s"], v["reused_s"], v["without_s"], v["without_kind"]), (12.0, 50.0, 62.0, "sum_estimate"))
        lines = te.versus_lines(v, [{"without_s": 10, "with_s": 9}])
        split = te.T("vs_split", {"count": 1, "n": 1, "total": 2, "u": 0, "ran_t": te.dur_text(12.0),
                                  "reused_t": te.dur_text(50.0)})
        self.assertIn(split, lines)
        self.assertIn("估算", split)

    def saved_line(self, lines):
        prefix = te.T("vs_saved", {"seconds": "X", "pct": "Y"}).split("X")[0]
        return [ln for ln in lines if ln.startswith(prefix)]

    def test_s3_saved_line_only_at_30s_and_20pct(self):
        before = [{"without_s": 10, "with_s": 9}]
        cases = [((12.0, 50.0, 20.0), True),      # 省 42 秒、68% ⇒ 印
                 ((100.0, 25.0, 105.0), False),   # 省 20 秒 < 30 ⇒ 不印
                 ((300.0, 40.0, 300.0), False),   # 省 40 秒但 12% < 20% ⇒ 不印
                 ((10.0, 30.0, 10.0), True)]      # 省 30 秒、75% ⇒ 剛好到門檻 ⇒ 印
        for (ran_s, reused_s, wall), shown in cases:
            with self.subTest(ran_s=ran_s, reused_s=reused_s, wall=wall):
                lines = te.versus_lines(self.vs(ran_s, reused_s, wall), before)
                got = self.saved_line(lines)
                self.assertEqual(bool(got), shown, lines)
                if got:
                    self.assertIn("估算", got[0])

    def test_s3_required_lines_stay_consistent(self):
        """沒印省時那行時,守門清單不能多塞(也不能重複塞 📊 那行)。"""
        req = []
        lines = te.versus_lines(self.vs(100.0, 25.0, 105.0), [{"without_s": 10, "with_s": 9}], required=req)
        self.assertEqual(len(req), len(set(req)), req)
        self.assertTrue(all(r in lines for r in req))
        self.assertTrue(any(r.startswith(te.T("vs_compare", {"without": "X", "actual": "Y"}).split("X")[0]) for r in req))

    def test_s3_no_save_still_says_so(self):
        lines = te.versus_lines(self.vs(10.0, 5.0, 40.0), [{"without_s": 10, "with_s": 9}])
        self.assertIn(te.T("vs_nosave"), lines)

    def test_s4_ledger_row_is_marked_and_total_is_not_net(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "test-stats.jsonl"
            v = self.vs(12.0, 50.0, 13.0)
            te.record_versus(Path(t), v, path=path)
            r = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(r["without_kind"], "sum_estimate")
            lines = te.versus_lines(v, [{"without_s": 100, "with_s": 50}])
            total = [ln for ln in lines if "📈" in ln][0]
            self.assertIn("相加估算", total)
            self.assertIn("不是淨省", total)
            self.assertNotIn("少等", total)

    def test_s4_every_locale_has_the_new_keys_and_same_placeholders(self):
        import re
        keys = ("line1_small", "live_small_batch", "live_start_plain", "vs_split", "vs_split_unverified", "vs_saved",
                "vs_total", "vs_total_plain", "vs_compare", "vs_compare_waited", "vs_billed")
        data = {L: json.loads((REPO / "i18n" / ("%s.json" % L)).read_text(encoding="utf-8")) for L in ("zh-TW", "en", "ja", "ko")}
        for k in keys:
            ph = {L: sorted(set(re.findall(r"\{(\w+)\}", d["aib.test." + k]))) for L, d in data.items()}
            with self.subTest(k=k):
                self.assertEqual(len(set(map(tuple, ph.values()))), 1, ph)


if __name__ == "__main__":
    unittest.main()
