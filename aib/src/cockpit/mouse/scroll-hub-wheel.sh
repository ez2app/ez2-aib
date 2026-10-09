#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 中欄滾輪。目標是內層 attach client 正在看的那一格。
# Grok/Claude:一筆手勢只送一次 PageUp/PageDown,不把滾輪序列打進輸入框。
# 沒開滑鼠、畫在主螢幕(Codex):copy-mode 捲幾行,按 Esc 可回輸入。
# 觸控板一次很多格,收成一筆再動。不寫紀錄檔,不寫 pane tty,不 send-keys -M。
# ★ 09-28 the maintainer:「快速向下拉會跳 returned 1」「要平滑一次一行,一次多行會看不到內容」:
#   - 本檔永遠 exit 0(滾輪不該在畫面上報錯);真的有指令失敗就把行號與指令記進 $burst/errors.log(只留最後 50 行)。
#   - 鎖目錄被中斷的行程留下(舊版之後每一格滾輪都被吞掉):鎖裡記 pid,pid 不在了就收回。
#   - 內容在 tmux 歷史裡的畫面(Codex、一般殼、有開滑鼠但沒用全螢幕的程式):每一格 = 1 行(AIB_HUB_WHEEL_LINES),
#     一次收到多格就一行一行地捲(每行間隔 AIB_HUB_WHEEL_STEP_MS 毫秒),不再一次跳 3 行。
#   - 全螢幕程式(alternate screen):tmux 沒有它的歷史。自己收滑鼠(mouse_any+SGR)的 ⇒ 一格送一個滾輪事件(09-30,一格一行);
#     沒收滑鼠的 ⇒ 照舊 PageUp/PageDown(不送滑鼠序列,見 test-mouse)。
dir="${1:-}"
sess="${2:-}"
# ★ review r6 m1:第 3 格起認 role=… / pane=…(mouse-ctl 的綁定這樣傳:值是空的也佔一格,不會往前遞補);
#   沒帶 key= 的照舊按位置讀(第一個是 role、第二個是 pane)—— 舊綁定與既有呼叫者不用改。
role=""
tag=""
_npos=0
for _a in "${@:3}"; do
  case "$_a" in
    role=*) role="${_a#role=}" ;;
    pane=*) tag="${_a#pane=}" ;;
    *)
      _npos=$((_npos + 1))
      if [ "$_npos" -eq 1 ]; then role="$_a"; elif [ "$_npos" -eq 2 ]; then tag="$_a"; fi
      ;;
  esac
done
case "$dir" in
  up|down|drain) ;;      # drain = 自己收尾時再收一次排隊中的格(不再追加一格)
  *) exit 0 ;;
esac
sess="${sess#\'}"; sess="${sess%\'}"
sess="${sess#\"}"; sess="${sess%\"}"
role="${role#\'}"; role="${role%\'}"
tag="${tag#\'}"; tag="${tag%\'}"
[ -n "$sess" ] || exit 0
trap 'exit 0' EXIT
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ "$tag" = "hub-frame" ]; then
  bash "$HERE/mouse-ctl.sh" --wheel "$dir" "$sess" >/dev/null 2>&1 || true
  exit 0
fi
if [ "$tag" = "pending-review" ]; then
  bash "$HERE/../pane-pending-review.sh" --key "$dir" >/dev/null 2>&1 || true
  exit 0
fi
if [ "$role" != "hub" ]; then
  exit 0
fi
inst="${sess#aib-}"
# 10-08 the maintainer:回報彈出畫面(aib-reply 的 popup,開在內層 hub)開著時,滾輪要捲它、不捲底下的對話。
#   外層這支從不把滑鼠轉給內層(見檔頭),popup 收不到滾輪 ⇒ 改送 ↑/↓ 鍵給中欄:內層 tmux 先把按鍵交給 popup,
#   reply_viewer 收到方向鍵就捲、並取消倒數。記號由 reply_viewer 開著時寫(內容是它的 pid),死掉的 pid 不算。
_viewer_mark="${AIB_REPLY_VIEWER_DIR:-$HOME/.ez2-aib/state/aib-report}/viewer-open-aihub-$inst"
if [ "$dir" != "drain" ] && [ -f "$_viewer_mark" ]; then
  _vpid="$(head -1 "$_viewer_mark" 2>/dev/null)"
  if [ -n "$_vpid" ] && kill -0 "$_vpid" 2>/dev/null; then
    _hub_pane="$(tmux list-panes -s -t "$sess" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub"{print $1; exit}')"
    if [ -n "$_hub_pane" ]; then
      case "$dir" in up) _k=Up ;; *) _k=Down ;; esac
      tmux send-keys -t "$_hub_pane" "$_k" 2>/dev/null || true
    fi
    exit 0
  fi
fi
burst="${AIB_HUB_WHEEL_BURST_DIR:-/tmp/aib-hub-burst-$inst}"
mkdir -p "$burst" 2>/dev/null || exit 0
_errlog="$burst/errors.log"
trap '_rc=$?; printf "%s rc=%s line=%s cmd=%s\n" "$(date +%m-%d_%H:%M:%S)" "$_rc" "$LINENO" "$BASH_COMMAND" >> "$_errlog" 2>/dev/null' ERR
if [ "$dir" != "drain" ]; then
  printf '%s\n' "$dir" >> "$burst/q" 2>/dev/null || exit 0
fi
# 鎖 = 指向自己 pid 的 symlink(建立與寫 pid 是同一個原子動作,沒有「有鎖沒 pid」的窗口)。
rm -rf "$burst/lock" 2>/dev/null || true   # 只清舊版留下的目錄鎖;symlink 不是目錄,-d 判斷在下面
if ! ln -s "$$" "$burst/lk" 2>/dev/null; then
  _holder="$(readlink "$burst/lk" 2>/dev/null || true)"
  if [ -z "$_holder" ]; then
    ln -s "$$" "$burst/lk" 2>/dev/null || exit 0     # 對方剛好放掉:再搶一次,搶不到就交給搶到的
  elif kill -0 "$_holder" 2>/dev/null; then
    exit 0                     # 有人正在收這一批,我這一格已經排進 q,它收尾時會再收一次
  else
    # 鎖是死掉的行程留下的:只收回「還是指向那個死 pid」的鎖(先改名再判,不會刪到別人剛建的)
    mv -f "$burst/lk" "$burst/lk.stale.$$" 2>/dev/null || exit 0
    if [ "$(readlink "$burst/lk.stale.$$" 2>/dev/null)" != "$_holder" ]; then
      mv -f "$burst/lk.stale.$$" "$burst/lk" 2>/dev/null || true   # 拿錯了(別人剛建的):放回去,讓它處理
      exit 0
    fi
    rm -f "$burst/lk.stale.$$" 2>/dev/null || true
    ln -s "$$" "$burst/lk" 2>/dev/null || exit 0
  fi
fi
ms="${AIB_HUB_WHEEL_BURST_MS:-80}"
[ "$dir" = "drain" ] && ms=0
if [ "$ms" != "0" ]; then
  sleep "0$(printf '.%03d' "$ms" 2>/dev/null || printf '.08')"
fi
# 先把 q 改名拿走再數:數的時候別人追加的格會進新的 q,不會被清掉(舊版數完再清空,中間進來的格會不見)
take="$burst/q.take.$$"
mv -f "$burst/q" "$take" 2>/dev/null || true
ups=$(grep -c '^up$' "$take" 2>/dev/null || true)
downs=$(grep -c '^down$' "$take" 2>/dev/null || true)
rm -f "$take" 2>/dev/null || true
rm -f "$burst/lk" 2>/dev/null || true
if [ -f "$_errlog" ]; then
  tail -n 50 "$_errlog" > "$_errlog.t" 2>/dev/null && mv -f "$_errlog.t" "$_errlog" 2>/dev/null || true
fi
ups=${ups:-0}
downs=${downs:-0}
if [ "$ups" -gt "$downs" ]; then
  dir=up; notches=$((ups - downs))
elif [ "$downs" -gt "$ups" ]; then
  dir=down; notches=$((downs - ups))
else
  [ -s "$burst/q" ] && exec bash "$0" drain "$sess" "$role" "$tag"
  exit 0
fi
per="${AIB_HUB_WHEEL_LINES:-1}"
case "$per" in ''|*[!0-9]*) per=1 ;; esac
lines=$((notches * per))
[ "$lines" -gt 200 ] && lines=200
step_ms="${AIB_HUB_WHEEL_STEP_MS:-12}"
case "$step_ms" in ''|*[!0-9]*) step_ms=12 ;; esac
# 我拿著鎖的時候別人排進來的格:收尾後再收一次,不然要等下一次滾輪才會動(一次快拉會少捲幾行)
drain_rest() {
  if [ -s "$burst/q" ]; then
    exec bash "$0" drain "$sess" "$role" "$tag"
  fi
  exit 0
}
# 一行一行捲:多格時每行之間停一下,看起來是滑過去而不是跳過去
scroll_lines() {   # $1=scroll-up|scroll-down
  local i=0
  while [ "$i" -lt "$lines" ]; do
    env -u TMUX tmux -L "$hub" send-keys -X -t "$pane" "$1" >/dev/null 2>&1 || return 0
    i=$((i + 1))
    if [ "$i" -lt "$lines" ] && [ "$step_ms" != "0" ]; then
      sleep "0$(printf '.%03d' "$step_ms")" 2>/dev/null || true
    fi
  done
}
hub="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_hub_socket 2>/dev/null || true)"
[ -n "$hub" ] || exit 0
pane="$(env -u TMUX tmux -L "$hub" list-clients -F '#{pane_id}' 2>/dev/null | awk 'NF{print; exit}')"
[ -n "$pane" ] || pane="$(env -u TMUX tmux -L "$hub" display-message -p -t aihub: '#{pane_id}' 2>/dev/null || true)"
[ -n "$pane" ] || exit 0
info="$(env -u TMUX tmux -L "$hub" display-message -p -t "$pane" '#{pane_in_mode} #{mouse_any_flag} #{alternate_on} #{mouse_sgr_flag} #{pane_width} #{pane_height}' 2>/dev/null || true)"
read -r in_mode mouse_flag alt_flag sgr_flag pw ph _ <<<"$info"
case "$pw" in ''|*[!0-9]*) pw=80 ;; esac
case "$ph" in ''|*[!0-9]*) ph=24 ;; esac
# 全螢幕又自己收滑鼠的程式(Claude Code 2.1 全螢幕、Codex):一格送一個滾輪事件給它,它自己捲一行。
#   ★ the maintainer 09-30 16:2x「我要所有的滾動都一行一行很絲滑的」。09-30 測試艙 b1af 實量:Claude Code 2.1.285
#     收一個 SGR 滾輪事件 = 捲 1 行,不進 copy-mode、輸入框不出字。
#   ★ 只在 mouse_any 與 SGR 兩旗都開時送:沒開滑鼠的程式收到這串會當成打字(舊「黃字 syntax error」那一族),
#     那種照舊 PageUp。事件座標落在格子正中(1 起算)。
wheel_events() {   # $1=64(上)|65(下)
  local i=0 x=$(( pw / 2 + 1 )) y=$(( ph / 2 + 1 ))
  while [ "$i" -lt "$lines" ]; do
    env -u TMUX tmux -L "$hub" send-keys -t "$pane" -l "$(printf '\033[<%s;%s;%sM' "$1" "$x" "$y")" >/dev/null 2>&1 || return 0
    i=$((i + 1))
    if [ "$i" -lt "$lines" ] && [ "$step_ms" != "0" ]; then
      sleep "0$(printf '.%03d' "$step_ms")" 2>/dev/null || true
    fi
  done
}
if [ "$in_mode" = "1" ]; then
  if [ "$dir" = "up" ]; then scroll_lines scroll-up; else scroll_lines scroll-down; fi
  drain_rest
fi
if [ "$alt_flag" = "1" ] && [ "$mouse_flag" = "1" ] && [ "$sgr_flag" = "1" ]; then
  if [ "$dir" = "up" ]; then wheel_events 64; else wheel_events 65; fi
  drain_rest
fi
if [ "$alt_flag" = "1" ]; then
  if [ "$dir" = "up" ]; then
    env -u TMUX tmux -L "$hub" send-keys -t "$pane" PageUp >/dev/null 2>&1 || true
    env -u TMUX tmux display-message -d 2500 -t "$sess" '已暫停跟著最新，滾回最底會再跟上' >/dev/null 2>&1 || true
  else
    env -u TMUX tmux -L "$hub" send-keys -t "$pane" PageDown >/dev/null 2>&1 || true
  fi
  drain_rest
fi
if [ "$dir" = "up" ]; then
  env -u TMUX tmux -L "$hub" copy-mode -e -t "$pane" >/dev/null 2>&1 || exit 0
  scroll_lines scroll-up
  env -u TMUX tmux display-message -d 2000 -t "$sess" '捲動紀錄中，按 Esc 回到輸入' >/dev/null 2>&1 || true
fi
drain_rest
