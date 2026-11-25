// 省時看板(saved.html)— c5d88 PLAN-skipstop F2。翻牌與四格照定版頁 PREVIEW-timesave-board-7b81e-1122.html。
// 規矩(DECISION-timesave-board-andy-7b81e-1122):
//   1. 頁面只畫,不自己算:數字全部來自 POST /api/v1/act {type:"time_saved"}(aibd 驗過外掛才跑它的 saved --json)。
//   2. 只有讀到的數字變了才翻;沒有新的省下就不動,不用時鐘跳秒。
//   3. 讀不到 / 沒裝 / 關了:說出來,不畫看板,不放 0(0 是一句話:「沒省到」)。
//   4. 大字是牆鐘(clock_ms);預檢 · 影子分項、另預估、擋錯倒扣寫在旁邊,不加進大字。
'use strict';
(() => {
  const KEY = 'aib.page_credential'; // pair_api.rs STORAGE_KEY
  const EVERY_MS = 30000; // 多久重讀一次(aibd 對同一問 5 秒內給同一答)
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (id) => document.getElementById(id);
  const p2 = (n) => String(n).padStart(2, '0');
  const sec = (ms) => Math.floor((Number(ms) || 0) / 1000);
  const say = (t, hm) => Math.floor(t / 3600) + (hm ? ' 小時 ' : ' 時 ') + p2(Math.floor(t / 60) % 60) + ' 分 ' + p2(t % 60) + ' 秒';
  const short = (t) => Math.floor(t / 3600) + ':' + p2(Math.floor(t / 60) % 60) + (t < 3600 ? ':' + p2(t % 60) : '');
  const WHY = {
    not_installed: ['還沒裝省時外掛', '裝上 aib-skipstop 之後,這裡才會開始記。'],
    off: ['省時外掛已關閉', '打開之後才會繼續記;關閉期間的數字不會補。'],
    not_runnable: ['省時外掛不能跑', '安裝的檔案和登記的不一樣,所以沒有執行它。重新安裝外掛。'],
    unreadable: ['省時外掛的設定檔讀不懂', '重新安裝外掛。'],
    no_answer: ['省時外掛沒有回答', '它沒有在時間內正常結束。'],
    bad_answer: ['省時外掛的回答讀不懂', '外掛與 AIB 的版本可能對不上。'],
    no_credential: ['這個分頁還沒配對', '從工作台打開這一頁。'],
    offline: ['連不上 AIB', '稍後會再試。'],
  };

  async function read() {
    let t = ''; try { t = sessionStorage.getItem(KEY) || ''; } catch (e) { t = ''; }
    if (!t) return { state: 'no_credential' };
    let r;
    try {
      r = await fetch('/api/v1/act', { method: 'POST', cache: 'no-store', credentials: 'omit',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t },
        body: JSON.stringify({ type: 'time_saved', utc_offset_min: -new Date().getTimezoneOffset() }) });
    } catch (e) { return { state: 'offline' }; }
    let j = null; try { j = await r.json(); } catch (e) { j = null; }
    if (!r.ok || !j || typeof j.state !== 'string') return { state: r.status === 401 || r.status === 403 ? 'no_credential' : 'bad_answer' };
    return j;
  }

  // 一格的數字:全部都在才算數;缺一個就整份不畫
  function box(b) {
    if (!b || typeof b.clock_ms !== 'number' || typeof b.skip_clock_ms !== 'number' || !b.total || !b.stop) return null;
    const n = [b.clock_ms, b.skip_clock_ms, b.stop.confirmed_ms, b.total.estimated_ms, b.total.penalty_ms, b.total.count, b.clock_unknown_runs];
    if (n.some((x) => typeof x !== 'number' || !(x >= 0))) return null;
    return { t: sec(b.clock_ms), skip: sec(b.skip_clock_ms), stop: sec(b.stop.confirmed_ms), est: sec(b.total.estimated_ms), pen: sec(b.total.penalty_ms), count: b.total.count, unknown: b.clock_unknown_runs };
  }

  // ── 主看板:木紋翻牌(定版頁原樣);只有目標數字變了才翻 ──
  const row = $('flaps'), boardBox = $('board');
  const BASE = [10, 10, 6, 10, 6, 10]; // 分、秒的十位只翻 0–5
  const digits = (t) => (p2(Math.min(99, Math.floor(t / 3600))) + p2(Math.floor(t / 60) % 60) + p2(t % 60)).split('');
  const cells = [];
  let gx = 0;
  ['時', '分', '秒'].forEach((lab, g) => {
    if (g) { const c = document.createElement('span'); c.className = 'b-colon'; c.append(document.createElement('i'), document.createElement('i')); row.appendChild(c); }
    const grp = document.createElement('span'); grp.className = 'b-grp';
    const pair = document.createElement('span'); pair.className = 'b-pair';
    for (let k = 0; k < 2; k++) {
      const c = document.createElement('span'); c.className = 'flap'; c.dataset.v = '0'; c.target = '0'; c.busy = false; c.base = BASE[g * 2 + k];
      c.style.setProperty('--gx', ((gx++ * 41) % 100) + '%');
      for (const cls of ['f-top', 'f-bot']) { const s = document.createElement('span'); s.className = cls; const i = document.createElement('i'); i.textContent = '0'; s.appendChild(i); c.appendChild(s); }
      pair.appendChild(c); cells.push(c);
    }
    const l = document.createElement('span'); l.className = 'b-lab'; l.textContent = lab;
    grp.append(pair, l); row.appendChild(grp);
  });
  const leaf = (cls, ch) => { const s = document.createElement('span'); s.className = cls + ' leaf'; const i = document.createElement('i'); i.textContent = ch; s.appendChild(i); return s; };
  const flipOnce = (c, ch) => new Promise((done) => {
    const old = c.dataset.v, top = c.querySelector('.f-top i'), bot = c.querySelector('.f-bot i');
    const l1 = leaf('f-top', old), l2 = leaf('f-bot', ch);
    c.append(l1, l2); top.textContent = ch;
    const D = 110;
    l1.animate([{ transform: 'rotateX(0deg)', filter: 'brightness(1)' }, { transform: 'rotateX(-90deg)', filter: 'brightness(.4)' }], { duration: D, easing: 'ease-in', fill: 'forwards' });
    l2.animate([{ transform: 'rotateX(90deg)', filter: 'brightness(1.45)' }, { transform: 'rotateX(0deg)', filter: 'brightness(1)' }], { duration: D, delay: D, easing: 'cubic-bezier(.3,1.5,.5,1)', fill: 'forwards' })
      .finished.then(() => { bot.textContent = ch; c.dataset.v = ch; l1.remove(); l2.remove(); done(); });
  });
  async function roll(c, target, delay) {
    c.target = target; if (c.busy) return; c.busy = true;
    if (delay) await new Promise((r) => setTimeout(r, delay));
    while (c.dataset.v !== c.target) await flipOnce(c, String((+c.dataset.v + 1) % c.base));
    c.busy = false;
  }
  const setStatic = (c, ch) => { c.dataset.v = ch; c.target = ch; c.querySelectorAll('i').forEach((i) => { i.textContent = ch; }); };
  function showBoard(t, first) {
    const d = digits(t);
    boardBox.setAttribute('aria-label', '今天替你省 ' + say(t, true));
    cells.forEach((c, i) => { if (reduce) setStatic(c, d[i]); else if (c.target !== d[i]) roll(c, d[i], first ? 350 + i * 120 : 0); });
  }
  const plus = document.createElement('span'); plus.className = 'b-plus n'; boardBox.appendChild(plus);

  // ── 四格 ──
  const TILES = [['week', '本週'], ['month', '本月'], ['year', '本年'], ['all', '至今總省']];
  const tiles = $('tiles'), tileEls = {};
  const span = (cls, text) => { const s = document.createElement('span'); s.className = cls; if (text !== undefined) s.textContent = text; return s; };
  TILES.forEach(([k, name], i) => {
    const t = document.createElement('div'); t.className = 'tile card in d' + (i + 2);
    const v = span('v n'), s = span('s'), p = span('p'), h = span('h'), x = span('x');
    s.append(p, document.createTextNode(' · '), h);
    t.append(span('k', name), v, s, x); tiles.appendChild(t);
    tileEls[k] = { v, p, h, x, t: null };
  });
  // 第一筆的日期,用這台瀏覽器的時區(外掛寫的是 UTC);讀不懂就不寫
  const sinceText = (s) => { const d = new Date(String(s || '')); return s && !isNaN(d) ? '自 ' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()) + ' 開始記' : ''; };
  const flash = (e) => { if (!reduce) e.animate([{ color: '#E3C072' }, { color: '' }], { duration: 1400, easing: 'ease-out' }); };

  let last = null; // 上一次畫出來的今天秒數;null = 還沒畫過看板
  function draw(r) {
    const ok = r.state === 'ok' && r.saved && r.saved.boxes;
    const B = ok ? { today: box(r.saved.boxes.today) } : {};
    if (ok) for (const [k] of TILES) B[k] = box(r.saved.boxes[k]);
    const whole = ok && Object.values(B).every(Boolean);
    const state = whole ? 'ok' : ok ? 'bad_answer' : r.state;
    $('board').hidden = $('tiles').hidden = $('pill').hidden = !whole;
    const note = $('note');
    if (!whole) {
      const w = WHY[state] || ['讀不到省時數字', ''];
      note.hidden = false; note.textContent = ''; const b = document.createElement('b'); b.textContent = w[0]; note.append(b, document.createTextNode(w[1]));
      $('foot').textContent = ''; last = null;
      return;
    }
    const T = B.today;
    $('pilln').textContent = say(T.t, true);
    $('bsub').textContent = ''; $('bsub').append(span('p', '預檢 ' + short(T.stop)), document.createTextNode(' · '), span('h', '影子 ' + short(T.skip)), document.createTextNode(T.est ? ' · 另預估 ' + short(T.est) : ''));
    showBoard(T.t, last === null);
    if (last !== null && T.t > last) {
      const d = T.t - last;
      plus.textContent = '+' + Math.floor(d / 60) + ' 分 ' + p2(d % 60) + ' 秒';
      if (!reduce) plus.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none', offset: .15 }, { opacity: 1, offset: .75 }, { opacity: 0, transform: 'translateY(-6px)' }], { duration: 2600, fill: 'forwards' });
      flash($('pilln'));
    }
    last = T.t;
    for (const [k] of TILES) {
      const b = B[k], e = tileEls[k];
      e.v.textContent = say(b.t, false);
      if (e.t !== null && e.t !== b.t) flash(e.v);
      e.t = b.t;
      e.p.textContent = '預檢 ' + short(b.stop); e.h.textContent = '影子 ' + short(b.skip);
      const more = [];
      if (b.est) more.push('另預估 ' + short(b.est));
      if (b.pen) more.push('擋錯 −' + short(b.pen));
      if (b.unknown) more.push('另 ' + b.unknown + ' 次未計');
      e.x.textContent = more.join(' · ') || (k === 'all' ? sinceText(r.saved.since) : '');
    }
    // 還沒有任何一筆:看板是 0,要說這個 0 是「還沒有紀錄」
    note.hidden = B.all.count > 0;
    if (B.all.count === 0) { note.textContent = ''; const b = document.createElement('b'); b.textContent = '還沒有紀錄'; note.append(b, document.createTextNode('第一次真的省下時間,上面的看板才會翻。')); }
    $('foot').textContent = '大字是牆鐘上省下的時間;「未計」是有省到、但沒有全部真跑的那一次可以比的次數。預估與擋錯倒扣不加進大字。';
  }

  let busy = false;
  async function tick() { if (busy || document.hidden) return; busy = true; try { draw(await read()); } finally { busy = false; } }
  tick();
  setInterval(tick, EVERY_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
})();
