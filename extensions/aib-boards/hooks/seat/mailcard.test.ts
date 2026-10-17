import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { statusOf, cardOf, upsert, syncRequests, requestCard, relayPath, iconOf, clip, UNLISTED_ICON, REQUEST_ICON } from './mailcard'

const q = { id: 'q_1', seat: '86ff0', sender_seat: '8f366', sender_label: 'Andy', sender_kind: 'human', msg: '機器空了', sent_at: 100 }

test('狀態:未讀 / 已讀未結 / 完成 / 卡住 / 封存', () => {
  expect(statusOf(q)).toBe('unread')
  expect(statusOf({ ...q, read_at: 1 })).toBe('read')
  expect(statusOf({ ...q, read_at: 1, done_at: 2 })).toBe('done')
  expect(statusOf({ ...q, held_at: 1 })).toBe('held')
  expect(statusOf({ ...q, superseded_by: 'q_2' })).toBe('archived')
})

test('卡片:只做真人來信(human / owner_paired),席、系統、bot、全球來信不做', () => {
  expect(cardOf(q, '86ff0')).not.toBeNull()
  expect(cardOf({ ...q, sender_kind: 'owner_paired' }, '86ff0')).not.toBeNull()
  for (const k of ['seat', 'system', 'other', 'external', 'external_seat', undefined]) expect(cardOf({ ...q, sender_kind: k }, '86ff0')).toBeNull()
})

test('卡片:自己寄的不做;只新增未讀;已有的只換狀態、開合照舊;關過的不再出現;新的在前', () => {
  expect(cardOf({ ...q, sender_seat: '86ff0' }, '86ff0')).toBeNull()
  const c = cardOf(q, '86ff0')!
  expect(c.text).toBe('機器空了')
  expect(upsert([], { ...c, status: 'read' }, new Set())).toEqual([])
  const one = upsert([], c, new Set())
  expect(one.length).toBe(1)
  const opened = one.map(x => ({ ...x, open: true }))
  const after = upsert(opened, { ...c, status: 'done' }, new Set())
  expect(after[0]!.status).toBe('done')
  expect(after[0]!.open).toBe(true)
  expect(upsert([], c, new Set(['q_1']))).toEqual([])
  const two = upsert(one, { ...c, id: 'q_2', at: 200 }, new Set())
  expect(two.map(x => x.id)).toEqual(['q_2', 'q_1'])
})

test('長訊落檔:只認狀態根底下的路徑', () => {
  expect(relayPath('長訊已落檔 請讀:/h/.ez2-aib/state/relay/x.txt', '/h/.ez2-aib')).toBe('/h/.ez2-aib/state/relay/x.txt')
  expect(relayPath('請讀:/etc/passwd', '/h/.ez2-aib')).toBeNull()
  expect(relayPath('沒有路徑', '/h/.ez2-aib')).toBeNull()
})

test('圖示照右欄的 mark;不在右欄的用右欄的未列角色 👤', () => {
  const marks = { '8f366': '👑', '04e0a': '⚓', bd4c1: '🧞' }
  expect(iconOf({ from: '8f366' }, marks)).toBe('👑')
  expect(iconOf({ from: 'bd4c1' }, marks)).toBe('🧞')
  expect(iconOf({ from: null }, marks)).toBe(UNLISTED_ICON)
  expect(iconOf({ from: 'zzzzz' }, marks)).toBe(UNLISTED_ICON)
})

test('收起只留三行,中文算兩格;沒超過就不切', () => {
  expect(clip('短', 10, 3)).toEqual({ text: '短', cut: false })
  const r = clip('一二三四五六七八九十'.repeat(3), 10, 3)
  expect(r.cut).toBe(true)
  expect(r.text.split('\n').length).toBe(3)
  expect(r.text.endsWith('…')).toBe(true)
})

const A = 'npub1' + 'q'.repeat(58), B = 'npub1' + 'p'.repeat(58)
test('好友申請:一位一張;已處理的拿掉;關過的不再出現;位址不對的不做', () => {
  expect(requestCard({ owner: 'npub1bad', name: 'x' })).toBeNull()
  const r = requestCard({ owner: A, name: 'Bob', note: '你好', at: 50 })!
  expect([r.id, r.kind, r.label, r.text, r.status]).toEqual(['req:' + A, 'request', 'Bob', '你好', 'unread'])
  expect(iconOf(r)).toBe(REQUEST_ICON)
  expect(requestCard({ owner: B })!.label).toBe('npub1pppp…pppp')
  const mail = cardOf(q, '86ff0')!
  const two = syncRequests([mail], [{ owner: A, name: 'Bob', at: 300 }, { owner: B, at: 200 }], new Set())
  expect(two.map(c => c.id)).toEqual(['req:' + A, 'req:' + B, 'q_1'])
  expect(syncRequests(two, [{ owner: B, at: 200 }], new Set()).map(c => c.id)).toEqual(['req:' + B, 'q_1'])
  expect(syncRequests([], [{ owner: A }], new Set(['req:' + A]))).toEqual([])
})
