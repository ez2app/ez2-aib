import { test, expect, mock } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'

// 新信卡片(the maintainer 10-14):信一到就在輸入框上方出卡 —— 狀態色、右欄同一個角色圖示、可展開、按 ✕ 才關,真人信不跳 toast(AI 彼此的信 10-25 起走 toast)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any
const SEAT = { cabin: '20261010-125119-931e', cabin_code: '931e', role: 'pm', code: '86ff0', window: '@10', seat_id: 'ee841',
  seat_file: '/x/w10.json', seat_file_has_uuid: true, captain: '8f366', captain_addr: '931e/8f366', roots: ['8f366'], home: '/h/.ez2-aib',
  unread: 0, unread_list: [], handoff: { done: false, at: null, file: null, steps: [] }, dispatch: null, blocks: [], err: [],
  logos: { '8f366': 'C', '86ff0': 'C' }, logo_colors: { '8f366': 'ansi256(173)', '86ff0': 'ansi256(173)' },
  marks: { '8f366': '👑', '86ff0': '👔' }, personas: { '8f366': { name: '第二艦長' }, '86ff0': { name: 'AIB艦長室開發官' } } }
const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
const LONG = '機器空了,請跑三包。'.repeat(30)

test('真人來信 → 卡片:狀態色、標題列角色圖示(不畫像素大頭像)、展開、讀了變色、✕ 才關;席的信不出卡、改 toast 一次', async ($: Any, on: Any) => {
  mock.clock(on)
  const toasts: string[] = [], files: Record<string, string> = {}
  on('process.run', async (_$: Any, e: Any) => { const a = e.argv as string[]
    if (a[2]?.endsWith('/i18n_lib.py')) return ok(JSON.stringify(FIXTURE[a[a.indexOf('--locale') + 1]!] ?? {}))   // 字典(語系由 env 給 zh-TW)
    if (a.includes('--dm-dir')) return ok('/h/.ez2-aib/state/dm-queue/86ff0\n')
    return ok(JSON.stringify(SEAT)) })
  on('fs.read', async (_$: Any, e: Any) => (files[e.path] !== undefined ? { value: files[e.path] } : { error: new Error('ENOENT') }) as never)
  on('fs.write', async () => ({ value: undefined }) as never)
  on('classic.SessionStart', async () => ({}) as never)
  on('classic.FileChanged', async () => ({}) as never)
  on('ui.render', async ($: Any, e: Any) => { const { Box } = $.ui.resolve(e); return <Box /> })   // 引擎那層:沒卡片時 band 交回引擎(空的)
  on('session.start', async (_$: Any, e: Any) => e as never)
  on('command.register', async () => ({ value: ({}) }) as never)
  on('ui.open', async () => ({ value: ({ isPlaced: true }) }) as never)
  on('ui.status', async () => ({ value: ({}) }) as never)
  on('ui.toast', async (_$: Any, e: Any) => { toasts.push(String(e.text ?? e)); return { value: undefined } as never })
  on('env.get', async (_$: Any, e: Any) => ({ value: ({ EZ2_AIB_ROOT: '/repo', EZ2_AIB_LOCALE: 'zh-TW' } as Record<string, string>)[e.name] }) as never)
  on('fs.exists', async () => ({ value: true }) as never)
  on('session.id', async () => ({ value: '577b90b2-62f8-40f7-ab1a-c1147b986ff0' }) as never)
  on('session.usage', async () => ({ value: ({ context: { percent: 40 } }) }) as never)
  on('session.model', async () => ({ value: 'claude-opus-5-5' }) as never)
  const r = await $.classic.SessionStart({ source: 'startup' })
  expect((r.watchPaths ?? []).some((p: string) => p.endsWith('/dm-queue/86ff0'))).toBe(true)
  await $.session.start({ cwd: '/x', source: 'startup' })

  const path = '/h/.ez2-aib/state/dm-queue/86ff0/q_9.json'
  const seatPath = '/h/.ez2-aib/state/dm-queue/86ff0/q_8.json'
  files[seatPath] = JSON.stringify({ id: 'q_8', seat: '86ff0', sender_seat: '8f366', sender_kind: 'seat', msg: '席的信', sent_at: 1791921000 })
  await $.classic.FileChanged({ file_path: seatPath, event: 'add' })
  const q = { id: 'q_9', seat: '86ff0', sender_seat: '8f366', sender_label: '@8f366(第二艦長)', sender_kind: 'human', msg: LONG, sent_at: 1791922000 }
  files[path] = JSON.stringify(q)
  await $.classic.FileChanged({ file_path: path, event: 'add' })
  const ui = await $.ui.mount({ plugin: 'aib-boards', surface: 'terminal', component: 'AbovePrompt', requestId: 'band',
    viewport: { rows: 50, columns: 120 }, props: { bodyColumns: 115, hasSurvey: false, isWorking: false, maxRows: 25 } as never })
  expect(await ui.find({ type: 'Text', text: /新信 1 封/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /● 未讀/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^第二艦長$/ })).toBeDefined()
  expect(await ui.find({ type: 'Raster' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^👑 $/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /席的信/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /…$/ })).toBeDefined()
  await ui.press({ key: 'mail-open-q_9' })
  expect(await ui.find({ type: 'Text', text: /^(機器空了,請跑三包。){30}$/ })).toBeDefined()
  files[path] = JSON.stringify({ ...q, read_at: 1791922100 })
  await $.classic.FileChanged({ file_path: path, event: 'change' })
  expect(await ui.find({ type: 'Text', text: /● 已讀未結/ })).toBeDefined()
  await ui.press({ key: 'mail-x-q_9' })
  expect(await ui.find({ type: 'Text', text: /新信/ })).toBeUndefined()
  await $.classic.FileChanged({ file_path: path, event: 'change' })
  expect(await ui.find({ type: 'Text', text: /新信/ })).toBeUndefined()
  expect(toasts.filter(t => t.includes('📬')).length).toBe(0)
  // Andy 10-25:席的信不出卡,改右上角 toast 一次(右欄圖示 + 寄件人 + 內容),再讀到同一封不重複
  expect(toasts.filter(t => t.includes('席的信'))).toEqual(['👑 @8f366:席的信'])
  await $.classic.FileChanged({ file_path: seatPath, event: 'change' })
  expect(toasts.filter(t => t.includes('席的信')).length).toBe(1)
  expect(toasts.some(t => t.includes('機器空了'))).toBe(false)
  await ui.unmount()
})
