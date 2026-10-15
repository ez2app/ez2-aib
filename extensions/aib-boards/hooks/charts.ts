// 原本從 tools/claude-mods/aib-seat/hooks/charts.ts(aib-seat 0.10.4)整份搬來;10-14 aib-seat 已拿掉(設計稿 §24),這裡是唯一一份。
// 面板圖表(the maintainer 10-09「圓餅圖、曲線圖、直條圖、六角寶石圖」):終端機沒有圖片可用,改用點字點陣(每格 2×4 點)與方塊字。
// 全部是純函式:輸入數值與 frame,輸出「每列一串 {文字, 顏色}」;register.tsx 只負責把它們包成 <Text>。

export type Seg = { text: string; color?: string; dim?: boolean }
export type Rows = Seg[][]

export const PALETTE = ['claude', 'success', 'suggestion', 'warning', 'permission', 'error', 'autoAccept', 'planMode'] as const

const DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]] as const

/** 點字畫布:w×h 格 = (2w)×(4h) 點。同一格最後畫上的顏色勝出。 */
export class Canvas {
  readonly bits: number[]
  readonly colors: (string | undefined)[]
  readonly w: number
  readonly h: number
  // 不用「參數屬性」寫法:aib boards 小窗格用 Node 直接跑這支,Node 只會去掉型別、不支援這種寫法(設計稿 §23.3)
  constructor(w: number, h: number) {
    this.w = w; this.h = h
    this.bits = new Array(w * h).fill(0)
    this.colors = new Array(w * h).fill(undefined)
  }
  set(x: number, y: number, color?: string): void {
    const px = Math.round(x), py = Math.round(y)
    if (px < 0 || py < 0 || px >= this.w * 2 || py >= this.h * 4) return
    const i = Math.floor(py / 4) * this.w + Math.floor(px / 2)
    this.bits[i] = (this.bits[i] ?? 0) | DOT[py % 4]![px % 2]!
    if (color) this.colors[i] = color
  }
  line(x0: number, y0: number, x1: number, y1: number, color?: string, dash = 0): void {
    const n = Math.max(1, Math.ceil(Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0))))
    for (let k = 0; k <= n; k++) {
      if (dash && Math.floor(k / dash) % 2 === 1) continue
      this.set(x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n, color)
    }
  }
  rows(): Rows {
    const out: Rows = []
    for (let r = 0; r < this.h; r++) {
      const segs: Seg[] = []
      for (let c = 0; c < this.w; c++) {
        const i = r * this.w + c
        const b = this.bits[i] ?? 0
        push(segs, b ? String.fromCharCode(0x2800 + b) : ' ', b ? this.colors[i] : undefined)
      }
      out.push(segs)
    }
    return out
  }
}

function push(segs: Seg[], ch: string, color?: string, dim?: boolean): void {
  const last = segs[segs.length - 1]
  if (last && last.color === color && last.dim === dim) last.text += ch
  else segs.push({ text: ch, color, dim })
}

export function rowsText(rows: Rows): string[] { return rows.map(r => r.map(s => s.text).join('')) }

/** 甜甜圈圓餅:w 格寬、h 列高(格子約 1:2,w≈2h 才圓)。一道亮光(▓)隨 frame 繞圈;進場時扇形依 frame 逐步展開。 */
export function donut(parts: { label: string; n: number }[], w: number, h: number, frame: number): Rows {
  const total = parts.reduce((a, p) => a + p.n, 0)
  const out: Rows = []
  const sweep = Math.min(1, (frame + 1) / 10) * Math.PI * 2
  const glint = ((frame * 0.35) % (Math.PI * 2))
  for (let r = 0; r < h; r++) {
    const segs: Seg[] = []
    for (let c = 0; c < w; c++) {
      const x = (c + 0.5 - w / 2) / (w / 2), y = (r + 0.5 - h / 2) / (h / 2)
      const d = Math.sqrt(x * x + y * y)
      if (total === 0 || d > 1 || d < 0.48) { push(segs, d <= 1 && d >= 0.48 ? '·' : ' ', 'subtle', true); continue }
      let a = Math.atan2(x, -y); if (a < 0) a += Math.PI * 2   // 12 點鐘起、順時針
      if (a > sweep) { push(segs, '·', 'subtle', true); continue }
      let acc = 0, k = 0
      for (; k < parts.length; k++) { acc += (parts[k]!.n / total) * Math.PI * 2; if (a <= acc) break }
      const near = Math.abs(((a - glint + Math.PI * 3) % (Math.PI * 2)) - Math.PI) > Math.PI - 0.35
      push(segs, near ? '▓' : '█', PALETTE[Math.min(k, parts.length - 1) % PALETTE.length])
    }
    out.push(segs)
  }
  return out
}

/** 曲線:values 0–100,最新在右;一條紅色虛線標 limit(85%)。 */
export function curve(values: number[], w: number, h: number, limit = 85, color = 'claude'): Rows {
  const cv = new Canvas(w, h)
  const W = w * 2 - 1, H = h * 4 - 1
  const yOf = (v: number) => H - (Math.max(0, Math.min(100, v)) / 100) * H
  cv.line(0, yOf(limit), W, yOf(limit), 'error', 2)
  const pts = values.slice(-(W + 1))
  const x0 = W - (pts.length - 1)
  for (let i = 1; i < pts.length; i++) cv.line(x0 + i - 1, yOf(pts[i - 1]!), x0 + i, yOf(pts[i]!), pts[i]! >= limit ? 'error' : color)
  if (pts.length === 1) cv.set(W, yOf(pts[0]!), color)
  return cv.rows()
}

const EIGHTHS = ' ▁▂▃▄▅▆▇█'
/** 直條:每根 2 格寬、間隔 1 格;高 h 列用八分之一方塊收尾。最新一根依 frame 閃(亮/暗交替)。 */
export function bars(values: number[], h: number, frame: number, color = 'success'): Rows {
  const max = Math.max(1, ...values)
  const out: Rows = []
  for (let r = 0; r < h; r++) {
    const segs: Seg[] = []
    values.forEach((v, i) => {
      const eighths = Math.round((v / max) * h * 8)
      const fromBottom = h - 1 - r
      const cell = Math.max(0, Math.min(8, eighths - fromBottom * 8))
      const newest = i === values.length - 1
      push(segs, EIGHTHS[cell]!.repeat(2), newest ? (frame % 2 ? 'warning' : color) : color, !newest && cell === 0)
      if (i < values.length - 1) push(segs, ' ')
    })
    out.push(segs)
  }
  return out
}

/** 六角寶石(雷達):6 個 0–1 的值。三圈六角網格、六根輻條、值的多邊形、一根依 frame 旋轉的掃描線。 */
export function hexRadar(values: number[], w: number, h: number, frame: number): Rows {
  const cv = new Canvas(w, h)
  const cx = w - 0.5, cy = h * 2 - 0.5
  const R = Math.min(w - 1, h * 2 - 1)
  const at = (k: number, r: number): [number, number] => {
    const a = -Math.PI / 2 + (k * Math.PI) / 3
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r]
  }
  for (const ring of [1 / 3, 2 / 3, 1]) {
    for (let k = 0; k < 6; k++) {
      const [x0, y0] = at(k, R * ring), [x1, y1] = at(k + 1, R * ring)
      cv.line(x0, y0, x1, y1, 'subtle', ring < 1 ? 2 : 0)
    }
  }
  for (let k = 0; k < 6; k++) { const [x, y] = at(k, R); cv.line(cx, cy, x, y, 'subtle', 2) }
  const sa = -Math.PI / 2 + frame * 0.3
  cv.line(cx, cy, cx + Math.cos(sa) * R, cy + Math.sin(sa) * R, 'warning')
  const v = Array.from({ length: 6 }, (_, k) => Math.max(0.05, Math.min(1, values[k] ?? 0)))
  for (let k = 0; k < 6; k++) {
    const [x0, y0] = at(k, R * v[k]!), [x1, y1] = at((k + 1) % 6, R * v[(k + 1) % 6]!)
    cv.line(x0, y0, x1, y1, 'claude')
    cv.set(x0, y0, 'claude')
  }
  return cv.rows()
}

/** "HH:MM:SS" ⇒ 當天第幾秒;認不得回 null。 */
export function secOfDay(t: string): number | null {
  const m = /^(\d\d):(\d\d)(?::(\d\d))?$/.exec(t)
  return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3] ?? 0) : null
}

/** 把時間點分桶:以最新一點為準,往回 n 桶、每桶 size 秒,回每桶的筆數(最舊在左)。 */
export function bucket(times: number[], n: number, size: number): number[] {
  const out = new Array(n).fill(0)
  if (!times.length) return out
  const end = Math.max(...times)
  for (const t of times) {
    const k = n - 1 - Math.floor((end - t) / size)
    if (k >= 0 && k < n) out[k]++
  }
  return out
}

/** 動態圓餅:整圈依 frame 慢轉;第 focus 片往外推(其他片外徑 0.86)並用 ▓ 加亮;中心兩列寫那一片的名稱與百分比。 */
export function spinPie(parts: { label: string; n: number }[], w: number, h: number, frame: number): Rows {
  const total = parts.reduce((a, p) => a + p.n, 0)
  const focus = parts.length ? Math.floor(frame / 8) % parts.length : -1
  const rot = frame * 0.06
  const out: Rows = []
  const label = focus >= 0 ? [parts[focus]!.label.slice(0, 6), `${Math.round((parts[focus]!.n / total) * 100)}%`] : ['', '']
  const mid = Math.floor(h / 2) - 1
  for (let r = 0; r < h; r++) {
    const segs: Seg[] = []
    for (let c = 0; c < w; c++) {
      const x = (c + 0.5 - w / 2) / (w / 2), y = (r + 0.5 - h / 2) / (h / 2)
      const d = Math.sqrt(x * x + y * y)
      if (d < 0.5) {
        const txt = r === mid ? label[0]! : r === mid + 1 ? label[1]! : ''
        const start = Math.floor((w - txt.length) / 2)
        const ch = txt && c >= start && c < start + txt.length ? txt[c - start]! : ' '
        push(segs, ch, focus >= 0 ? PALETTE[focus % PALETTE.length] : undefined)
        continue
      }
      if (total === 0 || d > 1) { push(segs, ' '); continue }
      let a = Math.atan2(x, -y) - rot; a = ((a % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)
      let acc = 0, k = 0
      for (; k < parts.length; k++) { acc += (parts[k]!.n / total) * Math.PI * 2; if (a <= acc) break }
      k = Math.min(k, parts.length - 1)
      if (k !== focus && d > 0.86) { push(segs, ' '); continue }
      push(segs, k === focus ? '▓' : '█', PALETTE[k % PALETTE.length], k !== focus && focus >= 0)
    }
    out.push(segs)
  }
  return out
}

export type Blip = { angle: number; r: number; color: string }
/** 雷達掃描:三圈距離環、十字線、依 frame 旋轉的掃描線 + 4 道漸暗餘光;光點在掃描線剛掃過(1.2 弧度內)時亮成一團,其餘只剩一點。
 *  r 0 = 中心(最近有動靜),1 = 外圈。 */
export function scanRadar(blips: Blip[], w: number, h: number, frame: number): Rows {
  const cv = new Canvas(w, h)
  const cx = w - 0.5, cy = h * 2 - 0.5, R = Math.min(w - 1, h * 2 - 1)
  for (const ring of [1 / 3, 2 / 3, 1]) {
    const n = Math.ceil(R * ring * 6)
    for (let i = 0; i < n; i += ring < 1 ? 3 : 1) {
      const a = (i / n) * Math.PI * 2
      cv.set(cx + Math.cos(a) * R * ring, cy + Math.sin(a) * R * ring, 'subtle')
    }
  }
  cv.line(cx - R, cy, cx + R, cy, 'subtle', 2); cv.line(cx, cy - R, cx, cy + R, 'subtle', 2)
  const sweep = (frame * 0.25) % (Math.PI * 2)
  const trail = ['success', 'success', 'subtle', 'subtle'] as const
  for (let k = trail.length; k >= 1; k--) {
    const a = sweep - k * 0.11
    cv.line(cx, cy, cx + Math.cos(a) * R, cy + Math.sin(a) * R, trail[k - 1], k > 2 ? 2 : 0)
  }
  cv.line(cx, cy, cx + Math.cos(sweep) * R, cy + Math.sin(sweep) * R, 'warning')
  for (const b of blips) {
    const x = cx + Math.cos(b.angle) * R * b.r, y = cy + Math.sin(b.angle) * R * b.r
    const behind = ((sweep - b.angle) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2)
    if (behind < 1.2) { for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1], [-1, 0], [0, -1]] as const) cv.set(x + dx, y + dy, b.color) }
    else cv.set(x, y, 'subtle')
  }
  return cv.rows()
}

/** 席碼 ⇒ 固定角度(同一席每次都在同一方位)。 */
export function angleOf(code: string): number {
  let hsh = 0
  for (const ch of code) hsh = (hsh * 31 + ch.charCodeAt(0)) >>> 0
  return ((hsh % 360) / 360) * Math.PI * 2
}

/** 閒了幾秒 ⇒ 距中心 0–1(對數:剛動 ≈ 0.1,一小時以上 = 1)。 */
export function idleToR(sec: number): number {
  return Math.max(0.1, Math.min(1, Math.log10(1 + Math.max(0, sec)) / Math.log10(3600)))
}
