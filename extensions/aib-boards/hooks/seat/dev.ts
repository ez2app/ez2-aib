// dev 視角:我的工作樹(分支名或路徑含席碼)改了什麼、對主線領先/落後、沒 commit 的檔、這棵樹最近一次 aib test 的結果。
// 純函式,資料由 seat-info.py --dev 量好。
import type { Alert } from './captain'
import { tr } from '../i18n'

export type DevTests = { green: number; red: number; rows: number; stale?: number; last: string | null; red_names?: string[] }
export type DevTree = { path: string; branch: string; ahead: number | null; behind: number | null; dirty: number; dirty_files: string[]
  files: number; ins: number; dels: number; last_age_s: number | null; subject: string; tests: DevTests }
export type DevInfo = { trees: DevTree[]; err: string[] }

export function devAlerts(d: DevInfo): Alert[] {
  const out: Alert[] = []
  for (const t of d.trees) {
    const name = t.branch === 'HEAD' ? t.path.split('/').pop() ?? t.path : t.branch
    if (t.tests.red > 0) out.push({ level: 'red', code: name, text: tr('seat_d_red', { name, n: t.tests.red, items: (t.tests.red_names ?? []).join(tr('seat_sep')) }) })
    if ((t.ahead ?? 0) > 0 && t.tests.rows === 0)
      out.push({ level: 'amber', code: name, text: (t.tests.stale ?? 0) > 0
        ? tr('seat_d_stale', { name, n: t.tests.stale ?? 0 })
        : tr('seat_d_untested', { name, n: t.ahead ?? 0 }) })
    if (t.dirty > 0) out.push({ level: 'amber', code: name, text: tr('seat_d_dirty', { name, n: t.dirty, items: t.dirty_files.slice(0, 3).join(tr('seat_sep')) + (t.dirty > 3 ? '…' : '') }) })
    if ((t.behind ?? 0) > 20) out.push({ level: 'info', code: name, text: tr('seat_d_behind', { name, n: t.behind ?? 0 }) })
  }
  if (d.trees.length === 0) out.push({ level: 'info', code: '-', text: tr('seat_d_none') })
  const rank = { red: 0, amber: 1, info: 2 } as const
  return out.sort((a, b) => rank[a.level] - rank[b.level])
}

export function devPromptLines(d: DevInfo, alerts: Alert[]): string[] {
  const trees = d.trees.map(t => tr('seat_d_tree', { branch: t.branch, v1: t.ahead ?? '?', v2: t.behind ?? '?', files: t.files, v3: t.ins, v4: t.dels,
    v5: t.dirty ? tr('seat_d_unsaved', { n: t.dirty }) : '', green: t.tests.green, red: t.tests.red, v6: t.tests.stale ? tr('seat_d_old', { n: t.tests.stale }) : '' }))
  const lines = [tr('seat_d_head', { n: d.trees.length, items: trees.join(tr('seat_sep2')) || tr('seat_none') })]
  const act = alerts.filter(a => a.level !== 'info').slice(0, 5)
  if (act.length) { lines.push(tr('seat_todo')); for (const a of act) lines.push(`  - ${a.text}`) }
  else lines.push(tr('seat_todo_none'))
  lines.push(tr('seat_d_rule'))
  return lines
}
