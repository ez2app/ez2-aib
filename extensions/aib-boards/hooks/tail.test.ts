import { test, expect } from 'claude-code/testing'
import { FIXTURE } from './i18n-fixture'

// 10-28 Andy 工作單 S「這個 aib 太多行了,我希望縮成一行」:未讀 dm(+交接標記)交給 💧 狀態列尾巴(seat-info.py --tail-put → 核心 statusline_tail.py)。
//   尾巴有在畫(shown)⇒ 收掉自己這一行;沒在畫 ⇒ 照舊自己一行;量不到席位 ⇒ 拿掉這段(--clear)。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any
const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
const SEAT = { cabin_code: '6ddf', role: 'dev', code: 'abb89', unread: 2, unread_list: [], mod: { on: true }, handoff: { done: false, steps: [] } }

function wire(on: Any, w: { shown: boolean; seat: unknown; pct?: number; puts: string[][]; statuses: (string | undefined)[] }) {
  on('env.get', async (_$: Any, e: Any) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h', EZ2_AIB_LOCALE: 'zh-TW' } as Record<string, string>)[e.name] }) as never)
  on('process.run', async (_$: Any, e: Any) => {
    const a = e.argv as string[]
    if (a[2]?.endsWith('/i18n_lib.py')) return ok(JSON.stringify(FIXTURE['zh-TW']))
    if (a.includes('--dm-dir')) return ok('')
    if (a[3] === '--tail-put') { w.puts.push(a.slice(4)); return ok(JSON.stringify({ ok: true, shown: w.shown })) }
    if (a[2]?.endsWith('/seat-info.py')) return w.seat ? ok(JSON.stringify(w.seat)) : { value: { exitCode: 1, stdout: '', stderr: 'boom', isStdoutTruncated: false, isStderrTruncated: false } } as never
    return ok('{}')
  })
  on('fs.exists', async (_$: Any, e: Any) => ({ value: String(e.path ?? '').endsWith('/i18n_lib.py') }) as never)   // 字典要找得到倉才載
  on('session.id', async () => ({ value: 'sid-1' }) as never)
  on('session.cwd', async () => ({ value: '/proj' }) as never)   // #401 R2:put 帶這個對話的 cwd
  on('session.usage', async () => ({ value: w.pct === undefined ? {} : { context: { percent: w.pct } } }) as never)
  on('ui.status', async (_$: Any, e: Any) => { w.statuses.push(e.text); return { value: undefined } as never })
  on('command.register', async () => ({ value: undefined }) as never)
  on('session.start', async (_$: Any, e: Any) => ({ cwd: e.cwd ?? '/repo' }) as never)
}

test('💧 狀態列有在畫(shown)⇒ 未讀 dm 交給尾巴、收掉自己這一行', async ($: Any, on: Any) => {
  const w = { shown: true, seat: SEAT, puts: [] as string[][], statuses: [] as (string | undefined)[] }
  wire(on, w)
  await $.session.start({ source: 'startup', cwd: '/repo' })
  expect(w.puts.at(-1)).toEqual(['sid-1', '--text', '未讀 dm 2', '--cwd', '/proj'])
  expect(w.statuses.at(-1)).toBe(undefined)
})

test('💧 狀態列沒在畫(會員用自己的狀態列)⇒ 照舊自己一行', async ($: Any, on: Any) => {
  const w = { shown: false, seat: SEAT, puts: [] as string[][], statuses: [] as (string | undefined)[] }
  wire(on, w)
  await $.session.start({ source: 'startup', cwd: '/repo' })
  expect(w.puts.length).toBe(1)
  expect(w.statuses.at(-1)).toContain('未讀 dm 2')
  expect(w.statuses.at(-1)).toContain('6ddf')
})

test('CTX 到交接門檻 ⇒ 交接標記跟著接到尾巴(只在有時),放最前面(#401 R1:窄終端從尾端截,先保它)', async ($: Any, on: Any) => {
  const w = { shown: true, seat: { ...SEAT, unread: 0 }, pct: 85, puts: [] as string[][], statuses: [] as (string | undefined)[] }
  wire(on, w)
  await $.session.start({ source: 'startup', cwd: '/repo' })
  const text = w.puts.at(-1)![2]!
  expect(text).toBe('⚠ 該交接 · dm 0')
})

test('量不到席位 ⇒ 尾巴拿掉這段(--clear),自己那句量不到照舊', async ($: Any, on: Any) => {
  const w = { shown: true, seat: null, puts: [] as string[][], statuses: [] as (string | undefined)[] }
  wire(on, w)
  await $.session.start({ source: 'startup', cwd: '/repo' })
  expect(w.puts.at(-1)).toEqual(['sid-1', '--clear', '--cwd', '/proj'])
  expect(typeof w.statuses.at(-1)).toBe('string')
})
