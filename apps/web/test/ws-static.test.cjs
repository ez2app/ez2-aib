// 043dd 11-20(519d0 ORDER-webonly r2 第 13 項:網頁席沒法開瀏覽器試跑,先把能不開畫面就驗的部分做成測試)。
// 跑法:node apps/web/test/ws-static.test.cjs(crates/aibd/tests/page_static.rs 會跑它)。只讀檔,不開埠、不開瀏覽器。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.join(__dirname, '..', '..', '..');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
const ws = read('apps/web/dist/ws.js');
const treeJs = read('apps/web/dist/tree.js');
const src = (dir) => fs.readdirSync(path.join(root, dir)).filter((f) => f.endsWith('.rs')).map((f) => read(path.join(dir, f))).join('\n');
const rust = src('crates/aibd/src');
let n = 0;
const ok = (c, m) => { assert.ok(c, m); n++; };

// 1. ws.js 能解析(網頁席沒有 node --check,這一條代替)
new vm.Script(ws, { filename: 'ws.js' }); n++;

// 2. 畫出來的 data-* 按鈕都有人接(點了沒反應 = 死按鈕);只拿來標記 / 給 CSS / 給 tree.js 的另列
// dqbox / dqcard / dqprog:頂端「等你決定」給 0eed7 驗收腳本的掛點(1a93b 11-20)
const MARK_ONLY = new Set(['pvid', 'pvtr', 'cls', 'dqbox', 'dqcard', 'dqprog', 'stall', 'rcprun', 'rcpform', 'rcpmsg', 'sendbackwait', 'sendbackmsg', 'trustwait', 'trustmsg', 'actev', 'seatnow', 'seatnowbox']);
const camel = (a) => a.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
const attrs = new Set([...ws.matchAll(/data-([a-z0-9-]+)="/g)].map((m) => m[1]));
for (const a of attrs) {
  if (MARK_ONLY.has(a)) continue;
  const used = [ws, treeJs].some((t) => t.includes('[data-' + a + ']') || t.includes('[data-' + a + '=') || new RegExp('dataset\\.' + camel(a) + '\\b').test(t));
  ok(used, 'reason: dead_button data-' + a);
}

// 3. 頁面送的 act 種類 aibd 都認得;還沒有的要在這裡列名,且頁面要寫明「示意」
const NOT_YET = new Set(['card_set', 'card_note']);
const acts = new Set([...ws.matchAll(/(?:act|fingerAct)\('([a-z_]+)'/g)].map((m) => m[1]));
ok(acts.size >= 20, 'act list read');
for (const t of acts) {
  if (NOT_YET.has(t)) { ok(ws.includes('aibd 還沒有 card_note / card_set'), 'not-yet act is marked 示意: ' + t); continue; }
  // 7b81e feat/trust-t1(1b6ce17d10)才有;沒進全合頭前頁面收到 unknown_act_type 就收起鈕。那片合進來後拿掉這行
  // 7ebca feat/receipt-check-7ebca-1120(4758a4702b / 62153be32a)才有 receipt_*;同上,合進來後拿掉
  if (/^receipt_(request|read|withdraw|list)$/.test(t) && !rust.includes('"' + t + '"')) { ok(ws.includes('7ebca 4758a4702b'), 'branch-only act is marked: ' + t); continue; }
  // a9136 feat/web-merge2-a9136-1120(bd4d0c267f)才有 seat_send_back;同上,合進來後拿掉
  if (t === 'seat_send_back' && !rust.includes('"' + t + '"')) { ok(ws.includes('a9136 IFACE-web-merge r1'), 'branch-only act is marked: ' + t); continue; }
  if ((t === 'seat_trust' || t === 'seat_untrust') && !rust.includes('"' + t + '"')) { ok(ws.includes('aibd 7b81e feat/trust-t1 才有 seat_trust / seat_untrust'), 'branch-only act is marked: ' + t); continue; }
  ok(rust.includes('"' + t + '"'), 'reason: act_unknown_to_aibd ' + t);
}

// 4. 登記倉 / 開在倉:aibd 會回的每個拒絕原因,頁面都有白話
const body = (ws.split('const REPO_WHY = {')[1] || '').split('\n  };')[0];
const why = new Set([...body.matchAll(/^\s+([a-z_]+):/gm)].map((m) => m[1]));
const codes = new Set([...(read('crates/aibd/src/repos.rs') + read('crates/aibd/src/seat_worktree.rs')).matchAll(/(?:Err\(|\|_\| )"([a-z_]+)"\)/g)].map((m) => m[1]));
ok(codes.size >= 10, 'repo refusal codes read');
for (const c of codes) ok(why.has(c), 'reason: repo_refusal_without_words ' + c);
for (const c of ['owner_check_unavailable', 'owner_check_not_approved', 'preview_no_dgrs']) ok(why.has(c) && rust.includes('"' + c + '"'), 'fingerprint / preview refusal in words: ' + c);

// 5. 等指紋:202 帶 ticket 才等、帶同一張單重送、2 分鐘停
const fa = (ws.split('async function fingerAct(')[1] || '').split('\n  }\n')[0];
ok(/__status === 202 && r\.ticket/.test(fa), 'waits only on 202 with a ticket');
// a9136 IFACE-web-merge r1 §4:送回倉重送要多帶第一步的 plan.head ⇒ extra(first) 的欄位;沒給 extra 就是空的,其他照舊同一份
ok(/Object\.assign\(\{\}, body, more, \{ ticket \}\)/.test(fa) && /more = extra \? extra\(first\) : \{\}/.test(fa), 'resends the same body with the ticket');
ok(/owner_check_not_approved/.test(fa) && /120000/.test(fa), 'not approved yet keeps waiting, up to 2 minutes');
ok(/\/api\/v1\/owner\/tickets\//.test(fa) && /'denied'/.test(fa) && /'expired'/.test(fa) && /probe = false/.test(fa), 'asks the ticket state when aibd has it (7b81e G2), else falls back to resending');

// 6. 做事席帶倉開的時候 aibd 不問指紋(7b81e 986bce061 asked_now = mode != work)⇒「在哪裡改」對做事席不能說要按指紋
if (/let asked_now = req\.mode != "work";/.test(read('crates/aibd/src/seats.rs'))) ok(/A\.mode === 'work' \? '開的時候不用按指紋/.test(ws), 'reason: work_repo_says_fingerprint');

console.log('ws-static: ' + n + ' 項全部通過');
