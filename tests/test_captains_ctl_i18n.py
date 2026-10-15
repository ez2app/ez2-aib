# SPDX-License-Identifier: Apache-2.0
"""(Brought back from public PR #11; the captain bar label uses the key aib.cockpit.hub_captain_label.)
captains-ctl.sh speaks the user's language: error messages and the captain bar label come from aib/i18n.

Standard library only: python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "aib" / "src" / "cockpit" / "nav" / "captains-ctl.sh"
KEYS = ["aib.install_guard.missing", "aib.captains_ctl.cabin_resolver_missing", "aib.captains_ctl.cabin_conflict",
        "aib.captains_ctl.cross_cabin_no_cleanup", "aib.captains_ctl.unique_unreadable",
        "aib.captains_ctl.unique_schema", "aib.captains_ctl.captain_exists", "aib.captains_ctl.unmeasured",
        "aib.cockpit.hub_captain_label"]


class CaptainsCtlI18n(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-captains-"))
        self.home = self.td / "cabin"
        (self.home / "seats").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def run_ctl(self, locale, *args, **extra):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td), "EZ2_AIB_LOCALE": locale,
               "EZ2_AIHUB_SOCKET": "test-hub", "EZ2_AIB_INSTANCE_HOME": str(self.home)}
        env.update(extra)
        return subprocess.run(["bash", str(CTL)] + list(args), env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def seat(self, wid, captain):
        (self.home / "seats" / ("w%d.json" % wid)).write_text(json.dumps(
            {"schema": "aib-seat/v1", "window_id": "@%d" % wid, "role": "root", "captain_assigned": captain}),
            encoding="utf-8")

    def test_keys_in_every_locale(self):
        for loc in ("en", "zh-TW", "ja", "ko"):
            table = json.loads((ROOT / "aib" / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for key in KEYS:
                self.assertIn(key, table, "%s missing %s" % (loc, key))

    def test_status_label_follows_locale_and_fits(self):
        for loc, label, length in (("en", "  Captain: ", 13), ("zh-TW", "  艦長: ", 10)):
            r = self.run_ctl(loc, "--install", "--dry-run", AIB_FAKE_WINDOWS="@1|root")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("status-left %s\n" % label, r.stdout)
            self.assertIn("status-left-length %d\n" % length, r.stdout)

    def test_second_captain_refused_in_english(self):
        self.seat(1, True)
        self.seat(2, False)
        r = self.run_ctl("en", "--assign", "@2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("a captain is already assigned: window 1", r.stderr)
        self.assertNotIn("已有受派艦長", r.stderr)
        self.assertNotIn("!!", r.stderr)


if __name__ == "__main__":
    unittest.main()
