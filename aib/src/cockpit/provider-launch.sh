#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# provider-launch v2.0.0 — Launch one provider CLI for a cockpit seat.
#
# ED.11(2026-08-13,the maintainer 直令「不遵守 ACB 權限,我們要拆除」):
# 預設改為**直接執行 provider CLI**,不再經過 ACB 載體。
#
# 為什麼:舊路徑 `exec acb <provider> <mode> --role ...` 會進 ACB 的 BIND 儀式,
# 而該儀式死於入冊的 A 類死因(`bootstrap capture does not have the exact outer
# shape` → receipt verification failed → pane 死),且裁決明定 ACB 廢止前提下
# 不投入修繕——所以「開席位」在本機是必死路徑,aib 永遠進不了開發畫面。
#
# 誠實邊界:直起的席位**沒有任何密碼學身份**,trust=self_reported,由 aib 啟動器
# 寫進席位紀錄;絕不冒充 registry_verified(那個狀態隨 ACB 一起死了,EH grant
# 模型上線前不會有繼任者)。EZ2_AIB_CARRIER=acb 不再執行 acb,直接拒絕。
set -euo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/identity-env.sh"

# 第 5 參 session_uuid 是 optional；第 6 參是 requested model。舊的 4/5 參
# 呼叫仍從 provider registry 解析一個可稽核的 launch default。
# 套組開著時，CLI 的 argv 帶明示 --model。套組關掉、而且這次沒有手指定型號，
# 則不代塞 --model，讓 codex 讀自己的設定檔。
case "$#" in 4|5|6) ;; *) echo "provider-launch: expected provider mode role cwd [session_uuid] [requested_model]" >&2; exit 2 ;; esac
PROVIDER="$1"
MODE="$2"
ROLE="$3"
PROJECT_DIR="$4"
SESSION_UUID="${5:-}"
REQUESTED_MODEL="${6:-}"

case "$PROVIDER" in claude|codex|agy|copilot|grok|deepseek|nvidia|openrouter|typesafe|openrouterjev|sand|grokbot|codexbot|claudebot|musebot) ;; *) echo "provider-launch: unsupported native provider: $PROVIDER" >&2; exit 2 ;; esac
case "$MODE" in safe|dgrs) ;; *) echo "provider-launch: invalid mode: $MODE" >&2; exit 2 ;; esac
# Codex 帳號管理與自動續跑是同一套。0＝這一席走原本的命令列。
_codex_pack=1
if [ "$PROVIDER" = "codex" ] && [ "${AIB_CODEX_PACK:-1}" = "0" ]; then
  _codex_pack=0
fi
[[ "$ROLE" =~ ^[a-z0-9-]+$ ]] || { echo "provider-launch: invalid role: $ROLE" >&2; exit 2; }
[ -d "$PROJECT_DIR" ] || { echo "provider-launch: missing directory: $PROJECT_DIR" >&2; exit 2; }
# 傳入就必須是完整 uuid（transcript 檔名同形，小寫）。壞值大聲擋，不靜默丟。
if [ -n "$SESSION_UUID" ]; then
  [[ "$SESSION_UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
    echo "provider-launch: invalid session uuid: $SESSION_UUID" >&2; exit 2; }
fi
if [ -z "$REQUESTED_MODEL" ]; then
  PY3="${EZ2_PY3:-python3}"
  REQUESTED_MODEL="$("$PY3" -c 'import json,sys
rows=json.load(open(sys.argv[1], encoding="utf-8")).get("providers", [])
match=[r for r in rows if r.get("id")==sys.argv[2] and r.get("status")=="supported"]
value=match[0].get("default_requested_model", "") if len(match)==1 else ""
print(value)' "$HERE/providers.json" "$PROVIDER")" || REQUESTED_MODEL=""
fi
[[ "$REQUESTED_MODEL" =~ ^[0-9A-Za-z][0-9A-Za-z._:/-]*$ ]] || {
  echo "provider-launch: requested model is missing or invalid" >&2; exit 2; }

# ★ 推理深度(2026-09-08,the maintainer 直裁「copilot 預設開啟為 kimi 最新 high」)。
#   ★ **資料驅動不是字面驅動**:只有 providers.json 那一格寫了 default_effort 的家
#     才會帶旗標。理由是各家 CLI 不同 —— copilot 有 `--effort/--reasoning-effort`
#     (1.0.83 實測 --help,choices = none/minimal/low/medium/high/xhigh/max),
#     而 agy 是把深度寫進模型字串(`gemini-3.1-pro-high`)。
#     **替沒有那個旗標的家帶上去 = 那一席開不起來**,所以要加新的一家之前,
#     先去該家 CLI 的 --help 量一次再往 JSON 裡寫。
#   ★ 取不到就不帶,不猜、也不擋開席:深度是偏好不是前置條件。
#   ★ 值必須在閉集內;寫錯字大聲擋 —— 靜默丟掉的話,席位會用一個沒人選過的深度跑。
REQUESTED_EFFORT=""
if [ "${AIB_NO_EFFORT:-0}" != "1" ]; then
  PY3="${EZ2_PY3:-python3}"
  REQUESTED_EFFORT="$("$PY3" -c 'import json,sys
rows=json.load(open(sys.argv[1], encoding="utf-8")).get("providers", [])
match=[r for r in rows if r.get("id")==sys.argv[2] and r.get("status")=="supported"]
print(match[0].get("default_effort", "") if len(match)==1 else "")' \
    "$HERE/providers.json" "$PROVIDER")" || REQUESTED_EFFORT="__READ_FAILED__"
fi
# ★ 第二審(Gemini)抓到:原本 `|| REQUESTED_EFFORT=""` 把「JSON 壞掉/讀不到」
#   靜默變成「這一家沒寫深度」—— 而註解上一段才剛宣稱「寫錯字大聲擋」。
#   **讀不到不是沒有**:登記簿壞掉要出聲,不能靠一個空字串把它變成正常路徑。
if [ "$REQUESTED_EFFORT" = "__READ_FAILED__" ]; then
  echo "provider-launch: 讀不到 providers.json 的 default_effort(登記簿壞了?)" >&2
  exit 2
fi
case "$REQUESTED_EFFORT" in
  ""|none|minimal|low|medium|high|xhigh|max) ;;
  *) echo "provider-launch: invalid default_effort: $REQUESTED_EFFORT" >&2; exit 2 ;;
esac

# 身份提示殘留一樣要清:pm 窗裡打 `aib ... dev new` 不得讓新席繼承 pm 的環境。
ENV_COMMAND=(env)
for name in "${AIB_CARRIER_SCRUB_VARS[@]}"; do
  ENV_COMMAND+=( -u "$name" )
done

# Desktop bridge must never enter CLI/ACB launch or alter desktop permissions.
if [ "$PROVIDER" = "codexbot" ]; then
  # shellcheck source=identity-env.sh
  . "$HERE/identity-env.sh"
  aib_normalize_codexbot_env
  exec "${ENV_COMMAND[@]}" "${EZ2_PY3:-python3}" "$HERE/codexbot_bridge.py" \
    --session-id "$SESSION_UUID" --thread-id "${AIB_CODEXBOT_THREAD_ID:-}" \
    --endpoint "${AIB_CODEXBOT_ENDPOINT:-}"
fi

if [ "${EZ2_AIB_CARRIER:-direct}" = "acb" ]; then
  # ACB 已關。就算 acb 執行檔還在,也不再交給它開席。
  echo "provider-launch: ACB 載體已關閉,不執行 acb" >&2
  exit 127
fi

# --- 直起路徑(預設)---
# --- deepseek (API-key 終端席；非 OAuth CLI / ED.4) -------------------------
# 不依賴 PATH 上的同名 CLI；直接 exec 本倉 deepseek_cli.py。
# 缺 DEEPSEEK_API_KEY 由 CLI 具名退出，訊息不指向 ED.4。
if [ "$PROVIDER" = "deepseek" ]; then
  PY3="${EZ2_PY3:-python3}"
  DS_ARGS=(--model "$REQUESTED_MODEL" --cwd "$PROJECT_DIR" --role "$ROLE" --mode "$MODE")
  [ -n "$SESSION_UUID" ] && DS_ARGS+=(--session-id "$SESSION_UUID")
  exec "${ENV_COMMAND[@]}" "$PY3" "$HERE/deepseek_cli.py" "${DS_ARGS[@]}"
fi

# --- nvidia (API-key 終端席；NVIDIA NIM OpenAI-compat；非 OAuth CLI / ED.4) ---
# 不依賴 PATH 上的同名 CLI；直接 exec 本倉 nvidia_cli.py。
# 缺 NVIDIA_API_KEY 由 CLI 具名退出，訊息不指向 ED.4。
if [ "$PROVIDER" = "nvidia" ]; then
  PY3="${EZ2_PY3:-python3}"
  NV_ARGS=(--model "$REQUESTED_MODEL" --cwd "$PROJECT_DIR" --role "$ROLE" --mode "$MODE")
  [ -n "$SESSION_UUID" ] && NV_ARGS+=(--session-id "$SESSION_UUID")
  exec "${ENV_COMMAND[@]}" "$PY3" "$HERE/nvidia_cli.py" "${NV_ARGS[@]}"
fi

# --- openrouter (API-key 終端席；OpenRouter OpenAI-compat；非 OAuth CLI / ED.4) ---
# 不依賴 PATH 上的同名 CLI；直接 exec 本倉 openrouter_cli.py。
# 缺 OPENROUTER_API_KEY 由 CLI 具名退出，訊息不指向 ED.4。
if [ "$PROVIDER" = "openrouter" ]; then
  PY3="${EZ2_PY3:-python3}"
  OR_ARGS=(--model "$REQUESTED_MODEL" --cwd "$PROJECT_DIR" --role "$ROLE" --mode "$MODE")
  [ -n "$SESSION_UUID" ] && OR_ARGS+=(--session-id "$SESSION_UUID")
  exec "${ENV_COMMAND[@]}" "$PY3" "$HERE/openrouter_cli.py" "${OR_ARGS[@]}"
fi

# --- typesafe (API-key 終端席；TypeSafe System One native；非 OAuth CLI / ED.4) ---
# 不依賴 PATH 上的同名 CLI；直接 exec 本倉 typesafe_cli.py。
# 缺 TYPESAFE_API_KEY 由 CLI 具名退出，訊息不指向 ED.4。
if [ "$PROVIDER" = "typesafe" ]; then
  PY3="${EZ2_PY3:-python3}"
  TS_ARGS=(--model "$REQUESTED_MODEL" --cwd "$PROJECT_DIR" --role "$ROLE" --mode "$MODE")
  [ -n "$SESSION_UUID" ] && TS_ARGS+=(--session-id "$SESSION_UUID")
  exec "${ENV_COMMAND[@]}" "$PY3" "$HERE/typesafe_cli.py" "${TS_ARGS[@]}"
fi

# --- openrouterjev (API-key 終端席;同一支 System One 問答介面,走 OpenRouter 決策端點;鑰=OPENROUTER_API_KEY) ---
# TypeSafe 對外開放前 an internal service 只在 OpenRouter 上;選單分開標「OpenRouter an internal service」/「TypeSafe an internal service」(the maintainer 09-25)。
if [ "$PROVIDER" = "openrouterjev" ]; then
  PY3="${EZ2_PY3:-python3}"
  OJ_ARGS=(--model "$REQUESTED_MODEL" --cwd "$PROJECT_DIR" --role "$ROLE" --mode "$MODE" --route openrouter)
  [ -n "$SESSION_UUID" ] && OJ_ARGS+=(--session-id "$SESSION_UUID")
  exec "${ENV_COMMAND[@]}" "$PY3" "$HERE/typesafe_cli.py" "${OJ_ARGS[@]}"
fi



# --- sand (Cursor 橋接席)-------------------------------------------------
# 不走 PATH 上的同名 CLI；直接 exec 本倉 sand_bridge.py。
# 必須在通用 BIN 查找之前：否則會誤報「找不到 provider CLI:sand」。
if [ "$PROVIDER" = "sand" ] || [ "$PROVIDER" = "grokbot" ] || [ "$PROVIDER" = "claudebot" ] || [ "$PROVIDER" = "musebot" ]; then
  PY3="${EZ2_PY3:-python3}"
  # sand_bridge 拒收短於 8 字的 --session-id。開席側應已由 AIB_SESSION_ID_PROVIDERS
  # 預發 uuid;若仍空(舊呼叫/漏網),這裡補發,避免死席印 invalid --session-id。
  if [ -z "$SESSION_UUID" ]; then
    SESSION_UUID="$("$PY3" -c 'import uuid;print(uuid.uuid4())' 2>/dev/null || true)"
    [ -n "$SESSION_UUID" ] || {
      echo "provider-launch: $PROVIDER 需要 session uuid,產生失敗" >&2
      exit 2
    }
    echo "provider-launch: $PROVIDER 未帶 session uuid,已補發 $SESSION_UUID" >&2
  fi
  SAND_ARGS=(--session-id "${SESSION_UUID}" --role "$ROLE" --cwd "$PROJECT_DIR" --mode "$MODE" --model "$REQUESTED_MODEL" --provider-id "$PROVIDER")
  [ -n "$REQUESTED_EFFORT" ] && SAND_ARGS+=(--effort "$REQUESTED_EFFORT")
  # 10-07 a seat(3f999 報):ENV_COMMAND 會 `env -u EZ2_AIB_INSTANCE`(防身分外漏,刻意的),橋就讀不到自己的艙 ⇒
  #   status.json 的 cabin 一直是空字串,bot 寄信的抬頭艙位碼只能退回掃登記簿。洗之前先算好艙位碼四碼
  #   (與 dm_header.cabin_tail 同式),用不在洗單上的 AIB_BRIDGE_CABIN 交給橋;只給碼,不給整個艙 id。
  _bridge_cabin="${EZ2_AIB_INSTANCE:-}"
  _bridge_cabin="${_bridge_cabin##*-}"
  [ "${#_bridge_cabin}" -gt 4 ] && _bridge_cabin="${_bridge_cabin: -4}"
  exec "${ENV_COMMAND[@]}" AIB_BRIDGE_CABIN="$_bridge_cabin" "$PY3" "$HERE/sand_bridge.py" "${SAND_ARGS[@]}"
fi

# provider CLI 用 type -P 解析真執行檔:codex 在本機是 alias(ez2-codex),
# alias 在非互動 shell 不生效,直接喊名字會 127(入冊教訓:argv0 來自變數)。
BIN="$(type -P "$PROVIDER" 2>/dev/null || true)"
if [ -z "$BIN" ] && [ "$PROVIDER" = "codex" ]; then
  BIN="$(type -P ez2-codex 2>/dev/null || true)"   # 本機 alias 的真身
fi
[ -n "$BIN" ] && [ -x "$BIN" ] || {
  # 10-14 公開版新用戶走查:講人話(以前寫內部代號 ED.4 / 訂閱 OAuth CLI);說法跟 aib 開席前檢查同一句。
  #   結束碼 6 = 沒裝:開席那一行看到 6 就不印 --resume(接回一個沒裝的工具沒有意義)。
  case "$PROVIDER" in
    claude) _pl_name="Claude Code"; _pl_login="claude" ;;
    codex) _pl_name="Codex"; _pl_login="codex login" ;;
    agy) _pl_name="Antigravity"; _pl_login="agy" ;;
    copilot) _pl_name="GitHub Copilot"; _pl_login="copilot + /login" ;;
    grok) _pl_name="Grok"; _pl_login="grok login" ;;
    *) _pl_name="$PROVIDER"; _pl_login="$PROVIDER" ;;
  esac
  "${EZ2_PY3:-python3}" "$HERE/i18n_lib.py" --param "provider=$PROVIDER" --param "name=$_pl_name" \
    --param "login=$_pl_login" aib.err.cli_missing >&2 2>/dev/null \
    || echo "provider-launch: $PROVIDER is not installed (no '$PROVIDER' command found)" >&2
  exit 6
}

# mode 對應各家 CLI 的旗標。只映射確知的;不確知的寧可 plain 啟動也不猜旗標。
ARGS=()
case "$PROVIDER:$MODE" in
  claude:dgrs) ARGS=(--dangerously-skip-permissions) ;;
  claude:safe) ARGS=() ;;
  # 09-29:codex 0.158.0 互動介面預設把對話與工具行程都交給常駐 app-server daemon(ppid=1),
  #   席位行程樹底下既沒有對話檔也沒有工具 shell ⇒ seat-detect 的 lsof 探針落空、席位內的
  #   aib-hdr/dm 用 ppid 鏈認不出自己。--no-daemon 讓 codex 回到自己的行程底下(實測:
  #   工具鏈 python→codex→node→tmux,對話檔由席位自己的 codex 持有)。
  #   ★ 只有這支 codex 的 --help 真有 --no-daemon 才帶(舊版如 Fish 的 0.144.1 沒有,硬帶=rc2 開席失敗);
  #     判法單源在 codex_daemon_flag.py(換帳號那條 codex_account_recover.py 也用同一支)。
  codex:dgrs)  ARGS=(--dangerously-bypass-approvals-and-sandbox $("${EZ2_PY3:-python3}" "$HERE/codex_daemon_flag.py" "$BIN")) ;;
  codex:safe)  ARGS=($("${EZ2_PY3:-python3}" "$HERE/codex_daemon_flag.py" "$BIN")) ;;
  # grok(CLI 1.0.13,2026-09-01 實測 --help):dgrs 的對應物是 --permission-mode
  # bypassPermissions。★ 在此之前 grok 落在下面那條 *) —— 席位窗名寫 dgrs 而
  # 實際起的是預設審批模式,「模式」這個欄位在 grok 席上是說謊的,而沒有任何
  # 讀數會紅:plain 啟動一樣起得來。這正是「名字宣稱在守、實際沒守」那一族。
  grok:dgrs)   ARGS=(--permission-mode bypassPermissions) ;;
  grok:safe)   ARGS=() ;;
  # agy(CLI 1.4.6,10-24 @abb89 右鍵實測 --help):dgrs 的對應物是 --dangerously-skip-permissions。
  #   先前落在 *) plain 啟動 ⇒ 窗名寫 dgrs、實際每個工具都要人按(跟上面 grok 同一族)。
  agy:dgrs)    ARGS=(--dangerously-skip-permissions) ;;
  agy:safe)    ARGS=() ;;
  # copilot(CLI 1.0.103,同日實測):dgrs 的對應物是 --allow-all(= --allow-all-tools --allow-all-paths --allow-all-urls,
  #   同 --yolo)。先前 plain 啟動 ⇒ 底部寫「Manual Approval」。
  copilot:dgrs) ARGS=(--allow-all) ;;
  *)           ARGS=() ;;   # copilot:safe 等:plain 啟動
esac

# The provider process always sees an explicit requested model.  This is only
# launch intent; runtime/effective truth is recorded separately by the
# session-model observation adapter and may be an owner-approved alias.
ARGS+=(--model "$REQUESTED_MODEL")
[ -n "$REQUESTED_EFFORT" ] && ARGS+=(--effort "$REQUESTED_EFFORT")
# 套組關、而且沒有手指定型號：拿掉 --model。dgrs 的審批旗標留下，那是開席模式。
if [ "$_codex_pack" = "0" ] && [ "${AIB_CODEX_MODEL_EXPLICIT:-0}" != "1" ]; then
  _kept=()
  _drop_model=0
  for _a in "${ARGS[@]}"; do
    if [ "$_drop_model" = "1" ]; then
      _drop_model=0
      continue
    fi
    if [ "$_a" = "--model" ]; then
      _drop_model=1
      continue
    fi
    _kept+=("$_a")
  done
  ARGS=()
  if [ "${#_kept[@]}" -gt 0 ]; then
    for _a in "${_kept[@]}"; do
      ARGS+=("$_a")
    done
  fi
fi

# ★ 身份綁定(ED.x,2026-08-17 起;2026-09-01 擴 grok):**能被指派新 session id
#   的 CLI** 才注入。aib 開席時產 uuid 並經第 5 參傳入,CLI 採用它 → 席位檔那個
#   session_uuid 是**權威值不是事後猜**,seat_status 立刻判為已編制、不再 ❓,
#   且與 cwd / transcript 出生時間無關。
#   claude:`--session-id <uuid>`(實測 2.1.233)。
#   grok  :`--session-id <uuid>`(實測 grok 1.0.13,2026-09-01)——★ 本行以前寫的是
#          「claude 是唯一能被指派的 CLI」,那句在 grok 上是**錯的**,而錯法不會變紅:
#          不注入時 grok 席一樣開得起來,只是永遠停在 probe:env-scan 事後猜。
#          量法(不是讀 --help 就算):指派 uuid 跑一次 `grok --session-id U -p ...`,
#          落檔出現 ~/.grok/sessions/<url-encoded-cwd>/U/ ⇒ 該 id 被真的採用。
#   codex/agy/copilot 的 CLI 不接受指派新 id,故不在此注入——它們走各自的
#   確定性 artifact 偵測(rollout / presence-lock),硬塞未被採用的 uuid 會是
#   seat_status 明令的「假資料比缺資料毒」。
# ★★ 09-24(a seat)分出新席:aib 只在**這一段命令前**放兩個值(不進 AIB_SEAT_ENV),這裡讀完立刻 unset
#   ——留著的話它會流進 CLI 的環境,CLI 裡的殼再開席就會繼承,同一個洞換個入口。
#   設計 docs/DESIGN-seat-fork-unique-0924.md;實測 claude 2.1.281:`--resume 舊 --fork-session --session-id 新`
#   新檔用指定的新 id、帶舊內容、舊檔不動。只實作 claude(grok 的對話檔位置沒量過)。
_fork_from="${AIB_FORK_FROM_SESSION_UUID:-}"; _fork_preamble="${AIB_FORK_PREAMBLE_FILE:-}"
unset AIB_FORK_FROM_SESSION_UUID AIB_FORK_PREAMBLE_FILE
_fork_respawn=0
# 新 id 的對話檔已經在 ⇒ 這是 pane 重生(tmux 重跑同一條啟動命令),分出早就發生過:接回新 id 自己,
#   不得再分一次(再帶 --session-id 新 會撞上已存在的對話)。
aib_claude_session_exists() {
  local hit
  for hit in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/projects/*/"$1".jsonl; do
    [ -f "$hit" ] && return 0
  done
  return 1
}
# 10-12 a seat(the maintainer「如何確保我的要求是程式化必要流程」→「開始」):開 Claude 席前確認 aib now 狀態檢查
#   (src/policy/hooks/aib-now-gate.py 的 Stop / SessionStart hook)有裝在 ~/.claude/settings.json;
#   已裝好不動、沒裝才裝(先備份);失敗不擋開席;AIB_NOW_GATE=0 關。要不要真的擋人另由試跑名單 gate-seats.txt 決定。
#   只在有終端機時([ -t 0 ]):真的開席一定有;測試跑本檔一律 stdin=DEVNULL(subprocess-stdin 棘輪),
#   不然沒換 HOME 的舊測試會改到真的 ~/.claude/settings.json(P5 ClaudeBot 自動裝 hook 同一個理由不放橋裡)。
if [ "$PROVIDER" = "claude" ] && [ -t 0 ] && [ -f "$HERE/../policy/hooks/aib-now-gate.py" ]; then
  "${EZ2_PY3:-python3}" "$HERE/../policy/hooks/aib-now-gate.py" ensure </dev/null || true
fi
# 10-13 a seat(the maintainer「10/30/50/70 自動精煉人格與專案第二大腦」,a seat 13:56 准照 aib-now-gate 做法):
#   同一個條件下確認依用量自動精煉的 UserPromptSubmit hook 有裝;AIB_AUTO_DISTILL=0 關。
if [ "$PROVIDER" = "claude" ] && [ -t 0 ] && [ -f "$HERE/../identity/auto_distill_hook.py" ]; then
  "${EZ2_PY3:-python3}" "$HERE/../identity/auto_distill_hook.py" ensure </dev/null || true
fi
# 非 Claude 席:一輪結束 hook(各家格式不同;a seat / a seat 10-13 實測)。Codex 新 hook 要 the maintainer 在 /hooks 信任一次。
case "$PROVIDER" in
  grok|copilot|agy|codex)
    if [ -t 0 ] && [ -f "$HERE/../identity/auto_distill_hook.py" ]; then
      "${EZ2_PY3:-python3}" "$HERE/../identity/auto_distill_hook.py" "ensure-$PROVIDER" </dev/null || true
    fi ;;
esac
# 10-13 a seat(the maintainer「所有的ai只要選單有選項,都應該自動判斷選擇」「五家都要做第一層」):選單一出現就觸發 AI 選
#   (Claude / Grok 的 Notification permission_prompt;Grok 也讀 ~/.claude/settings.json,重複觸發 2 秒內只算一次)。
#   同一個條件:只在有終端機時裝;AIB_MENU_PICK=0 關。
if { [ "$PROVIDER" = "claude" ] || [ "$PROVIDER" = "grok" ]; } && [ -t 0 ] && [ -f "$HERE/menu_pick.py" ]; then
  "${EZ2_PY3:-python3}" "$HERE/menu_pick.py" ensure </dev/null || true
fi
# Codex:PermissionRequest(核准提示出現前就讓 AI 決定);新 hook 要 the maintainer 在 Codex /hooks 信任一次才會跑。
if { [ "$PROVIDER" = "agy" ] || [ "$PROVIDER" = "copilot" ] || [ "$PROVIDER" = "codex" ]; } && [ -t 0 ] && [ -f "$HERE/menu_pick.py" ]; then
  "${EZ2_PY3:-python3}" "$HERE/menu_pick.py" "ensure-$PROVIDER" </dev/null || true
fi
# 10-14 a seat(the maintainer 02:3x「Codex /hooks 信任要自動」):上面兩段裝完,AIB 自己的掛點還沒信任 ⇒ Mac 彈窗
#   [同意][不要],30 秒沒按 = 同意;同意才寫 config.toml 的 trusted_hash(先備份)。別人的掛點不碰;AIB_CODEX_HOOK_TRUST=0 關。
# 10-24 @abb89(Andy「卡選項的新開席都要能自動選成功」):agy、grok(1.0.67 起)第一次進一個資料夾會先問信不信任,
#   開席單打不進那個畫面 ⇒ 席卡住。人在這個資料夾開席 = 要它在這裡做事 ⇒ 先寫進該家的信任清單
#   (同 Codex 的 ensure_project_trusted);讀不懂設定檔 / 人按過不信任就不動。AIB_FOLDER_TRUST=0 關。
if { [ "$PROVIDER" = "agy" ] || [ "$PROVIDER" = "grok" ]; } && [ -f "$HERE/folder_trust.py" ]; then
  "${EZ2_PY3:-python3}" "$HERE/folder_trust.py" ensure "$PROVIDER" "$PROJECT_DIR" </dev/null >/dev/null || true
fi
if [ "$PROVIDER" = "codex" ] && [ -t 0 ] && [ -f "$HERE/codex_hook_trust.py" ]; then
  "${EZ2_PY3:-python3}" "$HERE/codex_hook_trust.py" ensure </dev/null || true
fi
case "$PROVIDER" in
  claude|grok)
    if [ -n "$SESSION_UUID" ]; then
      # 09-14(20:07 事故復原):AIB_RESUME_UUID=1 ⇒ 這個 uuid 是**既有對話**,用 --resume 接回,
      #   不是 --session-id 新開;席位檔登記的仍是同一個 uuid(五碼不變,dm 位址不變)。
      #   09-24 起這條只給「原地復活」(舊席已死);「接回舊對話」改走下面的分出新席。
      # 10-02(a seat,f643 整艙被 kill-server 後復活):grok 1.0.46 也有 `--resume <id>`;
      #   原本只給 claude,grok 落到下面 --session-id 新開 ⇒ 撞「Session ID … is already in use」退出。
      if [ "${AIB_RESUME_UUID:-0}" = "1" ] && { [ "$PROVIDER" = "claude" ] || [ "$PROVIDER" = "grok" ]; }; then
        ARGS+=(--resume "$SESSION_UUID")
      elif [ "$PROVIDER" = "claude" ] && [ -n "$_fork_from" ] && [ "$_fork_from" != "$SESSION_UUID" ]; then
        if aib_claude_session_exists "$SESSION_UUID"; then
          _fork_respawn=1
          ARGS+=(--resume "$SESSION_UUID")
        else
          ARGS+=(--resume "$_fork_from" --fork-session --session-id "$SESSION_UUID")
        fi
      else
        ARGS+=(--session-id "$SESSION_UUID")
      fi
    fi
    ;;
esac
# 10-02(a seat):copilot 不接受指派新 id(見上),但原地復活要接回**既有**對話時有 `--resume <id>`。
#   沒這段時 copilot 原地復活會開一個全新的空對話,席位檔卻登記舊 id(名實不符,舊對話沒接上)。
#   copilot 不在 aib 的 AIB_SESSION_ID_PROVIDERS ⇒ 拿不到 SESSION_UUID/AIB_RESUME_UUID;照 codex 那段(下方)
#   直接讀 AIB_RESUME_SESSION_UUID(aib 的原地復活/舊名接回才會設),先驗形狀。
if [ "$PROVIDER" = "copilot" ] && [ -n "${AIB_RESUME_SESSION_UUID:-}" ]; then
  [[ "$AIB_RESUME_SESSION_UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
    echo "provider-launch: invalid AIB_RESUME_SESSION_UUID: $AIB_RESUME_SESSION_UUID" >&2; exit 2; }
  ARGS+=(--resume "$AIB_RESUME_SESSION_UUID")
fi

# Codex/AGY create their session artifacts only after the first prompt. Regular
# onboarding delivery requires that session ID, so an empty TUI leaves both
# sides waiting forever. Start one no-tool turn before the normal delivery.
# Keep AIB_NO_ONBOARD=1 as the explicit opt-out for plain/manual sessions.
# 套組關掉時也不塞「只回覆 AIB-READY」；codex 要等這個人自己打第一句。
if [ "${AIB_NO_ONBOARD:-0}" != "1" ] && [ "${_codex_pack:-1}" != "0" ]; then
  INITIAL_PROMPT="這是 AIB 新席初始化。請只回覆 AIB-READY，不要呼叫工具、讀取檔案或修改資料。正式開席單會另行送達，請等待。"
  # O5:能自取的家先落開席單當啟動參數,成功就不再往輸入框 dm,
  # 也不跟 AIB-READY 合句。
  # grok 2026-09-10 實測 `grok --help`:`grok [OPTIONS] [PROMPT] [COMMAND]`
  # 且 [PROMPT]=Initial prompt for the interactive session,e.g. grok "fix the bug"。
  # 與 claude/codex 相同:最後一參=開席單正文。
  # 窗號:測試縫 AIB_ONBOARD_WINDOW;活路徑問 pane 自己。
  ONB_PATH=""
  # ★★ 09-14(the maintainer 19:1x 實測:「我還沒選完、他就自動把 aib 的內容丟進對話框了」):這一段 O5 自取
  #   =把整份開席單當 CLI 啟動參數塞進去,**跑在選單之前**,所以三選一根本沒有意義。
  #   開席單三選一(the maintainer 18:0x)之後:只有走舊路(AIB_ONBOARD_AUTO=1)才在啟動時自取;
  #   預設(選單流程)啟動時**不帶開席單**,選了(或 30 秒逾時預設全部)才由 seat_onboard 投遞。
  #   codex/agy 仍要一個不帶工具的首回合才會生出 session 產物,所以下面的 INITIAL_PROMPT 保留。
  if [ "${AIB_ONBOARD_AUTO:-0}" = "1" ]; then
    case "$PROVIDER" in
      claude|codex|agy|grok)
        ONB_WIN="${AIB_ONBOARD_WINDOW:-}"
        if [ -z "$ONB_WIN" ] && [ -n "${TMUX_PANE:-}" ]; then
          ONB_WIN="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
        fi
        if [ -n "$ONB_WIN" ]; then
          PY3="${EZ2_PY3:-python3}"
          ONB_PATH="$("$PY3" "$HERE/seat_onboard.py" --prepare-self-fetch \
            --window "$ONB_WIN" --role "$ROLE" --provider "$PROVIDER" 2>/dev/null || true)"
        fi
        ;;
    esac
  fi
  if [ -n "$ONB_PATH" ] && [ -f "$ONB_PATH" ]; then
    case "$PROVIDER" in
      claude|codex|grok) ARGS+=("$(cat "$ONB_PATH")") ;;
      agy) ARGS+=(--prompt-interactive "$(cat "$ONB_PATH")") ;;
    esac
  else
    case "$PROVIDER" in
      codex) ARGS+=("$INITIAL_PROMPT") ;;
      agy) ARGS+=(--prompt-interactive "$INITIAL_PROMPT") ;;
    esac
  fi
fi
# ★★ 09-24(a seat)分出新席的第一句:你是 @新碼、對話裡的 @舊碼 是前身。只換碼不告知,新席照樣自稱舊碼
#   (09-24 @27 帶著 @4 的記憶自認 a seat,派工信你我寫反)。重生時不再送(第一次已經講過)。
#   開席單若已是最後一參(AIB_ONBOARD_AUTO=1 那條舊路),合成同一句,不送兩個起始參數。
if [ "$PROVIDER" = "claude" ] && [ "$_fork_respawn" = "0" ] && [ -n "$_fork_preamble" ] && [ -f "$_fork_preamble" ]; then
  if [ -n "${ONB_PATH:-}" ] && [ -f "${ONB_PATH:-}" ]; then
    ARGS[$((${#ARGS[@]}-1))]="$(cat "$_fork_preamble")"$'\n\n'"${ARGS[$((${#ARGS[@]}-1))]}"
  else
    ARGS+=("$(cat "$_fork_preamble")")
  fi
fi

# --- 用量准入(2026-09-08,the maintainer 直裁順序 4)-----------------------------------
# ★ 這裡是**那個數字的第一個消費者**。在此之前帳號額度只被畫出來,沒有接到任何
#   決策上 —— 當天 codex 週用量 100%,而系統照樣往那幾席派工,派出去的全是白工。
# ★★ 只在**量到的證據**上擋人:status=ok、值合法、且觀測沒過期。
#   量不到 / 過期 / 沒紀錄 ⇒ 放行。理由不是寬容,是**額度用完的 CLI 常常根本不回答**,
#   於是「額度滿」與「探不到」給同一個觀測值;讓探不到也擋人會長出
#   「探針壞掉 → 判成額度滿 → 擋住開席 → 沒人開得了席去修探針」這個自我維持的死結。
# ★ rc=7 是這條路自己的碼,不與 2(用法/參數)、6(CLI 不在)混用:
#   一個 rc 兩種意思的話,呼叫端就得去猜。
# ★ the maintainer 2026-09-22:新開席預設開真 CLI(AIB_IGNORE_USAGE 預設 1)。
#   額度滿不再默默掉進保活席;真要保活才設 AIB_FORCE_QUOTA_HOLD=1。
#   逃生門仍可顯式 AIB_IGNORE_USAGE=0 搭配 FORCE 走舊閘。
if [ "${AIB_FORCE_QUOTA_HOLD:-0}" = "1" ]; then
  AIB_IGNORE_USAGE=0
else
  : "${AIB_IGNORE_USAGE:=1}"
fi
if [ "${AIB_IGNORE_USAGE}" != "1" ]; then
  PY3="${EZ2_PY3:-python3}"
  USAGE_VERDICT="$("$PY3" -c 'import sys, time
sys.path.insert(0, sys.argv[1])
try:
    import usage_probe
except Exception:
    print("unknown\t匯入不了用量模組 —— 不擋"); raise SystemExit(0)
try:
    v, why = usage_probe.usage_admission(sys.argv[2], time.time())
except Exception as exc:
    print("unknown\t用量判定自己炸了(%s)—— 不擋" % type(exc).__name__); raise SystemExit(0)
print("%s\t%s" % (v, why))' "$HERE" "$PROVIDER" 2>/dev/null || printf 'unknown\t判不出來 —— 不擋')"
  USAGE_STATE="${USAGE_VERDICT%%	*}"
  USAGE_WHY="${USAGE_VERDICT#*	}"
  if [ "$USAGE_STATE" = "exhausted" ]; then
    echo "provider-launch: 這一家的額度已經用完,開席會是白工 —— $USAGE_WHY" >&2
    echo "  要照開真 CLI:AIB_IGNORE_USAGE=1 <原指令>(理由會留在這一行訊息裡)" >&2
    # the maintainer 2026-09-21:沒額度也要能對話。exit 7 會讓 tmux pane 變死窗,
    # 輸入區整塊 Pane is dead。有 TTY(或 AIB_QUOTA_HOLD=1)時改 exec 保活 REPL;
    # 無 TTY(單測/管線)仍 exit 7,行為與舊測試契約一致。
    if [ "${AIB_QUOTA_HOLD:-auto}" != "0" ] && { [ -t 1 ] || [ "${AIB_QUOTA_HOLD:-}" = "1" ]; }; then
      echo "provider-launch: 改開額度席保活(可對話/排隊,不起真 CLI)" >&2
      exec "$PY3" "$HERE/quota_hold_repl.py" --provider "$PROVIDER" --why "$USAGE_WHY"
    fi
    exit 7
  fi
fi

# --- 綁定時量一次(2026-09-08,the maintainer 直裁順序 3;SPEC USAGE-BIND-PROBE-v1 §1/§2/§5)---
# ★ 探測點選在這裡而不是面板重繪:重繪一天數百次(缺陷三十九就是死在那裡),
#   綁定當天是 8 個艙 / 24 席 —— **離散、稀少,而且正是「這個引擎要開始被用了」那一刻**。
# ★ 一天一家只探一次:每日上限 24 與當天席數 24 同量級,每次綁定都探會把上限吃光,
#   而**上限吃光的失效形狀是「探不到」**,跟探針壞掉同形。其餘留給人點 [重驗]。
# ★★ **它不擋人**:`bind_probe` 只做「今天探過沒有」的判斷,再交給 `_spawn_probe`
#   起一個**分離的** worker 就回來 —— 面板重繪走的是同一條路,所以它必須快。
#   回值一律忽略,綁定的成敗不因探針而改變 —— 附帶動作不是前置條件。
#   ★ 2026-09-08 這一行原本是裸 `&`,被 `test-shell-hygiene` 擋下:
#     **裸 & 起的程序沒有人接得到它的死訊**(規則:同檔內要取 `$!` 並有 kill/wait)。
#     不阻塞這件事改由 `_spawn_probe` 保證,不靠 `&`。
#   agy 現在卡在信任對話框且在等登入,如果這裡等它,開席就會掛在那個對話框上。
# ★ 留痕在 usage 目錄的 probe-count 與紀錄檔本身;自動行為不留痕就等於不可觀測。
if [ "${AIB_NO_BIND_PROBE:-0}" != "1" ]; then
  PY3="${EZ2_PY3:-python3}"
  "$PY3" -c 'import sys
sys.path.insert(0, sys.argv[1])
try:
    import usage_probe
    usage_probe.bind_probe(sys.argv[2])
except Exception:
    pass' "$HERE" "$PROVIDER" >/dev/null 2>&1 || true
fi

cd "$PROJECT_DIR" || exit 2
if [ "$PROVIDER" = "codex" ]; then
  "${EZ2_PY3:-python3}" -c 'import sys
sys.path.insert(0, sys.argv[1])
import usage_failover as uf
uf.ensure_project_trusted(sys.argv[2], sys.argv[3])
' "$HERE" "${CODEX_HOME:-$HOME/.codex}" "$PROJECT_DIR" >/dev/null 2>&1 || true
fi
# ★bash 3.2 + set -u:空陣列展開會炸 unbound variable(4.4 才修),
# 測試 safe 模式當場抓到——用條件展開慣用式,兩個陣列都套。
# 09-14(20:07 事故復原):codex 接回既有對話 = `codex resume [OPTIONS] <SESSION_ID>`。
#   codex 不收指派新 id,但收「接回哪一個」;開席單/AIB-READY 首回合都不帶(那是接回,不是新開)。
if [ "$PROVIDER" = "codex" ] && [ -n "${AIB_RESUME_SESSION_UUID:-}" ]; then
  [[ "$AIB_RESUME_SESSION_UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
    echo "provider-launch: invalid AIB_RESUME_SESSION_UUID: $AIB_RESUME_SESSION_UUID" >&2; exit 2; }
  _resume_args=()
  for _a in ${ARGS[@]+"${ARGS[@]}"}; do
    [ "$_a" = "${INITIAL_PROMPT:-}" ] && continue
    _resume_args+=("$_a")
  done
  ARGS=(resume ${_resume_args[@]+"${_resume_args[@]}"} "$AIB_RESUME_SESSION_UUID")
fi
# 額度用完才換號。開關沒開時這支立刻結束，不另開席、不改共用登入。
# 不放背景 &：python 自己分出看守，這層殼照舊 exec。
if [ "$PROVIDER" = "codex" ] && [ "${AIB_CODEX_AUTO_ROTATE_ARM:-1}" = "1" ] && [ "${_codex_pack:-1}" = "1" ]; then
  PY3="${EZ2_PY3:-python3}"
  _rotate_window=""
  if [ -n "${TMUX_PANE:-}" ]; then
    _rotate_window=$("${AIB_TMUX_BIN:-tmux}" display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)
  fi
  # 額度到頂由這一席自己跳出選單，看守不要同時偷偷換號。
  AIB_CODEX_QUOTA_MENU=1 "$PY3" "$HERE/codex_rotate_live.py" arm \
    --parent-pid "$$" \
    --resume "${AIB_RESUME_SESSION_UUID:-}" \
    --codex-home "${CODEX_HOME:-$HOME/.codex}" \
    --window "${_rotate_window}" || true
fi
# 換帳後不要接上一串別人的對話，否則一開就又是同一個 401。
_fresh_login_args() {
  local _kept=()
  if [ "$MODE" = "dgrs" ]; then
    _kept+=(--dangerously-bypass-approvals-and-sandbox)
  fi
  [ -n "${REQUESTED_EFFORT:-}" ] && _kept+=(--effort "$REQUESTED_EFFORT")
  [ -n "${REQUESTED_MODEL:-}" ] && _kept+=(--model "$REQUESTED_MODEL")
  ARGS=()
  if [ "${#_kept[@]}" -gt 0 ]; then
    local _a
    for _a in "${_kept[@]}"; do
      ARGS+=("$_a")
    done
  fi
  unset AIB_RESUME_SESSION_UUID
}

# ★ the maintainer 09-28「自動偵測某些場景、字詞時會跳到換帳號頁,目前太敏感」:
#   畫面上半部是對話內容(人或 AI 在聊登入、貼 log、行號 401 都會出現這些字),
#   Codex 自己的錯誤印在最底下幾行 ⇒ 登入失效與 401 只看最後 AIB_CODEX_DETECT_TAIL 行(預設 12,空行不算)。
#   401 還要像錯誤:旁邊有 unauthorized,或寫成 status/HTTP/code 401(判準與 codex_account_recover.py 同一條)。
_pane_tail() {
  printf '%s\n' "$1" | grep -v '^[[:space:]]*$' | tail -n "${AIB_CODEX_DETECT_TAIL:-12}"
}
_http_401() {
  _pane_tail "$1" | grep -Eiq 'unauthorized[^0-9]{0,40}(^|[^0-9])401([^0-9]|$)|(^|[^0-9])401([^0-9]|$)[^0-9]{0,40}unauthorized|(status|http|code)[ :=]*401([^0-9]|$)'
}
# 選了「回到本對話」之後的一段時間(AIB_CODEX_RECOVER_SNOOZE_S,預設 600 秒)不再自動跳換帳號頁:
# 回去時畫面上還留著那句錯誤,不這樣就會立刻又跳一次。
_recover_snooze_file() {
  printf '%s/codex-recover-snooze-%s' "${EZ2_COCKPIT_STATE_DIR:-${TMPDIR:-/tmp}}" "${TMUX_PANE#%}"
}
_recover_snoozed() {
  local _f _at _now
  _f="$(_recover_snooze_file)"
  [ -f "$_f" ] || return 1
  _at="$(cat "$_f" 2>/dev/null || echo 0)"
  _now="$(date +%s)"
  case "$_at" in ''|*[!0-9]*) return 1 ;; esac
  [ $((_now - _at)) -lt "${AIB_CODEX_RECOVER_SNOOZE_S:-600}" ]
}
_recover_snooze_now() {
  mkdir -p "$(dirname "$(_recover_snooze_file)")" 2>/dev/null || true
  date +%s > "$(_recover_snooze_file)" 2>/dev/null || true
}

# 401、用量到頂，或登入證明換不了。選成了就回 0，由外面再啟動。
_login_dead() {
  _pane_tail "$1" | grep -Eq 'Failed to refresh token|refresh token was rejected|access token could not be refreshed|Please sign in again|Not signed in'
}

# Codex **自己**印的額度用完那句,不是任何人打的字。
# 真檔量到的:375/375 筆都是 "You've hit your usage limit." + codex_error_info=usage_limit_exceeded。
# 舊判準是裸的 `usage limit|usage_limit|額度用完`,而這個監看抓的是 capture-pane ——
# 裡面看得到**輸入框正在打的字** ⇒ the maintainer 打「額度用完」就會讓自己的 Codex 被 kill(v0.70.0 #11)。
# ★ 09-30:先把換行與連續空白收成一格再比。-J 只接得回 **tmux** 折的行;Codex 的畫面是它自己排版的,
#   窄欄時那句會被它自己斷成兩行(真的換行,-J 接不回)⇒ 逐行 grep 看不到 ⇒ 真到頂判成沒事(靜的那一半)。
#   上游(96b4b65b / 2ba2a7ae)殼那半的 _quota_hit 做的就是這一步;揀進 main 時殼那半改走本函式,這一步沒跟過來。
#   字面判準本身不變(test-codex-quota-own-error 的刀錨仍是下面那一行 grep)。
_codex_own_quota_pane() {
  printf '%s' "$1" | tr '\n' ' ' | tr -s ' ' | grep -Eq "You'?ve hit your usage limit|usage_limit_exceeded|usage_limit_reached"
}

# pane 上的字看不出是誰說的(真檔裡就有人把含那句英文的終端機 log 貼進輸入框)⇒
# 命中之後回對話檔再問一次「那是 Codex 的錯誤項嗎」。問不到就不算(寧可不換號)。
_codex_quota_confirmed() {
  local _qpid="${1:-}"
  local _rp="${AIB_CODEX_ROTATE_PY:-$HERE/codex_rotate_live.py}"
  if [ -n "$_qpid" ]; then
    "${EZ2_PY3:-python3}" "$_rp" quota-seen --pid "$_qpid" >/dev/null 2>&1 </dev/null
    return $?
  fi
  # Codex 已經結束 ⇒ 沒有 pid 可以問,用這一席的 codex home + 續接編號去找對話檔。
  # 拿不到續接編號就**不做**這道確認(回 0 放行):否則等於把「真的額度用完」的救援整條關掉,
  # 而那時我們手上仍有 narrow 過的 Codex 原句(擋掉的是使用者打的字,沒擋掉貼 log)。
  if [ -z "${SESSION_UUID:-}" ]; then
    return 0
  fi
  "${EZ2_PY3:-python3}" "$_rp" quota-seen \
    --codex-home "${CODEX_HOME:-$HOME/.codex}" --resume "$SESSION_UUID" >/dev/null 2>&1 </dev/null
}

# 「要不要接續/救援」:額度那一支一律 pane 命中 **且** 對話檔確認;401 與登入死掉照舊。
_continue_needed() {
  _recover_snoozed && return 1          # 剛選過「回到本對話」:這段時間不再自動跳
  if _codex_own_quota_pane "$1"; then
    _codex_quota_confirmed "" && return 0
  fi
  _http_401 "$1" && return 0
  _login_dead "$1"
}

_offer_account_recover() {
  local _seen="$1"
  _continue_needed "$_seen" || return 1
  [ -n "${TMUX_PANE:-}" ] || return 1
  if _codex_own_quota_pane "$_seen"; then
    export AIB_CODEX_RECOVER_REASON=quota
  elif _login_dead "$_seen"; then
    export AIB_CODEX_RECOVER_REASON=refresh
  else
    export AIB_CODEX_RECOVER_REASON=401
  fi
  "${AIB_TMUX_BIN:-tmux}" set-option -p -t "$TMUX_PANE" mouse off >/dev/null 2>&1 || true
  local _home_file _new_home _recover_py _recover_rc
  _home_file="$(mktemp)"
  _recover_py="${AIB_CODEX_RECOVER_PY:-$HERE/codex_account_recover.py}"
  _recover_rc=1
  _pick_rc=0
  "${EZ2_PY3:-python3}" "$_recover_py" --result "$_home_file" || _pick_rc=$?
  if [ "$_pick_rc" -eq 0 ]; then
    _new_home="$(cat "$_home_file" 2>/dev/null || true)"
    if [ -n "$_new_home" ]; then
      export CODEX_HOME="$_new_home"
      _fresh_login_args
      _recover_rc=0
    fi
  elif [ "$_pick_rc" -eq 3 ]; then
    _recover_rc=3                       # 回到本對話:不換帳,由外面接回原對話
  fi
  rm -f "$_home_file"
  "${AIB_TMUX_BIN:-tmux}" set-option -p -t "$TMUX_PANE" mouse on >/dev/null 2>&1 || true
  return "$_recover_rc"
}

_run_codex_capture() {
  _codex_rc=0
  _codex_seen=""
  if [ -n "${AIB_CODEX_LUNA_ERRFILE:-}" ]; then
    ${ENV_COMMAND[@]+"${ENV_COMMAND[@]}"} "$BIN" ${ARGS[@]+"${ARGS[@]}"} >"$AIB_CODEX_LUNA_ERRFILE" 2>&1 || _codex_rc=$?
    _codex_seen="$(cat "$AIB_CODEX_LUNA_ERRFILE" 2>/dev/null || true)"
    return 0
  fi
  if [ -z "${TMUX_PANE:-}" ]; then
    ${ENV_COMMAND[@]+"${ENV_COMMAND[@]}"} "$BIN" ${ARGS[@]+"${ARGS[@]}"} || _codex_rc=$?
    return 0
  fi
  # 月上限通常不會把 Codex 關掉，只把那句留在畫面上。旁邊看著，一出現就停下並交給選單。
  # Codex 必須留在前景。腳本沒有作業控制時，丟到背景會被拿走終端機。
  local _pidfile _mon _parent
  _pidfile="$(mktemp)"
  _trust_sent="$(mktemp)"
  _parent=$$
  (
    while true; do
      _pid=""
      if [ -s "$_pidfile" ]; then
        _pid="$(cat "$_pidfile" 2>/dev/null || true)"
      fi
      if [ -z "$_pid" ]; then
        _pid="$(pgrep -P "$_parent" -f "$BIN" 2>/dev/null | head -1 || true)"
      fi
      if [ -n "$_pid" ] && ! kill -0 "$_pid" 2>/dev/null; then
        sleep 1
        continue
      fi
      # ★ `-J` 一定要帶(整合席 a seat + qa a seat 2026-09-26 18:0x 兩邊獨立指出):
      #   man tmux 逐字「-J preserves trailing spaces and joins any wrapped lines」。
      #   畫面是格子,超過欄寬就在**那一格**斷,斷點可以落在字中間 ——
      #   字中折要把換行**去掉**、字間折要把換行**換成空白**,兩者互斥,
      #   所以在字串層補不出一個兩邊都對的規則。帶 -J 拿到的是沒折過的原文。
      _pane="$("${AIB_TMUX_BIN:-tmux}" capture-pane -p -J -t "$TMUX_PANE" 2>/dev/null || true)"
      if printf '%s' "$_pane" | grep -q 'Trust and continue' \
        && printf '%s' "$_pane" | grep -q 'Folder access' \
        && [ ! -s "$_trust_sent" ]; then
        printf '1\n' > "$_trust_sent"
        "${AIB_TMUX_BIN:-tmux}" send-keys -t "$TMUX_PANE" 1 Enter
      fi
      if [ -n "$_pid" ] && ! _recover_snoozed \
        && { { _codex_own_quota_pane "$_pane" && _codex_quota_confirmed "$_pid"; } || _login_dead "$_pane"; }; then
        kill "$_pid" 2>/dev/null || true
        exit 0
      fi
      sleep 1
    done
  ) </dev/null &
  _mon=$!
  if [ -t 0 ]; then
    ${ENV_COMMAND[@]+"${ENV_COMMAND[@]}"} "$BIN" ${ARGS[@]+"${ARGS[@]}"} || _codex_rc=$?
  else
    ${ENV_COMMAND[@]+"${ENV_COMMAND[@]}"} "$BIN" ${ARGS[@]+"${ARGS[@]}"} &
    printf '%s' "$!" > "$_pidfile"
    wait "$(cat "$_pidfile")" || _codex_rc=$?
  fi
  kill "$_mon" 2>/dev/null || true
  wait "$_mon" 2>/dev/null || true
  rm -f "$_pidfile" "$_trust_sent"
  # -J 同上:兩處都要帶。只帶一處的失效形狀是「看守器對了、收尾那次判斷還是錯的」。
  _codex_seen="$("${AIB_TMUX_BIN:-tmux}" capture-pane -p -J -t "$TMUX_PANE" 2>/dev/null || true)"
  return 0
}

# 套組開著時，Codex 因讀不到帳號而結束就再進選單。選單按離開才真的結束。
# 自動挑的最新 Luna 若被登入拒絕，只改試清單裡的上一版。額度用完不換模型。
if [ "$PROVIDER" = "codex" ] && [ "${_codex_pack:-1}" = "1" ]; then
  _luna_switched=0
  while true; do
    _run_codex_capture
    # 月上限這句出現後,Codex 常常以 0 結束。0 不是「自己關掉」,一樣要進續跑。
    if [ "$_codex_rc" -eq 0 ] && ! _continue_needed "$_codex_seen"; then
      exit 0
    fi
    _older=""
    if [ "${AIB_CODEX_LUNA_AUTO:-0}" = "1" ] && [ "$_luna_switched" != "1" ]; then
      _older="$("${EZ2_PY3:-python3}" -c 'import sys
sys.path.insert(0, sys.argv[1])
import codex_luna_model
seen=sys.stdin.read()
if not codex_luna_model.model_rejected(seen):
    raise SystemExit(0)
print(codex_luna_model.previous_luna_slug(sys.argv[2]))' "$HERE" "$REQUESTED_MODEL" <<<"$_codex_seen" 2>/dev/null || true)"
    fi
    if [ -n "$_older" ]; then
      echo "provider-launch: ${REQUESTED_MODEL} 開不起來，改用舊一版 ${_older}" >&2
      _swapped=()
      _prev=""
      for _a in ${ARGS[@]+"${ARGS[@]}"}; do
        if [ "$_prev" = "--model" ]; then
          _swapped+=("$_older")
        else
          _swapped+=("$_a")
        fi
        _prev="$_a"
      done
      ARGS=("${_swapped[@]}")
      REQUESTED_MODEL="$_older"
      _luna_switched=1
      continue
    fi
    _handoff_home="${CODEX_HOME:-$HOME/.codex}"
    _handoff_uuid="${AIB_RESUME_SESSION_UUID:-}"
    _offer_rc=0
    _offer_account_recover "$_codex_seen" || _offer_rc=$?
    if [ "$_offer_rc" -eq 3 ]; then
      # 回到本對話【不輪替登入其他帳號】:同一個帳、接回同一段對話;這段時間不再自動跳換帳號頁。
      _recover_snooze_now
      _back_args=()
      _skip_resume=1
      for _a in ${ARGS[@]+"${ARGS[@]}"}; do
        if [ "$_skip_resume" = "1" ] && [ "$_a" = "resume" ]; then _skip_resume=0; continue; fi
        _skip_resume=0
        [ "$_a" = "${INITIAL_PROMPT:-}" ] && [ -n "${INITIAL_PROMPT:-}" ] && continue
        [ "$_a" = "${AIB_RESUME_SESSION_UUID:-}" ] && [ -n "${AIB_RESUME_SESSION_UUID:-}" ] && continue
        [ "$_a" = "--last" ] && continue
        _back_args+=("$_a")
      done
      _back_id="${AIB_RESUME_SESSION_UUID:-${SESSION_UUID:-}}"
      if [[ "$_back_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        ARGS=(resume ${_back_args[@]+"${_back_args[@]}"} "$_back_id")
      else
        ARGS=(resume ${_back_args[@]+"${_back_args[@]}"} --last)
      fi
      echo "provider-launch: 回到本對話,不換帳(${AIB_CODEX_RECOVER_SNOOZE_S:-600} 秒內不再自動跳換帳號頁)" >&2
      continue
    fi
    if [ "$_offer_rc" -ne 0 ]; then
      # 用量到頂時 Codex 以 0 結束。選單沒接上就不能再報成正常結束。
      if [ "$_codex_rc" -eq 0 ]; then
        exit 2
      fi
      exit "$_codex_rc"
    fi
    # 不 resume 舊編號。把舊對話、ez2 摘錄、第二大腦抄好，當成新帳的第一句丟回去跑。
    _continue_prompt="$("${EZ2_PY3:-python3}" -c 'import sys
sys.path.insert(0, sys.argv[1])
import usage_failover as uf
home, aib = sys.argv[2], sys.argv[3]
uuid = sys.argv[4] if len(sys.argv) > 4 else ""
pack = uf.prepare_continue_pack(home, aib, uuid)
sys.stdout.write(pack.get("prompt") or "")
' "$HERE" "$_handoff_home" "${EZ2_AIB_HOME:-$HOME/.ez2-aib}" "$_handoff_uuid" 2>/dev/null || true)"
    if [ -n "$_continue_prompt" ]; then
      ARGS+=("$_continue_prompt")
    fi
  done
fi
_codex_rc=0
${ENV_COMMAND[@]+"${ENV_COMMAND[@]}"} "$BIN" ${ARGS[@]+"${ARGS[@]}"} || _codex_rc=$?
exit "$_codex_rc"
