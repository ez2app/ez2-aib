#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# border-hints v2.1.0 — 可拖邊界視覺(E2.16 域小件)
#
# 裁決史(the maintainer 08-14 連裁收斂):主案邊界列 ⣿⇕⇔ → grip ·· → 點陣 ⣿ →
# 整條鋪滿 → 曾用 double(═/║)。**09-20 終裁=single**：對話／待審已有圓角
# 面板框，雙框線(上下橫＋右豎)全部移除，不再預設 double。
# 仍可用 EZ2_AIB_BORDER_LINES=double|heavy 覆寫。舊「邊界列+format」方案
# 全數退場(pane-border-status 維持眼驗基線 off,歸 cockpit-full 管)。
#
# 掛載鏈:src/cockpit/aib 於 ensure 後 --install(E2.17:224 負向=cockpit-full
# 零字樣,本 ctl 獨立)。前態保存:首次 install 逐窗存原 pane-border-lines
# 於 @aib_border_saved_lines(冪等重跑不覆寫原態),remove 依保存還原。
# 範圍:$EZ2_TMUX_SESSION 全部視窗(the maintainer 拍板全一致);內層 hub 不碰。
#
#   border-hints-ctl.sh --install [--dry-run]   全窗 single 線(冪等;可覆寫)
#   border-hints-ctl.sh --remove  [--dry-run]   還原保存的前態
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install|--remove)
      if [ -n "$MODE" ] && [ "$MODE" != "${1#--}" ]; then
        echo "border-hints: --install and --remove are mutually exclusive" >&2
        exit 2
      fi
      MODE="${1#--}"
      ;;
    --dry-run) DRY=1 ;;
    *) echo "border-hints: unknown arg $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$MODE" ] || { echo "border-hints: need --install or --remove" >&2; exit 2; }
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install border-hints-ctl.sh "$HERE" || exit $?
  else
    echo "border-hints-ctl.sh: 缺陷十九守衛檔缺席($COCKPIT/install-guard.sh);非 dry 安裝一律 fail-closed 拒裝" >&2
    exit 6
  fi
fi

S="${EZ2_TMUX_SESSION:?}"
LINES_STYLE="${EZ2_AIB_BORDER_LINES:-single}"

run() {
  if [ "$DRY" = "1" ]; then printf 'DRY: %s\n' "$*"; else "$@"; fi
}
outer_tmux() { env -u TMUX tmux "$@"; }
getopt_w() { outer_tmux show-options -wqv -t "$1" "$2" 2>/dev/null || true; }

# 視窗清單:dry-run 用治具(AIB_FAKE_WINDOWS 首欄);真路徑=list-windows,
# rc 不吞(採集失敗=不動任何窗,fail-visible,documented rc=3)。
WINDOWS=""
if [ "$DRY" = "1" ]; then
  WINDOWS="$(printf '%s\n' "${AIB_FAKE_WINDOWS:-$S:cockpit}" | cut -d'|' -f1)"
else
  WINDOWS="$(outer_tmux list-windows -t "$S" -F '#{window_id}' 2>/dev/null)" || {
    echo "border-hints: window snapshot failed; no changes" >&2
    exit 3
  }
fi

while IFS= read -r W; do
  [ -n "$W" ] || continue
  if [ "$MODE" = "install" ]; then
    if [ "$DRY" != "1" ] && [ "$(getopt_w "$W" @aib_border_saved)" != "1" ]; then
      _prev="$(getopt_w "$W" pane-border-lines)"
      run outer_tmux set-window-option -t "$W" @aib_border_saved_lines "${_prev:-single}"
      run outer_tmux set-window-option -t "$W" @aib_border_saved 1
    elif [ "$DRY" = "1" ]; then
      run outer_tmux set-window-option -t "$W" @aib_border_saved 1
    fi
    run outer_tmux set-window-option -t "$W" pane-border-lines "$LINES_STYLE"
  else
    if [ "$DRY" = "1" ]; then
      run outer_tmux set-window-option -t "$W" pane-border-lines single
    else
      _saved="$(getopt_w "$W" @aib_border_saved_lines)"
      run outer_tmux set-window-option -t "$W" pane-border-lines "${_saved:-single}"
    fi
    run outer_tmux set-window-option -u -t "$W" @aib_border_saved 2>/dev/null || true
    run outer_tmux set-window-option -u -t "$W" @aib_border_saved_lines 2>/dev/null || true
  fi
done <<EOF
$WINDOWS
EOF
exit 0
