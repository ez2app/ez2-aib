# SPDX-License-Identifier: Apache-2.0
"""The war-room window explains a missing project outline instead of reporting a read failure.

The public edition ships no docs/EZ2-AIB-DEV-OUTLINE.md, so a fresh cockpit used to open the war room on
"戰情室:大綱讀取失敗 …" (in every language). A missing outline is normal: say how to set one and keep listing
ruling tasks. An outline that exists but cannot be read is still an error.

Standard library only: python -m unittest discover -s tests -v
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "aib" / "src" / "cockpit" / "warroom-board.sh"


class WarroomNoOutline(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-warroom-"))
        for d in ("home", "state", "tasks"):
            (self.td / d).mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def board(self, locale, outline):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td / "home"), "EZ2_AIB_LOCALE": locale,
               "EZ2_ONESHOT": "1", "EZ2_OUTLINE": str(outline), "EZ2_TASKS_DIR": str(self.td / "tasks"),
               "EZ2_COCKPIT_STATE_DIR": str(self.td / "state"), "EZ2_AIHUB_SOCKET": "test-hub",
               "EZ2_AIB_INSTANCE_HOME": str(self.td / "home")}
        r = subprocess.run(["bash", str(BOARD)], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", r.stdout)

    def test_missing_outline_is_explained_in_locale(self):
        missing = self.td / "nope.md"
        out = self.board("en", missing)
        self.assertIn("no project outline yet", out)
        self.assertIn("EZ2_OUTLINE", out)
        self.assertNotIn("大綱讀取失敗", out)
        self.assertNotIn("cannot read", out)
        self.assertIn("還沒有專案大綱", self.board("zh-TW", missing))

    def test_missing_outline_still_lists_rulings(self):
        (self.td / "tasks" / "r1.json").write_text(json.dumps(
            {"id": "R-1", "source": "ruling", "status": "open", "title": "pick a license"}), encoding="utf-8")
        out = self.board("en", self.td / "nope.md")
        self.assertIn("R-1 open pick a license", out)

    def test_unreadable_outline_is_still_an_error(self):
        out = self.board("en", self.td / "state")  # a directory: exists, cannot be read as a file
        self.assertIn("cannot read the outline", out)
        self.assertNotIn("no project outline yet", out)

    def test_present_outline_renders_tree(self):
        outline = self.td / "outline.md"
        # The exact line format the no-outline notice tells users to write.
        outline.write_text("[1] approved p1 | Ship v1\n  [1.1] approved p1 | Write the README\n", encoding="utf-8")
        out = self.board("en", outline)
        self.assertIn("outline.md", out)
        self.assertIn("1 Ship v1", out)
        self.assertIn("1.1 Write the README", out)
        self.assertNotIn("no project outline yet", out)

    def test_english_board_has_no_chinese(self):
        outline = self.td / "outline.md"
        outline.write_text("[1] approved p1 | Ship v1\n[2] draft p2 | Maybe later\n", encoding="utf-8")
        (self.td / "tasks" / "t1.json").write_text(json.dumps(
            {"id": "T-1", "outline": "1", "status": "in_progress", "assignee": "ann"}), encoding="utf-8")
        (self.td / "tasks" / "r1.json").write_text(json.dumps(
            {"id": "R-1", "source": "ruling", "status": "open", "title": "pick"}), encoding="utf-8")
        out = self.board("en", outline)
        for want in ("War room: outline.md", "Owner", "Board", "Working", "awaiting approval", "Ruling work:",
                     "switch tab"):
            self.assertIn(want, out)
        self.assertIsNone(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", out), out)
        self.assertIn("戰情室:outline.md", self.board("zh-TW", outline))


if __name__ == "__main__":
    unittest.main()
