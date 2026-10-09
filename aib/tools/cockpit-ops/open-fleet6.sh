#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# open-fleet6.sh — the maintainer 說「aib sit6」時的唯一入口(2026-08-29 a seat 定版)。
#
# 封裝今晚三顆實測發現,程式化不再犯:
#   1 手工 tmux 開艙=無艙號/無艦長/席檔手寫 → 一律走 aib 正制。
#   2 seats_batch 對名冊外角色 rc=2 斷批 → 名冊外席逐席直開。
#   3 aib 代開會劫走人的終端 → 全程 AIB_NO_CLIENT_SWITCH=1,只印 attach。
# 開完跑七科 postflight;任一科紅 → rc=1 且逐科印讀數,不宣稱開完。
# 用法:tools/cockpit-ops/open-fleet6.sh   (固定六席:cap qa:codex dev1 dev2 dev3:copilot agy:agy)
set -uo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/../../src/cockpit" && pwd)"
export AIB_NO_CLIENT_SWITCH=1
say(){ printf '%s\n' "$*" >&2; }

# ── B14:開艙前先證這棵 checkout 對 origin/main 新鮮(裁定 RULING-B14-…)────────
# ★ 放在這裡而不是更後面:必須在**任何 tmux/aib 副作用之前**。擋到一半的艙比沒擋更貴。
# ★★ 判準是**白名單**:只有 rc==0 才繼續。
#   不要寫成「rc==1 就擋」—— 哪天閘多一種 rc,那種寫法會從**沒被列舉的那一側放行**,
#   而「放行」正是本件要擋的那個方向。
FRESHNESS_GATE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cabin-freshness.sh"
# ★ 這裡刻意**不用 `cd -P`**:修復指令是給人貼的,而 -P 會把 symlink 解掉
#   (macOS 上 /var → /private/var),印出來的路徑使用者不認得。
FRESHNESS_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -f "$FRESHNESS_GATE" ]; then
  bash "$FRESHNESS_GATE" "$FRESHNESS_REPO"
  FRESHNESS_RC=$?
  if [ "$FRESHNESS_RC" != "0" ]; then
    say "開艙中止:新鮮度閘 rc=$FRESHNESS_RC(未開任何席)"
    exit "$FRESHNESS_RC"
  fi
else
  # ★ 閘檔不存在 = **判不出來**,不是新鮮(cap a seat 16:20 補裁 §六)。
  #   沒有這一格的話,「閘在那裡」與「閘被刪了」在開艙路徑上是同一個結果。
  #
  # ★★ 下面這段是**逃生門規則的第二份**,我知道,而它不是疏忽:
  #   單一真相源是 cabin-freshness.sh 自己,而這一格的前提**就是那個檔不在**
  #   —— 沒有任何東西可以委派。兩份會漂移(環境變數名、留痕路徑、空白判定),
  #   所以三個字面刻意與閘內逐字相同,並請出臂席釘住「兩處用同一個變數名與同一條留痕路徑」。
  # ★ 配套的理由(cap 16:20):逃生門若只涵蓋「落後」,checkout 壞掉或檔案被誤刪時
  #   **沒有任何一條路能開艙** —— 那比它要防的病更貴。
  _LEGACY_FRESH_REASON_ENV="AIB_"'CABIN_FRESHNESS_REASON'
  if [ "${AIB_OPEN_FLEET_FRESHNESS_REASON+x}" != "x" ] \
     && printenv "$_LEGACY_FRESH_REASON_ENV" >/dev/null 2>&1; then
    say "開艙擋下:新鮮度判不出來(你設的是舊名 $_LEGACY_FRESH_REASON_ENV,現在的名字是 AIB_OPEN_FLEET_FRESHNESS_REASON)"
    say "開艙中止:新鮮度閘 rc=3(未開任何席)"
    exit 3
  fi
  _FRESH_REASON="${AIB_OPEN_FLEET_FRESHNESS_REASON:-}"
  if [ -n "${_FRESH_REASON//[[:space:]]/}" ]; then
    _FRESH_TRACE="$HOME/.ez2-aib/state/cabin-open-gate.jsonl"
    mkdir -p "$(dirname "$_FRESH_TRACE")" 2>/dev/null || true
    AIB_B14_TRACE="$_FRESH_TRACE" AIB_B14_VERDICT="gate_missing" \
    AIB_B14_REASON="$_FRESH_REASON" AIB_B14_REPO="$FRESHNESS_REPO" \
    python3 - <<'B14PY' 2>/dev/null || true
import datetime, json, os
row = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "gate": "cabin-freshness",
    "verdict": os.environ["AIB_B14_VERDICT"],
    "repo": os.environ["AIB_B14_REPO"],
    "reason": os.environ["AIB_B14_REASON"],
}
with open(os.environ["AIB_B14_TRACE"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
B14PY
    say "開艙放行:逃生門 AIB_OPEN_FLEET_FRESHNESS_REASON"
    say "  理由 = $_FRESH_REASON"
    say "  留痕 = $_FRESH_TRACE"
  else
    say "開艙擋下:新鮮度判不出來(閘檔不存在)"
    say "  這是**判不出來**,不是沒落後。缺的是 $FRESHNESS_GATE"
    say "  真的要放行:AIB_OPEN_FLEET_FRESHNESS_REASON=\"理由\" 重跑(理由會被記檔)"
    say "開艙中止:新鮮度閘 rc=3(未開任何席)"
    exit 3
  fi
fi

say "── 步1 正制開艙+艦長席(cap,claude)"
OUT="$(bash "$HERE/aib" claude dgrs cap new 2>&1)" || { say "$OUT"; exit 1; }
I="$(printf '%s' "$OUT" | sed -n 's/^aib: instance=\([0-9a-f-]*\) .*/\1/p' | head -1)"
case "$I" in *-*-*-*|"") say "艙號解析失敗或非正制:[$I]"; say "$OUT"; exit 1 ;; *-*-*) : ;; esac
HUB="$(bash "$HERE/cabin_resolve.sh" --hub "$I")" \
  || { say "艙 hub 單源解析失敗:[$I]"; exit 1; }
[ -n "$HUB" ] || { say "艙 hub 單源解析失敗:[$I](空輸出)"; exit 1; }
FLEET_SESSION="$(bash "$HERE/cabin_resolve.sh" --session "$I")" \
  || { say "指揮 session 單源解析失敗:[$I]"; exit 1; }
[ -n "$FLEET_SESSION" ] || { say "指揮 session 單源解析失敗:[$I](空輸出)"; exit 1; }
say "艙=$I"

resolve_window_provider(){
  local _wid="$1" _seat_file _seat_rc
  _seat_file="$HOME/.ez2-aib/instances/$I/seats/w${_wid#@}.json"
  _RP_SEAT=""
  if [ -e "$_seat_file" ]; then
    _RP_SEAT="$(python3 -c 'import json,sys
v=json.load(open(sys.argv[1],encoding="utf-8")).get("provider")
print(v if isinstance(v,str) and v else "__invalid__")' "$_seat_file" 2>/dev/null)"
    _seat_rc=$?
    [ "$_seat_rc" = "0" ] || _RP_SEAT="__invalid__"
  fi
  _RP_TMUX="$(tmux -L "$HUB" show-options -w -qv -t "aihub:$_wid" @aib_provider 2>/dev/null)"
  _RP_OUT="$(python3 "$HERE/composer_dialect.py" resolve-provider \
               "$_RP_SEAT" "$_RP_TMUX" 2>/dev/null)"
  _RP_RC=$?
  _RP_PROVIDER="$(printf '%s\n' "$_RP_OUT" | sed -n '1p')"
  _RP_SOURCE="$(printf '%s\n' "$_RP_OUT" | sed -n '2p')"
  _RP_DETAIL="$(printf '%s\n' "$_RP_OUT" | sed -n '3p')"
  [ -n "$_RP_DETAIL" ] || _RP_DETAIL="席位檔=${_RP_SEAT:-缺} tmux=${_RP_TMUX:-缺}"
  return "$_RP_RC"
}

say "── 步2 五席逐席直開(名冊外角色不走批次——實測 seats_batch 會斷)"
open1(){ EZ2_AIB_INSTANCE="$I" EZ2_AIHUB_SOCKET="$HUB" bash "$HERE/aib" "$1" dgrs "$2" >/dev/null 2>&1; }
open1 codex   qa   || say "★ qa(codex) 開席非零(額度紅時屬預期,pane 可能即死)"
open1 claude  dev1 || { say "dev1 開席失敗"; exit 1; }
open1 claude  dev2 || { say "dev2 開席失敗"; exit 1; }
open1 copilot dev3 || say "★ dev3(copilot) 開席非零"
open1 agy     monitor || say "★ agy(monitor)開席非零(額度紅時屬預期)"  # 角色照名冊=monitor,圖示 📡(修訂二殘格)

say "── 步2b codex 開機安頓(更新選單/歡迎頁會殺 pane 或卡投遞——08-29 實測)"
QAW="$(tmux -L "$HUB" list-windows -t aihub -F '#{window_id} #{window_name}' | awk '$2=="qa-codex"{print $1;exit}')"
if [ -n "$QAW" ]; then
  resolve_window_provider "$QAW"
  _qprc=$?
  [ "$_qprc" = "0" ] && [ "$_RP_PROVIDER" = "codex" ] \
    || { say "qa provider 停手:${_RP_DETAIL} resolved=${_RP_PROVIDER:-無}"; exit 1; }
  say "qa provider=$_RP_PROVIDER 來源=$_RP_SOURCE ($_RP_DETAIL)"
  for _i in 1 2 3 4 5 6; do
    _st="$(tmux -L "$HUB" capture-pane -p -e -t "aihub:$QAW" 2>/dev/null            | python3 "$HERE/composer_state.py" --stdin --provider "$_RP_PROVIDER" 2>/dev/null || true)"
    case "$_st" in empty|suggestion) say "codex 安頓完成(composer=$_st)"; break ;; esac
    _scr="$(tmux -L "$HUB" capture-pane -p -t "aihub:$QAW" 2>/dev/null | tail -8)"
    if printf '%s' "$_scr" | grep -q "Press enter to continue"; then
      tmux -L "$HUB" send-keys -t "aihub:$QAW" Enter
    elif printf '%s' "$_scr" | grep -qi "update available\|Skip until next"; then
      tmux -L "$HUB" send-keys -t "aihub:$QAW" 2; sleep 1
      tmux -L "$HUB" send-keys -t "aihub:$QAW" Enter   # 2=Skip:更新交人裁,開艙不自動升版
    elif printf '%s' "$_scr" | grep -q "Sign in"; then
      say "★ codex 需要登入(OAuth 屬人的動作),安頓停在登入頁"; break
    fi
    sleep 4
  done
fi

say "── 步2c copilot 安頓(它開機會停在舊 session 還原頁,esc=start fresh)"
CPW="$(tmux -L "$HUB" list-windows -t aihub -F '#{window_id} #{window_name}' | awk '$2=="dev3-copilot"{print $1;exit}')"
if [ -n "$CPW" ]; then
  resolve_window_provider "$CPW"
  _cpprc=$?
  [ "$_cpprc" = "0" ] && [ "$_RP_PROVIDER" = "copilot" ] \
    || { say "dev3 provider 停手:${_RP_DETAIL} resolved=${_RP_PROVIDER:-無}"; exit 1; }
  say "dev3 provider=$_RP_PROVIDER 來源=$_RP_SOURCE ($_RP_DETAIL)"
  for _i in 1 2 3; do
    _scr="$(tmux -L "$HUB" capture-pane -p -t "aihub:$CPW" 2>/dev/null | tail -4)"
    if printf '%s' "$_scr" | grep -q "esc start"; then
      tmux -L "$HUB" send-keys -t "aihub:$CPW" Escape; sleep 3
    else break; fi
  done
fi

say "── 步2d 開席單官方補投(aib seat onboard --missing;依目標席 provider 方言驗證)"
EZ2_AIB_INSTANCE="$I" EZ2_AIHUB_SOCKET="$HUB" bash "$HERE/aib" seat onboard --missing 2>&1 | tail -4 >&2 || true

say "── 步3 艦長任命(開席不含任命,是防誤任命的設計)"
CAPW="$(tmux -L "$HUB" list-windows -t aihub -F '#{window_index} #{window_name}' | awk '$2=="cap-claude"{print $1;exit}')"
python3 "$HERE/appoint.py" captain "@${CAPW:-2}" --by "${AIB_OPENER:-open-fleet6}" \
  --reason "open-fleet6 開航任命" --instance "$I" >/dev/null 2>&1 || { say "任命失敗"; exit 1; }

say "── 步4 七科 postflight(讀數說話,不宣稱)"
FAIL=0
case "$I" in *-*-*) say "科1 艙號正制 ✔ $I";; *) say "科1 ✘"; FAIL=1;; esac
NW="$(tmux -L "$HUB" list-windows -t aihub 2>/dev/null | wc -l | tr -d ' ')"
[ "$NW" -ge 6 ] && say "科2 窗數 ✔ $NW" || { say "科2 ✘ 窗數 $NW"; FAIL=1; }
NS="$(ls ~/.ez2-aib/instances/"$I"/seats/w*.json 2>/dev/null | wc -l | tr -d ' ')"
[ "$NS" -ge 6 ] && say "科3 席檔 ✔ $NS 份" || { say "科3 ✘ 席檔 $NS"; FAIL=1; }
NC=0; for w in $(tmux -L "$HUB" list-windows -t aihub -F '#{window_index}'); do
  [ "$(tmux -L "$HUB" show-options -w -t aihub:"$w" -qv @aib_captain 2>/dev/null)" = "1" ] && NC=$((NC+1)); done
[ "$NC" -eq 1 ] && say "科4 艦長恰一 ✔" || { say "科4 ✘ 艦長數 $NC"; FAIL=1; }
tmux -S /private/tmp/tmux-501/default has-session -t "$FLEET_SESSION" 2>/dev/null \
  && say "科5 指揮艙 ✔" || { say "科5 ✘ 指揮艙不存在"; FAIL=1; }
CAPCODE="$(python3 - "$HOME/.ez2-aib/instances/$I/seats" "$HERE" <<'PY'
import glob, json, os, sys
sys.path.insert(0, sys.argv[2])
import seat_address

records = []
for path in glob.glob(os.path.join(sys.argv[1], "w*.json")):
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        continue
    # B15:科6 驗的是**本次機器任命的裁定者**，不是開席時的角色標籤。
    # role=cap 的前任可已轉顧問；回退 role 會把開席回報安靜投給前任。
    if rec.get("captain_assigned") is True and rec.get("advisor") is not True:
        records.append(rec)
if len(records) != 1:
    raise SystemExit(2)
code = seat_address.code_of(records[0])
if not code:
    raise SystemExit(2)
print(code)
PY
)"
_capcode_rc=$?
if [ "$_capcode_rc" != "0" ] || [ -z "$CAPCODE" ]; then
  say "科6 ✘ 艦長座位碼量不到"
  FAIL=1
elif EZ2_AIHUB_SOCKET="$HUB" bash "$HERE/dm-ctl.sh" --resolve-only "$CAPCODE" </dev/null 2>/dev/null | grep -q canonical_target; then
  say "科6 定址 ✔ $CAPCODE"
else
  say "科6 ✘"
  FAIL=1
fi
say "科7 你的眼睛:tmux attach -t $FLEET_SESSION(掛上後 list-clients 可驗;本科不擋 rc)"
# 科8 composer 可投遞性:席位檔與 tmux provider 先同源對帳,再交方言單源。
for _w in $(tmux -L "$HUB" list-windows -t aihub -F '#{window_id}|#{window_name}' | awk -F'|' '$2!="watch"{print}'); do
  _id="${_w%%|*}"; _nm="${_w##*|}"
  resolve_window_provider "$_id"
  _rprc=$?
  if [ "$_rprc" != "0" ]; then
    say "科8 $_nm provider 停手:${_RP_DETAIL} ✘"; FAIL=1; continue
  fi
  _st="$(tmux -L "$HUB" capture-pane -p -e -t "aihub:$_id" 2>/dev/null          | python3 "$HERE/composer_state.py" --stdin --provider "$_RP_PROVIDER" 2>/dev/null || true)"
  case "$_st" in empty|suggestion) say "科8 $_nm provider=${_RP_PROVIDER:-unknown} 來源=$_RP_SOURCE composer=$_st ✔";;
    draft) say "科8 $_nm provider=${_RP_PROVIDER:-unknown} 來源=$_RP_SOURCE composer=draft ✘(有未送稿)"; FAIL=1;;
    *) say "科8 $_nm provider=${_RP_PROVIDER:-unknown} 來源=$_RP_SOURCE composer=${_st:-unknown} ✘(投遞會被擋)"; FAIL=1;; esac
done
[ "$FAIL" -eq 0 ] && { say "✔ 開艦完成(七科 6 硬科全綠)"; exit 0; } || { say "✘ 有科紅,不宣稱開完"; exit 1; }
