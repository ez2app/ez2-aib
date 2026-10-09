#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Focus a logical cockpit region without relying on mutable pane indices.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/cockpit-env.sh"
ROLE="${1:-}"
case "$ROLE" in left|hub|right|bottom) ;; *) exit 2 ;; esac
SESSION="$(tmux display-message -p '#{session_name}')"
PANE="$(tmux list-panes -t "$SESSION:cockpit" -F '#{pane_id}|#{@aib_role}' |
  awk -F '|' -v r="$ROLE" '$2 == r { print $1; exit }')"
[ -n "$PANE" ] || exit 1
tmux select-pane -t "$PANE"
