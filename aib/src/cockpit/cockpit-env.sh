#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 指揮艙共用設定 —— 所有外部路徑的唯一出口(E2.1)。
#
# demo 的病根是硬路徑散在各腳本(scratchpad 舊 session 路徑、舊大綱檔名、
# ~/.acb 直書)。遷入正本後,外部依賴一律走環境變數,預設值集中在這裡;
# 部署差異(別台機器/別個 vault)只改環境,不改腳本。
#
# 用法:每支腳本開頭 . "$(dirname "$0")/cockpit-env.sh"

COCKPIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export EZ2_COCKPIT_DIR="$COCKPIT_DIR"

# ★★ 2026-08-16:繼承來的路徑一律必須是絕對路徑,相對路徑是硬錯。
#
# 病例(aihub-8):session 環境帶著**加了引號所以沒被展開**的
#   EZ2_AIB_INSTANCE_HOME=~/.ez2-aib/instances/8
# 那不是家目錄,是一個叫「~」的相對目錄,落在哪完全取決於呼叫者的 cwd。
# 寫的人與讀的人用的是**逐字相同**的那條運算式,只因為 cwd 不同就分家:
#   aib(cwd=repo 根)         mkdir+寫席位 → <repo>/~/.ez2-aib/…/seats/   rc=0
#   pane-right-ai.sh(cwd=$HOME)  glob     → 不存在,零命中               rc=0
# **兩邊都成功**,而 @6/@7 在右欄顯示成「❓ -- 💤--」。同一顆壞值的第二個
# 發作點是 mouse-ctl.sh:320 的 `printf '%q'`——它把字面 ~ 跳脫成 `\~`,
# run-shell 的重導向在指令執行前就失敗,點擊整個沒被跑到。
#
# ★ 不做 expanduser 悄悄補救。相對路徑落在哪取決於 cwd,那本來就不是一個
#   可接受的輸入;靜靜替它補一個 $HOME 只會把「哪一次 cwd 不對」藏得更深。
# ★ 之所以必須擋在**進來的那一刻**:cockpit-full.sh:117 用 `printf '%q'`
#   把這些值烘進 relaunch 字串,壞值會一路傳染,重啟不會自癒。
# 上游拒收非絕對值之後換上的毒值標記。
# ★ 08-21 艦長讀 diff 時提的問題,實查後補的加固:毒值本身是**絕對路徑**,
#   所以任何「是不是絕對路徑」的下游守衛都會對它回答「是」。本庫實查:
#   `os.path.isabs` 七個呼叫點全部取自別的變數,毒值到不了它們(目前沒有洞);
#   但「絕對即豁免」這個形狀本庫確實有兩處(claim-gate-bash.py:248 絕對就不再
#   拼 cwd、lesson-reflux.py:350 絕對直接 continue 跳過後續檢查)。
#   所以至少讓**艦隊自己的**絕對路徑閘認得它:毒值進到這道閘是不合格,不是合格。
_AIB_REJECT_MARK_PREFIX="/nonexistent/aib-rejected-non-absolute-"
_aib_require_abs() {   # $1=變數名 $2=值;絕對路徑回 0,否則診斷後硬中止
  case "$2" in
    "$_AIB_REJECT_MARK_PREFIX"*)
      echo "AIB path already rejected upstream: $1=$2" >&2
      echo "  這是上游拒收後換上的毒值標記,不是可用路徑;絕對路徑檢查不得放行它。" >&2
      case "$-" in
        *i*) return 2 ;;
      esac
      exit 2 ;;
    /*) return 0 ;;
  esac
  echo "AIB path must be absolute: $1=$2" >&2
  case "$2" in
    '~'*)
      echo "  開頭的 ~ 沒有被展開。多半是賦值時加了引號:" >&2
      echo "  $1=\"~/x\"(壞)應寫成 $1=\"\$HOME/x\"(對)" >&2
      ;;
  esac
  # ★★ 這裡必須是 `exit` 而不是本檔既有的 `return 2 2>/dev/null || exit 2`。
  # 那個慣例把「呼叫端要不要停」外包給呼叫端的 `set -e`,而 08-17 實測:
  # **33 支 shell 消費者裡 16 支沒有 -e,且幾乎正好是全部的 pane-*.sh 渲染器**
  # (pane-right-ai / pane-left-* / pane-bottom-* / warroom-board / ctl-*-depth
  #  / dm-ctl)。★ 數 16 這個數字時要小心:`set -uo` 這種字串裡的 "set" 本身
  # 就含 e,用 `*e*` 比對整行會把它誤判成有 -e(我第一次就漏了 dm-ctl.sh,
  # 由 e5300 抓到)。要只取 `-` 後面的旗標字元。
  # 對它們 `return` 等於什麼都沒做——而渲染器正是這顆 bug 的**讀取端**,
  # 是畫面上顯示「❓ --」的那一側。守衛若在讀取端無聲通過,就又是一個
  # 「寫好了、測試全綠、而它不在任何人會走的路徑上」。
  case "$-" in
    *i*) return 2 ;;   # 互動 shell:只回報,不關掉真人的終端
  esac
  exit 2
}

# Instance identity.  `main` keeps every historical default byte-for-byte;
# a named instance gets its own outer tmux session, inner tmux socket and
# runtime state.  The launcher clears inherited derived values before setting
# EZ2_AIB_INSTANCE for `new`, so a command issued from inside an older cockpit
# cannot accidentally attach to that older cockpit.
# 刀 A(2026-08-23):四元組由 cabin_resolve.sh 一次算完,本檔不再自己拼艙名、
# 也不再自己比對主艙名。★ 失敗要**穿透 source**:`aib_cabin_resolve` 是函式,
# 它的 return 只結束自己,所以這裡必須逐層把 rc 接住再 return——
# 少接一層,呼叫端拿到的就是 0,而「有沒有 fail-closed」會得到永遠的綠。
# ★ 先檢查存在再 source:少了這一格,bash 只會丟一句自己的
#   「No such file or directory」,而那句話**不會說出這是誰的相依**。
#   刀 A 把 cabin_resolve.sh 變成 cockpit-env.sh 的硬相依(2026-08-23),
#   任何只複製 cockpit-env.sh 的治具或部署都會在這裡爆,而且爆得像環境壞掉。
#   fail-closed 的訊息紀律與 R3 一致:點名那個檔、點名它從哪來。
if [ ! -f "$COCKPIT_DIR/cabin_resolve.sh" ]; then
  echo "AIB cabin resolver missing: $COCKPIT_DIR/cabin_resolve.sh" >&2
  echo "  它是 cockpit-env.sh 的硬相依(刀 A,2026-08-23):只複製 cockpit-env.sh 的樹要一起複製它。" >&2
  return 2 2>/dev/null || exit 2
fi
. "$COCKPIT_DIR/cabin_resolve.sh" || { return 2 2>/dev/null || exit 2; }
aib_cabin_resolve || { return 2 2>/dev/null || exit 2; }
case "$EZ2_AIB_INSTANCE" in
  "$AIB_CABIN_PRIMARY") ;;
  *[!a-z0-9-]*|'')
    echo "invalid AIB instance: $EZ2_AIB_INSTANCE" >&2
    return 2 2>/dev/null || exit 2
    ;;
esac

# ED.1(2026-08-22):狀態根可注入——EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > $HOME/.ez2-aib,
# 與 src/cockpit/seats_dir_lib.state_root()、aib:aib_seats_dir 同一式子。這裡是
# EZ2_AIB_INSTANCE_HOME 的**上游**:上游不認注入,下游每一處認了也沒用。
_AIB_STATE_ROOT="$(aib_cabin_state_root)"
_AIB_INSTANCE_HOME="$EZ2_AIB_INSTANCE_HOME"          # 上面已由 cabin_resolve 算好並匯出
_AIB_DEFAULT_SESSION="$EZ2_TMUX_SESSION"
_AIB_DEFAULT_HUB="$EZ2_AIHUB_SOCKET"
# 擋在這裡而不是更後面:下面第一件事就是拿它去讀 instance-token,
# 而 EZ2_COCKPIT_STATE_DIR 由它推導、推導完就 mkdir。
_aib_require_abs EZ2_AIB_INSTANCE_HOME "$EZ2_AIB_INSTANCE_HOME" || \
  { return 2 2>/dev/null || exit 2; }
export EZ2_AIB_INSTANCE_TOKEN="${EZ2_AIB_INSTANCE_TOKEN:-}"
if [ "$EZ2_AIB_CABIN_NAMED" = "1" ] && [ -z "$EZ2_AIB_INSTANCE_TOKEN" ]; then
  _AIB_TOKEN_FILE="$EZ2_AIB_INSTANCE_HOME/instance-token"
  if [ -f "$_AIB_TOKEN_FILE" ] && [ ! -L "$_AIB_TOKEN_FILE" ]; then
    IFS= read -r EZ2_AIB_INSTANCE_TOKEN < "$_AIB_TOKEN_FILE" || true
    export EZ2_AIB_INSTANCE_TOKEN
  fi
fi
if [ -n "$EZ2_AIB_INSTANCE_TOKEN" ]; then
  if [ "${#EZ2_AIB_INSTANCE_TOKEN}" -ne 32 ] || [[ "$EZ2_AIB_INSTANCE_TOKEN" == *[!0-9a-f]* ]]; then
    echo "invalid AIB instance ownership token" >&2
    return 2 2>/dev/null || exit 2
  fi
fi

# repo 根與大綱正本(戰情室機器可讀源)
export EZ2_AIB_ROOT="${EZ2_AIB_ROOT:-$(cd "$COCKPIT_DIR/../.." && pwd)}"
export EZ2_OUTLINE="${EZ2_OUTLINE:-$EZ2_AIB_ROOT/docs/EZ2-AIB-DEV-OUTLINE.md}"
# 擋在讀 VERSION 之前:相對的 EZ2_AIB_ROOT 會讓下面那個檢查報成
# 「VERSION 不見了」——把路徑錯誤誤導成檔案錯誤。
_aib_require_abs EZ2_AIB_ROOT "$EZ2_AIB_ROOT" || { return 2 2>/dev/null || exit 2; }
_aib_require_abs EZ2_OUTLINE "$EZ2_OUTLINE" || { return 2 2>/dev/null || exit 2; }
_AIB_VERSION_FILE="$EZ2_AIB_ROOT/VERSION"
if [ ! -f "$_AIB_VERSION_FILE" ] || [ -L "$_AIB_VERSION_FILE" ]; then
  echo "AIB VERSION is missing or unsafe: $_AIB_VERSION_FILE" >&2
  return 2 2>/dev/null || exit 2
fi
IFS= read -r _AIB_VERSION < "$_AIB_VERSION_FILE" || true
if [[ ! "$_AIB_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  echo "AIB VERSION is invalid: $_AIB_VERSION" >&2
  return 2 2>/dev/null || exit 2
fi
export EZ2_AIB_VERSION="$_AIB_VERSION"
unset _AIB_VERSION _AIB_VERSION_FILE

# Four-region theme. The JSON is the only colour source; theme.py validates
# ownership, file mode, schema and xterm values before cockpit-full mutates tmux.
# 10-14(the maintainer「也要有轉成佈景」):使用者選的佈景 —— `aib theme use <名>` 寫 <狀態根>/theme,
#   佈景裝在 <狀態根>/themes/<名>/theme.json(aib ext install 裝的)。沒選、名字不合、檔不在 ⇒ 照舊用內建那套。
if [ -z "${EZ2_AIB_THEME_FILE:-}" ]; then
  _aib_theme_sel=""
  { IFS= read -r _aib_theme_sel < "$_AIB_STATE_ROOT/theme"; } 2>/dev/null || true
  if [[ "$_aib_theme_sel" =~ ^[A-Za-z0-9._-]+$ ]] && [ -f "$_AIB_STATE_ROOT/themes/$_aib_theme_sel/theme.json" ]; then
    EZ2_AIB_THEME_FILE="$_AIB_STATE_ROOT/themes/$_aib_theme_sel/theme.json"
  fi
  unset _aib_theme_sel
fi
export EZ2_AIB_THEME_FILE="${EZ2_AIB_THEME_FILE:-$EZ2_AIB_ROOT/themes/aib-default.json}"
_aib_require_abs EZ2_AIB_THEME_FILE "$EZ2_AIB_THEME_FILE" || \
  { return 2 2>/dev/null || exit 2; }

# 執行期狀態檔(bottom-tab/brain-depth/view-depth)——絕不寫進 repo
export EZ2_COCKPIT_STATE_DIR="${EZ2_COCKPIT_STATE_DIR:-$EZ2_AIB_INSTANCE_HOME/cockpit}"
# ★ 必須擋在 mkdir 之前:相對路徑的 mkdir -p 會**成功**,在呼叫者的 cwd
#   底下長出一棵狀態樹(08-16 就是這樣在 repo 根長出 `~/`)。
_aib_require_abs EZ2_COCKPIT_STATE_DIR "$EZ2_COCKPIT_STATE_DIR" || \
  { return 2 2>/dev/null || exit 2; }
if [ "${AIB_NO_STATE_WRITE:-0}" != "1" ]; then
  mkdir -p "$EZ2_COCKPIT_STATE_DIR"
fi

# 沙盒任務板
export EZ2_TASKS_DIR="${EZ2_TASKS_DIR:-$EZ2_COCKPIT_STATE_DIR/sandbox-tasks}"
_aib_require_abs EZ2_TASKS_DIR "$EZ2_TASKS_DIR" || { return 2 2>/dev/null || exit 2; }

# the maintainer 2026-10-13:EZ2_ACB_HOME 別名已撤。繼承到的值清掉。
unset EZ2_ACB_HOME EZ2_ACB_HOME_REJECTED AIB_REQUIRE_ACB_HOME 2>/dev/null || true

# 艦隊看板正本(share/claude-ui)
export EZ2_FLEET_BOARD="${EZ2_FLEET_BOARD:-$HOME/.claude/ez2-fleet-board.py}"

# Codex rollout 是目前唯一能雙向驗證、且提供可公開 child UUID 的子 AI 來源。
# Claude 的 temporary tasks 目錄混有背景 shell 命令，禁止當作子 AI 身份來源。
export EZ2_CODEX_SESSIONS_ROOT="${EZ2_CODEX_SESSIONS_ROOT:-$HOME/.codex/sessions}"

# 人格 runtime 與 CTX 實測
export EZ2_PERSONA_RUNTIME="${EZ2_PERSONA_RUNTIME:-$HOME/.ez2-persona-runtime}"
export EZ2_CTX_DIR="${EZ2_CTX_DIR:-$HOME/.ez2/ctx}"

# 第二大腦:route registry(先 worktree 後本機 checkout)與 vault
if [ -z "${EZ2_ROUTE_REGISTRY:-}" ]; then
  # 寫成顯式 if 而非 `[ -f x ] && VAR=x && break` 的 AND 串:後者在 errexit 下
  # 不會中止(失敗發生在 AND 串的非末段,bash/zsh 皆豁免,已實測),但只要
  # 有人把這種 AND 串挪成本檔的最後一句,source 的回傳值就是 1,而 `. file`
  # 在呼叫端是簡單命令,set -e 就會讓呼叫端整支退出。顯式 if 讓這個陷阱
  # 不存在,也比 AND 串好讀。
  r="$HOME/ez2-ai/samples/obsidian/obsidian-route-registry-contract.json"
  if [ -f "$r" ]; then
    EZ2_ROUTE_REGISTRY="$r"
  fi
fi
# 10-12 新用戶:vault 預設照 route_register.default_vault(EZ2_VAULT_DIR > EZ2_VAULT_ROOT > ~/Desktop/Obsidian/Ez2 有才算
#   > ~/.ez2-aib/vault);契約都找不到、vault 又是 aib 自己的預設 ⇒ 用套件附的預設契約(有自己 vault 的照舊不猜)。
if [ -z "${EZ2_VAULT_DIR:-}" ]; then
  if [ -n "${EZ2_VAULT_ROOT:-}" ]; then EZ2_VAULT_DIR="$EZ2_VAULT_ROOT"
  elif [ -d "$HOME/Desktop/Obsidian/Ez2" ]; then EZ2_VAULT_DIR="$HOME/Desktop/Obsidian/Ez2"
  else EZ2_VAULT_DIR="${EZ2_AIB_HOME:-$HOME/.ez2-aib}/vault"; fi
fi
export EZ2_VAULT_DIR
# 10-12 the maintainer「一併修正」:團隊新機(有 Ez2 知識庫、沒有 ez2-ai)⇒ 私有倉隨附的 ez2-ai 正本副本(公開版沒有這個檔)
if [ -z "${EZ2_ROUTE_REGISTRY:-}" ] && [ -f "$COCKPIT_DIR/../persona_route/vendor/obsidian-route-registry-contract.json" ]; then
  EZ2_ROUTE_REGISTRY="$COCKPIT_DIR/../persona_route/vendor/obsidian-route-registry-contract.json"
fi
# 10-12 a seat(Pro 的 vault 是 ~/Ez2/Vault,公開版 newrole 一律 rc=2):自訂 vault 也用預設契約,但只在安全時 ——
#   還沒有 / 空的、有自己的 uid 索引、已有 10-Roles。同 route_register.bundled_ok_for_vault。
_aib_bundled_ok() {
  [ -d "$1" ] || return 0
  [ -z "$(ls -A "$1" 2>/dev/null | grep -v '^\.')" ] && return 0
  [ -f "$1/.ez2/index/route-key-index.json" ] && grep -q '"uid_layout"' "$1/.ez2/migration-state.json" 2>/dev/null && return 0
  [ -d "$1/10-Roles" ]
}
if [ -z "${EZ2_ROUTE_REGISTRY:-}" ] && [ -f "$COCKPIT_DIR/../persona_route/default-route-contract.json" ] \
   && { [ "$EZ2_VAULT_DIR" = "${EZ2_AIB_HOME:-$HOME/.ez2-aib}/vault" ] || _aib_bundled_ok "$EZ2_VAULT_DIR"; }; then
  EZ2_ROUTE_REGISTRY="$COCKPIT_DIR/../persona_route/default-route-contract.json"
fi
unset -f _aib_bundled_ok
export EZ2_ROUTE_REGISTRY="${EZ2_ROUTE_REGISTRY:-}"
# ★ 08-17 艦外裁決(L2-00 對清):全機 69 份同名契約四種版本,**D lane(人格/大腦
#   綁定)讀契約一律 `git show main:samples/…`,不走工作樹檔案系統**。
#   本變數=給 card_registry / bind_one / binding_projection 的 `--contract` 規格:
#     顯式 EZ2_ROUTE_CONTRACT_SPEC > ez2-ai repo 在(EZ2_EZ2AI_REPO,預設 ~/ez2-ai,
#     bare 亦可)則 git:<repo>#main:<相對路徑> > 退回上面那個檔案路徑(舊形)。
#   它不過 _aib_require_abs(git: 規格不是路徑);讀端 route_register 兩形都認。
export EZ2_EZ2AI_REPO="${EZ2_EZ2AI_REPO:-$HOME/ez2-ai}"
if [ -z "${EZ2_ROUTE_CONTRACT_SPEC:-}" ]; then
  if git -C "$EZ2_EZ2AI_REPO" rev-parse --verify -q main >/dev/null 2>&1; then
    EZ2_ROUTE_CONTRACT_SPEC="git:$EZ2_EZ2AI_REPO#main:samples/obsidian/obsidian-route-registry-contract.json"
  else
    EZ2_ROUTE_CONTRACT_SPEC="$EZ2_ROUTE_REGISTRY"
  fi
fi
export EZ2_ROUTE_CONTRACT_SPEC
export EZ2_VAULT_DIR   # 已在上面照 default_vault 的規則定好(10-12)

# 其餘外部路徑一起過同一道閘。★ 只擋 INSTANCE_HOME/STATE_DIR 是不夠的
# (e5300 08-17 指出):同樣的錯從別的變數進來時,**下游不同、症狀會長得
# 完全不一樣**,沒有人會聯想到是同一顆病。
_aib_require_abs EZ2_FLEET_BOARD "$EZ2_FLEET_BOARD" || { return 2 2>/dev/null || exit 2; }
_aib_require_abs EZ2_CODEX_SESSIONS_ROOT "$EZ2_CODEX_SESSIONS_ROOT" || \
  { return 2 2>/dev/null || exit 2; }
_aib_require_abs EZ2_PERSONA_RUNTIME "$EZ2_PERSONA_RUNTIME" || \
  { return 2 2>/dev/null || exit 2; }
_aib_require_abs EZ2_CTX_DIR "$EZ2_CTX_DIR" || { return 2 2>/dev/null || exit 2; }
_aib_require_abs EZ2_VAULT_DIR "$EZ2_VAULT_DIR" || { return 2 2>/dev/null || exit 2; }
# ★ EZ2_ROUTE_REGISTRY 可以是空字串——空=「這台機器上沒有這個東西」,
#   是合法狀態,不是壞路徑。只有非空時才要求絕對。
if [ -n "$EZ2_ROUTE_REGISTRY" ]; then
  _aib_require_abs EZ2_ROUTE_REGISTRY "$EZ2_ROUTE_REGISTRY" || \
    { return 2 2>/dev/null || exit 2; }
fi
# ★ EZ2_PY3 刻意不過這道閘:它的最後手段預設是裸的 `python3`(靠 PATH 解),
#   那是既有的刻意設計,不是壞路徑。

# tmux 佈局(外層 session 名/內層 ai-hub socket)
export EZ2_TMUX_SESSION="${EZ2_TMUX_SESSION:-$_AIB_DEFAULT_SESSION}"
# 供下游判斷「session 名是本程式推導的,還是外部覆寫的」。
# 破壞性操作(如自癒重建)只在推導名下才可依結構特徵進行——
# 覆寫時我們無從得知那個 session 屬於誰(codex 審 cockpit 五輪反例)。
if [ "$EZ2_TMUX_SESSION" = "$_AIB_DEFAULT_SESSION" ]; then
  export EZ2_AIB_SESSION_IS_DERIVED=1
else
  export EZ2_AIB_SESSION_IS_DERIVED=0
fi
export EZ2_AIHUB_SOCKET="${EZ2_AIHUB_SOCKET:-$_AIB_DEFAULT_HUB}"

# python3 絕對路徑(HANDOFF 血淚 4:tmux #() 與 run-shell 是最小環境)
if [ -z "${EZ2_PY3:-}" ]; then
  for p in /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -x "$p" ]; then
      EZ2_PY3="$p"
      break
    fi
  done
fi
export EZ2_PY3="${EZ2_PY3:-python3}"

# 讓 heredoc python 找得到 cockpit_lib
export PYTHONPATH="$EZ2_COCKPIT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# ── 機器狀態列的熱度尺(the maintainer 2026-09-04 令「接」)─────────────────────────
# 三格(負載 / CPU 溫度 / 重活)委派給 tools/heat/rulers.py,而那支還在
# `root/heat-rulers-0904`(d129d31e)未進 main。machine_status 自己有具名縫:
# `EZ2_HEAT_RULERS` > 本樹 `tools/heat/rulers.py`,合入前就是指到那棵樹。
#
# ★ 這一段刻意寫成「本樹**沒有**才指出去」,不是無條件設:
#   machine_status 的順位是 env 優先,無條件設會讓合入之後面板**永遠讀那棵工作樹**——
#   而那棵樹可能被刪、可能落後,失效形狀是畫面上一組看起來完全正常的舊數字。
#   合入之後本樹就有那支檔,這個 if 自動變成 no-op,不必有人記得回來刪。
# ★ 指不到時不設:machine_status 會誠實印「量不到(讀取失敗)」,不會回 0,
#   也不會拿電池溫度冒充 CPU(5d3eb 09-04 02:0x 裁)。
if [ -z "${EZ2_HEAT_RULERS:-}" ] && [ ! -f "$EZ2_AIB_ROOT/tools/heat/rulers.py" ]; then
  _aib_heat_rulers="$HOME/worktrees/root-heat-rulers-0904/tools/heat/rulers.py"
  [ -f "$_aib_heat_rulers" ] && export EZ2_HEAT_RULERS="$_aib_heat_rulers"
  unset _aib_heat_rulers
fi
# 檔案 mtime(秒)的跨平台取法(09-28 修紅):原本各處寫 `stat -f %m`(只有 macOS 認得)。
#   Linux(雲端、a CI machine)的 GNU stat 把 -f 當「檔案系統資訊」,會把一大段文字印到 stdout,
#   `$(( ... ))` 直接語法錯,右欄的綁定提醒快取永遠判不新 ⇒ 舊提醒清不掉。先試 GNU、再試 BSD,都不是數字就回預設。
aib_mtime() {   # $1=檔案 $2=取不到時的預設(預設 0)
  local _m
  _m=$(stat -c %Y "$1" 2>/dev/null) || _m=$(stat -f %m "$1" 2>/dev/null) || _m=""
  case "$_m" in ''|*[!0-9]*) printf '%s\n' "${2:-0}" ;; *) printf '%s\n' "$_m" ;; esac
}
