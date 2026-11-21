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
const MARK_ONLY = new Set(['pvid', 'pvtr', 'cls', 'dqbox', 'dqcard', 'dqprog', 'stall', 'rcprun', 'rcpform', 'rcpmsg', 'sendbackwait', 'sendbackmsg', 'trustwait', 'trustmsg', 'actev', 'seatnow', 'seatnowbox', 'pushbox', 'pushwait', 'pushmsg', 'prform', 'prwait', 'prbody', 'top3', 'ghbox', 'ghpr', 'ghchecks', 'ghrun-out', 'hostate', 'cardaddform', 'caf', 'ghnum']);
const camel = (a) => a.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
const attrs = new Set([...ws.matchAll(/data-([a-z0-9-]+)="/g)].map((m) => m[1]));
for (const a of attrs) {
  if (MARK_ONLY.has(a)) continue;
  const used = [ws, treeJs].some((t) => t.includes('[data-' + a + ']') || t.includes('[data-' + a + '=') || new RegExp('dataset\\.' + camel(a) + '\\b').test(t));
  ok(used, 'reason: dead_button data-' + a);
}

// 3. 頁面送的 act 種類 aibd 都認得;還沒有的要在這裡列名,且頁面要寫明「示意」
const NOT_YET = new Set([]); // 1a93b d756e6d3b3:card_* 改送 node_add / node_set,沒有還不認得的了
const acts = new Set([...ws.matchAll(/(?:act|fingerAct)\('([a-z_]+)'/g)].map((m) => m[1]));
ok(acts.size >= 20, 'act list read');
for (const t of acts) {
  if (NOT_YET.has(t)) { ok(false, 'reason: not_yet_list_is_empty ' + t); continue; }
  ok(!/^card_(add|note|set)$/.test(t), 'reason: card_act_instead_of_node ' + t);
  // 7b81e feat/trust-t1(1b6ce17d10)才有;沒進全合頭前頁面收到 unknown_act_type 就收起鈕。那片合進來後拿掉這行
  // 7b81e feat/gh-view-7b81e-1120(1dae7efafe)才有 gh_view;card_add 跟 card_note / card_set 一樣 aibd 都還沒有(預覽才畫)。合進來後拿掉
  if (t === 'gh_view' && !rust.includes('"gh_view"')) { ok(ws.includes('7b81e 1dae7efafe'), 'branch-only act is marked: ' + t); continue; }
  // 第 5 件:repo_push 在 7b81e 000f98afc8、pr_open 在 4de2bce5d2;頁面收到 unknown_act_type 就收鈕寫原因。進來後拿掉這行
  if ((t === 'repo_push' || t === 'pr_open') && !rust.includes('"' + t + '"')) { ok(ws.includes('7b81e 4de2bce5d2'), 'branch-only act is marked: ' + t); continue; }
  // 7ebca feat/receipt-check-7ebca-1120(4758a4702b / 62153be32a)才有 receipt_*;同上,合進來後拿掉
  if (/^receipt_(request|read|withdraw|list)$/.test(t) && !rust.includes('"' + t + '"')) { ok(ws.includes('7ebca 4758a4702b'), 'branch-only act is marked: ' + t); continue; }
  // a9136 feat/web-merge2-a9136-1120(bd4d0c267f)才有 seat_send_back;同上,合進來後拿掉
  if (t === 'seat_send_back' && !rust.includes('"' + t + '"')) { ok(ws.includes('a9136 IFACE-web-merge r1'), 'branch-only act is marked: ' + t); continue; }
  if ((t === 'seat_trust' || t === 'seat_untrust') && !rust.includes('"' + t + '"')) { ok(ws.includes('aibd 7b81e feat/trust-t1 才有 seat_trust / seat_untrust'), 'branch-only act is marked: ' + t); continue; }
  // 1a93b feat/aibd-page-acts-1a93b-1120(62e1bdbeb8)才有 seat_clear;頁面看 version.acts 沒有就灰掉寫「要新版 aibd」。合進來後拿掉這行
  if (t === 'seat_clear' && !rust.includes('"' + t + '"')) { ok(ws.includes('1a93b 62e1bdbeb8') && ws.includes("hasAct('seat_clear') !== true"), 'branch-only act is marked: ' + t); continue; }
  // b4297 feat/add-test-host-b4297-1121(62fd9f9690)才有 test_host_*;頁面看 version.acts 沒有就不畫。合進來後拿掉這行
  if (/^test_host_(list|add|remove)$/.test(t) && !rust.includes('"' + t + '"')) { ok(ws.includes('b4297 CARD-add-test-host r0(62fd9f9690)') && ws.includes("hasAct('test_host_list') !== true"), 'branch-only act is marked: ' + t); continue; }
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

// 7. 每個控制項都要有 class(同 crates/aibd/tests/page_controls.rs;那支要 cargo,這裡本機就抓得到。igt1 收據 rcpsc 兩個 radio 漏了)
{
  const openTag = (at) => { let d = 0; for (let i = at + 1; i < ws.length; i++) { const c = ws[i]; if (c === '$' && ws[i + 1] === '{') { d++; i++; } else if (c === '}' && d > 0) d--; else if (c === '>' && d === 0) return ws.slice(at, i + 1); } return ws.slice(at); };
  const bad = [];
  for (const tag of ['button', 'select', 'textarea', 'details', 'summary', 'input']) {
    for (let from = 0, at; (at = ws.indexOf('<' + tag, from)) >= 0; from = at + tag.length + 1) {
      if (/[A-Za-z0-9_-]/.test(ws[at + tag.length + 1] || ' ')) continue;
      const t = openTag(at);
      if (t.includes('type="hidden"') || t.includes('class="')) continue;
      bad.push('ws.js:' + (ws.slice(0, at).split('\n').length) + ' ' + t.slice(0, 80));
    }
  }
  ok(!bad.length, 'reason: control_without_class\n' + bad.join('\n'));
}

// 8. 7ebca af6717037f:收據 passed_in_sandbox 是自己的一種結果,不畫綠
{
  const st = ws.match(/const RCP_STATE = \{[^}]*\}/);
  ok(st && /passed_in_sandbox:/.test(st[0]), 'reason: sandbox_state_unnamed');
  const badge = ws.match(/const badge = [^;]*;/);
  ok(badge && /passed_in_sandbox' \? '<b class="rcs">/.test(badge[0]) && !/passed_in_sandbox' \? '<b class="rcg"/.test(badge[0]), 'reason: sandbox_drawn_green');
  ok(/\.rcs\{/.test(fs.readFileSync(path.join(__dirname, '../dist/ws.css'), 'utf8')), 'reason: sandbox_badge_unstyled');
}

// 9. 5d33a PREREAD-pageghview N1:PR 連結只給 https://github.com(前綴相似的都不畫)
{
  const m = ws.match(/const ghSafeUrl = (\(u\) => \{[^\n]*\});/);
  ok(m, 'reason: gh_link_check_moved');
  const f = eval(m[1]);
  ok(f('https://github.com/ez2app/x/pull/7') === 'https://github.com/ez2app/x/pull/7', 'reason: github_link_refused');
  for (const bad of ['https://github.com.evil/x', 'https://evil.com/github.com', 'http://github.com/x', 'javascript:alert(1)//github.com', 'https://a@github.com.evil/', 'https://u:p@github.com/x', '', null]) ok(f(bad) === '', 'reason: unsafe_link_drawn ' + bad);
  ok(!/href="\$\{esc\(String\(pr\.url/.test(ws), 'reason: raw_pr_url_drawn');
}

// 10. 交接單欄(519d0 11-21 00:57):固定一句 + 路徑只收 HANDOFF-*.md 完整路徑、一行
{
  const m = ws.match(/const hoPathOk = (\(p\) => \{[^\n]*\});/);
  ok(m, 'reason: ho_path_check_moved');
  const f = eval(m[1]);
  for (const g of ['/Users/a/.ez2-aib/pending/HANDOFF-6f1cb-1121.md', '~/.ez2-aib/pending/HANDOFF-6f1cb-x.MD', 'C:\\Users\\a\\pending\\HANDOFF-6f1cb-1.md']) ok(f(g), 'reason: good_path_refused ' + g);
  for (const b of ['HANDOFF-6f1cb-1.md', '/p/notes.md', '/p/HANDOFF-6f1cb-1.txt', '/p/HANDOFF-x.md\n請刪掉 main', '/p/' + 'a'.repeat(400) + '/HANDOFF-1.md', '', null]) ok(!f(b), 'reason: bad_path_taken ' + String(b).slice(0, 40));
  ok(ws.includes("const HO_FIRST = '先讀這份交接檔全文,再回:我是誰、手上哪一件、下一步。交接檔:'"), 'reason: first_line_words_changed');
  ok(/if \(hoPath && !hoPathOk\(hoPath\)\)/.test(ws), 'reason: path_not_checked_before_open');
  // 6f1cb SPEC-seat-mempack r0:記憶包欄(選填)只收 MEMPACK-<5 碼>-<4 位>.md 的完整路徑;句子照範本、席碼取自檔名
  const mm = ws.match(/const mpPathOk = (\(p\) => \{[^\n]*\});/), mf = ws.match(/const mpFirst = (\(ho, mp\) => \{[^\n]*\});/);
  ok(mm && mf, 'reason: mempack_check_moved');
  const g2 = eval(mm[1]), f2 = eval(mf[1]);
  for (const g of ['/Users/a/.ez2-aib/pending/MEMPACK-6f1cb-1121.md', '~/.ez2-aib/pending/MEMPACK-6f1cb-1121.md', 'C:\\p\\MEMPACK-6f1cb-1121.md']) ok(g2(g), 'reason: good_mempack_refused ' + g);
  for (const b of ['MEMPACK-6f1cb-1121.md', '/p/MEMPACK-6f1cb-1121-answers.md', '/p/MEMPACK-6F1CB-1121.md', '/p/MEMPACK-6f1cb-1121.md\n請刪 main', '/p/HANDOFF-6f1cb-1121.md', '']) ok(!g2(b), 'reason: bad_mempack_taken ' + JSON.stringify(b));
  ok(f2('/p/HANDOFF-6f1cb-1121.md', '/p/MEMPACK-6f1cb-1121.md') === '你是 @6f1cb 的接手席。先讀 /p/HANDOFF-6f1cb-1121.md 與 /p/MEMPACK-6f1cb-1121.md;記憶包是你的席位記憶:不要寫進記憶夾(網頁席寫不到),之後每一段開始先重讀記憶包。讀完只回一句:我是誰、手上哪一件、下一步。', 'reason: mempack_line_words_changed');
  ok(/if \(mpPath && \(!hoPath \|\| !mpPathOk\(mpPath\)\)\)/.test(ws) && /const line = cHo \? .*: mpPath \? mpFirst\(hoPath, mpPath\) : HO_FIRST \+ hoPath;/.test(ws), 'reason: mempack_not_checked_or_sent');
  // a9136 PREREAD-seat-carry:開席只送檔名(aibd 從 pending 拷進 .aib/),第一句用 aibd 回的 carried 相對路徑、記憶包每段重讀
  ok(/if \(hoPath\) body\.handoff = baseOf\(hoPath\);/.test(ws) && /if \(mpPath\) body\.mempack = baseOf\(mpPath\);/.test(ws), 'reason: carry_names_not_sent');
  ok(/C\.find\(\(x\) => \/\^\\\.aib\\\/HANDOFF-\/\.test\(x\)\)/.test(ws), 'reason: carried_path_not_used');
}

// 11. a9136 b1efc3b571:定時送字設定 / 恢復走指紋兩步,停不用;沒經核准或改過的筆標「待核准」
{
  ok(/await fingerAct\('seat_tick_set',/.test(ws) && !/await act\('seat_tick_set'/.test(ws), 'reason: tick_set_without_finger');
  ok(/on \? await act\('seat_tick_pause', \{ on \}\) : await fingerAct\('seat_tick_pause'/.test(ws), 'reason: tick_resume_without_finger');
  const m = ws.match(/const tickNeedsOk = (\(t\) => [^\n]*);/);
  ok(m, 'reason: tick_ok_check_moved');
  const f = eval(m[1]);
  ok(f({ approved: false }) && f({ approved: true, changed_since_approved: true }) && !f({ approved: true, changed_since_approved: false }) && !f({}) && !f(null), 'reason: unapproved_tick_not_marked');
  // a9136 54b400fdef:not_sent 不是 null ⇒ 待核准
  ok(f({ approved: true, not_sent: 'expired' }) && f({ not_sent: 'bad_signature' }) && !f({ approved: true, signed: true, not_sent: null }), 'reason: not_sent_not_marked');
}

// 12. a9136 64a09bb2af 片 2:檔名只收 a-z 0-9 - _ .txt;從檔案的筆標未簽章;卡住 3 輪以上標出
{
  const m = ws.match(/const TICK_FILE_RE = (\/[^\n]*\/);/);
  ok(m, 'reason: tick_file_check_moved');
  const re = eval(m[1]);
  ok(re.test('loop.txt') && re.test('next_round-2.txt'), 'reason: good_tick_file_refused');
  for (const b of ['Loop.txt', '../x.txt', 'a/b.txt', 'x.md', '.txt', 'a b.txt']) ok(!re.test(b), 'reason: bad_tick_file_taken ' + b);
  ok(/if \(t\.file && !t\.signed\) return/.test(ws), 'reason: unsigned_file_not_marked');
  ok(/Number\(t\.stuck\) >= 3\) return/.test(ws), 'reason: stuck_not_marked');
  ok(ws.includes("e.type === 'seat.tick_stuck'"), 'reason: stuck_event_not_read');
}

// 13. 519d0 06:45 第 3、4 點:頂端「等你決定」件數讀右欄同一份數;「在想…」以 aibd 回合狀態(usage turn_started_at)收
{
  ok(/const all = Math\.max\(n, evCount\(1, evByCls\(\)\)\)/.test(ws) && /<span class="dqn">\$\{all\}<\/span>/.test(ws), 'reason: decide_counts_apart');
  ok(/if \(u && 'turn_started_at' in u\) return \{ running: !!\(u\.turn_started_at && live\)/.test(ws), 'reason: thinking_not_by_turn_state');
  ok(/if \(!start && j && !\/\^\(assistant\|stream_event\|user\)\$\/\.test/.test(ws), 'reason: hook_line_starts_turn');
}

// 14. 519d0 06:45 第 1、2 點:加一席看得到、改得了品牌預設模型(只在 aibd 有 default_model_set 時);額度卡有「換模型」(seat_model_set)
{
  const m = ws.match(/const MODEL_RE = (\/[^\n]*\/);/);
  ok(m, 'reason: model_check_moved');
  const re = eval(m[1]);
  for (const g of ['sonnet', 'claude-sonnet-4-5', 'gpt-5.5', '~opus', 'a/b:c']) ok(re.test(g), 'reason: good_model_refused ' + g);
  for (const b of ['--dangerously-skip-permissions', 'a b', '', '-x', 'a'.repeat(65), 'a;b']) ok(!re.test(b), 'reason: bad_model_taken ' + b);
  ok(/if \(hasAct\("default_model_set"\) !== true\) return "";/.test(ws), 'reason: default_model_shown_without_act');
  ok(/\$\{modelLine\(A, pv\)\}/.test(ws), 'reason: default_model_not_in_add');
  ok(/await act\("default_model_set", \{ provider: pv\.id, model: v \}\)/.test(ws), 'reason: default_model_not_sent');
  ok(/quota: lv === 3 && str\(c\.why\) === '額度'/.test(ws), 'reason: quota_card_not_read');
  ok(/if \(x\.quota && hasAct\('seat_model_set'\) === true\)/.test(ws), 'reason: swap_shown_without_act');
  ok(/await act\("seat_model_set", \{ seat_id: M\.seat, model: v, idem_key: idem\(\) \}\)/.test(ws), 'reason: swap_not_sent');
}

// 15. b4297 CARD-add-test-host r0:測試機區塊只在 aibd 有 test_host_list 時畫;加 / 收回走指紋兩段;why 代碼都有人話
{
  ok(/if \(hasAct\('test_host_list'\) !== true\) return '';/.test(ws), 'reason: test_host_without_act');
  ok(/await fingerAct\(kind === 'add' \? 'test_host_add' : 'test_host_remove', \{ name \}/.test(ws) && !/act\('test_host_add'/.test(ws), 'reason: test_host_without_finger');
  for (const w of ['reached_through_something_else', 'config_has_match_or_include', 'no_identity_file', 'identity_file_not_absolute', 'value_with_a_token', 'port', 'aibd_own_key']) ok(new RegExp('\\b' + w + ': ').test(ws), 'reason: why_without_words ' + w);
  for (const c of ['no_receipt_gate', 'not_a_mac', 'owner_login_failed', 'write_failed', 'remove_failed', 'page_only']) ok(new RegExp('\\b' + c + ': ').test(ws), 'reason: error_without_words ' + c);
  ok(/r\.reason === 'gate_did_not_answer'/.test(ws), 'reason: gate_silence_not_shown');
  ok(/const half = 'half' in m \? m\.half === true :/.test(ws), 'reason: half_field_not_read');
}

// 16. 519d0 10:07(Andy 09:4x–10:05 在預覽撞到的四處)
{
  ok(ws.includes("const SB_NAME = '交回本機原倉(新分支)'") && !ws.includes('>送回倉(建新分支)<'), 'reason: send_back_still_named_vaguely');
  ok(/main 沒動,也還沒上 GitHub/.test(ws), 'reason: send_back_done_without_main_untouched');
  ok(/if \(sbb && running\(sbb\.dataset\.sendback\)\)/.test(ws), 'reason: running_seat_send_back_silent');
  ok(/if \(S\.sbMsg && S\.sbMsg\.bad\) say\(S\.sbMsg\.text, 'err'\)/.test(ws), 'reason: send_back_error_only_in_seat_box');
  ok(/codex: '終端機打 codex login'/.test(ws) && !/例:claude 打 \/login/.test(ws), 'reason: codex_told_claude_login');
  ok(/data-addpv="1">去預覽開席</.test(ws), 'reason: mother_page_plus_no_preview_way');
  ok(/回了失敗碼/.test(ws), 'reason: nonzero_exit_marked_error');
  ok(/function repoTag\(s\)/.test(ws) && /\$\{repoTag\(s\)\}/.test(ws), 'reason: seat_card_without_repo_tag');
}

// 17. Andy 10:26 截圖:「各席現在」兩條橫線 = 列內 <b class="w"> 吃到流程站點的 .w::before(position:absolute、left:-50%、width:100%)
{
  ok(!/<b class="w">/.test(ws), 'reason: seat_now_word_takes_station_line');
  const css = fs.readFileSync(path.join(__dirname, '../dist/ws.css'), 'utf8');
  ok(/\.w::before\{/.test(css) && [...ws.matchAll(/<(?:b|span|i|em|small) class="w[ "]/g)].length === 0, 'reason: inline_w_class_besides_station');
}

// 18. 6f1cb FINDING-cutover r0:第一句 409 不算失敗、延後重試;不叫網頁席寫記憶夾
{
  ok(/if \(e && e\.status === 409\) f\.next = Date\.now\(\) \+ 10000; else f\.tries\+\+;/.test(ws), 'reason: first_line_409_counts_as_failure');
  ok(!/寫進你自己的記憶夾/.test(ws), 'reason: web_seat_told_to_write_memory_dir');
}

// 19. a9136 da2520fac:拷交接檔的三個 reason 有人話
for (const r of ['handoff', 'mempack', 'carry']) ok(ws.includes("c === 'invalid_request' && why === '" + r + "'"), 'reason: carry_error_without_words ' + r);

console.log('ws-static: ' + n + ' 項全部通過');
