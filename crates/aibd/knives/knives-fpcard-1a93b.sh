#!/usr/bin/env bash
# 刀名一律 PG 前綴(下面寫的是舊名):FC1–FC11(1a93b 11-23 驗證卡 57ce5d2425;fc7f9 照 pending KNIVES-fpcard-1a93b-57ce5d2425.tsv 轉寫進倉,FC7 / FC8 錨點跟著 page_csp 修法改成 data-sty)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGFC1
k '    const card = fpcOpen(type, body, first, until);' '    const card = { abort: false };'
# PGFC2
k '      if (card.abort) throw Object.assign(new Error('\''finger_cancel'\''), { code: '\''finger_cancel'\'', reason: '\''finger_cancel'\'', ticket });' '      // knife FC2'
# PGFC3
k '    try { const out = await fingerWait(); fpcDone(card, true); return out; }' '    try { const out = await fingerWait(); return out; }'
# PGFC4
k '    catch (e) { const rs = e && (e.reason || e.code || (e.body && e.body.reason)); fpcDone(card, false, (e && e.pageMsg) || FP_NO[rs] || (rs === '\''owner_check_not_approved'\'' ? '\''沒按到'\'' : '\''沒有完成'\'')); throw e; }' '    catch (e) { const rs = e && (e.reason || e.code || (e.body && e.body.reason)); fpcDone(null, false, (e && e.pageMsg) || FP_NO[rs] || (rs === '\''owner_check_not_approved'\'' ? '\''沒按到'\'' : '\''沒有完成'\'')); throw e; }'
# PGFC5
k '    const W = Array.isArray(first && first.shown) ? [String(first.words || type), first.shown.map((x) => [String((x && x.k) || '\'''\''), String((x && x.v) || '\'''\''), !!(x && x.up)])] : (FP_WHAT[type] ? FP_WHAT[type](body || {}, first) : [type, []]);' '    const W = FP_WHAT[type] ? FP_WHAT[type](body || {}, first) : [type, []];'
# PGFC6
k '    const rows = o.lines.filter((x) => x[1]).map(([k, v, up]) => `<span class="k">${esc(k)}</span><span${up ? '\'' class="up"'\'' : '\'''\''}>${esc(v)}</span>`).join('\'''\'');' '    const rows = o.lines.filter((x) => x[1]).map(([k, v, up]) => `<span class="k">${esc(k)}</span><span${up ? '\'' class="up"'\'' : '\'''\''}>${v}</span>`).join('\'''\'');'
# PGFC7
k '    return `<div class="fpc ${o.st} ${!o.st && left <= 30 ? '\''late'\'' : '\'''\''} ${!o.st && left <= 10 ? '\''urg'\'' : '\'''\''}" role="status" aria-live="polite"><div class="fpl"><svg viewBox="0 0 56 56" class="ring" aria-hidden="true"><circle cx="28" cy="28" r="25" class="bg"/><circle cx="28" cy="28" r="25" class="fg" data-sty="stroke-dashoffset:${off.toFixed(1)}"/></svg><span class="fpi">${icon}</span>${o.st ? '\'''\'' : `<span class="sec">還有 ${left} 秒</span>`}</div><div class="fpm"><b>${head}${esc(o.what)}</b>${rows ? `<div class="fpt">${rows}</div>` : '\'''\''}${foot}</div></div>`;' '    return `<div class="fpc ${o.st} ${!o.st && left <= 5 ? '\''late'\'' : '\'''\''} ${!o.st && left <= 10 ? '\''urg'\'' : '\'''\''}" role="status" aria-live="polite"><div class="fpl"><svg viewBox="0 0 56 56" class="ring" aria-hidden="true"><circle cx="28" cy="28" r="25" class="bg"/><circle cx="28" cy="28" r="25" class="fg" data-sty="stroke-dashoffset:${off.toFixed(1)}"/></svg><span class="fpi">${icon}</span>${o.st ? '\'''\'' : `<span class="sec">還有 ${left} 秒</span>`}</div><div class="fpm"><b>${head}${esc(o.what)}</b>${rows ? `<div class="fpt">${rows}</div>` : '\'''\''}${foot}</div></div>`;'
# PGFC8
k '    return `<div class="fpc ${o.st} ${!o.st && left <= 30 ? '\''late'\'' : '\'''\''} ${!o.st && left <= 10 ? '\''urg'\'' : '\'''\''}" role="status" aria-live="polite"><div class="fpl"><svg viewBox="0 0 56 56" class="ring" aria-hidden="true"><circle cx="28" cy="28" r="25" class="bg"/><circle cx="28" cy="28" r="25" class="fg" data-sty="stroke-dashoffset:${off.toFixed(1)}"/></svg><span class="fpi">${icon}</span>${o.st ? '\'''\'' : `<span class="sec">還有 ${left} 秒</span>`}</div><div class="fpm"><b>${head}${esc(o.what)}</b>${rows ? `<div class="fpt">${rows}</div>` : '\'''\''}${foot}</div></div>`;' '    return `<div class="fpc ${o.st} ${!o.st && left <= 30 ? '\''late'\'' : '\'''\''} ${!o.st && left <= 0 ? '\''urg'\'' : '\'''\''}" role="status" aria-live="polite"><div class="fpl"><svg viewBox="0 0 56 56" class="ring" aria-hidden="true"><circle cx="28" cy="28" r="25" class="bg"/><circle cx="28" cy="28" r="25" class="fg" data-sty="stroke-dashoffset:${off.toFixed(1)}"/></svg><span class="fpi">${icon}</span>${o.st ? '\'''\'' : `<span class="sec">還有 ${left} 秒</span>`}</div><div class="fpm"><b>${head}${esc(o.what)}</b>${rows ? `<div class="fpt">${rows}</div>` : '\'''\''}${foot}</div></div>`;'
# PGFC9
k '    repo_gh_token: (b) => ['\''存 GitHub 權杖'\'', [['\''倉'\'', b.repo], ['\''用途'\'', '\''推分支與開 PR'\'', true]]],' '    repo_gh_token: (b) => ['\''存 GitHub 權杖'\'', [['\''倉'\'', b.repo], ['\''用途'\'', '\''推分支與開 PR'\'', true], ['\''權杖'\'', b.token]]],'
# PGFC10
k '    if (F) return `<span class="trs" data-trustwait>${FP_SEE}</span>`;' '    if (F) return `<span class="trs" data-trustwait>請在這台 Mac 按指紋 · 還有 ${F.left | 0} 秒 · ${FINGER_BEHIND}</span>`;'
# PGFC11
k '<div class="fph dim">${o.code || o.codeWait ? '\''系統視窗上的<b>對照碼</b>跟這張卡一樣才按;動作、對象以系統視窗為準。對照碼不一樣就按取消。'\'' : '\''這台算不出對照碼:動作、對象以系統視窗為準,看不懂就按取消。'\''}</div>' ' '
# PGFK1 (8b09a REVIEW-fpcard M1:卡上對照碼 = aibd pair_code(fc7f9 疊在鏈頂))
k '<div class="fpcode">對照碼 <b data-fpcode>${o.code ? esc(o.code) : o.codeWait ? '\''…'\'' : '\''算不出來'\''}</b></div>' ' '
# PGFK2 (8b09a REVIEW-fpcard M1:卡上對照碼 = aibd pair_code(fc7f9 疊在鏈頂))
k 'Array.from(h.slice(0, 2), (b)' 'Array.from(h.slice(1, 3), (b)'
# PGFK3 (8b09a REVIEW-fpcard M1:卡上對照碼 = aibd pair_code(fc7f9 疊在鏈頂))
k 'code: FP_CODE_RE.test(given) ? given : '\'''\''' 'code: '\'''\'''
# PGFK4 (8b09a REVIEW-fpcard M1:卡上對照碼 = aibd pair_code(fc7f9 疊在鏈頂))
k '系統視窗上的<b>對照碼</b>跟這張卡一樣才按;動作、對象以系統視窗為準。' '系統視窗上的字跟這張卡一樣才按。'
# PGFK5 (8b09a REVIEW-fpcard M1:卡上對照碼 = aibd pair_code(fc7f9 疊在鏈頂))
k '${o.code || o.codeWait ? '\''系統視窗' '${1 ? '\''系統視窗'
# PGFN1 (8b09a REVIEW-fpcard N1:卡上截字要說共幾字)
k ' + '\''…(共 '\'' + a.length + '\'' 字)'\''' ' '
