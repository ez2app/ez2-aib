# SPDX-License-Identifier: Apache-2.0
"""`aib dm … --dry-run` creates nothing under HOME.

10-14: on a machine with no ~/.ez2-aib yet, `aib dm abcd/12345 hi --dry-run` left an empty ~/.ez2-aib/cockpit/ —
dm-ctl.sh sources cockpit-env.sh (which creates the cockpit state folder) before it parses --dry-run. The launcher now
sets the same no-state-write switch the seat dry run uses. Every file and folder under the temp HOME is compared
before and after (Python's bytecode cache under ~/.cache/ez2-aib/pycache is left out). Standard library only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]


def snapshot(home):
    seen = set()
    for dirpath, dirnames, filenames in os.walk(home):
        rel = os.path.relpath(dirpath, home)
        if rel == ".cache" or rel.startswith(".cache" + os.sep):
            dirnames[:] = []
            continue
        seen.add(rel + "/")
        seen.update(os.path.join(rel, f) for f in filenames)
    return seen


class DmDryRunNoStateDir(unittest.TestCase):
    def test_nothing_created(self):
        td = Path(tempfile.mkdtemp(prefix="aib-dmdry-"))
        try:
            # macOS built-in /usr/bin/python3 (3.9) caches bytecode under HOME/Library/Caches before aib can redirect it;
            # that is Python's cache, not aib writing, so keep it out of the snapshot.
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(td), "EZ2_AIB_LOCALE": "en", "PYTHONDONTWRITEBYTECODE": "1"}
            before = snapshot(td)
            r = subprocess.run(AIB + ["dm", "abcd/12345", "hello", "--dry-run"], cwd=td, env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(sorted(snapshot(td) - before), [], r.stdout + r.stderr)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
