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
    if (!r.ok) { const er = new ApiError(r.status, j.code, j.message); er.reason = j.reason || ''; er.body = j; throw er; }
    j.__status = r.status;
    return j;
  }
  const get = (p) => call('GET', p);
  const act = (type, f) => call('POST', '/api/v1/act', Object.assign({ type }, f));
  const soft = (p) => p.catch((e) => (e && (e.status === 404 || e.status === 403) ? null : Promise.reject(e)));

  // ---------- 狀態 ----------
  const S = {
    trust: {}, trustF: null, trustMsg: null, trustOff: false, // 受信任席(7b81e r2 補)
    hs: { list: null, f: null, del: '', busy: false, msg: null }, // S2 管理主機(fc7f9 11-23)
    rg: null, rgMsg: null, rgMsgKind: '', // S4 人格 / 大腦 / 角色 改與封存(fc7f9 11-23)
    pt: { list: null, off: '', busy: false, msg: null, f: null, del: '', seat: '', seatMsg: null }, // S3 權限模板(fc7f9 11-23)
    wait: {}, said: {}, lat: {}, doneDot: false, // 等回覆列(519d0 SPEC-wait-row-unify r0)
    cinbOpen: {}, cinbAns: null, // 送出框上方待審條(6f1cb 總帳 #7)
    inbox: null, inbPfx: '', inbUi: { col: 'unread', urg: false, seat: '', card: '', open: null, body: null }, // 總收件匣(1a93b DESIGN-web-inbox-page r2)
    moved: {}, moveAsk: null, moveCode: '', moveF: null, moveMsg: null, moveOff: false, // 搬舊席(G5 信橋,7ebca DESIGN-g5-bridge r1 §1)
    relog: null, // 重新登入 AI(a9136 DESIGN-relogin-card r2):{provider, left, id, url, mode, dcode, v, msg, bad, done}
    sb: null, sbMsg: null, // 送回倉(a9136 IFACE-web-merge r1)
    sbOk: null, pushF: null, pushMsg: null, prAsk: null, pushOff: '', prOff: '', prF: null, // 第 5 件殼(推上去 / 開 PR)
    rcp: {}, rcpListed: {}, rcpAsk: null, rcpMsg: null, rcpOff: false, rcpTimer: null, // 跑收據(7ebca 4758a4702b)
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
  // 519d0 03:17 派工:5d33a REVIEW-gitinseatrepo M1(aibd 在作者倉跑 git 會執行席寫的 filter / merge driver)修好、重審 + 0eed7 探針過之前,
  // 「自己的一份」不開新的、「合回」「改動」不出;已經開著的自己一份的席不動。修好把這行改 false
  const TREE_HOLD = true;
  // a9136 04:27:席 JSON 的 state 是大寫 ARCHIVED(store 常數);兩種都認
  const isArch = (s) => !!s && String(s.state || '').toLowerCase() === 'archived';
  const seatName = (s) => String(s.display || s.name || s.seat_id);
  // 519d0 自測 M2:真機 display = 「人格 · 品牌 · 模式[ · 席碼]」,同人格第二席起才有席碼 ⇒ 短名留人格 + 席碼,不然兩席一樣
  const seatBase = (s) => { const p = String(seatName(s)).split(' · '); return (p.length >= 4 ? p[0] + '·' + p[p.length - 1] : p[0]).trim(); };
  // aibd 沒加席碼、但在場另一席同名時(0eed7 VERIFY-tree M1)頁面自己補席碼
  const seatShort = (s) => { const b = seatBase(s), n = (x) => String(seatName(x)).split(' · ')[0].trim(); return String(seatName(s)).split(' · ').length < 4 && (S.seats || []).some((o) => o !== s && o.seat_id !== s.seat_id && !isArch(o) && n(o) === n(s)) ? b + '·' + String(s.code || String(s.seat_id).replace(/^(seat_|old_)/, '').slice(0, 5)) : b; };
  // 頭像下只放得下 6 字左右:長名字留尾段(「網頁示範-作者」→「…範-作者」),有席碼另起一行
  const seatTail = (s) => { const f = seatShort(s), i = f.lastIndexOf('·'), n = i > 0 ? f.slice(0, i) : f, c = i > 0 ? f.slice(i + 1) : '', t = n.length > 6 ? '…' + n.slice(-5) : n; return esc(t) + (c ? '<br><small>' + esc(c) + '</small>' : ''); };
  const seatById = (id) => S.seats.find((s) => s.seat_id === id);
  const lastSession = (id) => S.sessions.find((x) => x.seat_id === id) || null; // sessions 新到舊
  const nowOf = (id) => { const r = S.now.find((x) => x.seat_id === id); return r ? r.now : null; };
  // 舊式席(tmux 裡跑、用 aib dm 的):等 7ebca N-web-dmbridge 鏡像,暫定 GET seats 的列帶 origin:'legacy'(alive / now 照 2.0 席);
  // 頁面跟 2.0 席同一種畫法,只多一個「舊」角標;它沒有 aibd session,輸出看鏡像進來的信
  const isLegacy = (s) => !!s && (s.origin === 'legacy' || s.legacy === true);
  const running = (id) => { const s = seatById(id); if (isLegacy(s)) return s.alive !== false; const x = lastSession(id); return !!x && x.state === 'running'; };
  // 0eed7 17:54:停掉的 2.0 席(最後一段 session 不在跑)頁面要能重開(act seat_restart 是通的);新到舊,最多 6 席
  // 0eed7 VERIFY-realentry 9b570c2f M1(N-webonly-c15):關再開預覽後 sessions 是空的(上一場不在這一輪的清單)⇒ 沒有任何一場的 2.0 席也算停著,
  // 一樣畫灰頭像 + 重開(重開回同一席、同一個資料夾);最近有動靜的排前面,最多 6 個
  const seatAt = (s) => { const x = lastSession(s.seat_id); return String((x && (x.ended_at || x.started_at)) || s.updated_at || s.created_at || ''); };
  const stoppedSeats = () => S.seats.filter((s) => { if (isLegacy(s) || isArch(s)) return false; const x = lastSession(s.seat_id); return !x || !(x.state === 'running' || x.state === 'pending'); }).sort((a, b) => (seatAt(a) < seatAt(b) ? 1 : seatAt(a) > seatAt(b) ? -1 : 0)).slice(0, 6);
  // 排隊中:aibd 同時只跑 N 個場次,滿了新開 / 重開的席排隊、說話送不到它,直到有席停
  const queuedOf = (s) => { if (s && s.queue && typeof s.queue === 'object') { const lim = Number(s.queue.limit) || 0; if (lim) S.maxRun = lim; return { pos: Number(s.queue.position) || 0, slots: lim || S.maxRun || 0 }; } const x = s && lastSession(s.seat_id); if (!x || x.state !== 'pending') return null; const q = x.queue || (S.qv && S.qv[x.id]) || {}; return { pos: Number(q.position) || 0, slots: Number(q.slots) || S.maxRun || 0 }; }; // a9136 fe15f9d18:席 JSON queue {position, limit};舊 aibd 讀場次
  const qText = (q) => '同時最多 ' + (q.slots || '幾') + ' 席在跑,都在用' + (q.pos ? ';它排第 ' + q.pos + ' 位' : '') + '。停掉一席它就開始;排到之前說的話它收不到';
  const isStopped = (s) => !!s && !isLegacy(s) && (() => { const x = lastSession(s.seat_id); return !x || !(x.state === 'running' || x.state === 'pending'); })();
  const present = () => S.seats.filter((s) => { if (isLegacy(s)) return s.alive !== false; const x = lastSession(s.seat_id); return x && (x.state === 'running' || x.state === 'pending'); });
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
    const tip = '@' + seatName(s) + (modelText(s) ? ' · ' + modelText(s) : '') + (s.mode ? ' · ' + s.mode : '') + (seatName(s).includes(code) ? '' : ' · ' + code) + (isLegacy(s) ? ' · 舊式席(不是網頁開的)' : '');
 // 0eed7 L4:能改程式(dgrs)的席加紅角標,仍是同一個標誌
    const dg = s.mode === 'dgrs' ? 'dgm' : '';
    return `<span class="lg ${size || ''} ${b} ${ring} ${dg} ${extra || ''}" tabindex="0" data-seat="${esc(s.seat_id)}" aria-label="${esc(tip)}">${ICON[b]}${isLegacy(s) ? '<i class="oldb" aria-hidden="true">舊</i>' : ''}<span class="tip">${esc(tip)}</span></span>`;
  }

  // ---------- 讀資料 ----------
  async function loadCore() {
    const [seats, sessions, now, thermal, metrics, contacts, host, slotsR, usage, hosts] = await Promise.all([
      get('/api/v1/seats'), soft(get('/api/v1/sessions')), soft(get('/api/v1/now')),
      soft(get('/api/v1/thermal')), soft(get('/api/v1/metrics')), soft(get('/api/v1/contacts')), soft(get('/api/v1/host')),
      soft(get('/api/v1/slots')), soft(get('/api/v1/usage')), soft(get('/api/v1/hosts')),
    ]);
    // 總收件匣第 1 期(DESIGN-web-inbox-page-1a93b r2 / 7ebca §4):只給內建頁本次配對;403 / 404 ⇒ null,頂列不顯示「📥 信」
    { const ib = await soft(get('/api/v1/inbox?scope=all')).catch(() => null); S.inbox = ib && Array.isArray(ib.rows || ib.items) ? { rows: ib.rows || ib.items, counts: ib.counts || {}, next: ib.next || null, bridge: ib.bridge } : null; S.inbPfx = S.inbox && Number(S.inbox.counts.urgent) > 0 ? '(急 ' + (Number(S.inbox.counts.urgent) | 0) + ') ' : ''; if (document.getElementById('inbdlg') && document.getElementById('inbdlg').open) inboxDlg(); }
    S.hosts = hosts && Array.isArray(hosts.hosts) ? hosts.hosts : null;
    if (!S.gAt || Date.now() - S.gAt > 8000) await guideLoad(); // 新用戶指引:舊 aibd 404 ⇒ null,整塊不出
    // 7ebca 事件卡槽 + 用量(第二波);這版 aibd 沒有 ⇒ null,事件欄照頁面自己算
    if (S.version === undefined) S.version = await soft(get('/api/v1/version')).catch(() => null);
    S.aslots = slotsR && Array.isArray(slotsR.slots) ? slotsR.slots : null;
    // 7ebca §21:counts 有 ⇒ 這版 aibd 會分頁(slot / page / per / as_of);件數與 as_of 每輪即時
    S.slotCounts = slotsR && slotsR.counts && typeof slotsR.counts === 'object' ? slotsR.counts : null;
    // a9136 37bc78ceb:每張卡 origin = history | now,counts_now 只算 now(頂層或 meta 底下都收);沒有就照頁面自己的分法
    { const cn = slotsR && ((slotsR.counts_now && typeof slotsR.counts_now === 'object' && slotsR.counts_now) || (slotsR.meta && typeof slotsR.meta.counts_now === 'object' && slotsR.meta.counts_now)); S.slotCountsNow = cn || null; }
    // 5d33a bdb4fde64 L2:件數變了就換 as_of,第 2 頁以後跟第 1 頁同一個時點(不然被擠下去的舊卡兩頁都看不到)
    const cKey = S.slotCounts ? JSON.stringify(S.slotCounts) : '';
    if (S.slotCounts && slotsR.as_of && (!S.slotAsOf || cKey !== S.slotCKey)) S.slotAsOf = String(slotsR.as_of);
    S.slotCKey = cKey;
    S.usage = usage && Array.isArray(usage.usage) ? usage : null;
    S.seats = (seats && seats.seats) || [];
    netSeen(seats && seats.net_cards); // 3e53b net_cards 張數
    S.sessions = (sessions && sessions.sessions) || [];
    // 519d0 自測 r3 M3:排隊中的場次(state pending)清單不帶位置;逐個讀 GET sessions/<id> 的 queue {position, slots},記下上限
    S.qv = S.qv || {};
    for (const x of S.sessions) if (x.state === 'pending' && x.id) soft(get('/api/v1/sessions/' + encodeURIComponent(x.id))).then((r) => { const q = r && (r.queue || (r.session && r.session.queue)); if (q) { S.qv[x.id] = q; if (Number(q.slots) > 0) S.maxRun = Number(q.slots); } }).catch(() => {});
    S.now = (now && now.now) || [];
    // 1a93b d756e6d3b3(GAP §6):席用 seat_handoff 工具報的交接單 [{seat_id, path, at}];舊 aibd 沒這欄 ⇒ 空
    S.handoffs = (now && Array.isArray(now.handoffs)) ? now.handoffs : [];
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
    // a9136 02:36(0eed7 VERIFY-g13 M2):共同對話框改走串 ⇒ 那一串的貼文也併進來(不帶「哪一項」)
    const sh = S.sharedTid || findShared(L && L.threads);
    if (sh) { S.sharedTid = sh; const r = await soft(get('/api/v1/threads/' + encodeURIComponent(sh))).catch(() => null); for (const p of (r && r.posts) || []) out.push(Object.assign({ _shared: true, _tid: sh }, p)); }
    await Promise.all(ts.map(async (t) => {
      const r = await soft(get('/api/v1/threads/' + encodeURIComponent(t.thread_id))).catch(() => null);
      for (const p of (r && r.posts) || []) out.push(Object.assign({ _node: t.node, _title: t.title || t.node, _tid: t.thread_id }, p));
    }));
    S.itemTalk = out;
  }
  // §17 分類分頁(a9136 DESIGN-thread-cat §2):件數讀 aibd 的 counts,頁面不自己數、不放說明字;沒有 counts(舊 aibd)就不出分頁
  // 「校正」(NOTE-import-regroup r3 §3c,Andy 04:4x):每席交回時的自我校正 + 真人指正;分類鍵 correction 待 a9136 對
  const CATS = [['council', 'AI 遇事不決'], ['summary', '總結'], ['handoff', '交接'], ['correction', '校正'], ['drive', '自動推進'], ['shadow', '影子續跑'], ['seat_day', '每日'], ['system', '系統']]; // a9136 c9b294f9:counts 多 seat_day / system 兩鍵
  // 分頁:全部 + 五類 + 一般(沒分類的;Andy 19:4x 在頁面找不到「overnight 驗收」那段 = 沒分類、沒掛項目的對話原本沒入口)
  const CAT_TABS = [['all', '全部']].concat(CATS, [['none', '一般']]);
  function catBar() {
    const C = S.catCounts; if (!C) return '';
    return `<div class="cats">${CAT_TABS.map(([k, t]) => `<button type="button" class="ct ${S.mode === 'cat' && S.cat === k ? 'on' : ''}" data-cat="${k}" ${C[k] ? '' : 'disabled'}>${esc(t)} <i>${esc(C[k] || 0)}</i></button>`).join('')}</div>`;
  }
  function catWin() {
    const L = S.catThreads || [], name = (CAT_TABS.find((c) => c[0] === S.cat) || [])[1] || '';
    // a review pair's node (aibd assign::join_rows, R-<author code>) is not on the tree: name it, not its id (5d33a PREREAD-reviewpair 1)
    const pairName = (n) => { const m = /^R-([0-9A-Za-z]{1,5})$/.exec(String(n || '')); return m ? '審查 @' + m[1] : ''; };
    const tt = (t) => (S.tree && t.node && S.tree.get(t.node) ? S.tree.get(t.node).title : '') || pairName(t.node) || t.title || t.node || t.thread_id;
    return `${headHtml()}<div class="tl" id="tl">${L.length ? L.map((t) => `<div class="cth" data-th="${esc(t.thread_id)}" data-tt="${esc(String(tt(t)).slice(0, 60))}"><b>${esc(String(tt(t)).slice(0, 80))}</b><span class="tm">${esc(hhmm(t.created))}</span></div>`).join('') : `<div class="empty">${esc(name)}還沒有對話</div>`}</div>`;
  }
  // 一段對話直接用 thread_id 打開(分類清單裡沒有掛項目的那些,例如匯入的);讀與已讀照項目對話
  // 開一串並捲到某一則:那則在收合的日子裡就先展開那一天,再閃一下
  async function openAt(tid, mid, title) {
    await openThread(tid, title || '');
    const hit = () => mid && S.posts.find((q) => q.message_id === mid || pidOf(q) === mid);
    // 那則在更早的頁 ⇒ 往前翻(最多 30 頁 = 3,000 則)
    for (let i = 0; mid && !hit() && S.postsNext && i < 30; i++) { try { if (!(await loadOlder())) break; } catch (e) { break; } }
    const p = hit();
    if (!p) { if (mid) say('那一則不在這串讀到的範圍裡', 'err'); return; }
    { const k = stKey(p); if (k) stSel()[k] = 1; } // 那則的狀態沒勾就勾上
    S.hlPid = pidOf(p); // 標住這一則(畫面每輪重畫,閃一下會被洗掉;外框留到開別的)
    const d = dayOf(p.created); if (d) (S.openDays = S.openDays || {})[tid + '|' + d] = true;
    render();
    const el = document.querySelector(`.msg[data-pid="${CSS.escape(pidOf(p))}"]`);
    if (el) { el.scrollIntoView({ block: 'center' }); el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
  }
  async function openThread(tid, title) {
    S.hlPid = '';
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
    // a9136 759248c6e:每筆 {n, kind, root, title} + 讀得到內文的頁才有 file;用戶頁 title 是 {len, sha256};n = GET doc 的 n
    const KD = { SUMMARY: '總結', HANDOFF: '交接', REVIEW: '審查', RECEIPT: '收據', DECISION: '定稿', VERIFY: '驗收', PLAN: '計畫', SPEC: '規格', NOTE: '筆記', CHECK: '檢查' };
    const nm = (x) => (typeof x.title === 'string' && x.title) || x.file || (x.kind ? '' : '文件') + (x.title && x.title.len != null ? '(' + x.title.len + ' 字,內建頁可讀)' : '');
    const lb = (x) => (x.kind ? (KD[x.kind] || x.kind) + ' ' : '') + nm(x);
    return `<div class="dl">${L.map((x, i) => x && typeof x === 'object' ? `<a data-doc="${esc(p.message_id)}" data-dtid="${esc(tid)}" data-dn="${Number.isInteger(x.n) ? x.n : i}" data-lbl="${esc(lb(x))}" title="${esc(x.file || '')}">📄 ${x.kind ? `<span class="dk">${esc(KD[x.kind] || x.kind)}</span>` : ''}${esc(nm(x))}</a>` : '').join(' ')}</div>`;
  }
  // 事件卡帶的是檔名(7ebca ceaca0552 open {doc});先試 GET doc?file=,這版 aibd 只收 thread_id / message_id(400 unknown_field)⇒ 說還不能照檔名開,列出檔名
  function openDocFile(file) { return openDoc('', '', 0, file, file); }
  async function openDoc(tid, id, n, label, file) {
    let text = '', bad = '';
    try {
      const h = { Accept: 'text/plain' }; const t = token(); if (t) h.Authorization = 'Bearer ' + t;
      const q = file ? 'file=' + encodeURIComponent(file) : 'thread_id=' + encodeURIComponent(tid) + '&message_id=' + encodeURIComponent(id) + '&n=' + (Number(n) || 0);
      const r = await fetch('/api/v1/doc?' + q, { headers: h, cache: 'no-store', credentials: 'omit' });
      if (!r.ok && file && r.status === 400) { bad = '這版 AIB 還不能照檔名開文件:' + file; throw 0; }
      // 7ebca dfb4e1b9d:只認現在開著的帳本卡指的那份,其他 404 doc_not_found
      if (!r.ok && file && r.status === 404) { bad = '這張卡已經不在了(處理掉或檔已移走),讀不到:' + file; throw 0; }
      if (r.ok) text = await r.text(); else { let j = {}; try { j = await r.json(); } catch (e) { /* 不是 json */ } bad = r.status === 404 ? '這份文件讀不到(可能還沒有這個介面,或檔已不在)' : r.status === 403 ? (j.code === 'doc_refused' || j.code === 'doc_kind_not_listed' ? '這份文件不能打開(不在可開的文件種類裡,或是連結檔)' : '這個配對不能讀文件' + (j.code ? '(' + j.code + ')' : '')) : '讀不到(' + r.status + ')'; }
    } catch (e) { if (!bad) bad = '連不上 AIB'; }
    let d = document.getElementById('docdlg');
    if (!d) { d = document.createElement('dialog'); d.id = 'docdlg'; d.className = 'cdlg'; document.body.appendChild(d); d.addEventListener('click', (e) => { if (e.target === d || e.target.closest('[data-dclose]')) d.close(); }); }
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="kid">📄</span><span>${esc(label || '')}</span><button type="button" class="x" data-dclose="1" aria-label="關閉" autofocus>✕</button></div></div><div class="db">${bad ? `<div class="none">${esc(bad)}</div>` : `<pre class="doct">${esc(text)}</pre>`}</div><div class="df"><span></span><span>按 Esc 或點外面關閉</span></div></div>`;
    if (!d.open) d.showModal();
  }
  async function loadThreads() {
    if (!S.me) return;
    await loadItemTalk();
    driveLoad();
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

  // ---------- 新用戶指引(內建專案鏈;SPEC-onboarding-projects-ebab8-1126 r6、IFACE-guide-6770f-1126 r4b、DECISION-onboarding-bounds-3e53b r4、f163c PREREAD 1–16)----------
  // 勾全由 aibd 查(GET /api/v1/guide),頁面不存任何完成狀態;舊 aibd 404 ⇒ 整塊不出、面板全亮(頁面不自己猜)。
  // 暗格 / 遮罩 / 路線只是畫面:不送 act、不改任何鈕的 disabled 與焦點、沒有閘讀它;按下去走的都是頁面上原本那顆鈕。
  // 路線終點只從 GUIDE_GO 這張頁面寫死的表查(next_act 只當鍵),只指「開始」類入口,不指核准並啟用 / 本人確認 / 加測試機那幾顆。
  const GUIDE_STEPS = { start: ['health', 'pair', 'claude', 'allow', 'first', 'tour'], hosts: ['host_add', 'host_reach', 'host_handoff', 'site_first'] };
  const GUIDE_W = {
    'zh-TW': { start: '開始使用 AIB', hosts: '管理網站 / 主機', hostsOff: '（完成上面後開放）', builtin: '內建', later: '下一版', unsure: '判不出來', skip: '略過指引', again: '再看一次', dim: '完成「開始使用 AIB」後開放', here: '就是這一頁', pin: '沒設 Windows Hello?先設一個 PIN',
      tourLine: '現在全部開放了:可以選別的方案、人格、第二大腦、角色,也可以一次開多位夥伴。', hello: ['可能要 Windows Hello', '可能要 Touch ID 或密碼'],
      step: { health: 'AIB 已安裝並在執行', pair: '讓這個瀏覽器連上 AIB', claude: '找到 Claude 並已登入', allow: '允許 AIB 在這台開 Claude', first: '跟第一位 AI 夥伴說一句話', tour: '看看完整的選單', host_add: '加一台主機', host_reach: 'AIB 連得上那台', host_handoff: '交給一位夥伴管', site_first: '第一個網站' },
      go: { provider_install: '安裝 Claude', provider_relogin: '重新登入 Claude', session_policy_init: '打開開席設定', start_claude: '開始用 Claude', focus_chat: '去說一句', open_partner_panel: '打開「+ AI 夥伴」', add_host: '加一台主機' },
      why: { not_paired: '還沒配對', not_found: '這台找不到 Claude', npm_only: '這台的 Claude 是 npm 版,AIB 不能用', not_logged_in: 'Claude 還沒登入或已過期', login_unknown: '還不知道 Claude 登入了沒', no_policy: '還沒有開席設定', no_claude: '開席設定裡沒有 Claude', none_yet: '還沒跟夥伴說過話', seat_open: '夥伴開好了,到下面打一句話', not_yet: '還沒打開過完整選單', after_first: '先跟夥伴說一句話', none: '還沒有主機', source_error: 'AIB 這一項讀不到', unknown: '判不出來', write_failed: '沒記上,下次打開再試一次' } },
    en: { start: 'Get started with AIB', hosts: 'Manage sites / hosts', hostsOff: ' (opens after the steps above)', builtin: 'Built-in', later: 'Next version', unsure: 'Can’t tell', skip: 'Skip the guide', again: 'Show again', dim: 'Opens after “Get started with AIB”', here: 'this page', pin: 'No Windows Hello? Set up a PIN first',
      tourLine: 'Everything is open now: you can pick other plans, personas, second brains and roles, or open several partners at once.', hello: ['May ask for Windows Hello', 'May ask for Touch ID or password'],
      step: { health: 'AIB is installed and running', pair: 'Connect this browser to AIB', claude: 'Claude is found and signed in', allow: 'Let AIB open Claude on this computer', first: 'Say one thing to your first AI partner', tour: 'See the full menu', host_add: 'Add a host', host_reach: 'AIB can reach it', host_handoff: 'Hand it to a partner', site_first: 'Your first site' },
      go: { provider_install: 'Install Claude', provider_relogin: 'Sign in to Claude again', session_policy_init: 'Open seat settings', start_claude: 'Start with Claude', focus_chat: 'Say something', open_partner_panel: 'Open “+ AI partner”', add_host: 'Add a host' },
      why: { not_paired: 'Not paired yet', not_found: 'Claude isn’t on this computer', npm_only: 'This Claude is the npm version, which AIB can’t use', not_logged_in: 'Claude isn’t signed in, or the sign-in expired', login_unknown: 'Not known yet whether Claude is signed in', no_policy: 'No seat settings yet', no_claude: 'Seat settings don’t include Claude', none_yet: 'You haven’t talked to a partner yet', seat_open: 'Your partner is ready, type a line below', not_yet: 'You haven’t opened the full menu yet', after_first: 'Say one thing to a partner first', none: 'No hosts yet', source_error: 'AIB couldn’t read this', unknown: 'Can’t tell', write_failed: 'Not recorded; it will try again next time' } },
    ja: { start: 'AIB をはじめる', hosts: 'サイト / ホストの管理', hostsOff: '（上の手順が終わると使えます）', builtin: '内蔵', later: '次のバージョン', unsure: '判定できません', skip: 'ガイドをスキップ', again: 'もう一度見る', dim: '「AIB をはじめる」が終わると使えます', here: 'このページ', pin: 'Windows Hello 未設定?先に PIN を設定',
      tourLine: 'すべて使えるようになりました:ほかのプラン、人格、第二の脳、役割を選んだり、複数のパートナーを一度に開いたりできます。', hello: ['Windows Hello を求められることがあります', 'Touch ID またはパスワードを求められることがあります'],
      step: { health: 'AIB がインストールされ動いている', pair: 'このブラウザを AIB につなぐ', claude: 'Claude が見つかりサインイン済み', allow: 'このパソコンで AIB が Claude を開くのを許可', first: '最初の AI パートナーにひとこと話す', tour: 'メニュー全体を見る', host_add: 'ホストを 1 台追加', host_reach: 'AIB がそのホストにつながる', host_handoff: 'パートナーに管理を任せる', site_first: '最初のサイト' },
      go: { provider_install: 'Claude をインストール', provider_relogin: 'Claude に再サインイン', session_policy_init: '席の設定を開く', start_claude: 'Claude ではじめる', focus_chat: 'ひとこと話す', open_partner_panel: '「+ AI パートナー」を開く', add_host: 'ホストを追加' },
      why: { not_paired: 'まだペアリングしていません', not_found: 'このパソコンに Claude がありません', npm_only: 'この Claude は npm 版で、AIB では使えません', not_logged_in: 'Claude が未サインインか期限切れです', login_unknown: 'Claude のサインイン状態がまだ分かりません', no_policy: '席の設定がまだありません', no_claude: '席の設定に Claude がありません', none_yet: 'まだパートナーと話していません', seat_open: 'パートナーの準備ができました。下にひとこと入力してください', not_yet: 'まだメニュー全体を開いていません', after_first: '先にパートナーにひとこと話してください', none: 'ホストがまだありません', source_error: 'AIB がこの項目を読めません', unknown: '判定できません', write_failed: '記録できませんでした。次に開いたとき再試行します' } },
    ko: { start: 'AIB 시작하기', hosts: '사이트 / 호스트 관리', hostsOff: ' (위 단계를 마치면 열립니다)', builtin: '기본 제공', later: '다음 버전', unsure: '판단할 수 없음', skip: '안내 건너뛰기', again: '다시 보기', dim: '「AIB 시작하기」를 마치면 열립니다', here: '바로 이 페이지', pin: 'Windows Hello가 없나요? 먼저 PIN을 설정하세요',
      tourLine: '이제 모두 열렸습니다: 다른 플랜, 인격, 제2의 뇌, 역할을 고르거나 여러 파트너를 한 번에 열 수 있습니다.', hello: ['Windows Hello를 요청할 수 있음', 'Touch ID 또는 암호를 요청할 수 있음'],
      step: { health: 'AIB가 설치되어 실행 중', pair: '이 브라우저를 AIB에 연결', claude: 'Claude를 찾았고 로그인됨', allow: '이 컴퓨터에서 AIB가 Claude를 열도록 허용', first: '첫 AI 파트너에게 한마디 하기', tour: '전체 메뉴 보기', host_add: '호스트 한 대 추가', host_reach: 'AIB가 그 호스트에 연결됨', host_handoff: '파트너에게 관리 맡기기', site_first: '첫 사이트' },
      go: { provider_install: 'Claude 설치', provider_relogin: 'Claude 다시 로그인', session_policy_init: '좌석 설정 열기', start_claude: 'Claude로 시작', focus_chat: '한마디 하기', open_partner_panel: '「+ AI 파트너」 열기', add_host: '호스트 추가' },
      why: { not_paired: '아직 페어링하지 않음', not_found: '이 컴퓨터에 Claude가 없음', npm_only: '이 Claude는 npm 버전이라 AIB에서 쓸 수 없음', not_logged_in: 'Claude가 로그인되지 않았거나 만료됨', login_unknown: 'Claude 로그인 여부를 아직 모름', no_policy: '좌석 설정이 아직 없음', no_claude: '좌석 설정에 Claude가 없음', none_yet: '아직 파트너와 대화하지 않음', seat_open: '파트너가 준비됐어요. 아래에 한마디 입력하세요', not_yet: '아직 전체 메뉴를 열지 않음', after_first: '먼저 파트너에게 한마디 하세요', none: '아직 호스트가 없음', source_error: 'AIB가 이 항목을 읽지 못함', unknown: '판단할 수 없음', write_failed: '기록하지 못했습니다. 다음에 열 때 다시 시도합니다' } },
  };
  const gW = (k) => { const T = GUIDE_W[typeof askLang === 'function' ? askLang() : 'zh-TW'] || GUIDE_W['zh-TW']; return Object.prototype.hasOwnProperty.call(T, k) ? T[k] : GUIDE_W.en[k]; };
  const gOwn = (o, k) => !!o && typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k);
  const gWord = (grp, k) => { const T = gW(grp); return gOwn(T, k) ? T[k] : ''; };
  // 頁面寫死:next_act → 路線的候選終點(由前到後,取畫面上最後一個點得到的);表裡只有「開始」類入口
  const GUIDE_GO = {
    provider_install: { hello: true, to: ['[data-add="1"]'] },
    provider_relogin: { hello: true, to: ['[data-add="1"]'] },
    session_policy_init: { hello: true, to: ['[data-add="1"]'] },
    start_claude: { hello: true, to: ['[data-add="1"]', '[data-startclaude="1"]', '.adlg.ap [data-apcat="life"]', '.adlg.ap [data-apsub="life:0"]', '.adlg.ap .afoot .go'] },
    focus_chat: { to: ['#inp'], hole: '.send' }, // 客戶 11-26 18:5x:只挖 #inp 時送出鈕在洞外被蓋層吞 ⇒ 洞是整個輸入列(含送出鈕)
    open_partner_panel: { to: ['[data-add="1"]'] },
    add_host: { to: ['[data-thopen="1"]'] },
  };
  // 新手期「+ AI 夥伴」只亮這一條路:聊天陪伴(= 開始用 Claude 那組)與 AI 那一列
  const GUIDE_LIT = { apcat: 'life', apsub: 'life:0', aprc: 'life:0', aprow: 'brand' };
  const GUIDE_SKIP = 'aib.guide.skip';
  const GUIDE_UNPAIRED = { panel_lit: false, projects: [{ id: 'start', lit: true, steps: [{ id: 'health', done: true, why: 'ok' }, { id: 'pair', done: false, why: 'not_paired', next_act: 'pair' }].concat(['claude', 'allow', 'first', 'tour'].map((id) => ({ id, why: 'locked' }))) }] };
  const gProj = (G, id) => (G && Array.isArray(G.projects) ? G.projects.find((p) => p && p.id === id) : null) || null;
  const gSteps = (P, id) => (P && Array.isArray(P.steps) ? P.steps.filter((s) => s && GUIDE_STEPS[id].includes(s.id)) : []);
  // 新手期 = aibd 說面板還沒全亮(panel_lit 只由 aibd 算;頁面不看 1–5 自己推)
  const guideNew = (G) => !!(G && gProj(G, 'start') && G.panel_lit === false);
  // ok 打勾 / bad 未完成 / unsure 判不出(不勾、不擋後面) / lock 配對前鎖 / later 下一版
  function gState(s) {
    if (!s) return 'bad';
    if (s.why === 'next_version') return 'later';
    if (s.why === 'locked' || !Object.prototype.hasOwnProperty.call(s, 'done')) return 'lock';
    return s.done === true ? 'ok' : s.done === null ? 'unsure' : 'bad';
  }
  // 現在那一步:專案一第一個沒完成的;專案一走完、專案二亮著 ⇒ 專案二第一個沒完成的
  function guideCur(G) {
    for (const id of ['start', 'hosts']) {
      const P = gProj(G, id); if (!P || (id === 'hosts' && P.lit !== true)) continue;
      const s = gSteps(P, id).find((x) => gState(x) === 'bad'); if (s) return { proj: id, step: s };
    }
    return null;
  }
  const gSkipped = () => { try { if (localStorage.getItem(GUIDE_SKIP) === '1') return true; } catch (e) { /* 本機偏好讀不到 ⇒ 看這一頁的 */ } return !!S.gSkip; };
  function guideSkip(on) { try { const i = document.getElementById('inp'); if (i && !i.disabled && S.drafts) S.drafts[S.mode] = i.value; } catch (e) { /* 沒有輸入框就不存 */ } // 客戶 11-26:關指引後字不見
    S.gSkip = on !== false; try { if (S.gSkip) localStorage.setItem(GUIDE_SKIP, '1'); else localStorage.removeItem(GUIDE_SKIP); } catch (e) { /* 只存本機偏好,存不了就只記這一頁 */ } render(); }
  async function guideLoad() {
    S.gAt = Date.now();
    // f163c b0abe2097a N1:配對前沒有權杖 ⇒ aibd 回 401;回得了 401 就是 AIB 在跑 ⇒ 配對頁照實畫 1 打勾、2 配對、其他鎖著(不推其他步)
    const r = await soft(get('/api/v1/guide')).catch((e) => (e && e.status === 401 && S.pair ? GUIDE_UNPAIRED : null));
    S.guide = r && typeof r === 'object' && Array.isArray(r.projects) ? r : null;
  }
  // 真 AIB 實看 B1(fc7f9 13:58):開出 2.0 席後後端第 5 步仍回 start_claude,頂列一鍵鈕已不在 ⇒ 當 focus_chat(指輸入框,不是「+ AI 夥伴」)
  const gNa = (na) => (na === 'start_claude' && typeof present === 'function' && present().length > 0 ? 'focus_chat' : na);
  const gHello = (k) => (gOwn(GUIDE_GO, k) && GUIDE_GO[k].hello ? `<em class="ghello">${esc(gW('hello')[typeof fpWin === 'function' && fpWin() ? 0 : 1])}</em>` : '');
  function gRows(P, id, cur, out) {
    for (const s of gSteps(P, id)) {
      const st = gState(s), on = !!cur && cur.proj === id && cur.step === s, na = gNa(typeof s.next_act === 'string' ? s.next_act : '');
      const mark = on ? '▶' : st === 'ok' ? '✓' : st === 'lock' ? '🔒' : st === 'unsure' ? '?' : '·';
      const why = st === 'unsure' ? gW('unsure') : gWord('why', s.why);
      const right = on && gOwn(GUIDE_GO, na) ? `<span class="gact"><button type="button" class="k g gbtn" data-ggo="${esc(na)}">${esc(gWord('go', na))}</button>${gHello(na)}</span>`
        : st === 'later' ? `<span class="stn">${esc(gW('later'))}</span>` : '<span></span>';
      out.push(`<div class="row d1 gstep g${st}${on ? ' gcur' : ''}" data-gstep="${esc(s.id)}"><div class="nm"><span class="tw">${mark}</span><span class="tx" title="${esc(why)}">${esc(gWord('step', s.id))}${st === 'unsure' ? ' · ' + esc(gW('unsure')) : ''}</span></div>${right}<div class="seats"></div></div>${on && why ? `<div class="apn gwhy">${esc(why)}</div>` : ''}`);
    }
  }
  // 左欄「專案」卡最上面:用現有專案列的畫法(.row / .nm / .tw / .lv / progress.mini),不加新版面
  function guideHtml() {
    const G = S.guide, P1 = gProj(G, 'start'); if (!P1) return '';
    const cur = guideCur(G), out = [], O = S.gOpen || (S.gOpen = {});
    for (const id of ['start', 'hosts']) {
      const P = gProj(G, id); if (!P) continue;
      if (id === 'hosts' && P.lit !== true) { out.push(`<div class="row d0 gproj goff" data-gproj="hosts" aria-disabled="true"><div class="nm"><span class="tw">🔒</span><span class="lv a">${esc(gW('builtin'))}</span><span class="tx">${esc(gW('hosts'))}${esc(gW('hostsOff'))}</span></div><span></span><div class="seats"></div></div>`); continue; }
      const L = gSteps(P, id), tot = L.filter((s) => gState(s) !== 'later').length, okN = L.filter((s) => gState(s) === 'ok').length, all = tot > 0 && okN === tot;
      const open = gOwn(O, id) ? O[id] : !all, again = gSkipped() && cur && cur.proj === id;
      out.push(`<div class="row d0 gproj${all ? ' gok' : ''}" data-gproj="${id}"><div class="nm"><span class="tw" data-gtw="${id}">${L.length ? (open ? '▾' : '▸') : '·'}</span><span class="lv a">${esc(gW('builtin'))}</span><span class="tx">${esc(gW(id))}${all ? ` ✅ ${okN}/${tot}` : ''}</span></div>${tot ? `<progress class="mini" max="${tot}" value="${okN}" title="${okN} / ${tot}"></progress>` : '<span></span>'}<div class="seats">${again ? `<button type="button" class="k gagain" data-gagain="1">${esc(gW('again'))}</button>` : ''}</div></div>`);
      if (open) gRows(P, id, cur, out);
    }
    return `<div class="gbox">${out.join('')}</div>`;
  }
  // 配對前(沒有頁面憑證):aibd 只回 health 與 pair,其他四步 locked;配對鈕旁照實寫要先設 PIN(配對前不探 Hello)
  function guidePreHtml() {
    const P = gProj(S.guide, 'start'); if (!P) return '';
    const li = gSteps(P, 'start').map((s) => { const st = gState(s), on = s.id === 'pair' && st !== 'ok';
      return `<li class="g${st}${on ? ' gcur' : ''}"><span class="tw">${on ? '▶' : st === 'ok' ? '✓' : st === 'lock' ? '🔒' : '·'}</span>${esc(gWord('step', s.id))}${on ? ' · ' + esc(gW('here')) : ''}</li>`; }).join('');
    const win = typeof fpWin === 'function' && fpWin();
    const pin = win && typeof ocW === 'function' ? `<details class="gpin"><summary class="gpins">${esc(gW('pin'))}</summary><ol class="ocsteps">${ocW('steps').slice(0, 2).map((x) => `<li>${esc(x)}</li>`).join('')}</ol><div class="apn">${esc(ocW('nopw'))}</div></details>` : '';
    return `<div class="gpre"><b>${esc(gW('start'))}</b><ol>${li}</ol>${pin}</div>`;
  }
  // 新手期把「+ AI 夥伴」面板裡聊天陪伴以外的方案格與各列調暗加鎖:版面、順序、字都不動,只加 class / aria-disabled / 提示
  function guideDim(h) {
    if (!guideNew(S.guide)) return h;
    const t = esc(gW('dim'));
    return String(h).replace(/<button type="button" class="([^"]*)"([^>]*?) data-(apcat|apsub|aprc|aprow|apmine)="([^"]*)"/g, (m, c, mid, a, v) => (GUIDE_LIT[a] === v ? m : `<button type="button" class="${c} gdim" aria-disabled="true" title="${t}"${mid} data-${a}="${v}"`))
      .replace(/(<div class="apsrc">[^<]*<\/div>)/, `$1<div class="apn gdimh">🔒 ${t}</div>`);
  }
  const guideTourLine = () => (S.gTourLine ? `<div class="aok gtourl">${esc(gW('tourLine'))}</div>` : '');
  // 第 6 步「看看完整的選單」:打開「+ AI 夥伴」那一刻記一次(act guide_done,不帶任何值;409 guide_not_ready = 第 5 步還沒成立,不算錯)
  async function guideOnPanel() {
    const c = guideCur(S.guide);
    if (!c || c.step.id !== 'tour') { S.gTourLine = false; return; }
    if (S.gTourSent || hasAct('guide_done') !== true) return; // 6770f IFACE-guide r3b guide_done(還沒進主線);version.acts 有才送
    S.gTourSent = true; S.gMsg = '';
    try { const r = await act('guide_done'); if (r && r.ok) { S.gTourLine = true; S.gTourHere = true; } }
    catch (e) { if (!(e && e.status === 409)) { S.gTourSent = false; S.gMsg = gWord('why', 'write_failed'); } }
    await guideLoad(); render();
  }
  // 指引那顆鈕:按畫面上原本那顆入口(就是路線的終點),不另開捷徑
  function guideGo(k) {
    if (!gOwn(GUIDE_GO, k)) return false;
    if (k === 'focus_chat') { const i = document.getElementById('inp'); if (i) i.focus(); return true; }
    const sel = guidePick(GUIDE_GO[k].to.filter((s) => !/^\.adlg/.test(s)), gHas, false);
    const e = sel && document.querySelector(sel); if (e) e.click();
    return true;
  }
  // 候選終點由前到後,取最後一個「在畫面上、沒 disabled、(有對話框時)在對話框裡」的
  function guidePick(list, has, dlg) {
    let pick = null;
    for (const s of list) { const h = has(s); if (h && h.ok && !h.disabled && (!dlg || h.inDlg)) pick = s; }
    return pick;
  }
  function gHas(s) {
    const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect(), d = document.querySelector('.dlgbg');
    return { ok: r.width > 0 && r.height > 0, inDlg: !!(d && d.contains(e)), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true' };
  }
  // 本人確認 / 配對碼 / 錯誤說明 / 別的對話框出現 ⇒ 整層拆掉(不靠疊層高低);只有「+ AI 夥伴」一個對話框時才畫
  function guideBlocked(doc) {
    if (doc.querySelector('.pgbg,.anndlg,[role="alertdialog"],[role="alert"]')) return true;
    const D = Array.from(doc.querySelectorAll('.dlgbg'));
    return D.length > 1 || (D.length === 1 && !D[0].querySelector('.adlg.ap'));
  }
  // 對話框裡的關閉 / 取消(只有「+ AI 夥伴」那一個;它們不送 act)
  const guideExits = (doc) => Array.from(doc.querySelectorAll('.adlg.ap [data-addx]'));
  const gRect = (r, p) => ({ x: Math.round(r.left - p), y: Math.round(r.top - p), w: Math.round(r.width + 2 * p), h: Math.round(r.height + 2 * p) });
  // 蓋暗的一層:整個畫面一個框,挖掉的洞(現在那一步 + 終點)用 evenodd 不畫 ⇒ 洞裡點得到底下原本的鈕
  const guidePathD = (vw, vh, holes) => `M0 0H${vw}V${vh}H0Z` + holes.map((o) => `M${o.x} ${o.y}h${o.w}v${o.h}h${-o.w}Z`).join('');
  function guideLineD(a, b) {
    const ax = a.x + a.w, ay = a.y + a.h / 2, bx = b.x, by = b.y + b.h / 2, mx = (ax + bx) / 2;
    return `M${ax} ${ay}C${mx} ${ay} ${mx} ${by} ${bx} ${by}`;
  }
  // f163c 15'(f):連不上 AIB / 讀取出錯(頂端紅色那條)⇒ 遮罩與路線拆掉,不讓人卡在全暗的畫面
  const gDown = () => !!(S.banner && S.bannerKind === 'err');
  function guideRouteOn() {
    const G = S.guide; if (!G || S.pair || gSkipped() || gDown()) return null;
    const cur = guideCur(G); if (!cur) return null;
    if (!(guideNew(G) || (S.gTourHere && cur.proj === 'hosts'))) return null; // 走完、且不是專案二剛亮(這一頁剛記上「走完」)⇒ 不產生
    const na = gNa(typeof cur.step.next_act === 'string' ? cur.step.next_act : '');
    if (!gOwn(GUIDE_GO, na) || guideBlocked(document)) return null;
    const dlg = !!document.querySelector('.dlgbg');
    const sel = guidePick(GUIDE_GO[na].to, gHas, dlg); if (!sel) return null;
    const to = document.querySelector(sel), hz = GUIDE_GO[na].hole && to && to.closest(GUIDE_GO[na].hole);
    return { sel, to, ring: hz || to, from: dlg ? null : document.querySelector('[data-ggo]') };
  }
  let gRaf = 0;
  function guidePaintSoon() { if (gRaf || typeof requestAnimationFrame !== 'function') return; gRaf = requestAnimationFrame(() => { gRaf = 0; guidePaint(); }); }
  function guidePaint() {
    const old = document.getElementById('gtour'), R = guideRouteOn();
    if (!R || !R.to) { if (old) old.remove(); return; }
    const tr = (R.ring || R.to).getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight;
    if ((tr.bottom < 0 || tr.top > vh || tr.right < 0 || tr.left > vw) && S.gScr !== R.sel) { S.gScr = R.sel; R.to.scrollIntoView({ block: 'center', inline: 'nearest' }); return guidePaintSoon(); }
    const b = gRect(tr, 6), holes = [b];
    const fr = R.from && R.from.getBoundingClientRect(), a = fr && fr.width ? gRect(fr, 4) : null;
    if (a) holes.push(a);
    for (const x of guideExits(document)) { const r = x.getBoundingClientRect(); if (r.width) holes.push(gRect(r, 3)); } // f163c N1:面板的 ✕ / 取消也點得到
    const d = old || document.createElement('div');
    d.id = 'gtour'; d.className = 'gtour';
    d.innerHTML = `<svg class="gsvg" viewBox="0 0 ${vw} ${vh}" width="${vw}" height="${vh}" aria-hidden="true"><path class="gdimp" fill-rule="evenodd" d="${guidePathD(vw, vh, holes)}"/>${a ? `<path class="gline" d="${guideLineD(a, b)}"/>` : ''}<rect class="gring" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="8"/></svg><button type="button" class="k gskip" data-gskip="1">${esc(gW('skip'))} · Esc</button>`;
    if (!old) {
      d.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); if (e.target.closest('[data-gskip]')) return guideSkip(true); if (guideBlocked(document)) d.remove(); }); // 只吞不轉送;3e53b / f163c N2:確認類已在畫面上 ⇒ 拆層
      document.body.appendChild(d);
    }
  }
  if (typeof window !== 'undefined' && typeof document !== 'undefined' && document.addEventListener) {
    window.addEventListener('resize', guidePaintSoon, { passive: true });
    document.addEventListener('scroll', guidePaintSoon, { passive: true, capture: true });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && document.getElementById('gtour') && !(typeof imeBusy === 'function' && imeBusy(e))) guideSkip(true); }); // 客戶 11-26:組字中 Esc 是取消組字,不關指引
    // f163c / 3e53b N2:本人確認卡等不經 render 冒出來也要重判;路線層自己的變動不算
    if (typeof MutationObserver === 'function') new MutationObserver((ms) => { if (document.getElementById('gtour') && ms.some((m) => !(m.target.closest && m.target.closest('#gtour')))) guidePaintSoon(); }).observe(document.body || document.documentElement, { childList: true, subtree: true });
  }
  // ---------- 新用戶指引 end ----------

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
        ${right}<div class="seats">${upTag(n.id, 'sm')}${upBtn(n.id, 'sm')}${seats}</div></div>`);
      if (kids && isOpen) treeRows(n.id, depth + 1, out);
    }
  }
  function treeHtml() {
    if (!S.kids) return '<div class="bh"><b>專案</b></div>' + guideHtml();
    const out = [];
    treeRows('', 0, out);
    return `<div class="bh"><b>專案</b><small>唯讀</small></div>${guideHtml()}<div class="treebox">${out.join('')}</div>`;
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
  // §20b(Andy 23:2x「預覽版 可能會有非常多種的開發,所以不能寫死」):預覽不只一個 ⇒ 預覽清單。
  // aibd(a9136)GET /api/v1/previews → {previews:[{id, name, what, card, by, head, version, sha256, url, stage, running, not_usable:[…]}]};
  // act preview_start {preview_id, version, sha256, consent, idempotency_key} / preview_stop {preview_id, idempotency_key}。
  // 沒有這條(能看階段)⇒ 舊的單一 GET preview 當一筆;都沒有 ⇒ 畫示意清單(標「示意」,開關只在這頁動,不送 aibd)
  const PV_STAGE = ['能看', '已定版', '能用中', '已進正式'];
  const PV_STAGE_KEY = { see: 0, look: 0, viewable: 0, fixed: 1, frozen: 1, usable: 2, building: 2, shipped: 3, live: 3 };
  const pvStage = (x) => { const v = x && (x.station != null ? x.station : x.stage); if (typeof v === 'number') return Math.max(0, Math.min(3, v)); const k = PV_STAGE.indexOf(v); return k >= 0 ? k : PV_STAGE_KEY[String(v || '').toLowerCase()] || 0; };
  // 519d0 23:5x:只讀 a9136 79a88238 的 GET previews(清單空時它回兩筆 sample:true 假列);舊的 GET preview 不用了。讀不到 ⇒ 不出鈕
  async function loadPreviews() {
    const r = await soft(get('/api/v1/previews')).catch(() => null);
    if (!r || !Array.isArray(r.previews)) { S.pvs = null; S.pvsOff = []; S.pvsDemo = false; return; }
    if (r.in_preview === true) S.pvZone = true;
    if (r.update && r.update.nag_secs > 0) S.pvNagSecs = Number(r.update.nag_secs);
    if (r.update && r.update.idle_secs > 0) S.pvIdleSecs = Number(r.update.idle_secs);
    const L = r.previews.filter((x) => x && x.id);
    S.pvs = L.filter((x) => !x.retired).map((x) => Object.assign({}, x, x.sample ? { demo: true, nostart: true } : {}));
    S.pvsOff = L.filter((x) => x.retired); S.pvsDemo = L.length > 0 && L.every((x) => x.sample);
  }
  // 非正式區頂上:帶這一個預覽的名稱(aibd health / version 的 preview_name;沒有就只寫「預覽」)
  // §23-7 有新版:不打斷正在看的人,頂上一行「有新版,按一下更新」(aibd health preview_update:{version} 暫定)
  // §23b(Andy 23:4x):有人在看只出提示、不強制;提示出現超過 nag_secs(預設 30 分,aibd 可調)沒按 ⇒ 變醒目並標落後幾版
  // §23c(Andy 00:2x「對於 預覽版 修要好自動換」):修正版由 aibd 自動換上,頁面看到預覽版本變了 ⇒ 重新載入(憑證在分頁裡、席與對話由 aibd 保留),
  // 載入後頂上短暫顯示「已更新到新版 · 改了什麼」;「有新版,按一下更新」只留給新功能第一次上來(preview_update.kind = 'feature')。
  // 暫定形狀(a9136):health.preview_version、health.preview_summary(這版改了什麼,一行)
  const PVJ_MS = 8000;
  function pvSeeVersion(j) {
    const v = typeof j.preview_version === 'string' ? j.preview_version.slice(0, 40) : ''; if (!v) return;
    let prev = ''; try { prev = sessionStorage.getItem('aib.pv.ver') || ''; sessionStorage.setItem('aib.pv.ver', v); } catch (e) { /* 私密視窗:不提示也不重載 */ return; }
    const sum = typeof j.preview_summary === 'string' ? j.preview_summary.slice(0, 120) : '';
    if (S.pvVer && S.pvVer !== v) { try { sessionStorage.setItem('aib.pv.just', JSON.stringify({ v, sum, from: S.pvVer })); } catch (e) { /* 照樣重載 */ } location.reload(); return; }
    S.pvVer = v;
    if (prev && prev !== v) { S.pvJust = { v, sum, until: Date.now() + PVJ_MS }; pvUpdRec(j, prev, v, sum); }
    else { try { const k = JSON.parse(sessionStorage.getItem('aib.pv.just') || 'null'); if (k && k.v === v) { S.pvJust = { v, sum: k.sum || sum, until: Date.now() + PVJ_MS }; pvUpdRec(j, k.from || '', v, k.sum || sum); } } catch (e) { /* 沒有就算了 */ } }
    try { sessionStorage.removeItem('aib.pv.just'); } catch (e) { /* 同上 */ }
    if (S.pvJust) setTimeout(() => { S.pvJust = null; render(); }, PVJ_MS + 50);
  }
  // §23d(Andy 00:3x「修好自動換 在 跑登入進度條 時,能顯示 這次 自動換 是換什麼?誰換?的記錄嗎?」):
  // 暫定形狀(a9136):health.preview_history = [{from, to, at, by, how:'auto'|'manual', changes:[{text, seat, card}]}](新的在前)。
  // 只取上次進來之後的幾筆;沒有就用 preview_summary 拼一筆,缺的欄標「示意」
  function pvUpdRec(j, prev, v, sum) {
    // a9136 3dccf158(現行):health.preview_history = [{from, to, at, by:'auto'|'Andy', how, first, summary, changes:[{text, seat, card}]}];舊名 54e4e401:health.preview_swaps = [{at, by, how:'auto'|'manual'|'start', from, to, summary, changes:[{text, author, card}]}](新的在前,最多 10)
    const H = (Array.isArray(j.preview_history) ? j.preview_history : Array.isArray(j.preview_swaps) ? j.preview_swaps : []).filter((x) => x && x.to).map(swapRec);
    let seen = ''; try { seen = localStorage.getItem('aib.pv.seenAt') || ''; } catch (e) { /* 存不了就只看版本 */ }
    let L = [];
    if (H.length) { const k = H.findIndex((x) => x.to === prev || (seen && x.at && x.at <= seen)); L = (k >= 0 ? H.slice(0, k) : H).slice(0, 20); }
    try { if (H[0] && H[0].at) localStorage.setItem('aib.pv.seenAt', H[0].at); } catch (e) { /* 同上 */ }
    if (!L.length) L = [{ from: prev, to: v, at: new Date().toISOString(), by: '', how: 'auto', changes: sum ? [{ text: sum }] : [], demo: true }];
    S.pvRecs = L; progUpd();
  }
  // 換版紀錄的一筆:by 'auto' = 自動換、'page:<6 碼>' = 哪個配對頁按的;changes 的作者欄叫 author
  function swapRec(r) {
    const by = String(r.by || ''), how = r.first ? 'start' : r.how || (by === 'auto' ? 'auto' : 'manual');
    const who = by === 'auto' ? '' : by.startsWith('page:') ? '配對頁 ' + by.slice(5) : by;
    const ch = Array.isArray(r.changes) && r.changes.length ? r.changes.map((c) => ({ text: c.text, seat: c.seat || c.author, card: c.card })) : r.summary ? [{ text: r.summary }] : [];
    return Object.assign({}, r, { by: who, how, changes: ch, auto: by === 'auto' });
  }
  function updRecHtml(L, max) {
    const shown = L.slice(0, max), more = L.length - shown.length;
    const one = (r) => {
      const who = r.auto ? '<span>AIB 自動換</span>' : r.how === 'start' ? '<span>開預覽</span>' : r.by ? (seatById(r.by) ? logo(seatById(r.by), 'sm') + ' ' : '') + '<span>' + esc(r.by) + '</span>' : '<span class="na">誰換的還沒記</span>';
      const ch = (r.changes || []).map((c) => `<li><span>${esc(c.text || '')}</span>${c.seat ? `<span class="pvby">@${esc(c.seat)}</span>` : ''}${c.card ? `<span class="ecd">${esc(c.card)}</span>` : ''}</li>`).join('');
      return `<div class="pgur"><div class="pgu1">${r.from ? `<code>${esc(String(r.from).slice(0, 9))}</code> → ` : ''}<code>${esc(String(r.to).slice(0, 9))}</code><span class="pgw2">${esc(r.at ? evTime(r.at) : '')}</span><span class="upt ${r.how === 'manual' ? 'u5' : r.how === 'start' ? 'u1' : 'u2'} sm">${r.how === 'manual' ? '手動' : r.how === 'start' ? '開啟' : '自動'}</span>${who}${r.demo ? '<span class="pvdemo" title="AIB 還沒給這次更新的紀錄;版本與改了什麼是真的,其他欄還沒有">示意</span>' : ''}</div>${r.state === 'conflict' || r.state === 'build_failed' ? `<div class="pgerr">${r.state === 'conflict' ? '合不進去' : '編不過'}${r.error ? ':' + esc(typeof r.error === 'string' ? r.error : [r.error.node, r.error.head && String(r.error.head).slice(0, 9), r.error.file, r.error.line || r.error.message].filter(Boolean).join(' · ')) : ''}</div>` : ch ? `<ul class="pguc">${ch}</ul>` : '<div class="na">這次改了什麼還沒寫</div>'}</div>`;
    };
    return shown.map(one).join('') + (more > 0 ? `<button type="button" class="more pgmore" data-pgmore="1">再看 ${more} 次 ▾</button>` : '');
  }
  // 載入彈窗多一塊「這次更新」;有這塊時彈窗至少停 2 秒,點一下就不自己收(按「知道了」才收)
  const PG_UPD_MIN = 2200;
  function progUpd() {
    const L = S.pvRecs; if (!L || !L.length || !PROG.el) return;
    let b = PROG.el.querySelector('.pgu');
    if (!b) { b = document.createElement('div'); b.className = 'pgu'; PROG.el.querySelector('.pgd').appendChild(b); PROG.updAt = Date.now(); }
    b.innerHTML = `<div class="pguh"><b>這次更新</b><span>${L.length > 1 ? '自上次進來換了 ' + L.length + ' 次' : ''}</span><button type="button" class="k sm2" data-pgok="1">${PROG.pinned ? '知道了' : '點住不收'}</button></div>${updRecHtml(L, PROG.updAll ? 20 : 3)}`;
  }
  // 開著的預覽頁每分鐘看一次預覽版本(背景讀取,不算真人操作)
  setInterval(async () => { if (!inPreview()) return; try { const r = await fetch('/api/v1/health', { cache: 'no-store', credentials: 'omit' }); if (r.ok) pvSeeVersion(await r.json()); } catch (e) { /* 下一分鐘再看 */ } }, 60000);
  const pvNag = () => !!(S.pvNew && S.pvNewAt && Date.now() - S.pvNewAt >= (S.pvNagSecs || 1800) * 1000);
  const pvBar = () => {
    if (!inPreview()) return '';
    const nag = pvNag(), bh = S.pvBehind > 0 ? `落後 ${S.pvBehind} 版 · ` : '';
    const just = S.pvJust && Date.now() < S.pvJust.until ? `<span class="pvjust" role="status">已更新到新版 ${esc(S.pvJust.v.slice(0, 9))}${S.pvJust.sum ? ' · ' + esc(S.pvJust.sum) : ''}</span>` : '';
    return `<div class="pvbar ${nag ? 'nag' : ''}" role="status">${just}預覽${S.pvName ? ' · ' + esc(S.pvName) : ''} · 非正式區${S.pvNew ? `<button type="button" class="pvupd ${nag ? 'nag' : ''}" data-pvupd="1" title="${esc(S.pvNew)}">${nag ? esc(bh) + '有新版,按一下更新' : '有新版,按一下更新'}</button>` : ''}</div>`;
  };
  // c7(7b81e 11-20):預覽頁不送 preview_active。以前這裡在預覽裡每分鐘(有操作就)送一次,預覽自己的 aibd 一律拒
  // (preview_in_preview,403),Andy 的預覽紀錄每分鐘一筆被拒;這個訊號只有母體的清單用得上,改由 aibd 那邊接(a9136,N-pv-agree-drive 乙)。
  setInterval(() => { if (S.pvNew && pvNag() !== !!document.querySelector('.pvbar.nag')) render(); }, 30000);
  (async () => { try { const r = await fetch('/api/v1/health', { cache: 'no-store', credentials: 'omit' }); if (r.ok) { const j = await r.json(); if (j && j.preview === true) { S.pvZone = true; if (typeof j.preview_name === 'string') S.pvName = j.preview_name.slice(0, 40); if (typeof j.preview_id === 'string') S.pvId = j.preview_id.slice(0, 64); pvSeeVersion(j); if (j.preview_update && j.preview_update.kind !== 'fix') { const U = j.preview_update; S.pvNew = String(U.version || '新版').slice(0, 40); S.pvBehind = Number(U.behind) || 0; if (U.nag_secs > 0) S.pvNagSecs = Number(U.nag_secs); S.pvNewAt = U.since ? Date.parse(U.since) || Date.now() : Date.now(); } render(); } } } catch (e) { /* 舊 AIB 沒這欄 */ } })();
  const inPreview = () => !!S.pvZone || !!(S.version && (S.version.preview === true || S.version.channel === 'preview'));
  // 519d0 11-19 12:09(Andy 今天按了三次指紋):預覽換過版後,這一頁的配對變成延續的(只能看);
  // 在預覽裡不必重新配對,回母體按「打開」拿一張新開門票就好,不用指紋
  const PV_CARRIED = '這個預覽剛換過版,這一頁的配對只能看;回母體那一頁再按一次「打開」就好(不用打碼、不用指紋)';
  // 頂列一顆鈕「預覽 N」(有在跑的帶綠點),點開預覽清單
  function pvBox() {
    if (inPreview() || !S.pvs) return '';
    const on = S.pvs.filter((x) => x.running).length;
    return `<button type="button" class="pvbtn" data-pvlist="1" title="預覽清單">${on ? '<i class="pvon"></i>' : ''}預覽 <b>${S.pvs.filter((x) => !x.sample).length}</b></button>`;
  }
  function pvRow(x) {
    const st = pvStage(x), nu = Array.isArray(x.not_usable) ? x.not_usable : [];
    const byPage = /^page:/.test(String(x.by || ''));
    const who = x.by && !byPage ? seatById(x.by) || S.seats.find((s) => String(s.seat_id).replace(/^seat_/, '').startsWith(x.by)) : null;
    const step = PV_STAGE.map((t, k) => `<span class="pvst ${k < st ? 'dn' : k === st ? 'nw' : ''}"><i></i>${esc(t)}</span>`).join('');
    const ver = x.head || x.version || '';
    return `<div class="pvr ${x.running ? 'run' : ''}" data-pvid="${esc(x.id)}">
      <div class="pvr1"><b class="pvn" title="${esc(x.name || '')}">${esc(x.name || '預覽')}</b>${x.card ? `<span class="ecd">${esc(x.card)}</span>` : ''}
        ${x.sample ? '<span class="pvdemo" title="清單是空的時 AIB 給的示意列,不能起">示意</span>' : ''}<label class="pvsw ${x.nostart ? 'off' : ''}" title="${x.nostart ? '示意列不能起' : x.running ? '關掉這一個預覽' : '開這一個預覽(會先彈說明)'}"><input type="checkbox" class="pvswi" data-pvsw="${esc(x.id)}" ${x.running ? 'checked' : ''} ${x.nostart ? 'disabled' : ''}><span></span></label></div>
      ${x.what ? `<div class="pvw">${esc(x.what)}</div>` : ''}
      <div class="pvm">${who ? logo(who, 'sm') : byPage ? '<span class="pvby" title="' + esc(x.by) + '">這頁新增</span>' : x.by ? `<span class="pvby">@${esc(x.by)}</span>` : ''}${ver ? `<code title="這個預覽用的頭碼 / 版本">${esc(String(ver).slice(0, 12))}</code>` : ''}${x.newer ? `<span class="upt u4 sm">有新版未上${x.behind > 0 ? ' · 落後 ' + Number(x.behind) + ' 版' : ''}</span>` : ''}${x.newer && typeof x.seats_running === 'number' && x.seats_running > 0 ? `<span class="pvby" title="預覽裡沒有席在跑時,母體會自動換上新版(a9136 seats_running)">${Number(x.seats_running)} 席在跑,停了就自動換上</span>` : ''}${x.last_active_at ? `<span class="pvby" title="最後一次有人在這個預覽裡點 / 打字 / 捲動">有人看 ${esc(when(x.last_active_at))}</span>` : ''}${x.running && x.url ? `<a class="k" href="${esc(x.url)}" target="_blank" rel="noopener" data-pvopen="${esc(x.id)}">打開</a>` : ''}</div>
      <div class="pvsts" ${x.fixed && x.fixed.words ? `title="定版:${esc(String(x.fixed.words))}"` : ''}>${step}</div>
      ${x.fixed && x.fixed.words ? `<div class="pvfx">定版:「${esc(String(x.fixed.words).slice(0, 80))}」${x.fixed.head ? ' · ' + esc(String(x.fixed.head).slice(0, 9)) : ''}</div>` : ''}
      ${x.sample ? '' : `<div class="pvops">${st === 0 ? `<button type="button" class="k" data-pvfix="${esc(x.id)}">記定版</button><span class="pvfxh">記下你說「這一版可以」的那句話;現在只是記下來,還不會自動往下做</span>` : ''}<button type="button" class="k" data-pvoff="${esc(x.id)}">下架</button></div>`}
      ${S.pvStopAsk === x.id ? pvStopWarn(x) : ''}
      ${S.pvFixing === x.id ? `<div class="pvform"><input class="apq" data-pvfixw="1" placeholder="Andy 的原話(例:定)" maxlength="200"><button type="button" class="k g" data-pvfixgo="${esc(x.id)}">寫入定版</button></div>` : ''}
      ${x.card && S.tree && S.tree.get(x.card) ? `<details class="pvtr" data-pvtr="${esc(x.card)}"><summary class="pvs2">相依的樹</summary><div class="pvtrb">${upTreeHtml(upTreeMock(x.card))}</div></details>` : ''}
      ${(() => { const HS = Array.isArray(x.history) && x.history.length ? x.history : x.swaps; const V = Array.isArray(HS) && HS.length ? HS.map(swapRec) : Array.isArray(x.versions) ? x.versions.filter((r) => r && (r.to || r.version)).map((r) => swapRec(Object.assign({ to: r.version || r.to }, r))) : []; return V.length ? `<details class="pvtr pvvh"><summary class="pvs2">版本紀錄 ${V.length} 次</summary><div class="pvtrb">${updRecHtml(V, 8)}</div></details>` : ''; })()}
      ${nu.length ? `<details class="pvnu"><summary class="pvs2">可看、還不能用 ${nu.length} 項</summary><ul>${nu.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></details>` : ''}
    </div>`;
  }
  // 519d0 ORDER-autodrive 清單 5(c15):關預覽前先講會停幾席、之後怎麼接回;席數 aibd 有給(seats_running / running_seats)才寫數字,沒有就講「開著的席」
  function pvStopWarn(x) {
    const n = [x.seats_running, x.running_seats, x.sessions_running].find((v) => typeof v === 'number' && v >= 0);
    const what = n === 0 ? '裡面現在沒有開著的席' : n > 0 ? `裡面開著的 ${n} 席會全部停下` : '裡面開著的席會全部停下';
    return `<div class="pvwarn" role="alert"><b>要關「${esc(x.name || '預覽')}」嗎?</b>${what}。資料夾、對話、串都留著;下次打開預覽後,在右欄「停了」那一席按「重開」就接回原本的資料夾和對話。<div class="pvwb"><button type="button" class="k" data-pvstopno="1">先不要</button><button type="button" class="k n" data-pvstopgo="${esc(x.id)}">確定關掉</button></div></div>`;
  }
  // 送出框上方的待審條(519d0 總帳 #7,MOCK-composer-inbox-519d0-1121;6f1cb 11-21):只算跟眼前這一席有關的信(寄給它或它寄的),沒結的才算;
  // 急紅(aibd level)/ 等你決定橘(held_human)/ 一般淡金 / 被擋灰(blocked);平常一行件數,點開最多 5 列,其餘到待審區看;沒信整條收起。
  // 資料跟待審區、右欄同一個來源(S.inbox = GET /api/v1/inbox);這台 aibd 還沒有那條 ⇒ 用下面的示意列,條上標「示意」,示意列的答案不送出。
  const CINB_RANK = { urg: 0, ask: 1, say: 2, blk: 3 }, CINB_W = { urg: '急', ask: '等你決定', say: '一般', blk: '被擋' };
  const cinbKind = (m) => (m.blocked ? 'blk' : m.level === 'urgent' ? 'urg' : m.held_human ? 'ask' : 'say');
  const cinbId = (v) => String(v || '').replace(/^seat:(legacy:)?/, '');
  function cinbDemo(s) {
    const me = 'seat:' + s.seat_id;
    return [
      { id: 'demo-1', demo: true, from_seat: me, to_seat: 'human:andy', held_human: true, age_min: 12, first_line: '示意:要先做哪一件?A 還是 B' },
      { id: 'demo-2', demo: true, from_seat: 'seat:519d0', to_seat: me, level: 'urgent', level_by: '519d0', delivered_at: '1', age_min: 3, first_line: '示意:編譯紅擋換預覽,最優先' },
      { id: 'demo-3', demo: true, from_seat: 'seat:f1fae', to_seat: me, delivered_at: '1', read_at: '1', age_min: 6, first_line: '示意:第三顆頭凍結' },
      { id: 'demo-4', demo: true, from_seat: me, to_seat: 'seat:6f1cb', blocked: { reason: 'legacy_off' }, age_min: 9, first_line: '示意:這封沒送出' },
    ];
  }
  function cinbRows(s) {
    const demo = !S.inbox, R = demo ? cinbDemo(s) : S.inbox.rows || [];
    const L = R.filter((m) => !m.done_at && !m.superseded_by && (cinbId(m.to_seat) === s.seat_id || cinbId(m.from_seat) === s.seat_id));
    L.sort((a, b) => CINB_RANK[cinbKind(a)] - CINB_RANK[cinbKind(b)] || (Number(b.age_min) || 0) - (Number(a.age_min) || 0));
    return { demo, L };
  }
  function cinbStrip(s) {
    const { demo, L } = cinbRows(s);
    if (!L.length) return ''; // 沒信整條收起
    const n = { urg: 0, ask: 0, say: 0, blk: 0 }; for (const m of L) n[cinbKind(m)]++;
    const open = !!(S.cinbOpen && S.cinbOpen[s.seat_id]);
    const who = (v) => (/^human:/.test(String(v || '')) ? '你' : '@' + seatName(seatById(cinbId(v)) || { seat_id: cinbId(v) || '?' }));
    const st = (m) => (m.blocked ? '被擋' : m.held_human ? '等你 ' + inbAge(m) : m.read_at ? '讀了沒結 ' + inbAge(m) : m.delivered_at ? '送到 · 未讀 ' + inbAge(m) : '還沒送到 ' + inbAge(m));
    const btn = (m) => { const k = cinbKind(m); return k === 'ask' ? `<button type="button" class="go" data-cinbans="${esc(m.id)}">在下面回答</button>` : k === 'blk' ? `<button type="button" class="k" data-cinbre="${esc(m.id)}">改寄</button>` : `<button type="button" class="k" data-cinbread="${esc(m.id)}">看全文</button>`; };
    const pills = ['urg', 'ask', 'say', 'blk'].filter((k) => n[k]).map((k) => `<span class="cpill c${k}">${CINB_W[k]} ${n[k]}</span>`).join('');
    const rows = L.slice(0, 5).map((m) => `<div class="cit"><span class="ck c${cinbKind(m)}"></span><span class="ct"><b>${esc(who(m.from_seat))} → ${esc(who(m.to_seat))}</b> ${esc(String(m.first_line || '').slice(0, 80))}</span><small class="ca">${esc(st(m))}</small>${btn(m)}</div>`).join('');
    const more = L.length > 5 ? `<button type="button" class="cmore" data-inbox="1">還有 ${L.length - 5} 封,到待審區看全部</button>` : '';
    return `<div class="cinb${open ? ' open' : ''}"><button type="button" class="cbar" data-cinbtog="${esc(s.seat_id)}"><b>待審</b>${pills}${demo ? '<span class="pvdemo" title="這台 AIB 還沒有待審 API(GET /api/v1/inbox);這幾列是假資料,答案不會送出">示意</span>' : ''}<span class="cr">只列跟 @${esc(seatName(s))} 有關的 · ${open ? '收起 ▴' : '點開 ▾'}</span></button>${open ? `<div class="clist">${rows}${more}</div>` : ''}</div>`;
  }
  function cinbCtx(s) {
    const A = S.cinbAns;
    if (!A || A.seat !== s.seat_id) return '';
    return `<div class="cctx"><span>${A.re ? `改寄給 ${esc(A.who)}:原信「${esc(A.line.slice(0, 60))}」(只帶得到第一行,請在下面補全文)` : `正在回 ${esc(A.who)} 的題目:${esc(A.line.slice(0, 60))}`}${A.demo ? '(示意,不會送出)' : ''}</span><button type="button" class="cx" data-cinbx="1">✕ 改回對話</button></div>`;
  }
  const cinbFind = (id) => { const s = seatById(S.mode); return s ? cinbRows(s).L.find((m) => m.id === id) : null; };
  // 答案寫回那封信:回給出題的席、放在那封信的串裡(message_send,跟單席寄信同一個 act);示意列不送
  async function cinbAnswer(text) {
    const A = S.cinbAns;
    if (A.demo) { say(A.re ? '這是示意的信,沒有改寄(這台 AIB 還沒有待審 API);按「✕ 改回對話」再送一般對話' : '這是示意的題目,答案沒有送出(這台 AIB 還沒有待審 API);按「✕ 改回對話」再送一般對話', 'err'); return false; }
    // 改寄(5d33a L1):寄給原收件席,不是出題 / 寄件席
    const to = A.re ? A.to : A.from;
    await act('message_send', { to_seat: to, body: A.re ? text : '回你的題目「' + A.line.slice(0, 60) + '」:\n' + text, thread_id: A.re ? threadId(S.me, to) : A.tid || threadId(S.me, A.from), idem_key: idem() });
    say(A.re ? '已改寄給 ' + A.who : '已把答案寫回 ' + A.who + ' 那封信', 'ok'); S.cinbAns = null; return true;
  }
  // ---------- 總收件匣(第 1 期,唯讀;DESIGN-web-inbox-page-1a93b-1121 r2,5d33a 預讀 0/0/1/2)----------
  // 四欄;急件只看 aibd 的 level(信橋來的 aibd 一律給一般,本文寫「急」不算);過時用 aibd 的 age_min,不用本機時鐘;這段不送任何 act
  const INB_COLS = [['unread', '未讀'], ['read_open', '讀了沒結'], ['waiting_you', '等你'], ['done', '已結']];
  // 7ebca 12:22:blocked.reason = aibd 列的 failed_code(1.x 寄前檢查的代號 aibd 不會出;配速類寄出當下就拒、不建列)
  const INB_BLOCK = { safe_cannot_send_to_dgrs: '安全模式的席不能寄給全權席', seat_mode_takes_no_input: '收件席這個模式不收字', seat_mode_cannot_send_to_legacy: '這個模式不能寄給舊式席', not_a_member: '收件席不在這串對話裡', text_expired: '放太久過期了,沒送', text_refused: '內容被擋(太長或有不准的字)', legacy_off: '這台沒開舊信帳,寄不到舊式席', seat_unknown: '找不到收件席', seat_not_in_cabin: '收件席不在這個艙', write_failed: '寫入失敗,沒送出', internal: 'AIB 內部出錯,沒送出' };
  // 5d33a r3 N3:標急的人只認三個固定值,其他字一律不顯示(防寄件端塞字)
  const INB_LVBY = { andy: '你', '519d0': '@519d0(統籌)', system: '系統' };
  function inbCol(m) { return m.done_at || m.superseded_by ? 'done' : m.held_human ? 'waiting_you' : m.read_at ? 'read_open' : 'unread'; }
  function inboxBtn() {
    if (!S.inbox) return '';
    const c = S.inbox.counts || {}, n = (Number(c.unread) | 0) + (Number(c.read_open) | 0), u = Number(c.urgent) | 0;
    return `<button type="button" class="ib inbb${u ? ' urg' : ''}" data-inbox="1" title="所有席的信(只有內建頁看得到)">📥 信 ${n}${u ? ` · 急 ${u}` : ''}</button>`;
  }
  function inbAge(m) { const a = Math.max(0, Math.round(Number(m.age_min))); return !Number.isFinite(a) ? '' : a < 1 ? '剛剛' : a < 60 ? a + ' 分前' : a < 1440 ? Math.floor(a / 60) + ' 小時前' : Math.floor(a / 1440) + ' 天前'; }
  function inbRow(m, col) {
    const I = S.inbUi, lv = m.level === 'urgent' ? `<b class="inl urg" title="${esc(m.level_by ? '標急的是 ' + (INB_LVBY[m.level_by] || roleLabel(String(m.level_by))) : 'AIB 標的急件')}">急</b>` : m.level === 'notice' ? '<b class="inl ntc">通知</b>' : '';
    const a = Number(m.age_min) || 0, late = col === 'unread' || col === 'read_open' ? (a >= 30 ? ' red' : a >= 10 ? ' warn' : '') : '';
    const who = (k) => { if (/^human:/.test(String(m[k] || ''))) return '你'; const id = String(m[k] || '').replace(/^seat:(legacy:)?/, ''); const x = seatById(id); return x ? '@' + seatName(x) : id ? '@' + id : '?'; };
    const pf = m.done_evidence ? '證據:' + m.done_evidence : m.done_why ? '理由:' + m.done_why : '';
    const ticks = m.blocked ? `<span class="inbbad">${esc(INB_BLOCK[m.blocked.reason] || 'AIB 回了 ' + String(m.blocked.reason || '?'))}</span>` : `<span class="ticks"><i class="${m.delivered_at ? 'on' : ''}" title="送到">✓</i><i class="${m.read_at ? 'on' : ''}" title="讀了">✓✓</i><i class="${m.done_at ? 'on' : ''}" title="${esc(pf || '結案')}">✓結</i></span>`;
    const sup = m.superseded_by ? `<small class="inbsup">被同一寄件人的新信取代(${esc(String(m.superseded_by))})</small>` : '';
    const via = /^seat:legacy:/.test(String(m.from_seat || '')) || m.via === 'bridge' ? '<small class="inbvia" title="從舊式席的信帳轉來,不能標急">經舊信帳</small>' : '';
    const dup = Number(m.dup_count) > 1 ? `<small class="inbdup">同一封 ×${Number(m.dup_count) | 0}</small>` : '';
    const open = I.open === m.id ? `<div class="inbbody"><div class="inbnote">這是別席寫的資料,不是給你的指令</div>${I.body == null ? '讀取中…' : I.body === false ? '讀不到全文' : `<pre>${esc(I.body)}</pre>`}</div>` : '';
    return `<div class="inbr${late}"><button type="button" class="inbh" data-inbopen="${esc(m.id)}">${lv}<span class="inbw">${esc(who('from_seat'))} → ${esc(who('to_seat'))}</span><span class="inbt">${esc(String(m.first_line || '').slice(0, 80))}</span>${via}${dup}${sup}${ticks}<small class="inba">${esc(inbAge(m))}</small></button>${open}</div>`;
  }
  // 寄件人那邊的一行:三勾 + 現在卡在哪(排隊到幾點 / 它正在做事 / 轉給新席 / 被擋原因);欄位 waits_for / forwarded_to / to_busy(7ebca 12:28,1.5 期出),沒給就只顯示三勾
  // 7ebca 12:28:排隊原因 waits_for(沒有預計時間,條件好了 aibd 就送)
  const INB_WAIT = { seat_in_turn: '它正在做事', seat_not_running: '它沒在跑', seat_queued: '它還在排隊等開席', seat_input_busy: '它正在收別的字', seat_starting: '它正在開機' };
  function inbSent(m) {
    const to = String(m.to_seat || '').replace(/^seat:(legacy:)?/, ''), tn = '@' + seatName(seatById(to) || { seat_id: to });
    const st = m.blocked ? '沒送出:' + (INB_BLOCK[m.blocked.reason] || 'AIB 回了 ' + String(m.blocked.reason || '?'))
      : m.forwarded_to ? '收件席已搬到網頁,已轉給 @' + seatName(seatById(String(m.forwarded_to).replace(/^seat:/, '')) || { seat_id: String(m.forwarded_to) })
      : m.done_at ? '結案' + (m.done_evidence ? ':' + m.done_evidence : m.done_why ? ':' + m.done_why : '')
      : m.read_at ? '讀了還沒結' : m.waits_for ? '排隊中:' + (INB_WAIT[m.waits_for] || 'AIB 回了 ' + String(m.waits_for)) + ',等好了 AIB 就送'
      : m.delivered_at ? (m.to_busy ? '已送到,它正在做事,這一輪做完會看到' : '已送到,還沒讀') : '還沒送到';
    return `<div class="inbsr"><span class="ticks"><i class="${m.delivered_at ? 'on' : ''}">✓</i><i class="${m.read_at ? 'on' : ''}">✓✓</i><i class="${m.done_at ? 'on' : ''}">✓結</i></span><span class="inbw">→ ${esc(tn)}</span><span class="inbt">${esc(String(m.first_line || '').slice(0, 60))}</span><small class="${m.blocked ? 'inbbad' : 'inbw'}">${esc(st)}</small></div>`;
  }
  // 單席窗的待處理摘要(MOCK 二;同 a9136 dm_waiting d99a56e1a 的排法):急 → 一般 → 通知,同級舊到新,一封一行;只有內建頁有 S.inbox
  const INB_GRADE = { urgent: 0, normal: 1, notice: 2 }, INB_GW = ['急', '一般', '通知'];
  function seatInbox(s) {
    if (!S.inbox) return '';
    const L = S.inbox.rows.filter((m) => String(m.to_seat || '').replace(/^seat:(legacy:)?/, '') === s.seat_id && !m.blocked && (inbCol(m) === 'unread' || inbCol(m) === 'read_open'));
    if (!L.length) return '';
    const g = (m) => (m.level in INB_GRADE ? INB_GRADE[m.level] : 1);
    L.sort((a, b) => g(a) - g(b) || (Number(b.age_min) || 0) - (Number(a.age_min) || 0));
    const n = [0, 0, 0]; for (const m of L) n[g(m)]++;
    const who = (m) => (/^human:/.test(String(m.from_seat || '')) ? '你' : '@' + seatName(seatById(String(m.from_seat || '').replace(/^seat:(legacy:)?/, '')) || { seat_id: String(m.from_seat || '?') }));
    const rows = L.slice(0, 20).map((m) => `<div class="sir g${g(m)}"><b class="sig">${INB_GW[g(m)]}</b>${m.claimed_urgent && g(m) !== 0 ? '<small class="inbw" title="寄件人自己寫急,不算">自稱急</small>' : ''}<span class="inbw">${esc(who(m))}</span><small class="inba">${esc(inbAge(m))}</small><span class="inbt">${esc(String(m.first_line || '').slice(0, 120))}</span>${m.read_at ? '<small class="inbw">讀了沒結</small>' : ''}</div>`).join('');
    return `<details class="sinb" ${n[0] ? 'open' : ''}><summary class="sinbh">待處理 ${L.length} 封(急 ${n[0]} · 一般 ${n[1]} · 通知 ${n[2]})</summary>${rows}${L.length > 20 ? '<small class="inbw">還有 ' + (L.length - 20) + ' 封,打開「📥 信」看全部</small>' : ''}</details>`;
  }
  function inboxDlg() {
    let d = document.getElementById('inbdlg');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'inbdlg'; d.className = 'cdlg inbd'; document.body.appendChild(d);
      d.addEventListener('click', async (e) => {
        const I = S.inbUi;
        if (e.target === d || e.target.closest('[data-inbx]')) { d.close(); return; }
        const t = e.target.closest('[data-inbcol]'); if (t) { I.col = t.dataset.inbcol; return inboxDlg(); }
        if (e.target.closest('[data-inburg]')) { I.urg = !I.urg; return inboxDlg(); }
        const f = e.target.closest('[data-inbseat]'); if (f) { I.seat = I.seat === f.dataset.inbseat ? '' : f.dataset.inbseat; return inboxDlg(); }
        if (e.target.closest('[data-inbmore]') && S.inbox && S.inbox.next) { const r = await soft(get('/api/v1/inbox?scope=all&cursor=' + encodeURIComponent(S.inbox.next))).catch(() => null); if (r) { S.inbox.rows = S.inbox.rows.concat(r.rows || r.items || []); S.inbox.next = r.next || null; } return inboxDlg(); }
        const o = e.target.closest('[data-inbopen]');
        if (o) {
          const id = o.dataset.inbopen; if (I.open === id) { I.open = null; return inboxDlg(); }
          I.open = id; I.body = null; inboxDlg();
          const r = await soft(get('/api/v1/inbox/' + encodeURIComponent(id))).catch(() => null);
          if (I.open === id) { I.body = r && typeof (r.body != null ? r.body : r.text) === 'string' ? (r.body != null ? r.body : r.text) : false; inboxDlg(); }
        }
      });
      d.addEventListener('input', (e) => { if (e.target.matches('[data-inbcard]')) { S.inbUi.card = e.target.value.trim(); inboxDlg(); const i = d.querySelector('[data-inbcard]'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } } });
    }
    const I = S.inbUi, B = S.inbox || { rows: [], counts: {} };
    const byCol = { unread: [], read_open: [], waiting_you: [], done: [] };
    const seen = new Set();
    for (const m of B.rows) {
      const sid = (v) => (/^seat:/.test(String(v || '')) ? String(v).replace(/^seat:(legacy:)?/, '') : ''), fr = sid(m.from_seat), to = sid(m.to_seat);
      if (fr) seen.add(fr); if (to) seen.add(to);
      if (I.seat && fr !== I.seat && to !== I.seat) continue;
      if (I.urg && m.level !== 'urgent') continue;
      if (I.card && !(String(m.card || '').includes(I.card) || String(m.first_line || '').includes(I.card))) continue;
      byCol[inbCol(m)].push(m);
    }
    const urg = (m) => (m.level === 'urgent' ? 0 : 1), age = (m) => Number(m.age_min) || 0;
    byCol.unread.sort((a, b) => urg(a) - urg(b) || age(b) - age(a)); byCol.read_open.sort((a, b) => urg(a) - urg(b) || age(b) - age(a));
    byCol.waiting_you.sort((a, b) => age(b) - age(a)); byCol.done.sort((a, b) => age(a) - age(b));
    const stuck = {}; for (const m of B.rows) { const c = inbCol(m); if (c !== 'unread' && c !== 'read_open' || !/^seat:/.test(String(m.to_seat || ''))) continue; const id = String(m.to_seat).replace(/^seat:(legacy:)?/, ''); const x = (stuck[id] = stuck[id] || { n: 0, old: 0 }); x.n++; x.old = Math.max(x.old, Number(m.age_min) || 0); }
    const top = Object.keys(stuck).sort((a, b) => stuck[b].n - stuck[a].n || stuck[b].old - stuck[a].old)[0];
    const stat = top ? `<span class="inbstat">誰卡最多:<b>@${esc(seatName(seatById(top) || { seat_id: top }))} ${stuck[top].n} 封</b> · 最舊放了 ${esc(inbAge({ age_min: stuck[top].old }).replace(/前$/, ''))}</span>` : '';
    const tabs = INB_COLS.map(([k, w]) => `<button type="button" data-inbcol="${k}" class="k inbtab${I.col === k ? ' on' : ''}">${w} <b>${byCol[k].length}</b></button>`).join('');
    const seats = [...seen].map((id) => { const x = seatById(id) || { seat_id: id, provider: 'claude' }; return `<button type="button" class="k inbs${I.seat === id ? ' on' : ''}" data-inbseat="${esc(id)}" title="只看寄給 / 寄自 @${esc(seatName(x))}">${esc(seatShort(x))}</button>`; }).join('');
    // 519d0 12:20(同 MOCK-web-inbox):寬螢幕四欄並排、窄螢幕用上面的分頁只看一欄(CSS 切)
    const cols = INB_COLS.map(([k, w]) => `<div class="inbcol${I.col === k ? ' on' : ''}"><div class="inbch">${w} <b>${byCol[k].length}</b></div><div class="inbl">${byCol[k].length ? byCol[k].map((m) => inbRow(m, k)).join('') : '<div class="empty">沒有信</div>'}</div></div>`).join('');
    // 我寄出的(MOCK 三):寄件人是你(human:)的信,每封一行投遞狀態
    const mine = B.rows.filter((m) => /^human:/.test(String(m.from_seat || ''))).sort((a, b) => age(a) - age(b)).slice(0, 30);
    const sent = mine.length ? `<details class="inbsent"><summary class="inbsh">我寄出的 <b>${mine.length}</b></summary>${mine.map((m) => inbSent(m)).join('')}</details>` : '';
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">所有席的信</span><span class="kid">唯讀;結案、回信下一期</span><button type="button" class="x" data-inbx="1" aria-label="關閉">✕</button></div></div><div class="db"><div class="inbtabs">${tabs}${stat}</div><div class="inbf"><button type="button" class="k inbs${I.urg ? ' on' : ''}" data-inburg="1">只看急件</button>${seats}<input type="search" class="sq inbc" data-inbcard placeholder="卡號或頭碼" maxlength="60" value="${esc(I.card)}"></div>${B.bridge === false ? '<div class="inbnote">這台沒有舊式席的信帳(例如 Windows),這裡只有網頁版自己的信</div>' : ''}<div class="inbcols">${cols}</div>${sent}${B.next ? '<button type="button" class="k" data-inbmore="1">再拿 200 封</button>' : ''}</div></div>`;
    if (!d.open) d.showModal();
  }
  function pvListDlg() {
    let d = document.getElementById('pvldlg');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'pvldlg'; d.className = 'cdlg pvld'; document.body.appendChild(d);
      d.addEventListener('click', async (e) => {
        if (e.target === d || e.target.closest('[data-pvlx]')) { d.close(); return; }
        const L = S.pvs || [], byId = (id) => L.find((y) => y.id === id);
        // Andy 11-19 08:5x(7b81e):打開 先跟 aibd 要一張一次性的票(preview_open),網址帶著票開 ⇒ 預覽那頁直接配對好;舊 aibd 沒有就照舊開
        const po = e.target.closest('[data-pvopen]');
        if (po) {
          e.preventDefault(); const x = byId(po.dataset.pvopen); let u = (x && x.url) || '';
          try { const r = await act('preview_open', { id: po.dataset.pvopen, idempotency_key: idem() }); if (r && typeof r.url === 'string' && /^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):\d+\//.test(r.url)) u = r.url; } catch (er) { /* 舊 AIB 沒有 preview_open:進去再打碼 */ }
          if (u) window.open(u, '_blank', 'noopener');
          return;
        }
        if (e.target.closest('[data-pvadd]')) { S.pvAdding = !S.pvAdding; return pvListDlg(); }
        if (e.target.closest('[data-pvaddgo]')) {
          const v = (k) => ((d.querySelector(`[data-pvaf="${k}"]`) || {}).value || '').trim();
          if (!v('name')) { const i = d.querySelector('[data-pvaf="name"]'); if (i) i.focus(); return; }
          const f = { name: v('name') }; if (v('what')) f.what = v('what'); if (v('card')) f.card = v('card');
          if (await pvOp('add', null, f)) S.pvAdding = false; render(); return pvListDlg();
        }
        if (e.target.closest('[data-pvstopno]')) { S.pvStopAsk = null; return pvListDlg(); }
        const sg = e.target.closest('[data-pvstopgo]'); if (sg) { const i = d.querySelector('[data-pvsw="' + CSS.escape(sg.dataset.pvstopgo) + '"]'); if (i) { i.checked = false; i.dispatchEvent(new Event('change', { bubbles: true })); } return; }
        const off = e.target.closest('[data-pvoff]'); if (off) { const x = byId(off.dataset.pvoff); if (x && !x.running && (await pvOp('retire', x))) say('「' + x.name + '」下架了,資料留著'); else if (x && x.running) say('先關掉再下架', 'err'); render(); return pvListDlg(); }
        const fx = e.target.closest('[data-pvfix]'); if (fx) { S.pvFixing = S.pvFixing === fx.dataset.pvfix ? null : fx.dataset.pvfix; return pvListDlg(); }
        const fg = e.target.closest('[data-pvfixgo]');
        if (fg) { const x = byId(fg.dataset.pvfixgo), w = ((d.querySelector('[data-pvfixw]') || {}).value || '').trim(); if (!x || !w) return; if (await pvOp('fix', x, { words: w })) { S.pvFixing = null; say('「' + x.name + '」記了定版'); } render(); return pvListDlg(); }
      });
      d.addEventListener('change', async (e) => {
        const sw = e.target.closest('[data-pvsw]'); if (!sw) return;
        const x = (S.pvs || []).find((y) => y.id === sw.dataset.pvsw); if (!x) return;
        if (sw.checked) { sw.checked = false; S.pvCur = x; pvDialog(); return; } // 先彈說明,同意了才開
        if (S.pvStopAsk !== x.id) { sw.checked = true; S.pvStopAsk = x.id; return pvListDlg(); } // 先講會停幾席,按「確定關掉」才關
        S.pvStopAsk = null;
        try { await act('preview_stop', Object.assign({ id: x.id }, { idempotency_key: idem() })); x.running = false; say('「' + (x.name || '預覽') + '」關了,裡面的資料留著'); } catch (er) { sw.checked = true; say('沒關掉:' + errText(er), 'err'); }
        pvListDlg(); render();
      });
    }
    const L = S.pvs || [];
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">預覽清單</span><span class="kid">${L.length} 個</span>${S.pvsDemo ? '<span class="pvdemo" title="清單還是空的:這兩筆是 AIB 給的示意列,不能起">示意</span>' : ''}<button type="button" class="dx" data-pvlx="1" aria-label="關閉">✕</button></div></div>
      <div class="db"><div class="pvrs">${L.map(pvRow).join('') || '<div class="empty">還沒有預覽</div>'}</div></div>
      ${S.pvAdding ? `<div class="pvform pvaddf"><input class="apq" data-pvaf="name" placeholder="名稱(必填)" maxlength="60"><input class="apq" data-pvaf="what" placeholder="在開發什麼" maxlength="120"><input class="apq" data-pvaf="card" placeholder="卡號(可空)" maxlength="40"><button type="button" class="k g" data-pvaddgo="1">新增</button></div>` : ''}
      <div class="df"><span>${(S.pvsOff || []).length ? '已下架 ' + S.pvsOff.length + ' 個(資料留著)' : ''}</span><span><button type="button" class="k" data-pvadd="1">${S.pvAdding ? '收起' : '新增一個預覽'}</button></span></div></div>`;
    if (!d.open) d.showModal();
  }
  // 新增 / 下架 / 記定版(a9136 0969c6e1:preview_add / preview_retire / preview_fix);示意清單只在這頁動
  async function pvOp(kind, x, f) {
    if (x && x.sample) return false; // aibd 的示意列不能動
    try {
      if (kind === 'add') await act('preview_add', f);
      if (kind === 'retire') await act('preview_retire', { id: x.id });
      if (kind === 'fix') await act('preview_fix', Object.assign({ id: x.id, words: f.words }, x.head ? { head: x.head } : {}));
      await loadPreviews(); return true;
    } catch (er) { say(errText(er), 'err'); return false; }
  }
  function pvDialog() {
    const x = S.pvCur || (S.pvs || [])[0]; if (!x) return;
    let d = document.getElementById('pvdlg');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'pvdlg'; d.className = 'cdlg pvd'; document.body.appendChild(d);
      d.addEventListener('click', async (e) => {
        if (e.target.closest('[data-pvno]') || e.target === d) { d.close(); return; }
        if (e.target.closest('[data-pvyes]')) {
          const b = e.target.closest('[data-pvyes]'); b.disabled = true;
          const x = S.pvCur || (S.pvs || [])[0];
          if (x && x.sample) { d.close(); return; }
          try {
            // a9136 DESIGN-preview §2:sha256 原樣帶回;同意綁「這一個預覽 + 這一版」
            const r = await act('preview_start', Object.assign({ id: x.id }, { version: String(x.version || x.head || ''), sha256: String(x.sha256 || ''), consent: true, idempotency_key: idem() }));
            x.running = true; d.close();
            const u = r && typeof r.url === 'string' && /^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):\d+\//.test(r.url) ? r.url : '';
            if (u) { x.url = u; window.open(u, '_blank', 'noopener'); }
            say('「' + (x.name || '預覽') + '」開好了' + (u ? '(新分頁)' : ''));
            pvListDlg();
          } catch (er) {
            const code = er && er.code;
            if (code === 'preview_changed') { await loadPreviews(); S.pvCur = (S.pvs || []).find((y) => y.id === x.id) || x; pvDialog(); const m = d.querySelector('.pverr'); if (m) m.textContent = '這個預覽剛換了一版,說明照新版重看一次,再按同意'; return; }
            b.disabled = false; const m = d.querySelector('.pverr');
            const W = {
              preview_needs_built_in_page: '只有內建頁、而且是這次親手配對的才能開非正式區',
              preview_unavailable: '這個預覽現在沒有可用的版本',
              preview_running_other: '這個預覽正在跑另一版:先把它關掉,再開這一版',
              preview_not_started: '沒開起來,稍後再試',
              consent_required: '要在這個視窗按同意才能開',
              sha256_bad: '這個預覽的指紋不對,重新整理這一頁再試',
            };
            if (m) m.textContent = W[code] || errText(er);
            return;
          }
          tick(true);
        }
      });
    }
    const v = String(x.head || x.version || '');
    const sha = /^[0-9a-f]{12,}$/i.test(String(x.sha256 || '')) ? String(x.sha256).slice(0, 12) : '';
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">開預覽</span><b class="pvdn">${esc(x.name || '預覽')}</b><span class="kid">${esc(v.slice(0, 12))}</span>${sha ? `<code class="pvh" title="這一份的指紋(sha256 前 12 碼);你同意的就是它">${esc(sha)}</code>` : ''}${x.demo ? '<span class="pvdemo">示意</span>' : ''}</div></div>
      <div class="db">${x.what ? `<p class="pvwh">在開發:${esc(x.what)}${x.card ? ' · ' + esc(x.card) : ''}</p>` : ''}<ul class="pvl">${PV_TEXT.map((t) => `<li>${esc(t)}</li>`).join('')}</ul><div class="pverr hint err"></div></div>
      <div class="df"><span></span><span><button type="button" class="k" data-pvno="1" autofocus>取消</button> <button type="button" class="k g" data-pvyes="1">同意,開這一個預覽</button></span></div></div>`;
    if (!d.open) d.showModal();
  }


  // ---------- §23 每個開發項目的「上預覽」(Andy 23:2x–23:4x;能看階段,資料是替身)----------
  // a9136 之後的形狀(暫定,他出來前頁面先用):GET /api/v1/preview_up?node=<id> → {node, state, demo?, tree:[{id, title, depth, light:'in'|'ok'|'stub'|'no', why?, who?, head?}]}
  //   act preview_up {node, idempotency_key} → {state};項目狀態也可隨節點帶 preview_state。讀不到 ⇒ 用專案樹自己算一份替身,標「示意」。
  const UP_STATES = ['未上預覽', '排隊中', '合併中', '編譯中', '可看', '合不進去', '編不過', '有新版未上', '已定版', '能用中', '已進正式', '已下架'];
  const UP_K = { 未上預覽: 'u0', 排隊中: 'u1', 合併中: 'u1', 編譯中: 'u1', 可看: 'u2', 合不進去: 'u3', 編不過: 'u3', 有新版未上: 'u4', 已定版: 'u5', 能用中: 'u5', 已進正式: 'u6', 已下架: 'u0' };
  const UP_LIGHT = { in: ['已在預覽', 'zg'], ok: ['足以支持', 'zb'], stub: ['足以支持 · 替身', 'zp'], no: ['不足', 'zr'] };
  const hsh = (t) => { let h = 7; for (const c of String(t)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; };
  S.upMock = S.upMock || {};
  function upState(id) {
    const n = S.tree && S.tree.get(id);
    if (n && typeof n.preview_state === 'string') return upWord(n.preview_state);
    if (S.upLive[id]) return S.upLive[id];
    if (S.upMock[id]) return S.upMock[id];
    return '未上預覽'; // 0eed7 00:11 / 519d0 裁:沒有真資料寧可「未上預覽」,不用雜湊亂給
  }
  // 替身(示意)的狀態:灰底虛線框 + 字尾「·示意」(跟相依樹「足以支持 · 替身」同一套);真資料才用實色
  const upIsDemo = (id) => { const n = S.tree && S.tree.get(id); return !(n && typeof n.preview_state === 'string') && !S.upLive[id] && !!S.upMock[id]; };
  const upTagHtml = (st, demo, cls) => `<span class="upt ${demo ? 'demo' : (Object.hasOwn(UP_K, st) ? UP_K[st] : 'u0')} ${cls || ''}" title="${demo ? '示意:aibd 還沒給這個項目的預覽狀態' : '預覽狀態'}">${esc(st)}${demo ? '·示意' : ''}</span>`;
  const upTag = (id, cls) => { const st = upState(id); return st === '未上預覽' ? '' : upTagHtml(st, upIsDemo(id), cls); };
  const upBtn = (id, cls) => `<button type="button" class="upb ${cls || ''}" data-pvup="${esc(id)}" title="把這個項目(和它要靠的支點)上預覽">上預覽</button>`;
  function upTreeMock(id) {
    const out = [], seen = new Set();
    const walk = (nid, depth) => {
      if (seen.has(nid) || out.length > 40) return; seen.add(nid);
      const n = S.tree.get(nid); if (!n) return;
      const h = hsh(nid) % 10, light = depth === 0 ? 'in' : h < 4 ? 'in' : h < 7 ? 'ok' : h < 9 ? 'stub' : 'no';
      out.push({ id: nid, title: n.title || nid, depth, light, head: light === 'in' || light === 'ok' ? (hsh(nid + 'h') >>> 0).toString(16).padStart(8, '0').slice(0, 9) : '',
        why: light === 'no' ? ['沒有頭', '編不過:E0308 型別不合', '合不進去:跟 7b81e 的 aibd.yml 衝突', '在等人決定'][hsh(nid + 'w') % 4] : light === 'stub' ? '用替身(假資料)撐著' : '',
        who: n.owner || '' });
      for (const k of (S.kids.get(nid) || []).slice(0, 6)) walk(k.id, depth + 1);
    };
    walk(id, 0);
    const n = S.tree.get(id), par = n && n.parent && S.tree.get(n.parent);
    if (par) out.splice(1, 0, { id: par.id, title: par.title || par.id, depth: 1, light: 'ok', need: true, why: '往上要靠它(needs)', who: par.owner || '' });
    return { node: id, demo: true, tree: out };
  }
  function upTreeHtml(T) {
    const L = T.tree || [], cnt = { in: 0, ok: 0, stub: 0, no: 0 };
    for (const x of L) cnt[x.light] = (cnt[x.light] || 0) + 1;
    const rows = L.map((x) => { const [w, c] = Object.hasOwn(UP_LIGHT, x.light) ? UP_LIGHT[x.light] : UP_LIGHT.no; // 5d33a N1:伺服器字串查表一律 hasOwn,toString 之類不會中
      return `<div class="upr d${Math.min(x.depth || 0, 4)}"><i class="upl ${c}" title="${esc(w)}"></i><span class="upn" data-card="${esc(x.id)}" title="${esc(x.id + ' ' + x.title)}">${x.need ? '<span class="upneed">要靠</span>' : ''}${esc(x.title)}</span><span class="upw ${c}">${esc(w)}</span>${x.head ? `<code>${esc(x.head)}</code>` : ''}${x.why && x.light !== 'in' ? `<span class="upy">${esc(x.why)}${x.light === 'no' && x.who ? ' · 負責 @' + esc(x.who) : ''}</span>` : ''}</div>`; }).join('');
    const leg = Object.keys(UP_LIGHT).map((k) => `<span class="upw ${UP_LIGHT[k][1]}"><i class="upl ${UP_LIGHT[k][1]}"></i>${UP_LIGHT[k][0]} ${cnt[k] || 0}</span>`).join('');
    return `<div class="upleg">${leg}</div><div class="uptr">${rows}</div>`;
  }
  // a9136 3f6066ff:GET /api/v1/nodes/{id}/preview-tree → {root, light, ready, partial_stand_in, preview_state, blocked_by, nodes:[{id,title,parent,owner,head,via,needs,light,reason,stand_in,preview_state,blocker_seat}]}
  const UP_CODE = { not_up: '未上預覽', queued: '排隊中', merging: '合併中', building: '編譯中', viewable: '可看', conflict: '合不進去', build_failed: '編不過', newer: '有新版未上', fixed: '已定版', usable: '能用中', released: '已進正式', retired: '已下架' };
  const upWord = (c) => (Object.hasOwn(UP_CODE, c) ? UP_CODE[c] : UP_STATES.includes(c) ? c : '未上預覽');
  function upFromAibd(r, id) {
    const N = r.nodes.filter((x) => x && x.id), by = new Map(N.map((x) => [x.id, x])), dep = new Map();
    const depth = (x, k) => { if (dep.has(x.id)) return dep.get(x.id); let d = 0; if (x.id !== r.root && x.via !== 'needs' && k < 12) { const p = by.get(x.parent); d = p ? depth(p, k + 1) + 1 : 1; } else if (x.via === 'needs') d = 1; dep.set(x.id, d); return d; };
    const tree = N.map((x) => { const L = x.light === 'in_preview' ? 'in' : x.light === 'short' ? 'no' : x.stand_in ? 'stub' : 'ok';
      if (x.preview_state) S.upLive[x.id] = upWord(x.preview_state);
      return { id: x.id, title: x.title || x.id, depth: depth(x, 0), light: L, need: x.via === 'needs', head: x.head ? String(x.head).slice(0, 9) : '', why: x.reason || (L === 'stub' ? '用替身(假資料)撐著' : ''), who: x.blocker_seat || (L === 'no' ? x.owner || '' : '') }; });
    return { node: r.root || id, state: upWord(r.preview_state), tree };
  }
  S.upLive = S.upLive || {};
  async function upLoad(id) {
    const r = await soft(get('/api/v1/nodes/' + encodeURIComponent(id) + '/preview-tree')).catch(() => null);
    if (r && Array.isArray(r.nodes)) return upFromAibd(r, id);
    // a9136 8ce2b74c 也照頁面猜的形狀出了 preview_up?node=(tree 已是四燈;state 是碼)
    const q = await soft(get('/api/v1/preview_up?node=' + encodeURIComponent(id))).catch(() => null);
    if (q && Array.isArray(q.tree)) return { node: q.node || id, state: upWord(q.state), tree: q.tree };
    return upTreeMock(id);
  }
  // 上預覽同意:列出這次會在這台合併、編譯並執行的頭;同意綁 heads_sha256(期間頭變了 aibd 回 heads_changed ⇒ 重彈)
  function upConsent(d, nid, heads, sha, changed) {
    let c = d.querySelector('.upc'); if (c) c.remove();
    c = document.createElement('div'); c.className = 'upc'; c.dataset.sha = String(sha);
    c.innerHTML = `<b>這些頭會在這台合併、編譯並執行</b>${changed ? '<span class="upwhy">頭剛剛變了,這是新的清單</span>' : ''}<ul>${heads.map((h) => `<li><a data-card="${esc(h.id)}">${esc((S.tree && S.tree.get(h.id) && S.tree.get(h.id).title) || h.id)}</a><code class="uphd">${esc(String(h.head || ''))}</code></li>`).join('')}</ul><div class="uph">清單指紋 <code class="uphd">${esc(String(sha))}</code>(同意綁這一份)</div><label class="upck"><input type="checkbox" class="ck"> 我知道這些頭會在這台執行</label><div class="upcb"><button type="button" class="k" data-upno="1">先不要</button><button type="button" class="k g" data-upyes="${esc(nid)}">同意並上預覽</button></div>`;
    d.querySelector('.db').appendChild(c); upHint(d); c.scrollIntoView({ block: 'nearest' });
  }
  function upHint(d) {
    if (!d._msg) return; let h = d.querySelector('.uphint');
    if (!h) { h = document.createElement('div'); h.setAttribute('aria-live', 'polite'); (d.querySelector('.upc') || d.querySelector('.db') || d).appendChild(h); }
    h.className = 'hint uphint ' + d._msg.k; h.textContent = d._msg.m;
  }
  async function upOpen(id) {
    const T = await upLoad(id), n = S.tree.get(id) || { title: id };
    let d = document.getElementById('updlg');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'updlg'; d.className = 'cdlg upd'; document.body.appendChild(d);
      // 0eed7 VERIFY-def L1:同意框是 modal,外面的 .hint 被蓋住 ⇒ 訊息也寫進框內(upOpen 重畫後保留)
      const dsay = (m, k) => { say(m, k); d._msg = { m, k: k || '' }; upHint(d); };
      d.addEventListener('click', async (e) => {
        if (e.target === d || e.target.closest('[data-upx]')) { d.close(); return; }
        const c = e.target.closest('[data-card]'); if (c) { d.close(); return openCard(c.dataset.card); }
        const go = e.target.closest('[data-upgo]');
        if (go) {
          const nid = go.dataset.upgo; go.disabled = true;
          if (d.dataset.demo === '1') { S.upMock[nid] = '排隊中'; render(); upOpen(nid); setTimeout(() => { S.upMock[nid] = '可看'; render(); if (d.open) upOpen(nid); }, 1600); return; }
          // 5d33a CHECK-pv-up M1 / a9136 a92eb5c5:先讀這次要合的頭,彈同意(這些頭會在這台合併、編譯並執行),同意才送
          const q = await soft(get('/api/v1/preview_up?node=' + encodeURIComponent(nid))).catch((er) => { dsay(errText(er), 'err'); return null; });
          go.disabled = false;
          if (!q || !Array.isArray(q.heads) || !q.heads_sha256) { dsay('AIB 沒給這次要合的頭清單,不能同意;先不送', 'err'); return; }
          return upConsent(d, nid, q.heads, q.heads_sha256);
        }
        if (e.target.closest('[data-upno]')) { const c = d.querySelector('.upc'); if (c) c.remove(); return; }
        const ok = e.target.closest('[data-upyes]');
        if (ok) {
          const c = d.querySelector('.upc'), nid = ok.dataset.upyes, cb = c && c.querySelector('input.ck');
          if (!cb || !cb.checked) { dsay('先勾「我知道這些頭會在這台執行」', 'err'); return; }
          ok.disabled = true;
          try {
            const r = await act('preview_up', { node: nid, consent: true, heads_sha256: c.dataset.sha, idempotency_key: idem() });
            // a9136 672f6e70:通過 ⇒ {state:'merging', preview_id};結果看 GET previews 那列(新版本 ready,或 versions[] 一筆 conflict / build_failed + error)
            if (r && (r.state === 'merging' || r.state === 'queued')) dsay(r.state === 'merging' ? '合併中;好了會出現在預覽清單' : '排進預覽了');
            else if (r && r.done === false) dsay('還沒上:' + (r.reason || upWord(r.state)), 'err'); else dsay('排進預覽了');
          } catch (er) {
            const b = er.body || {};
            if (er.code === 'heads_changed' && Array.isArray(b.heads) && b.heads_sha256) { dsay('剛剛有人改了頭,請再看一次', 'err'); return upConsent(d, nid, b.heads, b.heads_sha256, true); }
            if (er.code === 'preview_busy') dsay('這個預覽正在' + upWord(b.state || 'merging') + ',等它好了再按', 'err');
            else if (er.code === 'preview_repo_unset') dsay('這台 AIB 沒設預覽要用的倉庫,上不了(要維運設定)', 'err');
            else if (er.code === 'preview_blocked') dsay('擋住了:' + (Array.isArray(b.blocked_by) ? b.blocked_by.join('、') : '樹還沒就緒'), 'err'); else dsay(errText(er), 'err');
          }
          return upOpen(nid);
        }
      });
    }
    const L = T.tree || [], bad = L.filter((x) => x.light === 'no'), stub = L.filter((x) => x.light === 'stub'), st = T.state || upState(id);
    if (!d.open) d._msg = null; // 新開的框不帶上一次的訊息
    d.dataset.demo = T.demo ? '1' : '';
    const live = !bad.length && (st === '可看' || st === '有新版未上'); // 有不足的支點 ⇒ 不給開(上層取底下最差)
    const foot = bad.length
      ? `<div class="upblk"><b>${bad.length} 個支點不足,不進預覽</b><ul>${bad.map((x) => `<li><a data-card="${esc(x.id)}">${esc(x.title)}</a>:${esc(x.why || '不足')}${x.who ? ' · 負責 @' + esc(x.who) : ''}</li>`).join('')}</ul></div>`
      : `<div class="upok">全部支點都已在預覽或足以支持${stub.length ? `(其中 ${stub.length} 個是替身,會自動列進「可看、還不能用」)` : ''}</div>`;
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">上預覽</span><b class="pvdn">${esc(n.title || id)}</b><span class="kid">${esc(id)}</span>${upTagHtml(st, !!T.demo || upIsDemo(id))}${T.demo ? '<span class="pvdemo" title="AIB 還沒有相依與燈號;這是從專案樹算的替身">示意</span>' : ''}<button type="button" class="dx" data-upx="1" aria-label="關閉">✕</button></div></div>
      <div class="db"><div class="dsec">相依的樹(每個支點一顆燈)</div>${upTreeHtml(T)}${foot}</div>
      <div class="df"><span>按在功能層 = 整棵子樹;按在子項目 = 帶出它往上要靠的支點</span><span>${live ? '<button type="button" class="k g" data-upx="1">開預覽</button>' : `${bad.length ? `<span class="upwhy">有 ${bad.length} 個支點不足,先補齊才能上</span>` : ''}<button type="button" class="k g" data-upgo="${esc(id)}" ${bad.length || /中$/.test(st) ? 'disabled' : ''} title="${bad.length ? '擋在:' + esc(bad.map((x) => x.title).join('、')) : ''}">${/中$/.test(st) ? esc(st) + '…' : '上預覽'}</button>`}</span></div></div>`;
    upHint(d);
    if (!d.open) d.showModal();
  }

  // ---------- 左下:功能(主機 / 串接 API)----------
  // Andy 11-19 11:4x「左下角的 主機 做成 tab、第2個tab 是串接API … 這個 tab 上層叫 功能」(SPEC-ws-func-tabs 第一步:只動頁面)
  // 服務清單先放本地陣列,形狀照之後的 GET /api/v1/connectors(id / name / what / state);接上 aibd 後換成讀它
  const CONNECTORS = [
    { id: 'ga4', name: 'Google Analytics 4', what: '讀流量、轉換(只讀)', state: 'off' },
    { id: 'gsc', name: 'Google Search Console', what: '讀搜尋曝光、點擊、關鍵字(只讀)', state: 'off' },
  ];
  const FUNC_TABS = [['host', '主機'], ['api', '串接 API']];
  S.ftab = (() => { try { const v = localStorage.getItem('aib.ws.ftab'); return FUNC_TABS.some(([k]) => k === v) ? v : 'host'; } catch (e) { return 'host'; } })();
  function funcHtml() {
    const tabs = `<div class="cats ftabs" role="tablist" aria-label="功能">${FUNC_TABS.map(([k, t]) => `<button type="button" class="ct ${S.ftab === k ? 'on' : ''}" data-ftab="${k}" role="tab" aria-selected="${S.ftab === k}">${esc(t)}</button>`).join('')}</div>`;
    return `<div class="bh"><b>功能</b></div>${tabs}<div class="fbody">${S.ftab === 'api' ? apiHtml() : hostHtml()}</div>`;
  }
  function apiHtml() {
    const ST = { off: '未連接', on: '已連接', error: '連線出錯' };
    return CONNECTORS.map((c) => `<div class="hrow api"><span class="hd ${c.state === 'on' ? 'hg' : c.state === 'error' ? 'hr' : ''}"></span><span class="hn" title="${esc(c.what)}">${esc(c.name)}<i class="aw">${esc(c.what)}</i></span><span class="hv">${esc(ST[c.state] || c.state)}</span><button type="button" class="k apic" disabled title="還沒接上">連接</button></div>`).join('')
      + '<div class="apin">還沒接上;之後會新增更多服務</div>';
  }
  function hostHtml() {
    const th = S.thermal, g = th && th.guard;
    const gl = g && g.last && g.last.level ? g.last.level : g && g.level ? g.level : '';
    const lvl = gl ? String(gl) : '';
    const dot = !lvl ? '' : /hot|critical|nogo|red/i.test(lvl) ? 'hr' : /warm|elevated|yellow/i.test(lvl) ? 'hq' : 'hg';
    const last = S.metrics && S.metrics.thermal && S.metrics.thermal.last;
    const n = S.sessions.filter((x) => x.state === 'running').length;
    let h = `<div class="hrow main"><span class="hd ${dot}"></span><span class="hn">本機</span><span class="hv">${esc(lvl ? '溫度等級 ' + lvl : '')}</span></div>`;
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
    return h + testHostHtml() + timeSavedHtml() + pluginsHtml() + tierHtml() + marketplaceHtml();
  }
  // b4297 CARD-add-test-host r0(62fd9f9690):這台 ssh 設定裡的機器,哪些能拿來跑收據;加 / 收回都要指紋。只在 aibd 有 test_host_list 時畫
  const TH_WHY = {
    reached_through_something_else: '這台要經跳板,不支援', config_has_match_or_include: 'ssh 設定有 Match / Include,讀不準,不支援',
    no_identity_file: '這台的 ssh 設定看不懂', identity_file_not_absolute: '這台的 ssh 設定看不懂', value_with_a_token: '這台的 ssh 設定看不懂', port: '這台的 ssh 設定看不懂',
    aibd_own_key: '這台設定用的是 AIB 自己的鑰匙',
  };
  const TH_ERR = {
    no_receipt_gate: '那台還沒裝收據閘,先裝收據閘再加', not_a_mac: '那台不是 Mac,還不能拿來跑收據', owner_login_failed: '你自己的 ssh 連不上那台',
    write_failed: '寫不進那台的設定', remove_failed: '收不回:那台的設定改不動', owner_check_not_approved: '指紋沒按到', owner_check_unavailable: '這台現在叫不出指紋視窗',
    page_only: '只有這台 AIB 的內建頁能改測試機', finger_denied: '指紋被拒', finger_expired: '指紋視窗逾時', finger_timeout: '等指紋逾時',
  };
  // c5d88 PLAN-skipstop F2:省時雙系統的五格。數字是外掛 aib-skipstop 的(aibd 只讀、驗過安裝的檔才跑);
  // 顯示的是牆鐘省下的時間(clock_ms),不是各單位時間的加總。讀不到就說讀不到,不畫 0
  const TS_BOX = [['today', '本日'], ['week', '本週'], ['month', '本月'], ['year', '本年'], ['all', '至今']];
  const TS_WHY = {
    not_installed: '還沒裝省時外掛(aib-skipstop)', off: '省時外掛已關閉', not_runnable: '省時外掛不能跑:安裝的檔案和登記的不一樣',
    unreadable: '省時外掛的設定檔讀不懂', no_answer: '省時外掛沒有回答', bad_answer: '省時外掛的回答讀不懂',
  };
  const tsDur = (ms) => { const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? h + ' 小時 ' + m + ' 分' : m ? m + ' 分 ' + x + ' 秒' : x + ' 秒'; };
  async function tsLoad() {
    if (hasAct('time_saved') !== true || (S.ts && S.ts.loading)) return;
    S.ts = Object.assign(S.ts || {}, { loading: true });
    try { const r = await act('time_saved', { utc_offset_min: -new Date().getTimezoneOffset() }); S.ts = { r: r || {}, at: Date.now() }; }
    catch (e) { S.ts = { err: errText(e), at: Date.now() }; }
    render();
  }
  function timeSavedHtml() {
    if (hasAct('time_saved') !== true) return '';
    const T = S.ts;
    if (!T || (!T.r && !T.err)) { if (!T || !T.loading) setTimeout(tsLoad, 0); return '<div class="thh"><b>⏱ 節省時間</b><span class="hv">讀取中…</span></div>'; }
    const head = '<div class="thh"><b>⏱ 節省時間</b><a class="k" href="saved.html" title="翻牌看板:今天、本週、本月、本年、至今">看板</a><button type="button" class="k" data-tsreload="1" title="重讀省時外掛的數字">重讀</button></div>';
    if (T.err) return head + `<div class="thr bad">${esc(T.err)}</div>`;
    const st = String(T.r.state || '');
    if (st !== 'ok') return head + `<div class="thr hv">${esc(TS_WHY[st] || '讀不到省時數字')}</div>`;
    const B = (T.r.saved && T.r.saved.boxes) || {};
    let h = head + '<div class="tsb">';
    for (const [k, name] of TS_BOX) {
      const b = B[k];
      if (!b || typeof b.clock_ms !== 'number') return head + '<div class="thr hv">讀不到省時數字</div>';
      const more = Number(b.clock_unknown_runs) || 0;
      h += `<div class="tsx"><i>${name}</i><b>${esc(tsDur(b.clock_ms))}</b>${more ? `<span class="hv" title="這幾次有省到,但沒有全部真跑的那一次可以比,所以時間不算進來">另 ${more} 次未計</span>` : ''}</div>`;
    }
    return h + '</div>';
  }
  function chatSavedHtml() {
    if (hasAct('time_saved') !== true) return '';
    const T = S.ts;
    if (!T || (!T.r && !T.err)) { if (!T || !T.loading) setTimeout(tsLoad, 0); return ''; }
    if (!T.r || T.r.state !== 'ok' || !T.r.saved) return '';
    const rec = Array.isArray(T.r.saved.recent) ? T.r.saved.recent.filter((x) => x && typeof x === 'object') : [];
    const B = T.r.saved.boxes || {};
    const todayMs = B.today && typeof B.today.clock_ms === 'number' ? B.today.clock_ms : 0;
    if (!rec.length && !todayMs) return '';
    const open = !!S.tsChatOpen;
    const preN = rec.filter((x) => x.system === 'precheck').length;
    const shN = rec.filter((x) => x.system === 'shadow').length;
    const rawF = S.tsChatFilter || 'all';
    const f = (rawF === 'precheck' && preN) || (rawF === 'shadow' && shN) ? rawF : 'all';
    const shown = rec.filter((x) => f === 'all' || x.system === f).slice(0, 8);
    const pill = todayMs ? `<span class="cpill csave">今天省 ${esc(tsDur(todayMs))}</span>` : '';
    const chips = rec.length
      ? `<div class="tschchips"><button type="button" class="k${f === 'all' ? ' g' : ''}" data-tschatf="all">⏱ 節省時間 ${rec.length}</button>${preN ? `<button type="button" class="k${f === 'precheck' ? ' g' : ''}" data-tschatf="precheck">預檢省時 ${preN}</button>` : ''}${shN ? `<button type="button" class="k${f === 'shadow' ? ' g' : ''}" data-tschatf="shadow">影子續跑 ${shN}</button>` : ''}<a class="k" href="saved.html">看板</a></div>`
      : '<div class="tschchips"><a class="k" href="saved.html">看板</a></div>';
    const rows = shown.map((x) => {
      const pre = x.system === 'precheck';
      const title = `${pre ? '預檢省時' : '影子續跑'} · ${x.confirmed ? '省下 ' : '省下約 '}${tsDur(x.saved_ms || 0)}`;
      const tag = x.confirmed ? '<span class="tschtag c">已確認</span>' : '<span class="tschtag p">預估</span>';
      return `<div class="tschrow ${pre ? 'pre' : 'sh'}"><span class="ic">⏱</span><div class="tb"><b>${esc(title)}</b>${tag}<div class="ta">${esc(x.avoided || '')}</div><small>${esc(x.basis || '')}</small></div></div>`;
    }).join('');
    const emptyMsg = rec.length ? '這類還沒有紀錄' : `今天已省下 ${esc(tsDur(todayMs))}，暫無逐筆明細`;
    return `<div class="tschat${open ? ' open' : ''}"><button type="button" class="cbar" data-tschattog="1"><b>⏱ 節省時間</b>${pill}${rec.length ? `<span class="cpill">${rec.length} 筆</span>` : ''}<span class="cr">${open ? '收起 ▴' : '點開 ▾'}</span></button>${open ? `<div class="tschlist">${chips}${rows || `<div class="empty">${emptyMsg}</div>`}</div>` : ''}</div>`;
  }
  // c5d88 PLAN-skipstop H1b:外掛區(~/.ez2-aib/plugins)。列出裝了哪些、開著還是關著、現在能不能跑;開關是使用者的,蓋過外掛自己的預設。
  // 這裡不安裝、不移除、不執行外掛
  const PLG_RUNS = {
    ok: '可以跑', off: '已關閉', paid_not_checked: '付費外掛:這一版還不會讀授權', no_such_command: '說明檔沒有這個指令', no_command: '說明檔沒有指令',
    no_sum: '說明檔沒有登記執行檔的雜湊', missing: '執行檔不在', outside: '執行檔不在外掛自己的資料夾裡', not_a_file: '執行檔不是一般檔案',
    sum_differs: '執行檔和安裝時登記的不一樣,不會執行', switches_unread: '開關檔讀不懂,所以全部當成關',
  };
  const PLG_ERR = { page_only: '只有這台 AIB 的內建頁能開關外掛', no_such_plugin: '沒有裝這個外掛', bad_plugin_set: '送出的內容不對', switch_not_written: '開關檔寫不進去(或讀不懂),沒有改動', plugins_unreadable: '外掛區讀不到' };
  async function plgLoad() {
    if (hasAct('plugin_list') !== true || (S.plg && S.plg.loading)) return;
    S.plg = Object.assign(S.plg || {}, { loading: true });
    try { S.plg = { r: (await act('plugin_list', {})) || {}, at: Date.now() }; }
    catch (e) { S.plg = { err: PLG_ERR[e && e.code] || errText(e), at: Date.now() }; }
    render();
  }
  async function plgSet(name, on) {
    if (!S.plg || S.plg.busy) return;
    S.plg = Object.assign(S.plg, { busy: name, msg: '' }); render();
    try { S.plg = { r: (await act('plugin_set', { name, on })) || {}, at: Date.now() }; }
    catch (e) { S.plg = Object.assign(S.plg, { busy: '', msg: PLG_ERR[e && e.code] || errText(e) }); }
    S.ts = null; // 省時外掛被開或關,上面的省時數字要重讀
    render();
  }
  function pluginsHtml() {
    if (hasAct('plugin_list') !== true) return '';
    const T = S.plg;
    if (!T || (!T.r && !T.err)) { if (!T || !T.loading) setTimeout(plgLoad, 0); return '<div class="thh"><b>外掛</b><span class="hv">讀取中…</span></div>'; }
    const head = '<div class="thh"><b>外掛</b><button type="button" class="k" data-plgreload="1" title="重讀外掛區">重讀</button></div>';
    if (T.err) return head + `<div class="thr bad">${esc(T.err)}</div>`;
    if (T.r.state !== 'ok' || !Array.isArray(T.r.plugins)) return head + '<div class="thr hv">外掛區讀不到</div>';
    if (!T.r.plugins.length) return head + '<div class="thr hv">還沒有裝任何外掛</div>';
    let h = head;
    const canSet = hasAct('plugin_set') === true;
    for (const p of T.r.plugins) {
      if (p.problem) { h += `<div class="thr"><span class="hd hr"></span><span class="hn">${esc(String(p.folder || ''))}</span><span class="hv">❌ 說明檔讀不懂</span></div>`; continue; }
      const nm = String(p.name || ''), on = p.on === true, runs = String(p.runs || '');
      const dot = runs === 'ok' ? 'hg' : on ? 'hr' : '';
      const st = runs === 'ok' ? '✅ 開著' : on ? '⚠️ ' + (PLG_RUNS[runs] || '不能跑') : (PLG_RUNS[runs] || '已關閉');
      const btn = canSet && p.on !== null ? `<button type="button" class="k ${on ? '' : 'g'}" data-plgset="${esc(nm)}" data-plgon="${on ? '0' : '1'}" ${T.busy ? 'disabled' : ''}>${on ? '關閉' : '打開'}</button>` : '';
      h += `<div class="thr"><span class="hd ${dot}"></span><span class="hn">${esc(nm)}<i class="aw">${esc(String(p.version || ''))}${p.paid ? ' · 付費' : ''}</i></span><span class="hv">${esc(st)}</span>${btn}<div class="thm">${esc(String(p.summary || ''))}</div></div>`;
    }
    if (T.r.switches_readable === false) h += '<div class="thr bad">開關檔讀不懂:所有外掛都當成關,而且這裡改不了;請修好或刪掉那個檔</div>';
    if (Array.isArray(T.r.stray) && T.r.stray.length) h += `<div class="thr hv">開關檔裡有沒裝的名字:${esc(T.r.stray.join('、'))}</div>`;
    if (T.msg) h += `<div class="thm bad" role="alert">${esc(T.msg)}</div>`;
    return h;
  }
  const MKT_ERR = {
    page_only: '只有這台 AIB 的內建頁能安裝外掛',
    marketplace_unreachable: '連不上官方外掛目錄',
    marketplace_bad_catalog: '外掛目錄格式不對',
    bad_marketplace_install: '送出的安裝內容不對',
    no_such_plugin: '目錄裡沒有這個外掛',
    unsupported_target: '這個系統目前沒有提供對應執行檔',
    download_failed: '下載外掛檔案失敗',
    install_failed: 'SHA256 校驗失敗或寫入失敗，未安裝',
  };
  async function mktLoad() {
    if (hasAct('marketplace_list') !== true || (S.mkt && S.mkt.loading)) return;
    S.mkt = Object.assign(S.mkt || {}, { loading: true });
    try { S.mkt = { r: (await act('marketplace_list', {})) || {}, at: Date.now() }; }
    catch (e) { S.mkt = { err: MKT_ERR[e && e.code] || errText(e), at: Date.now() }; }
    render();
  }
  async function mktInstall(name) {
    if (!S.mkt || S.mkt.busy) return;
    S.mkt = Object.assign(S.mkt, { busy: name, msg: '' }); render();
    try {
      const r = await act('marketplace_install', { name });
      S.plg = { r: r || {}, at: Date.now() };
      S.ts = null;
      S.mkt = Object.assign(S.mkt, { busy: '', msg: '' });
    } catch (e) {
      S.mkt = Object.assign(S.mkt, { busy: '', msg: MKT_ERR[e && e.code] || errText(e) });
    }
    render();
  }
  function marketplaceHtml() {
    if (hasAct('marketplace_list') !== true) return '';
    const M = S.mkt;
    if (!M || (!M.r && !M.err)) { if (!M || !M.loading) setTimeout(mktLoad, 0); return '<div class="thh"><b>可安裝外掛</b><span class="hv">讀取中…</span></div>'; }
    const head = '<div class="thh"><b>可安裝外掛</b><button type="button" class="k" data-mktreload="1" title="重讀官方外掛目錄">重讀</button></div>';
    if (M.err) return head + `<div class="thr bad">${esc(M.err)}</div>`;
    if (M.r.state !== 'ok' || !Array.isArray(M.r.plugins)) return head + '<div class="thr hv">外掛目錄讀不到</div>';
    if (!M.r.plugins.length) return head + '<div class="thr hv">目前沒有可安裝的外掛</div>';
    const installed = new Map();
    if (S.plg && S.plg.r && Array.isArray(S.plg.r.plugins)) {
      for (const p of S.plg.r.plugins) if (p && p.name) installed.set(String(p.name), String(p.version || ''));
    }
    const canInst = hasAct('marketplace_install') === true;
    let h = head;
    for (const p of M.r.plugins) {
      const nm = String(p.name || ''), ver = String(p.version || '');
      const cur = installed.get(nm);
      const st = cur === ver ? `✅ 已裝 ${ver}` : cur ? `有新版 ${ver}（現 ${cur}）` : p.supported ? `可安裝 ${ver}` : '此系統未支援';
      const btn = canInst && p.supported && cur !== ver
        ? `<button type="button" class="k g" data-mktinst="${esc(nm)}" ${M.busy ? 'disabled' : ''}>${M.busy === nm ? '安裝中…' : cur ? '更新' : '一鍵安裝'}</button>`
        : '';
      h += `<div class="thr"><span class="hd ${cur === ver ? 'hg' : ''}"></span><span class="hn">${esc(nm)}<i class="aw">${esc(ver)}${p.paid ? ' · 付費' : ''}</i></span><span class="hv">${esc(st)}</span>${btn}<div class="thm">${esc(String(p.summary || ''))}</div></div>`;
    }
    if (M.msg) h += `<div class="thm bad" role="alert">${esc(M.msg)}</div>`;
    return h;
  }
  async function tierLoad() {
    if (hasAct('skipstop_tier_get') !== true || (S.tier && S.tier.loading)) return;
    S.tier = Object.assign(S.tier || {}, { loading: true });
    try { const r = await act('skipstop_tier_get', {}); S.tier = { r, at: Date.now() }; }
    catch (e) { S.tier = { err: errText(e), at: Date.now() }; }
    render();
  }
  async function tierPick(tier) {
    const T = S.tier || {};
    S.tier = Object.assign({}, T, { busy: tier, msg: '' });
    render();
    try { const r = await act('skipstop_tier_set', { tier }); S.tier = { r, at: Date.now() }; }
    catch (e) { S.tier = Object.assign({}, T, { busy: '', msg: errText(e) }); }
    render();
  }
  function tierHtml() {
    if (hasAct('skipstop_tier_get') !== true) return '';
    const T = S.tier;
    if (!T || (!T.r && !T.err)) { if (!T || !T.loading) setTimeout(tierLoad, 0); return '<div class="thh"><b>省時續跑檔次</b><span class="hv">讀取中…</span></div>'; }
    const savedWeek = S.ts && S.ts.r && S.ts.r.boxes && S.ts.r.boxes.week ? fmtMs(S.ts.r.boxes.week.confirmed_ms || 0) : '0 秒';
    const head = `<div class="thh"><b>省時續跑檔次</b><span class="hv">本週已省 ${esc(savedWeek)}</span><button type="button" class="k" data-tierreload="1" title="重讀檔次設定">重讀</button></div>`;
    if (T.err) return head + `<div class="thr bad">${esc(T.err)}</div>`;
    const d = T.r || {}, p = d.policy || {}, rs = d.repo_settings || {};
    const eff = String(d.effective_tier || 'normal');
    const canSet = hasAct('skipstop_tier_set') === true;
    const tiers = [
      ['strict', '🔒 嚴格', '同版完全相符 · 每 5 次抽 1 · 有變全刀 · 失敗重跑 · Mac + Win', '不省'],
      ['normal', '⚖️ 一般（預設）', '改動碰不到也沿用 · 每 10 次抽 1 · 只跑受影響刀 · 主要平台', '省'],
      ['fast', '⚡ 快速（需硬體簽章）', '改動碰不到也沿用 · 每 20 次抽 1 · 只跑受影響刀 · 裝不下延後（未簽章退回嚴格）', '省'],
    ];
    let h = head + '<div class="thr">';
    for (const [id, lbl, desc, tag] of tiers) {
      const on = eff === id;
      h += `<button type="button" class="k ${on ? 'g' : ''}" data-tierpick="${id}" ${!canSet || T.busy ? 'disabled' : ''} title="${esc(desc)}">${lbl}（${tag}）${on ? ' ✓' : ''}</button>`;
    }
    h += '</div>';
    h += '<div class="thm">目前只記錄於本機設定，一期尚未接入 CI 執行；無硬體簽章之放寬設定會自動退回嚴格。</div>';
    if (d.fell_back_to_strict) {
      h += '<div class="thm" role="status">未完成硬體簽章驗證，已自動退回嚴格。</div>';
    }
    // f163c REVIEW-post884 M1:aibd plugins_page.rs 回的 dropped 是「刪掉最舊的幾組」的數字(presets.drain),不是清單
    const dropN = Number(d.dropped);
    if (Number.isInteger(dropN) && dropN > 0) {
      h += `<div class="thm" role="status">範本超過 20 組，已刪掉最舊的 ${dropN} 組</div>`;
    }
    const reuseLbl = p.reuse === 'main_exact' ? '同版完全相符' : p.reuse === 'exact' ? '同機完全相符' : '改動碰不到也沿用';
    const knivesLbl = p.knives === 'changed_all' ? '有變的全跑' : p.knives === 'affected' ? '只跑受影響的' : '只跑改動刀';
    const retryLbl = p.retry_miss === 'on' ? '當場重跑一次' : '不重跑';
    const platLbl = p.platforms === 'all' ? 'Mac + Win' : '主要平台';
    const ovLbl = p.overflow === 'add_cell' ? '加格全跑' : '照優先序延後';
    const fields = [
      ['什麼時候沿用', reuseLbl, '省很多'],
      ['沿用幾次抽查', p.spot ? `每 ${p.spot} 次抽 1` : '每 10 次抽 1', '省一點'],
      ['刀跑哪些', knivesLbl, p.knives === 'changed_all' ? '不省' : '省很多'],
      ['沒咬的刀', retryLbl, '不省'],
      ['測試平台', platLbl, '不省'],
      ['時間裝不下時', ovLbl, p.overflow === 'defer' ? '只是挪後' : '不省'],
      ['每格最長分鐘', `${p.cell_max_min || 90} 分`, '不省'],
      ['同時最多格數', `${p.cells_max || 5} 格`, '不省'],
    ];
    h += '<details class="thr"><summary class="hv">八欄明細與全倉設定（我的組合：' + (Array.isArray(d.presets) ? d.presets.length : 0) + ' 組）</summary>';
    for (const [k, v, s] of fields) {
      h += `<div class="thm">${esc(k)}：<b>${esc(String(v || ''))}</b> · <span class="hv">${s}</span></div>`;
    }
    const hrs = Array.isArray(rs.merge_batch_hours) ? rs.merge_batch_hours.join('/') : '10/16/22';
    h += `<div class="thm">全倉設定：平台無關刀搬 Linux=${rs.linux_knives ? '開' : '關'} · 刀長度檢查=${rs.knife_length_check === 'warn' ? '只提醒' : '開'}（單支 ≤ ${rs.script_max_knives || 140} 把、≤ ${rs.script_max_min || 15} 分）· 合併批次=${esc(hrs)} (${esc(String(rs.merge_batch_tz || 'Asia/Taipei'))})</div></details>`;
    if (T.msg) h += `<div class="thm bad" role="alert">${esc(T.msg)}</div>`;
    return h;
  }
  async function thLoad() {
    if (hasAct('test_host_list') !== true || (S.th && S.th.loading)) return;
    S.th = Object.assign(S.th || {}, { loading: true });
    try { const r = await act('test_host_list', {}); S.th = { list: Array.isArray(r && r.machines) ? r.machines : [], pinned: Number(r && r.pinned) || 0, max: Number(r && r.max) || 0, at: Date.now(), row: (S.th && S.th.row) || {} }; }
    catch (e) { S.th = { err: thErr(e && (e.code || e.reason)) || errText(e), at: Date.now(), row: {} }; }
    render();
  }
  // ebab8 11:56(客戶 Windows 截圖):客戶 ssh 設定裡的 9 台全列、8 台紅叉,客戶以為壞了,還把他的主機名與位址印在畫面上
  // ⇒ 沒有登記過的測試機時整塊收成一行 + 「從這台的 ssh 設定加」;按了才列 AIB 能用的那幾台;不能用的不列名字、不出紅叉,只寫幾台
  const TH_L = {
    'zh-TW': { none: '開發用的功能,按了才讀這台的 ssh 設定', open: '從這台的 ssh 設定加', close: '收起', bad: (n) => `另有 ${n} 台不能用(ssh 設定看不懂或要經跳板),沒有列出`, empty: '這台的 ssh 設定裡沒有 AIB 能用的機器' },
    en: { none: "A developer feature; this computer's ssh settings are read only when you press the button", open: "Add from this computer's ssh settings", close: 'Hide', bad: (n) => `${n} more can't be used (ssh settings not understood, or reached through a jump host); not listed`, empty: "This computer's ssh settings have no machine AIB can use" },
    ja: { none: '開発者向けの機能です。ボタンを押したときだけこのパソコンの ssh 設定を読みます', open: 'このパソコンの ssh 設定から追加', close: '閉じる', bad: (n) => `ほかに ${n} 台は使えません(ssh 設定が読めない、または踏み台経由)。一覧には出していません`, empty: 'このパソコンの ssh 設定には AIB が使えるマシンがありません' },
    ko: { none: '개발자용 기능입니다. 버튼을 눌렀을 때만 이 컴퓨터의 ssh 설정을 읽습니다', open: '이 컴퓨터의 ssh 설정에서 추가', close: '접기', bad: (n) => `그 밖에 ${n}대는 쓸 수 없습니다(ssh 설정을 읽을 수 없거나 점프 호스트 경유). 목록에 넣지 않았습니다`, empty: '이 컴퓨터의 ssh 설정에는 AIB가 쓸 수 있는 기기가 없습니다' },
  };
  function thHalf(T, m) {
    const R = T.row[String(m.name || '')] || {};
    const half = 'half' in m ? m.half === true : R.half || (m.pinned === false && R.gateless);
    return half;
  }
  // ebab8 12:00:客戶沒按就什麼都不讀(連 ssh 設定檔都不先讀);按過一次這個瀏覽器記住,開發機下次自己讀
  const thWant = () => { if (S.thWant != null) return S.thWant; try { return localStorage.getItem('aib.th.want') === '1'; } catch (e) { return false; } };
  const thWantSet = (v) => { S.thWant = v; try { if (v) localStorage.setItem('aib.th.want', '1'); else localStorage.removeItem('aib.th.want'); } catch (e) { /* 存不了就只記這一頁 */ } };
  // f163c 5d00 N1:測試機塊的標題 / 狀態 / 鈕也要四語(原本寫死中文)
  const TH_X = {
    'zh-TW': {"title": "測試機", "loading": "讀取中…", "recv": "收據可用 ", "max": " / 最多 ", "reload": "重讀", "reloadT": "重讀這台 ssh 設定裡的機器", "busy": "處理中…", "ok": "✅ 可跑收據", "half": "⚠️ 寫了但收據閘沒回", "add": "加", "rm": "收回這台", "notyet": "還沒加", "added": "已加上,之後收據可以排到這台", "removed": "已收回", "gateless": "已寫上,但那台的收據閘沒回;可以收回這台", "tap": "點一下開", "given": "已交給夥伴管", "give": "交給夥伴管", "cantGive": "這台不能交", "unsupported": "不支援", "evid": "交給夥伴的測試機,上面的收據在那段時間不能當證據", "testNo": "測試機還不能交給夥伴管:這一版 AIB 還沒有;要管就先在「伺服器」加一台主機"},
    en: {"title": "Test machines", "loading": "Loading…", "recv": "Usable for receipts ", "max": " / max ", "reload": "Reload", "reloadT": "Read the machines in this computer's ssh settings again", "busy": "Working…", "ok": "✅ Runs receipts", "half": "⚠️ Written, but its receipt gate didn't answer", "add": "Add", "rm": "Take back", "notyet": "Not added", "added": "Added; receipts can now be queued on this machine", "removed": "Taken back", "gateless": "Written, but that machine's receipt gate didn't answer; you can take it back", "tap": "Click to open", "given": "Already handed to the partner", "give": "Hand to the partner", "cantGive": "Can't hand this one over", "unsupported": "Not supported", "evid": "While a test machine is handed to the partner, receipts run on it during that time don't count as evidence", "testNo": "Test machines can't be handed to the partner yet: this version of AIB doesn't have it; to manage one, add it as a host under “Servers” first"},
    ja: {"title": "テスト機", "loading": "読み込み中…", "recv": "レシート可 ", "max": " / 最大 ", "reload": "再読み込み", "reloadT": "このパソコンの ssh 設定にあるマシンを読み直す", "busy": "処理中…", "ok": "✅ レシート実行可", "half": "⚠️ 書き込んだがレシートゲートが応答なし", "add": "追加", "rm": "外す", "notyet": "未追加", "added": "追加しました。以後レシートをこのマシンに回せます", "removed": "外しました", "gateless": "書き込みましたが、そのマシンのレシートゲートが応答しません。外すことができます", "tap": "クリックで開く", "given": "パートナーに任せ済み", "give": "パートナーに任せる", "cantGive": "このマシンは任せられません", "unsupported": "未対応", "evid": "パートナーに任せている間、そのテスト機で回したレシートは証拠になりません", "testNo": "テスト機はまだパートナーに任せられません(この版の AIB にはありません)。管理するには先に「サーバー」でホストとして追加してください"},
    ko: {"title": "테스트 기기", "loading": "읽는 중…", "recv": "영수증 가능 ", "max": " / 최대 ", "reload": "다시 읽기", "reloadT": "이 컴퓨터의 ssh 설정에 있는 기기를 다시 읽기", "busy": "처리 중…", "ok": "✅ 영수증 실행 가능", "half": "⚠️ 기록했지만 영수증 게이트가 응답 없음", "add": "추가", "rm": "빼기", "notyet": "추가 안 함", "added": "추가했습니다. 이제 영수증을 이 기기에 보낼 수 있습니다", "removed": "뺐습니다", "gateless": "기록했지만 그 기기의 영수증 게이트가 응답하지 않습니다. 뺄 수 있습니다", "tap": "클릭해서 열기", "given": "이미 파트너에게 맡김", "give": "파트너에게 맡기기", "cantGive": "이 기기는 맡길 수 없습니다", "unsupported": "지원하지 않음", "evid": "파트너에게 맡긴 동안 그 테스트 기기에서 돌린 영수증은 증거가 되지 않습니다", "testNo": "테스트 기기는 아직 파트너에게 맡길 수 없습니다(이번 버전 AIB에는 없음). 관리하려면 먼저 「서버」에서 호스트로 추가하세요"},
  };
  const thX = () => TH_X[typeof askLang === 'function' ? askLang() : 'zh-TW'] || TH_X['zh-TW'];
  // f163c b67 / de39 N1:不能用原因、加 / 收回的錯誤也跟頁面語言(中文仍讀 TH_WHY / TH_ERR 原表)
  const TH_WHY_L = {
    en: {"reached_through_something_else": "Reached through a jump host; not supported", "config_has_match_or_include": "The ssh settings use Match / Include and can't be read reliably; not supported", "no_identity_file": "This machine's ssh settings can't be understood", "identity_file_not_absolute": "This machine's ssh settings can't be understood", "value_with_a_token": "This machine's ssh settings can't be understood", "port": "This machine's ssh settings can't be understood", "aibd_own_key": "This entry uses AIB's own key"},
    ja: {"reached_through_something_else": "踏み台経由のため未対応", "config_has_match_or_include": "ssh 設定に Match / Include があり正確に読めないため未対応", "no_identity_file": "このマシンの ssh 設定が読み取れません", "identity_file_not_absolute": "このマシンの ssh 設定が読み取れません", "value_with_a_token": "このマシンの ssh 設定が読み取れません", "port": "このマシンの ssh 設定が読み取れません", "aibd_own_key": "この設定は AIB 自身の鍵を使っています"},
    ko: {"reached_through_something_else": "점프 호스트를 거쳐야 해서 지원하지 않음", "config_has_match_or_include": "ssh 설정에 Match / Include가 있어 정확히 읽을 수 없어 지원하지 않음", "no_identity_file": "이 기기의 ssh 설정을 이해할 수 없음", "identity_file_not_absolute": "이 기기의 ssh 설정을 이해할 수 없음", "value_with_a_token": "이 기기의 ssh 설정을 이해할 수 없음", "port": "이 기기의 ssh 설정을 이해할 수 없음", "aibd_own_key": "이 설정은 AIB 자체 키를 사용합니다"},
  };
  const TH_ERR_L = {
    en: {"no_receipt_gate": "That machine has no receipt gate yet; install it first, then add it", "not_a_mac": "That machine isn't a Mac, so it can't run receipts yet", "owner_login_failed": "Your own ssh can't reach that machine", "write_failed": "Couldn't write that machine's settings", "remove_failed": "Couldn't take it back: that machine's settings can't be changed", "owner_check_not_approved": "The owner check wasn't confirmed", "owner_check_unavailable": "This computer can't show the owner-check window right now", "page_only": "Only this computer's built-in AIB page can change test machines", "finger_denied": "The owner check was refused", "finger_expired": "The owner-check window timed out", "finger_timeout": "Timed out waiting for the owner check"},
    ja: {"no_receipt_gate": "そのマシンにはまだレシートゲートがありません。先に入れてから追加してください", "not_a_mac": "そのマシンは Mac ではないため、まだレシートを回せません", "owner_login_failed": "あなた自身の ssh でそのマシンにつながりません", "write_failed": "そのマシンの設定に書き込めません", "remove_failed": "外せません:そのマシンの設定を変更できません", "owner_check_not_approved": "本人確認が通りませんでした", "owner_check_unavailable": "このパソコンは今、本人確認の画面を出せません", "page_only": "テスト機を変更できるのはこのパソコンの AIB 内蔵ページだけです", "finger_denied": "本人確認が拒否されました", "finger_expired": "本人確認の画面が時間切れになりました", "finger_timeout": "本人確認の待ち時間が切れました"},
    ko: {"no_receipt_gate": "그 기기에는 아직 영수증 게이트가 없습니다. 먼저 설치한 뒤 추가하세요", "not_a_mac": "그 기기는 Mac이 아니어서 아직 영수증을 돌릴 수 없습니다", "owner_login_failed": "본인 ssh로 그 기기에 연결되지 않습니다", "write_failed": "그 기기의 설정에 쓸 수 없습니다", "remove_failed": "뺄 수 없습니다: 그 기기의 설정을 바꿀 수 없습니다", "owner_check_not_approved": "본인 확인이 되지 않았습니다", "owner_check_unavailable": "이 컴퓨터는 지금 본인 확인 창을 띄울 수 없습니다", "page_only": "테스트 기기는 이 컴퓨터의 AIB 내장 페이지에서만 바꿀 수 있습니다", "finger_denied": "본인 확인이 거부되었습니다", "finger_expired": "본인 확인 창 시간이 지났습니다", "finger_timeout": "본인 확인을 기다리다 시간이 지났습니다"},
  };
  const thL = () => (typeof askLang === 'function' ? askLang() : 'zh-TW');
  const thWhy = (c) => ((TH_WHY_L[thL()] || {})[c]) || TH_WHY[c] || '';
  const thErr = (c) => (typeof fpSay === 'function' ? fpSay : (t) => t)(((TH_ERR_L[thL()] || {})[c]) || TH_ERR[c] || '');
  // ebab8 14:00 B5(真 AIB 實看):客戶走指引「加一台主機」時,這一行用客戶字;開發用說明留在鈕的提示
  const TH_G = { 'zh-TW': { title: '主機', open: '加一台主機' }, en: { title: 'Hosts', open: 'Add a host' }, ja: { title: 'ホスト', open: 'ホストを追加' }, ko: { title: '호스트', open: '호스트 추가' } };
  const thGuide = () => { try { const G = S.guide, c = G && !gSkipped() && guideCur(G); return !!(c && c.proj === 'hosts' && c.step && c.step.id === 'host_add'); } catch (e) { return false; } };
  const thClosed = (none, open) => { const g = thGuide() && (TH_G[typeof askLang === 'function' ? askLang() : 'zh-TW'] || TH_G['zh-TW']);
    return g ? `<div class="thh"><b>${esc(g.title)}</b><button type="button" class="k" data-thopen="1" title="${esc(none)}">${esc(g.open)}</button></div>`
      : `<div class="thh"><b>${esc(thX().title)}</b><span class="hv">${esc(none)}</span><button type="button" class="k" data-thopen="1">${esc(open)}</button></div>`; };
  const thW = () => TH_L[typeof askLang === 'function' ? askLang() : 'zh-TW'] || TH_L['zh-TW'];
  function testHostHtml() {
    if (hasAct('test_host_list') !== true) return '';
    const T = S.th;
    if (!T && !thWant()) return thClosed(thW().none, thW().open);
    if (!T || (!T.list && !T.err)) { if (!T || !T.loading) setTimeout(thLoad, 0); return `<div class="thh"><b>${esc(thX().title)}</b><span class="hv">${esc(thX().loading)}</span></div>`; }
    let h = `<div class="thh"><b>${esc(thX().title)}</b><span class="hv">${T.list ? esc(thX().recv + T.pinned + (T.max ? thX().max + T.max : '')) : ''}</span><button type="button" class="k" data-threload="1" title="${esc(thX().reloadT)}">${esc(thX().reload)}</button></div>`;
    if (T.err) return h + `<div class="thr bad">${esc(T.err)}</div>`;
    const W = thW(), isHalf = (m) => thHalf(T, m), open = !!T.open || thWant();
    const kept = T.list.filter((m) => m.pinned || isHalf(m)), canAdd = T.list.filter((m) => m.usable && !m.pinned && !isHalf(m)), nBad = T.list.length - kept.length - canAdd.length;
    if (!kept.length && !open) return thClosed(W.none, W.open);
    const shown = open ? kept.concat(canAdd) : kept;
    if (open) h += `<div class="thr hv">${esc(shown.length ? '' : W.empty)}${nBad > 0 ? esc((shown.length ? '' : ' · ') + W.bad(nBad)) : ''}<button type="button" class="k" data-thclose="1">${esc(W.close)}</button></div>`;
    for (const m of shown) {
      const nm = String(m.name || ''), R = T.row[nm] || {};
      // b4297 f942d22137:list 每列有 half(寫上了但閘沒回);舊版沒這欄才用頁面自己記的
      const half = isHalf(m);
      const dot = m.pinned ? 'hg' : half ? 'hq' : m.usable ? '' : 'hr';
      const X = thX(), st = R.busy ? (R.wait || X.busy) : m.pinned ? X.ok : half ? X.half : m.usable ? X.notyet : '❌ ' + (thWhy(m.why) || X.unsupported);
      const btn = !m.usable && !m.pinned && !half ? '' : `<button type="button" class="k ${m.pinned || half ? '' : 'g'}" data-th${m.pinned || half ? 'rm' : 'add'}="${esc(nm)}" ${R.busy ? 'disabled' : ''}>${esc(m.pinned || half ? X.rm : X.add)}</button>`;
      h += `<div class="thr"><span class="hd ${dot}"></span><span class="hn">${esc(nm)}<i class="aw">${esc(String(m.address || ''))}</i></span><span class="hv">${esc(st)}</span>${btn}${R.msg ? `<div class="thm ${R.bad ? 'bad' : ''}" role="${R.bad ? 'alert' : 'status'}">${esc(R.msg)}</div>` : ''}</div>`;
    }
    return h;
  }
  async function thAct(kind, name) {
    const T = S.th; if (!T || !T.row) return;
    const R = (T.row[name] = { busy: true });
    render();
    try {
      const r = await fingerAct(kind === 'add' ? 'test_host_add' : 'test_host_remove', { name }, (t, sec) => { R.wait = FP_SEE; render(); });
      if (r && r.reason === 'gate_did_not_answer') { T.row[name] = { half: true, gateless: true, msg: thX().gateless, bad: true }; }
      else T.row[name] = { msg: kind === 'add' ? thX().added : thX().removed };
    } catch (e) { const c = e && (e.code || e.reason || (e.body && e.body.code)); T.row[name] = { msg: thErr(c) || errText(e), bad: true }; }
    S.th = Object.assign(S.th, { loading: false }); await thLoad();
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
    // 7ebca ceaca0552(N-ws-evcards-rest):舊帳檔案三種
    'ledger.review': '審查有退回', 'ledger.receipt': '收據紅', 'ledger.handoff': '交接單卡住',
  };
  // aibd 的卡(GET /api/v1/slots):新形狀 level / tag / text / card / since / rec / state / src / actions;
  // 舊形狀(slot / kind / item / why / at)照讀。⑤ 與「我寄的信沒送到 / 沒讀」aibd 沒有來源,頁面照算
  // a9136 派工(0eed7 VERIFY-humanpath L1):aibd 仍記 stop_unconfirmed(macOS 證不了全部子程序都走了),
  // 頁面照 ctl.rs stopped_but_unprovable 同一判斷:不再佔位、沒有活下來的、沒有讀不了的、候選 0 ⇒ 不出注意卡、寫「已停(看得到的都停了)」。
  // 7ebca 6f547e5998(第 2 件):卡槽 4 message.unclosed「N 封信收了沒結」;7ebca c153b852f5:提醒過 1 次又放 3 分鐘沒結、或提醒滿 2 次才出卡
  function unclosedWhy(c, s) {
    const it = typeof c.item === 'string' ? c.item : '';
    // 7ebca 20:43:這張卡有三種來源(提醒滿兩次 / 一小時提醒滿 6 次 / 這一席現在不收字),卡上分不出 ⇒ 先寫 aibd 的 state,再接下一步;規則放括號當說明
    const st = typeof c.state === 'string' && c.state ? c.state + ';' : '';
    return { text: (s ? seatName(s) + ' ' : '') + (it || '有信收了沒結'), st: st + '請它結案,或寫明在等誰(能提醒的席先提醒;提醒後 3 分鐘還沒結才出這張卡)' };
  }
  function stopLooksClean(v) {
    if (!v || !v.stop || typeof v.stop !== 'object') return false; // 沒有停止的明細就判不了
    const st = v.stop, c = st.candidates && typeof st.candidates === 'object' ? st.candidates : {};
    // b4297 74f0316b(aibd 同一判斷):stop.reason observed_clean;a9136:holds_slot false。兩個欄位哪個在都認
    // b4297 8f4a0396c:reason 要 observed_clean,holds_slot 是加條件(乾淨時一定 false);沒 reason 欄的舊回應才只看 holds_slot false
    if (v.holds_slot === true || (st.reason != null ? st.reason !== 'observed_clean' : v.holds_slot !== false)) return false;
    const empty = (a) => a == null || (Array.isArray(a) && !a.length);
    if (!empty(st.survivors) || !empty(c.unreadable) || Number(c.unreadable_more || 0) !== 0) return false;
    // a9136 10:01(fdd75dd52)stop_seen_clean:只看 survivors / unreadable;候選個數不比(短命系統程序中途結束會差 1)。
    // observed_clean 本身已保證候選都查完、沒有讀不到的
    return true;
  }
  function pageHides(c) {
    if (!c || c.kind !== 'session.stop_unconfirmed' || (Number(c.level || c.slot) || 6) !== 2) return false;
    const se = S.sessions.find((y) => (y.id || y.session_id) === c.session_id);
    return stopLooksClean(c) || stopLooksClean(se);
  }
  // 0eed7 VERIFY-a0a62a94c L1:頁面濾掉的 aibd 卡也要從 aibd 給的件數扣掉(分頁標、「全部」、類別框),不然留空框
  function slotCount(k) {
    const n = Number(S.slotCounts && S.slotCounts[String(k)]) || 0;
    const hid = (S.aslots || []).filter((c) => (Number(c.level || c.slot) || 6) === k && !S.dismissed.has(c.card_id) && pageHides(c)).length;
    return Math.max(0, n - hid);
  }
  function aibdCards(list) {
    const L = [];
    for (const c of list || S.aslots) {
      if (S.dismissed.has(c.card_id)) continue;
      const rf = c.kind === 'seat.rejoin_failed' && c.seat_id;
      if (rf && rejoinedSince(c.seat_id, c.since || c.at)) continue; // 已重開:這張 aibd 不會自己收
      // aibd 自己出的「停了但沒確認」2 號卡(0eed7 r1:slots.rs 出):那場照同一判斷是乾淨停的就不列;b4297 74f0316b 起乾淨的進 6 號通知,照列
      if (pageHides(c)) continue;
      const s = c.seat_id ? seatById(c.seat_id) : null;
      const lv = Number(c.level || c.slot) || 6;
      const str = (v) => (typeof v === 'string' && v ? v : '');
      // 等回應 / 說忙沒動:標題寫哪一席怎麼了,它寫的工作項目放狀態
      const stuck = !c.text && (c.kind === 'seat.waiting' || c.kind === 'seat.quiet') && s;
      // 519d0 03:58 / 0eed7 VERIFY-c18 L1:合回被拒的卡寫原因 + 要看哪些設定 + 下一步(aibd 的 item 是「合回停住:作者倉設定 <why>」)
      const mw = c.kind === 'seat.merge_unsafe' ? mergeWhy(c) : c.kind === 'message.unclosed' && !c.text ? unclosedWhy(c, s) : null;
      const text = (mw && mw.text) || str(c.text) || (stuck ? seatName(s) + ' ' + KIND_TEXT[c.kind] : '') || (c.kind === 'seat.usage' && c.pct != null ? (s ? seatName(s) + ' ' : '') + '用量 ' + c.pct + '%' : '') || str(c.item) || (str(c.line) ? (s ? seatName(s) + ' ' : '') + str(c.line) : '') || KIND_TEXT[c.kind] || str(c.why) || (s ? seatName(s) : String(c.kind || ''));
      L.push({
        lv, tag: str(c.tag) || LV_TAG[lv], text, ref: str(c.card), t: c.since || c.at,
        // 舊形狀的 why(席那一行的「為什麼」)放狀態;對不上的留空(SPEC §14)
        rec: str(c.rec), st: (mw && mw.st) || str(c.state) || (c.text ? '' : stuck ? str(c.item) : str(c.why)), src: str(c.src),
        who: s ? '@' + seatName(s) : '', seat: s ? s.seat_id : null, card: c.card_id, options: c.options,
        answer: (c.actions || []).includes('answer'), actions: c.actions || [], restart: rf ? c.seat_id : null,
        quota: lv === 3 && str(c.why) === '額度' && s ? s.seat_id : null, // 519d0 06:45 第 2 點:額度用完 ⇒ 換模型鈕
        // 7ebca 80b0cade9:舊帳的信(dm.hold / queued / unread / task)帶 open {thread_id, message_id} ⇒ 點了開那串、捲到那則
        open: c.kind !== 'seat.asked' && c.open && typeof c.open === 'object' && str(c.open.thread_id) ? { tid: str(c.open.thread_id), mid: str(c.open.message_id) } : null,
        // 7ebca a1eb8c735:審查意見跟真人需求不同(seat.asked)⇒ 卡上兩顆鈕,另給「打開串」捲到審查那則;卡上不放訊息內容
        ask: c.kind === 'seat.asked' && c.open && typeof c.open === 'object' && str(c.open.thread_id) ? { tid: str(c.open.thread_id), mid: str(c.open.message_id) || str(c.open.first_msg_id), pmid: str(c.open.person_message_id), kind: str(c.open.kind) || 'review_vs_person', first: str(c.open.first_msg_id), second: str(c.open.second_msg_id) } : null,
        // 審查 / 收據 / 交接卡:open {doc: 檔名} ⇒ 點了開那份文件
        doc: c.open && typeof c.open === 'object' && str(c.open.doc) ? str(c.open.doc) : '',
        pin: str(c.pin), // 7ebca e74d1dfa9:掛到的項目(slot_pin)
        // 7ebca fbbc3c3db:要你決定的建議(idea.consent:item 標題 / rec 做法 / why 好處 / state 代價)、真人未審清單(open.list / open.count)
        kind: str(c.kind), origin: str(c.origin), why: c.kind === 'idea.consent' ? str(c.why) : '',
        list: c.open && Array.isArray(c.open.list) ? c.open.list.filter((y) => y && typeof y.id === 'string') : null,
        count: c.open && Number.isFinite(Number(c.open.count)) ? Number(c.open.count) : null,
        unread: c.open && Number(c.open.unread) > 0 ? Number(c.open.unread) : 0, // 7ebca 525ee7fd8:後審帳讀不了的行數
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
  // 只用網頁時「剛才發生什麼」只看得到這裡(519d0 22:19):收據(7ebca 62153be32a)、送回倉(a9136 838c1c09a1)、受信任(7b81e 1b6ce17d10)。
  // 欄位:收據 {run_id, seat, head, host, state, green, reasons};送回倉 {seat_id, repo, branch, head, commits};受信任 {seat, until?}
  const ACT_EV = { 'seat.receipt_requested': 1, 'seat.receipt_started': 1, 'seat.receipt_done': 1, 'seat.receipt_withdrawn': 1, 'seat.sent_to_repo': 1, 'seat.trusted': 1, 'seat.untrusted': 1 };
  S.actv = S.actv || []; S.actSeen = S.actSeen || new Set();
  function actKeep(e) {
    const k = String(e.global_seq != null ? e.global_seq : e.sequence != null ? e.type + ':' + e.sequence : e.type + ':' + e.occurred_at);
    if (S.actSeen.has(k)) return; S.actSeen.add(k);
    const p = e.payload || {}, seat = String(p.seat_id || p.seat || '');
    if (!seat) return;
    S.actv.push({ t: String(e.occurred_at || ''), type: e.type, seat, p });
    if (S.actv.length > 200) S.actv.splice(0, S.actv.length - 200);
  }
  function actText(x) {
    const p = x.p, h12 = p.head ? ' · 頭 ' + String(p.head).slice(0, 12) : '', why = Array.isArray(p.reasons) && p.reasons.length ? ':' + p.reasons.map((w) => rcpReason(String(w))).join('、') : '';
    switch (x.type) {
      case 'seat.receipt_requested': return { c: '', h: '🧾 排了收據' + h12 + (p.host ? ' · ' + p.host : '') + rcpKnifeCard(p) };
      case 'seat.receipt_started': return { c: '', h: '🧾 收據開始跑' + h12 + (p.host ? ' · ' + p.host : '') };
      case 'seat.receipt_done': return p.green ? { c: 'ok', h: '🧾 收據綠' + h12 } : p.state === 'passed_in_sandbox' ? { c: '', h: '🧾 收據沙盒內通過(略過 21 支,不算綠)' + h12 } : p.state === 'lost' ? { c: 'bad', h: '🧾 收據沒跑完:測試機失聯' + h12 } : p.state === 'done' ? { c: '', h: '🧾 收據跑完(窄,不算綠)' + h12 } : { c: 'bad', h: '🧾 收據紅' + h12 + why };
      case 'seat.receipt_withdrawn': return { c: '', h: '🧾 收據撤了' + h12 };
      case 'seat.sent_to_repo': return { c: 'ok', h: '↩ 送回「' + String(p.repo || '') + '」建了新分支 ' + String(p.branch || '') + (p.commits != null ? '(' + (Number(p.commits) | 0) + ' 個提交)' : '') + h12 };
      case 'seat.trusted': return { c: 'ok', h: '🔓 升成受信任' + (p.until ? ',到 ' + hhmm(Number(p.until) * 1000) : '') };
      case 'seat.untrusted': return { c: '', h: '🔒 收回受信任' };
    }
    return { c: '', h: x.type };
  }
  // 席窗:這一席的;共同串:每一席的,前面標是誰
  const actItems = (id) => S.actv.filter((x) => x.seat === id).map((x) => { const a = actText(x); return { t: x.t, h: `<div class="ev ${a.c}" data-actev="${esc(x.type)}">${esc(a.h)} · ${esc(hhmm(x.t))}</div>` }; });
  function actRows() {
    return S.actv.map((x) => { const sx = seatById(x.seat) || { seat_id: x.seat, provider: 'claude' }, a = actText(x); return { t: x.t, h: `<div class="msg tkr" data-actev="${esc(x.type)}"><div class="body ${a.c}">${logo(sx, 'sm')} @${esc(seatShort(sx))} ${esc(a.h)}</div><div class="st">${esc(hhmm(x.t))}</div></div>` }; });
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
  // b4297 01ef670be:封存的做事席留下的樹 → 事件 seat.worktree_kept / seat.worktree_removed {seat(席碼), branch, unmerged, uncommitted?, error?}
  // 519d0 01:18:有提交沒合就出一張「N 個提交沒合」的卡。自己讀事件帳(跟接回失敗同一套,但不管 aibd 槽位有沒有都讀)
  const WTR = { cur: -1, busy: false, off: false };
  S.wtKept = S.wtKept || {};
  S.seatGuest = S.seatGuest || {};
  S.seatTree = S.seatTree || {}; // b4297 第二輪:seat.opened 有 opened_by.base_head = 這一席有自己一份 git 樹 ⇒ 給「看改了什麼」(tree.js)
  const hasTree = (s) => !!(s && (S.seatTree[s.seat_id] || (s.folder_owner && s.folder_owner.how === 'tree')));
  // 自動推進計時(519d0 06:35 派工,a9136 seat_tick,ORDER-webonly 第 7 項):act seat_tick_list / _set {seat_id, every_min 5–1440, text}
  // / _clear / 急停 seat_tick_pause {on}。aibd 只在回合之間送、只送給收得了字的模式;頁面只管設定與急停。舊 aibd 不認 ⇒ 整塊不出。
  const TICK = { at: 0, busy: false };
  const tickTake = (r) => { S.ticks = new Map(((r && r.ticks) || []).filter((t) => t && t.seat_id).map((t) => [String(t.seat_id), t])); S.tickPaused = !!(r && r.paused); tickDigest(); };
  // aibd 的 digest = 那句 UTF-8 的 SHA-256 前 8 位元組(16 個十六進位字);頁面算目前每席那句的 digest,事件對得上才把字畫出來
  const TDIG = {};
  async function tickDigest() {
    const C = typeof crypto !== 'undefined' && crypto.subtle; if (!C || !S.ticks) return;
    for (const t of S.ticks.values()) {
      const txt = String(t.text || ''); if (TDIG[txt]) continue;
      try { const b = new Uint8Array(await C.digest('SHA-256', new TextEncoder().encode(txt))); TDIG[txt] = [...b.slice(0, 8)].map((x) => x.toString(16).padStart(2, '0')).join(''); } catch (e) { return; }
    }
  }
  const TICK_SKIP = { seat_not_found: '找不到這一席', seat_mode_takes_no_input: '這種模式收不了字', text_refused: '那句被擋下(格式不對)', not_running: '它沒在跑', gone: '它已收掉', unsigned_not_sent: '從檔案來的那句還沒簽章,暫不送', changed_since_approved: '字跟本人核准那版不同,不送', expired: '本人核准已過 30 天,不送;按「重新核准」', bad_signature: '核准的簽章驗不過,不送;按「重新核准」', revoked: '這筆核准已撤銷,不送', file_missing: 'ticks 資料夾裡找不到那個檔,不送', file_bad: '那個檔讀不了,不送' };
  function tickRows() {
    const out = [];
    for (const x of S.tickLog || []) {
      const s = seatById(x.seat) || { seat_id: x.seat, provider: 'claude' }, t = S.ticks && S.ticks.get(x.seat);
      // a9136 64a09bb2af:seat.tick_stuck {seat, rounds, why}(第 3 輪一筆、之後每 3 輪)
      if (x.stuck) { out.push({ t: x.at, h: `<div class="msg tkr"><div class="body bad">⏱ 自動送 ${logo(s, 'sm')} @${esc(seatShort(s))} 連續 ${x.stuck} 輪送不進去:${esc(TICK_SKIP[x.why] || x.why || '原因不明')}</div><div class="st">${esc(hhmm(x.at))}</div></div>` }); continue; }
      const txt = t && TDIG[String(t.text || '')] === x.dig ? String(t.text || '') : '';
      const what = x.sent ? `自動送 ${logo(s, 'sm')} @${esc(seatShort(s))}:${txt ? '「' + esc(txt.slice(0, 200)) + '」' : '<span class="st">(那句後來改過或已停掉)</span>'}<button type="button" class="k" data-openseat="${esc(x.seat)}" title="它怎麼回的在它的席窗">看它回了什麼</button>` : `略過一次 ${logo(s, 'sm')} @${esc(seatShort(s))}:${esc(TICK_SKIP[x.why] || x.why || '沒送成')}`;
      out.push({ t: x.at, h: `<div class="msg tkr"><div class="body">⏱ ${what}</div><div class="st">${esc(hhmm(x.at))}</div></div>` });
    }
    return out;
  }
  async function loadTicks(force) {
    if (S.tickOff || TICK.busy || (!force && Date.now() - TICK.at < 20000)) return;
    TICK.busy = true; TICK.at = Date.now();
    try { tickTake(await act('seat_tick_list', {})); render(); }
    catch (e) { if (e && (e.status === 400 || e.status === 404 || e.code === 'seat_tick_built_in_page_only' || e.reason === 'seat_tick_built_in_page_only')) { S.tickOff = true; render(); } }
    finally { TICK.busy = false; }
  }
  const TICK_WHY = {
    every_min_out_of_range: '間隔要 5 到 1440 分鐘',
    text_length: '要送的那句不能空白,也不能超過 2000 字',
    too_many_ticks: '最多 32 席能設定時送',
    seat_not_found: '找不到這一席(可能已收掉)',
    no_tick: '這一席本來就沒有設定時送',
    seat_tick_built_in_page_only: '只有內建頁、這一輪配對的分頁能設定時送',
    // a9136 b1efc3b571(11-21 01:11):設 / 恢復要本人指紋(兩步:202 ticket → 按指紋 → 帶 ticket 再送)
    owner_check_unavailable: '設定時送要本人指紋,這台的 AIB 還沒接上指紋;先不能設',
    owner_check_not_approved: '指紋沒過(沒按、按了拒絕,或那張單已用過);再按一次',
    // a9136 64a09bb2af 片 2:那句可以改從 <data>/ticks/ 的檔來(二選一)
    file_name: '檔名只能小寫英文、數字、- 和 _,結尾 .txt',
    file_missing: 'AIB 的 ticks 資料夾裡沒有這個檔',
    file_bad: '這個檔讀不了(太大或不是文字)',
    text_or_file: '要送的那句和檔案只能選一個',
  };
  const TICK_FILE_RE = /^[a-z0-9_-]{1,60}\.txt$/;
  const tsHm = (u) => (Number.isFinite(Number(u)) && Number(u) > 0 ? hhmm(new Date(Number(u) * 1000).toISOString()) : '');
  // 每席那顆的說明:來源、上次 / 下次、卡住
  const tickInfo = (t) => [t.file ? '從檔案 ' + t.file + (t.signed ? '' : '(未簽章,AIB 暫不送)') + (t.file_error ? ';讀不到:' + t.file_error : '') : '', tsHm(t.last_at) ? '上次 ' + tsHm(t.last_at) + (t.last && t.last !== 'sent' ? '(' + (TICK_SKIP[t.last] || t.last) + ')' : '') : '', tsHm(t.next_at) ? '下次 ' + tsHm(t.next_at) : '', Number(t.until) > 0 ? '核准到 ' + new Date(Number(t.until) * 1000).toLocaleDateString() : '', Number(t.stuck) > 0 ? '連續 ' + Number(t.stuck) + ' 輪送不進去' : ''].filter(Boolean).join(';');
  const tickWhy = (e) => { const k = e && (e.reason || e.code); return (typeof fpSay === 'function' ? fpSay : (t) => t)(TICK_WHY[k] || (typeof REPO_WHY === 'object' && /^finger_/.test(String(k)) && REPO_WHY[k]) || errText(e)); };
  // b1efc3b571 的 seat_tick_list 每筆多 approved / changed_since_approved:沒經指紋設的舊筆、或字跟核准那版不同(aibd 不送)⇒ 標出來,改 = 重新核准
  // a9136 54b400fdef(11-21 01:30):每筆多 not_sent(現在不會送的原因;會送 = null)與 until(核准到期 unix 秒);not_sent 不是 null 就給「重新核准」
  const tickNeedsOk = (t) => !!t && ((t.not_sent !== undefined && t.not_sent !== null) || t.approved === false || t.changed_since_approved === true);
  const tickOkWhy = (t) => (typeof fpSay === 'function' ? fpSay : (t) => t)((t && t.not_sent ? (TICK_SKIP[t.not_sent] || '現在不會送(' + t.not_sent + ')') + (/重新核准/.test(TICK_SKIP[t.not_sent] || '') ? '' : ';按「重新核准」再按一次指紋') : t && t.changed_since_approved === true ? '字跟本人核准那版不同,AIB 不送;按「重新核准」再按一次指紋' : t && t.approved === false ? '這筆是指紋上線前設的,還沒經本人核准;按「重新核准」' : ''));
  const tickWait = (T) => (ticket, sec) => { if (S.tickEdit === T) { T.msg = FP_SEE; render(); } };
  // 1a93b d756e6d3b3(GAP §8):GET version 的 capabilities.acts = 這台 aibd 認得的全部 act 種類。有清單 ⇒ true / false;舊 aibd 沒清單 ⇒ null(照舊按了才知道)
  const hasAct = (t) => { const c = S.version && S.version.capabilities; return c && Array.isArray(c.acts) ? c.acts.includes(t) : null; };
  // 519d0 11-21 06:45 第 1 點:開席沒指定模型 ⇒ aibd 帶這個品牌的預設模型(version.default_models);第 2 點:額度用完的席換模型接著做
  const MODEL_RE = /^~?[0-9A-Za-z][0-9A-Za-z._:\/-]{0,63}$/;
  const dfltModel = (id) => { const m = S.version && S.version.default_models; const v = m && typeof m === "object" ? m[String(id || "").toLowerCase()] : ""; return typeof v === "string" ? v : ""; };
  const MODEL_ERR = { default_model_shape: "模型名稱只能是英文、數字和 . _ : / -(最多 64 字)", seat_model_shape: "模型名稱只能是英文、數字和 . _ : / -(最多 64 字)", default_model_provider: "這個品牌不能設預設模型", default_model_built_in_page_only: "用戶資料夾有自己的頁時不能改預設模型", seat_model_built_in_page_only: "用戶資料夾有自己的頁時不能換模型", seat_busy: "這一席正在做事,等這一輪做完再換", stop_unconfirmed: "模型換好了,但舊的那段沒確定停掉;等它停了按「重開」", seat_not_found: "找不到這一席", seat_archived: "這一席已封存", seat_running: "這一席正在重開" };
  function modelLine(A, pv) {
    if (hasAct("default_model_set") !== true) return "";
    const cur = dfltModel(pv.id), E = A.dm;
    if (E && E.p === pv.id) return `<div class="adm"><input class="apq" data-dmin="1" value="${esc(E.v)}" placeholder="例:sonnet" aria-label="預設模型" autocomplete="off" spellcheck="false"><button type="button" class="k g" data-dmsave="1" ${E.busy ? "disabled" : ""}>存</button>${cur ? `<button type="button" class="k" data-dmclear="1" ${E.busy ? "disabled" : ""}>不設(用它自己的)</button>` : ""}<button type="button" class="k" data-dmx="1">取消</button>${E.msg ? `<div class="aerr" role="alert">${esc(E.msg)}</div>` : ""}</div>`;
    return `<div class="adm">模型:<b>${cur ? esc(cur) : "沒設(用 " + esc(pv.name) + " 自己的預設)"}</b> <button type="button" class="k" data-dmedit="1">改</button><div class="apn">沒另外指定模型的席都用這個;所有新開的 ${esc(pv.name)} 席共用</div></div>`;
  }
  async function dmSave(clear) {
    const A = S.add, PV = providers(), pv = PV.find((x) => x.key === A.provider && x.open); const E = A && A.dm;
    if (!E || !pv) return;
    const v = clear ? null : String(E.v || "").trim();
    if (v !== null && !MODEL_RE.test(v)) { E.msg = MODEL_ERR.default_model_shape; return render(); }
    E.busy = true; E.msg = ""; render();
    try { const r = await act("default_model_set", { provider: pv.id, model: v }); S.version = Object.assign({}, S.version, { default_models: (r && r.default_models) || {} }); A.dm = null; say(v ? pv.name + " 預設模型改成 " + v : pv.name + " 不設預設模型"); }
    catch (e) { E.busy = false; E.msg = MODEL_ERR[e && e.code] || actRefused(e) || errText(e); }
    render();
  }
  async function mswapGo() {
    const M = S.mswap; if (!M || M.busy) return;
    const v = String(M.v || "").trim();
    if (!MODEL_RE.test(v)) { M.msg = MODEL_ERR.seat_model_shape; return render(); }
    M.busy = true; M.msg = ""; render();
    try {
      await fingerAct("seat_model_set", { seat_id: M.seat, model: v, idem_key: idem() }); // 08de5 A11:dgrs 席 202 要指紋
      say("@" + seatName(seatById(M.seat) || { seat_id: M.seat }) + " 換成 " + v + ",接著原本的對話做");
      if (M.card) { S.dismissed.add(M.card); act("slot_dismiss", { card_id: M.card }).catch(() => S.dismissed.delete(M.card)); }
      S.mswap = null;
    } catch (e) { M.busy = false; M.msg = MODEL_ERR[e && e.code] || actRefused(e) || errText(e); }
    render(); tick(true);
  }
  const inputModes = () => { const c = S.version && S.version.capabilities; return c && Array.isArray(c.input_modes) ? c.input_modes.map(String) : ['safe', 'work']; };
  function tickTag(s) {
    if (S.tickOff || !S.ticks || !s || !inputModes().includes(String(s.mode || 'safe'))) return '';
    const t = S.ticks.get(s.seat_id);
    if (!t) return `<button type="button" class="ppo tk" data-tickask="${esc(s.seat_id)}" title="每隔幾分鐘自動送 @${esc(seatName(s))} 一句(只在它回合之間送)">⏱</button>`;
    if (tickNeedsOk(t)) return `<button type="button" class="ppo tk on pz" data-tickask="${esc(s.seat_id)}" title="${esc([tickOkWhy(t), tickInfo(t)].filter(Boolean).join(';'))}">⏱ 待核准</button>`;
    if (t.file && !t.signed) return `<button type="button" class="ppo tk on pz" data-tickask="${esc(s.seat_id)}" title="${esc(tickInfo(t))}">⏱ 檔·未簽章</button>`;
    if (Number(t.stuck) >= 3) return `<button type="button" class="ppo tk on pz bad" data-tickask="${esc(s.seat_id)}" title="${esc(tickInfo(t))}">⏱ 卡 ${Number(t.stuck) | 0} 輪</button>`;
    return `<button type="button" class="ppo tk on${S.tickPaused ? ' pz' : ''}" data-tickask="${esc(s.seat_id)}" title="${S.tickPaused ? '已全部暫停;' : ''}每 ${esc(String(t.every_min))} 分送「${esc(String(t.text || '').slice(0, 60))}」;點這裡改或停">⏱ ${S.tickPaused ? '暫停' : esc(String(t.every_min)) + ' 分'}</button>`;
  }
  function tickBar() {
    const T = S.tickEdit, s = T && seatById(T.seat); if (!T) return '';
    const has = S.ticks && S.ticks.get(T.seat);
    return `<div class="tkbar"><b>每隔幾分鐘自動送 @${esc(s ? seatShort(s) : T.seat)} 一句</b><div class="tkf">每 <input class="tkin" type="number" min="5" max="1440" step="1" data-tickf="every" value="${esc(String(T.every))}" aria-label="間隔分鐘"> 分鐘送:${T.src === 'file' ? `<input class="tkin" type="text" maxlength="64" data-tickf="file" value="${esc(T.file || '')}" placeholder="檔名.txt(放在 AIB 的 ticks 資料夾)" aria-label="檔名" autocomplete="off" spellcheck="false">` : `<input class="tkin" type="text" maxlength="2000" data-tickf="text" value="${esc(T.text)}" aria-label="要送的那句" autocomplete="off">`}<button type="button" class="k" data-ticksrc="1">${T.src === 'file' ? '改成直接打字' : '改成從檔案'}</button></div>${T.src === 'file' ? '<div class="tkn bad">從檔案來的那句現在還沒有簽章,AIB 設得了但一律不送(等簽章上線)</div>' : ''}${has && tickInfo(has) ? `<div class="tkn">${esc(tickInfo(has))}</div>` : ''}<div class="tkn">只在它回合之間送;它在忙、排隊或停著就等下一輪。第一句在設定後一個間隔才送。${S.tickPaused ? '現在全部暫停中,設定會留著、恢復才送。' : ''}${(typeof fpSay === 'function' ? fpSay : (t) => t)('設定或改要按一次本人指紋。')}</div>${tickNeedsOk(has) ? `<div class="tkn bad">${esc(tickOkWhy(has))}</div>` : ''}${T.msg ? `<div class="tkn bad">${esc(T.msg)}</div>` : ''}<div class="tkb"><button type="button" class="k" data-tickx="1">取消</button>${has ? '<button type="button" class="k" data-tickclr="1">停掉這一席的</button>' : ''}<button type="button" class="k g" data-tickgo="1" ${T.busy ? 'disabled' : ''}>${tickNeedsOk(has) ? '重新核准' : has ? '改' : '開始'}</button></div></div>`;
  }
  const tickStop = () => (S.tickOff || !S.ticks || (!S.ticks.size && !S.tickPaused) ? '' : S.tickPaused
    ? `<button type="button" class="ib tkstop on" data-tickpause="0" title="自動送全部暫停中(設定都留著);按這裡恢復">⏸ 自動送已暫停 · 恢復</button>`
    : `<button type="button" class="ib tkstop" data-tickpause="1" title="急停:馬上停掉所有席的自動送(設定留著,之後可恢復)">⏸ 全部暫停自動送(${S.ticks.size} 席)</button>`);
  const treeBtn = (s) => (hasTree(s) && !TREE_HOLD ? `<button type="button" class="ppo" data-tree-seat="${esc(s.seat_id)}" data-tree-name="${esc(seatName(s))}" title="只讀看 @${esc(seatName(s))} 自己那一份:改了什麼、提交、檔案">改動</button>` : '');
  async function loadWorktree() {
    if (WTR.off || WTR.busy) return;
    WTR.busy = true;
    try {
      if (WTR.cur < 0) WTR.cur = Math.max(0, (await evTail()) - 1500);
      for (let k = 0; k < 4; k++) {
        const r = await evAfter(WTR.cur, 500), ev = (r && r.events) || [];
        for (const e of ev) if (ACT_EV[e.type]) actKeep(e); // 收據 / 送回倉 / 受信任(519d0 22:19):這一圈不管 aibd 槽位有沒有都讀
        // 519d0 自測 H1:seat.opened 的 opened_by.with_seat = 這一席是借誰的資料夾開的(或開在誰倉裡的自己一棵樹)⇒ 它不是資料夾主人,不能再被 with_seat
        for (const e of ev) if (e.type === 'seat.opened' && e.payload && e.payload.seat_id && e.payload.opened_by && e.payload.opened_by.with_seat) S.seatGuest[e.payload.seat_id] = String(e.payload.opened_by.with_seat);
        for (const e of ev) if (e.type === 'seat.opened' && e.payload && e.payload.seat_id && e.payload.opened_by && /^[0-9a-f]{40}$/.test(String(e.payload.opened_by.base_head || ''))) S.seatTree[e.payload.seat_id] = true;
        for (const e of ev) if ((e.type === 'seat.worktree_kept' || e.type === 'seat.worktree_removed') && e.payload && e.payload.seat) S.wtKept[e.payload.seat] = { at: e.occurred_at, kind: e.type, branch: e.payload.branch, unmerged: Number(e.payload.unmerged) || 0, dirty: Number(e.payload.uncommitted) || 0, err: e.payload.error };
        // 0eed7 VERIFY-c18 L1:合回被作者那份的 git 設定擋下 → 事件 seat.merge_unsafe {seat_id, why};aibd 沒開槽位卡,頁面自己出一張
        // 0eed7 VERIFY-tick L1:自動送的那句不在共同對話串 ⇒ 頁面從事件 seat.tick_sent / _skipped 畫一行(那句用 digest 對回目前的設定)
        for (const e of ev) if (e.type === 'seat.tick_stuck' && e.payload && (e.payload.seat || e.payload.seat_id)) { const L = (S.tickLog = S.tickLog || []), k = String(e.event_id || e.id || e.global_seq || e.occurred_at + 'stuck'); if (!L.some((x) => x.id === k)) { L.push({ id: k, at: String(e.occurred_at || ''), seat: String(e.payload.seat || e.payload.seat_id), stuck: Number(e.payload.rounds) || 3, why: String(e.payload.why || '') }); if (L.length > 200) L.splice(0, L.length - 200); } }
        for (const e of ev) if ((e.type === 'seat.tick_sent' || e.type === 'seat.tick_skipped') && e.payload && e.payload.seat_id) { const L = (S.tickLog = S.tickLog || []), k = String(e.event_id || e.id || e.global_seq || e.occurred_at + e.payload.seat_id); if (!L.some((x) => x.id === k)) { L.push({ id: k, at: String(e.occurred_at || ''), seat: String(e.payload.seat_id), sent: e.type === 'seat.tick_sent', dig: String(e.payload.digest || ''), why: String(e.payload.reason || '') }); if (L.length > 200) L.splice(0, L.length - 200); } }
        for (const e of ev) if (e.type === 'seat.merge_unsafe' && e.payload && e.payload.seat_id) (S.mergeStop || (S.mergeStop = {}))[e.payload.seat_id] = { at: e.occurred_at, why: String(e.payload.why || '') };
        if (r && r.next_cursor != null) WTR.cur = Number(r.next_cursor);
        if (ev.length < 500) break;
      }
    } catch (e) { if (e && (e.status === 404 || e.status === 403)) WTR.off = true; }
    finally { WTR.busy = false; }
  }
  // 519d0 ORDER-round2:三席以上時誰在寫哪一份一眼看得出。席 JSON folder_owner {seat, how: folder | tree}(a9136 654162420);
  // tree {branch, base, unmerged, dirty} 有就標分支 / 沒合數(a9136 第二輪);都沒有就不標
  // 交接並清空(519d0 22:25 第 4 點):先只做「請這一席寫交接單、顯示寫好了沒」。寫好了沒:aibd 沒有 API ⇒ 看它這次之後的回話有沒有「交接單寫好了」;
  // 清空要後端(缺:act seat_clear 或同等,開新一段且帶交接單)⇒ 鈕灰掉寫原因
  // 1a93b d756e6d3b3:寫好了以 aibd 的 handoffs 為準(席叫 seat_handoff 工具);舊 aibd 沒有 ⇒ 照舊看回話裡的「交接單寫好了」
  const HO_ASK = '請照 aib handoff 寫交接單(交接、記憶整理都做完),寫好後叫 seat_handoff 工具報交接單的絕對路徑(沒有這個工具就回一句「交接單寫好了:<交接單路徑>」)。寫完先不要繼續新工作。';
  function hoState(id) {
    const H = S.ho && S.ho[id];
    const used = S.hoUsed && S.hoUsed[id];
    const ah = (S.handoffs || []).find((x) => x && x.seat_id === id && typeof x.path === 'string' && x.at !== used && (!H || String(x.at || '') >= H.at));
    if (ah) return { at: H ? H.at : ah.at, done: true, path: ah.path, by: 'aibd', wrote: ah.at };
    if (!H) return null;
    const se = lastSession(id), o = se && S.out[se.id || se.session_id];
    let path = '';
    if (o) for (const it of o.items) { if (String(it.occurred_at || '') < H.at) continue; const p = it.payload || {}; if (typeof p.line !== 'string') continue; const m = /交接單寫好了[::]\s*([^\s"\\]+)/.exec(p.line); if (m) path = m[1]; }
    return { at: H.at, done: !!path, path };
  }
  // 1a93b 62e1bdbeb8(DESIGN-seat-clear r1):aibd 有 seat_clear ⇒ 交接單是 aibd 記的、30 分內才亮;按了先確認(席碼與路徑並排),再送
  const HO_FRESH_MS = 30 * 60 * 1000;
  function hoClearBtn(s, st) {
    if (hasAct('seat_clear') !== true) return '<button type="button" class="k" disabled title="這一版 AIB 還沒有清空重開的動作">清空(要新版 AIB)</button>';
    const why = !st.done ? '還沒有交接單' : st.by !== 'aibd' ? '交接單要由席用 seat_handoff 報給 AIB,清空才用得到' : Date.now() - new Date(st.wrote).getTime() > HO_FRESH_MS ? '交接單寫好超過 30 分鐘,請它再寫一次' : '';
    if (why) return `<button type="button" class="k" disabled title="${esc(why)}">清空並開新的一段</button><small class="dim">${esc(why)}</small>`;
    return `<button type="button" class="k" data-hoclear="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''} title="停掉這一段,用新的對話開同一席,第一句叫它讀交接單;舊對話留著">清空並開新的一段</button>`;
  }
  function hoBox(s) {
    const st = hoState(s.seat_id);
    const M = S.hoMsg && S.hoMsg.seat === s.seat_id ? `<div class="trm${S.hoMsg.bad ? ' bad' : ''}" data-homsg>${esc(S.hoMsg.text)}</div>` : '';
    if (!st) return M; // 清空後交接單已用掉,回應字照樣留著
    const C = S.hoConfirm && S.hoConfirm.seat === s.seat_id && st.by === 'aibd' && st.wrote === S.hoConfirm.at ? S.hoConfirm : null;
    const ask = C ? `<div class="rcpq" data-hoconfirm><span>清空 <b>@${esc(seatShort(s))}</b>(${esc(s.seat_id)})· 交接單 <code>${esc(st.path)}</code> · 寫於 ${esc(hhmm(st.wrote))}。舊對話留著,按「重開」不會回去;新的一段第一句會叫它讀這份交接單。</span><button type="button" class="k" data-hoclearyes="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''}>確定清空</button><button type="button" class="k" data-hoclearno="${esc(s.seat_id)}">取消</button></div>` : '';
    return `<div class="trm" data-hostate>${st.done ? '交接單寫好了' + (st.by === 'aibd' ? '(' + esc(hhmm(st.wrote)) + ')' : '') + ':<code>' + esc(st.path) + '</code>' : '已請它寫交接單(' + esc(hhmm(st.at)) + ');還沒看到「交接單寫好了」'} ${C ? '' : hoClearBtn(s, st)}</div>${ask}${M}`;
  }
  const HO_ERR = {
    no_handoff: '這一席沒有交接單(可能已經用掉了),請它再寫一次',
    handoff_changed: '席剛改了交接單,頁面看的是舊的那份;重新看過再按',
    handoff_path_outside: '交接單不在這一席自己的地方(pending/HANDOFF-<席碼>-*.md 或它的工作資料夾),不能用',
    handoff_stale: '交接單之後它又做了事,或寫好超過 30 分鐘;請它再寫一次',
    seat_busy: '這一席正在做事,等這一回合結束再清空',
    stop_unconfirmed: '這一席停不確定,先看停止卡',
    seat_running: '這一席正在重開或清空',
    dgrs_needs_switch: '這台沒開放對 dgrs 席打字,不能清空',
    seat_clear_built_in_page_only: '只有內建頁能清空',
    carried_pair_read_and_safe_only: '換版前延續來的配對不能清空,請重新配對',
    idempotency_conflict: '上一次清空還在進行',
  };
  async function hoClear(id) {
    const st = hoState(id);
    if (!st || st.by !== 'aibd') return;
    const K = (S.hoKey = S.hoKey || {});
    if (!K[id] || K[id].at !== st.wrote) K[id] = { at: st.wrote, key: idem() };
    S.sending = true; S.hoConfirm = null; render();
    try {
      const r = await fingerAct('seat_clear', { seat_id: id, handoff_at: st.wrote, idem_key: K[id].key }); // 08de5 A11:dgrs 席 202 要指紋
      delete K[id];
      // 這一份交接單用掉了(第一句沒送進去時 aibd 會放回板上、時間不變 ⇒ 不藏)
      if (!(r && r.first_line === false)) (S.hoUsed = S.hoUsed || {})[id] = st.wrote;
      const bits = ['清空了,新的一段開好了'];
      if (r && r.first_line === false) bits.push('但第一句沒送進去:交接單還沒用掉,可以再按「叫他交接」旁的請它讀交接單,或直接對它打字');
      if (r && r.kept === false) bits.push('這次清空沒記住新對話,之後「重開」會回到舊對話');
      S.hoMsg = { seat: id, text: bits.join(';'), bad: !!(r && (r.first_line === false || r.kept === false)) };
      if (S.ho) delete S.ho[id];
    } catch (e) {
      const c = e && (e.code || (e.body && e.body.code));
      if (c) delete K[id]; // aibd 回答了 ⇒ 鑰匙不再用;連不上就留著,重按是同一次
      S.hoMsg = { seat: id, text: HO_ERR[c] || (c === 'restart_refused' ? '停了但沒開起來:' + String((e && e.message) || '') + ';按「重開」會回到舊對話' : errText(e)), bad: true };
    }
    S.sending = false; render();
  }
  async function hoAsk(id) {
    S.sending = true; render();
    try { await sendSeat(id, HO_ASK); (S.ho = S.ho || {})[id] = { at: new Date().toISOString() }; say('已請 @' + seatShort(seatById(id) || { seat_id: id }) + ' 寫交接單', 'ok'); }
    catch (e) { say(errText(e), 'err'); }
    S.sending = false; render();
  }
  function seatNowLine(s) {
    const n = nowOf(s.seat_id), se = lastSession(s.seat_id), run = se && se.state === 'running';
    const bits = [];
    if (!run) bits.push(se ? '沒在跑' : '還沒開');
    else if (n && n.state === 'reply') bits.push('<b class="snw">等回應</b>');
    else if (n && n.state === 'decide') bits.push('<b class="snw">等你決定</b>');
    else if (n && n.state === 'busy') { const st = stallOf(s.seat_id); bits.push(st && st.none ? '<b class="bad">這一輪模型沒有回應</b>' : '在做'); }
    else bits.push('在跑');
    if (n && n.item) bits.push(esc(String(n.item).slice(0, 80)) + (n.total ? ` <small>${Number(n.done) | 0}/${Number(n.total) | 0}</small>` : ''));
    const mail = (S.aslots || []).filter((c) => c && c.seat_id === s.seat_id && (c.kind === 'message.unclosed' || c.kind === 'message.unread'));
    const m = mail.map((c) => { const k = String(c.item || ''); const d = /(\d+)\s*封/.exec(k); return { n: d ? Number(d[1]) : 1, open: c.kind === 'message.unclosed' }; });
    const nOpen = m.filter((x) => x.open).reduce((a, x) => a + x.n, 0), nUnread = m.filter((x) => !x.open).reduce((a, x) => a + x.n, 0);
    if (nOpen) bits.push(`<b class="snw">${nOpen} 封信沒結</b>`);
    if (nUnread) bits.push(`<b class="snw">${nUnread} 封信沒讀</b>`);
    const u = S.usage && (S.usage.usage || []).find((x) => x.seat_id === s.seat_id && (!se || x.session_id === (se.id || se.session_id)));
    if (u && u.pct != null) bits.push('用量 ' + (Number(u.pct) | 0) + '%');
    const tu = trustUntil(s); if (tu) bits.push('受信任到 ' + esc(hhmm(tu * 1000)));
    return `<div class="snl" data-seatnow="${esc(s.seat_id)}">${logo(s, 'sm')}<button type="button" class="snn" data-openseat="${esc(s.seat_id)}" title="打開 @${esc(seatName(s))} 的席窗">@${esc(seatShort(s))}</button> ${bits.join(' · ')}</div>`;
  }
  // 唯讀看 PR 與檢查格(519d0 22:25 第 3 點):act gh_view {what: pr | checks | run, number}(7b81e 1dae7efafe)。
  // 那片只收受信任席自己的權杖(seat_input.rs:seat_token),頁面憑證還沒有路 ⇒ 頁面叫會被拒;收到就收起並寫缺口。aibd 每席 10 秒只准問一次 ⇒ 一次只問一樣
  const GH_WHY = {
    trusted_seats_off: '這台沒開受信任席,看 PR 的開關跟著關', gh_view_user_page: '使用者資料夾頁在服務時不能看',
    gh_view_what: '只能看 PR、檢查格、run', gh_view_number: '號碼不對', gh_view_repo: '這台 AIB 的倉設定不是 owner/name',
    gh_view_seat_rate: '每 10 秒只准問一次,等一下再按', gh_view_rate: '這台一分鐘內問太多次(席和頁面合計 30 次),等一下再按', gh_view_no_answer: 'gh 20 秒沒回',
    // 1a93b 8b12d76d7(DESIGN-page-gh-view r1):頁面自己的入口與開關
    page_gh_view_off: '這台沒開「頁面看 PR」(bridge.json 的 page_gh_view)',
  };
  // 5d33a PREREAD-pageghview N1:只有 https 且 host 剛好是 github.com 才畫成連結(不用前綴比對;PR 標題等外部字一律當字)
  const ghSafeUrl = (u) => { try { const x = new URL(String(u || '')); return x.protocol === 'https:' && x.hostname === 'github.com' && !x.username && !x.password ? x.href : ''; } catch (e) { return ''; } };
  const GH_PAGE_GAP = '這一版 AIB 的頁面還不能看 PR(要有頁面版 gh_view 的新版 AIB)';
  function ghBox() {
    const G = S.gh || {};
    if (!G.off && hasAct('gh_view') === false) G.off = GH_PAGE_GAP;
    if (G.off) return `<div class="ghb" data-ghbox><span class="trs">${esc(G.off)}</span></div>`;
    const pr = G.pr, ck = Array.isArray(G.checks) ? G.checks : null, run = G.run;
    // Andy 13:2x「看PR號碼沒設計感」(519d0 13:40):收成一顆跟篩選鈕同款的膠囊;點開 = 深底金框輸入 + 在途 PR 下拉(狀態色點),打字即篩、Enter 開
    let h = `<div class="ghb" data-ghbox>${G.open ? `<div class="ghpk"><span class="ghh">#</span><input class="ghin" data-ghnum inputmode="numeric" maxlength="7" placeholder="輸入 PR 號碼" aria-label="PR 號碼" autocomplete="off" value="${esc(G.num || '')}"><button type="button" class="k" data-ghgo="pr" ${G.busy ? 'disabled' : ''}>看</button><button type="button" class="ghx" data-ghpill="0" aria-label="收起">×</button>${ghList(G)}</div>` : `<button type="button" class="ghpill" data-ghpill="1">${G.pr ? `<i class="ghd ${prDot(G.pr, G.checks)}"></i>#${G.pr.number | 0} · 換一張 →` : '# 輸入 PR 號碼 →'}</button>`}${pr ? `<button type="button" class="k" data-ghgo="checks" ${G.busy ? 'disabled' : ''}>檢查格</button>` : ''}`;
    if (G.msg) h += `<span class="trs ${G.bad ? 'bad' : ''}">${esc(G.msg)}</span>`;
    if (pr) h += `<div class="ghp" data-ghpr>#${pr.number | 0} ${esc(String(pr.title || ''))} · ${esc(String(pr.state || ''))}${pr.isDraft ? ' · 草稿' : ''} · ${esc(String(pr.baseRefName || ''))} ← ${esc(String(pr.headRefName || ''))} · 頭 ${esc(String(pr.headRefOid || '').slice(0, 12))}${pr.mergeable ? ' · ' + esc(String(pr.mergeable)) : ''}${pr.reviewDecision ? ' · ' + esc(String(pr.reviewDecision)) : ''}${ghSafeUrl(pr.url) ? ` · <a class="lk" data-ghlink href="${esc(ghSafeUrl(pr.url))}" target="_blank" rel="noopener noreferrer">開在 GitHub</a>` : ''}</div>`;
    if (ck) {
      const B = { pass: '綠', fail: '紅', pending: '跑著', skipping: '略過', cancel: '取消' };
      const cnt = {}; for (const c of ck) cnt[c.bucket] = (cnt[c.bucket] || 0) + 1;
      h += `<div class="ghc" data-ghchecks>${Object.keys(cnt).map((k) => (B[k] || k) + ' ' + cnt[k]).join(' · ')}</div>` + ck.filter((c) => c.bucket !== 'pass' && c.bucket !== 'skipping').slice(0, 20).map((c) => { const m = /\/runs\/(\d+)/.exec(String(c.link || '')); return `<div class="ghk ${c.bucket === 'fail' ? 'bad' : ''}">${esc(B[c.bucket] || c.bucket)} · ${esc(String(c.name || ''))}${m ? ` <button type="button" class="k" data-ghrun="${m[1]}" ${G.busy ? 'disabled' : ''}>看這次 run</button>` : ''}</div>`; }).join('');
    }
    if (run) h += `<div class="ghr" data-ghrun-out>run ${esc(String(G.runId || ''))} · ${esc(String(run.status || ''))}${run.conclusion ? ' · ' + esc(String(run.conclusion)) : ''} · 頭 ${esc(String(run.headSha || '').slice(0, 12))}${Array.isArray(run.jobs) ? ' · ' + run.jobs.filter((j) => j.conclusion && j.conclusion !== 'success' && j.conclusion !== 'skipped').map((j) => esc(String(j.name || '')) + ' ' + esc(String(j.conclusion))).slice(0, 8).join('、') : ''}</div>`;
    return h + '</div>';
  }
  // 狀態色點:綠可合 / 橘等審 / 紅檢查紅 / 灰草稿
  function prDot(pr, ck) {
    if (!pr) return '';
    if (pr.isDraft) return 'pg';
    const roll = Array.isArray(ck) ? ck.map((c) => c.bucket) : Array.isArray(pr.statusCheckRollup) ? pr.statusCheckRollup.map((c) => (/FAIL|ERROR|CANCEL/i.test(String(c.conclusion || c.state || '')) ? 'fail' : '')) : [];
    // a9136 d82d98a48:list 的每張帶 checks = 按結果計數 {SUCCESS:30, FAILURE:1, …}(完整 rollup 會超過 64 KB)
    const cnt = pr.checks && typeof pr.checks === 'object' && !Array.isArray(pr.checks) ? pr.checks : null;
    if (cnt && Object.keys(cnt).some((k) => /FAIL|ERROR|CANCEL|TIMED_OUT|ACTION_REQUIRED/i.test(k) && Number(cnt[k]) > 0)) return 'pr';
    if (roll.includes('fail')) return 'pr';
    if (String(pr.state || '').toUpperCase() === 'MERGED') return 'pg';
    return pr.reviewDecision === 'APPROVED' && String(pr.mergeable || '').toUpperCase() === 'MERGEABLE' ? 'pok' : 'po';
  }
  // 下拉的在途 PR:aibd 有 gh_view {what:"list"} 就用它;沒有就用這頁看過的 + 事件卡裡提到的 #號碼(沒狀態 ⇒ 灰點)
  function ghKnown(G) {
    const M = new Map();
    for (const x of slots()) for (const m of String(x.text || '').matchAll(/#(\d{3,5})\b/g)) if (!M.has(m[1])) M.set(m[1], { n: m[1], t: String(x.text || '').replace(/(?:PR\s*)?#\d+\s*/, ''), dot: '' });
    for (const k in (G.seen || {})) M.set(k, G.seen[k]);
    for (const p of G.list || []) M.set(String(p.number), { n: String(p.number), t: String(p.title || ''), dot: prDot(p) });
    return [...M.values()].sort((a, b) => Number(b.n) - Number(a.n)).slice(0, 30);
  }
  function ghList(G) {
    const L = ghKnown(G), q = String(G.num || '').trim();
    const rows = L.map((x) => `<button type="button" class="ghrow" data-ghpick="${esc(x.n)}"${q && x.n.indexOf(q) !== 0 ? ' hidden' : ''}><i class="ghd ${x.dot || ''}"></i><b>#${esc(x.n)}</b><span>${esc(Array.from(x.t).slice(0, 20).join(''))}</span></button>`).join('');
    return `<div class="ghdd" data-ghdd>${rows || '<div class="ghe">還沒有在途 PR 清單(AIB 沒給 list;看過的、事件提到的會列在這)</div>'}<div class="ghlg"><i class="ghd pok"></i>可合 <i class="ghd po"></i>等審 <i class="ghd pr"></i>檢查紅 <i class="ghd pg"></i>草稿</div></div>`;
  }
  async function ghListLoad() {
    const G = (S.gh = S.gh || {}); if (G.listTried) return; G.listTried = true;
    try { const r = await act('gh_view', { what: 'list' }); if (r && Array.isArray(r.json)) { G.list = r.json; render(); } } catch (e) { /* 舊版只認 pr / checks / run:用這頁知道的 */ }
  }
  async function ghAsk(what, number) {
    const G = (S.gh = S.gh || {});
    if (!/^\d{1,7}$/.test(String(number || ''))) { G.msg = '先填號碼'; G.bad = true; return render(); }
    G.busy = true; G.msg = ''; G.bad = false; render();
    try {
      const r = await act('gh_view', { what, number: Number(number) });
      const j = r && r.json;
      if (what === 'pr') { G.pr = j && typeof j === 'object' ? j : null; G.checks = null; G.run = null; if (G.pr) { G.seen = G.seen || {}; G.seen[String(G.pr.number)] = { n: String(G.pr.number), t: String(G.pr.title || ''), dot: prDot(G.pr) }; G.open = false; } G.msg = G.pr ? '' : 'gh 回的不是完整 JSON' + (r && r.cut ? '(太長被切了)' : ''); }
      else if (what === 'checks') { G.checks = Array.isArray(j) ? j : []; if (G.pr && G.seen) G.seen[String(G.pr.number)].dot = prDot(G.pr, G.checks); }
      else { G.run = j && typeof j === 'object' ? j : null; G.runId = number; }
      if (r && r.done === false) { G.msg = 'gh 結束碼 ' + r.rc; G.bad = true; }
    } catch (e) {
      const code = e && (e.code || e.reason);
      if (code === 'unknown_act_type' || (e && e.status === 401)) G.off = GH_PAGE_GAP;
      else { G.msg = GH_WHY[code] || errText(e); G.bad = true; }
    }
    G.busy = false; render();
  }
  function seatNowBox(P) {
    if (!P.length) return '';
    return `<div class="snb" data-seatnowbox><div class="snh">各席現在 <small>在做什麼 · 等誰 · 幾封信沒結</small></div>${P.map(seatNowLine).join('')}${ghBox()}</div>`;
  }
  // 519d0 10:07 第 3 點:席卡標明它動的是哪裡。aibd 只說得出「登記倉的副本」(repo_copy);其他做事席開在自己的新資料夾,不是你的倉
  function repoTag(s) {
    const rc = s.repo_copy && typeof s.repo_copy === 'object' ? s.repo_copy : null;
    if (rc) return `<span class="rpt cp" title="這一席改的是「${esc(String(rc.repo || ''))}」的副本;你的原倉要按「交回本機原倉」才會多一條分支,main 不動">原倉副本 · ${esc(String(rc.repo || ''))}</span>`;
    if (s.mode === 'work') return '<span class="rpt" title="這一席開在 AIB 給它的新資料夾,不是你登記的倉">自己的新資料夾</span>';
    return '';
  }
  function treeTag(s) {
    const L = (S.seats || []).filter((x) => !isArch(x));
    if (!L.some((x) => x.folder_owner && typeof x.folder_owner === 'object')) return '';
    if (s.repo_copy && typeof s.repo_copy === 'object') return `<small class="tt own" title="「${esc(String(s.repo_copy.repo || ''))}」的副本;做完用席窗的「交回本機原倉」建新分支(main 不動、不上 GitHub)">倉副本 · ${esc(String(s.repo_copy.repo || ''))}</small>`; // a9136 IFACE-web-merge r1 §1:這種席不給合回
    const fo = s.folder_owner, t = s.tree && typeof s.tree === 'object' ? s.tree : null;
    const own = (c) => { const o = L.find((y) => y.seat_id === c || String(y.code || '') === c || String(y.seat_id).replace(/^seat_/, '').startsWith(c)); return o ? '@' + seatShort(o) : '@' + c; };
    const br = t && t.branch ? String(t.branch).replace(/^refs\/heads\//, '') : '';
    if (fo && fo.how === 'folder') return `<small class="tt" title="跟 ${esc(own(fo.seat))} 寫同一份檔案">同一份 · ${esc(own(fo.seat))}</small>`;
    if (fo && fo.how === 'tree') {
      const nw = !!(t && t.new === true && !(Number(t.unmerged) > 0)), n = t && Number(t.unmerged) > 0 ? Number(t.unmerged) : nw ? 1 : 0, d = t && Number(t.dirty) > 0 ? Number(t.dirty) : 0;
      return `<small class="tt own" title="在 ${esc(own(fo.seat))} 的倉裡自己一份${br ? ',分支 ' + esc(br) : ''}${t && t.base ? ',做完合回 ' + esc(t.base) : ''}${d ? ',' + d + ' 個檔沒提交' : ''}">自己一份${n ? ` · <b>${nw ? '有新提交沒合' : n + ' 沒合'}</b>` : ''}</small>${n && !S.mbOff && !TREE_HOLD ? `<button type="button" class="ppo g" data-mbask="${esc(s.seat_id)}" title="把 @${esc(seatShort(s))} 做完${nw ? '新提交' : '的 ' + n + ' 個提交'}合回 ${esc(own(fo.seat))} 那一份">合回</button>` : ''}`;
    }
    return L.some((x) => x.folder_owner && x.folder_owner.seat && (s.seat_id === x.folder_owner.seat || String(s.code || '') === x.folder_owner.seat || String(s.seat_id).replace(/^seat_/, '').startsWith(x.folder_owner.seat))) ? '<small class="tt">主人那一份</small>' : '';
  }
  function mergeWhy(c) {
    const s = c.seat_id ? seatById(c.seat_id) : null, w = String((c.payload && c.payload.why) || c.why || String(c.item || '').replace(/^合回停住:作者倉設定\s*/, '') || '');
    return { text: (s ? '@' + seatShort(s) + ' 的' : '') + '合回停下:那一份的 git 設定不安全,所以不合', st: '要看的設定:' + (w ? unsafeWhy(w) + '(' + w + ')' : '作者那一份的 .git/config') + '。沒有合、什麼都沒動。下一步:請作者那一席把這項設定拿掉(或確認它本來就不該在那),拿掉後再按「合回」;不想動設定就改用「同一份檔案」開審查席' };
  }
  // 0eed7 REVIEW-mergeunsafe N1:白話對照多認幾種(屬性檔、共用倉指向、引入別的設定檔);認不得的照原字
  const unsafeWhy = (w) => { const m = /filter\.([\w.-]+)|merge\.([\w.-]+)|(core\.[\w.-]+)|(hooks?)|(info\/attributes|gitattributes)|(commondir)|(include(?:if)?[\w.:/-]*)/i.exec(w); return !m ? w : m[1] ? '檔案過濾設定 filter.' + m[1] : m[2] ? '合併程式設定 merge.' + m[2] : m[3] ? '設定 ' + m[3] : m[4] ? '鉤子程式' : m[5] ? '檔案屬性設定(.git/info/attributes,可叫出過濾 / 合併程式)' : m[6] ? '共用倉指向(.git/commondir,讓 git 讀別處的設定)' : '引入別的設定檔(' + m[7] + ')'; };
  function worktreeCards() {
    const out = [];
    for (const id in S.aslots ? {} : S.mergeStop || {}) {
      const f = S.mergeStop[id], s = seatById(id) || S.seats.find((y) => String(y.seat_id).replace(/^seat_/, '').startsWith(id)) || { seat_id: id, name: id };
      out.push({ lv: 2, tag: '注意', t: f.at, who: '@' + seatShort(s), seat: s.seat_id, text: '@' + seatShort(s) + ' 的合回停下:作者那份 git 設定有 AIB 不跑的東西' + (f.why ? '(' + unsafeWhy(f.why) + ')' : ''), st: '沒有合、什麼都沒動。下一步:請作者那一席把這項設定拿掉(或它本來就不該在那),拿掉後再按「合回」;不想動設定就改用「同一份檔案」開審查席' + (f.why ? ' · 原文 ' + f.why : ''), key: 'mu:' + id + ':' + f.at });
    }
    for (const code in S.wtKept) {
      const f = S.wtKept[code];
      if (!(f.unmerged > 0 || f.dirty > 0 || f.err)) continue;
      const s = S.seats.find((y) => y.seat_id === code || String(y.seat_id).replace(/^seat_/, '').startsWith(code)) || { seat_id: code, name: code };
      const text = f.err ? seatName(s) + ' 留下的樹收不掉' : f.unmerged > 0 ? seatName(s) + ' 有 ' + f.unmerged + ' 個提交沒合' : seatName(s) + ' 有 ' + f.dirty + ' 個檔沒提交';
      const st = f.err ? String(f.err) : (f.branch ? '分支 ' + f.branch : '') + (f.unmerged > 0 && f.dirty > 0 ? ' · 還有 ' + f.dirty + ' 個檔沒提交' : '') + (f.kind === 'seat.worktree_kept' ? ' · 樹留著' : ' · 樹收掉了,分支留著');
      out.push({ lv: 2, tag: '注意', t: f.at, who: '@' + seatName(s), seat: s.seat_id, text, st, key: 'wt:' + code + ':' + f.at, tree: f.kind === 'seat.worktree_kept' && !f.err && !TREE_HOLD ? s.seat_id : '', treeName: seatName(s) });
    }
    return out;
  }
  const OLD_BROKEN = '\u58de\u4e86';
  function slots() {
    const fix = (x) => { if (x && x.tag === OLD_BROKEN) x.tag = '注意'; return x; };
    // 接回失敗:aibd 槽位有(a9136 04e74d1c)就用那張、掛重開鈕;沒有槽位才用本頁讀事件帳拼的(5d33a PREREAD-slots-04e74d1c L1)
    const L = (S.aslots ? aibdCards() : rejoinCards()).concat(worktreeCards().filter((x) => !S.dismissed.has(x.key)));
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
      else if (x.state === 'stop_unconfirmed' && !stopLooksClean(x)) L.push(Object.assign({ lv: 2, tag: '注意', t: x.ended_at || x.started_at, text: seatName(s) + ' 停了但沒確認行程結束' }, b));
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
    if (x.seat && A.includes('relogin')) b.push(relogHtml((seatById(x.seat) || {}).provider));
    if (x.seat && (A.includes('handoff') || (!A.length && x.lv >= 2 && x.lv <= 4))) b.push(`<button type="button" class="k" data-handoff="${esc(x.seat)}">叫他交接</button>`);
    // 7ebca PAGE-talk-stopped:全艙停發卡(talk.stopped)actions 有 resume ⇒「繼續」送 talk_resume {card_id};不用指紋、不用確認
    if (A.includes('resume') && x.card) b.unshift(`<button type="button" class="k g" data-resume="${esc(x.card)}" title="看過沒問題才按:解除停發,這一艙的一小時重新算">繼續</button>`);
    if (x.quota && hasAct('seat_model_set') === true) {
      const M = S.mswap && S.mswap.seat === x.quota ? S.mswap : null;
      b.push(M ? `<span class="msw"><input class="apq" data-mswin="1" value="${esc(M.v || '')}" placeholder="換成哪個模型,例:sonnet" aria-label="換成哪個模型" autocomplete="off" spellcheck="false"><button type="button" class="k g" data-mswgo="1" ${M.busy ? 'disabled' : ''}>換,接著做</button><button type="button" class="k" data-mswx="1">取消</button>${M.msg ? `<span class="bad" role="alert">${esc(M.msg)}</span>` : ''}</span>` : `<button type="button" class="k g" data-msw="${esc(x.quota)}" data-mswcard="${esc(x.card || '')}" title="同一席、同一段對話,換一個模型接著做">換模型</button>`);
    }
    if (x.restart) b.push(`<button type="button" class="k g" data-restart="${esc(x.restart)}" data-rcard="${esc(x.card || '')}">重開</button>`);
    if (x.tree) b.push(`<button type="button" class="k" data-tree-seat="${esc(x.tree)}" data-tree-name="${esc(x.treeName || '')}" title="只讀看它那一份:改了什麼、提交、檔案">看改了什麼</button>`); // b4297 第二輪 tree.js
    if (x.seat) b.push(`<button type="button" class="k" data-go="${esc(x.seat)}">看這一席</button>`);
    if (x.card) b.push(`<button type="button" class="k" data-later="${esc(x.card)}" title="移到「暫不處理」;之後可以在事件框底下移回">移至暫不處理</button>`);
    // 建議:同意 / 不同意(act idea_answer);真人未審:每條一個「看過」(act review_seen)。只有確認過的內建頁按得了,aibd 擋了就地說
    const C = inDlg && S.card && S.card.ev === x ? S.card : null;
    let rv = '';
    if ((A.includes('ask_review') || A.includes('ask_first') || x.kind === 'seat.asked') && x.card) {
      const done = C && C.askDone, W = ASK_WORD[askLang()], O = askOpts(x);
      b.unshift(done ? `<span class="ideadone">${esc(askDoneText(done))}</span>` : O.length ? askQuotes(x) + O.map(([v, t], i) => `<button type="button" class="k ${i ? '' : 'g'}" data-ask="${v}" data-acard="${esc(x.card)}">${esc(t)}</button>`).join('') : `<span class="ideadone">${esc(W.odd)}</span>`);
      if (x.ask) b.push(`<button type="button" class="k" data-askopen="${esc(x.ask.tid)}" data-amid="${esc(x.ask.mid)}">${esc(W.open)}</button>`);
    }
    if (A.includes('agree') && x.card) {
      const done = C && C.ideaDone;
      b.unshift(done ? `<span class="ideadone">${done === 'agree' ? '已同意' : done === 'disagree' ? '已不同意' : '已答過'}</span>` : `<button type="button" class="k g" data-idea="agree" data-icard="${esc(x.card)}">同意</button><button type="button" class="k" data-idea="disagree" data-icard="${esc(x.card)}">不同意</button>`);
    }
    if (A.includes('seen')) {
      const seen = (C && C.seen) || {};
      // 7ebca 68b1f4db8(定稿 ideas-2 §2):勾幾條、按一次「看過」⇒ act review_seen {line_ids};要指紋,沒證實的只標「按過、未證實」
      const pick = (C && C.pick) || {}, np = Object.keys(pick).filter((k) => pick[k] && !seen[k]).length;
      const lineTag = (y) => [y.ref ? String(y.ref) : '', Number.isFinite(Number(y.line)) && y.line != null ? '第 ' + Number(y.line) + ' 行' : '', Array.isArray(y.replaces) && y.replaces.length ? '改寫過(取代舊的 ' + y.replaces.length + ' 條)' : ''].filter(Boolean).join(' · ');
      rv = (x.list ? `<div class="rvh"><span>勾選後按一次${np ? '(' + np + ' 條)' : ''}</span><button type="button" class="k g" data-seenpick="1" ${np && !(C && C.seenBusy) ? '' : 'disabled'}>看過</button></div><div class="rvl">${x.list.map((y) => `<label class="rvr${seen[y.id] ? ' ok' : ''}">${seen[y.id] ? '' : `<input type="checkbox" class="rvck" data-rvpick="${esc(y.id)}"${pick[y.id] ? ' checked' : ''}>`}<span class="rvt">${esc(String(y.text || ''))}${lineTag(y) ? `<span class="rvf">${esc(lineTag(y))}</span>` : ''}</span>${seen[y.id] ? `<span class="ideadone">${seen[y.id] === 'unv' ? '按過、未證實' : seen[y.id] === 'gone' ? '已不在清單' : '看過了'}</span>` : ''}</label>`).join('') || '<div class="empty">沒有等你看的</div>'}</div>`
        : `<div class="empty">${x.count != null ? x.count + ' 條;這一頁看不到內容,要在這台的內建頁打開' : '這一頁看不到內容'}</div>`) + (x.unread ? `<div class="pinmsg bad">後審帳另有 ${x.unread} 行讀不了,沒列在上面</div>` : '');
    }
    if (C && C.actMsg) rv += `<div class="pinmsg${C.actBad ? ' bad' : ''}">${esc((typeof fpSay === 'function' ? fpSay : (t) => t)(C.actMsg))}</div>`;
    const k = x.card || 'local:' + (x.seat || '');
    const free = x.answer && (x.card || x.seat) ? `<div class="ans"><input class="ansi" data-ansfor="${esc(k)}" maxlength="2000" placeholder="${opts.length || x.rec ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[k] || '')}"><button type="button" class="k g" data-answer="${esc(k)}" data-aseat="${esc(x.seat || '')}">回答</button></div>` : '';
    return `<div class="eb">${lines.join('')}${rv}${free}${b.length ? `<div class="ea">${b.join('')}</div>` : ''}</div>`;
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
  // 519d0 ORDER-autodrive 清單 2:「待人決策」只算現在真的要你按的(席開的卡 seat.asked、要指紋的建議 / 真人未審、2.0 席的「等你決定」);
  // 舊系統匯入的待人決策清單(andy.open)與終端機舊式席的 decide 列在框底「以前的題目」,收合、不算件數
  const isPast = (x) => !!x && (x.origin ? x.origin === 'history' : x.kind === 'andy.open' || (x.kind === 'seat.now' && (!x.seat || isLegacy(seatById(x.seat) || {}))));
  function evPastSeen(k, G) {
    if (k !== 1) return 0;
    const seen = new Set(), add = (x) => { if (isPast(x)) seen.add(x.card || x.key || x.text); };
    (G[k] || []).forEach(add); if (EVP[k] && EVP[k].cards) EVP[k].cards.forEach(add);
    return seen.size;
  }
  function evCount(k, G) {
    if (k === 1 && S.slotCountsNow && S.slotCountsNow['1'] != null) { const hid = (S.aslots || []).filter((c) => (Number(c.level || c.slot) || 6) === 1 && c.origin !== 'history' && !S.dismissed.has(c.card_id) && pageHides(c)).length; return Math.max(0, (Number(S.slotCountsNow['1']) || 0) - hid) + evLocal(G[k]).length; }
    if (S.slotCounts) return Math.max(0, slotCount(k) + evLocal(G[k]).length - evPastSeen(k, G));
    return G[k].length - evPastSeen(k, G);
  }
  // 0eed7 VERIFY-realentry 8b04171d M1:件數變了(待人決策 1)這一頁卻沿用舊的空頁 ⇒ 鍵帶這一類的即時件數;
  // 第 1 頁永遠讀最新(不帶 as_of),as_of 只用來讓第 2 頁以後翻頁穩定
  const evKey = (k, page) => k + ':' + page + ':' + (page > 1 ? S.slotAsOf || '' : '') + ':' + slotCount(k);
  async function evFetch(k, page) {
    const key = evKey(k, page);
    if (EVP[k] && EVP[k].key === key) return;
    EVP[k] = { key, loading: true, cards: (EVP[k] && EVP[k].cards) || null };
    try {
      const q = new URLSearchParams({ slot: String(k), page: String(page), per: String(EV_PER) }); if (S.slotAsOf && page > 1) q.set('as_of', S.slotAsOf);
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
      if (!P || P.key !== evKey(k, page)) evFetch(k, page);
      const loc = page === 1 ? evLocal(G[k]) : [];
      const pagesSrv = P && P.pages ? P.pages : Math.max(1, Math.ceil(slotCount(k) / EV_PER));
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
    return `<div class="ec l${x.lv}"><div class="eh one${showTag ? '' : ' nt'}" data-evopen="${esc(x.key)}" data-full="${esc(full)}" tabindex="0" role="button" title="${esc(full)}">${showTag ? `<span class="ek ${TAG_K[x.tag] || 'k3'}">${esc(tag)}</span>` : ''}<span class="et">${esc(x.text)}</span>${x.ref ? `<span class="ecd">${esc(x.ref)}</span>` : ''}${x.ref && S.tree && S.tree.get(x.ref) ? upTag(x.ref, 'sm') + upBtn(x.ref, 'sm ev') : ''}<span class="ew">${esc(when(x.t))}</span></div></div>`;
  }
  function evPager(k, P) {
    if (P.pages <= 1) return '';
    return `<div class="epg"><button type="button" class="k" data-evpg="${k}" data-to="${P.page - 1}" ${P.page <= 1 ? 'disabled' : ''}>上一頁</button><span>第 ${P.page} / ${P.pages} 頁</span><button type="button" class="k" data-evpg="${k}" data-to="${P.page + 1}" ${P.page >= P.pages ? 'disabled' : ''}>下一頁</button></div>`;
  }
  function evBox(k, G, solo) {
    const n = evCount(k, G);
    const open = solo || !!EVS.open[k];
    let body, foot = '';
    const past = k === 1 ? (S.slotCounts ? evPage(k, G).items : G[k]).filter(isPast) : [];
    if (!solo && !n && !past.length) return '';
    if (!open) {
      const peek = (S.slotCounts && G[k].length < Math.min(n, EV_PEEK) ? evPage(k, G).items : G[k]).filter((x) => !isPast(x));
      body = peek.slice(0, EV_PEEK).map((x) => evCard(x, k)).join('');
      if (n > EV_PEEK) foot = `<button type="button" class="more" data-evcls="${k}">還有 ${n - EV_PEEK} 則 ▾</button>`;
    } else {
      const P = evPage(k, G);
      const now = P.items.filter((x) => !isPast(x));
      body = now.length ? now.map((x) => evCard(x, k)).join('') : `<div class="empty">${P.loading ? '讀取中…' : P.err ? '這一頁讀不到' : k === 1 ? '現在沒有要你按的' : '沒有事件'}</div>`;
      foot = (P.newer ? `<button type="button" class="more" data-evnew="1">有 ${P.newer} 則新事件,點了看最新</button>` : '') + evPager(k, P) + (!solo && n > EV_PEEK ? `<button type="button" class="more" data-evcls="${k}">收回兩則 ▴</button>` : '');
    }
    if (past.length) foot += `<button type="button" class="more past" data-evpast="1">以前的題目 ${past.length} 則(舊系統匯入,不算件數)${S.evPast ? ' ▴' : ' ▾'}</button>${S.evPast ? `<div class="evpast">${past.map((x) => evCard(x, k)).join('')}</div>` : ''}`;
    return `<div class="ecls" data-cls="${k}">${solo ? '' : `<div class="ecl"><span class="ek ${TAG_K[LV_TAG[k]] || 'k3'}">${esc(EV_NAME[k])}</span><b>${n}</b></div>`}${body}${foot}</div>`;
  }
  // 已收起(slot_dismiss)的:框底一行「已收起 N 則」,點開列出、可還原(act slot_restore,7ebca 形狀待對;
  // 清單 aibd 有 GET slots?dismissed=1 就用,沒有就只列這一頁收起的)
  // Andy 00:2x「暫不處理 分類欄 沒有符合風格設計」⇒ 跟其他類別同一種框:灰色類別標 + 件數、預設收合、點標題列展開;放事件總框最底、最淡
  const dmCount = () => Math.max((S.dmc || new Map()).size, S.dmN || 0);
  function dmHtml(solo) {
    const M = S.dmc || new Map(); const n = dmCount();
    if (!n && !solo) return '';
    const open = solo || S.dmOpen;
    const rows = open ? [...M.values()].map((x) => `<div class="dmr"><span class="et" title="${esc(x.text)}">${esc(x.text)}</span><button type="button" class="k sm2" data-undm="${esc(x.card)}" title="移回事件框">移回</button></div>`).join('') || `<div class="empty">${n ? '讀取中…' : '沒有暫不處理的事件'}</div>` : '';
    const head = solo ? '' : `<button type="button" class="ecl dmh" data-dmopen="1" aria-expanded="${open ? 'true' : 'false'}"><span class="ek k0">暫不處理</span><b>${n}</b><span class="dmc">${open ? '▴' : '▾'}</span></button>`;
    return `<div class="ecls dm ${open ? 'open' : ''}">${head}${rows}</div>`;
  }
  async function loadDismissed() {
    const r = await soft(get('/api/v1/slots?dismissed=1')).catch(() => null);
    // 只認明確回 dismissed:true 的(不認得這個參數的 aibd 會回全部卡,不能當成已收起)
    const L = r && r.dismissed === true && Array.isArray(r.slots) ? r.slots : null; if (!L) return;
    const M = S.dmc || (S.dmc = new Map());
    for (const c of L) if (c && c.card_id && !M.has(c.card_id)) { const s = c.seat_id ? seatById(c.seat_id) : null; M.set(c.card_id, { card: c.card_id, text: (typeof c.text === 'string' && c.text) || (typeof c.item === 'string' && c.item) || (s ? seatName(s) + ' ' : '') + (KIND_TEXT[c.kind] || c.kind || '事件') }); }
    S.dmN = L.length; render();
  }
  // ---------- 即時熱度(519d0 SPEC-heat-rail r1;Andy 12:4x「待人決策下方、預載五個、移動要優雅、不要急」)----------
  // 頁面先用已拿到的事件卡算(最快上線);aibd 給分數之後只換資料來源(2.0 畫面只讀)
  // 熱度 = 近 30 分每則事件 × 種類權重 × 0.5^(分鐘/10) 加總 + 牽涉席數;主題 = 卡號 / PR / 收據 / 一對席
  const HEAT_W = { 1: 5, 4: 3, 2: 3, 3: 2, 5: 2, 6: 1 }, HEAT_N = 5, HEAT_H = 40, HEAT_MIN = 3, HEAT_GAP = 5000;
  const HEAT = { el: null, rows: {}, order: [], at: 0, prev: {}, gray: false };
  function heatTopic(x) {
    const t = String(x.text || '') + ' ' + String(x.ref || '');
    let m = /\b([A-Z]\d*-[A-Za-z0-9][\w-]{1,30})/.exec(String(x.ref || '')) || /\b([NSHEGM]\d*-[A-Za-z0-9][\w-]{1,30})/.exec(t);
    if (m) return { k: 'card:' + m[1], label: m[1] };
    if ((m = /(?:PR\s*)?#(\d{3,5})\b/.exec(t))) return { k: 'pr:' + m[1], label: 'PR #' + m[1] };
    if ((m = /\b([0-9a-f]{5}-[a-z0-9]{2,12}-[0-9a-f]{7,12})\b/.exec(t))) return { k: 'rc:' + m[1], label: '收據 ' + m[1] };
    // 一對席:先取本文點名的兩席(「@7ebca 跟 @f1fae 對頭碼」),不夠才補卡片所屬的席
    const who = new Set(); for (const w of t.matchAll(/@([0-9a-f]{5})\b/g)) if (who.size < 2) who.add(w[1]);
    if (who.size < 2 && x.seat) who.add(seatName(seatById(x.seat) || { seat_id: x.seat }));
    const L = [...who].filter(Boolean).sort(); if (!L.length) return null;
    return { k: 'seat:' + L.join('~'), label: L.map((w) => '@' + w).join(' ↔ ') };
  }
  function heatScore(now) {
    const T = {};
    for (const x of slots()) {
      if (isPast(x)) continue;
      const tp = heatTopic(x); if (!tp) continue;
      const at = new Date(x.t).getTime(), age = Number.isFinite(at) ? Math.max(0, (now - at) / 60000) : 30;
      const o = (T[tp.k] = T[tp.k] || { k: tp.k, label: tp.label, h: 0, last: 0, seats: new Set(), text: '' });
      if (at > o.last) { o.last = at; o.text = String(x.text || ''); }
      if (x.seat) o.seats.add(x.seat); for (const w of String(x.text || '').matchAll(/@([0-9a-f]{5})\b/g)) o.seats.add(w[1]);
      if (age <= 30) o.h += (HEAT_W[x.lv] || 1) * Math.pow(0.5, age / 10);
    }
    for (const k in T) { const o = T[k]; if (o.h > 0) o.h += o.seats.size; }
    return T;
  }
  // 遲滯:每 5 秒最多重排;新主題要高出第 5 名 15% 才擠進;分差 < 15% 不換位(同示意頁 MOCK-heat)
  function heatOrder(T, now) {
    const all = Object.values(T), hot = all.filter((o) => o.h >= HEAT_MIN).sort((a, b) => b.h - a.h);
    if (!hot.length) { HEAT.gray = true; return all.sort((a, b) => b.last - a.last).slice(0, HEAT_N).map((o) => o.k); }
    const wasGray = HEAT.gray; HEAT.gray = false;
    const cur = HEAT.order.filter((k) => T[k] && T[k].h >= HEAT_MIN);
    if (!cur.length || wasGray) return hot.slice(0, HEAT_N).map((o) => o.k);
    if (now - HEAT.at < HEAT_GAP) return HEAT.order;
    let next = cur.slice(0, HEAT_N);
    for (const c of hot) {
      if (next.includes(c.k)) continue;
      if (next.length < HEAT_N) { next.push(c.k); continue; }
      const lo = next.slice().sort((a, b) => T[a].h - T[b].h)[0];
      if (c.h > T[lo].h * 1.15) next[next.indexOf(lo)] = c.k;
    }
    const pos = (k) => { const i = HEAT.order.indexOf(k); return i < 0 ? 99 : i; };
    next.sort((a, b) => (Math.abs(T[a].h - T[b].h) < Math.max(T[a].h, T[b].h) * 0.15 ? pos(a) - pos(b) : T[b].h - T[a].h));
    return next;
  }
  // 五列放在同一個常駐節點(render 整頁重寫也不換),位置用 transform + Web Animations 慢慢滑,重寫不會打斷
  const heatCalm = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } };
  function heatSlide(n, prop, from, to, ms) {
    n.style[prop] = to;
    if (heatCalm() || from === to || !n.animate) return;
    try { n.animate([{ [prop]: from }, { [prop]: to }], { duration: ms, easing: prop === 'transform' ? 'cubic-bezier(.22,.9,.24,1)' : 'ease' }); } catch (e) { /* 舊瀏覽器:直接到位 */ }
  }
  function heatPaint() {
    const now = Date.now(), T = heatScore(now);
    const next = heatOrder(T, now);
    if (next.join() !== HEAT.order.join()) { HEAT.order = next; HEAT.at = now; }
    if (!HEAT.el) { HEAT.el = document.createElement('div'); HEAT.el.className = 'heatl'; HEAT.el.style.height = HEAT_N * HEAT_H + 'px'; }
    const box = HEAT.el, max = Math.max(1, ...HEAT.order.map((k) => (T[k] ? T[k].h : 0)));
    box.classList.toggle('gray', HEAT.gray);
    for (const k of Object.keys(HEAT.rows)) if (!HEAT.order.includes(k)) {
      const n = HEAT.rows[k]; delete HEAT.rows[k];
      heatSlide(n, 'opacity', '1', '0', 900); setTimeout(() => n.remove(), heatCalm() ? 0 : 900);
    }
    HEAT.order.forEach((k, i) => {
      const o = T[k]; if (!o) return;
      let n = HEAT.rows[k], fresh = false;
      if (!n) {
        fresh = true; n = HEAT.rows[k] = document.createElement('button'); n.type = 'button'; n.className = 'heatr'; n.dataset.heat = k;
        n.innerHTML = '<span class="hrk"></span><span class="ht"></span><span class="hbar"><i></i></span><span class="htr">·</span><span class="hsn"></span>';
        n.style.transform = 'translateY(' + HEAT_N * HEAT_H + 'px)'; box.appendChild(n);
      }
      const y = 'translateY(' + i * HEAT_H + 'px)';
      if (n.style.transform !== y) heatSlide(n, 'transform', n.style.transform, y, 1100);
      if (fresh) heatSlide(n, 'opacity', '0', '1', 900);
      n.querySelector('.hrk').textContent = String(i + 1);
      const t = n.querySelector('.ht'); t.textContent = !o.text ? o.label : o.text.indexOf(o.label.replace(/^(PR |收據 )/, '')) >= 0 ? o.text : o.label + ' · ' + o.text; n.title = t.textContent;
      const bar = n.querySelector('.hbar i'), w = HEAT.gray ? '0%' : Math.round((o.h / max) * 100) + '%';
      if (bar.style.width !== w) heatSlide(bar, 'width', bar.style.width || '0%', w, 1400);
      const pv = HEAT.prev[k], dv = pv == null ? 0 : o.h - pv, tr = n.querySelector('.htr');
      if (now - (HEAT.prevAt || 0) >= HEAT_GAP) { tr.textContent = dv > 0.5 ? '▲' : dv < -0.5 ? '▼' : '·'; tr.className = 'htr' + (dv > 0.5 ? ' up' : dv < -0.5 ? ' dn' : ''); }
      n.querySelector('.hsn').textContent = o.seats.size ? o.seats.size + ' 席' : '';
      n.setAttribute('aria-pressed', S.heatF === k ? 'true' : 'false');
    });
    if (now - (HEAT.prevAt || 0) >= HEAT_GAP) { HEAT.prev = {}; for (const k in T) HEAT.prev[k] = T[k].h; HEAT.prevAt = now; }
    HEAT.T = T;
    return box;
  }
  function heatHtml() {
    return `<div class="ecls heat"><div class="ecl"><span class="ek kheat">即時熱度</span><b>${HEAT.order.length}</b><span class="hhint">近 30 分鐘 · 點一列只看它</span></div><div data-heatslot></div></div>`;
  }
  // render 整頁重寫之後把常駐節點放回位子(不重建 ⇒ 動畫不中斷)
  function heatMount() {
    const slot = document.querySelector('[data-heatslot]'); if (!slot) return;
    slot.replaceWith(heatPaint());
  }
  const heatMatch = (x) => { const tp = heatTopic(x); return !!tp && tp.k === S.heatF; };
  function heatOnly(G) {
    const L = [].concat(...EV_CLS.map((k) => G[k] || [])).filter((x) => !isPast(x) && heatMatch(x)).sort((a, b) => new Date(b.t) - new Date(a.t));
    const name = (HEAT.T && HEAT.T[S.heatF] && HEAT.T[S.heatF].label) || S.heatF;
    return `<div class="ecls"><div class="ecl"><span class="ek kheat">只看</span><span class="hhint2">${esc(name)}</span><button type="button" class="k sm2" data-heatx="1">全部</button></div>${L.length ? L.map((x) => evCard(x)).join('') : '<div class="empty">這個主題現在沒有事件</div>'}</div>`;
  }
  setInterval(() => { if (document.querySelector('.heatl')) heatPaint(); }, HEAT_GAP);
  function slotsHtml() {
    const G = evByCls();
    const tot = EV_CLS.reduce((a, k) => a + evCount(k, G), 0);
    const tab = EVS.tab === 'all' || EVS.tab === 'dm' || EV_CLS.includes(Number(EVS.tab)) ? EVS.tab : 'all';
    const tabs = `<div class="etabs"><button type="button" class="et1 ${tab === 'all' ? 'on' : ''}" data-evtab="all">全部 <b>${tot}</b></button>${EV_CLS.map((k) => { const n = evCount(k, G); return `<button type="button" class="et1 ${String(tab) === String(k) ? 'on' : ''} ${n ? '' : 'z'}" data-evtab="${k}">${esc(EV_NAME[k])} <b>${n}</b></button>`; }).join('')}<button type="button" class="et1 dmt ${tab === 'dm' ? 'on' : ''} ${dmCount() ? '' : 'z'}" data-evtab="dm">暫不處理 <b>${dmCount()}</b></button></div>`;
    if (tab === 'dm' && !S.dmLoaded) { S.dmLoaded = true; loadDismissed(); }
    const body = tab === 'dm' ? dmHtml(true) : tab === 'all' ? (S.heatF ? evBox(1, G, false) + heatHtml() + heatOnly(G) : tot ? EV_CLS.map((k) => evBox(k, G, false) + (k === 1 ? heatHtml() + driveHtml() : '')).join('') + dmHtml() : heatHtml() + '<div class="empty">沒有事件</div>' + dmHtml()) : evBox(Number(tab), G, true);
    return favBox() + `<div class="evbox"><div class="bh"><b>事件</b> <span class="st">${tot} 則</span></div>${tabs}<div class="slots">${body}</div></div>`;
  }

  // ---------- 自動推進面板(519d0 DEV-drive-web D6 / D7 畫面;1a93b OPINION-driveweb)----------
  // aibd 有 drive_status ⇒ 照它(開關、next 七層、派工四格、卡點);沒有 ⇒ 用「自動推進的信」分類串拼派工列(只有「送到」確定,
  // 「開工」看收件席之後有沒有開始回合),整塊標「拼的」。開關照 a9136 PREREAD-drive-d7d5 §1:drive_toggle {to: on|off|paused, why},
  // 鈕只照 aibd 給的 can_on / can_off / can_pause 出(打開要 Andy 指紋、停派工要主管、全停任何配對頁),三種都要寫理由(5d33a L1);頁面不判權。
  const DRV = { st: null, posts: [], at: 0, busy: false, open: null, why: '', note: '' };
  const DRV_TO = { on: ['打開派工', '開著', 'drvon'], off: ['停派工', '只停派工', 'drvoff'], paused: ['全部暫停', '全部暫停', 'drvoff'] };
  // 三態放 switch(dispatch 是派工清單,不能撞名);舊形狀只有 on 布林
  const drvState = (st) => (st && /^(on|off|paused)$/.test(String(st.switch)) ? String(st.switch) : st && st.on === true ? 'on' : st && st.on === false ? 'off' : '');
  const drvCan = (st, to) => !!st && st['can_' + (to === 'paused' ? 'pause' : to)] === true && drvState(st) !== to;
  const DRV_STAGES = [['sent', '送到'], ['read', '已讀'], ['taken', '接單'], ['started', '開工']];
  const seatByCode = (c) => S.seats.find((x) => (x.code || String(x.seat_id).replace(/^seat_/, '').slice(0, 5)) === c) || null;
  async function driveLoad(force) {
    if (DRV.busy || (!force && Date.now() - DRV.at < 60000)) return;
    DRV.busy = true; DRV.at = Date.now();
    try {
      if (ctHas('drive_status')) { const r = await act('drive_status', {}).catch(() => null); DRV.st = r && typeof r === 'object' ? r : null; }
      if (!DRV.st) {
        const L = await soft(get('/api/v1/threads?category=drive&limit=3')).catch(() => null);
        const out = [];
        for (const t of ((L && L.threads) || []).slice(0, 3)) { const r = await soft(get('/api/v1/threads/' + encodeURIComponent(t.thread_id))).catch(() => null); for (const p of (r && r.posts) || []) out.push(p); }
        DRV.posts = out.filter((p) => p && p.source && p.source.kind === 'drive').sort((a, b) => (Date.parse(b.source.at || b.created || '') || 0) - (Date.parse(a.source.at || a.created || '') || 0)).slice(0, 8);
      }
    } finally { DRV.busy = false; }
    if (document.querySelector('[data-drv]')) render();
  }
  // 拼的「開工」:收件席在這封之後開始過回合(aibd 用量列 turn_started_at)
  function drvStarted(seatId, at) {
    const U = S.usage, rows = (U && U.usage) || []; const t0 = Date.parse(at || '') || 0;
    return rows.some((u) => u && u.seat_id === seatId && u.turn_started_at && Date.parse(u.turn_started_at) >= t0);
  }
  // a9136 D7b db36219e9f:清單收在 drive 物件(沒有 1.x 根時 drive = null);全是 aib-drive 自己的檔、AI 寫得到 ⇒ 只顯示,頁面不拿來判斷。
  // 舊形狀(頂層 next / dispatch / blockers)留作退路。stage 現在只有 queued / handed(見下)
  // a9136 21:58:queued = 已寫進收件席的 1.x 信件帳、等 1.x 投遞;handed = 已交 aibd 寄網頁席、還可能被擋 ⇒ 兩個都不算「送到」,四格都標 AIB 還沒給,列上另寫這兩個字
  const DRV_STAGE_IN = { queued: '', handed: '', sent: 'sent', read: 'read', taken: 'taken', started: 'started' };
  const DRV_STAGE_WORD = { queued: '已排進信件帳', handed: '已交 AIB 寄' };
  function drvLists(st) {
    if (!st) return null;
    const d = st.drive && typeof st.drive === 'object' ? st.drive : st.drive === null ? null : st;
    if (!d) return { none: true, next: [], dispatch: [], blockers: [] };
    const arr = (v) => (Array.isArray(v) ? v.filter((x) => x && typeof x === 'object') : []);
    return {
      next: arr(d.next).map((n) => ({ tag: String(n.kind || n.layer || (n.rank != null ? '#' + n.rank : '')), what: String(n.what || n.item || ''), to: String(n.to || n.seat || ''), why: [n.why_now, n.needs, n.waited_min != null ? '等了 ' + (Number(n.waited_min) | 0) + ' 分' : ''].filter(Boolean).map(String).join(' · ') })),
      dispatch: arr(d.dispatch).filter((x) => typeof x.to === 'string').map((x) => ({ to: x.to, text: String(x.first_line || x.item || x.text || ''), at: x.at_ms != null ? Number(x.at_ms) : x.at, st: Object.hasOwn(DRV_STAGE_IN, String(x.stage || '')) ? DRV_STAGE_IN[String(x.stage)] : '', raw: String(x.stage || ''), partial: d !== st ? !/^(read|taken|started)$/.test(String(x.stage || '')) : !Object.hasOwn(DRV_STAGE_IN, String(x.stage || '')) || !x.stage })),
      blockers: arr(d.blockers).map((b) => ({ what: String(b.what || b.subject || ''), who: String(b.owner || b.waits_for || ''), blocks: String(b.blocks || '') })),
      complete: d.next_complete !== false, asOf: d.next_as_of,
    };
  }
  function drvRows() {
    const D = drvLists(DRV.st);
    if (D && D.dispatch.length) return D.dispatch.slice(0, 8).map((d) => Object.assign(d, { real: true }));
    if (DRV.st) return [];
    return DRV.posts.map((p) => { const code = String(p.source.to || ''), x = seatByCode(code); return { to: code, text: String(p.body || '').split('\n').find((l) => l.trim()) || '', at: p.source.at || p.created, st: x && drvStarted(x.seat_id, p.source.at || p.created) ? 'started' : 'sent', real: false }; });
  }
  function driveHtml() {
    const st = DRV.st, rows = drvRows(), andy = dqItems().length;
    if (!st && !rows.length) return '';
    const cur = drvState(st);
    const on = st ? (cur ? `<b class="${DRV_TO[cur][2]}">${DRV_TO[cur][1]}</b>` : '') + (st.unapproved === true ? '<b class="drvbad">' + (typeof fpSay === 'function' ? fpSay : (t) => t)('未經指紋的開啟') + '</b>' : '') : '<span class="drvq">開關狀態 AIB 還沒給</span>';
    const who = st && st.by ? `<span class="drvby" title="${esc(st.why || '')}">${esc(String(st.by))} ${esc(hhmm(st.at))}</span>` : '';
    const tog = ['on', 'off', 'paused'].filter((to) => drvCan(st, to)).map((to) => `<button type="button" class="k" data-drvtog="${to}">${DRV_TO[to][0]}</button>`).join('');
    const form = DRV.open && drvCan(st, DRV.open) ? `<div class="drvf"><input class="drvwhy" data-drvwhy="1" maxlength="200" placeholder="為什麼${DRV_TO[DRV.open][0]}(必填;會記下誰、何時、這句)" value="${esc(DRV.why)}"><button type="button" class="go" data-drvgo="1">確定${DRV_TO[DRV.open][0]}</button><button type="button" class="k" data-drvx="1">取消</button>${DRV.open === 'on' ? '<span class="drvq">' + (typeof fpSay === 'function' ? fpSay : (t) => t)('打開要 Andy 在這台按指紋') + '</span>' : ''}</div>` : '';
    const dots = (r) => { const i = DRV_STAGES.findIndex((z) => z[0] === r.st); return DRV_STAGES.map((z, k) => { const known = r.real ? !r.partial : k === 0 || z[0] === 'started', on = r.real ? i >= 0 && k <= i && known : k === 0 || (z[0] === 'started' && r.st === 'started'); return `<i class="drvd ${on ? 'on' : known ? '' : 'unk'}" title="${esc(z[1] + (on ? '' : known ? ':還沒' : ':AIB 還沒給'))}"></i>`; }).join(''); };
    const list = rows.map((r) => { const x = seatByCode(r.to); return `<div class="drvr">${x ? logo(x, 'sm') : ''}<b class="drvto">@${esc(x ? seatShort(x) : r.to)}</b><span class="drvt">${esc(r.text.slice(0, 60))}</span><span class="drvdots">${dots(r)}</span>${r.real && r.partial ? `<small class="drvsw">${esc((Object.hasOwn(DRV_STAGE_WORD, r.raw) && DRV_STAGE_WORD[r.raw]) || '不明' + (r.raw ? '(' + r.raw.slice(0, 16) + ')' : ''))}</small>` : ''}<span class="tm">${esc(hhmm(r.at))}</span></div>`; }).join('') || '<div class="empty">還沒有派工</div>';
    const D = drvLists(st);
    const nx = D && D.next.length ? `<div class="drvs"><span class="drvh">下一步${D.complete ? '' : '(只列到一部分)'}${D.asOf ? ' · ' + esc(hhmm(D.asOf)) : ''}</span>${D.next.slice(0, 7).map((n) => `<div class="drvn" title="${esc(n.why)}"><i class="drvl">${esc(n.tag.slice(0, 12))}</i>${esc(n.what.slice(0, 70))}${n.to ? ' → @' + esc(n.to.slice(0, 20)) : ''}</div>`).join('')}</div>` : '';
    const bl = D && D.blockers.length ? `<div class="drvs"><span class="drvh">卡點</span>${D.blockers.slice(0, 5).map((b) => `<div class="drvn drvb" title="${esc(b.blocks ? '擋住:' + b.blocks : '')}">${esc(b.what.slice(0, 70))}${b.who ? ' · 等 ' + esc(b.who.slice(0, 20)) : ''}</div>`).join('')}</div>` : '';
    const none = D && D.none ? '<div class="drvs"><span class="drvq">這台沒有自動推進的帳(沒有 1.x),下一步 / 派工 / 卡點都空</span></div>' : '';
    const foot = `<div class="drvfoot">${andy ? `等你決定 ${andy} 件(在最上面)` : '沒有等你決定的'}${st ? '' : ' · 這塊是用「自動推進的信」拼的:只有「送到」確定,「開工」看那席之後有沒有開始回合;已讀 / 接單 AIB 還沒給'}${DRV.note ? ' · ' + esc(DRV.note) : ''}</div>`;
    return `<div class="ecls drvbox" data-drv="1"><div class="ecl"><span class="ek kdrv">自動推進</span> ${on}${who}${tog}${st ? '' : '<span class="drvq">拼的</span>'}</div>${form}${none}${nx}<div class="drvs"><span class="drvh">派工</span><span class="drvlg">送到 · 已讀 · 接單 · 開工</span>${list}</div>${bl}${foot}</div>`;
  }
  // 打開派工要 Andy 指紋(a9136 19:18 更正):跟 seat_trust 同形狀 —— 第一次 202 {ticket, reason: owner_check_pending},
  // fingerAct 每秒問 ticket、核准後帶 ticket 原樣重送一次({to, why} 一字不差,參數綁在指紋上)才寫入;指紋不可用 403 owner_check_unavailable
  const DRV_ERR = { owner_check_unavailable: '這台現在叫不出指紋視窗;打開派工要 Andy 本人指紋,先不能開', owner_check_not_approved: '指紋沒按到', finger_denied: '指紋被拒,這次沒打開', finger_expired: '指紋視窗逾時,這次沒打開', finger_timeout: '等指紋逾時,這次沒打開' };
  async function driveToggle() {
    const st = DRV.st, to = DRV.open; if (!to || !drvCan(st, to)) return;
    const why = DRV.why.trim(); if (!why) { DRV.note = '要寫一句理由'; return render(); }
    try {
      await fingerAct('drive_toggle', { to, why }, (t, sec) => { DRV.note = FP_SEE; render(); });
      DRV.open = null; DRV.why = ''; DRV.note = ''; await driveLoad(true);
    } catch (e) { const rs = e && (e.reason || e.code || (e.body && e.body.reason)); DRV.note = (rs === 'finger_timeout' || rs === 'finger_expired') && e.ticket ? '單號 ' + String(e.ticket).slice(0, 12) + ' 過期,可再按一次' : (typeof fpSay === 'function' ? fpSay : (t) => t)(DRV_ERR[rs] || '') || errText(e); }
    render();
  }

  // ---------- 頂端:等你決定(1a93b,519d0 ORDER-fastest-1120 第 5 條;取代本機頁 ANDY-NOW)----------
  // 來源:aibd /api/v1/slots 裡 card_id 開頭 andy: 的卡(aib-drive 待人決策清單,slots.rs andy_cards)。
  // 排法:決定 > 出手 > 知道,同一類等最久的在前(急迫);每則:等了多久、原話、建議、卡號、照建議 / 自己回 / 暫不處理。
  // 答過的(這一頁答的)收進「已答」;下面一條七件進度讀進度樹 N-webonly-g1..g7 的站(519d0 12:30)。
  const DQ_ORDER = { 決定: 0, 出手: 1, 知道: 2 };
  const DQ_SHOW = 3;
  // 0eed7 REVIEW-da539ef6 L2:已答記在這個分頁(sessionStorage),重載還在;存不了就只在這次
  const DQ_KEY = 'aib.ws.dqdone';
  if (!S.dqDone) { try { const v = JSON.parse(sessionStorage.getItem(DQ_KEY) || '[]'); S.dqDone = Array.isArray(v) ? v.filter((y) => y && typeof y.card === 'string').slice(0, 30) : []; } catch (e) { S.dqDone = []; } }
  const dqWait = (t) => { const d = new Date(t).getTime(); if (!Number.isFinite(d)) return { txt: '', h: 0 }; const h = Math.max(0, (Date.now() - d) / 3600000); return { txt: h < 1 ? '剛到' : h < 24 ? '等了 ' + Math.round(h) + ' 小時' : '等了 ' + Math.floor(h / 24) + ' 天', h }; };
  function dqItems() {
    const L = (S.aslots || []).filter((c) => c && typeof c.card_id === 'string' && c.card_id.startsWith('andy:') && !S.dismissed.has(c.card_id));
    const str = (v) => (typeof v === 'string' && v ? v : '');
    // 0eed7 N1:用戶資料夾的頁讀卡只有長度 + 雜湊(slots.rs hide_words)⇒ 講明看不到,不寫「沒寫題目」
    const hid = (v) => (v && typeof v === 'object' && 'sha256' in v ? '(這一頁看不到題目內容,請在內建頁面看)' : '');
    return L.map((c) => ({ card: c.card_id, tag: str(c.tag) || '決定', text: str(c.text) || str(c.item) || hid(c.text) || hid(c.item), rec: str(c.rec), st: str(c.state), src: str(c.src), ref: str(c.card), t: c.since || c.at, A: c.actions || [] }))
      .sort((a, b) => ((DQ_ORDER[a.tag] ?? 1) - (DQ_ORDER[b.tag] ?? 1)) || (new Date(a.t).getTime() || 0) - (new Date(b.t).getTime() || 0));
  }
  function dqRemember(card, answer) {
    const c = (S.aslots || []).find((y) => y && y.card_id === card);
    if (!c) return;
    S.dqDone = [{ card, text: (typeof c.text === 'string' && c.text) || (typeof c.item === 'string' && c.item) || card, answer, at: new Date().toISOString() }].concat(S.dqDone.filter((y) => y.card !== card)).slice(0, 30);
    try { sessionStorage.setItem(DQ_KEY, JSON.stringify(S.dqDone)); } catch (e) { /* 存不了:這次有效就好 */ }
  }
  function dqRow(x) {
    const w = dqWait(x.t), know = x.tag === '知道';
    const rec = x.rec && !know ? `<button type="button" class="k g" data-pick="${esc('照建議:' + x.rec)}" data-pcard="${esc(x.card)}" data-pseat="" title="${esc('送出:照建議:' + x.rec)}">照建議</button>` : '';
    const inp = know ? '' : `<input class="ansi" data-ansfor="${esc(x.card)}" maxlength="2000" placeholder="${x.rec ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[x.card] || '')}"><button type="button" class="k g" data-answer="${esc(x.card)}" data-aseat="">回答</button>`;
    const later = `<button type="button" class="k" data-later="${esc(x.card)}" title="移到「暫不處理」;之後可以在事件框底下移回">${know ? '知道了' : '暫不處理'}</button>`;
    return `<div class="dqr" data-dqcard="${esc(x.card)}"><div class="dqh"><span class="ek ${TAG_K[x.tag] || 'k1'}">${esc(x.tag)}</span>${w.txt ? `<span class="dqw ${w.h >= 24 ? 'old' : ''}">${esc(w.txt)}</span>` : ''}${x.ref ? `<span class="ecd">${esc(x.ref)}</span>` : ''}<span class="dqid">${esc(x.card.slice(5))}</span></div>
      <div class="dqq">${esc(x.text || '(沒寫題目)')}</div>${x.rec ? `<div class="dqrec"><b>建議</b>${esc(x.rec)}</div>` : ''}${x.st ? `<div class="dqst">${esc(x.st)}</div>` : ''}
      <div class="ans dqa">${rec}${inp}${later}</div></div>`;
  }
  function dqProgress() {
    if (!S.kids) return '';
    const G = (S.kids.get('N-webonly') || []).filter((n) => /^N-webonly-g[1-7]$/.test(n.id)).sort((a, b) => a.id.localeCompare(b.id));
    if (!G.length) return '';
    const done = G.filter((n) => stationIdx(n.status) === 6).length;
    return `<div class="dqp" data-dqprog="1"><span class="dqpl">七件 <b>${done}</b> / ${G.length} 驗過</span>${G.map((n) => { const si = stationIdx(n.status); return `<span class="dqpi s${si == null ? 0 : si + 1}" data-card="${esc(n.id)}" title="${esc((n.title || n.id) + ' · ' + (n.status || '?') + (n.owner ? ' · @' + n.owner : ''))}"><i>${esc(n.id.slice(-1))}</i>${esc(n.status || '?')}</span>`; }).join('')}</div>`;
  }
  // 三行:離期望差多少(七件站別)/ 要你出手幾件(andy: 卡 決定 / 出手 / 知道)/ 現在在做什麼(各席狀態);資料都是頁面已讀的,不另叫 API
  function top3(L) {
    const G = S.kids ? (S.kids.get('N-webonly') || []).filter((n) => /^N-webonly-g[1-7]$/.test(n.id)) : [];
    const by = {}; for (const n of G) { const k = n.status || '未開始'; by[k] = (by[k] || 0) + 1; }
    const done = G.filter((n) => stationIdx(n.status) === 6).length;
    const l1 = G.length ? `七件 ${done} / ${G.length} 驗過,差 <b>${G.length - done}</b> 件${Object.keys(by).filter((k) => stationIdx(k) !== 6).map((k) => '(' + esc(k) + ' ' + by[k] + ')').join('')}` : '進度樹還沒讀到';
    const t = { 決定: 0, 出手: 0, 知道: 0 }; for (const x of L) t[x.tag] = (t[x.tag] || 0) + 1;
    const act = (t['決定'] || 0) + (t['出手'] || 0);
    const l2 = `<b>${act}</b> 件(決定 ${t['決定'] || 0} · 出手 ${t['出手'] || 0})· 知道 ${t['知道'] || 0}`;
    const P = present(), c = { busy: 0, reply: 0, decide: 0, stall: 0, idle: 0 };
    for (const s0 of P) { const n0 = nowOf(s0.seat_id), st = stallOf(s0.seat_id); if (st && st.none) c.stall++; else if (n0 && c[n0.state] != null) c[n0.state]++; else c.idle++; }
    const busyItems = P.map((s0) => { const n0 = nowOf(s0.seat_id); return n0 && n0.state === 'busy' && n0.item ? '@' + seatShort(s0) + ' ' + String(n0.item).slice(0, 30) : ''; }).filter(Boolean).slice(0, 3);
    const l3 = `${c.busy} 席在做${c.reply ? ' · ' + c.reply + ' 等回應' : ''}${c.decide ? ' · <b>' + c.decide + ' 等你決定</b>' : ''}${c.stall ? ' · <b class="bad">' + c.stall + ' 沒有回應</b>' : ''}${c.idle ? ' · ' + c.idle + ' 閒著' : ''}${busyItems.length ? '<small>(' + busyItems.map(esc).join(';') + ')</small>' : ''}`;
    return `<div class="t3" data-top3><div><span>離期望</span>${l1}</div><div><span>要你出手</span>${l2}</div><div><span>現在在做</span>${l3}</div></div>`;
  }
  function decideHtml() {
    if (!S.aslots && !S.kids) return '';
    const L = dqItems(), n = L.length, prog = dqProgress();
    // 519d0 06:45 第 3 點(0eed7 VERIFY-pv6 L):頂端件數與右欄「待人決策」讀同一份數(evCount(1));
    // 這裡列的是 andy: 卡,右欄另有的(席開的卡、要指紋的建議…)寫一行帶過去
    const all = Math.max(n, evCount(1, evByCls())), rest = all - n;
    if (!all && !S.dqDone.length && !prog && !present().length) return '';
    const open = S.dqOpen !== false;
    const shown = S.dqMore ? L : L.slice(0, DQ_SHOW);
    const more = n > DQ_SHOW ? `<button type="button" class="more" data-dqmore="1">${S.dqMore ? '收回 ▴' : '還有 ' + (n - DQ_SHOW) + ' 則 ▾'}</button>` : '';
    const done = S.dqDone.length ? `<button type="button" class="more past" data-dqdone="1">已答 ${S.dqDone.length} 則${S.dqDoneOpen ? ' ▴' : ' ▾'}</button>${S.dqDoneOpen ? `<div class="dqdl">${S.dqDone.map((y) => `<div class="dqd"><span class="et" title="${esc(y.text)}">${esc(y.text)}</span><span class="dqda">→ ${esc(y.answer)}</span><span class="ew">${esc(ago(y.at))}</span></div>`).join('')}</div>` : ''}` : '';
    const restRow = rest > 0 ? `<button type="button" class="more" data-evtab="1">另有 ${rest} 則在右邊「待人決策」</button>` : '';
    const body = open ? (n ? shown.map(dqRow).join('') + more : rest ? '' : '<div class="empty">現在沒有等你決定的</div>') + restRow + done : '';
    return `<div class="dqbox ${all ? 'has' : ''}" data-dqbox="1">${top3(L)}<button type="button" class="dqt" data-dqtoggle="1" aria-expanded="${open ? 'true' : 'false'}"><b>等你決定</b><span class="dqn">${all}</span>${open ? '▴' : '▾'}</button>${prog}${body}</div>`;
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
  // N-ws-seat-window 第一批:工具那一行點開看結果(先 12 行,「再看」到 200 行);結果來自 stream-json 的 user.tool_result(對 tool_use.id)
  const WK_HEAD = 12, WK_MAX = 200;
  function resText(c) {
    const v = c && c.content;
    if (typeof v === 'string') return v;
    if (Array.isArray(v)) return v.map((x) => (x && x.type === 'text' ? x.text : x && x.type === 'image' ? '[圖片]' : '')).filter(Boolean).join('\n');
    return '';
  }
  function wkHtml(c, res) {
    const id = String(c.id || ''), open = id && S.wkOpen && S.wkOpen.has(id), all = id && S.wkAll && S.wkAll.has(id);
    const st = !res ? '<span class="wkst run">執行中</span>' : res.err ? (String(res.text || '').trim() ? '<span class="wkst soft" title="指令跑完了,但回了不是 0 的結束碼(例:搜尋沒對到)。不一定是壞事,點開看輸出">回了失敗碼</span>' : '<span class="wkst bad">出錯</span>') : '<span class="wkst">▸</span>';
    let body = '';
    if (open) {
      if (!res) body = '<div class="wkr na">還沒有結果</div>';
      else {
        const L = String(res.text || '').split('\n'), n = all ? WK_MAX : WK_HEAD, more = L.length - n;
        body = `<pre class="wkr ${res.err ? 'err' : ''}">${esc(L.slice(0, n).join('\n') || '(沒有輸出)')}</pre>${more > 0 ? `<button type="button" class="more wkmore" data-wkall="${esc(id)}">${all ? '只顯示到 ' + WK_MAX + ' 行(還有 ' + more + ' 行沒顯示)' : '再看 ' + Math.min(more, WK_MAX - WK_HEAD) + ' 行 ▾'}</button>` : ''}`;
      }
    }
    return `<div class="wkb ${open ? 'open' : ''}"><button type="button" class="wk wkx ${/^(Bash)$/.test(c.name) ? 'v' : ''}${res && res.err ? ' err' : ''}" data-wk="${esc(id)}" aria-expanded="${open ? 'true' : 'false'}"><span>${esc(toolLine(c))}</span>${st}</button>${body}</div>`;
  }
  // c44(7ebca 18:44 / 519d0 18:45):GET usage 的 now + 每列 turn_started_at / turn_output_at。
  // 這一輪開始超過 N 秒(aibd 給 noreply_s 就用它,不然 90)模型一個字都沒吐 ⇒ none;
  // 吐過字但 QUIET_S 秒沒新輸出 ⇒ quiet(長工具很正常,只加淡字)。三欄缺一(舊 aibd)⇒ null,照舊只顯示在想
  const QUIET_S = 300;
  function stallOf(seatId) {
    const U = S.usage; if (!U || !U.now) return null;
    const se = lastSession(seatId);
    const u = (U.usage || []).find((x) => x.seat_id === seatId && (!se || x.session_id === (se.id || se.session_id)));
    if (!u || !u.turn_started_at || !('turn_output_at' in u)) return null;
    const now = Date.parse(U.now), st = Date.parse(u.turn_started_at); if (!now || !st) return null;
    const n = Number(U.noreply_s) > 0 ? Number(U.noreply_s) : 90;
    if (!u.turn_output_at) return now - st > n * 1000 ? { none: true, ms: now - st, since: u.turn_started_at } : null;
    const o = Date.parse(u.turn_output_at);
    return o && now - o > QUIET_S * 1000 ? { quiet: true, ms: now - o } : null;
  }
  // 你在共同對話最後一句 @ 到這一席的話(重送用);找不到 ⇒ 不給重送鈕
  function lastAsk(seatId) {
    let best = null;
    for (const p of S.itemTalk || []) {
      if (!p._shared || typeof p.body !== 'string' || !(p.sender_kind === 'human' || p.from_seat === S.me)) continue;
      if (!(p.calls || []).some((c) => String(c.seat || '') === seatId)) continue;
      if (!best || String(p.created || '') > String(best.created || '')) best = p;
    }
    return best;
  }
  function stallHtml(seatId, st) {
    if (st.quiet) return `<span class="stq">${esc(durS(st.ms))}沒有新輸出</span>`;
    const p = lastAsk(seatId), done = S.resent && S.resent[seatId] === st.since;
    const b = !p ? '' : done ? '<span class="stq">已再送一次</span>' : `<button type="button" class="k rsd" data-resend="${esc(seatId)}" title="${esc('把你最後一句 @ 它的話再送一次;這一輪還沒結束,會排在它後面:' + String(p.body).slice(0, 120))}">再送一次</button>`;
    return `<span class="stn">這一輪模型沒有回應(已 ${esc(durS(st.ms))})</span>${b}`;
  }
  async function resend(seatId) {
    const st = stallOf(seatId), p = lastAsk(seatId);
    if (!st || !st.none || !p) return render();
    try {
      await act('thread_send', { thread_id: p._tid, body: p.body, to: [seatId], idem_key: idem() });
      S.resent = S.resent || {}; S.resent[seatId] = st.since;
      say('已再送一次;這一輪還沒結束的話,會排在它後面', 'ok');
    } catch (e) { say(errText(e), 'err'); }
    render();
  }
  // 這一回合:上一個 result 之後的第一筆輸出 = 開始;還沒有 result 就是在跑
  function turnOf(seatId) {
    const se = lastSession(seatId), o = se && S.out[se.id || se.session_id];
    if (!o || !o.items.length) return null;
    let start = null, last = null;
    for (const it of o.items) {
      const p = it.payload || {}; if (it.type !== 'session.output' || typeof p.line !== 'string' || p.stream === 'stderr') continue;
      let j = null; try { j = JSON.parse(p.line); } catch (e) { j = null; }
      if (j && j.type === 'result') { last = { at: it.occurred_at, ms: Number(j.duration_ms) || (start ? Date.parse(it.occurred_at) - Date.parse(start) : 0), turns: Number(j.num_turns) || 0, err: !!j.is_error }; start = null; continue; }
      // 519d0 06:45 第 4 點(pv5、6f1cb chat-reply.png):回完話 Stop hook 的 system 行、rate_limit_event 也是輸出,
      // 以前會把「在想…」重新點亮。只認模型自己說話的行開始一回合(同 aibd turns.rs says)
      if (!start && j && !/^(assistant|stream_event|user)$/.test(String(j.type || ''))) continue;
      if (!start) start = it.occurred_at;
    }
    const live = se.state === 'running' || se.state === 'pending';
    // aibd 自己的回合狀態(GET usage 每列 turn_started_at:回合中 = 時間,結束 = null)為準:結束了就收
    const U = S.usage, u = U && (U.usage || []).find((x) => x.seat_id === seatId && x.session_id === (se.id || se.session_id));
    if (u && 'turn_started_at' in u) return { running: !!(u.turn_started_at && live), since: u.turn_started_at || start, last };
    return { running: !!(start && live), since: start, last };
  }
  const durS = (ms) => { const t = Math.max(0, Math.round(ms / 1000)); return t >= 3600 ? Math.floor(t / 3600) + ' 小時 ' + Math.floor((t % 3600) / 60) + ' 分' : t >= 60 ? Math.floor(t / 60) + ' 分 ' + (t % 60) + ' 秒' : t + ' 秒'; };
  function turnHtml(seatId) {
    const T = turnOf(seatId); if (!T) return '';
    if (T.running) return `<span class="turn run" title="從這一回合第一筆輸出算起">這一回合在跑 · <b data-since="${esc(T.since)}">${esc(durS(Date.now() - Date.parse(T.since)))}</b></span>`;
    if (T.last) return `<span class="turn ${T.last.err ? 'bad' : ''}">上一回合 ${esc(durS(T.last.ms))}${T.last.turns ? ' · ' + T.last.turns + ' 步' : ''}</span>`;
    return '';
  }
  const TITLE0 = document.title, SPIN = ['◐', '◓', '◑', '◒']; let spinI = 0;
  // 分頁標題只在這裡組:等回覆轉圈(或回完沒看 ●)+「(N) 等你決定 · 」(askNotice 給)+ 原標題;兩處各寫會互蓋、一秒一閃
  function titleNow() { const n = Object.keys(S.wait).length; const t = (n ? SPIN[spinI] + ' ' : S.doneDot ? '● ' : '') + (S.inbPfx || '') + (S.askPfx || '') + TITLE0; if (document.title !== t) document.title = t; }
  setInterval(() => { spinI = (spinI + 1) % 4; titleNow(); }, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) S.doneDot = false; });
  setInterval(() => { for (const b of document.querySelectorAll('[data-netexp]')) b.textContent = netLeft(Number(b.dataset.netexp)); }, 1000); // 網路例外卡倒數每秒一格
  setInterval(() => { for (const b of document.querySelectorAll('[data-since]')) { const t = Date.parse(b.dataset.since); if (t) b.textContent = durS(Date.now() - t); } }, 1000);
  function outItems(seatId) {
    const se = lastSession(seatId);
    const o = se && S.out[se.id || se.session_id];
    const rows = [];
    if (!o) return rows;
    const RES = new Map();
    for (const it of o.items) {
      const p = it.payload || {}; if (it.type !== 'session.output' || typeof p.line !== 'string' || p.line.indexOf('tool_result') < 0) continue;
      let j = null; try { j = JSON.parse(p.line); } catch (e) { j = null; }
      if (j && j.type === 'user' && j.message && Array.isArray(j.message.content)) for (const c of j.message.content) if (c && c.type === 'tool_result' && c.tool_use_id) RES.set(c.tool_use_id, { text: resText(c), err: !!c.is_error });
    }
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
          else if (c.type === 'tool_use') rows.push({ t: it.occurred_at, h: wkHtml(c, RES.get(c.id)) });
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
      <div class="ans"><input class="ansi" data-ansfor="${esc(key)}" maxlength="2000" placeholder="${opts.length ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[key] || '')}"><button type="button" class="k g" data-answer="${esc(key)}" data-aseat="${esc(s.seat_id)}">回答</button>${card ? `<button type="button" class="k" data-later="${esc(card)}" title="移到「暫不處理」;之後可以在事件框底下移回">移至暫不處理</button>` : ''}</div></div>`;
  }
  // 答一題:有卡走 decision_answer(7ebca),沒卡(這版 aibd 沒有卡槽)就當一句話送進那一席
  async function answer(seat, card, text) {
    if (!text || S.sending) return;
    S.sending = true;
    try {
      if (card && !card.startsWith('local:')) { const r = await act('decision_answer', Object.assign(seat ? { seat_id: seat } : {}, { answer: text, card_id: card })); if (card.startsWith('andy:')) dqRemember(card, text); S.dismissed.add(card); say(r.__status === 202 ? '已交給它,這一回合結束就收到' : '已回答'); }
      else { await act('seat_input', { seat_id: seat, text }); const n = nowOf(seat); if (n) S.answered[seat] = n.updated_at; say('已回答'); }
      delete S.ansDraft[card];
    } catch (er) { say(er && er.code === 'decision_changed' ? '那一席換了題目,看新的那張' : er && er.code === 'already_answered' ? '已經答過了' : errText(er), 'err'); }
    S.sending = false; return tick(true);
  }
  function seatWin(s) {
    if (isLegacy(s)) return legacyWin(s);
    const n = nowOf(s.seat_id);
    const se = lastSession(s.seat_id);
    const stTxt = !se ? '沒在跑' : se.state === 'running' ? (n && n.state === 'busy' ? '在做' : n && n.state === 'reply' ? '等回應' : n && n.state === 'decide' ? '等你決定' : '在跑') : se.state;
    const steps = n && n.total ? `<progress class="ctxp" max="${Number(n.total) | 0}" value="${Number(n.done) | 0}"></progress><small>${Number(n.done) | 0}/${Number(n.total) | 0}</small>` : '';
    // 用量(7ebca GET /api/v1/usage):這一席最新一場的 pct;≥ handoff_pct 黃、≥ red_pct 紅
    const u = S.usage && (S.usage.usage || []).find((x) => x.seat_id === s.seat_id && (!se || x.session_id === (se.id || se.session_id)));
    const uc = u && u.pct != null ? (u.pct >= (S.usage.red_pct || 95) ? 'red' : u.pct >= (S.usage.handoff_pct || 85) ? 'warn' : '') : '';
    const use = u && u.pct != null ? `<span class="use ${uc}" title="${esc((u.model || '') + (u.used != null ? ' · ' + u.used + ' / ' + u.window : ''))}">用量<progress class="usep" max="100" value="${Math.max(0, Math.min(100, Number(u.pct) || 0))}"></progress>${Number(u.pct) | 0}%</span>` : '';
    const rows = outItems(s.seat_id).concat(sharedTo(s.seat_id), saidRows(s.seat_id), actItems(s.seat_id)).sort((a, b) => String(a.t || '').localeCompare(String(b.t || '')));
    { const wr = waitRow(s.seat_id, false); if (wr) rows.push(wr); else if (n && n.state === 'busy') rows.push({ h: '<div class="ev">在想…</div>' }); }
    const stop = se && (se.state === 'running' || se.state === 'pending' || se.state === 'stop_unconfirmed') ? `<button type="button" class="stop" data-stop="${esc(se.id || se.session_id)}" ${S.sending ? 'disabled' : ''}>${S.stopArm === (se.id || se.session_id) ? '再按一次停止' : '■ 停止'}</button>` : '';
    return `<div class="sh">${logo(s, 'big', 'pick')}<span class="nm2">@${esc(seatName(s))}</span><small>${esc([modelText(s), stTxt].filter(Boolean).join(' · '))}</small>${seatModeTag(s.mode)}${repoTag(s)}${turnHtml(s.seat_id)}${steps}${use}${trustHtml(s)}${ptSeatBtn(s)}${moveHtml(s)}${sendBackHtml(s)}${rcpBtn(s)}${running(s.seat_id) ? `<button type="button" class="k" data-handoff="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''} title="請這一席寫交接單;清空那一步要後端">交接並清空</button>` : ''}${stop}</div>${sendBackBox(s)}${rcpBox(s)}${hoBox(s)}${ptSeatBox(s)}${S.trustMsg && S.trustMsg.seat === s.seat_id ? `<div class="trm${S.trustMsg.bad ? ' bad' : ''}" data-trustmsg>${esc(S.trustMsg.text)}</div>` : ''}${S.moveMsg && S.moveMsg.seat === s.seat_id ? `<div class="trm${S.moveMsg.bad ? ' bad' : ''}" data-movemsg>${esc(S.moveMsg.text)}${moveMineHtml(S.moveMsg)}</div>` : ''}
      ${seatInbox(s)}${judgeRowFor(s)}${decideBox(s)}<div class="tl" id="tl">${rows.length ? rows.map((r) => r.h).join('') : '<div class="empty">這一席還沒有輸出</div>'}</div>
      <div class="rowb">${QUICK.map((q, i) => `<button type="button" class="bt" data-quick="${i}" ${running(s.seat_id) ? '' : 'disabled'}>${esc(q[0])}</button>`).join('')}</div>
      ${chatSavedHtml()}${cinbStrip(s)}${cinbCtx(s)}${chatTools()}<div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="只對 @${esc(seatName(s))} 說…">${esc(S.drafts[s.seat_id] || '')}</textarea><span class="gocol"><button type="button" class="go" data-send="seat" ${S.sending ? 'disabled' : ''}>送出</button>${enter2Box()}</span></div>
      ${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
  }
  // 舊式席的信件串:7ebca 會加 GET threads?seat=<碼>;沒有就讀整份清單,挑標題含這個碼的匯入對話
  async function loadLegacyThreads(code) {
    S.legacyLoading = true; S.legacyThreads = S.legacyThreads || {};
    try {
      let L = await soft(get('/api/v1/threads?seat=' + encodeURIComponent(code) + '&limit=50')).catch(() => null);
      let rows = L && (L.threads || L.items) || [];
      if (!rows.length) { L = await soft(get('/api/v1/threads?limit=200')).catch(() => null); rows = (L && (L.threads || L.items) || []).filter((t) => /匯入[))]\s*$/.test(String(t.title || '')) && String(t.title || '').includes(code)); }
      S.legacyThreads[code] = rows;
    } finally { S.legacyLoading = false; }
    render();
  }
  // 舊式席的運作視窗:同一種外框;輸出是鏡像進來的信(寄給它 / 它寄出),送字到舊式席是第二批
  function legacyWin(s) {
    const n = nowOf(s.seat_id);
    const stTxt = n && n.state === 'busy' ? '在做' : n && n.state === 'reply' ? '等回應' : n && n.state === 'decide' ? '等你決定' : s.alive === false ? '不在了' : '在跑';
    const steps = n && n.total ? `<progress class="ctxp" max="${Number(n.total) | 0}" value="${Number(n.done) | 0}"></progress><small>${Number(n.done) | 0}/${Number(n.total) | 0}</small>` : '';
    const code = String(s.code || s.seat_id).replace(/^old_/, '');
    const LT = (S.legacyThreads && S.legacyThreads[code]) || null;
    if (!LT && !S.legacyLoading) loadLegacyThreads(code);
    const thr = LT && LT.length ? `<div class="ltl"><div class="dsec">它的信件(從舊信件帳匯入)</div>${LT.map((t) => `<button type="button" class="ltr" data-th="${esc(t.thread_id)}" data-tt="${esc(t.title || '')}"><span>${esc(t.title || t.thread_id)}</span><span class="na">${t.posts_count != null ? Number(t.posts_count) + ' 則' : ''}${t.last_at ? ' · ' + esc(when(t.last_at)) : ''}</span></button>`).join('')}</div>` : '';
    const rows = sharedTo(s.seat_id).concat((Array.isArray(s.mirror) ? s.mirror : []).map((m) => ({ t: m.created, h: `<div class="out"><div class="body">${esc(String(m.body || ''))}</div><div class="tm">${esc(hhmm(m.created))}${m.to ? ' · 寄給 @' + esc(String(m.to).replace(/^seat:/, '')) : ''}</div></div>` }))).sort((a, b) => String(a.t || '').localeCompare(String(b.t || '')));
    return `<div class="sh">${logo(s, 'big', 'pick')}<span class="nm2">@${esc(seatName(s))}</span><small>${esc(['舊式席', modelText(s), stTxt].filter(Boolean).join(' · '))}</small>${n && n.item ? `<span class="turn">${esc(String(n.item).slice(0, 40))}</span>` : ''}${steps}${s.mirror ? '' : '<span class="pvdemo" title="鏡像還沒接上:這一席的信與輸出還讀不到">示意</span>'}</div>
      ${judgeRowFor(s)}<div class="tl" id="tl">${thr}${rows.length ? rows.map((r) => r.h).join('') : thr ? '' : '<div class="empty">這是舊式席(不是網頁開的);它的信接上鏡像(7ebca N-web-dmbridge)後會出現在這裡</div>'}</div>
      ${chatTools()}<div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="寫信給 @${esc(code)}(走舊信件帳)">${esc(S.drafts[s.seat_id] || '')}</textarea><span class="gocol"><button type="button" class="go" data-send="legacy" data-code="${esc(code)}" ${S.sending ? 'disabled' : ''}>寄信</button>${enter2Box()}</span></div>${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
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
  // 8 小時目標(519d0 定稿 webdev-8h §6,043dd 意見):共同對話只看得到每席最後回的話,看不出它讀檔、改檔、跑測試 ⇒
  // 每則席的回覆下一行工具摘要(從那席上一則到這一則之間的工具),席在跑時最下面一行「正在做什麼」;點了開那一席的視窗
  const sidOf = (k) => String(k || '').replace(/^seat:/, '');
  function seatPostTimes(id) {
    const T = [];
    for (const p of S.itemTalk || []) if (p._shared && sidOf(p.from_seat) === id && p.sender_kind !== 'human') T.push(String(p.created || ''));
    for (const m of S.threads[id] || []) if (m.from_seat !== S.me) T.push(String(m.created || ''));
    return T.sort();
  }
  // 共同對話要讀輸出的席:最近在共同對話發過言的、或正在跑的,最多 4 席
  function workSeats() {
    const last = new Map();
    for (const p of S.itemTalk || []) if (p._shared && p.from_seat && p.sender_kind !== 'human') { const id = sidOf(p.from_seat), t = String(p.created || ''); if (!last.has(id) || last.get(id) < t) last.set(id, t); }
    for (const id in S.threads) for (const m of S.threads[id]) if (m.from_seat !== S.me) { const t = String(m.created || ''); if (!last.has(id) || last.get(id) < t) last.set(id, t); }
    for (const se of S.sessions || []) if (se.state === 'running' && se.seat_id && !last.has(se.seat_id)) last.set(se.seat_id, '9');
    return [...last.entries()].filter(([id]) => lastSession(id)).sort((a, b) => b[1].localeCompare(a[1])).slice(0, 4).map(([id]) => id);
  }
  async function loadWorkOut() { await Promise.all(workSeats().map((id) => loadOut(id).catch(() => null))); }
  const TEST_RE = /(^|[\s;&|(])(node\s+--test|npm\s+(run\s+)?test|npx\s+jest|cargo\s+test|pytest|aib\s+test)\b/;
  const TEST_PASS_RE = /(^|\n)\s*(ℹ|#)\s*fail\s+0\b|test result: ok\b|\b\d+ passed\b(?![^\n]*\b[1-9]\d* failed)/;
  const TEST_FAIL_RE = /(^|\n)\s*(#\s*fail\s+[1-9]|not ok\b)|\b[1-9]\d* (failed|failing)\b|test result: FAILED/;
  // 一席在 (from, to] 之間的工具:讀幾檔、改幾檔、查幾次、跑了測試沒(綠 / 紅 / 跑中)、提交
  function workTools(id, from, to) {
    const se = lastSession(id), o = se && S.out[se.id || se.session_id];
    if (!o) return null;
    const uses = [], RES = new Map();
    for (const it of o.items) {
      const p = it.payload || {}, t = String(it.occurred_at || '');
      if (it.type !== 'session.output' || typeof p.line !== 'string' || p.stream === 'stderr' || t <= from || (to && t > to)) continue;
      if (p.line.indexOf('tool_') < 0) continue;
      let j = null; try { j = JSON.parse(p.line); } catch (e) { continue; }
      const C = j && j.message && Array.isArray(j.message.content) ? j.message.content : [];
      for (const c of C) {
        if (j.type === 'assistant' && c && c.type === 'tool_use') uses.push(c);
        else if (j.type === 'user' && c && c.type === 'tool_result' && c.tool_use_id) RES.set(c.tool_use_id, { text: resText(c), err: !!c.is_error });
      }
    }
    return { uses, RES };
  }
  function workSum(W) {
    const rd = new Set(), ed = new Set(); let q = 0, cmd = 0, test = null, commit = null;
    for (const c of W.uses) {
      const n = String(c.name || '').replace(/^mcp__[^_]+__/, ''), i = c.input || {}, r = W.RES.get(c.id);
      if (/^(Read|NotebookRead)$/.test(n)) rd.add(String(i.file_path || i.notebook_path || c.id));
      else if (/^(Grep|Glob|LS)$/.test(n)) q++;
      else if (/^(Edit|Write|MultiEdit|NotebookEdit)$/.test(n)) ed.add(String(i.file_path || i.notebook_path || c.id));
      else if (n === 'Bash') {
        const k = String(i.command || '');
        if (TEST_RE.test(k)) test = !r ? 'run' : TEST_FAIL_RE.test(r.text) ? 'red' : TEST_PASS_RE.test(r.text) ? 'green' : r.err ? 'red' : 'green'; // 519d0 自測 M1:`node --test; ls trial` 結束碼 1 是 ls 的,測試 6 過 0 紅
        else if (/\bgit\s+commit\b/.test(k)) { const m = r && !r.err && /\[[^\]]*?\b([0-9a-f]{7,40})\]/.exec(r.text); commit = m ? m[1].slice(0, 7) : r && r.err ? 'bad' : 'run'; }
        else cmd++;
      }
    }
    const P = [];
    if (rd.size) P.push(['讀 ' + rd.size + ' 檔', '']);
    if (q) P.push(['查 ' + q + ' 次', '']);
    if (ed.size) P.push(['改 ' + ed.size + ' 檔', '']);
    if (cmd) P.push(['指令 ' + cmd + ' 個', '']);
    if (test) P.push([test === 'green' ? '測試 綠' : test === 'red' ? '測試 紅' : '測試跑中', test === 'green' ? 'ok' : test === 'red' ? 'bad' : '']);
    if (commit) P.push([commit === 'bad' ? '提交沒成功' : commit === 'run' ? '提交中' : '提交 ' + commit, commit === 'bad' ? 'bad' : '']);
    return P;
  }
  function workLine(seat, created) {
    const id = sidOf(seat); if (!id || !S.out) return '';
    const t = String(created || ''), T = seatPostTimes(id), prev = T.filter((x) => x < t).pop() || '';
    const W = workTools(id, prev, t); if (!W || !W.uses.length) return '';
    const P = workSum(W); if (!P.length) return '';
    const tip = W.uses.slice(0, 14).map(toolLine).join('\n') + (W.uses.length > 14 ? '\n…還有 ' + (W.uses.length - 14) + ' 個' : '');
    return `<button type="button" class="wsum" data-openseat="${esc(id)}" title="${esc(tip)}">${P.map(([x, c]) => `<span class="${c}">${esc(x)}</span>`).join('<i>·</i>')}<b>看這一席 ›</b></button>`;
  }
  const DOING = (c) => {
    const n = String(c.name || '').replace(/^mcp__[^_]+__/, ''), i = c.input || {}, f = i.file_path ? String(i.file_path).split(/[\\/]/).pop() : '';
    if (/^(Read|NotebookRead)$/.test(n)) return '讀 ' + f;
    if (/^(Edit|Write|MultiEdit|NotebookEdit)$/.test(n)) return '改 ' + f;
    if (/^(Grep|Glob|LS)$/.test(n)) return '找檔案';
    if (n === 'Bash') { const k = String(i.command || ''); return TEST_RE.test(k) ? '跑測試' : /\bgit\s+commit\b/.test(k) ? '提交' : '跑指令 ' + k.split('\n')[0].slice(0, 60); }
    if (/send|post/.test(n)) return '寫回覆';
    return toolLine(c);
  };
  // 席在跑:最下面一行「@席 正在 …」,時間從這一回合第一筆輸出算
  // 519d0 SPEC-wait-row-unify r0(Andy 11:5x「能不能都用同一種方式」):單席與共同對話同一個等回覆列。
  // 秒數從按下送出起算(S.wait[id].at);三段字:已送到等它開始 / 在想… / 正在<工具>;回合中再送 = 排在後面(aibd 回 202),不是錯
  const WAIT_MAX = 30 * 60 * 1000;
  function waitMark(id, text, queued) {
    const now = Date.now(); S.wait[id] = { at: now, queued: !!queued, started: 0 };
    if (text != null) (S.said[id] = S.said[id] || []).push({ t: new Date(now).toISOString(), text: String(text) });
  }
  function waitPhase(id) {
    // 剛開的席還沒有任何輸出 ⇒ turnOf 是 null;回合開始以 aibd 用量那列的 turn_started_at 為準
    let T = turnOf(id);
    if (!T) { const se = lastSession(id), u = se && S.usage && (S.usage.usage || []).find((x) => x.seat_id === id && x.session_id === (se.id || se.session_id)); if (u && 'turn_started_at' in u) T = { running: !!(u.turn_started_at && (se.state === 'running' || se.state === 'pending')), since: u.turn_started_at }; }
    const W = S.wait[id], run = !!(T && T.running && T.since);
    if (W) {
      const since = run ? Date.parse(T.since) : 0;
      if (run && !W.started && since >= W.at - 1500) { W.started = Date.now(); const L = (S.lat[id] = S.lat[id] || []); L.push(W.started - W.at); if (L.length > 10) L.shift(); }
      if ((!run && W.started) || Date.now() - W.at > WAIT_MAX) { delete S.wait[id]; if (document.hidden) S.doneDot = true; }
    }
    const V = S.wait[id];
    if (!V && !run) return null;
    let what;
    if (V && !V.started) what = V.queued || run ? '它正在忙,這句排在後面' : '已送到,等它開始';
    else { const Wt = S.out && workTools(id, String(T.since || ''), ''); let cur = null; if (Wt) for (const c of Wt.uses) if (String(c.id || '') && !Wt.RES.has(c.id)) cur = c; what = cur ? '正在' + DOING(cur) : '在想…'; }
    return { what, base: V ? V.at : Date.parse(T.since), run };
  }
  const median = (a) => { const b = a.slice().sort((x, y) => x - y); return b.length ? b[Math.floor((b.length - 1) / 2)] : 0; };
  function waitRow(id, shared) {
    const P = waitPhase(id); if (!P) return null;
    const s = seatById(id) || { seat_id: id, provider: 'claude' };
    const stl = P.run ? stallOf(id) : null;
    const L = S.lat[id] || [], med = L.length >= 3 ? median(L) : 0, el = Date.now() - P.base;
    const usual = med ? `<small class="wusual${el > 3 * med ? ' slow' : ''}" title="最近 ${L.length} 次從送出到它開始">通常 ${Math.max(1, Math.round(med / 1000))} 秒</small>` : '';
    const iso = new Date(P.base).toISOString();
    const head = stl && stl.none ? stallHtml(id, stl) : `<span class="spn"></span> ${esc(P.what)} · <b data-since="${esc(iso)}">${esc(durS(el))}</b>${usual}${stl ? ' ' + stallHtml(id, stl) : ''}`;
    const se = lastSession(id), sid = se && (se.id || se.session_id);
    const stop = sid && (se.state === 'running' || se.state === 'pending') ? `<button type="button" class="k wstop" data-stop="${esc(sid)}">${S.stopArm === sid ? '再按一次停止' : '先停手'}</button>` : '';
    return { t: shared ? '\uffff' + id : '\uffff', h: `<div class="msg runl${stl && stl.none ? ' stall' : ''}" data-stall="${stl ? (stl.none ? 'none' : 'quiet') : ''}"><div class="who">${logo(s, 'sm')}</div><div class="body">${head}</div>${stop}${shared ? `<button type="button" class="wsum" data-openseat="${esc(id)}"><b>看這一席 ›</b></button>` : ''}</div>` };
  }
  // 單席窗:自己直接送的話(aibd 的輸出裡沒有),送出當下就放進時間軸
  function saidRows(id) {
    return (S.said[id] || []).map((m) => ({ t: m.t, h: `<div class="wk in mine"><span><span class="lg sm you">你</span> ${esc(m.text.split('\n')[0].slice(0, 200))}</span></div>` }));
  }
  function runRows() {
    const ids = new Set(workSeats().concat(Object.keys(S.wait)));
    const out = [];
    for (const id of ids) { const r = waitRow(id, true); if (r) out.push(r); }
    return out;
  }
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
      return { t: String(m.created || ''), h: `<div class="msg ${mine ? 'me' : ''} ${!mine && m.unread ? 'unread' : ''}"><div class="who">${who}${copyBtn('m:' + (m.message_id || m.created), txt)}${favBtn('m:' + (m.message_id || m.created), txt, m.from_seat, m.created, { seat_id: m._seat, message_id: m.message_id })}</div><div class="body">${body}</div>${mine ? '' : workLine(m._seat, m.created)}<div class="tm">${esc(hhmm(m.created))}</div>${seen}</div>` };
    }).concat(itemRows(), pairRows(), tickRows(), runRows(), actRows()).sort((a, b) => a.t.localeCompare(b.t)).map((x) => x.h).join('');
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
    const answered = (p, seat) => (S.itemTalk || []).find((q) => q._tid === p._tid && q !== p && String(q.from_seat || '') === String(seat || '') && (replyOf(q) === pidOf(p) || String(q.created || '') > String(p.created || '')));
    return (S.itemTalk || []).map((p) => {
      const sn = sysNote(p); if (sn) return { t: String(p.created || ''), h: sn };
      const mine = p.sender_kind === 'human' || p.from_seat === S.me;
      const body = typeof p.body === 'string' ? esc(p.body) : `<span class="st">(${esc(p.len != null ? p.len : p.body && p.body.len != null ? p.body.len : '?')} 字,內建頁可讀)</span>`;
      const who = mine ? '<span class="lg sm you">你</span>' : `${logo(sOf(p.from_seat), 'sm')}<b class="wn">${esc(seatShort(sOf(p.from_seat)))}</b>`; // 519d0 ORDER-autodrive 清單 6:同品牌多席分不出誰說的 ⇒ 寫席名
      const calls = (p.calls || []).map((c) => {
        const bad = c.failed_code === 'safe_cannot_send_to_dgrs' ? '收不到' : c.failed_code || c.state === 'failed' ? '沒送到' : '';
        // 被 @ 的席之後在同一串發過言(自動帶回或自己 thread_send)⇒ 已回;送到了還沒回 ⇒ 等回答
        const ans = !bad && answered(p, c.seat);
        const fail = ans && autoOf(ans) === 'turn_failed'; // a9136 G13 r2:回合沒做成,aibd 代貼「沒做成:原因」
        const t = bad || (fail ? '沒做成' : ans ? '已回' : RAW[c.status] || c.state || '');
        const wait = !bad && !ans && /^(delivered|read|done)$/.test(String(c.status || '')) ? '<span class="st wait">等回答</span>' : '';
        return `<span class="at">@</span>${logo(sOf(c.seat), 'sm')}<span class="st ${bad || fail ? 'bad' : t === '已讀' || t === '已回' ? 'ok' : ''}">${esc(t)}</span>${wait}`;
      }).join(' ');
      return { t: String(p.created || ''), h: `<div class="msg it ${mine ? 'me' : ''}${AUTO_CLS[autoOf(p)] || ''}" data-pid="${esc(pidOf(p))}">${p._shared ? '' : `<div class="ith" data-node="${esc(p._node)}" title="打開這一項的對話">📌 ${esc(String(p._title).slice(0, 40))}</div>`}<div class="who">${who}${autoTag(p)}${copyBtn('p:' + pidOf(p), p.body)}${favBtn('p:' + pidOf(p), p.body, p.from_seat || (p.sender_kind === 'human' ? 'human:' : ''), p.created, { thread_id: p._tid || (S.nt && S.nt.thread_id) || '', message_id: pidOf(p) })}</div><div class="body">${body}</div>${mine || !p._shared ? '' : workLine(p.from_seat, p.created)}${docLink(p)}<div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}</div>` };
    });
  }
  // 0eed7 VERIFY-humanthread L1:內文提到的 @ 也會送 ⇒ 輸入框下面即時列「會送給誰」,送出前看得到
  function toLine(kind) {
    const t = S.drafts[kind] || '';
    const L = targets(t);
    const all = /(^|\s)@all(\s|$)/i.test(t);
    const A = targets.amb || [];
    if (A.length) return `<div class="tol bad" id="tol">${A.map((n) => '@' + esc(n)).join(' ')} 有兩席以上同名,不知道要給誰:點 @ 選頭像(名字後面有席碼),或打 @席碼</div>`;
    if (!L.length && !all) return `<div class="tol" id="tol"></div>`;
    const mem = kind === 'node' && S.nt ? new Set((S.nt.members || []).map((m) => String(m.seat || '').replace(/^seat:/, ''))) : null;
    const QL = L.filter((x) => queuedOf(x));
    return `<div class="tol" id="tol">會送給 ${all && kind === 'node' ? '這一項全部的席' : L.map((x) => logo(x, 'sm') + (mem && !mem.has(x.seat_id) ? '<em class="nw">先加進這一項</em>' : '') + (queuedOf(x) ? '<em class="nw">排隊中</em>' : '')).join(' ')}${QL.length ? `<span class="qwarn">${QL.map((x) => '@' + esc(seatShort(x))).join(' ')} 在排隊、還沒開始:${esc(qText(queuedOf(QL[0])))}</span>` : ''}</div>`;
  }
  function refreshTo(kind) { const o = document.getElementById('tol'); if (o) o.outerHTML = toLine(kind); }
  function sharedWin() {
    const P = present();
    const rows = sharedRows();
    const atpop = `<div class="atpop" id="atpop" ${S.atOpen ? '' : 'hidden'}>${P.map((s) => `<span data-at="${esc(s.seat_id)}">${logo(s, 'big')}<em class="atn">${esc(atName(s))}</em>${s.mode === 'dgrs' ? '<em class="dg">不擋工具</em>' : ''}</span>`).join('')}${P.length ? `<span class="all" data-at="all">@all${P.some((s) => s.mode === 'dgrs') ? '<em class="dg">含不擋工具的席</em>' : ''}</span>` : '<span class="st">沒有在跑的席</span>'}</div>`;
    return `${headHtml()}
      <div class="tl" id="tl">${rows || `<div class="empty">${soleSeat() ? '還沒有對話。寫一句送出,就交給 @' + esc(seatName(soleSeat())) + ';有兩席以上再用 @ 選。' : '還沒有對話。打 @ 選一席,寫一句送出。'}</div>`}</div>
      ${atpop}${askBar()}
      ${chatTools()}<div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="寫給大家;要誰接手就 @ 誰">${esc(S.drafts.shared || '')}</textarea><span class="gocol"><button type="button" class="go" data-send="shared" ${S.sending ? 'disabled' : ''}>送出</button>${enter2Box()}</span></div>${toLine('shared')}
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
  // aibd 一次給 100 則(最後寫入的那 100 則)+ next(更早一頁的 cursor,GET threads/{id}?cursor=)。
  // 匯入的串寫入順序 ≠ 時間順序 ⇒ 頁面合併各頁、照 created 排;重讀第一頁時保留已載入的更早頁(0eed7 VERIFY-dmcards L2)
  // 同一秒的兩則照 aibd 的寫入順序(7ebca 05:2x):每則記「第幾頁 · 頁內第幾則」,更早的頁排前
  const byTime = (a, b) => (String(a.created || '') < String(b.created || '') ? -1 : String(a.created || '') > String(b.created || '') ? 1 : (a._ord || 0) - (b._ord || 0));
  const tagPage = (L, page) => (L || []).map((p, i) => Object.assign(p, { _ord: page * 100000 + i }));
  const pk = (p) => String(p.message_id || p.post_id || '');
  function mergePosts(old, add) {
    const m = new Map(); for (const p of old) m.set(pk(p), p); for (const p of add) m.set(pk(p), p);
    return [...m.values()].sort(byTime);
  }
  async function loadNode() {
    if (!S.nt) return;
    const tid = S.nt.thread_id;
    const r = await get('/api/v1/threads/' + encodeURIComponent(tid));
    const same = S.postsTid === tid;
    S.nt = Object.assign({}, S.nt, r, { posts: undefined, next: undefined });
    if (!same) S.postsPage = 0;
    S.posts = mergePosts(same ? S.posts || [] : [], tagPage(r.posts, 0));
    if (!same) { S.postsTid = tid; S.postsNext = r.next || ''; }
  }
  // 往前讀一頁;回傳這頁讀了幾則
  async function loadOlder() {
    if (!S.nt || !S.postsNext) return 0;
    const tid = S.nt.thread_id;
    const r = await get('/api/v1/threads/' + encodeURIComponent(tid) + '?cursor=' + encodeURIComponent(S.postsNext));
    if (!S.nt || S.nt.thread_id !== tid) return 0;
    S.postsPage = (S.postsPage || 0) - 1;
    S.posts = mergePosts(S.posts || [], tagPage(r.posts, S.postsPage));
    S.postsNext = r.next || '';
    return (r.posts || []).length;
  }
  // 7ebca N-web-dmbridge(IFACE-dmbridge-7ebca-1119 r1):舊信件帳匯入 / 鏡像的信 item 帶 imported、dm_status…;頁面只顯示,不改舊帳
  const DM_ST = { done: ['做完', 'ok'], read: ['進行中', ''], hold_human: ['待放行', 'warn'], queued: ['未讀', 'un'], claimed: ['未讀', 'un'], delivered: ['未讀', 'un'], archived: ['歷史', 'old'] };
  function impTag(p) {
    const it = (p && typeof p.item === 'object' && p.item) || p || {};
    if (!it.imported) return '';
    const V2S = { held: 'hold_human', queued: 'queued', unread: 'delivered', in_progress: 'read', done: 'done', history: 'archived', superseded: 'archived', expired: 'archived', deleted: 'archived' };
    const ss = Object.hasOwn(V2S, it.dm_view) ? V2S[it.dm_view] : it.dm_status; // dm_view 先(7ebca 8cbf94a92)
    const st = Object.hasOwn(DM_ST, ss) ? DM_ST[ss] : null;
    const via = it.source && it.source.via === 'bridge' ? '即時鏡像' : '匯入';
    const why = ''; // 做完的理由改貼在原信底下(dmFoot,7ebca 8cbf94a92),小標只寫狀態
    // 7ebca 3aa5cbc55:舊式席的回覆以它的身分貼進「<碼> ↔ hub(信件匯入)」,item.dm_reply / source.reply = true
    // 歸卡依據(a9136 重匯後;欄位名 item.card_rule 待對):掛到哪張卡、照哪一條規則歸的
    const CR = { card_text: '依內文卡號', pr: '依 PR / 頭碼', reply: '依原信', context: '依前後文', file: '依檔名', unassigned: '未歸卡' };
    const cr = it.card_rule && Object.hasOwn(CR, it.card_rule) ? `<span class="imt cr" title="歸卡依據">${CR[it.card_rule]}${it.card ? ` · <a data-card="${esc(it.card)}">${esc(it.card)}</a>` : ''}</span>` : it.card ? `<span class="imt cr"><a data-card="${esc(it.card)}">${esc(it.card)}</a></span>` : '';
    const rep = it.dm_reply === true || (it.source && it.source.reply === true) ? '<span class="imt rp" title="舊式席回的信">回信</span>' : '';
    return `${rep}${cr}<span class="imt" title="舊信件帳 ${esc(it.dm_id || '')} · ${via}${it.sender_label ? ' · 原寄件 ' + esc(it.sender_label) : ''}">${via}</span>${st ? `<span class="dms ${st[1]}" ${why ? `title="${esc(why)}"` : ''}>${st[0]}${why && it.dm_status === 'done' ? ' · ' + esc(why.slice(0, 24)) + (why.length > 24 ? '…' : '') : ''}</span>` : ''}`;
  }
  // a9136 G13(IFACE-reply-in-thread r0):席回合結束自動貼回的那一則帶 auto:'turn_end'、reply_to:<觸發的貼文>;點了跳過去
  const pidOf = (p) => String((p && (p.post_id || p.message_id || p.id)) || '');
  // 0eed7 VERIFY-g13 L1:aibd 把 auto / reply_to 放在 item 裡;兩處都讀
  const autoOf = (p) => (p && (p.auto || (p.item && typeof p.item === 'object' ? p.item.auto : ''))) || '';
  const replyOf = (p) => (p && (p.reply_to || (p.item && typeof p.item === 'object' ? p.item.reply_to : ''))) || '';
  // a9136 G13:turn_end = 回合結束帶回;turn_failed = 回合沒做成(item.failed = 已清洗的原因),用失敗樣式
  const AUTO_CLS = { turn_end: ' auto', turn_failed: ' auto fail' };
  // a9136 75c087ca7:叫到沒在跑的席 ⇒ aibd 以 system:aibd 補一則,item.auto = not_running、item.not_running = [席碼];畫成系統提示,不算哪一席說的
  function sysNote(p) {
    if (autoOf(p) !== 'not_running') return '';
    const L = (p.item && Array.isArray(p.item.not_running) ? p.item.not_running : p.not_running) || [];
    // item.not_running 是完整 seat_id(a9136 183eaeed);換成席碼顯示,同 seats.rs seat_code
    const code = (c) => { const id = String(c).replace(/^seat:/, ''); const s = seatById(id); return (s && s.code) || id.replace(/^(seat_|old_)/, '').slice(0, 5); };
    const who = L.map((c) => '@' + esc(code(c))).join('、') || '有席';
    // 開起來後 aibd 會自動補送(seat_send redeliver),不必再叫 ⇒ 字不能叫人重發
    return `<div class="msg sysn" data-pid="${esc(pidOf(p))}"><span class="sysi">系統</span>${who} 現在沒在跑,開了就會收到這則<span class="tm">${esc(hhmm(p.created))}</span></div>`;
  }
  function autoTag(p) {
    const a = autoOf(p);
    if (a !== 'turn_end' && a !== 'turn_failed') return '';
    const rt = replyOf(p);
    const r = rt ? `<button type="button" class="rpl" data-jump="${esc(rt)}" title="跳到觸發這次回答的那一則">回覆 ↑</button>` : '';
    if (a === 'turn_failed') { const why = (p && (p.failed || (p.item && p.item.failed))) || ''; return `<span class="autot bad" title="${esc(typeof why === 'string' ? why : '')}">沒做成</span>${r}`; }
    return `<span class="autot" title="這一席回合結束時,AIB 用它的身份把最後回答貼回這一串">自動帶回</span>${r}`;
  }
  // 519d0 NOTE-import-regroup r1 §9:長串照日期收合,預設展開最近兩天;舊的那幾天一行「日期 · N 則」,點了展開
  const DAY_FOLD_MIN = 30;
  const dayOf = (iso) => { try { return new Date(iso).toLocaleDateString('zh-TW', { timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit' }).replace('/', '-'); } catch (e) { return ''; } };
  function dayFold(posts, rows, tid) {
    if (posts.length < DAY_FOLD_MIN) return rows.join('');
    const G = [];
    posts.forEach((p, i) => { const d = dayOf(p.created) || '沒有日期'; const g = G[G.length - 1]; if (g && g.d === d) g.r.push(rows[i]); else G.push({ d, r: [rows[i]] }); });
    if (G.length < 2) return rows.join('');
    const O = (S.openDays = S.openDays || {});
    return G.map((g, i) => {
      const recent = i >= G.length - 2, k = (tid || '') + '|' + g.d, open = recent ? O[k] !== false : O[k] === true;
      return `<button type="button" class="dayh ${open ? 'on' : ''}" data-day="${esc(k)}" data-last="${recent ? 1 : 0}">${esc(g.d)} · ${g.r.length} 則 ${open ? '▾' : '▸'}</button>${open ? g.r.join('') : ''}`;
    }).join('');
  }
  // 舊帳狀態篩選(r3 §3b):待放行 / 未讀 / 進行中 / 做完 / 歷史,各帶件數;預設只開前三種(那串前三種一則都沒有就全開)
  const ST_F = [['hold', '待放行', ['hold_human']], ['unread', '未讀', ['queued', 'claimed', 'delivered']], ['active', '進行中', ['read']], ['done', '做完', ['done']], ['old', '歷史', ['archived', 'superseded', 'expired']]];
  // 7ebca 8cbf94a92:每則帶 dm_view(queued / unread / in_progress / held / done / history / superseded / expired / deleted),有就照它;舊形狀退回 dm_status
  const VIEW = { held: 'hold', queued: 'unread', unread: 'unread', in_progress: 'active', done: 'done', history: 'old', superseded: 'old', expired: 'old', deleted: 'old' };
  const stKey = (p) => { const it = (p && p.item) || {}; if (Object.hasOwn(VIEW, it.dm_view)) return VIEW[it.dm_view]; const s = it.dm_status; const f = s && ST_F.find((x) => x[2].includes(s)); return f ? f[0] : ''; };
  // 原信底下:等什麼 / 做完的理由與證據 / 被哪封取代
  function dmFoot(p) {
    const it = (p && p.item) || {}, str = (v) => (typeof v === 'string' && v ? v : '');
    const L = [];
    if (str(it.dm_wait) && (it.dm_view === 'in_progress' || it.dm_view === 'held' || !it.dm_view)) L.push(`<div class="dmf dmw">⏳ ${/^等/.test(it.dm_wait) ? '' : '等:'}${esc(it.dm_wait)}</div>`);
    if (str(it.dm_done_why) || str(it.dm_done_evidence)) L.push(`<div class="dmf dmd">✅ 做完${str(it.dm_done_why) ? ':' + esc(it.dm_done_why) : ''}${str(it.dm_done_evidence) ? ` <span class="ev" title="證據">${esc(it.dm_done_evidence)}</span>` : ''}</div>`);
    if (str(it.dm_superseded_by)) L.push(`<div class="dmf dms2">↪ 有更新版:<a data-jumpmid="${esc(it.dm_superseded_by)}">${esc(it.dm_superseded_by)}</a></div>`);
    return L.join('');
  }
  function stSel() {
    const tid = (S.nt && S.nt.thread_id) || '', A = (S.stSel = S.stSel || {});
    if (!A[tid]) { const has = (k) => S.posts.some((p) => stKey(p) === k); A[tid] = ['hold', 'unread', 'active'].some(has) ? { hold: 1, unread: 1, active: 1 } : { hold: 1, unread: 1, active: 1, done: 1, old: 1 }; }
    return A[tid];
  }
  function stBar() {
    const C = {}; let any = 0; S.posts.forEach((p) => { const k = stKey(p); if (k) { C[k] = (C[k] || 0) + 1; any++; } });
    if (!any) return '';
    const on = stSel();
    return `<div class="cats stf">${ST_F.map(([k, t]) => `<button type="button" class="ct ${on[k] ? 'on' : ''}" data-stf="${k}" ${C[k] ? '' : 'disabled'}>${t} <i>${C[k] || 0}</i></button>`).join('')}</div>`;
  }
  function postRows() {
    const on = stSel(), shown = S.posts.filter((p) => { const k = stKey(p); return !k || on[k]; });
    const hid = S.posts.length - shown.length;
    const older = S.postsNext ? `<button type="button" class="more older" data-older="1" ${S.olderBusy ? 'disabled' : ''}>${S.olderBusy ? '讀取中…' : `載入更早的(已載入 ${S.posts.length} 則)`}</button>` : '';
    return older + postRowsOf(shown) + (hid ? `<div class="st hidn">另有 ${hid} 則在沒勾的狀態裡,點上面的狀態看</div>` : '');
  }
  // ---------- 對話框:連按兩次 Enter 送出 + 回覆一鍵複製(519d0 SPEC-chat-enter-copy r0,Andy 12:4x)----------
  const E2K = 'aib.ws.enter2';
  const enter2On = () => { try { return localStorage.getItem(E2K) === '1'; } catch (e) { return false; } };
  const enter2Box = () => `<label class="e2"><input type="checkbox" class="e2c" data-enter2="1"${enter2On() ? ' checked' : ''}>連按兩次 Enter 送出</label>`;
  const E2 = { at: 0 };
  // IME-BEGIN 1a93b 11-22(Andy 第二次:「沒打完中文,就一直自動送出英文」;9918e 派工):
  // 組字狀態自己記,不只靠 isComposing / 229。① 組字中、與組字結束後 IME_GAP 毫秒內的 Enter 一律不算(瀏覽器會在選字後再補一個
  // isComposing=false 的 Enter),連按兩次的計時也從組字結束後重算;② 組字中不重畫(重畫會換掉輸入框、打斷組字,
  // 還沒選的字母就直接上字),結束後補畫一次。組字卡住(焦點已不在頁面裡、或超過 IME_MAX)就不再擋重畫。
  // 5d33a REVIEW-ime(44a5658a07):接在 document 上,掛在 body 的事件視窗(dlg)也收得到(M1);結束後的間隔用事件自己的
  // timeStamp 量,中間夾一次長的重畫也不會把間隔量長(L2);組字卡超過 IME_MAX 也不再擋 Enter(L3)
  const IME = { on: false, since: 0, end: -1e9, held: false };
  const IME_GAP = 80, IME_MAX = 60000;
  const imeOn = () => IME.on && Date.now() - IME.since <= IME_MAX;
  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && e.timeStamp - IME.end < IME_GAP);
  const imeHold = (root) => {
    if (!imeOn() || !root.contains(document.activeElement)) return false;
    IME.held = true; return true;
  };
  function imeWire(root, redraw) {
    root.addEventListener('compositionstart', () => { IME.on = true; IME.since = Date.now(); E2.at = 0; }, true);
    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; setTimeout(redraw, IME_GAP); } };
    root.addEventListener('compositionend', end, true);
    root.addEventListener('focusout', end, true);
  }
  // IME-END
  // 原文(Markdown 原字)按訊息 id 存,按鈕只帶 id;同一則重畫沿用同一格
  const COPYT = new Map();
  function copyBtn(key, txt) {
    if (typeof txt !== 'string' || !txt.trim()) return '';
    COPYT.set(String(key), txt); if (COPYT.size > 600) COPYT.delete(COPYT.keys().next().value);
    return `<button type="button" class="cpy" data-copy="${esc(String(key))}" title="複製原文">${esc(copyWord(String(key)))}</button>`;
  }
  // 按完的字記在表裡(整頁每秒可能重畫,記在按鈕上會被洗掉)
  const COPYST = {};
  const copyWord = (key) => { const o = COPYST[key]; return o && Date.now() < o.until ? o.w : '複製'; };
  async function doCopy(b) {
    const key = b.dataset.copy, txt = COPYT.get(key); if (typeof txt !== 'string') return;
    const back = (w) => { COPYST[key] = { w, until: Date.now() + 1500 }; for (const n of document.querySelectorAll('[data-copy]')) if (n.dataset.copy === key) { n.textContent = w; n.classList.add('on'); } setTimeout(() => { delete COPYST[key]; for (const n of document.querySelectorAll('[data-copy]')) if (n.dataset.copy === key) { n.textContent = '複製'; n.classList.remove('on'); } }, 1500); };
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('no clipboard');
      // 權限框沒人按會一直等 ⇒ 1 秒沒回當成被拒
      await Promise.race([navigator.clipboard.writeText(txt), new Promise((ok, no) => setTimeout(() => no(new Error('clipboard_timeout')), 1000))]); back('已複製');
    } catch (e) {
      // 剪貼簿被拒 ⇒ 選取整則:畫面上選起那則(看得到選了什麼),另放一個頁外文字框裝原文、選好並取得焦點,
      // 重畫把那則換掉也不影響 ⌘C;按過複製、離開焦點或 30 秒後收掉
      const body = b.closest('.msg') && b.closest('.msg').querySelector('.body');
      const ta = document.createElement('textarea'); ta.className = 'cpyta'; ta.value = txt; ta.setAttribute('aria-hidden', 'true'); ta.readOnly = true; document.body.appendChild(ta);
      const drop = () => { if (ta.isConnected) ta.remove(); }; ta.addEventListener('copy', () => setTimeout(drop, 0)); ta.addEventListener('blur', drop); setTimeout(drop, 30000);
      ta.focus(); ta.select();
      let ok = false; try { ok = document.execCommand && document.execCommand('copy'); } catch (err) { ok = false; }
      if (ok) { drop(); back('已複製'); return; }
      if (body) { try { const r = document.createRange(); r.selectNodeContents(body); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); ta.focus(); ta.select(); } catch (err) { /* 選不起來:頁外框還在 */ } }
      back(/Mac|iPhone|iPad/.test(navigator.platform || '') ? '已選取,按 ⌘C' : '已選取,按 Ctrl+C');
    }
  }
  // ---------- 對話工具:📸 截圖 + 標註 / 🎨 取色 / ↑ 送出紀錄 / ☆ 收藏(519d0 SPEC-web-capture-history-fav r0,Andy 13:0x;頁面提案 IFACE-chat-tools-page-1a93b-1121,aibd 實際形狀 a9136 IFACE-chat-tools-aibd-a9136-1121 r1:讀寫都是 act)----------
  // 存放都在 aibd(換電腦、換瀏覽器都在);aibd 還沒有對應 act ⇒ 只在本頁有效(不寫瀏覽器儲存),工具列寫一句
  const CT = { att: [], hist: null, histOpen: false, histSel: 0, favs: null, favOpen: false, favQ: '', colors: [], pick: null, note: '' };
  const SECRETISH = /(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|[A-Za-z0-9+/_-]{40,})/;
  const ctHas = (t) => hasAct(t) === true;
  // ebab8 11:56(客戶 Windows 截圖):黃字印了內部介面名 ⇒ 白話、灰字、四語;截圖那件不寫在這裡(按了會說怎麼用系統截圖貼上)
  const CT_MISS = { 'zh-TW': '這一版 AIB 還不會保存送出紀錄和收藏,重新整理這一頁就會清空。', en: "This version of AIB doesn't save sent messages or favourites yet; reloading this page clears them.", ja: 'この版の AIB は送信履歴とお気に入りをまだ保存しません。このページを再読み込みすると消えます。', ko: '이번 버전의 AIB는 보낸 기록과 즐겨찾기를 아직 저장하지 않습니다. 이 페이지를 새로 고치면 지워집니다.' };
  const ctMissWord = () => CT_MISS[typeof askLang === 'function' ? askLang() : 'zh-TW'] || CT_MISS['zh-TW'];
  function chatTools() {
    const miss = ['send_history_list', 'fav_add', 'screen_capture'].filter((t) => !ctHas(t));
    const hist = CT.histOpen ? ctHistHtml() : '';
    return `<div class="ctw">${hist}<div class="ctools"><button type="button" class="ctb" data-ct="shot" title="截整個螢幕(AIB),在頁面上拖框、標註">📸 截圖</button><button type="button" class="ctb" data-ct="pick" title="取色:吸管或在截圖上點">🎨 取色</button><button type="button" class="ctb" data-ct="file" title="選一張圖(也可以直接貼上或拖進輸入框)">📎 附圖</button><button type="button" class="ctb ${CT.favOpen ? 'on' : ''}" data-ct="fav" title="右欄收藏">☆ 收藏</button><span class="cth">↑ 叫回送出紀錄 · 貼上 / 拖入圖片也可</span></div>${miss.some((t) => t !== 'screen_capture') ? `<div class="ctn ctmiss">${esc(ctMissWord())}</div>` : ''}${CT.att.length ? `<div class="atts">${CT.att.map((a, k) => `<span class="atc"><img class="atimg" src="${esc(a.thumb)}" alt="">圖 ${k + 1}<button type="button" class="ghx" data-attx="${k}" aria-label="拿掉這張">×</button></span>`).join('')}<span class="cth">送出時一起交給這一席(最多 4 張)</span></div>` : ''}${CT.note ? `<div class="ctn">${esc(CT.note)}</div>` : ''}${CT.pick ? ctPickHtml() : ''}</div>`;
  }
  // ---- 送出紀錄 100 則(↑ 叫回;同上一則不存;權杖樣字串不存)----
  async function ctHistLoad() {
    if (CT.hist) return CT.hist;
    CT.hist = [];
    if (ctHas('send_history_list')) { try { const r = await act('send_history_list', {}); if (r && Array.isArray(r.history)) CT.hist = r.history.filter((x) => x && typeof x.text === 'string').map((x) => ({ id: x.n, text: x.text, at: x.at, to: '' })).slice(0, 100); } catch (e) { /* 讀不到:本頁先記 */ } }
    return CT.hist;
  }
  // 送出後先放進本頁清單(aibd 在 seat_input 成功時自己記;下次開頁從 aibd 讀)
  async function ctHistAdd(text, to) {
    const t = String(text || '').trim(); if (!t || SECRETISH.test(t)) return;
    await ctHistLoad();
    if (CT.hist[0] && CT.hist[0].text === t) return;
    CT.hist.unshift({ id: null, text: t, to: String(to || ''), at: new Date().toISOString() }); CT.hist.length = Math.min(CT.hist.length, 100);
    if (ctHas('send_history_list')) CT.histStale = true; // 號碼(n)要重讀才有,刪單則前重讀
  }
  // 刪單則:aibd 用號碼 n;本頁剛加、還沒號碼的先重讀一次找同字那則
  async function ctHistDel(h) {
    let n = h.id;
    if (n == null) { try { const r = await act('send_history_list', {}); const m = r && Array.isArray(r.history) && r.history.find((x) => x && x.text === h.text); n = m ? m.n : null; } catch (e) { n = null; } }
    if (n != null) act('send_history_delete', { n }).catch((e) => say(errText(e), 'err'));
  }
  function ctHistHtml() {
    const L = CT.hist || [];
    const rows = L.slice(0, 30).map((x, i) => `<button type="button" class="cthr ${i === CT.histSel ? 'on' : ''}" data-cthist="${i}"><span class="cthx">${esc(String(x.text).split('\n')[0].slice(0, 120))}</span><em>${esc(hhmm(x.at))}${x.to ? ' · ' + esc(String(x.to).replace(/^seat:/, '@')) : ''}</em><span class="cthd" data-cthdel="${i}" role="button" tabindex="-1" title="刪這則">✕</span></button>`).join('');
    return `<div class="cthist" data-cthbox><div class="cthh"><b>送出紀錄</b> ${L.length} / 100 · ↑↓ 選、Enter 放回、Esc 關<button type="button" class="k sm2" data-cthclear="1">清空</button></div>${rows || '<div class="empty">還沒有送出過</div>'}<div class="cthf">看起來像密碼或權杖的內容不會存</div></div>`;
  }
  function ctHistPut(i) {
    const x = (CT.hist || [])[i]; const inp = document.getElementById('inp'); if (!x || !inp) return;
    CT.draft = CT.draft == null ? inp.value : CT.draft; // 未送草稿留著:Esc 回去
    inp.value = x.text; inp.dispatchEvent(new Event('input', { bubbles: true })); CT.histOpen = false; render();
    const i2 = document.getElementById('inp'); if (i2) { i2.focus(); i2.setSelectionRange(i2.value.length, i2.value.length); }
  }
  // ---- 收藏 ----
  const FAVM = new Map(); // 鍵 → {text, from, at, ref}
  async function ctFavLoad() {
    if (CT.favs) return CT.favs;
    CT.favs = [];
    if (ctHas('fav_list')) { try { const r = await act('fav_list', {}); if (r && Array.isArray(r.favorites)) CT.favs = r.favorites.filter((x) => x && typeof x.text === 'string').map((x) => ({ id: x.fav_id, key: '', text: x.text, from: x.seat_id ? 'seat:' + x.seat_id : '', at: x.at, tag: x.tag || '', ref: { message_id: x.message_id, seat_id: x.seat_id } })); } catch (e) { /* 本頁先記 */ } }
    render(); return CT.favs;
  }
  const favOf = (key) => { const m = FAVM.get(key), mid = m && m.ref && m.ref.message_id; return (CT.favs || []).find((f) => f.key === key || (mid && f.ref && f.ref.message_id === mid)); };
  function favBtn(key, txt, from, at, ref) {
    if (typeof txt !== 'string' || !txt.trim()) return '';
    FAVM.set(String(key), { text: txt, from: String(from || ''), at: String(at || ''), ref: Object.assign({ key: String(key) }, ref || {}) });
    const on = !!favOf(String(key));
    return `<button type="button" class="cpy fav ${on ? 'on' : ''}" data-fav="${esc(String(key))}" title="${on ? '取消收藏' : '收藏這則'}">${on ? '★' : '☆'}</button>`;
  }
  async function ctFavToggle(key) {
    await ctFavLoad();
    const f = favOf(key);
    if (f) { CT.favs = CT.favs.filter((x) => x !== f); if (f.id && ctHas('fav_delete')) act('fav_delete', { fav_id: f.id }).catch((e) => say(errText(e), 'err')); return render(); }
    const m = FAVM.get(key); if (!m) return;
    if (SECRETISH.test(m.text)) { say('這則看起來含密碼或權杖,不收藏(5d33a N2,跟送出紀錄同一套)', 'err'); return render(); }
    const nf = { id: '', key, text: m.text, from: m.from, at: m.at, ref: m.ref, tag: '' }; CT.favs.unshift(nf);
    // aibd 要 seat_id + message_id:寫的人是席就用它;你自己在共同對話寫的用這串的對象席(沒有就只在本頁)
    const sid = String(m.ref.seat_id || m.from || '').replace(/^seat:/, '');
    if (ctHas('fav_add') && sid && !/^human:/.test(sid) && m.ref.message_id) act('fav_add', { message_id: String(m.ref.message_id), seat_id: sid, text: m.text.slice(0, 65536) }).then((r) => { if (r && r.fav_id) nf.id = r.fav_id; }).catch((e) => say(errText(e), 'err'));
    render();
  }
  const favList = () => { const q = CT.favQ.trim().toLowerCase(); return (CT.favs || []).filter((f) => !q || (f.text + ' ' + (f.tag || '') + ' ' + f.from).toLowerCase().includes(q)); };
  function favBox() {
    if (!CT.favOpen) return '';
    const L = favList();
    const rows = L.slice(0, 50).map((f, i) => `<div class="fvr"><div class="fvm"><b>${esc(f.from ? f.from.replace(/^seat:/, '@').replace(/^human:.*/, '你') : '')}</b>${esc(hhmm(f.at))}${f.tag ? ' · ' + esc(f.tag) : ''}</div><div class="fvt" title="${esc(f.text)}">${esc(String(f.text).slice(0, 160))}</div><div class="fvb"><button type="button" class="k sm2" data-fvput="${i}">貼回輸入框</button><button type="button" class="k sm2" data-fvjump="${i}">跳到原對話</button><button type="button" class="k sm2" data-fav="${esc(f.key || f.ref && f.ref.key || '')}" data-fvid="${esc(f.id || '')}">☆ 取消</button></div></div>`).join('');
    return `<div class="evbox fvbox"><div class="bh"><span class="ek kfav">收藏</span> <span class="st">${(CT.favs || []).length} 則</span><button type="button" class="ghx" data-ct="fav" aria-label="收起">×</button></div><input class="apq fvq" data-fvq type="search" placeholder="搜尋收藏…" value="${esc(CT.favQ)}">${rows || '<div class="empty">還沒有收藏;每則對話右上的 ☆ 可以收</div>'}<div class="cthf">${ctHas('fav_add') ? '收藏存在 AIB,換電腦、換瀏覽器都在' : 'AIB 還沒有收藏:只在這一頁有效'}</div></div>`;
  }
  // ---- 取色 ----
  const hex2 = (n) => n.toString(16).padStart(2, '0').toUpperCase();
  function ctColor(r, g, b) {
    const R = r / 255, G = g / 255, B = b / 255, mx = Math.max(R, G, B), mn = Math.min(R, G, B), l = (mx + mn) / 2, d = mx - mn;
    let h = 0, s = 0;
    if (d) { s = d / (1 - Math.abs(2 * l - 1)); h = mx === R ? ((G - B) / d) % 6 : mx === G ? (B - R) / d + 2 : (R - G) / d + 4; h = Math.round(h * 60); if (h < 0) h += 360; }
    return { hex: '#' + hex2(r) + hex2(g) + hex2(b), rgb: `rgb(${r} ${g} ${b})`, hsl: `hsl(${h} ${Math.round(s * 100)}% ${Math.round(l * 100)}%)` };
  }
  function ctPicked(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '')); if (!m) return;
    const n = parseInt(m[1], 16); CT.pick = ctColor(n >> 16, (n >> 8) & 255, n & 255);
    CT.colors = [CT.pick.hex].concat(CT.colors.filter((c) => c !== CT.pick.hex)).slice(0, 12);
    if (ctHas('color_push')) act('color_push', { hex: CT.pick.hex.toLowerCase() }).then((r) => { if (r && Array.isArray(r.colors)) { CT.colors = r.colors.map((c) => String(c).toUpperCase()).slice(0, 12); render(); } }).catch(() => {});
    render();
  }
  function ctPickHtml() {
    const P = CT.pick;
    const val = P.hex ? ['hex', 'rgb', 'hsl'].map((k) => `<button type="button" class="pkv" data-pkcopy="${esc(P[k])}">${esc(P[k])}</button>`).join('') : `<div class="cthf">${esc(P.msg || '')}</div>`;
    return `<div class="pkbox" data-pkbox><div class="pkh">取色 · ${'EyeDropper' in window ? '吸管可點螢幕任何地方' : '這個瀏覽器沒有吸管:在截圖上點'}<button type="button" class="ghx" data-pkx="1" aria-label="關">×</button></div>${P.hex ? `<i class="pksw" data-bg="${esc(P.hex)}"></i>` : ''}<div class="pkvs">${val}</div><div class="pkrec">${CT.colors.map((c) => `<button type="button" class="pkc" data-pkput="${esc(c)}" title="${esc(c)} 放進輸入框" data-bg="${esc(c)}"></button>`).join('')}</div><div class="cthf">最近 12 色 ·${ctHas('color_push') ? ' 記在 AIB' : ' 只在這一頁'} · 點色塊放進輸入框</div></div>`;
  }
  async function ctColorsLoad() { if (CT.colorsTried || !ctHas('color_list')) return; CT.colorsTried = true; try { const r = await act('color_list', {}); if (r && Array.isArray(r.colors)) { CT.colors = r.colors.map((c) => String(c).toUpperCase()).slice(0, 12); render(); } } catch (e) { /* 只在本頁 */ } }
  async function ctPick() {
    ctColorsLoad();
    if ('EyeDropper' in window) { try { const r = await new window.EyeDropper().open(); return ctPicked(r && r.sRGBHex); } catch (e) { return; } } // 取消(Esc)
    CT.pick = { msg: '先按 📸 截圖或貼一張圖,在標註窗裡按「🎨 從圖上取色」再點圖' }; render();
  }
  // ---- 圖:截圖 / 貼上 / 拖入 / 選檔 ⇒ 標註窗 ----
  const b64Blob = (b64, mime) => { const bin = atob(b64), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return new Blob([u], { type: mime }); };
  async function ctShot() {
    if (!ctHas('screen_capture')) { CT.note = 'AIB 還沒有截圖:先用 ⌘⇧4(Mac)或 Win+Shift+S(Windows)截好,在輸入框貼上就會開標註窗'; return render(); }
    CT.note = '截圖中…'; render();
    try {
      const r = await act('screen_capture', {}); if (!r || !r.media_id) throw new Error('AIB 沒回圖');
      const g = await act('media_get', { media_id: r.media_id }); if (!g || typeof g.data_base64 !== 'string') throw new Error('AIB 沒回圖');
      CT.note = ''; render(); annOpen(b64Blob(g.data_base64, g.mime || 'image/png'), true);
    } catch (e) {
      const code = e && (e.code || e.reason);
      CT.note = code === 'screen_permission_needed' ? 'Mac 要先在「系統設定 → 隱私權與安全性 → 螢幕錄製」允許 AIB(你自己按),再按一次 📸' : code === 'capture_rate' ? '截太快了:一分鐘最多 6 張' : code === 'screen_capture_unsupported' ? '這台的 AIB 不能截圖' : '截圖失敗:' + errText(e);
      render();
    }
  }
  function ctFile() {
    const f = document.createElement('input'); f.type = 'file'; f.accept = 'image/png,image/jpeg,image/webp,image/gif';
    f.addEventListener('change', () => { if (f.files && f.files[0]) annOpen(f.files[0], false); }); f.click();
  }
  // 鍵名照平台寫:Mac 寫 ⌘ / ⇧⌘,其他(Windows)寫 Ctrl+ / Ctrl+Shift+(鍵盤本來就兩種都收)
  const annKeyName = (k) => (/Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ? k : k.replace('⇧⌘', 'Ctrl+Shift+').replace('⌘', 'Ctrl+'));
  // 標註窗:常駐在 body(整頁重畫不動它)。第一步拖框裁切(或整張),之後六樣工具;所有筆畫記成清單,⌘Z / ⇧⌘Z 照清單重畫
  const ANN = { el: null };
  const ANN_TOOLS = [['rect', '▭ 框'], ['ellipse', '◯ 圈'], ['arrow', '➚ 箭頭'], ['zoom', '🔍 放大鏡'], ['mosaic', '▦ 馬賽克'], ['text', 'T 文字']];
  const ANN_COLORS = ['#ff3b30', '#e2b34a', '#5fbf7a', '#5aa9ff', '#ffffff'];
  function annOpen(blob, fromShot) {
    if (!blob || !/^image\//.test(blob.type || 'image/png')) return;
    if (blob.size > 12 * 1024 * 1024) { CT.note = '圖太大(超過 12 MB)'; return render(); }
    // aibd 頁的 CSP 是 img-src 'self' data:(static_files.rs),blob: 網址載不進來 ⇒ 轉成 data: 網址
    const fr = new FileReader(), img = new Image();
    img.onload = () => annBuild(img, fromShot);
    img.onerror = () => { CT.note = '讀不了這張圖'; render(); };
    fr.onload = () => { img.src = String(fr.result || ''); };
    fr.onerror = img.onerror;
    fr.readAsDataURL(blob);
  }
  function annBuild(img, fromShot) {
    annClose();
    const d = document.createElement('div'); d.className = 'anndlg'; d.setAttribute('role', 'dialog'); d.setAttribute('aria-label', '截圖標註');
    d.innerHTML = `<div class="annh"><b>📸 ${fromShot ? '截圖' : '圖片'}</b><span class="annst" data-annst>拖框選範圍 · 或按「整張」· Esc 取消</span><span class="annw">${fromShot ? 'Mac 第一次要在系統設定允許「螢幕錄製」(你自己按)' : ''}</span><button type="button" class="ghx" data-annx="1" aria-label="關">×</button></div><div class="annc"><canvas class="annv" data-annv></canvas><input class="anntx" data-anntx hidden maxlength="200" placeholder="打字,Enter 放上"></div><div class="annt"><button type="button" class="k" data-annall="1">整張</button>${ANN_TOOLS.map(([k, w]) => `<button type="button" class="k" data-anntool="${k}" disabled>${w}</button>`).join('')}${ANN_COLORS.map((c, i) => `<button type="button" class="annsw ${i ? '' : 'on'}" data-anncol="${c}" data-bg="${c}" aria-label="顏色 ${c}"></button>`).join('')}<button type="button" class="k" data-annpick="1" disabled>🎨 從圖上取色</button><button type="button" class="k" data-annundo="1" disabled>${annKeyName('⌘Z')}</button><button type="button" class="k" data-annredo="1" disabled>${annKeyName('⇧⌘Z')}</button><span class="annouts"><button type="button" class="k annout" data-annclip="1" disabled>複製到剪貼簿</button><button type="button" class="k annout" data-annwebp="1" disabled>存成 webp</button><button type="button" class="go annok" data-annok="1" disabled>送進對話</button></span></div><div class="cthf">送出前請自己看一下:密碼、權杖、Email 的地方用「▦ 馬賽克」蓋掉</div>`;
    document.body.appendChild(d); ANN.el = d; ctPaint(d);
    Object.assign(ANN, { img, crop: null, drag: null, ops: [], redo: [], tool: 'rect', col: ANN_COLORS[0], picking: false, work: null });
    const cv = d.querySelector('[data-annv]');
    ANN.cv = cv; annFit(); annDraw();
    cv.addEventListener('pointerdown', annDown); cv.addEventListener('pointermove', annMove); cv.addEventListener('pointerup', annUp);
    d.addEventListener('click', annClick); d.addEventListener('keydown', annKey);
    addEventListener('resize', annResize);
    d.tabIndex = -1; d.focus();
  }
  function annResize() { if (ANN.el) { annFit(); annDraw(); } }
  function annClose() { if (ANN.el) { ANN.el.remove(); ANN.el = null; removeEventListener('resize', annResize); } }
  // 畫面座標 ↔ 圖座標:裁切前看整張,裁切後只看那一塊
  function annSrc() { return ANN.crop ? ANN.work : ANN.img; }
  function annFit() {
    const s = annSrc(), box = ANN.el.querySelector('.annc'), W = Math.max(200, box.clientWidth), H = Math.max(160, box.clientHeight);
    const sw = s.width || s.naturalWidth, sh = s.height || s.naturalHeight, k = Math.min(W / sw, H / sh, 1);
    ANN.k = k; ANN.cv.width = Math.round(sw * k); ANN.cv.height = Math.round(sh * k);
  }
  const annPt = (e) => { const b = ANN.cv.getBoundingClientRect(); return { x: (e.clientX - b.left) / ANN.k, y: (e.clientY - b.top) / ANN.k }; };
  function annDown(e) {
    const p = annPt(e);
    if (ANN.picking) { const c = annPixel(p); ANN.picking = false; annBtns(); return ctPicked(c); }
    if (ANN.crop && ANN.tool === 'text') return annText(p);
    ANN.drag = { a: p, b: p }; ANN.cv.setPointerCapture(e.pointerId);
  }
  function annMove(e) { if (ANN.drag) { ANN.drag.b = annPt(e); annDraw(); } }
  function annUp() {
    const D = ANN.drag; ANN.drag = null; if (!D) return;
    const r = { x: Math.min(D.a.x, D.b.x), y: Math.min(D.a.y, D.b.y), w: Math.abs(D.b.x - D.a.x), h: Math.abs(D.b.y - D.a.y) };
    if (!ANN.crop) { if (r.w > 8 && r.h > 8) annCrop(r); else annDraw(); return; }
    if (r.w < 3 && r.h < 3 && ANN.tool !== 'zoom') return annDraw();
    ANN.ops.push({ t: ANN.tool, col: ANN.col, a: D.a, b: D.b, r }); ANN.redo = []; annDraw(); annBtns();
  }
  function annCrop(r) {
    const s = ANN.img, c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(r.w)); c.height = Math.max(1, Math.round(r.h));
    c.getContext('2d').drawImage(s, Math.round(r.x), Math.round(r.y), c.width, c.height, 0, 0, c.width, c.height);
    ANN.crop = r; ANN.work = c; annFit(); annDraw(); annBtns();
    const st = ANN.el.querySelector('[data-annst]'); if (st) st.textContent = `${c.width} × ${c.height} · 選工具在圖上拖 · ${annKeyName('⌘Z')} 復原`;
  }
  function annBtns() {
    const d = ANN.el; if (!d) return;
    for (const b of d.querySelectorAll('[data-anntool]')) { b.disabled = !ANN.crop; b.classList.toggle('on', ANN.crop && b.dataset.anntool === ANN.tool); }
    d.querySelector('[data-annpick]').disabled = false; d.querySelector('[data-annpick]').classList.toggle('on', ANN.picking);
    d.querySelector('[data-annundo]').disabled = !ANN.ops.length; d.querySelector('[data-annredo]').disabled = !ANN.redo.length;
    for (const b of d.querySelectorAll('[data-annok],[data-annclip],[data-annwebp]')) b.disabled = !ANN.crop; d.querySelector('[data-annall]').hidden = !!ANN.crop;
  }
  // 把清單畫到一張 2d 上(k = 縮放;匯出時 k = 1)
  function annPaint(x, k) {
    const s = annSrc(); x.drawImage(s, 0, 0, s.width * k, s.height * k);
    for (const o of ANN.ops) {
      const r = { x: o.r.x * k, y: o.r.y * k, w: o.r.w * k, h: o.r.h * k }, lw = Math.max(2, 3 * k);
      x.save(); x.strokeStyle = o.col; x.fillStyle = o.col; x.lineWidth = lw; x.lineCap = 'round';
      if (o.t === 'rect') x.strokeRect(r.x, r.y, r.w, r.h);
      else if (o.t === 'ellipse') { x.beginPath(); x.ellipse(r.x + r.w / 2, r.y + r.h / 2, Math.max(1, r.w / 2), Math.max(1, r.h / 2), 0, 0, Math.PI * 2); x.stroke(); }
      else if (o.t === 'arrow') { const ax = o.a.x * k, ay = o.a.y * k, bx = o.b.x * k, by = o.b.y * k, an = Math.atan2(by - ay, bx - ax), hl = Math.max(10, 16 * k); x.beginPath(); x.moveTo(ax, ay); x.lineTo(bx, by); x.stroke(); x.beginPath(); x.moveTo(bx, by); x.lineTo(bx - hl * Math.cos(an - 0.45), by - hl * Math.sin(an - 0.45)); x.lineTo(bx - hl * Math.cos(an + 0.45), by - hl * Math.sin(an + 0.45)); x.closePath(); x.fill(); }
      else if (o.t === 'mosaic') { const cell = Math.max(6, Math.round(12 * k)), sc = document.createElement('canvas'), sw = Math.max(1, Math.ceil(r.w / cell)), sh = Math.max(1, Math.ceil(r.h / cell)); sc.width = sw; sc.height = sh; sc.getContext('2d').drawImage(s, o.r.x, o.r.y, Math.max(1, o.r.w), Math.max(1, o.r.h), 0, 0, sw, sh); x.imageSmoothingEnabled = false; x.drawImage(sc, 0, 0, sw, sh, r.x, r.y, r.w, r.h); }
      else if (o.t === 'zoom') { const R = Math.max(40, Math.max(r.w, r.h) / 2 || 60 * k), cx = o.a.x * k, cy = o.a.y * k, z = 2; x.beginPath(); x.arc(cx, cy, R, 0, Math.PI * 2); x.save(); x.clip(); x.drawImage(s, o.a.x - R / k / z, o.a.y - R / k / z, (2 * R) / k / z, (2 * R) / k / z, cx - R, cy - R, 2 * R, 2 * R); x.restore(); x.beginPath(); x.arc(cx, cy, R, 0, Math.PI * 2); x.stroke(); }
      else if (o.t === 'text' && o.s) { const fz = Math.max(14, 22 * k); x.font = `700 ${fz}px -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif`; x.lineWidth = Math.max(3, 4 * k); x.strokeStyle = '#000a'; x.strokeText(o.s, o.a.x * k, o.a.y * k); x.fillText(o.s, o.a.x * k, o.a.y * k); }
      x.restore();
    }
  }
  function annDraw() {
    const x = ANN.cv.getContext('2d'); x.clearRect(0, 0, ANN.cv.width, ANN.cv.height); annPaint(x, ANN.k);
    const D = ANN.drag; if (!D) return;
    const r = { x: Math.min(D.a.x, D.b.x) * ANN.k, y: Math.min(D.a.y, D.b.y) * ANN.k, w: Math.abs(D.b.x - D.a.x) * ANN.k, h: Math.abs(D.b.y - D.a.y) * ANN.k };
    x.save(); x.setLineDash([6, 4]); x.strokeStyle = ANN.crop ? ANN.col : '#e2b34a'; x.lineWidth = 2;
    if (!ANN.crop) { x.fillStyle = '#0009'; x.fillRect(0, 0, ANN.cv.width, r.y); x.fillRect(0, r.y + r.h, ANN.cv.width, ANN.cv.height - r.y - r.h); x.fillRect(0, r.y, r.x, r.h); x.fillRect(r.x + r.w, r.y, ANN.cv.width - r.x - r.w, r.h); x.fillStyle = '#e2b34a'; x.font = '12px sans-serif'; x.fillText(`${Math.round(r.w / ANN.k)} × ${Math.round(r.h / ANN.k)}`, r.x + 4, Math.max(12, r.y - 6)); }
    x.strokeRect(r.x, r.y, r.w, r.h); x.restore();
  }
  function annPixel(p) {
    const s = annSrc(), c = document.createElement('canvas'); c.width = 1; c.height = 1;
    const g = c.getContext('2d'); g.drawImage(s, Math.max(0, Math.floor(p.x)), Math.max(0, Math.floor(p.y)), 1, 1, 0, 0, 1, 1);
    const d = g.getImageData(0, 0, 1, 1).data; return '#' + hex2(d[0]) + hex2(d[1]) + hex2(d[2]);
  }
  function annText(p) {
    const t = ANN.el.querySelector('[data-anntx]'), b = ANN.cv.getBoundingClientRect(), box = ANN.el.querySelector('.annc').getBoundingClientRect();
    t.hidden = false; t.value = ''; t.style.left = (b.left - box.left + p.x * ANN.k) + 'px'; t.style.top = (b.top - box.top + p.y * ANN.k - 14) + 'px'; t._p = p; t.focus();
  }
  function annClick(e) {
    const x = e.target;
    if (x.closest('[data-annx]')) return annClose();
    if (x.closest('[data-annall]')) return annCrop({ x: 0, y: 0, w: ANN.img.naturalWidth, h: ANN.img.naturalHeight });
    const tl = x.closest('[data-anntool]'); if (tl) { ANN.tool = tl.dataset.anntool; ANN.picking = false; return annBtns(); }
    const cl = x.closest('[data-anncol]'); if (cl) { ANN.col = cl.dataset.anncol; for (const n of ANN.el.querySelectorAll('[data-anncol]')) n.classList.toggle('on', n === cl); return; }
    if (x.closest('[data-annpick]')) { ANN.picking = !ANN.picking; return annBtns(); }
    if (x.closest('[data-annundo]')) return annUndo(false);
    if (x.closest('[data-annredo]')) return annUndo(true);
    if (x.closest('[data-annok]')) return annDone();
    if (x.closest('[data-annclip]')) return annClip(x.closest('[data-annclip]'));
    if (x.closest('[data-annwebp]')) return annWebp(x.closest('[data-annwebp]'));
  }
  function annUndo(redo) { const A = redo ? ANN.redo : ANN.ops, B = redo ? ANN.ops : ANN.redo; if (A.length) { B.push(A.pop()); annDraw(); annBtns(); } }
  function annKey(e) {
    if (e.target.matches && e.target.matches('[data-anntx]')) {
      if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); const t = e.target; if (t.value.trim()) { ANN.ops.push({ t: 'text', col: ANN.col, a: t._p, b: t._p, r: { x: t._p.x, y: t._p.y, w: 0, h: 0 }, s: t.value.trim() }); ANN.redo = []; } t.hidden = true; annDraw(); annBtns(); }
      if (e.key === 'Escape') { e.target.hidden = true; e.stopPropagation(); }
      return;
    }
    if (e.key === 'Escape') { e.preventDefault(); return annClose(); }
    if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); annUndo(e.shiftKey); }
  }
  // 三個出口都用標註後的圖(馬賽克已蓋上),不給原圖(7b81e ADDENDUM-web-capture-webp-clip)
  function annCanvas() { const s = annSrc(), c = document.createElement('canvas'); c.width = s.width || s.naturalWidth; c.height = s.height || s.naturalHeight; annPaint(c.getContext('2d'), 1); return c; }
  const annB64 = (c) => { const u = c.toDataURL('image/png'); return u.slice(u.indexOf(',') + 1); };
  const annSay = (w, bad) => { const st = ANN.el && ANN.el.querySelector('[data-annst]'); if (st) { st.textContent = w; st.classList.toggle('bad', !!bad); } };
  const saveBlob = (blob, name) => { const fr = new FileReader(); fr.onload = () => { const a = document.createElement('a'); a.href = String(fr.result); a.download = name; document.body.appendChild(a); a.click(); a.remove(); }; fr.readAsDataURL(blob); };
  const shotName = (ext) => { const d0 = new Date(), p2 = (n) => String(n).padStart(2, '0'); return `截圖-${d0.getFullYear()}${p2(d0.getMonth() + 1)}${p2(d0.getDate())}-${p2(d0.getHours())}${p2(d0.getMinutes())}-${Math.random().toString(36).slice(2, 6)}.${ext}`; }; // 本機時間 + 4 碼(同舊版)
  // 剪貼簿:瀏覽器只收 png;要在點擊裡呼叫(Safari),所以 ClipboardItem 直接收 Promise
  function annClip(btn) {
    const c = annCanvas();
    try {
      if (!navigator.clipboard || !navigator.clipboard.write || typeof ClipboardItem === 'undefined') throw new Error('no clipboard');
      const p = new Promise((ok, no) => c.toBlob((b) => (b ? ok(b) : no(new Error('png'))), 'image/png'));
      Promise.race([navigator.clipboard.write([new ClipboardItem({ 'image/png': p })]), new Promise((ok, no) => setTimeout(() => no(new Error('clipboard_timeout')), 3000))])
        .then(() => { btn.textContent = '已複製'; setTimeout(() => { if (btn.isConnected) btn.textContent = '複製到剪貼簿'; }, 1500); annSay('已放進剪貼簿(png)'); })
        .catch(() => annSay('複製沒成功,改按存成 webp', true));
    } catch (e) { annSay('複製沒成功,改按存成 webp', true); }
  }
  // 存成 webp:aibd 轉(media_put → media_webp → media_get,無損);aibd 沒有就存 png
  async function annWebp(btn) {
    const c = annCanvas(); btn.disabled = true; const w0 = btn.textContent; btn.textContent = '存檔中…';
    try {
      if (!ctHas('media_webp') || !ctHas('media_put')) { c.toBlob((b) => { if (b) saveBlob(b, shotName('png')); }, 'image/png'); annSay('AIB 還沒有 webp 轉檔:先存成 png', true); return; }
      const p = await act('media_put', { png_base64: annB64(c) }); const w = await act('media_webp', { media_id: p.media_id });
      const g = await act('media_get', { media_id: w.media_id });
      saveBlob(b64Blob(g.data_base64, g.mime || 'image/webp'), g.download_name || shotName('webp')); annSay('已存成 webp(瀏覽器的「下載」夾)');
    } catch (e) { annSay('存 webp 沒成功:' + errText(e), true); }
    finally { if (btn.isConnected) { btn.disabled = false; btn.textContent = w0; } }
  }
  // 送進對話:先存進 aibd;交給席(media:[id])aibd 下一顆才有,在那之前不送 media 欄、寫清楚
  async function annDone() {
    const c = annCanvas(), btn = ANN.el.querySelector('[data-annok]'); btn.disabled = true; btn.textContent = '存圖中…';
    if (ctHas('media_put')) {
      try {
        const r = await act('media_put', { png_base64: annB64(c) }); if (!r || !r.media_id) throw new Error('AIB 沒回圖');
        if (CT.att.length >= 4) { btn.disabled = false; btn.textContent = '送進對話'; annSay('一次最多 4 張,先送出或拿掉一張', true); return; }
        const t = document.createElement('canvas'), k = Math.min(1, 96 / Math.max(c.width, c.height)); t.width = Math.max(1, Math.round(c.width * k)); t.height = Math.max(1, Math.round(c.height * k)); t.getContext('2d').drawImage(c, 0, 0, t.width, t.height);
        CT.att.push({ id: String(r.media_id), thumb: t.toDataURL('image/png') });
        annClose(); CT.note = ''; render(); const i2 = document.getElementById('inp'); if (i2) i2.focus(); return;
      } catch (e) { btn.disabled = false; btn.textContent = '送進對話'; annSay('存圖失敗:' + errText(e), true); return; }
    }
    c.toBlob((b) => { if (b) saveBlob(b, shotName('png')); }, 'image/png');
    annClose(); CT.note = 'AIB 還收不了圖(沒有 media_put):已下載成 png,先自己拖給那一席'; render();
  }
  // aibd 頁 CSP style-src 'self':標籤裡寫內聯樣式屬性會被擋 ⇒ 畫完後用 CSSOM 上色,只收 #RRGGBB
  function ctPaint(root) { for (const n of (root || document).querySelectorAll('[data-bg]')) if (/^#[0-9a-f]{6}$/i.test(n.dataset.bg)) n.style.background = n.dataset.bg; }
  function ctInsert(s) {
    const inp = document.getElementById('inp'); if (!inp) return;
    const a = inp.selectionStart != null ? inp.selectionStart : inp.value.length;
    inp.value = inp.value.slice(0, a) + s + inp.value.slice(a); inp.dispatchEvent(new Event('input', { bubbles: true })); inp.focus(); inp.setSelectionRange(a + s.length, a + s.length);
  }
  function postRowsOf(POSTS) {
    const RAW = { queued: '排著', claimed: '送出中', delivered: '已送到', read: '已讀', done: '已處理', archived: '已處理' };
    const sOf = (k) => { const id = String(k || '').replace(/^seat:/, ''); return seatById(id) || { seat_id: id, provider: 'claude' }; };
    const rows = POSTS.map((p) => {
      const sn = sysNote(p); if (sn) return sn;
      const mine = p.sender_kind === 'human' || p.from_seat === S.me;
      const body = typeof p.body === 'string' ? esc(p.body) : `<span class="st">(${esc(p.len != null ? p.len : p.body && p.body.len != null ? p.body.len : '?')} 字,內建頁可讀)</span>`;
      // 519d0 ORDER-autodrive 清單 6:三席以上同串時同品牌圖示分不出誰說的 ⇒ 每則寫席名,這一串有角色就加角色
      const fs = sOf(p.from_seat), frole = !mine && ((S.nt && S.nt.members) || []).find((m) => String(m.seat || '').replace(/^seat:/, '') === fs.seat_id);
      const who = mine ? '<span class="lg sm you">你</span>' : `${logo(fs, 'sm')}<b class="wn">${esc(seatShort(fs))}</b>${frole && frole.role ? `<em class="rl">${esc(ROLE_ZH[frole.role] || roleLabel(frole.role))}</em>` : ''}`;
      const calls = (p.calls || []).map((c) => {
        const bad = c.failed_code === 'safe_cannot_send_to_dgrs' ? '收不到' : c.failed_code || c.state === 'failed' ? '沒送到' : '';
        const t = bad || RAW[c.status] || c.state || '';
        return `${logo(sOf(c.seat), 'sm')}<span class="st ${bad ? 'bad' : t === '已讀' ? 'ok' : ''}">${esc(t)}</span>`;
      }).join(' ');
      const rb = (p.read_by || []).filter((k) => /^seat:/.test(k)).map((k) => logo(sOf(k), 'sm')).join('');
      const im = impTag(p);
      return `<div class="msg ${mine ? 'me' : ''}${im ? ' imp' : ''}${AUTO_CLS[autoOf(p)] || ''}${S.hlPid && S.hlPid === pidOf(p) ? ' hl' : ''}" data-pid="${esc(pidOf(p))}"><div class="who">${im && /seat:import$/.test(String(p.from_seat || p.from_key || '')) ? '<span class="lg sm imp" title="寄件人不明(匯入)">匯</span>' : who}${im}${autoTag(p)}${copyBtn('p:' + pidOf(p), p.body)}${favBtn('p:' + pidOf(p), p.body, p.from_seat || (p.sender_kind === 'human' ? 'human:' : ''), p.created, { thread_id: p._tid || (S.nt && S.nt.thread_id) || '', message_id: pidOf(p) })}</div><div class="body">${body}</div>${docLink(p)}${im ? dmFoot(p) : ''}<div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}${rb ? `<div class="seen rb"><span class="st">讀過</span>${rb}</div>` : ''}</div>`;
    });
    return dayFold(POSTS, rows, S.nt && S.nt.thread_id);
  }
  function nodeWin() {
    const M = (S.nt.members || []).map((m) => { const id = String(m.seat || '').replace(/^seat:/, ''); const s = seatById(id); return s ? `<span data-at="${esc(id)}">${logo(s, 'sm')}<b class="wn">${esc(seatShort(s))}</b><em class="rl">${esc(ROLE_ZH[m.role] || roleLabel(m.role))}</em></span>` : ''; }).join('');
    // 0eed7 VERIFY-humanthread M1:現有的席也能從這裡加進來 —— @ 選單列全部在場的席,不是成員的送出前先掛「旁聽」
    const mem = new Map((S.nt.members || []).map((m) => [String(m.seat || '').replace(/^seat:/, ''), m.role]));
    const P = present();
    const atpop = `<div class="atpop" id="atpop" ${S.atOpen ? '' : 'hidden'}>${P.map((s) => `<span data-at="${esc(s.seat_id)}">${logo(s, 'big')}<em class="atn">${esc(atName(s))}</em><em class="rl">${esc(mem.has(s.seat_id) ? ROLE_ZH[mem.get(s.seat_id)] || mem.get(s.seat_id) || '成員' : '加進來')}</em></span>`).join('')}${mem.size ? '<span class="all" data-at="all">@all</span>' : ''}</div>`;
    // 匯入的舊信件串(opened_by seat:import):不掛樹上的項目、只讀(回信 / 結信是下一片,等界線)
    const imp = S.nt.opened_by === 'seat:import' || /匯入[))]\s*$/.test(String(S.nt.title || '')) || (S.posts.length > 0 && S.posts.every((p) => impTag(p)));
    if (imp) return `<div class="lh2"><div class="ttl">📨 ${esc(S.nt.title || S.nt.thread_id)}</div></div>${stBar()}
      <div class="tl" id="tl">${postRows() || '<div class="empty">這串信是空的</div>'}</div>
      <div class="send old"><textarea class="inp" id="inp" rows="2" disabled placeholder="從舊信件帳匯入的信只能看;回信、結信在下一片"></textarea><button type="button" class="go" disabled>送出</button></div>`;
    return `${headHtml()}
      ${M ? `<div class="mem">${M}</div>` : ''}
      <div class="tl" id="tl">${postRows() || '<div class="empty">這一項還沒有對話。寫一句送出;要誰接手就 @ 誰。</div>'}</div>
      ${atpop}
      ${askBar()}${chatTools()}<div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="對這一項說;@ 誰 = 叫他">${esc(S.drafts.node || '')}</textarea><span class="gocol"><button type="button" class="go" data-send="node" ${S.sending ? 'disabled' : ''}>送出</button>${enter2Box()}</span></div>${toLine('node')}
      ${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
  }
  async function sendNode(text) {
    const all = /(^|\s)@all(\s|$)/i.test(text);
    const to = all ? 'all' : targets(text).map((s) => s.seat_id);
    if (!all && (targets.amb || []).length) { say(targets.amb.map((n) => '@' + n).join(' ') + ' 有兩席以上同名,這句沒送;點 @ 選頭像(名字後面有席碼),或打 @席碼', 'err'); return false; }
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
  const REC = { need: '真人需求', yes: '真人同意', bio: '指紋', cc: 'AI 遇事不決', sum: '總結', ho: '交接', drv: '自動推進', shd: '影子續跑', host: '主機', del: '刪除 / 清理', dev: '開發', idea: '建議' };
  // 用戶資料夾頁拿到的 need / why / 紀錄是 {len, sha256}(7b81e 12:27),不是字:只寫幾個字、內建頁可讀
  const txt = (v) => (typeof v === 'string' ? v : v && typeof v === 'object' && v.len != null ? '(' + v.len + ' 字,內建頁可讀)' : v == null ? '' : String(v));
  // 519d0 裁定 2(11-18 13:0x):紀錄分類只留 aibd 一份(7b81e),頁面不自己分;過渡期 kind 可空 ⇒ 不標、只算在「全部」
  function recordsOf(n) {
    // 分類只看 aibd(519d0 r16 裁定 2):kinds 全部(7b81e 430b6a99),舊版只有 kind;頁面不自己分
    if (Array.isArray(n.records)) return n.records.map((r) => {
      const ks = (Array.isArray(r.kinds) ? r.kinds : [r.kind]).filter((k) => REC[k]);
      // 7ebca 7fdfcdbb9:kind idea 的紀錄帶 mark(已自動同意)+ evidence(來源)
      const mk = [typeof r.mark === 'string' ? r.mark : '', typeof r.evidence === 'string' && r.evidence ? '來源 ' + r.evidence : ''].filter(Boolean).join(' · ');
      return { at: r.at || '', by: r.by || '', k: ks[0] || '', ks, t: txt(r.text) + (mk ? '(' + mk + ')' : '') };
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
    const ss = L.slice(0, 4).map((c) => S.seats.find((s) => s.code === c || s.seat_id === c || seatName(s) === c) || { seat_id: c, name: c });
    return ss.map((x) => logo(x, 'big')).join('') + `<span class="slpk" aria-hidden="true">${ss.map((x) => '@' + esc(seatName(x))).join(' · ')}</span>`;
  }
  // 事件的狀態欄:aibd 給的代號一律轉成介面用字(519d0 22:32;Andy 看到 seat_busy);沒對照的照原字、記一筆在 S.unkState
  // 7ebca 00:2x 六個 needs 代號 + a9136 CODES-aibd-states-a9136-1119.md 全表(沒列到的仍灰色斜體顯示原字)
  const STATE_TEXT = { owner_lost: '負責席已不在(關了或換艙),等統籌改派', owner_off_roster: '負責席不在名冊上,等統籌改派', no_owner: '找不到負責席', no_free_reviewer: '沒有空的審查席', seat_size_none: '負責席這輪額度不夠,派不出去', seat_busy: '負責席或審查席在忙,派不出去', busy: '忙碌中', seat_idle: '閒著', idle: '閒著', queued: '排隊中', seat_queued: '排隊中', pending: '準備中', running: '在跑', seat_running: '在跑', stopped: '已停', seat_stopped: '已停', stop_unconfirmed: '停了、還沒確認結束', failed: '開不起來', seat_failed: '開不起來', reply: '等回應', waiting: '等回應', seat_waiting: '等回應', decide: '等你決定', quiet: '說忙、沒動', seat_quiet: '說忙、沒動', done: '做完了', rejoin_failed: '接回失敗', offline: '連不上', two_ways: '兩個做法', usage_high: '用量偏高', login_needed: '要登入', none: '不用等人,席自己能做', needs_human: '要真人出手', delivered: '已送達', partial: '部分完成', clear: '沒事', exited: '已正常結束', killed: '被強制結束', interrupted: 'AIB 重啟時被中斷', cancelled: '被取消', issued: '碼已發、還沒兌換', awaiting_confirmation: '等短碼確認', confirmed: '已確認', revoked: '已撤銷', expired: '已過期', conflict: '合不進去', build_failed: '編不過', ready: '可用', observed_clean: '看得到的都停了', lineage_unavailable: '追不到子程序', root_unrecorded: '沒記到最上層程序', candidates_incomplete: '可疑程序沒查完', candidates_unreadable: '有可疑程序讀不到', candidates_unreadable_older: '讀不到的都比這席更早開', root_identity_lost: '最上層程序身份對不上', stop_failed: '停的動作失敗', unclassified: '其他原因' };
  // Andy 00:1x:沒對照的代號在頁面上用灰色斜體顯示原字,方便抓漏
  function stateHtml(v) { const t = stateText(v); if (!t) return ''; return t === String(v).trim() && /^[a-z][a-z0-9_.]*$/.test(t) ? `<i class="unk" title="這個狀態代號還沒有中文對照">${esc(t)}</i>` : esc(t); }
  function stateText(v) {
    const t = String(v || '').trim(); if (!t) return '';
    if (Object.hasOwn(STATE_TEXT, t)) return STATE_TEXT[t];
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
  function mchartWs(u, still, ttl, tog) {
    const rows = useRows(u), unit = Number(u && u.unit_s) || 3600;
    ttl = ttl || '每個 AI 花掉的 token'; tog = tog || '';
    const seats = rows.filter((r) => Array.isArray(r.hours) && r.hours.length);
    const W = 1000, H = 210, L = 46, R = 12, T = 14, Bm = 30;
    const head = (a, b) => `<div class="mch"><span>${a}</span><span>${b}</span></div>`;
    if (!seats.length) {
      let g = ''; for (let k = 0; k <= 3; k++) { const y = T + (k / 3) * (H - T - Bm); g += `<line x1="${L}" x2="${W - R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" class="mcg"/>`; }
      return `<div class="mc">${head(esc(ttl), tog)}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-sty="width:100%;height:210px" role="img" aria-label="還沒有用量">${g}<text x="${W / 2}" y="${H / 2}" class="mct" text-anchor="middle">還沒有用量</text></svg></div>`;
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
    return `<div class="mc ${still ? 'still' : ''}">${head(esc(ttl) + '(照日期,每 ' + per + '一點;粗線 = 合計)', tog + '最高 ' + numK(mx) + ' / ' + per)}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-sty="width:100%;height:210px" role="img" aria-label="這張卡各席花掉的 token">${g}${ln}</svg>${lg}</div>`;
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
    const own = rows.length ? headRow + sigma + rows.map((r) => { const t = tot5(r); return `<div class="ur ut">${seatLogos(r.seat_id || r.seat)}<b>${esc(durT(t.act))}</b><span>${esc(firstLast(r, unit) || '—')}</span><span>${t.rep | 0} 次回覆</span><b>${numK(spentOf(r))}</b><span>輸出 ${numK(t.out)} · 新輸入 ${numK(t.ni)}</span><span>重讀 ${numK(t.rr)}</span></div>`; }).join('') : (A.sp > 0 ? '<div class="none">這張卡自己沒有用量,用量都在底下各項(看下面那張表)</div>' : '<div class="none">這張卡自己還沒有用量</div>');
    const ownBox = `<details class="cu2" ${S.card.ownShut ? '' : 'open'}><summary class="cus" data-owncost="1">這張卡自己 · ${rows.length ? rows.length + ' 席 · 動手 ' + esc(durT(T.act)) + ' · 花掉 ' + numK(T.sp) : A.sp > 0 ? '這張卡自己沒有,用量都在底下' : '0 席'}</summary><div class="uw">${own}</div></details>`;
    let kidBox = '';
    if (kids && kids.length) {
      const KU = S.card.kidUse || {};
      // 7ebca d1cc27cfc:subtree.items = 直接子項各自含底下的小計(照 spent、最多 12、只列有用量);有就直接用,不再逐張讀
      const IT = st && Array.isArray(st.items) ? st.items : null;
      if (IT) for (const it of IT) if (!KU[it.id]) KU[it.id] = { seats: [], subtree: it, nSeats: Number(it.seats) || 0 };
      // 每項用它自己的 subtree(含孫項);沒有 subtree 才用它自己各席
      const kSp = (x) => (x.u && x.u.subtree ? Number(x.u.subtree.spent) || 0 : useRows(x.u).reduce((s, r) => s + spentOf(r), 0));
      const kAll = IT ? IT.map((it) => kids.find((k) => k.id === it.id) || { id: it.id, title: it.title || it.id }) : kids;
      const got = kAll.map((k) => ({ k, u: KU[k.id] })).filter((x) => x.u && kSp(x) > 0);
      const moreN = IT && st.items_used > got.length ? Number(st.items_used) - got.length : 0;
      got.sort((a, b) => kSp(b) - kSp(a)); got.splice(12);
      const sub = got.reduce((s, x) => s + kSp(x), 0);
      const body = !S.card.kidOpen ? '' : S.card.kidLoading ? '<div class="none">讀取中…</div>' : got.length ? got.map(({ k, u: ku }) => { const rs = ku && ku.nSeats != null ? { length: ku.nSeats } : useRows(ku), ks = ku && ku.subtree; const t = ks ? { out: Number(ks.output) || 0, ni: Number(ks.new_input) || 0, rr: Number(ks.reread) || 0, act: Number(ks.active_s) || 0, sp: Number(ks.spent) || 0 } : rs.reduce((a, r) => { const x = tot5(r); a.out += x.out; a.ni += x.ni; a.rr += x.rr; a.act += x.act; a.sp += spentOf(r); return a; }, { out: 0, ni: 0, rr: 0, act: 0, sp: 0 }); return `<div class="ur uc" data-card="${esc(k.id)}" role="button" tabindex="0"><span class="kid">${esc(k.id)}</span><span class="uct">${esc(String(k.title || k.id).slice(0, 40))}</span><span>${rs.length} 席 · 動手 ${esc(durT(t.act))}</span><b>${numK(t.sp)}</b><span>輸出 ${numK(t.out)} · 新輸入 ${numK(t.ni)} · 重讀 ${numK(t.rr)}</span></div>`; }).join('') : '<div class="none">底下的項目還沒有用量</div>';
      const moreH = moreN > 0 ? `<div class="none">另有 ${moreN} 項也有用量,沒列出(只列花最多的 12 張)</div>` : '';
      kidBox = `<details class="cu2" ${S.card.kidOpen ? 'open' : ''}><summary class="cus" data-kidcost="1">底下各項目的用量(照花掉的 token 排,最多列 12 張)${S.card.kidOpen && !S.card.kidLoading ? ' · ' + got.length + ' 張有用量 · 花掉 ' + numK(sub) : ''}</summary><div class="uw">${body}${S.card.kidOpen ? moreH : ''}</div></details>`;
    }
    // Andy 00:3x「下方明明有使用 token 數據,但是為什麼沒有畫出動畫?」:曲線跟標頭同一份。
    // aibd 給 subtree.seats(整棵子樹每席每格)就畫它;沒有就拼這張卡 + 已讀到的直接底下各項,標明只到直接底下
    const kidRows = []; for (const id in (S.card.kidUse || {})) kidRows.push(...useRows(S.card.kidUse[id]));
    const subS = st && Array.isArray(st.seats) && st.seats.length ? st.seats : mergeSeats(rows.concat(kidRows));
    const hasSub = A.sp > T.sp && subS.some((r) => Array.isArray(r.hours) && r.hours.length);
    const showOwn = !hasSub || (S.card.chartOwn && rows.length);
    const nItems = st ? Math.max(0, (Number(st.nodes) || 0) - (rows.length ? 1 : 0)) : Object.keys(S.card.kidUse || {}).filter((id) => useRows(S.card.kidUse[id]).length).length;
    const ttl = showOwn ? (hasSub ? '只這張卡 · 每個 AI 花掉的 token' : '每個 AI 花掉的 token') : '含底下 ' + nItems + ' 項 · 每個 AI 花掉的 token' + (st && Array.isArray(st.seats) ? '' : '(只到直接底下一層)');
    const tog = hasSub && rows.length ? `<button type="button" class="k sm2" data-chartown="1">${showOwn ? '看含底下' : '只看這張卡'}</button> ` : '';
    const chart = mchartWs(showOwn ? u : { seats: subS, unit_s: unit }, S.card.drawn, ttl, tog);
    return `<div class="cu0">${sum}${chart}${ownBox}${kidBox}</div>`;
  }
  // 同一席在不同項目的序列併成一條(格起點相同就相加)
  function mergeSeats(rs) {
    const m = new Map();
    for (const r of rs) {
      const k = String(r.seat_id || r.seat || ''); if (!k || !Array.isArray(r.hours)) continue;
      const o = m.get(k) || { seat_id: k, hours: [] }; o.hours = o.hours.concat(r.hours); m.set(k, o);
    }
    return [...m.values()];
  }
  async function loadKidCost() {
    const c = S.card; if (!c || c.kidUse) return;
    const kids = ((c.n && Array.isArray(c.n.children) && c.n.children.length ? c.n.children : null) || (S.kids && S.kids.get(c.id)) || []).slice(0, 40); // 全部讀(最多 40)再照花掉排、列前 12;只讀前 12 會漏掉排在後面但用得多的
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
    if (x.open) return openAt(x.open.tid, x.open.mid, x.text);
    if (x.doc) return openDocFile(x.doc);
    let id = x.node || cardOf(x.ref);
    if (!id && x.seat) {
      const a = await soft(get('/api/v1/assignments?seat=' + encodeURIComponent(x.seat))).catch(() => null);
      const L = (a && a.assignments) || [];
      const r = L.find((y) => y.role === 'author') || L[0];
      if (r && r.node_id) id = r.node_id;
    }
    return openCard(id || '', x);
  }
  // Andy 00:1x:時間線的線要停在最後一點的點心、「再看 N 筆」接在線的延伸上 ⇒ 量最後一則的高度,寫成 CSS 變數(CSSOM,CSP 允許)
  function tlFit(root) {
    for (const w of (root || document).querySelectorAll('.tlx .tlw')) {
      const last = w.lastElementChild; if (!last) continue;
      const tail = Math.max(0, last.offsetHeight - TL_DOT_Y);
      w.style.setProperty('--tlb', tail + 'px');
      const x = w.parentElement; if (x) x.style.setProperty('--tle', tail + 'px');
    }
  }
  const TL_DOT_Y = 9; // 時間那一行 line-height 18px 的中線 = 點心(ws.css .cdlg .te time / .te:before 同一個數)
  window.addEventListener('resize', () => { const d = document.querySelector('.cdlg[open]'); if (d) tlFit(d); });
  // 頂上只放短標題(第一個分句、最多 18 字),全文只在「這則事件」出現一次
  function shortTitle(t) { const x = String(t || '').split(/[;;。,,::]/)[0].trim() || String(t || ''); return x.length > 18 ? x.slice(0, 18) + '…' : x; }
  // 點頭像看這一席當下的資訊:Andy 00:2x「之後要支援…但先不設計」⇒ 先只留這個入口
  function seatInfo(id) { say('之後點頭像會看到這一席當下的資訊(還沒做)'); return id; }
  function pinList(q) {
    const k = String(q || '').trim().toLowerCase(), L = [];
    if (S.tree) for (const n of S.tree.values()) { if (n.status === '作廢' || n.kind === 'suggestion') continue; if (!k || String(n.id).toLowerCase().includes(k) || String(n.title || '').toLowerCase().includes(k)) L.push(n); if (L.length >= 30) break; }
    return L.length ? L.map((n) => `<button type="button" class="pinr" data-pinto="${esc(n.id)}"><span class="ecd">${esc(n.id)}</span><span class="et">${esc(n.title || n.id)}</span></button>`).join('') : '<div class="empty">沒有找到</div>';
  }
  // ---------- 判斷 · 校正 · 總結 · 交接(519d0 NOTE-import-regroup r5 §3c,Andy 05:0x;a9136 da7bfca2b)----------
  // 校正貼文:source.kind = correction,item.track / why / idea / card_said / seat;同席同卡多則 ⇒ 頁面自己連成曲線
  const kindOf = (p) => { const it = (p && p.item) || {}; return (it.source && it.source.kind) || it.kind || ''; };
  function spark(L) {
    const V = L.map((x) => (x == null ? null : Math.max(0, Math.min(100, Number(x))))), n = V.length;
    if (n < 2) return '';
    const W = 120, H = 28, X = (i) => ((i / (n - 1)) * (W - 4) + 2).toFixed(1), Y = (v) => (H - 2 - (v / 100) * (H - 4)).toFixed(1);
    const pts = V.map((v, i) => (v == null ? null : X(i) + ',' + Y(v))).filter(Boolean).join(' ');
    return `<svg class="spk" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true"><line x1="0" x2="${W}" y1="${Y(70)}" y2="${Y(70)}" class="spk70"/><polyline points="${pts}"/></svg>`;
  }
  async function loadCorr(id) {
    const T = await soft(get('/api/v1/threads?node=' + encodeURIComponent(id) + '&limit=20')).catch(() => null);
    const L = ((T && T.threads) || []).slice(0, 4);
    const out = [];
    for (const t of L) {
      const r = await soft(get('/api/v1/threads/' + encodeURIComponent(t.thread_id))).catch(() => null);
      for (const p of (r && r.posts) || []) if (kindOf(p) === 'judgement' || kindOf(p) === 'correction') out.push(p); // a9136 f05f3d088:每次判斷 = judgement;correction = 偏離 → 拉回
    }
    return out.sort(byTime);
  }
  // 掛在這一項的事件卡(slot_pin,GET slots 的 pin);點了開那張事件
  function pinnedSec(id) {
    if (!id) return '';
    const L = slots().filter((x) => x.pin === id);
    if (!L.length) return '';
    return `<div class="dsec"><span>掛在這一項的事件</span><span>${L.length} 張</span></div><div class="pins">${L.map((x) => `<button type="button" class="pinr" data-evopen="${esc(x.key)}"><span class="ek ${TAG_K[x.tag] || 'k3'}">${esc(x.tag || LV_TAG[x.lv] || '')}</span>${esc(shortTitle(x.text))}</button>`).join('')}</div>`;
  }
  function corrSec() {
    const C = S.card && S.card.corr; if (!C || !C.length) return '';
    const G = new Map();
    for (const p of C) { const it = p.item || {}; const k = String(it.seat || p.from_seat || '').replace(/^seat:/, ''); if (!G.has(k)) G.set(k, []); G.get(k).push(p); }
    const open = S.card.corrOpen || '';
    const hasJ = C.some((q) => kindOf(q) === 'judgement');
    const rows = [...G.entries()].map(([k, A]) => {
      const L = hasJ ? A.filter((p) => kindOf(p) === 'judgement') : A, pulls = hasJ ? A.filter((p) => kindOf(p) === 'correction') : [];
      if (!L.length) return '';
      const last = L[L.length - 1].item || {}, tr = last.track;
      const low = L.slice(-3).length === 3 && L.slice(-3).every((p) => p.item && p.item.track != null && p.item.track < 70); // 70 = 主軸上(座艙 ON_TRACK_MIN)
      const det = open === k ? `<div class="crl">${pulls.length ? `<div class="cri pull">拉回:${pulls.slice(-5).map((p) => `${esc(hhmm(p.created))} ${esc((p.item || {}).from_track)}→${esc((p.item || {}).track)}%`).join(' · ')}</div>` : ''}${L.slice().reverse().slice(0, 20).map((p) => { const it = p.item || {}; const tx = (v) => (typeof v === 'string' ? esc(v) : v && v.len != null ? `(${v.len} 字)` : ''); return `<div class="cri"><b>${it.track != null ? esc(it.track) + '%' : '—'}</b><span>${esc(hhmm(p.created))}</span>${it.why ? `<i>為什麼:${tx(it.why)}</i>` : ''}${it.idea ? `<i>建議:${tx(it.idea)}</i>` : ''}${it.card_said ? `<i class="cs">卡號取自:${esc({ status_line: '狀態列', card_text: '卡上文字', reply: '回覆', file: '檔案', pr: 'PR' }[it.card_said] || '')}${{ status_line: 1, card_text: 1, reply: 1, file: 1, pr: 1 }[it.card_said] ? '' : tx(it.card_said)}</i>` : ''}</div>`; }).join('')}</div>` : '';
      return `<button type="button" class="crr ${low ? 'low' : ''} ${open === k ? 'on' : ''}" data-corr="${esc(k)}"><span class="crw">@${esc(k)}</span><b>${tr != null ? esc(tr) + '%' : '—'}</b>${spark(L.map((p) => p.item && p.item.track))}<small>最近 ${L.length} 次判斷${pulls.length ? ' · 拉回 ' + pulls.length + ' 次' : ''}</small></button>${det}`;
    }).join('');
    return `<div class="dsec"><span>各席判斷(自評 · 席寫的,讀這張卡最近四串)</span><span>${G.size} 席</span></div><div class="crs">${rows}</div>`;
  }
  // 席視窗頂上那排:每席每台北日一則 seat_day(item.judge / corrections / summaries / handoffs / first_goal / goal / day)
  // 讀法(a9136 05:2x f8087d4dba):各席一條固定串,標題「<席碼> · 每日(匯入)」、分類 seat_day ⇒ GET threads?category=seat_day 找標題開頭是席碼那條,取最新一則
  let seatDayList = null;
  async function loadSeatDay(code) {
    S.seatDay = S.seatDay || {}; if (S.seatDay[code] !== undefined) return; S.seatDay[code] = null;
    if (!seatDayList) seatDayList = soft(get('/api/v1/threads?category=seat_day&limit=200')).then((T) => (T && T.threads) || []).catch(() => []);
    const t = (await seatDayList).find((x) => String(x.title || '').startsWith(code)); if (!t) return;
    const r = await soft(get('/api/v1/threads/' + encodeURIComponent(t.thread_id))).catch(() => null);
    const L = ((r && r.posts) || []).filter((p) => kindOf(p) === 'seat_day' && (!p.item.seat || String(p.item.seat) === code));
    L.sort((a, b) => String((a.item || {}).day || a.created).localeCompare(String((b.item || {}).day || b.created)));
    S.seatDay[code] = L.length ? L[L.length - 1].item : null; render();
  }
  function judgeRowFor(s) {
    const sid = String(s.seat_id || ''); // 舊式席:old_<碼>,或 aibd 直接給五碼(a9136 759248c6 起)
    const code = String(s.code || (sid.startsWith('old_') ? sid.slice(4) : /^[0-9a-f]{5}$/i.test(sid) ? sid : '')).toLowerCase();
    if (!/^[0-9a-f]{5}$/.test(code)) return '';
    loadSeatDay(code); return judgeRow(code);
  }
  function judgeRow(code) {
    const d = S.seatDay && S.seatDay[code]; if (!d) return '';
    const tx = (v) => (typeof v === 'string' ? v : v && v.len != null ? `(${v.len} 字)` : '');
    const g = (o) => (o && o.item ? tx(o.item) : '');
    const open = S.judgeOpen === code;
    // at 是 unix 秒(a9136 真匯入)或 ISO 字;一律換成本機時間「MM-DD HH:mm」
    const at = (o) => { const v = o && o.at; if (v == null || v === '') return ''; const t = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(String(v)); if (isNaN(t)) return ''; const z = (n) => String(n).padStart(2, '0'); return `${z(t.getMonth() + 1)}-${z(t.getDate())} ${z(t.getHours())}:${z(t.getMinutes())}`; };
    const det = open ? `<div class="jdet"><div><span>最初目標</span>${esc(g(d.first_goal))}<small>${esc(at(d.first_goal))}</small></div><div><span>目前目標</span>${esc(g(d.goal))}<small>${esc(at(d.goal))}</small></div><div class="jsrc">${esc(d.day || '')} · 席自己寫的自評,不是驗過的事實</div></div>` : '';
    return `<button type="button" class="jrow ${open ? 'on' : ''}" data-judge="${esc(code)}" title="今天(台北):校正 = 從偏離(低於 70)拉回主軸的次數;總結 / 交接 = 今天寫了幾份"><span class="jk">${d.day ? esc(String(d.day).slice(5)) : '今天'}</span><span class="jk">判斷 <b>${d.judge != null ? esc(d.judge) + '%' : '—'}</b></span><span class="jk">校正 <b>${esc(d.corrections || 0)}</b></span><span class="jk">總結 <b>${esc(d.summaries || 0)}</b></span><span class="jk">交接 <b>${esc(d.handoffs || 0)}</b></span><span class="jg" title="${esc(g(d.goal))}">${esc(g(d.goal).slice(0, 40))}</span></button>${det}`;
  }
  // 事件卡掛到項目:act slot_pin {card_id, node_id|null}(7ebca e74d1dfa9);舊 aibd 沒這個 act ⇒ 只標在頁面、標示意
  // aibd 只收確認過的內建頁;用戶頁 / 帶過來的配對 403,舊 aibd 400 不認得這個動作
  function actRefused(e) {
    const c = e && e.body && e.body.code;
    if (c === 'slot_built_in_page_only') return '這一頁不能按,要在這台的內建頁按';
    if (c === 'carried_pair_read_and_safe_only') return '帶過來的配對只能看,要在這台的內建頁按';
    // 遇事不決定稿 ideas-2 §2:同意 / 不同意 / 看過都要指紋;這版 aibd 還沒接指紋 ⇒ 503,卡留著
    if (c === 'needs_fingerprint') return (typeof fpSay === 'function' ? fpSay : (t) => t)('請在這台按指紋;按完再點一次');
    if (c === 'owner_check_unavailable') return (typeof fpSay === 'function' ? fpSay : (t) => t)('要先按指紋確認是你本人,這版 AIB 還沒接上指紋;這張卡留著,接好再按');
    if (e && e.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request')) return '這版 AIB 還不能按,等新版';
    return '';
  }
  // 要你決定(seat.asked):鈕字頁面寫死四語(7ebca:卡上不帶字)
  const ASK_WORD = {
    'zh-TW': { firstL: '前一則', secondL: '後一則', first: '照前一則', second: '照後一則', doneFirst: '已選:照前一則', doneSecond: '已選:照後一則', odd: '這種題目要在串裡看', review: '照審查改', keep: '維持原需求', open: '打開串', doneReview: '已選:照審查改', doneKeep: '已選:維持原需求', answered: '已經答過了' },
    en: { firstL: 'Earlier', secondL: 'Later', first: 'Go with the earlier one', second: 'Go with the later one', doneFirst: 'Chosen: the earlier one', doneSecond: 'Chosen: the later one', odd: 'Read this one in the thread', review: 'Go with the review', keep: 'Keep my request', open: 'Open thread', doneReview: 'Chosen: go with the review', doneKeep: 'Chosen: keep my request', answered: 'Already answered' },
    ja: { firstL: '前', secondL: '後', first: '前の方に従う', second: '後の方に従う', doneFirst: '選択済み:前の方', doneSecond: '選択済み:後の方', odd: 'スレッドで確認してください', review: 'レビューに従う', keep: '元の依頼のまま', open: 'スレッドを開く', doneReview: '選択済み:レビューに従う', doneKeep: '選択済み:元の依頼のまま', answered: '回答済み' },
    ko: { firstL: '앞', secondL: '뒤', first: '앞의 요청대로', second: '뒤의 요청대로', doneFirst: '선택함: 앞의 요청', doneSecond: '선택함: 뒤의 요청', odd: '스레드에서 확인하세요', review: '리뷰대로 수정', keep: '원래 요청 유지', open: '스레드 열기', doneReview: '선택함: 리뷰대로 수정', doneKeep: '선택함: 원래 요청 유지', answered: '이미 답했습니다' },
  };
  const askLang = () => { const l = String((navigator.languages && navigator.languages[0]) || navigator.language || '').toLowerCase(); return /^zh/.test(l) ? 'zh-TW' : /^ja/.test(l) ? 'ja' : /^ko/.test(l) ? 'ko' : /^en/.test(l) ? 'en' : 'zh-TW'; };
  // 519d0 ORDER-autodrive 清單 1(Andy 11-20 00:2x 那輪 23 分有 14 分不知道在等他):要你決定的題目直接在對話裡出按鈕列、
  // 分頁標題前面加「(N) 等你決定」、新題目到時響一聲。題目來自卡槽 seat.asked(第一輪 slots 沒帶到就讀待人決策第 1 頁)
  S.askDone = S.askDone || {};
  function pendingAsks() {
    const L = slots().filter((x) => x.kind === 'seat.asked' && x.card);
    if (S.slotCounts && slotCount(1) > 0) { const P = evPage(1, evByCls()); for (const x of P.items || []) if (x.kind === 'seat.asked' && x.card) L.push(x); }
    const seen = new Set();
    return L.filter((x) => { if (seen.has(x.card) || S.dismissed.has(x.card) || S.askDone[x.card]) return false; seen.add(x.card); return true; });
  }
  function askBar() {
    const L = pendingAsks(); if (!L.length) return '';
    const W = ASK_WORD[askLang()];
    return `<div class="askbar">${L.slice(0, 3).map((x) => `<div class="askr"><span class="askt"><b>等你決定</b>${x.who ? esc(x.who) + ' · ' : ''}${esc(x.text)}</span>${askQuotes(x)}<span class="askb">${(() => { const O = askOpts(x); return O.length ? O.map(([v, t], i) => `<button type="button" class="k ${i ? '' : 'g'}" data-askq="${v}" data-acard="${esc(x.card)}">${esc(t)}</button>`).join('') : `<span class="askm">${esc(W.odd)}</span>`; })()}${x.ask ? `<button type="button" class="k" data-askopen="${esc(x.ask.tid)}" data-amid="${esc(x.ask.mid)}">${esc(W.open)}</button>` : ''}</span></div>`).join('')}${L.length > 3 ? `<div class="askm">還有 ${L.length - 3} 題,在右欄「待人決策」</div>` : ''}</div>`;
  }
  async function askQuick(card, answer) {
    if (!card || S.askBusy) return;
    S.askBusy = true; render();
    const W = ASK_WORD[askLang()];
    try {
      await act('decision_answer', { card_id: card, answer });
      S.askDone[card] = answer; S.dismissed.add(card); say(askDoneText(answer));
    } catch (e) {
      const c = e && (e.code || (e.body && e.body.code));
      if (c === 'already_answered') { S.askDone[card] = '?'; S.dismissed.add(card); say('這一題已經有人答過了'); }
      else if (c === 'no_decision') { S.askDone[card] = '?'; S.dismissed.add(card); say('這一題已經不在等你決定'); }
      else say(actRefused(e) || '沒送出:' + errText(e), 'err');
    }
    S.askBusy = false; render(); tick(true);
  }
  // 分頁標題 + 響一聲:新出現的題目才響(開頁時已在的只改標題);瀏覽器擋自動播放就只改標題
  let askHeard = null;
  function askNotice() {
    const L = pendingAsks(), n = L.length;
    S.askPfx = n ? '(' + n + ') 等你決定 · ' : ''; titleNow();
    const ids = new Set(L.map((x) => x.card));
    if (askHeard && [...ids].some((k) => !askHeard.has(k))) askBeep();
    if (S.paired) askHeard = ids;
  }
  function askBeep() {
    try {
      const C = window.AudioContext || window.webkitAudioContext; if (!C) return;
      const a = askBeep.ctx || (askBeep.ctx = new C()); if (a.state === 'suspended') a.resume().catch(() => {});
      [0, 0.18].forEach((d, i) => { const o = a.createOscillator(), g = a.createGain(); o.frequency.value = i ? 880 : 660; g.gain.setValueAtTime(0.0001, a.currentTime + d); g.gain.exponentialRampToValueAtTime(0.15, a.currentTime + d + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + d + 0.16); o.connect(g).connect(a.destination); o.start(a.currentTime + d); o.stop(a.currentTime + d + 0.17); });
    } catch (e) { /* 沒有聲音就只靠標題 */ }
  }
  // 7ebca c25d44fd6:題型在 open.kind。review_vs_person(舊) = 照審查改 / 維持原需求;person_vs_person = 你自己前後兩則衝突,
  // 鈕字照前一則 / 照後一則、鈕上方放那兩則原文(你自己寫的字,不是席的字);不認得的題型不出鈕,只給打開串
  function askOpts(x) {
    const W = ASK_WORD[askLang()], k = (x.ask && x.ask.kind) || 'review_vs_person';
    if (k === 'review_vs_person') return [['review', W.review], ['keep', W.keep]];
    if (k === 'person_vs_person') return [['first', W.first], ['second', W.second]];
    return [];
  }
  const askDoneText = (a) => { const W = ASK_WORD[askLang()]; return ({ review: W.doneReview, keep: W.doneKeep, first: W.doneFirst, second: W.doneSecond })[a] || W.answered; };
  S.askQuote = S.askQuote || {};
  function askQuotes(x) {
    if (!x.ask || x.ask.kind !== 'person_vs_person') return '';
    const q = S.askQuote[x.card];
    if (q === undefined) {
      S.askQuote[x.card] = null;
      soft(get('/api/v1/threads/' + encodeURIComponent(x.ask.tid))).then((t) => {
        const P = (t && (t.posts || t.messages)) || [], by = (id) => { const m = P.find((y) => (y.message_id || y.id) === id); return m && typeof m.body === 'string' ? m.body : ''; };
        S.askQuote[x.card] = { first: by(x.ask.first), second: by(x.ask.second) }; render();
      }).catch(() => { S.askQuote[x.card] = { first: '', second: '' }; });
    }
    const W = ASK_WORD[askLang()], cut = (t) => (t.length > 140 ? t.slice(0, 140) + '…' : t);
    return q && (q.first || q.second) ? `<div class="askq"><div><b>${esc(W.firstL)}</b>${esc(cut(q.first || '(讀不到,打開串看)'))}</div><div><b>${esc(W.secondL)}</b>${esc(cut(q.second || '(讀不到,打開串看)'))}</div></div>` : '';
  }
  async function askAnswer(card, answer) {
    const C = S.card; if (!C || !card || C.askBusy) return;
    C.askBusy = true; C.actMsg = '送出中…'; C.actBad = false; renderCard();
    try {
      await act('decision_answer', { card_id: card, answer });
      C.askDone = answer; C.actMsg = '已送回那一串;作者席會照這個做'; S.dismissed.add(card); // 519d0:按完卡收掉(aibd 下一輪也不再給)
    } catch (e) {
      const c = e && (e.code || (e.body && e.body.code)), r = e && (e.reason || (e.body && e.body.reason));
      if (c === 'already_answered' || r === 'already_answered') { C.askDone = '?'; C.actMsg = '這一題已經有人答過了'; }
      else if (c === 'no_decision' || r === 'no_decision') { C.actBad = true; C.actMsg = '這一題已經不在等你決定'; }
      else { C.actBad = true; C.actMsg = actRefused(e) || '沒送出:' + errText(e); }
    }
    C.askBusy = false; renderCard();
  }
  async function ideaAnswer(card, answer) {
    const C = S.card; if (!C || !card) return;
    C.actMsg = '送出中…'; C.actBad = false; renderCard();
    try {
      const r = await act('idea_answer', { card_id: card, answer });
      // 7ebca 68b1f4db8:指紋沒接 ⇒ 200 verified:false,卡留著,只記「頁面按過、未證實」
      if (r && r.verified === false) { C.actMsg = '頁面按過、未證實(' + (answer === 'agree' ? '同意' : '不同意') + ');要按指紋確認是你本人才算,這版 AIB 還沒接上指紋,卡留著'; }
      else { C.ideaDone = answer; C.actMsg = ''; }
    } catch (e) {
      const c = e && e.body && e.body.code;
      if (c === 'idea_answered') { C.ideaDone = '?'; C.actMsg = '這一條已經有人答過了'; }
      else { C.actBad = true; C.actMsg = actRefused(e) || (c === 'idea_not_waiting' ? '這一條已經不在等人決定' : '沒送出:' + errText(e)); }
    }
    renderCard();
  }
  async function reviewSeen() {
    const C = S.card; if (!C || C.seenBusy) return;
    const seen = C.seen || (C.seen = {}), pick = C.pick || {};
    const ids = Object.keys(pick).filter((k) => pick[k] && !seen[k]).slice(0, 200);
    if (!ids.length) return;
    C.seenBusy = true; C.actMsg = '送出中…'; C.actBad = false; renderCard();
    try {
      const r = await act('review_seen', { line_ids: ids });
      const unv = r && r.verified === false;
      for (const k of ids) { seen[k] = unv ? 'unv' : true; delete pick[k]; }
      const n = (r && Number(r.count)) || ids.length, h = r && typeof r.hash === 'string' ? r.hash : '';
      C.actMsg = (unv ? '頁面按過、未證實:' : '看過:') + n + ' 條' + (h ? '(清單雜湊 ' + h + ')' : '') + (unv ? ';要按指紋確認是你本人才算,這版 AIB 還沒接上指紋' : '');
    } catch (e) {
      const c = e && e.body && e.body.code;
      if (c === 'line_not_waiting') { C.actBad = true; C.actMsg = '勾的裡面有已經不在清單上的,重開這張卡再勾'; }
      else { C.actBad = true; C.actMsg = actRefused(e) || '沒送出:' + errText(e); }
    }
    C.seenBusy = false; renderCard();
  }
  async function pinCard(node) {
    const C = S.card, card = C && C.ev && C.ev.card; if (!card) return;
    C.pinOpen = false; C.pinMsg = '送出中…'; C.pinBad = false; renderCard();
    try {
      const r = await act('slot_pin', { card_id: card, node_id: node });
      C.pin = (r && r.node_id) || node || ''; C.pinDemo = false; C.pinMsg = node ? '已掛上' : '已拿掉';
      const x = (S.aslots || []).find((y) => y.card_id === card); if (x) x.pin = C.pin || undefined;
    } catch (e) {
      const c = e && e.code;
      if (e && e.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request')) { C.pin = node || ''; C.pinDemo = true; C.pinMsg = '這版 AIB 還不能掛,先標在頁面上'; }
      else { C.pinBad = true; C.pinMsg = c === 'node_not_found' ? '找不到這個項目' : c === 'slot_built_in_page_only' ? '用戶資料夾有自己的頁時不能掛' : c === 'carried_pair_read_and_safe_only' ? '這個配對只能看,不能掛' : '沒掛上:' + errText(e); }
    }
    renderCard();
  }
  async function openCard(id, ev, again) {
    if (!id && !ev) return;
    const base = id && S.tree && S.tree.get(id);
    // ↻ 重讀同一張:留著同一個狀態(不重播進場、不閃讀取中、鈕照轉),讀回來的蓋上去
    if (again && S.card && S.card.id === id) { S.card.kidUse = null; S.card.kidLoading = false; }
    else S.card = { id, ev: ev || null, only: !id, pin: (ev && ev.pin) || '', n: base ? Object.assign({}, base) : { id, title: id }, tab: 'all', use: null, loaded: !id };
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
    loadCorr(id).then((c) => { if (S.card && S.card.id === id) { S.card.corr = c; renderCard(); } }).catch(() => {});
    const u = await soft(get('/api/v1/nodes/' + encodeURIComponent(id) + '/usage')).catch(() => null);
    if (S.card && S.card.id === id) { S.card.use = u; S.card.loaded = true; S.card.at = new Date().toISOString(); const own = useRows(u).reduce((a, r) => a + spentOf(r), 0), st = u && u.subtree; if (st && Number(st.spent) > own && !useRows(u).length) S.card.kidOpen = true; renderCard(); if (!st || (Number(st.spent) > own && !Array.isArray(st.items))) loadKidCost(); }
  }
  let dlg = null, dlgHtml = '';
  // 頁面寫卡(7b81e IFACE-web-card-write r0,N-web-oneline 第 2 項)。1a93b d756e6d3b3:不另開 card_*,送 7b81e 的 node_set
  // {node_id, if_rev, fact, station?}(進站條件照 aib-plan entry_problems)+ idempotency_key;記一筆 = 只帶 fact。頁面能換的站只有這四個,
  // 審過 / 實查要收據與審查、定版要指紋 ⇒ 灰掉。aibd 沒有這兩種動作時照實說「沒寫進去」,標示意
  const PAGE_ST = ['未開始', '已定未做', '開發中', '作廢'];
  function cardWriteHtml(n) {
    const W = S.card.w || {};
    const opts = ['', ...STATIONS, '作廢'].map((x) => { const ok = !x || PAGE_ST.includes(x); return `<option value="${esc(x)}" ${ok ? '' : 'disabled'} ${W.st === x ? 'selected' : ''}>${x ? esc(x) + (ok ? '' : '(要收據 / 審查,頁面不能換)') : '不換站'}</option>`; }).join('');
    const res = W.res ? `<div class="cwr ${W.res.ok ? 'ok' : 'bad'}">${esc(W.res.text)}${W.res.demo ? '<span class="pvdemo" title="AIB 還沒有 node_set">示意</span>' : ''}</div>` : '';
    return `<div class="dsec"><span>在這裡記一筆</span><span class="na">寫的人記成「頁面」</span></div><div class="cw"><textarea class="inp cwt" rows="2" maxlength="2000" placeholder="記一件事實(例:Andy 看過,要改 X)" data-cwt="1">${esc(W.text || '')}</textarea><div class="cwb"><select class="cws" data-cws="1" aria-label="換站">${opts}</select><button type="button" class="k g" data-cwgo="1" ${W.busy ? 'disabled' : ''}>${W.st ? '換站並記下' : '記下'}</button></div>${res}</div>`;
  }
  // 0eed7 REVIEW-integ2-ui L1(N-webonly-c5):aibd 認不得 node_set ⇒ 正式頁不畫這一區;預覽才畫(試按)。version.acts 有 node_set ⇒ 正式頁也畫
  // 按過一次回「沒有這種動作」就收起來(結果那一句留到關視窗);寫成功過一次之後正式頁也畫
  const cardWriteShown = () => (S.cardWriteOk || inPreview() || hasAct('node_set') === true) && (!S.cardWriteOff || !!(S.card && ((S.card.w && S.card.w.res) || (S.card.add && S.card.add.res))));
  // 開卡(519d0 22:25 第 1 點):送 7b81e 的 node_add {parent_id, title, owner?, why?} + idempotency_key(1a93b d756e6d3b3:不另開 card_add);認不得就收起
  function cardAddHtml(n) {
    const A = S.card.add || {};
    const res = A.res ? `<div class="cwr ${A.res.ok ? 'ok' : 'bad'}">${esc(A.res.text)}${A.res.demo ? '<span class="pvdemo" title="AIB 還沒有 node_add">示意</span>' : ''}</div>` : '';
    if (!A.open) return `<div class="cwb"><button type="button" class="k" data-cardadd="1" title="在「${esc(n.title || S.card.id)}」底下開一張新卡">在這底下開卡</button></div>${res}`;
    return `<div class="dsec"><span>開卡</span><span class="na">放在 ${esc(S.card.id)} 底下</span></div><div class="cw" data-cardaddform><input class="inp" data-caf="title" maxlength="120" placeholder="標題(必填)" value="${esc(A.title || '')}"><div class="cwb"><input class="inp" data-caf="seat" maxlength="12" placeholder="負責席(可空)" value="${esc(A.seat || '')}"><input class="inp" data-caf="why" maxlength="400" placeholder="說明(可空)" value="${esc(A.why || '')}"><button type="button" class="k g" data-cardaddgo="1" ${A.busy ? 'disabled' : ''}>開</button><button type="button" class="k" data-cardaddno="1">不要</button></div>${res}</div>`;
  }
  async function cardAdd() {
    const c = S.card; if (!c || !c.id) return; const A = (c.add = c.add || {});
    for (const e of document.querySelectorAll('[data-caf]')) A[e.dataset.caf] = e.value.trim();
    if (!A.title) { A.res = { ok: false, text: '先寫標題' }; return renderCard(); }
    A.busy = true; renderCard();
    try {
      // 一次按鈕一把鑰匙:連點或網路重送,aibd 回第一次的答案(replayed),不會開兩張
      const key = A.key || (A.key = idem());
      const r = await act('node_add', Object.assign({ parent_id: c.id, title: A.title, idempotency_key: key }, A.seat ? { owner: A.seat } : {}, A.why ? { why: A.why } : {}));
      c.add = { open: false, res: { ok: true, text: '開了' + (r && r.node_id ? ' ' + r.node_id : '') + (r && r.station ? '(' + r.station + ')' : '') } }; S.cardWriteOk = true; openCard(c.id, null, true);
    } catch (er) {
      const code = er && er.code;
      if (er && er.status) A.key = ''; // 網路斷掉不清:再按用同一把鑰匙,aibd 已寫過就回第一次的答案
      if (er && (code === 'unknown_act_type' || code === 'unknown_act' || code === 'unknown_type')) { A.res = { ok: false, text: 'AIB 還沒有開卡的動作,這張沒有開;這一區先收起來', demo: true }; S.cardWriteOff = true; }
      else if (code === 'node_not_found') A.res = { ok: false, text: '上一層卡在 AIB 裡找不到,沒有開' };
      else if (code === 'node_exists') A.res = { ok: false, text: '這個卡號已經有了,沒有開' };
      else if (code === 'idempotency_conflict') A.res = { ok: false, text: '上一次按的還在處理或內容不同,沒有開;重讀後再按' };
      else if (code === 'node_built_in_page_only') A.res = { ok: false, text: '只有內建頁能開卡' };
      else if (code === 'seat_id') A.res = { ok: false, text: '負責席只能寫席碼(英數、_、-),沒有開' };
      else if (code === 'entry_refused') A.res = { ok: false, text: (er.body && er.body.why) || er.message };
      else if (code === 'built_in_page_only') A.res = { ok: false, text: '只有內建頁能開卡' };
      else if (code === 'tree_not_in_aibd') A.res = { ok: false, text: '進度樹還在檔案裡,頁面寫不進去(開關由 519d0 / f1fae 定)' };
      else A.res = { ok: false, text: errText(er) };
    }
    A.busy = false; renderCard();
  }
  async function cardWrite() {
    const c = S.card; if (!c || !c.id) return; const W = (c.w = c.w || {});
    const text = String(W.text || '').trim();
    if (!text) { W.res = { ok: false, text: W.st ? '換站也要寫一句事實(做了什麼、分支或頭碼),aib-plan 的進站條件看這一句' : '先寫一句' }; return renderCard(); }
    if (W.st && !PAGE_ST.includes(W.st)) { W.res = { ok: false, text: W.st + ' 要同頭收據與審查,頁面不能換' }; return renderCard(); }
    W.busy = true; renderCard();
    try {
      const rev = Number(c.n && c.n.rev) | 0;
      if (rev < 1) { W.res = { ok: false, text: '這張卡還沒從 AIB 讀到版本,先按 ↻ 重讀再寫' }; W.busy = false; return renderCard(); }
      const from = c.n && (c.n.status || c.n.station);
      const key = W.key || (W.key = idem());
      const r = await act('node_set', Object.assign({ node_id: c.id, if_rev: rev, fact: text, idempotency_key: key }, W.st ? { station: W.st } : {}));
      W.res = { ok: true, text: (r && r.moved && from && r.station && from !== r.station ? from + ' → ' + r.station + ',' : '') + '記下了' + (r && r.replayed ? '(重送,沒有多寫)' : '') };
      W.key = '';
      W.text = ''; W.st = ''; S.cardWriteOk = true; S.cardWriteOff = false; openCard(c.id, null, true);
    } catch (er) {
      const code = er && er.code;
      if (er && er.status) W.key = ''; // 同上
      if (er && (code === 'unknown_act_type' || code === 'unknown_act' || code === 'unknown_type')) { W.res = { ok: false, text: 'AIB 還沒有寫卡的動作,這筆沒有寫進去;這一區先收起來', demo: true }; S.cardWriteOff = true; }
      else if (code === 'node_rev_changed') { W.res = { ok: false, text: '你讀的之後有人改過這張卡,這筆沒有寫;重讀了,看過再按一次' }; openCard(c.id, null, true); }
      else if (code === 'node_not_found') W.res = { ok: false, text: '這張卡在 AIB 裡找不到,沒有寫' };
      else if (code === 'idempotency_conflict') W.res = { ok: false, text: '上一次按的還在處理或內容不同,這筆沒有寫;重讀後再按' };
      else if (code === 'node_built_in_page_only') W.res = { ok: false, text: '只有內建頁能寫卡' };
      else if (code === 'entry_refused') W.res = { ok: false, text: (er.body && er.body.why) || er.message };
      else if (code === 'built_in_page_only') W.res = { ok: false, text: '只有內建頁能寫卡' };
      else if (code === 'tree_not_in_aibd') W.res = { ok: false, text: '進度樹還在檔案裡,頁面寫不進去(開關由 519d0 / f1fae 定)' };
      else W.res = { ok: false, text: errText(er) };
    }
    W.busy = false; renderCard();
  }
  function renderCard() {
    if (!S.card) { if (dlg && dlg.open) dlg.close(); return; }
    if (!dlg) {
      dlg = document.createElement('dialog'); dlg.className = 'cdlg'; document.body.appendChild(dlg);
      // 記一筆的字與換站:存在 S.card.w,重畫不會清掉
      dlg.addEventListener('input', (e) => { if (e.target.dataset && e.target.dataset.cwt && S.card) { (S.card.w = S.card.w || {}).text = e.target.value; } });
      dlg.addEventListener('change', (e) => { if (e.target.dataset && e.target.dataset.cws && S.card) { (S.card.w = S.card.w || {}).st = e.target.value; const g = dlg.querySelector('[data-cwgo]'); if (g) g.textContent = e.target.value ? '換站並記下' : '記下'; } });
      dlg.addEventListener('click', (e) => {
        const pe = e.target.closest('[data-evopen]'); if (pe) return openEv(pe.dataset.evopen);
        const cq = e.target.closest('[data-corr]'); if (cq && S.card) { S.card.corrOpen = S.card.corrOpen === cq.dataset.corr ? '' : cq.dataset.corr; return renderCard(); }
        if (e.target === dlg || e.target.closest('[data-cclose]')) { S.card = null; dlg.close(); return; }
        const t = e.target.closest('[data-ctab]'); if (t) { S.card.tab = t.dataset.ctab; S.card.recAll = false; return renderCard(); }
        if (e.target.closest('[data-recall]')) { S.card.recAll = !S.card.recAll; return renderCard(); }
        const rf = e.target.closest('[data-crf]');
        if (rf && S.card) { const c = S.card; c.spinUntil = Date.now() + 700; setTimeout(() => { if (S.card === c) renderCard(); }, 720); return openCard(S.card.id, S.card.ev, true); }
        if (e.target.closest('[data-cwgo]') && S.card) { e.preventDefault(); return cardWrite(); }
        if (e.target.closest('[data-cardadd]') && S.card) { e.preventDefault(); S.card.add = { open: true }; return renderCard(); }
        if (e.target.closest('[data-cardaddno]') && S.card) { e.preventDefault(); S.card.add = null; return renderCard(); }
        if (e.target.closest('[data-cardaddgo]') && S.card) { e.preventDefault(); return cardAdd(); }
        const co = e.target.closest('[data-chartown]'); if (co && S.card) { e.preventDefault(); S.card.chartOwn = !S.card.chartOwn; S.card.drawn = false; return renderCard(); }
        const kd = e.target.closest('[data-kidcost]'); if (kd && S.card) { e.preventDefault(); S.card.kidOpen = !S.card.kidOpen; if (S.card.kidOpen) loadKidCost(); return renderCard(); }
        const ow = e.target.closest('[data-owncost]'); if (ow && S.card) { e.preventDefault(); S.card.ownShut = !S.card.ownShut; return renderCard(); }
        const upc = e.target.closest('[data-pvup]'); if (upc) return upOpen(upc.dataset.pvup);
        const sl = e.target.closest('.fd .lg[data-seat]'); if (sl) return seatInfo(sl.dataset.seat); // Andy 00:2x:之後點頭像看這一席當下的資訊(先留介面)
        if (e.target.closest('[data-pinopen]') && S.card) { S.card.pinOpen = !S.card.pinOpen; renderCard(); const i = dlg.querySelector('[data-pinq]'); if (i) i.focus(); return; }
        const ia = e.target.closest('[data-idea]'); if (ia && S.card) return ideaAnswer(ia.dataset.icard, ia.dataset.idea);
        const ak = e.target.closest('[data-ask]'); if (ak && S.card) return askAnswer(ak.dataset.acard, ak.dataset.ask);
        const ao = e.target.closest('[data-askopen]'); if (ao) { S.card = null; dlg.close(); return openAt(ao.dataset.askopen, ao.dataset.amid, ''); }
        const rp = e.target.closest('[data-rvpick]'); if (rp && S.card) { (S.card.pick = S.card.pick || {})[rp.dataset.rvpick] = rp.checked; return renderCard(); }
        if (e.target.closest('[data-seenpick]') && S.card) return reviewSeen();
        const pt = e.target.closest('[data-pinto],[data-pinoff]'); if (pt && S.card) { return pinCard(pt.dataset.pinoff ? null : pt.dataset.pinto); }
        const c = e.target.closest('[data-card]'); if (c) return openCard(c.dataset.card);
        // 這則事件的作答 / 叫交接 / 重開 / 看這一席 / 晚點:跟主畫面同一條路;會換畫面的(看這一席、晚點、作答)先關視窗
        // 519d0 06:45 第 2 點:額度卡「換模型」在這個視窗裡就地填、就地換;換好了關視窗
        const mw = e.target.closest('.evact [data-msw], .evact [data-mswgo], .evact [data-mswx]');
        if (mw) { const r = onMainClick(e); renderCard(); const i = dlg.querySelector('[data-mswin]'); if (i && mw.matches('[data-msw]')) i.focus(); Promise.resolve(r).then(() => { if (!S.card) return; if (!S.mswap && mw.matches('[data-mswgo]')) { S.card = null; dlg.close(); } else renderCard(); }); return; }
        // 重新登入在這個視窗裡就地做(按指紋、開登入頁、貼碼),不關視窗
        const rl = e.target.closest('.evact [data-relogin], .evact [data-relogsend], .evact [data-relogcopy]');
        if (rl) { const r = onMainClick(e); renderCard(); Promise.resolve(r).then(() => { if (S.card) renderCard(); }); return; }
        const ac = e.target.closest('.evact [data-pick], .evact [data-answer], .evact [data-handoff], .evact [data-restart], .evact [data-go], .evact [data-later], .evact [data-resume]');
        if (ac) { if (ac.matches('[data-go], [data-later], [data-pick], [data-answer], [data-resume]')) { S.card = null; dlg.close(); } return onMainClick(e); }
      });
      dlg.addEventListener('input', (e) => {
        if (e.target.dataset && e.target.dataset.ansfor) S.ansDraft[e.target.dataset.ansfor] = e.target.value;
        if (e.target.dataset && e.target.dataset.mswin && S.mswap) S.mswap.v = e.target.value;
        if (e.target.dataset && e.target.dataset.relogcode && S.relog) S.relog.v = e.target.value;
        if (e.target.dataset && e.target.dataset.pinq && S.card) { S.card.pinQ = e.target.value; const l = dlg.querySelector('.pinl'); if (l) l.innerHTML = pinList(S.card.pinQ); } // 只換清單,輸入框不重畫
      });
      dlg.addEventListener('keydown', (e) => {
        if (e.target.dataset && e.target.dataset.ansfor && e.key === 'Enter' && !imeBusy(e)) { e.preventDefault(); const b = e.target.closest('.ans') && e.target.closest('.ans').querySelector('[data-answer]'); if (b) b.click(); }
      });
      // close 事件是非同步的:關了又馬上開別張時,不要把新開的那張清掉
      dlg.addEventListener('close', () => { if (dlg.open) return; S.card = null; dlgHtml = ''; });
    }
    const ev = S.card.ev;
    const evSec = ev ? `<div class="dsec">這則事件</div><div class="evd"><div class="evh"><span class="ek ${TAG_K[ev.tag] || 'k3'}">${esc(ev.tag || LV_TAG[ev.lv] || '')}</span><b>${esc(ev.text)}</b></div><div class="fg">${fld('什麼時候', ev.t ? when(ev.t) + ' · ' + DISP_TZL + ' ' + evTime(ev.t) : '')}${ev.seat ? fldH('哪一席', logo(seatById(ev.seat) || { seat_id: ev.seat, name: ev.seat }, 'sm') + ' ' + esc(ev.who || '')) : ev.who ? fld('誰', ev.who) : ''}${ev.kind === 'andy.review_list' ? '' : ev.kind === 'idea.consent' ? fld('做法', ev.rec) + fld('好處', ev.why) + fld('代價', ev.st) : fld('我們的建議', ev.rec) + (ev.st ? fldH('狀態', stateHtml(ev.st)) : fld('狀態', ''))}${fld('出處', ev.src)}${ev.ref ? fld('卡號', ev.ref) : ''}</div><div class="evact">${evBody(ev, true)}</div></div>` : '';
    if (S.card.only) {
      const html = `<div class="dw"><div class="dh u-todo"><div class="dh1"><span class="pill u-todo">${esc(ev ? ev.tag || LV_TAG[ev.lv] || '事件' : '事件')}</span><b class="evst" title="${esc(ev ? ev.text : '')}">${esc(shortTitle(ev ? ev.text : ''))}</b><button type="button" class="x" data-cclose="1" aria-label="關閉" autofocus>✕</button></div>
          <div class="evpin"><span class="na">${S.card.pin ? (S.card.pinDemo ? '已選:' : '已掛到:') : '未對應專案項目'}</span>${S.card.pin ? `<a data-card="${esc(S.card.pin)}">${esc((S.tree && S.tree.get(S.card.pin) || {}).title || S.card.pin)}</a>${S.card.pinDemo ? '<span class="pvdemo" title="這版 AIB 還不能掛,只標在頁面上">示意</span>' : ''}` : ''}<button type="button" class="k" data-pinopen="1">${S.card.pin ? '換一項' : '掛到項目'}</button>${S.card.pin && !S.card.pinDemo ? '<button type="button" class="k" data-pinoff="1">拿掉</button>' : ''}${S.card.pinMsg ? `<span class="pinmsg ${S.card.pinBad ? 'bad' : ''}">${esc(S.card.pinMsg)}</span>` : ''}</div>
          ${S.card.pinOpen ? `<div class="pinbox"><input class="apq" data-pinq="1" placeholder="找專案項目(標題或卡號)" value="${esc(S.card.pinQ || '')}" maxlength="60"><div class="pinl">${pinList(S.card.pinQ)}</div></div>` : ''}</div>
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
    const tabs = ['all'].concat(Object.keys(REC)).map((k) => `<button type="button" data-ctab="${k}" class="${S.card.tab === k ? 'on' : ''}" ${k !== 'all' && !cnt[k] ? 'disabled' : ''}>${k === 'all' ? '全部' : (typeof fpSay === 'function' ? fpSay : (t) => t)(REC[k])} <i>${k === 'all' ? recs.length : cnt[k] || 0}</i></button>`).join('');
    const shownAll = recs.filter((r) => S.card.tab === 'all' || (r.ks || [r.k]).includes(S.card.tab));
    const REC_MAX = 6, shown = S.card.recAll ? shownAll : shownAll.slice(0, REC_MAX);
    const recMore = shownAll.length > REC_MAX ? `<button type="button" class="more" data-recall="1">${S.card.recAll ? '收起 ▴' : '再看 ' + (shownAll.length - REC_MAX) + ' 筆 ▾'}</button>` : '';
    const stepMs = shown.length ? Math.min(520, Math.round(7000 / shown.length)) : 0;
    const tl = shown.length ? shown.map((r, i) => `<div class="te ${i === 0 ? 'new' : ''}" data-sty="--d:${(shown.length - 1 - i) * stepMs}ms;"><time>${esc(r.at || '沒有寫時間')}${(r.ks || [r.k]).filter((k) => k && k !== 'dev').map((k) => `<b class="kt kt-${k}">${esc((typeof fpSay === 'function' ? fpSay : (t) => t)(REC[k]))}</b>`).join('')}</time><p>${r.by ? seatLogos(r.by) + ' ' : ''}${esc(r.t)}</p></div>`).join('') : '<div class="none">還沒有紀錄</div>';
    const kids = (Array.isArray(n.children) && n.children.length ? n.children : null) || (S.kids && S.kids.get(n.id)) || []; // 空陣列的 children 不蓋過樹裡的子項
    const chh = kids.length ? '<div class="ch">' + kids.map((c) => { const ci = stationIdx(c.status || c.station); return `<div class="cr" data-card="${esc(c.id)}" tabindex="0" role="button"><span class="stn ${ci != null ? 's' + (ci + 1) : ''}">${ci != null ? ci + 1 + ' ' + esc(STATIONS[ci]) : esc(c.status || c.station || '—')}</span><b>${esc(c.title || c.id)}</b><em>${c.owner ? seatLogos(c.owner) : ''}</em></div>`; }).join('') + '</div>' : '<div class="none">沒有再往下分的項目</div>';
    const html = `<div class="dw"><div class="dh u-${u}"><div class="dh1"><span class="pill u-${n.status === '實查不通過' || n.status === '卡住' ? 'bad' : vf ? (si === 6 ? 'ok' : si >= 2 ? 'run' : 'todo') : u}">${esc(n.status || '沒有狀態')}</span>${vf ? '<span class="pill u-bad">驗不通過</span>' : ''}<span class="kid">${esc(n.id)}</span><span>${si != null ? '第 ' + (si + 1) + ' 站 / 7' : '不在流程上'}</span><span class="uphd">${upTag(n.id)}${upBtn(n.id)}</span><button type="button" class="x" data-cclose="1" aria-label="關閉" autofocus>✕</button></div>
      <div class="dt">${esc(n.title || n.id)}</div><div class="crumb">${crumb.length ? crumb.map((p) => `<a data-card="${esc(p.id)}">${esc(String(p.title || p.id).slice(0, 16))}</a>`).join(' › ') : '總綱'}</div></div>
      <div class="db ${S.card.still ? 'still' : ''}" data-sty="--sp:${si != null && si > 0 ? si / 6 : 0};--st:${Math.max(si || 0, 0) * 480 + 1200}ms;--sc:var(--${u === 'todo' ? 'mute' : u === 'ok' ? 'ok' : u === 'bad' ? 'bad' : 'you'});--t:${Math.max(1200, shown.length * stepMs + 1200)}ms">${evSec}${ev && ev.kind === 'seat.asked' ? '' : `<div class="dsec">真人需求</div><div class="need">${n.need ? esc(txt(n.need)) : '<span class="na">—</span>'}</div>`}
      <div class="dsec">走到哪一站</div><div class="step stgo">${step}</div>${pinnedSec(n.id)}${corrSec()}
      <div class="dsec">基本資料</div><div class="fg">${fldH('負責席', seatLogos([n.owner].concat(n.with || [])))}${fldH('審查人', seatLogos(n.reviewer))}${fld('最後更新', n.updated)}${fld('預計', n.eta)}${fld('誰同意的', ap ? [ap.by, ap.at].filter(Boolean).join(' ') : '')}${fld('同意的依據', ap ? ap.pointer || ap.pt || '' : '')}${fld('要先等', Array.isArray(wait) ? wait.join('、') : wait)}${fld('兩平台', pfx)}</div>
      <div class="dsec">為什麼做</div>${n.why ? `<div class="why">${esc(txt(n.why))}</div>` : '<div class="none">沒有寫</div>'}
      <div class="dsec"><span>紀錄(新的在上)</span><span>${n.records_total > recs.length ? recs.length + ' / ' + n.records_total : recs.length} 筆</span></div>${Object.keys(cnt).some((k) => k) ? `<div class="tlt">${tabs}</div>` : ''}<div class="tlx"><div class="tlw">${tl}</div>${recMore}</div>
      ${S.card.only || !cardWriteShown() ? '' : cardWriteHtml(n) + cardAddHtml(n)}
      <div class="dsec"><span>底下的項目</span><span>${kids.length ? (n.children_total > kids.length ? kids.length + ' / ' + n.children_total : kids.length) + ' 項' : ''}</span></div>${chh}
      <div class="dsec">用了多久、花了多少</div>${S.card.loaded ? costHtml(S.card.use, kids) : '<div class="none">讀取中…</div>'}
      </div><div class="df"><span>資料時間:${esc(S.card.at ? TZL + ' ' + hhmm(S.card.at) : '—')}${S.tree && S.tree.has(n.id) ? ' · 跟流程表同一份資料' : ''} <button type="button" class="rf ${S.card.spinUntil && Date.now() < S.card.spinUntil ? 'spin' : ''}" data-crf="1" title="重讀最新的資料">↻</button></span><span>按 Esc 或點外面關閉</span></div></div>`;
    if (html !== dlgHtml) {
      const sc = dlg.querySelector('.db'); const top = sc && dlg.dataset.id === n.id ? sc.scrollTop : 0; const fa = document.activeElement, cwF = fa && fa.dataset && fa.dataset.cwt ? { a: fa.selectionStart, b: fa.selectionEnd } : null; dlg.innerHTML = html; if (cwF) { const t = dlg.querySelector('[data-cwt]'); if (t) { t.focus(); t.setSelectionRange(cwF.a, cwF.b); } } dlgHtml = html; dlg.dataset.id = n.id; const nb = dlg.querySelector('.db'); if (nb) nb.scrollTop = top;
      requestAnimationFrame(() => tlFit(dlg));
      if (!S.card.still) { const c = S.card; setTimeout(() => { if (S.card === c) c.still = true; }, 50); }
      if (S.card.use && !S.card.drawn) { const c = S.card; setTimeout(() => { if (S.card === c) c.drawn = true; }, 50); }
    }
    if (!dlg.open) dlg.showModal();
  }

  // ---------- 加一席(第二波;b4297 act seat_open + 7b81e assign_add)----------
  // 品牌照 GET /api/v1/version 的 capabilities.providers;不能開的顯示但不能按(5d33a B5,aibd 也擋)。
  // 模式照該品牌的 modes(沒寫 = 只有 safe)。人格從現有席挑。角色:開好後對左樹選中的項目 assign_add;
  // 指派失敗席照留、標出來(B4 兩步)
  // 送出用英文六字(7b81e 11:07,與 a9136 thread members 同一組);畫面顯示中文
  // 用戶新建的角色(rol_*,b4297 d191578b7 起可指派):名字從 GET roles 來;還沒讀到就讀一次,讀到前顯示「自訂角色」
  function roleLabel(uid) {
    if (!uid) return '';
    const r = S.reg && Array.isArray(S.reg.roles) && S.reg.roles.find((x) => x.uid === uid);
    if (r) return r.name || uid;
    if (!S.reg || !S.reg.rolesTried) { S.reg = S.reg || {}; S.reg.rolesTried = true; loadRegistry().then(render).catch(() => {}); }
    return /^rol_/.test(uid) ? '自訂角色' : uid;
  }
  const ROLE_EN = { 作者: 'author', 審查: 'reviewer', 驗收: 'verifier', 換上: 'deployer', 相依: 'depends', 旁聽: 'observer' };
  const ROLE_ZH = Object.fromEntries(Object.entries(ROLE_EN).map(([z, e]) => [e, z]));
  // §22(Andy 22:2x「這個不要支援 openrouterjev 並且這個畫面的設計太差了,重新依風格設計」):品牌只列 2.0 支援的四個,
  // 舊設定裡的自訂名稱(openrouterjev、typesafe、*bot…)一律不畫;capabilities 有回就照它標能不能開,沒回的變淡
  const ADD_BRANDS = [['claude', 'Claude', 'bc'], ['codex', 'Codex', 'bx'], ['agy', 'Antigravity', 'bg'], ['grok', 'Grok', 'bk'], ['copilot', 'Copilot', 'bp']];
  const BRAND_ALIAS = { claude: 'claude', codex: 'codex', agy: 'agy', antigravity: 'agy', gemini: 'agy', grok: 'grok', copilot: 'copilot' };
  // 0eed7 VERIFY-codex 2d1c3b98(00:5x):codex 安全席在串裡說話、回報已真跑過;叫你決定(ask_person)還沒證 ⇒ 只開安全席、說明寫清楚;C5 證了再拿掉
  //   { block: 原因 } = 整個灰掉;{ modes, note } = 能開但只給這些模式,品牌鈕下寫 note
  const HOLD_BRANDS = { codex: { modes: ['safe'], note: 'Codex 席能在對話裡說話、叫別席、開決定卡;還不能寫檔' } };
  function providers() {
    const L = (S.version && S.version.capabilities && S.version.capabilities.providers) || [];
    const got = {}; for (const p of L) { const k = p && BRAND_ALIAS[String(p.id || '').toLowerCase()]; if (k && !got[k]) got[k] = p; }
    if (!L.length) got.claude = { id: 'claude', open: true, modes: ['safe'] }; // 舊 aibd 沒 capabilities:只知道 claude 能開
    return ADD_BRANDS.filter(([k]) => got[k]).map(([k, name, b]) => {
      const p = got[k];
      // b4297 1c335b166:不能開時 reason = not_built(aibd 還不驅動)/ not_set_up(這台政策沒列)/ not_installed(列了但程式不在)
      const R = { not_built: '這版還不能開', not_set_up: '這台沒設定', not_installed: '這台沒裝', not_logged_in: '這台的登入沒有或已過期' };
      // Andy 11-20 00:2x 親打「先把 codex 選項灰掉」:codex 席叫不出 aibd 工具、話進不了串(0eed7 VERIFY-realentry ea0885a7 H1)⇒ aibd 修好前頁面不讓開
      const held = HOLD_BRANDS[k] || null;
      const npm = !(p && p.open) && typeof spNpm === 'function' && typeof S !== 'undefined' ? spNpm(k) : ''; // W-10 c2f0b 第 5 點
      const why = (held && held.block) || npm || (!p ? '這版還不能開' : p.open ? (held && held.note) || '' : R[p.reason] || '這台還不能開');
      const modes = ((p && p.modes) || ['safe']).filter((m) => !held || !held.modes || held.modes.includes(m));
      const ms = p && Array.isArray(p.models) ? p.models.filter((m) => m && typeof m.id === 'string' && m.id && MODEL_RE.test(m.id)).slice(0, 40).map((m) => ({ id: m.id, label: String(m.label || m.id).slice(0, 80), dflt: m.default === true, used: m.used === true })) : null;
      return { id: p ? p.id : k, key: k, name, b, nl: !!(p && !p.open && p.reason === 'not_logged_in'), open: !(held && held.block) && !!(p && p.open), modes: modes.length ? modes : ['safe'], why, models: ms, msrc: p && typeof p.models_source === 'string' ? p.models_source : '', mat: p && typeof p.models_fetched_at === 'string' ? p.models_fetched_at : '' };
    });
  }
  // ebab8 12:14 / 08de5:沒有人格卡檔的機器,aibd 回一張寫死的內建人格(uid aib000、builtin: true);頁面照語言顯示名字、標「內建」、只有它一張時自動選好
  const PERSONA_BUILTIN = { 'zh-TW': ['AIB 夥伴', '內建'], en: ['AIB Partner', 'Built-in'], ja: ['AIB パートナー', '内蔵'], ko: ['AIB 파트너', '기본 제공'] };
  const pbW = () => PERSONA_BUILTIN[typeof askLang === 'function' ? askLang() : 'zh-TW'] || PERSONA_BUILTIN['zh-TW'];
  const personaBuiltin = (u) => !!(S.personaBuiltin && S.personaBuiltin.includes(u));
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
  function nodeRows(A) {
    const k = String(A.nq || '').trim().toLowerCase();
    const L = nodeOpts().filter(([id, t]) => !k || t.toLowerCase().includes(k) || id.toLowerCase().includes(k)).slice(0, 8);
    return L.length ? L.map(([id, t]) => `<button type="button" class="apr ${A.node === id ? 'on' : ''}" data-addnu="${esc(id)}" title="${esc(t)}"><b>${esc(t)}</b><i>${esc(id)}</i></button>`).join('') : '<div class="apn">找不到</div>';
  }
  function personaList(q) {
    const PS = personas(); const k = String(q || '').trim().toLowerCase();
    return (k ? PS.filter(([u, nm]) => nm.toLowerCase().includes(k) || u.toLowerCase().includes(k)) : PS).slice(0, 8);
  }
  // 一次多 AI(示意 editor('mates')):搜尋 + 勾選搭檔人格;沒搜尋時先列已勾的 + 名字像糾察 / 驗收 / 審查 / 寫手的
  function mateRows(A) {
    const M = A.mates || [], q = String(A.mq || '').trim().toLowerCase(), PS = personas().filter(([u]) => u !== A.persona);
    const L = q ? PS.filter(([u, n]) => String(n).toLowerCase().includes(q) || u.includes(q)) : PS.filter(([u]) => M.includes(u)).concat(PS.filter(([u, n]) => !M.includes(u) && /糾察|驗收|審查|寫手|CTA/.test(n)).slice(0, 10));
    return L.length ? L.map(([u, n]) => `<button type="button" class="apr ${M.includes(u) ? 'on' : ''}" data-apmate="${esc(u)}" aria-pressed="${M.includes(u)}"><b>${M.includes(u) ? '✓ ' : '+ '}${esc(n)}</b><i>${esc(u.slice(-6))}</i></button>`).join('') : '<div class="apn">找不到</div>';
  }
  // 一次多 AI(ebab8 03:39):主夥伴開好後,搭檔依序各送一次 seat_open(人格換成搭檔的,模式 / 倉照這一組);每位的結果逐行顯示,全部完成切到主夥伴
  const apPName = (u) => ((personas().find(([x]) => x === u) || [])[1]) || u;
  function apGrp(A) { return A.grp ? `<div class="apgrp" role="status" aria-live="polite">${A.grp.map((g, i) => `<div class="${g.run ? 'run' : g.ok ? 'ok' : 'bad'}">${g.run ? '…' : g.ok ? '✓' : '✗'} ${i ? '搭檔' : '主夥伴'} ${esc(g.nm)}${g.why ? ' · ' + esc(g.why) : ''}</div>`).join('')}</div>` : ''; }
  async function apOpenMates(A, pv, b0, main) {
    const MT = (A.mates || []).slice(0, 7); if (!MT.length) return '';
    A.grp = [{ nm: '@' + main.nm, ok: true, why: main.wait ? '排隊中' : '' }].concat(MT.map((u) => ({ nm: apPName(u), run: true })));
    const K = A.mkeys || (A.mkeys = {});
    for (let i = 0; i < MT.length; i++) {
      const g = A.grp[i + 1], u = MT[i]; render();
      const mb = { provider: pv.id, mode: b0.mode, persona_uid: u, idem_key: K[u] || (K[u] = idem()) };
      if (b0.repo) { mb.repo = b0.repo; if (b0.base) mb.base = b0.base; }
      try {
        const r = await fingerAct('seat_open', mb, () => { if (S.add === A) { A.msg = FP_SEE; render(); } }); // 08de5 A11
        const st = r.seat || {}, se = r.session || {}; g.nm = '@' + (st.display || st.name || st.code || String(st.seat_id || '').replace(/^seat_/, '').slice(0, 5) || g.nm); g.ok = true; g.why = se.state === 'pending' ? '排隊中' : '';
      } catch (e) { g.ok = false; g.why = mb.repo ? repoWhy(e) : addErr(e); K[u] = idem(); }
      g.run = false; A.msg = ''; render();
    }
    const bad = A.grp.filter((g) => g.ok === false);
    return ';搭檔 ' + (MT.length - bad.length) + '/' + MT.length + ' 位開了' + (bad.length ? ',沒開成:' + bad.map((g) => g.nm + '(' + g.why + ')').join('、') : '');
  }
  function personaRows(A) {
    const L = personaList(A.pq);
    return L.length ? L.map(([u, nm]) => rgWrap('persona', u, `<button type="button" class="apr ${A.persona === u ? 'on' : ''}" data-addpu="${esc(u)}"><b>${esc(nm)}</b><i>${esc(personaBuiltin(u) ? pbW()[1] : u.slice(-6))}</i></button>`, personaBuiltin(u))).join('') : '<div class="apn">找不到</div>'; // S4:旁邊 ✎ / 🗑
  }
  // ---------- §22b(Andy 04:5x):角色膠囊、第二大腦、三樣都能新增(b4297 IFACE-open-bind r0;界線 5d33a CHECK-ws-open-bind)----------
  // 內建六角色 uid 照舊英文字;aibd 有 GET roles 就照它(用戶新增的排後面),沒有退回內建表
  const ROLE_WHAT = { author: '寫程式、開分支', reviewer: '讀改動、給結論', verifier: '實際跑、驗收', deployer: '合併、換上預覽 / 正式', depends: '這項要用到它的成果', observer: '只看、不動手' };
  function roleList() {
    const R = S.reg && S.reg.roles;
    if (R && R.length) return R;
    return Object.entries(ROLE_EN).map(([zh, en]) => ({ uid: en, name: zh, what: ROLE_WHAT[en], builtin: true }));
  }
  function brainList(q) {
    const B = (S.reg && S.reg.brains) || [], k = String(q || '').trim().toLowerCase();
    return (k ? B.filter((b) => String(b.name || '').toLowerCase().includes(k) || String(b.uid || '').toLowerCase().includes(k)) : B).slice(0, 8);
  }
  function brainRows(A) {
    const L = brainList(A.bq);
    const none = `<button type="button" class="apr ${!A.brain ? 'on' : ''}" data-addbu=""><b>不綁</b><i></i></button>`;
    if (!(S.reg && S.reg.brainsLive)) {
      // 還沒有登記好的第二大腦:列 aibd 找到的資料夾,只能看;登記(brain_add)要在這台按指紋
      const k = String(A.bq || '').trim().toLowerCase(), CS = ((S.reg && S.reg.cands) || []).filter((c) => !k || c.name.toLowerCase().includes(k)).slice(0, 8);
      return none + (CS.length ? `<div class="apn">下面是這台找到的資料夾,還沒登記成第二大腦;這一版還不能從頁面登記成第二大腦;先不綁也能開席。</div>` + CS.map((c) => `<div class="apr off" title="還沒登記"><b>${esc(c.name)}</b><i>${c.notes != null ? esc(c.notes) + ' 篇' : ''}</i></div>`).join('') : `<div class="apn">${S.reg && (S.reg.brainRoot === 'unset' || S.reg.brainRoot === 'unusable') ? esc(brainRootWhy(S.reg.brainRoot)) + ';' : '這台還沒有登記好的第二大腦;'}先不綁也能開席。</div>`);
    }
    return none + (L.length ? L.map((b) => rgWrap('brain', b.uid, `<button type="button" class="apr ${A.brain === b.uid ? 'on' : ''}" data-addbu="${esc(b.uid)}" title="${esc(b.what || '')}"><b>${esc(b.name || b.uid)}</b><i>${b.notes != null ? esc(b.notes) + ' 篇 · ' : ''}${esc(String(b.uid).slice(-6))}</i></button>`)).join('') : (S.reg && S.reg.brains ? '<div class="apn">找不到</div>' : '<div class="apn">讀取中…</div>'));
  }
  // 開視窗時讀三張表;讀不到就標示意(頁面用到的後端能力先探測,沒有退回舊路)
  async function loadRegistry() {
    const A = S.add; S.reg = S.reg || {};
    const [r, b, cd] = await Promise.all([soft(get('/api/v1/roles')).catch(() => null), soft(get('/api/v1/brains?limit=50')).catch(() => null), soft(get('/api/v1/brains/candidates')).catch(() => null)]);
    // Andy 11-19 11:3x「第二大腦也沒帶出正確的」:沒登記過的不再顯示示意 demo01 / 02,改列 aibd 的候選資料夾(登記要本人指紋)
    S.reg.brainRoot = cd && cd.root;
    S.reg.cands = cd && Array.isArray(cd.candidates) && !cd.demo ? cd.candidates.filter((y) => y && y.id).map((y) => ({ id: String(y.id), name: String(y.label || y.id), notes: y.notes })) : [];
    if (r && Array.isArray(r.roles) && r.roles.length) { S.reg.roles = r.roles.filter((x) => x && x.uid).map((x) => ({ uid: String(x.uid), name: String(x.name || x.uid), what: String(x.what || ROLE_WHAT[x.uid] || ''), builtin: !!x.builtin })); S.reg.rolesLive = true; }
    else S.reg.rolesLive = false;
    if (b && Array.isArray(b.brains) && !b.demo) { S.reg.brains = b.brains.filter((x) => x && x.uid).map((x) => ({ uid: String(x.uid), name: String(x.name || x.uid), what: String(x.what || ''), notes: x.notes, sug: Array.isArray(x.suggested_for) ? x.suggested_for.map(String) : [] })); S.reg.brainsLive = true; }
    else { S.reg.brains = []; S.reg.brainsLive = false; }
    S.arch = Object.assign(S.arch || {}, { role: rgArchOf(r), brain: rgArchOf(b) }); // A1:已封存的角色 / 大腦
    if (A && S.add === A) { brainPreselect(A); render(); }
  }
  // 預設帶人格建議的那一個(suggested_for);用戶自己點過就不動
  // S1b 4:這個人格有沒有常用(建議)的第二大腦;a9136 §4a usual_brain 進來後改讀它
  function personaHasBrain(u) { return ((S.reg && S.reg.brains) || []).some((x) => x.sug && x.sug.includes(u)); }
  function brainPreselect(A) {
    if (A.brainTouched) return;
    const b = ((S.reg && S.reg.brains) || []).find((x) => x.sug && x.sug.includes(A.persona));
    A.brain = b ? b.uid : '';
  }
  // + 新增(三張表共用一個小表單):name + 一句說明;aibd 沒這個 act ⇒ 標示意、不假裝建好
  const REG_KIND = { role: ['角色', 'role_add', 20, 80], persona: ['人格', 'persona_add', 40, 500], brain: ['第二大腦', 'brain_add', 40, 500] };
  function brainRootWhy(root) {
    if (root === 'unset') return '還沒設定第二大腦位置。設定方法:在這台的 ~/.ez2-aib/vault-dir 寫上資料夾的完整路徑(或啟動 AIB 時帶 AIBD_BRAIN_ROOT),再重開這個視窗';
    if (root === 'unusable') return '第二大腦位置用不了,請檢查設定(要是完整路徑、是資料夾、讀得到裡面的筆記)';
    return '這個位置裡還沒有找到可用的資料夾';
  }
  // ebab8 12:12 / FINDING-win-seatopen:persona_add / brain_add 後端沒接本人確認(每個平台都回 owner_check_unavailable、不跳窗)
  // ⇒ 鈕留著(灰)、旁邊照實寫「這一版還不能從頁面新增」,不提指紋(ebab8 12:14:不藏,免得客戶以為沒這功能)。接上之後把這裡改 true 就回到原本的新增鈕
  const REG_ADD_OPEN = { persona: false, brain: false, role: true };
  const regNotYet = (kind) => '這一版還不能從頁面新增' + REG_KIND[kind][0];
  function regForm(kind) {
    const A = S.add, F = A.newf;
    if (!REG_ADD_OPEN[kind]) return `<button type="button" class="anew" disabled aria-disabled="true" title="${esc(regNotYet(kind))}">+ 新增${REG_KIND[kind][0]}</button><div class="apn">${esc(regNotYet(kind))}</div>`;
    if (!F || F.kind !== kind) return `<button type="button" class="anew" data-addnew="${kind}">+ 新增${REG_KIND[kind][0]}</button>`;
    const fp = kind === 'role' ? '用戶新增的角色不帶任何權限' : (typeof fpSay === 'function' ? fpSay : (t) => t)('建立要在這台按一次指紋');
    // b4297 05:0x:brain_add 收 {candidate_id, name},候選由 aibd 列(GET brains/candidates),頁面不送路徑
    // 7b81e c12(DECISION-brain-root-c12-7b81e-1120 Q2):candidates 帶 root = file | library | unset | unusable,不回路徑;不再寫「示範」
    const C = F.cands, second = kind !== 'brain' ? `<input class="apq" data-addnf="what" value="${esc(F.what || '')}" maxlength="${REG_KIND[kind][3]}" placeholder="一句話:做什麼" aria-label="說明" autocomplete="off">`
      : C === undefined ? '<div class="apn">讀取候選中…</div>' : !C || !C.length ? `<div class="apn">${esc(brainRootWhy(F.root))}</div>`
      : `<select class="asel" data-addnf="cand" aria-label="資料夾"><option value="">選一個登記過的資料夾</option>${C.map((c) => `<option value="${esc(c.id)}" ${F.cand === c.id ? 'selected' : ''}>${esc(c.label || c.id)}</option>`).join('')}</select>`;
    return `<div class="anf"><input class="apq" data-addnf="name" value="${esc(F.name || '')}" maxlength="${REG_KIND[kind][2]}" placeholder="${REG_KIND[kind][0]}名稱" aria-label="${REG_KIND[kind][0]}名稱" autocomplete="off">${second}<div class="anfb"><i>${fp}</i><button type="button" class="k" data-addnx="1">取消</button><button type="button" class="go ${F.busy ? 'busy' : ''}" data-addns="1" ${F.busy ? 'disabled' : ''}>建立</button></div>${F.msg ? `<div class="aerr" role="alert">${esc(F.msg)}</div>` : ''}</div>`;
  }
  async function regAdd() {
    const A = S.add, F = A && A.newf; if (!F) return;
    const name = String(F.name || '').trim(), what = String(F.what || '').trim();
    if (!name) { F.msg = '要填名稱'; return render(); }
    if (F.kind === 'brain' && !F.cand) { F.msg = '要選一個資料夾'; return render(); }
    const [, type] = REG_KIND[F.kind];
    F.busy = true; F.msg = ''; render();
    try {
      const r = await act(type, F.kind === 'brain' ? { candidate_id: F.cand, name, idem_key: F.key } : { name, what, idem_key: F.key });
      const uid = r && String(r.uid || '');
      if (!uid) throw { code: 'no_uid' };
      if (F.kind === 'role') { S.reg.roles = roleList().concat({ uid, name: r.name || name, what, builtin: false }); A.role = uid; }
      else if (F.kind === 'persona') { S.personaList = personas().concat([[uid, String(r.name || name)]]); A.persona = uid; }
      else { S.reg.brains = ((S.reg && S.reg.brains) || []).concat({ uid, name: r.name || name, notes: 0, sug: [] }); A.brain = uid; A.brainTouched = true; }
      A.newf = null; say('建好了 ' + (r.name || name) + '(' + uid + ')');
    } catch (e) {
      F.busy = false;
      const rs = e && (e.reason || (e.body && e.body.reason));
      F.msg = e && (e.code === 'unknown_act_type' || e.reason === 'unknown_act_type' || e.code === 'not_built') ? '這版 AIB 還不能新增(示意)'
        : rs === 'owner_check_unavailable' ? (F.kind === 'persona' || F.kind === 'brain' ? regNotYet(F.kind) : (typeof fpSay === 'function' ? fpSay : (t) => t)('要本人指紋,這台還不能按'))
        : gateWhy(e) ? (typeof fpSay === 'function' ? fpSay : (t) => t)(gateWhy(e))
        : e && e.status === 403 ? '只有內建頁、這一輪配對的分頁能新增' : '沒建成:' + errText(e);
    }
    render();
  }
  // ---------- S4 人格 / 第二大腦 / 角色:改、封存、還原(fc7f9 11-23,ebab8 ORDER-page2 第 2 項;SPEC-ai-partner-open §5 + 示意 app.js data-medit / data-mdel)----------
  // 看不見的字:跟 aibd aib_approve::text::is_bad_char 同一張(Unicode 類別 Cc Cf Zl Zp Co Cn Cs、半形空白以外的 Zs,加 invisible_extra 那幾個);瀏覽器的 Unicode 版本跟 aibd 釘的不一定一樣,未分配字元可能差幾個
  const AIB_UNSEEN = String.raw`[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Co}\p{Cn}\p{Cs}\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u2800\u3164\ufe00-\ufe0f\uffa0\u{e0100}-\u{e01ef}]|(?! )\p{Zs}`;
  // aibd §1(08de5,未寫碼)才有改 / 封存 / 還原 6+3 支;名字 08de5 11-23 02:56 回覆照這張表定(對照 pending/IFACE-page2-fc7f9-1123.md),只經過 RG_ACTS。
  // <kind>_set {uid, name?, what?} / <kind>_archive {uid} / <kind>_restore {uid};要驗證的先回 202 ticket(fingerAct 處理),最後 2xx 就算成;version.acts 有才送,沒有就寫「這一版還沒有」、不送、不假裝改了。
  // 人格刪 = 封存(編號保留、可還原);第二大腦刪 = 從清單拿掉、筆記檔案不動;內建角色 🔒。要不要驗證由 aibd 決定(全走 fingerAct)
  const RG_ACTS = {
    persona: { set: 'persona_set', archive: 'persona_archive', restore: 'persona_restore' },
    brain: { set: 'brain_set', archive: 'brain_archive', restore: 'brain_restore' },
    role: { set: 'role_set', archive: 'role_archive', restore: 'role_restore' },
  };
  // 08de5 §1 persona_read(04:49 定形狀,還沒寫碼);頁面只在 version.acts 有才呼叫
  const RG_CARD = [['duty', '職責', '這一席負責什麼'], ['redlines', '紅線', '絕對不能做的事'], ['knowledge', '知識域', '熟悉哪些領域、工具']];
  const RG_CARD_NO = '職責 / 紅線 / 知識域:這一版 AIB 還沒有,先只改名稱';
  const rgCardOn = () => hasAct('persona_read') === true && hasAct('persona_set') === true;
  // 回 '' = 可以送;否則回人話
  function rgCardBad(v) {
    const t = String(v || '');
    if (t.length > 4000) return '每一欄最多 4000 字';
    if (/[\u0000-\u0008\u000b-\u001f\u007f]/.test(t)) return '有看不見的控制字元,請重打';
    // 08de5 IFACE-a1 補:零寬 / 方向字元 aibd 回 400 section_hidden_char ⇒ 頁面先擋
    if (new RegExp(AIB_UNSEEN, 'u').test(t.replace(/[\p{Cc}\u3000]/gu, ''))) return '有看不見的零寬或方向字元,請刪掉再存'; // aibd 先去控制字元、全形空白放行,其餘看不見的拒收
    if (t.split('\n').some((l) => /^\s*(#|---)/.test(l))) return '不能有 # 或 --- 開頭的行(會切壞人格卡的段落);改用 - 條列';
    return '';
  }
  async function rgCardLoad(R) {
    R.loading = true; render();
    try { const r = await act('persona_read', { persona_uid: R.uid }); if (S.rg !== R) return; for (const [k] of RG_CARD) R[k] = typeof (r && r[k]) === 'string' ? r[k] : ''; if (r && typeof r.name === 'string' && r.name) R.name = r.name; R.sha = String((r && r.base_sha) || ''); R.orig = { name: R.name, duty: R.duty, redlines: R.redlines, knowledge: R.knowledge }; }
    catch (e) { if (S.rg !== R) return; R.msg = { bad: true, text: '人格卡讀不到:' + rgWhy(e) }; }
    R.loading = false; render();
  }
  const RG_WORD = { persona: ['人格', '封存'], brain: ['第二大腦', '移除'], role: ['角色', '刪除'] };
  const rgHas = (kind, op) => !!RG_ACTS[kind] && hasAct(RG_ACTS[kind][op]) === true;
  const rgOff = (kind, op) => (op === 'set' ? '改' : RG_WORD[kind][1]) + RG_WORD[kind][0] + ':這一版 AIB 還沒有(要新版 AIB),沒有' + (op === 'set' ? '改' : RG_WORD[kind][1]);
  const RG_WHY = { unknown_act_type: '這一版 AIB 還沒有', not_archived: '它已經不在封存區了', archive_io: 'AIB 讀寫封存區失敗,稍後再試', section_hidden_char: '段落裡有看不見的零寬或方向字元(差異區標成 ⟦U+…⟧),刪掉再存', name_not_yet: '這版 AIB 還不能改人格名稱', persona_changed: '這個人格剛被別人改過;已重新讀一次,請再看一下再存', persona_in_use: '有夥伴正在用這個人格,先收掉或換人格', builtin_locked: '內建角色不能改或刪', role_builtin: '內建角色不能改或刪', name_taken: '名稱跟另一個重複', not_found: '找不到這一項(可能別的分頁改過)', page_only: '只有這台內建的頁面能改' };
  const rgOk = (r) => !!r && (r.done === true || (Number(r.__status) >= 200 && Number(r.__status) < 300 && Number(r.__status) !== 202));
  const rgCode = (e) => String((e && ((e.body && e.body.error && e.body.error.code) || e.reason || (e.body && e.body.reason) || e.code)) || '');
  const rgWhy = (e) => { const c = rgCode(e); return (typeof fpSay === 'function' ? fpSay : (t) => t)(RG_WHY[c] || REPO_WHY[c] || errText(e)); };
  // 正在用這個人格的夥伴(沒封存、還在跑或排隊)
  const rgUsers = (uid) => S.seats.filter((x) => x && x.persona_uid === uid && !isArch(x) && (S.sessions || []).some((se) => se.seat_id === x.seat_id && (se.state === 'running' || se.state === 'starting' || se.state === 'pending'))).map((x) => '@' + seatShort(x));
  function rgItem(kind, uid) {
    if (kind === 'persona') { const p = personas().find(([u]) => u === uid); return p ? { uid, name: String(p[1]), what: '' } : null; }
    if (kind === 'brain') { const b = ((S.reg && S.reg.brains) || []).find((x) => x.uid === uid); return b ? { uid, name: b.name, what: b.what || '', sug: b.sug || [] } : null; }
    const r = roleList().find((x) => x.uid === uid); return r ? { uid, name: r.name, what: r.what || '', builtin: !!r.builtin } : null;
  }
  // 包一層:選的那顆照舊,旁邊加 ✎ / 🗑(內建角色 🔒);點了在同一格下面展開改 / 確認
  function rgWrap(kind, uid, inner, builtin) {
    const R = S.rg && S.rg.kind === kind && S.rg.uid === uid ? S.rg : null;
    const act = builtin ? '<span class="rgk" title="內建,不能改或刪">🔒</span>' : `<button type="button" class="ptx" title="改" aria-label="改${RG_WORD[kind][0]}" data-rgedit="${kind}:${esc(uid)}">✎</button><button type="button" class="ptx" title="${RG_WORD[kind][1]}" aria-label="${RG_WORD[kind][1]}${RG_WORD[kind][0]}" data-rgdel="${kind}:${esc(uid)}">🗑</button>`;
    return `<div class="rgw ${R ? 'open' : ''}"><div class="rgr">${inner}<span class="pta">${act}</span></div>${R ? rgBox(kind, uid) : ''}</div>`;
  }
  function rgCardHtml(R) {
    const on = rgCardOn() && !!R.sha, ld = R.loading ? '<div class="apn">讀人格卡…</div>' : '';
    return ld + RG_CARD.map(([k, nm, ph]) => `<textarea class="apq rgta" data-rgf="${k}" rows="3" maxlength="4000" placeholder="${nm}:${ph}" aria-label="${nm}" ${on ? '' : 'disabled'}>${esc(R[k] || '')}</textarea>`).join('') + (on ? '<div class="apn">改了會存成新版本;正在用它的席下次開始時套用新版,要在這台按一次驗證</div>' : `<div class="apn ptoff">${esc(RG_CARD_NO)}</div>`);
  }
  function rgBox(kind, uid) {
    const R = S.rg, it = rgItem(kind, uid), W = RG_WORD[kind];
    if (!it) return '';
    const off = !rgHas(kind, R.op === 'edit' ? 'set' : 'archive') ? `<div class="apn ptoff">${esc(rgOff(kind, R.op === 'edit' ? 'set' : 'archive'))};先看樣子</div>` : '';
    const msg = R.msg ? `<div class="trm${R.msg.bad ? ' bad' : ''}" data-rgmsg>${esc(R.msg.text)}</div>` : '';
    if (R.op === 'edit') {
      return `<div class="ptf rgf"><b>${kind === 'persona' ? '修改人格' : '改' + W[0]}</b>${kind === 'persona' && rgCardOn() && R.sha ? `<div class="apn">名稱 <b>${esc(R.name)}</b>:這版 AIB 還不能改人格名稱</div>` : ''}<input class="apq" ${kind === 'persona' && rgCardOn() && R.sha ? 'hidden ' : ''}data-rgf="name" value="${esc(R.name)}" maxlength="${REG_KIND[kind][2]}" placeholder="${W[0]}名稱" aria-label="${W[0]}名稱" autocomplete="off">${kind === 'brain' ? '<div class="apn">只改顯示的名字;資料夾和裡面的筆記不動</div>' : kind === 'persona' && rgCardOn() && R.sha ? '' : `<input class="apq" data-rgf="what" value="${esc(R.what)}" maxlength="${REG_KIND[kind][3]}" placeholder="一句話:做什麼" aria-label="說明" autocomplete="off">`}${kind === 'persona' ? rgCardHtml(R) : ''}${off}<div class="ptact"><button type="button" class="go" data-rgok="1" ${R.busy ? 'disabled' : ''}>存檔</button><button type="button" class="k" data-rgno="1">取消</button></div>${kind === 'persona' && R.review ? rgDiffHtml(R) : ''}${msg}</div>`;
    }
    const U = kind === 'persona' ? rgUsers(uid) : [];
    if (U.length) return `<div class="cfm rgc"><b>不能封存</b>:${esc(U.join('、'))} 正在用這個人格;先收掉或換人格。<button type="button" class="k" data-rgno="1">知道了</button></div>`;
    const ask = kind === 'brain' ? '從清單移除「' + it.name + '」?裡面的筆記檔案不會刪。' + (it.sug && it.sug.length ? '有 ' + it.sug.length + ' 個人格預設綁它,之後改成不綁。' : '')
      : kind === 'persona' ? '封存「' + it.name + '」?不再出現在清單;編號保留、不會給別人用,之後可以還原。' : '刪除自訂角色「' + it.name + '」?已掛上這個角色的項目會改成不掛。';
    return `<div class="cfm rgc">${esc(ask)}${off}<span class="ptact"><button type="button" class="k" data-rgno="1">取消</button><button type="button" class="go danger" data-rgdelok="1" ${R.busy ? 'disabled' : ''}>${W[1]}</button></span>${msg}</div>`;
  }
  // 改 / 封存之後,這一頁手上的清單跟著改(aibd 清單下次開視窗會重讀)
  function rgLocal(kind, uid, op, name, what) {
    const A = S.add;
    if (kind === 'persona') {
      const L = personas().slice();
      S.personaList = op === 'set' ? L.map(([u, n]) => [u, u === uid ? name : n]) : op === 'archive' ? L.filter(([u]) => u !== uid) : L;
      if (op === 'archive' && A && A.persona === uid) { A.persona = ''; A.brain = ''; }
    } else if (kind === 'brain') {
      const B = (S.reg && S.reg.brains) || [];
      S.reg.brains = op === 'set' ? B.map((b) => (b.uid === uid ? Object.assign({}, b, { name }) : b)) : op === 'archive' ? B.filter((b) => b.uid !== uid) : B;
      if (op === 'archive' && A && A.brain === uid) { A.brain = ''; A.brainTouched = true; }
    } else {
      const R = roleList();
      S.reg.roles = op === 'set' ? R.map((r) => (r.uid === uid ? Object.assign({}, r, { name, what }) : r)) : op === 'archive' ? R.filter((r) => r.uid !== uid) : R;
      if (op === 'archive' && A && A.role === uid) A.role = '';
    }
  }
  function rgChanged(R) { const o = R.orig || {}; return RG_CARD.map(([k]) => k).filter((k) => String(R[k] || '') !== String(o[k] || '')); }
  // c2f0b persona_diff 同一種畫法:純文字、逐行,看不見的字標出來(⇥ tab、· 行尾空白、⍽ 不換行空白、⟦U+…⟧ 零寬 / 方向字元)
  const rgShow = (t) => String(t).replace(/\t/g, '⇥').replace(/\r/g, '␍').replace(/\u00a0/g, '⍽').replace(new RegExp(AIB_UNSEEN, 'gu'), (c) => c === '\u3000' ? c : '⟦U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') + '⟧').replace(/ +$/, (m) => '·'.repeat(m.length));
  function rgDiffLines(a, b) {
    // f163c page2r10 L4:逐行 LCS(行換順序也看得出:移走的標 −、移到的標 +);每段 ≤ 4000 字,n·m 夠
    const A = String(a || '').split('\n'), B = String(b || '').split('\n'), n = A.length, m = B.length, out = [];
    // f163c N3:超過約 400×400 行不算 LCS(省記憶體),整段標舊 −、新 +
    if (n * m > 160000) return A.map((l) => ['-', l]).concat(B.map((l) => ['+', l]));
    const T = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) T[i][j] = A[i] === B[j] ? T[i + 1][j + 1] + 1 : Math.max(T[i + 1][j], T[i][j + 1]);
    let i = 0, j = 0;
    while (i < n || j < m) { if (i < n && j < m && A[i] === B[j]) { out.push([' ', A[i]]); i++; j++; } else if (j >= m || (i < n && T[i + 1][j] >= T[i][j + 1])) out.push(['-', A[i++]]); else out.push(['+', B[j++]]); }
    return out;
  }
  function rgDiffHtml(R) {
    const o = R.orig || {}, n = (t) => Array.from(String(t || '')).length;
    return `<div class="rgdiff"><div class="apn"><b>送出前看一次</b>:${(typeof fpSay === 'function' ? fpSay : (t) => t)('指紋窗只列段名與字數')},內容以這裡為準</div>${RG_CARD.filter(([k]) => rgChanged(R).includes(k)).map(([k, nm]) => `<div class="rgdh">${esc(nm)}(原 ${n(o[k])} 字 → ${n(R[k])} 字)</div>${rgDiffLines(o[k], R[k]).map(([m, l]) => `<div class="rgdl ${m === '+' ? 'add' : m === '-' ? 'del' : ''}">${m === ' ' ? '  ' : m + ' '}${esc(rgShow(l))}</div>`).join('')}`).join('')}<div class="ptact"><button type="button" class="go" data-rgok="1" ${R.busy ? 'disabled' : ''}>確定,去驗證</button><button type="button" class="k" data-rgback="1">回去改</button></div></div>`;
  }
  async function rgSave() {
    const R = S.rg; if (!R || R.busy) return;
    const name = String(R.name || '').replace(/[\u0000-\u001f\u007f]/g, '').trim(), what = String(R.what || '').replace(/[\u0000-\u001f\u007f]/g, '').trim();
    if (!name) { R.msg = { bad: true, text: '要填名稱' }; return render(); }
    if (!rgHas(R.kind, 'set')) { R.msg = { bad: true, text: rgOff(R.kind, 'set') }; return render(); }
    const card = R.kind === 'persona' && rgCardOn() && R.sha;
    if (card) { for (const [k, nm] of RG_CARD) { const b = rgCardBad(R[k]); if (b) { R.msg = { bad: true, text: nm + ':' + b }; return render(); } } }
    // 08de5 A1(IFACE-a1-a15):只送改了的段、不送 name(name_not_yet);多送欄位一律 400 field。沒改就不送
    // f163c REVIEW-a1 L2:指紋窗只列段名與字數 ⇒ 要指紋之前先把改的段落整段差異給人看,按「確定,去驗證」才送
    const chg = card ? rgChanged(R) : [];
    if (card && !chg.length) { R.msg = { text: '沒有改動,不用送' }; R.review = false; return render(); }
    if (card && !R.review) { R.review = true; R.msg = null; return render(); }
    const body = card ? Object.assign({ persona_uid: R.uid, base_sha: R.sha }, ...chg.map((k) => ({ [k]: String(R[k] || '') }))) : R.kind === 'brain' ? { uid: R.uid, name } : { uid: R.uid, name, what };
    R.busy = true; R.msg = null; render();
    try {
      const r = await fingerAct(RG_ACTS[R.kind].set, body);
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      rgLocal(R.kind, R.uid, 'set', name, what);
      S.rgMsg = { text: '已改' + RG_WORD[R.kind][0] + '「' + name + '」' }; S.rg = null;
    } catch (e) { R.busy = false; R.msg = { bad: true, text: '沒有改:' + rgWhy(e) }; if (card && rgCode(e) === 'persona_changed') { R.review = false; return rgCardLoad(R); } }
    render();
  }
  async function rgArchive() {
    const R = S.rg; if (!R || R.busy) return;
    const it = rgItem(R.kind, R.uid); if (!it) return;
    if (!rgHas(R.kind, 'archive')) { R.msg = { bad: true, text: rgOff(R.kind, 'archive') }; return render(); }
    if (R.kind === 'persona' && rgUsers(R.uid).length) return render();
    R.busy = true; R.msg = null; render();
    try {
      const r = await fingerAct(RG_ACTS[R.kind].archive, { uid: R.uid });
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      rgLocal(R.kind, R.uid, 'archive');
      S.rgMsg = { text: '已' + RG_WORD[R.kind][1] + RG_WORD[R.kind][0] + '「' + it.name + '」' + (R.kind === 'brain' ? ';筆記檔案沒動' : ''), undo: rgHas(R.kind, 'restore') ? { kind: R.kind, uid: R.uid, name: it.name } : null };
      S.rg = null;
    } catch (e) { R.busy = false; R.msg = { bad: true, text: '沒有' + RG_WORD[R.kind][1] + ':' + rgWhy(e) }; }
    render();
  }
  async function rgRestore() {
    const U = S.rgMsg && S.rgMsg.undo; if (!U) return;
    try {
      const r = await fingerAct(RG_ACTS[U.kind].restore, { uid: U.uid });
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      S.rgMsg = { text: '已還原' + RG_WORD[U.kind][0] + '「' + U.name + '」;重開這個視窗就會再出現在清單' };
    } catch (e) { S.rgMsg = { bad: true, text: '沒有還原:' + rgWhy(e), undo: U }; }
    render();
  }
  // 08de5 IFACE-a1-a15 A1(2b959d2275,主線還沒有):GET personas / brains / roles 多一個 archived 陣列(列形狀同原陣列);頁面「已封存」區列出、按 ↺ 送 *_restore(走 fingerAct)。
  // 沒有 restore act 或沒有 archived ⇒ 不畫這一區;還原成功就從封存區拿掉、回到清單(人格直接加回、大腦 / 角色重讀);not_archived ⇒ 也拿掉
  const rgArchOf = (r) => (r && Array.isArray(r.archived) ? r.archived : []).filter((x) => x && x.uid).slice(0, 100).map((x) => [String(x.uid), String(x.display_name || x.name || x.uid)]);
  function rgArchHtml(kind) {
    const L = (S.arch && S.arch[kind]) || []; if (!L.length || !rgHas(kind, 'restore')) return '';
    const open = !!(S.archOpen && S.archOpen[kind]);
    return `<div class="rgarch"><button type="button" class="k" data-rgao="${kind}" aria-expanded="${open}">已${RG_WORD[kind][1]}的${RG_WORD[kind][0]} ${L.length} 個 ${open ? '▴' : '▾'}</button>${open ? L.map(([u, nm]) => `<div class="rgr"><span class="apn"><b>${esc(nm)}</b> <i>${esc(u.slice(-6))}</i></span><button type="button" class="k" data-rgrs="${kind}:${esc(u)}" ${S.rgArchBusy ? 'disabled' : ''}>↺ 還原</button></div>`).join('') : ''}</div>`;
  }
  async function rgRestoreArch(kind, uid) {
    const L = (S.arch && S.arch[kind]) || [], it = L.find(([u]) => u === uid); if (!it || !rgHas(kind, 'restore') || S.rgArchBusy) return;
    S.rgArchBusy = uid; S.rgMsgKind = kind; S.rgMsg = { text: FP_SEE }; render();
    try {
      const r = await fingerAct(RG_ACTS[kind].restore, { uid });
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done', arch: true });
      S.arch[kind] = L.filter(([u]) => u !== uid); // 從封存區拿掉
      if (kind === 'persona') S.personaList = personas().filter(([u]) => u !== uid).concat([[uid, it[1]]]); else loadRegistry();
      S.rgMsg = { text: '已還原' + RG_WORD[kind][0] + '「' + it[1] + '」,清單裡又有了' };
    } catch (e) { S.rgMsg = { bad: true, text: '沒有還原:' + rgWhy(e) }; if (rgCode(e) === 'not_archived') S.arch[kind] = L.filter(([u]) => u !== uid); }
    S.rgArchBusy = null; render();
  }
  // 列底下的一行結果(含 ↺ 還原)
  function rgNote(kind) {
    const M = S.rgMsg; if (!M || (M.undo && M.undo.kind !== kind) || (!M.undo && S.rgMsgKind !== kind)) return '';
    return `<div class="trm${M.bad ? ' bad' : ''}" data-rgmsg>${esc(M.text)}${M.undo ? ' <button type="button" class="k" data-rgundo="1">↺ 還原</button>' : ''}</div>`;
  }
  async function rgClick(x) {
    const ed = x.closest('[data-rgedit]'), dl = x.closest('[data-rgdel]');
    if (ed || dl) {
      const [kind, ...rest] = (ed || dl).dataset[ed ? 'rgedit' : 'rgdel'].split(':'), uid = rest.join(':'), it = rgItem(kind, uid);
      if (!it || it.builtin) return true;
      const same = S.rg && S.rg.kind === kind && S.rg.uid === uid && S.rg.op === (ed ? 'edit' : 'del');
      S.rg = same ? null : { kind, uid, op: ed ? 'edit' : 'del', name: it.name, what: it.what, busy: false, msg: null };
      S.rgMsg = null; S.rgMsgKind = kind; render();
      if (S.rg && ed) { const i = document.querySelector('[data-rgf="name"]'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
      if (S.rg && ed && kind === 'persona' && rgCardOn()) await rgCardLoad(S.rg);
      return true;
    }
    if (x.closest('[data-rgno]')) { S.rg = null; render(); return true; }
    if (x.closest('[data-rgok]')) { S.rgMsgKind = S.rg && S.rg.kind; await rgSave(); return true; }
    if (x.closest('[data-rgback]')) { if (S.rg) S.rg.review = false; render(); return true; }
    if (x.closest('[data-rgdelok]')) { S.rgMsgKind = S.rg && S.rg.kind; await rgArchive(); return true; }
    if (x.closest('[data-rgundo]')) { await rgRestore(); return true; }
    const ao = x.closest('[data-rgao]'); if (ao) { const S2 = (S.archOpen = S.archOpen || {}); S2[ao.dataset.rgao] = !S2[ao.dataset.rgao]; render(); return true; }
    const rs = x.closest('[data-rgrs]'); if (rs) { const i = rs.dataset.rgrs.indexOf(':'); await rgRestoreArch(rs.dataset.rgrs.slice(0, i), rs.dataset.rgrs.slice(i + 1)); return true; }
    return false;
  }
  const ICO_SAFE = '<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 4.6-3 8.3-7 10-4-1.7-7-5.4-7-10V6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  const ICO_WORK = '<svg viewBox="0 0 24 24"><path d="M3 7h7l2 2h9v10H3z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  const ICO_HOST = '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3" y="13" width="18" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7 7.5h.01M7 16.5h.01" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>'; // S2
  const ICO_DGRS = '<svg viewBox="0 0 24 24"><path d="M14.5 5.5a4 4 0 0 0 4.9 4.9l-8.6 8.6a2.1 2.1 0 0 1-3-3l8.6-8.6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  // 7b81e repos.rs(N-web-worktree,ORDER-webonly 第 1 項):要本人指紋的動作,第一次不帶 ticket ⇒ aibd 回 202 {ticket}、
  // 這台跳指紋視窗;之後每 3 秒帶同一張 ticket 重送同一份內容,過了回 200、還沒按回 403 owner_check_not_approved;
  // 到單號到期(沒給就 10 分)沒過就停(沒按或按了拒絕)。aibd 沒接指紋 ⇒ 403 owner_check_unavailable,直接丟給呼叫的人。
  // 7b81e IFACE-owner-touch r2 補(T1,5d33a 通過):把一席升成受信任 12 小時 / 收回;只給 safe / work 席。
  // 升要按這台的指紋(流程同登記倉,fingerAct);收回不用。受信任到幾點:席的 JSON 還沒有,先記這頁拿到的回應(trusted_until 是 unix 秒);
  // 席的 JSON 之後有 trusted_until 就以它為準。這台沒開(trusted_seats_off)或這頁不能做(page_only)⇒ 這頁不再顯示鈕
  // c46:指紋窗可能沒到最前面 ⇒ 每個倒數都提示;c45:票過期後視窗可能還在、還能按 ⇒ 明講這張已過期、請按取消
  const FINGER_BEHIND = '視窗可能在其他視窗後面';
  // ebab8 12:12(Andy 12:1x 徹查 Windows 開席):Windows 上沒有「指紋」,本人確認是 Windows Hello(PIN);字跟 AIB 那台的平台走,Mac 原字不動
  const fpWin = () => { const o = String((S.version && (S.version.os || (S.version.capabilities && S.version.capabilities.os))) || (S.thermal && S.thermal.os) || '').toLowerCase(); return o ? /^win/.test(o) : /Win/.test(String((globalThis.navigator && navigator.platform) || '')); };
  const FP_WIN_SAY = [['不用按指紋', '不用 Windows Hello 確認'], ['按一次指紋', '用 Windows Hello 確認一次'], ['按這台的指紋', '用這台的 Windows Hello 確認'], ['沒按指紋', '沒用 Windows Hello 確認'], ['按指紋', '用 Windows Hello 確認'], ['指紋沒按過', 'Windows Hello 沒確認'], ['指紋沒過', 'Windows Hello 沒通過'], ['指紋視窗', 'Windows Hello 視窗'], ['指紋窗', 'Windows Hello 視窗'], ['看到指紋按過', '看到 Windows Hello 確認過'], ['等指紋', '等 Windows Hello'], ['指紋', 'Windows Hello']];
  const fpSay = (t) => { if (typeof t !== 'string' || !t.includes('指紋') || !fpWin()) return t; let o = t.split('金鑰指紋').join('\u0001'); for (const [a, b] of FP_WIN_SAY) o = o.split(a).join(b); return o.split('\u0001').join('金鑰指紋'); }; // 3e53b N1:主機金鑰指紋是雜湊,不換
  const FP_SEE = '等你驗證 · 看畫面上那張卡'; // 倒數、對象、取消都在「等你驗證」卡上
  const FINGER_GONE = '這張已過期,視窗若還在請按取消';
  // 第 4 件「在網頁跑測試拿收據」(7ebca 4758a4702b 21:43,蓋過 b4297 IFACE r0):receipt_request / receipt_read / receipt_withdraw。
  // 頭碼:席的 JSON 還沒有;先讀 tree.head(已請 7ebca 補),沒有就鈕灰掉並說原因。沒有背景輪詢 ⇒ 這頁在跑的每 15 秒 read 一次
  const RCP_WHY = {
    bad_head: '頭碼不對(要 40 碼)', bad_scope: '只能選窄或整套', bad_tests: '窄收據要 1 到 8 項「crate:測試檔」(例 aibd:seats_tree)', seat_id_invalid: '席號不對',
    receipt_not_allowed: '這台沒開網頁跑收據,或這一席不是受信任席', head_not_seat_head: '這個頭碼不是這一席現在的頭(它可能又提交了;重新整理再按)',
    receipt_built_in_page_only: '只有這台內建的頁面能跑收據', receipt_in_flight: '這一席已經有收據在跑,等它跑完或撤掉再排',
    // f1fae 20:3x(aibd 片 6a5f2e0e44):測試機的收據閘從頭讀到的刀跟指紋卡上的不同 / 這頭沒刀 / 刀表做不出 ⇒ 一律 knives_refused
    knives_refused: '測試機那邊從這個頭讀到的刀,跟你按指紋時卡上寫的不一樣(或這頭沒有刀、刀表做不出來);沒有跑。重新整理再排一次',
    no_host: '現在沒有可用的測試機,沒有排', already_done: '這份已經跑完,不能撤', no_such_run: '找不到這份收據', bad_run_id: '收據號不對',
  };
  // 7ebca 62153be32a(21:52):多 asking(正在問測試機,10 分沒下文變 lost)與 lost(測試機失聯);receipt_list {seat_id} 給這一席的列(在途全給、結束的最近 10 份)
  const RCP_STATE = { asking: '正在問測試機', waiting_owner: '等主人同意', running: '在跑', done: '跑完', red: '紅', withdrawn: '已撤', lost: '測試機失聯', passed_in_sandbox: '沙盒內通過(固定略過 21 支,其餘全過;不算綠)' };
  // 7ebca af6717037f(11-21 00:35):passed_in_sandbox 是自己的一種結果,不畫綠(green 仍是 false、reasons 空)
  const RCP_REASON = { lost: '測試機失聯(問了 10 分鐘沒下文,或跑到一半斷了)', owner_refused: '主人沒同意', seal_missing: '收據沒有封條', 'rc_nonzero:restored': '刀跑完復原的那一步失敗', knives_refused: '測試機讀到的刀跟卡上不同(或沒刀 / 刀表做不出),沒有跑' };
  const rcpReason = (w) => RCP_REASON[w] || (/^knives_refused:/.test(w) ? RCP_REASON.knives_refused + '(' + w.slice(15) + ')' : w);
  // 頁卡三欄(f1fae 6a5f2e0e44):knives_n 刀幾把、knife_reader 讀表版本(例 knife-table/1)、base 比對的底頭碼;沒給就不寫
  const rcpKnifeCard = (x) => (x.knives_n != null ? ' · 卡上刀 ' + (Number(x.knives_n) | 0) + ' 把' : '') + (x.knife_reader ? ' · 讀表 ' + String(x.knife_reader).slice(0, 24) : '') + (/^[0-9a-f]{7,40}$/.test(String(x.base || '')) ? ' · 底 ' + String(x.base).slice(0, 9) : '');
  const rcpLive = (x) => x && (x.state === 'asking' || x.state === 'waiting_owner' || x.state === 'running');
  async function rcpList(id) {
    S.rcpListed[id] = true;
    try { const j = await act('receipt_list', { seat_id: id }); const L = Array.isArray(j && j.runs) ? j.runs : []; S.rcp[id] = []; for (const x of L.slice().reverse()) rcpKeep(Object.assign({ seat_id: id }, x)); render(); }
    catch (e) { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); if (rs === 'unknown_act_type') S.rcpListed[id] = 'old'; }
  }
  const headOf = (s) => { const h = String((s.tree && s.tree.head) || (s.repo_copy && s.repo_copy.head) || ''); return /^[0-9a-f]{40}$/.test(h) ? h : ''; };
  function rcpBtn(s) {
    if (S.rcpOff) return '';
    const h = headOf(s);
    return `<button type="button" class="k" data-rcpask="${esc(s.seat_id)}" ${h && !S.sending ? '' : 'disabled'} title="${esc(h ? '在測試機上跑這一席現在的頭 ' + h.slice(0, 12) + '(窄 = 指定幾個測試檔;整套 = 全部 + 刀)' : 'AIB 還沒給這一席的頭碼,還不能從頁面跑收據')}">跑收據</button>`;
  }
  function rcpBox(s) {
    if (!S.rcpOff && !S.rcpListed[s.seat_id]) { S.rcpListed[s.seat_id] = true; setTimeout(() => rcpList(s.seat_id), 0); }
    const A = S.rcpAsk && S.rcpAsk.seat === s.seat_id ? S.rcpAsk : null, L = S.rcp[s.seat_id] || [], M = S.rcpMsg && S.rcpMsg.seat === s.seat_id ? S.rcpMsg : null;
    let h = '';
    if (A) h += `<div class="rcpq" data-rcpform><span>跑收據 · 頭 ${esc(headOf(s).slice(0, 12))}</span><label><input type="radio" class="ck" name="rcpsc" data-rcpscope="narrow" ${A.scope === 'narrow' ? 'checked' : ''}> 窄(指定測試檔,不算綠)</label><label><input type="radio" class="ck" name="rcpsc" data-rcpscope="full" ${A.scope === 'full' ? 'checked' : ''}> 整套(全部測試 + 刀)</label>${A.scope === 'narrow' ? `<input class="apq" data-rcptests value="${esc(A.tests || '')}" placeholder="aibd:seats_tree, aib-drive:andy_open(最多 8 項)" aria-label="要跑的測試檔">` : ''}<button type="button" class="k g" data-rcpgo="${esc(s.seat_id)}">排</button><button type="button" class="k" data-rcpno="1">不要</button></div>`;
    if (M) h += `<div class="trm${M.bad ? ' bad' : ''}" data-rcpmsg>${esc(M.text)}</div>`;
    for (const x of L.slice(0, 3)) {
      const t = x.tests_total || {}, k = x.knives || {}, why = Array.isArray(x.reasons) ? x.reasons : [];
      const badge = x.state === 'done' && x.green ? '<b class="rcg">綠</b>' : x.state === 'passed_in_sandbox' ? '<b class="rcs">沙盒過</b>' : x.state === 'red' || (x.state === 'done' && !x.green && !x.narrow) ? '<b class="rcr">紅</b>' : '';
      h += `<div class="rcpr" data-rcprun="${esc(x.run_id)}">${badge}收據 ${x.narrow || x.scope === 'narrow' ? '窄' : '整套'} · 頭 ${esc(String(x.head || '').slice(0, 12))} · ${esc(RCP_STATE[x.state] || String(x.state || ''))}${x.host ? ' · ' + esc(x.host) : ''}${esc(rcpKnifeCard(x))}${x.sandbox && x.state !== 'passed_in_sandbox' ? ' · 沙盒內跑' : ''}${t.suites != null ? ` · 測試 ${t.passed | 0} 過 / ${t.failed | 0} 失敗${t.ignored ? ' / ' + (t.ignored | 0) + ' 略過' : ''}` : ''}${k.expected != null ? ` · 刀 ${k.bites | 0}/${k.expected | 0}${k.misses ? '(' + (k.misses | 0) + ' 沒咬)' : ''}` : ''}${why.length ? ` · <span class="rcw" title="${esc(why.join(' '))}">${why.map((w) => esc(rcpReason(String(w)))).join('、')}</span>` : ''}${rcpLive(x) ? `<button type="button" class="k" data-rcpread="${esc(x.run_id)}">更新</button><button type="button" class="k" data-rcpwd="${esc(x.run_id)}">撤</button>` : ''}</div>`;
    }
    return h;
  }
  function rcpKeep(row) {
    if (!row || !row.run_id) return;
    const id = String(row.seat_id || ''), L = (S.rcp[id] = S.rcp[id] || []);
    const i = L.findIndex((y) => y.run_id === row.run_id);
    if (i >= 0) L[i] = row; else L.unshift(row);
    if (L.some(rcpLive) && !S.rcpTimer) S.rcpTimer = setInterval(rcpPoll, 15000);
  }
  async function rcpPoll() {
    const live = Object.values(S.rcp).flat().filter(rcpLive);
    if (!live.length) { clearInterval(S.rcpTimer); S.rcpTimer = null; return; }
    for (const x of live) { try { rcpKeep(await act('receipt_read', { run_id: x.run_id })); } catch {} }
    render();
  }
  const rcpWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return (typeof fpSay === 'function' ? fpSay : (t) => t)(RCP_WHY[rs] || errText(e)); };
  async function rcpGo(id) {
    const s = seatById(id), A = S.rcpAsk; if (!s || !A) return;
    const tests = A.scope === 'narrow' ? String(A.tests || '').split(/[,\s]+/).filter(Boolean) : [];
    if (A.scope === 'narrow' && (tests.length < 1 || tests.length > 8 || tests.some((t) => !/^[a-z0-9_-]+:[A-Za-z0-9_\/.-]+$/.test(t)))) { S.rcpMsg = { seat: id, bad: true, text: RCP_WHY.bad_tests }; render(); return; }
    S.rcpMsg = null; S.sending = true; render();
    try {
      const row = await act('receipt_request', Object.assign({ seat_id: id, head: headOf(s), scope: A.scope }, A.scope === 'narrow' ? { tests } : {}));
      rcpKeep(Object.assign({ seat_id: id }, row)); S.rcpAsk = null;
      S.rcpMsg = { seat: id, text: '排了' + (row && row.host ? ',在 ' + row.host : '') + ';這頁每 15 秒更新一次' };
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code);
      if (rs === 'receipt_built_in_page_only') S.rcpOff = true;
      S.rcpMsg = { seat: id, bad: true, text: rcpWhy(e) };
    }
    S.sending = false; render();
  }
  async function rcpAct(type, run) {
    const x = Object.values(S.rcp).flat().find((y) => y.run_id === run), id = x ? x.seat_id : '';
    try { const row = await act(type, { run_id: run }); rcpKeep(Object.assign({ seat_id: id }, row)); if (type === 'receipt_withdraw') S.rcpMsg = { seat: id, text: '撤了' }; }
    catch (e) { S.rcpMsg = { seat: id, bad: true, text: rcpWhy(e) }; }
    render();
  }
  // 第 6 件第一片(a9136 IFACE-web-merge r1):有 repo_copy 的席 ⇒「送回倉(建新分支)」;這種席不給合回。
  // 兩步跟登記倉同形:先問(202 帶 plan 給畫面)→ 指紋 → 帶票 + plan.head 重送 ⇒ 200 {repo, branch, head, commits, files};分支名以 200 那次為準
  const SB_WHY = {
    seat_busy: '這一席正在做事,這一輪做完再按「交回本機原倉」(不用停席)', nothing_to_send: '副本裡沒有倉裡沒有的提交,沒東西可送(只送已提交的)',
    settings_touched: '動到 AI 設定檔、workflow、.gitmodules、.lfsconfig 或符號連結,不能從頁面送', objects_missing: '副本的物件不齊,沒有送',
    too_big: '副本太大,超過上限,沒有送', disk_low: '倉那邊的磁碟不夠,沒有送', repo_unsafe: '登記倉自己的設定指到程式,AIB 不在那裡跑 git',
    send_failed: 'git 建分支失敗(或 20 個名字都被佔了),倉沒有改', head_moved: '按指紋之後副本的頭變了,什麼都沒寫;請再按一次交回本機原倉',
    seat_id: '這一席不是登記倉的副本,不能交回原倉', send_back_built_in_page_only: '只有這台內建的頁面能交回原倉',
    owner_check_unavailable: '這台還不能按指紋,沒有送', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有送',
    finger_denied: '指紋取消了,沒有送', finger_expired: FINGER_GONE + ';沒有送,要的話再按一次', finger_timeout: '單號過期前沒按指紋,' + FINGER_GONE + ';沒有送',
  };
  const SB_SAFE = '只在你這台電腦的原倉建一條新分支:main 和你正在用的分支都不動,也不會上 GitHub。';
  const SB_NAME = '交回本機原倉(新分支)'; // 519d0 10:07 第 4 點(Andy 10:05 連問兩次「送回倉」是什麼)
  // G4 跟上(519d0 22:41,5d33a REVIEW-g4a r3 N3):開著閒著的席也能交回;只有正在做事(aibd 給的 repo_copy.sendable === false)才擋,不看 running()
  const sbHeld = (id) => { const s = seatById(id); const rc = s && s.repo_copy; return !!rc && typeof rc === 'object' && rc.sendable === false; };
  function sendBackHtml(s) {
    const rc = s.repo_copy && typeof s.repo_copy === 'object' ? s.repo_copy : null;
    if (!rc) return '';
    if (S.sb && S.sb.seat === s.seat_id) return '';
    return `<button type="button" class="k" data-sendback="${esc(s.seat_id)}" title="${esc((rc.sendable === false ? '這一席正在做事:這一輪做完再交回(不用停席);' : '') + '把這一席副本裡已提交的改動,交回你這台電腦的原倉「' + String(rc.repo || '') + '」,建一條新分支。' + SB_SAFE)}" ${S.sending || S.sb ? 'disabled' : ''}>${S.sbBusy === s.seat_id || rc.sendable === false ? '這輪做完再' : ''}${SB_NAME}</button>`;
  }
  function sendBackBox(s) {
    const B = S.sb && S.sb.seat === s.seat_id ? S.sb : null, M = S.sbMsg && S.sbMsg.seat === s.seat_id ? S.sbMsg : null;
    if (B) {
      const p = B.plan || {}, L = Array.isArray(p.listed) ? p.listed : [];
      const what = p.repo ? `送回「${esc(p.repo)}」,新分支 <b>${esc(p.branch || '')}</b>${p.head ? ' · 頭 ' + esc(String(p.head).slice(0, 12)) : ''}${p.commits != null ? ' · ' + (Number(p.commits) | 0) + ' 個提交' : ''}${p.files != null ? ' · ' + (Number(p.files) | 0) + ' 個檔' : ''}` : '正在問 AIB 要送什麼…';
      return `<div class="sbw" data-sendbackwait>${what}${L.length ? `<div class="sbl">要讓你看到的檔:${L.slice(0, 20).map((x) => '<code>' + esc(String(x)) + '</code>').join(' ')}</div>` : ''}<div>${SB_SAFE}只送已提交的。</div>${B.left != null ? `<div class="sbf">${FP_SEE}</div>` : ''}</div>`;
    }
    if (M) return `<div class="trm${M.bad ? ' bad big' : ''}" data-sendbackmsg role="${M.bad ? 'alert' : 'status'}">${esc(M.text)}${M.files && M.files.length ? '<div class="sbl">' + M.files.map((x) => '<code>' + esc(String(x)) + '</code>').join(' ') + '</div>' : ''}</div>${M.bad ? '' : pushBox(s)}`;
    return '';
  }
  async function sendBack(id) {
    S.sbMsg = null; S.sb = { seat: id, plan: null, left: null }; render();
    try {
      const r = await fingerAct('seat_send_back', { seat_id: id }, (t, sec, first) => { if (S.sb && S.sb.seat === id) { S.sb.plan = (first && first.plan) || S.sb.plan; S.sb.left = sec; render(); } }, (first) => ({ head: String((first && first.plan && first.plan.head) || '') }));
      S.sbOk = { seat: id, repo: String((r && r.repo) || ''), branch: String((r && r.branch) || ''), head: String((r && r.head) || '') }; S.pushMsg = null; S.prAsk = null;
      S.sbMsg = { seat: id, text: '已在你這台電腦的原倉「' + String((r && r.repo) || '') + '」建分支 ' + String((r && r.branch) || '') + (r && r.head ? '(頭 ' + String(r.head).slice(0, 12) + (r.commits != null ? ',' + (Number(r.commits) | 0) + ' 個提交' : '') + (r.files != null ? '、' + (Number(r.files) | 0) + ' 個檔' : '') + ')' : '') + '。main 沒動,也還沒上 GitHub。' };
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code), c = e && e.code;
      const k = SB_WHY[c] ? c : rs;
      const b = (e && e.body) || {};
      S.sbMsg = { seat: id, bad: true, text: '沒有交回:' + (typeof fpSay === 'function' ? fpSay : (t) => t)(SB_WHY[k] || errText(e)) + (k === 'repo_unsafe' && b.why ? ':' + String(b.why) : '') + (e && e.status === 409 ? '。沒有建分支;暫存的物件 git 之後會自己清' : ''), files: k === 'settings_touched' && Array.isArray(b.files) ? b.files.slice(0, 20) : null };
    }
    if (S.sbMsg && S.sbMsg.bad) say(S.sbMsg.text, 'err'); // 519d0 10:07 第 1 點:席窗沒開或捲走了,頂端也寫同一句
    S.sb = null; render();
  }
  // 第 5 件殼(519d0 22:19):送回倉成功那列多「推上去」(G2)與「開 PR」(G3)。
  // 推上去照 7b81e IFACE-owner-touch r3 補(d6bea879a3):repo_push {repo, branch}(+ 重送 ticket),頭碼與遠端 aibd 自己讀;202 不帶 plan ⇒ 用送回倉那次的分支與頭;200 {repo, branch, sha, remote_host}。
  // 開 PR 照 r4 補(7b81e 4de2bce5d2,519d0 22:30 內建頁叫 + 每次指紋):pr_open {repo, branch, title, body}(+ 同一份原字重送 ticket);200 {number, url, draft: true, base: main};
  // 現在每台都回 503 pr_open_unavailable(還沒有 GitHub 權杖,問指紋之前)⇒ 不當錯誤,鈕停用寫原因。aibd 回 unknown_act_type / repo_push_off / page_only ⇒ 收起那顆鈕、寫原因。只推 web/<碼>-<MMDD>-<n>
  const PUSH_WHY = {
    unknown_act_type: '推上去要等後端(7b81e 的 repo_push 還沒進這台的 AIB)', branch_not_web: '只能推 web/<碼>-<MMDD>-<n> 這種分支',
    repo_push_off: '這台沒開從頁面推上去', page_only: '只有這台內建的頁面能推', push_fields: '頁面送的欄位不對(請回報頁面工程席)', push_branch: '只能推 web/<碼>-<MMDD>-<n> 這種分支',
    push_repo_unknown: '找不到這個登記倉', push_branch_unknown: '倉裡沒有這條分支', push_no_remote: '這個倉沒有設定遠端', push_repo_unsafe: '登記倉自己的設定指到程式,AIB 不在那裡跑 git',
    push_changed: '按指紋之後分支或遠端變了,請再按一次', push_touches_github: '這條分支改到 .github/,不能從頁面推', push_leak: '分支裡有看起來像金鑰或主機資訊的內容,沒有推', push_leak_unavailable: '這台沒辦法檢查有沒有洩漏,沒有推',
    push_not_fast_forward: '遠端那條分支跟這裡分岔了,不會強推', push_base_unknown: '抓不到遠端 main(多半沒網路或認證不過)', push_failed: '遠端拒絕或連不上,沒有推', push_no_answer: '120 秒沒推完,當作沒推',
    owner_check_unavailable: '這台還不能按指紋,沒有推', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有推',
    finger_denied: '指紋取消了,沒有推', finger_expired: FINGER_GONE + ';沒有推,要的話再按一次', finger_timeout: '單號過期前沒按指紋,' + FINGER_GONE + ';沒有推',
  };
  const PR_WHY = {
    unknown_act_type: '開 PR 要等後端(7b81e 的 pr_open 還沒進這台的 AIB)', pr_open_unavailable: '這台還沒有開 PR 的權杖', pr_open_off: '這台沒開從頁面開 PR', page_only: '只有這台內建的頁面能開 PR',
    pr_fields: '頁面送的欄位不對(請回報頁面工程席)', pr_title: '標題要一行、1 到 200 字', pr_body: '說明太長(最多 16 KB)', pr_no_github_repo: '這個倉的遠端不是 GitHub',
    pr_changed: '按指紋之後分支或遠端變了,請再按一次', pr_exists: '這條分支已經有 PR 了', pr_failed: 'GitHub 拒絕或連不上,沒有開',
    owner_check_unavailable: '這台還不能按指紋,沒有開', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有開',
    finger_denied: '指紋取消了,沒有開', finger_expired: FINGER_GONE + ';沒有開,要的話再按一次', finger_timeout: '單號過期前沒按指紋,' + FINGER_GONE + ';沒有開',
    push_branch: '只能從 web/<碼>-<MMDD>-<n> 這種分支開', push_repo_unknown: '找不到這個登記倉', push_branch_unknown: '倉裡沒有這條分支', push_no_remote: '這個倉沒有設定遠端', push_repo_unsafe: '登記倉自己的設定指到程式,AIB 不在那裡跑 git', branch_not_web: '只能從 web/ 開頭的分支開 PR', not_pushed: '這條分支還沒推上去,先按「推上去」' };
  function pushBox(s) {
    const O = S.sbOk && S.sbOk.seat === s.seat_id ? S.sbOk : null;
    if (!O || !/^web\/[A-Za-z0-9]+-\d{4}-\d+$/.test(O.branch)) return '';
    const W = S.pushF && S.pushF.seat === s.seat_id ? S.pushF : null, A = S.prAsk && S.prAsk.seat === s.seat_id ? S.prAsk : null, M = S.pushMsg && S.pushMsg.seat === s.seat_id ? S.pushMsg : null;
    let h = '<div class="pbx" data-pushbox>';
    if (W) h += `<div class="sbw" data-pushwait>要推「${esc(O.repo)}」的 <b>${esc(O.branch)}</b> · 頭 ${esc(O.head.slice(0, 12))} 到遠端(只推 web/ 開頭,不動其他分支)<div class="sbf">${FP_SEE}</div></div>`;
    else if (S.prF && S.prF.seat === s.seat_id) h += `<div class="sbw" data-prwait>開 PR(草稿):基底 <b>main</b> ← <b>${esc(O.branch)}</b> · 「${esc(S.prF.title)}」<div class="sbf">${FP_SEE}</div></div>`;
    else if (S.ghTok && S.ghTok.seat === s.seat_id) h += ghTokBox(S.ghTok);
    else if (A) h += `<div class="rcpq" data-prform>開 PR(草稿):<input class="apq" data-prtitle value="${esc(A.title)}" maxlength="200" aria-label="PR 標題"><textarea class="apq" data-prbody rows="2" maxlength="16000" placeholder="說明(可空)" aria-label="PR 說明">${esc(A.body || '')}</textarea> 基底 <b>main</b> ← <b>${esc(O.branch)}</b><button type="button" class="k g" data-prgo="${esc(s.seat_id)}">開</button><button type="button" class="k" data-prno="1">不要</button></div>`;
    else h += (S.pushOff ? `<span class="trs">${esc(S.pushOff)}</span>` : `<button type="button" class="k" data-push="${esc(s.seat_id)}" title="把 ${esc(O.branch)} 推到遠端;${(typeof fpSay === 'function' ? fpSay : (t) => t)('要按這台的指紋')}">推上去</button>`) + (S.prOff ? `<span class="trs">${esc(S.prOff)}</span>` : `<button type="button" class="k" data-pr="${esc(s.seat_id)}" title="從 ${esc(O.branch)} 對 main 開 PR">開 PR</button>`);
    if (M) h += `<div class="trm${M.bad ? ' bad' : ''}" data-pushmsg>${esc(M.text)}${M.url ? ' <a href="' + esc(M.url) + '" target="_blank" rel="noopener">' + esc(M.url) + '</a>' : ''}</div>`;
    return h + '</div>';
  }
  // 7b81e DESIGN-gh-token-card r1(519d0 09:27,Andy 09:2x「要他開終端機的事都做成按鈕」):這個倉沒有 GitHub 權杖 ⇒
  // ① 開 GitHub 建權杖頁(網址參數帶好:名字、擁有者、90 天、contents / pull_requests 寫入;倉要人自己選)
  // ② 貼上 ⇒ aibd 先問 GitHub 這把能不能推這個倉(不能就說原因、不跳窗),能才按指紋存進鑰匙圈。
  // 權杖只在輸入框與送出的那一份 body 裡:不進 S、不進 localStorage、不顯示回來
  const GHTOK_WHY = {
    gh_token_fields: '頁面送的欄位不對(請回報頁面工程席)', gh_token_shape: '這不像 GitHub 權杖(只能有英文、數字與底線);重貼一次',
    gh_token_refused: 'GitHub 不認這把權杖(打錯、過期或被撤銷)', gh_token_repo_not_selected: '這把權杖看不到這個倉;建的時候「Repository access」要選到它',
    gh_token_no_write: '這把權杖對這個倉沒有寫入權限;「Contents」要 Read and write', gh_token_check_failed: '現在問不到 GitHub(網路或 GitHub 出狀況),沒有存;稍後再試',
    gh_token_unavailable: '這台的 AIB 還不能存權杖(Windows 要等 Windows Hello 接上)', gh_token_store: '寫不進這台的鑰匙圈,沒有存',
    push_repo_unknown: '找不到這個登記倉', pr_no_github_repo: '這個倉的遠端不是 GitHub', page_only: '只有這台內建的頁面能存權杖',
    owner_check_unavailable: '這台還不能按指紋,沒有存', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有存',
    finger_denied: '指紋取消了,沒有存', finger_expired: FINGER_GONE + ';沒有存,要的話再按一次', finger_timeout: '等了 2 分鐘沒按指紋,' + FINGER_GONE + ';沒有存',
  };
  const ghTokUrl = (slug) => {
    const [o, n] = String(slug).split('/');
    return 'https://github.com/settings/personal-access-tokens/new?' + new URLSearchParams({ name: ('ez2-aibd-' + n).slice(0, 40), description: 'Ez2 AIB:推 web/ 分支、開草稿 PR(' + slug + ')', target_name: o, expires_in: '90', contents: 'write', pull_requests: 'write' }).toString();
  };
  function ghTokBox(G) {
    const n = String(G.slug).split('/')[1] || '';
    if (G.left != null) return `<div class="sbw" data-ghtokwait>把 GitHub 權杖存進這台,給 <b>${esc(G.slug)}</b> 推分支與開 PR 用<div class="sbf">${FP_SEE}</div></div>`;
    return `<div class="rcpq" data-ghtok>推分支 / 開 PR 要 GitHub 權杖:<b>${esc(G.slug)}</b><div class="apn">① 到 GitHub 建一把細粒度權杖(名字、權限、90 天都帶好了);「Repository access」請選 Only select repositories → <b>${esc(n)}</b>(這一格 GitHub 不能預先幫你選)</div><a class="k" href="${esc(ghTokUrl(G.slug))}" target="_blank" rel="noopener noreferrer">① 到 GitHub 建權杖</a> <input class="apq" type="password" data-ghtokin autocomplete="off" spellcheck="false" placeholder="② 貼上權杖(github_pat_…)" aria-label="GitHub 權杖"><button type="button" class="k g" data-ghtokgo="${esc(G.seat)}">${(typeof fpSay === 'function' ? fpSay : (t) => t)('② 存進這台(按指紋)')}</button><button type="button" class="k" data-ghtokno="1">不要</button>${G.msg ? `<div class="apn bad">${esc(G.msg)}</div>` : ''}</div>`;
  }
  async function ghTokGo(id) {
    const G = S.ghTok, O = S.sbOk; if (!G || G.seat !== id || !O || S.sending) return;
    const inp = document.querySelector('[data-ghtokin]'); const token = inp ? String(inp.value || '').trim() : '';
    if (inp) inp.value = '';
    if (!/^[A-Za-z0-9_]{1,255}$/.test(token)) { G.msg = GHTOK_WHY.gh_token_shape; return render(); }
    S.sending = true; G.msg = ''; render();
    try {
      await fingerAct('repo_gh_token', { repo: O.repo, token }, (t, sec) => { if (S.ghTok === G) { G.left = sec; render(); } });
      S.ghTok = null; S.prOff = null; S.pushMsg = { seat: id, text: '權杖存好了(只存在這台的鑰匙圈);可以再按一次「推上去」或「開 PR」' };
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code);
      G.left = null; G.msg = (typeof fpSay === 'function' ? fpSay : (t) => t)(GHTOK_WHY[rs] || errText(e));
    }
    S.sending = false; render();
  }
  const pushWhy = (T, e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return (typeof fpSay === 'function' ? fpSay : (t) => t)(T[rs] || errText(e)); };
  async function pushGo(id) {
    const O = S.sbOk; if (!O || O.seat !== id) return;
    if (!/^web\/[A-Za-z0-9]+-\d{4}-\d+$/.test(O.branch)) { S.pushMsg = { seat: id, bad: true, text: PUSH_WHY.branch_not_web }; render(); return; }
    S.pushMsg = null; S.pushF = { seat: id, left: 120 }; render();
    try {
      const r = await fingerAct('repo_push', { repo: O.repo, branch: O.branch }, (t, sec) => { if (S.pushF && S.pushF.seat === id) { S.pushF.left = sec; render(); } });
      O.pushed = true;
      S.pushMsg = { seat: id, text: '已推上去:' + String((r && r.branch) || O.branch) + (r && r.sha ? '(頭 ' + String(r.sha).slice(0, 12) + ')' : '') + (r && r.remote_host ? ' 到 ' + r.remote_host : '') + ';還沒開 PR' };
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code);
      // pv8 第 9 格:這個倉沒有權杖 ⇒ aibd 在指紋窗前就說,這裡直接出權杖卡;權杖被 GitHub 拒 ⇒ 講哪裡不對
      const slug = e && e.body && typeof e.body.token_for === 'string' && /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/.test(e.body.token_for) ? e.body.token_for : '';
      if (rs === 'gh_token_missing' && slug) S.ghTok = { seat: id, slug, left: null, msg: '這個倉還沒有 GitHub 權杖;存好之後再按一次「推上去」' };
      else if (rs === 'unknown_act_type' || rs === 'repo_push_off' || rs === 'page_only') S.pushOff = (typeof fpSay === 'function' ? fpSay : (t) => t)(PUSH_WHY[rs]);
      else S.pushMsg = { seat: id, bad: true, text: /^gh_token_/.test(String(rs)) && GHTOK_WHY[rs] ? (typeof fpSay === 'function' ? fpSay : (t) => t)(GHTOK_WHY[rs]) : pushWhy(PUSH_WHY, e) };
    }
    S.pushF = null; render();
  }
  async function prGo(id) {
    const O = S.sbOk, A = S.prAsk; if (!O || !A || O.seat !== id) return;
    S.sending = true; render();
    try {
      if (!A.title || /[\r\n]/.test(A.title) || A.title.length > 200) { S.pushMsg = { seat: id, bad: true, text: PR_WHY.pr_title }; S.sending = false; return render(); }
      const body = { repo: O.repo, branch: O.branch, title: A.title, body: A.body || '' }; // 票綁原字:重送同一份(fingerAct 照送 body)
      S.prAsk = null; S.prF = { seat: id, left: 120, title: A.title }; render();
      const r = await fingerAct('pr_open', body, (t, sec) => { if (S.prF && S.prF.seat === id) { S.prF.left = sec; render(); } });
      S.pushMsg = { seat: id, text: '開了 PR' + (r && r.number ? ' #' + (Number(r.number) | 0) : '') + '(草稿,基底 ' + String((r && r.base) || 'main') + ')', url: r && typeof r.url === 'string' && /^https:\/\//.test(r.url) ? r.url : '' };
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code);
      S.prAsk = null;
      const slug = e && e.body && typeof e.body.token_for === 'string' && /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/.test(e.body.token_for) ? e.body.token_for : '';
      if (rs === 'pr_open_unavailable' && slug) S.ghTok = { seat: id, slug, left: null, msg: '' };
      else if (rs === 'unknown_act_type' || rs === 'pr_open_unavailable' || rs === 'pr_open_off' || rs === 'page_only') S.prOff = (typeof fpSay === 'function' ? fpSay : (t) => t)(PR_WHY[rs]); else S.pushMsg = { seat: id, bad: true, text: pushWhy(PR_WHY, e) };
    }
    S.prF = null; S.sending = false; render();
  }
  const TRUST_WHY = {
    trusted_seats_off: '這台沒開受信任席(bridge.json 的 trusted_seats 要開)', unknown_act_type: '這台的 AIB 還沒有受信任席(AIB 7b81e feat/trust-t1 才有 seat_trust / seat_untrust)', page_only: '只有這台內建的頁面能升受信任',
    seat_not_found: '找不到這一席', seat_archived: '這一席已封存', seat_trust_mode: '只有安全席、做事席能升受信任',
    owner_check_unavailable: '這台問不到指紋,沒有升', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有升',
    finger_denied: '指紋取消了,沒有升', finger_expired: FINGER_GONE + ';沒有升,要的話再按一次', finger_timeout: '單號過期前沒按指紋,' + FINGER_GONE + ';沒有升',
    seat_trust_changed: '按指紋之後這一席的模式、倉的遠端或測試機名單變了,請重按一次', seat_id: '席號不對', trust_store: 'AIB 寫不進受信任紀錄,請稍後再試',
  };
  const trustUntil = (s) => { const v = Number(s.trusted_until != null ? s.trusted_until : S.trust[s.seat_id]); return v > Date.now() / 1000 ? v : 0; };
  function trustHtml(s) {
    if (S.trustOff || !['safe', 'work'].includes(String(s.mode || 'safe'))) return '';
    const F = S.trustF && S.trustF.seat === s.seat_id ? S.trustF : null;
    if (F) return `<span class="trs" data-trustwait>${FP_SEE}</span>`;
    const u = trustUntil(s);
    if (u) return `<span class="trs on">受信任到 ${hhmm(u * 1000)}</span><button type="button" class="k" data-untrust="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''}>收回</button>`;
    return `<button type="button" class="k" data-trust="${esc(s.seat_id)}" title="${(typeof fpSay === 'function' ? fpSay : (t) => t)('按這台的指紋,把這一席升成受信任 12 小時')}" ${S.sending || S.trustF ? 'disabled' : ''}>升成受信任</button>`;
  }
  // 重新登入 AI(a9136 DESIGN-relogin-card r2,Andy 11-21 09:1x「為什麼不直接彈窗」):按 ⇒ 指紋 ⇒ aibd 起登入程式、回登入頁網址
  // (只回給按的這一頁,不進事件)⇒ 開新分頁登入 ⇒ claude 把頁面給的碼貼回來 / codex 在登入頁輸入這裡顯示的碼 ⇒ 每 3 秒問結果
  const RELOG_NAME = { claude: 'Claude', codex: 'Codex', grok: 'Grok', copilot: 'Copilot' };
  const RELOG_WHY = {
    provider: '這個品牌還不能從頁面重新登入', login_in_parent: '預覽裡的席用母版的登入:請到母版頁按重新登入', login_not_tried_here: '這一版 AIB 在 Windows 先只支援 Claude,這一家要等下一版',
    login_running: '已經有一輪登入在跑', login_cooling: '剛登入過,30 秒後再按', provider_not_listed: '這台的開席清單沒有這個品牌',
    login_not_started: '登入程式開不起來', url_host_refused: '登入程式給的網址不是官方登入頁,沒有轉給你', no_url: '登入程式 20 秒內沒給登入網址',
    relogin_built_in_page_only: '只有這台內建的頁面能重新登入', unknown_act_type: '這版 AIB 還不能從頁面重新登入',
    owner_check_unavailable: '這台問不到指紋,沒有開始', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有開始',
    finger_denied: '指紋取消了,沒有開始', finger_expired: FINGER_GONE + ';沒有開始,要的話再按一次', finger_timeout: '等了 2 分鐘沒按指紋,' + FINGER_GONE + ';沒有開始',
    timeout: '10 分鐘內沒登入完,這一輪結束了', exit_not_0: '登入沒成功(登入程式回報失敗)', not_logged_in: '登入程式結束了,但這台還是沒有登入', code: '碼要 8–512 個字、不能有空白,只能送一次', login_id: '這一輪登入找不到了',
  };
  // ebab8 03:34(08de5 核對 H、Andy「不要讓客戶自己執行」):Windows 上 Codex / Grok / Copilot 回 501 login_not_tried_here ⇒ 說這一版只支援 Claude,不叫人開終端機;四語
  const RELOG_WHY_L = {
    en: { login_not_tried_here: 'This version of AIB supports only Claude on Windows for now; this one comes in a later version' },
    ja: { login_not_tried_here: 'この版の AIB は Windows ではまず Claude だけに対応しています。こちらは次の版で対応します' },
    ko: { login_not_tried_here: '이번 버전의 AIB는 Windows에서 우선 Claude만 지원합니다. 이 서비스는 다음 버전에서 지원합니다' },
  };
  const relogWhy = (e) => {
    const rs = typeof e === 'string' ? e : e && (e.reason || (e.body && e.body.reason) || e.code), L = RELOG_WHY_L[typeof askLang === 'function' ? askLang() : ''];
    if (typeof rs === 'string' && L && Object.prototype.hasOwnProperty.call(L, rs)) return L[rs];
    return (typeof fpSay === 'function' ? fpSay : (t) => t)((typeof rs === 'string' && Object.prototype.hasOwnProperty.call(RELOG_WHY, rs) ? RELOG_WHY[rs] : '') || (typeof e === 'string' ? e : errText(e)));
  };
  // 08de5 新登入卡(SHOT-login-card-*-08de5-1126;DESIGN-login-card-page-fc7f9-1126):大字碼框、按了才複製、頂條寫家別 + 共幾步;
  // Andy 01:04:登入網址已帶碼(url 含 device_code)⇒ 不顯示碼框 / 複製,只留打開登入頁;有效時間 aibd 沒給 ⇒ 照實寫
  const RELOG_TTL_NA = '有效時間:這一版 AIB 還沒給';
  const relogAuto = (R) => !!(R && R.dcode && String(R.url || '').includes(R.dcode));
  const relogKey = () => (/Mac/.test(String((globalThis.navigator && navigator.platform) || '')) ? '⌘V' : 'Ctrl+V');
  // Andy 01:2x「需要大字碼卡的全照 grok 設計」:照 08de5 LOGIN-CARD-08de5-1126 同一份樣式(家別色頂條 + 字母圖示、大字碼框、綠字已複製、兩顆大鈕、編號步驟、底條)
  const RELOG_BRAND = ['claude', 'codex', 'grok', 'copilot'];
  // 四家標誌未取得書面同意,依 Andy 11-26 01:3x 決定放入(「B+直接找他們的logo 放」);來源、取得日期、sha256 見 docs/design/BRAND-LOGOS-login-card.md。
  // 檔案原樣(base64 = 官方檔逐位元組),只放在中性小方塊上(深色;Grok 原檔是 currentColor、在 img 裡畫成黑 ⇒ 白方塊),不染家色;旁邊照舊寫家名。
  // Andy 11-26 01:4x「logo 要適當的大小, codex 就太小」:以標誌本體可見範圍對齊,長邊 28px(方塊 44px 的 64%)。
  // 本體佔原檔外框:OpenAI Blossom 354.67/716 ≈ 49.5% ⇒ 整檔等比放大到 56px(留白由方塊邊遮住,路徑與顏色不動);GitHub / Claude 本體 ≈ 外框 ⇒ 28px;Grok 是 2.6:1 扁形,長邊 28px 看起來小一圈 ⇒ 34px(高約 13px)。
  const RELOG_LOGO_PX = { codex: 56, grok: 34 };
  const RELOG_LOGO = {
    codex: 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNzE2IiBoZWlnaHQ9IjcxNiIgdmlld0JveD0iMCAwIDcxNiA3MTYiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik01MDguNzQ5IDMxNy4zOTlDNTE2Ljc3NyAyODcuMzE0IDUwOC45OTEgMjUzLjg4NCA0ODUuMzg5IDIzMC4yODJDNDYxLjc4OCAyMDYuNjgxIDQyOC4zNiAxOTguODk1IDM5OC4yNzMgMjA2LjkyM0MzNzYuMjMxIDE4NC45MjggMzQzLjM5IDE3NC45NTYgMzExLjE0OCAxODMuNTk2QzI3OC45MDYgMTkyLjIzNCAyNTUuNDUgMjE3LjI5MiAyNDcuMzYgMjQ3LjM2MUMyMTcuMjkxIDI1NS40NTEgMTkyLjIzMyAyNzguOTEgMTgzLjU5NSAzMTEuMTQ5QzE3NC45NTcgMzQzLjM5MSAxODQuOTI3IDM3Ni4yMzIgMjA2LjkyNCAzOTguMjc0QzE5OC44OTYgNDI4LjM1OSAyMDYuNjgzIDQ2MS43ODkgMjMwLjI4NCA0ODUuMzkxQzI1My44ODUgNTA4Ljk5MiAyODcuMzEzIDUxNi43NzkgMzE3LjQwMSA1MDguNzVDMzM5LjQ0MiA1MzAuNzQ1IDM3Mi4yODYgNTQwLjcxNyA0MDQuNTI1IDUzMi4wNzlDNDM2Ljc2NyA1MjMuNDQxIDQ2MC4yMjMgNDk4LjM4NCA0NjguMzEzIDQ2OC4zMTVDNDk4LjM4MyA0NjAuMjI0IDUyMy40NCA0MzYuNzY2IDUzMi4wNzggNDA0LjUyNkM1NDAuNzE2IDM3Mi4yODUgNTMwLjc0NyAzMzkuNDQzIDUwOC43NDkgMzE3LjQwMlYzMTcuMzk5Wk00NzAuODk5IDI0NC43NzZDNDg2Ljg5MiAyNjAuNzcgNDkzLjQ4OCAyODIuNjAxIDQ5MC42ODcgMzAzLjQxMkw0MTUuNTc3IDI2MC4wNDZDNDEyLjQxMSAyNTguMjE4IDQwOC41MDkgMjU4LjIxOCA0MDUuMzQ1IDI2MC4wNDZMMzE3LjQwMSAzMTAuODJWMjc3LjUyNkMzMTcuNDAxIDI3NS4xOTEgMzE4LjY1MiAyNzMuMDA1IDMyMC42NzYgMjcxLjgzN0wzODcuNjQ0IDIzMy4xNzRDNDE0LjE3OCAyMTguMzUzIDQ0OC4zNDYgMjIyLjIyMyA0NzAuOTAxIDI0NC43NzZINDcwLjg5OVpNMzU3LjgzNyAzMTEuMTQ0TDM5OC4yNzUgMzM0LjQ5MVYzODEuMTg1TDM1Ny44MzcgNDA0LjUzMkwzMTcuMzk4IDM4MS4xODVWMzM0LjQ5MUwzNTcuODM3IDMxMS4xNDRaTTI2NC43NzYgMjY5LjY5M0MyNjUuMjA3IDIzOS4zMDUgMjg1LjY0NCAyMTEuNjQ5IDMxNi40NTMgMjAzLjM5M0MzMzguMyAxOTcuNTQgMzYwLjUwNSAyMDIuNzQ0IDM3Ny4xMjcgMjE1LjU3M0wzMDIuMDE0IDI1OC45MzdDMjk4Ljg0OCAyNjAuNzY0IDI5Ni44OTggMjY0LjE0NCAyOTYuODk4IDI2Ny43OThWMzY5LjM0NkwyNjguMDY1IDM1Mi42OTlDMjY2LjA0MyAzNTEuNTMxIDI2NC43NzYgMzQ5LjM1MyAyNjQuNzc2IDM0Ny4wMTdWMjY5LjY5MVYyNjkuNjkzWk0yMDMuMzkxIDMxNi40NTRDMjA5LjI0NCAyOTQuNjA4IDIyNC44NTQgMjc3Ljk3OCAyNDQuMjc2IDI2OS45OTlWMzU2LjczQzI0NC4yNzYgMzYwLjM4NCAyNDYuMjI2IDM2My43NjMgMjQ5LjM5MiAzNjUuNTkxTDMzNy4zMzcgNDE2LjM2NUwzMDguNTAzIDQzMy4wMTNDMzA2LjQ4MSA0MzQuMTgxIDMwMy45NjEgNDM0LjE4OCAzMDEuOTM5IDQzMy4wMkwyMzQuOTcxIDM5NC4zNTdDMjA4Ljg2OCAzNzguNzg5IDE5NS4xMzggMzQ3LjI2MSAyMDMuMzkxIDMxNi40NTRaTTI0NC43NzUgNDcwLjlDMjI4Ljc4MSA0NTQuOTA2IDIyMi4xODYgNDMzLjA3NSAyMjQuOTg2IDQxMi4yNjRMMzAwLjA5NiA0NTUuNjNDMzAzLjI2MyA0NTcuNDU3IDMwNy4xNjQgNDU3LjQ1NyAzMTAuMzI4IDQ1NS42M0wzOTguMjczIDQwNC44NTZWNDM4LjE0OUMzOTguMjczIDQ0MC40ODUgMzk3LjAyMiA0NDIuNjcxIDM5NC45OTcgNDQzLjgzOUwzMjguMDI5IDQ4Mi41MDJDMzAxLjQ5NSA0OTcuMzIyIDI2Ny4zMjcgNDkzLjQ1MiAyNDQuNzcyIDQ3MC45SDI0NC43NzVaTTQ1MC44OTcgNDQ1Ljk4MkM0NTAuNDY2IDQ3Ni4zNzEgNDMwLjAyOSA1MDQuMDI3IDM5OS4yMiA1MTIuMjgzQzM3Ny4zNzMgNTE4LjEzNiAzNTUuMTY4IDUxMi45MzIgMzM4LjU0NyA1MDAuMTAyTDQxMy42NTkgNDU2LjczOEM0MTYuODI2IDQ1NC45MTEgNDE4Ljc3NSA0NTEuNTMyIDQxOC43NzUgNDQ3Ljg3N1YzNDYuMzI5TDQ0Ny42MDkgMzYyLjk3N0M0NDkuNjMxIDM2NC4xNDUgNDUwLjg5NyAzNjYuMzIzIDQ1MC44OTcgMzY4LjY1OVY0NDUuOTg1VjQ0NS45ODJaTTUxMi4yODIgMzk5LjIyMUM1MDYuNDI5IDQyMS4wNjggNDkwLjgxOSA0MzcuNjk3IDQ3MS4zOTcgNDQ1LjY3NlYzNTguOTQ2QzQ3MS4zOTcgMzU1LjI5MiA0NjkuNDQ4IDM1MS45MTIgNDY2LjI4MSAzNTAuMDg1TDM3OC4zMzYgMjk5LjMxMUw0MDcuMTcgMjgyLjY2M0M0MDkuMTkyIDI4MS40OTUgNDExLjcxMiAyODEuNDg3IDQxMy43MzQgMjgyLjY1NUw0ODAuNzAyIDMyMS4zMThDNTA2LjgwNSAzMzYuODg3IDUyMC41MzYgMzY4LjQxNSA1MTIuMjgyIDM5OS4yMjFaIiBmaWxsPSJ3aGl0ZSIvPgo8L3N2Zz4K',
    copilot: 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iOTgiIGhlaWdodD0iOTYiIHZpZXdCb3g9IjAgMCA5OCA5NiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGcgY2xpcC1wYXRoPSJ1cmwoI2NsaXAwXzczMF8yNzEzNikiPgo8cGF0aCBkPSJNNDEuNDM5NSA2OS4zODQ4QzI4LjgwNjYgNjcuODUzNSAxOS45MDYyIDU4Ljc2MTcgMTkuOTA2MiA0Ni45OTAyQzE5LjkwNjIgNDIuMjA1MSAyMS42Mjg5IDM3LjAzNzEgMjQuNSAzMy41OTE4QzIzLjI1NTkgMzAuNDMzNiAyMy40NDczIDIzLjczNDQgMjQuODgyOCAyMC45NTlDMjguNzEwOSAyMC40ODA1IDMzLjg3ODkgMjIuNDkwMiAzNi45NDE0IDI1LjI2NTZDNDAuNTc4MSAyNC4xMTcyIDQ0LjQwNjIgMjMuNTQzIDQ5LjA5NTcgMjMuNTQzQzUzLjc4NTIgMjMuNTQzIDU3LjYxMzMgMjQuMTE3MiA2MS4wNTg2IDI1LjE2OTlDNjQuMDI1NCAyMi40OTAyIDY5LjI4OTEgMjAuNDgwNSA3My4xMTcyIDIwLjk1OUM3NC40NTcgMjMuNTQzIDc0LjY0ODQgMzAuMjQyMiA3My40MDQzIDMzLjQ5NjFDNzYuNDY2OCAzNy4xMzI4IDc4LjA5MzcgNDIuMDEzNyA3OC4wOTM3IDQ2Ljk5MDJDNzguMDkzNyA1OC43NjE3IDY5LjE5MzQgNjcuNjYyMSA1Ni4zNjkxIDY5LjI4OTFDNTkuNjIzIDcxLjM5NDUgNjEuODI0MiA3NS45ODgzIDYxLjgyNDIgODEuMjUyTDYxLjgyNDIgOTEuMjA1MUM2MS44MjQyIDk0LjA3NjIgNjQuMjE2OCA5NS43MDMxIDY3LjA4NzkgOTQuNTU0N0M4NC40MTAyIDg3Ljk1MTIgOTggNzAuNjI4OSA5OCA0OS4xOTE0Qzk4IDIyLjEwNzQgNzUuOTg4MyA2LjY5NTM5ZS0wNyA0OC45MDQzIDQuMzA5ZS0wN0MyMS44MjAzIDEuOTIyNjFlLTA3IC0xLjk0NzllLTA3IDIyLjEwNzQgLTQuMzM0M2UtMDcgNDkuMTkxNEMtNi4yMDYzMWUtMDcgNzAuNDM3NSAxMy40OTQxIDg4LjA0NjkgMzEuNjc3NyA5NC42NTA0QzM0LjI2MTcgOTUuNjA3NCAzNi43NSA5My44ODQ4IDM2Ljc1IDkxLjMwMDhMMzYuNzUgODMuNjQ0NUMzNS40MTAyIDg0LjIxODggMzMuNjg3NSA4NC42MDE2IDMyLjE1NjIgODQuNjAxNkMyNS44Mzk4IDg0LjYwMTYgMjIuMTA3NCA4MS4xNTYzIDE5LjQyNzcgNzQuNzQ0MUMxOC4zNzUgNzIuMTYwMiAxNy4yMjY2IDcwLjYyODkgMTUuMDI1NCA3MC4zNDE4QzEzLjg3NyA3MC4yNDYxIDEzLjQ5NDEgNjkuNzY3NiAxMy40OTQxIDY5LjE5MzRDMTMuNDk0MSA2OC4wNDQ5IDE1LjQwODIgNjcuMTgzNiAxNy4zMjIzIDY3LjE4MzZDMjAuMDk3NyA2Ny4xODM2IDIyLjQ5MDIgNjguOTA2MyAyNC45Nzg1IDcyLjQ0NzNDMjYuODkyNiA3NS4yMjI3IDI4LjkwMjMgNzYuNDY2OCAzMS4yOTQ5IDc2LjQ2NjhDMzMuNjg3NSA3Ni40NjY4IDM1LjIxODcgNzUuNjA1NSAzNy40MTk5IDczLjQwNDNDMzkuMDQ2OSA3MS43NzczIDQwLjI5MSA3MC4zNDE4IDQxLjQzOTUgNjkuMzg0OFoiIGZpbGw9IndoaXRlIi8+CjwvZz4KPGRlZnM+CjxjbGlwUGF0aCBpZD0iY2xpcDBfNzMwXzI3MTM2Ij4KPHJlY3Qgd2lkdGg9Ijk4IiBoZWlnaHQ9Ijk2IiBmaWxsPSJ3aGl0ZSIvPgo8L2NsaXBQYXRoPgo8L2RlZnM+Cjwvc3ZnPgo=',
    claude: 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iOTQiIGhlaWdodD0iOTQiIHZpZXdCb3g9IjAgMCA5NCA5NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTE4Ljc2NTcgNjIuNDQzN0wzNy4xODIyIDUyLjExNjdMMzcuNDg1NyA1MS4yMTIyTDM3LjE4MjIgNTAuNzA4NUgzNi4yNzE1TDMzLjE4NTIgNTAuNTIwOEwyMi42NjE1IDUwLjIzOTFMMTMuNTU0NSA0OS44NjM2TDQuNzAwNDQgNDkuMzk0MkwyLjQ3NDI4IDQ4LjkyNDhMMC4zOTk5MDIgNDYuMTU1M0wwLjYwMjI4MSA0NC43OTRMMi40NzQyOCA0My41MjY2TDUuMTU1NzkgNDMuNzYxM0wxMS4wNzU0IDQ0LjE4MzdMMTkuOTggNDQuNzk0TDI2LjQwNTUgNDUuMTY5NUwzNS45Njc5IDQ2LjE1NTNIMzcuNDg1N0wzNy42ODgxIDQ1LjU0NUwzNy4xODIyIDQ1LjE2OTVMMzYuNzc3NCA0NC43OTRMMjcuNTY5MiAzOC41NTA4TDE3LjYwMjEgMzEuOTc5MUwxMi4zOTA4IDI4LjE3NjlMOS42MDgxMiAyNi4yNTI0TDguMTkxNDcgMjQuNDY4Nkw3LjU4NDMzIDIwLjUyNTZMMTAuMTE0MSAxNy43MDkxTDEzLjU1NDUgMTcuOTQzOEwxNC40MTQ2IDE4LjE3ODVMMTcuOTA1NiAyMC44NTQyTDI1LjM0MyAyNi42Mjc5TDM1LjA1NzIgMzMuNzYyOUwzNi40NzM5IDM0LjkzNjRMMzcuMDQ0MyAzNC41NTE0TDM3LjEzMTYgMzQuMjc5MkwzNi40NzM5IDMzLjE5OTZMMzEuMjEyIDIzLjY3MDZMMjUuNTk2IDEzLjk1MzlMMjMuMDY2MyA5LjkxNjk1TDIyLjQwODYgNy41MjI5NkMyMi4xNTM4IDYuNTE4MzEgMjIuMDAzOCA1LjY4NzE0IDIyLjAwMzggNC42NTk1N0wyNC44ODc3IDAuNzE2NTQ0TDI2LjUwNjcgMC4yMDAxOTVMMzAuNDAyNSAwLjcxNjU0NEwzMi4wMjE1IDIuMTI0NzdMMzQuNDUwMSA3LjY2Mzc5TDM4LjM0NTggMTYuMzQ3OEw0NC40MTcyIDI4LjE3NjlMNDYuMTg4IDMxLjY5NzVMNDcuMTQ5MyAzNC45MzY0TDQ3LjUwMzUgMzUuOTIyMkg0OC4xMTA2VjM1LjM1ODlMNDguNjE2NiAyOC42OTMzTDQ5LjUyNzMgMjAuNTI1Nkw1MC40MzggMTAuMDEwOEw1MC43NDE1IDcuMDUzNTZMNTIuMjA4OCAzLjQ4NjA1TDU1LjE0MzMgMS41NjE0OEw1Ny40MiAyLjY0MTEyTDU5LjI5MiA1LjMxNjc0TDU5LjAzOSA3LjA1MzU2TDU3LjkyNiAxNC4yODI0TDU1Ljc1MDQgMjUuNTk1Mkw1NC4zMzM3IDMzLjE5OTZINTUuMTQzM0w1Ni4xMDQ2IDMyLjIxMzhMNTkuOTQ5NyAyNy4xNDQyTDY2LjM3NTIgMTkuMDcwNEw2OS4yMDg1IDE1Ljg3ODRMNzIuNTQ3OCAxMi4zNTc5TDc0LjY3MjggMTAuNjY4SDc4LjcyMDNMODEuNjU0OCAxNS4wODA0TDgwLjMzOTQgMTkuNjMzN0w3Ni4xOTA2IDI0Ljg5MTFMNzIuNzUwMiAyOS4zNTA0TDY3LjgxNzIgMzUuOTU5NUw2NC43NTYyIDQxLjI3MzRMNjUuMDMwNyA0MS43MTE4TDY1Ljc2ODEgNDEuNjQ4OUw3Ni44OTg5IDM5LjI1NUw4Mi45MTk3IDM4LjE3NTNMOTAuMTA0MSAzNi45NTQ5TDkzLjM0MjIgMzguNDU3TDkzLjY5NjMgNDAuMDA2TDkyLjQzMTUgNDMuMTUxTDg0Ljc0MTEgNDUuMDI4N0w3NS43MzUzIDQ2Ljg1OTRMNjIuMzI0NCA1MC4wMTY0TDYyLjE3NTkgNTAuMTM1OEw2Mi4zNTEyIDUwLjM5NThMNjguMzk5IDUwLjk0MzJMNzAuOTc5NCA1MS4wODRINzcuMzAzN0w4OS4wOTIyIDUxLjk3NTlMOTIuMTc4NSA1My45OTQ0TDkzLjk5OTkgNTYuNDgyMkw5My42OTYzIDU4LjQwNjhMODguOTQwNCA2MC44MDA4TDgyLjU2NTUgNTkuMjk4N0w2Ny42NDAxIDU1LjczMTJMNjIuNTMwMSA1NC40NjM4SDYxLjgyMTdWNTQuODg2Mkw2Ni4wNzE3IDU5LjA2NEw3My45MTM5IDY2LjEwNTFMODMuNjc4NiA3NS4yMTE2TDg0LjE4NDUgNzcuNDY0OEw4Mi45MTk3IDc5LjI0ODVMODEuNjA0MiA3OS4wNjA4TDczLjAwMzIgNzIuNTgyOUw2OS42NjM5IDY5LjY3MjZMNjIuMTc1OSA2My4zMzU2SDYxLjY3VjYzLjk5MjhMNjMuMzkwMiA2Ni41Mjc2TDcyLjU0NzggODAuMjgxMkw3My4wMDMyIDg0LjUwNTlMNzIuMzQ1NCA4NS44NjcyTDY5Ljk2NzUgODYuNzEyMUw2Ny4zODcxIDg2LjI0MjdMNjEuOTczNSA3OC42ODUyTDU2LjQ1ODcgNzAuMjM1OUw1Mi4wMDY0IDYyLjYzMTVMNTEuNDY4NyA2Mi45NzFMNDguODE4OSA5MS4yNjU0TDQ3LjYwNDcgOTIuNzIwNkw0NC43NzE0IDkzLjgwMDJMNDIuMzkzNCA5Mi4wMTY0TDQxLjEyODYgODkuMTA2MUw0Mi4zOTM0IDgzLjMzMjRMNDMuOTExMyA3NS44MjE5TDQ1LjEyNTUgNjkuODYwNEw0Ni4yMzg2IDYyLjQ0MzdMNDYuOTE4NCA1OS45NjYxTDQ2Ljg1ODMgNTkuODAwM0w0Ni4zMTUzIDU5Ljg5MTZMNDAuNzIzOCA2Ny41NjAzTDMyLjIyMzkgNzkuMDYwOEwyNS40OTQ4IDg2LjI0MjdMMjMuODc1OCA4Ni44OTk5TDIxLjA5MzEgODUuNDQ0N0wyMS4zNDYxIDgyLjg2M0wyMi45MTQ1IDgwLjU2MjlMMzIuMjIzOSA2OC43MzM4TDM3LjgzOTkgNjEuMzY0MUw0MS40NTk0IDU3LjEzMzdMNDEuNDI0MiA1Ni41MjE4TDQxLjIyNDQgNTYuNTA0OEwxNi40ODkgNzIuNjI5OUwxMi4wODczIDczLjE5MzJMMTAuMTY0NyA3MS40MDk0TDEwLjQxNzYgNjguNDk5MUwxMS4zMjgzIDY3LjU2MDNMMTguNzY1NyA2Mi40NDM3WiIgZmlsbD0iI0Q5Nzc1NyIvPgo8L3N2Zz4K',
    grok: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgNy40IDI0IDkuMiIgYXJpYS1oaWRkZW49InRydWUiIGNsYXNzPSJ0ZXh0LXByaW1hcnkgYmxvY2sgaC01IHctYXV0byBzaHJpbmstMCIgd2lkdGg9IjMyIiBoZWlnaHQ9IjEzIiBmb2N1c2FibGU9ImZhbHNlIiBzdHlsZT0iZmlsbDpjdXJyZW50Q29sb3IiPjxwYXRoIGQ9Ik0yNCA3LjQxN0M4Ljg4MiA4LjI4NyAxLjg5IDE0Ljc1LjMyMSAxNi4yOEwwIDE2LjU4M2gyLjc5N0MxMC4zNTYgOS4wMDUgMjEuMjIyIDcuNjYzIDI0IDcuNDE3em0tMTcuMDQ2IDYuMzVjLS40NzIuMzIxLS45NDUuNjgtMS4zOTggMS4wMmwyLjQ1NyAxLjc5NmgyLjc3OHpNMi45NDggMTAuOEguMTg5bDMuMjUgMi4zODFjLjQ3My0uMzIxIDEuMDItLjY2MSAxLjUxMi0uOTQ1WiI+PC9wYXRoPjwvc3ZnPg==',
  };
  // 品牌標(登入卡與安裝 / 換版卡共用):真 LOGO 放中性方底,Grok 白底;沒 LOGO 用名字首字
  const rlcMark = (b, N) => RELOG_LOGO[b] ? `<span class="rlcmark rlclogo${b === 'grok' ? ' rlclogo-light' : ''}"><img src="${RELOG_LOGO[b]}" alt="" width="${RELOG_LOGO_PX[b] || 28}" height="${RELOG_LOGO_PX[b] || 28}"></span>` : `<span class="rlcmark">${esc(String(N).slice(0, 1))}</span>`;
  function relogCard(R, N, msg) {
    const auto = relogAuto(R), key = relogKey(), b = RELOG_BRAND.includes(R.provider) ? R.provider : 'other';
    const said = R.copied === true ? `<div class="rlcok" role="status">✓ 已複製,到登入網頁按 ${key} 貼上</div>` : R.copied === false ? '<div class="rlcno" role="status">沒複製到(瀏覽器不准):請手動選取上面的碼</div>' : '';
    const go = `<a class="rlcgo" href="${esc(R.url)}" target="_blank" rel="noopener noreferrer">打開登入網頁 ↗</a>`;
    // f163c REVIEW-relogcard N1(RFC 8628 5.4):網址帶碼也留一行對照碼,讓人核登入頁上的碼一樣(不給複製,不用貼)
    const code = auto ? `<div class="rlcref">對照碼 <b>${esc(R.dcode)}</b>(登入頁上應該一樣)</div><div class="rlcrow">${go}</div>` : !R.dcode ? `<div class="rlcno">AIB 沒給登入碼,這一輪先取消再按一次</div><div class="rlcrow">${go}</div>` : `<div class="rlcode" aria-label="登入碼">${esc(R.dcode)}</div>${said}<div class="rlcrow"><button type="button" class="rlccp" data-relogcopy="1">${R.copied === true ? '再複製一次' : '複製碼'}</button>${go}</div>`;
    const sub = auto ? '碼已經帶在網址裡,打開登入網頁按授權就好。' : '打開登入網頁,在輸入框貼上這組碼,再按授權。';
    const steps = auto ? ['打開登入網頁,按授權(不用貼碼)', '授權完這裡會自己更新'] : ['到登入網頁的輸入框按 ' + key + ' 貼上碼', '按授權', '授權完這裡會自己更新'];
    return `<div class="rlc rlc-${b}"><div class="rlcband">${rlcMark(b, N)}<b>${esc(N)}</b><span class="rlcstep">共 ${steps.length} 步</span></div><div class="rlcbody"><h4>登入 ${esc(N)}</h4><p class="rlcsub">${sub}</p>${code}<ol class="rlcs">${steps.map((t) => `<li>${esc(t)}</li>`).join('')}</ol></div><div class="rlcfoot"><span>登入存在這台 ${esc(N)} 自己的登入設定(夥伴讀的同一處)</span><span class="rlct">${RELOG_TTL_NA}</span></div></div>${msg}`;
  }

  async function relogCopy() {
    const R = S.relog; if (!R || R.done || !R.dcode || relogAuto(R)) return;
    try { await navigator.clipboard.writeText(R.dcode); R.copied = true; } catch (_) { R.copied = false; }
    render();
  }
  function relogHtml(pv) {
    if (!RELOG_NAME[pv]) return '';
    const R = S.relog && S.relog.provider === pv ? S.relog : null, N = RELOG_NAME[pv];
    const msg = R && R.msg ? `<span class="${R.bad ? 'bad' : 'trs on'}" role="status">${esc(R.msg)}</span>` : '';
    if (!R || R.done) return `<button type="button" class="k g" data-relogin="${esc(pv)}" title="${esc((typeof fpSay === 'function' ? fpSay : (t) => t)('按這台的指紋,在瀏覽器重新登入 ' + N))}" ${S.relog && !S.relog.done ? 'disabled' : ''}>重新登入 ${esc(N)}</button>${msg}`;
    if (!R.url) return `<span class="trs" data-relogwait>${FP_SEE}</span>${msg}`;
    const link = `<a class="k g" href="${esc(R.url)}" target="_blank" rel="noopener noreferrer">打開 ${esc(N)} 登入頁</a>`;
    if (R.mode === 'device') return relogCard(R, N, msg);
    const step = R.sent ? '<span class="trs">碼已送出,等登入完成…</span>' : `<span class="msw"><input class="apq" data-relogcode="1" value="${esc(R.v || '')}" placeholder="登入頁授權後給的碼,貼在這裡" aria-label="登入碼" autocomplete="off" spellcheck="false"><button type="button" class="k g" data-relogsend="1" ${R.busy ? 'disabled' : ''}>送出</button></span>`;
    return link + step + msg;
  }
  async function relogin(pv) {
    S.relog = { provider: pv, left: 120 }; render();
    try {
      const r = await fingerAct('provider_relogin', { provider: pv }, (t, sec) => { if (S.relog && S.relog.provider === pv) { S.relog.left = sec; render(); } });
      const url = r && typeof r.url === 'string' && /^https:\/\/[a-z0-9.-]+\//.test(r.url) ? r.url : '';
      if (!url || !r.login_id) throw Object.assign(new Error('no_url'), { reason: 'no_url' });
      Object.assign(S.relog, { id: String(r.login_id), url, mode: r.mode === 'device' ? 'device' : 'paste', dcode: r.device_code ? String(r.device_code) : '', msg: '' });
      try { window.open(url, '_blank', 'noopener,noreferrer'); } catch (_) { /* 擋了就按上面的連結 */ }
      render(); relogPoll(pv, S.relog.id, Date.now() + 610000);
    } catch (e) { S.relog = { provider: pv, done: true, bad: true, msg: relogWhy(e) }; render(); }
  }
  async function relogPoll(pv, id, until) {
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 3000));
      const R = S.relog; if (!R || R.id !== id) return;
      const r = await act('provider_relogin_state', { login_id: id }).catch(() => null);
      const st = r && String(r.state || '');
      if (st === 'ok') {
        const A = S.add, inAdd = !!(A && (A.needLogin === pv || providers().some((p) => p.key === pv && p.nl)));
        S.relog = { provider: pv, done: true, msg: '已重新登入 ' + RELOG_NAME[pv] + (inAdd ? '' : ';按「重開」讓這一席接著做') };
        S.version = await soft(get('/api/v1/version')).catch(() => S.version); // 08de5 定案 4:capabilities 不快取 ⇒ 重抓一次就是 open=true;開席被 409 擋的那一次自動再送
        // f163c L1 / 3e53b M1:只在設定跟被擋那次一模一樣(同一家、同模式 / 人格 / 倉 …)才自動再送;改過就只重畫,讓人自己按「開」
        if (inAdd && S.add === A) { const snap = A.needLogin === pv ? A.nlSnap : null; A.needLogin = null; A.nlSnap = null; A.msg = ''; A.err = false; if (snap) return doAdd(snap); }
        return render();
      }
      if (st === 'failed' || Date.now() > until) { S.relog = { provider: pv, done: true, bad: true, msg: relogWhy(r ? String(r.reason || 'exit_not_0') : 'timeout') }; return render(); }
    }
  }
  async function relogSend() {
    const R = S.relog; if (!R || !R.id || R.busy) return;
    R.busy = true; render();
    try { await act('provider_relogin_code', { login_id: R.id, code: String(R.v || '').trim() }); R.sent = true; R.msg = ''; R.bad = false; }
    catch (e) { R.msg = relogWhy(e); R.bad = true; }
    R.busy = false; render();
  }
  // ---------- c2f0b IFACE-provider-lifecycle r1(L3,Andy 11-26 02:3x「新安裝、更新、綁定 都是走類似流程」):三種動作同一張卡(版面照登入卡 rlc)----------
  // 讀 GET /api/v1/providers/lifecycle(next 由 aibd 判,頁面照著出卡);做 = act provider_install / provider_update(fingerAct:202 + ticket、本人驗證)、取消 = provider_job_cancel;
  // 登入(next = login)交給既有的登入卡 relogHtml。GET 讀不到(舊 aibd)或 acts 沒有那一項 ⇒ 照舊寫「這一版 AIB 還沒有」,不送。「先不要」只記在頁面記憶體(r1-6 2)。
  const LC_URL = '/api/v1/providers/lifecycle';
  const LC_NAME = { claude: 'Claude', codex: 'Codex', copilot: 'GitHub Copilot', grok: 'Grok' };
  const LC_MAKER = { claude: 'Anthropic', codex: 'OpenAI', copilot: 'GitHub', grok: 'xAI' };
  const LC_W = {
    'zh-TW': {
      tag: { install: '安裝', update: '換版' }, go: { install: '同意並安裝', update: '換版' }, again: '再按一次', no: '先不要', cancel: '取消', na: '這一版 AIB 還沒有',
      installT: (n) => `這台還沒有 ${n},要幫你裝嗎?`,
      installB: (n, v, mb, mk) => `從 ${mk} 官方下載 ${n} ${v}${mb ? `(約 ${mb} MB)` : ''},裝在你自己的帳號裡,不需要系統管理員。裝好就能在這台開 ${n}。`,
      updateT: (n) => `${n} 換成 AIB 試過的版本`,
      updateB: (n, cur, v) => `這台的 ${n} 是 ${cur},AIB 還沒試過這一版。要換成 AIB 試過的 ${v} 嗎?`,
      phase: { download: (p) => `正在下載(${p}%)…`, verify: () => '正在核對是不是官方的檔…', place: () => '正在安裝…', install: () => '正在安裝…' },
      owner: '按下去會跳本人驗證(Touch ID / Windows Hello 或登入密碼),驗證窗上寫的版本跟這裡一樣才按。',
      src: (s) => `來源:${s}`, cancelled: (n) => `已取消,${n} 沒有裝。`, cancelledU: (n) => `已取消,${n} 還是原本的版本。`, whyU: { lifecycle_offline: '連不到官方下載站,這次沒換;還是原本的版本。網路好了再按一次。' }, other: (c) => `沒有完成(${c})`,
      why: { lifecycle_offline: '連不到官方下載站,這次沒裝。網路好了再按一次。', lifecycle_verify_failed: '下載下來的檔跟 AIB 記的不一樣,AIB 沒有執行它,已經刪掉。再按一次;一直這樣就截圖給我們。', lifecycle_failed: (n) => `裝到一半停了,${n} 還不能用。再按一次。`, lifecycle_timeout: (n) => `裝到一半停了,${n} 還不能用。再按一次。`, provider_unsigned: (n) => `這台的 ${n} 不是官方簽的那一支(可能被換過),AIB 不會開它。請重新安裝。`, provider_unmeasured: (n) => `這台的 ${n} 版本 AIB 還沒試過。請換版。`, lifecycle_busy: '已經在進行了,等它跑完。' },
    },
    en: {
      tag: { install: 'Install', update: 'Change version' }, go: { install: 'Agree and install', update: 'Change version' }, again: 'Try again', no: 'Not now', cancel: 'Cancel', na: 'This version of AIB doesn\'t have this yet',
      installT: (n) => `${n} isn't on this computer yet. Install it for you?`,
      installB: (n, v, mb, mk) => `Downloads ${n} ${v}${mb ? ` (about ${mb} MB)` : ''} from ${mk}'s official site and puts it in your own account; no administrator needed. Once it's in, you can open ${n} here.`,
      updateT: (n) => `Switch ${n} to a version AIB has tested`,
      updateB: (n, cur, v) => `${n} on this computer is ${cur}, which AIB hasn't tested. Switch to ${v}, which AIB has tested?`,
      phase: { download: (p) => `Downloading (${p}%)…`, verify: () => 'Checking it is the official file…', place: () => 'Installing…', install: () => 'Installing…' },
      owner: 'Pressing it asks for the owner check (Touch ID / Windows Hello, or your login password). Only approve if the window shows the same version as here.',
      src: (s) => `Source: ${s}`, cancelled: (n) => `Cancelled; ${n} was not installed.`, cancelledU: (n) => `Cancelled; ${n} is still on the version it had.`, whyU: { lifecycle_offline: 'Couldn\'t reach the official download site; nothing was changed and it is still on the version it had. Try again when the network is back.' }, other: (c) => `Not finished (${c})`,
      why: { lifecycle_offline: 'Couldn\'t reach the official download site; nothing was installed. Try again when the network is back.', lifecycle_verify_failed: 'The downloaded file doesn\'t match what AIB has on record. AIB didn\'t run it and has deleted it. Try again; if it keeps happening, send us a screenshot.', lifecycle_failed: (n) => `It stopped partway; ${n} can't be used yet. Try again.`, lifecycle_timeout: (n) => `It stopped partway; ${n} can't be used yet. Try again.`, provider_unsigned: (n) => `${n} on this computer isn't the one signed by its maker (it may have been replaced). AIB won't open it. Please reinstall.`, provider_unmeasured: (n) => `AIB hasn't tested this version of ${n}. Please change the version.`, lifecycle_busy: 'It\'s already running; wait for it to finish.' },
    },
    ja: {
      tag: { install: 'インストール', update: 'バージョン変更' }, go: { install: '同意してインストール', update: 'バージョンを変える' }, again: 'もう一度', no: '今はしない', cancel: 'キャンセル', na: 'この版の AIB にはまだありません',
      installT: (n) => `このパソコンにはまだ ${n} がありません。インストールしますか?`,
      installB: (n, v, mb, mk) => `${mk} の公式サイトから ${n} ${v}${mb ? `(約 ${mb} MB)` : ''}をダウンロードし、あなた自身のアカウントに入れます。管理者は不要です。入れ終われば、このパソコンで ${n} を開けます。`,
      updateT: (n) => `${n} を AIB が試した版に変える`,
      updateB: (n, cur, v) => `このパソコンの ${n} は ${cur} で、AIB はまだこの版を試していません。AIB が試した ${v} に変えますか?`,
      phase: { download: (p) => `ダウンロード中(${p}%)…`, verify: () => '公式のファイルか確認中…', place: () => 'インストール中…', install: () => 'インストール中…' },
      owner: '押すと本人確認(Touch ID / Windows Hello、またはログインパスワード)が出ます。確認画面の版がここと同じときだけ承認してください。',
      src: (s) => `入手元:${s}`, cancelled: (n) => `キャンセルしました。${n} は入れていません。`, cancelledU: (n) => `キャンセルしました。${n} は元のバージョンのままです。`, whyU: { lifecycle_offline: '公式のダウンロードサイトにつながらず、今回は変えていません。元のバージョンのままです。ネットワークが戻ったらもう一度押してください。' }, other: (c) => `完了していません(${c})`,
      why: { lifecycle_offline: '公式のダウンロードサイトにつながらず、今回は入れていません。ネットワークが戻ったらもう一度押してください。', lifecycle_verify_failed: 'ダウンロードしたファイルが AIB の記録と違うため、実行せずに削除しました。もう一度押してください。続く場合はスクリーンショットを送ってください。', lifecycle_failed: (n) => `途中で止まりました。${n} はまだ使えません。もう一度押してください。`, lifecycle_timeout: (n) => `途中で止まりました。${n} はまだ使えません。もう一度押してください。`, provider_unsigned: (n) => `このパソコンの ${n} は公式に署名されたものではありません(差し替えられた可能性があります)。AIB は開きません。入れ直してください。`, provider_unmeasured: (n) => `この版の ${n} は AIB がまだ試していません。バージョンを変えてください。`, lifecycle_busy: 'すでに実行中です。終わるまでお待ちください。' },
    },
    ko: {
      tag: { install: '설치', update: '버전 변경' }, go: { install: '동의하고 설치', update: '버전 바꾸기' }, again: '다시 시도', no: '나중에', cancel: '취소', na: '이 버전의 AIB에는 아직 없습니다',
      installT: (n) => `이 컴퓨터에는 아직 ${n}이(가) 없습니다. 설치할까요?`,
      installB: (n, v, mb, mk) => `${mk} 공식 사이트에서 ${n} ${v}${mb ? `(약 ${mb} MB)` : ''}을(를) 내려받아 내 계정에 설치합니다. 관리자 권한은 필요 없습니다. 설치가 끝나면 이 컴퓨터에서 ${n}을(를) 열 수 있습니다.`,
      updateT: (n) => `${n}을(를) AIB가 시험한 버전으로 바꾸기`,
      updateB: (n, cur, v) => `이 컴퓨터의 ${n}은(는) ${cur}이며, AIB가 아직 시험하지 않은 버전입니다. AIB가 시험한 ${v}(으)로 바꿀까요?`,
      phase: { download: (p) => `내려받는 중(${p}%)…`, verify: () => '공식 파일인지 확인하는 중…', place: () => '설치하는 중…', install: () => '설치하는 중…' },
      owner: '누르면 본인 확인(Touch ID / Windows Hello 또는 로그인 비밀번호) 창이 뜹니다. 창에 표시된 버전이 여기와 같을 때만 승인하세요.',
      src: (s) => `출처: ${s}`, cancelled: (n) => `취소했습니다. ${n}은(는) 설치되지 않았습니다.`, cancelledU: (n) => `취소했습니다. ${n}은(는) 원래 버전 그대로입니다.`, whyU: { lifecycle_offline: '공식 다운로드 사이트에 연결하지 못해 이번에는 바꾸지 않았습니다. 원래 버전 그대로입니다. 네트워크가 돌아오면 다시 눌러 주세요.' }, other: (c) => `완료되지 않았습니다(${c})`,
      why: { lifecycle_offline: '공식 다운로드 사이트에 연결하지 못해 이번에는 설치하지 않았습니다. 네트워크가 돌아오면 다시 눌러 주세요.', lifecycle_verify_failed: '내려받은 파일이 AIB 기록과 달라 실행하지 않고 삭제했습니다. 다시 눌러 주세요. 계속되면 스크린샷을 보내 주세요.', lifecycle_failed: (n) => `도중에 멈췄습니다. ${n}은(는) 아직 쓸 수 없습니다. 다시 눌러 주세요.`, lifecycle_timeout: (n) => `도중에 멈췄습니다. ${n}은(는) 아직 쓸 수 없습니다. 다시 눌러 주세요.`, provider_unsigned: (n) => `이 컴퓨터의 ${n}은(는) 공식 서명된 것이 아닙니다(바뀌었을 수 있습니다). AIB는 열지 않습니다. 다시 설치해 주세요.`, provider_unmeasured: (n) => `이 버전의 ${n}은(는) AIB가 아직 시험하지 않았습니다. 버전을 바꿔 주세요.`, lifecycle_busy: '이미 진행 중입니다. 끝날 때까지 기다려 주세요.' },
    },
  };
  const lcW = () => LC_W[askLang()] || LC_W['zh-TW'];
  const lcEntry = (k) => { const d = S.lc && S.lc.d; return (d && d.providers.find((p) => p && p.name === k)) || null; };
  const lcHas = (t) => !!(S.lc && S.lc.d && Array.isArray(S.lc.d.acts) && S.lc.d.acts.includes(t));
  const lcRun = (p) => !!(p && p.job && p.job.state === 'running');
  const lcAct = (p) => (lcRun(p) ? String(p.job.action || '') : p && (p.next === 'install' || p.next === 'update') ? p.next : '');
  // 開席視窗 / 開席設定卡用:這家要出安裝或換版卡嗎(登入另走 relogHtml,不重複)
  const lcNeeds = (k) => { const a = lcAct(lcEntry(k)); return (a === 'install' || a === 'update') && !(S.lc.no[k] && !lcRun(lcEntry(k))); };
  const lcName = (k) => LC_NAME[k] || RELOG_NAME[k] || String(k);
  function lcWhy(c, n, a) { const W = lcW(), w = (a === 'update' && W.whyU[c]) || W.why[c]; return typeof w === 'function' ? w(n) : w || W.other(c || '?'); }
  function lcCard(k) {
    const p = lcEntry(k); if (!p) return '';
    if (p.next === 'login' && !lcRun(p)) return relogHtml(k) + lcQuotaHtml(k, 'login');
    const a = lcAct(p); if (a !== 'install' && a !== 'update') return '';
    const L = S.lc, W = lcW(), N = lcName(k), j = p.job && typeof p.job === 'object' ? p.job : null, c = p.card && typeof p.card === 'object' ? p.card : {};
    if (L.no[k] && !lcRun(p)) return '';
    const b = RELOG_BRAND.includes(k) ? k : 'other', v = String(c.version || p.measured || ''), sz = Number(c.size), mb = Number.isFinite(sz) && sz > 0 ? Math.round(sz / 1e6) : 0;
    const cur = String((p.installed && p.installed.version) || '?'), mk = LC_MAKER[k] || N;
    const title = a === 'install' ? W.installT(N) : W.updateT(N), body = a === 'install' ? W.installB(N, v, mb, mk) : W.updateB(N, cur, v);
    let state = '', btn = '';
    if (lcRun(p)) {
      const ph = W.phase[j.phase] ? String(j.phase) : 'download', dn = Number(j.done), tt = Number(j.total), pct = tt > 0 && dn >= 0 ? Math.min(100, Math.floor((dn * 100) / tt)) : 0;
      state = `<div class="lcst" role="status">${esc(W.phase[ph](pct))}</div>${ph === 'download' ? `<progress class="lcpg" max="100" value="${pct}" aria-label="${esc(W.phase.download(pct))}"></progress>` : ''}`;
      btn = lcHas('provider_job_cancel') ? `<button type="button" class="k" data-lccancel="${esc(k)}" ${L.busy[k] ? 'disabled' : ''}>${esc(W.cancel)}</button>` : '';
    } else {
      const done = j && (j.state === 'failed' || j.state === 'cancelled') ? (j.state === 'cancelled' ? (String(j.action || a) === 'update' ? W.cancelledU(N) : W.cancelled(N)) : lcWhy(String(j.error || 'lifecycle_failed'), N, String(j.action || a))) : '';
      const m = L.msg[k] ? L.msg[k] : '';
      state = (done ? `<div class="rlcno" role="alert">${esc(done)}</div>` : '') + (m ? `<div class="rlcno" role="alert">${esc(m)}</div>` : '');
      btn = lcHas('provider_' + a) ? `<button type="button" class="rlccp" data-lcgo="${esc(k)}" ${L.busy[k] ? 'disabled' : ''}>${esc(done ? W.again : W.go[a])}</button><button type="button" class="k" data-lcno="${esc(k)}">${esc(W.no)}</button>` : `<span class="rlcno">${esc(W.na)}</span>`;
    }
    const foot = [c.source ? W.src(String(c.source)) : '', lcRun(p) ? '' : W.owner].filter(Boolean).join(' · ');
    return `<div class="rlc rlc-${b} lc"><div class="rlcband">${rlcMark(b, N)}<b>${esc(N)}</b><span class="rlcstep">${esc(W.tag[a])}</span></div><div class="rlcbody"><h4>${esc(title)}</h4><p class="rlcsub">${esc(body)}</p>${state}<div class="rlcrow">${btn}</div>${lcQuotaHtml(k, a)}</div><div class="rlcfoot"><span>${esc(foot)}</span></div></div>`;
  }
  // ---------- §10.6 卡片下方「方案與用量」(c2f0b IFACE-provider-lifecycle §10 / §10.6;Andy 11-26 03:0x「用類似的方格顯示」,ebab8 轉)----------
  // 樣子照座艙右欄「用量」面板:每個時段一列 = 家別圖示 + 時段名 + 10 格方格條(一格 10%,四捨五入到格,同 render_lib.usage_bar)+ 百分比;第二行小字 = 量於多久前 · 重置。
  // 顏色門檻照抄 render_lib.CTX_TIERS(≥95 紅、≥75 橙、≥50 黃、其他藍),到上限整列紅;過期三態(fresh / counting / rolled_over / no_reset)由 aibd 算,頁面照 state 畫。
  // 連結只用這裡的常數(§10.3:不從 aibd 回應或席位輸出拿網址);數字只供參考,頁面不拿來判斷任何事。
  // c2f0b REVIEW-words-2e03fbb2 N3(curl 實查):claude.com/pricing、chatgpt.com/pricing、github.com/features/copilot/plans 都 200;x.ai/grok 回 403(擋程式)未實查、先用
  //(github.com/settings/copilot 要登入、不是方案頁,出自 copilot help billing,不用)
  const LC_PLAN_URL = { claude: 'https://claude.com/pricing', codex: 'https://chatgpt.com/pricing', copilot: 'https://github.com/features/copilot/plans', grok: 'https://x.ai/grok' };
  const LC_Q_TIERS = [[95, 't3'], [75, 't2'], [50, 't1'], [0, 't0']];
  const LC_Q_CELLS = 10;
  const LC_QW = {
    'zh-TW': { win: { '5h': '5 小時', '7d': '每 1 週' }, link: '看方案 ↗', official: '方案與價格以官方頁為準', na: '用量:這一版 AIB 還沒有',
      none: { not_seen_yet: '還沒有用量資料:開一席跑一回合就會有', not_available: '查不到' }, recheck: '重驗', rechecking: '重驗中…', nonew: '沒有更新的資料:開一席跑一回合就會有',
      ago: (m, h, d) => (d ? `量於 ${d} 天前` : h ? `量於 ${h} 小時前` : m ? `量於 ${m} 分鐘前` : '量於剛剛'), reset: (t) => `${t} 重置`, least: (p) => `(至少 ${p}%)`, cd: (t) => `重置倒數 ${t}`,
      rolled: '資訊過期(舊讀數不採用)', nextcd: (t) => `下次重置倒數 ${t}`, noreset: '無重置時刻', limit: (t) => (t ? `已達上限 · ${t}後恢復` : '已達上限'),
      dur: (d, h, m) => (d ? `${d} 天${h ? ` ${h} 小時` : ''}` : h ? `${h} 小時${m ? ` ${m} 分鐘` : ''}` : `${m} 分鐘`), wd: '日一二三四五六', date: (md, w, hm) => `${md}(${w}) ${hm}` },
    en: { win: { '5h': '5 hours', '7d': 'Weekly' }, link: 'See plans ↗', official: 'Plans and prices are as shown on the official page', na: 'Usage: this version of AIB doesn\'t have this yet',
      none: { not_seen_yet: 'No usage data yet: open a partner and run one turn', not_available: 'Not available' }, recheck: 'Recheck', rechecking: 'Rechecking…', nonew: 'No newer data: open a partner and run one turn',
      ago: (m, h, d) => (d ? `Checked ${d} days ago` : h ? `Checked ${h} h ago` : m ? `Checked ${m} min ago` : 'Checked just now'), reset: (t) => `resets ${t}`, least: (p) => ` (at least ${p}%)`, cd: (t) => `resets in ${t}`,
      rolled: 'Out of date (old reading not used)', nextcd: (t) => `next reset in ${t}`, noreset: 'no reset time', limit: (t) => (t ? `Limit reached · back in ${t}` : 'Limit reached'),
      dur: (d, h, m) => (d ? `${d} d${h ? ` ${h} h` : ''}` : h ? `${h} h${m ? ` ${m} min` : ''}` : `${m} min`), wd: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], date: (md, w, hm) => `${w} ${md} ${hm}` },
    ja: { win: { '5h': '5 時間', '7d': '1 週間ごと' }, link: 'プランを見る ↗', official: 'プランと価格は公式ページの内容が優先されます', na: '使用量:この版の AIB にはまだありません',
      none: { not_seen_yet: '使用量のデータはまだありません。パートナーを開いて 1 回動かすと出ます', not_available: '取得できません' }, recheck: '再確認', rechecking: '再確認中…', nonew: '新しいデータはありません。パートナーを開いて 1 回動かすと出ます',
      ago: (m, h, d) => (d ? `${d} 日前に取得` : h ? `${h} 時間前に取得` : m ? `${m} 分前に取得` : 'たった今取得'), reset: (t) => `${t} にリセット`, least: (p) => `(少なくとも ${p}%)`, cd: (t) => `リセットまで ${t}`,
      rolled: '情報が古い(古い値は使いません)', nextcd: (t) => `次のリセットまで ${t}`, noreset: 'リセット時刻なし', limit: (t) => (t ? `上限に達しました · ${t}後に回復` : '上限に達しました'),
      dur: (d, h, m) => (d ? `${d} 日${h ? ` ${h} 時間` : ''}` : h ? `${h} 時間${m ? ` ${m} 分` : ''}` : `${m} 分`), wd: '日月火水木金土', date: (md, w, hm) => `${md}(${w}) ${hm}` },
    ko: { win: { '5h': '5시간', '7d': '1주마다' }, link: '요금제 보기 ↗', official: '요금제와 가격은 공식 페이지 기준입니다', na: '사용량: 이 버전의 AIB에는 아직 없습니다',
      none: { not_seen_yet: '아직 사용량 데이터가 없습니다. 파트너를 열어 한 번 실행하면 생깁니다', not_available: '확인할 수 없음' }, recheck: '다시 확인', rechecking: '다시 확인하는 중…', nonew: '새 데이터가 없습니다. 파트너를 열어 한 번 실행하면 생깁니다',
      ago: (m, h, d) => (d ? `${d}일 전 확인` : h ? `${h}시간 전 확인` : m ? `${m}분 전 확인` : '방금 확인'), reset: (t) => `${t} 초기화`, least: (p) => ` (최소 ${p}%)`, cd: (t) => `초기화까지 ${t}`,
      rolled: '정보가 오래됨(이전 값은 쓰지 않음)', nextcd: (t) => `다음 초기화까지 ${t}`, noreset: '초기화 시각 없음', limit: (t) => (t ? `한도 도달 · ${t} 후 회복` : '한도 도달'),
      dur: (d, h, m) => (d ? `${d}일${h ? ` ${h}시간` : ''}` : h ? `${h}시간${m ? ` ${m}분` : ''}` : `${m}분`), wd: '일월화수목금토', date: (md, w, hm) => `${md}(${w}) ${hm}` },
  };
  const lcQW = () => LC_QW[askLang()] || LC_QW['zh-TW'];
  const lcQNow = () => Math.floor(Date.now() / 1000);
  const lcQSec = (x) => { const n = Number(x); return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0; };
  function lcQDur(sec) { const W = lcQW(), t = Math.max(60, Math.floor(sec)), d = Math.floor(t / 86400), h = Math.floor((t % 86400) / 3600), m = Math.floor((t % 3600) / 60); return W.dur(d, h, d ? 0 : m); }
  function lcQAgo(at) { const a = Math.max(0, lcQNow() - at); return lcQW().ago(a >= 60 && a < 3600 ? Math.floor(a / 60) : 0, a >= 3600 && a < 86400 ? Math.floor(a / 3600) : 0, a >= 86400 ? Math.floor(a / 86400) : 0); }
  function lcQDate(at) { const W = lcQW(), d = new Date(at * 1000), z = (n) => String(n).padStart(2, '0'); return W.date(z(d.getMonth() + 1) + '/' + z(d.getDate()), W.wd[d.getDay()], z(d.getHours()) + ':' + z(d.getMinutes())); }
  // 3e53b N1:aibd 的字只當鍵查自己的表(constructor 之類不算)
  const lcOwn = (o, k) => (typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : '');
  const lcQTier = (pct) => (LC_Q_TIERS.find(([f]) => pct >= f) || LC_Q_TIERS[LC_Q_TIERS.length - 1])[1];
  function lcQRow(r, mark) {
    const W = lcQW(), now = lcQNow(), st = String(r.state || ''), seen = lcQSec(r.seen_at), rs = lcQSec(r.resets_at), rec = lcQSec(r.recover_at) || rs;
    const pct = Math.max(0, Math.min(100, Math.round(Number(r.used_percent != null ? r.used_percent : r.used_pct) || 0))), nm = lcOwn(W.win, r.window) || String(r.window || '');
    const head = `${mark}<span class="lcqn">${esc(nm)}</span>`;
    if (st === 'rolled_over') return `<div class="lcqr none">${head}<small>${esc([W.rolled, rs > now ? W.nextcd(lcQDur(rs - now)) : ''].filter(Boolean).join(' · '))}</small></div>`;
    const f = Math.max(0, Math.min(LC_Q_CELLS, Math.round(pct / 10))), lim = r.at_limit === true, old = st === 'counting' || st === 'no_reset';
    const grid = `<span class="lcqg" role="img" aria-label="${pct}%">${'<i class="on"></i>'.repeat(f)}${'<i></i>'.repeat(LC_Q_CELLS - f)}</span>`;
    const sub = st === 'counting' ? [seen ? lcQAgo(seen) + W.least(pct) : W.least(pct).trim(), rs > now ? W.cd(lcQDur(rs - now)) : ''] : st === 'no_reset' ? [seen ? lcQAgo(seen) : '', W.noreset] : [seen ? lcQAgo(seen) : '', rs ? W.reset(lcQDate(rs)) : ''];
    return `<div class="lcqr ${lim ? 'lim t3' : lcQTier(pct)}${old ? ' old' : ''}">${head}${grid}<b class="lcqv">${pct}%</b>${lim ? `<em>${esc(W.limit(rec > now ? lcQDur(rec - now) : ''))}</em>` : ''}<small>${esc(sub.filter(Boolean).join(' · '))}</small></div>`;
  }
  // a = install | update | login:安裝還沒登入 ⇒ 只放連結;換版 / 登入照 quota 畫;quota 欄不存在(舊 aibd)⇒ 連結 +「這一版 AIB 還沒有」
  function lcQuotaHtml(k, a) {
    const p = lcEntry(k); if (!p) return '';
    const W = lcQW(), N = lcName(k), b = RELOG_BRAND.includes(k) ? k : 'other', url = Object.prototype.hasOwnProperty.call(LC_PLAN_URL, k) ? LC_PLAN_URL[k] : '', L = S.lc;
    const ln = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(W.link)}</a>` : '';
    if (a === 'install') return ln ? `<div class="lcq"><div class="lcqp">${ln} · ${esc(W.official)}</div></div>` : '';
    if (!Object.prototype.hasOwnProperty.call(p, 'quota')) return `<div class="lcq"><div class="lcqp">${ln}</div><div class="lcqx">${esc(W.na)}</div></div>`;
    const q = p.quota && typeof p.quota === 'object' ? p.quota : null, R = q && Array.isArray(q.rows) ? q.rows.filter((r) => r && typeof r === 'object').slice(0, 4) : [];
    const plan = q && typeof q.plan === 'string' && /^[a-z0-9_]{1,24}$/.test(q.plan) ? q.plan.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '';
    const mark = rlcMark(b, N), why = String((q && q.reason) || p.quota_reason || 'not_seen_yet');
    const rows = R.length ? R.map((r) => lcQRow(r, mark)).join('') : `<div class="lcqr none">${mark}<span class="lcqn">${esc(W.win['7d'])}</span><small>${esc(lcOwn(W.none, why) || W.none.not_available)}</small></div>`;
    const stale = !R.length || R.some((r) => r.state !== 'fresh'), can = q && q.can_recheck === true && (k === 'claude' || k === 'codex') && lcHas('provider_quota_recheck');
    const rb = stale && can ? `<button type="button" class="k lcqrb" data-lcqr="${esc(k)}" ${L.qbusy && L.qbusy[k] ? 'disabled' : ''}>${esc(L.qbusy && L.qbusy[k] ? W.rechecking : W.recheck)}</button>` : '';
    const msg = L.qmsg && L.qmsg[k] ? `<div class="lcqx" role="status">${esc(L.qmsg[k])}</div>` : '';
    return `<div class="lcq"><div class="lcqp">${plan ? `<b>${esc(plan)}</b>${ln ? ' · ' : ''}` : ''}${ln}</div>${rows}${rb || msg ? `<div class="lcqf">${rb}${msg}</div>` : ''}</div>`;
  }
  // 重驗 = aibd 重讀它手上最新的席位事件(不跳窗、不開 CLI、不花額度);seen_at 沒變 ⇒ 照實說沒有新資料
  async function lcQRecheck(k) {
    const L = S.lc, p = lcEntry(k); if (!L || !p || (k !== 'claude' && k !== 'codex') || !lcHas('provider_quota_recheck')) return;
    L.qbusy = L.qbusy || {}; L.qmsg = L.qmsg || {}; if (L.qbusy[k]) return;
    const seen = (x) => Math.max(0, ...((x && x.quota && Array.isArray(x.quota.rows) ? x.quota.rows : []).map((r) => lcQSec(r && r.seen_at))));
    const before = seen(p); L.qbusy[k] = true; L.qmsg[k] = ''; render();
    try { await act('provider_quota_recheck', { provider: k }); await lcLoad(); L.qmsg[k] = seen(lcEntry(k)) > before ? '' : lcQW().nonew; } catch (e) { L.qmsg[k] = lcWhy(rgCode(e), lcName(k)); }
    L.qbusy[k] = false; render();
  }
  async function lcLoad() {
    const L = S.lc = S.lc || { d: null, no: {}, msg: {}, busy: {}, run: {} };
    const r = await soft(get(LC_URL)).catch(() => null);
    L.d = r && typeof r === 'object' && Array.isArray(r.providers) ? r : null;
    const now = {}; (L.d ? L.d.providers : []).forEach((p) => { if (lcRun(p)) now[p.name] = 1; });
    const ended = Object.keys(L.run).filter((k) => !now[k]); L.run = now;
    // 工作結束 ⇒ capabilities 不快取,重抓 version(08de5 定案 4);開席設定卡開著就重讀;照 next 接下一張卡(登入卡 / 開席設定卡)
    if (ended.length) { S.version = await soft(get('/api/v1/version')).catch(() => S.version); if (S.sp && typeof spLoad === 'function') spLoad(); }
    render();
    if (Object.keys(now).length && !L.tm) L.tm = setTimeout(() => { L.tm = 0; lcLoad(); }, 1000);
    return L.d;
  }
  async function lcGo(k) {
    const L = S.lc, p = lcEntry(k), a = p && (p.next === 'install' || p.next === 'update') ? p.next : '';
    if (!L || !a || L.busy[k] || lcRun(p) || !lcHas('provider_' + a)) return;
    L.busy[k] = true; L.msg[k] = ''; L.no[k] = false; render();
    // body 只有 {type, provider}(r0 §2.2 白名單);本人驗證與回送 ticket 由 fingerAct 做,每次都是新的一窗
    try { await (a === 'install' ? fingerAct('provider_install', { provider: k }) : fingerAct('provider_update', { provider: k })); }
    catch (e) { L.msg[k] = lcWhy(rgCode(e), lcName(k)); }
    L.busy[k] = false; await lcLoad();
  }
  async function lcCancel(k) {
    const L = S.lc; if (!L || L.busy[k] || !lcHas('provider_job_cancel')) return;
    L.busy[k] = true; render();
    try { await act('provider_job_cancel', { provider: k }); } catch (e) { L.msg[k] = lcWhy(rgCode(e), lcName(k)); }
    L.busy[k] = false; await lcLoad();
  }
  function lcClick(x) {
    const g = x.closest('[data-lcgo]'); if (g) { lcGo(g.dataset.lcgo); return true; }
    const c = x.closest('[data-lccancel]'); if (c) { lcCancel(c.dataset.lccancel); return true; }
    const qr = x.closest('[data-lcqr]'); if (qr) { lcQRecheck(qr.dataset.lcqr); return true; }
    const n = x.closest('[data-lcno]'); if (n && S.lc) { S.lc.no[n.dataset.lcno] = true; render(); return true; }
    return false;
  }
  // ---------- lifecycle 卡到這裡 ----------
  const trustWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return (typeof fpSay === 'function' ? fpSay : (t) => t)(TRUST_WHY[rs] || errText(e)); };
  async function seatTrust(id, on) {
    S.trustMsg = null;
    try {
      if (on) {
        S.trustF = { seat: id, left: 120 }; render();
        const r = await fingerAct('seat_trust', { seat_id: id }, (t, sec) => { if (S.trustF && S.trustF.seat === id) { S.trustF.left = sec; render(); } });
        S.trust[id] = Number(r && r.trusted_until) || 0;
        S.trustMsg = { seat: id, text: (r && r.already ? '本來就是受信任' : '已升成受信任') + (S.trust[id] ? ',到 ' + hhmm(S.trust[id] * 1000) : '') };
      } else {
        const r = await act('seat_untrust', { seat_id: id });
        S.trust[id] = 0;
        S.trustMsg = { seat: id, text: r && r.was_trusted === false ? '這一席本來就不是受信任' : '已收回受信任' };
      }
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code);
      if (rs === 'trusted_seats_off' || rs === 'page_only' || rs === 'unknown_act_type') S.trustOff = true;
      S.trustMsg = { seat: id, bad: true, text: trustWhy(e) };
    }
    S.trustF = null; render();
  }
  // G5 信橋(7ebca DESIGN-g5-bridge r1 §1;5d33a 63af3c25):把終端機舊席的信改送到這一席,要按指紋;搬回不用指紋
  const MOVE_WHY = {
    unknown_act_type: '這台的 AIB 還沒有搬舊席(G5 信橋才有 seat_move)', page_only: '只有這台內建的頁面能搬舊席',
    body: '舊席碼是 5 碼小寫英數(0–9、a–f)', seat_not_found: '找不到這一席', seat_archived: '這一席已封存',
    old_seat_not_found: '這台的舊信帳裡沒有這個舊席碼', old_code_moved: '這個舊席已經搬進另一席,先在那一席搬回',
    seat_stands_for_another: '這一席已經收另一個舊席的信,先搬回', seat_move_changed: '按指紋之後狀況變了,請重按一次',
    owner_check_unavailable: '這台問不到指紋,沒有搬', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有搬',
    finger_denied: '指紋取消了,沒有搬', finger_expired: FINGER_GONE + ';沒有搬,要的話再按一次', finger_timeout: '單號過期前沒按指紋,' + FINGER_GONE + ';沒有搬',
    store: 'AIB 寫不進搬席紀錄,請稍後再試',
  };
  const MOVE_CODE = /^[0-9a-f]{5}$/;
  function moveHtml(s) {
    if (S.moveOff) return '';
    const id = s.seat_id, F = S.moveF && S.moveF.seat === id ? S.moveF : null;
    if (F) return `<span class="trs" data-movewait>搬 @${esc(F.code)}:${FP_SEE}</span>`;
    const m = S.moved[id];
    if (m) return `<span class="trs on">收 @${esc(m)} 的信</span><button type="button" class="k" data-unmove="${esc(id)}" ${S.sending ? 'disabled' : ''} title="${(typeof fpSay === 'function' ? fpSay : (t) => t)('不用指紋;搬回後舊席收信要用 1.x 指令重開')}">搬回</button>`;
    if (S.moveAsk === id) return `<input class="mvc" data-movecode maxlength="5" placeholder="舊席碼" autocomplete="off" spellcheck="false" value="${esc(S.moveCode || '')}"><button type="button" class="k" data-movego="${esc(id)}" ${S.moveF ? 'disabled' : ''}>${(typeof fpSay === 'function' ? fpSay : (t) => t)('按指紋搬')}</button><button type="button" class="k" data-moveno>取消</button>`;
    return `<button type="button" class="k" data-move="${esc(id)}" title="${(typeof fpSay === 'function' ? fpSay : (t) => t)('把一個舊式席的信改送到這一席(要按這台的指紋)')}" ${S.moveF ? 'disabled' : ''}>搬進舊席</button>`;
  }
  const moveWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return (typeof fpSay === 'function' ? fpSay : (t) => t)(MOVE_WHY[rs] || errText(e)); };
  // 0eed7 VERIFY-integ3 r1 M4:搬進成功那句列出這席帶著的開著的派工信(第一行「派工:」),寫「這些是你的」,別席的不列。
  // 來源:aibd 回 letters [{id, first_line}] 就用它;沒有 ⇒ 重讀待審框(GET /api/v1/inbox),挑收件席 = 這席、沒結、沒被取代、
  // 第一行「派工:」、箭頭後指名的席(有寫)是舊席碼或這席;列上有 dm_to / forwarded_to 時再核是不是從這個舊席碼轉來的
  const MOVE_DISP = /^\s*派工\s*[:：]/;
  async function moveMine(id, code, r) {
    // 5d33a r9 L2 / N1:頁面不判派工權;只有 aibd 在 letters 上標 dispatch:true 的才寫「這些是你的」,其餘只列「帶著的派工信」並寫寄件人、標未核
    const pick = (L) => L.map((m) => ({ id: String(m.id || m.letter_id || ''), line: String(m.first_line || '').trim(), from: cinbId(m.sender || m.from_seat || ''), ok: m.dispatch === true, ask: m.asks_work })).filter((m) => m.id && (m.ask === true || m.ok || (m.ask === undefined && MOVE_DISP.test(m.line))));
    // 7ebca 5d6e3df0da:letters 多 asks_work(第一行寫著「派工:」,只是點名);信橋轉來的 dispatch 一律 false ⇒ 落「寫著派工、寄件權還沒核」
    if (r && Array.isArray(r.letters)) return { src: 'aibd', L: pick(r.letters) };
    const ib = await soft(get('/api/v1/inbox?scope=all')).catch(() => null), R = ib && (ib.rows || ib.items);
    if (!Array.isArray(R)) return { src: 'none', L: [] };
    const me = [id, seatName(seatById(id) || { seat_id: id })];
    return { src: 'inbox', L: pick(R.filter((m) => {
      if (cinbId(m.to_seat) !== id || m.done_at || m.superseded_by || m.blocked) return false;
      if (m.dm_to && String(m.dm_to) !== code && !m.forwarded_to) return false;
      if (m.forwarded_to && cinbId(m.forwarded_to) !== id) return false;
      const t = /→\s*@?([0-9a-z][0-9a-z_-]*)/i.exec(String(m.first_line || ''));
      return !t || t[1] === code || me.some((x) => String(x).toLowerCase() === t[1].toLowerCase());
    })) };
  }
  function moveMineHtml(M) {
    if (!M.mine) return '';
    const { src, L } = M.mine;
    if (src === 'none') return M.n ? '<div class="mvl"><small>這台 AIB 還沒給信的清單,看不到轉來的是哪幾封;到待審區看</small></div>' : '';
    if (!L.length) return '<div class="mvl"><small>沒有帶著派工信(第一行「派工:」的)</small></div>';
    // 7ebca 59509a1b64:sender 是 sender_label(例「AIB2統籌@519d0」)、沒有才是席號 ⇒ 認得的席寫 @名字,帶 @ 的標籤照原樣但標「(1.x 自稱)」(5d33a r11 N2:沒認證,別讓人以為是統籌寄的)
    const who = (m) => (!m.from ? '寄件人不明' : seatById(m.from) ? '@' + seatName(seatById(m.from)) : m.from.includes('@') ? m.from.slice(0, 40) + '(1.x 自稱)' : '@' + m.from.slice(0, 40));
    const row = (m) => '<div class="mvi"><code>' + esc(m.id.slice(0, 14)) + '</code> <b>' + esc(who(m)) + '</b> ' + esc(m.line.slice(0, 90)) + '</div>';
    const part = (A, head) => (A.length ? '<b>' + head + '(' + A.length + ' 封)</b>' + A.slice(0, 8).map(row).join('') + (A.length > 8 ? '<small>還有 ' + (A.length - 8) + ' 封,到待審區看全部</small>' : '') : '');
    const yes = L.filter((m) => m.ok), un = L.filter((m) => !m.ok);
    return '<div class="mvl">' + part(yes, '這些是你的') + part(un, '寫著派工、寄件權還沒核(先看內容再決定)') + '</div>';
  }
  async function seatMove(id, code) {
    S.moveMsg = null;
    if (!MOVE_CODE.test(code)) { S.moveMsg = { seat: id, bad: true, text: MOVE_WHY.body }; return render(); }
    S.moveAsk = null; S.moveF = { seat: id, code, left: 120 }; render();
    try {
      const r = await fingerAct('seat_move', { old_code: code, seat_id: id }, (t, sec) => { if (S.moveF && S.moveF.seat === id) { S.moveF.left = sec; render(); } });
      S.moved[id] = code;
      const n = Number(r && r.forwarded) | 0;
      S.moveMsg = { seat: id, n, text: (r && r.already ? '本來就收 @' + code + ' 的信' : '已搬:寄給 @' + code + ' 的信改送這一席') + (n ? ',舊帳裡沒結的 ' + n + ' 封一併轉過來' : '') + ';舊席那邊照步驟停收' };
      S.moveF = null; render();
      S.moveMsg.mine = await moveMine(id, code, r); render(); // 5d33a r9 L1:清單到了就畫,不等下一個事件
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code);
      if (rs === 'page_only' || rs === 'unknown_act_type') S.moveOff = true;
      S.moveMsg = { seat: id, bad: true, text: moveWhy(e) };
    }
    S.moveF = null; render();
  }
  async function seatUnmove(id) {
    const code = S.moved[id]; S.moveMsg = null;
    if (!code) return render();
    try {
      await act('seat_unmove', { old_code: code });
      delete S.moved[id];
      S.moveMsg = { seat: id, text: '已搬回:寄給 @' + code + ' 的新信回到舊信帳;舊席收信要用 1.x 指令重開' };
    } catch (e) { S.moveMsg = { seat: id, bad: true, text: moveWhy(e) }; }
    render();
  }
  // ---------- 「等你驗證」卡(fpbatch 頁面稿 r2 第 ① 項;Andy 11-23 02:0x 看過 verify-card 示意回「ok」;2e2cd ORDER-s1 補段)----------
  // 每一個要本人驗證的動作都用這一張(fingerAct 一律叫它,S1 開夥伴也是)。卡只顯示、不能核准:真正被按的是系統畫的指紋視窗。
  // 字:aibd 回了 words / shown 就照畫;還沒回(現在的單筆票)才用下面這張表,只拿送出去的 body 組字,不放權杖之類的秘密。
  // 卡自己每秒畫、不走 render(不打斷打字);事件視窗(dialog)開著就掛進那個視窗,不然在 body。
  const FP_MODE = { safe: '討論研究', work: '沙盒開發', dgrs: '完全權限' };
  const fpAt = (id) => { const x = id && seatById(id); return '@' + (x ? seatShort(x) : String(id || '').replace(/^seat_/, '').slice(0, 5)); };
  // 字串表(2e2cd 02:07:交稿時附給 7ebca 對系統視窗的字):[動作, [[欄, 值, 是不是「這次多開」]…]]
  const fpWhen = (v) => { const t = typeof v === 'number' ? v * (v < 1e12 ? 1000 : 1) : Date.parse(String(v || '')); return Number.isFinite(t) ? new Date(t).toLocaleString('zh-TW', { hour12: false }) : v ? String(v) : '(沒帶)'; };
  const FP_WHAT = {
    // 6770f IFACE-merge r1 §3 / §5:合併 / 還原要本人驗證;卡上印跟系統窗一樣的對照碼(window_sha12)
    merge_run: (b, first) => { const M = typeof S !== 'undefined' && S.add ? S.add.mx : null, P = (M && M.pv) || {}, code = String((first && first.window_sha12) || P.window_sha12 || ''); return ['合併' + (typeof MG_KN !== 'undefined' && M ? MG_KN[M.kind] || '' : ''), [['步驟', String((Array.isArray(P.steps) ? P.steps.length : 0)) + ' 步'], ['對照碼', code, true]]]; },
    merge_undo: (b) => ['整筆還原合併', [['批次', String(b.batch_id || ''), true]]],
    // W-10(c2f0b IFACE r2 第 3 點):窗字「允許 AIB 在這台開:Claude(完整路徑)」,兩家用、分隔;頁面抄剛讀到的 native,以系統窗為準
    session_policy_init: () => ['允許 AIB 在這台開', [['程式', typeof spFound === 'function' && typeof S !== 'undefined' ? spFound(S.sp && S.sp.d).filter((f) => f.kind === 'native').map((f) => spName(f.name) + '(' + f.path + ')').join('、') : '', true], ['', '名單與路徑以系統窗為準(AIB 跳窗當下會再找一次)']]],
    // 3e53b IFACE-netcard:網路例外卡要驗證的選項(15 分鐘 / 1 小時 / 到這一位關掉 / 一直允許)
    // c2f0b S2-2:窗上(aibd 寫)是人格名 · +N −M 行 · 對照碼 diff12;卡上照同一份提案印
    persona_propose_approve: (b) => { const p = ppCard(b.request_id) || {}; return ['把提案寫回人格卡「' + String(p.persona_name || p.persona_uid || '') + '」', [['夥伴', p.seat_id ? fpAt(p.seat_id) : ''], ['改動', '+' + (Number(p.added) || 0) + ' −' + (Number(p.removed) || 0) + ' 行', true], ['對照碼', String(p.diff12 || String(b.diff_sha || '').slice(0, 12)), true]]]; },
    net_card_answer: (b) => { const c = netCard(b.request_id) || {}, o = (c.choices || []).find((x) => x && x.id === b.choice) || {}; return ['讓 ' + (c.seat_id ? fpAt(c.seat_id) : '夥伴') + ' 連 ' + String(c.dest_name || c.dest_ip || '內網'), [['夥伴', c.seat_id ? fpAt(c.seat_id) : ''], ['目的地', String(c.dest_ip || '') + ':' + String(c.dest_port || ''), true], ['開放', o.id ? netAlwaysWords(o, c) : String(b.choice), true]].concat(b.choice === 'always' ? [['影響', netAlwaysWho(c), true]] : [])]; },
    // 08de5 A11(734e430144):完全權限的席重開 / 換模型 / 清空也要本人驗證
    seat_restart: (b) => ['重開 ' + fpAt(b.seat_id), [['夥伴', fpAt(b.seat_id), true], ['之後', '接原本的對話,照原本的權限繼續跑']]],
    seat_model_set: (b) => ['把 ' + fpAt(b.seat_id) + ' 換成 ' + b.model, [['夥伴', fpAt(b.seat_id)], ['模型', b.model, true], ['之後', '下一個回合起用新模型']]],
    seat_clear: (b) => ['清空 ' + fpAt(b.seat_id) + ' 開新的一段', [['夥伴', fpAt(b.seat_id), true], ['交接單', fpWhen(b.handoff_at)], ['之後', '舊對話不帶,新的一段先讀交接單']]],
    repo_register: (b) => ['登記程式倉 ' + b.name, [['倉', b.path], ['之後', '開夥伴只用名字,不再填路徑']]],
    seat_open: (b) => ['開一位' + (FP_MODE[b.mode] || b.mode) + '的夥伴', [['倉', b.repo || '系統新開的資料夾'], ['權限', FP_MODE[b.mode] === '完全權限' ? '完全權限:跟你本人一樣' : FP_MODE[b.mode] || b.mode, b.mode === 'dgrs'], ['人格', ((personas().find(([u]) => u === b.persona_uid) || [])[1]) || b.persona_uid], ['可管的主機', Array.isArray(b.hosts) && b.hosts.length ? b.hosts.map((id) => ((S.hs && S.hs.list) || []).find((h) => h.id === id) || { name: id }).map((h) => h.name).join('、') + ';代連 = 交出 shell' : '', true], ['權限模板', b.tpl_uid ? ((ptOf(b.tpl_uid) || {}).name || b.tpl_uid) + ':' + (ptOf(b.tpl_uid) ? ptWords(ptItems(ptOf(b.tpl_uid).items)) : '') : '', true]]],
    seat_send_back: (b, f) => ['把 ' + fpAt(b.seat_id) + ' 的成果送回倉', [['夥伴', fpAt(b.seat_id)], ['頭', String((f && f.plan && f.plan.head) || '').slice(0, 10) || '(AIB 還沒給)'], ['目標分支', String((f && f.plan && (f.plan.branch || f.plan.target)) || '(AIB 還沒給)')]]],
    repo_push: (b) => ['推分支到遠端', [['倉', b.repo], ['分支', b.branch, true], ['範圍', '只推 web/ 開頭,不動其他分支']]],
    pr_open: (b) => ['開草稿 PR', [['倉', b.repo], ['分支', b.branch + ' → main'], ['標題', b.title, true]]],
    repo_gh_token: (b) => ['存 GitHub 權杖', [['倉', b.repo], ['用途', '推分支與開 PR', true]]],
    provider_relogin: (b) => ['重新登入 AI', [['品牌', b.provider]]],
    seat_trust: (b) => [fpAt(b.seat_id) + ' 設為受信任', [['對象', fpAt(b.seat_id)], ['這次多開', '受信任 12 小時', true], ['收回', '隨時,不用驗證']]],
    seat_move: (b) => ['把舊席 @' + b.old_code + ' 搬進 ' + fpAt(b.seat_id), [['舊席', '@' + b.old_code], ['新夥伴', fpAt(b.seat_id)], ['之後', '寄給舊席的信改送這一位', true]]],
    test_host_add: (b) => ['加一台測試機 ' + b.name, [['機器', b.name], ['之後', '收據可以排到這台跑', true]]],
    test_host_remove: (b) => ['收回測試機 ' + b.name, [['機器', b.name]]],
    drive_toggle: (b) => ['打開自動派工', [['理由', b.why], ['之後', 'AIB 會自己把下一件派給夥伴', true]]],
    seat_tick_set: (b) => ['設定時送字給 ' + fpAt(b.seat_id), [['對象', fpAt(b.seat_id)], ['每', b.every_min + ' 分鐘'], ['送', b.file ? '檔案 ' + b.file : fpClip(String(b.text || ''), 80), true]]],
    seat_tick_pause: () => ['恢復自動送字', [['範圍', '全部夥伴的定時送字', true]]],
    // S2(fc7f9):加主機;開 host 席那張在 seat_open 多一行主機
    // 3e53b DESIGN-hostmode 13.3 / 13.6 / 13.7:{from: trusted, name} = 把這台的測試機交給夥伴管(只收 hosts 名)
    managed_host_add: (b) => b.from === 'trusted' ? ['把測試機「' + b.name + '」交給夥伴管', [['來源', '這台的測試機清單'], ['之後', '可以指派給連主機的夥伴;代連 = 交出 shell'], ['收據', TH_EVID, true], ['指紋', '機器與金鑰指紋以系統視窗為準']]] : ['加一台可管的主機「' + b.name + '」', [['位址', String(b.addr) + ':' + b.port], ['使用者', b.user], ['金鑰檔', b.key_path], ['指紋', '機器與金鑰指紋以系統視窗為準'], ['之後', '可以指派給連主機的夥伴;代連 = 交出 shell', true]]],
    // S4(fc7f9):人格 / 大腦 / 角色的改與封存(act 名照 RG_ACTS,08de5 §1 定了再對)
    persona_set: (b) => { const u = b.persona_uid || b.uid, o = S.rg && S.rg.uid === u && S.rg.orig, pn = ((personas().find(([x]) => x === u) || [])[1]) || ''; const L = (t) => Array.from(String(t || '')).length, ch = o ? [['name', '名稱'], ...RG_CARD].filter(([k]) => b[k] !== undefined && String(b[k]) !== String(o[k] || '')).map(([k, n]) => k === 'name' ? n : n + '(原 ' + L(o[k]) + ' 字 → ' + L(b[k]) + ' 字)').join('、') : ''; return ['修改人格「' + (b.name || pn || u) + '」', [['人格', pn && pn !== b.name ? pn + '(' + u + ')' : u, true], ['改了', o ? ch || '沒有改動' : ''], ['新名稱', b.name], ['說明', b.what]]]; },
    persona_archive: (b) => ['封存人格', [['人格', b.uid, true], ['之後', '不再出現在清單,編號保留、可以還原']]],
    persona_restore: (b) => ['還原人格', [['人格', b.uid, true]]],
    brain_set: (b) => ['改第二大腦名稱「' + b.name + '」', [['第二大腦', b.uid], ['新名稱', b.name, true]]],
    brain_archive: (b) => ['從清單移除第二大腦', [['第二大腦', b.uid, true], ['筆記檔案', '不會刪']]],
    brain_restore: (b) => ['把第二大腦加回清單', [['第二大腦', b.uid, true]]],
    role_set: (b) => ['改角色「' + b.name + '」', [['角色', b.uid], ['新名稱', b.name, true], ['說明', b.what]]],
    role_archive: (b) => ['刪除自訂角色', [['角色', b.uid, true], ['之後', '掛著這個角色的項目改成不掛']]],
    role_restore: (b) => ['還原自訂角色', [['角色', b.uid, true]]],
    // S3 權限模板(fc7f9):aibd 7a 的窗字是「權限模板「名」底 X:…;這次多開 …」,這裡同順序
    perm_template_add: (b) => ['新增權限模板「' + b.name + '」', [['底', FP_MODE[b.base] || b.base, b.base === 'dgrs'], ['開的項目', ptWords(b.items) || '什麼都不開', true]]],
    perm_template_set: (b) => { const o = ptOf(b.tpl_uid), w = o ? ptItems(o.items) : {}; return ['改權限模板「' + b.name + '」', [['底', FP_MODE[b.base] || b.base], ['開的項目', ptWords(b.items) || '什麼都不開'], ['這次多開', PT_ITEMS.filter(([k]) => b.items && b.items[k] && !w[k]).map(([, nm]) => nm).join('、') + (o && o.base !== b.base ? (PT_ITEMS.some(([k]) => b.items && b.items[k] && !w[k]) ? '、' : '') + '底 ' + (FP_MODE[o.base] || o.base) + ' → ' + (FP_MODE[b.base] || b.base) : ''), true], ['已開的夥伴', '不會跟著變,要重新套用']]]; },
    perm_template_apply: (b) => { const o = ptOf(b.tpl_uid); return [fpAt(b.seat_id) + ' 套用權限模板「' + (o ? o.name : b.tpl_uid) + '」', [['對象', fpAt(b.seat_id)], ['底', o ? FP_MODE[o.base] || o.base : ''], ['開的項目', o ? ptWords(ptItems(o.items)) : '(AIB 會列)', true], ['生效', '重開這一位之後']]]; },
  };
  // 8b09a REVIEW-fpcard N1:卡上只放前 N 字時說共幾字,讓人知道沒看完
  function fpClip(t, n) { const a = Array.from(t); return a.length > n ? a.slice(0, n).join('') + '…(共 ' + a.length + ' 字)' : t; }
  const FPC = { cur: null, hide: 0 };
  const FP_C = 157; // 2πr,r = 25
  // 8b09a REVIEW-fpcard M1:對照碼 = aibd owner_ok::pair_code = SHA-256(ticket) 前 4 碼大寫(單號 ot_1 ⇒ 4554,跟 aibd 測試同一張票);
  // aibd 202 有給 code / pair_code 就用它的,沒有才在頁面算(127.0.0.1 是安全來源,crypto.subtle 可用)
  const FP_CODE_RE = /^[0-9A-F]{4}$/;
  async function fpPairCode(ticket) {
    const c = globalThis.crypto && globalThis.crypto.subtle; if (!c || !ticket) return '';
    const h = new Uint8Array(await c.digest('SHA-256', new TextEncoder().encode(String(ticket))));
    return Array.from(h.slice(0, 2), (b) => b.toString(16).padStart(2, '0')).join('').toUpperCase();
  }
  function fpcOpen(type, body, first, until) {
    const W = Array.isArray(first && first.shown) ? [String(first.words || type), first.shown.map((x) => [String((x && x.k) || ''), String((x && x.v) || ''), !!(x && x.up)])] : (FP_WHAT[type] ? FP_WHAT[type](body || {}, first) : [type, []]);
    clearTimeout(FPC.hide);
    const given = String((first && (first.code || first.pair_code)) || '').toUpperCase();
    const o = FPC.cur = { what: W[0], lines: W[1], until, total: Math.max(1, Math.ceil((until - Date.now()) / 1000)), st: '', why: '', abort: false, code: FP_CODE_RE.test(given) ? given : '', codeWait: false };
    if (!o.code && first && first.ticket) { o.codeWait = true; o.codeDone = fpPairCode(first.ticket).catch(() => '').then((c) => { o.code = FP_CODE_RE.test(c) ? c : ''; o.codeWait = false; if (FPC.cur === o) fpcPaint(); }); }
    fpcPaint(); return o;
  }
  function fpcDone(o, ok, why) {
    if (!o || FPC.cur !== o) return;
    o.st = ok ? 'ok' : 'no'; o.why = why || ''; fpcPaint();
    clearTimeout(FPC.hide); FPC.hide = setTimeout(() => { if (FPC.cur === o) { FPC.cur = null; fpcPaint(); } }, ok ? 4000 : 12000);
  }
  function fpcHtml(o) {
    const left = Math.max(0, Math.ceil((o.until - Date.now()) / 1000));
    const head = o.st === 'ok' ? '✓ 已驗證:' : o.st === 'no' ? '沒有執行:' : '等你驗證:';
    const icon = o.st === 'ok' ? '✓' : o.st === 'no' ? '✕' : '👆';
    const off = o.st ? 0 : FP_C * (1 - left / o.total);
    const rows = o.lines.filter((x) => x[1]).map(([k, v, up]) => `<span class="k">${esc(k)}</span><span${up ? ' class="up"' : ''}>${esc(v)}</span>`).join('');
    const foot = o.st === 'ok' ? '<div class="fph">已執行。</div>'
      : o.st === 'no' ? `<div class="fph">${esc(o.why || '取消或逾時')} = 不執行,什麼都沒改。要做再按一次。</div><div class="fpa"><button type="button" class="k" data-fpcok="1">好</button></div>`
      : `<div class="fph">${(typeof fpWin === 'function' && fpWin()) ? '請在這台電腦跳出的 <b>Windows Hello</b> 視窗確認(輸入 PIN 就可以)。' : '請在這台電腦跳出的<b>系統視窗</b>按指紋;沒有指紋就輸入電腦登入密碼。'}視窗可能在其他視窗後面。</div><div class="fpcode">對照碼 <b data-fpcode>${o.code ? esc(o.code) : o.codeWait ? '…' : '算不出來'}</b></div><div class="fph dim">${o.code || o.codeWait ? '系統視窗上的<b>對照碼</b>跟這張卡一樣才按;動作、對象以系統視窗為準。對照碼不一樣就按取消。' : '這台算不出對照碼:動作、對象以系統視窗為準,看不懂就按取消。'}</div><div class="fpa"><button type="button" class="k" data-fpcx="1">取消這次</button></div>`;
    return `<div class="fpc ${o.st} ${!o.st && left <= 30 ? 'late' : ''} ${!o.st && left <= 10 ? 'urg' : ''}" role="status" aria-live="polite"><div class="fpl"><svg viewBox="0 0 56 56" class="ring" aria-hidden="true"><circle cx="28" cy="28" r="25" class="bg"/><circle cx="28" cy="28" r="25" class="fg" data-sty="stroke-dashoffset:${off.toFixed(1)}"/></svg><span class="fpi">${icon}</span>${o.st ? '' : `<span class="sec">還有 ${left} 秒</span>`}</div><div class="fpm"><b>${head}${esc(o.what)}</b>${rows ? `<div class="fpt">${rows}</div>` : ''}${foot}</div></div>`;
  }
  function fpcPaint() {
    let box = document.getElementById('fpcbox');
    const o = FPC.cur;
    if (!o) { if (box) box.remove(); return; }
    const host = document.querySelector('dialog[open]') || document.body;
    if (!box || box.parentNode !== host) {
      if (box) box.remove();
      box = document.createElement('div'); box.id = 'fpcbox'; box.className = 'fpcbox';
      box.addEventListener('click', (e) => {
        const c = FPC.cur; if (!c) return;
        if (e.target.closest('[data-fpcx]')) { c.abort = true; return; } // fingerAct 下一輪看到就停,不再帶票重送 ⇒ 系統視窗晚按也不會執行
        // 8b09a REVIEW-fpcard L2:owner-request r4 是 aibd 核准後自己接著做 —— 接 7ebca 真 act 時,取消一定要通知 aibd 作廢這張票,不然「什麼都沒改」會變假話(DESIGN-fpbatch-page 步驟 5)
        if (e.target.closest('[data-fpcok]')) { FPC.cur = null; fpcPaint(); }
      });
      host.appendChild(box);
    }
    // 8b09a REVIEW-fpcard L1:每秒只換秒數與倒數圈,「取消這次」按鈕節點不換 ⇒ 按下到放開之間不會被換掉、吃掉 click
    const left = Math.max(0, Math.ceil((o.until - Date.now()) / 1000));
    const k = [o.st, !o.st && left <= 30, !o.st && left <= 10, o.code, o.codeWait, o.what].join('|');
    const sec = box.querySelector('.sec'), fg = box.querySelector('.ring .fg');
    if (box.dataset.k === k && sec && fg) { sec.textContent = '還有 ' + left + ' 秒'; fg.style.strokeDashoffset = (FP_C * (1 - left / o.total)).toFixed(1); return; }
    box.dataset.k = k; box.innerHTML = fpcHtml(o);
  }
  // AP26 W-7(c2f0b PAGE-WORDS-ap26-c2f0b-1125 r0 §2;ebab8 16:14,Andy:Windows 與沒有生物辨識的方案先上):aibd 回的碼 → 頁面那一句。
  // 頁面只照碼選字,不顯示請求或回應裡帶的任何字(唯一例外:解鎖時間只收 HH:MM:SS 形狀)。碼不存在就不會出現。
  // §1 機器分類頂端那一行、§3 密語窗:等 3e53b W-3 / W-5 給欄位名再接(見 OWNER_CLASS_NA)。
  const OWNER_WHY = {
    owner_check_too_weak: '這台的本人驗證方式不夠強,這個動作不開放。',
    owner_check_unmeasured: 'Windows Hello 還沒在這台電腦上實測過,高風險動作先不開放。',
    owner_batch_one_at_a_time: '這台每件要分開確認(每件跳一次 Windows Hello),請一件一件按。',  // c2f0b B2 L3 8ea92b569e;頁面不送 owner_batch,收到就照字(ebab8 10:43)
    unavailable: 'AIB 現在不在你登入的桌面上執行(例如從遠端連線啟動),沒辦法跳出確認視窗。請在這台電腦前登入後再按。',
    no_biometry: '這台沒有設定 Windows Hello。',
    cancel: '你按了取消,沒有動任何東西。',
    pair_touch_timeout: '確認視窗等太久沒有回應,沒有動任何東西。可以再按一次。',
    auth_failed: '驗證沒通過,沒有動任何東西。',
    pair_key_weak: '這台的配對金鑰不需要本人就能簽,可能被換過。請重建配對金鑰。',
    pair_key_mismatch: '這台的配對金鑰跟登記的不一樣。請重建配對金鑰。',
    owner_check_key_mismatch: '這台的配對金鑰跟登記的不一樣。請重建配對金鑰。',
    pair_key_missing: '這台還沒有配對金鑰。請先建配對金鑰。',
    pair_touch_not_on_this_system: '這台電腦的系統目前還不支援本人驗證。',
    passphrase_locked: '密語打錯太多次,暫時鎖住。',
    // c2f0b 18:00(Pro 16 實機單 2 r7):蓋上蓋子會讀成這個,打開就恢復
    owner_check_finger_not_now: '這台的 Touch ID 現在讀不到(蓋子蓋著,或指紋鍵盤沒接):打開蓋子或接上鍵盤再按一次。',
    // W-10(c2f0b IFACE r3 核字;11-26 後半句照用字第 11 列改成「沒有動任何東西」,待 c2f0b 再核):寫開席設定的拒絕原因
    policy_no_native: '這台沒找到可以用的 AI 程式本體,沒寫設定。請用官方安裝程式裝好後按「重新找」。',
    policy_exists: '這台已經有開席設定,AIB 不會蓋掉它。要重設,請先刪掉開席設定檔再按「重新找」。',
    policy_write_failed: 'AIB 寫不進開席設定檔,沒有動任何東西。可以再按一次。',
    policy_bad_request: '頁面送出的請求格式不對,沒有動任何東西;請重新整理頁面再試。',
  };
  // 不夠強時,c2f0b 依機器分類補一句;分類(W-3)頁面還讀不到 ⇒ Windows 上只補「有密碼沒設 Hello」那條怎麼做(ebab8:寫到 PIN (Windows Hello)),其他照實寫還沒有
  // c2f0b PAGE-WORDS-ap26 §1 (b)「怎麼設 PIN」三步照抄;第 3 步的「重新檢查」要 W-3 分類上 API 才有那顆鈕 ⇒ 先寫「再按一次」(ebab8 03:10:沒 PIN 時頁面要引導,Windows 今天上線)
  // f163c DECISION-w2 驗收面附條件 1:沒 TPM 的軟體 Hello 已經有 PIN,回的也是 too_weak ⇒ 照 owner-check class 分,hello_software 不接 PIN 三步
  const OWNER_NO_TPM = '這台沒有安全晶片(TPM),這類動作在這台不開放;設 PIN 也解決不了。';
  const OWNER_PIN_HOW = '還沒設 Windows Hello 的話,照這三步設一個 PIN(不用指紋或臉部辨識的硬體):1. 打開「設定」→「帳戶」→「登入選項」。2. 在「PIN (Windows Hello)」按「設定」,照畫面設一個 PIN。3. 設好回到這頁再按一次。沒有 Windows 登入密碼的話,要先設密碼才能設 PIN。';
  const OWNER_CLASS_NA = '這台是哪一種本人驗證、要怎麼補強:這一版 AIB 還沒有';
  const OWNER_LOCK_NA = '解鎖時間:這一版 AIB 還沒有。';
  // 拒絕原因四語(ebab8 11-26 00:35;Andy 00:4x 定「本人驗證」「配對金鑰」,其餘照 RETRO-fc7f9-1126 §0.2):語言跟 askLang 同一條。
  // ja / ko 是提案字(未核);Windows / Apple 介面原名(unavailable、finger_not_now、PIN 路徑)沒對過該語系截圖 ⇒ ja / ko 不給,退英文、不猜
  const OWNER_WHY_L = {
    en: {
      owner_check_too_weak: "This computer's owner verification is not strong enough, so this action is not available.",
      owner_check_unmeasured: 'Windows Hello has not had an on-device check on this computer yet, so high-risk actions are not available for now.',
      owner_batch_one_at_a_time: 'On this computer each item has to be confirmed separately (Windows Hello asks once per item); please do them one at a time.',
      unavailable: 'AIB is not running on the desktop you signed in to (for example, it was started from a remote connection), so it cannot show the confirmation window. Sign in at this computer and press again.',
      no_biometry: 'Windows Hello is not set up on this computer.',
      cancel: 'You pressed Cancel. Nothing was changed.',
      pair_touch_timeout: 'The confirmation window got no answer for too long. Nothing was changed. You can press again.',
      auth_failed: 'The check did not pass. Nothing was changed.',
      pair_key_weak: "This computer's pairing key can sign without the owner, so it may have been replaced. Make a new pairing key.",
      pair_key_mismatch: "This computer's pairing key is not the one on record. Make a new pairing key.",
      owner_check_key_mismatch: "This computer's pairing key is not the one on record. Make a new pairing key.",
      pair_key_missing: 'This computer has no pairing key yet. Make a pairing key first.',
      pair_touch_not_on_this_system: "This computer's system does not support owner verification yet.",
      passphrase_locked: 'The passphrase was wrong too many times, so it is locked for now.',
      owner_check_finger_not_now: "Touch ID on this computer can't be read right now (the lid is closed, or the Magic Keyboard with Touch ID is not connected). Open the lid or connect the keyboard and press again.",
      policy_no_native: 'No usable AI program was found on this computer, so no setup was written. Install it with the official installer, then press “重新找” (Find again).',
      policy_exists: "This computer already has a seat-opening setup, and AIB won't overwrite it. To reset it, delete the setup file first, then press “重新找” (Find again).",
      policy_write_failed: 'AIB could not write the seat-opening setup file. Nothing was changed. You can press again.',
      policy_bad_request: 'The page sent a request in the wrong format. Nothing was changed. Reload the page and try again.',
      pin: 'If Windows Hello is not set up yet, set up a PIN in three steps (no fingerprint or face hardware needed): 1. Open Settings → Accounts → Sign-in options. 2. Under PIN (Windows Hello), press Set up and follow the screens. 3. Come back to this page and press again. If this computer has no Windows sign-in password, set one first.',
      class_na: "Which kind of owner verification this computer has, and how to make it stronger: This version of AIB doesn't have this yet.",
      no_tpm: 'This computer has no security chip (TPM), so this kind of action is not available here; setting a PIN will not change that.',
      lock_at: 'You can try again after {t}.',
      lock_na: "When it unlocks: This version of AIB doesn't have this yet.",
    },
    ja: {
      owner_check_too_weak: 'このパソコンの本人確認の方式が十分に強くないため、この操作は使えません。',
      owner_check_unmeasured: 'Windows Hello はまだこのパソコンで実測していないため、高リスクの操作は今は使えません。',
      owner_batch_one_at_a_time: 'このパソコンでは 1 件ずつ確認が必要です(1 件ごとに Windows Hello が表示されます)。1 件ずつ押してください。',
      no_biometry: 'このパソコンには Windows Hello が設定されていません。',
      cancel: 'キャンセルを押しました。何も変更していません。',
      pair_touch_timeout: '確認ウィンドウが長いあいだ応答しませんでした。何も変更していません。もう一度押せます。',
      auth_failed: '認証が通りませんでした。何も変更していません。',
      pair_key_weak: 'このパソコンのペアリングキーは本人なしで署名できるため、差し替えられた可能性があります。ペアリングキーを作り直してください。',
      pair_key_mismatch: 'このパソコンのペアリングキーが登録済みのものと違います。ペアリングキーを作り直してください。',
      owner_check_key_mismatch: 'このパソコンのペアリングキーが登録済みのものと違います。ペアリングキーを作り直してください。',
      pair_key_missing: 'このパソコンにはまだペアリングキーがありません。先にペアリングキーを作ってください。',
      pair_touch_not_on_this_system: 'このパソコンのシステムはまだ本人確認に対応していません。',
      passphrase_locked: 'パスフレーズを何度も間違えたため、一時的にロックされています。',
      policy_no_native: 'このパソコンで使える AI プログラム本体が見つからなかったため、設定は書いていません。公式インストーラーで入れてから「重新找」(再検索)を押してください。',
      policy_exists: 'このパソコンにはすでに AI の席を開く設定があり、AIB は上書きしません。リセットするには、先に設定ファイルを削除してから「重新找」(再検索)を押してください。',
      policy_write_failed: 'AIB は AI の席を開く設定ファイルに書き込めませんでした。何も変更していません。もう一度押せます。',
      policy_bad_request: 'ページが送ったリクエストの形式が正しくありません。何も変更していません。ページを再読み込みしてから試してください。',
      class_na: 'このパソコンの本人確認の種類と強化の方法:この AIB のバージョンにはまだありません。',
      no_tpm: 'このパソコンにはセキュリティチップ(TPM)がないため、この種類の操作はここでは使えません。PIN を設定しても変わりません。',
      lock_at: '{t} 以降にもう一度試せます。',
      lock_na: 'ロック解除の時刻:この AIB のバージョンにはまだありません。',
    },
    ko: {
      owner_check_too_weak: '이 컴퓨터의 본인 확인 방식이 충분히 강하지 않아 이 작업은 사용할 수 없습니다.',
      owner_check_unmeasured: 'Windows Hello를 아직 이 컴퓨터에서 실측하지 않아 고위험 작업은 지금 사용할 수 없습니다.',
      owner_batch_one_at_a_time: '이 컴퓨터에서는 항목마다 따로 확인해야 합니다(항목마다 Windows Hello가 한 번씩 뜹니다). 하나씩 눌러 주세요.',
      no_biometry: '이 컴퓨터에는 Windows Hello가 설정되어 있지 않습니다.',
      cancel: '취소를 눌렀습니다. 아무것도 바꾸지 않았습니다.',
      pair_touch_timeout: '확인 창이 너무 오래 응답하지 않았습니다. 아무것도 바꾸지 않았습니다. 다시 누를 수 있습니다.',
      auth_failed: '인증을 통과하지 못했습니다. 아무것도 바꾸지 않았습니다.',
      pair_key_weak: '이 컴퓨터의 페어링 키는 본인 없이도 서명할 수 있어 바뀌었을 수 있습니다. 페어링 키를 다시 만드세요.',
      pair_key_mismatch: '이 컴퓨터의 페어링 키가 등록된 것과 다릅니다. 페어링 키를 다시 만드세요.',
      owner_check_key_mismatch: '이 컴퓨터의 페어링 키가 등록된 것과 다릅니다. 페어링 키를 다시 만드세요.',
      pair_key_missing: '이 컴퓨터에는 아직 페어링 키가 없습니다. 먼저 페어링 키를 만드세요.',
      pair_touch_not_on_this_system: '이 컴퓨터의 시스템은 아직 본인 확인을 지원하지 않습니다.',
      passphrase_locked: '암호 문구를 너무 많이 틀려 잠시 잠겼습니다.',
      policy_no_native: '이 컴퓨터에서 쓸 수 있는 AI 프로그램 본체를 찾지 못해 설정을 쓰지 않았습니다. 공식 설치 프로그램으로 설치한 뒤 「重新找」(다시 찾기)를 누르세요.',
      policy_exists: '이 컴퓨터에는 이미 AI 좌석 열기 설정이 있으며, AIB는 덮어쓰지 않습니다. 다시 설정하려면 먼저 설정 파일을 지운 뒤 「重新找」(다시 찾기)를 누르세요.',
      policy_write_failed: 'AIB가 AI 좌석 열기 설정 파일에 쓰지 못했습니다. 아무것도 바꾸지 않았습니다. 다시 누를 수 있습니다.',
      policy_bad_request: '페이지가 보낸 요청 형식이 올바르지 않습니다. 아무것도 바꾸지 않았습니다. 페이지를 새로 고친 뒤 다시 시도하세요.',
      class_na: '이 컴퓨터의 본인 확인 종류와 강화 방법: 이 버전의 AIB에는 아직 없습니다.',
      no_tpm: '이 컴퓨터에는 보안 칩(TPM)이 없어 이런 작업은 여기서 쓸 수 없습니다. PIN을 설정해도 바뀌지 않습니다.',
      lock_at: '{t} 이후에 다시 시도할 수 있습니다.',
      lock_na: '잠금 해제 시간: 이 버전의 AIB에는 아직 없습니다.',
    },
  };
  // 只認 aibd 的碼(字串、表裡有的);不讀 message / words 等任何帶字的欄位
  function ownerWhy(code, e) {
    const c = typeof code === 'string' ? code : '';
    if (!Object.prototype.hasOwnProperty.call(OWNER_WHY, c)) return '';
    const L = typeof askLang === 'function' ? askLang() : 'zh-TW', has = (o, k) => !!o && Object.prototype.hasOwnProperty.call(o, k);
    // 中文照中文表;其他語先找該語,沒有就英文(ja / ko 不猜介面原名)
    const word = (k, zh) => (L === 'zh-TW' ? zh : has(OWNER_WHY_L[L], k) ? OWNER_WHY_L[L][k] : OWNER_WHY_L.en[k]);
    const w = word(c, OWNER_WHY[c]), sp = L === 'zh-TW' || L === 'ja' ? '' : ' ';
    const isWin = /Win/.test(String((globalThis.navigator && navigator.platform) || ''));
    const ocCls = typeof S === 'object' && S && S.oc && S.oc.d && S.oc.d.platform === 'windows' ? String(S.oc.d.class || '') : '';
    if (c === 'owner_check_too_weak') return w + sp + (isWin ? (ocCls === 'hello_software' ? word('no_tpm', OWNER_NO_TPM) : word('pin', OWNER_PIN_HOW)) : word('class_na', OWNER_CLASS_NA));
    if (c === 'no_biometry' && isWin) return w + sp + word('pin', OWNER_PIN_HOW); // 沒設 Hello:不能只說沒有,要說怎麼設
    if (c === 'passphrase_locked') { const t = String((e && (e.retry_hms || (e.body && e.body.retry_hms))) || ''); return w + sp + (/^\d\d:\d\d:\d\d$/.test(t) ? word('lock_at', '{t} 後可以再試。').replace('{t}', () => t) : word('lock_na', OWNER_LOCK_NA)); }
    return w;
  }
  const FP_NO = { finger_cancel: '你按了取消這次', finger_denied: '系統視窗按了拒絕', finger_expired: '時間到了', finger_timeout: '時間到了', owner_check_unavailable: '這台現在叫不出驗證視窗' };
  async function fingerAct(type, body, wait, extra) {
    let r = await act(type, body);
    if (!(r && r.__status === 202 && r.ticket)) return r;
    const first = r, more = extra ? extra(first) : {};
    // 519d0 19:2x:等到 aibd 單號實際到期(expires_at);沒給到期就等 10 分(原本寫死 2 分,轉信慢的窗會先過期)
    const exp = Date.parse(String(r.expires_at || '')), ticket = String(r.ticket), until = Number.isFinite(exp) && exp > Date.now() ? Math.min(exp, Date.now() + 900000) : Date.now() + 600000; // 最多等 15 分(5d33a N2)
    // 7b81e IFACE-owner-touch r0(1a93b 11-20):每 1 秒問一次、到單號到期為止;每一輪把剩幾秒交給畫面倒數;unavailable 直接停
    const left = () => Math.max(0, Math.ceil((until - Date.now()) / 1000));
    const card = fpcOpen(type, body, first, until);
    try { const out = await fingerWait(); fpcDone(card, true); return out; }
    catch (e) { const rs = e && (e.reason || e.code || (e.body && e.body.reason)); fpcDone(card, false, (e && e.pageMsg) || FP_NO[rs] || (rs === 'owner_check_not_approved' ? '沒按到' : '沒有完成')); throw e; }
    async function fingerWait() {
    if (wait) wait(ticket, left(), first);
    // 7b81e 06:2x:G2 會有 GET /api/v1/owner/tickets/{ticket} ⇒ {state: pending|approved|denied|expired};有就先問狀態、過了才帶單重送一次(分得出拒絕);沒有(404)⇒ 照舊帶單重送
    let probe = true;
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 1000));
      if (card.abort) throw Object.assign(new Error('finger_cancel'), { code: 'finger_cancel', reason: 'finger_cancel', ticket });
      if (wait) wait(ticket, left(), first);
      fpcPaint();
      if (probe) {
        const t = await soft(get('/api/v1/owner/tickets/' + encodeURIComponent(ticket))).catch(() => null);
        const st = t && String(t.state || '');
        if (!t || !st) probe = false;
        else if (st === 'unavailable') { const why = String(t.reason || 'owner_check_unavailable'); throw Object.assign(new Error(why), { code: 'owner_check_unavailable', reason: REPO_WHY[why] ? why : 'owner_check_unavailable', why, pageMsg: ownerWhy(why, t) || undefined }); }
        else if (st === 'denied' || st === 'expired') throw Object.assign(new Error('finger_' + st), { code: 'finger_' + st, reason: 'finger_' + st, ticket, why: t.reason, pageMsg: ownerWhy(t.reason, t) || undefined });
        else if (st !== 'approved') { if (Date.now() > until) throw Object.assign(new Error('finger_timeout'), { code: 'finger_timeout', reason: 'finger_timeout', ticket }); continue; }
      }
      try { r = await act(type, Object.assign({}, body, more, { ticket })); if (!(r && r.__status === 202)) return r; }
      catch (e) { const rs = e && (e.reason || (e.body && e.body.reason)); if (rs !== 'owner_check_not_approved') throw e; }
      if (Date.now() > until) throw Object.assign(new Error('finger_timeout'), { code: 'finger_timeout', reason: 'finger_timeout', ticket });
    }
    }
  }
  const REPO_WHY = {
    name: '名字只能 1–40 個小寫英文、數字、減號(例 ez2-aib)',
    path: '路徑要是這台 Mac 上一個資料夾的完整路徑(以 / 開頭)',
    path_inside_aibd: '這個資料夾在 AIB 自己的資料夾裡,不能登記',
    not_a_repo: '這個資料夾不是 git 倉',
    not_repo_top: '要填倉的最上層資料夾(有 .git 的那一層),不是裡面的子資料夾',
    settings_symlink: '倉裡的專案設定檔有捷徑(連結),AIB 不收;拿掉再登記',
    settings_not_file: '倉裡的專案設定不是一般檔案,AIB 不收',
    settings_too_many: '倉裡的專案設定檔太多,AIB 不收',
    settings_too_deep: '倉裡的專案設定資料夾太深,AIB 不收',
    project_settings_changed: '這個倉的專案設定在登記後改過;要重新登記(會再按一次指紋)',
    repos_unwritable: 'AIB 寫不進倉的名單',
    repos_unreadable: 'AIB 讀不到倉的名單',
    owner_check_unavailable: '要本人指紋,這台的 AIB 還沒接上指紋;先不能登記',
    owner_check_not_approved: '指紋沒過(沒按、按了拒絕,或那張單已用過)',
    finger_timeout: '單號過期前沒看到指紋按過(沒按或按了拒絕);' + FINGER_GONE + ',要的話再按一次',
    finger_denied: '指紋視窗按了拒絕;要的話再按一次',
    finger_expired: FINGER_GONE + ';再按一次會開新的一張',
    finger_cancel: '你按了取消這次,沒有執行;要的話再按一次',
    // 7b81e IFACE-owner-touch r0:第一步就被擋的三種
    pair_touch_busy: '已經有一個指紋視窗開著;先回那一個再按',
    pair_touch_cooling: '剛按了不同意,冷卻中;稍等一下再按',
    pair_touch_limited: '10 分鐘內最多 3 張指紋窗(一小時最多 6 張);晚點再試',
    preview_no_dgrs: '預覽區不開「能改程式」的席,倉只能在正式那顆 AIB 用',
    settings_unreadable: '讀不到倉裡的專案設定,AIB 不收',
    hooks: 'AIB 準備不了自己的 git 設定資料夾;重開 AIB 再試',
    repo_copies_full: '這個倉同時開的份數已到上限(最多 9 份);先收掉一席再開',
    disk_low: '這台磁碟剩不到 2 GiB,不再多開一份倉;先清出空間',
    base: '「從哪一顆開始」要填完整的 40 碼提交(小寫英數),或留空',
    base_not_in_repo: '這個倉裡沒有那一顆提交(或沒有分支走得到它);檢查提交碼',
    tree_not_opened: '在這個倉開不出這一席自己的一份(分支);看倉是不是在改到一半',
  };
  const repoWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return (typeof fpSay === 'function' ? fpSay : (t) => t)(gateWhy(e) || REPO_WHY[rs] || (rs === 'repo' ? '這個倉不能這樣開:' + errText(e) : errText(e))); };
  // 519d0 06:2x 判:做事席也能帶登記的倉(沙盒照舊、一律開自己的一份),7b81e 做;aibd 在 GET version 給 repo_modes 才開給做事席,沒給 = 只有能改程式席
  const repoModes = () => (S.version && Array.isArray(S.version.repo_modes) ? S.version.repo_modes.map(String) : ['dgrs']);
  const curRepo = (A) => { const R = S.version && Array.isArray(S.version.repos) ? S.version.repos.map((x) => String((x && x.name) || x || '')) : []; return A.repo && R.includes(A.repo) && repoModes().includes(A.mode) ? A.repo : ''; };
  // 能改程式的席開在哪:aibd 的 GET version 有 repos(名字清單)才出這段;沒有 = 舊 aibd,照舊系統新開資料夾
  function repoSec(A) {
    const R = S.version && Array.isArray(S.version.repos) ? S.version.repos.map((x) => String((x && x.name) || x || '')).filter(Boolean) : null;
    if (!R) return '';
    const F = A.repoF;
    const form = F ? `<div class="anf"><input class="apq" data-addrf="name" value="${esc(F.name || '')}" maxlength="40" placeholder="倉的名字(例 ez2-aib)" aria-label="倉的名字" autocomplete="off"><input class="apq" data-addrf="path" value="${esc(F.path || '')}" placeholder="這台 Mac 上倉的最上層資料夾,例 /Users/你/ez2-aib" aria-label="倉的資料夾" autocomplete="off" spellcheck="false"><div class="apn">${F.ticket ? FP_SEE : esc((typeof fpSay === 'function' ? fpSay : (t) => t)('AIB 會先讀這個倉(最上層、遠端、專案設定),再請你在這台按一次指紋;按過才記下來。之後開席只用名字,不再填路徑。'))}</div>${F.msg ? `<div class="apn bad">${esc(F.msg)}</div>` : ''}<div class="anfb"><button type="button" class="k" data-addrx="1">取消</button><button type="button" class="go ${F.busy ? 'busy' : ''}" data-addrs="1" ${F.busy ? 'disabled' : ''}>${F.busy ? (F.ticket ? (typeof fpSay === 'function' ? fpSay : (t) => t)('等指紋…') : '讀倉中…') : '登記'}</button></div></div>` : '';
    const cur = A.repo && R.includes(A.repo) ? A.repo : '';
    return `<div class="asec"><span>在登記過的倉裡做(開自己的分支)</span><div class="apn">選登記過的倉 ⇒ 這一席在那個倉的自己一份(自己的分支)上改,${esc((typeof fpSay === 'function' ? fpSay : (t) => t)(A.mode === 'work' ? '開的時候不用按指紋(做事席照舊在沙盒裡)' : '開之前要按一次指紋'))};不選 = 系統新開一個空資料夾</div><div class="apl"><button type="button" class="apr ${cur ? '' : 'on'}" data-addrepo=""><b>系統新開的資料夾</b></button>${R.map((n) => `<button type="button" class="apr ${cur === n ? 'on' : ''}" data-addrepo="${esc(n)}"><b>${esc(n)}</b><i>倉</i></button>`).join('')}${F ? '' : '<button type="button" class="apr" data-addrnew="1"><b>+ 登記一個倉</b></button>'}</div>${cur ? `<div class="anf"><input class="apq" data-addf="base" value="${esc(A.base || '')}" maxlength="40" placeholder="從哪一顆開始(選填,40 碼提交;空白 = 倉現在的頭)" aria-label="從哪一顆提交開始" autocomplete="off" spellcheck="false"></div>` : ''}${form}</div>`;
  }
  // S0-6(2e2cd 11-23 01:5x;ebab8 FINDING-reporeg-dash-ebab8-1123:Andy 打出「ez2−aib」(U+2212)被拒,也沒說是哪個字):
  // 名字先正規化 —— 各種橫線換成 -、全形英數符號轉半形、大寫轉小寫;還不合規才報錯,並指出第幾個字
  const REPO_DASH = /[\uFF0D\u2010\u2011\u2012\u2013\u2014\u2212]/g;
  const repoNameNorm = (v) => String(v || '').replace(REPO_DASH, '-').replace(/[\uFF01-\uFF5E]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/\u3000/g, ' ').toLowerCase();
  function repoNameBad(n) {
    const C = [...String(n)];
    if (!C.length) return '名字是空的';
    if (C.length > 40) return '名字最多 40 個字(現在 ' + C.length + ' 個)';
    const i = C.findIndex((c) => !/^[a-z0-9-]$/.test(c));
    return i < 0 ? '' : '第 ' + (i + 1) + ' 個字「' + C[i] + '」' + (/[\s]/.test(C[i]) ? '是空白,' : '') + '不能用';
  }
  async function repoReg() {
    const A = S.add, F = A && A.repoF; if (!F || F.busy) return;
    const name = repoNameNorm(F.name).trim(), path = String(F.path || '').trim();
    F.name = name; // 欄位顯示正規化後的值
    const bad = repoNameBad(name); if (bad) { F.msg = bad + ';' + REPO_WHY.name; return render(); }
    if (!path.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(path)) { F.msg = REPO_WHY.path; return render(); }
    F.busy = true; F.msg = ''; F.ticket = ''; render();
    try {
      const r = await fingerAct('repo_register', { name, path }, (t, sec) => { if (S.add === A && A.repoF === F) { F.ticket = t; F.left = sec; render(); } });
      const nm = String((r && r.name) || name);
      const v = await soft(get('/api/v1/version')).catch(() => null);
      if (v) S.version = Object.assign({}, S.version, { repos: v.repos });
      if (S.add === A) { A.repoF = null; A.repo = nm; A.key = idem(); }
      say('登記好了 倉 ' + nm);
    } catch (e) { F.busy = false; F.ticket = ''; F.msg = '沒登記成:' + repoWhy(e); }
    render();
  }
  // a9136 00bb67ef4:with_seat + own_tree:true = 開在那一席倉的自己一棵樹;第三個以上做事席預設勾(同資料夾最多兩個寫的)
  const ownTree = (A, WS) => (TREE_HOLD ? false : A.ownTree !== undefined ? !!A.ownTree : WS.length >= 2);
  // ---------- S1 新增 AI 夥伴(2e2cd 11-23 派工,能看;SPEC-ai-partner-open-ebab8-1122 r1 + DECISION-council-aipartner-ebab8-1122 r1)----------
  // 方案(大分類 → 小分類)照 ebab8 示意 v26 的 CATS;mode 只用現有三種(safe / work / dgrs),「管理主機」那兩項(主機維運、防護)等 aibd 做好才放,
  // 現在連灰的也不放。pk = 用名字找人格的關鍵字(找不到就不選);role 是角色;不帶專案項目(要用戶自己選);sens = 敏感類,不帶第二大腦
  const APS = (name, what, o) => Object.assign({ name, what, mode: 'work', pk: '', role: '' }, o);
  const AP_CATS = [
    { id: 'research', ic: '🔎', zh: '研究與數據', subs: [APS('研究調查', '查資料、整理來源', { mode: 'safe', pk: '研究專家' }), APS('新聞 / 熱門追蹤', '每日摘要、熱門影音', { mode: 'safe', pk: '情報探子' }), APS('數據 / 成效分析', '讀數字、給決策', { pk: '成效決策' }), APS('競品 / 市場監測', '每天自動跑一輪', { mode: 'safe', pk: '情報探子' })] },
    { id: 'writing', ic: '✍️', zh: '寫作與翻譯', subs: [APS('寫文章 / 部落格', '寫稿 + 審稿', { pk: '寫手' }), APS('口吻文案', '照品牌口吻寫', { pk: '口吻文案' }), APS('Email / 客服回覆', '照口吻回信', { mode: 'safe', pk: '口吻文案' }), APS('創意寫作 / 角色扮演', '故事、劇本', { mode: 'safe', pk: '尹天仇' })] },
    { id: 'business', ic: '📈', zh: '商業與行銷', subs: [APS('SEO / GEO', '排名、AI 搜尋曝光', { pk: 'SEO主管', role: 'author' }), APS('社群內容', '貼文、圖卡', { pk: '內容管家' }), APS('文章內 CTA', '轉換按鈕、導購', { pk: 'CTA專家' }), APS('LINE 圖文選單', '選單、自動回覆', { mode: 'dgrs', pk: 'LINE 圖文選單', role: 'author' })] },
    { id: 'video', ic: '🎬', zh: '影片與音訊', subs: [APS('短影音腳本', 'Reels / Shorts / TikTok', { pk: 'Reels腳本' }), APS('外拍 / 拍攝清單', '場景、鏡頭、道具', { pk: '外拍清單' }), APS('AI 生成影片', '提示詞、鏡頭、轉場', { pk: '影片提示詞' }), APS('剪輯指示 / 字幕', '時間碼、多語字幕', { pk: '剪輯' }), APS('YouTube 上架', '標題、描述、章節', { pk: 'YouTube' }), APS('Podcast / 口播', '講稿、提綱', { mode: 'safe', pk: '口吻文案' })] },
    { id: 'design', ic: '🌐', zh: '設計與網站', subs: [APS('設計網站 / 改頁面', '改版、落地頁', { mode: 'dgrs', pk: '頁面工程師', role: 'author' }), APS('Ez2.PAGE 頁面', 'bio 頁、一頁式', { mode: 'dgrs', pk: 'Ez2.PAGE' }), APS('生圖 / 美術', '插圖、素材', { pk: '生圖專家' }), APS('圖卡', '社群圖卡', { pk: '圖卡專家' })] },
    { id: 'tech', ic: '💻', zh: '程式與科技', subs: [APS('寫程式開發', '開分支寫功能', { mode: 'dgrs', pk: '工程師', role: 'author' }), APS('寫測試 / 驗收', '實跑、給結論', { pk: '驗收審查員', role: 'verifier' })] },
    { id: 'work', ic: '🗂️', zh: '工作與生產力', subs: [APS('SOP / 流程文件', '步驟、檢查表', { pk: 'SOP流程' }), APS('工作操作教練', '一步步帶做', { mode: 'safe', pk: '工作操作教練' }), APS('統籌 / 派工', '排序、分工、追進度', { mode: 'dgrs', pk: '主軸統籌' })] },
    { id: 'learn', ic: '🎓', zh: '學習與教學', subs: [APS('家教 / 學新東西', '一步步教', { mode: 'safe', pk: '教練' }), APS('技能工匠', '把做法寫成技能', { pk: '技能工匠' })] },
    { id: 'life', ic: '🏠', zh: '生活與娛樂', subs: [APS('聊天陪伴', '閒聊', { mode: 'safe', pk: '唐僧' }), APS('分身', '用你的口吻回話', { mode: 'safe', pk: 'AndyAI分身' })] },
    { id: 'sens', ic: '🛡️', zh: '健康 · 理財 · 法律 · 人際 · 心靈', subs: [APS('健康問答', '整理資訊、不做診斷', { mode: 'safe', pk: '研究專家', sens: 1 }), APS('合約 / 法規閱讀', '標重點、列問題', { mode: 'safe', pk: '研究專家', sens: 1 })] },
    { id: 'handoff', ic: '🔁', zh: '接手舊席', back: 1, subs: [] },
    { id: 'custom', ic: '⚙️', zh: '自訂', subs: [APS('自訂', '每一項自己挑', { mode: '' })] },
  ];
  // 下拉一項 = 品牌 · 模型;模型 = aibd 的這家預設(default_model_set 改的那個),沒設就寫用它自己的預設
  const apPvLabel = (p) => p.name + ' · ' + (dfltModel(p.id) || '它自己的預設模型');
  // 選中的那一個模型(A.model 空 = 用預設);下拉值 = 品牌|模型
  const apPvModel = (p, A) => (A && A.provider === p.key && A.model && (p.models || []).some((m) => m.id === A.model) ? A.model : '');
  function apPvOpts(p, pv, A) {
    const L = p.models && p.models.length ? p.models : null, cur = pv && p.key === pv.key ? apPvModel(p, A) : null;
    if (!p.open) return `<optgroup label="${esc(p.name)} — 不能選:${esc(p.why || '這台還不能開')}" disabled><option value="${esc(p.key)}|" disabled>${esc(apPvLabel(p))}</option></optgroup>`;
    if (!L) return `<option value="${esc(p.key)}|" ${cur !== null ? 'selected' : ''}>${esc(apPvLabel(p))}</option>`;
    return `<optgroup label="${esc(p.name)}">${L.map((m) => `<option value="${esc(p.key)}|${m.dflt ? '' : esc(m.id)}" ${cur !== null && (m.dflt ? !cur : cur === m.id) ? 'selected' : ''}>${esc(p.name + ' · ' + m.label)}${m.dflt ? '(預設)' : ''}${m.used ? '(用過)' : ''}</option>`).join('')}</optgroup>`;
  }
  // Andy 05:1x:「每一次按下對應的新增 AI 品牌時,就應該要自動更新最新的 AI 模型與使用量」⇒ 開品牌那一列、或換一家時重讀 version 與 usage(3 秒內不重讀)
  // aibd 的用量只有每位夥伴的脈絡用量(usage.rs:used / window / pct / model);帳號還剩多少額度它沒有 ⇒ 寫這一版還沒有
  async function apPvRefresh() {
    const A = S.add; if (!A || Date.now() - (A.pvAt || 0) < 3000) return;
    A.pvAt = Date.now(); A.pvBusy = true; render();
    const [v, u] = await Promise.all([soft(get('/api/v1/version')).catch(() => null), soft(get('/api/v1/usage')).catch(() => null)]);
    if (v) S.version = v; if (u && Array.isArray(u.usage)) S.usage = u;
    if (S.add === A) { A.pvBusy = false; A.pvRead = new Date().toTimeString().slice(0, 5); render(); }
  }
  function apPvUse(p) {
    const A = S.add, live = present().filter((x) => !isArch(x) && String(x.provider || 'claude').toLowerCase() === p.id.toLowerCase());
    const rows = live.map((x) => { const se = lastSession(x.seat_id), u = S.usage && (S.usage.usage || []).find((r) => r.seat_id === x.seat_id && (!se || r.session_id === (se.id || se.session_id))); return '@' + seatShort(x) + (u && u.model ? ' ' + u.model : '') + (u && u.pct != null ? ' 用了 ' + u.pct + '%' : ''); });
    const src = p.msrc === 'codex_cache' ? '模型清單來自這台 Codex 的快取' + (p.mat ? '(' + p.mat.replace('T', ' ').slice(5, 16) + ')' : '') : p.msrc === 'alias' ? '模型清單是 AIB 內建的別名' : p.models ? '' : '這一版 AIB 只給預設模型,沒有清單';
    return `<div class="apn apuse">${A && A.pvBusy ? '更新中…' : A && A.pvRead ? '剛更新(' + esc(A.pvRead) + ')' : ''}${src ? ' · ' + esc(src) : ''}</div><div class="apn">現在用 ${esc(p.name)} 的夥伴 ${live.length} 位${rows.length ? ':' + esc(rows.slice(0, 8).join('、')) : ''}。用量是每位這一輪的對話長度</div>${apQuota(p)}`;
  }
  // 08de5 A15(IFACE-a1-a15,頭 308171af95):GET /api/v1/usage 多 providers.{claude|codex}.quota = {windows:[{name, used_pct, resets_at?, status?, window_mins?}], source, status?, plan?, seen_at};
  // 沒看過 = {quota:null, quota_reason:'not_seen_yet'};不會有餘額 / 金額 ⇒ 只寫「用了幾 %、何時重算」;名字 / 狀態是 [a-z_] 小字,頁面自己翻,不認得照原字
  const QW_NAME = { five_hour: '五小時', seven_day: '七天', seven_day_opus: '七天 · Opus', seven_day_sonnet: '七天 · Sonnet', primary: '主要額度', secondary: '次要額度' };
  const QW_ST = { allowed: '可用', allowed_warning: '快到上限', rejected: '已到上限' };
  const qwWhen = (sec) => { const n = Number(sec); if (!Number.isFinite(n) || n <= 0) return ''; const d = new Date(n * 1000), now = new Date(); return (d.toDateString() === now.toDateString() ? '' : (d.getMonth() + 1) + '/' + d.getDate() + ' ') + d.toTimeString().slice(0, 5); };
  function apQuota(p) {
    const P = S.usage && S.usage.providers;
    if (!P || typeof P !== 'object') return '<div class="apn">帳號額度:這一版 AIB 還沒有</div>';
    const e = P[String(p.id || '').toLowerCase()], q = e && e.quota;
    if (!q || !Array.isArray(q.windows)) return `<div class="apn">帳號額度:這台還沒看過 ${esc(p.name)} 的額度(要先開過一位 ${esc(p.name)} 夥伴${String(p.id).toLowerCase() === 'claude' ? '、等它跑完第一個回合' : ''})</div>`;
    const W = q.windows.slice(0, 8).map((w) => {
      const pct = Math.max(0, Math.min(100, Number(w && w.used_pct) || 0)), nm = String((w && w.name) || ''), wm = Number(w && w.window_mins);
      // f163c A15 N1:有 window_mins 就以長度為準(codex primary 300 = 5 小時),沒有才翻名字
      const label = Number.isFinite(wm) && wm > 0 ? (wm % 1440 === 0 ? wm / 1440 + ' 天' : wm % 60 === 0 ? wm / 60 + ' 小時' : wm + ' 分') : QW_NAME[nm] || nm || '額度';
      const at = qwWhen(w && w.resets_at), st = w && w.status ? QW_ST[w.status] || String(w.status) : '';
      return `<div class="qw${pct >= 90 ? ' red' : pct >= 70 ? ' warn' : ''}"><span>${esc(label)}</span><progress class="usep" max="100" value="${pct}"></progress><b>用了 ${esc(String(Math.round(pct * 10) / 10))}%</b><i>${at ? esc(at) + ' 重算' : ''}${st ? (at ? ' · ' : '') + esc(st) : ''}</i></div>`;
    }).join('');
    const seen = qwWhen(q.seen_at), tail = [q.plan ? '方案 ' + String(q.plan) : '', q.status ? QW_ST[q.status] || String(q.status) : '', seen ? '看到於 ' + seen : ''].filter(Boolean).join(' · ');
    return `<div class="apn">帳號額度(AIB 從 ${esc(p.name)} 自己回報的數字讀,只有用了幾 %,沒有餘額)${tail ? ' · ' + esc(tail) : ''}</div><div class="qws">${W || '<div class="apn">沒有額度視窗</div>'}</div>`;
  }
  // 08de5 11-25(3e53b REVIEW-grok L1;Andy 選 (a) 接受):grok 1.0.70 安全席仍有 4 支 xAI 伺服器端 X 搜尋工具,旗標關不掉(EVIDENCE-brand-grok-08de5-1125)⇒ 品牌說明與開席前那一行都照實寫
  const GROK_X = 'Grok 安全席仍可搜尋 X(關不掉)';
  // 08de5 11-25(ebab8 q_17279e54,Andy「我沒金鑰啊」):Copilot 用 GitHub 帳號登入(權杖在系統鑰匙圈),不要跟人要金鑰
  const COPILOT_LOGIN = '用 GitHub 帳號登入,不用金鑰';
  const BRAND_WHAT = { claude: '能對話、能改程式', codex: '能對話、能改程式', agy: '能對話、查資料', grok: '能對話、查即時資訊 · ' + GROK_X + ':X 搜尋在 xAI 伺服器上,AIB 關不掉;碰不到你的檔案,但讀得到 X 上的公開貼文', copilot: '能對話、查資料 · ' + COPILOT_LOGIN };
  const AP_MODE = { safe: ['討論研究', '低'], work: ['沙盒開發', '中'], dgrs: ['完全權限', '最高'] };
  function seatModeTag(m) {
    const k = m === 'host' ? ['管理主機', '高', 3] : AP_MODE[m] ? [AP_MODE[m][0], AP_MODE[m][1], m === 'dgrs' ? 4 : m === 'work' ? 2 : 1] : null;
    return k ? `<em class="smode d${k[2]}" title="這一位開的模式">${esc(k[0])} · 危險程度:${esc(k[1])}</em>` : m ? `<em class="smode">${esc(String(m))}</em>` : '';
  }
  const AP_RK = 'ws.addRecent';
  const apSub = (key) => { const m = /^([a-z]+):(\d+)$/.exec(String(key || '')); const C = m && AP_CATS.find((x) => x.id === m[1]); return C && C.subs[Number(m[2])] ? { C, p: C.subs[Number(m[2])], i: Number(m[2]) } : null; };
  function apRecent() { try { const L = JSON.parse(localStorage.getItem(AP_RK) || '[]'); return Array.isArray(L) ? L.filter((k) => apSub(k) && !/^custom:/.test(k)).slice(0, 3) : []; } catch (e) { return []; } }
  function apRecentSave(key) { if (!apSub(key) || /^custom:/.test(key)) return; try { localStorage.setItem(AP_RK, JSON.stringify([key].concat(apRecent().filter((k) => k !== key)).slice(0, 3))); } catch (e) { /* 記不住就算了 */ } }
  // 點小分類 / 最近用過:帶出模式、人格(名字含關鍵字的第一個)、第二大腦(人格常用的;敏感類不帶)、角色。專案項目一律不帶
  function apPick(A, key) {
    const x = apSub(key); if (!x) return;
    const p = x.p; A.plan = key; A.cat = x.C.id; A.rowOpen = null; A.noRepoAsk = false; A.pkMiss = '';
    if (p.mode) A.mode = p.mode;
    apPkPick(A, p);
    if (p.sens) { A.brain = ''; A.brainTouched = true; }
    if (x.C.id !== 'custom') A.role = p.role || '';
    A.key = idem();
  }
  // 大分類一列幾顆:量上一次畫出來的格子(minmax 130 + 間距 8,示意 drawTop 同算法);還沒畫過照視窗寬 640 算 4 顆
  // 用途分類 15 類的編號(519d0 CATEGORIES-usecase-15,示意 data 同一套)
  const AP_NUM = { research: '06', writing: '02', business: '04', video: '07', design: '07', tech: '05', work: '03', learn: '01·14', life: '08·13', sens: '09–12·15' };
  // ebab8 / f163c 12:16(客戶截圖「找不到「唐僧」」):方案的 pk 是開發機上的人格名,客戶機沒有 ⇒ 不准把 pk 印出來;找不到就落到內建那張(08de5 aib000)並選好
  function apPkPick(A, p) {
    if (!p || !p.pk) return;
    const k = String(p.pk).toLowerCase(), hits = personas().filter(([, n]) => String(n).toLowerCase().includes(k)), hit = hits.find(([u]) => personaHasBrain(u)) || hits[0];
    A.personaTried = true; A.brainTouched = false;
    if (hit) { A.persona = hit[0]; brainPreselect(A); return; }
    const b = personas().find(([u]) => personaBuiltin(u)); A.brain = '';
    if (b) { A.persona = b[0]; A.pkMiss = ''; } else { A.persona = ''; A.pkMiss = '1'; }
  }
  const apPersonaSum = (A, pn) => pn || (A.pkMiss ? '這台沒有這個方案配的人格,請自己選' : '還沒選');
  // ebab8 12:20:新機第一次打開(清單只有內建人格、沒有席)⇒ 全部選好:Claude、討論研究、內建人格、第二大腦不綁、角色不掛、方案「聊天陪伴」;只要按一次「開」
  const AP_FIRST_PLAN = 'life:0';
  function apFirstDefaults(A) {
    if (!A || A.firstDone || A.plan) return false;
    const L = S.personaList || [];
    if (!(L.length === 1 && personaBuiltin(L[0][0])) || (S.seats || []).some((s) => s && !isArch(s))) return false;
    A.firstDone = true; apPick(A, AP_FIRST_PLAN);
    const cl = providers().find((p) => p.key === 'claude' && p.open); if (cl) A.provider = 'claude';
    A.mode = 'safe'; A.brain = ''; A.brainTouched = true; A.role = '';
    return true;
  }
  function apPkN(p) { if (!p || !p.pk) return ''; const k = String(p.pk).toLowerCase(), n = personas().filter(([, x]) => String(x).toLowerCase().includes(k)).length; return n ? ` · ${n} 個人格` : personas().some(([u]) => personaBuiltin(u)) ? ` · 用${esc(pbW()[1])}的 ${esc(pbW()[0])}` : ' · <span class="apwarn">這台還沒有對應人格</span>'; }
  function apCols() { const g = typeof document !== 'undefined' && document.querySelector('.adlg.ap .apcats'); return g && g.clientWidth ? Math.max(1, Math.floor((g.clientWidth + 8) / 138)) : 4; }
  // S1b 2(Andy 11-23 03:00「點下去都會跳回最上方」):render 整頁重畫,加夥伴視窗的捲動位置與焦點記下來、畫完還原;剛展開的那一列捲進視野
  function apKeep(root) {
    const d = root && root.querySelector('.adlg'); if (!d) return null;
    const a = typeof document !== 'undefined' ? document.activeElement : null; let fk = null;
    if (a && d.contains(a) && a.attributes) for (const x of a.attributes) if (/^data-[a-z0-9-]+$/.test(x.name)) { const v = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(x.value) : /^[\w:.-]*$/.test(x.value) ? x.value : null; if (v == null) continue; fk = '[' + x.name + '="' + v + '"]'; break; } // f163c L1:值一律 CSS.escape,換行 / 引號不會讓 querySelector 拋例外
    return { top: d.scrollTop, fk, sel: a && fk && typeof a.selectionStart === 'number' ? [a.selectionStart, a.selectionEnd] : null };
  }
  function apRestore(root, k) {
    const d = root && root.querySelector('.adlg'); if (!d) return;
    if (k) { d.scrollTop = k.top; const f = k.fk && d.querySelector(k.fk); if (f) { try { f.focus({ preventScroll: true }); } catch (e) { f.focus(); } if (k.sel) try { f.setSelectionRange(k.sel[0], k.sel[1]); } catch (e) { /* 按鈕沒有選取 */ } } }
    const A = S.add; if (A && A.reveal) { const r = d.querySelector('.aprow.open') || d.querySelector('.apsubs'); if (r && r.scrollIntoView) r.scrollIntoView({ block: 'nearest' }); A.reveal = null; }
  }
  // S1b 5:模式對照表(示意 cmpTable):每種模式能做什麼,選中那一欄整欄亮、表頭可以點來選
  const AP_PERMS = [['chat', '對話、叫別席'], ['code', '寫程式'], ['own', '讀寫自己資料夾的檔'], ['cmd', '跑名單內的指令'], ['persona', '寫回人格'], ['brain', '更新第二大腦'], ['any', '讀寫資料夾外的檔'], ['anycmd', '跑任何指令'], ['net', '自己連外網⁶'], ['push', '推送 / 開 PR'], ['host', '連主機']];
  const AP_LEVEL = { host: { chat: 1, own: 1, net: 1, host: 1, persona: 'p', brain: 't' }, safe: { chat: 1, persona: 'p', net: 'n' }, work: { chat: 1, code: 1, own: 1, cmd: 1, persona: 'p', brain: 't', net: 'n' }, dgrs: { chat: 1, code: 1, own: 1, cmd: 1, persona: 'p', brain: 1, any: 1, anycmd: 1, net: 1, push: 1 } };
  const ICO_SRV = '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3" y="13" width="18" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
  // 管理主機那一顆:能開(之後 S2 接上)才亮;這一版 AIB 還沒有 ⇒ 灰、寫原因,不消失
  function apHostCard(A, pv) { return `<button type="button" class="amd off" data-addm="host" disabled aria-disabled="true"><span class="amh">${ICO_SRV}<b>${AP_HOST_NAME}</b></span><i>只管你選的主機,${(typeof fpSay === 'function' ? fpSay : (t) => t)('每台按一次指紋')} · 這一版還沒有</i><em class="adang d3">危險程度:高</em></button>`; }
  const AP_HOST_NAME = '管理主機';
  // 格子小註與上標(示意 CELL_NOTE / cell):¹ 帶倉免 ² 提案 ³ 這一版還沒有 ⁴ 另開
  const AP_NOTE = { 'work:code': '自己那份', 'dgrs:code': '任何地方', 'host:host': '你選的那幾台', 'host:own': '放腳本、紀錄', 'dgrs:brain': '直接寫檔' };
  // 我的 AI 夥伴(示意 #seatsBox):整塊預設收合;逐位展開看它的模式與對照表。
  // 這一版 AIB 還不能在這裡改已開那位的權限、也還沒有「AI 要連本機 / 內網」的請求卡與一直允許清單 ⇒ 畫出位置、寫這一版還沒有,不假裝能改
  const AP_MINE_NO = '在這裡改已開那位的權限:這一版 AIB 還沒有;要換模式先收掉這一位、再照新的模式開';
  const AP_ASK_NO = 'AI 要連本機或內網時,會在這裡彈卡給你選「允許這一次 / 一直允許 / 拒絕」:這一版 AIB 還沒有(現在一律擋)';
  // c2f0b IFACE-passphrase r2(接線片 6573c52350,主線還沒有):系統本人驗證不能用時的密語備援。頁面只讀 passphrase_state 顯示一行;
  // 設定與輸入都在系統安全輸入窗(網頁不收、不存、不顯示密語);鎖住、剩幾次照 aibd 給的欄位,頁面不自己算
  const PW_ACT = 'passphrase_state';
  const PW_WHY = { passphrase_built_in_page_only: '只有這台電腦內建的頁面看得到', passphrase_unavailable: 'AIB 還沒準備好,稍後再看' };
  async function pwLoad() {
    const W = (S.pw = S.pw || {}); if (W.busy || hasAct(PW_ACT) !== true) return;
    W.busy = true;
    try { const r = await act(PW_ACT, {}); W.st = r && typeof r === 'object' ? r : null; W.err = null; }
    catch (e) { W.err = PW_WHY[rgCode(e)] || errText(e); }
    W.at = Date.now(); W.busy = false; render();
  }
  const pwAt = (s) => new Date(Number(s) * 1000).toLocaleString('zh-TW', { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  function pwText(p) {
    if (p.swapped === true) return { bad: true, text: '密語紀錄被換過,請到設定窗重設;重設前不收密語' };
    if (p.available !== true) return { text: (typeof fpWin === 'function' && fpWin()) ? '這台用 Windows Hello 確認是你本人,不用密語' : '這台用系統本人驗證(指紋或登入密碼),不用密語' };
    if (p.set !== true) return { text: '密語備援還沒設定;要設定請到系統的設定窗(網頁不收密語)' };
    const at = Number(p.set_at) > 0 ? '密語已於 ' + pwAt(p.set_at) + ' 設定' : '密語已設定';
    if (p.locked === true) { const u = Number(p.locked_until), m = Math.ceil((u * 1000 - Date.now()) / 60000); return { bad: true, text: at + ' · 鎖住了' + (u > 0 ? ',到 ' + pwAt(u) + (m > 0 ? '(約 ' + m + ' 分)' : '') : '') + ',鎖住時不收密語' }; }
    const w = Number(p.wrong) || 0; return { text: at + (w > 0 ? ' · 連錯 ' + w + ' 次,還能錯 ' + Math.max(0, Number(p.tries_left) || 0) + ' 次' : '') };
  }
  function pwHtml() {
    if (hasAct(PW_ACT) !== true) return '<div class="apn">密語備援:這一版 AIB 還沒有</div>';
    const W = (S.pw = S.pw || {});
    if (!W.busy && Date.now() - (W.at || 0) > 60000) setTimeout(pwLoad, 0); // 打開時讀、之後一分鐘內不重讀
    if (W.err) return `<div class="trm bad">密語狀態讀不到:${esc(W.err)}</div>`;
    if (!W.st) return '<div class="apn">讀取中…</div>';
    const t = pwText(W.st); return `<div class="${t.bad ? 'trm bad' : 'apn'}">${esc(t.text)}</div>`;
  }
  // c2f0b S2-2(頭 1000abaa95,等 A12 進整合線;主線還沒有):夥伴提案改自己的人格卡,內建頁列出(persona_proposals)、整段差異照純文字畫(不當 HTML / markdown)。
  // 核准 = persona_propose_approve {request_id, diff_sha} 走 fingerAct(202 + 驗證窗,窗上對照碼 = diff12)⇒ 200 {state:"written", new_sha};不要 = persona_propose_deny {ids}。act 沒有 ⇒ 這一版還沒有
  const PP_ACTS = { list: 'persona_proposals', deny: 'persona_propose_deny', approve: 'persona_propose_approve' };
  const PP_WHY = { request_not_found: '找不到這份提案(可能已逾時或被撤回)', request_not_waiting: '這份提案已經不在等了(撤回、拒絕、逾時或正在寫)', request_changed: '提案跟你看到的不一樣了,請重新看一次', persona_changed: '這張人格卡剛被改過或登記表剛重掃,這份提案作廢;請夥伴重掃一次卡片後再提', persona_archived: '這個人格已封存,寫不回去', persona_aliased: '這個人格已併到別的人格,寫不回去', persona_none: '找不到這個人格', persona_unreadable: '讀不到人格卡,沒有寫', persona_registry_edited: '人格清單檔被手動改過,沒有寫;先看一下清單', persona_registry_changed: '人格清單剛被改過,沒有寫;再按一次', persona_built_in_page_only: '只有這台電腦內建的頁面能核准或拒絕', persona_write_mismatch: '寫入核對不一致,沒有寫成', persona_write_io: '寫入失敗,沒有寫成', persona_path_outside: '人格卡不在人格資料夾裡,沒有寫', persona_path_symlink: '人格卡是捷徑檔,沒有寫', request_not_writing: '寫入被打斷,沒有寫成', owner_check_not_approved: '指紋沒過,沒有寫回;提案還在,可以再按一次', item_off: '這一項被關掉了', not_done: 'AIB 沒有回「寫好了」' };
  const PP_GONE = new Set(['request_not_found', 'request_not_waiting', 'request_changed', 'persona_changed', 'persona_archived', 'persona_aliased', 'persona_none']);
  const ppHas = (k) => hasAct(PP_ACTS[k]) === true;
  const ppWhy = (e) => (typeof fpSay === 'function' ? fpSay : (t) => t)(PP_WHY[rgCode(e)] || errText(e)); // f163c eddb7acca1 L1:五個出口也跟平台走
  const ppCard = (rid) => ((S.pp && S.pp.items) || []).find((x) => x && x.request_id === rid) || null;
  async function ppLoad() {
    const P = (S.pp = S.pp || {}); if (P.busy || !ppHas('list')) return;
    P.busy = true;
    try { const r = await act(PP_ACTS.list, {}); P.items = (Array.isArray(r && r.proposals) ? r.proposals : []).filter((x) => x && x.request_id && x.diff_sha).slice(0, 20); P.err = null; }
    catch (e) { P.items = P.items || []; P.err = ppWhy(e); }
    P.at = Date.now(); P.busy = false; render();
  }
  function ppLeft(exp) { const ms = Number(exp) * 1000 - Date.now(); return !Number.isFinite(ms) ? '' : ms <= 0 ? '已逾時' : '約 ' + Math.max(1, Math.round(ms / 60000)) + ' 分後逾時'; }
  function ppCardHtml(p) {
    const P = S.pp || {}, rid = esc(p.request_id), add = Number(p.added) || 0, del = Number(p.removed) || 0, canOk = ppHas('approve');
    const lines = (Array.isArray(p.diff) ? p.diff : []).map((l) => { const op = l && l.op; return `<div class="rgdl ${op === 'add' ? 'add' : op === 'del' ? 'del' : ''}">${op === 'add' ? '+ ' : op === 'del' ? '− ' : '  '}${esc(rgShow(String((l && l.text) || '')))}</div>`; }).join('');
    const why = p.why ? `<div class="netwhy"><i>夥伴寫的理由(沒驗證):</i>${esc(String(p.why))}</div>` : '';
    return `<div class="netc ppc"><div class="neth"><b>${esc(p.seat_id ? fpAt(p.seat_id) : '夥伴')}</b> 想改人格卡「${esc(String(p.persona_name || p.persona_uid || ''))}」</div><div class="netd">+${add} −${del} 行 · 對照碼 <code>${esc(String(p.diff12 || String(p.diff_sha).slice(0, 12)))}</code></div>${why}<div class="rgdiff ppdiff">${lines || '<div class="apn">沒有差異內容</div>'}</div><div class="apn">${esc(ppLeft(p.expires_at))}${canOk ? ';核准會跳驗證窗,窗上的對照碼要跟這裡一樣' : ''}</div><div class="ptact"><button type="button" class="go" data-ppok="${rid}" ${P.ans || !canOk ? 'disabled' : ''}>${canOk ? '核准寫回,去驗證' : '核准寫回:這一版 AIB 還沒有'}</button><button type="button" class="k" data-ppno="${rid}" ${P.ans || !ppHas('deny') ? 'disabled' : ''}>不要</button></div></div>`;
  }
  function ppHtml() {
    if (!ppHas('list')) return '<div class="apn">這一版 AIB 還沒有</div>';
    const P = (S.pp = S.pp || {});
    if (!P.busy && (!P.items || Date.now() - (P.at || 0) > 60000)) setTimeout(ppLoad, 0); // 打開時讀、之後一分鐘內不重讀(提案 30 分逾時)
    if (!P.items) return '<div class="apn">讀取中…</div>';
    const msg = P.msg ? `<div class="trm${P.msg.bad ? ' bad' : ''}">${esc(P.msg.text)}</div>` : '';
    return (P.items.length ? P.items.map(ppCardHtml).join('') : '<div class="apn">現在沒有夥伴提案改人格卡</div>') + (P.err ? `<div class="trm bad">讀不到:${esc(P.err)}</div>` : '') + msg;
  }
  function ppFail(P, rid, e, pre) {
    P.msg = { bad: true, text: pre + ppWhy(e) };
    if (PP_GONE.has(rgCode(e))) { P.items = (P.items || []).filter((x) => x.request_id !== rid); P.at = 0; }
  }
  async function ppApprove(rid) {
    const P = (S.pp = S.pp || {}), p = ppCard(rid); if (!p || P.ans || !ppHas('approve')) return;
    P.ans = rid; P.msg = { text: FP_SEE }; render();
    try {
      const r = await fingerAct(PP_ACTS.approve, { request_id: rid, diff_sha: String(p.diff_sha) });
      if (!(r && r.state === 'written')) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      P.items = (P.items || []).filter((x) => x.request_id !== rid);
      P.msg = { text: '已寫回「' + String(p.persona_name || p.persona_uid || '') + '」的人格卡' + (r.new_sha ? '(新版 ' + String(r.new_sha).slice(0, 12) + ')' : '') };
    } catch (e) { ppFail(P, rid, e, '沒有寫回:'); }
    P.ans = null; render();
  }
  async function ppDeny(rid) {
    const P = (S.pp = S.pp || {}), p = ppCard(rid); if (!p || P.ans || !ppHas('deny')) return;
    P.ans = rid; P.msg = null; render();
    try {
      const r = await act(PP_ACTS.deny, { ids: [rid] }), o = (Array.isArray(r && r.results) ? r.results : []).find((x) => x && x.request_id === rid);
      if (!o || o.code || !o.state) { const cd = (o && o.code) || 'not_done'; throw Object.assign(new Error(cd), { code: cd }); }
      P.items = (P.items || []).filter((x) => x.request_id !== rid); P.msg = { text: '已拒絕 ' + (p.seat_id ? fpAt(p.seat_id) : '夥伴') + ' 的人格卡提案' };
    } catch (e) { ppFail(P, rid, e, '沒有拒絕:'); }
    P.ans = null; render();
  }
  // 3e53b IFACE-netcard r0(A6c):夥伴連內網、又不在它的一直允許清單 ⇒ aibd 把連線掛著出一張卡(120 秒沒選 = 拒絕)。
  // 選項(id + words + needs_owner)照 aibd 給的順序畫,頁面不自己寫字、不送時段;要驗證的走 202 + 等你驗證卡。act 沒有 ⇒ 照舊寫這一版還沒有
  const NET_ACTS = { list: 'net_card_list', answer: 'net_card_answer', allow: 'net_allow_list', revoke: 'net_allow_revoke' };
  const NET_WHY = { not_on_list: '這個位址已經不在清單上(可能已收回或這一位重開過)', page_only: '只有這台電腦內建的頁面能回答', net_card_expired: '這張卡已經過了 120 秒,連線被拒了', net_card_used: '這張卡已經回答過了', net_card_unknown: '找不到這張卡(可能已經逾時)', not_a_choice: '沒有這個選項', owner_check_not_approved: '指紋沒過,這張卡只剩拒絕;夥伴再連會出新的一張' };
  const NET_HINT = '這只是提示,夥伴也可能不經過這裡直接連';
  // 3e53b(f163c REVIEW-sandboxnet19 N2):允許綁「位址 + 埠」;換位址的網站(CDN)會一直出卡,一直允許也只記這一個位址
  const NET_BIND = '允許是綁這個「位址 + 埠」:像 CDN 這種每次換位址的網站會一直出卡,選「一直允許」也只記這一個位址';
  const NET_BIND_NA = '允許是綁這個「位址 + 埠」:像 CDN 這種每次換位址的網站會一直出卡'; // 卡上沒有一直允許時不提它
  // ebab8 DECISION-council-netallow-store r1:一直允許綁人格 + 這台、每筆最長 30 天(aibd 給 always_days 就用它的);頁面字照實寫
  const netAlwaysDays = (c) => { const n = Number(c && c.always_days); return Number.isInteger(n) && n > 0 && n <= 3650 ? n : 30; };
  const netPersonaSeats = (uid) => (uid ? (S.seats || []).filter((x) => x && x.persona_uid === uid && String(x.state || '').toLowerCase() !== 'archived').length : 0);
  const netAlwaysWords = (o, c) => String(o.words || '') + (o.id === 'always' && !/\d+\s*天/.test(String(o.words || '')) ? '(每 ' + netAlwaysDays(c) + ' 天再確認一次)' : '');
  const netAlwaysWho = (c) => '綁人格「' + String(c.persona_name || c.persona_uid || '這個人格') + '」:現在 ' + netPersonaSeats(c.persona_uid) + ' 位,之後開的同人格也算;合併或封存這個人格就收回';
  // ae921 post-b4(c2f0b REVIEW-netallow-wire M2 / M3):aibd 簽不出一直允許(沒開硬體本人驗證)就不給這個選項 ⇒ 頁面寫原因;清單每列帶 state,不是 ok 的標紅寫原因、不藏
  const NET_NO_ALWAYS = '沒有「一直允許」:這台 AIB 沒開硬體本人驗證(配對的 Touch ID / Windows Hello 金鑰),一直允許的紀錄簽不出來;只能選有時限的';
  const NET_ST = { expired: '已過期(超過 30 天或到期),不算數;要用就再允許一次', class_changed: '這個位址現在不算內網了,不算數', persona_inactive: '這個人格現在沒在用(封存或合併了),不算數', bad_sig: '簽章對不上,不算數;建議收回', revoked_reappeared: '收回過又出現,不算數;建議收回' };
  const netStWhy = (x) => (x && x.state !== undefined && x.state !== 'ok' ? NET_ST[x.state] || '不算數:' + String(x.state) : '');
  const netMs = (v) => (typeof v === 'number' ? v * (v < 1e12 ? 1000 : 1) : Date.parse(String(v || '')));
  const netPName = (uid) => { const p = (typeof personas === 'function' ? personas() : []).find(([u]) => u === uid); return p ? String(p[1]) : ''; };
  const netHas = (k) => hasAct(NET_ACTS[k]) === true;
  const netWhy = (e) => (typeof fpSay === 'function' ? fpSay : (t) => t)(NET_WHY[rgCode(e)] || errText(e));
  const netCard = (rid) => ((S.net && S.net.items) || []).find((x) => x && x.request_id === rid) || null;
  async function netLoad() {
    const N = (S.net = S.net || {}); if (N.busy || !netHas('list')) return;
    N.busy = true;
    try { const r = await act(NET_ACTS.list, {}); N.items = Array.isArray(r && r.items) ? r.items.slice(0, 20) : []; N.err = null; }
    catch (e) { N.items = N.items || []; N.err = netWhy(e); }
    N.busy = false; render();
  }
  function netTick() { // 「需要你決定」打開時每 5 秒讀一次(卡 120 秒就逾時)
    if (S.netTimer) return;
    S.netTimer = setTimeout(async () => { S.netTimer = null; if (S.add && S.add.mine && netHas('list')) { await netLoad(); netTick(); } }, 5000);
  }
  // 3e53b IFACE-netcard r1 第 27 行(f163c N3):GET /api/v1/seats 頂層帶 net_cards:<未決張數>(只給數字)。>0 ⇒「+ AI 夥伴」與「我的 AI 夥伴」亮紅點;
  // 張數變多而面板沒開 ⇒ 提示列寫一句;分頁在背景且瀏覽器已允許通知才發系統通知(不主動問權限)。沒人看到 ⇒ 120 秒後拒,這只是方便、不是防線
  const NET_NAG = '有夥伴要連網,需要你決定';
  const netDot = () => (S.netCards > 0 ? `<i class="netdot" title="${esc(NET_NAG)}" aria-label="${S.netCards} 張連線要你決定">${S.netCards > 9 ? '9+' : S.netCards}</i>` : '');
  function netSeen(n) {
    const prev = S.netCards || 0; S.netCards = typeof n === 'number' && Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
    if (!(S.netCards > prev)) return;
    if (S.add && S.add.mine) { if (netHas('list')) netLoad(); return; }
    say(NET_NAG + '(' + S.netCards + ' 張,點「+ AI 夥伴」看)', 'warn');
    try { if (typeof document !== 'undefined' && document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') new Notification('AIB', { body: NET_NAG, tag: 'aib-net-card' }); } catch (e) { /* 通知不能用就只靠紅點 */ }
  }
  const netLeft = (exp) => { const s = Math.ceil((exp - Date.now()) / 1000); return s <= 0 ? '已逾時,拒絕了' : s <= 10 ? '快逾時了(約 ' + s + ' 秒),沒選就拒絕' : '還有約 ' + s + ' 秒,沒選就拒絕'; };
  function netCardHtml(c) {
    const N = S.net || {}, se = seatById(c.seat_id), exp = netMs(c.expires_at), left = Number.isFinite(exp) ? Math.max(0, Math.ceil((exp - Date.now()) / 1000)) : null;
    const net = S.version && S.version.capabilities && S.version.capabilities.net, pv = net && se ? net[String(se.provider || 'claude').toLowerCase()] : null, cell = pv && se ? pv[se.mode] : '';
    const why = c.source === 'net_ask' && c.why ? `<div class="netwhy"><i>夥伴寫的理由:</i>${esc(String(c.why))}</div>` : '';
    // f163c r5km L1:驗證窗開過(沒過)⇒ aibd 那張卡只剩拒絕(別的選項回 net_card_used),只畫拒絕
    const asked = !!(N.asked && N.asked[c.request_id]);
    const ch = (Array.isArray(c.choices) ? c.choices : []).filter((o) => !asked || (o && o.id === 'deny')).map((o) => `<button type="button" class="k" data-netans="${esc(c.request_id)}|${esc(o.id)}" ${N.ans ? 'disabled' : ''}>${esc(netAlwaysWords(o, c))}${o.needs_owner ? ' · 要驗證' : ''}</button>`).join('');
    return `<div class="netc"><div class="neth"><b>${se ? '@' + esc(seatShort(se)) : esc(String(c.seat_id || ''))}</b>${c.persona_name ? ' ' + esc(c.persona_name) : ''} 想連內網</div><div class="netd">${c.dest_name ? esc(c.dest_name) + ' ' : ''}<code>${esc(String(c.dest_ip || ''))}:${esc(String(c.dest_port || ''))}</code></div>${why}${cell === 'hint' ? `<div class="apn apwarn">${esc(NET_HINT)}</div>` : ''}${c.risk === 'high' ? `<div class="apn apwarn">連到這台電腦自己的服務(在聽的程式:${esc(String(c.listener || '查不到'))});每個允許都要驗證,沒有「一直允許」</div>` : ''}${!asked && c.risk !== 'high' && c.category !== 'local_self' && Array.isArray(c.choices) && c.choices.length && !c.choices.some((o) => o && o.id === 'always') ? `<div class="apn">${esc(NET_NO_ALWAYS)}</div>` : ''}<div class="apn"${left === null ? '>沒選就拒絕' : ` data-netexp="${exp}">${netLeft(exp)}`}</div><div class="ptact">${ch}</div>${!asked && (c.choices || []).some((o) => o && o.id !== 'deny' && o.id !== 'once') ? `<div class="apn">${esc((c.choices || []).some((o) => o && o.id === 'always') ? NET_BIND : NET_BIND_NA)}</div>` : ''}</div>`;
  }
  function netAskHtml() {
    if (!netHas('list')) return `<div class="apn">${esc(AP_ASK_NO)}</div>`;
    const N = (S.net = S.net || {});
    if (!N.items) { if (!N.busy) setTimeout(netLoad, 0); netTick(); return '<div class="apn">讀取中…</div>'; }
    netTick();
    const msg = N.msg ? `<div class="trm${N.msg.bad ? ' bad' : ''}">${esc(N.msg.text)}</div>` : '';
    return (N.items.length ? N.items.map(netCardHtml).join('') : '<div class="apn">現在沒有要你決定的連線</div>') + (N.err ? `<div class="trm bad">讀不到:${esc(N.err)}</div>` : '') + msg;
  }
  async function netAnswer(rid, choice) {
    const N = (S.net = S.net || {}), c = netCard(rid); if (!c || N.ans) return;
    const o = (c.choices || []).find((x) => x && x.id === choice); if (!o || (N.asked && N.asked[rid] && choice !== 'deny')) return;
    N.ans = rid; N.msg = o.needs_owner ? { text: FP_SEE } : null; render();
    try {
      // f163c r12 N1:每個選項都走 fingerAct(aibd 回 202 就開驗證窗、不是 202 原樣回來),needs_owner 只決定先不先說「看指紋」
      const r = await fingerAct(NET_ACTS.answer, { request_id: rid, choice });
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      N.items = (N.items || []).filter((x) => x.request_id !== rid);
      N.msg = { text: (choice === 'deny' ? '拒絕了' : '允許了:' + o.words) + '(' + String(c.dest_name || c.dest_ip || '') + ')' };
      if (choice === 'always' && N.allow) delete N.allow[c.seat_id];
    } catch (e) { const cd = rgCode(e); N.msg = { bad: true, text: '沒有送出:' + netWhy(e) }; if (cd === 'owner_check_not_approved') (N.asked = N.asked || {})[rid] = true; if (cd === 'net_card_expired' || cd === 'net_card_used' || cd === 'net_card_unknown') N.items = (N.items || []).filter((x) => x.request_id !== rid); }
    N.ans = null; render();
  }
  async function netAllowLoad(sid) {
    const N = (S.net = S.net || {}), A = (N.allow = N.allow || {});
    try { const r = await act(NET_ACTS.allow, { seat_id: sid }); A[sid] = (Array.isArray(r && r.items) ? r.items : []).filter((x) => x && (x.seat_id === undefined || x.seat_id === sid)).slice(0, 50); }
    catch (e) { A[sid] = { err: netWhy(e) }; }
    render();
  }
  function netAllowHtml(sid) {
    if (!netHas('allow')) return '<div class="apn">這一版 AIB 還沒有</div>';
    const N = (S.net = S.net || {}), A = (N.allow = N.allow || {}), L = A[sid];
    if (L === undefined) { A[sid] = 'loading'; setTimeout(() => netAllowLoad(sid), 0); return '<div class="apn">讀取中…</div>'; }
    if (L === 'loading') return '<div class="apn">讀取中…</div>';
    const msg = N.amsg && N.amsg.seat === sid ? `<div class="trm${N.amsg.bad ? ' bad' : ''}">${esc(N.amsg.text)}</div>` : '';
    if (!Array.isArray(L)) return `<div class="trm bad">讀不到:${esc(L.err || '')}</div>` + msg;
    const rv = netHas('revoke');
    return (L.length ? `<div class="netal">${L.map((x) => { const ipp = x.ip !== undefined && x.port !== undefined, un = x.until !== undefined ? x.until : x.expires_at, bad = netStWhy(x), pn = x.persona_uid ? netPName(x.persona_uid) || String(x.persona_uid) : ''; return `<div class="netar${bad ? ' bad' : ''}"><span><code>${esc(ipp ? String(x.ip) + ':' + String(x.port) : String(x.addr || ''))}</code>${un ? ` <span class="apn">到 ${esc(fpWhen(un))} 要再確認</span>` : ''}${pn ? ` <span class="apn">綁人格「${esc(pn)}」</span>` : ''}${bad ? `<span class="netbad">${esc(bad)}</span>` : ''}</span>${rv && ipp ? `<button type="button" class="k" data-netrv="${esc(sid)}|${esc(String(x.ip))}|${esc(String(x.port))}" ${N.rv ? 'disabled' : ''}>收回</button>` : ''}</div>`; }).join('')}</div>` : '<div class="apn">沒有一直允許的位址</div>') + '<div class="apn">「到這一位關掉」那種不在這裡:這一位結束或重開就沒了</div>' + msg;
  }
  async function netRevoke(sid, ip, port) {
    const N = (S.net = S.net || {}); if (!netHas('revoke') || N.rv) return;
    N.rv = true; render();
    try { const r = await act(NET_ACTS.revoke, { seat_id: sid, ip, port: Number(port) }); N.amsg = { seat: sid, text: '已收回 ' + ip + ':' + port + ',正在連的已斷(' + (Number(r && r.closed) || 0) + ' 條)' }; }
    catch (e) { N.amsg = { seat: sid, bad: true, text: '沒有收回:' + netWhy(e) }; }
    N.rv = false; if (N.allow) delete N.allow[sid]; render();
  }
  // 6770f IFACE-merge r0 §6:夥伴可帶 next {persona_next, brain_next, batch_id} ⇒「下次開換成 X」;開 / 接回時 aibd 自己套用,頁面不送
  function mgNextHtml(x) {
    const n = x && x.next; if (!n || typeof n !== 'object') return '';
    const pu = n.persona_next ? String(n.persona_next) : '', p = pu ? ((typeof personas === 'function' ? personas() : []).find(([u]) => u === pu) || [pu, pu])[1] : '', b = n.brain_next ? String(n.brain_next) : '';
    if (!p && !b) return '';
    return `<div class="apn apnext">下次開換成 ${[p ? '人格「' + esc(p) + '」' : '', b ? '第二大腦「' + esc(b) + '」' : ''].filter(Boolean).join('、')}</div>`;
  }
  function apMine(A) {
    const L = present().filter((x) => !isArch(x)), open = !!A.mine;
    const rows = L.map((x) => { const id = x.seat_id, on = A.mineSeat === id, m = AP_MODE[x.mode] ? x.mode : 'safe';
      return `<div class="apmst ${on ? 'open' : ''}"><button type="button" class="apmrh" data-apms="${esc(id)}" aria-expanded="${on}"><span class="apmav" aria-hidden="true">✳</span><span class="apmnm"><b>@${esc(seatShort(x))}</b><i>${esc(x.persona_name || '')}</i></span><em class="adang d${m === 'dgrs' ? 4 : m === 'work' ? 2 : 1}">${esc(AP_MODE[m][0])}</em><i class="apchev" aria-hidden="true">⌄</i></button>${mgNextHtml(x)}${on ? `<div class="apmrb">${apCmp({ mode: m }, { ro: true })}${m === 'host' ? hsLogHtml(id) : ''}<div class="aplab">套用我的權限模板</div>${ptSeatBox(x) || `<div class="apn apwarn">${esc(AP_MINE_NO)}</div>`}<div class="aplab">一直允許的位址</div>${netAllowHtml(id)}</div>` : ''}</div>`; }).join('');
    return `<div class="apmine ${open ? 'open' : ''}"><button type="button" class="apmh" data-apmine="1" aria-expanded="${open}"><b>我的 AI 夥伴 · 隨時改權限</b>${netDot()}<span class="apex">${L.length} 位在工作</span><i class="apchev" aria-hidden="true">⌄</i></button>${open ? `<div class="apmb"><div class="apask"><div class="aplab">⚠️ 需要你決定</div>${netAskHtml()}</div><div class="apask"><div class="aplab">✍️ 夥伴想改自己的人格卡</div>${ppHtml()}</div><div class="apask"><div class="aplab">🔑 密語備援</div>${pwHtml()}</div>${rows || '<div class="apn">現在沒有在工作的夥伴</div>'}</div>` : ''}</div>`;
  }
  // 合併類似(示意 mergeBtn / mergePanel / mergePreview):人格 / 第二大腦 / 角色,自動找出名字很像的,一組一組選要留哪個、哪些不併,再看預覽。
  // 這一版 AIB 還沒有合併的動作 ⇒ 找、選、預覽都照做(只讀),最後「確認合併」寫這一版還沒有、不送
  const MG_KN = { persona: '人格', brain: '第二大腦', role: '角色' };
  const MG_NO = '合併:這一版 AIB 還沒有;現在可以先封存用不到的那個(人格 / 第二大腦 / 角色列的封存)';
  const mgNorm = (x) => String(x || '').toLowerCase().replace(/[-_]\d+$/, '').replace(/專案|project/g, '').replace(/[\s.·()()|\-—–_、,,:]/g, '');
  function mgScore(a, b) {
    const x = mgNorm(a), y = mgNorm(b); if (!x || !y) return 0; if (x === y) return 1;
    const [sh, lo] = x.length <= y.length ? [x, y] : [y, x];
    if (sh.length >= 4 && lo.includes(sh) && sh.length / lo.length >= 0.5) return 0.9;
    const bi = (z) => { const o = new Set(); for (let i = 0; i < z.length - 1; i++) o.add(z.slice(i, i + 2)); return o; };
    const P = bi(x), Q = bi(y); let n = 0; P.forEach((g) => { if (Q.has(g)) n++; });
    return P.size + Q.size ? (2 * n) / (P.size + Q.size) : 0;
  }
  function mgItems(kind) {
    const live = present();
    if (kind === 'persona') return personas().map(([uid, name]) => ({ uid, name, use: live.filter((x) => x.persona_uid === uid).length }));
    if (kind === 'brain') return ((S.reg && S.reg.brains) || []).map((b) => ({ uid: b.uid, name: b.name || b.uid, use: 0 }));
    return roleList().map((r) => ({ uid: r.uid, name: r.name, use: 0, lock: !!r.builtin }));
  }
  function mgFind(kind) {
    const L = mgItems(kind).slice(0, 400), hi = new Set(), par = L.map((_, i) => i), fd = (i) => (par[i] === i ? i : (par[i] = fd(par[i])));
    for (let i = 0; i < L.length; i++) for (let j = i + 1; j < L.length; j++) { const v = mgScore(L[i].name, L[j].name); if (v >= 0.7) { par[fd(i)] = fd(j); if (v >= 0.85) { hi.add(i); hi.add(j); } } }
    const M = new Map(); L.forEach((x, i) => { x.hi = hi.has(i); const r = fd(i); if (!M.has(r)) M.set(r, []); M.get(r).push(x); });
    return [...M.values()].filter((g) => g.length > 1 && g.some((x) => !x.lock)).map((g) => {
      const keep = (g.find((x) => x.lock) || g.slice().sort((a, b) => b.use - a.use || /[-_]\d+$/.test(a.name) - /[-_]\d+$/.test(b.name) || mgNorm(b.name).length - mgNorm(a.name).length)[0]).uid;
      const sure = g.every((x) => x.hi), off = {}; if (!sure) g.forEach((x) => { if (x.uid !== keep) off[x.uid] = true; });
      return { items: g, keep, off, sure };
    });
  }
  // 6770f IFACE-merge r0 §1:有 merge_find 就用 aibd 的分組(keep / sure / low / ticked / builtin / used),沒有就照舊頁面自己找;打開面板就重讀一次
  function mgFromAib(r) {
    return (r && Array.isArray(r.groups) ? r.groups : []).slice(0, 100).map((g) => {
      const items = (Array.isArray(g && g.items) ? g.items : []).map((x) => ({ uid: String(x.uid), name: String(x.name || x.uid), use: Number(x.used) || 0, lock: !!x.builtin, hi: !!g.sure }));
      const tk = new Set(Array.isArray(g.ticked) ? g.ticked.map(String) : []), keep = items.some((x) => x.uid === String(g.keep)) ? String(g.keep) : (items[0] && items[0].uid), off = {};
      items.forEach((x) => { if (x.uid !== keep && !tk.has(x.uid)) off[x.uid] = true; });
      return { items, keep, off, sure: !!g.sure, low: typeof g.low === 'number' && Number.isFinite(g.low) ? g.low : null };
    }).filter((g) => g.items.length > 1);
  }
  function mgGroups(kind) {
    if (hasAct('merge_find') !== true) return mgFind(kind);
    const C = (S.mgAib = S.mgAib || {}), v = C[kind];
    if (v === undefined) { C[kind] = null; act('merge_find', { kind }).then((r) => { C[kind] = mgFromAib(r); render(); }).catch((e) => { C[kind] = { err: rgWhy(e) }; render(); }); return []; }
    return Array.isArray(v) ? v : [];
  }
  const mgWait = (kind) => hasAct('merge_find') === true && S.mgAib && !Array.isArray(S.mgAib[kind]) ? (S.mgAib[kind] && S.mgAib[kind].err ? '讀不到:' + S.mgAib[kind].err : '讀取中…') : '';
  // 6770f IFACE-merge r1 §2–§5:有 merge_preview ⇒ 預覽由 aibd 算(steps / effects / other_hosts / 對照碼),只送 keep + merge,其他欄不送 = aibd 預設
  // (名稱照舊、夥伴改用留下的、筆記不搬、封存 30 天、人格卡只留留下那張);merge_run 原樣送回預覽給的 plan + plan_sha,一律走 fingerAct(要驗證才跳窗);
  // 進度讀 merge_log {batch_id}(r2:外層固定 {batches});做完可整筆還原(merge_undo)。沒有這些 act ⇒ 照舊頁面自己的預覽、確認合併寫這一版還沒有
  const MG_WHY = { batch_unknown: '找不到這批合併紀錄', plan_changed: '清單在預覽後變了,請重新預覽', undo_expired: '已經過了還原期限', owner_check_not_approved: '沒有完成驗證,沒有合併' };
  const MG_ST = { pending: '…', run: '…', done: '✓', failed: '✗', undone: '↩' };
  const mgWhy = (e) => MG_WHY[rgCode(e)] || (e && e.body && typeof e.body.message === 'string' && e.body.message) || rgWhy(e);
  const mgGone = (g) => g.items.filter((x) => x.uid !== g.keep && !g.off[x.uid] && !x.lock);
  const mgCanRun = (A) => hasAct('merge_run') === true && !!(A.mx && A.mx.pv && A.mx.pv.plan_sha && A.mx.pv.plan);
  // 6770f IFACE-merge r1 §2「每條可改」:改了才送那一欄,沒改 = aibd 預設(封存 30 天、筆記不搬、人格卡只留留下那張);改完再叫一次預覽
  // 6770f r3:days 只收 7 / 30 / 90 / "forever"(永久);null 跟不送一樣 = 30 天。紀錄的 keep_until 是 null 才表示永久
  const MG_DAYS = [7, 30, 90, 'forever'];
  function mgOptBody(M) {
    const o = M.opt || {}, b = {};
    if (MG_DAYS.includes(o.days)) b.days = o.days;
    if (M.kind === 'persona' && o.card === 'propose') b.card = 'propose';
    if (M.kind === 'brain' && o.notes === 'copy' && !M.ncBad) b.notes = 'copy';
    return b;
  }
  function mgOptHtml(A) {
    const M = A.mx; if (hasAct('merge_preview') !== true || M.done) return '';
    const o = M.opt || {}, d = MG_DAYS.includes(o.days) ? o.days : 30, nc = M.ncBad || null, ncBad = !!nc;
    const days = MG_DAYS.map((n) => `<button type="button" class="k ${d === n ? 'on' : ''}" data-apmgd="${n}" aria-pressed="${d === n}" ${M.pvBusy ? 'disabled' : ''}>${n === 'forever' ? '永久' : n + ' 天'}</button>`).join('');
    const card = M.kind === 'persona' ? `<label class="mgopt"><input type="checkbox" class="ck" data-apmgcard="1" ${o.card === 'propose' ? 'checked' : ''} ${M.pvBusy ? 'disabled' : ''}>合併後開一張「寫回人格」提案,把被併的人格卡內容併進來給你審(不勾 = 只留留下那張)</label>` : '';
    const notes = M.kind === 'brain' ? `<label class="mgopt"><input type="checkbox" class="ck" data-apmgnotes="1" ${o.notes === 'copy' && !ncBad ? 'checked' : ''} ${M.pvBusy || ncBad ? 'disabled' : ''}>把被併大腦的筆記複製進留下的大腦(不勾 = 不搬,原資料夾不動)</label>${ncBad ? `<div class="apn apwarn">這組筆記不能複製:${nc.over_limit ? '超過上限' : '有 ' + Number(nc.cloud_placeholders) + ' 個雲端還沒下載的檔'};不搬,原資料夾不動</div>` : ''}` : '';
    return `<div class="mgopts"><div class="aplab">可以改</div><div class="ptact"><span class="apn">封存多久(期間可整筆還原)</span>${days}</div>${card}${notes}</div>${mgGrpHtml(A)}`;
  }
  // 6770f IFACE-merge r3 §2「每條可改」的 name / seat_to / rebind:受影響的名單照預覽回來、aibd 補好預設的 plan 畫(頁面不自己猜誰受影響);
  // 改了那組才送那一欄,送整張表(plan 那份 + 改的);沒改 = aibd 預設(名稱照舊、夥伴改用留下的、人格改綁留下的大腦)
  const mgPlanG = (M, keep) => { const P = M.pv && M.pv.plan; return (P && Array.isArray(P.groups) ? P.groups : []).find((x) => x && x.keep === keep) || null; };
  // f163c r6 N1:控制字之外,零寬 / 方向字元(看不見、會讓名字看起來跟別的一樣)也拿掉;aibd 端仍會再驗
  const mgClean = (t) => Array.from(String(t || '').replace(new RegExp(AIB_UNSEEN, 'gu'), '').trim()).slice(0, 20).join('');
  const mgOrig = (g) => String((g.items.find((x) => x.uid === g.keep) || {}).name || '');
  function mgGrpBody(M, g) {
    const q = (M.gopt || {})[g.keep] || {}, b = {}, pg = mgPlanG(M, g.keep) || {}, nm = mgClean(q.name);
    if (nm && nm !== mgOrig(g)) b.name = nm;
    for (const [k, f] of [['seat_to', 'persona'], ['rebind', 'brain']]) { const ch = q[k] || {}; if (M.kind === f && Object.keys(ch).length) b[k] = Object.assign({}, pg[k] || {}, ch); }
    return b;
  }
  function mgGrpHtml(A) {
    const M = A.mx; if (hasAct('merge_preview') !== true || M.done || !M.pv || !M.pv.plan) return '';
    const dis = M.pvBusy ? 'disabled' : '', pn = new Map(personas());
    const own = M.kind === 'persona' ? personas().map(([uid, name]) => ({ uid, name })) : M.kind === 'brain' ? ((S.reg && S.reg.brains) || []).map((b) => ({ uid: b.uid, name: b.name || b.uid })) : [];
    return (Array.isArray(M.pv.plan.groups) ? M.pv.plan.groups : []).map((pg) => {
      const g = pg && M.G.find((x) => x.keep === pg.keep); if (!g) return '';
      const q = (M.gopt || {})[g.keep] || {}, gone = new Set((Array.isArray(pg.merge) ? pg.merge : []).map(String)), opts = own.filter((x) => !gone.has(x.uid));
      const sel = (at, key, cur) => { const L = opts.some((x) => x.uid === cur) ? opts : [{ uid: cur, name: cur }].concat(opts); return `<select class="mgin" ${at}="${esc(g.keep)}|${esc(key)}" ${dis}>${L.map((x) => `<option value="${esc(x.uid)}" ${x.uid === cur ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select>`; };
      const nm = `<label class="mgopt">合併後名稱(最多 20 字)<input class="mgin" data-apmgnm="${esc(g.keep)}" maxlength="20" value="${esc(q.name !== undefined ? q.name : String(pg.name || mgOrig(g)))}" ${dis}></label>`;
      const st = M.kind === 'persona' ? Object.keys(pg.seat_to || {}).map((sid) => `<label class="mgopt">${esc(fpAt(sid))} 下次開改用 ${sel('data-apmgst', sid, String((q.seat_to || {})[sid] || pg.seat_to[sid]))}</label>`).join('') : '';
      const rb = M.kind === 'brain' ? Object.keys(pg.rebind || {}).map((pu) => `<label class="mgopt">人格「${esc(pn.get(pu) || pu)}」改綁 ${sel('data-apmgrb', pu, String((q.rebind || {})[pu] || pg.rebind[pu]))}</label>`).join('') : '';
      return `<div class="mgopts mggrp"><div class="aplab">留下「${esc(mgOrig(g))}」這組</div>${nm}${st}${rb}</div>`;
    }).join('');
  }
  function mgGrpChange(A, t) {
    const M = A && A.mx; if (!M || M.done || M.pvBusy || hasAct('merge_preview') !== true) return false; // 算預覽時、送出後不收
    const d = t.dataset, go = (M.gopt = M.gopt || {});
    if (d.apmgnm !== undefined) { const q = (go[d.apmgnm] = go[d.apmgnm] || {}); q.name = mgClean(t.value); mgPreview(A); return true; }
    for (const [k, v] of [['seat_to', d.apmgst], ['rebind', d.apmgrb]]) { if (!v) continue; const i = v.indexOf('|'), keep = v.slice(0, i), q = (go[keep] = go[keep] || {}); (q[k] = q[k] || {})[v.slice(i + 1)] = String(t.value); mgPreview(A); return true; }
    return false;
  }
  function mgOptClick(A, x) {
    const M = A && A.mx; if (!M || M.done || M.pvBusy || hasAct('merge_preview') !== true) return false;
    const o = (M.opt = M.opt || {});
    const d = x.closest('[data-apmgd]'); if (d) { const n = d.dataset.apmgd === 'forever' ? 'forever' : Number(d.dataset.apmgd); if (!MG_DAYS.includes(n) || o.days === n) return true; o.days = n === 30 ? undefined : n; mgPreview(A); return true; }
    const c = x.closest('[data-apmgcard]'); if (c && M.kind === 'persona') { o.card = c.checked ? 'propose' : undefined; mgPreview(A); return true; }
    const nt = x.closest('[data-apmgnotes]'); if (nt && M.kind === 'brain') { o.notes = nt.checked ? 'copy' : undefined; mgPreview(A); return true; }
    return false;
  }
  async function mgPreview(A) {
    const M = A.mx; if (!M || hasAct('merge_preview') !== true) return;
    const R = (M.prev === 'all' ? M.G : [M.G[M.prev]]).filter((g) => g && mgGone(g).length);
    const groups = R.map((g) => Object.assign({ keep: g.keep, merge: mgGone(g).map((x) => x.uid) }, mgOptBody(M), mgGrpBody(M, g))); // 先用上一次預覽的 plan 補整張表,再清掉
    M.pv = null; M.pvErr = ''; M.done = null; if (!R.length) return render();
    const seq = (M.pvSeq = (M.pvSeq || 0) + 1); M.pvBusy = true; render();
    try { const r = await act('merge_preview', { kind: M.kind, groups }); if (seq === M.pvSeq) M.pv = r && typeof r === 'object' ? r : null; }
    catch (e) { if (seq === M.pvSeq) M.pvErr = mgWhy(e); }
    // f163c r5e L1:aibd 說這組筆記不能搬 ⇒ 清掉 copy、記下原因、不帶 copy 再預覽一次(畫面跟送出的 plan 一致)
    if (seq === M.pvSeq && !M.ncBad && M.pv && M.kind === 'brain' && (M.opt || {}).notes === 'copy' && M.pv.notes_check && (M.pv.notes_check.cloud_placeholders > 0 || M.pv.notes_check.over_limit)) { M.ncBad = M.pv.notes_check; M.opt.notes = undefined; M.pvBusy = false; return mgPreview(A); }
    if (seq === M.pvSeq) M.pvBusy = false; render();
  }
  function mgPvHtml(A) {
    const M = A.mx; if (hasAct('merge_preview') !== true) return '';
    if (M.pvBusy) return '<div class="apn">AIB 正在算預覽…</div>';
    if (M.pvErr) return `<div class="trm bad">預覽失敗:${esc(M.pvErr)}</div>`;
    const P = M.pv; if (!P) return '';
    const eff = (Array.isArray(P.groups) ? P.groups : []).map((g) => (Array.isArray(g && g.effects) ? g.effects : []).map((x) => `<div class="mgef"><b>${esc(String(x.tag || ''))}</b>${esc(String(x.text || ''))}</div>`).join('')).join('');
    const st = Array.isArray(P.steps) && P.steps.length ? `<ol class="mgst">${P.steps.map((x) => `<li>${esc(String(x))}</li>`).join('')}</ol>` : '';
    const nc = P.notes_check && (P.notes_check.cloud_placeholders > 0 || P.notes_check.over_limit) ? `<div class="apn apwarn">筆記不能搬:${P.notes_check.over_limit ? '超過上限' : '有 ' + Number(P.notes_check.cloud_placeholders) + ' 個雲端還沒下載的檔'}</div>` : '';
    return `<div class="mgpv"><div class="aplab">AIB 會做這幾步</div>${st}${eff}${nc}${P.other_hosts ? `<div class="apn">${esc(String(P.other_hosts))}</div>` : ''}${P.needs_owner && !M.done ? `<div class="apn">要你本人驗證;系統視窗會寫對照碼 <code>${esc(String(P.window_sha12 || ''))}</code>,跟這裡一樣才按</div>` : ''}</div>`;
  }
  function mgDoneHtml(A) {
    const D = A.mx && A.mx.done; if (!D) return '';
    const b = D.log || {}, steps = Array.isArray(b.steps) ? b.steps : [];
    const head = D.err ? `<div class="trm bad">${esc(D.err)}</div>` : b.state === 'done' ? '<div class="trm">合併好了;工作中的夥伴下次開才換</div>' : b.state === 'failed' ? '<div class="trm bad">合併失敗,已做的步驟都倒回了</div>' : b.state === 'undone' ? '<div class="trm">整筆還原了</div>' : b.state === 'partial_undo' ? '<div class="trm">還原了,但有幾項沒動(見下面)</div>' : '<div class="apn">合併中…</div>';
    const kc = (Array.isArray(D.kept) ? D.kept : []).map((k) => `<div class="apn apwarn">留下的「${esc(String(k.target || ''))}」合併後改過,沒有動它${k.why ? '(' + esc(String(k.why)) + ')' : ''}</div>`).join('');
    const exp = Number(b.keep_until), until = b.keep_until === null ? '永久封存,隨時可整筆還原' : Number.isFinite(exp) && exp > 0 ? '到 ' + new Date(exp * 1000).toLocaleDateString('zh-TW') + ' 前可整筆還原' : '';
    const ub = b.state === 'done' && b.can_undo && hasAct('merge_undo') === true ? `<button type="button" class="k" data-apmgundo="${esc(String(D.batch || ''))}" ${D.busy ? 'disabled' : ''}>整筆還原</button>` : '';
    return `<div class="mgdone">${head}${steps.length ? `<ol class="mgst">${steps.map((s) => `<li>${MG_ST[s.state] || '…'} ${esc(String(s.op || ''))} ${esc(String(s.target || ''))}</li>`).join('')}</ol>` : ''}${kc}${until && b.state === 'done' ? `<div class="apn">${esc(until)}</div>` : ''}${ub ? `<div class="ptact">${ub}</div>` : ''}</div>`;
  }
  async function mgLogPoll(A, batch, n) {
    const M = A.mx; if (!M || !M.done || M.done.batch !== batch) return;
    try { const r = await act('merge_log', { batch_id: batch }), L = r && Array.isArray(r.batches) ? r.batches : []; M.done.log = L.find((x) => x && x.batch_id === batch) || M.done.log; }
    catch (e) { M.done.err = '讀不到進度:' + mgWhy(e); }
    render();
    if (!M.done.err && (!M.done.log || M.done.log.state === 'run') && n < 150) setTimeout(() => mgLogPoll(A, batch, n + 1), 2000);
  }
  async function mgRun(A) {
    const M = A.mx; if (!mgCanRun(A) || (M.done && M.done.busy)) return;
    const P = M.pv; M.done = { busy: true, log: { state: 'run' } }; M.msg = ''; render();
    try {
      const r = await fingerAct('merge_run', { plan_sha: P.plan_sha, plan: P.plan });
      if (r && r.state === 'failed') { M.done = { log: { state: 'failed' }, err: '合併失敗' + (Number.isFinite(Number(r.stopped_at)) && r.stopped_at != null ? '(停在第 ' + Number(r.stopped_at) + ' 步)' : '') + ',已做的步驟都倒回了' + (r.code ? '(' + String(r.code) + ')' : '') }; return render(); }
      if (!r || !r.batch_id) throw Object.assign(new Error('no_batch'), { code: 'no_batch' });
      M.done = { batch: String(r.batch_id), log: { state: String(r.state || 'run') } };
      if (S.mgAib) delete S.mgAib[M.kind];
      if (typeof loadRegistry === 'function') loadRegistry();
      render(); mgLogPoll(A, M.done.batch, 0);
    } catch (e) {
      if (rgCode(e) === 'plan_changed') { M.done = null; M.msg = MG_WHY.plan_changed; mgPreview(A); return; }
      M.done = { log: {}, err: '沒有合併:' + mgWhy(e) }; render();
    }
  }
  async function mgUndo(A, batch) {
    const M = A.mx; if (!M || !M.done || M.done.batch !== batch || M.done.busy || hasAct('merge_undo') !== true) return;
    M.done.busy = true; render();
    try { const r = await fingerAct('merge_undo', { batch_id: batch }); M.done.log = Object.assign({}, M.done.log, { state: String((r && r.state) || 'undone'), can_undo: false }); M.done.kept = Array.isArray(r && r.kept_changed) ? r.kept_changed.slice(0, 50) : []; if (S.mgAib) delete S.mgAib[M.kind]; if (typeof loadRegistry === 'function') loadRegistry(); }
    catch (e) { M.done.err = '沒有還原:' + mgWhy(e); }
    M.done.busy = false; render();
  }
  function mgOf(A, kind) { if (!A.mx || A.mx.kind !== kind || (!A.mx.G.length && !mgWait(kind) && hasAct('merge_find') === true)) A.mx = { kind, G: mgGroups(kind), prev: null }; return A.mx.G; }
  function mgBtn(A, kind) { const n = mgGroups(kind).length; return `<button type="button" class="apmg ${n ? '' : 'none'} ${A.merge === kind ? 'on' : ''}" data-apmg="${kind}" aria-expanded="${A.merge === kind}">⇄ 合併類似${n ? ' · ' + n + ' 組' : ''}</button>`; }
  function mgPanel(A, kind) {
    if (A.merge !== kind) return '';
    const G = mgOf(A, kind), nm = MG_KN[kind], gone = (g) => g.items.filter((x) => x.uid !== g.keep && !g.off[x.uid] && !x.lock);
    if (!G.length) return `<div class="apmgp"><div class="apmgh"><b>合併類似${nm}</b><button type="button" class="k" data-apmgx="1">收起</button></div><div class="apn">${esc(mgWait(kind) || '沒有找到名字很像的' + nm)}</div></div>`;
    if (A.mx.prev != null) {
      const R = (A.mx.prev === 'all' ? G : [G[A.mx.prev]]).filter((g) => gone(g).length), n = R.reduce((a, g) => a + gone(g).length, 0), live = R.reduce((a, g) => a + gone(g).reduce((b, x) => b + x.use, 0), 0);
      return `<div class="apmgp prev"><div class="apmgh"><b>合併預覽 · ${nm}</b><span class="apex">${A.mx.done ? '已送出,看下面進度' : '還沒動任何東西'}</span></div><div class="apn"><b>${R.length}</b> 組 · <b>${n}</b> 個${nm}會併掉 · <b>${live}</b> 位工作中的夥伴受影響(下次開才換,正在跑的不會中途換)</div>${R.map((g) => `<div class="apmgg"><div class="apmgf">${gone(g).map((x) => `<span class="apmgc out">${esc(x.name)}</span>`).join('')}<span aria-hidden="true">→</span><span class="apmgc in">${esc(g.items.find((x) => x.uid === g.keep).name)}</span></div></div>`).join('')}${mgPvHtml(A)}${mgOptHtml(A)}${A.mx.msg ? `<div class="apn apwarn" role="alert">${esc(A.mx.msg)}</div>` : ''}${mgDoneHtml(A)}${A.mx.done ? '' : `<div class="ptact"><button type="button" class="k" data-apmgok="1" ${hasAct('merge_preview') === true && hasAct('merge_run') === true && !mgCanRun(A) ? 'disabled' : ''}>確認,開始合併</button><button type="button" class="k" data-apmgback="1">回上一步</button></div>`}</div>`;
    }
    return `<div class="apmgp"><div class="apmgh"><b>找到 ${G.length} 組類似的${nm}</b><span class="apex">點 ◉ 選要留下的,點名字取消就不併</span><button type="button" class="k" data-apmgx="1">收起</button></div>${G.map((g, gi) => `<div class="apmgg"><span class="apex">${g.sure ? '幾乎一樣 · 預設會併' : '可能類似 · 預設不併,要的自己點'}${g.low != null && g.low < 1 ? ' · 相似度 ' + Math.round(g.low * 100) + '%' : ''}</span>${g.items.map((x) => { const keep = g.keep === x.uid, off = g.off[x.uid] && !keep; return `<div class="apmgi ${keep ? 'keep' : ''} ${off ? 'off' : ''}"><button type="button" class="apmgr" data-apmgk="${gi}|${esc(x.uid)}" aria-label="留下 ${esc(x.name)}" aria-pressed="${keep}" ${g.items.some((y) => y.lock) && !x.lock ? 'disabled' : ''}>${keep ? '◉' : '○'}</button><button type="button" class="apmgn" data-apmgo="${gi}|${esc(x.uid)}" ${keep || x.lock ? 'disabled' : ''} aria-pressed="${!off}">${esc(x.name)}${x.lock ? ' 🔒' : ''}</button><span class="apex">${x.use ? x.use + ' 位在用' : ''}</span></div>`; }).join('')}<div class="ptact"><button type="button" class="k" data-apmgp="${gi}">預覽合併這組</button></div></div>`).join('')}<div class="ptact"><button type="button" class="k" data-apmgp="all">預覽全部合併(${G.length} 組)</button></div></div>`;
  }
  function mgClick(x) {
    const A = S.add; if (!A) return false;
    const b = x.closest('[data-apmg]'); if (b) { const k = b.dataset.apmg; A.merge = A.merge === k ? '' : k; A.mx = null; if (S.mgAib && A.merge) delete S.mgAib[k]; render(); return true; } // 打開就重讀 aibd 分組(+ 新增後)
    if (!A.mx) return false;
    const g = (v) => A.mx.G[Number(String(v).split('|')[0])], u = (v) => String(v).split('|').slice(1).join('|');
    if (x.closest('[data-apmgx]')) { A.merge = ''; A.mx = null; render(); return true; }
    const k = x.closest('[data-apmgk]'); if (k) { const G = g(k.dataset.apmgk); if (G) { G.keep = u(k.dataset.apmgk); delete G.off[G.keep]; } render(); return true; }
    const o = x.closest('[data-apmgo]'); if (o) { const G = g(o.dataset.apmgo), id = u(o.dataset.apmgo); if (G) { if (G.off[id]) delete G.off[id]; else G.off[id] = true; } render(); return true; }
    const p = x.closest('[data-apmgp]'); if (p) { A.mx.prev = p.dataset.apmgp === 'all' ? 'all' : Number(p.dataset.apmgp); A.mx.msg = ''; A.mx.done = null; A.mx.ncBad = null; A.mx.gopt = null; render(); mgPreview(A); return true; }
    if (x.closest('[data-apmgback]')) { A.mx.prev = null; A.mx.msg = ''; A.mx.pv = null; A.mx.pvErr = ''; A.mx.pvBusy = false; A.mx.ncBad = null; A.mx.gopt = null; A.mx.pvSeq = (A.mx.pvSeq || 0) + 1; A.mx.done = null; render(); return true; }
    if (mgOptClick(A, x)) return true;
    const un = x.closest('[data-apmgundo]'); if (un) { mgUndo(A, un.dataset.apmgundo); return true; }
    if (x.closest('[data-apmgok]')) { if (mgCanRun(A)) { mgRun(A); return true; } A.mx.msg = MG_NO; render(); return true; } // 沒有 merge_run 不假裝合併了
    return false;
  }
  // 3e53b 05:5x(sandboxnet r7 §16,頭 1d90ef689):version.capabilities.net = {品牌: {safe|work|dgrs: enforced|hint|off}};
  // 頁面照欄位畫、不自己判平台(代理那片還沒進時 Mac work 也回 off);沒給這格就什麼都不說。Andy 05:5x DECISION 十七:Windows 照實標「只是提示」
  const NET_WORDS = { enforced: '能上網;沙盒夥伴連不到這台電腦上的本機服務', hint: '能上網;網路規則只是提示,擋不住;連得到這台電腦上的本機服務與內網', off: '不能上網' };
  const NET_CELL = { enforced: '✓<small>擋本機</small>', hint: '✓<small>⚠️只是提示</small>', off: '—' };
  function apNet(pv, m) {
    const c = S.version && S.version.capabilities, n = c && c.net && pv ? c.net[pv.id] || c.net[pv.key] : null;
    const v = n && typeof n === 'object' ? String(n[m] || '') : ''; return NET_WORDS[v] ? v : '';
  }
  // 08de5 A14(頭 d3dfd6b22b):capabilities.mode_net = {safe|work|dgrs: {code, words}} 是夥伴開席時被告知的同一句;有就照它寫,沒有才用上面 capabilities.net 的三句
  function apNetWords(pv, m) {
    const c = S.version && S.version.capabilities, w = c && c.mode_net && c.mode_net[m] && c.mode_net[m].words;
    if (typeof w === 'string' && w.trim()) return w.trim().slice(0, 200);
    const v = apNet(pv, m); return v ? NET_WORDS[v] : '';
  }
  function apCmp(A, ok) {
    const M = [['safe', '討論研究'], ['work', '沙盒開發'], ['host', AP_HOST_NAME], ['dgrs', '完全權限']], sel = AP_LEVEL[A.mode] ? A.mode : 'safe', s = (m) => (m === sel ? ' s' : ''), can = (m) => !(ok && ok.ro) && (m === 'safe' || !!(ok && ok[m]));
    // 3e53b:aibd 給了 capabilities.net,「自己連外網」那格就照它(enforced / hint / off),不再寫 🚧
    const cell = (m, k) => { const v = AP_LEVEL[m][k], nt = AP_NOTE[m + ':' + k], nv = k === 'net' && ok && ok.net ? apNet(ok.net, m) : ''; if (nv) return `<i class="${nv === 'hint' ? 'w' : nv === 'enforced' ? 'y' : 'n'}${s(m)}" title="${esc(NET_WORDS[nv])}">${NET_CELL[nv]}</i>`; if (k === 'host' && !v) return `<i class="p${s(m)}">另開⁴</i>`; return v === 'p' ? `<i class="p${s(m)}">提案²</i>` : v === 't' ? `<i class="y${s(m)}">✓<small>🚧³</small></i>` : v === 'n' ? `<i class="y${s(m)}">✓<small>🚧⁷</small></i>` : v ? `<i class="y${s(m)}">✓${nt ? `<small>${nt}</small>` : ''}</i>` : `<i class="n${s(m)}">—</i>`; };
    // 表頭可以點來選;那一顆開不了(預覽區完全權限、這一版還沒有管理主機)就只是字,不能選
    return `<div class="apcmp" role="table" aria-label="模式對照"><span></span>${M.map(([m, n]) => (can(m) ? `<button type="button" class="ch${s(m)}" data-addm="${m}">${esc(n)}</button>` : `<b class="ch off${s(m)}">${esc(n)}</b>`)).join('')}${AP_PERMS.map(([k, n]) => `<span>${n}</span>${M.map(([m]) => cell(m, k)).join('')}`).join('')}<span>開的時候</span><i class="${s('safe').trim()}">免</i><i class="${s('work').trim()}">免¹</i><i class="w${s('host')}">${(typeof fpSay === 'function' ? fpSay : (t) => t)('逐台按指紋')}</i><i class="w${s('dgrs')}">${(typeof fpSay === 'function' ? fpSay : (t) => t)('按指紋')}⁵</i></div><div class="apn apfn">${(typeof fpSay === 'function' ? fpSay : (t) => t)(`¹ 帶倉時也免:倉登記時已按過;沙盒是系統層擋的,不是靠 AI 自己守規矩<br>² 提案 = 它寫好改動,浮出卡片請你看過、按指紋才寫進人格卡<br>³ 🚧 這一版 AIB 還不能寫第二大腦:沙盒擋資料夾外的檔,要由 AIB 代寫進它綁的那一個<br>⁴ 管理主機模式自帶這一項;其他模式要在「可管的主機」另外開,逐台選、每台按一次指紋<br>⁵ 沒有指紋的電腦改成電腦本身的解鎖:Mac 跳登入密碼、Windows 用 Hello PIN<br>⁶ 連外網:外網可以,本機(127.0.0.1)與內網一律擋;某一位要連時彈出卡片給你選<br>⁷ 🚧 這一版還沒有,沙盒連外網做好才有(現在討論研究 / 沙盒開發連不出去)`)}</div>`;
  }
  // S1b A:底部摘要(示意 #sum):品牌 · 模式 · 人格 · 現在幾位在工作
  function apSum(A, pv) {
    const pn = A.persona ? ((personas().find(([u]) => u === A.persona) || [])[1] || A.persona) : '未選人格';
    const run = (S.sessions || []).filter((x) => x.state === 'running' || x.state === 'starting').length, mx = S.maxRun || 8;
    const mn = (A.mates || []).length;
    return `${pv.name} · ${(AP_MODE[A.mode] || AP_MODE.safe)[0]} · ${pn}${mn ? ' + ' + mn + ' 搭檔' : ''} · 現在 ${run}/${mx} 位在工作${run + 1 + mn > mx ? ',會排隊' : ''}${pv.key === 'grok' ? ' · ' + GROK_X : ''}`;
  }
  // 示意 .src:人格 N · 第二大腦 N · 角色 N · 倉 N · 席 N,讀自這台 AIB
  function apSrc() { const R = S.reg || {}; return `人格 ${personas().length} · 第二大腦 ${(R.brains || []).length} · 角色 ${roleList().length} · 倉 ${addRepos().length} · ${apCounts()}`; }
  function apCounts() { const L = S.sessions || []; const run = L.filter((x) => x.state === 'running' || x.state === 'starting').length, q = L.filter((x) => x.state === 'pending').length; return `${run} 位在工作${q ? ' / ' + q + ' 位排隊中' : ''}`; }
  const apCode = (s) => { const c = String((s && s.code) || String((s && s.seat_id) || '').replace(/^(seat_|old_)/, '')).slice(0, 5); return /^[0-9a-f]{5}$/.test(c) ? c : ''; };
  // 接回:從舊夥伴挑一個 ⇒ 帶它的人格、模式,交接單填好 HANDOFF-<席碼>- 開頭。頁面讀不到 pending 的清單(aibd 的 pending_list 只給席用),
  // 所以最新那份的檔名要人補完;沿用現行交接單 / 記憶包的送法(只送檔名,aibd 從 pending 那一層拷)
  // 08de5 S6 handoff_latest(頭 1ff6980ce5,DESIGN-s6-handofflatest r1):{code} ⇒ {handoff: {file, mtime} | null, mempack: {…} | null},只給檔名與時間;
  // 頁面照舊只送檔名給開席(aibd 從 pending 那一層拷)。沒有這個 act 就照舊請人補完檔名
  const HL_FILE = { handoff: /^HANDOFF-[0-9a-f]{5}-[\w.-]{1,80}\.md$/, mempack: /^MEMPACK-[0-9a-f]{5}-[\w.-]{1,80}\.md$/ };
  function hlPick(r, k, code) { const x = r && r[k]; const fl = x && typeof x.file === 'string' ? x.file : ''; return HL_FILE[k].test(fl) && fl.includes('-' + code + '-') ? { file: fl, mtime: Number(x.mtime) || 0 } : null; }
  async function hlLoad(A, code) {
    if (hasAct('handoff_latest') !== true) return;
    A.hl = { code, busy: true }; render();
    let r = null; try { r = await act('handoff_latest', { code }); } catch (e) { r = { err: true }; }
    if (S.add !== A || A.from !== code) return;
    const h = hlPick(r, 'handoff', code), m = hlPick(r, 'mempack', code);
    A.hl = { code, err: !!(r && r.err), h, m };
    if (h && (!A.ho || /HANDOFF-[0-9a-f]{5}-$/.test(A.ho))) A.ho = '~/.ez2-aib/pending/' + h.file;
    if (m && !A.mp) A.mp = '~/.ez2-aib/pending/' + m.file;
    render();
  }
  function hlNote(A) {
    const H = A.hl, t = (x) => { const p = x.mtime ? tparts(x.mtime * 1000) : null; return p ? '(' + p.mo + '/' + p.d + ' ' + p.h + ':' + p.mi + ')' : ''; };
    if (!H || H.code !== A.from) return `<div class="apn">接回 @${esc(A.from)}:把 pending 裡它最新那份交接單的檔名補完整(這一版 AIB 還不能自動找最新那份)</div>`;
    if (H.busy) return '<div class="apn">找 @' + esc(A.from) + ' 最新的交接單…</div>';
    if (H.err) return '<div class="trm bad">讀不到 @' + esc(A.from) + ' 的交接單清單;可以自己補完檔名</div>';
    return `<div class="apn apho">接回 @${esc(A.from)}:${H.h ? '最新交接單 <b>' + esc(H.h.file) + '</b>' + esc(t(H.h)) : '這一席沒有交接單'}${H.m ? ' · 記憶包 <b>' + esc(H.m.file) + '</b>' + esc(t(H.m)) : ''};開好會自動送給新的夥伴先讀</div>`;
  }
  function apBack(A) {
    const L = S.seats.filter((x) => x && !isArch(x) && apCode(x));
    const list = `<div class="apl apbl">${L.length ? L.map((x) => `<button type="button" class="apr ${A.from === apCode(x) ? 'on' : ''}" data-apback="${esc(apCode(x))}"><b>@${esc(seatName(x))}</b><i>${esc(apCode(x))}${x.persona_uid ? ' · ' + esc(((personas().find(([u]) => u === x.persona_uid) || [])[1]) || x.persona_uid) : ''}</i></button>`).join('') : '<div class="apn">這台還沒有舊夥伴可以接回</div>'}</div>`;
    // ebab8 11-23 03:20:示意沒有獨立「接回」鈕 ⇒ 只留大分類「接手舊席」,點了在那一列下面展開(跟小分類同一塊樣式)
    return `<div class="apsubs" role="group" aria-label="接手舊席"><div class="apst">🔁 接手舊席 · 從舊夥伴接著做(帶它的人格與交接單)</div>${list}</div>`;
  }
  // ⚙️ 自訂(示意 chatBox / propose / propHtml / chatDo):說要做什麼 ⇒ 在這頁用現有的人格 / 第二大腦 / 角色 / 專案清單比對,配一組建議。
  // 比對是頁面自己做的(不是 AI 回的,寫明);「帶到下面再改」真的帶進設定;「照這樣開」(由配置助理代送、你驗證後才執行)這一版 AIB 還沒有
  const CH_HELLO = '說說你想讓 AI 幫你做什麼?我用你已經有的人格、第二大腦、角色、專案配一組給你看(這一版是頁面自己比對名字,不是 AI 回的)。';
  const CH_CHIPS = ['幫家具店寫 SEO 文章', '研究競品並做成簡報', '修 AIB 網頁版的 bug,改完給人審', '每天整理新聞,寫進第二大腦'];
  const CH_GO_NO = '照這樣開(配置助理代送、你驗證後才執行):這一版 AIB 還沒有;先按「帶到下面再改」,看過後按底下的「開」';
  const CH_INT = [
    { w: ['seo', '排名', '關鍵字'], kw: ['SEO主管', 'SEO專家', 'SEO'], topic: 'SEO' },
    { w: ['新聞', '情報', '整理'], kw: ['情報', '新聞', '研究'], topic: '新聞' },
    { w: ['研究', '競品', '簡報', '調查'], kw: ['研究專家', '研究'], topic: '研究' },
    { w: ['寫', '文章', '文案', '貼文'], kw: ['寫手', '文案'], topic: '文章' },
    { w: ['bug', '程式', '修', '開發', '網頁版'], kw: ['工程師'], topic: '開發' },
    { w: ['主機', '伺服器', '備份', '封鎖'], kw: ['主機維運', '維運'], topic: '主機' },
  ];
  function chPick(L, name, text, client, kws, need) {
    const t = mgNorm(text); let b = null, v = 0;
    L.forEach((x) => { const n = String(name(x) || ''); if (client && !n.toLowerCase().includes(client.toLowerCase())) return; const ki = kws.findIndex((k) => n.toLowerCase().includes(k.toLowerCase())); const nn = mgNorm(n); let h = 0; for (let i = 0; i < t.length - 1; i++) if (nn.includes(t.slice(i, i + 2))) h++; const sc = (ki >= 0 ? 10 - ki : 0) + h; if (sc > v) { v = sc; b = x; } });
    return v >= need ? b : null;
  }
  // ebab8:公開倉 + 出給所有用戶 ⇒ 不寫死任何客戶名 / 主機代號;「客戶」= 這句話裡跟使用者自己的人格 / 第二大腦 / 專案名重疊最長的一段(≥ 2 字,先去掉意圖字),取不到就空字串
  function chClient(text, names) {
    let t = String(text).toLowerCase();
    for (const x of CH_INT) for (const w of x.w.concat(x.kw, [x.topic])) t = t.split(w.toLowerCase()).join(' ');
    const N = names.map((n) => String(n || '').toLowerCase()).filter(Boolean); let best = '';
    for (const seg of t.split(/[\s,,。、!?!?:;;()()「」]+/)) for (let i = 0; i < seg.length; i++) for (let j = Math.min(seg.length, i + 12); j - i >= 2 && j - i > best.length; j--) { const c = seg.slice(i, j); if (N.some((n) => n.includes(c))) { best = c; break; } }
    // f163c page2r11 N1:小寫後長度可能變(İ 等),位置改在原文上逐段比
    if (!best) return ''; const src = String(text); for (let i = 0; i < src.length; i++) for (let j = i + 1; j <= src.length && j - i <= best.length + 2; j++) if (src.slice(i, j).toLowerCase() === best) return src.slice(i, j);
    return best;
  }
  function chPropose(text) {
    const T = String(text).toLowerCase(), has = (...w) => w.some((x) => T.includes(x.toLowerCase()));
    const host = has('主機', '伺服器', 'ssh');
    const mode = has('部署', '推送', '上線', '發版') ? 'dgrs' : has('程式', 'bug', '修', '開發', '重構') ? 'work' : 'safe';
    const it = CH_INT.find((x) => has(...x.w)) || { kw: ['助理'], topic: '筆記' };
    const PS = personas().map(([uid, name]) => ({ uid, name })), BR = ((S.reg && S.reg.brains) || []).map((b) => ({ uid: b.uid, name: b.name || b.uid }));
    const client = chClient(text, PS.map((p) => p.name).concat(BR.map((b) => b.name), nodeOpts().map((n) => n[1])));
    const per = chPick(PS, (p) => p.name, text, client, it.kw, 8) || (client ? chPick(PS, (p) => p.name, text, '', it.kw, 12) : null);
    const brain = chPick(BR, (b) => b.name, text, client, [it.topic], client ? 1 : 2);
    const RL = roleList(), rr = (en, zh) => RL.find((r) => r.uid === en || r.name === zh);
    const role = has('審', 'review') && !has('給人審', '給別人審') ? rr('reviewer', '審查') : has('驗收', '測試') ? rr('verifier', '驗收') : rr('author', '作者');
    const node = chPick(nodeOpts().map(([id, t]) => ({ id, t })), (n) => n.t, text, '', [], 5);
    const mates = has('審', 'review') ? [PS.find((p) => /驗收審查/.test(p.name)) || PS.find((p) => /審查/.test(p.name))].filter((p) => p && (!per || p.uid !== per.uid)) : [];
    return { text: String(text), mode, host, per, perNew: per ? '' : client + it.kw[0], brain, brainNew: brain ? '' : (client ? client + ' ' : '') + it.topic, role: role || null, node, mates };
  }
  function chProp(pr, i) {
    const ln = (k, v, st) => `<div class="chl"><span>${k}</span><b>${esc(v)}</b><em class="${st === '已有' ? 'ok' : st === '要新建' ? 'w' : ''}">${st}</em></div>`;
    return `<div class="chb">我的建議:用 <b>${esc(AP_MODE[pr.mode][0])}</b>,下面是配好的。</div><div class="chp">${ln('人格', pr.per ? pr.per.name : pr.perNew, pr.per ? '已有' : '要新建')}${ln('第二大腦', pr.brain ? pr.brain.name : pr.brainNew, pr.brain ? '已有' : '要新建')}${ln('角色', pr.role ? pr.role.name : '不掛', '已有')}${ln('模式與權限', AP_MODE[pr.mode][0] + (pr.host ? ' · 管理主機這一版還沒有' : ''), '建議')}${ln('專注已有專案', pr.node ? pr.node.t : '不指定', pr.node ? '已有' : '—')}${pr.mates.length ? ln('一次多 AI', pr.mates.map((m) => m.name).join('、') + '(審查)', '已有') : ''}</div>${pr.perNew || pr.brainNew ? '<div class="apn">「要新建」的這一版不會自動建:帶到下面後在那一列按「+ 新增」</div>' : ''}<div class="ptact"><button type="button" class="k go" data-chdo="${i}|go">照這樣開</button><button type="button" class="k" data-chdo="${i}|edit">帶到下面再改</button></div>`;
  }
  function chBox(A) {
    const L = A.chat || [{ who: 'ai', t: CH_HELLO }], P = A.props || [];
    return `<div class="apsubs apchat" role="group" aria-label="自訂"><div class="apst">⚙️ 自訂 · 說你要什麼</div><div class="chlog" id="chlog">${L.map((m) => `<div class="chm ${m.who}">${m.who === 'ai' ? '<span class="chav" aria-hidden="true">AI</span>' : ''}<div class="chbub">${m.p != null && P[m.p] ? chProp(P[m.p], m.p) : esc(m.t)}</div></div>`).join('')}</div>${L.length < 3 ? `<div class="chips">${CH_CHIPS.map((c) => `<button type="button" class="apc sub chip" data-chchip="${esc(c)}">${esc(c)}</button>`).join('')}</div>` : ''}<div class="chin"><textarea class="apq" data-chin="1" rows="2" placeholder="例如:每天整理新聞,寫進我的第二大腦" aria-label="說你要什麼">${esc(A.chDraft || '')}</textarea><button type="button" class="k go" data-chsend="1">送出</button></div><div class="apn">不想用對話?<button type="button" class="lnk" data-apsub="custom:0">每一項自己挑</button></div></div>`;
  }
  const chEnd = () => { const l = document.getElementById('chlog'); if (l) l.scrollTop = l.scrollHeight; }; // 送出後看到最新那則
  function chSend(A, text) {
    text = String(text || '').trim().slice(0, 400); if (!text) return;
    A.chat = (A.chat || [{ who: 'ai', t: CH_HELLO }]).concat({ who: 'me', t: text }); A.props = A.props || []; A.props.push(chPropose(text));
    A.chat.push({ who: 'ai', p: A.props.length - 1 }); A.chDraft = '';
  }
  function chDo(A, i, how) {
    const pr = (A.props || [])[i]; if (!pr) return;
    if (how === 'go') { A.chat.push({ who: 'ai', t: CH_GO_NO }); A.chEnd = true; return; } // 不假裝送出
    apPick(A, 'custom:0'); A.cat = 'custom';
    A.mode = pr.mode; if (pr.per) { A.persona = pr.per.uid; brainPreselect(A); }
    if (pr.brain && S.reg && S.reg.brainsLive) { A.brain = pr.brain.uid; A.brainTouched = true; }
    A.role = pr.role ? pr.role.uid : ''; A.node = pr.node ? pr.node.id : ''; A.mates = pr.mates.map((m) => m.uid);
    A.chat.push({ who: 'ai', t: '帶到下面「帶出的設定」了,哪一列不對點開來改,改好按「開」。' + (pr.perNew || pr.brainNew ? '「要新建」的在那一列按「+ 新增」。' : '') });
    A.chReveal = true;
  }
  function apTop(A) {
    const R = apRecent();
    const rec = R.length ? `<div class="apblk"><div class="aplab"><span class="apstep">1</span><b>最近選用的方案</b></div><div class="apcs">${R.map((k) => { const x = apSub(k); return `<button type="button" class="apc ${A.plan === k ? 'on' : ''}" data-aprc="${esc(k)}"><b>${esc(x.p.name)}</b><i>${esc(x.C.zh)}</i></button>`; }).join('')}</div></div>` : '';
    // S1b(Andy 11-23 02:59「第二分類 底色要不同」):小分類那塊插在被點那一列的最後一顆後面、跨整列(示意 app.js drawTop 同算法),深底 + 金框 + 小標
    const C = AP_CATS.find((x) => x.id === A.cat && !x.back), BK = A.backOpen ? AP_CATS.find((x) => x.back) : null, sel = C ? AP_CATS.indexOf(C) : BK ? AP_CATS.indexOf(BK) : -1, cols = apCols();
    const at = sel >= 0 ? Math.min(AP_CATS.length - 1, Math.floor(sel / cols) * cols + cols - 1) : -1;
    const subs = C && C.id === 'custom' ? chBox(A) : C ? `<div class="apsubs" role="group" aria-label="${esc(C.zh)} 的方案"><div class="apst">${C.ic} ${esc(C.zh)} 的方案</div><div class="apsl">${C.subs.map((p, i) => `<button type="button" class="apc sub ${A.plan === C.id + ':' + i ? 'on' : ''}" data-apsub="${esc(C.id + ':' + i)}" data-sty="animation-delay:${i * 45}ms"><b>${esc(p.name)}</b><i>${esc(p.what)}${apPkN(p)}</i></button>`).join('')}</div></div>` : BK ? apBack(A) : '';
    const cats = AP_CATS.map((X, k) => `<button type="button" class="apc ${A.cat === X.id || (X.back && A.backOpen) ? 'on' : ''} ${X.back || X.id === 'custom' ? 'util' : ''}" data-apcat="${esc(X.id)}" aria-expanded="${A.cat === X.id || !!(X.back && A.backOpen)}"><span>${X.ic}</span><span class="apct"><em class="${X.id === 'sens' ? 'sens' : ''}">${AP_NUM[X.id] || '—'}</em><b>${esc(X.zh)}</b></span></button>${k === at ? subs : ''}`).join('');
    return `${rec}<div class="apblk"><div class="aplab"><span class="apstep">${R.length ? 2 : 1}</span><b>大分類</b>點一下,下面展開小分類</div><div class="apcs apcats">${cats}</div></div>`;
  }
  // 帶出的設定:每列收合只寫摘要,點開才改(一次開一列)
  function apRow(A, k, label, sum, body) { const on = A.rowOpen === k; return `<div class="aprow ${on ? 'open' : ''}"><button type="button" class="aprh" data-aprow="${k}" aria-expanded="${on}"><span>${label}</span><b>${sum}</b><i class="apchev" aria-hidden="true">⌄</i></button><div class="aprb"><div>${on ? body : ''}</div></div></div>`; }
  // W-10 開席設定引導卡(c2f0b IFACE-w10-session-policy r0 §2;ebab8 19:06 派):沒有人寫開席設定時,「+ AI 夥伴」改成這張卡
  // 讀 GET /api/v1/session-policy(不跳窗,每次讀 aibd 都重找);頁面不送任何名字或路徑,寫哪些由 aibd 自己從 found 挑 kind = native
  // 寫設定走既有 act 流程(c2f0b IFACE r2:type = session_policy_init,202 + ticket;body 只有 type,多帶欄位 aibd 回 400)— 舊 aibd 沒有 GET ⇒ 不出卡,照舊那一句
  const SP_INIT = 'session_policy_init';
  const SP_KINDS = ['native', 'npm', 'script', 'interpreter', 'not_found'];
  const SP_NAME = { claude: 'Claude', codex: 'Codex' };
  const SP_INSTALL_NA = '官方安裝說明的連結:這一版 AIB 還沒有';
  const SP_NPM_WHY = (n) => `這台的是用 npm 裝的,AIB 不能用這種。npm 版其實是用 Node 執行的腳本,檔案任何程式都改得到,AIB 沒辦法確定開的是不是真的 ${n}。請改用官方安裝程式(下面的連結),裝好後按「重新找」。`;
  const spCut = (t, n) => { const a = Array.from(String(t == null ? '' : t)); return a.length > n ? a.slice(0, n).join('') + '…' : a.join(''); };
  const spName = (n) => (Object.prototype.hasOwnProperty.call(SP_NAME, n) ? SP_NAME[n] : spCut(n, 20)); // f163c N2
  // 路徑長的留前 3 段 + … + 檔名(滑過看全文);分隔字照原樣
  function spPath(t) {
    const p = String(t || ''), sep = p.includes('\\') ? '\\' : '/', a = p.split(sep);
    return a.length > 5 && p.length > 48 ? a.slice(0, 3).join(sep) + sep + '…' + sep + a[a.length - 1] : p;
  }
  // 只收 IFACE 形狀:name / path 字串、kind 在五種裡;不合的整列不畫
  function spFound(d) {
    return (d && Array.isArray(d.found) ? d.found : []).filter((f) => f && typeof f.name === 'string' && f.name && SP_KINDS.includes(f.kind)).slice(0, 10).map((f) => ({ name: f.name, kind: f.kind, path: typeof f.path === 'string' ? f.path : '' }));
  }
  function spNpm(k) { return spFound(S.sp && S.sp.d).some((f) => f.name === k && f.kind === 'npm') ? SP_NPM_WHY(spName(k)) : ''; }
  function spNeed(d) { return !!d && (d.policy === 'missing' || d.policy === 'invalid' || !(Array.isArray(d.programs) && d.programs.length)); }
  function spRow(f) {
    const n = esc(spName(f.name)), pt = f.path ? `<span class="sppath" title="${esc(f.path)}">${esc(spPath(f.path))}</span>` : '';
    if (f.kind === 'native') return `<li class="spok">✅ ${n}:找到了${pt ? '(' + pt + ')' : ''}</li>`;
    if (f.kind === 'npm') return `<li class="spwarn">⚠️ ${n}:${esc(SP_NPM_WHY(spName(f.name)))}${pt ? '<br>' + pt : ''}</li>`;
    if (f.kind === 'not_found') return `<li>${n}:沒找到</li>`;
    return `<li class="spwarn">⚠️ ${n}:找到的不是程式本體${pt ? '(' + pt + ')' : ''},請用官方安裝程式重裝</li>`;
  }
  function spCard(SP) {
    // c2f0b IFACE r3 + f163c REVIEW L1:檔已存在(讀不懂,或讀得懂但沒有程式)aibd 一律不覆蓋、按了必定 policy_exists ⇒ 只有 missing 才出核准鈕,其他叫人刪檔;路徑用 GET 的 policy_path
    const d = SP.d, F = spFound(d), bad = d.policy === 'invalid', canW = d.policy === 'missing' && d.can_write === true && F.some((f) => f.kind === 'native');
    const pp = typeof d.policy_path === 'string' && d.policy_path ? `(<span class="sppath" title="${esc(d.policy_path)}">${esc(spPath(d.policy_path))}</span>)` : '';
    const head = bad ? `<div class="aerr" role="alert">這台的開席設定檔讀不懂${pp}。AIB 不會自己蓋掉它;請刪掉這個檔後按「重新找」。</div>` : d.policy === 'loaded' ? `<div class="aerr" role="alert">這台已經有開席設定${pp},但裡面沒有任何程式。要重設,請先刪掉開席設定檔再按「重新找」。</div>` : '';
    const msg = SP.msg ? `<div class="${SP.msg.bad ? 'aerr' : 'aok'}" role="${SP.msg.bad ? 'alert' : 'status'}">${esc(SP.msg.text)}</div>` : '';
    return `<div class="spcard" role="region" aria-label="開席設定"><h4>還沒設定這台能開哪些 AI</h4>
      <div class="apn">AIB 只開你核准過、這台電腦上真正安裝的 AI 程式。</div>${ocHtml()}${head}
      <ul class="anop">${F.length ? F.map(spRow).join('') : '<li>這台沒找到任何 AI 程式</li>'}</ul>
      ${spInstallHtml()}${msg}
      <div class="afoot"><button type="button" class="k" data-addx="1">關閉</button><button type="button" class="k" data-spre="1" ${SP.busy ? 'disabled' : ''}>重新找</button>${canW ? `<button type="button" class="go" data-spok="1" ${SP.busy ? 'disabled' : ''}>核准並啟用</button>` : ''}</div></div>`;
  }
  // IFACE-provider-lifecycle §5:開席設定卡那句「官方安裝說明的連結:這一版 AIB 還沒有」換成同一張安裝 / 換版卡;讀不到 lifecycle(舊 aibd)照舊寫那句
  function spInstallHtml() { return S.lc && S.lc.d ? S.lc.d.providers.filter((p) => p && lcNeeds(p.name)).map((p) => lcCard(p.name)).join('') : `<div class="apn">${esc(SP_INSTALL_NA)}</div>`; }
  // Andy 11-26 10:1x 定 Windows 走 B2(不建 Hello 金鑰,每次要本人當下跳 Hello;DECISION-win-hello-fallback-3e53b-1126)⇒ hello_key_unknown 改說每次當下跳 Hello(c2f0b 10:17)
  // c2f0b 03:31(ebab8 03:28 派):GET /api/v1/owner-check(頁面憑證、不跳窗、不建金鑰)→ {platform, class, class_is_final, words};開席設定卡頂端照 class 寫這台是哪一種(W-7 §1)。
  // 頁面照 class 選字、不印 words;no_hello / unknown 出設 PIN 三步;「重新檢查」= 再 GET 一次(不是 act)。舊 aibd 404 / 非 Windows / 不認得的 class ⇒ 不出
  // ja / ko:設 PIN 三步的 Windows 介面原名沒核,退英文(同 AP26 pin)
  const OC_CLASSES = ['hello_hardware', 'hello_software', 'hello_key_unknown', 'no_hello', 'not_at_desk', 'unknown'];
  const OC_W = {
    'zh-TW': { line: { hello_hardware: '這台用 Windows Hello 確認是你本人(金鑰存在電腦的安全晶片裡)。', hello_software: '這台用 Windows Hello 確認是你本人,但這台沒有安全晶片(TPM),金鑰存在一般檔案裡。', hello_key_unknown: '這台用 Windows Hello 確認是你本人:每次要你本人時,當下跳一次 Hello。', no_hello: '這台還沒設 Windows Hello。設一個 PIN,這台就能用 Windows Hello 確認是你本人(不需要指紋或臉部辨識的硬體)。', not_at_desk: 'AIB 現在不在你登入的桌面上執行(例如從遠端連線啟動),沒辦法跳出確認視窗。請在這台電腦前登入後按「重新檢查」。', unknown: '還不知道這台有沒有設 Windows Hello。還沒設的話,照下面三步設一個 PIN。' },
      steps: ['打開「設定」→「帳戶」→「登入選項」。', '在「PIN (Windows Hello)」按「設定」,照畫面設一個 PIN。', '設好回到這頁按「重新檢查」。'], nopw: '沒有 Windows 登入密碼的話,要先設密碼才能設 PIN。', recheck: '重新檢查', checking: '檢查中…' },
    en: { line: { hello_hardware: 'This computer uses Windows Hello to check that it is you (the key is kept in the security chip).', hello_software: 'This computer uses Windows Hello to check that it is you, but it has no security chip (TPM), so the key is kept in an ordinary file.', hello_key_unknown: 'This computer uses Windows Hello to check that it is you: each time AIB needs you, Windows Hello asks right then.', no_hello: 'Windows Hello is not set up on this computer. Set up a PIN and this computer can use Windows Hello to check that it is you (no fingerprint or face hardware needed).', not_at_desk: 'AIB is not running on the desktop you signed in to (for example it was started over a remote connection), so it cannot show the confirmation window. Sign in at this computer, then press Check again.', unknown: 'We don\'t know yet whether Windows Hello is set up on this computer. If it isn\'t, set up a PIN with the three steps below.' },
      steps: ['Open Settings → Accounts → Sign-in options.', 'Under PIN (Windows Hello), press Set up and follow the screens.', 'Come back to this page and press Check again.'], nopw: 'If this computer has no Windows sign-in password, set one first.', recheck: 'Check again', checking: 'Checking…' },
    ja: { line: { hello_hardware: 'このパソコンは Windows Hello で本人を確認します(鍵はセキュリティチップに保存されています)。', hello_software: 'このパソコンは Windows Hello で本人を確認しますが、セキュリティチップ(TPM)がないため鍵は通常のファイルに保存されます。', hello_key_unknown: 'このパソコンは Windows Hello で本人を確認します。AIB が本人確認を必要とするたびに、その場で Windows Hello が表示されます。', no_hello: 'このパソコンには Windows Hello が設定されていません。PIN を設定すると、このパソコンで Windows Hello による本人確認ができます(指紋や顔認証のハードウェアは不要です)。', not_at_desk: 'AIB はあなたがサインインしているデスクトップで動いていません(リモート接続から起動した場合など)。確認画面を出せません。このパソコンの前でサインインしてから「再確認」を押してください。', unknown: 'このパソコンに Windows Hello が設定されているかまだ分かりません。未設定なら、下の 3 つの手順で PIN を設定してください。' }, recheck: '再確認', checking: '確認中…' },
    ko: { line: { hello_hardware: '이 컴퓨터는 Windows Hello로 본인을 확인합니다(키는 보안 칩에 저장됨).', hello_software: '이 컴퓨터는 Windows Hello로 본인을 확인하지만, 보안 칩(TPM)이 없어 키가 일반 파일에 저장됩니다.', hello_key_unknown: '이 컴퓨터는 Windows Hello로 본인을 확인합니다. AIB가 본인 확인이 필요할 때마다 그 자리에서 Windows Hello가 뜹니다.', no_hello: '이 컴퓨터에는 Windows Hello가 설정되어 있지 않습니다. PIN을 설정하면 이 컴퓨터에서 Windows Hello로 본인을 확인할 수 있습니다(지문이나 얼굴 인식 하드웨어는 필요 없음).', not_at_desk: 'AIB가 로그인한 데스크톱에서 실행되고 있지 않아(예: 원격 연결로 시작) 확인 창을 띄울 수 없습니다. 이 컴퓨터 앞에서 로그인한 뒤 「다시 확인」을 누르세요.', unknown: '이 컴퓨터에 Windows Hello가 설정되어 있는지 아직 모릅니다. 설정되어 있지 않다면 아래 세 단계로 PIN을 설정하세요.' }, recheck: '다시 확인', checking: '확인하는 중…' },
  };
  const ocW = (k) => { const T = OC_W[typeof askLang === 'function' ? askLang() : 'zh-TW'] || OC_W['zh-TW']; return Object.prototype.hasOwnProperty.call(T, k) ? T[k] : OC_W.en[k]; };
  async function ocLoad() {
    const O = S.oc = S.oc || {}; if (O.busy) return;
    O.busy = true; render();
    const r = await soft(get('/api/v1/owner-check')).catch(() => null);
    O.busy = false; O.d = r && typeof r === 'object' && !Array.isArray(r) ? r : null; render();
  }
  function ocHtml() {
    const O = S.oc, d = O && O.d; if (!d || d.platform !== 'windows' || !OC_CLASSES.includes(d.class)) return '';
    const c = d.class, pin = c === 'no_hello' || c === 'unknown', re = pin || c === 'not_at_desk';
    const st = pin ? `<ol class="ocsteps">${ocW('steps').map((x) => `<li>${esc(x)}</li>`).join('')}</ol><div class="apn">${esc(ocW('nopw'))}</div>` : '';
    const btn = re ? `<button type="button" class="k ocre" data-ocre="1" ${O.busy ? 'disabled' : ''}>${esc(O.busy ? ocW('checking') : ocW('recheck'))}</button>` : '';
    return `<div class="occ${re ? ' warn' : ''}" role="status"><b>${esc(ocW('line')[c])}</b>${st}${btn}</div>`;
  }
  async function spLoad() {
    const SP = S.sp = S.sp || {};
    ocLoad();
    SP.busy = true; render();
    const r = await soft(get('/api/v1/session-policy')).catch(() => null);
    SP.busy = false; SP.d = r && typeof r === 'object' && !Array.isArray(r) ? r : null; render();
  }
  async function spInit() {
    const SP = S.sp; if (!SP || !SP.d || SP.busy) return;
    SP.busy = true; SP.msg = null; render();
    try {
      const r = await fingerAct(SP_INIT, {});
      const P = r && Array.isArray(r.programs) ? r.programs.filter((x) => typeof x === 'string' && x) : [];
      SP.msg = { bad: false, text: '已設定:' + (P.length ? P.map(spName).join('、') : '(AIB 沒回名單)') };
      S.version = await soft(get('/api/v1/version')).catch(() => S.version);
    } catch (e) { SP.msg = { bad: true, text: errText(e) }; }
    SP.busy = false; await spLoad();
  }
  // 新增視窗裡的重新登入:同一張卡(relogHtml);登入完重抓 version,開席時被 409 擋下的那一次自動再送
  const addLoginCard = (k) => `<span class="anlc">${relogHtml(k)}</span>`;
  const addSnap = (b) => JSON.stringify(Object.assign({}, b, { idem_key: '' })); // 開席 body 快照(idem_key 每次新的,不算)
  function addHtml() {
    const A = S.add, PV = providers();
    let pv = PV.find((p) => p.key === A.provider && p.open) || PV.find((p) => p.key === 'claude' && p.open) || PV.find((p) => p.open) || null;
    if (pv) A.provider = pv.key;
    const canD = !inPreview() && !!(pv && pv.modes.includes('dgrs'));
    if (A.mode === 'dgrs' && !canD) A.mode = 'safe';
    // a9136 12:14 / 12:17(8 小時目標):第三種模式 work = 做事席,限工作資料夾、名單外的指令直接拒(共用資料夾見下方 with_seat)
    // 519d0 20:41:Windows 上做事席還沒有沙盒(a9136 設計、5d33a 審)⇒ aibd 在 Windows 上就算回 work 也先灰掉
    const hostOs = String((S.version && (S.version.os || (S.version.capabilities && S.version.capabilities.os))) || (S.thermal && S.thermal.os) || '').toLowerCase();
    const winHost = /^win/.test(hostOs);
    const canW = !winHost && !!(pv && pv.modes.includes('work'));
    if (A.mode === 'work' && !canW) A.mode = 'safe';
    if (A.mode === 'host' && (!hsCan(pv) || hsWinWait())) A.mode = 'safe'; // S2:預設不顯示;aibd 沒有就不讓選
    // 519d0 15:45 改定:頁面不給路徑也不給名稱;共用資料夾 = 7ebca caf84f494 的 with_seat(第二席開在那一席自己的 seat-work 資料夾)
    const WS = S.seats.filter((x) => x && x.mode === 'work' && !isArch(x) && (S.sessions || []).some((se) => se.seat_id === x.seat_id && (se.state === 'running' || se.state === 'pending')));
    // 只有資料夾主人能被 with_seat(aibd shared_folder:cwd 要是那一席自己的 seat-work);借別席開的列出來但灰掉、寫借誰的
    // a9136 654162420:席 JSON 帶 folder_owner {seat, how: folder | tree},有就以它為準;舊 aibd 才讀 seat.opened 事件
    const ownerOf = (x) => { const fo = x.folder_owner, c = fo && typeof fo === 'object' && fo.seat ? String(fo.seat) : x.folder_owner === null ? '' : S.seatGuest[x.seat_id]; return c ? S.seats.find((y) => y.seat_id === c || String(y.seat_id).replace(/^seat_/, '').startsWith(c)) || { seat_id: c, name: c } : null; };
    const WSO = WS.filter((x) => !ownerOf(x));
    if (A.withSeat && !WSO.some((x) => x.seat_id === A.withSeat)) A.withSeat = '';
    // 519d0 ORDER-autodrive 清單 3:預設 Claude + 做事;已有開著的做事席就預設跟它同一個資料夾(第二席通常是審查)。只在開視窗第一次套,之後照使用者點的
    // S1b 4(ebab8 11-23 03:01):預設不跟別的夥伴共用資料夾(519d0 ORDER-autodrive 清單 3 那條改掉);要共用自己在「存哪裡」選
    if (!A.dflt && pv) { A.dflt = true; if (canW) A.mode = 'work'; } // 預設最早開的資料夾主人(通常是作者),不是最新那席
    if (A.repo === undefined && addRepoSec(A) && addRepos().length === 1) A.repo = addRepos()[0]; // S0-5:只登記一個倉時預設選它
    const PS = personas();
    // 519d0 自測 L1:不再預設清單第一個(跟這件事無關的人格);用上次開席選的,不在清單就不選
    // S0-2:人格清單(GET /api/v1/personas)回來之前不試;先試會拿現有席湊的短清單比,上次用的不在裡面就永遠不再選
    if (!A.persona && S.personaList && S.personaList.length === 1 && personaBuiltin(S.personaList[0][0])) A.persona = S.personaList[0][0]; // 只有內建那一張:客戶不用選
    if (!A.persona && !A.personaTried && (A.personasIn || (S.personaList && S.personaList.length))) { A.personaTried = true; let last = ''; try { last = localStorage.getItem('ws.lastPersona') || ''; } catch (e) { /* 沒有就不選 */ } if (last && PS.some(([u]) => u === last)) { A.persona = last; brainPreselect(A); } }
    const pn = (PS.find(([u]) => u === A.persona) || [A.persona, A.persona])[1];
    const NO = nodeOpts(); const node = A.node || ''; // S0-3:不默默帶左樹選中的那項,要用戶自己選
    const nodeT = (S.tree && node && S.tree.get(node)) ? S.tree.get(node).title || node : '';
    // S1b 3(Andy 11-23 03:00「不同品牌的AI 設計的不好」):跟其他選項同一套橫向按鈕;沒有原因的寫這家能做什麼
    const brand = `<select class="asel apsel" data-addpsel="1" aria-label="用哪一個 AI">${PV.map((p) => apPvOpts(p, pv, A)).join('')}</select>${pv ? `<div class="apn">${esc(BRAND_WHAT[pv.key] || '')}${pv.why ? ' · ' + esc(pv.why) : ''}</div>${apPvUse(pv)}` : ''}${PV.filter((p) => (p.nl || A.needLogin === p.key) && RELOG_NAME[p.key]).map((p) => `<div class="apn anl">這台的 ${esc(p.name)} 沒登入或已過期。${addLoginCard(p.key)}</div>`).join('')}${PV.filter((p) => !p.open && lcNeeds(p.key)).map((p) => `<div class="apn anl">${lcCard(p.key)}</div>`).join('')}`;
    const mode = `<button type="button" class="amd ${A.mode !== 'dgrs' && A.mode !== 'work' && A.mode !== 'host' ? 'on' : ''}" data-addm="safe"><span class="amh">${ICO_SAFE}<b>討論研究</b></span><i>不碰檔案、不跑指令${apNetWords(pv, 'safe') ? ';' + esc(apNetWords(pv, 'safe')) : ''}</i><em class="adang d1">危險程度:低</em></button><button type="button" class="amd ${A.mode === 'work' ? 'on' : ''} ${canW ? '' : 'off'}" data-addm="work" ${canW ? '' : 'disabled'}><span class="amh">${ICO_WORK}<b>沙盒開發</b></span><i>${canW ? '只動自己的資料夾,不推送' + (apNetWords(pv, 'work') ? ';' + esc(apNetWords(pv, 'work')) : '') : winHost ? 'Windows 上還沒有沙盒,先不能開' : pv && PV.some((p) => p.open && p.modes.includes('work')) ? esc(pv.name) + ' 還不能開做事席' : '這版 AIB 還不能開'}</i><em class="adang d2">危險程度:中</em></button>${hsModeBtn(A, pv) || apHostCard(A, pv)}<button type="button" class="amd warn ${A.mode === 'dgrs' ? 'on' : ''} ${canD && !inPreview() ? '' : 'off'}" data-addm="dgrs" ${canD && !inPreview() ? '' : 'disabled aria-disabled="true"'}><span class="amh">${ICO_DGRS}<b>完全權限</b></span><i>${inPreview() ? '預覽區不能開完全權限,到正式版才行' : canD ? '跟你本人一樣,每次開都要驗證' + (apNetWords(pv, 'dgrs') ? ';' + esc(apNetWords(pv, 'dgrs')) : '') : '這個品牌在這台只能開討論研究'}</i><em class="adang d4">危險程度:最高</em></button>`; // S1b 5(Andy 11-23 03:01「只有二個」):開不了也畫出來、變灰寫原因,不要整顆消失
    const pers = PS.length ? `<div class="aps"><input class="apq" data-addq="1" value="${esc(A.pq || '')}" placeholder="找人格" aria-label="找人格" autocomplete="off"><div class="apl" id="addpl">${personaRows(A)}</div></div>` : `<input class="apq" data-addf="persona" value="${esc(A.persona || '')}" placeholder="人格 uid" aria-label="人格">`;
    const RL = roleList();
    const role = `<div class="arl" role="radiogroup" aria-label="角色"><button type="button" class="arc ${!A.role ? 'on' : ''}" data-addrole="" role="radio" aria-checked="${!A.role}"><b>不掛</b><i>只開席,不掛到項目</i></button>${RL.map((r) => rgWrap('role', r.uid, `<button type="button" class="arc ${A.role === r.uid ? 'on' : ''} ${r.builtin ? '' : 'usr'}" data-addrole="${esc(r.uid)}" role="radio" aria-checked="${A.role === r.uid}" title="${esc(r.uid)}"><b>${esc(r.name)}</b><i>${esc(r.what || '')}</i></button>`, r.builtin)).join('')}</div>${S.reg && S.reg.rolesLive === false ? '<div class="ademo">角色清單:這版 AIB 還沒給,先用內建六種</div>' : ''}${rgNote('role')}${rgArchHtml('role')}${regForm('role')}`;
    // Andy 11-19 11:3x「最下方有一個下拉但又很怪的東西」⇒ 跟人格 / 第二大腦同一種:找 + 清單,選了的寫在上面
    A.node = node;
    const where = `<div class="apl"><button type="button" class="apr ${!node ? 'on' : ''}" data-addnu="" aria-pressed="${!node}"><b>不指定</b><i>自由發揮,不綁專案</i></button></div><div class="aps"><input class="apq" data-addnq="1" value="${esc(A.nq || '')}" placeholder="找已有專案(標題或編號)" aria-label="找已有專案" autocomplete="off"><div class="apl" id="addnl">${nodeRows(A)}</div></div><div class="apn">讀自進度樹;選了之後它只專注做這個專案,不會再默默帶入左樹選中的那一項${node && A.roleAuto ? ';掛到專案要一個角色,先幫你選了「作者」,角色那列可改' : node && !A.role ? ';掛到專案要一個角色,請在角色那列選一個(不選就只開席、不掛)' : ''}</div>`;
    const step = A.busy ? (A.step === 2 ? '掛角色中…' : '開席中…') : '開';
    // Andy 11-19 11:3x:品牌不能開時整個視窗還能一路選到底、「開」是灰的卻沒說為什麼 ⇒ 最上面講原因和怎麼辦,其餘先不顯示
    // Andy 11-26 02:0x「用那台既有的登入,過期或沒有一樣彈出綁定視窗」:沒登入 / 過期不叫人去終端機,就地接同一張重新登入卡(TABLE-win-login-on-open-fc7f9-1126 §F)
    const FIX = { 這台沒裝: '在這台裝好它的命令列程式,再重開這個視窗', 這台沒設定: '這台的開席設定還沒列它,要先在設定裡加上它', 這版還不能開: '這版 AIB 還不會開它,等新版' };
    if (!pv) {
      const why = PV.map((p) => `<li><b>${esc(p.name)}</b>:${esc(p.why || '這台還不能開')}${p.nl && RELOG_NAME[p.key] ? '。' + addLoginCard(p.key) : lcNeeds(p.key) ? '。' + `<span class="anlc">${lcCard(p.key)}</span>` : FIX[p.why] ? '。怎麼辦:' + esc(FIX[p.why]) : ''}</li>`).join('');
      // 母體頁(管預覽的那一頁)用的是母體自己的登入,本來就開不了席 ⇒ 不叫人去登入,導去預覽開席
      const pvGo = !inPreview() && Array.isArray(S.pvs) && S.pvs.some((x) => !x.sample);
      if (pvGo) return `<div class="dlgbg"><div class="adlg" role="dialog" aria-label="新增 AI 夥伴">
      <div class="ah"><h3>新增 AI 夥伴</h3><button type="button" class="dx" data-addx="1" aria-label="關閉">✕</button></div>
      <div class="aok">這一頁是管預覽的母體頁,不在這裡開席。開席請到預覽那一頁:按下面的鈕,在預覽清單按「打開」就會帶好配對直接進去。</div>
      <div class="afoot"><button type="button" class="k" data-addx="1">關閉</button><button type="button" class="go" data-addpv="1">去預覽開席</button></div></div></div>`;
      return `<div class="dlgbg"><div class="adlg" role="dialog" aria-label="新增 AI 夥伴">
      <div class="ah"><h3>新增 AI 夥伴</h3><button type="button" class="dx" data-addx="1" aria-label="關閉">✕</button></div>
      ${S.sp && S.sp.d && spNeed(S.sp.d) ? spCard(S.sp) : `<div class="aerr" role="alert">這台現在開不了任何 AI,所以不能加席。</div><ul class="anop">${why || '<li>AIB 沒列出任何品牌</li>'}</ul>
      <div class="afoot"><button type="button" class="k" data-addx="1">關閉</button></div>`}</div></div>`;
    }
    const repoBlk = `${(A.mode === 'dgrs' && canD) || (A.mode === 'work' && repoModes().includes('work') && A.provider !== 'codex') ? repoSec(A) : ''}${A.mode === 'work' && A.provider === 'codex' ? `<div class="asec"><span>資料夾</span><div class="apn">Codex 做事席只用自己的新資料夾(還不能跟別席同一份、也不能開在別席的倉裡)</div></div>` : ''}${A.mode === 'work' && A.provider !== 'codex' && !curRepo(A) ? `<div class="asec"><span>跟別席共用一個資料夾(同一份檔案,最多兩席在寫)</span><div class="apl"><button type="button" class="apr ${!A.withSeat ? 'on' : ''}" data-addws=""><b>自己的資料夾</b><i>新的一份</i></button>${WS.map((x) => { const o = ownerOf(x); return o ? `<div class="apr off" title="這一席是借 @${esc(seatShort(o))} 的資料夾開的;要同一份請選 @${esc(seatShort(o))}"><b>@${esc(seatShort(x))}</b><i>借 @${esc(seatShort(o))} 的,選 @${esc(seatShort(o))}</i></div>` : `<button type="button" class="apr ${A.withSeat === x.seat_id ? 'on' : ''}" data-addws="${esc(x.seat_id)}"><b>@${esc(seatShort(x))}</b><i>共用它的資料夾</i></button>`; }).join('')}</div><div class="apn">${WS.length ? '選一席 = 兩席讀寫同一份程式(例:作者開好後,審查席選作者)' : '現在沒有開著的做事席;先開的這一席用自己的資料夾,第二席再選它'}</div>${A.withSeat ? `<div class="apl aown2"><button type="button" class="apr ${!ownTree(A, WS) ? 'on' : ''}" data-addown="0"><b>同一份檔案(最多兩席寫)</b><i>兩席讀寫同一份,適合作者 + 審查</i></button>${TREE_HOLD ? `<div class="apr off" title="這個做法在修一個安全問題,修好再開"><b>同一個倉、自己的一份(各寫各的,做完再合)</b><i>這個做法在修一個安全問題,修好再開</i></div>` : ''}<button type="button" class="apr ${ownTree(A, WS) ? 'on' : ''}" data-addown="1" ${TREE_HOLD ? 'hidden' : ''}><b>同一個倉、自己的一份(各寫各的,做完再合)</b><i>自己的分支,同時寫不互踩;已有兩席在寫時用這個</i></button></div>` : ''}</div>` : ''}`;
    const brN = (((S.reg && S.reg.brains) || []).find((x) => x.uid === A.brain) || {}).name || A.brain;
    const roN = A.role ? ((roleList().find((r) => r.uid === A.role) || {}).name || A.role) : '不掛';
    const hoN = A.ho ? String(A.ho).split(/[\\/]/).pop() : '不帶';
    const set = A.plan ? `<div class="apblk apset"><div class="aplab"><span class="apstep">${apRecent().length ? 3 : 2}</span><b>帶出的設定</b>點任一列展開、直接改</div>
      ${apRow(A, 'brand', 'AI', esc(apPvModel(pv, A) ? pv.name + ' · ' + ((pv.models.find((m) => m.id === A.model) || {}).label || A.model) : apPvLabel(pv)), `${brand}<div class="apn">選單列的是這台 AIB 現在能開的 AI;開不了的也列著、不能選,後面寫原因</div>${modelLine(A, pv)}`)}
      ${apRow(A, 'mode', '模式與權限', (A.tpl && ptOf(A.tpl) ? esc(ptOf(A.tpl).name) + ' <em class="ptex">我的模板 · 底 ' + esc((AP_MODE[A.mode] || AP_MODE.safe)[0]) + '</em> ' : esc((AP_MODE[A.mode] || AP_MODE.safe)[0])) + ` <em class="adang d${A.mode === 'dgrs' ? 4 : A.mode === 'host' ? 3 : A.mode === 'work' ? 2 : 1}">危險程度:${esc((AP_MODE[A.mode] || AP_MODE.safe)[1])}</em>`, `<div class="amds">${mode}</div>${winHost && !(S.version && S.version.capabilities && (S.version.capabilities.net || S.version.capabilities.mode_net)) ? `<div class="apn">${esc(HS_WIN_NET)}</div>` : ''}${ptBlock(A)}${apCmp(A, { work: canW, dgrs: canD && !inPreview(), host: hsCan(pv) && !hsWinWait(), net: pv })}${ptFine(A)}`)}
      ${A.mode === 'host' ? hsRow(A) : hsRowOff(A)}
      ${repoBlk.trim() ? apRow(A, 'repo', '存哪裡', esc(curRepo(A) || (A.withSeat ? '跟 @' + seatShort(seatById(A.withSeat) || { seat_id: A.withSeat }) + ' 同一個資料夾' : '系統新開的資料夾')), repoBlk) : ''}
      ${apRow(A, 'persona', '人格', esc(apPersonaSum(A, pn)), `${mgBtn(A, 'persona')}${mgPanel(A, 'persona')}${PS.length ? `<div class="apsel"><b>${esc(pn || '')}</b></div>` : ''}${pers}${rgNote('persona')}${rgArchHtml('persona')}${regForm('persona')}`)}
      ${apRow(A, 'brain', '第二大腦', esc(brN || '不綁'), `${mgBtn(A, 'brain')}${mgPanel(A, 'brain')}<div class="aps"><input class="apq" data-addbq="1" value="${esc(A.bq || '')}" placeholder="找第二大腦" aria-label="找第二大腦" autocomplete="off"><div class="apl" id="addbl">${brainRows(A)}</div></div>${rgNote('brain')}${rgArchHtml('brain')}${regForm('brain')}`)}
      ${apRow(A, 'role', '角色', esc(roN), `${mgBtn(A, 'role')}${mgPanel(A, 'role')}<div class="apn">作者寫程式,審查讀改動給結論;只想聊天就不掛</div>${role}`)}
      ${apRow(A, 'node', '專注已有專案', esc(nodeT || node || '不指定'), where)}
      ${apRow(A, 'mates', '一次多 AI', (A.mates || []).length ? (A.mates || []).map((u) => `<span class="apill">+ ${esc(((personas().find(([x]) => x === u) || [])[1]) || u)}</span>`).join('') : '<span class="apex">只開這一席</span>', `<div class="aps"><input class="apq" data-apmq="1" value="${esc(A.mq || '')}" placeholder="找搭檔人格" aria-label="找搭檔人格" autocomplete="off"><div class="apl" id="addml">${mateRows(A)}</div></div><div class="apn">勾了就一起開;每位各佔一個名額(同時最多 8 位)</div>${(A.mates || []).length ? '<div class="apn">按「開這一組」= 主夥伴先開,搭檔再一位一位開;要驗證的那幾位各跳各的卡</div>' + (A.mode === 'dgrs' ? '<div class="apn apwarn">完全權限的搭檔每位各要驗證一次,不是按一次就全開</div>' : '') : ''}`)}
      ${apRow(A, 'ho', '交接單', esc(hoN), `<div class="apn">從舊式席搬來的席才填:貼舊席交接檔的完整路徑,開好會自動送「先讀這份交接檔全文,再回:我是誰、手上哪一件、下一步」;新開的席留空</div><input class="apq" data-addho="1" value="${esc(A.ho || '')}" placeholder="/…/pending/HANDOFF-<舊席碼>-….md" aria-label="交接檔路徑" autocomplete="off" spellcheck="false"><input class="apq" data-addmp="1" value="${esc(A.mp || '')}" placeholder="記憶包(選填):/…/pending/MEMPACK-<舊席碼>-<月日>.md" aria-label="記憶包路徑" autocomplete="off" spellcheck="false">${A.from ? hlNote(A) : ''}`)}
    </div>` : '<div class="apn apwait">先選要做什麼(或「接手舊席」),設定會自動帶出來</div>';
    return `<div class="dlgbg"><div class="adlg ap ${A.still ? 'still' : ''}" role="dialog" aria-label="新增 AI 夥伴">
      <div class="ah"><h3>新增 AI 夥伴<small>選要做的事,人格、權限、搭檔自動配好</small></h3><button type="button" class="dx" data-addx="1" aria-label="關閉">✕</button></div>
      <div class="apsrc">${esc(apSrc())}</div>${guideTourLine()}
      ${apTop(A)}${set}
      ${apMine(A)}
      ${A.msg ? `<div class="aerr" role="alert">${esc(A.msg)}</div>` : ''}
      ${(() => { const mx = S.maxRun || Number(S.version && (S.version.max_sessions || (S.version.sessions && S.version.sessions.max))) || 0; const run = (S.sessions || []).filter((x) => x.state === 'running' || x.state === 'starting').length; return mx && run >= mx ? `<div class="qwarn">現在已經有 ${run} 席在跑,同時最多 ${mx} 席;這一席開了會先排隊,停掉一席它才開始</div>` : ''; })()}
      ${apGrp(A)}
      <div class="afoot">${!addNoRepo(A) ? `<span class="apsum">${esc(A.plan && pv ? apSum(A, pv) : '先選一個方案')}</span>` : ''}${addNoRepo(A) ? `<span class="anorepo" role="alert">${esc(NO_REPO)}${A.noRepoAsk ? ';確定要開就再按一次' : ''}</span>` : ''}<button type="button" class="k" data-addx="1">取消</button><button type="button" class="go ${A.busy ? 'busy' : ''}" data-addgo="1" ${A.busy || !pv || !A.plan ? 'disabled' : ''}>${A.busy ? '<i class="spn"></i>' : ''}${esc(addNoRepo(A) && A.noRepoAsk && !A.busy ? '確定不用倉,開' : !A.busy && (A.mates || []).length ? `開這一組(${(A.mates || []).length + 1} 位)` : step)}</button></div></div></div>`;
  }
  // S0-5(2e2cd 11-23,BRIEF-seatopen-ui P1 / D2;11-22 21:06、21:08 兩個空資料夾席):做事 / 全開席、這台有登記過的倉、
  // 「在哪裡改」那段有出來、卻選了「系統新開的資料夾」(也沒有跟別席同一個資料夾)⇒ 這一席沒有程式倉
  function addRepoSec(A) { return (A.mode === 'dgrs' && !inPreview()) || (A.mode === 'work' && repoModes().includes('work') && A.provider !== 'codex'); }
  function addRepos() { return S.version && Array.isArray(S.version.repos) ? S.version.repos.map((x) => String((x && x.name) || x || '')).filter(Boolean) : []; }
  function addNoRepo(A) { return !!A && addRepoSec(A) && addRepos().length > 0 && !curRepo(A) && !(A.mode === 'work' && A.withSeat); }
  const NO_REPO = '這一席沒有程式倉,改不了程式';
  // ---------- S2 管理主機(fc7f9 11-23,ebab8 ORDER-page2 第 3 項;SPEC §3 + DECISION §一-4 + 3e53b DESIGN-hostmode r4 §1 §2)----------
  // 預設不顯示:aibd 開席能力(capabilities.providers[].modes)列出 host、而且有主機清單 act,才出「管理主機」那顆和「可管的主機」那列。
  // 主機清單的頁面 act 稿裡沒寫名字 ⇒ 只經過 HS_ACTS 這張表(接口留一層);名字照 IFACE-page2 r2 C(3e53b 定):managed_host_list / _add / _remove / _log。
  // 開席:mode=host + hosts(主機 id 陣列),一律走 fingerAct(開 host 席要本人驗證,驗證窗由 aibd 列每台的指紋 / IP:port / 使用者)。
  const HS_ACTS = { list: 'managed_host_list', add: 'managed_host_add', remove: 'managed_host_remove', log: 'managed_host_log', cand: 'managed_host_candidates' };
  // 3e53b 06:0x(DESIGN-hostmode 13.3 + 13.6,f163c 預讀過;碼還沒寫、名字不改):managed_host_candidates {kind:test} ⇒ {hosts:[{name, os, already, refused}]}
  const TH_EVID = '交給夥伴的測試機,上面的收據在那段時間不能當證據';
  AP_MODE.host = ['管理主機', '高'];
  FP_MODE.host = '管理主機';
  const HS_SHELL = '代連 = 把那台主機的 shell 交給這位 AI,不是唯讀。';
  const HS_WHY = { host_not_in_list: '這台不在測試機清單裡(伺服器要用「加一台」填位址)', test_host_busy: '這台測試機正在跑收據,跑完才能交給夥伴', unknown_act_type: '這一版 AIB 還沒有管理主機', host_addr_refused: '這個位址不能加(本機、這台自己、link-local 或保留段)', host_name_taken: '名稱跟另一台重複', host_unknown: '找不到這台(可能別的分頁拿掉了)', host_key_unreachable: '連不到這台,拿不到主機金鑰', host_user: '使用者只能英數、點、底線、減號,不能 - 開頭', host_key_path: '金鑰路徑不對(要完整路徑、不能 - 開頭)', hosts_missing: '管理主機要至少勾一台', host_verify_failed: '主機驗證沒過(主機金鑰對不上,或那台拒絕登入)', host_ssh_missing: '這台 Windows 沒有內建 OpenSSH,管理主機開不了' };
  const HS_ADDR = { loopback: '本機位址(127.x / ::1)', link_local: 'link-local 位址', this_computer: '這台電腦自己', reserved: '保留段位址', multicast: '多播位址', unspecified: '0.0.0.0 這類未指定位址', no_address: '查不到這個名稱的位址' };
  const hsHas = (op) => hasAct(HS_ACTS[op]) === true;
  const hsCan = (pv) => !!pv && Array.isArray(pv.modes) && pv.modes.includes('host') && hsHas('list');
  const hsCode = (e) => String((e && ((e.body && e.body.error && e.body.error.code) || e.reason || (e.body && e.body.reason) || e.code)) || '');
  // 做過什麼(managed_host_log,紀錄不是防線):我的 AI 夥伴展開管理主機那位時讀,最近 20 筆
  async function hsLogLoad(seatId) {
    const H = S.hs; H.log = H.log || {};
    try { const r = await act(HS_ACTS.log, { seat_id: seatId, limit: 20 }); H.log[seatId] = Array.isArray(r && r.items) ? r.items.slice(0, 20) : []; } catch (e) { H.log[seatId] = { err: hsWhy(e) }; }
    render();
  }
  function hsLogHtml(seatId) {
    if (hasAct(HS_ACTS.log) !== true) return '<div class="aplab">連主機做過什麼</div><div class="apn">這一版 AIB 還沒有</div>';
    const L = (S.hs.log || {})[seatId]; if (!L) return '<div class="aplab">連主機做過什麼</div><div class="apn">讀取中…</div>';
    if (L.err) return `<div class="aplab">連主機做過什麼</div><div class="apn ptoff">讀不到:${esc(L.err)}</div>`;
    const nm = (id) => ((S.hs.list || []).find((h) => h.id === id) || {}).name || id;
    return `<div class="aplab">連主機做過什麼 <em class="apex">紀錄,不是防線</em></div>${L.length ? `<div class="ptlog">${L.map((x) => `<div class="ptlr ${x.stopped ? 'gone' : ''}"><span>${esc(String(x.at || '').replace('T', ' ').slice(5, 16))}</span><b>${esc(nm(x.host_id))}</b><i>${esc(String(x.command || '').slice(0, 200))}</i><em>${x.stopped ? '被停掉' : 'rc ' + esc(String(x.rc))}</em></div>`).join('')}</div>` : '<div class="apn">還沒有紀錄</div>'}`;
  }
  const hsWhy = (e) => { const c = hsCode(e), why = String((e && ((e.body && e.body.error && e.body.error.reason) || (e.body && e.body.reason) || e.reason)) || ''); if (c === 'host_addr_refused' && HS_ADDR[why]) return '這個位址不能加:' + HS_ADDR[why]; return (typeof fpSay === 'function' ? fpSay : (t) => t)(HS_WHY[c] || REPO_WHY[c] || errText(e)); };
  const hsOk = (h) => h && typeof h.id === 'string' && h.id && typeof h.name === 'string';
  // 模式那一排的第四顆;不能開就整顆不畫(不放灰的)
  // Windows:W5 / W6 沒實機量過前灰掉、寫「Windows 待驗」(aibd 量過會帶 capabilities.host_windows_verified: true;欄位名見 IFACE-page2-fc7f9-1123)
  const hsWinWait = () => /^win/.test(String((S.version && (S.version.os || (S.version.capabilities && S.version.capabilities.os))) || '').toLowerCase()) && !(S.version && S.version.capabilities && S.version.capabilities.host_windows_verified === true);
  // Andy 05:5x DECISION 十七:Windows 上完全權限以外的夥伴能上網,沒有對等沙盒 ⇒ 只是提示(取代原本「沙盒前不能上網」);aibd 給了 capabilities.net 就照它畫、不顯示這句
  const HS_WIN_NET = 'Windows:沒有對等的沙盒,網路規則只是提示、擋不住(跟完全權限同一句)';
  function hsModeBtn(A, pv) {
    if (!hsCan(pv)) return '';
    if (hsWinWait()) return `<button type="button" class="amd off" data-addm="host" disabled aria-disabled="true"><span class="amh">${ICO_HOST}<b>管理主機</b></span><i>Windows 待驗</i><em class="adang d3">危險程度:高</em></button>`;
    return `<button type="button" class="amd ${A.mode === 'host' ? 'on' : ''}" data-addm="host"><span class="amh">${ICO_HOST}<b>管理主機</b></span><i>只管你選的主機,每台驗證一次</i><em class="adang d3">危險程度:高</em></button>`;
  }
  async function hsLoad() {
    const H = S.hs;
    if (!hsHas('list')) { H.list = null; return; }
    try { const r = await act(HS_ACTS.list, {}); H.list = Array.isArray(r && r.hosts) ? r.hosts.filter(hsOk) : []; }
    catch (e) { H.list = []; H.msg = { bad: true, text: '讀不到主機清單:' + hsWhy(e) }; }
    render();
  }
  const hsLine = (h) => [h.addr ? String(h.addr) + (h.port ? ':' + Number(h.port) : '') : '', h.user ? '使用者 ' + h.user : '', h.hostkey_sha256 ? '指紋 ' + String(h.hostkey_sha256).slice(0, 80) : ''].filter(Boolean).join(' · ');
  // 「可管的主機」那一列(模式選了管理主機才出)
  async function hsCandLoad() {
    const H = S.hs; H.candBusy = true;
    try { const r = await act(HS_ACTS.cand, { kind: 'test' }); H.cand = Array.isArray(r && r.hosts) ? r.hosts.filter((m) => m && typeof m.name === 'string' && m.name).slice(0, 40) : []; }
    catch (e) { H.cand = []; H.candErr = hsWhy(e); }
    H.candBusy = false; render();
  }
  async function hsTestAdd(name) {
    const H = S.hs; if (H.busy) return;
    H.busy = true; H.msg = { text: FP_SEE }; render();
    try {
      const r = await fingerAct(HS_ACTS.add, { from: 'trusted', name });
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      H.msg = { text: '測試機「' + name + '」交給夥伴管了;' + TH_EVID }; H.busy = false; H.cand = null;
      if (S.add && r.id) S.add.hosts = (S.add.hosts || []).concat(String(r.id));
      return hsLoad();
    } catch (e) { H.msg = { bad: true, text: '沒有交:' + hsWhy(e) }; }
    H.busy = false; render();
  }
  function hsTestChips() {
    // f163c 897efdc8cd L1:「+ AI 夥伴」面板一打開也會讀 ssh 設定 ⇒ 跟設定頁同一個開關,沒按過只出一行 + 同一顆鈕
    if ((hsHas('cand') || hasAct('test_host_list') === true) && !thWant()) return `<div class="aplab apgold">${esc(thX().title)}</div><div class="apn">${esc(thW().none)} <button type="button" class="k" data-thopen="1">${esc(thW().open)}</button></div>`;
    if (hsHas('cand')) {
      const H = S.hs; if (!H.cand) { if (!H.candBusy) setTimeout(hsCandLoad, 0); return `<div class="aplab apgold">${esc(thX().title)}</div><div class="apn">${esc(thX().loading)}</div>`; }
      if (!H.cand.length) return H.candErr ? `<div class="aplab apgold">${esc(thX().title)}</div><div class="apn">${esc(H.candErr)}</div>` : '';
      return `<div class="aplab apgold">${esc(thX().title)}</div><div class="apn">${esc(thX().evid)}</div><div class="apl">${H.cand.map((m) => m.already ? `<span class="apr off"><b>${esc(m.name)}</b><i>${esc(thX().given)}</i></span>` : m.refused ? `<span class="apr off"><b>${esc(m.name)}</b><i>${esc(HS_WHY[m.refused] || thX().cantGive)}</i></span>` : `<button type="button" class="apr" data-hstadd="${esc(m.name)}"><b>${esc(m.name)}</b><i>${esc(String(m.os || ''))}${m.os ? ' · ' : ''}${esc(thX().give)}</i></button>`).join('')}</div>`;
    }
    if (hasAct('test_host_list') !== true) return '';
    const T = S.th; if (!T || (!T.list && !T.err)) { if (!T || !T.loading) setTimeout(thLoad, 0); return `<div class="aplab apgold">${esc(thX().title)}</div><div class="apn">${esc(thX().loading)}</div>`; }
    const ok = T.err ? [] : T.list.filter((m) => m.usable || m.pinned); // 只列能用的(不能用的不出名字)
    if (!ok.length) return '';
    return `<div class="aplab apgold">${esc(thX().title)}</div><div class="apl hsoff">${ok.slice(0, 20).map((m) => `<button type="button" class="apr" data-hstest="${esc(String(m.name || ''))}"><b>${esc(String(m.name || ''))}</b><i>${esc(thX().tap)}</i></button>`).join('')}</div>`;
  }
  const HS_OTHER_NO = '這一版還沒有:別的模式還不能另外開主機;要管主機,模式選「管理主機」';
  function hsRowOff(A) {
    const H = S.hs, L = H.list || [];
    if (A.rowOpen === 'hosts' && H.list === null && hsHas('list') && !H.ld) { H.ld = true; hsLoad().finally(() => { H.ld = false; }); }
    const chips = !hsHas('list') ? '<div class="apn">管理主機:這一版 AIB 還沒有</div>' : H.list === null ? '<div class="apn">讀取中…</div>' : L.length ? `<div class="apl hsoff">${L.map((h) => `<button type="button" class="apr" data-hsoff="${esc(h.id)}"><b>${esc(h.name)}</b><i>點一下開</i></button>`).join('')}</div>` : '<div class="apn">還沒有登記主機;模式選「管理主機」可以加</div>';
    const msg = A.hsOffMsg ? `<div class="trm bad" data-hsoffmsg>${esc(A.hsOffMsg)}</div>` : '';
    return apRow(A, 'hosts', '可管的主機', '<em class="apex">不開(看不到也碰不到任何主機)</em>', `<div class="apn">預設不開:這一位看不到、也連不到任何主機。注意:代連 = 把那台主機的 shell 交給這位 AI,不是唯讀</div><div class="aplab apgold">伺服器</div>${chips}${hsTestChips()}${msg}`);
  }
  function hsRow(A) {
    const H = S.hs, L = H.list || [], sel = (A.hosts || []).filter((id) => L.some((h) => h.id === id));
    const sum = sel.length ? sel.map((id) => (L.find((h) => h.id === id) || {}).name).join('、') : '還沒選';
    const rows = H.list === null ? '<div class="apn">讀取中…</div>' : L.length ? `<div class="apl">${L.map((h) => H.del === h.id
      ? `<div class="cfm rgc">從清單拿掉「${esc(h.name)}」?立刻生效:用它的夥伴之後連不到,正在跑的會被停掉。<span class="ptact"><button type="button" class="k" data-hsdelno="1">不拿掉</button><button type="button" class="go danger" data-hsdelok="${esc(h.id)}" ${H.busy ? 'disabled' : ''}>拿掉</button></span></div>`
      : `<div class="rgr"><button type="button" class="apr ${sel.includes(h.id) ? 'on' : ''}" data-hspick="${esc(h.id)}" role="checkbox" aria-checked="${sel.includes(h.id)}"><b>${sel.includes(h.id) ? '☑' : '☐'} ${esc(h.name)}</b><i>${esc(hsLine(h))}</i></button>${hsHas('remove') ? `<span class="pta"><button type="button" class="ptx" title="從清單拿掉" aria-label="拿掉主機 ${esc(h.name)}" data-hsdel="${esc(h.id)}">🗑</button></span>` : ''}</div>`).join('')}</div>` : '<div class="apn">清單上還沒有主機</div>';
    const F = H.f;
    const form = !hsHas('add') ? '' : F ? `<div class="ptf"><b>加一台主機</b>
      <input class="apq" data-hsf="name" value="${esc(F.name)}" maxlength="40" placeholder="名稱,例如:測試機一號" aria-label="主機名稱" autocomplete="off">
      <input class="apq" data-hsf="addr" value="${esc(F.addr)}" maxlength="253" placeholder="IP 或主機名" aria-label="位址" autocomplete="off" spellcheck="false">
      <input class="apq" data-hsf="port" value="${esc(F.port)}" maxlength="5" inputmode="numeric" placeholder="埠(預設 22)" aria-label="埠" autocomplete="off">
      <input class="apq" data-hsf="user" value="${esc(F.user)}" maxlength="32" placeholder="使用者" aria-label="使用者" autocomplete="off" spellcheck="false">
      <input class="apq" data-hsf="key" value="${esc(F.key)}" maxlength="400" placeholder="金鑰檔完整路徑,例如 ~/.ssh/id_ed25519" aria-label="金鑰檔路徑" autocomplete="off" spellcheck="false">
      <div class="apn">加的時候 AIB 會先連一次拿主機金鑰,驗證窗列出指紋、真的 IP:埠、使用者、金鑰路徑,你對過再按。AIB 不讀 ~/.ssh/config;本機、這台自己、link-local 的位址不能加</div>
      <div class="ptact"><button type="button" class="go" data-hsok="1" ${H.busy ? 'disabled' : ''}>加</button><button type="button" class="k" data-hsno="1">取消</button></div></div>`
      : '<div class="ptact"><button type="button" class="k" data-hsnew="1">+ 加一台主機</button></div>';
    const msg = H.msg ? `<div class="trm${H.msg.bad ? ' bad' : ''}" data-hsmsg>${esc(H.msg.text)}</div>` : '';
    return apRow(A, 'hosts', '可管的主機', esc(sum), `<div class="ademo hswarn">${esc(HS_SHELL)}</div><div class="apn">這一位只看得到、只連得到你勾的這幾台;拿不到金鑰,由 AIB 代連,每條指令都留紀錄</div><div class="aplab apgold">伺服器</div>${rows}${form}${hsTestChips()}${A.hsOffMsg ? `<div class="trm bad" data-hsoffmsg>${esc(A.hsOffMsg)}</div>` : ''}${msg}`);
  }
  function hsCheck(F) {
    const name = String(F.name || '').trim(), addr = String(F.addr || '').trim(), user = String(F.user || '').trim(), key = String(F.key || '').trim(), port = String(F.port || '').trim();
    if (!name || !addr || !user || !key) return '名稱、位址、使用者、金鑰路徑都要填';
    if (/[\u0000-\u001f\u007f\s]/.test(addr) || addr.startsWith('-')) return '位址不能有空白,也不能 - 開頭';
    if (!/^[A-Za-z0-9._][A-Za-z0-9._-]*$/.test(user)) return HS_WHY.host_user;
    if (key.startsWith('-') || /[\u0000-\u001f\u007f]/.test(key) || !/^(~\/|\/|[A-Za-z]:\\|%USERPROFILE%\\)/.test(key)) return HS_WHY.host_key_path;
    if (port && !(/^\d{1,5}$/.test(port) && Number(port) >= 1 && Number(port) <= 65535)) return '埠要是 1–65535';
    return '';
  }
  async function hsAdd() {
    const H = S.hs, F = H.f; if (!F || H.busy) return;
    const bad = hsCheck(F); if (bad) { H.msg = { bad: true, text: bad }; return render(); }
    const body = { name: String(F.name).trim(), addr: String(F.addr).trim(), port: Number(String(F.port || '').trim() || 22), user: String(F.user).trim(), key_path: String(F.key).trim() };
    H.busy = true; H.msg = { text: FP_SEE }; render();
    try {
      const r = await fingerAct(HS_ACTS.add, body);
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      H.f = null; H.msg = { text: '已加主機「' + body.name + '」' }; H.busy = false;
      if (S.add && r.id) S.add.hosts = (S.add.hosts || []).concat(String(r.id));
      return hsLoad();
    } catch (e) { H.msg = { bad: true, text: '沒有加:' + hsWhy(e) }; }
    H.busy = false; render();
  }
  async function hsRemove(id) {
    const H = S.hs, h = (H.list || []).find((x) => x.id === id); if (!h || H.busy) return;
    H.busy = true; render();
    try {
      const r = await act(HS_ACTS.remove, { id });
      if (!rgOk(r)) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      const n = r && Number.isInteger(r.stopped) && r.stopped >= 0 ? r.stopped : null;
      H.del = ''; H.msg = { text: '已拿掉「' + h.name + '」;用它的夥伴已收回' + (n === null ? '、正在跑的已停' : n ? '、正在跑的已停(' + n + ' 個)' : ',當下沒有在跑的') }; H.busy = false;
      if (S.add) S.add.hosts = (S.add.hosts || []).filter((x) => x !== id);
      return hsLoad();
    } catch (e) { H.del = ''; H.msg = { bad: true, text: '沒有拿掉:' + hsWhy(e) }; }
    H.busy = false; render();
  }
  async function hsClick(x) {
    const H = S.hs, A = S.add;
    const pk = x.closest('[data-hspick]'); if (pk && A) { const id = pk.dataset.hspick, L = A.hosts || []; A.hosts = L.includes(id) ? L.filter((y) => y !== id) : L.concat(id); A.key = idem(); render(); return true; }
    if (x.closest('[data-hsnew]')) { H.f = { name: '', addr: '', port: '', user: '', key: '' }; H.msg = null; render(); const i = document.querySelector('[data-hsf="name"]'); if (i) i.focus(); return true; }
    if (x.closest('[data-hsno]')) { H.f = null; H.msg = null; render(); return true; }
    if (x.closest('[data-hsok]')) { await hsAdd(); return true; }
    const dl = x.closest('[data-hsdel]'); if (dl) { H.del = dl.dataset.hsdel; render(); return true; }
    if (x.closest('[data-hsdelno]')) { H.del = ''; render(); return true; }
    const dk = x.closest('[data-hsdelok]'); if (dk) { await hsRemove(dk.dataset.hsdelok); return true; }
    return false;
  }
  // ---------- S3 權限模板(fc7f9 11-23,ebab8 ORDER-page2;SPEC-ai-partner-open §4 + DECISION-council-aipartner §一-5、§一-9)----------
  // aibd:08de5 7a f511716fc1(現頭 855a8b487d)才有 perm_template_list / _add / _set / _remove;還沒合進來時 aibd 回 unknown_act_type ⇒ 整塊寫「這一版還沒有」,不假裝存了。
  // 開夥伴帶模板、已開的夥伴套模板 = 7b(DESIGN-brainnote-08de5 §2d:seat_open 帶 tpl_uid + rev、perm_template_apply {seat_id, tpl_uid, rev}),version.acts 有 perm_template_apply 才放。
  // 要不要驗證、這台做不做得到(not_enforceable)全由 aibd 判;頁面只預告,不替 aibd 決定。
  const PT_ITEMS = [
    ['chat', '對話、叫別席', '在對話裡說話、@ 別席'], ['code', '寫程式', '改程式碼、提交到自己的分支'], ['own', '讀寫自己資料夾的檔', '它的資料夾,或倉裡它自己那一份'],
    ['cmd', '跑名單內的指令', 'git 提交 / 切分支、node 測試、格式檢查'], ['persona', '寫回人格(提案)', '學到的寫成提案,你驗證才寫進人格卡'], ['brain', '更新第二大腦', '筆記寫進它綁的那一個第二大腦'],
    ['any', '讀寫資料夾外的檔', '整台電腦,包括其他專案'], ['anycmd', '跑任何指令', '安裝、編譯、刪除…都可以'], ['net', '自己連外網', '本機與內網一律擋,要連時彈出給你選'],
    ['push', '推送 / 開 PR', '送到 GitHub'], ['host', '連主機', 'ssh 到伺服器 / 測試機;另外開,逐台選'],
  ];
  const PT_DANGER = ['any', 'anycmd', 'push', 'host'];
  const PT_RESERVED = ['討論研究', '沙盒開發', '管理主機', '完全權限', 'safe', 'work', 'host', 'dgrs'];
  const PT_BASES = ['safe', 'work', 'dgrs'];
  // 這一版 aibd 開每種模式實際給什麼(08de5 perm_tpl::gives_now);「把這組存成模板」照這個帶,存下去就不會有做不到的項目
  const PT_GIVES = { safe: ['chat'], work: ['chat', 'code', 'own', 'cmd', 'brain'], dgrs: ['chat', 'code', 'own', 'cmd', 'brain', 'any', 'anycmd', 'net', 'push'] };
  const PT_OFF = '我的權限模板:這一版 AIB 還沒有(要新版 AIB);現在只能用上面三種模式';
  const PT_NO_OPEN = '用模板開夥伴:這一版 AIB 還沒有,先用上面三種模式開';
  const PT_NO_APPLY = '把模板套到已開的夥伴:這一版 AIB 還沒有(要新版 AIB)';
  const PT_WHY = {
    template_name: '名稱要 1–20 個字,不能跟四種模式同名', template_base: '底只能是討論研究 / 沙盒開發 / 完全權限', template_items: '逐項開關不完整,請重新整理再試',
    template_unknown: '這個模板已經不在了(可能別的分頁刪了)', template_changed: '這個模板剛被改過,已重新讀一次;請再看一下再存', template_name_taken: '名稱跟另一個模板重複',
    item_not_enforceable: '有幾項這一版還沒有,AIB 沒有套', seat_not_found: '找不到這一位', seat_archived: '這一位已封存',
    unknown_act_type: PT_OFF, page_only: '只有這台內建的頁面能改權限模板',
    template_base_differs: '換底模式請開一位新的(模板的底跟這一位現在的不同)', template: '這一版模板不能跟帶倉一起開;要帶倉就先不用模板',
    log_not_found: '找不到這一筆紀錄(重新讀一次)', revoked_already: '這一筆已經收回了', nothing_to_revoke: '開席那一筆不能收回', invalid_request: '送出的內容不對,請重新整理再試',
  };
  // template_more_than_given:aibd 的 message 本身就是人話(哪幾項這一版關不掉),照原文;沒有 message 才用通用字
  const PT_MORE = '模板關了這一版還關不掉的項目';
  const PT_RESTART = { restart_after_turn: '它這一輪做完才會重開', stop_unconfirmed: '沒確認它停了,請到它那格看一下', restart_failed: '重開沒成功,請手動重開這一位' };
  // 套模板 / 收回的回應(同一套):unchanged / pending_restart(+ 重開不成的原因)/ restarting;沒有 done 欄
  function ptRes(r) {
    const why = r ? Object.keys(PT_RESTART).find((k) => r[k]) : '';
    if (r && r.unchanged) return '沒有變(權限跟現在一樣)';
    if (r && r.restarting) return 'AIB 正在重開這一位,重開完才生效';
    if (r && r.pending_restart) return '驗證過了;重開這一位之後才生效' + (why ? '(' + PT_RESTART[why] + ')' : '');
    return '';
  }
  const ptItems = (o) => { const r = {}; for (const [k] of PT_ITEMS) r[k] = !!(o && o[k] === true); return r; };
  const ptName = (k) => (PT_ITEMS.find(([x]) => x === k) || [k, k])[1];
  const ptWords = (it) => PT_ITEMS.filter(([k]) => it && it[k]).map(([, nm]) => nm).join('、');
  const ptNeedFp = (base, it) => base === 'dgrs' || base === 'host' || PT_DANGER.some((k) => it && it[k]);
  const ptRank = (b) => ({ safe: 0, work: 1, dgrs: 2 }[b]);
  // 改模板:任何一項從關變開、或底往上(host 不同軸,動了就算)⇒ aibd 會要驗證(perm_tpl::needs_owner_change)
  const ptRaised = (was, f) => !!was && ((was.base !== f.base && !(ptRank(was.base) >= 0 && ptRank(f.base) >= 0 && ptRank(f.base) < ptRank(was.base))) || PT_ITEMS.some(([k]) => f.items[k] && !was.items[k]));
  const ptOf = (uid) => ((S.pt && S.pt.list) || []).find((t) => t.tpl_uid === uid) || null;
  const ptCode = (e) => String((e && ((e.body && e.body.error && e.body.error.code) || e.reason || (e.body && e.body.reason) || e.code)) || '');
  const ptWhy = (e) => { const c = ptCode(e); if (c === 'template_more_than_given') { const m = String((e && ((e.body && e.body.error && e.body.error.message) || e.message)) || '').trim(); return m && m !== c ? m : PT_MORE; } return (typeof fpSay === 'function' ? fpSay : (t) => t)(PT_WHY[c] || REPO_WHY[c] || errText(e)); };
  const ptNeWords = (ne) => { const L = Array.isArray(ne) ? ne.map((k) => { const it = k && typeof k === 'object' ? String(k.item || '') : String(k); const nm = it === 'base:host' ? '連主機的模式' : ptName(it); return k && typeof k === 'object' && k.platform ? nm + '(' + String(k.platform) + ')' : nm; }) : []; return L.length ? '這幾項這一版還沒有,用它開的夥伴實際拿不到:' + L.join('、') : ''; };
  function ptCheck(f, list) {
    const nm = String((f && f.name) || '').replace(/[\u0000-\u001f\u007f]/g, '').trim();
    if (!nm) return '模板要有名稱';
    if ([...nm].length > 20) return '名稱最多 20 個字';
    if (PT_RESERVED.some((r) => r.toLowerCase() === nm.toLowerCase())) return '名稱不能跟四種模式同名';
    if ((list || []).some((t) => t.name === nm && t.tpl_uid !== f.uid)) return '名稱跟另一個模板重複';
    if (!PT_BASES.includes(f.base) && !(f.was && f.was.base === f.base)) return '先選底:討論研究 / 沙盒開發 / 完全權限';
    return '';
  }
  async function ptLoad() {
    const P = S.pt;
    if (hasAct('perm_template_list') === false) { P.off = PT_OFF; return render(); }
    try {
      const r = await act('perm_template_list', {});
      P.list = Array.isArray(r && r.templates) ? r.templates.filter((t) => t && typeof t.tpl_uid === 'string' && typeof t.name === 'string') : [];
      P.off = '';
    } catch (e) {
      const c = ptCode(e);
      if (c === 'unknown_act_type' || (e && e.status === 404)) P.off = PT_OFF; else P.msg = { bad: true, text: '讀不到權限模板:' + ptWhy(e) };
    }
    render();
  }
  const ptBlank = (base, fromMode) => ({ uid: '', rev: 0, name: '', base: PT_BASES.includes(base) ? base : 'safe', items: ptItems(Object.fromEntries((fromMode ? PT_GIVES[base] || ['chat'] : ['chat']).map((k) => [k, true]))), was: null });
  function ptForm() {
    const f = S.pt.f; if (!f) return '';
    const fp = f.was ? ptRaised(f.was, f) : ptNeedFp(f.base, f.items);
    const bases = PT_BASES.concat(f.was && !PT_BASES.includes(f.was.base) ? [f.was.base] : []);
    return `<div class="ptf"><b>${f.uid ? '改模板' : '新增權限模板'}</b>
      <input class="apq" data-ptf="name" value="${esc(f.name)}" maxlength="20" placeholder="模板名稱,例如:SEO 寫手" aria-label="模板名稱" autocomplete="off">
      <div class="aplab">底(開程序時用哪一種)</div><div class="ptbs" role="radiogroup" aria-label="底">${bases.map((b) => `<button type="button" class="k ${f.base === b ? 'on' : ''}" data-ptbase="${esc(b)}" role="radio" aria-checked="${f.base === b}">${esc(FP_MODE[b] || b)}</button>`).join('')}</div>
      <div class="aplab">逐項</div><div class="ptis">${PT_ITEMS.map(([k, nm, d]) => `<div class="pti"><div><b>${esc(nm)}</b><i>${esc(d)}</i></div><button type="button" class="ptsw" role="switch" aria-checked="${!!f.items[k]}" aria-label="${esc(nm)}" data-ptit="${k}"></button></div>`).join('')}</div>
      <div class="apn">${f.was ? (fp ? '這次有調高(多開了項目或底往上)⇒ 存的時候要驗證' : '這次只有調低或改名 ⇒ 不用驗證') + ';已經用它開好的夥伴不會跟著變,要的話到那一位按「權限模板」重新套用' : (fp ? '這組含完全權限,或資料夾外的檔 / 任何指令 / 推送 / 連主機 ⇒ 新增時要驗證' : '這組不用驗證就能存')}</div>
      <div class="ptact"><button type="button" class="go" data-ptok="1" ${S.pt.busy ? 'disabled' : ''}>${f.uid ? '存' : '新增'}</button><button type="button" class="k" data-ptno="1">取消</button></div></div>`;
  }
  // 放在「模式與權限」那一列底下
  // ebab8 11-23 03:20 第 4 條:「逐項調整」接 S3 —— 示意是對照表下面一條 details;這裡用一顆列鈕開關(整頁重畫時 details 會自己收起)
  function ptFine(A) {
    const P = S.pt, on = !!(P.f && P.f.fine);
    return `<div class="apfine ${on ? 'open' : ''}"><button type="button" class="apfs" data-ptfine="1" aria-expanded="${on}">逐項調整 <i class="apchev" aria-hidden="true">⌄</i></button>${on ? `<div class="apn">個別調整就變「自訂權限」;這一版要先存成模板才能拿來開(開好之後也能隨時改)</div>${ptForm()}` : ''}</div>`;
  }
  function ptBlock(A) {
    const P = S.pt, head = '<div class="aplab">我的權限模板</div>';
    const msg = P.msg ? `<div class="trm${P.msg.bad ? ' bad' : ''}" data-ptmsg>${esc(P.msg.text)}</div>` : '';
    if (P.off) return `<div class="ptb">${head}<div class="apn ptoff">${esc(P.off)}</div></div>`;
    if (!P.list) return `<div class="ptb">${head}<div class="apn">讀取中…</div>${msg}</div>`;
    const open7b = hasAct('perm_template_apply') === true;
    const rows = P.list.map((t) => {
      const it = ptItems(t.items), ne = ptNeWords(t.not_enforceable);
      if (P.del === t.tpl_uid) return `<div class="ptc cfm">刪掉模板「${esc(t.name)}」?用它開好的夥伴權限不變 <button type="button" class="k del" data-ptdelok="${esc(t.tpl_uid)}" ${P.busy ? 'disabled' : ''}>刪</button><button type="button" class="k" data-ptdelno="1">不刪</button></div>`;
      return `<div class="ptc ${A && A.tpl === t.tpl_uid ? 'on' : ''}"><button type="button" class="ptp" data-ptpick="${esc(t.tpl_uid)}" ${open7b ? '' : 'disabled'} aria-pressed="${!!(A && A.tpl === t.tpl_uid)}"><b>${esc(t.name)}</b><em>底 ${esc(FP_MODE[t.base] || t.base)}</em><i>${esc(ptWords(it) || '什麼都不開')}${ptNeedFp(t.base, it) ? ' · 開時要驗證' : ''}</i>${ne ? `<i class="ptne">${esc(ne)}</i>` : ''}</button><span class="pta"><button type="button" class="ptx" title="改" aria-label="改模板 ${esc(t.name)}" data-ptedit="${esc(t.tpl_uid)}">✎</button><button type="button" class="ptx" title="刪" aria-label="刪模板 ${esc(t.name)}" data-ptdel="${esc(t.tpl_uid)}">🗑</button></span></div>`;
    }).join('');
    const btns = P.f ? '' : `<div class="ptact"><button type="button" class="k" data-ptnew="1">+ 新增權限模板</button></div>`;
    return `<div class="ptb">${head}${P.list.length ? `<div class="ptl">${rows}</div>` : '<div class="apn">還沒有模板</div>'}${P.list.length && !open7b ? `<div class="apn ptoff">${esc(PT_NO_OPEN)}</div>` : ''}${btns}${P.f && P.f.fine ? '' : ptForm()}${msg}</div>`;
  }
  async function ptSave() {
    const P = S.pt, f = P.f; if (!f || P.busy) return;
    const bad = ptCheck(f, P.list); if (bad) { P.msg = { bad: true, text: bad }; return render(); }
    const body = { name: String(f.name).replace(/[\u0000-\u001f\u007f]/g, '').trim(), base: f.base, items: ptItems(f.items) };
    if (f.uid) Object.assign(body, { tpl_uid: f.uid, rev: f.rev });
    P.busy = true; P.msg = (f.was ? ptRaised(f.was, f) : ptNeedFp(f.base, f.items)) ? { text: FP_SEE } : null; render();
    try {
      const r = await fingerAct(f.uid ? 'perm_template_set' : 'perm_template_add', body);
      if (!r || r.done !== true) throw Object.assign(new Error('not_done'), { code: 'not_done' });
      P.f = null; P.msg = { text: (f.uid ? '已存模板「' : '已新增模板「') + body.name + '」' + (f.uid ? ';已開的夥伴不會跟著變,要的話重新套用' : '') };
      P.busy = false; await ptLoad(); return;
    } catch (e) {
      const c = ptCode(e);
      if (c === 'unknown_act_type') { P.off = PT_OFF; P.f = null; } else P.msg = { bad: true, text: (f.uid ? '沒有存:' : '沒有新增:') + ptWhy(e) };
      P.busy = false;
      if (c === 'template_changed' || c === 'template_unknown') { P.f = null; return ptLoad(); }
    }
    render();
  }
  async function ptRemove(uid) {
    const P = S.pt, t = ptOf(uid); if (!t || P.busy) return;
    P.busy = true; render();
    try {
      await act('perm_template_remove', { tpl_uid: t.tpl_uid, rev: t.rev });
      if (S.add && S.add.tpl === uid) S.add.tpl = '';
      P.del = ''; P.msg = { text: '已刪模板「' + t.name + '」;用它開好的夥伴權限不變' }; P.busy = false; return ptLoad();
    } catch (e) { const c = ptCode(e); P.del = ''; if (c === 'unknown_act_type') P.off = PT_OFF; else P.msg = { bad: true, text: '沒有刪:' + ptWhy(e) }; P.busy = false; if (c === 'template_changed' || c === 'template_unknown') return ptLoad(); }
    render();
  }
  // 已開的夥伴:夥伴列上「權限模板」⇒ 選一個套用。調高要驗證;調高調低都是重開這一位之後才生效(DECISION §一-2),重開前不說已經改好
  function ptSeatBtn(s) {
    if (!s || isArch(s)) return '';
    return `<button type="button" class="k ${S.pt.seat === s.seat_id ? 'on' : ''}" data-ptsa="${esc(s.seat_id)}" title="把一個權限模板套到這一位" aria-expanded="${S.pt.seat === s.seat_id}">權限模板</button>`;
  }
  // 08de5 7b perm_apply.rs(0742adfab8)才有 seat_perm_log / seat_perm_revoke(IFACE-page2 r2 A);頁面只在 version.acts 有才呼叫,沒有寫「這一版還沒有」
  const PT_ACT = { open: '開席', lower: '調低', raise_pending: '調高(等重開)', restart_applied: '重開後生效', revoke: '收回' };
  const ptSet = (x) => (!x ? '—' : x.tpl_uid ? '模板「' + ((ptOf(x.tpl_uid) || {}).name || x.tpl_uid) + '」' : FP_MODE[x.base] || x.base || '—');
  async function ptLogLoad(seatId) {
    const P = S.pt; P.log = P.log || {};
    try { const r = await act('seat_perm_log', { seat_id: seatId }); P.log[seatId] = { now: (r && r.now) || null, pending: (r && r.pending) || null, items: Array.isArray(r && r.log) ? r.log.filter((x) => x && x.log_id != null).slice(0, 200) : [] }; }
    catch (e) { P.log[seatId] = { err: ptWhy(e), items: [] }; }
    render();
  }
  function ptLogHtml(seatId) {
    if (hasAct('seat_perm_log') !== true) return '<div class="apn">權限紀錄與收回:這一版 AIB 還沒有</div>';
    const L = (S.pt.log || {})[seatId]; if (!L) return '<div class="apn">權限紀錄讀取中…</div>';
    if (L.err) return `<div class="apn ptoff">權限紀錄讀不到:${esc(L.err)}</div>`;
    const canRv = hasAct('seat_perm_revoke') === true;
    const head = `<div class="aplab">權限紀錄</div><div class="apn">現在:${esc(ptSet(L.now))}${L.pending ? ' · 重開後生效:' + esc(ptSet(L.pending)) : ''}</div>`;
    return head + (L.items.length ? `<div class="ptlog">${L.items.slice(0, 20).map((x) => `<div class="ptlr ${x.revoked_at ? 'gone' : ''}"><span>${esc(String(x.at || '').replace('T', ' ').slice(5, 16))}</span><b>${esc(PT_ACT[x.action] || x.action || '')}</b><i>${esc(ptSet(x.from))} → ${esc(ptSet(x.to))}</i>${x.revoked_at ? '<em>已收回</em>' : canRv && x.action !== 'open' && x.action !== 'revoke' ? `<button type="button" class="k" data-ptrv="${esc(String(x.log_id))}" ${S.pt.busy ? 'disabled' : ''}>收回</button>` : ''}</div>`).join('')}</div>` : '<div class="apn">還沒有紀錄</div>');
  }
  async function ptRevoke(seatId, logId) {
    const P = S.pt; if (!seatId || P.busy) return;
    P.busy = true; P.seatMsg = { seat: seatId, text: '收回中…' }; render();
    try {
      const r = await fingerAct('seat_perm_revoke', { seat_id: seatId, log_id: /^\d+$/.test(logId) ? Number(logId) : logId }, () => { P.seatMsg = { seat: seatId, text: FP_SEE }; render(); });
      const w = ptRes(r); P.seatMsg = w ? { seat: seatId, text: '已收回:' + w } : { seat: seatId, bad: true, text: '沒有收回:AIB 沒有回結果' };
    } catch (e) { P.seatMsg = { seat: seatId, bad: true, text: '沒有收回:' + ptWhy(e) }; }
    P.busy = false; await ptLogLoad(seatId);
  }
  function ptSeatBox(s) {
    const P = S.pt; if (!s || P.seat !== s.seat_id) return '';
    const msg = P.seatMsg && P.seatMsg.seat === s.seat_id ? `<div class="trm${P.seatMsg.bad ? ' bad' : ''}" data-ptmsg>${esc(P.seatMsg.text)}</div>` : '';
    let inner;
    if (P.off) inner = `<div class="apn ptoff">${esc(P.off)}</div>`;
    else if (hasAct('perm_template_apply') !== true) inner = `<div class="apn ptoff">${esc(PT_NO_APPLY)}</div>`;
    else if (!P.list) inner = '<div class="apn">讀取中…</div>';
    else if (!P.list.length) inner = '<div class="apn">還沒有模板;到「+ AI 夥伴」→ 模式與權限 新增</div>';
    else inner = `<div class="ptl">${P.list.map((t) => { const it = ptItems(t.items), ne = ptNeWords(t.not_enforceable); return `<div class="ptc"><button type="button" class="ptp" data-ptap="${esc(t.tpl_uid)}" ${P.busy ? 'disabled' : ''}><b>${esc(t.name)}</b><em>底 ${esc(FP_MODE[t.base] || t.base)}</em><i>${esc(ptWords(it) || '什麼都不開')}</i>${ne ? `<i class="ptne">${esc(ne)}</i>` : ''}</button></div>`; }).join('')}</div><div class="apn">調高要驗證;不論調高調低,都是重開這一位之後才生效。之後改了模板不會自動套過來,要再套一次</div>`;
    return `<div class="ptseat">${inner}${msg}${ptLogHtml(s.seat_id)}</div>`;
  }
  async function ptApply(seatId, uid) {
    const P = S.pt, t = ptOf(uid); if (!t || !seatId || P.busy) return;
    if (hasAct('perm_template_apply') !== true) { P.seatMsg = { seat: seatId, bad: true, text: PT_NO_APPLY }; return render(); }
    P.busy = true; P.seatMsg = { seat: seatId, text: '送出中…' }; render();
    try {
      const r = await fingerAct('perm_template_apply', { seat_id: seatId, tpl_uid: t.tpl_uid, rev: t.rev }, () => { P.seatMsg = { seat: seatId, text: FP_SEE }; render(); });
      const ne = ptNeWords(r && r.not_enforceable);
      P.seatMsg = { seat: seatId, text: '已套用「' + t.name + '」:' + (ptRes(r) || '重開這一位之後才生效') + (ne ? ';' + ne : '') };
      if (hasAct('seat_perm_log') === true) await ptLogLoad(seatId);
    } catch (e) {
      const c = ptCode(e);
      P.seatMsg = { seat: seatId, bad: true, text: '沒有套用:' + ptWhy(e) };
      if (c === 'template_changed' || c === 'template_unknown') { P.busy = false; return ptLoad(); }
    }
    P.busy = false; render();
  }
  // 點擊:回 true = 處理掉了
  async function ptClick(x) {
    const P = S.pt, A = S.add;
    if (x.closest('[data-ptnew]')) { P.f = ptBlank(A ? A.mode : 'safe', false); P.msg = null; P.del = ''; render(); ptFocus(); return true; }
    if (x.closest('[data-ptfine]')) { if (P.f && P.f.fine) P.f = null; else { P.f = ptBlank(A ? A.mode : 'safe', true); P.f.fine = true; } P.msg = null; P.del = ''; render(); if (P.f) ptFocus(); return true; }
    const ed = x.closest('[data-ptedit]'); if (ed) { const t = ptOf(ed.dataset.ptedit); if (t) { const it = ptItems(t.items); P.f = { uid: t.tpl_uid, rev: t.rev, name: t.name, base: t.base, items: Object.assign({}, it), was: { base: t.base, items: it } }; P.msg = null; P.del = ''; } render(); ptFocus(); return true; }
    const dl = x.closest('[data-ptdel]'); if (dl) { P.del = dl.dataset.ptdel; P.f = null; render(); return true; }
    if (x.closest('[data-ptdelno]')) { P.del = ''; render(); return true; }
    const dk = x.closest('[data-ptdelok]'); if (dk) { await ptRemove(dk.dataset.ptdelok); return true; }
    const bs = x.closest('[data-ptbase]'); if (bs && P.f) { P.f.base = bs.dataset.ptbase; render(); return true; }
    const it = x.closest('[data-ptit]'); if (it && P.f) { const k = it.dataset.ptit; P.f.items[k] = !P.f.items[k]; render(); return true; }
    if (x.closest('[data-ptno]')) { P.f = null; P.msg = null; render(); return true; }
    if (x.closest('[data-ptok]')) { await ptSave(); return true; }
    const pk = x.closest('[data-ptpick]'); if (pk) { const t = ptOf(pk.dataset.ptpick); if (A && t && hasAct('perm_template_apply') === true) { A.tpl = A.tpl === t.tpl_uid ? '' : t.tpl_uid; if (A.tpl) A.mode = t.base; A.noRepoAsk = false; A.key = idem(); } render(); return true; }
    const sa = x.closest('[data-ptsa]'); if (sa) { P.seat = P.seat === sa.dataset.ptsa ? '' : sa.dataset.ptsa; P.seatMsg = null; render(); if (P.seat && !P.list && !P.off) await ptLoad(); if (P.seat && hasAct('seat_perm_log') === true) await ptLogLoad(P.seat); return true; }
    const rv = x.closest('[data-ptrv]'); if (rv) { await ptRevoke(P.seat, rv.dataset.ptrv); return true; }
    const ap = x.closest('[data-ptap]'); if (ap) { await ptApply(P.seat, ap.dataset.ptap); return true; }
    return false;
  }
  function ptFocus() { const i = document.querySelector('[data-ptf="name"]'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
  // 交接單欄(519d0 11-21 00:57 派工,CUTOVER-seats-to-aibd r1):從終端機搬來的席,開好就送固定一句讀交接檔。
  // 那一句是頁面的固定字 + Andy 貼的路徑;aibd 不讀這個路徑(席自己讀),所以頁面只擋明顯不是交接檔的字
  const HO_FIRST = '先讀這份交接檔全文,再回:我是誰、手上哪一件、下一步。交接檔:';
  // 6f1cb SPEC-seat-mempack r0(519d0 07:42,切換單前提 9):新席的記憶夾不會帶過去 ⇒ 交接時另寫記憶包 MEMPACK-<席碼>-<MMDD>.md;
  // 填了就送範本句(席碼取自記憶包檔名),新席把記憶包第 2 段寫進自己的記憶夾
  const mpPathOk = (p) => { const t = String(p || ''); const base = t.split(/[\\/]/).pop() || ''; return t.length > 0 && t.length <= 400 && !/[\u0000-\u001f\u007f]/.test(t) && (/^\//.test(t) || /^~\//.test(t) || /^[A-Za-z]:[\\/]/.test(t)) && /^MEMPACK-[0-9a-f]{5}-[0-9]{4}\.md$/.test(base); };
  const mpFirst = (ho, mp) => { const code = (String(mp).split(/[\\/]/).pop() || '').slice(8, 13); return '你是 @' + code + ' 的接手席。先讀 ' + ho + ' 與 ' + mp + ';記憶包是你的席位記憶:不要寫進記憶夾(網頁席寫不到),之後每一段開始先重讀記憶包。讀完只回一句:我是誰、手上哪一件、下一步。'; }; // 6f1cb FINDING-cutover-seat-cant-read-handoff r0:網頁席的記憶夾寫不到
  const hoPathOk = (p) => { const t = String(p || ''); const base = t.split(/[\\/]/).pop() || ''; return t.length > 0 && t.length <= 400 && !/[\u0000-\u001f\u007f]/.test(t) && (/^\//.test(t) || /^~\//.test(t) || /^[A-Za-z]:[\\/]/.test(t)) && /^HANDOFF-[^\\/]+\.md$/i.test(base); };
  // 開好但還在排隊(沒在跑)的席:等它跑了再送;20 分鐘內一直試。409(剛開還在起 / 回合中)不算失敗、10 秒後再試;
  // 其他錯才算一次,3 次放棄(6f1cb FINDING-cutover r0:剛開的席 409 三次就放棄了)
  async function hoFirstFlush() {
    const P = S.hoFirst || {};
    for (const id of Object.keys(P)) {
      const f = P[id], se = (S.sessions || []).find((x) => x.seat_id === id);
      if (Date.now() - f.at > 20 * 60 * 1000 || f.tries >= 3) { delete P[id]; say('@' + f.nm + ' 的第一句沒送進去,請到那一席貼:' + f.line, 'err'); continue; }
      if (!se || se.state !== 'running' || f.sending || (f.next && Date.now() < f.next)) continue;
      f.sending = true;
      try { await act('seat_input', { seat_id: id, text: f.line }); delete P[id]; say('已請 @' + f.nm + ' 讀交接檔'); } catch (e) { f.sending = false; if (e && e.status === 409) f.next = Date.now() + 10000; else f.tries++; }
    }
  }
  function addErr(e) {
    const c = e && e.code, why = e && e.reason;
    if (c === 'host_ssh_missing' || c === 'host_verify_failed') return HS_WHY[c] + '。席沒有開';
    // a9136 da2520fac:交接單 / 記憶包只送檔名,aibd 從 pending 那一層讀、拷進席資料夾 .aib/;400 invalid_request reason 三種
    if (c === 'invalid_request' && why === 'handoff') return '交接單拷不進新席:檔名不對、不在 ~/.ez2-aib/pending/ 那一層、太大或不是文字檔。席沒有開';
    if (c === 'invalid_request' && why === 'mempack') return '記憶包拷不進新席:檔名不對、不在 pending 那一層、太大、不是文字檔,或沒一起填交接單。席沒有開';
    if (c === 'invalid_request' && why === 'carry') return '新席資料夾裡的 .aib 不是一般資料夾或寫不進去,交接單沒拷進去。席沒有開';
    if (c === 'codex_work_own_folder' || why === 'codex_work_own_folder') return 'Codex 做事席只能用自己的新資料夾;「跟哪一席同一個資料夾」選「自己的資料夾」再開';
    if (/own_tree/.test(String(why || '') + ' ' + String((e && e.message) || ''))) return '這版 AIB 還不會開「同一個倉、自己的一份」;改選「同一份檔案」再開';
    if (/\brole\b/.test(String(why || '') + ' ' + String((e && e.message) || '')) && c === 'invalid_request') return '這版 AIB 開席時還不收角色;角色先選「不掛」開好,再到項目裡掛審查';
    if (why === 'with_seat' || c === 'with_seat') return '選的那一席不是資料夾的主人(它是借別席資料夾開的,或不是頁面開的做事席);改選最先開的那一席(通常是作者)';
    if (c === 'preview_refuses' || why === 'preview_refuses') return '預覽只能開「安全」或「做事」席';
    // 0eed7 VERIFY-realentry 79c883726 M4:資料夾或上層有專案設定(.claude)⇒ 400 cwd_has_project_settings;原本落到「這版 aibd 不收這些欄位」
    if (c === 'cwd_has_project_settings' || why === 'cwd_has_project_settings') return '這個資料夾(或它上層)有自己的 Claude 專案設定,為了安全不在這裡開席' + (e && e.body && e.body.file ? '(' + String(e.body.file) + ')' : '');
    // 0eed7 VERIFY-realentry 基準 M3:人格卡讀不到 ⇒ 503 persona_source_unavailable(預覽沒帶到卡時)
    if (c === 'persona_source_unavailable' || why === 'persona_source_unavailable') return '這台的人格卡檔讀不到(檔壞了或沒有權限),開不了任何席' + (e && e.body && e.body.file ? '(' + String(e.body.file) + ')' : '');
    // b4297 ed8d14cf:403 看 code、400 看 reason
    if (e && e.status === 400 && why) return why === 'provider_unsupported' ? '這個品牌這台開不了' : why === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : why === 'persona_missing' ? '找不到這個人格' : why === 'brain' ? '這個第二大腦不在清單裡,重開視窗再選' : why === 'key_missing' ? '頁面送的識別碼不對,重開這個小視窗再試' : '這版 AIB 不收這些欄位(' + why + ')';
    return c === 'shared_folder_writers_full' ? '這個資料夾已經有兩席在寫,請開在自己的資料夾' : c === 'open_built_in_page_only' ? '用戶資料夾有自己的頁時不能開席' : c === 'dgrs_needs_switch' ? '這台還沒開「能改程式」的開關' : c === 'provider_unsupported' ? '這個品牌這台開不了' : c === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : e && e.status === 400 && c === 'invalid_request' ? '這版 AIB 還不能從頁面開席' : errText(e);
  }
  async function doAdd(onlySnap) { // onlySnap:登入完自動再送時,要跟被擋那次的 body 快照一樣才送
    const A = S.add, PV = providers(), pv = PV.find((p) => p.key === A.provider && p.open) || PV.find((p) => p.open);
    const persona = A.persona;
    if (!pv || !persona) { A.msg = pv ? '先選一個人格(這一席的個性與專長)' : '要選品牌和人格'; A.err = true; return render(); }
    if (addNoRepo(A) && !A.noRepoAsk) { A.noRepoAsk = true; return render(); } // S0-5:第一下只出提醒,再按一次才開
    const hoPath = String(A.ho || '').trim();
    const mpPath = String(A.mp || '').trim();
    if (mpPath && (!hoPath || !mpPathOk(mpPath))) { A.msg = hoPath ? '記憶包要是完整路徑、檔名 MEMPACK-<5 碼席碼>-<月日>.md(一行、不超過 400 字)' : '填了記憶包也要填交接單'; A.err = true; return render(); }
    if (hoPath && !hoPathOk(hoPath)) { A.msg = '交接單要是完整路徑、檔名 HANDOFF-….md(一行、不超過 400 字)'; A.err = true; return render(); }
    try { localStorage.setItem('ws.lastPersona', persona); } catch (e) { /* 記不住就下次再選 */ }
    A.busy = true; A.step = 1; A.msg = ''; A.grp = null; render();
    let body0 = null;
    try {
      // brain 只在清單是 aibd 給的才送(示意的 uid 不送;舊 aibd 不認這一欄)
      const body = (body0 = { provider: pv.id, mode: A.mode || 'safe', persona_uid: persona, idem_key: A.key });
      if (apPvModel(pv, A)) body.model = A.model;
      if (A.brain && S.reg && S.reg.brainsLive) body.brain = A.brain;
      if (body.mode === 'work' && A.withSeat && A.provider !== 'codex' && !curRepo(A)) { body.with_seat = A.withSeat; if (ownTree(A, S.seats.filter((x) => x && x.mode === 'work' && !isArch(x) && (S.sessions || []).some((se) => se.seat_id === x.seat_id && (se.state === 'running' || se.state === 'pending'))))) body.own_tree = true; }
      // a9136 feaa1c1bb:跟某席同資料夾 / 自己一棵樹時選了「審查」⇒ 開席就帶 role:reviewer(讀得到作者的樹);不帶 = observer
      if (body.with_seat && A.role && (A.role === 'reviewer' || /(^|_)reviewer$/.test(String(A.role)) || ((roleList().find((x) => x.uid === A.role) || {}).name === '審查'))) body.role = 'reviewer';
      if (curRepo(A) && (body.mode === 'dgrs' || (body.mode === 'work' && A.provider !== 'codex'))) body.repo = A.repo;
      // 7b81e 986bce061:帶倉可給 base(40 碼小寫提交)⇒ 這一席的分支從那一顆開(例:全合頭);空白不送
      const bs = String(A.base || '').trim();
      if (body.repo && bs) { if (!/^[0-9a-f]{40}$/.test(bs)) { A.busy = false; A.msg = REPO_WHY.base; A.err = true; return render(); } body.base = bs; }
      // a9136 PREREAD-seat-carry(519d0 10:46):網頁席讀不到 pending ⇒ 只送檔名,aibd 從 pending 拷進席資料夾的 .aib/,回 carried 相對路徑
      const baseOf = (p) => String(p).split(/[\\/]/).pop() || '';
      if (hoPath) body.handoff = baseOf(hoPath);
      if (mpPath) body.mempack = baseOf(mpPath);
      // S2:管理主機帶勾的主機 id(aibd 開席時存進這一席的 hosts);一台都沒勾不送
      if (body.mode === 'host') { const ids = (A.hosts || []).filter((id) => (S.hs.list || []).some((h) => h.id === id)); if (!ids.length) { A.busy = false; A.msg = HS_WHY.hosts_missing; A.err = true; return render(); } body.hosts = ids; }
      // S3 / 7b(DESIGN-brainnote-08de5 §2d):選了模板就送 tpl_uid + rev,aibd 自己從表展開逐項(不信頁面送的名字或逐項);底 = 模板的底
      const T = A.tpl && hasAct('perm_template_apply') === true ? ptOf(A.tpl) : null;
      if (T && body.repo) { A.busy = false; A.msg = PT_WHY.template; A.err = true; return render(); } // 08de5:兩個驗證會搶 ticket 欄,aibd 回 400 reason=template
      if (T) { body.tpl_uid = T.tpl_uid; body.rev = T.rev; body.mode = T.base; }
      // 自動再送:body 不帶 ticket(c2f0b N1:409 在本人窗之前,完全權限照樣要新的本人窗);idem_key 每次新的,不算進快照
      if (typeof onlySnap === 'string' && addSnap(body) !== onlySnap) { A.busy = false; A.msg = '已重新登入;設定跟剛才被擋那次不一樣,沒有自動開,請確認後按「開」'; A.err = false; return render(); }
      A.nlSnap = addSnap(body);
      const r = await fingerAct('seat_open', body, (t, sec) => { if (S.add === A) { A.msg = FP_SEE; render(); } }); // 08de5 A11:dgrs 不帶倉也會 202 要指紋 ⇒ 一律走 fingerAct(不是 202 就照原樣回)
      const seat = r.seat || {};
      let note = '';
      const node = A.node || '';
      if (A.role && node && seat.seat_id) {
        A.step = 2; render();
        try { await act('assign_add', { node_id: node, seat_id: seat.seat_id, role: A.role }); } catch (e) { note = ';席開好了,但沒掛上角色(' + (e && e.code === 'assign_built_in_page_only' ? '用戶資料夾有自己的頁時不能指派' : e && e.code === 'role' ? '這版 AIB 指派只認內建六種,新建的角色還掛不上' : errText(e)) + ')'; } // b4297 e993bd244 實打:assign_add 拒 rol_* → 400 code role
      }
      // 0eed7:提示寫名字(aibd 的 display / name),沒有才寫短碼
      const nm = seat.display || seat.name || seat.code || String(seat.seat_id || '').replace(/^seat_/, '').slice(0, 5) || '新席';
      const ss = r.session || {}, sq = ss.state === 'pending' ? (ss.queue || {}) : null;
      if (sq && Number(sq.slots) > 0) S.maxRun = Number(sq.slots);
      if (sq) note += ';它在排隊:同時最多 ' + (Number(sq.slots) || S.maxRun || '幾') + ' 席在跑、都在用' + (sq.position ? ',排第 ' + sq.position + ' 位' : '') + ',停掉一席它就開始';
      let hoNote = '';
      if (hoPath && seat.seat_id) {
        const C = Array.isArray(r.carried) ? r.carried.map(String) : [];
        const cHo = C.find((x) => /^\.aib\/HANDOFF-/.test(x)), cMp = C.find((x) => /^\.aib\/MEMPACK-/.test(x));
        // 拷進來了 ⇒ 第一句用相對路徑、記憶包每段重讀(網頁席沒有記憶夾);舊 aibd 沒回 carried ⇒ 照舊句
        const line = cHo ? (cMp ? mpFirst(cHo, cMp) : HO_FIRST + cHo) : mpPath ? mpFirst(hoPath, mpPath) : HO_FIRST + hoPath;
        if (ss.state === 'pending') { (S.hoFirst = S.hoFirst || {})[seat.seat_id] = { line, nm, at: Date.now(), tries: 0 }; hoNote = ';它開始跑時會自動送第一句(讀交接檔)'; }
        else { try { await act('seat_input', { seat_id: seat.seat_id, text: line }); hoNote = ';已送第一句(讀交接檔)'; } catch (e) { (S.hoFirst = S.hoFirst || {})[seat.seat_id] = { line, nm, at: Date.now(), tries: 1 }; note += ';第一句還沒送進去,稍後自動再送'; } }
      }
      const mn = await apOpenMates(A, pv, body0, { nm, wait: !!sq });
      apRecentSave(A.plan);
      S.add = null; say('開了 @' + nm + note + hoNote + mn, note || /沒開成/.test(mn) ? 'err' : '');
      // S0-4:開好切到新席的窗(席清單要先拉回來,不然 render 找不到它會退回共同對話)
      await tick(true);
      if (seat.seat_id && seatById(seat.seat_id)) { S.mode = seat.seat_id; S.atOpen = false; render(); } // 不走 setMode:它會清掉上面那句「開了 @… / 在排隊…」
      return;
    } catch (e) {
      // 08de5 定案 3:開席前 aibd 先查登入,沒登入 / 過期回 409 not_logged_in ⇒ 就地出重新登入卡,登入完自動再送這一次
      const nl = e && (e.code === 'not_logged_in' || e.reason === 'not_logged_in' || (e.body && e.body.reason === 'not_logged_in'));
      if (nl && RELOG_NAME[A.provider]) { A.needLogin = A.provider; A.msg = '這台的 ' + RELOG_NAME[A.provider] + ' 沒登入或已過期,席沒有開。按下面「重新登入 ' + RELOG_NAME[A.provider] + '」,登入完會自動再開一次'; A.err = true; A.key = idem(); }
      else { A.msg = body0 && body0.repo ? '沒開成:' + repoWhy(e) : addErr(e); A.err = true; A.key = idem(); } // 沒開成 ⇒ 下一次是新的一席,換新的 idem_key
    }
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
  // 519d0 02:03 N2:配對頁開著時網址換成新的 #code= ⇒ 輸入框換新碼(以前留著過期舊碼,送出回「碼打錯」)
  window.addEventListener('hashchange', () => {
    const h = location.hash || '';
    const m = /(?:^#|&)(?:code|pc)=([A-Za-z0-9-]{4,40})(?:&|$)/.exec(h); if (!m) return;
    const x = /(?:^#|&)exp=(\d{9,11})(?:&|$)/.exec(h);
    const nc = m[1].toUpperCase();
    CODE.cur = nc; CODE.exp = x ? Number(x[1]) * 1000 : 0; CODE.gone = false; CODE.off = false; CODE.tries = 0;
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* 留在網址列也不擋 */ }
    const inp = document.getElementById('pcode'); if (inp) inp.value = nc;
    if (S.pair) { S.pair = Object.assign({}, S.pair, { view: S.pair.view === 'wait' ? 'code' : S.pair.view, msg: '網址帶了新的配對碼,按配對就好', code: nc }); render(); }
  });
  // ebab8 14:00 B6(真 AIB 實看):Windows 客戶不開終端機、不看到 aibd ⇒ 配對頁的字在 Windows 換成「從開始選單或桌面點 AIB」;Mac 原字不動
  // 3e53b 1a7f49dea0 L1:Windows 配對確認在 Windows Hello 視窗(c11e3d607c 起 Hello 第 2 行寫同一組碼),不是「AIB 的視窗」;對碼要說在 Hello 那一步
  const PAIR_WIN_SAY = [['在終端機跑 <code>aib web</code>(或 <code>aibd ctl web</code>),把它給的配對碼打在這裡。', '從開始選單或桌面點「AIB」,它會打開這一頁並給你配對碼,把碼打在這裡。'],
    ['請在終端機再打一次 aibd ctl web', '請從開始選單或桌面再點一次「AIB」'], ['等終端機換一組新的', '等 AIB 換一組新的'], ['終端機會換一組新的', 'AIB 會換一組新的'],
    ['對一下終端機上的碼再送', '對一下 AIB 給的碼再送'], ['在這一頁確認,終端機不用按 y', '在這一頁確認就好'],
    ['<h2>到終端機確認</h2><p>終端機會顯示同一組短碼,一樣就在終端機按 <b>y</b>。</p>', '<h2>在 Windows Hello 視窗確認</h2><p>Windows Hello 視窗會寫同一組碼,一樣才輸入 PIN;不一樣按取消。</p>']];
  const pairSay = (t) => { if (typeof t !== 'string' || !(typeof fpWin === 'function' && fpWin())) return t; let o = t; for (const [a, b] of PAIR_WIN_SAY) o = o.split(a).join(b); return o; };
  function cdLeft() { return CODE.exp ? Math.ceil((CODE.exp - Date.now()) / 1000) : null; }
  function cdText() {
    const n = cdLeft(); if (n == null) return '';
    if (CODE.gone || (CODE.off && n != null && n <= 0)) return pairSay('這組碼過期了,請在終端機再打一次 aibd ctl web');
    return n > 0 ? '這組碼還剩 ' + n + ' 秒' : pairSay('這組碼過期了,等終端機換一組新的…');
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
      // f163c 1a7f49de N1:AIB 設成終端機確認(channel terminal-unverified)時確認真的在終端機按 y ⇒ 等確認框照原字,不換成 Windows Hello
      return (P.chan === 'terminal-unverified' ? (t) => t : pairSay)(`<div class="pairbox"><h2>到終端機確認</h2><p>終端機會顯示同一組短碼,一樣就在終端機按 <b>y</b>。</p>${P.short ? `<div class="short">${esc(P.short)}</div>` : ''}<p class="st">確認後這一頁自己會打開(${esc(P.left != null ? P.left + ' 秒內' : '')})。</p><button type="button" class="ib" data-repair="1">重新輸入配對碼</button></div>`);
    }
    return pairSay(`<form class="pairbox" id="pairf" method="post" action="/api/v1/pair/redeem"><h2>配對這一頁</h2><p>在終端機跑 <code>aib web</code>(或 <code>aibd ctl web</code>),把它給的配對碼打在這裡。</p>${P.msg ? `<p class="bad">${esc(P.msg)}</p>` : ''}<p class="pcd" id="pcd" aria-live="polite">${esc(cdText())}</p><input class="pcode" name="code" id="pcode" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" required placeholder="ABCDE-FGHJK" maxlength="40" value="${esc(P.code || CODE.cur || PREFILL)}" ${P.code || CODE.cur || PREFILL ? 'autofocus' : ''}>${S.pairNew ? `<label class="cip"><input type="checkbox" class="ck" id="pcip" ${S.cip === false ? '' : 'checked'}> 在這一頁確認,終端機不用按 y</label>` : ''}<button type="submit" class="go" ${PAIRING.on ? 'disabled' : ''}>${PAIRING.on ? '配對中…' : '配對'}</button></form>`);
  }
  function render() {
    guidePaintSoon(); // 新用戶指引路線層:畫完下一格重算(不產生就拆掉)
    try { askNotice(); } catch (e) { /* 標題提醒壞了不擋畫面 */ }
    if (imeHold(el)) return; // 組字中不換掉輸入框;組字結束後 imeWire 補畫
    if (S.pair) {
      if (S.gPre === undefined) { S.gPre = 1; guideLoad().then(() => { if (S.guide) render(); }); }
      const k = JSON.stringify([S.pair, S.boot, !!S.pvZone, !!S.guide]);
      if (k === pairShown) return;
      pairShown = k;
      el.innerHTML = `${pvBar()}<div class="bar0"><div class="brandt">多席工作台</div><div class="grow"></div><a class="ib" href="contacts.html" title="原聯絡中心;所有席的信分四欄、光點從寄件人流到收件人">📥 待審區</a></div>${guidePreHtml()}${pairHtml()}`;
      const c = document.getElementById('pcode'); if (c) c.focus();
      return;
    }
    pairShown = '';
    const apK = apKeep(el); // S1b 2
    const keepScroll = (() => { const t = document.getElementById('tl'); return t ? { top: t.scrollTop, atEnd: t.scrollTop + t.clientHeight >= t.scrollHeight - 30 } : null; })();
    const inp = document.getElementById('inp');
    const focus = inp && document.activeElement === inp ? { a: inp.selectionStart, b: inp.selectionEnd } : null;
    const dwf = document.activeElement && document.activeElement.matches && document.activeElement.matches('[data-drvwhy]') ? { a: document.activeElement.selectionStart, b: document.activeElement.selectionEnd } : null;
    const ghf = document.activeElement && document.activeElement.matches && document.activeElement.matches('[data-ghnum]') ? { a: document.activeElement.selectionStart, b: document.activeElement.selectionEnd } : null;
    const sqe = document.getElementById('sq'), sqF = sqe && document.activeElement === sqe ? { a: sqe.selectionStart, b: sqe.selectionEnd } : null;
    const ae = document.activeElement, ansF = ae && ae.dataset && ae.dataset.ansfor ? { card: ae.dataset.ansfor, a: ae.selectionStart } : null;
    const seat = S.mode !== 'shared' && S.mode !== 'cat' ? seatById(S.mode) : null;
    if (S.mode === 'node' && !S.nt) S.mode = 'shared';
    if (S.mode === 'cat' && !S.cat) S.mode = 'shared';
    if (S.mode !== 'shared' && S.mode !== 'node' && S.mode !== 'cat' && !seat && S.seats.length) S.mode = 'shared';
    const P = present();
    // 0eed7 VERIFY-humanpath N1:單擊頭像是插 @、雙擊才開席窗,停止鈕在席窗裡 ⇒ 頭像下多一個看得到的「開」
    // a9136 654162420:停掉的席可以收掉(act seat_archive);先在頁面上問一次,不用瀏覽器彈窗
    const aa = S.archAsk && seatById(S.archAsk);
    const archBar = aa ? `<div class="pvwarn">收掉 @${esc(seatShort(aa))}?收掉後不再列在這裡、也不能重開;它的對話紀錄還在。${aa.folder_owner && aa.folder_owner.how === 'tree' || S.seatGuest[aa.seat_id] ? '它自己一棵樹裡還沒合的提交會留著,右欄出一張卡提醒你。' : ''}<button type="button" class="k" data-archno="1">不要</button><button type="button" class="k g" data-archgo="${esc(aa.seat_id)}">收掉</button></div>` : '';
    const mb = S.mbAsk && seatById(S.mbAsk), mbt = mb && mb.tree ? mb.tree : null;
    const mbBar = mb ? `<div class="pvwarn">把 @${esc(seatShort(mb))} ${mbt && Number(mbt.unmerged) > 0 ? '的 ' + esc(String(mbt.unmerged)) + ' 個提交' : '的新提交'}合回主人那一份${mbt && mbt.base ? '(' + esc(mbt.base) + ')' : ''}?有衝突會停下、復原,不會硬蓋。${mbt && mbt.dirty > 0 ? '它還有 ' + esc(String(mbt.dirty)) + ' 個檔沒提交,AIB 會擋;先叫它提交再合回。' : ''}<button type="button" class="k" data-mbno="1">不要</button><button type="button" class="k g" data-mbgo="${esc(mb.seat_id)}">合回</button></div>` : '';
    const pp = archBar + mbBar + tickBar() + `<div class="pp">${P.length ? P.map((s) => `<span class="ppi">${logo(s, 'big', S.mode === s.seat_id ? 'pick' : '')}<em class="ppn" title="${esc(seatShort(s))}">${seatTail(s)}</em>${(() => { const q = queuedOf(s); return q ? `<small class="tt qd" title="${esc(qText(q))}">排隊中${q.pos ? ' · 第 ' + q.pos + ' 位' : ''}</small>` : ''; })()}${treeTag(s)}${tickTag(s)}<button type="button" class="ppo" data-openseat="${esc(s.seat_id)}" title="打開 @${esc(seatName(s))} 的席窗(看它在做什麼、停止)">開</button>${treeBtn(s)}</span>`).join('') : '<span class="none">沒有在跑的 2.0 席</span><button type="button" class="k g startc" data-startclaude="1" title="用預設(Claude · 討論研究 · 內建人格)開一席">開始用 Claude</button>'}${stoppedSeats().map((s) => `<span class="ppi off" title="@${esc(seatName(s))} 停了">${logo(s, 'big', 'off')}<em class="ppn" title="${esc(seatShort(s))}">${seatTail(s)}</em><button type="button" class="ppo g" data-restart="${esc(s.seat_id)}" title="重開 @${esc(seatName(s))}(同一席開新的一段,之前的對話還在)">重開</button>${treeBtn(s)}<button type="button" class="ppo" data-archask="${esc(s.seat_id)}" title="收掉 @${esc(seatName(s))}(不再列在這裡;它自己一棵樹裡沒合的提交會留著並出卡提醒)">收掉</button></span>`).join('')}${S.seats.some(isLegacy) ? '' : '<span class="oldph" title="舊式席(不是網頁開的),等 7ebca 的鏡像(N-web-dmbridge)接上就跟這些席排在一起;現在還沒有資料">舊式席 · 示意</span>'}<span class="lg big you" title="你">你</span><button type="button" class="addb" data-add="1" title="新增 AI 夥伴">+ AI 夥伴${netDot()}</button></div>`;
    const nt = S.nt ? `<button type="button" class="md ${S.mode === 'node' ? 'on' : ''}" data-mode="node">💬 ${esc(String(S.nt.title || S.nt.node || '項目').slice(0, 24))}</button>` : '';
    const tabs = `<div class="mode"><button type="button" class="md ${S.mode === 'shared' ? 'on' : ''}" data-mode="shared">共同對話</button>${nt}${seat ? `<button type="button" class="md on">${logo(seat, 'sm')} @${esc(seatName(seat))}</button>` : ''}</div>`;
    el.innerHTML = `${pvBar()}${bootBar()}<div class="bar0"><div class="brandt">多席工作台</div>${counts()}<div class="grow"></div>${pvBox()}<input id="sq" class="sq" type="search" placeholder="🔍" aria-label="搜尋" autocomplete="off" maxlength="200" value="${esc(S.q)}">${tickStop()}${inboxBtn()}<a class="ib" href="contacts.html" title="原聯絡中心;所有席的信分四欄、光點從寄件人流到收件人">📥 待審區</a></div>
      ${S.newVer ? `<div class="banner newver" role="status">AIB 換了新版,這一頁還是舊的畫面程式。<button type="button" class="k g" data-reload="1">重新載入</button></div>` : ''}${S.banner ? `<div class="banner ${S.bannerKind}">${esc(S.banner)}</div>` : ''}
      ${decideHtml()}<div class="upper">
        <div class="lcol"><div class="box">${treeHtml()}</div><div class="box host">${funcHtml()}</div></div>
        ${S.add ? guideDim(addHtml()) : ''}<div class="box ctr">${S.q.trim() ? searchWin() : `${tabs}${catBar()}${pp}${seat ? seatWin(seat) : S.mode === 'node' ? nodeWin() : S.mode === 'cat' ? catWin() : seatNowBox(P) + sharedWin()}`}</div>
        <div class="box">${slotsHtml()}</div>
      </div>`;
    const t = document.getElementById('tl');
    if (t) t.scrollTop = !keepScroll || keepScroll.atEnd ? t.scrollHeight : keepScroll.top;
    apRestore(el, apK); // S1b 2:加夥伴視窗停在原地
    heatMount(); // 即時熱度:常駐節點放回位子
    ctPaint(el);
    if (dwf) { const di = document.querySelector('[data-drvwhy]'); if (di) { di.focus(); try { di.setSelectionRange(dwf.a, dwf.b); } catch (e) { /* 舊瀏覽器 */ } } }
    if (ghf) { const gi = document.querySelector('[data-ghnum]'); if (gi) { gi.focus(); try { gi.setSelectionRange(ghf.a, ghf.b); } catch (e) { /* 數字框不給設 */ } } }
    if (focus) { const i = document.getElementById('inp'); if (i) { i.focus(); i.setSelectionRange(focus.a, focus.b); } }
    if (sqF) { const i = document.getElementById('sq'); if (i) { i.focus(); try { i.setSelectionRange(sqF.a, sqF.b); } catch (e) { /* type=search 某些瀏覽器不給 */ } } }
    if (ansF) { const i = [...el.querySelectorAll('[data-ansfor]')].find((x) => x.dataset.ansfor === ansF.card); if (i) { i.focus(); i.setSelectionRange(ansF.a, ansF.a); } }
  }

  // ---------- 動作 ----------
  function say(msg, kind) { S.hint = (typeof fpSay === 'function' ? fpSay : (t) => t)(msg); S.hintKind = kind || ''; } // 3e53b 2a6a99a871 L1:提示列也跟平台走
  // 0eed7 pv7 L(519d0 07:46):指紋閘擋下的三種講真正原因;滿了講幾點幾分幾秒後可以再試(鐘點是 aibd 給的 retry_hms,同指紋窗的字)
  function gateWhy(e) {
    const b = (e && e.body) || {}, rs = e && (e.reason || b.reason || e.code);
    if (rs === 'pair_touch_limited') { const t = String(b.retry_hms || ''); return (typeof fpSay === 'function' ? fpSay : (t) => t)('10 分鐘內最多 3 張指紋窗(一小時最多 6 張)' + (/^\d\d:\d\d:\d\d$/.test(t) ? ',' + t + ' 後可以再試' : ',晚點再試')); } // f163c 36317e8b N1:自己包,不靠呼叫端
    return (typeof fpSay === 'function' ? fpSay : (t) => t)(rs === 'pair_touch_busy' || rs === 'pair_touch_cooling' ? REPO_WHY[rs] : '');
  }
  function errText(e) { return (typeof fpSay === 'function' ? fpSay : (t) => t)(errText0(e)); }
  function errText0(e) {
    if (!e) return '出錯了';
    if (gateWhy(e)) return gateWhy(e);
    if (e.pageMsg) return e.pageMsg;
    { const rc = e.reason || (e.body && e.body.reason) || e.code; if (rc !== 'unavailable' && rc !== 'cancel') { const ow = ownerWhy(rc, e); if (ow) return ow; } }
    if (e.code === 'finger_cancel') return '你按了取消這次,沒有執行';
    if (e.status === 0) return '連不上 AIB';
    if (e.code === 'user_page_safe_seats_only') return '用戶資料夾的頁只能對安全模式的席送字';
    // S89-pair-carry:換版延續來的配對只能讀、只能對安全模式的席送字(519d0 r16 裁定 3,b4297 eb0bf164)
    if (e.code === 'carried_pair_read_and_safe_only') return inPreview() ? PV_CARRIED : '這個配對是換版前延續來的,只能看和對安全模式的席送字;要開席或改指派請重新配對';
    if (e.code === 'carried_pair_safe_seats_only') return inPreview() ? PV_CARRIED : '這個配對是換版前延續來的,只能對安全模式的席送字;要對這一席說話請重新配對';
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
    waitMark(seatId, text, false); render();
    let r;
    // a9136 ba9112bd7:seat_input 加 media:[id](1–4 張),aibd 拷進席的 附圖/ 並把路徑接在字後;409 attach_failed = 整封沒打,附件留著
    const media = CT.att.map((a) => a.id).slice(0, 4);
    try { r = await act('seat_input', media.length ? { seat_id: seatId, text, media } : { seat_id: seatId, text }); } catch (e) { delete S.wait[seatId]; (S.said[seatId] || []).pop(); if (e && (e.code || e.reason) === 'attach_failed') e.message = '附圖沒交上去,整封沒送(附件還在,可以再按送出)'; throw e; }
    if (media.length) CT.att = [];
    if (r.__status === 202 && S.wait[seatId]) S.wait[seatId].queued = true;
    return r.__status === 202 ? '它正在忙,這句排在後面;這一回合結束就收到' : '已送出';
  }
  // 共同對話:打 @ ⇒ 對被 @ 的席各送一次(message_send 存成一筆、有 id、看得到送達)
  // 點選補進輸入框的字:看得懂的席名(沒空白、在場只有它叫這個名字),不然用 seat_id
  const atName = (x) => seatShort(x); // 真機 display 帶「· claude · work」尾巴(0eed7 VERIFY-restart L1)
  const atWord = (k) => { const x = seatById(k), nm = x ? atName(x) : ''; return nm && !/\s/.test(nm) && present().filter((y) => atName(y) === nm).length === 1 ? nm : k; };
  const atKeys = (x) => [String(x.seat_id || ''), String(x.code || ''), String(x.name || ''), seatName(x), seatShort(x)].map((y) => y.toLowerCase()).filter(Boolean);
  function targets(text) {
    const P = present();
    const amb = new Set(); targets.amb = [];
    if (/(^|\s)@all(\s|$)/i.test(text)) return P;
    const out = [];
    for (const m of text.matchAll(/@([^\s@,,、。:]+)/g)) {
      const k = m[1].toLowerCase();
      // 先找在場的席(同名的舊席已停),席碼優先於名字
      // 席碼(5 碼,同 seats.rs seat_code)也認:aibd 只看 to 陣列,打字 @d4809 要在這裡換成 seat_id(a9136 06:11)
      const codeOf = (x) => String(x.code || String(x.seat_id).replace(/^(seat_|old_)/, '').slice(0, 5)).toLowerCase();
      // 0eed7 VERIFY-tree 738b5799 M1:名字對到兩席以上不猜(以前取第一個 ⇒ 想叫第三席卻送給作者);記下來,「會送給」那行講、送出擋
      const one = (L, f) => { const M = L.filter(f); if (M.length > 1) { amb.add(m[1]); return null; } return M[0] || null; };
      // 0eed7 VERIFY-realentry 5858545e L1:aibd 只給後開那席加尾碼時,「@名字」在 seatShort 那關會唯一命中沒尾碼那席 ⇒ 先看光名字對到幾席,≥2 就不猜(要打 名字·席碼)
      const bare = (x) => String(seatName(x)).split(' · ')[0].trim().toLowerCase();
      const nameAmb = (L) => { if (L.filter((x) => !isArch(x) && bare(x) === k).length > 1) { amb.add(m[1]); return true; } return false; };
      const hit = (L) => L.find((x) => String(x.seat_id).toLowerCase() === k) || L.find((x) => codeOf(x) === k) || (nameAmb(L) ? null : one(L, (x) => seatShort(x).toLowerCase() === k)) || one(L, (x) => String(x.name || '').toLowerCase() === k || seatName(x).toLowerCase() === k);
      // 519d0 示範後 (3):認席名的一部分 —— 完整的認不到時,在場的席裡只有一席含這段字就是它(兩席以上不猜)
      const part = () => { const L = P.filter((x) => atKeys(x).some((y) => y.includes(k))); return L.length === 1 ? L[0] : null; };
      const before = amb.size;
      const s = hit(P) || (amb.size > before ? null : hit(S.seats) || (k.length >= 2 ? part() : null));
      if (s) amb.delete(m[1]);
      if (s && !out.includes(s)) out.push(s);
    }
    targets.amb = [...amb];
    return out;
  }
  const idem = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
  // 共同對話自己的那一串:沒掛項目、標題「共同對話」;多段時用最早那段(跟 thread_open 冪等同規則)
  const SHARED_TITLE = '共同對話';
  function findShared(list) {
    const c = (list || []).filter((t) => t && t.thread_id && !t.node && String(t.title || '') === SHARED_TITLE);
    c.sort((a, b) => String(a.created || '').localeCompare(String(b.created || '')));
    return c.length ? c[0].thread_id : '';
  }
  async function ensureShared() {
    if (S.sharedTid) return S.sharedTid;
    const L = await get('/api/v1/threads?limit=200');
    let id = findShared(L && L.threads);
    // a9136 02:41:不帶 node 的 thread_open 每次新開、不冪等 ⇒ 先找;成員開串時寫死,帶當下全部席
    if (!id) id = await openShared();
    S.sharedTid = id; return id;
  }
  // a9136 1102a001e:reuse:'title' ⇒ 有同標題自由串回最早那串並補齊成員(200 existing),沒有才新開;
  // 舊 aibd 不認 reuse 會直接忽略、照樣新開(a9136 02:46)⇒ 回應帶 existing(true / false)才算認得 reuse(S.reuseOk);
  // 回應沒有 existing ⇒ 這一頁記成不支援(舊 aibd 每載入一次頁面最多多開一段)。平常先 GET 找,所以不會重開
  const sharedMembers = () => S.seats.filter((x) => !isLegacy(x)).map((x) => ({ seat: 'seat:' + x.seat_id, role: 'observer' }));
  const tidOf = (r) => (r && (r.thread_id || (r.thread && r.thread.thread_id))) || '';
  async function openShared(reuseOnly) {
    try { const r = await act('thread_open', { title: SHARED_TITLE, members: sharedMembers(), reuse: 'title' }); if (r && typeof r.existing === 'boolean') S.reuseOk = true; else S.reuseOk = false; return reuseOnly && !S.reuseOk ? '' : tidOf(r); }
    catch (e) { if (reuseOnly || !(e && e.status === 400)) throw e; return tidOf(await act('thread_open', { title: SHARED_TITLE, members: sharedMembers() })); }
  }
  // ebab8 12:20(Andy「要最快讓用戶一鍵可用」):只有一席在跑、這句沒 @ ⇒ 就交給那一席;兩席以上照舊要 @
  const soleSeat = () => { const L = present().filter((s) => !isArch(s)); return L.length === 1 && !isLegacy(L[0]) ? L[0] : null; }; // f163c L1:活著的 1.x 席也算一席(兩席以上要 @)
  // 3e53b 5a8d46f5eb L1:只在這句完全沒有 @ 時才交給唯一那席;打了 @ 但對不上(打錯名、@ 封存席)照舊擋
  const soleTarget = (text, T) => (!T.length && !(targets.amb || []).length && !String(text).includes('@') ? soleSeat() : null);
  async function sendShared(text) {
    let T = targets(text);
    { const one = soleTarget(text, T); if (one) T = [one]; }
    if ((targets.amb || []).length) { say(targets.amb.map((n) => '@' + n).join(' ') + ' 有兩席以上同名,這句沒送;點 @ 選頭像(名字後面有席碼),或打 @席碼', 'err'); return false; }
    if (!T.length) { say('沒有 @ 到在場的席;今晚的共同對話要 @ 誰才送得到', 'err'); return false; }
    // 0eed7 17:54:@ 到停掉的席,aibd 才在串裡貼「沒在跑」⇒ 頁面先擋,叫人先重開
    const off = T.filter(isStopped);
    if (off.length) { say(off.map((s) => '@' + seatShort(s)).join(' ') + ' 停了,這句沒送;先按它頭像下的「重開」再送', 'err'); return false; } // 0eed7 VERIFY L1:只用席名,不帶「· claude · work」
    // a9136 02:36:改送 thread_send(aibd 投遞記來源,席回合結束把回答帶回同一串);舊 aibd 沒有串 ⇒ 退回一對一信
    if (!S.itemOff) {
      let tid = '';
      try { tid = await ensureShared(); } catch (e) { if (!(e && (e.status === 404 || e.status === 405 || e.code === 'unknown_act_type'))) { say(errText(e), 'err'); return false; } }
      if (tid) {
        try { await act('thread_send', { thread_id: tid, body: text, to: T.map((s) => s.seat_id), idem_key: idem() }); for (const x of T) waitMark(x.seat_id, null, running(x.seat_id) && !!(turnOf(x.seat_id) || {}).running); { const QT = T.filter((s) => queuedOf(s)); say(QT.length ? '已送出,但 ' + QT.map((s) => '@' + seatShort(s)).join(' ') + ' 還在排隊,排到才看得到' : '已送給 ' + T.map((s) => '@' + seatName(s)).join(' ') + ';回答會回到這裡', QT.length ? 'err' : ''); } return true; }
        catch (e) {
          // 開串之後才加的席不是成員 ⇒ 退回一對一信(回答不會帶回這裡,照實說)
          if (!(e && e.code === 'to_not_a_member')) { say(errText(e), 'err'); return false; }
          // 先補成員再送一次(新 aibd);補不了才退一對一信
          // 0eed7 VERIFY-m2 L1:重新整理後不記得 reuseOk ⇒ 沒確定不支援就直接試;舊 aibd(回應沒有 existing)這一頁記成不支援,不再試
          try { const t2 = S.reuseOk === false ? '' : await openShared(true); if (t2) { S.sharedTid = t2; await act('thread_send', { thread_id: t2, body: text, to: T.map((s) => s.seat_id), idem_key: idem() }); say('已送給 ' + T.map((s) => '@' + seatName(s)).join(' ') + '(先把新加的席補進共同對話);回答會回到這裡'); return true; } } catch (e2) { /* 舊 aibd:沒有補成員 */ }
          const ok = await sendDirect(T, text);
          if (ok) say('有席是開這串之後才加的,改用一對一信送出;它的回答在它自己的視窗,不會回到這裡', 'err');
          return ok;
        }
      }
    }
    return sendDirect(T, text);
  }
  async function sendDirect(T, text) {
    if (!S.me) { say('讀不到你自己的聯絡人(這版 AIB 沒有聯絡中心),送不出', 'err'); return false; }
    const res = await Promise.allSettled(T.map((s) => act('message_send', { to_seat: 'seat:' + s.seat_id, body: text, thread_id: threadId(S.me, 'seat:' + s.seat_id), idem_key: idem() })));
    for (const r of res) if (r.status === 'fulfilled' && r.value && r.value.message_id) S.sentText[r.value.message_id] = text;
    const bad = res.map((r, i) => (r.status === 'rejected' ? '@' + seatName(T[i]) + ' ' + errText(r.reason) : '')).filter(Boolean);
    const QT = T.filter((s) => queuedOf(s));
    say(bad.length ? bad.join(';') : QT.length ? '已送出,但 ' + QT.map((s) => '@' + seatShort(s)).join(' ') + ' 還在排隊,排到才看得到' : '已送給 ' + T.map((s) => '@' + seatName(s)).join(' '), bad.length || QT.length ? 'err' : '');
    return bad.length < T.length;
  }
  // 7ebca 49de928f1:網頁寫信給舊式席 = act legacy_send;信立刻出現在「import ↔ <碼>(信件匯入)」那串
  const LEGACY_ERR = {
    legacy_off: 'AIB 沒有接上舊信件帳(AIBD_LEGACY_CABIN 沒設),這封沒寄出',
    legacy_send_built_in_page_only: '只有內建頁能寄信給舊式席',
    legacy_send_pair_this_run: '這一頁要在這次啟動的 AIB 上重新配對才能寄',
    seat_unknown: '席碼不對',
    seat_not_in_cabin: '這一席不在這個艙',
    legacy_send_rate: '寄太快了:每一席 1 分鐘最多 6 封,等一下再寄',
  };
  async function sendLegacy(code, text) {
    try {
      const r = await act('legacy_send', { seat: code, text, idempotency_key: idem() });
      say((r && r.again ? '這封剛才已寄過,沒有再寄一次' : '寄出了') + (r && r.dm_id ? '(信件 ' + r.dm_id + ')' : '') + ',會出現在它的信件串裡');
      if (S.legacyThreads) delete S.legacyThreads[code];
      return true;
    } catch (er) {
      const c = er && er.code, b = (er && er.body) || {};
      if (c === 'unknown_act_type') say('AIB 還沒有寄信給舊式席的動作,這封沒寄出 · 示意', 'err');
      else if (c === 'lint_refused') say('信被檢查擋下:' + String(b.detail || b.why || er.message || '').slice(0, 200), 'err');
      else if (c && Object.hasOwn(LEGACY_ERR, c)) say(LEGACY_ERR[c], 'err');
      else say(errText(er), 'err');
      return false;
    }
  }
  async function doSend(kind, code) {
    const i = document.getElementById('inp');
    const text = (i ? i.value.trim() : '') || (kind === 'seat' && CT.att.length ? '(附圖)' : '');
    if (!text || S.sending) return;
    if (CT.att.length && kind !== 'seat') { CT.note = '附圖現在只能在單席對話送(共同對話、舊席寄信 AIB 還沒接);附件先留著'; render(); }
    S.sending = true; render();
    try {
      if (kind === 'seat' && S.cinbAns && S.cinbAns.seat === S.mode) { if (await cinbAnswer(text)) S.drafts[S.mode] = ''; }
      else if (kind === 'seat') { say(await sendSeat(S.mode, text)); S.drafts[S.mode] = ''; }
      else if (kind === 'legacy') { if (await sendLegacy(code, text)) S.drafts[S.mode] = ''; }
      else if (kind === 'node') { await sendNode(text); S.drafts.node = ''; }
      else if (await sendShared(text)) S.drafts.shared = '';
      ctHistAdd(text, kind === 'seat' ? 'seat:' + S.mode : kind === 'legacy' ? 'seat:' + code : kind); CT.draft = null; // 送出紀錄 100 則(送失敗不會到這行)
    } catch (e) { say(errText(e), 'err'); }
    // 0eed7 量用量 N1:送完先把「送出」放開再去重讀(重讀在負載高時要好幾秒,之前這段時間鈕一直是灰的)
    S.sending = false; S.atOpen = false; render();
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
      // 0eed7 VERIFY-pkg-ec695674 H1:舊 aibd 不認 n / confirm_in_page,多帶就 401「碼打錯了」。
      // 只有 GET pair/prefs 答得出 confirm_in_page(7b81e 845ef9128 起)才帶這兩欄;舊的只送碼
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
      msg = pairSay(c === 'pairing_rate_limited' ? '試太多次了,等一下再試'
        : c === 'pairing_code_expired' ? '這組碼過期了' + (CODE.gone ? ',請在終端機再打一次 aibd ctl web' : ',終端機會換一組新的,框裡會跟著換')
          : c === 'pairing_code_reused' ? '這組碼已經用過了,請在終端機再打一次 aibd ctl web 拿新碼'
            : c === 'pairing_code_wrong' || (c === 'pairing_failed' && r.status === 401) ? '碼打錯了,對一下終端機上的碼再送'
              : r.status === 403 ? '被 AIB 擋下(' + (c || r.status) + ')' : '配對碼不對或已過期');
      if (c === 'pairing_code_expired') { CODE.exp = Date.now() - 1; CODE.off = false; codePoll(); }
    } catch (er) { msg = '連不上 AIB'; }
    PAIRING.on = false; bootEnd(false);
    S.pair = { view: 'code', msg, code: /過期/.test(msg) ? '' : code }; render(); // 打錯 / 用過:留著打的字好改;過期:讓換來的新碼頂上
  });
  el.addEventListener('dblclick', (e) => {
    const lg = e.target.closest('[data-seat]');
    if (lg && seatById(lg.dataset.seat)) { e.preventDefault(); setMode(lg.dataset.seat); }
  });
  async function onMainClick(e) {
    const x = e.target;
    if (x.closest('.gdim')) return; // 新手期暗格:畫面上不能按(不是權限,也不報錯)
    const gdo = x.closest('[data-ggo]'); if (gdo) return guideGo(gdo.dataset.ggo);
    const gtw = x.closest('[data-gtw]'); if (gtw) { const O = S.gOpen || (S.gOpen = {}), k = gtw.dataset.gtw, P = gProj(S.guide, k); O[k] = !(gOwn(O, k) ? O[k] : !(P && gSteps(P, k).every((s) => ['ok', 'later'].includes(gState(s))))); return render(); }
    if (x.closest('[data-gagain]')) return guideSkip(false);
    if (x.closest('[data-hspick],[data-hsnew],[data-hsno],[data-hsok],[data-hsdel],[data-hsdelno],[data-hsdelok]') && await hsClick(x)) return; // S2 管理主機
    if (x.closest('[data-rgedit],[data-rgdel],[data-rgno],[data-rgok],[data-rgback],[data-rgdelok],[data-rgundo],[data-rgao],[data-rgrs]') && await rgClick(x)) return; // S4 人格 / 大腦 / 角色
    if (x.closest('[data-ptnew],[data-ptfine],[data-ptedit],[data-ptdel],[data-ptdelno],[data-ptdelok],[data-ptbase],[data-ptit],[data-ptno],[data-ptok],[data-ptpick],[data-ptsa],[data-ptap],[data-ptrv]') && await ptClick(x)) return; // S3 權限模板
    // c44:這一輪模型沒有回應 ⇒ 把你最後一句 @ 它的話再送一次(同一串、只送它)
    const hob = x.closest('[data-handoff]'); if (hob && !S.sending) return hoAsk(hob.dataset.handoff);
    const hcl = x.closest('[data-hoclear]'); if (hcl && !S.sending) { const st = hoState(hcl.dataset.hoclear); S.hoConfirm = st && st.wrote ? { seat: hcl.dataset.hoclear, at: st.wrote } : null; S.hoMsg = null; return render(); }
    const hcy = x.closest('[data-hoclearyes]'); if (hcy && !S.sending) return hoClear(hcy.dataset.hoclearyes);
    const hcn = x.closest('[data-hoclearno]'); if (hcn) { S.hoConfirm = null; return render(); }
    const gpl = x.closest('[data-ghpill]'); if (gpl) { const G = (S.gh = S.gh || {}); G.open = gpl.dataset.ghpill === '1'; render(); if (G.open) { ghListLoad(); const i = document.querySelector('[data-ghnum]'); if (i) i.focus(); } return; }
    const gpk = x.closest('[data-ghpick]'); if (gpk) { const G = (S.gh = S.gh || {}); G.num = gpk.dataset.ghpick; return ghAsk('pr', G.num); }
    const gg = x.closest('[data-ghgo]'); if (gg) { const G = (S.gh = S.gh || {}), ni = document.querySelector('[data-ghnum]'); if (ni) G.num = ni.value.trim(); return ghAsk(gg.dataset.ghgo, G.num); }
    const gr = x.closest('[data-ghrun]'); if (gr) return ghAsk('run', gr.dataset.ghrun);
    const pu = x.closest('[data-push]'); if (pu) { pu.disabled = true; return pushGo(pu.dataset.push); }
    const pr = x.closest('[data-pr]'); if (pr && S.sbOk) { S.prAsk = { seat: pr.dataset.pr, title: 'web: ' + S.sbOk.branch }; S.pushMsg = null; render(); return; }
    if (x.closest('[data-prno]')) { S.prAsk = null; render(); return; }
    const gt = x.closest('[data-ghtokgo]'); if (gt) return ghTokGo(gt.dataset.ghtokgo);
    if (x.closest('[data-ghtokno]')) { S.ghTok = null; render(); return; }
    const pg = x.closest('[data-prgo]'); if (pg && !S.sending) { const ti = document.querySelector('[data-prtitle]'), tb = document.querySelector('[data-prbody]'); if (ti && S.prAsk) S.prAsk.title = ti.value.trim(); if (tb && S.prAsk) S.prAsk.body = tb.value; return prGo(pg.dataset.prgo); }
    const rq = x.closest('[data-rcpask]'); if (rq) { S.rcpAsk = { seat: rq.dataset.rcpask, scope: 'narrow', tests: '' }; S.rcpMsg = null; render(); return; }
    const rsc = x.closest('[data-rcpscope]'); if (rsc && S.rcpAsk) { const ti0 = document.querySelector('[data-rcptests]'); if (ti0) S.rcpAsk.tests = ti0.value; S.rcpAsk.scope = rsc.dataset.rcpscope; render(); return; }
    if (x.closest('[data-rcpno]')) { S.rcpAsk = null; render(); return; }
    const rg = x.closest('[data-rcpgo]'); if (rg && !S.sending) { const ti = document.querySelector('[data-rcptests]'); if (ti && S.rcpAsk) S.rcpAsk.tests = ti.value; return rcpGo(rg.dataset.rcpgo); }
    const rrd = x.closest('[data-rcpread],[data-rcpwd]'); if (rrd) { rrd.disabled = true; return rrd.dataset.rcpread ? rcpAct('receipt_read', rrd.dataset.rcpread) : rcpAct('receipt_withdraw', rrd.dataset.rcpwd); }
    const sbb = x.closest('[data-sendback]'); if (sbb && sbHeld(sbb.dataset.sendback)) { S.sbMsg = { seat: sbb.dataset.sendback, bad: true, text: '這一席正在做事,沒有交回。這一輪做完再按「交回本機原倉」(不用停席)' }; say(S.sbMsg.text, 'err'); return render(); }
    if (sbb) { sbb.disabled = true; return sendBack(sbb.dataset.sendback); }
    if (lcClick(x)) return;
    const rlb = x.closest('[data-relogin]'); if (rlb && !(S.relog && !S.relog.done)) return relogin(rlb.dataset.relogin);
    if (x.closest('[data-relogsend]')) return relogSend();
    if (x.closest('[data-relogcopy]')) return relogCopy();
    const mvb = x.closest('[data-move]'); if (mvb) { S.moveAsk = mvb.dataset.move; S.moveCode = ''; S.moveMsg = null; render(); const ci = document.querySelector('[data-movecode]'); if (ci) ci.focus(); return; }
    if (x.closest('[data-moveno]')) { S.moveAsk = null; render(); return; }
    const mvg = x.closest('[data-movego]'); if (mvg && !S.moveF) { const ci = document.querySelector('[data-movecode]'); mvg.disabled = true; return seatMove(mvg.dataset.movego, String(ci ? ci.value : S.moveCode || '').trim()); }
    const umv = x.closest('[data-unmove]'); if (umv) { umv.disabled = true; return seatUnmove(umv.dataset.unmove); }
    const trb = x.closest('[data-trust],[data-untrust]'); if (trb) { trb.disabled = true; return trb.dataset.trust ? seatTrust(trb.dataset.trust, true) : seatTrust(trb.dataset.untrust, false); }
    const rsb = x.closest('[data-resend]'); if (rsb) { rsb.disabled = true; return resend(rsb.dataset.resend); }
    // 頂端「等你決定」:收合 / 看更多 / 已答
    if (x.closest('[data-dqtoggle]')) { S.dqOpen = S.dqOpen === false; return render(); }
    if (x.closest('[data-dqmore]')) { S.dqMore = !S.dqMore; return render(); }
    if (x.closest('[data-dqdone]')) { S.dqDoneOpen = !S.dqDoneOpen; return render(); }
    const up = x.closest('[data-pvup]'); if (up) { e.stopPropagation(); return upOpen(up.dataset.pvup); }
    if (x.closest('[data-bootretry]')) { location.reload(); return; } // 憑證在這個分頁裡,重新載入不會再送配對碼
    if (x.closest('[data-sr]')) { S.q = ''; SR.seq++; clearTimeout(SR.timer); S.sr = null; const sq = document.getElementById('sq'); if (sq) sq.value = ''; render(); } // 點一筆:跳過去,搜尋收起(框清空、中欄回原樣;0eed7 L2:之前只清了 S.q,沒重畫,框與結果都還在)
    const md = x.closest('[data-mode]'); if (md) return setMode(md.dataset.mode);
    const tha = x.closest('[data-thadd],[data-thrm]'); if (tha) return thAct(tha.dataset.thadd ? 'add' : 'rm', tha.dataset.thadd || tha.dataset.thrm);
    if (x.closest('[data-threload]')) { const o = !!(S.th && S.th.open); S.th = null; await thLoad(); if (S.th) { S.th.open = o; render(); } return; }
    if (x.closest('[data-thopen]')) { thWantSet(true); if (!S.th || !S.th.list) await thLoad(); if (S.th) S.th.open = true; return render(); }
    if (x.closest('[data-thclose]')) { thWantSet(false); S.th = null; return render(); }
    if (x.closest('[data-tsreload]')) { S.ts = null; return tsLoad(); }
    if (x.closest('[data-tschattog]')) { S.tsChatOpen = !S.tsChatOpen; return render(); }
    const tsf = x.closest('[data-tschatf]'); if (tsf) { S.tsChatFilter = tsf.dataset.tschatf; return render(); }
    if (x.closest('[data-plgreload]')) { S.plg = null; return plgLoad(); }
    const plgB = x.closest('[data-plgset]'); if (plgB) return plgSet(plgB.dataset.plgset, plgB.dataset.plgon === '1');
    if (x.closest('[data-mktreload]')) { S.mkt = null; return mktLoad(); }
    const mktB = x.closest('[data-mktinst]'); if (mktB) return mktInstall(mktB.dataset.mktinst);
    if (x.closest('[data-tierreload]')) { S.tier = null; return tierLoad(); }
    const tierB = x.closest('[data-tierpick]'); if (tierB) return tierPick(tierB.dataset.tierpick);
    const ft = x.closest('[data-ftab]'); if (ft) { S.ftab = ft.dataset.ftab; try { localStorage.setItem('aib.ws.ftab', S.ftab); } catch (e) { /* 存不了:這次有效就好 */ } return render(); }
    const ct = x.closest('[data-cat]'); if (ct) { S.cat = ct.dataset.cat; S.catThreads = null; return setMode('cat'); }
    const th = x.closest('[data-th]'); if (th) return openThread(th.dataset.th, th.dataset.tt);
    const dc = x.closest('[data-doc]'); if (dc) return openDoc(dc.dataset.dtid, dc.dataset.doc, dc.dataset.dn, dc.dataset.lbl || dc.textContent.replace(/^📄\s*/, ''));
    const go = x.closest('[data-go]'); if (go) return setMode(go.dataset.go);
    const cd = x.closest('[data-card]');
    if (cd) { e.stopPropagation(); return openCard(cd.dataset.card); }
    const tw = x.closest('[data-tw]');
    if (tw) { const id = tw.dataset.tw; if (S.open.has(id)) S.open.delete(id); else S.open.add(id); return render(); }
    const row = x.closest('[data-node]');
    if (row) { S.sel = row.dataset.node; for (let n = S.tree && S.tree.get(S.sel); n; n = n.parent ? S.tree.get(n.parent) : null) S.open.add(n.id); openNode(S.sel); render(); const r = el.querySelector('.treebox .sel, .treebox [data-node="' + CSS.escape(S.sel) + '"]'); if (r && r.scrollIntoView) r.scrollIntoView({ block: 'nearest' }); return; }
    const at = x.closest('[data-at]');
    if (at) { const i = document.getElementById('inp'); const v = (i ? i.value : '').replace(/@[^\s@]*$/, ''); S.drafts[S.mode === 'node' ? 'node' : 'shared'] = (v && !/\s$/.test(v) ? v + ' ' : v) + '@' + atWord(at.dataset.at) + ' '; S.atOpen = false; render(); const j = document.getElementById('inp'); if (j) { j.focus(); j.setSelectionRange(j.value.length, j.value.length); } return; }
    const os = x.closest('[data-openseat]'); if (os && seatById(os.dataset.openseat)) return setMode(os.dataset.openseat);
    // 共同對話裡單擊在場的席 = 加 @ 它
    const lg = x.closest('.pp [data-seat]');
    if (lg && S.mode === 'shared') { const s = seatById(lg.dataset.seat); const i = document.getElementById('inp'); if (s && i) { const v = i.value; const add = '@' + s.seat_id + ' '; if (!v.includes(add)) i.value = (v && !/\s$/.test(v) ? v + ' ' : v) + add; S.drafts.shared = i.value; } return; }
    const q = x.closest('[data-quick]');
    if (q && S.mode !== 'shared') { if (S.sending) return; S.sending = true; render(); try { say(await sendSeat(S.mode, QUICK[Number(q.dataset.quick)][1])); } catch (er) { say(errText(er), 'err'); } S.sending = false; return tick(true); }
    const jm = x.closest('[data-jumpmid]'); if (jm) { const m = jm.dataset.jumpmid, q = S.posts.find((y) => y.message_id === m || (y.item && (y.item.dm_id === m || (y.item.source && y.item.source.id === m)))); if (q && S.nt) return openAt(S.nt.thread_id, q.message_id || pidOf(q), S.nt.title); return say('更新版不在這串', 'err'); }
    if (x.closest('[data-older]')) { if (S.olderBusy) return; S.olderBusy = true; render(); try { await loadOlder(); } catch (er) { say(errText(er), 'err'); } S.olderBusy = false; return render(); }
    const jd = x.closest('[data-judge]'); if (jd) { S.judgeOpen = S.judgeOpen === jd.dataset.judge ? '' : jd.dataset.judge; return render(); }
    const sf = x.closest('[data-stf]'); if (sf) { const on = stSel(), k = sf.dataset.stf; if (on[k]) delete on[k]; else on[k] = 1; return render(); }
    const dh = x.closest('[data-day]'); if (dh) { const O = (S.openDays = S.openDays || {}), k = dh.dataset.day, last = dh.dataset.last === '1'; const open = last ? O[k] !== false : O[k] === true; O[k] = !open; return render(); }
    const jp = x.closest('[data-jump]'); if (jp) { const t = document.querySelector(`.msg[data-pid="${CSS.escape(jp.dataset.jump)}"]`); if (t) { t.scrollIntoView({ block: 'center', behavior: 'smooth' }); t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash'); } else say('觸發的那一則不在這一頁', 'err'); return; }
    const wa = x.closest('[data-wkall]'); if (wa) { S.wkAll = S.wkAll || new Set(); const k = wa.dataset.wkall; if (S.wkAll.has(k)) S.wkAll.delete(k); else S.wkAll.add(k); return render(); }
    const wk = x.closest('[data-wk]'); if (wk && wk.dataset.wk) { S.wkOpen = S.wkOpen || new Set(); const k = wk.dataset.wk; if (S.wkOpen.has(k)) S.wkOpen.delete(k); else S.wkOpen.add(k); return render(); }
    const sp = x.closest('[data-stop]');
    if (sp) {
      const id = sp.dataset.stop;
      // 第一下只變「再按一次停止」,3 秒內再按才送(停掉正在做事的席,不讓誤觸)
      if (S.stopArm !== id) { S.stopArm = id; render(); setTimeout(() => { if (S.stopArm === id) { S.stopArm = null; render(); } }, 3000); return; }
      S.stopArm = null; S.sending = true; render();
      try { const r = await act('session_stop', { session_id: id }); say(r.state === 'stop_unconfirmed' && !stopLooksClean(r) ? '已送停止,但行程還沒確認結束' : r.state === 'stop_unconfirmed' ? '已停止(看得到的都停了)' : '已停止'); } catch (er) { say(errText(er), 'err'); }
      S.sending = false; return tick(true);
    }
    const an = x.closest('[data-answer]');
    if (an) {
      // 右欄卡與運作窗可能同一張卡各有一個框:讀按鈕旁邊那一個
      const card = an.dataset.answer, inp = (an.closest('.ans') || el).querySelector('[data-ansfor]');
      return answer(an.dataset.aseat, card, inp ? inp.value.trim() : '');
    }
    if (x.closest('[data-pvupd]')) { try { await act('preview_update', { idempotency_key: idem() }); } catch (er) { /* 能看階段:aibd 還沒有 */ } S.pvNew = ''; S.pvNewAt = 0; say('更新中,席與對話會保留'); return render(); }
    if (x.closest('[data-pvlist]')) { await loadPreviews(); return pvListDlg(); }
    const cbt = x.closest('[data-cinbtog]'); if (cbt) { const id = cbt.dataset.cinbtog; S.cinbOpen = S.cinbOpen || {}; S.cinbOpen[id] = !S.cinbOpen[id]; return render(); }
    const cba = x.closest('[data-cinbans]'); if (cba) { const m = cinbFind(cba.dataset.cinbans); if (!m) return; S.cinbAns = { seat: S.mode, id: m.id, from: m.from_seat, tid: m.thread_id || null, who: /^human:/.test(String(m.from_seat || '')) ? '你' : '@' + seatName(seatById(cinbId(m.from_seat)) || { seat_id: cinbId(m.from_seat) }), line: String(m.first_line || ''), demo: !!m.demo }; render(); const i = document.getElementById('inp'); if (i) i.focus(); return; }
    if (x.closest('[data-cinbx]')) { S.cinbAns = null; return render(); }
    const cbr = x.closest('[data-cinbre]'); if (cbr) { const m = cinbFind(cbr.dataset.cinbre); if (!m) return; const to = String(m.to_seat || ''), id = cinbId(to);
      // 5d33a L1:收件人是這席 ⇒ 放進這席送出框;收件人是別席 ⇒ 開一封給原收件席的信(橘條寫明),不寄回寄件席自己
      if (!/^human:/.test(to) && id && id !== S.mode) { S.cinbAns = { seat: S.mode, id: m.id, re: true, to, who: '@' + seatName(seatById(id) || { seat_id: id }), line: String(m.first_line || ''), demo: !!m.demo }; S.drafts[S.mode] = String(m.first_line || ''); render(); const i = document.getElementById('inp'); if (i) i.focus(); return; }
      S.drafts[S.mode] = String(m.first_line || ''); say('把沒送出的那封放進送出框了;看過再按「送出」寄給 @' + seatName(seatById(S.mode) || { seat_id: S.mode }), 'ok'); return render(); }
    const cbo = x.closest('[data-cinbread]'); if (cbo) { const m = cinbFind(cbo.dataset.cinbread); if (!m) return; if (m.demo) { say('示意列沒有全文', 'err'); return render(); } S.inbUi.col = inbCol(m); S.inbUi.open = null; inboxDlg(); const b = document.querySelector('[data-inbopen="' + CSS.escape(m.id) + '"]'); if (b) b.click(); return; }
    if (x.closest('[data-inbox]')) return inboxDlg();
    const eo = x.closest('[data-evopen]'); if (eo) return openEv(eo.dataset.evopen);
    if (x.closest('[data-dmopen]')) { S.dmOpen = !S.dmOpen; render(); if (S.dmOpen) loadDismissed(); return; }
    const ud = x.closest('[data-undm]');
    if (ud) {
      const card = ud.dataset.undm;
      try { await act('slot_restore', { card_id: card }); S.dismissed.delete(card); if (S.dmc) S.dmc.delete(card); if (S.dmN) S.dmN--; say('移回了'); }
      catch (er) {
        // 7ebca f3681b98c:404 slot_not_dismissed = 已經不在收起名單(還原過 / 回答過)⇒ 從清單拿掉就好
        const c = er && er.code;
        if (c === 'slot_not_dismissed') { S.dismissed.delete(card); if (S.dmc) S.dmc.delete(card); if (S.dmN) S.dmN--; say('這則已經不在收起的清單'); }
        else say(c === 'slot_built_in_page_only' ? '用戶資料夾有自己的頁時不能移回' : c === 'carried_pair_read_and_safe_only' ? '換版接回的配對不能移回,重新配對一次' : er && (er.status === 404 || (er.status === 400 && /unknown|type/.test(String(c || er.reason || '')))) ? '這版 AIB 還不能還原;重啟常駐後會自己回來' : errText(er), 'err');
      }
      return tick(true);
    }
    const hr = x.closest('[data-heat]'); if (hr) { S.heatF = S.heatF === hr.dataset.heat ? null : hr.dataset.heat; return render(); }
    if (x.closest('[data-heatx]')) { S.heatF = null; return render(); }
    const etab = x.closest('[data-evtab]'); if (etab) { EVS.tab = etab.dataset.evtab; evSave(); return render(); }
    if (x.closest('[data-evpast]')) { S.evPast = !S.evPast; return render(); }
    const ecls = x.closest('[data-evcls]'); if (ecls) { const k = ecls.dataset.evcls; if (EVS.open[k]) delete EVS.open[k]; else { EVS.open[k] = 1; EVS.page[k] = 1; } evSave(); return render(); }
    const epg = x.closest('[data-evpg]'); if (epg) { EVS.page[epg.dataset.evpg] = Math.max(1, Number(epg.dataset.to) || 1); evSave(); return render(); }
    if (x.closest('[data-evnew]')) { S.slotAsOf = ''; for (const k in EVP) delete EVP[k]; await tick(true); return; }
    // 重開接回失敗的席:act seat_restart {seat_id}(頁面憑證只能送 act);這版 aibd 沒有就說清楚
    const tka = x.closest('[data-tickask]'); if (tka) { const id = tka.dataset.tickask, t = S.ticks && S.ticks.get(id); S.tickEdit = { seat: id, every: t ? t.every_min : 20, text: t ? String(t.text || '') : '繼續', file: t && t.file ? String(t.file) : '', src: t && t.file ? 'file' : 'text', msg: '' }; render(); return; }
    if (x.closest('[data-ticksrc]') && S.tickEdit) { S.tickEdit.src = S.tickEdit.src === 'file' ? 'text' : 'file'; S.tickEdit.msg = ''; render(); return; }
    if (x.closest('[data-tickx]')) { S.tickEdit = null; render(); return; }
    if ((x.closest('[data-tickgo]') || x.closest('[data-tickclr]')) && S.tickEdit && !S.tickEdit.busy) {
      const T = S.tickEdit, clr = !!x.closest('[data-tickclr]'), every = Math.round(Number(T.every)), text = String(T.text || '').trim();
      if (!clr && !(every >= 5 && every <= 1440)) { T.msg = TICK_WHY.every_min_out_of_range; render(); return; }
      const fileName = String(T.file || '').trim(), byFile = T.src === 'file';
      if (!clr && byFile && !TICK_FILE_RE.test(fileName)) { T.msg = TICK_WHY.file_name; render(); return; }
      if (!clr && !byFile && !text) { T.msg = TICK_WHY.text_length; render(); return; }
      T.busy = true; T.msg = ''; render();
      try {
        tickTake(clr ? await act('seat_tick_clear', { seat_id: T.seat }) : await fingerAct('seat_tick_set', byFile ? { seat_id: T.seat, every_min: every, file: fileName } : { seat_id: T.seat, every_min: every, text }, tickWait(T)));
        const nm = seatShort(seatById(T.seat) || { seat_id: T.seat });
        S.tickEdit = null; say(clr ? '@' + nm + ' 不再自動送' : byFile ? '@' + nm + ' 每 ' + every + ' 分從檔案 ' + fileName + ' 送(未簽章,AIB 暫不送)' : '@' + nm + ' 每 ' + every + ' 分送「' + text.slice(0, 20) + (text.length > 20 ? '…' : '') + '」' + (S.tickPaused ? '(現在全部暫停中,恢復才送)' : ''));
      } catch (e) { T.busy = false; T.msg = (clr ? '沒停成:' : '沒設成:') + tickWhy(e); }
      render(); return;
    }
    const tkp = x.closest('[data-tickpause]');
    if (tkp && !S.sending) {
      const on = tkp.dataset.tickpause === '1';
      S.sending = true; render();
      // 停(on:true)不用指紋;恢復(on:false)要(b1efc3b571)
      try { tickTake(on ? await act('seat_tick_pause', { on }) : await fingerAct('seat_tick_pause', { on }, (t, sec) => { say(FP_SEE); })); say(on ? '自動送已全部暫停(設定都留著)' : '自動送恢復了'); }
      catch (e) { say((on ? '沒暫停成:' : '沒恢復成:') + tickWhy(e), 'err'); }
      S.sending = false; render(); return;
    }
    if (x.closest('[data-mbask]')) { S.mbAsk = x.closest('[data-mbask]').dataset.mbask; render(); return; }
    if (x.closest('[data-mbno]')) { S.mbAsk = null; render(); return; }
    const mg = x.closest('[data-mbgo]');
    if (mg && !S.sending) {
      const id = mg.dataset.mbgo, nm = seatShort(seatById(id) || { seat_id: id });
      S.sending = true; S.mbAsk = null; render();
      try { const r = await act('seat_merge_back', { seat_id: id, idem_key: idem() }); if (S.mergeStop) delete S.mergeStop[id]; say('@' + nm + ' 合回了' + (r && r.merged ? ' ' + r.merged + ' 個提交' : '') + (r && r.told ? ';已在串裡告訴主人' : '')); }
      catch (e) {
        const c = e && (e.code || (e.body && e.body.code)), b = (e && e.body) || {};
        if (e && e.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request')) { S.mbOff = true; say('這版 AIB 還不能從頁面合回,等新版', 'err'); }
        else say(c === 'merge_conflict' ? '@' + nm + ' 合回有衝突' + (Array.isArray(b.files) && b.files.length ? '(' + b.files.slice(0, 3).join('、') + (b.files.length > 3 ? ' 等 ' + b.files.length + ' 個檔' : '') + ')' : '') + ',已復原、沒動到任何檔;' + (b.card_id ? '右欄有一張卡給你決定怎麼處理' : '叫它先接上主人那一份、解完再合回') : c === 'author_busy' ? '主人' + (b.author ? ' @' + seatShort(seatById(b.author) || { seat_id: String(b.author), name: String(b.author) }) : '') + ' 正在回合中,等它這一輪做完再合回' : c === 'base_unsafe' ? '主人那一份的 git 設定有 AIB 不會跑的項目' + (b.why ? '(' + b.why + ')' : '') + ',先不能合回' : c === 'merge_left_half' ? '@' + nm + ' 合回停在一半、撤不回' + (Array.isArray(b.files) && b.files.length ? '(' + b.files.slice(0, 3).join('、') + ')' : '') + ';先別在主人那一份上動,右欄有一張卡' : c === 'tree_dirty' ? '@' + nm + ' 還有沒提交的檔,先叫它提交再合回' : c === 'base_dirty' ? '主人那一份還有沒提交的檔,先讓主人提交再合回' : c === 'nothing_to_merge' ? '@' + nm + ' 沒有要合回的提交' : c === 'seat_busy' ? '@' + nm + ' 還在寫,等它這一輪做完再合回' : actRefused(e) || '沒合回:' + errText(e), 'err');
      }
      S.sending = false; render(); tick(true); return;
    }
    if (x.closest('[data-archask]')) { S.archAsk = x.closest('[data-archask]').dataset.archask; render(); return; }
    if (x.closest('[data-archno]')) { S.archAsk = null; render(); return; }
    const ag = x.closest('[data-archgo]');
    if (ag && !S.sending) {
      const id = ag.dataset.archgo, nm = seatShort(seatById(id) || { seat_id: id });
      S.sending = true; S.archAsk = null; render();
      try { await act('seat_archive', { seat_id: id, idem_key: idem() }); say('已收掉 @' + nm); }
      catch (e) {
        const c = e && (e.code || (e.body && e.body.code));
        say(c === 'seat_busy' ? '@' + nm + ' 還在跑,先停掉再收' : c === 'archive_built_in_page_only' ? '收掉席只能在 AIB 內建的頁面做' : actRefused(e) || '沒收掉:' + errText(e), 'err');
      }
      S.sending = false; render(); tick(true); return;
    }
    const msw = x.closest('[data-msw]'); if (msw) { const sid = msw.dataset.msw, s0 = seatById(sid); S.mswap = { seat: sid, card: msw.dataset.mswcard || '', v: dfltModel(s0 && s0.provider) || '' }; render(); const i = document.querySelector('[data-mswin]'); if (i) i.focus(); return; }
    if (x.closest('[data-mswgo]')) return mswapGo();
    if (x.closest('[data-mswx]')) { S.mswap = null; return render(); }
    const rs = x.closest('[data-restart]');
    if (rs && !S.sending) {
      S.sending = true; render();
      try {
        await fingerAct('seat_restart', { seat_id: rs.dataset.restart, idem_key: idem() }); // 08de5 A11:dgrs 席 202 要指紋
        say('已重開 @' + seatName(seatById(rs.dataset.restart) || { seat_id: rs.dataset.restart }));
        const rc = rs.dataset.rcard;
        if (rc) { S.dismissed.add(rc); act('slot_dismiss', { card_id: rc }).catch(() => S.dismissed.delete(rc)); }
      }
      catch (er) {
        // a9136 00:00:拒絕的 JSON 是 {code, message, request_id};restart_refused 的原因在 message(8 個 snake_case 字)⇒ 換人話,認不得的照原字
        const restartWhy = (e) => { const r = String((e && ((e.body && e.body.message) || e.message)) || '').trim(); const W = { folder_refused: '原本的資料夾不在了,或裡面有自己的 Claude 專案設定', persona_missing: '這一席的人格卡已經不在名單上', persona_text_unreadable: '這一席的人格卡讀不到', no_conversation_id: '找不到上一段對話的編號', provider_cannot_resume: '這個品牌不能接回上一段對話', no_mode: '這一席沒有記下模式', start_failed: '開不起來(看 AIB 的紀錄)', codex_folder_settings: '資料夾裡有 Codex 自己的專案設定(.codex),Codex 席不在這種資料夾開', codex_mcp_unlisted: '讀不到這台 Codex 的工具伺服器清單,先不開', codex_mcp_unnamed: '這台 Codex 有一個工具伺服器的名字關不掉,先不開', codex_mcp_aibd_taken: '這台 Codex 已有一個叫 aibd 的工具伺服器,跟這一席自己的撞名', codex_version: '這台的 Codex 版本還沒驗過(只收 0.167 版)', stop_unconfirmed: '上一段還沒確認停乾淨', dgrs_input_off: RS.dgrs_needs_switch }; return W[r] || r || '接回條件不合'; };
        // a9136 532b54da 的回應碼
        const c = er && er.code, RS = { seat_not_found: '找不到這一席', seat_running: '這一席已經在跑了(或剛按過一次)', seat_archived: '這一席已封存,不能重開', restart_built_in_page_only: '用戶資料夾有自己的頁時不能重開席', dgrs_needs_switch: '這台還沒開「能改程式」的開關,這一席重開不了', dgrs_input_off: '這台還沒開「能改程式」的開關,這一席重開不了' };
        say(er && er.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request') ? '這版 AIB 還不能從頁面重開這一席,等新版' : c === 'restart_refused' ? '重開被擋:' + restartWhy(er) : RS[c] || errText(er), 'err');
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
    const aq = x.closest('[data-askq]'); if (aq) return askQuick(aq.dataset.acard, aq.dataset.askq);
    const ao = x.closest('[data-askopen]'); if (ao && !x.closest('dialog')) return openAt(ao.dataset.askopen, ao.dataset.amid, '');
    const pk = x.closest('[data-pick]');
    if (pk) return answer(pk.dataset.pseat, pk.dataset.pcard, pk.dataset.pick);
    const rsm = x.closest('[data-resume]');
    if (rsm && !S.sending) {
      S.sending = true; render();
      try { await act('talk_resume', { card_id: rsm.dataset.resume }); say('席與席的信恢復了;這一艙的一小時重新算'); }
      catch (e) {
        const c = e && (e.code || e.reason), why = e && (e.reason || (e.body && e.body.reason));
        if (e && e.status === 404) say('已經恢復了(別的分頁先按,或 AIB 重開過)');
        else say(why === 'slot_built_in_page_only' || c === 'slot_built_in_page_only' ? '只有內建頁能按「繼續」' : why === 'carried_pair_read_and_safe_only' || c === 'carried_pair_read_and_safe_only' ? '這個配對是更新前延用的,只能看;重新配對後再按' : e && e.status === 400 && c === 'unknown_act_type' ? '這版 AIB 還不能從頁面恢復,等新版' : '沒恢復成:' + errText(e), 'err');
      }
      S.sending = false; return tick(true);
    }
    const lt = x.closest('[data-later]');
    if (lt) { const card = lt.dataset.later; const ex = slots().find((y) => y.card === card); if (ex) (S.dmc || (S.dmc = new Map())).set(card, ex); S.dismissed.add(card); render(); try { await act('slot_dismiss', { card_id: card }); } catch (er) { S.dismissed.delete(card); say(errText(er), 'err'); } return tick(true); }
    if (x.closest('[data-add],[data-startclaude]')) {
      S.add = { provider: '', mode: 'safe', persona: '', role: '', brain: '', key: idem(), autoGo: !!x.closest('[data-startclaude]') };
      if (S.netCards > 0) S.add.mine = true; // 有連線卡等你決定 ⇒ 直接展開「我的 AI 夥伴」
      S.rg = null; S.rgMsg = null;
      guideOnPanel();
      render();
      loadRegistry();
      if (!S.pt.off) ptLoad(); // S3:每次開都重讀(別的分頁可能改過)
      hsLoad(); // S2:沒有 act 就不送
      lcLoad(); // L3:安裝 / 換版卡(舊 aibd 沒有這支 GET ⇒ 不出卡)
      S.sp = null; spLoad(); // W-10:開席設定;舊 aibd 404 ⇒ 不出卡;每次開都清掉上次的結果(f163c N1)
      { const A = S.add; setTimeout(() => { if (S.add === A) A.still = true; }, 400); } // 進場只播一次,之後點選重畫不再播
      // 0eed7 L:人格清單有介面就用(GET /api/v1/personas,形狀未定:{personas:[{uid, display_name}]} 或陣列);沒有就從現有席收
      const r = await soft(get('/api/v1/personas')).catch(() => null);
      const L = r && (Array.isArray(r.personas) ? r.personas : Array.isArray(r) ? r : null);
      if (L && S.add) { const P = L.filter((p) => p && p.uid); S.personaBuiltin = P.filter((p) => p.builtin === true).map((p) => String(p.uid)); S.personaList = P.map((p) => [String(p.uid), p.builtin === true ? pbW()[0] : String(p.display_name || p.name || p.uid)]); brainPreselect(S.add); }
      S.arch = Object.assign(S.arch || {}, { persona: rgArchOf(r) }); // A1:已封存的人格
      if (S.add) { S.add.personasIn = true; render(); } // 拿到或拿不到都算回來了,這時才預選上次的人格
      { const A = S.add; if (A && apFirstDefaults(A)) render(); // f163c N1:Claude 開不了就不自動開(不退到別家)
      if (A && A.autoGo && A.plan && A.persona && !A.busy && A.provider === 'claude') { A.autoGo = false; doAdd(); } } // ebab8 12:20:新機預設;「開始用 Claude」= 選好就直接開
      return;
    }
    if (S.add && x.closest('[data-spre]')) { spLoad(); return; }
    if (S.add && x.closest('[data-ocre]')) { ocLoad(); return; }
    if (S.add && x.closest('[data-spok]')) { spInit(); return; }
    if (S.add) {
      // S1:接回 / 最近用過 / 大分類 / 小分類 / 設定列
      const bk = x.closest('[data-apback]'); if (bk) { const A = S.add, code = bk.dataset.apback, sx = S.seats.find((y) => apCode(y) === code); A.plan = 'back:' + code; A.from = code; A.cat = 'handoff'; A.noRepoAsk = false; if (sx && sx.persona_uid) { A.persona = sx.persona_uid; A.personaTried = true; A.brainTouched = false; brainPreselect(A); } if (sx && AP_MODE[sx.mode]) A.mode = sx.mode; if (!A.ho || /HANDOFF-[0-9a-f]{5}-$/.test(A.ho)) A.ho = '~/.ez2-aib/pending/HANDOFF-' + code + '-'; A.rowOpen = 'ho'; A.key = idem(); render(); hlLoad(A, code); const i = document.querySelector('[data-addho]'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } return; }
      const rc = x.closest('[data-aprc]'); if (rc) { apPick(S.add, rc.dataset.aprc); return render(); }
      const ac = x.closest('[data-apcat]'); if (ac) { const c = ac.dataset.apcat, C = AP_CATS.find((y) => y.id === c); if (C && C.back) { S.add.backOpen = !S.add.backOpen; S.add.cat = null; S.add.reveal = S.add.backOpen; return render(); } S.add.cat = S.add.cat === c ? null : c; S.add.backOpen = false; S.add.reveal = !!S.add.cat; if (c === 'custom') apPick(S.add, 'custom:0'); return render(); }
      const sb = x.closest('[data-apsub]'); if (sb) { apPick(S.add, sb.dataset.apsub); return render(); }
      if (mgClick(x)) return;
      const cc = x.closest('[data-chchip]'); if (cc) { chSend(S.add, cc.dataset.chchip); render(); return chEnd(); }
      if (x.closest('[data-chsend]')) { const ta = document.querySelector('[data-chin]'); chSend(S.add, ta ? ta.value : S.add.chDraft); render(); return chEnd(); }
      const cd = x.closest('[data-chdo]'); if (cd) { const [i, how] = cd.dataset.chdo.split('|'); chDo(S.add, Number(i), how); render(); if (S.add && S.add.chEnd) { S.add.chEnd = false; chEnd(); } if (S.add && S.add.chReveal) { S.add.chReveal = false; const r = document.querySelector('[data-aprow=mode]'); if (r && r.scrollIntoView) r.scrollIntoView({ block: 'start', behavior: 'smooth' }); } return; }
      if (x.closest('[data-apmine]')) { S.add.mine = !S.add.mine; return render(); }
      const ppo = x.closest('[data-ppok]'); if (ppo) return ppApprove(ppo.dataset.ppok); const ppn = x.closest('[data-ppno]'); if (ppn) return ppDeny(ppn.dataset.ppno); // c2f0b 寫回人格
      const na = x.closest('[data-netans]'); if (na) { const i = na.dataset.netans.lastIndexOf('|'); return netAnswer(na.dataset.netans.slice(0, i), na.dataset.netans.slice(i + 1)); } // 3e53b 網路例外卡
      const nrv = x.closest('[data-netrv]'); if (nrv) { const p = nrv.dataset.netrv.split('|'); return netRevoke(p[0], p.slice(1, -1).join('|'), p[p.length - 1]); }
      const hso = x.closest('[data-hsoff]'); if (hso) { S.add.hsOffMsg = HS_OTHER_NO; return render(); }
      if (x.closest('[data-hstest]')) { S.add.hsOffMsg = thX().testNo; return render(); }
      const hta = x.closest('[data-hstadd]'); if (hta) return hsTestAdd(hta.dataset.hstadd);
      const ms = x.closest('[data-apms]'); if (ms) { S.add.mineSeat = S.add.mineSeat === ms.dataset.apms ? '' : ms.dataset.apms; S.pt.seat = S.add.mineSeat; S.pt.seatMsg = null; render(); if (S.pt.seat && !S.pt.list && !S.pt.off) await ptLoad(); if (S.pt.seat && hasAct('seat_perm_log') === true) await ptLogLoad(S.pt.seat); const se = S.add.mineSeat && seatById(S.add.mineSeat); if (se && se.mode === 'host' && hasAct(HS_ACTS.log) === true) await hsLogLoad(se.seat_id); return; }
      const mt = x.closest('[data-apmate]'); if (mt) { const u = mt.dataset.apmate, M = S.add.mates || (S.add.mates = []); const i = M.indexOf(u); if (i >= 0) M.splice(i, 1); else if (M.length < 7) M.push(u); S.add.msg = null; return render(); }
      const rw = x.closest('[data-aprow]'); if (rw) { S.add.rowOpen = S.add.rowOpen === rw.dataset.aprow ? null : rw.dataset.aprow; S.add.reveal = !!S.add.rowOpen; render(); if (S.add.rowOpen === 'brand') apPvRefresh(); return; }
      const pu = x.closest('[data-addpu]'); if (pu) { S.add.persona = pu.dataset.addpu; brainPreselect(S.add); return render(); }
      const bu = x.closest('[data-addbu]'); if (bu) { S.add.brain = bu.dataset.addbu; S.add.brainTouched = true; return render(); }
      if (x.closest('[data-addown]') && !TREE_HOLD) { S.add.ownTree = x.closest('[data-addown]').dataset.addown === '1'; return render(); }
      const wsb = x.closest('[data-addws]'); if (wsb) { S.add.withSeat = wsb.dataset.addws; S.add.noRepoAsk = false; return render(); }
      const rp = x.closest('[data-addrepo]'); if (rp) { S.add.repo = rp.dataset.addrepo; S.add.noRepoAsk = false; S.add.key = idem(); return render(); }
      if (x.closest('[data-addrnew]')) { S.add.repoF = { name: '', path: '' }; render(); const i = document.querySelector('[data-addrf="name"]'); if (i) i.focus(); return; }
      if (x.closest('[data-addrx]')) { if (!(S.add.repoF && S.add.repoF.busy)) S.add.repoF = null; return render(); }
      if (x.closest('[data-addrs]')) return repoReg();
      const nu = x.closest('[data-addnu]'); if (nu) { S.add.node = nu.dataset.addnu; if (S.add.node && !S.add.role && roleList().some((r) => r.uid === 'author')) { S.add.role = 'author'; S.add.roleAuto = true; } return render(); }
      const ro = x.closest('[data-addrole]'); if (ro) { S.add.role = ro.dataset.addrole; S.add.roleAuto = false; return render(); }
      const nw = x.closest('[data-addnew]'); if (nw) {
        const F = (S.add.newf = { kind: nw.dataset.addnew, name: '', what: '', key: idem() }); render(); const i = document.querySelector('[data-addnf="name"]'); if (i) i.focus();
        if (F.kind === 'brain') { const c = await soft(get('/api/v1/brains/candidates')).catch(() => null); F.root = c && c.root; F.cands = c && Array.isArray(c.candidates) && !c.demo ? c.candidates.filter((y) => y && y.id) : null; if (S.add && S.add.newf === F) render(); }
        return;
      }
      if (x.closest('[data-addnx]')) { S.add.newf = null; return render(); }
      if (x.closest('[data-addns]')) return regAdd();
      if (x.closest('[data-dmedit]')) { const pv = providers().find((y) => y.key === S.add.provider && y.open); if (pv) S.add.dm = { p: pv.id, v: dfltModel(pv.id) }; render(); const i = document.querySelector('[data-dmin]'); if (i) i.focus(); return; }
      if (x.closest('[data-dmsave]')) return dmSave(false);
      if (x.closest('[data-dmclear]')) return dmSave(true);
      if (x.closest('[data-dmx]')) { S.add.dm = null; return render(); }
      // S0-1(2e2cd 11-23,SPEC-ai-partner-open §8):點品牌不再把模式打回安全;新品牌不支援的模式由 addHtml 的 canW / canD 降回
      const ap = x.closest('[data-addp]'); if (ap) { S.add.provider = ap.dataset.addp; S.add.noRepoAsk = false; S.add.key = idem(); return render(); }
      const am = x.closest('[data-addm]'); if (am) { S.add.mode = am.dataset.addm; S.add.tpl = ''; S.add.noRepoAsk = false; S.add.key = idem(); return render(); }
      if (x.closest('[data-addx]') || x.classList.contains('dlgbg')) { S.add = null; return render(); }
      if (x.closest('[data-addpv]')) { S.add = null; render(); await loadPreviews(); return pvListDlg(); }
      if (x.closest('[data-addgo]')) return doAdd();
    }
    const sd = x.closest('[data-send]'); if (sd) return doSend(sd.dataset.send, sd.dataset.code);
    const cpy = x.closest('[data-copy]'); if (cpy) return doCopy(cpy);
    const dtg = x.closest('[data-drvtog]'); if (dtg) { DRV.open = DRV.open === dtg.dataset.drvtog ? null : dtg.dataset.drvtog; DRV.note = ''; return render(); }
    if (x.closest('[data-drvx]')) { DRV.open = null; DRV.why = ''; return render(); }
    if (x.closest('[data-drvgo]')) return driveToggle();
    const atx = x.closest('[data-attx]'); if (atx) { CT.att.splice(Number(atx.dataset.attx), 1); return render(); }
    const ctb = x.closest('[data-ct]'); if (ctb) { const k = ctb.dataset.ct; CT.note = ''; if (k === 'shot') return ctShot(); if (k === 'pick') return ctPick(); if (k === 'file') return ctFile(); if (k === 'fav') { CT.favOpen = !CT.favOpen; if (CT.favOpen) ctFavLoad(); return render(); } }
    const fvb = x.closest('[data-fav]'); if (fvb) { if (!fvb.dataset.fav && fvb.dataset.fvid) { CT.favs = (CT.favs || []).filter((f) => f.id !== fvb.dataset.fvid); act('fav_delete', { fav_id: fvb.dataset.fvid }).catch(() => {}); return render(); } return ctFavToggle(fvb.dataset.fav); }
    const fvp = x.closest('[data-fvput]'); if (fvp) { const f = favList()[Number(fvp.dataset.fvput)]; if (f) ctInsert(f.text); return; }
    const fvj = x.closest('[data-fvjump]'); if (fvj) { const f = favList()[Number(fvj.dataset.fvjump)]; const pid = f && f.ref && f.ref.message_id; const n = pid && [...document.querySelectorAll('[data-pid]')].find((m) => m.dataset.pid === pid); if (n) { S.hlPid = pid; n.scrollIntoView({ block: 'center', behavior: 'smooth' }); n.classList.add('hl'); } else say('原對話不在現在這一頁:先切到那一席或那一串'); return; }
    const hd = x.closest('[data-cthdel]'); if (hd) { e.stopPropagation(); const i = Number(hd.dataset.cthdel), h = (CT.hist || [])[i]; if (h) { CT.hist.splice(i, 1); render(); if (ctHas('send_history_delete')) ctHistDel(h); } return; }
    const hr2 = x.closest('[data-cthist]'); if (hr2) return ctHistPut(Number(hr2.dataset.cthist));
    if (x.closest('[data-cthclear]')) { CT.hist = []; CT.histOpen = false; if (ctHas('send_history_delete')) act('send_history_delete', { all: true }).catch((err) => say(errText(err), 'err')); return render(); }
    const pkc = x.closest('[data-pkcopy]'); if (pkc) { const v = pkc.dataset.pkcopy; (navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(v) : Promise.reject()).then(() => { pkc.textContent = '已複製 ' + v; setTimeout(() => { pkc.textContent = v; }, 1500); }).catch(() => ctInsert(v)); return; }
    const pkp = x.closest('[data-pkput]'); if (pkp) { ctInsert(pkp.dataset.pkput); return; }
    if (x.closest('[data-pkx]')) { CT.pick = null; return render(); }
    const e2 = x.closest('[data-enter2]'); if (e2) { try { localStorage.setItem(E2K, e2.checked ? '1' : '0'); } catch (err) { /* 存不了:這次有效 */ } E2.at = 0; return; }
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
  el.addEventListener('change', (e) => { if (e.target.dataset && e.target.dataset.addpsel && S.add) { const [k, mo] = String(e.target.value).split('|'); const p = providers().find((x) => x.key === k && x.open); if (p) { const nw = S.add.provider !== p.key; S.add.provider = p.key; S.add.model = mo && (p.models || []).some((m) => m.id === mo) ? mo : ''; S.add.noRepoAsk = false; S.add.key = idem(); if (!p.modes.includes(S.add.mode)) S.add.mode = 'safe'; render(); if (nw) apPvRefresh(); } return; }
    if (e.target.dataset && (e.target.dataset.apmgnm !== undefined || e.target.dataset.apmgst || e.target.dataset.apmgrb) && S.add && mgGrpChange(S.add, e.target)) return; // 合併每組可改(名稱 / 夥伴改用 / 改綁)
    const f = e.target.dataset && e.target.dataset.addf; if (f && S.add) { S.add[f] = e.target.value; if (f === 'role' || f === 'node') render(); } });
  el.addEventListener('paste', (e) => {
    if (e.target.id !== 'inp' || !e.clipboardData) return;
    const f = [...e.clipboardData.items].find((it) => it.kind === 'file' && /^image\//.test(it.type));
    if (f) { e.preventDefault(); annOpen(f.getAsFile(), false); }
  });
  el.addEventListener('dragover', (e) => { if (e.target.id === 'inp' && e.dataTransfer && [...e.dataTransfer.items].some((it) => it.kind === 'file')) e.preventDefault(); });
  el.addEventListener('drop', (e) => {
    if (e.target.id !== 'inp' || !e.dataTransfer) return;
    const f = [...e.dataTransfer.files].find((x) => /^image\//.test(x.type)); if (f) { e.preventDefault(); annOpen(f, false); }
  });
  el.addEventListener('input', (e) => {
    if (e.target.dataset && 'drvwhy' in e.target.dataset) { DRV.why = e.target.value; return; }
    if (e.target.dataset && 'fvq' in e.target.dataset) { CT.favQ = e.target.value; const box = e.target.closest('.fvbox'); if (box) { const tmp = document.createElement('div'); tmp.innerHTML = favBox(); const nb = tmp.firstElementChild; for (const n of [...box.querySelectorAll('.fvr,.empty')]) n.remove(); const tail = box.querySelector('.cthf'); for (const n of [...nb.querySelectorAll('.fvr,.empty')]) box.insertBefore(n, tail); } return; }
    if (e.target.dataset && 'ghnum' in e.target.dataset) { const G = (S.gh = S.gh || {}), q = e.target.value.trim(); G.num = q; for (const r of document.querySelectorAll('[data-ghpick]')) r.hidden = !!q && r.dataset.ghpick.indexOf(q) !== 0; return; } // 打字即篩(不重畫,焦點不跳)
    if (e.target.dataset && e.target.dataset.addho && S.add) { S.add.ho = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.addmp && S.add) { S.add.mp = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.dmin && S.add && S.add.dm) { S.add.dm.v = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.mswin && S.mswap) { S.mswap.v = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.relogcode && S.relog) { S.relog.v = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.addbq && S.add) { S.add.bq = e.target.value; const l = document.getElementById('addbl'); if (l) l.innerHTML = brainRows(S.add); return; }
    if (e.target.dataset && e.target.dataset.addnq && S.add) { S.add.nq = e.target.value; const l = document.getElementById('addnl'); if (l) l.innerHTML = nodeRows(S.add); return; }
    if (e.target.dataset && e.target.dataset.tickf && S.tickEdit) { S.tickEdit[e.target.dataset.tickf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.addrf && S.add && S.add.repoF) {
      // S0-6:名字欄邊打邊正規化(組字中不動,選完字的下一次輸入或送出時再換)
      if (e.target.dataset.addrf === 'name' && !e.isComposing) { const t = e.target, v = repoNameNorm(t.value); if (v !== t.value) { const a = t.selectionStart, b = t.selectionEnd; t.value = v; try { t.setSelectionRange(a, b); } catch (er) { /* 不給設就算了 */ } } }
      S.add.repoF[e.target.dataset.addrf] = e.target.value; return;
    }
    if (e.target.dataset && e.target.dataset.addnf && S.add && S.add.newf) { S.add.newf[e.target.dataset.addnf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.chin && S.add) { S.add.chDraft = e.target.value; return; } // 草稿記著,重畫不丟
    if (e.target.dataset && e.target.dataset.apmq && S.add) { S.add.mq = e.target.value; const l = document.getElementById('addml'); if (l) l.innerHTML = mateRows(S.add); return; } // 只換清單,輸入框不重畫
    if (e.target.dataset && e.target.dataset.hsf && S.hs.f) { S.hs.f[e.target.dataset.hsf] = e.target.value; return; } // S2:邊打邊記,不重畫
    if (e.target.dataset && e.target.dataset.rgf && S.rg) { S.rg[e.target.dataset.rgf] = e.target.value; if (S.rg.review) { S.rg.review = false; const d = document.querySelector('.rgdiff'); if (d) d.remove(); } return; } // S4:邊打邊記,不重畫;看過差異後又改字 ⇒ 差異作廢、要再按存檔重看(看到的 = 送出的)
    if (e.target.dataset && e.target.dataset.ptf && S.pt.f) { S.pt.f[e.target.dataset.ptf] = e.target.value; return; } // S3:模板名稱邊打邊記,不重畫(不掉焦點)
    if (e.target.dataset && e.target.dataset.addq && S.add) { S.add.pq = e.target.value; const l = document.getElementById('addpl'); if (l) l.innerHTML = personaRows(S.add); return; } // 只換清單,輸入框不重畫(不掉焦點)
    if (e.target.dataset && e.target.dataset.addf && S.add) { S.add[e.target.dataset.addf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.ansfor) { S.ansDraft[e.target.dataset.ansfor] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.movecode !== undefined) { S.moveCode = e.target.value; return; } // 重畫時不掉字
    if (e.target.id === 'sq') {
      S.q = e.target.value; clearTimeout(SR.timer);
      if (!S.q.trim()) { SR.seq++; S.sr = null; render(); return; } // 清空就回原畫面
      S.sr = null; SR.timer = setTimeout(runSearch, 300); return; // 停 300 毫秒才查
    }
    if (e.target.id !== 'inp') return;
    S.drafts[S.mode === 'shared' ? 'shared' : S.mode] = e.target.value;
    if (S.mode === 'shared' || S.mode === 'node') refreshTo(S.mode);
    if (S.mode === 'shared' || S.mode === 'node') {
      // 519d0 示範後 (3):打 @ 後接著打席名的一部分,清單照打的字過濾;點一下補成完整的
      const m = /(^|\s)@([^\s@]*)$/.exec(e.target.value.slice(0, e.target.selectionStart));
      const q = m ? m[2].toLowerCase() : '', p = document.getElementById('atpop');
      let any = false;
      if (p && m) for (const sp of p.querySelectorAll('[data-at]')) { const k = sp.dataset.at, x = seatById(k); const ok = !q || (k === 'all' ? 'all'.startsWith(q) : !!x && atKeys(x).some((y) => y.includes(q))); sp.hidden = !ok; if (ok) any = true; }
      const open = !!m && any;
      if (open !== S.atOpen) { S.atOpen = open; if (p) p.hidden = !open; }
    }
  });
  imeWire(document, render);
  el.addEventListener('keydown', (e) => {
    if (e.target.dataset && e.target.dataset.chin && S.add && e.key === 'Enter' && !e.shiftKey && !imeBusy(e)) { e.preventDefault(); chSend(S.add, e.target.value); render(); chEnd(); const t = document.querySelector('[data-chin]'); if (t) t.focus(); return; }
    // ↑ 送出紀錄:游標在第一行才叫出;開著時 ↑↓ 選、Enter 放回、Esc 關(未送草稿留著)
    if (e.target.id === 'inp' && !imeBusy(e) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const t = e.target;
      if (CT.histOpen) {
        const n = Math.min(30, (CT.hist || []).length);
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); if (n) { CT.histSel = (CT.histSel + (e.key === 'ArrowUp' ? 1 : n - 1)) % n; render(); } return; }
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); return ctHistPut(CT.histSel); }
        if (e.key === 'Escape') { e.preventDefault(); CT.histOpen = false; return render(); }
      } else if (e.key === 'ArrowUp' && !e.shiftKey && t.selectionStart === t.selectionEnd && t.value.lastIndexOf('\n', t.selectionStart - 1) < 0) {
        e.preventDefault(); ctHistLoad().then(() => { CT.histOpen = true; CT.histSel = 0; render(); }); return;
      } else if (e.key === 'Escape' && CT.draft != null) { e.preventDefault(); t.value = CT.draft; CT.draft = null; t.dispatchEvent(new Event('input', { bubbles: true })); return; }
    }
    if (e.key === 'Escape' && CT.pick) { CT.pick = null; render(); }
    if (e.key === 'Escape' && S.add && !S.add.busy) { S.add = null; render(); return; }
    if (e.target.id === 'sq' && e.key === 'Escape') { S.q = ''; SR.seq++; clearTimeout(SR.timer); S.sr = null; render(); return; }
    if (e.target.dataset && e.target.dataset.ansfor && e.key === 'Enter' && !imeBusy(e)) { e.preventDefault(); const b = (e.target.closest('.ans') || el).querySelector('[data-answer]'); if (b) b.click(); return; }
    if (e.target.matches && e.target.matches('[data-ghnum]') && !imeBusy(e)) {
      const G = (S.gh = S.gh || {});
      if (e.key === 'Enter') { e.preventDefault(); const v = e.target.value.trim(), first = document.querySelector('[data-ghpick]:not([hidden])'); G.num = /^\d+$/.test(v) && !(first && v !== first.dataset.ghpick && first.dataset.ghpick.indexOf(v) === 0 && v.length < 3) ? v : first ? first.dataset.ghpick : v; return ghAsk('pr', G.num); }
      if (e.key === 'Escape') { G.open = false; return render(); }
    }
    if (e.target.id === 'inp' && e.key === 'Enter' && !imeBusy(e)) {
      const go = () => { const b = e.target.closest('.send') && e.target.closest('.send').querySelector('[data-send]'); if (b && !b.disabled) b.click(); };
      if (e.metaKey || e.ctrlKey) { e.preventDefault(); E2.at = 0; go(); } // Cmd / Ctrl+Enter 永遠送出
      else if (!e.shiftKey && enter2On()) { // 勾了:第一下照常換行;0.5 秒內第二下 = 去掉剛才的換行再送
        const t = e.target, now = Date.now();
        if (now - E2.at <= 500 && t.selectionStart === t.selectionEnd && t.value.slice(0, t.selectionStart).endsWith('\n')) {
          e.preventDefault(); E2.at = 0; const i = t.selectionStart; t.value = t.value.slice(0, i - 1) + t.value.slice(i);
          t.dispatchEvent(new Event('input', { bubbles: true }));
          if (t.value.trim()) go();
        } else E2.at = now;
      }
    }
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
  loadReg({ key: 'talk', label: lw('ui.load.talk'), order: 40, why: '對話還沒讀回來', after: ['core'], run: async () => { if (S.mode === 'node') await loadNode(); else if (S.mode !== 'shared' && S.mode !== 'cat') await loadOut(S.mode); await loadThreads(); last = Date.now(); } }); // 工具摘要要的各席輸出不在開頁這一步等(0eed7 VERIFY L2):下一輪 tick 才讀
  loadReg({ key: 'rejoin', label: lw('ui.load.rejoin'), order: 60, need: false, after: ['core'], run: () => (S.aslots ? null : loadRejoin()) });
  loadReg({ key: 'previews', label: '預覽清單', order: 65, need: false, after: ['core'], run: () => loadPreviews().then(() => render()) });
  loadReg({ key: 'read', label: lw('ui.load.read'), order: 70, need: false, after: ['talk'], run: () => markRead() });

  // 共用進度彈窗 PROG(§19g 的樣子;項目照 loadPlan,之後開席、換版重連也用):放在 body 上就地更新,進度條平順不跳格
  const PROG = { el: null, list: [], st: {}, title: '', retry: null, done: false, t: 0 };
  const PG_CHECK = '<svg viewBox="0 0 16 16"><path d="M3.5 8.4l2.9 2.9 6.1-6.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function progStart(title, list, retry) {
    clearTimeout(PROG.t);
    if (PROG.el && PROG.title === title && !PROG.done) return;
    if (PROG.el) PROG.el.remove();
    Object.assign(PROG, { list, st: {}, title, retry: retry || null, done: false, updAt: 0, pinned: false, updAll: false, close: null });
    const P = loadPlan(list, {});
    const d = document.createElement('div'); d.className = 'pgbg'; d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true'); d.setAttribute('aria-label', title);
    d.innerHTML = `<div class="pgd"><div class="pgh"><b>${esc(title)}</b><span class="pgn" aria-live="polite"></span></div><div class="pgbar"><i></i></div><ol class="pgs">${P.rows.map((r) => `<li class="todo" data-k="${esc(r.key)}"><span class="pgdot"></span><span class="pgt">${esc(r.label)}</span><span class="pgw"></span></li>`).join('')}</ol></div>`;
    d.addEventListener('click', (e) => {
      if (e.target.closest('[data-pgretry]') && PROG.retry) return PROG.retry();
      if (e.target.closest('[data-pgmore]')) { PROG.updAll = true; PROG.pinned = true; return progUpd(); }
      if (e.target.closest('[data-pgok]')) { if (PROG.pinned) { PROG.pinned = false; if (PROG.done && PROG.close) PROG.close(); } else { PROG.pinned = true; progUpd(); } return; }
      if (e.target.closest('.pgu')) { PROG.pinned = true; progUpd(); } // 點在「這次更新」上 = 點住不收
    });
    document.body.appendChild(d); PROG.el = d; progPaint(); progUpd();
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
    const hold = PROG.updAt ? Math.max(560, PG_UPD_MIN - (Date.now() - PROG.updAt)) : 560;
    const out = () => { if (PROG.pinned) return; d.classList.add('out'); setTimeout(() => { d.remove(); if (PROG.el === d) PROG.el = null; }, 260); };
    PROG.close = () => { PROG.pinned = false; out(); };
    PROG.t = setTimeout(out, hold);
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
  // 519d0 02:03 N1:aibd 換新執行檔後開著的頁面還跑舊的 ws.js ⇒ 每 30 秒看 aibd 有沒有重開(instance_id / started_at),
  // 重開了再比這一頁的 ws.js / ws.css 跟現在送的一不一樣,不一樣才出「重新載入」(只是重開、檔沒變不吵)
  const VER = { base: null, files: null, at: 0, busy: false };
  async function fileSig() {
    const one = async (f) => { const r = await fetch(f, { cache: 'no-store', credentials: 'omit' }); if (!r.ok) return '?'; const t = await r.text(); let h = 2166136261; for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 16777619); } return t.length + ':' + (h >>> 0).toString(16); };
    return (await one('ws.js')) + '|' + (await one('ws.css'));
  }
  fileSig().then((f) => { if (!VER.files) VER.files = f; }).catch(() => {});
  document.addEventListener('click', (e) => { if (e.target && e.target.closest && e.target.closest('[data-reload]')) location.reload(); });
  async function verCheck() {
    if (VER.busy || S.newVer || Date.now() - VER.at < 30000) return;
    VER.busy = true; VER.at = Date.now();
    try {
      if (!VER.files) VER.files = await fileSig();
      const v = await soft(get('/api/v1/version')).catch(() => null); if (!v) return;
      const id = String(v.instance_id || '') + '|' + String(v.started_at || '') + '|' + String(v.version || '');
      if (VER.base === null) { VER.base = id; return; }
      if (id === VER.base) return;
      VER.base = id; S.version = v;
      if ((await fileSig()) !== VER.files) { S.newVer = true; render(); }
    } catch (e) { /* 下一輪再看 */ } finally { VER.busy = false; }
  }
  async function tick(force) {
    if (busy) { if (force) again = true; return; }
    if (!force && document.hidden) return;
    busy = true;
    try {
      if (!token()) { if (!S.pair || S.pair.view !== 'code') S.pair = { view: 'code', msg: S.pair && S.pair.msg }; return; }
      if (!S.paired) {
        let st = null;
        try { st = await get('/api/v1/pair/status'); } catch (e) { if (e && (e.status === 401 || e.status === 403)) { dropToken(); S.pair = { view: 'code', msg: '這個配對已失效,請重新輸入配對碼' }; return; } throw e; }
        if (st.state === 'awaiting_confirmation') { S.pair = { view: 'wait', short: st.short_code || '', left: st.expires_in, chan: typeof st.channel === 'string' ? st.channel : '' }; return; }
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
      if (S.hoFirst) hoFirstFlush();
      verCheck();
      loadTicks();
      if (S.boot) render();
      if (!S.aslots) await loadRejoin();
      loadWorktree();
      if (S.mode === 'node') await loadNode(); else if (S.mode !== 'shared' && S.mode !== 'cat') await loadOut(S.mode);
      if (S.mode === 'shared' || S.mode === 'cat' || force || Date.now() - last > 6000) { await loadThreads(); last = Date.now(); }
      if (S.mode === 'shared') await loadWorkOut();
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
      else if (e && e.status === 0) { S.banner = '連不上 AIB(重試中)'; S.bannerKind = 'err'; }
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
  // Andy 11-19 08:5x(7b81e):母體已配對的頁開預覽時,網址片段帶一次性的票 #pv=(43 字);這裡先把它從網址拿掉,
  // 再換成這一頁的憑證(POST /api/v1/pair/preview),不用再打碼。票用過 / 過期 / 不是預覽 ⇒ 照舊出打碼框
  async function pvPass() {
    const m = /^#pv=([A-Za-z0-9_-]{43})$/.exec(location.hash || '');
    if (!m) return;
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* 拿不掉也只是一張用過的票 */ }
    if (token()) return;
    try {
      const r = await fetch('/api/v1/pair/preview', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'pass=' + m[1], credentials: 'omit', cache: 'no-store' });
      const t = await r.text(), k = "setItem('" + KEY + "','", i = r.ok ? t.indexOf(k) : -1;
      const cred = i >= 0 ? t.slice(i + k.length).split("'")[0] : '';
      if (cred && /^[A-Za-z0-9_.~-]+$/.test(cred)) { sessionStorage.setItem(KEY, cred); return; }
    } catch (e) { /* 連不上:照舊打碼 */ }
    S.pair = Object.assign(S.pair || {}, { msg: '這張預覽的開門票用過或過期了,請照常打配對碼(或回母體再按一次「打開」)' });
  }
  pvPass().finally(() => tick(true));
  setInterval(() => {
    if (S.paired && !PUSH.started && !PUSH.off) { PUSH.started = true; pushLoop(); }
    if (S.live) return;
    if (PUSH.busy && Date.now() - lastTick < 15000) return;
    lastTick = Date.now();
    tick();
  }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(true); });
})();
