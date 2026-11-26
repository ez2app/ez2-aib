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
k "const OM_WHY = { owner_key_not_as_paired: 'reset', " "const OM_WHY = { "
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
k '${esc(W.nokey)}</div>' '${esc(W.reset)}</div>'
# OM26 收起設定寫死中文(f163c N1)
k '>${esc(omW().setClose)}</button>' '>收起設定</button>'
