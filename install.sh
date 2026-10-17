#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# aib 一行安裝,也是一行更新(10-09 起開源版預設:從 github.com/ez2app/ez2-aib 取,裝到 ~/ez2-aib)
#   curl -fsSL https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh | bash                  沒裝過就安裝;裝過就只快轉到最新(不重抓、不蓋你的改動)
#   curl -fsSL https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh | bash -s -- --tools     另外再問一次要加裝哪些 AI 工具
#   (網址跟 README 一致;https://aib.ez2.app/install.sh 是同一支的短網址,兩個都可以。)
#   bash aib-bootstrap.sh [--dir DIR] [--tools] [--package-base HTTPS_URL …] [--dry-run];管理者舊路徑:--source GIT_URL
# 一開始問一次管理員密碼(sudo),之後自動裝:Apple 開發者工具、Homebrew、tmux、勾選的 AI 工具、aib。可重跑;已有的跳過。
# bash 照已排定的第 3 步升級,這裡不動。必須本人在終端機執行(要輸入密碼)。
# 整支包在 main 裡,下載不完整不會跑半截。
# 10-04 FishMac a seat(外網使用者裝不起來時沒有人會連進去幫忙):每一種失敗都要印白話原因與「怎麼辦」,
#   全程寫紀錄檔(只有本人讀得到),失敗時另存一份遮掉個資的「給客服」版本並告訴使用者檔在哪。
# 10-04 a seat 審查 P1/P2 已改:紀錄檔等 tee 寫完才遮蔽、遮蔽失敗不產生可寄送檔、中斷統一清理、
#   第三方 AI 安裝先下載再跑並檢查結束碼/逾時/版本、無桌面不白等、AIB_AI 白名單、macOS/磁碟門檻依實際支援範圍。

# ── 中斷與結束時的統一清理(Ctrl-C、關視窗、出錯都會走到;只清這次自己建立的東西)──
_TICK_PID="" _KEEP_PID="" _AI_PID="" _CLT_MARK="" _TMP_FILES="" _TMP_DIRS="" _TEE_PID="" _FIFO="" _SUMMARY=""
# _reap <pid>:整組結束一個背景程序並等它收完;期間把 bash 自己的「Terminated/警告」訊息導走(bash 3.2 會印英文到使用者畫面)
_reap() {
  [ -n "${1:-}" ] || return 0
  exec 5>&2 2>/dev/null
  kill -KILL -- "-$1" 2>/dev/null; kill -KILL "$1" 2>/dev/null; wait "$1" 2>/dev/null
  exec 2>&5 5>&-
  return 0
}
_cleanup() {
  _reap "${_TICK_PID:-}"; _TICK_PID=""
  _reap "${_KEEP_PID:-}"; _KEEP_PID=""
  # 10-04 a seat r2 P1-3:第三方安裝程式跑在自己的程序群組,整組一起停(子孫程序也收掉,不碰本程式所在的群組)
  _reap "${_AI_PID:-}"; _AI_PID=""
  [ -n "${_CLT_MARK:-}" ] && rm -f "$_CLT_MARK"; _CLT_MARK=""     # 只有這次建立的標記才記在這裡
  local f; for f in ${_TMP_FILES:-}; do rm -f "$f"; done; _TMP_FILES=""
  for f in ${_TMP_DIRS:-}; do case "$f" in /private/tmp/*|/var/folders/*) rm -rf "$f" ;; esac; done; _TMP_DIRS=""
  [ "${1:-}" = preserve-summary ] || { [ -n "${_SUMMARY:-}" ] && rm -f "$_SUMMARY"; _SUMMARY=""; }
  return 0
}
# 背景小程序(監看、心跳、保活)一律先 trap - :不繼承主程式的中斷處理,被停掉時安靜結束
#   (10-04 實測:沒加時被 kill 會印英文「run_pending_traps: signal handler is SIG_DFL…」到使用者畫面)
# 10-04 a seat r2 P1-2:紀錄收尾(還原輸出 → 等 tee 寫完,最多 20 秒 → 刪管線檔)。可重入:正常結束、Ctrl-C、關視窗都呼叫。
_finish_log() {
  # 中斷剛好發生在 run_limited 暫時導走 stderr 的那段時,先把 stderr 還原(否則「已中斷」的說明會被吞掉)
  { : >&5; } 2>/dev/null && exec 2>&5 5>&-
  if [ -n "${_TEE_PID:-}" ]; then
    exec 1>&3 2>&4 3>&- 4>&-
    local n=0   # 在主程式裡輪詢,不另開背景監看(背景小程序剛啟動就被停掉會印英文警告)
    while kill -0 "$_TEE_PID" 2>/dev/null && [ "$n" -lt 100 ]; do sleep 0.2; n=$((n+1)); done   # 最多等 20 秒
    kill -KILL "$_TEE_PID" 2>/dev/null; wait "$_TEE_PID" 2>/dev/null   # tee 不理 TERM(見上),等滿 20 秒才硬收
    _TEE_PID=""
  fi
  [ -n "${_FIFO:-}" ] && rm -f "$_FIFO"; _FIFO=""
  return 0
}

# ── 給客服的摘要 ──
# 10-04 a seat r2 P1-4:改成「允許清單」——客服摘要只從 say() 的獨立紀錄取資料,第三方工具共用 stdout 的輸出一律不收;
#   再遮個資;最後用涵蓋每一類、比遮蔽更寬的掃描檢查,還有任何一類就整份不產生。原始紀錄只留在本機(0600)。
_esc() { printf '%s' "$1" | sed -e 's/[][\.*^$|/+?(){}]/\\&/g'; }
# 10-07:整字邊界。BSD(macOS)的 sed/grep 認 [[:<:]] [[:>:]];GNU(Linux)不認、直接出錯 ⇒ 以前在 Linux 上
#   給客服的那份一份都不產生、掃描也漏掉帳號名。先試這台認哪一種,GNU 用 \b。
if printf 'a\n' | sed -E 's/[[:<:]]a[[:>:]]/b/' >/dev/null 2>&1; then _WB_L='[[:<:]]' _WB_R='[[:>:]]'; else _WB_L='\b' _WB_R='\b'; fi
_support_scan() {   # _support_scan <檔>:找得到任何個資/秘密就印出類別並回 0
  local f="$1" me host cname part
  me="$(id -un)"; host="$(hostname -s 2>/dev/null)"; cname="$(scutil --get ComputerName 2>/dev/null)"
  [ "${#me}" -ge 3 ] && grep -qE "${_WB_L}$(_esc "$me")${_WB_R}" "$f" && { echo 帳號名; return 0; }
  [ "${#host}" -ge 3 ] && grep -qiF "$host" "$f" && { echo 電腦名稱; return 0; }
  [ "${#cname}" -ge 3 ] && grep -qiF "$cname" "$f" && { echo 電腦名稱; return 0; }
  for part in $(printf '%s' "$cname" | tr -c 'A-Za-z0-9' ' '); do
    [ "${#part}" -ge 5 ] && grep -qiF "$part" "$f" && { echo 電腦名稱片段; return 0; }; done
  grep -qE 'AAAA[A-Za-z0-9+/=]{20,}' "$f" && { echo 公鑰; return 0; }
  grep -qiE '(authorization|cookie|x-api-key|api[_-]?key)[[:space:]]*[:=][[:space:]]*[^<[:space:]]' "$f" && { echo 認證標頭; return 0; }
  grep -qiE 'bearer[[:space:]]+[^<[:space:]]' "$f" && { echo 認證標頭; return 0; }
  grep -qiE '(token|secret|passw(or)?d|pass|code|activation|key)[[:space:]]*[=:][[:space:]]*[^<&[:space:]]' "$f" && { echo 參數秘密; return 0; }
  grep -qiE '(sk-|sk_|ghp_|gho_|ghs_|github_pat_|xox[abp]-|akia|aiza)[a-z0-9_-]{6,}' "$f" && { echo 金鑰; return 0; }
  grep -qE '[0-9]{1,3}(\.[0-9]{1,3}){3}' "$f" && { echo IPv4; return 0; }
  grep -qiE '([0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}|[0-9a-f]{0,4}::[0-9a-f]' "$f" && { echo IPv6; return 0; }
  grep -qiE '([0-9a-f]{2}[:-]){5}[0-9a-f]{2}' "$f" && { echo MAC; return 0; }
  grep -qiE '\.ts\.net' "$f" && { echo Tailscale; return 0; }
  grep -qiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' "$f" && { echo 識別碼; return 0; }
  grep -qiE 'serial[^:]*:[[:space:]]*[^<[:space:]]' "$f" && { echo 序號; return 0; }
  return 1
}
_support_copy() {   # _support_copy <原始紀錄> <輸出檔>;成功回 0;失敗時把找到的類別印在 stdout
  local src="$1" out="$2" me host cname part tmp
  me="$(id -un)"; host="$(hostname -s 2>/dev/null)"; cname="$(scutil --get ComputerName 2>/dev/null)"
  tmp="$out.tmp"
  ( umask 077
    printf '%s\n' "Ez2 AIB 安裝摘要(只含安裝程式自己的訊息,不含第三方工具的輸出)" \
      "系統:macOS $(sw_vers -productVersion 2>/dev/null) / $(uname -m) / 磁碟剩 $(df -g "$HOME" 2>/dev/null | awk 'NR==2{print $4}') GB" ""
    [ ! -f "$src" ] || cat "$src" ) > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  local red=(-E
    -e "s|$(_esc "$HOME")|~|g"
    -e "s#(ssh-[a-z0-9-]+|ecdsa-[a-z0-9-]+) AAAA[A-Za-z0-9+/=]+#<公鑰>#g"
    -e "s#([Aa]uthorization|[Cc]ookie|[Xx]-[Aa]pi-[Kk]ey):.*#\\1: <已遮>#g"
    -e "s#([Bb]earer) [^[:space:]]+#\\1 <已遮>#g"
    -e "s#(sk-|sk_|ghp_|gho_|ghs_|github_pat_|xox[abp]-|AKIA|AIza)[A-Za-z0-9_-]{6,}#<金鑰>#g"
    -e "s#(([Tt]oken|[Kk]ey|[Ss]ecret|[Pp]ass(word|wd)?|[Cc]ode|[Aa]ctivation)=)[^&[:space:]]+#\\1<已遮>#g"
    -e "s#[A-Za-z0-9.-]+\\.ts\\.net#<Tailscale 名稱>#g"
    -e "s#[0-9]{1,3}(\\.[0-9]{1,3}){3}#<IP>#g"
    -e "s#([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}#<MAC>#g"
    -e "s#[0-9A-Fa-f]{0,4}::[0-9A-Fa-f:]*#<IP>#g"
    -e "s#([0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}#<IP>#g"
    -e "s#[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}#<識別碼>#g"
    -e "s#(Serial Number[^:]*: *)[A-Z0-9]+#\\1<已遮>#g")
  # 先遮長的再遮短的:主機名稱常是電腦名稱的一部分,先換短的會讓完整名稱對不上而漏遮(10-04 實測漏掉「a-mac…13」)
  [ "${#cname}" -ge 3 ] && red+=(-e "s|$(_esc "$cname")|<電腦名稱>|g")
  [ "${#host}" -ge 3 ] && red+=(-e "s|$(_esc "$host")|<電腦名稱>|g")
  for part in $(printf '%s' "$cname" | tr -c 'A-Za-z0-9' ' '); do   # 電腦名稱裡 5 字以上的片段也遮
    [ "${#part}" -ge 5 ] && red+=(-e "s|$(_esc "$part")|<電腦名稱>|g"); done
  if [ "${#me}" -ge 3 ]; then
    red+=(-e "s|/Users/$(_esc "$me")|/Users/<使用者>|g" -e "s|${_WB_L}$(_esc "$me")${_WB_R}|<使用者>|g"); fi
  ( umask 077; sed "${red[@]}" "$tmp" > "$out" ) 2>/dev/null || { rm -f "$tmp" "$out"; return 1; }
  rm -f "$tmp"
  if _support_scan "$out"; then rm -f "$out"; return 1; fi
  chmod 600 "$out"; return 0
}

main() {
  umask 077
  trap '_cleanup; _finish_log' EXIT
  trap '_cleanup; _finish_log; printf "\n已中斷。重跑同一行指令即可,已裝好的會自動跳過。\n" >&2; exit 130' INT
  trap '_cleanup; _finish_log; exit 143' TERM
  trap '_cleanup; _finish_log; exit 129' HUP
  local LOGDIR="$HOME/.ez2-aib/install-logs" LOG=""
  if mkdir -p "$LOGDIR" 2>/dev/null; then
    chmod 700 "$LOGDIR" 2>/dev/null
    LOG="$LOGDIR/install-$(date +%Y%m%d-%H%M%S).log"
    _SUMMARY="$LOGDIR/.summary-$$"
    : > "$_SUMMARY"; chmod 600 "$_SUMMARY"
    _FIFO="$LOGDIR/.out-$$.fifo"; rm -f "$_FIFO"
    if mkfifo -m 600 "$_FIFO" 2>/dev/null; then
      : > "$LOG"; chmod 600 "$LOG"
      # 10-07 Mac 實踩:整組收到 TERM/HUP/INT 時 tee 先死,bash 印「Terminated」進 fifo 就吃 SIGPIPE ⇒ 死在半路(-13)、
      #   trap 沒跑、fifo 與摘要留下。tee 改成不理這三個訊號(忽略會跟著 exec 繼承),寫端關掉讀到 EOF 才自己結束。
      ( trap '' TERM HUP INT; exec tee -a "$LOG" ) < "$_FIFO" & _TEE_PID=$!
      exec 3>&1 4>&2 > "$_FIFO" 2>&1
    else
      LOG=""; _FIFO=""
    fi
  fi
  install_main "$@"; local rc=$?
  _cleanup preserve-summary
  _finish_log   # 10-04 @00000 P1-1:先等 tee 把紀錄寫完才做客服版
  if [ "$rc" != 0 ] && [ -n "$LOG" ]; then
    local pub="${LOG%.log}-給客服.log"
    printf '%s\n' "" "沒有裝完。修好上面「怎麼辦」那一項後,重跑同一行指令即可,已裝好的會自動跳過。"
    if _support_copy "$_SUMMARY" "$pub" >/dev/null; then
      printf '%s\n' "需要協助時,把這個檔寄給我們(已遮掉帳號名、電腦名稱、IP、金鑰等個資):" "  $pub"
    else
      printf '%s\n' "需要協助時,請把這個視窗的畫面截圖寄給我們。" \
        "(紀錄檔裡有沒能完全遮掉的個人資料,為了保護你,這次不產生可寄送的檔案。)"
    fi
  fi
  [ -n "${_SUMMARY:-}" ] && rm -f "$_SUMMARY"; _SUMMARY=""
  return "$rc"
}

install_main() {
  set -u
  local SOURCE="${AIB_SOURCE:-}"
  local DIR="${AIB_DIR:-}" DRY=0 TOOLS=0 UPDATE=0 miss=() note=()
  local PUBLIC_SOURCE="https://github.com/ez2app/ez2-aib.git"
  local PACKAGE_BASE="${AIB_PACKAGE_BASE:-}" PACKAGE_BUNDLE="${AIB_PACKAGE_BUNDLE_DIR:-}"
  local PACKAGE_INSTALLER="${AIB_PACKAGE_INSTALLER:-}" ALLOWED_SIGNERS="${AIB_ALLOWED_SIGNERS:-}"
  local PACKAGE_INSTALLER_URL="${AIB_PACKAGE_INSTALLER_URL:-https://aib.ez2.app/package-install.sh}"
  local PACKAGE_INSTALLER_SHA256="${AIB_PACKAGE_INSTALLER_SHA256:-fede9c21b5bbb98f1c1c70016f73ad39eb1ed5e1213b985e326df429d882a7d5}"
  local RELEASE_SIGNER='release@ez2-aib ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEekiJWbGyrQb2FZcMfWI9FGlvtUZ4szXu5hFtwYAen2'
  say() { # 每個參數各一行;客服摘要另走獨立檔,不從混有第三方輸出的完整 log 猜來源
    printf '%s\n' "$@"
    [ -z "${_SUMMARY:-}" ] || printf '%s\n' "$@" >> "$_SUMMARY"
  }
  while [ $# -gt 0 ]; do
    case "$1" in
      --source|--dir|--package-base|--bundle-dir|--package-installer|--allowed-signers)
        [ $# -ge 2 ] || { say "✗ 參數 $1 後面要接一個值"; return 2; }
        case "$1" in
          --source) SOURCE="$2"; PACKAGE_BASE=""; PACKAGE_BUNDLE="" ;;
          --dir) DIR="$2" ;;
          --package-base) PACKAGE_BASE="$2"; PACKAGE_BUNDLE="" ;;
          --bundle-dir) PACKAGE_BUNDLE="$2"; PACKAGE_BASE="" ;;
          --package-installer) PACKAGE_INSTALLER="$2" ;;
          --allowed-signers) ALLOWED_SIGNERS="$2" ;;
        esac
        shift 2 ;;
      --dry-run) DRY=1; shift ;;
      --tools) TOOLS=1; shift ;;
      -h|--help) sed -n '2,6p' "$0" 2>/dev/null; return 0 ;;
      *) say "✗ 不認得的參數:$1"; return 2 ;;
    esac
  done
  # 開源預設走公開 git。簽章套件要明確指定 --package-base / --bundle-dir / AIB_PACKAGE_BASE。
  if [ -z "$PACKAGE_BASE" ] && [ -z "$PACKAGE_BUNDLE" ]; then
    SOURCE="${SOURCE:-$PUBLIC_SOURCE}"
  fi
  # 10-09 a seat(the maintainer「開源版要一行可以直接更新」):公開版預設裝到 ~/ez2-aib —— 不碰 ~/ez2-aib(開發機上的私有正本)。
  #   管理者走 --source 舊路徑時預設仍是 ~/ez2-aib;--dir / AIB_DIR 照舊優先。
  # 10-11(the maintainer FishMac 實裝):提示裡的「重跑這行」要是一定連得到的網址 ⇒ 公開來源(不是 ssh)指 GitHub 上的 install.sh;
  #   私有倉 / 簽章套件照舊用網站。
  local SELF_URL="https://aib.ez2.app/install.sh"
  case "$SOURCE" in ""|ssh://*|git@*) ;; *) SELF_URL="https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh" ;; esac
  if [ -z "$DIR" ]; then
    if [ "$SOURCE" = "$PUBLIC_SOURCE" ]; then DIR="$HOME/ez2-aib"; else DIR="$HOME/ez2-aib"; fi
  fi
  # 10-04 乾淨虛擬機:~/.local/bin 不在 PATH 時,Grok 安裝程式不放捷徑(我們就找不到它)、Claude 安裝程式印英文的「請自己加 PATH」。
  #   一開始就放進這次執行的 PATH(寫進 ~/.zprofile 讓之後的視窗也有,仍在後面第 5 步)。
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
  run() { if [ "$DRY" = 1 ]; then say "  (試跑)$*"; else "$@"; fi; }
  # fail <發生什麼> <怎麼辦>:白話兩行,呼叫端接 return
  fail() { say "✗ $1"; [ -n "${2:-}" ] && say "  → 怎麼辦:$2"; return 0; }
  # same_source:安裝位置已有一份時,它必須是從同一個來源裝的才准快轉更新(10-09 a seat)。
  #   不是 ⇒ 一律不碰(多半是開發者自己的倉庫,例如 ~/ez2-aib 正本),回 2 並說怎麼辦;是 ⇒ UPDATE=1(這次是更新)。
  # 10-12 Pro 實裝抓到(a seat 1459fa8a7 找到原因、a seat a336ddb30 補完整,a seat 取後者合併):
  #   git 全域 url.git@github.com:.insteadOf https://github.com/ 時,https 裝的那份 remote get-url 會回 ssh 寫法
  #   ⇒ 一行安裝/更新把同一個倉庫當成「別的來源」整個擋掉(Pro install-20261012-231841)。
  #   網址一律化成 https://<主機>/<擁有者>/<倉庫>(小寫):git@、ssh://(含 :22)、https、http、git:// 都算;
  #   別的主機、別的倉照舊不同;本機路徑照舊不動(不改大小寫)。測試照函式開頭那行與結尾單獨一行的右大括號抽出整個函式(說明裡別寫出開頭那行的原字)。
  _norm_src() {
    local x="${1%/}"; x="${x%.git}"
    case "$x" in
      git@*:*) x="${x#git@}"; x="${x%%:*}/${x#*:}" ;;
      ssh://*|https://*|http://*|git://*) x="${x#*://}"; x="${x#*@}"; x="${x/:22\//\/}" ;;
      *) printf '%s' "$x"; return 0 ;;
    esac
    printf 'https://%s' "$x" | tr '[:upper:]' '[:lower:]'
  }
  same_source() {
    [ -n "$SOURCE" ] && [ -d "$DIR/.git" ] || return 0
    local have; have="$(git -C "$DIR" remote get-url origin 2>/dev/null || true)"
    if [ "$(_norm_src "$have")" != "$(_norm_src "$SOURCE")" ]; then
      fail "$DIR 已經有一份程式,但它不是從 $SOURCE 裝的(它的來源是 ${have:-沒有設定}),這次沒有動它" \
           "這份若是你自己的開發用倉庫,就別讓安裝程式碰它;要另外裝一份公開版,重跑時指定別的位置:curl -fsSL $SELF_URL | bash -s -- --dir \"\$HOME/ez2-aib-public\""
      return 2
    fi
    UPDATE=1
  }
  have_tty() { ( : < /dev/tty ) 2>/dev/null; }   # 真的打得開才算(-r 只看權限,沒有終端機時也會是真)
  # 長步驟的心跳:每 60 秒印一行,讓人知道沒當掉
  tick_start() { local t0; t0=$(date +%s); set -m
    ( trap - INT TERM HUP EXIT; while sleep 60; do say "  … 還在進行,已經 $(( ($(date +%s)-t0)/60 )) 分鐘(請不要關掉這個視窗)"; done ) & _TICK_PID=$!
    set +m; }
  tick_stop() { _reap "${_TICK_PID:-}"; _TICK_PID=""; }
  # 10-04 the maintainer:「加上每步花費時間」——每一步完成印實際花了多久,結尾印總時間
  local T_ALL; T_ALL=$(date +%s)
  took() { local d=$(( $(date +%s) - $1 )); if [ "$d" -ge 60 ]; then printf '%d 分 %d 秒' $((d/60)) $((d%60)); else printf '%d 秒' "$d"; fi; }
  # run_limited <秒> <指令…>:跑指令,超過秒數就停掉並回 124
  run_limited() {
    local secs="$1" t0 rc; shift; t0=$(date +%s)
    set -m; "$@" & _AI_PID=$!; set +m      # set -m:讓它自成一個程序群組,逾時/中斷可整組停掉
    local timed_out=0   # 在主程式裡每 0.2 秒看一次,不另開背景監看
    exec 5>&2 2>/dev/null   # 這段期間 bash 自己印的「Terminated: 15」導走;安裝程式啟動時已拿到原本的 stderr,不受影響
    while kill -0 "$_AI_PID" 2>/dev/null; do
      if [ $(( $(date +%s) - t0 )) -ge "$secs" ]; then
        timed_out=1
        printf '%s\n' "  … 這一步超過 $((secs/60)) 分鐘還沒好,先把它停掉(接下來若出現英文錯誤訊息,是它被停掉時自己印的,可以忽略)"
        kill -TERM -- "-$_AI_PID" 2>/dev/null
        local k=0; while kill -0 "$_AI_PID" 2>/dev/null && [ "$k" -lt 25 ]; do sleep 0.2; k=$((k+1)); done
        kill -KILL -- "-$_AI_PID" 2>/dev/null; break
      fi
      sleep 0.2
    done
    wait "$_AI_PID" 2>/dev/null; rc=$?   # 2>/dev/null:不讓 bash 印英文的「Terminated: 15」
    kill -TERM -- "-$_AI_PID" 2>/dev/null   # 主程式結束了,它留下的子孫一起收掉
    exec 2>&5 5>&-
    _AI_PID=""
    [ "$timed_out" = 1 ] && return 124
    return "$rc"
  }

  # 10-04 the maintainer:「加一個流程,讓用戶勾選要安裝哪些 CLI AI」。一開始就問完(之後全自動,使用者可以走開)。
  #   代號|顯示名|指令名|安裝方式|登入指令|實測最快|單步上限秒數
  #   安裝方式:cask:<名稱>(Homebrew)/ bash:<網址> / sh:<網址>(官方安裝程式,先完整下載再執行)
  #   ★ copilot 用 GitHub 官方獨立安裝程式(gh.io/copilot-install,README 已查證;一般帳號裝到 ~/.local/bin、不會停下來問)。
  #     10-04 乾淨虛擬機:brew cask copilot-cli 下載超過 8 分鐘。另:formula「copilot」是 AWS 的工具,不是 GitHub Copilot。
  #   ★ codex 用 OpenAI 官方獨立安裝程式(10-04 brew cask 在乾淨機器實測 9 分鐘;a seat 建議,README 已查證)。
  local AI_TABLE='claude|Claude Code(Anthropic)|claude|bash:https://claude.ai/install.sh|claude(第一次開啟會帶你登入)|最快約 11 秒|900
codex|Codex(OpenAI)|codex|sh:https://chatgpt.com/codex/install.sh|codex login|最快約 5 秒|900
agy|Antigravity(Google)|agy|cask:antigravity-cli|agy(第一次開啟會帶你登入)|最快約 8 秒|900
copilot|Copilot(GitHub)|copilot|copilot:official|copilot(進去後輸入 /login)|下載包約 97 MB,中斷後重跑會接著下載|1800
grok|Grok(xAI)|grok|bash:https://x.ai/cli/install.sh|grok login|最快約 3 秒|900'
  local AI_PICK="" AI_LOGIN="" VOUT=""
  show_miss() { [ "${#miss[@]}" -gt 0 ] || return 0; say "  另外,前面這些也沒裝上(之後重跑同一行指令會再試):"; local x; for x in "${miss[@]}"; do say "    ✗ $x"; done; }
  # verify_cmd <執行檔>:實跑一次 --version(30 秒上限),結束碼 0 且有輸出才算成功;成功時 VOUT=第一行
  #   10-04 a seat r2 P1-1:以前經管線取第一行,拿到的是 head 的結束碼 ⇒ 版本指令失敗也會被當成功
  verify_cmd() {
    local out rc; out="$(mktemp "${TMPDIR:-/tmp}/aib-ver.XXXXXX")"; _TMP_FILES="$_TMP_FILES $out"
    run_limited 30 "$1" --version </dev/null >"$out" 2>&1; rc=$?
    VOUT="$(head -n1 "$out")"; rm -f "$out"
    [ "$rc" = 0 ] || return "$rc"
    [ -n "$VOUT" ] || return 1
    return 0
  }
  # fetch_run <網址> <直譯器> <上限秒數> [環境變數設定…]:完整下載到暫存檔(非空)才執行。
  #   回傳:6=下載失敗(沒有執行)、124=逾時、其他=安裝程式自己的結束碼
  fetch_run() {
    local url="$1" interp="$2" limit="$3" tmp rc; shift 3
    tmp="$(mktemp "${TMPDIR:-/tmp}/aib-dl.XXXXXX")"; _TMP_FILES="$_TMP_FILES $tmp"
    if ! curl -fsSL --max-time 300 -o "$tmp" "$url" || [ ! -s "$tmp" ]; then rm -f "$tmp"; return 6; fi
    tick_start; run_limited "$limit" env "$@" "$interp" "$tmp" </dev/null; rc=$?; tick_stop
    rm -f "$tmp"; return "$rc"
  }
  # GitHub 官方腳本把 97 MB 檔案放在一次性目錄，逾時後重跑會從零開始。
  # 這裡仍只取 GitHub 官方 release 與官方 SHA256SUMS，但把未完成檔留在私有 cache，重跑用 curl -C - 續傳。
  install_copilot() {
    local limit="$1" platform arch base file sums expected cache tmp rc got entries
    case "$(uname -s)" in Darwin) platform=darwin ;; *) return 3 ;; esac
    case "$(uname -m)" in arm64|aarch64) arch=arm64 ;; x86_64|amd64) arch=x64 ;; *) return 3 ;; esac
    base="https://github.com/github/copilot-cli/releases/latest/download"
    file="copilot-${platform}-${arch}.tar.gz"
    cache="$HOME/.cache/ez2-aib/downloads/$file.part"
    sums="$(mktemp "${TMPDIR:-/tmp}/copilot-sums.XXXXXX")"; tmp="$(mktemp -d "${TMPDIR:-/tmp}/copilot-install.XXXXXX")"
    _TMP_FILES="$_TMP_FILES $sums"; _TMP_DIRS="$_TMP_DIRS $tmp"
    mkdir -p "$(dirname "$cache")"; chmod 700 "$HOME/.cache/ez2-aib" "$HOME/.cache/ez2-aib/downloads" 2>/dev/null
    curl -fsSL --max-time 60 "$base/SHA256SUMS.txt" -o "$sums" || { rm -rf "$tmp"; return 6; }
    expected="$(awk -v f="$file" '$2==f || $2=="*"f {print $1; exit}' "$sums")"
    [ -n "$expected" ] || { rm -rf "$tmp"; return 6; }
    # 完整但屬於舊 release 的 cache 不能續接新檔；不完整檔才保留續傳。
    if [ -s "$cache" ] && tar -tzf "$cache" >/dev/null 2>&1; then
      got="$(shasum -a 256 "$cache" | awk '{print $1}')"
      [ "$got" = "$expected" ] || rm -f "$cache"
    fi
    tick_start
    run_limited "$limit" curl -fL --retry 3 --retry-delay 2 --connect-timeout 20 \
      --continue-at - -o "$cache" "$base/$file" </dev/null; rc=$?
    tick_stop
    [ "$rc" = 0 ] || { rm -rf "$tmp"; return "$rc"; }
    chmod 600 "$cache"
    got="$(shasum -a 256 "$cache" | awk '{print $1}')"
    [ "$got" = "$expected" ] || { rm -f "$cache"; rm -rf "$tmp"; return 201; }
    entries="$(tar -tzf "$cache")" || { rm -f "$cache"; rm -rf "$tmp"; return 201; }
    # 官方包目前只有 copilot；即使日後多檔，也不准絕對路徑或 .. 越出暫存目錄。
    printf '%s\n' "$entries" | awk -F/ '$0 ~ /^\// || $0 ~ /(^|\/)\.\.(\/|$)/ {bad=1} END {exit bad}' \
      || { rm -f "$cache"; rm -rf "$tmp"; return 201; }
    tar -xzf "$cache" -C "$tmp" || { rm -rf "$tmp"; return 201; }
    [ -x "$tmp/copilot" ] || { rm -rf "$tmp"; return 201; }
    mkdir -p "$HOME/.local/bin"
    install -m 755 "$tmp/copilot" "$HOME/.local/bin/copilot" || { rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"
    return 0
  }
  ai_path() { command -v "$1" 2>/dev/null || { [ -x "$HOME/.local/bin/$1" ] && printf '%s\n' "$HOME/.local/bin/$1"; }; }
  ai_have() { [ -n "$(ai_path "$1")" ]; }
  ai_field() { printf '%s\n' "$AI_TABLE" | awk -F'|' -v id="$1" -v n="$2" '$1==id{print $n}'; }
  choose_ai() {
    local ids=() names=() bins=() on=() i=0 id name bin rest ans n w ok
    while IFS='|' read -r id name bin rest; do ids+=("$id"); names+=("$name"); bins+=("$bin"); on+=(0); done <<< "$AI_TABLE"
    if [ -n "${AIB_AI:-}" ]; then   # 不能互動時用環境變數指定,例:AIB_AI=claude,codex
      AI_PICK=""
      for w in $(printf '%s' "$AIB_AI" | tr ',' ' '); do
        [ "$w" = none ] && continue   # 10-12:AIB_AI=none = 不裝任何 AI 工具(CI 的全新電腦那關;之後要裝再 --tools)
        ok=0; for id in "${ids[@]}"; do [ "$id" = "$w" ] && ok=1; done
        [ "$ok" = 1 ] || { fail "AIB_AI 裡的「${w}」不認得" "只能填這些(用逗號分隔):${ids[*]}"; return 2; }
        AI_PICK="$AI_PICK $w"
      done
      AI_PICK="${AI_PICK# }"; say "… 依 AIB_AI 安裝:${AI_PICK:-(無)}"; return 0
    fi
    if ! have_tty; then say "  · 沒有可以打字的視窗,AI 工具先不裝(之後可重跑並設 AIB_AI=claude,codex)"; return 0; fi
    while :; do
      say "" "要安裝哪些 AI 工具?輸入數字切換勾選(可一次輸入多個,例如 1 3),輸入 a 全選,直接按 Enter 確認:"
      i=0; while [ "$i" -lt "${#ids[@]}" ]; do
        if ai_have "${bins[$i]}"; then say "  $((i+1)) [已裝] ${names[$i]}"
        elif [ "${on[$i]}" = 1 ]; then say "  $((i+1)) [x] ${names[$i]}"
        else say "  $((i+1)) [ ] ${names[$i]}"; fi
        i=$((i+1)); done
      printf '> '
      if ! IFS= read -r ans < /dev/tty; then say "" "  (沒讀到輸入,這次不裝 AI 工具)"; on=(); i=0; while [ "$i" -lt "${#ids[@]}" ]; do on+=(0); i=$((i+1)); done; break; fi
      [ -z "$ans" ] && break
      if [ "$ans" = a ] || [ "$ans" = A ]; then i=0; while [ "$i" -lt "${#ids[@]}" ]; do on[$i]=1; i=$((i+1)); done; continue; fi
      for n in $ans; do
        case "$n" in *[!0-9]*|'') say "  (看不懂「${n}」,請輸入數字)"; continue ;; esac
        [ "$n" -ge 1 ] && [ "$n" -le "${#ids[@]}" ] || { say "  (沒有第 $n 項)"; continue; }
        i=$((n-1)); if [ "${on[$i]}" = 1 ]; then on[$i]=0; else on[$i]=1; fi
      done
    done
    AI_PICK=""; i=0; while [ "$i" -lt "${#ids[@]}" ]; do
      [ "${on[$i]}" = 1 ] && ! ai_have "${bins[$i]}" && AI_PICK="$AI_PICK ${ids[$i]}"; i=$((i+1)); done
    AI_PICK="${AI_PICK# }"
    if [ -n "$AI_PICK" ]; then
      say "✓ 會安裝下面這些(接下來不用再操作,可以先離開):"
      for w in $AI_PICK; do say "    $(ai_field "$w" 2) —— $(ai_field "$w" 6),隨網速變化"; done
    else say "  · 這次不裝 AI 工具"; fi
  }
  install_ai() {
    [ -n "$AI_PICK" ] || return 0
    local want name bin how login vendor limit t_ai rc bp
    for want in $AI_PICK; do
      name="$(ai_field "$want" 2)"; bin="$(ai_field "$want" 3)"; how="$(ai_field "$want" 4)"; login="$(ai_field "$want" 5)"; limit="$(ai_field "$want" 7)"
      if ai_have "$bin"; then say "✓ $name(已裝)"; AI_LOGIN="$AI_LOGIN|$name:$login"; continue; fi
      vendor="${name#*(}"; vendor="${vendor%)}"
      say "… [$vendor] 裝 ${name%%(*}(接下來的訊息來自 $vendor 的安裝程式)"
      if [ "$DRY" = 1 ]; then say "  (試跑)$how"; continue; fi
      t_ai=$(date +%s); rc=0
      case "$how" in
        cask:*)
          tick_start; run_limited "$limit" brew install --cask "${how#cask:}" </dev/null; rc=$?; tick_stop ;;
        copilot:official)
          install_copilot "$limit"; rc=$? ;;
        bash:*|sh:*)
          # 10-04 a seat P1-4:先完整下載到暫存檔,下載失敗就不執行;保留結束碼;設 15 分鐘上限
          fetch_run "${how#*:}" "${how%%:*}" "$limit" CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR="$HOME/.local/bin"; rc=$? ;;
      esac
      # 不只看檔案在不在:實際跑一次 --version,結束碼與輸出都要對
      bp="$(ai_path "$bin")"
      if [ "$rc" = 0 ]; then
        if [ -z "$bp" ]; then rc=127; else verify_cmd "$bp"; rc=$?; [ "$rc" = 0 ] || rc=200; fi
      fi
      local why=""
      case "$rc" in
        0)   say "✓ $name $VOUT(花了 $(took "$t_ai"))"; AI_LOGIN="$AI_LOGIN|$name:$login" ;;
        6)   why="$name 的官方安裝程式下載失敗(確認網路後重跑同一行指令)" ;;
        124) why="$name 安裝超過 $((limit/60)) 分鐘被停掉(多半是網路太慢,稍後重跑同一行指令再試)" ;;
        127) why="$name 裝完找不到它的指令(稍後重跑同一行指令再試)" ;;
        200) why="$name 裝了但跑不起來(稍後重跑同一行指令再試)" ;;
        201) why="$name 官方下載檔的 SHA-256 或內容不符,已刪除;請重跑同一行指令" ;;
        *)   why="$name 的安裝程式回報失敗(rc=$rc;稍後重跑同一行指令再試)" ;;
      esac
      # 10-04 乾淨虛擬機:以前只記在最後的總結,後面步驟提早停下時總結沒印 ⇒ 使用者完全不知道這家沒裝上。現在當場就印。
      if [ -n "$why" ]; then say "✗ $why"; miss+=("$why"); fi
    done
  }
  # 一開始就判來源(不先問密碼、不先裝東西);macOS 沒裝開發者工具時 git 會跳安裝視窗 ⇒ 那時留到第 4 步再判
  #   Linux 沒有 xcode-select:有 git 就判(10-11 雲端 Linux 實測:不判的話重跑標題寫「開始安裝」而不是「開始更新」)
  if { [ "$(uname -s)" != Darwin ] || xcode-select -p >/dev/null 2>&1; } && command -v git >/dev/null 2>&1; then same_source || return 2; fi
  # 預設走公開 git,裝在 $DIR(公開版 ~/ez2-aib;--source 舊路徑 ~/ez2-aib);簽章套件要明確指定才落到 ~/.local/share/ez2-aib/current/repo。
  local shown_dir
  if [ -n "$PACKAGE_BASE" ] || [ -n "$PACKAGE_BUNDLE" ]; then
    shown_dir="${AIB_PACKAGE_ROOT:-$HOME/.local/share/ez2-aib}/current/repo"
  else
    shown_dir="$DIR"
  fi
  case "$shown_dir" in "$HOME"/*) shown_dir="~${shown_dir#"$HOME"}" ;; esac
  if [ "$UPDATE" = 1 ]; then say "== 開始更新 Ez2 AIB(在 $shown_dir)$( [ "$DRY" = 1 ] && echo ' 【試跑,不改任何東西】') =="
  else say "== 開始安裝 Ez2 AIB(會裝在 $shown_dir)$( [ "$DRY" = 1 ] && echo ' 【試跑,不改任何東西】') =="; fi

  # 1. 系統、管理員密碼(只問這一次)
  # 10-10(the maintainer「在另一個 linux 測試」):公開版在 Linux 上不再直接拒絕 ⇒ 只裝測試安全網(clone + 連結 aib),
  #   跳過 Mac 才有的步驟(開發者工具、Homebrew、tmux、AI 工具、管理員密碼)。私有來源 / 簽章套件仍只支援 Mac。
  local LINUX=0
  if [ "$(uname -s)" != Darwin ]; then
    # 公開來源 = 不是 ssh 的那種(私有倉一律走 ssh;測試與自架鏡像用本機路徑 / https)
    if case "$SOURCE" in ssh://*|git@*) false ;; *) true ;; esac && [ -z "$PACKAGE_BASE$PACKAGE_BUNDLE" ]; then
      LINUX=1
      say "… 這台不是 Mac:只裝測試安全網(aib test、aib ship);駕駛艙需要 macOS,所以不裝 AI 工具、Homebrew、tmux"
      command -v git >/dev/null 2>&1 || { fail "這台沒有 git" "先用系統的套件管理裝好 git(例如 sudo apt install git)再跑同一行"; return 3; }
      python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
        || { fail "需要 Python 3.9 以上" "先裝好 Python 3.9 以上的版本,再跑同一行"; return 3; }
      case "$SOURCE" in https://*) [ "$DRY" = 1 ] || git ls-remote -q "$SOURCE" HEAD >/dev/null 2>&1 ;; *) true ;; esac \
        || { fail "連不上網路,或網路擋住了 github.com" "確認這台有連上網路;在公司網路請問網管是否擋住 github.com"; return 3; }
    else
      fail "目前只支援 Mac(macOS)" "請在 Mac 上執行"; return 3
    fi
  fi
  if [ "$LINUX" = 0 ]; then
  # 10-04 a seat P2-7(已查證 Homebrew 文件):Apple 晶片上 macOS 15~27 正式支援;11~14 不支援但可能可用;Intel 是第三級
  local osv osmaj; osv="$(sw_vers -productVersion)"; osmaj="${osv%%.*}"
  [ "$osmaj" -ge 11 ] 2>/dev/null || { fail "你的 macOS 是 $osv,太舊了(至少要 11,建議 15 以上)" "到「系統設定 → 一般 → 軟體更新」升級後再跑"; return 3; }
  [ "$osmaj" -ge 15 ] || note+=("你的 macOS 是 $osv:Homebrew 只正式支援 15 以上,這次可能可以裝,但之後若出問題建議先升級")
  [ "$(uname -m)" = arm64 ] || note+=("這台是 Intel 晶片:Homebrew 對 Intel Mac 只做最低限度的支援,遇到問題的機會較高")
  local free_gb; free_gb="$(df -g "$HOME" 2>/dev/null | awk 'NR==2{print $4}')"
  [ "${free_gb:-0}" -ge 5 ] 2>/dev/null || { fail "磁碟剩 ${free_gb:-?} GB,不夠(至少要 5 GB)" "清出空間後再跑(可到「系統設定 → 一般 → 儲存空間」看哪些可以刪)"; return 3; }
  [ "${free_gb:-0}" -ge 10 ] || say "  · 提醒:磁碟只剩 ${free_gb} GB,裝得起來,但建議之後清出一些空間"
  curl -fsSI --max-time 15 https://github.com >/dev/null 2>&1 || { fail "連不上網路,或網路擋住了 github.com" "確認這台有連上網路;在公司網路請問網管是否擋住 github.com"; return 3; }
  # 10-04 the maintainer:「要在一開始跟客戶說,要下載的是其他元件,不要讓程式小白覺得是 Ez2 AIB 的問題」
  #   分鐘數=10-04 乾淨虛擬機(macOS 26.6.2)實測的最快值;the maintainer:「寫最快時間,但要描述隨網速變化」。改數字前請先重測。
  local plan=() n=0 m
  xcode-select -p >/dev/null 2>&1 || plan+=("Apple 開發者工具 —— Apple 官方提供,最快約 1 分半")
  { command -v brew >/dev/null 2>&1 || [ -x /opt/homebrew/bin/brew ]; } || plan+=("Homebrew —— 開源的 Mac 軟體安裝工具,最快約 4 分鐘")
  command -v tmux >/dev/null 2>&1 || plan+=("tmux —— 開源的終端機視窗工具,最快約 2 分半")
  command -v flock >/dev/null 2>&1 || plan+=("flock —— 開源的小工具(防止同時改同一個檔),時間未實測")
  ask_ai() { [ "$UPDATE" = 0 ] || [ "$TOOLS" = 1 ] || [ -n "${AIB_AI:-}" ]; }   # 已裝過的更新:不再問 AI 工具(要問加 --tools)
  if ask_ai; then plan+=("你等一下勾選的 AI 工具 —— 由各家 AI 公司提供,每家時間不同,勾選後會告訴你"); fi
  plan+=("Ez2 AIB 本身 —— 最快約 1 分鐘")
  say "" "這支安裝程式會依序準備好下面這些東西:"
  for m in "${plan[@]}"; do n=$((n+1)); say "  $n. $m"; done
  say "上面是我們實測的最快時間;實際會隨你的網路速度與對方伺服器的狀況而變慢,慢一些是正常的。" \
      "除了最後一項,其他都是從各家官方網站下載的工具。" \
      "過程中出現英文訊息、進度條,或畫面停住幾分鐘,都是那些工具自己在運作,屬於正常現象,不是 Ez2 AIB 出錯。" ""
  local need_sudo=0
  xcode-select -p >/dev/null 2>&1 || need_sudo=1
  command -v brew >/dev/null 2>&1 || [ -x /opt/homebrew/bin/brew ] || need_sudo=1
  if [ "$need_sudo" = 1 ] && [ "$DRY" = 0 ]; then
    have_tty || { fail "這一步要輸入這台 Mac 的密碼,但目前沒有可以打字的視窗" "打開「終端機」App,在裡面貼上安裝指令"; return 3; }
    id -Gn | tr ' ' '\n' | grep -qx admin || { fail "你登入的帳號($(id -un))不是管理員,沒辦法安裝開發工具" "改用管理員帳號登入這台 Mac 再跑,或請管理員到「系統設定 → 使用者與群組」把你設成管理員"; return 3; }
    say "… 需要輸入一次這台 Mac 的登入密碼(打字時畫面不會顯示,打完按 Enter;之後不再問)"
    sudo -v < /dev/tty || { fail "密碼不對,沒拿到管理員權限" "密碼是你登入這台 Mac 用的那組;重跑同一行指令再試一次"; return 3; }
    set -m; ( trap - INT TERM HUP EXIT; while kill -0 $$ 2>/dev/null; do sudo -n true 2>/dev/null; sleep 50; done ) & _KEEP_PID=$!; set +m   # 安裝程式被強制結束時自己也停
  fi
  if [ "$DRY" = 0 ]; then
    if ask_ai; then choose_ai || return $?
    else say "… 這台已經裝過 aib:這次只更新,不再問要裝哪些 AI 工具(要加裝:curl -fsSL $SELF_URL | bash -s -- --tools)"; fi
  fi
  # 1a. Apple 開發者工具:用軟體更新在背景裝,不跳視窗
  local t_step; t_step=$(date +%s); local clt_new=0
  if ! xcode-select -p >/dev/null 2>&1; then clt_new=1
    say "… [Apple 官方] 裝開發者工具(最快約 1 分半,隨網速變化;這段不會有進度條,畫面停住是正常的)"
    if [ "$DRY" = 0 ]; then
      # 10-03 FishMac a seat:標記檔要**留到 softwareupdate -i 跑完**才刪(Homebrew 官方 install.sh 同順序)。
      #   先刪再裝 ⇒ 系統不把開發者工具當可裝項目,逐字回「No such update」而 -i 仍回 0(the maintainer-a CI machine 實撞兩次)。
      #   10-04 a seat P1-3:標記若原本就在(別的安裝程式在用),這次不建也不刪。
      local clt_mark=/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
      if [ ! -e "$clt_mark" ]; then touch "$clt_mark" && _CLT_MARK="$clt_mark"; fi
      local label; label="$(softwareupdate -l 2>/dev/null | sed -n 's/^.*Label: \(Command Line Tools.*\)$/\1/p' | tail -n1)"
      local su_rc=0
      if [ -n "$label" ]; then
        tick_start; sudo softwareupdate -i "$label" --verbose; su_rc=$?; tick_stop
      fi
      [ -n "$_CLT_MARK" ] && rm -f "$_CLT_MARK"; _CLT_MARK=""
      if ! xcode-select -p >/dev/null 2>&1; then
        # 軟體更新找不到或裝不起來(公司管理的電腦常鎖軟體更新)⇒ 改跳 Apple 的安裝視窗,等使用者按完
        # 10-04 a seat P2-5:沒有本人登入的桌面就跳不出視窗,不白等
        if [ "$(stat -f%Su /dev/console 2>/dev/null)" != "$(id -un)" ]; then
          fail "Apple 開發者工具用軟體更新裝不起來,而這台目前沒有你登入的桌面畫面,沒辦法改跳安裝視窗" \
               "到這台 Mac 前面登入桌面,在終端機輸入 xcode-select --install 並按「安裝」,裝好後重跑同一行指令"; return 3
        fi
        say "… 改用 Apple 的安裝視窗:畫面會跳出「安裝命令列開發者工具」,請按「安裝」再按「同意」(這裡會等你,最多 45 分鐘)"
        if ! xcode-select --install >/dev/null 2>&1 && ! xcode-select -p >/dev/null 2>&1; then
          fail "Apple 的安裝視窗沒有跳出來" "在終端機輸入 xcode-select --install 按「安裝」,裝好後重跑同一行指令"; return 3
        fi
        tick_start; local waited=0
        while ! xcode-select -p >/dev/null 2>&1 && [ "$waited" -lt 2700 ]; do sleep 15; waited=$((waited+15)); done
        tick_stop
      fi
      xcode-select -p >/dev/null 2>&1 || { fail "Apple 開發者工具沒有裝上(軟體更新結果 rc=$su_rc)" "如果是公司管理的電腦,軟體更新可能被鎖,請問 IT;也可以在終端機輸入 xcode-select --install,按完視窗裝好後重跑"; return 3; }
    fi
  fi
  if [ "$clt_new" = 1 ]; then say "✓ Apple 開發者工具(花了 $(took "$t_step"))"; else say "✓ Apple 開發者工具(已裝)"; fi
  local py; py="$(command -v python3 || true)"
  if [ -z "$py" ] || ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    fail "找不到可用的 Python(需要 3.9 以上)" "在終端機輸入 xcode-select --install 裝好開發者工具後重跑"; return 3; fi
  command -v git >/dev/null || { fail "找不到 git" "在終端機輸入 xcode-select --install 裝好開發者工具後重跑"; return 3; }
  say "✓ macOS $(sw_vers -productVersion)、$("$py" -V 2>&1)、git"

  # 2. Homebrew、tmux
  if ! command -v brew >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/brew ]; then
    say "… [Homebrew] 裝 Homebrew(接下來的英文訊息與進度條都是 Homebrew 自己印的)"
    if [ "$DRY" = 0 ]; then
      # 10-04 a seat r2 P2-1:以前 curl 失敗時 bash -c "" 回 0 而印成功;改成先下載再執行,最後實跑 brew --version
      t_step=$(date +%s)
      fetch_run "${AIB_BREW_INSTALL_URL:-https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh}" /bin/bash 900 NONINTERACTIVE=1; local hb_rc=$?
      if [ "$hb_rc" = 0 ]; then
        if [ -x /opt/homebrew/bin/brew ]; then verify_cmd /opt/homebrew/bin/brew; hb_rc=$?; else hb_rc=127; fi
      fi
      [ "$hb_rc" != 6 ] || { fail "Homebrew 的官方安裝程式下載失敗" "確認網路後重跑同一行指令;公司網路請確認沒有擋住 raw.githubusercontent.com"; return 3; }
      [ "$hb_rc" = 0 ] || { fail "Homebrew 沒有裝上(rc=$hb_rc)" "多半是網路中斷;確認網路後重跑同一行指令"; return 3; }
      say "✓ Homebrew(花了 $(took "$t_step"))"
    fi
  fi
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  if ! grep -q 'brew shellenv' "$HOME/.zprofile" 2>/dev/null && [ -x /opt/homebrew/bin/brew ]; then
    run sh -c 'printf "\n# aib-bootstrap: Homebrew\neval \"\$(/opt/homebrew/bin/brew shellenv)\"\n" >> "$HOME/.zprofile"'
  fi
  if command -v tmux >/dev/null; then say "✓ tmux $(tmux -V)"
  elif command -v brew >/dev/null; then say "… [Homebrew] 裝 tmux"; t_step=$(date +%s)
    if run brew install tmux </dev/null; then say "✓ tmux(花了 $(took "$t_step"))"; else miss+=("tmux 沒裝上(可以稍後重跑同一行指令再試)"); fi
  else miss+=("tmux:Homebrew 沒裝起來")
  fi
  # 10-04 Mac mini 實機開席:macOS 沒有 flock,AIB 組裝座艙要它(flock -w / -n),缺了就開不了任何一席(rc=5)。
  #   FishMac/the maintainer MacBook 早就裝過所以一直沒人發現;總綱也記過「flock 是未宣告的 Homebrew 相依」。
  brew_dep() {   # brew_dep <套件> <白話名稱> <缺了會怎樣>
    if command -v "$1" >/dev/null 2>&1; then say "✓ $2(已裝)"; return 0; fi
    command -v brew >/dev/null 2>&1 || { miss+=("$2:Homebrew 沒裝起來,$3"); return 0; }
    say "… [Homebrew] 裝 $2"; local t0; t0=$(date +%s)
    tick_start; run_limited 900 brew install "$1" </dev/null; local r=$?; tick_stop
    if [ "$r" = 0 ] && command -v "$1" >/dev/null 2>&1; then say "✓ $2(花了 $(took "$t0"))"
    else say "✗ $2 沒裝上,$3"; miss+=("$2 沒裝上,$3(稍後重跑同一行指令再試)"); fi
  }
  if [ "$DRY" = 0 ]; then
    brew_dep flock "flock(防止同時改同一個檔的小工具)" "沒有它 Ez2 AIB 開不了席"
    local need_node=0
    ai_have codex && need_node=1
    case " $AI_PICK " in *" codex "*) need_node=1 ;; esac
    [ "$need_node" = 0 ] || brew_dep node "Node.js(Codex 席位顯示模型名稱用)" "Codex 席位會少顯示模型名稱,其他照常"
  fi
  [ "$SELF_URL" = "https://aib.ez2.app/install.sh" ] && note+=("bash:照已排定的第 3 步再升級,這裡不動")
  install_ai

  # 把官方安裝器拉下來。英文錯誤進暫存檔不給使用者看;404/410 說「還沒放到下載區」,不要說成網路壞了。
  download_signed_installer() {
    local url="$1" out="$2" err code
    err="$(mktemp "${TMPDIR:-/tmp}/aib-curl.XXXXXX")"; _TMP_FILES="$_TMP_FILES $err"
    code="$(curl -sL --proto '=https' --tlsv1.2 --max-time 120 -o "$out" -w "%{http_code}" -- "$url" 2>"$err")" || true
    case "$code" in
      200)
        [ -s "$out" ] || { fail "簽章套件安裝器下載失敗" "確認網路後重新執行同一行"; return 5; }
        return 0 ;;
      404|410)
        fail "Ez2 AIB 這版還沒放到下載區" "這不是你的網路問題。等放到 https://aib.ez2.app 之後再跑同一行,或聯絡 Ez2 AIB。"
        return 5 ;;
      *)
        fail "簽章套件安裝器下載失敗" "確認網路後重新執行同一行"
        return 5 ;;
    esac
  }

  # 3. 可選簽章套件(要 --package-base / --bundle-dir / AIB_PACKAGE_BASE)。沒指定就走下面公開 git。
  if [ -n "$PACKAGE_BASE" ] || [ -n "$PACKAGE_BUNDLE" ]; then
    say "… 下載並驗證 Ez2 AIB 正式版本"
    t_step=$(date +%s)
    if [ "$DRY" = 1 ]; then
      say "  (試跑)驗證簽章套件後原子切換版本"
    else
      if [ -z "$PACKAGE_INSTALLER" ]; then
        PACKAGE_INSTALLER="$(mktemp "${TMPDIR:-/tmp}/aib-package-installer.XXXXXX")"; _TMP_FILES="$_TMP_FILES $PACKAGE_INSTALLER"
        download_signed_installer "$PACKAGE_INSTALLER_URL" "$PACKAGE_INSTALLER" || return $?
        [ "$(shasum -a 256 "$PACKAGE_INSTALLER" | awk '{print $1}')" = "$PACKAGE_INSTALLER_SHA256" ] || {
          fail "簽章套件安裝器內容不符,已停止" "請勿繼續;把畫面截圖交給 Ez2 AIB 管理者"; return 7; }
      fi
      if [ -z "$ALLOWED_SIGNERS" ]; then
        ALLOWED_SIGNERS="$(mktemp "${TMPDIR:-/tmp}/aib-release-signers.XXXXXX")"; _TMP_FILES="$_TMP_FILES $ALLOWED_SIGNERS"
        ( umask 077; printf '%s\n' "$RELEASE_SIGNER" > "$ALLOWED_SIGNERS" )
      fi
      [ -f "$PACKAGE_INSTALLER" ] || { fail "找不到簽章套件安裝器" "請重新從 https://aib.ez2.app/install.sh 開始安裝"; return 5; }
      [ -f "$ALLOWED_SIGNERS" ] || { fail "找不到 Ez2 AIB 發版公鑰" "請重新從 https://aib.ez2.app/install.sh 開始安裝"; return 5; }
      if [ -n "$PACKAGE_BASE" ] && [ -z "${AIB_ACTIVATION_CODE:-}" ]; then
        have_tty || { fail "需要安裝碼,但目前沒有可以輸入的終端機視窗" "打開「終端機」App,重新貼上安裝指令"; return 6; }
        printf '請輸入 Ez2 AIB 安裝碼(輸入時不會顯示):' > /dev/tty
        IFS= read -r -s AIB_ACTIVATION_CODE < /dev/tty || { printf '\n' > /dev/tty; return 6; }
        printf '\n' > /dev/tty
        export AIB_ACTIVATION_CODE
      fi
      local pkg_args=(--allowed-signers "$ALLOWED_SIGNERS") pkg_rc
      if [ -n "$PACKAGE_BUNDLE" ]; then pkg_args+=(--bundle-dir "$PACKAGE_BUNDLE")
      else pkg_args+=(--base-url "$PACKAGE_BASE"); fi
      tick_start; run_limited 1800 bash "$PACKAGE_INSTALLER" "${pkg_args[@]}"; pkg_rc=$?; tick_stop
      case "$pkg_rc" in
        0) ;;
        7|8|9|10|11) fail "Ez2 AIB 套件安全驗證沒通過(rc=$pkg_rc)" "請勿繼續或重試;把畫面截圖交給 Ez2 AIB 管理者"; return "$pkg_rc" ;;
        22) fail "安裝碼不對,或下載來源目前不放行" "確認安裝碼後重跑;若仍失敗請聯絡 Ez2 AIB 管理者"; return "$pkg_rc" ;;
        124) fail "Ez2 AIB 套件下載超過 30 分鐘" "確認網路後重跑;舊版不會被覆蓋"; return "$pkg_rc" ;;
        *) fail "Ez2 AIB 正式版本下載或安裝失敗(rc=$pkg_rc)" "確認網路後重跑;舊版若存在不會被覆蓋"; return "$pkg_rc" ;;
      esac
    fi
    DIR="${AIB_PACKAGE_ROOT:-$HOME/.local/share/ez2-aib}/current/repo"
    run mkdir -p "$HOME/.ez2-aib"
    if [ "$DRY" = 0 ]; then
      AIB_PATH_LINK_ROOT="${AIB_PACKAGE_ROOT:-$HOME/.local/share/ez2-aib}/current/repo" \
        AIB_PATH_BIN="$HOME/.local/bin" bash "$DIR/tools/aib-install-path" || miss+=("aib-install-path 失敗")
      AIB_SKILLS_LINK_ROOT="${AIB_PACKAGE_ROOT:-$HOME/.local/share/ez2-aib}/current/repo" \
        bash "$DIR/tools/aib-install-skills" --all || miss+=("aib-install-skills 失敗")
      # 10-12 a seat:無頭 Chrome 收屍排程(每 5 分鐘;這一版只觀察、不砍,下一版驗過才改 --enforce)
      if [ -f "$DIR/tools/chrome-reaper/install.sh" ]; then
        bash "$DIR/tools/chrome-reaper/install.sh" --root "$DIR" >/dev/null || miss+=("無頭 Chrome 收屍排程沒裝上")
      fi
    fi
    if ! grep -q 'aib-bootstrap: PATH' "$HOME/.zprofile" 2>/dev/null; then
      run sh -c 'printf "\n# aib-bootstrap: PATH\nexport PATH=\"\$HOME/.local/bin:\$PATH\"\n" >> "$HOME/.zprofile"'
    fi
    if [ "$DRY" = 0 ]; then
      local pbin="$HOME/.local/bin/aib" pout prc pv=""
      if [ -x "$pbin" ]; then pout="$(cd "$DIR" && "$pbin" --version 2>&1)"; prc=$?; else pout=""; prc=127; fi
      [ "$prc" = 0 ] && pv="$(printf '%s\n' "$pout" | grep -m1 'Ez2 AIB')"
      [ -n "$pv" ] && say "✓ $pv(簽章已驗證,花了 $(took "$t_step"))" || miss+=("aib 指令裝好後跑不起來(rc=$prc)")
    fi
    say "== 完成(全部花了 $(took "$T_ALL"))。缺的:${#miss[@]} 項"
    local pm; for pm in "${miss[@]+"${miss[@]}"}"; do say "  ✗ $pm"; done
    if [ "$DRY" = 1 ]; then
      say "" "試跑完成:上面只顯示預計動作,沒有安裝 Ez2 AIB 或其他工具。"
      return 0
    fi
    [ "${#miss[@]}" -eq 0 ] && say "" "現在就能用:source ~/.zprofile && aib --version"
    [ "${#miss[@]}" -eq 0 ]; return $?
  fi

  fi   # LINUX=0:Mac 才有的步驟到這裡為止

  # 3b. 公開 git(預設 https://github.com/ez2app/ez2-aib.git)或管理者指定的 --source
  case "$SOURCE" in
    ssh://*|git@*)
      # 10-04 FishMac a seat:新機第一次連來源會停在英文的「Are you sure you want to continue connecting (yes/no)?」等人回答
      #   (乾淨虛擬機實撞)⇒ 第一次自動接受主機指紋(之後指紋若被換掉仍會擋),而且絕不停下來問。
      export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15 -o ForwardAgent=no -o ClearAllForwardings=yes"   # 10-11 ssh 加固(ACB 搬回 A3)
      if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
        say "… 這台沒有 ssh 金鑰,建一把 ~/.ssh/id_ed25519(不設密碼)"
        run mkdir -p "$HOME/.ssh"; run ssh-keygen -q -t ed25519 -N "" -C "ez2-bootstrap-$(hostname -s)" -f "$HOME/.ssh/id_ed25519"
      fi
      if [ "$DRY" = 1 ]; then say "  (試跑)git ls-remote $SOURCE"
      elif ! git ls-remote -q "$SOURCE" HEAD >/dev/null 2>&1; then
        fail "下載 Ez2 AIB 程式時連不上來源" "這台還沒被授權下載;請把下面這行交給 Ez2 AIB 管理者,授權後重跑同一行指令"
        [ -f "$HOME/.ssh/id_ed25519.pub" ] && say "  $(cat "$HOME/.ssh/id_ed25519.pub")"
        show_miss; return 4
      fi ;;
    https://*|http://*)
      export GIT_TERMINAL_PROMPT=0
      if [ "$DRY" = 1 ]; then say "  (試跑)git ls-remote $SOURCE"
      elif ! git ls-remote -q "$SOURCE" HEAD >/dev/null 2>&1; then
        fail "下載 Ez2 AIB 程式時連不上公開倉庫" "確認這台可連 github.com 後重跑同一行。倉庫若還沒公開,請等開源完成。"
        show_miss; return 4
      fi ;;
    "")
      fail "沒有指定要下載的來源" "重跑同一行,或加 --source <倉庫網址>"; return 2 ;;
  esac
  if [ "$DRY" = 1 ]; then say "  (試跑)來源 $SOURCE"; else say "✓ 來源倉庫可連"; fi

  # 4. 取倉庫。已存在就只快轉,不重 clone;有本機改動不覆蓋。
  repo_ver() {
    if git -C "$1" cat-file -e HEAD:VERSION 2>/dev/null; then
      git -C "$1" show HEAD:VERSION | head -n 1
    elif git -C "$1" cat-file -e HEAD:aib/VERSION 2>/dev/null; then   # 公開版:版號在套件裡
      git -C "$1" show HEAD:aib/VERSION | head -n 1
    else
      git -C "$1" rev-parse --short HEAD
    fi
  }
  # 10-11(the maintainer「照你的建議」):公開版裝「最新的正式版本標籤」(v*),不是 main 最新 ⇒ PR 合併後要等維護者核准發版,
  #   一行安裝的人才拿得到,跟 PyPI 同一版。來源沒有 v* 標籤(自架鏡像、測試)或私有來源 ⇒ 照舊用 main。
  release_ref() {
    local tag=""
    if [ "$SELF_URL" != "https://aib.ez2.app/install.sh" ]; then
      tag="$(git -C "$1" tag -l 'v[0-9]*' --sort=-v:refname 2>/dev/null | head -n 1)"
    fi
    if [ -n "$tag" ]; then printf '%s' "$tag"; else printf '%s' "origin/main"; fi
  }
  t_step=$(date +%s)
  same_source || { show_miss; return 2; }
  if [ -d "$DIR/.git" ]; then
    say "… 已有 $DIR,只快轉到來源最新"
    if [ "$DRY" = 1 ]; then
      say "  (試跑)git fetch && 沒有本機改動才 git merge --ff-only"
    elif ! git -C "$DIR" fetch -q --tags origin; then
      miss+=("快轉失敗(連不上來源),請稍後重跑同一行")
    else
      local dirty before after ver
      # 只看追蹤中的檔(10-09 a seat 用真公開版實測:跑過一次 aib 就多出 aib/__pycache__/,被當成本機改動、從此更新不了)。
      #   沒追蹤的檔快轉本來就不會蓋;真的會撞到時 git merge --ff-only 自己會拒絕。
      dirty="$(git -C "$DIR" status --porcelain --untracked-files=no)"
      before="$(git -C "$DIR" rev-parse HEAD)"
      local want; want="$(release_ref "$DIR")"
      after="$(git -C "$DIR" rev-parse "$want^{commit}" 2>/dev/null || true)"
      if [ -n "$dirty" ]; then
        say "✗ 這份安裝裡有你改過、還沒提交的檔,沒有覆蓋"
        say "  → 怎麼辦:先把改動從安裝目錄移走或另外存好,再重跑同一行。重跑只會快轉,不會整份重抓,也不會蓋掉你的檔。"
        miss+=("有本機改動,沒有覆蓋")
      elif [ -z "$after" ]; then
        miss+=("快轉失敗(來源沒有 main)")
      elif [ "$before" = "$after" ] || git -C "$DIR" merge-base --is-ancestor "$after" "$before" 2>/dev/null; then
        # 已在這版、或比最新正式版還新(以前從 main 裝的)⇒ 不往回退
        ver="$(repo_ver "$DIR")"
        say "✓ 已是最新($ver)"
      elif git -C "$DIR" merge -q --ff-only "$after"; then
        ver="$(repo_ver "$DIR")"
        say "✓ 已更新到 $ver($(git -C "$DIR" rev-parse --short "$before") → $(git -C "$DIR" rev-parse --short HEAD))"   # 版號沒變也看得出有更新
      else
        miss+=("快轉失敗(歷史分叉,不能快轉),請手動看")
      fi
    fi
  else
    run env GIT_TERMINAL_PROMPT=0 git clone -q "$SOURCE" "$DIR" || { fail "下載 aib 程式失敗" "確認這台可連 github.com 後重跑同一行"; show_miss; return 5; }
    if [ "$DRY" = 0 ]; then
      local first; first="$(release_ref "$DIR")"
      # 剛 clone、還沒有任何本機改動 ⇒ 直接停在最新正式版
      [ "$first" = "origin/main" ] || git -C "$DIR" reset -q --hard "$first^{commit}" || miss+=("切到正式版 $first 失敗")
    fi
  fi
  run mkdir -p "$HOME/.ez2-aib"

  # 5. 放進路徑、裝技能
  # 10-03 FishMac a seat:要帶 --launcher,aib 這支包裝檔才會寫進 ~/.local/bin(不帶只裝周邊指令,新機會沒有 aib)
  local PUB=0
  if [ ! -e "$DIR/tools/aib-install-path" ] && { [ -f "$DIR/bin/aib" ] || [ "$DRY" = 1 ]; }; then
    # 10-09 a seat 公開版(github.com/ez2app/ez2-aib 只有 aib 核心套件,沒有 aib-install-path):
    #   ~/.local/bin/aib 直接連到 checkout 的 bin/aib(README「免安裝」那條路)。更新 = 上面第 4 步的快轉,連結不用動。
    #   那個位置已經是別的 aib(開發機的包裝檔、pipx 裝的…)⇒ 不蓋,說怎麼辦。
    PUB=1
    local link="$HOME/.local/bin/aib" cur="" KEEP=0
    [ -L "$link" ] && cur="$(readlink "$link")"
    if [ -e "$link" ] || [ -L "$link" ]; then
      # 10-11 the maintainer FishMac 實裝:那裡是正在用的 aib ⇒ 不蓋,也不叫人自己蓋(蓋了就換掉正在用的駕駛艙);不算「缺」。
      if [ "$cur" != "$DIR/bin/aib" ]; then
        KEEP=1
        note+=("這台已經有另一個 aib($link),沒有動它:你原本的 aib 照常可用" \
               "公開版放在 $DIR,要看說明直接跑:$DIR/bin/aib --help" \
               "兩個 aib 共用 ~/.ez2-aib(席位、記憶);要真的試公開版的駕駛艙,請用另一個使用者帳號")
      fi
    else
      run mkdir -p "$HOME/.local/bin"
      run ln -s "$DIR/bin/aib" "$link" || miss+=("沒能建立 $link")
    fi
  else
    run bash "$DIR/tools/aib-install-path" --launcher || miss+=("aib-install-path 失敗")
    run bash "$DIR/tools/aib-install-skills" --all || miss+=("aib-install-skills 失敗")
    # 10-12 a seat:無頭 Chrome 收屍排程(Mac 才有 launchd;這一版只觀察、不砍,下一版驗過才改 --enforce)
    if [ "$LINUX" != 1 ] && { [ -f "$DIR/tools/chrome-reaper/install.sh" ] || [ "$DRY" = 1 ]; }; then
      run bash "$DIR/tools/chrome-reaper/install.sh" --root "$DIR" || miss+=("無頭 Chrome 收屍排程沒裝上")
    fi
  fi
  local PROFILE="$HOME/.zprofile"
  [ "$LINUX" = 1 ] && PROFILE="$HOME/.profile"   # Linux 多半是 bash:登入殼讀 ~/.profile
  if ! grep -q 'aib-bootstrap: PATH' "$PROFILE" 2>/dev/null; then
    run sh -c 'printf "\n# aib-bootstrap: PATH\nexport PATH=\"\$HOME/.local/bin:\$PATH\"\n" >> "$1"' sh "$PROFILE"
  fi

  # 6. 自檢
  if [ "$DRY" = 0 ]; then
    # 10-03 FishMac a seat:自檢要走**路徑上**的 aib(開新視窗後使用者打的就是它);直跑倉庫那支,aib 沒進路徑也會過。
    #   直接執行 ~/.local/bin/aib(不靠 PATH 順序,免得別處另一支 aib 冒充);結束碼與輸出分開看(不經管線,rc 不被吃掉)。
    local abin="$HOME/.local/bin/aib" vout vrc v=""
    [ "${KEEP:-0}" = 1 ] && abin="$DIR/bin/aib"   # 路徑上那支是別的 aib ⇒ 驗這份公開版本身
    local shown="$abin"; case "$abin" in "$HOME"/*) shown="~/${abin#"$HOME"/}" ;; esac
    if [ "$PUB" = 1 ]; then
      # 公開版:aib --help 印用法、結束碼 2(核心套件的約定);有「aib test」那行才算跑得起來
      if [ -x "$abin" ]; then vout="$(cd "$DIR" && "$abin" --help 2>&1)"; vrc=$?; else vout=""; vrc=127; fi
      if [ "$vrc" = 2 ] && printf '%s\n' "$vout" | grep -q 'aib test'; then vrc=0; v="aib(公開版 $(repo_ver "$DIR"))"; fi
    else
      if [ -x "$abin" ]; then vout="$(cd "$DIR" && "$abin" --version 2>&1)"; vrc=$?; else vout=""; vrc=127; fi
      [ "$vrc" = 0 ] && v="$(printf '%s\n' "$vout" | grep -m1 'Ez2 AIB')"
    fi
    if [ -n "$v" ]; then say "✓ $v($shown,花了 $(took "$t_step"))"
      # 10-12 新用戶全面檢查(the maintainer「新安裝的 aib 也要解決這個問題」):裝完就建好狀態樹(aib init,可重跑);
      #   第二大腦的預設 vault 與預設路由契約在第一次用時自動就緒。已有別的 aib(共用 ~/.ez2-aib)⇒ 不碰。
      if [ "${KEEP:-0}" != 1 ]; then
        # 10-15(維護者「安裝 / 升級時就裝,不必等開席」):這台有裝的 AI CLI 都裝上 CTX 用量表的 hook(沒裝的略過並說明;
        #   各家設定先備份;AIB_AUTO_DISTILL=0 全關;Codex 印一行「/hooks 信任一次」)。放在建狀態目錄那一步前面:那一步的輸出被吃掉。
        #   已有別的 aib(KEEP=1)⇒ 不碰(會把那支 aib 的 hook 換成這份的路徑)。
        for _adh in "$DIR/aib/src/identity/auto_distill_hook.py" "$DIR/src/identity/auto_distill_hook.py"; do
          if [ -f "$_adh" ]; then python3 "$_adh" ensure-all </dev/null || true; break; fi
        done
        # 10-17(the maintainer「以後都應該自動更新」):狀態列腳本跟著換新 —— 只換 aib 裝的舊版、你改過的不動(印一行手動換法)、
        #   先備份、沒在用的那家不建;AIB_STATUSLINE_AUTO=0 全關。失敗只記一筆,不擋安裝 / 升級。
        for _sli in "$DIR/aib/tools/statusline-install.sh" "$DIR/tools/statusline-install.sh"; do
          if [ -f "$_sli" ]; then
            local sl_out
            if sl_out="$(bash "$_sli" --auto </dev/null 2>&1)"; then [ -z "$sl_out" ] || say "$sl_out"
            else [ -z "$sl_out" ] || say "$sl_out"; note+=("狀態列沒自動換成:之後可以自己跑 bash $_sli"); fi
            break
          fi
        done
        if (cd "$DIR" && "$abin" init >/dev/null 2>&1); then say "✓ aib 狀態目錄就緒(~/.ez2-aib)"
        else note+=("aib init 沒跑成:開第一席前請自己跑一次 aib init"); fi
        # 10-15(the maintainer「裝上並在下一版自動安裝」):附帶的免費、已簽章 mod(extensions/)裝好並登記到 Claude Code;
        #   aib update 也是重跑這支,一起涵蓋。AIB_EXT_AUTO=0 全關。裝不成只記一筆,不擋安裝。
        local ext_out
        if ext_out="$(cd "$DIR" && "$abin" ext auto 2>&1)"; then [ -z "$ext_out" ] || say "$ext_out"
        else [ -z "$ext_out" ] || say "$ext_out"; note+=("附帶的 mod 沒裝成:之後可以自己跑 aib ext auto"); fi
        # 10-16(維護者「安裝時就裝好 Nostr 的程式」):全球對談先備好 —— venv + nostr-sdk(要網路,約 1–3 分鐘)與主人金鑰;
        #   只在本機,不上線(要上線:aib chat on --name … --country …)。失敗不擋安裝;AIB_CHAT_PREINSTALL=0 略過。
        if [ "${AIB_CHAT_PREINSTALL:-1}" != 0 ]; then
          say "… 線上對話先備好(裝 nostr-sdk,約 1–3 分鐘;不會上線)"
          if (cd "$DIR" && "$abin" chat prepare --quiet </dev/null >/dev/null 2>&1) && (cd "$DIR" && "$abin" chat status --json 2>/dev/null | grep -q '"venv": "ok"'); then
            say "✓ 線上對話已備好(要開通:aib chat on --name \"<名稱>\" --country TW)"
          else note+=("線上對話的 nostr-sdk 沒裝成(多半是網路):之後跑 aib chat prepare 再補"); fi
        fi
      fi
    elif [ "$vrc" = 127 ]; then miss+=("$shown 不見了或不能執行")
    else miss+=("$shown 跑不起來(結束碼 $vrc)"); fi
  fi
  say "== 完成(全部花了 $(took "$T_ALL"))。缺的:${#miss[@]} 項"
  for m in "${miss[@]+"${miss[@]}"}"; do say "  ✗ $m"; done
  for m in "${note[@]+"${note[@]}"}"; do say "  · $m"; done
  if [ "$LINUX" = 1 ]; then
    say "  · Linux:目前只有測試安全網(aib test、aib ship);駕駛艙與 AI 席位需要 macOS"
  elif [ -n "$AI_LOGIN" ]; then
    say "  · 各家 AI 要各自登入一次(在終端機輸入):"
    local pair; local IFS_OLD="$IFS"; IFS='|'
    for pair in ${AI_LOGIN#|}; do say "      ${pair%%:*}:${pair#*:}"; done
    IFS="$IFS_OLD"
  else
    say "  · 之後要加裝 AI 工具:curl -fsSL $SELF_URL | bash -s -- --tools"
  fi
  [ "$LINUX" = 1 ] || say "  · 登入後用 aib <provider> safe <role> 開席"
  if [ "$DRY" = 1 ]; then
    say "" "試跑完成:上面只顯示預計動作,沒有安裝 Ez2 AIB 或其他工具。"
  elif [ "${#miss[@]}" -eq 0 ] && [ "${KEEP:-0}" = 1 ]; then
    say "" "公開版裝好了(和你原本的 aib 並存,沒有取代它)。"
  elif [ "${#miss[@]}" -eq 0 ]; then
    if [ "$LINUX" = 1 ]; then say "" "現在就能用:在這個視窗輸入下面這行(之後開新視窗就不用再打):" "  . ~/.profile && aib --help"
    else say "" "現在就能用:在這個視窗輸入下面這行(之後開新視窗就不用再打):" "  source ~/.zprofile && aib --version"; fi
  fi
  [ "${#miss[@]}" -eq 0 ]
}
main "$@"
