#!/usr/bin/env bash
# aib-knives: os=unix
# 刀名一律 PG 前綴(下面寫的是舊名):SF1–SF64 + HS17 (no SF19; fc7f9 11-23; SF16 / SF30 / SF34 written for the page2 stack top — S3 template block, 逐項調整, S2 Windows line and host button sit in the mode row)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGSF1
k 'const at = sel >= 0 ? Math.min(AP_CATS.length - 1, Math.floor(sel / cols) * cols + cols - 1) : -1;' 'const at = sel >= 0 ? AP_CATS.length - 1 : -1;'
# PGSF2
k '<div class="apst">${C.ic} ${esc(C.zh)} 的方案</div>' ''
# PGSF3
k 'data-sty="animation-delay:${i * 45}ms"' ''
# PGSF4
k 'return g && g.clientWidth ? Math.max(1, Math.floor((g.clientWidth + 8) / 138)) : 4; }' 'return 4; }'
# PGSF5
k '    if (k) { d.scrollTop = k.top;' '    if (k) {'
# PGSF6
k 'const f = k.fk && d.querySelector(k.fk); if (f) {' 'const f = null; if (f) {'
# PGSF7
k '    apRestore(el, apK); // S1b 2:加夥伴視窗停在原地' '    // knife SF7'
# PGSF8
k 'if (r && r.scrollIntoView) r.scrollIntoView({ block: '\''nearest'\'' }); A.reveal = null;' 'A.reveal = null;'
# PGSF9
k 'S.add.reveal = !!S.add.rowOpen; render();' 'render();'
# PGSF10
k ' — 不能選:${esc(p.why || '\''這台還不能開'\'')}" disabled>' '" disabled>'
# PGSF11
k '    if (!A.dflt && pv) { A.dflt = true; if (canW) A.mode = '\''work'\''; }' '    if (!A.dflt && pv) { A.dflt = true; if (canW) { A.mode = '\''work'\''; if (WSO.length) A.withSeat = WSO[0].seat_id; } }'
# PGSF12
k 'hit = hits.find(([u]) => personaHasBrain(u)) || hits[0];' 'hit = hits[0];'
# PGSF13
k '    const apK = apKeep(el); // S1b 2' '    const apK = null; // S1b 2'
# PGSF14
k '${canD && !inPreview() ? '\'''\'' : '\''off'\''}" data-addm="dgrs" ${canD && !inPreview() ? '\'''\'' : '\''disabled aria-disabled="true"'\''}>' '${canD ? '\'''\'' : '\''off'\''}" data-addm="dgrs" ${canD ? '\'''\'' : '\''disabled'\''}>'
# PGSF15
k '${inPreview() ? '\''預覽區不能開完全權限,到正式版才行'\'' : canD' '${canD'
# PGSF16
k '<div class="amds">${mode}</div>${winHost && !(S.version && S.version.capabilities && (S.version.capabilities.net || S.version.capabilities.mode_net)) ? `<div class="apn">${esc(HS_WIN_NET)}</div>` : '\'''\''}${ptBlock(A)}${apCmp(A, { work: canW, dgrs: canD && !inPreview(), host: hsCan(pv) && !hsWinWait(), net: pv })}' '<div class="amds">${mode}</div>${winHost && !(S.version && S.version.capabilities && (S.version.capabilities.net || S.version.capabilities.mode_net)) ? `<div class="apn">${esc(HS_WIN_NET)}</div>` : '\'''\''}${ptBlock(A)}'
# PGSF17
k 'sel = AP_LEVEL[A.mode] ? A.mode : '\''safe'\'', s = (m) => (m === sel ? '\'' s'\'' : '\'''\''), can' 'sel = AP_LEVEL[A.mode] ? A.mode : '\''safe'\'', s = (m) => '\'''\'', can'
# PGSF18
k '<em class="adang d4">危險程度:最高</em></button>`; // S1b 5' '</button>`; // S1b 5'
# PGSF20
k 'return n ? ` · ${n} 個人格` : '\'' · <span class="apwarn">這台還沒有對應人格</span>'\''; }' 'return '\'''\''; }'
# PGSF21
k '<em class="${X.id === '\''sens'\'' ? '\''sens'\'' : '\'''\''}">${AP_NUM[X.id] || '\''—'\''}</em>' ''
# PGSF22
k '${esc(A.plan && pv ? apSum(A, pv) : '\''先選一個方案'\'')}' '${esc(A.plan && pv ? '\'''\'' : '\''先選一個方案'\'')}'
# PGSF23
k '<i class="apchev" aria-hidden="true">⌄</i></button><div class="aprb">' '<i>${on ? '\''收起'\'' : '\''改'\''}</i></button><div class="aprb">'
# PGSF24
k '<div class="apsrc">${esc(apSrc())}</div>' '<div class="apn apsub">選要做的事</div>'
# PGSF25
k ': /^[\w:.-]*$/.test(x.value) ? x.value : null; if (v == null) continue;' ': x.value;'
# PGSF26
k 'const v = typeof CSS !== '\''undefined'\'' && CSS.escape ? CSS.escape(x.value) :' 'const v ='
# PGSF27
k '<div class="asec"><span>在登記過的倉裡做(開自己的分支)</span>' '<div class="asec"><span>在哪裡改</span>'
# PGSF28
k '<div class="asec"><span>跟別席共用一個資料夾(同一份檔案,最多兩席在寫)</span>' '<div class="asec"><span>跟哪一席同一個資料夾</span>'
# PGSF29
k '</div></div>` : BK ? apBack(A) : '\'''\'';' '</div></div>` : '\'''\'';'
# PGSF30
k '<em class="adang d2">危險程度:中</em></button>${hsModeBtn(A, pv) || apHostCard(A, pv)}<button' '<em class="adang d2">危險程度:中</em></button><button'
# PGSF31
k 'can = (m) => !(ok && ok.ro) && (m === '\''safe'\'' || !!(ok && ok[m]));' 'can = (m) => !(ok && ok.ro) && true;'
# PGSF32
k 'v ? `<i class="y${s(m)}">✓${nt ? `<small>${nt}</small>` : '\'''\''}</i>`' 'v ? `<i class="y${s(m)}">✓</i>`'
# PGSF33
k 'if (k === '\''host'\'' && !v) return `<i class="p${s(m)}">另開⁴</i>`;' ''
# PGSF34
k 'data-addm="host" disabled aria-disabled="true"><span class="amh">${ICO_SRV}' 'data-addm="host"><span class="amh">${ICO_SRV}'
# PGSF35
k 'const mn = await apOpenMates(A, pv, body0, { nm, wait: !!sq });' 'const mn = '\'''\'';'
# PGSF36
k '!A.busy && (A.mates || []).length ? `開這一組(${(A.mates || []).length + 1} 位)` : step' 'step'
# PGSF37
k 'PS = personas().filter(([u]) => u !== A.persona);' 'PS = personas();'
# PGSF38
k '${mn ? '\'' + '\'' + mn + '\'' 搭檔'\'' : '\'''\''}' ''
# PGSF39
k '${apMine(A)}' ''
# PGSF40
k 'if (x.closest('\''[data-apmine]'\'')) { S.add.mine = !S.add.mine; return render(); }' ''
# PGSF41
k 'can = (m) => !(ok && ok.ro) && (m === '\''safe'\'' || !!(ok && ok[m]));' 'can = (m) => m === '\''safe'\'' || !!(ok && ok[m]);'
# PGSF42
k '<div class="apn apwarn">${esc(AP_MINE_NO)}</div>' ''
# PGSF43
k 'if (S.add.node && !S.add.role && roleList().some((r) => r.uid === '\''author'\'')) { S.add.role = '\''author'\''; S.add.roleAuto = true; }' ''
# PGSF44
k 'S.add.role = ro.dataset.addrole; S.add.roleAuto = false;' 'S.add.role = ro.dataset.addrole;'
# PGSF45
k ' : node && !A.role ? '\'';掛到專案要一個角色,請在角色那列選一個(不選就只開席、不掛)'\'' : '\'''\''}' ' : '\'''\''}'
# PGSF46
k '<b>不指定</b><i>自由發揮,不綁專案</i></button></div>' '</div>'
# PGSF47
k 'if (x.closest('\''[data-apmgok]'\'')) { if (mgCanRun(A)) { mgRun(A); return true; } A.mx.msg = MG_NO; render(); return true; }' ''
# PGSF48
k 'if (v >= 0.85) { hi.add(i); hi.add(j); }' ''
# PGSF49
k 'const sure = g.every((x) => x.hi), off = {}; if (!sure) g.forEach((x) => { if (x.uid !== keep) off[x.uid] = true; });' 'const sure = g.every((x) => x.hi), off = {};'
# PGSF50
k 'G.keep = u(k.dataset.apmgk); delete G.off[G.keep];' 'G.keep = u(k.dataset.apmgk);'
# PGSF51
k '${mgBtn(A, '\''persona'\'')}${mgPanel(A, '\''persona'\'')}' '${mgBtn(A, '\''persona'\'')}'
# PGSF52
k 'if (mgClick(x)) return;' ''
# PGSF53
k 'g.why = mb.repo ? repoWhy(e) : addErr(e); K[u] = idem(); }' 'g.why = mb.repo ? repoWhy(e) : addErr(e); }'
# PGSF54
k 'persona_uid: u, idem_key: K[u] || (K[u] = idem()) };' 'persona_uid: A.persona, idem_key: K[u] || (K[u] = idem()) };'
# PGSF55
k 'if (b0.repo) { mb.repo = b0.repo; if (b0.base) mb.base = b0.base; }' ''
# PGSF56
k 'safe: { chat: 1, persona: '\''p'\'', net: '\''n'\'' }' 'safe: { chat: 1, persona: '\''p'\'', net: 1 }'
# PGSF57
k 'const r = await fingerAct('\''seat_open'\'', mb, () =>' 'const r = await act('\''seat_open'\'', mb, () =>'
# PGSF58
k 'g.ok = true; g.why = se.state === '\''pending'\'' ? '\''排隊中'\'' : '\'''\'';' 'g.ok = true;'
# PGSF59
k 'if (how === '\''go'\'') { A.chat.push({ who: '\''ai'\'', t: CH_GO_NO }); A.chEnd = true; return; }' 'if (how === '\''go'\'') { A.chat.push({ who: '\''ai'\'', t: CH_GO_NO }); A.chEnd = true; }'
# PGSF60
k '${m.p != null && P[m.p] ? chProp(P[m.p], m.p) : esc(m.t)}' '${m.p != null && P[m.p] ? chProp(P[m.p], m.p) : m.t}'
# PGSF61
k 'const mates = has('\''審'\'', '\''review'\'') ?' 'const mates = false ?'
# PGSF62
k 'A.mode = pr.mode; if (pr.per) { A.persona = pr.per.uid; brainPreselect(A); }' 'if (pr.per) { A.persona = pr.per.uid; brainPreselect(A); }'
# PGSF63
k 'const subs = C && C.id === '\''custom'\'' ? chBox(A) : C ?' 'const subs = C ?'
# PGSF64
k 'text = String(text || '\'''\'').trim().slice(0, 400); if (!text) return;' 'text = String(text || '\'''\'').slice(0, 400); if (!text) return;'
# PGHS17
k 'host: hsCan(pv) && !hsWinWait(), net: pv })}' 'host: true, net: pv })}'
