#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 畫面變動後重算左欄高度：
#   總高 − 私訊(≡) − 待審(≡, floor≥12) → 剩餘強制給中間
#   開 hub-io：中間＝回應+輸入；關 hub-io：中間＝CLI(hub)
# 由 client-resized / window-resized / ≡ 切換呼叫；失敗不阻斷外層 resize。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"

S="${EZ2_TMUX_SESSION:-${1:-}}"
[ -n "$S" ] || exit 0

# state：優先既有 ENV；否則從 session @aib_state_dir
if [ -z "${EZ2_COCKPIT_STATE_DIR:-}" ]; then
  EZ2_COCKPIT_STATE_DIR="$(env -u TMUX tmux show-option -qv -t "$S" @aib_state_dir 2>/dev/null || true)"
fi
export EZ2_TMUX_SESSION="$S"
export EZ2_COCKPIT_STATE_DIR="${EZ2_COCKPIT_STATE_DIR:-}"

# instance home：…/instances/<id>/cockpit → parent
if [ -z "${EZ2_AIB_INSTANCE_HOME:-}" ] && [ -n "$EZ2_COCKPIT_STATE_DIR" ]; then
  _base="$(basename "$EZ2_COCKPIT_STATE_DIR")"
  if [ "$_base" = "cockpit" ] || [ "$_base" = "state" ]; then
    EZ2_AIB_INSTANCE_HOME="$(dirname "$EZ2_COCKPIT_STATE_DIR")"
  fi
fi
if [ -n "${EZ2_AIB_INSTANCE_HOME:-}" ]; then
  export EZ2_AIB_INSTANCE_HOME
  [ -n "${EZ2_AIB_INSTANCE:-}" ] || export EZ2_AIB_INSTANCE="$(basename "$EZ2_AIB_INSTANCE_HOME")"
fi

# hub socket（Claude 關開判定）
if [ -z "${EZ2_AIHUB_SOCKET:-}" ]; then
  EZ2_AIHUB_SOCKET="$(env -u TMUX tmux show-option -qv -t "$S" @aib_hub_socket 2>/dev/null || true)"
  [ -n "$EZ2_AIHUB_SOCKET" ] && export EZ2_AIHUB_SOCKET
fi

# 無 hub-frame 則非 AIB 左欄艙，略過
has_frame="$(env -u TMUX tmux list-panes -t "$S" -F '#{@aib_role}' 2>/dev/null | grep -cx 'hub-frame' || true)"
[ "${has_frame:-0}" = "1" ] || exit 0

bash "$HERE/pane-hub-frame.sh" --apply-input-height >/dev/null 2>&1 || true
bash "$HERE/pane-pending-review.sh" --apply-pending-height >/dev/null 2>&1 || true
exit 0
