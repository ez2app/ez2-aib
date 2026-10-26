#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise Apple Terminal colour conversion without touching live tmux servers."""
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src/cockpit/terminal-compat.sh"
RULE = "xterm-256color:setrgbf@:setrgbb@:RGB@:Tc@"


class TerminalCompat(unittest.TestCase):
    def invoke(self, program, current="linux*:AX@", rc=0):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls"
            stub = Path(tmp) / "tmux"
            stub.write_text(
                "#!/usr/bin/env python3\nimport json,os,sys\n"
                "with open(os.environ['CALL_LOG'],'a') as f: "
                "f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                "if sys.argv[1]=='show-options':\n"
                " print(os.environ['CURRENT'])\n"
                " sys.exit(int(os.environ['SHOW_RC']))\n"
            )
            stub.chmod(0o755)
            env = dict(os.environ, TERM_PROGRAM=program, CALL_LOG=str(log),
                       CURRENT=current, SHOW_RC=str(rc))
            result = subprocess.run(
                ["/bin/bash", "-c", 'set -eu; . "$1"; aib_terminal_compat "$2"',
                 "test", str(HELPER), str(stub)], env=env, capture_output=True,
                text=True, timeout=10)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def test_other_terminals_do_not_change(self):
        for program in ("iTerm.app", "WezTerm", "", "vscode"):
            with self.subTest(program=program):
                result, calls = self.invoke(program)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, [])

    def test_preserves_existing_overrides_and_appends_once(self):
        result, calls = self.invoke("Apple_Terminal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1], ["set-option", "-as", "terminal-overrides", "," + RULE])
        result, calls = self.invoke("Apple_Terminal", "linux*:AX@\n" + RULE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_unreadable_options_do_not_mutate_server(self):
        result, calls = self.invoke("Apple_Terminal", rc=3)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(calls), 1)

    def test_ensure_applies_before_launcher_attach(self):
        source = (ROOT / "src/cockpit/cockpit-full.sh").read_text()
        pos = source.index("aib_terminal_compat outer_tmux")
        self.assertGreater(pos, source.index("scrub_outer_session_environment\nfi"))
        self.assertLess(pos, source.index("# A viewer pane may die"))

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for the PTY output check")
    def test_real_tmux_converts_rgb_before_terminal_output(self):
        with tempfile.TemporaryDirectory(prefix="aib-color-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None) as tmp:
            cmd = [shutil.which("tmux"), "-S", str(Path(tmp) / "socket"), "-f", "/dev/null"]
            env = dict(os.environ, HOME=tmp, TERM="xterm-256color", TERM_PROGRAM="Apple_Terminal")
            env.pop("TMUX", None)
            env.pop("COLORTERM", None)
            master = slave = client = None
            try:
                subprocess.run(cmd + ["new-session", "-d", "-s", "test", "sleep 20"],
                               env=env, check=True, capture_output=True, timeout=10)
                # Reproduce the advertised RGB capability even if host terminfo differs.
                subprocess.run(cmd + ["set-option", "-as", "terminal-features", ",xterm-256color:RGB"],
                               env=env, check=True, capture_output=True, timeout=10)
                subprocess.run(["/bin/bash", "-c", '. "$1"; shift; aib_terminal_compat "$@"',
                                "test", str(HELPER)] + cmd, env=env, check=True,
                               capture_output=True, timeout=10)
                # Put RGB text into the pane while no display client is attached.
                subprocess.run(cmd + ["respawn-pane", "-k", "-t", "test:0.0",
                                      "printf '\\033[38;2;238;238;238m\\033[48;2;30;30;30mINPUTCHECK\\033[0m'; sleep 20"],
                               env=env, check=True, capture_output=True, timeout=10)
                master, slave = pty.openpty()
                client = subprocess.Popen(cmd + ["attach", "-t", "test"], env=env,
                                          stdin=slave, stdout=slave, stderr=slave,
                                          start_new_session=True)
                output = bytearray()
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and b"INPUTCHECK" not in output:
                    if select.select([master], [], [], 0.2)[0]:
                        output.extend(os.read(master, 65536))
                    self.assertLess(len(output), 262144)
                self.assertIn(b"INPUTCHECK", output)
                self.assertNotIn(b"38;2;", output)
                self.assertNotIn(b"48;2;", output)
                self.assertIn(b"38;5;", output)
                info = subprocess.check_output(cmd + ["info"], env=env, timeout=10)
                self.assertIn(b"setrgbf: [missing]", info)
                self.assertIn(b"setrgbb: [missing]", info)
            finally:
                subprocess.run(cmd + ["kill-server"], env=env, capture_output=True, timeout=10)
                if client is not None:
                    client.wait(timeout=10)
                for fd in (master, slave):
                    if fd is not None:
                        os.close(fd)


if __name__ == "__main__":
    unittest.main()
