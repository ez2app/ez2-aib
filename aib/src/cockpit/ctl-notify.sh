#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 四色通知總開關。M-n 只會走 toggle；show 供測試與狀態對帳。
#
#   ctl-notify.sh show [tmux-session]
#   ctl-notify.sh toggle [tmux-session]
#   ctl-notify.sh toggle-level red|yellow|green|blue [tmux-session]
#
# 狀態固定落在目前 AIB instance 的 state dir，不寫 repo，也不讀顯示名稱
# 推測 instance。若由 keymap 呼叫，tmux session 的 @aib_state_dir 是正本。
set -euo pipefail

ACTION="${1:-show}"
LEVEL=""
SESSION=""

case "$ACTION" in
  show|toggle)
    SESSION="${2:-}"
    [ "$#" -le 2 ] || { echo "too many arguments" >&2; exit 2; }
    ;;
  toggle-level)
    LEVEL="${2:-}"
    SESSION="${3:-}"
    case "$LEVEL" in red|yellow|green|blue) ;; *)
      echo "notification level must be red, yellow, green or blue" >&2
      exit 2
      ;;
    esac
    [ "$#" -le 3 ] || { echo "too many arguments" >&2; exit 2; }
    ;;
  *)
    echo "usage: ctl-notify.sh show|toggle [tmux-session] | toggle-level red|yellow|green|blue [tmux-session]" >&2
    exit 2
    ;;
esac

if [ -n "$SESSION" ]; then
  STATE="$(tmux show-option -qv -t "$SESSION" @aib_state_dir 2>/dev/null || true)"
  [ -z "$STATE" ] || export EZ2_COCKPIT_STATE_DIR="$STATE"
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/cockpit-env.sh"

POLICY="$HERE/../messaging/notify_policy.py"
STATE_FILE="$EZ2_COCKPIT_STATE_DIR/notify-state.json"

case "$ACTION" in
  show)
    exec "$EZ2_PY3" "$POLICY" show-state --state "$STATE_FILE"
    ;;
  toggle)
    exec "$EZ2_PY3" "$POLICY" toggle-state --state "$STATE_FILE"
    ;;
  toggle-level)
    exec "$EZ2_PY3" "$POLICY" toggle-state --state "$STATE_FILE" \
      --level "$LEVEL"
    ;;
esac
