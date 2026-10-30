import type { FleetRow } from './captain'
import type { FriendReq } from './mailcard'
import type { IntegInfo } from './integrator'
import type { DevInfo } from './dev'
import type { QaInfo } from './qa'
import { tr } from '../i18n'

// 網頁每 8 秒 POST /api/summary 存成 state/captain-room/summary.json:各層人數、等艦長決定、未讀、在線(seat-info 加 age_s)
export type RoomSummary = { f1: number; f2: number; f3: number; street: number; decide: number; unread: number; online: number; at: number; age_s: number; hosts?: number; down?: number }
export type RoomInfo = { url: string | null; port: number | null; cabin: string | null; pid: number | null; alive: boolean
  script: string | null; argv: string[]; thumb: string | null; thumb_age_s: number | null; summary?: RoomSummary | null }
export type SeatInfo = {
  fleet?: FleetRow[] | null
  mod?: { enabled: boolean; why: string; config: string | null } | null
  integ?: IntegInfo | null
  dev?: DevInfo | null
  qa?: QaInfo | null
  bot?: unknown                            // 舊 bot 視角(面板已拿掉),探測可能還帶,不用
  home?: string | null
  room?: RoomInfo | null                   // 艦長室網頁(tools/captain-room):在哪、活著嗎、最新縮圖
  room_installed?: boolean                 // 這份 aib 有沒有艦長室(內建 captain_room.py 或外掛 captain-room);沒有 ⇒ /aib room 只說開發中
  cabin: string | null; cabin_code: string | null; role: string | null; code: string | null; window: string | null
  seat_id: string | null; seat_file: string | null; seat_file_has_uuid: boolean | null
  captain: string | null; captain_addr: string | null; captain_src?: string; roots: string[]
  unread: number | null; unread_list: { id: string; rev: number; sender: string; when: string; msg: string; sender_seat?: string | null; ts?: number | null }[]; logos?: Record<string, string>; logo_colors?: Record<string, string>
  personas?: Record<string, { name?: string | null; role?: string | null }>; marks?: Record<string, string>; home?: string | null
  friend_requests?: FriendReq[]             // 線上好友申請(只有艦長席的探測會帶;新信卡片用)
  handoff?: { done: boolean; at: string | null; file: string | null; steps: string[] } | null
  roster?: { code: string; role: string | null; provider: string | null; window: string | null; idle_s: number | null }[]
  blocks?: { ts: string; gate: string; why: string; first: string; n: number }[]
  dispatch?: { id: string; sender: string; when: string; age_min: number | null; status: string; msg: string } | null; err: string[]
}

export const HANDOFF_PCT = 80   // the maintainer 10-13 17:5x「同意提早到80%」

/** 探測只出機器鍵(Python 端不寫顯示字,過 tools i18n 棘輪);顯示字在字典 seat_lbl_*。認不得的鍵原樣顯示。 */
const LABELS: Record<string, string> = { crown: 'seat_lbl_crown', report_lint: 'seat_lbl_report_lint' }
export function label(key: string | null | undefined): string { return key ? (LABELS[key] ? tr(LABELS[key]!) : key) : '' }

/** 'none' 未到 80%;'due' 到了沒交接;'done' 這一輪已有收據(可 /clear)。收據量不到時當 'due',不當已交。 */
export function handoffState(s: SeatInfo, ctxPercent: number | null): 'none' | 'due' | 'done' {
  if (s.handoff?.done) return 'done'
  return ctxPercent !== null && ctxPercent >= HANDOFF_PCT ? 'due' : 'none'
}

function handoffTag(s: SeatInfo, ctxPercent: number | null): string {
  const h = handoffState(s, ctxPercent)
  return h === 'done' ? tr('seat_ho_done', { at: s.handoff?.at ? ' ' + s.handoff.at : '' }) : h === 'due' ? tr('seat_ho_due') : ''
}

function dmLabel(s: SeatInfo): string {
  return s.unread === null ? tr('seat_st_dm_na') : s.unread > 0 ? tr('seat_st_dm_unread', { n: s.unread }) : 'dm 0'
}

export function statusLabel(s: SeatInfo, ctxPercent: number | null): string {
  const na = tr('seat_na')
  const ctx = (ctxPercent === null ? `CTX ${na}` : `CTX ${Math.round(ctxPercent)}%`) + handoffTag(s, ctxPercent)
  return tr('seat_st_line', { context: ctx, instance: s.cabin_code ?? na, role: s.role ?? tr('seat_unbound'), code: s.code ?? na, msg: dmLabel(s) })
}

/** 10-28 Andy 工作單 S:接在 💧 狀態列尾巴的那一段 —— 未讀 dm + 交接標記(只在有時)。CTX、席碼那條已經有,艙號 Andy 沒列 ⇒ 不帶。
 *  #401 R1:交接標記放最前面(窄終端截長行時從尾端截,先保住它) */
export function tailLabel(s: SeatInfo, ctxPercent: number | null): string {
  const tag = handoffTag(s, ctxPercent).trim()
  return tag ? `${tag} · ${dmLabel(s)}` : dmLabel(s)
}

/** src = 誰量的(Claude 席是 aib-boards mod;非 Claude 席是 aib-seat-hooks 外掛) */
export function promptSection(s: SeatInfo, ctxPercent: number | null, src = 'aib-boards mod'): string {
  const na = tr('seat_na')
  const captain = s.captain_addr ? s.captain_addr + (s.captain_src ? tr('seat_p_captain_src', { src: label(s.captain_src) }) : '')
    : s.roots.length ? tr('seat_p_roots', { n: s.roots.length, items: s.roots.map(r => `${s.cabin_code}/${r}`).join(tr('seat_sep')) }) : tr('seat_p_no_root')
  const lines = [
    tr('seat_p_head', { src }),
    tr('seat_p_seat', { instance: s.cabin ?? na, c: s.cabin_code ?? na, role: s.role ?? tr('seat_unbound'), code: s.code ?? na, window: s.window ?? na, id: s.seat_id ?? na }),
    tr('seat_p_captain', { target: captain }),
  ]
  if (s.seat_file_has_uuid === false) lines.push(tr('seat_p_no_uuid'))
  if (s.unread === null) lines.push(tr('seat_p_inbox_na'))
  else if (s.unread > 0) {
    lines.push(tr('seat_p_inbox_unread', { n: s.unread, code: s.code ?? na }))
    for (const m of s.unread_list) lines.push(`  - ${m.id} rev=${m.rev} ${m.when} ${m.sender}:${m.msg}`)
  } else lines.push(tr('seat_p_inbox_zero'))
  const h = handoffState(s, ctxPercent)
  if (h === 'done') lines.push(tr('seat_p_done', { at: s.handoff?.at ?? '?', file: s.handoff?.file ?? '' }))
  else if (h === 'due') lines.push(tr('seat_p_due', { pct: Math.round(ctxPercent ?? 0), limit: HANDOFF_PCT }))
  return lines.join('\n')
}

/** 還沒報過的擋下紀錄:比 seen 新、而且 30 分鐘內(舊的不在開席時一口氣倒進事件流)。ts 是 ISO 字串,可直接比。 */
export function newBlocks(blocks: SeatInfo['blocks'], seen: string, nowMs: number): NonNullable<SeatInfo['blocks']> {
  return (blocks ?? []).filter(b => b.ts > seen && nowMs - Date.parse(b.ts) < 30 * 60_000)
}
