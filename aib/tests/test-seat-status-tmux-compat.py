#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Keep external tmux coverage separate so pure status tests remain reusable.
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/cockpit"))
import seat_now as S


class TmuxCompatibility(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for format compatibility check")
    def test_legacy_and_new_seat_borders_do_not_duplicate_context(self):
        with tempfile.TemporaryDirectory(prefix="aib-status-") as tmp:
            cmd = [shutil.which("tmux"), "-S", str(Path(tmp) / "socket"), "-f", "/dev/null"]
            try:
                subprocess.run(cmd + ["new-session", "-d", "-s", "test", "sleep 10"],
                               check=True, capture_output=True, timeout=10)
                # Evaluate the exact outer conditional without launching a job.
                fmt = S.context_status_format()
                prefix = "#{?#{m:*--now-only*,#{pane-border-format}},"
                self.assertTrue(fmt.startswith(prefix))
                condition = prefix + "CONTEXT,}"
                for border, want in (("--border combined", ""), ("--border --now-only", "CONTEXT"), ("", "")):
                    subprocess.run(cmd + ["set-option", "-w", "-t", "test:0", "pane-border-format", border],
                                   check=True, capture_output=True, timeout=10)
                    got = subprocess.check_output(cmd + ["display-message", "-p", "-t", "test:0", condition],
                                                  text=True, timeout=10).strip()
                    self.assertEqual(got, want)
            finally:
                subprocess.run(cmd + ["kill-server"], capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
