// 一席自己那一份(git 樹)給真人看:改了什麼 / 提交 / 檔案(b4297 11-20 第二輪「審查席讀樹接到頁面」)。
// 讀 GET /api/v1/seats/{id}/tree[/log|/diff](tree_read.rs,只讀;aibd 每次讀都記 worktree.read 事件)。
// 任何按鈕帶 data-tree-seat="<seat_id>"(可加 data-tree-name)就會打開;ws.js 只負責畫按鈕。
// 頁面 CSP 是 script-src 'self' / style-src 'self':不寫行內樣式,樣式全在 tree.css。
'use strict';
(function () {
  const KEY = 'aib.page_credential'; // 同 ws.js / api.js
  const token = () => { try { return sessionStorage.getItem(KEY) || ''; } catch (e) { return ''; } };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  async function get(path) {
    const h = { Accept: 'application/json' };
    const t = token();
    if (t) h.Authorization = 'Bearer ' + t;
    let r;
    try { r = await fetch(path, { method: 'GET', headers: h, cache: 'no-store', credentials: 'omit' }); } catch (e) { const er = new Error('offline'); er.reason = 'offline'; throw er; }
    let j = {};
    try { j = await r.json(); } catch (e) { j = {}; }
    if (!r.ok) { const er = new Error(j.message || String(r.status)); er.status = r.status; er.reason = j.reason || j.code || ''; throw er; }
    return j;
  }

  // aibd 的拒絕原因 → 人看得懂的話(tree_read.rs answer 的 reason)
  const WHY = {
    no_tree: '這一席沒有自己的一份(它不是開在倉裡的樹,或樹已經收掉了;分支還在)',
    not_reviewer: '只有被指派為審查的席能讀別席的樹',
    tree_built_in_page_only: '只有 AIB 內建的頁面能看席的樹',
    assignments_unavailable: '讀不到指派表,稍後再試',
    path: '這個路徑不在那一席的樹裡',
    git: '在那一棵樹裡跑 git 失敗',
    offline: '連不上 AIB',
  };
  const whyText = (e) => WHY[e && e.reason] || (e && e.status === 404 ? WHY.no_tree : '讀不到:' + ((e && e.message) || '不明原因'));

  const T = { seat: '', name: '', tab: 'diff', path: '', dlg: null };
  const enc = (s) => encodeURIComponent(s);
  const treeUrl = (seat) => '/api/v1/seats/' + enc(seat) + '/tree';
  const base = () => treeUrl(T.seat);

  function dialog() {
    if (T.dlg) return T.dlg;
    const d = document.createElement('dialog');
    d.className = 'trdlg';
    d.setAttribute('aria-label', '這一席自己的一份');
    d.innerHTML = '<div class="trhd"><b class="trtt"></b><span class="trsub"></span><button type="button" class="trx" data-trclose aria-label="關閉">×</button></div>'
      + '<div class="trtabs" role="tablist"><button type="button" role="tab" data-trtab="diff">改了什麼</button><button type="button" role="tab" data-trtab="log">提交</button><button type="button" role="tab" data-trtab="files">檔案</button></div>'
      + '<div class="trbody" tabindex="0"></div>';
    d.addEventListener('click', (e) => {
      if (e.target === d || e.target.closest('[data-trclose]')) { d.close(); return; }
      const tb = e.target.closest('[data-trtab]');
      if (tb) { T.tab = tb.dataset.trtab; if (T.tab === 'files') T.path = ''; show(); return; }
      const p = e.target.closest('[data-trpath]');
      if (p) { T.path = p.dataset.trpath; show(); }
    });
    document.body.appendChild(d);
    T.dlg = d;
    return d;
  }

  const body = () => T.dlg.querySelector('.trbody');
  const say = (h) => { body().innerHTML = h; };

  // unified diff 一行一行上色(只靠 class);檔頭、區段頭、增、刪
  function diffHtml(patch) {
    return String(patch || '').split('\n').map((l) => {
      const c = l.startsWith('diff --git') ? 'trf' : l.startsWith('@@') ? 'trh' : l.startsWith('+++') || l.startsWith('---') ? 'trm' : l.startsWith('+') ? 'tra' : l.startsWith('-') ? 'trd' : '';
      return c ? '<span class="' + c + '">' + esc(l) + '</span>' : esc(l);
    }).join('\n');
  }

  async function showDiff() {
    const d = await get(base() + '/diff');
    const n = Number(d.uncommitted) || 0;
    // a9136 04a7975c0:席自己的一份是獨立 clone,aibd 不在裡面跑 git,沒提交的檔數不算(null)
    const un = d.uncommitted === null ? ';它是那一席自己的一份(獨立複本),沒提交的檔不在這裡算' : n ? ';另外還有 <b>' + n + '</b> 個檔改了沒提交,不在下面' : '';
    const top = '<p class="trnote">從開席那一刻(' + esc(String(d.base || '').slice(0, 9)) + ')到現在的提交' + un + (d.truncated ? ';太長,只顯示前面一段' : '') + '</p>';
    if (!String(d.patch || '').trim()) { say(top + '<p class="trnone">還沒有提交任何改動</p>'); return; }
    say(top + (d.stat ? '<pre class="trstat">' + esc(d.stat) + '</pre>' : '') + '<pre class="trdiff">' + diffHtml(d.patch) + '</pre>');
  }

  async function showLog() {
    const d = await get(base() + '/log');
    const cs = d.commits || [];
    if (!cs.length) { say('<p class="trnone">開席之後還沒有提交</p>'); return; }
    say('<ol class="trlog">' + cs.map((c) => '<li><code>' + esc(String(c.sha || '').slice(0, 9)) + '</code> <span class="trsj">' + esc(c.subject) + '</span><span class="trwho">' + esc(c.author) + ' · ' + esc(String(c.at || '').replace('T', ' ').slice(0, 16)) + '</span></li>').join('') + '</ol>');
  }

  async function showFiles() {
    const d = await get(base() + '?path=' + enc(T.path));
    const parts = T.path ? T.path.split('/') : [];
    const crumbs = ['<button type="button" class="trcr" data-trpath="">根</button>'].concat(parts.map((p, i) => '<button type="button" class="trcr" data-trpath="' + esc(parts.slice(0, i + 1).join('/')) + '">' + esc(p) + '</button>')).join('<i>/</i>');
    let h = '<div class="trcrumbs">' + crumbs + '</div>';
    if (d.kind === 'dir') {
      const es = (d.entries || []).slice().sort((a, b) => (a.kind === 'dir') === (b.kind === 'dir') ? (a.name < b.name ? -1 : 1) : a.kind === 'dir' ? -1 : 1);
      h += '<ul class="trls">' + es.map((e) => {
        const p = (T.path ? T.path + '/' : '') + e.name;
        return e.kind === 'link' ? '<li class="trln">' + esc(e.name) + ' →</li>' : '<li><button type="button" class="' + (e.kind === 'dir' ? 'trdir' : 'trfile') + '" data-trpath="' + esc(p) + '">' + esc(e.name) + (e.kind === 'dir' ? '/' : '') + '</button></li>';
      }).join('') + '</ul>' + (d.truncated ? '<p class="trnote">檔案太多,只列前面一段</p>' : '');
    } else if (d.too_big) h += '<p class="trnone">檔案太大,不在頁面上顯示(' + Math.round(Number(d.size) / 1024) + ' KB)</p>';
    else if (d.binary) h += '<p class="trnone">不是文字檔</p>';
    else h += '<pre class="trtext">' + esc(d.text) + '</pre>';
    say(h);
  }

  async function show() {
    const d = dialog();
    for (const b of d.querySelectorAll('[data-trtab]')) b.setAttribute('aria-selected', String(b.dataset.trtab === T.tab));
    say('<p class="trnone">讀取中…</p>');
    const want = T.seat + '|' + T.tab + '|' + T.path;
    try {
      if (T.tab === 'log') await showLog();
      else if (T.tab === 'files') await showFiles();
      else await showDiff();
    } catch (e) {
      if (want === T.seat + '|' + T.tab + '|' + T.path) say('<p class="trerr">' + esc(whyText(e)) + '</p>');
    }
  }

  function open(seat, name) {
    T.seat = String(seat || '');
    T.name = String(name || seat || '');
    T.tab = 'diff';
    T.path = '';
    const d = dialog();
    d.querySelector('.trtt').textContent = '@' + T.name + ' 自己的一份';
    d.querySelector('.trsub').textContent = '只讀;你看過會記在事件帳';
    if (!d.open) d.showModal();
    show();
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-tree-seat]');
    if (!b) return;
    // 抓取階段先接:卡片 / 事件列自己的點擊不會再動(不會同時打開事件視窗)
    e.preventDefault();
    e.stopPropagation();
    open(b.dataset.treeSeat, b.dataset.treeName);
  }, true);

  window.AIB_TREE = { open, diffHtml, whyText, treeUrl };
})();
