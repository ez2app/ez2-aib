// 聯絡中心 2.0 — 畫面(S89-talk-2c;開源)。只有清單、時間線、直接回、光點、五條滑桿。
// 版面照 SPEC-contacts-center-A-f5fb8-1029.md A 兩欄;資料全由 aibd 給(api.js)。
// 本文一律 esc() 後才進 innerHTML;沒有 body 的訊息(用戶資料夾頁的讀範圍)只顯示長度與狀態。
'use strict';
(function () {
  // ---------- 文案 ----------
  const LANGS = Object.keys(window.AIB_COPY);
  const pickLang = () => {
    const q = new URLSearchParams(location.search).get('lang');
    if (q && AIB_COPY[q]) return q;
    for (const l of (navigator.languages || [navigator.language || ''])) {
      const L = String(l);
      if (AIB_COPY[L]) return L;
      const base = L.split('-')[0];
      if (base === 'zh') return 'zh-TW';
      const hit = LANGS.find((k) => k.split('-')[0] === base);
      if (hit) return hit;
    }
    return 'zh-TW';
  };
  const LANG = pickLang();
  document.documentElement.lang = LANG;
  const t = (k, v) => { let s = (AIB_COPY[LANG] || {})[k] ?? AIB_COPY['zh-TW'][k] ?? k; if (v) for (const n in v) s = s.split('{' + n + '}').join(String(v[n])); return s; };
  document.title = t('title');

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const NARROW = matchMedia('(max-width: 760px)');

  // ---------- 狀態 ----------
  const S = {
    f: 'all', q: '', cur: null, view: 'list', help: false, fx: false, pair: null,
    contacts: [], me: null, thread: null, threadOf: null, banner: null, bannerKind: '', flowsNone: false,
    drafts: {},
    ib: null, ibF: 'all', ibOpen: null, ibBody: null, // 待審區(519d0 SPEC-contacts-to-inbox r0–r4):aibd 給總收件匣才有;沒有(用戶資料夾頁 403)⇒ 照舊聯絡中心
  };
  const el = document.getElementById('cc');

  // ---------- 資料 → 畫面用的形狀 ----------
  const isHuman = (c) => c.kind === 'human';
  const avId = (key) => (S.me && key === S.me.key ? 'me' : key);
  const emoji = (c) => (isHuman(c) ? '🧑' : c.provider === 'codex' ? '🛠' : '🤖');
  function presence(c) {
    if (isHuman(c)) return c.presence === 'running' ? t('p_human_on') : t('p_human_off');
    if (c.presence === 'running') return t('p_running');
    if (c.presence === 'queued') return t('p_queued', { n: c.queue_position ?? '?' });
    if (c.presence === 'stopped') return t('p_stopped');
    return t('p_unknown');
  }
  const online = (c) => c.presence === 'running';
  const ago = (iso) => {
    if (!iso) return '';
    const d = (Date.now() - Date.parse(iso)) / 1000;
    if (!Number.isFinite(d)) return '';
    if (d < 60) return '<1m';
    if (d < 3600) return Math.floor(d / 60) + 'm';
    if (d < 86400) return Math.floor(d / 3600) + 'h';
    return Math.floor(d / 86400) + 'd';
  };
  const hhmm = (iso) => { const d = new Date(iso); return Number.isFinite(d.getTime()) ? d.toLocaleString(LANG, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''; };
  // 排序(SPEC §1):真人在上;等我決定 > 未讀 > 最近
  const rank = (c) => (c.decide_open ? 0 : c.unread ? 1 : 2);
  function visible() {
    const q = S.q.trim().toLowerCase();
    let L = S.contacts.filter((c) => !(S.me && c.key === S.me.key));
    if (S.pair) return L.filter((c) => c.key === S.pair);
    if (S.f === 'archived') L = L.filter((c) => c.archived_at);
    else L = L.filter((c) => !c.archived_at && !c.blocked);
    if (S.f === 'dec') L = L.filter((c) => c.decide_open > 0);
    else if (S.f === 'unread') L = L.filter((c) => c.unread > 0);
    else if (S.f === 'h') L = L.filter(isHuman);
    if (q) L = L.filter((c) => [c.name, c.persona_uid, c.key].some((x) => String(x || '').toLowerCase().includes(q)));
    return L.sort((a, b) => (isHuman(b) - isHuman(a)) || (b.pinned - a.pinned) || (rank(a) - rank(b)) || String(b.last_message_at || '').localeCompare(String(a.last_message_at || '')) || String(a.name).localeCompare(String(b.name)));
  }

  // ---------- 片段 ----------
  const avatar = (c) => `<span class="av ${isHuman(c) ? 'h' : ''} ${c.provider === 'codex' ? 'cx' : ''} ${online(c) ? 'live' : ''}" data-av="${esc(avId(c.key))}" aria-hidden="true">${emoji(c)}<i class="dot ${online(c) ? 'on' : ''}"></i></span>`;
  function row(c) {
    const b = c.decide_open ? `<span class="badge dec" title="${esc(t('f_dec'))}">${Number(c.decide_open) | 0}</span>` : c.unread ? `<span class="badge" title="${esc(t('f_unread'))}">${Number(c.unread) | 0}</span>` : '';
    const sub = c.archived_at ? `<span class="ro">${esc(t('archived_ro'))}</span>` : esc(presence(c));
    return `<div class="p" role="button" tabindex="0" data-id="${esc(c.key)}" aria-current="${S.cur === c.key}">
      ${avatar(c)}
      <div class="minw0"><div class="nm2"><span>${esc(c.name)}</span>${isHuman(c) ? `<span class="hum">${esc(t('f_h'))}</span>` : ''}${c.pinned ? '<span aria-hidden="true">📌</span>' : ''}</div><div class="sub">${sub}</div></div>
      <div class="meta"><span>${esc(ago(c.last_message_at))}</span>${b}</div></div>`;
  }
  function listHtml() {
    const L = visible();
    if (!L.length) return `<div class="empty">${esc(t('empty'))}</div>`;
    const H = L.filter(isHuman), A = L.filter((c) => !isHuman(c));
    let s = '';
    if (H.length) s += `<div class="grp">${esc(t('grp_h'))}</div>` + H.map(row).join('');
    if (A.length) s += `<div class="grp">${esc(t('grp_ai'))}</div>` + A.map(row).join('');
    return s;
  }
  const stText = (m) => ({ failed: t('st_failed'), accepted: t('st_pending'), queued: t('st_pending'), dispatched: t('st_pending'), received: t('st_received'), processed: t('st_processed') }[m.state] || m.state || '');
  function msgHtml(m, c, readOnly) {
    const mine = S.me && m.from_seat === S.me.key;
    const dec = m.kind === 'decide';
    const openDec = dec && !mine && m.state !== 'processed';
    const text = typeof m.body === 'string' ? esc(m.body) : `<span class="len">${esc(t('len_only', { n: m.len ?? '?' }))}</span>`;
    const st = mine || m.state === 'failed' ? `<div class="${m.state === 'failed' ? 'hold' : 'st'}"><abbr title="state=${esc(m.state)}">${esc(stText(m))}</abbr></div>` : '';
    const acts = mine || readOnly ? '' : `<div class="ma">${m.unread ? `<button class="g-btn" type="button" data-read="${esc(m.message_id)}">${esc(t('read'))}</button>` : ''}${openDec ? `<button class="g-btn gold" type="button" data-done="${esc(m.message_id)}">${esc(t('done'))}</button>` : ''}<button class="g-btn" type="button" data-reply="1">${esc(t('reply'))}</button></div>`;
    return `<div class="m ${mine ? 'me' : ''} ${m.unread ? 'unread' : ''}">
      <div class="mh"><span>${mine ? esc(t('you')) : ''}${openDec ? esc(t('dec_tag')) : ''}</span><span>${esc(hhmm(m.created))}</span></div>
      <div class="mt">${text}</div>${st}${acts}</div>`;
  }
  function timeline(c, back) {
    if (!c) return `<div class="empty">${esc(t('pick'))}</div>`;
    const readOnly = !!c.archived_at;
    const msgs = S.threadOf === c.key && S.thread ? S.thread.messages || [] : [];
    const draft = S.drafts[c.key] || '';
    return `<div class="tl">
      <div class="tlh">${back ? `<button class="g-btn back" type="button" data-back="1">${esc(t('back'))}</button>` : ''}
        <div class="who">${avatar(c)}<b>${esc(c.name)}</b><span class="st">${esc(c.persona_uid || c.key)}${c.mode ? ' · ' + esc(c.mode) : ''}${c.provider ? ' · ' + esc(c.provider) : ''}</span></div>
        <div class="st">${esc(presence(c))}${readOnly ? ' · ' + esc(t('archived_ro')) : ''}</div>
</div>
      <div class="msgs">${msgs.length ? msgs.map((m) => msgHtml(m, c, readOnly)).join('') : `<div class="empty">${esc(t('no_msgs'))}</div>`}</div>
      ${readOnly ? '' : `<form class="reply" data-send="${esc(c.key)}"><input name="t" autocomplete="off" maxlength="32768" placeholder="${esc(t('reply_to', { name: c.name }))}" aria-label="${esc(t('reply_to', { name: c.name }))}" value="${esc(draft)}"><button class="g-btn gold" type="submit">${esc(t('send'))}</button></form>`}</div>`;
  }
  // ---------- 待審區 ----------
  // 一封一張卡;欄 0 未讀 / 1 讀了沒結 / 2 等你 / 3 已結(同網頁版總收件匣 1a93b c734997a8a);急件只看 aibd 的 level
  const ibKey = (v) => { const k = String(v || ''); return /^human:/.test(k) && S.me ? S.me.key : k.replace(/^seat:legacy:/, 'seat:'); };
  const ibCol = (m) => (m.done_at || m.superseded_by ? 3 : m.held_human || /^human:/.test(String(m.to_seat || '')) ? 2 : m.read_at ? 1 : 0);
  const ibKind = (m) => (m.blocked ? 'blk' : m.level === 'urgent' ? 'urg' : ibCol(m) === 2 ? 'ask' : 'say');
  const IB_RANK = { urg: 0, ask: 1, say: 2, blk: 3 }, IB_FX = { urg: 'urg', ask: 'ask', say: 'gold', blk: 'hold' };
  // aibd 列的 failed_code(7ebca 12:22);同 ws.js INB_BLOCK,沒對到照寫代號
  const IB_BLOCK = { safe_cannot_send_to_dgrs: '安全模式的席不能寄給全權席', seat_mode_takes_no_input: '收件席這個模式不收字', seat_mode_cannot_send_to_legacy: '這個模式不能寄給終端機的舊席', not_a_member: '收件席不在這串對話裡', text_expired: '放太久過期了,沒送', text_refused: '內容被擋(太長或有不准的字)', legacy_off: '這台沒開舊信帳,寄不到終端機的席', seat_unknown: '找不到收件席', seat_not_in_cabin: '收件席不在這個艙', write_failed: '寫入失敗,沒送出', internal: 'aibd 內部出錯,沒送出' };
  // 7ebca 13:2x(e41c75d354):level_by 是角色 uid ⇒ 查 /api/v1/roles 的名字;查不到寫 uid(都走 esc)
  const ROLES = {}; let rolesTried = false;
  const roleName = (uid) => { if (!rolesTried) { rolesTried = true; AIB.roles().then((r) => { for (const x of (r && r.roles) || []) if (x && x.uid) ROLES[x.uid] = String(x.name || x.uid); render(); }).catch(() => {}); } return ROLES[uid] || uid; };
  const IB_LVBY = { andy: '你', '519d0': '@519d0', system: '系統' };
  const ibName = (k) => { const c = byKey(k); return c ? (S.me && c.key === S.me.key ? t('you') : c.name) : '@' + String(k).replace(/^(seat|human):/, ''); };
  const ibAv = (k) => { const c = byKey(k); return c ? emoji(c) : '🤖'; };
  function ibShow(m) {
    if (S.pair && ibKey(m.from_seat) !== S.pair && ibKey(m.to_seat) !== S.pair) return false;
    return S.ibF === 'all' || ibKind(m) === S.ibF;
  }
  function ibCard(m) {
    const col = ibCol(m), k = ibKind(m), a = Number(m.age_min) || 0;
    const age = col < 3 ? (a > 30 ? ' old' : a > 10 ? ' y' : '') : '';
    const disp = /^派工\s*[::]/.test(String(m.first_line || ''));
    const by = k === 'urg' ? (m.level_by ? IB_LVBY[m.level_by] || roleName(String(m.level_by)) : '') : '';
    const tag = `<span class="k ${disp && k === 'say' ? 'disp' : k}"${by ? ` title="${esc(t('ib_by', { r: by }))}"` : ''}>${esc(t(disp && k === 'say' ? 'ib_k_disp' : 'ib_k_' + k))}</span>`;
    const tk = `<span class="tk"><span class="${m.delivered_at ? 'on' : ''}">${esc(t('ib_t_sent'))}</span><span class="${m.read_at ? 'on' : ''}">${esc(t('ib_t_read'))}</span><span class="${m.done_at ? 'on' : ''}"${m.done_evidence || m.done_why ? ` title="${esc(m.done_evidence || m.done_why)}"` : ''}>${esc(t('ib_t_done'))}</span></span>`;
    const via = /^seat:legacy:/.test(String(m.from_seat || '')) || m.via === 'bridge' ? `<span class="via">${esc(t('ib_via'))}</span>` : '';
    const dup = Number(m.dup_count) > 1 ? `<span class="dup">${esc(t('ib_dup', { n: Number(m.dup_count) | 0 }))}</span>` : '';
    const why = m.superseded_by ? `<div class="why">${esc(t('ib_sup'))}</div>` : m.blocked ? `<div class="why" title="${esc('aibd 代號 ' + String(m.blocked.reason || '?'))}">${esc(IB_BLOCK[m.blocked.reason] || 'aibd 回了 ' + String(m.blocked.reason || '?'))}</div>` : '';
    return `<div class="card ${k === 'urg' ? 'urg' : k === 'ask' ? 'dec' : k === 'blk' ? 'blk' : age.trim()}${ibShow(m) ? '' : ' dim'}${S.ibOpen === m.id ? ' on' : ''}" role="button" tabindex="0" data-ib="${esc(m.id)}">
      <div class="r1"><span class="mini">${ibAv(ibKey(m.from_seat))}</span><span class="who">${esc(ibName(ibKey(m.from_seat)))}</span>→<span class="mini">${ibAv(ibKey(m.to_seat))}</span><span class="who2">${esc(ibName(ibKey(m.to_seat)))}</span>${tag}<span class="age${age}">${esc(t('ib_min', { n: Math.round(a) }))}</span></div>
      <div class="t">${esc(String(m.first_line || '').slice(0, 120))}</div><div class="r3">${tk}${dup}${via}</div>${why}</div>`;
  }
  function ibDrawer() {
    const m = S.ibOpen && S.ib.rows.find((x) => x.id === S.ibOpen); if (!m) return '';
    const body = S.ibBody == null ? t('ib_loading') : S.ibBody === false ? t('ib_nobody') : S.ibBody;
    return `<div class="open ibdr"><div class="wall">${esc(t('ib_wall'))}</div><div class="r1"><span class="who">${esc(ibName(ibKey(m.from_seat)))}</span>→ ${esc(ibName(ibKey(m.to_seat)))}<button type="button" class="g-btn" data-ibx="1">${esc(t('ib_close'))}</button></div><pre class="ibt">${esc(body)}</pre></div>`;
  }
  function ibPeople() {
    const open = {}; for (const m of S.ib.rows) if (ibCol(m) < 3) { const k = ibKey(m.to_seat); const x = (open[k] = open[k] || { n: 0, hot: false }); x.n++; if (ibKind(m) === 'urg' || (Number(m.age_min) || 0) > 30) x.hot = true; }
    const L = S.contacts.filter((c) => !c.archived_at && !c.blocked).sort((a, b) => (isHuman(b) - isHuman(a)) || ((open[b.key] || {}).n || 0) - ((open[a.key] || {}).n || 0));
    return L.map((c) => { const o = open[c.key]; return `<div class="p" role="button" tabindex="0" data-ibp="${esc(c.key)}" aria-current="${S.pair === c.key}">${avatar(c)}<div class="minw0"><div class="nm2"><span>${esc(S.me && c.key === S.me.key ? t('you') : c.name)}</span></div><div class="sub">${esc(presence(c))}</div></div><div class="meta">${o ? `<span class="badge${o.hot ? ' dec' : ''}">${o.n}</span>` : ''}</div></div>`; }).join('');
  }
  function ibHtml() {
    const R = S.ib.rows, cols = [[], [], [], []];
    for (const m of R) cols[ibCol(m)].push(m);
    for (let i = 0; i < 4; i++) cols[i].sort((a, b) => (i < 3 ? IB_RANK[ibKind(a)] - IB_RANK[ibKind(b)] || (Number(b.age_min) || 0) - (Number(a.age_min) || 0) : (Number(a.age_min) || 0) - (Number(b.age_min) || 0)));
    const stuck = {}; let oldest = 0, openN = 0;
    for (const m of R) if (ibCol(m) < 3) { openN++; const k = ibKey(m.to_seat); stuck[k] = (stuck[k] || 0) + 1; oldest = Math.max(oldest, Number(m.age_min) || 0); }
    const top = Object.keys(stuck).sort((a, b) => stuck[b] - stuck[a])[0];
    const stat = top ? `<span class="ibstat">${esc(t('ib_stat', { n: openN, who: ibName(top), k: stuck[top], m: Math.round(oldest) }))}</span>` : '';
    const chips = [['all', 'ib_all'], ['urg', 'ib_urg'], ['ask', 'ib_ask'], ['blk', 'ib_blk']].map(([k, w]) => `<button type="button" class="fchip ${k === 'urg' ? 'urg' : ''}" data-ibf="${k}" aria-pressed="${S.ibF === k}">${esc(t(w))}</button>`).join('');
    const colHtml = cols.map((L, i) => { const seen = L.filter(ibShow).length; return `<div class="col"><h3>${esc(t('ib_c' + i))}<span class="n">${seen === L.length ? L.length : esc(t('ib_seen', { n: seen, all: L.length }))}</span>${i === 2 ? `<span class="ph">${esc(t('ib_c2_note'))}</span>` : ''}</h3>${L.map(ibCard).join('')}</div>`; }).join('');
    const lg = `<div class="legend"><span class="lg urg"><i></i>${esc(t('ib_lg_urg'))}</span><span class="lg ask"><i></i>${esc(t('ib_lg_ask'))}</span><span class="lg say"><i></i>${esc(t('ib_lg_say'))}</span><span class="lg blk"><i></i>${esc(t('ib_lg_blk'))}</span><span>${esc(t('ib_lg_age'))}</span></div>`;
    return `<div class="bar ibbar">${chips}${stat}</div>${S.ib.bridge === false ? `<div class="off-banner">${esc(t('ib_nobridge'))}</div>` : ''}<div class="ibmain"><div class="panel ibppl"><h4>${esc(t('ib_ppl'))}</h4>${ibPeople()}</div><div class="ibright"><div class="cols">${colHtml}</div>${ibDrawer()}</div></div>${lg}`;
  }
  // 光點 = 還沒結的信:寄件人頭像流到收件人頭像;篩選 / 點人之後只留那些
  function ibFlows() {
    const m = new Map(), on = new Set([...el.querySelectorAll('[data-av]')].map((n) => n.dataset.av));
    for (const x of S.ib.rows) {
      if (ibCol(x) === 3 || !ibShow(x)) continue;
      const a = avId(ibKey(x.from_seat)), b = avId(ibKey(x.to_seat)); if (a === b || !on.has(a) || !on.has(b)) continue;
      const k = a + '|' + b + '|' + IB_FX[ibKind(x)]; m.set(k, (m.get(k) || 0) + 1);
    }
    return [...m].map(([k, n]) => { const [a, b, kind] = k.split('|'); return { a, b, kind, n }; });
  }
  const FILTERS = ['all', 'dec', 'unread', 'h', 'archived'];
  function head() {
    const dec = S.contacts.reduce((a, c) => a + (c.archived_at ? 0 : c.decide_open || 0), 0);
    return `<div class="title"><span class="lv">2.0</span><span class="nm">${esc(t('title'))}</span><small>aibd</small></div>
    <div class="bar"><div class="tabs"><a class="tablink" href="./">${esc(t('tab_chat'))}</a><button type="button" aria-pressed="true">${esc(t('tab_contacts'))}${dec ? ` <b class="n-red">${Number(dec) | 0}</b>` : ''}</button></div><span class="me-node"><span class="av live" data-av="me" aria-hidden="true">👑</span>${esc(t('me'))}${S.me ? ' ' + esc(S.me.name || '') : ''}</span><button type="button" class="pill fxgear" data-fxgear="1" aria-label="${esc(t('fx'))}" title="${esc(t('fx'))}" aria-expanded="${S.fx}">✨</button></div>`;
  }
  function filters() {
    const n = { dec: 0, unread: 0 };
    for (const c of S.contacts) if (!c.archived_at && !(S.me && c.key === S.me.key)) { n.dec += c.decide_open || 0; n.unread += c.unread || 0; }
    return `<div class="bar">${FILTERS.map((k) => `<button type="button" class="fchip ${k === 'dec' ? 'dec' : ''}" data-f="${k}" aria-pressed="${S.f === k}">${esc(t('f_' + k))}${n[k] ? `<b>${Number(n[k]) | 0}</b>` : ''}</button>`).join('')}
      <label class="search">🔍 <input type="search" data-q="1" placeholder="${esc(t('search'))}" aria-label="${esc(t('search'))}" value="${esc(S.q)}"></label><button type="button" class="pill help" data-help="1" aria-label="${esc(t('help'))}" aria-expanded="${S.help}">?</button></div>`;
  }
  const SL = ['sz', 'sp', 'de', 'tl', 'al'];
  const fxPop = () => `<div class="fxpop fxopts">${SL.map((k) => `<label class="sl"><span>${esc(t(k))}</span><input type="range" min="0" max="100" step="10" data-k="${k}" aria-label="${esc(t(k))}"><output data-o="${k}"></output></label>`).join('')}</div>`;
  function syncOpts() {
    for (const i of el.querySelectorAll('input[data-k]')) { i.value = FX.opt[i.dataset.k]; i.setAttribute('aria-valuetext', FX.opt[i.dataset.k] + '%'); }
    for (const o of el.querySelectorAll('output[data-o]')) o.textContent = FX.opt[o.dataset.o] ? FX.opt[o.dataset.o] + '%' : (o.dataset.o === 'tl' ? t('none') : t('off'));
  }

  // ---------- 畫 ----------
  function render() {
    // 重畫會換掉輸入框:記住焦點與游標
    const a = document.activeElement, focus = a && el.contains(a) ? (a.dataset.q ? '[data-q]' : a.name === 't' ? 'form.reply input' : null) : null;
    const pos = focus ? a.selectionStart : 0;
    const cur = S.contacts.find((c) => c.key === S.cur) || null;
    let body;
    if (S.ib) body = ibHtml();
    else if (NARROW.matches) body = S.view === 'tl' && cur ? `<div class="panel">${timeline(cur, true)}</div>` : `<div class="panel list">${listHtml()}</div>`;
    else body = `<div class="two"><div class="panel list">${listHtml()}</div><div class="panel">${timeline(cur, false)}</div></div>`;
    const pc = S.pair ? S.contacts.find((c) => c.key === S.pair) : null;
    const pairChip = pc ? `<div class="bar"><button type="button" class="pairchip" data-unpair="1">${esc(t('only_pair', { name: pc.name }))}</button></div>` : '';
    const banner = S.banner ? `<div class="${S.bannerKind === 'err' ? 'err-banner' : 'off-banner'}" role="status">${esc(S.banner)}</div>` : '';
    const flowsNote = S.flowsNone && S.fx ? `<div class="off-banner">${esc(t('flows_none'))}</div>` : '';
    el.innerHTML = head() + (S.fx ? fxPop() : '') + flowsNote + (S.ib ? '' : filters()) + pairChip + (S.help ? `<div class="helpbox">${esc(t('help_text'))}</div>` : '') + banner + body;
    syncOpts();
    if (el._cv) { el.appendChild(el._cv); FX.rendered(el); }
    if (S.ib) FX.setFlows(ibFlows()); // 頭像在畫面上才算得到路,畫完再給
    const m = el.querySelector('.msgs'); if (m) m.scrollTop = m.scrollHeight;
    if (focus) { const n = el.querySelector(focus); if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) {} } }
  }
  function say(msg, kind) { S.banner = msg; S.bannerKind = kind || ''; render(); }
  function fail(e) {
    if (e && e.status === 409) { say(t('conflict')); return refresh(); }
    if (e && e.status === 0) return say(t('offline'), 'err');
    // 用戶頁模式下 aibd 不讓頁讀 / 結信(5d33a L3,b4297 5b8f6586):是提示,不是錯
    if (e && e.status === 403 && (e.code === 'read_built_in_page_only' || e.code === 'done_built_in_page_only')) return say(t('builtin_only'), 'note');
    say(t('err', { m: (e && (e.code || e.message)) || '?' }), 'err');
  }

  // ---------- 讀資料 ----------
  let busy = false;
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const r = await AIB.contacts({ archived: true });
      S.contacts = r.contacts || [];
      S.me = AIB.me(S.contacts);
      await loadInbox();
      if (S.cur && !S.ib) await loadThread();
      if (S.bannerKind === 'err' || S.bannerKind === 'note' || S.banner === t('offline')) S.banner = null;
      if (!S.ib) await loadFlows();
    } catch (e) {
      if (e && (e.status === 401 || e.status === 403)) S.banner = t('need_pair'), S.bannerKind = 'err';
      else if (e && e.status === 0) S.banner = t('offline'), S.bannerKind = 'err';
      // a9136 11a40f6c N2:2a / 2b 還沒合的 aibd 沒有 /contacts ⇒ 說「這版還沒有」,不當錯誤碼丟給人
      else if (e && e.status === 404) S.banner = t('not_yet'), S.bannerKind = 'note'; // 提示色,紅只給錯誤與危險(a9136 839bf494 N1)
      else S.banner = t('err', { m: (e && (e.code || e.message)) || '?' }), S.bannerKind = 'err';
    } finally { busy = false; render(); }
  }
  async function loadInbox() {
    try { const r = await AIB.inbox(); S.ib = r && Array.isArray(r.rows || r.items) ? { rows: r.rows || r.items, counts: r.counts || {}, bridge: r.bridge } : null; }
    catch (e) { if (e && (e.status === 403 || e.status === 404 || e.status === 405)) S.ib = null; else throw e; }
  }
  async function ibOpen(id) {
    if (S.ibOpen === id) { S.ibOpen = null; return render(); }
    S.ibOpen = id; S.ibBody = null; render();
    let b = false; try { const r = await AIB.inboxOne(id); b = r && typeof (r.body != null ? r.body : r.text) === 'string' ? (r.body != null ? r.body : r.text) : false; } catch (e) { b = false; }
    if (S.ibOpen === id) { S.ibBody = b; render(); }
  }
  async function loadThread() {
    if (!S.me || !S.cur) { S.thread = null; return; }
    try { S.thread = await AIB.thread(AIB.threadId(S.me.key, S.cur)); S.threadOf = S.cur; markUnread(); }
    catch (e) { if (e && e.status === 404) { S.thread = { messages: [] }; S.threadOf = S.cur; } else throw e; }
  }
  // 未讀:訊息自帶 unread(2a e04e3bba:寄給我、還在 queued / delivered)就照它;
  // 舊形狀才退回 read_upto(讀到哪一則),再沒有才照 contacts 的 unread 數取最後幾則別人寄的
  function markUnread() {
    const ms = (S.thread && S.thread.messages) || [], c = byKey(S.cur);
    if (ms.length && ms.every((m) => typeof m.unread === 'boolean')) return;
    const incoming = ms.filter((m) => !(S.me && m.from_seat === S.me.key));
    let unread;
    if (S.thread.read_upto !== undefined) { const i = ms.findIndex((m) => m.message_id === S.thread.read_upto); unread = new Set(incoming.filter((m) => ms.indexOf(m) > i).map((m) => m.message_id)); }
    else unread = new Set(incoming.slice(Math.max(0, incoming.length - ((c && c.unread) || 0))).map((m) => m.message_id));
    for (const m of ms) m.unread = unread.has(m.message_id);
  }
  // flows → 粒子:say / decide / shout / failed → 金 / 紅 / 藍 / 灰(SPEC §2)
  const KIND = { say: 'gold', decide: 'dec', shout: 'shout', failed: 'hold' };
  async function loadFlows() {
    let f;
    try { f = await AIB.flows(600); } catch (e) { S.flowsNone = true; FX.setFlows([]); return; }
    S.flowsNone = f.source === 'none';
    const out = [];
    for (const p of f.pairs || []) {
      const tos = /^t_/.test(String(p.to || '')) ? (await members(p.to)).filter((x) => x !== p.from) : [p.to];
      for (const to of tos) for (const k in KIND) if (p[k] > 0) out.push({ a: avId(p.from), b: avId(to), kind: KIND[k], n: p[k] });
    }
    FX.setFlows(out);
  }
  const memo = {};
  async function members(tid) {
    const c = memo[tid]; if (c && Date.now() - c.at < 60000) return c.list;
    let list = []; try { const r = await AIB.thread(tid); const th = (r && (r.thread || r)) || {}; list = (th.members || []).map((m) => String(m.seat || m.member || m)).filter(Boolean); } catch (e) { list = []; }
    memo[tid] = { at: Date.now(), list }; return list;
  }
  async function loadPrefs() { try { FX.loadPrefs(await AIB.prefs()); syncOpts(); } catch (e) { /* 沒有偏好 = 用預設 */ } }

  // ---------- 動作 ----------
  async function run(p) { try { await p; S.banner = null; } catch (e) { return fail(e); } await refresh(); }
  const byKey = (k) => S.contacts.find((c) => c.key === k);
  const msgById = (id) => ((S.thread && S.thread.messages) || []).find((m) => m.message_id === id);
  async function open(key) {
    S.cur = key; S.view = 'tl'; render();
    try { await loadThread(); } catch (e) { return fail(e); }
    render();
    const ms = (S.thread && S.thread.messages) || [];
    const lastIn = [...ms].reverse().find((m) => !(S.me && m.from_seat === S.me.key));
    if (lastIn && ms.some((m) => m.unread)) run(AIB.read(AIB.threadId(S.me.key, key), lastIn.message_id));
  }
  el.addEventListener('click', (e) => {
    const x = e.target;
    if (x.closest('[data-unpair]')) { S.pair = null; return render(); }
    const ibf = x.closest('[data-ibf]'); if (ibf) { S.ibF = ibf.dataset.ibf; S.pair = null; return render(); }
    const ibp = x.closest('[data-ibp]'); if (ibp) { S.pair = S.pair === ibp.dataset.ibp ? null : ibp.dataset.ibp; return render(); }
    const ibc = x.closest('[data-ib]'); if (ibc) return ibOpen(ibc.dataset.ib);
    if (x.closest('[data-ibx]')) { S.ibOpen = null; return render(); }
    const f = x.closest('[data-f]'); if (f) { S.f = f.dataset.f; S.pair = null; return render(); }
    if (x.closest('[data-help]')) { S.help = !S.help; return render(); }
    if (x.closest('[data-fxgear]')) { S.fx = !S.fx; return render(); }
    if (x.closest('.fxpop') || x.closest('form.reply') && !x.closest('button')) return;
    if (x.closest('[data-back]')) { S.view = 'list'; return render(); }
    const rd = x.closest('[data-read]'); if (rd && S.me) return run(AIB.read(AIB.threadId(S.me.key, S.cur), rd.dataset.read));
    const dn = x.closest('[data-done]'); if (dn) { const m = msgById(dn.dataset.done); return m && run(AIB.done(m)); }
    if (x.closest('[data-reply]')) { const i = el.querySelector('form.reply input'); if (i) i.focus(); return; }
    const p = x.closest('[data-id]');
    if (!p && !x.closest('button')) {
      const hit = FX.hit(el, e);
      if (hit && hit !== 'me') { S.pair = S.ib && S.pair === hit ? null : hit; return S.ib ? render() : open(hit); }
    }
    if (p) return open(p.dataset.id);
  });
  el.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-id]')) { e.preventDefault(); open(e.target.dataset.id); }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-ib],[data-ibp]')) { e.preventDefault(); e.target.click(); }
  });
  el.addEventListener('input', (e) => {
    const i = e.target;
    if (i.matches('input[data-k]')) { FX.setOpt(i.dataset.k, +i.value); syncOpts(); return; }
    if (i.matches('[data-q]')) { S.q = i.value; return render(); }
    if (i.name === 't' && S.cur) S.drafts[S.cur] = i.value;
  });
  el.addEventListener('submit', (e) => {
    const form = e.target.closest('form.reply'); if (!form) return;
    e.preventDefault();
    const key = form.dataset.send, text = (form.elements.t.value || '').trim();
    if (!text) return;
    // 同一段字只配一把 idem_key:送出失敗再按一次是重送同一封,不是第二封
    const idem = S.idem && S.idem.key === key && S.idem.text === text ? S.idem.id : (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
    S.idem = { key, text, id: idem };
    run(AIB.send(key, text, S.me ? AIB.threadId(S.me.key, key) : undefined, idem).then((r) => { S.drafts[key] = ''; S.idem = null; return r; }));
  });
  NARROW.addEventListener?.('change', render);
  FX.onSaveError = fail;

  // ---------- 開始 ----------
  el._s = S; // fx.js 用 el._s.pair 篩「只看兩人」
  render();
  FX.attach(el);
  if (!AIB.hasToken()) { say(t('need_pair'), 'err'); return; }
  loadPrefs();
  // 網址 #<key>(例 #seat:demo1)直接開那個人:給通知連結與截圖驗收用
  let deep = '';
  try { deep = decodeURIComponent(location.hash.slice(1)); } catch (e) { deep = ''; } // 壞的 # 段不擋開始
  refresh().then(() => { if (deep && byKey(deep)) open(deep); });
  // 輪詢:分頁藏起來就停(SPEC §2 同一原則)
  setInterval(() => { if (!document.hidden) refresh(); }, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
