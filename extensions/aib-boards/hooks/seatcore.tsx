import { atom, read, update } from 'claude-code'
import type { Register, EngineInterface, Timer } from 'claude-code'
import { fleetPromptLines, fleetAlerts } from './seat/captain'
import { integPromptLines, integAlerts } from './seat/integrator'
import { devPromptLines, devAlerts } from './seat/dev'
import { qaPromptLines, qaAlerts } from './seat/qa'
import { cardOf, upsert, syncRequests, relayPath, iconOf, clip, STATUS, REQ_PREFIX, type MailCard, type QItem } from './seat/mailcard'
import { statusLabel, promptSection, handoffState, newBlocks, label, type SeatInfo } from './seat/seat-label'
import { tr } from './i18n'
import { openRoom } from './room'   // /aib room(艦長室外掛)拆在 room.ts
import { ready } from './ready'   // 出字前先讀語系 + 字典(與 register.tsx 同一份快取)

// 席位核心(設計稿 §24,the maintainer 10-14「直接把 aib-seat 拿掉」→「整個拿掉,必要的先搬」):原本 aib-seat 外掛裡每一席都要的那一層搬到這裡 ——
// 系統提示的席位身份(+ 艦長 / 整合 / dev / qa 摘要)、狀態列一行、85% 交接提醒、守衛擋下的提示、新信卡片(輸入框上方)。/aib room 是艦長室外掛那段,拆在 room.ts(沒裝就說開發中)。
// aib-seat 的面板、圖表、角色視角、事件流、子代理卡、demo、mod off 都不搬(跟 aib-boards 重複)。字走 mod.aib_boards.seat_* / st_* / mail_*
// (出字前先 ready($) 讀語系與字典,ready.ts 與 register.tsx 共用)。

const REFRESH_MS = 60_000
const mailCards = atom({ plugin: 'aib-boards', key: 'mailCards' } as const, [] as MailCard[])
const mailDismissed = atom({ plugin: 'aib-boards', key: 'mailDismissed' } as const, [] as string[])
const blockSeen = atom({ plugin: 'aib-boards', key: 'blockSeen' } as const, '')

let seat: SeatInfo | undefined
let probeWhy = ''
let ctxPercent: number | null = null
let warnedHandoff = false
let toldHandoffDone = false
let tick: Timer | undefined
// 新信卡片:開席時已經在的未讀不做卡(只做之後來的);人按過 ✕ 的記在 mailDismissed
let mailBaseline: Set<string> | null = null
let dmDir = ''

// 席位檔 mod.enabled=false(依角色關)⇒ 不寫系統提示、狀態列只留一句
const modOn = () => seat?.mod?.enabled !== false
const isRealCaptain = () => !!seat?.code && seat.code === seat.captain
const isRealIntegrator = () => seat?.role === 'integrator'
const isRealDev = () => seat?.role === 'dev'
const isRealQa = () => seat?.role === 'qa' || seat?.role === 'acceptance'

async function py($: EngineInterface): Promise<string> { return (await $.env.get('EZ2_PY3')) ?? 'python3' }
async function probe($: EngineInterface): Promise<SeatInfo | undefined> {
  const uuid = await $.session.id().catch(() => '')
  const flags = [...(isRealCaptain() ? ['--fleet'] : []), ...(isRealIntegrator() ? ['--integrator'] : []), ...(isRealDev() ? ['--dev'] : []), ...(isRealQa() ? ['--qa'] : [])]
  const r = await $.process.run([await py($), '-I', `${$.plugin.root}/bin/seat-info.py`, uuid, ...flags], { timeoutMs: 25_000 }).catch((err: unknown) => {
    probeWhy = tr('seat_probe_spawn', { err: String(err).slice(0, 80) }); return undefined })
  if (!r) return undefined
  if (r.exitCode !== 0 || !r.stdout.trim()) { probeWhy = `rc=${r.exitCode} ${(r.stderr ?? '').trim().split('\n').pop()?.slice(0, 80) ?? ''}`; return undefined }
  try { probeWhy = ''; return JSON.parse(r.stdout) as SeatInfo } catch { probeWhy = tr('seat_probe_json'); return undefined }
}
async function readCtx($: EngineInterface): Promise<number | null> {
  const u = await $.session.usage().catch(() => undefined)
  const p = u?.context?.percent
  return typeof p === 'number' ? p : null
}
async function dmDirOf($: EngineInterface): Promise<string> {
  if (dmDir) return dmDir
  const r = await $.process.run([await py($), '-I', `${$.plugin.root}/bin/seat-info.py`, '--dm-dir'], { timeoutMs: 5000 }).catch(() => null)
  dmDir = r?.exitCode === 0 ? r.stdout.trim() : ''
  return dmDir
}
async function readQ($: EngineInterface, path: string): Promise<QItem | null> {
  try { return JSON.parse(await $.fs.read(path)) as QItem } catch { return null }
}
// 一份信件檔 → 卡片(長訊落檔的讀全文,最多 4000 字)→ 進 atom
async function takeMail($: EngineInterface, q: QItem): Promise<void> {
  const me = seat?.code ?? null
  if (q.seat && me && q.seat !== me) return
  if (mailBaseline?.has(q.id ?? '') && !(await read($, mailCards)).some(c => c.id === q.id)) return
  const rp = relayPath(String(q.msg ?? ''), seat?.home ?? null)
  const full = rp ? await $.fs.read(rp).then(t => t.slice(0, 4000)).catch(() => null) : null
  const c = cardOf(q, me, full); if (!c) return
  const gone = new Set(await read($, mailDismissed))
  await update($, mailCards, list => upsert(list, c, gone))
  $.ui.invalidate('ui.render')
}
async function refresh($: EngineInterface): Promise<void> {
  await ready($)
  const [s, pct] = await Promise.all([probe($), readCtx($)])
  ctxPercent = pct
  if (!s) { $.ui.status(tr('seat_st_na', { context: pct === null ? '' : ` · CTX ${Math.round(pct)}%` })); return }
  seat = s
  if (!modOn()) { $.ui.status(tr('seat_st_off', { role: s.role ?? tr('seat_unbound'), why: s.mod?.why ?? '' })); return }
  $.ui.status(statusLabel(s, pct))
  // 新信卡片的後備(監看沒登記上時):照未讀清單補卡;已有的卡重讀一次信件檔更新狀態(已讀 / 完成)
  if (mailBaseline === null) mailBaseline = new Set((s.unread_list ?? []).map(m => m.id))
  const dir = await dmDirOf($)
  if (dir) {
    const ids = new Set([...(s.unread_list ?? []).map(m => m.id), ...(await read($, mailCards)).map(c => c.id)])
    for (const id of ids) { const q = await readQ($, `${dir}/${id}.json`); if (q) await takeMail($, q) }
  }
  // 線上好友申請(只有艦長席的探測會帶):新的做卡、已處理的拿掉
  const gone = new Set(await read($, mailDismissed)), before = (await read($, mailCards)).map(c => c.id).join()
  await update($, mailCards, list => syncRequests(list, s.friend_requests ?? [], gone))
  if ((await read($, mailCards)).map(c => c.id).join() !== before) $.ui.invalidate('ui.render')
  const fresh = newBlocks(s.blocks, await read($, blockSeen), Date.now())
  for (const b of fresh) $.ui.toast(tr('seat_toast_block', { label: label(b.gate), why: b.why }), { timeoutMs: 8000 })
  if (fresh.length) await update($, blockSeen, () => fresh[fresh.length - 1]!.ts)
  const hs = handoffState(s, pct)
  if (hs === 'due' && !warnedHandoff) {
    warnedHandoff = true
    $.ui.toast(tr('seat_toast_due', { pct: Math.round(pct ?? 0) }), { timeoutMs: 12000 })
  }
  if (hs === 'done' && !toldHandoffDone) {
    toldHandoffDone = true
    $.ui.toast(tr('seat_toast_done', { at: s.handoff?.at ?? '?' }), { timeoutMs: 10000 })
  }
}

// 開席(session.start)與信件檔異動(classic.FileChanged)由 register.tsx 現有的那一個 handler 呼叫 ——
// 同一個事件不能登記兩次;信件資料夾由 classic.SessionStart 一起登記監看
export async function seatOnStart($: EngineInterface): Promise<void> {
  warnedHandoff = false; toldHandoffDone = false; ctxPercent = null
  await $.command.register({ name: 'aib', description: 'Ez2 AIB seat: /aib room opens the captain-room web page' })
  await refresh($)
  if (seat?.seat_file_has_uuid === false && modOn()) $.ui.toast(tr('seat_toast_no_uuid'), { timeoutMs: 10000 })
  tick?.cancel()   // /clear 會再跑一次 session.start:舊計時器先停,不疊兩個
  tick = $.clock.every(REFRESH_MS, () => { void refresh($) })
}
export async function seatWatchDir($: EngineInterface): Promise<string> { return dmDirOf($) }
export async function seatOnFileChanged($: EngineInterface, path: string, event: string): Promise<void> {
  if (!dmDir || !path.startsWith(dmDir + '/') || !/\/q_[^/]+\.json$/.test(path) || event === 'unlink') return
  const q = await readQ($, path); if (q) await takeMail($, q)
}

export const registerSeat: Register = on => {
  on('command.run', { command: 'aib' }, async ($, e) => {
    const arg = e.args.trim()
    if (arg === 'room') {
      await ready($)
      // 探測沒回來(seat 空)不能說「開發中」:當作裝了,走「還沒跑過」那句
      return { text: await openRoom($, seat ? seat.room_installed : true, seat?.room) }
    }
    await ready($)
    return { text: tr('seat_cmd_usage') }
  })

  on('prompt.compose', async ($, e, next) => {
    const r = await next(e)
    if (seat && !modOn()) return r
    await ready($)
    if (!seat) return { sections: [...r.sections, { id: 'aib-seat', scope: 'session' as const,
      text: tr('seat_p_noseat', { src: 'aib-boards', why: probeWhy ? tr('seat_paren', { value: probeWhy }) : '' }) }] }
    ctxPercent = await readCtx($)
    let text = promptSection(seat, ctxPercent)
    if (isRealCaptain() && seat.fleet?.length) text += '\n' + fleetPromptLines(seat.fleet, fleetAlerts(seat.fleet, seat.code)).join('\n')
    if (isRealIntegrator() && seat.integ) text += '\n' + integPromptLines(seat.integ, integAlerts(seat.integ, seat.code, Date.now())).join('\n')
    if (isRealDev() && seat.dev) text += '\n' + devPromptLines(seat.dev, devAlerts(seat.dev)).join('\n')
    if (isRealQa() && seat.qa) text += '\n' + qaPromptLines(seat.qa, qaAlerts(seat.qa)).join('\n')
    return { sections: [...r.sections, { id: 'aib-seat', text, scope: 'session' as const }] }
  }).catch(($, e, next) => (next.called ? next(e) : next(e)))

  // 新信卡片(輸入框上方):只有真人來信與好友申請;狀態色外框、標題列角色圖示、收起兩行可展開、按 ✕ 才關
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const cards = await read($, mailCards)
    if (e.props.hasSurvey || !cards.length) return next(e)
    await ready($)
    const { Box, Text, Button } = $.ui.resolve(e)
    const W = Math.max(30, e.props.bodyColumns)
    const close = (id: string) => async () => { await update($, mailDismissed, l => [...l.filter(x => x !== id), id].slice(-200)); await update($, mailCards, l => l.filter(x => x.id !== id)); $.ui.invalidate('ui.render') }
    const flip = (id: string) => async () => { await update($, mailCards, l => l.map(x => (x.id === id ? { ...x, open: !x.open } : x))); $.ui.invalidate('ui.render') }
    const closeAll = async () => { const ids = cards.map(c => c.id); await update($, mailDismissed, l => [...l, ...ids].slice(-200)); await update($, mailCards, () => []); $.ui.invalidate('ui.render') }
    const hm = (t: number) => { if (!t) return '?'; const d = new Date(t * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
    return (
      <Box flexDirection="column" width={W}>
        <Box flexDirection="row" justifyContent="space-between"><Text bold>{tr('mail_band', { n: cards.length })}<Text dimColor>{tr('mail_close_hint')}</Text></Text>
          {cards.length > 1 ? <Button key="mail-x-all" variant="secondary" onPress={closeAll}>{tr('mail_close_all')}</Button> : null}</Box>
        {cards.map(c => {
          const st = STATUS[c.status], req = c.kind === 'request', p = c.from ? seat?.personas?.[c.from] : undefined
          const raw = req ? (c.text || tr('mail_req_no_note')) : c.text.replace(/\s+\n/g, '\n')
          const body = c.open ? { text: raw, cut: false } : clip(raw, Math.max(10, W - 6), 2)
          return (
            <Box key={`mail-${c.id}`} flexDirection="column" borderStyle="round" borderColor={st.color} paddingX={1} width={W}>
              <Box flexDirection="row" justifyContent="space-between">
                <Text wrap="truncate-end"><Text>{iconOf(c, seat?.marks)} </Text><Text bold>{p?.name ?? c.label ?? c.from ?? '?'}</Text>
                  <Text dimColor>{req ? ` ${tr('mail_req_title')}` : c.from ? ` @${c.from}` : ''} · {hm(c.at)}  </Text><Text color={st.color}>● {tr(st.key)}</Text></Text>
                <Box flexDirection="row" gap={1}>
                  {body.cut || c.open ? <Button key={`mail-open-${c.id}`} variant="secondary" onPress={flip(c.id)}>{c.open ? tr('collapse') : tr('expand')}</Button> : null}
                  <Button key={`mail-x-${c.id}`} variant="secondary" onPress={close(c.id)}>✕</Button>
                </Box>
              </Box>
              <Text wrap="wrap" dimColor={req && !c.text}>{body.text}</Text>
              {req ? <Text dimColor wrap="truncate-end">{tr('mail_req_hint', { owner: c.id.slice(REQ_PREFIX.length) })}</Text> : null}
            </Box>)
        })}
      </Box>)
  }).catch(($, e, next) => (next.called ? next(e) : next(e)))
}
