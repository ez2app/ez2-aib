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
k '    if (R.mode === '\''device'\'') return relogCard(R, N, msg);' ' '
# RLC9 主畫面複製鈕沒接
k '    if (x.closest('\''[data-relogcopy]'\'')) return relogCopy();' ' '
# RLC10 事件窗複製鈕沒接
k ', .evact [data-relogsend], .evact [data-relogcopy]'\'');' ', .evact [data-relogsend]'\'');'
# RLC11 網址帶碼仍畫碼框
k 'const code = auto ?' 'const code = false ?'
# RLC12 沒碼不說
k '<div class="rlcno">AIB 沒給登入碼,這一輪先取消再按一次</div>' '<div class="rlcno"></div>'
# RLC13 按了不真的寫剪貼簿
k 'try { await navigator.clipboard.writeText(R.dcode); R.copied = true; }' 'try { R.copied = true; }'
# RLC14 網址帶碼時不給對照碼(f163c N1)
k 'const code = auto ? `<div class="rlcref">對照碼 <b>${esc(R.dcode)}</b>(登入頁上應該一樣)</div><div class="rlcrow">${go}</div>` :' 'const code = auto ? `<div class="rlcrow">${go}</div>` :'
# RLC15 對照碼不跳脫
k '<div class="rlcref">對照碼 <b>${esc(R.dcode)}</b>' '<div class="rlcref">對照碼 <b>${R.dcode}</b>'
# RLC16 家別色直接用 provider 原字
k 'b = RELOG_BRAND.includes(R.provider) ? R.provider : '\''other'\'';' 'b = R.provider;'
# RLC17 打開登入網頁不開新分頁
k '<a class="rlcgo" href="${esc(R.url)}" target="_blank" rel="noopener noreferrer">' '<a class="rlcgo" href="${esc(R.url)}">'
# RLC18 Codex 標誌改一個位元組(不是官方檔)
k '    codex: '\''data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNzE2IiBo' '    codex: '\''data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNzE2IiBA'
# RLC19 標誌不放在中性方塊(少 rlclogo)
k '`<span class="rlcmark rlclogo${b === ' '`<span class="rlcmark${b === '
# RLC20 不放標誌、一律字母
k 'const rlcMark = (b, N) => RELOG_LOGO[b] ? `' 'const rlcMark = (b, N) => false ? `'
# RLC21 Grok 不放白方塊(原檔黑標放在深色方塊上看不見)
k 'b === '\''grok'\'' ? '\'' rlclogo-light'\'' : '\'''\''' 'b === '\''grok'\'' ? '\'''\'' : '\'''\'''
# RLC22 Codex 不放大(本體只剩方塊三成)
k '  const RELOG_LOGO_PX = { codex: 56, grok: 34 };' '  const RELOG_LOGO_PX = { grok: 34 };'
# RLC23 不認得大小時不給預設 28
k 'width="${RELOG_LOGO_PX[b] || 28}"' 'width="${RELOG_LOGO_PX[b] || 44}"'
# RLC24 Grok 扁形不加大
k '  const RELOG_LOGO_PX = { codex: 56, grok: 34 };' '  const RELOG_LOGO_PX = { codex: 56 };'
# RC25 (Windows Claude 回 paste 被當 device;08de5 03:45 Windows 回應形狀)
k 'mode: r.mode === '\''device'\'' ? '\''device'\'' : '\''paste'\''' 'mode: r.mode === '\''paste'\'' ? '\''device'\'' : '\''paste'\'''
# RC26 (device_code null 印成字;08de5 03:45 Windows 回應形狀)
k 'dcode: r.device_code ? String(r.device_code) : '\'''\''' 'dcode: String(r.device_code)'
