#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB DM v3 — fail-closed 直投引擎(v2 守衛全保留+通道升級,2026-08-14)
#
#   bash tools/cockpit-ops/aib-dm.sh <目標視窗名|@窗id> <訊息...>
#
# 沿革:v1(版控外 demo aib-dm.sh)=九行裸 send-keys 毀稿型,**已廢棄**——
# 2026-08-10 四條 lane 全滅的直接機制(派工單被打進 BIND 提示當答案,工具
# 照報成功);任何人不要再複製那個形狀。v2 立 fail-closed 五守衛。v3(本版):
#   - 通道 send-keys -l → **load-buffer+paste-buffer -p**(暫存檔免轉義,
#     bracketed paste 對 TUI 安全;艦長對 codex 實證路徑,繞 IME 與長文切字)。
#     裸 send-keys -l 字面投遞自此禁用(測試負向看守);Enter 仍為獨立按鍵。
#   - 新守衛:**貼前卡稿偵測**——目標 composer 有滯留稿時拒投,並把整幅
#     畫面留檔(卡稿復原三步第一步:留檔原文;@4 兩次實證,裸 Enter 會毀稿)。
#   - 目標支援 @窗id(席位解析層的產物;窗名仍收,精確比對)。
#
# 本工具只證明按鍵確實落進目標 pane 的輸入區(貼後回讀、Enter 前比對),
# 絕不宣稱對方讀了或做了——送達≠送到工作(HANDOFF 第十一形)。
#
# exit code: 0 傳輸已驗證 / 2 用法錯 / 3 目標不可投遞 / 4 畫面不可投遞狀態
#            (BIND 提示或卡稿) / 5 傳輸未通過驗證
#            / 6 停投名單拒投(政策,非技術失敗——與 3 分開才判得出來)
set -uo pipefail

# ★★ 08-16:同一件事原本有**兩個變數名**,而這是「每一層各自決定我在哪一艙」
#   這個結構問題的第四個位置(前三:`aib --instance` 不帶就開到第一台、
#   本檔下面那個 `has-session` 拿 socket 名當 session 名查、席位守衛的抽屜)。
#
#   分岔的形狀:上游 `seat_onboard.py:308` 與 `dm-ctl.sh:27` 讀
#   `EZ2_AIHUB_SOCKET`,而本檔只讀 `AIB_DM_SOCKET`。**上游設對了,傳到本檔
#   就斷了**,退回預設 `aihub`(第一台)——在那裡當然找不到別台的窗。
#   ★ 這個坑當天就咬了人:第一艦長 4cdc5 傳 `AIB_DM_SOCKET` 去測上游的
#     `seat onboard`(那條鏈根本不讀它),拿到 rc=3 之後把「我的探針沒接上」
#     報成「產品沒有證據能動」,還寫進了 CHANGELOG 與交接單。
#
#   兩個名字都認,`AIB_DM_SOCKET` 優先(本檔專屬覆寫勝過全域):
SOCKET="${AIB_DM_SOCKET:-${EZ2_AIHUB_SOCKET:-aihub}}"
# hub 內的 session 名與 socket 名是**兩件事**:socket 每個 instance 一個
# (aihub / aihub-7 / aihub-8 …),而裡面的 session 一律叫 aihub。
# 本檔下游與 dm-ctl.sh / pane-right-ai.sh 都用 `-t aihub`,這裡對齊。
HUB_SESSION="${AIB_DM_HUB_SESSION:-aihub}"
TMUX_BIN="${AIB_DM_TMUX:-tmux}"
# ---- 暫存/留檔目錄:單一來源,兩處同源(FIXFWD-AIB-DM-20260814 修1)------
# 08-14 記界單(裁核 a seat,maker 候動工 0a5b3,因額度暫緩)指名兩處同型:
#   :25 STATE_DIR 預設鏈繼承 TMPDIR(/var/folders 拒寫)→ 留檔掉證據
#   :84 mktemp **又重複推導同一式子** —— 此處才是真阻斷:die 5 信送不出
# 2026-08-16 實測仍在:seat 0c153 投遞開席單時 `mktemp: Permission denied`。
# ★ 我當時把它判成「我這個 shell 的沙箱問題,不是產品問題」——那句是錯的,
#   記界單早就寫著它是產品缺陷。**已經有人記過的東西,不要再判一次成環境問題。**
#
# root3 三點裁,逐條照做:
#   a. 收斂單一 STATE_DIR,兩處同源,不准只補一處
#   b. 可寫性=**真寫真刪探針**,禁 `[ -w ]`(唯讀掛載/seatbelt 會讓 -w 假過)
#   c. 顯式 AIB_DM_STATE_DIR 原樣尊重不靜默替換(治具靠它釘死)→ 空值時 TMPDIR
#      (真可寫才用,不剝奪環境意圖)→ /private/tmp → /tmp → 全不可寫誠實回報
_dm_writable() {
  # ★ 真寫真刪,不用 [ -w ]:唯讀掛載與 seatbelt 沙箱會讓 -w 假過,
  #   那正是「探針說可以、真寫下去才炸」的形狀。
  [ -d "$1" ] || return 1
  _probe="$1/.aib-dm-probe.$$"
  ( : > "$_probe" ) 2>/dev/null || return 1
  rm -f "$_probe" 2>/dev/null
  return 0
}

if [ -n "${AIB_DM_STATE_DIR:-}" ]; then
  # 顯式指定=原樣尊重。不可寫也不靜默換掉——治具靠它釘死路徑,
  # 而「我指定了 A 卻寫到 B」比「寫不進去」更難查。
  STATE_DIR="$AIB_DM_STATE_DIR"
else
  STATE_DIR=""
  for _cand in "${TMPDIR:-}" /private/tmp /tmp; do
    [ -n "$_cand" ] || continue
    if _dm_writable "$_cand"; then STATE_DIR="$_cand"; break; fi
  done
fi
# 卡稿偵測樣式:composer 行(│ > 稿 或行首 > 稿)。誤殺時可調樣式;
# AIB_DM_SKIP_DRAFT_GUARD=1=文檔化逃生口(明知無稿誤判時用,預設守衛在)。
DRAFT_RE="${AIB_DM_DRAFT_RE:-(^|│)[[:space:]]*[>❯][[:space:]]+[^[:space:]]}"

die() { echo "aib-dm: $2" >&2; exit "$1"; }

[ "$#" -ge 2 ] || die 2 "用法: aib-dm.sh <目標視窗|@窗id> <訊息...>"
TARGET="$1"; shift
# 08-14 覆審發現一(主管 0a5b3 對抗式覆審):目標為空白時,下面的
# `grep -qxF "" <<<"$_wins"` 會**匹配 here-string 產生的那個空行**而回 0
# =誤判「窗存在」,再靠下游 pane_dead 閘擋下來,錯誤訊息還會說成「pane 已
# 死」而不是「窗不存在」。最小反例:`grep -qxF "" <<<""` rc=0,而真正的
# 零行生產端 `printf '' | grep -qxF ""` rc=1。空目標本來就是用法錯,在這裡
# fail-closed 才對,不要讓它走到比對邏輯。
[ -n "${TARGET//[[:space:]]/}" ] || die 2 "目標不得為空白"
MSG="$*"
[ -n "${MSG//[[:space:]]/}" ] || die 2 "訊息不得為空白"

# B17:本檔是已解析 window_id 的投遞引擎,不是對人入口。現行受支援路徑中,
# dm-ctl 在席位 socket / pid 三格全數通過之後,才會交下原始選擇子載體。直接呼叫
# 本檔時沒有這份承重收據;繼續送會讓「根本沒查席位檔」與「席位檔真的缺欄」
# 在輸出上同形,並可能把同一個 @window_id 送往錯的 socket。
# ★ 誠實邊界:這是防止正常工具鏈誤走旁路,不是密碼學證明;呼叫端若刻意偽填
# AIB_DM_INPUT_SELECTOR 仍可繞過。不可偽造的跨行程證明需要另定協定,本件不冒充已有。
#
# 這裡刻意不提供跳過旗標:工具鏈需要投遞時也應走唯一入口。拒絕放在任何
# tmux 查詢與 load/paste/send 之前,所以非零同時保證目標端零進入。
if [ -z "${AIB_DM_INPUT_SELECTOR:-}" ]; then
  die 2 "本次未經席位 socket 閘,拒絕直呼投遞引擎。請改用 aib dm;本艙包裝器請用 dm49。"
fi

# ── T-dm-send-record(判準 a seat b79ab2a4,D1–D5):投遞留痕 ────────────
# ★★★ D4 的落點是 `trap EXIT`,不是「die 與成功路徑各補一次」:
#     rc 只有在離開的那一刻才是確定的,而**寫在送出之前記的是「打算送誰」**
#     —— 本件要消滅的正是那個東西(dev1 08-27 假信頭同型)。
# ★★ D5 成功與失敗同格式同檔:trap 是全檔**唯一同時涵蓋兩側**的那一個點;
#     現有三個寫入點(:235/:278/:295)全在失敗路徑,所以這裡是補一次呼叫,
#     不是新建第二份日誌。

# D2 必紅:記不了就不投。
# ★ 「沒有紀錄」與「沒送過」在帳上逐字同形 —— 那正是本件要消滅的那個同形,
#   所以它不能靜默降級成「送了但沒記」。
[ -n "${STATE_DIR:-}" ] && _dm_writable "$STATE_DIR" \
  || die 3 "留痕落點不可寫(STATE_DIR='${STATE_DIR:-空}')——記不了就不投:沒有紀錄與沒送過在帳上同形。"
DM_RECORD="${AIB_DM_RECORD:-$STATE_DIR/aib-dm-sent.tsv}"

# D1 指紋:sha256 前 12 碼 + 字元數。
# ★ 只存指紋不存內容(也不存前 N 字元)——別席的私訊/畫面不進任何紀錄。
_dm_fp="$(printf '%s' "$MSG" | shasum -a 256 2>/dev/null | cut -c1-12)"
[ -n "$_dm_fp" ] || _dm_fp="nosha"
_dm_len="${#MSG}"

# ★ 判準 a seat 22:51 裁 3:六欄格式落**新檔**,舊檔一個位元組不動。
#   理由(他的):若同一檔容忍兩種欄數,「舊列」與「新列而該欄丟失」**逐字相同**
#   ⇒ 那是一個讀不出來的狀態,補多少臂都問不出真相。分檔之後新檔可以無例外釘死 6。
DM_RECORD_V2="${AIB_DM_RECORD_V2:-$STATE_DIR/aib-dm-sent-v2.tsv}"

# v3 九欄另檔(工單 AH,現量 AD 三洞):socket、寄件人、契約 rc。
# 舊 v1/v2 一位元組不動。分檔理由與 v2 相同:同一檔容忍兩種欄數,
# 「舊列」與「新列而該欄丟失」逐字相同。
# 持久落點=家目錄 state(系統清不掉);暫存目錄那份照寫(相容,讀的人還在找 TMPDIR)。
# 測試/治具設了 AIB_DM_STATE_DIR 時,不偷偷寫進家目錄(否則臂汙染真帳)。
if [ -n "${AIB_DM_PERSIST_DIR:-}" ]; then
  _DM_PERSIST_DIR="$AIB_DM_PERSIST_DIR"
elif [ -z "${AIB_DM_STATE_DIR:-}" ]; then
  _DM_PERSIST_DIR="${HOME}/.ez2-aib/state"
else
  _DM_PERSIST_DIR=""
fi
DM_RECORD_V3="${AIB_DM_RECORD_V3:-$STATE_DIR/aib-dm-sent-v3.tsv}"
if [ -n "$_DM_PERSIST_DIR" ]; then
  DM_RECORD_V3_PERSIST="${AIB_DM_RECORD_V3_PERSIST:-$_DM_PERSIST_DIR/aib-dm-sent-v3.tsv}"
else
  DM_RECORD_V3_PERSIST=""
fi

# ★ 裁 2:第六欄=**原始選擇子**(人打進 `aib dm` 的那個字),缺席時記哨兵 `-`,**不記空字串**。
#   ★★ 這一欄必須**自己**分得出「沒傳下來」,不得借 :90 那道「目標不得為空白」的閘 ——
#      他的理由逐字:那樣這一欄的語意就住在**別的檔裡的一道閘**上,
#      而 :90 哪天為它自己的理由被放寬,這一欄就開始同形,**而不會有任何東西紅**。
#   ⇒ 自分辨的根據是**哨兵的形狀本身**:`-` 不在合法選擇子的語言裡
#      (dm-ctl 三形 = role | [0-9a-z]{4,8} | @窗id,沒有一種產得出 `-`),
#      所以「值是 `-`」與「有人真的打了 `-`」在結構上不可能混。
# ★ 載體名 **未定** —— qa3 22:51 耦合裁定:env 名由 a seat 先落版控,兩半都引它,不各自假設。
#   在它落版控之前,本檔**只認一個位置**(下面這一行),改名時只改這一行。
_DM_SEL_CARRIER_NAME="${AIB_DM_SEL_CARRIER_NAME:-AIB_DM_INPUT_SELECTOR}"   # ← 候 @00000 落版控後對齊
eval "_dm_sel_raw=\${$_DM_SEL_CARRIER_NAME:-}"
if [ -z "${_dm_sel_raw//[[:space:]]/}" ]; then _dm_sel="-"; else _dm_sel="$_dm_sel_raw"; fi

# 寄件人選擇子:ctl 交下來的 uuid 尾五碼。缺席記哨兵 `-`,不記空字串。
# 載體名只認下面這一處,改名時只改這一行(與目標選擇子同一紀律)。
_DM_SENDER_CARRIER_NAME="${AIB_DM_SENDER_CARRIER_NAME:-AIB_DM_SENDER_SELECTOR}"
eval "_dm_sender_raw=\${$_DM_SENDER_CARRIER_NAME:-}"
if [ -z "${_dm_sender_raw//[[:space:]]/}" ]; then _dm_sender="-"; else _dm_sender="$_dm_sender_raw"; fi

# 契約 rc ← 引擎 rc。正本一句在 dm-ctl.sh 接口契約:
#   0→0; 5→3; 其餘→2。
# ★ 兩欄必須能不同:讀的人拿引擎第三欄數「rc=3」會數到 0(工單 AD 現量)。
_dm_contract_from_engine() {
  case "$1" in
    0) printf '%s' 0 ;;
    5) printf '%s' 3 ;;
    *) printf '%s' 2 ;;
  esac
}

_dm_write_v3_row() {
  _v3_line="$1"
  _v3_path="$2"
  { printf '%s\n' "$_v3_line" >> "$_v3_path"; } 2>/dev/null \
    || echo "aib-dm: ★ 投遞留痕 v3 這一次沒有寫進去 —— 落點 $_v3_path 寫不進;舊檔仍在,**這一次的 socket/寄件人/契約 rc 從此問不出來**(不影響投遞)。" >&2
}

_dm_record_written=0
_dm_write_record() {
  _rc="$1"
  [ "$_dm_record_written" = "0" ] || return 0
  _dm_record_written=1
  _dm_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  _dm_sock="$SOCKET"
  [ -n "${_dm_sock//[[:space:]]/}" ] || _dm_sock="-"
  _dm_contract="$(_dm_contract_from_engine "$_rc")"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$_dm_ts" "$TARGET" "$_rc" "$_dm_fp" "$_dm_len" \
    >> "$DM_RECORD" 2>/dev/null
  # ★ 六欄新檔:舊檔那一行**原樣保留**,兩檔各守各的欄數(裁 3)。
  # ★★ 判準 qa3 23:05 裁(甲):寫不進去要**具名告警**,而「具名」是承重字 ——
  #   要說出**後果**(哪一欄這次沒寫進去),不是說出 errno。
  #   他的判準逐字:**痕跡的價值 = 它讓讀者問得出下一個問題的能力。**
  #   `Permission denied` 問不出來(而那正是不加這一段時畫面上唯一會出現的東西:
  #   bash 自己的重導向錯誤,我原本以為 `2>/dev/null` 蓋得住它,**蓋不住**)。
  # ★ 這裡**不 die** —— qa3 22:51 裁:把留痕缺口升級成投遞中斷,失效方向不成比例。
  #   ★★★ 翻轉條件(他 23:05 要求寫進碼與收據):
  #     **v2 一旦被任何人拿去當對帳依據,這一格必須重審 —— 屆時「不投」才是對的。**
  #     分界不是新舊,是**有沒有人拿它當對帳依據**:舊檔是承重的帳,v2 今天還不是任何人的證據。
  #     沒有這一句,那一裁會在某個沒有人注意的時刻變成錯的,而不會有任何東西紅。
  { printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$_dm_ts" "$TARGET" "$_rc" "$_dm_fp" "$_dm_len" "$_dm_sel" \
      >> "$DM_RECORD_V2"; } 2>/dev/null \
    || echo "aib-dm: ★ 投遞留痕的**第六欄(原始選擇子)這一次沒有寫進去** —— 落點 $DM_RECORD_V2 寫不進;舊檔那一列仍在,**這一次的『人打了什麼』從此問不出來**(不影響投遞)。" >&2
  # v3 九欄:UTC socket 寄件人 目標窗 目標選擇子 引擎rc 契約rc 指紋 字數
  _v3_row="$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
    "$_dm_ts" "$_dm_sock" "$_dm_sender" "$TARGET" "$_dm_sel" \
    "$_rc" "$_dm_contract" "$_dm_fp" "$_dm_len")"
  _dm_write_v3_row "$_v3_row" "$DM_RECORD_V3"
  if [ -n "$DM_RECORD_V3_PERSIST" ]; then
    _v3_tmp_abs=""
    _v3_persist_abs=""
    _v3_tmp_abs="$(cd "$(dirname "$DM_RECORD_V3")" 2>/dev/null && pwd)/$(basename "$DM_RECORD_V3")"
    mkdir -p "$(dirname "$DM_RECORD_V3_PERSIST")" 2>/dev/null || true
    _v3_persist_abs="$(cd "$(dirname "$DM_RECORD_V3_PERSIST")" 2>/dev/null && pwd)/$(basename "$DM_RECORD_V3_PERSIST")"
    if [ -n "$_v3_persist_abs" ] && [ "$_v3_persist_abs" != "$_v3_tmp_abs" ]; then
      _dm_write_v3_row "$_v3_row" "$DM_RECORD_V3_PERSIST"
    fi
  fi
}
# ★ trap 裝在停投檢查**之前**:被名單拒掉也是一次投遞嘗試,而「有人嘗試過」
#   正是那條令要看的東西。裝在後面的話,唯一不留痕的就是它要防的那一種。
trap '_dm_write_record "$?"' EXIT

# D3 停投名單:落點在**投遞當下**——收件端擋不住「已經送出」。
_dm_stoplist="${AIB_DM_STOPLIST:-$HOME/.ez2-aib/state/dm-stoplist.txt}"
# ── D3-b(qa3 更正二):落空端要印的不是「幾筆」,是**這個名單當下的狀態** ──
# ★★★ 舊版只印 N,而 `N=0` 同時是**檔不存在**與**檔存在而空** ——
#     **探測器的失效值(名單檔不見了)等於它表示安全的那個值(名單是空的)。**
#     判準作者自己標:錯的是那條判準不是實作;這裡把它補成三態。
# ★ 三態必須各自可分辨:MISSING / EMPTY / N 筆。
_dm_stop_n=0
if [ ! -e "$_dm_stoplist" ]; then
  _dm_stop_state="MISSING"
elif [ ! -r "$_dm_stoplist" ]; then
  # ★ 第四種形狀:檔在而讀不到 —— 它既不是 MISSING 也不是 EMPTY,
  #   而把它併進任何一邊都會讓「讀不到」長得像「沒有人被擋」。
  _dm_stop_state="UNREADABLE"
else
  # ★ 不用 `grep -c … || echo 0`:零命中時 grep 印 0 且 rc=1,那個 `||` 會**再印一個 0**。
  _dm_stop_n="$(grep -cvE '^[[:space:]]*(#|$)' "$_dm_stoplist" 2>/dev/null)"
  [ -n "$_dm_stop_n" ] || _dm_stop_n=0
  if [ "$_dm_stop_n" -eq 0 ]; then _dm_stop_state="EMPTY"; else _dm_stop_state="LISTED"; fi
fi
case "$_dm_stop_state" in
  MISSING)    echo "aib-dm: 停投名單 **MISSING**(檔不存在):$_dm_stoplist —— 沒有名單不等於沒有人該被擋" >&2 ;;
  UNREADABLE) die 3 "停投名單讀不到(檔在而不可讀):$_dm_stoplist —— **讀不到不是「名單是空的」**,拒絕投遞。" ;;
  EMPTY)      echo "aib-dm: 停投名單 EMPTY(檔在,0 筆):$_dm_stoplist" >&2 ;;
  LISTED)     echo "aib-dm: 停投名單 ${_dm_stop_n} 筆:$_dm_stoplist" >&2 ;;
esac
# ★ 更正一(qa3 13:0x):同一件事的兩個檔落在兩個目錄,而畫面上只印了其中一個的路徑
#   ⇒ **一個被印出來的路徑,會變成讀者對「其他檔在哪」的預設。**
#   他照停投名單的路徑去找留痕檔、找不到,差一步報「活體投遞沒有留下任何一筆」。
#   修法不是叫人小心,是**把另一個路徑也印出來**。
echo "aib-dm: 投遞留痕落點:$DM_RECORD" >&2
echo "aib-dm: v3 暫存落點:$DM_RECORD_V3" >&2
if [ -n "$DM_RECORD_V3_PERSIST" ]; then
  echo "aib-dm: v3 持久落點:$DM_RECORD_V3_PERSIST" >&2
fi
if [ "$_dm_stop_n" -gt 0 ] && grep -qxF -- "$TARGET" "$_dm_stoplist" 2>/dev/null; then
  die 6 "目標在停投名單上,拒絕投遞:$TARGET(名單 $_dm_stoplist,${_dm_stop_n} 筆)。解除=從名單移除該行。"
fi
# ── /T-dm-send-record ────────────────────────────────────────────────────

# 1. socket 與 session 必須在
# ★★ 2026-08-16 修:原本是 `has-session -t "$HUB_SESSION"`——**拿 socket 名當
#    session 名查**。hub 內的 session 一律叫 `aihub`(與本檔下游、dm-ctl.sh、
#    pane-right-ai.sh 的 `-t aihub` 一致),只有第一台因為 socket 也叫 aihub
#    才碰巧對上。第二台起(aihub-7 / aihub-8 …)這一行**必然失敗**,於是
#    `aib` 的開席單投遞每次都回「未驗」,那些席位**從來沒收到過開席單**。
#    ★ 一個判準只在某一種配置下碰巧正確,在其餘配置下靜默失敗——而失敗的
#    症狀(rc=3「未驗」)看起來像「CLI 冷啟中」這種暫時性問題,所以沒有人
#    去查。實測:aihub → 過;aihub-7 / aihub-8 → 失敗,而三者 session 都在。
"$TMUX_BIN" -L "$SOCKET" has-session -t "$HUB_SESSION" 2>/dev/null \
  || die 3 "socket/session 不存在: -L $SOCKET(session=$HUB_SESSION)"

# 2. 視窗必須存在。@窗id=精確比 window_id;其餘=精確比窗名(不用模糊
#    target 解析,避免投錯窗)。
#    ★ 08-14 CI 修(root3 實測):原本是 `tmux list-windows | grep -qxF`,
#    那是**短路管線**——grep -q 一比中就退出並關掉讀端,tmux 還沒寫完剩下
#    的窗就吃 SIGPIPE(rc 141),而 set -o pipefail 取管線中最右的非零 rc,
#    於是「比中了」被判成失敗 → 誤報窗不存在、投遞被擋。實測(外部生產端
#    13 窗、命中第一行)1800 次有 251 次 rc=141,約 14%;**越早命中的窗
#    越容易誤判**,真機上對 @1/@2 這種前段窗投遞最常中。CI run
#    31774572396 的 ubuntu/py3.14 紅就是這個。
#    修法=**先落值再比對**:here-string 不是管線,不與 pipefail 交互;
#    grep -qxF 的「整行、字面、精確」語意零變更。
case "$TARGET" in
  @*)
    _wins="$("$TMUX_BIN" -L "$SOCKET" list-windows -t "$HUB_SESSION" \
             -F '#{window_id}' 2>/dev/null)"
    grep -qxF -- "$TARGET" <<<"$_wins" || die 3 "窗 id 不存在: $SOCKET:$TARGET"
    ;;
  *)
    _wins="$("$TMUX_BIN" -L "$SOCKET" list-windows -t "$HUB_SESSION" \
             -F '#{window_name}' 2>/dev/null)"
    grep -qxF -- "$TARGET" <<<"$_wins" || die 3 "視窗不存在: $SOCKET:$TARGET"
    ;;
esac

PANE="$SOCKET:$TARGET.0"
case "$TARGET" in @*) PANE="$TARGET" ;; esac   # window_id 可直接當 target

# 3. pane 必須活著。死 pane 收不到任何按鍵,v1 對它送信也照樣回報成功。
DEAD="$("$TMUX_BIN" -L "$SOCKET" display-message -p -t "$PANE" '#{pane_dead}' 2>/dev/null)"
[ "$DEAD" = "0" ] || die 3 "目標 pane 已死(pane_dead=$DEAD): $PANE"

# 4a. 畫面狀態閘:身份/同意提示期間一律拒投。
#     2026-08-10 四條 lane 全滅的根因守衛(派工單被當 BIND 答案吞掉)。
# ★ 只抓一次,帶 -e(保留 SGR):卡稿守衛要靠 dim 判 CLI 建議,而 BIND/樣式比對要純文字。
#   多抓一次不只是多一次往返——畫面在兩次抓取之間會變,兩個判斷就會建立在不同的畫面上
#   (實測:第二次抓到的已經是貼稿後的畫面)。同一個決策只准有一份畫面。
# ★★ rc 獨立取(艦長 23:16 令;root2 發現)。原本 `$(... 2>/dev/null)` 把 rc 丟掉,
#   於是**「capture 失敗」與「框本來就乾淨」產生同一個值——空字串**。
#   後果落在承重件上:BEFORE 空 → `before_placeholders` 空 → AFTER 裡任何一枚
#   佔位符都算「新出現」→ **白名單條件一自動成立,而條件一是那條規矩唯一擋得住
#   模態的部分**;它會在 capture 失敗時**自己消失而且不報錯**。
#   ★ 不對稱使「兩次一起失敗所以互相抵銷」不成立::141 帶 `-e`、:241 不帶,
#     旗標面不同不保證同時失敗,而 fail-open 需要的正是「BEFORE 空、AFTER 有值」。
#   同一句話今晚已對 `git status` 講過一次(N1 v2.1),這是同一個病的第二個工具。
BEFORE_E="$("$TMUX_BIN" -L "$SOCKET" capture-pane -e -p -t "$PANE" 2>/dev/null)"; _cap_rc=$?
[ "$_cap_rc" = "0" ] || die 3 "讀不到目標畫面(capture-pane -e rc=$_cap_rc):**取不到畫面不等於畫面乾淨**,拒絕投遞。"
# 10-14 交接單 K8:Claude 輸入框停在輸入歷史(框頂線 History n/m)時框裡是別席的舊訊息,不是草稿。
#   貼前先試著退回空框:只按 ↓、每按一次重讀、畫面在動就一個鍵都不按(composer_history.py);
#   退不出來就照常往下 —— 守衛會判成 history,擋下時說原因與解法(不是「請對方清稿」)。
#   只有 claude 方言宣告了歷史框頂線;別家 composer_history 一定回 history=no,不按任何鍵。
# ★ 10-15 回補 #36 時改:先照原樣讀 BEFORE_E,畫面上真的有「History」字樣才去退、退完重讀 ——
#   平常一次都不多讀畫面(原寫法每封多讀一次,吃掉照順序吐畫面的假 tmux 一張,test-dm-auto-resend-159 等 9 格紅)。
if [ "${AIB_DM_SKIP_HISTORY_LEAVE:-0}" != "1" ] && [[ "$BEFORE_E" == *History* ]]; then
  _HIST_PROVIDER="${AIB_DM_TARGET_PROVIDER:-$("$TMUX_BIN" -L "$SOCKET" show-options -w -qv -t "$PANE" @aib_provider 2>/dev/null)}"
  _HIST_OUT="$("${EZ2_PY3:-python3}" "$(dirname "${BASH_SOURCE[0]}")/../../src/cockpit/composer_history.py" leave \
                 --socket "$SOCKET" --pane "$PANE" --provider "${_HIST_PROVIDER:-claude}" --tmux "$TMUX_BIN" 2>&1)"
  if [ "$_HIST_OUT" != "history=no" ]; then
    echo "aib-dm: 輸入歷史:${_HIST_OUT:-(沒有回應)}"
    BEFORE_E="$("$TMUX_BIN" -L "$SOCKET" capture-pane -e -p -t "$PANE" 2>/dev/null)"; _cap_rc=$?
    [ "$_cap_rc" = "0" ] || die 3 "讀不到目標畫面(capture-pane -e rc=$_cap_rc):**取不到畫面不等於畫面乾淨**,拒絕投遞。"
  fi
fi
BEFORE="$(printf '%s' "$BEFORE_E" | LC_ALL=C sed $'s/\033\[[0-9;]*m//g')"
# ★★ 刀 M+H(艦長 23:12 派、dev4 提):**BEFORE 已經被抓了,只是沒被交出來**——
#   守衛在下面 die 4 之前必須先 capture,而那份畫面只被引擎自己用掉,人接手時等於
#   從零重建判準(「介面沒把已有的資訊交給下游」的第三個實例)。
#   交出來之後一次關掉兩件:
#   (甲) 白名單條件一(「這一枚是我剛造成的」)從「人多做一次 capture」變成「讀一個欄位」;
#   (乙) **模態偵測不必存在**——模態永遠不會產生新的 `[Pasted text #N]`,所以
#        「AFTER 必須比 BEFORE 多一枚新佔位符」天然把模態擋在外面,不必認得它長什麼樣。
#   ★ 已知盲區照 dev5 23:11 寫進本體不放附註:**行數對得上是必要條件不是充分條件**
#     ——同一秒若另一席貼了同行數的稿進同一個框,行數會通過;真正夠強的形式是
#     守衛在按鍵前把**自己貼的內容**與框內容對起來,那在碼裡不在人的判斷裡。
# ★ 正則要看得見**無 `+K lines`** 的佔位符(root2 23:43 實測:單行長稿被貼成
#   `[Pasted text #83]`)。舊式只認帶 +K 的,於是**框裡有一枚舊佔位符卻回報「框是空的」**
#   ——而白名單條件一(AFTER 要有一枚 BEFORE 沒有的新佔位符)會把那枚舊的算成新的。
#   ★ 這正是模態排除論證所站的那條腿:**條件一的資料來源看不見一整類佔位符,那條腿就是軟的。**
#     今天救得下來的是合取(條件二 K+1 在單行佔位符上無法滿足)——**合取第二次救場,而這次是資料層。**
BEFORE_PH="$(printf '%s\n' "$BEFORE" \
  | grep -Eo -- '\[Pasted text #[0-9]+( \+[0-9]+ lines?)?\]|\[Pasted: [0-9]+ lines?\]|\[Pasted: [0-9]+ KB\]|\[Pasted: [0-9]+(\.[0-9]+)? MB\]|\[Paste #[0-9]+ - [0-9]+ lines?\]|\[Saved pasted content to workspace \([0-9]+(\.[0-9]+)? KB\) id=[0-9]+\]' | tr '\n' ' ')"
BEFORE_PH="${BEFORE_PH% }"
export AIB_DM_BEFORE_PLACEHOLDERS="$BEFORE_PH"
# ★ 一律印一行(不只在 die 4):成功路徑同樣需要它——下游要能分辨「這一枚是我
#   剛造成的」與「它本來就在」,而**成功那次正是最需要被追溯的那次**。
#   空值印 `(無)`=貼前沒有佔位符,與「沒查到」(dm-ctl 填 unknown)不同格。
# ★ 人讀行也要分得出「抓到了但空的」與「沒抓到」:`:-` 預設值兩者同形(root2 §三)。
#   抓到了→印清單或哨兵 NONE;沒抓到→這行根本不會被印(引擎已在更前面 die)。
echo "aib-dm: 貼前佔位符=${BEFORE_PH:+[$BEFORE_PH]}${BEFORE_PH:-NONE(抓到了,框內無佔位符)}"
# 3-18:身份提示是輸入區狀態,不是整幅捲軸的關鍵字搜尋。以最後一行為正本,
# 兼容舊提示的固定版位:提示本體在倒數第二行,最後一行是單獨提示符。
# 受限序列仍須符合 U+0042/U+0049/U+004E/U+0044 加 uuid/role/hex 外形才可晉升;
# 其餘三條既有提示走同一份 pattern 表。正文裡隔著普通輸出的相同字面不具狀態語意。
# 受限序列只在執行期由碼位組字。
# 3-18b:命中時交出提示區域與去 ANSI 後完整 capture 的 1 起算行號;
# 沒命中不印欄位,避免正文同字面被下游誤讀成提示狀態。
identity_prompt_matches() {
  case "$1" in
    *"BIND "*|*"請完整輸入以下文字"*|*"請輸入一張既有角色卡"*|*"operator acknowledgement"*) return 0 ;;
  esac
  return 1
}
printf -v _IDENTITY_MARKER '\x42\x49\x4e\x44'
_PROMPT_REGION="$(printf '%s\n' "$BEFORE" | tail -n 1)"
_PROMPT_CAPTURE_LINES="$(printf '%s\n' "$BEFORE" | awk 'END { print NR }')"
_IDENTITY_PROMPT_REGION="last_line"
_IDENTITY_PROMPT_LINE="$_PROMPT_CAPTURE_LINES"
case "$_PROMPT_REGION" in
  ">"|"> "|"❯"|"❯ ")
    _PROMPT_PREV="$(printf '%s\n' "$BEFORE" | tail -n 2 | head -n 1)"
    _PROMOTE_PROMPT_PREV=0
    case "$_PROMPT_PREV" in
      *"$_IDENTITY_MARKER "*)
        if [[ "$_PROMPT_PREV" =~ ^${_IDENTITY_MARKER}[[:space:]]+[[:alnum:]-]+[[:space:]]+[[:alnum:]_-]+[[:space:]]+[[:xdigit:]]+([[:space:]]+[[:xdigit:]]+)*$ ]]; then
          _PROMOTE_PROMPT_PREV=1
        fi
        ;;
      *)
        if identity_prompt_matches "$_PROMPT_PREV"; then
          _PROMOTE_PROMPT_PREV=1
        fi
        ;;
    esac
    if [ "$_PROMOTE_PROMPT_PREV" = "1" ]; then
      _PROMPT_REGION="$_PROMPT_PREV"$'\n'"$_PROMPT_REGION"
      _IDENTITY_PROMPT_REGION="previous_line"
      _IDENTITY_PROMPT_LINE="$((_PROMPT_CAPTURE_LINES - 1))"
    fi
    ;;
esac
if identity_prompt_matches "$_PROMPT_REGION"; then
  printf 'aib-dm: identity_prompt_region=%s identity_prompt_line=%s\n' \
    "$_IDENTITY_PROMPT_REGION" "$_IDENTITY_PROMPT_LINE"
  die 4 "目標正處於身份/同意提示,拒絕投遞(訊息會被當成 BIND 答案)。請先完成綁定。"
fi

# 4b. 貼前卡稿偵測(v3;卡稿復原三步第一步入引擎):composer 有滯留稿=
#     拒投+整幅畫面留檔——貼上+Enter 會把對方半寫的稿毀掉送出。
#     ★ 08-14 修(root3 實測,比上面那兩處更嚴重):原本是
#     `printf | tail -n 10 | grep -Eq`,同一個短路管線缺陷——但這裡的方向
#     是 **fail-open**:真的有滯留稿時 grep 比中即退出,tail 吃 SIGPIPE
#     (141),pipefail 把管線判非零,if 因此為假 → **守衛不觸發** → 稿被
#     貼上去再 Enter 送出,正是這道守衛存在的理由(@4 兩次毀稿實證)。
#     實測 1200 次有 3 次(0.25%)該觸發而沒觸發。同樣以先落值再比對根治。
# ★★ 08-18 the maintainer 直令改判準:守衛原本把**CLI 自己預載的下一步建議**當成滯留稿。
#   那不是殘影、不是壞掉,它一直都在——而畫面上與人打的字長得一模一樣。
#   實害:今天擋掉艦外五道令,還擋掉 the maintainer 一道 34 分鐘沒送到 dev2 的指令。
#   機器判準(the maintainer 用 `capture-pane -e` 親跑,兩端都驗):建議整段包在 ESC[2m(SGR 2 dim),
#   真打的字不帶 dim。→ dim=視同空框放行;非 dim=真稿才擋。
#   定不出輸入框(捲軸上只有舊回顯等)一律擋:看不清楚時不動別人的框(fail-closed)。
# ★★ 2026-08-22(a seat 釘 N1c、a seat 真投遞獨立指同一處;本段由 a seat 改,
#   而 tools/ 不在 SPEC-dm-auto-enter §七 指給我的檔裡——已明列回報,候裁)。
#   下游 dm-ctl 的自動送出需要「貼前那個框是不是空的」。**這件事本檔本來就算過了**
#   (下面那行 `_CS=`),只是沒有交出去——與「貼前佔位符本來就抓了只是沒交出來」
#   逐字同一個形狀,而那個形狀在本檔上面已經被記過一次。
#   ★ 為什麼非交不可:dm-ctl 原本用 `before_placeholders == []` 當「框是空的」,
#     而那個值只由**佔位符正則**產生——它看不見「別人打到一半的純文字」。
#     於是同一個值有兩個意思:「框是空的」與「框裡沒有佔位符」,而它讀的是強的那個。
#   ★ 計算移到旗標外面:SKIP=1 時**仍然算、仍然交**,只是不據以擋。
#     判準與處置分開——**關掉一道閘不該同時關掉那道閘的儀器**。
# ★★ 2026-08-22 02:4x rebase 解衝突(a seat,艦長 02:43 令「兩者不互斥」):
#   撞的兩半是兩席各自的判斷 —— a87ada4 管「引擎交出什麼」,本分支管「被擋時該說什麼」。
#   **兩半都保留**:`_CS` 仍在旗標外算、仍印 `貼前輸入框=`(a87ada4 的語意一字未動);
#   路徑抽成 `_CS_PY` 只是為了讓下面 draft 出口那次 `--reason` 呼叫共用同一條路徑,
#   不要在同一個檔裡出現第二份路徑字面。
_CS_PY="$(dirname "${BASH_SOURCE[0]}")/../../src/cockpit/composer_state.py"
_CS_DIALECT="$(dirname "${BASH_SOURCE[0]}")/../../src/cockpit/composer_dialect.py"
_DM_PASTE="$(dirname "${BASH_SOURCE[0]}")/../../src/messaging/dm_paste.py"
_CS_SEAT_PROVIDER="${AIB_DM_TARGET_PROVIDER:-}"
_CS_TMUX_PROVIDER="$("$TMUX_BIN" -L "$SOCKET" show-options -w -qv \
                       -t "$PANE" @aib_provider 2>/dev/null)"
_CS_PROVIDER_RESULT="$("${EZ2_PY3:-python3}" "$_CS_DIALECT" resolve-provider \
                        "$_CS_SEAT_PROVIDER" "$_CS_TMUX_PROVIDER" 2>/dev/null)"
_CS_PROVIDER_RC=$?
_CS_PROVIDER="$(printf '%s\n' "$_CS_PROVIDER_RESULT" | sed -n '1p')"
_CS_PROVIDER_SOURCE="$(printf '%s\n' "$_CS_PROVIDER_RESULT" | sed -n '2p')"
_CS_PROVIDER_DETAIL="$(printf '%s\n' "$_CS_PROVIDER_RESULT" | sed -n '3p')"
if [ "$_CS_PROVIDER_RC" != "0" ]; then
  [ -n "$_CS_PROVIDER_DETAIL" ] \
    || _CS_PROVIDER_DETAIL="席位檔=${_CS_SEAT_PROVIDER:-缺} tmux=${_CS_TMUX_PROVIDER:-缺}"
  die 4 "provider 來源不一致或不合法(${_CS_PROVIDER_DETAIL}),拒絕投遞。"
fi
echo "aib-dm: provider=${_CS_PROVIDER:-legacy} 來源=${_CS_PROVIDER_SOURCE:-legacy} (${_CS_PROVIDER_DETAIL})"
_CS_ARGS=(--stdin)
[ -n "$_CS_PROVIDER" ] && _CS_ARGS+=(--provider "$_CS_PROVIDER")
# R7(09-25 a seat):橋接席(方言宣告 cursor_line)只看游標那一行;量不到游標就不給,composer_state 行為不變。
_CS_CURSOR_Y="$("$TMUX_BIN" -L "$SOCKET" display-message -p -t "$PANE" '#{cursor_y}' 2>/dev/null)" || _CS_CURSOR_Y=""
case "$_CS_CURSOR_Y" in ''|*[!0-9]*) _CS_CURSOR_Y="" ;; esac
[ -z "$_CS_CURSOR_Y" ] || _CS_ARGS+=(--cursor-y "$_CS_CURSOR_Y")
_CS="$("${EZ2_PY3:-python3}" "$_CS_PY" "${_CS_ARGS[@]}" <<< "$BEFORE_E" 2>/dev/null)"
[ -n "$_CS" ] || _CS="unknown"
# 一律印一行(成功路徑同樣需要):unknown 與 empty 是兩件事,下游不得自己補預設。
echo "aib-dm: 貼前輸入框=$_CS"
if [ "${AIB_DM_SKIP_DRAFT_GUARD:-0}" != "1" ]; then
  case "$_CS" in
    suggestion|empty)
      : ;;                                   # CLI 建議或空框:放行(不是卡稿)
    n/a)
      # ★★ EH.16(艦長 12:2x 裁,a seat 審出):**不適用 ≠ 判不出來。**
      #   `n/a` 是 `composer_state` 明確回報「這個引擎沒有輸入框可判」——那是一個
      #   **答案**,不是一次失敗。處置=放行,與 empty 同側。
      #   ★ 沒有這一臂的話它會落 `*)`,而 `*)` 的訊息逐字說「判不出目標輸入框的狀態」
      #     ——**把一個明確的答案講成無法判斷**,正是 EH.16 這一葉要解的那件事本身,
      #     只是位置在消費端。
      #   ★ 今天摸不到:`--pane-command` 在 src/ 與 tools/ 的呼叫端是 0(2026-08-22 量)。
      #     **現在就加的理由不是它今天會發生,是讓下一個想開那個旗標的人看得見這個決定** ——
      #     留給 `*)` 的話,他不會知道有這件事要決定,而新態會在出生的當下被折回 unknown。
      echo "aib-dm: 貼前輸入框=n/a(該引擎不適用輸入框判定,非未知)—— 放行" >&2
      : ;;
    draft)
      _ts="$(date +%s)"
      _draft_file="$STATE_DIR/aib-dm-stuck-draft-${TARGET#@}-${_ts}.txt"
      printf '%s\n' "$BEFORE" > "$_draft_file" 2>/dev/null || _draft_file="(留檔失敗)"
      # ★★ 2026-08-22 艦長 01:06/01:29:**擋的動作對,說的話錯**,而錯的那句話
      #   正好誘導人去關掉這道守衛。`draft` 這個出口收兩種畫面:真草稿,以及
      #   模態選單(composer_state 的相容降級 MODAL→draft,那個降級是刻意的、
      #   不動它 —— 見該檔 _main:新增一態不得靠消費端同步更新才安全)。
      #   模態沒有稿可清,送件人 capture 一看是選單,會合理判定這是誤判,
      #   然後照本檔文件化的逃生口 AIB_DM_SKIP_DRAFT_GUARD=1 繞過去 ——
      #   **貼進一個正在問「要不要執行危險 rm」的確認框。**
      # ★ 那句正確的話早就寫好了(composer_state.send_block_reason),
      #   只是生產路徑從來沒帶 --reason,所以它從沒上過畫面。
      #   閘門三段的第三段:存在 → 被經過 → 響了有人聽。它響了,說了錯的話。
      # ★ 兩態都向它要,不是只有模態(艦長 01:29 裁):只改模態的話,
      #   DRAFT 就變成**已知有第二真相源而刻意留著**,下一個讀的人會以為那是設計。
      # ★ 界線不變(艦長 23:12):第三態只准改理由,不准改准不准按。
      #   MODAL 照樣走這個出口、照樣 die 4,零 send-keys。
      _reason_state="$("${EZ2_PY3:-python3}" "$_CS_PY" "${_CS_ARGS[@]}" --modal-aware \
                         <<< "$BEFORE_E" 2>/dev/null)"
      # ★ 第 55 項:機器可讀的擋下種類(draft/modal/browse),給 dm-ctl 分流;散文那句仍只住在 send_block_reason。
      echo "aib-dm: block_kind=${_reason_state:-unknown}"
      # 理由完整字面只住在 composer_state.send_block_reason。這裡傳入上面從
      # 同一畫面判出的 modal-aware state；不得在 shell 另組 draft/modal 句。
      _reason="$("${EZ2_PY3:-python3}" -c 'import sys
sys.path.insert(0, sys.argv[1])
import composer_state as cs
print(cs.send_block_reason(sys.argv[2]) or "")' \
                  "$(dirname "$_CS_PY")" "$_reason_state" 2>/dev/null)"
      # 取不到就說取不到 —— 不要退回任何一態的字面當預設。**猜錯的那一句
      # 正是這次要修的東西**;而它仍然 die 4,失效方向不變。
      [ -n "$_reason" ] || _reason="目標輸入框不是空的,而本次取不到它是哪一種(草稿/模態)。拒絕投遞。"
      # ★ `${...}` 大括號是必要的不是風格:`$_draft_file。` 會讓 bash 把全形句號的
      #   第一個位元組吃進變數名(實測 `_draft_file<0xE3>: 未綁定的變數`,set -u 直接死)。
      #   舊行後面接的是 ASCII `;` 所以沒事 —— **換一個字就開火,而它是我改出來的。**
      die 4 "${_reason} 拒絕投遞。畫面已留檔:${_draft_file}。貼前佔位符=[${BEFORE_PH:-(無)}]" ;;
    *)
      # 08-29 艦長改裁:unknown 的處置取決於 provider 能否識別它的 composer。
      # copilot/agy/codex 判不出來就 fail-closed;舊 claude/legacy 路徑維持兩層契約:
      # 先跑樣式守衛,樣式命中仍擋真稿,沒命中才放行。明示 strict 時全部擋下。
      _AIB_DM_FALLBACK_GUARD=1
      _AIB_DM_STATE_UNKNOWN="$_CS"
      case "${_CS_PROVIDER:-legacy}" in
        copilot|agy|codex) _AIB_DM_UNKNOWN_FAIL_CLOSED=1 ;;
        *) _AIB_DM_UNKNOWN_FAIL_CLOSED="${AIB_DM_STRICT_UNKNOWN:-0}" ;;
      esac
      ;;
  esac
fi
if [ "${_AIB_DM_FALLBACK_GUARD:-0}" = "1" ] && [ "${AIB_DM_SKIP_DRAFT_GUARD:-0}" != "1" ]; then
  _before_tail="$(printf '%s\n' "$BEFORE" | tail -n 10)"
  # `--` 同上:DRAFT_RE 可由 AIB_DM_DRAFT_RE 覆寫,以 `-` 開頭的樣式會被
  # grep 當選項吃掉(rc=2+usage 噴 stderr),而**這道守衛的失效方向是毀稿**。
  if grep -Eq -- "$DRAFT_RE" <<<"$_before_tail"; then
    _ts="$(date +%s)"
    _draft_file="$STATE_DIR/aib-dm-stuck-draft-${TARGET#@}-${_ts}.txt"
    printf '%s\n' "$BEFORE" > "$_draft_file" 2>/dev/null || _draft_file="(留檔失敗)"
    die 4 "目標輸入框疑有滯留稿,拒絕投遞(裸 Enter 會毀稿)。請對方清稿後重送。畫面已留檔:$_draft_file;請人工 capture 確認。貼前佔位符=[${BEFORE_PH:-(無)}]"
  fi
  if [ "${_AIB_DM_UNKNOWN_FAIL_CLOSED:-0}" = "1" ]; then
    _ts="$(date +%s)"
    _draft_file="$STATE_DIR/aib-dm-unknown-state-${TARGET#@}-${_ts}.txt"
    printf '%s\n' "$BEFORE" > "$_draft_file" 2>/dev/null || _draft_file="(留檔失敗)"
    die 4 "判不出目標輸入框的狀態(composer_state 回 '${_AIB_DM_STATE_UNKNOWN:-空}'),拒絕投遞——**判不出來不等於安全**。畫面已留檔:$_draft_file;請人工 capture 確認。"
  fi
fi

# 5. v3 通道:load-buffer(暫存檔,零轉義面)→ paste-buffer -p(bracketed
#    paste,TUI 不把內容當快鍵)。真 rc 逐段驗;絕不 send-keys -l。
# ★ 與上面同源:這裡原本重複推導 `${AIB_DM_STATE_DIR:-${TMPDIR:-/tmp}}`,
#   於是上面補了、這裡沒補,而**真阻斷在這一行**(die 5=信送不出)。
#   兩處各寫一份的判斷,今天等值、改一邊就分岔。
[ -n "$STATE_DIR" ] || die 5 "找不到可寫的暫存目錄(試過 TMPDIR/private/tmp、/tmp)"
TMPF="$(mktemp "$STATE_DIR/aib-dm.XXXXXX")" \
  || die 5 "mktemp 失敗於 $STATE_DIR"
# ★ 2026-09-02 現量:grok TUI 把詞首 U+0040 當檔案提及,開 fuzzy picker,
#   Enter 選中反白檔、整份稿被吃掉。只對 grok 盾;失效時下一腿(貼後選單偵測)
#   仍會停手。盾失敗不准繼續貼——安靜貼進去就是本缺陷本身。
# ★ 盾後畫面現查把 U+200B 吃掉(框內仍顯示詞首 U+0040、選單不開),
#   所以指紋仍用原文 MSG,只把暫存檔換成盾後字串。兩者對調=短稿逐字路徑假紅。
# ★ 大小理智檢查(2026-09-02):81KB 是未引號 heredoc 執行反引號灌出來的,
#   約 30 倍正常工單,投遞層一聲不吭照貼。預設 65536 位元組;0=關。
#   超過具名擋 rc=4(還沒貼,但要人把稿縮掉,不當「可安全原樣重送」)。
_max_bytes="${AIB_DM_MAX_BYTES:-65536}"
_nbytes="$(printf '%s' "$MSG" | wc -c | tr -d '[:space:]')"
if [ "$_max_bytes" != "0" ]; then
  case "$_max_bytes" in *[!0-9]*) die 4 "AIB_DM_MAX_BYTES 不是數字:$_max_bytes" ;; esac
  if [ "$_nbytes" -gt "$_max_bytes" ]; then
    die 4 "稿 ${_nbytes} 位元組超過理智上限 ${_max_bytes}(AIB_DM_MAX_BYTES):不貼。"
  fi
fi
_PASTE_MSG="$MSG"
if [ "${_CS_PROVIDER:-}" = "grok" ]; then
  _PASTE_MSG="$(printf '%s' "$MSG" | "${EZ2_PY3:-python3}" "$_DM_PASTE" shield-at)" \
    || { rm -f "$TMPF"; die 4 "grok at-shield 失敗:不敢貼,選單可能吃掉整份稿"; }
fi
printf '%s' "$_PASTE_MSG" > "$TMPF" || { rm -f "$TMPF"; die 5 "訊息暫存失敗"; }
if ! "$TMUX_BIN" -L "$SOCKET" load-buffer -b aibdm "$TMPF" 2>/dev/null; then
  rm -f "$TMPF"
  die 5 "load-buffer 失敗"
fi
rm -f "$TMPF"
"$TMUX_BIN" -L "$SOCKET" paste-buffer -p -d -b aibdm -t "$PANE" 2>/dev/null \
  || die 5 "paste-buffer 失敗"
# ★ 10-01(the maintainer 令;a seat copilot 長稿現量):貼稿這件事**已經發生**,要交給下游。
#   下游 dm-ctl 的「非 0/5 出口」原本只看佔位符殘留與兩幀是否相同,就給「目標端零新增、
#   可安全再投」的承諾;而本檔在貼之後還有 die 4 的出口(檔案提及選單)。貼過就不准再給那個承諾。
#   dm-ctl 讀這一行(字面由它的尺釘住):有這一行 ⇒ 該出口一律落 4。
echo "aib-dm: 已貼入目標(paste-buffer rc=0)"

# 6. 傳輸驗證:Enter 之前先回讀,確認字真的落進輸入區。
#    2026-08-19 修誤報(刀五;pm2 五筆+艦外六封實測落地為機器判準)。
#    舊版只認「頭 24 非空白字元在畫面」,對三種真送達必假紅:
#    (a) 摺疊型:CLI 把多行稿摺成 [Pasted text #N +K lines],字面**永遠**不在
#        畫面上——結構性 100% 誤報。判準=K+1==稿件行數(pm2 五筆全中),
#        且該佔位符須為**貼後新出現**(BEFORE 沒有)——防捲軸上舊佔位符恰好同行數。
#        行數不符照樣 die,不放寬:那正是「別人的稿在框」該紅的形狀。
#    (b) 窄框型:長稿在輸入框只顯尾段,頭指紋被捲出可見區——補**尾 24** 指紋。
#    (c) 渲染競態型:長稿貼後 TUI 未渲染完就回讀會撲空(00:24 @1 實證)——
#        有界重試 3 次(0.3s 間隔);重試只重抓畫面,絕不重貼(重貼=疊稿兩份)。
#    ★ 框空真失敗必須仍 die(本守衛今晚救過一次真失敗);修法是修判準,不是關守衛。
#    ★ rc 維持 5 不拆(艦長 00:25 裁):dm-ctl 對映是刀四寫入面,引擎先出新碼=
#      半套遷移漏裸 rc。「無跡象(疑零字進入)/佔位符行數不符(稿疑在框,勿重貼)」
#      兩型拆 rc 6/7 的完整遷移案見交付書面,下一批單一寫入者一刀完成。
# ★★ 2026-08-19 刀 M:指紋只取**正文**,不取自動抬頭。
#   為什麼(root2 診斷,取代「暱稱」版,涵蓋更廣):**前 24 字是一個預算,
#   抬頭花掉多少、時戳就剩多少機會擠進去**;而預算消耗量同時取決於送件席與
#   收件席兩邊的標籤長度——**同一席對不同對象暴露與否可能不同,新增一席、
#   改一次角色名、加一個暱稱,都可能讓原本安全的一對變成暴露**。
#   實測:`[a seat(專案經理)>a seat(架構總` 前 24 字對同一收件席**每封恆等**
#   → 從第二封起 head 指紋必已在對方捲動區 → 逐字路徑恆真 → rc=0 而稿可能沒進框。
#   ★★ 判準(root2):**當安全性取決於一個沒有人看得見的預算時,唯一的修法
#   是不要用那個預算**——故不是「縮短抬頭」而是「指紋不碰抬頭」。
#   邊界由**產生抬頭的人**(dm-ctl)以 AIB_DM_BODY_OFFSET 明示(字元數,結構
#   非常數;艦長 04:44 裁(甲))。引擎不自己解析抬頭格式:那等於把一個會變的
#   格式複製到第二個地方,而本檔 :193-195 就有同型的疤(STATE_DIR 兩處各寫一份)。
#   拿不到 offset → **fail-closed 降級**:整串取指紋並在收據標 literal(degraded),
#   不靜默沿用舊行為。
# ★ locale(dev2):`cut -c` 與 `${var:0:N}` 的字元/位元組語意隨 locale 變
#   (C locale 下 24 ≈ 8 個漢字),而 cron/CI/非互動 shell 常見 C locale。
#   故本段**釘死在 UTF-8 下量**,取不到 UTF-8 locale 時記錄實際使用值。
# ★ 10-04(雲端實踩):en_US.UTF-8 不是每台都有(Linux 雲端只有 C.UTF-8)。設不進去時 bash 只印警告、
#   照舊用位元組切 ⇒ 中文指紋切在字元中間、回讀比不到 ⇒ 長稿誤判。改:依序試 AIB_DM_FP_LOCALE、en_US.UTF-8、
#   C.UTF-8、C.utf8,取第一個「一個漢字長度 = 1」的;不叫外部程式(locale -a),不改呼叫端的 LC_ALL。
#   ★ 兩處同一套挑法(dm-ctl.sh 算 offset、aib-dm.sh 取指紋),挑出來的必須一樣;改一處要改另一處。
_aib_pick_utf8_lc() {
  local _c _s='中' _sv="${LC_ALL-__aib_unset__}" _pick=""
  for _c in "${AIB_DM_FP_LOCALE:-}" en_US.UTF-8 C.UTF-8 C.utf8; do
    [ -n "$_c" ] || continue
    LC_ALL=C   # ★ 先回 C:設不進去的 locale 會讓 bash 留在前一個,若前一個已是 UTF-8 就誤判(雲端實踩)
    { LC_ALL="$_c"; } 2>/dev/null
    if [ "${#_s}" = 1 ]; then _pick="$_c"; break; fi
  done
  if [ "$_sv" = "__aib_unset__" ]; then unset LC_ALL; else LC_ALL="$_sv"; fi
  printf '%s' "${_pick:-${AIB_DM_FP_LOCALE:-en_US.UTF-8}}"
}
_fp_lc="$(_aib_pick_utf8_lc)"
_body_offset="${AIB_DM_BODY_OFFSET:-}"
_fp_degraded=""
# ★ locale 必須釘在**參數展開發生的那一刻**:`LC_ALL=x printf '%s' "${v:0:24}"`
#   釘不住——`${v:0:24}` 在指令執行前就由**當前** shell 依當前 locale 切好了,
#   賦值前綴只影響 printf 自己的環境。故改成先設殼層 LC_ALL、切完再還原。
#   (dev1 量化:UTF-8 下 24=24 字元;LC_ALL=C 下 24=24 位元組≈9 個漢字。)
_fp_lc_saved="${LC_ALL-__aib_unset__}"
LC_ALL="$_fp_lc"
case "$_body_offset" in
  ''|*[!0-9]*) _fp_degraded="degraded"; _fp_src="$MSG" ;;
  *)
    # offset=「抬頭在原始 MSG 裡佔的字元數」;先切原文再去空白,順序不可反
    # (去空白後位移就對不上原文了)。
    _fp_src="${MSG:$_body_offset}"
    [ -n "$(printf '%s' "$_fp_src" | tr -d '[:space:]')" ] \
      || { _fp_degraded="degraded"; _fp_src="$MSG"; }   # 正文空=退回整串,不留空指紋
    ;;
esac
# ★ 10-04:全形空白 U+3000 —— BSD tr(Mac)在 UTF-8 下會吃掉它(iswspace),GNU tr(Linux)是位元組工具、不吃;
#   python 側(dm_paste.head_fingerprint)會吃。故 tr 之後再明刪 U+3000,三處(稿、貼前、貼後)同一套:
#   Mac 上是空操作,Linux 上與 Mac、python 對齊。(test-dm-fingerprint-parity-9a2be 全形格釘這件事)
_stripped="$(printf '%s' "$_fp_src" | tr -d '[:space:]')"
_stripped="${_stripped//　/}"
probe_head="${_stripped:0:24}"
# 尾指紋用 bash 原生 ${var: -24}(UTF-8 locale 按**字元**切)。
# 位元組切法(LC_ALL=C awk substr)會把中文切在字元中間;但 bash 的 case 走
# fnmatch=**位元組**匹配,壞序列仍是合法子字串,而本引擎全鏈釘死 bash
# (shebang+dm-ctl `bash $THROAT`),故位元組切在現行實路徑**不會發作**
# (zsh 才是字元匹配、比不到——量它要用引擎的殼,不是你的互動殼)。
# 改字元切的理由是**跨殼防禦+指紋人類可讀**,不是在修活著的 bug;
# bash 下無判別性突變可讓它轉紅,四對齊測試屬回歸甲冑(root2 判準調整採認)。
if [ "${#_stripped}" -gt 24 ]; then
  probe_tail="$(LC_ALL="$_fp_lc" printf '%s' "${_stripped: -24}")"
else
  probe_tail="$_stripped"   # 短稿:整串即指紋(空字串指紋=恆真,絕不可)
fi
# locale 還原:指紋切完就交還,不讓本段的釘子外溢到後面的比對與輸出。
if [ "$_fp_lc_saved" = "__aib_unset__" ]; then unset LC_ALL; else LC_ALL="$_fp_lc_saved"; fi
# 空指紋恆真=最壞的假綠;兩個指紋都空就不該有「已驗證」這回事。
# ★ 誠實聲明:此 die **目前不可達**——offset 過長/正文為空都已在上面退回整串,
#   而空白訊息更早就被 die 2 擋掉。故**無判別性突變可得**(殺掉它 16 支測試全綠),
#   它是防禦性冗餘,不宣稱已測(先例:刀五四對齊測試的回歸甲冑判準)。
[ -n "$probe_head" ] || die 5 "傳輸未通過驗證:指紋為空(正文去空白後無字元),拒絕宣稱已驗證。"
BEFORE_NS="$(printf '%s' "$BEFORE" | tr -d '[:space:]')"
BEFORE_NS="${BEFORE_NS//　/}"   # U+3000(見 _stripped 那段)
# ★ 10-01(a seat copilot 現量):畫框型輸入框(copilot `┃ `、grok `│ … │`)把一行長稿折成
#   好幾列,**每一列前面都多一個框線字**。去空白之後框線字夾在稿的中間,24 字的頭/尾指紋
#   只要跨過折行點就比不到 ⇒ 稿明明整段在框裡,引擎卻說「回讀無任何跡象」。
#   框線字從畫面那一側剝掉(貼前貼後同一套,BEFORE 閘因此只會更嚴不會更鬆);
#   指紋本身不動(與 dm_paste.head_fingerprint 的對照尺照舊逐字一致)。
#   ★ R16 m2:只在長稿(≥1KB)剝;短稿逐字是舊行為(剝框線那行搬到 _long_paste 算出來之後)。
# ★★ 10-01 長稿落地等待(the maintainer 令;a seat copilot+Kimi K3 現量,3060 與 7932 位元組單行稿):
#   對方 CLI 吃一大段 bracketed paste 要時間。舊碼固定回讀 3 次(間隔 0.3 秒),
#   8KB 的稿還沒畫完就判「無跡象」⇒ 不按 Enter、轉下游(3KB 那封卡在框裡轉待人處理,
#   8KB 那封被改排暫存、6 分鐘後又貼一次)。
#   預算 = 基本 3 次 + 每 KB 加 AIB_DM_LAND_PER_KB_TRIES 次(預設 2),上限 AIB_DM_LAND_MAX_TRIES(預設 60);
#   間隔 AIB_DM_LAND_POLL_S(預設 0.3)。**1KB 以下的稿逐字是舊行為**(3 次、0.3 秒)。
#   重試只重抓畫面,絕不重貼(重貼=疊稿兩份)——這一條不因預算變大而改變。
_land_kb=$(( ${_nbytes:-0} / 1024 ))
_land_poll="${AIB_DM_LAND_POLL_S:-0.3}"
case "$_land_poll" in ''|*[!0-9.]*) _land_poll=0.3 ;; esac
_land_per_kb="${AIB_DM_LAND_PER_KB_TRIES:-2}"
case "$_land_per_kb" in ''|*[!0-9]*) _land_per_kb=2 ;; esac
_land_cap="${AIB_DM_LAND_MAX_TRIES:-60}"
case "$_land_cap" in ''|*[!0-9]*) _land_cap=60 ;; esac
_max_try=$(( 3 + _land_kb * _land_per_kb ))
[ "$_max_try" -gt "$_land_cap" ] && _max_try="$_land_cap"
[ "$_max_try" -lt 3 ] && _max_try=3
_long_paste=0
[ "$_land_kb" -ge 1 ] && _long_paste=1
if [ "$_long_paste" = "1" ]; then
  BEFORE_NS="${BEFORE_NS//┃/}"; BEFORE_NS="${BEFORE_NS//│/}"   # 框線字(見上面 BEFORE_NS 那段;只給長稿)
fi
_head_only_seen=""
_msg_lines="$(printf '%s\n' "$MSG" | wc -l | tr -d '[:space:]')"
_verified="" ; _fold_mismatch="" ; _queued=""
_try=0
while [ "$_try" -lt "$_max_try" ]; do
  _try=$((_try+1))
  AFTER_RAW="$("$TMUX_BIN" -L "$SOCKET" capture-pane -p -t "$PANE" 2>/dev/null)"; _cap_rc=$?
  # 同上:驗證階段讀不到畫面 → 不得當成「沒跡象」,更不得送 Enter。
  [ "$_cap_rc" = "0" ] || die 5 "讀不到目標畫面(驗證階段 capture-pane rc=$_cap_rc):無法判別,已中止且不送 Enter。"
  # ★ grok 檔案提及選單(現量計數行 ─N/M─ 下一行 ❯ 路徑)。必須在摺疊/指紋
  #   判定之前:選單開著時佔位符仍可能出現 → 舊判準會送 Enter=選中檔案。
  #   不對目標送 Escape(idle 關選單、busy 會取消對方那一輪)。
  if "${EZ2_PY3:-python3}" "$_DM_PASTE" looks-mention-picker <<<"$AFTER_RAW"; then
    die 4 "貼後畫面是 grok 檔案提及選單:稿可能已被選單吃掉,不送 Enter,不得宣稱送到"
  fi
  AFTER="$(printf '%s' "$AFTER_RAW" | tr -d '[:space:]')"
  AFTER="${AFTER//　/}"   # U+3000(見 _stripped 那段)
  if [ "$_long_paste" = "1" ]; then
    AFTER="${AFTER//┃/}"; AFTER="${AFTER//│/}"   # 框線字(見 BEFORE_NS 那段;R16 m2 只給長稿)
  fi
  # ★★ 逐字路徑的 BEFORE 閘(刀 M 主修;dev1 發現、pm 獨立驗證)。
  #   舊版直接 `case "$AFTER" in *probe*) _verified=literal`,**零 BEFORE 對照**,
  #   而摺疊路徑早有 `case "$BEFORE" in *"$_ph"*) : ;;`——**同一支檔兩條路徑
  #   判準不對稱**。發作形狀是**假綠**:probe 若在貼之前就已在畫面(我重送同
  #   一句、對方引述了我的句子),**稿根本沒進框也判已驗證、回 rc=0 宣稱送達**。
  #   閘的語意與摺疊路徑同源:**只有「貼後新出現」的跡象才算數**。
  # ★ 退化預告(艦長 04:39 H1):補閘之後,凡指紋曾出現在對方捲動區的情況
  #   逐字路徑就不再成立,實際只剩摺疊路徑與尾指紋——**那是對的,不是誤殺**。
  _lit=""
  case "$AFTER" in *"$probe_head"*) _lit="head" ;; esac
  [ -z "$_lit" ] && case "$AFTER" in *"$probe_tail"*) _lit="tail" ;; esac
  # ★ 10-01 長稿:頭到了不代表整段到了 —— 對方還在吃 paste 的時候,畫面上先出現的就是頭。
  #   這時按 Enter,Enter 會跟還沒吃完的稿擠在一起(a seat 3KB 那封:稿整段在框裡,Enter 沒送出)。
  #   所以長稿(≥1KB)要**尾**也到了(貼後新出現)才算落地;只見頭就繼續等,
  #   等到預算最後一次仍只有頭,才照舊行為以頭為準(不比從前更不肯送)。短稿逐字是舊行為。
  if [ "$_long_paste" = "1" ] && [ "$_lit" = "head" ]; then
    case "$AFTER" in
      *"$probe_tail"*)
        case "$BEFORE_NS" in *"$probe_tail"*) : ;; *) _lit="head" ;; esac ;;
      *)
        if [ "$_try" -lt "$_max_try" ]; then
          case "$BEFORE_NS" in *"$probe_head"*) : ;; *) _head_only_seen=1; _lit="" ;; esac
        fi ;;
    esac
  fi
  if [ -n "$_lit" ]; then
    _before_has=""
    case "$_lit" in
      head) case "$BEFORE_NS" in *"$probe_head"*) _before_has=1 ;; esac ;;
      tail) case "$BEFORE_NS" in *"$probe_tail"*) _before_has=1 ;; esac ;;
    esac
    if [ -z "$_before_has" ]; then
      _verified="literal(${_lit}${_fp_degraded:+,$_fp_degraded})"; break
    fi
    _lit_stale="貼前畫面已含${_lit}指紋"   # 不作數,續試摺疊路徑
  fi
  # 摺疊佔位符:取畫面**最下**一枚(最靠輸入框);grep 一律 `--`(0q)。
  # ★★ 白名單是**合取,兩個條件缺一不可**(艦長 23:18 定版措辭):
  #   (1) AFTER 有一枚 BEFORE 沒有的**新**佔位符,且 (2) K+1 == 送出檔案行數。
  #   ✔ 正確說法是後果式:「**必須正面認出那是我的稿,而模態認不出來**」;
  #   ✘ 不可寫成前提式「模態不會產生佔位符所以安全」——那需要一個我們證不了的
  #     全稱否定(root2 掃 330 份留檔:模態 11/佔位符 32/交集 0,但 11 份全是
  #     同一種模態,對「任何模態」幾乎沒有增加資訊)。
  #   ★ 兩個條件各自都不夠且失效方式不同,故不能互相替代:
  #     條件二(行數)必要非充分——同一秒另一席貼同行數的稿會過(dev5 23:11);
  #     條件一(新佔位符)在 capture 失敗時會自己成立(故上面 rc 獨立取即 die)。
  #   ★ 今天沒有後果;有後果的是**有人日後為了簡化把條件二拿掉**的那天。
  _ph="$(printf '%s\n' "$AFTER_RAW" \
    | grep -Eo -- '\[Pasted text #[0-9]+ \+[0-9]+ lines?\]|\[Pasted: [0-9]+ lines?\]|\[Pasted: [0-9]+ KB\]|\[Pasted: [0-9]+(\.[0-9]+)? MB\]|\[Paste #[0-9]+ - [0-9]+ lines?\]|\[Saved pasted content to workspace \([0-9]+(\.[0-9]+)? KB\) id=[0-9]+\]' | tail -n 1)"
  if [ -n "$_ph" ]; then
    case "$BEFORE" in
      *"$_ph"*) : ;;  # 貼前已在畫面=舊佔位符,不作數(fail-closed)
      *)
        # ★★ 2026-09-02(現量席 eeb46 交長稿實測時抓到):K+1 是 **claude 家族**
        #   的算法,而這段對**任何**字面都跑。grok 的 `[Pasted: 38 lines]` 與
        #   copilot 的 `[Paste #7 - 12 lines]` **裡面沒有 `+`**,於是
        #   `${_ph##*+}` 取不到東西、整串被留下,`_k` 變成 `[Pasted:`,
        #   `$(( ))` 當場語法錯(被 2>/dev/null 吃掉),流程掉進 else,
        #   訊息長成「佔位符 +[Pasted: 行 vs 稿件 38 行」。
        #   ★ 今天沒有後果,因為 dm-ctl 的 verify-fold 方言覆寫會把它救回來 ——
        #     **有後果的是有人日後把那個覆寫拿掉的那天**,
        #     而那天這裡會給出一句看起來像診斷、其實是亂碼的理由。
        #   ⇒ 只在字面**真的是 `+K` 那一式**時才做這個算術;
        #     其餘方言具名交給下游方言驗證,不在這裡瞎猜。
        case "$_ph" in
          *"+"*)
            _k="${_ph##*+}"; _k="${_k%% *}"
            case "$_k" in
              ''|*[!0-9]*) _fold_mismatch="佔位符取不到行數(字面=${_ph})" ;;
              *)
                if [ "$((_k + 1))" -eq "$_msg_lines" ]; then
                  _verified="fold(+${_k} lines)"; break
                else
                  _fold_mismatch="佔位符 +${_k} 行 vs 稿件 ${_msg_lines} 行"
                fi
                ;;
            esac
            ;;
          *)
            # 非 claude 家族字面:本層不判,交給 dm-ctl 的方言 verify-fold。
            _fold_mismatch="非 K+1 方言字面(${_ph}),本層不判,交方言驗證"
            ;;
        esac
        ;;
    esac
  fi
  # ★ 第三態:佇列態(艦長 22:55 觀測 + 22:5x 我方實地遭遇)。目標忙碌時稿進
  #   佇列,**摺疊佔位符不會出現在框上**,字面也不在——舊碼於是掉回逐字路徑而
  #   逐字路徑沒有 BEFORE 閘,所以「一定會綠」。三個必須分辨的狀態被壓成 rc=0
  #   一個值:(a) 摺疊態=可驗 (b) 框空=真沒送到 (c) 佇列態=送到了但驗不到。
  # ★★ 本刀**只偵測、不放寬**:判決仍是未驗證(die 5),因為升級成「可驗」的
  #   依據目前只有單一觀測(艦長自標);但**指引必須分開**——(b) 的指引是
  #   「確認框空後才重送」,對佇列態照做會**重送成兩份**。
  #   偵測同摺疊路徑紀律:必須是**貼後新出現**(BEFORE 沒有),否則那是別人先前排的隊。
  case "$AFTER_RAW" in
    *"Press up to edit queued messages"*)
      case "$BEFORE" in
        *"Press up to edit queued messages"*) : ;;   # 貼前就有=別人的佇列,不作數
        *) _queued=1 ;;
      esac ;;
  esac
  [ "$_try" -lt "$_max_try" ] && sleep "$_land_poll"
done
if [ -z "$_verified" ]; then
  if [ -n "$_fold_mismatch" ]; then
    die 5 "傳輸未通過驗證:摺疊佔位符行數不符(${_fold_mismatch})——框內狀態不明,**勿重貼**(疊稿風險);請 capture 目標框人工驗稿。"
  fi
  if [ -n "$_queued" ]; then
    die 5 "傳輸未通過驗證:**疑似佇列態**(框出現「Press up to edit queued messages」且貼前沒有)——對方忙碌,稿可能已排入佇列;**未驗證但勿重送**(重送會變兩份),請 capture 或請對方回報。"
  fi
  die 5 "傳輸未通過驗證:回讀無任何跡象(頭/尾指紋與新佔位符皆未出現),已中止且不送 Enter;請 capture 確認框空後才重送。"
fi

# 6b. 長稿(≥1KB)落地後先等畫面靜下來才送 Enter(10-01 the maintainer 令;a seat 3KB 現量)。
#   看見尾指紋 = 稿的最後一段已經畫上去;但對方 CLI 可能還在處理 paste 收尾(重排、捲動框),
#   這時 Enter 會被當成稿的一部分吃掉。判準:連續兩次回讀**逐字相同**才按。
#   上限 2+每 KB 一次(最多 AIB_DM_LAND_SETTLE_MAX,預設 20);到上限仍在動(對方正在輸出)就照舊按
#   —— 不比從前更不肯送,只是不再在稿還在流進去的那一刻按。1KB 以下不跑這段(逐字舊行為)。
if [ "$_long_paste" = "1" ]; then
  _settle_cap="${AIB_DM_LAND_SETTLE_MAX:-20}"
  case "$_settle_cap" in ''|*[!0-9]*) _settle_cap=20 ;; esac
  _settle_max=$(( 2 + _land_kb ))
  [ "$_settle_max" -gt "$_settle_cap" ] && _settle_max="$_settle_cap"
  _settle_n=0; _settle_ok=""; _settle_prev="$AFTER_RAW"
  while [ "$_settle_n" -lt "$_settle_max" ]; do
    _settle_n=$((_settle_n+1))
    sleep "$_land_poll"
    _settle_cur="$("$TMUX_BIN" -L "$SOCKET" capture-pane -p -t "$PANE" 2>/dev/null)"; _cap_rc=$?
    [ "$_cap_rc" = "0" ] || die 5 "讀不到目標畫面(落地靜止檢查 capture-pane rc=$_cap_rc):稿已在框內,不送 Enter;**勿重貼**,請 capture 人工確認。"
    if [ "$_settle_cur" = "$_settle_prev" ]; then _settle_ok=1; break; fi
    _settle_prev="$_settle_cur"
  done
  if [ -n "$_settle_ok" ]; then
    echo "aib-dm: 長稿落地(${_land_kb}KB):第 ${_try} 次回讀見到落字、畫面靜止後才送 Enter"
  else
    echo "aib-dm: 長稿落地(${_land_kb}KB):畫面 ${_settle_max} 次回讀仍在變(對方可能正在輸出),照舊送 Enter"
  fi
fi

# 7. 確認落字後才送 Enter(獨立按鍵,與貼稿分離)
"$TMUX_BIN" -L "$SOCKET" send-keys -t "$PANE" Enter 2>/dev/null \
  || die 5 "send-keys Enter 失敗(字面內容已落入輸入區,對方可能需手動送出)"

echo "aib-dm: 傳輸已驗證[${_verified}] -> $SOCKET:$TARGET(${#MSG} 字元)"
echo "aib-dm: 本收據只證明按鍵落入目標 pane,不證明對方已讀或已執行。"
