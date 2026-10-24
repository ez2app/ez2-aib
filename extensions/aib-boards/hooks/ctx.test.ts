import { test, expect } from 'claude-code/testing'
import { FIXTURE } from './i18n-fixture'

// 10-24 @abb89:每一輪結束就重讀 CTX 刷狀態列(以前只靠 60 秒計時器,開席頭 60 秒「CTX 量不到」)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any
const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
const SEAT = { cabin_code: '6ddf', role: 'dev', code: 'abb89', unread: 0, unread_list: [], mod: { on: true }, handoff: { done: false, steps: [] } }
test('turn.complete:CTX 量到了就刷狀態列,不用等 60 秒', async ($: Any, on: Any) => {
  const statuses: string[] = []
  let pct: number | undefined
  on('env.get', async (_$: Any, e: Any) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h', EZ2_AIB_LOCALE: 'zh-TW' } as Record<string, string>)[e.name] }) as never)
  on('process.run', async (_$: Any, e: Any) => { const a = e.argv as string[]; return a[2]?.endsWith('/i18n_lib.py') ? ok(JSON.stringify(FIXTURE['zh-TW'])) : a.includes('--dm-dir') ? ok('') : a[2]?.endsWith('/seat-info.py') ? ok(JSON.stringify(SEAT)) : ok('{}') })
  on('fs.exists', async () => ({ value: false }) as never)
  on('session.usage', async () => ({ value: pct === undefined ? {} : { context: { percent: pct } } }) as never)
  on('ui.status', async (_$: Any, e: Any) => { statuses.push(String(e.text ?? e)); return { value: undefined } as never })
  on('turn.complete', async () => ({ text: '' }) as never)
  on('command.register', async () => ({ value: undefined }) as never)
  on('session.start', async (_$: Any, e: Any) => ({ cwd: e.cwd ?? '/repo' }) as never)
  await $.session.start({ source: 'startup', cwd: '/repo' })
  const before = statuses.length
  pct = 37
  const turn = { answer: '已就緒', durationMs: 10, isAborted: false, turnId: 't1', reason: 'answer' }
  await $.turn.complete(turn)
  expect(statuses.length).toBe(before + 1)          // 量到了 ⇒ 當場刷一次
  await $.turn.complete({ ...turn, turnId: 't2' })
  expect(statuses.length).toBe(before + 1)          // 沒變 ⇒ 不重刷
})
