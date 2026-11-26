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
k '    if (p.next === '\''login'\'' && !lcRun(p)) return relogHtml(k) + lcQuotaHtml(k, '\''login'\'');' ''
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
# LQ1 (門檻跟座艙不一樣;§10.6 方案與用量)
k '[[95, '\''t3'\''], [75, '\''t2'\''], [50, '\''t1'\''], [0, '\''t0'\'']]' '[[90, '\''t3'\''], [75, '\''t2'\''], [50, '\''t1'\''], [0, '\''t0'\'']]'
# LQ2 (格數算法不是四捨五入;§10.6 方案與用量)
k 'Math.round(pct / 10))), lim' 'Math.floor(pct / 10))), lim'
# LQ3 (到上限不轉紅;§10.6 方案與用量)
k '${lim ? '\''lim t3'\'' : lcQTier(pct)}' '${lcQTier(pct)}'
# LQ4 (counting 不寫下界;§10.6 方案與用量)
k 'lcQAgo(seen) + W.least(pct)' 'lcQAgo(seen)'
# LQ5 (rolled_over 照畫舊條;§10.6 方案與用量)
k 'if (st === '\''rolled_over'\'') return' 'if (false) return'
# LQ6 (no_reset 編倒數;§10.6 方案與用量)
k 'st === '\''no_reset'\'' ? [seen ? lcQAgo(seen) : '\'''\'', W.noreset] :' 'st === '\''no_reset'\'' ? [seen ? lcQAgo(seen) : '\'''\'', W.cd(lcQDur(rs - now))] :'
# LQ7 (安裝卡也畫用量;§10.6 方案與用量)
k 'if (a === '\''install'\'') return ln ?' 'if (false) return ln ?'
# LQ8 (舊 aibd 不寫還沒有;§10.6 方案與用量)
k 'if (!Object.prototype.hasOwnProperty.call(p, '\''quota'\'')) return' 'if (false) return'
# LQ9 (Grok 也有重驗鈕;§10.6 方案與用量)
k '(k === '\''claude'\'' || k === '\''codex'\'') && lcHas('\''provider_quota_recheck'\'')' 'lcHas('\''provider_quota_recheck'\'')'
# LQ10 (重驗不看 act;§10.6 方案與用量)
k '&& lcHas('\''provider_quota_recheck'\'');' '&& true;'
# LQ11 (新鮮也出重驗;§10.6 方案與用量)
k 'rb = stale && can ?' 'rb = can ?'
# LQ12 (沒新資料不講;§10.6 方案與用量)
k 'L.qmsg[k] = seen(lcEntry(k)) > before ? '\'''\'' : lcQW().nonew;' 'L.qmsg[k] = '\'''\'';'
# LQ13 (方案名不過白名單;§10.6 方案與用量)
k '/^[a-z0-9_]{1,24}$/.test(q.plan)' 'q.plan'
# LQ14 (連結不加 noopener;§10.6 方案與用量)
k 'target="_blank" rel="noopener noreferrer">${esc(W.link)}' 'target="_blank">${esc(W.link)}'
# LQ15 (登入卡沒掛;§10.6 方案與用量)
k 'return relogHtml(k) + lcQuotaHtml(k, '\''login'\'');' 'return relogHtml(k);'
# LQ16 (卡片下方沒掛;§10.6 方案與用量)
k '${lcQuotaHtml(k, a)}</div><div class="rlcfoot">' '</div><div class="rlcfoot">'
# LQ17 (韓文上限句錯;§10.6 方案與用量)
k '(t ? `한도 도달 · ${t} 후 회복` : '\''한도 도달'\'')' '(t ? `한도 도달` : '\''한도 도달'\'')'
# LQ18 (重驗不是 provider_quota_recheck;§10.6 方案與用量)
k 'await act('\''provider_quota_recheck'\'', { provider: k }); await lcLoad();' 'await lcLoad();'
# LQ19 (counting 沒守 seen=0(f163c N1);§10.6 方案與用量)
k '[seen ? lcQAgo(seen) + W.least(pct) : W.least(pct).trim(),' '[lcQAgo(seen) + W.least(pct),'
# LQ20 (aibd 的字直接查表(3e53b N1);§10.6 方案與用量)
k '(typeof k === '\''string'\'' && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : '\'''\'')' '(o[k] || '\'''\'')'
