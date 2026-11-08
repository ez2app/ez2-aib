#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared tmux sandbox refuses selector overrides; mutation never reaches tmux."""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from aib_knife_copy import knife_copy
from aib_tmux_sandbox import Sandbox, SandboxRefused, _shim_main, reject_global_selector

ROOT = Path(__file__).resolve().parents[1]
TEST = 'test-ctx-triggers-unify-abb89-1015.py'


class Guard(unittest.TestCase):
    def test_global_options_only(self):
        for args in (['-u', '-L', 'other', 'ls'], ['-f', '/dev/null', '-S', '/tmp/x', 'ls'],
                     ['-Lother', 'ls'], ['-uLother', 'ls']):
            with self.subTest(args=args), self.assertRaises(SandboxRefused):
                reject_global_selector(args)
        for args in (['capture-pane', '-p', '-S', '-'], ['select-pane', '-L'],
                     ['-f', '/dev/null', 'capture-pane', '-S', '-']):
            with self.subTest(args=args):
                reject_global_selector(args)

    def test_shim_rejects_alias_and_rewrites_selector(self):
        with tempfile.TemporaryDirectory(prefix='aibguard-', dir='/private/tmp') as d:
            box = Sandbox(d, '/usr/bin/tmux')
            prefix = [d, str(box.sock), box.tmux_bin, 'expected']
            with patch('aib_tmux_sandbox.os.execve') as execute:
                with self.assertRaises(SandboxRefused):
                    _shim_main(prefix + ['-L', 'wrong', 'list-sessions'])
                execute.assert_not_called()
                _shim_main(prefix + ['-L', 'expected', 'capture-pane', '-S', '-'])
                self.assertEqual(execute.call_args.args[1],
                                 [box.tmux_bin, '-S', str(box.sock), 'capture-pane', '-S', '-'])

    @unittest.skipUnless(shutil.which('tmux'), 'needs tmux')
    def test_shim_capture_accepts_command_option(self):
        with tempfile.TemporaryDirectory(prefix='aibguard-', dir='/private/tmp') as d:
            box = Sandbox(d, shutil.which('tmux'))
            shim = box.make_shim(Path(d) / 'tmux', 'expected')
            try:
                self.assertEqual(box.tm('new-session', '-d', '-s', 'x', 'printf marker; sleep 10').returncode, 0)
                deadline = time.monotonic() + 3
                while True:
                    p = subprocess.run([shim, '-L', 'expected', 'capture-pane', '-t', 'x', '-p', '-S', '-', '-E', '-'],
                                       text=True, capture_output=True, timeout=10)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    if 'marker' in p.stdout or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                self.assertIn('marker', p.stdout)
            finally:
                box.cleanup()

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
