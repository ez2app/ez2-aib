#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib plugins licensed <外掛> [--json]:不在 aib 行程裡的付費外掛(例 Obsidian 標題外掛 obsidian-labels)問授權用(10-15)。

外掛端只認:rc 0 且 JSON 的 "licensed" 為 true(aib-plugins#1)。判準是 aib_plugins.licensed(),既有授權測試守它;
本支守的是輸出約定與入口接線。
P1 沙盒沒授權 ⇒ rc 1、licensed=false、why 以 no-license 開頭。
P2 licensed() 判有 ⇒ rc 0、{"licensed": true, "addon": …}。
P3 外掛名帶怪字元 ⇒ rc 2,不查。
P4 aib 入口 `aib plugins licensed …` 接到這支。刀:拿掉入口那段 ⇒ P4 紅。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
import aib_plugins as AP  # noqa: E402

AIB = ROOT / "src" / "cockpit" / "aib"


def _sandbox_env(home):
    return {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "en_US.UTF-8"}


class Cli(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)

    def test_P1_no_license_is_rc1_false(self):
        with mock.patch.dict(os.environ, _sandbox_env(self.home), clear=True):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = AP.main(["licensed", "obsidian-labels", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["licensed"], False)
        self.assertTrue(out["why"].startswith("no-license"), out)

    def test_P2_licensed_is_rc0_true(self):
        buf = io.StringIO()
        with mock.patch.object(AP, "licensed", return_value=(True, "addon")), redirect_stdout(buf):
            rc = AP.main(["licensed", "obsidian-labels", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual((out["licensed"], out["addon"], out["why"]), (True, "obsidian-labels", "addon"))
        self.assertIn("signature_enforced", out)

    def test_P3_bad_name_is_rc2(self):
        with mock.patch.object(AP, "licensed") as m:
            self.assertEqual(AP.main(["licensed", "a;b", "--json"]), 2)
            m.assert_not_called()

    def test_P4_aib_entry_routes_plugins(self):
        p = subprocess.run(["bash", str(AIB), "plugins", "licensed", "obsidian-labels", "--json"],
                           capture_output=True, text=True, timeout=60, env=_sandbox_env(self.home),
                           stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 1, p.stderr[-400:])
        self.assertIs(json.loads(p.stdout.strip().splitlines()[-1])["licensed"], False)


if __name__ == "__main__":
    unittest.main()
