import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { nextLine, NEXT_SCHEMA, type NextDoc } from './next'

const ok = [{ name: 'slice_due', state: 'ok' }, { name: 'seats', state: 'ok' }]
const doc = (o: Partial<NextDoc>): string => JSON.stringify({ schema: NEXT_SCHEMA, complete: true, sources: ok, rows: [], ...o })
const row = (to: string, what: string) => ({ rank: 1, kind: 'receipt', what, to, why_now: '差:沒有收據' })

test('next: the first row addressed to this seat, skipping rows for other seats', () => {
  const out = nextLine('b433b', { exitCode: 0, stderr: '', stdout: doc({ rows: [row('a9136', 'A 推'), row('b433b', 'B 跑收據'), row('b433b', 'C')] }) })
  expect(out).toBe('你現在可以先做:B 跑收據(receipt;差:沒有收據)')
})

test('next: rc 3 with JSON is shown, marked incomplete with the sources that are not ok', () => {
  const out = nextLine('b433b', { exitCode: 3, stderr: '', stdout: doc({ complete: false, sources: [...ok, { name: 'tidy_scan', state: 'unsupported' }], rows: [row('b433b', 'B')] }) })
  expect(out).toContain('你現在可以先做:B')
  expect(out).toContain('(清單不完整:tidy_scan unsupported)')
})

test('next: nothing for this seat is said as such, never as unreadable; incomplete still marked', () => {
  expect(nextLine('b433b', { exitCode: 0, stderr: '', stdout: doc({ rows: [row('a9136', 'A')] }) })).toBe('你現在可以先做:清單裡沒有輪到你的')
  expect(nextLine('b433b', { exitCode: 3, stderr: '', stdout: doc({ complete: false, rows: [] }) })).toBe('你現在可以先做:清單裡沒有輪到你的(清單不完整:rc=3)')
})

test('next: unreadable cases say 清單讀不到 with the reason', () => {
  const bad = '你現在可以先做:清單讀不到:'
  expect(nextLine('b433b', { missing: true })).toBe(bad + '沒裝 aib-drive')
  expect(nextLine('b433b', { spawn: 'Error: timed out' })).toBe(bad + 'Error: timed out')
  expect(nextLine('b433b', { exitCode: 3, stdout: '', stderr: 'x\nseats: 讀不到席位表' })).toBe(bad + 'rc=3 seats: 讀不到席位表')
  expect(nextLine('b433b', { exitCode: 0, stdout: 'not json', stderr: '' })).toBe(bad + '輸出不是 JSON')
  expect(nextLine('b433b', { exitCode: 0, stdout: JSON.stringify({ schema: 'aib-drive.next.v2', rows: [] }), stderr: '' })).toBe(bad + '格式 aib-drive.next.v2 不認得')
  expect(nextLine('b433b', { exitCode: 64, stdout: doc({ rows: [row('b433b', 'B')] }), stderr: 'usage' })).toBe(bad + 'rc=64 usage')
})
