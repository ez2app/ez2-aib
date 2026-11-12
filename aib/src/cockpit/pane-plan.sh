#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 座艙中欄(Andy 11-13「要，寫進自動組裝」):上格 aib-plan 進度樹,下格 aib-hosts 主機群(pane-hosts.sh 帶參數叫本檔)。
# 用法:pane-plan.sh [aib-plan|aib-hosts]
# 找程式順序:$AIB_PLAN_BIN / $AIB_HOSTS_BIN → ~/.ez2-aib/bin/<名> → PATH。
# 找不到不退出(格子會被 tmux 收掉,自癒又重建,變成閃爍迴圈):印一行四語提示、每 30 秒再找一次。
# 進度樹帶 --seat(見 _seat);人換席位窗,3 秒內換成那一席。測試可用 AIB_PLAN_SEAT 指定。
# 程式是背景子行程(才能邊跑邊看換席):SIGINT 對它是忽略的 ⇒ 只靠 raw mode 把 ^C 當按鍵讀;
# aib-plan / aib-hosts 都是 raw mode TUI。換成不是 raw 的程式時 Ctrl-C 不會停它,用 q。
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

# 本席主軸那幾行(選單列上方)要 --seat。aib-plan 自己只問「本窗格所在的窗」的 @aib_seat,
# 但進度欄在外框、不在席位窗 ⇒ 永遠問不到(Andy 11-13「上方還是沒有回來」)。
# 這裡改問 hub 伺服器:目前有人附著的 session 的作用中窗 = 人正在看的那一席;
# 窗選項 @aib_seat 只有艦長窗有值,其餘窗讀席位檔 seats/w<窗號>.json 的 session_uuid 末 5 碼(席碼)。
_seat() {
  [ "$NAME" = aib-plan ] || return 0
  [ -n "${AIB_PLAN_SEAT:-}" ] && { printf '%s' "$AIB_PLAN_SEAT"; return 0; }
  local sock="${EZ2_AIHUB_SOCKET:-}" rows wid seat f
  # EZ2_AIHUB_SOCKET 是 -L 的名字(aihub / aihub-<艙>,同 cockpit-full.sh 的 hub_tmux);帶斜線才當 socket 路徑
  local sflag=-L; case "$sock" in */*) sflag=-S ;; esac
  [ -n "$sock" ] && rows="$(tmux "$sflag" "$sock" list-windows -a \
    -F '#{session_attached} #{window_active} #{window_id} #{@aib_seat}' 2>/dev/null)"
  # 1) 人正在看的那一窗:窗選項有值就用
  read -r wid seat <<EOF2
$(printf '%s\n' "${rows:-}" | awk '$1 > 0 && $2 == 1 { print $3, $4; exit }')
EOF2
  [ -n "$seat" ] && { printf '%s' "$seat"; return 0; }
  # 2) 同一窗的席位檔
  seat="$(_seat_of_file "${EZ2_AIB_INSTANCE_HOME:-}/seats/w${wid#@}.json")"
  [ -n "$wid" ] && [ -n "$seat" ] && { printf '%s' "$seat"; return 0; }
  # Andy 11-13「強制一定要畫出來」:看的那窗查不到(選單窗、剛開的窗)也不能空 ⇒
  # 3) 艦長窗(有 @aib_seat 的第一窗) 4) 這艙最近用過的席位檔
  seat="$(printf '%s\n' "${rows:-}" | awk 'NF >= 4 { print $4; exit }')"
  [ -n "$seat" ] && { printf '%s' "$seat"; return 0; }
  for f in $(ls -t "${EZ2_AIB_INSTANCE_HOME:-/nonexistent}"/seats/w*.json 2>/dev/null); do
    seat="$(_seat_of_file "$f")"
    [ -n "$seat" ] && { printf '%s' "$seat"; return 0; }
  done
  return 0
}

_seat_of_file() {  # 席碼 = 席位檔 session_uuid 末 5 碼(sed,不每 3 秒起 python)
  [ -f "$1" ] || return 0
  local u
  u="$(sed -n 's/.*"session_uuid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1)"
  [ "${#u}" -ge 5 ] && printf '%s' "${u: -5}"
  return 0
}

# 換席時是從外面收掉 TUI(SIGTERM):它來不及把終端機還原,下一個會把 raw / 替代畫面當原狀存起來 ⇒ 先還原
_tty_reset() {
  stty sane <&3 2>/dev/null
  printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1049l\033[?25h'
}

# Ctrl-C 只該停掉裡面的程式,不該連包裝一起收掉(格子沒了 ⇒ 自癒卡住,審查 M1)
trap ':' INT
exec 3<&0     # 背景子行程預設 stdin=/dev/null;明講接回本格的終端機
while :; do
  BIN="$(_find_bin)"
  if [ -z "$BIN" ]; then
    clear 2>/dev/null
    _msg aib.cockpit.plan_missing "name=$NAME"
    sleep 30
    continue
  fi
  cur="$(_seat)"
  if [ -n "$cur" ]; then "$BIN" --seat "$cur" <&3 & else "$BIN" <&3 & fi
  child=$!
  switched=0
  while kill -0 "$child" 2>/dev/null; do
    sleep 3
    [ "$NAME" = aib-plan ] || continue
    if [ "$(_seat)" != "$cur" ]; then   # 人換到別的席位窗 ⇒ 換那一席的主軸
      switched=1; kill "$child" 2>/dev/null; break
    fi
  done
  wait "$child"; rc=$?
  if [ "$switched" = 1 ]; then _tty_reset; continue; fi
  _msg aib.cockpit.plan_exited "name=$NAME" "rc=$rc"
  sleep 5
done
