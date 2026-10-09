#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Open ChatGPT Usage settings for reset (active account button only)."""
from __future__ import annotations

import os
import subprocess
import sys

USAGE_SETTINGS_URL = "https://chatgpt.com/#settings/Usage"


def main() -> int:
    # Args kept for mouse-ctl compatibility: cockpit, acct_id, [state]
    if len(sys.argv) < 3:
        return 2
    cockpit, acct_id = sys.argv[1], sys.argv[2]
    sys.path.insert(0, cockpit)
    try:
        import usage_accounts as ua
    except Exception:
        ua = None
    if ua is not None:
        try:
            data = ua.load()
            active = str(data.get("active_codex_oauth_id") or "")
            if active and active != acct_id:
                # Soft guard: only main login should reach here; ignore stray clicks.
                return 0
        except Exception:
            pass
    # macOS: open default browser to Usage settings (reset lives there)
    subprocess.run(["open", USAGE_SETTINGS_URL], check=False)
    pane = os.environ.get("TMUX_PANE") or ""
    if pane:
        subprocess.run(
            ["tmux", "set-option", "-p", "-t", pane, "@aib_pane", "ai-list"],
            check=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
