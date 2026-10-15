# SPDX-License-Identifier: Apache-2.0
"""The version in pyproject.toml (pip / pipx installs) and aib/VERSION (one-line installs, `aib update`) agree,
and every CHANGELOG starts with that version.

0.1.8 was first prepared with only pyproject.toml bumped: aib/VERSION still said 0.1.7, so one-line installs
would have reported the old version. Standard library only.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOGS = ["CHANGELOG.md", "CHANGELOG.zh-TW.md", "CHANGELOG.ja.md", "CHANGELOG.ko.md"]


def pyproject_version():
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m and m.group(1)


class VersionFiles(unittest.TestCase):
    def test_version_file_matches_pyproject(self):
        v = pyproject_version()
        self.assertTrue(v, "no version in pyproject.toml")
        self.assertEqual((ROOT / "aib" / "VERSION").read_text(encoding="utf-8").strip(), v)

    def test_changelogs_start_with_the_version(self):
        v = pyproject_version()
        for name in CHANGELOGS:
            with self.subTest(file=name):
                first = next((l for l in (ROOT / name).read_text(encoding="utf-8").splitlines() if l.startswith("## ")), "")
                self.assertTrue(first == "## " + v or first.startswith("## " + v + " "), first)


if __name__ == "__main__":
    unittest.main()
