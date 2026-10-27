#!/bin/bash
# SPDX-License-Identifier: Apache-2.0

# Apple Terminal needs indexed colours on the outer connection. Advertising
# setrgbf/setrgbb can make Codex's RGB composer background hide its input text.
# Keep RGB inside the nested hub; the outer server performs the conversion.
# Call after the owned cockpit exists and before attaching the display client.
# The command prefix is supplied by the caller so this never selects a socket.
aib_terminal_compat() {
  [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ] || return 0
  local rule current
  rule='xterm-256color:setrgbf@:setrgbb@:RGB@:Tc@'
  current="$("$@" show-options -sv terminal-overrides)" || return $?
  case $'\n'"$current"$'\n' in
    *$'\n'"$rule"$'\n'*) return 0 ;;
  esac
  "$@" set-option -as terminal-overrides ",$rule"
}
