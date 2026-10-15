// 純計算(不碰 $):分頁、待辦分組、燈號顏色、時間。lib.test.ts 測這些。
import type { Screen, Todo, Tab, Level, Know, Charts, Dm, Plan } from '../types'
import { tr } from './i18n'

// 顯示字一律走 tr()(mod.aib_boards.* 鍵,語系由 register.tsx / tui 換進來);label / title 寫成 getter,呼叫端照舊 x.label
const tabOf = (id: Tab, hotkey: string, pkg: string) => ({ id, hotkey, pkg, get label() { return tr(`tab_${id}`) } })
export const TABS: { id: Tab; label: string; hotkey: string; pkg: string }[] = [
  tabOf('today', '1', 'M1'), tabOf('proj', '2', 'M3'), tabOf('dm', '3', 'M4'), tabOf('charts', '4', 'M2'), tabOf('custom', '5', 'M5'),
]
export const CACHE_S = 15   // 跟 seat_screen.py 自己的快取同秒數;打開才算,不輪詢(§21.2)

// 只在用戶端打開(the maintainer 10-14):引擎蓋章的 origin.kind === 'composer' = 真人在終端機按 Enter
export const isHumanTyped = (origin: { kind: string } | undefined) => origin?.kind === 'composer'

// 待辦分組順序(設計稿 §4.2):🔴 現在要做 → ⏳ 在做 → 🙋 等 the maintainer → 👥 等別席 → 📝 自己記的
const grpOf = (id: string, icon: string) => ({ id, icon, get title() { return tr(`grp_${id}`) } })
export const GROUPS: { id: string; icon: string; title: string }[] = [grpOf('now', '🔴'), grpOf('doing', '⏳'), grpOf('human', '🙋'), grpOf('others', '👥'), grpOf('self', '📝')]
export function groupTodos(todo: Todo[]): { id: string; icon: string; title: string; items: Todo[] }[] {
  const known = new Set(GROUPS.map(g => g.id)), norm = (x: string) => known.has(x) ? x : 'self'   // S1 組名 human
  return GROUPS.map(g => ({ id: g.id, icon: g.icon, title: g.title, items: todo.filter(t => norm(t.group) === g.id) })).filter(g => g.items.length)   // 展開 getter 只拷值,所以逐欄寫
}
export const levelColor = (l: Level): 'success' | 'warning' | 'error' | 'inactive' => l === 'ok' ? 'success' : l === 'warn' ? 'warning' : l === 'red' ? 'error' : 'inactive'
export const levelDot = (l: Level) => l === 'unknown' ? '○' : '●'
// §22.5 第 1 步:驗收席 / bot 席照 S1 給的節點畫流程;dev 家族(或舊版 S1 沒給)回 null ⇒ 畫原本的流程
export const roleFlow = (f?: { family: string; nodes: { key: string }[] } | null) => (f && (f.family === 'qa' || f.family === 'bot') && f.nodes.length ? f : null)
// §22.5 第 2 步 依人格換主題:人格卡設定區的 theme_*(S1 persona.theme);沒寫的那項用預設 —— 主色 claude、光點 ●、速度 normal、框線 round。
// 速度:光點每格走 mul / div 步(slow = 半速、fast = 兩倍)
export type Theme = { color?: string; particle?: string; speed?: string; border?: string }
export const themeOf = (t?: Theme | null) => ({ accent: t?.color ?? 'claude', particle: t?.particle ?? '●',
  mul: t?.speed === 'fast' ? 2 : 1, div: t?.speed === 'slow' ? 2 : 1, border: (t?.border ?? 'round') as 'round' | 'single' | 'double' | 'bold' })
// §22.5 第 3 步 依使用習慣調整:記這一席今天頁每個區塊被點開 / 收起幾次(存 state/aib-boards/<seat_id>.json 的 usage,不外傳)。
//   沒手動開關過的區塊:點過 ≥ USAGE_OPEN 次 ⇒ 預設展開;下方收合區塊照點過的次數排(多的在上,同分照原順序);一鍵回預設 = 清掉 usage 與開關。
export const USAGE_OPEN = 3
export const USAGE_KEYS = ['pkgs', 'log', 'persona', 'project', 'know'] as const
export type Usage = Record<string, number>
export const bumpUsage = (u: Usage | null | undefined, k: string): Usage => ({ ...(u ?? {}), [k]: (u?.[k] ?? 0) + 1 })
export function effectiveEx<T extends Record<string, unknown>>(ex: T, u: Usage | null | undefined, keys: readonly string[] = USAGE_KEYS): T {
  const o: Record<string, unknown> = { ...ex }
  for (const k of keys) if (o[k] === undefined) o[k] = (u?.[k] ?? 0) >= USAGE_OPEN
  return o as T
}
export const foldOrder = (keys: readonly string[], u: Usage | null | undefined): string[] =>
  keys.map((k, i) => [k, i] as const).sort((a, b) => (u?.[b[0]] ?? 0) - (u?.[a[0]] ?? 0) || a[1] - b[1]).map(x => x[0])
export const flowColor = (l: string): 'success' | 'warning' | 'error' => (l === 'error' ? 'error' : l === 'warning' ? 'warning' : 'success')
export const ago = (s: number) => s < 60 ? tr('ago_s', { n: Math.max(0, Math.round(s)) }) : s < 3600 ? tr('ago_m', { n: Math.round(s / 60) }) : s < 172800 ? tr('ago_h', { n: Math.round(s / 3600) }) : tr('ago_d', { n: Math.round(s / 86400) })
// 資料舊要看得出來(§21 第 3 條):超過 10 分鐘標「資料過期」
export const isStale = (measuredAt: number, now: number) => now - measuredAt > 600
export const nowState = (s: string | undefined) => (s === 'busy' || s === 'reply' || s === 'decide' ? tr(`state_${s}`) : (s || '—'))
export function parseScreen(text: string): Screen {
  const d = JSON.parse(text) as Screen
  if (d.schema !== 'aib-seat-screen/v1') throw new Error(tr('err_schema', { what: String(d.schema) }))
  return { ...d, todo: d.todo ?? [], know: d.know ?? [], missing: d.missing ?? [] }
}

// 卡片沒寫 line(S1 v1 的 inbox / my_trees 只給 data)⇒ 用 data 組一行;都沒有就只寫感測器名
const SENSORS = new Set(['machine_lease', 'push_chain', 'main_head', 'inbox', 'my_trees', 'red_lines', 'project_state', 'my_tests'])
const sensorName = (s: string) => (s === 'ctx' ? 'CTX' : SENSORS.has(s) ? tr(`sensor_${s}`) : s)
// [項目名, 內容];沒內容 = null(只寫項目名)。knowLine 與 knowParts 共用,不再拿全形冒號切(英文是半形 + 空白)
function knowPieces(k: { sensor: string; line?: string | null; data?: Record<string, unknown> }): [string, string | null] {
  const name = sensorName(k.sensor), d = k.data ?? {}
  if (k.sensor === 'inbox' && (d.unread !== undefined || d.open !== undefined)) return [name, tr('know_inbox', { v1: String(d.unread ?? 0), v2: String(d.open ?? 0) })]
  if (k.sensor === 'my_trees' && d.ahead !== undefined) return [name, tr('know_trees', { v1: String(Array.isArray(d.trees) ? d.trees.length : (d.trees ?? '?')), v2: String(d.ahead), v3: String(d.dirty ?? 0) }) + (k.line ? tr('know_trees_last', { text: k.line }) : '')]
  return [name, k.line ? k.line : null]
}
export function knowLine(k: { sensor: string; line?: string | null; data?: Record<string, unknown> }): string {
  const [name, body] = knowPieces(k)
  return body === null ? name : tr('know_line', { name, text: body })
}

export const SPIN = ['◐', '◓', '◑', '◒']
// 應知卡 → 雷達光點:每張卡一個角度;紅最近中心、黃中間、綠外圈、量不到最外圈灰
export function knowBlips(know: Know[]): { angle: number; r: number; color: string }[] {
  const R: Record<string, number> = { red: 0.2, warn: 0.5, ok: 0.85, unknown: 1 }
  const C: Record<string, string> = { red: 'error', warn: 'warning', ok: 'success', unknown: 'inactive' }
  return know.map((k, i) => ({ angle: (i / Math.max(1, know.length)) * Math.PI * 2, r: R[k.level] ?? 1, color: C[k.level] ?? 'inactive' }))
}
// 待辦 → 圓餅:每組一片(照 GROUPS 順序)
export function todoParts(todo: Todo[]): { label: string; n: number }[] {
  return groupTodos(todo).map(g => ({ label: g.title, n: g.items.length }))
}

// 畫面 1:4(the maintainer 10-14 00:3x 先說 1:4、看過後改 15:35;02:5x 看過流程儀表板又改回「1:4」):對話 1 份、面板 4 份 = 面板拿 80%。
// 放在輸入框上方時看 rows(格高 70%),停靠在對話旁時看 columns(終端寬 70%);都是請求,人手拖過的大小優先。
export const PANE_SHARE = 4 / 5
export function split41(termCols: number, paneRows: number): { columns: number; rows: number } {
  return { columns: Math.max(40, Math.floor(termCols * PANE_SHARE)), rows: Math.max(12, Math.floor(paneRows * PANE_SHARE)) }
}

// 六角寶石六個角(跟示範頁 extract4 同一算法):成功 = 綠率;速度 = 1 − 讀信中位分 / 60;餘量 = 1 − CTX;收信 = 1/(1+未讀);交接 = 收據 / 輪;協作 = 7 天寄給幾席 / 4
export function hexValues(h: Charts['hex'], ctxPercent: number | null): { v: (number | null)[]; raw: string[] } {
  const r2 = (x: number) => Math.round(x * 100) / 100
  const g = h.success?.green ?? 0, rd = h.success?.red ?? 0
  const v = [g + rd ? r2(g / (g + rd)) : null, h.speed ? r2(Math.max(0, 1 - Math.min(1, h.speed.median_min / 60))) : null,
    ctxPercent !== null ? r2(1 - ctxPercent / 100) : null, h.inbox ? r2(1 / (1 + h.inbox.unread)) : null,
    h.handoff && h.handoff.rounds ? r2(Math.min(1, h.handoff.receipts / h.handoff.rounds)) : null, h.collab ? r2(Math.min(1, h.collab.seats / 4)) : null]
  const raw = [tr('hex_green_red', { v1: g, v2: rd }), h.speed ? tr('hex_speed', { n: Math.round(h.speed.median_min) }) : tr('hex_no_sample'), ctxPercent !== null ? `CTX ${Math.round(ctxPercent)}%` : tr('na'),
    h.inbox ? tr('hex_unread', { n: h.inbox.unread }) : tr('na'), h.handoff ? tr('hex_handoff', { v1: h.handoff.rounds, v2: h.handoff.receipts }) : tr('hex_no_handoff'), h.collab ? tr('hex_collab', { n: h.collab.seats }) : tr('na')]
  return { v, raw }
}
export function parseCharts(text: string): Charts {
  const d = JSON.parse(text) as { charts?: Charts; measured_at?: number; took_ms?: number }
  if (!d.charts) throw new Error(tr('err_no_charts'))
  const c = d.charts
  // 量的時間 / 花多久在 S1 外層,不在 charts 裡;不帶過來 ⇒ 圖表頁寫「量於 NaN 天前」
  return { ...c, measured_at: c.measured_at ?? d.measured_at ?? NaN, took_ms: c.took_ms ?? d.took_ms, trees: c.trees ?? [], trend: c.trend ?? [], slowest: c.slowest ?? [], commits: c.commits ?? [], files: c.files ?? [], hex: c.hex ?? {} }
}

export function parseDm(text: string): Dm {
  const d = (JSON.parse(text) as { dm?: Dm }).dm
  if (!d) throw new Error(tr('err_no_dm'))
  return { items: d.items ?? [], counts: d.counts ?? { unread: 0, open: 0, done_7d: 0 }, chat: d.chat ?? [], seats: d.seats ?? {} }
}
export function parsePlan(text: string): Plan {
  const d = (JSON.parse(text) as { plan?: Plan | null }).plan
  if (!d) throw new Error(tr('err_no_plan'))
  return { ...d, nodes: d.nodes ?? [] }
}

// 「新信」= 還沒讀、沒結案、2 分鐘內寄到、這次沒跳過的。標讀 / 結案時改檔(暫存 + 改名)也可能報 add,靠這幾條擋住誤跳
export function isFreshMail(m: { id?: string; read_at?: unknown; done_at?: unknown; ts?: number; sent_at?: number }, nowS: number, seen: Set<string>): boolean {
  const t = Number(m.sent_at ?? m.ts ?? 0)
  return !!m.id && !m.read_at && !m.done_at && t > 0 && nowS - t < 120 && !seen.has(m.id)
}

// 新信要不要跳、跳什麼:真的新信才跳,同一封只跳一次(之後改檔不重跳);seen 只留最近 200 筆
export function mailToast(m: { id?: string; sender?: string; sender_seat?: string; seat?: string; msg?: string; read_at?: unknown; done_at?: unknown; ts?: number; sent_at?: number }, nowS: number, seen: Set<string>, scr?: { code?: string | null; logos?: Record<string, string> } | null): string | null {
  if (!isFreshMail(m, nowS, seen)) return null
  seen.add(m.id!)
  if (seen.size > 200) seen.delete(seen.values().next().value as string)
  // 誰 DM 誰、時間、內容,兩邊帶引擎標誌(the maintainer 10-14 03:1x)
  const lg = (c?: string | null) => (c && scr?.logos?.[c] ? `${scr.logos[c]} ` : '')
  const from = m.sender_seat ? `${lg(m.sender_seat)}@${m.sender_seat}` : (m.sender ?? tr('mail_new')), to = m.seat ?? scr?.code
  const d = new Date(Number(m.sent_at ?? m.ts ?? nowS) * 1000), hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `📨 ${from}${to ? ` → ${lg(to)}@${to}` : ''} · ${hm}\n${String(m.msg ?? '').replace(/\s+/g, ' ').slice(0, 120)}`
}

// 應知一列拆成「項目名 / 內容」兩欄(排版對齊用;the maintainer 10-14 02:2x「字太擁擠,要優雅有間隔並對齊」)
export function knowParts(k: { sensor: string; line?: string | null; data?: Record<string, unknown> }): [string, string] {
  const [name, body] = knowPieces(k)
  return [name, body ?? '']
}

// ── §22.4 今天頁重排(the maintainer 10-14 02:2x「雷達與分布是必要的嗎?…照這樣做,雷達搬到自訂」)──
// 最上一條「此刻」四小格:CTX 離 85% 多遠 / 機器誰在用、幾點放 / 信箱未讀與最新寄件人 / 我的包有沒有紅燈
export type Cell = { label: string; text: string; sub: string; color: 'success' | 'warning' | 'error' | 'inactive' }
const hm = (s: number) => { const d = new Date(s * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
export function ctxCell(p: number | null): Cell {
  if (p === null) return { label: 'CTX', text: tr('na'), sub: '', color: 'inactive' }
  const left = Math.round(85 - p)
  return { label: 'CTX', text: `${Math.round(p)}%`, sub: left > 0 ? tr('ctx_left', { n: left }) : tr('ctx_hit'), color: p >= 85 ? 'error' : p >= 70 ? 'warning' : 'success' }
}
export function leaseCell(know: Know[], code: string | null, nowS: number): Cell {
  const k = know.find(x => x.sensor === 'machine_lease'), d = (k?.data ?? {}) as { state?: string; owner?: string; acquired_epoch?: number; expiry_epoch?: number; queue?: number }
  const label = tr('sensor_machine_lease')
  if (!k || k.level === 'unknown') return { label, text: tr('na'), sub: '', color: 'inactive' }
  if (d.state === 'free') return { label, text: tr('lease_free'), sub: d.queue ? tr('queue_n', { n: d.queue }) : '', color: 'success' }
  const who = String(d.owner ?? '?'), mine = !!code && who.includes(code)
  const used = d.acquired_epoch ? tr('lease_used', { n: Math.max(0, Math.round((nowS - d.acquired_epoch) / 60)) }) : ''
  const until = d.expiry_epoch ? tr('lease_until', { when: hm(d.expiry_epoch) }) : ''
  return { label, text: mine ? tr('lease_mine') : who, sub: [used, until].filter(Boolean).join(' · '), color: mine ? 'success' : 'warning' }
}
export function mailCell(know: Know[], todo: Todo[]): Cell {
  const k = know.find(x => x.sensor === 'inbox'), d = (k?.data ?? {}) as { unread?: number; open?: number }
  const label = tr('sensor_inbox')
  if (!k || k.level === 'unknown') return { label, text: tr('na'), sub: '', color: 'inactive' }
  const last = todo.filter(t => t.source === 'dm').sort((a, b) => (b.since ?? 0) - (a.since ?? 0))[0]
  const unread = d.unread ?? 0
  return { label, text: unread ? tr('hex_unread', { n: unread }) : tr('no_unread'), sub: last?.from ? tr('latest_from', { who: last.from.replace(/[\uff08(].*$/, '') }) : (d.open ? tr('open_n', { n: d.open }) : ''), color: unread ? 'warning' : 'success' }
}
// 我的包:S1 my_trees 每棵樹帶 stage(0 都沒收 / 1 部分 / 2 都在合包 / 3 都在主線)與 reds(推包被擋的新紅裡、這棵樹動過的檔)
export type Pkg = { branch: string; head?: string; stage?: number; commits?: number; reds?: string[]; refused?: string | null; dirty?: boolean | null }
export function myPkgs(know: Know[]): { open: Pkg[]; landed: Pkg[]; known: boolean } {
  const k = know.find(x => x.sensor === 'my_trees')
  const trees = (((k?.data ?? {}) as { trees?: Pkg[] }).trees ?? []).filter(t => t.branch)
  const landed = trees.filter(t => t.stage === 3)
  const open = trees.filter(t => t.stage !== 3).sort((a, b) => (b.reds?.length ?? 0) - (a.reds?.length ?? 0) || (a.stage ?? -1) - (b.stage ?? -1))
  return { open, landed, known: !!k && k.level !== 'unknown' }
}
export function pkgCell(p: { open: Pkg[]; known: boolean }): Cell {
  const label = tr('my_pkgs')
  if (!p.known) return { label, text: tr('na'), sub: '', color: 'inactive' }
  const reds = p.open.reduce((a, t) => a + (t.reds?.length ?? 0), 0)
  if (reds) return { label, text: tr('reds_n', { n: reds }), sub: tr('pkgs_blocked', { n: p.open.filter(t => t.reds?.length).length }), color: 'error' }
  return { label, text: tr('no_reds'), sub: tr('pkgs_open', { n: p.open.length }), color: 'success' }
}
// 三關:交出(至少一顆已被收進合包或主線)→ 合包(每一顆都在合包或主線)→ 主線(每一顆都在主線);量不到 = null
export function pkgStages(t: Pkg): [string, boolean | null][] {
  const s = typeof t.stage === 'number' ? t.stage : null
  return [[tr('stage_1'), s === null ? null : s >= 1], [tr('stage_2'), s === null ? null : s >= 2], [tr('stage_3'), s === null ? null : s >= 3]]
}

// ── 今天頁流程儀表板(the maintainer 10-14 02:5x 給參考圖:上方狀態列、左欄此刻、中間流程框 + 條、下方紀錄、底部一行)──
export const bar = (v: number, w: number) => { const f = Math.max(0, Math.min(w, Math.round(v * w))); return ['█'.repeat(f), '░'.repeat(w - f)] as const }
export const hhmm = (s?: number | null) => { if (!s) return '--:--'; const d = new Date(s * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
export const shortBranch = (b: string) => b.replace(/^(feat|fix|docs|chore|ci|int)\//, '')
export type LogRow = { at: number; who: string; tag: string; text: string; color: string }
export type Ev = { at: number; kind: string; who: string; text: string; status?: string | null }
const EV: Record<string, string> = { dm_in: 'claude', dm_out: 'suggestion', commit: 'permission', ship_push: 'success', ship_refuse: 'error' }   // 事件種類 → 顏色;標籤字走 ev_<種類>
// 欄寬照語系量(審查 10-14:英文 / 日文 / 韓文標籤比中文長,固定欄寬會截字或黏到下一欄):最長標籤 + 1 格間隔,不小於原本的寬度(中文寬度不變)
const widest = (keys: string[], min: number) => Math.max(min, ...keys.map(k => sw(tr(k)) + 1))
/** 紀錄區「種類」欄寬(原本 9) */
export const logTagW = () => widest(['ev_dm_in', 'ev_dm_out', 'ev_commit', 'ev_ship_push', 'ev_ship_refuse', 'log_now', 'log_self', 'log_lease', 'log_main'], 9)
/** 今天頁左欄(此刻)與「機器 · 主線 · 信箱」框的標籤欄寬(原本 10;左欄只有 30 格,不跟下面的摺疊列一起算,免得中文也被擠) */
export const labelW = () => widest(['kv_todo', 'grp_now', 'kv_human', 'kv_know', 'kv_measured', 'sensor_machine_lease', 'sensor_main_head', 'sensor_inbox'], 10)
/** 摺疊列(人格職責 / 紅線、應知各項)的標籤欄寬(原本 10;中文「我的工作樹」剛好 10 格、原本就黏著內容 ⇒ 變 11) */
export const rowLabelW = () => widest(['duty', 'red_line', 'sensor_machine_lease', 'sensor_push_chain', 'sensor_main_head', 'sensor_inbox', 'sensor_my_trees', 'sensor_red_lines',
  'sensor_project_state', 'sensor_my_tests'], 10)
// 紀錄(the maintainer 02:59「這地方不夠」):S1 events(近 24 小時收信 / 寄信 / 提交 / 推包)+ 狀態列 + 自己記的 + 拿機器 + 主線最新一顆;新的在上
export function logRows(todo: Todo[], know: Know[], events: Ev[], n = 12, nowAt?: number | null): LogRow[] {
  const rows: LogRow[] = events.map(e => { const [tag, color] = EV[e.kind] ? [tr(`ev_${e.kind}`), EV[e.kind]!] : [e.kind, 'inactive']; return { at: e.at, who: e.who.replace(/[\uff08(].*$/, ''), tag, text: e.text.replace(/\s+/g, ' '), color } })
  for (const t of todo) {
    if (t.source === 'dm' || !t.since) continue   // 信件已在 events 裡
    const at = t.source === 'now' ? (nowAt ?? t.since) : t.since   // 狀態列用最近一次更新的時間,不是開始做的時間
    rows.push({ at, who: (t.from ?? '').replace(/[\uff08(].*$/, '') || tr('me'), tag: t.source === 'now' ? tr('log_now') : tr('log_self'), text: t.text.replace(/\s+/g, ' '), color: t.source === 'now' ? 'claude' : 'inactive' })
  }
  const lease = know.find(k => k.sensor === 'machine_lease')?.data as { state?: string; owner?: string; acquired_epoch?: number } | undefined
  if (lease?.state === 'held' && lease.acquired_epoch) rows.push({ at: lease.acquired_epoch, who: String(lease.owner ?? '?'), tag: tr('log_lease'), text: tr('log_lease_text'), color: 'warning' })
  const main = know.find(k => k.sensor === 'main_head')
  const mc = (main?.data as { committed_at?: number; sha?: string } | undefined)
  if (mc?.committed_at) rows.push({ at: mc.committed_at, who: tr('log_main_who'), tag: tr('log_main'), text: `${mc.sha ?? ''} ${main?.line ?? ''}`.trim(), color: 'success' })
  return rows.sort((x, y) => y.at - x.at).slice(0, n)
}

// ── 對齊(the maintainer 03:02「沒有對齊」):→ … — · 這類「寬度不定」的字,Ink 算 1 格、終端機(東亞寬度)畫 2 格 ⇒ 那一列被推歪。
//   紀錄區改成自己算寬度(中日韓 / 全形 / emoji 算 2)、自己補空白與截斷,寬度不定的字先換成半形。
const AMBIG: Record<string, string> = { '→': '->', '←': '<-', '…': '...', '—': '-', '–': '-', '·': '.', '•': '*', '“': '"', '”': '"', '‘': "'", '’': "'", '×': 'x', '○': 'o', '●': '*', '◆': '*', '■': '#', '▶': '>', '✓': 'v', '✗': 'x' }
export const plain = (s: string) => Array.from(s.replace(/\s+/g, ' ')).map(c => AMBIG[c] ?? c).join('')
export function cw(c: string): number {
  const p = c.codePointAt(0) ?? 0
  if (p < 0x1100) return 1
  if ((p <= 0x115f) || (p >= 0x2e80 && p <= 0xa4cf) || (p >= 0xac00 && p <= 0xd7a3) || (p >= 0xf900 && p <= 0xfaff) || (p >= 0xfe30 && p <= 0xfe4f)
    || (p >= 0xff00 && p <= 0xff60) || (p >= 0xffe0 && p <= 0xffe6) || (p >= 0x1f300 && p <= 0x1faff) || (p >= 0x20000 && p <= 0x3fffd)) return 2
  return 1
}
export const sw = (s: string) => Array.from(s).reduce((a, c) => a + cw(c), 0)
// 剛好 w 格:太長就截掉補 ...,太短補空白(寬字不會被切一半)
export function fit(s: string, w: number): string {
  const t = plain(s)
  if (sw(t) <= w) return t + ' '.repeat(w - sw(t))
  let out = '', used = 0
  for (const c of Array.from(t)) { if (used + cw(c) > w - 3) break; out += c; used += cw(c) }
  return out + '...' + ' '.repeat(Math.max(0, w - 3 - used))
}

// ── 紀錄動態(the maintainer 03:0x「要像影片一樣一行一行有動態、數碼感」):逐行吐出、正在吐的那行打字 + 後段亂碼、掃描光棒 ──
const NOISE = '0123456789ABCDEF#$%&*+=<>/'
// 一行打到 shown 格:前段是真字,後段用亂碼補到同寬(寬字補兩個半形,寬度不變);seed 換了亂碼就跳
export function typeIn(s: string, w: number, shown: number, seed: number): [string, string] {
  const t = fit(s, w)
  let done = '', used = 0, rest = ''
  const cs = Array.from(t)
  let i = 0
  for (; i < cs.length; i++) { const c = cs[i]!; if (used + cw(c) > shown) break; done += c; used += cw(c) }
  let k = seed
  for (; i < cs.length; i++) { const c = cs[i]!; const n = cw(c); for (let j = 0; j < n; j++) { k = (k * 1103515245 + 12345) & 0x7fffffff; rest += c === ' ' ? ' ' : NOISE[k % NOISE.length] } }
  return [done, rest]
}
// 第幾格開始吐、現在到哪:每格吐 1 行;正在吐的那行每格打 step 格寬
export function reveal(frame: number, start: number, n: number, step = 24): { rows: number; typing: number; chars: number } {
  const f = Math.max(0, frame - start)
  const rows = Math.min(n, Math.floor(f / 2) + 1)
  return { rows, typing: rows - 1, chars: rows >= n && f >= n * 2 ? Infinity : (f % 2 + 1) * step }
}

// 席碼前面加引擎標誌(the maintainer 10-14 03:1x「所有的 mods 有帶到 aib 席位的,都要帶上他的 logo」;標誌由 S1 logos 給)
// 「pm@86ff0」這種黏著寫的(租約持有者)標誌前補一格,不黏成「pm󰚩 @86ff0」
export const withLogo = (s: string, logos?: Record<string, string> | null) => !logos ? s : s.replace(/@([0-9a-f]{5})\b/g, (m, c: string, at: number) => (logos[c] ? `${at > 0 && /\S/.test(s[at - 1]!) ? ' ' : ''}${logos[c]} ${m}` : m))
// 標誌補原廠色(the maintainer 10-14「仔細把 aib-boards 的所有 logo 補回原廠色」):把已帶標誌的字串切段,
// 「標誌 @席碼」的標誌那一格給 colors[席碼];其他字不給色(沿用外層)。先 withLogo / fit 再切,截斷過的也認得出來
export function logoSegs(s: string, logos?: Record<string, string> | null, colors?: Record<string, string> | null): { text: string; color?: string }[] {
  if (!logos || !colors) return [{ text: s }]
  const out: { text: string; color?: string }[] = []
  let last = 0
  for (const m of s.matchAll(/(\S+) @([0-9a-f]{5})\b/gu)) {
    const c = m[2]!, g = logos[c], col = colors[c]
    if (!g || !col || !m[1]!.endsWith(g)) continue
    const at = m.index! + m[1]!.length - g.length
    if (at > last) out.push({ text: s.slice(last, at) })
    out.push({ text: g, color: col })
    last = at + g.length
  }
  if (last < s.length) out.push({ text: s.slice(last) })
  return out
}
// 圖表格子裡的 @席碼也補標誌 + 原廠色(the maintainer 10-14「繼續做標誌原廠色」):每段先 withLogo 再切;
// 加標誌會多出「標誌 + 空白」的寬,段尾的補白吃掉同樣寬(至少留一格),欄位不歪(時間軸 / 熱力格 / 租約泳道的左欄都是補白過的)
export function logoRows(rows: { text: string; color?: string; dim?: boolean }[][], logos?: Record<string, string> | null, colors?: Record<string, string> | null) {
  if (!logos) return rows
  return rows.map(r => r.flatMap(sg => {
    let t = withLogo(sg.text, logos)
    if (t === sg.text) return [sg]
    const grow = [...t].length - [...sg.text].length, tail = /( +)$/.exec(t)?.[1]?.length ?? 0
    t = t.slice(0, t.length - Math.min(grow, Math.max(0, tail - 1)))   // 至少留一格空白,席碼不黏到後面的圖
    return logoSegs(t, logos, colors).map(x => ({ ...sg, text: x.text, ...(x.color ? { color: x.color, dim: false } : {}) }))
  }))
}
