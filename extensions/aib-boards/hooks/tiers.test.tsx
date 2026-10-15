import { test, expect } from 'claude-code/testing'
import { FIXTURE } from './i18n-fixture'

// 三級圖表 + 私訊 + 專案 + 自訂都要真的畫得出來(資料用模擬的 S1 輸出)
const T = Date.now() / 1000
const base = { schema: 'aib-seat-screen/v1', seat_id: 'S1', code: '8f366', cabin: '931e', role: 'root', persona: { name: '第二艦長' }, project: { uid: 'proj:x', title: 'AIB 指令' }, now: null, todo: [], know: [], measured_at: T }
const eng = { trees: [{ branch: 'int/batch20', ahead: 82, in_batch: true, green: 25, red: 2 }], shadow: null, trend: [['10-13', 87, 1]], slowest: [['test-a.py', 172.4, 1]], commits: [['10-13', 40]], files: [['a.py', 10]],
  hex: { success: { green: 87, red: 1 }, speed: { median_min: 0.5, n: 9 }, inbox: { unread: 0 }, handoff: { rounds: 11, receipts: 10 }, collab: { seats: 3 } }, measured_at: T }
const seats = [{ code: 'a9769', prov: 'claude', ctx: 54, idle_s: 60, unread: 0, now_state: 'busy' }, { code: '25515', prov: 'grok', ctx: 88, idle_s: 9000, unread: 4, now_state: 'reply', stale: true }]
const lead = { subs: seats, funnel: [8, 5, 3, 11], wait: [0.2, 3, 12, 70, 300], gantt: [{ title: 'B1 人格', owner: '86ff0', status: 'done', s: T - 9000, e: T - 3000 }], hot: [['test-x.py', 4, 9]] }
const qa = { fleet_trend: [['10-12', 30, 2], ['10-13', 90, 5]], flaky: [['test-f.py', 3, 2]], pareto: [['上次量不到', 915], ['紀錄池有壞列', 513]], gate_blocks: [['86ff0', 3]], events: [{ t: T - 3600, who: '86ff0', kind: 'now_gate' }] }
const captain = { shadow: { runs: [[T - 9000, 8000, 2000, 0], [T - 3000, 9000, 3000, 100]] }, seats, lease: [{ s: T - 7200, e: T - 3600, who: '推包 ship.py', ex: true }], heat: { codes: ['8f366'], rows: { '8f366': Array.from({ length: 24 }, (_, i) => i) } },
  river: [[T - 21600, 3, 1, 0], [T, 2, 0, 1]], waterfall: { without_h: 311, reuse_h: 36, select_h: 39, shard_h: 117, actual_h: 119 }, chord: { names: ['8f366', 'a9769'], m: [[0, 5], [3, 0]] }, funnel: [10, 7, 4, 15],
  decide: [{ since: T - 4000, text: '要你決定:Pro 的 python 版本', from: '@8f366' }], beat: Array.from({ length: 72 }, (_, i) => i % 9), sankey: [{ a: 'seat', d: '10-13', o: 'open', n: 1 }, { a: 'aib', d: '10-12', o: 'fixed', n: 2 }] }
const dm = { items: [{ id: 'q1', sender: '@a9769', status: 'waiting', when: '00:40', text: 'batch20 推前紅,請修' }, { id: 'q2', sender: '@8f366', status: 'done', when: '22:59', text: '收到 a986cef6b' }], counts: { unread: 1, open: 1, done_7d: 9 }, chat: [{ at: T - 60, from: 'a9769', to: '8f366', out: false, state: 'unread', text: '你好,這是對話氣泡' }, { at: T - 30, from: '8f366', to: 'a9769', out: true, state: 'done', text: '收到' }], seats: { a9769: { logo: 'C', name: 'Bot 接線員', emoji: '🔗' }, '8f366': { logo: 'C', name: '第二艦長', emoji: '⚓' } } }
const plan = { project_uid: 'proj:x', title: 'AIB 指令', nodes: [{ id: 'g', parent: null, layer: 'goal', title: '總綱', status: 'in_progress' }, { id: 'm', parent: 'g', layer: 'major', title: '大項一', status: 'done', progress: 1 }, { id: 't', parent: 'm', layer: 'task', title: '8f366 的 Task', status: 'in_progress', owner: '@8f366', progress: 0.5 }] }
const ok = (o: unknown) => ({ value: { exitCode: 0, stdout: typeof o === 'string' ? o : JSON.stringify(o), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any
function mock(on: Any, tier: string, extra: Record<string, unknown>) {
  on('env.get', async (_$: Any, e: Any) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h', EZ2_AIB_LOCALE: 'zh-TW' } as Record<string, string>)[(e as { name: string }).name] }) as never)
  on('process.run', async (_$: Any, e: Any) => { const a = (e as { argv: string[] }).argv
    if (a[2]?.endsWith('/i18n_lib.py')) return ok(FIXTURE[a[a.indexOf('--locale') + 1]!] ?? {})
    if (a[0] === 'sh') return ok('/x/seat_screen.py||/h')
    if (a.includes('--charts')) return ok({ ...base, tier, charts: { ...eng, ...extra } })
    if (a.includes('--dm')) return ok({ ...base, tier, dm })
    if (a.includes('--plan')) return ok({ ...base, tier, plan })
    return ok({ ...base, tier }) })
  on('fs.exists', async (_$: Any, e: Any) => ({ value: !String((e as { path?: string }).path ?? '').endsWith('.json') }) as never)
  on('fs.write', async () => ({ value: undefined }) as never)
}
const mount = ($: Any) => $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'Pane', requestId: 'aib-boards', viewport: { rows: 200, columns: 150 }, props: { bodyColumns: 150 } as never })

test('艦長級 14 張全畫得出來(影子續跑排第一)', async ($, on) => {
  mock(on, 'captain', { captain })
  const ui = await mount($); await ui.press({ key: 'refresh' }); await ui.press({ key: 'tab-charts' })
  for (const re of [/艦長級 14 張/, /^01 $/, /影子續跑$/, /影子續跑累積/, /全艦掃描雷達/, /六角寶石/, /推包河流/, /機器租約泳道/, /席位熱力格/, /瀑布/, /和弦/, /漏斗/, /紅燈去向/, /各席 CTX 儀表/, /主線心跳/, /等你決定/, /要你決定:Pro/, /少等/, /還沒轉綠/, /之後轉綠/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  await ui.unmount()
})
test('主管級 9 張(影子續跑排第一)', async ($, on) => {
  mock(on, 'lead', { lead })
  const ui = await mount($); await ui.press({ key: 'refresh' }); await ui.press({ key: 'tab-charts' })
  for (const re of [/主管級 9 張/, /影子續跑$/, /手下掃描雷達/, /我的包走到哪/, /手下各席狀態/, /我的分支漏斗/, /等待時間分布/, /甘特/, /測試熱點/, /B1 人格/, /忙碌太久沒更新/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  await ui.unmount()
})
test('監看級 7 張(影子續跑排第一)', async ($, on) => {
  mock(on, 'qa', { qa })
  const ui = await mount($); await ui.press({ key: 'refresh' }); await ui.press({ key: 'tab-charts' })
  for (const re of [/監看級 7 張/, /影子續跑$/, /紅綠趨勢/, /時紅時綠/, /帕雷托/, /累計/, /狀態列擋人/, /巡查時間軸/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  await ui.unmount()
})
test('私訊、專案、自訂', async ($, on) => {
  mock(on, 'eng', {})
  const ui = await mount($); await ui.press({ key: 'refresh' })
  await ui.press({ key: 'tab-dm' })
  for (const re of [/未讀 1/, /@a9769/, /batch20 推前紅/, /7 天完成 9/, /^對話$/, /收件匣/, /C 🔗 Bot 接線員 @a9769/, /你好,這是對話氣泡/, /\(我\)$/, /給 @/, /收到/, /\[未讀\]/, /\[對方完成\]/]) { if (!(await ui.find({ type: "Text", text: re }))) console.log("MISS", String(re)); expect(await ui.find({ type: "Text", text: re })).toBeDefined() }
  await ui.press({ key: 'tab-proj' })
  for (const re of [/總綱/, /大項一/, /8f366 的 Task/, /50%/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  await ui.press({ key: 'tab-custom' })
  expect(await ui.find({ type: 'Button', text: /☑ \d\d 六角寶石/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /☐\s+最慢的測試/ })).toBeDefined()
  await ui.press({ key: 'pick-SL' })
  expect(await ui.find({ type: 'Button', text: /☑ \d\d 最慢的測試/ })).toBeDefined()
  // §22.4 雷達 / 圓餅從今天頁搬來:預設不勾,勾了才在圖表頁畫
  expect(await ui.find({ type: 'Button', text: /☐\s+應知雷達/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /☐\s+待辦分布/ })).toBeDefined()
  await ui.press({ key: 'pick-KR' }); await ui.press({ key: 'pick-TP' })
  await ui.press({ key: 'tab-charts' })
  expect(await ui.find({ type: 'Text', text: /最慢的測試/ })).toBeDefined()
  for (const re of [/應知雷達/, /沒有應知卡/, /待辦分布/, /沒有待辦/]) expect(await ui.find({ type: 'Text', text: re })).toBeDefined()
  await ui.unmount()
})
