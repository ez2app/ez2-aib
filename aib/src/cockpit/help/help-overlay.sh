#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 說明浮層(E2.12):彈出快鍵總表。
#
#   help-overlay.sh [--print] [--install] [--dry-run]
#
# 預設:在 tmux 內用 display-popup 彈出;不在 tmux(或沒有 popup)就直接印。
#   --print    只把總表印到 stdout(測試與管線用)
#   --install  印/執行 tmux 綁鍵指令
#   --dry-run  只印將執行的指令,不碰 tmux(AIB_DRY_RUN=1 同義)
#
# 兩個設計決定,理由寫在這裡免得下一棒改回去:
#
# 1. 綁的是 <修飾鍵>+? 與 prefix ?,**不綁 root 表的裸 ?**。
#    root 表的裸 ? 是全域的,中欄跑著真的 AI 對話 —— 在那裡打一個問號是
#    每天都會發生的事,綁下去等於使用者從此打不出問號。這不是保守,是
#    那個鍵本來就不該被搶。
#
# 2. 修飾鍵的**顯示名**隨平台變(macOS=Option、其他=Alt),但 tmux 的
#    **綁定名**永遠是 M-(tmux 只認 M-)。顯示與綁定分開,才不會有人把
#    "Option-?" 寫進 bind-key 然後整組快鍵靜默失效。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE="popup"

for a in "$@"; do
  case "$a" in
    --print) MODE="print" ;;
    --install) MODE="install" ;;
    --dry-run) DRY=1 ;;
    -h|--help) MODE="print" ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install help-overlay.sh "$HERE" || exit $?
  else
    echo "help-overlay.sh: 缺陷十九守衛檔缺席($COCKPIT/install-guard.sh);非 dry 安裝一律 fail-closed 拒裝" >&2
    exit 6
  fi
fi

# 平台自適應:只影響「畫面上寫什麼」,不影響綁定字串
if [ -z "${EZ2_MODIFIER_LABEL:-}" ]; then
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin) EZ2_MODIFIER_LABEL="Option" ;;
    *)      EZ2_MODIFIER_LABEL="Alt" ;;
  esac
fi
export EZ2_MODIFIER_LABEL

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

render() {  # 總表內容(寬度計算與版面走 E2.11 的 render_lib,不另造一套)
  EZ2_HELP_COCKPIT="$COCKPIT" "$EZ2_PY3" - <<'PY'
import os
import sys

cockpit = os.environ["EZ2_HELP_COCKPIT"]
sys.path.insert(0, os.path.join(cockpit, "render"))
sys.path.insert(0, cockpit)
from render_lib import DIM, RESET, fit, pad, visible_width, trunc  # noqa: E402
from i18n_lib import Translator  # noqa: E402

MOD = os.environ.get("EZ2_MODIFIER_LABEL", "Alt")
tr = Translator()
p = {"mod": MOD}

SECTIONS = [
    ("aib.overlay.section.layout", ["pane_focus", "page_switch"]),
    ("aib.overlay.section.panels", ["bottom_tab", "left_tab", "right_tab",
                                    "brain_toggle", "view_depth"]),
    ("aib.overlay.section.hub", ["hub_switch", "send_message", "notify_toggle"]),
    ("aib.overlay.section.help", ["help"]),
]

rows = []
for _, ids in SECTIONS:
    for i in ids:
        rows.append(tr.t("aib.keys.%s.combo" % i, p))
COMBO_W = max(visible_width(r) for r in rows) + 2
WIDTH = 66

lines = [""]
lines.append(" " + tr.t("aib.overlay.title"))
lines.append(" " + DIM + "-" * (WIDTH - 2) + RESET)
for section, ids in SECTIONS:
    lines.append("")
    lines.append(" " + DIM + tr.t(section) + RESET)
    for i in ids:
        combo = tr.t("aib.keys.%s.combo" % i, p)
        desc = tr.t("aib.keys.%s.desc" % i, p)
        lines.append("  " + pad(combo, COMBO_W) + trunc(desc, WIDTH - COMBO_W - 4))
lines.append("")
lines.append(" " + DIM + tr.t("aib.overlay.modifier_note", p) + RESET)
lines.append(" " + DIM + tr.t("aib.overlay.close") + RESET)
lines.append("")

for ln in lines:
    print(fit(ln, WIDTH))
PY
}

case "$MODE" in
  print)
    render
    ;;
  install)
    # tmux 只認 M-;顯示名(Option/Alt)不進綁定字串
    run tmux bind-key -n 'M-?' run-shell "bash $HERE/help-overlay.sh"
    run tmux bind-key '?' run-shell "bash $HERE/help-overlay.sh"
    ;;
  popup)
    if [ "$DRY" = "1" ]; then
      run tmux display-popup -E -w 70 -h 27 "bash $HERE/help-overlay.sh --print; bash -c 'read -rsn1'"
    elif [ -n "${TMUX:-}" ] && tmux display-popup -E true 2>/dev/null; then
      tmux display-popup -E -w 70 -h 27 "bash $HERE/help-overlay.sh --print; bash -c 'read -rsn1'"
    else
      render
    fi
    ;;
esac
