#!/usr/bin/env bash
# 本人確認方式設定(fc7f9 11-27;c2f0b df67611099;PLAN-owner-method-c2f0b r2 §2 / §5 / §9;Andy 21:1x 改方式放設定頁、不顯眼):照實寫目前方式、改成密碼先看說明按兩次、Windows 不出選項。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, om_* block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# OM1 改成密碼不用先看說明
k "    if (to === 'password' && !O.ask) return; // 沒先看過說明不送" "    // 沒先看過說明不送"
# OM2 指紋時直接出改成密碼的送出鈕(不經說明)
k 'body += `<button type="button" class="k" data-omask="1">' 'body += `<button type="button" class="k" data-omset="password">'
# OM3 說明字拿掉降級那句
k "用登入密碼:知道這台電腦登入密碼的人,都能核准 AIB 的動作。之後" "之後"
# OM4 Windows 不寫去哪改(Windows 不出選項是結構:windows_hello 分支排在按鈕分支前,om_windows_never_choice 核)
k "    if (m === 'windows_hello') body += esc(W.winHow);" "    if (m === 'windows_hello') body += '';"
# OM5 沒讀到金鑰時還說目前用指紋(f163c a179d649 N1)
k '</b>${esc(W.nokey)}' '</b>目前用指紋。${esc(W.nokey)}'
# OM6 不看 can_change
k "const m = d.method, set = hasAct(OM_SET) === true && d.can_change === true;" "const m = d.method, set = hasAct(OM_SET) === true;"
# OM7 多送欄位
k "try { await act(OM_SET, { to });" "try { await act(OM_SET, { to, by: 'page' });"
# OM8 金鑰跟配對時不同的錯誤碼沒照表(3e53b L1:用重設出路)
k "const OM_WHY = { owner_key_not_as_paired: 'omReset', " "const OM_WHY = { "
# OM9 取消的錯誤碼沒照表
k "user_cancel: 'cancel', " ""
# OM10 舊 aibd 也出設定鈕
k "const setBtn = () => (hasAct(OM_ST) === true ? " "const setBtn = () => (true ? "
# OM11 設定鈕沒接上
k "\${typeof setBtn === 'function' ? setBtn() : ''}<a class=\"ib\"" "<a class=\"ib\""
# OM12 打開設定不重讀狀態
k "if (S.setOpen) { S.om = null; omLoad(); }" "if (S.setOpen) { }"
# OM13 一次一個
k "if (!O || O.busy || !O.d ||" "if (!O || !O.d ||"
# OM14 改回指紋要先問(變成兩段)
k "    else if (set && m === 'password') body +=" "    else if (set && m === 'password' && O.ask) body +="
# OM15 讀不到狀態不照實寫
k "O.d = null; O.bad = true; }" "O.d = null; }"
# OM16 重設出路碼不照表(只剩一個)
k "  const OM_RESET = ['owner_key_not_as_paired', 'owner_method_no_pin', 'owner_method_record_not_as_paired', 'owner_method_pin_not_moved'];" "  const OM_RESET = ['owner_key_not_as_paired'];"
# OM17 重設時還出改鈕
k "    if (OM_RESET.includes(d.why)) return" "    if (false) return"
# OM18 選擇卡不限新手期
k "if (S.pair || typeof guideNew !== 'function' || !guideNew(S.guide) || hasAct(OM_ST)" "if (S.pair || typeof guideNew !== 'function' || hasAct(OM_ST)"
# OM19 選擇卡不看 method(密碼 / Windows 也出)
k "if (!d || d.method !== 'biometry' || d.can_change !== true" "if (!d || d.can_change !== true"
# OM20 選擇卡選密碼不經說明
k 'data-ompick="1">${esc(W.pickBio)}</button><button type="button" class="k" data-omask="1">' 'data-ompick="1">${esc(W.pickBio)}</button><button type="button" class="k" data-omset="password">'
# OM21 選了還一直出
k "|| hasAct(OM_SET) !== true || omPicked()) return '';" "|| hasAct(OM_SET) !== true) return '';"
# OM22 改成功不記已選
k "W.donePw : W.doneBio }; omMark(); }" "W.donePw : W.doneBio }; }"
# OM23 卡不是確認類(指引遮罩會蓋住它)
k "<div class=\"occ omcard\" role=\"alertdialog\"" "<div class=\"occ omcard\" role=\"group\""
# OM24 選擇卡沒接進指引
k 'return `<div class="gbox">${typeof omCard ' 'return `<div class="gbox">${typeof omCardGone '

# OM25 沒讀到金鑰也寫成要重設 / 重新配對(f163c 04019a58 L1)
k '${esc(W.nokey)}</div>' '${esc(W.omReset)}</div>'
# OM26 收起設定寫死中文(f163c N1)
k '>${esc(omW().setClose)}</button>' '>收起設定</button>'

# SK1 Windows 也寫 ⌘(ebab8 11-27 09:36 送出鍵提示)
k "fpWin() ? 'Ctrl' : '⌘'}+Enter" "fpWin() ? '⌘' : '⌘'}+Enter"
# SK2 Mac 寫成 Ctrl
k "fpWin() ? 'Ctrl' : '⌘'}+Enter" "fpWin() ? 'Ctrl' : 'Ctrl'}+Enter"
# SK3 單席輸入框沒提示
k '說…${sendKey(' '說…${String(' 
# SK4 舊席寄信寫成送出
k '(走舊信件帳)${sendKey('"'"'寄出'"'"')}' '(走舊信件帳)${sendKey('"'"'送出'"'"')}'

# PH1 沒碼也只丟終端機指令(ebab8 11-27 10:08 第 3 點)
k ' : PAIR_NOCODE + pairHowHtml(P.how)}' ' : pairHowHtml(true)}'
# PH2 步驟一開就攤開、沒有「怎麼拿配對碼」鈕
k 'const pairHowHtml = (open) => (open ?' 'const pairHowHtml = (open) => (true ?'
# PH3 Windows 步驟照 Mac 寫終端機
k '<li>從開始選單點「AIB」(或開一個新的命令提示字元,打 <code>aib web</code>)</li>' '<li>打開「終端機」,打 <code>aib web</code> 按 Enter(打了說找不到,改貼 <code>~/.local/bin/aibd ctl web</code>)</li>'
# PH4 按了鈕沒反應
k 'S.pair = Object.assign({}, S.pair, { how: true })' 'S.pair = Object.assign({}, S.pair, { how: false })'
# PH5 白話句改成指令
k 'PAIR_NOCODE = '"'"'<p>這一頁還沒配對' 'PAIR_NOCODE = '"'"'<p>在終端機跑 aib web,這一頁還沒配對'

# RS1 四語重設句有一語漏了紀錄檔名(3e53b 11-27 10:10 §1 d)
k 'pair-key-pin.json と pair-key-method.json を削除' 'pair-key-pin.json を削除'
# RS2 OM_WHY 對回舊鍵名 reset(用量表那個)
k "owner_key_not_as_paired: 'omReset'" "owner_key_not_as_paired: 'reset'"

# PH6 Mac 第 1 步拿掉完整路徑備援(c2f0b 讓乾淨安裝有 aib 之前,打 aib web 會找不到;f163c 45d289a4 L1、Andy 10:1x)
k '按 Enter(打了說找不到,改貼 <code>~/.local/bin/aibd ctl web</code>)</li><li>AIB' '按 Enter</li><li>AIB'
# PH7 第 2 步沒說還要確認本人(f163c 45d289a4 N1)
k ';按「配對」,再照畫面確認是你本人</li>' ',按「配對」就好</li>'

# CL1 清過紀錄不說(c2f0b 11-27 10:18 §2)
k "if (d.cleared === 'owner_method_record_cleared' && m === 'biometry') body = " "if (false) body = "
# CL2 不能改也叫人重新選
k "esc(W.cleared) + (set ? esc(W.clearedPick) : '')" "esc(W.cleared) + esc(W.clearedPick)"
# CL3 非指紋也說清過
k "d.cleared === 'owner_method_record_cleared' && m === 'biometry'" "d.cleared === 'owner_method_record_cleared'"
# CL4 什麼值都算清過
k "if (d.cleared === 'owner_method_record_cleared' && m" "if (d.cleared && m"

# PW1 等指紋時 5 秒到照樣寫 slow(ebab8 11-27 Andy 10:14 截圖)
k "if (pairHeld()) pairHoldPaint(); else progSet" "if (false) pairHoldPaint(); else progSet"
# PW2 等確認那步也給再試一次
k "wh = r.state === 'slow' && r.why ?" "wh = (r.state === 'slow' || r.state === 'hold') && r.why ?"
# PW3 時間到了不寫原因
k "if (n != null && n <= 0) return progSet" "if (false) return progSet"
# PW4 不倒數
k "(left > 0 ? '(還有 ' + left + ' 秒)' : '')" "''"
# PW5 Windows 寫本人確認視窗
k "fpWin() ? '等你在 Windows Hello 視窗確認'" "fpWin() ? '等你在本人確認視窗確認'"
# PW6 送出時沒打開等確認
k "Object.assign(PAIR_HOLD, { on: cip, chan" "Object.assign(PAIR_HOLD, { on: false, chan"
# PW7 配對好了沒關掉
k "S.paired = true; S.pair = null; PAIR_HOLD.on = false;" "S.paired = true; S.pair = null;"

# KK1 Mac 照舊只套 Windows 字(c2f0b 11-27 10:17 應用程式入口)
k "fpWin() ? PAIR_WIN_SAY : PAIR_MAC_SAY)" "fpWin() ? PAIR_WIN_SAY : [])"
# KK2 Mac 拿碼句寫回終端機
k "'用 Spotlight(⌘ 空白鍵)搜「AIB」打開,它會打開這一頁並給你配對碼,把碼打在這裡。'" "'在終端機跑 <code>aib web</code>,把它給的配對碼打在這裡。'"
# KK3 沒碼步驟拿掉 aib web 第二條路(Andy 10:1x)
k "'<li>用 Spotlight(⌘ 空白鍵)搜「AIB」打開(或在終端機打 <code>aib web</code>)</li>'" "'<li>用 Spotlight(⌘ 空白鍵)搜「AIB」打開</li>'"
# KK4 不看 key_kind
k "Object.prototype.hasOwnProperty.call(PAIR_WAIT_KK, kk) ? PAIR_WAIT_KK[kk] : PAIR_WAIT_MAC" "PAIR_WAIT_MAC"
# KK5 Touch ID 與密碼並列
k "login_password: '<h2>輸入登入密碼確認</h2>" "login_password: '<h2>用 Touch ID 或登入密碼確認</h2>"
# KK6 進度那步不看 key_kind
k "kk === 'touch_id' ? '等你用 Touch ID 確認' : " ""
# KK7 status 的 key_kind 沒帶進來
k "kk: typeof st.key_kind === 'string' ? st.key_kind : '' }" "kk: '' }"

# HC1 兌換卡住永遠寫在等你(f163c e7e78fe4 N1)
k ": PAIR_HOLD.at ? Math.ceil((PAIR_HOLD.at + PAIR_HOLD_MAX - Date.now()) / 1000) : null);" ": null);"
# HC2 兌換還沒回也寫假秒數
k "pairHoldSay(PAIR_HOLD.chan, PAIR_HOLD.until ? n : null, PAIR_HOLD.kk)" "pairHoldSay(PAIR_HOLD.chan, n, PAIR_HOLD.kk)"

# RE1 拿到新碼沒填進欄(Andy 10:18 重取配對碼)
k "code: nc || S.pair.code || '' });" "code: S.pair.code || '' });"
# RE2 409 照樣出鈕
k "else if (r.status === 409 || r.status === 404 || r.status === 405) PAIR_RE.no = true;" "else if (r.status === 404 || r.status === 405) PAIR_RE.no = true;"
# RE3 429 沒白話
k "r.status === 429 ? '按太多次了,等幾分鐘再按' : " ""
# RE4 焦點沒到配對鈕
k "if (b) b.focus(); }" "}"
# RE5 碼沒驗形狀
k "typeof j.code === 'string' && /^[A-Za-z0-9-]{4,40}\$/.test(j.code)" "typeof j.code === 'string'"
# RE6 配對中也能按
k "if (PAIR_RE.no || PAIR_RE.busy || PAIRING.on) return;" "if (PAIR_RE.no || PAIR_RE.busy) return;"
# RE7 鈕沒接上點擊
k 'class="ib" data-pairre="1"' 'class="ib" data-pairrx="1"'

# NW1 Windows 沒寫要新開視窗(c2f0b 15:15 op8)
k '(或開一個新的命令提示字元,打 <code>aib web</code>)' '(或打 <code>aib web</code>)'
