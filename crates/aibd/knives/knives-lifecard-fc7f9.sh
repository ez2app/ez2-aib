#!/usr/bin/env bash
# 安裝 / 換版 / 登入共用卡(fc7f9 11-26,c2f0b IFACE-provider-lifecycle r1 L3;Andy 11-26 02:3x「新安裝、更新、綁定 都是走類似流程」)。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, lcChecks) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# LC1 (沒有 act 也送)
k '|| !lcHas('\''provider_'\'' + a)) return;' ') return;'
# LC2 (body 多帶欄位)
k 'fingerAct('\''provider_install'\'', { provider: k })' 'fingerAct('\''provider_install'\'', { provider: k, version: '\''2.1.326'\'' })'
# LC3 (進度不換百分比)
k 'Math.floor((dn * 100) / tt)' 'Math.floor(dn)'
# LC4 (登入不交給登入卡)
k '    if (p.next === '\''login'\'' && !lcRun(p)) return relogHtml(k);' ''
# LC5 (結束不重抓 version)
k '    if (ended.length) {' '    if (false) {'
# LC6 (先不要沒用)
k '    if (L.no[k] && !lcRun(p)) return '\'''\'';' ''
# LC7 (沒 act 假裝有)
k ': `<span class="rlcno">${esc(W.na)}</span>`;' ': '\'''\'';'
# LC8 (換版卡目前版本拿錯)
k 'cur = String((p.installed && p.installed.version) || '\''?'\'')' 'cur = String(p.measured || '\''?'\'')'
# LC9 (取消不帶 provider)
k 'act('\''provider_job_cancel'\'', { provider: k })' 'act('\''provider_job_cancel'\'', {})'
# LC10 (標題沒經 esc)
k '<h4>${esc(title)}</h4>' '<h4>${title}</h4>'
# LC11 (開席設定卡舊 aibd 沒那句)
k ': `<div class="apn">${esc(SP_INSTALL_NA)}</div>`; }' ': '\'''\''; }'
# LC12 (忙碌字)
k 'lifecycle_busy: '\''已經在進行了,等它跑完。'\'' },' 'lifecycle_busy: '\''忙'\'' },'
# LC13 (英文安裝標題)
k 'on this computer yet. Install it for you?`' 'on this computer yet.`'
# LC14 (開席視窗不接安裝卡)
k ': lcNeeds(p.key) ? '\''。'\'' + `<span class="anlc">${lcCard(p.key)}</span>` : FIX[p.why] ?' ': FIX[p.why] ?'
# LC15 (跑著不重讀)
k 'if (Object.keys(now).length && !L.tm)' 'if (false)'
# LC16 (安裝卡不寫大小)
k '官方下載 ${n} ${v}${mb ? `(約 ${mb} MB)` : '\'''\''}' '官方下載 ${n} ${v}'
# LC17 (MB 用 1048576(c2f0b N1))
k 'Math.round(sz / 1e6)' 'Math.round(sz / 1048576)'
# LC18 (換版取消也寫沒有裝(c2f0b N2 / f163c N1))
k '(String(j.action || a) === '\''update'\'' ? W.cancelledU(N) : W.cancelled(N))' 'W.cancelled(N)'
# LC19 (換版連不到也寫沒裝)
k 'w = (a === '\''update'\'' && W.whyU[c]) || W.why[c];' 'w = W.why[c];'
# LC20 (韓文 owner 句少登入密碼(3e53b N1))
k '(Touch ID / Windows Hello 또는 로그인 비밀번호)' '(Touch ID / Windows Hello)'
