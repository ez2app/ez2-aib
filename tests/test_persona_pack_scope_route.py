# SPDX-License-Identifier: Apache-2.0
"""範圍外導引(the maintainer 10-16):人格包每一包都帶「先跑 aib who-owns」那一行，四語都有。"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "aib" / "src" / "identity"))
import persona_pack  # noqa: E402

LOCALES = ("zh-TW", "en", "ja", "ko")


class ScopeRouteLine(unittest.TestCase):
    def test_key_in_all_locales(self):
        for loc in LOCALES:
            d = json.loads((ROOT / "aib" / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertIn("aib.ppack.scope_route", d, loc)
            self.assertIn("aib who-owns", d["aib.ppack.scope_route"], loc)

    def test_pack_carries_line_every_locale(self):
        old = os.environ.get("EZ2_AIB_LOCALE")
        try:
            for loc in LOCALES:
                os.environ["EZ2_AIB_LOCALE"] = loc
                persona_pack._TR.clear()
                text, _ = persona_pack.assemble("n", "u", "s8", [("職責", "## 職責\n- x")], "", [], "", "", "ab")
                self.assertIn(persona_pack.scope_route_line(), text, loc)
                self.assertIn("aib who-owns", text, loc)
        finally:
            if old is None:
                os.environ.pop("EZ2_AIB_LOCALE", None)
            else:
                os.environ["EZ2_AIB_LOCALE"] = old
            persona_pack._TR.clear()

    def test_line_survives_byte_budget(self):
        big = [("職責", "## 職責\n" + "- 很長的職責\n" * 400), ("紅線", "## 紅線\n- 不刪站")]
        text, parts = persona_pack.assemble("n", "u", "s8", big, "", [], "", "", "ab")
        self.assertLessEqual(len(text.encode("utf-8")), persona_pack.MAX_BYTES)
        self.assertIn("aib who-owns", text)


if __name__ == "__main__":
    unittest.main()
