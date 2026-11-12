#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib now 寫主軸歷史(Andy 11-12 13:2x「讓 aib now 寫歷史」)。

<狀態根>/state/seat-now/<席>.history.jsonl:主項目文字或啟動時間換了才加一行 {"item","started_at","seen"};
只改步數 / 狀態不加;太大只留最後 HISTORY_KEEP 行;寫不進去不擋 aib now。
讀者:crates/aib-plan/src/mainline.rs(同欄位)。
"""
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


if __name__ == "__main__":
    unittest.main()
