import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { qaAlerts, qaPromptLines, type QaInfo } from './qa'

const q = (o: Partial<QaInfo>): QaInfo => ({ deliveries: [], unverified: [], reviews: [], err: [], ...o })

test('qa alerts: unread delivery red, old read delivery amber, fail cells red, unmeasured amber', () => {
  const a = qaAlerts(q({
    deliveries: [
      { id: 'd1', kind: 'deliver', sender: '@3d156(AndyAI分身)', when: '10-09 08:00', age_min: 30, read: false, msg: 'x' },
      { id: 'd2', kind: 'report', sender: '@04e0a', when: '10-09 02:00', age_min: 400, read: true, msg: 'y' },
      { id: 'd3', kind: 'deliver', sender: '@04e0a', when: '10-09 08:30', age_min: 10, read: true, msg: 'z' },
    ],
    unverified: [{ branch: 'feat/x', tests: 161, unmeasured: 91, fail_cells: 2, unmeasured_names: ['a.py'], fail_names: ['b.py', 'c.py'] }],
  }))
  expect(a.map(x => [x.level, x.code])).toEqual([['red', 'd1'], ['red', 'feat/x'], ['amber', 'd2'], ['amber', 'feat/x']])
  expect(a[0]!.text).toContain('@3d156 交來的包還沒讀')
  expect(a[2]!.text).toContain('回報讀了 7 小時還沒結案')
  expect(a[3]!.text).toContain('91/161 支「沒驗」')
})

test('qa prompt summary counts and carries the rule that skipped is not passed', () => {
  const info = q({ deliveries: [{ id: 'd1', kind: 'deliver', sender: '@x', when: 'w', age_min: 1, read: false, msg: 'm' }],
    unverified: [{ branch: 'b', tests: 10, unmeasured: 3, fail_cells: 0, unmeasured_names: [], fail_names: [] }], reviews: [{ file: 'XREVIEW-by-86ff0.md', age_s: 100 }] })
  const t = qaPromptLines(info, qaAlerts(info)).join('\n')
  expect(t).toContain('交給你驗的 1 件(未讀 1)'); expect(t).toContain('沒驗的格子 3 支'); expect(t).toContain('審查檔 1 份')
  expect(t).toContain('跳過 = 沒驗')
  expect(qaPromptLines(q({}), []).join('\n')).toContain('要處理:無')
})
