#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib now 寫主軸歷史(Andy 11-12 13:2x「讓 aib now 寫歷史」)。

<狀態根>/state/seat-now/<席>.history.jsonl:主項目文字或啟動時間換了才加一行 {"item","started_at","seen"};
只改步數 / 狀態不加;太大只留最後 HISTORY_KEEP 行;寫不進去不擋 aib now。
讀者:crates/aib-plan/src/mainline.rs(同欄位)。

判斷(Andy 11-12 13:3x):aib now --track on|off --why --idea 記進 seat-now/<席>.json 的 review;
階段完成(步數做滿 /【等回應】/【等你決定】)交回時，本回合沒回報判斷 ⇒ aib-now-gate 擋。
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src" / "cockpit"))
import seat_now  # noqa: E402


class History(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def rows(self):
        p = seat_now.history_path("c0de1", state_root=self.root)
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def w(self, item, done, now, **kw):
        return seat_now.write("c0de1", item, done, 4, state_root=self.root, now=now, **kw)

    def test_logs_only_mainline_changes(self):
        self.w("做 A", 0, 1000)
        self.w("做 A", 2, 1100)                      # 只改步數 ⇒ 不記
        self.w("做 A", 3, 1200, state="reply")       # 只改狀態 ⇒ 不記
        self.w("做 A(改字)", 3, 1300)                 # 改字 ⇒ 記，啟動時間沿用
        self.w("做 B", 0, 1400, new=True)            # --new ⇒ 記，新的啟動時間
        self.w("做 B", 0, 1500, new=True)            # 同字再 --new ⇒ 啟動時間換了 ⇒ 記
        r = self.rows()
        self.assertEqual([x["item"] for x in r], ["做 A", "做 A(改字)", "做 B", "做 B"])
        self.assertEqual([x["started_at"] for x in r], [1000, 1000, 1400, 1500])
        self.assertEqual([x["seen"] for x in r], [1000, 1300, 1400, 1500])
        self.assertEqual(set(r[0]), {"item", "started_at", "seen"})

    def test_trims_when_too_big(self):
        old = (seat_now.HISTORY_MAX_BYTES, seat_now.HISTORY_KEEP)
        seat_now.HISTORY_MAX_BYTES, seat_now.HISTORY_KEEP = 400, 3
        self.addCleanup(lambda: (setattr(seat_now, "HISTORY_MAX_BYTES", old[0]),
                                 setattr(seat_now, "HISTORY_KEEP", old[1])))
        for i in range(30):
            self.w("項目 %d" % i, 0, 2000 + i)
        r = self.rows()
        self.assertLessEqual(len(r), 3 + 10)
        self.assertEqual(r[-1]["item"], "項目 29")

    def test_unwritable_history_does_not_break_now(self):
        d = seat_now.now_dir(self.root)
        os.makedirs(os.path.join(d, "c0de1" + seat_now.HISTORY_SUFFIX))  # 擋住：同名是資料夾
        rec = self.w("做 C", 1, 3000)
        self.assertEqual(rec["item"], "做 C")
        self.assertEqual(seat_now.read("c0de1", state_root=self.root)["item"], "做 C")


class Review(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        # main() 先掃死席殘影：假席 c0de1 不在任何艙 ⇒ 紀錄會被掃掉;這裡只測判斷欄位
        from unittest import mock
        p = mock.patch.object(seat_now, "sweep_dead", lambda **kw: [])
        p.start()
        self.addCleanup(p.stop)

    def now(self, *argv):
        import io
        out, err = io.StringIO(), io.StringIO()
        rc = seat_now.main(list(argv), code="c0de1", out=out, err=err, state_root=self.root)
        return rc, err.getvalue()

    def rec(self):
        return seat_now.read("c0de1", state_root=self.root)

    def test_flags_store_review_and_carry_within_same_mainline(self):
        rc, e = self.now("做 A", "4/4", "--state", "reply", "--track", "on", "--why", "因為 X", "--idea", "無")
        self.assertEqual(rc, 0, e)
        r = self.rec()["review"]
        self.assertEqual((r["track"], r["why"], r["idea"]), ("on", "因為 X", "無"))
        self.assertGreater(r["at"], 0)
        self.assertEqual(self.now("做 A", "4/4")[0], 0)          # 同主軸再更新 ⇒ 沿用判斷
        self.assertEqual(self.rec()["review"]["why"], "因為 X")
        self.assertEqual(self.now("做 B", "0/3", "--new")[0], 0)  # 換主軸 ⇒ 舊判斷不帶過去
        self.assertNotIn("review", self.rec())

    def test_partial_or_blank_review_is_refused(self):
        for argv in (["--track", "on"], ["--track", "on", "--why", "x", "--idea", "  "],
                     ["--why", "x", "--idea", "y"]):
            rc, e = self.now("做 A", "1/2", *argv)
            self.assertEqual(rc, 2, argv)
        self.assertIsNone(self.rec())

    def test_auto_new_when_topic_changes(self):
        import time
        self.assertEqual(self.now("席位接續 v2 決定已寫", "1/2", "--started", "11-12 00:09")[0], 0)
        st0 = self.rec()["started_at"]
        rc, e = self.now("席位接續 v2 決定已寫,等 Andy 看", "2/2")   # 只是補字 ⇒ 沿用
        self.assertEqual((rc, e), (0, ""))
        self.assertEqual(self.rec()["started_at"], st0)
        before = int(time.time())
        rc, e = self.now("主機發熱三條修法", "0/3")                   # 字面全換 ⇒ 自動重新計時 + 提示
        self.assertEqual(rc, 0)
        self.assertIn("aib now:", e)
        self.assertGreaterEqual(self.rec()["started_at"], before)
        rc, e = self.now("另一件完全無關", "0/1", "--started", "11-12 00:09")  # 有 --started ⇒ 照給的，不自動
        self.assertEqual(e, "")
        self.assertEqual(self.rec()["started_at"], st0)

    def test_similarity_matches_aib_plan(self):
        # 跟 crates/aib-plan/src/mainline.rs 的測試同一組數字
        self.assertGreater(seat_now.similarity("aib-plan 主軸兩行", "aib-plan 主軸兩行已裝"), 0.6)
        self.assertLess(seat_now.similarity("席位接續 v2 決定", "主機發熱三條修法"), 0.15)
        self.assertEqual(seat_now.similarity("", ""), 1.0)
        self.assertFalse(seat_now.auto_new({"idle": True, "item": "x"}, "完全不同"))
        self.assertFalse(seat_now.auto_new({}, "完全不同"))

    def test_stage_done(self):
        self.assertTrue(seat_now.stage_done({"steps_done": 3, "steps_total": 3, "state": "busy"}))
        self.assertTrue(seat_now.stage_done({"steps_done": 1, "steps_total": 3, "state": "reply"}))
        self.assertTrue(seat_now.stage_done({"steps_done": 1, "steps_total": 3, "state": "decide"}))
        self.assertFalse(seat_now.stage_done({"steps_done": 1, "steps_total": 3, "state": "busy"}))
        self.assertFalse(seat_now.stage_done(None))


GATE = HERE.parent / "src" / "policy" / "hooks" / "aib-now-gate.py"


class GateReview(unittest.TestCase):
    """Stop 整條：本回合有用工具、aib now 也更新了;階段完成卻沒回報判斷 ⇒ rc 2。"""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=self.root, AIB_NOW_GATE_CODE="c0de1", AIB_LOCALE="zh-TW")
        for k in ("AIB_DM_GATE", "AIB_DM_GATE_REASON", "AIB_NOW_GATE", "AIB_NOW_GATE_REASON"):
            self.env.pop(k, None)
        d = os.path.join(self.root, "state", "seat-now")
        os.makedirs(d)
        with open(os.path.join(d, "gate-seats.txt"), "w") as f:
            f.write("c0de1\n")
        self.tp = os.path.join(self.root, "t.jsonl")
        with open(self.tp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-10-30T09:00:00Z",
                                "message": {"role": "user", "content": "做事"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}]}}) + "\n")
        self.state = os.path.join(self.root, "state")

    def write(self, done, total, state, review=None):
        seat_now.write("c0de1", "做 A", done, total, state=state, state_root=self.root, review=review)

    def stop(self):
        return subprocess.run([sys.executable, str(GATE), "stop"], input=json.dumps({"transcript_path": self.tp}),
                              text=True, capture_output=True, env=self.env, timeout=60)

    def test_stage_done_without_review_blocks(self):
        self.write(4, 4, "reply")
        r = self.stop()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--track", r.stderr)
        self.assertIn("--idea", r.stderr)

    def test_stage_done_with_review_passes(self):
        self.write(4, 4, "reply", review={"track": "off", "why": "x", "idea": "y"})
        r = self.stop()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_mid_stage_busy_passes(self):
        self.write(1, 4, "busy")
        self.assertEqual(self.stop().returncode, 0)


if __name__ == "__main__":
    unittest.main()
