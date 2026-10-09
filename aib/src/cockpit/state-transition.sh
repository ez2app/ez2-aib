#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 可 source 的原子狀態轉移器(codex 審 cockpit 域中級三條的共同解)。
#
# 三個原本各自的毛病,根因是同一個「沒有把轉移當成一件事」:
#   1. `cmd > "$F"` 會在計算失敗**之前**就把正本截成空檔;
#   2. read/compute/write 之間沒有鎖,兩個並發 toggle 會遺失一次轉移;
#   3. 寫入非原子時,中斷會留下半個檔案。
#
# 用法:
#   . "$(dirname "$0")/state-transition.sh"
#   aib_state_transition "$STATE_FILE" compute_fn
# compute_fn 收目前值(可為空)於 $1,把新值印到 stdout;非零退出=不動正本。

aib_state_transition() {
  local file="$1" compute="$2"
  local dir lock cur next tmp rc
  dir="$(dirname "$file")"
  mkdir -p "$dir" || return 1
  lock="$file.lock"

  # ED.9(2026-08-13):具名 fd(bash 4.1+)改固定 fd 9——macOS /bin/bash 3.2 對
  # `exec {var}>>` 回 127,四支 cockpit 測試與 AtomicTransitionTest 在 CI macOS 格
  # 全倒。fd 9 與 cockpit-full.sh 的 fd 8 錯開,ensure 鎖持有中呼叫本函式不撞。
  exec 9>>"$lock" || return 1
  if ! flock 9; then
    exec 9>&-
    return 1
  fi

  cur="$(cat "$file" 2>/dev/null || true)"
  next="$("$compute" "$cur")"
  rc=$?
  if [ $rc -ne 0 ] || [ -z "$next" ]; then
    # 計算失敗:正本一個 byte 都不動(舊行為會先截空,再發現算不出來)
    exec 9>&-
    return $rc
  fi

  tmp="$file.tmp.$$"
  printf '%s\n' "$next" > "$tmp" || { exec 9>&-; return 1; }
  mv -f "$tmp" "$file" || { rm -f "$tmp"; exec 9>&-; return 1; }
  exec 9>&-
  return 0
}
