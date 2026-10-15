import { test, expect, mock } from 'claude-code/testing'
import { FIXTURE } from './i18n-fixture'

// 面板要真的畫得出來(aib-seat 10-09 因變數 h 蓋掉 JSX 工廠整片空白、測試卻全綠)
const sample = { schema: 'aib-seat-screen/v1', seat_id: 'ee841', code: '86ff0', cabin: '931e', role: 'pm',
  persona: { uid: 'rvmtfw', name: 'AIB艦長室開發官', emoji: '🏢', category: '開發', duties: ['做好、驗好、交出'], red_lines: ['不把推測寫成量到'] },
  project: { uid: 'proj:015306a1b9935d81', title: '艦長室網頁與席位面板', work_state: 'S1 做資料層' },
  now: { text: 'aib-boards M1', state: 'busy', at: Date.now() / 1000 - 60 },
  todo: [{ id: 'dm:q_1', group: 'now', text: '修 batch20 推前紅', from: '@8f366', source: 'dm' }, { id: 'l:1', group: 'self', text: '主機 mod 進倉', source: 'ledger' }],
  know: [{ sensor: 'machine_lease', level: 'warn', line: '機器:ship.py 用到 02:24', measured_at: Date.now() / 1000, data: { state: 'held', owner: 'ship.py', acquired_epoch: Date.now() / 1000 - 600, expiry_epoch: Date.now() / 1000 + 3600 } },
    { sensor: 'inbox', level: 'warn', data: { unread: 1, open: 2 } },
    { sensor: 'my_trees', level: 'ok', data: { trees: [
      { branch: 'fix/wheel-86ff0-1014', head: '6cbcdeac0', stage: 1, commits: 2, reds: ['test-mouse.py'], refused: 'int/b2021-8f366-1014' },
      { branch: 'feat/mods-86ff0-1014', head: '8617c957a', stage: 0, commits: 15, reds: [], dirty: true },
      { branch: 'fix/old-86ff0-1013', head: 'a986cef6b', stage: 3, commits: 3, reds: [] }] } }],
  measured_at: Date.now() / 1000, took_ms: 420, missing: ['push_chain: 逾時'] }
const chartsSample = { charts: { trees: [{ branch: 'fix/a-86ff0', ahead: 12, in_batch: false, green: 20, red: 1 }, { branch: 'docs/b-86ff0', ahead: 3, in_batch: true, green: 0, red: 0 }],
  shadow: { kept: 1, skipped: 0, ran: 4, without_s: 54.6, actual_s: 66, reasons: [['讀過的檔內容變了', 3], ['上次是紅燈', 1]] },
  trend: [['10-08', 0, 0], ['10-09', 0, 0], ['10-10', 0, 0], ['10-11', 0, 0], ['10-12', 3, 0], ['10-13', 92, 2], ['10-14', 4, 0]],
  slowest: [], commits: [['10-08', 0], ['10-09', 0], ['10-10', 0], ['10-11', 0], ['10-12', 2], ['10-13', 23], ['10-14', 5]],
  files: [['docs/DESIGN-seat-screen-86ff0-1013.md', 485], ['src/persona_behavior/measure.py', 278]],
  hex: { success: { green: 99, red: 3 }, speed: { median_min: 0, n: 12 }, inbox: { unread: 0 }, handoff: { rounds: 14, receipts: 36 }, collab: { seats: 3 } }, measured_at: Date.now() / 1000, took_ms: 900 } }
const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
// 語系(§5):i18n_lib.py --prefix 由測試答(字典快照 i18n-fixture.ts);EZ2_AIB_LOCALE 由測試給(aib-languages 平常會設)
const dict: Record<string, Record<string, string>> = { ...FIXTURE }
const i18n = (a: string[]) => (a[2]?.endsWith('/i18n_lib.py') ? ok(JSON.stringify(dict[a[a.indexOf('--locale') + 1]!] ?? {})) : null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const wireEnv = (on: any, loc = 'zh-TW') => on('env.get', async (_$: unknown, e: unknown) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h', EZ2_AIB_LOCALE: loc } as Record<string, string>)[(e as { name: string }).name] }) as never)
const CJK = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/

test('今天頁畫得出來:狀態、應知、待辦分組、職責紅線、專案、沒量到', async ($, on) => {
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : a.includes('--charts') ? ok(JSON.stringify(chartsSample)) : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'aib-boards', surface, component: 'Pane', requestId: 'aib-boards', viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 120 } as never })
    await ui.press({ key: 'refresh' })
    expect(await ui.find({ type: 'Button', text: /動畫:開/ })).toBeDefined()
    for (const re of [/此刻/, /CTX/, /機器/, /ship\.py/, /最晚 \d\d:\d\d 放/, /信箱/, /未讀 1/, /最新 @8f366/, /我的包/, /紅 1 支/, /忙碌中/, /現在要做/, /修 batch20 推前紅/,
      /我的包 · 2 包沒上主線 · 1 包已上/, /wheel-86ff0-1014/, /紅:test-mouse\.py/, /int\/b2021-8f366-1014 被擋/, /交 = /, /推包席/, /機器租約/, /主線/, /下一件/, /紀錄/, /拿機器/, /AIB艦長室開發官/, /職責 1 · 紅線 1/, /艦長室網頁與席位面板/, /3 項/, /沒量到:push_chain/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
    for (const re of [/雷達與分布/, /越近中心越要處理/, /做好、驗好、交出/]) expect(await ui.find({ type: 'Text', text: re })).toBeUndefined()
    await ui.press({ key: 'ex-persona' })
    for (const re of [/做好、驗好、交出/, /不把推測寫成量到/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
    await ui.press({ key: 'ex-persona' })
    await ui.press({ key: 'tab-charts' })
    for (const re of [/六角寶石/, /我的分支測試/, /影子續跑/, /我的測試紅綠/, /提交節奏/, /改動熱區/, /fix\/a-86ff0/, /沒省到/, /讀過的檔內容變了/, /成功 /, /7 天寄給 3 席/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
    await ui.press({ key: 'tab-today' })
    await ui.unmount()
  }
})
test('沒裝資料層:照實說還沒裝', async ($, on) => {
  wireEnv(on)
  on('process.run', async (_$, e) => i18n((e as { argv: string[] }).argv) ?? ok('||/h'))
  on('fs.exists', async (_$, e) => ({ value: String((e as { path?: string }).path ?? '').endsWith('/i18n_lib.py') }) as never)   // 只有字典在,資料層不在
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 120 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Text', text: /seat_screen\.py 還沒裝/ })).toBeDefined()
  await ui.unmount()
})
test('外掛叫 /aib-boards 不開', async ($, on) => {
  wireEnv(on)
  on('process.run', async (_$, e) => i18n((e as { argv: string[] }).argv) ?? ok(''))
  on('fs.exists', async () => ({ value: true }) as never)
  const r = await $.command.run({ command: 'aib-boards', args: '' } as never)
  expect(String((r as { text?: string }).text)).toMatch(/只在用戶端打開/)
})
test('面板窄(實測 103 格):機器 / 主線 / 信箱收成一框,舊標題不重複', async ($, on) => {
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 45, columns: 103 }, props: { bodyColumns: 103 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Text', text: /機器 · 主線 · 信箱/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /量於 \d+ 秒前\(420 ms\)$/ })).toBeUndefined()
  await ui.unmount()
})
test('標誌原廠色:今天頁的席碼標誌帶原廠色,不跟外層變暗', async ($, on) => {
  const withLogos = { ...sample, logos: { '86ff0': '', '8f366': '' }, logo_colors: { '86ff0': '#d97757', '8f366': '#d97757' } }
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(withLogos))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 120 } as never })
  await ui.press({ key: 'refresh' })
  const n = await ui.find({ type: 'Text', text: /^$/ }) as { props?: { color?: string; dimColor?: boolean } } | undefined
  expect(n?.props?.color).toBe('#d97757')
  expect(n?.props?.dimColor).toBeFalsy()
  await ui.unmount()
})

// ── 語系(10-14 aib-languages §5)──
test('依角色換流程(§22.5 第 1 步):驗收席照 S1 的節點畫 待驗 → 驗過 → 退回,不畫開發席的推包框', async ($, on) => {
  const qa = { ...sample, role: 'acceptance', flow: { family: 'qa', nodes: [
    { key: 'qa_pending', level: 'error', count: 2, guess: false, items: [{ who: '@8f366(第二艦長)', text: 'relogin 那包請驗', at: 1 }] },
    { key: 'qa_passed', level: 'ok', count: 0, guess: false, items: [] },
    { key: 'qa_returned', level: 'warning', count: 1, guess: true, items: [{ who: '→ @8f366', text: '退回:2 支紅', at: 1 }] }] } }
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(qa))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 60, columns: 140 }, props: { bodyColumns: 140 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Text', text: /^待驗 2$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^退回 1/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /推估/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /推包席/ })).toBeUndefined()
  await ui.unmount()
})
test('依人格換主題:人格卡沒寫 theme ⇒ 預設(沒有 double 框;排在有主題那條前面 —— 面板資料會留到下一條)', async ($, on) => {
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 60, columns: 140 }, props: { bodyColumns: 140 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Text', text: /^✦$/ })).toBeUndefined()   // find 的 props 篩選不生效(會比到最外層) ⇒ 用畫出來的光點字驗
  await ui.unmount()
})
test('依人格換主題(§22.5 第 2 步):人格卡 theme 的框線與主色套到今天頁', async ($, on) => {
  const themed = { ...sample, persona: { ...sample.persona, theme: { color: '#3b82f6', particle: '✦', speed: 'fast', border: 'double' } } }
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(themed))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 60, columns: 140 }, props: { bodyColumns: 140 } as never })
  await ui.press({ key: 'refresh' })
  const p = await ui.find({ type: 'Text', text: /^✦$/ })   // 連接線光點換成人格的字與主色(找到的是外層那行,顏色在裡面那一格)
  expect(JSON.stringify(p)).toContain('#3b82f6')
  await ui.unmount()
})
test('依使用習慣調整(§22.5 第 3 步):設定檔 usage 點最多的收合區塊排第一、預設展開,出「版面回預設」', async ($, on) => {
  wireEnv(on)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  on('fs.read', async (_$, e) => (String((e as { path?: string }).path ?? '').endsWith(`/state/aib-boards/${sample.seat_id}.json`)
    ? { value: JSON.stringify({ charts: ['SH'], usage: { know: 4 } }) } : { deny: 'ENOENT' }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 60, columns: 140 }, props: { bodyColumns: 140 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Button', text: /▾ 應知/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /▸ 人格/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /版面回預設/ })).toBeDefined()
  await ui.unmount()
})
test('切成英文:分頁、按鈕、框標題都是英文,按鈕上沒有中日韓字;日期不寫死 zh-TW', async ($, on) => {
  wireEnv(on, 'en')
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : a.includes('--charts') ? ok(JSON.stringify(chartsSample)) : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 120 } as never })
  await ui.press({ key: 'refresh' })
  for (const re of [/^Today$/, /^Project$/, /^Chat$/, /^Charts$/, /^Custom$/, /^Refresh$/, /Animation: on/, /Persona/, /Know/]) expect(await ui.find({ type: 'Button', text: re })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: CJK })).toBeUndefined()
  for (const re of [/^Now$/, /My packages · 2 not on main yet · 1 landed/, /Push seat · /, /Next up · top of the to-do list/, /^Log$/, /Checked every time:/, /D = at least one commit/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  for (const re of [/此刻/, /我的包/, /推包席/, /下一件/, /紀錄/]) expect(await ui.find({ type: 'Text', text: re })).toBeUndefined()
  await ui.press({ key: 'tab-charts' })
  for (const re of [/^Hex gem$/, /^Shadow resume$/, /Engineer tier/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /六角寶石|影子續跑|工程級/ })).toBeUndefined()
  await ui.unmount()
})
test('字典缺鍵:畫面寫 !!鍵!!(fail-loud),不偷偷退回中文', async ($, on) => {
  const partial = { ...FIXTURE.en! }; delete partial['mod.aib_boards.tab_today']
  dict.ko = partial                                                      // 用 ko 當這一格的語系:別的測試沒用過,快取是乾淨的
  wireEnv(on, 'ko')
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv; return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 120 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Button', text: '!!mod.aib_boards.tab_today!!' })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /^Project$/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /^今天$/ })).toBeUndefined()
  await ui.unmount()
})
test('沒有 aib-languages(env 沒設):問 --resolve;問失敗時沿用上次問到的語系,不閃回中文、也不把失敗記成快取', async ($, on) => {
  const clock = mock.clock(on)
  let fail = false, asked = 0
  on('env.get', async (_$: unknown, e: unknown) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h' } as Record<string, string>)[(e as { name: string }).name] }) as never)
  on('process.run', async (_$, e) => { const a = (e as { argv: string[] }).argv
    if (a[2]?.endsWith('/i18n_lib.py') && a.includes('--resolve')) { asked++; return fail ? ({ value: { exitCode: 1, stdout: '', stderr: 'boom', isStdoutTruncated: false, isStderrTruncated: false } }) as never : ok(JSON.stringify({ locale: 'en', source: 'user', files: {} })) }
    return i18n(a) ?? (a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : ok(JSON.stringify(sample))) })
  on('fs.exists', async () => ({ value: true }) as never)
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 120 } as never })
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ type: 'Button', text: /^Today$/ })).toBeDefined()
  const n = asked
  fail = true
  await clock.advance(11_000)                                            // 10 秒快取過期 ⇒ 重問,這次失敗
  await ui.press({ key: 'refresh' })
  expect(asked).toBeGreaterThan(n)
  expect(await ui.find({ type: 'Button', text: /^Today$/ })).toBeDefined()   // 還是英文
  expect(await ui.find({ type: 'Button', text: /^今天$/ })).toBeUndefined()
  const m = asked
  await ui.press({ key: 'refresh' })
  expect(asked).toBe(m)                                                  // 剛失敗:5 秒內不重問(不每格起一支 Python)
  fail = false
  await clock.advance(6_000)                                             // 過了 5 秒:再問(動畫重畫或按鈕都會觸發),成功
  await ui.press({ key: 'refresh' })
  expect(asked).toBeGreaterThan(m)
  expect(await ui.find({ type: 'Button', text: /^Today$/ })).toBeDefined()
  await ui.unmount()
})
