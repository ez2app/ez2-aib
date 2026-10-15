import { test, expect } from 'claude-code/testing'
import { FIXTURE } from '../i18n-fixture'
import { useT, makeT } from '../i18n'
// 字走 tr();斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { devAlerts, devPromptLines, type DevTree } from './dev'

const tree = (o: Partial<DevTree>): DevTree => ({ path: '/w/x', branch: 'feat/x-86ff0-1009', ahead: 2, behind: 0, dirty: 0, dirty_files: [],
  files: 21, ins: 2043, dels: 0, last_age_s: 600, subject: 's', tests: { green: 10, red: 0, rows: 10, last: '2026-10-09 08:52' }, ...o })

test('dev alerts: red tests first, untested commits and dirty files amber, far behind info', () => {
  const a = devAlerts({ trees: [
    tree({ branch: 'a', tests: { green: 3, red: 2, rows: 5, last: null, red_names: ['test-x.py', 'test-y.py'] } }),
    tree({ branch: 'b', tests: { green: 0, red: 0, rows: 0, stale: 73, last: null } }),
    tree({ branch: 'c', dirty: 2, dirty_files: ['f1', 'f2'] }),
    tree({ branch: 'd', behind: 30 }),
  ], err: [] })
  expect(a.map(x => [x.level, x.code])).toEqual([['red', 'a'], ['amber', 'b'], ['amber', 'c'], ['info', 'd']])
  expect(a[0]!.text).toContain('test-x.py、test-y.py')
  expect(a[1]!.text).toContain('73 筆測試結果比最後一個 commit 舊')
})

test('dev alerts: no trees says so; prompt summary carries diff stats and the rules', () => {
  expect(devAlerts({ trees: [], err: [] })[0]!.text).toContain('沒有')
  const d = { trees: [tree({})], err: [] }
  const t = devPromptLines(d, devAlerts(d)).join('\n')
  expect(t).toContain('feat/x-86ff0-1009 +2/-0 改 21 檔(+2043/−0) 測試 10綠/0紅')
  expect(t).toContain('要處理:無'); expect(t).toContain('包交整合席')
})
