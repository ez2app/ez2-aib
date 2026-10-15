#!/bin/bash
# aib boards(設計稿 §23):在一席的 tmux 視窗右邊切一格,跑這台電腦的小窗格。不是 Claude 的席也能用。
#   open.sh                在哪一席打就開在哪一席旁邊
#   open.sh --seat <席碼>   開在指定那一席的視窗(本艙)
#   open.sh --close [--seat <席碼>]   收掉
# 比例(the maintainer 10-14 18:0x「修好開窗比例」):旁邊那一席的 AI 一定留 AI_MIN 欄(原本右邊佔 72% ⇒ 128 欄的視窗 AI 只剩 30 欄,
#   輸入框被擠掉、信排隊送不進去);小窗格最多佔 BOARD_PCT%、最少 BOARD_MIN 欄,左右放不下就開在下方(BELOW_PCT% 高)。
#   收掉(q)後整格消失:艙的 tmux 會保留結束的窗格,這一格把 remain-on-exit 關掉。
set -u
AI_MIN=80 BOARD_PCT=55 BOARD_MIN=70 BELOW_PCT=45   # 小窗格最少 70 欄(tui 測試 R1 量到 70 欄不超寬)
# 切法:旁邊 AI 留 AI_MIN 欄後,小窗格寬 = min(視窗 × BOARD_PCT%, 視窗 − AI_MIN − 1);不到 BOARD_MIN ⇒ 改開下方。印「-h 欄數」或「-v 列數」
board_split() {
  local w="$1" h="$2" cols
  cols=$(( w * BOARD_PCT / 100 )); [ "$cols" -gt $(( w - AI_MIN - 1 )) ] && cols=$(( w - AI_MIN - 1 ))
  if [ "$cols" -ge "$BOARD_MIN" ]; then echo "-h $cols"
  elif [ $(( h * BELOW_PCT / 100 )) -ge 5 ]; then echo "-v $(( h * BELOW_PCT / 100 ))"
  else echo "- 0"; fi   # 太小(下方不到 5 列):不切,呼叫端照實說
}
[ "${1:-}" = "--split-for" ] && { board_split "$2" "$3"; exit 0; }   # 測試用:只算切法
DIR="$(cd "$(dirname "$0")" && pwd)"
SEAT=""; CLOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --seat) [ $# -ge 2 ] || { echo "用法:aib boards [--seat <席碼>] [--close]" >&2; exit 2; }   # 少了席碼:shift 2 會失敗又不位移 ⇒ 迴圈卡死(審查 #6)
            SEAT="${2#@}"; shift 2
            [ -n "$SEAT" ] || { echo "用法:aib boards [--seat <席碼>] [--close](--seat 後面要給席碼)" >&2; exit 2; } ;;   # 寫成 @00000 也認;空的 / 只有 @ 不當成自己那一席
    --close) CLOSE=1; shift ;;
    *) echo "用法:aib boards [--seat <席碼>] [--close]" >&2; exit 2 ;;
  esac
done
COCKPIT="${EZ2_AIB_ROOT:-$HOME/ez2-ai-b}/src/cockpit"
NODE="$(command -v node || true)"
[ -n "$NODE" ] || { echo "aib boards:這台沒有 node,開不了小窗格" >&2; exit 1; }

# 找目標:席碼 → (艙, 視窗);沒給席碼 ⇒ 用現在這一格所在的視窗與這一席的席碼
read -r CODE CABIN WIN < <(SEAT="$SEAT" /usr/bin/python3 -I - "$COCKPIT" <<'PY'
import glob, json, os, sys
sys.path.insert(0, sys.argv[1])
from seat_address import code_of
from seats_dir_lib import state_root
want = os.environ.get("SEAT") or ""
if not want:
    try:
        from seat_now import self_code
        want = self_code() or ""
    except Exception:
        want = ""
best = None
for f in glob.glob(os.path.join(state_root()[0], "instances", "*", "seats", "w*.json")):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if d.get("retired") or d.get("exited_at_epoch") or code_of(d) != want:
        continue
    if best is None or (d.get("started_epoch") or 0) > (best.get("started_epoch") or 0):
        best = d
print(want or "-", (best or {}).get("cabin_id") or "-", (best or {}).get("window_id") or "-")
PY
)
[ "${CODE:-}" != "-" ] && [ -n "${CODE:-}" ] || { echo "aib boards:量不到是哪一席(打 aib boards --seat <席碼>)" >&2; exit 1; }
[ "$WIN" != "-" ] || { echo "aib boards:@$CODE 沒有登記視窗" >&2; exit 1; }
HUB="$(bash "$COCKPIT/cabin_resolve.sh" --hub "$CABIN" 2>/dev/null)" || HUB=""   # 艙名只准單源拼(test-cabin-resolution R8)
[ -n "$HUB" ] || { echo "aib boards:@$CODE 的艙 $CABIN 解不出 tmux 名" >&2; exit 1; }
T=(tmux -L "$HUB")
# 艙的 tmux 不在 ⇒ 照實說,不退回預設那台(同號視窗會是別人的,審查 #7)
"${T[@]}" has-session >/dev/null 2>&1 || { echo "aib boards:@$CODE 的艙 $CABIN 沒在跑(tmux -L $HUB 找不到)" >&2; exit 1; }

# 已經開著 ⇒ --close 收掉,否則跳過去
EXIST="$("${T[@]}" list-panes -t "$WIN" -F '#{pane_id} #{@aib_boards}' 2>/dev/null | awk '$2=="1"{print $1; exit}')"
if [ "$CLOSE" = 1 ]; then
  [ -n "$EXIST" ] && "${T[@]}" kill-pane -t "$EXIST" && echo "aib boards:@$CODE 的小窗格收掉了" || echo "aib boards:@$CODE 沒有開著的小窗格"
  exit 0
fi
if [ -n "$EXIST" ]; then "${T[@]}" select-pane -t "$EXIST"; echo "aib boards:@$CODE 已經開著,跳過去"; exit 0; fi

# 量與切的是同一格:視窗裡最大的那一格(AI 主窗格;席檔沒記窗格 id)。原本量整個視窗、切「作用中」那格 ⇒
#   視窗已分兩格時算出的寬度比要切的那格還寬、也不一定切在 AI 那格(審查 10-14)
read -r TGT W H < <("${T[@]}" list-panes -t "$WIN" -F '#{pane_id} #{pane_width} #{pane_height}' 2>/dev/null | awk '{a=$2*$3; if(a>m){m=a; l=$0}} END{print l}')
[ -n "${TGT:-}" ] || { echo "aib boards:@$CODE 的視窗 $WIN 量不到窗格" >&2; exit 1; }
read -r SPLIT SIZE <<<"$(board_split "$W" "$H")"
[ "$SPLIT" != - ] || { echo "aib boards:@$CODE 那一格太小(${W}x${H}),開不下小窗格" >&2; exit 1; }
# 新窗格吃的是 tmux 伺服器的環境,不是這個殼的 ⇒ 資料層位置與狀態根用 -e 帶過去(審查 #9);路徑一律 %q 引號(審查:路徑含 ' 會壞)
ENVS=(-e "EZ2_AIB_ROOT=${EZ2_AIB_ROOT:-$(cd "$COCKPIT/../.." && pwd)}")
[ -n "${EZ2_AIB_STATE_ROOT:-}" ] && ENVS+=(-e "EZ2_AIB_STATE_ROOT=$EZ2_AIB_STATE_ROOT")
[ -n "${EZ2_AIB_HOME:-}" ] && ENVS+=(-e "EZ2_AIB_HOME=$EZ2_AIB_HOME")
[ -n "${EZ2_AIB_SEAT_SCREEN:-}" ] && ENVS+=(-e "EZ2_AIB_SEAT_SCREEN=$EZ2_AIB_SEAT_SCREEN")
# 語系(i18n_lib.py --resolve)要看得到「這一艙」的艙檔 ⇒ 帶目標艙的 EZ2_AIB_INSTANCE_HOME;位置向 cabin_resolve.sh 單源要,不自己拼
#   (從別艙打 --seat 時,本殼的 EZ2_AIB_INSTANCE_HOME 是自己這艙的,不能直接帶 ⇒ 先拿掉再用目標艙解)
INST_HOME="$(env -u EZ2_AIB_INSTANCE_HOME -u EZ2_TMUX_SESSION -u EZ2_AIHUB_SOCKET EZ2_AIB_INSTANCE="$CABIN" bash "$COCKPIT/cabin_resolve.sh" --print 2>/dev/null | sed -n 's/^EZ2_AIB_INSTANCE_HOME=//p')"
[ -n "$INST_HOME" ] && ENVS+=(-e "EZ2_AIB_INSTANCE_HOME=$INST_HOME")
CMD="exec $(printf %q "$NODE") --no-warnings --import $(printf %q "$DIR/reg.mjs") $(printf %q "$DIR/main.mjs") --seat $(printf %q "$CODE")"
NEW="$("${T[@]}" split-window "$SPLIT" -d -P -F '#{pane_id}' -l "$SIZE" -t "$TGT" -c "$HOME" "${ENVS[@]}" "$CMD")" || { echo "aib boards:切不出窗格" >&2; exit 1; }
"${T[@]}" set-option -p -t "$NEW" @aib_boards 1 2>/dev/null || { echo "aib boards:窗格 $NEW 一開就結束了(小窗格程式起不來;單獨跑 node $DIR/main.mjs --seat $CODE 看錯誤)" >&2; exit 1; }
"${T[@]}" set-option -p -t "$NEW" remain-on-exit off 2>/dev/null || true   # 按 q 收掉就整格消失,不留死窗格佔位
[ "$SPLIT" = -h ] && WHERE="右邊($NEW,$SIZE 欄,AI 那格 $TGT 留 $(( W - SIZE - 1 )) 欄)" || WHERE="下方($NEW,$SIZE 列)"
echo "aib boards:@$CODE 的電腦開在 $WIN $WHERE;在那一格按 q 收掉"
