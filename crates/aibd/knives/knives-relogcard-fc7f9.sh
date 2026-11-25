#!/usr/bin/env bash
# 登入卡(fc7f9 11-26;08de5 SHOT-login-card-*-08de5-1126、DESIGN-login-card-page-fc7f9-1126;Andy 01:04 網址已帶碼就不顯示貼碼)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, relogcard block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# RLC1 網址帶碼也顯示貼碼
k 'const relogAuto = (R) => !!(R && R.dcode && String(R.url || '\'''\'').includes(R.dcode));' 'const relogAuto = (R) => false;'
# RLC2 沒按也寫已複製
k 'const said = R.copied === true ?' 'const said = R.copied !== false ?'
# RLC3 Mac / Windows 鍵名對調
k '(/Mac/.test(String((globalThis.navigator && navigator.platform) || '\'''\'')) ? '\''⌘V'\'' : '\''Ctrl+V'\'')' '(/Win/.test(String((globalThis.navigator && navigator.platform) || '\'''\'')) ? '\''⌘V'\'' : '\''Ctrl+V'\'')'
# RLC4 有效時間假裝有
k 'const RELOG_TTL_NA = '\''有效時間:這一版 AIB 還沒給'\'';' 'const RELOG_TTL_NA = '\''有效時間:10 分'\'';'
# RLC5 碼不跳脫
k '<div class="rlcode" aria-label="登入碼">${esc(R.dcode)}</div>' '<div class="rlcode" aria-label="登入碼">${R.dcode}</div>'
# RLC6 複製失敗也算已複製
k 'catch (_) { R.copied = false; }' 'catch (_) { R.copied = true; }'
# RLC7 網址帶碼也寫剪貼簿
k 'if (!R || R.done || !R.dcode || relogAuto(R)) return;' 'if (!R || R.done || !R.dcode) return;'
# RLC8 device 不走新卡
k '    if (R.mode === '\''device'\'') return relogCard(R, N, link, msg);' ' '
# RLC9 主畫面複製鈕沒接
k '    if (x.closest('\''[data-relogcopy]'\'')) return relogCopy();' ' '
# RLC10 事件窗複製鈕沒接
k ', .evact [data-relogsend], .evact [data-relogcopy]'\'');' ', .evact [data-relogsend]'\'');'
# RLC11 網址帶碼仍畫碼框
k 'const code = auto ? '\'''\'' :' 'const code = false ? '\'''\'' :'
# RLC12 沒碼不說
k ''\''<div class="bad">AIB 沒給登入碼,這一輪先取消再按一次</div>'\''' ''\'''\'''
# RLC13 按了不真的寫剪貼簿
k 'try { await navigator.clipboard.writeText(R.dcode); R.copied = true; }' 'try { R.copied = true; }'
