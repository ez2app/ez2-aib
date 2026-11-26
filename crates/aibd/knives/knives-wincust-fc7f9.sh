#!/usr/bin/env bash
# 客戶 Windows 工作台兩處(fc7f9 11-26,ebab8 11:56 客戶截圖):測試機區塊沒登記時收成一行、不列客戶主機名與位址、不出紅叉;輸入框上方黃字不印內部介面名、改白話四語。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, block 15) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# CU1 沒登記也照列全部主機(不收)(ebab8 11:56 客戶 Windows 截圖)
k '    if (!kept.length && !open) return thClosed(' '    if (false && !kept.length && !open) return thClosed('
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
k '    if (!T && !thWant()) return thClosed(' '    if (false && !T && !thWant()) return thClosed('
# CU10 按了不記住、開發機每次都要再按(ebab8 12:00)
k 'if (v) localStorage.setItem('\''aib.th.want'\'', '\''1'\'');' 'if (v) localStorage.setItem('\''aib.th.want'\'', '\''0'\'');'
# CU11 收起後仍留著清單、下次照讀(ebab8 12:00)
k '{ thWantSet(false); S.th = null; return render(); }' '{ thWantSet(true); S.th = null; return render(); }'
# CU12 記住過的開發機重整後仍收著(ebab8 12:00)
k 'open = !!T.open || thWant();' 'open = !!T.open;'
# CU13 標題寫死中文(f163c 5d00 N1)
k ': `<div class="thh"><b>${esc(thX().title)}</b><span class="hv">' ': `<div class="thh"><b>測試機</b><span class="hv">'
# CU14 收據可用 / 重讀寫死中文(f163c 5d00 N1)
k 'esc(thX().recv + T.pinned' 'esc('\''收據可用 '\'' + T.pinned'
# CU15 列鈕寫死中文(f163c 5d00 N1)
k '${esc(m.pinned || half ? X.rm : X.add)}' '${m.pinned || half ? '\''收回這台'\'' : '\''加'\''}'
# CU16 英文字表缺(f163c 5d00 N1)
k '    en: {"title": "Test machines"' '    en0: {"title": "Test machines"'
# CU17 + AI 夥伴面板沒按也讀 ssh 設定(f163c 897efdc8cd L1)
k 'if ((hsHas('\''cand'\'') || hasAct('\''test_host_list'\'') === true) && !thWant()) return' 'if (false && (hsHas('\''cand'\'') || hasAct('\''test_host_list'\'') === true) && !thWant()) return'
# CU18 面板只看候選那條、舊版 test_host_list 那條漏擋(f163c 897efdc8cd L1)
k 'if ((hsHas('\''cand'\'') || hasAct('\''test_host_list'\'') === true) && !thWant())' 'if ((hsHas('\''cand'\'')) && !thWant())'
# CU19 面板列出不能用的名字(f163c 897efdc8cd L1)
k 'T.list.filter((m) => m.usable || m.pinned);' 'T.list.filter((m) => true);'
# CU20 + AI 夥伴面板測試機標題寫死中文(f163c b67ffd54a4)
k 'return `<div class="aplab apgold">${esc(thX().title)}</div><div class="apl hsoff">' 'return `<div class="aplab apgold">測試機</div><div class="apl hsoff">'
# CU21 英文不能用原因表不見(f163c N1)
k '    en: {"reached_through_something_else":' '    en0: {"reached_through_something_else":'
# CU22 原因查表不看語言(f163c N1)
k 'const thWhy = (c) => ((TH_WHY_L[thL()] || {})[c])' 'const thWhy = (c) => ((TH_WHY_L['\''zh-TW'\''] || {})[c])'
# CU23 錯誤查表不看語言(f163c N1)
k '(t) => t)(((TH_ERR_L[thL()] || {})[c])' '(t) => t)(((TH_ERR_L['\''zh-TW'\''] || {})[c])'
# CU24 面板英文鈕字寫回中文(f163c de39 N1)
k '"tap": "Click to open"' '"tap": "點一下開"'
# CU25 點測試機的訊息寫死中文(f163c N1)
k 'S.add.hsOffMsg = thX().testNo;' 'S.add.hsOffMsg = '\''測試機還不能交給夥伴管'\'';'
# CU26 日文不支援字缺(f163c N1)
k '"unsupported": "未対応"' '"unsupported0": "未対応"'
