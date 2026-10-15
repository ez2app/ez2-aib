// 艦長視角(the maintainer 10-09「先做艦長的分角色畫面」):全艙每一席的 CTX、未讀、交接、閒置、手上派工,以及要處理的事。
// 純函式:資料由 seat-info.py --fleet 量好,這裡只判斷與排版。
export type FleetRow = {
  code: string; role: string | null; provider: string | null; window: string | null; idle_s: number | null
  ctx: number | null; ctx_stale: boolean | null; unread: number | null
  handoff: { done: boolean; steps: string[] } | null
  dispatch: { sender: string; age_min: number | null; msg: string } | null
}
export type Alert = { level: 'red' | 'amber' | 'info'; code: string; text: string }

import { HANDOFF_PCT } from './seat-label'   // 單一來源(the maintainer 10-13 17:5x 改 80%)
import { tr } from '../i18n'
// statusline 寫 .pct 的引擎;bot 席(grokbot / musebot / claudebot)量不到 CTX 是正常的,不報
const CTX_PROVIDERS = new Set(['claude', 'codex', 'grok', 'agy'])

export function ago(sec: number | null): string {
  if (sec === null) return '?'
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  return `${(sec / 3600).toFixed(1)}h`
}

/** 要艦長處理的事,紅的在前。me = 艦長自己的席碼(自己的事也列,但標「你」)。 */
export function fleetAlerts(rows: FleetRow[], me: string | null): Alert[] {
  const out: Alert[] = []
  for (const r of rows) {
    const who = r.code === me ? tr('seat_you', { code: r.code }) : r.code
    if (r.ctx !== null && r.ctx >= HANDOFF_PCT && !r.handoff?.done)
      out.push({ level: 'red', code: r.code, text: tr('seat_f_ctx', { who, pct: Math.round(r.ctx) }) })
    if ((r.unread ?? 0) >= 3) out.push({ level: 'amber', code: r.code, text: tr('seat_f_unread', { who, n: r.unread ?? 0 }) })
    if (r.idle_s !== null && r.idle_s > 2 * 3600 && r.dispatch)
      out.push({ level: 'amber', code: r.code, text: tr('seat_f_idle', { who, ago: ago(r.idle_s), sender: r.dispatch.sender.replace(/\(.*\)/, '') }) })
    if (r.ctx_stale) out.push({ level: 'info', code: r.code, text: tr('seat_f_stale', { who }) })
    if (r.ctx === null && CTX_PROVIDERS.has(r.provider ?? '')) out.push({ level: 'info', code: r.code, text: tr('seat_f_ctx_na', { who }) })
  }
  const rank = { red: 0, amber: 1, info: 2 } as const
  return out.sort((a, b) => rank[a.level] - rank[b.level])
}

/** 表格順序:有紅的 → 有黃的 → 其他;同級依最近有動靜排前面。 */
export function fleetOrder(rows: FleetRow[], alerts: Alert[]): FleetRow[] {
  const worst = new Map<string, number>()
  for (const a of alerts) worst.set(a.code, Math.min(worst.get(a.code) ?? 9, a.level === 'red' ? 0 : a.level === 'amber' ? 1 : 2))
  return [...rows].sort((a, b) => (worst.get(a.code) ?? 9) - (worst.get(b.code) ?? 9) || (a.idle_s ?? 1e9) - (b.idle_s ?? 1e9))
}

/** 給艦長 AI 每回合讀的摘要(最多 5 條要處理的事)。 */
export function fleetPromptLines(rows: FleetRow[], alerts: Alert[]): string[] {
  const busy = rows.filter(r => r.idle_s !== null && r.idle_s < 600).length
  const lines = [tr('seat_f_head', { n: rows.length, count: busy })]
  const act = alerts.filter(a => a.level !== 'info').slice(0, 5)
  if (act.length) { lines.push(tr('seat_todo')); for (const a of act) lines.push(`  - ${a.text}`) }
  else lines.push(tr('seat_f_none'))
  return lines
}
