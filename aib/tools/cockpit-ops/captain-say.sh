#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 艦長送話器(the maintainer 令 2026-08-08「送出之後不送 enter,強制寫成程式」之制度解)
# 用法: captain-say.sh <視窗名> "<訊息>"
# 行為: 以 literal 模式送文字 -> 補 Enter -> 驗證輸入列殘留 -> 殘留則再補(最多 3 次)
# 驗證原理: Claude Code 未送出的輸入顯示在「❯ 」後;送出後輸入列回到空的「❯」
set -u
W="$1"; shift
MSG="$*"
T="aihub:$W"
SOCK=(-L aihub)

# ── §五-6 廣播防呆(R2-4,a seat 更正標的後重寫)──────────────────────────
#
# 原始教訓寫的是「廣播迴圈無防呆卡死一次」,但全 repo 沒有跨窗廣播迴圈——
# 當年那段在版控外手打,從未進 repo。標的因此改指到**它會被建構出來的零件**:
# 本檔。
#
# ★ 改指理由(a seat 驗出、ROOT 原述不精確):
#   我原本寫「本檔沒有時間上界」,那句把結論說對、機制說錯。
#   `for w in 1..6` 與 `for i in 1..3` 都是**定數迴圈**,迭代次數是有界的。
#   真正的無界在:下面 5 個 tmux 呼叫沒有任何一個包 timeout。
#   **迭代次數有界不等於時間有界**——單一 tmux 呼叫掛住,定數迴圈一樣永遠回不來。
#   任何未來的廣播迴圈只要逐窗呼叫本檔,一個沒回應的窗就會卡死整輪。
#
# 沒有 timeout 二進位時 fail-closed:靜默跑無界等於這個修正沒接上,
# 那是本 repo 記過的「防禦看起來關了洞、實際沒接到執法點」。
TMUX_TIMEOUT="${CAPTAIN_SAY_TMUX_TIMEOUT:-5}"
if command -v timeout >/dev/null 2>&1; then
  TO=(timeout "$TMUX_TIMEOUT")
elif command -v gtimeout >/dev/null 2>&1; then
  TO=(gtimeout "$TMUX_TIMEOUT")
else
  echo "captain-say: 找不到 timeout/gtimeout —— 拒絕在無時間上界下送話" >&2
  echo "  裝 coreutils,或明示 CAPTAIN_SAY_TMUX_TIMEOUT 之外的解法。" >&2
  exit 4
fi

# 逾時視同該次呼叫失敗(timeout 回 124),由既有的重試/退出路徑接手。
tmux_bounded() { "${TO[@]}" tmux "$@"; }

# 選單開著時打字會被吞(P1 教訓 2026-08-08:否決令沒送達 0號)——先等對話框不在選單態
for w in 1 2 3 4 5 6; do
  if ! tmux_bounded "${SOCK[@]}" capture-pane -pt "$T" | tail -18 | grep -q 'Do you want\|Esc to cancel'; then break; fi
  sleep 5
done
tmux_bounded "${SOCK[@]}" send-keys -t "$T" -l -- "$MSG" || exit 1
for i in 1 2 3; do
  sleep 0.5
  tmux_bounded "${SOCK[@]}" send-keys -t "$T" Enter
  sleep 0.7
  # 輸入列若仍帶著我們訊息的開頭=沒送出去,重補
  probe=$(tmux_bounded "${SOCK[@]}" capture-pane -pt "$T" | grep '❯' | tail -1)
  if ! printf '%s' "$probe" | grep -qF -- "${MSG:0:12}"; then
    # 正向驗證:訊息開頭必須出現在畫面內容裡(送出的訊息會顯示在對話區)
    if tmux_bounded "${SOCK[@]}" capture-pane -pt "$T" -S -60 | grep -qF -- "${MSG:0:12}"; then
      exit 0
    fi
    echo "WARN: $T 訊息疑似被吞(畫面查無),請重送" >&2
    exit 3
  fi
done
echo "WARN: $T 輸入列疑似仍殘留,請人工確認" >&2
exit 2
