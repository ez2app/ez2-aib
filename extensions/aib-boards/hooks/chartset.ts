// 每一級有哪些圖、預設放哪幾張(設計稿 §18 5:4:3 + §18.2 艦長放回 3 張):艦長 14、主管 9、監看 7、工程 6(影子續跑 03:1x 起各級都放第一張);其餘收進「自訂」可勾回。
import type { Rows } from './charts'
import type { Charts, Screen } from '../types'
import { scanRadar, spinPie } from './charts'
import { hexGem, hexKeys, treeRows, shadowRows, stackCols, commitCurve, hbarRows } from './engcharts'
import { seatRadar, seatTable, funnelRows, waitHist, ganttRows, listBars, flakyRows, eventTimeline, shadowCumulative, riverRows, leaseLanes, heatGrid, waterfallRows, chordMatrix, decideCards, beatRows, sankeyRows } from './tiercharts'
import { hexValues, knowBlips, todoParts, logoRows } from './lib'
import { tr } from './i18n'

export type Card = { key: string; title: string; big?: string; how: string; body: Rows; src: string; wide?: boolean }
type Ctx = { cd: Charts; ctx: number | null; frame: number; half: number; full: number; now: number; scr?: Screen | null }
const none = (t: string): Rows => [[{ text: t, color: 'inactive' }]]

export const TIER_DEFAULT: Record<string, string[]> = {
  captain: ['SH', 'SC', 'FR', 'H', 'RV', 'LS', 'HT', 'WF', 'CH', 'FU', 'SK', 'CT', 'BT', 'DC'],
  lead: ['SH', 'SR', 'H', 'PK', 'ST', 'LF', 'WT', 'GT', 'HOT'],
  qa: ['SH', 'H', 'QT', 'FL', 'PA', 'GB', 'EV'],
  eng: ['SH', 'H', 'TR', 'RG', 'CM', 'FI'],
  design: ['SH', 'H', 'TR', 'RG', 'CM', 'FI'],
}
// 自訂頁可勾的全部(預設 + 收起的)
export const TIER_ALL: Record<string, string[]> = {
  captain: [...TIER_DEFAULT.captain!, 'KR', 'TP', 'TR', 'RG', 'CM', 'FI', 'SL'],
  lead: [...TIER_DEFAULT.lead!, 'KR', 'TP', 'TR', 'RG', 'CM', 'FI', 'SL', 'MY'],
  qa: [...TIER_DEFAULT.qa!, 'KR', 'TP', 'TR', 'RG', 'CM', 'FI', 'SL', 'MY'],
  eng: [...TIER_DEFAULT.eng!, 'KR', 'TP', 'SL', 'MY'],
  design: [...TIER_DEFAULT.design!, 'KR', 'TP', 'SL', 'MY'],
}
// 圖表名走 ct_<代碼小寫>;寫成 getter,呼叫端照舊 CHART_TITLE[k]
export const CHART_TITLE: Record<string, string> = Object.defineProperties({} as Record<string, string>,
  Object.fromEntries(['H', 'TR', 'SH', 'RG', 'CM', 'FI', 'SL', 'MY', 'KR', 'TP', 'SR', 'PK', 'ST', 'LF', 'WT', 'GT', 'HOT', 'QT', 'FL', 'PA', 'GB', 'EV', 'SC', 'FR', 'RV', 'LS', 'HT', 'WF', 'CH', 'FU', 'SK', 'CT', 'BT', 'DC'].map(k => [k, { enumerable: true, get: () => tr(`ct_${k.toLowerCase()}`) }])))
// 影子續跑最重要(the maintainer 10-14 03:1x「一定要放最上面」):不管自訂怎麼勾、勾的順序,影子續跑(與艦長的累積圖)一律排最前
export const PINNED = ['SH', 'SC']
export const pinFirst = (list: string[]) => [...PINNED.filter(k => list.includes(k)), ...list.filter(k => !PINNED.includes(k))]
export const tierOf = (t?: string | null) => (t && TIER_DEFAULT[t] ? t : 'eng')

export function buildCard(key: string, c: Ctx): Card | null {
  const card = buildCard0(key, c)
  return card && c.scr?.logos ? { ...card, body: logoRows(card.body, c.scr.logos, c.scr.logo_colors) } : card
}
function buildCard0(key: string, c: Ctx): Card | null {
  const { cd, frame, half, full, now } = c, L = cd.lead, Q = cd.qa, K = cd.captain
  switch (key) {
    case 'H': { const hv = hexValues(cd.hex, c.ctx)
      const hk = hexKeys(), body = hexGem(hv.v, 13, 6, frame).map((row, i) => [...row, { text: '   ' }, ...(i < 6 ? [{ text: `${hk[i]} `, color: hv.v[i] === null ? 'inactive' : 'text' }, { text: hv.v[i] === null ? '—' : String(Math.round(hv.v[i]! * 100)), color: 'claude' }, { text: ` ${hv.raw[i]}`, color: 'inactive' }] : [])])
      return { key, title: CHART_TITLE.H!, how: tr('how_h'), body, src: 'verify-shadow / dm-queue / handoff / CTX' } }
    case 'TR': return { key, title: CHART_TITLE.TR!, big: tr('big_trees', { n: cd.trees.length }), how: tr('how_tr'), body: cd.trees.length ? treeRows(cd.trees, frame, half - 4) : none(tr('none_own_tree')), src: 'git worktree + verify-shadow' }
    case 'SH': return { key, title: CHART_TITLE.SH!, wide: true, how: tr('how_sh'), body: cd.shadow ? shadowRows(cd.shadow, full - 4, frame) : none(tr('none_shadow')), src: tr('src_sh') }
    case 'RG': return { key, title: CHART_TITLE.RG!, big: tr('big_runs', { n: cd.trend.reduce((a, d) => a + d[1] + d[2], 0) }), how: tr('how_rg'), body: cd.trend.some(d => d[1] + d[2]) ? stackCols(cd.trend, 6, frame, half - 4) : none(tr('none_tests_7d')), src: tr('src_rg') }
    case 'CM': return { key, title: CHART_TITLE.CM!, big: tr('big_commits', { n: cd.commits.reduce((a, d) => a + d[1], 0) }), how: tr('how_cm'), body: cd.commits.some(d => d[1]) ? commitCurve(cd.commits, half - 6, 5, frame) : none(tr('none_commits_7d')), src: tr('src_cm') }
    case 'FI': return { key, title: CHART_TITLE.FI!, big: tr('big_top', { n: 10 }), how: tr('how_fi'), body: cd.files.length ? hbarRows(cd.files, half - 4) : none(tr('none_commits')), src: 'git show --numstat' }
    case 'SL': return { key, title: CHART_TITLE.SL!, big: tr('big_top', { n: 8 }), how: tr('how_sl'), body: listBars(cd.slowest.map(x => [`${x[0]} ×${x[2]}`, x[1]] as [string, number]), half - 4, 'warning', tr('unit_sec')), src: 'verify-shadow duration_s' }
    case 'MY': { const v = c.ctx; return { key, title: CHART_TITLE.MY!, how: tr('how_my'), body: v === null ? none(tr('na')) : seatTable([{ code: tr('me'), prov: '', ctx: v, unread: null, now_state: '' }], frame), src: tr('src_my') } }
    // §22.4:從今天頁搬來(雷達 = 應知換個畫法、圓餅 = 待辦件數),預設不勾
    case 'KR': return c.scr ? { key, title: CHART_TITLE.KR!, big: tr('big_items', { n: c.scr.know.length }), how: tr('how_kr'), body: c.scr.know.length ? scanRadar(knowBlips(c.scr.know), 14, 6, frame) : none(tr('none_know')), src: tr('src_kr') } : null
    case 'TP': return c.scr ? { key, title: CHART_TITLE.TP!, big: tr('big_todos', { n: c.scr.todo.length }), how: tr('how_tp'), body: c.scr.todo.length ? spinPie(todoParts(c.scr.todo), 18, 6, frame) : none(tr('no_todo')), src: tr('src_tp') } : null
    case 'SR': return L ? { key, title: CHART_TITLE.SR!, big: tr('big_seats', { n: L.subs.length }), how: tr('how_sr'), body: L.subs.length ? seatRadar(L.subs, 14, 6, frame) : none(tr('none_subs')), src: tr('src_sr') } : null
    case 'PK': return { key, title: CHART_TITLE.PK!, big: tr('big_in_batch', { v1: cd.trees.filter(t => t.in_batch).length, v2: cd.trees.length }), how: tr('how_pk'), body: cd.trees.length ? treeRows(cd.trees, frame, half - 4) : none(tr('none_trees')), src: tr('src_pk') }
    case 'ST': return L ? { key, title: CHART_TITLE.ST!, big: tr('big_seats', { n: L.subs.length }), how: tr('how_st'), body: seatTable(L.subs, frame), src: 'seat-info --fleet' } : null
    case 'LF': return L ? { key, title: CHART_TITLE.LF!, how: tr('how_lf'), body: funnelRows(L.funnel, half - 4, frame), src: tr('src_lf') } : null
    case 'WT': return L ? { key, title: CHART_TITLE.WT!, big: tr('big_mails', { n: L.wait.length }), how: tr('how_wt'), body: waitHist(L.wait, frame), src: 'dm-queue audit' } : null
    case 'GT': return L ? { key, title: CHART_TITLE.GT!, how: tr('how_gt'), body: ganttRows(L.gantt, half - 4, frame), src: 'plan.jsonl + git log' } : null
    case 'HOT': return L ? { key, title: CHART_TITLE.HOT!, how: tr('how_hot'), body: listBars(L.hot.map(h => [tr('hot_runs', { name: h[0], n: h[2] }), h[1]] as [string, number]), half - 4, 'error', tr('unit_red')), src: 'verify-shadow' } : null
    case 'QT': return Q ? { key, title: CHART_TITLE.QT!, big: tr('big_days', { n: 7 }), how: tr('how_qt'), body: Q.fleet_trend.some(d => d[1] + d[2]) ? stackCols(Q.fleet_trend, 6, frame, half - 4) : none(tr('none_tests_7d')), src: 'verify-shadow' } : null
    case 'FL': return Q ? { key, title: CHART_TITLE.FL!, how: tr('how_fl'), body: flakyRows(Q.flaky, half - 4, frame), src: 'verify-shadow' } : null
    case 'PA': return Q ? { key, title: CHART_TITLE.PA!, big: tr('big_days', { n: 3 }), how: tr('how_pa'), body: listBars(Q.pareto, half - 4, 'permission', '', true), src: 'verify-shadow/batch-pushgate.jsonl' } : null
    case 'GB': return Q ? { key, title: CHART_TITLE.GB!, how: tr('how_gb'), body: listBars(Q.gate_blocks.map(g => ['@' + g[0], g[1]] as [string, number]), half - 4, 'warning', tr('unit_times')), src: 'logs/now-gate.log' } : null
    case 'EV': return Q ? { key, title: CHART_TITLE.EV!, big: tr('big_hours', { n: 48 }), how: tr('how_ev'), body: eventTimeline(Q.events ?? [], half - 4, now), src: 'now-gate.log + report-lint-stop.jsonl' } : null
    case 'SC': return K?.shadow ? { key, title: CHART_TITLE.SC!, how: tr('how_sc'), body: shadowCumulative(K.shadow.runs, Math.min(60, full - 6), 7), src: 'test-stats + ship-stats', wide: true } : null
    case 'FR': return K?.seats ? { key, title: CHART_TITLE.FR!, big: tr('big_seats', { n: K.seats.length }), how: tr('how_fr'), body: seatRadar(K.seats, 14, 6, frame), src: tr('src_fr') } : null
    case 'RV': return K?.river ? { key, title: CHART_TITLE.RV!, how: tr('how_rv'), body: riverRows(K.river, frame), src: 'ship-stats' } : null
    case 'LS': return K?.lease ? { key, title: CHART_TITLE.LS!, big: tr('big_hours', { n: 48 }), how: tr('how_ls'), body: leaseLanes(K.lease, half - 4, now), src: 'state/host-lease.json.log' } : null
    case 'HT': return K?.heat ? { key, title: CHART_TITLE.HT!, big: tr('big_hours', { n: 24 }), how: tr('how_ht'), body: heatGrid(K.heat), src: 'dm-queue audit' } : null
    case 'WF': return K?.waterfall ? { key, title: CHART_TITLE.WF!, how: tr('how_wf'), body: waterfallRows(K.waterfall, half - 4, frame), src: tr('src_wf') } : null
    case 'CH': return K?.chord ? { key, title: CHART_TITLE.CH!, big: tr('big_days', { n: 7 }), how: tr('how_ch'), body: chordMatrix(K.chord), src: 'dm-queue' } : null
    case 'FU': return K?.funnel ? { key, title: CHART_TITLE.FU!, how: tr('how_fu'), body: funnelRows(K.funnel, half - 4, frame), src: tr('src_lf') } : null
    case 'SK': return K?.sankey ? { key, title: CHART_TITLE.SK!, big: tr('big_hours', { n: 72 }), how: tr('how_sk'), body: sankeyRows(K.sankey), src: 'batch-pushgate' } : null
    case 'CT': return K?.seats ? { key, title: CHART_TITLE.CT!, how: tr('how_ct'), body: seatTable(K.seats, frame), src: 'seat-info --fleet' } : null
    case 'BT': return K?.beat ? { key, title: CHART_TITLE.BT!, big: tr('big_hours', { n: 72 }), how: tr('how_bt'), body: beatRows(K.beat, frame), src: 'git log main', wide: true } : null
    case 'DC': return K?.decide ? { key, title: CHART_TITLE.DC!, big: tr('big_cards', { n: K.decide.length }), how: tr('how_dc'), body: decideCards(K.decide, half - 4, now), src: tr('src_dc') } : null
  }
  return null
}
