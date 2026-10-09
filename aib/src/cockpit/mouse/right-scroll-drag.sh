#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 拖右欄捲軸。每移動一列都會呼叫一次；這裡只記下游標的列。
# 同一次拖曳只留一支 python 在改位置，其餘立刻結束。
set -euo pipefail
y="${1:-}"
sess="${2:-}"
case "$y" in
  ''|*[!0-9]*) exit 0 ;;
esac
sd="${EZ2_COCKPIT_STATE_DIR:-}"
if [ -n "$sess" ]; then
  looked="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_state_dir 2>/dev/null || true)"
  if [ -z "$looked" ]; then
    inst="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_instance 2>/dev/null || true)"
    [ -n "$inst" ] && looked="${EZ2_AIB_INSTANCE_ROOT:-$HOME/.ez2-aib/instances}/$inst/cockpit"
  fi
  [ -n "$looked" ] && sd="$looked"
fi
[ -n "$sd" ] && [ -d "$sd" ] || exit 0
[ -f "$sd/scrollbar-grab" ] || exit 0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${EZ2_PY3:-python3}"
printf '%s\n' "$y" > "$sd/.scroll-drag.y.tmp"
mv -f "$sd/.scroll-drag.y.tmp" "$sd/.scroll-drag.y"
lock="$sd/.scroll-drag.lock"
if ! mkdir "$lock" 2>/dev/null; then
  wpid=""
  [ -f "$lock/pid" ] && wpid="$(cat "$lock/pid" 2>/dev/null || true)"
  if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null; then
    exit 0
  fi
  rm -rf "$lock"
  mkdir "$lock" 2>/dev/null || exit 0
fi
printf '%s\n' "$$" > "$lock/pid"
trap 'rm -rf "$lock"' EXIT
"$PY" "$HERE/right_scroll_drag.py" "$sd"
