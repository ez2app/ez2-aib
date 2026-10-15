import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { integAlerts, integPromptLines, gateVerdict, leaseAge, agoS, type IntegInfo, type Pkg, type GateRun } from './integrator'

const pkg = (o: Partial<Pkg>): Pkg => ({ path: '/w/x', branch: 'feat/x', ahead: 1, behind: 0, dirty: 0, last_age_s: 600, subject: 's', ...o })
const gate = (o: Partial<GateRun>): GateRun => ({ time: '08:36', day: '10-09', rc: 0, ran: 22, total: 997, wall_s: 750, early_stop: false, ...o })
const base = (o: Partial<IntegInfo>): IntegInfo => ({ repo: '/r', main_remote: 'github', main: { sha: 'bf623e7', age_s: 3600, subject: 'docs' },
  packages: [], gate: [], lease: { state: 'free', owner: null, what: null, acquired_at: null, pid: null, queued: [] }, ship_running: [], err: [], ...o })
const NOW = Date.parse('2026-10-09T09:00:00+08:00')

test('gate verdict: rc 0 green, 5 early stop, 4 unverified, else red', () => {
  expect(gateVerdict(gate({ rc: 0 }))).toBe('green'); expect(gateVerdict(gate({ rc: 5 }))).toBe('early_stop')
  expect(gateVerdict(gate({ rc: 4 }))).toBe('unverified'); expect(gateVerdict(gate({ rc: 1 }))).toBe('red')
  expect(agoS(90000)).toBe('1.0d'); expect(agoS(null)).toBe('?')
})

test('alerts: lease held by someone else over an hour is amber; my own lease is not', () => {
  const held = { state: 'held', owner: '4f8d/3d156', what: 'aib test', acquired_at: '2026-10-09T07:00:00+08:00', pid: 1, queued: [] }
  expect(leaseAge(held, NOW)).toBe(7200)
  expect(integAlerts(base({ lease: held }), '86ff0', NOW).map(a => a.level)).toEqual(['amber'])
  expect(integAlerts(base({ lease: { ...held, owner: '4f8d/86ff0' } }), '86ff0', NOW)).toEqual([])
})

test('alerts: last gate red is red; dirty or far-behind packages are amber; cold packages info', () => {
  const i = base({ gate: [gate({ rc: 0 }), gate({ rc: 1 })],
    packages: [pkg({ branch: 'a', dirty: 2 }), pkg({ branch: 'b', behind: 61 }), pkg({ branch: 'c', last_age_s: 4 * 86400 }), pkg({ branch: 'd' })] })
  const a = integAlerts(i, null, NOW)
  expect(a.map(x => [x.level, x.code])).toEqual([['red', 'gate'], ['amber', 'a'], ['amber', 'b'], ['info', 'c']])
  expect(a[2]!.text).toContain('rebase')
})

test('prompt summary names waiting packages, lease holder, and the ship rule', () => {
  const i = base({ packages: [pkg({ branch: 'feat/x', ahead: 2 }), pkg({ branch: 'main', ahead: 0, dirty: 1 })],
    lease: { state: 'held', owner: '4f8d/3d156', what: 'x', acquired_at: null, pid: 1, queued: [] } })
  const t = integPromptLines(i, integAlerts(i, null, NOW)).join('\n')
  expect(t).toContain('待併 1 包(feat/x +2)'); expect(t).toContain('4f8d/3d156 持有'); expect(t).toContain('要處理:無')
  expect(t).toContain('ship.py package 全綠才推')
})
