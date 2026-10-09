#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 底部 tab 循環 0->1->2->3->0(狀態機正本在 cockpit_lib.next_tab)
# 遷自 ~/ez2-cockpit-demo/bottom-tab-toggle.sh(E2.1;狀態檔改進 state dir,不再寫腳本目錄)
SESSION="${1:-}"
if [ -n "$SESSION" ]; then
  STATE="$(tmux show-option -qv -t "$SESSION" @aib_state_dir 2>/dev/null || true)"
  [ -z "$STATE" ] || export EZ2_COCKPIT_STATE_DIR="$STATE"
fi
. "$(dirname "$0")/cockpit-env.sh"
. "$(dirname "$0")/state-transition.sh"
F="$EZ2_COCKPIT_STATE_DIR/bottom-tab"

_compute() {
  EZ2_CUR="$1" "$EZ2_PY3" -c \
    'import os; from cockpit_lib import next_tab; print(next_tab(os.environ.get("EZ2_CUR", "")))'
}

aib_state_transition "$F" _compute
