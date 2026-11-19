// 多席工作台第一版(N-workspace;SPEC-workspace-multiseat-519d0-1118 §10;樣子照 board/mock-workspace-v8-shared / -seat)。
// 內建首頁(apps/web/dist/index.html;聯絡中心改在 contacts.html,0eed7 r3:Andy 開原網址就要看到工作台;519d0 09:3x 裁定改走內建:本文只給內建頁);只打快修鏈頭 3a2d6b09 已有的 /api/v1(讀 GET、寫 POST /api/v1/act),
// 不加新介面;沒有的資料留空不顯示,不做假資料。
// 順序:席的運作窗 → 在場的席與雙擊切換 → 共同對話(@ 送字)→ 右欄事件 → 左樹唯讀 → 主機。
'use strict';
(function () {
  // ---------- 呼叫層(同聯絡中心 api.js:憑證在這個分頁的 sessionStorage,配對交換頁存的) ----------
  const KEY = 'aib.page_credential';
  // aibd 頁面的 CSP 是 style-src 'self':HTML 裡寫的 style 屬性一律被擋(動畫延遲、進度、線色都會失效)。
  // 畫面一律寫 data-sty="…",元素一放進頁面就用 CSSOM 套上(CSP 允許程式設 style)
  function applySty(root) {
    const one = (e) => { const v = e.getAttribute('data-sty'); if (v == null) return; e.removeAttribute('data-sty'); for (const d of v.split(';')) { const i = d.indexOf(':'); if (i > 0) e.style.setProperty(d.slice(0, i).trim(), d.slice(i + 1).trim()); } };
    if (root.nodeType !== 1) return; if (root.hasAttribute('data-sty')) one(root); for (const e of root.querySelectorAll('[data-sty]')) one(e);
  }
  new MutationObserver((ms) => { for (const m of ms) for (const nd of m.addedNodes) applySty(nd); }).observe(document.documentElement, { childList: true, subtree: true });
  const PAIRING = { on: false };
  const PAGE_N = (() => { try { const a = new Uint8Array(16); crypto.getRandomValues(a); return [...a].map((x) => x.toString(16).padStart(2, '0')).join(''); } catch (e) { return String(Date.now()) + Math.random().toString(16).slice(2); } })(); // 配對碼送出中(一次只送一個)
  const token = () => { try { return sessionStorage.getItem(KEY) || ''; } catch (e) { return ''; } };
  class ApiError extends Error {
    constructor(status, code, message) { super(message || code || String(status)); this.status = status; this.code = code || ''; }
  }
  async function call(method, path, body) {
    const h = { Accept: 'application/json' };
    const t = token();
    if (t) h.Authorization = 'Bearer ' + t;
    const init = { method, headers: h, cache: 'no-store', credentials: 'omit' };
    if (body !== undefined) { h['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, init); } catch (e) { throw new ApiError(0, 'offline', String(e)); }
    let j = {};
    try { j = await r.json(); } catch (e) { j = {}; }
    if (!r.ok) { const er = new ApiError(r.status, j.code, j.message); er.reason = j.reason || ''; throw er; }
    j.__status = r.status;
    return j;
  }
  const get = (p) => call('GET', p);
  const act = (type, f) => call('POST', '/api/v1/act', Object.assign({ type }, f));
  const soft = (p) => p.catch((e) => (e && (e.status === 404 || e.status === 403) ? null : Promise.reject(e)));

  // ---------- 狀態 ----------
  const S = {
    seats: [], sessions: [], now: [], thermal: null, metrics: null, me: null, hosts: null,
    tree: null, kids: null, open: new Set(), sel: null,
    mode: 'shared', // 'shared' 或 seat_id(某一席自己的運作窗)
    out: {}, // session_id → {items:[], next, done}
    threads: {}, // seat_id → messages[]
    banner: null, bannerKind: '', hint: '', hintKind: '', sending: false,
    drafts: { shared: '' }, atOpen: false, sentText: {}, pairs: {},
    paired: false, pair: null,
    nt: null, posts: [], ntOff: false,
    stopArm: null, rejoin: {}, q: '', sr: null, card: null, itemTalk: [], itemOff: false, cat: '', catCounts: null, catThreads: null, evOpen: undefined, evAll: false, answered: {}, ansDraft: {}, version: undefined, add: null, aslots: null, usage: null, dismissed: new Set(), readSent: new Set(), readOff: false, ntReadOff: false,
  };
  const el = document.getElementById('app');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // 時間一律照顯示時區(519d0 11-18 21:2x 裁:照 board-config tz_name = Asia/Taipei,不用這台本機時區;aibd 之後給設定就換)
  const DISP_TZ = 'Asia/Taipei', DISP_TZL = '台北';
  const TP = (() => { try { return new Intl.DateTimeFormat('en-GB', { timeZone: DISP_TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }); } catch (e) { return null; } })();
  // 顯示時區的 {mo, d, h, mi}(字串、補零);格式器不能用就退本機
  function tparts(ms) {
    const dt = new Date(ms); if (!Number.isFinite(dt.getTime())) return null;
    if (TP) { const o = {}; for (const x of TP.formatToParts(dt)) o[x.type] = x.value; return { mo: o.month, d: o.day, h: o.hour === '24' ? '00' : o.hour, mi: o.minute }; }
    const z = (n) => String(n).padStart(2, '0'); return { mo: z(dt.getMonth() + 1), d: z(dt.getDate()), h: z(dt.getHours()), mi: z(dt.getMinutes()) };
  }
  const hhmm = (iso) => { const p = tparts(new Date(iso).getTime()); return p ? p.h + ':' + p.mi : ''; };
  const ago = (iso) => { const d = new Date(iso).getTime(); if (!Number.isFinite(d)) return ''; const m = Math.max(0, Math.round((Date.now() - d) / 60000)); return m < 1 ? '剛剛' : m < 60 ? m + ' 分前' : Math.round(m / 60) + ' 時前'; };

  // ---------- 席 ----------
  const seatName = (s) => String(s.display || s.name || s.seat_id);
  const seatById = (id) => S.seats.find((s) => s.seat_id === id);
  const lastSession = (id) => S.sessions.find((x) => x.seat_id === id) || null; // sessions 新到舊
  const nowOf = (id) => { const r = S.now.find((x) => x.seat_id === id); return r ? r.now : null; };
  const running = (id) => { const x = lastSession(id); return !!x && x.state === 'running'; };
  const present = () => S.seats.filter((s) => { const x = lastSession(s.seat_id); return x && (x.state === 'running' || x.state === 'pending'); });
  const BRAND = { claude: 'bc', codex: 'bx', gemini: 'bg', grok: 'bk' };
  const ICON = {
    bc: '<svg viewBox="0 0 24 24"><g stroke="#000" stroke-width="2.6" stroke-linecap="round"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4"/></g></svg>',
    bx: '<svg viewBox="0 0 24 24"><path d="M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z" fill="none" stroke="#000" stroke-width="2.4" stroke-linejoin="round"/><circle cx="12" cy="12" r="2.4" fill="#000"/></svg>',
    bg: '<svg viewBox="0 0 24 24"><path d="M12 2c.9 5.4 4.6 9.1 10 10-5.4.9-9.1 4.6-10 10-.9-5.4-4.6-9.1-10-10 5.4-.9 9.1-4.6 10-10z" fill="#000"/></svg>',
    bk: '<svg viewBox="0 0 24 24"><g stroke="#000" stroke-width="2.8" stroke-linecap="round"><path d="M5 19L19 5M6 6l5 5M13.5 13.5L18 18"/></g></svg>',
  };
  const modelText = (s) => [s.provider ? s.provider[0].toUpperCase() + s.provider.slice(1) : '', s.model].filter(Boolean).join(' ');
  // 一個標誌:品牌色 + 外框(綠 = 在跑,金 = 等回應 / 等你決定);滑過看席位與模型;雙擊切到該席
  function logo(s, size, extra) {
    const b = BRAND[s.provider] || 'bc';
    const n = nowOf(s.seat_id);
    const ring = n && (n.state === 'reply' || n.state === 'decide') ? 'wq' : running(s.seat_id) ? 'lit' : '';
    // 0eed7 L3 / r4:同名的席滑過也分得出 ⇒ 帶席碼前 5 碼(跟 aibd 同名加的尾碼同一種);名字已帶就不再加
    const code = s.code || String(s.seat_id).replace(/^seat_/, '').slice(0, 5); // aibd seats.rs seat_code
    const tip = '@' + seatName(s) + (modelText(s) ? ' · ' + modelText(s) : '') + (s.mode ? ' · ' + s.mode : '') + (seatName(s).includes(code) ? '' : ' · ' + code);
 // 0eed7 L4:能改程式(dgrs)的席加紅角標,仍是同一個標誌
    const dg = s.mode === 'dgrs' ? 'dgm' : '';
    return `<span class="lg ${size || ''} ${b} ${ring} ${dg} ${extra || ''}" tabindex="0" data-seat="${esc(s.seat_id)}" aria-label="${esc(tip)}">${ICON[b]}<span class="tip">${esc(tip)}</span></span>`;
  }

  // ---------- 讀資料 ----------
  async function loadCore() {
    const [seats, sessions, now, thermal, metrics, contacts, host, slotsR, usage, hosts, pv] = await Promise.all([
      get('/api/v1/seats'), soft(get('/api/v1/sessions')), soft(get('/api/v1/now')),
      soft(get('/api/v1/thermal')), soft(get('/api/v1/metrics')), soft(get('/api/v1/contacts')), soft(get('/api/v1/host')),
      soft(get('/api/v1/slots')), soft(get('/api/v1/usage')), soft(get('/api/v1/hosts')), soft(get('/api/v1/preview')),
    ]);
    S.pv = pv && typeof pv === 'object' ? pv : null;
    S.hosts = hosts && Array.isArray(hosts.hosts) ? hosts.hosts : null;
    // 7ebca 事件卡槽 + 用量(第二波);這版 aibd 沒有 ⇒ null,事件欄照頁面自己算
    if (S.version === undefined) S.version = await soft(get('/api/v1/version')).catch(() => null);
    S.aslots = slotsR && Array.isArray(slotsR.slots) ? slotsR.slots : null;
    // 7ebca §21:counts 有 ⇒ 這版 aibd 會分頁(slot / page / per / as_of);件數與 as_of 每輪即時
    S.slotCounts = slotsR && slotsR.counts && typeof slotsR.counts === 'object' ? slotsR.counts : null;
    if (S.slotCounts && slotsR.as_of && !S.slotAsOf) S.slotAsOf = String(slotsR.as_of);
    S.usage = usage && Array.isArray(usage.usage) ? usage : null;
    S.seats = (seats && seats.seats) || [];
    S.sessions = (sessions && sessions.sessions) || [];
    S.now = (now && now.now) || [];
    S.thermal = thermal; S.metrics = metrics; S.host = host;
    const me = contacts && (contacts.contacts || []).find((c) => c.kind === 'human' && c.self);
    S.me = me ? me.key : null;
  }
  // 席的輸出:每一行是 Claude Code stream-json 的一筆;只往後讀(after = next)
  async function loadOut(seatId) {
    const se = lastSession(seatId);
    if (!se) return;
    const id = se.id || se.session_id;
    const o = S.out[id] || (S.out[id] = { items: [], next: 0 });
    for (let i = 0; i < 6; i++) {
      const r = await get('/api/v1/sessions/' + encodeURIComponent(id) + '/output?after=' + o.next + '&limit=500');
      const L = r.output || [];
      o.items.push(...L);
      if (o.items.length > 4000) o.items.splice(0, o.items.length - 4000);
      o.next = r.next != null ? r.next : o.next;
      if (L.length < 500) break;
    }
  }
  const threadId = (a, b) => [a, b].sort().join('~');
  // 今晚派工(519d0 ORDER-overnight):席在項目對話裡彼此 @ 的話(a9136 thread_send 工具)也要在首頁的共同對話看得到。
  // 讀最近的 12 段項目對話(GET /api/v1/threads,新的在前),每段的貼文併進共同對話,前面帶「這是哪一項」;點了開那一項
  async function loadItemTalk() {
    if (S.itemOff) return;
    let L = null;
    try { L = await get('/api/v1/threads?limit=12'); } catch (e) { if (e && (e.status === 404 || e.status === 405 || e.status === 400)) { S.itemOff = true; return; } throw e; }
    S.catCounts = L && L.counts && typeof L.counts === 'object' ? L.counts : null;
    if (S.mode === 'cat' && S.cat) { const C = await soft(get('/api/v1/threads?' + (S.cat === 'all' ? '' : 'category=' + encodeURIComponent(S.cat) + '&') + 'limit=50')).catch(() => null); S.catThreads = (C && C.threads) || []; }
    const ts = ((L && L.threads) || []).filter((t) => t && t.thread_id && t.node).slice(0, 12);
    const out = [];
    await Promise.all(ts.map(async (t) => {
      const r = await soft(get('/api/v1/threads/' + encodeURIComponent(t.thread_id))).catch(() => null);
      for (const p of (r && r.posts) || []) out.push(Object.assign({ _node: t.node, _title: t.title || t.node, _tid: t.thread_id }, p));
    }));
    S.itemTalk = out;
  }
  // §17 分類分頁(a9136 DESIGN-thread-cat §2):件數讀 aibd 的 counts,頁面不自己數、不放說明字;沒有 counts(舊 aibd)就不出分頁
  const CATS = [['council', 'AI 遇事不決'], ['summary', '總結'], ['handoff', '交接'], ['drive', '自動推進'], ['shadow', '影子續跑']];
  // 分頁:全部 + 五類 + 一般(沒分類的;Andy 19:4x 在頁面找不到「overnight 驗收」那段 = 沒分類、沒掛項目的對話原本沒入口)
  const CAT_TABS = [['all', '全部']].concat(CATS, [['none', '一般']]);
  function catBar() {
    const C = S.catCounts; if (!C) return '';
    return `<div class="cats">${CAT_TABS.map(([k, t]) => `<button type="button" class="ct ${S.mode === 'cat' && S.cat === k ? 'on' : ''}" data-cat="${k}" ${C[k] ? '' : 'disabled'}>${esc(t)} <i>${esc(C[k] || 0)}</i></button>`).join('')}</div>`;
  }
  function catWin() {
    const L = S.catThreads || [], name = (CAT_TABS.find((c) => c[0] === S.cat) || [])[1] || '';
    const tt = (t) => (S.tree && t.node && S.tree.get(t.node) ? S.tree.get(t.node).title : '') || t.title || t.node || t.thread_id;
    return `${headHtml()}<div class="tl" id="tl">${L.length ? L.map((t) => `<div class="cth" data-th="${esc(t.thread_id)}" data-tt="${esc(String(tt(t)).slice(0, 60))}"><b>${esc(String(tt(t)).slice(0, 80))}</b><span class="tm">${esc(hhmm(t.created))}</span></div>`).join('') : `<div class="empty">${esc(name)}還沒有對話</div>`}</div>`;
  }
  // 一段對話直接用 thread_id 打開(分類清單裡沒有掛項目的那些,例如匯入的);讀與已讀照項目對話
  async function openThread(tid, title) {
    S.nt = { thread_id: tid, title }; S.mode = 'node'; S.hint = '';
    try { await loadNode(); } catch (e) { say(errText(e), 'err'); }
    render();
  }
  // §17 第 4 點:總結 / 交接的貼文帶 doc(一份或一串)⇒ 每份一個可點的連結;內容由 aibd 照「哪段對話、哪則、第幾份」給
  // (7b81e GET /api/v1/doc?thread_id=&message_id=&n=,5d33a 4ad11ad7 L1),頁面不送路徑
  function docLink(p) {
    const d = p && p.doc, tid = p && (p.thread_id || p._tid || (S.nt && S.nt.thread_id));
    if (!d || !p.message_id || !tid) return '';
    const L = Array.isArray(d) ? d : [d];
    return `<div class="dl">${L.map((x, n) => x ? `<a data-doc="${esc(p.message_id)}" data-dtid="${esc(tid)}" data-dn="${n}" title="${esc(x.file || '')}">📄 ${esc(x.title || x.file || '文件')}</a>` : '').join(' ')}</div>`;
  }
  async function openDoc(tid, id, n, label) {
    let text = '', bad = '';
    try {
      const h = { Accept: 'text/plain' }; const t = token(); if (t) h.Authorization = 'Bearer ' + t;
      const r = await fetch('/api/v1/doc?thread_id=' + encodeURIComponent(tid) + '&message_id=' + encodeURIComponent(id) + '&n=' + (Number(n) || 0), { headers: h, cache: 'no-store', credentials: 'omit' });
      if (r.ok) text = await r.text(); else { let j = {}; try { j = await r.json(); } catch (e) { /* 不是 json */ } bad = r.status === 404 ? '這份文件讀不到(可能還沒有這個介面,或檔已不在)' : r.status === 403 ? '這個配對不能讀文件' + (j.code ? '(' + j.code + ')' : '') : '讀不到(' + r.status + ')'; }
    } catch (e) { bad = '連不上 aibd'; }
    let d = document.getElementById('docdlg');
    if (!d) { d = document.createElement('dialog'); d.id = 'docdlg'; d.className = 'cdlg'; document.body.appendChild(d); d.addEventListener('click', (e) => { if (e.target === d || e.target.closest('[data-dclose]')) d.close(); }); }
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="kid">📄</span><span>${esc(label || '')}</span><button type="button" class="x" data-dclose="1" aria-label="關閉" autofocus>✕</button></div></div><div class="db">${bad ? `<div class="none">${esc(bad)}</div>` : `<pre class="doct">${esc(text)}</pre>`}</div><div class="df"><span></span><span>按 Esc 或點外面關閉</span></div></div>`;
    if (!d.open) d.showModal();
  }
  async function loadThreads() {
    if (!S.me) return;
    await loadItemTalk();
    const ids = S.seats.map((s) => s.seat_id);
    await Promise.all(ids.map(async (id) => {
      try { const r = await get('/api/v1/threads/' + encodeURIComponent(threadId(S.me, 'seat:' + id))); S.threads[id] = r.messages || []; }
      catch (e) { if (e && e.status === 404) S.threads[id] = []; else throw e; }
    }));
    await loadPairs();
  }
  // 0eed7 VERIFY-humanthread M2:席之間的一對一信也要在共同對話看得到 —— 內建頁可讀同艙兩席的串(519d0 11-18 裁定 2,
  // aibd page_reads_seat_pair);只讀在場席兩兩之間、最多 8 席,隔 3 輪讀一次;403 / 用戶頁 ⇒ 不再試
  const PAIRS = { n: 0, off: false };
  async function loadPairs() {
    if (PAIRS.off || PAIRS.n++ % 3) return;
    const P = present().slice(0, 8), jobs = [];
    for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) jobs.push([P[i].seat_id, P[j].seat_id]);
    const out = {};
    await Promise.all(jobs.map(async ([a, b]) => {
      const k = threadId('seat:' + a, 'seat:' + b);
      try { const r = await get('/api/v1/threads/' + encodeURIComponent(k)); out[k] = r.messages || []; }
      catch (e) { if (e && e.status === 403) PAIRS.off = true; out[k] = []; }
    }));
    if (!PAIRS.off) S.pairs = out;
  }
  async function loadTree() {
    try {
      // 7b81e:先讀 GET /api/v1/tree(<data>/tree.json,欄位 station);這版 aibd 沒有這條或沒放檔
      // ⇒ 退回用戶資料夾的 /tree.json(欄位 status)。兩形都收成 status
      let d = null;
      try { d = await get('/api/v1/tree'); } catch (e) { if (!(e && (e.status === 404 || e.status === 405 || e.status === 503))) throw e; }
      if (!d || d.source === 'none') {
        const r = await fetch('/tree.json', { cache: 'no-store', credentials: 'omit' });
        if (!r.ok) return;
        d = await r.json();
      }
      const nodes = (d && d.nodes) || [];
      for (const n of nodes) if (n.status == null && n.station != null) n.status = n.station;
      const kids = new Map();
      for (const n of nodes) { if (n.status === '作廢' || n.kind === 'suggestion') continue; const p = n.parent || ''; if (!kids.has(p)) kids.set(p, []); kids.get(p).push(n); }
      S.tree = new Map(nodes.map((n) => [n.id, n])); S.kids = kids;
      const root = (kids.get('') || [])[0];
      if (root) { S.open.add(root.id); if (!S.sel) S.sel = root.id; }
    } catch (e) { /* 沒有 tree.json ⇒ 左樹留空 */ }
  }

  // ---------- 左:專案樹(唯讀) ----------
  const STATIONS = ['未開始', '已定未做', '開發中', '審過未推', '審過排隊', '未驗', '驗過'];
  // 跟流程表 stations.html 的 ST 同一張對照(0eed7 VERIFY-cardview-27df927e M1:實查不通過是第 3 站「開發中」,不是未驗)
  const stationIdx = (st) => ({ 未開始: 0, 等前置: 0, 暫停: 0, 已定未做: 1, 已同意未做: 1, 開發中: 2, 卡住: 2, 實查不通過: 2, 審過未推: 3, 審過排隊: 4, 未驗: 5, 驗收中: 5, 驗過: 6, 實查通過: 6 }[st]);
  const LV = ['總綱', '大項目', '子項目'];
  function doneShare(id) {
    let all = 0, ok = 0;
    const walk = (k) => { for (const c of S.kids.get(k) || []) { if (c.status) { all++; if (stationIdx(c.status) === 6) ok++; } walk(c.id); } };
    walk(id);
    return all ? [ok, all] : null;
  }
  function treeRows(pid, depth, out) {
    for (const n of S.kids.get(pid) || []) {
      const kids = (S.kids.get(n.id) || []).length;
      const isOpen = S.open.has(n.id);
      const si = stationIdx(n.status);
      const lv = depth < 3 && kids ? LV[depth] : 'Task';
      const share = kids ? doneShare(n.id) : null;
      const right = share ? `<progress class="mini" max="${share[1]}" value="${share[0]}" data-card="${esc(n.id)}" title="驗過 ${share[0]} / ${share[1]}"></progress>`
        : si != null ? `<span class="stn s${si + 1}" data-card="${esc(n.id)}" title="打開這張卡">${si + 1} ${esc(STATIONS[si])}</span>` : n.status ? `<span class="stn" data-card="${esc(n.id)}" title="打開這張卡">${esc(n.status)}</span>` : '<span></span>';
      const who = [n.owner].concat(n.with || []).filter((x) => x && /^[0-9a-f]{5}$/.test(x));
      const seats = who.map((c) => `<span class="lg sm bc off" title="@${esc(c)}(舊版席)">${ICON.bc}</span>`).join('');
      out.push(`<div class="row d${Math.min(depth, 3)} ${S.sel === n.id ? 'sel' : ''}" data-node="${esc(n.id)}">
        <div class="nm"><span class="tw" data-tw="${esc(n.id)}">${kids ? (isOpen ? '▾' : '▸') : '·'}</span><span class="lv ${depth === 0 ? 'a' : ''}" data-card="${esc(n.id)}" title="打開這張卡">${esc(lv)}</span><span class="tx" title="${esc(n.id + ' ' + (n.title || ''))}">${esc(n.title || n.id)}</span></div>
        ${right}<div class="seats">${seats}</div></div>`);
      if (kids && isOpen) treeRows(n.id, depth + 1, out);
    }
  }
  function treeHtml() {
    if (!S.kids) return '<div class="bh"><b>專案</b></div>';
    const out = [];
    treeRows('', 0, out);
    return `<div class="bh"><b>專案</b><small>唯讀</small></div><div class="treebox">${out.join('')}</div>`;
  }

  // ---------- 搜尋(SPEC §18,Andy 19:5x「要有一個 搜尋框 搜尋出來 要分類」)----------
  // 查詢在 aibd(a9136 GET /api/v1/search,IFACE-search-a9136-1118 r1):groups 依序 node / thread(by_category 六類,general = 一般)/ seat / doc / andy_open,
  // 每組 count + 前 limit 筆;full=false(用戶頁 / 延續配對)時沒有 snippet / text,頁面照有什麼顯示;
  // 這版 aibd 沒有(404)⇒ 只在頁面已經有的專案項目與席位裡找,其他組不出(不為搜尋把資料全抓回來)
  const SR_ORDER = [['node', '', '專案項目']].concat(CATS.map(([k, t]) => ['thread', k, '對話 · ' + t]), [['thread', 'none', '對話 · 一般'], ['seat', '', '席位'], ['doc', '', '文件'], ['decision', '', '待人決策']]);
  const SR = { timer: 0, seq: 0, off: false };
  const srKey = (kind, cat) => kind + ':' + (kind === 'thread' ? cat || 'none' : '');
  function srNormalize(r) {
    const G = new Map();
    const add = (kind, cat, it, total) => { const k = srKey(kind, cat); const g = G.get(k) || { items: [], total: 0 }; if (it) g.items.push(it); g.total = Math.max(g.total, total || 0, g.items.length); G.set(k, g); };
    const cat = (c) => (c === 'general' || !c ? 'none' : c);
    if (r && Array.isArray(r.groups)) for (const g of r.groups) {
      if (!g) continue;
      const kind = g.kind === 'andy_open' ? 'decision' : g.kind, its = Array.isArray(g.items) ? g.items : [], total = Number(g.count != null ? g.count : g.total) || 0;
      if (kind === 'thread' && g.by_category && typeof g.by_category === 'object') {
        // 對話再分類:件數照 by_category,筆照各筆 category
        for (const [c, n] of Object.entries(g.by_category)) if (Number(n) > 0) add('thread', cat(c), null, Number(n));
        for (const it of its) add('thread', cat(it && it.category), it, 0);
        continue;
      }
      if (!its.length && !total) continue;
      for (const it of its) add(kind, cat(g.category), it, total);
      if (!its.length) add(kind, cat(g.category), null, total);
    }
    else if (r && Array.isArray(r.results)) for (const it of r.results) if (it) add(it.kind, it.category, it, 0);
    return G;
  }
  function srLocal(q) {
    const G = new Map(), Q = q.toLowerCase(), hit = (v) => typeof v === 'string' && v.toLowerCase().includes(Q);
    const nodes = []; if (S.tree) for (const [id, n] of S.tree) if (hit(id) || hit(n.title)) nodes.push({ node: id, title: n.title || id });
    if (nodes.length) G.set(srKey('node'), { items: nodes.slice(0, 20), total: nodes.length });
    const seats = S.seats.filter((x) => hit(seatName(x)) || hit(x.seat_id) || hit(x.provider) || hit(x.persona_name)).map((x) => ({ seat_id: x.seat_id, title: '@' + seatName(x) }));
    if (seats.length) G.set(srKey('seat'), { items: seats.slice(0, 20), total: seats.length });
    return G;
  }
  async function runSearch() {
    const q = S.q.trim(), my = ++SR.seq;
    if (!q) { S.sr = null; return render(); }
    let G = null;
    if (!SR.off) {
      try { G = srNormalize(await get('/api/v1/search?q=' + encodeURIComponent(q) + '&limit=20')); }
      catch (e) { if (e && (e.status === 404 || e.status === 405)) SR.off = true; else { if (my === SR.seq) { say(errText(e), 'err'); render(); } return; } }
    }
    if (my !== SR.seq) return; // 打字中又有新的
    S.sr = G || srLocal(q); render();
  }
  // 搜尋回的席碼可能是 seat_id 或五碼(IFACE 範例寫 a9136)⇒ 對到頁面席表的 seat_id
  const srSeat = (v) => (!v ? '' : seatById(v) ? v : ((S.seats.find((x) => String(x.seat_id).endsWith(v) || String(x.code || '') === v || seatName(x).endsWith(v)) || {}).seat_id || ''));
  function srItem(kind, it) {
    const str = (v) => (typeof v === 'string' ? v : '');
    // r2:席回 display(含尾碼)與 state;seat_id 跟 GET /api/v1/seats 同一個
    const title = kind === 'seat' && (str(it.display) || str(it.persona)) ? '@' + (str(it.display) || str(it.persona) + (str(it.suffix) ? '-' + str(it.suffix) : ''))
      : kind === 'decision' ? str(it.text) || str(it.id) || '(沒有內容)'
      : str(it.title) || str(it.file) || str(it.name) || str(it.node) || str(it.thread_id) || str(it.id) || '(沒有標題)';
    const at = kind === 'thread' && str(it.last_at) ? when(it.last_at) : '';
    const sub = kind === 'seat' ? [str(it.brand), it.state === 'running' || it.running ? '在跑' : str(it.state)].filter(Boolean).join(' · ') : kind === 'decision' ? [str(it.id), str(it.card)].filter(Boolean).join(' · ') : kind === 'node' ? str(it.station) : str(it.snippet) || (kind === 'doc' && str(it.title) ? str(it.file) : '');
    const node = str(it.node) || str(it.card) || (kind === 'node' ? str(it.id) : '');
    const a = kind === 'node' && node ? `data-card="${esc(node)}"`
      : kind === 'thread' && str(it.thread_id || it.id) ? `data-th="${esc(str(it.thread_id || it.id))}" data-tt="${esc(title.slice(0, 60))}"`
      : kind === 'seat' && srSeat(str(it.seat_id || it.id)) ? `data-go="${esc(srSeat(str(it.seat_id || it.id)))}"`
      : kind === 'doc' && str(it.thread_id) && str(it.message_id) ? `data-doc="${esc(str(it.message_id))}" data-dtid="${esc(str(it.thread_id))}" data-dn="${Number(it.n) || 0}"`
      : node ? `data-card="${esc(node)}"` : '';
    return `<div class="sri${a ? '' : ' na'}" data-sr="1" ${a}><b>${esc(title.slice(0, 100))}${at ? ` <time>${esc(at)}</time>` : ''}</b>${str(it.id) && kind === 'node' ? `<code>${esc(str(it.id))}</code>` : ''}${sub ? `<span>${esc(sub.slice(0, 140))}</span>` : ''}</div>`;
  }
  function searchWin() {
    const G = S.sr; if (!G) return `<div class="srw"><div class="empty">…</div></div>`;
    const out = [];
    for (const [kind, cat, name] of SR_ORDER) {
      const g = G.get(srKey(kind, cat)); if (!g || !g.total) continue;
      out.push(`<div class="srg"><div class="srh">${esc(name)} <i>${esc(g.total)}</i></div>${g.items.map((it) => srItem(kind, it)).join('')}</div>`);
    }
    return `<div class="srw">${out.length ? out.join('') : '<div class="empty">沒有找到</div>'}</div>`;
  }

  // ---------- 試用預覽版(SPEC §20,Andy 20:3x)----------
  // aibd(a9136)形狀未定,頁面先讀:GET /api/v1/preview → {available, version, running, url?, consented_version?};
  // 啟用 act preview_start {version, consent: true, idem_key} → {url};關 act preview_stop {}。這版 aibd 沒有 ⇒ 勾選框不出
  // 預覽區自己的頁:GET /api/v1/version 帶 preview: true(或 channel: "preview")⇒ 最上方一直標「預覽 · 非正式區」
  const PV_TEXT = [
    '這是還沒審過、還沒跑完測試的版本,可能打不開、可能有壞掉的地方。',
    '它開在「非正式區」:另一個位址、另一份資料;你在裡面做的事不會進正式的資料,正式區照常運作、不受影響。',
    '非正式區要另外配對一次;在裡面開 Claude 的席,第一次也要另外登入一次。',
    '在非正式區開「能改程式的席」,它改的是這台電腦上真的檔案——這一點跟正式區一樣,不會因為是預覽就變成假的。',
    '隨時可以取消勾選,非正式區就關掉;裡面的資料留著,下次再開還在,也可以選擇清掉。',
  ];
  const pvBar = () => (inPreview() ? '<div class="pvbar" role="status">預覽 · 非正式區</div>' : '');
  (async () => { try { const r = await fetch('/api/v1/health', { cache: 'no-store', credentials: 'omit' }); if (r.ok) { const j = await r.json(); if (j && j.preview === true) { S.pvZone = true; render(); } } } catch (e) { /* 舊 aibd 沒這欄 */ } })();
  const inPreview = () => !!S.pvZone || !!(S.version && (S.version.preview === true || S.version.channel === 'preview'));
  function pvBox() {
    const P = S.pv; if (!P || inPreview()) return '';
    const on = !!P.running || S.pvAsk;
    return `<label class="pvc ${P.available ? '' : 'off'}" title="${P.available ? '' : '這台沒有可用的預覽版'}"><input type="checkbox" id="pvck" ${on ? 'checked' : ''} ${P.available || P.running ? '' : 'disabled'}> 試用預覽版${P.running ? `<span class="pvs" title="${esc([P.running_version, P.running_sha256 ? String(P.running_sha256).slice(0, 12) : ''].filter(Boolean).join(' · '))}">非正式區在跑${P.running_version && P.running_version !== P.version ? '(另一版)' : ''}</span>` : ''}</label>`;
  }
  function pvUndo() { S.pvAsk = false; const c = document.getElementById('pvck'); if (c) c.checked = !!(S.pv && S.pv.running); }
  function pvDialog() {
    let d = document.getElementById('pvdlg');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'pvdlg'; d.className = 'cdlg pvd'; document.body.appendChild(d);
      d.addEventListener('click', async (e) => {
        if (e.target.closest('[data-pvno]') || e.target === d) { pvUndo(); d.close(); return; }
        if (e.target.closest('[data-pvyes]')) {
          const b = e.target.closest('[data-pvyes]'); b.disabled = true;
          try {
            // a9136 DESIGN-preview §2:sha256 原樣帶回,檔案在彈說明之後被換掉 ⇒ 409 preview_changed
            const r = await act('preview_start', { version: String(S.pv && S.pv.version || ''), sha256: String(S.pv && S.pv.sha256 || ''), consent: true, idempotency_key: idem() });
            S.pvOk = true; d.close();
            const u = r && typeof r.url === 'string' && /^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):\d+\//.test(r.url) ? r.url : '';
            if (u) window.open(u, '_blank', 'noopener'); say(u ? '非正式區開好了(新分頁)' : '非正式區開好了');
          } catch (er) {
            const code = er && er.code;
            if (code === 'preview_changed') { // 預覽檔換了:重讀、用新版重彈說明
              S.pv = await soft(get('/api/v1/preview')).catch(() => S.pv); pvDialog();
              const m = d.querySelector('.pverr'); if (m) m.textContent = '預覽版剛換了一版,說明照新版重看一次,再按同意';
              return;
            }
            b.disabled = false; const m = d.querySelector('.pverr');
            const W = {
              preview_needs_built_in_page: '只有內建頁、而且是這次親手配對的才能開非正式區',
              preview_unavailable: '這台現在沒有可用的預覽版',
              preview_running_other: '非正式區正在跑另一版:先取消勾選把它關掉,再開這一版',
              preview_not_started: '非正式區沒開起來,稍後再試',
              consent_required: '要在這個視窗按同意才能開',
              sha256_bad: '預覽版的指紋不對,重新整理這一頁再試',
            };
            if (m) m.textContent = W[code] || errText(er);
            return;
          }
          tick(true);
        }
      });
      // Esc 也算取消
      d.addEventListener('close', () => { if (!S.pvOk) pvUndo(); S.pvOk = false; });
    }
    const v = S.pv && S.pv.version ? String(S.pv.version) : '';
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">試用預覽版</span><span class="kid">${esc(v)}</span>${S.pv && /^[0-9a-f]{12,}$/i.test(String(S.pv.sha256 || '')) ? `<code class="pvh" title="預覽執行檔的指紋(sha256 前 12 碼);你同意的就是這一份">${esc(String(S.pv.sha256).slice(0, 12))}</code>` : ''}<button type="button" class="x" data-pvno="1" aria-label="關閉">✕</button></div></div>
      <div class="db"><ul class="pvl">${PV_TEXT.map((t) => `<li>${esc(t)}</li>`).join('')}</ul><div class="pverr hint err"></div></div>
      <div class="df"><span></span><span><button type="button" class="k" data-pvno="1" autofocus>取消</button> <button type="button" class="k g" data-pvyes="1">同意,開非正式區</button></span></div></div>`;
    if (!d.open) d.showModal();
  }

  // ---------- 左下:主機 ----------
  function hostHtml() {
    const th = S.thermal, g = th && th.guard;
    const gl = g && g.last && g.last.level ? g.last.level : g && g.level ? g.level : '';
    const lvl = gl ? String(gl) : '';
    const dot = !lvl ? '' : /hot|critical|nogo|red/i.test(lvl) ? 'hr' : /warm|elevated|yellow/i.test(lvl) ? 'hq' : 'hg';
    const last = S.metrics && S.metrics.thermal && S.metrics.thermal.last;
    const n = S.sessions.filter((x) => x.state === 'running').length;
    let h = `<div class="bh"><b>主機</b></div><div class="hrow main"><span class="hd ${dot}"></span><span class="hn">本機</span><span class="hv">${esc(lvl ? '溫度等級 ' + lvl : '')}</span></div>`;
    if (last && typeof last.avg === 'number') h += `<div class="hrow"><span class="hd"></span><span class="hn">溫度</span><span class="hv">平均 ${Math.round(last.avg)}°C${typeof last.max === 'number' ? ' · 最高 ' + Math.round(last.max) + '°C' : ''}</span></div>`;
    const H = S.host, gb = (b) => (b / 1073741824).toFixed(b < 10737418240 ? 1 : 0);
    if (H && H.memory && H.memory.total_bytes) h += `<div class="hrow"><span class="hd"></span><span class="hn">記憶體</span><span class="hv">可用 ${gb(H.memory.available_bytes)} / ${gb(H.memory.total_bytes)} GB</span></div>`;
    if (H && H.disk && H.disk.total_bytes) h += `<div class="hrow"><span class="hd"></span><span class="hn">磁碟</span><span class="hv">可用 ${gb(H.disk.available_bytes)} / ${gb(H.disk.total_bytes)} GB</span></div>`;
    const q = H && H.sessions && H.sessions.queued;
    h += `<div class="hrow"><span class="hd ${n ? 'hg' : ''}"></span><span class="hn">AI 席位</span><span class="hv">${n} 在跑${q ? ' · ' + q + ' 排隊' : ''}</span></div>`;
    // SPEC §9:其他主機每台一列(7b81e GET /api/v1/hosts;開始時間 7e638478):忙 / 空 / 離線、誰在用、跑多久、排隊數;不出位址
    for (const x of S.hosts || []) {
      const st = String(x.state || ''), seat = typeof x.seat === 'string' ? x.seat : '';
      const dot = st === 'free' ? 'hg' : st === 'busy' || st === 'queued' ? 'hq' : 'hr';
      const ago = typeof x.start_epoch === 'number' ? Math.max(0, Math.round(Date.now() / 60000 - x.start_epoch / 60)) : null;
      const who = seat ? '@' + seatName(seatById(seat) || { seat_id: seat, name: seat }) : '';
      let v = st === 'free' ? '空' : st === 'offline' ? '離線' : st === 'unreadable' ? '讀不到' : (who || '有人在用') + (ago != null ? ' · ' + (ago < 60 ? ago + ' 分' : Math.floor(ago / 60) + ' 時 ' + (ago % 60) + ' 分') : '');
      if (x.queue_len) v += ' · ' + x.queue_len + ' 排隊';
      const flags = [x.hot ? '熱' : '', x.low_disk ? '磁碟快滿' : '', x.unleased_busy ? '沒租約在忙' : ''].filter(Boolean);
      if (flags.length) v += ' · ' + flags.join('、');
      const tip = [typeof x.what === 'string' ? x.what : '', typeof x.run_id === 'string' ? x.run_id : '', typeof x.why === 'string' ? x.why : ''].filter(Boolean).join('\n');
      h += `<div class="hrow"${tip ? ` title="${esc(tip)}"` : ''}><span class="hd ${dot}"></span><span class="hn">${esc(String(x.name || '?'))}</span><span class="hv">${esc(v)}</span></div>`;
    }
    return h;
  }

  // ---------- 右:事件 ----------
  // 事件卡照流程表「待人決策」(SPEC §14,Andy 11:4x):標籤 + 一句話 + 卡號 + 日期;點了展開
  // 「我們的建議 / 狀態 / 出處」+ 按鈕。① 決定 / 出手、② 注意(§21c,Andy 22:2x 改名;代號 broken 不變)、③ 用量、④ 卡住、⑤ 可推進、⑥ 知道
  const LV_TAG = { 1: '決定', 2: '注意', 3: '用量', 4: '卡住', 5: '可推進', 6: '知道' };
  const TAG_K = { 決定: 'k1', 出手: 'k2', 注意: 'k2', 知道: 'k3', 可推進: 'k4', 用量: 'k5', 卡住: 'k6' };
  const KIND_TEXT = {
    'session.spawn_failed': '席開不起來', 'session.spawn_rejected': '開席被拒', 'session.stop_unconfirmed': '停了但沒確認行程結束', 'session.timeout': '逾時',
    'thermal.would_nogo': '太熱,先不開新的', 'thermal.would_reap': '太熱,要收掉一席',
    'seat.waiting': '等回應 30 分沒新行', 'seat.quiet': '說在忙,30 分沒輸出', 'session.queued': '在排隊',
    'seat.opened': '席開好', 'session.killed': '停了', 'session.cancelled': '取消', 'session.exited': '停了', 'message.done': '信結案',
  };
  // aibd 的卡(GET /api/v1/slots):新形狀 level / tag / text / card / since / rec / state / src / actions;
  // 舊形狀(slot / kind / item / why / at)照讀。⑤ 與「我寄的信沒送到 / 沒讀」aibd 沒有來源,頁面照算
  function aibdCards(list) {
    const L = [];
    for (const c of list || S.aslots) {
      if (S.dismissed.has(c.card_id)) continue;
      const rf = c.kind === 'seat.rejoin_failed' && c.seat_id;
      if (rf && rejoinedSince(c.seat_id, c.since || c.at)) continue; // 已重開:這張 aibd 不會自己收
      const s = c.seat_id ? seatById(c.seat_id) : null;
      const lv = Number(c.level || c.slot) || 6;
      const str = (v) => (typeof v === 'string' && v ? v : '');
      // 等回應 / 說忙沒動:標題寫哪一席怎麼了,它寫的工作項目放狀態
      const stuck = !c.text && (c.kind === 'seat.waiting' || c.kind === 'seat.quiet') && s;
      const text = str(c.text) || (stuck ? seatName(s) + ' ' + KIND_TEXT[c.kind] : '') || (c.kind === 'seat.usage' && c.pct != null ? (s ? seatName(s) + ' ' : '') + '用量 ' + c.pct + '%' : '') || str(c.item) || KIND_TEXT[c.kind] || str(c.why) || (s ? seatName(s) : String(c.kind || ''));
      L.push({
        lv, tag: str(c.tag) || LV_TAG[lv], text, ref: str(c.card), t: c.since || c.at,
        // 舊形狀的 why(席那一行的「為什麼」)放狀態;對不上的留空(SPEC §14)
        rec: str(c.rec), st: str(c.state) || (c.text ? '' : stuck ? str(c.item) : str(c.why)), src: str(c.src),
        who: s ? '@' + seatName(s) : '', seat: s ? s.seat_id : null, card: c.card_id, options: c.options,
        answer: (c.actions || []).includes('answer'), actions: c.actions || [], restart: rf ? c.seat_id : null,
      });
    }
    return L;
  }
  // 席接回失敗(0eed7 L7,a9136 f2f57e36 事件 seat.rejoin_failed {seat_id, session_id, exit_code, signal}):
  // 頁面從事件帳找最近的,這席之後又有一場開起來就不再顯示。事件帳舊的在前:第一次先找到尾巴,只回頭讀最近 1,500 筆
  const EVR = { cur: -1, busy: false, off: false };
  async function evAfter(c, n) { return get('/api/v1/events?cursor=' + c + '&limit=' + n); }
  async function evTail() {
    let lo = 0, step = 1024;
    while ((await evAfter(lo + step, 1)).events.length && step < 1 << 26) { lo += step; step *= 2; }
    let hi = lo + step; // lo 之後有、hi 之後沒有
    while (hi - lo > 1) { const mid = Math.floor((lo + hi) / 2); if ((await evAfter(mid, 1)).events.length) lo = mid; else hi = mid; }
    return lo;
  }
  async function loadRejoin() {
    if (EVR.off || EVR.busy) return;
    EVR.busy = true;
    try {
      if (EVR.cur < 0) EVR.cur = Math.max(0, (await evTail()) - 1500);
      for (let k = 0; k < 4; k++) {
        const r = await evAfter(EVR.cur, 500), ev = (r && r.events) || [];
        for (const e of ev) if (e.type === 'seat.rejoin_failed' && e.payload && e.payload.seat_id) S.rejoin[e.payload.seat_id] = { at: e.occurred_at, code: e.payload.exit_code, sig: e.payload.signal, session: e.payload.session_id };
        if (r && r.next_cursor != null) EVR.cur = Number(r.next_cursor);
        if (ev.length < 500) break;
      }
    } catch (e) { if (e && (e.status === 404 || e.status === 403)) EVR.off = true; }
    finally { EVR.busy = false; }
  }
  const rejoinedSince = (id, at) => { const t = new Date(at).getTime(); return S.sessions.some((x) => x.seat_id === id && x.state === 'running' && new Date(x.started_at).getTime() > t); };
  function rejoinCards() {
    const out = [];
    for (const id in S.rejoin) {
      const f = S.rejoin[id];
      if (rejoinedSince(id, f.at)) continue;
      const s = seatById(id) || { seat_id: id, name: id };
      out.push({ lv: 2, tag: '注意', t: f.at, who: '@' + seatName(s), seat: id, text: '這席接回失敗,要重開', st: (f.sig ? '被訊號 ' + f.sig + ' 結束' : '結束碼 ' + (f.code != null ? f.code : '?')) + '(接回後 10 秒內)', restart: id, key: 'rejoin:' + id + ':' + f.at });
    }
    return out;
  }
  const OLD_BROKEN = '\u58de\u4e86';
  function slots() {
    const fix = (x) => { if (x && x.tag === OLD_BROKEN) x.tag = '注意'; return x; };
    // 接回失敗:aibd 槽位有(a9136 04e74d1c)就用那張、掛重開鈕;沒有槽位才用本頁讀事件帳拼的(5d33a PREREAD-slots-04e74d1c L1)
    const L = S.aslots ? aibdCards() : rejoinCards();
    if (!S.aslots) for (const r of S.now) {
      const s = seatById(r.seat_id), n = r.now;
      if (!s || !n || n.state !== 'decide' || S.answered[s.seat_id] === n.updated_at) continue;
      L.push({ lv: 1, tag: '決定', t: n.updated_at, who: '@' + seatName(s), text: typeof n.item === 'string' ? n.item : '(內建頁可讀)', st: typeof n.why === 'string' ? n.why : '', seat: s.seat_id, answer: true });
    }
    const recent = (x) => { const t = new Date(x.ended_at || x.started_at).getTime(); return Number.isFinite(t) && Date.now() - t < 30 * 60000; };
    const seen = new Set();
    if (!S.aslots) for (const x of S.sessions) {
      if (seen.has(x.seat_id)) continue; // 每席只看最新一場
      seen.add(x.seat_id);
      const s = seatById(x.seat_id); if (!s) continue;
      const b = { who: '@' + seatName(s), seat: s.seat_id };
      if (x.state === 'failed') L.push(Object.assign({ lv: 2, tag: '注意', t: x.ended_at || x.started_at, text: seatName(s) + ' 開不起來', st: x.stop_reason || x.ended_by || '' }, b));
      else if (x.state === 'stop_unconfirmed') L.push(Object.assign({ lv: 2, tag: '注意', t: x.ended_at || x.started_at, text: seatName(s) + ' 停了但沒確認行程結束' }, b));
      else if (recent(x) && x.state === 'running') L.push(Object.assign({ lv: 6, tag: '知道', t: x.started_at, text: seatName(s) + ' 開好在跑' }, b));
      else if (recent(x) && (x.state === 'exited' || x.state === 'stopped')) L.push(Object.assign({ lv: 6, tag: '知道', t: x.ended_at, text: seatName(s) + ' 停了' }, b));
    }
    // ④ 卡住(門檻先用預設,記在卡 N-workspace):我寄的信排著 / 送出中超過 10 分、送到了 30 分還沒讀;
    // 席的這一場排隊(pending)超過 10 分
    const old = (t, min) => { const v = new Date(t).getTime(); return Number.isFinite(v) && Date.now() - v > min * 60000; };
    // 7ebca 20:47:aibd 槽位已出 4 號(mailq: 信排隊、席等太久)和 5 號(next: merge / push)⇒ 有 aibd 槽位時頁面不另算,免得件數重複、卡出兩張
    if (!S.aslots) for (const id in S.threads) {
      const s = seatById(id); if (!s) continue;
      const mine = (S.threads[id] || []).filter((m) => m.from_seat === S.me);
      const late = mine.filter((m) => (m.status === 'queued' || m.status === 'claimed') && old(m.created, 10));
      const unread = mine.filter((m) => m.status === 'delivered' && old(m.created, 30));
      if (late.length) L.push({ lv: 4, tag: '卡住', t: late[0].created, who: '@' + seatName(s), text: '寄給 @' + seatName(s) + ' 的信 ' + late.length + ' 則還沒送到', st: '排著超過 10 分', seat: s.seat_id });
      else if (unread.length) L.push({ lv: 4, tag: '卡住', t: unread[0].created, who: '@' + seatName(s), text: '寄給 @' + seatName(s) + ' 的信 ' + unread.length + ' 則送到沒讀', st: '送到超過 30 分', seat: s.seat_id });
    }
    if (!S.aslots) for (const x of S.sessions) {
      const s = seatById(x.seat_id);
      if (s && x.state === 'pending' && old(x.started_at || x.created_at, 10)) L.push({ lv: 4, tag: '卡住', t: x.started_at || x.created_at, who: '@' + seatName(s), text: seatName(s) + ' 排隊超過 10 分還沒開', seat: s.seat_id });
    }
    // ⑤ 可推進:樹上「審過排隊」(審過 + 收據綠、等合)的項目;卡號 = 項目 id
    if (S.tree && !S.aslots) {
      for (const n of [...S.tree.values()].filter((n) => n.status === '審過排隊' && n.kind !== 'suggestion')) {
        L.push({ lv: 5, tag: '可推進', t: n.updated || n.updated_at || '', text: n.title || n.id, ref: n.id, st: '審過 + 收據綠,等合', who: n.owner ? '@' + n.owner : '', node: n.id });
      }
    }
    L.sort((a, b) => a.lv - b.lv || String(b.t || '').localeCompare(String(a.t || '')));
    for (const x of L) x.key = x.card || [x.lv, x.ref || '', x.seat || '', x.text].join('|');
    return L.map(fix);
  }
  // 日期:一天內寫「N 分前」,再早寫月-日(流程表同款)
  function when(t) {
    const v = new Date(String(t || '').replace(' ', 'T')).getTime();
    if (!Number.isFinite(v)) return '';
    if (Date.now() - v < 86400000) return ago(t);
    const d = new Date(v);
    const p = tparts(v); return p ? p.mo + '-' + p.d : '';
  }
  function evBody(x, inDlg) {
    const lines = [];
    if (inDlg) { /* 視窗上面的欄位已寫了建議 / 狀態 / 出處,這裡只留作答 */ } else {
    if (x.rec) lines.push(`<div><b>我們的建議:</b>${esc(x.rec)}</div>`);
    if (x.st) lines.push(`<div><b>狀態:</b>${esc(stateText(x.st))}</div>`);
    const src = [x.src, x.who].filter(Boolean).join(' · ');
    if (src) lines.push(`<div><b>出處:</b>${esc(src)}</div>`);
    }
    const opts = x.answer ? optionsOf(x.text, x.options).filter((o) => o !== '照建議') : [];
    const b = [];
    const A = x.actions || [];
    if ((A.includes('follow_rec') || x.answer) && x.rec) b.push(`<button type="button" class="k g" data-pick="照建議" data-pcard="${esc(x.card || 'local:' + (x.seat || ''))}" data-pseat="${esc(x.seat || '')}">照建議</button>`);
    for (const o of opts) b.push(`<button type="button" class="k" data-pick="${esc(o)}" data-pcard="${esc(x.card || 'local:' + (x.seat || ''))}" data-pseat="${esc(x.seat || '')}">${esc(o)}</button>`);
    if (x.seat && (A.includes('handoff') || (!A.length && x.lv >= 2 && x.lv <= 4))) b.push(`<button type="button" class="k" data-handoff="${esc(x.seat)}">叫他交接</button>`);
    if (x.restart) b.push(`<button type="button" class="k g" data-restart="${esc(x.restart)}" data-rcard="${esc(x.card || '')}">重開</button>`);
    if (x.seat) b.push(`<button type="button" class="k" data-go="${esc(x.seat)}">看這一席</button>`);
    if (x.card) b.push(`<button type="button" class="k" data-later="${esc(x.card)}" title="先從清單收起;沒處理的話重啟後會再出現">先收起</button>`);
    const k = x.card || 'local:' + (x.seat || '');
    const free = x.answer && (x.card || x.seat) ? `<div class="ans"><input class="ansi" data-ansfor="${esc(k)}" maxlength="2000" placeholder="${opts.length || x.rec ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[k] || '')}"><button type="button" class="k g" data-answer="${esc(k)}" data-aseat="${esc(x.seat || '')}">回答</button></div>` : '';
    return `<div class="eb">${lines.join('')}${free}${b.length ? `<div class="ea">${b.join('')}</div>` : ''}</div>`;
  }
  // ---------- 右欄:事件總框(SPEC §21,Andy 21:0x)----------
  // 最上方分類分頁(全部 + 六類帶件數);「全部」裡每類一個框、預設露兩則、「還有 N 則」展開;展開後一頁 10 則、超過分頁。
  // aibd 回 counts(7ebca a9edfa725)⇒ 件數照 aibd、展開的頁向 aibd 要(slot / page / per=10 / 固定 as_of,新進的不推頁);
  // 沒有 ⇒ 照頁面手上的全部卡自己切。頁面自己算的卡(卡住、可推進)排在那一類第一頁前面。展開 / 頁碼每位觀看者自己記
  const EV_CLS = [1, 2, 3, 4, 5, 6];
  const EV_NAME = { 1: '待人決策', 2: '注意', 3: '額度 / 要登入', 4: '卡住', 5: '可推進', 6: '動態' };
  const EV_PER = 10, EV_PEEK = 2;
  const EVS = (() => { try { return JSON.parse(localStorage.getItem('aib.ws.ev') || '{}') || {}; } catch (e) { return {}; } })();
  if (!EVS.open) EVS.open = {}; if (!EVS.page) EVS.page = {}; if (!EVS.tab) EVS.tab = 'all';
  const evSave = () => { try { localStorage.setItem('aib.ws.ev', JSON.stringify(EVS)); } catch (e) { /* 存不了:這次有效就好 */ } };
  const EVP = {}; // 向 aibd 要的頁:{ [k]: { key, cards, pages, newer, at } }
  function evByCls() {
    const G = {}; for (const k of EV_CLS) G[k] = [];
    for (const x of slots()) (G[x.lv] || G[6]).push(x);
    return G;
  }
  const evLocal = (L) => L.filter((x) => !x.card);
  function evCount(k, G) {
    if (S.slotCounts) return (Number(S.slotCounts[String(k)]) || 0) + evLocal(G[k]).length;
    return G[k].length;
  }
  async function evFetch(k, page) {
    const key = k + ':' + page + ':' + (S.slotAsOf || '');
    if (EVP[k] && EVP[k].key === key) return;
    EVP[k] = { key, loading: true, cards: (EVP[k] && EVP[k].cards) || null };
    try {
      const q = new URLSearchParams({ slot: String(k), page: String(page), per: String(EV_PER) }); if (S.slotAsOf) q.set('as_of', S.slotAsOf);
      const r = await get('/api/v1/slots?' + q.toString());
      const cards = aibdCards(Array.isArray(r.slots) ? r.slots : []);
      cards.forEach((x) => { x.key = x.card || x.key; });
      EVP[k] = { key, cards, pages: Number(r.pages) || 1, newer: Number(r.newer) || 0 };
    } catch (e) { EVP[k] = { key, err: true, cards: null }; }
    render();
  }
  // 某一類展開後的這一頁:aibd 分頁(第一頁前面加頁面自己算的卡)或自己切
  function evPage(k, G) {
    const page = Math.max(1, Number(EVS.page[k]) || 1);
    if (S.slotCounts) {
      const P = EVP[k];
      if (!P || P.key !== k + ':' + page + ':' + (S.slotAsOf || '')) evFetch(k, page);
      const loc = page === 1 ? evLocal(G[k]) : [];
      const pagesSrv = P && P.pages ? P.pages : Math.max(1, Math.ceil((Number(S.slotCounts[String(k)]) || 0) / EV_PER));
      return { page, pages: pagesSrv, items: loc.concat(P && P.cards ? P.cards : []), loading: !P || P.loading, err: P && P.err, newer: P && P.newer };
    }
    const pages = Math.max(1, Math.ceil(G[k].length / EV_PER));
    const pg = Math.min(page, pages);
    return { page: pg, pages, items: G[k].slice((pg - 1) * EV_PER, pg * EV_PER) };
  }
  // §21b(Andy 22:1x「只要一行就好,hover 可以看全標題,點擊才打開」):每則固定一行(標題截斷 …、卡號、時間同一行);
  // 滑過看完整標題(觸控長按);點了開卡片視窗,小卡不再展開,作答鈕在視窗裡。類別框裡跟框名相同的類別標不重複寫(519d0 後審)
  function evCard(x, box) {
    const tag = x.tag || LV_TAG[x.lv] || '';
    const showTag = tag && !(box && (tag === EV_NAME[box] || tag === LV_TAG[box])); // 「決定」在待人決策框、「用量」在額度框…都算同名
    const full = x.text + (x.ref ? ' · ' + x.ref : '');
    return `<div class="ec l${x.lv}"><div class="eh one${showTag ? '' : ' nt'}" data-evopen="${esc(x.key)}" data-full="${esc(full)}" tabindex="0" role="button" title="${esc(full)}">${showTag ? `<span class="ek ${TAG_K[x.tag] || 'k3'}">${esc(tag)}</span>` : ''}<span class="et">${esc(x.text)}</span>${x.ref ? `<span class="ecd">${esc(x.ref)}</span>` : ''}<span class="ew">${esc(when(x.t))}</span></div></div>`;
  }
  function evPager(k, P) {
    if (P.pages <= 1) return '';
    return `<div class="epg"><button type="button" class="k" data-evpg="${k}" data-to="${P.page - 1}" ${P.page <= 1 ? 'disabled' : ''}>上一頁</button><span>第 ${P.page} / ${P.pages} 頁</span><button type="button" class="k" data-evpg="${k}" data-to="${P.page + 1}" ${P.page >= P.pages ? 'disabled' : ''}>下一頁</button></div>`;
  }
  function evBox(k, G, solo) {
    const n = evCount(k, G);
    if (!solo && !n) return '';
    const open = solo || !!EVS.open[k];
    let body, foot = '';
    if (!open) {
      body = G[k].slice(0, EV_PEEK).map((x) => evCard(x, k)).join('');
      if (n > EV_PEEK) foot = `<button type="button" class="more" data-evcls="${k}">還有 ${n - EV_PEEK} 則 ▾</button>`;
    } else {
      const P = evPage(k, G);
      body = P.items.length ? P.items.map((x) => evCard(x, k)).join('') : `<div class="empty">${P.loading ? '讀取中…' : P.err ? '這一頁讀不到' : '沒有事件'}</div>`;
      foot = (P.newer ? `<button type="button" class="more" data-evnew="1">有 ${P.newer} 則新事件,點了看最新</button>` : '') + evPager(k, P) + (!solo && n > EV_PEEK ? `<button type="button" class="more" data-evcls="${k}">收回兩則 ▴</button>` : '');
    }
    return `<div class="ecls" data-cls="${k}">${solo ? '' : `<div class="ecl"><span class="ek ${TAG_K[LV_TAG[k]] || 'k3'}">${esc(EV_NAME[k])}</span><b>${n}</b></div>`}${body}${foot}</div>`;
  }
  // 已收起(slot_dismiss)的:框底一行「已收起 N 則」,點開列出、可還原(act slot_restore,7ebca 形狀待對;
  // 清單 aibd 有 GET slots?dismissed=1 就用,沒有就只列這一頁收起的)
  function dmHtml() {
    const M = S.dmc || new Map(); const n = Math.max(M.size, S.dmN || 0);
    if (!n) return '';
    const rows = S.dmOpen ? [...M.values()].map((x) => `<div class="dmr"><span class="et" title="${esc(x.text)}">${esc(x.text)}</span><button type="button" class="k" data-undm="${esc(x.card)}">還原</button></div>`).join('') || '<div class="empty">讀取中…</div>' : '';
    return `<div class="dmb"><button type="button" class="more" data-dmopen="1" aria-expanded="${S.dmOpen ? 'true' : 'false'}">已收起 ${n} 則 ${S.dmOpen ? '▴' : '▾'}</button>${rows}</div>`;
  }
  async function loadDismissed() {
    const r = await soft(get('/api/v1/slots?dismissed=1')).catch(() => null);
    // 只認明確回 dismissed:true 的(不認得這個參數的 aibd 會回全部卡,不能當成已收起)
    const L = r && r.dismissed === true && Array.isArray(r.slots) ? r.slots : null; if (!L) return;
    const M = S.dmc || (S.dmc = new Map());
    for (const c of L) if (c && c.card_id && !M.has(c.card_id)) { const s = c.seat_id ? seatById(c.seat_id) : null; M.set(c.card_id, { card: c.card_id, text: (typeof c.text === 'string' && c.text) || (typeof c.item === 'string' && c.item) || (s ? seatName(s) + ' ' : '') + (KIND_TEXT[c.kind] || c.kind || '事件') }); }
    S.dmN = L.length; render();
  }
  function slotsHtml() {
    const G = evByCls();
    const tot = EV_CLS.reduce((a, k) => a + evCount(k, G), 0);
    const tab = EVS.tab === 'all' || EV_CLS.includes(Number(EVS.tab)) ? EVS.tab : 'all';
    const tabs = `<div class="etabs"><button type="button" class="et1 ${tab === 'all' ? 'on' : ''}" data-evtab="all">全部 <b>${tot}</b></button>${EV_CLS.map((k) => { const n = evCount(k, G); return `<button type="button" class="et1 ${String(tab) === String(k) ? 'on' : ''} ${n ? '' : 'z'}" data-evtab="${k}">${esc(EV_NAME[k])} <b>${n}</b></button>`; }).join('')}</div>`;
    const body = tab === 'all' ? (tot ? EV_CLS.map((k) => evBox(k, G, false)).join('') : '<div class="empty">沒有事件</div>') : evBox(Number(tab), G, true);
    return `<div class="evbox"><div class="bh"><b>事件</b> <span class="st">${tot} 則</span></div>${tabs}<div class="slots">${body}</div>${dmHtml()}</div>`;
  }

  // ---------- 中:標題與七站 ----------
  function headHtml() {
    const n = S.tree && S.sel ? S.tree.get(S.sel) : null;
    if (!n) return '';
    const si = stationIdx(n.status);
    const way = STATIONS.map((x, i) => `<div class="w ${si != null && i < si ? 'dn' : si === i ? 'nw' : ''}"><i></i><span>${esc(x)}</span></div>`).join('');
    return `<div class="lh2"><div class="ttl" data-card="${esc(n.id)}" title="打開這張卡">💬 ${esc(n.title || n.id)}</div>${si != null ? `<div class="way">${way}</div>` : ''}</div>`;
  }

  // ---------- 中:某一席自己的運作窗 ----------
  function toolLine(c) {
    const i = c.input || {};
    const arg = i.file_path ? String(i.file_path).split(/[\\/]/).pop() : i.command || i.pattern || i.description || i.url || i.query || '';
    return String(c.name || 'tool') + (arg ? ' · ' + String(arg).split('\n')[0].slice(0, 140) : '');
  }
  function outItems(seatId) {
    const se = lastSession(seatId);
    const o = se && S.out[se.id || se.session_id];
    const rows = [];
    if (!o) return rows;
    for (const it of o.items) {
      const p = it.payload || {};
      if (it.type === 'session.output_truncated') { rows.push({ t: it.occurred_at, h: '<div class="ev bad">輸出太多,後面沒有存</div>' }); continue; }
      if (it.type !== 'session.output' || typeof p.line !== 'string') continue;
      if (p.stream === 'stderr') { if (p.line.trim()) rows.push({ t: it.occurred_at, h: `<div class="wk err"><span>${esc(p.line.slice(0, 300))}</span><span>stderr</span></div>` }); continue; }
      let j = null;
      try { j = JSON.parse(p.line); } catch (e) { j = null; }
      if (!j || typeof j !== 'object') { if (p.line.trim()) rows.push({ t: it.occurred_at, h: `<div class="out"><div class="body">${esc(p.line)}</div></div>` }); continue; }
      if (j.type === 'system' && j.subtype === 'init') { rows.push({ t: it.occurred_at, h: `<div class="ev gn">▶ 開始${j.model ? ' · ' + esc(j.model) : ''} · ${esc(hhmm(it.occurred_at))}</div>` }); continue; }
      if (j.type === 'assistant' && j.message && Array.isArray(j.message.content)) {
        for (const c of j.message.content) {
          if (c.type === 'text' && c.text && c.text.trim()) rows.push({ t: it.occurred_at, h: `<div class="out"><div class="body">${esc(c.text)}</div><div class="tm">${esc(hhmm(it.occurred_at))}</div></div>` });
          else if (c.type === 'tool_use') rows.push({ t: it.occurred_at, h: `<div class="wk ${/^(Bash)$/.test(c.name) ? 'v' : ''}"><span>${esc(toolLine(c))}</span><span>▶</span></div>` });
        }
        continue;
      }
      if (j.type === 'result') rows.push({ t: it.occurred_at, h: `<div class="ev ${j.is_error ? 'bad' : 'ok'}">${j.is_error ? '✗ 這一回合出錯' : '✓ 這一回合做完'} · ${esc(hhmm(it.occurred_at))}</div>` });
    }
    return rows;
  }
  // 共同對話裡寄給這一席的(@ 它)也放進它的窗,標「共同對話」
  function sharedTo(seatId) {
    return (S.threads[seatId] || []).filter((m) => m.from_seat === S.me).map((m) => ({
      t: m.created,
      h: `<div class="wk in"><span><span class="lg sm you">你</span> ${typeof m.body === 'string' ? esc(m.body.split('\n')[0].slice(0, 200)) : esc('(' + (m.len || '?') + ' 字)')}</span><span class="in-tag">共同對話</span></div>`,
    }));
  }
  // 「等你決定」的題目與選項(第三波 3,取代舊版黃框):卡有 options 就用;沒有就從題目拆
  // (「A 還是 B」、1. 2. 編號、A / B、要不要 → 要 / 不要),拆不出來只給輸入框
  function optionsOf(item, given) {
    if (Array.isArray(given) && given.length) return given.filter((x) => typeof x === 'string' && x.trim()).slice(0, 6);
    const full = String(item || '').replace(/[??]\s*$/, '').trim();
    if (!full) return [];
    // 「題目:A / B」⇒ 只拆冒號後面
    const tail = full.split(/[::]/).pop().trim();
    const t = tail && tail !== full ? tail : full;
    const num = t.split(/\s*(?:^|\s)(?:[1-9][.、)]|[(（][1-9][)）])\s*/).map((x) => x.trim()).filter(Boolean);
    const lead = /^(?:[1-9][.、)]|[(（][1-9][)）])/.test(t) ? 0 : 1; // 開頭就是 1. ⇒ 沒有前言
    if (/(?:^|\s)(?:[1-9][.、)])/.test(t) && num.length - lead >= 2) return num.slice(lead, lead + 6).map((x) => x.replace(/[,,;;]$/, ''));
    if (t.includes('還是')) { const P = t.split('還是').map((x) => x.replace(/^(要|是|該|先)?/, (m) => (m === '先' ? m : '')).trim()).filter(Boolean); if (P.length >= 2 && P.length <= 4) return P.map((x, i) => (i === 0 ? x.replace(/^.*?(?:要|是)(?=\S)/, '') : x)); }
    const sl = t.split(/\s+\/\s+|\s*／\s*/).map((x) => x.trim()).filter(Boolean);
    if (sl.length >= 2 && sl.length <= 4 && sl.every((x) => x.length <= 30)) return sl;
    if (/^要不要/.test(t)) return ['要', '不要'];
    return [];
  }
  function decideBox(s) {
    const c = (S.aslots || []).find((x) => (x.level || x.slot) === 1 && x.seat_id === s.seat_id && !S.dismissed.has(x.card_id));
    const n = nowOf(s.seat_id);
    // 有卡槽(7ebca)只看卡;沒有就看狀態列,答過同一行就不再出(等那一席寫新的一行)
    if (S.aslots ? !c : !(n && n.state === 'decide') || S.answered[s.seat_id] === n.updated_at) return '';
    const q = c ? c.text || c.item : n.item;
    const why = c ? c.rec ? '我們的建議:' + c.rec : c.why : n && n.why;
    const card = c ? c.card_id : '';
    const opts = (c && c.rec ? ['照建議'] : []).concat(optionsOf(q, c && c.options));
    const key = card || 'local:' + s.seat_id;
    return `<div class="dcd"><div class="dh"><b>等你決定</b>${c && c.at ? `<span>${esc(ago(c.at))}</span>` : ''}</div>
      <div class="dq">${typeof q === 'string' && q ? esc(q) : '(這一席沒寫題目)'}</div>${typeof why === 'string' && why ? `<div class="dw">${esc(why)}</div>` : ''}
      ${opts.length ? `<div class="dopt">${opts.map((o) => `<button type="button" class="chip" data-pick="${esc(o)}" data-pcard="${esc(card)}" data-pseat="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''}>${esc(o)}</button>`).join('')}</div>` : ''}
      <div class="ans"><input class="ansi" data-ansfor="${esc(key)}" maxlength="2000" placeholder="${opts.length ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[key] || '')}"><button type="button" class="k g" data-answer="${esc(key)}" data-aseat="${esc(s.seat_id)}">回答</button>${card ? `<button type="button" class="k" data-later="${esc(card)}" title="先從清單收起;沒處理的話重啟後會再出現">先收起</button>` : ''}</div></div>`;
  }
  // 答一題:有卡走 decision_answer(7ebca),沒卡(這版 aibd 沒有卡槽)就當一句話送進那一席
  async function answer(seat, card, text) {
    if (!text || S.sending) return;
    S.sending = true;
    try {
      if (card && !card.startsWith('local:')) { const r = await act('decision_answer', Object.assign(seat ? { seat_id: seat } : {}, { answer: text, card_id: card })); S.dismissed.add(card); say(r.__status === 202 ? '已交給它,這一回合結束就收到' : '已回答'); }
      else { await act('seat_input', { seat_id: seat, text }); const n = nowOf(seat); if (n) S.answered[seat] = n.updated_at; say('已回答'); }
      delete S.ansDraft[card];
    } catch (er) { say(er && er.code === 'decision_changed' ? '那一席換了題目,看新的那張' : er && er.code === 'already_answered' ? '已經答過了' : errText(er), 'err'); }
    S.sending = false; return tick(true);
  }
  function seatWin(s) {
    const n = nowOf(s.seat_id);
    const se = lastSession(s.seat_id);
    const stTxt = !se ? '沒在跑' : se.state === 'running' ? (n && n.state === 'busy' ? '在做' : n && n.state === 'reply' ? '等回應' : n && n.state === 'decide' ? '等你決定' : '在跑') : se.state;
    const steps = n && n.total ? `<progress class="ctxp" max="${Number(n.total) | 0}" value="${Number(n.done) | 0}"></progress><small>${Number(n.done) | 0}/${Number(n.total) | 0}</small>` : '';
    // 用量(7ebca GET /api/v1/usage):這一席最新一場的 pct;≥ handoff_pct 黃、≥ red_pct 紅
    const u = S.usage && (S.usage.usage || []).find((x) => x.seat_id === s.seat_id && (!se || x.session_id === (se.id || se.session_id)));
    const uc = u && u.pct != null ? (u.pct >= (S.usage.red_pct || 95) ? 'red' : u.pct >= (S.usage.handoff_pct || 85) ? 'warn' : '') : '';
    const use = u && u.pct != null ? `<span class="use ${uc}" title="${esc((u.model || '') + (u.used != null ? ' · ' + u.used + ' / ' + u.window : ''))}">用量<progress class="usep" max="100" value="${Math.max(0, Math.min(100, Number(u.pct) || 0))}"></progress>${Number(u.pct) | 0}%</span>` : '';
    const rows = outItems(s.seat_id).concat(sharedTo(s.seat_id)).sort((a, b) => String(a.t || '').localeCompare(String(b.t || '')));
    if (n && n.state === 'busy') rows.push({ h: '<div class="ev">在想…</div>' });
    const stop = se && (se.state === 'running' || se.state === 'pending' || se.state === 'stop_unconfirmed') ? `<button type="button" class="stop" data-stop="${esc(se.id || se.session_id)}" ${S.sending ? 'disabled' : ''}>${S.stopArm === (se.id || se.session_id) ? '再按一次停止' : '■ 停止'}</button>` : '';
    return `<div class="sh">${logo(s, 'big', 'pick')}<span class="nm2">@${esc(seatName(s))}</span><small>${esc([modelText(s), s.mode, stTxt].filter(Boolean).join(' · '))}</small>${steps}${use}${stop}</div>
      ${decideBox(s)}<div class="tl" id="tl">${rows.length ? rows.map((r) => r.h).join('') : '<div class="empty">這一席還沒有輸出</div>'}</div>
      <div class="rowb">${QUICK.map((q, i) => `<button type="button" class="bt" data-quick="${i}" ${running(s.seat_id) ? '' : 'disabled'}>${esc(q[0])}</button>`).join('')}</div>
      <div class="send"><textarea id="inp" rows="2" maxlength="32768" placeholder="只對 @${esc(seatName(s))} 說…">${esc(S.drafts[s.seat_id] || '')}</textarea><button type="button" class="go" data-send="seat" ${S.sending ? 'disabled' : ''}>送出</button></div>
      ${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
  }
  // 五顆快捷鈕 = 送一句固定的話給這一席(舊版對話面板的五顆;今晚只送字,沒有另外的介面)
  const QUICK = [
    ['回報進度', '請用三行回報你現在的進度:做到哪、下一步、卡在哪。'],
    ['叫他交接', '請準備交接:寫下做到哪、還沒做完的、下一步,寫好告訴我檔案在哪。'],
    ['先停手', '先停手,把目前的狀態講清楚,等我下一句。'],
    ['讀信', '請去讀你的信並處理。'],
    ['總結', '請總結這一段做了什麼、結果如何。'],
  ];

  // ---------- 中:共同對話 ----------
  function sharedRows() {
    const all = [];
    for (const id in S.threads) for (const m of S.threads[id]) all.push(Object.assign({ _seat: id }, m));
    all.sort((a, b) => String(a.created || '').localeCompare(String(b.created || '')));
    // 同一句 @ 了幾席 = 每席一筆;畫面併成一個泡泡(同內文、10 秒內),每席各自的送達狀態排在下面
    const G = [];
    for (const m of all) {
      const mine = m.from_seat === S.me;
      const g = mine && G.find((x) => x.mine && x.key === (m.body || '#' + m.len) && !x.to.some((y) => y._seat === m._seat) && Math.abs(new Date(m.created) - new Date(x.m.created)) < 10000);
      if (g) g.to.push(m); else G.push({ mine, key: m.body || '#' + m.len, m, to: [m] });
    }
    const ST = { failed: '沒送到', queued: '排著', dispatched: '送出中', received: '已送到', processed: '已處理' };
    const RAW = { queued: '排著', claimed: '送出中', delivered: '已送到', read: '已讀', done: '已處理', archived: '已處理' };
    const replied = (x) => (S.threads[x._seat] || []).some((r) => r.from_seat !== S.me && String(r.created || '') > String(x.created || ''));
    const stOf = (x) => (x.state === 'failed' ? '沒送到' : replied(x) ? '已回' : RAW[x.status] || ST[x.state] || x.state || '');
    return G.map(({ mine, m, to }) => {
      const sOf = (x) => seatById(x._seat) || { seat_id: x._seat, provider: 'claude' };
      const txt = typeof m.body === 'string' ? m.body : S.sentText[m.message_id];
      const body = typeof txt === 'string' ? esc(txt) : `<span class="st">(${esc(m.len != null ? m.len : '?')} 字)</span>`;
      const who = mine ? to.map((x) => `<span class="at">@</span>${logo(sOf(x), 'sm')}`).join('') : logo(sOf(m));
      const seen = mine ? `<div class="seen">${to.map((x) => `${logo(sOf(x), 'sm')}<span class="st ${x.state === 'failed' ? 'bad' : stOf(x) === '已讀' || stOf(x) === '已回' ? 'ok' : ''}">${esc(stOf(x))}</span>`).join(' ')}</div>` : '';
      return { t: String(m.created || ''), h: `<div class="msg ${mine ? 'me' : ''} ${!mine && m.unread ? 'unread' : ''}"><div class="who">${who}</div><div class="body">${body}</div><div class="tm">${esc(hhmm(m.created))}</div>${seen}</div>` };
    }).concat(itemRows(), pairRows()).sort((a, b) => a.t.localeCompare(b.t)).map((x) => x.h).join('');
  }
  // 席與席之間的一對一信:寄件席 → 收件席,跟真人的話分開一個樣子
  function pairRows() {
    const sOf = (k) => { const id = String(k || '').replace(/^seat:/, ''); return seatById(id) || { seat_id: id, provider: 'claude' }; };
    const out = [];
    for (const k in S.pairs || {}) for (const m of S.pairs[k]) {
      const body = typeof m.body === 'string' ? esc(m.body) : `<span class="st">(${esc(m.len != null ? m.len : m.body && m.body.len != null ? m.body.len : '?')} 字,內建頁可讀)</span>`;
      out.push({ t: String(m.created || ''), h: `<div class="msg pr"><div class="who">${logo(sOf(m.from_seat), 'sm')}<span class="at">→</span>${logo(sOf(m.to_seat), 'sm')}</div><div class="body">${body}</div><div class="tm">${esc(hhmm(m.created))}</div></div>` });
    }
    return out;
  }
  // 項目對話的貼文(席與席之間、真人在項目裡說的):頭像 + 叫了誰 + 送達,前面一個小標「在哪一項」
  function itemRows() {
    const RAW = { queued: '排著', claimed: '送出中', delivered: '已送到', read: '已讀', done: '已處理', archived: '已處理' };
    const sOf = (k) => { const id = String(k || '').replace(/^seat:/, ''); return seatById(id) || { seat_id: id, provider: 'claude' }; };
    return (S.itemTalk || []).map((p) => {
      const mine = p.sender_kind === 'human' || p.from_seat === S.me;
      const body = typeof p.body === 'string' ? esc(p.body) : `<span class="st">(${esc(p.len != null ? p.len : p.body && p.body.len != null ? p.body.len : '?')} 字,內建頁可讀)</span>`;
      const who = mine ? '<span class="lg sm you">你</span>' : logo(sOf(p.from_seat), 'sm');
      const calls = (p.calls || []).map((c) => {
        const bad = c.failed_code === 'safe_cannot_send_to_dgrs' ? '收不到' : c.failed_code || c.state === 'failed' ? '沒送到' : '';
        const t = bad || RAW[c.status] || c.state || '';
        return `<span class="at">@</span>${logo(sOf(c.seat), 'sm')}<span class="st ${bad ? 'bad' : t === '已讀' ? 'ok' : ''}">${esc(t)}</span>`;
      }).join(' ');
      return { t: String(p.created || ''), h: `<div class="msg it ${mine ? 'me' : ''}"><div class="ith" data-node="${esc(p._node)}" title="打開這一項的對話">📌 ${esc(String(p._title).slice(0, 40))}</div><div class="who">${who}</div><div class="body">${body}</div>${docLink(p)}<div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}</div>` };
    });
  }
  // 0eed7 VERIFY-humanthread L1:內文提到的 @ 也會送 ⇒ 輸入框下面即時列「會送給誰」,送出前看得到
  function toLine(kind) {
    const t = S.drafts[kind] || '';
    const L = targets(t);
    const all = /(^|\s)@all(\s|$)/i.test(t);
    if (!L.length && !all) return `<div class="tol" id="tol"></div>`;
    const mem = kind === 'node' && S.nt ? new Set((S.nt.members || []).map((m) => String(m.seat || '').replace(/^seat:/, ''))) : null;
    return `<div class="tol" id="tol">會送給 ${all && kind === 'node' ? '這一項全部的席' : L.map((x) => logo(x, 'sm') + (mem && !mem.has(x.seat_id) ? '<em class="nw">先加進這一項</em>' : '')).join(' ')}</div>`;
  }
  function refreshTo(kind) { const o = document.getElementById('tol'); if (o) o.outerHTML = toLine(kind); }
  function sharedWin() {
    const P = present();
    const rows = sharedRows();
    const atpop = `<div class="atpop" id="atpop" ${S.atOpen ? '' : 'hidden'}>${P.map((s) => `<span data-at="${esc(s.seat_id)}">${logo(s, 'big')}${s.mode === 'dgrs' ? '<em class="dg">不擋工具</em>' : ''}</span>`).join('')}${P.length ? `<span class="all" data-at="all">@all${P.some((s) => s.mode === 'dgrs') ? '<em class="dg">含不擋工具的席</em>' : ''}</span>` : '<span class="st">沒有在跑的席</span>'}</div>`;
    return `${headHtml()}
      <div class="tl" id="tl">${rows || '<div class="empty">還沒有對話。打 @ 選一席,寫一句送出。</div>'}</div>
      ${atpop}
      <div class="send"><textarea id="inp" rows="2" maxlength="32768" placeholder="寫給大家;要誰接手就 @ 誰">${esc(S.drafts.shared || '')}</textarea><button type="button" class="go" data-send="shared" ${S.sending ? 'disabled' : ''}>送出</button></div>${toLine('shared')}
      ${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
  }

  // ---------- 中:項目的對話(第二波;a9136 b488387e)----------
  // 點樹一項 = act thread_open {node, title} ⇒ 那一項的對話(已有就沿用);時間線 = GET threads/{id} 的
  // posts;發言 = act thread_send {thread_id, body, to}。這版 aibd 沒有 thread_open ⇒ 不再試,維持共同對話
  async function openNode(id) {
    if (S.ntOff || !S.tree) return;
    const n = S.tree.get(id);
    if (!n) return;
    try {
      // 0eed7 M1:舊版 aibd 的 thread_open 對同一項每次新開 ⇒ 先查這一項已有的,用最早那段
      // (a9136 b1a39a86 起 thread_open 冪等、回最早那段;兩邊選同一段)
      let th = null;
      const L = await soft(get('/api/v1/threads?node=' + encodeURIComponent(id))).catch(() => null);
      const have = L && (Array.isArray(L.threads) ? L.threads : Array.isArray(L) ? L : []).filter((t) => t && t.thread_id && (!t.node || t.node === id));
      if (have && have.length) th = have.sort((a, b) => String(a.created || '').localeCompare(String(b.created || '')))[0];
      if (!th) { const r = await act('thread_open', { node: id, title: String(n.title || id).slice(0, 200) }); th = r.thread || r; }
      if (!th || !th.thread_id) return;
      if (!S.nt || S.nt.thread_id !== th.thread_id) S.posts = [];
      S.nt = th; S.mode = 'node'; S.hint = '';
      await loadNode();
    } catch (e) {
      if (e && e.status === 400 && (e.code === 'invalid_request' || e.code === 'unknown_act_type')) S.ntOff = true;
      else say(errText(e), 'err');
    }
    render();
  }
  async function loadNode() {
    if (!S.nt) return;
    const r = await get('/api/v1/threads/' + encodeURIComponent(S.nt.thread_id));
    S.nt = Object.assign({}, S.nt, r, { posts: undefined });
    S.posts = r.posts || [];
  }
  function postRows() {
    const RAW = { queued: '排著', claimed: '送出中', delivered: '已送到', read: '已讀', done: '已處理', archived: '已處理' };
    const sOf = (k) => { const id = String(k || '').replace(/^seat:/, ''); return seatById(id) || { seat_id: id, provider: 'claude' }; };
    return S.posts.map((p) => {
      const mine = p.sender_kind === 'human' || p.from_seat === S.me;
      const body = typeof p.body === 'string' ? esc(p.body) : `<span class="st">(${esc(p.len != null ? p.len : p.body && p.body.len != null ? p.body.len : '?')} 字,內建頁可讀)</span>`;
      const who = mine ? '<span class="lg sm you">你</span>' : logo(sOf(p.from_seat), 'sm');
      const calls = (p.calls || []).map((c) => {
        const bad = c.failed_code === 'safe_cannot_send_to_dgrs' ? '收不到' : c.failed_code || c.state === 'failed' ? '沒送到' : '';
        const t = bad || RAW[c.status] || c.state || '';
        return `${logo(sOf(c.seat), 'sm')}<span class="st ${bad ? 'bad' : t === '已讀' ? 'ok' : ''}">${esc(t)}</span>`;
      }).join(' ');
      const rb = (p.read_by || []).filter((k) => /^seat:/.test(k)).map((k) => logo(sOf(k), 'sm')).join('');
      return `<div class="msg ${mine ? 'me' : ''}"><div class="who">${who}</div><div class="body">${body}</div>${docLink(p)}<div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}${rb ? `<div class="seen rb"><span class="st">讀過</span>${rb}</div>` : ''}</div>`;
    }).join('');
  }
  function nodeWin() {
    const M = (S.nt.members || []).map((m) => { const id = String(m.seat || '').replace(/^seat:/, ''); const s = seatById(id); return s ? `<span data-at="${esc(id)}">${logo(s, 'sm')}<em class="rl">${esc(ROLE_ZH[m.role] || m.role || '')}</em></span>` : ''; }).join('');
    // 0eed7 VERIFY-humanthread M1:現有的席也能從這裡加進來 —— @ 選單列全部在場的席,不是成員的送出前先掛「旁聽」
    const mem = new Map((S.nt.members || []).map((m) => [String(m.seat || '').replace(/^seat:/, ''), m.role]));
    const P = present();
    const atpop = `<div class="atpop" id="atpop" ${S.atOpen ? '' : 'hidden'}>${P.map((s) => `<span data-at="${esc(s.seat_id)}">${logo(s, 'big')}<em class="rl">${esc(mem.has(s.seat_id) ? ROLE_ZH[mem.get(s.seat_id)] || mem.get(s.seat_id) || '成員' : '加進來')}</em></span>`).join('')}${mem.size ? '<span class="all" data-at="all">@all</span>' : ''}</div>`;
    return `${headHtml()}
      ${M ? `<div class="mem">${M}</div>` : ''}
      <div class="tl" id="tl">${postRows() || '<div class="empty">這一項還沒有對話。寫一句送出;要誰接手就 @ 誰。</div>'}</div>
      ${atpop}
      <div class="send"><textarea id="inp" rows="2" maxlength="32768" placeholder="對這一項說;@ 誰 = 叫他">${esc(S.drafts.node || '')}</textarea><button type="button" class="go" data-send="node" ${S.sending ? 'disabled' : ''}>送出</button></div>${toLine('node')}
      ${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
  }
  async function sendNode(text) {
    const all = /(^|\s)@all(\s|$)/i.test(text);
    const to = all ? 'all' : targets(text).map((s) => s.seat_id);
    // 不是這一項成員的席:先 assign_add(旁聽)再送,不然 aibd 回 to_not_a_member
    if (!all && S.nt.node) {
      const mem = new Set((S.nt.members || []).map((m) => String(m.seat || '').replace(/^seat:/, '')));
      const added = [];
      for (const id of to) if (!mem.has(id)) {
        try { await act('assign_add', { node_id: S.nt.node, seat_id: id, role: 'observer' }); added.push(id); }
        catch (e) { throw Object.assign(new Error(''), { pageMsg: '沒把 @' + seatName(seatById(id) || { seat_id: id }) + ' 加進這一項:' + (e && e.code === 'assign_built_in_page_only' ? '用戶資料夾有自己的頁時不能指派' : errText(e)) }); }
      }
      if (added.length) { S.nt.members = (S.nt.members || []).concat(added.map((id) => ({ seat: 'seat:' + id, role: 'observer' }))); }
    }
    const f = { thread_id: S.nt.thread_id, body: text, idem_key: idem() };
    if (all || to.length) f.to = to;
    await act('thread_send', f);
    say(all ? '已送給這一項的全部席' : to.length ? '已送,叫了 ' + to.length + ' 席' : '已記在這一項');
  }

  // ---------- 卡片視窗(第四波,SPEC §15:樣子與內容照流程表 board/stations.html 的 #dlg)----------
  // 頂列(站別、卡號、第幾站 / 7、標題、路徑)→ 真人需求 → 走到哪一站 → 基本資料八格 → 為什麼做 →
  // 紀錄(分類頁籤 + 時間線,新的在上)→ 底下的項目 → 用量。資料:GET /api/v1/nodes/{id}(7b81e),
  // 還沒有就用左樹已讀到的同一份(tree.json 的欄位);用量 GET /api/v1/nodes/{id}/usage(7ebca)
  const REC = { need: '真人需求', yes: '真人同意', bio: '指紋', cc: 'AI 遇事不決', sum: '總結', ho: '交接', drv: '自動推進', shd: '影子續跑', host: '主機', del: '刪除 / 清理', dev: '開發' };
  // 用戶資料夾頁拿到的 need / why / 紀錄是 {len, sha256}(7b81e 12:27),不是字:只寫幾個字、內建頁可讀
  const txt = (v) => (typeof v === 'string' ? v : v && typeof v === 'object' && v.len != null ? '(' + v.len + ' 字,內建頁可讀)' : v == null ? '' : String(v));
  // 519d0 裁定 2(11-18 13:0x):紀錄分類只留 aibd 一份(7b81e),頁面不自己分;過渡期 kind 可空 ⇒ 不標、只算在「全部」
  function recordsOf(n) {
    // 分類只看 aibd(519d0 r16 裁定 2):kinds 全部(7b81e 430b6a99),舊版只有 kind;頁面不自己分
    if (Array.isArray(n.records)) return n.records.map((r) => {
      const ks = (Array.isArray(r.kinds) ? r.kinds : [r.kind]).filter((k) => REC[k]);
      return { at: r.at || '', by: r.by || '', k: ks[0] || '', ks, t: txt(r.text) };
    });
    return String(n.evidence || '').split(' | ').filter(Boolean).map((x) => {
      const m = /^(\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)\s+(\S+)\s+([\s\S]*)$/.exec(x);
      return m ? { at: m[1], by: m[2], k: '', t: m[3] } : { at: '', by: '', k: '', t: x };
    }).reverse();
  }
  // 負責席 / 審查人:用標誌(滑過看席位與模型),不寫字;找不到 2.0 席就用席碼當名字
  function seatLogos(v) {
    const L = (Array.isArray(v) ? v : String(v || '').split(/[\s,,、]+/)).map((x) => String(x || '').trim()).filter(Boolean);
    if (!L.length) return '<span class="na">—</span>';
    return L.slice(0, 4).map((c) => logo(S.seats.find((s) => s.code === c || s.seat_id === c || seatName(s) === c) || { seat_id: c, name: c }, 'big')).join('');
  }
  // 事件的狀態欄:aibd 給的代號一律轉成介面用字(519d0 22:32;Andy 看到 seat_busy);沒對照的照原字、記一筆在 S.unkState
  const STATE_TEXT = { seat_busy: '忙碌中', busy: '忙碌中', seat_idle: '閒著', idle: '閒著', queued: '排隊中', seat_queued: '排隊中', pending: '準備中', running: '在跑', seat_running: '在跑', stopped: '已停', seat_stopped: '已停', stop_unconfirmed: '停了、還沒確認結束', failed: '開不起來', seat_failed: '開不起來', reply: '等回應', waiting: '等回應', seat_waiting: '等回應', decide: '等你決定', quiet: '說忙、沒動', seat_quiet: '說忙、沒動', done: '做完了', rejoin_failed: '接回失敗', offline: '連不上', two_ways: '兩個做法', usage_high: '用量偏高', login_needed: '要登入' };
  function stateText(v) {
    const t = String(v || '').trim(); if (!t) return '';
    if (STATE_TEXT[t]) return STATE_TEXT[t];
    if (/^[a-z][a-z0-9_.]*$/.test(t)) { (S.unkState || (S.unkState = new Set())).add(t); if (S.unkState.size < 50) console.info('[ws] 沒有介面用字的狀態代號', t); }
    return t;
  }
  // 事件時間:台北、到分(11-18 22:28)
  function evTime(t) { const v = new Date(String(t || '').replace(' ', 'T')).getTime(); const p = Number.isFinite(v) ? tparts(v) : null; return p ? p.mo + '-' + p.d + ' ' + p.h + ':' + p.mi : ''; }
  function fld(k, v) { return `<div class="fd"><span>${esc(k)}</span><b class="${v ? '' : 'na'}">${v ? esc(v) : '—'}</b></div>`; }
  function fldH(k, h) { return `<div class="fd"><span>${esc(k)}</span><b>${h}</b></div>`; }
  function spark(a) {
    if (!Array.isArray(a) || a.length < 2) return '';
    const mx = Math.max(1, ...a), w = 240, h = 36, st = w / (a.length - 1);
    return `<svg class="spk" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="currentColor" stroke-width="1.6" points="${a.map((v, i) => (i * st).toFixed(1) + ',' + (h - 2 - (v / mx) * (h - 4)).toFixed(1)).join(' ')}"/></svg>`;
  }
  // 7ebca:hours 只列有用量的小時 ⇒ 從第一小時到最後一小時,中間沒有的補 0(最多 14 天)
  function fillHours(hs, unit) {
    const m = new Map(); for (const h of hs) m.set(Number(h[0]), (m.get(Number(h[0])) || 0) + (Number(h[1]) || 0) + (Number(h[2]) || 0));
    const ks = [...m.keys()].filter(Number.isFinite).sort((x, y) => x - y);
    if (!ks.length) return [];
    const out = []; for (let t = ks[0]; t <= ks[ks.length - 1] && out.length < 336; t += unit) out.push(m.get(t) || 0);
    return out;
  }
  // ---------- 卡片視窗:用量(照流程表 mchart / costOf)----------
  const TZL = DISP_TZL;
  const MCOL = (i) => 'hsl(' + ((i * 47 + 18) % 360) + ' 72% 64%)';
  const numK = (x) => (x >= 1e6 ? (x / 1e6).toFixed(1) + 'M' : x >= 1e4 ? Math.round(x / 1e3) + 'k' : x >= 1e3 ? (x / 1e3).toFixed(1) + 'k' : String(Math.round(x) | 0));
  const durT = (sec) => (!sec ? '—' : sec >= 3600 ? (sec / 3600).toFixed(1) + ' 小時' : Math.max(1, Math.round(sec / 60)) + ' 分');
  const useRows = (u) => (u && (Array.isArray(u.seats) ? u.seats : Array.isArray(u.usage) ? u.usage : null)) || [];
  const tot5 = (r) => { const t = r && r.total && typeof r.total === 'object' ? r.total : {}; return { out: Number(t.output) || 0, ni: Number(t.new_input) || 0, rr: Number(t.reread) || 0, rep: Number(t.replies) || 0, act: Number(t.active_s) || 0 }; };
  const spentOf = (r) => (r && r.spent != null ? Number(r.spent) || 0 : (tot5(r).out + tot5(r).ni));
  // 每席每格的序列:hours = [[格起點(unix 秒), 輸出, 新輸入, 重讀, 回覆]…](7ebca r4);一格的值 = 輸出 + 新輸入
  function mchartWs(u, still) {
    const rows = useRows(u), unit = Number(u && u.unit_s) || 3600;
    const seats = rows.filter((r) => Array.isArray(r.hours) && r.hours.length);
    const W = 1000, H = 210, L = 46, R = 12, T = 14, Bm = 30;
    const head = (a, b) => `<div class="mch"><span>${a}</span><span>${b}</span></div>`;
    if (!seats.length) {
      let g = ''; for (let k = 0; k <= 3; k++) { const y = T + (k / 3) * (H - T - Bm); g += `<line x1="${L}" x2="${W - R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" class="mcg"/>`; }
      return `<div class="mc">${head('每個 AI 花掉的 token', '')}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-sty="width:100%;height:210px" role="img" aria-label="還沒有用量">${g}<text x="${W / 2}" y="${H / 2}" class="mct" text-anchor="middle">還沒有用量</text></svg></div>`;
    }
    const sec = (t) => { const v = Number(t); return v > 1e12 ? Math.floor(v / 1000) : v; };
    let h0 = Infinity, h1 = 0;
    for (const r of seats) for (const h of r.hours) { const b = Math.floor(sec(h[0]) / unit); if (b < h0) h0 = b; if (b > h1) h1 = b; }
    const nowB = Math.floor(Date.now() / 1000 / unit); if (nowB > h1) h1 = nowB; if (h1 - h0 < 6) h0 = h1 - 6;
    const span = h1 - h0, bin = span > 120 ? 4 : span > 48 ? 2 : 1, nb = Math.floor(span / bin) + 1;
    const xs = (i) => L + (nb > 1 ? i / (nb - 1) : 0) * (W - L - R);
    const ser = [], tot = new Array(nb).fill(0);
    for (const r of seats) { const a = new Array(nb).fill(0); for (const h of r.hours) { const i = Math.floor((Math.floor(sec(h[0]) / unit) - h0) / bin); const v = (Number(h[1]) || 0) + (Number(h[2]) || 0); if (i >= 0 && i < nb) { a[i] += v; tot[i] += v; } } ser.push(a); }
    const mx = Math.max(1, ...tot), ys = (v) => T + (1 - v / mx) * (H - T - Bm);
    const pathOf = (a) => a.map((v, i) => (i ? 'L' : 'M') + xs(i).toFixed(1) + ' ' + ys(v).toFixed(1)).join('');
    let g = '';
    for (let k = 0; k <= 3; k++) { const v = (mx * k) / 3, y = ys(v); g += `<line x1="${L}" x2="${W - R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" class="mcg"/><text x="${L - 6}" y="${(y + 3).toFixed(1)}" class="mct" text-anchor="end">${numK(v)}</text>`; }
    // 日界線(本機時區午夜)
    for (let b = h0; b <= h1; b++) { const pp = tparts(b * unit * 1000); if (unit !== 3600 || !pp || pp.h !== '00') continue; const x = xs((b - h0) / bin); g += `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${T}" y2="${H - Bm}" class="mcd"/><text x="${(x + 4).toFixed(1)}" y="${H - 10}" class="mct">${pp.mo}-${pp.d}</text>`; }
    let ln = ''; seats.forEach((r, i) => { ln += `<path d="${pathOf(ser[i])}" pathLength="1" class="mcl" data-sty="stroke:${MCOL(i)};--d:${i * 160}ms"><title>${esc(seatName(seatById(String(r.seat_id || r.seat || '').replace(/^seat:/, '')) || { seat_id: r.seat_id || r.seat }))}</title></path>`; });
    const tp = pathOf(tot), li = nb - 1;
    ln += `<path d="${tp}L${xs(li).toFixed(1)} ${H - Bm}L${xs(0).toFixed(1)} ${H - Bm}Z" class="mca"/><path d="${tp}" pathLength="1" class="mcl mctot" data-sty="--d:${seats.length * 160}ms"/><circle cx="${xs(li).toFixed(1)}" cy="${ys(tot[li]).toFixed(1)}" r="4" class="mcn" data-sty="--d:${seats.length * 160 + 2200}ms"/>`;
    const lg = `<div class="mclg"><span class="mck"><i data-sty="background:var(--fg);height:3px"></i>合計</span>${seats.map((r, i) => `<span class="mck"><i data-sty="background:${MCOL(i)}"></i>${seatLogos(r.seat_id || r.seat)}</span>`).join('')}</div>`;
    const per = bin * unit >= 3600 ? (bin * unit) / 3600 + ' 小時' : Math.round((bin * unit) / 60) + ' 分';
    return `<div class="mc ${still ? 'still' : ''}">${head('每個 AI 花掉的 token(照日期,每 ' + per + '一點;粗線 = 合計)', '最高 ' + numK(mx) + ' / ' + per)}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-sty="width:100%;height:210px" role="img" aria-label="這張卡各席花掉的 token">${g}${ln}</svg>${lg}</div>`;
  }
  function firstLast(r, unit) {
    const t = r && r.total && typeof r.total === 'object' ? r.total : {};
    const ms = (v) => { const n = typeof v === 'string' ? new Date(v).getTime() : Number(v); return n > 1e12 ? n : n > 0 ? n * 1000 : NaN; };
    if (t.first_at && t.last_at && Number.isFinite(ms(t.first_at)) && Number.isFinite(ms(t.last_at))) {
      const f2 = (x) => { const p = tparts(x); return p ? p.mo + '-' + p.d + ' ' + p.h + ':' + p.mi : ''; };
      return f2(ms(t.first_at)) + ' → ' + f2(ms(t.last_at));
    }
    const hs = (Array.isArray(r.hours) ? r.hours : []).filter((h) => (Number(h[1]) || 0) + (Number(h[2]) || 0) + (Number(h[3]) || 0) > 0).map((h) => { const v = Number(h[0]); return v > 1e12 ? v : v * 1000; });
    if (!hs.length) return '';
    const f = (t) => { const p = tparts(t); return p ? p.mo + '-' + p.d + ' ' + p.h + ':' + p.mi : ''; };
    return f(Math.min(...hs)) + ' → ' + f(Math.max(...hs) + (unit || 3600) * 1000);
  }
  function costHtml(u, kids) {
    const rows = useRows(u).slice().sort((a, b) => spentOf(b) - spentOf(a)), unit = Number(u && u.unit_s) || 3600;
    const T = { sp: 0, out: 0, ni: 0, rr: 0, rep: 0, act: 0 };
    for (const r of rows) { const t = tot5(r); T.sp += spentOf(r); T.out += t.out; T.ni += t.ni; T.rr += t.rr; T.rep += t.rep; T.act += t.act; }
    // 開頭 = 這張卡 + 底下(照流程表 costOf 的 all):aibd 回 subtree(整棵子樹合計)就用;沒有就加上已讀到的直接子項
    const A = Object.assign({}, T); let subNote = '';
    const st = u && u.subtree && typeof u.subtree === 'object' ? u.subtree : null;
    // 7ebca 4e16f769e:subtree = 這項 + 整棵子樹(含自己),nodes = 有用量的項目數(含自己)⇒ 直接用,不再加本卡
    if (st) { A.sp = Number(st.spent) || 0; A.out = Number(st.output) || 0; A.ni = Number(st.new_input) || 0; A.rr = Number(st.reread) || 0; A.rep = Number(st.replies) || 0; A.act = Number(st.active_s) || 0; const k = Math.max(0, (Number(st.nodes) || 0) - (rows.length ? 1 : 0)); subNote = k ? '含底下 ' + k + ' 項' : ''; }
    else if (S.card.kidUse) { let k = 0; for (const id in S.card.kidUse) { const rs = useRows(S.card.kidUse[id]); if (rs.length) k++; for (const r of rs) { const x = tot5(r); A.sp += spentOf(r); A.out += x.out; A.ni += x.ni; A.rr += x.rr; A.rep += x.rep; A.act += x.act; } } subNote = k ? '含底下直接 ' + k + ' 項' : ''; }
    else if (kids && kids.length) subNote = '底下讀取中…';
    const sum = `<div class="cub0"><b>${numK(A.sp)}</b><span>${subNote ? esc(subNote) + ' · ' : ''}動手 ${esc(durT(A.act))} · 輸出 ${numK(A.out)} · 新輸入 ${numK(A.ni)} · 重讀(快取)${numK(A.rr)} · ${A.rep | 0} 次回覆</span></div>`;
    const headRow = '<div class="ur ut uth"><span></span><span>動手多久</span><span>第一次 → 最後一次</span><span>回覆</span><span>花掉的 token</span><span>其中</span><span>重讀(快取)</span></div>';
    const sigma = rows.length > 1 ? `<div class="ur ut utt"><span class="sig">Σ</span><b>${esc(durT(T.act))}</b><span></span><span>${T.rep | 0} 次回覆</span><b>${numK(T.sp)}</b><span>輸出 ${numK(T.out)} · 新輸入 ${numK(T.ni)}</span><span>重讀 ${numK(T.rr)}</span></div>` : '';
    const own = rows.length ? headRow + sigma + rows.map((r) => { const t = tot5(r); return `<div class="ur ut">${seatLogos(r.seat_id || r.seat)}<b>${esc(durT(t.act))}</b><span>${esc(firstLast(r, unit) || '—')}</span><span>${t.rep | 0} 次回覆</span><b>${numK(spentOf(r))}</b><span>輸出 ${numK(t.out)} · 新輸入 ${numK(t.ni)}</span><span>重讀 ${numK(t.rr)}</span></div>`; }).join('') : '<div class="none">這張卡自己還沒有用量</div>';
    const ownBox = `<details class="cu2" ${S.card.ownShut ? '' : 'open'}><summary data-owncost="1">這張卡自己 · ${rows.length} 席${rows.length ? ' · 動手 ' + esc(durT(T.act)) + ' · 花掉 ' + numK(T.sp) : ''}</summary><div class="uw">${own}</div></details>`;
    let kidBox = '';
    if (kids && kids.length) {
      const KU = S.card.kidUse || {};
      const got = kids.slice(0, 12).map((k) => ({ k, u: KU[k.id] })).filter((x) => x.u && useRows(x.u).length);
      got.sort((a, b) => useRows(b.u).reduce((s, r) => s + spentOf(r), 0) - useRows(a.u).reduce((s, r) => s + spentOf(r), 0));
      const sub = got.reduce((s, x) => s + useRows(x.u).reduce((a, r) => a + spentOf(r), 0), 0);
      const body = !S.card.kidOpen ? '' : S.card.kidLoading ? '<div class="none">讀取中…</div>' : got.length ? got.map(({ k, u: ku }) => { const rs = useRows(ku); const t = rs.reduce((a, r) => { const x = tot5(r); a.out += x.out; a.ni += x.ni; a.rr += x.rr; a.act += x.act; a.sp += spentOf(r); return a; }, { out: 0, ni: 0, rr: 0, act: 0, sp: 0 }); return `<div class="ur uc" data-card="${esc(k.id)}" role="button" tabindex="0"><span class="kid">${esc(k.id)}</span><span class="uct">${esc(String(k.title || k.id).slice(0, 40))}</span><span>${rs.length} 席 · 動手 ${esc(durT(t.act))}</span><b>${numK(t.sp)}</b><span>輸出 ${numK(t.out)} · 新輸入 ${numK(t.ni)} · 重讀 ${numK(t.rr)}</span></div>`; }).join('') : '<div class="none">底下的項目還沒有用量</div>';
      kidBox = `<details class="cu2" ${S.card.kidOpen ? 'open' : ''}><summary data-kidcost="1">底下各項目的用量(照花掉的 token 排,最多列 12 張)${S.card.kidOpen && !S.card.kidLoading ? ' · ' + got.length + ' 張有用量 · 花掉 ' + numK(sub) : ''}</summary><div class="uw">${body}</div></details>`;
    }
    return `<div class="cu0">${sum}${mchartWs(u, S.card.drawn)}${ownBox}${kidBox}</div>`;
  }
  async function loadKidCost() {
    const c = S.card; if (!c || c.kidUse) return;
    const kids = (Array.isArray(c.n && c.n.children) ? c.n.children : (S.kids && S.kids.get(c.id)) || []).slice(0, 12);
    c.kidLoading = true; renderCard();
    const out = {};
    await Promise.all(kids.map(async (k) => { out[k.id] = await soft(get('/api/v1/nodes/' + encodeURIComponent(k.id) + '/usage')).catch(() => null); }));
    if (S.card === c) { c.kidUse = out; c.kidLoading = false; renderCard(); }
  }
  function useHtml(u) {
    const rows = u && (Array.isArray(u.seats) ? u.seats : Array.isArray(u.usage) ? u.usage : null);
    if (!rows || !rows.length) return '<div class="none">還沒有用量資料</div>';
    const num = (x) => (x >= 1e6 ? (x / 1e6).toFixed(1) + 'M' : x >= 1e4 ? Math.round(x / 1e3) + 'k' : x >= 1e3 ? (x / 1e3).toFixed(1) + 'k' : String(x | 0));
    const dur = (sec) => (!sec ? '—' : sec >= 3600 ? (sec / 3600).toFixed(1) + ' 小時' : Math.max(1, Math.round(sec / 60)) + ' 分');
    const T = { spent: 0, output: 0, new_input: 0, reread: 0, replies: 0, active_s: 0 };
    const k = (r, f) => Number(r.total && typeof r.total === 'object' ? r.total[f] : 0) || 0;
    for (const r of rows) { T.spent += r.spent != null ? Number(r.spent) || 0 : k(r, 'output') + k(r, 'new_input'); for (const f of ['output', 'new_input', 'reread', 'replies', 'active_s']) T[f] += k(r, f); }
    const head = `<div class="usum"><b>${num(T.spent)}</b><span>${rows.length} 席 · 動手 ${esc(dur(T.active_s))} · 輸出 ${num(T.output)} · 新輸入 ${num(T.new_input)} · 重讀(快取)${num(T.reread)} · ${T.replies | 0} 次回覆</span></div>`;
    return head + '<div class="use2">' + rows.map((r) => {
      // 7ebca r4:hours = [[hour_start, output, new_input, reread, replies]…],曲線 = output + new_input(流程表同算法);spent = 合計
      const a = Array.isArray(r.hours) ? fillHours(r.hours, Number(u.unit_s) || 3600)
        : Array.isArray(r.hourly) ? r.hourly.map((h) => (typeof h === 'number' ? h : Number(h && (h.tokens != null ? h.tokens : h.used)) || 0)) : [];
      const tot = r.spent != null ? Number(r.spent) : typeof r.total === 'number' ? r.total : r.total && typeof r.total === 'object' ? (Number(r.total.output) || 0) + (Number(r.total.new_input) || 0) : a.reduce((x, y) => x + y, 0);
      const act = r.total && r.total.active_s ? ' · 跑了 ' + (r.total.active_s >= 3600 ? (r.total.active_s / 3600).toFixed(1) + ' 小時' : Math.round(r.total.active_s / 60) + ' 分') : '';
      const det = r.total && typeof r.total === 'object' ? `輸出 ${num(k(r, 'output'))} · 新輸入 ${num(k(r, 'new_input'))} · 重讀 ${num(k(r, 'reread'))} · ${k(r, 'replies') | 0} 次回覆` : '';
      return `<div class="ur">${seatLogos(r.seat_id || r.seat)}<span class="spw">${spark(a)}</span><b>${num(tot)}${esc(act)}</b>${det ? `<small>${esc(det)}</small>` : ''}</div>`;
    }).join('') + '</div>';
  }
  // 事件卡的卡號(7ebca r4):next:<kind>:<項目> 開那一項;next:<kind>:#PR、andy:<id> 不是項目 ⇒ 不開
  function cardOf(ref) {
    ref = String(ref || '');
    const m = /^next:[^:]+:(.+)$/.exec(ref);
    if (m) return m[1].startsWith('#') ? '' : m[1];
    return /^(andy|local):/.test(ref) ? '' : ref;
  }
  // 519d0 19:5x(Andy 問「右上方的小卡,為什麼只有展開,沒有點擊放大之後的細項畫面」):每張小卡都開得出視窗
  async function openEv(key) {
    const x = slots().find((e) => e.key === key); if (!x) return;
    let id = x.node || cardOf(x.ref);
    if (!id && x.seat) {
      const a = await soft(get('/api/v1/assignments?seat=' + encodeURIComponent(x.seat))).catch(() => null);
      const L = (a && a.assignments) || [];
      const r = L.find((y) => y.role === 'author') || L[0];
      if (r && r.node_id) id = r.node_id;
    }
    return openCard(id || '', x);
  }
  async function openCard(id, ev, again) {
    if (!id && !ev) return;
    const base = id && S.tree && S.tree.get(id);
    // ↻ 重讀同一張:留著同一個狀態(不重播進場、不閃讀取中、鈕照轉),讀回來的蓋上去
    if (again && S.card && S.card.id === id) { S.card.kidUse = null; S.card.kidLoading = false; }
    else S.card = { id, ev: ev || null, only: !id, n: base ? Object.assign({}, base) : { id, title: id }, tab: 'all', use: null, loaded: !id };
    renderCard();
    if (!id) return;
    const r = await soft(get('/api/v1/nodes/' + encodeURIComponent(id))).catch(() => null);
    if (S.card && S.card.id === id && r) {
      const n = r.node || r;
      if (n.station != null && n.status == null) n.status = n.station;
      S.card.n = Object.assign(S.card.n, n);
    }
    // 卡號在左樹與 aibd 都查不到(例如待人決策的 H-a6)⇒ 只開這則事件
    if (S.card && S.card.id === id && !base && !r && ev) { S.card.only = true; S.card.loaded = true; return renderCard(); }
    const u = await soft(get('/api/v1/nodes/' + encodeURIComponent(id) + '/usage')).catch(() => null);
    if (S.card && S.card.id === id) { S.card.use = u; S.card.loaded = true; S.card.at = new Date().toISOString(); renderCard(); if (!(u && u.subtree)) loadKidCost(); }
  }
  let dlg = null, dlgHtml = '';
  function renderCard() {
    if (!S.card) { if (dlg && dlg.open) dlg.close(); return; }
    if (!dlg) {
      dlg = document.createElement('dialog'); dlg.className = 'cdlg'; document.body.appendChild(dlg);
      dlg.addEventListener('click', (e) => {
        if (e.target === dlg || e.target.closest('[data-cclose]')) { S.card = null; dlg.close(); return; }
        const t = e.target.closest('[data-ctab]'); if (t) { S.card.tab = t.dataset.ctab; S.card.recAll = false; return renderCard(); }
        if (e.target.closest('[data-recall]')) { S.card.recAll = !S.card.recAll; return renderCard(); }
        const rf = e.target.closest('[data-crf]');
        if (rf && S.card) { const c = S.card; c.spinUntil = Date.now() + 700; setTimeout(() => { if (S.card === c) renderCard(); }, 720); return openCard(S.card.id, S.card.ev, true); }
        const kd = e.target.closest('[data-kidcost]'); if (kd && S.card) { e.preventDefault(); S.card.kidOpen = !S.card.kidOpen; if (S.card.kidOpen) loadKidCost(); return renderCard(); }
        const ow = e.target.closest('[data-owncost]'); if (ow && S.card) { e.preventDefault(); S.card.ownShut = !S.card.ownShut; return renderCard(); }
        const c = e.target.closest('[data-card]'); if (c) return openCard(c.dataset.card);
        // 這則事件的作答 / 叫交接 / 重開 / 看這一席 / 晚點:跟主畫面同一條路;會換畫面的(看這一席、晚點、作答)先關視窗
        const ac = e.target.closest('.evact [data-pick], .evact [data-answer], .evact [data-handoff], .evact [data-restart], .evact [data-go], .evact [data-later]');
        if (ac) { if (ac.matches('[data-go], [data-later], [data-pick], [data-answer]')) { S.card = null; dlg.close(); } return onMainClick(e); }
      });
      dlg.addEventListener('input', (e) => { if (e.target.dataset && e.target.dataset.ansfor) S.ansDraft[e.target.dataset.ansfor] = e.target.value; });
      dlg.addEventListener('keydown', (e) => {
        if (e.target.dataset && e.target.dataset.ansfor && e.key === 'Enter' && !e.isComposing) { e.preventDefault(); const b = e.target.closest('.ans') && e.target.closest('.ans').querySelector('[data-answer]'); if (b) b.click(); }
      });
      // close 事件是非同步的:關了又馬上開別張時,不要把新開的那張清掉
      dlg.addEventListener('close', () => { if (dlg.open) return; S.card = null; dlgHtml = ''; });
    }
    const ev = S.card.ev;
    const evSec = ev ? `<div class="dsec">這則事件</div><div class="evd"><div class="evh"><span class="ek ${TAG_K[ev.tag] || 'k3'}">${esc(ev.tag || LV_TAG[ev.lv] || '')}</span><b>${esc(ev.text)}</b></div><div class="fg">${fld('什麼時候', ev.t ? when(ev.t) + ' · ' + DISP_TZL + ' ' + evTime(ev.t) : '')}${ev.seat ? fldH('哪一席', logo(seatById(ev.seat) || { seat_id: ev.seat, name: ev.seat }, 'sm') + ' ' + esc(ev.who || '')) : ev.who ? fld('誰', ev.who) : ''}${fld('我們的建議', ev.rec)}${fld('狀態', stateText(ev.st))}${fld('出處', ev.src)}${ev.ref ? fld('卡號', ev.ref) : ''}</div><div class="evact">${evBody(ev, true)}</div></div>` : '';
    if (S.card.only) {
      const html = `<div class="dw"><div class="dh u-todo"><div class="dh1"><span class="pill u-todo">${esc(ev ? ev.tag || LV_TAG[ev.lv] || '事件' : '事件')}</span><span class="kid">${esc(ev && ev.ref ? ev.ref : '')}</span><span>沒有掛項目</span><button type="button" class="x" data-cclose="1" aria-label="關閉" autofocus>✕</button></div><div class="dt">${esc(ev ? ev.text : '')}</div></div>
        <div class="db">${evSec}</div><div class="df"><span></span><span>按 Esc 或點外面關閉</span></div></div>`;
      if (html !== dlgHtml) { dlg.innerHTML = html; dlgHtml = html; dlg.dataset.id = ''; }
      if (!dlg.open) dlg.showModal();
      return;
    }
    const n = S.card.n, si = stationIdx(n.status), recs = recordsOf(n);
    // 519d0 21:2x 裁(規則之後放 aibd,7b81e N-workspace-c3;先照 stations-gen 同一段):
    // 舊站名「實查不通過」= 第 3 站 + 紅標;新做法:站照寫的(例如未驗)、驗收不通過且 H / M 沒關 ⇒ 站不動、加紅色「驗不通過」
    const vf = n.verify_fail && typeof n.verify_fail === 'object' ? !n.verify_fail.closed : n.verify_fail === true;
    const u = si == null ? 'todo' : n.status === '實查不通過' || n.status === '卡住' || vf ? 'bad' : si === 6 ? 'ok' : si >= 2 ? 'run' : 'todo';
    const crumb = []; for (let p = n.parent && S.tree ? S.tree.get(n.parent) : null; p; p = p.parent ? S.tree.get(p.parent) : null) crumb.unshift(p);
    const step = STATIONS.map((t, j) => `<div class="sp ${si != null && j < si ? 'past' : si === j ? 'cur u-' + u : ''}"${si != null && j <= si ? ` data-sty="--d:${j * 480}ms"` : ''}><i>${si != null && j < si ? '✓' : j + 1}</i>${esc(t)}</div>`).join('');
    const ap = n.approved || (n.approved_by ? { by: n.approved_by, at: n.approved_at, pointer: n.approval_ref } : null);
    const pf = n.platform || n.platforms;
    const pfx = pf && typeof pf === 'object' ? Object.entries(pf).map(([k, v]) => (k === 'mac' ? 'Mac ' : k === 'win' ? 'Windows ' : k + ' ') + v).join(' · ') : pf || '';
    const wait = n.after || n.wait;
    const cnt = {}; for (const r of recs) for (const k of r.ks || [r.k]) cnt[k] = (cnt[k] || 0) + 1;
    if (S.card.tab !== 'all' && !cnt[S.card.tab]) S.card.tab = 'all';
    const tabs = ['all'].concat(Object.keys(REC)).map((k) => `<button type="button" data-ctab="${k}" class="${S.card.tab === k ? 'on' : ''}" ${k !== 'all' && !cnt[k] ? 'disabled' : ''}>${k === 'all' ? '全部' : REC[k]} <i>${k === 'all' ? recs.length : cnt[k] || 0}</i></button>`).join('');
    const shownAll = recs.filter((r) => S.card.tab === 'all' || (r.ks || [r.k]).includes(S.card.tab));
    const REC_MAX = 6, shown = S.card.recAll ? shownAll : shownAll.slice(0, REC_MAX);
    const recMore = shownAll.length > REC_MAX ? `<button type="button" class="more" data-recall="1">${S.card.recAll ? '收起 ▴' : '再看 ' + (shownAll.length - REC_MAX) + ' 筆 ▾'}</button>` : '';
    const stepMs = shown.length ? Math.min(520, Math.round(7000 / shown.length)) : 0;
    const tl = shown.length ? shown.map((r, i) => `<div class="te ${i === 0 ? 'new' : ''}" data-sty="--d:${(shown.length - 1 - i) * stepMs}ms;"><time>${esc(r.at || '沒有寫時間')}${(r.ks || [r.k]).filter((k) => k && k !== 'dev').map((k) => `<b class="kt kt-${k}">${esc(REC[k])}</b>`).join('')}</time><p>${r.by ? seatLogos(r.by) + ' ' : ''}${esc(r.t)}</p></div>`).join('') : '<div class="none">還沒有紀錄</div>';
    const kids = Array.isArray(n.children) ? n.children : (S.kids && S.kids.get(n.id)) || [];
    const chh = kids.length ? '<div class="ch">' + kids.map((c) => { const ci = stationIdx(c.status || c.station); return `<div class="cr" data-card="${esc(c.id)}" tabindex="0" role="button"><span class="stn ${ci != null ? 's' + (ci + 1) : ''}">${ci != null ? ci + 1 + ' ' + esc(STATIONS[ci]) : esc(c.status || c.station || '—')}</span><b>${esc(c.title || c.id)}</b><em>${c.owner ? seatLogos(c.owner) : ''}</em></div>`; }).join('') + '</div>' : '<div class="none">沒有再往下分的項目</div>';
    const html = `<div class="dw"><div class="dh u-${u}"><div class="dh1"><span class="pill u-${n.status === '實查不通過' || n.status === '卡住' ? 'bad' : vf ? (si === 6 ? 'ok' : si >= 2 ? 'run' : 'todo') : u}">${esc(n.status || '沒有狀態')}</span>${vf ? '<span class="pill u-bad">驗不通過</span>' : ''}<span class="kid">${esc(n.id)}</span><span>${si != null ? '第 ' + (si + 1) + ' 站 / 7' : '不在流程上'}</span><button type="button" class="x" data-cclose="1" aria-label="關閉" autofocus>✕</button></div>
      <div class="dt">${esc(n.title || n.id)}</div><div class="crumb">${crumb.length ? crumb.map((p) => `<a data-card="${esc(p.id)}">${esc(String(p.title || p.id).slice(0, 16))}</a>`).join(' › ') : '總綱'}</div></div>
      <div class="db ${S.card.still ? 'still' : ''}" data-sty="--sp:${si != null && si > 0 ? si / 6 : 0};--st:${Math.max(si || 0, 0) * 480 + 1200}ms;--sc:var(--${u === 'todo' ? 'mute' : u === 'ok' ? 'ok' : u === 'bad' ? 'bad' : 'you'});--t:${Math.max(1200, shown.length * stepMs + 1200)}ms">${evSec}<div class="dsec">真人需求</div><div class="need">${n.need ? esc(txt(n.need)) : '<span class="na">—</span>'}</div>
      <div class="dsec">走到哪一站</div><div class="step stgo">${step}</div>
      <div class="dsec">基本資料</div><div class="fg">${fldH('負責席', seatLogos([n.owner].concat(n.with || [])))}${fldH('審查人', seatLogos(n.reviewer))}${fld('最後更新', n.updated)}${fld('預計', n.eta)}${fld('誰同意的', ap ? [ap.by, ap.at].filter(Boolean).join(' ') : '')}${fld('同意的依據', ap ? ap.pointer || ap.pt || '' : '')}${fld('要先等', Array.isArray(wait) ? wait.join('、') : wait)}${fld('兩平台', pfx)}</div>
      <div class="dsec">為什麼做</div>${n.why ? `<div class="why">${esc(txt(n.why))}</div>` : '<div class="none">沒有寫</div>'}
      <div class="dsec"><span>紀錄(新的在上)</span><span>${n.records_total > recs.length ? recs.length + ' / ' + n.records_total : recs.length} 筆</span></div>${Object.keys(cnt).some((k) => k) ? `<div class="tlt">${tabs}</div>` : ''}<div class="tlx"><div class="tlw">${tl}</div>${recMore}</div>
      <div class="dsec"><span>底下的項目</span><span>${kids.length ? (n.children_total > kids.length ? kids.length + ' / ' + n.children_total : kids.length) + ' 項' : ''}</span></div>${chh}
      <div class="dsec">用了多久、花了多少</div>${S.card.loaded ? costHtml(S.card.use, kids) : '<div class="none">讀取中…</div>'}
      </div><div class="df"><span>資料時間:${esc(S.card.at ? TZL + ' ' + hhmm(S.card.at) : '—')}${S.tree && S.tree.has(n.id) ? ' · 跟流程表同一份資料' : ''} <button type="button" class="rf ${S.card.spinUntil && Date.now() < S.card.spinUntil ? 'spin' : ''}" data-crf="1" title="重讀最新的資料">↻</button></span><span>按 Esc 或點外面關閉</span></div></div>`;
    if (html !== dlgHtml) {
      const sc = dlg.querySelector('.db'); const top = sc && dlg.dataset.id === n.id ? sc.scrollTop : 0; dlg.innerHTML = html; dlgHtml = html; dlg.dataset.id = n.id; const nb = dlg.querySelector('.db'); if (nb) nb.scrollTop = top;
      if (!S.card.still) { const c = S.card; setTimeout(() => { if (S.card === c) c.still = true; }, 50); }
      if (S.card.use && !S.card.drawn) { const c = S.card; setTimeout(() => { if (S.card === c) c.drawn = true; }, 50); }
    }
    if (!dlg.open) dlg.showModal();
  }

  // ---------- 加一席(第二波;b4297 act seat_open + 7b81e assign_add)----------
  // 品牌照 GET /api/v1/version 的 capabilities.providers;不能開的顯示但不能按(5d33a B5,aibd 也擋)。
  // 模式照該品牌的 modes(沒寫 = 只有 safe)。人格從現有席挑。角色:開好後對左樹選中的項目 assign_add;
  // 指派失敗席照留、標出來(B4 兩步)
  const ROLES = ['作者', '審查', '驗收', '換上', '相依', '旁聽'];
  // 送出用英文六字(7b81e 11:07,與 a9136 thread members 同一組);畫面顯示中文
  const ROLE_EN = { 作者: 'author', 審查: 'reviewer', 驗收: 'verifier', 換上: 'deployer', 相依: 'depends', 旁聽: 'observer' };
  const ROLE_ZH = Object.fromEntries(Object.entries(ROLE_EN).map(([z, e]) => [e, z]));
  // §22(Andy 22:2x「這個不要支援 openrouterjev 並且這個畫面的設計太差了,重新依風格設計」):品牌只列 2.0 支援的四個,
  // 舊設定裡的自訂名稱(openrouterjev、typesafe、*bot…)一律不畫;capabilities 有回就照它標能不能開,沒回的變淡
  const ADD_BRANDS = [['claude', 'Claude', 'bc'], ['codex', 'Codex', 'bx'], ['agy', 'Antigravity', 'bg'], ['grok', 'Grok', 'bk']];
  const BRAND_ALIAS = { claude: 'claude', codex: 'codex', agy: 'agy', antigravity: 'agy', gemini: 'agy', grok: 'grok' };
  function providers() {
    const L = (S.version && S.version.capabilities && S.version.capabilities.providers) || [];
    const got = {}; for (const p of L) { const k = p && BRAND_ALIAS[String(p.id || '').toLowerCase()]; if (k && !got[k]) got[k] = p; }
    if (!L.length) got.claude = { id: 'claude', open: true, modes: ['safe'] }; // 舊 aibd 沒 capabilities:只知道 claude 能開
    return ADD_BRANDS.filter(([k]) => got[k]).map(([k, name, b]) => {
      const p = got[k];
      // b4297 1c335b166:不能開時 reason = not_built(aibd 還不驅動)/ not_set_up(這台政策沒列)/ not_installed(列了但程式不在)
      const R = { not_built: '這版還不能開', not_set_up: '這台沒設定', not_installed: '這台沒裝', not_logged_in: '這台還沒登入' };
      const why = !p ? '這版還不能開' : p.open ? '' : R[p.reason] || '這台還不能開';
      return { id: p ? p.id : k, key: k, name, b, open: !!(p && p.open), modes: (p && p.modes) || ['safe'], why };
    });
  }
  function personas() {
    if (S.personaList && S.personaList.length) return S.personaList;
    const m = new Map();
    for (const x of S.seats) if (x.persona_uid && !m.has(x.persona_uid)) m.set(x.persona_uid, x.display || x.name || x.persona_uid);
    return [...m.entries()];
  }
  // 加一席視窗(§22):工作台風格,進場同卡片視窗;不放說明字
  function nodeOpts() {
    const out = [];
    const walk = (pid, d) => { for (const c of S.kids.get(pid) || []) { out.push([c.id, c.title || c.id, d]); if (out.length < 400) walk(c.id, d + 1); } };
    if (S.kids) walk(null, 0);
    if (!out.length && S.tree) for (const n of S.tree.values()) out.push([n.id, n.title || n.id, 0]);
    return out;
  }
  function personaList(q) {
    const PS = personas(); const k = String(q || '').trim().toLowerCase();
    return (k ? PS.filter(([u, nm]) => nm.toLowerCase().includes(k) || u.toLowerCase().includes(k)) : PS).slice(0, 8);
  }
  function personaRows(A) {
    const L = personaList(A.pq);
    return L.length ? L.map(([u, nm]) => `<button type="button" class="apr ${A.persona === u ? 'on' : ''}" data-addpu="${esc(u)}"><b>${esc(nm)}</b><i>${esc(u.slice(-6))}</i></button>`).join('') : '<div class="apn">找不到</div>';
  }
  const ICO_SAFE = '<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 4.6-3 8.3-7 10-4-1.7-7-5.4-7-10V6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  const ICO_DGRS = '<svg viewBox="0 0 24 24"><path d="M14.5 5.5a4 4 0 0 0 4.9 4.9l-8.6 8.6a2.1 2.1 0 0 1-3-3l8.6-8.6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  function addHtml() {
    const A = S.add, PV = providers();
    let pv = PV.find((p) => p.key === A.provider && p.open) || PV.find((p) => p.open) || null;
    if (pv) A.provider = pv.key;
    const canD = !!(pv && pv.modes.includes('dgrs'));
    if (A.mode === 'dgrs' && !canD) A.mode = 'safe';
    const PS = personas();
    if (!A.persona && PS[0]) A.persona = PS[0][0];
    const pn = (PS.find(([u]) => u === A.persona) || [A.persona, A.persona])[1];
    const NO = nodeOpts(); const node = A.node !== undefined ? A.node : S.sel || '';
    const nodeT = (S.tree && node && S.tree.get(node)) ? S.tree.get(node).title || node : '';
    const brand = PV.map((p) => `<button type="button" class="abr ${pv && p.key === pv.key ? 'on' : ''} ${p.open ? '' : 'off'}" data-addp="${esc(p.key)}" ${p.open ? '' : 'disabled aria-disabled="true"'}><span class="lg lgb ${p.b}">${ICON[p.b]}</span><b>${esc(p.name)}</b>${p.why ? `<i>${esc(p.why)}</i>` : ''}</button>`).join('');
    const mode = `<button type="button" class="amd ${A.mode !== 'dgrs' ? 'on' : ''}" data-addm="safe">${ICO_SAFE}<b>安全</b></button><button type="button" class="amd warn ${A.mode === 'dgrs' ? 'on' : ''} ${canD ? '' : 'off'}" data-addm="dgrs" ${canD ? '' : 'disabled'}>${ICO_DGRS}<b>能改程式</b>${canD ? '' : '<i>這個品牌沒有</i>'}</button>`;
    const pers = PS.length ? `<div class="aps"><input class="apq" data-addq="1" value="${esc(A.pq || '')}" placeholder="找人格" aria-label="找人格" autocomplete="off"><div class="apl" id="addpl">${personaRows(A)}</div></div>` : `<input class="apq" data-addf="persona" value="${esc(A.persona || '')}" placeholder="人格 uid" aria-label="人格">`;
    const role = `<select class="asel" data-addf="role" aria-label="角色"><option value="">不掛</option>${ROLES.map((r) => `<option ${A.role === r ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select>`;
    const where = `<select class="asel wide" data-addf="node" aria-label="掛在哪一項" title="${esc(nodeT)}" ${A.role ? '' : 'disabled'}><option value="">${A.role ? '選一項' : '—'}</option>${NO.map(([id, t, d]) => `<option value="${esc(id)}" title="${esc(t)}" ${id === node ? 'selected' : ''}>${'\u3000'.repeat(Math.min(d, 4))}${esc(t)}</option>`).join('')}</select>`;
    const step = A.busy ? (A.step === 2 ? '掛角色中…' : '開席中…') : '開';
    return `<div class="dlgbg"><div class="adlg ${A.still ? 'still' : ''}" role="dialog" aria-label="加一席">
      <div class="ah"><h3>加一席</h3><button type="button" class="dx" data-addx="1" aria-label="關閉">✕</button></div>
      <div class="asec"><span>品牌</span><div class="abrs">${brand}</div></div>
      <div class="asec"><span>模式</span><div class="amds">${mode}</div></div>
      <div class="asec"><span>人格</span>${PS.length ? `<div class="apsel"><b>${esc(pn || '')}</b></div>` : ''}${pers}</div>
      <div class="asec"><span>角色 · 掛在哪一項</span><div class="arow">${role}${where}</div></div>
      ${A.msg ? `<div class="aerr" role="alert">${esc(A.msg)}</div>` : ''}
      <div class="afoot"><button type="button" class="k" data-addx="1">取消</button><button type="button" class="go ${A.busy ? 'busy' : ''}" data-addgo="1" ${A.busy || !pv ? 'disabled' : ''}>${A.busy ? '<i class="spn"></i>' : ''}${esc(step)}</button></div></div></div>`;
  }
  function addErr(e) {
    const c = e && e.code, why = e && e.reason;
    // b4297 ed8d14cf:403 看 code、400 看 reason
    if (e && e.status === 400 && why) return why === 'provider_unsupported' ? '這個品牌這台開不了' : why === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : why === 'persona_missing' ? '找不到這個人格' : why === 'key_missing' ? '頁面送的識別碼不對,重開這個小視窗再試' : '這版 aibd 不收這些欄位(' + why + ')';
    return c === 'open_built_in_page_only' ? '用戶資料夾有自己的頁時不能開席' : c === 'dgrs_needs_switch' ? '這台還沒開「能改程式」的開關' : c === 'provider_unsupported' ? '這個品牌這台開不了' : c === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : e && e.status === 400 && c === 'invalid_request' ? '這版 aibd 還不能從頁面開席' : errText(e);
  }
  async function doAdd() {
    const A = S.add, PV = providers(), pv = PV.find((p) => p.key === A.provider && p.open) || PV.find((p) => p.open);
    const persona = A.persona || (personas()[0] || [])[0];
    if (!pv || !persona) { A.msg = '要選品牌和人格'; A.err = true; return render(); }
    A.busy = true; A.step = 1; A.msg = ''; render();
    try {
      const r = await act('seat_open', { provider: pv.id, mode: A.mode || 'safe', persona_uid: persona, idem_key: A.key });
      const seat = r.seat || {};
      let note = '';
      const node = A.node !== undefined ? A.node : S.sel;
      if (A.role && node && seat.seat_id) {
        A.step = 2; render();
        try { await act('assign_add', { node_id: node, seat_id: seat.seat_id, role: ROLE_EN[A.role] || A.role }); } catch (e) { note = ';席開好了,但沒掛上角色(' + (e && e.code === 'assign_built_in_page_only' ? '用戶資料夾有自己的頁時不能指派' : errText(e)) + ')'; }
      }
      // 0eed7:提示寫名字(aibd 的 display / name),沒有才寫短碼
      const nm = seat.display || seat.name || seat.code || String(seat.seat_id || '').replace(/^seat_/, '').slice(0, 5) || '新席';
      S.add = null; say('開了 @' + nm + note, note ? 'err' : '');
      return tick(true);
    } catch (e) { A.msg = addErr(e); A.err = true; A.key = idem(); } // 沒開成 ⇒ 下一次是新的一席,換新的 idem_key
    A.busy = false; render();
  }

  // ---------- 整頁 ----------
  function counts() {
    if (!S.tree) return '';
    let dev = 0, unv = 0, ok = 0;
    for (const n of S.tree.values()) { if (n.status === '開發中') dev++; else if (n.status === '未驗') unv++; else if (n.status === '實查通過' || n.status === '驗過') ok++; }
    return `<span class="ib">開發中 ${dev} · 未驗 ${unv} · 驗過 ${ok}</span>`;
  }
  // 配對(519d0 09:4x:Andy 拿到碼卻沒地方輸入):沒憑證 ⇒ 輸入配對碼(內建頁,不帶 to=user);
  // 兌換後還沒確認 ⇒ 顯示短碼、請到終端機按 y;確認了才進工作台。
  // 用 fetch 送、不用表單直送:aibd 的頁都是 Referrer-Policy no-referrer,那種頁的表單 POST
  // 瀏覽器送 Origin: null,兌換會被擋;同源 fetch 帶真的 Origin。兌換頁回的是一段把憑證
  // 寫進分頁的 HTML,這裡照同一個鍵取出憑證寫進 sessionStorage,留在這一頁。
  let pairShown = '';
  // §19f:到期時間由 aibd 跟碼一起帶到頁面(#code=…&exp=<到期的 unix 秒>),頁面不自己猜;沒帶就不顯示倒數
  const CODE = { exp: 0, tries: 0, polling: false, gone: false };
  const PREFILL = (() => {
    const h = location.hash || '';
    const m = /(?:^#|&)(?:code|pc)=([A-Za-z0-9-]{4,40})(?:&|$)/.exec(h);
    const x = /(?:^#|&)exp=(\d{9,11})(?:&|$)/.exec(h); if (m && x) CODE.exp = Number(x[1]) * 1000;
    if (/(?:^#|&)(?:code|pc)=/.test(h)) { try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { location.hash = ''; } } // 碼不留在網址列與歷史
    return m ? m[1].toUpperCase() : '';
  })();
  function cdLeft() { return CODE.exp ? Math.ceil((CODE.exp - Date.now()) / 1000) : null; }
  function cdText() {
    const n = cdLeft(); if (n == null) return '';
    if (CODE.gone || (CODE.off && n != null && n <= 0)) return '這組碼過期了,請在終端機再打一次 aibd ctl web';
    return n > 0 ? '這組碼還剩 ' + n + ' 秒' : '這組碼過期了,等終端機換一組新的…';
  }
  // 每秒只改倒數那一行(不重畫整頁,免得打到一半的字被蓋掉);到期就去問 aibd 換好的新碼
  setInterval(() => {
    const e = document.getElementById('pcd'); if (!e || !CODE.exp) return;
    const n = cdLeft(); e.textContent = cdText(); e.classList.toggle('warn', n != null && n <= 20 && n > 0); e.classList.toggle('bad', n != null && n <= 0);
    if (n != null && n <= 0 && !CODE.off) codePoll();
  }, 1000);
  // 倒數與自動換碼(7b81e 878e5bc1f,DESIGN-oneweb §13):POST /api/v1/pair/next(form code=<框裡的碼>,只收 aibd 自己的 Origin)
  // 200 {code, expires_in, expires_at_ms}:同一組 = 還有效、照 expires_at_ms 倒數;不同 = ctl web 換了新碼;204 = 過期還沒換;
  // 401 pairing_failed = 這組碼沒人建過(算失敗次數,不再打)。開頁打一次拿到期時間,歸零後每 2 秒打一次
  async function codePoll(first) {
    if (CODE.polling || CODE.gone) return; CODE.polling = true;
    try {
      // 問的是 aibd 發的那一組(網址帶來的或上次換來的),不是框裡的字:框裡打錯一個字不該讓換碼停掉
      const inp = document.getElementById('pcode'); const old = String(CODE.cur || PREFILL || (inp && inp.value) || '').trim().toUpperCase();
      if (!old) return;
      const r = await fetch('/api/v1/pair/next', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ code: old }).toString(), cache: 'no-store', credentials: 'omit' });
      if (r.status === 204) return;
      if (r.status === 401 || r.status === 404 || r.status === 405) { CODE.off = true; return; } // 沒人建過這組 / 舊 aibd 沒這支:不再打
      if (!r.ok) return;
      const j = await r.json();
      if (!j || typeof j.code !== 'string' || !/^[A-Za-z0-9-]{4,40}$/.test(j.code)) return;
      const nc = j.code.toUpperCase();
      CODE.exp = Number(j.expires_at_ms) > 0 ? Number(j.expires_at_ms) : Number(j.expires_in) > 0 ? Date.now() + Number(j.expires_in) * 1000 : CODE.exp;
      if (nc !== old) {
        CODE.cur = nc; CODE.swaps = (CODE.swaps || 0) + 1;
        const now = document.getElementById('pcode');
        if (now && (now.value.trim().toUpperCase() === old || !now.value.trim())) now.value = nc;
        if (S.pair) { S.pair = Object.assign({}, S.pair, { msg: '換了一組新碼,按配對就好', code: '' }); render(); }
      }
    } catch (e) { /* 下一輪再試 */ } finally { setTimeout(() => { CODE.polling = false; }, 2000); }
  }
  async function loadPairPrefs() {
    if (S.cipAsked) return; S.cipAsked = true;
    if (PREFILL && !CODE.off) codePoll(true);
    try { const r = await fetch('/api/v1/pair/prefs', { cache: 'no-store', credentials: 'omit' }); if (r.ok) { const j = await r.json(); if (j.preview === true) { S.pvZone = true; render(); } if (typeof j.confirm_in_page === 'boolean') S.pairNew = true; if (typeof j.confirm_in_page === 'boolean' && S.cip == null) { S.cip = j.confirm_in_page; pairShown = ''; render(); } } } catch (e) { /* 沒有這支:預設打勾 */ }
  }
  function pairHtml() {
    const P = S.pair;
    if (P.view === 'code') loadPairPrefs();
    if (P.view === 'busy' || (P.view === 'wait' && P.opening)) return `<div class="pairbox">${bootBar() || `<div class="boot"><div class="bt"><i data-sty="width:40%"></i></div><span>配對好了,正在打開工作台…</span></div>`}</div>`;
    if (P.view === 'wait') {
      return `<div class="pairbox"><h2>到終端機確認</h2><p>終端機會顯示同一組短碼,一樣就在終端機按 <b>y</b>。</p>${P.short ? `<div class="short">${esc(P.short)}</div>` : ''}<p class="st">確認後這一頁自己會打開(${esc(P.left != null ? P.left + ' 秒內' : '')})。</p><button type="button" class="ib" data-repair="1">重新輸入配對碼</button></div>`;
    }
    return `<form class="pairbox" id="pairf" method="post" action="/api/v1/pair/redeem"><h2>配對這一頁</h2><p>在終端機跑 <code>aib web</code>(或 <code>aibd ctl web</code>),把它給的配對碼打在這裡。</p>${P.msg ? `<p class="bad">${esc(P.msg)}</p>` : ''}<p class="pcd" id="pcd" aria-live="polite">${esc(cdText())}</p><input name="code" id="pcode" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" required placeholder="ABCDE-FGHJK" maxlength="40" value="${esc(P.code || CODE.cur || PREFILL)}" ${P.code || CODE.cur || PREFILL ? 'autofocus' : ''}>${S.pairNew ? `<label class="cip"><input type="checkbox" id="pcip" ${S.cip === false ? '' : 'checked'}> 在這一頁確認,終端機不用按 y</label>` : ''}<button type="submit" class="go" ${PAIRING.on ? 'disabled' : ''}>${PAIRING.on ? '配對中…' : '配對'}</button></form>`;
  }
  function render() {
    if (S.pair) {
      const k = JSON.stringify([S.pair, S.boot, !!S.pvZone]);
      if (k === pairShown) return;
      pairShown = k;
      el.innerHTML = `${pvBar()}<div class="bar0"><div class="brandt">多席工作台</div><div class="grow"></div><a class="ib" href="contacts.html">👥 聯絡中心</a></div>${pairHtml()}`;
      const c = document.getElementById('pcode'); if (c) c.focus();
      return;
    }
    pairShown = '';
    const keepScroll = (() => { const t = document.getElementById('tl'); return t ? { top: t.scrollTop, atEnd: t.scrollTop + t.clientHeight >= t.scrollHeight - 30 } : null; })();
    const inp = document.getElementById('inp');
    const focus = inp && document.activeElement === inp ? { a: inp.selectionStart, b: inp.selectionEnd } : null;
    const sqe = document.getElementById('sq'), sqF = sqe && document.activeElement === sqe ? { a: sqe.selectionStart, b: sqe.selectionEnd } : null;
    const ae = document.activeElement, ansF = ae && ae.dataset && ae.dataset.ansfor ? { card: ae.dataset.ansfor, a: ae.selectionStart } : null;
    const seat = S.mode !== 'shared' && S.mode !== 'cat' ? seatById(S.mode) : null;
    if (S.mode === 'node' && !S.nt) S.mode = 'shared';
    if (S.mode === 'cat' && !S.cat) S.mode = 'shared';
    if (S.mode !== 'shared' && S.mode !== 'node' && S.mode !== 'cat' && !seat && S.seats.length) S.mode = 'shared';
    const P = present();
    const pp = `<div class="pp">${P.length ? P.map((s) => logo(s, 'big', S.mode === s.seat_id ? 'pick' : '')).join('') : '<span class="none">沒有在跑的 2.0 席</span>'}<span class="lg big you" title="你">你</span><button type="button" class="addb" data-add="1" title="加一席">＋</button></div>`;
    const nt = S.nt ? `<button type="button" class="md ${S.mode === 'node' ? 'on' : ''}" data-mode="node">💬 ${esc(String(S.nt.title || S.nt.node || '項目').slice(0, 24))}</button>` : '';
    const tabs = `<div class="mode"><button type="button" class="md ${S.mode === 'shared' ? 'on' : ''}" data-mode="shared">共同對話</button>${nt}${seat ? `<button type="button" class="md on">${logo(seat, 'sm')} @${esc(seatName(seat))}</button>` : ''}</div>`;
    el.innerHTML = `${pvBar()}${bootBar()}<div class="bar0"><div class="brandt">多席工作台</div>${counts()}<div class="grow"></div>${pvBox()}<input id="sq" class="sq" type="search" placeholder="🔍" aria-label="搜尋" autocomplete="off" maxlength="200" value="${esc(S.q)}"><a class="ib" href="contacts.html">👥 聯絡中心</a></div>
      ${S.banner ? `<div class="banner ${S.bannerKind}">${esc(S.banner)}</div>` : ''}
      <div class="upper">
        <div class="lcol"><div class="box">${treeHtml()}</div><div class="box host">${hostHtml()}</div></div>
        ${S.add ? addHtml() : ''}<div class="box ctr">${S.q.trim() ? searchWin() : `${tabs}${catBar()}${pp}${seat ? seatWin(seat) : S.mode === 'node' ? nodeWin() : S.mode === 'cat' ? catWin() : sharedWin()}`}</div>
        <div class="box">${slotsHtml()}</div>
      </div>`;
    const t = document.getElementById('tl');
    if (t) t.scrollTop = !keepScroll || keepScroll.atEnd ? t.scrollHeight : keepScroll.top;
    if (focus) { const i = document.getElementById('inp'); if (i) { i.focus(); i.setSelectionRange(focus.a, focus.b); } }
    if (sqF) { const i = document.getElementById('sq'); if (i) { i.focus(); try { i.setSelectionRange(sqF.a, sqF.b); } catch (e) { /* type=search 某些瀏覽器不給 */ } } }
    if (ansF) { const i = [...el.querySelectorAll('[data-ansfor]')].find((x) => x.dataset.ansfor === ansF.card); if (i) { i.focus(); i.setSelectionRange(ansF.a, ansF.a); } }
  }

  // ---------- 動作 ----------
  function say(msg, kind) { S.hint = msg; S.hintKind = kind || ''; }
  function errText(e) {
    if (!e) return '出錯了';
    if (e.pageMsg) return e.pageMsg;
    if (e.status === 0) return '連不上 aibd';
    if (e.code === 'user_page_safe_seats_only') return '用戶資料夾的頁只能對安全模式的席送字';
    // S89-pair-carry:換版延續來的配對只能讀、只能對安全模式的席送字(519d0 r16 裁定 3,b4297 eb0bf164)
    if (e.code === 'carried_pair_read_and_safe_only') return '這個配對是換版前延續來的,只能看和對安全模式的席送字;要開席或改指派請重新配對';
    if (e.code === 'carried_pair_safe_seats_only') return '這個配對是換版前延續來的,只能對安全模式的席送字;要對這一席說話請重新配對';
    if (e.code === 'seat_not_running') return '這一席沒在跑';
    if (e.code === 'seat_queued') return '這一席還在排隊等開,開了才收得到';
    if (e.code === 'seat_mode_takes_no_input' || e.code === 'seat_takes_no_input') return '這一席的模式不收輸入';
    if (e.code === 'seat_input_busy') return '這一席正在收上一句,等一下再送';
    if (e.code === 'seat_input_closed') return '這一席的輸入已關(可能正在停)';
    if (e.code === 'stop_built_in_page_only') return '用戶資料夾有自己的頁時不能停席;把自己的頁移走才能做';
    if (e.code === 'session_not_found') return '找不到這一場(可能已經停了)';
    return '沒送出:' + (e.code || e.message || e.status);
  }
  async function sendSeat(seatId, text) {
    const r = await act('seat_input', { seat_id: seatId, text });
    return r.__status === 202 ? '已交給它,這一回合結束就收到' : '已送出';
  }
  // 共同對話:打 @ ⇒ 對被 @ 的席各送一次(message_send 存成一筆、有 id、看得到送達)
  function targets(text) {
    const P = present();
    if (/(^|\s)@all(\s|$)/i.test(text)) return P;
    const out = [];
    for (const m of text.matchAll(/@([^\s@,,、。:]+)/g)) {
      const k = m[1].toLowerCase();
      // 先找在場的席(同名的舊席已停),席碼優先於名字
      const hit = (L) => L.find((x) => String(x.seat_id).toLowerCase() === k) || L.find((x) => String(x.name || '').toLowerCase() === k || seatName(x).toLowerCase() === k);
      const s = hit(P) || hit(S.seats);
      if (s && !out.includes(s)) out.push(s);
    }
    return out;
  }
  const idem = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
  async function sendShared(text) {
    const T = targets(text);
    if (!T.length) { say('沒有 @ 到在場的席;今晚的共同對話要 @ 誰才送得到', 'err'); return false; }
    if (!S.me) { say('讀不到你自己的聯絡人(這版 aibd 沒有聯絡中心),送不出', 'err'); return false; }
    const res = await Promise.allSettled(T.map((s) => act('message_send', { to_seat: 'seat:' + s.seat_id, body: text, thread_id: threadId(S.me, 'seat:' + s.seat_id), idem_key: idem() })));
    for (const r of res) if (r.status === 'fulfilled' && r.value && r.value.message_id) S.sentText[r.value.message_id] = text;
    const bad = res.map((r, i) => (r.status === 'rejected' ? '@' + seatName(T[i]) + ' ' + errText(r.reason) : '')).filter(Boolean);
    say(bad.length ? bad.join(';') : '已送給 ' + T.map((s) => '@' + seatName(s)).join(' '), bad.length ? 'err' : '');
    return bad.length < T.length;
  }
  async function doSend(kind) {
    const i = document.getElementById('inp');
    const text = i ? i.value.trim() : '';
    if (!text || S.sending) return;
    S.sending = true; render();
    try {
      if (kind === 'seat') { say(await sendSeat(S.mode, text)); S.drafts[S.mode] = ''; }
      else if (kind === 'node') { await sendNode(text); S.drafts.node = ''; }
      else if (await sendShared(text)) S.drafts.shared = '';
    } catch (e) { say(errText(e), 'err'); }
    S.sending = false; S.atOpen = false;
    await tick(true);
  }
  function setMode(m) { S.mode = m; S.hint = ''; S.atOpen = false; render(); tick(true); }

  function dropToken() { try { sessionStorage.removeItem(KEY); } catch (e) { /* 沒有 sessionStorage */ } }
  el.addEventListener('submit', async (e) => {
    if (e.target.id !== 'pairf') return;
    e.preventDefault();
    if (PAIRING.on) return; // 519d0 20:34(Andy 在預覽版連按兩次):第二次送出會讓 aibd 撤掉第一次的憑證
    if (token()) { S.pair = { view: 'wait', opening: true }; render(); return tick(true); } // 已經配對到一半 / 好了:不再兌換
    const code = String((document.getElementById('pcode') || {}).value || '').trim().toUpperCase();
    if (!code) return;
    PAIRING.on = true;
    const btn = e.target.querySelector('button[type="submit"]'); if (btn) btn.disabled = true;
    S.pair = { view: 'busy' }; boot();
    let msg = '';
    try {
      const cb = document.getElementById('pcip'), cip = cb ? cb.checked : true;
      S.cip = cip;
      // 0eed7 VERIFY-pkg-ec695674 H1 / 519d0 01:41 裁:頁面用的後端能力先探測。舊 aibd 不認 n / confirm_in_page,
      // 多帶就 401「碼打錯了」;只有 GET pair/prefs 答得出 confirm_in_page 才帶,讀不到只送碼(回終端機按 y)
      const f = { code }; if (S.pairNew && cip) f.confirm_in_page = '1';
      if (S.pairNew) f.n = PAGE_N; // 7b81e 9a6432347:同一頁重送帶同一個 n ⇒ aibd 不撤第一次的憑證(只在這頁記憶體)
      const r = await fetch('/api/v1/pair/redeem', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(f).toString(), credentials: 'omit', cache: 'no-store' });
      const t = await r.text();
      const k = "setItem('" + KEY + "','";
      const i = r.ok ? t.indexOf(k) : -1;
      const cred = i >= 0 ? t.slice(i + k.length).split("'")[0] : '';
      if (cred && /^[A-Za-z0-9_.~-]+$/.test(cred)) {
        sessionStorage.setItem(KEY, cred); S.paired = false; S.pair = { view: 'wait', opening: cip }; PAIRING.on = false; if (!cip) bootEnd(false);
        const pc = document.getElementById('pcode'); if (pc) pc.value = '';
        render(); return tick(true);
      }
      let j = null; try { j = JSON.parse(t); } catch (er) { /* 不是 JSON */ }
      // §19i:同一頁 30 秒內重送同碼 + 同 n ⇒ aibd 回 200 already,不重發、不撤;手上有憑證就照用
      if (r.ok && j && j.already === true && token()) { S.paired = false; S.pair = { view: 'wait', opening: true }; PAIRING.on = false; render(); return tick(true); }
      const c = j && (j.code || (j.error && j.error.code));
      // §19f 三種分開講:過期 / 用過 / 打錯(7b81e 的碼;舊 aibd 兩種都回 not_found ⇒ 退回合併那句)
      msg = c === 'pairing_rate_limited' ? '試太多次了,等一下再試'
        : c === 'pairing_code_expired' ? '這組碼過期了' + (CODE.gone ? ',請在終端機再打一次 aibd ctl web' : ',終端機會換一組新的,框裡會跟著換')
          : c === 'pairing_code_reused' ? '這組碼已經用過了,請在終端機再打一次 aibd ctl web 拿新碼'
            : c === 'pairing_code_wrong' || (c === 'pairing_failed' && r.status === 401) ? '碼打錯了,對一下終端機上的碼再送'
              : r.status === 403 ? '被 aibd 擋下(' + (c || r.status) + ')' : '配對碼不對或已過期';
      if (c === 'pairing_code_expired') { CODE.exp = Date.now() - 1; CODE.off = false; codePoll(); }
    } catch (er) { msg = '連不上 aibd'; }
    PAIRING.on = false; bootEnd(false);
    S.pair = { view: 'code', msg, code: /過期/.test(msg) ? '' : code }; render(); // 打錯 / 用過:留著打的字好改;過期:讓換來的新碼頂上
  });
  el.addEventListener('dblclick', (e) => {
    const lg = e.target.closest('[data-seat]');
    if (lg && seatById(lg.dataset.seat)) { e.preventDefault(); setMode(lg.dataset.seat); }
  });
  async function onMainClick(e) {
    const x = e.target;
    if (x.closest('[data-bootretry]')) { location.reload(); return; } // 憑證在這個分頁裡,重新載入不會再送配對碼
    if (x.closest('[data-sr]')) { S.q = ''; SR.seq++; clearTimeout(SR.timer); S.sr = null; } // 點一筆:跳過去,搜尋收起
    const md = x.closest('[data-mode]'); if (md) return setMode(md.dataset.mode);
    const ct = x.closest('[data-cat]'); if (ct) { S.cat = ct.dataset.cat; S.catThreads = null; return setMode('cat'); }
    const th = x.closest('[data-th]'); if (th) return openThread(th.dataset.th, th.dataset.tt);
    const dc = x.closest('[data-doc]'); if (dc) return openDoc(dc.dataset.dtid, dc.dataset.doc, dc.dataset.dn, dc.textContent.replace(/^📄\s*/, ''));
    const go = x.closest('[data-go]'); if (go) return setMode(go.dataset.go);
    const cd = x.closest('[data-card]');
    if (cd) { e.stopPropagation(); return openCard(cd.dataset.card); }
    const tw = x.closest('[data-tw]');
    if (tw) { const id = tw.dataset.tw; if (S.open.has(id)) S.open.delete(id); else S.open.add(id); return render(); }
    const row = x.closest('[data-node]');
    if (row) { S.sel = row.dataset.node; for (let n = S.tree && S.tree.get(S.sel); n; n = n.parent ? S.tree.get(n.parent) : null) S.open.add(n.id); openNode(S.sel); render(); const r = el.querySelector('.treebox .sel, .treebox [data-node="' + CSS.escape(S.sel) + '"]'); if (r && r.scrollIntoView) r.scrollIntoView({ block: 'nearest' }); return; }
    const at = x.closest('[data-at]');
    if (at) { const i = document.getElementById('inp'); const v = (i ? i.value : '').replace(/@$/, ''); S.drafts[S.mode === 'node' ? 'node' : 'shared'] = (v && !/\s$/.test(v) ? v + ' ' : v) + '@' + at.dataset.at + ' '; S.atOpen = false; render(); const j = document.getElementById('inp'); if (j) { j.focus(); j.setSelectionRange(j.value.length, j.value.length); } return; }
    // 共同對話裡單擊在場的席 = 加 @ 它
    const lg = x.closest('.pp [data-seat]');
    if (lg && S.mode === 'shared') { const s = seatById(lg.dataset.seat); const i = document.getElementById('inp'); if (s && i) { const v = i.value; const add = '@' + s.seat_id + ' '; if (!v.includes(add)) i.value = (v && !/\s$/.test(v) ? v + ' ' : v) + add; S.drafts.shared = i.value; } return; }
    const q = x.closest('[data-quick]');
    if (q && S.mode !== 'shared') { if (S.sending) return; S.sending = true; render(); try { say(await sendSeat(S.mode, QUICK[Number(q.dataset.quick)][1])); } catch (er) { say(errText(er), 'err'); } S.sending = false; return tick(true); }
    const sp = x.closest('[data-stop]');
    if (sp) {
      const id = sp.dataset.stop;
      // 第一下只變「再按一次停止」,3 秒內再按才送(停掉正在做事的席,不讓誤觸)
      if (S.stopArm !== id) { S.stopArm = id; render(); setTimeout(() => { if (S.stopArm === id) { S.stopArm = null; render(); } }, 3000); return; }
      S.stopArm = null; S.sending = true; render();
      try { const r = await act('session_stop', { session_id: id }); say(r.state === 'stop_unconfirmed' ? '已送停止,但行程還沒確認結束' : '已停止'); } catch (er) { say(errText(er), 'err'); }
      S.sending = false; return tick(true);
    }
    const an = x.closest('[data-answer]');
    if (an) {
      // 右欄卡與運作窗可能同一張卡各有一個框:讀按鈕旁邊那一個
      const card = an.dataset.answer, inp = (an.closest('.ans') || el).querySelector('[data-ansfor]');
      return answer(an.dataset.aseat, card, inp ? inp.value.trim() : '');
    }
    const eo = x.closest('[data-evopen]'); if (eo) return openEv(eo.dataset.evopen);
    if (x.closest('[data-dmopen]')) { S.dmOpen = !S.dmOpen; render(); if (S.dmOpen) loadDismissed(); return; }
    const ud = x.closest('[data-undm]');
    if (ud) {
      const card = ud.dataset.undm;
      try { await act('slot_restore', { card_id: card }); S.dismissed.delete(card); if (S.dmc) S.dmc.delete(card); if (S.dmN) S.dmN--; say('還原了'); }
      catch (er) {
        // 7ebca f3681b98c:404 slot_not_dismissed = 已經不在收起名單(還原過 / 回答過)⇒ 從清單拿掉就好
        const c = er && er.code;
        if (c === 'slot_not_dismissed') { S.dismissed.delete(card); if (S.dmc) S.dmc.delete(card); if (S.dmN) S.dmN--; say('這則已經不在收起的清單'); }
        else say(c === 'slot_built_in_page_only' ? '用戶資料夾有自己的頁時不能還原' : c === 'carried_pair_read_and_safe_only' ? '換版接回的配對不能還原,重新配對一次' : er && (er.status === 404 || (er.status === 400 && /unknown|type/.test(String(c || er.reason || '')))) ? '這版 aibd 還不能還原;重啟常駐後會自己回來' : errText(er), 'err');
      }
      return tick(true);
    }
    const etab = x.closest('[data-evtab]'); if (etab) { EVS.tab = etab.dataset.evtab; evSave(); return render(); }
    const ecls = x.closest('[data-evcls]'); if (ecls) { const k = ecls.dataset.evcls; if (EVS.open[k]) delete EVS.open[k]; else { EVS.open[k] = 1; EVS.page[k] = 1; } evSave(); return render(); }
    const epg = x.closest('[data-evpg]'); if (epg) { EVS.page[epg.dataset.evpg] = Math.max(1, Number(epg.dataset.to) || 1); evSave(); return render(); }
    if (x.closest('[data-evnew]')) { S.slotAsOf = ''; for (const k in EVP) delete EVP[k]; await tick(true); return; }
    // 重開接回失敗的席:act seat_restart {seat_id}(頁面憑證只能送 act);這版 aibd 沒有就說清楚
    const rs = x.closest('[data-restart]');
    if (rs && !S.sending) {
      S.sending = true; render();
      try {
        await act('seat_restart', { seat_id: rs.dataset.restart, idem_key: idem() });
        say('已重開 @' + seatName(seatById(rs.dataset.restart) || { seat_id: rs.dataset.restart }));
        const rc = rs.dataset.rcard;
        if (rc) { S.dismissed.add(rc); act('slot_dismiss', { card_id: rc }).catch(() => S.dismissed.delete(rc)); }
      }
      catch (er) {
        // a9136 532b54da 的回應碼
        const c = er && er.code, RS = { seat_not_found: '找不到這一席', seat_running: '這一席已經在跑了(或剛按過一次)', seat_archived: '這一席已封存,不能重開', restart_built_in_page_only: '用戶資料夾有自己的頁時不能重開席', dgrs_needs_switch: '這台還沒開「能改程式」的開關,這一席重開不了' };
        say(er && er.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request') ? '這版 aibd 還不能從頁面重開;請在終端機重開這一席' : c === 'restart_refused' ? '重開被擋:' + (er.message || er.reason || '接回條件不合') : RS[c] || errText(er), 'err');
      }
      finally { S.sending = false; render(); tick(true); }
      return;
    }
    const ho = x.closest('[data-handoff]');
    if (ho && !S.sending) {
      S.sending = true;
      try { await act('seat_input', { seat_id: ho.dataset.handoff, text: QUICK[1][1] }); say('已叫 @' + seatName(seatById(ho.dataset.handoff) || { seat_id: ho.dataset.handoff }) + ' 交接'); } catch (er) { say(errText(er), 'err'); }
      S.sending = false; return tick(true);
    }
    const pk = x.closest('[data-pick]');
    if (pk) return answer(pk.dataset.pseat, pk.dataset.pcard, pk.dataset.pick);
    const lt = x.closest('[data-later]');
    if (lt) { const card = lt.dataset.later; const ex = slots().find((y) => y.card === card); if (ex) (S.dmc || (S.dmc = new Map())).set(card, ex); S.dismissed.add(card); render(); try { await act('slot_dismiss', { card_id: card }); } catch (er) { S.dismissed.delete(card); say(errText(er), 'err'); } return tick(true); }
    if (x.closest('[data-add]')) {
      S.add = { provider: '', mode: 'safe', persona: '', role: '', key: idem() };
      render();
      { const A = S.add; setTimeout(() => { if (S.add === A) A.still = true; }, 400); } // 進場只播一次,之後點選重畫不再播
      // 0eed7 L:人格清單有介面就用(GET /api/v1/personas,形狀未定:{personas:[{uid, display_name}]} 或陣列);沒有就從現有席收
      const r = await soft(get('/api/v1/personas')).catch(() => null);
      const L = r && (Array.isArray(r.personas) ? r.personas : Array.isArray(r) ? r : null);
      if (L && S.add) { S.personaList = L.filter((p) => p && p.uid).map((p) => [String(p.uid), String(p.display_name || p.name || p.uid)]); render(); }
      return;
    }
    if (S.add) {
      const pu = x.closest('[data-addpu]'); if (pu) { S.add.persona = pu.dataset.addpu; return render(); }
      const ap = x.closest('[data-addp]'); if (ap) { S.add.provider = ap.dataset.addp; S.add.mode = 'safe'; S.add.key = idem(); return render(); }
      const am = x.closest('[data-addm]'); if (am) { S.add.mode = am.dataset.addm; S.add.key = idem(); return render(); }
      if (x.closest('[data-addx]') || x.classList.contains('dlgbg')) { S.add = null; return render(); }
      if (x.closest('[data-addgo]')) return doAdd();
    }
    const sd = x.closest('[data-send]'); if (sd) return doSend(sd.dataset.send);
    if (x.closest('[data-repair]')) { dropToken(); S.paired = false; S.pair = { view: 'code' }; return render(); }
  }
  el.addEventListener('click', onMainClick);
  // 觸控沒有滑過:長按 0.5 秒浮出完整標題,放開那一下不算點
  {
    let lp = null, tip = null, ate = false;
    const hide = () => { clearTimeout(lp); lp = null; if (tip) tip.hidden = true; };
    el.addEventListener('pointerdown', (e) => {
      const r = e.pointerType !== 'mouse' && e.target.closest('[data-full]'); if (!r) return;
      ate = false; clearTimeout(lp);
      lp = setTimeout(() => {
        if (!tip) { tip = document.createElement('div'); tip.className = 'evtip'; tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip); }
        tip.textContent = r.dataset.full; const b = r.getBoundingClientRect();
        tip.style.setProperty('left', Math.max(8, Math.min(b.left, innerWidth - 328)) + 'px'); tip.style.setProperty('top', (b.bottom + 6) + 'px');
        tip.hidden = false; ate = true;
      }, 500);
    });
    el.addEventListener('pointerup', hide); el.addEventListener('pointercancel', hide); el.addEventListener('scroll', hide, true);
    el.addEventListener('click', (e) => { if (ate && e.target.closest('[data-full]')) { ate = false; e.stopImmediatePropagation(); e.preventDefault(); } }, true);
  }
  el.addEventListener('change', (e) => { const f = e.target.dataset && e.target.dataset.addf; if (f && S.add) { S.add[f] = e.target.value; if (f === 'role' || f === 'node') render(); } });
  // 試用預覽版:勾 ⇒ 先彈說明、在彈窗裡同意才啟用(取消就把勾拿掉);取消勾 ⇒ 關非正式區(資料留著)
  el.addEventListener('change', async (e) => {
    if (e.target.id !== 'pvck') return;
    if (e.target.checked) { S.pvAsk = true; pvDialog(); return; }
    S.pvAsk = false;
    if (S.pv && S.pv.running) {
      try { await act('preview_stop', { idempotency_key: idem() }); say('非正式區關了,裡面的資料留著'); } catch (er) { say('沒關掉非正式區:' + errText(er), 'err'); }
      tick(true);
    } else render();
  });
  el.addEventListener('input', (e) => {
    if (e.target.dataset && e.target.dataset.addq && S.add) { S.add.pq = e.target.value; const l = document.getElementById('addpl'); if (l) l.innerHTML = personaRows(S.add); return; } // 只換清單,輸入框不重畫(不掉焦點)
    if (e.target.dataset && e.target.dataset.addf && S.add) { S.add[e.target.dataset.addf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.ansfor) { S.ansDraft[e.target.dataset.ansfor] = e.target.value; return; }
    if (e.target.id === 'sq') {
      S.q = e.target.value; clearTimeout(SR.timer);
      if (!S.q.trim()) { SR.seq++; S.sr = null; render(); return; } // 清空就回原畫面
      S.sr = null; SR.timer = setTimeout(runSearch, 300); return; // 停 300 毫秒才查
    }
    if (e.target.id !== 'inp') return;
    S.drafts[S.mode === 'shared' ? 'shared' : S.mode] = e.target.value;
    if (S.mode === 'shared' || S.mode === 'node') refreshTo(S.mode);
    if (S.mode === 'shared' || S.mode === 'node') {
      const open = /(^|\s)@$/.test(e.target.value.slice(0, e.target.selectionStart));
      if (open !== S.atOpen) { S.atOpen = open; const p = document.getElementById('atpop'); if (p) p.hidden = !open; }
    }
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && S.add && !S.add.busy) { S.add = null; render(); return; }
    if (e.target.id === 'sq' && e.key === 'Escape') { S.q = ''; SR.seq++; clearTimeout(SR.timer); S.sr = null; render(); return; }
    if (e.target.dataset && e.target.dataset.ansfor && e.key === 'Enter' && !e.isComposing) { e.preventDefault(); const b = (e.target.closest('.ans') || el).querySelector('[data-answer]'); if (b) b.click(); return; }
    if (e.target.id === 'inp' && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doSend(S.mode === 'shared' || S.mode === 'node' ? S.mode : 'seat'); }
    if (e.key === 'Enter' && e.target.matches('.lg[data-seat]')) setMode(e.target.dataset.seat); // 鍵盤:Enter = 雙擊
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-evopen]')) { e.preventDefault(); e.target.click(); }
  });

  // 已讀(內建頁可標):看得到的串裡,寄給我、還沒讀的 ⇒ message_read 到最後一則;
  // 共同對話看全部串,某一席的窗只看那一席。用戶頁 403 ⇒ 不再試(那時 aibd 不准頁標已讀)
  async function markRead() {
    if (S.readOff || !S.me || document.visibilityState !== 'visible') return;
    if (S.mode === 'node') {
      // 項目的對話:act thread_read 到畫面上最後一則(只往前);用戶頁 403 或這版沒有 thread_read
      // ⇒ 只停項目對話的已讀(共同對話的 message_read 照送,0eed7 12:02)
      if (S.ntReadOff) return;
      const last = S.posts.length ? S.posts[S.posts.length - 1] : null;
      if (!last || S.readSent.has(last.message_id) || S.nt.read_upto === last.message_id) return;
      S.readSent.add(last.message_id);
      try { const r = await act('thread_read', { thread_id: S.nt.thread_id, message_id: last.message_id }); S.nt.read_upto = r.read_upto || last.message_id; }
      catch (e) { if (e && (e.status === 403 || (e.status === 400 && e.code === 'unknown_act_type'))) { S.ntReadOff = true; return; } S.readSent.delete(last.message_id); }
      return;
    }
    if (S.mode === 'cat') return; // 分類清單只列對話,打開那段才標已讀
    const ids = S.mode === 'shared' ? Object.keys(S.threads) : [S.mode];
    for (const id of ids) {
      const ms = S.threads[id] || [];
      const un = ms.filter((m) => m.unread && m.from_seat !== S.me);
      if (!un.length) continue;
      const lastIn = un[un.length - 1];
      if (S.readSent.has(lastIn.message_id)) continue;
      S.readSent.add(lastIn.message_id);
      try { await act('message_read', { thread_id: threadId(S.me, 'seat:' + id), message_id: lastIn.message_id }); for (const m of un) m.unread = false; }
      catch (e) { if (e && (e.status === 403 || (e.status === 400 && e.code === 'unknown_act_type'))) { S.readOff = true; return; } S.readSent.delete(lastIn.message_id); }
    }
  }

  // ---------- 輪詢(規格 §3:先用 2 秒) ----------
  let busy = false, last = 0;
  // 開場進度:配對中 → 載入專案 → 載入席位與對話;一步超過 5 秒寫卡在哪、給「再試一次」(重新載入,憑證在分頁裡不用再配對)
  const BOOT = { t: 0 };
  // §19h(Andy 22:4x「未來可能會載入更多不同的東西,應該要動態載入、而不是寫死的」):
  // 每一塊要載入的東西向載入清單 LOAD 登記 {key, word(用字表鍵), label, order, need, after, run};彈窗照清單裡 need 的項目畫、分母動態;
  // 新增一塊 = 多一筆 loadReg,不改彈窗。need 的都好了彈窗就收;need:false 的背景續載。after 寫依賴,其餘平行。
  // @load-registry-begin(純資料與計算,不碰畫面;apps/web/test/load-registry.test.cjs 抽這段跑)
  const LOAD = [];
  function loadReg(x) {
    if (!x || !x.key || LOAD.some((y) => y.key === x.key)) return false;
    LOAD.push(Object.assign({ order: 50, need: true, after: [], run: null }, x));
    LOAD.sort((a, b) => a.order - b.order || (a.key < b.key ? -1 : 1));
    return true;
  }
  // 彈窗要畫的:need 的項目照順序,各自狀態(todo / cur / ok / slow);進度 = ok / 全部
  function loadPlan(list, st) {
    const rows = list.filter((x) => x.need).map((x) => ({ key: x.key, label: x.label, state: (st && st[x.key] && st[x.key].s) || 'todo', why: (st && st[x.key] && st[x.key].why) || '' }));
    const ok = rows.filter((r) => r.state === 'ok').length, cur = rows.filter((r) => r.state === 'cur' || r.state === 'slow').length;
    return { rows, n: rows.length, ok, cur, pct: rows.length ? (ok / rows.length) * 100 : 100, glide: rows.length ? Math.min(100, ((ok + 0.85 * Math.min(cur, 1)) / rows.length) * 100) : 100 };
  }
  // 跑清單:照 after 排、其餘平行;need 項回報 cur / slow / ok / fail,背景項錯了不擋;回傳「need 全部好了」
  // 載回來是空的(0 席、0 段對話、null)也算好了 —— 完成只看 run 有沒有結束,不看拿到幾筆
  function loadRun(list, onState, opt) {
    const P = {}, slowMs = opt && opt.slowMs != null ? opt.slowMs : 5000;
    const start = (x) => P[x.key] || (P[x.key] = (async () => {
      await Promise.all(x.after.map((k) => { const y = list.find((z) => z.key === k); return y ? start(y) : null; }));
      if (x.need) onState(x.key, 'cur');
      const t = x.need && slowMs ? setTimeout(() => onState(x.key, 'slow', '超過 5 秒:' + (x.why || '還沒好')), slowMs) : 0;
      try { if (x.run) await x.run(); if (x.need) onState(x.key, 'ok'); }
      catch (e) { if (x.need) onState(x.key, 'fail', e); throw e; }
      finally { clearTimeout(t); if (opt && opt.each) opt.each(x); }
    })());
    for (const x of list) if (!x.need) start(x).catch(() => { /* 背景項自己的區塊顯示讀取中 / 讀不到 */ });
    return Promise.all(list.filter((x) => x.need).map(start));
  }
  // @load-registry-end
  // 顯示名稱走用字表(GLOSSARY ui.load.*);頁面先帶 zh-TW
  const LOAD_WORD = { 'ui.load.pair': '配對', 'ui.load.tree': '載入專案', 'ui.load.core': '載入席位與事件', 'ui.load.talk': '載入對話', 'ui.load.rejoin': '接回狀態', 'ui.load.read': '已讀' };
  const lw = (k, d) => LOAD_WORD[k] || d || k;
  loadReg({ key: 'pair', label: lw('ui.load.pair'), order: 10 }); // 由配對流程標完成,不自己跑
  loadReg({ key: 'tree', label: lw('ui.load.tree'), order: 20, why: '專案項目還沒讀回來', run: () => (S.kids ? null : loadTree()) });
  loadReg({ key: 'core', label: lw('ui.load.core'), order: 30, why: '席位與事件還沒讀回來', run: () => loadCore() });
  loadReg({ key: 'talk', label: lw('ui.load.talk'), order: 40, why: '對話還沒讀回來', after: ['core'], run: async () => { if (S.mode === 'node') await loadNode(); else if (S.mode !== 'shared' && S.mode !== 'cat') await loadOut(S.mode); await loadThreads(); last = Date.now(); } });
  loadReg({ key: 'rejoin', label: lw('ui.load.rejoin'), order: 60, need: false, after: ['core'], run: () => (S.aslots ? null : loadRejoin()) });
  loadReg({ key: 'read', label: lw('ui.load.read'), order: 70, need: false, after: ['talk'], run: () => markRead() });

  // 共用進度彈窗 PROG(§19g 的樣子;項目照 loadPlan,之後開席、換版重連也用):放在 body 上就地更新,進度條平順不跳格
  const PROG = { el: null, list: [], st: {}, title: '', retry: null, done: false, t: 0 };
  const PG_CHECK = '<svg viewBox="0 0 16 16"><path d="M3.5 8.4l2.9 2.9 6.1-6.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function progStart(title, list, retry) {
    clearTimeout(PROG.t);
    if (PROG.el && PROG.title === title && !PROG.done) return;
    if (PROG.el) PROG.el.remove();
    Object.assign(PROG, { list, st: {}, title, retry: retry || null, done: false });
    const P = loadPlan(list, {});
    const d = document.createElement('div'); d.className = 'pgbg'; d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true'); d.setAttribute('aria-label', title);
    d.innerHTML = `<div class="pgd"><div class="pgh"><b>${esc(title)}</b><span class="pgn" aria-live="polite"></span></div><div class="pgbar"><i></i></div><ol class="pgs">${P.rows.map((r) => `<li class="todo" data-k="${esc(r.key)}"><span class="pgdot"></span><span class="pgt">${esc(r.label)}</span><span class="pgw"></span></li>`).join('')}</ol></div>`;
    d.addEventListener('click', (e) => { if (e.target.closest('[data-pgretry]') && PROG.retry) PROG.retry(); });
    document.body.appendChild(d); PROG.el = d; progPaint();
  }
  function progBar(pct, ms, ease) { const f = PROG.el && PROG.el.querySelector('.pgbar i'); if (!f) return; f.style.setProperty('transition', 'width ' + ms + 'ms ' + ease); f.style.setProperty('width', pct + '%'); }
  function progPaint() {
    if (!PROG.el) return;
    const P = loadPlan(PROG.list, PROG.st);
    for (const r of P.rows) {
      const li = PROG.el.querySelector(`.pgs li[data-k="${r.key}"]`); if (!li) continue;
      const cls = r.state === 'slow' ? 'cur slow' : r.state;
      if (li.className !== cls) { li.className = cls; li.querySelector('.pgdot').innerHTML = r.state === 'ok' ? PG_CHECK : ''; }
      const w = li.querySelector('.pgw'), wh = r.state === 'slow' && r.why ? `<span>${esc(r.why)}</span>${PROG.retry ? '<button type="button" class="k" data-pgretry="1">再試一次</button>' : ''}` : '';
      if (w.innerHTML !== wh) w.innerHTML = wh;
    }
    PROG.el.querySelector('.pgn').textContent = P.ok === P.n ? '完成' : '第 ' + Math.min(P.ok + 1, P.n) + ' / ' + P.n + ' 步'; // 條的起點 = 完成數 / 總數,最後一步時至少過 (n-1)/n
    const g = P.glide, seq = (PROG.seq = (PROG.seq || 0) + 1);
    progBar(Math.max(P.pct, 2), 380, 'cubic-bezier(.3,.7,.3,1)');
    if (P.ok < P.n) setTimeout(() => { if (PROG.seq === seq && !PROG.done) progBar(g, 6000, 'cubic-bezier(.08,.6,.2,1)'); }, 400);
  }
  function progSet(key, s, why) { if (!PROG.el) return; PROG.st[key] = { s, why: why || '' }; progPaint(); }
  function progDone() {
    if (!PROG.el || PROG.done) return;
    for (const r of loadPlan(PROG.list, PROG.st).rows) PROG.st[r.key] = { s: 'ok' };
    progPaint(); PROG.done = true; const d = PROG.el;
    progBar(100, 260, 'ease-out');
    PROG.t = setTimeout(() => { d.classList.add('out'); setTimeout(() => { d.remove(); if (PROG.el === d) PROG.el = null; }, 260); }, 560);
  }
  function progClose() { clearTimeout(PROG.t); if (PROG.el) PROG.el.remove(); PROG.el = null; PROG.done = true; }
  // 載入出錯的一句話:aibd 回的錯照 errText;頁面自己的程式錯(TypeError…)寫名字與訊息,截圖就看得到原因
  function loadErr(e) { return e instanceof ApiError || (e && e.status != null) ? '讀不到:' + errText(e) : '頁面出錯:' + ((e && e.name) || 'Error') + ' ' + String((e && e.message) || e).slice(0, 120); }
  const bootOpen = () => progStart('打開工作台', LOAD, () => location.reload()); // 再試一次 = 重新載入(憑證在分頁裡,不用再配對)
  // 跑載入清單:need 的平行(照 after),每項超過 5 秒寫原因;need 的都好了彈窗收;need:false 背景續載
  async function loadAll() {
    bootOpen(); progSet('pair', 'ok');
    await loadRun(LOAD.filter((x) => x.key !== 'pair'), (k, st, w) => progSet(k, st === 'fail' ? 'slow' : st, st === 'fail' ? loadErr(w) : w), { each: () => render() });
    S.boot = null; progDone();
  }
  function boot() { S.boot = { at: Date.now() }; bootOpen(); progSet('pair', 'cur'); setTimeout(() => { if (S.boot && PROG.st.pair && PROG.st.pair.s === 'cur') progSet('pair', 'slow', '超過 5 秒:aibd 還沒回配對結果'); }, 5000); render(); }
  function bootEnd(ok) { S.boot = null; clearTimeout(BOOT.t); if (ok) progDone(); else progClose(); }
  function bootBar() { return ''; } // 進度在彈窗(§19g / §19h);頁面上不另畫
  let again = false;
  async function tick(force) {
    if (busy) { if (force) again = true; return; }
    if (!force && document.hidden) return;
    busy = true;
    try {
      if (!token()) { if (!S.pair || S.pair.view !== 'code') S.pair = { view: 'code', msg: S.pair && S.pair.msg }; return; }
      if (!S.paired) {
        let st = null;
        try { st = await get('/api/v1/pair/status'); } catch (e) { if (e && (e.status === 401 || e.status === 403)) { dropToken(); S.pair = { view: 'code', msg: '這個配對已失效,請重新輸入配對碼' }; return; } throw e; }
        if (st.state === 'awaiting_confirmation') { S.pair = { view: 'wait', short: st.short_code || '', left: st.expires_in }; return; }
        if (st.state !== 'confirmed') { dropToken(); S.pair = { view: 'code', msg: '配對' + (st.state === 'expired' ? '已過期' : '已取消') + ',請重新輸入配對碼' }; return; }
        if (st.user_page) { S.banner = '這是用戶資料夾頁的配對:只能叫安全模式的席,讀不到本文'; S.bannerKind = 'err'; }
        S.paired = true; S.pair = null;
        // §19e(Andy 20:5x「直接進畫面?或著有進度條說明?」):先畫框架,資料到一塊補一塊;串流當下就連,不等 2 秒定時器
        if (!PUSH.started && !PUSH.off) { PUSH.started = true; pushLoop(); }
        S.boot = S.boot || { at: Date.now() };
        await loadAll(); // §19h:照載入清單跑(必要的平行、背景的續載),這一輪到此
        return;
      }
      await loadCore();
      if (S.boot) render();
      if (!S.aslots) await loadRejoin();
      if (S.mode === 'node') await loadNode(); else if (S.mode !== 'shared' && S.mode !== 'cat') await loadOut(S.mode);
      if (S.mode === 'shared' || S.mode === 'cat' || force || Date.now() - last > 6000) { await loadThreads(); last = Date.now(); }
      await markRead();
      if (S.boot || (PROG.el && !PROG.done)) bootEnd(true);
      if (!(S.banner && /用戶資料夾頁的配對/.test(S.banner))) S.banner = null;
    } catch (e) {
      if (e && e.status === 401) { dropToken(); S.paired = false; S.pair = { view: 'code', msg: '配對已失效,請重新輸入配對碼' }; if (S.boot) bootEnd(false); }
      else if (PROG.el && !PROG.done) {
        console.error('[ws] 載入出錯', e);
        BOOT.retry = (BOOT.retry || 0) + 1;
        if (BOOT.retry <= 6) setTimeout(() => { if (PROG.el && !PROG.done) tick(true); }, 1500 * BOOT.retry);
      }
      else if (e && e.status === 403) { S.banner = '沒有權限:' + ((e && e.code) || '?'); S.bannerKind = 'err'; }
      else if (e && e.status === 0) { S.banner = '連不上 aibd(重試中)'; S.bannerKind = 'err'; }
      else { S.banner = '讀取出錯:' + ((e && (e.code || e.message)) || '?'); S.bannerKind = 'err'; }
    } finally { busy = false; if (!S.add) render(); if (again) { again = false; setTimeout(() => tick(true), 0); } } // 加一席的小視窗開著時不重畫(選單會被關掉)
  }
  // 網址 #seat=<seat_id> 直接開那一席的運作窗(通知連結與截圖驗收用)
  const deep = /^#seat=(.+)$/.exec(location.hash);
  if (deep) S.mode = decodeURIComponent(deep[1]);
  // 事件推送(第三波 2,a9136 f0b7d7d5 GET /api/v1/events/stream):fetch 讀串流(要帶憑證,不用 EventSource),
  // 來一則就重拉一次;接著時不輪詢(a9136 12:3x,ADR 0003)。沒有這條(404 / 405 / 501)就照舊每 2 秒;
  // 斷了照 Last-Event-ID 重連,間隔 1、2、4… 秒,最多 30 秒,這段時間每 2 秒輪詢;
  // 429 too_many_streams(同時超過 16 條)也退避重連,但只 15 秒輪詢一次,免得多開分頁一起狂打
  const PUSH = { started: false, off: false, busy: false, last: '', fails: 0, t: 0 };
  function poke() { clearTimeout(PUSH.t); PUSH.t = setTimeout(() => tick(), 250); }
  async function pushLoop() {
    if (PUSH.off || !token()) { PUSH.started = false; return; }
    try {
      const h = { Accept: 'text/event-stream', Authorization: 'Bearer ' + token() };
      if (PUSH.last) h['Last-Event-ID'] = PUSH.last;
      const r = await fetch('/api/v1/events/stream', { headers: h, cache: 'no-store', credentials: 'omit' });
      if (r.status === 404 || r.status === 405 || r.status === 501) { PUSH.off = true; PUSH.started = false; return; }
      PUSH.busy = r.status === 429;
      if (!r.ok || !r.body) throw new Error(String(r.status));
      PUSH.fails = 0; S.live = true;
      const rd = r.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await rd.read();
        if (done) break;
        buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const blk = buf.slice(0, i); buf = buf.slice(i + 2);
          for (const ln of blk.split('\n')) if (ln.startsWith('id:')) PUSH.last = ln.slice(3).trim();
          if (/^data:/m.test(blk)) poke();
        }
      }
    } catch (e) { /* 斷線:下面重連 */ }
    S.live = false;
    if (PUSH.off) { PUSH.started = false; return; }
    setTimeout(pushLoop, Math.min(30000, 1000 * 2 ** Math.min(5, PUSH.fails++)));
  }
  let lastTick = 0;
  tick(true);
  setInterval(() => {
    if (S.paired && !PUSH.started && !PUSH.off) { PUSH.started = true; pushLoop(); }
    if (S.live) return;
    if (PUSH.busy && Date.now() - lastTick < 15000) return;
    lastTick = Date.now();
    tick();
  }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(true); });
})();
