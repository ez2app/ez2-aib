#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Toggle the eye-verified content tab in the left or right side pane.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SIDE="${1:-}"
SESSION="${2:-}"
case "$SIDE" in left|right) ;; *) echo "usage: pane-tab-toggle.sh left|right [session]" >&2; exit 2 ;; esac

if [ -n "$SESSION" ]; then
  STATE="$(tmux show-option -qv -t "$SESSION" @aib_state_dir 2>/dev/null || true)"
  [ -z "$STATE" ] || export EZ2_COCKPIT_STATE_DIR="$STATE"
fi
. "$HERE/cockpit-env.sh"
mkdir -p "$EZ2_COCKPIT_STATE_DIR"
. "$HERE/state-transition.sh"
F="$EZ2_COCKPIT_STATE_DIR/$SIDE-tab"

_compute() {
  if [ "$SIDE" = "right" ]; then
    # 10-30 右欄四頁輪切(#446):1.AI(2/0/其他)→ 2.AI用量(1)→ 3.離艙(3)→ 4.艦長艙(4)→ 1.AI
    case "$1" in 1) printf '3\n' ;; 3) printf '4\n' ;; 4) printf '2\n' ;; *) printf '1\n' ;; esac
    return 0
  fi
  case "$1" in 1) printf '0\n' ;; *) printf '1\n' ;; esac
}

aib_state_transition "$F" _compute
