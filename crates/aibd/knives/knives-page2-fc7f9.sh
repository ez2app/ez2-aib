#!/usr/bin/env bash
# PT / RG / HS (fc7f9 11-23, 頁面第二輪 S3 權限模板 / S4 人格·大腦·角色 改與封存 / S2 管理主機;ORDER-page2-ebab8-fc7f9-1123).
# First half (split again 11-25: the other 71 knives are in knives-page2a-fc7f9.sh; 142 in one script ran past the per-script watchdog of #884).
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs §37 §38 §39) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGPT1 (pt_fp_missing push)
k '  const PT_DANGER = ['\''any'\'', '\''anycmd'\'', '\''push'\'', '\''host'\''];' '  const PT_DANGER = ['\''any'\'', '\''anycmd'\'', '\''host'\''];'
# PGPT2 (pt_raise_missed code)
k ' || PT_ITEMS.some(([k]) => f.items[k] && !was.items[k]));' ');'
# PGPT3 (pt_reserved_name 討論研究)
k '    if (PT_RESERVED.some((r) => r.toLowerCase() === nm.toLowerCase())) return '\''名稱不能跟四種模式同名'\'';' '    // knife PT3'
# PGPT4 (pt_off_not_said)
k '      if (c === '\''unknown_act_type'\'' || (e && e.status === 404)) P.off = PT_OFF; else' '      if (false) P.off = PT_OFF; else'
# PGPT5 (pt_add_claims_without_done)
k '      if (!r || r.done !== true) throw Object.assign(new Error('\''not_done'\''), { code: '\''not_done'\'' });' '      // knife PT5'
# PGPT6 (pt_set_body)
k '    if (f.uid) Object.assign(body, { tpl_uid: f.uid, rev: f.rev });' '    if (f.uid) Object.assign(body, { tpl_uid: f.uid });'
# PGPT7 (pt_pick_without_7b)
k 'data-ptpick="${esc(t.tpl_uid)}" ${open7b ? '\'''\'' : '\''disabled'\''}' 'data-ptpick="${esc(t.tpl_uid)}" ${'\'''\''}'
# PGPT8 (pt_apply_says_now)
k '(ptRes(r) || '\''重開這一位之後才生效'\'')' ''\''已生效'\'''
# PGPT9 (pt_name_unescaped)
k '<b>${esc(t.name)}</b><em>底 ${esc(FP_MODE[t.base] || t.base)}</em><i>${esc(ptWords(it) || '\''什麼都不開'\'')}${ptNeedFp' '<b>${t.name}</b><em>底 ${esc(FP_MODE[t.base] || t.base)}</em><i>${esc(ptWords(it) || '\''什麼都不開'\'')}${ptNeedFp'
# PGPT10 (pt_not_enforceable_not_shown)
k '${ne ? `<i class="ptne">${esc(ne)}</i>` : '\'''\''}</button><span class="pta">' '</button><span class="pta">'
# PGPT11 (pt_open_body)
k 'if (T) { body.tpl_uid = T.tpl_uid; body.rev = T.rev; body.mode = T.base; }' 'if (T) { body.tpl_uid = T.tpl_uid; }'
# PGPT12 (pt_remove)
k 'await act('\''perm_template_remove'\'', { tpl_uid: t.tpl_uid, rev: t.rev });' 'await act('\''perm_template_remove'\'', { tpl_uid: t.tpl_uid });'
# PGPT13 (pt_mode_keeps_tpl)
k 'S.add.mode = am.dataset.addm; S.add.tpl = '\'''\''; S.add.noRepoAsk = false;' 'S.add.mode = am.dataset.addm; S.add.noRepoAsk = false;'
# PGPT14 (pt_apply_sent_without_7b)
k '    if (hasAct('\''perm_template_apply'\'') !== true) { P.seatMsg = { seat: seatId, bad: true, text: PT_NO_APPLY }; return render(); }' '    // knife PT14'
# PGPT15 (pt_dup_name)
k '    if ((list || []).some((t) => t.name === nm && t.tpl_uid !== f.uid)) return '\''名稱跟另一個模板重複'\'';' '    // knife PT15'
# PGPT16 (pt_toggle_or_fp_hint)
k '    const it = x.closest('\''[data-ptit]'\''); if (it && P.f) { const k = it.dataset.ptit; P.f.items[k] = !P.f.items[k]; render(); return true; }' '    const it = x.closest('\''[data-ptit]'\''); if (it && P.f) { render(); return true; }'
# PGPT17 (pt_gives_not_aibd)
k '  const PT_GIVES = { safe: ['\''chat'\''], work: ['\''chat'\'', '\''code'\'', '\''own'\'', '\''cmd'\'', '\''brain'\''],' '  const PT_GIVES = { safe: ['\''chat'\''], work: ['\''chat'\'', '\''code'\'', '\''own'\'', '\''cmd'\'', '\''brain'\'', '\''net'\''],'
# PGRG1 (rg_act_sent_ungated persona_set)
k '    if (!rgHas(R.kind, '\''set'\'')) { R.msg = { bad: true, text: rgOff(R.kind, '\''set'\'') }; return render(); }' '    // knife RG1'
# PGRG2 (rg_act_sent_ungated persona_set)
k '    if (!rgHas(R.kind, '\''archive'\'')) { R.msg = { bad: true, text: rgOff(R.kind, '\''archive'\'') }; return render(); }' '    // knife RG2'
# PGRG3 (rg_in_use_sent)
k '    if (R.kind === '\''persona'\'' && rgUsers(R.uid).length) return render();' '    // knife RG3'
# PGRG4 (rg_claims_without_done)
k '  const rgOk = (r) => !!r && (r.done === true || (Number(r.__status) >= 200 && Number(r.__status) < 300 && Number(r.__status) !== 202));' '  const rgOk = (r) => !!r;'
# PGRG5 (rg_builtin_not_locked)
k '    const act = builtin ? '\''<span class="rgk" title="內建,不能改或刪">🔒</span>'\'' :' '    const act = false ? '\'''\'' :'
# PGRG6 (rg_builtin_opens)
k '      if (!it || it.builtin) return true;' '      if (!it) return true;'
# PGRG7 (rg_brain_set)
k ' : R.kind === '\''brain'\'' ? { uid: R.uid, name } : { uid: R.uid, name, what };' ' : { uid: R.uid, name, what };'
# PGRG8 (rg_act_sent_ungated persona_set)
k 'undo: rgHas(R.kind, '\''restore'\'') ? { kind: R.kind, uid: R.uid, name: it.name } : null };' 'undo: { kind: R.kind, uid: R.uid, name: it.name } };'
# PGRG9 (rg_persona_archive)
k '      if (op === '\''archive'\'' && A && A.persona === uid) { A.persona = '\'''\''; A.brain = '\'''\''; }' '      // knife RG9'
# PGRG10 (rg_persona_in_use)
k '    if (U.length) return `<div class="cfm rgc"><b>不能封存</b>' '    if (false) return `<div class="cfm rgc"><b>不能封存</b>'
# PGRG11 (rg_edit_form)
k 'data-rgf="name" value="${esc(R.name)}"' 'data-rgf="name" value="${R.name}"'
# PGRG12 (rg_notes)
k '${pers}${rgNote('\''persona'\'')}${rgArchHtml('\''persona'\'')}${regForm('\''persona'\'')}' '${pers}${rgArchHtml('\''persona'\'')}${regForm('\''persona'\'')}'
# PGRG13 (rg_events)
k '    if (e.target.dataset && e.target.dataset.rgf && S.rg) { S.rg[e.target.dataset.rgf] = e.target.value; if (S.rg.review) { S.rg.review = false; const d = document.querySelector('\''.rgdiff'\''); if (d) d.remove(); } return; }' '    // knife RG13'
# PGHS1 (hs_mode_button)
k '  const hsCan = (pv) => !!pv && Array.isArray(pv.modes) && pv.modes.includes('\''host'\'') && hsHas('\''list'\'');' '  const hsCan = (pv) => !!pv && hsHas('\''list'\'');'
# PGHS2 (hs_act_sent_ungated managed_host_list)
k '  const hsCan = (pv) => !!pv && Array.isArray(pv.modes) && pv.modes.includes('\''host'\'') && hsHas('\''list'\'');' '  const hsCan = (pv) => !!pv && Array.isArray(pv.modes) && pv.modes.includes('\''host'\'');'
# PGHS3 (hs_mode_not_reset)
k '    if (A.mode === '\''host'\'' && (!hsCan(pv) || hsWinWait())) A.mode = '\''safe'\''; // S2' '    // knife HS3'
# PGHS4 (hs_open_body)
k 'if (!ids.length) { A.busy = false; A.msg = HS_WHY.hosts_missing; A.err = true; return render(); } ' ''
# PGHS5 (hs_open_body)
k 'const r = await fingerAct('\''seat_open'\'', body, (t, sec) =>' 'const r = await act('\''seat_open'\'', body, (t, sec) =>'
# PGHS6 (hs_check_lets user=-oProxyCommand=x)
k '    if (!/^[A-Za-z0-9._][A-Za-z0-9._-]*$/.test(user)) return HS_WHY.host_user;' '    // knife HS6'
# PGHS7 (hs_check_lets key=-i)
k '    if (key.startsWith('\''-'\'') || /[\u0000-\u001f\u007f]/.test(key) || !/^(~\/|\/|[A-Za-z]:\\|%USERPROFILE%\\)/.test(key)) return HS_WHY.host_key_path;' '    // knife HS7'
# PGHS8 (hs_add_after)
k '      H.f = null; H.msg = { text: '\''已加主機「'\'' + body.name + '\''」'\'' }; H.busy = false;' '      H.msg = { text: '\''已加主機「'\'' + body.name + '\''」'\'' }; H.busy = false;'
# PGHS9 (hs_shell_words)
k '`<div class="ademo hswarn">${esc(HS_SHELL)}</div>' '`<div class="ademo hswarn"></div>'
# PGHS10 (hs_row)
k '<b>${sel.includes(h.id) ? '\''☑'\'' : '\''☐'\''} ${esc(h.name)}</b>' '<b>${sel.includes(h.id) ? '\''☑'\'' : '\''☐'\''} ${h.name}</b>'
# PGHS11 (hs_remove)
k '      if (S.add) S.add.hosts = (S.add.hosts || []).filter((x) => x !== id);' '      // knife HS11'
# PGHS12 (hs_click_not_wired)
k '    if (x.closest('\''[data-hspick],[data-hsnew],[data-hsno],[data-hsok],[data-hsdel],[data-hsdelno],[data-hsdelok]'\'') && await hsClick(x)) return;' '    // knife HS12'
# PGHS13 (hs_mode_not_reset)
k '    if (A.mode === '\''host'\'' && (!hsCan(pv) || hsWinWait())) A.mode = '\''safe'\''; // S2' '    if (A.mode === '\''host'\'' && (!hsCan(pv))) A.mode = '\''safe'\''; // S2'
# PGHS14 (hs_row_place)
k '${A.mode === '\''host'\'' ? hsRow(A) : hsRowOff(A)}' '${hsRow(A)}'
# PGHS15 (hs_check_lets port=70000)
k '    if (port && !(/^\d{1,5}$/.test(port) && Number(port) >= 1 && Number(port) <= 65535)) return '\''埠要是 1–65535'\'';' '    // knife HS15'
# PGHS16 (hs_windows_not_grey)
k '    if (hsWinWait()) return `' '    if (false) return `'
# PGPT18 (pt_ne_object_shape)
k 'const it = k && typeof k === '\''object'\'' ? String(k.item || '\'''\'') : String(k);' 'const it = String(k);'
# PGPT19 (pt_pending_restart_words)
k '    if (r && r.pending_restart) return '\''驗證過了;重開這一位之後才生效'\'' + (why ? '\''('\'' + PT_RESTART[why] + '\'')'\'' : '\'''\'');' ' '
# PGPT20 (逐項調整 ebab8 03:20 第 4 條)
k 'host: hsCan(pv) && !hsWinWait(), net: pv })}${ptFine(A)}' 'host: hsCan(pv) && !hsWinWait(), net: pv })}'
# PGPT21 (逐項調整 ebab8 03:20 第 4 條)
k '${btns}${P.f && P.f.fine ? '\'''\'' : ptForm()}${msg}' '${btns}${ptForm()}${msg}'
# PGPT22 (逐項調整 ebab8 03:20 第 4 條)
k 'else { P.f = ptBlank(A ? A.mode : '\''safe'\'', true); P.f.fine = true; }' 'else { P.f = ptBlank('\''safe'\'', true); P.f.fine = true; }'
# PGPT23 (逐項調整 ebab8 03:20 第 4 條)
k '${on ? `<div class="apn">個別調整就變「自訂權限」;這一版要先存成模板才能拿來開(開好之後也能隨時改)</div>${ptForm()}` : '\'''\''}' '${on ? `<div class="apn">個別調整</div>` : '\'''\''}'
# PGPT24 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'if (T && body.repo) { A.busy = false; A.msg = PT_WHY.template; A.err = true; return render(); }' ' '
# PGPT25 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'if (r && r.unchanged) return '\''沒有變(權限跟現在一樣)'\'';' ' '
# PGPT26 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'x.action !== '\''open'\'' && x.action !== '\''revoke'\'' ?' 'x.action !== '\''revoke'\'' ?'
# PGPT27 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'return m && m !== c ? m : PT_MORE;' 'return PT_MORE;'
# PGPT28 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'P.seatMsg = w ? { seat: seatId, text: '\''已收回:'\'' + w } : { seat: seatId, bad: true, text: '\''沒有收回:AIB 沒有回結果'\'' };' 'P.seatMsg = { seat: seatId, text: '\''已收回:'\'' + w };'
# PGPT29 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'if (hasAct('\''seat_perm_log'\'') !== true) return '\''<div class="apn">權限紀錄與收回:這一版 AIB 還沒有</div>'\'';' ' '
# PGPT30 (IFACE-page2 r2 A:08de5 錯碼 / seat_perm_log / revoke)
k 'const why = r ? Object.keys(PT_RESTART).find((k) => r[k]) : '\'''\'';' 'const why = '\'''\'';'
# PGHS18 (IFACE-page2 r2 C:3e53b 錯碼 / stopped / managed_host_log)
k 'if (c === '\''host_addr_refused'\'' && HS_ADDR[why]) return '\''這個位址不能加:'\'' + HS_ADDR[why]; ' ' '
# PGHS19 (IFACE-page2 r2 C:3e53b 錯碼 / stopped / managed_host_log)
k 'n ? '\''、正在跑的已停('\'' + n + '\'' 個)'\'' : '\'',當下沒有在跑的'\''' ''\''、正在跑的已停'\'''
# PGHS20 (IFACE-page2 r2 C:3e53b 錯碼 / stopped / managed_host_log)
k '${m === '\''host'\'' ? hsLogHtml(id) : '\'''\''}' ' '
# PGHS21 (IFACE-page2 r2 C:3e53b 錯碼 / stopped / managed_host_log)
k 'if (hasAct(HS_ACTS.log) !== true) return '\''<div class="aplab">連主機做過什麼</div><div class="apn">這一版 AIB 還沒有</div>'\'';' ' '
# PGHS22 (IFACE-page2 r2 C:3e53b 錯碼 / stopped / managed_host_log)
k 'esc(String(x.command || '\'''\'').slice(0, 200))' 'String(x.command || '\'''\'').slice(0, 200)'
# PGHS23 (IFACE-page2 r2 C:3e53b 錯碼 / stopped / managed_host_log)
k 'h.hostkey_sha256 ? '\''指紋 '\'' + String(h.hostkey_sha256).slice(0, 80) : '\'''\''' 'h.hostkey_sha256 ? '\''指紋 '\'' + String(h.hostkey_sha256).slice(0, 16) + '\''…'\'' : '\'''\'''
# PGSC1 (S1c 400 寬再對示意 C1)
k '先選要做什麼(或「接手舊席」),設定會自動帶出來' '先選要做什麼(或「接回」),設定會自動帶出來'
# PGSC2 (S1c r2:page_csp 倒數圈 data-sty + 完全權限搭檔提示)
k 'class="fg" data-sty="stroke-dashoffset:${off.toFixed(1)}"/>' 'class="fg" style="stroke-dashoffset:${off.toFixed(1)}"/>'
# PGSC3 (S1c r2:page_csp 倒數圈 data-sty + 完全權限搭檔提示)
k '(A.mode === '\''dgrs'\'' ? '\''<div class="apn apwarn">' '(A.mode === '\''nope'\'' ? '\''<div class="apn apwarn">'
# PGSD1 (S1d:Andy 04:4x 品牌改下拉(品牌 · 預設模型))
k '" disabled><option value="${esc(p.key)}|" disabled>' '"><option value="${esc(p.key)}|">'
# PGSD2 (S1d:Andy 04:4x 品牌改下拉(品牌 · 預設模型))
k 'if (!p.modes.includes(S.add.mode)) S.add.mode = '\''safe'\'';' ' '
# PGSD3 (S1d:Andy 04:4x 品牌改下拉(品牌 · 預設模型))
k 'const p = providers().find((x) => x.key === k && x.open);' 'const p = providers().find((x) => x.key === k);'
