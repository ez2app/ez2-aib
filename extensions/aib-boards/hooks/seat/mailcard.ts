// 新信卡片(the maintainer 10-14「新信彈窗重做」:狀態色、完整換行、可點開、不自動關;「彈窗要放大皇冠、⚓、👔、🧞 這種角色 icon 當成圖像」
//   「所有的角色都要有」)。toast 只能一行純文字、幾秒就關 ⇒ 改畫在輸入框上方那一條(AbovePrompt),關掉要人按 ✕。
//   這裡只放純函式(測試直接載);畫面在 register.tsx。

export type MailStatus = 'unread' | 'read' | 'done' | 'held' | 'archived'
export type MailCard = { id: string; from: string | null; label: string | null; kind: string | null; at: number; text: string; status: MailStatus; open: boolean }
export type QItem = { id?: string; seat?: string; sender?: string; sender_seat?: string | null; sender_label?: string | null; sender_kind?: string | null
  kind?: string | null; msg?: string | null; ts?: number | null; sent_at?: number | null; read_at?: unknown; done_at?: unknown; held_at?: unknown
  archived_at?: unknown; superseded_by?: unknown; deleted_at?: unknown }
export type Icon = { w: number; h: number; px: number[][] }

// 狀態字走字典 mod.aib_boards.st_*(Claude 面板 seatcore.tsx 與小窗格 tui 同一組鍵);這裡只放鍵與顏色
export const STATUS: Record<MailStatus, { key: string; color: string }> = {
  unread: { key: 'st_unread', color: 'error' }, read: { key: 'st_read_open', color: 'warning' }, done: { key: 'st_done', color: 'success' },
  held: { key: 'st_held', color: 'permission' }, archived: { key: 'st_archived', color: 'inactive' } }

export function statusOf(q: QItem): MailStatus {
  if (q.done_at) return 'done'
  if (q.archived_at || q.superseded_by || q.deleted_at) return 'archived'
  if (q.held_at) return 'held'
  return q.read_at ? 'read' : 'unread'
}

// 信件檔 → 卡片;寄件人是自己(寄給自己的回條)不做卡
export function cardOf(q: QItem, me: string | null, full?: string | null): MailCard | null {
  if (!q.id || (me && q.sender_seat === me)) return null
  return { id: q.id, from: q.sender_seat ?? null, label: q.sender_label ?? q.sender ?? null, kind: q.sender_kind ?? null,
    at: Number(q.sent_at ?? q.ts ?? 0), text: String(full ?? q.msg ?? ''), status: statusOf(q), open: false }
}

// 收到新的一份信件檔:已有的卡只更新狀態與內容(開合照舊);沒有的卡只在「沒讀、沒結、沒被人關過」時才新增,新的在前,最多 max 張
export function upsert(cards: MailCard[], c: MailCard, dismissed: ReadonlySet<string>, max = 8): MailCard[] {
  const i = cards.findIndex(x => x.id === c.id)
  if (i >= 0) return cards.map((x, k) => (k === i ? { ...c, open: x.open } : x))
  if (dismissed.has(c.id) || c.status !== 'unread') return cards
  return [c, ...cards].sort((a, b) => b.at - a.at).slice(0, max)
}

// 長訊落檔(「長訊已落檔 … 請讀:<路徑>」)⇒ 回路徑,卡片展開時讀全文
export function relayPath(msg: string, home: string | null): string | null {
  const m = /請讀[::]\s*(\S+)/.exec(msg)
  if (!m || !home) return null
  return m[1]!.startsWith(home.replace(/\/$/, '') + '/') ? m[1]! : null
}

// 圖示一律用右欄 AI 清單那一格的(seat-info 讀 ai-rowmap.json 的 mark:👑 / ⚓ / 🤖 / 👔 / 🧞 …,the maintainer 10-14「右邊的 ai 欄早就訂好所有 icon」);
//   不在右欄的寄件人(真人、系統、別艙)用右欄自己的「未列角色」圖示 👤(pane-right-ai.sh UNLISTED_ROLE_ICON),不另訂
export const UNLISTED_ICON = '\u{1F464}'
export function iconOf(c: Pick<MailCard, 'from'>, marks?: Record<string, string> | null): string {
  return (c.from ? marks?.[c.from] : null) || UNLISTED_ICON
}

const cw = (ch: string) => /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]|\p{Extended_Pictographic}/u.test(ch) ? 2 : 1
// 收起時只看前 lines 行:照格寬切(中文兩格),換行符號也算一行;切掉的補 …
export function clip(text: string, width: number, lines: number): { text: string; cut: boolean } {
  const rows: string[] = []
  for (const para of text.split('\n')) {
    let cur = '', w = 0
    for (const ch of para) { const n = cw(ch); if (w + n > width) { rows.push(cur); cur = ''; w = 0 } cur += ch; w += n }
    rows.push(cur)
    if (rows.length > lines) break
  }
  if (rows.length <= lines) return { text: rows.join('\n'), cut: false }
  const keep = rows.slice(0, lines)
  keep[lines - 1] = keep[lines - 1]!.replace(/.{0,1}$/u, '…')
  return { text: keep.join('\n'), cut: true }
}

// 像素圖 → Raster 格子:一格上下兩個像素(▀ 前景 = 上、背景 = 下);透明的用終端機預設色。h 是奇數就補一列透明
const DEFAULT = 0x01000000
export function rasterCells(icon: Icon, alphaMin = 64): { columns: number; rows: number; cells: string } {
  const { w, h, px } = icon, rows = Math.ceil(h / 2), words = new Uint32Array(w * rows * 3)
  const col = (p?: number[]) => (p && p[3]! >= alphaMin ? ((p[0]! << 16) | (p[1]! << 8) | p[2]!) >>> 0 : DEFAULT)
  for (let r = 0; r < rows; r++) for (let x = 0; x < w; x++) {
    const top = col(px[(r * 2) * w + x]), bot = col(r * 2 + 1 < h ? px[(r * 2 + 1) * w + x] : undefined), k = (r * w + x) * 3
    if (top === DEFAULT && bot === DEFAULT) { words[k] = 0x20; words[k + 1] = DEFAULT; words[k + 2] = DEFAULT }
    else if (top === DEFAULT) { words[k] = 0x2584; words[k + 1] = bot; words[k + 2] = DEFAULT }   // ▄ 只有下半
    else { words[k] = 0x2580; words[k + 1] = top; words[k + 2] = bot }                           // ▀ 上半前景、下半背景
  }
  return { columns: w, rows, cells: b64(new Uint8Array(words.buffer)) }
}
function b64(u: Uint8Array): string {
  const t = u as Uint8Array & { toBase64?: () => string }
  if (typeof t.toBase64 === 'function') return t.toBase64()
  let s = ''; for (const b of u) s += String.fromCharCode(b)
  return btoa(s)
}
