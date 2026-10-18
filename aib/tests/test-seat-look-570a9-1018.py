#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""席的外觀 look:好友收到的島上走動的人帶各席的外觀字串(艦長室定義意思,核心只驗型別)。只用標準庫。

L1 clean_seat_look:合 ^av[0-9]{1,2}:[0-9a-z]{1,64}\\Z 才回原字串;大寫、空白、太長、結尾換行、不是字串 ⇒ None
L2 attach_looks:room.json seats[席碼].look 合格的放進 people;沒有 / 不合 ⇒ 不出現 look 鍵(原本有的也拿掉)
L3 傳輸:encode → decode 後 look 還在;收件端再驗一次(壞的拿掉、其他欄位不動);只有 look 不會把快照標成 v3
刀(複本):K1 clean_seat_look 不驗 ⇒ L1 紅;K2 attach_looks 不放 ⇒ L2 紅;K3 收件端不再驗 ⇒ L3 紅
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import room_snapshot as RS  # noqa: E402

LOOK = "av1:011225a0d0033040314"


class Pure(unittest.TestCase):
    def test_l1_clean_look(self):
        self.assertEqual(RS.clean_seat_look(LOOK), LOOK)
        self.assertEqual(RS.clean_seat_look("av12:z"), "av12:z")
        for bad in (None, 3, "", "av1:", "AV1:01", "av1:0A", "av1:01 2", "av1:" + "0" * 65, LOOK + "\n", "xx1:01", "av123:01", ["av1:01"]):
            self.assertIsNone(RS.clean_seat_look(bad), repr(bad))

    def test_l2_attach(self):
        people = [{"seat": "aaaaa", "name": "甲"}, {"seat": "bbbbb", "name": "乙", "look": "av1:00"}, {"seat": "ccccc"}]
        RS.attach_looks(people, {"aaaaa": {"look": LOOK, "floor": 1}, "bbbbb": {"look": "壞的"}, "ddddd": {"look": LOOK}})
        self.assertEqual(people[0].get("look"), LOOK)
        self.assertNotIn("look", people[1], "不合的拿掉,不留舊值")
        self.assertNotIn("look", people[2])
        self.assertEqual(RS.attach_looks([{"seat": "aaaaa"}], None), [{"seat": "aaaaa"}], "沒有 room.json 也不出錯")

    def test_l3_wire(self):
        snap = {"v": 2, "people": [{"seat": "aaaaa", "name": "甲", "look": LOOK}, {"seat": "bbbbb", "name": "乙", "look": "av1:BAD"}],
                "rooms": None, "island": {"name": "島"}}
        self.assertFalse(RS.has_k_fields(RS.clean_island({"name": "島"})), "只有 look ⇒ 還是 v2,舊版收件端照收")
        got = RS.decode(RS.encode(snap))
        self.assertEqual(got["people"][0]["look"], LOOK)
        self.assertNotIn("look", got["people"][1], "收件端再驗一次")
        self.assertEqual(got["people"][1]["name"], "乙", "其他欄位不動")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/room_snapshot.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=120)
        out = r.stdout + r.stderr
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + out)
        self.assertIn("FAIL", out, "要是斷言紅,不是測試本身倒掉:\n" + out)
        self.assertNotIn("ERROR", out, "測試本身倒掉 = 假紅:\n" + out)

    def test_k1_no_check(self):
        self._knife('    return v if isinstance(v, str) and SEAT_LOOK_RE.match(v) else None',
                    '    return v', "Pure.test_l1_clean_look")

    def test_k2_no_attach(self):
        self._knife('        if look:\n            p["look"] = look', '        if False:\n            p["look"] = look', "Pure.test_l2_attach")

    def test_k3_receiver_trusts(self):
        self._knife('        if isinstance(p, dict) and "look" in p and not clean_seat_look(p["look"]):',
                    '        if False:', "Pure.test_l3_wire")


if __name__ == "__main__":
    unittest.main()
