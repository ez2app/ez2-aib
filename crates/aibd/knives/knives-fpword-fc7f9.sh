#!/usr/bin/env bash
# 頁面本人確認字跟平台走(fc7f9 11-26;ebab8 13:3x 第三包、LIST-fingerword-fc7f9-1126 靜態 36 處)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, fp_win_* / fpc / pw) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
kc() { python3 -I scripts/knife.py apps/web/dist/ws.css "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# FW1 推上去的提示沒換字
k '推到遠端;${(typeof fpSay === '\''function'\'' ? fpSay : (t) => t)('\''要按這台的指紋'\'')}">' '推到遠端;要按這台的指紋">'
# FW2 搬舊席鈕沒換字
k '>${(typeof fpSay === '\''function'\'' ? fpSay : (t) => t)('\''按指紋搬'\'')}</button>' '>按指紋搬</button>'
# FW3 測試機錯誤不經平台字
k '  const thErr = (c) => (typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(((TH_ERR_L[thL()] || {})[c]) || TH_ERR[c] || '\'''\'');' '  const thErr = (c) => (((TH_ERR_L[thL()] || {})[c]) || TH_ERR[c] || '\'''\'');'
# FW4 卡片訊息不經平台字
k '${esc((typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(C.actMsg))}' '${esc(C.actMsg)}'
# FW5 時間軸類別名沒換
k '${esc((typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(REC[k]))}</b>' '${esc(REC[k])}</b>'
# FW6 密語列 Windows 也寫指紋
k 'return { text: (typeof fpWin === '\''function'\'' && fpWin()) ? '\''這台用 Windows Hello 確認是你本人,不用密語'\'' :' 'return { text: false ? '\''這台用 Windows Hello 確認是你本人,不用密語'\'' :'
# FW7 確認卡 Mac 字混 Windows
k '沒有指紋就輸入電腦登入密碼。'\''}' '沒有指紋就輸入電腦登入密碼(Windows 用 Hello PIN)。'\''}'
# FW8 對照表少一條(逐台按指紋)
k '${(typeof fpSay === '\''function'\'' ? fpSay : (t) => t)('\''逐台按指紋'\'')}</i>' '逐台按指紋</i>'
# FW9 提示列不經平台字(3e53b L1)
k 'S.hint = (typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(msg);' 'S.hint = msg;'
# FW10 主機金鑰指紋也被換成 Windows Hello(3e53b N1)
k 'let o = t.split('\''金鑰指紋'\'').join('\''\u0001'\'');' 'let o = t;'
# FW11 推送 / 權杖錯誤不經平台字(3e53b L1)
k 'return (typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(T[rs] || errText(e)); };' 'return T[rs] || errText(e); };'
# FW12 搬舊席錯誤不經平台字(3e53b L1)
k 'return (typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(MOVE_WHY[rs] || errText(e)); };' 'return MOVE_WHY[rs] || errText(e); };'
