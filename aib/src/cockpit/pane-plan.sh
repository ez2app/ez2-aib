#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 座艙中欄(Andy 11-13「要，寫進自動組裝」):上格 aib-plan 進度樹,下格 aib-hosts 主機群(pane-hosts.sh 帶參數叫本檔)。
# 用法:pane-plan.sh [aib-plan|aib-hosts]
# 找程式順序:$AIB_PLAN_BIN / $AIB_HOSTS_BIN → ~/.ez2-aib/bin/<名> → PATH。
# 找不到不退出(格子會被 tmux 收掉,自癒又重建,變成閃爍迴圈):印一行四語提示、每 30 秒再找一次。
# 程式結束(按 q、當掉)也一樣:印 rc、5 秒後重開。不要這一欄 ⇒ aib plan off。
. "$(dirname "$0")/cockpit-env.sh"
NAME="${1:-aib-plan}"
case "$NAME" in
  aib-plan) OVERRIDE="${AIB_PLAN_BIN:-}" ;;
  aib-hosts) OVERRIDE="${AIB_HOSTS_BIN:-}" ;;
  *) echo "pane-plan.sh: unknown program $NAME" >&2; exit 2 ;;
esac
[ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane "${NAME#aib-}" 2>/dev/null

_msg() {  # _msg <key> [k=v ...];python 起不來就印鍵名,讓壞掉的樣子跟正確的不同
  local key="$1" out; shift
  local args=() kv
  for kv in "$@"; do args+=(--param "$kv"); done
  out="$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/i18n_lib.py" "${args[@]}" "$key" 2>/dev/null)" || out="$key"
  printf '%s\n' "${out:-$key}"
}

_find_bin() {
  if [ -n "$OVERRIDE" ]; then
    [ -x "$OVERRIDE" ] && printf '%s' "$OVERRIDE"
    return 0
  fi
  if [ -x "$HOME/.ez2-aib/bin/$NAME" ]; then
    printf '%s' "$HOME/.ez2-aib/bin/$NAME"
    return 0
  fi
  command -v "$NAME" 2>/dev/null || true
}

while :; do
  BIN="$(_find_bin)"
  if [ -z "$BIN" ]; then
    clear 2>/dev/null
    _msg aib.cockpit.plan_missing "name=$NAME"
    sleep 30
    continue
  fi
  "$BIN"
  rc=$?
  _msg aib.cockpit.plan_exited "name=$NAME" "rc=$rc"
  sleep 5
done
