#!/usr/bin/env bash
# 開席被拒三個碼的字與鈕(fc7f9 11-26;c2f0b IFACE-provider-lifecycle §5b、f163c REVIEW-l2 36937f30 N1):verify_failed / unmeasured 接 provider_install,path_linked 只說明,四語。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, rf_* block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# RF1 (開席拒字沒接)
k '      else if (rfOwn(rgCode(e))) {' '      else if (false) {'
# RF2 (認任何鍵(constructor 也算))
k 'Object.prototype.hasOwnProperty.call(OPEN_RF['\''zh-TW'\''], c) ? c : '\'''\''' 'c in OPEN_RF['\''zh-TW'\''] ? c : '\'''\'''
# RF3 (path_linked 也給鈕)
k '搬回來或請我們協助。'\'', '\'''\''],' '搬回來或請我們協助。'\'', '\''修好'\''],'
# RF4 (沒有 provider_install 也出鈕)
k 'btn = w[2] && typeof lcHas === '\''function'\'' && lcHas('\''provider_install'\'');' 'btn = w[2];'
# RF5 (裝的時候多帶欄位)
k 'fingerAct('\''provider_install'\'', { provider: R.provider })' 'fingerAct('\''provider_install'\'', { provider: R.provider, version: '\''x'\'' })'
# RF6 (失敗當成裝好)
k 'catch (e) { R.err = lcWhy(rgCode(e), lcName(R.provider)); }' 'catch (e) { R.done = true; }'
# RF7 (新的一次不清舊拒字)
k 'A.grp = null; A.refuse = null; render();' 'A.grp = null; render();'
# RF8 (鈕沒接)
k '    if (S.add && x.closest('\''[data-addrfx]'\'')) { rfFix(); return; }' ' '
# RF10 (韓文退回中文)
k '  const rfT = () => OPEN_RF[typeof askLang === '\''function'\'' ? askLang() : '\''zh-TW'\''] || OPEN_RF['\''zh-TW'\''];' '  const rfT = () => OPEN_RF['\''zh-TW'\''];'
# RF11 (又寫成捷徑;Windows 的捷徑是 .lnk,f163c REVIEW-l2 02e02b04 N1)
k '被換成了連結(symlink / junction),AIB' '被換成了捷徑,AIB'
