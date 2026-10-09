#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 框內視圖宿主(E2.16 第二階段):在 pane border status 掛「視圖標籤列」+ 標
# @aib_pane + 依 view-state 執行對應視圖腳本。不改三個 REVIEWED 左框腳本內容,
# 用組合(wrap)而非修改——標籤列在 border 線上,不與子腳本的 clear 迴圈打架。
#
#   pane-view.sh --pane left [--host]        宿主模式:設 border-status、exec 視圖
#   pane-view.sh --pane left --render         只重算標籤列與 spans(切換後刷新用)
#
# 視圖註冊表(pane -> 有序視圖):標籤列順序=tab 循環順序=點擊命中順序,同源。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

PANE=""
MODE="host"
while [ $# -gt 0 ]; do
  case "$1" in
    --pane) PANE="${2:-}"; shift ;;
    --host) MODE="host" ;;
    --render) MODE="render" ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$PANE" ] || { echo "--pane required" >&2; exit 2; }

# 視圖註冊表:view|label|script(script 相對 cockpit 目錄;中欄不在此表)
views_for() {
  case "$1" in
    left)
      printf '%s\n' \
        'persona|人格|pane-left-persona.sh' \
        'brain|專案樹|pane-left-brain.sh' \
        'fleet|艦隊|pane-left-fleet.sh' ;;
    *) return 1 ;;
  esac
}

# 組 --views JSON(給 view_switch.py bar)+ 取當前視圖的 script
build_views_json() {
  "$EZ2_PY3" - "$@" <<'PY'
import json, sys
views = []
for line in sys.argv[1:]:
    v, label, _script = line.split("|", 2)
    views.append({"view": v, "label": label})
print(json.dumps(views, ensure_ascii=False))
PY
}

script_for_view() {
  local want="$1"; shift
  local line v label script
  for line in "$@"; do
    IFS='|' read -r v label script <<<"$line"
    [ "$v" = "$want" ] && { printf '%s\n' "$script"; return 0; }
  done
  return 1
}

# process substitution 的退出碼不傳給讀取端,故以陣列長度判未註冊框。
# ED.9(08-13):mapfile 是 bash 4.0+ 內建,macOS /bin/bash 3.2 無(CI macOS 格
# 實紅 command not found);改 while-read 累加,3.2 起可用,語意等價。
REG=()
while IFS= read -r _v; do REG+=("$_v"); done < <(views_for "$PANE" || true)
[ "${#REG[@]}" -gt 0 ] || { echo "unknown pane: $PANE" >&2; exit 2; }
VIEWS_JSON="$(build_views_json "${REG[@]}")"

# 渲染標籤列 + 落 spans(渲染即命中同源);回傳 bar 字串
BAR="$("$EZ2_PY3" "$HERE/view_switch.py" bar --pane "$PANE" \
        --state-dir "$EZ2_COCKPIT_STATE_DIR" --views "$VIEWS_JSON")"

# 當前視圖(view_switch.py bar 已確保 state 有值)
CUR="$("$EZ2_PY3" "$HERE/view_switch.py" peek --pane "$PANE" \
        --state-dir "$EZ2_COCKPIT_STATE_DIR" \
        | "$EZ2_PY3" -c 'import json,sys;print(json.load(sys.stdin)["view"] or "")')"
SCRIPT="$(script_for_view "$CUR" "${REG[@]}" || true)"
[ -n "$SCRIPT" ] || SCRIPT="$(printf '%s' "${REG[0]}" | cut -d'|' -f3)"

if [ -n "${TMUX:-}" ]; then
  # 標記本 pane 可接管(tab/滑鼠放行判定源)、把標籤列掛上 border status 頂線
  tmux set-option -p -t "$TMUX_PANE" @aib_pane "view:$PANE" 2>/dev/null || true
  tmux set -p pane-border-status top 2>/dev/null || true
  tmux set -p pane-border-format " $BAR " 2>/dev/null || true
fi

if [ "$MODE" = "render" ]; then
  printf '%s\n' "$BAR"
  exit 0
fi

# host:交棒給當前視圖腳本(不改其內容,只是選它來跑)
exec bash "$COCKPIT/$SCRIPT"
