#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 開 CLI 席時「要不要載入舊席位」選單(the maintainer 2026-09-24 H1–H3)。由 aib 開席取碼段 source 並呼叫。
#
#   aib_cli_session_menu <provider> <cwd> <seats_dir> <role>
#     ⇒ 選了某列:設 AIB_FORK_FROM_SESSION_UUID=<那段對話>(分出新席,**永遠新碼**)
#       逾時/選 0/沒清單/選單跳不出來:不動(= 空白開席)
#
# 裁定史(這是裁定變更,不是 bug):
#   09-21 the maintainer:新開/右鍵新開預設接回原對話 —— 當時沒有選單,直接接回
#   09-24 20:4x the maintainer:改成跳選單列舊席位;**30 秒未選 ⇒ 空白開席**(預設值翻面)
#   09-24 21:xx the maintainer 澄清:接回 = 新開獨一無二的席位 + 載入舊對話/人格/第二大腦 ⇒ 選任一列都是 fork
# 機制原抄 bot-thread-menu.sh(display-menu 放背景、本殼輪詢選擇檔);09-26 起改 display-popup(見下),
# 落「選擇檔」、本殼輪詢到逾時。兩支的差別只在逾時預設(那支=沿用上次綁定;這支=空白)。
# ★ 兩支還沒收斂成一支共用殼(RECOMMENDATIONS H3 建議共用);codexbot 那支剛上線、屬別席,這輪不動它。
#
# 旗標:AIB_SEAT_RESUME_MENU=0 關(自動化開席:直接空白)、AIB_SEAT_RESUME_MENU_TIMEOUT_S(30)、
#       AIB_CLI_SESSIONS_JSON_FILE(尺的注入點:離線餵清單)、AIB_CLI_SESSION_MENU_TARGET(tmux 目標)、
#       AIB_CLI_SESSION_MENU_OUTER(外層艙 session 名;預設 aib 的 $S,空=不判、照舊跳 tmux 選單)。
#
# ★★ 2026-09-25 23:4x(the maintainer「aib claude dgrs root 以前秒開,現在要幾十秒」):
#   從外面的終端機打 `aib claude ...` 時,選單跳在 hub 上,而那一刻**沒有任何終端接在 hub 上**
#   (aib 要到最後才 attach)⇒ 沒人看得到,只能等滿 30 秒;attach 之後選單才浮出來,
#   而那時再選已經沒用(碼表 23:35 那次:32.1 秒逾時空白,23:35:51 才選到 d1debbdc,被丟掉)。
#   修法:先問外層艙(aib 最後 attach 的 session)有沒有終端接著(list-clients),沒有就不跳、不等。
#     ★ 不能問 hub:hub 上永遠接著指揮艙中間那格內嵌的 tmux,問了恆為「有人」。
#   逾時後選擇檔**留著**寫 __timeout__,還掛在畫面上的舊選單之後再點是 no-op(不會落一個沒人讀的檔)。
#
# ★★ 2026-09-26 00:0x the maintainer:「右欄出現之後有兩個流程(載入舊 AI、三選一開席單),都倒數 30 秒,
#   併成一個,其他所有 AI 比照辦理」。本函式就是那一個選單,由 aib 背景段在**右欄五項正確、整備之後**
#   於那一席的窗上跳(所有 AI 同一個時點;CLI 已先起好,選單不擋人)。開席單三項(1 全部/2 只鐵則/3 不載入)
#   + 能分出新席的家(今天只有 claude)再列舊席位 a–i(= 用新的 claude 載入舊的人格、第二大腦、上一場紀錄)。
#   選項只落選擇檔;投遞 / 分出新席重起由 aib 背景段做。AIB_CLI_SESSION_MENU_EXCLUDE=本席 uuid 不列。
#     aib_cli_session_menu <provider> <cwd> <seats_dir> <role> [with_onboard=0] [with_fork=1]
#     ⇒ AIB_OPEN_MENU_CHOICE = fork(並 export AIB_FORK_FROM_SESSION_UUID)| blank | 1 | 2 | 3 | timeout
#       | noviewer(外層艙沒人接著,沒跳)| none(沒得選/跳不出來)

# 畫面字走四語字典(語系盤查第 14 條,10-14):_csm <鍵> [名=值 …];每次照 i18n_lib.resolve_locale() 取。
#   本檔被 aib(set -euo pipefail)載入:python 起不來 ⇒ 印鍵名、rc 照 0。
_csm() {
  local key="$1"; shift
  local args=() kv
  for kv in "$@"; do args+=(--param "$kv"); done
  "${T_PY3:-${EZ2_PY3:-python3}}" "${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/i18n_lib.py" \
    ${args[@]+"${args[@]}"} "$key" 2>/dev/null || printf '%s\n' "$key"
}

aib_cli_session_menu() {
  local provider="$1" cwd="$2" seats_dir="$3" role="$4" with_onboard="${5:-0}" with_fork="${6:-1}"
  AIB_OPEN_MENU_CHOICE="none"
  [ "${DRY:-0}" != "1" ] || return 0
  [ "${AIB_SEAT_RESUME_MENU:-1}" != "0" ] || with_fork=0
  [ -z "${AIB_FORK_FROM_SESSION_UUID:-}" ] || with_fork=0          # 已明確指定來源
  local py="${T_PY3:-${EZ2_PY3:-python3}}" here="${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  local json rows=""
  local target="${AIB_CLI_SESSION_MENU_TARGET:-${EZ2_TMUX_SESSION_NAME:-aihub}}"
  local hub_sock="${HUB:-${EZ2_AIHUB_SOCKET:-aihub}}"
  local popup_w_pct=90        # 與 cli_sessions.POPUP_W_PCT 同一個值
  if [ "$with_fork" = "1" ]; then
    if [ -n "${AIB_CLI_SESSIONS_JSON_FILE:-}" ]; then
      json="$(cat "$AIB_CLI_SESSIONS_JSON_FILE" 2>/dev/null)" || json=""
    else
      # #399 R1(@aeb40):列的寬度預算要用**彈窗內寬**(終端寬 × 90% − 2 框線),不是終端寬 ⇒ 先量彈窗要開在的那個 client;
      #   量不到(沒有 tmux、非數字)⇒ 不給,cli_sessions 用 80 欄終端的保守值
      local client_w popup_cols=0
      client_w="$(env -u TMUX "${AIB_TMUX_BIN:-tmux}" -L "$hub_sock" display-message -p -t "$target" '#{client_width}' 2>/dev/null || true)"
      case "$client_w" in ''|*[!0-9]*) ;; *) popup_cols=$(( client_w * popup_w_pct / 100 - 2 )) ;; esac
      json="$("$py" "$here/cli_sessions.py" --provider "$provider" --cwd "$cwd" --seats-dir "$seats_dir" \
                --role "$role" --limit 9 --popup-cols "$popup_cols" --json 2>/dev/null)" || json=""
    fi
    [ -z "$json" ] || rows="$(printf '%s' "$json" | "$py" -c '
import sys, json
d = json.load(sys.stdin)
if not d.get("supported"): sys.exit(0)
skip = sys.argv[1] if len(sys.argv) > 1 else ""
for r in d.get("rows") or []:
    if skip and r.get("id") == skip:
        continue                     # 這一席自己剛起的空白對話不列(先啟動、後選)
    tail = (r.get("tail") or "").replace("\t", " ").replace("\n", " ")
    # bot_name = Grok Bot 側欄名;空則選單退回 model(claude 模型名 / grokbot 字面)
    # ★ bound 必須在 bot_name 前:bash IFS=$'\t' read 會把空欄位吃掉,空 bot 會把 bound「1/0」讀成名(顯示〔1〕且丟灰階)。
    bot = (r.get("bot_name") or "").replace("\t", " ").replace("\n", " ")
    model = (r.get("model") or "").replace("\t", " ")
    # four 接在 star 後、tail 前(兩個都不會空),免得被前面講的空欄吞掉而錯位
    print("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
        r["id"], r.get("code") or "", "1" if r.get("star") else "0", "1" if r.get("four_col") else "0",
        tail[:90], model, "1" if r.get("bound") else "0", bot))
' "${AIB_CLI_SESSION_MENU_EXCLUDE:-}" 2>/dev/null)" || rows=""
  fi
  [ -n "$rows" ] || [ "$with_onboard" = "1" ] || return 0      # 沒舊席位也沒開席單 ⇒ 不跳選單
  local limit="${AIB_SEAT_RESUME_MENU_TIMEOUT_S:-${AIB_ONBOARD_MENU_TIMEOUT_S:-30}}"
  # 看的是**外層艙**(aib 最後 attach 的那個 session,在 default 伺服器)有沒有人接著;
  # hub 上永遠接著一個 client —— 指揮艙中間那格內嵌的 tmux ⇒ 問 hub 恆為「有人」(23:44 測試艙實測)。
  local outer="${AIB_CLI_SESSION_MENU_OUTER-${S:-}}" viewers=1
  if [ -n "$outer" ]; then
    viewers="$(env -u TMUX "${AIB_TMUX_BIN:-tmux}" -L default list-clients -t "$outer" -F x 2>/dev/null | grep -c . || true)"
  fi
  if [ "${viewers:-0}" = "0" ]; then
    AIB_OPEN_MENU_CHOICE="noviewer"
    echo "aib: $(_csm aib.climenu.no_terminal provider="${provider}")" >&2
    return 0
  fi
  local state_dir="${EZ2_COCKPIT_STATE_DIR:-${TMPDIR:-/tmp}}"; mkdir -p "$state_dir" 2>/dev/null
  # 逾時留下的選擇檔(見檔頭)一小時後清掉;只清本選單的檔名
  find "$state_dir" -maxdepth 1 -name 'cli-session-choice-*' -mmin +60 -delete 2>/dev/null || true
  local choice_f="$state_dir/cli-session-choice-$$"; rm -f "$choice_f" 2>/dev/null
  local n=0 id code star four tail label key title note spec items_tsv=""
  local keys="abcdefghi"
  _aib_menu_item() { items_tsv="${items_tsv}$2"$'\t'"$1"$'\t'"$3"$'\t'"${4:-0}"$'\n'; }   # $1=標籤 $2=鍵 $3=值 $4=dim(1=已綁灰階)
  if [ "$with_onboard" = "1" ]; then
    _aib_menu_item "$(_csm aib.climenu.onboard_full)" 1 1
    _aib_menu_item "$(_csm aib.climenu.onboard_rules)" 2 2
    _aib_menu_item "$(_csm aib.climenu.onboard_none)" 3 3
    [ -z "$rows" ] || items_tsv="${items_tsv}"$'\t\t\n'           # 分隔線
    title="$(_csm aib.climenu.title_open provider="${provider}")"; note="$(_csm aib.climenu.note_none)${rows:+$(_csm aib.climenu.note_no_old)}"
  else
    title="$(_csm aib.climenu.title_pick_old provider="${provider}")"; note="$(_csm aib.climenu.note_blank)"
  fi
  # ★ 10-28 Andy「下方的載入席位,只要顯示 席位/人格/專案名稱/最新開啟時間 這樣就好」取代 the maintainer 09-26
  #   「最左帶上一次 AI 模型」:four=1 的列(claude / codex / CLI)只剩「★ @碼 · 人格 · 專案 · 時間 [· 使用中]」,
  #   不再帶〔模型〕、「載入」、角色、大腦、交接。橋接 bot 列(four=0)這次不動,照下面 09-26 的舊格式。
  # ★ the maintainer 09-26(bot 列):有 bot_name(側欄名)用它,否則用 model,放最左。
  # ★ the maintainer 09-26 灰階:已連 Grok Bot agent(bound=1)列 dim;「未綁」字樣仍=無 persona(資料層分開)。
  # ★ TSV 欄序 id/code/star/four/tail/model/bound/bot_name —— bound 不可接在空 bot 後面(bash 吞空欄)。
  local load_word=""; [ "$with_onboard" = "1" ] && load_word="$(_csm aib.climenu.load)"   # 迴圈外取一次(每列叫一次 python 會讓彈窗慢)
  while IFS=$'\t' read -r id code star four tail model bound bot_name; do
    [ -n "$id" ] || continue; n=$((n+1)); [ "$n" -le 9 ] || break
    if [ "$with_onboard" = "1" ]; then key="${keys:$((n-1)):1}"; else key="$n"; fi
    if [ "$four" = "1" ]; then
      label="@${code} · $tail"; [ "$star" = "1" ] && label="★ $label"
      _aib_menu_item "$label" "$key" "$id" "${bound:-0}"; continue
    fi
    name="${bot_name:-${model:-$(_csm aib.climenu.model_unknown)}}"
    label="〔${name}〕 @${code}"
    [ "$star" = "1" ] && label="$label ★"
    [ "$with_onboard" = "1" ] && label="$label $load_word"
    _aib_menu_item "$label  $tail" "$key" "$id" "${bound:-0}"
  done <<< "$rows"
  [ "$with_onboard" = "1" ] || _aib_menu_item "$(_csm aib.climenu.blank_default)" 0 __blank__
  unset -f _aib_menu_item
  # ★ the maintainer 09-26:「這個清單必需要有選擇,或倒數完成才能消失」⇒ 不用 display-menu(q/Esc/點外面就關、逾時後殘留),
  #   改 display-popup -E 跑 open_menu_popup.py:只有選了或倒數到 0 才結束,結束=彈窗關。截止時刻由這裡定,兩邊同一個。
  local deadline=$(( $(date +%s) + limit )) nlines
  spec="$state_dir/cli-session-menu-$$.json"
  printf '%s' "$items_tsv" | "$py" -c '
import json, sys
title, note, deadline, cf, out = sys.argv[1:6]
items = []
for line in sys.stdin.read().split("\n"):
    if not line:
        continue
    k, label, value, dim = (line.split("\t") + ["", "", "", "0"])[:4]
    if not k:
        items.append({"separator": True})
    else:
        it = {"key": k, "label": label, "value": value}
        if dim == "1":
            it["dim"] = True          # 已綁 → 彈窗灰階
        items.append(it)
json.dump({"title": title, "note": note, "deadline": int(deadline), "choice_file": cf, "items": items},
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
' "$title" "$note" "$deadline" "$choice_f" "$spec" 2>/dev/null || { echo "aib: $(_csm aib.climenu.spec_fail)" >&2; return 0; }
  nlines="$(printf '%s' "$items_tsv" | grep -c '' || true)"
  local popup_cmd; printf -v popup_cmd '%q %q %q' "$py" "$here/open_menu_popup.py" "$spec"
  env -u TMUX "${AIB_TMUX_BIN:-tmux}" -L "$hub_sock" display-popup -E -t "$target" -x C -y C \
      -w "${popup_w_pct}%" -h "$(( nlines + 6 ))" -T " ${title} " "$popup_cmd" 2>/dev/null &
  local mpid=$!; sleep 0.5
  if ! kill -0 "$mpid" 2>/dev/null; then
    _menu_rc=0; wait "$mpid" 2>/dev/null || _menu_rc=$?
    # 彈窗 0.5 秒內就結束:rc≠0=跳不出來;rc=0 可能是已經選了(自動選/秒選)——看選擇檔
    if [ "$_menu_rc" != "0" ] && [ ! -f "$choice_f" ]; then
      rm -f "$spec" 2>/dev/null || true
      echo "aib: $(_csm aib.climenu.popup_fail rc="$_menu_rc")" >&2; return 0
    fi
  fi
  AIB_OPEN_MENU_CHOICE="timeout"
  # 碼表:跳出的那一刻記(aib 有 aib_attempt_mark 才記;選單結束後才記會把「跳出」記成「結束」)
  if declare -F aib_attempt_mark >/dev/null 2>&1; then aib_attempt_mark menu_shown "$target" || true; fi
  local waited=0 choice="" _menu_rc=0
  while [ "$waited" -lt "$(( (limit + 1) * 5 ))" ]; do          # 彈窗自己在截止時刻關;多等 1 秒收尾
    if [ -f "$choice_f" ]; then choice="$(cat "$choice_f" 2>/dev/null)"; break; fi
    sleep 0.2; waited=$((waited+1))
  done
  # ★★ 2026-09-25(the maintainer 實測「選單可以選了,但 returned 1」):aib 是 `set -euo pipefail`,
  #   而**使用者真的選了一列**時 display-menu 已經回傳、背景行程已結束 ⇒ 這個 kill 必失敗
  #   ⇒ set -e 當場砍掉 aib,rc=1 而且**一個字都不印**(日誌裡只有前一行)。
  #   逾時那條路選單還活著、kill 成功,所以**只驗逾時永遠看不到這個洞**——我上線前就是只驗了逾時。
  #   同段的 `wait` 也一樣:非零時 set -e 先動手,後面那行 `[ "$?" = "0" ]` 是死碼。
  kill "$mpid" 2>/dev/null || true
  rm -f "$choice_f" 2>/dev/null || true
  rm -f "$spec" 2>/dev/null || true
  # 逾時:佔住選擇檔 ⇒ 彈窗萬一還沒關,之後再選也不落檔(O_EXCL)
  [ -n "$choice" ] || printf '__timeout__' > "$choice_f" 2>/dev/null || true
  case "$choice" in
    "") if [ "$with_onboard" = "1" ]; then echo "aib: $(_csm aib.climenu.timeout_onboard provider="${provider}" seconds="${limit}")" >&2
        else echo "aib: $(_csm aib.climenu.timeout_blank provider="${provider}" seconds="${limit}")" >&2; fi ;;
    __blank__) AIB_OPEN_MENU_CHOICE="blank"; echo "aib: $(_csm aib.climenu.picked_blank provider="${provider}")" >&2 ;;
    1|2|3) AIB_OPEN_MENU_CHOICE="$choice"; echo "aib: $(_csm aib.climenu.picked provider="${provider}" value="$choice")" >&2 ;;
    *) AIB_OPEN_MENU_CHOICE="fork"; export AIB_FORK_FROM_SESSION_UUID="$choice"
       echo "aib: $(_csm aib.climenu.forked provider="${provider}" value="${choice}")" >&2 ;;
  esac
  return 0
}
