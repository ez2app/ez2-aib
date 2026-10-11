#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Right content tabs, as eye-verified: 1.AI / 2.AI用量.
# the maintainer 09-12 15:3x 令:第 2 頁由「2.任務」改「2.AI用量」,用量塊從 1.AI 頁尾搬到第 2 頁置頂,
# 舊的沙盒任務段(pane-right-tasks.sh)收掉、不再被本檔呼叫。
. "$(dirname "$0")/cockpit-env.sh"
# 同一 state 只准一條右欄繪製迴圈。殘留的 tabs.sh（舊艙／重掛沒死乾淨）
# 會跟活艙搶著清／掛 @aib_pane，用量頁就會突然整頁點不到。
# macOS 的 flock 在 Homebrew，不在 /usr/bin。右欄測試把 PATH 收成系統目錄，
# tmux 也常常沒有 brew。裸叫 flock 得到 127 時，下面會把它說成「鎖被占用」
# 然後退出，右欄一幀都不畫。跟 EZ2_PY3 一樣先找絕對路徑。
# /opt/homebrew/opt/flock/bin 要排在 /opt/homebrew/bin 前面：後者有時指到
# 不吃「flock -n <fd>」的那一版。
# 找到了才畫。執行不了（126/127）或鎖被占用，都不得無鎖開畫：
# 兩條繪製迴圈會搶 @aib_pane，用量頁整頁點不到。訊號中斷是 128 以上，同樣不畫。
if [ -z "${EZ2_FLOCK:-}" ]; then
  for _flock_bin in \
      /opt/homebrew/opt/flock/bin/flock \
      /opt/homebrew/bin/flock \
      /usr/local/bin/flock \
      /usr/bin/flock
  do
    if [ -x "$_flock_bin" ]; then
      EZ2_FLOCK="$_flock_bin"
      break
    fi
  done
  unset _flock_bin
fi
EZ2_FLOCK="${EZ2_FLOCK:-flock}"
_LOCKF="${EZ2_COCKPIT_STATE_DIR}/right-tabs.lock"
mkdir -p "${EZ2_COCKPIT_STATE_DIR}"
exec 9>"$_LOCKF"
_flock_rc=0
"$EZ2_FLOCK" -n 9 || _flock_rc=$?
if [ "$_flock_rc" -ne 0 ]; then
  if [ "$_flock_rc" -eq 126 ] || [ "$_flock_rc" -eq 127 ]; then
    printf 'pane-right-tabs: flock not runnable (%s, rc=%s); skip paint\n' \
      "$EZ2_FLOCK" "$_flock_rc" >&2
  else
    printf 'pane-right-tabs: lock busy (%s, rc=%s); skip orphan painter\n' \
      "$_LOCKF" "$_flock_rc" >&2
  fi
  exit 0
fi
TABF="$EZ2_COCKPIT_STATE_DIR/right-tab"
# 09-14 踢右欄(the maintainer「開席要最快」):aib 寫完席位檔就把一個 nonce 寫進這個檔,
# 本檔的 tick 迴圈每 0.1 秒看它,變了就提前中斷、立刻重畫這一幀 —— 不等 4 秒閒置輪詢。
# 與頁簽檔同一招(pane_read_file + 比對),不另起機制、不用訊號。
KICKF="$EZ2_COCKPIT_STATE_DIR/right-kick"
# 滾輪只會把方向寫進這個目錄。目錄要先在，否則那一行寫不進去。
mkdir -p "$EZ2_COCKPIT_STATE_DIR/.scroll-wheel.q" 2>/dev/null || true
# 右欄不是輸入區。鍵盤若停在這裡，字會印在捲軸上。
stty -echo 2>/dev/null || true
[ "$(uname -s)" = Darwin ] && MOD="Option" || MOD="Alt"
MOD="${EZ2_MODIFIER_LABEL:-$MOD}"
export EZ2_MODIFIER_LABEL="$MOD"

# 頁簽字從 i18n 取(UI 紅線:四語),與底部頁簽 pane-bottom-tabs.sh 同一條路。開檔時取一次:
# 字典不會在一次 session 內變,每幀查一次是白燒。★ 取不到不退中文預設 —— 印鍵名本身
# (i18n_lib 對缺鍵回 !!key!!,python 起不來則下面自己印鍵名),讓壞掉的樣子跟正確的不同。
IFS=$'\t' read -r TAB_AI TAB_USAGE TAB_LEFT <<EOF
$("$EZ2_PY3" - <<'PYI18N' 2>/dev/null
import json, os, sys
sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
sys.path.insert(0, os.path.join(os.environ["EZ2_COCKPIT_DIR"], "render"))
from i18n_lib import Translator
tr = Translator()
ai, us, lf = tr.t("aib.tabs.right_ai"), tr.t("aib.tabs.right_usage"), tr.t("aib.tabs.right_leftover")
# 09-30 第 3 頁「離艙」:分頁列多一格,點哪一格要看 x。三格的起訖欄寫成檔給 mouse-ctl 讀
# (字依語言而不同寬,不在滑鼠那邊寫死)。三種作用中的分頁列,三格的位置相同(見下方三種 printf)。
try:
    from render_lib import visible_width as vw
    a1 = 1 + vw(ai) + 2                  # " " + " 1.AI "
    l0 = a1 + 1 + vw(us) + 1             # + " " + "2.AI用量" + " "(第 3 格從這一欄起)
    d = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    if d:
        tmp = os.path.join(d, "right-tab-spans.json.%d" % os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ai": [0, a1], "usage": [a1, l0], "left": [l0, l0 + vw(lf) + 3]}, fh)
        os.replace(tmp, os.path.join(d, "right-tab-spans.json"))
except Exception:
    pass
print(ai + "\t" + us + "\t" + lf)
PYI18N
)
EOF
[ -n "${TAB_AI:-}" ] || TAB_AI="aib.tabs.right_ai"
[ -n "${TAB_USAGE:-}" ] || TAB_USAGE="aib.tabs.right_usage"
[ -n "${TAB_LEFT:-}" ] || TAB_LEFT="aib.tabs.right_leftover"

render_frame() {
  local tab="$1"
  if [ "$tab" = "3" ]; then
    # 第 3 頁「離艙」(the maintainer 09-30):已不在任何活艙、但還沒收乾淨的席位,每席右邊 <X>。
    # 資料在背景盤點(leftover_seats.py produce),這一幀只讀快取;點擊表(ai-rowmap.json)由它順手寫。
    [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
    printf ' \033[K\n'; printf '  \033[38;5;245m%s  %s\033[0m \033[1;7m %s \033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
    "$EZ2_PY3" "$EZ2_COCKPIT_DIR/leftover_seats.py" render --width "${EZ2_PANE_W:-44}" --height "${EZ2_PANE_H:-30}" --row-offset 2 2>/dev/null \
      | sed 's/$/\x1b[K/'
  elif [ "$tab" = "1" ]; then
    # 第 2 頁「AI用量」:同一支採集端(pane-right-ai.sh)換一頁畫(EZ2_RIGHT_VIEW=usage),
    # 它自己會掛 @aib_pane ai-list 與發布 rowmap,點 LOGO/[重驗] 的路由與 1.AI 時代同一條。
    # the maintainer 09-04 令:拿掉「Option+Shift+E 切換」那一行。表頭因此由 3 行變 2 行,
    # 下面的 offset 跟著改 —— ★ 兩處要一起改,漏一處就是點擊錯位。
    # 分頁列只佔一列。前面再送一列清行，終端不會把它算成可見列，
    # 點擊會整頁往下錯一列：點在「用量 | Codex」上其實點到空行。
    # ★ the maintainer 09-30:分頁列上方空一列。那一列印一個空格(看得見的列,不是只有清行碼),
    #   分頁列在 y=1、內容從 y=2 起,本檔每一處 offset 都 +1(滑鼠 y<=1 仍是切分頁)。
    printf ' \033[K\n'; printf '  \033[38;5;245m%s\033[0m \033[1;7m %s \033[0m \033[38;5;245m%s\033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
    EZ2_ONESHOT=1 EZ2_RIGHT_VIEW=usage EZ2_RIGHT_PAGE=usage \
      EZ2_H_OFFSET=2 EZ2_ROW_OFFSET=2 bash "$EZ2_COCKPIT_DIR/pane-right-ai.sh"
    # 用量頁 paint 失敗會撤 @aib_pane；這裡一律釘回，否則整頁按鍵假死
    [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
  else
    [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
    printf ' \033[K\n'; printf ' \033[1;7m %s \033[0m \033[38;5;245m%s  %s\033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
    # 機器狀態列(the maintainer 09-04 令「接」)。三件寫在這裡,因為它們是這一格的全部風險:
    #   ★ 行數**動態**:字樣長度會隨「量到/量不到」變,寫死 offset 會讓點擊錯位一整天而沒有人會紅。
    #     所以先取行數,再把 EZ2_H_OFFSET / EZ2_ROW_OFFSET 一起 +N —— 兩個都要,漏一個就是錯位。
    #   ★ 取值**不在這一層**:machine_status_row.py 只讀快取(毫秒),過期時背景更新,
    #     不擋畫面。直接叫 machine_status 是 0.72s/輪 = 每輪多燒 14%(v0.53 BF 剛壓下去的那件事)。
    #   ★ 取不到值印「量測中」不印空白:空白與「這一格壞了」在畫面上逐字相同。
    _mach_rows=""
    if [ -x "$EZ2_COCKPIT_DIR/machine_status_row.py" ] || [ -f "$EZ2_COCKPIT_DIR/machine_status_row.py" ]; then
      _mach_rows="$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/machine_status_row.py" --width "${EZ2_PANE_W:-44}" 2>/dev/null || true)"
    fi
    _mach_n=0
    if [ -n "$_mach_rows" ]; then
      _mach_n=$(printf '%s\n' "$_mach_rows" | wc -l | tr -d ' ')
      printf '%s\033[K\n' "$_mach_rows" | sed 's/$/\x1b[K/'
    fi
    _off=$((2 + _mach_n))   # 2 = 上方空一列(the maintainer 09-30)+ 分頁列。不要再加那列看不見的清行。
    EZ2_ONESHOT=1 EZ2_H_OFFSET="$_off" EZ2_ROW_OFFSET="$_off" EZ2_RIGHT_PAGE=ai \
      env -u EZ2_RIGHT_VIEW bash "$EZ2_COCKPIT_DIR/pane-right-ai.sh"
  fi
}

# ★ 2026-09-01:徽章三態的「即時 TTY」必須在**這一層**判,不能在下游判。
#   本檔的 fd 1 是 pane 的終端(實測 lsof:1u CHR /dev/ttysNNN),
#   但下面 `FRAME="$(render_frame ...)"` 把整個渲染包進命令替換
#   ⇒ pane-right-ai.sh 以及它再往下的每一層,fd 1 都是管線,`[ -t 1 ]` 恆為假。
#   前一顆 560eac1d 只修掉 pane-right-ai.sh 自己那層的 `$( )`,**沒有修好**:
#   真右欄擷取出來仍是純字母。判斷點必須提到命令替換之前的這一層。
#   ★ 這是同一個缺陷的第二層:「把探測搬到正確的那一層」與「探測寫法正確」是兩件事,
#     而兩者都對了畫面才會變。第一次只做了後者,而測不出差別——因為臂也在下游。
if [ -t 1 ]; then EZ2_AIB_BADGE_TTY=1; else EZ2_AIB_BADGE_TTY=0; fi
export EZ2_AIB_BADGE_TTY

FIRST_PAINT=1

# 滾輪只留下方向。這裡加總一次。沒拿到鎖就讓正在加的那一支做完。
_drain_scroll_nudge() {
  local q f found=0 lock wpid
  q="$EZ2_COCKPIT_STATE_DIR/.scroll-wheel.q"
  [ -d "$q" ] || return 0
  for f in "$q"/e-*; do
    [ -f "$f" ] || continue
    found=1
    break
  done
  [ "$found" = 1 ] || return 0
  lock="$EZ2_COCKPIT_STATE_DIR/.scroll-wheel.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    # 同 mouse-ctl.sh:拿鎖的人死了、或鎖超過 1 分鐘(停電/重開機留下的)⇒ 接手;否則讓它做完。
    wpid=""
    [ -f "$lock/pid" ] && IFS= read -r wpid < "$lock/pid" || true
    if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null && [ -n "$(find "$lock" -maxdepth 0 -mmin -1 2>/dev/null)" ]; then
      return 0
    fi
    rm -rf "$lock" 2>/dev/null || return 0
    mkdir "$lock" 2>/dev/null || return 0
  fi
  printf '%s\n' "$$" > "$lock/pid" || true
  "$EZ2_PY3" "$EZ2_COCKPIT_DIR/mouse/scroll_nudge.py" "$EZ2_COCKPIT_STATE_DIR" >/dev/null 2>&1 || true
  rm -rf "$lock" 2>/dev/null || true
}

# 拖捲軸時每格只寫下游標的列。這裡每一拍套用最後那一列。
_apply_scroll_drag() {
  local y
  [ -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab" ] || return 0
  [ -f "$EZ2_COCKPIT_STATE_DIR/.scroll-drag.y" ] || return 0
  IFS= read -r y < "$EZ2_COCKPIT_STATE_DIR/.scroll-drag.y" || return 0
  case "$y" in
    ''|*[!0-9]*) return 0 ;;
  esac
  [ "$y" = "${_drag_y_seen:-}" ] && return 0
  _drag_y_seen="$y"
  "$EZ2_PY3" -c 'import sys
sys.path.insert(0, sys.argv[1])
import right_scroll_drag
right_scroll_drag.apply_y(sys.argv[2], int(sys.argv[3]))' \
    "$EZ2_COCKPIT_DIR/mouse" "$EZ2_COCKPIT_STATE_DIR" "$y" >/dev/null 2>&1 || true
}

# 整頁貼上時每一列都清到行尾。只在最後送 \033[J 清不掉短列右邊的舊字，
# 用量條收起後，舊的方塊就留在畫面上，看起來像沒收。
# 最後一列不要再送換行。列數剛好等於窗高時，那個換行會把第一列
# （分頁列）捲出畫面，後面每一列的點擊都往下錯一格。
pane_paint_frame() {
  printf '\033[H'
  printf '%s\n' "$1" | awk 'NR>1{printf "\n"} {printf "%s\033[K", $0}'
  printf '\033[J'
}

# 拖捲軸時不要重開整支右欄。只重切已經畫好的用量頁，標題心跳這輪先不做。
_blit_usage_scroll() {
  local body frame
  body="$(EZ2_H_OFFSET=2 EZ2_ROW_OFFSET=2 \
    "$EZ2_PY3" "$EZ2_COCKPIT_DIR/render/usage_scroll_blit.py" 2>/dev/null)" || return 1
  frame="$(
    printf ' \033[K\n'; printf '  \033[38;5;245m%s\033[0m \033[1;7m %s \033[0m \033[38;5;245m%s\033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
    printf '%s\n' "$body" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'
  )"
  printf '\033[H%s\033[J' "$frame"
  LAST_FRAME="$frame"
  return 0
}

# 1.AI 同樣只重切清單。機器狀態列用快取，不重查席位。
_blit_ai_scroll() {
  local body frame mach n off
  mach=""
  if [ -f "$EZ2_COCKPIT_DIR/machine_status_row.py" ]; then
    mach="$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/machine_status_row.py" --width "${EZ2_PANE_W:-44}" 2>/dev/null || true)"
  fi
  n=0
  if [ -n "$mach" ]; then
    n=$(printf '%s\n' "$mach" | wc -l | tr -d ' ')
  fi
  off=$((2 + n))
  body="$(EZ2_H_OFFSET="$off" EZ2_ROW_OFFSET="$off" \
    "$EZ2_PY3" "$EZ2_COCKPIT_DIR/render/ai_scroll_blit.py" 2>/dev/null)" || return 1
  frame="$(
    printf ' \033[K\n'; printf ' \033[1;7m %s \033[0m \033[38;5;245m%s  %s\033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
    if [ -n "$mach" ]; then
      printf '%s\n' "$mach" | sed 's/$/\x1b[K/'
    fi
    printf '%s\n' "$body" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'
  )"
  printf '\033[H%s\033[J' "$frame"
  LAST_FRAME="$frame"
  return 0
}
LAST_FRAME=""
LAST_GEOMETRY=""
REFRESH_TICKS="${EZ2_RIGHT_REFRESH_TICKS:-40}"
case "$REFRESH_TICKS" in
  ''|*[!0-9]*|0) REFRESH_TICKS=40 ;;
esac

# 自動降溫(the maintainer 09-04 令):熱了就把自己的輪詢放慢 —— 面板是這台機器上
# 少數「我自己就能關小」的耗電源。秒數不寫死在這裡,由 cooldown.py 依現況給,
# 兩邊只有一份門檻(重寫一份的失效形狀是「兩邊各自都對,合起來錯」)。
# ★ 只吃已經量好的快取(毫秒),不在這一層取值:取值 0.72 秒,放進迴圈就本末倒置。
# ★ 讀不到就維持原值 —— 降溫機制自己壞掉時面板要照原速跑,不是停住。
pane_cooldown_ticks() {
  local j p
  j="$EZ2_COCKPIT_STATE_DIR/machine-status.json"
  [ -f "$j" ] || return 0
  p="$("$EZ2_PY3" -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
    v=(d.get("level") or {}).get("poll_s")
    print(int(v) if isinstance(v,(int,float)) and v>0 else "")
except Exception:
    print("")' "$j" 2>/dev/null)"
  case "$p" in
    ''|*[!0-9]*) return 0 ;;
  esac
  REFRESH_TICKS=$(( p * 10 ))
  [ "$REFRESH_TICKS" -ge 10 ] || REFRESH_TICKS=10
  # 再熱也不要超過 8 秒才重查。拖的時候這一輪會被按住，放開後數字才換上。
  [ "$REFRESH_TICKS" -le 80 ] || REFRESH_TICKS=80
}
PANE_SELF_SCRIPT="$EZ2_COCKPIT_DIR/pane-right-tabs.sh"   # BF:碼換了自己重跑
. "$EZ2_COCKPIT_DIR/pane-tick.sh"
pane_tick_init
# ★★ 視窗標題心跳(09-14,the maintainer「標題顯示不正確」):title-beat.sh --once 原本只掛在
#   左欄(pane-left-fleet.sh)與下欄(pane-bottom-tabs.sh)的迴圈裡,而 09-05 起
#   panes.py 預設只建 hub+right —— 於是預設座艙裡**沒有任何行程在更新標題**,
#   標題凍在 cockpit-full.sh 開艙那一刻寫的靜態初值(只有版號,沒有「正在看哪一席」),
#   四個活艙的 cockpit/ 底下連 title-beat-seq 都沒有(=從沒跳過一拍)。
#   tests/test-cockpit-heartbeat.py 舊格的母體是「cockpit-full.sh 提到的每一格」,
#   左/下欄在名單裡所以它一直綠;新格改用 `cockpit-full.sh --print-plan` 算出來的
#   「預設真的會建的格」當母體,本檔是那個集合裡唯一的一格。
#   失敗一律吞掉:標題是輔助資訊,不得擋住右欄渲染(rc0 no-op 紅線)。
hub_active_window() {
  local sock="${EZ2_AIHUB_SOCKET:-}"
  [ -n "$sock" ] || return 0
  env -u TMUX tmux -L "$sock" display-message -p '#{window_id}' 2>/dev/null || true
}
HUBWIN_TICKS="${EZ2_RIGHT_HUBWIN_TICKS:-3}"
case "$HUBWIN_TICKS" in
  ''|*[!0-9]*|0) HUBWIN_TICKS=3 ;;
esac
FAST_REASON=""
TITLE_BEAT_PID=""
CAPTAINS_PID=""
_wait_bg() {
  [ -n "${TITLE_BEAT_PID:-}" ] && wait "$TITLE_BEAT_PID" 2>/dev/null || true
  [ -n "${CAPTAINS_PID:-}" ] && wait "$CAPTAINS_PID" 2>/dev/null || true
}
trap '_wait_bg' EXIT
while true; do
  pane_reexec_if_changed                # BF:腳本換了就 exec 自己(每 ~30 秒問一次)
  pane_cooldown_ticks                   # 降溫:熱了就放慢自己的輪詢
  pane_wait_if_detached && continue     # 沒人看:不渲染,維持運作(§59)
  pane_wait_if_composing && continue    # 終端機組字中:不渲染(10-10,避開 Terminal 重釘組字區的崩潰;見 pane-tick.sh)
  PANE_GEOM_DIRTY=0
  pane_read_file TAB "$TABF"
  pane_read_file KICK "$KICKF"
  # 上一輪若是因為點了頁簽、子頁或捲了用量才醒來，這一輪直接貼預載。
  # 五秒心跳不帶原因，仍整頁重查，時鐘才會走。
  # 以後的右欄頁：頁簽值在下面這段對到 page id。有子選單的頁走該頁的一行檔
  # （用量是 usage-subtab）；沒有子選單的頁走 .right-page-<id>.json。
  EZ2_USAGE_FAST_REASON=""
  EZ2_RIGHT_FAST_REASON=""
  if [ "$TAB" = "1" ]; then
    case "$FAST_REASON" in
      swap|scroll) EZ2_USAGE_FAST_REASON="$FAST_REASON" ;;
      tab) EZ2_USAGE_FAST_REASON=swap ;;
    esac
  elif [ "$FAST_REASON" = "tab" ]; then
    EZ2_RIGHT_FAST_REASON=tab
  fi
  export EZ2_USAGE_FAST_REASON EZ2_RIGHT_FAST_REASON
  if [ -n "${TMUX_PANE:-}" ]; then
    read -r EZ2_PANE_W EZ2_PANE_H <<EOF
$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-44}" "${LINES:-30}")
EOF
    export EZ2_PANE_W EZ2_PANE_H
  fi
  PANE_GEOMETRY="${EZ2_PANE_W:-44} ${EZ2_PANE_H:-30}"
  FRAME="$(render_frame "$TAB")"
  if [ "$FIRST_PAINT" = "1" ] || [ "$FRAME" != "$LAST_FRAME" ] || \
     [ "$PANE_GEOMETRY" != "$LAST_GEOMETRY" ]; then
    pane_paint_frame "$FRAME"
    LAST_FRAME="$FRAME"
    LAST_GEOMETRY="$PANE_GEOMETRY"
    FIRST_PAINT=0
  fi
  # 標題心跳與艦長列在背景做。放在輪詢前面會讓 1.AI、用量、Codex
  # 的點擊都等它們做完（艦長列大約零點七秒）。
  # 上一幀還沒結束就不要再起:連殺會砍到還在跑的 title-beat / captains-ctl。
  if [ -n "${TITLE_BEAT_PID:-}" ] && kill -0 "$TITLE_BEAT_PID" 2>/dev/null; then
    :
  else
    env -u TMUX bash "${AIB_TITLE_BEAT:-$EZ2_COCKPIT_DIR/title/title-beat.sh}" \
      --once >/dev/null 2>&1 || true &
    TITLE_BEAT_PID=$!
  fi
  if [ -n "${CAPTAINS_PID:-}" ] && kill -0 "$CAPTAINS_PID" 2>/dev/null; then
    :
  else
    env -u TMUX bash "${AIB_CAPTAINS_CTL:-$EZ2_COCKPIT_DIR/nav/captains-ctl.sh}" \
      --sync >/dev/null 2>&1 || true &
    CAPTAINS_PID=$!
  fi
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  # 09-15:hub_active_window 只給下面的 tick 迴圈判「切窗提前中斷」用;單幀模式(治具/截幀)不會進迴圈,
  #   不必問 tmux(v0.62.0 證詞 test-codex-ctx-probe:tabs 治具 tmux 是禁用樁)。放在 break 之後,活迴圈行為不變。
  HUB_WIN="$(hub_active_window)"
  HUB_PANE="$(tmux list-panes -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub"{print $1; exit}')"
  # 這一幀自己寫過的子頁與捲動位置，當成下一輪的基準，避免剛換頁又被當成又捲了一次。
  pane_read_file SUBTAB "$EZ2_COCKPIT_STATE_DIR/usage-subtab"
  pane_read_file SCROLL "$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
  FAST_REASON=""
  # Parallel receipt verification normally takes under one second; a four-
  # second idle wait keeps start-to-start refresh near five seconds.  Human
  # tab switches are still polled every 100 ms (zero fork);pane/font resize
  # 走 SIGWINCH 事件,不再每 tick 問 tmux(§59)。
  for ((tick=0; tick<REFRESH_TICKS; tick++)); do
    # 手指按著，或滾輪還有沒走完的距離時，改 0.02 秒看一次。平常仍 0.1 秒。
    _scroll_busy=0
    [ -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab" ] && _scroll_busy=1
    for _qf in "$EZ2_COCKPIT_STATE_DIR/.scroll-wheel.q"/e-*; do
      [ -f "$_qf" ] || continue
      _scroll_busy=1
      break
    done
    if [ "$_scroll_busy" = 1 ]; then
      pane_tick_sleep 0.02
    else
      pane_tick_sleep 0.1
    fi
    [ "$PANE_GEOM_DIRTY" = "1" ] && break          # resize 事件(WINCH)
    pane_read_file TAB_NOW "$TABF"
    if [ "$TAB_NOW" != "$TAB" ]; then
      FAST_REASON=tab                                  # 點了 1.AI／2.AI用量:下一幀貼預載
      break
    fi
    pane_read_file KICK_NOW "$KICKF"
    [ "$KICK_NOW" = "$KICK" ] || break               # 被踢:立刻重畫(開席那一席要馬上出現)
    pane_read_file SUB_NOW "$EZ2_COCKPIT_STATE_DIR/usage-subtab"
    if [ "$SUB_NOW" != "$SUBTAB" ]; then
      FAST_REASON=swap                               # 點了用量／Codex:下一幀貼預載
      break
    fi
    if [ "$_scroll_busy" = 1 ] && [ "$TAB" != "3" ]; then
      # 改位置和重切畫面同一支程式，避免每動一下連開三支。(第 3 頁沒有捲動,不走這條,免得貼上 AI 清單)
      if [ "$TAB" = "1" ]; then
        _body="$(EZ2_H_OFFSET=2 EZ2_ROW_OFFSET=2 \
          "$EZ2_PY3" "$EZ2_COCKPIT_DIR/render/right_scroll_follow.py" usage 2>/dev/null)"
      else
        _off=2
        _body="$(EZ2_H_OFFSET="$_off" EZ2_ROW_OFFSET="$_off" \
          "$EZ2_PY3" "$EZ2_COCKPIT_DIR/render/right_scroll_follow.py" ai 2>/dev/null)"
      fi
      _frc=$?
      if [ "$_frc" -eq 0 ]; then
        if [ "$TAB" = "1" ]; then
          _frame="$(
            printf ' \033[K\n'; printf '  \033[38;5;245m%s\033[0m \033[1;7m %s \033[0m \033[38;5;245m%s\033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
            printf '%s\n' "$_body" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'
          )"
        else
          _frame="$(
            printf ' \033[K\n'; printf ' \033[1;7m %s \033[0m \033[38;5;245m%s  %s\033[0m\033[K\n' "$TAB_AI" "$TAB_USAGE" "$TAB_LEFT"
            printf '%s\n' "$_body" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'
          )"
        fi
        printf '\033[H%s\033[J' "$_frame"
        LAST_FRAME="$_frame"
        pane_read_file SCROLL "$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
        tick=0
        continue
      fi
      # 3 = 已經到頂或到底。留著現在這版，不要接著整頁重查（那一拍又慢又會把捲軸畫壞）。
      if [ "$_frc" -eq 3 ]; then
        tick=0
        continue
      fi
    fi
    # 打字會讓對話欄重畫，寬字偶爾仍溢進來。發現對話欄有動，立刻把右欄上一版貼回去，不等幾秒。
    if [ -n "${HUB_PANE:-}" ] && [ -n "$LAST_FRAME" ]; then
      _hub_act="$(tmux display-message -p -t "$HUB_PANE" '#{pane_activity}' 2>/dev/null || true)"
      if [ -n "$_hub_act" ] && [ "$_hub_act" != "${LAST_HUB_ACT:-}" ]; then
        [ -n "${LAST_HUB_ACT:-}" ] && pane_paint_frame "$LAST_FRAME"
        LAST_HUB_ACT="$_hub_act"
      fi
    fi
    _apply_scroll_drag
    _drain_scroll_nudge
    if [ "$TAB" = "3" ]; then
      # 3.離艙(09-30):沒有預切的快貼,位置一變就整頁重畫(這一頁只讀快取,很快)。
      pane_read_file SCROLL_NOW "$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
      if [ "$SCROLL_NOW" != "$SCROLL" ]; then
        FAST_REASON=scroll
        break
      fi
    elif [ "$TAB" = "1" ]; then
      pane_read_file SCROLL_NOW "$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
      if [ "$SCROLL_NOW" != "$SCROLL" ]; then
        if _blit_usage_scroll; then
          SCROLL="$SCROLL_NOW"                        # 這一幀已貼上，不用整頁重查
          tick=0                                     # 還在拖就不要插進整頁重查
          continue
        fi
        FAST_REASON=scroll
        break
      fi
    else
      pane_read_file SCROLL_NOW "$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
      if [ "$SCROLL_NOW" != "$SCROLL" ]; then
        if _blit_ai_scroll; then
          SCROLL="$SCROLL_NOW"
          tick=0
          continue
        fi
        FAST_REASON=scroll
        break
      fi
    fi
    # 切窗提前中斷(承 pane-bottom-tabs.sh 同一招):標題要在「切到別席」這個事件上
    # 更新,不能只靠五秒心跳 —— 切過去到標題跟上之間那幾秒正是最容易誤導人的視窗。
    if [ -n "$HUB_WIN" ] && [ $(( (tick + 1) % HUBWIN_TICKS )) -eq 0 ]; then
      CURRENT_HUB_WIN="$(hub_active_window)"
      [ "$CURRENT_HUB_WIN" = "$HUB_WIN" ] || break
    fi
  done
done
