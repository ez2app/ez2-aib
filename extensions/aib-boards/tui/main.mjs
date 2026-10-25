// aib boards 小窗格(設計稿 §23,第一步 B1:外框 + 今天頁)。不是 Claude 的席(Codex / Copilot / grok / Gemini)也有這台電腦。
// 用法:node main.mjs --seat <席碼>   (open.sh 會在那一席的 tmux 視窗右邊切一格跑這支)
// 資料:seat_screen.py --seat <碼>(跟 aib-boards 同一支資料層);版面照 aib-boards 的今天頁逐段搬(register.tsx「今天頁 = 流程儀表板」)。
import { execFile, execFileSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync, mkdirSync, watch, readdirSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { h, Box, Text, Button, render, paint, textOf, dispW } from './ui.mjs'
import { ctxCell, leaseCell, mailCell, myPkgs, pkgCell, pkgStages, bar as meter, hhmm, shortBranch, logRows, fit, groupTodos, levelColor, levelDot, roleFlow, flowColor, themeOf, bumpUsage, effectiveEx, foldOrder,
  ago, nowState, knowParts, parseScreen, parseCharts, parsePlan, parseDm, SPIN, TABS, withLogo, logoSegs, labelW, rowLabelW, logTagW } from '../hooks/lib.ts'
import { cardOf, upsert, relayPath, iconOf, clip, STATUS } from '../hooks/seat/mailcard.ts'
import { TIER_DEFAULT, TIER_ALL, CHART_TITLE, tierOf, buildCard, pinFirst } from '../hooks/chartset.ts'
import { normalize, makeT, useT, tr } from '../hooks/i18n.ts'
import { makePoller } from './locale-poll.mjs'

const args = process.argv.slice(2), arg = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined }
const SEAT = arg('--seat') ?? ''
const HOME = homedir()
// 狀態根:跟 seats_dir_lib.state_root 同一個找法(先 EZ2_AIB_STATE_ROOT、再 EZ2_AIB_HOME、最後 ~/.ez2-aib)—— CTX、信件、圖示、勾選四處共用(審查 #9)
const stateRoot = () => process.env.EZ2_AIB_STATE_ROOT || process.env.EZ2_AIB_HOME || `${HOME}/.ez2-aib`
const now = () => Date.now() / 1000

// ---- 語系(docs/DESIGN-aib-languages-04e0a-1014.md §5、seat-screen 設計稿 §23.10):這一格不在 Claude 裡,沒有 aib-languages 幫忙設 env ⇒
//   問 i18n_lib.py --resolve(單一順位:env > 艙檔 > 人檔 > 系統 > zh-TW,回 {locale, source, files});每 5 秒看那幾個檔的 mtime,
//   變了才重問,語系換了就重讀字典、重畫。設定檔路徑不自己拼,一律用 --resolve 給的;字跟 Claude 面板共用 mod.aib_boards.* 鍵。
const PREFIX = 'mod.aib_boards'
const REPO = (() => { for (const r of [fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, ''), process.env.EZ2_AIB_ROOT, `${HOME}/ez2-ai-b`]) if (r && existsSync(`${r}/src/cockpit/i18n_lib.py`)) return r; return null })()
const i18nArgs = a => ['-I', `${REPO}/src/cockpit/i18n_lib.py`, ...a]
const lastJson = out => JSON.parse(String(out).trim().split('\n').pop() || '{}')
let LOC = 'zh-TW', poller = null
const FORCED = normalize(arg('--locale'))   // 測試 / 手動指定:不問設定檔、不輪詢
function useBundle(loc, data) { LOC = loc; useT(loc, makeT(PREFIX, data)) }
const mtimes = files => { const o = {}; for (const f of files) { try { o[f] = statSync(f).mtimeMs } catch { o[f] = -1 } } return o }   // -1 = 檔不在(--clear 掉也算變)
function loadLocaleSync() {   // 開窗那一下:同步問,第一張畫面就是對的語言
  let loc = FORCED, files = []
  if (!REPO) { useBundle(loc ?? 'zh-TW', {}); return }
  if (!loc) { try { const r = lastJson(execFileSync('python3', i18nArgs(['--resolve']), { encoding: 'utf8', timeout: 10000, stdio: ['ignore', 'pipe', 'ignore'] })); files = Object.values(r.files ?? {}).filter(Boolean); loc = normalize(r.locale) } catch { /* 問不到就預設;輪詢會每輪重問 */ } }
  loc = loc ?? 'zh-TW'
  let data = {}
  try { data = lastJson(execFileSync('python3', i18nArgs(['--locale', loc, '--prefix', PREFIX, '--json']), { encoding: 'utf8', timeout: 10000, stdio: ['ignore', 'pipe', 'ignore'] })) } catch { /* 字典讀不到 ⇒ 畫面出 !!鍵!!,不偷用中文 */ }
  useBundle(loc, data)
  if (FORCED) return
  // 每 5 秒(setInterval 在下面):看 --resolve 給的檔 mtime,變了才重問;邏輯在 locale-poll.mjs(失敗不記「看過」,下一輪重試)
  poller = makePoller({
    mtimes,
    resolve: cb => execFile('python3', i18nArgs(['--resolve']), { timeout: 10000 }, (e, out) => { try { if (e) throw e; const r = lastJson(out); cb(null, { loc: normalize(r.locale), files: Object.values(r.files ?? {}).filter(Boolean) }) } catch (x) { cb(x) } }),
    load: (l, cb) => execFile('python3', i18nArgs(['--locale', l, '--prefix', PREFIX, '--json']), { timeout: 10000 }, (e, out) => { try { if (e) throw e; cb(null, lastJson(out)) } catch (x) { cb(x) } }),
    current: () => LOC,
    onSwitch: (l, d) => { useBundle(l, d); draw() },
  }, files, mtimes(files))
}
const pollLocale = () => { poller?.tick() }
// ---- 資料層路徑:跟 aib-boards 的 scriptPath() 同一個找法
function s1Path() {
  let dev = ''
  try { dev = readFileSync(`${stateRoot()}/state/aib-boards/dev-seat-screen.txt`, 'utf8').trim() } catch { /* 沒有就算了 */ }
  const root = process.env.EZ2_AIB_ROOT
  for (const p of [process.env.EZ2_AIB_SEAT_SCREEN, dev, root && `${root}/src/cockpit/seat_screen.py`, `${HOME}/ez2-ai-b/src/cockpit/seat_screen.py`]) if (p && existsSync(p)) return p
  return null
}
const S = { data: null, err: null, at: 0, busy: false }
// 這一格直接畫在 tmux 上、不經 Claude Code ⇒ 跟資料層說可以給原廠 24-bit 色(brand_color 在 tmux 裡預設給 256 號,那是給 Claude Code 用的)
const ENV = { ...process.env, CLAUDE_CODE_TMUX_TRUECOLOR: '1' }
function load(fresh = false) {
  const p = s1Path()
  if (!p) { S.err = tr('no_s1_short'); draw(); return }
  if (S.busy) return
  S.busy = true; draw()
  execFile('python3', ['-I', p, ...(SEAT ? ['--seat', SEAT] : []), ...(fresh ? ['--fresh'] : [])], { timeout: 25000, maxBuffer: 8 << 20, env: ENV }, (e, out, errOut) => {
    S.busy = false
    if (e) S.err = (String(errOut || e.message).trim().split('\n').pop() || 'rc≠0').slice(0, 120)
    else { try { S.data = parseScreen(out); S.err = null; S.at = now() } catch (x) { S.err = String(x.message ?? x) } }
    draw()
  })
}
// 重的(圖表 / 專案計畫樹):點開那頁才算(§21.2),留 60 秒;r 強制重算
const C = { data: null, err: null, at: 0, busy: false }, P = { data: null, err: null, at: 0, busy: false }, D = { data: null, err: null, at: 0, busy: false }
function loadHeavy(box, flag, parse, fresh = false) {
  const p = s1Path()
  if (!p || box.busy || (!fresh && box.data && now() - box.at < 60)) return
  box.busy = true; draw()
  execFile('python3', ['-I', p, flag, ...(SEAT ? ['--seat', SEAT] : []), ...(fresh ? ['--fresh'] : [])], { timeout: 25000, maxBuffer: 16 << 20, env: ENV }, (e, out, errOut) => {
    box.busy = false
    if (box === D && dmAgain) { dmAgain = false; setTimeout(() => loadHeavy(D, '--dm', parseDm, true), 0) }   // 讀的時候又來信:讀完補一次(審查第二輪 #3)
    if (e) box.err = (String(errOut || e.message).trim().split('\n').pop() || 'rc≠0').slice(0, 120)
    else { try { box.data = parse(out); box.err = null; box.at = now() } catch (x) { box.err = String(x.message ?? x) } }
    draw()
  })
}
const loadTab = (t, fresh = false) => { if (t === 'charts' || t === 'custom') loadHeavy(C, '--charts', parseCharts, fresh); if (t === 'proj') loadHeavy(P, '--plan', parsePlan, fresh); if (t === 'dm') loadHeavy(D, '--dm', parseDm, fresh) }
// 自訂勾了哪些圖:跟 Claude 席的 aib-boards 同一個檔(同一席兩邊看到的一樣)
const picksFile = id => `${stateRoot()}/state/aib-boards/${id}.json`
function loadPicks(id, tier) { try { const v = JSON.parse(readFileSync(picksFile(id), 'utf8')).charts; if (Array.isArray(v)) return v } catch { /* 用預設 */ } return TIER_DEFAULT[tier] }
// 同一個檔還放 usage(§22.5 第 3 步):寫之前讀回別的欄位合併,不蓋掉對方(Claude 面板同一個檔、同一種合併)
function readPrefs(id) { try { return JSON.parse(readFileSync(picksFile(id), 'utf8')) ?? {} } catch { return {} } }
function writePrefs(id, patch) { try { mkdirSync(`${stateRoot()}/state/aib-boards`, { recursive: true }); writeFileSync(picksFile(id), JSON.stringify({ ...readPrefs(id), ...patch, at: now() })) } catch { /* 寫不進去就只在這次有效 */ } }
const usageOf = id => { const u = readPrefs(id).usage; return u && typeof u === 'object' ? u : {} }
function savePicks(id, list) { try { writePrefs(id, { charts: list }) } catch { /* 寫不進去就只這次有效 */ } }
const tierLabel = t => tr(`tier_${['captain', 'lead', 'qa', 'eng', 'design'].includes(t) ? t : 'eng'}`)
const took = ms => (ms !== undefined ? tr('took', { n: ms }) : '')
const tw = x => [...x].reduce((a, ch) => a + (/[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\uff00-\uff60]/.test(ch) ? 2 : 1), 0)
let pickOverride = null   // 這一輪剛勾的(檔案寫完前畫面先反應)

// ---- 專案頁(照 register.tsx「proj」)
function projPage(cols) {
  const pd = P.data, d = S.data
  if (!pd) return h(Box, { paddingX: 2, marginTop: 1, flexDirection: 'column' }, h(Text, { color: 'warning' }, P.err ? tr('proj_err', { err: P.err }) : tr('plan_loading')), !d?.project ? h(Text, { dimColor: true }, tr('bind_hint')) : null)
  const kids = new Map(); pd.nodes.forEach(n => { const k = n.parent ?? ''; kids.set(k, [...(kids.get(k) ?? []), n]) })
  const out = [], ST = { done: ['✔', 'success'], in_progress: ['▶', 'suggestion'], waiting_prereq: ['⛓', 'warning'], blocked: ['✖', 'error'], partial: ['◐', 'inactive'], pending: ['○', 'inactive'] }
  const LY = { goal: tr('layer_goal'), major: tr('layer_major'), sub: tr('layer_sub'), task: 'Task' }
  const walk = (pid, depth) => (kids.get(pid) ?? []).forEach(n => {
    const [ic, c] = ST[n.status] ?? ['·', 'inactive'], mine = !!d?.code && (n.owner ?? '').includes(d.code), pr = n.progress == null ? null : Math.max(0, Math.min(1, n.progress))
    out.push(h(Text, { wrap: 'truncate-end' }, '  '.repeat(depth), h(Text, { color: c, dimColor: n.status === 'in_progress' && !saver && frame % 4 >= 2 }, `${ic} `), h(Text, { dimColor: true }, `${LY[n.layer] ?? n.layer} `), h(Text, { bold: mine, color: mine ? 'claude' : 'text' }, n.title),
      pr != null ? h(Text, { color: 'success' }, ` ${'█'.repeat(Math.round(pr * 8))}`, h(Text, { color: 'subtle' }, '░'.repeat(8 - Math.round(pr * 8))), ` ${Math.round(pr * 100)}%`) : null, ...(n.owner ? Lg(` · ${n.owner}`, { dim: true }) : [])))
    walk(n.id, depth + 1) })
  const roots = pd.nodes.filter(n => !n.parent || !pd.nodes.some(m => m.id === n.parent)); kids.set('__root', roots); walk('__root', 0)
  const cnt = st => pd.nodes.filter(n => n.status === st).length, PW = Math.min(120, cols - 4)
  return h(Box, { flexDirection: 'column', paddingX: 2, marginTop: 1, width: PW + 4 },
    h(Box, { flexDirection: 'row', gap: 3 }, h(Text, { bold: true }, tr('tab_proj')), h(Text, null, pd.title ?? ''), h(Text, { dimColor: true }, pd.project_uid)),
    h(Box, { flexDirection: 'row', gap: 3, marginBottom: 1 }, h(Text, { dimColor: true }, tr('nodes_n', { n: pd.nodes.length })), h(Text, { color: 'success' }, `✔ ${cnt('done')}`), h(Text, { color: 'suggestion' }, `▶ ${cnt('in_progress')}`), h(Text, { color: 'warning' }, `⛓ ${cnt('waiting_prereq')}`), h(Text, { color: 'error' }, `✖ ${cnt('blocked')}`), h(Text, { dimColor: true }, tr('mine_lit'))),
    h(Box, { flexDirection: 'column', borderStyle: 'round', borderColor: 'subtle', paddingX: 2 }, ...out))
}

// ---- 圖表頁 / 自訂頁(照 register.tsx「charts / custom」;卡片內容用同一支 chartset.buildCard)
function chartsPage(cols, custom) {
  const cd = C.data, d = S.data
  if (!cd) return h(Box, { paddingX: 2, marginTop: 1 }, h(Text, { color: C.err ? 'error' : 'warning' }, C.err ? tr('charts_err', { err: C.err }) : tr('charts_loading')))
  const tier = tierOf(d?.tier), seatId = d?.seat_id ?? 'unknown', list = pickOverride ?? loadPicks(seatId, tier)
  if (custom) {
    const all = TIER_ALL[tier], toggle = k => () => { const cur = list.includes(k) ? list.filter(x => x !== k) : [...list, k]; pickOverride = cur; savePicks(seatId, cur); draw() }
    const setAll = v => () => { pickOverride = v; savePicks(seatId, v); draw() }
    const cellW = Math.max(...all.map(k => tw(CHART_TITLE[k] ?? k))) + 14, inner = Math.max(cellW, Math.min(cols - 8, 120))
    const perRow = Math.max(1, Math.floor(inner / cellW)), gridRows = []
    for (let k = 0; k < all.length; k += perRow) gridRows.push(all.slice(k, k + perRow))
    const order = pinFirst(list), no = k => (list.includes(k) ? String(order.indexOf(k) + 1).padStart(2, '0') : '  ')
    return h(Box, { flexDirection: 'column', paddingX: 2, marginTop: 1 },
      h(Text, { bold: true }, tr('custom_title')),
      h(Text, { dimColor: true }, tr('custom_sub_tui', { value: tierLabel(d?.tier), v1: list.length, v2: all.length })),
      h(Box, { flexDirection: 'column', borderStyle: 'round', borderColor: 'subtle', paddingX: 2, paddingY: 1, marginTop: 1, width: perRow * cellW + 6 },
        ...gridRows.map((row, ri) => h(Box, { flexDirection: 'row', marginBottom: ri < gridRows.length - 1 ? 1 : 0 },
          ...row.map(k => h(Box, { width: cellW }, h(Button, { key: `pick-${k}`, variant: list.includes(k) ? 'primary' : 'secondary', onPress: toggle(k) }, `${list.includes(k) ? '☑' : '☐'} ${no(k)} ${CHART_TITLE[k] ?? k}`)))))),
      h(Box, { flexDirection: 'row', gap: 2, marginTop: 1 }, h(Button, { key: 'pick-reset', onPress: setAll(TIER_DEFAULT[tier]) }, tr('pick_reset')), h(Button, { key: 'pick-all', onPress: setAll(all) }, tr('pick_all')), h(Button, { key: 'pick-none', onPress: setAll([]) }, tr('pick_none'))),
      h(Box, { marginTop: 1 }, h(Text, { dimColor: true }, tr('custom_hint'))))
  }
  // 放不下兩欄(每欄最少 44)⇒ 一欄,每張卡用整寬,圖也照整寬畫
  const half0 = Math.max(44, Math.floor((cols - 3) / 2)), two = half0 * 2 + 1 <= cols - 2, half = two ? half0 : cols - 2
  const ctxN = readCtx(d), cx = { cd, ctx: typeof ctxN === 'number' ? ctxN : null, frame, half, full: cols - 2, now: now(), scr: d }
  // 半寬放不下的圖(雷達、熱力格、和弦這類固定大小的)⇒ 用整寬重畫、獨佔一列,不讓它被折行
  const rowW = c => Math.max(0, ...c.body.map(r => dispW(r.map(sg => sg.text).join(''))))
  const cards = pinFirst(list).map(k => { const c = buildCard(k, cx); if (!c || c.wide || !two || rowW(c) <= half - 4) return c
    const w = buildCard(k, { ...cx, half: cols - 2 }); return w ? { ...w, wide: true } : c }).filter(Boolean)
  const missingCards = pinFirst(list).filter(k => !buildCard(k, cx)).map(k => CHART_TITLE[k] ?? k)
  const rowsEl = rows => rows.map(r => h(Text, null, ...r.map(sg => h(Text, { color: sg.color, dimColor: sg.dim }, sg.text))))
  const card = (c, i) => h(Box, { flexDirection: 'column', width: c.wide || !two ? cols - 2 : half, borderStyle: 'round', borderColor: 'subtle', paddingX: 1, marginBottom: 1 },
    h(Text, { wrap: 'truncate-end' }, h(Text, { color: 'warning' }, `${String(i + 1).padStart(2, '0')} `), h(Text, { bold: true }, c.title), c.big ? ` · ${c.big}` : ''),
    h(Text, { dimColor: true, wrap: 'truncate-end' }, c.how), ...rowsEl(c.body), h(Text, { dimColor: true, wrap: 'truncate-end' }, c.src))
  // 兩欄各自往下疊:每張卡放到比較矮的那一欄;寬卡獨佔一列;窗格太窄(放不下兩張)就一欄
  const blocks = []
  let colA = [], colB = [], hA = 0, hB = 0
  const flush = () => { if (colA.length || colB.length) blocks.push(h(Box, { flexDirection: 'row', gap: 1 }, h(Box, { flexDirection: 'column', width: half }, ...colA), h(Box, { flexDirection: 'column', width: half }, ...colB))); colA = []; colB = []; hA = hB = 0 }
  cards.forEach((c, i) => {
    if (c.wide || !two) { flush(); blocks.push(card(c, i)); return }
    const hh = c.body.length + 4
    if (hA <= hB) { colA.push(card(c, i)); hA += hh } else { colB.push(card(c, i)); hB += hh }
  })
  flush()
  return h(Box, { flexDirection: 'column', paddingX: 1, marginTop: 1 },
    h(Text, { dimColor: true }, `${tr('charts_head_tui', { value: tierLabel(d?.tier), n: cards.length, when: ago(now() - cd.measured_at) })}${took(cd.took_ms)}${missingCards.length ? tr('charts_missing', { what: missingCards.join(tr('sep')) }) : ''}`),
    ...blocks)
}

// ---- 對話頁(照 register.tsx「dm」:左 8 像 telegram、右 2 收件匣;我在右、對方在左)
function dmPage(cols) {
  const dd = D.data, d = S.data
  if (!dd) return h(Box, { paddingX: 2, marginTop: 1 }, h(Text, { color: D.err ? 'error' : 'warning' }, D.err ? tr('dm_err', { err: D.err }) : tr('dm_loading')))
  const col = s => (s === 'waiting' ? 'error' : s === 'read' ? 'warning' : 'success'), lbl = s => (s === 'waiting' ? tr('st_unread') : s === 'read' ? tr('st_read_open') : s === 'done' ? tr('st_done') : s)
  // 先定右邊收件匣(最少 18),左邊拿剩下的 ⇒ 兩框加間距剛好等於可用寬(審查 #1:原本 70 欄時 52+1+18=71 超出一格)
  const TW2 = cols - 4, RW = Math.max(18, Math.floor(TW2 * 0.2)), LW = Math.max(20, TW2 - RW - 1)
  const seats = dd.seats ?? {}, me = d?.code ?? ''
  const FRAME = ['claude', 'success', 'suggestion', 'warning', 'permission', 'autoAccept', 'planMode']
  const seatColor = c => { if (!c) return 'inactive'; let hh = 0; for (const ch of c) hh = (hh * 31 + ch.charCodeAt(0)) >>> 0; return FRAME[hh % FRAME.length] }
  const logoEl = c => (c && seats[c]?.logo ? h(Text, { color: seats[c]?.color || undefined }, `${seats[c].logo} `) : null)
  const who = c => { const x = c ? seats[c] : undefined; return `${x?.emoji ? x.emoji + ' ' : ''}${x?.name ?? x?.role ?? ''}${c ? ` @${c}` : ''}`.trim() }
  const ST = { unread: [tr('st_unread'), 'error'], read: [tr('st_read'), 'warning'], done: [tr('st_done'), 'success'], held: [tr('st_held'), 'permission'], archived: [tr('st_archived'), 'inactive'] }
  const allChat = dd.chat ?? [], fk = ex.chatf
  const chat = fk ? allChat.filter(r => `${r.out ? 'out' : 'in'}:${r.state ?? 'unread'}` === fk) : allChat, shown = ex.chat ? chat : chat.slice(-24)
  const tally = out => Object.keys(ST).map(k => [k, allChat.filter(r => r.out === out && (r.state ?? 'unread') === k).length]).filter(x => x[1])
  const setF = v => () => { ex.chatf = v; draw() }
  const summary = h(Box, { flexDirection: 'column', marginBottom: 1 },
    ...[[false, tr('recv')], [true, tr('sent')]].map(([out, nm]) => h(Box, { flexDirection: 'row', gap: 1, flexWrap: 'wrap' }, h(Box, { width: 11 }, h(Text, { dimColor: true }, nm)),
      ...tally(out).map(([k, n]) => { const key = `${out ? 'out' : 'in'}:${k}`; return h(Button, { key: `f-${key}`, variant: fk === key ? 'primary' : 'secondary', onPress: setF(fk === key ? undefined : key) }, `${ST[k][0]} ${n}`) }),
      tally(out).length ? null : h(Text, { dimColor: true }, tr('none')))),
    fk ? h(Box, null, h(Button, { key: 'f-all', variant: 'secondary', onPress: setF(undefined) }, tr('view_all', { n: allChat.length }))) : null)
  const bubbleW = Math.max(28, Math.floor((LW - 4) * 0.6))
  let lastDay = ''
  const msgs = shown.map(r => {
    const day = new Date(r.at * 1000).toLocaleDateString(LOC, { month: '2-digit', day: '2-digit' }), sep = day !== lastDay ? day : ''
    lastDay = day
    const other = r.out ? r.to : r.from, c = seatColor(r.out ? me : r.from), stt = r.state ? ST[r.state] : undefined
    const badge = stt ? h(Text, { color: stt[1], bold: r.state === 'unread' }, `[${r.out ? tr('badge_them', { text: stt[0] }) : stt[0]}]`) : null
    const head = r.out ? h(Text, null, badge, h(Text, { dimColor: true }, `  ${hhmm(r.at)}  ${tr('to_who', { who: other ? `@${other}` : '?' })}  `), logoEl(me), h(Text, { color: c, bold: true }, tr('me_suffix', { name: who(me) })))
      : h(Text, null, logoEl(r.from), h(Text, { color: c, bold: true }, who(r.from)), h(Text, { dimColor: true }, `  ${hhmm(r.at)}  `), badge)
    return h(Box, { flexDirection: 'column', width: LW - 4 },
      sep ? h(Box, { flexDirection: 'row', justifyContent: 'center', marginTop: 1, marginBottom: 1 }, h(Text, { dimColor: true }, `──── ${sep} ────`)) : null,
      h(Box, { flexDirection: 'column', alignItems: r.out ? 'flex-end' : 'flex-start', marginBottom: 1 },
        head, h(Box, { borderStyle: 'round', borderColor: c, paddingX: 1, width: bubbleW }, h(Text, { wrap: 'wrap' }, r.text))))
  })
  const left = h(Box, { flexDirection: 'column', borderStyle: 'round', borderColor: 'claude', paddingX: 1, width: LW },
    h(Box, { flexDirection: 'row', justifyContent: 'space-between' }, h(Text, null, h(Text, { bold: true }, tr('tab_dm')), h(Text, { dimColor: true }, tr('chat_sub', { n: chat.length })), logoEl(me), h(Text, { color: seatColor(me) }, who(me))),
      chat.length > 24 ? h(Button, { key: 'ex-chat', variant: 'secondary', onPress: flip('chat') }, ex.chat ? tr('only_recent') : tr('earlier', { n: chat.length - 24 })) : null),
    summary, ...(msgs.length ? msgs : [h(Text, { dimColor: true }, fk ? tr('no_msg_state') : tr('no_chat'))]))
  const right = h(Box, { flexDirection: 'column', borderStyle: 'round', borderColor: 'subtle', paddingX: 1, width: RW },
    h(Text, { bold: true }, tr('inbox_title')),
    h(Text, null, h(Text, { color: 'error' }, tr('hex_unread', { n: dd.counts.unread })), h(Text, { dimColor: true }, ' · '), h(Text, { color: 'warning' }, tr('open_short', { n: dd.counts.open }))),
    h(Text, { dimColor: true }, tr('done7', { n: dd.counts.done_7d })),
    ...dd.items.slice(0, 20).map(m => h(Box, { flexDirection: 'column', marginTop: 1 },
      h(Text, { wrap: 'truncate-end' }, h(Text, { color: col(m.status), dimColor: m.status === 'waiting' && !saver && frame % 4 >= 2 }, '●'), ' ', ...Lg(m.sender_seat ? `@${m.sender_seat}` : (m.sender ?? '?'), { color: seatColor(m.sender_seat), bold: true })),
      h(Text, { dimColor: true, wrap: 'truncate-end' }, `${m.when ?? ''} · ${lbl(m.status)}`),
      h(Text, { wrap: 'truncate-end', dimColor: m.status === 'done' }, m.text.replace(/\s+/g, ' ')))))
  return h(Box, { flexDirection: 'row', gap: 1, paddingX: 2, marginTop: 1 }, left, right)
}

// ---- 新信卡片(同 Claude 面板 seatcore.tsx 的卡片規則 mailcard.ts;只彈真人來信,Andy 10-25 像素大頭像拿掉、圖示放標題列):監看這一席的信件資料夾,信一到就出;讀了 / 結了自己變色;按 ✕ 才關
//   放在這一格最上面(那一席 AI 的輸入框是 Codex / grok 自己的畫面,動不到 —— 設計稿 §23.4)
let mail = [], mailSeen = null
const dismissed = new Set()
const rowmapOf = d => `${stateRoot()}/instances/${d.cabin}/cockpit/ai-rowmap.json`   // 路徑另起一行(test-cabin-resolution R8:同一行不要又有艙路徑又比對主列)
const marksOf = d => { try { const out = {}; for (const r of JSON.parse(readFileSync(rowmapOf(d), 'utf8'))) { const m = /^@([0-9a-f]{5})$/.exec(r?.detail?.label ?? ''); if (m && r.detail.mark && r.detail.kind === 'main' && !out[m[1]]) out[m[1]] = r.detail.mark } return out } catch { return {} } }
function readMail(path) {
  let q; try { q = JSON.parse(readFileSync(path, 'utf8')) } catch { return }
  const me = S.data?.code ?? SEAT
  if (q.seat && me && q.seat !== me) return
  if (mailSeen?.has(q.id) && !mail.some(c => c.id === q.id)) return
  const rp = relayPath(String(q.msg ?? ''), stateRoot())
  let full = null; if (rp) { try { full = readFileSync(rp, 'utf8').slice(0, 4000) } catch { /* 讀不到就用摘要 */ } }
  const c = cardOf(q, me, full); if (!c) return
  mail = upsert(mail, c, dismissed)
  draw()
}
// 信件資料夾一變動:2 秒內沒新變動就重讀對話頁一次(同 register.tsx 的去抖動;審查 #8)
let dmTimer = null
let dmAgain = false
function dmDirty() { if (dmTimer) clearTimeout(dmTimer); dmTimer = setTimeout(() => { dmTimer = null; if (!(D.data || tab === 'dm')) return; if (D.busy) dmAgain = true; else loadHeavy(D, '--dm', parseDm, true) }, 2000) }
function watchMail(retry = false) {
  const dir = `${stateRoot()}/state/dm-queue/${SEAT}`
  if (!SEAT) return
  if (!existsSync(dir)) { setTimeout(() => watchMail(true), 60000); return }   // 新席還沒有信件資料夾:每分鐘再試(審查 #8)
  // 開這一格時已經在的未讀不做卡(只做之後來的),跟 Claude 面板一樣
  try { mailSeen = new Set(JSON.parse(readFileSync(`${dir}/order.json`, 'utf8'))?.ids ?? []) } catch { mailSeen = new Set() }
  // 開窗後資料夾才出現(新席的第一封信)⇒ 裡面的都是開窗後才來的,不當舊信(審查第二輪 #4)
  if (retry) { mailSeen = new Set(); for (const f of readdirSafe(dir)) if (/^q_.*\.json$/.test(f)) setTimeout(() => readMail(`${dir}/${f}`), 0) }
  else try { for (const f of readdirSafe(dir)) if (/^q_.*\.json$/.test(f)) { try { const q = JSON.parse(readFileSync(`${dir}/${f}`, 'utf8')); if (!q.read_at) mailSeen.add(q.id) } catch { /* 壞檔略過 */ } } } catch { /* 量不到就全部當新的 */ }
  const onChange = (ev, f) => { if (f && /^q_.*\.json$/.test(String(f))) { setTimeout(() => readMail(`${dir}/${f}`), 150); dmDirty() } }
  // 監看失敗(例如開檔數用光):每分鐘只重掛監看、補讀一次 —— 沿用原本的「開窗前就在」名單,空窗期新到的信照樣出卡、舊未讀照樣不出(審查第三、四輪)
  const attach = () => { try { watch(dir, onChange); for (const f of readdirSafe(dir)) if (/^q_.*\.json$/.test(f)) readMail(`${dir}/${f}`) } catch { setTimeout(attach, 60000) } }
  try { watch(dir, onChange) } catch { setTimeout(attach, 60000) }
}
function mailBand(cols) {
  if (!mail.length) return null
  const d = S.data, marks = d ? marksOf(d) : {}, W = cols - 4, me = d?.code ?? SEAT
  const lg = c => (c && d?.logos?.[c] ? h(Text, { color: d.logo_colors?.[c] }, `${d.logos[c]} `) : null)
  const close = id => () => { dismissed.add(id); mail = mail.filter(x => x.id !== id); draw() }
  const flipM = id => () => { mail = mail.map(x => (x.id === id ? { ...x, open: !x.open } : x)); draw() }
  return h(Box, { flexDirection: 'column', paddingX: 2, marginTop: 1 },
    h(Box, { flexDirection: 'row', justifyContent: 'space-between' }, h(Text, { bold: true }, tr('mail_band', { n: mail.length }), h(Text, { dimColor: true }, tr('mail_close_hint'))),
      mail.length > 1 ? h(Button, { key: 'mail-x-all', variant: 'secondary', onPress: () => { mail.forEach(c => dismissed.add(c.id)); mail = []; draw() } }, tr('mail_close_all')) : null),
    ...mail.map(c => {
      const st = { color: STATUS[c.status].color, label: tr(MAIL_ST[c.status] ?? 'st_unread') }, emoji = iconOf(c, marks)
      const body = c.open ? { text: c.text, cut: false } : clip(c.text.replace(/\s+\n/g, '\n'), Math.max(10, W - 6), 3)
      const name = d?.logos && c.from ? c.from : null
      return h(Box, { flexDirection: 'column', borderStyle: 'round', borderColor: st.color, paddingX: 1, width: W },
        h(Box, { flexDirection: 'row', justifyContent: 'space-between' },
          h(Text, { wrap: 'truncate-end' }, h(Text, null, `${emoji} `), h(Text, { color: st.color, bold: true }, `● ${st.label}`), '  ', lg(name), h(Text, { bold: true }, c.label ?? c.from ?? '?'), h(Text, { dimColor: true }, ' → '), lg(me), h(Text, { dimColor: true }, `@${me} · ${hhmm(c.at)}`)),
          h(Box, { flexDirection: 'row', gap: 1 },
            body.cut || c.open ? h(Button, { key: `mail-open-${c.id}`, variant: 'secondary', onPress: flipM(c.id) }, c.open ? tr('collapse') : tr('expand')) : null,
            h(Button, { key: `mail-x-${c.id}`, variant: 'secondary', onPress: close(c.id) }, '✕'))),
        h(Text, { wrap: 'wrap' }, body.text))
    }))
}
// 卡片狀態字照語系(STATUS 只拿顏色,字走 st_* 鍵;Claude 面板的新信卡片 seatcore.tsx 同一套)
const MAIL_ST = { unread: 'st_unread', read: 'st_read_open', done: 'st_done', held: 'st_held', archived: 'st_archived' }
function readdirSafe(dir) { try { return readdirSync(dir) } catch { return [] } }

// CTX:非 Claude 席沒有 Claude 的用量 API ⇒ 讀右欄 AI 清單那一格(ai-rowmap.json 的 context,例 "39%↻")
function readCtx(d) {
  try {
    const rows = JSON.parse(readFileSync(rowmapOf(d), 'utf8'))
    const row = rows.find(r => r?.detail?.label === `@${d.code}` && r.detail.kind === 'main')
    const raw = String(row?.detail?.context ?? '').trim(), m = /(\d+(?:\.\d+)?)%/.exec(raw)
    return m ? Number(m[1]) : raw ? { raw } : null
  } catch { return null }
}
// 右欄那格沒有 % 但有字(例 agy 的「待更新」)⇒ 照抄它,不說「量不到」(右欄自己也還沒量到,兩處講法一致)
const ctxOf = c => (c !== null && typeof c === 'object' ? { ...ctxCell(null), text: c.raw.slice(0, 8) } : ctxCell(c))

// ---- 狀態
let tab = 'today', frame = 0, saver = false, scroll = 0, hits = []
const ex = {}, exAll = ex   // exAll:今天頁手動開關過的(其他頁照舊直接用 ex)
const flip = k => () => { ex[k] = !ex[k]; draw() }

// 席碼前補標誌、標誌上原廠色(這一格不經 Claude Code,tmux 支援真彩色 ⇒ 直接用 #hex)
function Lg(s, o = {}) { return Lgw(withLogo(s, S.data?.logos), o) }
function Lgw(s, o = {}) {   // 已經帶標誌的字串(先 withLogo 再 fit 的)只上色、不再補一次標誌
  const d = S.data
  return logoSegs(s, d?.logos, d?.logo_colors).map(x => (x.color ? h(Text, { color: x.color }, x.text) : h(Text, { color: o.color, dimColor: o.dim, bold: o.bold }, x.text)))
}

// ---- 今天頁(照 aib-boards register.tsx 逐段搬:上方狀態列、左欄此刻、中間流程、下方紀錄、收成一行的人格 / 專案 / 應知、底部一行)
function todayPage(cols) {
  // §22.5 第 3 步:沒手動開關過的區塊照使用次數決定開合;每次點都記一筆(這一席自己的設定檔)
  const sid = S.data?.seat_id ?? S.data?.code ?? 'unknown', use = usageOf(sid), ex = effectiveEx(exAll, use)
  const flip = k => () => { exAll[k] = !ex[k]; writePrefs(sid, { usage: bumpUsage(usageOf(sid), k) }); draw() }
  const resetLayout = () => { for (const k of Object.keys(exAll)) delete exAll[k]; writePrefs(sid, { usage: {} }); draw() }
  const d = S.data
  // 窄(放不下 此刻 30 格 + 流程 40 格)⇒ 此刻疊在流程上面,流程用整寬
  const Wd = Math.min(120, cols - 4), stack = Wd < 30 + 2 + 40, SW = stack ? Wd : 30, FW = stack ? Wd : Math.max(40, Wd - SW - 2), LBL = labelW(), RL = rowLabelW()
  const pk = myPkgs(d.know), nowS = now(), ctxP = readCtx(d)
  const cells = [ctxOf(ctxP), leaseCell(d.know, d.code, nowS), mailCell(d.know, d.todo), pkgCell(pk)]
  const live = !saver, spin = live ? SPIN[frame % SPIN.length] : '·', pulse = c => live && c === 'error' && frame % 4 >= 2
  const kd = s => d.know.find(k => k.sensor === s)
  const lease = kd('machine_lease')?.data ?? {}, push = kd('push_chain')?.data ?? {}
  const mainK = kd('main_head'), main = mainK?.data ?? {}
  const groups = groupTodos(d.todo), cnt = id => groups.find(g => g.id === id)?.items.length ?? 0
  const sd = d.now?.steps_done ?? null, st = d.now?.steps_total ?? null
  const th = themeOf(d.persona?.theme)   // §22.5 第 2 步:人格主題(同 Claude 面板)
  const frameBox = (title, color, w, ...kids) => h(Box, { flexDirection: 'column', borderStyle: th.border, borderColor: color, paddingX: 1, width: w },
    h(Text, { wrap: 'truncate-end' }, h(Text, { bold: true, color }, title)), ...kids)
  const LH = 4, TAIL = 3, pos = Math.floor((frame * th.mul) / th.div) % (LH + TAIL)
  const dot = r => { if (!live) return ['│', 'subtle', true]; const k = pos - r
    return k === 0 ? [th.particle, th.accent, false] : k === 1 ? [th.particle, th.accent, true] : k === 2 ? ['•', th.accent, true] : k === 3 ? ['│', th.accent, true] : ['│', 'subtle', true] }
  const T3 = Math.floor((FW - 2) / 3), MID = T3 + 1 + Math.floor(T3 / 2)
  const link = label => h(Box, { flexDirection: 'column', paddingLeft: MID, width: FW },
    ...Array.from({ length: LH }, (_, r) => { const [ch, c, dim] = dot(r)
      return h(Text, null, h(Text, { color: c, dimColor: dim, bold: !dim }, ch), r === 1 && label ? h(Text, { dimColor: true }, `  ${label}`) : null) }))
  const fork = third => {
    const c0 = Math.floor(third / 2), c1 = MID, c2 = 2 * (third + 1) + Math.floor(third / 2)
    const line = Array.from({ length: c2 + 1 }, (_, x) => (x === c0 ? '┌' : x === c1 ? '┴' : x === c2 ? '┐' : x > c0 && x < c2 ? '─' : ' ')).join('')
    const pad = n => ' '.repeat(Math.max(0, n))
    return h(Box, { flexDirection: 'column', width: FW }, h(Text, { color: 'subtle' }, line),
      ...Array.from({ length: LH - 1 }, (_, r) => { const [ch, c, dim] = dot(r + 1), g = () => h(Text, { color: c, dimColor: dim, bold: !dim }, ch)
        return h(Text, null, pad(c0), g(), pad(c1 - c0 - 1), g(), pad(c2 - c1 - 1), g()) }))
  }
  const kv = (k, v, c) => h(Box, { flexDirection: 'row' }, h(Box, { width: LBL }, h(Text, { dimColor: true }, k)), h(Box, { flexGrow: 1 }, h(Text, { color: c, wrap: 'truncate-end' }, v)))

  // 表頭左邊放不下就先拿掉艙位碼、再拿掉角色(右邊四格優先;原本整段被截成「艙 93…」)
  const who = `${d.persona?.emoji ?? '🖥'} ${d.persona?.name ?? tr('no_persona')}`
  const cellsW = cells.reduce((n, c, k) => n + (k ? 3 : 0) + dispW('■') + dispW(` ${c.label} `) + dispW(String(c.text)), 0)
  const idFull = [`  @${d.code ?? '?'}`, d.role ?? '?', tr('cabin', { value: (d.cabin ?? '?').split('-').pop() })]
  // 窄到連「@席碼」都放不下 ⇒ 四格換到第二行(原本 70 欄時左邊整段被吃掉、右邊也被截)
  const two = Wd - 4 - cellsW - dispW(who) - 2 < dispW(idFull[0])
  const room = Wd - 4 - (two ? 0 : cellsW + 1) - dispW(who) - 2   // 2 = 標誌一格 + 留白
  let idParts = idFull.slice()
  while (idParts.length > 1 && dispW(idParts.join(' · ')) > room) idParts.pop()
  const idTail = idParts.join(' · ')
  const top = h(Box, { flexDirection: two ? 'column' : 'row', borderStyle: th.border, borderColor: 'subtle', paddingX: 1, width: Wd, justifyContent: 'space-between' },
    h(Text, { wrap: 'truncate-end' }, h(Text, { bold: true }, who), ...Lg(idTail, { dim: true })),
    h(Text, { wrap: 'truncate-end' }, ...cells.map((c, k) => h(Text, null, k ? '   ' : '', h(Text, { color: c.color, dimColor: pulse(c.color) }, '■'), h(Text, { dimColor: true }, ` ${c.label} `), h(Text, { color: c.color }, c.text)))))

  const stepBar = (() => {
    const n = Math.max(1, st ?? 1), tail = Math.max(8, dispW(tr('steps', { v1: sd ?? 0, v2: st ?? 0 }))), segW = Math.max(1, Math.floor((SW - 4 - tail - (n - 1)) / n)), done = Math.min(n, sd ?? 0)   // 尾巴「 2/5 步」照語系量寬(日文「ステップ」較長)
    const span = n * (segW + 1), wave = live ? Math.floor(frame / 2) % (span + 6) : -99
    return h(Text, null, ...Array.from({ length: n }, (_, k) => h(Text, null, k ? ' ' : '', ...Array.from({ length: segW }, (_, x) => {
      const dist = wave - (k * (segW + 1) + x), fill = k < done
      return dist === 0 ? h(Text, { color: th.accent, bold: true }, '█') : dist === 1 || dist === 2 ? h(Text, { color: th.accent, dimColor: true }, dist === 1 ? '▓' : '▒') : h(Text, { color: fill ? th.accent : 'subtle', dimColor: !fill }, fill ? '█' : '░') }))),
    h(Text, { dimColor: true }, tr('steps', { v1: sd ?? 0, v2: st ?? 0 })))
  })()
  const side = h(Box, { flexDirection: 'column', borderStyle: th.border, borderColor: th.accent, paddingX: 1, width: SW },
    h(Text, null, h(Text, { bold: true, color: 'claude' }, tr('now_title')), h(Text, { dimColor: true }, tr('now_sub'))),
    h(Text, { color: d.now?.state === 'decide' ? 'warning' : 'claude' }, `${spin} 【${nowState(d.now?.state)}】`, d.now?.at ? h(Text, { dimColor: true }, ` ${ago(nowS - d.now.at)}`) : null),
    h(Box, { borderStyle: 'single', borderColor: 'subtle', paddingX: 1 }, h(Text, { wrap: 'wrap' }, d.now?.text ?? tr('now_none'))),
    st ? stepBar : h(Text, { dimColor: true }, tr('no_steps')),
    h(Box, { marginTop: 1 }, h(Text, { dimColor: true }, tr('checks'))),
    ...cells.map(c => h(Box, { flexDirection: 'column' },
      h(Text, null, h(Text, { color: c.color, dimColor: pulse(c.color) }, '◆'), ' ', h(Text, null, c.label), ' ', h(Text, { bold: true, color: c.color }, c.text)),
      c.sub ? h(Box, { paddingLeft: 2 }, h(Text, { wrap: 'truncate-end' }, ...Lg(c.sub, { dim: true }))) : null)),
    h(Box, { marginTop: 1, flexDirection: 'column' },
      kv(tr('kv_todo'), tr('todo_n', { n: d.todo.length })), kv(tr('grp_now'), String(cnt('now')), cnt('now') ? 'error' : undefined), kv(tr('kv_human'), String(cnt('human')), cnt('human') ? 'warning' : undefined),
      kv(tr('kv_know'), tr('know_n', { n: d.know.length, v1: d.know.filter(k => k.level === 'warn').length, v2: d.know.filter(k => k.level === 'red').length })),
      kv(tr('kv_measured'), `${ago(nowS - d.measured_at)}${took(d.took_ms)}`)))

  const shown = ex.pkgs ? pk.open : pk.open.slice(0, 4)
  const SEG = Math.max(3, Math.min(5, Math.floor((FW - 4 - 24 - 2 - 2 - 12 - 2 - 8) / 3)))
  const pkgRow = t => {
    const st0 = typeof t.stage === 'number' ? t.stage : -1, red = !!t.reds?.length
    let hsh = 0; for (const ch of t.branch) hsh = (hsh * 31 + ch.charCodeAt(0)) >>> 0
    const span = 3 * SEG + 2, slow = 2 + (hsh % 2), cyc = span + 6 + (hsh % 5), wave = live ? Math.floor((frame + (hsh % 97)) / slow) % cyc : -99
    const cell = (k, x) => { const g = k * (SEG + 1) + x, dist = wave - g, filled = st0 > k, base = filled ? (red ? 'error' : 'success') : 'subtle'
      if (dist === 0) return h(Text, { color: red ? 'error' : filled ? 'success' : 'warning', bold: true }, '█')
      if (dist === 1 || dist === 2) return h(Text, { color: red ? 'error' : filled ? 'success' : 'warning', dimColor: true }, dist === 1 ? '▓' : '▒')
      return h(Text, { color: base, dimColor: !filled }, filled ? '█' : '░') }
    const segs = [0, 1, 2].map(k => h(Text, null, ...Array.from({ length: SEG }, (_, x) => cell(k, x))))
    return h(Box, { flexDirection: 'column' },
      h(Box, { flexDirection: 'row' },
        h(Box, { width: 24, marginRight: 2 }, h(Text, { wrap: 'truncate-end', bold: red }, shortBranch(t.branch))),
        h(Text, null, segs[0], ' ', segs[1], ' ', segs[2]),
        h(Box, { marginLeft: 2 }, h(Text, null, ...pkgStages(t).map(([n, v], k) => { const cur = st0 === k, lit = cur && wave >= span && wave < span + 4
          return h(Text, null, k ? ' ' : '', h(Text, { color: v === null ? 'inactive' : v ? 'success' : lit ? 'warning' : 'subtle', bold: lit, dimColor: cur && !lit }, `${n[0]}${v === null ? '?' : v ? '✓' : cur ? '●' : '·'}`)) }))),
        h(Box, { marginLeft: 2 }, h(Text, { dimColor: true }, `${tr('big_commits', { n: t.commits ?? '?' })}${t.dirty ? ' ✎' : ''}`))),
      red ? h(Box, { paddingLeft: 2 }, h(Text, { color: 'error', wrap: 'truncate-end' }, `${tr('reds_line', { what: t.reds.join(tr('sep')) })}${t.refused ? tr('refused', { what: t.refused }) : ''}`)) : null)
  }
  const running = push.running?.[0]
  const leaseUsed = lease.acquired_epoch && lease.expiry_epoch ? (nowS - lease.acquired_epoch) / Math.max(1, lease.expiry_epoch - lease.acquired_epoch) : 0
  const [lf, le] = meter(leaseUsed, 10)
  const third = Math.floor((FW - 2) / 3)
  const unreadTodo = d.todo.filter(t => t.source === 'dm').sort((a, b) => (b.since ?? 0) - (a.since ?? 0))
  const topTodo = groups[0]?.items[0]
  // §22.5 第 1 步 依角色換流程(同 Claude 面板 register.tsx)
  const rf = roleFlow(d.flow)
  const flowRole = rf ? h(Box, { flexDirection: 'column', width: FW },
    ...rf.nodes.flatMap((n, k) => [link(k ? undefined : tr('flow_from')),
      frameBox(tr(`flow_${n.key}`, { n: n.count }), flowColor(n.level), FW,
        ...(n.items.length ? n.items.map(i => h(Text, { wrap: 'truncate-end' }, ...Lg(i.who.replace(/[（(].*$/, ''), { color: 'claude' }), ` ${i.text.replace(/\s+/g, ' ')}`))
          : [h(Text, { dimColor: true }, tr('flow_empty'))]),
        n.guess ? h(Text, { dimColor: true, wrap: 'truncate-end' }, tr('flow_guess')) : null)])) : null
  const flowDev = h(Box, { flexDirection: 'column', width: FW },
    link(tr('link_out')),
    frameBox(tr('pkgs_title', { what: pk.known ? `${tr('pkgs_open', { n: pk.open.length })}${pk.landed.length ? tr('pkgs_landed', { n: pk.landed.length }) : ''}` : tr('na') }), pk.open.some(t => t.reds?.length) ? 'error' : 'success', FW,
      ...(pk.known ? (shown.length ? shown.map(pkgRow) : [h(Text, { color: 'success' }, tr('all_landed'))]) : [h(Text, { dimColor: true }, tr('no_trees_role'))]),
      pk.open.length > 4 ? h(Box, null, h(Button, { key: 'ex-pkgs', variant: 'secondary', onPress: flip('pkgs') }, ex.pkgs ? tr('collapse') : tr('more_pkgs', { n: pk.open.length - 4 }))) : null,
      h(Text, { dimColor: true, wrap: 'truncate-end' }, tr('stage_legend'))),
    link(tr('link_batch')),
    frameBox(tr('ship_title', { what: running ? tr('ship_running') : tr('ship_idle') }), running ? 'warning' : 'subtle', FW,
      running ? h(Text, { wrap: 'truncate-end' }, h(Text, { color: 'warning' }, `${spin} `), running.branch ?? '?', h(Text, { dimColor: true }, tr('ship_ran', { n: Math.round(running.elapsed_s / 60), pid: running.pid }))) : h(Text, { dimColor: true }, tr('ship_nobody')),
      push.queued ? h(Text, { dimColor: true }, tr('machine_queue', { n: push.queued })) : null),
    third >= 26 ? fork(third) : link(tr('link_split')),
    third >= 26
      ? h(Box, { flexDirection: 'row', gap: 1, width: FW },
        frameBox(tr('lease_title'), lease.state === 'free' ? 'success' : 'warning', third,
          h(Text, { wrap: 'truncate-end' }, ...(lease.state === 'free' ? [tr('lease_free')] : Lg(String(lease.owner ?? tr('na'))))),
          lease.state === 'held' ? h(Text, null, h(Text, { color: 'warning' }, lf), h(Text, { color: 'subtle' }, le)) : h(Text, { dimColor: true }, ' '),
          h(Text, { dimColor: true, wrap: 'truncate-end' }, lease.state === 'held' ? tr('lease_span', { v1: hhmm(lease.acquired_epoch), v2: hhmm(lease.expiry_epoch) }) : lease.queue ? tr('queue_n', { n: lease.queue }) : tr('can_test'))),
        frameBox(tr('sensor_main_head'), 'success', third,
          h(Text, { wrap: 'truncate-end' }, h(Text, { color: 'success' }, main.sha ?? '?'), h(Text, { dimColor: true }, ` ${main.committed_at ? ago(nowS - main.committed_at) : ''}`)),
          h(Text, { wrap: 'truncate-end', dimColor: true }, mainK?.line ?? tr('na')), h(Text, { dimColor: true }, ' ')),
        frameBox(tr('sensor_inbox'), cells[2].color, third,
          h(Text, { wrap: 'truncate-end', color: cells[2].color }, cells[2].text),
          h(Text, { wrap: 'truncate-end' }, ...(unreadTodo[0] ? [...Lg((unreadTodo[0].from ?? '').replace(/[（(].*$/, ''), { color: 'claude' }), ` ${unreadTodo[0].text}`] : [h(Text, { dimColor: true }, tr('no_mail_todo'))])),
          h(Text, { dimColor: true }, unreadTodo.length ? tr('mail_todo_n', { n: unreadTodo.length }) : ' ')))
      : frameBox(tr('trio_title'), lease.state === 'held' ? 'warning' : 'success', FW,
        kv(tr('sensor_machine_lease'), lease.state === 'free' ? h(Text, { color: 'success' }, tr('lease_free')) : h(Text, null, ...Lg(`${String(lease.owner ?? tr('na'))} `, { color: 'warning' }), h(Text, { color: 'warning' }, lf), h(Text, { color: 'subtle' }, le), h(Text, { dimColor: true }, tr('latest', { when: hhmm(lease.expiry_epoch) })))),
        kv(tr('sensor_main_head'), h(Text, null, h(Text, { color: 'success' }, main.sha ?? '?'), h(Text, { dimColor: true }, ` ${main.committed_at ? ago(nowS - main.committed_at) : ''} ${mainK?.line ?? ''}`))),
        kv(tr('sensor_inbox'), h(Text, null, h(Text, { color: cells[2].color }, cells[2].text), ...(unreadTodo[0] ? Lg(` · ${(unreadTodo[0].from ?? '').replace(/[（(].*$/, '')} ${unreadTodo[0].text}`, { dim: true }) : [])))),
    link(tr('link_back')),
    frameBox(tr('next_title'), topTodo ? (groups[0].id === 'now' ? 'error' : 'claude') : 'subtle', FW,
      topTodo ? h(Text, { wrap: 'truncate-end' }, `${groups[0].icon} `, ...(topTodo.from ? Lg(`${topTodo.from.replace(/[（(].*$/, '')} `, { color: 'claude' }) : []), topTodo.text.replace(/\s+/g, ' ')) : h(Text, { dimColor: true }, tr('no_todo'))))
  const flow = flowRole ?? flowDev

  const rows = logRows(d.todo, d.know, d.events ?? [], ex.log ? 40 : 12, d.now?.at)
  const TG = logTagW(), TW = Math.max(10, Wd - 4 - 6 - 16 - TG - 3 - 1)
  const clock = new Date(), hms = [clock.getHours(), clock.getMinutes(), clock.getSeconds()].map(n => String(n).padStart(2, '0')).join(':')
  const log = h(Box, { flexDirection: 'column', borderStyle: th.border, borderColor: live ? th.accent : 'subtle', paddingX: 1, width: Wd },
    h(Box, { flexDirection: 'row', justifyContent: 'space-between' },
      h(Text, null, h(Text, { color: live && frame % 2 ? 'error' : 'inactive' }, '●'), h(Text, { bold: true, color: live ? 'error' : 'inactive' }, ' LIVE '), h(Text, { bold: true }, tr('log_title')),
        h(Text, { dimColor: true }, tr('log_sub', { value: `${rows.length}${d.events?.length ? `/${d.events.length}` : ''}` })), h(Text, { color: 'success' }, hms)),
      h(Button, { key: 'ex-log', variant: 'secondary', onPress: flip('log') }, ex.log ? tr('less') : tr('more'))),
    ...(rows.length ? rows.map(r => h(Text, null, h(Text, { dimColor: true }, fit(hhmm(r.at), 6)), ' ', ...Lgw(`${fit(withLogo(r.who, d.logos), 16)} `, { color: 'claude' }), h(Text, { color: r.color }, `${fit(r.tag, TG)} `), fit(r.text, TW)))
      : [h(Text, { dimColor: true }, tr('no_log'))]))

  const fold = (k, title, oneLine, ...more) => h(Box, { flexDirection: 'column', width: Wd },
    h(Box, { flexDirection: 'row', gap: 1 }, h(Button, { key: `ex-${k}`, variant: 'secondary', onPress: flip(k) }, `${ex[k] ? '▾' : '▸'} ${title}`), h(Box, { flexGrow: 1 }, h(Text, { wrap: 'truncate-end' }, oneLine))),
    ex[k] ? h(Box, { flexDirection: 'column', paddingLeft: 4, marginBottom: 1 }, ...more) : null)
  const row = (dotEl, label, value, tail) => h(Box, { flexDirection: 'row' }, h(Box, { width: 2 }, dotEl), h(Box, { width: RL }, h(Text, { dimColor: true, wrap: 'truncate-end' }, label)),
    h(Box, { flexGrow: 1 }, h(Text, { wrap: 'truncate-end' }, value)), tail ? h(Box, { marginLeft: 1 }, h(Text, { dimColor: true }, tail)) : null)
  const kc = lv => d.know.filter(k => k.level === lv).length
  const footer = h(Text, { wrap: 'truncate-end' }, h(Text, { dimColor: true }, `${tr('ft_seat')} [`), ...Lg(`@${d.code} · ${d.role}`, { color: 'claude', dim: true }),
    h(Text, { dimColor: true }, `]  ${tr('ft_state')} [`, h(Text, { color: 'claude' }, `${nowState(d.now?.state)}${st ? ` ${sd ?? 0}/${st}` : ''}`), `]  ${tr('ft_machine')} [`, h(Text, { color: cells[1].color }, cells[1].text), `]  ${tr('ft_main')} [`, h(Text, { color: 'success' }, main.sha ?? '?'), `]  ${tr('ft_data')} [${ago(nowS - d.measured_at)}]`))
  return h(Box, { flexDirection: 'column', paddingX: 2, marginTop: 1, width: Wd + 4 },
    top,
    h(Box, { flexDirection: stack ? 'column' : 'row', gap: stack ? 1 : 2, marginTop: 1 }, side, flow),
    h(Box, { marginTop: 1 }, log),
    h(Box, { flexDirection: 'column', marginTop: 1 },
      // 常用的往上(§22.5 第 3 步):照這一席點過的次數排
      ...foldOrder(['persona', 'project', 'know'], use).map(k => (k === 'persona'
        ? fold('persona', tr('fold_persona'), h(Text, null, h(Text, { bold: true }, `${d.persona?.emoji ?? ''} ${d.persona?.name ?? tr('no_persona')}`), h(Text, { dimColor: true }, `${tr('persona_counts', { v1: (d.persona?.duties ?? []).length, v2: (d.persona?.red_lines ?? []).length })}${d.persona?.uid ? `  [[${d.persona.uid}]]` : ''}`)),
          ...(d.persona?.duties ?? []).map(x => row(h(Text, { color: 'success' }, '•'), tr('duty'), x)), ...(d.persona?.red_lines ?? []).map(x => row(h(Text, { color: 'error' }, '•'), tr('red_line'), x)))
        : k === 'project'
        ? fold('project', tr('fold_project'), d.project ? h(Text, null, h(Text, { bold: true }, d.project.title ?? ''), h(Text, { dimColor: true }, `  ${d.project.uid ?? ''}`)) : h(Text, { dimColor: true }, tr('proj_unbound')),
          d.project?.work_state ? h(Text, { wrap: 'wrap', dimColor: true }, d.project.work_state) : h(Text, { dimColor: true }, tr('no_progress')))
        : fold('know', tr('fold_know'), h(Text, null, h(Text, { dimColor: true }, tr('know_count', { n: d.know.length })), h(Text, { color: 'error' }, `● ${kc('red')}`), '  ', h(Text, { color: 'warning' }, `● ${kc('warn')}`), '  ', h(Text, { color: 'success' }, `● ${kc('ok')}`), '  ', h(Text, { color: 'inactive' }, `○ ${kc('unknown')}`)),
          ...d.know.map(k => { const [lb, val] = knowParts(k); return row(h(Text, { color: levelColor(k.level) }, levelDot(k.level)), lb, val, k.measured_at ? ago(nowS - k.measured_at) : '') })))),
      Object.keys(use).length ? h(Box, null, h(Button, { key: 'usage-reset', variant: 'secondary', onPress: resetLayout }, tr('usage_reset')), h(Text, { dimColor: true }, `  ${tr('usage_hint')}`)) : null),
    d.missing?.length ? h(Text, { dimColor: true, wrap: 'truncate-end' }, tr('missing', { what: d.missing.join(tr('sep')) })) : null,
    h(Box, { marginTop: 1 }, footer))
}

// ---- 外框:分頁列 + 內容;還沒做的分頁照實說(B2~B4)
const LATER = {}
function screen(cols) {
  const go = id => () => { tab = id; scroll = 0; loadTab(id); draw() }
  const bar = h(Box, { flexDirection: 'row', flexWrap: 'wrap', gap: 1, marginTop: 1, paddingX: 2 },
    ...TABS.map((t, i) => h(Button, { key: `tab-${t.id}`, variant: tab === t.id ? 'primary' : 'secondary', onPress: go(t.id) }, `${i + 1} ${t.label}`)),
    h(Button, { key: 'refresh', onPress: () => { load(true); loadTab(tab, true) } }, `r ${tr('refresh')}`),
    h(Button, { key: 'saver', variant: saver ? 'primary' : 'secondary', onPress: () => { saver = !saver; draw() } }, `a ${saver ? tr('anim_saver') : tr('anim_on')}`),
    h(Button, { key: 'quit', onPress: quit }, `q ${tr('quit')}`),
    S.busy || C.busy || P.busy || D.busy ? h(Text, { color: 'warning' }, `⏳ ${tr('measuring')}`) : null)
  let body
  if (!S.data) body = h(Box, { paddingX: 2, marginTop: 1 }, h(Text, { color: S.err ? 'error' : 'warning' }, S.err ? tr('na_err', { err: S.err }) : tr('measuring')))
  else if (tab === 'today') body = todayPage(cols)
  else if (tab === 'proj') body = projPage(cols)
  else if (tab === 'dm') body = dmPage(cols)
  else if (tab === 'charts' || tab === 'custom') body = chartsPage(cols, tab === 'custom')
  else body = h(Box, { paddingX: 2, marginTop: 1 }, h(Text, { dimColor: true }, tr('later', { what: LATER[tab] ?? 'B2' })))
  return h(Box, { flexDirection: 'column', width: cols }, bar, mailBand(cols), body)
}

// ---- 終端機:全螢幕、滑鼠、鍵盤、每 0.4 秒一格動畫(省電時停)
const out = process.stdout
function draw() {
  const cols = out.columns || 100, rowsN = out.rows || 40
  const { lines } = render(screen(cols), cols)
  scroll = Math.max(0, Math.min(scroll, Math.max(0, lines.length - rowsN)))
  const p = paint(lines, cols, scroll, rowsN)
  hits = p.hits
  out.write('\x1b[H' + p.text)
}
function quit() { out.write('\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l'); process.exit(0) }
loadLocaleSync()
if (args.includes('--once')) {   // 測試用:畫一張就走(不進全螢幕);--locale <L> 指定語系
  const cols = Number(arg('--cols') ?? 100)
  const fake = arg('--data')
  if (fake) { S.data = parseScreen(readFileSync(fake, 'utf8')); S.at = now() }
  if (arg('--charts')) { C.data = parseCharts(readFileSync(arg('--charts'), 'utf8')); C.at = now() }
  if (arg('--dm')) { D.data = parseDm(readFileSync(arg('--dm'), 'utf8')); D.at = now() }
  if (arg('--mail')) { const q = JSON.parse(readFileSync(arg('--mail'), 'utf8')); const c = cardOf(q, S.data?.code ?? null); if (c) mail = upsert(mail, c, new Set()) }
  if (arg('--plan')) { P.data = parsePlan(readFileSync(arg('--plan'), 'utf8')); P.at = now() }
  if (arg('--tab')) tab = arg('--tab')
  const { lines } = render(screen(cols), cols)
  process.stdout.write(paint(lines, cols, 0, lines.length).text.replace(/\x1b\[[0-9;]*[mK]/g, '') + '\n')
  process.exit(0)
}
out.write('\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h\x1b[2J')
process.stdin.setRawMode?.(true); process.stdin.resume(); process.stdin.setEncoding('utf8')
// 一次讀到的可能是好幾個鍵(連按、貼上):切成一個一個處理 —— 滑鼠(\x1b[<…M/m)、方向鍵 / 翻頁(\x1b[A、\x1b[5~)、單一字元
function key(k) {
  const go = id => { tab = id; scroll = 0; loadTab(id); draw() }
  const m = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/.exec(k)
  if (m) {
    const b = Number(m[1]), x = Number(m[2]) - 1, y = Number(m[3]) - 1
    if (b === 64) { scroll -= 3; draw() } else if (b === 65) { scroll += 3; draw() }
    else if (b === 0 && m[4] === 'M') { const hit = hits.find(t => t.y === y && x >= t.x0 && x < t.x1); if (hit?.fn) hit.fn() }
    return
  }
  if (k === 'q' || k === '\x03') quit()
  else if (k >= '1' && k <= '5' && k.length === 1) go(TABS[Number(k) - 1].id)
  else if (k === 'r') { load(true); loadTab(tab, true) }
  else if (k === 'a') { saver = !saver; draw() }
  else if (k === '\x1b[A' || k === 'k') { scroll -= 1; draw() } else if (k === '\x1b[B' || k === 'j') { scroll += 1; draw() }
  else if (k === '\x1b[5~') { scroll -= (out.rows || 40) - 2; draw() } else if (k === '\x1b[6~') { scroll += (out.rows || 40) - 2; draw() }
}
export const splitKeys = s => s.match(/\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[[0-9;]*[A-Za-z~]|\x1b[^\x1b]|[\s\S]/g) ?? []
// 一段讀到的結尾是不完整的序列(滑鼠 \x1b[<0;12 這種被切成兩段)⇒ 先留著,跟下一段接起來再切(審查 #5)
let pending = ''
export function feed(chunk) {
  // 上一段結尾留下單獨的 Esc、這一段又不是 [ 開頭 ⇒ 那是一個真的 Esc 鍵,先送出(Esc 再按 q 不會黏成一個鍵,審查第三輪)
  if (pending === '\x1b' && chunk[0] !== '[') { pending = ''; return ['\x1b', ...feed(chunk)] }
  let s = pending + chunk; pending = ''
  // 結尾的半截序列(單獨的 Esc、或 \x1b[<0;12 這種)先留著,跟下一段接起來再切(審查第三輪:序列剛好切在 Esc 後面也接得回去)
  const m = /\x1b(\[[<0-9;]*)?$/.exec(s)
  if (m) { pending = m[0]; s = s.slice(0, m.index) }
  return splitKeys(s)
}
process.stdin.on('data', s => { for (const k of feed(s)) key(k) })
out.on('resize', () => { out.write('\x1b[2J'); draw() })
setInterval(() => { if (!saver) { frame++; draw() } }, 400)
setInterval(() => { if (now() - S.at > 60) load() }, 15000)
setInterval(pollLocale, 5000)   // 語系:看設定檔 mtime,變了才重問   // 今天頁輕量:看得見時一分鐘量一次(資料層自己有 15 秒快取)
watchMail()
load()
draw()
