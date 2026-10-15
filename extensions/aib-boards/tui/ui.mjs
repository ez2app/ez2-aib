// aib boards 小窗格的排版引擎(設計稿 §23.3):用跟 aib-boards 面板一樣的 Box / Text / Button 寫法,
// 排成終端機的一格一格字,再印成色碼。只做面板真的用到的那幾種排法(直排 / 橫排、框線、寬度、間距、靠右、置中、折行 / 截斷)。
// 寫法:h(Box, { flexDirection: 'row', ... }, ...children) —— 跟 JSX 編出來的一樣,面板那邊的版面可以逐行對照搬。
import { cw as libCw } from '../hooks/lib.ts'

// 字寬照 tmux 算:面板那支 cw 再補兩件 —— 變體選擇字 / 連接字 0 格;⚓ ⌚ 這類 emoji(Emoji_Presentation)2 格
//   (面板的 plain() 會把 · ● ■ 換成 . * #、把連續空白壓成一格,那是 Claude 畫面才要的,這裡不用)
const cellW = ch => { const p = ch.codePointAt(0) ?? 0
  if (/[\u200d\ufe0e\ufe0f\u0300-\u036f]/.test(ch)) return 0
  if (/\p{Emoji_Presentation}/u.test(ch)) return 2
  if (p >= 0x1f300 && p <= 0x1faff) return 1   // 🖥 這類沒有預設彩色樣式的:tmux 算 1 格(面板那支算 2)
  return libCw(ch) }
const plain = s => s

export const Box = 'box', Text = 'text', Button = 'button'
export function h(type, props, ...children) { return { type, props: props ?? {}, children: children.flat(Infinity).filter(c => c !== null && c !== undefined && c !== false && c !== true) } }

// 面板用的主題色名 → 真彩色(對照 Claude Code 深色主題;#hex 與 ansi256(n) 原樣照用)
const THEME = { claude: '#d77757', success: '#4eba65', error: '#ff6b80', warning: '#ffc107', suggestion: '#b1b9f9', permission: '#b1b9f9',
  autoAccept: '#af87ff', planMode: '#48968c', subtle: '#505050', inactive: '#999999', text: '#e6e6e6', secondaryText: '#999999' }
export function sgrColor(c, bg = false) {
  if (!c) return ''
  const v = THEME[c] ?? c
  let m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(v)
  if (m) return `\x1b[${bg ? 48 : 38};2;${parseInt(m[1], 16)};${parseInt(m[2], 16)};${parseInt(m[3], 16)}m`
  m = /^ansi256\((\d+)\)$/.exec(v)
  if (m) return `\x1b[${bg ? 48 : 38};5;${m[1]}m`
  return ''
}

const W = s => { let n = 0; for (const ch of s) n += cellW(ch); return n }
export const dispW = W

// ---- 文字:把巢狀 Text 攤平成一段一段(帶樣式),再依寬度折行或截斷
function flatten(node, st, out) {
  if (typeof node === 'string' || typeof node === 'number') { out.push({ text: String(node), ...st }); return }
  if (node.type === Text) {
    const p = node.props, s2 = { ...st }
    if (p.color) s2.fg = p.color
    if (p.backgroundColor) s2.bg = p.backgroundColor
    if (p.bold) s2.bold = true
    if (p.dimColor) s2.dim = true
    if (p.inverse) s2.inv = true
    for (const c of node.children) flatten(c, s2, out)
    return
  }
  if (node.type === Button) { out.push(...buttonSegs(node, st)); return }
  for (const c of node.children ?? []) flatten(c, st, out)
}
function buttonSegs(node, st) {
  const label = node.children.map(c => (typeof c === 'object' ? textOf(c) : String(c))).join('')
  const prim = node.props.variant === 'primary'
  return [{ text: `[ ${label} ]`, ...st, fg: prim ? 'suggestion' : st.fg, bold: true, hit: { key: node.props.key ?? label, fn: node.props.onPress } }]
}
export const textOf = n => (typeof n === 'string' || typeof n === 'number' ? String(n) : (n.children ?? []).map(textOf).join(''))

// 一列 = 一串格子 {ch, ...樣式};全形字占兩格(第二格 ch = '')
// 一個「字形」= 一個字 + 黏在它後面的零寬字(FE0F、連接字、附加符號);寬度在這裡一次算好(審查 10-14 #2:
//   FE0F 讓 🖥 這種 1 格 emoji 變 2 格,要在換行 / 截斷之前就知道,不能事後才補)
function glyphs(segs) {
  const out = []
  for (const s of segs) {
    const chs = [...plain(s.text)]
    for (let i = 0; i < chs.length; i++) {
      const ch = chs[i]
      if (ch === '\n') { out.push({ nl: true }); continue }
      let g = ch, w = cellW(ch)
      if (w === 0) {   // 開頭就是零寬字:黏到前一段;FE0F 讓前一段的 1 格 emoji 變 2 格(審查第二輪 #5)
        const prev = out[out.length - 1]
        if (prev && !prev.nl) { prev.c.ch += ch; if (ch === '\ufe0f' && prev.w === 1 && /\p{Emoji}/u.test([...prev.c.ch][0])) { prev.w = 2; prev.c.w = 2 } }
        continue }
      while (i + 1 < chs.length && cellW(chs[i + 1]) === 0 && chs[i + 1] !== '\n') { g += chs[++i] }
      if (w === 1 && g.includes('\ufe0f') && /\p{Emoji}/u.test(ch)) w = 2
      out.push({ w, c: { ch: g, w, fg: s.fg, bg: s.bg, bold: s.bold, dim: s.dim, inv: s.inv, hit: s.hit } })
    }
  }
  return out
}
const cellsOf = gl => gl.flatMap(x => (x.w === 2 ? [x.c, { ch: '', fg: x.c.fg, bg: x.c.bg, hit: x.c.hit }] : [x.c]))
function wrapCells(gl, width, mode) {
  width = Math.max(1, width)
  const lines = [[]]
  let used = 0
  for (const x of gl) {
    if (x.nl) {
      if (mode !== 'wrap') {   // 截斷模式遇到換行:後面還有字就照超寬處理,補 …(審查第二輪 #2)
        if (gl.indexOf(x) < gl.length - 1) { const cur = lines[0]; while (cur.length && used + 1 > width) { const last = cur.pop(); used -= last.w } cur.push({ w: 1, c: { ch: '…', w: 1, fg: 'inactive' } }); return [cellsOf(cur)] }
        break }
      lines.push([]); used = 0; continue }
    let w = x.w, cell = x
    if (w > width) { cell = { w: 1, c: { ...x.c, ch: '…', w: 1 } }; w = 1 }   // 寬 1 放不下寬字:換成 …(審查 #10)
    if (used + w > width) {
      if (mode !== 'wrap') {   // 截斷:留到 width-1 格,補 …;砍到的寬字整個拿掉
        const cur = lines[0]
        while (cur.length && used + 1 > width) { const last = cur.pop(); used -= last.w }
        cur.push({ w: 1, c: { ch: '…', w: 1, fg: 'inactive' } })
        return [cellsOf(cur)]
      }
      if (lines[lines.length - 1].length) { lines.push([]); used = 0 }
    }
    lines[lines.length - 1].push(cell); used += w
  }
  return lines.map(cellsOf)
}
function natText(node) { let best = 0, cur = 0; for (const x of glyphs(flattenAll(node))) { if (x.nl) { best = Math.max(best, cur); cur = 0 } else cur += x.w } return Math.max(best, cur) }
function flattenAll(node) { const out = []; flatten(node, {}, out); return out }

// ---- 排版:render(node, 可用寬) → { lines: 格子列[], w }
const BORDER = { round: '╭╮╰╯─│', single: '┌┐└┘─│', bold: '┏┓┗┛━┃', double: '╔╗╚╝═║' }
const blank = n => Array.from({ length: Math.max(0, n) }, () => ({ ch: ' ' }))
// 切到 w 格:切在寬字中間(前半格留著、後半格被切)⇒ 前半格換空白,不然畫出來多一格(審查 #1)
const padTo = (line, w) => {
  if (w <= 0) return []
  if (line.length < w) return [...line, ...blank(w - line.length)]
  const out = line.slice(0, w), last = out[w - 1]
  if (last && last.w === 2 && line[w]?.ch === '') out[w - 1] = { ch: ' ' }
  return out
}

function natural(node, avail) {
  if (typeof node === 'string' || typeof node === 'number' || node.type === Text || node.type === Button) return Math.min(avail, natText(node))
  const p = node.props
  if (p.width) return Math.min(avail, p.width)
  const frame = (p.borderStyle ? 2 : 0) + 2 * (p.paddingX ?? 0) + (p.paddingLeft ?? 0) + (p.paddingRight ?? 0) + (p.marginLeft ?? 0) + (p.marginRight ?? 0)
  const kids = node.children
  if (!kids.length) return Math.min(avail, frame)
  const inner = avail - frame
  if (p.flexDirection === 'row') return Math.min(avail, frame + kids.reduce((a, k) => a + natural(k, inner), 0) + (p.gap ?? 0) * (kids.length - 1))
  return Math.min(avail, frame + Math.max(...kids.map(k => natural(k, inner))))
}

export function render(node, avail) {
  if (typeof node === 'string' || typeof node === 'number' || node.type === Text || node.type === Button) {
    const mode = node.props?.wrap && node.props.wrap !== 'wrap' ? 'truncate' : 'wrap'
    const lines = wrapCells(glyphs(flattenAll(node)), Math.max(1, avail), mode)
    return { lines, w: Math.max(0, ...lines.map(l => l.length)) }
  }
  const p = node.props
  if (p.display === 'none') return { lines: [], w: 0 }
  const ml = p.marginLeft ?? 0, mr = p.marginRight ?? 0, mt = p.marginTop ?? 0, mb = p.marginBottom ?? 0
  const outerW = Math.max(0, Math.min(avail, p.width ? p.width + ml + mr : avail))
  const boxW = outerW - ml - mr, bd = p.borderStyle ? 1 : 0
  const pl = (p.paddingX ?? 0) + (p.paddingLeft ?? 0), pr = (p.paddingX ?? 0) + (p.paddingRight ?? 0)
  const innerW = Math.max(0, boxW - 2 * bd - pl - pr)
  const kids = node.children, gap = p.gap ?? 0
  let body = []
  if (p.flexDirection === 'row' && p.flexWrap === 'wrap') {
    // 會換行的橫排(分頁列、勾選格):照自然寬一個個排,放不下就換下一列,列與列之間不空行
    const groups = [[]]; let used = 0
    for (const k of kids) { const w = natural(k, innerW), cur = groups[groups.length - 1]
      if (cur.length && used + gap + w > innerW) { groups.push([k]); used = w } else { cur.push(k); used += (cur.length > 1 ? gap : 0) + w } }
    for (const g of groups) body.push(...render(h(Box, { flexDirection: 'row', gap }, ...g), innerW).lines)
  } else if (p.flexDirection === 'row') {
    // 寬度:有 width 的照給;flexGrow 的分剩下的;其他用自然寬,放不下就從後面縮
    const fixed = kids.map(k => (typeof k === 'object' && k.type === Box && k.props.width ? Math.min(innerW, k.props.width + (k.props.marginLeft ?? 0) + (k.props.marginRight ?? 0)) : null))
    const grow = kids.map(k => (typeof k === 'object' && k.type === Box && k.props.flexGrow ? k.props.flexGrow : 0))
    let ws = kids.map((k, i) => fixed[i] ?? (grow[i] ? 0 : natural(k, innerW)))
    kids.forEach((k, i) => { if (typeof k === 'object' && k.type === Button) fixed[i] = ws[i] })   // 按鈕不縮、不折
    const used = () => ws.reduce((a, b) => a + b, 0) + gap * Math.max(0, kids.length - 1)
    const g = grow.reduce((a, b) => a + b, 0)
    if (g) { const room = Math.max(0, innerW - used()); let left = room; grow.forEach((gg, i) => { if (gg) { const add = Math.floor(room * gg / g); ws[i] += add; left -= add } }); const last = grow.lastIndexOf(Math.max(...grow)); if (last >= 0) ws[last] += left }
    const cut = k => typeof k === 'object' && (k.type === Text ? k.props.wrap && k.props.wrap !== 'wrap' : k.type === Box && k.props.flexGrow)
    for (const pass of [true, false]) for (let i = kids.length - 1; i >= 0 && used() > innerW; i--) if (fixed[i] === null && (!pass || cut(kids[i]))) ws[i] = Math.max(1, ws[i] - (used() - innerW))   // 先縮會截斷的,再從後面縮
    for (let i = kids.length - 1; i >= 0 && used() > innerW; i--) if (typeof kids[i] === 'object' && kids[i].type === Box) ws[i] = Math.max(1, ws[i] - (used() - innerW))   // 還放不下:固定寬的框也縮(審查 #1;Ink 預設也會縮)
    const parts = kids.map((k, i) => render(k, ws[i]))
    const H = Math.max(0, ...parts.map(x => x.lines.length))
    let extra = Math.max(0, innerW - used()), between = gap, lead = 0
    if (p.justifyContent === 'space-between' && kids.length > 1) between = gap + extra / (kids.length - 1)
    else if (p.justifyContent === 'flex-end') lead = extra
    else if (p.justifyContent === 'center') lead = Math.floor(extra / 2)
    for (let r = 0; r < H; r++) {
      let line = blank(lead), acc = 0
      parts.forEach((pt, i) => {
        if (i) { acc += between; const n = Math.round(acc); acc -= n; line = line.concat(blank(n)) }
        const l = pt.lines[p.alignItems === 'flex-end' ? r - (H - pt.lines.length) : r] ?? []
        line = line.concat(padTo(l, ws[i]))
      })
      body.push(line)
    }
  } else {
    kids.forEach((k, i) => {
      if (i && gap) for (let gI = 0; gI < gap; gI++) body.push([])
      const kw = p.alignItems && p.alignItems !== 'stretch' ? natural(k, innerW) : innerW   // 文字也量自然寬,靠右 / 置中才有作用(審查 #4)
      const pt = render(k, kw)
      for (const l of pt.lines) {
        const lw = l.length
        const off = p.alignItems === 'flex-end' ? innerW - Math.max(lw, kw) : p.alignItems === 'center' ? Math.floor((innerW - Math.max(lw, kw)) / 2) : 0
        body.push(blank(Math.max(0, off)).concat(l))
      }
    })
  }
  const pt = p.paddingY ?? p.paddingTop ?? 0, pb = p.paddingY ?? p.paddingBottom ?? 0
  body = [...Array.from({ length: pt }, () => []), ...body, ...Array.from({ length: pb }, () => [])]
  if (p.height) body = body.slice(0, p.height)
  const shrinkW = p.width ? boxW : (p.borderStyle || p.flexDirection === 'row' ? boxW : Math.min(boxW, 2 * bd + pl + pr + Math.max(0, ...body.map(l => l.length))))
  const fullW = p.width || p.borderStyle || p.flexGrow ? boxW : shrinkW
  // 左右留白比框還寬時把留白縮掉,中間行才會跟框線一樣寬(審查 #10)
  const room = Math.max(0, fullW - 2 * bd), pl2 = Math.min(pl, room), pr2 = Math.min(pr, room - pl2), inW = room - pl2 - pr2
  let lines = body.map(l => [...blank(pl2), ...padTo(l, inW), ...blank(pr2)])
  if (bd) {
    const [tl, tr, bl, br, hz, vt] = BORDER[p.borderStyle] ?? BORDER.round, bc = { fg: p.borderColor ?? 'subtle', dim: p.borderDimColor }
    const edge = (a, b) => [{ ch: a, ...bc }, ...Array.from({ length: fullW - 2 }, () => ({ ch: hz, ...bc })), { ch: b, ...bc }]
    lines = [edge(tl, tr), ...lines.map(l => [{ ch: vt, ...bc }, ...l, { ch: vt, ...bc }]), edge(bl, br)]
  }
  lines = lines.map(l => [...blank(ml), ...l, ...blank(mr)])
  lines = [...Array.from({ length: mt }, () => []), ...lines, ...Array.from({ length: mb }, () => [])]
  return { lines, w: fullW + ml + mr }
}

// ---- 印出:格子 → 色碼字串;同時收集按鈕位置(給滑鼠點)
export function paint(lines, width, top, rows) {
  const out = [], hits = []
  for (let r = 0; r < rows; r++) {
    const line = lines[top + r] ?? []
    let s = '', last = ''
    for (let x = 0; x < Math.min(width, line.length); x++) {
      const c = line[x]
      if (c.hit) { const h0 = hits[hits.length - 1]; if (h0 && h0.y === r && h0.key === c.hit.key && h0.x1 === x) h0.x1 = x + 1; else hits.push({ y: r, x0: x, x1: x + 1, key: c.hit.key, fn: c.hit.fn }) }
      if (c.ch === '') continue   // 後半格:點擊範圍算進去(上一行),字不再印一次(審查 #3)
      const sty = `\x1b[0m${c.bold ? '\x1b[1m' : ''}${c.dim ? '\x1b[2m' : ''}${c.inv ? '\x1b[7m' : ''}${sgrColor(c.fg)}${sgrColor(c.bg, true)}`
      if (sty !== last) { s += sty; last = sty }
      s += c.ch
    }
    out.push(s + '\x1b[0m\x1b[K')
  }
  return { text: out.join('\r\n'), hits }
}
