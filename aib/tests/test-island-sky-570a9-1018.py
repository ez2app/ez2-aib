#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""島的天色 sky:好友收到的島帶島主那邊的季節 / 天氣 / 時間(艦長室定義意思,核心只驗型別)。只用標準庫。

S1 clean_sky:6 個鍵各自驗 [a-z0-9_]{1,24},不合的那項當沒有;其他鍵(例如座標)一律不帶;不是 dict 或全不合 ⇒ None
S2 clean_island:合格的 sky 照帶;沒有 / 全不合 ⇒ 不出現 sky 鍵;座標不會出現在輸出裡
S3 傳輸:encode → decode 後 sky 還在、收件端再驗一次(壞值丟掉);只有 sky 不會把快照標成 v3(舊版照收)
刀(複本):K1 不篩鍵 ⇒ S1 紅;K2 clean_island 不帶 sky ⇒ S2 紅
"""
import json
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

SKY = {"season": "winter", "weather": "snow", "time": "night", "fest": "none", "realm": "island", "src": "real"}


class Pure(unittest.TestCase):
    def test_s1_clean_sky(self):
        self.assertEqual(RS.clean_sky(SKY), SKY)
        got = RS.clean_sky(dict(SKY, weather="Snow!", fest=3, lat=25.1, lon=121.5, city="Taipei"))
        self.assertEqual(got, {"season": "winter", "time": "night", "realm": "island", "src": "real"}, "壞值當沒有、不認得的鍵不帶")
        for bad in (None, "winter", ["winter"], {}, {"lat": 25.1}, {"season": "冬"}, {"season": "x" * 25}):
            self.assertIsNone(RS.clean_sky(bad), bad)

    def test_s2_island(self):
        isl = RS.clean_island({"name": "島", "sky": dict(SKY, lat=25.1, lon=121.5)})
        self.assertEqual(isl.get("sky"), SKY)
        self.assertNotIn("25.1", json.dumps(isl), "座標不會跟著島出去")
        self.assertNotIn("sky", RS.clean_island({"name": "島"}))
        self.assertNotIn("sky", RS.clean_island({"name": "島", "sky": {"season": "冬"}}))

    def test_s3_wire(self):
        isl = RS.clean_island({"name": "島", "sky": SKY})
        self.assertFalse(RS.has_k_fields(isl), "只有 sky ⇒ 還是 v2,舊版收件端照收(不認得的欄位它自己丟掉)")
        got = RS.decode(RS.encode({"v": 2, "people": [], "rooms": None, "island": isl}))
        self.assertEqual(got["island"]["sky"], SKY)
        raw = RS.decode(RS.encode({"v": 2, "people": [], "rooms": None, "island": {"name": "島", "sky": dict(SKY, weather="BAD")}}))
        self.assertNotIn("weather", raw["island"]["sky"], "收件端再驗一次")


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

    def test_k1_no_key_filter(self):
        self._knife('    out = {k: v[k] for k in SKY_KEYS if isinstance(v.get(k), str) and _KEY_RE.match(v[k])}',
                    '    out = dict(v)', "Pure.test_s1_clean_sky")

    def test_k2_island_drops_sky(self):
        self._knife('    if sky:\n        out["sky"] = sky', '    if False:\n        out["sky"] = sky', "Pure.test_s2_island")


if __name__ == "__main__":
    unittest.main()
