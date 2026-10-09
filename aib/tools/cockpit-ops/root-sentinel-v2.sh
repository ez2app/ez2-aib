#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# root 執行哨 v2(@6)supervisor:每 20 秒驗 aihub tmux 服務存活+四席窗(@3 @4 @5 @6),
# 異常立即 printf 寫 /dev/ttys019 報總艦長;同時代管 captain-patrol.sh(其觸發退出時本腳本一併退出,由 @6 重掛)。
# 退出碼:10=aihub 服務無回應 11=席位窗消失 12=rescue 分支消失 0=patrol 自然退出(TRIGGER/HEARTBEAT,詳 patrol.log 尾行)
# 告警管道絕不寫死 tty:寫死的裝置在該終端消失後會靜默失效。
# 2026-08-10 實案:原值是總艦長 a seat 的終端,該 session 一歿裝置檔即不存在,
# 而原本的 2>/dev/null 把寫入失敗一併吞掉——監督者的告警全寫進虛空且無人知道。
# 未設定就只進 log 並明說,絕不假裝送出去了。
TTY="${AIB_SENTINEL_TTY:-}"
DEMO="$HOME/ez2-cockpit-demo"
LOG="$DEMO/root-sentinel.log"
SEATS="@3 @4 @5 @6"
REPO="$HOME/ez2-aib"
RESCUE_BRANCH="rescue/gen1-lanes-20260809"

say_tty() {
  local msg="root@6:$1"
  printf '%s\n' "$msg" >> "$LOG"
  if [ -z "$TTY" ]; then
    printf 'SENTINEL: 未設定 AIB_SENTINEL_TTY,本則告警只進 log\n' >> "$LOG"
    return 0
  fi
  if [ ! -c "$TTY" ]; then
    printf 'SENTINEL: 告警管道失效(非字元裝置):%s\n' "$TTY" | tee -a "$LOG" >&2
    return 1
  fi
  if ! printf '%s\n' "$msg" > "$TTY"; then
    printf 'SENTINEL: 寫入告警管道失敗:%s\n' "$TTY" | tee -a "$LOG" >&2
    return 1
  fi
  return 0
}

# v2 編制窗名(排除自己 root-claude-1,避免把自己的權限框當觸發)
export EZ2_WORKERS="${EZ2_WORKERS:-dev1-claude-1 dev2-claude-1 qa-claude-1}"
# 閒置席(空提示待令)每 10 分即觸停滯造成喚醒風暴;降頻至 30 分(選項/窗消失/標記觸發不受影響,仍 20 秒一輪)
export EZ2_STALL_MIN="${EZ2_STALL_MIN:-30}"
bash "$DEMO/captain-patrol.sh" >> "$LOG" 2>&1 &
patrol_pid=$!

while :; do
  if ! wins=$(tmux -L aihub list-windows -a -F '#{window_id}' 2>/dev/null); then
    say_tty "SENTINEL aihub tmux 服務無回應($(date '+%H:%M:%S'))"
    echo "SENTINEL: aihub server down $(date '+%H:%M:%S')" >> "$LOG"
    kill "$patrol_pid" 2>/dev/null
    exit 10
  fi
  missing=""
  for w in $SEATS; do
    printf '%s\n' "$wins" | grep -qx "$w" || missing="$missing $w"
  done
  if [ -n "$missing" ]; then
    say_tty "SENTINEL 席位窗消失:$missing($(date '+%H:%M:%S'))"
    echo "SENTINEL: missing$missing $(date '+%H:%M:%S')" >> "$LOG"
    kill "$patrol_pid" 2>/dev/null
    exit 11
  fi
  if ! git -C "$REPO" show-ref --verify --quiet "refs/heads/$RESCUE_BRANCH"; then
    say_tty "SENTINEL rescue 分支消失:$RESCUE_BRANCH($(date '+%H:%M:%S'))"
    echo "SENTINEL: rescue branch missing $(date '+%H:%M:%S')" >> "$LOG"
    kill "$patrol_pid" 2>/dev/null
    exit 12
  fi
  kill -0 "$patrol_pid" 2>/dev/null || exit 0
  sleep 20
done
