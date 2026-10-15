// 整合席視角(the maintainer 10-09「推完再做整合席的畫面」):待併的包、推包閘門、機器鎖、主線。純函式,資料由 seat-info.py --integrator 量好。
import type { Alert } from './captain'
import { tr } from '../i18n'

export type Pkg = { path: string; branch: string; ahead: number | null; behind: number | null; dirty: number; last_age_s: number | null; subject: string }
export type GateRun = { time: string; day: string; rc: number; ran: number; total: number; wall_s: number; early_stop: boolean }
export type Lease = { state: string | null; owner: string | null; what: string | null; acquired_at: string | null; pid: number | null; queued: string[] }
export type IntegInfo = {
  repo: string | null; main_remote: string | null
  main: { sha: string; age_s: number; subject: string } | null
  packages: Pkg[]; gate: GateRun[]; lease: Lease | null
  ship_running: { pid: number; elapsed_s: number; branch: string | null }[]; err: string[]
}

export function agoS(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return '?'
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`
  return `${(sec / 86400).toFixed(1)}d`
}

/** 租約拿了多久(秒);時間字串認不得回 null。 */
export function leaseAge(l: Lease | null, nowMs: number): number | null {
  if (!l?.acquired_at) return null
  const t = Date.parse(l.acquired_at)
  return Number.isNaN(t) ? null : Math.max(0, Math.round((nowMs - t) / 1000))
}

export function gateVerdict(g: GateRun): 'green' | 'red' | 'early_stop' | 'unverified' {
  if (g.rc === 0) return 'green'
  if (g.rc === 5 || g.early_stop) return 'early_stop'
  if (g.rc === 4) return 'unverified'
  return 'red'
}

/** 要整合席處理的事,紅的在前。me = 本席席碼(租約是自己的就不算別人佔著)。 */
export function integAlerts(i: IntegInfo, me: string | null, nowMs: number): Alert[] {
  const out: Alert[] = []
  const l = i.lease
  const la = leaseAge(l, nowMs)
  if (l?.state === 'held' && l.owner && !(me && l.owner.endsWith(me)) && la !== null && la > 3600)
    out.push({ level: 'amber', code: l.owner, text: tr('seat_i_lease', { who: l.owner, ago: agoS(la), what: l.what ?? '?' }) })
  for (const r of i.ship_running) out.push({ level: 'info', code: r.branch ?? '?', text: tr('seat_i_ship', { ago: agoS(r.elapsed_s), branch: r.branch ? ` · ${r.branch}` : '' }) })
  const last = i.gate[i.gate.length - 1]
  if (last && gateVerdict(last) === 'red') out.push({ level: 'red', code: 'gate', text: tr('seat_i_gate_red', { d: last.day, ts: last.time, ran: last.ran, total: last.total }) })
  for (const p of i.packages) {
    const name = p.branch === 'HEAD' ? p.path.split('/').pop() ?? p.path : p.branch
    if (p.dirty > 0 && (p.ahead ?? 0) > 0) out.push({ level: 'amber', code: name, text: tr('seat_i_dirty', { name, v1: p.ahead ?? 0, v5: p.dirty }) })
    if ((p.behind ?? 0) > 20 && (p.ahead ?? 0) > 0) out.push({ level: 'amber', code: name, text: tr('seat_i_behind', { name, n: p.behind ?? 0 }) })
    if ((p.ahead ?? 0) > 0 && p.last_age_s !== null && p.last_age_s > 3 * 86400) out.push({ level: 'info', code: name, text: tr('seat_i_cold', { name, ago: agoS(p.last_age_s) }) })
  }
  const rank = { red: 0, amber: 1, info: 2 } as const
  return out.sort((a, b) => rank[a.level] - rank[b.level])
}

/** 給整合席 AI 每回合讀的摘要。 */
export function integPromptLines(i: IntegInfo, alerts: Alert[]): string[] {
  const waiting = i.packages.filter(p => (p.ahead ?? 0) > 0)
  const lines = [tr('seat_i_head', { remote: i.main_remote ?? '?', sha: i.main?.sha ?? '?', ago: agoS(i.main?.age_s), n: waiting.length,
    items: waiting.map(p => `${p.branch} +${p.ahead}`).slice(0, 6).join(tr('seat_sep')) || tr('seat_none'),
    state: i.lease?.state === 'held' ? tr('seat_i_held', { who: i.lease.owner ?? '?' }) : tr('seat_i_free') })]
  const act = alerts.filter(a => a.level !== 'info').slice(0, 5)
  if (act.length) { lines.push(tr('seat_todo')); for (const a of act) lines.push(`  - ${a.text}`) }
  else lines.push(tr('seat_todo_none'))
  lines.push(tr('seat_i_rule'))
  return lines
}
