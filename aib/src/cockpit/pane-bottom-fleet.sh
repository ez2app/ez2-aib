#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 主畫面底部:正本艦隊看板(share/claude-ui 的 ez2-fleet-board.py,唯讀 --fleet)
# 遷自 ~/ez2-cockpit-demo/acb-bottom-fleet.sh(E2.1;board 路徑與 python 改 env)
. "$(dirname "$0")/cockpit-env.sh"
while true; do
  W=$(tmux display -p '#{pane_width}' 2>/dev/null)
  clear
  EZ2_COLS="${W:-100}" "$EZ2_PY3" "$EZ2_FLEET_BOARD" --fleet 2>/dev/null
  sleep 5
done
