// 工程級 6 張圖(對齊示範網頁 §17/§18):純函式,輸入 S1 的 charts + frame,輸出 Rows(每列一串 {文字, 顏色})。
import { Canvas } from './charts'
import type { Rows, Seg } from './charts'
import { fit } from './lib'
import { tr } from './i18n'

const seg = (text: string, color?: string, dim?: boolean): Seg => ({ text, color, dim })
// 終端機顯示寬:中日韓全形字占 2 格(對不齊的根因:原本當 1 格算)
const cw = (ch: string) => /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/.test(ch) ? 2 : 1
export const dispW = (s: string) => [...s].reduce((a, ch) => a + cw(ch), 0)
const pad = (s: string, n: number) => {
  if (dispW(s) <= n) return s + ' '.repeat(n - dispW(s))
  let out = '', w = 0
  for (const ch of s) { if (w + cw(ch) > n - 1) break; out += ch; w += cw(ch) }
  return out + '…' + ' '.repeat(Math.max(0, n - 1 - w))
}
const padL = (s: string, n: number) => ' '.repeat(Math.max(0, n - dispW(s))) + s
const blocks = (n: number) => '█'.repeat(Math.max(0, Math.round(n)))

// 01 六角寶石:實心寶石,切面依順時針輪流亮(3.6 秒一圈 ≈ 9 格),頂點閃星(值越高閃越常),沒來源的角縮在中心
const HEX_K = ['hex_success', 'hex_speed_k', 'hex_room', 'hex_mail', 'hex_handoff_k', 'hex_team']
// 六個角的名字(照語系;補到同寬,英文長短不一時數字才對得齊 —— 中文都是兩字,補了也不變)
export const hexKeys = (): string[] => { const k = HEX_K.map(x => tr(x)), w = Math.max(...k.map(dispW)); return k.map(x => x + ' '.repeat(w - dispW(x))) }
export function hexGem(values: (number | null)[], w: number, h: number, frame: number): Rows {
  const cv = new Canvas(w, h), cx = w - 0.5, cy = h * 2 - 0.5, R = Math.min(w - 1, h * 2 - 1)
  const at = (k: number, r: number): [number, number] => { const a = -Math.PI / 2 + (k * Math.PI) / 3; return [cx + Math.cos(a) * r, cy + Math.sin(a) * r] }
  for (const ring of [1 / 3, 2 / 3, 1]) for (let k = 0; k < 6; k++) { const [x0, y0] = at(k, R * ring), [x1, y1] = at(k + 1, R * ring); cv.line(x0, y0, x1, y1, 'subtle', ring < 1 ? 2 : 0) }
  const v = values.map(x => (x == null ? 0.06 : Math.max(0.08, Math.min(1, x))))
  const P = v.map((f, k) => at(k, R * f))
  const lit = Math.floor(frame / 1.5) % 6
  // 每個切面 = 中心 + 相鄰兩個頂點的三角形;逐點判斷在不在裡面
  const inTri = (px: number, py: number, a: [number, number], b: [number, number], c: [number, number]) => {
    const s = (p: [number, number], q: [number, number], r: [number, number]) => (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    const d1 = s([px, py], a, b), d2 = s([px, py], b, c), d3 = s([px, py], c, a)
    return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))
  }
  for (let y = 0; y < h * 4; y++) for (let x = 0; x < w * 2; x++) for (let k = 0; k < 6; k++) {
    if (inTri(x, y, [cx, cy], P[k]!, P[(k + 1) % 6]!)) { if ((x + y + k) % 2 === 0 || k === lit) cv.set(x, y, k === lit ? 'warning' : 'claude'); break }
  }
  for (let k = 0; k < 6; k++) { const [x0, y0] = P[k]!, [x1, y1] = P[(k + 1) % 6]!; cv.line(x0, y0, x1, y1, 'claude') }
  const rows = cv.rows()
  // 頂點閃星:值越高,閃得越常(週期 4.2 − 3×值 秒 ≈ 格數)
  v.forEach((f, k) => {
    if (values[k] == null) return
    const period = Math.max(3, Math.round((4.2 - 3 * f) / 0.4))
    if (frame % period !== 0) return
    const [x, y] = P[k]!, r = Math.max(0, Math.min(h - 1, Math.floor(y / 4))), c = Math.max(0, Math.min(w - 1, Math.floor(x / 2)))
    const row = rows[r]; if (!row) return
    let col = 0
    for (let i = 0; i < row.length; i++) { const L = [...row[i]!.text].length; if (c < col + L) { const t = [...row[i]!.text]; t[c - col] = '✦'; row[i] = { ...row[i]!, text: t.join(''), color: 'warning' }; break } col += L }
  })
  return rows
}

// 02 我的分支測試:分支、領先主線(開根號比例)、最近一次測試綠紅(紅段閃)、沒跑過虛線、合包圓點
export type TreeRow = { branch: string; ahead: number; in_batch: boolean; green: number; red: number }
// 一列一棵樹,整列剛好 width 格(the maintainer 03:1x 實看:原本固定 30+18+20 格,半寬卡放不下 ⇒ 每棵樹折成兩行、表頭「合包」掉下去)
//   分支名照寬度截(中文算 2 格)、領先條 8 格、測試條 6 格 + 數字 8 格、合包燈 2 格
export function treeRows(trees: TreeRow[], frame: number, width = 64): Rows {
  const mxA = Math.max(1, ...trees.map(t => t.ahead)), mxN = Math.max(1, ...trees.map(t => t.green + t.red))
  // 分支名欄 = 寬度扣掉後面固定 35 格;原本最少給 14,窄卡(46 格)時整列 49 格超出卡片、末尾的 ○ / ● 被折到下一行
  //   合包欄 = 表頭字寬(至少 4 格;英文 Batch 5 格)⇒ 分支名欄讓出去,整列不超寬
  //   測試欄的字(「沒跑過」/「3綠1紅」)同理:至少 8 格,照語系最長的字 + 1 格間隔
  const B4 = Math.max(4, dispW(tr('th_batch'))), NW = Math.max(8, dispW(tr('never_ran')) + 1), BW = Math.max(8, width - (8 + 7 + 6 + 1 + NW + B4 + 1))
  const head: Rows = [[seg(fit(tr('th_branch'), BW + 1), 'inactive'), seg(fit(tr('th_ahead'), 15), 'inactive'), seg(fit(tr('th_last_test'), 7 + NW), 'inactive'), seg(tr('th_batch'), 'inactive')]]
  const MAX = 10, more = trees.length - MAX
  return head.concat(trees.slice(0, MAX).map(t => {
    const a = Math.round(Math.sqrt(t.ahead / mxA) * 8), n = t.green + t.red
    const g = n ? Math.max(t.green ? 1 : 0, Math.round((t.green / mxN) * 6)) : 0, r = n ? Math.min(6 - g, Math.max(t.red ? 1 : 0, Math.round((t.red / mxN) * 6))) : 0
    return [seg(fit(t.branch || tr('no_branch'), BW) + ' '), seg(blocks(a), 'suggestion', frame % 6 >= 3), seg('░'.repeat(8 - a), 'subtle'), seg(' ' + fit(t.ahead ? '+' + t.ahead : '0', 6), 'inactive'),
      ...(n ? [seg(blocks(g), 'success'), seg(blocks(r), 'error', frame % 4 >= 2), seg('░'.repeat(6 - g - r), 'subtle'), seg(' ' + fit(`${tr('green_n', { n: t.green })}${t.red ? tr('red_n', { n: t.red }) : ''}`, NW), t.red ? 'error' : 'success')]
        : [seg('┄┄┄┄┄┄', 'subtle'), seg(' ' + fit(tr('never_ran'), NW), 'inactive')]),
      seg('  ' + (t.in_batch ? '●' : '○') + ' '.repeat(B4 - 3), t.in_batch ? 'warning' : 'inactive')]
  }), more > 0 ? [[seg(tr('more_trees', { n: more }), 'inactive')]] : [])
}

// 03 影子續跑:三色條(沿用 / 挑掉不跑 / 實跑)、兩條時間對照(沒省到 = 紅、閃)、沒省到的原因
export type Shadow = { kept: number; skipped: number; ran: number; without_s: number | null; actual_s: number | null; reasons: [string, number][] }
export function shadowRows(s: Shadow, width: number, frame: number): Rows {
  // the maintainer 10-14 04:3x「圖與字高度與間隔太近,而且換行有問題」:段與段之間空一行;每列(標籤 + 條 + 尾巴字)量好顯示寬再定條長,
  //   不准超出卡片寬(超出就被終端機折到下一行,「沒省到」掉到下一行就是這個);原因整句放條的右邊,不再塞進 20 格被切
  const tot = s.kept + s.skipped + s.ran, W = Math.max(20, width - 2), gap: Seg[] = [seg(' ')]
  const out: Rows = [[seg(tr('sh_how', { n: tot }), 'inactive')], gap]
  if (tot > 0) {
    // 三段長度用最大餘數分配,加起來剛好 W(各自四捨五入會多出一兩格把整條擠到下一行)
    const raw = [s.kept, s.skipped, s.ran].map(n => (n / tot) * W), len = raw.map(Math.floor)
    raw.map((r, k) => [r - Math.floor(r), k] as [number, number]).sort((a, b) => b[0] - a[0]).slice(0, W - len.reduce((a, b) => a + b, 0)).forEach(([, k]) => { len[k]! += 1 })
    out.push([seg(blocks(len[0]!), 'success'), seg(blocks(len[1]!), 'suggestion'), seg(blocks(len[2]!), 'warning')])
  }
  out.push([seg(`■${tr('sh_kept', { n: s.kept })}  `, 'success'), seg(`■${tr('sh_skipped', { n: s.skipped })}  `, 'suggestion'), seg(`■${tr('sh_ran', { n: s.ran })}`, 'warning')])
  if (s.without_s != null && s.actual_s != null) {
    const saved = s.actual_s < s.without_s, mx = Math.max(s.without_s, s.actual_s, 1)
    const L1 = tr('sh_without'), L2 = tr('sh_actual'), LBL = Math.max(dispW(L1), dispW(L2)) + 2
    const tails = [tr('secs', { n: s.without_s }), tr('secs', { n: s.actual_s }) + (saved ? '' : tr('sh_not_saved'))]
    const bw = Math.max(6, W - LBL - Math.max(...tails.map(dispW)))
    out.push(gap, [seg(tr('sh_time'), 'inactive')])
    out.push([seg(padL(L1 + ' ', LBL)), seg(blocks((s.without_s / mx) * bw), 'inactive'), seg(tails[0]!)])
    out.push([seg(padL(L2 + ' ', LBL)), seg(blocks((s.actual_s / mx) * bw), saved ? 'success' : 'error', !saved && frame % 4 >= 2), seg(tails[1]!, saved ? 'success' : 'error')])
  }
  if (s.reasons.length) {
    const mx = Math.max(1, ...s.reasons.map(r => r[1])), BW = 12, nW = Math.max(...s.reasons.map(r => dispW(` ${r[1]}`)))
    out.push(gap, [seg(tr('sh_reasons'), 'inactive')])
    for (const [t, n] of s.reasons.slice(0, 4)) {
      const num = ` ${n}`, room = Math.max(6, W - 2 - BW - nW - 2)
      out.push([seg('  '), seg(blocks((n / mx) * BW), 'permission'), seg(' '.repeat(BW - Math.round((n / mx) * BW))), seg(num + ' '.repeat(nW - dispW(num)) + '  '), seg(dispW(t) > room ? pad(t, room) : t)])
    }
  }
  return out
}

// 04 我的測試紅綠(7 天):每天一根,綠在下、紅疊上;最新一天亮暗交替
const EIGHTHS = ' ▁▂▃▄▅▆▇█'
// width:卡片內寬;每天一欄平分整張卡(the maintainer 10-14 04:3x「這感覺右邊空掉了」:原本固定每天 6 格 = 42 格寬,寬卡右邊一大塊空白)
export function stackCols(trend: [string, number, number][], h: number, frame: number, width = 42): Rows {
  const mx = Math.max(1, ...trend.map(d => d[1] + d[2])), out: Rows = []
  const colW = Math.max(6, Math.floor(width / Math.max(1, trend.length))), barW = Math.max(3, colW - 2)
  for (let r = 0; r < h; r++) {
    const fromBottom = h - 1 - r, row: Seg[] = []
    trend.forEach(([, g, rd], i) => {
      const ge = Math.round((g / mx) * h * 8), te = Math.round(((g + rd) / mx) * h * 8)
      const cellT = Math.max(0, Math.min(8, te - fromBottom * 8)), cellG = Math.max(0, Math.min(8, ge - fromBottom * 8))
      const red = cellT > cellG, ch = EIGHTHS[cellT]!.repeat(barW)
      row.push(seg(ch, red ? 'error' : 'success', i === trend.length - 1 && frame % 6 >= 3), seg(' '.repeat(colW - barW)))
    })
    out.push(row)
  }
  out.push(trend.map(([d]) => seg(pad(d.slice(3), colW), 'inactive')))
  out.push(trend.map(([, g, r]) => seg(pad(g + r ? `${g}${r ? '/' + r : ''}` : '·', colW), r ? 'error' : 'inactive')))
  return out
}

// 05 提交節奏(7 天):點陣曲線 + 面積點 + 每天數字
export function commitCurve(commits: [string, number][], w: number, h: number, frame: number): Rows {
  // 點從最左排到最右(the maintainer 10-14 04:5x「這個右邊有點空」:原本每天一欄、最後一天點在欄的第 1 格,右邊空一截);
  //   日期與數字標在點的正下方(每個標籤從點那一格開始寫,最後一個靠右對齊)
  const n = Math.max(1, commits.length), cols = Math.max(n * 3, w), last = cols - 2
  const cx = commits.map((_, i) => (n === 1 ? 0 : Math.round((i * last) / (n - 1))))
  const cv = new Canvas(cols, h), H = h * 4 - 1, mx = Math.max(1, ...commits.map(c => c[1]))
  const xs = cx.map(c => c * 2), ys = commits.map(c => H - (c[1] / mx) * (H - 1))
  for (let i = 0; i < xs.length; i++) for (let y = Math.ceil(ys[i]!); y <= H; y += 2) cv.set(xs[i]!, y, 'subtle')
  for (let i = 1; i < xs.length; i++) cv.line(xs[i - 1]!, ys[i - 1]!, xs[i]!, ys[i]!, 'suggestion')
  const live = frame % n
  xs.forEach((x, i) => { cv.set(x, ys[i]!, i === live ? 'warning' : 'suggestion'); cv.set(x + 1, ys[i]!, i === live ? 'warning' : 'suggestion') })
  const rows = cv.rows()
  const under = (lab: (c: [string, number]) => string, color: (c: [string, number]) => string) => {
    const segs: Seg[] = []; let at = 0
    commits.forEach((c, i) => { const t = lab(c), tw = dispW(t), x = i === n - 1 ? Math.max(at, cols - tw) : Math.max(at, cx[i]!)
      if (x > at) segs.push(seg(' '.repeat(x - at))); segs.push(seg(t, color(c))); at = x + tw })
    return segs
  }
  rows.push(under(c => c[0].slice(3), () => 'inactive'))
  rows.push(under(c => (c[1] ? String(c[1]) : '·'), c => (c[1] ? 'suggestion' : 'inactive')))
  return rows
}

// 06 改動熱區:前 10 檔,紫色橫條 + 行數
export function hbarRows(files: [string, number][], width: number): Rows {
  // 行數尾巴照語系量寬:中文「 0 行」5 格是原本算好的,其他語言多出幾格就從條上扣
  const unitX = Math.max(0, dispW(tr('lines_n', { n: 0 })) - 5)
  const mx = Math.max(1, ...files.map(f => f[1])), nameW = Math.min(38, Math.max(16, width - 24)), bw = Math.max(8, width - nameW - 10 - unitX)
  return files.map(([f, n]) => [seg(padL(f.length > nameW ? '…' + f.slice(-(nameW - 1)) : f, nameW) + ' '), seg(blocks(Math.max(1, (n / mx) * bw)), 'permission'), seg(tr('lines_n', { n }), 'inactive')])
}
export { pad, padL, seg, blocks }
