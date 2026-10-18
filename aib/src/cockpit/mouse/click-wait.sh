#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 右欄重繪的空窗裡點到的那一下(10-18 Andy:「點了不會切換,要點兩下」)。
#
# pane-right-ai.sh 每幀「撤 marker → 畫 → 發布 rowmap → 掛回 marker」,畫面與座標表不會錯配;
# 代價是 @aib_pane 會空一小段(實測 9–15% 的時間、最長 1.25 秒),這時 MouseUp1Pane 原本落到 else 分支 = 這一下白點。
# 這裡不改發布時序,改在點擊端等:marker 掛回來(最多 1.5 秒)⇒ 照**新的** rowmap 交給 mouse-ctl --click。
# 等不到就放掉這一下(寧可不動作,也不要照舊座標點到別列)。
#
#   click-wait.sh <pane_id> <x> <y> <session>
set -u
pane="${1:-}" x="${2:-}" y="${3:-}" sess="${4:-}"
[ -n "$pane" ] || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
tries="${AIB_CLICK_WAIT_TRIES:-30}"        # 30 × 0.05 秒 = 1.5 秒
i=0
while [ "$i" -lt "$tries" ]; do
  m="$(tmux show-options -pqv -t "$pane" @aib_pane 2>/dev/null || true)"
  case "$m" in
    ai-list|right-tasks)                   # 右欄只會掛這兩種;其他值(或別格)不接
      exec bash "$here/mouse-ctl.sh" --click "$m" "$x" "$y" "$sess" --role right ;;
  esac
  sleep 0.05
  i=$((i + 1))
done
echo "click-wait: $pane 等 1.5 秒 marker 沒掛回,這一下不處理" >&2
exit 0
