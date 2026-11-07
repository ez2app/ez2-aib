#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# pane-vis-bump.sh <序號檔>:tmux 可見性事件鉤子呼叫(見 pane-tick.sh「喚醒事件序號」契約)。
# 序號 +1:mkdir 上鎖 → 讀 → 寫暫存 → rename(原子)。讀的一方只會看到完整、單調遞增的數字。
# 什麼都不印、永遠 rc0(run-shell 有輸出或 rc≠0 會把當前格切進 view-mode)。
f="$1"
[ -n "$f" ] || exit 0
lk="$f.lk"
i=0
while ! mkdir "$lk" 2>/dev/null; do
  i=$((i + 1))
  # 鎖被前一個當掉的鉤子留著:等約 1 秒後接手(序號仍會變,等待方照樣醒)
  if [ "$i" -gt 50 ]; then rm -rf "$lk" 2>/dev/null; mkdir "$lk" 2>/dev/null; break; fi
  sleep 0.02 2>/dev/null || :
done
n=0
{ read -r n < "$f"; } 2>/dev/null || n=0
case "$n" in ''|*[!0-9]*) n=0 ;; esac
t="$f.$$"
if printf '%s\n' "$((n + 1))" > "$t" 2>/dev/null; then
  mv -f "$t" "$f" 2>/dev/null || rm -f "$t" 2>/dev/null
fi
rmdir "$lk" 2>/dev/null
exit 0
