#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 左下大腦樹開合切換 1<->2(狀態機正本在 cockpit_lib.toggle_brain_depth)
# 遷自 ~/ez2-cockpit-demo/brain-depth-toggle.sh(E2.1)
SESSION="${1:-}"
if [ -n "$SESSION" ]; then
  STATE="$(tmux show-option -qv -t "$SESSION" @aib_state_dir 2>/dev/null || true)"
  [ -z "$STATE" ] || export EZ2_COCKPIT_STATE_DIR="$STATE"
fi
. "$(dirname "$0")/cockpit-env.sh"
F="$EZ2_COCKPIT_STATE_DIR/brain-depth"
. "$(dirname "$0")/state-transition.sh"

_compute() {
  EZ2_CUR="$1" "$EZ2_PY3" -c \
    'import os; from cockpit_lib import toggle_brain_depth; print(toggle_brain_depth(os.environ.get("EZ2_CUR", "")))'
}

aib_state_transition "$F" _compute
