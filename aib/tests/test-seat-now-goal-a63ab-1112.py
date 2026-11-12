#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib now --goal(RULES §17,Andy 11-12 21:2x;DECISION-goalcheck-gate-a63ab-1112)。

每件事開始 / 完成都對主目標:aib now 記下這一階段對應的目標(A1–A99、E1–E99、none)。
這裡只驗記錄:格式、沿用、歷史行;目標在不在表、有沒有偏離由 Rust aib-expect check-goal 判。
"""
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src" / "cockpit"))
import seat_now  # noqa: E402


class ParseGoal(unittest.TestCase):
    def test_accepted_forms_are_normalised(self):
        for raw, want in [("A8", "A8"), ("a8", "A8"), (" E6 ", "E6"), ("A4b", "A4b"), ("a4B", "A4b"),
                          ("A08", "A8"), ("E11", "E11")]:
            self.assertEqual(seat_now.parse_goal(raw), (want, True), raw)

    def test_missing_goal_is_fine(self):
        self.assertEqual(seat_now.parse_goal(None), (None, True))

    def test_bad_forms_are_refused(self):
        for raw in ["", "8", "A", "B3", "A123", "A8 E6", "A8;rm", "goal", "A4bc", "none!"]:
            self.assertEqual(seat_now.parse_goal(raw, why="x"), (None, False), raw)

    def test_none_needs_a_why(self):
        self.assertEqual(seat_now.parse_goal("none"), (None, False))
        self.assertEqual(seat_now.parse_goal("none", why="   "), (None, False))
        self.assertEqual(seat_now.parse_goal("NONE", why="回 dm"), ("none", True))


class Record(unittest.TestCase):
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

    def test_goal_is_recorded_and_kept_within_the_stage(self):
        r = self.w("做 #726", 0, 1000, new=True, goal="A8")
        self.assertEqual((r["goal"], r["goal_at"]), ("A8", 1000))
        r = self.w("做 #726", 2, 1100)
        self.assertEqual((r["goal"], r["goal_at"]), ("A8", 1000), "同一主軸沒給就沿用")
        r = self.w("另一件完全不同的事", 0, 1200, new=True)
        self.assertNotIn("goal", r, "換主軸不沿用上一件的目標")

    def test_history_rows_carry_the_goal_and_a_late_goal_is_visible(self):
        self.w("做 #726", 0, 1000, new=True)               # 開始時沒寫目標
        self.w("做 #726", 1, 1100, goal="A8")               # 後來才補
        self.w("做 #726", 2, 1200)                          # 沒變 ⇒ 不加行
        rows = self.rows()
        self.assertEqual(len(rows), 2, rows)
        self.assertNotIn("goal", rows[0])
        self.assertEqual(rows[1]["goal"], "A8")
        self.assertEqual(rows[0]["started_at"], rows[1]["started_at"], "同一階段:讀者據此判「事後才補」")

    def test_changing_the_goal_adds_a_row(self):
        self.w("做 #726", 0, 1000, new=True, goal="A8")
        self.w("做 #726", 1, 1100, goal="E6")
        self.assertEqual([r.get("goal") for r in self.rows()], ["A8", "E6"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        rc = seat_now.main(list(argv), code="c0de1", out=out, err=err, state_root=self.root)
        return rc, out.getvalue(), err.getvalue()

    def test_bad_goal_is_rc_2_and_nothing_is_written(self):
        rc, _, err = self.run_cli("做事", "0/2", "--goal", "B3")
        self.assertEqual(rc, 2, err)
        self.assertIn("--goal", err)
        self.assertIsNone(seat_now.read("c0de1", state_root=self.root))

    def test_none_without_why_is_refused(self):
        rc, _, err = self.run_cli("回 dm", "0/1", "--goal", "none")
        self.assertEqual(rc, 2, err)

    def test_good_goal_is_written(self):
        rc, _, err = self.run_cli("做事", "0/2", "--goal", "a8")
        self.assertEqual(rc, 0, err)
        self.assertEqual(seat_now.read("c0de1", state_root=self.root)["goal"], "A8")


if __name__ == "__main__":
    unittest.main()
