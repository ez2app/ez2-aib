// S79 F5 第二步(519d0 11-15 派 b433b):席位身份那段多一行「你現在可以先做」,讀 `aib-drive next --seat <席> --json` 的第一列(to = 這一席)。
// 座艙不自己算(不叫 aib-slice),只讀 next。純函式;跑指令那段在 seatcore.tsx。
// 讀不到(沒裝、跑不起來、逾時、輸出不是 JSON、格式不認得)⇒「清單讀不到:<原因>」,不顯示成沒事。
// rc 3 = next 自己說清單不完整(例:tidy_scan 還不支援),JSON 照樣印 ⇒ 有列照列、尾巴標「清單不完整」;沒有 JSON 才算讀不到。
import { tr } from '../i18n'

export const NEXT_SCHEMA = 'aib-drive.next.v1'
export type NextRow = { rank?: number; kind?: string; what?: string; to?: string; why_now?: string }
export type NextSource = { name?: string; state?: string }
export type NextDoc = { schema?: string; complete?: boolean; sources?: NextSource[]; rows?: NextRow[] }
/** 跑一次 next 的結果:missing = 找不到執行檔;spawn = 叫不起來(逾時也算);其餘照 rc / stdout / stderr */
export type NextRun = { missing: true } | { spawn: string } | { exitCode: number | null; stdout: string; stderr: string }

const lastLine = (s: string) => (s ?? '').trim().split('\n').pop()?.trim().slice(0, 80) ?? ''

export function nextLine(seat: string, r: NextRun): string {
  const bad = (why: string) => tr('seat_next_err', { why })
  if ('missing' in r) return bad(tr('seat_next_missing'))
  if ('spawn' in r) return bad(r.spawn.slice(0, 80))
  let doc: NextDoc
  try { doc = JSON.parse(r.stdout) as NextDoc } catch {
    return bad(r.exitCode === 0 ? tr('seat_next_json') : `rc=${r.exitCode ?? '?'} ${lastLine(r.stderr)}`.trim())
  }
  if (!doc || typeof doc !== 'object' || doc.schema !== NEXT_SCHEMA) return bad(tr('seat_next_schema', { schema: String(doc?.schema ?? '?') }))
  if (r.exitCode !== 0 && r.exitCode !== 3) return bad(`rc=${r.exitCode ?? '?'} ${lastLine(r.stderr)}`.trim())
  const rows = Array.isArray(doc.rows) ? doc.rows : []
  const mine = rows.find(x => x && x.to === seat)
  const missing = (Array.isArray(doc.sources) ? doc.sources : []).filter(s => s && s.state !== 'ok').map(s => `${s.name ?? '?'} ${s.state ?? '?'}`)
  const partial = doc.complete === true && r.exitCode === 0 ? '' : tr('seat_next_partial', { items: missing.join(tr('seat_sep')) || `rc=${r.exitCode ?? '?'}` })
  if (!mine) return tr('seat_next_none') + partial
  return tr('seat_next_row', { what: mine.what ?? '?', kind: mine.kind ?? '?', why: mine.why_now ?? '' }) + partial
}
