import { test, expect } from 'claude-code/testing'
import type { EngineInterface } from 'claude-code'
import { FIXTURE } from './i18n-fixture'
import { useT, makeT } from './i18n'
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { roomStep, openRoom } from './room'
import type { RoomInfo } from './seat/seat-label'

// /aib room 拆出席位核心(PLAN-extensions §2.4):沒裝艦長室外掛 ⇒ 只說開發中,連 aib room / open 都不跑
const live: RoomInfo = { url: 'http://127.0.0.1:8770/', port: 8770, cabin: '931e', pid: 1, alive: true, script: '/x/server.py', argv: [], thumb: null, thumb_age_s: null }
const fake = (calls: string[][], rc = 0) => ({ process: { run: async (argv: string[]) => { calls.push(argv); return { exitCode: rc, stdout: '', stderr: '' } } } }) as unknown as EngineInterface

test('not installed says in development even when an old room record is alive', async () => {
  expect(roomStep(false, live)).toBe('dev')
  expect(roomStep(undefined, live)).toBe('dev')
  const calls: string[][] = []
  expect(await openRoom(fake(calls), false, live)).toContain('開發中')
  expect(calls).toEqual([])
})

test('installed: never ran / dead without script / restart / open', async () => {
  expect(roomStep(true, null)).toBe('never')
  expect(roomStep(true, { ...live, alive: false, script: null })).toBe('dead')
  expect(roomStep(true, { ...live, alive: false })).toBe('restart')
  expect(roomStep(true, live)).toBe('open')
  const calls: string[][] = []
  expect(await openRoom(fake(calls), true, { ...live, alive: false })).toContain('剛重新啟動')
  expect(await openRoom(fake(calls), true, live)).toContain('已開 http://127.0.0.1:8770/')
  expect(calls).toEqual([['aib', 'room'], ['open', 'http://127.0.0.1:8770/']])
})
