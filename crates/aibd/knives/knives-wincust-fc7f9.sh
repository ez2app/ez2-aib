#!/usr/bin/env bash
# 客戶 Windows 工作台兩處(fc7f9 11-26,ebab8 11:56 客戶截圖):測試機區塊沒登記時收成一行、不列客戶主機名與位址、不出紅叉;輸入框上方黃字不印內部介面名、改白話四語。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, block 15) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# CU1 沒登記也照列全部主機(不收)(ebab8 11:56 客戶 Windows 截圖)
k '    if (!kept.length && !T.open) return `<div class="thh">' '    if (false && !kept.length && !T.open) return `<div class="thh">'
# CU2 收起時仍列不能用的(紅叉回來)(ebab8 11:56 客戶 Windows 截圖)
k '    const shown = T.open ? kept.concat(canAdd) : kept;' '    const shown = T.list;'
# CU3 展開也列不能用的名字(ebab8 11:56 客戶 Windows 截圖)
k 'kept.concat(canAdd) : kept;' 'T.list : kept;'
# CU4 不寫另有幾台(ebab8 11:56 客戶 Windows 截圖)
k '${nBad > 0 ? esc(' '${nBad < 0 ? esc('
# CU5 「從這台的 ssh 設定加」鈕沒接(ebab8 11:56 客戶 Windows 截圖)
k '    if (x.closest('\''[data-thopen]'\'') && S.th)' '    if (x.closest('\''[data-thopen0]'\'') && S.th)'
# CU6 英文表缺、退回中文(ebab8 11:56 客戶 Windows 截圖)
k '    en: { none: '\''None added (a developer feature)'\''' '    en0: { none: '\''None added (a developer feature)'\'''
# CU7 黃字又印介面名(ebab8 11:56 客戶 Windows 截圖)
k '${miss.some((t) => t !== '\''screen_capture'\'') ? `<div class="ctn ctmiss">${esc(ctMissWord())}</div>` : '\'''\''}' '${miss.some((t) => t !== '\''screen_capture'\'') ? `<div class="ctn ctmiss">${esc(miss.join('\'' / '\''))}</div>` : '\'''\''}'
# CU8 日文表缺(ebab8 11:56 客戶 Windows 截圖)
k 'ja: '\''この版の AIB は送信履歴' 'ja0: '\''この版の AIB は送信履歴'
