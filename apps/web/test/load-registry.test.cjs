// §19h:載入清單是動態的 —— 登記一筆假項 ⇒ 彈窗的清單與進度分母都多一項。
// 只抽 ws.js 裡 @load-registry-begin … end 那段(純資料與計算)來跑,不需要瀏覽器。
// 用法:node apps/web/test/load-registry.test.cjs [ws.js 路徑];由 crates/aibd/tests/page_load_registry.rs 呼叫
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const file = process.argv[2] || path.join(__dirname, '..', 'dist', 'ws.js');
const src = fs.readFileSync(file, 'utf8');
const m = /\/\/ @load-registry-begin[^\n]*\n([\s\S]*?)\n\s*\/\/ @load-registry-end/.exec(src);
if (!m) { console.error('FAIL: ws.js 沒有 @load-registry-begin / end 段'); process.exit(1); }
const ctx = { setTimeout, clearTimeout, Promise };
vm.runInNewContext(m[1] + '\nthis.LOAD = LOAD; this.loadReg = loadReg; this.loadPlan = loadPlan; this.loadRun = loadRun;', ctx);
const { LOAD, loadReg, loadPlan, loadRun } = ctx;
let fails = 0;
const ok = (c, msg) => { if (!c) { fails++; console.error('FAIL: ' + msg); } else console.log('ok - ' + msg); };

loadReg({ key: 'a', label: 'A', order: 10 });
loadReg({ key: 'b', label: 'B', order: 20 });
loadReg({ key: 'bg', label: 'BG', order: 30, need: false });
const p0 = loadPlan(LOAD, {});
ok(p0.n === 2 && p0.rows.map((r) => r.key).join() === 'a,b', '只畫必要項,照順序');

ok(loadReg({ key: 'fake', label: '假項', order: 15 }) === true, '登記一筆假項');
const p1 = loadPlan(LOAD, {});
ok(p1.n === p0.n + 1, '進度分母多一項(' + p0.n + ' → ' + p1.n + ')');
ok(p1.rows.map((r) => r.key).join() === 'a,fake,b', '清單多一項、排在它的順序');
ok(loadReg({ key: 'fake', label: '重複' }) === false && loadPlan(LOAD, {}).n === p1.n, '同一個 key 不重複登記');

const p2 = loadPlan(LOAD, { a: { s: 'ok' }, fake: { s: 'cur' } });
ok(p2.ok === 1 && Math.abs(p2.pct - 100 / 3) < 1e-9, '進度 = 完成數 / 必要項數');
ok(p2.glide > p2.pct && p2.glide < (2 / 3) * 100, '進行中的推進量在這一格內');
const p3 = loadPlan(LOAD, { a: { s: 'ok' }, fake: { s: 'ok' }, b: { s: 'ok' } });
ok(p3.ok === p3.n && p3.pct === 100, '必要項都好了 = 100%(背景項不算)');

// 519d0 22:5x:席位與對話是空的也會收(預覽版空艙卡在最後一步那次)
(async () => {
  const L = [];
  const reg = (x) => L.push(Object.assign({ order: 50, need: true, after: [], run: null }, x));
  reg({ key: 'tree', run: async () => ({ nodes: [] }) });
  reg({ key: 'core', run: async () => ({ seats: [], sessions: [] }) });
  reg({ key: 'talk', after: ['core'], run: async () => [] });
  reg({ key: 'none', run: async () => null });
  reg({ key: 'bg', need: false, run: async () => { throw new Error('背景讀不到'); } });
  const st = {};
  await loadRun(L, (k, s) => { st[k] = s; }, { slowMs: 0 });
  ok(['tree', 'core', 'talk', 'none'].every((k) => st[k] === 'ok'), '席位與對話是空的也算載完');
  ok(st.bg === undefined, '背景項出錯不擋、也不進彈窗');
  ok(loadPlan(L, Object.fromEntries(Object.entries(st).map(([k, s]) => [k, { s }]))).pct === 100, '空的全部好了 = 100%');

  const L2 = [];
  const reg2 = (x) => L2.push(Object.assign({ order: 50, need: true, after: [], run: null }, x));
  let order = [];
  reg2({ key: 'core', run: async () => { order.push('core'); } });
  reg2({ key: 'talk', after: ['core'], run: async () => { order.push('talk'); } });
  reg2({ key: 'bad', run: async () => { throw new TypeError('x is null'); } });
  const st2 = {};
  let rejected = false;
  try { await loadRun(L2, (k, s) => { st2[k] = s; }, { slowMs: 0 }); } catch (e) { rejected = e instanceof Error; }
  await new Promise((r) => setTimeout(r, 20)); // 一項出錯會先往外丟,其餘照跑完
  ok(rejected && st2.bad === 'fail', '必要項出錯 ⇒ 標 fail 並往外丟(彈窗寫原因、開場自排重試)');
  ok(order.join() === 'core,talk', 'after 照順序');

  if (fails) { console.error(fails + ' 項失敗'); process.exit(1); }
  console.log('全部通過');
})();
