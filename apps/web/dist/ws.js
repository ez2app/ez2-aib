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
    const tip = '@' + seatName(s) + (modelText(s) ? ' · ' + modelText(s) : '') + (s.mode ? ' · ' + s.mode : '') + (seatName(s).includes(code) ? '' : ' · ' + code) + (isLegacy(s) ? ' · 舊式席(在終端機裡跑)' : '');
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
    S.hosts = hosts && Array.isArray(hosts.hosts) ? hosts.hosts : null;
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
    S.sessions = (sessions && sessions.sessions) || [];
    // 519d0 自測 r3 M3:排隊中的場次(state pending)清單不帶位置;逐個讀 GET sessions/<id> 的 queue {position, slots},記下上限
    S.qv = S.qv || {};
    for (const x of S.sessions) if (x.state === 'pending' && x.id) soft(get('/api/v1/sessions/' + encodeURIComponent(x.id))).then((r) => { const q = r && (r.queue || (r.session && r.session.queue)); if (q) { S.qv[x.id] = q; if (Number(q.slots) > 0) S.maxRun = Number(q.slots); } }).catch(() => {});
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
    const tt = (t) => (S.tree && t.node && S.tree.get(t.node) ? S.tree.get(t.node).title : '') || t.title || t.node || t.thread_id;
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
      if (!r.ok && file && r.status === 400) { bad = '這版 aibd 還不能照檔名開文件:' + file; throw 0; }
      // 7ebca dfb4e1b9d:只認現在開著的帳本卡指的那份,其他 404 doc_not_found
      if (!r.ok && file && r.status === 404) { bad = '這張卡已經不在了(處理掉或檔已移走),讀不到:' + file; throw 0; }
      if (r.ok) text = await r.text(); else { let j = {}; try { j = await r.json(); } catch (e) { /* 不是 json */ } bad = r.status === 404 ? '這份文件讀不到(可能還沒有這個介面,或檔已不在)' : r.status === 403 ? (j.code === 'doc_refused' || j.code === 'doc_kind_not_listed' ? '這份文件不能打開(不在可開的文件種類裡,或是連結檔)' : '這個配對不能讀文件' + (j.code ? '(' + j.code + ')' : '')) : '讀不到(' + r.status + ')'; }
    } catch (e) { if (!bad) bad = '連不上 aibd'; }
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
        ${right}<div class="seats">${upTag(n.id, 'sm')}${upBtn(n.id, 'sm')}${seats}</div></div>`);
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
      const who = r.auto ? '<span>aibd 自動換</span>' : r.how === 'start' ? '<span>開預覽</span>' : r.by ? (seatById(r.by) ? logo(seatById(r.by), 'sm') + ' ' : '') + '<span>' + esc(r.by) + '</span>' : '<span class="na">誰換的還沒記</span>';
      const ch = (r.changes || []).map((c) => `<li><span>${esc(c.text || '')}</span>${c.seat ? `<span class="pvby">@${esc(c.seat)}</span>` : ''}${c.card ? `<span class="ecd">${esc(c.card)}</span>` : ''}</li>`).join('');
      return `<div class="pgur"><div class="pgu1">${r.from ? `<code>${esc(String(r.from).slice(0, 9))}</code> → ` : ''}<code>${esc(String(r.to).slice(0, 9))}</code><span class="pgw2">${esc(r.at ? evTime(r.at) : '')}</span><span class="upt ${r.how === 'manual' ? 'u5' : r.how === 'start' ? 'u1' : 'u2'} sm">${r.how === 'manual' ? '手動' : r.how === 'start' ? '開啟' : '自動'}</span>${who}${r.demo ? '<span class="pvdemo" title="aibd 還沒給這次更新的紀錄;版本與改了什麼是真的,其他欄還沒有">示意</span>' : ''}</div>${r.state === 'conflict' || r.state === 'build_failed' ? `<div class="pgerr">${r.state === 'conflict' ? '合不進去' : '編不過'}${r.error ? ':' + esc(typeof r.error === 'string' ? r.error : [r.error.node, r.error.head && String(r.error.head).slice(0, 9), r.error.file, r.error.line || r.error.message].filter(Boolean).join(' · ')) : ''}</div>` : ch ? `<ul class="pguc">${ch}</ul>` : '<div class="na">這次改了什麼還沒寫</div>'}</div>`;
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
  (async () => { try { const r = await fetch('/api/v1/health', { cache: 'no-store', credentials: 'omit' }); if (r.ok) { const j = await r.json(); if (j && j.preview === true) { S.pvZone = true; if (typeof j.preview_name === 'string') S.pvName = j.preview_name.slice(0, 40); if (typeof j.preview_id === 'string') S.pvId = j.preview_id.slice(0, 64); pvSeeVersion(j); if (j.preview_update && j.preview_update.kind !== 'fix') { const U = j.preview_update; S.pvNew = String(U.version || '新版').slice(0, 40); S.pvBehind = Number(U.behind) || 0; if (U.nag_secs > 0) S.pvNagSecs = Number(U.nag_secs); S.pvNewAt = U.since ? Date.parse(U.since) || Date.now() : Date.now(); } render(); } } } catch (e) { /* 舊 aibd 沒這欄 */ } })();
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
        ${x.sample ? '<span class="pvdemo" title="清單是空的時 aibd 給的示意列,不能起">示意</span>' : ''}<label class="pvsw ${x.nostart ? 'off' : ''}" title="${x.nostart ? '示意列不能起' : x.running ? '關掉這一個預覽' : '開這一個預覽(會先彈說明)'}"><input type="checkbox" class="pvswi" data-pvsw="${esc(x.id)}" ${x.running ? 'checked' : ''} ${x.nostart ? 'disabled' : ''}><span></span></label></div>
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
          try { const r = await act('preview_open', { id: po.dataset.pvopen, idempotency_key: idem() }); if (r && typeof r.url === 'string' && /^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):\d+\//.test(r.url)) u = r.url; } catch (er) { /* 舊 aibd 沒有 preview_open:進去再打碼 */ }
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
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">預覽清單</span><span class="kid">${L.length} 個</span>${S.pvsDemo ? '<span class="pvdemo" title="清單還是空的:這兩筆是 aibd 給的示意列,不能起">示意</span>' : ''}<button type="button" class="dx" data-pvlx="1" aria-label="關閉">✕</button></div></div>
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
          if (!q || !Array.isArray(q.heads) || !q.heads_sha256) { dsay('aibd 沒給這次要合的頭清單,不能同意;先不送', 'err'); return; }
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
            else if (er.code === 'preview_repo_unset') dsay('這台 aibd 沒設預覽要用的倉庫,上不了(要維運設定)', 'err');
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
    d.innerHTML = `<div class="dw"><div class="dh"><div class="dh1"><span class="pill u-todo">上預覽</span><b class="pvdn">${esc(n.title || id)}</b><span class="kid">${esc(id)}</span>${upTagHtml(st, !!T.demo || upIsDemo(id))}${T.demo ? '<span class="pvdemo" title="aibd 還沒有相依與燈號;這是從專案樹算的替身">示意</span>' : ''}<button type="button" class="dx" data-upx="1" aria-label="關閉">✕</button></div></div>
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
    // 7ebca ceaca0552(N-ws-evcards-rest):舊帳檔案三種
    'ledger.review': '審查有退回', 'ledger.receipt': '收據紅', 'ledger.handoff': '交接單卡住',
  };
  // aibd 的卡(GET /api/v1/slots):新形狀 level / tag / text / card / since / rec / state / src / actions;
  // 舊形狀(slot / kind / item / why / at)照讀。⑤ 與「我寄的信沒送到 / 沒讀」aibd 沒有來源,頁面照算
  // a9136 派工(0eed7 VERIFY-humanpath L1):aibd 仍記 stop_unconfirmed(macOS 證不了全部子程序都走了),
  // 頁面照 ctl.rs stopped_but_unprovable 同一判斷:不再佔位、沒有活下來的、沒有讀不了的、候選 0 ⇒ 不出注意卡、寫「已停(看得到的都停了)」。
  // 7ebca 6f547e5998(第 2 件):卡槽 4 message.unclosed「N 封信收了沒結」;7ebca 03aec37f67:最多提醒兩次、兩次至少隔 3 分鐘
  function unclosedWhy(c, s) {
    const it = typeof c.item === 'string' ? c.item : '';
    // 7ebca 20:43:這張卡有三種來源(提醒滿兩次 / 一小時提醒滿 6 次 / 這一席現在不收字),卡上分不出 ⇒ 先寫 aibd 的 state,再接下一步;規則放括號當說明
    const st = typeof c.state === 'string' && c.state ? c.state + ';' : '';
    return { text: (s ? seatName(s) + ' ' : '') + (it || '有信收了沒結'), st: st + '請它結案,或寫明在等誰(能提醒的席最多提醒兩次,兩次至少隔 3 分鐘)' };
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
      case 'seat.receipt_requested': return { c: '', h: '🧾 排了收據' + h12 + (p.host ? ' · ' + p.host : '') };
      case 'seat.receipt_started': return { c: '', h: '🧾 收據開始跑' + h12 + (p.host ? ' · ' + p.host : '') };
      case 'seat.receipt_done': return p.green ? { c: 'ok', h: '🧾 收據綠' + h12 } : p.state === 'lost' ? { c: 'bad', h: '🧾 收據沒跑完:測試機失聯' + h12 } : p.state === 'done' ? { c: '', h: '🧾 收據跑完(窄,不算綠)' + h12 } : { c: 'bad', h: '🧾 收據紅' + h12 + why };
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
  const TICK_SKIP = { seat_not_found: '找不到這一席', seat_mode_takes_no_input: '這種模式收不了字', text_refused: '那句被擋下(格式不對)', not_running: '它沒在跑', gone: '它已收掉' };
  function tickRows() {
    const out = [];
    for (const x of S.tickLog || []) {
      const s = seatById(x.seat) || { seat_id: x.seat, provider: 'claude' }, t = S.ticks && S.ticks.get(x.seat);
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
  };
  const tickWhy = (e) => TICK_WHY[e && (e.code || e.reason)] || errText(e);
  const inputModes = () => { const c = S.version && S.version.capabilities; return c && Array.isArray(c.input_modes) ? c.input_modes.map(String) : ['safe', 'work']; };
  function tickTag(s) {
    if (S.tickOff || !S.ticks || !s || !inputModes().includes(String(s.mode || 'safe'))) return '';
    const t = S.ticks.get(s.seat_id);
    if (!t) return `<button type="button" class="ppo tk" data-tickask="${esc(s.seat_id)}" title="每隔幾分鐘自動送 @${esc(seatName(s))} 一句(像終端機的 /loop;只在它回合之間送)">⏱</button>`;
    return `<button type="button" class="ppo tk on${S.tickPaused ? ' pz' : ''}" data-tickask="${esc(s.seat_id)}" title="${S.tickPaused ? '已全部暫停;' : ''}每 ${esc(String(t.every_min))} 分送「${esc(String(t.text || '').slice(0, 60))}」;點這裡改或停">⏱ ${S.tickPaused ? '暫停' : esc(String(t.every_min)) + ' 分'}</button>`;
  }
  function tickBar() {
    const T = S.tickEdit, s = T && seatById(T.seat); if (!T) return '';
    const has = S.ticks && S.ticks.get(T.seat);
    return `<div class="tkbar"><b>每隔幾分鐘自動送 @${esc(s ? seatShort(s) : T.seat)} 一句</b><div class="tkf">每 <input class="tkin" type="number" min="5" max="1440" step="1" data-tickf="every" value="${esc(String(T.every))}" aria-label="間隔分鐘"> 分鐘送:<input class="tkin" type="text" maxlength="2000" data-tickf="text" value="${esc(T.text)}" aria-label="要送的那句" autocomplete="off"></div><div class="tkn">只在它回合之間送;它在忙、排隊或停著就等下一輪。第一句在設定後一個間隔才送。${S.tickPaused ? '現在全部暫停中,設定會留著、恢復才送。' : ''}</div>${T.msg ? `<div class="tkn bad">${esc(T.msg)}</div>` : ''}<div class="tkb"><button type="button" class="k" data-tickx="1">取消</button>${has ? '<button type="button" class="k" data-tickclr="1">停掉這一席的</button>' : ''}<button type="button" class="k g" data-tickgo="1" ${T.busy ? 'disabled' : ''}>${has ? '改' : '開始'}</button></div></div>`;
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
  const HO_ASK = '請照 aib handoff 寫交接單(交接、記憶整理都做完),寫好後回一句「交接單寫好了:<交接單路徑>」。寫完先不要繼續新工作。';
  function hoState(id) {
    const H = S.ho && S.ho[id]; if (!H) return null;
    const se = lastSession(id), o = se && S.out[se.id || se.session_id];
    let path = '';
    if (o) for (const it of o.items) { if (String(it.occurred_at || '') < H.at) continue; const p = it.payload || {}; if (typeof p.line !== 'string') continue; const m = /交接單寫好了[::]\s*([^\s"\\]+)/.exec(p.line); if (m) path = m[1]; }
    return { at: H.at, done: !!path, path };
  }
  function hoBox(s) {
    const st = hoState(s.seat_id);
    const clear = '<button type="button" class="k" disabled title="清空要後端:aibd 還沒有清空重開的動作(缺口已列給 519d0)">清空(要後端)</button>';
    if (!st) return '';
    return `<div class="trm" data-hostate>${st.done ? '交接單寫好了:<code>' + esc(st.path) + '</code>' : '已請它寫交接單(' + esc(hhmm(st.at)) + ');還沒看到「交接單寫好了」'} ${clear}</div>`;
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
    else if (n && n.state === 'reply') bits.push('<b class="w">等回應</b>');
    else if (n && n.state === 'decide') bits.push('<b class="w">等你決定</b>');
    else if (n && n.state === 'busy') { const st = stallOf(s.seat_id); bits.push(st && st.none ? '<b class="bad">這一輪模型沒有回應</b>' : '在做'); }
    else bits.push('在跑');
    if (n && n.item) bits.push(esc(String(n.item).slice(0, 80)) + (n.total ? ` <small>${Number(n.done) | 0}/${Number(n.total) | 0}</small>` : ''));
    const mail = (S.aslots || []).filter((c) => c && c.seat_id === s.seat_id && (c.kind === 'message.unclosed' || c.kind === 'message.unread'));
    const m = mail.map((c) => { const k = String(c.item || ''); const d = /(\d+)\s*封/.exec(k); return { n: d ? Number(d[1]) : 1, open: c.kind === 'message.unclosed' }; });
    const nOpen = m.filter((x) => x.open).reduce((a, x) => a + x.n, 0), nUnread = m.filter((x) => !x.open).reduce((a, x) => a + x.n, 0);
    if (nOpen) bits.push(`<b class="w">${nOpen} 封信沒結</b>`);
    if (nUnread) bits.push(`<b class="w">${nUnread} 封信沒讀</b>`);
    const u = S.usage && (S.usage.usage || []).find((x) => x.seat_id === s.seat_id && (!se || x.session_id === (se.id || se.session_id)));
    if (u && u.pct != null) bits.push('用量 ' + (Number(u.pct) | 0) + '%');
    const tu = trustUntil(s); if (tu) bits.push('受信任到 ' + esc(hhmm(tu * 1000)));
    return `<div class="snl" data-seatnow="${esc(s.seat_id)}">${logo(s, 'sm')}<button type="button" class="snn" data-openseat="${esc(s.seat_id)}" title="打開 @${esc(seatName(s))} 的席窗">@${esc(seatShort(s))}</button> ${bits.join(' · ')}</div>`;
  }
  // 唯讀看 PR 與檢查格(519d0 22:25 第 3 點):act gh_view {what: pr | checks | run, number}(7b81e 1dae7efafe)。
  // 那片只收受信任席自己的權杖(seat_input.rs:seat_token),頁面憑證還沒有路 ⇒ 頁面叫會被拒;收到就收起並寫缺口。aibd 每席 10 秒只准問一次 ⇒ 一次只問一樣
  const GH_WHY = {
    trusted_seats_off: '這台沒開受信任席,看 PR 的開關跟著關', gh_view_user_page: '使用者資料夾頁在服務時不能看',
    gh_view_what: '只能看 PR、檢查格、run', gh_view_number: '號碼不對', gh_view_repo: '這台 aibd 的倉設定不是 owner/name',
    gh_view_seat_gap: 'aibd 每 10 秒只准問一次,等一下再按', gh_view_machine_cap: '這台一分鐘內問太多次,等一下再按', gh_view_no_answer: 'gh 20 秒沒回',
  };
  const GH_PAGE_GAP = '頁面還不能看 PR:gh_view 只開給受信任席自己的權杖(缺:頁面也能叫的 gh_view)';
  function ghBox() {
    const G = S.gh || {};
    if (G.off) return `<div class="ghb" data-ghbox><span class="trs">${esc(G.off)}</span></div>`;
    const pr = G.pr, ck = Array.isArray(G.checks) ? G.checks : null, run = G.run;
    let h = `<div class="ghb" data-ghbox><span>看 PR</span><input class="apq" data-ghnum inputmode="numeric" maxlength="7" placeholder="號碼" value="${esc(G.num || '')}"><button type="button" class="k" data-ghgo="pr" ${G.busy ? 'disabled' : ''}>看</button>${pr ? `<button type="button" class="k" data-ghgo="checks" ${G.busy ? 'disabled' : ''}>檢查格</button>` : ''}`;
    if (G.msg) h += `<span class="trs ${G.bad ? 'bad' : ''}">${esc(G.msg)}</span>`;
    if (pr) h += `<div class="ghp" data-ghpr>#${pr.number | 0} ${esc(String(pr.title || ''))} · ${esc(String(pr.state || ''))}${pr.isDraft ? ' · 草稿' : ''} · ${esc(String(pr.baseRefName || ''))} ← ${esc(String(pr.headRefName || ''))} · 頭 ${esc(String(pr.headRefOid || '').slice(0, 12))}${pr.mergeable ? ' · ' + esc(String(pr.mergeable)) : ''}${pr.reviewDecision ? ' · ' + esc(String(pr.reviewDecision)) : ''}</div>`;
    if (ck) {
      const B = { pass: '綠', fail: '紅', pending: '跑著', skipping: '略過', cancel: '取消' };
      const cnt = {}; for (const c of ck) cnt[c.bucket] = (cnt[c.bucket] || 0) + 1;
      h += `<div class="ghc" data-ghchecks>${Object.keys(cnt).map((k) => (B[k] || k) + ' ' + cnt[k]).join(' · ')}</div>` + ck.filter((c) => c.bucket !== 'pass' && c.bucket !== 'skipping').slice(0, 20).map((c) => { const m = /\/runs\/(\d+)/.exec(String(c.link || '')); return `<div class="ghk ${c.bucket === 'fail' ? 'bad' : ''}">${esc(B[c.bucket] || c.bucket)} · ${esc(String(c.name || ''))}${m ? ` <button type="button" class="k" data-ghrun="${m[1]}" ${G.busy ? 'disabled' : ''}>看這次 run</button>` : ''}</div>`; }).join('');
    }
    if (run) h += `<div class="ghr" data-ghrun-out>run ${esc(String(G.runId || ''))} · ${esc(String(run.status || ''))}${run.conclusion ? ' · ' + esc(String(run.conclusion)) : ''} · 頭 ${esc(String(run.headSha || '').slice(0, 12))}${Array.isArray(run.jobs) ? ' · ' + run.jobs.filter((j) => j.conclusion && j.conclusion !== 'success' && j.conclusion !== 'skipped').map((j) => esc(String(j.name || '')) + ' ' + esc(String(j.conclusion))).slice(0, 8).join('、') : ''}</div>`;
    return h + '</div>';
  }
  async function ghAsk(what, number) {
    const G = (S.gh = S.gh || {});
    if (!/^\d{1,7}$/.test(String(number || ''))) { G.msg = '先填號碼'; G.bad = true; return render(); }
    G.busy = true; G.msg = ''; G.bad = false; render();
    try {
      const r = await act('gh_view', { what, number: Number(number) });
      const j = r && r.json;
      if (what === 'pr') { G.pr = j && typeof j === 'object' ? j : null; G.checks = null; G.run = null; G.msg = G.pr ? '' : 'gh 回的不是完整 JSON' + (r && r.cut ? '(太長被切了)' : ''); }
      else if (what === 'checks') G.checks = Array.isArray(j) ? j : [];
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
  function treeTag(s) {
    const L = (S.seats || []).filter((x) => !isArch(x));
    if (!L.some((x) => x.folder_owner && typeof x.folder_owner === 'object')) return '';
    if (s.repo_copy && typeof s.repo_copy === 'object') return `<small class="tt own" title="「${esc(String(s.repo_copy.repo || ''))}」的副本;做完用席窗的「送回倉」建新分支">倉副本 · ${esc(String(s.repo_copy.repo || ''))}</small>`; // a9136 IFACE-web-merge r1 §1:這種席不給合回
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
      out.push({ lv: 2, tag: '注意', t: f.at, who: '@' + seatShort(s), seat: s.seat_id, text: '@' + seatShort(s) + ' 的合回停下:作者那份 git 設定有 aibd 不跑的東西' + (f.why ? '(' + unsafeWhy(f.why) + ')' : ''), st: '沒有合、什麼都沒動。下一步:請作者那一席把這項設定拿掉(或它本來就不該在那),拿掉後再按「合回」;不想動設定就改用「同一份檔案」開審查席' + (f.why ? ' · 原文 ' + f.why : ''), key: 'mu:' + id + ':' + f.at });
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
    if (x.seat && (A.includes('handoff') || (!A.length && x.lv >= 2 && x.lv <= 4))) b.push(`<button type="button" class="k" data-handoff="${esc(x.seat)}">叫他交接</button>`);
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
    if (C && C.actMsg) rv += `<div class="pinmsg${C.actBad ? ' bad' : ''}">${esc(C.actMsg)}</div>`;
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
  function slotsHtml() {
    const G = evByCls();
    const tot = EV_CLS.reduce((a, k) => a + evCount(k, G), 0);
    const tab = EVS.tab === 'all' || EVS.tab === 'dm' || EV_CLS.includes(Number(EVS.tab)) ? EVS.tab : 'all';
    const tabs = `<div class="etabs"><button type="button" class="et1 ${tab === 'all' ? 'on' : ''}" data-evtab="all">全部 <b>${tot}</b></button>${EV_CLS.map((k) => { const n = evCount(k, G); return `<button type="button" class="et1 ${String(tab) === String(k) ? 'on' : ''} ${n ? '' : 'z'}" data-evtab="${k}">${esc(EV_NAME[k])} <b>${n}</b></button>`; }).join('')}<button type="button" class="et1 dmt ${tab === 'dm' ? 'on' : ''} ${dmCount() ? '' : 'z'}" data-evtab="dm">暫不處理 <b>${dmCount()}</b></button></div>`;
    if (tab === 'dm' && !S.dmLoaded) { S.dmLoaded = true; loadDismissed(); }
    const body = tab === 'dm' ? dmHtml(true) : tab === 'all' ? (tot ? EV_CLS.map((k) => evBox(k, G, false)).join('') : '<div class="empty">沒有事件</div>') + dmHtml() : evBox(Number(tab), G, true);
    return `<div class="evbox"><div class="bh"><b>事件</b> <span class="st">${tot} 則</span></div>${tabs}<div class="slots">${body}</div></div>`;
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
    if (!n && !S.dqDone.length && !prog && !present().length) return '';
    const open = S.dqOpen !== false;
    const shown = S.dqMore ? L : L.slice(0, DQ_SHOW);
    const more = n > DQ_SHOW ? `<button type="button" class="more" data-dqmore="1">${S.dqMore ? '收回 ▴' : '還有 ' + (n - DQ_SHOW) + ' 則 ▾'}</button>` : '';
    const done = S.dqDone.length ? `<button type="button" class="more past" data-dqdone="1">已答 ${S.dqDone.length} 則${S.dqDoneOpen ? ' ▴' : ' ▾'}</button>${S.dqDoneOpen ? `<div class="dqdl">${S.dqDone.map((y) => `<div class="dqd"><span class="et" title="${esc(y.text)}">${esc(y.text)}</span><span class="dqda">→ ${esc(y.answer)}</span><span class="ew">${esc(ago(y.at))}</span></div>`).join('')}</div>` : ''}` : '';
    const body = open ? (n ? shown.map(dqRow).join('') + more : '<div class="empty">現在沒有等你決定的</div>') + done : '';
    return `<div class="dqbox ${n ? 'has' : ''}" data-dqbox="1">${top3(L)}<button type="button" class="dqt" data-dqtoggle="1" aria-expanded="${open ? 'true' : 'false'}"><b>等你決定</b><span class="dqn">${n}</span>${open ? '▴' : '▾'}</button>${prog}${body}</div>`;
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
    const st = !res ? '<span class="wkst run">執行中</span>' : res.err ? '<span class="wkst bad">出錯</span>' : '<span class="wkst">▸</span>';
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
      if (!start) start = it.occurred_at;
    }
    const live = se.state === 'running' || se.state === 'pending';
    return { running: !!(start && live), since: start, last };
  }
  const durS = (ms) => { const t = Math.max(0, Math.round(ms / 1000)); return t >= 3600 ? Math.floor(t / 3600) + ' 小時 ' + Math.floor((t % 3600) / 60) + ' 分' : t >= 60 ? Math.floor(t / 60) + ' 分 ' + (t % 60) + ' 秒' : t + ' 秒'; };
  function turnHtml(seatId) {
    const T = turnOf(seatId); if (!T) return '';
    if (T.running) return `<span class="turn run" title="從這一回合第一筆輸出算起">這一回合在跑 · <b data-since="${esc(T.since)}">${esc(durS(Date.now() - Date.parse(T.since)))}</b></span>`;
    if (T.last) return `<span class="turn ${T.last.err ? 'bad' : ''}">上一回合 ${esc(durS(T.last.ms))}${T.last.turns ? ' · ' + T.last.turns + ' 步' : ''}</span>`;
    return '';
  }
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
    const rows = outItems(s.seat_id).concat(sharedTo(s.seat_id), actItems(s.seat_id)).sort((a, b) => String(a.t || '').localeCompare(String(b.t || '')));
    if (n && n.state === 'busy') { const stl = stallOf(s.seat_id); rows.push({ h: stl ? `<div class="ev${stl.none ? ' stall' : ''}" data-stall="${stl.none ? 'none' : 'quiet'}">${stl.none ? '' : '在想… '}${stallHtml(s.seat_id, stl)}</div>` : '<div class="ev">在想…</div>' }); }
    const stop = se && (se.state === 'running' || se.state === 'pending' || se.state === 'stop_unconfirmed') ? `<button type="button" class="stop" data-stop="${esc(se.id || se.session_id)}" ${S.sending ? 'disabled' : ''}>${S.stopArm === (se.id || se.session_id) ? '再按一次停止' : '■ 停止'}</button>` : '';
    return `<div class="sh">${logo(s, 'big', 'pick')}<span class="nm2">@${esc(seatName(s))}</span><small>${esc([modelText(s), s.mode, stTxt].filter(Boolean).join(' · '))}</small>${turnHtml(s.seat_id)}${steps}${use}${trustHtml(s)}${sendBackHtml(s)}${rcpBtn(s)}${running(s.seat_id) ? `<button type="button" class="k" data-handoff="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''} title="請這一席寫交接單;清空那一步要後端">交接並清空</button>` : ''}${stop}</div>${sendBackBox(s)}${rcpBox(s)}${hoBox(s)}${S.trustMsg && S.trustMsg.seat === s.seat_id ? `<div class="trm${S.trustMsg.bad ? ' bad' : ''}" data-trustmsg>${esc(S.trustMsg.text)}</div>` : ''}
      ${judgeRowFor(s)}${decideBox(s)}<div class="tl" id="tl">${rows.length ? rows.map((r) => r.h).join('') : '<div class="empty">這一席還沒有輸出</div>'}</div>
      <div class="rowb">${QUICK.map((q, i) => `<button type="button" class="bt" data-quick="${i}" ${running(s.seat_id) ? '' : 'disabled'}>${esc(q[0])}</button>`).join('')}</div>
      <div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="只對 @${esc(seatName(s))} 說…">${esc(S.drafts[s.seat_id] || '')}</textarea><button type="button" class="go" data-send="seat" ${S.sending ? 'disabled' : ''}>送出</button></div>
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
      ${judgeRowFor(s)}<div class="tl" id="tl">${thr}${rows.length ? rows.map((r) => r.h).join('') : thr ? '' : '<div class="empty">這一席在終端機裡跑;它的信接上鏡像(7ebca N-web-dmbridge)後會出現在這裡</div>'}</div>
      <div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="寫信給 @${esc(code)}(走舊信件帳,它在終端機收)">${esc(S.drafts[s.seat_id] || '')}</textarea><button type="button" class="go" data-send="legacy" data-code="${esc(code)}" ${S.sending ? 'disabled' : ''}>寄信</button></div>${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
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
  function runRows() {
    const out = [];
    for (const id of workSeats()) {
      const T = turnOf(id); if (!T || !T.running) continue;
      const W = workTools(id, String(T.since || ''), ''); let cur = null;
      if (W) for (const c of W.uses) if (String(c.id || '') && !W.RES.has(c.id)) cur = c;
      const s = seatById(id) || { seat_id: id, provider: 'claude' };
      const what = cur ? '正在' + DOING(cur) : '在想…';
      const stl = stallOf(id);
      const head = stl && stl.none ? stallHtml(id, stl) : `<span class="spn"></span> ${esc(what)} · <b data-since="${esc(T.since)}">${esc(durS(Date.now() - Date.parse(T.since)))}</b>${stl ? ' ' + stallHtml(id, stl) : ''}`;
      out.push({ t: '\uffff' + id, h: `<div class="msg runl${stl && stl.none ? ' stall' : ''}" data-stall="${stl ? (stl.none ? 'none' : 'quiet') : ''}"><div class="who">${logo(s, 'sm')}</div><div class="body">${head}</div><button type="button" class="wsum" data-openseat="${esc(id)}"><b>看這一席 ›</b></button></div>` });
    }
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
      return { t: String(m.created || ''), h: `<div class="msg ${mine ? 'me' : ''} ${!mine && m.unread ? 'unread' : ''}"><div class="who">${who}</div><div class="body">${body}</div>${mine ? '' : workLine(m._seat, m.created)}<div class="tm">${esc(hhmm(m.created))}</div>${seen}</div>` };
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
      return { t: String(p.created || ''), h: `<div class="msg it ${mine ? 'me' : ''}${AUTO_CLS[autoOf(p)] || ''}" data-pid="${esc(pidOf(p))}">${p._shared ? '' : `<div class="ith" data-node="${esc(p._node)}" title="打開這一項的對話">📌 ${esc(String(p._title).slice(0, 40))}</div>`}<div class="who">${who}${autoTag(p)}</div><div class="body">${body}</div>${mine || !p._shared ? '' : workLine(p.from_seat, p.created)}${docLink(p)}<div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}</div>` };
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
      <div class="tl" id="tl">${rows || '<div class="empty">還沒有對話。打 @ 選一席,寫一句送出。</div>'}</div>
      ${atpop}${askBar()}
      <div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="寫給大家;要誰接手就 @ 誰">${esc(S.drafts.shared || '')}</textarea><button type="button" class="go" data-send="shared" ${S.sending ? 'disabled' : ''}>送出</button></div>${toLine('shared')}
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
    const rep = it.dm_reply === true || (it.source && it.source.reply === true) ? '<span class="imt rp" title="舊式席從終端機回的信">回信</span>' : '';
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
    return `<span class="autot" title="這一席回合結束時,aibd 用它的身份把最後回答貼回這一串">自動帶回</span>${r}`;
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
      return `<div class="msg ${mine ? 'me' : ''}${im ? ' imp' : ''}${AUTO_CLS[autoOf(p)] || ''}${S.hlPid && S.hlPid === pidOf(p) ? ' hl' : ''}" data-pid="${esc(pidOf(p))}"><div class="who">${im && /seat:import$/.test(String(p.from_seat || p.from_key || '')) ? '<span class="lg sm imp" title="寄件人不明(匯入)">匯</span>' : who}${im}${autoTag(p)}</div><div class="body">${body}</div>${docLink(p)}${im ? dmFoot(p) : ''}<div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}${rb ? `<div class="seen rb"><span class="st">讀過</span>${rb}</div>` : ''}</div>`;
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
      ${askBar()}<div class="send"><textarea class="inp" id="inp" rows="2" maxlength="32768" placeholder="對這一項說;@ 誰 = 叫他">${esc(S.drafts.node || '')}</textarea><button type="button" class="go" data-send="node" ${S.sending ? 'disabled' : ''}>送出</button></div>${toLine('node')}
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
  const STATE_TEXT = { owner_lost: '負責席已不在(關了或換艙),等統籌改派', owner_off_roster: '負責席不在名冊上,等統籌改派', no_owner: '找不到負責席', no_free_reviewer: '沒有空的審查席', seat_size_none: '負責席這輪額度不夠,派不出去', seat_busy: '負責席或審查席在忙,派不出去', busy: '忙碌中', seat_idle: '閒著', idle: '閒著', queued: '排隊中', seat_queued: '排隊中', pending: '準備中', running: '在跑', seat_running: '在跑', stopped: '已停', seat_stopped: '已停', stop_unconfirmed: '停了、還沒確認結束', failed: '開不起來', seat_failed: '開不起來', reply: '等回應', waiting: '等回應', seat_waiting: '等回應', decide: '等你決定', quiet: '說忙、沒動', seat_quiet: '說忙、沒動', done: '做完了', rejoin_failed: '接回失敗', offline: '連不上', two_ways: '兩個做法', usage_high: '用量偏高', login_needed: '要登入', none: '不用等人,席自己能做', needs_human: '要真人出手', delivered: '已送達', partial: '部分完成', clear: '沒事', exited: '已正常結束', killed: '被強制結束', interrupted: 'aibd 重啟時被中斷', cancelled: '被取消', issued: '碼已發、還沒兌換', awaiting_confirmation: '等短碼確認', confirmed: '已確認', revoked: '已撤銷', expired: '已過期', conflict: '合不進去', build_failed: '編不過', ready: '可用', observed_clean: '看得到的都停了', lineage_unavailable: '追不到子程序', root_unrecorded: '沒記到最上層程序', candidates_incomplete: '可疑程序沒查完', candidates_unreadable: '有可疑程序讀不到', candidates_unreadable_older: '讀不到的都比這席更早開', root_identity_lost: '最上層程序身份對不上', stop_failed: '停的動作失敗', unclassified: '其他原因' };
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
    if (c === 'needs_fingerprint') return '請在這台按指紋;按完再點一次';
    if (c === 'owner_check_unavailable') return '要先按指紋確認是你本人,這版 aibd 還沒接上指紋;這張卡留著,接好再按';
    if (e && e.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request')) return '這版 aibd 還不能按,等新版';
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
  let askTitle0 = null, askHeard = null;
  function askNotice() {
    if (askTitle0 === null) askTitle0 = document.title;
    const L = pendingAsks(), n = L.length;
    const t = (n ? '(' + n + ') 等你決定 · ' : '') + askTitle0;
    if (document.title !== t) document.title = t;
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
      if (r && r.verified === false) { C.actMsg = '頁面按過、未證實(' + (answer === 'agree' ? '同意' : '不同意') + ');要按指紋確認是你本人才算,這版 aibd 還沒接上指紋,卡留著'; }
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
      C.actMsg = (unv ? '頁面按過、未證實:' : '看過:') + n + ' 條' + (h ? '(清單雜湊 ' + h + ')' : '') + (unv ? ';要按指紋確認是你本人才算,這版 aibd 還沒接上指紋' : '');
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
      if (e && e.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request')) { C.pin = node || ''; C.pinDemo = true; C.pinMsg = '這版 aibd 還不能掛,先標在頁面上'; }
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
  // 頁面寫卡(7b81e IFACE-web-card-write r0,N-web-oneline 第 2 項):act card_note / card_set;頁面能換的站只有這四個,
  // 審過 / 實查要收據與審查、定版要指紋 ⇒ 灰掉。aibd 沒有這兩種動作時照實說「沒寫進去」,標示意
  const PAGE_ST = ['未開始', '已定未做', '開發中', '作廢'];
  function cardWriteHtml(n) {
    const W = S.card.w || {};
    const opts = ['', ...STATIONS, '作廢'].map((x) => { const ok = !x || PAGE_ST.includes(x); return `<option value="${esc(x)}" ${ok ? '' : 'disabled'} ${W.st === x ? 'selected' : ''}>${x ? esc(x) + (ok ? '' : '(要收據 / 審查,頁面不能換)') : '不換站'}</option>`; }).join('');
    const res = W.res ? `<div class="cwr ${W.res.ok ? 'ok' : 'bad'}">${esc(W.res.text)}${W.res.demo ? '<span class="pvdemo" title="aibd 還沒有 card_note / card_set">示意</span>' : ''}</div>` : '';
    return `<div class="dsec"><span>在這裡記一筆</span><span class="na">寫的人記成「頁面」</span></div><div class="cw"><textarea class="inp cwt" rows="2" maxlength="2000" placeholder="記一件事實(例:Andy 看過,要改 X)" data-cwt="1">${esc(W.text || '')}</textarea><div class="cwb"><select class="cws" data-cws="1" aria-label="換站">${opts}</select><button type="button" class="k g" data-cwgo="1" ${W.busy ? 'disabled' : ''}>${W.st ? '換站並記下' : '記下'}</button></div>${res}</div>`;
  }
  // 0eed7 REVIEW-integ2-ui L1(N-webonly-c5):aibd 還沒有 card_note / card_set ⇒ 正式頁不畫這一區;預覽才畫(試按)。
  // 按過一次回「沒有這種動作」就收起來(結果那一句留到關視窗);寫成功過一次之後正式頁也畫
  const cardWriteShown = () => (S.cardWriteOk || inPreview()) && (!S.cardWriteOff || !!(S.card && ((S.card.w && S.card.w.res) || (S.card.add && S.card.add.res))));
  // 開卡(519d0 22:25 第 1 點):act card_add {parent, title, kind, seat?, why?} 照 aib-plan add;aibd 還沒有(card_note / card_set / card_add 都沒有,列缺口)⇒ 同寫卡一樣只在預覽畫、回沒有就收起
  function cardAddHtml(n) {
    const A = S.card.add || {};
    const res = A.res ? `<div class="cwr ${A.res.ok ? 'ok' : 'bad'}">${esc(A.res.text)}${A.res.demo ? '<span class="pvdemo" title="aibd 還沒有 card_add">示意</span>' : ''}</div>` : '';
    if (!A.open) return `<div class="cwb"><button type="button" class="k" data-cardadd="1" title="在「${esc(n.title || S.card.id)}」底下開一張新卡">在這底下開卡</button></div>${res}`;
    return `<div class="dsec"><span>開卡</span><span class="na">放在 ${esc(S.card.id)} 底下</span></div><div class="cw" data-cardaddform><input class="inp" data-caf="title" maxlength="120" placeholder="標題(必填)" value="${esc(A.title || '')}"><div class="cwb"><select class="cws" data-caf="kind" aria-label="種類">${[['item', '項目'], ['task', '工作'], ['group', '一組']].map(([v, t]) => `<option value="${v}" ${(A.kind || 'item') === v ? 'selected' : ''}>${t}</option>`).join('')}</select><input class="inp" data-caf="seat" maxlength="12" placeholder="負責席(可空)" value="${esc(A.seat || '')}"><input class="inp" data-caf="why" maxlength="400" placeholder="說明(可空)" value="${esc(A.why || '')}"><button type="button" class="k g" data-cardaddgo="1" ${A.busy ? 'disabled' : ''}>開</button><button type="button" class="k" data-cardaddno="1">不要</button></div>${res}</div>`;
  }
  async function cardAdd() {
    const c = S.card; if (!c || !c.id) return; const A = (c.add = c.add || {});
    for (const e of document.querySelectorAll('[data-caf]')) A[e.dataset.caf] = e.value.trim();
    if (!A.title) { A.res = { ok: false, text: '先寫標題' }; return renderCard(); }
    A.busy = true; renderCard();
    try {
      const r = await act('card_add', Object.assign({ parent: c.id, title: A.title, kind: A.kind || 'item', idempotency_key: idem() }, A.seat ? { seat: A.seat } : {}, A.why ? { why: A.why } : {}));
      c.add = { open: false, res: { ok: true, text: '開了' + (r && r.id ? ' ' + r.id : '') } }; S.cardWriteOk = true; openCard(c.id, null, true);
    } catch (er) {
      const code = er && er.code;
      if (er && (er.status === 404 || code === 'unknown_act_type' || code === 'unknown_act' || code === 'unknown_type')) { A.res = { ok: false, text: 'aibd 還沒有開卡的動作,這張沒有開;這一區先收起來', demo: true }; S.cardWriteOff = true; }
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
    if (!text && !W.st) { W.res = { ok: false, text: '先寫一句,或選要換到哪一站' }; return renderCard(); }
    if (W.st && !PAGE_ST.includes(W.st)) { W.res = { ok: false, text: W.st + ' 要同頭收據與審查,頁面不能換' }; return renderCard(); }
    W.busy = true; renderCard();
    try {
      const r = W.st ? await act('card_set', { id: c.id, status: W.st, fact: text, idempotency_key: idem() }) : await act('card_note', { id: c.id, fact: text, idempotency_key: idem() });
      W.res = { ok: true, text: r && r.from && r.to ? (r.from === r.to ? '記下了' : r.from + ' → ' + r.to + ',記下了') : '記下了' };
      W.text = ''; W.st = ''; S.cardWriteOk = true; S.cardWriteOff = false; openCard(c.id, null, true);
    } catch (er) {
      const code = er && er.code;
      if (er && (er.status === 404 || code === 'unknown_act_type' || code === 'unknown_act' || code === 'unknown_type')) { W.res = { ok: false, text: 'aibd 還沒有寫卡的動作,這筆沒有寫進去;這一區先收起來', demo: true }; S.cardWriteOff = true; }
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
        const ac = e.target.closest('.evact [data-pick], .evact [data-answer], .evact [data-handoff], .evact [data-restart], .evact [data-go], .evact [data-later]');
        if (ac) { if (ac.matches('[data-go], [data-later], [data-pick], [data-answer]')) { S.card = null; dlg.close(); } return onMainClick(e); }
      });
      dlg.addEventListener('input', (e) => {
        if (e.target.dataset && e.target.dataset.ansfor) S.ansDraft[e.target.dataset.ansfor] = e.target.value;
        if (e.target.dataset && e.target.dataset.pinq && S.card) { S.card.pinQ = e.target.value; const l = dlg.querySelector('.pinl'); if (l) l.innerHTML = pinList(S.card.pinQ); } // 只換清單,輸入框不重畫
      });
      dlg.addEventListener('keydown', (e) => {
        if (e.target.dataset && e.target.dataset.ansfor && e.key === 'Enter' && !e.isComposing) { e.preventDefault(); const b = e.target.closest('.ans') && e.target.closest('.ans').querySelector('[data-answer]'); if (b) b.click(); }
      });
      // close 事件是非同步的:關了又馬上開別張時,不要把新開的那張清掉
      dlg.addEventListener('close', () => { if (dlg.open) return; S.card = null; dlgHtml = ''; });
    }
    const ev = S.card.ev;
    const evSec = ev ? `<div class="dsec">這則事件</div><div class="evd"><div class="evh"><span class="ek ${TAG_K[ev.tag] || 'k3'}">${esc(ev.tag || LV_TAG[ev.lv] || '')}</span><b>${esc(ev.text)}</b></div><div class="fg">${fld('什麼時候', ev.t ? when(ev.t) + ' · ' + DISP_TZL + ' ' + evTime(ev.t) : '')}${ev.seat ? fldH('哪一席', logo(seatById(ev.seat) || { seat_id: ev.seat, name: ev.seat }, 'sm') + ' ' + esc(ev.who || '')) : ev.who ? fld('誰', ev.who) : ''}${ev.kind === 'andy.review_list' ? '' : ev.kind === 'idea.consent' ? fld('做法', ev.rec) + fld('好處', ev.why) + fld('代價', ev.st) : fld('我們的建議', ev.rec) + (ev.st ? fldH('狀態', stateHtml(ev.st)) : fld('狀態', ''))}${fld('出處', ev.src)}${ev.ref ? fld('卡號', ev.ref) : ''}</div><div class="evact">${evBody(ev, true)}</div></div>` : '';
    if (S.card.only) {
      const html = `<div class="dw"><div class="dh u-todo"><div class="dh1"><span class="pill u-todo">${esc(ev ? ev.tag || LV_TAG[ev.lv] || '事件' : '事件')}</span><b class="evst" title="${esc(ev ? ev.text : '')}">${esc(shortTitle(ev ? ev.text : ''))}</b><button type="button" class="x" data-cclose="1" aria-label="關閉" autofocus>✕</button></div>
          <div class="evpin"><span class="na">${S.card.pin ? (S.card.pinDemo ? '已選:' : '已掛到:') : '未對應專案項目'}</span>${S.card.pin ? `<a data-card="${esc(S.card.pin)}">${esc((S.tree && S.tree.get(S.card.pin) || {}).title || S.card.pin)}</a>${S.card.pinDemo ? '<span class="pvdemo" title="這版 aibd 還不能掛,只標在頁面上">示意</span>' : ''}` : ''}<button type="button" class="k" data-pinopen="1">${S.card.pin ? '換一項' : '掛到項目'}</button>${S.card.pin && !S.card.pinDemo ? '<button type="button" class="k" data-pinoff="1">拿掉</button>' : ''}${S.card.pinMsg ? `<span class="pinmsg ${S.card.pinBad ? 'bad' : ''}">${esc(S.card.pinMsg)}</span>` : ''}</div>
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
    const tabs = ['all'].concat(Object.keys(REC)).map((k) => `<button type="button" data-ctab="${k}" class="${S.card.tab === k ? 'on' : ''}" ${k !== 'all' && !cnt[k] ? 'disabled' : ''}>${k === 'all' ? '全部' : REC[k]} <i>${k === 'all' ? recs.length : cnt[k] || 0}</i></button>`).join('');
    const shownAll = recs.filter((r) => S.card.tab === 'all' || (r.ks || [r.k]).includes(S.card.tab));
    const REC_MAX = 6, shown = S.card.recAll ? shownAll : shownAll.slice(0, REC_MAX);
    const recMore = shownAll.length > REC_MAX ? `<button type="button" class="more" data-recall="1">${S.card.recAll ? '收起 ▴' : '再看 ' + (shownAll.length - REC_MAX) + ' 筆 ▾'}</button>` : '';
    const stepMs = shown.length ? Math.min(520, Math.round(7000 / shown.length)) : 0;
    const tl = shown.length ? shown.map((r, i) => `<div class="te ${i === 0 ? 'new' : ''}" data-sty="--d:${(shown.length - 1 - i) * stepMs}ms;"><time>${esc(r.at || '沒有寫時間')}${(r.ks || [r.k]).filter((k) => k && k !== 'dev').map((k) => `<b class="kt kt-${k}">${esc(REC[k])}</b>`).join('')}</time><p>${r.by ? seatLogos(r.by) + ' ' : ''}${esc(r.t)}</p></div>`).join('') : '<div class="none">還沒有紀錄</div>';
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
  const ADD_BRANDS = [['claude', 'Claude', 'bc'], ['codex', 'Codex', 'bx'], ['agy', 'Antigravity', 'bg'], ['grok', 'Grok', 'bk']];
  const BRAND_ALIAS = { claude: 'claude', codex: 'codex', agy: 'agy', antigravity: 'agy', gemini: 'agy', grok: 'grok' };
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
      const R = { not_built: '這版還不能開', not_set_up: '這台沒設定', not_installed: '這台沒裝', not_logged_in: '這台還沒登入' };
      // Andy 11-20 00:2x 親打「先把 codex 選項灰掉」:codex 席叫不出 aibd 工具、話進不了串(0eed7 VERIFY-realentry ea0885a7 H1)⇒ aibd 修好前頁面不讓開
      const held = HOLD_BRANDS[k] || null;
      const why = (held && held.block) || (!p ? '這版還不能開' : p.open ? (held && held.note) || '' : R[p.reason] || '這台還不能開');
      const modes = ((p && p.modes) || ['safe']).filter((m) => !held || !held.modes || held.modes.includes(m));
      return { id: p ? p.id : k, key: k, name, b, open: !(held && held.block) && !!(p && p.open), modes: modes.length ? modes : ['safe'], why };
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
  function nodeRows(A) {
    const k = String(A.nq || '').trim().toLowerCase();
    const L = nodeOpts().filter(([id, t]) => !k || t.toLowerCase().includes(k) || id.toLowerCase().includes(k)).slice(0, 8);
    return L.length ? L.map(([id, t]) => `<button type="button" class="apr ${A.node === id ? 'on' : ''}" data-addnu="${esc(id)}" title="${esc(t)}"><b>${esc(t)}</b><i>${esc(id)}</i></button>`).join('') : '<div class="apn">找不到</div>';
  }
  function personaList(q) {
    const PS = personas(); const k = String(q || '').trim().toLowerCase();
    return (k ? PS.filter(([u, nm]) => nm.toLowerCase().includes(k) || u.toLowerCase().includes(k)) : PS).slice(0, 8);
  }
  function personaRows(A) {
    const L = personaList(A.pq);
    return L.length ? L.map(([u, nm]) => `<button type="button" class="apr ${A.persona === u ? 'on' : ''}" data-addpu="${esc(u)}"><b>${esc(nm)}</b><i>${esc(u.slice(-6))}</i></button>`).join('') : '<div class="apn">找不到</div>';
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
      return none + (CS.length ? `<div class="apn">下面是這台找到的資料夾,還沒登記成第二大腦;登記要在這台按指紋,這台還不能按。先不綁也能開席。</div>` + CS.map((c) => `<div class="apr off" title="還沒登記"><b>${esc(c.name)}</b><i>${c.notes != null ? esc(c.notes) + ' 篇' : ''}</i></div>`).join('') : `<div class="apn">${S.reg && (S.reg.brainRoot === 'unset' || S.reg.brainRoot === 'unusable') ? esc(brainRootWhy(S.reg.brainRoot)) + ';' : '這台還沒有登記好的第二大腦;'}先不綁也能開席。</div>`);
    }
    return none + (L.length ? L.map((b) => `<button type="button" class="apr ${A.brain === b.uid ? 'on' : ''}" data-addbu="${esc(b.uid)}" title="${esc(b.what || '')}"><b>${esc(b.name || b.uid)}</b><i>${b.notes != null ? esc(b.notes) + ' 篇 · ' : ''}${esc(String(b.uid).slice(-6))}</i></button>`).join('') : (S.reg && S.reg.brains ? '<div class="apn">找不到</div>' : '<div class="apn">讀取中…</div>'));
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
    if (A && S.add === A) { brainPreselect(A); render(); }
  }
  // 預設帶人格建議的那一個(suggested_for);用戶自己點過就不動
  function brainPreselect(A) {
    if (A.brainTouched) return;
    const b = ((S.reg && S.reg.brains) || []).find((x) => x.sug && x.sug.includes(A.persona));
    A.brain = b ? b.uid : '';
  }
  // + 新增(三張表共用一個小表單):name + 一句說明;aibd 沒這個 act ⇒ 標示意、不假裝建好
  const REG_KIND = { role: ['角色', 'role_add', 20, 80], persona: ['人格', 'persona_add', 40, 500], brain: ['第二大腦', 'brain_add', 40, 500] };
  function brainRootWhy(root) {
    if (root === 'unset') return '還沒設定第二大腦位置。設定方法:在這台的 ~/.ez2-aib/vault-dir 寫上資料夾的完整路徑(或啟動 aibd 時帶 AIBD_BRAIN_ROOT),再重開這個視窗';
    if (root === 'unusable') return '第二大腦位置用不了,請檢查設定(要是完整路徑、是資料夾、讀得到裡面的筆記)';
    return '這個位置裡還沒有找到可用的資料夾';
  }
  function regForm(kind) {
    const A = S.add, F = A.newf;
    if (!F || F.kind !== kind) return `<button type="button" class="anew" data-addnew="${kind}">+ 新增${REG_KIND[kind][0]}</button>`;
    const fp = kind === 'role' ? '用戶新增的角色不帶任何權限' : '建立要在這台按一次指紋';
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
      F.msg = e && (e.code === 'unknown_act_type' || e.reason === 'unknown_act_type' || e.code === 'not_built') ? '這版 aibd 還不能新增(示意)'
        : rs === 'owner_check_unavailable' ? '要本人指紋,這台還不能按'
        : e && e.status === 403 ? '只有內建頁、這一輪配對的分頁能新增' : '沒建成:' + errText(e);
    }
    render();
  }
  const ICO_SAFE = '<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 4.6-3 8.3-7 10-4-1.7-7-5.4-7-10V6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  const ICO_WORK = '<svg viewBox="0 0 24 24"><path d="M3 7h7l2 2h9v10H3z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  const ICO_DGRS = '<svg viewBox="0 0 24 24"><path d="M14.5 5.5a4 4 0 0 0 4.9 4.9l-8.6 8.6a2.1 2.1 0 0 1-3-3l8.6-8.6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  // 7b81e repos.rs(N-web-worktree,ORDER-webonly 第 1 項):要本人指紋的動作,第一次不帶 ticket ⇒ aibd 回 202 {ticket}、
  // 這台跳指紋視窗;之後每 3 秒帶同一張 ticket 重送同一份內容,過了回 200、還沒按回 403 owner_check_not_approved;
  // 2 分鐘沒過就停(沒按或按了拒絕)。aibd 沒接指紋 ⇒ 403 owner_check_unavailable,直接丟給呼叫的人。
  // 7b81e IFACE-owner-touch r2 補(T1,5d33a 通過):把一席升成受信任 12 小時 / 收回;只給 safe / work 席。
  // 升要按這台的指紋(流程同登記倉,fingerAct);收回不用。受信任到幾點:席的 JSON 還沒有,先記這頁拿到的回應(trusted_until 是 unix 秒);
  // 席的 JSON 之後有 trusted_until 就以它為準。這台沒開(trusted_seats_off)或這頁不能做(page_only)⇒ 這頁不再顯示鈕
  // c46:指紋窗可能沒到最前面 ⇒ 每個倒數都提示;c45:票過期後視窗可能還在、還能按 ⇒ 明講這張已過期、請按取消
  const FINGER_BEHIND = '視窗可能在其他視窗後面';
  const FINGER_GONE = '這張已過期,視窗若還在請按取消';
  // 第 4 件「在網頁跑測試拿收據」(7ebca 4758a4702b 21:43,蓋過 b4297 IFACE r0):receipt_request / receipt_read / receipt_withdraw。
  // 頭碼:席的 JSON 還沒有;先讀 tree.head(已請 7ebca 補),沒有就鈕灰掉並說原因。沒有背景輪詢 ⇒ 這頁在跑的每 15 秒 read 一次
  const RCP_WHY = {
    bad_head: '頭碼不對(要 40 碼)', bad_scope: '只能選窄或整套', bad_tests: '窄收據要 1 到 8 項「crate:測試檔」(例 aibd:seats_tree)', seat_id_invalid: '席號不對',
    receipt_not_allowed: '這台沒開網頁跑收據,或這一席不是受信任席', head_not_seat_head: '這個頭碼不是這一席現在的頭(它可能又提交了;重新整理再按)',
    receipt_built_in_page_only: '只有這台內建的頁面能跑收據', receipt_in_flight: '這一席已經有收據在跑,等它跑完或撤掉再排',
    no_host: '現在沒有可用的測試機,沒有排', already_done: '這份已經跑完,不能撤', no_such_run: '找不到這份收據', bad_run_id: '收據號不對',
  };
  // 7ebca 62153be32a(21:52):多 asking(正在問測試機,10 分沒下文變 lost)與 lost(測試機失聯);receipt_list {seat_id} 給這一席的列(在途全給、結束的最近 10 份)
  const RCP_STATE = { asking: '正在問測試機', waiting_owner: '等主人同意', running: '在跑', done: '跑完', red: '紅', withdrawn: '已撤', lost: '測試機失聯' };
  const RCP_REASON = { lost: '測試機失聯(問了 10 分鐘沒下文,或跑到一半斷了)', owner_refused: '主人沒同意', seal_missing: '收據沒有封條', 'rc_nonzero:restored': '刀跑完復原的那一步失敗' };
  const rcpReason = (w) => RCP_REASON[w] || w;
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
    return `<button type="button" class="k" data-rcpask="${esc(s.seat_id)}" ${h && !S.sending ? '' : 'disabled'} title="${esc(h ? '在測試機上跑這一席現在的頭 ' + h.slice(0, 12) + '(窄 = 指定幾個測試檔;整套 = 全部 + 刀)' : 'aibd 還沒給這一席的頭碼,還不能從頁面跑收據')}">跑收據</button>`;
  }
  function rcpBox(s) {
    if (!S.rcpOff && !S.rcpListed[s.seat_id]) { S.rcpListed[s.seat_id] = true; setTimeout(() => rcpList(s.seat_id), 0); }
    const A = S.rcpAsk && S.rcpAsk.seat === s.seat_id ? S.rcpAsk : null, L = S.rcp[s.seat_id] || [], M = S.rcpMsg && S.rcpMsg.seat === s.seat_id ? S.rcpMsg : null;
    let h = '';
    if (A) h += `<div class="rcpq" data-rcpform><span>跑收據 · 頭 ${esc(headOf(s).slice(0, 12))}</span><label><input type="radio" name="rcpsc" data-rcpscope="narrow" ${A.scope === 'narrow' ? 'checked' : ''}> 窄(指定測試檔,不算綠)</label><label><input type="radio" name="rcpsc" data-rcpscope="full" ${A.scope === 'full' ? 'checked' : ''}> 整套(全部測試 + 刀)</label>${A.scope === 'narrow' ? `<input class="apq" data-rcptests value="${esc(A.tests || '')}" placeholder="aibd:seats_tree, aib-drive:andy_open(最多 8 項)" aria-label="要跑的測試檔">` : ''}<button type="button" class="k g" data-rcpgo="${esc(s.seat_id)}">排</button><button type="button" class="k" data-rcpno="1">不要</button></div>`;
    if (M) h += `<div class="trm${M.bad ? ' bad' : ''}" data-rcpmsg>${esc(M.text)}</div>`;
    for (const x of L.slice(0, 3)) {
      const t = x.tests_total || {}, k = x.knives || {}, why = Array.isArray(x.reasons) ? x.reasons : [];
      const badge = x.state === 'done' && x.green ? '<b class="rcg">綠</b>' : x.state === 'red' || (x.state === 'done' && !x.green && !x.narrow) ? '<b class="rcr">紅</b>' : '';
      h += `<div class="rcpr" data-rcprun="${esc(x.run_id)}">${badge}收據 ${x.narrow || x.scope === 'narrow' ? '窄' : '整套'} · 頭 ${esc(String(x.head || '').slice(0, 12))} · ${esc(RCP_STATE[x.state] || String(x.state || ''))}${x.host ? ' · ' + esc(x.host) : ''}${t.suites != null ? ` · 測試 ${t.passed | 0} 過 / ${t.failed | 0} 失敗${t.ignored ? ' / ' + (t.ignored | 0) + ' 略過' : ''}` : ''}${k.expected != null ? ` · 刀 ${k.bites | 0}/${k.expected | 0}${k.misses ? '(' + (k.misses | 0) + ' 沒咬)' : ''}` : ''}${why.length ? ` · <span class="rcw" title="${esc(why.join(' '))}">${why.map((w) => esc(rcpReason(String(w)))).join('、')}</span>` : ''}${rcpLive(x) ? `<button type="button" class="k" data-rcpread="${esc(x.run_id)}">更新</button><button type="button" class="k" data-rcpwd="${esc(x.run_id)}">撤</button>` : ''}</div>`;
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
  const rcpWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return RCP_WHY[rs] || errText(e); };
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
    seat_busy: '這一席還在跑,先停下它再送', nothing_to_send: '副本裡沒有倉裡沒有的提交,沒東西可送(只送已提交的)',
    settings_touched: '動到 AI 設定檔、workflow、.gitmodules、.lfsconfig 或符號連結,不能從頁面送', objects_missing: '副本的物件不齊,沒有送',
    too_big: '副本太大,超過上限,沒有送', disk_low: '倉那邊的磁碟不夠,沒有送', repo_unsafe: '登記倉自己的設定指到程式,aibd 不在那裡跑 git',
    send_failed: 'git 建分支失敗(或 20 個名字都被佔了),倉沒有改', head_moved: '按指紋之後副本的頭變了,什麼都沒寫;請再按一次送回倉',
    seat_id: '這一席不是登記倉的副本,不能送回倉', send_back_built_in_page_only: '只有這台內建的頁面能送回倉',
    owner_check_unavailable: '這台還不能按指紋,沒有送', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有送',
    finger_denied: '指紋取消了,沒有送', finger_expired: FINGER_GONE + ';沒有送,要的話再按一次', finger_timeout: '等了 2 分鐘沒按指紋,' + FINGER_GONE + ';沒有送',
  };
  const SB_SAFE = '只在倉裡建一條新分支,不會推、不會改你正在用的分支。';
  function sendBackHtml(s) {
    const rc = s.repo_copy && typeof s.repo_copy === 'object' ? s.repo_copy : null;
    if (!rc) return '';
    if (S.sb && S.sb.seat === s.seat_id) return '';
    return `<button type="button" class="k" data-sendback="${esc(s.seat_id)}" title="${esc((rc.sendable === false ? '這一席看起來還在跑,先停下再送;' : '') + '把這一席副本裡已提交的改動,送回「' + String(rc.repo || '') + '」建一條新分支。' + SB_SAFE)}" ${S.sending || S.sb ? 'disabled' : ''}>送回倉(建新分支)</button>`;
  }
  function sendBackBox(s) {
    const B = S.sb && S.sb.seat === s.seat_id ? S.sb : null, M = S.sbMsg && S.sbMsg.seat === s.seat_id ? S.sbMsg : null;
    if (B) {
      const p = B.plan || {}, L = Array.isArray(p.listed) ? p.listed : [];
      const what = p.repo ? `送回「${esc(p.repo)}」,新分支 <b>${esc(p.branch || '')}</b>${p.head ? ' · 頭 ' + esc(String(p.head).slice(0, 12)) : ''}${p.commits != null ? ' · ' + (Number(p.commits) | 0) + ' 個提交' : ''}${p.files != null ? ' · ' + (Number(p.files) | 0) + ' 個檔' : ''}` : '正在問 aibd 要送什麼…';
      return `<div class="sbw" data-sendbackwait>${what}${L.length ? `<div class="sbl">要讓你看到的檔:${L.slice(0, 20).map((x) => '<code>' + esc(String(x)) + '</code>').join(' ')}</div>` : ''}<div>${SB_SAFE}只送已提交的。</div>${B.left != null ? `<div class="sbf">請在這台 Mac 按指紋 · 還有 ${B.left | 0} 秒 · ${FINGER_BEHIND}</div>` : ''}</div>`;
    }
    if (M) return `<div class="trm${M.bad ? ' bad' : ''}" data-sendbackmsg>${esc(M.text)}${M.files && M.files.length ? '<div class="sbl">' + M.files.map((x) => '<code>' + esc(String(x)) + '</code>').join(' ') + '</div>' : ''}</div>${M.bad ? '' : pushBox(s)}`;
    return '';
  }
  async function sendBack(id) {
    S.sbMsg = null; S.sb = { seat: id, plan: null, left: null }; render();
    try {
      const r = await fingerAct('seat_send_back', { seat_id: id }, (t, sec, first) => { if (S.sb && S.sb.seat === id) { S.sb.plan = (first && first.plan) || S.sb.plan; S.sb.left = sec; render(); } }, (first) => ({ head: String((first && first.plan && first.plan.head) || '') }));
      S.sbOk = { seat: id, repo: String((r && r.repo) || ''), branch: String((r && r.branch) || ''), head: String((r && r.head) || '') }; S.pushMsg = null; S.prAsk = null;
      S.sbMsg = { seat: id, text: '已在「' + String((r && r.repo) || '') + '」建新分支 ' + String((r && r.branch) || '') + (r && r.head ? '(頭 ' + String(r.head).slice(0, 12) + (r.commits != null ? ',' + (Number(r.commits) | 0) + ' 個提交' : '') + (r.files != null ? '、' + (Number(r.files) | 0) + ' 個檔' : '') + ')' : '') + '。不會推、不會改你正在用的分支。' };
    } catch (e) {
      const rs = e && (e.reason || (e.body && e.body.reason) || e.code), c = e && e.code;
      const k = SB_WHY[c] ? c : rs;
      const b = (e && e.body) || {};
      S.sbMsg = { seat: id, bad: true, text: (SB_WHY[k] || errText(e)) + (k === 'repo_unsafe' && b.why ? ':' + String(b.why) : '') + (e && e.status === 409 ? '。沒有建分支;暫存的物件 git 之後會自己清' : ''), files: k === 'settings_touched' && Array.isArray(b.files) ? b.files.slice(0, 20) : null };
    }
    S.sb = null; render();
  }
  // 第 5 件殼(519d0 22:19):送回倉成功那列多「推上去」(G2)與「開 PR」(G3)。
  // 推上去照 7b81e IFACE-owner-touch r3 補(d6bea879a3):repo_push {repo, branch}(+ 重送 ticket),頭碼與遠端 aibd 自己讀;202 不帶 plan ⇒ 用送回倉那次的分支與頭;200 {repo, branch, sha, remote_host}。
  // 開 PR 照 r4 補(7b81e 4de2bce5d2,519d0 22:30 內建頁叫 + 每次指紋):pr_open {repo, branch, title, body}(+ 同一份原字重送 ticket);200 {number, url, draft: true, base: main};
  // 現在每台都回 503 pr_open_unavailable(還沒有 GitHub 權杖,問指紋之前)⇒ 不當錯誤,鈕停用寫原因。aibd 回 unknown_act_type / repo_push_off / page_only ⇒ 收起那顆鈕、寫原因。只推 web/<碼>-<MMDD>-<n>
  const PUSH_WHY = {
    unknown_act_type: '推上去要等後端(7b81e 的 repo_push 還沒進這台的 aibd)', branch_not_web: '只能推 web/<碼>-<MMDD>-<n> 這種分支',
    repo_push_off: '這台沒開從頁面推上去', page_only: '只有這台內建的頁面能推', push_fields: '頁面送的欄位不對(請回報頁面工程席)', push_branch: '只能推 web/<碼>-<MMDD>-<n> 這種分支',
    push_repo_unknown: '找不到這個登記倉', push_branch_unknown: '倉裡沒有這條分支', push_no_remote: '這個倉沒有設定遠端', push_repo_unsafe: '登記倉自己的設定指到程式,aibd 不在那裡跑 git',
    push_changed: '按指紋之後分支或遠端變了,請再按一次', push_touches_github: '這條分支改到 .github/,不能從頁面推', push_leak: '分支裡有看起來像金鑰或主機資訊的內容,沒有推', push_leak_unavailable: '這台沒辦法檢查有沒有洩漏,沒有推',
    push_not_fast_forward: '遠端那條分支跟這裡分岔了,不會強推', push_base_unknown: '抓不到遠端 main(多半沒網路或認證不過)', push_failed: '遠端拒絕或連不上,沒有推', push_no_answer: '120 秒沒推完,當作沒推',
    owner_check_unavailable: '這台還不能按指紋,沒有推', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有推',
    finger_denied: '指紋取消了,沒有推', finger_expired: FINGER_GONE + ';沒有推,要的話再按一次', finger_timeout: '等了 2 分鐘沒按指紋,' + FINGER_GONE + ';沒有推',
  };
  const PR_WHY = {
    unknown_act_type: '開 PR 要等後端(7b81e 的 pr_open 還沒進這台的 aibd)', pr_open_unavailable: '這台還沒有開 PR 的權杖', pr_open_off: '這台沒開從頁面開 PR', page_only: '只有這台內建的頁面能開 PR',
    pr_fields: '頁面送的欄位不對(請回報頁面工程席)', pr_title: '標題要一行、1 到 200 字', pr_body: '說明太長(最多 16 KB)', pr_no_github_repo: '這個倉的遠端不是 GitHub',
    pr_changed: '按指紋之後分支或遠端變了,請再按一次', pr_exists: '這條分支已經有 PR 了', pr_failed: 'GitHub 拒絕或連不上,沒有開',
    owner_check_unavailable: '這台還不能按指紋,沒有開', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有開',
    finger_denied: '指紋取消了,沒有開', finger_expired: FINGER_GONE + ';沒有開,要的話再按一次', finger_timeout: '等了 2 分鐘沒按指紋,' + FINGER_GONE + ';沒有開',
    push_branch: '只能從 web/<碼>-<MMDD>-<n> 這種分支開', push_repo_unknown: '找不到這個登記倉', push_branch_unknown: '倉裡沒有這條分支', push_no_remote: '這個倉沒有設定遠端', push_repo_unsafe: '登記倉自己的設定指到程式,aibd 不在那裡跑 git', branch_not_web: '只能從 web/ 開頭的分支開 PR', not_pushed: '這條分支還沒推上去,先按「推上去」' };
  function pushBox(s) {
    const O = S.sbOk && S.sbOk.seat === s.seat_id ? S.sbOk : null;
    if (!O || !/^web\/[A-Za-z0-9]+-\d{4}-\d+$/.test(O.branch)) return '';
    const W = S.pushF && S.pushF.seat === s.seat_id ? S.pushF : null, A = S.prAsk && S.prAsk.seat === s.seat_id ? S.prAsk : null, M = S.pushMsg && S.pushMsg.seat === s.seat_id ? S.pushMsg : null;
    let h = '<div class="pbx" data-pushbox>';
    if (W) h += `<div class="sbw" data-pushwait>要推「${esc(O.repo)}」的 <b>${esc(O.branch)}</b> · 頭 ${esc(O.head.slice(0, 12))} 到遠端(只推 web/ 開頭,不動其他分支)<div class="sbf">請在這台 Mac 按指紋 · 還有 ${W.left | 0} 秒 · ${FINGER_BEHIND}</div></div>`;
    else if (S.prF && S.prF.seat === s.seat_id) h += `<div class="sbw" data-prwait>開 PR(草稿):基底 <b>main</b> ← <b>${esc(O.branch)}</b> · 「${esc(S.prF.title)}」<div class="sbf">請在這台 Mac 按指紋 · 還有 ${S.prF.left | 0} 秒 · ${FINGER_BEHIND}</div></div>`;
    else if (A) h += `<div class="rcpq" data-prform>開 PR(草稿):<input class="apq" data-prtitle value="${esc(A.title)}" maxlength="200" aria-label="PR 標題"><textarea class="apq" data-prbody rows="2" maxlength="16000" placeholder="說明(可空)" aria-label="PR 說明">${esc(A.body || '')}</textarea> 基底 <b>main</b> ← <b>${esc(O.branch)}</b><button type="button" class="k g" data-prgo="${esc(s.seat_id)}">開</button><button type="button" class="k" data-prno="1">不要</button></div>`;
    else h += (S.pushOff ? `<span class="trs">${esc(S.pushOff)}</span>` : `<button type="button" class="k" data-push="${esc(s.seat_id)}" title="把 ${esc(O.branch)} 推到遠端;要按這台的指紋">推上去</button>`) + (S.prOff ? `<span class="trs">${esc(S.prOff)}</span>` : `<button type="button" class="k" data-pr="${esc(s.seat_id)}" title="從 ${esc(O.branch)} 對 main 開 PR">開 PR</button>`);
    if (M) h += `<div class="trm${M.bad ? ' bad' : ''}" data-pushmsg>${esc(M.text)}${M.url ? ' <a href="' + esc(M.url) + '" target="_blank" rel="noopener">' + esc(M.url) + '</a>' : ''}</div>`;
    return h + '</div>';
  }
  const pushWhy = (T, e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return T[rs] || errText(e); };
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
      if (rs === 'unknown_act_type' || rs === 'repo_push_off' || rs === 'page_only') S.pushOff = PUSH_WHY[rs];
      else S.pushMsg = { seat: id, bad: true, text: pushWhy(PUSH_WHY, e) };
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
      if (rs === 'unknown_act_type' || rs === 'pr_open_unavailable' || rs === 'pr_open_off' || rs === 'page_only') S.prOff = PR_WHY[rs]; else S.pushMsg = { seat: id, bad: true, text: pushWhy(PR_WHY, e) };
    }
    S.prF = null; S.sending = false; render();
  }
  const TRUST_WHY = {
    trusted_seats_off: '這台沒開受信任席(bridge.json 的 trusted_seats 要開)', unknown_act_type: '這台的 aibd 還沒有受信任席(aibd 7b81e feat/trust-t1 才有 seat_trust / seat_untrust)', page_only: '只有這台內建的頁面能升受信任',
    seat_not_found: '找不到這一席', seat_archived: '這一席已封存', seat_trust_mode: '只有安全席、做事席能升受信任',
    owner_check_unavailable: '這台問不到指紋,沒有升', owner_check_not_approved: '指紋沒按過(取消、逾時或已用過),沒有升',
    finger_denied: '指紋取消了,沒有升', finger_expired: FINGER_GONE + ';沒有升,要的話再按一次', finger_timeout: '等了 2 分鐘沒按指紋,' + FINGER_GONE + ';沒有升',
    seat_trust_changed: '按指紋之後這一席的模式、倉的遠端或測試機名單變了,請重按一次', seat_id: '席號不對', trust_store: 'aibd 寫不進受信任紀錄,請稍後再試',
  };
  const trustUntil = (s) => { const v = Number(s.trusted_until != null ? s.trusted_until : S.trust[s.seat_id]); return v > Date.now() / 1000 ? v : 0; };
  function trustHtml(s) {
    if (S.trustOff || !['safe', 'work'].includes(String(s.mode || 'safe'))) return '';
    const F = S.trustF && S.trustF.seat === s.seat_id ? S.trustF : null;
    if (F) return `<span class="trs" data-trustwait>請在這台 Mac 按指紋 · 還有 ${F.left | 0} 秒 · ${FINGER_BEHIND}</span>`;
    const u = trustUntil(s);
    if (u) return `<span class="trs on">受信任到 ${hhmm(u * 1000)}</span><button type="button" class="k" data-untrust="${esc(s.seat_id)}" ${S.sending ? 'disabled' : ''}>收回</button>`;
    return `<button type="button" class="k" data-trust="${esc(s.seat_id)}" title="按這台的指紋,把這一席升成受信任 12 小時" ${S.sending || S.trustF ? 'disabled' : ''}>升成受信任</button>`;
  }
  const trustWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return TRUST_WHY[rs] || errText(e); };
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
  async function fingerAct(type, body, wait, extra) {
    let r = await act(type, body);
    if (!(r && r.__status === 202 && r.ticket)) return r;
    const first = r, more = extra ? extra(first) : {};
    const ticket = String(r.ticket), until = Date.now() + 120000;
    // 7b81e IFACE-owner-touch r0(1a93b 11-20):每 1 秒問一次、最長 120 秒;每一輪把剩幾秒交給畫面倒數;unavailable 直接停
    const left = () => Math.max(0, Math.ceil((until - Date.now()) / 1000));
    if (wait) wait(ticket, left(), first);
    // 7b81e 06:2x:G2 會有 GET /api/v1/owner/tickets/{ticket} ⇒ {state: pending|approved|denied|expired};有就先問狀態、過了才帶單重送一次(分得出拒絕);沒有(404)⇒ 照舊帶單重送
    let probe = true;
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 1000));
      if (wait) wait(ticket, left(), first);
      if (probe) {
        const t = await soft(get('/api/v1/owner/tickets/' + encodeURIComponent(ticket))).catch(() => null);
        const st = t && String(t.state || '');
        if (!t || !st) probe = false;
        else if (st === 'unavailable') { const why = String(t.reason || 'owner_check_unavailable'); throw Object.assign(new Error(why), { code: 'owner_check_unavailable', reason: REPO_WHY[why] ? why : 'owner_check_unavailable' }); }
        else if (st === 'denied' || st === 'expired') throw Object.assign(new Error('finger_' + st), { code: 'finger_' + st, reason: 'finger_' + st });
        else if (st !== 'approved') { if (Date.now() > until) throw Object.assign(new Error('finger_timeout'), { code: 'finger_timeout', reason: 'finger_timeout' }); continue; }
      }
      try { r = await act(type, Object.assign({}, body, more, { ticket })); if (!(r && r.__status === 202)) return r; }
      catch (e) { const rs = e && (e.reason || (e.body && e.body.reason)); if (rs !== 'owner_check_not_approved') throw e; }
      if (Date.now() > until) throw Object.assign(new Error('finger_timeout'), { code: 'finger_timeout', reason: 'finger_timeout' });
    }
  }
  const REPO_WHY = {
    name: '名字只能 1–40 個小寫英文、數字、減號(例 ez2-aib)',
    path: '路徑要是這台 Mac 上一個資料夾的完整路徑(以 / 開頭)',
    path_inside_aibd: '這個資料夾在 aibd 自己的資料夾裡,不能登記',
    not_a_repo: '這個資料夾不是 git 倉',
    not_repo_top: '要填倉的最上層資料夾(有 .git 的那一層),不是裡面的子資料夾',
    settings_symlink: '倉裡的專案設定檔有捷徑(連結),aibd 不收;拿掉再登記',
    settings_not_file: '倉裡的專案設定不是一般檔案,aibd 不收',
    settings_too_many: '倉裡的專案設定檔太多,aibd 不收',
    settings_too_deep: '倉裡的專案設定資料夾太深,aibd 不收',
    project_settings_changed: '這個倉的專案設定在登記後改過;要重新登記(會再按一次指紋)',
    repos_unwritable: 'aibd 寫不進倉的名單',
    repos_unreadable: 'aibd 讀不到倉的名單',
    owner_check_unavailable: '要本人指紋,這台的 aibd 還沒接上指紋;先不能登記',
    owner_check_not_approved: '指紋沒過(沒按、按了拒絕,或那張單已用過)',
    finger_timeout: '2 分鐘內沒看到指紋按過(沒按或按了拒絕);' + FINGER_GONE + ',要的話再按一次',
    finger_denied: '指紋視窗按了拒絕;要的話再按一次',
    finger_expired: FINGER_GONE + ';再按一次會開新的一張',
    // 7b81e IFACE-owner-touch r0:第一步就被擋的三種
    pair_touch_busy: '已經有一個指紋視窗開著;先回那一個再按',
    pair_touch_cooling: '剛按了不同意,冷卻中;稍等一下再按',
    pair_touch_limited: '一小時內問指紋太多次;晚點再試',
    preview_no_dgrs: '預覽區不開「能改程式」的席,倉只能在正式那顆 aibd 用',
    settings_unreadable: '讀不到倉裡的專案設定,aibd 不收',
    hooks: 'aibd 準備不了自己的 git 設定資料夾;重開 aibd 再試',
    repo_copies_full: '這個倉同時開的份數已到上限(最多 9 份);先收掉一席再開',
    disk_low: '這台磁碟剩不到 2 GiB,不再多開一份倉;先清出空間',
    base: '「從哪一顆開始」要填完整的 40 碼提交(小寫英數),或留空',
    base_not_in_repo: '這個倉裡沒有那一顆提交(或沒有分支走得到它);檢查提交碼',
    tree_not_opened: '在這個倉開不出這一席自己的一份(分支);看倉是不是在改到一半',
  };
  const repoWhy = (e) => { const rs = e && (e.reason || (e.body && e.body.reason) || e.code); return REPO_WHY[rs] || (rs === 'repo' ? '這個倉不能這樣開:' + errText(e) : errText(e)); };
  // 519d0 06:2x 判:做事席也能帶登記的倉(沙盒照舊、一律開自己的一份),7b81e 做;aibd 在 GET version 給 repo_modes 才開給做事席,沒給 = 只有能改程式席
  const repoModes = () => (S.version && Array.isArray(S.version.repo_modes) ? S.version.repo_modes.map(String) : ['dgrs']);
  const curRepo = (A) => { const R = S.version && Array.isArray(S.version.repos) ? S.version.repos.map((x) => String((x && x.name) || x || '')) : []; return A.repo && R.includes(A.repo) && repoModes().includes(A.mode) ? A.repo : ''; };
  // 能改程式的席開在哪:aibd 的 GET version 有 repos(名字清單)才出這段;沒有 = 舊 aibd,照舊系統新開資料夾
  function repoSec(A) {
    const R = S.version && Array.isArray(S.version.repos) ? S.version.repos.map((x) => String((x && x.name) || x || '')).filter(Boolean) : null;
    if (!R) return '';
    const F = A.repoF;
    const form = F ? `<div class="anf"><input class="apq" data-addrf="name" value="${esc(F.name || '')}" maxlength="40" placeholder="倉的名字(例 ez2-aib)" aria-label="倉的名字" autocomplete="off"><input class="apq" data-addrf="path" value="${esc(F.path || '')}" placeholder="這台 Mac 上倉的最上層資料夾,例 /Users/你/ez2-aib" aria-label="倉的資料夾" autocomplete="off" spellcheck="false"><div class="apn">${F.ticket ? '請在這台 Mac 跳出的指紋視窗按下(視窗寫:AIB 網頁要求:repo.register|對象:…|單號 ' + esc(F.ticket.slice(-6)) + ')' + (F.left != null ? ' · 還有 ' + F.left + ' 秒 · ' + FINGER_BEHIND : '') : 'aibd 會先讀這個倉(最上層、遠端、專案設定),再請你在這台按一次指紋;按過才記下來。之後開席只用名字,不再填路徑。'}</div>${F.msg ? `<div class="apn bad">${esc(F.msg)}</div>` : ''}<div class="anfb"><button type="button" class="k" data-addrx="1">取消</button><button type="button" class="go ${F.busy ? 'busy' : ''}" data-addrs="1" ${F.busy ? 'disabled' : ''}>${F.busy ? (F.ticket ? '等指紋…' : '讀倉中…') : '登記'}</button></div></div>` : '';
    const cur = A.repo && R.includes(A.repo) ? A.repo : '';
    return `<div class="asec"><span>在哪裡改</span><div class="apn">選登記過的倉 ⇒ 這一席在那個倉的自己一份(自己的分支)上改,${A.mode === 'work' ? '開的時候不用按指紋(做事席照舊在沙盒裡)' : '開之前要按一次指紋'};不選 = 系統新開一個空資料夾</div><div class="apl"><button type="button" class="apr ${cur ? '' : 'on'}" data-addrepo=""><b>系統新開的資料夾</b></button>${R.map((n) => `<button type="button" class="apr ${cur === n ? 'on' : ''}" data-addrepo="${esc(n)}"><b>${esc(n)}</b><i>倉</i></button>`).join('')}${F ? '' : '<button type="button" class="apr" data-addrnew="1"><b>+ 登記一個倉</b></button>'}</div>${cur ? `<div class="anf"><input class="apq" data-addf="base" value="${esc(A.base || '')}" maxlength="40" placeholder="從哪一顆開始(選填,40 碼提交;空白 = 倉現在的頭)" aria-label="從哪一顆提交開始" autocomplete="off" spellcheck="false"></div>` : ''}${form}</div>`;
  }
  async function repoReg() {
    const A = S.add, F = A && A.repoF; if (!F || F.busy) return;
    const name = String(F.name || '').trim(), path = String(F.path || '').trim();
    if (!/^[a-z0-9-]{1,40}$/.test(name)) { F.msg = REPO_WHY.name; return render(); }
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
    // 519d0 15:45 改定:頁面不給路徑也不給名稱;共用資料夾 = 7ebca caf84f494 的 with_seat(第二席開在那一席自己的 seat-work 資料夾)
    const WS = S.seats.filter((x) => x && x.mode === 'work' && !isArch(x) && (S.sessions || []).some((se) => se.seat_id === x.seat_id && (se.state === 'running' || se.state === 'pending')));
    // 只有資料夾主人能被 with_seat(aibd shared_folder:cwd 要是那一席自己的 seat-work);借別席開的列出來但灰掉、寫借誰的
    // a9136 654162420:席 JSON 帶 folder_owner {seat, how: folder | tree},有就以它為準;舊 aibd 才讀 seat.opened 事件
    const ownerOf = (x) => { const fo = x.folder_owner, c = fo && typeof fo === 'object' && fo.seat ? String(fo.seat) : x.folder_owner === null ? '' : S.seatGuest[x.seat_id]; return c ? S.seats.find((y) => y.seat_id === c || String(y.seat_id).replace(/^seat_/, '').startsWith(c)) || { seat_id: c, name: c } : null; };
    const WSO = WS.filter((x) => !ownerOf(x));
    if (A.withSeat && !WSO.some((x) => x.seat_id === A.withSeat)) A.withSeat = '';
    // 519d0 ORDER-autodrive 清單 3:預設 Claude + 做事;已有開著的做事席就預設跟它同一個資料夾(第二席通常是審查)。只在開視窗第一次套,之後照使用者點的
    if (!A.dflt && pv) { A.dflt = true; if (canW) { A.mode = 'work'; if (WSO.length) A.withSeat = WSO[0].seat_id; } } // 預設最早開的資料夾主人(通常是作者),不是最新那席
    const PS = personas();
    // 519d0 自測 L1:不再預設清單第一個(跟這件事無關的人格);用上次開席選的,不在清單就不選
    if (!A.persona && !A.personaTried) { A.personaTried = true; let last = ''; try { last = localStorage.getItem('ws.lastPersona') || ''; } catch (e) { /* 沒有就不選 */ } if (last && PS.some(([u]) => u === last)) { A.persona = last; brainPreselect(A); } }
    const pn = (PS.find(([u]) => u === A.persona) || [A.persona, A.persona])[1];
    const NO = nodeOpts(); const node = A.node !== undefined ? A.node : S.sel || '';
    const nodeT = (S.tree && node && S.tree.get(node)) ? S.tree.get(node).title || node : '';
    const brand = PV.map((p) => `<button type="button" class="abr ${pv && p.key === pv.key ? 'on' : ''} ${p.open ? '' : 'off'}" data-addp="${esc(p.key)}" ${p.open ? '' : 'disabled aria-disabled="true"'}><span class="lg lgb ${p.b}">${ICON[p.b]}</span><b>${esc(p.name)}</b>${p.why ? `<i>${esc(p.why)}</i>` : ''}</button>`).join('');
    const mode = `<button type="button" class="amd ${A.mode !== 'dgrs' && A.mode !== 'work' ? 'on' : ''}" data-addm="safe">${ICO_SAFE}<b>安全</b><i>只聊天、叫別席</i></button><button type="button" class="amd ${A.mode === 'work' ? 'on' : ''} ${canW ? '' : 'off'}" data-addm="work" ${canW ? '' : 'disabled'}>${ICO_WORK}<b>做事</b><i>${canW ? '限資料夾、名單外拒絕' : winHost ? 'Windows 上還沒有沙盒,先不能開' : pv && PV.some((p) => p.open && p.modes.includes('work')) ? esc(pv.name) + ' 還不能開做事席' : '這版 aibd 還不能開'}</i></button>${inPreview() ? '' : `<button type="button" class="amd warn ${A.mode === 'dgrs' ? 'on' : ''} ${canD ? '' : 'off'}" data-addm="dgrs" ${canD ? '' : 'disabled'}>${ICO_DGRS}<b>能改程式</b>${canD ? '' : '<i>這個品牌在這台只能開安全模式</i>'}</button>`}`; // 519d0 自測 L2:預覽一定拒 ⇒ 不出
    const pers = PS.length ? `<div class="aps"><input class="apq" data-addq="1" value="${esc(A.pq || '')}" placeholder="找人格" aria-label="找人格" autocomplete="off"><div class="apl" id="addpl">${personaRows(A)}</div></div>` : `<input class="apq" data-addf="persona" value="${esc(A.persona || '')}" placeholder="人格 uid" aria-label="人格">`;
    const RL = roleList();
    const role = `<div class="arl" role="radiogroup" aria-label="角色"><button type="button" class="arc ${!A.role ? 'on' : ''}" data-addrole="" role="radio" aria-checked="${!A.role}"><b>不掛</b><i>只開席,不掛到項目</i></button>${RL.map((r) => `<button type="button" class="arc ${A.role === r.uid ? 'on' : ''} ${r.builtin ? '' : 'usr'}" data-addrole="${esc(r.uid)}" role="radio" aria-checked="${A.role === r.uid}" title="${esc(r.uid)}"><b>${esc(r.name)}</b><i>${esc(r.what || '')}</i></button>`).join('')}</div>${S.reg && S.reg.rolesLive === false ? '<div class="ademo">角色清單:這版 aibd 還沒給,先用內建六種</div>' : ''}${regForm('role')}`;
    // Andy 11-19 11:3x「最下方有一個下拉但又很怪的東西」⇒ 跟人格 / 第二大腦同一種:找 + 清單,選了的寫在上面
    A.node = node;
    const where = A.role ? `${node ? `<div class="apsel"><b>${esc(nodeT || node)}</b></div>` : ''}<div class="aps"><input class="apq" data-addnq="1" value="${esc(A.nq || '')}" placeholder="找專案項目(標題或編號)" aria-label="找專案項目" autocomplete="off"><div class="apl" id="addnl">${nodeRows(A)}</div></div>` : '<div class="apn">先選角色才要掛到項目;「不掛」就只開席</div>';
    const step = A.busy ? (A.step === 2 ? '掛角色中…' : '開席中…') : '開';
    // Andy 11-19 11:3x:品牌不能開時整個視窗還能一路選到底、「開」是灰的卻沒說為什麼 ⇒ 最上面講原因和怎麼辦,其餘先不顯示
    const FIX = { 這台還沒登入: '在這台的終端機先登入它(例:claude 打 /login),登入後重開這個視窗', 這台沒裝: '在這台裝好它的命令列程式,再重開這個視窗', 這台沒設定: '這台的開席設定還沒列它,要先在設定裡加上它', 這版還不能開: '這版 aibd 還不會開它,等新版' };
    if (!pv) {
      const why = PV.map((p) => `<li><b>${esc(p.name)}</b>:${esc(p.why || '這台還不能開')}${FIX[p.why] ? '。怎麼辦:' + esc(FIX[p.why]) : ''}</li>`).join('');
      return `<div class="dlgbg"><div class="adlg" role="dialog" aria-label="加一席">
      <div class="ah"><h3>加一席</h3><button type="button" class="dx" data-addx="1" aria-label="關閉">✕</button></div>
      <div class="aerr" role="alert">這台現在開不了任何 AI,所以不能加席。</div><ul class="anop">${why || '<li>aibd 沒列出任何品牌</li>'}</ul>
      <div class="afoot"><button type="button" class="k" data-addx="1">關閉</button></div></div></div>`;
    }
    return `<div class="dlgbg"><div class="adlg ${A.still ? 'still' : ''}" role="dialog" aria-label="加一席">
      <div class="ah"><h3>加一席</h3><button type="button" class="dx" data-addx="1" aria-label="關閉">✕</button></div>
      <div class="asec"><span>品牌</span><div class="abrs">${brand}</div><div class="apn">用哪一家的 AI;灰的是這台現在開不了,底下寫了原因</div></div>
      <div class="asec"><span>模式</span><div class="amds">${mode}</div><div class="apn">要它寫程式、提交就選做事;只想討論選安全</div></div>
      ${(A.mode === 'dgrs' && canD) || (A.mode === 'work' && repoModes().includes('work') && A.provider !== 'codex') ? repoSec(A) : ''}${A.mode === 'work' && A.provider === 'codex' ? `<div class="asec"><span>資料夾</span><div class="apn">Codex 做事席只用自己的新資料夾(還不能跟別席同一份、也不能開在別席的倉裡)</div></div>` : ''}${A.mode === 'work' && A.provider !== 'codex' && !curRepo(A) ? `<div class="asec"><span>跟哪一席同一個資料夾</span><div class="apl"><button type="button" class="apr ${!A.withSeat ? 'on' : ''}" data-addws=""><b>自己的資料夾</b><i>新的一份</i></button>${WS.map((x) => { const o = ownerOf(x); return o ? `<div class="apr off" title="這一席是借 @${esc(seatShort(o))} 的資料夾開的;要同一份請選 @${esc(seatShort(o))}"><b>@${esc(seatShort(x))}</b><i>借 @${esc(seatShort(o))} 的,選 @${esc(seatShort(o))}</i></div>` : `<button type="button" class="apr ${A.withSeat === x.seat_id ? 'on' : ''}" data-addws="${esc(x.seat_id)}"><b>@${esc(seatShort(x))}</b><i>共用它的資料夾</i></button>`; }).join('')}</div><div class="apn">${WS.length ? '選一席 = 兩席讀寫同一份程式(例:作者開好後,審查席選作者)' : '現在沒有開著的做事席;先開的這一席用自己的資料夾,第二席再選它'}</div>${A.withSeat ? `<div class="apl aown2"><button type="button" class="apr ${!ownTree(A, WS) ? 'on' : ''}" data-addown="0"><b>同一份檔案(最多兩席寫)</b><i>兩席讀寫同一份,適合作者 + 審查</i></button>${TREE_HOLD ? `<div class="apr off" title="這個做法在修一個安全問題,修好再開"><b>同一個倉、自己的一份(各寫各的,做完再合)</b><i>這個做法在修一個安全問題,修好再開</i></div>` : ''}<button type="button" class="apr ${ownTree(A, WS) ? 'on' : ''}" data-addown="1" ${TREE_HOLD ? 'hidden' : ''}><b>同一個倉、自己的一份(各寫各的,做完再合)</b><i>自己的分支,同時寫不互踩;已有兩席在寫時用這個</i></button></div>` : ''}</div>` : ''}
      <div class="asec"><span>人格</span><div class="apn">這一席的個性與專長;不確定就用預設那個</div>${PS.length ? `<div class="apsel"><b>${esc(pn || '')}</b></div>` : ''}${pers}${regForm('persona')}</div>
      <div class="asec"><span>第二大腦</span><div class="aps"><input class="apq" data-addbq="1" value="${esc(A.bq || '')}" placeholder="找第二大腦" aria-label="找第二大腦" autocomplete="off"><div class="apl" id="addbl">${brainRows(A)}</div></div>${regForm('brain')}</div>
      <div class="asec"><span>角色</span><div class="apn">作者寫程式,審查讀改動給結論;只想聊天就不掛</div>${role}</div>
      <div class="asec"><span>掛在哪一項</span><div class="arow">${where}</div></div>
      ${A.msg ? `<div class="aerr" role="alert">${esc(A.msg)}</div>` : ''}
      ${(() => { const mx = S.maxRun || Number(S.version && (S.version.max_sessions || (S.version.sessions && S.version.sessions.max))) || 0; const run = (S.sessions || []).filter((x) => x.state === 'running' || x.state === 'starting').length; return mx && run >= mx ? `<div class="qwarn">現在已經有 ${run} 席在跑,同時最多 ${mx} 席;這一席開了會先排隊,停掉一席它才開始</div>` : ''; })()}
      <div class="afoot"><button type="button" class="k" data-addx="1">取消</button><button type="button" class="go ${A.busy ? 'busy' : ''}" data-addgo="1" ${A.busy || !pv ? 'disabled' : ''}>${A.busy ? '<i class="spn"></i>' : ''}${esc(step)}</button></div></div></div>`;
  }
  function addErr(e) {
    const c = e && e.code, why = e && e.reason;
    if (c === 'codex_work_own_folder' || why === 'codex_work_own_folder') return 'Codex 做事席只能用自己的新資料夾;「跟哪一席同一個資料夾」選「自己的資料夾」再開';
    if (/own_tree/.test(String(why || '') + ' ' + String((e && e.message) || ''))) return '這版 aibd 還不會開「同一個倉、自己的一份」;改選「同一份檔案」再開';
    if (/\brole\b/.test(String(why || '') + ' ' + String((e && e.message) || '')) && c === 'invalid_request') return '這版 aibd 開席時還不收角色;角色先選「不掛」開好,再到項目裡掛審查';
    if (why === 'with_seat' || c === 'with_seat') return '選的那一席不是資料夾的主人(它是借別席資料夾開的,或不是頁面開的做事席);改選最先開的那一席(通常是作者)';
    if (c === 'preview_refuses' || why === 'preview_refuses') return '預覽只能開「安全」或「做事」席';
    // 0eed7 VERIFY-realentry 79c883726 M4:資料夾或上層有專案設定(.claude)⇒ 400 cwd_has_project_settings;原本落到「這版 aibd 不收這些欄位」
    if (c === 'cwd_has_project_settings' || why === 'cwd_has_project_settings') return '這個資料夾(或它上層)有自己的 Claude 專案設定,為了安全不在這裡開席' + (e && e.body && e.body.file ? '(' + String(e.body.file) + ')' : '');
    // 0eed7 VERIFY-realentry 基準 M3:人格卡讀不到 ⇒ 503 persona_source_unavailable(預覽沒帶到卡時)
    if (c === 'persona_source_unavailable' || why === 'persona_source_unavailable') return '這台讀不到人格卡,開不了任何席' + (e && e.body && e.body.file ? '(' + String(e.body.file) + ')' : '');
    // b4297 ed8d14cf:403 看 code、400 看 reason
    if (e && e.status === 400 && why) return why === 'provider_unsupported' ? '這個品牌這台開不了' : why === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : why === 'persona_missing' ? '找不到這個人格' : why === 'brain' ? '這個第二大腦不在清單裡,重開視窗再選' : why === 'key_missing' ? '頁面送的識別碼不對,重開這個小視窗再試' : '這版 aibd 不收這些欄位(' + why + ')';
    return c === 'shared_folder_writers_full' ? '這個資料夾已經有兩席在寫,請開在自己的資料夾' : c === 'open_built_in_page_only' ? '用戶資料夾有自己的頁時不能開席' : c === 'dgrs_needs_switch' ? '這台還沒開「能改程式」的開關' : c === 'provider_unsupported' ? '這個品牌這台開不了' : c === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : e && e.status === 400 && c === 'invalid_request' ? '這版 aibd 還不能從頁面開席' : errText(e);
  }
  async function doAdd() {
    const A = S.add, PV = providers(), pv = PV.find((p) => p.key === A.provider && p.open) || PV.find((p) => p.open);
    const persona = A.persona;
    if (!pv || !persona) { A.msg = pv ? '先選一個人格(這一席的個性與專長)' : '要選品牌和人格'; A.err = true; return render(); }
    try { localStorage.setItem('ws.lastPersona', persona); } catch (e) { /* 記不住就下次再選 */ }
    A.busy = true; A.step = 1; A.msg = ''; render();
    let body0 = null;
    try {
      // brain 只在清單是 aibd 給的才送(示意的 uid 不送;舊 aibd 不認這一欄)
      const body = (body0 = { provider: pv.id, mode: A.mode || 'safe', persona_uid: persona, idem_key: A.key });
      if (A.brain && S.reg && S.reg.brainsLive) body.brain = A.brain;
      if (body.mode === 'work' && A.withSeat && A.provider !== 'codex' && !curRepo(A)) { body.with_seat = A.withSeat; if (ownTree(A, S.seats.filter((x) => x && x.mode === 'work' && !isArch(x) && (S.sessions || []).some((se) => se.seat_id === x.seat_id && (se.state === 'running' || se.state === 'pending'))))) body.own_tree = true; }
      // a9136 feaa1c1bb:跟某席同資料夾 / 自己一棵樹時選了「審查」⇒ 開席就帶 role:reviewer(讀得到作者的樹);不帶 = observer
      if (body.with_seat && A.role && (A.role === 'reviewer' || /(^|_)reviewer$/.test(String(A.role)) || ((roleList().find((x) => x.uid === A.role) || {}).name === '審查'))) body.role = 'reviewer';
      if (curRepo(A) && (body.mode === 'dgrs' || (body.mode === 'work' && A.provider !== 'codex'))) body.repo = A.repo;
      // 7b81e 986bce061:帶倉可給 base(40 碼小寫提交)⇒ 這一席的分支從那一顆開(例:全合頭);空白不送
      const bs = String(A.base || '').trim();
      if (body.repo && bs) { if (!/^[0-9a-f]{40}$/.test(bs)) { A.busy = false; A.msg = REPO_WHY.base; A.err = true; return render(); } body.base = bs; }
      const r = body.repo ? await fingerAct('seat_open', body, (t, sec) => { if (S.add === A) { A.msg = '請在這台 Mac 跳出的指紋視窗按下(開「' + A.repo + '」這個倉的能改程式席);按過就開 · 還有 ' + sec + ' 秒 · ' + FINGER_BEHIND; render(); } }) : await act('seat_open', body);
      const seat = r.seat || {};
      let note = '';
      const node = A.node !== undefined ? A.node : S.sel;
      if (A.role && node && seat.seat_id) {
        A.step = 2; render();
        try { await act('assign_add', { node_id: node, seat_id: seat.seat_id, role: A.role }); } catch (e) { note = ';席開好了,但沒掛上角色(' + (e && e.code === 'assign_built_in_page_only' ? '用戶資料夾有自己的頁時不能指派' : e && e.code === 'role' ? '這版 aibd 指派只認內建六種,新建的角色還掛不上' : errText(e)) + ')'; } // b4297 e993bd244 實打:assign_add 拒 rol_* → 400 code role
      }
      // 0eed7:提示寫名字(aibd 的 display / name),沒有才寫短碼
      const nm = seat.display || seat.name || seat.code || String(seat.seat_id || '').replace(/^seat_/, '').slice(0, 5) || '新席';
      const ss = r.session || {}, sq = ss.state === 'pending' ? (ss.queue || {}) : null;
      if (sq && Number(sq.slots) > 0) S.maxRun = Number(sq.slots);
      if (sq) note += ';它在排隊:同時最多 ' + (Number(sq.slots) || S.maxRun || '幾') + ' 席在跑、都在用' + (sq.position ? ',排第 ' + sq.position + ' 位' : '') + ',停掉一席它就開始';
      S.add = null; say('開了 @' + nm + note, note ? 'err' : '');
      return tick(true);
    } catch (e) { A.msg = body0 && body0.repo ? '沒開成:' + repoWhy(e) : addErr(e); A.err = true; A.key = idem(); } // 沒開成 ⇒ 下一次是新的一席,換新的 idem_key
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
    return `<form class="pairbox" id="pairf" method="post" action="/api/v1/pair/redeem"><h2>配對這一頁</h2><p>在終端機跑 <code>aib web</code>(或 <code>aibd ctl web</code>),把它給的配對碼打在這裡。</p>${P.msg ? `<p class="bad">${esc(P.msg)}</p>` : ''}<p class="pcd" id="pcd" aria-live="polite">${esc(cdText())}</p><input class="pcode" name="code" id="pcode" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" required placeholder="ABCDE-FGHJK" maxlength="40" value="${esc(P.code || CODE.cur || PREFILL)}" ${P.code || CODE.cur || PREFILL ? 'autofocus' : ''}>${S.pairNew ? `<label class="cip"><input type="checkbox" class="ck" id="pcip" ${S.cip === false ? '' : 'checked'}> 在這一頁確認,終端機不用按 y</label>` : ''}<button type="submit" class="go" ${PAIRING.on ? 'disabled' : ''}>${PAIRING.on ? '配對中…' : '配對'}</button></form>`;
  }
  function render() {
    try { askNotice(); } catch (e) { /* 標題提醒壞了不擋畫面 */ }
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
    // 0eed7 VERIFY-humanpath N1:單擊頭像是插 @、雙擊才開席窗,停止鈕在席窗裡 ⇒ 頭像下多一個看得到的「開」
    // a9136 654162420:停掉的席可以收掉(act seat_archive);先在頁面上問一次,不用瀏覽器彈窗
    const aa = S.archAsk && seatById(S.archAsk);
    const archBar = aa ? `<div class="pvwarn">收掉 @${esc(seatShort(aa))}?收掉後不再列在這裡、也不能重開;它的對話紀錄還在。${aa.folder_owner && aa.folder_owner.how === 'tree' || S.seatGuest[aa.seat_id] ? '它自己一棵樹裡還沒合的提交會留著,右欄出一張卡提醒你。' : ''}<button type="button" class="k" data-archno="1">不要</button><button type="button" class="k g" data-archgo="${esc(aa.seat_id)}">收掉</button></div>` : '';
    const mb = S.mbAsk && seatById(S.mbAsk), mbt = mb && mb.tree ? mb.tree : null;
    const mbBar = mb ? `<div class="pvwarn">把 @${esc(seatShort(mb))} ${mbt && Number(mbt.unmerged) > 0 ? '的 ' + esc(String(mbt.unmerged)) + ' 個提交' : '的新提交'}合回主人那一份${mbt && mbt.base ? '(' + esc(mbt.base) + ')' : ''}?有衝突會停下、復原,不會硬蓋。${mbt && mbt.dirty > 0 ? '它還有 ' + esc(String(mbt.dirty)) + ' 個檔沒提交,aibd 會擋;先叫它提交再合回。' : ''}<button type="button" class="k" data-mbno="1">不要</button><button type="button" class="k g" data-mbgo="${esc(mb.seat_id)}">合回</button></div>` : '';
    const pp = archBar + mbBar + tickBar() + `<div class="pp">${P.length ? P.map((s) => `<span class="ppi">${logo(s, 'big', S.mode === s.seat_id ? 'pick' : '')}<em class="ppn" title="${esc(seatShort(s))}">${seatTail(s)}</em>${(() => { const q = queuedOf(s); return q ? `<small class="tt qd" title="${esc(qText(q))}">排隊中${q.pos ? ' · 第 ' + q.pos + ' 位' : ''}</small>` : ''; })()}${treeTag(s)}${tickTag(s)}<button type="button" class="ppo" data-openseat="${esc(s.seat_id)}" title="打開 @${esc(seatName(s))} 的席窗(看它在做什麼、停止)">開</button>${treeBtn(s)}</span>`).join('') : '<span class="none">沒有在跑的 2.0 席</span>'}${stoppedSeats().map((s) => `<span class="ppi off" title="@${esc(seatName(s))} 停了">${logo(s, 'big', 'off')}<em class="ppn" title="${esc(seatShort(s))}">${seatTail(s)}</em><button type="button" class="ppo g" data-restart="${esc(s.seat_id)}" title="重開 @${esc(seatName(s))}(同一席開新的一段,之前的對話還在)">重開</button>${treeBtn(s)}<button type="button" class="ppo" data-archask="${esc(s.seat_id)}" title="收掉 @${esc(seatName(s))}(不再列在這裡;它自己一棵樹裡沒合的提交會留著並出卡提醒)">收掉</button></span>`).join('')}${S.seats.some(isLegacy) ? '' : '<span class="oldph" title="終端機裡跑的舊式席,等 7ebca 的鏡像(N-web-dmbridge)接上就跟這些席排在一起;現在還沒有資料">舊式席 · 示意</span>'}<span class="lg big you" title="你">你</span><button type="button" class="addb" data-add="1" title="加一席">＋</button></div>`;
    const nt = S.nt ? `<button type="button" class="md ${S.mode === 'node' ? 'on' : ''}" data-mode="node">💬 ${esc(String(S.nt.title || S.nt.node || '項目').slice(0, 24))}</button>` : '';
    const tabs = `<div class="mode"><button type="button" class="md ${S.mode === 'shared' ? 'on' : ''}" data-mode="shared">共同對話</button>${nt}${seat ? `<button type="button" class="md on">${logo(seat, 'sm')} @${esc(seatName(seat))}</button>` : ''}</div>`;
    el.innerHTML = `${pvBar()}${bootBar()}<div class="bar0"><div class="brandt">多席工作台</div>${counts()}<div class="grow"></div>${pvBox()}<input id="sq" class="sq" type="search" placeholder="🔍" aria-label="搜尋" autocomplete="off" maxlength="200" value="${esc(S.q)}">${tickStop()}<a class="ib" href="contacts.html">👥 聯絡中心</a></div>
      ${S.newVer ? `<div class="banner newver" role="status">aibd 換了新版,這一頁還是舊的畫面程式。<button type="button" class="k g" data-reload="1">重新載入</button></div>` : ''}${S.banner ? `<div class="banner ${S.bannerKind}">${esc(S.banner)}</div>` : ''}
      ${decideHtml()}<div class="upper">
        <div class="lcol"><div class="box">${treeHtml()}</div><div class="box host">${funcHtml()}</div></div>
        ${S.add ? addHtml() : ''}<div class="box ctr">${S.q.trim() ? searchWin() : `${tabs}${catBar()}${pp}${seat ? seatWin(seat) : S.mode === 'node' ? nodeWin() : S.mode === 'cat' ? catWin() : seatNowBox(P) + sharedWin()}`}</div>
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
    const r = await act('seat_input', { seat_id: seatId, text });
    return r.__status === 202 ? '已交給它,這一回合結束就收到' : '已送出';
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
  async function sendShared(text) {
    const T = targets(text);
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
        try { await act('thread_send', { thread_id: tid, body: text, to: T.map((s) => s.seat_id), idem_key: idem() }); { const QT = T.filter((s) => queuedOf(s)); say(QT.length ? '已送出,但 ' + QT.map((s) => '@' + seatShort(s)).join(' ') + ' 還在排隊,排到才看得到' : '已送給 ' + T.map((s) => '@' + seatName(s)).join(' ') + ';回答會回到這裡', QT.length ? 'err' : ''); } return true; }
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
    if (!S.me) { say('讀不到你自己的聯絡人(這版 aibd 沒有聯絡中心),送不出', 'err'); return false; }
    const res = await Promise.allSettled(T.map((s) => act('message_send', { to_seat: 'seat:' + s.seat_id, body: text, thread_id: threadId(S.me, 'seat:' + s.seat_id), idem_key: idem() })));
    for (const r of res) if (r.status === 'fulfilled' && r.value && r.value.message_id) S.sentText[r.value.message_id] = text;
    const bad = res.map((r, i) => (r.status === 'rejected' ? '@' + seatName(T[i]) + ' ' + errText(r.reason) : '')).filter(Boolean);
    const QT = T.filter((s) => queuedOf(s));
    say(bad.length ? bad.join(';') : QT.length ? '已送出,但 ' + QT.map((s) => '@' + seatShort(s)).join(' ') + ' 還在排隊,排到才看得到' : '已送給 ' + T.map((s) => '@' + seatName(s)).join(' '), bad.length || QT.length ? 'err' : '');
    return bad.length < T.length;
  }
  // 7ebca 49de928f1:網頁寫信給舊式席 = act legacy_send;信立刻出現在「import ↔ <碼>(信件匯入)」那串
  const LEGACY_ERR = {
    legacy_off: 'aibd 沒有接上舊信件帳(AIBD_LEGACY_CABIN 沒設),這封沒寄出',
    legacy_send_built_in_page_only: '只有內建頁能寄信給舊式席',
    legacy_send_pair_this_run: '這一頁要在這次啟動的 aibd 上重新配對才能寄',
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
      if (c === 'unknown_act_type') say('aibd 還沒有寄信給舊式席的動作,這封沒寄出 · 示意', 'err');
      else if (c === 'lint_refused') say('信被檢查擋下:' + String(b.detail || b.why || er.message || '').slice(0, 200), 'err');
      else if (c && Object.hasOwn(LEGACY_ERR, c)) say(LEGACY_ERR[c], 'err');
      else say(errText(er), 'err');
      return false;
    }
  }
  async function doSend(kind, code) {
    const i = document.getElementById('inp');
    const text = i ? i.value.trim() : '';
    if (!text || S.sending) return;
    S.sending = true; render();
    try {
      if (kind === 'seat') { say(await sendSeat(S.mode, text)); S.drafts[S.mode] = ''; }
      else if (kind === 'legacy') { if (await sendLegacy(code, text)) S.drafts[S.mode] = ''; }
      else if (kind === 'node') { await sendNode(text); S.drafts.node = ''; }
      else if (await sendShared(text)) S.drafts.shared = '';
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
    // c44:這一輪模型沒有回應 ⇒ 把你最後一句 @ 它的話再送一次(同一串、只送它)
    const hob = x.closest('[data-handoff]'); if (hob && !S.sending) return hoAsk(hob.dataset.handoff);
    const gg = x.closest('[data-ghgo]'); if (gg) { const G = (S.gh = S.gh || {}), ni = document.querySelector('[data-ghnum]'); if (ni) G.num = ni.value.trim(); return ghAsk(gg.dataset.ghgo, G.num); }
    const gr = x.closest('[data-ghrun]'); if (gr) return ghAsk('run', gr.dataset.ghrun);
    const pu = x.closest('[data-push]'); if (pu) { pu.disabled = true; return pushGo(pu.dataset.push); }
    const pr = x.closest('[data-pr]'); if (pr && S.sbOk) { S.prAsk = { seat: pr.dataset.pr, title: 'web: ' + S.sbOk.branch }; S.pushMsg = null; render(); return; }
    if (x.closest('[data-prno]')) { S.prAsk = null; render(); return; }
    const pg = x.closest('[data-prgo]'); if (pg && !S.sending) { const ti = document.querySelector('[data-prtitle]'), tb = document.querySelector('[data-prbody]'); if (ti && S.prAsk) S.prAsk.title = ti.value.trim(); if (tb && S.prAsk) S.prAsk.body = tb.value; return prGo(pg.dataset.prgo); }
    const rq = x.closest('[data-rcpask]'); if (rq) { S.rcpAsk = { seat: rq.dataset.rcpask, scope: 'narrow', tests: '' }; S.rcpMsg = null; render(); return; }
    const rsc = x.closest('[data-rcpscope]'); if (rsc && S.rcpAsk) { const ti0 = document.querySelector('[data-rcptests]'); if (ti0) S.rcpAsk.tests = ti0.value; S.rcpAsk.scope = rsc.dataset.rcpscope; render(); return; }
    if (x.closest('[data-rcpno]')) { S.rcpAsk = null; render(); return; }
    const rg = x.closest('[data-rcpgo]'); if (rg && !S.sending) { const ti = document.querySelector('[data-rcptests]'); if (ti && S.rcpAsk) S.rcpAsk.tests = ti.value; return rcpGo(rg.dataset.rcpgo); }
    const rrd = x.closest('[data-rcpread],[data-rcpwd]'); if (rrd) { rrd.disabled = true; return rrd.dataset.rcpread ? rcpAct('receipt_read', rrd.dataset.rcpread) : rcpAct('receipt_withdraw', rrd.dataset.rcpwd); }
    const sbb = x.closest('[data-sendback]'); if (sbb) { sbb.disabled = true; return sendBack(sbb.dataset.sendback); }
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
        else say(c === 'slot_built_in_page_only' ? '用戶資料夾有自己的頁時不能移回' : c === 'carried_pair_read_and_safe_only' ? '換版接回的配對不能移回,重新配對一次' : er && (er.status === 404 || (er.status === 400 && /unknown|type/.test(String(c || er.reason || '')))) ? '這版 aibd 還不能還原;重啟常駐後會自己回來' : errText(er), 'err');
      }
      return tick(true);
    }
    const etab = x.closest('[data-evtab]'); if (etab) { EVS.tab = etab.dataset.evtab; evSave(); return render(); }
    if (x.closest('[data-evpast]')) { S.evPast = !S.evPast; return render(); }
    const ecls = x.closest('[data-evcls]'); if (ecls) { const k = ecls.dataset.evcls; if (EVS.open[k]) delete EVS.open[k]; else { EVS.open[k] = 1; EVS.page[k] = 1; } evSave(); return render(); }
    const epg = x.closest('[data-evpg]'); if (epg) { EVS.page[epg.dataset.evpg] = Math.max(1, Number(epg.dataset.to) || 1); evSave(); return render(); }
    if (x.closest('[data-evnew]')) { S.slotAsOf = ''; for (const k in EVP) delete EVP[k]; await tick(true); return; }
    // 重開接回失敗的席:act seat_restart {seat_id}(頁面憑證只能送 act);這版 aibd 沒有就說清楚
    const tka = x.closest('[data-tickask]'); if (tka) { const id = tka.dataset.tickask, t = S.ticks && S.ticks.get(id); S.tickEdit = { seat: id, every: t ? t.every_min : 20, text: t ? String(t.text || '') : '繼續', msg: '' }; render(); return; }
    if (x.closest('[data-tickx]')) { S.tickEdit = null; render(); return; }
    if ((x.closest('[data-tickgo]') || x.closest('[data-tickclr]')) && S.tickEdit && !S.tickEdit.busy) {
      const T = S.tickEdit, clr = !!x.closest('[data-tickclr]'), every = Math.round(Number(T.every)), text = String(T.text || '').trim();
      if (!clr && !(every >= 5 && every <= 1440)) { T.msg = TICK_WHY.every_min_out_of_range; render(); return; }
      if (!clr && !text) { T.msg = TICK_WHY.text_length; render(); return; }
      T.busy = true; T.msg = ''; render();
      try {
        tickTake(clr ? await act('seat_tick_clear', { seat_id: T.seat }) : await act('seat_tick_set', { seat_id: T.seat, every_min: every, text }));
        const nm = seatShort(seatById(T.seat) || { seat_id: T.seat });
        S.tickEdit = null; say(clr ? '@' + nm + ' 不再自動送' : '@' + nm + ' 每 ' + every + ' 分送「' + text.slice(0, 20) + (text.length > 20 ? '…' : '') + '」' + (S.tickPaused ? '(現在全部暫停中,恢復才送)' : ''));
      } catch (e) { T.busy = false; T.msg = (clr ? '沒停成:' : '沒設成:') + tickWhy(e); }
      render(); return;
    }
    const tkp = x.closest('[data-tickpause]');
    if (tkp && !S.sending) {
      const on = tkp.dataset.tickpause === '1';
      S.sending = true; render();
      try { tickTake(await act('seat_tick_pause', { on })); say(on ? '自動送已全部暫停(設定都留著)' : '自動送恢復了'); }
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
        if (e && e.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request')) { S.mbOff = true; say('這版 aibd 還不能從頁面合回,等新版', 'err'); }
        else say(c === 'merge_conflict' ? '@' + nm + ' 合回有衝突' + (Array.isArray(b.files) && b.files.length ? '(' + b.files.slice(0, 3).join('、') + (b.files.length > 3 ? ' 等 ' + b.files.length + ' 個檔' : '') + ')' : '') + ',已復原、沒動到任何檔;' + (b.card_id ? '右欄有一張卡給你決定怎麼處理' : '叫它先接上主人那一份、解完再合回') : c === 'author_busy' ? '主人' + (b.author ? ' @' + seatShort(seatById(b.author) || { seat_id: String(b.author), name: String(b.author) }) : '') + ' 正在回合中,等它這一輪做完再合回' : c === 'base_unsafe' ? '主人那一份的 git 設定有 aibd 不會跑的項目' + (b.why ? '(' + b.why + ')' : '') + ',先不能合回' : c === 'merge_left_half' ? '@' + nm + ' 合回停在一半、撤不回' + (Array.isArray(b.files) && b.files.length ? '(' + b.files.slice(0, 3).join('、') + ')' : '') + ';先別在主人那一份上動,右欄有一張卡' : c === 'tree_dirty' ? '@' + nm + ' 還有沒提交的檔,先叫它提交再合回' : c === 'base_dirty' ? '主人那一份還有沒提交的檔,先讓主人提交再合回' : c === 'nothing_to_merge' ? '@' + nm + ' 沒有要合回的提交' : c === 'seat_busy' ? '@' + nm + ' 還在寫,等它這一輪做完再合回' : actRefused(e) || '沒合回:' + errText(e), 'err');
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
        say(c === 'seat_busy' ? '@' + nm + ' 還在跑,先停掉再收' : c === 'archive_built_in_page_only' ? '收掉席只能在 aibd 內建的頁面做' : actRefused(e) || '沒收掉:' + errText(e), 'err');
      }
      S.sending = false; render(); tick(true); return;
    }
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
        // a9136 00:00:拒絕的 JSON 是 {code, message, request_id};restart_refused 的原因在 message(8 個 snake_case 字)⇒ 換人話,認不得的照原字
        const restartWhy = (e) => { const r = String((e && ((e.body && e.body.message) || e.message)) || '').trim(); const W = { folder_refused: '原本的資料夾不在了,或裡面有自己的 Claude 專案設定', persona_missing: '這一席的人格卡已經不在名單上', persona_text_unreadable: '這一席的人格卡讀不到', no_conversation_id: '找不到上一段對話的編號', provider_cannot_resume: '這個品牌不能接回上一段對話', no_mode: '這一席沒有記下模式', start_failed: '開不起來(看 aibd 的紀錄)', stop_unconfirmed: '上一段還沒確認停乾淨', dgrs_input_off: RS.dgrs_needs_switch }; return W[r] || r || '接回條件不合'; };
        // a9136 532b54da 的回應碼
        const c = er && er.code, RS = { seat_not_found: '找不到這一席', seat_running: '這一席已經在跑了(或剛按過一次)', seat_archived: '這一席已封存,不能重開', restart_built_in_page_only: '用戶資料夾有自己的頁時不能重開席', dgrs_needs_switch: '這台還沒開「能改程式」的開關,這一席重開不了', dgrs_input_off: '這台還沒開「能改程式」的開關,這一席重開不了' };
        say(er && er.status === 400 && (c === 'unknown_act_type' || c === 'invalid_request') ? '這版 aibd 還不能從頁面重開;請在終端機重開這一席' : c === 'restart_refused' ? '重開被擋:' + restartWhy(er) : RS[c] || errText(er), 'err');
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
    const lt = x.closest('[data-later]');
    if (lt) { const card = lt.dataset.later; const ex = slots().find((y) => y.card === card); if (ex) (S.dmc || (S.dmc = new Map())).set(card, ex); S.dismissed.add(card); render(); try { await act('slot_dismiss', { card_id: card }); } catch (er) { S.dismissed.delete(card); say(errText(er), 'err'); } return tick(true); }
    if (x.closest('[data-add]')) {
      S.add = { provider: '', mode: 'safe', persona: '', role: '', brain: '', key: idem() };
      render();
      loadRegistry();
      { const A = S.add; setTimeout(() => { if (S.add === A) A.still = true; }, 400); } // 進場只播一次,之後點選重畫不再播
      // 0eed7 L:人格清單有介面就用(GET /api/v1/personas,形狀未定:{personas:[{uid, display_name}]} 或陣列);沒有就從現有席收
      const r = await soft(get('/api/v1/personas')).catch(() => null);
      const L = r && (Array.isArray(r.personas) ? r.personas : Array.isArray(r) ? r : null);
      if (L && S.add) { S.personaList = L.filter((p) => p && p.uid).map((p) => [String(p.uid), String(p.display_name || p.name || p.uid)]); brainPreselect(S.add); render(); }
      return;
    }
    if (S.add) {
      const pu = x.closest('[data-addpu]'); if (pu) { S.add.persona = pu.dataset.addpu; brainPreselect(S.add); return render(); }
      const bu = x.closest('[data-addbu]'); if (bu) { S.add.brain = bu.dataset.addbu; S.add.brainTouched = true; return render(); }
      if (x.closest('[data-addown]') && !TREE_HOLD) { S.add.ownTree = x.closest('[data-addown]').dataset.addown === '1'; return render(); }
      const wsb = x.closest('[data-addws]'); if (wsb) { S.add.withSeat = wsb.dataset.addws; return render(); }
      const rp = x.closest('[data-addrepo]'); if (rp) { S.add.repo = rp.dataset.addrepo; S.add.key = idem(); return render(); }
      if (x.closest('[data-addrnew]')) { S.add.repoF = { name: '', path: '' }; render(); const i = document.querySelector('[data-addrf="name"]'); if (i) i.focus(); return; }
      if (x.closest('[data-addrx]')) { if (!(S.add.repoF && S.add.repoF.busy)) S.add.repoF = null; return render(); }
      if (x.closest('[data-addrs]')) return repoReg();
      const nu = x.closest('[data-addnu]'); if (nu) { S.add.node = nu.dataset.addnu; return render(); }
      const ro = x.closest('[data-addrole]'); if (ro) { S.add.role = ro.dataset.addrole; return render(); }
      const nw = x.closest('[data-addnew]'); if (nw) {
        const F = (S.add.newf = { kind: nw.dataset.addnew, name: '', what: '', key: idem() }); render(); const i = document.querySelector('[data-addnf="name"]'); if (i) i.focus();
        if (F.kind === 'brain') { const c = await soft(get('/api/v1/brains/candidates')).catch(() => null); F.root = c && c.root; F.cands = c && Array.isArray(c.candidates) && !c.demo ? c.candidates.filter((y) => y && y.id) : null; if (S.add && S.add.newf === F) render(); }
        return;
      }
      if (x.closest('[data-addnx]')) { S.add.newf = null; return render(); }
      if (x.closest('[data-addns]')) return regAdd();
      const ap = x.closest('[data-addp]'); if (ap) { S.add.provider = ap.dataset.addp; S.add.mode = 'safe'; S.add.key = idem(); return render(); }
      const am = x.closest('[data-addm]'); if (am) { S.add.mode = am.dataset.addm; S.add.key = idem(); return render(); }
      if (x.closest('[data-addx]') || x.classList.contains('dlgbg')) { S.add = null; return render(); }
      if (x.closest('[data-addgo]')) return doAdd();
    }
    const sd = x.closest('[data-send]'); if (sd) return doSend(sd.dataset.send, sd.dataset.code);
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
  el.addEventListener('input', (e) => {
    if (e.target.dataset && e.target.dataset.addbq && S.add) { S.add.bq = e.target.value; const l = document.getElementById('addbl'); if (l) l.innerHTML = brainRows(S.add); return; }
    if (e.target.dataset && e.target.dataset.addnq && S.add) { S.add.nq = e.target.value; const l = document.getElementById('addnl'); if (l) l.innerHTML = nodeRows(S.add); return; }
    if (e.target.dataset && e.target.dataset.tickf && S.tickEdit) { S.tickEdit[e.target.dataset.tickf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.addrf && S.add && S.add.repoF) { S.add.repoF[e.target.dataset.addrf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.addnf && S.add && S.add.newf) { S.add.newf[e.target.dataset.addnf] = e.target.value; return; }
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
      // 519d0 示範後 (3):打 @ 後接著打席名的一部分,清單照打的字過濾;點一下補成完整的
      const m = /(^|\s)@([^\s@]*)$/.exec(e.target.value.slice(0, e.target.selectionStart));
      const q = m ? m[2].toLowerCase() : '', p = document.getElementById('atpop');
      let any = false;
      if (p && m) for (const sp of p.querySelectorAll('[data-at]')) { const k = sp.dataset.at, x = seatById(k); const ok = !q || (k === 'all' ? 'all'.startsWith(q) : !!x && atKeys(x).some((y) => y.includes(q))); sp.hidden = !ok; if (ok) any = true; }
      const open = !!m && any;
      if (open !== S.atOpen) { S.atOpen = open; if (p) p.hidden = !open; }
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
