#!/usr/bin/env bash
# PT / RG / HS (fc7f9 11-23, 頁面第二輪 S3 權限模板 / S4 人格·大腦·角色 改與封存 / S2 管理主機;ORDER-page2-ebab8-fc7f9-1123).
# Third part of knives-page2-fc7f9.sh (split again 11-25 by ebab8: knives-page2b took 1080-1351 s on GitHub macOS, over the 900 s a script may take).
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs §37 §38 §39) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGMO7 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'Object.assign({ keep: g.keep, merge: mgGone(g).map((x) => x.uid) }, mgOptBody(M), mgGrpBody(M, g))' 'Object.assign({ keep: g.keep, merge: mgGone(g).map((x) => x.uid) }, mgGrpBody(M, g))'
# PGMO8 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k '${mgPvHtml(A)}${mgOptHtml(A)}${A.mx.msg ?' '${mgPvHtml(A)}${A.mx.msg ?'
# PGMO9 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k '    if (mgOptClick(A, x)) return true;' ' '
# PGMO10 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'const MG_WHY = { batch_unknown: '\''找不到這批合併紀錄'\'', plan_changed:' 'const MG_WHY = { plan_changed:'
# PGMO11 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'L = r && Array.isArray(r.batches) ? r.batches : [];' 'L = (r && r.items) || [];'
# PGMO12 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'const notes = M.kind === '\''brain'\'' ?' 'const notes = M.kind ?'
# PGMO13 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k '${o.notes === '\''copy'\'' && !ncBad ? '\''checked'\'' : '\'''\''}' '${o.notes === '\''copy'\'' ? '\''checked'\'' : '\'''\''}'
# PGMO14 (f163c r5e N2 / N3)
k 'hasAct('\''merge_preview'\'') === true && hasAct('\''merge_run'\'') === true && !mgCanRun(A)' 'hasAct('\''merge_preview'\'') === true && !mgCanRun(A)'
# PGMO15 (f163c r5e N2 / N3)
k '(Number.isFinite(Number(r.stopped_at)) && r.stopped_at != null ? '\''(停在第 '\'' + Number(r.stopped_at) + '\'' 步)'\'' : '\'''\'')' ''\'''\'''
# PGMO16 (6770f IFACE-merge r3:永久封存 forever)
k 'const MG_DAYS = [7, 30, 90, '\''forever'\''];' 'const MG_DAYS = [7, 30, 90];'
# PGMO17 (6770f IFACE-merge r3:永久封存 forever)
k 'const n = d.dataset.apmgd === '\''forever'\'' ? '\''forever'\'' : Number(d.dataset.apmgd);' 'const n = Number(d.dataset.apmgd);'
# PGMO18 (6770f IFACE-merge r3:永久封存 forever)
k 'until = b.keep_until === null ? '\''永久封存,隨時可整筆還原'\'' :' 'until ='
# PGMO19 (6770f IFACE-merge r3:永久封存 forever)
k '${n === '\''forever'\'' ? '\''永久'\'' : n + '\'' 天'\''}' '${n + '\'' 天'\''}'
# PGMO20 (f163c r5e L1:筆記不能搬就不送 copy)
k 'o.notes === '\''copy'\'' && !M.ncBad) b.notes' 'o.notes === '\''copy'\'') b.notes'
# PGMO21 (f163c r5e L1:筆記不能搬就不送 copy)
k 'if (seq === M.pvSeq && !M.ncBad && M.pv && M.kind === '\''brain'\'' && (M.opt || {}).notes === '\''copy'\'' && M.pv.notes_check && (M.pv.notes_check.cloud_placeholders > 0 || M.pv.notes_check.over_limit)) { M.ncBad = M.pv.notes_check; M.opt.notes = undefined; M.pvBusy = false; return mgPreview(A); }' ' '
# PGMO22 (f163c r5e L1:筆記不能搬就不送 copy)
k 'M.ncBad = M.pv.notes_check; M.opt.notes = undefined;' 'M.ncBad = M.pv.notes_check;'
# PGMO23 (f163c r5e L1:筆記不能搬就不送 copy)
k 'nc = M.ncBad || null, ncBad = !!nc;' 'nc = null, ncBad = false;'
# PGMO24 (f163c 8f8e N1:換組清 ncBad)
k 'A.mx.done = null; A.mx.ncBad = null; A.mx.gopt = null; render(); mgPreview(A);' 'A.mx.done = null; A.mx.gopt = null; render(); mgPreview(A);'
# PGMO25 (f163c 8f8e N1:換組清 ncBad)
k 'A.mx.pvBusy = false; A.mx.ncBad = null;' 'A.mx.pvBusy = false;'
# PGNC15 (3e53b 4efe41d99:收回 not_on_list)
k 'not_on_list: '\''這個位址已經不在清單上(可能已收回或這一位重開過)'\'', ' ''
# PGNC16 (3e53b IFACE-netcard r3:owner_check_not_approved 卡還在)
k 'owner_check_not_approved: '\''指紋沒過,這張卡只剩拒絕;夥伴再連會出新的一張'\'' ' ' '
# PGNC17 (f163c r5km L1:驗證沒過只剩拒絕)
k '.filter((o) => !asked || (o && o.id === '\''deny'\'')).map((o) =>' '.map((o) =>'
# PGNC18 (f163c r5km L1:驗證沒過只剩拒絕)
k 'if (!o || (N.asked && N.asked[rid] && choice !== '\''deny'\'')) return;' 'if (!o) return;'
# PGNC19 (f163c r5km L1:驗證沒過只剩拒絕)
k 'if (cd === '\''owner_check_not_approved'\'') (N.asked = N.asked || {})[rid] = true;' ' '
# PGPP1 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'if (!(r && r.state === '\''written'\'')) throw' 'if (!r) throw'
# PGPP2 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '{ request_id: rid, diff_sha: String(p.diff_sha) }' '{ request_id: rid }'
# PGPP3 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'await act(PP_ACTS.deny, { ids: [rid] })' 'await act(PP_ACTS.deny, { ids: rid })'
# PGPP4 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'if (!o || o.code || !o.state) {' 'if (!o) {'
# PGPP5 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'if (PP_GONE.has(rgCode(e))) {' 'if (false) {'
# PGPP6 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '${esc(rgShow(String((l && l.text) || '\'''\'')))}' '${String((l && l.text) || '\'''\'')}'
# PGPP7 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '${esc(String(p.why))}' '${String(p.why)}'
# PGPP8 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'const ppHas = (k) => hasAct(PP_ACTS[k]) === true;' 'const ppHas = (k) => true;'
# PGPP9 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'if (!p || P.ans || !ppHas('\''approve'\'')) return;' 'if (!p || P.ans) return;'
# PGPP10 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '.filter((x) => x && x.request_id && x.diff_sha).slice(0, 20)' '.slice(0, 20)'
# PGPP11 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '['\''對照碼'\'', String(p.diff12 || String(b.diff_sha || '\'''\'').slice(0, 12)), true]' '['\''對照碼'\'', '\'''\'', true]'
# PGPP12 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '${P.ans || !canOk ? '\''disabled'\'' : '\'''\''}' '${!canOk ? '\''disabled'\'' : '\'''\''}'
# PGPP13 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'if (ppo) return ppApprove(ppo.dataset.ppok);' 'if (ppo) return;'
# PGPP14 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'op === '\''del'\'' ? '\''del'\'' : '\'''\''}">' 'op === '\''del'\'' ? '\'''\'' : '\'''\''}">'
# PGPP15 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k ' owner_check_not_approved: '\''指紋沒過,沒有寫回;提案還在,可以再按一次'\'',' ' '
# PGPP16 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k '${ppHtml()}</div>' '</div>'
# PGPP17 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'if (!p || P.ans || !ppHas('\''deny'\'')) return;' 'if (!p || P.ans) return;'
# PGPP18 (c2f0b S2-2 寫回人格核准卡(1000abaa95))
k 'P.items = (P.items || []).filter((x) => x.request_id !== rid); P.msg = { text: '\''已拒絕 '\''' 'P.msg = { text: '\''已拒絕 '\'''
# PGMK1 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'if (nm && nm !== mgOrig(g)) b.name = nm;' 'if (nm) b.name = nm;'
# PGMK2 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'if (M.kind === f && Object.keys(ch).length) b[k] = Object.assign({}, pg[k] || {}, ch); }' 'if (M.kind === f && Object.keys(ch).length) b[k] = Object.assign({}, ch); }'
# PGMK3 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'for (const [k, f] of [['\''seat_to'\'', '\''persona'\''], ['\''rebind'\'', '\''brain'\'']]) { const ch = q[k] || {}; if (M.kind === f &&' 'for (const [k, f] of [['\''seat_to'\'', '\''persona'\''], ['\''rebind'\'', '\''brain'\'']]) { const ch = q[k] || {}; if ('
# PGMK4 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'const mgClean = (t) => Array.from(String(t || '\'''\'').replace(new RegExp(AIB_UNSEEN, '\''gu'\''), '\'''\'').trim())' 'const mgClean = (t) => Array.from(String(t || '\'''\'').trim())'
# PGMK5 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'String(t || '\'''\'').replace(new RegExp(AIB_UNSEEN, '\''gu'\''), '\'''\'').trim()).slice(0, 20)' 'String(t || '\'''\'').replace(/[\u0000-\u001f\u007f\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/g, '\'''\'').trim()).slice(0, 20)'
# PGMK6 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'opts = own.filter((x) => !gone.has(x.uid));' 'opts = own;'
# PGMK7 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'const groups = R.map((g) => Object.assign({ keep: g.keep, merge: mgGone(g).map((x) => x.uid) }, mgOptBody(M), mgGrpBody(M, g)));' 'const groups = R.map((g) => Object.assign({ keep: g.keep, merge: mgGone(g).map((x) => x.uid) }, mgOptBody(M)));'
# PGMK8 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'A.mx.ncBad = null; A.mx.gopt = null; render(); mgPreview(A); return true; }' 'A.mx.ncBad = null; render(); mgPreview(A); return true; }'
# PGMK9 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'if (!M || M.done || M.pvBusy || hasAct('\''merge_preview'\'') !== true) return false; // 算預覽時、送出後不收' 'if (!M) return false; // 算預覽時、送出後不收'
# PGMK10 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'const M = A.mx; if (hasAct('\''merge_preview'\'') !== true || M.done || !M.pv || !M.pv.plan) return '\'''\'';' 'const M = A.mx; if (M.done || !M.pv || !M.pv.plan) return '\'''\'';'
# PGMK11 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k '${mgGrpHtml(A)}`;' '`;'
# PGMK12 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'const st = M.kind === '\''persona'\'' ? Object.keys(pg.seat_to || {})' 'const st = true ? Object.keys(pg.seat_to || {})'
# PGMK13 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'const rb = M.kind === '\''brain'\'' ? Object.keys(pg.rebind || {})' 'const rb = true ? Object.keys(pg.rebind || {})'
# PGMK14 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k '(q[k] = q[k] || {})[v.slice(i + 1)] = String(t.value); mgPreview(A); return true; }' '(q[k] = q[k] || {})[v.slice(i + 1)] = String(t.value); return true; }'
# PGMK15 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'const L = opts.some((x) => x.uid === cur) ? opts : [{ uid: cur, name: cur }].concat(opts);' 'const L = opts;'
# PGMK16 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k '${esc(pn.get(pu) || pu)}' '${esc(pu)}'
# PGPW1 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'if (p.swapped === true) return { bad: true, text: '\''密語紀錄被換過' 'if (p.swapped === 1) return { bad: true, text: '\''密語紀錄被換過'
# PGPW2 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'if (p.available !== true) return { text: (typeof fpWin' 'if (false) return { text: (typeof fpWin'
# PGPW3 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'if (p.set !== true) return { text: '\''密語備援還沒設定' 'if (p.set === false && p.set_at) return { text: '\''密語備援還沒設定'
# PGPW4 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'if (p.locked === true) { const u' 'if (false) { const u'
# PGPW5 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k ''\'' · 連錯 '\'' + w + '\'' 次,還能錯 '\'' + Math.max(0, Number(p.tries_left) || 0) + '\'' 次'\''' ''\'' · 連錯 '\'' + w + '\'' 次,還能錯 '\'' + (5 - w) + '\'' 次'\'''
# PGPW6 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'const PW_WHY = { passphrase_built_in_page_only: '\''只有這台電腦內建的頁面看得到'\'',' 'const PW_WHY = {'
# PGPW7 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'if (hasAct(PW_ACT) !== true) return '\''<div class="apn">密語備援:這一版 AIB 還沒有</div>'\'';' ' '
# PGPW8 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'const t = pwText(W.st); return `<div class="${t.bad ? '\''trm bad'\'' : '\''apn'\''}">${esc(t.text)}</div>`;' 'const t = pwText(W.st); return `<div class="apn">${esc(t.text)}</div>`;'
# PGPW9 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'try { const r = await act(PW_ACT, {}); W.st = r && typeof r === '\''object'\'' ? r : null; W.err = null; }' 'try { const r = await act(PW_ACT, { all: true }); W.st = r && typeof r === '\''object'\'' ? r : null; W.err = null; }'
# PGPW10 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k '<div class="aplab">🔑 密語備援</div>${pwHtml()}</div>' ' '
# PGPW11 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k '(m > 0 ? '\''(約 '\'' + m + '\'' 分)'\'' : '\'''\'')' ''\'''\'''
# PGPW12 (c2f0b IFACE-passphrase r2 密語備援狀態一行)
k 'if (W.err) return `<div class="trm bad">密語狀態讀不到:${esc(W.err)}</div>`;' ' '
# PGNC20 (3e53b 例外卡綁位址 + 埠說明(f163c sandboxnet19 N2))
k '${!asked && (c.choices || []).some((o) => o && o.id !== '\''deny'\'' && o.id !== '\''once'\'') ? `<div class="apn">${esc((c.choices || []).some((o) => o && o.id === '\''always'\'') ? NET_BIND : NET_BIND_NA)}</div>` : '\'''\''}' ' '
# PGNC21 (3e53b 例外卡綁位址 + 埠說明(f163c sandboxnet19 N2))
k '${!asked && (c.choices || []).some(' '${(c.choices || []).some('
