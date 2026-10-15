import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { statusOf, cardOf, upsert, relayPath, iconOf, clip, rasterCells, UNLISTED_ICON } from './mailcard'

const q = { id: 'q_1', seat: '86ff0', sender_seat: '8f366', sender_label: '@8f366(第二艦長)', sender_kind: 'seat', msg: '機器空了', sent_at: 100 }

test('狀態:未讀 / 已讀未結 / 完成 / 卡住 / 封存', () => {
  expect(statusOf(q)).toBe('unread')
  expect(statusOf({ ...q, read_at: 1 })).toBe('read')
  expect(statusOf({ ...q, read_at: 1, done_at: 2 })).toBe('done')
  expect(statusOf({ ...q, held_at: 1 })).toBe('held')
  expect(statusOf({ ...q, superseded_by: 'q_2' })).toBe('archived')
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

test('像素圖 → 半格方塊:上下兩像素一格,透明用預設色', () => {
  const R = [255, 0, 0, 255], T = [0, 0, 0, 0], B = [0, 0, 255, 255]
  const out = rasterCells({ w: 2, h: 2, px: [R, T, B, T] })
  expect(out.columns).toBe(2)
  expect(out.rows).toBe(1)
  const bin = atob(out.cells), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i)
  const w = new Uint32Array(u.buffer)
  expect([w[0], w[1], w[2]]).toEqual([0x2580, 0xff0000, 0x0000ff])
  expect([w[3], w[4], w[5]]).toEqual([0x20, 0x01000000, 0x01000000])
})
