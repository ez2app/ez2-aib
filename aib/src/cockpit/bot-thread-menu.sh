#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 右鍵新增本地對話 AI 席位(codexbot/…)時的「要綁哪條對話」選單。由 aib 在 --check 之前 source 並呼叫。
#
#   aib_bot_thread_menu <provider>
#     ⇒ 設定 AIB_CODEXBOT_THREAD_OVERRIDE(選了某條)或不動(逾時/沒選/沒清單=沿用上次綁定的預設)
#     grokbot(09-25 a seat):選的是 Grok Bot agent ⇒ 設定 AIB_GROKBOT_AGENT_OVERRIDE + AIB_GROKBOT_AGENT_ID;
#       預設=呼叫前的 AIB_GROKBOT_AGENT_ID(aib 端先填:唯一一個綁過 webhook 的 agent)。
#
# the maintainer 2026-09-24:「右鍵新增 codexbot 時,應該先列出已知的 uuid 讓用戶選擇綁定;如果沒有選擇,就將
# 上次綁定過的預設 30 秒自動綁定;這個 list 也應該列出其他的例如 grok 或未來的本地對話 ai。」
# 清單來源:bot_threads.py --provider <P> --json(一家一個後端;沒後端=不跳選單,直接沿用預設)。
# 選單機制抄 aib_onboard_menu:display-menu 放背景、選項用 run-shell 落「選擇檔」、本殼輪詢到逾時。
# 逾時(AIB_BOT_THREAD_MENU_TIMEOUT_S,預設 30)=預設 —— 這裡的「預設」就是 codexbot.env 上次綁的那條,
# 不是「不綁」:沒有對話可綁的席開了也沒用。

aib_bot_thread_menu() {
  local provider="$1"
  [ "${AIB_BOT_THREAD_MENU:-1}" != "0" ] || return 0            # 自動化/測試關掉
  [ "${DRY:-0}" != "1" ] || return 0
  [ -z "${AIB_CODEXBOT_THREAD_OVERRIDE:-}" ] || [ "$provider" = "grokbot" ] || [ "$provider" = "claudebot" ] || [ "$provider" = "musebot" ] || return 0   # 已經 --thread 指定
  [ -z "${AIB_GROKBOT_AGENT_OVERRIDE:-}" ] || [ "$provider" != "grokbot" ] || return 0
  [ -z "${AIB_CLAUDEBOT_SESSION_OVERRIDE:-}" ] || [ "$provider" != "claudebot" ] || return 0
  local ask="要接哪條桌面對話?"
  [ "$provider" != "grokbot" ] || ask="要綁哪個 Grok agent?"
  [ "$provider" != "claudebot" ] || ask="要綁哪條本機 Claude 對話?"
  [ "$provider" != "musebot" ] || ask="要綁哪段 Muse 對話?"
  local py="${T_PY3:-${EZ2_PY3:-python3}}" here="${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  local state_dir="${EZ2_COCKPIT_STATE_DIR:-${TMPDIR:-/tmp}}"; mkdir -p "$state_dir" 2>/dev/null
  local json list_err="" _lrc=0
  # 10-07 a seat:清單拿不到以前是 `|| return 0`,一個字都不印、直接沿用上次 —— 上次那條已不在時就是 23:1x 那三次
  #   「--check 失敗」。改成:印出為什麼拿不到,然後走下面同一套沿用規則(會查上次那條還在不在、被不被占)。
  if [ -n "${AIB_BOT_THREADS_JSON_FILE:-}" ]; then          # 尺的注入點:離線餵清單
    json="$(cat "$AIB_BOT_THREADS_JSON_FILE" 2>/dev/null)" || { _lrc=$?; json=""; list_err="注入檔讀不到"; }
  else
    json="$("$py" "$here/bot_threads.py" --provider "$provider" --json --limit "${AIB_BOT_THREAD_MENU_LIMIT:-6}" 2>"$state_dir/bot-threads-err-$$")" || { _lrc=$?; json=""; }
    [ "$_lrc" = "0" ] || list_err="bot_threads rc=$_lrc:$(head -c 160 "$state_dir/bot-threads-err-$$" 2>/dev/null | tr '\n' ' ')"
    rm -f "$state_dir/bot-threads-err-$$" 2>/dev/null
  fi
  if [ -n "$list_err" ]; then
    echo "aib: ${provider} 對話清單拿不到(${list_err});選單不跳,走沿用規則" >&2
  fi
  # 解析成「uuid<US>標題<US>列尾<US>占用」列;沒後端 ⇒ 不跳(grokbot/未來 AI 沒清單是常態)。清單失敗(json 空)⇒ rows 空但繼續走沿用規則。
  # 候選要排除「上次那條」:各家的預設變數不同(與下面 default_id 同一張對照)
  local _pick_exclude="${AIB_CODEXBOT_THREAD_ID:-}"
  [ "$provider" != "grokbot" ] || _pick_exclude="${AIB_GROKBOT_AGENT_ID:-}"
  [ "$provider" != "claudebot" ] || _pick_exclude="${AIB_CLAUDEBOT_SESSION_ID:-}"
  [ "$provider" != "musebot" ] || _pick_exclude="${AIB_MUSEBOT_MENU_DEFAULT:-}"   # P3:aib_musebot_preflight 先算好的沿用那段
  local rows; rows="$(printf '%s' "$json" | "$py" -c '
import sys, json, time
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
if not d.get("supported"): sys.exit(0)
rows = d.get("rows") or []
# 10-07:候選一條 —— 上次那條不在/被占時改綁它,不再沿用死 id。沒被占、不是預設;最近 AIB 綁過的優先,其次最新更新。
default_id = sys.argv[1] if len(sys.argv) > 1 else ""
def _i(v):
    try: return int(v or 0)
    except (TypeError, ValueError): return 0
cands = [r for r in rows if r.get("id") and r["id"] != default_id and not (r.get("held_by") or [])]
cands.sort(key=lambda r: (_i(r.get("last_bound")), _i(r.get("updated"))), reverse=True)
if cands:
    c = cands[0]
    if _i(c.get("last_bound")) > 0:
        why = "最近 AIB 綁過 " + time.strftime("%m-%d %H:%M", time.localtime(_i(c.get("last_bound"))))
    elif _i(c.get("updated")) > 0:
        why = "最新更新 " + time.strftime("%m-%d %H:%M", time.localtime(_i(c.get("updated"))))
    else:
        why = "未用過"
    name = (c.get("name") or "(無標題)").replace("\t", " ").replace("\n", " ").replace("\x1f", " ")[:34]
    print("__pick__\x1f%s\x1f%s\x1f%s" % (c["id"], name, why.replace("\x1f", " ")))
for r in rows:
    name = (r.get("name") or "(無標題)").replace("\t", " ").replace("\n", " ")[:34]
    # 列尾:「上次 MM-DD HH:MM · <bot> [誰綁著]」由 bot_threads 算好(r["tail"]),選單不自己拼第二份
    tail = (r.get("tail") or "")
    # ★ 09-26(a seat):列尾原本只有**上次綁定**時間,沒有**最後更新** ⇒ 全新空對話與「剛剛還在用、
    #   只是沒標題」的對話在選單上長得一模一樣(都是「(無標題) · codexbot」),而 aib bot list 的表分得出來。
    #   updated 是 0/缺 = 這條從來沒被動過 ⇒ 明寫「未用過」,不要留白讓人自己猜。
    try:
        upd = int(r.get("updated") or 0)
    except (TypeError, ValueError):
        upd = 0
    when = ("更新 " + time.strftime("%m-%d %H:%M", time.localtime(upd))) if upd > 0 else "未用過"
    tail = (when + " " + tail).strip()
    held = " ".join(r.get("held_by") or []).replace("\t", " ")   # P6:被另一隻活橋占著的列
    # ★ 分隔用 \x1f 不用 Tab:Tab 是 IFS 空白,連續兩個會被 read 合併 ⇒ tail 空的列,held 會被讀進 tail 欄。
    print("%s\x1f%s\x1f%s\x1f%s" % (r["id"], name.replace("\x1f", " "), tail.replace("\x1f", " "), held.replace("\x1f", " ")))
' "$_pick_exclude" 2>/dev/null)"
  # rows 空 且 清單沒失敗 = 沒後端/沒對話 ⇒ 不跳(舊行為);清單失敗 ⇒ 繼續走沿用規則(下面會查預設還在不在)
  if [ -z "$rows" ] && [ -z "$list_err" ]; then return 0; fi
  local choice_f="$state_dir/bot-thread-choice-$$"; rm -f "$choice_f" 2>/dev/null
  local default_id="${AIB_CODEXBOT_THREAD_ID:-}"
  [ "$provider" != "grokbot" ] || default_id="${AIB_GROKBOT_AGENT_ID:-}"
  [ "$provider" != "claudebot" ] || default_id="${AIB_CLAUDEBOT_SESSION_ID:-}"
  [ "$provider" != "musebot" ] || default_id="${AIB_MUSEBOT_MENU_DEFAULT:-}"
  # A4(09-24):預設對話被刪/歸檔時,「沿用上次」會綁到不存在的 thread(--check 才發現)。先問 server 它還在不在;
  #   不在 ⇒ 不給「沿用」,逾時=不開席(回 1,aib 端 die)。連不上(unknown)照舊沿用:量不到 ≠ 不在。
  local default_state="${AIB_BOT_THREAD_DEFAULT_STATE:-}"          # 尺的注入點:yes|no|unknown
  if [ -z "$default_state" ] && [ -n "$default_id" ]; then
    if [ "$provider" = "grokbot" ] || [ "$provider" = "claudebot" ] || [ "$provider" = "musebot" ]; then
      default_state="unknown"      # --exists 問的是 Codex server;grokbot 的名冊是快取、claudebot 沒有 server,量不到 ≠ 不在
    else
      default_state="$("$py" "$here/bot_threads.py" --exists "$default_id" 2>/dev/null)" || default_state="unknown"
    fi
  fi
  # P6(09-25,多隻 CodexBot):「沿用上次」那條如果正被另一隻活橋占著,第二隻逾時就會撞上它,
  #   窗開了、席登記了,才在橋啟動時死。所以預設被占 ⇒ 不給沿用,逾時=不開席(與 A4「預設已不在」同一條路)。
  #   ★ 占用資訊**先從清單拿**(清單每列已帶 held_by);清單裡沒有預設那條、且不是注入模式,才另外 probe。
  #     注入模式(AIB_BOT_THREADS_JSON_FILE)絕不去碰真狀態根——上線前實測:既有選單尺的預設 thread
  #     正是真機上一隻活 CodexBot 綁著的那條,probe 真狀態根會讓尺讀到真的鎖而變色。
  local default_held="" default_seen=0 pick_id="" pick_name="" pick_why=""
  local -a items=(); local n=0 line id name bound sh cmd held
  while IFS=$'\x1f' read -r id name tail held; do
    [ -n "$id" ] || continue
    if [ "$id" = "__pick__" ]; then pick_id="$name"; pick_name="$tail"; pick_why="$held"; continue; fi
    n=$((n+1)); [ "$n" -le 9 ] || break
    local tag=""; [ "$id" = "$default_id" ] && tag=" ★"; [ -n "$tail" ] && tag="$tag  $tail"
    if [ -n "$default_id" ] && [ "$id" = "$default_id" ]; then default_seen=1; default_held="$held"; fi
    if [ -n "$held" ]; then
      # tmux display-menu:名字以 - 開頭 = 停用(灰掉、選不到)。key 與指令留空 ⇒ 沒有任何鍵能觸發它。
      items+=("-$n. ${name}  [正被 ${held} 使用,不能選]" "" "")
      continue
    fi
    printf -v sh '[ -e %q ] || printf %%s %q > %q' "$choice_f" "$id" "$choice_f"
    printf -v cmd 'run-shell -b %q' "$sh"
    items+=("$n. ${name}${tag}" "$n" "$cmd")
  done <<< "$rows"
  local limit="${AIB_BOT_THREAD_MENU_TIMEOUT_S:-30}"
  printf -v sh '[ -e %q ] || printf %%s %q > %q' "$choice_f" "__default__" "$choice_f"
  printf -v cmd 'run-shell -b %q' "$sh"
  if [ -n "$default_id" ] && [ "$default_seen" = "0" ] && [ -z "${AIB_BOT_THREADS_JSON_FILE:-}" ]; then
    local _h_rc=0 _h_out=""
    _h_out="$("$py" "$here/bot_threads.py" --provider "$provider" --held "$default_id" 2>/dev/null)" || _h_rc=$?
    [ "$_h_rc" = "3" ] && default_held="${_h_out#held }"
  fi
  local title_tail="${limit} 秒沒選=上次綁定"
  if [ "$provider" = "claudebot" ]; then
    # v0.70.0 #15(the maintainer 09-27 10:43):右鍵開的 claudebot 席逾時沿用上次 ⇒ 00216 綁到別人的測試對話。
    #   claudebot 的「上次」常是另一席正在用或別人的對話,逾時一律**不綁**並印警告;要綁就點一條。
    # 10-07 a seat:「0」走 _aib_bot_thread_fallback(不綁)之後,preflight 會一鍵代開一條對話並綁上 ⇒ 字照實寫。
    #   另加「b. 先開席、之後再綁」(the maintainer 10-07「開席在本 AIB 裡、然後再綁」):不代開,席上打 /bind 再綁(sand_bridge)。
    if [ "${AIB_CLAUDEBOT_AUTOSPAWN:-1}" != "0" ]; then
      items+=("0. AIB 代開一條新對話並綁上(預設;${limit} 秒沒選也是這個)" "0" "$cmd")
      title_tail="${limit} 秒沒選=代開新對話"
    else
      items+=("0. 不綁(預設;${limit} 秒沒選也是這個)" "0" "$cmd")
      title_tail="${limit} 秒沒選=不綁"
    fi
    local later_sh later_cmd
    printf -v later_sh '[ -e %q ] || printf %%s %q > %q' "$choice_f" "__later__" "$choice_f"
    printf -v later_cmd 'run-shell -b %q' "$later_sh"
    items+=("b. 先開席,之後在席上打 /bind 再綁" "b" "$later_cmd")
  elif [ "$default_state" = "no" ]; then
    # 10-07:有沒人用的候選 ⇒ 逾時改綁它(選單上就說清楚會綁誰);沒有才「不開席」
    if [ -n "$pick_id" ]; then
      items+=("0. 上次綁的對話已不在,改綁「${pick_name}」(${limit} 秒沒選也是這個)" "0" "$cmd")
      title_tail="${limit} 秒沒選=改綁「${pick_name}」"
    else
      items+=("0. 上次綁的對話已不在,不能沿用(${limit} 秒沒選=不開席)" "0" "$cmd")
      title_tail="${limit} 秒沒選=不開席"
    fi
  elif [ -n "$default_held" ]; then
    if [ -n "$pick_id" ]; then
      items+=("0. 上次那條正被 ${default_held} 使用,改綁「${pick_name}」(${limit} 秒沒選也是這個)" "0" "$cmd")
      title_tail="${limit} 秒沒選=改綁「${pick_name}」"
    else
      items+=("0. 上次那條正被 ${default_held} 使用,不能沿用(${limit} 秒沒選=不開席)" "0" "$cmd")
      title_tail="${limit} 秒沒選=不開席"
    fi
  else
    items+=("0. 沿用上次綁定(預設;${limit} 秒沒選也是這個)" "0" "$cmd")
  fi
  if [ "$provider" = "grokbot" ]; then
    # P4(10-11 a seat):名冊是 app 的快取,剛建的 agent 要在 app 點一下才寫進來 ⇒ 選單上直接講(停用列,點不到)
    items+=("-剛建的 agent 不在這裡:先在 Grok Bot app 點它一下,再右鍵重開" "" "")
  fi
  if [ "$provider" = "musebot" ]; then
    # P3(10-11 a seat):Muse 沒有本機名冊,新的一段列不出來 ⇒ 給「先開席」,席上 /bind <id> 綁(P1 起不用重開)
    local m_later_sh m_later_cmd
    printf -v m_later_sh '[ -e %q ] || printf %%s %q > %q' "$choice_f" "__later__" "$choice_f"
    printf -v m_later_cmd 'run-shell -b %q' "$m_later_sh"
    items+=("b. 綁新的一段:先開席,之後在席上打 /bind <Muse 對話 id>" "b" "$m_later_cmd")
  fi
  if [ -z "$rows" ]; then
    _aib_bot_thread_fallback "$provider" "清單拿不到"; return $?
  fi
  local target="${AIB_BOT_THREAD_MENU_TARGET:-${EZ2_TMUX_SESSION_NAME:-aihub}}" sock="${HUB:-${EZ2_AIHUB_SOCKET:-aihub}}"
  local menu_err="$state_dir/bot-thread-menu-err-$$"
  # 10-09 a seat(D6):選項後面一定要有 `--`。停用列的名字以 `-` 開頭(上面 P6 那段),它若排第一個,
  #   tmux 會把 `-1. …` 當成旗標 ⇒ `command display-menu: unknown flag -1`,整個選單出不來、走沿用規則。
  #   10-09 02:0x 真機實踩(grokbot 席,tmux 3.7c);一次性私有伺服器實驗:不加 rc=1 unknown flag,加了解析通過。
  env -u TMUX "${AIB_TMUX_BIN:-tmux}" -L "$sock" display-menu -t "$target" \
      -T " ${provider}:${ask}(${title_tail}) " -x C -y C -- "${items[@]}" 2>"$menu_err" &
  local mpid=$!; sleep 0.5
  if ! kill -0 "$mpid" 2>/dev/null; then
    _menu_rc=0; wait "$mpid" 2>/dev/null || _menu_rc=$?
    if [ "$_menu_rc" != "0" ]; then
      if [ "$provider" = "claudebot" ]; then
        # A 包 r1 低(a seat):選單跳不出來時 claudebot 也照「不綁」走,不再同時印「沿用上次」與「沒綁」
        unset AIB_CLAUDEBOT_SESSION_ID AIB_CLAUDEBOT_SESSION_OVERRIDE
        rm -f "$menu_err" 2>/dev/null
        _aib_claudebot_nopick_note "對話選單跳不出來"; return 0
      fi
      # 10-07:以前這裡印一句就 `return 0` 沿用上次,**跳過**了下面「上次那條還在不在/被不被占」的檢查 ——
      #   23:1x 三次「沿用死 id → --check 失敗」就是走這條。現在印出為什麼跳不出來(socket、目標、tmux 的話),再走同一套沿用規則。
      echo "aib: 對話選單跳不出來(tmux -L ${sock} display-menu -t ${target} rc=${_menu_rc}:$(head -c 160 "$menu_err" 2>/dev/null | tr '\n' ' '));走沿用規則" >&2
      rm -f "$menu_err" 2>/dev/null
      _aib_bot_thread_fallback "$provider" "選單跳不出來"; return $?
    fi
  fi
  rm -f "$menu_err" 2>/dev/null
  local waited=0 choice="" _menu_rc=0
  while [ "$waited" -lt "$((limit * 5))" ]; do
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
  case "$choice" in
    ""|__default__)
       _aib_bot_thread_fallback "$provider" "${limit} 秒沒選" || return $? ;;
    __later__)
       if [ "$provider" = "musebot" ]; then
         AIB_MUSEBOT_BIND_LATER=1
         echo "aib: musebot 先開席不綁(選單選的);席上打 /bind 列出綁過的、/bind <Muse 對話 id> 綁新的一段" >&2
       else
         unset AIB_CLAUDEBOT_SESSION_ID AIB_CLAUDEBOT_SESSION_OVERRIDE
         AIB_CLAUDEBOT_BIND_LATER=1
         echo "aib: claudebot 先開席不綁(選單選的);席上打 /bind 列出能綁的對話,/bind new 讓 AIB 代開一條" >&2
       fi ;;
    *) if [ "$provider" = "musebot" ]; then
         AIB_MUSEBOT_PICKED=1
         export AIB_MUSEBOT_CONVERSATION_ID="$choice"
       elif [ "$provider" = "grokbot" ]; then
         AIB_GROKBOT_AGENT_OVERRIDE="$choice"
         export AIB_GROKBOT_AGENT_ID="$choice"
       elif [ "$provider" = "claudebot" ]; then
         AIB_CLAUDEBOT_SESSION_OVERRIDE="$choice"
         export AIB_CLAUDEBOT_SESSION_ID="$choice"
       else
         AIB_CODEXBOT_THREAD_OVERRIDE="$choice"
         export AIB_CODEXBOT_THREAD_ID="$choice" AIB_CODEXAPP_THREAD_ID="$choice" CODEX_THREAD_ID="$choice"
       fi
       echo "aib: ${provider} 這一席綁 $choice(選單選的)" >&2 ;;
  esac
  return 0
}


# 10-09 a seat:claudebot 沒選到對話時那一句要講實話。以前一律印「這一席不綁(訊息只進信箱,沒有人收)」,
#   但預設(AIB_CLAUDEBOT_AUTOSPAWN 沒關、不是試跑)下一步 aib_claudebot_session_preflight 會代開一條新對話並綁上
#   ⇒ 同一次開席前一行說沒綁、下一行說已綁。判準與 preflight 代開那一行同一組條件。
_aib_claudebot_nopick_note() {
  local why="$1"
  if [ "${AIB_CLAUDEBOT_AUTOSPAWN:-1}" != "0" ] && [ "${DRY:-0}" != "1" ] && [ "${AIB_DRY_RUN:-0}" != "1" ]; then
    echo "aib: claudebot ${why} ⇒ 沒選對話,交給 AIB 代開一條新的本機對話並綁上(不要代開:AIB_CLAUDEBOT_AUTOSPAWN=0)" >&2
  else
    echo "aib: claudebot ${why} ⇒ 這一席不綁(訊息只進信箱,沒有人收);要綁:席上打 /bind,或 AIB_CLAUDEBOT_SESSION_ID=<uuid> aib claudebot …" >&2
  fi
}


# 10-07 a seat:三條「沒人選」的路(清單拿不到、選單跳不出來、逾時/選 0)共用的沿用規則。
#   讀呼叫端的區域變數:provider 之外還有 default_id / default_state / default_held / pick_id / pick_name / pick_why。
#   規則:claudebot 一律不綁(v0.70.0 #15);上次那條還在且沒被占 ⇒ 沿用;不在或被占 ⇒ 有候選就改綁候選並明說,
#   沒候選 ⇒ 不開席並說明(回 1)。以前只有逾時那條路有這套檢查,清單失敗與選單失敗兩條路直接沿用 —— 死 id 就是這樣進來的。
_aib_bot_thread_fallback() {
  local provider="$1" reason="${2:-}"
  if [ "$provider" = "claudebot" ]; then
    unset AIB_CLAUDEBOT_SESSION_ID AIB_CLAUDEBOT_SESSION_OVERRIDE
    _aib_claudebot_nopick_note "${reason:-沒選對話}"
    return 0
  fi
  # P3(10-11 a seat):musebot 沒選 ⇒ 回去走 aib_musebot_preflight 原本的沿用規則(pick_default:新到舊第一段沒被占的;都被占不開席)
  [ "$provider" != "musebot" ] || return 0
  local why=""
  if [ "${default_state:-}" = "no" ]; then
    why="上次綁的對話 ${default_id:-(無)} 已不在 server 上(被刪或歸檔)"
  elif [ -n "${default_held:-}" ]; then
    why="上次綁的對話 ${default_id:-(無)} 正被 ${default_held} 使用中(一條桌面對話同時只能接一隻)"
  fi
  if [ -n "$why" ]; then
    if [ -n "${pick_id:-}" ]; then
      if [ "$provider" = "grokbot" ]; then
        AIB_GROKBOT_AGENT_OVERRIDE="$pick_id"; export AIB_GROKBOT_AGENT_ID="$pick_id"
      else
        AIB_CODEXBOT_THREAD_OVERRIDE="$pick_id"
        export AIB_CODEXBOT_THREAD_ID="$pick_id" AIB_CODEXAPP_THREAD_ID="$pick_id" CODEX_THREAD_ID="$pick_id"
      fi
      echo "aib: ${provider} ${why};${reason},改綁「${pick_name}」${pick_id}(${pick_why})。要換一條:右鍵重開並在選單點。" >&2
      return 0
    fi
    echo "aib: ${provider} ${why},${reason},也沒有別條可綁;不開席。用 aib bot list 看清單後重開並選一條。" >&2
    return 1
  fi
  echo "aib: ${provider} 沿用上次綁定的對話 ${default_id:-(無)}(${reason})" >&2
  return 0
}


# P6(09-25,多隻 CodexBot):開席前最後一道——這條對話是不是已被另一隻活橋占著。
#   選單那邊只是讓人看得到、選不到;**--thread 直接指定的路不經過選單**,所以真正的關卡在這裡,aib 在 --check 前呼叫。
#   判準委給 bot_threads.py --held(= 橋自己那把 flock),不在殼裡另寫一份。
#   回 0=可開 / 1=被占(已印原因)。量不到 ⇒ 印一行照開(量不到 ≠ 被占;真被占的話橋啟動時仍會擋)。
aib_bot_thread_held_guard() {
  local provider="$1" tid="${AIB_CODEXBOT_THREAD_ID:-}"
  [ "$provider" != "grokbot" ] || tid="${AIB_GROKBOT_AGENT_ID:-}"
  [ "$provider" != "claudebot" ] || tid="${AIB_CLAUDEBOT_SESSION_ID:-}"
  [ "$provider" != "musebot" ] || tid="${AIB_MUSEBOT_CONVERSATION_ID:-}"
  { [ "$provider" = "codexbot" ] || [ "$provider" = "grokbot" ] || [ "$provider" = "claudebot" ] || [ "$provider" = "musebot" ]; } && [ -n "$tid" ] || return 0
  local py="${T_PY3:-${EZ2_PY3:-python3}}" here="${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  local out="" rc=0
  out="$("$py" "$here/bot_threads.py" --provider "$provider" --held "$tid" 2>/dev/null)" || rc=$?
  case "$rc" in
    0) return 0 ;;
    3) if [ "$provider" = "grokbot" ]; then
         echo "aib: grokbot 這個 agent ${tid} 正被 ${out#held } 使用中(一個 agent 同時只能接一席)。要多開一席請在選單選另一個 agent。" >&2
         return 1
       fi
       if [ "$provider" = "claudebot" ]; then
         echo "aib: claudebot 這條 Claude 對話 ${tid} 正被 ${out#held } 使用中(一條對話同時只能接一席)。要多開一席請在選單選另一條。" >&2
         return 1
       fi
       if [ "$provider" = "musebot" ]; then
         echo "aib: musebot 這段 Muse 對話 ${tid} 正被 ${out#held } 使用中(一段對話同時只能接一席);不開席。要多開一席請帶另一段:AIB_MUSEBOT_CONVERSATION_ID=<另一段 id>。" >&2
         return 1
       fi
       echo "aib: ${provider} 這條對話 ${tid} 正被 ${out#held } 使用中(一條桌面對話同時只能接一隻)。多開一隻要綁另一條:aib bot list 看清單,重開時選一條沒人用的,或 --thread <另一條 UUID>。" >&2
       return 1 ;;
    *) echo "aib: 量不到 ${tid} 有沒有被占用(${out:-bot_threads rc=$rc});照開,若真被占,橋啟動時會擋。" >&2
       return 0 ;;
  esac
}


# 09-25 a seat(a seat 派):aib 開 grokbot 席前的整段——預設 agent、30 秒選單、占用關卡、講清楚綁了誰。
#   預設=環境已帶的 AIB_GROKBOT_AGENT_ID;沒帶且恰好一個 agent 綁過 webhook(aib bot connect grokbot)就用它;兩個以上不猜。
#   回 0=可開(可能沒綁 agent,已印一句);1=那個 agent 被占用(已印原因)。
aib_grokbot_agent_preflight() {
  local py="${T_PY3:-${EZ2_PY3:-python3}}" here="${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  if [ -z "${AIB_GROKBOT_AGENT_ID:-}" ]; then
    local only=""
    only="$("$py" "$here/grokbot_threads.py" connected 2>/dev/null \
      | "$py" -c 'import json,sys; c=json.load(sys.stdin).get("connected") or []; print(c[0] if len(c) == 1 else "")' 2>/dev/null || true)"
    [ -z "$only" ] || export AIB_GROKBOT_AGENT_ID="$only"
  fi
  aib_bot_thread_menu grokbot || return 1
  aib_bot_thread_held_guard grokbot || return 1
  # P4(10-11 a seat,the maintainer「最少步驟串成 bot」):選到的 agent 還沒接門鈴 ⇒ 開席前直接跳貼兩格的視窗
  #   (同 aib bot connect grokbot:切到 app 例行任務頁、請 bot 建例行任務那句已放剪貼簿)。
  #   取消或失敗不擋開席:席照開,席上打 /connect 再接。試跑、AIB_GROKBOT_AUTO_CONNECT=0 不跳。
  if [ -n "${AIB_GROKBOT_AGENT_ID:-}" ] && [ "${DRY:-0}" != "1" ] && [ "${AIB_DRY_RUN:-0}" != "1" ] \
     && [ "${AIB_GROKBOT_AUTO_CONNECT:-1}" != "0" ]; then
    local _gb_conn=""
    _gb_conn="$("$py" "$here/grokbot_threads.py" connected 2>/dev/null)" || _gb_conn=""
    case "$_gb_conn" in
      *"\"${AIB_GROKBOT_AGENT_ID}\""*) ;;
      "") ;;   # 量不到 ≠ 沒接:不跳
      *)
        echo "aib: grokbot agent ${AIB_GROKBOT_AGENT_ID} 還沒接門鈴 ⇒ 跳視窗讓你貼「POST 到」與「金鑰」(請 bot 建例行任務那句已放剪貼簿)" >&2
        if "$py" "$here/grokbot_threads.py" connect --agent "$AIB_GROKBOT_AGENT_ID" >&2; then
          echo "aib: grokbot 門鈴接好了" >&2
        else
          echo "aib: grokbot 門鈴沒接上(取消或貼錯);席照開,之後在席上打 /connect 再接" >&2
        fi ;;
    esac
  fi
  if [ -z "${AIB_GROKBOT_AGENT_ID:-}" ]; then
    echo "aib: grokbot 這一席沒有綁 Grok Bot agent(沒選,也沒有唯一一個綁過 webhook 的);席照開,但 bot 收不到這一席的信。綁法:席上打 /bind 列出 agent、/bind <編號> 綁上(不用重開);agent 還沒接門鈴要先 aib bot connect grokbot --agent <agentId>。" >&2
  else
    echo "aib: grokbot 這一席綁 agent ${AIB_GROKBOT_AGENT_ID}" >&2
  fi
  return 0
}


# 09-26 ClaudeBot(docs/CLAUDEBOT-DESKTOP.md):aib 開 claudebot 席前的整段——30 秒選單(本機 Claude 對話)、占用關卡、講清楚綁了誰。
#   預設=環境已帶的 AIB_CLAUDEBOT_SESSION_ID;沒有就不猜(本機對話很多,猜錯會把別條對話接進 AIB)。
#   回 0=可開(可能沒綁,已印一句);1=那條對話被占用(已印原因)。
aib_claudebot_session_preflight() {
  if [ -n "${AIB_CLAUDEBOT_SESSION_ID:-}" ] && ! [[ "$AIB_CLAUDEBOT_SESSION_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    echo "aib: AIB_CLAUDEBOT_SESSION_ID 不是完整小寫 UUID(${AIB_CLAUDEBOT_SESSION_ID}),不開席。" >&2
    return 1
  fi
  aib_bot_thread_menu claudebot || return 1
  aib_bot_thread_held_guard claudebot || return 1
  # P5(10-11 a seat,the maintainer 點頭):全域沒裝 ClaudeBot hook(或裝了會壞)⇒ 自動 install(先備份 ~/.claude/settings.json)。
  #   要在代開對話之前:hook 是 Claude 對話啟動時載入的,先開對話再裝,那條要重開才吃得到。失敗不擋開席(橋開起來會再標「⚠沒裝hook」)。
  #   試跑不動;AIB_CLAUDEBOT_AUTO_HOOK=0 關掉。
  if [ "${DRY:-0}" != "1" ] && [ "${AIB_DRY_RUN:-0}" != "1" ]; then
    "${EZ2_PY3:-python3}" "${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/bot_ready.py" ensure-claude-hook || true
  fi
  # 10-07 the maintainer 令「把 claudebot 整合進來」(一鍵開席自帶對話):沒選到對話 ⇒ AIB 自己在 tmux -L claudebot 開一條本機 claude
  #   對話並綁上(src/cockpit/claudebot_spawn.py;背景等橋綁好再替你送第一句,門鈴才會掛上)。
  #   AIB_CLAUDEBOT_AUTOSPAWN=0 關掉(回到「沒選就不綁」);試跑(--dry-run)不開。對話開在 AIB 倉庫根(已受信任,不卡信任提示)。
  #   10-07:選單選了「b. 先開席,之後再綁」(AIB_CLAUDEBOT_BIND_LATER=1)⇒ 不代開。
  if [ -z "${AIB_CLAUDEBOT_SESSION_ID:-}" ] && [ "${AIB_CLAUDEBOT_BIND_LATER:-0}" != "1" ] && [ "${AIB_CLAUDEBOT_AUTOSPAWN:-1}" != "0" ] && [ "${DRY:-0}" != "1" ] && [ "${AIB_DRY_RUN:-0}" != "1" ]; then
    local _cb_here _cb_sid
    _cb_here="${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
    if _cb_sid="$("${EZ2_PY3:-python3}" "$_cb_here/claudebot_spawn.py" start --cwd "${AIB_CLAUDEBOT_CWD:-${EZ2_AIB_ROOT:-$PWD}}")" \
        && [[ "$_cb_sid" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
      export AIB_CLAUDEBOT_SESSION_ID="$_cb_sid"
      # 10-09 a seat:講清楚用哪個帳號 —— Claude app 登入別的帳號時,app 裡本來就看不到這條(代開的也不會列在 app 裡)
      local _cb_acct; _cb_acct="$("${EZ2_PY3:-python3}" "$_cb_here/claudebot_threads.py" cli-account 2>/dev/null)" || _cb_acct=""
      echo "aib: claudebot 沒選對話 ⇒ AIB 已代開一條本機 claude 對話 ${_cb_sid} 並綁上(終端機 claude 帳號:${_cb_acct:-讀不到});要進去打字:tmux -L claudebot attach -t cb-${_cb_sid:0:8}(離開按 Ctrl-b d)。這條不會出現在 Claude app 裡;要在 app 裡看,請在 app 的 Code 頁開對話再綁(docs/CLAUDEBOT-DESKTOP.md)" >&2
    fi
  fi
  if [ -z "${AIB_CLAUDEBOT_SESSION_ID:-}" ]; then
    echo "aib: claudebot 這一席沒有綁 Claude 對話(沒選);席照開,但訊息只會進信箱。綁法:席上打 /bind(不用重開),或 AIB_CLAUDEBOT_SESSION_ID=<本機 Claude session UUID> 再重開。" >&2
  else
    echo "aib: claudebot 這一席綁 Claude 對話 ${AIB_CLAUDEBOT_SESSION_ID}" >&2
  fi
  return 0
}


# 2026-10-05 MuseBot(docs/MUSEBOT-DESKTOP.md):aib 開 musebot 席前的整段——
#   預設=環境已帶的 AIB_MUSEBOT_CONVERSATION_ID;沒有就不猜(Muse 對話 id 不透明,形狀由 Muse 側定義)。
#   沒有 30 秒選單(bot_threads 尚無 musebot 後端,見 UNSUPPORTED 註記)。回 0=可開(可能沒綁,已印一句)。
aib_musebot_preflight() {
  local conv="${AIB_MUSEBOT_CONVERSATION_ID:-}"
  # 10-09 a seat(D8):沒帶 id ⇒ 預設沿用綁過的 Muse 對話。
  #   沒帶就開成「沒綁」的席,右欄是綠的、Muse 側看守找不到它,寄來的信永遠沒人讀(10-09 a seat/a seat 實量)。
  # 10-09 a seat(the maintainer 採「四家同一套」,同 codexbot/grokbot 的 _aib_bot_thread_fallback):
  #   從新到舊挑第一段沒被占的(musebot_threads.pick_default),並講清楚綁了哪段、跳過了誰占的;
  #   綁過的每一段都被占 ⇒ 不開席(回 1)。以前只看最新一段、它被占就「照開不綁」—— 又是一席沒人收信。
  #   從沒綁過 ⇒ 照開不綁(第一隻 musebot 席要開得起來;the maintainer 未裁前的預設 A)。AIB_MUSEBOT_NO_DEFAULT=1 關掉沿用。
  if [ -z "$conv" ] && [ "${AIB_MUSEBOT_NO_DEFAULT:-0}" != "1" ] && [ "${DRY:-0}" != "1" ]; then
    local _pk _pk_conv _pk_hist _pk_skip _mb_here
    _mb_here="${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
    _pk="$("${T_PY3:-${EZ2_PY3:-python3}}" "${_mb_here}/musebot_threads.py" pick-default 2>/dev/null)" || _pk=""
    IFS=$'\x1f' read -r _pk_conv _pk_hist _pk_skip <<< "$_pk"
    # P3(10-11 a seat,the maintainer「最少步驟串成 bot」):綁過、而且有沒被占的 ⇒ 跟另三家一樣跳選單讓人點;
    #   沒選(逾時/選 0/選單跳不出來)⇒ 照下面原本的沿用規則。全部被占 ⇒ 不跳(沒得選),照原本說明並不開席。
    if [ -n "$_pk_conv" ]; then
      AIB_MUSEBOT_MENU_DEFAULT="$_pk_conv" AIB_MUSEBOT_PICKED=0 AIB_MUSEBOT_BIND_LATER=0
      aib_bot_thread_menu musebot || return 1
      unset AIB_MUSEBOT_MENU_DEFAULT
      if [ "${AIB_MUSEBOT_BIND_LATER:-0}" = "1" ]; then
        unset AIB_MUSEBOT_CONVERSATION_ID
        return 0
      fi
      if [ "${AIB_MUSEBOT_PICKED:-0}" = "1" ] && [ -n "${AIB_MUSEBOT_CONVERSATION_ID:-}" ]; then
        conv="$AIB_MUSEBOT_CONVERSATION_ID"
        _pk_conv=""; _pk_hist=0     # 選了就不再印沿用那幾句
      fi
    fi
    if [ -n "$_pk_conv" ]; then
      conv="$_pk_conv"
      export AIB_MUSEBOT_CONVERSATION_ID="$conv"
      if [ -n "$_pk_skip" ]; then
        echo "aib: musebot 沒帶對話編號;較新的幾段正被占著(${_pk_skip}),改綁沒人用的 Muse 對話 ${conv}(不要沿用:AIB_MUSEBOT_NO_DEFAULT=1)" >&2
      else
        echo "aib: musebot 沒帶對話編號 ⇒ 沿用上一次綁的 Muse 對話 ${conv}(不要沿用:AIB_MUSEBOT_NO_DEFAULT=1)" >&2
      fi
    elif [ "${_pk_hist:-0}" != "0" ] && [ -n "$_pk_hist" ]; then
      echo "aib: musebot 綁過的 ${_pk_hist} 段 Muse 對話都正被別席使用(${_pk_skip:-?});一段對話只准一席,開了也沒人收信 ⇒ 不開席。要多開一席請帶一段新的:AIB_MUSEBOT_CONVERSATION_ID=<Muse 對話 id>。" >&2
      return 1
    fi
  fi
  if [ -z "$conv" ]; then
    echo "aib: musebot 這一席沒有綁 Muse 對話(AIB_MUSEBOT_CONVERSATION_ID 沒帶,也沒有綁過的可沿用);席照開,訊息進信箱等排程喚醒 —— Muse 側看守若只認綁定,收不到這一席的信。綁法:席上打 /bind <Muse 對話 id>(不用重開)。" >&2
  else
    # 明帶的 id 也要過占用關卡:以前只有沿用那條避開了被占的,明帶被占的 ⇒ 窗開了、席登記了,橋搶不到鎖才 rc=3 死
    aib_bot_thread_held_guard musebot || return 1
    echo "aib: musebot 這一席綁 Muse 對話 ${conv}" >&2
  fi
  return 0
}
