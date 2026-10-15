import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { statusLabel, promptSection, handoffState, newBlocks, type SeatInfo } from './seat-label'

// Terminal.app 貼不了縮圖:艦長室格子改顯示網頁推來的一行文字摘要(summary.json),數字照抄、缺的當 0
const base: SeatInfo = { cabin: '20261009-011921-4f8d', cabin_code: '4f8d', role: 'pm', code: '86ff0', window: '@3', seat_id: '706eb',
  seat_file: '/x/w3.json', seat_file_has_uuid: true, captain: '04e0a', captain_addr: '4f8d/04e0a', roots: ['04e0a'], unread: 0, unread_list: [], err: [] }

test('status label shows cabin, role@code and ctx', () => {
  expect(statusLabel(base, 42)).toBe('CTX 42% · 艙 4f8d · pm@86ff0 · dm 0')
})

test('85% flips the handoff warning on in both status and prompt', () => {
  expect(statusLabel(base, 85)).toContain('該交接')
  expect(promptSection(base, 85)).toContain('aib handoff')
  expect(promptSection(base, 60)).not.toContain('aib handoff')
})

test('unread dms are listed with the mark-read command and the seat code', () => {
  const s = { ...base, unread: 1, unread_list: [{ id: 'q_1', rev: 1, sender: '@04e0a', when: '01:30', msg: 'hi' }] }
  const t = promptSection(s, null)
  expect(t).toContain('q_1')
  expect(t).toContain('--by seat:86ff0')
  expect(t).toContain('待審框:1 則未讀')
})

test('unmeasured fields say 量不到 instead of a guess', () => {
  const s = { ...base, cabin_code: null, code: null, unread: null, captain_addr: null }
  expect(statusLabel(s, null)).toBe('CTX 量不到 · 艙 量不到 · pm@量不到 · dm 量不到')
  expect(promptSection(s, null)).toContain('艦長 dm 地址:量不到')
})

test('several root seats and no reports_to: list them, never pick one', () => {
  const s = { ...base, captain: null, captain_addr: null, roots: ['04e0a', '6cb6a'] }
  const t = promptSection(s, null)
  expect(t).toContain('不要猜')
  expect(t).toContain('4f8d/04e0a')
  expect(t).toContain('4f8d/6cb6a')
})

test('receipt for this round turns the 85% warning into a green "可 /clear"', () => {
  const s = { ...base, handoff: { done: true, at: '05:37', file: '/p/HANDOFF.md', steps: ['1', '2', '3'] } }
  expect(statusLabel(s, 96)).toContain('已交接 05:37,可 /clear')
  expect(statusLabel(s, 96)).not.toContain('該交接')
  const t = promptSection(s, 96)
  expect(t).toContain('這一輪已交接')
  expect(t).not.toContain('不可跳過')
})

test('no receipt (or receipt unreadable) at 85% still demands the handoff', () => {
  expect(handoffState({ ...base, handoff: { done: false, at: null, file: null, steps: [] } }, 90)).toBe('due')
  expect(handoffState({ ...base, handoff: null }, 90)).toBe('due')
  expect(handoffState({ ...base, handoff: null }, 40)).toBe('none')
  expect(promptSection({ ...base, handoff: null }, 90)).toContain('不可跳過')
})

test('guard blocks: only unseen ones from the last 30 minutes', () => {
  const now = Date.parse('2026-10-09T06:10:00+08:00')
  const b = (ts: string) => ({ ts, gate: '回報閘', why: '回報檔 lint 紅', first: 'x', n: 1 })
  const list = [b('2026-10-08T01:59:38+08:00'), b('2026-10-09T06:00:00+08:00'), b('2026-10-09T06:05:00+08:00')]
  expect(newBlocks(list, '', now).map(x => x.ts)).toEqual(['2026-10-09T06:00:00+08:00', '2026-10-09T06:05:00+08:00'])
  expect(newBlocks(list, '2026-10-09T06:00:00+08:00', now).length).toBe(1)
  expect(newBlocks(undefined, '', now)).toEqual([])
})

test('captain taken from the right-pane crown says so', () => {
  const t = promptSection({ ...base, captain: '04e0a', captain_addr: '4f8d/04e0a', captain_src: 'crown' }, null)
  expect(t).toContain('艦長 dm 地址:4f8d/04e0a(依右欄 👑)')
})
