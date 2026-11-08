#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared tmux sandbox refuses selector overrides; mutation never reaches tmux."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aib_knife_copy import knife_copy
from aib_tmux_sandbox import Sandbox, SandboxRefused

ROOT = Path(__file__).resolve().parents[1]
TEST = 'test-ctx-triggers-unify-abb89-1015.py'


class Guard(unittest.TestCase):
    def test_reject_selector(self):
        with tempfile.TemporaryDirectory(prefix='aibguard-', dir='/private/tmp') as d:
            box = Sandbox(d, shutil.which('tmux') or '/usr/bin/tmux')
            with self.assertRaises(SandboxRefused):
                box.tm('-L', 'wrong', 'kill-server')
            with self.assertRaises(SandboxRefused):
                box.tm('-S', '/tmp/default', 'kill-server')

    @unittest.skipUnless(shutil.which('tmux'), 'needs tmux')
    def test_knife_one_test_switches_back_to_L(self):
        needle = 'r = box.tm("-f", "/dev/null", "new-session", "-d", "-s", "p", cmd, stdin=subprocess.DEVNULL)'
        replacement = 'r = box.tm("-L", "wrong", "-f", "/dev/null", "new-session", "-d", "-s", "p", cmd, stdin=subprocess.DEVNULL)'
        with knife_copy(ROOT, 'tests/' + TEST, needle, replacement, tests=(TEST,)) as tree:
            p = subprocess.run([sys.executable, '-B', str(tree / 'tests' / TEST), 'Install.test_I4b_launch_in_tmux_pane_installs'],
                               cwd=str(tree), capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
            out = p.stdout + p.stderr
            self.assertNotEqual(p.returncode, 0, out)
            self.assertIn('FAIL: test_I4b_launch_in_tmux_pane_installs', out)
            self.assertIn('tmux 沙盒守門拒絕 socket 覆寫', out)
            self.assertNotIn('ERROR:', out)


if __name__ == '__main__':
    unittest.main()
