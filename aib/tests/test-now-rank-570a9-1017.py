#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""狀態列顯示等級(10-17 the maintainer):寫成 n⭐ / n🌙 / n☀️ / n👑,後面接目前獲得的資源;📌 行只留更新時間。

核心不算等級,只讀擴充寫的快照 <狀態根>/state/seat-rank/<席碼>.json(資源名稱也由擴充給)。
R1 快照新 ⇒ 「5⭐ 2👑 30木材/12石塊」;沒冠不畫 👑、沒 res 只畫等級;資源最多 4 種、名稱濾掉分隔符號與 & \\
R2 沒快照 / 超過一天 / 讀不懂 / 數值越界 ⇒ 不顯示
R3 --statusline:帶 --rank-field ⇒ 等級是第 4 欄、📌 行不重複;沒帶(舊版殼)⇒ 接在 📌 行尾;窄窗讓位
R4 📌 行只有更新時間,不畫啟動時間(啟動時間照記)
刀(複本):K1 不檢查快照新舊 ⇒ R2 的「超過一天」紅
"""
import io
import json
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
    snap = {"lv": 13, "rank": {"kind": "star", "n": 5}, "crowns": ["collab", "guard"], "at": time.time(),
            "res": [["木材", 30], ["石塊", 12]]}
    snap.update(kw)
    (d / (code + ".json")).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")


def statusline(root, *flags, width=200):
    out = io.StringIO()
    rc = seat_now.main(["--statusline", "--width", str(width), *flags], code=CODE, out=out, err=io.StringIO(),
                       state_root=root, gate_check=False)
    return rc, out.getvalue().rstrip("\n").split(seat_now.SEP)


class RankText(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-now-rank-")
        self.root = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def rt(self):
        return seat_now.rank_text(CODE, state_root=self.root)

    def test_R1_fresh_snapshot(self):
        write_snap(self.root, CODE)
        self.assertEqual(self.rt(), "5⭐ 2👑 30木材/12石塊")
        write_snap(self.root, CODE, lv=40, rank={"kind": "moon", "n": 4}, crowns=[], res=None)
        self.assertEqual(self.rt(), "4🌙")
        write_snap(self.root, CODE, lv=70, rank={"kind": "sun", "n": 2}, crowns=["captain"], res=[["木&材", 1], ["石|塊", 2], ["鐵", 3], ["金", 4], ["靈石", 5]])
        self.assertEqual(self.rt(), "2☀️ 1👑 1木材/2石塊/3鐵/4金", "資源最多 4 種、名稱濾掉 | & 等字")
        write_snap(self.root, CODE, res=[["木材", -1], ["", 5], "壞", ["石塊", 9]])
        self.assertEqual(self.rt(), "5⭐ 2👑 9石塊", "壞掉的資源項略過,其餘照畫")
        self.assertEqual(seat_now.rank_mark(CODE, state_root=self.root), " | 5⭐ 2👑 9石塊")

    def test_R2_missing_stale_or_bad(self):
        self.assertEqual(self.rt(), "")
        write_snap(self.root, CODE, at=time.time() - 2 * 86400)
        self.assertEqual(self.rt(), "", "超過一天的快照不該顯示")
        for bad in ({"lv": 0}, {"lv": 101}, {"rank": {"kind": "comet", "n": 1}}, {"rank": {"kind": "sun", "n": 11}}, {"crowns": "x"}, {"at": "昨天"}):
            write_snap(self.root, CODE, **bad)
            self.assertEqual(self.rt(), "", bad)
        p = Path(self.root) / "state" / "seat-rank" / (CODE + ".json")
        p.write_text("{不是 json", encoding="utf-8")
        self.assertEqual(self.rt(), "")
        self.assertEqual(seat_now.rank_text("", state_root=self.root), "")

    def test_R3_statusline_fields(self):
        seat_now.write(CODE, "畫等級圖示", 1, 2, state_root=self.root)
        write_snap(self.root, CODE)
        rc, f = statusline(self.root, "--rank-field")
        self.assertEqual(rc, 0)
        self.assertEqual(len(f), 4)
        self.assertEqual(f[0], CODE)
        self.assertEqual(f[3], "5⭐ 2👑 30木材/12石塊")
        self.assertNotIn("5⭐", f[1], "帶 --rank-field 時等級不再接在 📌 行")
        rc, f = statusline(self.root)
        self.assertEqual(len(f), 3, "舊版殼只讀 3 欄")
        self.assertIn("| 5⭐ 2👑 30木材/12石塊", f[1])
        rc, f = statusline(self.root, width=50)
        self.assertNotIn("5⭐", f[1], "窄窗時等級讓位給主項目")

    def test_R4_only_updated_time(self):
        seat_now.write(CODE, "畫等級圖示", 1, 2, state_root=self.root, started_at=time.time() - 3600)
        rc, f = statusline(self.root, "--rank-field")
        updated, started = seat_now.T("aib.now.updated", when=""), seat_now.T("aib.now.started", when="")
        self.assertIn(updated.strip(), f[1])
        self.assertNotIn(started.strip(), f[1], "📌 行不畫啟動時間")
        self.assertTrue(seat_now.read(CODE, state_root=self.root).get("started_at"), "啟動時間照記")


class Knife(unittest.TestCase):
    def test_K1_no_age_check_turns_R2_red(self):
        needle = "or not (0 <= now - at <= RANK_MAX_AGE_S)"
        with knife_copy(REPO, "src/cockpit/seat_now.py", needle, "or False", tests=(THIS,)) as copy_root:
            r = run_guarded([sys.executable, str(copy_root / "tests" / THIS), "RankText.test_R2_missing_stale_or_bad"],
                            cwd=str(copy_root), timeout=120)
        self.assertNotEqual(r.returncode, 0, "拿掉新舊檢查後 R2 應該紅:\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
