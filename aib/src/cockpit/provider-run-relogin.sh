#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# After native CLI exits: if Codex OAuth auth was cleared (/logout),
# relaunch a fresh real CLI onto the sign-in screen instead of Pane is dead.
# If Codex still ends non-zero (e.g. q on the account picker after a failed login),
# a visible pane stays on a small menu instead of dying (the maintainer 09-30).
# Same argv as provider-launch.sh.
#
# Evening B (DISPATCH-1857a-evening-0926): best-effort §10.14 top-line paint
# via tools/hdr/pane_top.py before the provider CLI. This script is the AI-seat
# open entry aib builds into LAUNCH_CMD (see aib_build_launch_cmd) — hub chrome
# / non-AI panes never call it. FORBIDDEN: pane border format option
# (tests/test-eye-ui-launch.py ~925–928). Paint failure must not block open.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PROVIDER="${1:-}"
MODE="${2:-}"
ROLE="${3:-}"
CALLER_CWD="${4:-}"
SESSION_UUID="${5:-}"
MODEL_REQUESTED="${6:-}"

codex_auth() {
  printf '%s' "${CODEX_HOME:-$HOME/.codex}/auth.json"
}

# Best-effort: print top line as first pane content (allowed mechanism).
# Never touches pane border format option / pane border status option.
paint_ai_seat_top_line() {
  [ "${AIB_PANE_TOP:-1}" = "0" ] && return 0
  # AI providers only (same closed set as provider-launch.sh).
  case "${PROVIDER:-}" in
    claude|codex|agy|copilot|grok|deepseek|nvidia|openrouter|typesafe|openrouterjev|sand|grokbot|codexbot|musebot) ;;
    *) return 0 ;;
  esac
  local py script seat_file="" width wid=""
  py="${AIB_PYTHON3:-python3}"
  script="${AIB_PANE_TOP_PY:-}"
  if [ -z "$script" ]; then
    script="$HERE/../../tools/hdr/pane_top.py"
  fi
  [ -f "$script" ] || return 0
  width="${AIB_TOP_LINE_WIDTH:-100}"
  if [ -n "${AIB_SEAT_FILE:-}" ] && [ -f "${AIB_SEAT_FILE}" ]; then
    seat_file="$AIB_SEAT_FILE"
  elif [ -n "${EZ2_AIB_INSTANCE_HOME:-}" ] && [ -n "${TMUX_PANE:-}" ]; then
    wid="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
    if [ -n "$wid" ]; then
      seat_file="${EZ2_AIB_INSTANCE_HOME}/seats/w${wid#@}.json"
      [ -f "$seat_file" ] || seat_file=""
    fi
  fi
  if [ -n "$seat_file" ]; then
    "$py" "$script" --width "$width" --from-seat-file "$seat_file" 2>/dev/null || true
  else
    "$py" "$script" --width "$width" 2>/dev/null || true
  fi
  return 0
}

run_launch() {
  local sid="$1"
  bash "$HERE/provider-launch.sh" "$PROVIDER" "$MODE" "$ROLE" "$CALLER_CWD" "$sid" "$MODEL_REQUESTED"
}

# ── 席位自我登記(the maintainer 09-28 00:2x「為什麼會漏跑?要程式化,絕對不能漏跑」)────────────
# 以前 `aib seat re-register` 只串在 aib 開席組出來的外層那行指令上;任何不走 aib 開席的重啟
# (tmux respawn-pane、換 worktree 重起、啟動器自己重登)都會漏 ⇒ 席位檔留舊 pid,dm 判「連不到」。
# 搬進本檔:只要本檔在 tmux 窗格裡起一個 AI 席,就一定先登記一次;失敗只警告,不擋開席。
# AIB_SEAT_SELF_REGISTER=0 可關(只給治具用)。
self_register_seat() {
  [ "${AIB_SEAT_SELF_REGISTER:-1}" = "0" ] && return 0
  [ -n "${TMUX_PANE:-}" ] || return 0
  case "${PROVIDER:-}" in
    claude|codex|agy|copilot|grok|deepseek|nvidia|openrouter|typesafe|openrouterjev|sand|grokbot|codexbot|claudebot|musebot) ;;
    *) return 0 ;;
  esac
  local tmux_bin wid aib_bin
  tmux_bin="${AIB_TMUX_BIN:-tmux}"
  wid="$("$tmux_bin" display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null)" || wid=""
  case "$wid" in @[0-9]*) ;; *) echo "aib: 席位自我登記跳過:讀不到窗號(TMUX_PANE=$TMUX_PANE)" >&2; return 0 ;; esac
  aib_bin="${AIB_SEAT_REGISTER_AIB:-$HERE/aib}"
  if ! bash "$aib_bin" seat re-register "$wid" --provider "$PROVIDER" --mode "$MODE" --role "$ROLE" \
        --cwd "$CALLER_CWD" --model "$MODEL_REQUESTED" --session-uuid "$SESSION_UUID" >/dev/null 2>&1; then
    echo "aib: 席位自我登記失敗($wid;dm 可能判連不到;修:aib seat re-register $wid)" >&2
  fi
  return 0
}

self_register_seat
paint_ai_seat_top_line

# ── CodexBot:與本機 Codex server 斷線(橋以 rc=3 結束)⇒ 退避重連同一席(10-07 a seat)────────
# 10-07 00:03 真機:server 重起 ⇒ 橋死 ⇒ Pane is dead;救法是 tmux respawn-pane 原指令(同 session uuid、同席碼)。
# 這裡把那一招程式化:橋自己判斷「是斷線不是別的錯」才回 3;這裡只在 3 時重開,其他 rc 照舊結束。
# 退避 1,2,4,8,15 秒,最多 AIB_CODEXBOT_RECONNECT_MAX(預設 5)次;=0 關掉(治具)。重開前橋會自己 ensure server。
codexbot_reconnect_loop() {
  local rc="$1" max n=0 delay r5=0 r5max
  r5max="${AIB_CODEXBOT_RELOAD_MAX:-3}"
  case "$r5max" in ''|*[!0-9]*) r5max=3 ;; esac
  max="${AIB_CODEXBOT_RECONNECT_MAX:-5}"
  case "$max" in ''|*[!0-9]*) max=5 ;; esac
  # 10-11 a seat(P1b):rc=4 = 席上 /bind 換綁(橋已記下選的那條、已把舊的那條改回未綁定)⇒ 馬上重開同一席,不退避、不算斷線次數
  # 10-11 a seat(P6):rc=5 = 程式更新、席閒著,橋自己收掉要載新碼 ⇒ 同 rc=4 馬上重開同一席
  while { [ "$rc" -eq 3 ] && [ "$n" -lt "$max" ]; } || [ "$rc" -eq 4 ] || [ "$rc" -eq 5 ]; do
    if [ "$rc" -eq 4 ]; then
      r5=0
      echo "aib: CodexBot 席上換綁,重開同一席接新的那條(席碼不變)" >&2
    elif [ "$rc" -eq 5 ]; then
      # 10-11 a seat(review 建議):載新碼連續太多次 = 判斷「碼變了」的那段可能壞了 ⇒ 停,不無限重開
      r5=$((r5 + 1))
      if [ "$r5" -gt "$r5max" ]; then
        echo "aib: CodexBot 連續 ${r5max} 次載新碼後又要重開,停下來不再自動重開(可能是程式有問題;右鍵重開可再試)" >&2
        break
      fi
      echo "aib: CodexBot 程式已更新,重開同一席載新碼(綁定不變)" >&2
    else
      r5=0
      n=$((n + 1))
      case "$n" in 1) delay=1 ;; 2) delay=2 ;; 3) delay=4 ;; 4) delay=8 ;; *) delay=15 ;; esac
      echo "aib: CodexBot 與本機 Codex server 斷線,${delay} 秒後重連同一席(第 ${n}/${max} 次)" >&2
      sleep "$delay"
    fi
    paint_ai_seat_top_line
    run_launch "$SESSION_UUID"
    rc=$?
  done
  if [ "$rc" -eq 3 ]; then
    echo "aib: CodexBot 重連 ${max} 次仍斷線,不再自動重連(右鍵「重開」或 tmux respawn-pane 可再試)" >&2
  fi
  return "$rc"
}

if [ "$PROVIDER" = "codexbot" ]; then
  run_launch "$SESSION_UUID"
  codexbot_reconnect_loop $?
  exit $?
fi

if [ "$PROVIDER" != "codex" ]; then
  run_launch "$SESSION_UUID"
  exit $?
fi

AUTH="$(codex_auth)"
had_auth=0
[ -f "$AUTH" ] && had_auth=1

run_launch "$SESSION_UUID"
rc=$?

# ★ 10-05 a seat(Mini 實際踩到):這台根本沒裝 codex 時,啟動器以 127(找不到指令)結束,
#   而下面那圈只看「有沒有登入檔」⇒ 判成沒登入、重開 3 次、選單還叫人按 l 登入 —— 全指錯方向。
#   127 且這個殼真的找不到 codex ⇒ 直說沒裝與裝法,不進重開圈(登入檔不存在也不重開)。
#   只認「127 + 找不到」兩個都成立:codex 在但別的東西 127,照舊走登入判斷。
codex_missing=0
if [ "$rc" -eq 127 ] && ! command -v "${AIB_CODEX_CLI:-codex}" >/dev/null 2>&1; then
  codex_missing=1
  echo "aib: 這台找不到 codex 指令(沒裝,或不在 PATH)—— 不是沒登入,不重開。" >&2
  echo "aib: 裝法:npm install -g @openai/codex,裝好跑 codex login 登入,再按 Enter 重開這一席" >&2
fi

# /logout deletes auth.json and exits. Re-open fresh CLI → Sign in menu.
# Keep re-opening while still logged out so cancel/Ctrl-C on the login
# screen does not leave Pane is dead either. Close the window to leave.
# ★ the maintainer 10-01 裁:最多重開 3 次(AIB_CODEX_RELOGIN_MAX,治具可調)。以前沒有上限 ⇒ 沒人在看的窗格裡 codex 秒退時
#   每秒重開約 3 次、永遠不停(10-01 b10_10 逾時查到)。3 次還沒登入 ⇒ 交給下面的小選單(有人看:Enter 接回原對話 /
#   l 在這一格登入 / q 關閉);沒人看 ⇒ 照舊結束(窗格收掉,seat_exit 記一筆、印接回指令)。
relogin_max="${AIB_CODEX_RELOGIN_MAX:-3}"
case "$relogin_max" in ''|*[!0-9]*) relogin_max=3 ;; esac
relogin_n=0
while [ "$codex_missing" = 0 ] && [ ! -f "$AUTH" ]; do
  if [ "$relogin_n" -ge "$relogin_max" ]; then
    echo "aib: Codex 重開 ${relogin_max} 次仍未登入,不再自動重開(有人看的窗格會停在選單:l 可在這一格登入)" >&2
    [ "$rc" -eq 0 ] && rc=1      # 讓下面的選單接手(它只在非 0 結束時停住)
    break
  fi
  relogin_n=$((relogin_n + 1))
  if [ "$had_auth" = "1" ]; then
    echo "aib: 偵測到 Codex /logout，重開真 CLI 接登入畫面" >&2
  else
    echo "aib: Codex 尚未登入，重開真 CLI 接登入畫面" >&2
  fi
  had_auth=0
  unset AIB_RESUME_SESSION_UUID AIB_RESUME_UUID || true
  export AIB_RESUME_UUID=0
  export AIB_FRESH_SESSION=1
  export AIB_IGNORE_USAGE="${AIB_IGNORE_USAGE:-1}"
  paint_ai_seat_top_line
  run_launch ""
  rc=$?
done

# ── Codex 結束了,窗格也不收(the maintainer 09-30「codex 如果 logout 無法顯示對話欄也修」)────────
# 登入失效(權杖換不了、被登出)時 provider-launch 會跳換帳號頁;在那裡按 q、或沒有帳可選,
# 它以非 0 結束。登入檔(auth.json)還在,所以上面那圈不會接手 ⇒ 舊版這裡直接 exit ⇒ Pane is dead,
# 對話也跟著看不到(09-28 3c73 @54 真機:換帳號頁按 q ⇒ rc=2 ⇒ 死窗)。
# 改成停在一個小選單:Enter = 用原對話編號把對話開回來(不進換帳號頁;沒登入時畫面會帶登入錯誤,
# 但對話在,09-28 RUNBOOK-restore-codex-seat-e16e4 實測過)、l = 在這一格登入、q = 真的關閉。
# 只在有人看得到的窗格(TTY + tmux)才停;單測/管線沒有 TTY 照舊結束。AIB_CODEX_HOLD=0 關、=1 強開(治具)。
codex_hold_wanted() {
  case "${AIB_CODEX_HOLD:-auto}" in
    0) return 1 ;;
    1) return 0 ;;
  esac
  [ -t 0 ] && [ -n "${TMUX_PANE:-}" ]
}

# 原對話編號:這一席帶進來的續接編號;沒有就讀席位檔記的 session_uuid。都沒有 ⇒ 空(開一段新對話)。
codex_resume_uuid() {
  local u="${AIB_RESUME_SESSION_UUID:-}" wid seat_file
  if [ -z "$u" ] && [ -n "${EZ2_AIB_INSTANCE_HOME:-}" ] && [ -n "${TMUX_PANE:-}" ]; then
    wid="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
    seat_file="${EZ2_AIB_INSTANCE_HOME}/seats/w${wid#@}.json"
    if [ -n "$wid" ] && [ -f "$seat_file" ]; then
      u="$("${AIB_PYTHON3:-python3}" -c 'import json,sys
print((json.load(open(sys.argv[1], encoding="utf-8")).get("session_uuid") or "").strip())' "$seat_file" 2>/dev/null || true)"
    fi
  fi
  [[ "$u" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] && printf '%s' "$u"
  return 0
}

while [ "$rc" -ne 0 ] && codex_hold_wanted; do
  _hold_uuid="$(codex_resume_uuid)"
  printf '\n這一席的 Codex 結束了(代碼 %s)。窗格先留著,對話沒有丟。\n' "$rc"
  if [ -n "$_hold_uuid" ]; then
    printf '  Enter  把原對話開回來(對話編號 %s)\n' "${_hold_uuid:0:8}"
  else
    printf '  Enter  重開 Codex(找不到原對話編號,會是一段新對話)\n'
  fi
  printf '  l      在這一格登入 Codex\n'
  printf '  q      真的關閉這一席\n> '
  IFS= read -r _hold_choice || break          # 讀不到輸入(窗格被關)⇒ 照舊結束
  case "$_hold_choice" in
    q|Q) break ;;
    l|L)
      "${AIB_CODEX_BIN:-codex}" login || true
      continue ;;
  esac
  paint_ai_seat_top_line
  if [ -n "$_hold_uuid" ]; then
    AIB_RESUME_UUID=1 AIB_RESUME_SESSION_UUID="$_hold_uuid" AIB_IGNORE_USAGE=1 AIB_CODEX_PACK=0 AIB_NO_ONBOARD=1 \
      run_launch "$SESSION_UUID"
  else
    AIB_IGNORE_USAGE=1 AIB_CODEX_PACK=0 AIB_NO_ONBOARD=1 run_launch ""
  fi
  rc=$?
done

exit "$rc"
