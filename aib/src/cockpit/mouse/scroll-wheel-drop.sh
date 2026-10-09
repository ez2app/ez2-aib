#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 右欄滾輪。只用 shell 內建指令，不再另開程式。
# 開太多程式時 bash 會以 128 結束，tmux 就把那行錯誤印在畫面上。
dir="${1:-}"
sess="${2:-}"
case "$dir" in
  up|down) ;;
  *) exit 0 ;;
esac
sess="${sess#\'}"; sess="${sess%\'}"
sess="${sess#\"}"; sess="${sess%\"}"
inst="${sess#aib-}"
if [ -z "$inst" ] || [ "$inst" = "$sess" ]; then
  exit 0
fi
qdir="${HOME:-}/.ez2-aib/instances/${inst}/cockpit/.scroll-wheel.q"
printf '%s\n' "$dir" > "$qdir/e-$$-$RANDOM" 2>/dev/null || true
exit 0
