// b4297 11-20 第二輪:tree.js(一席自己那一份給真人看)的純函式 + 頁面接線。
// 跑法:node apps/web/test/tree.test.cjs(crates/aibd/tests/page_tree.rs 會跑它)
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const dist = path.join(__dirname, '..', 'dist');
const read = (f) => fs.readFileSync(path.join(dist, f), 'utf8');
const src = read('tree.js');

const listeners = [];
const win = {};
const ctx = {
  window: win,
  document: { addEventListener: (type, fn, cap) => listeners.push({ type, fn, cap }) },
  sessionStorage: { getItem: () => '' },
  encodeURIComponent,
  String, Number, Math, Error, Object, Array,
};
vm.createContext(ctx);
vm.runInContext(src, ctx, { filename: 'tree.js' });
const T = win.AIB_TREE;
let n = 0;
const ok = (c, m) => { assert.ok(c, m); n++; };

// 1. unified diff 上色只靠 class,內容一律跳脫(席寫進檔案的東西不能變成頁面的 HTML)
const h = T.diffHtml('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-<b>old</b>\n+<img src=x onerror=alert(1)>\n same');
ok(h.includes('<span class="trf">diff --git a/x b/x</span>'), 'file header class');
ok(h.includes('<span class="trm">--- a/x</span>') && h.includes('<span class="trm">+++ b/x</span>'), 'old / new file lines are not counted as - / +');
ok(h.includes('<span class="trh">@@ -1 +1 @@</span>'), 'hunk header class');
ok(h.includes('<span class="trd">-&lt;b&gt;old&lt;/b&gt;</span>'), 'removed line escaped');
ok(h.includes('<span class="tra">+&lt;img src=x onerror=alert(1)&gt;</span>'), 'added line escaped');
ok(!/<img|<b>/.test(h), 'reason: diff_html_not_escaped');
ok(h.endsWith('\n same'), 'context line plain');

// 2. 讀不到時說人話,不是代碼
ok(T.whyText({ reason: 'not_reviewer' }).includes('審查'), 'not_reviewer in words');
ok(T.whyText({ status: 404 }).includes('沒有自己的一份'), '404 without a reason is no_tree');
ok(T.whyText({ reason: 'tree_built_in_page_only' }).includes('內建'), 'built-in page only');

// 3. 席碼進網址要編碼(不能拼出別的路徑)
ok(T.treeUrl('seat_ab/../x') === '/api/v1/seats/seat_ab%2F..%2Fx/tree', 'reason: seat_not_encoded');

// 4. 只接一個點擊,抓取階段(卡片 / 事件列自己的點擊不再動)
ok(listeners.length === 1 && listeners[0].type === 'click' && listeners[0].cap === true, 'reason: click_not_capture');

// 5. 頁面 CSP 是 style-src 'self':tree.js 不寫行內樣式;樣式在 tree.css,index.html 兩個都載
ok(!/style=|\.style\./.test(src), 'reason: inline_style');
const idx = read('index.html');
ok(idx.includes('<script src="tree.js" defer></script>') && idx.includes('<link rel="stylesheet" href="tree.css">'), 'reason: tree_not_loaded');

// 6. ws.js 有兩個入口:留著的樹那張卡、右欄席列
const ws = read('ws.js');
ok(/if \(x\.tree\) b\.push\(`<button type="button" class="k" data-tree-seat=/.test(ws), 'reason: card_has_no_tree_button');
ok((ws.match(/\$\{treeBtn\(s\)\}/g) || []).length === 2, 'reason: strip_has_no_tree_button');

console.log('tree.js ' + n + ' 項全部通過');
