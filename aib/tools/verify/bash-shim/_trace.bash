# SPDX-License-Identifier: Apache-2.0
# 第 96b 項:bash 子行程的追蹤(由 BASH_ENV 載入;非互動 bash 啟動時 source 這支)。
#
# 為什麼要有它:live40 40 列 measured 裡 11 列 `bash ./src/cockpit/*.sh`,那支 .sh 與它 source 的檔都不在鑰匙裡,
# 改了照樣被沿用。python 子行程有 sitecustomize,bash 以前什麼都沒有(child-untracked)。
#
# 兩個通道,各記各的:
#   1. xtrace(set -x + BASH_XTRACEFD 指到 trace 檔):每個**展開後**的命令一行,前綴 +EZ2BT␟src␟line␟pwd␟pid␟。
#      看得到 `. /full/path/lib.sh`、`git -C /tmp/x status`、`[ -f path ]`——分析端拿它分類子命令、找 source 與存在探測。
#   2. DEBUG trap(看的是**展開前**的文字):xtrace 看不到的讀取——`$(<file)`、`cmd < file`——在讀的當下解析路徑並雜湊;
#      解析不了(含 $( 、反引號、函式內的 $1)⇒ 記 unmeasured(保守側:量不到就真跑)。
#   DEBUG trap 同時看 BASH_SOURCE[1](正在執行程式碼的檔:主腳本、被 source 的檔、被呼叫的函式所在的檔)第一次出現就雜湊;
#   `. path` 命令的文字能安全展開(含 $(dirname "$0") 慣用語)的也當場雜湊——只有函式定義的檔不會有 DEBUG 事件落在它裡面。
#   EXIT trap:雜湊主腳本、記 exit(完成證據)。腳本自己蓋掉 EXIT trap ⇒ 沒 exit ⇒ 分析端判 unmeasured。
#   ★ 這支檔不准起任何非必要的外部命令(唯一的是每個新檔一次 shasum):tests/test-panel-load-at.py 在數 exec。
#
# 紀律:trap 一律回 0、不開 extdebug(它會讓 DEBUG 回非零時跳過命令、還會開 -E 改變 ERR trap 語意);
# 只用 ${!name} 做**參數展開**,絕不 eval;set -u 下每個變數都帶 :-;不動腳本的 IFS/選項(functrace 除外——沒有它函式體與子殼不追)。
# 腳本 `set +x` 之後看不見 ⇒ 記一次 unmeasured bash-xtrace-off,不偷偷再開(它可能是在藏秘密)。

# 只在追蹤模式、且不是墊片自己(墊片行程 exec 成真 bash 時 $0 會換掉,那時才載)時生效
if [ -z "${EZ2_VERIFY_TRACE:-}" ] || [ -n "${_EZ2_BT_LOADED:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
case "$0" in */bash-shim/*) return 0 2>/dev/null || exit 0 ;; esac
_EZ2_BT_LOADED=1
# macOS 的 /bin/bash 是 3.2:沒有 BASH_XTRACEFD(4.1 起),set -x 只能吐到 stderr——那是測試在看的東西,不准碰。
# ⇒ 3.2 只記 inherit/exit 與一條 unmeasured,整列量不到(#!/bin/bash 直接執行的腳本、tests 裡寫死 /bin/bash 的都是這一類)。
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ] || { [ "${BASH_VERSINFO[0]:-0}" -eq 4 ] && [ "${BASH_VERSINFO[1]:-0}" -lt 1 ]; }; then
  printf '{"ch":"inherit","pid":%s,"via":"bash","argv0":"%s"}\n' "$$" "${0//\"/\\\"}" >> "$EZ2_VERIFY_TRACE"
  printf '{"ch":"unmeasured","why":"bash-version-no-xtrace:%s","pid":%s}\n' "${BASH_VERSION%%(*}" "$$" >> "$EZ2_VERIFY_TRACE"
  trap 'printf "{\"ch\":\"exit\",\"pid\":%s,\"via\":\"bash\"}\n" "$$" >> "$EZ2_VERIFY_TRACE"' EXIT
  return 0 2>/dev/null || exit 0
fi
_EZ2_BT_SEP=$'\x1f'
_EZ2_BT_ARGS=("$@")          # 腳本的位置參數快照:頂層的 $1..$9 用它展開;函式裡的 $1 不展開(量不到)
declare -A _EZ2_BT_SEEN=()
declare -A _EZ2_BT_SRC_SEEN=()
# 正規式放變數:寫在 [[ ]] 裡的 \< 在 macOS 的 regcomp 是字界不是字面
_EZ2_BT_RE_SUBST='\$\(<[[:space:]]*([^)]+)\)'                 # $(<file)
_EZ2_BT_RE_REDIR='(^|[^<])<[[:space:]]*([^[:space:]<(&;|]+)'    # cmd < file(排掉 <<、<(、<&、<<<)
_EZ2_BT_RE_VAR='^([^$]*)\$(\{?)([A-Za-z_][A-Za-z0-9_]*|[0-9])(\}?)(.*)$'   # $name / ${name} / $N
# T0-2:雜湊器一次選定。macOS 的 shasum 是 perl 腳本(實測每次 13.6 ms),sha256sum(/sbin,C)3.4 ms;每個新檔、每個 bash 行程
# 各一次 exec,選慢的那個會讓多子行程的面板每支多幾十毫秒。command -v 是內建,不起行程。輸出格式兩者同(hash␠␠path)。
_EZ2_BT_SHA=shasum
command -v sha256sum >/dev/null 2>&1 && _EZ2_BT_SHA=sha256sum

_ez2_bt_json() {   # 只做最小的 JSON 字串跳脫
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\t'/\\t}
  printf '%s' "$s"
}
_ez2_bt_emit() { printf '%s\n' "$1" >> "$EZ2_VERIFY_TRACE" 2>/dev/null || :; }   # 寫不進去就算了:鉤子的失敗不准變成腳本的失敗
_ez2_bt_unmeasured() { _ez2_bt_emit "{\"ch\":\"unmeasured\",\"why\":\"$(_ez2_bt_json "$1")\",\"pid\":$$}"; }

_ez2_bt_open() {   # 記一個讀到的檔的內容(每個行程每條路徑一次);目錄不記;不存在記 missing
  local p=$1 ap
  [ -n "$p" ] || return 0
  case $p in /*) ap=$p ;; *) ap=$PWD/$p ;; esac
  ap=${ap//\/.\//\/}
  [ -n "${_EZ2_BT_SEEN[$ap]:-}" ] && return 0
  _EZ2_BT_SEEN[$ap]=1
  if [ -f "$ap" ]; then
    local h
    if [ "$_EZ2_BT_SHA" = sha256sum ]; then h=$(sha256sum -- "$ap" 2>/dev/null) || h=""; else h=$(shasum -a 256 -- "$ap" 2>/dev/null) || h=""; fi
    h=${h%% *}
    if [ -n "$h" ]; then
      # 10-03 產物規則(bash 側):比「起跑標記檔」新 ⇒ 這次跑測試期間才寫出來的(test -nt 是內建,不開行程)。
      #   標記檔由測試行程起來時建(sitecustomize_audit._emit_t0);沒有標記檔 ⇒ 不記 born(分析端照舊當輸入)。
      local _b=""
      if [ -e "$EZ2_VERIFY_TRACE.t0" ] && [ "$ap" -nt "$EZ2_VERIFY_TRACE.t0" ]; then _b=",\"born\":true"; fi
      _ez2_bt_emit "{\"ch\":\"open\",\"path\":\"$(_ez2_bt_json "$ap")\",\"sha256\":\"$h\",\"pid\":$$,\"via\":\"bash\"$_b}"
    else
      _ez2_bt_unmeasured "bash-read-unhashable:$ap"
    fi
  elif [ ! -e "$ap" ]; then
    _ez2_bt_emit "{\"ch\":\"missing\",\"path\":\"$(_ez2_bt_json "$ap")\",\"pid\":$$,\"via\":\"bash\"}"
  fi
}

_ez2_bt_expand() {   # 安全展開:只換 $name / ${name} / 頂層的 $N;含 $( 、反引號、其他形 ⇒ 失敗(回 1)。
  # 結果放 _EZ2_BT_OUT(不用 $(…):子殼裡 BASH_REMATCH 對不上、又會被 xtrace 記一大串)。
  local t=$1 out="" name val
  _EZ2_BT_OUT=""
  # 常見慣用語先換成可展開的形:$(dirname "$0")、$(cd "$(dirname "$0")" && pwd)、${0%/*}、${BASH_SOURCE[0]%/*}
  local d0=${0%/*}
  [ "$d0" = "$0" ] && d0=.
  t=${t//'$(cd "$(dirname "$0")" && pwd)'/$d0}
  t=${t//'$(dirname "$0")'/$d0}
  t=${t//'$(dirname "${BASH_SOURCE[0]}")'/$d0}
  t=${t//'${0%/*}'/$d0}
  t=${t//'${BASH_SOURCE[0]%/*}'/$d0}
  case $t in *'$('*|*'`'*) return 1 ;; esac
  local rest
  while [[ $t =~ $_EZ2_BT_RE_VAR ]]; do
    # 群組先抄出來:下一個 [[ =~ ]] 會把 BASH_REMATCH 清掉(這裡踩過:讀到的 $D/data.txt 變成 .)
    out+=${BASH_REMATCH[1]:-}
    name=${BASH_REMATCH[3]:-}
    rest=${BASH_REMATCH[5]:-}
    case $name in
      [0-9])
        [ "${FUNCNAME[2]:-main}" = "main" ] || return 1     # 函式裡的 $1 是函式的參數,快照對不上
        if [ "$name" = 0 ]; then val=$0; else val=${_EZ2_BT_ARGS[$((name-1))]:-}; fi ;;
      *) val=${!name:-} ;;
    esac
    out+=$val
    t=$rest
  done
  case $t in *'$'*) return 1 ;; esac
  out+=$t
  out=${out//\"/}; out=${out//\'/}
  _EZ2_BT_OUT=$out
  return 0
}

_ez2_bt_strip_heredocs() {   # BASH_COMMAND 會把 heredoc 本文一起帶進來;本文是寫死在腳本裡的文字(腳本檔已整份雜湊),不是讀檔
  # ★ 09-29 a seat 量到:aib-release.sh 裡 `python3 - <<'PYEXP'` 的本文有 Python 註解 `<UTC>  <detail>`,
  #   下面的 `cmd < file` 正規式把它當成轉向 ⇒ bash-read-unparsed ⇒ 整列量不到(B8 58 支裡 11 支帶這個原因)。
  # 結果放 _EZ2_BT_OUT;不用 [[ =~ ]](不碰被追腳本的 BASH_REMATCH)、不起外部命令。
  # 安全側兩條:(1) `((` 之後的 `<<` 是算術左移,不當 heredoc;(2) 本文沒找到結束行 ⇒ 不剝,照原文掃
  #   (剝錯 = 後面幾行被丟掉 = 真的讀取漏記 = 錯誤沿用;照原文最多多判一次量不到)。
  # ★ 09-29 13:0x a seat 實量退步:逐行切 rest 是平方成本 —— pane-right-ai.sh 那段 77KB 的內嵌 Python 一次 5 秒(3.2 要 7 秒),
  #   DEBUG trap 每執行一次那個指令就算一次 ⇒ test-pane-right-ai 從 116 秒變成逾時。兩道:
  #   (1) 超過 _EZ2_BT_STRIP_MAX 位元組不剝(回 1,呼叫端照原文掃 = main 的舊行為,最多多判一次量不到);
  #   (2) 同一段原文重複進來 ⇒ 直接給上次的結果(比字串是線性的)。
  if [ ${#1} -gt ${_EZ2_BT_STRIP_MAX:-8192} ]; then _EZ2_BT_OUT=$1; return 1; fi
  if [ -n "${_EZ2_BT_STRIP_LASTIN+x}" ] && [ "$1" = "$_EZ2_BT_STRIP_LASTIN" ]; then
    _EZ2_BT_OUT=$_EZ2_BT_STRIP_LASTOUT; return $_EZ2_BT_STRIP_LASTRC
  fi
  _ez2_bt_strip_heredocs_raw "$1"; local _rc=$?
  _EZ2_BT_STRIP_LASTIN=$1; _EZ2_BT_STRIP_LASTOUT=$_EZ2_BT_OUT; _EZ2_BT_STRIP_LASTRC=$_rc
  return $_rc
}

_ez2_bt_strip_heredocs_raw() {
  local rest=$1 line out="" t d pre
  local -a delims=()
  while :; do
    case $rest in *$'\n'*) line=${rest%%$'\n'*}; rest=${rest#*$'\n'} ;; *) line=$rest; rest="" ;; esac
    if [ ${#delims[@]} -gt 0 ]; then
      t=$line
      while [ "${t#$'\t'}" != "$t" ]; do t=${t#$'\t'}; done       # <<- 允許行首 tab
      [ "$t" = "${delims[0]}" ] && delims=("${delims[@]:1}")
    else
      out+=$line$'\n'
      t=$line
      while :; do
        case $t in *'<<'*) ;; *) break ;; esac
        pre=${t%%'<<'*}
        t=${t#*'<<'}
        case $t in '<'*) t=${t#'<'}; continue ;; esac                # <<< 是 here-string,不是 heredoc
        case $pre in *'(('*) continue ;; esac                        # $((1<<n)) 算術左移
        t=${t#-}
        while [ "${t# }" != "$t" ]; do t=${t# }; done
        d=${t%%[[:space:]\;\|\&\<\>\)]*}
        d=${d//\'/}; d=${d//\"/}; d=${d//\\/}
        case $d in ''|*[!A-Za-z0-9_]*) continue ;; esac               # 分隔字不是單字 ⇒ 不當 heredoc
        delims+=("$d")
      done
    fi
    [ -n "$rest" ] || break
  done
  if [ ${#delims[@]} -gt 0 ]; then _EZ2_BT_OUT=$1; return 1; fi     # 沒找到結束行 ⇒ 不剝
  _EZ2_BT_OUT=${out%$'\n'}
  return 0
}

_ez2_bt_xoff() {   # 腳本自己 set +x 之後我們看不見:記一次 unmeasured,不偷偷再開(它可能是在藏秘密)
  [ -n "${_EZ2_BT_XOFF:-}" ] && return 0
  [ -n "${_EZ2_BT_ARMED:-}" ] || return 0
  [ -n "${_EZ2_BT_EXITING:-}" ] && return 0
  _EZ2_BT_XOFF=1; _ez2_bt_unmeasured "bash-xtrace-off"
  return 0
}

_ez2_bt_debug() {
  # ★ T0-2(09-14):只有 trap 字串的 case 挑出來的命令才會進到這裡(見檔尾 trap 那行):含 `<` 的、`. `/`source `、`exec `、
  #   或「這個檔第一次有命令執行」。其餘命令(絕大多數:賦值、[ ]、case、read -t、printf)在 trap 字串裡就結束,
  #   不呼叫函式、不切 xtrace、不跑正規式。實測(3000 圈 ×7 命令的迴圈):舊寫法 50×,只加快路徑仍 36×——瓶頸不在正規式,
  #   在「每個命令呼叫一次函式 + set +x/-x + 兩行 xtrace 噪音」;trap 字串只留 case 之後落在 xtrace 自己的地板附近。
  # 進來時 xtrace 還開著:呼叫這支的那行、下面的 _EZ2_BT_XON=? 與 set +x 會進 xtrace(分析端 BASH_XT_NOISE 濾掉)。
  case $- in *x*) _EZ2_BT_XON=1;; *) _EZ2_BT_XON=0;; esac
  set +x
  [ -n "${_EZ2_BT_EXITING:-}" ] && return 0
  # ★ 09-14 09:2x 艦長量到 test-theme / test-eye-ui-launch「沒追蹤器 rc=0、開追蹤器 rc=1」:根因是這個處理器自己的
  #   [[ =~ ]] 把**被追腳本的 BASH_REMATCH 蓋掉**——DEBUG trap 落在腳本的 `[[ x =~ re ]]` 與它下一行 `${BASH_REMATCH[1]}`
  #   之間,腳本 set -u 就死在「未綁定的變數」(theme-ctl.sh:145)。而且每個命令都會觸發這裡的正規式,不只含 < 的。
  #   觀測者不得改變被觀測物:進來先存、出去前還原(bash ≥5.1 可賦值;3.2 走不到這裡)。刀:B12。
  local _xon=${_EZ2_BT_XON:-1}
  local cmd=$BASH_COMMAND m
  # 10-03:上一個命令是 trap(可能蓋掉我們的 EXIT)⇒ 現在它已經跑完,把結束紀錄接回它的收尾動作後面
  [ -n "${_EZ2_BT_TC:-}" ] && { _EZ2_BT_TC=; _ez2_bt_rearm_exit; }
  case $cmd in "trap "*|"builtin trap "*) [ "${BASHPID:-$$}" = "$$" ] && _EZ2_BT_TC=T ;; esac
  # ★ `|| :` 必要(09-29 17:4x 兩臂 C 抓到):剝除函式在「超過上限/找不到結束行」時回 1(意思是「沒剝」),
  #   被追的腳本若 set -e,trap 裡這個非零會讓整支腳本靜靜 rc=1 結束(pane-pending-review.sh、mouse-ctl.sh 實例)。
  #   觀測者不得改變被觀測物:回傳值只給我們自己看,一律吞掉。
  case $cmd in *'<<'*$'\n'*) _ez2_bt_strip_heredocs "$cmd" || :; cmd=$_EZ2_BT_OUT ;; esac
  # 10-04(共用驗收 bash A8 抓到):/dev/tcp、/dev/udp 是 bash 自己開的連線,不經過任何子行程,xtrace 也不印重導向 ⇒
  #   只有這裡(BASH_COMMAND 原文)看得到。看到就報外部(寬:字面出現就算,寧可多跑)。
  case $cmd in *"/dev/tcp/"*|*"/dev/udp/"*) _ez2_bt_emit "{\"ch\":\"external\",\"why\":\"network\",\"pid\":$$,\"via\":\"bash\"}" ;; esac
  # exec 成別的程式:EXIT trap 不會再跑,完成證據在這裡先記(exec 只做重導向的形不算)
  case $cmd in
    "exec "*) case $cmd in "exec "[0-9\{\<\>\&]*) ;; *) _ez2_bt_emit "{\"ch\":\"exit\",\"pid\":$$,\"via\":\"bash\",\"exec\":true}" ;; esac ;;
  esac
  # 正在執行程式碼的檔(主腳本、被 source 的檔、被呼叫的函式所在的檔):第一次看到就雜湊
  # 鍵與 trap 字串那個 case 用同一個:沒有 BASH_SOURCE(bash -c)時用 `/` 佔位,否則 -c 的每個命令都會走到這裡(慢路徑)
  local src=${BASH_SOURCE[1]:-}
  if [ -z "${_EZ2_BT_SRC_SEEN[${src:-/}]:-}" ]; then
    _EZ2_BT_SRC_SEEN[${src:-/}]=1
    [ -n "$src" ] && [ -f "$src" ] && _ez2_bt_open "$src"
  fi
  # 存還 BASH_REMATCH(fb09e650:處理器自己的 [[ =~ ]] 會蓋掉被追腳本的;刀 B12)
  local -a _ez2_rm=()
  [ "${#BASH_REMATCH[@]}" -gt 0 ] 2>/dev/null && _ez2_rm=("${BASH_REMATCH[@]}")
  # source / . 的檔:只有函式定義的檔不會有任何 DEBUG 事件落在它裡面,所以在這裡就照文字展開、雜湊
  case $cmd in
    ". "*|"source "*)
      m=${cmd#. }; m=${m#source }; m=${m%% *}
      if _ez2_bt_expand "$m"; then _ez2_bt_open "$_EZ2_BT_OUT"; fi ;;   # 展不開的靠 BASH_SOURCE 那條(函式被呼叫時會看到)或分析端判 unhashed
  esac
  # $(<file):xtrace 只印得出結果值,這裡在讀的當下把檔記下來
  while [[ $cmd =~ $_EZ2_BT_RE_SUBST ]]; do
    m=${BASH_REMATCH[0]}
    if _ez2_bt_expand "${BASH_REMATCH[1]:-}"; then _ez2_bt_open "$_EZ2_BT_OUT"; else _ez2_bt_unmeasured "bash-read-unparsed:$m"; fi
    cmd=${cmd/"$m"/}
  done
  # cmd < file(不是 <<、<(、<&、<<<)
  while [[ $cmd =~ $_EZ2_BT_RE_REDIR ]]; do
    m=${BASH_REMATCH[0]}
    if _ez2_bt_expand "${BASH_REMATCH[2]:-}"; then _ez2_bt_open "$_EZ2_BT_OUT"; else _ez2_bt_unmeasured "bash-read-unparsed:<${BASH_REMATCH[2]:-}"; fi
    cmd=${cmd/"$m"/}
  done
  [ "${#_ez2_rm[@]}" -gt 0 ] && { BASH_REMATCH=("${_ez2_rm[@]}") 2>/dev/null || :; }
  [ "$_xon" = 1 ] && set -x
  return 0
}

# 10-03:腳本自己 `trap '…' EXIT` 會蓋掉我們的 EXIT ⇒ 沒有結束紀錄 ⇒ 整支量不到(雲端 test-dm-header-unify 8 個行程全是這樣:
#   dm-ctl.sh、aib-dm.sh 用 EXIT 清暫存檔)。不改寫腳本、不重定義 trap:在它設完之後(下一個命令的 DEBUG),
#   把它的收尾動作原樣留著、後面接我們的 _ez2_bt_exit。它的動作先跑、$? 照舊;它在收尾動作裡 exit ⇒ 我們不跑 ⇒ 沒結束紀錄 ⇒ 量不到(保守)。
#   只管主行程(子殼的 $$ 是父行程的,替子殼記結束會冒充父行程)。看不懂 trap -p 的格式 ⇒ 記量不到,不猜。
_ez2_bt_rearm_exit() {
  [ "${BASHPID:-$$}" = "$$" ] || return 0
  local cur act
  cur=$(builtin trap -p EXIT)
  case $cur in
    *_ez2_bt_exit*) return 0 ;;
    "") builtin trap '_ez2_bt_exit' EXIT; return 0 ;;
    "trap -- '"*"' EXIT") act=${cur#"trap -- '"}; act=${act%"' EXIT"}; act=${act//"'\\''"/"'"} ;;
    *) _ez2_bt_unmeasured "bash-exit-trap-unparsed"; return 0 ;;
  esac
  builtin trap -- "$act"$'\n''_ez2_bt_exit' EXIT
  return 0
}

_ez2_bt_exit() {
  local _rc=$?
  _EZ2_BT_EXITING=1
  set +x
  # 不在這裡 grep trace 檔:每多一個外部命令,數 exec 的測試就多一筆(觀測者不得改變被觀測物);source 的檔在 DEBUG 就雜湊了
  [ -f "$0" ] && [ "$0" != "${BASH:-}" ] && _ez2_bt_open "$0"     # 主腳本本身(bash x.sh / #!/bin/bash 直接執行);bash -c 時 $0 是 bash 二進位,不雜湊
  case "$(trap -p DEBUG)" in *"_ez2_bt_debug;; esac' DEBUG") ;; *) _ez2_bt_unmeasured "bash-debug-trap-overridden" ;; esac
  _ez2_bt_emit "{\"ch\":\"exit\",\"pid\":$$,\"via\":\"bash\"}"
  return 0
}

# ① 結構上不可能讓被追的腳本變色(艦長 09-14 00:50 三條件之一):trace 檔寫不進去 ⇒ 什麼都不裝、什麼都不印,
#    分析端會因為沒有 inherit 而判 bash-child-no-inherit(量不到 ⇒ 真跑);這支檔裡沒有 set -e、沒有裸 exit,每條路收尾 return 0。
if ! ( : >> "$EZ2_VERIFY_TRACE" ) 2>/dev/null; then
  return 0 2>/dev/null || exit 0
fi
# 10-03(tmux 第一版):記這個 bash 起來時手上的 TMUX_TMPDIR / TMUX(沒設 = null),分析端判 tmux socket 是不是測試自己的
if [ -n "${TMUX_TMPDIR+x}" ]; then _ez2_bt_tt="\"$(_ez2_bt_json "$TMUX_TMPDIR")\""; else _ez2_bt_tt=null; fi
if [ -n "${TMUX+x}" ]; then _ez2_bt_tx="\"$(_ez2_bt_json "$TMUX")\""; else _ez2_bt_tx=null; fi
#   以及 PATH 上的 tmux 解析到哪(hash 不開子殼;BASH_CMDS 是 bash 4 起才有,沒有就 null ⇒ 分析端不放行)
_ez2_bt_tp=null
if hash tmux 2>/dev/null && [ -n "${BASH_CMDS[tmux]:-}" ]; then _ez2_bt_tp="\"$(_ez2_bt_json "${BASH_CMDS[tmux]}")\""; fi
# 10-05:起來時手上有沒有改 git 倉的環境變數(GIT_DIR…,與 shadow_tracer.GIT_REPO_ENV 同一組);有 ⇒ 這個 bash 的 git 一律量不到
_ez2_bt_ge=0
for _ez2_bt_v in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR \
    GIT_NAMESPACE GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CEILING_DIRECTORIES \
    GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_REPLACE_REF_BASE GIT_NO_REPLACE_OBJECTS GIT_GRAFT_FILE GIT_SHALLOW_FILE; do
  [ -n "${!_ez2_bt_v+x}" ] && _ez2_bt_ge=1
done
_ez2_bt_emit "{\"ch\":\"inherit\",\"pid\":$$,\"via\":\"bash\",\"argv0\":\"$(_ez2_bt_json "$0")\",\"tmux_tmpdir\":$_ez2_bt_tt,\"tmux\":$_ez2_bt_tx,\"tmux_exe\":$_ez2_bt_tp,\"gitenv\":$_ez2_bt_ge}"
unset _ez2_bt_tt _ez2_bt_tx _ez2_bt_tp _ez2_bt_ge _ez2_bt_v
exec {_EZ2_BT_FD}>>"$EZ2_VERIFY_TRACE"     # ★ exec 上不准再掛 2>/dev/null:exec 的重導向是永久的,會把腳本的 stderr 一起吞掉(09-14 實踩)
BASH_XTRACEFD=$_EZ2_BT_FD
PS4="+EZ2BT${_EZ2_BT_SEP}\${BASH_SOURCE:-}${_EZ2_BT_SEP}\${LINENO}${_EZ2_BT_SEP}\${PWD}${_EZ2_BT_SEP}\$\$${_EZ2_BT_SEP} "
# ★ T0-2:DEBUG trap 字串就是熱路徑(bash 每次都重新解析這一串,所以只放兩個 case,一個外部命令都沒有):
#   第一個 case 看 xtrace 還開著沒(腳本 set +x ⇒ 記一次 unmeasured);第二個 case 只把「含 <」「. /source」「exec」
#   或「這個檔第一次執行命令」(_EZ2_BT_SRC_SEEN 沒有它 ⇒ 前綴 0)的命令交給 _ez2_bt_debug;其餘命令在這裡就結束。
#   case 的字面判準只決定「要不要走慢路徑」,判錯的方向只有多跑一次慢路徑,不會漏記(慢路徑自己會再查)。
#   10-04:`((` / `[[` 裡的 `<` 是比大小不是讀檔(`for ((i=0;i<N;i++))` 每圈都含 `<`,以前每圈走一次慢路徑,
#   Linux 上 t02 迴圈 45× → 23×)⇒ 開頭是 `((`/`[[ ` 且沒有 `$`、反引號 ⇒ 快路徑;有 `$`/反引號(可能 `$(<file)`)照樣走慢路徑。
#   掛在 `[[ ]]`/`(( ))` 外面的 `< file` 不會出現在 BASH_COMMAND 裡,舊規則也看不到,沒有因此少記。
#   量:tests/test-verify-tracer-slowdown-t02.py S1(兩臂比值釘上界)。
trap 'case $- in *x*) ;; *) _ez2_bt_xoff;; esac; case ${_EZ2_BT_TC:-}${_EZ2_BT_SRC_SEEN[${BASH_SOURCE[0]:-/}]:-0}$BASH_COMMAND in T*|1"(("*[\$\`]*|1"[[ "*[\$\`]*) _ez2_bt_debug;; 1"(("*|1"[[ "*) ;; 1*"<"*|1*"/dev/"[tu][cd]"p/"*|1". "*|1"source "*|1"exec "*|1"trap "*|1"builtin trap "*|0*) _ez2_bt_debug;; esac' DEBUG
trap '_ez2_bt_exit' EXIT
set -o functrace
set -x
_EZ2_BT_ARMED=1
