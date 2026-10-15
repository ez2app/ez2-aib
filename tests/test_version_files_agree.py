# SPDX-License-Identifier: Apache-2.0
"""aib --version 讀 aib/VERSION、發版讀 pyproject.toml 的 version:兩邊要一樣。
10-15 0.1.8 只改了 pyproject.toml,aib/VERSION 留在 0.1.7 ⇒ 裝 0.1.8 的人會看到 v0.1.7(@8f366 發版前抓到)。
CHANGELOG 四語的最上面一節也要是同一版。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pyproject_version():
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


class VersionFilesAgree(unittest.TestCase):
    def test_version_file_matches_pyproject(self):
        self.assertEqual((ROOT / "aib" / "VERSION").read_text(encoding="utf-8").strip(), pyproject_version())

    def test_changelogs_top_section_is_this_version(self):
        v = pyproject_version()
        for name in ("CHANGELOG.md", "CHANGELOG.zh-TW.md", "CHANGELOG.ja.md", "CHANGELOG.ko.md"):
            m = re.search(r"^## (\S+)", (ROOT / name).read_text(encoding="utf-8"), re.M)
            self.assertIsNotNone(m, name)
            self.assertEqual(m.group(1), v, name)


if __name__ == "__main__":
    unittest.main()
