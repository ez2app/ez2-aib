// 主管 / 監看 / 艦長三級的圖(對齊示範網頁 §12、§18):純函式,輸入 S1 的 charts.lead / qa / captain + frame,輸出 Rows。
import { Canvas, scanRadar } from './charts'
import type { Rows, Seg } from './charts'
import { pad, padL, seg, blocks, dispW } from './engcharts'
import type { SeatRow } from '../types'
import { tr } from './i18n'

const SHADE = ' ░▒▓█'
const none = (t: string): Rows => [[seg(t, 'inactive')]]
const hm = (t: number) => { const d = new Date(t * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }

// 掃描雷達(全艦 / 手下):光點 = 每一席,離中心越近 = 越剛有動作;紅 = 要處理(CTX ≥ 85 / 未讀 ≥ 3 / 狀態列忙碌太久沒更新)
export const needsAttention = (s: SeatRow) => (s.ctx ?? 0) >= 85 || (s.unread ?? 0) >= 3 || !!s.stale
export function seatRadar(seats: SeatRow[], w: number, h: number, frame: number): Rows {
  const blips = seats.map((s, i) => ({ angle: (i / Math.max(1, seats.length)) * Math.PI * 2, r: Math.min(1, Math.max(0.12, Math.log10(1 + (s.idle_s ?? 3600)) / 4.5)), color: needsAttention(s) ? 'error' : s.idle_s != null && s.idle_s < 300 ? 'success' : 'suggestion' }))
  const rows = scanRadar(blips, w, h, frame)
  const legend = seats.map(s => seg(`${needsAttention(s) ? '●' : '○'}${s.code} `, needsAttention(s) ? 'error' : 'inactive'))
  return [...rows, legend]
}

// 各席狀態表:席碼 / 引擎 / CTX 條(80 黃、85 紅)/ 未讀 / 狀態列
export function seatTable(seats: SeatRow[], frame: number): Rows {
  if (!seats.length) return none(tr('none_subs'))
  // 欄寬照語系最長的表頭 + 1 格間隔,不小於原本(席 8、引擎 8、未讀 5);資料列跟著同寬
  const WS = Math.max(8, dispW(tr('th_seat')) + 1), WE = Math.max(8, dispW(tr('th_engine')) + 1), WU = Math.max(5, dispW(tr('th_unread')) + 1)
  return [[seg(pad(tr('th_seat'), WS), 'inactive'), seg(pad(tr('th_engine'), WE), 'inactive'), seg(pad('CTX', 18), 'inactive'), seg(pad(tr('th_unread'), WU), 'inactive'), seg(tr('th_status'), 'inactive')],
    ...seats.map(s => { const c = s.ctx ?? null, n = c === null ? 0 : Math.max(0, Math.min(10, Math.round(c / 10))), col = c === null ? 'inactive' : c >= 85 ? 'error' : c >= 80 ? 'warning' : 'success'
      return [seg(pad('@' + s.code, WS)), seg(pad(s.prov ?? '?', WE), 'inactive'), seg(blocks(n), col, c !== null && c >= 85 && frame % 4 >= 2), seg('░'.repeat(10 - n), 'subtle'), seg(' ' + pad(c === null ? '—' : `${Math.round(c)}%`, 6), col),
        seg(pad(String(s.unread ?? '—'), WU), (s.unread ?? 0) >= 3 ? 'error' : 'text'), seg(s.stale ? tr('busy_stale') : (s.now_state ?? '—'), s.stale ? 'error' : 'inactive')] })]
}

// 漏斗:有東西交出去 → 全部進合包 → 全部上主線(每關剩幾條)
export function funnelRows(f: number[], width: number, frame: number): Rows {
  const [total = 0, delivered = 0, inBatch = 0, onMain = 0] = f.length === 4 ? [f[3]!, f[0]!, f[1]!, f[2]!] : f
  const steps: [string, number, string][] = [[tr('fn_all'), total, 'subtle'], [tr('fn_out'), delivered, 'suggestion'], [tr('fn_batch'), inBatch, 'warning'], [tr('fn_main'), onMain, 'success']]
  const mx = Math.max(1, total), bw = Math.max(10, width - 24)
  return steps.map(([t, n, col], i) => { const len = Math.max(n ? 1 : 0, Math.round((n / mx) * bw)), indent = Math.round((bw - len) / 2)
    return [seg(pad(t, 14)), seg(' '.repeat(indent)), seg(blocks(len), col, i === frame % 4 && n > 0), seg(` ${n}`)] })
}

// 等待時間分布:讀信要等幾分鐘的直方圖
export function waitHist(mins: number[], frame: number): Rows {
  if (!mins.length) return none(tr('hex_no_sample'))
  const B: [string, number][] = [[tr('wb_1m'), 1], ['1-5', 5], ['5-15', 15], ['15-60', 60], [tr('wb_4h'), 240], [tr('wb_over'), Infinity]]
  const CW = Math.max(6, ...B.map(b => dispW(b[0]) + 1))   // 每格寬:照最長的區間名 + 1(日韓「1-4時間」比中文長),至少 6
  const cnt = B.map((b, i) => mins.filter(m => m < b[1] && m >= (i ? B[i - 1]![1] : 0)).length), mx = Math.max(1, ...cnt), H = 5, out: Rows = []
  for (let r = 0; r < H; r++) out.push(cnt.map((c, i) => { const e = Math.round((c / mx) * H * 8), cell = Math.max(0, Math.min(8, e - (H - 1 - r) * 8)); return seg(' ▁▂▃▄▅▆▇█'[cell]!.repeat(CW - 1) + ' ', i >= 4 ? 'warning' : 'suggestion', i === frame % 6 && cell > 0) }))
  out.push(B.map(b => seg(pad(b[0], CW), 'inactive'))); out.push(cnt.map(c => seg(pad(String(c), CW), 'inactive')))
  return out
}

// 甘特:每個 Task 一條時間條(第一顆到最後一顆提交)
export function ganttRows(rows: { title: string; owner?: string; status?: string; s: number; e: number }[], width: number, frame: number): Rows {
  if (!rows.length) return none(tr('none_gantt'))
  const s0 = Math.min(...rows.map(r => r.s)), e0 = Math.max(...rows.map(r => r.e)), span = Math.max(1, e0 - s0), tw = 22, bw = Math.max(10, width - tw - 2)
  const col = (st?: string) => st === 'done' ? 'success' : st === 'blocked' ? 'error' : st === 'in_progress' ? 'suggestion' : 'warning'
  return [...rows.slice(0, 10).map(r => { const a = Math.round(((r.s - s0) / span) * bw), b = Math.max(a + 1, Math.round(((r.e - s0) / span) * bw))
    return [seg(pad(r.title, tw)), seg(' '.repeat(a)), seg('━'.repeat(b - a), col(r.status), r.status === 'in_progress' && frame % 4 >= 2), seg(' ' + (r.owner ?? ''), 'inactive')] }),
    [seg(pad('', tw)), seg(pad(hm(s0), Math.floor(bw / 2)), 'inactive'), seg(padL(hm(e0), Math.ceil(bw / 2)), 'inactive')]]
}

// 通用橫條清單(測試熱點、時紅時綠、帕雷托、狀態列擋人)
export function listBars(items: [string, number][], width: number, color: string, unit = '', cumulative = false): Rows {
  if (!items.length) return none(tr('none_data'))
  // 每段量顯示寬(中文兩格):名字欄 + 空一格 + 條 + 尾巴(數字、累計 %)剛好 ≤ width;原本名字照字數切、尾巴沒算,帕雷托多出一格被折行
  const mx = Math.max(1, ...items.map(i => i[1])), tot = items.reduce((a, i) => a + i[1], 0), list = items.slice(0, 8)
  let acc = 0
  const nums = list.map(([, n]) => ` ${n}${unit}`), cums = list.map(([, n]) => { acc += n; return cumulative ? tr('cumulative', { n: Math.round((acc / Math.max(1, tot)) * 100) }) : '' })
  const tails = nums.map((x, k) => x + cums[k])
  const tw = Math.max(...tails.map(dispW)), nw = Math.min(30, Math.max(8, width - tw - 1 - 6)), bw = Math.max(1, width - nw - 1 - tw)
  return list.map(([t, n], k) => {
    const name = dispW(t) > nw ? pad(t, nw) : padL(t, nw), tail = tails[k]!
    return [seg(name + ' '), seg(blocks(Math.max(1, (n / mx) * bw)), color), seg(cumulative ? nums[k]! : tail), ...(cumulative ? [seg(cums[k]!, 'inactive')] : [])]
  })
}
export function flakyRows(items: [string, number, number][], width: number, frame: number): Rows {
  if (!items.length) return none(tr('none_flaky'))
  const nw = Math.min(30, Math.max(14, width - 22)), mx = Math.max(1, ...items.map(i => i[1] + i[2]))
  return items.slice(0, 8).map(([t, g, r]) => [seg(padL(t.length > nw ? '…' + t.slice(-(nw - 1)) : t, nw) + ' '), seg(blocks(Math.max(1, (g / mx) * 10)), 'success'), seg(blocks(Math.max(1, (r / mx) * 10)), 'error', frame % 4 >= 2), seg(` ${tr('green_n', { n: g })} ${tr('red_n', { n: r })}`, 'inactive')])
}
// 巡查時間軸:過去 48 小時,每席一列,事件畫成點
export function eventTimeline(events: { t: number; who: string; kind: string }[], width: number, now: number): Rows {
  if (!events.length) return none(tr('none_events'))
  const who = [...new Set(events.map(e => e.who))].slice(0, 8), bw = Math.max(20, width - 12), t0 = now - 48 * 3600
  const rows: Rows = who.map(w => { const line = new Array(bw).fill('·'); events.filter(e => e.who === w).forEach(e => { const x = Math.min(bw - 1, Math.max(0, Math.floor(((e.t - t0) / (48 * 3600)) * bw))); line[x] = e.kind.includes('now') ? '◆' : '●' })
    return [seg(pad('@' + w, 10)), ...line.map(c => seg(c, c === '·' ? 'subtle' : c === '◆' ? 'warning' : 'error'))] })
  rows.push([seg(pad('', 10)), seg(pad('-48h', Math.floor(bw / 2)), 'inactive'), seg(padL(tr('now_axis'), Math.ceil(bw / 2)), 'inactive')])
  return rows
}

// ── 艦長級 ──
// 影子續跑累積:不靠 aib 要等(紅)vs 實際花(藍),點陣兩條線,中間差 = 少等
export function shadowCumulative(runs: [number, number, number, number][], w: number, h: number): Rows {
  if (!runs.length) return none(tr('none_runs'))
  let a = 0, b = 0; const A: number[] = [], Bv: number[] = []
  for (const r of runs) { a += r[1]; b += r[2]; A.push(a); Bv.push(b) }
  const cv = new Canvas(w, h), W = w * 2 - 1, H = h * 4 - 1, mx = Math.max(1, a)
  const xy = (i: number, v: number): [number, number] => [Math.round((i / Math.max(1, runs.length - 1)) * W), H - (v / mx) * H]
  for (let i = 0; i < runs.length; i++) { const [x, ya] = xy(i, A[i]!), [, yb] = xy(i, Bv[i]!); for (let y = Math.ceil(ya); y < yb; y += 2) cv.set(x, y, 'success') }
  for (let i = 1; i < runs.length; i++) { const p = xy(i - 1, A[i - 1]!), q = xy(i, A[i]!); cv.line(p[0], p[1], q[0], q[1], 'error'); const p2 = xy(i - 1, Bv[i - 1]!), q2 = xy(i, Bv[i]!); cv.line(p2[0], p2[1], q2[0], q2[1], 'suggestion') }
  return [...cv.rows(), [seg(`■${tr('sc_without', { value: (a / 3600).toFixed(1) })}  `, 'error'), seg(`■${tr('sc_actual', { value: (b / 3600).toFixed(1) })}  `, 'suggestion'), seg(`■${tr('sc_saved', { value: ((a - b) / 3600).toFixed(1) })}`, 'success')]]
}
// 推包河流:每 6 小時一段,寬度 = 推了幾輪,顏色 = 結果
export function riverRows(river: [number, number, number, number][], frame: number): Rows {
  if (!river.length) return none(tr('none_ship'))
  const mx = Math.max(1, ...river.map(r => r[1] + r[2] + r[3])), H = 6, out: Rows = []
  for (let r = 0; r < H; r++) {
    const fb = H - 1 - r
    out.push(river.slice(-16).map(([, g, rd, o], i, arr) => { const tot = g + rd + o, e = Math.round((tot / mx) * H), cell = fb < e
      const share = tot ? (fb / Math.max(1, e)) : 0, col = !cell ? 'subtle' : share < g / tot ? 'success' : share < (g + rd) / tot ? 'error' : 'warning'
      return seg(cell ? '██ ' : '   ', col, i === arr.length - 1 && frame % 4 >= 2) }))
  }
  out.push([seg(`■${tr('rv_ok')} `, 'success'), seg(`■${tr('rv_red')} `, 'error'), seg(`■${tr('rv_other')}`, 'warning'), seg(tr('rv_note', { n: Math.min(16, river.length) }), 'inactive')])
  return out
}
// 機器租約泳道:過去 48 小時,每個持有者一列,斜線 = 獨佔
export function leaseLanes(lease: { s: number; e: number; who: string; ex?: boolean }[], width: number, now: number): Rows {
  if (!lease.length) return none(tr('none_lease'))
  const t0 = now - 48 * 3600, bw = Math.max(20, width - 16), who = [...new Set(lease.map(l => l.who))].slice(0, 8)
  const rows: Rows = who.map(w => { const line = new Array(bw).fill(' '); lease.filter(l => l.who === w && l.e > t0).forEach(l => { const a = Math.max(0, Math.floor(((l.s - t0) / (48 * 3600)) * bw)), b = Math.min(bw, Math.max(a + 1, Math.ceil(((l.e - t0) / (48 * 3600)) * bw))); for (let x = a; x < b; x++) line[x] = l.ex ? '▚' : '█' })
    return [seg(pad(w, 14)), ...line.map(c => seg(c === ' ' ? '·' : c, c === ' ' ? 'subtle' : w.includes('ship') || w.includes('推包') ? 'warning' : 'suggestion'))] })
  rows.push([seg(pad('', 14)), seg(pad('-48h', Math.floor(bw / 2)), 'inactive'), seg(padL(tr('now_axis'), Math.ceil(bw / 2)), 'inactive')])
  return rows
}
// 席位熱力格:每格 = 一席那一小時的信件往來,越亮越忙
export function heatGrid(heat: { codes: string[]; rows: Record<string, number[]> }): Rows {
  const mx = Math.max(1, ...heat.codes.flatMap(c => heat.rows[c] ?? []))
  const rows: Rows = heat.codes.map(c => [seg(pad('@' + c, 8)), ...(heat.rows[c] ?? []).map(v => { const k = v ? Math.max(1, Math.min(4, Math.ceil(Math.sqrt(v / mx) * 4))) : 0; return seg(SHADE[k]!.repeat(2), k >= 3 ? 'warning' : k ? 'suggestion' : 'subtle') })])
  rows.push([seg(pad('', 8)), seg(pad('-24h', 24), 'inactive'), seg(padL(tr('now_axis'), 24), 'inactive')])
  return rows
}
// 瀑布:從「不靠 aib 要等多久」一段一段扣到實際
export function waterfallRows(w: { without_h: number; reuse_h: number; select_h: number; shard_h: number; actual_h: number }, width: number, frame: number): Rows {
  const steps: [string, number, number, string][] = [[tr('wf_without'), 0, w.without_h, 'error'], [tr('wf_reuse'), w.without_h - w.reuse_h, w.reuse_h, 'success'], [tr('wf_select'), w.without_h - w.reuse_h - w.select_h, w.select_h, 'success'], [tr('wf_shard'), w.actual_h, w.shard_h, 'success'], [tr('wf_actual'), 0, w.actual_h, 'suggestion']]
  // 標籤欄照語系最長的字 + 1 格間隔(至少 16,中文不變);多出來的從條上扣,整列寬度不變
  const LW = Math.max(16, ...steps.map(s => dispW(s[0]) + 1)), bw = Math.max(16, width - 30 - (LW - 16)), mx = Math.max(1, w.without_h), x = (v: number) => Math.round((v / mx) * bw)
  return steps.map(([t, from, len, col], i) => [seg(pad(t, LW)), seg(' '.repeat(x(from))), seg(blocks(Math.max(1, x(len))), col, i === frame % 5), seg(tr('hours_v', { value: len.toFixed(1) }))])
}
// 和弦(終端版):誰寄給誰的矩陣,越亮越多
export function chordMatrix(c: { names: string[]; m: number[][] }): Rows {
  const mx = Math.max(1, ...c.m.flat()), n = c.names.map(x => x.slice(0, 5))
  const HW = Math.max(8, dispW(tr('chord_head')) + 1)   // 左上角表頭照語系量寬(韓文 9 格)
  return [[seg(pad(tr('chord_head'), HW), 'inactive'), ...n.map(x => seg(pad(x, 6), 'inactive'))],
    ...c.m.map((row, i) => [seg(pad(n[i]!, HW)), ...row.map((v, j) => { const k = v ? Math.max(1, Math.min(4, Math.ceil(Math.sqrt(v / mx) * 4))) : 0; return seg(pad(i === j ? '·' : (SHADE[k]!.repeat(2) + (v ? String(v) : '')), 6), k >= 3 ? 'warning' : k ? 'suggestion' : 'subtle') })])]
}
// 等 the maintainer 決定:卡片 + 等了多久(每秒在跑)
export function decideCards(d: { since: number; text: string; from?: string }[], width: number, now: number): Rows {
  if (!d.length) return none(tr('none_decide'))
  return d.slice(0, 5).flatMap(x => { const s = Math.max(0, now - x.since), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60), ring = Math.min(10, Math.floor((s / 86400) * 10))
    return [[seg('◔◑◕●'[Math.min(3, Math.floor(ring / 3))]! + ' ', 'warning'), seg(`${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')} `, 'error'), seg(pad(x.text, Math.max(10, width - 14)))], [seg('   ' + (x.from ?? ''), 'inactive')]] })
}
// 主線心跳:過去 72 小時每小時幾顆提交
export function beatRows(beat: number[], frame: number): Rows {
  const mx = Math.max(1, ...beat), H = 4, out: Rows = []
  for (let r = 0; r < H; r++) out.push(beat.map((v, i) => { const e = Math.round((Math.sqrt(v / mx)) * H * 8), cell = Math.max(0, Math.min(8, e - (H - 1 - r) * 8)); return seg(' ▁▂▃▄▅▆▇█'[cell]!, i === beat.length - 1 - (frame % 6) && cell ? 'warning' : 'success') }))
  out.push([seg(pad('-72h', Math.floor(beat.length / 2)), 'inactive'), seg(padL(tr('now_axis'), Math.ceil(beat.length / 2)), 'inactive')])
  return out
}
// 紅燈去向(終端版 sankey):哪一區 → 哪一天 → 之後有沒有轉綠
export function sankeyRows(s0: { a: string; d: string; o: string; n: number }[]): Rows {
  let s = s0
  if (!s.length) return none(tr('none_reds'))
  const by = (k: 'a' | 'd' | 'o') => { const m = new Map<string, number>(); s.forEach(x => m.set(x[k], (m.get(x[k]) ?? 0) + x.n)); return [...m.entries()].sort((p, q) => q[1] - p[1]) }
  // S1 給機器鍵(fixed / open),畫面翻成人話;顏色看機器鍵,不看翻出來的字
  const OUT = (o: string) => (o === 'fixed' ? tr('sk_fixed') : o === 'open' ? tr('sk_open') : o), stillRed = new Set([tr('sk_open')])
  s = s.map(x => ({ ...x, o: OUT(x.o) }))
  const A = by('a').slice(0, 6), D = by('d'), O = by('o'), rows = Math.max(A.length, D.length, O.length), out: Rows = [[seg(pad(tr('sk_area'), 18), 'inactive'), seg(pad(tr('sk_day'), 14), 'inactive'), seg(tr('sk_after'), 'inactive')]]
  for (let i = 0; i < rows; i++) { const a = A[i], d = D[i], o = O[i]
    out.push([seg(pad(a ? `${a[0]} ${blocks(Math.min(6, a[1]))}${a[1]}` : '', 18), 'error'), seg(pad(d ? `→ ${d[0]} ${d[1]}` : '', 14), 'warning'), seg(o ? `→ ${o[0]} ${o[1]}` : '', o && stillRed.has(o[0]) ? 'error' : 'success')]) }
  return out
}
export const segText = (r: Seg[]) => r.map(s => s.text).join('')
