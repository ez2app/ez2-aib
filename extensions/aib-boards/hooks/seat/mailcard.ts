// 新信卡片(the maintainer 10-14「新信彈窗重做」:狀態色、完整換行、可點開、不自動關;「彈窗要放大皇冠、⚓、👔、🧞 這種角色 icon 當成圖像」
//   「所有的角色都要有」)。toast 只能一行純文字、幾秒就關 ⇒ 改畫在輸入框上方那一條(AbovePrompt),關掉要人按 ✕。
//   Andy 10-17:卡片只彈「真人來信」與「加好友」(席與席之間的信照舊進佇列與狀態列,不彈);像素大頭像拿掉,圖示放回標題列。
//   這裡只放純函式(測試直接載);畫面在 register.tsx。

export type MailStatus = 'unread' | 'read' | 'done' | 'held' | 'archived'
export type MailCard = { id: string; from: string | null; label: string | null; kind: string | null; at: number; text: string; status: MailStatus; open: boolean }
export type QItem = { id?: string; seat?: string; sender?: string; sender_seat?: string | null; sender_label?: string | null; sender_kind?: string | null
  kind?: string | null; msg?: string | null; ts?: number | null; sent_at?: number | null; read_at?: unknown; done_at?: unknown; held_at?: unknown
  archived_at?: unknown; superseded_by?: unknown; deleted_at?: unknown }
export type FriendReq = { owner?: string | null; name?: string | null; country?: string | null; note?: string | null; at?: number | null; msgid?: string | null }

// 真人來信:本機真人親打(human)與主人手機(owner_paired);其他(席、系統、bot、全球來信)不彈卡
export const HUMAN_KINDS: ReadonlySet<string> = new Set(['human', 'owner_paired'])
export const REQ_PREFIX = 'req:'

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

// 信件檔 → 卡片;寄件人是自己(寄給自己的回條)或不是真人 ⇒ 不做卡
export function cardOf(q: QItem, me: string | null, full?: string | null): MailCard | null {
  if (!q.id || (me && q.sender_seat === me) || !HUMAN_KINDS.has(String(q.sender_kind ?? ''))) return null
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

// 好友申請 → 卡片(kind = 'request');一位主人一張,id = req:<主人 npub>(提示列的同意指令要用)
export function requestCard(r: FriendReq): MailCard | null {
  if (!r.owner || !/^npub1[02-9ac-hj-np-z]{58}$/.test(r.owner)) return null
  return { id: REQ_PREFIX + r.owner, from: null, label: r.name || `${r.owner.slice(0, 9)}…${r.owner.slice(-4)}`, kind: 'request',
    at: Number(r.at ?? 0), text: String(r.note ?? ''), status: 'unread', open: false }
}

// 申請清單 → 卡片:已處理(同意 / 拒絕,清單裡沒了)的申請卡拿掉;新的照 upsert 規則加(關過的不再出現)
export function syncRequests(cards: MailCard[], reqs: FriendReq[], dismissed: ReadonlySet<string>, max = 8): MailCard[] {
  const fresh = reqs.map(requestCard).filter((c): c is MailCard => !!c), live = new Set(fresh.map(c => c.id))
  let out = cards.filter(c => c.kind !== 'request' || live.has(c.id))
  for (const c of fresh) out = upsert(out, c, dismissed, max)
  return out
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
export const REQUEST_ICON = '\u{1F91D}'   // 🤝 好友申請
export function iconOf(c: Pick<MailCard, 'from'> & { kind?: string | null }, marks?: Record<string, string> | null): string {
  if (c.kind === 'request') return REQUEST_ICON
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
