#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Environment names that belong to one live provider/session and must never
# bleed into a newly composed AIB cockpit.

AIB_INSTANCE_VARS=(
  EZ2_AIB_INSTANCE
  EZ2_AIB_INSTANCE_HOME
  EZ2_AIB_INSTANCE_TOKEN
  EZ2_TMUX_SESSION
  EZ2_AIHUB_SOCKET
  EZ2_COCKPIT_STATE_DIR
  EZ2_TASKS_DIR
  EZ2_BOARD_DIR
  EZ2_SENTINEL_STATE
  EZ2_AIB_STATE_DIR
)

AIB_IDENTITY_VARS=(
  EZ2_PERSONA
  EZ2_PERSONA_SOURCE
  EZ2_SESSION_ROLE
  EZ2_SESSION_SIG
  EZ2_SESSION_AI_ID
  EZ2_SESSION_UUID
  EZ2_SESSION_ENGINE
  EZ2_SESSION_PROVIDER
  EZ2_SESSION_MODE
  EZ2_RDL_LANE
  EZ2_LANE
  AIB_CODEXBOT_THREAD_ID
  AIB_CODEXBOT_ENDPOINT
  AIB_CODEXAPP_THREAD_ID
  AIB_CODEXAPP_ENDPOINT
  CODEX_THREAD_ID
  CODEX_SESSION_ID
  CLAUDE_CODE_SESSION_ID
  CLAUDE_SESSION_ID
  AGY_SESSION_UUID
  AGY_SESSION_ID
  COPILOT_AGENT_SESSION_ID
)

# ★ 2026-09-08(第二審 Gemini 抓到):用量准入的逃生門也要洗。
#   它是**一次性的旗標**,不是身份 —— 但不洗掉的話,帶著它開出來的席會把它
#   繼承下去,那一席之後開的每一席都靜靜地繞過准入閘,而**沒有任何一格會叫**。
#   同一族的教訓已經有兩條(pm 窗開 dev 席繼承 pm 的 SESSION_ID、
#   unset 不是隔離)—— 這是第三條,形狀一樣:一個只該活一次的值活了下去。
AIB_ONESHOT_FLAG_VARS=(
  AIB_IGNORE_USAGE
)

AIB_CARRIER_SCRUB_VARS=(
  "${AIB_INSTANCE_VARS[@]}"
  "${AIB_IDENTITY_VARS[@]}"
  "${AIB_ONESHOT_FLAG_VARS[@]}"
)

# ============================================================================
# 環境門(艙門):只有**宣告過**的東西過得去(2026-09-25,the maintainer 令「從根本上修」)
#
# ★ 名字叫 AIB_ENV_DOOR_* 不叫 AIB_CABIN_DOOR_*:`AIB_CABIN_*` 是
#   `cabin_resolve.sh` 的命名空間,tests/test-cabin-resolution.py 的
#   「resolver 內部常數外流」那一格在守它。我第一版撞了名字,被它擋下來 ——
#   對的處置是改我的名字,不是去放寬那個閘。
# ============================================================================
#
# ## 它修的是什麼
#
# 上面那份 `AIB_CARRIER_SCRUB_VARS` 是**黑名單**,而且它掛在**窗格指令**上
# (`cockpit-full.sh:pane_command`)。tmux **伺服器**本身沒有任何洗 ——
# 伺服器拿到的是「誰跑 aib 就是誰的殼」,而 tmux 的全域環境是之後每一個
# 新窗格的底。洗了碗,沒洗鍋。
#
# 09-25 的實例:艙 aihub-20260921-150346-d6a8 是從一席 AI 的工具殼開出來的,
# 於是整組「把顏色關掉」(NO_COLOR/FORCE_COLOR/CLICOLOR/CLICOLOR_FORCE)
# 進了伺服器,那一艙開的每個 Claude 席**整個介面都是黑白的**;同一包裡還有
# 上一代 grok 會話的 GROK_SESSION_ID、開艙者的 TMUX_PANE、
# 以及疊了三次的 PYTHONPATH。量到的數字:人從真終端機開的伺服器 34 個
# 全域變數,那一艙 92 個。
#
# ## ★ 為什麼改成白名單(這是本段唯一重要的決定)
#
# 黑名單**結構上**補不完:它是用名字列的,所以它看不見
#   (a) 還沒有人遇過的變數(NO_COLOR 這類外觀旗標從來不在母體裡),
#   (b) 新廠商的身份變數(grok 08-31 登記成 provider,改了 7 個檔,
#       這份名單不在裡面 —— GROK_SESSION_ID 至今沒被洗),
#   (c) 會累積的值(PYTHONPATH 每套一層就長一段)。
# 這份名單 08-09 開檔 39 個名字,七週長到 45 個,每一個都是出事之後補的。
#
# ★★ 兩種名單的**失效方向相反**,這才是換邊的理由:
#     黑名單漏一個 → 髒東西靜靜地進去,沒有任何一格會紅(失效是綠的);
#     白名單漏一個 → 那個東西當場不見,拿它的人立刻壞掉(失效是紅的)。
#     **會安靜失敗的那一種不該當守衛。**
#
# ## 帶什麼過去
#
# 1. `AIB_ENV_DOOR_BASICS` —— 行程基本盤。**這份不是我挑的**:它是
#    2026-09-25 從**人用真終端機開的**那台 tmux 伺服器實測的 34 個名字
#    (`tmux -S …/default show-environment -g`)為底,再加幾個「有設才帶」
#    的(COLORTERM/LC_*/TZ/GPG_TTY)。拿健康的對照組當基準,不是憑印象。
#    ★ SECURITYSESSIONID / XPC_* 不能拿掉:那是 macOS 的工作階段,
#      拿掉之後席位裡的 security / gh 認不得鑰匙圈,而那個壞法很難追。
# 2. 本專案命名空間 `EZ2_*` / `AIB_*` —— **減掉**上面那份
#    `AIB_CARRIER_SCRUB_VARS`(身份與一次性旗標,它們本來就不該過)。
#    ⇒ 兩份名單**合作**,不是互相取代:黑名單繼續管「我們自己的東西裡
#      哪些只能活一次」,白名單管「別人的東西一律不過」。
# 3. 逃生門 `AIB_ENV_DOOR_PASSTHROUGH="NAME1 NAME2"` —— 額外放行,
#    **並且會印在 stderr**。沒有逃生門的閘會被整個關掉;而安靜的逃生門
#    等於沒有閘。
#
# ## 已知的限制(寫出來,不假裝沒有)
#
# 規則 2 認的是名字前綴,所以**分不出**「EZ2_/AIB_ 是我們設的」還是
# 「某個 AI 工具的設定檔剛好設了同前綴的變數」(例如 ~/.claude/settings.json 的 env 段),
# 那種也會被當成我們自己的東西帶過去。
# 這是前綴規則的性質,不是漏洞;要分得出來得讓艙自己宣告它匯出了什麼,
# 那是另一件事。
#
# PYTHONPATH **刻意不帶**:`cockpit-env.sh:303` 會用 `$EZ2_COCKPIT_DIR`
# 重新推導它,而每個窗格腳本都 source 了那支檔(pane-right-tabs.sh:5 等)。
# 不帶 = 順手治掉「每套一層就多一段」的累積。

AIB_ENV_DOOR_BASICS=(
  # 殼與使用者
  HOME PATH USER LOGNAME SHELL SHLVL PWD OLDPWD
  # 終端機與語系(COLORTERM 決定右欄能不能用 24-bit 色)
  TERM TERM_PROGRAM TERM_PROGRAM_VERSION TERM_SESSION_ID COLORTERM
  LANG LC_ALL LC_CTYPE TZ
  # macOS 工作階段:鑰匙圈與 launchd 認得這台機器上的「你」靠這幾個
  SECURITYSESSIONID XPC_FLAGS XPC_SERVICE_NAME LaunchInstanceID
  __CFBundleIdentifier __CF_USER_TEXT_ENCODING
  # 工具鏈
  # ★ TMUX_TMPDIR 非帶不可:tmux 用它決定 socket 目錄。漏掉它的那一版,
  #   沙箱艙的 server 生在預設目錄而臂去沙箱目錄找 ⇒「no server running」。
  #   這個漏是**白名單自己當場抓到的**(tests/test-smoke-cabin-to-screen.py
  #   一跑就紅),而同樣的漏若發生在黑名單那一側是靜悄悄的 —— 兩種名單的
  #   失效方向相反,這一次剛好把那句話證了一遍。
  TMUX_TMPDIR
  TMPDIR SSH_AUTH_SOCK GPG_TTY
  HOMEBREW_PREFIX HOMEBREW_CELLAR HOMEBREW_REPOSITORY
  FPATH INFOPATH ANDROID_HOME
)

AIB_ENV_DOOR_PREFIXES=( EZ2_ AIB_ )

# ★ 10-24(@abb89,右鍵另開 AI 實測):只給「這一次開席」用的接回 / 分出旗標,不准過門進 tmux 伺服器。
#   艙重開時 cabin_restart 帶 AIB_REVIVE_SESSION_UUID=<舊席> 跑 aib;若這一次剛好生出 tmux 伺服器,
#   它就留在伺服器全域環境 ⇒ 之後每個 run-shell(右鍵另開 AI)都想原地復活那一席,活著就被擋,
#   右鍵開任何一家都失敗、畫面沒有一個字。新席要的接回值走 AIB_SEAT_ENV 明寫,不靠伺服器環境。
#   不放進 AIB_CARRIER_SCRUB_VARS:那份也管窗格指令,這幾個在 aib 本行程裡還要讀。
AIB_ENV_DOOR_DENY_VARS=(
  AIB_REVIVE_SESSION_UUID
  AIB_RESUME_SESSION_UUID
  AIB_RESUME_UUID
  AIB_RESUME_FORCE
  AIB_FORK_FROM_SESSION_UUID
)

aib_env_door_is_scrubbed() {
  local n want="$1"
  for n in "${AIB_CARRIER_SCRUB_VARS[@]}" "${AIB_ENV_DOOR_DENY_VARS[@]}"; do
    [ "$n" = "$want" ] && return 0
  done
  return 1
}

# 印出要帶過門的名字,每行一個(純輸出,不改環境;好測)。
aib_env_door_names() {
  local n pfx keep
  for n in "${AIB_ENV_DOOR_BASICS[@]}"; do
    [ -n "${!n+x}" ] && printf '%s\n' "$n"
  done
  # compgen -e 只列**已匯出**的名字(bash 3.2 實測:未匯出的不會出現)
  for n in $(compgen -e 2>/dev/null); do
    keep=0
    for pfx in "${AIB_ENV_DOOR_PREFIXES[@]}"; do
      case "$n" in "$pfx"*) keep=1 ;; esac
    done
    [ "$keep" = "1" ] || continue
    aib_env_door_is_scrubbed "$n" && continue
    printf '%s\n' "$n"
  done
  for n in ${AIB_ENV_DOOR_PASSTHROUGH:-}; do
    [ -n "${!n+x}" ] && printf '%s\n' "$n"
  done
}

# 填 AIB_ENV_DOOR_ENV=( NAME=VALUE … ),供 `env -i "${AIB_ENV_DOOR_ENV[@]}"` 用。
# ★ 不回字串:值裡有空白/引號時,字串化再由殼重新分詞就是另一個 bug
#   (本庫記過 `zsh 對 $VAR 不分詞` 那一條,方向相反但同一族)。
aib_env_door_build() {
  AIB_ENV_DOOR_ENV=()
  local n seen=""
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    case " $seen " in *" $n "*) continue ;; esac
    seen="$seen $n"
    AIB_ENV_DOOR_ENV+=( "$n=${!n}" )
  done <<EOF_DOOR
$(aib_env_door_names)
EOF_DOOR
  if [ -n "${AIB_ENV_DOOR_PASSTHROUGH:-}" ]; then
    echo "aib: 艙門逃生門放行:${AIB_ENV_DOOR_PASSTHROUGH}" >&2
  fi
}

# 開艙時印出「擋下了什麼」。沒有這一句,洗乾淨與沒洗在畫面上逐字相同。
aib_env_door_report_dropped() {
  local n carried="" dropped=""
  for n in "${AIB_ENV_DOOR_ENV[@]}"; do carried="$carried ${n%%=*}"; done
  for n in $(compgen -e 2>/dev/null); do
    case " $carried " in *" $n "*) continue ;; esac
    dropped="$dropped $n"
  done
  [ -n "$dropped" ] || return 0
  # 10-17 fishmac:「擋下 N 個外來變數」像出錯(多半是舊安裝或別的工具留下的環境變數)⇒ 照舊列出,但說清楚不影響使用
  echo "aib: 開艙時沒帶進 $(printf '%s' "$dropped" | wc -w | tr -d ' ') 個外面的環境變數(舊安裝或別的工具留下的,不影響使用):$dropped" >&2
}

aib_scrub_carrier_env() {
  unset "${AIB_CARRIER_SCRUB_VARS[@]}" 2>/dev/null || true
}

# CodexBot desktop bridge: one public name (CODEXBOT) + legacy aliases (CODEXAPP).
# Call before --check / provider-launch so right-click and aib-codexbot agree.
# Optional durable bind: ~/.ez2-aib/secrets/codexbot.env (0600; KEY=value only).
aib_load_codexbot_secrets() {
  local f line key val
  f="${AIB_CODEXBOT_SECRETS_FILE:-${HOME}/.ez2-aib/secrets/codexbot.env}"
  [ -f "$f" ] || return 0
  # Only fill empties — live export / seat env always wins.
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    # strip optional single/double quotes
    case "$val" in
      '*') val="${val:1:${#val}-2}" ;;
      "*") val="${val:1:${#val}-2}" ;;
    esac
    case "$key" in
      AIB_CODEXBOT_THREAD_ID|AIB_CODEXAPP_THREAD_ID|CODEX_THREAD_ID)
        # A live Codex CLI exports its own CODEX_THREAD_ID.  That generic
        # carrier must not steal CodexBot's durable binding; only an explicit
        # CodexBot/CodexApp override outranks the secrets file.
        if [ -z "${AIB_CODEXBOT_THREAD_ID:-}${AIB_CODEXAPP_THREAD_ID:-}" ]; then
          export AIB_CODEXBOT_THREAD_ID="$val"
          export AIB_CODEXAPP_THREAD_ID="$val"
          export CODEX_THREAD_ID="$val"
        fi
        ;;
      AIB_CODEXBOT_ENDPOINT|AIB_CODEXAPP_ENDPOINT)
        if [ -z "${AIB_CODEXBOT_ENDPOINT:-}${AIB_CODEXAPP_ENDPOINT:-}" ]; then
          export AIB_CODEXBOT_ENDPOINT="$val"
          export AIB_CODEXAPP_ENDPOINT="$val"
        fi
        ;;
      AIB_CODEXBOT_QUEUE_TAKEOVER_SECS)
        case "$val" in
          ''|*[!0-9]*) ;;
          *) [ -n "${AIB_CODEXBOT_QUEUE_TAKEOVER_SECS:-}" ] || export AIB_CODEXBOT_QUEUE_TAKEOVER_SECS="$val" ;;
        esac
        ;;
      AIB_CODEXBOT_MODE)
        # 09-24 the maintainer:aib 與桌面要能同時運作 ⇒ 席預設模式由這個檔決定(writer|queue)。
        # 同樣只填空:活的 export 優先。不認得的值不吃(橋的 --mode choices 會擋,這裡先不放進去)。
        case "$val" in
          writer|queue) [ -n "${AIB_CODEXBOT_MODE:-}" ] || export AIB_CODEXBOT_MODE="$val" ;;
        esac
        ;;
    esac
  done < "$f"
}

# E5(09-24 a seat):行為設定不放 secrets 檔。~/.ez2-aib/config/codexbot.env 通吃所有 AIB_CODEXBOT_* 鍵(只填空、活的 export 優先),
#   綁定鍵(THREAD_ID/ENDPOINT)例外——那兩個由 aib bot connect 管在 secrets 檔,設定檔裡出現就忽略。每加一個旋鈕不必再改這裡。
aib_load_codexbot_config() {
  local f line key val
  f="${AIB_CODEXBOT_CONFIG_FILE:-${HOME}/.ez2-aib/config/codexbot.env}"
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    case "$val" in
      \'*\') val="${val:1:${#val}-2}" ;;
      \"*\") val="${val:1:${#val}-2}" ;;
    esac
    case "$key" in
      AIB_CODEXBOT_THREAD_ID|AIB_CODEXBOT_ENDPOINT|AIB_CODEXAPP_*|CODEX_THREAD_ID) continue ;;
      AIB_CODEXBOT_[A-Z0-9_]*)
        case "$key" in *[!A-Z0-9_]*) continue ;; esac
        [ -n "$(eval "printf '%s' \"\${$key:-}\"")" ] || export "$key=$val"
        ;;
    esac
  done < "$f"
}

aib_normalize_codexbot_env() {
  local thread endpoint
  aib_load_codexbot_secrets
  aib_load_codexbot_config
  thread="${AIB_CODEXBOT_THREAD_ID:-${AIB_CODEXAPP_THREAD_ID:-${CODEX_THREAD_ID:-}}}"
  endpoint="${AIB_CODEXBOT_ENDPOINT:-${AIB_CODEXAPP_ENDPOINT:-}}"
  export AIB_CODEXBOT_THREAD_ID="$thread"
  export AIB_CODEXBOT_ENDPOINT="$endpoint"
  # keep legacy names in sync for seat文件／舊呼叫端
  export AIB_CODEXAPP_THREAD_ID="$thread"
  export AIB_CODEXAPP_ENDPOINT="$endpoint"
  export CODEX_THREAD_ID="$thread"
}

aib_env_unset_prefix() {
  local result="env"
  local name quoted
  for name in "$@"; do
    printf -v quoted '%q' "$name"
    result="$result -u $quoted"
  done
  printf '%s\n' "$result"
}

aib_carrier_env_prefix() {
  aib_env_unset_prefix "${AIB_CARRIER_SCRUB_VARS[@]}"
}
