"""`aib web` hands over to `aibd ctl web` (ADR 0006 §7 W1, PLAN A5): the arguments pass through unchanged, and a
missing aibd is a clear rc 3, not a traceback. No cargo needed: AIBD_BIN points at a fake. POSIX only (#! fake)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
