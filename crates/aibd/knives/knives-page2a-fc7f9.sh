#!/usr/bin/env bash
# PT / RG / HS (fc7f9 11-23, 頁面第二輪 S3 權限模板 / S4 人格·大腦·角色 改與封存 / S2 管理主機;ORDER-page2-ebab8-fc7f9-1123).
# Second half of knives-page2-fc7f9.sh (split again 11-25 by fc7f9 for ebab8: 142 knives took 695 s on CI and 1321 s on a slow runner, past the tightened per-script watchdog of #884; 71 + 71, knife lines unchanged).
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs §37 §38 §39) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGSD4 (S1d:Andy 04:4x 品牌改下拉(品牌 · 預設模型))
k 'const apPvLabel = (p) => p.name + '\'' · '\'' + (dfltModel(p.id) || '\''它自己的預設模型'\'');' 'const apPvLabel = (p) => p.name + '\'' · '\'' + ('\''claude-opus-5-5'\'');'
# PGSE1 (S1d:ebab8 04:4x 套模板進我的 AI 夥伴 + 可管的主機各模式都有)
k '${A.mode === '\''host'\'' ? hsRow(A) : hsRowOff(A)}' '${A.mode === '\''host'\'' ? hsRow(A) : '\'''\''}'
# PGSE2 (S1d:ebab8 04:4x 套模板進我的 AI 夥伴 + 可管的主機各模式都有)
k 'S.pt.seat = S.add.mineSeat; S.pt.seatMsg = null;' 'S.pt.seatMsg = null;'
# PGSE3 (S1d:ebab8 04:4x 套模板進我的 AI 夥伴 + 可管的主機各模式都有)
k '<button type="button" class="apr" data-hsoff=' '<button type="button" class="apr" data-hspick='
# PGSE4 (S1d:ebab8 04:4x 套模板進我的 AI 夥伴 + 可管的主機各模式都有)
k '${ptSeatBox(x) || `<div class="apn apwarn">' '${'\'''\'' || `<div class="apn apwarn">'
# PGSG1 (S1d:改人格三欄(08de5 persona_read / persona_set 新形狀))
k 'if (t.split('\''\n'\'').some((l) => /^\s*(#|---)/.test(l)))' 'if (t.split('\''\n'\'').some((l) => /^\s*(@@@)/.test(l)))'
# PGSG2 (S1d:改人格三欄(08de5 persona_read / persona_set 新形狀))
k '{ persona_uid: R.uid, base_sha: R.sha }, ...chg' '{ persona_uid: R.uid }, ...chg'
# PGSG3 (S1d:改人格三欄(08de5 persona_read / persona_set 新形狀))
k 'if (card && rgCode(e) === '\''persona_changed'\'') { R.review = false; return rgCardLoad(R); }' ' '
# PGSG4 (S1d:改人格三欄(08de5 persona_read / persona_set 新形狀))
k 'if (S.rg && ed && kind === '\''persona'\'' && rgCardOn()) await rgCardLoad(S.rg);' ' '
# PGSG5 (S1d:改人格三欄(08de5 persona_read / persona_set 新形狀))
k 'const on = rgCardOn() && !!R.sha,' 'const on = false,'
# PGSH1 (S1d:ORDER-s1fix 第 7 條 席位窗上方模式與危險程度)
k '.join('\'' · '\''))}</small>${seatModeTag(s.mode)}' '.join('\'' · '\''))}</small>'
# PGSH2 (S1d:ORDER-s1fix 第 7 條 席位窗上方模式與危險程度)
k 'const k = m === '\''host'\'' ? ['\''管理主機'\'', '\''高'\'', 3] :' 'const k = m === '\''nope'\'' ? ['\''管理主機'\'', '\''高'\'', 3] :'
# PGSI1 (S1e:f163c 05:07 改人格只留名稱 + 三欄)
k 'kind === '\''persona'\'' && rgCardOn() && R.sha ? '\'''\'' :' 'false ? '\'''\'' :'
# PGSJ1 (S1f:08de5 A13 模型清單 + Andy 05:1x 按品牌就重讀模型與用量)
k 'if (S.add.rowOpen === '\''brand'\'') apPvRefresh();' ' '
# PGSJ2 (S1f:08de5 A13 模型清單 + Andy 05:1x 按品牌就重讀模型與用量)
k 'if (apPvModel(pv, A)) body.model = A.model;' ' '
# PGSJ3 (S1f:08de5 A13 模型清單 + Andy 05:1x 按品牌就重讀模型與用量)
k '(p.models || []).some((m) => m.id === A.model) ? A.model : '\'''\'');' 'true ? A.model : '\'''\'');'
# PGSJ4 (S1f:08de5 A13 模型清單 + Andy 05:1x 按品牌就重讀模型與用量)
k '${m.dflt ? '\''(預設)'\'' : '\'''\''}${m.used ? '\''(用過)'\'' : '\'''\''}' '${m.used ? '\''(用過)'\'' : '\'''\''}'
# PGSJ5 (S1f:08de5 A13 模型清單 + Andy 05:1x 按品牌就重讀模型與用量)
k 'const [v, u] = await Promise.all([soft(get('\''/api/v1/version'\'')).catch(() => null), soft(get('\''/api/v1/usage'\'')).catch(() => null)]);' 'const [v, u] = await Promise.all([soft(get('\''/api/v1/version'\'')).catch(() => null), null]);'
# PGSK1 (S1f:f163c L1 伺服器 / 測試機兩組)
k '<div class="aplab apgold">伺服器</div>${chips}' '${chips}'
# PGSK2 (S1f:f163c L1 伺服器 / 測試機兩組)
k 'if (x.closest('\''[data-hstest]'\'')) { S.add.hsOffMsg = HS_TEST_NO; return render(); }' ' '
# PGNK1 (S1g:3e53b capabilities.net(sandboxnet r7 §16)+ Andy 05:5x DECISION 十七 Windows 只是提示 + f163c 討論研究不能上網)
k 'if (nv) return `<i class=' 'if (false) return `<i class='
# PGNK2 (S1g:3e53b capabilities.net(sandboxnet r7 §16)+ Andy 05:5x DECISION 十七 Windows 只是提示 + f163c 討論研究不能上網)
k '<i>不碰檔案、不跑指令${apNetWords(pv, '\''safe'\'') ? '\'';'\'' + esc(apNetWords(pv, '\''safe'\'')) : '\'''\''}</i>' '<i>不碰檔案、不跑指令</i>'
# PGNK3 (S1g:3e53b capabilities.net(sandboxnet r7 §16)+ Andy 05:5x DECISION 十七 Windows 只是提示 + f163c 討論研究不能上網)
k 'hint: '\''能上網;網路規則只是提示,擋不住;連得到這台電腦上的本機服務與內網'\''' 'hint: '\''能上網'\'''
# PGNK4 (S1g:3e53b capabilities.net(sandboxnet r7 §16)+ Andy 05:5x DECISION 十七 Windows 只是提示 + f163c 討論研究不能上網)
k 'host: hsCan(pv) && !hsWinWait(), net: pv })' 'host: hsCan(pv) && !hsWinWait() })'
# PGNK5 (S1g:3e53b capabilities.net(sandboxnet r7 §16)+ Andy 05:5x DECISION 十七 Windows 只是提示 + f163c 討論研究不能上網)
k 'const v = n && typeof n === '\''object'\'' ? String(n[m] || '\'''\'') : '\'''\'';' 'const v = n && typeof n === '\''object'\'' ? String(n.work || '\'''\'') : '\'''\'';'
# PGML1 (f163c REVIEW-page2r8 L1(改人格卡看得到改誰、改了哪幾欄)+ 08de5 A14 mode_net 句子優先)
k '[['\''人格'\'', pn && pn !== b.name ? pn + '\''('\'' + u + '\'')'\'' : u, true], ' '['
# PGML2 (f163c REVIEW-page2r8 L1(改人格卡看得到改誰、改了哪幾欄)+ 08de5 A14 mode_net 句子優先)
k '['\''改了'\'', o ? ch || '\''沒有改動'\'' : '\'''\''], ' ''
# PGML3 (f163c REVIEW-page2r8 L1(改人格卡看得到改誰、改了哪幾欄)+ 08de5 A14 mode_net 句子優先)
k 'R.orig = { name: R.name, duty: R.duty, redlines: R.redlines, knowledge: R.knowledge }; }' '}'
# PGMN1 (f163c REVIEW-page2r8 L1(改人格卡看得到改誰、改了哪幾欄)+ 08de5 A14 mode_net 句子優先)
k 'if (typeof w === '\''string'\'' && w.trim()) return w.trim().slice(0, 200);' ' '
# PGHL1 (S6 接回:08de5 handoff_latest 帶最新交接單 / 記憶包)
k 'render(); hlLoad(A, code); const i' 'render(); const i'
# PGHL2 (S6 接回:08de5 handoff_latest 帶最新交接單 / 記憶包)
k ' && fl.includes('\''-'\'' + code + '\''-'\'') ? { file: fl' ' ? { file: fl'
# PGHL3 (S6 接回:08de5 handoff_latest 帶最新交接單 / 記憶包)
k 'if (h && (!A.ho || /HANDOFF-[0-9a-f]{5}-$/.test(A.ho))) A.ho' 'if (h) A.ho'
# PGHL4 (S6 接回:08de5 handoff_latest 帶最新交接單 / 記憶包)
k '    if (hasAct('\''handoff_latest'\'') !== true) return;' ' '
# PGHL5 (S6 接回:08de5 handoff_latest 帶最新交接單 / 記憶包)
k 'if (S.add !== A || A.from !== code) return;' ' '
# PGTH1 (3e53b DESIGN-hostmode 13.3/13.6:測試機交給夥伴管)
k '    if (hsHas('\''cand'\'')) {' '    if (false) {'
# PGTH2 (3e53b DESIGN-hostmode 13.3/13.6:測試機交給夥伴管)
k 'm.already ? `<span class="apr off"><b>${esc(m.name)}</b><i>已交給夥伴管</i></span>` : ' ''
# PGTH3 (3e53b DESIGN-hostmode 13.3/13.6:測試機交給夥伴管)
k 'fingerAct(HS_ACTS.add, { from: '\''trusted'\'', name })' 'fingerAct(HS_ACTS.add, { name })'
# PGTH4 (3e53b DESIGN-hostmode 13.3/13.6:測試機交給夥伴管)
k '<div class="apn">${esc(TH_EVID)}</div><div class="apl">' '<div class="apl">'
# PGTH5 (3e53b DESIGN-hostmode 13.3/13.6:測試機交給夥伴管)
k '['\''收據'\'', TH_EVID, true]' '['\''收據'\'', '\'''\'']'
# PGTH6 (3e53b DESIGN-hostmode 13.3/13.6:測試機交給夥伴管)
k 'const hta = x.closest('\''[data-hstadd]'\''); if (hta) return hsTestAdd(hta.dataset.hstadd);' ' '
# PGAN1 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k 'Object.assign({ persona_uid: R.uid, base_sha: R.sha }, ...chg.map((k) => ({ [k]: String(R[k] || '\'''\'') })))' 'Object.assign({ persona_uid: R.uid, base_sha: R.sha, name }, ...chg.map((k) => ({ [k]: String(R[k] || '\'''\'') })))'
# PGAN2 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k '    if (card && !chg.length) { R.msg = { text: '\''沒有改動,不用送'\'' }; R.review = false; return render(); }' ' '
# PGAN3 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k '    if (card && !R.review) { R.review = true; R.msg = null; return render(); }' ' '
# PGAN4 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k '<div class="apn"><b>送出前看一次</b>:指紋窗只列段名與字數,內容以這裡為準</div>' ''
# PGAN5 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k '.replace(/[\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/g,' '.replace(/[\u00ad\u034f\u061c\u180e\u200c]/g,'
# PGAN6 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k 'if (S.rg.review) { S.rg.review = false; const d = document.querySelector('\''.rgdiff'\''); if (d) d.remove(); } ' ''
# PGAN7 (08de5 A1 + f163c REVIEW-page2r10 L1/L2:人格不送 name、只送改的段、送前看差異)
k '<input class="apq" ${kind === '\''persona'\'' && rgCardOn() && R.sha ? '\''hidden '\'' : '\'''\''}data-rgf="name"' '<input class="apq" data-rgf="name"'
# PGAN9 (08de5 IFACE-a1 補:section_hidden_char 頁面先擋)
k '    if (/[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/.test(t)) return '\''有看不見的零寬或方向字元,請刪掉再存'\'';' ' '
# PGAN10 (08de5 IFACE-a1 補:section_hidden_char 頁面先擋)
k 'section_hidden_char: '\''段落裡有看不見的零寬或方向字元(差異區標成 ⟦U+…⟧),刪掉再存'\'', ' ''
# PGAN11 (f163c page2r10 L4 / N1 / N2:逐行 LCS、補看不見字、交管卡指紋行)
k '    while (i < n || j < m) { if (i < n && j < m && A[i] === B[j]) { out.push(['\'' '\'', A[i]]); i++; j++; } else if (j >= m || (i < n && T[i + 1][j] >= T[i][j + 1])) out.push(['\''-'\'', A[i++]]); else out.push(['\''+'\'', B[j++]]); }' '    for (const l of A) if (!B.includes(l)) out.push(['\''-'\'', l]); const left = A.slice(); for (const l of B) { const k = left.indexOf(l); if (k >= 0) { left.splice(k, 1); out.push(['\'' '\'', l]); } else out.push(['\''+'\'', l]); }'
# PGAN12 (f163c page2r10 L4 / N1 / N2:逐行 LCS、補看不見字、交管卡指紋行)
k '.replace(/\r/g, '\''␍'\'')' ''
# PGAN13 (f163c page2r10 L4 / N1 / N2:逐行 LCS、補看不見字、交管卡指紋行)
k '[\u00ad\u034f\u061c\u180e\u200b-' '[\u034f\u061c\u180e\u200b-'
# PGAN14 (f163c page2r10 L4 / N1 / N2:逐行 LCS、補看不見字、交管卡指紋行)
k ', ['\''指紋'\'', '\''機器與金鑰指紋以系統視窗為準'\'']]]' ']]'
# PGCN1 (ebab8 1123:客戶名不寫死,從使用者自己的人格 / 大腦 / 專案名推)
k '    if (!best) return '\'''\''; const src = String(text); for (let i = 0; i < src.length; i++)' '    return '\'''\''; const src = String(text); for (let i = 0; i < src.length; i++)'
# PGCN2 (ebab8 1123:客戶名不寫死,從使用者自己的人格 / 大腦 / 專案名推)
k '    for (const x of CH_INT) for (const w of x.w.concat(x.kw, [x.topic])) t = t.split(w.toLowerCase()).join('\'' '\'');' ' '
# PGCN3 (ebab8 1123:客戶名不寫死,從使用者自己的人格 / 大腦 / 專案名推)
k '    const client = chClient(text, PS.map((p) => p.name).concat(BR.map((b) => b.name), nodeOpts().map((n) => n[1])));' '    const client = chClient(text, []);'
# PGAN15 (f163c page2r10 N3 / N4:大段不算 LCS、手動主機卡指紋行)
k '    if (n * m > 160000) return A.map((l) => ['\''-'\'', l]).concat(B.map((l) => ['\''+'\'', l]));' ' '
# PGAN16 (f163c page2r10 N3 / N4:大段不算 LCS、手動主機卡指紋行)
k '['\''金鑰檔'\'', b.key_path], ['\''指紋'\'', '\''機器與金鑰指紋以系統視窗為準'\''],' '['\''金鑰檔'\'', b.key_path],'
# PGFA1 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k 'await fingerAct('\''seat_restart'\'', { seat_id: rs.dataset.restart, idem_key: idem() });' 'await act('\''seat_restart'\'', { seat_id: rs.dataset.restart, idem_key: idem() });'
# PGFA2 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k 'const r = await fingerAct('\''seat_clear'\'', { seat_id: id, handoff_at: st.wrote, idem_key: K[id].key });' 'const r = await act('\''seat_clear'\'', { seat_id: id, handoff_at: st.wrote, idem_key: K[id].key });'
# PGFA3 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k 'await fingerAct("seat_model_set", { seat_id: M.seat, model: v, idem_key: idem() });' 'await act("seat_model_set", { seat_id: M.seat, model: v, idem_key: idem() });'
# PGFA4 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k 'const r = await fingerAct('\''seat_open'\'', mb, () => { if (S.add === A) { A.msg = FP_SEE; render(); } });' 'const r = mb.repo ? await fingerAct('\''seat_open'\'', mb, () => { if (S.add === A) { A.msg = FP_SEE; render(); } }) : await act('\''seat_open'\'', mb);'
# PGFA5 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k 'const r = await fingerAct('\''seat_open'\'', body, (t, sec) => { if (S.add === A) { A.msg = FP_SEE; render(); } });' 'const r = body.repo ? await fingerAct('\''seat_open'\'', body, (t, sec) => { if (S.add === A) { A.msg = FP_SEE; render(); } }) : await act('\''seat_open'\'', body);'
# PGFA6 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k '    seat_model_set: (b) => ['\''把 '\'' + fpAt(b.seat_id) + '\'' 換成 '\'' + b.model, [['\''夥伴'\'', fpAt(b.seat_id)], ['\''模型'\'', b.model, true],' '    seat_model_set: (b) => ['\''把 '\'' + fpAt(b.seat_id) + '\'' 換成 '\'' + b.model, [['\''夥伴'\'', fpAt(b.seat_id)], ['\''模型'\'', b.model],'
# PGFA7 (08de5 A11 / 90856 整合預檢:dgrs 席 seat_open / restart / model_set / clear 都走指紋)
k 'return Number.isFinite(t) ? new Date(t).toLocaleString('\''zh-TW'\'', { hour12: false }) : v ? String(v) : '\''(沒帶)'\''; };' 'return Number.isFinite(t) ? new Date(t).toLocaleString('\''zh-TW'\'', { hour12: false }) : '\''(沒帶)'\''; };'
# PGCN4 (f163c page2r11 N1:客戶名取原文位置)
k 'if (src.slice(i, j).toLowerCase() === best) return src.slice(i, j);' 'if (src.slice(i, j).toLowerCase() === best) return src.toLowerCase().slice(i, j);'
# PGQA1 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '    if (!P || typeof P !== '\''object'\'') return '\''<div class="apn">帳號額度:這一版 AIB 還沒有</div>'\'';' ' '
# PGQA2 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '    if (!q || !Array.isArray(q.windows)) return' '    if (!q) return'
# PGQA3 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k 'const pct = Math.max(0, Math.min(100, Number(w && w.used_pct) || 0))' 'const pct = Number(w && w.used_pct) || 0'
# PGQA4 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k ': QW_NAME[nm] || nm || '\''額度'\'';' ': nm || '\''額度'\'';'
# PGQA5 (08de5 A15:帳號額度(只有用了幾 %,沒有餘額))
k '(wm % 1440 === 0 ? wm / 1440 + '\'' 天'\'' :' '('
