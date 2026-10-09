#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 焦點感知框內視圖切換(E2.16):每框視圖標籤可點 + 選中框 tab 循環 + 視覺焦點。
#
#   view-nav-ctl.sh --install [--dry-run]   綁 tab 標記放行 + 視覺焦點
#   view-nav-ctl.sh --tab [--reverse]       選中框按 tab:切該框下一個視圖
#
# 安全設計(理由寫在這,免得被改成裸吃 Tab):
#   中欄 ai-hub 跑真 AI 對話,Tab 是它的輸入(補全/縮排),絕不能全域攔截。
#   故 Tab 綁定用 @aib_pane 標記條件放行(同 E2.13 mouse-ctl):只有標記過的
#   唯讀面板內 Tab 才切視圖,沒標記的面板 send-keys Tab 原樣放行。
#   視覺焦點三者全上(the maintainer 拍板):壓暗其他框 + 活躍框邊框強化 + 活躍框底色加深。
#
# 2026-08-14 事故修復(wrong-surface-binding,登錄簿 INC-20260814-status-line-hit):
#   --hit 與其 MouseDown1Status 綁定整段移除。原意=「點面板內視圖標籤列」,
#   卻綁上外層狀態列(語意不對應的表面)且無 if-shell 後備——the maintainer 點
#   「1:war-room」被吞掉原生 select-window、--hit 收到空 pane rc=2,tmux
#   彈錯誤畫面卡死(活艙實證)。標籤列點擊的正確表面早在 E2.13/E2.16 的
#   mouse-ctl MouseUp1Pane 管線,且該路刻意不走 view-nav(respawn 會洗掉
#   眼驗版格式,見 mouse-ctl --click 註)。命中邏輯正本留在 view_switch.py
#   hit(spans 語意測試仍用);shell 端不再有第二個命中入口。
#   回歸守衛=tests/test-view-switch.py:install 產出禁再出現該事件名與 --hit。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
REVERSE=0
PANE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --install) MODE="install" ;;
    --tab) MODE="tab"; case "${2:-}" in --*|"") ;; *) PANE="${2:-}"; shift ;; esac ;;
    --reverse) REVERSE=1 ;;
    --dry-run) DRY=1 ;;
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
    aib_refuse_tmp_install view-nav-ctl.sh "$HERE" || exit $?
  else
    echo "view-nav-ctl.sh: 缺陷十九守衛檔缺席($COCKPIT/install-guard.sh);非 dry 安裝一律 fail-closed 拒裝" >&2
    exit 6
  fi
fi

run() { if [ "$DRY" = "1" ]; then printf '+ %s\n' "$*"; else "$@"; fi; }

# @aib_pane 標記 -> 框名:"view:left" -> "left"。右欄 ai 視圖的正本腳本
# (pane-right-ai.sh,REVIEWED 不動)會自標 "ai-list" 供內容點擊,故收作
# right 的別名——標記不論哪個先落,tab/點標籤都切得動。其餘原樣回
# (查無對應 spans 檔自然 noop,fail-closed)。
strip_marker() {
  case "$1" in
    view:*) printf '%s\n' "${1#view:}" ;;
    ai-list) printf '%s\n' right ;;
    left-tabs) printf '%s\n' left ;;      # 左框開機標記(pane-left-tabs.sh:5)
    right-tasks) printf '%s\n' right ;;   # 右框舊任務視圖標記(09-12 起兩頁都掛 ai-list;留給還沒重畫的舊 pane)
    *) printf '%s\n' "$1" ;;
  esac
}

SPANS="$EZ2_COCKPIT_STATE_DIR/view-spans"   # 實際檔為 view-spans-<pane>.json

# pane 名 -> tmux pane 目標。先按 @aib_pane 標記反查實 pane id(定址紀律:
# id 不憑座標,版面挪格不跟著壞);反查無果退 1:3:1 座標(左=0.0 右=0.2)。
pane_target() {
  local want="$1" pid marker
  while IFS=' ' read -r pid marker; do
    if [ -n "$pid" ] && [ "$(strip_marker "${marker:-}")" = "$want" ]; then
      printf '%s\n' "$pid"
      return 0
    fi
  done < <(tmux list-panes -s -t "$EZ2_TMUX_SESSION" \
             -F '#{pane_id} #{@aib_pane}' 2>/dev/null || true)
  case "$want" in
    left) printf '%s:0.0\n' "$EZ2_TMUX_SESSION" ;;
    right) printf '%s:0.2\n' "$EZ2_TMUX_SESSION" ;;
    *) return 1 ;;
  esac
}

apply_set_view() {
  # set-view -> 狀態已由 view_switch.py 落好;此處 respawn 該 pane 讓 pane-view.sh
  # 依新 view-state 重跑對應視圖腳本並刷新標籤列高亮(不重造切換邏輯)。
  local pane="$1" view="$2" target
  if [ "$DRY" = "1" ]; then
    printf '+ set-view pane=%s view=%s (respawn pane-view.sh)\n' "$pane" "$view"
    return 0
  fi
  target="$(pane_target "$pane" 2>/dev/null || true)"
  [ -n "$target" ] || return 0   # 未知 pane:狀態已落,不強行 respawn
  tmux respawn-pane -k -t "$target" \
    "bash $HERE/pane-view.sh --pane $pane" 2>/dev/null || true
}

case "$MODE" in
  install)
    # 1) Tab 標記放行:標記面板內切視圖,否則原樣送(中欄 AI 對話不受影響)
    run tmux bind-key -n Tab \
      if-shell -F '#{@aib_pane}' \
        "run-shell '$HERE/view-nav-ctl.sh --tab \"#{@aib_pane}\"'" \
        "send-keys Tab"
    # BTab(shift-tab)反向;同樣標記放行
    run tmux bind-key -n BTab \
      if-shell -F '#{@aib_pane}' \
        "run-shell '$HERE/view-nav-ctl.sh --tab \"#{@aib_pane}\" --reverse'" \
        "send-keys BTab"
    # 2) 滑鼠點視圖標籤列:**不在這裡綁**——正確表面是 mouse-ctl 的
    #    MouseUp1Pane 管線(檔頭 2026-08-14 事故修復註);狀態列保持原生
    #    select-window,本腳本禁止再碰任何 *Status 事件。
    # 3) 視覺焦點三者全上(the maintainer 拍板):壓暗其他 + 活躍邊框 + 活躍底色
    run tmux set -g window-style 'fg=colour245,bg=default'                 # 非活躍只壓字色
    run tmux set -g window-active-style 'fg=terminal,bg=default'  # 活躍不填黑底(壁紙艙會變黑框)
    run tmux set -g pane-active-border-style 'fg=#0c1210,bg=#0c1210'   # 框線沉入背景
    run tmux set -g pane-border-style 'fg=#0c1210,bg=#0c1210'
    ;;
  tab)
    RARG=""; [ "$REVERSE" = "1" ] && RARG="--reverse"
    # 當前面板名:綁定傳入的 @aib_pane 標記(如 view:left)去前綴;缺則預設 left
    P="$(strip_marker "${PANE:-left}")"
    ACTION="$("$EZ2_PY3" "$HERE/view_switch.py" tab \
              --spans "${SPANS}-${P}.json" --state-dir "$EZ2_COCKPIT_STATE_DIR" \
              --pane "$P" $RARG)"
    KIND="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["action"])' "$ACTION")"
    if [ "$KIND" = "set-view" ]; then
      V="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["view"])' "$ACTION")"
      apply_set_view "$P" "$V"
    else
      [ "$DRY" = "1" ] && printf '+ noop\n' || true
    fi
    ;;
  *)
    echo "usage: view-nav-ctl.sh --install | --tab [--reverse] [--dry-run]" >&2
    exit 2
    ;;
esac
