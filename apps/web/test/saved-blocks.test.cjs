// c5d88 PLAN-skipstop F2 / H1b:工作台上的「⏱ 節省時間」與「外掛」兩個區塊。
// 把 ws.js 裡這一段原樣取出來,在 node 裡用假的 act / S 執行,看它對各種回答畫出什麼:
// 沒有 act 不畫、讀不到說人話不畫 0、畫的是牆鐘、外掛名字與說明會跳脫、開關檔讀不懂時沒有開關鈕、按鈕送出的只有名字與開關。
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');
const ws = fs.readFileSync(path.join(__dirname, '../dist/ws.js'), 'utf8');
let n = 0;
const ok = (c, why) => { n++; assert.ok(c, why); };

const from = ws.indexOf('  // c5d88 PLAN-skipstop F2:省時雙系統的五格');
const to = ws.indexOf('  async function thLoad() {');
ok(from > 0 && to > from, 'reason: section_moved');
const section = ws.slice(from, to);

function world(acts, answers) {
  const calls = [];
  const sent = { S: {}, renders: 0, calls };
  const ctx = {
    S: sent.S,
    hasAct: (t) => (acts === null ? null : acts.includes(t)), // null:還不知道 aibd 有哪些 act(ws.js 的 hasAct 同)
    act: async (type, f) => { calls.push([type, f]); const a = answers[type]; if (a instanceof Error) throw a; return typeof a === 'function' ? a(f) : a; },
    esc: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    errText: (e) => 'ERR ' + (e && e.message),
    render: () => { sent.renders++; },
    setTimeout: (f) => { sent.later = f; },
    Date, Math, Number, String, Array, Object,
  };
  vm.createContext(ctx);
  vm.runInContext(section + '\n;this.api = { tsLoad, timeSavedHtml, chatSavedHtml, plgLoad, plgSet, pluginsHtml, tsDur };', ctx);
  sent.api = ctx.api;
  return sent;
}
const sum = { confirmed_ms: 0, estimated_ms: 0, penalty_ms: 0, count: 0 };
const box = (clock, unknown) => ({ clock_ms: clock, clock_unknown_runs: unknown || 0, skip_clock_ms: clock, total: sum, skip: sum, stop: sum });
const saved = { state: 'ok', saved: { v: 1, boxes: { today: box(103000), week: box(3723000, 2), month: box(0), year: box(0), all: box(0) }, since: null } };

(async () => {
  // ── 節省時間 ──
  let w = world([], {});
  ok(w.api.timeSavedHtml() === '' && w.api.pluginsHtml() === '', 'reason: drawn_without_the_act');
  w = world(null, {});
  ok(w.api.timeSavedHtml() === '' && w.api.pluginsHtml() === '' && w.later === undefined, 'reason: drawn_before_the_acts_are_known');
  await w.api.tsLoad(); await w.api.plgLoad();
  ok(w.calls.length === 0, 'reason: asked_before_the_acts_are_known');

  w = world(['time_saved'], { time_saved: saved });
  let h = w.api.timeSavedHtml();
  ok(h.includes('讀取中') && typeof w.later === 'function', 'reason: no_first_read');
  await w.api.tsLoad();
  ok(w.calls.length === 1 && w.calls[0][0] === 'time_saved' && typeof w.calls[0][1].utc_offset_min === 'number' && Object.keys(w.calls[0][1]).length === 1, 'reason: time_saved_asks_more_than_the_clock');
  h = w.api.timeSavedHtml();
  ok(h.includes('<b>1 分 43 秒</b>') && h.includes('<b>1 小時 2 分</b>') && h.includes('另 2 次未計'), 'reason: boxes_not_the_clock\n' + h);
  ok((h.match(/class="tsx"/g) || []).length === 5 && h.includes('href="saved.html"'), 'reason: not_five_boxes_or_no_board_link');
  ok(w.api.tsDur(59999) === '59 秒' && w.api.tsDur(3600000) === '1 小時 0 分', 'reason: duration_words');

  const SAYS = { not_installed: '還沒裝', off: '已關閉', not_runnable: '不能跑', unreadable: '設定檔讀不懂', no_answer: '沒有回答', bad_answer: '回答讀不懂', something_new: '讀不到省時數字' };
  for (const st of Object.keys(SAYS)) {
    w = world(['time_saved'], { time_saved: { state: st } });
    await w.api.tsLoad();
    h = w.api.timeSavedHtml();
    ok(h.includes(SAYS[st]), 'reason: state_without_its_own_words ' + st + '\n' + h);
    ok(!h.includes('class="tsx"') && !/\d 秒/.test(h) && /省時/.test(h), 'reason: boxes_or_zeros_without_an_answer ' + st + '\n' + h);
  }
  // 少一個數字:整份不畫
  const holed = JSON.parse(JSON.stringify(saved)); delete holed.saved.boxes.year.clock_ms;
  w = world(['time_saved'], { time_saved: holed }); await w.api.tsLoad();
  ok(!w.api.timeSavedHtml().includes('class="tsx"'), 'reason: a_missing_number_is_drawn');
  // 連不上
  w = world(['time_saved'], { time_saved: new Error('boom') }); await w.api.tsLoad();
  ok(w.api.timeSavedHtml().includes('ERR boom') && !w.api.timeSavedHtml().includes('class="tsx"'), 'reason: an_error_is_drawn_as_boxes');

  // ── 外掛 ──
  const list = (over) => Object.assign({ state: 'ok', switches_readable: true, stray: [], plugins: [
    { name: 'aib-skipstop', version: '0.3.0', summary: 'Skip <b>what</b> need not run', paid: false, default_on: true, on: true, runs: 'ok' },
    { name: 'other', version: '1"2', summary: '', paid: true, default_on: false, on: false, runs: 'off' },
    { folder: 'bro<ken', problem: 'not_understood' },
    { name: 'changed', version: '1', summary: '', paid: false, default_on: true, on: true, runs: 'sum_differs' },
  ] }, over);
  w = world(['plugin_list', 'plugin_set'], { plugin_list: list(), plugin_set: (f) => list({ echoed: f }) });
  await w.api.plgLoad();
  h = w.api.pluginsHtml();
  ok(h.includes('Skip &lt;b&gt;what&lt;/b&gt;') && !h.includes('<b>what</b>') && h.includes('bro&lt;ken') && h.includes('1&quot;2'), 'reason: plugin_text_not_escaped\n' + h);
  ok(h.includes('data-plgset="aib-skipstop" data-plgon="0"') && h.includes('>關閉</button>') && h.includes('data-plgset="other" data-plgon="1"') && h.includes('>打開</button>'), 'reason: switch_buttons_wrong\n' + h);
  ok(h.includes('✅ 開著') && h.includes('⚠️ 執行檔和安裝時登記的不一樣') && h.includes('❌ 說明檔讀不懂') && h.includes('付費'), 'reason: plugin_states_without_words\n' + h);
  await w.api.plgSet('other', true);
  const sent = w.calls.find((c) => c[0] === 'plugin_set');
  ok(sent && JSON.stringify(sent[1]) === JSON.stringify({ name: 'other', on: true }), 'reason: plugin_set_sends_more_than_name_and_on');
  ok(w.S.ts === null, 'reason: saved_numbers_not_reread_after_a_switch');

  // 沒有 plugin_set(舊 aibd)或開關檔讀不懂(on 是 null):沒有開關鈕
  w = world(['plugin_list'], { plugin_list: list() }); await w.api.plgLoad();
  ok(!w.api.pluginsHtml().includes('data-plgset'), 'reason: switch_shown_without_plugin_set');
  const unread = list({ switches_readable: false }); unread.plugins.forEach((p) => { if ('on' in p) { p.on = null; p.runs = 'switches_unread'; } });
  w = world(['plugin_list', 'plugin_set'], { plugin_list: unread }); await w.api.plgLoad();
  h = w.api.pluginsHtml();
  ok(!h.includes('data-plgset') && h.includes('開關檔讀不懂'), 'reason: switch_shown_when_it_cannot_be_moved\n' + h);
  // 沒裝、讀不到、被拒
  w = world(['plugin_list'], { plugin_list: list({ plugins: [] }) }); await w.api.plgLoad();
  ok(w.api.pluginsHtml().includes('還沒有裝任何外掛'), 'reason: empty_area_without_words');
  w = world(['plugin_list'], { plugin_list: { state: 'unreadable' } }); await w.api.plgLoad();
  ok(w.api.pluginsHtml().includes('外掛區讀不到'), 'reason: unreadable_area_without_words');
  const refused = Object.assign(new Error('x'), { code: 'page_only' });
  w = world(['plugin_list', 'plugin_set'], { plugin_list: list(), plugin_set: refused }); await w.api.plgLoad();
  await w.api.plgSet('other', true);
  h = w.api.pluginsHtml();
  ok(h.includes('只有這台 AIB 的內建頁能開關外掛') && h.includes('data-plgset="other"'), 'reason: a_refused_switch_is_silent_or_loses_the_list\n' + h);

  // ── 對話 ⏱ 條與「節省時間」篩選 ──
  w = world(['other'], {});
  ok(w.api.chatSavedHtml() === '', 'reason: chat_saved_shown_without_act');
  w = world(['time_saved'], { time_saved: { state: 'off' } });
  await w.api.tsLoad();
  ok(w.api.chatSavedHtml() === '', 'reason: chat_saved_shown_when_not_ok');

  const withRecent = {
    state: 'ok',
    saved: {
      v: 1,
      boxes: { today: box(103000), week: box(103000), month: box(0), year: box(0), all: box(0) },
      since: null,
      recent: [
        { at: '2026-11-23T06:00:00Z', system: 'precheck', confirmed: true, saved_ms: 60000, avoided: 'cargo <script>test</script>', basis: 'median<img>' },
        { at: '2026-11-23T05:59:00Z', system: 'shadow', confirmed: false, saved_ms: 43000, avoided: 'aib-ledger', basis: 'est' },
      ],
    },
  };
  w = world(['time_saved'], { time_saved: withRecent });
  await w.api.tsLoad();
  ok(w.api.chatSavedHtml().includes('今天省 1 分 43 秒') && !w.api.chatSavedHtml().includes('tschrow'), 'reason: chat_saved_bar_closed_wrong\n' + w.api.chatSavedHtml());
  w.S.tsChatOpen = true;
  h = w.api.chatSavedHtml();
  ok(h.includes('預檢省時 1') && h.includes('影子續跑 1') && h.includes('cargo &lt;script&gt;test&lt;/script&gt;') && h.includes('median&lt;img&gt;') && !h.includes('<script>') && !h.includes('<img>'), 'reason: chat_saved_open_or_escape\n' + h);
  ok(h.includes('已確認') && h.includes('預估') && h.includes('省下 1 分 0 秒') && h.includes('省下約 43 秒'), 'reason: chat_saved_tags\n' + h);
  w.S.tsChatFilter = 'shadow';
  h = w.api.chatSavedHtml();
  ok(!h.includes('cargo &lt;script&gt;') && h.includes('aib-ledger'), 'reason: chat_saved_filter_shadow\n' + h);
  w.S.tsChatFilter = 'precheck';
  h = w.api.chatSavedHtml();
  ok(h.includes('cargo &lt;script&gt;') && !h.includes('aib-ledger'), 'reason: chat_saved_filter_precheck\n' + h);
  w.S.tsChatFilter = 'all';
  h = w.api.chatSavedHtml();
  ok(h.includes('cargo &lt;script&gt;') && h.includes('aib-ledger'), 'reason: chat_saved_filter_all\n' + h);

  // stale filter falls back to all; empty recent with todayMs > 0 keeps board link
  const noPrecheck = JSON.parse(JSON.stringify(withRecent));
  noPrecheck.saved.recent = [noPrecheck.saved.recent[1]];
  w = world(['time_saved'], { time_saved: noPrecheck });
  await w.api.tsLoad();
  w.S.tsChatOpen = true;
  w.S.tsChatFilter = 'precheck';
  h = w.api.chatSavedHtml();
  ok(h.includes('aib-ledger') && !h.includes('這類還沒有紀錄'), 'reason: stale_filter_not_reset\n' + h);

  const manyRecent = JSON.parse(JSON.stringify(withRecent));
  manyRecent.saved.recent = Array.from({ length: 12 }, (_, i) => ({
    at: '2026-11-23T06:00:00Z', system: 'shadow', confirmed: true, saved_ms: 1000, avoided: 'unit-' + i, basis: 'b',
  }));
  w = world(['time_saved'], { time_saved: manyRecent });
  await w.api.tsLoad();
  w.S.tsChatOpen = true;
  h = w.api.chatSavedHtml();
  ok(h.includes('unit-7') && !h.includes('unit-8') && h.includes('12 筆'), 'reason: recent_slice_8_cap\n' + h);

  const todayOnly = JSON.parse(JSON.stringify(withRecent));
  todayOnly.saved.recent = [];
  w = world(['time_saved'], { time_saved: todayOnly });
  await w.api.tsLoad();
  w.S.tsChatOpen = true;
  h = w.api.chatSavedHtml();
  ok(h.includes('href="saved.html"') && h.includes('暫無逐筆明細') && !h.includes('這類還沒有紀錄'), 'reason: empty_recent_with_today\n' + h);

  console.log('saved-blocks: ' + n + ' 項全部通過');
})().catch((e) => { console.error(e); process.exit(1); });
