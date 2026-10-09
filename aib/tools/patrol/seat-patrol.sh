#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 席位巡查器。★ 它必須住在版控裡 —— 前一版住在 session 的 scratchpad,
# session 一沒腳本就不見,而交接單只寫「要重新武裝」沒寫怎麼武裝(FIX-018)。
#
# 武裝(Claude Code):
#   Monitor(command="EZ2_AIHUB_SOCKET=<socket> AIB_PATROL_WATCH=\"@11:codex:S1 @10:grok:S7\" \
#                    bash tools/patrol/seat-patrol.sh", persistent=true)
# 單跑一輪自檢:把最後的 sleep 換成 break。
#
# ★ 武裝前要證明它有分辨力(規格 §8.2):對照組(讀得到真資料)+ 三把正向刀 + 必落空端。
#   靜默不等於正常 —— 讀不到畫面的巡查器與「兩席都很好」的巡查器同形。
# 席位巡查:只吐「我會據以行動」的事件。靜默不等於順利,所以每一種終止態都要有樣式。
TOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROUND=0
SOCK="${EZ2_AIHUB_SOCKET:?需要 EZ2_AIHUB_SOCKET,例 aihub-20260908-103133-a801}"
declare -A LASTSIG STUCK PEND WALL WASBUSY
HB_ROWS=""
mkdir -p "${HOME}/.ez2-aib/state"
WATCH="${AIB_PATROL_WATCH:?需要 AIB_PATROL_WATCH,格式 @窗:名字:包 以空白分隔,例 \"@11:codex-pm:S1 @10:grok:S7\"}"
while true; do
  for spec in $WATCH; do
    W="${spec%%:*}"; rest="${spec#*:}"; NAME="${rest%%:*}"; PKG="${rest##*:}"
    info=$(tmux -L "$SOCK" list-panes -t "$W" -F '#{pane_dead}' 2>/dev/null)
    if [ -z "$info" ]; then echo "[$NAME $PKG] 窗不見了($W)—— 席位消失,派工中斷"; continue; fi
    [ "$info" = "1" ] && { echo "[$NAME $PKG] pane 已死($W)—— 需要重開席"; continue; }
    scr=$(tmux -L "$SOCK" capture-pane -p -t "$W" 2>/dev/null | tail -40)
    # ★★ 讀不到畫面必須與「畫面上很正常」分得開:capture 成功但空字串,
    #   在底下每一條樣式檢查上都表現成「什麼都沒命中」= 全部安靜 = 看起來很好。
    if [ -z "$scr" ]; then
      echo "[$NAME $PKG] ★ 讀不到畫面($W)—— 這不是「沒事」,是量不到"; continue; fi
    # (1) 卡在確認/權限彈窗 —— copilot 就是這樣卡住而外表像在跑
    if echo "$scr" | grep -qE "Do you want to allow|Allow directory access|approve this|\[y/N\]|1\. Yes"; then
      echo "[$NAME $PKG] 卡在確認彈窗,需要人按 —— 它不會自己過"; fi
    # (2) 輸入框有未送出的東西 = 疊稿風險
    #   ★ 要連續兩輪都看到才叫:貼上與 Enter 之間有一瞬間會長成這樣,
    #     單輪就叫會在每一次 dm 之後誤報一次(2026-09-09 04:2x 實際發生)。
    #   ★★ 只看最後 8 行:輸入框永遠在底部,而 codex 把**已送出**的訊息也用 `›` 顯示在
    #      歷史裡 —— 看 40 行會把歷史撈成「未送出」。對 copilot 有效、對 codex 無效,
    #      而它報的是同一句話(2026-09-09 04:0x 連兩次誤報才找到)。
    if echo "$scr" | tail -8 | grep -qE "^\s*[❯›]\s+\[@00000"; then
      PEND[$W]=$(( ${PEND[$W]:-0} + 1 ))
      [ "${PEND[$W]}" -ge 2 ] && echo "[$NAME $PKG] 輸入框連兩輪有未送出的訊息 —— 補 Enter,不要重貼"
    else PEND[$W]=0; fi
    # (3) 額度/登入牆
    #   ★★ FIX-020(2026-09-09 05:0x 實際誤報):舊樣式 `rate.?limit|quota|usage limit` 命中的是
    #      **被巡查席正在調查的資料**——@11 的包 W1 就是「用量准入閘」,它印出的 JSON 裡有
    #      `rate_limit_reached_type: null`(值是 null = 沒撞牆)而偵測器照樣開火。
    #      ⇒ 對 W1 這一包,舊樣式結構上恆真、零分辨力;而且它沒有栓,會每 60 秒叫一次。
    #   修法三件:(a) 只收**有空格的整句話**,不收識別字片段;(b) 只看底部 12 行的活動區,
    #      不看整段 scrollback(與 (2) 同一個判例);(c) 加栓,只在**轉態**時叫一次。
    #   ★ 誠實邊界:(b) 會讓「牆的訊息已被後續輸出捲離底部 12 行」的情況漏報;
    #      那一種靠 (4) 靜止偵測接住(撞牆的席不會再有活動指標)。
    if echo "$scr" | tail -12 | grep -qE "hit your usage limit|usage limit reached|Not logged in|rate limit reached|quota exceeded|Please (log|sign) in"; then
      if [ "${WALL[$W]:-0}" = "0" ]; then
        WALL[$W]=1
        echo "[$NAME $PKG] 撞到額度或登入牆(轉態,只叫這一次)"; fi
    else WALL[$W]=0; fi
    # (4) 靜止:畫面指紋連續 N 輪不變且沒有活動指標
    sig=$(echo "$scr" | md5 -q 2>/dev/null || echo "$scr" | md5sum | cut -d' ' -f1)
    busy=$(echo "$scr" | grep -cE "Working|Thinking|Responding|esc to interrupt|esc interrupt")
    if [ "$sig" = "${LASTSIG[$W]}" ] && [ "$busy" = "0" ]; then
      STUCK[$W]=$(( ${STUCK[$W]:-0} + 1 ))
      [ "${STUCK[$W]}" = "20" ] && echo "[$NAME $PKG] 靜止約 20 分鐘且無活動指標 —— 已排除「交件等裁定」那一段,這個要看"
    else STUCK[$W]=0; fi
    LASTSIG[$W]="$sig"
    # ── (6) ★ 心跳:把「有在工作」的**正向證據**寫下來,不只在出事時叫 ──
    #   舊版只報異常 ⇒ 靜默同時是「一切正常」「兩席都在睡」「巡查器自己死了」。
    #   三者處置完全不同,而它們在事件流上逐字相同(= 沒有事件)。
    #   ⇒ 每輪落一份可查的快照;要問「他們現在在做事嗎」不必再去 capture。
    HB_ROWS="${HB_ROWS}${HB_ROWS:+,}$(printf '{"win":"%s","name":"%s","pkg":"%s","busy":%s,"same_screen_rounds":%s,"at":"%s"}' \
      "$W" "$NAME" "$PKG" "$([ "$busy" -gt 0 ] && echo true || echo false)" "${STUCK[$W]:-0}" "$(date -u +%FT%TZ)")"
    # ★ 由靜到動也值得叫一次:它證明我送的東西真的被拿去做了,
    #   而那是「投遞成功」之外的第二個獨立證據(收據只證明按鍵落進輸入框)。
    if [ "${WASBUSY[$W]:-x}" = "false" ] && [ "$busy" -gt 0 ]; then
      echo "[$NAME $PKG] 開始動了(靜→動)—— 上一則指令被接走了"; fi
    WASBUSY[$W]=$([ "$busy" -gt 0 ] && echo true || echo false)
  done
  # ── (7) ★ 週期顯示待真人決策(the maintainer 13:5x)──
  #   ★ 為什麼要週期而不是只在被問的時候答:**安靜地等,與沒有東西在等,長得一樣。**
  #     今天「43 顆合不合入」那幾格,是他問了我才講的。
  #   ★ 兩個開火條件,因為它們回答不同的問題:
  #     (a) 每 PERIOD 輪報一次現況 —— 回答「現在有幾件在等」
  #     (b) 有一件**剛跨過基線** —— 回答「有一件等太久了」,這個不等週期
  ROUND=$(( ${ROUND:-0} + 1 ))
  PD_JSON=$(python3 -B "${TOP:-.}/tools/dispatch/pending-decisions.py" list --json 2>/dev/null)
  if [ -n "$PD_JSON" ]; then
    PD_N=$(printf '%s' "$PD_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["open"])' 2>/dev/null || echo "")
    PD_OVER=$(printf '%s' "$PD_JSON" | python3 -c 'import sys,json;print(sum(1 for i in json.load(sys.stdin)["items"] if i["over_baseline"]))' 2>/dev/null || echo "")
    # ★ 讀不出來要說出口:空字串與 0 在下面的比較裡會同形。
    if [ -z "$PD_N" ] || [ -z "$PD_OVER" ]; then
      echo "[待決] 登記簿讀不出來 —— 這不是「沒有人在等」"
    else
      if [ "$PD_OVER" -gt "${PD_OVER_LAST:-0}" ]; then
        echo "[待決] ★ 有一件等超過基線了(超線 ${PD_OVER} 件 / 共 ${PD_N} 件)—— 這件不等週期,現在就報"
      elif [ "$PD_N" -gt 0 ] && [ $(( ROUND % ${AIB_PATROL_PENDING_EVERY:-30} )) -eq 0 ]; then
        echo "[待決] 目前 ${PD_N} 件等真人決策(其中 ${PD_OVER} 件已超過基線)"
      fi
      PD_OVER_LAST="$PD_OVER"
    fi
  fi
  # 心跳檔:一輪一份,覆寫。★ 落在 state 下,不進事件流(事件要留給「我會據以行動」的事)
  printf '{"at":"%s","socket":"%s","seats":[%s]}\n' "$(date -u +%FT%TZ)" "$SOCK" "$HB_ROWS" \
    > "${HOME}/.ez2-aib/state/seat-heartbeat.json.tmp" 2>/dev/null \
    && mv "${HOME}/.ez2-aib/state/seat-heartbeat.json.tmp" "${HOME}/.ez2-aib/state/seat-heartbeat.json"
  HB_ROWS=""
  # (5) 寫集越界:本輪不該有人動 ez2-aib 的 src/
  #   10-08:主樹位置不再寫死某一台的家目錄;照 AIB_PATROL_MAIN_TREE,沒設就用 ~/ez2-aib(不在就安靜略過,跟以前 git -C 失敗一樣)
  out=$(git -C "${AIB_PATROL_MAIN_TREE:-$HOME/ez2-aib}" status --porcelain 2>/dev/null | grep -E "^\s*[AMD?]+\s+src/" | head -3)
  [ -n "$out" ] && echo "[越界] ez2-aib 主樹 src/ 出現改動(本輪寫集不含它):$(echo "$out" | tr '\n' ' ')"
  sleep 60
done
