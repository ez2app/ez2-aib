#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 滑鼠互動(E2.13):點底部分頁切換、點 AI 列跳中欄。
#
#   mouse-ctl.sh --install [--dry-run]        綁 tmux 滑鼠鍵
#   mouse-ctl.sh --click <role> <x> <y> [session] [--dry-run]   處理一次點擊
#   mouse-ctl.sh --context <surface> <role> <x> <y> <pane> <session> [--dry-run]
#   mouse-ctl.sh --role right --close-row ai-list <x> <y> [session] [--dry-run]
#                                                  右鍵「關閉這一席」:彈 [X] 同一個確認選單
#   mouse-ctl.sh --release-confirmed <window> [session] [--dry-run]
#                                                  [X] 確認選單按「確定」後的真殺
#   ⚠ --dry-run 對釋放路徑的診斷力是**零**:DRY 全程不碰真 tmux(治具以
#     「有沒有呼叫 tmux」當絆線),因此**跳過殺前重驗探針**、也不查 pane
#     幾何。要驗重驗邏輯請直接跑 activity_probe.py 或走非 dry 沙箱,
#     別拿 --dry-run 的綠當作重驗有效(第三艦長 08-14 被它誤導過一次)。
#   mouse-ctl.sh --perf-close-confirmed <id> [--dry-run]   效能頁 [X] 確認後單關
#   mouse-ctl.sh --perf-reap-confirmed [--dry-run]          效能頁聚合列確認後全關
#   mouse-ctl.sh --open-window <window> <session> [--dry-run]
#   mouse-ctl.sh --bind-menu <kind> <seat> <arg> <pane> <x> <y> [--dry-run]
#                                                  L2-03 綁定人格/大腦的回呼式選單層
#     kind: persona(分類層)/persona-cat(卡層,arg=分類)/persona-do(綁,arg=uid)
#           brain(模組層)/brain-mod(路由層,arg=模組)/brain-do(綁,arg=routeKey)
#           unbind-do(arg=persona|brain)/sop-hint(只印新建人格 SOP 入口,不造卡)
#           new-project-do(包 O:新開專案;arg 任意占位,真正動作不看 arg)
#
# 安全設計(理由寫在這裡,免得被改成「全域攔截」):
#
#   左鍵只在**標記過的指揮艙面板**上接管 MouseUp1。
#   右欄畫出來的捲軸例外:MouseDown 不得 send-keys -M,拖曳不得進 copy-mode。
#   那兩條預設會把滑鼠序列打進窗格;中欄是巢狀的 tmux attach,序列打進去
#   或複製模式抓到中欄,attach 會退出,整艙回到終端機提示字元。
#   左/右/下格的拖曳由最外層直接進 copy-mode。中欄(10-08 the maintainer 裁)拖曳轉給內層 tmux,選得到對話歷史;
#   按下/拖/放三個事件同走內層(外層這格不在 copy-mode 時),不會把 DragEnd 丟在內外層之間。
#
#   root table 之外,copy-mode/copy-mode-vi 兩張表的 MouseDragEnd1Pane 另綁
#   copy-pipe-and-cancel 進系統剪貼簿(只在拖選狀態機內生效):macOS 終端
#   不支援 OSC 52,set-clipboard external 無效,不 pipe 的話選取只進
#   tmux buffer(the maintainer 08-14「不能複製已選取的文字」即此)。
#
#   右鍵為避免 tmux 內建 MouseDown3 選單搶先,在 AIB 指揮艙內只於 Down
#   做 no-op,Pane/Border/Status 共用的情境選單一律到 Up 才顯示。安裝前
#   會保存每個 server-global 右鍵綁定,非 AIB session 繼續執行原綁定,
#   不把 tmux 內建 display-menu 降級成 send-keys。
#
#   已標記面板內若沒點到互動目標,結果是 passthrough=不動作(只有選取該面板)。
#   判不出來就不動 —— 滑鼠是使用者的手,寧可沒反應也不要跳去他沒要去的地方。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"

# 10-07 a seat:外層 tmux 的滑鼠綁定是**全機一張表**,以前 --install 把它指向「最後一個開艙/開席的人的樹」(`$HERE`)。
#   10-06 23:1x 真機:3f999 從 ~/wt-musebot-drain-3f999 開席 ⇒ 綁定改指它的樹 ⇒ 右鍵另開 CodexBot 走到那棵樹的
#   aib/bot-thread-menu,對話選單跳不出來、沿用死 id、--check 必敗(aib-open.log 三次同形);之後它砍樹,所有艙右欄全死。
#   改成:綁定一律指向**穩定入口的那棵樹**(~/.local/bin/aib 裡 exec 的那條路徑),工作樹開席只是「用」綁定,不「寫」綁定的路徑。
#   找不到入口 ⇒ 退回 $HERE(單機沒裝入口的情況,行為同舊)。AIB_MOUSE_BINDINGS_SCRIPT 可直接指定(治具/特殊部署)。
stable_mouse_ctl_path() {
  if [ -n "${AIB_MOUSE_BINDINGS_SCRIPT:-}" ] && [ -f "$AIB_MOUSE_BINDINGS_SCRIPT" ]; then
    printf '%s' "$AIB_MOUSE_BINDINGS_SCRIPT"; return 0
  fi
  local entry="${AIB_ENTRY_BIN:-$HOME/.local/bin/aib}" root=""
  if [ -f "$entry" ]; then
    root="$(sed -n 's/^exec "\(.*\)\/src\/cockpit\/aib" .*/\1/p' "$entry" 2>/dev/null | head -1)"
  fi
  if [ -n "$root" ] && [ -f "$root/src/cockpit/mouse/mouse-ctl.sh" ]; then
    printf '%s' "$root/src/cockpit/mouse/mouse-ctl.sh"; return 0
  fi
  printf '%s' "$HERE/mouse-ctl.sh"
}
# 套組關掉時，帳號列上的點擊不再開登入、不再切主用。讀不到開關就維持可點。
_codex_pack_on() {
  "${EZ2_PY3:-python3}" -c 'import sys
sys.path.insert(0, sys.argv[1])
import codex_pack
raise SystemExit(0 if codex_pack.enabled() else 1)' "$COCKPIT" 2>/dev/null
}
# 滾輪不走下面整支程式。滑一下會連送上百次；每次再去開程式，開到不能開就跳出 128。
# 這裡只留一筆方向。拿到鎖的那一次才加總。對話框的滾輪仍走原路。
_wheel_drop() {
  local dir="$1" sess="$2" inst sd qdir lock wpid py
  case "$dir" in
    up|down) ;;
    *) return 0 ;;
  esac
  sess="${sess#\'}"; sess="${sess%\'}"
  sess="${sess#\"}"; sess="${sess%\"}"
  inst="${sess#aib-}"
  [ -n "${inst:-}" ] && [ "$inst" != "$sess" ] || return 0
  sd="${HOME:-}/.ez2-aib/instances/${inst}/cockpit"
  [ -d "$sd" ] || return 0
  qdir="$sd/.scroll-wheel.q"
  mkdir -p "$qdir" || return 0
  printf '%s\n' "$dir" > "$qdir/e-$$-$RANDOM" || return 0
  lock="$sd/.scroll-wheel.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    wpid=""
    [ -f "$lock/pid" ] && IFS= read -r wpid < "$lock/pid" || true
    # 拿鎖的人還活著、鎖也不到 1 分鐘 ⇒ 讓它做完。超過 1 分鐘算舊鎖:停電/重開機後 pid 可能被別的行程用掉(10-10)。
    if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null && [ -n "$(find "$lock" -maxdepth 0 -mmin -1 2>/dev/null)" ]; then
      return 0
    fi
    rm -rf "$lock" 2>/dev/null || return 0
    mkdir "$lock" 2>/dev/null || return 0
  fi
  printf '%s\n' "$$" > "$lock/pid" || true
  py="${EZ2_PY3:-python3}"
  "$py" "$HERE/scroll_nudge.py" "$sd" >/dev/null 2>&1 || true
  rm -rf "$lock" 2>/dev/null || true
  return 0
}
if [ "${1:-}" = "--bar-drag" ] && [ "${AIB_DRY_RUN:-0}" != "1" ]; then
  # 拖捲軸每格都會進來。不載入這支大程式，只把游標的列交給已經在跑的那一個。
  bash "$HERE/right-scroll-drag.sh" "${2:-}" "${3:-}" || true
  exit 0
fi
if [ "${1:-}" = "--wheel" ] && [ "${AIB_DRY_RUN:-0}" != "1" ]; then
  _wdir="${2:-}"
  _wsess="${3:-}"
  _wtag=""
  _wtag="$(env -u TMUX tmux display-message -p -t= '#{@aib_pane}' 2>/dev/null || true)" || true
  if [ "$_wtag" != "hub-frame" ]; then
    _wheel_drop "$_wdir" "$_wsess" || true
    exit 0
  fi
fi
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
ABORT_OPEN=0
CLOSE_CABIN=0
SOFT_SINCE=""
PANE_ROLE=""
HIT_INTENT="click"
EXPECT_WINDOW=""
ROLE=""; CX=""; CY=""; SESSION=""; SURFACE=""; PANE=""; WINDOW=""
NEW_AI_ENGINE=""
NEW_AI_ROLE=""
NEW_AI_REPORTS_TO=""
BIND_KIND=""; BIND_SEAT=""; BIND_ARG=""
PERF_ID=""
QSEAT=""; QITEM=""; QPRI=""; QBODY=""; QDIR=""; QREV=""; QEPOCH=""; NO_REV_CHECK=0
INSTALL_LOCK="aib-mouse-install"
INSTALL_LOCKED=0

while [ $# -gt 0 ]; do
  case "$1" in
    --install) MODE="install" ;;
    --usage-remove-confirmed) MODE="usage-remove-confirmed" ;;
    --leftover-confirmed) MODE="leftover-confirmed" ;;
    --role)
      PANE_ROLE="${2:-}"; shift "$(( $# < 1 ? $# : 1 ))" ;;
    --close-row)
      # 10-24 @abb89:右鍵「關閉這一席」的回呼。走 --click 同一條路,只是命中器用 close 意圖
      #   (整列都算、只認有 [X]/(x) 的列)⇒ 彈的是跟點 [X] 同一個確認選單,真關照舊只在 --release-confirmed。
      #   審查 r1 M2:參數逐格判 --* / 空,短少時不把 --dry-run 當成角色 / 座標吃掉
      HIT_INTENT="close"
      MODE="click"
      for _slot in ROLE CX CY SESSION; do
        case "${2:-}" in --*|"") break ;; esac
        printf -v "$_slot" '%s' "$2"
        shift
      done
      ;;
    --click)
      MODE="click"
      ROLE="${2:-}"
      CX="${3:-}"
      CY="${4:-}"
      case "${5:-}" in --*|"") ;; *) SESSION="$5"; shift ;; esac
      shift "$(( $# < 3 ? $# : 3 ))"
      ;;
    --bar-arm)
      # 右欄按下時:在畫出來的捲軸上就 exit 0(不要把序列送進窗格),否則 exit 1。
      # 本迴圈尾端還有一次 shift 吃掉 --bar-arm;這裡只吃座標,不把 --dry-run 當座標。
      MODE="bar-arm"
      n=1
      case "${2:-}" in --*|"" ) ;; *) CX="$2"; n=$((n + 1)) ;; esac
      if [ "$n" -ge 2 ]; then
        case "${3:-}" in --*|"" ) ;; *) CY="$3"; n=$((n + 1)) ;; esac
      fi
      if [ "$n" -ge 3 ]; then
        case "${4:-}" in --*|"" ) ;; *) SESSION="$4"; n=$((n + 1)) ;; esac
      fi
      shift "$((n - 1))" || true
      ;;
    --bar-drag)
      MODE="bar-drag"
      CY="${2:-}"
      case "${3:-}" in --*|"") ;; *) SESSION="$3"; shift ;; esac
      shift "$(( $# < 1 ? $# : 1 ))"
      ;;
    --wheel)
      # 274 (e)3:滾輪只吃右欄(tmux 綁定端用 if-shell 判 @aib_pane=ai-list,其他格原樣 send-keys -M)
      MODE="wheel"
      WHEEL_DIR="${2:-}"
      case "${3:-}" in --*|"") ;; *) SESSION="$3"; shift ;; esac
      shift "$(( $# < 1 ? $# : 1 ))"
      ;;
    --context)
      MODE="context"
      SURFACE="${2:-}"
      ROLE="${3:-}"
      CX="${4:-}"
      CY="${5:-}"
      PANE="${6:-}"
      SESSION="${7:-}"
      shift "$(( $# < 6 ? $# : 6 ))"
      ;;
    --open-window)
      MODE="open-window"
      WINDOW="${2:-}"
      SESSION="${3:-}"
      shift "$(( $# < 2 ? $# : 2 ))"
      ;;
    --perf-close-confirmed)
      # 只由效能頁 [X] 確認選單的「確定」項呼叫;E 面板第三片(the maintainer 08-18)
      MODE="perf-close-confirmed"
      PERF_ID="${2:-}"
      shift "$(( $# < 1 ? $# : 1 ))"
      ;;
    --perf-reap-confirmed)
      MODE="perf-reap-confirmed"
      ;;
    --release-confirmed)
      # 只由 [X] 確認選單的「確定」項呼叫(08-14 the maintainer 新令);人也可手動
      # 09-14:後面可接 --abort-open(開席中/載入中的列點 [X]=中斷開席,跳過閒置重驗;
      #   那一席正在讀開席單、重驗必說 active 而拒殺;人按 X 又按確定=兩重明示)
      # 用它,語意=已確認的釋放
      MODE="release-confirmed"
      WINDOW="${2:-}"
      SESSION="${3:-}"
      shift "$(( $# < 2 ? $# : 2 ))"
      # 09-14 the maintainer:開席中/載入中的列點 [X]=中斷開席。那一席正在讀開席單(活動新鮮),
      #   閒置重驗一定說 active 而拒殺;人已按 X 又按確定=兩重明示,這條旗標跳過那一關。
      ;;
    --abort-open) ABORT_OPEN=1 ;;      # 只跟 --release-confirmed 一起用;單獨給沒有意義但也不炸
    --soft-since)                      # 10-24:(x) 軟關入口,帶選單開出的時間(epoch 秒);見 release-confirmed 段
      SOFT_SINCE="${2:-}"
      case "$SOFT_SINCE" in --*|"") SOFT_SINCE="" ;; *) shift "$(( $# < 1 ? $# : 1 ))" ;; esac  # 沒接值不吃下一個旗標
      ;;
    --expect-window)                   # 10-24:只跟 --close-row 一起用;右鍵選單開出時那一列的窗 id
      EXPECT_WINDOW="${2:-}"
      case "$EXPECT_WINDOW" in --*|"") EXPECT_WINDOW="" ;; *) shift "$(( $# < 1 ? $# : 1 ))" ;; esac
      ;;
    --close-cabin) CLOSE_CABIN=1 ;;    # 09-14 the maintainer:關的是最後一席 ⇒ 關完席把整艙收掉(走 idle_reaper 收艙,不強制;見下方 close-cabin 段)
    --kill-focused-seat)
      # the maintainer 18:2x:右鍵「刪除聚焦窗格」。只殺席位窗,不用確認對話框,
      # 關窗後走 captains-ctl --sync 收席位檔。
      MODE="kill-focused-seat"
      SESSION="${2:-}"
      shift "$(( $# < 1 ? $# : 1 ))"
      ;;
    --copy-cabin)
      MODE="copy-cabin"
      ;;
    --new-ai-menu)
      # 「另開 AI」的選單層:engine 空=開引擎選單,非空=開該引擎的角色選單。
      # 由上一層選單以 run-shell 回呼(不用巢狀字串,見 NEW_AI_ACTION 註解)
      MODE="new-ai-menu"
      NEW_AI_ENGINE="${2:-}"
      PANE="${3:-}"
      CX="${4:-}"
      CY="${5:-}"
      shift "$(( $# < 4 ? $# : 4 ))"
      ;;
    --new-ai-hang)
      # 第三層:已有艦長時,選掛在哪一位底下。沒選(這一層沒開)就預設第一艦長。
      MODE="new-ai-hang"
      NEW_AI_ENGINE="${2:-}"
      NEW_AI_ROLE="${3:-}"
      PANE="${4:-}"
      CX="${5:-}"
      CY="${6:-}"
      shift "$(( $# < 5 ? $# : 5 ))"
      ;;
    --new-ai-key)
      # 10-24 @abb89:計費組缺金鑰 ⇒ 引擎層那一項改叫這裡:叫出 aib secret ask,不開死窗
      MODE="new-ai-key"
      NEW_AI_ENGINE="${2:-}"
      PANE="${3:-}"
      shift "$(( $# < 2 ? $# : 2 ))"
      ;;
    --new-ai-open)
      # CodexBot 專用可見 preflight。一般引擎仍走原本直接開席路徑。
      MODE="new-ai-open"
      NEW_AI_ENGINE="${2:-}"
      NEW_AI_ROLE="${3:-}"
      NEW_AI_REPORTS_TO="${4:-}"
      [ "$NEW_AI_REPORTS_TO" != "-" ] || NEW_AI_REPORTS_TO=""
      PANE="${5:-}"
      shift "$(( $# < 4 ? $# : 4 ))"
      ;;
    --bind-menu)
      # L2-03:綁定人格/大腦選單層(由 --click 的 bind-*-menu 或上一層以 run-shell 回呼)
      MODE="bind-menu"
      BIND_KIND="${2:-}"
      BIND_SEAT="${3:-}"
      BIND_ARG="${4:-}"
      PANE="${5:-}"
      CX="${6:-}"
      CY="${7:-}"
      shift "$(( $# < 6 ? $# : 6 ))"
      ;;
    --theme-menu)
      # 主題底色選單(設計席第一刀的 UI 掛接;由情境選單以 run-shell 回呼)
      MODE="theme-menu"
      PANE="${2:-}"
      CX="${3:-}"
      CY="${4:-}"
      SESSION="${5:-}"
      shift "$(( $# < 4 ? $# : 4 ))"
      ;;
    --dm-queue-priority|--dm-queue-body)
      MODE="${1#--}"
      QSEAT="${2:-}"
      QITEM="${3:-}"
      # 第 4 個參數:priority 或正文;第 5 個是那一幀的修訂號(純數字才收;沒帶 ⇒ 執行端一律拒,沒有舊路)
      if [ "$MODE" = "dm-queue-priority" ]; then QPRI="${4:-}"; else QBODY="${4:-}"; fi
      case "${5:-}" in
        ''|*[!0-9]*) QREV=""; shift "$(( $# < 3 ? $# : 3 ))" ;;
        *) QREV="$5"; shift "$(( $# < 4 ? $# : 4 ))" ;;
      esac
      ;;
    --dm-queue-delete-confirmed|--dm-queue-send-confirmed)
      MODE="${1#--}"
      QSEAT="${2:-}"
      QITEM="${3:-}"
      # 第 36 項:第 4 個參數是確認框那一幀的修訂號(純數字才收);QREV 空 ⇒ 執行端 dm_queue_refuse_no_rev
      #   一律不執行(fail-closed),沒有舊路;只有顯式 --no-rev-check 才繞過並留痕
      case "${4:-}" in
        ''|*[!0-9]*) QREV=""; shift "$(( $# < 2 ? $# : 2 ))" ;;
        *)
          QREV="$4"
          # 第 37 項:第 5 個參數是那一幀的 epoch(純數字才收),只留痕不擋
          case "${5:-}" in
            ''|*[!0-9]*) QEPOCH=""; shift "$(( $# < 3 ? $# : 3 ))" ;;
            *) QEPOCH="$5"; shift "$(( $# < 4 ? $# : 4 ))" ;;
          esac
          ;;
      esac
      ;;
    --dblclick)
      # 雙擊(DoubleClick1Pane 綁定);參數形狀同 --click。缺參數也恆 exit 0(E2.13)。
      MODE="dblclick"
      ROLE="${2:-}"
      CX="${3:-}"
      CY="${4:-}"
      case "${5:-}" in --*|"") ;; *) SESSION="$5"; shift ;; esac
      shift "$(( $# < 3 ? $# : 3 ))"
      ;;
    --pending-edit)
      MODE="pending-edit"
      QSEAT="${2:-}"
      QITEM="${3:-}"
      shift "$(( $# < 2 ? $# : 2 ))"
      ;;
    --dm-queue-move)
      MODE="dm-queue-move"
      QSEAT="${2:-}"
      QITEM="${3:-}"
      QDIR="${4:-}"
      shift "$(( $# < 3 ? $# : 3 ))"
      ;;
    --dry-run) DRY=1 ;;
    # 第 36/37 項:明示繞過修訂號檢查(只給腳本/舊呼叫端);留痕欄位固定 rev_bypass=explicit-no-rev-check,
    # 與「沒帶 rev 被拒」那行(rev_bypass=none reason=no_rev)不得同形(qa 建議,同 53 legacy 兩來源的教訓)
    --no-rev-check) NO_REV_CHECK=1 ;;
    --wh)
      # 第 35 項:乾跑時覆寫欄高(沒有 tmux 可量),讓拒開那條路在乾跑也走得到
      DRY_WH="${2:-}"
      shift "$(( $# < 1 ? $# : 1 ))"
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  # ★shift 必須防空:各分支已用 ${N:-} 防「未帶參」,但 set -e 下 shift
  # 本身在 $#=0 時失敗就會**靜默**中止整支(rc=1,連日誌都沒有,最難查)。
  # 08-14 第三艦長實測 `--release-confirmed @999`(照 usage 的可選 session
  # 用法)即中此;同型在 --open-window/--click/--context 各有一個,是**類別
  # 不是單點**。E2.13 鐵律是「恆 exit 0」不是「主要路徑 exit 0」。
  shift || true
done
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install mouse-ctl.sh "$HERE" || exit $?
  else
    # 報錯走 aib/i18n 四語鍵;字典或 python 出事就印鍵名,報錯不能因翻譯失敗而消失。
    { python3 "$COCKPIT/i18n_lib.py" aib.install_guard.missing --param script=mouse-ctl.sh \
        --param "path=$COCKPIT/install-guard.sh" 2>/dev/null || printf '%s\n' aib.install_guard.missing; } >&2
    exit 6
  fi
fi

# 選單座標(08-14 the maintainer 缺陷「右鍵選單卡在最左上方」;第三艦長實證根因):
# `-x M -y M` 的 M 要靠**當前滑鼠事件**才解得出來,而我們的選單是從
# run-shell 的子行程叫 display-menu——那時已脫離滑鼠事件情境,M 無值可解,
# tmux 退回 0,0=左上角。實證:一般 shell 問 `#{mouse_x}`/`#{mouse_y}` 兩個
# 空值,而 pane 幾何查得到。
# 座標其實一直都有:綁定已把 #{q:mouse_x}/#{q:mouse_y} 餵成 $CX/$CY,但那是
# **pane 相對**,display-menu -x/-y 吃視窗絕對 → 要加 pane 偏移。
# ★不得退回 -x M -y M 當降級路徑(那就是這個 bug 本身);取不到就明確置中
# 並在 stderr 說一句——靜默退回錯位置比顯示在中間更難查。
# ★preserved_binding() 內那三個 -x M -y M 不要動:那是保存下來的 tmux 內建
# 綁定字串,**在綁定內**執行,M 解得出來,改了會弄壞非 AIB session 的原生
# 行為。同一個字面在兩種情境下語意不同,這是本修法的關鍵區分。
# ★選單一律帶 **-O -M 兩個**(08-14 the maintainer 四、五度回報「按了沒刪除」;
# 兩個旗標解決的是**不同的事**,看起來都跟滑鼠有關,但少任何一個功能都不成立
# ——後人請勿當成重複砍掉其中一個:
#   -M:讓這個選單**接受滑鼠事件**。man 原文「by default only menus opened
#       from mouse key bindings do so」——我們的選單是從 **run-shell 子行程**
#       下 display-menu 開的,不是滑鼠綁定直接開的,**不加 -M 就完全點不動**
#       (the maintainer 用滑鼠點在空氣上;鍵盤 q/y/方向鍵仍有效,這也是自證假說的
#       對照組:鍵盤能殺而滑鼠不能=缺 -M 的確證)。
#   -O:接受滑鼠之後,**放開不算選取**、必須再點一次才選。tmux man 原文:「選單由滑鼠綁定開啟時,**放開按鍵而有項目被選中就選那一項**」。
# 我們把選單開在游標處(位置修法),於是第一項正好坐在指標下——使用者放開
# 左鍵的瞬間就被判定選了第一項(取消),選單關閉、零動作、零日誌。
# **the maintainer 每一次點都是在選取消。** -O 改成「放開不選、必須再點一次才選」,
# 這正是「確認」該有的語意;安全性自此由 -O 保證,不再靠「取消排第一」這個
# 與位置耦合的脆弱技巧(排序保留,但它不再是安全機制)。
# ---- 「另開 AI」選單(08-14 the maintainer 直令;09-11 加掛艦長層)-------------------
# 右鍵 → 另開 AI → 引擎 → 角色 →(有艦長且角色不是艦長)掛在誰底下
#   → `aib <engine> dgrs <role> --again [--reports-to SEAT]`
# 沒有艦長、或選的是艦長席、或這一層沒開(CLI/Esc):預設掛第一艦長(席位檔
# 不寫 reports_to,右欄缺欄=第一艦長)。Esc 不開席(tmux 選單沒命令=取消)。
# ★引擎清單**從 aib 的 PROVIDERS 白名單長出來,不在這裡寫死**(the maintainer 直令
#   「要預留未來新的不同品牌 AI」):新增品牌只改 aib 那一行,選單自動多一項。
#   同一天的教訓兩次:清單會腐,判準要從碼長出來。
# ★用 `--again`(同 hub 加次席)**不是 `new`**:`new` 在 aib 裡是開全新
#   instance(自己的 hub socket+cockpit+war-room+五格佈局全部重建),the maintainer
#   08-14 實測「卡住/很慢」就是誤用 new 開出了整套 aib-6 指揮艙。兩者在
#   aib:145 是明確的正交語意。
# ★模式固定 dgrs(the maintainer 直令「完整 dgrs 預設啟用」)。
# ★角色清單走 env 覆寫,預設只列**實測開得起來**的:aib 對 role 只驗格式
#   不驗角色卡存在,不存在的 role 會讓載體停在「請輸入一張既有角色卡」等
#   真人回答(2026-08-10 四條 lane 全滅的根因)。design 已由 the maintainer 08-14 實證
#   可用。the maintainer 09-11 要選單列出監察/品管/專家,預設改含 monitor qa advisor;
#   沒有對應角色卡時開席可能停在「請輸入既有角色卡」,選單仍先出現。
NEW_AI_ROLES="${EZ2_AIB_MENU_ROLES:-root pm dev1 design monitor qa advisor}"

new_ai_providers() {   # 從 aib 白名單解析,再扣 providers.json in_new_ai_menu=false
  # CLI aib <id> 仍可用；只藏右鍵另開 AI。失敗回空=選單不長這項。
  local _raw _filtered
  _raw=$(sed -n 's/^PROVIDERS="\([^"]*\)".*/\1/p' "$COCKPIT/aib" 2>/dev/null | head -1)
  [ -n "$_raw" ] || return 0
  _filtered=$("$EZ2_PY3" "$HERE/filter_new_ai_providers.py" "$_raw" "$COCKPIT/providers.json" 2>/dev/null || printf %s "$_raw")
  printf %s "$_filtered"
}


# 10-09 a seat:這家是不是橋接 bot(providers.json menu_group=bot)。是 ⇒ 右鍵開席走 --new-ai-open(失敗會在畫面講原因)。
#   名單從正本問(bot_caps.py is-bot),不在殼裡再抄一份(DIAG-bot-compare §0:同一份名單已散在八處、對不上)。
#   問不到(python 壞了)⇒ 當成不是 bot,走舊路(直接開席),不擋開席。
_is_bot_engine() {
  [ -n "${1:-}" ] || return 1
  "$EZ2_PY3" "$COCKPIT/bot_caps.py" is-bot "$1" >/dev/null 2>&1
}

# 引擎列顏色=render_lib ENGINE_LETTER_COLORS(與右欄徽章同色);無色則原名
new_ai_engine_label() {
  local _lbl
  _lbl="$("$EZ2_PY3" "$HERE/engine_menu_label.py" "${1:-}" 2>/dev/null || true)"
  if [ -n "$_lbl" ]; then
    printf '%s' "$_lbl"
  else
    printf '%s' "${1:-}"
  fi
}

# ★tmux `run-shell` 有輸出=把該格切進 **view-mode** 並接管鍵盤,而且**畫面上
# 不會告訴使用者他在裡面**(08-14 the maintainer「卡住、打不了字」的真根因:選單開席
# 的 run-shell 沒重導,aib 的 stdout 三行把外層中間格切進 view-mode)。
# 凡在 cockpit 格內呼叫 run-shell,輸出一律重導到**日誌**——
# ★不准 `/dev/null`:那會把開席失敗變成靜默失敗,今天已經吃夠靜默的虧
# (靜默 0 偏移 / 靜默 rc=1 / 靜默吃掉真人鍵盤,同一天第三種形狀)。
# ★★ 2026-09-25(a seat,the maintainer 實測「新開席選單卡住、移不動也點不了,只能等逾時」):
#   **開席那幾條 `run-shell` 一律帶 `-b`**。不帶 `-b` 的 run-shell 會把 client 的**鍵盤/滑鼠事件處理**
#   壓住到指令跑完為止(它不擋別的 tmux 指令 —— 所以拿 `list-sessions` 去量會量不到,我第一版就這樣量錯)。
#   v0.64.0 起開席會跳「要載入哪個舊席位」選單並等最多 30 秒 ⇒ 整艙凍 30 秒,而選單看得到卻按不動。
#   沙盒實測(pty 真 client,巢狀選單重現右鍵流程):不帶 -b = 送 Enter 後 10.9 秒才生效;帶 -b = 0.3 秒。
#   ★ 這是 08-14「run-shell 有輸出把該格切進 view-mode」的同族第二種:同樣是 run-shell 吃掉真人鍵盤,
#     但那次是輸出造成、這次是**同步執行**造成,兩者修法不同,所以兩條註解都留著。
RUNSHELL_LOG="$EZ2_COCKPIT_STATE_DIR/aib-open.log"
printf -v RUNSHELL_REDIR ' >>%q 2>&1' "$RUNSHELL_LOG"

MENU_POS=()
set_menu_pos() {   # $1=目標 pane(可空;DRY 下不查幾何)
  local target="${1:-}" pl=0 pt=0 geom=""
  case "${CX:-}${CY:-}" in
    ''|*[!0-9]*) MENU_POS=(-x C -y C)
      # 降級必須留痕。★但 stderr 在 context 路徑會被 `exec 2>/dev/null`
      # 吞掉——那是既有刻意設計(情境選單是選用 UI,壞狀態不得在 AI pane
      # 產生終端噪音),不為了這則訊息推翻。故兩路併走:stderr(click 路徑
      # 看得到)+ mouse-errors.log(所有路徑都留得住,既有 fail-visible
      # 慣例)。靜默退回錯位置才是真罪,不是「非得走 stderr」。
      echo "mouse-ctl: 滑鼠座標不可用($CX,$CY),選單置中顯示" >&2
      echo "$(date '+%F %T') menu-pos fallback: cx=$CX cy=$CY -> centre" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      return 0 ;;
  esac
  if [ "$DRY" != "1" ] && [ -n "$target" ]; then
    geom="$(env -u TMUX tmux display-message -p -t "$target" \
      '#{pane_left} #{pane_top}' 2>/dev/null || true)"
    case "$geom" in
      [0-9]*' '[0-9]*) pl="${geom%% *}"; pt="${geom##* }" ;;
    esac
  fi
  MENU_POS=(-x "$(( pl + CX ))" -y "$(( pt + CY ))")
}

pending_nav_enter() {
  [ "$DRY" = "1" ] && return 0
  env -u TMUX tmux switch-client -t "${SESSION:-$EZ2_TMUX_SESSION}" -T pending-nav >/dev/null 2>&1 || true
}

pending_nav_leave() {
  [ "$DRY" = "1" ] && return 0
  env -u TMUX tmux switch-client -t "${SESSION:-$EZ2_TMUX_SESSION}" -T root >/dev/null 2>&1 || true
}

pending_inbox_tab_key() {   # 待審區目前那一頁(state/pending-inbox-tab 0..4)→ dm_queue view --tab;壞值/沒有=waiting
  local v
  v="$(cat "${EZ2_COCKPIT_STATE_DIR:-/nonexistent}/pending-inbox-tab" 2>/dev/null || true)"
  case "${v//[[:space:]]/}" in
    1) printf 'read' ;;
    2) printf 'done' ;;
    3) printf 'archived' ;;
    4) printf 'deleted' ;;
    *) printf 'waiting' ;;
  esac
}

pending_kick_redraw() {
  # 切席後待審立刻重畫,不等畫圈 0.4 秒。
  if [ "$DRY" = "1" ]; then
    run bash "$COCKPIT/pane-pending-review.sh" --kick
    return 0
  fi
  bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
}

pending_open_editor() {
  # 待審底下拆加高框。Enter 換行;框裡[儲存]。
  local review sess lock budget edit_h keep_n too_short old
  sess="${SESSION:-$EZ2_TMUX_SESSION}"
  lock="${EZ2_COCKPIT_STATE_DIR}/pending-edit.lock"
  printf -v PY_Q '%q' "$EZ2_PY3"
  printf -v ED_Q '%q' "$COCKPIT/pending_edit.py"
  printf -v SEAT_Q '%q' "$QSEAT"
  printf -v ITEM_Q '%q' "$QITEM"
  if [ "$DRY" = "1" ]; then
    # 乾跑沒有 tmux 可量:欄高用 --wh 覆寫(預設 40 是典型值),問同一支預算,不寫死 12。
    # 拒開那條路在乾跑也要走得到(--dry-run --wh 10),否則它只能用字面守。
    case "${DRY_WH:-}" in ''|*[!0-9]*) DRY_WH=40 ;; esac
    budget="$(EZ2_COCKPIT_STATE_DIR= "$EZ2_PY3" "$COCKPIT/pending_height.py" --editor-budget \
      --wh "$DRY_WH" --want-pending 3 2>/dev/null || true)"
    edit_h="$(printf '%s\n' "$budget" | awk -F= '$1=="editor"{print $2}')"
    too_short="$(printf '%s\n' "$budget" | awk '/^msg=/{sub(/^msg=/,""); print}')"
    case "$edit_h" in ''|*[!0-9]*) edit_h=0 ;; esac
    if [ "$edit_h" -lt 1 ]; then
      printf '+ pending-edit refused: %s\n' "${too_short:-pending_height.py 量不到欄高,不開編輯框}"
      return 0
    fi
    run tmux split-window -v -l "$edit_h" -t review \
      "$PY_Q $ED_Q --seat $SEAT_Q --id $ITEM_Q"
    return 0
  fi
  review="${PANE:-}"
  case "$review" in ""|"=") review="" ;; esac
  [ -n "$review" ] || review="$(lookup_role_pane review)"
  [ -n "$review" ] || review="$(lookup_role_pane "${PANE_ROLE:-}")"
  [ -n "$review" ] || exit 0
  pending_nav_leave
  old="$(env -u TMUX tmux list-panes -s ${sess:+-t "$sess"} \
    -F '#{pane_id} #{@aib_pane}' 2>/dev/null \
    | awk '$2=="pending-edit"{print $1; exit}')" || old=""
  [ -n "$old" ] && env -u TMUX tmux kill-pane -t "$old" >/dev/null 2>&1 || true
  # 第 35 項:編輯框幾列、待審框縮到幾列,問 render_lib.feature_height_budget 那一支
  # (經 pending_height.py 量中央欄高);本檔不再自己寫中央 8 列 / 編輯框 12 列。
  # 放不下 ⇒ 暫拒開編輯、具名說出來、留痕,不開一個更矮的、不建鎖。
  budget="$(EZ2_TMUX_SESSION="${sess:-}" "$EZ2_PY3" "$COCKPIT/pending_height.py" --editor-budget --pane "$review" 2>/dev/null || true)"
  edit_h="$(printf '%s\n' "$budget" | awk -F= '$1=="editor"{print $2}')"
  keep_n="$(printf '%s\n' "$budget" | awk -F= '$1=="pending"{print $2}')"
  too_short="$(printf '%s\n' "$budget" | awk '/^msg=/{sub(/^msg=/,""); print}')"
  case "$edit_h" in ''|*[!0-9]*) edit_h=0 ;; esac
  case "$keep_n" in ''|*[!0-9]*) keep_n=0 ;; esac
  if [ "$edit_h" -lt 1 ]; then
    [ -n "$too_short" ] || too_short="pending_height.py 量不到欄高,不開編輯框"
    printf '%s pending-edit refused: %s\n' "$(date '+%H:%M:%S')" "$too_short" \
      >> "${EZ2_COCKPIT_STATE_DIR}/aib-open.log" 2>/dev/null || true
    env -u TMUX tmux display-message -t "$review" "$too_short" >/dev/null 2>&1 || true
    return 0
  fi
  : > "$lock"
  env -u TMUX tmux resize-pane -t "$review" -y $((keep_n + edit_h)) >/dev/null 2>&1 || true
  if ! env -u TMUX tmux split-window -v -t "$review" -l "$edit_h" \
      -e EZ2_AIB_INSTANCE="${EZ2_AIB_INSTANCE:-}" \
      -e EZ2_AIB_INSTANCE_HOME="${EZ2_AIB_INSTANCE_HOME:-}" \
      -e EZ2_AIB_STATE_ROOT="${EZ2_AIB_STATE_ROOT:-}" \
      -e EZ2_TMUX_SESSION="${sess:-}" \
      -e EZ2_COCKPIT_STATE_DIR="${EZ2_COCKPIT_STATE_DIR:-}" \
      -e EZ2_AIHUB_SOCKET="${EZ2_AIHUB_SOCKET:-}" \
      "tmux set-option -p @aib_role edit >/dev/null; tmux set-option -p @aib_pane pending-edit >/dev/null; $EZ2_PY3 \"$COCKPIT/pending_edit.py\" --seat $SEAT_Q --id $ITEM_Q; rc=\$?; rm -f \"$lock\"; exit \$rc" \
      >/dev/null 2>&1; then
    rm -f "$lock"
  fi
}

dm_queue_bypass_note() {   # $1=delete|send;明示 --no-rev-check 才走到這裡:留痕字面固定,與被拒那行不同形
  local verb="$1"
  if [ "$DRY" = "1" ]; then
    printf '+ dm-queue %s bypass rev_bypass=explicit-no-rev-check %s %s\n' "$verb" "$QSEAT" "$QITEM"
    return 0
  fi
  echo "$(date '+%F %T') dm-queue $verb bypass rev_bypass=explicit-no-rev-check $QSEAT $QITEM" \
    >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
}

dm_queue_refuse_no_rev() {   # $1=delete|send;36-1:沒帶修訂號的確認一律不執行、具名、留痕
  # 留痕字面固定 rev_bypass=none reason=no_rev;明示繞過那行是 rev_bypass=explicit-no-rev-check,兩者不得同形
  local verb="$1" msg="這一幀沒有修訂號,重畫後再按"
  if [ "$DRY" = "1" ]; then
    printf '+ dm-queue %s refused rev_bypass=none reason=no_rev %s %s (%s)\n' "$verb" "$QSEAT" "$QITEM" "$msg"
    return 0
  fi
  echo "$(date '+%F %T') dm-queue $verb refused rev_bypass=none reason=no_rev $QSEAT $QITEM" \
    >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
  env -u TMUX tmux display-message "$msg" >/dev/null 2>&1 || true
  bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
}

pending_queue_confirm() {
  # $1=標題動詞 $2=確定項文字 $3=--dm-queue-*-confirmed
  # 取消排第一項,選單開在游標處。Esc/點外面=取消。
  local verb="$1" clabel="$2" flag="$3"
  local preview="" title=""
  preview="$("$EZ2_PY3" "$COCKPIT/dm_queue.py" --reveal-global show --seat "$QSEAT" --id "$QITEM" 2>/dev/null || true)"
  preview="$(printf '%s' "$preview" | tr '\n\r[]\"' '    ')"
  preview="${preview#"${preview%%[![:space:]]*}"}"
  preview="${preview:0:36}"
  title="$verb · @$QSEAT"
  [ -n "$preview" ] && title="$title · $preview"
  MENU_PANE="${PANE:-}"
  if [ -z "$MENU_PANE" ] && [ "$DRY" != "1" ]; then
    MENU_PANE="$(lookup_role_pane "${PANE_ROLE:-}")"
  fi
  MENU_TARGET=()
  [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
  set_menu_pos "$MENU_PANE"
  printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
  printf -v QSEAT_Q '%q' "$QSEAT"
  printf -v QITEM_Q '%q' "$QITEM"
  printf -v LOG_Q '%q' "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log"
  local confirm cancel rev_arg=""
  # the maintainer 09-30 15:3x(收件匣 A 項 3):呼叫端可先填 QCONFIRM_WARN(警告句陣列),在「取消」與確定項之間
  #   印成不可點的灰行(tmux display-menu 名稱以 - 開頭 = 停用項)。沒填 = 與 09-30 前逐字相同。
  local warn_items=() _w
  for _w in ${QCONFIRM_WARN[@]+"${QCONFIRM_WARN[@]}"}; do
    warn_items+=("-$_w" "" "")
  done
  # 第 36 項:確認回呼帶這一幀的修訂號(seat + item + rev,不帶列號);執行前重讀比對
  # 第 37 項:再帶這一幀的 epoch(第 5 參數)留痕——內容是否被改仍由 rev 擋,epoch 擋的是點擊層
  [ -n "${QREV:-}" ] && rev_arg=" $QREV"
  [ -n "${QREV:-}" ] && [ -n "${QEPOCH:-}" ] && rev_arg="$rev_arg $QEPOCH"
  confirm="run-shell 'bash $SCRIPT_Q $flag $QSEAT_Q $QITEM_Q$rev_arg$RUNSHELL_REDIR'"
  cancel="run-shell 'printf \"%s $flag cancelled ($QSEAT $QITEM)\\n\" \"\$(date \"+%F %T\")\" >> $LOG_Q'"
  pending_nav_leave
  if [ "$DRY" = "1" ]; then
    run tmux display-menu -O -M -T "$title" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
      "取消" q "$cancel" "" ${warn_items[@]+"${warn_items[@]}"} "$clabel" y "$confirm"
  else
    tmux display-menu -O -M -T "$title" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
      ${MENU_POS[@]+"${MENU_POS[@]}"} \
      "取消" q "$cancel" "" ${warn_items[@]+"${warn_items[@]}"} "$clabel" y "$confirm" >/dev/null 2>&1 || true
  fi
}

lookup_role_pane() {   # $1=role; 空字串=找不到
  local role="${1:-}" sess="${SESSION:-$EZ2_TMUX_SESSION}" id=""
  [ -n "$role" ] || return 0
  if [ -n "$sess" ]; then
    id="$(env -u TMUX tmux list-panes -s -t "$sess" \
      -F '#{pane_id} #{@aib_role}' 2>/dev/null \
      | awk -v r="$role" '$2==r{print $1; exit}')" || id=""
  fi
  if [ -z "$id" ]; then
    id="$(env -u TMUX tmux list-panes -a \
      -F '#{pane_id} #{@aib_role}' 2>/dev/null \
      | awk -v r="$role" '$2==r{print $1; exit}')" || id=""
  fi
  printf '%s' "$id"
}

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

pending_queue_move() {  # $1=seat $2=id $3=up|down
  # 沒動要講出來:跨緊急程度或已在最上/最下時 display-message,不要裝死。
  local seat="$1" item="$2" dir="$3" out ok msg
  local dq="$COCKPIT/dm_queue.py"
  local wflag=()
  # 待審 tab:對調畫面上的鄰居(含已送達那幾則,只改待審頁顯示順序)
  [ "$(pending_inbox_tab_key)" = "waiting" ] && wflag=(--waiting)
  case "$dir" in up|down) ;; *) return 0 ;; esac
  if [ "$DRY" = "1" ]; then
    run "$EZ2_PY3" "$dq" move --seat "$seat" --id "$item" --dir "$dir" ${wflag[@]+"${wflag[@]}"}
    run env -u TMUX tmux display-message -d 2500 "沒有可對調的（要同一緊急程度）"
    return 0
  fi
  out="$("$EZ2_PY3" "$dq" move --seat "$seat" --id "$item" --dir "$dir" ${wflag[@]+"${wflag[@]}"} 2>/dev/null || true)"
  ok="$("$EZ2_PY3" -c 'import json,sys
try:
    print("1" if json.loads(sys.argv[1]).get("ok") else "0")
except Exception:
    print("0")' "$out" 2>/dev/null || printf '0')"
  if [ "$ok" = "1" ]; then
    return 0
  fi
  msg="$("$EZ2_PY3" -c 'import json,sys
try:
    print(json.loads(sys.argv[1]).get("message") or "")
except Exception:
    print("")' "$out" 2>/dev/null || true)"
  [ -n "$msg" ] || msg="沒有可對調的（要同一緊急程度）"
  env -u TMUX tmux display-message -d 2500 "$msg" >/dev/null 2>&1 || true
}

# ---- L2-03:綁定人格/大腦選單層(回呼式;每層 run-shell 回呼自己) --------------
# 清單來源=bind_menu.py(cards.json v2 既有卡 / 契約可寫非敏感 route),本函式只畫
# 選單與接線。執行=bind_one.py(單席執行器,綁完自動重投影→右欄 5 秒內更新)。
# 帳本署名=mouse@<seat>/aib、kind=human(是人在點)。所有輸出進 RUNSHELL_LOG。
BIND_STATE_ROOT="${EZ2_AIB_HOME:-$HOME/.ez2-aib}"
bind_menu_layer() {
  set_menu_pos "$PANE"
  local _target=() _items=() _title="" _k=1 _line _key _label _cards _contract _actor _cat _ukey
  [ -n "$PANE" ] && _target=(-t "$PANE")
  printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
  printf -v PANE_ARG_Q '%q' "$PANE"
  printf -v SEAT_Q '%q' "$BIND_SEAT"
  _cards="$BIND_STATE_ROOT/identity/cards.json"
  _contract="${EZ2_ROUTE_CONTRACT_SPEC:-${EZ2_ROUTE_REGISTRY:-}}"
  _actor="mouse@$BIND_SEAT/aib"
  case "$BIND_KIND" in
    persona)
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.bindmenu.persona.title \
        --param seat="$BIND_SEAT" 2>/dev/null | sed -n '1p' || true)"
      # ★ 分類名(中文)以 hex 進回呼字串:macOS 系統 bash 3.2 的 %q 會把 UTF-8 拆成
      #   「原始位元組+八進位」混合(實測 `printf %q 開發/品質`),字串進 run-shell 就毀。
      #   hex 純 ASCII,免引號;bind_menu.py --category-hex 解回。
      while IFS=$'\t' read -r _key _label; do
        [ -n "$_key" ] || continue
        case "$_key" in *[!0-9a-f]*) continue ;; esac
        _items+=("$_label" "" "run-shell 'bash $SCRIPT_Q --bind-menu persona-cat $SEAT_Q $_key $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      done < <("$EZ2_PY3" "$HERE/bind_menu.py" persona-cats --cards "$_cards" 2>>"$RUNSHELL_LOG" || true)
      # 「新建人格」=綁定的反面不是一鍵造卡:只印 SOP 入口(總綱 §二-D/E)
      _items+=("" "" "" "[新建人格] 走人格提取 SOP(不在此造卡)" "" "run-shell 'bash $SCRIPT_Q --bind-menu sop-hint $SEAT_Q x $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      ;;
    persona-cat)
      case "$BIND_ARG" in ''|*[!0-9a-f]*) return 0 ;; esac
      _cat="$("$EZ2_PY3" -c 'import sys; print(bytes.fromhex(sys.argv[1]).decode("utf-8","replace"))' "$BIND_ARG" 2>/dev/null || true)"
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.bindmenu.persona.category \
        --param seat="$BIND_SEAT" --param name="${_cat:-?}" 2>/dev/null | sed -n '1p' || true)"
      while IFS=$'\t' read -r _key _label; do
        [ -n "$_key" ] || continue
        case "$_key" in *[!0-9a-z]*) continue ;; esac     # uid 形狀(base36 六碼)才進回呼
        _items+=("$_label" "" "run-shell 'bash $SCRIPT_Q --bind-menu persona-do $SEAT_Q $_key $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      done < <("$EZ2_PY3" "$HERE/bind_menu.py" persona-cards --cards "$_cards" --category-hex "$BIND_ARG" 2>>"$RUNSHELL_LOG" || true)
      ;;
    brain)
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.bindmenu.brain.title \
        --param seat="$BIND_SEAT" 2>/dev/null | sed -n '1p' || true)"
      # AR-3(艦長 a6b2e 09-02 裁):[換專案] 開的選單第一項固定「解除綁定」,走既有
      # unbind-brain 確認層(取消排第一項的規矩在那一層)。只在 BIND_ARG=bound 時列——
      # [綁大腦](沒綁過)開的同一層不列,否則是「畫一個看起來能點卻不能點的東西」。
      if [ "$BIND_ARG" = "bound" ]; then
        _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.bindmenu.brain.change_title \
          --param seat="$BIND_SEAT" 2>/dev/null | sed -n '1p' || true)"
        _items+=("解除綁定" "" "run-shell 'bash $SCRIPT_Q --bind-menu unbind-confirm $SEAT_Q brain $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'" "" "" "")
      fi
      # 包 O(DESIGN §10.2):「新開專案」— 免費可建;憲法候選未核准不生效
      _items+=("新開專案" "" "run-shell 'bash $SCRIPT_Q --bind-menu new-project-do $SEAT_Q x $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      while IFS= read -r _line; do
        [ -n "$_line" ] || continue
        case "$_line" in *[!A-Za-z0-9._-]*) continue ;; esac
        _items+=("$_line" "" "run-shell 'bash $SCRIPT_Q --bind-menu brain-mod $SEAT_Q $_line $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      done < <("$EZ2_PY3" "$HERE/bind_menu.py" brain-modules --contract "$_contract" 2>>"$RUNSHELL_LOG" || true)
      ;;
    brain-mod)
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.bindmenu.brain.module \
        --param seat="$BIND_SEAT" --param mod="$BIND_ARG" 2>/dev/null | sed -n '1p' || true)"
      while IFS=$'\t' read -r _key _label; do
        [ -n "$_key" ] || continue
        case "$_key" in *[!a-z0-9._-]*) continue ;; esac
        _items+=("$_label" "" "run-shell 'bash $SCRIPT_Q --bind-menu brain-do $SEAT_Q $_key $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      done < <("$EZ2_PY3" "$HERE/bind_menu.py" brain-routes --contract "$_contract" --module "$BIND_ARG" 2>>"$RUNSHELL_LOG" || true)
      ;;
    unbind-confirm)
      # 取消排第一項(同 [X] 四裁③:選單開在游標下,手快連點不能變成直接解綁)
      if [ "$BIND_ARG" = brain ]; then _ukey=aib.bindmenu.unbind_brain_title
      else _ukey=aib.bindmenu.unbind_persona_title; fi
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" "$_ukey" \
        --param seat="$BIND_SEAT" 2>/dev/null | sed -n '1p' || true)"
      printf -v ARG_Q '%q' "$BIND_ARG"
      _items+=("取消" "" "" "確定解綁" "" "run-shell 'bash $SCRIPT_Q --bind-menu unbind-do $SEAT_Q $ARG_Q $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
      ;;
    persona-do|brain-do|unbind-do)
      # 執行層:不畫選單,叫 bind_one.py;結果留痕;失敗用 display-message 說一句
      # (E2.13 恆 exit 0:綁不成不擋艙)。
      case "$BIND_ARG" in ''|*[!0-9a-z._-]*) exit 0 ;; esac
      local _bo="$COCKPIT/../identity/bind_one.py" _cmd=() _out _rc
      case "$BIND_KIND" in
        persona-do) _cmd=("$EZ2_PY3" "$_bo" bind --seat "$BIND_SEAT" --card "$BIND_ARG" --rebind-persona) ;;
        brain-do)   _cmd=("$EZ2_PY3" "$_bo" bind --seat "$BIND_SEAT" --brain "$BIND_ARG") ;;
        unbind-do)  _cmd=("$EZ2_PY3" "$_bo" unbind --seat "$BIND_SEAT")
                    [ "$BIND_ARG" = brain ] && _cmd+=(--brain-only) ;;
      esac
      _cmd+=(--actor "$_actor" --actor-kind human --state-root "$BIND_STATE_ROOT" --cards "$_cards")
      [ -n "$_contract" ] && _cmd+=(--contract "$_contract")
      if [ "$DRY" = "1" ]; then
        run "${_cmd[@]}"
        return 0
      fi
      _out="$("${_cmd[@]}" 2>&1)"; _rc=$?
      printf '%s bind-menu %s seat=%s arg=%s rc=%s\n%s\n' "$(date '+%F %T')" "$BIND_KIND" \
        "$BIND_SEAT" "$BIND_ARG" "$_rc" "$_out" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      local _fit=""
      printf '%s' "$_out" | grep -q 'bind-one: ⚠ 人格卡' && _fit=1
      if [ "$_rc" -ne 0 ]; then
        # 審查低 3:重投影失敗(rc=1)時卡已綁上,人格卡不合也要說一聲(兩則提示會互蓋,併成一行)
        env -u TMUX tmux display-message -d 4000 ${_target[@]+"${_target[@]}"} \
          "綁定未完成(rc=$_rc):$(printf '%s' "$_out" | grep -v 'bind-one: ⚠ 人格卡' | grep -m1 'bind-one:' | cut -c1-120 | sed 's/#/##/g')${_fit:+;另:人格卡跟這一席的 bot 種類不合(見 mouse-errors.log)}" >/dev/null 2>&1 || true
      elif [ -n "$_fit" ]; then
        # 10-12 Bot 開發第 16 件:卡寫給別家 bot ⇒ 照綁,但要讓人看得到(只警告)
        # 審查低 2:不用 cut -c 切(C locale 會切半中文字),只拿掉開頭的「bind-one: 」
        env -u TMUX tmux display-message -d 6000 ${_target[@]+"${_target[@]}"} \
          "$(printf '%s' "$_out" | grep -m1 'bind-one: ⚠ 人格卡' | sed 's/^bind-one: //' | sed 's/#/##/g')" >/dev/null 2>&1 || true
      fi
      return 0
      ;;
    new-project-do)
      # 包 O:新開專案 — 建卡+憲法候選+綁席 project_uid;免費;approved 恆 false
      local _np="$EZ2_AIB_ROOT/tools/project/new_project.py" _out _rc _uid _msg
      if [ ! -f "$_np" ]; then
        run env -u TMUX tmux display-message -d 4000 ${_target[@]+"${_target[@]}"} \
          "新開專案失敗:找不到 new_project.py"
        return 0
      fi
      if [ "$DRY" = "1" ]; then
        run "$EZ2_PY3" "$_np" --seat "$BIND_SEAT" --state-root "$BIND_STATE_ROOT" --cwd "$EZ2_AIB_ROOT" --dry-run
        return 0
      fi
      _out="$("$EZ2_PY3" "$_np" --seat "$BIND_SEAT" --state-root "$BIND_STATE_ROOT" --cwd "$EZ2_AIB_ROOT" 2>&1)"; _rc=$?
      printf '%s bind-menu new-project-do seat=%s rc=%s\n%s\n' "$(date '+%F %T')" \
        "$BIND_SEAT" "$_rc" "$_out" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      _uid="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("project_uid") or "")
except Exception:
 print("")' "$_out" 2>/dev/null || true)"
      if [ "$_rc" -eq 0 ] && [ -n "$_uid" ]; then
        _msg="新開專案完成: $_uid(憲法候選未核准)"
      else
        _msg="新開專案失敗(rc=$_rc):$(printf '%s' "$_out" | tr '\n' ' ' | cut -c1-100)"
      fi
      env -u TMUX tmux display-message -d 5000 ${_target[@]+"${_target[@]}"} "$_msg" >/dev/null 2>&1 || true
      return 0
      ;;
    sop-hint)
      # 只印指引,永不造卡/造夾(總綱 §二-E:新建角色卡=導向人格提取 SOP 首煉分支,
      # ez2-role-create.js 是唯一合法寫入器且要人核准)
      run env -u TMUX tmux display-message -d 6000 ${_target[@]+"${_target[@]}"} \
        "新建人格請走人格提取 SOP(角色庫/_人格建立SOP.md → ez2-role-create.js,人核准後 card_registry.py scan 重掃);此處只綁既有卡"
      return 0
      ;;
    *) return 0 ;;
  esac
  [ "${#_items[@]}" -gt 0 ] || return 0
  if [ "$DRY" = "1" ]; then
    run env -u TMUX tmux display-menu -O -M -T "$_title" \
      ${_target[@]+"${_target[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} "${_items[@]}"
  else
    env -u TMUX tmux display-menu -O -M -T "$_title" \
      ${_target[@]+"${_target[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} "${_items[@]}" >/dev/null 2>&1 || true
  fi
  return 0
}

# 鎖檔在這個登入的家目錄。tmux 的 wait-for 鎖在程式被砍之後還留在伺服器裡，
# 下一回開席會無限等。目錄鎖看得到持有者；人死了下一回立刻清掉。
mouse_install_lock_dir() {
  if [ -n "${EZ2_MOUSE_INSTALL_LOCK:-}" ]; then
    printf '%s\n' "$EZ2_MOUSE_INSTALL_LOCK"
  else
    printf '%s\n' "${HOME}/.ez2-aib/aib-mouse-install.lockdir"
  fi
}

acquire_install_lock() {
  local dir pid i
  dir="$(mouse_install_lock_dir)"
  mkdir -p "$(dirname "$dir")"
  i=0
  while [ "$i" -lt 20 ]; do
    if mkdir "$dir" 2>/dev/null; then
      printf '%s\n' "$$" > "$dir/pid" || true
      INSTALL_LOCKED=1
      if [ -n "${MOUSE_BIND_LOG:-}" ]; then
        printf '%s\n' "mouse-install-lock acquire" >> "$MOUSE_BIND_LOG"
      fi
      return 0
    fi
    pid="$(cat "$dir/pid" 2>/dev/null || true)"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$dir"
      continue
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "mouse-ctl: 滑鼠安裝鎖等超過 20 秒（持有者 ${pid:-不明}）。這次不裝。" >&2
  return 1
}

release_install_lock() {
  if [ "$INSTALL_LOCKED" = "1" ]; then
    INSTALL_LOCKED=0
    rm -rf "$(mouse_install_lock_dir)"
    if [ -n "${MOUSE_BIND_LOG:-}" ]; then
      printf '%s\n' "mouse-install-lock release" >> "$MOUSE_BIND_LOG"
    fi
  fi
}

# tmux root key table 是 server-global。第一次安裝時將現有綁定的 command list
# 原樣保存在 server option，重裝時重用，避免把 AIB wrapper 一層層包回
# 自己。原來沒綁定的事件才使用 send-keys -M，與 tmux 的未綁定傳遞
# 相容。--dry-run 不讀寫真 server，只顯示 tmux 預設選單類型。
# root 表裡某個事件目前綁的 command list。**單一真相源**:安裝器與
# test-hover-mouse 的真環境測試都走這一個形狀,tmux 再改輸出格式只改這裡。
#
# ★ 為什麼不用 `list-keys -T root <EVENT>` 指名查詢(08-15 CI 實測,不是推測):
#   tmux 3.7b 上指名查詢**回空字串而且 rc=0**,而同一個 server 的全表 dump
#   拿得到那一行。本機 3.6a 兩種都拿得到,所以這個缺陷在開發機上不現形。
#     tmux -V: 3.7b
#     list-keys -T root MouseDown3Pane  → rc=0 stdout='' stderr=''
#     list-keys -T root(全表)          → rc=0,24 行,MouseDown3Pane 在裡面
#   後果:安裝器以為「本來就沒有內建右鍵選單」,存下 send-keys -M,把使用者
#   的 tmux 內建右鍵選單**安靜地弄不見**——沒有錯誤訊息,升級 tmux 才發作。
#
# ★ 第二個坑,與上面獨立(只修一個仍然錯):3.7b 全表輸出是
#   `bind-key  -T root …`(**bind-key 後面兩個空格**,多了一欄),而舊碼的
#   前綴比對寫死單空格 `"bind-key -T root $EVENT "`,即使查得到也對不上。
#   故此處不比對前綴,改成「找 `-T root <EVENT>` 這三個欄位,取其後全部」,
#   對 flag 欄位數、空白數、以及未來多出來的欄位都不敏感。
#
# rc:0=查到綁定(內容在 stdout)/1=表讀得到但這個事件沒綁(這一版就是沒有)
#    /2=**表根本讀不到**(查詢方式失效)。★1 與 2 的處置完全相反,舊碼把
#    兩者都表現成空字串,這正是它會靜默弄壞東西的原因。
root_table_binding() {
  local EVENT="$1" TABLE RC
  TABLE="$(env -u TMUX tmux list-keys -T root 2>/dev/null)"
  RC=$?
  # 表空視同讀不到:root 表在預設 tmux 上不可能是空的。若有人真的把 root
  # 表清光,這裡會多留一行痕跡而行為不變(誤留痕 < 靜默弄壞)。
  if [ "$RC" -ne 0 ] || [ -z "$TABLE" ]; then
    return 2
  fi
  printf '%s\n' "$TABLE" | awk -v ev="$EVENT" '
    match($0, "-T[ \t]+root[ \t]+" ev "[ \t]+") {
      printf "%s", substr($0, RSTART + RLENGTH); found = 1; exit
    }
    END { exit(found ? 0 : 1) }'
}

preserved_binding() {
  local EVENT="$1"
  local SLUG SAVED_OPT MARKER_OPT SAVED ORIGINAL LOOKUP_RC
  if [ "$DRY" = "1" ]; then
    case "$EVENT" in
      MouseDown3Pane)
        printf '%s' "display-menu -T '#[align=centre]#{pane_index} (#{pane_id})' -t = -x M -y M"
        ;;
      MouseDown3Status)
        printf '%s' "display-menu -T '#[align=centre]#{window_index}:#{window_name}' -t = -x W -y W"
        ;;
      MouseDown3StatusLeft)
        printf '%s' "display-menu -T '#[align=centre]#{session_name}' -t = -x M -y W"
        ;;
      *) printf '%s' "send-keys -M" ;;
    esac
    return 0
  fi

  SLUG="$(printf '%s' "$EVENT" | tr '[:upper:]' '[:lower:]')"
  SAVED_OPT="@aib_mouse_original_${SLUG}"
  MARKER_OPT="@aib_mouse_original_${SLUG}_saved"
  SAVED="$(env -u TMUX tmux show-option -gqv "$MARKER_OPT" 2>/dev/null || true)"
  if [ "$SAVED" = "1" ]; then
    ORIGINAL="$(env -u TMUX tmux show-option -gqv "$SAVED_OPT" 2>/dev/null || true)"
  else
    ORIGINAL="$(root_table_binding "$EVENT")"
    LOOKUP_RC=$?
    if [ "$LOOKUP_RC" -eq 2 ]; then
      # 查詢方式失效 ≠ 這一版沒有這個綁定。**不落盤**:落了就等於把
      # 「我沒查到」永久記成「本來就沒有」,下次安裝連重試的機會都沒有
      # (marker 一設就再也不讀真 server)。本次仍用 send-keys -M 讓安裝
      # 走得完,但留痕,並保留下次修好後自動救回來的路。
      ORIGINAL="send-keys -M"
      echo "mouse-ctl: 讀不到 tmux root 綁定表,$EVENT 的內建綁定未保存(本次以 send-keys -M 代替,未落盤)" >&2
      echo "$(date '+%F %T') preserved-binding lookup failed: $EVENT (tmux list-keys -T root 無輸出)" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
    else
      # rc=0 查到內容,rc=1 這一版真的沒綁——兩者都是**知道**,可以落盤。
      [ "$LOOKUP_RC" -eq 0 ] || ORIGINAL="send-keys -M"
      env -u TMUX tmux set-option -gq "$SAVED_OPT" "$ORIGINAL"
      env -u TMUX tmux set-option -gq "$MARKER_OPT" "1"
    fi
  fi
  [ -n "$ORIGINAL" ] || ORIGINAL="send-keys -M"
  printf '%s' "$ORIGINAL"
}

# ★★ 09-14 20:07 事故根因(root a seat 事故單 INCIDENT-4c24-closed-by-x-from-b556):
#   外層 default 伺服器的滑鼠綁定所有艙共用一份,run-shell 起的本殼環境是**伺服器第一個艙**的
#   (EZ2_AIB_INSTANCE / _HOME / EZ2_AIHUB_SOCKET / EZ2_COCKPIT_STATE_DIR 全指那一艙)。
#   從 b556 點 [X]:關窗用了 session 的 hub(對),殺前重驗 / 席退役 / 收艙 / 日誌卻讀 env(錯)⇒
#   重驗拿別艙同窗號判「已恢復活動」拒殺、席位檔搬錯艙、最後整個 4c24 被收掉。
#   ⇒ 破壞性路徑一開頭就從**被點的 session** 重導艙身份並 export,之後所有下游只讀這一份;
#     session 取不到艙身份 ⇒ 不動手(寧可沒關,也不關錯艙)。env 不得參與作答。
aib_cabin_env_from_session() {
  [ "$DRY" != "1" ] || return 0
  local sess="$1" inst hub sd home
  [ -n "$sess" ] || return 1
  inst="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_instance 2>/dev/null || true)"
  hub="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_hub_socket 2>/dev/null || true)"
  sd="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_state_dir 2>/dev/null || true)"
  [ -n "$inst" ] && [ -n "$hub" ] || return 1
  home="${EZ2_AIB_INSTANCE_ROOT:-$HOME/.ez2-aib/instances}/$inst"
  export EZ2_AIB_INSTANCE="$inst" EZ2_AIB_INSTANCE_HOME="$home" EZ2_AIHUB_SOCKET="$hub" EZ2_TMUX_SESSION="$sess"
  export EZ2_COCKPIT_STATE_DIR="${sd:-$home/cockpit}"
  return 0
}

case "$MODE" in
  leftover-confirmed)
    # 09-30 右欄第 3 頁「離艙」:確認選單按了「確定」才到這裡。要收哪一個寫在 leftover-pending.txt
    # (一行:<艙>|@<窗> 或 ALL),不放在選單指令字串裡 —— 那裡要過兩層引號。
    PENDING="${EZ2_COCKPIT_STATE_DIR:-/tmp}/leftover-pending.txt"
    [ -f "$PENDING" ] || exit 0
    KEY="$(head -1 "$PENDING")"
    rm -f "$PENDING"
    # 先掛「正在關閉」記號並踢右欄:這一頁立刻換成那句話,不畫舊清單、也不閃「量測中」(the maintainer 09-30)。
    date +%s > "${EZ2_COCKPIT_STATE_DIR}/leftover-closing" 2>/dev/null || true
    date +%s > "${EZ2_COCKPIT_STATE_DIR}/right-kick" 2>/dev/null || true
    if [ "$KEY" = "ALL" ]; then
      OUT="$("$EZ2_PY3" "$COCKPIT/leftover_seats.py" close-all 2>>"${EZ2_COCKPIT_STATE_DIR}/mouse-errors.log" || true)"
      MSG="已收 ${OUT:-0} 席"
    else
      OUT="$("$EZ2_PY3" "$COCKPIT/leftover_seats.py" close --key "$KEY" 2>>"${EZ2_COCKPIT_STATE_DIR}/mouse-errors.log" || true)"
      MSG="$OUT"
    fi
    printf '%s leftover-confirmed key=%s out=%s\n' "$(date '+%H:%M:%S')" "$KEY" "$OUT" >> "${EZ2_COCKPIT_STATE_DIR}/mouse-errors.log" 2>/dev/null || true
    env -u TMUX tmux display-message -d 3000 "離艙:${MSG}" >/dev/null 2>&1 || true
    rm -f "${EZ2_COCKPIT_STATE_DIR}/leftover-seats.json" 2>/dev/null || true   # 下一幀重新盤點
    # 收完再碰一次記號:之後盤點出來的清單(時刻晚於它)才算收完之後的,那時右欄自己回到原頁。
    touch "${EZ2_COCKPIT_STATE_DIR}/leftover-closing" 2>/dev/null || true
    date +%s > "${EZ2_COCKPIT_STATE_DIR}/right-kick" 2>/dev/null || true
    exit 0
    ;;

  usage-remove-confirmed)
    PENDING="${EZ2_COCKPIT_STATE_DIR:-/tmp}/usage-remove-pending.tsv"
    if [ ! -f "$PENDING" ]; then
      echo "usage-remove-confirmed: no pending" >> "${EZ2_COCKPIT_STATE_DIR:-/tmp}/mouse-errors.log" 2>/dev/null || true
      exit 0
    fi
    ACCT="$(cut -f1 "$PENDING" | head -1)"
    EM="$(cut -f2 "$PENDING" | head -1)"
    SEQ="$(cut -f3 "$PENDING" | head -1)"
    rm -f "$PENDING"
    case "$ACCT" in ""|*[!0-9a-fA-F]*) exit 0 ;; esac
    OUT="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" remove "$ACCT" 2>>"${EZ2_COCKPIT_STATE_DIR}/mouse-errors.log" || true)"
    printf '%s usage-remove-confirmed acct=%s seq=%s out=%s\n' "$(date '+%H:%M:%S')" "$ACCT" "${SEQ:-}" "$OUT" >> "${EZ2_COCKPIT_STATE_DIR}/mouse-errors.log" 2>/dev/null || true
    if [ "$OUT" = "ok" ]; then
      if [ -n "$SEQ" ]; then
        env -u TMUX tmux display-message -d 2000 "已刪除 ${SEQ}：${EM}" >/dev/null 2>&1 || true
      else
        env -u TMUX tmux display-message -d 2000 "已刪除：${EM}" >/dev/null 2>&1 || true
      fi
    else
      env -u TMUX tmux display-message -d 3000 "刪除失敗（${OUT:-missing}）" >/dev/null 2>&1 || true
    fi
    date +%s > "${EZ2_COCKPIT_STATE_DIR}/right-kick" 2>/dev/null || true
    exit 0
    ;;

  install)
    if [ "$DRY" != "1" ]; then
      # 兩次開艙同時改同一台 tmux 的滑鼠綁定會互相蓋掉。鎖住這一段。
      # 不用 tmux wait-for：持有者被砍時那把鎖不會還，之後每次新開席都無限等。
      acquire_install_lock || exit 1
      trap 'release_install_lock' EXIT
      trap 'release_install_lock; exit 130' HUP INT TERM
    fi
    # 放開才處理點擊,拖曳選字才留得住。右欄捲軸例外,見下面 MouseDown/Drag。
    # -t= 強制 format 取滑鼠底下那一格,不能抓到當下聚焦的中欄。
    # E2.16 補完(08-13 the maintainer 直令:左上/右上首行標籤要可點):條件擴 left-tabs/
# right-tasks——這兩個標記的框先前點了完全沒有 handler(條件不含=預設放行)。
# ★role!=hub 是結構守衛:內容標記會飄到中欄(視圖 respawn 洗標,08-13 監看實證),
# 一旦條件吃掉中欄的 MouseUp,claude 的滑鼠事件就斷一半。role 永不飄,擋在最外層。
MOUSE_CONDITION='#{&&:#{!=:#{@aib_role},hub},#{&&:#{==:#{@aib_cockpit},1},#{||:#{||:#{||:#{==:#{@aib_pane},tabs},#{==:#{@aib_pane},ai-list}},#{||:#{==:#{@aib_pane},left-tabs},#{==:#{@aib_pane},right-tasks}}},#{||:#{||:#{==:#{@aib_pane},pending-review},#{==:#{@aib_role},review}},#{==:#{@aib_pane},hub-frame}}}}}'
    HUB_CLICK='#{&&:#{==:#{@aib_cockpit},1},#{==:#{@aib_role},hub}}'
    MOUSE_SCRIPT_Q=""
    MOUSE_SCRIPT_PATH="$(stable_mouse_ctl_path)"
    MOUSE_SCRIPT_DIR="$(dirname "$MOUSE_SCRIPT_PATH")"
    if [ "$MOUSE_SCRIPT_PATH" != "$HERE/mouse-ctl.sh" ]; then
      echo "mouse-ctl: 滑鼠綁定指向穩定入口 $MOUSE_SCRIPT_PATH(本樹 $HERE 不是入口;開席者的樹砍掉時綁定才不會跟著死)" >&2
    fi
    printf -v MOUSE_SCRIPT_Q '%q' "$MOUSE_SCRIPT_PATH"
    printf -v MOUSE_LOG_Q '%q' "$EZ2_COCKPIT_STATE_DIR/aib-open.log"
    MOUSE_COMMAND="display-message -d 1500 'CLICK #{@aib_pane} #{mouse_x},#{mouse_y}' ; run-shell -b -t= 'bash $MOUSE_SCRIPT_Q --click #{q:@aib_pane} #{q:mouse_x} #{q:mouse_y} #{q:session_name} --role #{q:@aib_role} >>$MOUSE_LOG_Q 2>&1'"
    # 10-18:右欄重繪時 @aib_pane 會短暫撤下(發布時序,見 pane-right-ai.sh),那一下原本落到 else = 白點、要點兩下。
    #   右欄的 @aib_role=right 不會撤 ⇒ 認得出是右欄,交給 click-wait.sh 等 marker 掛回再照新 rowmap 處理(最多 1.5 秒)。
    RIGHT_GAP='#{&&:#{==:#{@aib_cockpit},1},#{&&:#{==:#{@aib_role},right},#{==:#{@aib_pane},}}}'
    printf -v CLICK_WAIT_Q '%q' "$MOUSE_SCRIPT_DIR/click-wait.sh"
    RIGHT_GAP_COMMAND="run-shell -b -t= 'bash $CLICK_WAIT_Q #{q:pane_id} #{q:mouse_x} #{q:mouse_y} #{q:session_name} >>$MOUSE_LOG_Q 2>&1'"
    # 中欄放開也不送滑鼠序列。右欄等點擊仍走原來的條件;右欄空窗走 click-wait;其他照舊送序列。
    run env -u TMUX tmux bind-key -n MouseUp1Pane \
      if-shell -F -t= "$HUB_CLICK" \
        "send-keys -M -t =" \
        "if-shell -F -t= \"$MOUSE_CONDITION\" \"$MOUSE_COMMAND\" \"if-shell -F -t= \\\"$RIGHT_GAP\\\" \\\"$RIGHT_GAP_COMMAND\\\" \\\"send-keys -M -t =\\\"\""

    # 先看這台機器有沒有剪貼簿工具。沒有就不要裝 copy-pipe，否則放開拖曳會失敗。
    COPY_CMD=""
    case "$(uname -s)" in
      Darwin)
        if command -v pbcopy >/dev/null 2>&1; then
          # cockpit 的最外層 tmux 可能是從沒有 locale 的 GUI/背景
          # 環境啟動。pbcopy 沒有 locale 時會退回 C 編碼:英數能複製,
          # 但中文或 icon 會失敗。命令本身固定 UTF-8,不依賴 server env。
          COPY_CMD="env LC_ALL=en_US.UTF-8 /usr/bin/pbcopy"
        fi
        ;;
      *)
        if command -v wl-copy >/dev/null 2>&1; then
          COPY_CMD="wl-copy"
        elif command -v xclip >/dev/null 2>&1; then
          COPY_CMD="xclip -selection clipboard -in"
        fi
        ;;
    esac

    # 拖曳選字仍由最外層直接進 copy-mode。右欄那條畫出來的捲軸不要進複製模式,
    # 也不要把按下的滑鼠序列送進窗格(會打進中欄的巢狀 tmux,整艙退回終端機,
    # shell 還會把序列當成指令,對話框底下冒 syntax error)。
    # -t = 釘死滑鼠底下那一格。沒有 -t = 時,複製模式和 send-keys 會抓到
    # 當下聚焦的中欄。
    RIGHT_MOUSE='#{&&:#{==:#{@aib_cockpit},1},#{==:#{@aib_pane},ai-list}}'
    BAR_ARM="bash $MOUSE_SCRIPT_Q --bar-arm #{q:mouse_x} #{q:mouse_y} #{q:session_name}"
    # 拖一下會連送很多格。只把游標的列寫進檔，不啟動這支大程式。
    BAR_DRAG="printf '%s\\n' #{q:mouse_y} > #{@aib_state_dir}/.scroll-drag.y; exit 0"
    # 點右欄(含捲軸)都不要 select 右欄,也不要 send-keys。焦點留在右欄的話,
    # 接著打字會印在捲軸上;滑鼠序列打進中欄會變成 syntax error。
    printf -v FOCUS_HUB_Q '%q' "$MOUSE_SCRIPT_DIR/focus-hub-pane.sh"
    # 中欄按下只選格,不送滑鼠序列(序列進輸入框會變黃字)。
    run env -u TMUX tmux bind-key -n MouseDown1Pane \
      if-shell -F -t= "$RIGHT_MOUSE" \
        "run-shell -b -t= 'bash $MOUSE_SCRIPT_Q --bar-arm #{q:mouse_x} #{q:mouse_y} #{q:session_name}$RUNSHELL_REDIR; bash $FOCUS_HUB_Q #{window_id}$RUNSHELL_REDIR'" \
        "if-shell -F -t= \"$HUB_CLICK\" \"select-pane -t = ; send-keys -M -t =\" \"select-pane -t = ; send-keys -M -t =\""
    # 10-08 the maintainer 裁「中欄要能內層捲動」:中欄的拖曳交給內層 tmux(它看得到對話歷史;先用滾輪捲上去再拖就選得到),
    #   不再由外層 copy-mode 只選當下畫面。判準看格子(@aib_role=hub),**不看 mouse_any_flag**:中欄跑的是內層 attach
    #   client,那個旗標恆開,10-01 起外層拖曳一律不看它(tests/test-mouse.py outer_mouse_any_outside_non_aib_fallback)。
    #   外層這格已在 copy-mode(使用者在外層捲過)⇒ 照舊外層選,不把一半的手勢丟進內層。
    #   放開(MouseDragEnd1Pane)外層不在 copy-mode 時本來就 send-keys -M -t = 轉給同一格 ⇒ 按下/拖/放三個事件同在內層。
    #   -t = 照 09-23 釘死滑鼠底下那一格(沒有 -t = 會抓到聚焦的格,中欄 attach 退出、整艙掉回終端機)。
    HUB_DRAG='#{&&:#{==:#{@aib_cockpit},1},#{&&:#{==:#{@aib_role},hub},#{!=:#{pane_in_mode},1}}}'
    run env -u TMUX tmux bind-key -n MouseDrag1Pane \
      if-shell -F -t= "$RIGHT_MOUSE" \
        "if-shell -t= \"test -f #{@aib_state_dir}/scrollbar-grab\" \"run-shell -b -t= '$BAR_DRAG$RUNSHELL_REDIR'\" \"copy-mode -M -t =\"" \
        "if-shell -F -t= \"$HUB_DRAG\" \"send-keys -M -t =\" \"copy-mode -M -t =\""
    if [ -n "$COPY_CMD" ]; then
      run env -u TMUX tmux bind-key -n MouseDragEnd1Pane \
        if-shell -t= "test -f #{@aib_state_dir}/scrollbar-grab" \
          "run-shell true" \
          "if-shell -F \"#{pane_in_mode}\" \"send-keys -X copy-pipe-and-cancel \\\"$COPY_CMD\\\"\" \"send-keys -M -t =\""
    else
      run env -u TMUX tmux bind-key -n MouseDragEnd1Pane \
        if-shell -t= "test -f #{@aib_state_dir}/scrollbar-grab" \
          "run-shell true" \
          "send-keys -M -t ="
    fi

    # 雙擊待審列 ⇒ 點選那一則並直接開編輯框(展開全文、可改)。只認待審框;
    # 其他格照安裝前的原綁定(tmux 預設雙擊=選字複製),不改別人的行為。
    DBL_CONDITION='#{&&:#{==:#{@aib_cockpit},1},#{||:#{==:#{@aib_pane},pending-review},#{==:#{@aib_role},review}}}'
    DBL_FALLBACK="$(preserved_binding DoubleClick1Pane)"
    # list-keys 印的多指令用 ` \; ` 分隔,那是 bind-key 參數層的寫法;當成 if-shell 的字串再解析時
    # `\;` 不是分隔符(隔離 socket 實測:too many arguments / argument 5 must be "string")⇒
    # 別的格雙擊選字會整個壞掉。換成 ` ; ` 才是同一串指令(實測 else 分支兩道都跑)。
    DBL_FALLBACK="$(printf '%s' "$DBL_FALLBACK" | sed 's/ \\; / ; /g')"
    DBL_COMMAND="run-shell -t= 'bash $MOUSE_SCRIPT_Q --dblclick #{q:@aib_pane} #{q:mouse_x} #{q:mouse_y} #{q:session_name} --role #{q:@aib_role} >>$MOUSE_LOG_Q 2>&1'"
    run env -u TMUX tmux bind-key -n DoubleClick1Pane \
      if-shell -F -t= "$DBL_CONDITION" \
        "$DBL_COMMAND" \
        "$DBL_FALLBACK"
    # ★ review r7 m6:滾輪的「非 AIB 格」fallback 要用**安裝前**使用者自己的那條(例如他在 .tmux.conf 綁的
    #   WheelUpPane),所以在下面 pane-pending-review.sh --bind-keys 蓋掉 root 表**之前**先存起來
    #   (preserved_binding 存在 server option,之後重裝一律讀存下來的那份 ⇒ 重裝不會一次比一次深)。
    #   存下來的若是 AIB 自己的(舊版 AIB 裝過、或待審框先平綁過)⇒ 下面照舊退回 tmux 內建形。
    #   查詢方式失效(preserved_binding 沒落盤 marker)⇒ 不信那份代用的 send-keys -M,也退回內建形。
    _WHEEL_ORIG_UP=""
    _WHEEL_ORIG_DOWN=""
    if [ "$DRY" != "1" ]; then
      _WHEEL_ORIG_UP="$(preserved_binding WheelUpPane)"
      [ "$(env -u TMUX tmux show-option -gqv @aib_mouse_original_wheeluppane_saved 2>/dev/null || true)" = "1" ] \
        || _WHEEL_ORIG_UP=""
      _WHEEL_ORIG_DOWN="$(preserved_binding WheelDownPane)"
      [ "$(env -u TMUX tmux show-option -gqv @aib_mouse_original_wheeldownpane_saved 2>/dev/null || true)" = "1" ] \
        || _WHEEL_ORIG_DOWN=""
    fi
    # 點待審列之後上/下鍵換列。綁定條件只認作用中的待審框,不搶中欄對話。
    # 10-07 a seat:帶 AIB_PENDING_MOUSE_FIXING=1 —— 我們正拿著安裝鎖;--bind-keys 的 _ensure_pending_clicks 若一時讀不到
    #   MouseUp1Pane(list-keys 空)會回頭叫 --install,那個內層 install 等的就是我們手上的鎖 ⇒ 自己等自己滿 20 秒,
    #   同時開的另一席也跟著等滿放棄 ⇒ cockpit-full.sh --ensure rc=1、開席中止(真 tmux 測試兩個併發開席那格)。
    #   假 tmux 實量:21.1 秒 → 0.61 秒。pane-pending-review.sh 自己叫 --install 時也帶同一個變數擋迴圈。
    if [ "$DRY" = "1" ]; then
      run env AIB_PENDING_MOUSE_FIXING=1 bash "$COCKPIT/pane-pending-review.sh" --bind-keys
    else
      AIB_PENDING_MOUSE_FIXING=1 bash "$COCKPIT/pane-pending-review.sh" --bind-keys >/dev/null 2>&1 || true
    fi
    # 274 (e)3:滾輪只吃右欄。條件=本艙且 @aib_pane=ai-list(右欄採集格);其他格走原本那條。
    # ★★ 必須排在 pane-pending-review.sh --bind-keys **之後**(09-17 a seat 真 tmux 沙箱實量,艦長裁 A):
    #   它無條件重綁 WheelUp/DownPane(待審框才吃,否則退 tmux 預設)——排在它前面就被蓋掉,右欄滾輪永遠到不了
    #   這裡,而 dry-run 的安裝計畫看起來完全正確(test-mouse 只驗有沒有下這條命令,不驗最後誰留在表上)。
    #   (09-17 那時 fallback 用當下 root 表那條 = 待審框那條;10-01 起待審框/中欄/hub-frame 的條件直接寫進下面的鏈,
    #   fallback 只給非 AIB 格。)★ review r7 m6:fallback 改用上面在待審框重綁**之前**存下的 preserved_binding
    #   (使用者安裝前自己的那條);它是 AIB 自己的、或沒存成功 ⇒ tmux 內建形。當下 root 表那條不再拿來用
    #   (待審框剛平綁過,那條一定是 AIB 自己的)。
    #   重裝不套娃:fallback 只會是「存下的使用者原綁定」或「tmux 內建形」,兩者都不含 AIB 的鏈 ⇒ root 表上那條
    #   每次重裝逐字相同(test-right-fold-wheel-274-real-tmux W2 裝兩次後仍是同一條)。
    WHEEL_CONDITION='#{&&:#{==:#{@aib_cockpit},1},#{||:#{==:#{@aib_pane},ai-list},#{==:#{@aib_pane},hub-frame}}}'
    for _wdir in Up Down; do
      _wlower="$(printf '%s' "$_wdir" | tr 'A-Z' 'a-z')"
      _wfallback=""
      if [ "$DRY" != "1" ]; then                                    # dry-run 沒有 server 可查,計畫用下面的預設形
        if [ "$_wdir" = "Up" ]; then _wfallback="$_WHEEL_ORIG_UP"; else _wfallback="$_WHEEL_ORIG_DOWN"; fi
      fi
      _wfallback="$(printf '%s' "$_wfallback" | sed 's/ \\; / ; /g')"
      # ★ 10-01 a seat(review:_wfallback 算了沒用 ⇒ 非 AIB 格滾輪全死):不是 AIB 中欄/待審框/hub-frame/右欄的格
      #   (含共用 default 伺服器上**非 AIB 的 session**)一律走這條 fallback,不再交給 scroll-hub-wheel.sh
      #   (它對那些格直接 exit ⇒ 滾輪沒反應、進不了 copy-mode)。
      #   ★ review r7 m6:fallback = 安裝前存下的使用者原綁定(見上面 _WHEEL_ORIG_*);那份若是 AIB 自己裝的
      #   (舊版 AIB 裝過的整條鏈、或待審框平綁的那條)或沒存成功 ⇒ 改用 tmux 內建形,不包進來
      #   (待審框已由下面的條件交給 scroll-hub-wheel.sh 處理;包進來會一次比一次深 = 重裝套娃)。
      #   tmux 3.7c 內建(-f /dev/null 實量):WheelUpPane 如下;WheelDownPane 沒綁 = 事件交給格 = send-keys -M。
      case "$_wfallback" in
        ""|*@aib_*|*scroll-hub-wheel*)
          if [ "$_wdir" = "Up" ]; then
            _wfallback='if-shell -F "#{||:#{alternate_on},#{pane_in_mode},#{mouse_any_flag}}" { send-keys -M } { copy-mode -e }'
          else
            _wfallback='send-keys -M'
          fi
          ;;
      esac
      # ★ 09-30 16:5x the maintainer 截圖:往上滑再往下滑,中欄印出「'bash …scroll-hub-wheel.sh up … 2>&1' returned 1」
      #   (tmux 把 run-shell 非零的整條指令印進格裡)。本檔 exit 0 有 trap 守著,重現 15 次都 0;非零只可能出在
      #   殼那一層(例如 >> 紀錄檔那一步失敗)。滾輪不該在畫面上報錯 ⇒ 整條尾巴補 `|| true`。原因另查。
      # ★ review r1 m11:紀錄檔路徑是**安裝那一艙**的(RUNSHELL_REDIR 在安裝時展開),那個目錄不在時
      #   殼先印「No such file or directory」再輪到 2>&1,tmux 就把那行印進格裡。整條包成 `{ …; } 2>&-`:
      #   外層先關 stderr,開紀錄檔失敗那一句無處可印;開得了的話裡面的 2>&1 照舊把輸出寫進紀錄檔(不是丟掉)。
      #   (不用 2>/dev/null:本檔 run-shell 一律不准重導到 /dev/null,見 test-mouse 那格。sh/bash/zsh 三殼實量同形。)
      # ★ review r2 m8:右欄(ai-list)那條舊寫法 `printf > 佇列檔; exit 0 >>紀錄檔 2>&1`:佇列目錄或紀錄檔目錄
      #   不在時殼印「No such file or directory」並回 1,tmux 把它印進格裡。
      #   ★ 上面 r1 m11 的 `{ …; } 2>&-` **沒有效**(10-01 本機 macOS 實量:sh/bash 開紀錄檔失敗那句照印,只有 zsh 不印;
      #   群組的 2>&- 擋不到群組裡重導失敗的訊息)。兩條一起改成開頭 `exec 2>&-`(整條 run-shell 的 stderr 關掉,
      #   sh/bash/zsh 三殼實量都不印)+ 結尾 `exit 0`;printf 包在 { } 裡帶紀錄檔重導 ⇒ 寫不進佇列那句進紀錄檔,不是丟掉。
      #   狀態根照 cockpit-env.sh / seats_dir_lib 同一式子:EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > $HOME/.ez2-aib(執行期展開)。
      # ★ review r3 I1:上面那種「指令本身帶 >>紀錄檔」的寫法,紀錄檔打不開(RUNSHELL_REDIR 是**安裝那一艙**的
      #   aib-open.log,安裝時就展開寫死;那一艙收掉、目錄不在)時殼會**整條指令跳過**—— exec 2>&- 又把錯誤吞掉
      #   ⇒ 滾輪靜靜地什麼都不做。改成:紀錄檔**執行期**從狀態根 + #{session_name} 算(艙 id = session 名去掉 aib-),
      #   目錄在且寫得進去才 `exec >>紀錄檔 2>&1`,否則把 stdout 關掉(run-shell 有輸出會把格切進 view-mode;
      #   不准 /dev/null,見 test-mouse);主指令**一定跑**,再 exit 0。
      #   (先判「寫得進去」再 exec:/bin/sh 是 POSIX 模式,exec 的重導失敗會讓整個殼直接結束。)
      # ★ review r4 m2:上面「狀態根 + instances/<session 名去掉 aib->/cockpit」對主艙(session aib-main,狀態目錄 $r/cockpit)
      #   與設了 EZ2_COCKPIT_STATE_DIR 的艙都是錯的目錄 ⇒ 佇列寫不進、紀錄檔開不了。改讀那一艙自己的 session 選項
      #   @aib_state_dir(cockpit-full.sh 建艙時設,right-scroll-wheel.sh 早就讀它):佇列與紀錄檔都在那個目錄。
      #   沒設(非 AIB 艙)⇒ d 是空字串 ⇒ 不重導(關 stdout)、佇列那句寫不進(stderr 已關,不印),主指令照跑。
      WHEEL_RUNSHELL_REDIR="d=#{q:@aib_state_dir}; L=\$d/aib-open.log; exec 2>&-; { [ -n \"\$d\" ] && [ -d \"\$d\" ] && [ -w \"\$d\" ] && { [ ! -e \"\$L\" ] || [ -w \"\$L\" ]; } && exec >>\"\$L\" 2>&1; } || exec >&-"   # r4-m2-state-dir
      # 清單頁的滾輪只寫一筆。AIB 中欄(@aib_role=hub)/待審框/hub-frame 交 scroll-hub-wheel.sh 判斷;
      # 其他格走上面的 fallback。
      # ★ 09-24 a8805b106 拿掉巢狀 if-shell 是因為引號對不上 ⇒ 中欄滾輪掉回 send-keys -M、輸入框出黃字。
      #   這裡的內層 if-shell 自己組成一個變數,分支用 tmux 大括號 { … } 包(大括號裡的指令照原樣解析,
      #   不必再跳脫一層雙引號;run-shell 本體仍是單引號,tmux 單引號裡不展開 $、不吃反斜線)。
      #   條件字串只有 #{…},用單引號包。10-01 私有 socket 實量:中欄/待審框/ai-list/一般格四種格各走對的分支。
      printf -v SCROLL_HUB_Q '%q' "$HERE/scroll-hub-wheel.sh"
      WHEEL_HUB_CONDITION='#{&&:#{==:#{@aib_cockpit},1},#{||:#{==:#{@aib_role},hub},#{||:#{==:#{@aib_pane},pending-review},#{==:#{@aib_pane},hub-frame}}}}'
      # ★ review r6 M1:上面那條 fallback 只給**沒有 AIB 標記**的格。AIB 自己畫的格(左欄 left-tabs、下方 tabs、
      #   待審編輯 pending-edit、hub-pad-l/r 等,帶 @aib_pane 或 @aib_role)不在交給 scroll-hub-wheel.sh 的那三種裡,
      #   走 fallback 就會被 tmux 內建形拉進外層 copy-mode ⇒ 標籤列凍住、要按 q 才回來。
      #   那些格滾輪一律不做事(跟 fallback 改動前一樣:當時全交 scroll-hub-wheel.sh,它對這些格直接 exit 0)。
      #   no-op 用 `run-shell -b true`(不帶 -t=,沒有輸出,不會把格切進 view-mode)。
      # ★ review r6 m1:role/pane 用 key=value 傳。#{q:…} 對空值展開成空字串,裸寫的那一格在殼裡**整個消失**,
      #   後面的往前遞補(待審框沒設 @aib_role 時 scroll-hub-wheel.sh 會把 pending-review 讀成 role)。
      #   `role=` 就算值是空的也佔一格;scroll-hub-wheel.sh 兩種寫法都認(舊的位置式呼叫照舊)。
      WHEEL_AIB_MARKED_CONDITION='#{&&:#{==:#{@aib_cockpit},1},#{||:#{!=:#{@aib_pane},},#{!=:#{@aib_role},}}}'   # r6-m1-aib-marked
      WHEEL_OTHER_CMD="if-shell -F -t= '$WHEEL_HUB_CONDITION' { run-shell -b -t= '$WHEEL_RUNSHELL_REDIR; bash $SCROLL_HUB_Q $_wlower #{q:session_name} role=#{q:@aib_role} pane=#{q:@aib_pane} mx=#{mouse_x} my=#{mouse_y}; exit 0' } { if-shell -F -t= '$WHEEL_AIB_MARKED_CONDITION' { run-shell -b true } { $_wfallback } }"
      run env -u TMUX tmux bind-key -n "Wheel${_wdir}Pane" \
        if-shell -F -t= "#{&&:#{==:#{@aib_cockpit},1},#{==:#{@aib_pane},ai-list}}" \
          "run-shell -b -t= '$WHEEL_RUNSHELL_REDIR; printf \"%s\\n\" $_wlower > \"\$d/.scroll-wheel.q/e-\$\$-\$RANDOM\"; exit 0'" \
          "$WHEEL_OTHER_CMD"
    done
    # 內層不要 send-keys -M。那會把滾輪序列打進輸入框,變黃字 syntax error。
    # 外層中欄已經改送 PageUp。這裡只在已經進複製模式時捲幾行。
    # ★ 10-01 the maintainer:API 席(deepseek/nvidia/openrouter… 那格 alt=0 mouse=0)只能靠 copy-mode 捲,
    #   捲完要按 Esc 才能打字 ⇒ 改成「一打字就離開 copy-mode,那個字照樣送進去」。
    #   做法:copy-mode / copy-mode-vi 兩張表綁 Any(只有**沒綁**的鍵才會落到 Any;Esc、q、方向鍵、
    #   PageUp/PageDown、滾輪、拖曳照舊)。send-keys 不帶鍵 = 把「觸發這條綁定的那個鍵」送進格
    #   (tmux 3.7 man send-keys:"If no keys are given and the command is bound to a key, then that key is used")。
    #   ★ 條件 #{mouse_x} 非空 = 這是滑鼠事件 ⇒ 什麼都不做。沒有這道,點一下(MouseUp1Pane 沒綁)、
    #   右鍵、移動都會落到 Any 而把 copy-mode 關掉(10-01 真 tmux 3.7c + 真 client 實量)。
    #   ★ 這裡的 copy-mode 表本身已綁的字(emacs:f g n q r t F N P R T X , ;;vi 更多,h j k l w b / ? …)
    #   仍是 copy-mode 指令,打它們不會離開 —— 那是 tmux 的鍵,不在 Any 的範圍。
    #   ★ 表名是 copy-mode(emacs)與 copy-mode-vi;tmux 3.7 沒有 copy-mode-emacs 這張表
    #   (下面那行 Escape 綁定是舊寫法,emacs 表的 Escape 預設就是 cancel,所以沒壞)。
    # ★ review r1 m10:條件寫成「mouse_x 不等於空字串」,不是裸 #{mouse_x}:if-shell -F 把 "0" 當假,
    #   裸寫法在**第 0 欄**點一下會被當成鍵盤而把 copy-mode 關掉(tmux 3.7c 實量:#{!=:0,}=1、#{!=:,}=0)。
    ANY_EXIT_IF_MOUSE='#{!=:#{mouse_x},}'
    ANY_EXIT_CMD='send-keys -X cancel ; send-keys'
    # ★ the maintainer 09-30 22:0x 裁:內層(AI 格)copy-mode 裡**所有可列印鍵**都要離開 copy-mode 並把那個鍵送進格
    #   —— 不只 copy-mode 沒綁的鍵。代價(the maintainer 選的):AI 格的 copy-mode 搜尋/跳轉鍵(/ ? n N f t q g G h j k l w b …)
    #   全部失效,只剩方向鍵、PageUp/PageDown、Home/End、Escape(cancel)與滾輪在 copy-mode 裡照舊。
    #   逐鍵綁(Any 只接得到「表上沒綁」的鍵,copy-mode 表已綁的字落不到 Any)。鍵名照 tmux 3.7 man KEY BINDINGS:
    #   空白是 Space;分號要寫成 \;(傳給 tmux 的 argv 是字面反斜線+分號);其餘標點原字。另加常用 C- 鍵。
    #   鍵盤事件不必再判滑鼠(那道只給 Any)。外層 default server 一格都不動。tmux 3.7c 沙箱實量 104 鍵全綁得上。
    # ★ review r2 m7(依 the maintainer「所有可列印鍵」收窄 C- 鍵):
    #   C-a C-e C-k C-s C-r = 離開+送鍵(行首/行尾/刪到行尾/搜尋是打字時常按的);
    #   C-c = **只**離開 copy-mode,不送進 AI(送進去會中斷那一席正在跑的工作);
    #   C-d C-u C-w 不綁離開(留 tmux 預設:vi 表的半頁捲動、emacs 表的複製;沒有預設的那幾個只離開,見 r4 m1);
    #   copy-mode-vi 另外不綁 C-e(vi 的往下捲一行)。
    #   ★ dry-run 計畫與真裝走**同一份清單、同一個迴圈**(_inner_copy_bind 分兩條路),尺驗計畫就是驗真裝的那份。
    ANY_EXIT_KEYS=()
    for _k in {a..z} {A..Z} {0..9}; do ANY_EXIT_KEYS+=("$_k"); done
    ANY_EXIT_KEYS+=('!' '"' '#' '$' '%' '&' "'" '(' ')' '*' '+' ',' '-' '.' '/' ':' '\;' '<' '=' '>' '?' '@'
                    '[' '\' ']' '^' '_' '`' '{' '|' '}' '~' Space C-a C-e C-k C-s C-r)
    ANY_EXIT_VI_SKIP=C-e              # copy-mode-vi 不綁的離開鍵(vi 自己要用;字面比對,不走 glob —— 清單裡有 * ? [)
    ANY_CANCEL_ONLY_KEYS=(C-c)        # 只離開、不送鍵
    _inner_copy_bind() {  # 內層 server 一條 bind-key:dry-run 印出來,真裝執行(單條失敗不擋其餘)
      if [ "$DRY" = "1" ]; then
        run env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key "$@"
      else
        env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key "$@" >/dev/null 2>&1 || true
      fi
    }
    # ★ review r3 m1:C-d C-u C-w(兩張表)與 vi 的 C-e「不綁」只對新裝有效 —— 舊版裝過的內層 server 上它們還是
    #   「離開+送鍵」,重裝不會自己變回來。安裝時明確還原成 tmux 預設(tmux 3.7c `tmux -f /dev/null list-keys` 實量):
    #   copy-mode:C-w = copy-pipe-and-cancel(沒有剪貼簿工具時用 copy-selection-and-cancel,見下)。
    #   copy-mode-vi:C-d = halfpage-down、C-u = halfpage-up、C-e = scroll-down。
    # ★ review r4 m1:tmux 預設**沒有**綁的那三個(copy-mode 的 C-d、C-u,copy-mode-vi 的 C-w)不能 unbind ——
    #   表上有 Any 時,沒綁的鍵會落到 Any =「離開+把那個鍵送進 AI」⇒ C-d(EOF,可能讓 CLI 結束)、C-u(清掉輸入框)
    #   照樣送進去,unbind 等於沒還原。改綁成「只離開 copy-mode、不送鍵」(同 C-c)。
    #   ★ 與上面的剪貼簿判準一致(COPY_CMD):這台機器沒有剪貼簿工具 ⇒ 本檔一條 pipe 綁定都不裝,
    #     C-w 改還原成不 pipe 的同義指令 copy-selection-and-cancel(一樣複製進 tmux 緩衝區再離開)。
    if [ -n "$COPY_CMD" ]; then
      _cw_default=copy-pipe-and-cancel
    else
      _cw_default=copy-selection-and-cancel
    fi
    ANY_RESTORE_BIND=("copy-mode C-w $_cw_default" "copy-mode-vi C-d halfpage-down"
                      "copy-mode-vi C-u halfpage-up" "copy-mode-vi C-e scroll-down"
                      "copy-mode C-d cancel" "copy-mode C-u cancel" "copy-mode-vi C-w cancel")   # r4-m1-cancel-only
    if [ -n "${EZ2_AIHUB_SOCKET:-}" ]; then
      _inner_live=0
      if [ "$DRY" = "1" ]; then
        run env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -n WheelUpPane \
          if-shell -F "#{pane_in_mode}" "send-keys -X scroll-up" "copy-mode -e"
        run env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -n WheelDownPane \
          if-shell -F "#{pane_in_mode}" "send-keys -X scroll-down" "run-shell true"
        _inner_live=1
      elif env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" has-session >/dev/null 2>&1; then
        env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -n WheelUpPane \
          if-shell -F "#{pane_in_mode}" "send-keys -X scroll-up" "copy-mode -e"
        env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -n WheelDownPane \
          if-shell -F "#{pane_in_mode}" "send-keys -X scroll-down" "run-shell true"
        env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -T copy-mode-emacs Escape send-keys -X cancel
        env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -T copy-mode-vi Escape send-keys -X cancel
        _inner_live=1
      fi
      if [ "$_inner_live" = "1" ]; then
        for _any_table in copy-mode copy-mode-vi; do
          _inner_copy_bind -T "$_any_table" Any if-shell -F "$ANY_EXIT_IF_MOUSE" "" "$ANY_EXIT_CMD"
          for _k in "${ANY_EXIT_KEYS[@]}"; do
            if [ "$_any_table" = "copy-mode-vi" ] && [ "$_k" = "$ANY_EXIT_VI_SKIP" ]; then
              continue
            fi
            _inner_copy_bind -T "$_any_table" "$_k" send-keys -X cancel '\;' send-keys
          done
          for _k in "${ANY_CANCEL_ONLY_KEYS[@]}"; do
            _inner_copy_bind -T "$_any_table" "$_k" send-keys -X cancel
          done
        done
        for _r in "${ANY_RESTORE_BIND[@]}"; do          # review r3 m1:還原 tmux 預設(見上;不用 set --,別動到本檔的引數)
          read -r _rt _rk _ra <<<"$_r"
          _inner_copy_bind -T "$_rt" "$_rk" send-keys -X "$_ra"
        done
      fi
    fi

    # tmux 的內建右鍵選單綁在 MouseDown3。AIB 在 Down 只抑制內建選單，
    # 自己的情境選單一律等 Up 才開；非 AIB 視窗仍走安裝前的
    # 原綁定。
    # Pane/Border/Status 五個事件名稱共用同一個 --context 入口。
    CONTEXT_CONDITION='#{==:#{@aib_cockpit},1}'
    for TARGET in Pane Border Status StatusLeft StatusRight; do
      case "$TARGET" in
        Pane) SURFACE_NAME="pane" ;;
        Border) SURFACE_NAME="border" ;;
        *) SURFACE_NAME="status" ;;
      esac
      DOWN_FALLBACK="$(preserved_binding "MouseDown3$TARGET")"
      run env -u TMUX tmux bind-key -n "MouseDown3$TARGET" \
        if-shell -F -t= "$CONTEXT_CONDITION" \
          "run-shell 'true'" \
          "$DOWN_FALLBACK"
      UP_FALLBACK="$(preserved_binding "MouseUp3$TARGET")"
      # An unmarked center pane expands @aib_pane to an empty string. Use an
      # explicit sentinel so the positional argv never shifts left.
      ROLE_FORMAT='#{?@aib_pane,#{q:@aib_pane},unmarked}'
      CONTEXT_COMMAND="run-shell -t= 'bash $MOUSE_SCRIPT_Q --context $SURFACE_NAME $ROLE_FORMAT #{q:mouse_x} #{q:mouse_y} #{q:pane_id} #{q:session_name} >>$MOUSE_LOG_Q 2>&1'"
      run env -u TMUX tmux bind-key -n "MouseUp3$TARGET" \
        if-shell -F -t= "$CONTEXT_CONDITION" \
          "$CONTEXT_COMMAND" \
          "$UP_FALLBACK"
    done

    # 剪貼簿(the maintainer 08-14「選取不能複製」根因修復):綁 copy-mode 兩張表的
    # DragEnd → copy-pipe-and-cancel。工具沿用上面的 COPY_CMD。
    # 找不到剪貼簿工具就一條 pipe 綁定都不裝——裝一個會失敗的綁定比沒有更糟;
    # 此時維持既有行為(選取進 tmux buffer, prefix+] 可貼)。
    # bind-key 同表同鍵覆寫=重跑冪等。
    if [ -n "$COPY_CMD" ]; then
      for TABLE in copy-mode copy-mode-vi; do
        run env -u TMUX tmux bind-key -T "$TABLE" MouseDragEnd1Pane \
          send-keys -X copy-pipe-and-cancel "$COPY_CMD"
        # 內層 hub server 同綁(正常流 cockpit-full 先起 hub 再 install)。
        # dry-run 固定印雙 server 保持測試確定性;real 模式探活後才綁,
        # 探不到不得靜默——沒做的事要說出來。
        if [ "$DRY" = "1" ]; then
          run env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -T "$TABLE" \
            MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "$COPY_CMD"
        elif env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" has-session \
            >/dev/null 2>&1; then
          env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" bind-key -T "$TABLE" \
            MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "$COPY_CMD"
        else
          echo "aihub server 不在,內層 $TABLE 剪貼簿綁定跳過" >&2
        fi
      done
    fi

    if [ "$DRY" != "1" ]; then
      release_install_lock
      # 10-03(影子續跑):只清信號的處理,EXIT 那條留著 —— release_install_lock 看 INSTALL_LOCKED,
      #   已放過就什麼都不做,行為不變;留著才不會把追蹤鉤子接在 EXIT 的「結束紀錄」一起清掉
      #   (以前這行是腳本最後一個命令,清掉 EXIT 後鉤子沒有下一個命令可以接回 ⇒ 整列量不到)。
      trap - HUP INT TERM
    fi
    ;;
  bar-arm)
    SESSION="${SESSION:-}"
    if [ "$DRY" = "1" ]; then
      exit 0
    fi
    if [ -n "$SESSION" ]; then
      aib_cabin_env_from_session "$SESSION" || exit 1
    fi
    [ -n "${EZ2_COCKPIT_STATE_DIR:-}" ] || exit 1
    rm -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-dragged"
    if "$EZ2_PY3" "$HERE/hit_test.py" --intent scrollbar \
        --x "${CX:-}" --y "${CY:-}" --state-dir "$EZ2_COCKPIT_STATE_DIR"; then
      : > "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab"
      exit 0
    fi
    # 上下箭頭只負責點一下拉一截，不進入拖曳。
    if "$EZ2_PY3" "$HERE/hit_test.py" --intent usage-arrow \
        --x "${CX:-}" --y "${CY:-}" --state-dir "$EZ2_COCKPIT_STATE_DIR"; then
      rm -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab"
      exit 0
    fi
    rm -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab"
    exit 1
    ;;
  bar-drag)
    if [ -n "${SESSION:-}" ]; then
      aib_cabin_env_from_session "$SESSION" || exit 0
    fi
    [ -n "${EZ2_COCKPIT_STATE_DIR:-}" ] || exit 0
    [ -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab" ] || exit 0
    : > "$EZ2_COCKPIT_STATE_DIR/scrollbar-dragged"
    off="$("$EZ2_PY3" "$HERE/hit_test.py" --intent scrollbar-offset \
      --y "${CY:-0}" --state-dir "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null)" || exit 0
    case "$off" in ''|*[!0-9]*) exit 0 ;; esac
    "$EZ2_PY3" "$COCKPIT/render/right_fold_ctl.py" --state-dir "$EZ2_COCKPIT_STATE_DIR" \
      --action "{\"action\":\"fold-scroll-to\",\"offset\":$off}" \
      >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
    exit 0
    ;;
  wheel)
    # 274 (b):右欄滾輪 ±3 列(規格 §2)。狀態改在 right_fold_ctl.py,採集端下一幀讀。恆 exit 0。
    case "$WHEEL_DIR" in up|down) ;; *) exit 0 ;; esac
    if [ -n "${SESSION:-}" ]; then aib_cabin_env_from_session "$SESSION" || true; fi
        # hub-frame：照抄待審滾輪，捲對話框正文
    PANE_TAG="$(env -u TMUX tmux display-message -p -t= '#{@aib_pane}' 2>/dev/null || true)"
    if [ "$PANE_TAG" = "hub-frame" ]; then
      if [ "$DRY" = "1" ]; then
        printf '+ dialog-scroll %s\n' "$WHEEL_DIR"
      else
        bash "$COCKPIT/pane-hub-frame.sh" --scroll "$WHEEL_DIR" >/dev/null 2>&1 || true
        # 只重畫；禁止 --ensure（會 resize hub、擠壓待審）
        bash "$COCKPIT/pane-hub-frame.sh" --repaint >/dev/null 2>&1 || true
      fi
      exit 0
    fi
WHEEL_ACTION="{\"action\":\"fold-scroll\",\"dir\":\"$WHEEL_DIR\",\"rows\":3}"
    if [ "$DRY" = "1" ]; then
      run "$EZ2_PY3" "$COCKPIT/render/right_fold_ctl.py" --state-dir "$EZ2_COCKPIT_STATE_DIR" --action "$WHEEL_ACTION"
    else
      "$EZ2_PY3" "$COCKPIT/render/right_fold_ctl.py" --state-dir "$EZ2_COCKPIT_STATE_DIR" --action "$WHEEL_ACTION" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
    fi
    exit 0
    ;;
  click)
    # 界線(08-14 與第三艦長裁定,可測版本):凡可能被 run-shell/按鍵綁定
    # 呼叫的 verb,**任何參數形狀下恆 exit 0**;只有「verb 本身不存在」才
    # 非零。--click 正是綁定 verb,缺參數是形狀問題不是打錯 verb,故改
    # 「說一句 + exit 0」——原本的 exit 2 會讓 run-shell 把錯誤畫面蓋上艙。
    # (本處由「綁定 verb 清單從碼推導」的防腐測試抓出,是同型第五個實例。)
    # 待審框:@aib_pane 可能還沒掛上,但 @aib_role=review 穩定。空 ROLE 不得
    # 在這裡先被拒,否則點了沒反應。
    if [ "${PANE_ROLE:-}" = "review" ]; then
      ROLE=pending-review
    fi
    [ -n "$ROLE" ] || { echo "--click needs <role> <x> <y>" >&2; exit 0; }
    # ★路由全按 @aib_role(結構穩定),與 @aib_pane 內容標記徹底脫鉤——
    # 標記會被視圖 respawn 洗掉(08-13 監看實證),按它分派=左格點擊誤入右格語意。
    case "${PANE_ROLE:-}" in
      bottom) ROLE=tabs ;;
      right)  [ "${CY:-99}" -ge 2 ] 2>/dev/null && ROLE=ai-list ;;
      left)   [ "${CY:-99}" -ge 2 ] 2>/dev/null && exit 0 ;;  # 左格內容列無點擊語意
      review) ROLE=pending-review ;;
      hub-frame) ROLE=hub-frame ;;
    esac
    # 右鍵「關閉這一席」(審查 r1 M1):只在 ai-list 內容列(y>=2)才往下走,
    #   不讓 --close-row 經過下面的捲軸 / 分頁切換前置動作
    if [ "$HIT_INTENT" = "close" ]; then
      case "${CY:-}" in ''|*[!0-9]*|?????*) exit 0 ;; esac      # 只收 1–4 位數字
      case "${CX:-}" in ''|*[!0-9]*|?????*) exit 0 ;; esac      # 欄一樣(命中器不看欄,但不讓怪字串往下傳)
      [ "$ROLE" = "ai-list" ] && [ "$CY" -ge 2 ] || exit 0
    fi
    # ★點擊也要從被點 session 重導艙身份（wheel／殺席已做；click 先前漏做）
    # 否則 hit_test 讀錯艙 hub-frame-rowmap、toggle 寫錯艙 panel-layout ⇒ 畫面零反應。
    SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
    if [ -n "${SESSION:-}" ]; then
      aib_cabin_env_from_session "$SESSION" || true
    fi
    # 這一按下是從捲軸開始的:拖過的話位置已經跟著走,放開不要再翻一頁,
    # 也不要誤觸同一列上的刪帳／切帳。
    #   右鍵「關閉這一席」不是從捲軸開始的按下 ⇒ 不碰這兩個狀態檔(審查 r2 M1)
    if [ "$HIT_INTENT" != "close" ] && [ -n "${EZ2_COCKPIT_STATE_DIR:-}" ] \
        && [ -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab" ]; then
      _scrolled=0
      [ -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-dragged" ] && _scrolled=1
      rm -f "$EZ2_COCKPIT_STATE_DIR/scrollbar-grab" "$EZ2_COCKPIT_STATE_DIR/scrollbar-dragged"
      if [ "$_scrolled" = "1" ]; then
        exit 0
      fi
    fi
    # 首行標籤帶(y<=1;左右框標籤渲染佔前兩列)點擊=視圖切換,轉交 E2.16 的
    # 命中器(它有各框 spans 正本,不在這裡重造第二份命中邏輯——反重造)。
    # ai-list 的 y=0 同樣是「1.AI 2.AI用量」標籤列(09-12 起第 2 頁是用量),一併轉交;>=2 才是內容列。
    # 左右首行標籤點擊=寫回**眼驗版**的狀態檔(與 Alt+Shift+Q/E 同一條路,
    # 1↔2 切換)。★不走 view-nav:它用 respawn 換渲染器,會把眼驗版格式洗掉
    # (08-13 the maintainer 實測「能點但格式錯了」即此)。渲染迴圈自己會撿到新值重畫。
    if [ "${CY:-99}" -le 1 ] 2>/dev/null; then
      case "${PANE_ROLE:-}" in
        left|right)
          TABF="$EZ2_COCKPIT_STATE_DIR/${PANE_ROLE}-tab"
          CUR="$(cat "$TABF" 2>/dev/null || echo 1)"
          [ "$CUR" = "2" ] && NEXT=1 || NEXT=2
          # 09-30 右欄有第 3 頁「離艙」:點哪一格就去哪一頁(格的起訖欄由 pane-right-tabs.sh 寫成檔)。
          # 值:2=1.AI、1=2.AI用量、3=3.離艙。檔讀不到、或點在分頁列以外(空的那一列)⇒ 照舊 1↔2 切換。
          if [ "$PANE_ROLE" = "right" ] && [ -f "$EZ2_COCKPIT_STATE_DIR/right-tab-spans.json" ]; then
            _PICK="$("$EZ2_PY3" -c 'import json,sys
d=json.load(open(sys.argv[1], encoding="utf-8")); x=int(sys.argv[2])
for name, val in (("ai", "2"), ("usage", "1"), ("left", "3")):
    a, b = d.get(name) or (0, 0)
    if a <= x < b:
        print(val); break' "$EZ2_COCKPIT_STATE_DIR/right-tab-spans.json" "${CX:-0}" 2>/dev/null || true)"
            [ "${CY:-0}" = "1" ] && [ -n "$_PICK" ] && NEXT="$_PICK"
          fi
          if [ "$DRY" = "1" ]; then
            printf '+ write %s-tab=%s\n' "$PANE_ROLE" "$NEXT"
          else
            printf '%s\n' "$NEXT" > "$TABF"
          fi
          exit 0
          ;;
      esac
    fi
    if ! ACTION="$("$EZ2_PY3" "$HERE/hit_test.py" --role "$ROLE" \
          --x "${CX:-0}" --y "${CY:-0}" --cabin "${EZ2_AIB_INSTANCE:-}" \
          --intent "$HIT_INTENT" --state-dir "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null)"; then
      exit 0
    fi
    if ! KIND="$("$EZ2_PY3" -c \
          'import json,sys; print(json.loads(sys.argv[1])["action"])' \
          "$ACTION" 2>/dev/null)"; then
      exit 0
    fi
    # 右鍵「關閉這一席」只准走 [X] 那一支(確認選單);命中器給別的一律當沒點到
    if [ "$HIT_INTENT" = "close" ] && [ "$KIND" != "kill-idle" ]; then
      exit 0
    fi
    # 審查 r2 S1:選單開著時右欄重畫 / 重排,同一行可能已換成別席 ⇒ 命中的窗不是選單開出時那一席就不動
    if [ "$HIT_INTENT" = "close" ]; then
      _HIT_WIN="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("window") or "")' "$ACTION" 2>/dev/null || true)"
      [ -n "$EXPECT_WINDOW" ] && [ "$_HIT_WIN" = "$EXPECT_WINDOW" ] || exit 0
    fi
    case "$KIND" in
      usage-header-tap|usage-subtab|usage-add-codex-oauth|usage-add-codex-token|\
      usage-move-account-up|usage-move-account-down|usage-activate-account|\
      usage-activate-next|usage-remove-account|usage-rebind-codex-oauth|\
      usage-redeem-reset)
        _codex_pack_on || exit 0
        ;;
    esac
    case "$KIND" in
      task-zone-focus)
        # 第 68 項:點任務區本體 ⇒ 進 task-nav 表(d 切精簡/詳細、Esc 回對話);說一句讓人知道鍵在哪
        if [ "$DRY" = "1" ]; then
          printf '+ task-zone-focus (switch-client -T task-nav)\n'
        else
          env -u TMUX tmux switch-client -t "${SESSION:-$EZ2_TMUX_SESSION}" -T task-nav >/dev/null 2>&1 || true
          env -u TMUX tmux display-message -d 2000 "任務區:d 切精簡/詳細 · Esc 回對話" >/dev/null 2>&1 || true
        fi
        ;;
      stale-frame)
        # 第 37 項:這一幀不能用(跨艙 / 剛重畫),說出來、留痕、不執行
        SF_REASON="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("reason") or "")' "$ACTION")"
        SF_MSG="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("message") or "")' "$ACTION")"
        if [ "$DRY" = "1" ]; then
          printf '+ stale-frame %s (%s)\n' "$SF_REASON" "$SF_MSG"
        else
          echo "$(date '+%F %T') stale-frame $SF_REASON x=${CX:-?} y=${CY:-?} $SF_MSG" \
            >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          env -u TMUX tmux display-message -d 2500 "$SF_MSG" >/dev/null 2>&1 || true
          bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
        fi
        ;;
      set-tab)
        TAB="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["tab"])' "$ACTION")"
        if [ "$DRY" = "1" ]; then
          printf '+ write bottom-tab=%s\n' "$TAB"
        else
          printf '%s\n' "$TAB" > "$EZ2_COCKPIT_STATE_DIR/bottom-tab"
        fi
        ;;
      copy-cabin)
        CABIN="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("cabin") or "")' "$ACTION")"
        [ -n "$CABIN" ] || CABIN="${EZ2_AIB_INSTANCE:-}"
        if [ "$DRY" = "1" ]; then
          printf '+ pbcopy %s\n' "$CABIN"
        elif [ -n "$CABIN" ]; then
          printf '%s' "$CABIN" | pbcopy 2>/dev/null || true
          tmux display-message "aib: 已複製艙號 $CABIN" >/dev/null 2>&1 || true
        fi
        ;;
      focus-hub)
        WIN="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["window"])' "$ACTION")"
        SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
        HUB="$EZ2_AIHUB_SOCKET"
        if [ "$DRY" != "1" ]; then
          HUB_FROM_SESSION="$(env -u TMUX tmux show-option -qv -t "$SESSION" @aib_hub_socket 2>/dev/null || true)"
          [ -z "$HUB_FROM_SESSION" ] || HUB="$HUB_FROM_SESSION"
        fi
        if [ "$DRY" = "1" ]; then
          run env -u TMUX tmux -L "$HUB" select-window -t "aihub:$WIN"
          run env -u TMUX bash "$COCKPIT/nav/region-nav-ctl.sh" --focus hub "$SESSION"
          pending_kick_redraw
        else
          # 08-14 the maintainer「另開 AI 後視角被搶回」的追查用留痕:hub 的中間格是
          # 單一 client(cockpit-full.sh:95 `tmux -L aihub attach`),一次只
          # 顯示一個窗,所以「視角被切回」必然是有人呼叫 select-window。
          # 本席假說=**`-O` 的副作用**:加了 -O 之後使用者必須「再點一次」
          # 才選得到項目,選單關閉後那多出來的一下若落在 ai-list 面板上,
          # 就會命中某一列 → focus-hub → 把視角切到那一列的窗。
          # 留痕在**自己的碼**裡而不是往活艙掛 tmux hook(HANDOFF 有殘留
          # hook 造成 ensure 連環 rc=1 的前科);下次重現時日誌會直接說出
          # 「誰在幾點切到哪個窗」。
          echo "$(date '+%F %T') focus-hub -> $WIN (click x=${CX:-?} y=${CY:-?})" \
            >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          # rowmap 可比 inner hub 活得久，也可能描述另一個 instance。選不到
          # 視窗是 stale hit，不是要刷進 pane 的錯誤；靜默降級為 passthrough。
          env -u TMUX tmux -L "$HUB" select-window -t "aihub:$WIN" \
            >/dev/null 2>&1 || exit 0
          env -u TMUX bash "$COCKPIT/nav/region-nav-ctl.sh" --focus hub "$SESSION" \
            >/dev/null 2>&1 || true
          pending_kick_redraw
        fi
        ;;
      bind-persona-menu|bind-brain-menu|change-brain-menu|unbind-persona|unbind-brain)
        # L2-03:第二/三行方框的精準命中(hit_test spans)。四個 verb 全走
        # --bind-menu 回呼層(與 --new-ai-menu 同形:每層 run-shell 回呼自己,
        # 不巢狀字串);解綁先彈確認(取消排第一項,同 [X] 的四裁)。
        BIND_SEAT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("seat") or "")' "$ACTION")"
        case "$BIND_SEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
        # 選單目標 pane 由 @aib_role 反查(同 kill-idle 的理由:座標與目標同源)
        MENU_PANE=""
        if [ "$DRY" != "1" ] && [ -n "${PANE_ROLE:-}" ]; then
          if ! MENU_PANE="$(env -u TMUX tmux list-panes -a \
              -F '#{pane_id} #{@aib_role}' 2>/dev/null \
              | awk -v r="$PANE_ROLE" '$2==r{print $1; exit}')"; then
            MENU_PANE=""
          fi
        fi
        case "$KIND" in
          bind-persona-menu) BIND_KIND="persona" ;;
          bind-brain-menu)   BIND_KIND="brain" ;;
          # AR-3:[換大腦]=同一個大腦選單,BIND_ARG=bound 讓選單層加「解除綁定」第一項
          change-brain-menu) BIND_KIND="brain"; BIND_ARG="bound" ;;
          unbind-persona)    BIND_KIND="unbind-confirm"; BIND_ARG="persona" ;;
          unbind-brain)      BIND_KIND="unbind-confirm"; BIND_ARG="brain" ;;
        esac
        PANE="$MENU_PANE"
        bind_menu_layer
        exit 0
        ;;
      perf-close|perf-reap)
        # 效能頁 [X](E 面板第三片):確認選單→close --yes / reap。
        # 選單紀律沿 kill-idle 四裁:①標題帶目標身分②Esc=取消③取消排
        # 第一項④恆 exit 0。真動作搬到 --perf-*-confirmed(選單回呼)。
        PERF_TID=""; PERF_NAME=""; PERF_N=""
        if [ "$KIND" = "perf-close" ]; then
          if ! PERF_TID="$("$EZ2_PY3" -c               'import json,sys; print(json.loads(sys.argv[1])["id"])'               "$ACTION" 2>/dev/null)"; then
            exit 0
          fi
          if ! PERF_NAME="$("$EZ2_PY3" -c               'import json,sys; print(json.loads(sys.argv[1]).get("name") or "")'               "$ACTION" 2>/dev/null)"; then
            PERF_NAME=""
          fi
          # 白名單再驗(hit_test 已驗;選單命令串要進 run-shell,縱深一層)
          case "$PERF_TID" in
            ''|*[!A-Za-z0-9:/._-]*) exit 0 ;;
          esac
          MENU_TITLE="收艙 ${PERF_NAME:-$PERF_TID} · $PERF_TID"
          printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
          printf -v PERF_TID_Q '%q' "$PERF_TID"
          CONFIRM_ACTION="run-shell 'bash $SCRIPT_Q --perf-close-confirmed $PERF_TID_Q$RUNSHELL_REDIR'"
        else
          if ! PERF_N="$("$EZ2_PY3" -c               'import json,sys; print(int(json.loads(sys.argv[1])["count"]))'               "$ACTION" 2>/dev/null)"; then
            exit 0
          fi
          MENU_TITLE="全關 $PERF_N 項(reaper 安全集)"
          printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
          CONFIRM_ACTION="run-shell 'bash $SCRIPT_Q --perf-reap-confirmed$RUNSHELL_REDIR'"
        fi
        printf -v LOG_Q '%q' "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log"
        CANCEL_ACTION="run-shell 'printf \"%s perf cancelled (${PERF_TID:-reap})\\n\" \
\"\$(date \"+%F %T\")\" >> $LOG_Q'"
        # 選單 pane:@aib_role 反查(同 kill-idle 的理由:座標與目標同源;
        # 命令替換包 if,set -e 下裸賦值失敗會靜默中止整支)
        MENU_PANE=""
        if [ "$DRY" != "1" ] && [ -n "${PANE_ROLE:-}" ]; then
          if ! MENU_PANE="$(env -u TMUX tmux list-panes -a               -F '#{pane_id} #{@aib_role}' 2>/dev/null               | awk -v r="$PANE_ROLE" '$2==r{print $1; exit}')"; then
            MENU_PANE=""
          fi
        fi
        MENU_TARGET=()
        [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
        set_menu_pos "$MENU_PANE"
        if [ "$DRY" = "1" ]; then
          run tmux display-menu -O -M -T "$MENU_TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"}             ${MENU_POS[@]+"${MENU_POS[@]}"}             "取消" q "$CANCEL_ACTION" "" "確定關閉" y "$CONFIRM_ACTION"
        else
          tmux display-menu -O -M -T "$MENU_TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"}             ${MENU_POS[@]+"${MENU_POS[@]}"}             "取消" q "$CANCEL_ACTION" "" "確定關閉" y "$CONFIRM_ACTION" >/dev/null 2>&1 || true
        fi
        ;;
      open-usage-page)
        # the maintainer 09-06 13:3x 裁:用量列點 LOGO/品牌名 ⇒ 開該家用量頁。URL 只認 hit_test 的
        # USAGE_PAGES 表(不從畫面來),且必須是 https://;DRY 只印不開。全路徑恆 exit 0。
        URL="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("url") or "")' "$ACTION")"
        case "$URL" in https://*) ;; *) exit 0 ;; esac
        if [ "$DRY" = "1" ]; then
          run open "$URL"
        else
          echo "$(date '+%F %T') open-usage-page -> $URL (click x=${CX:-?} y=${CY:-?})" \
            >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          open "$URL" >/dev/null 2>&1 || true
        fi
        ;;
      fold-toggle|fold-expand-all|fold-collapse-all|fold-scroll)
        # 274 右欄艦隊收合/捲動(規格 RIGHT-FLEET-FOLD-v1 §1 §2):狀態檔只在這裡改,採集端下一幀讀。
        #   fold-toggle:切換一支艦隊;expand/collapse-all:對 rowmap 上所有摺疊鍵;fold-scroll:offset ±rows。
        #   (e)2 點擊前驗同一幀:rowmap 的 epoch 與 action 帶的 epoch 不同 ⇒ 不動(畫面已經換過)。DRY 只印。恆 exit 0。
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/render/right_fold_ctl.py" --state-dir "$EZ2_COCKPIT_STATE_DIR" --action "$ACTION"
        else
          "$EZ2_PY3" "$COCKPIT/render/right_fold_ctl.py" --state-dir "$EZ2_COCKPIT_STATE_DIR" --action "$ACTION" \
            >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      reprobe-usage)
        # the maintainer 09-06 13:5x 裁:過期列的 [重驗] ⇒ 只對那一家起一次探針(usage_probe.py --reprobe,
        # 走 _spawn_probe 同鎖/同 socket/同每日上限)。engine 只認小寫字母;DRY 只印。恆 exit 0。
        ENGINE="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("engine") or "")' "$ACTION")"
        case "$ENGINE" in ''|*[!a-z]*) exit 0 ;; esac
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_probe.py" --reprobe "$ENGINE"
        else
          echo "$(date '+%F %T') reprobe-usage -> $ENGINE (click x=${CX:-?} y=${CY:-?})" \
            >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          "$EZ2_PY3" "$COCKPIT/usage_probe.py" --reprobe "$ENGINE" >/dev/null 2>&1 || true
        fi
        ;;


      usage-subtab)
        # 子頁簽 用量|Codex：寫 store.subtab 後重繪右欄用量
        SUB="$("$EZ2_PY3" -c 'import json,sys
d=json.loads(sys.argv[1])
s=(d.get("subtab") or d.get("arg") or "usage")
print("usage" if s in ("用量","usage") else ("codex" if s=="codex" else "usage"))' "$ACTION")"
        case "$SUB" in usage|codex) ;; *) SUB=usage ;; esac
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" subtab "$SUB"
        else
          # 先寫一行，右欄 0.1 秒內就能換上預載的那頁，不必等這支 python 把帳號檔存完。
          if [ -n "${EZ2_COCKPIT_STATE_DIR:-}" ]; then
            _side="$EZ2_COCKPIT_STATE_DIR/usage-subtab"
            printf '%s\n' "$SUB" > "${_side}.pane.tmp" 2>/dev/null \
              && mv -f "${_side}.pane.tmp" "$_side" 2>/dev/null || true
          fi
          "$EZ2_PY3" "$COCKPIT/usage_accounts.py" subtab "$SUB"             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-header-tap)
        # 隱藏門：3 秒內連點「─ 用量 ─」≥3 → 解鎖 Codex 多憑證區。恆 exit 0。
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" tap
        else
          "$EZ2_PY3" "$COCKPIT/usage_accounts.py" tap             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-add-codex-oauth)
        # [+] 才開登入。名單裡已有的信箱不新增、也不改舊列。
        # 清殘留 login/proxy 用 proc_find kill(10-10):pkill -f 會連「指令列剛好含這幾個字」的上層 shell 一起殺。
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-start
          exit 0
        fi
        "$EZ2_PY3" "$COCKPIT/proc_find.py" kill "codex login" >/dev/null 2>&1 || true
        "$EZ2_PY3" "$COCKPIT/proc_find.py" kill "localhost_ipv6_proxy.py --port 1455" >/dev/null 2>&1 || true
        sleep 0.2
        START="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-start 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
        printf '%s\n' "$START" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        URL="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("url") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
        LPID="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("pid") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
        PROXY_PID="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("proxy_pid") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
        PREV_FP="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("prev_fingerprint") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
        case "$URL" in https://*) ;; *)
          osascript -e 'display dialog "無法取得登入頁。請確認本機 codex login 可用。" buttons {"知道了"} default button 1 with title "AIB · 沒有新增"' >/dev/null 2>&1 || true
          exit 0
          ;;
        esac
        case "$(uname -s)" in
          Darwin) ;;
          *) open "$URL" >/dev/null 2>&1 || true ;;
        esac
        BTN="$(osascript -e "button returned of (display dialog \"瀏覽器會開一個登入頁。\\n\\n請登一個名單裡還沒有的信箱。登完回到這裡按「已登入完成」。\\n同一個信箱不會再加一列。\" buttons {\"取消\",\"已登入完成\"} default button 2 with title \"AIB · OAuth 新增\")" 2>/dev/null || true)"
        case "$BTN" in
          "已登入完成")
            if [ -n "$LPID" ]; then
              "$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-wait "$LPID" --prev-fp "$PREV_FP" --timeout 180 \
                >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
            fi
            OUT="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" add-new-after-login --prev-fp "$PREV_FP" 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
            printf '%s\n' "$OUT" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
            ADD_ACTION="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("_add_action") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
            ADD_MSG="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("_add_message") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
            case "$ADD_ACTION" in
              added)
                osascript -e "display dialog \"${ADD_MSG:-已新增}\" buttons {\"好\"} default button 1 with title \"AIB · 已新增\"" >/dev/null 2>&1 || true
                ;;
              *)
                osascript -e "display dialog \"${ADD_MSG:-沒有新增}\" buttons {\"知道了\"} default button 1 with title \"AIB · 沒有新增\"" >/dev/null 2>&1 || true
                ;;
            esac
            ;;
          *)
            if [ -n "$LPID" ]; then
              "$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-cancel "$LPID" --proxy-pid "${PROXY_PID:-0}" \
                >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
            fi
            ;;
        esac
        _pin_pane="$(lookup_role_pane "${PANE_ROLE:-right}")"
        if [ -n "$_pin_pane" ]; then
          env -u TMUX tmux set-option -p -t "$_pin_pane" @aib_pane ai-list 2>/dev/null || true
        fi
        date +%s > "$EZ2_COCKPIT_STATE_DIR/right-kick" 2>/dev/null || true
        ;;
      usage-add-codex-token)
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" add-token --token DRY_TOKEN_PLACEHOLDER
        else
          TOK="$(osascript -e 'text returned of (display dialog "貼上 Codex Token Key" default answer "" with hidden answer)' 2>/dev/null || true)"
          case "$TOK" in "") exit 0 ;; esac
          "$EZ2_PY3" "$COCKPIT/usage_accounts.py" add-token --token "$TOK"             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-reprobe-account)
        ACCT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("account_id") or "")' "$ACTION")"
        case "$ACCT" in ""|*[!0-9a-fA-F]*) exit 0 ;; esac
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" probe "$ACCT"
        else
          "$EZ2_PY3" "$COCKPIT/usage_accounts.py" probe "$ACCT"             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-move-account-up|usage-move-account-down)
        ACCT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("account_id") or json.loads(sys.argv[1]).get("account_id") or "")' "$ACTION")"
        case "$ACCT" in ""|*[!0-9a-fA-F]*) exit 0 ;; esac
        MOVE_CMD=move-up
        case "$KIND" in usage-move-account-down) MOVE_CMD=move-down ;; esac
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" "$MOVE_CMD" "$ACCT"
        else
          "$EZ2_PY3" "$COCKPIT/usage_accounts.py" "$MOVE_CMD" "$ACCT"             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-activate-account)
        ACCT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("account_id") or "")' "$ACTION")"
        case "$ACCT" in ""|*[!0-9a-fA-F]*) exit 0 ;; esac
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" activate "$ACCT"
        else
          OUT="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" activate "$ACCT" 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
          printf '%s\n' "$OUT" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          OK="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print("1" if d.get("ok") else "0")
except Exception:
 print("0")' "$OUT" 2>/dev/null || true)"
          EM="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("email") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
          HINT="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("hint") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
          if [ "$OK" = "1" ]; then
            osascript -e "display dialog \"已切到：${EM:-槽}\n\n${HINT:-請開新 Codex 席，勿沿用舊 session。}\" buttons {\"好\"} default button 1 with title \"AIB · 切帳\"" >/dev/null 2>&1 || true
          else
            REASON="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("reason") or d.get("hint") or "失敗")
except Exception:
 print("失敗")' "$OUT" 2>/dev/null || true)"
            osascript -e "display dialog \"切帳失敗：${REASON}\" buttons {\"知道了\"} default button 1 with title \"AIB · 切帳\"" >/dev/null 2>&1 || true
          fi
        fi
        ;;
      usage-activate-next)
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" activate-next
        else
          OUT="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" activate-next 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
          printf '%s\n' "$OUT" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          OK="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print("1" if d.get("ok") else "0")
except Exception:
 print("0")' "$OUT" 2>/dev/null || true)"
          EM="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("email") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
          HINT="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("hint") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
          if [ "$OK" = "1" ]; then
            osascript -e "display dialog \"已切到下一可用：${EM:-槽}\n\n${HINT:-請開新 Codex 席，勿沿用舊 session。}\" buttons {\"好\"} default button 1 with title \"AIB · 切下一帳\"" >/dev/null 2>&1 || true
          else
            REASON="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("reason") or d.get("hint") or "沒有可用槽")
except Exception:
 print("沒有可用槽")' "$OUT" 2>/dev/null || true)"
            osascript -e "display dialog \"無法切下一帳：${REASON}\" buttons {\"知道了\"} default button 1 with title \"AIB · 切下一帳\"" >/dev/null 2>&1 || true
          fi
        fi
        ;;
      usage-remove-account)
        ACCT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("account_id") or "")' "$ACTION")"
        case "$ACCT" in ""|*[!0-9a-fA-F]*) exit 0 ;; esac
        EM="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("oauth_account") or "")' "$ACTION" 2>/dev/null || true)"
        if [ -z "$EM" ]; then EM="$ACCT"; fi
        SEQ="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("seq_label") or "")' "$ACTION" 2>/dev/null || true)"
        if [ -z "$SEQ" ]; then SEQ="???"; fi
        printf '%s usage-remove-account acct=%s seq=%s email=%s dry=%s\n' "$(date '+%H:%M:%S')" "$ACCT" "$SEQ" "$EM" "${DRY:-0}" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        # 不在這裡直接刪。確認選單要開在這一點旁邊；沒給座標時 tmux 把選單放整窗正中，
        # 人看著右欄的 [X]，會以為點了沒反應。
        printf '%s\t%s\t%s\n' "$ACCT" "$EM" "$SEQ" > "$EZ2_COCKPIT_STATE_DIR/usage-remove-pending.tsv"
        SCRIPT_Q=$(printf '%q' "$HERE/mouse-ctl.sh")
        LOG_Q=$(printf '%q' "$EZ2_COCKPIT_STATE_DIR/aib-open.log")
        STATE_Q=$(printf '%q' "$EZ2_COCKPIT_STATE_DIR")
        TITLE=$(printf '刪除 %s %s？' "$SEQ" "$EM")
        CONFIRM_ACTION="run-shell -b 'EZ2_COCKPIT_STATE_DIR=$STATE_Q bash $SCRIPT_Q --usage-remove-confirmed >>$LOG_Q 2>&1'"
        MENU_PANE=""
        if [ "$DRY" != "1" ] && [ -n "${PANE_ROLE:-}" ]; then
          MENU_PANE="$(lookup_role_pane "$PANE_ROLE")"
        fi
        MENU_TARGET=()
        [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
        set_menu_pos "$MENU_PANE"
        if [ "$DRY" = "1" ]; then
          run tmux display-menu -O -M -T "$TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
            ${MENU_POS[@]+"${MENU_POS[@]}"} \
            "取消" q "" "" "確定刪除 $SEQ" y "$CONFIRM_ACTION"
        else
          env -u TMUX tmux display-menu -O -M -T "$TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
            ${MENU_POS[@]+"${MENU_POS[@]}"} \
            "取消" q "" "" "確定刪除 $SEQ" y "$CONFIRM_ACTION" \
            >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      leftover-close|leftover-close-all)
        # 09-30 右欄第 3 頁「離艙」:<X> 收一席、<全收> 收全部(只收只剩紀錄與死窗)。一律先彈確認選單,
        # 取消排第一項(同 kill-idle 的理由:選單開在游標處,手快連點不能等於直接收)。
        if [ "$KIND" = "leftover-close" ]; then
          LKEY="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("key") or "")' "$ACTION")"
          LLABEL="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("label") or "")' "$ACTION")"
          case "$LKEY" in *"|@"*) ;; *) exit 0 ;; esac
          TITLE="收掉離艙席 ${LLABEL:-$LKEY}?"
          YES="確定收掉"
        else
          LKEY="ALL"
          LCOUNT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("count") or 0)' "$ACTION")"
          TITLE="收掉全部離艙席(${LCOUNT} 個席位檔,只剩紀錄與死窗;還在跑的不收)?"
          YES="確定全收"
        fi
        printf '%s %s key=%s dry=%s\n' "$(date '+%H:%M:%S')" "$KIND" "$LKEY" "${DRY:-0}" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        printf '%s\n' "$LKEY" > "$EZ2_COCKPIT_STATE_DIR/leftover-pending.txt"
        SCRIPT_Q=$(printf '%q' "$HERE/mouse-ctl.sh")
        LOG_Q=$(printf '%q' "$EZ2_COCKPIT_STATE_DIR/aib-open.log")
        STATE_Q=$(printf '%q' "$EZ2_COCKPIT_STATE_DIR")
        CONFIRM_ACTION="run-shell -b 'EZ2_COCKPIT_STATE_DIR=$STATE_Q bash $SCRIPT_Q --leftover-confirmed >>$LOG_Q 2>&1'"
        MENU_PANE=""
        if [ "$DRY" != "1" ] && [ -n "${PANE_ROLE:-}" ]; then
          MENU_PANE="$(lookup_role_pane "$PANE_ROLE")"
        fi
        MENU_TARGET=()
        [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
        set_menu_pos "$MENU_PANE"
        if [ "$DRY" = "1" ]; then
          run tmux display-menu -O -M -T "$TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
            ${MENU_POS[@]+"${MENU_POS[@]}"} \
            "取消" q "" "" "$YES" y "$CONFIRM_ACTION"
        else
          env -u TMUX tmux display-menu -O -M -T "$TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
            ${MENU_POS[@]+"${MENU_POS[@]}"} \
            "取消" q "" "" "$YES" y "$CONFIRM_ACTION" \
            >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-redeem-reset)
        ACCT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("account_id") or "")' "$ACTION")"
        case "$ACCT" in ""|*[!0-9a-fA-F]*) exit 0 ;; esac
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/redeem_reset_credit_cli.py" "$COCKPIT" "$ACCT" "$EZ2_COCKPIT_STATE_DIR"
        else
          "$EZ2_PY3" "$COCKPIT/redeem_reset_credit_cli.py" "$COCKPIT" "$ACCT" "$EZ2_COCKPIT_STATE_DIR" \
            >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
        fi
        ;;
      usage-rebind-codex-oauth)
        # 🔄＝開瀏覽器 OAuth（codex login，免識別碼；不看桌面已登入帳）。登完再加綁／更新槽。
        ACCT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("account_id") or "")' "$ACTION")"
        case "$ACCT" in *[!0-9a-fA-F]*) exit 0 ;; esac
        # 對話框標的：有槽→該槽 email；無槽／新加→第一次新增
        TARGET_LABEL="$("$EZ2_PY3" -c 'import json,sys
d=json.loads(sys.argv[1])
em=(d.get("oauth_account") or "").strip()
aid=(d.get("account_id") or "").strip()
if em:
 print(em)
elif not aid:
 print("第一次新增")
else:
 print("")' "$ACTION" 2>/dev/null || true)"
        if [ -z "$TARGET_LABEL" ] && [ -n "$ACCT" ]; then
          TARGET_LABEL="$("$EZ2_PY3" -c 'import json,sys,os
from pathlib import Path
aid=sys.argv[1]
paths=[]
base=os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
if base:
 paths.append(Path(base)/"usage-accounts.json")
home=Path.home()/".ez2-aib"
paths.append(home/"usage-accounts.json")
if (home/"instances").is_dir():
 paths += sorted((home/"instances").glob("*/cockpit/usage-accounts.json"))
email=""
for path in paths:
 if not path.is_file():
  continue
 try:
  d=json.loads(path.read_text())
 except Exception:
  continue
 accs=d if isinstance(d,list) else (d.get("accounts") or [])
 for a in accs:
  if isinstance(a,dict) and str(a.get("id") or "")==aid:
   email=str(a.get("oauth_account") or "").strip()
   break
 if email:
  break
print(email or aid)' "$ACCT" 2>/dev/null || true)"
        fi
        [ -n "$TARGET_LABEL" ] || TARGET_LABEL="第一次新增"
        if [ "$DRY" = "1" ]; then
          run "$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-start
        else
          # 清掉上次沒關乾淨的 login/proxy，避免對話框殘留、1455 佔住、點擊像死掉
          "$EZ2_PY3" "$COCKPIT/proc_find.py" kill "codex login" >/dev/null 2>&1 || true
          "$EZ2_PY3" "$COCKPIT/proc_find.py" kill "localhost_ipv6_proxy.py --port 1455" >/dev/null 2>&1 || true
          sleep 0.2
          START="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-start 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
          printf '%s\n' "$START" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          URL="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("url") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
          CODE="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("code") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
          LPID="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("pid") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
          PROXY_PID="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("proxy_pid") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
          PREV_FP="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("prev_fingerprint") or "")
except Exception:
 print("")' "$START" 2>/dev/null || true)"
          case "$URL" in https://*) ;; *)
            osascript -e 'display dialog "無法取得 Codex 重綁 URL。請確認本機 codex login 可用。" buttons {"知道了"} default button 1 with title "AIB · 重綁失敗"' >/dev/null 2>&1 || true
            exit 0
            ;;
          esac
          # macOS：Codex 已經開過這個授權頁。再 open 一次就是第二個大頁。
          case "$(uname -s)" in
            Darwin) ;;
            *) open "$URL" >/dev/null 2>&1 || true ;;
          esac
          TL_ESC="$(printf '%s' "$TARGET_LABEL" | sed 's/\\/\\\\/g; s/"/\\"/g')"
          if [ "$TARGET_LABEL" = "第一次新增" ]; then
            DLG_HEAD="對象：第一次新增"
          else
            DLG_HEAD="重綁信箱：${TL_ESC}"
          fi
          BTN="$(osascript -e "button returned of (display dialog \"${DLG_HEAD}\\n\\n瀏覽器會開一個 ChatGPT 登入頁（免輸入識別碼）。\\n\\n在那個分頁選帳並完成後，回到這裡按「已登入完成」。\" buttons {\"取消\",\"已登入完成\"} default button 2 with title \"AIB · OAuth 重綁\")" 2>/dev/null || true)"
          case "$BTN" in
            "已登入完成")
              if [ -n "$LPID" ]; then
                "$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-wait "$LPID" --prev-fp "$PREV_FP" --timeout 180 \
                  >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
              fi
              if [ -n "$ACCT" ]; then
                OUT="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" rebind-after-login "$ACCT" --prev-fp "$PREV_FP" 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
              else
                OUT="$("$EZ2_PY3" "$COCKPIT/usage_accounts.py" rebind-after-login --prev-fp "$PREV_FP" 2>>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" || true)"
              fi
              printf '%s\n' "$OUT" >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
              ACTION_RB="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("_rebind_action") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
              MSG_RB="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("_rebind_message") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
              case "$ACTION_RB" in
                added_slot|filled_slot|refreshed_tokens)
                  EM="$("$EZ2_PY3" -c 'import json,sys
try:
 d=json.loads(sys.argv[1]); print(d.get("oauth_account") or d.get("_rebind_email") or "")
except Exception:
 print("")' "$OUT" 2>/dev/null || true)"
                  if [ "$ACTION_RB" = "refreshed_tokens" ]; then MSG_OK="已更新：${EM:-同信箱}"; TITLE_OK="AIB · 已更新"; else MSG_OK="已加綁：${EM:-新槽}"; TITLE_OK="AIB · 加綁成功"; fi
                  osascript -e "display dialog \"${MSG_OK}\" buttons {\"好\"} default button 1 with title \"${TITLE_OK}\"" >/dev/null 2>&1 || true
                  ;;
                already_bound|login_unchanged)
                  osascript -e "display dialog \"${MSG_RB:-登入結果仍是已綁過的信箱，未加新槽。}\" buttons {\"知道了\"} default button 1 with title \"AIB · 未加新槽\"" >/dev/null 2>&1 || true
                  ;;
                *)
                  osascript -e "display dialog \"${MSG_RB:-重綁未完成。}\" buttons {\"知道了\"} default button 1 with title \"AIB · 重綁\"" >/dev/null 2>&1 || true
                  ;;
              esac
              ;;
            *)
              if [ -n "$LPID" ]; then
                "$EZ2_PY3" "$COCKPIT/usage_accounts.py" login-cancel "$LPID" --proxy-pid "${PROXY_PID:-0}" \
                  >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
              fi
              ;;
          esac
          # 重綁對話框結束後釘回點擊標籤（中途撤標或 cancel 失敗時會假死）。
          # run-shell 底下沒有 TMUX_PANE，改用角色找回那一格。
          _pin_pane="$(lookup_role_pane "${PANE_ROLE:-right}")"
          if [ -n "$_pin_pane" ]; then
            env -u TMUX tmux set-option -p -t "$_pin_pane" @aib_pane ai-list 2>/dev/null || true
          fi
          date +%s > "$EZ2_COCKPIT_STATE_DIR/right-kick" 2>/dev/null || true
        fi
        ;;
      pending-focus|pending-unfocus|pending-scroll-up|pending-scroll-down|pending-visible-cycle|dialog-visible-cycle|realtime-visible-cycle|input-visible-cycle|dialog-scroll-up|dialog-scroll-down|pending-new|pending-global-toggle|pending-global-visible-cycle|hub-io-toggle|feature-tab|inbox-tab|dm-queue-move)
        QSEAT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("seat") or "")' "$ACTION")"
        QITEM="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("item") or "")' "$ACTION")"
        case "$KIND" in
          dialog-visible-cycle|realtime-visible-cycle|input-visible-cycle|hub-io-toggle|dialog-scroll-up|dialog-scroll-down) ;;  # 左欄框無 seat
          *)
            case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
            ;;
        esac
        DQ="$COCKPIT/dm_queue.py"
        # 點選對「畫面上這一頁」的列作用(09-14:畫「已讀」而只認待審 ⇒ 點了沒反應)
        QTABKEY="$(pending_inbox_tab_key)"
        if [ "$KIND" = "pending-focus" ]; then
          case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
          if [ "$DRY" = "1" ]; then run "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --focus-set "$QITEM" --tab "$QTABKEY"
          else
            "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --focus-set "$QITEM" --tab "$QTABKEY" >/dev/null 2>&1 || true
            # 雙擊已開編輯框時,第二下的放開不得把鍵盤切進 pending-nav(會搶走編輯框的方向鍵/Esc)
            [ -f "${EZ2_COCKPIT_STATE_DIR:-/nonexistent}/pending-edit.lock" ] || pending_nav_enter
          fi
        elif [ "$KIND" = "pending-unfocus" ]; then
          if [ "$DRY" = "1" ]; then run "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --focus-clear --tab "$QTABKEY"
          else
            "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --focus-clear --tab "$QTABKEY" >/dev/null 2>&1 || true
            pending_nav_leave
          fi
        elif [ "$KIND" = "pending-scroll-up" ]; then
          if [ "$DRY" = "1" ]; then run "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --scroll up --tab "$QTABKEY"
          else "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --scroll up --tab "$QTABKEY" >/dev/null 2>&1 || true; fi
        elif [ "$KIND" = "pending-scroll-down" ]; then
          if [ "$DRY" = "1" ]; then run "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --scroll down --tab "$QTABKEY"
          else "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --scroll down --tab "$QTABKEY" >/dev/null 2>&1 || true; fi
        elif [ "$KIND" = "pending-new" ]; then
          if [ "$DRY" = "1" ]; then run "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --jump-new --tab "$QTABKEY"
          else "$EZ2_PY3" "$DQ" view --seat "$QSEAT" --jump-new --tab "$QTABKEY" >/dev/null 2>&1 || true; fi
        elif [ "$KIND" = "inbox-tab" ]; then
          # 第 70 項 P4:點待審區五 tab(待審/已讀/完成/備存/刪除)。這是「人按」切頁的滑鼠路——
          # pending-inbox-tab 的寫者只准這裡與 pane-pending-review.sh --key tab(性質圈尺守著)。不切功能區頁。
          QTAB="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("tab"))' "$ACTION")"
          case "$QTAB" in 0|1|2|3|4) ;; *) exit 0 ;; esac
          if [ "$DRY" = "1" ]; then
            printf '+ inbox-tab %s\n' "$QTAB"
          else
            mkdir -p "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null || true
            printf '%s\n' "$QTAB" > "$EZ2_COCKPIT_STATE_DIR/pending-inbox-tab"
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "feature-tab" ]; then
          QTAB="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("tab"))' "$ACTION")"
          case "$QTAB" in 0|1) ;; *) exit 0 ;; esac
          # 34 接口二規則 1(A4):切頁必把鍵還回 root——隱藏的頁不得持有鍵盤
          pending_nav_leave
          if [ "$DRY" = "1" ]; then
            printf '+ feature-tab %s\n' "$QTAB"
          else
            mkdir -p "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null || true
            printf '%s\n' "$QTAB" > "$EZ2_COCKPIT_STATE_DIR/pending-feature-tab"
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "pending-visible-cycle" ]; then
          if [ "$DRY" = "1" ]; then
            run "$EZ2_PY3" -c 'print("pending-visible-cycle")'
          else
            "$EZ2_PY3" - "$EZ2_COCKPIT_STATE_DIR" "$COCKPIT/render" <<'PY' >/dev/null 2>&1 || true
import os, sys
sys.path.insert(0, sys.argv[2])
from render_lib import pending_visible_of, pending_visible_next
from pathlib import Path
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
p = root / "pending-visible"
cur = p.read_text(encoding="utf-8").strip() if p.is_file() else ""
p.write_text("%d\n" % pending_visible_next(cur), encoding="utf-8")
PY
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --apply-pending-height >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "dialog-visible-cycle" ]; then
          if [ "$DRY" = "1" ]; then
            run "$EZ2_PY3" -c 'print("dialog-visible-cycle")'
          else
            "$EZ2_PY3" - "$COCKPIT" <<'PY' >/dev/null 2>&1 || true
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
PL.cycle_dialog_height()
PY
            # 改 ≡ 後重算左欄：關態 frame 縮回私訊+[+]，剩餘給 hub CLI
            bash "$COCKPIT/pane-hub-frame.sh" --apply-input-height >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-hub-frame.sh" --ensure >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --apply-pending-height >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "realtime-visible-cycle" ]; then
          if [ "$DRY" = "1" ]; then
            run "$EZ2_PY3" -c 'print("realtime-visible-cycle")'
          else
            "$EZ2_PY3" - "$COCKPIT" <<'PY' >/dev/null 2>&1 || true
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
PL.cycle_realtime_height()
PY
            bash "$COCKPIT/pane-hub-frame.sh" --apply-input-height >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-hub-frame.sh" --ensure >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "input-visible-cycle" ]; then
          if [ "$DRY" = "1" ]; then
            run "$EZ2_PY3" -c 'print("input-visible-cycle")'
          else
            "$EZ2_PY3" - "$COCKPIT" <<'PY' >/dev/null 2>&1 || true
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
PL.cycle_input_height()
PY
            bash "$COCKPIT/pane-hub-frame.sh" --apply-input-height >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-hub-frame.sh" --ensure >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "hub-io-toggle" ]; then
          if [ "$DRY" = "1" ]; then
            run "$EZ2_PY3" -c 'print("hub-io-toggle")'
          else
            SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
            if [ -n "${SESSION:-}" ]; then
              aib_cabin_env_from_session "$SESSION" || true
            fi
            # 與 pane-hub-frame --paint 同讀：FOCUS_PROVIDER → aihub 作用中窗 @aib_provider
            _prov="${EZ2_AIB_FOCUS_PROVIDER:-}"
            _HUB="${EZ2_AIHUB_SOCKET:-}"
            if [ -z "$_prov" ]; then
              if [ -z "$_HUB" ] && [ -n "${SESSION:-}" ]; then
                _HUB="$(env -u TMUX tmux show-option -qv -t "$SESSION" @aib_hub_socket 2>/dev/null || true)"
              fi
              if [ -z "$_HUB" ] && [ -n "${EZ2_TMUX_SESSION:-}" ]; then
                _HUB="$(env -u TMUX tmux show-option -qv -t "$EZ2_TMUX_SESSION" @aib_hub_socket 2>/dev/null || true)"
              fi
              if [ -n "$_HUB" ]; then
                _prov="$(env -u TMUX tmux -L "$_HUB" list-windows -t aihub \
                  -F '#{window_active} #{@aib_provider}' 2>/dev/null \
                  | awk '$1==1 && $2!=""{print $2; exit}')"
              fi
            fi
            export EZ2_AIB_FOCUS_PROVIDER="$_prov"
            [ -n "$_HUB" ] && export EZ2_AIHUB_SOCKET="$_HUB"
            mkdir -p "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null || true
            echo "$(date '+%F %T') hub-io-toggle prov=${_prov:-?} x=${CX:-?} y=${CY:-?}" \
              >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
            "$EZ2_PY3" - "$COCKPIT" "$_prov" <<'PY' >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
PL.toggle_hub_io(provider_id=(sys.argv[2] if len(sys.argv) > 2 else "") or None)
PY
            bash "$COCKPIT/pane-hub-frame.sh" --apply-input-height >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
            bash "$COCKPIT/pane-hub-frame.sh" --ensure >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
            bash "$COCKPIT/pane-hub-frame.sh" --repaint >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --kick >>"$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
          fi

        elif [ "$KIND" = "dialog-scroll-up" ] || [ "$KIND" = "dialog-scroll-down" ]; then
          if [ "$DRY" = "1" ]; then
            printf '+ %s\n' "$KIND"
          else
            DIR=up; [ "$KIND" = "dialog-scroll-down" ] && DIR=down
            "$EZ2_PY3" - "$EZ2_COCKPIT_STATE_DIR" "$DIR" <<'PY' >/dev/null 2>&1 || true
import sys
from pathlib import Path
root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
p = root / "dialog-scroll-offset"
try:
    cur = int(p.read_text(encoding="utf-8").strip() or "0")
except Exception:
    cur = 0
delta = -1 if sys.argv[2] == "up" else 1
p.write_text("%d\n" % max(0, cur + delta), encoding="utf-8")
PY
            # 只重畫對話框；禁止 --ensure（會把 hub 拉高、待審壓扁）
            bash "$COCKPIT/pane-hub-frame.sh" --repaint >/dev/null 2>&1 || true
          fi

        elif [ "$KIND" = "pending-global-toggle" ]; then
          if [ "$DRY" = "1" ]; then
            printf '+ pending-global-toggle\n'
          else
            mkdir -p "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null || true
            gp="$EZ2_COCKPIT_STATE_DIR/pending-global-open"
            cur="$(cat "$gp" 2>/dev/null || true)"
            if [ "$cur" = "1" ]; then printf '0\n' > "$gp"
            else printf '1\n' > "$gp"
            fi
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
          fi
        elif [ "$KIND" = "pending-global-visible-cycle" ]; then
          if [ "$DRY" = "1" ]; then
            printf '+ pending-global-visible-cycle\n'
          else
            "$EZ2_PY3" - "$EZ2_COCKPIT_STATE_DIR" "$COCKPIT/render" <<'PY' >/dev/null 2>&1 || true
import sys
sys.path.insert(0, sys.argv[2])
from render_lib import pending_visible_of, pending_visible_next
from pathlib import Path
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
p = root / "pending-global-visible"
cur = p.read_text(encoding="utf-8").strip() if p.is_file() else ""
p.write_text("%d\n" % pending_visible_next(cur), encoding="utf-8")
PY
            bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
            bash "$COCKPIT/pane-pending-review.sh" --apply-pending-height >/dev/null 2>&1 || true
          fi
        else
          case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
          MENU_PANE="${PANE:-}"
          if [ -z "$MENU_PANE" ] && [ "$DRY" != "1" ]; then
            MENU_PANE="$(lookup_role_pane "${PANE_ROLE:-}")"
          fi
          MENU_TARGET=()
          [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
          set_menu_pos "$MENU_PANE"
          printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
          printf -v QSEAT_Q '%q' "$QSEAT"
          printf -v QITEM_Q '%q' "$QITEM"
          UP_ACTION="run-shell 'bash $SCRIPT_Q --dm-queue-move $QSEAT_Q $QITEM_Q up$RUNSHELL_REDIR'"
          DOWN_ACTION="run-shell 'bash $SCRIPT_Q --dm-queue-move $QSEAT_Q $QITEM_Q down$RUNSHELL_REDIR'"
          if [ "$DRY" = "1" ]; then
            run tmux display-menu -O -M -T "上下移動" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
              "上移" k "$UP_ACTION" \
              "下移" j "$DOWN_ACTION"
          else
            pending_nav_leave
            tmux display-menu -O -M -T "上下移動" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
              ${MENU_POS[@]+"${MENU_POS[@]}"} \
              "上移" k "$UP_ACTION" \
              "下移" j "$DOWN_ACTION" \
              >/dev/null 2>&1 || true
          fi
        fi
        ;;
      dm-queue-up|dm-queue-down|dm-queue-delete|dm-queue-send-now|dm-queue-edit)
        QSEAT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("seat") or "")' "$ACTION")"
        QITEM="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("item") or "")' "$ACTION")"
        QREV="$("$EZ2_PY3" -c 'import json,sys; v=json.loads(sys.argv[1]).get("rev"); print(v if isinstance(v,int) else "")' "$ACTION")"
        case "$QREV" in ''|*[!0-9]*) QREV="" ;; esac
        QEPOCH="$("$EZ2_PY3" -c 'import json,sys; v=json.loads(sys.argv[1]).get("epoch"); print(v if isinstance(v,int) else "")' "$ACTION")"
        case "$QEPOCH" in ''|*[!0-9]*) QEPOCH="" ;; esac
        case "$QSEAT" in ''|*[!0-9a-z]* ) exit 0 ;; esac
        case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
        DQ="$COCKPIT/dm_queue.py"
        if [ "$KIND" = "dm-queue-up" ]; then
          pending_queue_move "$QSEAT" "$QITEM" up
        elif [ "$KIND" = "dm-queue-down" ]; then
          pending_queue_move "$QSEAT" "$QITEM" down
        elif [ "$KIND" = "dm-queue-delete" ]; then
          pending_queue_confirm "刪除暫存" "確定刪除" "--dm-queue-delete-confirmed"
        elif [ "$KIND" = "dm-queue-send-now" ]; then
          # the maintainer 09-30 15:3x(收件匣 A 項 3):待人處理且 hold_rc=3 的信 = 上次**已按了 Enter**、只是回讀沒確認到
          #   (契約:勿重貼)。舊選單照樣給「確定送出」,人一按就是第二份。這種信改成:取消仍排第一、
          #   中間兩行灰字說清楚可能重複、確定項改名「仍要再送一次」—— 要人明確選才送。其他 hold_rc 不變。
          _q_hrc="$("$EZ2_PY3" -c 'import sys; sys.path.insert(0, sys.argv[1]); import dm_queue as Q
it = Q._load_item(sys.argv[2], sys.argv[3]) or {}
print(it.get("hold_rc") if it.get("status") == Q.HOLD_STATUS and it.get("hold_rc") is not None else "")' \
            "$COCKPIT" "$QSEAT" "$QITEM" 2>/dev/null || true)"
          if [ "$_q_hrc" = "3" ]; then
            QCONFIRM_WARN=("上次已送出,但沒確認對方收到" "對方可能已經有這則,再送一次可能重複")
            pending_queue_confirm "再送一次?" "仍要再送一次" "--dm-queue-send-confirmed"
          elif [ "$_q_hrc" = "4" ]; then
            # ★ review r15 M1(佇列):4 比 3 更嚴(契約「絕不重送」):逾時被砍、補按 Enter 後回讀失敗、判不出來 ——
            #   字常常已經進了對方框或已送出。同 3 一樣要人明確選才送。
            QCONFIRM_WARN=("上次字可能已進對方框或已送出" "再送一次可能讓對方收到兩份")
            pending_queue_confirm "再送一次?" "仍要再送一次" "--dm-queue-send-confirmed"
          else
          pending_queue_confirm "立刻送出" "確定送出" "--dm-queue-send-confirmed"
          fi
        else
          # 編輯:四種狀態 + 改留言。恆 exit 0。
          # 選單要開在待審框(滑鼠點的那格),不能用空 PANE——空的話座標
          # 變成視窗左上,選單就跑到最上面。
          MENU_PANE="${PANE:-}"
          if [ -z "$MENU_PANE" ] && [ "$DRY" != "1" ]; then
            MENU_PANE="$(lookup_role_pane "${PANE_ROLE:-}")"
          fi
          MENU_TARGET=()
          [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
          set_menu_pos "$MENU_PANE"
          printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
          # 第 37 項:改緊急程度的回呼也帶這一幀的修訂號(第 5 參數),執行端沒帶一律拒
          PRI_REV=""
          [ -n "${QREV:-}" ] && PRI_REV=" $QREV"
          SETP="run-shell 'bash $SCRIPT_Q --dm-queue-priority $QSEAT $QITEM" # RUNSHELL_REDIR appended at use
          EDIT_OPEN="run-shell 'bash $SCRIPT_Q --pending-edit $QSEAT $QITEM$RUNSHELL_REDIR'"
          if [ "$DRY" = "1" ]; then
            run tmux display-menu -O -M -T "編輯暫存" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
              "緊急" u "${SETP} urgent${PRI_REV}$RUNSHELL_REDIR'" \
              "重要" i "${SETP} important${PRI_REV}$RUNSHELL_REDIR'" \
              "一般" n "${SETP} normal${PRI_REV}$RUNSHELL_REDIR'" \
              "提醒" r "${SETP} reminder${PRI_REV}$RUNSHELL_REDIR'" \
              "改留言" e "$EDIT_OPEN"
          else
            pending_nav_leave
            # 真實路徑也要帶這一幀的修訂號(乾跑那支早就帶);沒帶 ⇒ 執行端 no_rev 一律拒
            # (09-15 00:27/00:28 the maintainer 按「緊急」兩次都被記 refused reason=no_rev,狀態從沒改成過)
            tmux display-menu -O -M -T "編輯暫存" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
              ${MENU_POS[@]+"${MENU_POS[@]}"} \
              "緊急" u "${SETP} urgent${PRI_REV}$RUNSHELL_REDIR'" \
              "重要" i "${SETP} important${PRI_REV}$RUNSHELL_REDIR'" \
              "一般" n "${SETP} normal${PRI_REV}$RUNSHELL_REDIR'" \
              "提醒" r "${SETP} reminder${PRI_REV}$RUNSHELL_REDIR'" \
              "改留言" e "$EDIT_OPEN" \
              >/dev/null 2>&1 || true
          fi
        fi
        ;;
      kill-idle)
        # 08-14 the maintainer 新令:[X] **改彈確認選單**,推翻同日「單擊直殺」直令
        # (舊註解不留,免誤導下一棒)。兩道上游安全欄不變(閒置門檻+三格
        # 精準命中),本處只多插一道人為確認;真殺搬到 --release-confirmed。
        # 選單設計(第三艦長四裁):①標題帶被殺對象身分=角色NN@席位五碼+
        # 窗名+閒置時間(只寫「確定釋放?」等於沒確認);②Esc/點選單外=取消
        # (tmux 原生);③**取消排第一項**——選單開在游標處,若「確定」長在
        # 游標下,手快連點兩下就還原成單擊直殺,這道閘等於白加;④E2.13
        # 鐵律不動:全路徑恆 exit 0。
        WIN="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["window"])' "$ACTION")"
        LABEL="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("label") or "")' "$ACTION")"
        STATUS="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("status") or "")' "$ACTION")"
        SOFT="$("$EZ2_PY3" -c 'import json,sys; print(1 if json.loads(sys.argv[1]).get("soft") is True else 0)' "$ACTION")"
        SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
        HUB="$EZ2_AIHUB_SOCKET"
        if [ "$DRY" != "1" ]; then
          HUB_FROM_SESSION="$(env -u TMUX tmux show-option -qv -t "$SESSION" @aib_hub_socket 2>/dev/null || true)"
          [ -z "$HUB_FROM_SESSION" ] || HUB="$HUB_FROM_SESSION"
        fi
        # 窗名:hub 現查(rowmap 只存 window_id——一輪審中1「跳轉存 id 不存
        # 名」不動);查不到就退窗 id,不因取名失敗擋掉確認閘。
        # DRY 不查:dry-run 全路徑不得碰真 tmux(既有治具以「有沒有呼叫
        # tmux」當絆線)。
        WNAME=""
        if [ "$DRY" != "1" ]; then
          WNAME="$(env -u TMUX tmux -L "$HUB" display-message -p \
            -t "aihub:$WIN" '#{window_name}' 2>/dev/null || true)"
        fi
        [ -n "$WNAME" ] || WNAME="$WIN"
        MENU_TITLE="釋放 ${LABEL:-$WIN} · $WNAME · ${STATUS:-閒置}"
        ABORT_FLAG=""
        if [ "$STATUS" = "pending" ]; then      # 開席中/載入中的列(rowmap status=pending):中斷開席
          MENU_TITLE="中斷開席 ${LABEL:-$WIN} · $WNAME"
          ABORT_FLAG=" --abort-open"
        fi
        # 09-14 the maintainer:最後一席按 [X] 要能關,且警語要說「即將關閉最後一席,本 AIB 將全部關閉」——
        #   關完席把整艙收掉(hub 伺服器 + 外殼 session + 席位檔/佇列)。最後一席=hub 裡除了 watch 只剩它。
        _SEAT_WINS=""
        if [ "$DRY" != "1" ]; then
          _SEAT_WINS="$(env -u TMUX tmux -L "$HUB" list-windows -t aihub -F '#{window_id} #{window_name}' 2>/dev/null \
            | awk '$2!="watch"{print $1}' | wc -l | tr -d ' ')"
        fi
        if [ "${_SEAT_WINS:-0}" = "1" ]; then
          MENU_TITLE="即將關閉最後一席 ${LABEL:-$WIN} · 本 AIB 將全部關閉"
          ABORT_FLAG="$ABORT_FLAG --abort-open --close-cabin"
        fi
        printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
        printf -v WIN_Q '%q' "$WIN"
        printf -v SESSION_Q '%q' "$SESSION"
        # 10-24:(x) 軟關帶選單開出的時間,執行端只問「之後有沒有再動」(見 release-confirmed 段)
        if [ "$SOFT" = "1" ]; then
          ABORT_FLAG="$ABORT_FLAG --soft-since $(date +%s)"
        fi
        CONFIRM_ACTION="run-shell 'bash $SCRIPT_Q --release-confirmed $WIN_Q $SESSION_Q$ABORT_FLAG$RUNSHELL_REDIR'"
        # 取消也留痕(第三艦長二退要的不變量):按下確定後**必定**在
        # mouse-errors.log 留一行(殺了/拒了/取消了)。先前形狀是「按下去
        # 毫無反應也毫無痕跡」,連「他到底按了哪一個」都分不出來——那是
        # 最糟的一種,查起來只能靠猜。
        printf -v LOG_Q '%q' "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log"
        CANCEL_ACTION="run-shell 'printf \"%s release cancelled ($WIN)\\n\" \
\"\$(date \"+%F %T\")\" >> $LOG_Q'"
        # 選單目標 pane:**不能用 TMUX_PANE**——`tmux run-shell` 的子行程
        # 裡它恆不存在(第三艦長活艙實測 TMUX_PANE=<未設>)。先前版本因此
        # 傳空字串給 set_menu_pos,幾何查詢整段被守衛跳過、pl/pt 留在 0,
        # 等於把 pane 相對座標當終端絕對座標用 → 選單落在偏左上(the maintainer 兩次
        # 回報的「卡在中上/左上」)。**它是安靜地用了 0 偏移,連降級日誌
        # 都不會留**,比走降級更難查。
        # 改由 @aib_role 反查:role 是結構穩定的路由鍵(碼內既有紀律「role
        # 永不飄」),而 --click 已經把它當 --role 傳進來;查到的 pane 同時
        # 當 -t 目標與幾何來源=**座標與目標同源**(E2.13 紅線精神:兩者分家
        # 就是這種病的前身)。
        MENU_PANE=""
        if [ "$DRY" != "1" ] && [ -n "${PANE_ROLE:-}" ]; then
          # ★命令替換要包在 if 條件裡:set -euo pipefail 下,list-panes 失敗
          # (hub 不在/socket 不同)會讓裸賦值**靜默中止整支**,rc 漏給
          # run-shell。同型今天第三次(前兩次:探針拒殺、參數 shift),
          # 一律用這個形狀,別再寫裸賦值。
          if ! MENU_PANE="$(env -u TMUX tmux list-panes -a \
              -F '#{pane_id} #{@aib_role}' 2>/dev/null \
              | awk -v r="$PANE_ROLE" '$2==r{print $1; exit}')"; then
            MENU_PANE=""
          fi
        fi
        # ★bash 3.2 + set -u:空陣列 "${arr[@]}" 會報 unbound variable
        # 而**靜默中止**(macOS 系統 bash 就是 3.2,測試走 shebang 直執行
        # 因此踩得到;用 homebrew bash 5 手動跑則不會——「本機綠證明不了
        # bash 3.2」的第二種形狀)。展開一律用 ${arr[@]+"${arr[@]}"}。
        MENU_TARGET=()
        [ -n "$MENU_PANE" ] && MENU_TARGET=(-t "$MENU_PANE")
        # 連點去抖(08-14 真人使用實證:@8 被連按四次,因為列要等下一幀
        # 最多 5 秒才消失,看不到回饋就再按)。點下即把該列標成
        # kill_pending,hit_test 見到就 passthrough=**立刻不可再點**,不等
        # 重繪;下一幀 rowmap 重發自然解除(窗真被殺的話該列本來就沒了)。
        # 原子換檔沿 rowmap 既有紀律(temp+rename,不半寫)。
        if [ "$DRY" = "1" ]; then
          printf '+ debounce kill_pending %s\n' "$WIN"
        else
          "$EZ2_PY3" - "$EZ2_COCKPIT_STATE_DIR/ai-rowmap.json" "$WIN" <<'PY' 2>/dev/null || true
import json, os, sys
path, win = sys.argv[1], sys.argv[2]
try:
    rows = json.load(open(path, encoding='utf-8'))
except (OSError, ValueError):
    sys.exit(0)
for r in rows:
    if isinstance(r, dict) and r.get('window') == win and r.get('kill'):
        r['kill_pending'] = True
tmp = '%s.debounce.%d' % (path, os.getpid())
with open(tmp, 'w', encoding='utf-8') as fh:
    json.dump(rows, fh, ensure_ascii=False)
os.replace(tmp, path)
PY
        fi
        set_menu_pos "$MENU_PANE"
        if [ "$DRY" = "1" ]; then
          run tmux display-menu -O -M -T "$MENU_TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
            ${MENU_POS[@]+"${MENU_POS[@]}"} \
            "取消" q "$CANCEL_ACTION" "" "確定釋放" y "$CONFIRM_ACTION"
        else
          tmux display-menu -O -M -T "$MENU_TITLE" ${MENU_TARGET[@]+"${MENU_TARGET[@]}"} \
            ${MENU_POS[@]+"${MENU_POS[@]}"} \
            "取消" q "$CANCEL_ACTION" "" "確定釋放" y "$CONFIRM_ACTION" >/dev/null 2>&1 || true
        fi
        ;;
      *)
        [ "$DRY" = "1" ] && printf '+ passthrough\n' || true
        ;;
    esac
    ;;
  theme-menu)
    # 主題底色選單(the maintainer「任一格按右鍵→就在那一格彈出選單→選既有主題底色→
    # 套用到該格」)。底層是設計席第一刀的 theme-ctl.sh,本刀只做 UI 掛接。
    # ★沿用 set_menu_pos 座標機制**不重造**(設計席規格);★目標由**事件帶的
    # pane_id 反查 @aib_role**,不用會飄的 @aib_pane(視圖 respawn 會洗標);
    # ★色清單從 `theme.py palette` 長出來**不寫死十色**;★「恢復預設」=
    # **清除覆寫**不是寫入當下預設色(設計席已有測試釘住,掛接不得繞過)。
    exec 2>/dev/null                     # 選用 UI,不在 AI pane 產生噪音
    _pal="$("$EZ2_PY3" "$COCKPIT/theme.py" palette 2>/dev/null || true)"
    [ -n "$_pal" ] || exit 0
    _trole=""
    if [ "$DRY" = "1" ]; then
      _trole="right"                     # dry 不碰真 tmux(治具絆線)
    elif [ -n "$PANE" ]; then
      if ! _trole="$(env -u TMUX tmux display-message -p -t "$PANE" \
          '#{@aib_role}' 2>/dev/null)"; then
        _trole=""
      fi
    fi
    [ -n "$_trole" ] || exit 0           # 反查不到角色=不開(fail-closed)
    set_menu_pos "$PANE"
    _tt=()
    [ -n "$PANE" ] && _tt=(-t "$PANE")
    printf -v THEME_Q '%q' "$COCKPIT/theme-ctl.sh"
    printf -v ROOT_Q '%q' "$EZ2_AIB_ROOT"
    printf -v ROLE_Q '%q' "$_trole"
    printf -v SESS_Q '%q' "${SESSION:-$EZ2_TMUX_SESSION}"
    _ids=(); _keys=()
    while IFS='|' read -r _pid _pkey _rest; do
      [ -n "$_pid" ] || continue
      _ids+=("$_pid"); _keys+=("$_pkey")
    done <<EOF
$_pal
EOF
    _labels="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" \
      ${_keys[@]+"${_keys[@]}"} aib.context.menu.theme_color \
      aib.context.menu.theme_reset aib.context.menu.theme_none \
      aib.context.menu.theme_reset_and_none \
      2>/dev/null || true)"
    _n=${#_ids[@]}
    _titem=()
    _i=0
    while [ "$_i" -lt "$_n" ]; do
      _lbl="$(printf '%s\n' "$_labels" | sed -n "$((_i + 1))p")"
      [ -n "$_lbl" ] || _lbl="${_ids[$_i]}"
      printf -v _pq '%q' "${_ids[$_i]}"
      _titem+=("$_lbl" "$((_i + 1))" \
        "run-shell 'cd $ROOT_Q && $THEME_Q --set-override $ROLE_Q $_pq $SESS_Q$RUNSHELL_REDIR'")
      _i=$((_i + 1))
    done
    _ttitle="$(printf '%s\n' "$_labels" | sed -n "$((_n + 1))p")"
    _treset="$(printf '%s\n' "$_labels" | sed -n "$((_n + 2))p")"
    _tnone="$(printf '%s\n' "$_labels" | sed -n "$((_n + 3))p")"
    [ -n "$_ttitle" ] || _ttitle="theme"
    [ -n "$_treset" ] || _treset="reset"
    [ -n "$_tnone" ] || _tnone="no background"
    # 「無底色」與「恢復預設底色」**並列不合併**(the maintainer 08-14 裁):前者=不上色
    # 讓終端背景透出,後者=回主題預設色,而主題預設本身就是有色的。今天已證實
    # 使用者會把兩者讀成同一件事,所以兩列都要在。
    #
    # 無底色是全艙開關(覆寫檔保留鍵 `_cockpit`),不帶 role——四格與兩條
    # status 列一起變,理由見 theme-ctl.sh 的 COCKPIT_KEY 註解。
    # 恢復預設**同時關掉無底色**:否則使用者開了無底色之後再按「恢復預設
    # 底色」,畫面一個位元都不會變——那正是裁定 1 要避免的混淆換方向發作。
    #
    # 代價是這一鍵的作用域比其他鍵大(全艙),所以 root3 08-15 裁:行為照舊
    # 但**文字要動態揭露作用域**——無底色開著時才顯示「並取消無底色」。
    # 理由=使用者有權在按下之前知道作用域;**沒告知的副作用等於靜默**。
    if "$COCKPIT/theme-ctl.sh" --is-no-bg 2>/dev/null; then
      _tresetlbl="$(printf '%s\n' "$_labels" | sed -n "$((_n + 4))p")"
      [ -n "$_tresetlbl" ] || _tresetlbl="$_treset"
    else
      _tresetlbl="$_treset"
    fi
    _titem+=("" "$_tresetlbl" "0" \
      "run-shell 'cd $ROOT_Q && $THEME_Q --clear-override $ROLE_Q $SESS_Q && $THEME_Q --clear-no-bg $SESS_Q$RUNSHELL_REDIR'")
    # ★ the maintainer 09-10 20:4x 裁:兩列**對調**——「切換可右鍵自定底色」(原「恢復預設
    #   底色」,行為不變:清 per-region 覆寫 + 關全艙無底色)在上,
    #   「透明淡黑(不可改色)」(原「無底色(透出終端)」,行為不變:--set-no-bg)在下。
    #   字面改成兩種**模式**的名字:透明淡黑時右鍵選色無效(所以標「不可改色」),
    #   切回可自定後 1–9 才生效。快捷鍵 0 / n 不動。
    _titem+=("" "$_tnone" "n" \
      "run-shell 'cd $ROOT_Q && $THEME_Q --set-no-bg $SESS_Q$RUNSHELL_REDIR'")
    if [ "$DRY" = "1" ]; then
      run env -u TMUX tmux display-menu -O -M -T "$_ttitle" \
        ${_tt[@]+"${_tt[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} "${_titem[@]}"
    else
      env -u TMUX tmux display-menu -O -M -T "$_ttitle" \
        ${_tt[@]+"${_tt[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} "${_titem[@]}" \
        >/dev/null 2>&1 || true
    fi
    exit 0
    ;;
  bind-menu)
    # L2-03:由 run-shell 回呼(或 --click 直接進);任何參數形狀恆 exit 0。
    exec 2>>"$RUNSHELL_LOG"      # 選用 UI:壞狀態不進 AI pane,但要留痕給查
    case "$BIND_SEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    bind_menu_layer
    exit 0
    ;;
  new-ai-key)
    # 缺金鑰那一項:白名單內、api_key 那一組才動;值只在 aib secret ask 的遮字框與 Touch ID 裡走,不經過這裡。
    case " $(new_ai_providers) " in
      *" $NEW_AI_ENGINE "*) ;;
      *) exit 0 ;;
    esac
    if [ "$DRY" = "1" ]; then
      run "$EZ2_PY3" "$HERE/open_readiness.py" ask-key "$NEW_AI_ENGINE"
      exit 0
    fi
    _notice="$("$EZ2_PY3" "$HERE/open_readiness.py" ask-key "$NEW_AI_ENGINE" 2>>"$RUNSHELL_LOG" || true)"
    [ -n "$_notice" ] || exit 0
    _target=()
    [ -n "$PANE" ] && _target=(-t "$PANE")
    env -u TMUX tmux display-message -d 15000 \
      ${_target[@]+"${_target[@]}"} "$_notice" >/dev/null 2>&1 || true
    exit 0
    ;;
  new-ai-open)
    # 右鍵開 CodexBot 原本把 preflight 錯誤全丟進 aib-open.log，使用者只會
    # 看到「沒反應」。這層保留完整日誌，同時把可行動原因顯示在艙底部。
    # 10-09 a seat:所有橋接 bot(providers.json menu_group=bot)都走這一層 —— 以前只有 codexbot;
    #   其他家在角色層直接 run-shell 開席,失敗只進 aib-open.log,畫面一個字都沒有。
    _is_bot_engine "$NEW_AI_ENGINE" || exit 0
    case "$NEW_AI_ROLE" in ''|*[!a-z0-9-]*) exit 0 ;; esac
    if [ -n "$NEW_AI_REPORTS_TO" ] && [[ ! "$NEW_AI_REPORTS_TO" =~ ^[0-9a-z]{4,8}$ ]]; then
      exit 0
    fi
    _cmd=(bash "$COCKPIT/aib" "$NEW_AI_ENGINE" dgrs "$NEW_AI_ROLE" --again)
    [ -z "$NEW_AI_REPORTS_TO" ] || _cmd+=(--reports-to "$NEW_AI_REPORTS_TO")
    if [ "$DRY" = "1" ]; then
      run env -u TMUX "${_cmd[@]}"
      exit 0
    fi
    _open_out="$(mktemp "${TMPDIR:-/tmp}/aib-new-ai-open.XXXXXX" 2>/dev/null)" || _open_out=""
    if [ -n "$_open_out" ]; then
      _open_rc=0; (cd "$EZ2_AIB_ROOT" && "${_cmd[@]}") >"$_open_out" 2>&1 || _open_rc=$?
      cat "$_open_out" >>"$RUNSHELL_LOG" 2>/dev/null || true
    else
      _open_rc=0; (cd "$EZ2_AIB_ROOT" && "${_cmd[@]}") >>"$RUNSHELL_LOG" 2>&1 || _open_rc=$?
    fi
    if [ "$_open_rc" = "0" ]; then
      [ -z "$_open_out" ] || rm -f "$_open_out"
      exit 0
    fi
    # 這一次開席自己講的最後一句 aib: 說明(開席取消的原因,例如那段對話正被別席占著);拿不到才給通用句。
    #   10-09 a seat:codexbot 以前一律「尚未連結」—— 對話被占、上次那條不在、--check 失敗都講成同一句。
    _why=""
    [ -z "$_open_out" ] || _why="$(grep -a '^aib: ' "$_open_out" 2>/dev/null | tail -n 1 | cut -c1-300)"
    if [ -n "$_why" ]; then
      _notice="$_why"
    elif [ "$NEW_AI_ENGINE" = "codexbot" ]; then
      _notice="DotsBot 尚未連結。請先執行：aib bot connect codexbot --thread <UUID> --endpoint <本機位址>"
    else
      _notice="aib: ${NEW_AI_ENGINE} 開席失敗(rc=${_open_rc});原因在 ${RUNSHELL_LOG}"
    fi
    [ -z "$_open_out" ] || rm -f "$_open_out"
    _target=()
    [ -n "$PANE" ] && _target=(-t "$PANE")
    env -u TMUX tmux display-message -d 15000 \
      ${_target[@]+"${_target[@]}"} "$_notice" >/dev/null 2>&1 || true
    exit 0
    ;;
  new-ai-menu)
    # E2.13:由 run-shell 呼叫,任何參數形狀恆 exit 0(選單開不起來就不開,
    # 不得把錯誤畫面蓋上艙)。座標由上一層傳下來,三層開在同一點。
    exec 2>/dev/null            # 選用 UI,壞狀態不在 AI pane 產生噪音(同 context)
    _providers="$(new_ai_providers)"
    [ -n "$_providers" ] || exit 0
    set_menu_pos "$PANE"
    _menu_target=()
    [ -n "$PANE" ] && _menu_target=(-t "$PANE")
    printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
    printf -v PANE_ARG_Q '%q' "$PANE"
    _items=()
    if [ -z "$NEW_AI_ENGINE" ]; then
      # 第一層:引擎(清單從 aib PROVIDERS 長出來,新品牌自動出現)
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.context.menu.new_ai \
        2>/dev/null | sed -n '1p' || true)"
      # 10-24 @abb89:開席前先看每家缺不缺東西(open_readiness.py,一次問完、一家一行;查壞了一律當 ok)。
      _ready_lines=()
      while IFS= read -r _rl; do _ready_lines+=("$_rl"); done \
        < <("$EZ2_PY3" "$HERE/open_readiness.py" $_providers 2>/dev/null || true)
      _ri=0
      for _p in $_providers; do
        _ready="${_ready_lines[$_ri]:-ok}"
        _ri=$((_ri + 1))
        # filter_new_ai_providers.py 在 native/bot/api_key 段之間插入 __SEP__
        if [ "$_p" = "__SEP__" ]; then
          _items+=("" "" "")
          continue
        fi
        printf -v _pq '%q' "$_p"
        _plabel="$(new_ai_engine_label "$_p")"
        #   缺金鑰 ⇒ 寫「缺金鑰」、點了叫出 aib secret ask,不開死窗;bot 沒接好 ⇒ 寫缺哪步、照樣點得開。
        _rstate=ok _rdetail="" _rsuffix=""
        IFS=$'\t' read -r _rstate _rdetail _rsuffix <<< "$_ready"
        [ -z "${_rsuffix:-}" ] || _plabel="$_plabel #[fg=colour244]${_rsuffix}#[default]"
        # the maintainer 09-20:引擎名右側不要數字快捷鍵(與 bind-menu 同形,key 留空)
        if [ "${_rstate:-ok}" = "need_key" ]; then
          _items+=("$_plabel" "" "run-shell -b 'bash $SCRIPT_Q --new-ai-key $_pq $PANE_ARG_Q$RUNSHELL_REDIR'")
        else
          _items+=("$_plabel" "" "run-shell 'bash $SCRIPT_Q --new-ai-menu $_pq $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
        fi
      done
    else
      # 第二層:角色 → 真正開席(--again=同 hub 次席,dgrs=完整模式)
      case " $_providers " in
        *" $NEW_AI_ENGINE "*) ;;
        *) exit 0 ;;            # 不在白名單的引擎=不開(fail-closed)
      esac
      _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.context.menu.new_ai_role \
        2>/dev/null | sed -n '1p' || true)"
      _title="$_title ($(new_ai_engine_label "$NEW_AI_ENGINE"))"
      printf -v AIB_Q '%q' "$COCKPIT/aib"
      # ★開席前必須顯式切到 repo 工作目錄,不能靠繼承 run-shell 的 cwd:
      #   run-shell 的 cwd 是 `/Users/user`(不是 repo),而該資料夾在
      #   ~/.claude.json 是 hasTrustDialogAccepted=False → Claude Code 會問
      #   「Is this a project you created or one you trust?」並**停住等人按 1**
      #   (08-14 @14/@15 兩席都中,不是偶發;aib:22 用 `pwd -P` 當新席 cwd)。
      #   與 08-10「四條 lane 全滅」同形狀:開起來了、字也在畫面上,但落進一個
      #   需要真人回答的提示。
      # ★路徑取 EZ2_AIB_ROOT(cockpit-env.sh:53 既有正本,由 cockpit 目錄推導)
      #   ——**不寫死任何人的 repo 路徑**,否則換機器或換 instance 就再爆一次;
      #   本機已有多 instance 在跑(instances/5,6,7 各有自己的 seats,the maintainer 刻意)。
      # ★也不靠「把該資料夾加進信任清單」解:那是改使用者的全域設定去遷就
      #   我們的 bug,還會降低他在別處的安全提示。
      printf -v ROOT_Q '%q' "$EZ2_AIB_ROOT"
      _k=1
      _role_lines="$("$EZ2_PY3" "$HERE/role_menu_label.py" $NEW_AI_ROLES 2>/dev/null || true)"
      if [ -z "$_role_lines" ]; then
        for _r in $NEW_AI_ROLES; do
          _role_lines="${_role_lines}${_r}"$'\t'"${_r}"$'\n'
        done
      fi
      _have_caps=0
      # 不得 `head -1`:pipefail 下 SIGPIPE 會讓整段變空,兩位以上艦長時
      # 角色層會誤判成沒有艦長、直接開席。
      _cap_list="$("$EZ2_PY3" "$HERE/list_captains.py" 2>/dev/null || true)"
      [ -n "$_cap_list" ] && _have_caps=1
      printf -v _eq '%q' "$NEW_AI_ENGINE"
      while IFS=$'\t' read -r _r _disp; do
        [ -n "$_r" ] || continue
        [ -n "$_disp" ] || _disp="$_r"
        printf -v _rq '%q' "$_r"
        _fam="$(printf '%s' "$_r" | sed 's/[0-9][0-9]*$//')"
        [ -n "$_fam" ] || _fam="$_r"
        if [ "$_have_caps" = "1" ] && [ "$_fam" != "root" ]; then
          _items+=("$_disp" "$_k" "run-shell -b 'bash $SCRIPT_Q --new-ai-hang $_eq $_rq $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'")
        elif _is_bot_engine "$NEW_AI_ENGINE"; then
          _items+=("$_disp" "$_k" "run-shell -b 'bash $SCRIPT_Q --new-ai-open $_eq $_rq - $PANE_ARG_Q$RUNSHELL_REDIR'")
        else
          _items+=("$_disp" "$_k" "run-shell -b 'cd $ROOT_Q && $AIB_Q $NEW_AI_ENGINE dgrs $_rq --again$RUNSHELL_REDIR'")
        fi
        _k=$((_k + 1))
      done <<< "$_role_lines"
    fi
    [ -n "$_title" ] || _title="AI"
    if [ "$DRY" = "1" ]; then
      run env -u TMUX tmux display-menu -O -M -T "$_title" \
        ${_menu_target[@]+"${_menu_target[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} \
        "${_items[@]}"
    else
      env -u TMUX tmux display-menu -O -M -T "$_title" \
        ${_menu_target[@]+"${_menu_target[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} \
        "${_items[@]}" >/dev/null 2>&1 || true
    fi
    exit 0
    ;;
  new-ai-hang)
    # 第三層:已有艦長時選掛點。沒有艦長(競態)就直接開席,預設第一艦長。
    # E2.13:任何參數形狀恆 exit 0。
    exec 2>/dev/null
    _providers="$(new_ai_providers)"
    [ -n "$_providers" ] || exit 0
    case " $_providers " in
      *" $NEW_AI_ENGINE "*) ;;
      *) exit 0 ;;
    esac
    case "$NEW_AI_ROLE" in ''|*[!a-z0-9-]*) exit 0 ;; esac
    set_menu_pos "$PANE"
    _menu_target=()
    [ -n "$PANE" ] && _menu_target=(-t "$PANE")
    printf -v AIB_Q '%q' "$COCKPIT/aib"
    printf -v ROOT_Q '%q' "$EZ2_AIB_ROOT"
    printf -v ENGINE_Q '%q' "$NEW_AI_ENGINE"
    printf -v ROLE_Q '%q' "$NEW_AI_ROLE"
    printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
    printf -v PANE_ARG_Q '%q' "$PANE"
    _caps="$("$EZ2_PY3" "$HERE/list_captains.py" 2>/dev/null || true)"
    if [ -z "$_caps" ]; then
      if _is_bot_engine "$NEW_AI_ENGINE"; then
        _dry_arg=()
        [ "$DRY" != "1" ] || _dry_arg=(--dry-run)
        bash "$HERE/mouse-ctl.sh" --new-ai-open "$NEW_AI_ENGINE" "$NEW_AI_ROLE" - "$PANE" \
          ${_dry_arg[@]+"${_dry_arg[@]}"}
        exit 0
      fi
      if [ "$DRY" = "1" ]; then
        run env -u TMUX tmux run-shell -b \
          "cd $ROOT_Q && $AIB_Q $ENGINE_Q dgrs $ROLE_Q --again$RUNSHELL_REDIR"
      else
        env -u TMUX tmux run-shell -b \
          "cd $ROOT_Q && $AIB_Q $ENGINE_Q dgrs $ROLE_Q --again$RUNSHELL_REDIR" \
          >/dev/null 2>&1 || true
      fi
      exit 0
    fi
    _title="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.context.menu.new_ai_hang \
      2>/dev/null | sed -n '1p' || true)"
    [ -n "$_title" ] || _title="captain"
    _deflbl="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" aib.context.menu.new_ai_hang_default \
      2>/dev/null | sed -n '1p' || true)"
    _items=()
    _k=1
    while IFS=$'\t' read -r _sid _disp; do
      [ -n "$_sid" ] || continue
      case "$_sid" in *[!0-9a-z]*) continue ;; esac
      [ -n "$_disp" ] || _disp="$_sid"
      if [ "$_k" -eq 1 ] && [ -n "$_deflbl" ]; then
        _disp="$_disp $_deflbl"
      fi
      printf -v _sq '%q' "$_sid"
      if _is_bot_engine "$NEW_AI_ENGINE"; then
        _items+=("$_disp" "$_k" "run-shell -b 'bash $SCRIPT_Q --new-ai-open $ENGINE_Q $ROLE_Q $_sq $PANE_ARG_Q$RUNSHELL_REDIR'")
      else
        _items+=("$_disp" "$_k" "run-shell -b 'cd $ROOT_Q && $AIB_Q $ENGINE_Q dgrs $ROLE_Q --again --reports-to $_sq$RUNSHELL_REDIR'")
      fi
      _k=$((_k + 1))
    done <<< "$_caps"
    [ "${#_items[@]}" -gt 0 ] || exit 0
    if [ "$DRY" = "1" ]; then
      run env -u TMUX tmux display-menu -O -M -T "$_title" \
        ${_menu_target[@]+"${_menu_target[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} \
        "${_items[@]}"
    else
      env -u TMUX tmux display-menu -O -M -T "$_title" \
        ${_menu_target[@]+"${_menu_target[@]}"} ${MENU_POS[@]+"${MENU_POS[@]}"} \
        "${_items[@]}" >/dev/null 2>&1 || true
    fi
    exit 0
    ;;
  perf-close-confirmed|perf-reap-confirmed)
    # 效能頁確認後的真動作(E 面板第三片)。兩紅線在這裡不重造判準:
    # close 內部自帶重掃+classify(殺前重驗)且 human_attached rc=3 拒收;
    # reap 走 idle_reaper 自己的 auto 閘與留痕(idle-reaper.jsonl)。本處
    # 職責只有:呼叫、把「拒收原因」顯示給人(rc=3 不是失敗)、成功後使
    # idle-scan-cache 失效讓下一幀清單即更新。E2.13 鐵律:恆 exit 0。
    if [ "$MODE" = "perf-close-confirmed" ]; then
      [ -n "$PERF_ID" ] || exit 0
      case "$PERF_ID" in
        ''|*[!A-Za-z0-9:/._-]*)
          echo "$(date '+%F %T') perf close rejected (bad id)"             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          exit 0 ;;
        cli:*)
          # 紅線:cli:* 滑鼠路徑永不收(the maintainer 未一句放寬前不動)。正常到不了
          # 這裡(cli 恆 auto=false 無 [X]),到了=上游被繞,拒收留痕。
          if [ "$DRY" = "1" ]; then
            printf '+ refused cli target %s\n' "$PERF_ID"
          else
            tmux display-message "aib: cli 目標不走滑鼠收艙" >/dev/null 2>&1 || true
          fi
          echo "$(date '+%F %T') perf close refused (cli red line): $PERF_ID"             >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
          exit 0 ;;
      esac
      if [ "$DRY" = "1" ]; then
        run "$COCKPIT/aib" idle close "$PERF_ID" --yes
        printf '+ invalidate idle-scan-cache\n'
        exit 0
      fi
      # ★無 --force、無例外:有人接著 close 自己會 rc=3。命令替換包 if
      #   (set -e 紀律);stderr 才是拒收原因,一併收。
      if PERF_OUT="$("$COCKPIT/aib" idle close "$PERF_ID" --yes 2>&1)"; then
        PERF_RC=0
      else
        PERF_RC=$?
      fi
      if [ "$PERF_RC" = "0" ]; then
        rm -f "$EZ2_COCKPIT_STATE_DIR/idle-scan-cache" 2>/dev/null || true
        tmux display-message "aib: 已收艙 $PERF_ID" >/dev/null 2>&1 || true
      else
        # rc=3=拒收(有人接著/重驗未過)、rc=2=目標已不在——都要讓人看到
        # 原因,不是靜默失敗;取輸出最後一行當理由(close 的訊息在那)。
        PERF_WHY="$(printf '%s\n' "$PERF_OUT" | tail -1)"
        tmux display-message "aib: 未收 $PERF_ID(rc=$PERF_RC)${PERF_WHY:+ $PERF_WHY}"           >/dev/null 2>&1 || true
      fi
      echo "$(date '+%F %T') perf close $PERF_ID rc=$PERF_RC"         >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      exit 0
    fi
    # perf-reap-confirmed:全關=同一道 reap 閘(不逐一 close,不另造清單)
    if [ "$DRY" = "1" ]; then
      run "$COCKPIT/aib" idle reap
      printf '+ invalidate idle-scan-cache\n'
      exit 0
    fi
    if PERF_OUT="$("$COCKPIT/aib" idle reap 2>&1)"; then
      PERF_RC=0
    else
      PERF_RC=$?
    fi
    rm -f "$EZ2_COCKPIT_STATE_DIR/idle-scan-cache" 2>/dev/null || true
    if [ "$PERF_RC" = "0" ]; then
      tmux display-message "aib: 全關完成" >/dev/null 2>&1 || true
    else
      tmux display-message "aib: 全關部分未完成(rc=$PERF_RC,詳 idle-reaper.jsonl)"         >/dev/null 2>&1 || true
    fi
    echo "$(date '+%F %T') perf reap rc=$PERF_RC"       >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
    exit 0
    ;;
  release-confirmed)
    # [X] 確認選單按下「確定」後的真殺路徑(08-14 the maintainer 新令拆出;殺窗鏈
    # 語意與前版逐字相同,只是前面多了一道人為確認)。職責=殺前留影
    # (誠實可追)+真殺+fail-visible;席位檔不直接刪,交 captains-ctl 既有
    # 死席清理閘(勿繞原子/交叉驗)。E2.13 鐵律:恆 exit 0。
    [ -n "$WINDOW" ] || exit 0
    WIN="$WINDOW"
    SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
    if ! aib_cabin_env_from_session "$SESSION"; then
      echo "$(date '+%F %T') release refused ($WIN): session '$SESSION' 取不到艙身份(@aib_instance/@aib_hub_socket),不動手" \
        >> "${EZ2_COCKPIT_STATE_DIR:-/tmp}/mouse-errors.log" 2>/dev/null || true
      tmux display-message "aib: 取不到這一艙的身份,不關席" >/dev/null 2>&1 || true
      exit 0
    fi
    HUB="$EZ2_AIHUB_SOCKET"
    # ★殺前重驗閒置(第三艦長退件補正):**確認選單會等人,而人可以等任意
    # 久**——選單開著的期間目標席完全可能又活起來(被 DM、被喚醒、自己續
    # 工),此時按下確定就會殺掉正在工作的窗。這個時間差是「加確認」這一刀
    # 自己創造出來的(前版單擊直殺,判定與執行之間幾乎沒有間隔),安全機制
    # 引進的新不安全面要在同一刀補掉。
    # 判定重用 activity_probe(**單一真相源**,不另寫一份:兩份實作遲早分岔
    # 成「面板說閒置、殺前說不閒置」);rc 0=達門檻可殺,其餘一律拒殺
    # (fail-closed:無訊號也拒)。
    # ★命令替換必須包在 if 條件裡:本檔 set -euo pipefail,裸賦值一旦拿到
    # 非零就整支中止,rc 漏給 run-shell=錯誤畫面蓋艙(E2.13 鐵律破)。
    # 探針「拒殺」本來就是回非零的正常路徑,踩過一次(rc=3 漏出)。
    # 10-24 @abb89(右鍵實測):(x) 是「有 💤、未達 [X] 門檻」的軟關入口(Andy 09-21),先前這裡照樣拿
    #   300 秒門檻重驗 ⇒ 按 (x) 再按確定一定被拒,還誤報「已恢復活動」。軟關改問「選單開出之後有沒有再動」:
    #   門檻 = 選單開著的秒數(至少 1)。這道重驗存在的原因(選單等人的期間席又活起來)照樣擋得住。
    _PROBE_TH=()
    #   時間在未來(手打 / 時鐘往回調)⇒ 不放寬，照 [X] 的 300 秒;不收前導零(bash 算術會當八進位、set -e 中止)。
    if [[ "$SOFT_SINCE" =~ ^[1-9][0-9]{8,10}$ ]]; then
      _SOFT_WAIT=$(( $(date +%s) - SOFT_SINCE ))
      if [ "$_SOFT_WAIT" -ge 0 ]; then
        [ "$_SOFT_WAIT" -ge 1 ] || _SOFT_WAIT=1
        _PROBE_TH=(--threshold "$_SOFT_WAIT")
      fi
    fi
    if PROBE_OUT="$("$EZ2_PY3" "$COCKPIT/activity_probe.py" \
         --window "$WIN" ${_PROBE_TH[@]+"${_PROBE_TH[@]}"} 2>/dev/null)"; then
      PROBE_RC=0
    else
      PROBE_RC=$?
    fi
    # ★ 08-29 the maintainer 直令(接 [X] 三層驗屍):人按了 X 又按了確定=兩重明示,
    #   「無訊號」不得再擋(無訊號席連 [X] 都永遠殺不掉=安全機制自鎖)。
    #   只有「探針明說 active(剛恢復活動)」才拒——那是這道重驗存在的原因。
    _PROBE_SRC="$("$EZ2_PY3" -c 'import json,sys
try: print(json.loads(sys.argv[1]).get("source") or "")
except Exception: print("")' "${PROBE_OUT:-}" 2>/dev/null || echo "")"
    if [ "${ABORT_OPEN:-0}" = "1" ]; then
      echo "$(date '+%F %T') abort-open human-confirmed ($WIN): 中斷開席,不做閒置重驗(兩重明示)" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      PROBE_RC=0
    fi
    if [ "$PROBE_RC" != "0" ] && [ "$_PROBE_SRC" != "active" ]; then
      echo "$(date '+%F %T') release no-signal human-confirmed ($WIN): 放行(兩重明示)"         >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      PROBE_RC=0
    fi
    # 10-24(審查第 1 位):(x) 的 💤 看的是席位檔自報時間,對話檔可能幾分鐘前才寫過;席若正在跑一個很長的
    #   工具呼叫,對話檔不會動,只看「選單開出後有沒有寫」會把它關掉。軟關再取一次 CPU(探針同一支):
    #   還在跑 ⇒ 拒;量不到 ⇒ 照 08-29「兩重明示,無訊號不擋」。DRY 給一個不存在的絕對路徑,碰不到真 tmux。
    if [ "$PROBE_RC" = "0" ] && [ ${#_PROBE_TH[@]} -gt 0 ] && [ "${ABORT_OPEN:-0}" != "1" ]; then
      if [ "$DRY" = "1" ]; then
        _CPU_SOCK="/nonexistent/aib-dry-socket"
      else
        _CPU_SOCK="$(env -u TMUX tmux -L "$HUB" display-message -p '#{socket_path}' 2>/dev/null || true)"
      fi
      _CPU_OUT=""
      if [ -n "$_CPU_SOCK" ]; then
        _CPU_OUT="$("$EZ2_PY3" "$COCKPIT/activity_probe.py" --window "$WIN" --sample-socket "$_CPU_SOCK" 2>/dev/null || true)"
      fi
      if [ "$("$EZ2_PY3" -c 'import json,sys
try: print(json.loads(sys.argv[1]).get("source") or "")
except Exception: print("")' "$_CPU_OUT" 2>/dev/null || echo "")" = "active" ]; then
        PROBE_RC=3
        PROBE_OUT='{"source": "busy"}'
      fi
    fi
    if [ "$PROBE_RC" != "0" ]; then
      REASON="$("$EZ2_PY3" -c 'import json,sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
src = d.get("source")
idle = d.get("idle_s")
if src == "busy":
    print("該席還在跑(CPU 有在動),取消釋放")
elif src == "active":
    print("該席已恢復活動(閒置 %ss<門檻),取消釋放" % idle)
else:
    print("拿不到活動跡證,拒絕釋放(寧缺勿假)")' "${PROBE_OUT:-}" 2>/dev/null \
        || echo "重驗未通過,取消釋放")"
      # 人要看得到為什麼沒殺;display-message 失敗也不得漏 rc(E2.13)。
      # DRY 不碰真 tmux(治具以「有沒有呼叫 tmux」當絆線)。
      if [ "$DRY" = "1" ]; then
        printf '+ refused %s %s\n' "$WIN" "$REASON"
      else
        tmux display-message "aib: $REASON" >/dev/null 2>&1 || true
      fi
      echo "$(date '+%F %T') release refused ($WIN): $REASON" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      exit 0
    fi
    if [ "$DRY" != "1" ]; then
      HUB_FROM_SESSION="$(env -u TMUX tmux show-option -qv -t "$SESSION" @aib_hub_socket 2>/dev/null || true)"
      [ -z "$HUB_FROM_SESSION" ] || HUB="$HUB_FROM_SESSION"
    fi
    if [ "$DRY" = "1" ]; then
      run env -u TMUX tmux -L "$HUB" kill-window -t "aihub:$WIN"
    else
      # 殺前留影。★空內容不落檔(08-14 真人使用實證:13 個留影裡 5 個是
      # 0 bytes——窗已被殺時 capture 取不到東西,`> "$SNAP"` 照樣把檔建出來)。
      # 空的「留影」不是證據是噪音,還會稀釋真快照的可信度。
      SNAP="$EZ2_COCKPIT_STATE_DIR/aib-idle-release-${WIN#@}-$(date +%s).txt"
      CAP="$(env -u TMUX tmux -L "$HUB" capture-pane -p -t "aihub:$WIN" \
        2>/dev/null || true)"
      if [ -n "${CAP//[[:space:]]/}" ]; then
        printf '%s\n' "$CAP" > "$SNAP" 2>/dev/null || true
        # ★ B10:留影是第二個真消費點。恢復指令**不在這裡組**——
        #   同一份 provider 表寫兩處,兩處會各自漂移,而漂移不會有任何一格變紅。
        #   單一真相源是 activity_probe.py --resume-command,它查不到會印「量不到」,
        #   所以這裡不需要、也不可以判斷「有沒有值」:那個判斷會把誠實的量不到吃掉。
        _RESUME="$("$EZ2_PY3" "$COCKPIT/activity_probe.py" \
          --window "$WIN" --resume-command 2>/dev/null || true)"
        printf '%s\n' "$_RESUME" >> "$SNAP" 2>/dev/null || true
      fi
      if ! env -u TMUX tmux -L "$HUB" kill-window -t "aihub:$WIN" 2>/dev/null; then
        # ★kill 回非零≠沒殺成:殺掉最後一窗=hub server 收攤,連線斷
        # 也回非零(the maintainer 活艙實測第一擊=tmux 錯誤覆蓋,動作其實完成)。
        # 真偽以「窗還在否」判;失敗 fail-visible 走 state 日誌。
        if env -u TMUX tmux -L "$HUB" display-message -p \
             -t "aihub:$WIN" '' >/dev/null 2>&1; then
          echo "$(date '+%F %T') idle-release kill failed: $WIN" \
            >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        fi
      fi
      # ★不變量(第三艦長二退):**按下確定後必定留一行**——殺了/拒了/
      # 取消了三種都留痕。先前只有失敗才寫,於是「使用者以為按了確定、實際
      # 選到取消、系統完全靜默」查不出來,繞了三輪才發現根本沒有任何一次
      # 確定被執行過。成功也要留。
      echo "$(date '+%F %T') release executed ($WIN)" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      # 09-14(pm 163 / the maintainer):關席要連帶收席位檔與佇列(搬到 retired/,不 rm);不收的話右欄不畫了、
      #   席位檔與佇列還在,巡查一直叫「積壓」,下一席拿到同窗號才被覆蓋。失敗只留痕不擋。
      # ★★ 20:07 事故第二半:seat_retire 預設用 env 的艙家(外層伺服器第一艙=4c24)找席位檔,
      #   於是 b556 點 [X] 關的是 b556 的窗、搬走的卻是 4c24 的 w2/w20/w21/w22(艦長/pm/root2/pm2)。
      #   艙家只准從被點 session 的 @aib_instance 推;推不到就**不搬**(留痕),寧可留檔也不搬錯艙。
      "$EZ2_PY3" "$COCKPIT/seat_retire.py" --window "$WIN" --by "mouse-ctl:release" \
        --seats "$EZ2_AIB_INSTANCE_HOME/seats" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 || true
      env -u TMUX bash "$COCKPIT/nav/captains-ctl.sh" --sync >/dev/null 2>&1 || true
      # ★★ 20:07 事故:本殼由外層 tmux 的 run-shell 起,環境是**外層伺服器第一個艙**的(4c24),
      #   不是點 [X] 的那一艙(b556);第一版先讀 EZ2_AIB_INSTANCE ⇒ 把 pm 那一艙整個收掉、六席全滅。
      #   艙 id **只准**從被點的那個 session 的 @aib_instance 選項取;取不到就不收、只留痕。
      #   ★ 暫時整條關閉(AIB_CLOSE_CABIN_ON_LAST_X=1 才開),等尺把「env 不得參與作答」釘住再開。
      _INST="$EZ2_AIB_INSTANCE"          # 已由 aib_cabin_env_from_session 從被點 session 重導
      if [ "${CLOSE_CABIN:-0}" = "1" ] && [ "${AIB_CLOSE_CABIN_ON_LAST_X:-0}" != "1" ]; then
        echo "$(date '+%F %T') close-cabin (last seat $WIN) 已停用:20:07 事故後候尺;艙 inst:${_INST:-?} 未收,請手動 aib idle close" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        CLOSE_CABIN=0
      fi
      if [ "${CLOSE_CABIN:-0}" = "1" ] && [ -n "$_INST" ]; then
        # 關的是最後一席:整艙收掉。走 idle_reaper 既有的收艙順序(內艙 kill-server → 外殼 kill-session →
        # 殘留 PID)。★ 09-15 拿掉強制旗標(v0.62.0 證詞 live49 test-perf-click 紅):按 [X] 的人一定還附著,
        #   不強制 ⇒ idle_reaper 以 human_attached 拒收、只留痕,不會無視有人附著就收艙(20:07 同族風險)。
        #   要讓這條真的收掉整艙,屬「關艙可否無視有人附著」的產品決定,重開本開關時與 the maintainer 一起定。
        echo "$(date '+%F %T') close-cabin (last seat $WIN) inst:$_INST" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        # 同步跑、不背景化(test-shell-hygiene 禁裸 & 與 nohup):收的是外層 session 與 hub 伺服器,
        #   本殼是外層伺服器 run-shell 的子行程,不在被收的 session 裡。
        env -u TMUX "$EZ2_PY3" "$COCKPIT/idle_reaper.py" close "inst:$_INST" --yes \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>&1 < /dev/null || true
      fi
    fi
    exit 0
    ;;
  open-window)
    [ -n "$WINDOW" ] || exit 0
    SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
    HUB="$EZ2_AIHUB_SOCKET"
    if [ "$DRY" != "1" ]; then
      HUB_FROM_SESSION="$(env -u TMUX tmux show-option -qv -t "$SESSION" \
        @aib_hub_socket 2>/dev/null || true)"
      [ -z "$HUB_FROM_SESSION" ] || HUB="$HUB_FROM_SESSION"
      # 08-14「視角被搶回」追查:**每一條會 select-window 的路徑都要留痕,
      # 只照亮一半的房間等於沒照**(第三艦長:黑暗那半正好符合症狀)。
      # 格式與 focus-hub 那條一致,便於用同一個 grep 對時間軸。
      echo "$(date '+%F %T') open-window -> $WINDOW (context menu)" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      env -u TMUX tmux -L "$HUB" select-window -t "aihub:$WINDOW" \
        >/dev/null 2>&1 || exit 0
      env -u TMUX bash "$COCKPIT/nav/region-nav-ctl.sh" --focus hub "$SESSION" \
        >/dev/null 2>&1 || true
      pending_kick_redraw
    else
      run env -u TMUX tmux -L "$HUB" select-window -t "aihub:$WINDOW"
      run env -u TMUX bash "$COCKPIT/nav/region-nav-ctl.sh" --focus hub "$SESSION"
      pending_kick_redraw
    fi
    ;;
  context)
    # Context UI is optional. Malformed/stale state, a vanished pane or tmux
    # display failure is a silent no-op, never terminal noise in the AI pane.
    exec 2>/dev/null
    case "$SURFACE" in pane|border|status) ;; *) exit 0 ;; esac
    SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
    # 審查 #287 r3 M1:run-shell 的環境是伺服器第一個艙的;右鍵選單讀的 rowmap 要是**被點的那一艙**
    #   (否則第二艙以後「詳情 / 開啟」對到別艙同一行、「關閉這一席」帶錯窗 id)。取不到艙身份 ⇒ 不給關閉項。
    CTX_CABIN_OK=0
    aib_cabin_env_from_session "$SESSION" && CTX_CABIN_OK=1
    [ -n "$PANE" ] || PANE="="
    LABEL_BLOCK="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" \
      "aib.context.surface.${SURFACE}" \
      aib.context.menu.details \
      aib.context.menu.open_conversation 2>/dev/null || true)"
    SURFACE_LABEL="$(printf '%s\n' "$LABEL_BLOCK" | sed -n '1p')"
    DETAILS_LABEL="$(printf '%s\n' "$LABEL_BLOCK" | sed -n '2p')"
    OPEN_LABEL="$(printf '%s\n' "$LABEL_BLOCK" | sed -n '3p')"
    [ -n "$SURFACE_LABEL" ] && [ -n "$DETAILS_LABEL" ] && \
      [ -n "$OPEN_LABEL" ] || exit 0
    TITLE="AIB ${SURFACE_LABEL}"
    DETAIL_PAYLOAD=""
    WINDOW=""

    if [ "$SURFACE" = "pane" ] && [ "$ROLE" = "ai-list" ]; then
      ACTION="$("$EZ2_PY3" "$HERE/hit_test.py" --role "$ROLE" \
        --x "${CX:-0}" --y "${CY:-0}" --intent context \
        --state-dir "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null || true)"
      META="$("$EZ2_PY3" "$HERE/detail-card.py" --action "$ACTION" \
        2>/dev/null || true)"
      if [ -n "$META" ]; then
        TITLE="$("$EZ2_PY3" -c \
          'import json,sys; print(json.loads(sys.argv[1]).get("title", ""))' \
          "$META" 2>/dev/null || true)"
        DETAIL_PAYLOAD="$("$EZ2_PY3" -c \
          'import json,sys; print(json.loads(sys.argv[1]).get("payload", ""))' \
          "$META" 2>/dev/null || true)"
        WINDOW="$("$EZ2_PY3" -c \
          'import json,sys; print(json.loads(sys.argv[1]).get("window", ""))' \
          "$META" 2>/dev/null || true)"
      fi
    fi

    # 10-24 @abb89:右鍵「關閉這一席」—— 只有那一列本來就有 [X]/(x) 才出現;點了回呼 --close-row,
    #   彈的是跟點 [X] 同一個確認選單(取消排第一、真關只在 --release-confirmed、軟關帶 --soft-since)。
    CLOSE_ACTION=""
    CLOSE_TITLE=""
    _CX_OK="${CX:-0}"; _CY_OK="${CY:-0}"
    case "$_CX_OK" in *[!0-9]*) _CX_OK=0 ;; esac          # 座標只收數字才嵌進 run-shell 字串
    case "$_CY_OK" in *[!0-9]*) _CY_OK=0 ;; esac
    if [ "$CTX_CABIN_OK" = "1" ] && [ -n "$WINDOW" ] && [ "$_CY_OK" -ge 2 ]; then
      _CLOSE_HIT="$("$EZ2_PY3" "$HERE/hit_test.py" --role "$ROLE" \
        --x "${CX:-0}" --y "${CY:-0}" --intent close \
        --state-dir "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null || true)"
      case "$_CLOSE_HIT" in
        *'"kill-idle"'*)
          CLOSE_TITLE="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" \
            aib.context.menu.close_seat 2>/dev/null | sed -n '1p' || true)"
          if [ -n "$CLOSE_TITLE" ]; then
            printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
            printf -v SESSION_ARG_Q '%q' "$SESSION"
            printf -v WIN_ARG_Q '%q' "$WINDOW"
            CLOSE_ACTION="run-shell 'bash $SCRIPT_Q --role right --close-row ai-list $_CX_OK $_CY_OK $SESSION_ARG_Q --expect-window $WIN_ARG_Q$RUNSHELL_REDIR'"
          fi
          ;;
      esac
    fi

    printf -v PANE_ARG_Q '%q' "$PANE"
    # the maintainer 09-11:右鍵不要「聚焦窗格」「刪除聚焦窗格」。另開 AI / 主題底色留下。
    # ---- 「另開 AI」項(08-14 the maintainer 直令)-----------------------------------
    # ★三層不用巢狀字串,改**每層 run-shell 回呼自己**(--new-ai-menu):
    #   把 `display-menu …` 當字串塞進上層選單項時,shell 的 %q 會把它跳脫成
    #   **一個含空格的 token**,tmux 於是把整串當成單一命令名去找 → 找不到 →
    #   **靜默什麼都不發生**(pty 沙箱實測:第二層根本沒開)。回呼式每層都是
    #   單層 display-menu,零巢狀引號,而且每層都能重用 set_menu_pos 與
    #   -O -M,座標一路傳下去所以三層開在同一點。
    NEW_AI_ACTION=""
    NEW_AI_TITLE="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" \
      aib.context.menu.new_ai 2>/dev/null | sed -n '1p' || true)"
    if [ -n "$NEW_AI_TITLE" ] && [ -n "$(new_ai_providers)" ]; then
      printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
      printf -v PANE_ARG_Q '%q' "$PANE"
      NEW_AI_ACTION="run-shell 'bash $SCRIPT_Q --new-ai-menu \"\" $PANE_ARG_Q ${CX:-0} ${CY:-0}$RUNSHELL_REDIR'"
    fi
    # 主題底色項(設計席第一刀的 UI 掛接;同樣 run-shell 回呼不用巢狀字串)
    printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
    THEME_ACTION=""
    THEME_TITLE="$("$EZ2_PY3" "$COCKPIT/i18n_lib.py" \
      aib.context.menu.theme_color 2>/dev/null | sed -n '1p' || true)"
    if [ -n "$THEME_TITLE" ]; then
      printf -v SESSION_ARG_Q '%q' "$SESSION"
      THEME_ACTION="run-shell 'bash $SCRIPT_Q --theme-menu $PANE_ARG_Q ${CX:-0} ${CY:-0} $SESSION_ARG_Q$RUNSHELL_REDIR'"
    fi
    if [ -n "$DETAIL_PAYLOAD" ] && [ -n "$WINDOW" ]; then
      printf -v PY_Q '%q' "$EZ2_PY3"
      printf -v CARD_Q '%q' "$HERE/detail-card.py"
      printf -v PAYLOAD_Q '%q' "$DETAIL_PAYLOAD"
      POPUP_COMMAND="$PY_Q $CARD_Q --payload $PAYLOAD_Q"
      printf -v POPUP_COMMAND_Q '%q' "$POPUP_COMMAND"
      DETAIL_ACTION="display-popup -E -w 48 -h 9 $POPUP_COMMAND_Q"

      printf -v SCRIPT_Q '%q' "$HERE/mouse-ctl.sh"
      printf -v WINDOW_Q '%q' "$WINDOW"
      printf -v SESSION_Q '%q' "$SESSION"
      OPEN_ACTION="run-shell 'bash $SCRIPT_Q --open-window $WINDOW_Q $SESSION_Q$RUNSHELL_REDIR'"
      set_menu_pos "$PANE"
      if [ "$DRY" = "1" ]; then
        run env -u TMUX tmux display-menu -O -M -T "$TITLE" -t "$PANE" ${MENU_POS[@]+"${MENU_POS[@]}"} \
          "$DETAILS_LABEL" d "$DETAIL_ACTION" \
          "$OPEN_LABEL" o "$OPEN_ACTION" \
          ${NEW_AI_ACTION:+"$NEW_AI_TITLE" n "$NEW_AI_ACTION"} \
          ${THEME_ACTION:+"$THEME_TITLE" t "$THEME_ACTION"} \
          ${CLOSE_ACTION:+"$CLOSE_TITLE" x "$CLOSE_ACTION"}
      else
        # 「關閉這一席」排最後(離游標最遠);點了還有一道確認選單,不是直關
        env -u TMUX tmux display-menu -O -M -T "$TITLE" -t "$PANE" ${MENU_POS[@]+"${MENU_POS[@]}"} \
          "$DETAILS_LABEL" d "$DETAIL_ACTION" \
          "$OPEN_LABEL" o "$OPEN_ACTION" \
          ${NEW_AI_ACTION:+"$NEW_AI_TITLE" n "$NEW_AI_ACTION"} \
          ${THEME_ACTION:+"$THEME_TITLE" t "$THEME_ACTION"} \
          ${CLOSE_ACTION:+"$CLOSE_TITLE" x "$CLOSE_ACTION"} \
          >/dev/null 2>&1 || true
      fi
    elif [ "$DRY" = "1" ]; then
      set_menu_pos "$PANE"
      run env -u TMUX tmux display-menu -O -M -T "$TITLE" -t "$PANE" ${MENU_POS[@]+"${MENU_POS[@]}"} \
        ${NEW_AI_ACTION:+"$NEW_AI_TITLE" n "$NEW_AI_ACTION"} \
          ${THEME_ACTION:+"$THEME_TITLE" t "$THEME_ACTION"}
    else
      set_menu_pos "$PANE"
      env -u TMUX tmux display-menu -O -M -T "$TITLE" -t "$PANE" ${MENU_POS[@]+"${MENU_POS[@]}"} \
        ${NEW_AI_ACTION:+"$NEW_AI_TITLE" n "$NEW_AI_ACTION"} \
          ${THEME_ACTION:+"$THEME_TITLE" t "$THEME_ACTION"} \
        >/dev/null 2>&1 || true
    fi
    ;;
  kill-focused-seat)
    SESSION="${SESSION:-$EZ2_TMUX_SESSION}"
    HUB="$EZ2_AIHUB_SOCKET"
    if [ "$DRY" != "1" ]; then
      HUB_FROM_SESSION="$(env -u TMUX tmux show-option -qv -t "$SESSION" @aib_hub_socket 2>/dev/null || true)"
      [ -z "$HUB_FROM_SESSION" ] || HUB="$HUB_FROM_SESSION"
    fi
    WIN=""
    if [ "$DRY" = "1" ]; then
      WIN="@dry"
    else
      WIN="$(env -u TMUX tmux -L "$HUB" list-windows -t aihub \
        -F '#{window_id} #{window_active} #{@aib_provider}' 2>/dev/null \
        | awk '$2==1 && $3!="" {print $1; exit}')"
    fi
    [ -n "$WIN" ] || exit 0
    if [ "$DRY" = "1" ]; then
      run env -u TMUX tmux -L "$HUB" kill-window -t "aihub:$WIN"
    else
      env -u TMUX tmux -L "$HUB" kill-window -t "aihub:$WIN" >/dev/null 2>&1 || true
      env -u TMUX bash "$COCKPIT/nav/captains-ctl.sh" --sync >/dev/null 2>&1 || true
      echo "$(date '+%F %T') kill-focused-seat ($WIN)" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
    fi
    exit 0
    ;;
  copy-cabin)
    CABIN="${EZ2_AIB_INSTANCE:-}"
    [ -n "$CABIN" ] || CABIN="${EZ2_AIHUB_SOCKET:-}"
    [ -n "$CABIN" ] || exit 0
    if [ "$DRY" = "1" ]; then
      printf '+ pbcopy %s\n' "$CABIN"
    else
      printf '%s' "$CABIN" | pbcopy 2>/dev/null || printf '%s' "$CABIN" | xclip -selection clipboard 2>/dev/null || true
      tmux display-message "aib: 已複製艙號 $CABIN" >/dev/null 2>&1 || true
    fi
    exit 0
    ;;
  dblclick)
    # 雙擊待審列 = 點選那一則(對目前這一頁)+ 開編輯框。其餘位置不做事。任何參數形狀恆 exit 0。
    [ "${PANE_ROLE:-}" = "review" ] && ROLE=pending-review
    [ "${ROLE:-}" = "pending-review" ] || exit 0
    case "${CX:-}" in ''|*[!0-9]*) exit 0 ;; esac
    case "${CY:-}" in ''|*[!0-9]*) exit 0 ;; esac
    ACTION="$("$EZ2_PY3" "$HERE/hit_test.py" --role "$ROLE" --x "$CX" --y "$CY" \
      --cabin "${EZ2_AIB_INSTANCE:-}" --state-dir "$EZ2_COCKPIT_STATE_DIR" 2>/dev/null)" || exit 0
    KIND="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("action") or "")' "$ACTION" 2>/dev/null)" || exit 0
    [ "$KIND" = "pending-focus" ] || exit 0
    QSEAT="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("seat") or "")' "$ACTION" 2>/dev/null)" || exit 0
    QITEM="$("$EZ2_PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("item") or "")' "$ACTION" 2>/dev/null)" || exit 0
    case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
    QTABKEY="$(pending_inbox_tab_key)"
    if [ "$DRY" = "1" ]; then
      run "$EZ2_PY3" "$COCKPIT/dm_queue.py" view --seat "$QSEAT" --focus-set "$QITEM" --tab "$QTABKEY"
    else
      "$EZ2_PY3" "$COCKPIT/dm_queue.py" view --seat "$QSEAT" --focus-set "$QITEM" --tab "$QTABKEY" >/dev/null 2>&1 || true
    fi
    pending_open_editor || true
    exit 0
    ;;
  pending-edit)
    case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
    pending_open_editor
    exit 0
    ;;
  dm-queue-move)
    case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
    case "$QDIR" in up|down) ;; *) exit 0 ;; esac
    pending_queue_move "$QSEAT" "$QITEM" "$QDIR"
    exit 0
    ;;
  dm-queue-priority|dm-queue-body)
    case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    if [ "$MODE" = "dm-queue-priority" ]; then
      case "$QPRI" in urgent|important|normal|reminder) ;; *) exit 0 ;; esac
      EDIT_ARGS=(--priority "$QPRI")
    else
      EDIT_ARGS=(--msg "$QBODY")
    fi
    REV_ARGS=(--if-rev "$QREV")
    if [ -z "${QREV:-}" ]; then
      if [ "$NO_REV_CHECK" = "1" ]; then
        dm_queue_bypass_note "$MODE"
        REV_ARGS=(--no-rev-check)
      else
        dm_queue_refuse_no_rev "$MODE"
        exit 0
      fi
    fi
    if [ "$DRY" = "1" ]; then
      run "$EZ2_PY3" "$COCKPIT/dm_queue.py" edit --seat "$QSEAT" --id "$QITEM" "${EDIT_ARGS[@]}" ${REV_ARGS[@]+"${REV_ARGS[@]}"}
      exit 0
    fi
    _erc=0
    "$EZ2_PY3" "$COCKPIT/dm_queue.py" edit --seat "$QSEAT" --id "$QITEM" "${EDIT_ARGS[@]}" ${REV_ARGS[@]+"${REV_ARGS[@]}"} >/dev/null 2>&1 || _erc=$?
    if [ "$_erc" = "5" ] || [ "$_erc" = "4" ]; then
      echo "$(date '+%F %T') dm-queue $MODE refused rc=$_erc (rev $QREV) $QSEAT $QITEM" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      env -u TMUX tmux display-message "這封信在你確認前被改過(或已被處理),沒有改" >/dev/null 2>&1 || true
    fi
    bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
    exit 0
    ;;
  dm-queue-delete-confirmed)
    case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
    # 36-1(艦長裁):這一幀沒帶修訂號 ⇒ 一律不執行(fail-closed),不走舊路——升級那一幀正是它要擋的情境
    REV_ARGS=(--if-rev "$QREV")
    if [ -z "${QREV:-}" ]; then
      if [ "$NO_REV_CHECK" = "1" ]; then
        dm_queue_bypass_note delete
        REV_ARGS=(--no-rev-check)
      else
        dm_queue_refuse_no_rev delete
        exit 0
      fi
    fi
    # 第 70 項 P2 起 CLI delete 沒帶 --by 一律拒(actor_missing rc 7)。這一條是人在座艙點[刪除]再按確認才到得了,
    # 署名照 human_attest 的 human_id 定義(真實 uid 的登入名,不是自報的 $USER)。
    # ★ 09-14 實測:原本沒帶 --by ⇒ rc 7 而下面只認 4/5 ⇒ 落到 else 記成「delete confirmed」,信其實沒刪。
    DEL_BY="human:$("$EZ2_PY3" -c 'import os,pwd; print(pwd.getpwuid(os.getuid()).pw_name)' 2>/dev/null || true)"
    if [ "$DRY" = "1" ]; then
      run "$EZ2_PY3" "$COCKPIT/dm_queue.py" delete --seat "$QSEAT" --id "$QITEM" ${REV_ARGS[@]+"${REV_ARGS[@]}"} --by "$DEL_BY"
    else
      _drc=0
      "$EZ2_PY3" "$COCKPIT/dm_queue.py" delete --seat "$QSEAT" --id "$QITEM" ${REV_ARGS[@]+"${REV_ARGS[@]}"} --by "$DEL_BY" >/dev/null 2>&1 || _drc=$?
      if [ "$_drc" = "5" ] || [ "$_drc" = "4" ]; then
        # 第 36 項:確認前被改過/已不在 ⇒ 不刪,具名說出來(不是靜默)
        echo "$(date '+%F %T') dm-queue delete refused rc=$_drc (rev $QREV) $QSEAT $QITEM" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        env -u TMUX tmux display-message "這封信在你確認前被改過(或已被處理),沒有刪" >/dev/null 2>&1 || true
      elif [ "$_drc" != "0" ]; then
        # 其他非零(actor/狀態不允許等)也是沒刪:不得記成 confirmed
        echo "$(date '+%F %T') dm-queue delete refused rc=$_drc by=$DEL_BY $QSEAT $QITEM" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        env -u TMUX tmux display-message "沒有刪(rc=$_drc,原因在 mouse-errors.log)" >/dev/null 2>&1 || true
      else
        echo "$(date '+%F %T') dm-queue delete confirmed rev_bypass=$([ "${REV_ARGS[0]:-}" = "--no-rev-check" ] && echo explicit-no-rev-check || echo none) rev=${QREV:-none} epoch=${QEPOCH:-none} $QSEAT $QITEM" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      fi
      bash "$COCKPIT/pane-pending-review.sh" --kick >/dev/null 2>&1 || true
    fi
    exit 0
    ;;
  dm-queue-send-confirmed)
    case "$QSEAT" in ''|*[!0-9a-z]*) exit 0 ;; esac
    case "$QITEM" in q_[0-9a-f][0-9a-f]*) ;; *) exit 0 ;; esac
    DQ="$COCKPIT/dm_queue.py"
    REV_ARGS=(--if-rev "$QREV")
    if [ -z "${QREV:-}" ]; then
      if [ "$NO_REV_CHECK" = "1" ]; then
        dm_queue_bypass_note send
        REV_ARGS=(--no-rev-check)
      else
        dm_queue_refuse_no_rev send
        exit 0
      fi
    fi
    # 待人處理的信:立刻送出 = 真人先放回佇列再照原路送(09-15 the maintainer 選 2)。
    # hold_human 仍在 order.json(ACTIVE_STATUSES),狀態轉換不動 rev ⇒ 放回後照這一幀的 rev 領得到。
    # 署名照 human_attest 的 human_id 定義(真實 uid 的登入名);requeue 沒帶 human 一律 rc 7。
    _qstatus="$("$EZ2_PY3" -c 'import sys; sys.path.insert(0, sys.argv[1]); import dm_queue as Q
it = Q._load_item(sys.argv[2], sys.argv[3]) or {}
print(it.get("status") or "")' "$COCKPIT" "$QSEAT" "$QITEM" 2>/dev/null || true)"
    SEND_BY="human:$("$EZ2_PY3" -c 'import os,pwd; print(pwd.getpwuid(os.getuid()).pw_name)' 2>/dev/null || true)"
    if [ "$DRY" = "1" ]; then
      [ "$_qstatus" = "hold_human" ] && run "$EZ2_PY3" "$DQ" requeue --seat "$QSEAT" --id "$QITEM" --by "$SEND_BY"
      run "$EZ2_PY3" "$DQ" claim-id --seat "$QSEAT" --id "$QITEM" ${REV_ARGS[@]+"${REV_ARGS[@]}"}
      run env AIB_DM_SORT_DELIVER=1 AIB_DM_QUEUED_ITEM_ID="$QITEM" AIB_DM_QUEUED_SEAT="$QSEAT" bash "$COCKPIT/dm-ctl.sh" "$QSEAT" --file TMP --verify
      run "$EZ2_PY3" "$DQ" settle --seat "$QSEAT" --id "$QITEM" --rc RC
      exit 0
    fi
    if [ "$_qstatus" = "hold_human" ]; then
      _rqrc=0
      "$EZ2_PY3" "$DQ" requeue --seat "$QSEAT" --id "$QITEM" --by "$SEND_BY" >/dev/null 2>&1 || _rqrc=$?
      if [ "$_rqrc" != "0" ]; then
        echo "$(date '+%F %T') dm-queue send refused requeue rc=$_rqrc by=$SEND_BY $QSEAT $QITEM" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
        env -u TMUX tmux display-message "人審的信放不回佇列(rc=$_rqrc),沒有送" >/dev/null 2>&1 || true
        exit 0
      fi
    fi
    # the maintainer 09-30 15:3x(收件匣 A 項 2):貼時效行(本則發出於…已過 N 分)再接原文,與排空器同一支 delivery_text;
    #   收件席才知道這則是排了多久才送到的,不會當成剛寫的。
    _qmsg="$("$EZ2_PY3" "$DQ" show --seat "$QSEAT" --id "$QITEM" --with-age 2>/dev/null || true)"
    # ★ review r14 M1:與排空器同一套投遞環境(dm_queue._dmctl_env)—— 信上記著 no_relay(給讀不到落檔的家的
    #   人格包/交接/開席單)⇒ AIB_DM_RELAY_BYTES=0 整行貼;不然 dm-ctl 用預設 600 換成「去讀某檔」的指標。
    #   也照 DELIVER_IDENTITY_DROP 洗掉按鈕所在行程的 CODEX_THREAD_ID / DM_CODEX_SESSION_UUID(review r1 m5 同一件)。
    _qrelay="$("$EZ2_PY3" -c 'import sys; sys.path.insert(0, sys.argv[1]); import dm_queue as Q
it = Q._load_item(sys.argv[2], sys.argv[3]) or {}
print("0" if it.get("no_relay") else "")' "$(dirname "$DQ")" "$QSEAT" "$QITEM" 2>/dev/null || true)"
    _crc=0
    "$EZ2_PY3" "$DQ" claim-id --seat "$QSEAT" --id "$QITEM" ${REV_ARGS[@]+"${REV_ARGS[@]}"} >/dev/null 2>&1 || _crc=$?
    if [ "$_crc" = "5" ] || [ "$_crc" = "4" ]; then
      # 第 36 項:確認前被改過/已不在 ⇒ 不送,具名說出來
      echo "$(date '+%F %T') dm-queue send refused rc=$_crc (rev $QREV) $QSEAT $QITEM" \
        >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      env -u TMUX tmux display-message "這封信在你確認前被改過(或已被處理),沒有送" >/dev/null 2>&1 || true
      exit 0
    fi
    [ "$_crc" = "0" ] || exit 0
    _qfile="$(mktemp "${TMPDIR:-/tmp}/aib-dmq.XXXXXX" 2>/dev/null || true)"
    if [ -n "$_qfile" ] && [ -n "${_qmsg}" ]; then
      printf '%s' "$_qmsg" > "$_qfile"
      _qrc=0
      # AIB_DM_QUEUED_ITEM_ID(the maintainer 09-30 15:3x,收件匣 A 項 2):抬頭寫這一則的**原寄件人**。沒帶它,dm-ctl 從
      #   本行程鏈推寄件人 ⇒ 來源是 mouse_ctl ⇒ 抬頭變成「the maintainer」,收件席以為是真人寫的。dm-ctl 自己回佇列讀那一則驗過才用。
      #   AIB_DM_QUEUED_SEAT(review r2 m6):那一則住在哪一席的佇列 —— dm-ctl 轉給後繼席後收件席碼就不是它了
      env -u CODEX_THREAD_ID -u DM_CODEX_SESSION_UUID ${_qrelay:+AIB_DM_RELAY_BYTES=0} \
        AIB_DM_QUEUED_ITEM_ID="$QITEM" AIB_DM_QUEUED_SEAT="$QSEAT" AIB_DM_SORT_DELIVER=1 \
        bash "$COCKPIT/dm-ctl.sh" "$QSEAT" --file "$_qfile" --verify >/dev/null 2>&1 || _qrc=$?
      rm -f "$_qfile"
      # 送完照投遞 rc 結清(0→已送達留歷史;2→退回/滿次轉待人處理;其他→待人處理),與排空器同一支 _settle_delivery。
      # ★ 09-15:原本 rc0 走 delete 且沒帶 --by(刪除只准真人)⇒ 被拒 ⇒ 信永遠卡 claimed。
      _src=0
      "$EZ2_PY3" "$DQ" settle --seat "$QSEAT" --id "$QITEM" --rc "$_qrc" >/dev/null 2>&1 || _src=$?
      if [ "$_src" != "0" ]; then
        echo "$(date '+%F %T') dm-queue send settle failed rc=$_src deliver_rc=$_qrc $QSEAT $QITEM" \
          >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
      fi
    else
      "$EZ2_PY3" "$DQ" unclaim --seat "$QSEAT" --id "$QITEM" >/dev/null 2>&1 || true
    fi
    echo "$(date '+%F %T') dm-queue send confirmed $QSEAT $QITEM" \
      >> "$EZ2_COCKPIT_STATE_DIR/mouse-errors.log" 2>/dev/null || true
    exit 0
    ;;
  *)
    echo "usage: mouse-ctl.sh --install | --click <role> <x> <y> [session] | --context <surface> <role> <x> <y> <pane> <session> | --release-confirmed <window> [session] | --kill-focused-seat [session] | --copy-cabin" >&2
    exit 2
    ;;
esac
