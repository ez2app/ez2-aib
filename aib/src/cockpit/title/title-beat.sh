#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E2.2 標題接軌:把 title-beat 與信任紅燈掛上 tmux 標題。
#
#   title-beat.sh --once [--print]     取樣一次(--print 只印,不碰 tmux)
#   title-beat.sh --loop               常駐取樣(預設 5 秒一拍)
#   title-beat.sh --install [--dry-run] 設 tmux set-titles 讓標題吃我們的字串
#
# 為什麼要有節拍:靜止的標題和當掉的更新器長得一模一樣。節拍每拍前進一格,
# 標題凍住就是更新器死了 —— 讓「還活著」變成看得見的事實,而不是假設。
#
# 為什麼判定不在這支腳本裡:紅黃綠的規則是純函數(title_beat.py),
# shell 只負責取樣與貼字串。規則有測試,取樣沒有,兩者分開才驗得動。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE="once"
PRINT_ONLY=0
INTERVAL="${EZ2_TITLE_INTERVAL:-5}"
BEAT_FILE="$EZ2_COCKPIT_STATE_DIR/title-beat-seq"

for a in "$@"; do
  case "$a" in
    --once) MODE="once" ;;
    --loop) MODE="loop" ;;
    --install) MODE="install" ;;
    --print) PRINT_ONLY=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install title-beat.sh "$HERE" || exit $?
  else
    # 報錯走 aib/i18n 四語鍵;字典或 python 出事就印鍵名,報錯不能因翻譯失敗而消失。
    { python3 "$COCKPIT/i18n_lib.py" aib.install_guard.missing --param script=title-beat.sh \
        --param "path=$COCKPIT/install-guard.sh" 2>/dev/null || printf '%s\n' aib.install_guard.missing; } >&2
    exit 6
  fi
fi

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

# 08-14:原 read-modify-write 無鎖+寫失敗 `|| true` 吞掉,改走 aib_state_transition
# (flock+temp+rename,與 depth/tab 類寫者同一套)。寫不進=stderr 留痕+節拍凍在
# 檔值——凍住是看得見的事實(見檔頭),但標題本體與信任燈不陪葬,rc 仍 0。
. "$COCKPIT/state-transition.sh"

_advance_beat() {
  local n="$1"
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  printf '%s\n' $(( (n + 1) % 4 ))
}

next_beat() {
  local n
  if ! aib_state_transition "$BEAT_FILE" _advance_beat; then
    echo "title-beat: beat 狀態寫入失敗($BEAT_FILE),節拍凍結" >&2
  fi
  n=$(cat "$BEAT_FILE" 2>/dev/null || echo 0)
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  printf '%s' "$n"
}

# ★★ 2026-08-17:定址修正。這支腳本原本的兩半**打到不同的 tmux server**:
#   寫入用裸 `tmux`(吃 $TMUX,從席位跑就是**內層 hub**),
#   窗號探針用 `env -u TMUX tmux`(→ **外層** default server)。
#   後果實測(instance 8):
#     窗號探針回 @0 —— 那是外層某個 session 的窗,而席位檔的鍵是**hub 的窗號**
#       (@2/@3/@6/@7 → w2/w3/w6/w7.json),於是永遠查不到席位,印 `unbound`
#     寫入落在 aihub-8,而那台 `set-titles=off`、`status-right` 還是原廠值
#       —— **那一格連讀者都沒有**
#   ★ `title_beat.py` 的判定邏輯**沒有壞**:餵對窗號就對
#       EZ2_TB_WINDOW=@3 → `◐ 🎓 a seat/Claude … | 1d5h`(advisor 圖示與 uptime 都正確)
#     所以這一刀只改**去處**,不動那個純函數。看到 `unbound` 不要去改 title_beat.py。
#
# 顯示哪一席:the maintainer 2026-08-17 裁「顯示你當下正在看的那一席」——
#   中欄切到哪一席標題就跟到哪一席(直令原文「方便所有 AI 彼此定位」=
#   標題要隨時回答「我現在在跟誰說話」)。所以來源是 **hub 的 active window**,
#   不是艦長席。★ 順帶好處:它不依賴「誰是艦長」那個會飄的來源。
hub_active_window() {
  # tmux 的 format 讀不到別台 server,shell 可以 —— 這是這一格必須是行程而不是
  # format 的唯一理由。取不到就回空:席位那條路自然走不通而退回原本的燈,
  # **不編一個窗號出來**(同下面 EZ2_TB_WINDOW 的既有紀律)。
  local sock="${EZ2_AIHUB_SOCKET:-}"
  [ -n "$sock" ] || return 0
  env -u TMUX tmux -L "$sock" display-message -p '#{window_id}' 2>/dev/null || true
}

# 標題寫在哪:**外層 session 層**,不是 `-g`。
#   tmux 的 session 層設定蓋過全域,而 cockpit-full.sh:417 寫的正是 session 層。
#   所以原本的 `set -g @aib_title` 就算被正確呼叫、算得完全正確,也**永遠不會
#   上畫面** —— 而任何以「@aib_title 有沒有被寫」為判準的測試都會通過,
#   因為那個值真的被寫進去了,只是寫在沒有讀者的那一層。
#   ★ 判準只能是「讀的那一端拿到了什麼」:`display-message -p '#{E:@aib_title}'`。
write_title() {
  local title="$1" s="${EZ2_TMUX_SESSION:-}"
  if [ -z "$s" ]; then
    echo "title-beat: EZ2_TMUX_SESSION 未設,不知道要寫到哪個 session" >&2
    return 0     # rc0 紅線:標題寫不進去不得擋住呼叫者
  fi
  run env -u TMUX tmux set-option -t "$s" @aib_title "$title" 2>/dev/null || true
  # 版號與更新狀態餵給狀態列(顏色只在這一層可能——標題那層只吃純文字)。
  # 兩個都是**純值**,樣式由 status-left 的格式字串決定;把樣式寫進值裡會
  # 讓「值」與「怎麼畫」混在一起,而下一個人改配色時得去改產生器。
  local vf="$EZ2_COCKPIT_STATE_DIR/title-version.json"
  if [ -f "$vf" ]; then
    local ver state
    ver="$("$EZ2_PY3" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("ver") or "")' "$vf" 2>/dev/null || true)"
    state="$("$EZ2_PY3" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state") or "unknown")' "$vf" 2>/dev/null || true)"
    [ -n "$ver" ] && run env -u TMUX tmux set-option -t "$s" @aib_ver "$ver" 2>/dev/null || true
    [ -n "$state" ] && run env -u TMUX tmux set-option -t "$s" @aib_ver_state "$state" 2>/dev/null || true
  fi
}

sample() {  # 取樣一次 -> 印出標題字串
  local uuid="${EZ2_SESSION_UUID:-${CLAUDE_CODE_SESSION_ID:-}}"
  local beat; beat="$(next_beat)"
  # EZ2_TB_WINDOW:席位檔的檔名綁窗號(w<N>.json)。取不到就留空,
  # 席位檔那條路自然走不通而退回原本的紅燈——**不編一個窗號出來**。
  # ★ `--print` 的契約是**零接觸 tmux**(test_print_mode_never_touches_tmux),
  #   所以只有在非 print 模式、且環境沒給窗號時才去問 tmux。
  #   第一版我無條件問,當場弄紅那支既有測試——**它是對的,我改壞了契約**。
  local wid="${EZ2_TB_WINDOW:-}"
  if [ -z "$wid" ] && [ "$PRINT_ONLY" != "1" ]; then
    wid="$(hub_active_window)"
  fi
  # 取樣的 python 原本是這裡的 heredoc;2026-11-12 搬成 title_sample.py,Rust 版 aib-title 跑同一份。
  EZ2_TB_UUID="$uuid" EZ2_TB_BEAT="$beat" EZ2_TB_COLOR="${1:-0}" EZ2_TB_PRINT_ONLY="$PRINT_ONLY" \
  EZ2_TB_WINDOW="$wid" "$EZ2_PY3" "$HERE/title_sample.py"
}

case "$MODE" in
  install)
    # 讓 tmux 的視窗標題吃我們的字串(#{@aib_title} 由 --once 寫入)。
    # ★ 與 @aib_title 同層:寫 session 層,否則 cockpit-full.sh 的 session 層
    #   會蓋掉這裡設的全域值(見 write_title 的註解)。
    if [ -n "${EZ2_TMUX_SESSION:-}" ]; then
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" set-titles on
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" set-titles-string '#{@aib_title}'
      # ---- 狀態列:版號 + **有顏色的**更新狀態 -----------------------------
      # ★ 這是顏色唯一能成立的地方。視窗標題走的是終端機的 OSC 標題通道,
      #   那個通道只送一串純文字——實測 macOS Terminal 讀回來就是字串,
      #   沒有樣式可言(title_beat.py:49 早就記著這件事,當初改用形狀
      #   ○◐● 而不是顏色就是為了它)。
      # ★ 樣式寫在**格式字串裡**(字面),不是從變數展開進來:後者要靠
      #   tmux 對展開值再處理一次樣式,那條路我沒有辦法在這裡實測(顏色
      #   只有人眼看得到),所以選確定會成立的寫法。
      # ★ status-left-length 一起設:全域預設是 **10**,而這串遠超過——
      #   在此之前 `[Ez2 AIB v0.42.1-alpha.1]` 實際只顯示到 `[Ez2 AIB v`,
      #   版號一直是被截掉的。**那是既有缺陷,不是本次改動造成的。**
      # 10-10(the maintainer 眼驗令):這一行開頭先放給使用者的提示(四語字典 aib.cockpit.footer_hint,與 cockpit-full.sh 同一句,
      #   前補 2 格與 Claude 輸入框下方那三行對齊),帶顏色的版號更新狀態接在後面——那是更新提示(↑ ⟳ ?)的唯一彩色出口,不拿掉。
      #   長度 40 → 80 裝得下兩段;字典讀不到(!!key!! 或空)退回中文字面。
      #   10-14 語系盤查第 23 條:改成 tmux #(…) 每次重畫狀態列重算(切語系不用重開艙);寫法限制同 cockpit-full.sh(不能有 ")" 與 "%")。
      #   tmux #(…) 只給 server 全域環境 ⇒ 艙檔 / 狀態根寫死進指令(同 cockpit-full.sh)。
      _tb_env=""
      for _v in EZ2_AIB_INSTANCE_HOME EZ2_AIB_STATE_ROOT EZ2_AIB_HOME; do
        [ -n "${!_v:-}" ] && _tb_env="$_tb_env$(printf '%s=%q ' "$_v" "${!_v}")"
      done
      _tb_hint="#(v=\`$_tb_env$(printf '%q -I %q' "$EZ2_PY3" "$HERE/../i18n_lib.py") aib.cockpit.footer_hint 2>/dev/null\`; echo \"\$v\" | grep -q '^!!' && v=; [ -n \"\$v\" ] || v='若有任何想法,請訪問:aib.ez2.app'; echo \"\$v\")"
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-left-length 80
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-left \
        "  $_tb_hint  "'#{?#{==:#{@aib_ver_state},ok},#[fg=colour71][AIB v#{@aib_ver}]#[default] ,#{?#{==:#{@aib_ver_state},behind},#[fg=colour178][AIB v#{@aib_ver} ↑]#[default] ,#{?#{==:#{@aib_ver_state},stale},#[fg=colour208][AIB v#{@aib_ver} ⟳]#[default] ,#[fg=colour167][AIB v#{@aib_ver} ?]#[default] }}}'
      # 狀態列右側也掛一份:標題在某些終端機看不到(tmux 內、或標題被 shell 蓋掉),
      # 狀態列是艙內永遠看得見的那一行。兩處同源,都讀 @aib_title。
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-right '#{@aib_title}  '
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-right-length 80
    else
      echo "title-beat: EZ2_TMUX_SESSION 未設,--install 無對象" >&2
    fi
    ;;
  once)
    TITLE="$(sample 0)"
    if [ "$PRINT_ONLY" = "1" ]; then
      printf '%s\n' "$TITLE"
    else
      write_title "$TITLE"
      printf '%s\n' "$TITLE"
    fi
    ;;
  loop)
    # ── BL(2026-09-04):常駐迴圈也要自癒 ─────────────────────────────────
    # 根因與面板三支同一個(見 pane-tick.sh 的 BF 段):bash 的 while 在啟動時就把
    # 整份解析完,檔案後來怎麼改都不影響已經在跑的那一份 —— 落地的修法永遠到不了
    # 活著的那個行程,而畫面完全正常。
    # ★ 這裡**不能**直接呼叫 pane_reexec_if_changed:刀三(k3)要砍的是本支自己的
    #   指紋比對錨;且歷史上 title-beat 借取法不借判斷(見 test-longrun-reexec-bl
    #   RestoreKnife 說明)。模式住在 argv(`--loop`),exec 必須帶 "$@"。
    # ★ 2026-09-20:指紋檢查必須在 sample/write **之前**。sample 在 B8 高壓下
    #   可能卡住數十秒;若先 sample 再檢查,測例等 30 秒會誤判「改檔不重跑」。
    # 失效側與 BF 同:指紋量不到一律當沒變(舊碼繼續跑是貴,exec 進不存在的檔是死)。
    PANE_SELF_SCRIPT="$HERE/title-beat.sh"
    . "$COCKPIT/pane-tick.sh"
    _bl_fp=""
    _bl_next=0
    while true; do
      if [ "$SECONDS" -ge "$_bl_next" ]; then
        _bl_next=$(( SECONDS + PANE_REEXEC_INTERVAL ))
        _bl_now="$(pane_script_fingerprint)"
        if [ -n "$_bl_now" ]; then
          if [ -z "$_bl_fp" ]; then
            _bl_fp="$_bl_now"
          elif [ "$_bl_now" != "$_bl_fp" ] && [ -f "$PANE_SELF_SCRIPT" ]; then
            pane_reexec_log "$PANE_SELF_SCRIPT" "$_bl_fp" "$_bl_now"
            printf 'title-beat: 腳本已更新,重跑自己(%s)\n' "$PANE_SELF_SCRIPT" >&2
            exec bash "$PANE_SELF_SCRIPT" "$@"
          fi
        fi
      fi
      pane_wait_if_composing && continue    # 終端機組字中:不改標題(改標題也是送資料給 Terminal;10-10,見 pane-tick.sh)
      TITLE="$(sample 0)"
      write_title "$TITLE"
      sleep "$INTERVAL"
    done
    ;;
esac
