#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Left content tabs, as eye-verified: 1.人格 / 2.大腦.
. "$(dirname "$0")/cockpit-env.sh"
TABF="$EZ2_COCKPIT_STATE_DIR/left-tab"
[ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane left-tabs 2>/dev/null || true
[ "$(uname -s)" = Darwin ] && MOD="Option" || MOD="Alt"
MOD="${EZ2_MODIFIER_LABEL:-$MOD}"
export EZ2_MODIFIER_LABEL="$MOD"

render_frame() {
  local tab="$1"
  if [ "$tab" = "1" ]; then
    printf '\033[K\n  \033[38;5;245m1.人格\033[0m \033[1;7m 2.大腦 \033[0m\033[K\n \033[38;5;245m%s+Shift+Q 切換\033[0m\033[K\n' "$MOD"
    EZ2_ONESHOT=1 EZ2_H_OFFSET=3 bash "$EZ2_COCKPIT_DIR/pane-left-brain.sh"
  else
    printf '\033[K\n \033[1;7m 1.人格 \033[0m \033[38;5;245m2.大腦\033[0m\033[K\n \033[38;5;245m%s+Shift+Q 切換\033[0m\033[K\n' "$MOD"
    EZ2_ONESHOT=1 EZ2_H_OFFSET=3 bash "$EZ2_COCKPIT_DIR/pane-left-persona.sh"
  fi
}

FIRST_PAINT=1
LAST_FRAME=""
LAST_GEOMETRY=""
REFRESH_TICKS="${EZ2_LEFT_REFRESH_TICKS:-10}"
case "$REFRESH_TICKS" in
  ''|*[!0-9]*|0) REFRESH_TICKS=10 ;;
esac
PANE_SELF_SCRIPT="$EZ2_COCKPIT_DIR/pane-left-tabs.sh"   # BF:碼換了自己重跑
. "$EZ2_COCKPIT_DIR/pane-tick.sh"
pane_tick_init
while true; do
  pane_reexec_if_changed                # BF:腳本換了就 exec 自己(每 ~30 秒問一次)
  pane_wait_if_detached && continue     # 沒人看:不渲染,維持運作(§59)
  pane_wait_if_composing && continue    # 終端機組字中:不渲染(10-10,避開 Terminal 重釘組字區的崩潰;見 pane-tick.sh)
  PANE_GEOM_DIRTY=0
  pane_read_file TAB "$TABF"
  if [ -n "${TMUX_PANE:-}" ]; then
    read -r EZ2_PANE_W EZ2_PANE_H <<EOF
$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-44}" "${LINES:-30}")
EOF
    export EZ2_PANE_W EZ2_PANE_H
  fi
  PANE_GEOMETRY="${EZ2_PANE_W:-44} ${EZ2_PANE_H:-30}"
  FRAME="$(render_frame "$TAB")"
  if [ "$FIRST_PAINT" = "1" ] || [ "$FRAME" != "$LAST_FRAME" ] || \
     [ "$PANE_GEOMETRY" != "$LAST_GEOMETRY" ]; then
    printf '\033[H%s\033[J' "$FRAME"
    LAST_FRAME="$FRAME"
    LAST_GEOMETRY="$PANE_GEOMETRY"
    FIRST_PAINT=0
  fi
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  for ((tick=0; tick<REFRESH_TICKS; tick++)); do
    pane_tick_sleep 0.1
    [ "$PANE_GEOM_DIRTY" = "1" ] && break          # resize 事件(WINCH)
    pane_read_file TAB_NOW "$TABF"
    [ "$TAB_NOW" = "$TAB" ] || break
  done
done
