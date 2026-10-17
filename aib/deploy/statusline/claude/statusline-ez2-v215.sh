#!/bin/bash
ACB_UI_PREFIX="${ACB_UI_PREFIX:-$HOME/.local}"
case "$ACB_UI_PREFIX" in
  /*) ;;
  *) ACB_UI_PREFIX="$HOME/.local" ;;
esac
case "$ACB_UI_PREFIX" in
  *:*|*$'\n'*|*$'\r'*) ACB_UI_PREFIX="$HOME/.local" ;;
esac
export ACB_UI_PREFIX
# Prepend the pinned ACB prefix, but retain the provider's inherited PATH so a
# Node installed through nvm/asdf remains available for the CTX gauge.
export PATH="$ACB_UI_PREFIX/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH:-}"
ACB_CLAUDE_UI_DIR=$(CDPATH= cd -P -- "$(dirname -- "$0")" 2>/dev/null && pwd -P)
# BE(2026-09-03,the maintainer 直令關閉 ACB):本檔原本把 cache/trace/title-override/now/收據
# 全寫在 $HOME/.acb 底下,而那些呼叫幾乎都是 `mkdir -p`——直接封存 ~/.acb 的話,
# 這支狀態列會**把它一格一格重新建回來**,ACB 看起來關了其實沒關。
# 改吃一個基底變數;預設落在 aib 的狀態樹,舊值可用環境變數指回去。
EZ2_UI_STATE_ROOT="${EZ2_UI_STATE_ROOT:-$HOME/.ez2-aib/ui-state}"
if [ -z "$ACB_CLAUDE_UI_DIR" ] || [ -L "$0" ]; then
  ACB_CLAUDE_UI_DIR="$HOME/.claude"
fi
export ACB_CLAUDE_UI_DIR

# Identity evidence always lives under HOME/.acb. ACB_DATA* may select only
# non-authoritative UI state. Conflicting aliases are ambiguous, so the UI
# consumes the provider payload but does not read or write any ACB UI state.
ACB_UI_DATA_CONFLICT=0
if [ -n "${ACB_DATA_DIR:-}" ] && [ -n "${ACB_DATA:-}" ] \
  && [ "$ACB_DATA_DIR" != "$ACB_DATA" ]; then
  _acb_ui_data_dir=$(/usr/bin/python3 -I -c \
    'import os,sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' \
    "$ACB_DATA_DIR" 2>/dev/null)
  _acb_ui_data=$(/usr/bin/python3 -I -c \
    'import os,sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' \
    "$ACB_DATA" 2>/dev/null)
  if [ -z "$_acb_ui_data_dir" ] || [ -z "$_acb_ui_data" ] \
    || [ "$_acb_ui_data_dir" != "$_acb_ui_data" ]; then
    ACB_UI_DATA_CONFLICT=1
  fi
fi
export ACB_UI_DATA_CONFLICT

sl_safe_sid() {
  case "$1" in
    ""|*[!A-Za-z0-9_-]* ) return 1 ;;
  esac
  [ "${#1}" -le 128 ]
}
# ACB 2.17.0 status line + terminal title (wiring name v215 kept: settings.json points here) — facts only from authority.
#
# WP-D P8/P9(a seat,2026-08-01):身份查詢移出關鍵路徑。
# 量到的事實:冷跑中位 642ms,其中 `acb session-authority` 一支就 436ms(68%);
# 熱路徑(快取命中)是 161ms。所以瓶頸只有一個,且它不需要同步等待——
# 改成「先出畫面,背景刷新」之後,冷跑會落在熱路徑的地板附近。
#
# ★誠實紅線(工單明訂):身份列用快取**必標齡或 fail-closed**,過期身份不得顯示成現值。
#   本檔的三段式:
#     age <  SL_FRESH   服務中的身份視為現值,不標齡(一個心跳內)
#     age <  SL_MAX     照樣顯示,但**標上齡**,並在背景刷新
#     age >= SL_MAX     不顯示身份(fail-closed),改出降級文案
#   沒有快取時也不同步等——出「查詢中」並背景取,不假裝有身份。
SL_FRESH="${SL_FRESH:-10}"
SL_MAX="${SL_MAX:-600}"

# --refresh <sid>:只跑身份查詢並寫快取,不輸出。由前景呼叫以背景方式觸發。
if [ "$1" = "--refresh" ]; then
  SID="$2"
  sl_safe_sid "$SID" || exit 0
  # N1b:身份列快取與 .title 同根因(見下方 TITLE_DIR 註解),一併遷出 /tmp。
  CACHE="${ACB_TITLE_CACHE_DIR:-$EZ2_UI_STATE_ROOT/state/title-cache}/${SID:-none}.cache"
  mkdir -p "$(dirname "$CACHE")" 2>/dev/null && chmod 700 "$(dirname "$CACHE")" 2>/dev/null
  REFRESH_LOCK="${CACHE%.cache}.refreshing"
  # 同一 session 同時只跑一支刷新;鎖超過 60 秒視為死鎖殘留。
  if [ -f "$REFRESH_LOCK" ]; then
    if [ $(( $(date +%s) - $(stat -f %m "$REFRESH_LOCK" 2>/dev/null || echo 0) )) -lt 60 ]; then
      exit 0
    fi
    rm -f "$REFRESH_LOCK"   # 死鎖殘留(>60s):持有者已不在,清掉後走下面的原子建立
  fi
  # ★原子建立(C1,41379 實測 6 支同時 spawn 有 2 支過 check-then-create 的窗口):
  #   noclobber=O_EXCL 語意,同時只有一個建立者成功;輸的直接退,不重複 436ms 查詢。
  if ! ( set -C; : > "$REFRESH_LOCK" ) 2>/dev/null; then
    exit 0
  fi
  trap 'rm -f "$REFRESH_LOCK"' EXIT
  SL_REFRESH_MODE=1
  export SL_REFRESH_MODE
fi

# 刷新模式沒有 stdin(它是被背景叫起來的),不能在這裡等 cat——會永遠卡住。
if [ "${SL_REFRESH_MODE:-0}" = "1" ]; then
  INPUT=$(printf '{"session_id":"%s"}' "$SID")
else
  INPUT=$(cat)
fi
SID=$(printf '%s' "$INPUT" | /usr/bin/python3 -I -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
# 2026-10-10 the maintainer:CTX 那一行也要有席位、上面掛主項目(全部的 AI 席都要)。席位碼與 📌 那一行都問 aib now --statusline
#   (seat_now.py:窗 id → 席位檔 → seat_address.uuid_tail5;/clear 後對話 id 換、席位碼不換;非艦長席也有)。
#   不在 AIB 席上(沒有 TMUX_PANE、查不到席位檔)就是空,兩樣都不畫。
_EZ2_SEAT_CODE=""
_EZ2_NOW_LINE=""
_EZ2_LOGO=""
_EZ2_RANK=""
# 10-10 a seat:有的 Claude 席沒有 TMUX_PANE ⇒ 也遞 session_id(seat_now 拿去比席位檔,恰好一席才用)
if [ -n "${TMUX_PANE:-}" ] || [ -n "$SID" ]; then
  _ez2_now_out=""
  if [ -n "${EZ2_AIB_ROOT:-}" ] && [ -f "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" ]; then
    _ez2_now_out="$(/usr/bin/python3 "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" --statusline --rank-field --session "$SID" 2>/dev/null || true)"
    # 10-17:舊版 seat_now 不認得 --rank-field ⇒ 空 ⇒ 不帶旗再問一次(等級就接在 📌 行尾)
    [ -n "$_ez2_now_out" ] || _ez2_now_out="$(/usr/bin/python3 "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" --statusline --session "$SID" 2>/dev/null || true)"
  elif command -v aib >/dev/null 2>&1; then
    _ez2_now_out="$(aib now --statusline --rank-field --session "$SID" 2>/dev/null || true)"
    [ -n "$_ez2_now_out" ] || _ez2_now_out="$(aib now --statusline --session "$SID" 2>/dev/null || true)"
  fi
  # 欄位用 \x1f 分(不是 tab:bash read 會把連續 tab 併成一個,📌 欄空時 AI 標誌會跑錯欄)
  # 第 4 欄 = 等級與資源(10-17 the maintainer:「5⭐ 30木材/12石塊」放在 CTX 那一行 @席位 後面;舊版只有 3 欄 ⇒ 空)
  IFS=$'\x1f' read -r _EZ2_SEAT_CODE _EZ2_NOW_LINE _EZ2_LOGO _EZ2_RANK <<<"$_ez2_now_out"
  case "$_EZ2_SEAT_CODE" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) _EZ2_SEAT_CODE=""; _EZ2_NOW_LINE=""; _EZ2_LOGO=""; _EZ2_RANK="" ;; esac
fi
sl_safe_sid "$SID" || SID=""

# ── 自動回報(the maintainer 2026-08-07 拍板):把「這個 session 現在站在哪」寫進
#    ~/.acb/state/now/<sid>,艦隊看板的任務列讀它。取代「要 AI 自己記得呼叫
#    set-status」——會忘記的機制等於沒有機制,實證是三個活體 session 全都空著。
#
# 三條硬約束,每條都有理由:
#   1. 只在**非刷新模式**寫。刷新是背景叫起來的,沒有真 stdin 也沒有 session 的
#      真 cwd(見本檔 §背景刷新 cwd=~ 那段),在那裡寫等於回報一個假位置。
#   2. **手動回報優先**。既有檔若沒有 .auto 旁標,代表是人或 AI 自己寫的,
#      一律不覆蓋——自動化不該把人講的話蓋掉。
#   3. **不 spawn git**。本檔冷跑中位 642ms 的教訓在上面寫著,直接讀 .git/HEAD,
#      往上找上限 6 層,找不到就只報目錄名。
#
# 誠實邊界:這回報的是「在哪」不是「在做什麼」。位置是可觀測事實,意圖不是——
# 寧可報一個真的窄事實,不要猜一個好看的寬宣稱。
sl_auto_now() {
  [ "${SL_REFRESH_MODE:-0}" = "1" ] && return 0
  sl_safe_sid "$SID" || return 0
  _an_dir="$EZ2_UI_STATE_ROOT/state/now"
  [ -d "$_an_dir" ] || return 0
  _an_f="$_an_dir/$SID"
  # symlink 一律不碰(同本檔其他守衛讀的規矩)
  [ -L "$_an_f" ] && return 0
  if [ -e "$_an_f" ] && [ ! -e "$_an_f.auto" ]; then
    return 0                     # 手動回報,不覆蓋
  fi
  _an_dirpath="$PWD"
  _an_repo=""; _an_branch=""
  _an_p="$_an_dirpath"; _an_n=0
  while [ "$_an_n" -lt 6 ] && [ -n "$_an_p" ] && [ "$_an_p" != "/" ]; do
    if [ -e "$_an_p/.git" ]; then
      _an_repo="$_an_p"
      # ★.git 有兩種形態,只處理目錄那種會讓**每個 worktree 都誤報 detached**:
      #   worktree 的 .git 是一個檔案,內容 "gitdir: <實際 gitdir>"。本 repo 的
      #   AGENTS.md 規定開工就要開 worktree,所以那才是常態不是例外。
      # ★這裡刻意不用 sl_safe_cat:它定義在本檔更下方,在這個呼叫點還不存在,
      #   而外層的 2>/dev/null 會把 command-not-found 吞成空字串 —— 實測就是這樣
      #   產生了假的 (detached)。要嘛把守衛帶在身邊,要嘛別假裝有守衛。
      _an_gitdir=""
      if [ -d "$_an_p/.git" ]; then
        _an_gitdir="$_an_p/.git"
      elif [ -f "$_an_p/.git" ] && [ ! -L "$_an_p/.git" ]; then
        _an_gd=$(head -c 4096 "$_an_p/.git" 2>/dev/null)
        case "$_an_gd" in
          "gitdir: "*) _an_gitdir="${_an_gd#gitdir: }" ;;
        esac
      fi
      if [ -n "$_an_gitdir" ] && [ -f "$_an_gitdir/HEAD" ] \
         && [ ! -L "$_an_gitdir/HEAD" ]; then
        _an_head=$(head -c 512 "$_an_gitdir/HEAD" 2>/dev/null)
        case "$_an_head" in
          "ref: refs/heads/"*)
            _an_branch="${_an_head#ref: refs/heads/}"
            _an_branch="${_an_branch%%
*}" ;;
          *) _an_branch="" ;;    # 真 detached:不猜分支名
        esac
      fi
      break
    fi
    _an_p=$(dirname "$_an_p"); _an_n=$((_an_n + 1))
  done
  if [ -n "$_an_branch" ]; then
    _an_text="$(basename "$_an_repo")@$_an_branch"
  elif [ -n "$_an_repo" ]; then
    _an_text="$(basename "$_an_repo")@(detached)"
  else
    _an_text="$(basename "$_an_dirpath")"
  fi
  # 板子上限 512 bytes;第二行走它宣告的 repo=/path branch=name 格式
  _an_tmp="$_an_f.tmp.$$"
  {
    printf '%.200s\n' "$_an_text"
    if [ -n "$_an_repo" ] && [ -n "$_an_branch" ]; then
      printf 'repo=%s branch=%s\n' "$_an_repo" "$_an_branch"
    fi
  } > "$_an_tmp" 2>/dev/null || { rm -f "$_an_tmp" 2>/dev/null; return 0; }
  chmod 600 "$_an_tmp" 2>/dev/null
  mv -f "$_an_tmp" "$_an_f" 2>/dev/null || { rm -f "$_an_tmp" 2>/dev/null; return 0; }
  : > "$_an_f.auto" 2>/dev/null   # 旁標:這份是自動寫的,下次可以覆蓋
  chmod 600 "$_an_f.auto" 2>/dev/null
  return 0
}
sl_auto_now 2>/dev/null || true
if [ "$ACB_UI_DATA_CONFLICT" = "1" ]; then
  [ "${SL_REFRESH_MODE:-0}" = "1" ] && exit 0
  _acb_conflict_line=$(printf '%s' "$INPUT" \
    | node "$ACB_CLAUDE_UI_DIR/statusline-ez2-ctx.js" 2>/dev/null)
  printf '%s' "${_acb_conflict_line:-● | ACB | UI data root conflict,身份資訊已隱藏}"
  exit 0
fi
# ★與刷新入口(上方)同一條路徑式:這行原本硬編 $HOME,env 覆寫下 .cache/.reason/
#   .refreshing 落三個目錄——python 段那句「路徑跟著 CACHE 走」跟到的就是這份硬編,
#   P9 分流在治具裡必落 catch-all(F3 機器收據)。同一件事寫兩處必分岔,收成同一式。
CACHE="${ACB_TITLE_CACHE_DIR:-$EZ2_UI_STATE_ROOT/state/title-cache}/${SID:-none}.cache"
# 呼叫量測:每次被呼叫記一行,用來量刷新節奏。
# 預設就記(一行 append,成本可忽略);設 ACB_STATUSLINE_TRACE=off 可關。
# ★不用 env 當開關的預設值:env 在 session 啟動當下固定,對已在跑的 session 改 settings
#   完全無效——腳本本體則是每次呼叫都重讀,所以量測邏輯必須寫在腳本裡。
# ★F5:量測檔遷出 /tmp(1777)——舊位置任何使用者可先建 symlink 指到受害者可寫的檔,
#   盲目 >> 就成了跨 user 任意附加(與 N1 同構,41379「量測檔清單」的實例)。
#   新位置=自家 logs(0700 家族);縱深再加 symlink 拒寫(env 覆寫值同樣要過這道)。
ACB_STATUSLINE_TRACE="${ACB_STATUSLINE_TRACE:-$EZ2_UI_STATE_ROOT/logs/statusline-trace.tsv}"
if [ "$ACB_STATUSLINE_TRACE" != "off" ] && [ ! -L "$ACB_STATUSLINE_TRACE" ]; then
  _hit="miss"
  if [ -f "$CACHE" ] && [ $(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) )) -lt 10 ]; then _hit="cachehit"; fi
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$_hit" "${SID:0:8}" "$(ps -o tty= -p $PPID 2>/dev/null | tr -d ' ')" >> "$ACB_STATUSLINE_TRACE" 2>/dev/null
fi
# ★N1(重大,6db35 於三輪複審新 R1 端到端實測):.title 原本住 /tmp(1777)且讀取端
#   只有非空檢查,任何同機行程都能搶先建檔決定整個標題=顯示值不再來自 authority。
#   .mark 住 0700 且有六道檢查,餵同一個顯示表面的 .title 卻是零道——防禦等級不對稱。
#   修法與 e63f4 當初把覆蓋檔遷出 /tmp 同向:遷 ~/.acb/state/title-cache(0700)。
#   權限只在建立當下設(讀取路徑純檢查,每次 chmod 等於讀前自動修門=閘變死碼)。
TITLE_DIR="${ACB_TITLE_CACHE_DIR:-$EZ2_UI_STATE_ROOT/state/title-cache}"
if [ ! -d "$TITLE_DIR" ]; then
  mkdir -p "$TITLE_DIR" 2>/dev/null && chmod 700 "$TITLE_DIR" 2>/dev/null
fi
TITLE_CACHE="$TITLE_DIR/${SID:-none}.title"
TITLE_BASE="$TITLE_DIR/${SID:-none}.title.base"
TITLE_DISPLAY="$TITLE_DIR/${SID:-none}.title.display.json"
TITLE_GENERATION="$TITLE_DIR/${SID:-none}.title.display.generation"
sl_clear_title_artifacts() {
  /usr/bin/python3 -I - "$1" "$2" <<'PY' >/dev/null 2>&1
import os, re, stat, sys
directory, sid = sys.argv[1:]
uuid_re = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
if not os.path.isabs(directory) or not uuid_re.fullmatch(sid or ""):
    raise SystemExit(0)
try:
    parent = os.lstat(directory)
    safe_parent = (stat.S_ISDIR(parent.st_mode)
                   and not stat.S_ISLNK(parent.st_mode)
                   and parent.st_uid == os.getuid()
                   and not (stat.S_IMODE(parent.st_mode) & 0o022))
except OSError:
    safe_parent = False
if safe_parent:
    for suffix in (".title", ".title.base", ".title.display.json",
                   ".title.display.generation"):
        path = os.path.join(directory, sid + suffix)
        try:
            target = os.lstat(path)
            if ((stat.S_ISREG(target.st_mode) or stat.S_ISLNK(target.st_mode))
                    and target.st_uid == os.getuid()):
                os.unlink(path)
        except OSError:
            pass
PY
}
sl_clear_fast_snapshot() {
  /usr/bin/python3 -I - "$1" "$2" <<'PY' >/dev/null 2>&1
import os, re, stat, sys
directory, sid = sys.argv[1:]
uuid_re = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
if not os.path.isabs(directory) or not uuid_re.fullmatch(sid or ""):
    raise SystemExit(0)
try:
    parent = os.lstat(directory)
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) & 0o022):
        raise SystemExit(0)
    path = os.path.join(directory, sid + ".title.display.generation")
    target = os.lstat(path)
    if ((stat.S_ISREG(target.st_mode) or stat.S_ISLNK(target.st_mode))
            and target.st_uid == os.getuid()):
        os.unlink(path)
except OSError:
    pass
PY
}
# ★基底/顯示分檔(F1,a seat 整檔通讀後定案):
#   .title.base=刷新模式產的 receipt-bound 基底(daemon 唯一重申來源);
#   .title=前景組好的**顯示值**(daemon 永不讀)。分開的理由:顯示值回寫同一個檔會讓
#   下一次呼叫把「已替換過的標題」再替換一次——佔位已不在,實時 CTX 段就純追加,
#   齡標同理,治具實測三跑三份(收據=plans/tj1nr3/review-statusline-full-read-pm10342.md)。
#   每次都從基底重組=天然冪等;基底缺(升級過渡)=照舊重申 .title 原樣,不追加不回寫,
#   下一輪刷新補上基底後自癒。
# 底部列=Ez2 CTX 儀表(the maintainer 拍板 08-01:回 Ez2 討論版,身份資訊留在標題)。
# ez2-ctx-core 自己 fail-open;它輸出空(node 缺/炸)才退回 ACB 身份列,狀態列永不消失。
# ★刷新模式禁跑 Ez2 儀表:它的最小 payload 沒有 context_window,跑了會把真油表
# 蓋成 None(08-01 the maintainer 抓到「CTX 不見了」,根因就是這條;gauge 是量測檔不是快取)。
if [ "${SL_REFRESH_MODE:-0}" = "1" ]; then
  EZ2LINE=""
else
  EZ2LINE=$(printf '%s' "$INPUT" | node "$ACB_CLAUDE_UI_DIR/statusline-ez2-ctx.js" 2>/dev/null)
fi
# 標題右側 CTX 段(the maintainer 拍板 08-01):取 Ez2 線去掉 ANSI 色碼與開頭 model 段。
EZ2CTX_SEG=$(printf '%s' "$EZ2LINE" | LC_ALL=C sed -e $'s/\x1b\\[[0-9;]*m//g' -e 's/^[^|]*| //')
export EZ2CTX_SEG
# 2026-10-10 the maintainer:「Opus 5.5 | 席位 | 💧 Ez2 CTX …」—— 席位插在模型名後面(標題右側 CTX 段在上一行已取完,不受影響)。
#   10-17:席位後面接等級與資源(@570a9 5⭐ 30木材/12石塊),📌 那一行就只剩主項目、步數與更新時間。
[ -n "$_EZ2_SEAT_CODE" ] && [ -n "$EZ2LINE" ] && EZ2LINE="${EZ2LINE/ | / | @$_EZ2_SEAT_CODE${_EZ2_RANK:+ $_EZ2_RANK} | }"
# 2026-10-10 the maintainer:「最前面要加上對應的 AI logo,比照右方 ai 席的 logo」—— 右欄同一個圖示與品牌色(seat_now.logo_of)。
#   圖示後面空**兩**格:Nerd Font 圖示畫得比一格寬,會蓋掉一格空白(the maintainer 10-10「icon 與字要空一格」;右欄 09-02 同一件事)。
[ -n "$_EZ2_LOGO" ] && [ -n "$EZ2LINE" ] && EZ2LINE="$_EZ2_LOGO  $EZ2LINE"
# D41(2026-09-06):pane id 只在單一 tmux server 內唯一,不能當跨艙身份鍵。
# /clear 後位址仍是登記 session_uuid 的末五碼,所以 live session 改寫
# by-seat/<登記五碼>.json。找不到艙內席位檔就不寫,也不退回 by-pane。
# ★★ 09-07 root744f4:`EZ2_AIB_INSTANCE_HOME` 在 **Claude Code 起 statusline 的那個環境裡是空的**
#   —— 讓 statusline 自己印才量到的:`refresh=0 tmux=set pane=%2 sid=set home=EMPTY`
#   (席位行程的 argv 有設它,但 Claude Code 給子行程的環境沒有帶過來)。
#   於是這整段**從來沒有被跑到**,by-seat 那座橋一筆都沒寫過 —— 本艙開席到收工 0 筆,
#   目錄裡最新的兩筆是前一晚別艙的。
#   後果:`clear`/`compact` 換掉對話 id 之後,statusline 寫 `<新對話id>.pct`,
#   而右欄用**席位檔的 session_uuid**(啟動時的值,不換)去讀 ⇒ 兩邊從那一刻起讀寫不同的檔,
#   右欄的 CTX% 就凍在換 id 前的最後一個值。`resolve_live_session()` 本來就是為了修這件事,
#   它靠的正是這座橋。
# ⇒ 空的時候從 `$TMUX` 的 socket 名導出(取法與 `dm-ctl.sh:496` 的 `${TMUX%%,*}` 同源)。
#   ★ **fail-closed**:導不出來、或那個目錄沒有 `seats/`,就維持原樣不寫 —— 不猜一個路徑。
_EZ2_SEAT_HOME="${EZ2_AIB_INSTANCE_HOME:-}"
if [ -z "$_EZ2_SEAT_HOME" ] && [ -n "${TMUX:-}" ]; then
  _ez2_sock="$(basename "${TMUX%%,*}" 2>/dev/null || true)"
  case "$_ez2_sock" in
    aihub-*) _ez2_try="$HOME/.ez2-aib/instances/${_ez2_sock#aihub-}" ;;
    aihub)   _ez2_try="$HOME/.ez2-aib/instances/main" ;;
    *)       _ez2_try="" ;;
  esac
  [ -n "$_ez2_try" ] && [ -d "$_ez2_try/seats" ] && _EZ2_SEAT_HOME="$_ez2_try"
fi
if [ "${SL_REFRESH_MODE:-0}" != "1" ] && [ -n "${TMUX:-}" ] && [ -n "${TMUX_PANE:-}" ] \
   && [ -n "$SID" ] && [ -n "${_EZ2_SEAT_HOME:-}" ]; then
  _EZ2_LIVE_WINDOW="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
  /usr/bin/python3 -I - "$SID" "$_EZ2_LIVE_WINDOW" "$_EZ2_SEAT_HOME" \
    "${EZ2_CTX_DIR:-$HOME/.ez2/ctx}" "${EZ2_AIB_INSTANCE:-}" "$PPID" <<'PYBYSEAT' 2>/dev/null || true
import json, os, re, sys, tempfile

sid, window, instance_home, root, instance, pid_text = sys.argv[1:7]
uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
window_re = re.compile(r"@([0-9]{1,10})")
instance_re = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}")
utc_re = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")
m = window_re.fullmatch(window)
if not instance:
    base = os.path.basename(os.path.realpath(instance_home))
    instance = base if os.path.basename(os.path.dirname(os.path.realpath(instance_home))) == "instances" else "main"
try:
    pid = int(pid_text)
except (TypeError, ValueError):
    pid = 0
if m and uuid_re.fullmatch(sid) and instance_re.fullmatch(instance) and pid > 0:
    seat_path = os.path.join(instance_home, "seats", "w%s.json" % m.group(1))
    try:
        with open(seat_path, encoding="utf-8") as fh:
            seat_rec = json.load(fh)
    except Exception:
        seat_rec = None
    registered = seat_rec.get("session_uuid") if isinstance(seat_rec, dict) else None
    seat = registered.replace("-", "")[-5:].lower() if uuid_re.fullmatch(registered or "") else None
    valid_seat = (isinstance(seat_rec, dict)
                  and seat_rec.get("schema") == "aib-seat/v1"
                  and seat_rec.get("window_id") == window
                  and seat is not None and re.fullmatch(r"[0-9a-f]{5}", seat))
    try:
        with open(os.path.join(root, sid + ".pct"), encoding="utf-8") as fh:
            gauge = json.load(fh)
    except Exception:
        gauge = None
    observed = gauge.get("observed_at") if isinstance(gauge, dict) and gauge.get("session_id") == sid else None
    if valid_seat and utc_re.fullmatch(observed or ""):
        d = os.path.join(root, "by-seat")
        os.makedirs(d, mode=0o700, exist_ok=True)
        out = {"schema": "ez2-ctx-by-seat-v1", "seat": seat, "instance": instance,
               "window": window, "session_id": sid, "observed_at": observed, "pid": pid}
        fd, tmp = tempfile.mkstemp(prefix=".%s." % seat, suffix=".tmp", dir=d)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(json.dumps(out, separators=(",", ":")) + "\n")
            os.replace(tmp, os.path.join(d, seat + ".json"))
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
PYBYSEAT
fi
# tty 在 bash 層量($PPID=claude 本體才有 ttys),python 自己的行程樹在 harness 下是 ??
# ——實測:miss 路徑 python 查 getppid 拿 ?? 而 bash 查 $PPID 拿 ttys030。
# ★刷新模式繼承前景 export 的值,不重量:nohup 子行程的 PPID 在前景退出後 reparent
#   到 1,重量=「」→ 基底標題沒有 tty 前綴。前景在不在是競態,繼承讓它變確定
#   (the maintainer 08-01 晚實報:清快取重生後所有標題的 ttys 同時消失=競態全輸的形狀)。
if [ "${SL_REFRESH_MODE:-0}" = "1" ] && [ -n "${ACB_SL_TTY:-}" ] && [ "${ACB_SL_TTY:-}" != "??" ]; then
  :
else
  ACB_SL_TTY=$(ps -o tty= -p $PPID 2>/dev/null | tr -d ' ')
fi
export ACB_SL_TTY
# 視窗寬度(響應式看板用):從真 tty 量,量不到=完整版。
EZ2_COLS=$(stty -f "/dev/$ACB_SL_TTY" size 2>/dev/null | awk '{print $2}')
export EZ2_COLS="${EZ2_COLS:-999}"
# e63f4 P1 裁定後遷出全機可寫 /tmp;目錄 0700=跨 user 不可達。
OVR_DIR="$EZ2_UI_STATE_ROOT/state/title-override"
# 權限只在建立當下設,讀取路徑純檢查——每次 chmod 等於讀前自動修門,目錄閘變死碼
# (探針3 實測抓到的形狀:防護被自己的 self-heal 繞過)。
if [ ! -d "$OVR_DIR" ]; then
  mkdir -p "$OVR_DIR" 2>/dev/null && chmod 700 "$EZ2_UI_STATE_ROOT/state" "$OVR_DIR" 2>/dev/null
fi
OVR="$OVR_DIR/${SID:-none}"
# 艦隊駕駛艙段(the maintainer 授權 08-01 重打造):只有主視窗(👑 mark)追加;讀快照極快,
# 快照過期由收集器自己背景刷新,不拖慢本次輸出。段含 🔴=有協作 AI 危急,同步點亮標題警報。
FLEET_COCKPIT=""
FLEET_ALERT=0
_MK="$OVR_DIR/${SID:-none}.mark"
# 艦隊看板的顯示資格(the maintainer 拍板 08-01 晚,放寬):👑主看全艦隊,⚓艦長看自己那一列。
# 理由=艦長要督導自己的艦隊卻看不到,只能手打 acb II 查,那是把責任給了人卻不給儀表。
# 組員仍不顯示(每個視窗都畫一大片重複資訊沒有價值)。
# 「看哪幾列」的判定在引擎(read_fleet_view/自動判定艦長列),這裡只管「要不要畫」。
if [ -f "$_MK" ] && [ ! -L "$_MK" ] && grep -qE '👑|⚓' "$_MK" 2>/dev/null; then
  FLEET_COCKPIT=$(CLAUDE_CODE_SESSION_ID="$SID" /usr/bin/python3 -I "$ACB_CLAUDE_UI_DIR/ez2-fleet-board.py" --cockpit 2>/dev/null)
  case "$FLEET_COCKPIT" in *🔴*) FLEET_ALERT=1;; esac
fi
export FLEET_ALERT
# 每 session 覆蓋檔:標題與底部狀態列兩個表面共用同一份。
# 底部列採前綴式(覆蓋值 | 身份列),身份資訊不刪;快取存「未加前綴」的身份列,
# 前綴在輸出當下才加,所以 rm/改覆蓋檔在下一次呼叫立即反映,不吃 10 秒快取。
override_value() {  # bash 快取路徑的效能重實作;判準正本=acb.session_title_override(含防禦集),parity 套件鎖定
  [ -e "$OVR" ] || return 0
  [ -L "$OVR" ] && return 0
  [ -f "$OVR" ] || return 0
  local fst dst fmode dmode
  fst=$(stat -f '%u %l %z %Lp' "$OVR" 2>/dev/null) || return 0
  set -- $fst; [ "$#" -eq 4 ] || return 0
  [ "$1" = "$(id -u)" ] && [ "$2" = "1" ] || return 0
  [ "$3" -gt 0 ] 2>/dev/null && [ "$3" -le 4096 ] || return 0
  fmode=$((8#$4)); [ $((fmode & 2)) -eq 0 ] || return 0
  dst=$(stat -f '%u %Lp' "$OVR_DIR" 2>/dev/null) || return 0
  set -- $dst; [ "$1" = "$(id -u)" ] || return 0
  dmode=$((8#$2)); [ $((dmode & 18)) -eq 0 ] || return 0
  tr -d '\n"' < "$OVR" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}
# 2026-10-10 the maintainer:「讓所有的 ai 在同個位置回報目前正在做的 主項目 | 完成%」「放在 CTX 那行的上面」。
#   席上跑 aib now "主項目" 2/5 ⇒ src/cockpit/seat_now.py 記下來;上面 aib now --statusline 已經算好這一行。
#   所有輸出路徑都經過 emit_with_override(包括提早結束的那幾條),📌 那一行在那裡接上。
# 10-10 the maintainer「上下二行對調」⇒ CTX 那一行在上、📌 那一行在下:emit_with_override 先算好 CTX 那一行
#   (_emit_ctx_line,原本的本體),再把 📌 那一行接在下面;CTX 那一行不印(艙內量不到)時只印 📌 那一行。
emit_with_override() {  # emit_with_override <status-line-text>
  local _main
  _main="$(_emit_ctx_line "$@")"
  if [ -n "$_main" ]; then
    printf '%s' "$_main"
    [ -n "$_EZ2_NOW_LINE" ] && printf '\n%s' "$_EZ2_NOW_LINE"
  else
    [ -n "$_EZ2_NOW_LINE" ] && printf '%s' "$_EZ2_NOW_LINE"
  fi
  return 0
}
_emit_ctx_line() {
  # 2026-09-21 the maintainer：AIB 艙內整列不印（含早退路徑），避免紅框 Model|Ez2 CTX。
  _hide=0
  [ -n "${EZ2_AIB_INSTANCE_HOME:-}${AIB_INSTANCE:-}${EZ2_AIB_INSTANCE:-}" ] && _hide=1
  case "${TMUX:-}" in *aihub-*) _hide=1 ;; esac
  # 巢狀在 AIB 艙時，外層 socket 也可能是 default，但 pane 標題／AIB 標記會在環境
  [ -n "${EZ2_TMUX_SESSION:-}" ] && case "${EZ2_TMUX_SESSION}" in aib-*) _hide=1 ;; esac
  [ -n "${EZ2_AIHUB_SOCKET:-}" ] && _hide=1
  # 2026-09-30 the maintainer:「能先顯示 CTX %數回來這個對話下方嗎」⇒ 艙內只在量不到(會畫 ❓ 紅框)時才整列不印;
  #   量得到百分比就照常顯示。
  # ★ review r1 m12:艙內改成「**正面**量到才印」—— 文字裡(剝色碼後)要有「CTX <數字>%」才印;
  #   其餘一律不印(包括尾端那次 emit_with_override "" —— 舊版會印出孤零零的「覆寫字 | 」)。
  #   舊判準是「看到 ❓ / CTX ? 才不印」,沒列到的失效形狀(空字串、別種佔位)照樣冒出來。
  if [ "$_hide" = "1" ]; then
    case "$1" in
      *"❓"*|*"CTX ?"*) printf ''; return 0 ;;
    esac
    printf '%s' "$1" | LC_ALL=C sed -e $'s/\x1b\\[[0-9;]*m//g' \
      | LC_ALL=C grep -Eq 'CTX[^0-9?]{0,6}[0-9]{1,3}%' || { printf ''; return 0; }
  fi
  local _ov
  _ov=$(override_value)
  if [ -n "$_ov" ]; then
    printf '%s | %s' "$_ov" "$1"
  else
    printf '%s' "$1"
  fi
}
sl_safe_cat() {  # 守衛讀(F4):非 symlink/普通檔/擁有者自己——.cache/.reason 餵同一
  # 顯示表面,不該比 .title 低一級(n1 收據寫「讀取端三檢」,這兩處當時是零檢)。
  [ -s "$1" ] && [ ! -L "$1" ] && [ -f "$1" ] && [ -O "$1" ] || return 0
  cat "$1"
}
sl_age() {  # 快取齡(秒);無快取回 -1
  [ -f "$CACHE" ] || { echo -1; return; }
  echo $(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) ))
}
# 自鑄收據的固定標記(檔名尾碼必須是 12 位小寫十六進位,這裡佔前 6 位,後 6 位放
# 本行程編號)。用固定標記是為了讓「哪些是顯示層自己鑄的」可以被機械識別:清理只碰
# 這些,bootstrap 與安裝鑄的稽核件一概不動。
SL_MINT_TAG="d15b1a"
SL_MINT_MIN_AGE="${SL_MINT_MIN_AGE:-60}"
sl_mint_receipt() {  # 續鑄顯示用收據;必須在前景(祖先鏈還接得回 provider)執行
  # 重放要的收據必須 90 秒內、且與當下 registry 檔同 sha,而心跳每分鐘重寫那個檔。
  # 鑄造事件卻只綁 bootstrap 與安裝這類稀疏動作,所以開場幾十秒後身份就永久降級。
  # 這裡補的是**密度**,不是放寬:鑄的是官方 CLI 的 --current,五條 display 檢查、
  # TTL、sha 比對、活體錨全部照原樣走一遍,鑄完還要過完整重放才會被採用。
  #
  # ★★★為什麼不能放在背景刷新裡(08-03 a seat 實測):`--current` 會核對呼叫者的
  #   行程祖先鏈,而背景刷新是 nohup 脫離的,前景一退出它就被 reparent,鏈就斷了
  #   ——實測 rc=2 且 blocking=process_live_binding,caller_process_anchor_evidence_v2。
  #   ★我一度以為背景可行,因為探針「通過」了;那是假象:當時父行程還被探針自己的
  #   sleep 撐著沒退出。**要驗背景行為,就不能讓父行程剛好還活著。**
  [ "${SL_REFRESH_MODE:-0}" = "1" ] && return 0
  [ -n "$SID" ] || return 0
  local dir="$EZ2_UI_STATE_ROOT/receipts/session-authority/$SID"
  [ -d "$dir" ] && [ ! -L "$dir" ] && [ -O "$dir" ] || return 0
  # 節流判準要跟「什麼會讓收據失效」對齊,不能只看秒數:重放比對的是 registry 檔的
  # 整檔 sha,所以**心跳一寫,再新的收據也立刻失配**。純時間節流會讓每次心跳後
  # 到節流到期之間持續降級。用 mtime 比較等價且便宜(不必解 JSON):收據比 registry
  # 檔新 = 這張是在最後一次心跳之後鑄的 = 還對得上。
  local newest now reg_mtime rc_mtime
  now=$(date +%s)
  newest=$(ls -t "$dir"/*-"$SL_MINT_TAG"*.json 2>/dev/null | head -1)
  if [ -n "$newest" ]; then
    rc_mtime=$(stat -f %m "$newest" 2>/dev/null || echo 0)
    reg_mtime=$(stat -f %m "$EZ2_UI_STATE_ROOT/sessions/by-id/$SID.json" 2>/dev/null || echo 0)
    if [ "$rc_mtime" -gt "$reg_mtime" ] \
      && [ $(( now - rc_mtime )) -lt "$SL_MINT_MIN_AGE" ]; then
      return 0
    fi
  fi
  # ★檔名要帶行程編號。原本只用秒數,兩支併發呼叫會算出同一個路徑:A 建好、B 因
  #   「檔案已存在」失敗,然後 B 的清理把 A 那張刪掉——實測併發六輪,六輪自鑄件都是 0。
  #   收據檔名格式固定是 <epoch>-<12 位十六進位>,所以把後半拆成「固定標記+本行程」。
  local out
  out=$(printf '%s/%s-%s%06x.json' "$dir" "$now" "$SL_MINT_TAG" "$$")
  [ -e "$out" ] && return 0
  # 鑄不成就什麼都不留:半張收據會被下一輪當成可用的最新件(實測 rc=2 的 blocked
  # 收據仍然會落檔)。
  if ! acb session-authority --current --receipt-out "$out" >/dev/null 2>&1; then
    rm -f "$out" 2>/dev/null
    return 0
  fi
  # 舊的自鑄件清掉,否則讀取端的 64 上限會在幾十輪之內把顯示鎖死。
  # ★★★只刪**比自己舊**的,不能刪「不是自己那張」的。狀態列會被並行呼叫,兩支
  #   同時鑄的話,原本那種寫法會互相把對方刪掉——A 保留 A 刪掉 B、B 保留 B 刪掉 A,
  #   交錯之後一張都不剩,顯示層就降級。實測:併發跑四輪,四輪都是自鑄件=0。
  #   改成只刪更舊的之後,最新那張一定有人留著,誰贏都不影響結果。
  local old om nm
  nm=$(stat -f %m "$out" 2>/dev/null || echo 0)
  for old in "$dir"/*-"$SL_MINT_TAG"*.json; do
    [ "$old" = "$out" ] && continue
    [ -e "$old" ] || continue
    om=$(stat -f %m "$old" 2>/dev/null || echo 0)
    [ "$om" -lt "$nm" ] && rm -f "$old" 2>/dev/null
  done
  return 0
}
sl_spawn_refresh() {  # 背景刷新;不繼承 stdin/stdout,不拖慢本次輸出
  # ★不要用 setsid:macOS 不內建它,而 `( setsid ... & )` 這個寫法無論它在不在
  #   都回 0,於是 `|| 後備` 永遠不會執行——背景刷新看起來有接、實際從沒跑起來。
  #   (實測:第一版就是這樣靜默失效的。)nohup 是 POSIX,macOS 有。
  [ "${SL_REFRESH_MODE:-0}" = "1" ] && return 0
  # 先在這裡(前景,祖先鏈完整)備好收據,背景那支只負責重放與算畫面。
  sl_mint_receipt
  nohup bash "$0" --refresh "$SID" </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || :
}
sl_age_tag() {  # 標齡字樣;白話,不用 jargon
  local a="$1"
  if [ "$a" -lt 60 ]; then printf '(身份 %s 秒前)' "$a"
  else printf '(身份 %s 分前)' $(( a / 60 )); fi
}
sl_verified_title_base() {
  local _raw _header _schema _sid _observed _expires _base_sha _extra
  local _base _actual_base _now _meta_before _meta_after _mode
  [ -s "$TITLE_GENERATION" ] && [ ! -L "$TITLE_GENERATION" ] \
    && [ -f "$TITLE_GENERATION" ] && [ -O "$TITLE_GENERATION" ] || return 1
  _meta_before=$(stat -f '%d %i %u %l %z %Lp %m' \
    "$TITLE_GENERATION" 2>/dev/null) || return 1
  set -- $_meta_before
  [ "$#" -eq 7 ] && [ "$3" = "$(id -u)" ] && [ "$4" = "1" ] \
    && [ "$5" -gt 0 ] && [ "$5" -le 4608 ] || return 1
  _mode=$((8#$6)); [ "$_mode" -eq $((8#600)) ] || return 1
  _raw=$(cat "$TITLE_GENERATION" 2>/dev/null) || return 1
  case "$_raw" in *$'\n'*) ;; *) return 1;; esac
  _header=${_raw%%$'\n'*}
  _base=${_raw#*$'\n'}
  IFS=' ' read -r _schema _sid _observed _expires _base_sha _extra \
    <<< "$_header"
  [ -z "$_extra" ] \
    && [ "$_schema" = "acb-claude-ui-title-fast/v1" ] \
    && [ "$_sid" = "$SID" ] || return 1
  case "$_observed:$_expires" in *[!0-9:]*|:*|*:) return 1;; esac
  case "$_base_sha" in *[!0-9a-f]*|'') return 1;; esac
  [ "${#_base_sha}" -eq 64 ] && [ -n "$_base" ] || return 1
  _now=$(date +%s)
  [ "$_observed" -le $(( _now + 5 )) ] \
    && [ "$_expires" -ge "$_now" ] \
    && [ $(( _expires - _observed )) -eq 90 ] || return 1
  _actual_base=$(printf '%s' "$_base" | /usr/bin/shasum -a 256 \
    | awk '{print $1}') || return 1
  [ "$_actual_base" = "$_base_sha" ] || return 1
  _meta_after=$(stat -f '%d %i %u %l %z %Lp %m' \
    "$TITLE_GENERATION" 2>/dev/null) || return 1
  [ "$_meta_after" = "$_meta_before" ] || return 1
  printf '%s' "$_base"
}

# sl_write_title <ttys名> <標題> — 標題寫入的唯一入口(WP-I,a seat,2026-08-01)
#
# ★為什麼改掉 osascript 主路徑(P7 缺陷根治,復現在手):
#   舊寫法 `windows whose tty is "/dev/X"` 是**用 tty 挑視窗**,而 Terminal 在
#   外殼結束後仍保留該視窗最後的 tty;macOS 又會回收 ttys 編號給新 shell。
#   於是「一個死視窗 + 一個活視窗持有同一 tty」是真實可復現的狀態,實測:
#     舊寫法 命中數=2 [id=189285 busy=true] [id=188904 busy=false]  <- 連死的一起寫
#     (另一次量到命中數=1 且只命中死的那個——它的行為在兩次量測間不一致)
#   兩種結果都錯:輕則活視窗標題不更新,重則**死視窗掛上活 session 的身分**,
#   而那正是這套標題機制要防的身分混淆。且 osascript 全程 exit 0,錯得無聲。
#
# 主路徑=OSC 逃逸序列直寫自己的 /dev/ttysN。
#   寫的是「裝置」不是「選出來的視窗」,誰接在上面誰收到,結構上不存在選錯對象。
#   實測同一碰撞情境:OSC 寫入後活視窗標題變更、死視窗原封不動。
#   附帶好處:實測 10 次平均 3ms,osascript 101ms(冷跑預算 161-185ms,這是改善不是負擔)。
#   另:不經 Apple Events=不吃 TCC(與 WP-H 的 daemon 同一技術理由)。
#
# fallback=osascript,但改成逐視窗比對並加**活性過濾**(busy is true),排除殘留死視窗。
#   保留 fallback 是因為 OSC 依賴 /dev/ttysN 可寫,非終端情境(重導向/無 tty)會失敗。
sl_write_title() {
  local _tty="$1" _title="$2"
  [ -n "$_tty" ] && [ "$_tty" != "??" ] || return 1
  # ★M1 縱深:寫進 OSC 前剝掉所有 C0 控制字元與 DEL。
  #   值裡一個 BEL 就提前終結這段序列,後面再帶 ESC]0; 就是第二個設標題指令
  #   ——契約上只是前綴的 .mark 因此能吃掉整個標題(6db35 R2 端到端實測判重大)。
  #   上游 sanitize_override 已補剝,這裡再剝一次是縱深:日後新增的標題成分
  #   不會因為忘了走上游就變成注入面。LC_ALL=C 讓 tr 按位元組處理不理會語系。
  _title=$(printf '%s' "$_title" | LC_ALL=C tr -d '\000-\037\177')
  # 主路徑:OSC。可寫才試,避免對不存在的裝置噴錯。
  if [ -w "/dev/$_tty" ] \
    && printf '\033]0;%s\007' "$_title" > "/dev/$_tty" 2>/dev/null; then
    return 0
  fi
  # fallback:活性過濾的 osascript。引號已由呼叫端剝除。
  osascript -e "tell application \"Terminal\"
    repeat with w from 1 to (count of windows)
      set t to tab 1 of window w
      if (tty of t is \"/dev/$_tty\") and (busy of t is true) then
        set custom title of t to \"$_title\"
      end if
    end repeat
  end tell" >/dev/null 2>&1
}

if [ "${SL_REFRESH_MODE:-0}" != "1" ]; then
  _age=$(sl_age)
  if [ "$_age" -ge 0 ] && [ "$_age" -lt "$SL_MAX" ]; then
    # 快取可用。★超過 SL_FRESH 就標齡——不標齡而照顯示等於把過期身份當現值。
    _tag=""
    if [ "$_age" -ge "$SL_FRESH" ]; then _tag=" $(sl_age_tag "$_age")"; sl_spawn_refresh; fi
    # 快取命中時**仍然重申標題**。原版在這裡直接 exit,於是每 10 秒有一個視窗完全不寫標題;
    # 而 Claude 自己的標題寫入實測約每 3 秒一次(探針:設 PROBE 後 3 秒內被蓋回,spinner 字元
    # 同時在變=活的寫入)。不重申等於把標題讓給對方。重申走快取的標題字串,不再跑 python。
    # 縱深三檢:非 symlink / 普通檔 / 擁有者是自己。目錄閘是主防線,這裡是備援
    # ——.mark 有六道而這個檔餵同一個表面,不該是零道(N1)。
    # Foreground 也只接受 sidecar 精確綁定的 0600 base。失敗 cache 的新 mtime
    # 不得替舊身份續命;generation/sidecar/base 任一不符即清全部舊表面,只寫 no identity。
    _t="$(sl_verified_title_base)"; _base_rc=$?
    if [ "$_base_rc" -ne 0 ] || [ -z "$_t" ]; then
      sl_clear_title_artifacts "$TITLE_DIR" "$SID"
      [ "$_age" -ge "$SL_FRESH" ] && sl_spawn_refresh
      sl_write_title "$ACB_SL_TTY" "● ACB | no identity" || :
      _b="${EZ2LINE:-● | ACB | 身份顯示收據無效,正在重新查}"
      [ -n "$FLEET_COCKPIT" ] && _b="$FLEET_COCKPIT"
      emit_with_override "$_b"; exit 0
    fi
    if [ -n "$_t" ]; then
      # ★基底帶的是誠實佔位 `CTX用 unknown`(刷新沒有 Ez2 段)。顯示前用前景這次
      #   量到的實時 CTX 段取代;沒量到就保留佔位——那是誠實的「不知道」,不是拿舊值頂。
      [ -n "$EZ2CTX_SEG" ] && _t="$_t | $EZ2CTX_SEG"
      _t="$_t$_tag"
      # `.title` 只保留前景實時視圖;daemon 不再讀它,只重申 receipt-bound base。
      printf '%s' "$_t" > "$TITLE_CACHE" 2>/dev/null || :
      _p=$(ps -o tty= -p $PPID 2>/dev/null | tr -d ' ')
      sl_write_title "$_p" "$_t"
    fi
    _b="${EZ2LINE:-$(sl_safe_cat "$CACHE")$_tag}"
    [ -n "$FLEET_COCKPIT" ] && _b="$FLEET_COCKPIT"
    emit_with_override "$_b"; exit 0
  fi
  # 到這裡=沒有快取,或快取已超過 SL_MAX。兩種都不同步等 436ms 的身份查詢:
  # 先出誠實畫面並在背景取,下一次呼叫(狀態列呼叫很密集)就有真值。
  # ★超過 SL_MAX 的舊身份**不顯示**——fail-closed,不是拿舊的頂著。
  sl_clear_title_artifacts "$TITLE_DIR" "$SID"
  sl_write_title "$ACB_SL_TTY" "● ACB | no identity" || :
  sl_spawn_refresh
  if [ "$_age" -ge "$SL_MAX" ]; then
    _b="${EZ2LINE:-● | ACB | 身份資料太舊了,正在重新查(上次是 $(( _age / 60 )) 分鐘前)}"
  else
    _b="${EZ2LINE:-● | ACB | 正在查身份…}"
  fi
  [ -n "$FLEET_COCKPIT" ] && _b="$FLEET_COCKPIT"
  emit_with_override "$_b"; exit 0
fi
if [ "${SL_REFRESH_MODE:-0}" = "1" ]; then
  sl_clear_fast_snapshot "$TITLE_DIR" "$SID"
fi
OUT=$(/usr/bin/python3 -I - "$SID" <<'PY' 2>/dev/null
import sys, os, json, subprocess, time, hashlib
prefix = os.environ.get("ACB_UI_PREFIX") or os.path.join(
    os.path.expanduser("~"), ".local")
if not os.path.isabs(prefix) or any(ch in prefix for ch in ("\x00", "\n", "\r", ":")):
    raise SystemExit(0)
sys.path.insert(0, os.path.join(prefix, "lib", "acb"))
from acb.session_title_facts import facts_from_authority
from acb.session_title import build_title_view, render_session_title
from acb.session_status_facet import classify
from acb.session_status_line import render_status_line
sid = sys.argv[1]
acb_bin = os.path.join(prefix, "bin", "acb")
# P9 降級分流:三種失敗要能分辨,否則只能給一句籠統的話,使用者不知道該修什麼。
# 分類寫進檔案讓 bash 取用;bash 這層沒有 timeout 指令(macOS 不內建),
# 逾時只有在這裡用 subprocess 的 timeout 才量得到。
# ★路徑跟著 CACHE 走,不各自寫死:bash 側算的是 ${CACHE%.cache}.reason,
# 這裡若硬編 /tmp 就會在 N1 遷移後分岔(實測:分流全部落 catch-all,六條測試同時紅)。
# 「同一件事寫在兩處就一定會分岔」——這次是它自己示範給我看。
_rdir = os.environ.get("ACB_TITLE_CACHE_DIR") or os.path.join(
    os.path.expanduser("~"), ".acb", "state", "title-cache")
reason_path = os.path.join(_rdir, "%s.reason" % (sid or "none"))
def _write_reason(tok):
    try:
        with open(reason_path, "w", encoding="utf-8") as fh:
            fh.write(tok)
    except OSError:
        pass
# ★ acb_bin 可能來自可攜 prefix。用 os.path.exists 只會檢查存在,
# 而 ACB repo 根目錄底下就有一個 acb/ 套件目錄——在那個 cwd 下 exists("acb") 恆真,
# missing 判定會失效。shutil.which 查的是 PATH(絕對路徑也照樣驗可執行),
# 與呼叫者站在哪無關。(a seat 複驗抓到,本機在 repo cwd 下實測復現。)
import re, shutil, stat

# Background refresh may be reparented before it reaches this block. It must
# therefore never ask for --current (which correctly requires a provider
# ancestor), and it must never reconstruct a launcher environment. Instead it
# replays one closed current receipt produced inside the provider, then checks
# that every stable identity field still matches a fresh registry record and
# the exact live provider process. The resulting object is display-only.
_uuid_re = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_receipt_name_re = re.compile(r"^[0-9]{1,20}-[a-f0-9]{12}\.json$")
_sha_re = re.compile(r"^[a-f0-9]{64}$")
_required_display_details = {
    "current_native_uuid_evidence_v2": "matched_provider_native_uuid",
    "launcher_session_uuid_binding": "matched_provider_native_uuid",
    "process_live_binding": "matched_caller_provider",
    "caller_process_anchor_evidence_v2": "matched_caller_provider",
    "display_identity_semantics_v3":
        "safe_current_native_and_unique_process_anchor",
}
_max_authority_receipts = 64
_display_receipt_ttl = 90

def _safe_dir(directory):
    try:
        info = os.lstat(directory)
        return (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.getuid()
                and not (stat.S_IMODE(info.st_mode) & 0o022))
    except OSError:
        return False

def _stable_registry(session_id):
    acb_home = os.path.join(os.path.expanduser("~"), ".acb")
    root = os.path.join(acb_home, "sessions", "by-id")
    target = os.path.join(root, "%s.json" % session_id)
    if (not all(_safe_dir(item) for item in
                (acb_home, os.path.join(acb_home, "sessions"), root))
            or os.path.islink(target)):
        return None, None, None
    fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= 128 * 1024):
            return None, None, None
        chunks = []
        remaining = before.st_size + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        current = os.lstat(target)
        if (len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)):
            return None, None, None
        return (json.loads(raw.decode("utf-8")), after,
                hashlib.sha256(raw).hexdigest())
    except (OSError, UnicodeError, ValueError):
        return None, None, None
    finally:
        if fd >= 0:
            os.close(fd)

def _stable_receipt(path):
    fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= 256 * 1024):
            return None
        chunks = []
        remaining = before.st_size + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        current = os.lstat(path)
        if (len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)
                or (current.st_dev, current.st_ino) !=
                    (after.st_dev, after.st_ino)
                or stat.S_ISLNK(current.st_mode)):
            return None
        return hashlib.sha256(raw).hexdigest()
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)

def _latest_receipt(session_id):
    acb_home = os.path.join(os.path.expanduser("~"), ".acb")
    receipts = os.path.join(acb_home, "receipts")
    root = os.path.join(receipts, "session-authority")
    directory = os.path.join(root, session_id)
    if not all(_safe_dir(item) for item in
               (acb_home, receipts, root, directory)):
        return None
    try:
        names = [name for name in os.listdir(directory)
                 if _receipt_name_re.fullmatch(name)]
    except OSError:
        return None
    if not names or len(names) > _max_authority_receipts:
        return None
    epochs = {name: int(name.split("-", 1)[0]) for name in names}
    newest_epoch = max(epochs.values())
    newest = [name for name in names if epochs[name] == newest_epoch]
    ranked = []
    for name in newest:
        target = os.path.join(directory, name)
        try:
            info = os.lstat(target)
        except OSError:
            return None
        ranked.append((info.st_mtime_ns, target))
    ranked.sort(reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return None
    digest = _stable_receipt(ranked[0][1])
    return (ranked[0][1], digest) if digest else None

def _provider_from_process_identity(comm, args):
    auxiliary = {
        "codex-code-mode-host", "claude-code-mode-host",
        "agy-code-mode-host", "antigravity-code-mode-host",
    }
    def executable_provider(value):
        executable = os.path.basename(value).lower()
        if executable.endswith(".js"):
            bare = executable[:-3]
        else:
            bare = executable
        if bare in auxiliary:
            return None
        if executable == "codex" or re.fullmatch(
                r"codex-(?:x86_64|aarch64)(?:-[a-z0-9_.-]+)?",
                executable):
            return "codex"
        if executable == "claude":
            return "claude"
        if executable in {"agy", "antigravity"}:
            return "agy"
        if executable == "copilot":
            return "copilot"
        return None
    provider = executable_provider(comm)
    if provider:
        return provider
    tokens = args[:2048].split(maxsplit=2)[:2]
    if not tokens:
        return None
    provider = executable_provider(tokens[0])
    if provider:
        return provider
    if os.path.basename(tokens[0]).lower() not in {"node", "nodejs"} \
            or len(tokens) < 2:
        return None
    launcher = tokens[1].lower()
    provider = executable_provider(launcher)
    if provider:
        return provider
    launcher_name = os.path.basename(launcher)
    if "/@anthropic-ai/claude-code/" in launcher \
            and launcher_name in {"claude.js", "cli.js"}:
        return "claude"
    if "/@openai/codex/" in launcher and launcher_name == "codex.js":
        return "codex"
    if "/@google/antigravity/" in launcher \
            and launcher_name in {"agy.js", "antigravity.js", "cli.js"}:
        return "agy"
    if "/@github/copilot/" in launcher \
            and launcher_name in {"copilot.js", "npm-loader.js", "index.js"}:
        return "copilot"
    return None

def _live_claude_process(registry):
    pid = registry.get("pid")
    started = registry.get("process_started_epoch")
    if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1
            or isinstance(started, bool) or not isinstance(started, int)
            or started <= 0):
        return False
    command = [
        "/bin/ps", "-ww", "-p", str(pid), "-o", "pid=", "-o", "uid=",
        "-o", "tty=", "-o", "lstart=", "-o", "comm=", "-o", "args=",
    ]
    snapshots = []
    for _ in range(2):
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=2,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError):
            return False
        lines = proc.stdout.rstrip("\n").splitlines() \
            if proc.returncode == 0 else []
        if len(lines) != 1:
            return False
        match = re.fullmatch(
            r"\s*(\d+)\s+(\d+)\s+(\S+)\s+"
            r"([A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] "
            r"[0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4})\s+"
            r"(\S+)\s+(.+)", lines[0])
        if not match:
            return False
        observed_pid, uid, tty, started_text, comm, args = match.groups()
        try:
            observed = (
                int(observed_pid), int(uid), tty,
                int(time.mktime(time.strptime(
                    started_text, "%a %b %d %H:%M:%S %Y"))),
                _provider_from_process_identity(comm, args),
            )
        except (OverflowError, ValueError):
            return False
        snapshots.append(observed)
    if snapshots[0] != snapshots[1]:
        return False
    observed_pid, uid, tty, observed_started, provider = snapshots[0]
    registry_tty = registry.get("tty")
    tty_safe = tty in {"?", "??"} or re.fullmatch(
        r"(?:tty[A-Za-z0-9._-]{0,63}|pts/[0-9]{1,10})", tty) is not None
    return (observed_pid == pid and uid == os.getuid()
            and observed_started == started and provider == "claude"
            and tty_safe
            and (registry_tty in (None, "", "?", "??")
                 or registry_tty == tty))

def _receipt_matches_registry(authority, registry, registry_info,
                              registry_sha, session_id):
    session = authority.get("session") if isinstance(authority, dict) else None
    receipt_registry = authority.get("registry") if isinstance(authority, dict) else None
    if (not isinstance(session, dict) or not isinstance(registry, dict)
            or not isinstance(receipt_registry, dict)):
        return False
    beat = registry.get("beat_epoch", registry.get("heartbeat_epoch"))
    now = int(time.time())
    resolved = authority.get("resolved_at_epoch")
    if (isinstance(beat, bool) or not isinstance(beat, int)
            or not -5 <= now - beat <= 900
            or abs(int(registry_info.st_mtime) - beat) > 10
            or isinstance(resolved, bool) or not isinstance(resolved, int)
            or not -5 <= now - resolved <= _display_receipt_ttl
            or _sha_re.fullmatch(str(registry_sha or "")) is None):
        return False
    checks = authority.get("checks", ())
    if not isinstance(checks, list):
        return False
    required = [item for item in checks
                if isinstance(item, dict)
                and item.get("required_for_registry_verified") is True]
    by_name = {}
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return False
        if item["name"] in by_name:
            return False
        by_name[item["name"]] = item
    for name, detail in _required_display_details.items():
        item = by_name.get(name)
        if not (isinstance(item, dict)
                and item.get("required_for_registry_verified") is True
                and item.get("status") == "passed"
                and item.get("detail") == detail):
            return False
    if not (authority.get("schema_version") == "acb-session-authority/v1"
            and authority.get("requested_session_uuid") == session_id
            and authority.get("status") == "passed"
            and authority.get("trust_level") == "registry_verified"
            and authority.get("query_mode") == "current"
            and authority.get("event_group_credit") is True
            and authority.get("blocking_error") is None
            and required and all(item.get("status") == "passed" for item in required)
            and _sha_re.fullmatch(str(authority.get("receipt_sha256") or ""))):
        return False
    expected_ai_id = "claude:%s:%s" % (
        registry.get("host_namespace"), session_id)
    registry_root = os.path.join(
        os.path.expanduser("~"), ".acb", "sessions", "by-id")
    if not (receipt_registry.get("root") == registry_root
            and receipt_registry.get("record_path")
                == os.path.join(registry_root, "%s.json" % session_id)
            and receipt_registry.get("record_sha256") == registry_sha
            and receipt_registry.get("record_mtime_epoch")
                == int(registry_info.st_mtime)
            and receipt_registry.get("record_mode") == "0600"
            and receipt_registry.get("record_owner_uid") == os.getuid()):
        return False
    if not (registry.get("session_uuid") == session_id
            and registry.get("session_status") == "active"
            and ("provider" not in registry
                 or registry.get("provider") == "claude")
            and registry.get("ai_id") == expected_ai_id):
        return False
    pairs = (
        (session.get("session_uuid"), session_id),
        (session.get("registry_filename_session_uuid"), session_id),
        (session.get("provider"), "claude"),
        (session.get("pid"), registry.get("pid")),
        (session.get("process_started_epoch"), registry.get("process_started_epoch")),
        (session.get("sender_sig"), registry.get("sig")),
        (session.get("sender_ai_id"), registry.get("ai_id")),
        (session.get("host_namespace"), registry.get("host_namespace")),
        (session.get("ai_id_host_namespace"), registry.get("host_namespace")),
        (session.get("role"), registry.get("acb_role")),
        (session.get("engine"), registry.get("engine")),
        (session.get("binding_schema_version"), registry.get("binding_schema_version")),
        (session.get("binding_uid"), registry.get("binding_uid")),
        (session.get("binding_sha256"), registry.get("binding_sha256")),
        (session.get("persona_uid"), registry.get("persona_uid")),
        (session.get("heartbeat_epoch"), beat),
    )
    return all(observed == expected for observed, expected in pairs)

def _display_authority(session_id):
    registry, registry_info, registry_sha = _stable_registry(session_id)
    if registry is None:
        return None
    if not _live_claude_process(registry):
        return None
    # 白名單環境仍需兩件事才叫得動 acb(08-03 a seat 實測,缺一即 rc=1):
    # ACB_DATA 缺席時 acb 落回 /var/acb(Linux hub 慣例)在 Mac 上建目錄失敗;
    # flock 在 macOS 只存在於 Homebrew/local 目錄。值仍為決定值,不從呼叫環境
    # 繼承——與本檔其餘 registry 讀取同錨 ~/.acb,免得驗的和讀的不是同一個店。
    observer_env = {
        "HOME": os.path.expanduser("~"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
        "LC_ALL": "C",
    }
    selected = _latest_receipt(session_id)
    if selected is None:
        return None
    receipt, before_sha = selected
    try:
        proc = subprocess.run(
            [acb_bin, "session-authority", "--verify-receipt", receipt,
             "--require-verified", "--json"],
            capture_output=True, text=True, timeout=2,
            cwd=os.path.expanduser("~"), env=observer_env,
        )
    except subprocess.TimeoutExpired:
        raise
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        candidate = json.loads(proc.stdout)
    except (TypeError, ValueError):
        return None
    after = _latest_receipt(session_id)
    registry_after, registry_info_after, registry_sha_after = \
        _stable_registry(session_id)
    if (after is None or after[0] != receipt or after[1] != before_sha
            or registry_after is None or registry_sha_after != registry_sha
            or (registry_info_after.st_dev, registry_info_after.st_ino,
                registry_info_after.st_size, registry_info_after.st_mtime_ns)
                != (registry_info.st_dev, registry_info.st_ino,
                    registry_info.st_size, registry_info.st_mtime_ns)
            or not _receipt_matches_registry(
                candidate, registry, registry_info, registry_sha, session_id)):
        return None
    return {
        "receipt": candidate,
        "registry": registry,
        "display_only": True,
        "event_group_credit": False,
        "display_source": "closed_current_authority_receipt",
    }

try:
    if shutil.which(acb_bin) is None:
        _write_reason("missing")
        raise SystemExit(0)
    if not _uuid_re.fullmatch(sid):
        _write_reason("error")
        raise SystemExit(0)
    display_snapshot = _display_authority(sid)
except subprocess.TimeoutExpired:
    _write_reason("timeout")
    raise SystemExit(0)
except (FileNotFoundError, OSError):
    _write_reason("missing")
    raise SystemExit(0)
if display_snapshot is None:
    _write_reason("error")
    raise SystemExit(0)
# The renderer consumes the immutable receipt snapshot. The observer-facing
# projection above remains display-only and never exposes event credit as its
# own capability.
auth = display_snapshot["receipt"]
display_registry = display_snapshot["registry"]
try:
    os.unlink(reason_path)
except OSError:
    pass
facts, gaps = facts_from_authority(auth)
now = int(time.time())
view = build_title_view(facts, now_epoch=now)
status_text = render_status_line(view, classify(auth)).text
title = render_session_title(facts, now_epoch=now)
# ★刷新模式=只寫身份快取,標題表面(OSC/title cache)一律不碰——它沒有 tty 也沒有
# Ez2 段,寫出來的是殘缺標題;曾把好標題蓋成「無 tty+佔位還魂」(the maintainer 二度抓到
# CTX 消失)。與 932a558 同一條哲學:背景路徑不得越權寫顯示表面/量測檔。
_refresh_only = os.environ.get("SL_REFRESH_MODE") == "1"
# Identity base and context gauge have different authorities and lifetimes.
# Keep the receipt-bound base identity-only; foreground and daemon append only
# a fresh provider gauge. An unavailable gauge is omitted, never rendered as a
# repeatedly restated `CTX用 unknown` value.
title = title.replace(" | CTX用 unknown", "")
# 佔位段落剝除(the maintainer 拍板 08-01):conv(P3 無 provider 源)與 剩(P5 過期集合不完備)
# 的誠實 unknown/BLOCKED 對標題是常駐噪音。只剝「恰好等於佔位字樣」的段,
# 一旦上游有真值(conv abc123 / 剩 45:00)字樣不同,自動浮現——不是把 unknown 畫成綠。
for _ph in (" | conv unknown", " | 剩 BLOCKED"):
    title = title.replace(_ph, "")
import subprocess as sp
ptty = os.environ.get("ACB_SL_TTY", "").strip()
if not ptty or ptty == "??":
    ptty = sp.run(["ps", "-o", "tty=", "-p", str(os.getppid())], capture_output=True, text=True).stdout.strip()
# tty 前綴(the maintainer 拍板 08-01):量測自本次呼叫的 PPID,不是寫死;無 tty(非終端呼叫)就不加,
# 不顯示猜的值。放在覆蓋 apply 之前=覆蓋的「整個取代」契約不變。
if ptty and ptty != "??":
    title = ptty + " | " + title
# 艦隊警報燈:協作 AI 有人危急=標題最左紅點(分頁列永遠看得到的唯一位置)。
if os.environ.get("FLEET_ALERT") == "1":
    title = "\U0001f534" + title
# 角色標記(the maintainer 拍板 08-01「列出你是主」):每 session 標記檔 <sid>.mark 同目錄同防禦,
# 內容即標記(主/副/…),放最左。無檔=不顯示,不猜。
from acb.session_title_override import override_path, _read_override_file, sanitize_override
_mp = override_path(sid or "none")
if _mp:
    _mark = sanitize_override(_read_override_file(_mp + ".mark") or "")
    if _mark:
        title = _mark + " | " + title
# 每 session 覆蓋:判準正本=acb.session_title_override(repo 分支 pm0bbe0/title-override-2151)。
# 覆蓋值照原流程進 title 快取,快取命中的重申路徑自動跟上。
from acb.session_title_override import read_override, apply_to_title
title = apply_to_title(title, read_override(sid or "none"))
if _refresh_only:
    ptty = ""
if ptty and ptty != "??":
    # 與 bash 的 sl_write_title 同語意:OSC 直寫自己的 tty 為主,osascript 僅 fallback
    # 且加活性過濾。理由見該函式的註解(死視窗會持有同一 tty,舊寫法會寫錯對象)。
    # ★M1 縱深(與 bash sl_write_title 同語意):進 OSC 前剝 C0 控制字元與 DEL。
    _t = "".join(c for c in title if ord(c) >= 0x20 and ord(c) != 0x7F).replace('"', '')
    _written = False
    try:
        with open("/dev/%s" % ptty, "w") as _fh:
            _fh.write("\033]0;%s\007" % _t)
        _written = True
    except OSError:
        _written = False
    if not _written:
        osa = ('tell application "Terminal"\n'
               '  repeat with w from 1 to (count of windows)\n'
               '    set t to tab 1 of window w\n'
               '    if (tty of t is "/dev/%s") and (busy of t is true) then\n'
               '      set custom title of t to "%s"\n'
               '    end if\n'
               '  end repeat\n'
               'end tell') % (ptty, _t)
        sp.run(["osascript", "-e", osa], capture_output=True, timeout=5)
# 標題另存一份,讓快取命中的那一路不必重跑 python 就能重申標題。
#
# ★2026-08-01 a seat 殘案修正:這裡原本是 `None if _refresh_only else ...`,
#   而 WP-D 之後**前景永遠不跑這段 python**(沒快取就 spawn 背景刷新後直接出畫面),
#   於是唯一還會執行到這裡的路徑就是刷新模式——被這個條件擋掉的結果是
#   **沒有任何路徑會寫 .title**,快取命中的重申路徑(bash 那邊 `[ -s "$TITLE_CACHE" ]`)
#   永遠不觸發。兩個各自正確的改動,交集把唯一的路徑消掉了。
#
#   復現:真 SID + 移除本條件 -> .title 生成;保留條件 -> .cache 正常寫、.title 永不生成。
#
# ★但原條件擋的問題是真的,不能直接拿掉:刷新模式沒有 Ez2 段,
#   它算出來的標題帶的是誠實佔位 `CTX用 unknown`(實測內容:
#   `👤組員 | a seat/Claude | 首啟 08/01 01:19 | CTX用 unknown`)。
#   把它當顯示值寫上畫面 = the maintainer 二度抓到的「CTX 消失」。
#
# 解法=分離「快取」與「顯示」為**兩個檔**(F1 定案,a seat):
#   .title.base=本段無條件寫的 receipt-bound 基底;.title=前景顯示值,由基底重組,
#   daemon 只穩定讀 0600 base 並驗 sidecar hash。單檔共用時「顯示值回寫」會讓下一次重組變純追加
#   (佔位已不在),CTX 段與齡標逐次累積——治具三跑三份,收據見
#   plans/tj1nr3/review-statusline-full-read-pm10342.md。
#   刷新模式仍然不碰顯示表面(上面 `if _refresh_only: ptty = ""` 那條不動)。
# N1:與 bash 同一個 0700 目錄(單一真相源;路徑寫兩處必分岔)。
# ★這段必須頂格:它是無條件執行的——P20 的結論是「刷新模式**要**寫快取」,
#   把它放進任何條件區塊都會讓基底再次無人生成(我 N1 遷移時就這樣復活了 P20,
#   使用者的標題與底部身份列同時消失才發現)。
_tdir = os.environ.get("ACB_TITLE_CACHE_DIR") or os.path.join(
    os.path.expanduser("~"), ".acb", "state", "title-cache")
try:
    if not os.path.isdir(_tdir):
        os.makedirs(_tdir, exist_ok=True)
        os.chmod(_tdir, 0o700)
except OSError:
    pass
title_cache = os.path.join(_tdir, "%s.title" % (sid or "none"))
title_base = os.path.join(_tdir, "%s.title.base" % (sid or "none"))
title_display = os.path.join(
    _tdir, "%s.title.display.json" % (sid or "none"))
title_generation = os.path.join(
    _tdir, "%s.title.display.generation" % (sid or "none"))
def _clear_title_artifacts():
    if not os.path.isabs(_tdir) or not _safe_dir(_tdir):
        return
    for path in (title_cache, title_base, title_display, title_generation):
        try:
            info = os.lstat(path)
            if ((stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
                    and info.st_uid == os.getuid()):
                os.unlink(path)
        except OSError:
            pass

def _atomic_private(path, raw):
    if (not isinstance(raw, bytes) or not raw or not os.path.isabs(path)
            or not _safe_dir(os.path.dirname(path))):
        return False
    tmp = "%s.tmp-%s-%s" % (path, os.getpid(), time.time_ns())
    fd = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL \
            | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short private display write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass

_safe_title = "".join(
    char for char in title
    if ord(char) >= 0x20 and not 0x7F <= ord(char) <= 0x9F)
_base_raw = title.encode("utf-8")
if (_safe_title != title or title.strip() != title
        or not 0 < len(_base_raw) <= 4096
        or not _atomic_private(title_base, _base_raw)):
    _clear_title_artifacts()
    _write_reason("error")
    raise SystemExit(0)
# `.title` is foreground-only. Initialise it for cold start, but the daemon
# never trusts or replays it; the receipt-bound 0600 base below is its sole text.
if title_cache and not os.path.exists(title_cache):
    _atomic_private(title_cache, _base_raw)

# Claude's launchd/nohup title re-stater is an observer, not an authority
# caller. Give it only a short-lived display projection bound to the exact
# registry tuple checked above. This is a bounded snapshot: it is not an event
# credit, an immediate-revocation mechanism, or protection from the same OS
# account. The daemon never replays the source receipt itself.
_projection = {
    "schema_version": "acb-claude-ui-display/v1",
    "display_only": True,
    "event_group_credit": False,
    "provider": "claude",
    "session_uuid": sid,
    "pid": display_registry.get("pid"),
    "process_started_epoch": display_registry.get("process_started_epoch"),
    "host_namespace": display_registry.get("host_namespace"),
    "sender_ai_id": display_registry.get("ai_id"),
    "sender_sig": display_registry.get("sig"),
    "role": display_registry.get("acb_role"),
    "engine": display_registry.get("engine"),
    "binding_schema_version": display_registry.get("binding_schema_version"),
    "binding_uid": display_registry.get("binding_uid"),
    "binding_sha256": display_registry.get("binding_sha256"),
    "persona_uid": display_registry.get("persona_uid"),
    "heartbeat_epoch": display_registry.get(
        "beat_epoch", display_registry.get("heartbeat_epoch")),
    "observed_at_epoch": now,
    "expires_at_epoch": now + 90,
    "source_receipt_sha256": auth.get("receipt_sha256"),
    "title_base_sha256": hashlib.sha256(_base_raw).hexdigest(),
}
_projection_raw = (json.dumps(
    _projection, ensure_ascii=False, sort_keys=True,
    separators=(",", ":")) + "\n").encode("utf-8")
if not _atomic_private(title_display, _projection_raw):
    _clear_title_artifacts()
    _write_reason("error")
    raise SystemExit(0)
_generation_raw = (
    "acb-claude-ui-title-fast/v1 %s %d %d %s\n" % (
        sid, now, now + 90, hashlib.sha256(_base_raw).hexdigest())
).encode("ascii") + _base_raw
if not _atomic_private(title_generation, _generation_raw):
    _clear_title_artifacts()
    _write_reason("error")
    raise SystemExit(0)
print(status_text)
PY
)
if [ -z "$OUT" ]; then
  # A failed receipt refresh must revoke its bounded display projection now;
  # the title daemon may keep the explicit degraded path, but cannot continue
  # restating a formerly verified healthy identity.
  sl_clear_title_artifacts "$TITLE_DIR" "$SID"
  # P9 降級文案三情境分流。原本三種失敗共用一句
  # "authority unreachable (honest blank, not fake green)"——它誠實但沒有可行動性:
  # 看到的人不知道是自己沒裝、還是裝了查不到、還是只是慢。三句話各自指向不同的下一步。
  REASON_FILE="${CACHE%.cache}.reason"
  _why=$(sl_safe_cat "$REASON_FILE")
  case "$_why" in
    missing) OUT="● | ACB | 沒有裝 acb,所以查不到身份" ;;
    timeout) OUT="● | ACB | 查身份等太久了,先不顯示" ;;
    error)   OUT="● | ACB | acb 在,但查不出這個 session 的身份" ;;
    *)       OUT="● | ACB | 身份查詢沒有回應,先不顯示" ;;
  esac
  # ★F2:背景路徑不寫顯示表面(8eb1250 立的不變量,python 側有閘、這條 bash 退路沒有)。
  #   實務窗口極窄(nohup reparent 後 tty=?? 被 sl_write_title 擋),但手動在終端跑
  #   --refresh 會把降級標題寫上自己的終端——同一不變量不該有沒關的門。
  if [ "${SL_REFRESH_MODE:-0}" != "1" ]; then
    PTTY=$(ps -o tty= -p $PPID | tr -d ' ')
    # fallback 也吃覆蓋檔,否則 authority 短暫壞掉時標題會閃回 no identity。
    _FT="● ACB | no identity"
    _OV=$(override_value)
    [ -n "$_OV" ] && _FT="$_OV"
    sl_write_title "$PTTY" "$_FT"
  fi
fi
printf '%s' "$OUT" > "$CACHE"
# 刷新模式只負責把快取寫新,不輸出——它是背景跑的,輸出沒有人接。
[ "${SL_REFRESH_MODE:-0}" = "1" ] && exit 0
# 2026-09-21 the maintainer：AIB 艙內隱藏底部「Model | Ez2 CTX」紅框列。
# 判定：實例家目錄／艙環境，或 tmux socket 名含 aihub-（AIB 右欄巢狀 CLI）。
_aib=0
[ -n "${EZ2_AIB_INSTANCE_HOME:-}${AIB_INSTANCE:-}${EZ2_AIB_INSTANCE:-}" ] && _aib=1
case "${TMUX:-}" in *aihub-*) _aib=1 ;; esac
if [ "$_aib" = "1" ]; then
  emit_with_override ""
  exit 0
fi
_b="${EZ2LINE:-$OUT}"
[ -n "$FLEET_COCKPIT" ] && _b="$FLEET_COCKPIT"
emit_with_override "$_b"
