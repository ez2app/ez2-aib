// 聯絡中心 2.0 — 呼叫層(S89-talk-2c)。舊端點 → 2.0 端點的對照集中在這一支:
// 2a(a9136,PLAN-S89-talk-2c-page-043dd-1118.md「3 的回答」)/ 2b(7b81e,IFACE-S89-talk-2b-7b81e-1118.md)
// 改名只改這裡。讀走 GET、寫一律 POST /api/v1/act(頁面憑證只准讀 + act)。
'use strict';
(function () {
  const KEY = 'aib.page_credential'; // pair_api.rs STORAGE_KEY:配對交換頁存在這個分頁的 sessionStorage
  const DEMO = new URLSearchParams(location.search).has('demo');

  function token() {
    try { return sessionStorage.getItem(KEY) || ''; } catch (e) { return ''; }
  }

  class ApiError extends Error {
    constructor(status, code, message, body) { super(message || code || String(status)); this.status = status; this.code = code || ''; this.body = body || {}; }
  }

  async function call(method, path, body) {
    if (DEMO) return window.AIB_DEMO.call(method, path, body);
    const h = { 'Accept': 'application/json' };
    const t = token();
    if (t) h['Authorization'] = 'Bearer ' + t;
    let init = { method, headers: h, cache: 'no-store', credentials: 'omit' };
    if (body !== undefined) { h['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, init); } catch (e) { throw new ApiError(0, 'offline', String(e)); }
    let j = {};
    try { j = await r.json(); } catch (e) { j = {}; }
    if (!r.ok) throw new ApiError(r.status, j.code, j.message, j);
    return j;
  }
  const get = (p) => call('GET', p);
  const act = (type, fields) => call('POST', '/api/v1/act', Object.assign({ type }, fields));
  const qs = (o) => { const u = new URLSearchParams(); for (const k in o) if (o[k] !== undefined && o[k] !== '' && o[k] !== null) u.set(k, String(o[k])); const s = u.toString(); return s ? '?' + s : ''; };

  // thread_id = 兩端排序後以 ~ 相接,一對一一條(a9136 §1)
  const threadId = (a, b) => [a, b].sort().join('~');

  window.AIB = {
    demo: DEMO,
    ApiError,
    hasToken: () => DEMO || !!token(),
    threadId,

    // ---- 讀 ----
    // 2b §1:一列 = 一個對象;key = seat:<id> / human:<配對 id 前 16>
    contacts: (o) => get('/api/v1/contacts' + qs({ q: o && o.q, include_archived: o && o.archived ? 1 : undefined })),
    // 2b §3:{window, now, pairs:[{from,to,say,decide,shout,failed,last_at}], source?}
    flows: (window_s) => get('/api/v1/flows' + qs({ window: window_s || 600 })),
    // 2b §4:{name, value:{size,speed,count,length,alpha}, revision}
    prefs: () => get('/api/v1/prefs/contacts_fx'),
    // 2a §1:{thread_id, messages:[{message_id, from_seat, to_seat, kind, state, revision, len, created, body?}]}
    // 本文 body 只在內建頁憑證讀時出現;沒有 body ⇒ 頁面只顯示長度與狀態
    thread: (id) => get('/api/v1/threads/' + encodeURIComponent(id)),
    // 這一頁自己的身分(真人 key);2a 待定,暫由 contacts 裡 kind human 且 self:true 那列取
    me: (list) => (list || []).find((c) => c.kind === 'human' && c.self) || null,

    // ---- 寫(全走 act,帶 expected_revision)----
    send: (to, text, threadIdOpt) => act('message_send', { to_seat: to, body: text, thread_id: threadIdOpt }),
    read: (thread, upTo) => act('message_read', { thread_id: thread, message_id: upTo }),
    done: (msg) => act('message_done', { message_id: msg.message_id, expected_revision: msg.revision }),
    prefsSet: (rev, value) => act('prefs_set', { name: 'contacts_fx', expected_revision: rev, value }),
  };
})();
