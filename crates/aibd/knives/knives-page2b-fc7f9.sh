#!/usr/bin/env bash
# PT / RG / HS (fc7f9 11-23, 頁面第二輪 S3 權限模板 / S4 人格·大腦·角色 改與封存 / S2 管理主機;ORDER-page2-ebab8-fc7f9-1123).
# Second half of knives-page2-fc7f9.sh (split 11-24 by ebab8: one script ran past its 30m watchdog on PR 881, 203 of 286 knives done, all bit).
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs §37 §38 §39) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGQA6 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '<div class="qw${pct >= 90 ? '\'' red'\'' : pct >= 70 ? '\'' warn'\'' : '\'''\''}">' '<div class="qw">'
# PGQA7 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k 'const at = qwWhen(w && w.resets_at), st = w && w.status ? QW_ST[w.status] || String(w.status) : '\'''\'';' 'const at = qwWhen(w && w.resets_at), st = '\'''\'';'
# PGQA8 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k 'q.plan ? '\''方案 '\'' + String(q.plan) : '\'''\'',' ''\'''\'','
# PGQA9 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '<span>${esc(label)}</span>' '<span>${label}</span>'
# PGQA10 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '${String(p.id).toLowerCase() === '\''claude'\'' ? '\''、等它跑完第一個回合'\'' : '\'''\''}' '、等它跑完第一個回合'
# PGQA11 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '<div class="apn">帳號額度(AIB 從 ${esc(p.name)} 自己回報的數字讀,只有用了幾 %,沒有餘額)' '<div class="apn">帳號額度'
# PGNC1 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '      const r = await fingerAct(NET_ACTS.answer, { request_id: rid, choice });' '      const r = o.needs_owner ? await fingerAct(NET_ACTS.answer, { request_id: rid, choice }) : await act(NET_ACTS.answer, { request_id: rid, choice });'
# PGNC2 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '${o.needs_owner ? '\'' · 要驗證'\'' : '\'''\''}' ''
# PGNC3 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k 'const why = c.source === '\''net_ask'\'' && c.why ? `<div class="netwhy"><i>夥伴寫的理由:</i>${esc(String(c.why))}</div>` : '\'''\'';' 'const why = c.why ? `<div class="netwhy"><i>夥伴寫的理由:</i>${String(c.why)}</div>` : '\'''\'';'
# PGNC4 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '${cell === '\''hint'\'' ? `<div class="apn apwarn">${esc(NET_HINT)}</div>` : '\'''\''}' ''
# PGNC5 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k 'if (cd === '\''net_card_expired'\'' || cd === '\''net_card_used'\'' || cd === '\''net_card_unknown'\'') N.items = (N.items || []).filter((x) => x.request_id !== rid);' ' '
# PGNC6 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k 'const o = (c.choices || []).find((x) => x && x.id === choice); if (!o || (N.asked && N.asked[rid] && choice !== '\''deny'\'')) return;' 'const o = (c.choices || []).find((x) => x && x.id === choice) || { id: choice, words: choice }; if (N.asked && N.asked[rid] && choice !== '\''deny'\'') return;'
# PGNC7 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k 'N.amsg = { seat: sid, text: '\''已收回 '\'' + ip + '\'':'\'' + port + '\'',正在連的已斷('\'' + (Number(r && r.closed) || 0) + '\'' 條)'\'' };' 'N.amsg = { seat: sid, text: '\''已收回 '\'' + ip + '\'':'\'' + port };'
# PGNC8 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '{ seat_id: sid, ip, port: Number(port) }' '{ seat_id: sid, ip, port }'
# PGNC9 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '    if (!netHas('\''list'\'')) return `<div class="apn">${esc(AP_ASK_NO)}</div>`;' ' '
# PGNC10 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '    if (!netHas('\''allow'\'')) return '\''<div class="apn">這一版 AIB 還沒有</div>'\'';' ' '
# PGNC11 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '+ '\''<div class="apn">「到這一位關掉」那種不在這裡:這一位結束或重開就沒了</div>'\''' ''
# PGNC12 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '['\''目的地'\'', String(c.dest_ip || '\'''\'') + '\'':'\'' + String(c.dest_port || '\'''\''), true]' '['\''目的地'\'', String(c.dest_ip || '\'''\''), true]'
# PGNC13 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '<div class="aplab">⚠️ 需要你決定</div>${netAskHtml()}</div>' '<div class="aplab">⚠️ 需要你決定</div><div class="apn">${esc(AP_ASK_NO)}</div></div>'
# PGNC14 (3e53b IFACE-netcard r0:網路例外卡 + 一直允許清單)
k '<div class="aplab">一直允許的位址</div>${netAllowHtml(id)}</div>' '<div class="aplab">一直允許的位址</div><div class="apn">這一版 AIB 還沒有</div></div>'
# PGMG1 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'if (x.uid !== keep && !tk.has(x.uid)) off[x.uid] = true;' 'if (x.uid !== keep && tk.has(x.uid)) off[x.uid] = true;'
# PGMG2 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'keep = items.some((x) => x.uid === String(g.keep)) ? String(g.keep) : (items[0] && items[0].uid)' 'keep = items[0] && items[0].uid'
# PGMG3 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'use: Number(x.used) || 0, lock: !!x.builtin' 'use: 0, lock: !!x.builtin'
# PGMG4 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '}).filter((g) => g.items.length > 1);' '});'
# PGMG5 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '    if (hasAct('\''merge_find'\'') !== true) return mgFind(kind);' ' '
# PGMG6 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'if (v === undefined) { C[kind] = null; act(' 'if (v === undefined || v === null) { C[kind] = null; act('
# PGMG7 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '.catch((e) => { C[kind] = { err: rgWhy(e) }; render(); })' '.catch(() => { C[kind] = []; render(); })'
# PGMG8 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'if (S.mgAib && A.merge) delete S.mgAib[k];' ''
# PGMG9 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '${g.low != null && g.low < 1 ? '\'' · 相似度 '\'' + Math.round(g.low * 100) + '\''%'\'' : '\'''\''}' ''
# PGMG10 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '${esc(mgWait(kind) || '\''沒有找到名字很像的'\'' + nm)}' '沒有找到名字很像的${nm}'
# PGMG11 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '(下次開才換,正在跑的不會中途換)' ''
# PGMG12 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k '</button>${mgNextHtml(x)}${on ?' '</button>${on ?'
# PGMG13 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'if (!p && !b) return '\'''\'';' ' '
# PGMG14 (6770f IFACE-merge r0:merge_find 分組 + 下次開換成)
k 'b ? '\''第二大腦「'\'' + esc(b) + '\''」'\'' : '\'''\''' ''\'''\'''
# PGND1 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k '    if (!(S.netCards > prev)) return;' ' '
# PGND2 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k 'if (S.add && S.add.mine) { if (netHas('\''list'\'')) netLoad(); return; }' 'if (S.add && S.add.mine) { return; }'
# PGND3 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k 'say(NET_NAG + '\''('\'' + S.netCards + '\'' 張,點「+ AI 夥伴」看)'\'', '\''warn'\'');' 'say(NET_NAG, '\''warn'\'');'
# PGND4 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k 'S.netCards = typeof n === '\''number'\'' && Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;' 'S.netCards = Number(n) || 0;'
# PGND5 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k '${S.netCards > 9 ? '\''9+'\'' : S.netCards}' '${S.netCards}'
# PGND6 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k 'const netDot = () => (S.netCards > 0 ?' 'const netDot = () => (S.netCards >= 0 ?'
# PGND7 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k 'return s <= 0 ? '\''已逾時,拒絕了'\'' : s <= 10 ?' 'return s <= 10 ?'
# PGND8 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k '    netSeen(seats && seats.net_cards); // 3e53b net_cards 張數' ' '
# PGND9 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k '      if (S.netCards > 0) S.add.mine = true; // 有連線卡等你決定 ⇒ 直接展開「我的 AI 夥伴」' ' '
# PGND10 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k '<b>我的 AI 夥伴 · 隨時改權限</b>${netDot()}' '<b>我的 AI 夥伴 · 隨時改權限</b>'
# PGND11 (3e53b IFACE-netcard r1:net_cards 紅點 + 提示 + 每秒倒數)
k 'b.textContent = netLeft(Number(b.dataset.netexp)); }, 1000);' 'b.textContent = netLeft(Number(b.dataset.netexp)); }, 5000);'
# PGMG15 (f163c r5bc N1:low=null 不顯示相似度 0%)
k 'low: typeof g.low === '\''number'\'' && Number.isFinite(g.low) ? g.low : null' 'low: Number.isFinite(Number(g.low)) ? Number(g.low) : null'
# PGMR1 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'const o = M.opt || {}, b = {};' 'const o = M.opt || {}, b = { days: 30 };'
# PGMR2 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'await fingerAct('\''merge_run'\'', { plan_sha: P.plan_sha, plan: P.plan });' 'await fingerAct('\''merge_run'\'', { plan_sha: P.plan_sha, plan: { kind: M.kind } });'
# PGMR3 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'const r = await fingerAct('\''merge_run'\'',' 'const r = await act('\''merge_run'\'','
# PGMR4 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'if (rgCode(e) === '\''plan_changed'\'') { M.done = null; M.msg = MG_WHY.plan_changed; mgPreview(A); return; }' ' '
# PGMR5 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'if (r && r.state === '\''failed'\'') {' 'if (false) {'
# PGMR6 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'M.done.log = L.find((x) => x && x.batch_id === batch) || M.done.log;' 'M.done.log = L[0] || M.done.log;'
# PGMR7 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'if (!M.done.err && (!M.done.log || M.done.log.state === '\''run'\'') && n < 150) setTimeout(' 'if (false) setTimeout('
# PGMR8 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'const ub = b.state === '\''done'\'' && b.can_undo && hasAct('\''merge_undo'\'') === true ?' 'const ub = b.state === '\''done'\'' ?'
# PGMR9 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'M.done.kept = Array.isArray(r && r.kept_changed) ? r.kept_changed.slice(0, 50) : [];' ' '
# PGMR10 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'const mgCanRun = (A) => hasAct('\''merge_run'\'') === true && !!(A.mx && A.mx.pv && A.mx.pv.plan_sha && A.mx.pv.plan);' 'const mgCanRun = (A) => hasAct('\''merge_run'\'') === true;'
# PGMR11 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k '${P.needs_owner && !M.done ? `<div class="apn">要你本人驗證;系統視窗會寫對照碼 <code>${esc(String(P.window_sha12 || '\'''\''))}</code>,跟這裡一樣才按</div>` : '\'''\''}' ''
# PGMR12 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k '${hasAct('\''merge_preview'\'') === true && hasAct('\''merge_run'\'') === true && !mgCanRun(A) ? '\''disabled'\'' : '\'''\''}' ''
# PGMR13 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'if (mgCanRun(A)) { mgRun(A); return true; } A.mx.msg = MG_NO;' 'A.mx.msg = MG_NO;'
# PGMR14 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k '['\''對照碼'\'', code, true]' '['\''對照碼'\'', '\'''\'', true]'
# PGMR16 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'A.mx.msg = '\'''\''; A.mx.done = null; A.mx.ncBad = null; A.mx.gopt = null; render(); mgPreview(A); return true; }' 'A.mx.msg = '\'''\''; A.mx.done = null; A.mx.ncBad = null; A.mx.gopt = null; render(); return true; }'
# PGMR17 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k '${steps.map((s) => `<li>${MG_ST[s.state] || '\''…'\''} ' '${steps.map((s) => `<li>${'\''…'\''} '
# PGMR18 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'if (seq === M.pvSeq) M.pv = r && typeof r === '\''object'\'' ? r : null;' 'M.pv = r && typeof r === '\''object'\'' ? r : null;'
# PGMR19 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k 'A.mx.pvBusy = false; A.mx.ncBad = null; A.mx.gopt = null; A.mx.pvSeq' 'A.mx.ncBad = null; A.mx.gopt = null; A.mx.pvSeq'
# PGMR20 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k '${P.needs_owner && !M.done ?' '${P.needs_owner ?'
# PGMR21 (6770f IFACE-merge r1:預覽 / 執行 / 紀錄 / 還原)
k '${A.mx.done ? '\''已送出,看下面進度'\'' : '\''還沒動任何東西'\''}' '還沒動任何東西'
# PGMO1 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'if (MG_DAYS.includes(o.days)) b.days = o.days;' ' '
# PGMO2 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'if (M.kind === '\''persona'\'' && o.card === '\''propose'\'') b.card = '\''propose'\'';' 'if (o.card === '\''propose'\'') b.card = '\''propose'\'';'
# PGMO3 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'if (M.kind === '\''brain'\'' && o.notes === '\''copy'\'' && !M.ncBad) b.notes = '\''copy'\'';' 'if (o.notes === '\''copy'\'' && !M.ncBad) b.notes = '\''copy'\'';'
# PGMO4 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'o.days = n === 30 ? undefined : n;' 'o.days = n;'
# PGMO5 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k 'o.card = c.checked ? '\''propose'\'' : undefined; mgPreview(A);' 'o.card = '\''propose'\''; mgPreview(A);'
# PGMO6 (6770f IFACE-merge r1/r2:可改欄位 + batches)
k '${M.pvBusy || ncBad ? '\''disabled'\'' : '\'''\''}' '${M.pvBusy ? '\''disabled'\'' : '\'''\''}'
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
k '.replace(/[\u0000-\u001f\u007f]/g, '\'''\'').trim()).slice(0, 20).join('\'''\'');' '.trim()).slice(0, 20).join('\'''\'');'
# PGMK5 (6770f IFACE-merge r3 §2 每組可改 name / seat_to / rebind)
k 'String(t || '\'''\'').replace(/[\u0000-\u001f\u007f]/g, '\'''\'').trim()).slice(0, 20)' 'String(t || '\'''\'').replace(/[\u0000-\u001f\u007f]/g, '\'''\'').trim()).slice(0, 40)'
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
k 'if (p.available !== true) return { text: '\''這台用系統本人驗證' 'if (false) return { text: '\''這台用系統本人驗證'
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
k '${!asked && (c.choices || []).some((o) => o && o.id !== '\''deny'\'' && o.id !== '\''once'\'') ? `<div class="apn">${esc(NET_BIND)}</div>` : '\'''\''}' ' '
# PGNC21 (3e53b 例外卡綁位址 + 埠說明(f163c sandboxnet19 N2))
k '${!asked && (c.choices || []).some(' '${(c.choices || []).some('
