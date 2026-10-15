// QA / 驗收視角:交給我驗的包(dm kind=deliver/report 沒結案)、我的樹裡沒驗的格子、我最近寫的審查檔。純函式。
import type { Alert } from './captain'
import { tr } from '../i18n'

export type Delivery = { id: string; kind: string; sender: string; when: string; age_min: number | null; read: boolean; msg: string }
export type Unverified = { branch: string; tests: number; unmeasured: number; fail_cells: number; unmeasured_names: string[]; fail_names: string[] }
export type QaInfo = { deliveries: Delivery[]; unverified: Unverified[]; reviews: { file: string; age_s: number }[]; err: string[] }

export function qaAlerts(q: QaInfo): Alert[] {
  const out: Alert[] = []
  for (const d of q.deliveries) {
    const who = d.sender.replace(/\(.*\)/, '')
    const what = tr(d.kind === 'report' ? 'seat_q_report' : 'seat_q_pkg')
    if (!d.read) out.push({ level: 'red', code: d.id, text: tr('seat_q_unread', { who, what, when: d.when }) })
    else if ((d.age_min ?? 0) > 240) out.push({ level: 'amber', code: d.id, text: tr('seat_q_open', { who, what, h: Math.round((d.age_min ?? 0) / 60) }) })
  }
  for (const u of q.unverified) {
    if (u.fail_cells > 0) out.push({ level: 'red', code: u.branch, text: tr('seat_q_red', { branch: u.branch, n: u.fail_cells, items: u.fail_names.join(tr('seat_sep')) }) })
    if (u.unmeasured > 0) out.push({ level: 'amber', code: u.branch, text: tr('seat_q_unmeasured', { branch: u.branch, n: u.unmeasured, total: u.tests }) })
  }
  const rank = { red: 0, amber: 1, info: 2 } as const
  return out.sort((a, b) => rank[a.level] - rank[b.level])
}

export function qaPromptLines(q: QaInfo, alerts: Alert[]): string[] {
  const lines = [tr('seat_q_head', { n: q.deliveries.length, v1: q.deliveries.filter(d => !d.read).length,
    v8: q.unverified.reduce((a, u) => a + u.unmeasured, 0), v7: q.reviews.length })]
  const act = alerts.filter(a => a.level !== 'info').slice(0, 5)
  if (act.length) { lines.push(tr('seat_todo')); for (const a of act) lines.push(`  - ${a.text}`) }
  else lines.push(tr('seat_todo_none'))
  lines.push(tr('seat_q_rule'))
  return lines
}
