#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 點右欄捲軸不能把鍵盤留在右欄。字會直接印在捲軸上，看起來像捲軸碎掉。
win="${1:-}"
[ -n "$win" ] || exit 0
target="$(env -u TMUX tmux list-panes -t "$win" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub"{print $1; exit}')"
[ -n "$target" ] || exit 0
env -u TMUX tmux select-pane -t "$target" >/dev/null 2>&1 || true
exit 0
