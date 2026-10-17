#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接機器事實「卡住沒送到的 dm」(10-18 @9f808 建議、維護者同意由 @abb89 做)。
對方輸入框有草稿 ⇒ blocked / hold_human、不會自己重送;待審框只量已送進來的,量不到這種。

S1 寄出:別席佇列裡 sender_seat = 這席、hold_human / queued、沒 delivered_at ⇒ 列在 out
S2 收進:這席佇列裡卡住的 ⇒ 列在 in
S3 已送達(delivered_at 有值)、已讀、別人寄給別人的 ⇒ 都不列
S4 超過 hours 的舊信不列;每邊最多 limit 封,total 照實
S5 交接報告(facts → 文字)有這一行、寄出 → / 收進 ← 方向對
刀(複本):K1 拿掉 delivered_at 判斷 ⇒ S3 紅
"""
import pathlib
import sys
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import aib_handoff as H  # noqa: E402

THIS = pathlib.Path(__file__).name
NOW = 1_800_000_000


def item(i, status="hold_human", sender="abb89", delivered=None, age_h=1, why="草稿擋住"):
    return {"id": "q_%s" % i, "status": status, "sender_seat": sender, "delivered_at": delivered,
            "sent_at": NOW - age_h * 3600, "hold_why": why, "msg": "訊息 %s" % i}


class FakeQ:
    def __init__(self, data):
        self.data = data

    def all_seats(self, env=None):
        return sorted(self.data)

    def _all_items(self, seat, env=None):
        return list(self.data.get(seat, []))


class Stuck(unittest.TestCase):
    def run_with(self, data, **kw):
        saved = sys.modules.get("dm_queue")
        sys.modules["dm_queue"] = FakeQ(data)
        try:
            return H.stuck_dm("abb89", now=NOW, **kw)
        finally:
            if saved is None:
                sys.modules.pop("dm_queue", None)
            else:
                sys.modules["dm_queue"] = saved

    def test_S1_S2_out_and_in(self):
        r = self.run_with({"d07f9": [item(1)], "abb89": [item(2, status="queued", sender="9f808")]})
        self.assertEqual([x["id"] for x in r["out"]], ["q_1"])
        self.assertEqual(r["out"][0]["seat"], "d07f9")
        self.assertEqual([x["id"] for x in r["in"]], ["q_2"])
        self.assertEqual(r["in"][0]["seat"], "9f808")

    def test_S3_delivered_read_and_others_not_listed(self):
        r = self.run_with({"d07f9": [item(1, delivered=NOW - 10), item(2, status="read"), item(3, sender="977e3")]})
        self.assertEqual((r["out_total"], r["in_total"]), (0, 0))

    def test_S4_window_and_limit(self):
        r = self.run_with({"d07f9": [item(i, age_h=1) for i in range(5)] + [item(99, age_h=72)]}, limit=3)
        self.assertEqual(r["out_total"], 5, "72 小時前的不算")
        self.assertEqual(len(r["out"]), 3)

    def test_S5_render_line(self):
        facts = {"stuck_dm": {"out": [{"id": "q_1", "seat": "d07f9", "status": "hold_human", "why": "w"}],
                              "in": [{"id": "q_2", "seat": "9f808", "status": "queued", "why": ""}],
                              "out_total": 1, "in_total": 1}}
        facts.update({"schema": H.SCHEMA, "ts": NOW, "seat": {"code": "abb89"}})
        text = H.render_draft(facts)
        self.assertIn("q_1→@d07f9", text)
        self.assertIn("q_2←@9f808", text)


class Knife(unittest.TestCase):
    def test_K1_ignore_delivered(self):
        with knife_copy(ROOT, "src/cockpit/aib_handoff.py",
                        'if it.get("status") not in STUCK_STATUSES or it.get("delivered_at") is not None:',
                        'if it.get("status") not in STUCK_STATUSES:', tests=(THIS,)) as copy_root:
            r = run_guarded([sys.executable, str(copy_root / "tests" / THIS),
                             "Stuck.test_S3_delivered_read_and_others_not_listed"], timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("FAIL", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
