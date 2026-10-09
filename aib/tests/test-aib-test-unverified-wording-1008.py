#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「沒驗」那行照實寫原因(10-08):只有缺條件才寫「這台缺 X」;全部跳過、pytest 一支都沒收集到不是缺條件,不冠「這台缺」。

以前樣板寫死「這台缺 {what}」⇒ 出現「沒驗:這台缺 全部 3 格都跳過」這種誤導的句子。
U1 缺條件 ⇒ needs 是「這台缺 tool:x」;每行、即時行都看得到
U2 全部跳過 ⇒ 原因裡沒有「這台缺」
U3 推包工具認「沒驗」的規則(依語系樣板)兩種行都認得出測試名(4 語系)
刀(複本):K1 缺條件不寫「這台缺」⇒ U1 紅
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tools" / "release"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import test_entry as te  # noqa: E402
try:  # 10-09 D11:ship.py 會拿機器租約 ⇒ 載 ship 的測試進 24d 母體;本支也進開源版(tools/oss),那邊沒有這支治具也沒有租約
    import aib_host_lease_fixture  # noqa: E402,F401
except ImportError:
    pass


class Wording(unittest.TestCase):
    def test_u1_missing_says_machine_lacks(self):
        r = te._unverified("tests/test-a.py", ["tool:plutil"])
        self.assertEqual(r["needs"], [te.T("need_missing", {"what": "tool:plutil"})])
        self.assertIn("這台缺", r["needs"][0])
        self.assertIn("這台缺 tool:plutil", te.T("row_unverified", {"test": "x", "what": " ".join(r["needs"])}))

    def test_u2_all_skipped_not_machine(self):
        r = te._mark_all_skipped({"name": "tests/test-b.py", "rc": 0, "output": "Ran 2 tests in 0.1s\n\nOK (skipped=2)\n"})
        self.assertTrue(r.get("unverified"))
        line = te.T("row_unverified", {"test": r["name"], "what": " ".join(r["needs"])})
        self.assertNotIn("這台缺", line)
        self.assertNotIn("這台缺", te.T("live_unverified", {"test": r["name"], "what": " ".join(r["needs"])}))

    def test_u3_ship_parses_both(self):
        import i18n_lib
        import ship
        old = ship.LOC
        try:
            for loc in ("zh-TW", "en", "ja", "ko"):
                ship.LOC = loc
                a = i18n_lib.t("aib.test.row_unverified", {"test": "tests/test-a-9.py",
                               "what": i18n_lib.t("aib.test.need_missing", {"what": "tool:plutil"}, locale=loc)}, locale=loc)
                b = i18n_lib.t("aib.test.row_unverified", {"test": "tests/test-b.py",
                               "what": i18n_lib.t("aib.test.need_all_skipped", {"n": 2}, locale=loc)}, locale=loc)
                self.assertEqual(ship.gate_unverified("  " + a + "\n" + b + "\n"), {"tests/test-a-9.py", "tests/test-b.py"}, loc)
        finally:
            ship.LOC = old


class Knives(unittest.TestCase):
    def test_k1_no_prefix_reds_u1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/test_entry.py", '"needs": [T("need_missing", {"what": c}) for c in unmet]',
                        '"needs": list(unmet)', tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Wording.test_u1_missing_says_machine_lacks"],
                               cwd=str(root), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_u1_missing_says_machine_lacks", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
