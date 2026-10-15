import { test, expect } from 'claude-code/testing'
import { FIXTURE } from './i18n-fixture'

// 新信彈窗:監看這一席的信件資料夾;真人沒打開過面板 ⇒ 有新信也不跳(AI 席不被打擾)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any
const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
test('沒打開過面板:有新信也不跳;watchPaths 有登記這一席的信件資料夾', async ($: Any, on: Any) => {
  const toasts: string[] = []
  let py = ''
  on('env.get', async (_$: Any, e: Any) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h', EZ2_AIB_LOCALE: 'zh-TW' } as Record<string, string>)[e.name] }) as never)
  on('process.run', async (_$: Any, e: Any) => { const a = e.argv as string[]; return a[2]?.endsWith('/i18n_lib.py') ? ok(JSON.stringify(FIXTURE['zh-TW'])) : a[0] === 'sh' ? ok('/x/seat_screen.py||/h') : a.includes('-c') ? (py = a.join(' '), ok('/h/.ez2-aib/state/dm-queue/86ff0\n')) : ok('{}') })
  on('fs.exists', async () => ({ value: true }) as never)
  on('fs.read', async () => ({ value: JSON.stringify({ id: 'q_1', sender: '@8f366', msg: '新信', ts: Date.now() / 1000 }) }) as never)
  on('classic.SessionStart', async () => ({}) as never)   // 引擎那層:沒有設定檔的 hook
  on('classic.FileChanged', async () => ({}) as never)
  on('ui.toast', async (_$: Any, e: Any) => { toasts.push(String(e.text ?? e)); return { value: undefined } as never })
  const r = await $.classic.SessionStart({ source: 'startup' })
  expect(py).toContain('self_code')   // 席碼用正本 seat_now.self_code,不是只看 AIB_RESUME_SESSION_UUID
  expect((r.watchPaths ?? []).some((p: string) => p.endsWith('/dm-queue/86ff0'))).toBe(true)
  await $.classic.FileChanged({ file_path: '/h/.ez2-aib/state/dm-queue/86ff0/q_1.json', event: 'add' })
  expect(toasts.length).toBe(0)
})
