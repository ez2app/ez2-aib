#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/verify"))
import pushgate


class GenericPopulation(unittest.TestCase):
    def test_dry_run_excludes_non_test_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "tests/test_real.py": "def test_valid():\n    assert True\n",
                "src/mouse/hit_test.py": "import sys\nraise SystemExit(2)\n",
                "tools/verify/test_entry.py": "import sys\nraise SystemExit(2)\n",
            }
            for rel, text in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            env = dict(os.environ, HOME=tmp, AIB_LOCALE="en", EZ2_AIB_LOCALE="en",
                       EZ2_AIB_HOME=str(root / "state"), EZ2_AIB_STATE_ROOT=str(root / "state"))
            subprocess.run(["git", "init", "-q", tmp], env=env, check=True)
            subprocess.run(["git", "-C", tmp, "add", *files], env=env, check=True)
            subprocess.run(["git", "-C", tmp, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-qm", "fixture"], env=env, check=True)
            self.assertEqual(pushgate.all_tests(root), ["tests/test_real.py"])
            result = subprocess.run([sys.executable, str(ROOT / "tools/verify/pushgate.py"),
                                     "--repo", tmp, "--base", "HEAD", "--mode", "generic",
                                     "--jobs", "1", "--dry-run"], env=env, capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("pre-push check: 1 tests", result.stdout)
            self.assertNotIn("pre-push check: 3 tests", result.stdout)

    def test_declared_files_are_not_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".aib").mkdir()
            (root / ".aib/ship.toml").write_text('[test]\nfiles = ["tools/check.py"]\n')
            (root / "tools").mkdir()
            (root / "tools/check.py").write_text("assert True\n")
            self.assertEqual(pushgate.all_tests(root), ["tools/check.py"])


if __name__ == "__main__":
    unittest.main()
