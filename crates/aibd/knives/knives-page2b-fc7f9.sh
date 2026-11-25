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
