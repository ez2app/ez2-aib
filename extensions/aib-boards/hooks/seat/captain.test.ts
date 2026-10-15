import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { fleetAlerts, fleetOrder, fleetPromptLines, ago, type FleetRow } from './captain'

const row = (o: Partial<FleetRow>): FleetRow => ({ code: 'aaaaa', role: 'root', provider: 'claude', window: '@2', idle_s: 30,
  ctx: 40, ctx_stale: false, unread: 0, handoff: { done: false, steps: [] }, dispatch: null, ...o })

test('alerts: over the handoff threshold (80%) without receipt is red; with receipt it is not', () => {
  const a = fleetAlerts([row({ code: '04e0a', ctx: 91 }), row({ code: '3d156', ctx: 92, handoff: { done: true, steps: ['1', '2', '3'] } })], null)
  expect(a.map(x => [x.level, x.code])).toEqual([['red', '04e0a']])
  expect(a[0]!.text).toContain('91%')
})

test('alerts: mail pile, idle with dispatch, stale, and bots without CTX are not reported', () => {
  const a = fleetAlerts([
    row({ code: 'mail1', unread: 3 }),
    row({ code: 'idle1', idle_s: 3 * 3600, dispatch: { sender: '@04e0a(AndyAI分身)', age_min: 200, msg: 'x' } }),
    row({ code: 'stal1', ctx_stale: true }),
    row({ code: 'bot01', provider: 'musebot', ctx: null }),
    row({ code: 'cli01', provider: 'claude', ctx: null }),
  ], 'mail1')
  expect(a.map(x => x.code)).toEqual(['mail1', 'idle1', 'stal1', 'cli01'])
  expect(a[0]!.text).toContain('mail1(你)')
  expect(a[1]!.text).toContain('@04e0a 的派工')
})

test('table order: red, amber, then most recently active', () => {
  const rows = [row({ code: 'quiet', idle_s: 900 }), row({ code: 'busyy', idle_s: 5 }), row({ code: 'hotct', ctx: 90, idle_s: 3000 })]
  expect(fleetOrder(rows, fleetAlerts(rows, null)).map(r => r.code)).toEqual(['hotct', 'busyy', 'quiet'])
})

test('prompt summary lists actionable alerts only, or says none', () => {
  const rows = [row({ code: 'hotct', ctx: 90 }), row({ code: 'stal1', ctx_stale: true })]
  const t = fleetPromptLines(rows, fleetAlerts(rows, null)).join('\n')
  expect(t).toContain('本艙 2 席'); expect(t).toContain('hotct'); expect(t).not.toContain('stal1')
  expect(fleetPromptLines([row({})], []).join('\n')).toContain('要處理:無')
  expect(ago(null)).toBe('?'); expect(ago(5400)).toBe('1.5h')
})
