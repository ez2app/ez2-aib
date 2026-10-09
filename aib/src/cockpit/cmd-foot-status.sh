#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 回應顯示區底蓋一行（aihub status-format[1]）。stdout 只能有這一行。
export PATH="/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin:$PATH"
sock="${EZ2_AIHUB_SOCKET:-}"
w=
if [ -n "$sock" ]; then
  w=$(tmux -L "$sock" display-message -p '#{client_width}' 2>/dev/null || true)
fi
case "$w" in ''|*[!0-9]*) w=80 ;; esac
[ "$w" -lt 16 ] && w=16
inner=$((w - 2))
[ "$inner" -lt 8 ] && inner=8
PY=/opt/homebrew/bin/python3
[ -x "$PY" ] || PY=$(command -v python3 || true)
if [ -n "$PY" ]; then
  exec "$PY" -c "import sys; w=int(sys.argv[1]); sys.stdout.write('╰'+('\u2500'*w)+'╯')" "$inner"
fi
printf '+'; printf '%*s' "$inner" '' | tr ' ' '-'; printf '+'
