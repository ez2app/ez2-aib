#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接圍欄鍵固定 zh-TW 是刻意的(語系盤查確認過)。

src/cockpit/aib_handoff.py _field_key 寫圍欄、tools/dispatch/handoff_fields.py _label 讀圍欄,兩邊都固定取 zh-TW 的
aib.handoff_fields.key.*:圍欄鍵是規格逐字(交接六格規格「鍵逐字照上表」),不隨顯示語系變;
值照當下語系。本支釘住這個約定:不管哪一種語系寫,讀的那邊六格都讀得回來;有一邊改成跟著語系 ⇒ 紅。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tools" / "dispatch"))
import aib_testlib  # noqa: E402,F401
import aib_handoff as H  # noqa: E402
import handoff_fields as F  # noqa: E402

LOCALES = ("zh-TW", "en", "ja", "ko")


class FixedKeys(unittest.TestCase):
    def test_01_every_locale_round_trips_all_six_fields(self):
        for loc in LOCALES:
            with self.subTest(loc=loc), mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": loc}):
                fields, from_v = F.parse_fixed(H.fields_block({}))
                self.assertEqual(sorted(fields), sorted(F.KEYS), loc)
                self.assertIsNotNone(from_v, loc)

    def test_02_both_sides_take_the_zh_tw_literal(self):
        for slug in H.FIELD_SLUGS:
            with self.subTest(slug=slug), mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "en"}):
                self.assertEqual(H._field_key(slug), F._label(slug))
                self.assertIn(F._label(slug), F.KEYS)

    def test_04_reader_decided_at_import_time_too(self):
        # 審查 C1:讀的一方 LOC / KEYS 是 import 時就定的 ⇒ 只 patch 環境量不到「讀的一方改成 locale=LOC」。
        #   在 en 底下重新 import 一次讀的一方,鍵仍要是 zh-TW 字面、仍讀得回寫的一方的六格。
        import importlib
        import i18n_lib
        zh = i18n_lib.load_dict("zh-TW")
        try:
            with mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "en"}):
                F2 = importlib.reload(F)
                self.assertEqual(sorted(F2.KEYS),
                                 sorted(zh["aib.handoff_fields.key." + s] for s in H.FIELD_SLUGS))
                fields, _ = F2.parse_fixed(H.fields_block({}))
                self.assertEqual(sorted(fields), sorted(F2.KEYS))
        finally:
            importlib.reload(F)

    def test_03_keys_really_differ_by_locale(self):
        # 不然 test_01 綠只是因為四語的鍵剛好一樣(那就量不到「固定 zh-TW」)。ja / ko 的鍵實測跟 zh-TW 一字不差,量得到的只有 en
        import i18n_lib
        zh = i18n_lib.load_dict("zh-TW")
        en = i18n_lib.load_dict("en")
        k = "aib.handoff_fields.key." + H.FIELD_SLUGS[0]
        self.assertNotEqual(zh[k], en[k])


if __name__ == "__main__":
    unittest.main()
