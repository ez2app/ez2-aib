// 多席工作台第一版(N-workspace;SPEC-workspace-multiseat-519d0-1118 §10;樣子照 board/mock-workspace-v8-shared / -seat)。
// 內建首頁(apps/web/dist/index.html;聯絡中心改在 contacts.html,0eed7 r3:Andy 開原網址就要看到工作台;519d0 09:3x 裁定改走內建:本文只給內建頁);只打快修鏈頭 3a2d6b09 已有的 /api/v1(讀 GET、寫 POST /api/v1/act),
// 不加新介面;沒有的資料留空不顯示,不做假資料。
// 順序:席的運作窗 → 在場的席與雙擊切換 → 共同對話(@ 送字)→ 右欄事件 → 左樹唯讀 → 主機。
'use strict';
(function () {
  // ---------- 呼叫層(同聯絡中心 api.js:憑證在這個分頁的 sessionStorage,配對交換頁存的) ----------
  const KEY = 'aib.page_credential';
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
    seats: [], sessions: [], now: [], thermal: null, metrics: null, me: null,
    tree: null, kids: null, open: new Set(), sel: null,
    mode: 'shared', // 'shared' 或 seat_id(某一席自己的運作窗)
    out: {}, // session_id → {items:[], next, done}
    threads: {}, // seat_id → messages[]
    banner: null, bannerKind: '', hint: '', hintKind: '', sending: false,
    drafts: { shared: '' }, atOpen: false, sentText: {},
    paired: false, pair: null,
    nt: null, posts: [], ntOff: false,
    stopArm: null, card: null, evOpen: undefined, evAll: false, answered: {}, ansDraft: {}, version: undefined, add: null, aslots: null, usage: null, dismissed: new Set(), readSent: new Set(), readOff: false, ntReadOff: false,
  };
  const el = document.getElementById('app');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hhmm = (iso) => { const d = new Date(iso); return Number.isFinite(d.getTime()) ? d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false }) : ''; };
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
    const [seats, sessions, now, thermal, metrics, contacts, host, slotsR, usage] = await Promise.all([
      get('/api/v1/seats'), soft(get('/api/v1/sessions')), soft(get('/api/v1/now')),
      soft(get('/api/v1/thermal')), soft(get('/api/v1/metrics')), soft(get('/api/v1/contacts')), soft(get('/api/v1/host')),
      soft(get('/api/v1/slots')), soft(get('/api/v1/usage')),
    ]);
    // 7ebca 事件卡槽 + 用量(第二波);這版 aibd 沒有 ⇒ null,事件欄照頁面自己算
    if (S.version === undefined) S.version = await soft(get('/api/v1/version')).catch(() => null);
    S.aslots = slotsR && Array.isArray(slotsR.slots) ? slotsR.slots : null;
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
  async function loadThreads() {
    if (!S.me) return;
    const ids = S.seats.map((s) => s.seat_id);
    await Promise.all(ids.map(async (id) => {
      try { const r = await get('/api/v1/threads/' + encodeURIComponent(threadId(S.me, 'seat:' + id))); S.threads[id] = r.messages || []; }
      catch (e) { if (e && e.status === 404) S.threads[id] = []; else throw e; }
    }));
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
  const stationIdx = (st) => ({ 未開始: 0, 等前置: 0, 已定未做: 1, 已同意未做: 1, 開發中: 2, 審過未推: 3, 審過排隊: 4, 未驗: 5, 實查不通過: 5, 驗過: 6, 實查通過: 6 }[st]);
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
    return h;
  }

  // ---------- 右:事件 ----------
  // 事件卡照流程表「待人決策」(SPEC §14,Andy 11:4x):標籤 + 一句話 + 卡號 + 日期;點了展開
  // 「我們的建議 / 狀態 / 出處」+ 按鈕。① 決定 / 出手、② 壞了、③ 用量、④ 卡住、⑤ 可推進、⑥ 知道
  const LV_TAG = { 1: '決定', 2: '壞了', 3: '用量', 4: '卡住', 5: '可推進', 6: '知道' };
  const TAG_K = { 決定: 'k1', 出手: 'k2', 壞了: 'k2', 知道: 'k3', 可推進: 'k4', 用量: 'k5', 卡住: 'k6' };
  const KIND_TEXT = {
    'session.spawn_failed': '席開不起來', 'session.spawn_rejected': '開席被拒', 'session.stop_unconfirmed': '停了但沒確認行程結束', 'session.timeout': '逾時',
    'thermal.would_nogo': '太熱,先不開新的', 'thermal.would_reap': '太熱,要收掉一席',
    'seat.waiting': '等回應 30 分沒新行', 'seat.quiet': '說在忙,30 分沒輸出', 'session.queued': '在排隊',
    'seat.opened': '席開好', 'session.killed': '停了', 'session.cancelled': '取消', 'session.exited': '停了', 'message.done': '信結案',
  };
  // aibd 的卡(GET /api/v1/slots):新形狀 level / tag / text / card / since / rec / state / src / actions;
  // 舊形狀(slot / kind / item / why / at)照讀。⑤ 與「我寄的信沒送到 / 沒讀」aibd 沒有來源,頁面照算
  function aibdCards() {
    const L = [];
    for (const c of S.aslots) {
      if (S.dismissed.has(c.card_id)) continue;
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
        answer: (c.actions || []).includes('answer'), actions: c.actions || [],
      });
    }
    return L;
  }
  function slots() {
    const L = S.aslots ? aibdCards() : [];
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
      if (x.state === 'failed') L.push(Object.assign({ lv: 2, tag: '壞了', t: x.ended_at || x.started_at, text: seatName(s) + ' 開不起來', st: x.stop_reason || x.ended_by || '' }, b));
      else if (x.state === 'stop_unconfirmed') L.push(Object.assign({ lv: 2, tag: '壞了', t: x.ended_at || x.started_at, text: seatName(s) + ' 停了但沒確認行程結束' }, b));
      else if (recent(x) && x.state === 'running') L.push(Object.assign({ lv: 6, tag: '知道', t: x.started_at, text: seatName(s) + ' 開好在跑' }, b));
      else if (recent(x) && (x.state === 'exited' || x.state === 'stopped')) L.push(Object.assign({ lv: 6, tag: '知道', t: x.ended_at, text: seatName(s) + ' 停了' }, b));
    }
    // ④ 卡住(門檻先用預設,記在卡 N-workspace):我寄的信排著 / 送出中超過 10 分、送到了 30 分還沒讀;
    // 席的這一場排隊(pending)超過 10 分
    const old = (t, min) => { const v = new Date(t).getTime(); return Number.isFinite(v) && Date.now() - v > min * 60000; };
    for (const id in S.threads) {
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
    if (S.tree) {
      for (const n of [...S.tree.values()].filter((n) => n.status === '審過排隊' && n.kind !== 'suggestion')) {
        L.push({ lv: 5, tag: '可推進', t: n.updated || n.updated_at || '', text: n.title || n.id, ref: n.id, st: '審過 + 收據綠,等合', who: n.owner ? '@' + n.owner : '', node: n.id });
      }
    }
    L.sort((a, b) => a.lv - b.lv || String(b.t || '').localeCompare(String(a.t || '')));
    for (const x of L) x.key = x.card || [x.lv, x.ref || '', x.seat || '', x.text].join('|');
    return L;
  }
  // 日期:一天內寫「N 分前」,再早寫月-日(流程表同款)
  function when(t) {
    const v = new Date(String(t || '').replace(' ', 'T')).getTime();
    if (!Number.isFinite(v)) return '';
    if (Date.now() - v < 86400000) return ago(t);
    const d = new Date(v);
    return String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  const EV_MAX = 10;
  function evBody(x) {
    const lines = [];
    if (x.rec) lines.push(`<div><b>我們的建議:</b>${esc(x.rec)}</div>`);
    if (x.st) lines.push(`<div><b>狀態:</b>${esc(x.st)}</div>`);
    const src = [x.src, x.who].filter(Boolean).join(' · ');
    if (src) lines.push(`<div><b>出處:</b>${esc(src)}</div>`);
    const opts = x.answer ? optionsOf(x.text, x.options).filter((o) => o !== '照建議') : [];
    const b = [];
    const A = x.actions || [];
    if ((A.includes('follow_rec') || x.answer) && x.rec) b.push(`<button type="button" class="k g" data-pick="照建議" data-pcard="${esc(x.card || 'local:' + (x.seat || ''))}" data-pseat="${esc(x.seat || '')}">照建議</button>`);
    for (const o of opts) b.push(`<button type="button" class="k" data-pick="${esc(o)}" data-pcard="${esc(x.card || 'local:' + (x.seat || ''))}" data-pseat="${esc(x.seat || '')}">${esc(o)}</button>`);
    if (x.seat && (A.includes('handoff') || (!A.length && x.lv >= 2 && x.lv <= 4))) b.push(`<button type="button" class="k" data-handoff="${esc(x.seat)}">叫他交接</button>`);
    if (x.seat) b.push(`<button type="button" class="k" data-go="${esc(x.seat)}">看這一席</button>`);
    if (x.node || x.ref) b.push(`<button type="button" class="k" data-card="${esc(x.node || x.ref)}">看這一項</button>`);
    if (x.card) b.push(`<button type="button" class="k" data-later="${esc(x.card)}">晚點</button>`);
    const k = x.card || 'local:' + (x.seat || '');
    const free = x.answer && (x.card || x.seat) ? `<div class="ans"><input class="ansi" data-ansfor="${esc(k)}" maxlength="2000" placeholder="${opts.length || x.rec ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[k] || '')}"><button type="button" class="k g" data-answer="${esc(k)}" data-aseat="${esc(x.seat || '')}">回答</button></div>` : '';
    return `<div class="eb">${lines.join('')}${free}${b.length ? `<div class="ea">${b.join('')}</div>` : ''}</div>`;
  }
  function slotsHtml() {
    const L = slots();
    if (S.evOpen === undefined) { const f = L.find((x) => x.lv === 1); if (f) S.evOpen = f.key; } // 第一張 ① 預設展開(資料到了才定)
    const shown = S.evAll ? L : L.slice(0, EV_MAX);
    const card = (x) => {
      const open = S.evOpen === x.key;
      return `<div class="ec l${x.lv} ${open ? 'open' : ''}"><div class="eh" data-evk="${esc(x.key)}"><span class="ek ${TAG_K[x.tag] || 'k3'}">${esc(x.tag || LV_TAG[x.lv] || '')}</span><span class="et">${esc(x.text)}${x.ref ? ` <span class="ecd" data-card="${esc(x.ref)}" title="打開這張卡">${esc(x.ref)}</span>` : ''}</span><span class="ew">${esc(when(x.t))}</span><span class="ex">${open ? '▾' : '▸'}</span></div>${open ? evBody(x) : ''}</div>`;
    };
    return `<div class="bh"><b>事件</b></div><div class="slots">${L.length ? shown.map(card).join('') + (L.length > EV_MAX ? `<button type="button" class="more" data-evall="1">${S.evAll ? '收起 ▴' : '＋' + (L.length - EV_MAX) + ' ▾'}</button>` : '') : '<div class="empty">沒有事件</div>'}</div>`;
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
      <div class="ans"><input class="ansi" data-ansfor="${esc(key)}" maxlength="2000" placeholder="${opts.length ? '或自己寫' : '回答'}" value="${esc(S.ansDraft[key] || '')}"><button type="button" class="k g" data-answer="${esc(key)}" data-aseat="${esc(s.seat_id)}">回答</button>${card ? `<button type="button" class="k" data-later="${esc(card)}">晚點</button>` : ''}</div></div>`;
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
      return `<div class="msg ${mine ? 'me' : ''} ${!mine && m.unread ? 'unread' : ''}"><div class="who">${who}</div><div class="body">${body}</div><div class="tm">${esc(hhmm(m.created))}</div>${seen}</div>`;
    }).join('');
  }
  function sharedWin() {
    const P = present();
    const rows = sharedRows();
    const atpop = `<div class="atpop" id="atpop" ${S.atOpen ? '' : 'hidden'}>${P.map((s) => `<span data-at="${esc(s.seat_id)}">${logo(s, 'big')}${s.mode === 'dgrs' ? '<em class="dg">不擋工具</em>' : ''}</span>`).join('')}${P.length ? `<span class="all" data-at="all">@all${P.some((s) => s.mode === 'dgrs') ? '<em class="dg">含不擋工具的席</em>' : ''}</span>` : '<span class="st">沒有在跑的席</span>'}</div>`;
    return `${headHtml()}
      <div class="tl" id="tl">${rows || '<div class="empty">還沒有對話。打 @ 選一席,寫一句送出。</div>'}</div>
      ${atpop}
      <div class="send"><textarea id="inp" rows="2" maxlength="32768" placeholder="寫給大家;要誰接手就 @ 誰">${esc(S.drafts.shared || '')}</textarea><button type="button" class="go" data-send="shared" ${S.sending ? 'disabled' : ''}>送出</button></div>
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
      const body = typeof p.body === 'string' ? esc(p.body) : `<span class="st">(${esc(p.len != null ? p.len : '?')} 字)</span>`;
      const who = mine ? '<span class="lg sm you">你</span>' : logo(sOf(p.from_seat), 'sm');
      const calls = (p.calls || []).map((c) => {
        const bad = c.failed_code === 'safe_cannot_send_to_dgrs' ? '收不到' : c.failed_code || c.state === 'failed' ? '沒送到' : '';
        const t = bad || RAW[c.status] || c.state || '';
        return `${logo(sOf(c.seat), 'sm')}<span class="st ${bad ? 'bad' : t === '已讀' ? 'ok' : ''}">${esc(t)}</span>`;
      }).join(' ');
      const rb = (p.read_by || []).filter((k) => /^seat:/.test(k)).map((k) => logo(sOf(k), 'sm')).join('');
      return `<div class="msg ${mine ? 'me' : ''}"><div class="who">${who}</div><div class="body">${body}</div><div class="tm">${esc(hhmm(p.created))}</div>${calls ? `<div class="seen">${calls}</div>` : ''}${rb ? `<div class="seen rb"><span class="st">讀過</span>${rb}</div>` : ''}</div>`;
    }).join('');
  }
  function nodeWin() {
    const M = (S.nt.members || []).map((m) => { const id = String(m.seat || '').replace(/^seat:/, ''); const s = seatById(id); return s ? `<span data-at="${esc(id)}">${logo(s, 'sm')}<em class="rl">${esc(ROLE_ZH[m.role] || m.role || '')}</em></span>` : ''; }).join('');
    return `${headHtml()}
      ${M ? `<div class="mem">${M}</div>` : ''}
      <div class="tl" id="tl">${postRows() || '<div class="empty">這一項還沒有對話。寫一句送出;要誰接手就 @ 誰。</div>'}</div>
      <div class="send"><textarea id="inp" rows="2" maxlength="32768" placeholder="對這一項說;@ 誰 = 叫他">${esc(S.drafts.node || '')}</textarea><button type="button" class="go" data-send="node" ${S.sending ? 'disabled' : ''}>送出</button></div>
      ${S.hint ? `<div class="hint ${S.hintKind}">${esc(S.hint)}</div>` : ''}`;
  }
  async function sendNode(text) {
    const all = /(^|\s)@all(\s|$)/i.test(text);
    const to = all ? 'all' : targets(text).map((s) => s.seat_id);
    const f = { thread_id: S.nt.thread_id, body: text, idem_key: idem() };
    if (all || to.length) f.to = to;
    await act('thread_send', f);
    say(all ? '已送給這一項的全部席' : to.length ? '已送,叫了 ' + to.length + ' 席' : '已記在這一項');
  }

  // ---------- 卡片視窗(第四波,SPEC §15:樣子與內容照流程表 board/stations.html 的 #dlg)----------
  // 頂列(站別、卡號、第幾站 / 7、標題、路徑)→ 真人需求 → 走到哪一站 → 基本資料八格 → 為什麼做 →
  // 紀錄(分類頁籤 + 時間線,新的在上)→ 底下的項目 → 用量。資料:GET /api/v1/nodes/{id}(7b81e),
  // 還沒有就用左樹已讀到的同一份(tree.json 的欄位);用量 GET /api/v1/nodes/{id}/usage(7ebca)
  const REC = { need: '真人需求', yes: '真人同意', cc: 'AI 遇事不決', sum: '總結', ho: '交接', drv: '自動推進', shd: '影子續跑', host: '主機', del: '刪除 / 清理', dev: '開發' };
  function recKind(t) {
    t = String(t || '');
    if (/親打|原話|Andy[^,,。]{0,6}[「:]/.test(t)) return 'need';
    if (/同意|核准|准了|准清/.test(t)) return 'yes';
    if (/遇事不決|council/i.test(t)) return 'cc';
    if (/總結|SUMMARY-/.test(t)) return 'sum';
    if (/交接|HANDOFF-/.test(t)) return 'ho';
    if (/自動推進|aib-drive/.test(t)) return 'drv';
    if (/影子續跑|沿用/.test(t)) return 'shd';
    if (/收據|RECEIPT-|租約|測試機/.test(t)) return 'host';
    if (/刪除|清理|作廢/.test(t)) return 'del';
    return 'dev';
  }
  function recordsOf(n) {
    if (Array.isArray(n.records)) return n.records.map((r) => ({ at: r.at || '', by: r.by || '', k: REC[r.kind] ? r.kind : recKind(r.text), t: String(r.text || '') })).sort((a, b) => String(b.at).localeCompare(String(a.at)));
    return String(n.evidence || '').split(' | ').filter(Boolean).map((x) => {
      const m = /^(\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)\s+(\S+)\s+([\s\S]*)$/.exec(x);
      return m ? { at: m[1], by: m[2], k: recKind(m[3]), t: m[3] } : { at: '', by: '', k: recKind(x), t: x };
    }).reverse();
  }
  // 負責席 / 審查人:用標誌(滑過看席位與模型),不寫字;找不到 2.0 席就用席碼當名字
  function seatLogos(v) {
    const L = (Array.isArray(v) ? v : String(v || '').split(/[\s,,、]+/)).map((x) => String(x || '').trim()).filter(Boolean);
    if (!L.length) return '<span class="na">—</span>';
    return L.slice(0, 4).map((c) => logo(S.seats.find((s) => s.code === c || s.seat_id === c || seatName(s) === c) || { seat_id: c, name: c }, 'big')).join('');
  }
  function fld(k, v) { return `<div class="fd"><span>${esc(k)}</span><b class="${v ? '' : 'na'}">${v ? esc(v) : '—'}</b></div>`; }
  function fldH(k, h) { return `<div class="fd"><span>${esc(k)}</span><b>${h}</b></div>`; }
  function spark(a) {
    if (!Array.isArray(a) || a.length < 2) return '';
    const mx = Math.max(1, ...a), w = 240, h = 36, st = w / (a.length - 1);
    return `<svg class="spk" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="currentColor" stroke-width="1.6" points="${a.map((v, i) => (i * st).toFixed(1) + ',' + (h - 2 - (v / mx) * (h - 4)).toFixed(1)).join(' ')}"/></svg>`;
  }
  function useHtml(u) {
    const rows = u && (Array.isArray(u.seats) ? u.seats : Array.isArray(u.usage) ? u.usage : null);
    if (!rows || !rows.length) return '<div class="none">還沒有用量資料</div>';
    const num = (x) => (x >= 1e6 ? (x / 1e6).toFixed(1) + 'M' : x >= 1e3 ? Math.round(x / 1e3) + 'k' : String(x | 0));
    return '<div class="use2">' + rows.map((r) => {
      const a = Array.isArray(r.hourly) ? r.hourly.map((h) => (typeof h === 'number' ? h : Number(h && (h.tokens != null ? h.tokens : h.used)) || 0)) : [];
      const tot = r.total != null ? Number(r.total) : a.reduce((x, y) => x + y, 0);
      return `<div class="ur">${seatLogos(r.seat_id || r.seat)}<span class="spw">${spark(a)}</span><b>${num(tot)}</b></div>`;
    }).join('') + '</div>';
  }
  async function openCard(id) {
    if (!id) return;
    const base = S.tree && S.tree.get(id);
    S.card = { id, n: base ? Object.assign({}, base) : { id, title: id }, tab: 'all', use: null, loaded: false };
    renderCard();
    const r = await soft(get('/api/v1/nodes/' + encodeURIComponent(id))).catch(() => null);
    if (S.card && S.card.id === id && r) {
      const n = r.node || r;
      if (n.station != null && n.status == null) n.status = n.station;
      S.card.n = Object.assign(S.card.n, n);
    }
    const u = await soft(get('/api/v1/nodes/' + encodeURIComponent(id) + '/usage')).catch(() => null);
    if (S.card && S.card.id === id) { S.card.use = u; S.card.loaded = true; renderCard(); }
  }
  let dlg = null, dlgHtml = '';
  function renderCard() {
    if (!S.card) { if (dlg && dlg.open) dlg.close(); return; }
    if (!dlg) {
      dlg = document.createElement('dialog'); dlg.className = 'cdlg'; document.body.appendChild(dlg);
      dlg.addEventListener('click', (e) => {
        if (e.target === dlg || e.target.closest('[data-cclose]')) { S.card = null; dlg.close(); return; }
        const t = e.target.closest('[data-ctab]'); if (t) { S.card.tab = t.dataset.ctab; return renderCard(); }
        const c = e.target.closest('[data-card]'); if (c) return openCard(c.dataset.card);
      });
      dlg.addEventListener('close', () => { S.card = null; dlgHtml = ''; });
    }
    const n = S.card.n, si = stationIdx(n.status), recs = recordsOf(n);
    const u = si == null ? 'todo' : si === 6 ? 'ok' : n.status === '實查不通過' ? 'bad' : si >= 2 ? 'run' : 'todo';
    const crumb = []; for (let p = n.parent && S.tree ? S.tree.get(n.parent) : null; p; p = p.parent ? S.tree.get(p.parent) : null) crumb.unshift(p);
    const step = STATIONS.map((t, j) => `<div class="sp ${si != null && j < si ? 'past' : si === j ? 'cur u-' + u : ''}"><i>${si != null && j < si ? '✓' : j + 1}</i>${esc(t)}</div>`).join('');
    const ap = n.approved || (n.approved_by ? { by: n.approved_by, pointer: n.approval_ref } : null);
    const pf = n.platform || n.platforms;
    const pfx = pf && typeof pf === 'object' ? Object.entries(pf).map(([k, v]) => (k === 'mac' ? 'Mac ' : k === 'win' ? 'Windows ' : k + ' ') + v).join(' · ') : pf || '';
    const wait = n.after || n.wait;
    const cnt = {}; for (const r of recs) cnt[r.k] = (cnt[r.k] || 0) + 1;
    if (S.card.tab !== 'all' && !cnt[S.card.tab]) S.card.tab = 'all';
    const tabs = ['all'].concat(Object.keys(REC)).map((k) => `<button type="button" data-ctab="${k}" class="${S.card.tab === k ? 'on' : ''}" ${k !== 'all' && !cnt[k] ? 'disabled' : ''}>${k === 'all' ? '全部' : REC[k]} <i>${k === 'all' ? recs.length : cnt[k] || 0}</i></button>`).join('');
    const shown = recs.filter((r) => S.card.tab === 'all' || r.k === S.card.tab);
    const tl = shown.length ? shown.map((r, i) => `<div class="te ${i === 0 ? 'new' : ''}"><time>${esc(r.at || '沒有寫時間')}${r.k !== 'dev' ? `<b class="kt kt-${r.k}">${esc(REC[r.k])}</b>` : ''}</time><p>${r.by ? seatLogos(r.by) + ' ' : ''}${esc(r.t)}</p></div>`).join('') : '<div class="none">還沒有紀錄</div>';
    const kids = Array.isArray(n.children) ? n.children : (S.kids && S.kids.get(n.id)) || [];
    const chh = kids.length ? '<div class="ch">' + kids.map((c) => { const ci = stationIdx(c.status || c.station); return `<div class="cr" data-card="${esc(c.id)}" tabindex="0" role="button"><span class="stn ${ci != null ? 's' + (ci + 1) : ''}">${ci != null ? ci + 1 + ' ' + esc(STATIONS[ci]) : esc(c.status || c.station || '—')}</span><b>${esc(c.title || c.id)}</b><em>${c.owner ? seatLogos(c.owner) : ''}</em></div>`; }).join('') + '</div>' : '<div class="none">沒有再往下分的項目</div>';
    const html = `<div class="dw"><div class="dh u-${u}"><div class="dh1"><span class="pill u-${u}">${esc(n.status || '沒有狀態')}</span><span class="kid">${esc(n.id)}</span><span>${si != null ? '第 ' + (si + 1) + ' 站 / 7' : '不在流程上'}</span><button type="button" class="x" data-cclose="1" aria-label="關閉" autofocus>✕</button></div>
      <div class="dt">${esc(n.title || n.id)}</div><div class="crumb">${crumb.length ? crumb.map((p) => `<a data-card="${esc(p.id)}">${esc(String(p.title || p.id).slice(0, 16))}</a>`).join(' › ') : '總綱'}</div></div>
      <div class="db"><div class="dsec">真人需求</div><div class="need">${n.need ? esc(n.need) : '<span class="na">—</span>'}</div>
      <div class="dsec">走到哪一站</div><div class="step">${step}</div>
      <div class="dsec">基本資料</div><div class="fg">${fldH('負責席', seatLogos([n.owner].concat(n.with || [])))}${fldH('審查人', seatLogos(n.reviewer))}${fld('最後更新', n.updated)}${fld('預計', n.eta)}${fld('誰同意的', ap ? [ap.by, ap.at].filter(Boolean).join(' ') : '')}${fld('同意的依據', ap ? ap.pointer || ap.pt || '' : '')}${fld('要先等', Array.isArray(wait) ? wait.join('、') : wait)}${fld('兩平台', pfx)}</div>
      <div class="dsec">為什麼做</div>${n.why ? `<div class="why">${esc(n.why)}</div>` : '<div class="none">沒有寫</div>'}
      <div class="dsec"><span>紀錄(新的在上)</span><span>${recs.length} 筆</span></div>${recs.length ? `<div class="tlt">${tabs}</div>` : ''}<div class="tlw">${tl}</div>
      <div class="dsec"><span>底下的項目</span><span>${kids.length ? kids.length + ' 項' : ''}</span></div>${chh}
      <div class="dsec">用了多久、花了多少</div>${S.card.loaded ? useHtml(S.card.use) : '<div class="none">讀取中…</div>'}
      </div><div class="df"><span>${S.tree && S.tree.has(n.id) ? '跟流程表同一份資料' : ''}</span><span>按 Esc 或點外面關閉</span></div></div>`;
    if (html !== dlgHtml) { const sc = dlg.querySelector('.db'); const top = sc && dlg.dataset.id === n.id ? sc.scrollTop : 0; dlg.innerHTML = html; dlgHtml = html; dlg.dataset.id = n.id; const nb = dlg.querySelector('.db'); if (nb) nb.scrollTop = top; }
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
  function providers() {
    const L = (S.version && S.version.capabilities && S.version.capabilities.providers) || [];
    return L.length ? L : [{ id: 'claude', open: true, modes: ['safe'] }];
  }
  function personas() {
    if (S.personaList && S.personaList.length) return S.personaList;
    const m = new Map();
    for (const x of S.seats) if (x.persona_uid && !m.has(x.persona_uid)) m.set(x.persona_uid, x.display || x.name || x.persona_uid);
    return [...m.entries()];
  }
  function addHtml() {
    const A = S.add, PV = providers(), pv = PV.find((p) => p.id === A.provider) || PV.find((p) => p.open) || PV[0];
    const modes = (pv && pv.modes && pv.modes.length ? pv.modes : ['safe']);
    const PS = personas();
    const n = S.tree && S.sel ? S.tree.get(S.sel) : null;
    return `<div class="dlgbg"><div class="dlg" role="dialog" aria-label="加一席"><h3>加一席</h3>
      <div class="fl"><span>品牌</span><div class="chips">${PV.map((p) => `<button type="button" class="chip ${pv && p.id === pv.id ? 'on' : ''}" data-addp="${esc(p.id)}" ${p.open ? '' : 'disabled'}>${esc(p.id)}</button>`).join('')}</div></div>
      <div class="fl"><span>模式</span><div class="chips">${modes.map((m) => `<button type="button" class="chip ${A.mode === m ? 'on' : ''}" data-addm="${esc(m)}">${esc(m === 'dgrs' ? '能改程式(不擋工具)' : m === 'safe' ? '安全' : m)}</button>`).join('')}</div></div>
      <div class="fl"><span>人格</span>${PS.length ? `<select data-addf="persona">${PS.map(([u, nm]) => `<option value="${esc(u)}" ${A.persona === u ? 'selected' : ''}>${esc(nm)}</option>`).join('')}</select>` : `<input data-addf="persona" value="${esc(A.persona || '')}" placeholder="人格 uid">`}</div>
      <div class="fl"><span>角色</span><select data-addf="role"><option value="">不掛</option>${ROLES.map((r) => `<option ${A.role === r ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select><small>${n ? '掛在「' + esc(String(n.title || n.id).slice(0, 20)) + '」' : '左樹沒選項目就不掛'}</small></div>
      ${A.msg ? `<div class="hint ${A.err ? 'err' : ''}">${esc(A.msg)}</div>` : ''}
      <div class="dlgb"><button type="button" class="k" data-addx="1">取消</button><button type="button" class="go" data-addgo="1" ${A.busy ? 'disabled' : ''}>開</button></div></div></div>`;
  }
  function addErr(e) {
    const c = e && e.code, why = e && e.reason;
    // b4297 ed8d14cf:403 看 code、400 看 reason
    if (e && e.status === 400 && why) return why === 'provider_unsupported' ? '這個品牌這台開不了' : why === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : why === 'persona_missing' ? '找不到這個人格' : why === 'key_missing' ? '頁面送的識別碼不對,重開這個小視窗再試' : '這版 aibd 不收這些欄位(' + why + ')';
    return c === 'open_built_in_page_only' ? '用戶資料夾有自己的頁時不能開席' : c === 'dgrs_needs_switch' ? '這台還沒開「能改程式」的開關' : c === 'provider_unsupported' ? '這個品牌這台開不了' : c === 'provider_mode_unsupported' ? '這個品牌不支援這個模式' : e && e.status === 400 && c === 'invalid_request' ? '這版 aibd 還不能從頁面開席' : errText(e);
  }
  async function doAdd() {
    const A = S.add, PV = providers(), pv = PV.find((p) => p.id === A.provider) || PV.find((p) => p.open);
    const persona = A.persona || (personas()[0] || [])[0];
    if (!pv || !persona) { A.msg = '要選品牌和人格'; A.err = true; return render(); }
    A.busy = true; A.msg = ''; render();
    try {
      const r = await act('seat_open', { provider: pv.id, mode: A.mode || 'safe', persona_uid: persona, idem_key: A.key });
      const seat = r.seat || {};
      let note = '';
      if (A.role && S.sel && seat.seat_id) {
        try { await act('assign_add', { node_id: S.sel, seat_id: seat.seat_id, role: ROLE_EN[A.role] || A.role }); } catch (e) { note = ';席開好了,但沒掛上角色(' + (e && e.code === 'assign_built_in_page_only' ? '用戶資料夾有自己的頁時不能指派' : errText(e)) + ')'; }
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
  function pairHtml() {
    const P = S.pair;
    if (P.view === 'wait') {
      return `<div class="pairbox"><h2>到終端機確認</h2><p>終端機會顯示同一組短碼,一樣就在終端機按 <b>y</b>。</p>${P.short ? `<div class="short">${esc(P.short)}</div>` : ''}<p class="st">確認後這一頁自己會打開(${esc(P.left != null ? P.left + ' 秒內' : '')})。</p><button type="button" class="ib" data-repair="1">重新輸入配對碼</button></div>`;
    }
    return `<form class="pairbox" id="pairf" method="post" action="/api/v1/pair/redeem"><h2>配對這一頁</h2><p>在終端機跑 <code>aib web</code>(或 <code>aibd ctl web</code>),把它給的配對碼打在這裡。</p>${P.msg ? `<p class="bad">${esc(P.msg)}</p>` : ''}<input name="code" id="pcode" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" required placeholder="ABCDE-FGHJK" maxlength="40"><button type="submit" class="go">配對</button></form>`;
  }
  function render() {
    if (S.pair) {
      const k = JSON.stringify(S.pair);
      if (k === pairShown) return;
      pairShown = k;
      el.innerHTML = `<div class="bar0"><div class="brandt">多席工作台</div><div class="grow"></div><a class="ib" href="contacts.html">👥 聯絡中心</a></div>${pairHtml()}`;
      const c = document.getElementById('pcode'); if (c) c.focus();
      return;
    }
    pairShown = '';
    const keepScroll = (() => { const t = document.getElementById('tl'); return t ? { top: t.scrollTop, atEnd: t.scrollTop + t.clientHeight >= t.scrollHeight - 30 } : null; })();
    const inp = document.getElementById('inp');
    const focus = inp && document.activeElement === inp ? { a: inp.selectionStart, b: inp.selectionEnd } : null;
    const ae = document.activeElement, ansF = ae && ae.dataset && ae.dataset.ansfor ? { card: ae.dataset.ansfor, a: ae.selectionStart } : null;
    const seat = S.mode !== 'shared' ? seatById(S.mode) : null;
    if (S.mode === 'node' && !S.nt) S.mode = 'shared';
    if (S.mode !== 'shared' && S.mode !== 'node' && !seat && S.seats.length) S.mode = 'shared';
    const P = present();
    const pp = `<div class="pp">${P.length ? P.map((s) => logo(s, 'big', S.mode === s.seat_id ? 'pick' : '')).join('') : '<span class="none">沒有在跑的 2.0 席</span>'}<span class="lg big you" title="你">你</span><button type="button" class="addb" data-add="1" title="加一席">＋</button></div>`;
    const nt = S.nt ? `<button type="button" class="md ${S.mode === 'node' ? 'on' : ''}" data-mode="node">💬 ${esc(String(S.nt.title || S.nt.node || '項目').slice(0, 24))}</button>` : '';
    const tabs = `<div class="mode"><button type="button" class="md ${S.mode === 'shared' ? 'on' : ''}" data-mode="shared">共同對話</button>${nt}${seat ? `<button type="button" class="md on">${logo(seat, 'sm')} @${esc(seatName(seat))}</button>` : ''}</div>`;
    el.innerHTML = `<div class="bar0"><div class="brandt">多席工作台</div>${counts()}<div class="grow"></div><a class="ib" href="contacts.html">👥 聯絡中心</a></div>
      ${S.banner ? `<div class="banner ${S.bannerKind}">${esc(S.banner)}</div>` : ''}
      <div class="upper">
        <div class="lcol"><div class="box">${treeHtml()}</div><div class="box host">${hostHtml()}</div></div>
        ${S.add ? addHtml() : ''}<div class="box ctr">${tabs}${pp}${seat ? seatWin(seat) : S.mode === 'node' ? nodeWin() : sharedWin()}</div>
        <div class="box">${slotsHtml()}</div>
      </div>`;
    const t = document.getElementById('tl');
    if (t) t.scrollTop = !keepScroll || keepScroll.atEnd ? t.scrollHeight : keepScroll.top;
    if (focus) { const i = document.getElementById('inp'); if (i) { i.focus(); i.setSelectionRange(focus.a, focus.b); } }
    if (ansF) { const i = [...el.querySelectorAll('[data-ansfor]')].find((x) => x.dataset.ansfor === ansF.card); if (i) { i.focus(); i.setSelectionRange(ansF.a, ansF.a); } }
  }

  // ---------- 動作 ----------
  function say(msg, kind) { S.hint = msg; S.hintKind = kind || ''; }
  function errText(e) {
    if (!e) return '出錯了';
    if (e.status === 0) return '連不上 aibd';
    if (e.code === 'user_page_safe_seats_only') return '用戶資料夾的頁只能對安全模式的席送字';
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
    const code = String((document.getElementById('pcode') || {}).value || '').trim().toUpperCase();
    if (!code) return;
    let msg = '';
    try {
      const r = await fetch('/api/v1/pair/redeem', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ code }).toString(), credentials: 'omit', cache: 'no-store' });
      const t = await r.text();
      const k = "setItem('" + KEY + "','";
      const i = r.ok ? t.indexOf(k) : -1;
      const cred = i >= 0 ? t.slice(i + k.length).split("'")[0] : '';
      if (cred && /^[A-Za-z0-9_.~-]+$/.test(cred)) { sessionStorage.setItem(KEY, cred); S.paired = false; S.pair = { view: 'wait' }; render(); return tick(true); }
      let j = null; try { j = JSON.parse(t); } catch (er) { /* 不是 JSON */ }
      const c = j && (j.code || (j.error && j.error.code));
      msg = c === 'pairing_rate_limited' ? '試太多次了,等一下再試' : c === 'pairing_code_reused' ? '這組碼用過了,請在終端機重跑 aib web 拿新碼' : r.status === 403 ? '被 aibd 擋下(' + (c || r.status) + ')' : '配對碼不對或已過期';
    } catch (er) { msg = '連不上 aibd'; }
    S.pair = { view: 'code', msg }; render();
  });
  el.addEventListener('dblclick', (e) => {
    const lg = e.target.closest('[data-seat]');
    if (lg && seatById(lg.dataset.seat)) { e.preventDefault(); setMode(lg.dataset.seat); }
  });
  el.addEventListener('click', async (e) => {
    const x = e.target;
    const md = x.closest('[data-mode]'); if (md) return setMode(md.dataset.mode);
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
    const ek = x.closest('[data-evk]');
    if (ek) { S.evOpen = S.evOpen === ek.dataset.evk ? '' : ek.dataset.evk; return render(); }
    if (x.closest('[data-evall]')) { S.evAll = !S.evAll; return render(); }
    const ho = x.closest('[data-handoff]');
    if (ho && !S.sending) {
      S.sending = true;
      try { await act('seat_input', { seat_id: ho.dataset.handoff, text: QUICK[1][1] }); say('已叫 @' + seatName(seatById(ho.dataset.handoff) || { seat_id: ho.dataset.handoff }) + ' 交接'); } catch (er) { say(errText(er), 'err'); }
      S.sending = false; return tick(true);
    }
    const pk = x.closest('[data-pick]');
    if (pk) return answer(pk.dataset.pseat, pk.dataset.pcard, pk.dataset.pick);
    const lt = x.closest('[data-later]');
    if (lt) { const card = lt.dataset.later; S.dismissed.add(card); render(); try { await act('slot_dismiss', { card_id: card }); } catch (er) { S.dismissed.delete(card); say(errText(er), 'err'); } return tick(true); }
    if (x.closest('[data-add]')) {
      S.add = { provider: '', mode: 'safe', persona: '', role: '', key: idem() };
      render();
      // 0eed7 L:人格清單有介面就用(GET /api/v1/personas,形狀未定:{personas:[{uid, display_name}]} 或陣列);沒有就從現有席收
      const r = await soft(get('/api/v1/personas')).catch(() => null);
      const L = r && (Array.isArray(r.personas) ? r.personas : Array.isArray(r) ? r : null);
      if (L && S.add) { S.personaList = L.filter((p) => p && p.uid).map((p) => [String(p.uid), String(p.display_name || p.name || p.uid)]); render(); }
      return;
    }
    if (S.add) {
      const ap = x.closest('[data-addp]'); if (ap) { S.add.provider = ap.dataset.addp; S.add.mode = 'safe'; S.add.key = idem(); return render(); }
      const am = x.closest('[data-addm]'); if (am) { S.add.mode = am.dataset.addm; S.add.key = idem(); return render(); }
      if (x.closest('[data-addx]') || x.classList.contains('dlgbg')) { S.add = null; return render(); }
      if (x.closest('[data-addgo]')) return doAdd();
    }
    const sd = x.closest('[data-send]'); if (sd) return doSend(sd.dataset.send);
    if (x.closest('[data-repair]')) { dropToken(); S.paired = false; S.pair = { view: 'code' }; return render(); }
  });
  el.addEventListener('change', (e) => { const f = e.target.dataset && e.target.dataset.addf; if (f && S.add) S.add[f] = e.target.value; });
  el.addEventListener('input', (e) => {
    if (e.target.dataset && e.target.dataset.addf && S.add) { S.add[e.target.dataset.addf] = e.target.value; return; }
    if (e.target.dataset && e.target.dataset.ansfor) { S.ansDraft[e.target.dataset.ansfor] = e.target.value; return; }
    if (e.target.id !== 'inp') return;
    S.drafts[S.mode === 'shared' ? 'shared' : S.mode] = e.target.value;
    if (S.mode === 'shared') {
      const open = /(^|\s)@$/.test(e.target.value.slice(0, e.target.selectionStart));
      if (open !== S.atOpen) { S.atOpen = open; const p = document.getElementById('atpop'); if (p) p.hidden = !open; }
    }
  });
  el.addEventListener('keydown', (e) => {
    if (e.target.dataset && e.target.dataset.ansfor && e.key === 'Enter' && !e.isComposing) { e.preventDefault(); const b = (e.target.closest('.ans') || el).querySelector('[data-answer]'); if (b) b.click(); return; }
    if (e.target.id === 'inp' && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doSend(S.mode === 'shared' || S.mode === 'node' ? S.mode : 'seat'); }
    if (e.key === 'Enter' && e.target.matches('.lg[data-seat]')) setMode(e.target.dataset.seat); // 鍵盤:Enter = 雙擊
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
  async function tick(force) {
    if (busy) return;
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
        if (!S.kids) await loadTree();
      }
      await loadCore();
      if (S.mode === 'node') await loadNode(); else if (S.mode !== 'shared') await loadOut(S.mode);
      if (S.mode === 'shared' || force || Date.now() - last > 6000) { await loadThreads(); last = Date.now(); }
      await markRead();
      if (!(S.banner && /用戶資料夾頁的配對/.test(S.banner))) S.banner = null;
    } catch (e) {
      if (e && e.status === 401) { dropToken(); S.paired = false; S.pair = { view: 'code', msg: '配對已失效,請重新輸入配對碼' }; }
      else if (e && e.status === 403) { S.banner = '沒有權限:' + ((e && e.code) || '?'); S.bannerKind = 'err'; }
      else if (e && e.status === 0) { S.banner = '連不上 aibd(重試中)'; S.bannerKind = 'err'; }
      else { S.banner = '讀取出錯:' + ((e && (e.code || e.message)) || '?'); S.bannerKind = 'err'; }
    } finally { busy = false; if (!S.add) render(); } // 加一席的小視窗開著時不重畫(選單會被關掉)
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
