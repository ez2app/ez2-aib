#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# ai-hub 方向鍵選 AI(E2.15):中欄用方向鍵選/跳 AI,鍵鼠與 E2.13 同源。
#
#   ai-nav-ctl.sh --install [--dry-run]     綁 tmux key-table(M-a 進選取模式)
#   ai-nav-ctl.sh --move up|down            移動選取游標(寫 ai-selection.json)
#   ai-nav-ctl.sh --focus                   跳轉到選中 AI(走 select-window,同 mouse-ctl)
#   ai-nav-ctl.sh --cancel                  退出選取模式(清 selection)
#
# 安全設計(理由寫在這裡,免得被改成綁裸方向鍵或 M-方向鍵):
#   裸 ↑↓←→ 留給中欄真 AI 對話;M-方向鍵已被 E2.5 佔滿。故用 tmux key-table
#   'ai-nav':M-a 進入,方向鍵只在此 table 有效,Enter 跳轉/Esc 退出即回歸原義。
#   focus 動作與 E2.13 mouse-ctl 走同一條 select-window,達成鍵鼠一致。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
DIR=""
ROWMAP="$EZ2_COCKPIT_STATE_DIR/ai-rowmap.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --install) MODE="install" ;;
    --move) MODE="move"; DIR="${2:-}"; shift ;;
    --focus) MODE="focus" ;;
    --cancel) MODE="cancel" ;;
    --dry-run) DRY=1 ;;
    --rowmap) ROWMAP="${2:-}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install ai-nav-ctl.sh "$HERE" || exit $?
  else
    echo "ai-nav-ctl.sh: 缺陷十九守衛檔缺席($COCKPIT/install-guard.sh);非 dry 安裝一律 fail-closed 拒裝" >&2
    exit 6
  fi
fi

run() {
  if [ "$DRY" = "1" ]; then printf '+ %s\n' "$*"; else "$@"; fi
}

case "$MODE" in
  install)
    # M-a 進入 ai-nav key-table;table 內方向鍵綁本腳本,離開即回歸原義。
    # 不碰裸方向鍵的全域綁定、不碰 M-方向鍵(E2.5 已佔)。
    run tmux bind-key -n M-a switch-client -T ai-nav
    run tmux bind-key -T ai-nav Up \
      run-shell "$HERE/ai-nav-ctl.sh --move up" \; switch-client -T ai-nav
    run tmux bind-key -T ai-nav Down \
      run-shell "$HERE/ai-nav-ctl.sh --move down" \; switch-client -T ai-nav
    run tmux bind-key -T ai-nav Right run-shell "$HERE/ai-nav-ctl.sh --focus"
    run tmux bind-key -T ai-nav Enter run-shell "$HERE/ai-nav-ctl.sh --focus"
    run tmux bind-key -T ai-nav Left run-shell "$HERE/ai-nav-ctl.sh --cancel"
    run tmux bind-key -T ai-nav Escape run-shell "$HERE/ai-nav-ctl.sh --cancel"
    ;;
  move)
    [ "$DIR" = "up" ] || [ "$DIR" = "down" ] || { echo "--move needs up|down" >&2; exit 2; }
    ACTION="$("$EZ2_PY3" "$HERE/ai_nav.py" move --rowmap "$ROWMAP" \
              --state-dir "$EZ2_COCKPIT_STATE_DIR" --dir "$DIR")"
    [ "$DRY" = "1" ] && printf '+ selected %s\n' "$ACTION" || true
    ;;
  focus)
    ACTION="$("$EZ2_PY3" "$HERE/ai_nav.py" focus --rowmap "$ROWMAP" \
              --state-dir "$EZ2_COCKPIT_STATE_DIR")"
    KIND="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["action"])' "$ACTION")"
    if [ "$KIND" = "focus-hub" ]; then
      WIN="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["window"])' "$ACTION")"
      # 與 E2.13 mouse-ctl focus-hub 同一條 select-window(鍵鼠一致)
      #
      # ★★★ T-rowmap-window(判準 qa3/criteria-rowmap-window-0828,走丙):
      #   rowmap 可能比 hub 活得久,指向的窗可能已經不在。滑鼠那條路
      #   (mouse-ctl.sh:614)早就容忍這件事——`select-window … || exit 0`,
      #   註解逐字「選不到視窗是 stale hit…靜默降級為 passthrough」。
      #   而本檔 :12 自己宣告了「focus 動作與 E2.13 mouse-ctl 走同一條
      #   select-window,達成鍵鼠一致」——**對等在唯一要緊的那一維上是缺的**:
      #   這裡零降級,而 :13 是 `set -euo pipefail`,於是選不到窗會讓整支 rc=1、
      #   stderr 是 tmux 原始錯誤、使用者端沒有任何回饋。
      #   ⇒ 這一段不是給鍵盤加新行為,是**讓那句宣告變成真的**。
      #
      # ★★ R7:降級**必須留痕**,不得照抄 :614 的靜默。
      #   「選不到窗」與「沒有人按鍵」在 noop 之後逐字相同,而兩者要分得開。
      #   留痕沿產出端既有慣例(aib-hdr.py 的 `row_why`):
      #   **值保持原字面(照樣 noop 不跳轉),理由住自己的欄位**——這裡是 `nav_why=`。
      if [ "$DRY" = "1" ]; then
        run tmux -L "$EZ2_AIHUB_SOCKET" select-window -t "aihub:$WIN"
        run bash "$COCKPIT/pane-pending-review.sh" --kick
      elif ! tmux -L "$EZ2_AIHUB_SOCKET" select-window -t "aihub:$WIN" \
             >/dev/null 2>&1; then
        # 降級:不跳轉、不中止(rc 0);而**說得出為什麼**。
        printf 'ai-nav: nav_why=window_gone target=aihub:%s\n' "$WIN" >&2
        printf 'ai-nav: 選不到那個窗(已關閉,或 rowmap 比它活得久);沒有跳轉。\n' >&2
      else
        bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
      fi
    else
      [ "$DRY" = "1" ] && printf '+ noop (no selection)\n' || true
    fi
    ;;
  cancel)
    "$EZ2_PY3" "$HERE/ai_nav.py" cancel --state-dir "$EZ2_COCKPIT_STATE_DIR" >/dev/null
    [ "$DRY" = "1" ] && printf '+ cancelled\n' || true
    ;;
  *)
    echo "usage: ai-nav-ctl.sh --install | --move up|down | --focus | --cancel [--dry-run]" >&2
    exit 2
    ;;
esac
