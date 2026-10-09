#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB-UI-NAV-v1: focus-aware navigation for the four eye-verified regions.
#
#   region-nav-ctl.sh --install [--dry-run]
#   region-nav-ctl.sh --tab <pane-id> <outer-session>
#   region-nav-ctl.sh --next-region <pane-id> <outer-session>
#   region-nav-ctl.sh --focus <role> <outer-session>
#
# Tab changes a tab inside the active region. Shift+Tab changes only the outer
# region (left -> hub -> right -> bottom -> left). The binding is server-wide,
# so it is guarded by both the cockpit-window marker and a known @aib_role;
# every other pane receives the original key.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
PANE=""
ROLE=""
SESSION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --install)
      MODE="install"
      ;;
    --tab)
      MODE="tab"
      PANE="${2:-}"
      SESSION="${3:-}"
      shift 2
      ;;
    --next-region)
      MODE="next-region"
      PANE="${2:-}"
      SESSION="${3:-}"
      shift 2
      ;;
    --focus)
      MODE="focus"
      ROLE="${2:-}"
      SESSION="${3:-}"
      shift 2
      ;;
    --dry-run)
      DRY=1
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install region-nav-ctl.sh "$HERE" || exit $?
  else
    echo "region-nav-ctl.sh: 缺陷十九守衛檔缺席($COCKPIT/install-guard.sh);非 dry 安裝一律 fail-closed 拒裝" >&2
    exit 6
  fi
fi

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

outer_tmux() {
  env -u TMUX tmux "$@"
}

known_role() {
  case "$1" in
    left|hub|right|bottom) return 0 ;;
    *) return 1 ;;
  esac
}

pane_rows() {
  outer_tmux list-panes -t "$1:cockpit" -F '#{pane_id}|#{@aib_role}'
}

role_for_pane() {
  local pane="$1" session="$2" rows matches role
  rows="$(pane_rows "$session")" || return 1
  matches="$(printf '%s\n' "$rows" | awk -F '|' -v p="$pane" '$1 == p { print $2 }')"
  [ "$(printf '%s\n' "$matches" | awk 'NF { n++ } END { print n+0 }')" -eq 1 ] || return 1
  role="$(printf '%s\n' "$matches" | awk 'NF { print; exit }')"
  known_role "$role" || return 1
  printf '%s\n' "$role"
}

pane_for_role() {
  local role="$1" session="$2" rows matches pane
  known_role "$role" || return 1
  rows="$(pane_rows "$session")" || return 1
  matches="$(printf '%s\n' "$rows" | awk -F '|' -v r="$role" '$2 == r { print $1 }')"
  [ "$(printf '%s\n' "$matches" | awk 'NF { n++ } END { print n+0 }')" -eq 1 ] || return 1
  pane="$(printf '%s\n' "$matches" | awk 'NF { print; exit }')"
  [ -n "$pane" ] || return 1
  printf '%s\n' "$pane"
}

focus_role() {
  local role="$1" session="$2" target
  target="$(pane_for_role "$role" "$session")" || {
    echo "cockpit role is missing or not unique: $role" >&2
    return 3
  }
  run env -u TMUX tmux select-pane -t "$target"
}

case "$MODE" in
  install)
    # @aib_cockpit is a window option. It prevents the root-table bindings
    # from treating an unrelated pane that happens to use @aib_role as ours.
    ROLE_CONDITION='#{&&:#{==:#{@aib_cockpit},1},#{||:#{==:#{@aib_role},left},#{||:#{==:#{@aib_role},hub},#{||:#{==:#{@aib_role},right},#{==:#{@aib_role},bottom}}}}}'
    NAV_SCRIPT_Q=""
    printf -v NAV_SCRIPT_Q '%q' "$HERE/region-nav-ctl.sh"
    # Every value expanded by tmux is shell-quoted with the q modifier before
    # run-shell sees it. Session names are user-controlled and may contain
    # quotes or command substitutions; ordinary shell quotes are not enough.
    TAB_COMMAND="run-shell 'bash $NAV_SCRIPT_Q --tab #{q:pane_id} #{q:session_name}'"
    REGION_COMMAND="run-shell 'bash $NAV_SCRIPT_Q --next-region #{q:pane_id} #{q:session_name}'"
    run env -u TMUX tmux bind-key -n Tab \
      if-shell -F "$ROLE_CONDITION" "$TAB_COMMAND" "send-keys Tab"
    run env -u TMUX tmux bind-key -n BTab \
      if-shell -F "$ROLE_CONDITION" "$REGION_COMMAND" "send-keys BTab"
    ;;
  tab)
    [ -n "$PANE" ] && [ -n "$SESSION" ] || {
      echo "--tab needs <pane-id> <outer-session>" >&2
      exit 2
    }
    ROLE="$(role_for_pane "$PANE" "$SESSION")" || {
      echo "pane is not a unique cockpit region: $PANE" >&2
      exit 3
    }
    STATE="$(outer_tmux show-option -qv -t "$SESSION" @aib_state_dir 2>/dev/null || true)"
    [ -n "$STATE" ] || {
      echo "cockpit state directory is missing for session: $SESSION" >&2
      exit 3
    }
    case "$ROLE" in
      left|right)
        EZ2_COCKPIT_STATE_DIR="$STATE" bash "$COCKPIT/pane-tab-toggle.sh" "$ROLE"
        ;;
      bottom)
        EZ2_COCKPIT_STATE_DIR="$STATE" bash "$COCKPIT/ctl-bottom-tab.sh"
        ;;
      hub)
        HUB="$(outer_tmux show-option -qv -t "$SESSION" @aib_hub_socket 2>/dev/null || true)"
        [ -n "$HUB" ] || {
          echo "ai-hub socket is missing for session: $SESSION" >&2
          exit 3
        }
        env -u TMUX tmux -L "$HUB" next-window -t aihub
        ;;
    esac
    ;;
  next-region)
    [ -n "$PANE" ] && [ -n "$SESSION" ] || {
      echo "--next-region needs <pane-id> <outer-session>" >&2
      exit 2
    }
    ROLE="$(role_for_pane "$PANE" "$SESSION")" || {
      echo "pane is not a unique cockpit region: $PANE" >&2
      exit 3
    }
    case "$ROLE" in
      left) NEXT=hub ;;
      hub) NEXT=right ;;
      right) NEXT=bottom ;;
      bottom) NEXT=left ;;
    esac
    focus_role "$NEXT" "$SESSION"
    ;;
  focus)
    [ -n "$ROLE" ] && [ -n "$SESSION" ] || {
      echo "--focus needs <role> <outer-session>" >&2
      exit 2
    }
    known_role "$ROLE" || {
      echo "unknown cockpit role: $ROLE" >&2
      exit 3
    }
    focus_role "$ROLE" "$SESSION"
    ;;
  *)
    echo "usage: region-nav-ctl.sh --install | --tab <pane-id> <outer-session> | --next-region <pane-id> <outer-session> | --focus <role> <outer-session> [--dry-run]" >&2
    exit 2
    ;;
esac
