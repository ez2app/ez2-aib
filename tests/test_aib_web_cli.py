"""`aib web` hands over to `aibd ctl web` (ADR 0006 §7 W1, PLAN A5): the arguments pass through unchanged, and a
missing aibd is a clear rc 3, not a traceback. No cargo needed: AIBD_BIN points at a fake. POSIX only (#! fake)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RUN = "import sys; sys.argv = ['aib'] + sys.argv[1:]; from aib import cli; sys.exit(cli.main())"


def aib(args, env):
    return subprocess.run([sys.executable, "-c", RUN] + args, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)


@unittest.skipUnless(os.name == "posix", "the fake aibd is a #! script")
class AibWeb(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        self.addCleanup(self.td.cleanup)

    def test_hands_over_to_aibd_ctl_web_with_the_same_arguments(self):
        out = self.d / "argv.txt"
        fake = self.d / "aibd"
        fake.write_text("#!/bin/sh\nprintf '%%s\\n' \"$@\" > %s\n" % out)
        fake.chmod(0o755)
        env = dict(os.environ, AIBD_BIN=str(fake))
        r = aib(["web", "--no-open"], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text().split("\n")[:3], ["ctl", "web", "--no-open"])

    def test_without_aibd_it_says_how_to_get_it(self):
        env = dict(os.environ, PATH=str(self.d))
        env.pop("AIBD_BIN", None)
        r = aib(["web"], env)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("aibd not found", r.stderr)


class AibWebHandOver(unittest.TestCase):
    """In process, on every OS: POSIX replaces the process with aibd; Windows waits for it and passes its rc on,
    because its execv returns to the shell, which then reads the console together with aibd's y/N (a9136 A5 L1)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.remove, str(ROOT))
        from aib import cli
        self.cli = cli
        self.env = mock.patch.dict(os.environ, {"AIBD_BIN": "/fake/aibd"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_windows_waits_for_aibd_and_returns_its_rc(self):
        ran = []
        with mock.patch.object(self.cli.os, "name", "nt"), \
                mock.patch.object(self.cli.os, "execv", side_effect=AssertionError("execv on Windows")), \
                mock.patch.object(self.cli.subprocess, "run",
                                  side_effect=lambda a: ran.append(a) or subprocess.CompletedProcess(a, 7)):
            rc = self.cli._web(["--no-open"])
        self.assertEqual(rc, 7)
        self.assertEqual(ran, [["/fake/aibd", "ctl", "web", "--no-open"]])

    def test_windows_ctrl_c_is_rc_130_not_a_traceback(self):
        def interrupted(a):
            raise KeyboardInterrupt
        with mock.patch.object(self.cli.os, "name", "nt"), \
                mock.patch.object(self.cli.os, "execv", side_effect=AssertionError("execv on Windows")), \
                mock.patch.object(self.cli.subprocess, "run", side_effect=interrupted):
            self.assertEqual(self.cli._web([]), 130)

    def test_posix_replaces_the_process(self):
        seen = []
        with mock.patch.object(self.cli.os, "name", "posix"), \
                mock.patch.object(self.cli.os, "execv", side_effect=lambda p, a: seen.append((p, a))), \
                mock.patch.object(self.cli.subprocess, "run", side_effect=AssertionError("run on POSIX")):
            self.cli._web(["--no-open"])
        self.assertEqual(seen, [("/fake/aibd", ["/fake/aibd", "ctl", "web", "--no-open"])])


if __name__ == "__main__":
    unittest.main()
