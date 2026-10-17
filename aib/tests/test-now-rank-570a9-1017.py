#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""狀態列顯示等級(10-17 the maintainer):aib now --statusline 尾端加「 | Lv42 ☀️☀️ 👑👑」。

核心不算等級,只讀擴充寫的快照 <狀態根>/state/seat-rank/<席碼>.json。
R1 快照新 ⇒ 顯示 Lv、星 / 月 / 日個數、冠個數;沒冠就不畫 👑
R2 沒快照 / 超過一天 / 讀不懂 / 數值越界 ⇒ 不顯示
R3 --statusline 真的接上:同一席有快照 ⇒ 📌 行尾端有等級;窄窗(扣掉等級剩不到 40 字寬)⇒ 等級讓位
刀(複本):K1 不檢查快照新舊 ⇒ R2 的「超過一天」紅
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
import seat_now  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

THIS = Path(__file__).name
CODE = "a1b2c"


def write_snap(root, code, **kw):
    d = Path(root) / "state" / "seat-rank"
    d.mkdir(parents=True, exist_ok=True)
    snap = {"lv": 42, "rank": {"kind": "sun", "n": 2}, "crowns": ["collab", "guard"], "at": time.time()}
    snap.update(kw)
    (d / (code + ".json")).write_text(json.dumps(snap), encoding="utf-8")


class RankMark(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-now-rank-")
        self.root = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def test_R1_fresh_snapshot(self):
        write_snap(self.root, CODE)
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), " | Lv42 ☀️☀️ 👑👑")
        write_snap(self.root, CODE, lv=7, rank={"kind": "star", "n": 3}, crowns=[])
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), " | Lv7 ⭐⭐⭐")
        write_snap(self.root, CODE, lv=40, rank={"kind": "moon", "n": 4}, crowns=None)
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), " | Lv40 🌙🌙🌙🌙")

    def test_R2_missing_stale_or_bad(self):
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), "")
        write_snap(self.root, CODE, at=time.time() - 2 * 86400)
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), "", "超過一天的快照不該顯示")
        for bad in ({"lv": 0}, {"lv": 101}, {"rank": {"kind": "comet", "n": 1}}, {"rank": {"kind": "sun", "n": 11}}, {"crowns": "x"}, {"at": "昨天"}):
            write_snap(self.root, CODE, **bad)
            self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), "", bad)
        p = Path(self.root) / "state" / "seat-rank" / (CODE + ".json")
        p.write_text("{不是 json", encoding="utf-8")
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), "")
        self.assertEqual(seat_now.rank_mark("", state_root=self.root), "")

    def test_R3_statusline_wired(self):
        seat_now.write(CODE, "畫等級圖示", 1, 2, state_root=self.root)
        write_snap(self.root, CODE)
        out = io.StringIO()
        rc = seat_now.main(["--statusline", "--width", "200"], code=CODE, out=out, err=io.StringIO(), state_root=self.root, gate_check=False)
        self.assertEqual(rc, 0)
        code, line, _logo = out.getvalue().rstrip("\n").split(seat_now.SEP)
        self.assertEqual(code, CODE)
        self.assertIn("畫等級圖示", line)
        self.assertIn("| Lv42 ☀️☀️ 👑👑", line)
        narrow = io.StringIO()
        seat_now.main(["--statusline", "--width", "50"], code=CODE, out=narrow, err=io.StringIO(), state_root=self.root, gate_check=False)
        self.assertNotIn("Lv42", narrow.getvalue(), "窄窗時等級要讓位給主項目")


class Knife(unittest.TestCase):
    def test_K1_no_age_check_turns_R2_red(self):
        needle = "or not (0 <= now - at <= RANK_MAX_AGE_S)"
        with knife_copy(REPO, "src/cockpit/seat_now.py", needle, "or False", tests=(THIS,)) as copy_root:
            r = run_guarded([sys.executable, str(copy_root / "tests" / THIS), "RankMark.test_R2_missing_stale_or_bad"],
                            cwd=str(copy_root), timeout=120)
        self.assertNotEqual(r.returncode, 0, "拿掉新舊檢查後 R2 應該紅:\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
