#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Idempotent assembler for the maintainer's eye-verified cockpit:
# left content tabs | live ai-hub | right content tabs, with a full-width
# bottom tab pane and a separate war-room window.
set -euo pipefail
. "$(dirname "$0")/cockpit-env.sh"

AIB_MODE="${1:---ensure}"
AIB_PROBE_LIVE=0
case "$AIB_MODE" in
  --ensure|--print-plan) ;;
  *) echo "usage: cockpit-full.sh [--ensure|--print-plan] [--probe-live]" >&2; exit 2 ;;
esac
if [ "$#" -eq 2 ] && [ "$2" = "--probe-live" ]; then
  AIB_PROBE_LIVE=1
elif [ "$#" -gt 1 ]; then
  echo "usage: cockpit-full.sh [--ensure|--print-plan] [--probe-live]" >&2
  exit 2
fi

S="$EZ2_TMUX_SESSION"
HUB="$EZ2_AIHUB_SOCKET"
C="$EZ2_COCKPIT_DIR"
# 畫面字走四語字典(語系盤查第 14 條,10-14):_cft <鍵> [名=值 …];每次照 i18n_lib.resolve_locale() 取,python 起不來印鍵名。
_cft() {
  local key="$1"; shift
  local args=() kv
  for kv in "$@"; do args+=(--param "$kv"); done
  "${EZ2_PY3:-python3}" "$C/i18n_lib.py" ${args[@]+"${args[@]}"} "$key" 2>/dev/null || printf '%s\n' "$key"
}
TOKEN="$EZ2_AIB_INSTANCE_TOKEN"
AIB_TITLE="Ez2 AIB v$EZ2_AIB_VERSION"

# ── `--print-plan`(L4 第 0 件,the maintainer 2026-09-05 四格開關的前置)
#
# 印出「這次組裝**會建哪幾格、從誰切出來**」,然後 exit 0。
#
# ★★ 它必須在 `mkdir`/`flock`/任何 tmux 呼叫**之前**就結束。
#    不是為了快:`flock 8` 會**擋住同時在跑的真 ensure**,而一個叫作
#    「印出計畫」的東西讓別人的組裝卡住,那是最難聯想的一種副作用。
#    (theme.py 那一段也在下面,它會讀檔;印計畫不需要主題。)
#
# ★★ 建格走本函式:`grow_cockpit_panes()` 讀這張表。冷啟動 / 自癒都叫同一支
#    grow,不再另寫一份四格樹。眼驗臂仍用 `code.index()` 釘
#    `P_RIGHT="$(outer_tmux split-window` 這段字面,所以 grow 裡那一行保留。
#
# 輸出格式(一格一行,欄以 `|` 分隔,**機器可讀,臂就讀這個**):
#     <role>|<anchor>|<direction>|<size>|<script>
#   anchor `-` = 這一格是 new-session 的初始 pane(沒有切點)
#   direction  = `v`(往下切) / `h`(往右切) / `-`(初始 pane)
#   size       = `-` 表示不指定 -l
cockpit_pane_plan() {
  # 接 panes.py --expected-roles:off 的格不印、不建。預設只 hub+right。
  # 左/下腳本留在樹上,只是這次不建。
  # roles 一行一個;用換行當邊界,空白對不上(command substitution 不會把
  # 換行改成空白)。
  local roles
  roles="$("$EZ2_PY3" "$C/panes.py" --expected-roles)" || return 2
  _plan_has() {
    case $'\n'"$roles"$'\n' in
      *$'\n'"$1"$'\n'*) return 0 ;;
      *) return 1 ;;
    esac
  }
  if _plan_has left; then
    printf 'left|-|-|-|%s\n' "$C/pane-left-tabs.sh"
    if _plan_has bottom; then
      printf 'bottom|left|v|12|%s\n' "$C/pane-bottom-tabs.sh"
    fi
    if _plan_has hub; then
      printf 'hub|left|h|-|<placeholder>\n'
    fi
    if _plan_has right; then
      if _plan_has hub; then
        printf 'right|hub|h|-|%s\n' "$C/pane-right-tabs.sh"
      else
        printf 'right|left|h|-|%s\n' "$C/pane-right-tabs.sh"
      fi
    fi
  elif _plan_has hub; then
    printf 'hub|-|-|-|<placeholder>\n'
    if _plan_has bottom; then
      printf 'bottom|hub|v|12|%s\n' "$C/pane-bottom-tabs.sh"
    fi
    if _plan_has right; then
      printf 'right|hub|h|-|%s\n' "$C/pane-right-tabs.sh"
    fi
  elif _plan_has right; then
    printf 'right|-|-|-|%s\n' "$C/pane-right-tabs.sh"
    if _plan_has bottom; then
      printf 'bottom|right|v|12|%s\n' "$C/pane-bottom-tabs.sh"
    fi
  elif _plan_has bottom; then
    printf 'bottom|-|-|-|%s\n' "$C/pane-bottom-tabs.sh"
  fi
  return 0
}

if [ "$AIB_MODE" = "--print-plan" ]; then
  cockpit_pane_plan
  exit 0
fi
. "$C/identity-env.sh"
CARRIER_ENV_PREFIX="$(aib_carrier_env_prefix)"

# ★ 艙門白名單(2026-09-25,the maintainer 令「從根本上修」)。判準與理由的正本在
#   identity-env.sh 的「艙門」那一段;這裡只是**用**它,不重述也不抄第二份。
#   建在這裡的理由:下面每一次 hub_tmux 都要用,而**第一次**用到它的那一次
#   就是把 tmux 伺服器生出來的那一次 —— 伺服器的環境是之後每個窗格的底。
aib_env_door_build

# Fail before creating or changing any tmux object when the selected theme is
# unsafe or malformed. A half-built cockpit with no readable colour source is
# harder to diagnose than a clean fail-closed launch.
AIB_THEME_SNAPSHOT="$("$EZ2_PY3" "$C/theme.py" \
  --file "$EZ2_AIB_THEME_FILE" snapshot)"

# 「無底色」的第三、四處讀端(前兩處是四格 window-style,在 theme-ctl)。
#
# 為什麼兩條 status 列的預設值留在這裡而不是搬進主題檔:它們不是 region,
# 沒有 region 契約可查;搬走等於在啟動器裡多一條會壞掉整艙的相依。所以只在
# 既有取值處包一層。the maintainer 09-11:未設定 / 新安裝 = 透明淡黑,所以沒有覆寫檔
# 或沒有 `_cockpit` 鍵時走 strip-bg;明示 `palette` 才用下面寫死的有色預設。
# 覆寫檔壞掉一律沿用有色預設:啟動器的失敗模式必須是「顏色沒變」而不是「艙起不來」。
cockpit_status_style() {  # $1=今天寫死的預設值
  local ovr
  ovr="$("$EZ2_PY3" -c '
import json, os, sys
path = os.path.join(os.environ.get("EZ2_COCKPIT_STATE_DIR", ""),
                    "theme-overrides.json")
if not os.path.isfile(path):
    print("none")
    raise SystemExit(0)
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    raise SystemExit(0)
if not isinstance(data, dict):
    raise SystemExit(0)
value = data.get("_cockpit")
if value in (None, "", "none"):
    print("none")
' 2>/dev/null || true)"
  if [ "$ovr" = "none" ]; then
    "$EZ2_PY3" "$C/theme.py" --file "$EZ2_AIB_THEME_FILE" strip-bg "$1" \
      2>/dev/null || printf '%s\n' "$1"
  else
    printf '%s\n' "$1"
  fi
}

# 刀 A:述詞由 cabin_resolve 匯出(EZ2_AIB_CABIN_NAMED),不在這裡再判一次主艙名
if [ "$EZ2_AIB_CABIN_NAMED" = "1" ] && [ -z "$TOKEN" ]; then
  echo "named AIB instance is missing its ownership token" >&2
  exit 4
fi

# The cockpit always lives on the user's default tmux server.  AIB is often
# invoked from the nested ai-hub, where ambient TMUX points at the inner named
# socket; every outer operation must therefore scrub TMUX explicitly.
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux 伺服器。
# 位置刻意在**這裡**(theme 快照與 token 檢查之後、mkdir/flock/任何 tmux 之前):
#   1. `--print-plan` 已在上面 exit 0,合法印計畫不會走到這 —— 而 dev6 的 K1 刀
#      拿掉那個 exit 時,--print-plan 就會**落到這裡**變成真 ensure,被這道擋下。
#   2. 排在 theme 快照之後,才不會搶掉「不安全主題要先出聲」那條既有契約
#      (test-theme 的 unsafe-theme 格靠的就是那個先後)。
# 判準與 09-05 事故經過在 install-guard.sh;沒有旗標可繞:治具給 TMUX_TMPDIR 沙箱、
# 或讓 PATH 上的 tmux 是暫存目錄裡的替身,兩條路都放行,要寫活的伺服器就從真樹跑。
. "$C/install-guard.sh"
aib_refuse_tmp_install cockpit-full.sh "$C" 0 || exit $?

# 高2(codex 審):兩個並發 --ensure 可同時判定損壞並交錯 kill/new-window,
# 留下重複 cockpit window 或讓一方持有已刪除的 pane id。整段組裝取 instance 鎖。
mkdir -p "$EZ2_COCKPIT_STATE_DIR"
# ED.9(2026-08-13):`exec {VAR}>>` 具名 fd 是 bash 4.1+ 語法,macOS /bin/bash 3.2
# 回「exec: {AIB_ENSURE_LOCK}: not found」rc=127——本機不炸只因 PATH 上是 homebrew
# bash 5.x,那是未宣告的相依。改固定 fd 8(state-transition.sh 用 9,兩者可巢狀)。
# 鎖檔 v2(08-13):v1 被 cockpit 首建時的 tmux 伺服器以繼承 fd 永久霸佔,
# 換檔名讓新 build 之間仍互斥;配合上面 8>&-,v2 不會再被任何 daemon 帶走。
exec 8>>"$EZ2_COCKPIT_STATE_DIR/.cockpit-ensure.v2.lock"
# 224:flock -w,不默等。逾時具名 exit 5;8>&- 那道仍在下面,防 tmux 繼承 fd。
flock -w "${AIB_ENSURE_LOCK_WAIT_SEC:-60}" 8 || {
  echo "$(_cft aib.cfull.lock_busy seconds="${AIB_ENSURE_LOCK_WAIT_SEC:-60}" path="$EZ2_COCKPIT_STATE_DIR/.cockpit-ensure.v2.lock")" >&2
  exit 5
}

# ★鎖 fd 不外流(08-13 實測):ensure 持 fd 8 flock 期間開的 tmux 伺服器會
# **繼承這個 fd 並永久持有**(daemon 不死鎖不放),之後每一次 ensure 都無限等
# ——cockpit 首建後第二次 aib 必掛死,lsof 可見兩個 tmux server 各掛一個 fd。
# 所以每個 tmux 子程序都關 8>&-;flock 由本行程獨佔,壽命=本腳本。
outer_tmux() { env -u TMUX tmux "$@" 8>&-; }
# ★ 2026-09-25:從 `env -u TMUX` 改成 `env -i <白名單>`。
#   舊寫法只拿掉 TMUX 一個名字,其餘**整包**跟著進 tmux 伺服器,而伺服器的
#   全域環境是之後每一個新窗格(含席位)的底 —— 從 AI 工具殼開的艙因此整艙
#   黑白(NO_COLOR),還帶著上一代會話的 GROK_SESSION_ID。
#   `-i` 已經涵蓋 `-u TMUX`(TMUX 不在白名單裡),所以那一項不必再寫。
hub_tmux() { env -i ${AIB_ENV_DOOR_ENV[@]+"${AIB_ENV_DOOR_ENV[@]}"} tmux -L "$HUB" "$@" 8>&-; }

scrub_outer_session_environment() {
  local name
  for name in "${AIB_CARRIER_SCRUB_VARS[@]}"; do
    outer_tmux set-environment -r -t "$S" "$name"
  done
  outer_tmux set-environment -t "$S" EZ2_AIB_INSTANCE "$EZ2_AIB_INSTANCE"
  outer_tmux set-environment -t "$S" EZ2_AIB_INSTANCE_HOME "$EZ2_AIB_INSTANCE_HOME"
  outer_tmux set-environment -t "$S" EZ2_AIB_INSTANCE_TOKEN "$TOKEN"
  outer_tmux set-environment -t "$S" EZ2_TMUX_SESSION "$S"
  outer_tmux set-environment -t "$S" EZ2_AIHUB_SOCKET "$HUB"
  outer_tmux set-environment -t "$S" EZ2_COCKPIT_STATE_DIR "$EZ2_COCKPIT_STATE_DIR"
  outer_tmux set-environment -t "$S" EZ2_TASKS_DIR "$EZ2_TASKS_DIR"
  outer_tmux set-environment -t "$S" EZ2_AIB_THEME_FILE "$EZ2_AIB_THEME_FILE"
}

scrub_hub_session_environment() {
  local name
  for name in "${AIB_CARRIER_SCRUB_VARS[@]}"; do
    hub_tmux set-environment -r -t aihub "$name"
  done
  hub_tmux set-environment -t aihub EZ2_AIB_INSTANCE "$EZ2_AIB_INSTANCE"
  hub_tmux set-environment -t aihub EZ2_AIB_INSTANCE_HOME "$EZ2_AIB_INSTANCE_HOME"
  hub_tmux set-environment -t aihub EZ2_AIB_INSTANCE_TOKEN "$TOKEN"
  hub_tmux set-environment -t aihub EZ2_TMUX_SESSION "$S"
  hub_tmux set-environment -t aihub EZ2_AIHUB_SOCKET "$HUB"
  hub_tmux set-environment -t aihub EZ2_COCKPIT_STATE_DIR "$EZ2_COCKPIT_STATE_DIR"
  hub_tmux set-environment -t aihub EZ2_TASKS_DIR "$EZ2_TASKS_DIR"
}

mkdir -p "$EZ2_COCKPIT_STATE_DIR"
for f in left-tab right-tab bottom-tab; do
  [ -f "$EZ2_COCKPIT_STATE_DIR/$f" ] || printf '0\n' > "$EZ2_COCKPIT_STATE_DIR/$f"
done

# R6 徽章三態的**使用者偏好**(the maintainer 2026-09-01 23:5x 直令「設為永久」)。
# 之前只用 `tmux set-environment` 下在 session 上,--ensure 重建就掉——
# 而掉了之後畫面退回 both,與「使用者本來就想要 both」逐字相同,沒有任何讀數會紅。
# ⇒ 存成偏好檔,由本檔在組 pane 指令時讀進去,重建後仍在。
#   順位:呼叫端環境 > 偏好檔 > 空(空=不注入,採集端自己的預設 both,裁定不變)。
# ★ 不合法的值**具名拒收並退回空**,不靜默當 both——靜默當 both 會讓打錯字
#   與「沒設過」在畫面上同形。
# 偏好有**兩層**(the maintainer 2026-09-02 直令「下次直接 aib 時是預設的」):
#   艙層 $EZ2_AIB_INSTANCE_HOME/engine-badge-mode  這一艙專用
#   人層 <狀態根>/engine-badge-mode                 所有艙(含之後 `aib` 開的新艙)
# ★ 只有艙層是**錯的**:`aib` 的預設行為是開**新艙**,新艙有新的 INSTANCE_HOME,
#   偏好檔不在那裡 ⇒ 設定每開一艙就掉一次,而掉了之後畫面退回採集端預設,
#   與「使用者本來就要那個預設」逐字相同,沒有讀數會紅。這是 41199764 沒補完的一半。
AIB_BADGE_PREF_FILE="${EZ2_AIB_BADGE_PREF_FILE:-$EZ2_AIB_INSTANCE_HOME/engine-badge-mode}"
# ★ 狀態根**向單源要**,不手抄優先序:tests/test-cabin-resolution.py 的
#   test_no_second_state_root_chain 立刻抓到我抄的那一份(它是對的)。
#   同一份知識散在兩處的失效形狀=某天有人改了 cabin_resolve 而這裡沒跟上,
#   偏好會靜靜地讀錯目錄,而畫面上只是「設定沒生效」。
AIB_BADGE_USER_PREF_FILE="${EZ2_AIB_BADGE_USER_PREF_FILE:-$(aib_cabin_state_root)/engine-badge-mode}"
resolve_badge_pref() {
  local v="${EZ2_AIB_ENGINE_BADGE:-}"
  local src="env"
  if [ -z "$v" ] && [ -r "$AIB_BADGE_PREF_FILE" ]; then
    IFS= read -r v < "$AIB_BADGE_PREF_FILE" || v=""
    src="cabin"
  fi
  if [ -z "$v" ] && [ -r "$AIB_BADGE_USER_PREF_FILE" ]; then
    IFS= read -r v < "$AIB_BADGE_USER_PREF_FILE" || v=""
    src="user"
  fi
  case "$v" in
    both|icon|letters) printf '%s' "$v" ;;
    '') printf '' ;;
    *) echo "cockpit: $(_cft aib.cfull.badge_bad value="${v}" source="${src}")" >&2; printf '' ;;
  esac
}

pane_command() {
  local out=""
  local badge=""
  badge="$(resolve_badge_pref)"
  local badge_env=""
  [ -z "$badge" ] || printf -v badge_env 'EZ2_AIB_ENGINE_BADGE=%q ' "$badge"
  local usage_probe_env=""
  # Unset first: a reused tmux server may carry an old opt-in.
  # Only this invocation, and only the right pane, may grant LIVE=1.
  if [ "${AIB_PROBE_LIVE:-0}" = "1" ] && [ "${1##*/}" = "pane-right-tabs.sh" ]; then
    usage_probe_env="EZ2_USAGE_PROBE_LIVE=1 "
  elif [ "${1##*/}" = "pane-right-tabs.sh" ] && [ "${AIB_PROBE_LIVE_AGY:-1}" = "1" ]; then
    # v0.70.0 #14:真艙右欄預設只准 agy 一家 headless 探(30 分一次);AIB_PROBE_LIVE_AGY=0 關掉。
    usage_probe_env="EZ2_USAGE_PROBE_LIVE_ENGINES=agy "
  fi
  # -u EZ2_ONESHOT / EZ2_RIGHT_VIEW:tmux 全域若殘留這兩個，右欄畫一幀就退出，
  # 窗格被收掉，新開席只剩中欄。單幀旗只准呼叫端在子行程顯式加上。
  printf -v out 'exec %s -u EZ2_USAGE_PROBE_LIVE -u EZ2_USAGE_PROBE_LIVE_ENGINES -u EZ2_ONESHOT -u EZ2_RIGHT_VIEW EZ2_AIB_INSTANCE=%q EZ2_AIB_INSTANCE_TOKEN=%q EZ2_AIB_INSTANCE_HOME=%q EZ2_TMUX_SESSION=%q EZ2_AIHUB_SOCKET=%q EZ2_COCKPIT_STATE_DIR=%q EZ2_TASKS_DIR=%q EZ2_AIB_ROOT=%q EZ2_OUTLINE=%q EZ2_AIB_THEME_FILE=%q %s%sbash %q' \
    "$CARRIER_ENV_PREFIX" "$EZ2_AIB_INSTANCE" "$TOKEN" \
    "$EZ2_AIB_INSTANCE_HOME" "$S" "$HUB" \
    "$EZ2_COCKPIT_STATE_DIR" "$EZ2_TASKS_DIR" "$EZ2_AIB_ROOT" \
    "$EZ2_OUTLINE" "$EZ2_AIB_THEME_FILE" "$badge_env" "$usage_probe_env" "$1"
  printf '%s\n' "$out"
}

HUB_CMD=""
# The hub is a nested tmux client.  Do not rely on the outer terminal's locale:
# a fresh attach can otherwise start with client_utf8=0 and make CJK input and
# output disappear from the provider pane.  Keep the default explicit and
# allow operators to select another UTF-8 locale when the host uses one.
AIB_UTF8_LOCALE="${AIB_UTF8_LOCALE:-en_US.UTF-8}"
case "$AIB_UTF8_LOCALE" in
  *[Uu][Tt][Ff]-8|*[Uu][Tt][Ff]8) ;;
  *)
    echo "AIB_UTF8_LOCALE must name a UTF-8 locale: $AIB_UTF8_LOCALE" >&2
    exit 2
    ;;
esac
printf -v HUB_CMD 'exec %s -u TMUX EZ2_AIB_INSTANCE=%q EZ2_AIB_INSTANCE_TOKEN=%q EZ2_AIB_INSTANCE_HOME=%q EZ2_TMUX_SESSION=%q EZ2_AIHUB_SOCKET=%q EZ2_COCKPIT_STATE_DIR=%q LANG=%q LC_ALL=%q LC_CTYPE=%q tmux -u -L %q attach -t aihub' \
  "$CARRIER_ENV_PREFIX" "$EZ2_AIB_INSTANCE" "$TOKEN" \
  "$EZ2_AIB_INSTANCE_HOME" "$S" "$HUB" \
  "$EZ2_COCKPIT_STATE_DIR" "$AIB_UTF8_LOCALE" "$AIB_UTF8_LOCALE" \
  "$AIB_UTF8_LOCALE" "$HUB"

# The hub is a separate tmux server.  A live named socket that does not already
# contain our owned `aihub` session belongs to somebody else.  Do not add a
# session or change server-global options on that foreign server.
HUB_SESSIONS_BEFORE="$(hub_tmux list-sessions -F '#{session_name}' 2>/dev/null || true)"
if ! hub_tmux has-session -t aihub 2>/dev/null; then
  if [ -n "$HUB_SESSIONS_BEFORE" ]; then
    echo "existing AIB hub socket contains foreign sessions" >&2
    exit 4
  fi
  # 這一行之後 tmux 伺服器才真的生出來 ⇒ 在這裡報「艙門擋下了什麼」。
  # 不報的話,洗乾淨與根本沒洗在畫面上逐字相同。
  aib_env_door_report_dropped
  LOBBY_ID="$(hub_tmux new-session -d -s aihub -n lobby \
    -P -F '#{window_id}' "$(pane_command "$C/hub-lobby.sh")")"
  hub_tmux set-option -w -t "$LOBBY_ID" @aib_lobby 1
  [ -z "$TOKEN" ] || hub_tmux set-option -t aihub @aib_instance_token "$TOKEN"
elif [ -n "$TOKEN" ]; then
  if [ "$(hub_tmux show-option -qv -t aihub @aib_instance_token 2>/dev/null || true)" != "$TOKEN" ]; then
    echo "existing AIB hub is not owned by instance $EZ2_AIB_INSTANCE" >&2
    exit 4
  fi
# ★ 2026-08-16 實測缺陷:主艙的 TOKEN 恆空(cockpit-env.sh:57 只對具名艙要求
#   token),而上面那條擁有權閘原本寫成 `elif [ -n "$TOKEN" ] && …` ——
#   **token 空就整條短路,擁有權檢查完全沒跑**。於是只要那個 socket 上有一台
#   別人的 tmux server、而它唯一的 session 剛好叫 `aihub`(這台機器上是常見
#   名字),下面就會改掉那台 server 的 prefix / status-left / mouse / root
#   binding,而且 rc=0、一句警告都沒有。沙箱實證:C-b → C-a。
#
#   受害者是**不知道自己被改的人**,而且他會把它讀成「我的 tmux 壞了」,
#   不會讀成 aib 的 bug —— 所以這個缺陷不會被回報。
#
#   ★ 這一刀刻意**不碰**下面 HUB_SESSIONS_AFTER 那道補救閘(第一艦長 e5300 裁):
#     在補救閘上加條件會讓兩道閘的責任更糊,下一個人分不出哪道在守什麼。
#     真正該修的是這條 `-n "$TOKEN"` 的短路。
#   ★ 「主艙該不該有 token」不在這一刀裡裁。這一刀只要求一件事:
#     **沒有擁有權證明時,不得改任何 `-g` 設定。**
#
#   無 token 時的擁有權證明用什麼:`EZ2_AIHUB_SOCKET` 這個 session environment。
#   它由下面的 scrub_hub_session_environment 在**每次 ensure** 都寫一次,所以
#   任何我們建過/接管過的 hub 都有;外人的 tmux server 不會有。
#   ★ 為什麼不用 `@aib_lobby`(上面建艙時會設的那個):**實測活體兩個 hub
#     (socket `aihub` 與 `aihub-8`)的視窗全都沒有這個標記**,拿它當判準會
#     在下一次 --ensure 把 the maintainer 正在跑的指揮艙擋成 exit 4。判準要挑活體真的
#     帶著的那一個,不是碼上看起來會設的那一個。
elif ! hub_tmux show-environment -t aihub EZ2_AIHUB_SOCKET >/dev/null 2>&1; then
  echo "existing AIB hub carries no AIB ownership marker; refusing to adopt a foreign tmux server" >&2
  exit 4
fi

# Recheck after creation to close the probe/create race before changing prefix,
# status or mouse settings that are global to this named tmux server.
HUB_SESSIONS_AFTER="$(hub_tmux list-sessions -F '#{session_name}' 2>/dev/null || true)"
if [ "$HUB_SESSIONS_AFTER" != "aihub" ]; then
  echo "AIB hub socket is not isolated to the owned aihub session" >&2
  exit 4
fi
scrub_hub_session_environment
# Advertise synchronized output to tmux-256color clients.  Unsupported
# terminals ignore this capability; report animation timing stays unchanged.
hub_tmux set-option -as terminal-features ',tmux-256color:sync'
hub_tmux set-option -g prefix C-a
hub_tmux unbind-key C-b 2>/dev/null || true
hub_tmux bind-key C-a send-prefix
hub_tmux set-option -g status-style "$(cockpit_status_style 'bg=colour24,fg=colour255')"
# 10-10(the maintainer 眼驗令):status-left 前補 2 格,與 Claude 輸入框下方那三行(固定縮 2 格)對齊;hub 艦長列(captains-ctl.sh)同令。
hub_tmux set-option -g status-left "  [$AIB_TITLE] "
# The nested hub must own mouse routing so its status tabs remain clickable.
# Providers commonly request terminal mouse reporting; tmux would then forward
# a drag to the provider instead of entering copy-mode. Override only Drag so
# all central drags select text. Down/Up/wheel keep their tmux defaults, and an
# ordinary click still reaches both the inner status and the provider.
hub_tmux set-option -g mouse on
hub_tmux bind-key -T root MouseDrag1Pane copy-mode -M

# ---- 閒置收艙 watch 常駐窗(the maintainer 08-18 裁:lobby 窗常駐 `aib idle watch`)----
# 三條約束(整合席工單):
#   (a) 開首席時 aib 只殺 `@aib_lobby=1` 的窗——watch 窗**不標** @aib_lobby,
#       標 `@aib_role=sys.watch`(aib 的 ROLE_RE 禁 `.`,人開的席不可能撞名;
#       右欄/艦長列以 `sys.` 前綴辨識系統窗不列為席)。
#   (b) 節奏由 idle_reaper watch 自己管(預設 300s 一輪、flock 單飛),不在這裡加任何輪詢。
#   (c) 冪等:hub 內已有 sys.watch 窗=保留;pane 已死=respawn;沒有才 new-window。
#       ★不 kill 任何窗(組裝器不殺對話的契約不變);重入疊窗只可能來自
#       flock 外的手工建窗,那不是這裡的責任。
#   hub 讀不到=不動(degraded,與 captains 同紀律),不擋開艙。
WATCH_CMD=""
printf -v WATCH_CMD 'exec %s EZ2_AIB_INSTANCE=%q EZ2_AIB_INSTANCE_HOME=%q EZ2_AIHUB_SOCKET=%q EZ2_AIB_ROOT=%q EZ2_AIB_VERSION=%q %q %q watch --interval %q' \
  "$CARRIER_ENV_PREFIX" "$EZ2_AIB_INSTANCE" "$EZ2_AIB_INSTANCE_HOME" "$HUB" \
  "$EZ2_AIB_ROOT" "${EZ2_AIB_VERSION:-}" "$EZ2_PY3" "$C/idle_reaper.py" \
  "${EZ2_AIB_IDLE_WATCH_INTERVAL:-300}"
ensure_watch_window() {
  local ids wid role dead found=""
  ids="$(hub_tmux list-windows -t aihub -F '#{window_id}' 2>/dev/null)" || {
    echo "cockpit-full: hub window snapshot failed; watch window untouched" >&2
    return 0
  }
  while IFS= read -r wid; do
    [ -n "$wid" ] || continue
    case "$wid" in @*[!0-9]*|@|[!@]*) continue ;; esac
    role="$(hub_tmux show-options -wqv -t "$wid" @aib_role 2>/dev/null || true)"
    [ "$role" = "sys.watch" ] || continue
    [ -z "$found" ] || continue          # 多出來的不動也不再算(見上 (c))
    found="$wid"
    dead="$(hub_tmux display-message -p -t "$wid" '#{pane_dead}' 2>/dev/null || printf '0')"
    # ★ W1:pane 活著**不等於** watch 還在跑。remain-on-exit 只讓「行程退出」變成
    #   pane_dead=1;行程卡住(不再輪詢)時 pane 完全正常,而監控其實已經停了。
    #   心跳查驗補的就是這一格:逾時/pid 不見 → 一併 respawn(艦外交付 3「順手復活」)。
    #   rc=2(自己驗自己)與 last_scan_rc(活著但掃描出錯)只警告不重生——
    #   前者是我們站錯位置,後者的迴圈還在跑,殺掉它並不會讓它變好。
    if [ "$dead" != "1" ]; then
      hb_out="$("$EZ2_PY3" "$C/idle_reaper.py" watch-status --json 2>/dev/null || true)"
      case "$hb_out" in
        *'"rc": 3'*|*'"rc":3'*)
          case "$hb_out" in
            *heartbeat_missing*|*stale:*|*pid_gone*|*instance_mismatch*)
              echo "cockpit-full: $(_cft aib.cfull.hb_respawn window="$wid" detail="$hb_out")" >&2
              dead=1 ;;
            *) echo "cockpit-full: $(_cft aib.cfull.hb_odd detail="$hb_out")" >&2 ;;
          esac ;;
      esac
    fi
    if [ "$dead" = "1" ]; then
      hub_tmux respawn-pane -k -t "$wid" "$WATCH_CMD" 2>/dev/null || \
        echo "cockpit-full: watch window respawn failed ($wid)" >&2
    fi
  done <<EOF_W
$ids
EOF_W
  if [ -z "$found" ]; then
    wid="$(hub_tmux new-window -d -t aihub -n watch -P -F '#{window_id}' "$WATCH_CMD" 2>/dev/null)" || {
      echo "cockpit-full: watch window create failed" >&2
      return 0
    }
    hub_tmux set-option -w -t "$wid" @aib_role sys.watch
    hub_tmux set-option -w -t "$wid" remain-on-exit on
  fi
}
ensure_watch_window

# ---- 全球對談轉接程式 sys.chat 窗(Andy 10-15 22:5x;開發文件第 20.1 節)----
#   只在 `aib chat on` 過(開關檔在)才開;一台一支由 chat_bridge.py 的 flock 管,別艙的窗排隊備援。
#   冪等:有窗且活著 = 不動;pane 死了 = respawn;沒有才開。失敗只警告,不擋開艙。
if "$EZ2_PY3" "$C/chat_bridge.py" enabled 2>/dev/null; then
  EZ2_AIB_INSTANCE="$EZ2_AIB_INSTANCE" EZ2_AIB_INSTANCE_HOME="$EZ2_AIB_INSTANCE_HOME" EZ2_AIHUB_SOCKET="$HUB" \
    EZ2_AIB_ROOT="$EZ2_AIB_ROOT" "$EZ2_PY3" "$C/chat_bridge.py" ensure-window --socket "$HUB" >/dev/null 2>&1 || \
    echo "cockpit-full: chat window ensure failed" >&2
fi

# ---- 選單自動選常駐窗(10-13 a seat,the maintainer「所有的ai只要選單有選項,都應該自動判斷選擇」「五家都要做第一層」;
#   這是第二層:每 3 秒看本艙各席底部,卡在選單就叫 AI 選;建議 3「每艙開艙自帶一支」)----
#   與 sys.watch 同三條約束:不標 @aib_lobby、標 @aib_role=sys.menu、冪等(有就留、死了 respawn、沒有才建、不殺窗)。
#   menu_pick watch 自己 flock 單飛(每艙一把鎖);AIB_MENU_PICK=0 ⇒ 不建。
MENU_CMD=""
printf -v MENU_CMD 'exec %s EZ2_AIB_INSTANCE=%q EZ2_AIB_INSTANCE_HOME=%q EZ2_AIHUB_SOCKET=%q EZ2_AIB_ROOT=%q %q %q watch --interval %q' \
  "$CARRIER_ENV_PREFIX" "$EZ2_AIB_INSTANCE" "$EZ2_AIB_INSTANCE_HOME" "$HUB" \
  "$EZ2_AIB_ROOT" "$EZ2_PY3" "$C/menu_pick.py" "${EZ2_AIB_MENU_PICK_INTERVAL:-3}"
ensure_menu_window() {
  [ "${AIB_MENU_PICK:-1}" = "0" ] && return 0
  [ -f "$C/menu_pick.py" ] || return 0
  local ids wid role dead found=""
  ids="$(hub_tmux list-windows -t aihub -F '#{window_id}' 2>/dev/null)" || return 0
  while IFS= read -r wid; do
    [ -n "$wid" ] || continue
    case "$wid" in @*[!0-9]*|@|[!@]*) continue ;; esac
    role="$(hub_tmux show-options -wqv -t "$wid" @aib_role 2>/dev/null || true)"
    [ "$role" = "sys.menu" ] || continue
    [ -z "$found" ] || continue
    found="$wid"
    dead="$(hub_tmux display-message -p -t "$wid" '#{pane_dead}' 2>/dev/null || printf '0')"
    if [ "$dead" = "1" ]; then
      hub_tmux respawn-pane -k -t "$wid" "$MENU_CMD" 2>/dev/null || \
        echo "cockpit-full: menu window respawn failed ($wid)" >&2
    fi
  done <<EOF_M
$ids
EOF_M
  if [ -z "$found" ]; then
    wid="$(hub_tmux new-window -d -t aihub -n menu -P -F '#{window_id}' "$MENU_CMD" 2>/dev/null)" || {
      echo "cockpit-full: menu window create failed" >&2
      return 0
    }
    hub_tmux set-option -w -t "$wid" @aib_role sys.menu
    hub_tmux set-option -w -t "$wid" remain-on-exit on
  fi
}
ensure_menu_window

# ---- 截圖小幫手(10-09 the maintainer):AIB 開著時,Mac 任何地方按 Control+Shift+S 截圖 ----
#   背景跑、不等它、失敗不擋開艙(它第一次要編譯,還可能跳視窗問要不要關輸入法的截圖)。
#   不是 macOS / AIB_SHOT=0 / 不是真座艙(測試、截幀)時 shot_helper.py 自己略過;
#   艙全關時小幫手自己結束(每 30 秒問一次 `shot_helper.py alive`)。紀錄在 ~/.ez2-aib/shot/shot.log。
( EZ2_AIB_INSTANCE="${EZ2_AIB_INSTANCE:-}" "$EZ2_PY3" "$C/shot_helper.py" ensure \
    </dev/null >/dev/null 2>&1 & ) || true
# 紀錄檔上限(10-10 the maintainer「紀錄檔清理」):<狀態根>/logs 超過 8MB 的檔壓成 .1.gz,留 3 份。
#   claim-gate-runs.jsonl 有自己的輪替,log_cap 跳過它。不在真座艙裡(測試)不動手。
( EZ2_AIB_INSTANCE="${EZ2_AIB_INSTANCE:-}" "$EZ2_PY3" "$C/log_cap.py" sweep \
    </dev/null >/dev/null 2>&1 & ) || true

pane_by_role() {
  local role="$1" _rows
  _rows="$(outer_tmux list-panes -t "$S:cockpit" -F '#{pane_id}|#{@aib_role}' 2>/dev/null || true)"
  awk -F '|' -v r="$role" '$2 == r { print $1 }' <<<"$_rows"
}

# 這次組裝**會建**哪幾格(`@aib_role`,一行一個)—— 判準的右手邊。
#
# 右手邊取 `cockpit_pane_plan()` 的 role 欄:plan 已接 panes.py,建格也走 plan。
# 判準與建格同一個源。預設 hub+right;四格全開要設定打開。
# ★ 探測失敗**不吞**:回非零,由呼叫端決定怎麼辦(見 validate_cockpit_layout 的 rc=2)。
#   `set -o pipefail` 是本檔全域設定,plan 失敗時整條管線就回非零。
cockpit_expected_roles() {
  local _plan
  _plan="$(cockpit_pane_plan)" || return $?
  awk -F '|' 'NF { print $1 }' <<<"$_plan"
}

cockpit_root_role() {
  # 先落值再比對:awk 一命中就 exit 會關管線,plan 還沒印完就吃 SIGPIPE(141)。
  local _plan
  _plan="$(cockpit_pane_plan)" || return $?
  awk -F '|' '$2 == "-" { print $1; exit }' <<<"$_plan"
}

# rc 三態,**刻意不是兩態**:
#   0 = 版面與設定相符
#   1 = 版面壞了(呼叫端可以自癒)
#   2 = **讀不到設定**(探測失敗)—— 呼叫端**不得**因此自癒
# ★★ 為什麼一定要第三態:自癒的動作是**刪掉並重建 cockpit window**,那是破壞性的。
#    把「我量不到」併進「壞了」,等於在一次失敗的量測上執行破壞性動作;
#    併進「好的」則是讓真壞掉的版面沒人修。兩個方向都錯,所以要三態。
#    (同一條紀律在 cockpit-resize.sh 的 absent/unknown 分家上已經付過一次。)
validate_cockpit_layout() {
  local rows count role role_count expected want
  rows="$(outer_tmux list-panes -t "$S:cockpit" -F '#{pane_id}|#{@aib_role}' 2>/dev/null)" ||
    return 1
  expected="$(cockpit_expected_roles)" || return 2
  [ -n "$expected" ] || return 2      # 空清單不是答案,是探測失敗
  want="$(printf '%s\n' $expected | awk 'NF { n++ } END { print n+0 }')"
  # 對話底下的待審框(@aib_role=review)是疊上去的,不是壞掉的多一格。
  # 把它算進 count 會讓 --ensure 以為版面不完整,整窗拆掉重建,
  # 右欄又回到正式樹、待審列又出現在艦隊底下。
  count="$(printf '%s\n' "$rows" | awk -F '|' '$2 != "review" && NF { n++ } END { print n+0 }')"
  [ "$count" -eq "$want" ] || return 1
  for role in $expected; do
    role_count="$(printf '%s\n' "$rows" | awk -F '|' -v r="$role" '$2 == r { n++ } END { print n+0 }')"
    [ "$role_count" -eq 1 ] || return 1
  done
  return 0
}

mark_cockpit_window() {
  # 標記必須是建 window 後的**第一件事**(codex 三輪:建好卻還沒標記時
  # 中斷,下次 --ensure 因缺標記拒絕自癒=永久卡死)。
  outer_tmux set-window-option -t "$S:cockpit" @aib_cockpit 1 2>/dev/null || true
}

_pane_id_of() {
  case "$1" in
    left) printf '%s' "${P_LEFT:-}" ;;
    hub) printf '%s' "${P_HUB:-}" ;;
    right) printf '%s' "${P_RIGHT:-}" ;;
    bottom) printf '%s' "${P_BOTTOM:-}" ;;
    *) printf '' ;;
  esac
}

_assign_pane_role() {
  local pane="$1" role="$2"
  [ -n "$pane" ] || return 0
  outer_tmux set-option -p -t "$pane" @aib_role "$role"
  case "$role" in
    left) P_LEFT="$pane" ;;
    hub) P_HUB="$pane" ;;
    right) P_RIGHT="$pane" ;;
    bottom) P_BOTTOM="$pane" ;;
  esac
}

grow_cockpit_panes() {
  # 從 plan 的初始格長出其餘格。冷啟動與自癒共用,避免兩條路徑漂移。
  # 預設只 hub+right;left/bottom 在 plan 裡才建。
  local root="$1" role anchor dir size script parent new
  P_LEFT=""; P_HUB=""; P_RIGHT=""; P_BOTTOM=""
  HUB_PLACEHOLDER_CMD="exec sleep 3600"
  while IFS='|' read -r role anchor dir size script; do
    [ -n "$role" ] || continue
    if [ "$anchor" = "-" ]; then
      _assign_pane_role "$root" "$role"
      continue
    fi
    parent="$(_pane_id_of "$anchor")"
    [ -n "$parent" ] || continue
    if [ "$role" = "bottom" ] && [ "$anchor" = "left" ]; then
      P_BOTTOM="$(outer_tmux split-window -v -t "$P_LEFT" -l 12 \
        -P -F '#{pane_id}' "$(pane_command "$C/pane-bottom-tabs.sh")")"
      _assign_pane_role "$P_BOTTOM" bottom
    elif [ "$role" = "hub" ] && [ "$anchor" = "left" ]; then
      P_HUB="$(outer_tmux split-window -h -t "$P_LEFT" \
        -P -F '#{pane_id}' "$HUB_PLACEHOLDER_CMD")"
      _assign_pane_role "$P_HUB" hub
    elif [ "$role" = "right" ] && [ "$anchor" = "hub" ]; then
      P_RIGHT="$(outer_tmux split-window -h -t "$P_HUB" \
        -P -F '#{pane_id}' "$(pane_command "$C/pane-right-tabs.sh")")"
      _assign_pane_role "$P_RIGHT" right
    else
      if [ "$script" = "<placeholder>" ]; then
        if [ "$size" != "-" ] && [ -n "$size" ]; then
          new="$(outer_tmux split-window -"$dir" -t "$parent" -l "$size" \
            -P -F '#{pane_id}' "$HUB_PLACEHOLDER_CMD")"
        else
          new="$(outer_tmux split-window -"$dir" -t "$parent" \
            -P -F '#{pane_id}' "$HUB_PLACEHOLDER_CMD")"
        fi
      else
        if [ "$size" != "-" ] && [ -n "$size" ]; then
          new="$(outer_tmux split-window -"$dir" -t "$parent" -l "$size" \
            -P -F '#{pane_id}' "$(pane_command "$script")")"
        else
          new="$(outer_tmux split-window -"$dir" -t "$parent" \
            -P -F '#{pane_id}' "$(pane_command "$script")")"
        fi
      fi
      _assign_pane_role "$new" "$role"
    fi
  done <<EOF
$(cockpit_pane_plan)
EOF
}

_cmd_for_root_role() {
  local role="$1"
  HUB_PLACEHOLDER_CMD="exec sleep 3600"
  case "$role" in
    left) pane_command "$C/pane-left-tabs.sh" ;;
    hub) printf '%s\n' "$HUB_PLACEHOLDER_CMD" ;;
    right) pane_command "$C/pane-right-tabs.sh" ;;
    bottom) pane_command "$C/pane-bottom-tabs.sh" ;;
    *) return 1 ;;
  esac
}

if ! outer_tmux has-session -t "$S" 2>/dev/null; then
  # 初始格跟 plan 走(預設 hub,不再無條件建左欄)。
  _root_role="$(cockpit_root_role)"
  [ -n "$_root_role" ] || { echo "cockpit: $(_cft aib.cfull.no_panes)" >&2; exit 2; }
  P_ROOT="$(outer_tmux new-session -d -s "$S" -n cockpit -x 220 -y 50 \
    -P -F '#{pane_id}' "$(_cmd_for_root_role "$_root_role")")"
  mark_cockpit_window
  scrub_outer_session_environment
  grow_cockpit_panes "$P_ROOT"
  outer_tmux set-option -t "$S" @aib_state_dir "$EZ2_COCKPIT_STATE_DIR"
  outer_tmux set-option -t "$S" @aib_hub_socket "$HUB"
  outer_tmux set-option -t "$S" @aib_instance "$EZ2_AIB_INSTANCE"
  [ -z "$TOKEN" ] || outer_tmux set-option -t "$S" @aib_instance_token "$TOKEN"

  # Establish final pane geometry before the first nested attach. respawn-pane
  # kills only the disposable sleep above; the owned inner aihub and every
  # provider window remain untouched.
  bash "$C/cockpit-resize.sh" "$S" "${P_LEFT:-}" "${P_HUB:-}" "${P_RIGHT:-}" "${P_BOTTOM:-}"
  [ -n "${P_HUB:-}" ] && outer_tmux respawn-pane -k -t "$P_HUB" "$HUB_CMD"

  WAR_CMD="$(pane_command "$C/warroom-board.sh")"
  WAR_ID="$(outer_tmux new-window -d -t "$S:" -n war-room -P -F '#{window_id}' "$WAR_CMD")"
  outer_tmux set-option -w -t "$WAR_ID" @aib_warroom 1
else
  if [ -n "$TOKEN" ] && \
     [ "$(outer_tmux show-option -qv -t "$S" @aib_instance_token 2>/dev/null || true)" != "$TOKEN" ]; then
    echo "existing AIB cockpit is not owned by instance $EZ2_AIB_INSTANCE" >&2
    exit 4
  fi
  # ★★ 09-05 缺陷二十一之三:這裡**不得**寫成 `validate_cockpit_layout; _layout_rc=$?`。
  #    本檔是 `set -e`,而「函式呼叫」在 `cmd; var=$?` 裡是一個**沒有被測試**的
  #    簡單指令 ⇒ 它一回非零,shell 當場結束,`_layout_rc` 根本沒被指派,
  #    底下整段三態處置(含**刻意設計**的 rc=2 不自癒那一條)全部是死碼。
  #    失效形狀:`aib` 靜靜 exit 1,**一個字都不印** —— 與「沒有這個功能」逐字同形。
  #    `|| _layout_rc=$?` 讓它成為 `||` 串列的左手邊(=被測試的),-e 因此不開火。
  _layout_rc=0
  validate_cockpit_layout || _layout_rc=$?
  if [ "$_layout_rc" -eq 2 ]; then
    # 讀不到設定:**什麼都不做**,但一定出聲。不自癒的理由見 validate 的註解 ——
    # 自癒是破壞性的,不在一次失敗的量測上執行破壞性動作。
    echo "$(_cft aib.cfull.layout_unread session="$S")" >&2
  elif [ "$_layout_rc" -ne 0 ]; then
    # 自癒(codex 審 cockpit 域高:冷啟動中斷會留下不足四 pane 的 session,
    # 下次 --ensure 直接拒絕=永久卡死,只能人工砍 session)。
    # 條件很嚴:必須是本實例擁有(token 已在上面驗過)、且沒有活著的
    # provider pane——有人在裡面工作時絕不重建。
    live_providers="$(outer_tmux list-panes -t "$S" -F '#{pane_current_command}' \
      2>/dev/null | grep -cE 'claude|codex|node|python' || true)"
    # 中欄是 tmux attach,指令名不是 claude/codex。人正在對話卻被當成
    # 「沒有活體」→ 拆窗重建。有 hub 格就視同有人在用。
    hub_live="$(outer_tmux list-panes -t "$S:cockpit" -F '#{@aib_role}' \
      2>/dev/null | awk '$1=="hub"{print 1; exit}')"
    if [ "${live_providers:-0}" -gt 0 ] || [ "${hub_live:-0}" = "1" ]; then
      echo "$(_cft aib.cfull.layout_refuse session="$S")" >&2
      exit 3
    fi
    # 高3:main instance 允許空 token,ownership 檢查會被跳過——刪 window 前
    # 必須另外確認它真的帶 AIB 的 @aib_cockpit 標記,否則可能刪到同名的別人視窗。
    cockpit_marked="$(outer_tmux show-option -qvw -t "$S:cockpit" @aib_cockpit \
      2>/dev/null || true)"
    # 雙重判定(codex 三輪):window 標記可能在「建好但尚未標記」的窄窗口
    # 內缺席;session 級 @aib_state_dir 是建 session 時就設的,兩者任一
    # 成立即認定歸屬。兩者皆無才拒絕——那才真的可能是別人的視窗。
    session_marked="$(outer_tmux show-option -qv -t "$S" @aib_state_dir \
      2>/dev/null || true)"
    # 第三條(codex 四輪:標記之前的任何中斷點都會留下「兩個標記皆無」的
    # 殘骸)。改用結構特徵當後備,窄窗口因此不存在:
    #   session 名由本程式依 instance 算出(非使用者輸入)
    #   + window 名為 cockpit + pane 數不足四 + 上面已驗無活體 provider
    # 三者同時成立 = 這是我們自己中斷留下的殘骸,重建它是安全的。
    pane_count="$(outer_tmux list-panes -t "$S:cockpit" -F x 2>/dev/null |
      awk 'END { print NR+0 }')"
    structural_remnant=0
    # 只有在 session 名是本程式推導出來時,結構特徵才足以認定歸屬:
    # 使用者若把 EZ2_TMUX_SESSION 指向別人的 session,那個 session 裡
    # 恰好有個 1-3 pane 的 cockpit window 就會被誤刪(codex 五輪反例)。
    # ★★ 「不足四格」曾經等於「中斷殘骸」,L4 之後**不再成立**:
    #    設定只開 center+right 的座艙是**健康的 2 格**,而 0 < 2 < 4。
    #    照舊寫法它會被歸成殘骸 ⇒ 兩個標記都缺時**可以被刪掉**。
    #    判準改成「比設定說的少」,而**讀不到設定時一律不算殘骸**(fail-closed:
    #    刪窗是破壞性的,寧可拒絕自動處理也不要在猜測上刪)。
    expected_count="$(cockpit_expected_roles 2>/dev/null | awk 'NF { n++ } END { print n+0 }')" || expected_count=0
    if [ "${EZ2_AIB_SESSION_IS_DERIVED:-0}" = "1" ] && \
       [ "${expected_count:-0}" -gt 0 ] && \
       [ "${pane_count:-0}" -gt 0 ] && [ "${pane_count:-0}" -lt "${expected_count}" ]; then
      structural_remnant=1
    fi
    if [ "$cockpit_marked" != "1" ] && [ -z "$session_marked" ] && \
       [ "$structural_remnant" -ne 1 ]; then
      echo "$(_cft aib.cfull.no_mark_refuse session="$S" n="${pane_count:-?}")" >&2
      exit 3
    fi
    # 刪窗前留可稽核的殘骸快照(codex 六輪殘餘風險:若使用者自己建了同名
    # session 且其中有個空的 cockpit window,結構後備仍會誤判。該情境需
    # 名稱完全撞上且 window 內無任何工作,代價是一個空 shell window;
    # 快照讓誤刪可被發現與還原。)
    if [ -n "${EZ2_COCKPIT_STATE_DIR:-}" ]; then
      {
        printf 'killed_at=%s session=%s panes=%s marked=%s session_marked=%s\n' \
          "$(date +%s)" "$S" "${pane_count:-?}" "${cockpit_marked:-}" \
          "${session_marked:-}"
        outer_tmux list-panes -t "$S:cockpit" \
          -F 'pane=#{pane_id} cmd=#{pane_current_command}' 2>/dev/null || true
        outer_tmux capture-pane -p -t "$S:cockpit" 2>/dev/null | head -40 || true
      } > "$EZ2_COCKPIT_STATE_DIR/.selfheal-last-removed" 2>/dev/null || true
    fi
    echo "$(_cft aib.cfull.rebuild session="$S")" >&2
    # 只重建壞掉的那個 window,session 與戰情室原樣保留
    outer_tmux kill-window -t "$S:cockpit" 2>/dev/null || true
    _root_role="$(cockpit_root_role)"
    NEW_LEFT="$(outer_tmux new-window -d -t "$S:" -n cockpit \
      -P -F '#{pane_id}' "$(_cmd_for_root_role "$_root_role")")"
    mark_cockpit_window
    grow_cockpit_panes "$NEW_LEFT"
    scrub_outer_session_environment
    # 高1:grow 出來的 hub 是 placeholder(sleep 3600),它不是 dead,
    # 後面的 pane_dead 檢查不會救它——自癒完版面對了但中欄沒有對話。
    [ -n "${P_HUB:-}" ] && outer_tmux respawn-pane -k -t "$P_HUB" "$HUB_CMD"
    outer_tmux set-window-option -t "$S:cockpit" @aib_cockpit 1
  fi
  P_LEFT="$(pane_by_role left)"
  P_HUB="$(pane_by_role hub)"
  P_RIGHT="$(pane_by_role right)"
  P_BOTTOM="$(pane_by_role bottom)"
  scrub_outer_session_environment
fi

# A viewer pane may die if an older hub disappeared; reviving that attach does
# not restart or signal any provider process.  Side renderers are likewise
# respawned only when already dead, never while a live UI is running.
if [ -n "${P_HUB:-}" ] && \
   [ "$(outer_tmux display-message -p -t "$P_HUB" '#{pane_dead}')" = "1" ]; then
  outer_tmux respawn-pane -k -t "$P_HUB" "$HUB_CMD"
fi
for pane_script in \
  "${P_LEFT:-}:$C/pane-left-tabs.sh" \
  "${P_RIGHT:-}:$C/pane-right-tabs.sh" \
  "${P_BOTTOM:-}:$C/pane-bottom-tabs.sh"; do
  pane="${pane_script%%:*}"
  script="${pane_script#*:}"
  [ -n "$pane" ] || continue
  if [ "$(outer_tmux display-message -p -t "$pane" '#{pane_dead}')" = "1" ]; then
    outer_tmux respawn-pane -k -t "$pane" "$(pane_command "$script")"
  fi
done

outer_tmux set-option -t "$S" @aib_state_dir "$EZ2_COCKPIT_STATE_DIR"
outer_tmux set-option -t "$S" @aib_hub_socket "$HUB"
outer_tmux set-option -t "$S" @aib_instance "$EZ2_AIB_INSTANCE"

_war_rows="$(outer_tmux list-windows -t "$S" -F '#{window_id}|#{@aib_warroom}' 2>/dev/null || true)"
WAR_ID="$(awk -F '|' '$2 == 1 { print $1; exit }' <<<"$_war_rows")"
if [ -z "$WAR_ID" ]; then
  WAR_CMD="$(pane_command "$C/warroom-board.sh")"
  WAR_ID="$(outer_tmux new-window -d -t "$S:" -n war-room -P -F '#{window_id}' "$WAR_CMD")"
  outer_tmux set-option -w -t "$WAR_ID" @aib_warroom 1
fi

outer_tmux set-option -t "$S" status-style "$(cockpit_status_style 'bg=colour235,fg=colour245')"
# 10-10(the maintainer 眼驗令):最下面那行不再放 [Ez2 AIB v…],改成給使用者的提示(四語字典 aib.cockpit.footer_hint);
#   字典讀不到(印 !!key!! 或空)就退回中文字面,不讓狀態列空掉。前補 2 格與 Claude 輸入框下方三行對齊(同 hub 艦長列)。
#   版號仍在視窗標題與 @aib_title。title-beat.sh --install 也會設這行(接帶顏色的更新狀態),但它目前零呼叫點(見下面「終端機視窗標題」段)⇒ 實際生效的是這裡。
#   10-14 語系盤查第 23 條:原本開艙算一次寫死 ⇒ 切語系後這行一直是舊語言。改成 tmux #(…) 每次重畫狀態列(status-interval)重算。
#   ★ #(…) 裡不能有不成對的 ")"(case 的 ")" 會把指令切斷)、"%" 要寫 %%(status-left 先過 strftime,printf %s 會變秒數)
#     ⇒ 反引號 + grep 判 !!key!!、echo 輸出(實測 tmux 3.7c)。
#   ★ tmux 跑 #(…) 只給 server 的全域環境(不給 session 環境)⇒ 艙檔那層(EZ2_AIB_INSTANCE_HOME/locale)與狀態根要在這裡寫死進指令,
#     不然 aib lang --cabin 切了窗格換、底列不換(審查實測)。python 加 -I 不吃 PYTHONPATH。
_fh_env=""
for _v in EZ2_AIB_INSTANCE_HOME EZ2_AIB_STATE_ROOT EZ2_AIB_HOME; do
  [ -n "${!_v:-}" ] && _fh_env="$_fh_env$(printf '%s=%q ' "$_v" "${!_v}")"
done
_footer_hint="#(v=\`$_fh_env$(printf '%q -I %q' "$EZ2_PY3" "$C/i18n_lib.py") aib.cockpit.footer_hint 2>/dev/null\`; echo \"\$v\" | grep -q '^!!' && v=; [ -n \"\$v\" ] || v='若有任何想法,請訪問:aib.ez2.app'; echo \"\$v\")"
outer_tmux set-option -t "$S" status-left-length 40
outer_tmux set-option -t "$S" status-left "  $_footer_hint "
# 10-10(the maintainer 眼驗令):最下面那行不列視窗分頁(0:cockpit* 1:war-room),只留提示句。
#   ★ 逐窗設 window 選項,不動全域:預設 socket 是全機共用的,全域一改,別艙與使用者自己的 tmux 都跟著掉分頁。
#   cockpit 與 war-room 兩個窗到這裡都已存在(war-room 在上面剛建);分頁本來就只有這兩個。
for _w in $(outer_tmux list-windows -t "$S" -F '#{window_id}' 2>/dev/null || true); do
  outer_tmux set-option -w -t "$_w" window-status-format ''
  outer_tmux set-option -w -t "$_w" window-status-current-format ''
  outer_tmux set-option -w -t "$_w" window-status-separator ''
done

# ★★ 終端機視窗標題:接管它,並且第一個字就是**這是哪一艙**(the maintainer 08-16
#   直令:「aib 執行後最上方要有一個獨一無二的 aib 專屬 uuid,用來方便所有
#   AI 彼此定位」)。
#
#   在此之前 `set-titles` 是 off,所以視窗標題一直是終端機的預設(「終端機」
#   三個字),**開了幾艙都長一樣** —— 那正是要解的病。
#   ★ `title-beat.sh --install` 早就寫好了會開這兩個選項,但它**零呼叫點**
#     (全庫沒有任何地方叫它),所以從來沒生效過。這裡把它接上。
#
#   艙碼=`<session 名>·<instance-token 前八碼>`,**不新發識別碼**:那個
#   token 每艙唯一、早就存在、而且已經在每一席的環境變數裡,只是沒上過畫面。
#   完整字串由 title-beat 寫進 @aib_title;失敗一律吞掉不擋開艙(rc0 紅線)。
outer_tmux set-option -t "$S" set-titles on
outer_tmux set-option -t "$S" set-titles-string '#{@aib_title}'
# 先寫一次初值,免得裝好之後到第一次心跳之間標題是空的。
# ★ 這裡刻意用 shell 取前八碼而不是叫 title_beat.cabin_code():開艙路徑上
#   多一層 python 子行程就多一種靜默失敗。判準(前八碼、取不到印 no-token)
#   與那支函式同義,而**同義由 tests/test-title-beat.py 的
#   test_shell_and_python_cabin_code_agree 看守**,不靠人記得改兩處。
# 10-07 the maintainer:艙段改 `<IP>:[<艙位碼四碼>]`(與 dm 地址同一組碼);以前 `[<session 名>·<token 八碼>]` 把開艙時間印兩次。
#   艙位碼 = EZ2_AIB_INSTANCE 最後一個 `-` 之後、最多四碼(與 title_beat.cabin_label / dm_address.cabin_code 同式;
#   同義由 tests/test-title-beat.py 的 test_shell_and_python_cabin_code_agree 看守)。取不到印 no-cabin。
_aib_cabin="${EZ2_AIB_INSTANCE:-}"
_aib_cabin="${_aib_cabin##*-}"
[ "${#_aib_cabin}" -gt 4 ] && _aib_cabin="${_aib_cabin: -4}"
[ -n "$_aib_cabin" ] || _aib_cabin="no-cabin"
# IP 與 dm 抬頭同源(dm_header.local_addr);開艙路徑上這一個 python 子行程失敗就不印 IP,不擋開艙。
_aib_addr="$(cd "$C" 2>/dev/null && "${EZ2_PY3:-python3}" -c 'import dm_header; print(dm_header.local_addr())' 2>/dev/null || true)"
if [ -n "$_aib_addr" ]; then
  outer_tmux set-option -t "$S" @aib_title "AIB ${_aib_addr}:[${_aib_cabin}] $AIB_TITLE" 2>/dev/null || true
else
  outer_tmux set-option -t "$S" @aib_title "AIB [${_aib_cabin}] $AIB_TITLE" 2>/dev/null || true
fi
# Outer mouse routing provides click focus, native side/bottom copy-mode and
# border resize. The AIB click handler is installed on MouseUp only, so it does
# not consume the default MouseDown/Drag/DragEnd/Border state machine.
outer_tmux set-option -t "$S" mouse on
outer_tmux set-window-option -t "$S:cockpit" @aib_cockpit 1
# Neutral window fallback only. Pane-level colours come exclusively from the
# validated theme data and deliberately override these two compatibility keys.
outer_tmux set-window-option -t "$S:cockpit" window-style 'fg=default,bg=default'
outer_tmux set-window-option -t "$S:cockpit" window-active-style 'fg=default,bg=default'
outer_tmux set-window-option -t "$S:cockpit" pane-border-style 'fg=#0c1210,bg=#0c1210'
# 壁紙艙：框線用 #0c1210 近黑實心沉入森林底，避免 colour234 在壁紙上仍成灰線。
outer_tmux set-window-option -t "$S:cockpit" pane-active-border-style 'fg=#0c1210,bg=#0c1210'
outer_tmux set-window-option -t "$S:cockpit" pane-border-status off
# latest 會跟「最後一個變小的 client」走,開艙瞬間巢狀/窄窗會把右欄擠沒;
# largest 保留曾開過的最大幾何,右欄百分比切分才站得住。
outer_tmux set-window-option -t "$S:cockpit" window-size largest
outer_tmux rename-window -t "$S:cockpit" cockpit

RESIZE_CMD=""
# ★ 09-10 L 上線後實付:缺席的格是空字串,%q 印成 '' 再被下面 run-shell 的單引號包住就整個消失,
#   cockpit-resize.sh 只收到 3 個參數 ⇒ rc=2、每次 attach/resize 都在畫面上噴 usage。
#   缺席一律用 "-" 佔位(cockpit-resize.sh 把 "-" 當空),參數數目才守得住 5。
printf -v RESIZE_CMD 'env EZ2_COCKPIT_STATE_DIR=%q bash %q %q %q %q %q %q' \
  "$EZ2_COCKPIT_STATE_DIR" "$C/cockpit-resize.sh" "$S" \
  "${P_LEFT:--}" "${P_HUB:--}" "${P_RIGHT:--}" "${P_BOTTOM:--}"
outer_tmux set-hook -t "$S" client-resized "run-shell -b '$RESIZE_CMD'"
# 缺陷三十一:attach 那一輪帶事件名進去,resize 才分得出「attach 瞬間的不收斂」與真失敗。
outer_tmux set-hook -t "$S" client-attached "run-shell -b 'env AIB_RESIZE_EVENT=client-attached $RESIZE_CMD'"
outer_tmux set-hook -w -t "$S:cockpit" window-resized "run-shell -b '$RESIZE_CMD'"

# 10-09 a seat:下面四支綁的是**全機共用**的外層 tmux 表(所有艙一份),指令寫的是這棵樹的絕對路徑。
# 從開發 worktree 開艙會把每一艙的按鍵/滑鼠都改指向那棵樹(10-09 4f8d「無法打字」即此)。
# ⇒ 只有正本樹、安裝版、或隔離的測試 tmux 才裝;門在 global_bind_gate.sh。
. "$C/global_bind_gate.sh"
if aib_global_bind_ok "$C"; then
  env -u TMUX EZ2_TMUX_SESSION="$S" EZ2_COCKPIT_STATE_DIR="$EZ2_COCKPIT_STATE_DIR" \
    bash "$C/keys/install-keys.sh" --install
  env -u TMUX EZ2_TMUX_SESSION="$S" EZ2_AIHUB_SOCKET="$HUB" \
    EZ2_COCKPIT_STATE_DIR="$EZ2_COCKPIT_STATE_DIR" \
    bash "$C/nav/region-nav-ctl.sh" --install
  env -u TMUX EZ2_TMUX_SESSION="$S" EZ2_AIHUB_SOCKET="$HUB" \
    EZ2_COCKPIT_STATE_DIR="$EZ2_COCKPIT_STATE_DIR" \
    bash "$C/mouse/mouse-ctl.sh" --install
  # E2.16:視圖標籤導航(Tab/BTab/滑鼠點標籤)。★08-13 實測抓到的回退:這支
  # 從來不在 ensure 鏈裡——E2.16 lane 當年手裝一次,cockpit 重建(今晨 A 類
  # 死因後全新開艙)綁定就蒸發,「左上 1.人格 2.大腦 點不動」即此。裝進鏈=根治。
  env -u TMUX EZ2_TMUX_SESSION="$S" EZ2_AIHUB_SOCKET="$HUB" \
    EZ2_COCKPIT_STATE_DIR="$EZ2_COCKPIT_STATE_DIR" \
    bash "$C/nav/view-nav-ctl.sh" --install
fi
# E2.19:內層 status bar 改造成艦長選擇器(冪等;原本那行是預設樣式的
# session 名+主機+時鐘,即 08-13 截圖紅框——資訊零價值,還被誤讀成壞掉)。
# rc=3=degraded sync(documented,三輪審中2):hub 短暫不可讀時 ensure
# 不因艦長列降級而中止;其他 rc 仍屬真錯誤,set -e 照炸。
env -u TMUX EZ2_AIHUB_SOCKET="$HUB" \
  EZ2_AIB_INSTANCE_HOME="${EZ2_AIB_INSTANCE_HOME:-}" \
  bash "$C/nav/captains-ctl.sh" --install || [ $? -eq 3 ]
bash "$C/cockpit-resize.sh" "$S" "$P_LEFT" "$P_HUB" "$P_RIGHT" "$P_BOTTOM"
outer_tmux select-window -t "$S:cockpit"
outer_tmux select-pane -t "$P_HUB"
env -u TMUX EZ2_AIB_THEME_FILE="$EZ2_AIB_THEME_FILE" \
  EZ2_AIB_THEME_SNAPSHOT="$AIB_THEME_SNAPSHOT" \
  bash "$C/theme-ctl.sh" --install-hooks "$S"
env -u TMUX EZ2_AIB_THEME_FILE="$EZ2_AIB_THEME_FILE" \
  EZ2_AIB_THEME_SNAPSHOT="$AIB_THEME_SNAPSHOT" \
  bash "$C/theme-ctl.sh" --apply "$S"
# 待審框是疊在對話底下的,不進四格計畫。重開指揮艙要自己長回來,
# 否則畫面退回沒有黃按鈕／加高改留言的舊待審。失敗不得擋開艙。
env -u TMUX EZ2_TMUX_SESSION="$S" EZ2_AIHUB_SOCKET="$HUB" \
  EZ2_AIB_INSTANCE="${EZ2_AIB_INSTANCE:-}" \
  EZ2_AIB_INSTANCE_HOME="${EZ2_AIB_INSTANCE_HOME:-}" \
  EZ2_COCKPIT_STATE_DIR="$EZ2_COCKPIT_STATE_DIR" \
  bash "$C/pane-pending-review.sh" --ensure >/dev/null 2>&1 || true
