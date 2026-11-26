#!/usr/bin/env bash
# 客戶 Windows 工作台兩處(fc7f9 11-26,ebab8 11:56 客戶截圖):測試機區塊沒登記時收成一行、不列客戶主機名與位址、不出紅叉;輸入框上方黃字不印內部介面名、改白話四語。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, block 15) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# CU1 沒登記也照列全部主機(不收)(ebab8 11:56 客戶 Windows 截圖)
k '    if (!kept.length && !open) return `<div class="thh">' '    if (false && !kept.length && !open) return `<div class="thh">'
# CU2 收起時仍列不能用的(紅叉回來)(ebab8 11:56 客戶 Windows 截圖)
k '    const shown = open ? kept.concat(canAdd) : kept;' '    const shown = T.list;'
# CU3 展開也列不能用的名字(ebab8 11:56 客戶 Windows 截圖)
k 'kept.concat(canAdd) : kept;' 'T.list : kept;'
# CU4 不寫另有幾台(ebab8 11:56 客戶 Windows 截圖)
k '${nBad > 0 ? esc(' '${nBad < 0 ? esc('
# CU5 「從這台的 ssh 設定加」鈕沒接(ebab8 11:56 客戶 Windows 截圖)
k '    if (x.closest('\''[data-thopen]'\'')) { thWantSet(true);' '    if (x.closest('\''[data-thopen0]'\'')) { thWantSet(true);'
# CU6 英文表缺、退回中文(ebab8 11:56 客戶 Windows 截圖)
k '    en: { none: "A developer feature;' '    en0: { none: "A developer feature;'
# CU7 黃字又印介面名(ebab8 11:56 客戶 Windows 截圖)
k '${miss.some((t) => t !== '\''screen_capture'\'') ? `<div class="ctn ctmiss">${esc(ctMissWord())}</div>` : '\'''\''}' '${miss.some((t) => t !== '\''screen_capture'\'') ? `<div class="ctn ctmiss">${esc(miss.join('\'' / '\''))}</div>` : '\'''\''}'
# CU8 日文表缺(ebab8 11:56 客戶 Windows 截圖)
k 'ja: '\''この版の AIB は送信履歴' 'ja0: '\''この版の AIB は送信履歴'
# CU9 沒按就先讀 ssh 設定(ebab8 12:00)
k '    if (!T && !thWant()) return `<div class="thh">' '    if (false && !T && !thWant()) return `<div class="thh">'
# CU10 按了不記住、開發機每次都要再按(ebab8 12:00)
k 'if (v) localStorage.setItem('\''aib.th.want'\'', '\''1'\'');' 'if (v) localStorage.setItem('\''aib.th.want'\'', '\''0'\'');'
# CU11 收起後仍留著清單、下次照讀(ebab8 12:00)
k '{ thWantSet(false); S.th = null; return render(); }' '{ thWantSet(true); S.th = null; return render(); }'
# CU12 記住過的開發機重整後仍收著(ebab8 12:00)
k 'open = !!T.open || thWant();' 'open = !!T.open;'
# CU13 標題寫死中文(f163c 5d00 N1)
k 'if (!T && !thWant()) return `<div class="thh"><b>${esc(thX().title)}</b>' 'if (!T && !thWant()) return `<div class="thh"><b>測試機</b>'
# CU14 收據可用 / 重讀寫死中文(f163c 5d00 N1)
k 'esc(thX().recv + T.pinned' 'esc('\''收據可用 '\'' + T.pinned'
# CU15 列鈕寫死中文(f163c 5d00 N1)
k '${esc(m.pinned || half ? X.rm : X.add)}' '${m.pinned || half ? '\''收回這台'\'' : '\''加'\''}'
# CU16 英文字表缺(f163c 5d00 N1)
k '    en: {"title": "Test machines"' '    en0: {"title": "Test machines"'
