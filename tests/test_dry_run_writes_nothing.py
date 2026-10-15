# SPDX-License-Identifier: Apache-2.0
"""`--dry-run` writes nothing under HOME (or in the working directory).

New-user walkthrough (10-14): `aib newrole … --dry-run` on a new machine created ~/.ez2-aib with ~60 secrets
templates, a ledger and readme files (it ran the state-tree bootstrap and created the role-library folder even when
only trying). The check compares every file (path + content hash) and directory under HOME before and after.
Python's own bytecode cache (~/.cache/ez2-aib/pycache, used by every Python program aib runs) is left out.
Standard library only.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
PERSONA = "Reviews code.\n\n## 職責\nReview diffs.\n\n## 紅線\nNo untested approvals.\n\n## 知識域\nPython.\n"
PYCACHE = os.path.join(".cache", "ez2-aib", "pycache")


class DryRunWritesNothing(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-dryrun-"))
        self.home = self.td / "home"
        self.work = self.td / "work"
        self.home.mkdir()
        self.work.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def snapshot(self):
        seen = {}
        for dirpath, dirnames, filenames in os.walk(self.td):   # HOME and the working directory
            rel = os.path.relpath(dirpath, self.home)
            if rel == PYCACHE or rel.startswith(PYCACHE + os.sep):
                dirnames[:] = []
                continue
            if rel not in (".cache", os.path.join(".cache", "ez2-aib")):   # just the parents of the bytecode cache
                seen[rel + "/"] = "dir"
            for name in filenames:
                p = os.path.join(dirpath, name)
                with open(p, "rb") as fh:
                    seen[os.path.join(rel, name)] = hashlib.sha256(fh.read()).hexdigest()
        return seen

    def aib(self, *args):
        # macOS built-in /usr/bin/python3 (3.9) caches bytecode under HOME/Library/Caches before aib can redirect it;
        # that is Python's cache, not aib writing, so keep it out of the snapshot.
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "EZ2_AIB_LOCALE": "en", "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(AIB + list(args), cwd=self.work, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def assert_dry_run_clean(self, *args):
        before = self.snapshot()
        r = self.aib(*args)
        after = self.snapshot()
        added = sorted(set(after) - set(before))
        changed = sorted(k for k in before if k in after and before[k] != after[k])
        removed = sorted(set(before) - set(after))
        self.assertEqual((added, changed, removed), ([], [], []), "%s\n%s" % (args, r.stdout + r.stderr))
        return r

    def test_newrole_on_a_new_machine(self):
        r = self.assert_dry_run_clean("newrole", "--name", "Reviewer", "--prompt", PERSONA, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run, nothing written", r.stderr)

    def test_newrole_after_a_real_one(self):
        first = self.aib("newrole", "--name", "First", "--prompt", PERSONA)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        r = self.assert_dry_run_clean("newrole", "--name", "Second", "--prompt", PERSONA, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_newrole_with_an_empty_vault(self):
        # The vault folder exists but has no role library yet: a real run would create it, so a dry run must
        # succeed too (and still create nothing).
        (self.home / ".ez2-aib" / "vault").mkdir(parents=True)
        r = self.assert_dry_run_clean("newrole", "--name", "Reviewer", "--prompt", PERSONA, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run, nothing written", r.stderr)

    def test_other_dry_runs(self):
        self.assert_dry_run_clean("newproj", "--title", "My blog", "--dry-run")
        # `aib dm --dry-run` is not covered here yet: cockpit-env.sh (shared by every cockpit script) creates an empty
        # ~/.ez2-aib/cockpit state folder before dm-ctl.sh sees --dry-run; left for after the dm-ctl.sh change (#13).
        self.assert_dry_run_clean("claude", "safe", "dev", "--dry-run")


if __name__ == "__main__":
    unittest.main()
