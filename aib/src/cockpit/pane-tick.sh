#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# pane-tick.sh —— 面板迴圈的節拍與可見性(2026-09-02 效能線,大綱 §59 第一階段)。
#
# 用法:每支面板腳本在主迴圈前 `. "$EZ2_COCKPIT_DIR/pane-tick.sh"; pane_tick_init`。
#
# 為什麼要有這支(09-02 實測,10 核 16GB 機器 load 117):
#   四支面板每 0.1 秒 exec 一次 `sleep`,warroom 與右欄每個 tick 再 exec 一次
#   `tmux display-message` 問幾何 —— 一個 cockpit 每秒 60 次 fork/exec,閒置就吃
#   0.42 核;6 個 cockpit 裡 3 個根本沒人在看。macOS 每次 exec 都過 syspolicyd 與
#   Xprotect,系統態 CPU 因此佔了 42%。
#
# 三態(the maintainer 09-02 定向,本檔實作):
#   detached  沒人附著這個 session → **不渲染**,每 EZ2_PANE_DETACHED_TICK 秒問一次
#             有沒有人回來;維持運作,不退出。
#   attached  有人看、沒事件 → 照各面板自己的 REFRESH_TICKS 心跳渲染(第二階段再改
#             成事件驅動 + 慢心跳)。
#   event     pane 被 resize(SIGWINCH,09-02 私有 socket 實證 resize/split 都會送到
#             pane 的 bash)、分頁檔被改 → 提前中斷 tick 迴圈,立即渲染。
#             ★ 實測 `read -t` **不會**被 WINCH 打斷(trap 在等待到期後才跑,5.02s 量到),
#             所以事件延遲由 0.1s 的 tick 長度保證,不是由訊號保證;detached 的 2s 等待
#             也因此最多慢 2s 才看到 resize,那段時間沒人在看,可接受。
#
# 節拍等待用 bash 內建 `read -t`(零 fork);bash 3.2 不支援小數 timeout,探一次
# 失敗就退回 exec `sleep`,並把用的是哪一種寫在 PANE_TICK_MODE(誠實,不靜默)。
# 幾何不再每 tick 問 tmux:WINCH 到了才標髒;沒收到 WINCH 時每個渲染週期頂端仍會
# 讀一次幾何,所以最壞是慢一個週期,不是永遠錯。
#
# 失效側:pane_attached 量不到(tmux 不在、格式不認得)一律當「有人看」——那是舊行為,
# 貴但畫面對;反過來(量不到當沒人看)會讓看得見的面板凍住,那才是缺陷。

PANE_TICK_MODE=""          # builtin-read | exec-sleep
PANE_TICK_FD=200           # 固定 fd 號:`{var}<>` 自動配號是 bash 4.1 語法,/bin/bash 3.2 會整檔解析失敗
PANE_GEOM_DIRTY=0

pane_tick_init() {
  PANE_GEOM_DIRTY=0
  trap 'PANE_GEOM_DIRTY=1' WINCH
  PANE_TICK_MODE="exec-sleep"
  if [ "${EZ2_PANE_TICK_FORCE_SLEEP:-0}" != "1" ]; then
    # 開一個永遠沒資料的 fd:read -t 在它上面只會逾時(rc>128),不會讀到東西。
    # 用具名 fifo 讀寫開(不會擋),開完就 unlink,fd 留著。這是唯一一次 exec(mkfifo)。
    # ★ 不能用 `<> <(:)`:macOS 的 process substitution 是唯讀 fd,讀寫開會 Permission denied
    #   (09-02 實測,四支面板 stderr 因此非空、四格眼驗紅)。
    local _dir="${TMPDIR:-/tmp}" _fifo=""
    _fifo="$_dir/aib-pane-tick.$$.$RANDOM"
    if mkfifo -m 600 "$_fifo" 2>/dev/null && exec 200<> "$_fifo" 2>/dev/null; then
      rm -f "$_fifo"
      # ★★ 2026-09-25(a seat 全倉盤點):`read -t` **逾時就是正常情況**,它回 rc>128。
      #   呼叫者若帶 set -e(現在 title-beat.sh 就有,只是還沒用到本函式),
      #   這一行會當場砍掉整支腳本,而且是靜的。共用檔自己守住,不要依賴每個呼叫者記得加 `|| true`。
      _tick_rc=0; read -t 0.01 -u "$PANE_TICK_FD" _ 2>/dev/null || _tick_rc=$?
      if [ "$_tick_rc" -gt 128 ]; then
        PANE_TICK_MODE="builtin-read"
      else
        exec 200>&- 2>/dev/null
      fi
    else
      rm -f "$_fifo" 2>/dev/null
    fi
  fi
  export PANE_TICK_MODE
}

# pane_tick_sleep <秒>:等待滿 <秒>(WINCH 不會提早結束它,trap 在返回後才跑)。永遠 rc0。
pane_tick_sleep() {
  if [ "$PANE_TICK_MODE" = "builtin-read" ]; then
    # ★ 同上:逾時=正常,不得讓 set -e 的呼叫者死在這裡(實測 rc=142 會砍掉整支)。
    read -t "$1" -u "$PANE_TICK_FD" _ 2>/dev/null || true
  else
    sleep "$1"
  fi
  return 0
}

# pane_read_file <變數名> <檔>:讀第一行進變數(零 fork);檔不在 → 空字串。
pane_read_file() {
  local __v="$1" __f="$2" __line=""
  if [ -r "$__f" ]; then
    IFS= read -r __line < "$__f" 2>/dev/null || true
  fi
  printf -v "$__v" '%s' "$__line"
}

# pane_attached:rc0=有人看(或不在 tmux 裡 / 量不到);rc1=這個 session 沒人附著。
pane_attached() {
  [ -n "${TMUX_PANE:-}" ] || return 0
  local a
  a="$(tmux display-message -p -t "$TMUX_PANE" '#{session_attached}' 2>/dev/null)" || return 0
  case "$a" in
    0) return 1 ;;
    *) return 0 ;;
  esac
}

# pane_wait_if_detached:沒人看就睡一個 detached tick 並回 rc0(呼叫端 `&& continue`);
# 有人看回 rc1 並把退避歸零。detached 期間不渲染;回來時迴圈頂端會重讀幾何,所以不需要記住 WINCH。
# 退避:第一次沒人看等 EZ2_PANE_DETACHED_TICK(預設 2s),之後每次加倍到 EZ2_PANE_DETACHED_TICK_MAX
# (預設 10s)。理由(AT 判準 09-02 實測):每次問附著是兩支 exec(計數殼+tmux 客戶端),固定 2s 一問
# 在 60s 窗裡佔了 attached 成本的 1/5,達不到「detached ≤ attached 的 1/10」;退避後約 1/25。
# 代價:離開很久再回來,最慢 10s 才重畫——那段時間 pane 上仍是上一幀,不是空白。
# ── BF(2026-09-03):碼換了要自己重跑,否則落地的效能修法永遠到不了活艙 ──────────
# 09-03 15:3x 實測的根因:§59 第一階段(detached 不渲染)09-03 00:28 才落到磁碟,而當時
# 六個 cockpit 的 18 支面板迴圈**全部**啟動於那之前。bash 的 `while` 迴圈在啟動時就解析完,
# 檔案後來怎麼改都不影響已經在跑的那一份 ⇒ 四個沒人附著的 cockpit 照樣整幀渲染,
# 合計燒掉約 3.7 CPU 小時。重啟後同樣沒人看的迴圈 10 分鐘只用 0.07 秒 CPU(約 83 倍差)。
#
# ★ 失效形狀是**畫面完全正常**:效能修法已合入、報表寫著「detached 5%」、而活艙一格都沒吃到,
#   沒有任何一支臂會紅——因為臂跑的是磁碟上那一份。
#
# 修法:迴圈每 PANE_REEXEC_EVERY 個 tick 比一次自己這組腳本的指紋(mtime+size,零 fork);
# 變了就 `exec` 自己。重跑的是**同一條 pane 啟動指令**,所以身分、環境、pane 都不變。
# 失效側刻意選「不重跑」:量不到指紋(檔被刪、stat 失敗)一律當沒變——舊碼繼續跑是貴,
# 自己 exec 進一個不存在的檔是死。
PANE_REEXEC_FINGERPRINT=""
PANE_REEXEC_NEXT=0
# ★ 節流用**秒**不用 tick 數(BF-2,09-03 16:0x 現場實測改的):第一版寫 300 個 tick,
#   那是照 attached 的 0.1 秒節拍算的 30 秒;但 detached 那條路每圈要等 2–10 秒,
#   同樣 300 圈變成 10–50 分鐘——而**沒人看的那些 cockpit 正是最需要自己吃到新碼的**。
#   `SECONDS` 是 bash 內建計數器(零 fork),兩條路徑用同一把尺。
PANE_REEXEC_INTERVAL="${EZ2_PANE_REEXEC_INTERVAL:-30}"
case "$PANE_REEXEC_INTERVAL" in ''|*[!0-9]*) PANE_REEXEC_INTERVAL=30 ;; esac

pane_script_fingerprint() {
  # 本檔 + 呼叫端腳本的 mtime/size。用 bash 內建做不到 stat,這裡一次 exec;
  # 因為只在每 PANE_REEXEC_EVERY 個 tick 跑一次,平均成本 < 每秒 0.04 次 exec。
  # ★ 少一個檔就回空,不是「跳過它繼續算」:第一版跳過,於是腳本被刪時指紋照樣「變了」,
  #   下一步就 exec 進一個不存在的檔、pane 當場死掉(臂 d 當場抓到)。
  #   量不到就當沒變——舊碼繼續跑是貴,exec 進不存在的檔是死。
  local f one out=""
  for f in "$EZ2_COCKPIT_DIR/pane-tick.sh" "$PANE_SELF_SCRIPT"; do
    [ -n "$f" ] || continue
    [ -f "$f" ] || { printf ''; return 0; }
    # GNU 先、BSD 後(10-04 雲端修紅):GNU 的 `stat -f` 是「檔案系統資訊」,把 '%m:%z' 當檔名、
    #   rc=1 但 stdout 照印一大段(含剩餘區塊數)⇒ 指紋每次都「變了」⇒ 一直重跑、刪檔也照重跑。
    #   BSD 的 `stat -c` 是非法選項:只寫 stderr、stdout 空、rc≠0,所以這個順序兩邊都對。
    one="$(stat -c '%Y:%s' "$f" 2>/dev/null || stat -f '%m:%z' "$f" 2>/dev/null)"
    [ -n "$one" ] || { printf ''; return 0; }
    out="$out$one|"
  done
  printf '%s' "$out"
}

# pane_reexec_log:把一次重跑寫成一行 JSON(附舊/新指紋),路徑可用 EZ2_PANE_REEXEC_LOG 覆寫。
# 寫不進去不得讓主流程死:整段容錯,失敗就只剩 stderr 那一行。
PANE_REEXEC_LOG="${EZ2_PANE_REEXEC_LOG:-$HOME/.ez2-aib/logs/pane-reexec.log}"
pane_reexec_log() {
  local dir; dir="${PANE_REEXEC_LOG%/*}"
  [ -d "$dir" ] || mkdir -p "$dir" 2>/dev/null || return 0
  printf '{"ts":"%s","pid":%s,"pane":"%s","script":"%s","old":"%s","new":"%s"}\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$$" "${TMUX_PANE:-}" "$1" "$2" "$3" \
    >> "$PANE_REEXEC_LOG" 2>/dev/null || true
}

# pane_reexec_if_changed:呼叫端每圈叫一次(零成本,除了每 PANE_REEXEC_INTERVAL 秒的一支 stat)。
# 指紋變了就 exec 自己;PANE_SELF_SCRIPT 沒設或指紋量不到 ⇒ 什麼都不做。
# ★ BL(2026-09-04,艦長 a6b2e 派):重跑要**帶回引數**。
#   面板三支不吃 argv,所以第一版 `exec bash "$SELF"` 對它們是對的;但凡是
#   「模式住在 argv 裡」的長駐腳本(`--loop`、`watch --interval …`),不帶引數重跑
#   就退回預設模式然後**正常退出** —— 留痕有了、行程沒了,rc=0,而那與「它更新完
#   就死了」逐字相同。呼叫端要帶引數就設陣列 `PANE_SELF_ARGS=("$@")`;
#   **沒設等於一個引數都不帶,與本次改動之前逐位元組同**(空陣列在 bash 3.2 + set -u
#   下會炸 unbound,所以用 `${PANE_SELF_ARGS+…}` 先問存不存在再展開)。
pane_reexec_if_changed() {
  [ -n "${PANE_SELF_SCRIPT:-}" ] || return 0
  [ "$SECONDS" -ge "$PANE_REEXEC_NEXT" ] || return 0
  PANE_REEXEC_NEXT=$(( SECONDS + PANE_REEXEC_INTERVAL ))
  local now; now="$(pane_script_fingerprint)"
  [ -n "$now" ] || return 0                  # 量不到=當沒變(失效側選舊碼繼續跑)
  if [ -z "$PANE_REEXEC_FINGERPRINT" ]; then
    PANE_REEXEC_FINGERPRINT="$now"
    return 0
  fi
  [ "$now" = "$PANE_REEXEC_FINGERPRINT" ] && return 0
  # exec 前最後一次確認檔還在:指紋與 exec 之間有個窗口,那一瞬間被刪一樣會死。
  [ -f "$PANE_SELF_SCRIPT" ] || return 0
  # ★ BF-3(09-03 16:4x):重跑要**留在檔案裡**,不能只寫 stderr。
  #   實測:重跑確實發生了(指紋換、迴圈的 SECONDS 歸零重數),而 stderr 那一行在 pane 裡
  #   看不到——我因此連續三次把「有開火」判成「沒開火」,還差點去改一個沒壞的東西。
  #   一個沒有留痕的自癒,與沒有自癒在畫面上逐字相同。
  pane_reexec_log "$PANE_SELF_SCRIPT" "$PANE_REEXEC_FINGERPRINT" "$now"
  printf 'pane: 腳本已更新,重跑自己(%s)\n' "$PANE_SELF_SCRIPT" >&2
  exec bash "$PANE_SELF_SCRIPT" ${PANE_SELF_ARGS+"${PANE_SELF_ARGS[@]}"}
}

# ── 組字停火(2026-10-10,the maintainer 第 2 項):終端機裡有一段還沒送出的中文時,不渲染 ──────────
# 為什麼:macOS 27 Terminal.app 2.15 的瑕疵——視窗裡有組字區(IME marked text)時,tmux 每送一批新畫面進來,
#   Terminal 就把組字區重釘回原位(-[TTView setMarkedText:…] → -[TTLogicalScreen displayPositionForUnicharOffset:]),
#   釘到已不存在的行就丟 NSRangeException:走重畫路徑 ⇒ AppKit 當場殺掉整個 Terminal(10-05 ×3、10-09 18:57 的 .ips,
#   所有艙的視窗一起沒);走收資料路徑 ⇒ 只記錯、那批資料解到一半被丟 ⇒ 畫面壞/視窗不見(10-10 13:40:39–50 六發,
#   931e 艙的視窗就是那十秒沒的;當天 04:52–14:28 共 17 發,每一發當下鼠鬚管都在 Setting marked text)。
#   Terminal 改不了、鼠鬚管沒 Xcode 重編不了;能動的是「組字期間少送畫面」。這不是根治(AI 席串流中的輸出
#   管不到),是把面板這一側的曝險拿掉;成效用 `log show --predicate 'process == "Terminal" AND
#   eventMessage CONTAINS "NSRangeException"'` 每小時幾發來量。
# 旗標:$EZ2_IME_COMPOSING_FLAG(預設 ~/.ez2-aib/state/ime-composing),由鼠鬚管的 lua/liu_aib_quiet.lua 寫
#   (只在 app_options 標了 aib_terminal 的 app 組字時寫;內容 = 最後更新的 epoch 秒;送出/取消/切走就刪)。
# 失效側一律「照畫」:旗標不在、內容不是數字、超過 EZ2_IME_QUIET_MAX 秒(預設 5;lua 每個鍵都重寫,所以打字中不會過期,停手 5 秒或 lua 漏清就恢復畫面)沒更新 ⇒ 回 rc1 當沒在組字。
#   理由同 pane_attached:量不到當沒人組字,代價只是多一次曝險;反過來會讓看得見的面板凍住,那才是缺陷。
# 成本:旗標不在時零 fork(一個 [ -f ]);旗標在時每拍一支 date(只在組字那幾秒)。
# 用法:主迴圈 `pane_wait_if_composing && continue`,放在 pane_wait_if_detached 後面。
PANE_COMPOSING_SEEN=0
pane_wait_if_composing() {
  local f="${EZ2_IME_COMPOSING_FLAG:-$HOME/.ez2-aib/state/ime-composing}" t="" now max tick
  if [ ! -f "$f" ]; then
    PANE_COMPOSING_SEEN=0
    return 1
  fi
  pane_read_file t "$f"
  case "$t" in ''|*[!0-9]*) return 1 ;; esac
  max="${EZ2_IME_QUIET_MAX:-5}"; case "$max" in ''|*[!0-9]*|0) max=5 ;; esac
  now="$(date +%s 2>/dev/null)"; case "$now" in ''|*[!0-9]*) return 1 ;; esac
  [ $(( now - t )) -le "$max" ] || return 1
  PANE_COMPOSING_SEEN=1
  tick="${EZ2_IME_QUIET_TICK:-0.25}"
  pane_tick_sleep "$tick"
  return 0
}

PANE_DETACHED_WAIT=""
pane_wait_if_detached() {
  if pane_attached; then
    PANE_DETACHED_WAIT=""
    return 1
  fi
  local t="${EZ2_PANE_DETACHED_TICK:-2}" tmax="${EZ2_PANE_DETACHED_TICK_MAX:-10}"
  case "$t" in ''|*[!0-9]*|0) t=2 ;; esac
  case "$tmax" in ''|*[!0-9]*|0) tmax=10 ;; esac
  if [ -z "$PANE_DETACHED_WAIT" ]; then
    PANE_DETACHED_WAIT="$t"
  else
    PANE_DETACHED_WAIT=$(( PANE_DETACHED_WAIT * 2 ))
    [ "$PANE_DETACHED_WAIT" -gt "$tmax" ] && PANE_DETACHED_WAIT="$tmax"
  fi
  pane_tick_sleep "$PANE_DETACHED_WAIT"
  return 0
}
