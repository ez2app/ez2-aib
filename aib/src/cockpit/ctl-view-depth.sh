#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 戰情室展開深度控制:more=多開一層 其他=收一層(範圍 1-3;正本在 cockpit_lib.next_view_depth)
# 遷自 ~/ez2-cockpit-demo/acb-sandbox/depth-ctl.sh(E2.1)
SESSION="${2:-}"
if [ -n "$SESSION" ]; then
  STATE="$(tmux show-option -qv -t "$SESSION" @aib_state_dir 2>/dev/null || true)"
  [ -z "$STATE" ] || export EZ2_COCKPIT_STATE_DIR="$STATE"
fi
. "$(dirname "$0")/cockpit-env.sh"
F="$EZ2_COCKPIT_STATE_DIR/view-depth"
# 08-14:接上 state-transition.sh——原 `cmd > "$F"` 直導向是 depth/tab 類
# 唯一漏網的三病寫者(先截空/無鎖/非原子),照 ctl-brain-depth.sh 既有形。
. "$(dirname "$0")/state-transition.sh"
EZ2_DIR="${1:-less}"

_compute() {
  EZ2_CUR="$1" EZ2_DIR="$EZ2_DIR" "$EZ2_PY3" -c \
    'import os; from cockpit_lib import next_view_depth; print(next_view_depth(os.environ.get("EZ2_CUR", ""), os.environ.get("EZ2_DIR", "less")))'
}

aib_state_transition "$F" _compute
