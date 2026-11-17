// ?demo=1 才用:沒有 aibd 時的假資料(給截圖驗收與本機看版面;正式路徑不碰)。欄位照 2a r0 / 2b r0。
'use strict';
(function () {
  if (!new URLSearchParams(location.search).has('demo')) return;
  const now = Date.now(), iso = (s) => new Date(now - s * 1000).toISOString();
  const ME = 'human:0123456789abcdef';
  const C = [
    { key: ME, kind: 'human', self: true, name: 'You', presence: 'running', unread: 0, decide_open: 0, revision: 1 },
    { key: 'human:fedcba9876543210', kind: 'human', name: 'You (other page)', presence: 'stopped', unread: 0, decide_open: 0, revision: 1 },
    { key: 'seat:demo1', kind: 'seat', name: 'Backend Engineer', persona_uid: 'demo1', provider: 'claude', mode: 'safe', presence: 'running', last_message_at: iso(120), unread: 1, decide_open: 0, revision: 3 },
    { key: 'seat:demo2', kind: 'seat', name: 'Release Reviewer', persona_uid: 'demo2', provider: 'claude', mode: 'dgrs', presence: 'running', last_message_at: iso(300), unread: 1, decide_open: 1, revision: 2 },
    { key: 'seat:demo3', kind: 'seat', name: 'Asset Librarian', persona_uid: 'demo3', provider: 'claude', mode: 'safe', presence: 'queued', queue_position: 2, last_message_at: iso(500), unread: 0, decide_open: 0, pinned: true, revision: 1 },
    { key: 'seat:demo4', kind: 'seat', name: 'Scene Designer', persona_uid: 'demo4', provider: 'codex', mode: 'safe', presence: 'stopped', unread: 0, decide_open: 0, revision: 1 },
    { key: 'seat:demo5', kind: 'seat', name: 'Old Seat (archived)', persona_uid: 'demo5', provider: 'claude', mode: 'safe', presence: 'stopped', archived_at: iso(86400), unread: 0, decide_open: 0, revision: 4 },
  ];
  const T = {};
  const th = (k) => [ME, k].sort().join('~');
  const msg = (id, from, to, kind, state, body, ago) => ({ message_id: id, thread_id: th(from === ME ? to : from), from_seat: from, to_seat: to, kind, state, revision: 1, len: body.length, created: iso(ago), body, unread: false }); // 2a:每則自帶 unread
  T[th('seat:demo1')] = { messages: [msg('m1', 'seat:demo1', ME, 'say', 'received', 'Search change is ready on the local build.', 600), msg('m2', ME, 'seat:demo1', 'say', 'received', 'Looks good, open the PR.', 400), msg('m3', 'seat:demo1', ME, 'say', 'received', '<b>not bold</b> every body is escaped.', 120)] };
  T[th('seat:demo2')] = { messages: [msg('m4', 'seat:demo2', ME, 'decide', 'received', 'One test touches the real home folder: skip it here, or add an exception?', 300), msg('m5', ME, 'seat:demo2', 'say', 'failed', 'Later.', 200)] };
  T[th('seat:demo3')] = { messages: [{ message_id: 'm6', from_seat: 'seat:demo3', to_seat: ME, kind: 'say', state: 'received', revision: 1, len: 42, created: iso(500), unread: false }] };
  T[th('seat:demo1')].messages[2].unread = true; // 寄給我、還沒讀
  T[th('seat:demo2')].messages[0].unread = true;
  let prefs = { name: 'contacts_fx', value: { size: 70, speed: 20, count: 10, length: 100, alpha: 30 }, revision: 1 };
  const flows = { window: 600, now: iso(0), pairs: [
    { from: 'seat:demo1', to: ME, say: 6, decide: 0, shout: 0, failed: 0 },
    { from: ME, to: 'seat:demo1', say: 2, decide: 0, shout: 0, failed: 0 },
    { from: 'seat:demo2', to: ME, say: 0, decide: 1, shout: 0, failed: 0 },
    { from: ME, to: 'seat:demo2', say: 0, decide: 0, shout: 0, failed: 1 },
    { from: 'seat:demo3', to: ME, say: 3, decide: 0, shout: 0, failed: 0 },
  ] };
  const ok = (j) => Promise.resolve(JSON.parse(JSON.stringify(j)));
  const err = (s, code) => Promise.reject(Object.assign(new Error(code), { status: s, code }));
  window.AIB_DEMO = {
    call(method, path, body) {
      const u = new URL(path, location.origin);
      if (method === 'GET' && u.pathname === '/api/v1/contacts') return ok({ contacts: C });
      if (method === 'GET' && u.pathname === '/api/v1/flows') return ok(flows);
      if (method === 'GET' && u.pathname === '/api/v1/prefs/contacts_fx') return ok(prefs);
      if (method === 'GET' && u.pathname.startsWith('/api/v1/threads/')) { const id = decodeURIComponent(u.pathname.slice(16)); return T[id] ? ok(T[id]) : err(404, 'thread_not_found'); }
      if (method === 'POST' && u.pathname === '/api/v1/act') {
        if (body.type === 'prefs_set') { if (body.expected_revision !== prefs.revision) return err(409, 'revision_conflict'); prefs = { name: 'contacts_fx', value: body.value, revision: prefs.revision + 1 }; return ok(prefs); }
        if (body.type === 'message_send') { const id = th(body.to_seat); (T[id] = T[id] || { messages: [] }).messages.push(msg('m' + Math.random().toString(36).slice(2, 8), ME, body.to_seat, 'say', 'dispatched', body.body, 0)); return ok({ ok: true }); }
        if (body.type === 'message_read') { const c = C.find((x) => th(x.key) === body.thread_id); if (c) c.unread = 0; for (const m of (T[body.thread_id] || { messages: [] }).messages) if (m.to_seat === ME) m.unread = false; return ok({ ok: true }); }
        if (body.type === 'message_done') { for (const id in T) for (const m of T[id].messages) if (m.message_id === body.message_id) { m.state = 'processed'; m.unread = false; const c = C.find((x) => th(x.key) === id); if (c) c.decide_open = 0; } return ok({ ok: true }); }
      }
      return err(404, 'not_found');
    },
  };
})();
