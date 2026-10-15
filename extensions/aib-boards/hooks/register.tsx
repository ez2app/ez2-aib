// Ez2 AIB「每席一台電腦」免費分頁(the maintainer 10-14 00:1x「aib-boards 才是免費的」):今天 / 專案 / 私訊 / 圖表 / 自訂。
// 資料只讀 src/cockpit/seat_screen.py 一份(§21 第 6、7 條);打開才算、各自快取、不輪詢(§21.2);新信靠檔案事件推。
// 只在真人打字時打開(the maintainer 10-14 00:0x);畫面 1:4;動畫 20 秒自停、可省電(對話要能捲)。
import { atom, read, update } from 'claude-code'
import type { Register, EngineInterface, Timer } from 'claude-code'
import type { Screen, Tab, Charts, Dm, Plan } from '../types'
import type { Rows } from './charts'
import { ctxCell, leaseCell, mailCell, myPkgs, pkgCell, pkgStages, bar as meter, hhmm, shortBranch, logRows, fit, typeIn, reveal, withLogo, logoSegs } from './lib'
import type { Pkg } from './lib'
import { sw, labelW, rowLabelW, logTagW } from './lib'
import { knowParts, split41, SPIN, knowLine, TABS, CACHE_S, isHumanTyped, groupTodos, levelColor, levelDot, roleFlow, flowColor, themeOf, bumpUsage, effectiveEx, foldOrder, ago, isStale, nowState, parseScreen, parseCharts, parseDm, parsePlan } from './lib'
import { TIER_DEFAULT, TIER_ALL, CHART_TITLE, tierOf, buildCard, pinFirst } from './chartset'
import { tr as t_ } from './i18n'
import { registerSeat, seatOnStart, seatOnFileChanged, seatWatchDir } from './seatcore'
import { ready } from './ready'

const PANE = 'aib-boards'
const tab = atom({ plugin: 'aib-boards', key: 'tab' } as const, 'today' as Tab)
const screen = atom({ plugin: 'aib-boards', key: 'screen' } as const, { at: 0, data: null as Screen | null, err: null as string | null })
const charts = atom({ plugin: 'aib-boards', key: 'charts' } as const, { at: 0, data: null as Charts | null, err: null as string | null })
const dm = atom({ plugin: 'aib-boards', key: 'dm' } as const, { at: 0, data: null as Dm | null, err: null as string | null })
const plan = atom({ plugin: 'aib-boards', key: 'plan' } as const, { at: 0, data: null as Plan | null, err: null as string | null })
const now = () => Date.now() / 1000
// 正在讀哪幾種資料(審查 10-14:原本四種共用一個 busy,打開時正在算「今天」就按 3 ⇒ 私訊頁永遠「量中」;
// 重載時舊程式的等待被取消、finally 不跑 ⇒ busy 卡 true)。改成每種各記開始時間,60 秒過期,模組變數重載歸零。
const inflight: Record<string, number> = {}
const isBusy = (k: string) => !!inflight[k] && Date.now() - inflight[k]! < 60_000
const anyBusy = () => Object.keys(inflight).some(isBusy)
// 這次對話真人打開過面板(彈窗只在那之後跳);記在 state,重載後還在
const openedAtom = atom({ plugin: 'aib-boards', key: 'opened' } as const, false)
// 今天頁摺疊(人格 / 專案 / 應知 / 包清單)展開了哪幾個;記在 state,重畫不歸零
const expand = atom({ plugin: 'aib-boards', key: 'expand' } as const, {} as Record<string, boolean>)
const dmDirAtom = atom({ plugin: 'aib-boards', key: 'dmDir' } as const, '')

// ── 動畫:面板開著才 400ms 一格;20 秒自停(一直重畫會把對話拉回底部);按鈕 / 重新整理 / 在面板上滾再動 20 秒 ──
const ANIM_MS = 200, ANIM_RUN_MS = 20_000   // 200ms 一格(the maintainer 03:09「光點走快一點」;原 400)
let anim: Timer | undefined, frame = 0, saver = false, animUntil = 0
let logAt = -1, logStart = 0   // 紀錄逐行吐出:資料換新(或重新整理)就從這一格重吐
function animStart($: EngineInterface) {
  if (saver) return
  animUntil = Date.now() + ANIM_RUN_MS
  if (anim) return
  anim = $.clock.every(ANIM_MS, () => { if (Date.now() > animUntil) { animStop(); $.ui.invalidate('ui.render'); return } frame++; $.ui.invalidate('ui.render') })
}
function animStop() { anim?.cancel(); anim = undefined }

// ── 語系與字典:ready.ts(與 seatcore.tsx 共用);純檔一律走 i18n.ts 的 tr()。
// ── 資料層位置:$EZ2_AIB_SEAT_SCREEN → 開發期 dev-seat-screen.txt → $EZ2_AIB_ROOT/src/cockpit → ~/ez2-ai-b/src/cockpit ──
let HOME = ''
async function scriptPath($: EngineInterface): Promise<string | null> {
  const r = await $.process.run(['sh', '-c', 'printf "%s|%s|%s" "$EZ2_AIB_SEAT_SCREEN" "$EZ2_AIB_ROOT" "$HOME"'], { timeoutMs: 5000 }).catch(() => null)
  const [explicit = '', root = '', home = ''] = (r?.stdout ?? '').split('|')
  HOME = home
  let dev = ''
  try { const f = `${home}/.ez2-aib/state/aib-boards/dev-seat-screen.txt`; if (home && (await $.fs.exists(f))) dev = (await $.fs.read(f)).trim() } catch { /* 沒有就算了 */ }
  for (const p of [explicit, dev, root && `${root}/src/cockpit/seat_screen.py`, home && `${home}/ez2-ai-b/src/cockpit/seat_screen.py`]) if (p && (await $.fs.exists(p))) return p
  return null
}
async function runS1($: EngineInterface, flags: string[]): Promise<string> {
  const p = await scriptPath($)
  if (!p) throw new Error(t_('no_s1'))
  const r = await $.process.run(['python3', '-I', p, ...flags], { timeoutMs: 25000 })
  if (r.exitCode !== 0) throw new Error(r.stderr.trim().split('\n').pop() || `rc=${r.exitCode}`)
  return r.stdout
}
async function refresh($: EngineInterface, fresh = false) {
  if (isBusy('today')) return
  inflight.today = Date.now()   // 先占位再查快取(審查:按鈕與重畫同時叫會各起一次重的計算)
  let cur; try { cur = await read($, screen) } catch { delete inflight.today; return }
  if (!fresh && cur.data && now() - cur.at < CACHE_S) { delete inflight.today; return }
  $.ui.invalidate('ui.render')
  try { const data = parseScreen(await runS1($, fresh ? ['--fresh'] : [])); await update($, screen, () => ({ at: now(), data, err: null })) }
  catch (err) { await update($, screen, v => ({ ...v, at: now(), err: String((err as Error).message ?? err) })) }
  finally { delete inflight.today; $.ui.invalidate('ui.render') }
}
async function refreshCharts($: EngineInterface, fresh = false) {
  if (isBusy('charts')) return
  inflight.charts = Date.now()   // 先占位再查快取(審查:按鈕與重畫同時叫會各起一次重的計算)
  let cur; try { cur = await read($, charts) } catch { delete inflight.charts; return }
  if (!fresh && cur.data && now() - cur.at < 60) { delete inflight.charts; return }
  $.ui.invalidate('ui.render')
  try { const data = parseCharts(await runS1($, ['--charts', ...(fresh ? ['--fresh'] : [])])); await update($, charts, () => ({ at: now(), data, err: null })) }
  catch (err) { await update($, charts, v => ({ ...v, at: now(), err: String((err as Error).message ?? err) })) }
  finally { delete inflight.charts; $.ui.invalidate('ui.render') }
}
async function refreshDm($: EngineInterface, fresh = false) {
  if (isBusy('dm')) return
  inflight.dm = Date.now()   // 先占位再查快取(審查:按鈕與重畫同時叫會各起一次重的計算)
  let cur; try { cur = await read($, dm) } catch { delete inflight.dm; return }
  if (!fresh && cur.data && now() - cur.at < 15) { delete inflight.dm; return }
  $.ui.invalidate('ui.render')
  try { const data = parseDm(await runS1($, ['--dm', ...(fresh ? ['--fresh'] : [])])); await update($, dm, () => ({ at: now(), data, err: null })) }
  catch (err) { await update($, dm, v => ({ ...v, at: now(), err: String((err as Error).message ?? err) })) }
  finally { delete inflight.dm; $.ui.invalidate('ui.render') }
}
async function refreshPlan($: EngineInterface, fresh = false) {
  if (isBusy('plan')) return
  inflight.plan = Date.now()   // 先占位再查快取(審查:按鈕與重畫同時叫會各起一次重的計算)
  let cur; try { cur = await read($, plan) } catch { delete inflight.plan; return }
  if (!fresh && cur.data && now() - cur.at < 60) { delete inflight.plan; return }
  $.ui.invalidate('ui.render')
  try { const data = parsePlan(await runS1($, ['--plan', ...(fresh ? ['--fresh'] : [])])); await update($, plan, () => ({ at: now(), data, err: null })) }
  catch (err) { await update($, plan, v => ({ ...v, at: now(), err: String((err as Error).message ?? err) })) }
  finally { delete inflight.plan; $.ui.invalidate('ui.render') }
}
async function refreshTab($: EngineInterface, id: Tab, fresh = false) {
  if (id === 'today') await refresh($, fresh)
  else if (id === 'proj') await refreshPlan($, fresh)
  else if (id === 'dm') await refreshDm($, fresh)
  else await refreshCharts($, fresh)
}

async function readCtx($: EngineInterface): Promise<number | null> {
  const u = await $.session.usage().catch(() => undefined)
  const p = u?.context?.percent
  return typeof p === 'number' ? p : null
}
async function paneRows($: EngineInterface): Promise<number> {
  const r = await $.process.run(['sh', '-c', '[ -n "$TMUX" ] && tmux display -p -t "$TMUX_PANE" "#{pane_height}"'], { timeoutMs: 3000 }).catch(() => null)
  const n = Number((r?.stdout ?? '').trim()); return Number.isFinite(n) && n > 0 ? n : 40
}

// ── 自訂:這一席要放哪些圖,存 ~/.ez2-aib/state/aib-boards/<seat_id>.json ──
let picks: string[] | null = null, picksFor = ''
const picksFile = (seatId: string) => `${HOME}/.ez2-aib/state/aib-boards/${seatId}.json`
async function loadPicks($: EngineInterface, seatId: string, tier: string): Promise<string[]> {
  if (picks && picksFor === seatId) return picks
  picksFor = seatId; picks = null
  try { const f = picksFile(seatId); if (HOME && (await $.fs.exists(f))) { const v = (JSON.parse(await $.fs.read(f)) as { charts?: string[] }).charts; if (Array.isArray(v)) picks = v } } catch { /* 用預設 */ }
  return picks ?? TIER_DEFAULT[tier]!
}
// 同一個檔還放 usage(§22.5 第 3 步):寫的時候先讀回別的欄位再合併,誰寫都不蓋掉對方
async function readPrefs($: EngineInterface, seatId: string): Promise<Record<string, unknown>> {
  try { const f = picksFile(seatId); if (HOME && (await $.fs.exists(f))) return JSON.parse(await $.fs.read(f)) as Record<string, unknown> } catch { /* 壞檔當沒有 */ }
  return {}
}
async function writePrefs($: EngineInterface, seatId: string, patch: Record<string, unknown>) {
  if (HOME) await $.fs.write(picksFile(seatId), JSON.stringify({ ...(await readPrefs($, seatId)), ...patch, at: now() })).catch(() => undefined)
}
async function savePicks($: EngineInterface, seatId: string, list: string[]) { picks = list; picksFor = seatId; await writePrefs($, seatId, { charts: list }) }
// 使用習慣:這一席今天頁每個區塊點了幾次(只存本機這一席的設定檔,不外傳)
let usage: Record<string, number> | null = null, usageFor = ''
async function loadUsage($: EngineInterface, seatId: string): Promise<Record<string, number>> {
  if (usage && usageFor === seatId) return usage
  usageFor = seatId
  const v = (await readPrefs($, seatId)).usage
  usage = v && typeof v === 'object' ? v as Record<string, number> : {}
  return usage
}
async function saveUsage($: EngineInterface, seatId: string, u: Record<string, number>) { usage = u; usageFor = seatId; await writePrefs($, seatId, { usage: u }) }

// ── 新信彈窗:監看這一席的信件資料夾(檔案事件,不輪詢);只在這次對話真人打開過面板後才跳 ──
async function dmWatchDir($: EngineInterface): Promise<string> {
  const cur = await read($, dmDirAtom); if (cur) return cur
  const p = await scriptPath($).catch(() => null); if (!p) return ''
  const cockpit = p.replace(/\/seat_screen\.py$/, '')
  // 席碼用正本 seat_now.self_code()(審查第二輪:只看 AIB_RESUME_SESSION_UUID 的話,一般新開 / 分出的席是空的 ⇒ 永遠不跳;繼承到死掉的舊 uuid ⇒ 監看到別席)
  const py = 'import os,sys;sys.path.insert(0,sys.argv[1]);from seat_now import self_code;from seats_dir_lib import state_root;c=self_code();print(os.path.join(state_root()[0],"state","dm-queue",c) if c else "")'
  const r = await $.process.run(['python3', '-I', '-c', py, cockpit], { timeoutMs: 3000 }).catch(() => null)
  const dir = (r?.exitCode === 0 ? r.stdout.trim() : '')
  if (dir) await update($, dmDirAtom, () => dir)
  return dir
}
let dmDirtyAt = 0, dmWake = false, dmUpgraded = false, s1Upgraded = false   // 去抖動計時器同時只掛一個(畫面每 0.4 秒重畫,不能每次都掛)

export const register: Register = on => {
  registerSeat(on)   // 席位核心(從 aib-seat 搬來,設計稿 §24);外掛只能有一個 hooks 模組 ⇒ 由這裡掛上
  on('session.start', async ($, e, next) => {
    // 指令說明只在開席時登記一次,切語系跟不上 ⇒ 固定英文(同 aib-languages)
    await $.command.register({ name: 'aib-boards', description: "This seat's computer: Today / Project / Chat / Charts / Custom" })
    await seatOnStart($).catch(() => undefined)   // 席位核心:身份、狀態列、交接提醒、新信卡片(seatcore.tsx)
    return next(e)
  })
  on('classic.SessionStart', async ($, e, next) => {
    const r = await next(e), dir = await dmWatchDir($).catch(() => ''), sdir = await seatWatchDir($).catch(() => '')   // 新信卡片也要同一個資料夾(兩支各自量,量到的都登記、去重)
    const add = [...new Set([dir, sdir].filter(Boolean))]
    return add.length ? { ...r, watchPaths: [...(r.watchPaths ?? []), ...add] } : r
  }).catch(($, e, next) => (next.called ? next(e) : next(e)))   // 只顯示的 mod 出錯不擋流程
  on('classic.FileChanged', async ($, e, next) => {
    const r = await next(e)
    await seatOnFileChanged($, e.file_path, e.event).catch(() => undefined)   // 新信卡片(輸入框上方,seatcore.tsx;原本在 aib-seat)
    const dmDir = await read($, dmDirAtom)
    if (dmDir && e.file_path.startsWith(dmDir + '/') && /\/q_[^/]+\.json$/.test(e.file_path) && e.event !== 'unlink') {
      dmDirtyAt = Date.now()   // 信件連續改檔:等 2 秒沒新變動才重讀一次(去抖動);之前失敗過也會重讀
      await update($, dm, v => ({ ...v, err: null }))
      $.ui.invalidate('ui.render')
    }
    return r
  }).catch(($, e, next) => (next.called ? next(e) : next(e)))   // 只顯示的 mod 出錯不擋流程
  on('command.run', { command: 'aib-boards' }, async ($, e) => {
    const { t } = await ready($)
    if (!isHumanTyped(e.origin)) return { text: t('only_human', { what: e.origin?.kind ?? t('origin_unknown') }) }
    // 面板標題只在打開時給一次:切語系後要關掉再開才換(面板內容每次重畫都照新語系)
    const o = await $.ui.open({ id: PANE, title: t('pane_title'), ...split41(e.presentation?.columns ?? 120, await paneRows($)) })
    await update($, openedAtom, () => true)
    if (o.isPlaced) animStart($)
    void refresh($)
    return { text: t('opened') }
  })
  on('ui.close', async ($, e, next) => { if (e.id === PANE) animStop(); return next(e) }).catch(($, e, next) => (next.called ? next(e) : next(e)))   // 只顯示的 mod 出錯不擋流程
  on('ui.scroll', { component: 'Pane', requestId: PANE }, async ($, e, next) => { animStart($); return next(e) }).catch(($, e, next) => (next.called ? next(e) : next(e)))   // 只顯示的 mod 出錯不擋流程

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const { t: T, loc } = await ready($)
    const tierLabel = (x?: string | null) => T(`tier_${x && ['captain', 'lead', 'qa', 'eng', 'design'].includes(x) ? x : 'eng'}`)
    const took = (ms?: number) => (ms !== undefined ? T('took', { n: ms }) : '')
    const [t, S] = await Promise.all([read($, tab), read($, screen)]), bz = anyBusy()
    const rowsEl = (rows: Rows) => rows.map(r => <Text>{r.map(sg => <Text color={sg.color} dimColor={sg.dim}>{sg.text}</Text>)}</Text>)
    const go = (id: Tab) => () => { animStart($); void update($, tab, () => id); void refreshTab($, id) }
    const bar = (
      <Box flexDirection="row" gap={1} flexWrap="wrap">
        {TABS.map(x => <Button key={`tab-${x.id}`} hotkey={x.hotkey} variant={t === x.id ? 'primary' : 'secondary'} onPress={go(x.id)}>{x.label}</Button>)}
        <Button key="refresh" onPress={() => { animStart($); void refresh($, true); if (t !== 'today') void refreshTab($, t, true) }}>{T('refresh')}</Button>
        <Button key="saver" variant={saver ? 'primary' : 'secondary'} onPress={() => { saver = !saver; if (saver) animStop(); else animStart($); $.ui.invalidate('ui.render') }}>{saver ? T('anim_saver') : T('anim_on')}</Button>
        {bz ? <Text color="warning">⏳ {T('measuring')}</Text> : null}
      </Box>)
    const d = S.data
    // 標誌一律原廠色(the maintainer 10-14「仔細把 aib-boards 的所有 logo 補回原廠色」):Pw 切已帶標誌的字串,P 先補標誌再切;
    //   標誌那一格只給原廠色、不跟外層變暗(外層 dimColor 會把整串連標誌一起變暗,所以變暗改由每段自己帶)
    const Lgw = (s: string, o: { color?: string; dim?: boolean; bold?: boolean } = {}) => logoSegs(s, d?.logos, d?.logo_colors).map(x => x.color ? <Text color={x.color}>{x.text}</Text> : <Text color={o.color} dimColor={o.dim} bold={o.bold}>{x.text}</Text>)
    const Lg = (s: string, o: { color?: string; dim?: boolean; bold?: boolean } = {}) => Lgw(withLogo(s, d?.logos), o)
    // 手上是舊格式(有標誌、沒原廠色,資料層升級前抓的)⇒ 自己重抓一次
    if (d?.logos && Object.keys(d.logos).length && !d.logo_colors && !isBusy('today') && !s1Upgraded) { s1Upgraded = true; void refresh($, true) }
    const head = d
      ? <Box flexDirection="column" marginTop={1}>
          <Text><Text color="claude">{saver ? '·' : SPIN[frame % SPIN.length]} </Text><Text bold>{d.persona?.emoji ?? '🖥'} {d.persona?.name ?? T('no_persona')}</Text>{isStale(d.measured_at, now()) ? <Text color="warning">  {T('stale')}</Text> : null}</Text>
          <Text wrap="truncate-end">{Lg(`   @${d.code ?? '?'}  ·  ${d.role ?? '?'}  ·  ${tierLabel(d.tier)}  ·  ${T('cabin', { value: (d.cabin ?? '?').split('-').pop() ?? '?' })}  ·  ${T('measured_ago', { when: ago(now() - d.measured_at) })}${took(d.took_ms)}`, { dim: true })}</Text>
        </Box>
      : <Text color={S.err ? 'error' : 'warning'}>{S.err ? T('na_err', { err: S.err }) : T('measuring')}</Text>
    // 今天頁自己有狀態列,不再疊舊標題(the maintainer 02:5x「版面還要再調整」:實看面板 103×45,上方重複)
    const page = (...body: unknown[]) => (<Box flexDirection="column">{bar}{t === 'today' && d ? null : head}{body as never}</Box>)
    const cols = e.props?.bodyColumns ?? e.viewport?.columns ?? 120, half = Math.max(44, Math.floor((cols - 3) / 2))
    const tier = tierOf(d?.tier)

    if (t === 'charts' || t === 'custom') {
      const C = await read($, charts), cd = C.data
      if (!cd && !isBusy('charts') && !C.err) void refreshCharts($)
      if (!cd) return page(<Text color={C.err ? 'error' : 'warning'}>{C.err ? T('charts_err', { err: C.err }) : T('charts_loading')}</Text>)
      const seatId = d?.seat_id ?? 'unknown', list = await loadPicks($, seatId, tier)
      if (t === 'custom') {
        const all = TIER_ALL[tier]!, toggle = (k: string) => () => { animStart($); const cur = list.includes(k) ? list.filter(x => x !== k) : [...list, k]; void savePicks($, seatId, cur).then(() => $.ui.invalidate('ui.render')) }
        // the maintainer 10-14 04:4x「這有點擠,不美觀,請重新排列對齊」:等寬格子排(每格同寬、每列同欄數),勾了的標順序號;段與段空一行
        const tw = (x: string) => [...x].reduce((a, ch) => a + (/[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\uff00-\uff60]/.test(ch) ? 2 : 1), 0)
        const cellW = Math.max(...all.map(k => tw(CHART_TITLE[k] ?? k))) + 14, inner = Math.max(cellW, Math.min(cols - 8, 120))
        const perRow = Math.max(1, Math.floor(inner / cellW)), gridRows: string[][] = []
        for (let k = 0; k < all.length; k += perRow) gridRows.push(all.slice(k, k + perRow))
        const order = pinFirst(list), no = (k: string) => (list.includes(k) ? String(order.indexOf(k) + 1).padStart(2, '0') : '  ')
        return page(
          <Box flexDirection="column" paddingX={2} marginTop={1}>
            <Text bold>{T('custom_title')}</Text>
            <Text dimColor>{T('custom_sub', { value: tierLabel(d?.tier), v1: list.length, v2: all.length })}</Text>
            <Box flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={2} paddingY={1} marginTop={1} width={perRow * cellW + 6}>
              {gridRows.map((row, ri) => (
                <Box flexDirection="row" marginBottom={ri < gridRows.length - 1 ? 1 : 0}>
                  {row.map(k => (
                    <Box width={cellW}>
                      <Button key={`pick-${k}`} variant={list.includes(k) ? 'primary' : 'secondary'} onPress={toggle(k)}>{list.includes(k) ? '☑' : '☐'} {no(k)} {CHART_TITLE[k] ?? k}</Button>
                    </Box>))}
                </Box>))}
            </Box>
            <Box flexDirection="row" gap={2} marginTop={1}><Button key="pick-reset" onPress={() => { void savePicks($, seatId, TIER_DEFAULT[tier]!).then(() => $.ui.invalidate('ui.render')) }}>{T('pick_reset')}</Button><Button key="pick-all" onPress={() => { void savePicks($, seatId, all).then(() => $.ui.invalidate('ui.render')) }}>{T('pick_all')}</Button><Button key="pick-none" onPress={() => { void savePicks($, seatId, []).then(() => $.ui.invalidate('ui.render')) }}>{T('pick_none')}</Button></Box>
            <Box marginTop={1}><Text dimColor>{T('custom_hint')}</Text></Box>
          </Box>)
      }
      const ctx = await readCtx($), cx = { cd, ctx, frame, half, full: cols - 2, now: now(), scr: d }
      // 半寬放不下的圖(雷達、熱力格、和弦這類固定大小的)⇒ 用整寬重畫、獨佔一列(同 aib boards 小窗格),不讓它被折行
      const rowW = (c: { body: { text: string }[][] }) => Math.max(0, ...c.body.map(r => sw(r.map(sg => sg.text).join(''))))
      const cards = pinFirst(list).map(k => { const c = buildCard(k, cx); if (!c || c.wide || rowW(c) <= half - 4) return c
        const w = buildCard(k, { ...cx, half: cols - 2 }); return w ? { ...w, wide: true } : c }).filter((x): x is NonNullable<typeof x> => !!x)
      const missingCards = pinFirst(list).filter(k => !buildCard(k, cx)).map(k => CHART_TITLE[k] ?? k)
      return page(
        <Text dimColor>{T('charts_head', { value: tierLabel(d?.tier), n: cards.length, when: ago(now() - cd.measured_at) })}{took(cd.took_ms)}{missingCards.length ? T('charts_missing', { what: missingCards.join(T('sep')) }) : ''}</Text>,
        (() => {
          // 兩欄各自往下疊(the maintainer 03:13「這個圖表區,如果一個過高,其他會很怪」):每張卡放到目前比較矮的那一欄;寬卡獨佔一列、前後重新起欄
          const card = (c: (typeof cards)[number], i: number) => (
            <Box flexDirection="column" width={c.wide ? cols - 2 : half} borderStyle="round" borderColor="subtle" paddingX={1} marginBottom={1}>
              <Text wrap="truncate-end"><Text color="warning">{String(i + 1).padStart(2, '0')} </Text><Text bold>{c.title}</Text>{c.big ? <Text> · {c.big}</Text> : null}</Text>
              <Text dimColor wrap="truncate-end">{c.how}</Text>
              {rowsEl(c.body)}
              <Text dimColor wrap="truncate-end">{c.src}</Text>
            </Box>)
          const blocks: unknown[] = []
          let colA: unknown[] = [], colB: unknown[] = [], hA = 0, hB = 0
          const flush = () => { if (colA.length || colB.length) blocks.push(<Box flexDirection="row" gap={1} alignItems="flex-start"><Box flexDirection="column" width={half}>{colA as never}</Box><Box flexDirection="column" width={half}>{colB as never}</Box></Box>); colA = []; colB = []; hA = 0; hB = 0 }
          cards.forEach((c, i) => {
            if (c.wide) { flush(); blocks.push(card(c, i)); return }
            const h = c.body.length + 4
            if (hA <= hB) { colA.push(card(c, i)); hA += h } else { colB.push(card(c, i)); hB += h }
          })
          flush()
          return <Box flexDirection="column">{blocks as never}</Box>
        })())
    }
    if (t === 'dm') {
      const D = await read($, dm), dd = D.data
      if (dmDirtyAt && Date.now() - dmDirtyAt > 2000 && !isBusy('dm')) { dmDirtyAt = 0; void refreshDm($, true) }
      else if (!dd && !isBusy('dm') && !D.err) void refreshDm($)
      else if (dmDirtyAt && !dmWake) { dmWake = true; $.clock.after(2100, () => { dmWake = false; $.ui.invalidate('ui.render') }) }
      if (!dd) return page(<Text color={D.err ? 'error' : 'warning'}>{D.err ? T('dm_err', { err: D.err }) : T('dm_loading')}</Text>)
      // 手上是舊格式(沒有狀態 / 原廠色,資料層升級前抓的)⇒ 自己重抓一次,不等人按重新整理(the maintainer 03:4x「icon 色還是沒出現」)
      if (!isBusy('dm') && ((dd.chat?.length ? !dd.chat.some(r => r.state) : false) || !Object.values(dd.seats ?? {}).some(x => x.color)) && !dmUpgraded) { dmUpgraded = true; void refreshDm($, true) }
      const col = (s: string) => s === 'waiting' ? 'error' : s === 'read' ? 'warning' : 'success', lbl = (s: string) => (s === 'waiting' ? T('st_unread') : s === 'read' ? T('st_read_open') : s === 'done' ? T('st_done') : s)
      // 「對話」頁(the maintainer 10-14 03:3x「私訊改成對話,分左右 8:2;現在的內容改到右邊,左邊改成像 telegram 的對話,要帶 AI 的 logo、顏色、人格」)
      const ex = await read($, expand)
      const TW2 = cols - 4, LW = Math.floor(TW2 * 0.8), RW = Math.max(18, TW2 - LW - 1)
      const seats = dd.seats ?? {}, me = d?.code ?? ''
      // the maintainer 03:46:框色每席不同(由席碼固定挑);只有標誌用原廠色(render_lib.ENGINE_LETTER_COLORS,grok 黑白 ⇒ 預設字色)
      const FRAME = ['claude', 'success', 'suggestion', 'warning', 'permission', 'autoAccept', 'planMode']
      const seatColor = (c?: string | null) => { if (!c) return 'inactive'; let h = 0; for (const ch of c) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return FRAME[h % FRAME.length]! }
      const logoEl = (c?: string | null) => (c && seats[c]?.logo ? <Text color={seats[c]?.color || undefined}>{seats[c]!.logo} </Text> : null)
      const who = (c?: string | null) => { const x = c ? seats[c] : undefined
        return `${x?.emoji ? x.emoji + ' ' : ''}${x?.name ?? x?.role ?? ''}${c ? ` @${c}` : ''}`.trim() }
      // 真實狀態(the maintainer 03:4x):每則訊息頭帶狀態;最上方總覽 收到 / 寄出 × 未讀 / 已讀 / 完成 / 卡住 / 封存,點了只看那一種
      const ST: Record<string, [string, string]> = { unread: [T('st_unread'), 'error'], read: [T('st_read'), 'warning'], done: [T('st_done'), 'success'], held: [T('st_held'), 'permission'], archived: [T('st_archived'), 'inactive'] }
      const allChat = dd.chat ?? [], fk = (ex as Record<string, unknown>).chatf as string | undefined
      const chat = fk ? allChat.filter(r => `${r.out ? 'out' : 'in'}:${r.state ?? 'unread'}` === fk) : allChat, shown = ex.chat ? chat : chat.slice(-24)
      const tally = (out: boolean) => Object.keys(ST).map(k => [k, allChat.filter(r => r.out === out && (r.state ?? 'unread') === k).length] as [string, number]).filter(x => x[1])
      const setF = (v?: string) => () => { void update($, expand, x => ({ ...x, chatf: v as never })) }
      const summary = (
        <Box flexDirection="column" marginBottom={1}>
          {([[false, T('recv')], [true, T('sent')]] as [boolean, string][]).map(([out, nm]) => (
            <Box flexDirection="row" gap={1} flexWrap="wrap"><Box width={11}><Text dimColor>{nm}</Text></Box>
              {tally(out).map(([k, n]) => <Button key={`f-${out ? 'out' : 'in'}-${k}`} variant={fk === `${out ? 'out' : 'in'}:${k}` ? 'primary' : 'secondary'} onPress={setF(fk === `${out ? 'out' : 'in'}:${k}` ? undefined : `${out ? 'out' : 'in'}:${k}`)}>{ST[k]![0]} {n}</Button>)}
              {tally(out).length ? null : <Text dimColor>{T('none')}</Text>}
            </Box>))}
          {fk ? <Box><Button key="f-all" variant="secondary" onPress={setF(undefined)}>{T('view_all', { n: allChat.length })}</Button></Box> : null}
        </Box>)
      // 我在右、對方在左(the maintainer 10-14 04:0x「自已是在右邊,而對方是在左邊」):泡泡只佔六成寬,左右才分得開(七成二時兩邊疊在中間,看不出誰是誰)
      const bubbleW = Math.max(28, Math.floor((LW - 4) * 0.6))
      let lastDay = ''
      const msgs = shown.map(r => {
        const day = new Date(r.at * 1000).toLocaleDateString(loc, { month: '2-digit', day: '2-digit' }), sep = day !== lastDay ? day : ''
        lastDay = day
        const other = r.out ? r.to : r.from, c = seatColor(r.out ? me : r.from)
        return (
          <Box flexDirection="column" width={LW - 4}>
            {sep ? <Box justifyContent="center" marginY={1}><Text dimColor>──── {sep} ────</Text></Box> : null}
            <Box flexDirection="column" alignItems={r.out ? 'flex-end' : 'flex-start'} marginBottom={1}>
              <Text>{(() => { const stt = r.state ? ST[r.state] : undefined; const badge = stt ? <Text color={stt[1]} bold={r.state === 'unread'}>[{r.out ? T('badge_them', { text: stt[0] }) : stt[0]}]</Text> : null
                // the maintainer 04:08:右邊那則的頭原本秀對方的標誌與人格(「我 → ⚓ 第二艦長」),一眼看成右邊是對方 ⇒ 右邊頭只放我自己;收件人收成一小段灰字
                return r.out ? <Text>{badge}<Text dimColor>  {hhmm(r.at)}  {T('to_who', { who: other ? `@${other}` : '?' })}  </Text>{logoEl(me)}<Text color={c} bold>{T('me_suffix', { name: who(me) })}</Text></Text> : <Text>{logoEl(r.from)}<Text color={c} bold>{who(r.from)}</Text><Text dimColor>  {hhmm(r.at)}  </Text>{badge}</Text> })()}</Text>
              <Box borderStyle="round" borderColor={c} paddingX={1} width={bubbleW}><Text wrap="wrap">{r.text}</Text></Box>
            </Box>
          </Box>)
      })
      const left = (
        <Box flexDirection="column" borderStyle="round" borderColor="claude" paddingX={1} width={LW}>
          <Box flexDirection="row" justifyContent="space-between"><Text><Text bold>{T('tab_dm')}</Text><Text dimColor>{T('chat_sub', { n: chat.length })}</Text>{logoEl(me)}<Text color={seatColor(me)}>{who(me)}</Text></Text>
            {chat.length > 24 ? <Button key="ex-chat" variant="secondary" onPress={() => { void update($, expand, v => ({ ...v, chat: !v.chat })) }}>{ex.chat ? T('only_recent') : T('earlier', { n: chat.length - 24 })}</Button> : null}</Box>
          {summary}
          {msgs.length ? msgs : [<Text dimColor>{fk ? T('no_msg_state') : T('no_chat')}</Text>]}
        </Box>)
      const right = (
        <Box flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1} width={RW}>
          <Text bold>{T('inbox_title')}</Text>
          <Text><Text color="error">{T('hex_unread', { n: dd.counts.unread })}</Text><Text dimColor> · </Text><Text color="warning">{T('open_short', { n: dd.counts.open })}</Text></Text>
          <Text dimColor>{T('done7', { n: dd.counts.done_7d })}</Text>
          {dd.items.slice(0, 20).map(m => (
            <Box flexDirection="column" marginTop={1}>
              <Text wrap="truncate-end"><Text color={col(m.status)} dimColor={m.status === 'waiting' && !saver && frame % 4 >= 2}>●</Text> {Lg(m.sender_seat ? `@${m.sender_seat}` : (m.sender ?? '?'), { color: seatColor(m.sender_seat), bold: true })}</Text>
              <Text dimColor wrap="truncate-end">{m.when ?? ''} · {lbl(m.status)}</Text>
              <Text wrap="truncate-end" dimColor={m.status === 'done'}>{m.text.replace(/\s+/g, ' ')}</Text>
            </Box>))}
        </Box>)
      return page(<Box flexDirection="row" gap={1} paddingX={2} marginTop={1}>{left}{right}</Box>)
    }
    if (t === 'proj') {
      const P = await read($, plan), pd = P.data
      if (!pd && !isBusy('plan') && !P.err) void refreshPlan($)
      if (!pd) return page(<Text color={P.err ? 'warning' : 'warning'}>{P.err ? T('proj_err', { err: P.err }) : T('plan_loading')}</Text>, !d?.project ? <Text dimColor>{T('bind_hint')}</Text> : null)
      const kids = new Map<string, typeof pd.nodes>(); pd.nodes.forEach(n => { const k = n.parent ?? ''; kids.set(k, [...(kids.get(k) ?? []), n]) })
      const out: unknown[] = [], ST: Record<string, [string, string]> = { done: ['✔', 'success'], in_progress: ['▶', 'suggestion'], waiting_prereq: ['⛓', 'warning'], blocked: ['✖', 'error'], partial: ['◐', 'inactive'], pending: ['○', 'inactive'] }
      const LY: Record<string, string> = { goal: T('layer_goal'), major: T('layer_major'), sub: T('layer_sub'), task: 'Task' }
      const walk = (pid: string, depth: number) => (kids.get(pid) ?? []).forEach(n => {
        const [ic, c] = ST[n.status] ?? ['·', 'inactive'], mine = !!d?.code && (n.owner ?? '').includes(d.code), pr = n.progress == null ? null : Math.max(0, Math.min(1, n.progress))
        out.push(<Text wrap="truncate-end"><Text>{'  '.repeat(depth)}</Text><Text color={c} dimColor={n.status === 'in_progress' && !saver && frame % 4 >= 2}>{ic} </Text><Text dimColor>{LY[n.layer] ?? n.layer} </Text><Text bold={mine} color={mine ? 'claude' : 'text'}>{n.title}</Text>{pr != null ? <Text color="success"> {'█'.repeat(Math.round(pr * 8))}<Text color="subtle">{'░'.repeat(8 - Math.round(pr * 8))}</Text> {Math.round(pr * 100)}%</Text> : null}{n.owner ? Lg(` · ${n.owner}`, { dim: true }) : null}</Text>)
        walk(n.id, depth + 1) })
      const roots = pd.nodes.filter(n => !n.parent || !pd.nodes.some(m => m.id === n.parent)); roots.forEach(r => { kids.set('__root', roots) }); walk('__root', 0)
      const cnt = (s: string) => pd.nodes.filter(n => n.status === s).length
      const PW = Math.min(120, cols - 4)
      return page(
        <Box flexDirection="column" paddingX={2} marginTop={1} width={PW + 4}>
          <Box flexDirection="row" gap={3}><Text bold>{T('tab_proj')}</Text><Text>{pd.title ?? ''}</Text><Text dimColor>{pd.project_uid}</Text></Box>
          <Box flexDirection="row" gap={3} marginBottom={1}><Text dimColor>{T('nodes_n', { n: pd.nodes.length })}</Text><Text color="success">✔ {cnt('done')}</Text><Text color="suggestion">▶ {cnt('in_progress')}</Text><Text color="warning">⛓ {cnt('waiting_prereq')}</Text><Text color="error">✖ {cnt('blocked')}</Text><Text dimColor>{T('mine_lit')}</Text></Box>
          <Box flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={2}>{out as never}</Box>
        </Box>)
    }
    if (!d) return page()
    // 今天頁 = 流程儀表板(the maintainer 10-14 02:5x 參考圖:上方狀態列、左欄此刻、中間流程框 + 條、下方紀錄、底部一行狀態)
    //   內容照 §22.4:此刻 / 待辦 / 我的包走到哪 / 人格專案應知收一行;雷達與圓餅在「自訂」
    const W = Math.min(120, cols - 4), SW = 30, FW = Math.max(40, W - SW - 2), LBL = labelW(), RL = rowLabelW()
    // §22.5 第 3 步:沒手動開關過的區塊照使用次數決定開合;每次點都記一筆(這一席自己的設定檔)
    const sid = d.seat_id ?? d.code ?? 'unknown', use = await loadUsage($, sid)
    const ex = effectiveEx(await read($, expand), use)
    const flip = (k: string) => () => { const was = !!ex[k]; void update($, expand, v => ({ ...v, [k]: !was })); void saveUsage($, sid, bumpUsage(usage, k)) }
    const resetLayout = () => { void update($, expand, () => ({})); void saveUsage($, sid, {}) }
    const pk = myPkgs(d.know), ctxP = await readCtx($), nowS = now()
    const cells = [ctxCell(ctxP), leaseCell(d.know, d.code, nowS), mailCell(d.know, d.todo), pkgCell(pk)]
    const live = !saver, spin = live ? SPIN[frame % SPIN.length] : '·', pulse = (c: string) => live && c === 'error' && frame % 4 >= 2
    const kd = (s: string) => d.know.find(k => k.sensor === s)
    const lease = (kd('machine_lease')?.data ?? {}) as { state?: string; owner?: string; acquired_epoch?: number; expiry_epoch?: number; queue?: number }
    const push = (kd('push_chain')?.data ?? {}) as { running?: { pid: number; elapsed_s: number; branch?: string | null }[]; queued?: number | null }
    const mainK = kd('main_head'), main = (mainK?.data ?? {}) as { sha?: string; committed_at?: number }
    const groups = groupTodos(d.todo), cnt = (id: string) => groups.find(g => g.id === id)?.items.length ?? 0
    const sd = d.now?.steps_done ?? null, st = d.now?.steps_total ?? null
    const [bf, be] = meter(st ? (sd ?? 0) / st : 0, 12)
    const th = themeOf(d.persona?.theme)   // §22.5 第 2 步:人格主題(主色 / 光點 / 速度 / 框線)
    const frameBox = (title: string, color: string, w: number, ...kids: unknown[]) => (
      <Box flexDirection="column" borderStyle={th.border} borderColor={color} paddingX={1} width={w}>
        <Text wrap="truncate-end"><Text bold color={color}>{title}</Text></Text>
        {kids as never}
      </Box>)
    // 流程框之間的連接線:一顆光點往下流(動畫開著才動)
    // 框之間的連接線(the maintainer 03:06 高一點 + 粒子;03:07 有順序、線性;03:09 走快一點、尾巴拉長、所有粒子端點與方向一致):
    //   全部由上往下;每條線(含分岔的三道)同一個相位:光點從線頂出發、走到線底,後面拖 3 格漸暗尾巴,尾巴走完才重來
    const LH = 4, TAIL = 3, pos = Math.floor((frame * th.mul) / th.div) % (LH + TAIL)
    const dot = (r: number): [string, string, boolean] => {   // 第 r 行(0 = 線頂)該畫什麼:[字, 顏色, 暗]
      if (!live) return ['│', 'subtle', true]
      const k = pos - r
      return k === 0 ? [th.particle, th.accent, false] : k === 1 ? [th.particle, th.accent, true] : k === 2 ? ['•', th.accent, true] : k === 3 ? ['│', th.accent, true] : ['│', 'subtle', true]
    }
    // 端點要正確(the maintainer 03:09):所有光點落在同一欄 MID = 中間那個框的正中(分岔線的 ┴ 也在這一欄);標籤一律在光點右邊,不參與置中
    const T3 = Math.floor((FW - 2) / 3), MID = T3 + 1 + Math.floor(T3 / 2)
    const link = (label?: string) => (
      <Box flexDirection="column" paddingLeft={MID} width={FW}>
        {Array.from({ length: LH }, (_, r) => { const [ch, c, dim] = dot(r)
          return <Text><Text color={c} dimColor={dim} bold={!dim}>{ch}</Text>{r === 1 && label ? <Text dimColor>  {label}</Text> : null}</Text> })}
      </Box>)
    // 分岔線:上面一條橫線(接住上一條線,不跑光點),下面三道跟其他線同一相位往下流到三個框的正中
    const fork = (third: number) => {
      const c0 = Math.floor(third / 2), c1 = MID, c2 = 2 * (third + 1) + Math.floor(third / 2)
      const line = Array.from({ length: c2 + 1 }, (_, x) => x === c0 ? '┌' : x === c1 ? '┴' : x === c2 ? '┐' : x > c0 && x < c2 ? '─' : ' ').join('')
      const pad = (n: number) => ' '.repeat(Math.max(0, n))
      return (
        <Box flexDirection="column" width={FW}>
          <Text color="subtle">{line}</Text>
          {Array.from({ length: LH - 1 }, (_, r) => { const [ch, c, dim] = dot(r + 1)
            const g = <Text color={c} dimColor={dim} bold={!dim}>{ch}</Text>
            return <Text>{pad(c0)}{g}{pad(c1 - c0 - 1)}{g}{pad(c2 - c1 - 1)}{g}</Text> })}
        </Box>)
    }
    const kv = (k: string, v: unknown, c?: string) => (
      <Box flexDirection="row"><Box width={LBL}><Text dimColor>{k}</Text></Box><Box flexGrow={1} flexShrink={1}><Text color={c} wrap="truncate-end">{v as never}</Text></Box></Box>)

    // 上方狀態列
    const top = (
      <Box flexDirection="row" borderStyle={th.border} borderColor="subtle" paddingX={1} width={W} justifyContent="space-between">
        <Text wrap="truncate-end"><Text bold>{d.persona?.emoji ?? '🖥'} {d.persona?.name ?? T('no_persona')}</Text>{Lg(`  @${d.code ?? '?'} · ${d.role ?? '?'} · ${T('cabin', { value: (d.cabin ?? '?').split('-').pop() ?? '?' })}`, { dim: true })}</Text>
        <Text>{cells.map((c, k) => <Text>{k ? '   ' : ''}<Text color={c.color} dimColor={pulse(c.color)}>■</Text><Text dimColor> {c.label} </Text><Text color={c.color}>{c.text}</Text></Text>)}</Text>
      </Box>)

    // 左欄:此刻(像參考圖的評審面板)
    // 步數條(the maintainer 03:26「這個方格子是不是也沒有動態?」):一步一段,做完的段填滿,
    //   光波同「我的包」—— 慢、從第一格掃到最後一格(做完的也掃亮)
    const stepBar = (() => {
      const n = Math.max(1, st ?? 1), tail = Math.max(8, sw(T('steps', { v1: sd ?? 0, v2: st ?? 0 }))), segW = Math.max(1, Math.floor((SW - 4 - tail - (n - 1)) / n)), done = Math.min(n, sd ?? 0)   // 尾巴「 2/5 步」照語系量寬(日文「ステップ」較長)
      const span = n * (segW + 1), wave = live ? Math.floor(frame / 2) % (span + 6) : -99
      return <Text>{Array.from({ length: n }, (_, k) => <Text>{k ? ' ' : ''}{Array.from({ length: segW }, (_, x) => {
        const dist = wave - (k * (segW + 1) + x), fill = k < done
        return dist === 0 ? <Text color={th.accent} bold>█</Text> : dist === 1 || dist === 2 ? <Text color={th.accent} dimColor>{dist === 1 ? '▓' : '▒'}</Text> : <Text color={fill ? th.accent : 'subtle'} dimColor={!fill}>{fill ? '█' : '░'}</Text> })}</Text>)}<Text dimColor>{T('steps', { v1: sd ?? 0, v2: st ?? 0 })}</Text></Text>
    })()
    const side = (
      <Box flexDirection="column" borderStyle={th.border} borderColor={th.accent} paddingX={1} width={SW}>
        <Text><Text bold color="claude">{T('now_title')}</Text><Text dimColor>{T('now_sub')}</Text></Text>
        <Text color={d.now?.state === 'decide' ? 'warning' : 'claude'}>{spin} 【{nowState(d.now?.state)}】{d.now?.at ? <Text dimColor> {ago(nowS - d.now.at)}</Text> : null}</Text>
        <Box borderStyle="single" borderColor="subtle" paddingX={1}><Text wrap="wrap">{d.now?.text ?? T('now_none')}</Text></Box>
        {st ? stepBar : <Text dimColor>{T('no_steps')}</Text>}
        <Box marginTop={1}><Text dimColor>{T('checks')}</Text></Box>
        {cells.map(c => (
          <Box flexDirection="column">
            <Text><Text color={c.color} dimColor={pulse(c.color)}>◆</Text> <Text>{c.label}</Text> <Text bold color={c.color}>{c.text}</Text></Text>
            {c.sub ? <Box paddingLeft={2}><Text wrap="truncate-end">{Lg(c.sub, { dim: true })}</Text></Box> : null}
          </Box>))}
        <Box marginTop={1} flexDirection="column">
          {kv(T('kv_todo'), T('todo_n', { n: d.todo.length }))}
          {kv(T('grp_now'), cnt('now'), cnt('now') ? 'error' : undefined)}
          {kv(T('kv_human'), cnt('human'), cnt('human') ? 'warning' : undefined)}
          {kv(T('kv_know'), T('know_n', { n: d.know.length, v1: d.know.filter(k => k.level === 'warn').length, v2: d.know.filter(k => k.level === 'red').length }))}
          {kv(T('kv_measured'), `${ago(nowS - d.measured_at)}${took(d.took_ms)}`)}
        </Box>
      </Box>)

    // 中間流程:我 → 我的包 → 推包席 → 機器 / 主線 / 信箱 → 回到我
    const shown = ex.pkgs ? pk.open : pk.open.slice(0, 4)
    // 每條包一列(the maintainer 03:16「數值旁的格子太近?而且沒有變化?」):名字 | 三段格(交 / 合 / 主,走過的填滿,下一關有亮點往右流,還沒到的暗格)| 關卡字 | 顆數,欄間留 2 格
    const SEG = Math.max(3, Math.min(5, Math.floor((FW - 4 - 24 - 2 - 2 - 12 - 2 - 8) / 3)))
    const pkgRow = (t: Pkg) => {
      const st0 = typeof t.stage === 'number' ? t.stage : -1, red = !!t.reds?.length
      // 光波(the maintainer 03:29「太快了,改更優雅一點,要隨機感,光波可以一直推到最後」):一道光從第一格掃到最後一格(走過的段也被掃亮);
      //   每列起點與速度由分支名決定(看起來隨機、每次一樣),2~3 格(0.4~0.6 秒)才走一格,掃完停一下再來
      let hsh = 0; for (const ch of t.branch) hsh = (hsh * 31 + ch.charCodeAt(0)) >>> 0
      const span = 3 * SEG + 2, slow = 2 + (hsh % 2), cyc = span + 6 + (hsh % 5)
      const wave = live ? Math.floor((frame + (hsh % 97)) / slow) % cyc : -99
      const cell = (k: number, x: number) => {   // 第 k 段第 x 格
        const g = k * (SEG + 1) + x, dist = wave - g, filled = st0 > k
        const base = filled ? (red ? 'error' : 'success') : 'subtle'
        if (dist === 0) return <Text color={red ? 'error' : filled ? 'success' : 'warning'} bold>█</Text>
        if (dist === 1 || dist === 2) return <Text color={red ? 'error' : filled ? 'success' : 'warning'} dimColor>{dist === 1 ? '▓' : '▒'}</Text>
        return <Text color={base} dimColor={!filled}>{filled ? '█' : '░'}</Text>
      }
      const segs = [0, 1, 2].map(k => <Text>{Array.from({ length: SEG }, (_, x) => cell(k, x))}</Text>)
      return (
        <Box flexDirection="column">
          <Box flexDirection="row">
            <Box width={24} marginRight={2}><Text wrap="truncate-middle" bold={red}>{shortBranch(t.branch)}</Text></Box>
            <Text>{segs[0]}<Text> </Text>{segs[1]}<Text> </Text>{segs[2]}</Text>
            <Box marginLeft={2}><Text>{pkgStages(t).map(([n, v], k) => { const cur = st0 === k
              // 正在走的那關:光波推到最後一格才亮(the maintainer 03:3x「交● 等到光推到,再亮」),亮 4 拍後暗回去
              const lit = cur && wave >= span && wave < span + 4
              return <Text>{k ? ' ' : ''}<Text color={v === null ? 'inactive' : v ? 'success' : lit ? 'warning' : 'subtle'} bold={lit} dimColor={cur && !lit}>{n[0]}{v === null ? '?' : v ? '✓' : cur ? '●' : '·'}</Text></Text> })}</Text></Box>
            <Box marginLeft={2}><Text dimColor>{T('big_commits', { n: t.commits ?? '?' })}{t.dirty ? ' ✎' : ''}</Text></Box>
          </Box>
          {red ? <Box paddingLeft={2}><Text color="error" wrap="truncate-end">{T('reds_line', { what: t.reds!.join(T('sep')) })}{t.refused ? T('refused', { what: t.refused }) : ''}</Text></Box> : null}
        </Box>) }
    const running = push.running?.[0]
    const leaseUsed = lease.acquired_epoch && lease.expiry_epoch ? (nowS - lease.acquired_epoch) / Math.max(1, lease.expiry_epoch - lease.acquired_epoch) : 0
    const [lf, le] = meter(leaseUsed, 10)
    const third = Math.floor((FW - 2) / 3)
    const unreadTodo = d.todo.filter(t => t.source === 'dm').sort((a, b) => (b.since ?? 0) - (a.since ?? 0))
    const topTodo = groups[0]?.items[0]
    // §22.5 第 1 步 依角色換流程:驗收席 待驗 → 驗過 → 退回;bot 席 來訊 → 回覆 → 結案(節點與資料由 S1 給,這裡只照著畫)
    const rf = roleFlow(d.flow)
    const flowRole = rf ? (
      <Box flexDirection="column" width={FW}>
        {rf.nodes.flatMap((n, k) => [link(k ? undefined : T('flow_from')),
          frameBox(T(`flow_${n.key}`, { n: n.count }), flowColor(n.level), FW,
            ...(n.items.length ? n.items.map(i => <Text wrap="truncate-end">{Lg(i.who.replace(/[（(].*$/, ''), { color: 'claude' })}<Text> {i.text.replace(/\s+/g, ' ')}</Text></Text>)
              : [<Text dimColor>{T('flow_empty')}</Text>]),
            n.guess ? <Text dimColor wrap="truncate-end">{T('flow_guess')}</Text> : null)])}
      </Box>) : null
    const flowDev = (
      <Box flexDirection="column" width={FW}>
        {link(T('link_out'))}
        {frameBox(T('pkgs_title', { what: pk.known ? `${T('pkgs_open', { n: pk.open.length })}${pk.landed.length ? T('pkgs_landed', { n: pk.landed.length }) : ''}` : T('na') }), pk.open.some(t => t.reds?.length) ? 'error' : 'success', FW,
          ...(pk.known ? (shown.length ? shown.map(t => pkgRow(t)) : [<Text color="success">{T('all_landed')}</Text>]) : [<Text dimColor>{T('no_trees_role')}</Text>]),
          pk.open.length > 4 ? <Box><Button key="ex-pkgs" variant="secondary" onPress={flip('pkgs')}>{ex.pkgs ? T('collapse') : T('more_pkgs', { n: pk.open.length - 4 })}</Button></Box> : null,
          <Text dimColor wrap="truncate-end">{T('stage_legend')}</Text>)}
        {link(T('link_batch'))}
        {frameBox(T('ship_title', { what: running ? T('ship_running') : T('ship_idle') }), running ? 'warning' : 'subtle', FW,
          running ? <Text wrap="truncate-end"><Text color="warning">{spin} </Text>{running.branch ?? '?'}<Text dimColor>{T('ship_ran', { n: Math.round(running.elapsed_s / 60), pid: running.pid })}</Text></Text> : <Text dimColor>{T('ship_nobody')}</Text>,
          push.queued ? <Text dimColor>{T('machine_queue', { n: push.queued })}</Text> : null)}
        {third >= 26 ? fork(third) : link(T('link_split'))}
        {third >= 26 ? (
        <Box flexDirection="row" gap={1} width={FW}>
          {frameBox(T('lease_title'), lease.state === 'free' ? 'success' : 'warning', third,
            <Text wrap="truncate-end">{lease.state === 'free' ? T('lease_free') : Lg(String(lease.owner ?? T('na')))}</Text>,
            lease.state === 'held' ? <Text><Text color="warning">{lf}</Text><Text color="subtle">{le}</Text></Text> : <Text dimColor> </Text>,
            <Text dimColor wrap="truncate-end">{lease.state === 'held' ? T('lease_span', { v1: hhmm(lease.acquired_epoch), v2: hhmm(lease.expiry_epoch) }) : lease.queue ? T('queue_n', { n: lease.queue }) : T('can_test')}</Text>)}
          {frameBox(T('sensor_main_head'), 'success', third,
            <Text wrap="truncate-end"><Text color="success">{main.sha ?? '?'}</Text><Text dimColor> {main.committed_at ? ago(nowS - main.committed_at) : ''}</Text></Text>,
            <Text wrap="truncate-end" dimColor>{mainK?.line ?? T('na')}</Text>,
            <Text dimColor> </Text>)}
          {frameBox(T('sensor_inbox'), cells[2]!.color, third,
            <Text wrap="truncate-end" color={cells[2]!.color}>{cells[2]!.text}</Text>,
            <Text wrap="truncate-end">{unreadTodo[0] ? <Text>{Lg((unreadTodo[0].from ?? '').replace(/[（(].*$/, ''), { color: 'claude' })} {unreadTodo[0].text}</Text> : <Text dimColor>{T('no_mail_todo')}</Text>}</Text>,
            <Text dimColor>{unreadTodo.length ? T('mail_todo_n', { n: unreadTodo.length }) : ' '}</Text>)}
        </Box>
        ) : frameBox(T('trio_title'), lease.state === 'held' ? 'warning' : 'success', FW,
          kv(T('sensor_machine_lease'), <Text>{lease.state === 'free' ? <Text color="success">{T('lease_free')}</Text> : <Text>{Lg(`${String(lease.owner ?? T('na'))} `, { color: 'warning' })}<Text color="warning">{lf}</Text><Text color="subtle">{le}</Text><Text dimColor>{T('latest', { when: hhmm(lease.expiry_epoch) })}</Text></Text>}</Text>),
          kv(T('sensor_main_head'), <Text><Text color="success">{main.sha ?? '?'}</Text><Text dimColor> {main.committed_at ? ago(nowS - main.committed_at) : ''} {mainK?.line ?? ''}</Text></Text>),
          kv(T('sensor_inbox'), <Text><Text color={cells[2]!.color}>{cells[2]!.text}</Text>{unreadTodo[0] ? Lg(` · ${(unreadTodo[0].from ?? '').replace(/[\uff08(].*$/, '')} ${unreadTodo[0].text}`, { dim: true }) : null}</Text>))}
        {link(T('link_back'))}
        {frameBox(T('next_title'), topTodo ? (groups[0]!.id === 'now' ? 'error' : 'claude') : 'subtle', FW,
          topTodo ? <Text wrap="truncate-end"><Text>{groups[0]!.icon} </Text>{topTodo.from ? Lg(`${topTodo.from.replace(/[（(].*$/, '')} `, { color: 'claude' }) : null}{topTodo.text.replace(/\s+/g, ' ')}</Text> : <Text dimColor>{T('no_todo')}</Text>)}
      </Box>)
    const flow = flowRole ?? flowDev

    // 下方紀錄(像參考圖的 session log)
    const rows = logRows(d.todo, d.know, d.events ?? [], ex.log ? 40 : 12, d.now?.at)
    if (S.at !== logAt) { logAt = S.at; logStart = frame }
    const TG = logTagW(), TW = Math.max(10, W - 4 - 6 - 16 - TG - 3 - 1)
    const rv = live ? reveal(frame, logStart, rows.length) : { rows: rows.length, typing: -1, chars: Infinity }
    const scan = live && rv.chars === Infinity && rows.length ? (frame - logStart) % rows.length : -1
    const clock = new Date(), hms = `${String(clock.getHours()).padStart(2, '0')}:${String(clock.getMinutes()).padStart(2, '0')}:${String(clock.getSeconds()).padStart(2, '0')}`
    const log = (
      <Box flexDirection="column" borderStyle={th.border} borderColor={live ? th.accent : 'subtle'} paddingX={1} width={W}>
        <Box flexDirection="row" justifyContent="space-between">
          <Text><Text color={live && frame % 2 ? 'error' : 'inactive'}>●</Text><Text bold color={live ? 'error' : 'inactive'}> LIVE </Text><Text bold>{T('log_title')}</Text><Text dimColor>{T('log_sub', { value: `${rows.length}${d.events?.length ? `/${d.events.length}` : ''}` })}</Text><Text color="success">{hms}</Text></Text>
          <Button key="ex-log" variant="secondary" onPress={flip('log')}>{ex.log ? T('less') : T('more')}</Button>
        </Box>
        {rows.length ? rows.slice(0, rv.rows).map((r, k) => {
          const typing = k === rv.typing && rv.chars !== Infinity, hot = k === scan
          const [done, noise] = typing ? typeIn(r.text, TW, rv.chars, frame * 7919 + k) : [fit(r.text, TW), '']
          return (
            <Text bold={hot}><Text color={hot ? 'success' : undefined} dimColor={!hot} inverse={hot}>{fit(hhmm(r.at), 6)}</Text><Text> </Text>{Lgw(`${fit(withLogo(r.who, d.logos), 16)} `, { color: 'claude' })}<Text color={r.color}>{fit(r.tag, TG)} </Text><Text color={typing ? 'success' : undefined}>{done}</Text>{noise ? <Text><Text color="success">█</Text><Text color="success" dimColor>{noise.slice(1)}</Text></Text> : null}</Text>)
        }) : [<Text dimColor>{T('no_log')}</Text>]}
        {live && rv.rows < rows.length ? <Text color="success" dimColor>{T('log_loading') + '.'.repeat(frame % 4)}</Text> : null}
      </Box>)

    // 收成一行的:人格 / 專案 / 應知
    const fold = (k: string, title: string, oneLine: unknown, ...more: unknown[]) => (
      <Box flexDirection="column" width={W}>
        <Box flexDirection="row" gap={1}>
          <Button key={`ex-${k}`} variant="secondary" onPress={flip(k)}>{ex[k] ? '▾' : '▸'} {title}</Button>
          <Box flexGrow={1} flexShrink={1}><Text wrap="truncate-end">{oneLine as never}</Text></Box>
        </Box>
        {ex[k] ? <Box flexDirection="column" paddingLeft={4} marginBottom={1}>{more as never}</Box> : null}
      </Box>)
    const row = (dot: unknown, label: string, value: unknown, tail?: string) => (
      <Box flexDirection="row">
        <Box width={2}>{dot as never}</Box>
        <Box width={RL}><Text dimColor wrap="truncate-end">{label}</Text></Box>
        <Box flexGrow={1} flexShrink={1}><Text wrap="truncate-end">{value as never}</Text></Box>
        {tail ? <Box marginLeft={1}><Text dimColor>{tail}</Text></Box> : null}
      </Box>)
    const kc = (lv: string) => d.know.filter(k => k.level === lv).length
    const footer = (
      <Text wrap="truncate-end"><Text dimColor>{T('ft_seat')} [</Text>{Lg(`@${d.code} · ${d.role}`, { color: 'claude', dim: true })}<Text dimColor>]  {T('ft_state')} [<Text color="claude">{nowState(d.now?.state)}{st ? ` ${sd ?? 0}/${st}` : ''}</Text>]  {T('ft_machine')} [<Text color={cells[1]!.color}>{cells[1]!.text}</Text>]  {T('ft_main')} [<Text color="success">{main.sha ?? '?'}</Text>]  {T('ft_data')} [{ago(nowS - d.measured_at)}]</Text></Text>)
    return page(
      <Box flexDirection="column" paddingX={2} marginTop={1} width={W + 4}>
        {top}
        <Box flexDirection="row" gap={2} marginTop={1}>{side}{flow}</Box>
        <Box marginTop={1}>{log}</Box>
        <Box flexDirection="column" marginTop={1}>
          {/* 常用的往上(§22.5 第 3 步):照這一席點過的次數排 */}
          {foldOrder(['persona', 'project', 'know'], use).map(k => (k === 'persona'
            ? fold('persona', T('fold_persona'), <Text><Text bold>{d.persona?.emoji ?? ''} {d.persona?.name ?? T('no_persona')}</Text><Text dimColor>{T('persona_counts', { v1: (d.persona?.duties ?? []).length, v2: (d.persona?.red_lines ?? []).length })}{d.persona?.uid ? `  [[${d.persona.uid}]]` : ''}</Text></Text>,
            ...(d.persona?.duties ?? []).map(x => row(<Text color="success">•</Text>, T('duty'), x)),
            ...(d.persona?.red_lines ?? []).map(x => row(<Text color="error">•</Text>, T('red_line'), x)))
            : k === 'project'
            ? fold('project', T('fold_project'), d.project ? <Text><Text bold>{d.project.title ?? ''}</Text><Text dimColor>  {d.project.uid ?? ''}</Text></Text> : <Text dimColor>{T('proj_unbound')}</Text>,
            d.project?.work_state ? <Text wrap="wrap" dimColor>{d.project.work_state}</Text> : <Text dimColor>{T('no_progress')}</Text>)
            : fold('know', T('fold_know'), <Text><Text dimColor>{T('know_count', { n: d.know.length })}</Text><Text color="error">● {kc('red')}</Text><Text>  </Text><Text color="warning">● {kc('warn')}</Text><Text>  </Text><Text color="success">● {kc('ok')}</Text><Text>  </Text><Text color="inactive">○ {kc('unknown')}</Text></Text>,
            ...d.know.map(k => { const [lb, val] = knowParts(k)
              return row(<Text color={levelColor(k.level)}>{levelDot(k.level)}</Text>, lb, val, k.measured_at ? ago(nowS - k.measured_at) : '') }))))}
          {Object.keys(use).length ? <Box><Button key="usage-reset" variant="secondary" onPress={resetLayout}>{T('usage_reset')}</Button><Text dimColor>  {T('usage_hint')}</Text></Box> : null}
        </Box>
        {d.missing?.length ? <Text dimColor wrap="truncate-end">{T('missing', { what: d.missing.join(T('sep')) })}</Text> : null}
        <Box marginTop={1}>{footer}</Box>
      </Box>)
  })
}
