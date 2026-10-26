#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/cockpit"))
import seat_now as S


class TwoLines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        S.write("abcde", "Implement readable status rows", 2, 3, state_root=self.root)

    def line(self, **kw):
        with patch.object(S, "code_for_window", return_value="abcde"), patch.object(
                S, "_gauge", return_value={"pct": 52, "model": "gpt-6.1-sol"}):
            return S.border_line("@5", "isolated", provider="codex", state_root=self.root, width=86, **kw)

    def test_task_has_full_row_without_context(self):
        line = self.line(now_only=True)
        self.assertIn("Implement readable status rows", line)
        self.assertNotIn("CTX", line)
        self.assertNotIn("gpt-6.1-sol", line)

    def test_context_has_full_row_without_task(self):
        line = self.line(ctx_only=True)
        self.assertIn("CTX 52%", line)
        self.assertIn("gpt-6.1-sol", line)
        self.assertIn("@abcde", line)
        self.assertNotIn("Implement readable status rows", line)

    def test_native_context_engines_do_not_duplicate(self):
        for provider in ("claude", "agy", ""):
            with self.subTest(provider=provider), patch.object(S, "code_for_window") as lookup:
                self.assertEqual(S.border_line("@1", "sock", provider=provider, ctx_only=True), "")
                lookup.assert_not_called()

    def test_new_and_existing_seats_use_two_rows(self):
        self.assertIn("--now-only", S.border_format("codex"))
        fmt = S.context_status_format()
        self.assertIn("--ctx-only", fmt)
        self.assertIn("#{window_id}", fmt)
        self.assertIn("#{@aib_provider}", fmt)
        calls = []
        def tmux(hub, *args):
            calls.append(args)
            if args[0] == "list-windows":
                return "@5\tcodex\n@6\tclaude"
            return ""
        done, bad = S.apply_borders(hubs=["test"], tmux=tmux,
                                    code_for=lambda *_: "abcde", out=io.StringIO())
        self.assertEqual((done, bad), (1, 0))
        self.assertIn(("set-option", "-t", "aihub", "status-format[0]", fmt), calls)
        self.assertFalse(any("@6" in call for call in calls if call[0] == "set-option"))

    def test_captain_refresh_keeps_context_row(self):
        src = (ROOT / "src/cockpit/nav/captains-ctl.sh").read_text()
        self.assertIn('"$COCKPIT/seat_now.py" --context-status-format', src)
        self.assertIn("'status-format[0]' \"$_ctx_fmt\"", src)
        self.assertIn('--apply-borders --border-hub "$HUB"', src)

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
