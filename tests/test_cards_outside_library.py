# SPDX-License-Identifier: Apache-2.0
"""Persona cards (角色.md) elsewhere in the knowledge base are listed, not silently left out of the index.

6ddf walkthrough K6 (10-14): the card scan only reads the role library; an older card under another folder was
quietly not indexed, so it could not be bound and nothing said why. Real `aib newrole` in a temp HOME;
standard library only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
SCAN = ROOT / "aib" / "src" / "memory" / "card_registry.py"
PERSONA = "x\n\n## 職責\na\n\n## 紅線\nb\n\n## 知識域\nc\n"
CARD = "---\nuid: \"zz9zz9\"\n---\n" + PERSONA


class CardsOutsideLibrary(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-outside-"))
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td), "EZ2_AIB_LOCALE": "en"}
        r = self.run_(AIB + ["newrole", "--name", "First", "--prompt", PERSONA])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.vault = self.td / ".ez2-aib" / "vault"
        for rel in ("06-Work/Old agent/角色.md", ".obsidian/plugins/x/角色.md"):
            (self.vault / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / rel).write_text(CARD, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def run_(self, cmd):
        return subprocess.run(cmd, cwd=self.td, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_newrole_tells_the_user(self):
        r = self.run_(AIB + ["newrole", "--name", "Second", "--prompt", PERSONA])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = r.stdout + r.stderr
        self.assertIn("outside the role library", text)
        self.assertIn("06-Work/Old agent/角色.md", text)
        self.assertNotIn(".obsidian", text)

    def test_scan_summary_lists_them(self):
        r = self.run_([sys.executable, str(SCAN), "scan", "--vault", str(self.vault), "--dry-run"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        summary = json.loads(r.stdout)
        self.assertEqual(summary["outside_library"], ["06-Work/Old agent/角色.md"])
        self.assertEqual(summary["outside_library_count"], 1)
        self.assertIn("outside the role library", r.stderr)
        self.assertNotIn("zz9zz9", json.dumps(summary["skipped"]))   # not indexed, and not mistaken for a skip


if __name__ == "__main__":
    unittest.main()
