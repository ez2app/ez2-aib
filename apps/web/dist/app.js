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
  const FILTERS = ['all', 'dec', 'unread', 'h', 'archived'];
  function head() {
    const dec = S.contacts.reduce((a, c) => a + (c.archived_at ? 0 : c.decide_open || 0), 0);
    return `<div class="title"><span class="lv">2.0</span><span class="nm">${esc(t('title'))}</span><small>aibd</small></div>
    <div class="bar"><div class="tabs"><button type="button" class="later" disabled>${esc(t('tab_chat'))}</button><button type="button" aria-pressed="true">${esc(t('tab_contacts'))}${dec ? ` <b class="n-red">${Number(dec) | 0}</b>` : ''}</button></div><span class="me-node"><span class="av live" data-av="me" aria-hidden="true">👑</span>${esc(t('me'))}${S.me ? ' ' + esc(S.me.name || '') : ''}</span><button type="button" class="pill fxgear" data-fxgear="1" aria-label="${esc(t('fx'))}" title="${esc(t('fx'))}" aria-expanded="${S.fx}">✨</button></div>`;
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
    if (NARROW.matches) body = S.view === 'tl' && cur ? `<div class="panel">${timeline(cur, true)}</div>` : `<div class="panel list">${listHtml()}</div>`;
    else body = `<div class="two"><div class="panel list">${listHtml()}</div><div class="panel">${timeline(cur, false)}</div></div>`;
    const pc = S.pair ? S.contacts.find((c) => c.key === S.pair) : null;
    const pairChip = pc ? `<div class="bar"><button type="button" class="pairchip" data-unpair="1">${esc(t('only_pair', { name: pc.name }))}</button></div>` : '';
    const banner = S.banner ? `<div class="${S.bannerKind === 'err' ? 'err-banner' : 'off-banner'}" role="status">${esc(S.banner)}</div>` : '';
    const flowsNote = S.flowsNone && S.fx ? `<div class="off-banner">${esc(t('flows_none'))}</div>` : '';
    el.innerHTML = head() + (S.fx ? fxPop() : '') + flowsNote + filters() + pairChip + (S.help ? `<div class="helpbox">${esc(t('help_text'))}</div>` : '') + banner + body;
    syncOpts();
    if (el._cv) { el.appendChild(el._cv); FX.rendered(el); }
    const m = el.querySelector('.msgs'); if (m) m.scrollTop = m.scrollHeight;
    if (focus) { const n = el.querySelector(focus); if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) {} } }
  }
  function say(msg, kind) { S.banner = msg; S.bannerKind = kind || ''; render(); }
  function fail(e) {
    if (e && e.status === 409) { say(t('conflict')); return refresh(); }
    if (e && e.status === 0) return say(t('offline'), 'err');
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
      if (S.cur) await loadThread();
      if (S.bannerKind === 'err' || S.banner === t('offline')) S.banner = null;
      await loadFlows();
    } catch (e) {
      if (e && (e.status === 401 || e.status === 403)) S.banner = t('need_pair'), S.bannerKind = 'err';
      else if (e && e.status === 0) S.banner = t('offline'), S.bannerKind = 'err';
      else S.banner = t('err', { m: (e && (e.code || e.message)) || '?' }), S.bannerKind = 'err';
    } finally { busy = false; render(); }
  }
  async function loadThread() {
    if (!S.me || !S.cur) { S.thread = null; return; }
    try { S.thread = await AIB.thread(AIB.threadId(S.me.key, S.cur)); S.threadOf = S.cur; markUnread(); }
    catch (e) { if (e && e.status === 404) { S.thread = { messages: [] }; S.threadOf = S.cur; } else throw e; }
  }
  // 未讀:thread 有 read_upto(讀到哪一則)就照它;沒有就照 contacts 的 unread 數取最後幾則別人寄的
  function markUnread() {
    const ms = (S.thread && S.thread.messages) || [], c = byKey(S.cur);
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
    for (const p of f.pairs || []) for (const k in KIND) if (p[k] > 0) out.push({ a: avId(p.from), b: avId(p.to), kind: KIND[k], n: p[k] });
    FX.setFlows(out);
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
    const c = byKey(key);
    if (lastIn && c && c.unread) run(AIB.read(AIB.threadId(S.me.key, key), lastIn.message_id));
  }
  el.addEventListener('click', (e) => {
    const x = e.target;
    if (x.closest('[data-unpair]')) { S.pair = null; return render(); }
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
      if (hit && hit !== 'me') { S.pair = hit; return open(hit); }
    }
    if (p) return open(p.dataset.id);
  });
  el.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-id]')) { e.preventDefault(); open(e.target.dataset.id); }
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
