#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E2.4 Alt+m 送信:R-30 前置 lint + 先 dry-run + 人確認才真送。
#
#   ctl-send-message.sh --install [--dry-run]     綁 <修飾鍵>+m
#   ctl-send-message.sh --compose [--dry-run]     開對話框收「收件者/內容」
#   ctl-send-message.sh --preview --to T --body B 只預演(lint+閘門+將執行的指令)
#   ctl-send-message.sh --confirm --to T --body B 真送(前面兩關都過才會走到)
#
# 三個決定,理由寫在這裡:
#
# 1. **先預演再送,而且確認是獨立一步**。訊息送出去收不回來,而 Alt+m 是
#    一個很容易誤觸的鍵。預演把 lint 結果與「將執行的完整指令」攤開,
#    人看過才有 --confirm。沒有「記住我的選擇」這種選項。
#
# 2. **lint 在身份判定之前**。內容不合規就沒有「誰可以送」的問題,
#    而且先擋能少一次身份查詢。
#
# 3. **綁 M-m 不綁裸 m**。跟 E2.12 的裸 ? 同一條理由:root 表是全域的,
#    中欄跑著真的 AI 對話,搶走一個字母鍵等於使用者從此打不出那個字。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/../cockpit" && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
TO=""; BODY=""
# 通道更換(2026-08-14 dm 刀):咽喉 --execute 走 DM 引擎,需要投遞窗。
# --tmux-window 顯式給或 EZ2_SEND_TMUX_WINDOW 環境;缺=confirm 誠實 rc2
# (咽喉訊息已明講),目標解析歸 aib dm/dm-ctl,本工具不猜窗。
TW="${EZ2_SEND_TMUX_WINDOW:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --install) MODE="install" ;;
    --compose) MODE="compose" ;;
    --preview) MODE="preview" ;;
    --confirm) MODE="confirm" ;;
    --to) TO="${2:-}"; shift ;;
    --body) BODY="${2:-}"; shift ;;
    --tmux-window) TW="${2:-}"; shift ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

t() {  # 人話字串一律走 i18n
  "$EZ2_PY3" "$COCKPIT/i18n_lib.py" "$1"
}

sender_arg() {
  # 送訊者身份:優先用本 session 的 registry uuid;沒有就交由血緣律 fail-closed
  if [ -n "${EZ2_SESSION_UUID:-}" ]; then
    printf -- '--sender-uuid\n%s\n' "$EZ2_SESSION_UUID"
  elif [ -n "${EZ2_SENDER_JSON:-}" ]; then
    printf -- '--sender-json\n%s\n' "$EZ2_SENDER_JSON"
  fi
}

case "$MODE" in
  install)
    # tmux 只認 M-;顯示名(Option/Alt)是 E2.12 的事,不進綁定字串
    run tmux bind-key -n M-m run-shell "bash $HERE/ctl-send-message.sh --compose"
    ;;

  compose)
    # 兩段式對話框:收件者 -> 內容 -> 進預演。command-prompt 的值原樣帶進下一關。
    run tmux command-prompt -p "$(t aib.send.compose_prompt)" \
      "command-prompt -p '$(t aib.send.body_prompt)' \
       'run-shell \"bash $HERE/ctl-send-message.sh --preview --to %1 --body %%\"'"
    ;;

  preview)
    [ -n "$TO" ] || { echo "--preview 需要 --to" >&2; exit 2; }
    echo "$(t aib.send.dry_run_first)"
    echo "→ $TO"
    echo "---"
    printf '%s\n' "$BODY"
    echo "---"
    # lint 與閘門都在 aib-send.sh 裡跑;它預設就是 dry-run,不會真送
    set +e
    IFS=$'\n' read -r -d '' -a SARG < <(sender_arg && printf '\0')
    bash "$HERE/aib-send.sh" --verb dm --to "$TO" --body "$BODY" \
         ${SARG[@]+"${SARG[@]}"}
    RC=$?
    set -e
    if [ "$RC" = "0" ]; then
      echo "確認送出:ctl-send-message.sh --confirm --to $TO --body <同一段內容>"
    fi
    exit "$RC"
    ;;

  confirm)
    [ -n "$TO" ] || { echo "--confirm 需要 --to" >&2; exit 2; }
    set +e
    IFS=$'\n' read -r -d '' -a SARG < <(sender_arg && printf '\0')
    if [ "$DRY" = "1" ]; then
      bash "$HERE/aib-send.sh" --verb dm --to "$TO" --body "$BODY" \
           ${SARG[@]+"${SARG[@]}"}
    else
      bash "$HERE/aib-send.sh" --verb dm --to "$TO" --body "$BODY" \
           ${SARG[@]+"${SARG[@]}"} ${TW:+--tmux-window "$TW"} --execute
    fi
    RC=$?
    set -e
    exit "$RC"
    ;;

  *)
    echo "usage: ctl-send-message.sh --install|--compose|--preview|--confirm [--to T] [--body B] [--dry-run]" >&2
    exit 2
    ;;
esac
