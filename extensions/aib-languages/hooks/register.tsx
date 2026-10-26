// Ez2 AIB 切換語言(10-14,the maintainer「所有介面、按鈕、mod、顯示、對話都要對應到」;設計 docs/DESIGN-aib-languages-04e0a-1014.md §4)。
// 真相在設定檔(aib lang 寫);這個 mod 做四件事:
//   1. /aib-languages 開選單(四個語言各用自己的寫法;範圍:這台全部艙 / 只這一艙)→ 跑 aib lang → toast。
//   2. 本席行程 $.env.set("EZ2_AIB_LOCALE"):掛勾子行程(aib-now-gate、claim-gate…)立刻跟。
//   3. 每 5 秒看設定檔 mtime:別席 / 艦長室切了,本席 env、狀態列、別的 mod 的字跟上(bundle 快取以語系為鍵)。
//   4. prompt.compose 每次請求加「回答語言」段(session scope):切完**下一句**就換,不用 /clear,蓋過人格包舊行。
import { atom, read, update } from 'claude-code'
import type { Register, EngineInterface } from 'claude-code'
import type { LangStatus, Scope } from '../types'
import { LOCALES, SELF_NAMES, normalize, cached, remember, forget, makeT } from './i18n'
import type { Loc } from './i18n'

const PANE = 'aib-languages'
const NO_AIB_FALLBACK = 'AIB notice · Cannot find the aib command (EZ2_AIB_ROOT unset and no ~/ez2-ai-b)'
const PREFIX = 'mod.aib_languages'
const POLL_MS = 5000
// 初始值要是物件:引擎用 WeakMap 記版本,null 當鍵會炸(kit 二分 10-14 實測);locale 空 = 還沒讀到
const EMPTY: LangStatus = { locale: '', source: 'default', choices: [], files: { user: '' } }
const status = atom({ plugin: 'aib-languages', key: 'status' } as const, EMPTY)
const scope = atom({ plugin: 'aib-languages', key: 'scope' } as const, 'machine' as Scope)
let poll: (() => void) | undefined          // $.clock.every 回的是「取消」函式
let seen: Record<string, number> = {}     // 檔路徑 → 上次看到的 mtimeMs(模組變數:重載後第一輪重讀一次,無妨)

/** aib 倉的根:$EZ2_AIB_ROOT / $AIB_SOURCE_ROOT → ~/ez2-ai-b。回 null = 找不到(呼叫端要說出來,不猜) */
async function repoRoot($: EngineInterface): Promise<string | null> {
  // 驗證器要 $.env.get 的名字是字面值(它列得出模組讀寫哪些變數),所以不用迴圈
  const explicit = (await $.env.get('EZ2_AIB_ROOT').catch(() => undefined)) || (await $.env.get('AIB_SOURCE_ROOT').catch(() => undefined))
  if (explicit && (await $.fs.exists(`${explicit}/src/cockpit/i18n_lib.py`).catch(() => false))) return explicit
  const home = (await $.env.get('HOME').catch(() => undefined)) ?? ''
  const guess = `${home}/ez2-ai-b`
  if (home && (await $.fs.exists(`${guess}/src/cockpit/i18n_lib.py`).catch(() => false))) return guess
  return null
}

/** 讀一個 mod 的整組字(設計 §5):同語系第二次起走快取 */
async function bundle($: EngineInterface, prefix: string, loc: Loc): Promise<Record<string, string>> {
  const hit = cached(prefix, loc)
  if (hit) return hit
  const root = await repoRoot($)
  if (!root) return {}
  const py = (await $.env.get('EZ2_PY3').catch(() => undefined)) ?? 'python3'
  const r = await $.process.run([py, '-I', `${root}/src/cockpit/i18n_lib.py`, '--locale', loc, '--prefix', prefix, '--json'], { timeoutMs: 10_000 }).catch(() => null)
  if (!r || r.exitCode !== 0) return {}
  let data: Record<string, string> = {}
  try { data = JSON.parse(r.stdout.trim().split('\n').pop() || '{}') } catch { data = {} }
  remember(prefix, loc, data)
  return data
}

async function aibBin($: EngineInterface): Promise<string | null> {
  const root = await repoRoot($)
  return root ? `${root}/src/cockpit/aib` : null
}

async function readStatus($: EngineInterface, args: string[] = []): Promise<LangStatus> {
  const bin = await aibBin($)
  if (!bin) throw new Error('no_aib')
  const r = await $.process.run([bin, 'lang', ...args, '--json'], { timeoutMs: 60_000 })
  const last = r.stdout.trim().split('\n').pop() || ''
  if (r.exitCode !== 0 || !last.startsWith('{')) throw new Error((r.stderr || r.stdout).trim().split('\n').pop() || `rc=${r.exitCode}`)
  return JSON.parse(last) as LangStatus
}

async function tFor($: EngineInterface, loc: Loc) { return makeT(PREFIX, await bundle($, PREFIX, loc)) }

/** 把新語系套到本席:env、狀態列、重畫 */
async function apply($: EngineInterface, st: LangStatus, announce: boolean) {
  const loc = normalize(st.locale) ?? 'zh-TW'
  const prev = (await read($, status)).locale
  await update($, status, () => st)
  await $.env.set('EZ2_AIB_LOCALE', loc)
  if (prev !== st.locale) forget()
  const t = await tFor($, loc)
  $.ui.status(t('status', { value: loc }))
  if (announce && prev !== st.locale) $.ui.toast(t('switched', { value: SELF_NAMES[loc] }))
  $.ui.invalidate('ui.render')
}

async function checkFiles($: EngineInterface) {
  const st = await read($, status)
  if (!st.locale) return
  let changed = false
  for (const p of Object.values(st.files)) {
    if (!p) continue
    const m = await $.fs.stat(p).then(s => s.mtimeMs).catch(() => -1)     // -1 = 檔不在(被 --clear 掉也算變)
    if (seen[p] !== undefined && seen[p] !== m) changed = true
    seen[p] = m
  }
  if (!changed) return
  const fresh = await readStatus($).catch(() => null)
  if (fresh && fresh.locale !== st.locale) await apply($, fresh, true)
}

async function switchTo($: EngineInterface, loc: Loc, sc: Scope): Promise<string | null> {
  try {
    const st = await readStatus($, [loc, ...(sc === 'cabin' ? ['--cabin'] : [])])
    for (const p of Object.values(st.files)) if (p) seen[p] = await $.fs.stat(p).then(s => s.mtimeMs).catch(() => -1)
    await apply($, st, true)
    return null
  } catch (err) {
    return String((err as Error).message ?? err)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'aib-languages', description: 'Switch the AIB language (zh-TW / en / ja / ko)' })
    const st = await readStatus($).catch(() => null)
    if (st) {
      await apply($, st, false)
      for (const p of Object.values(st.files)) if (p) seen[p] = await $.fs.stat(p).then(s => s.mtimeMs).catch(() => -1)
    }
    poll?.()
    poll = $.clock.every(POLL_MS, () => { void checkFiles($) })
    return next(e)
  })

  on('command.run', { command: 'aib-languages' }, async ($, e) => {
    const cur = normalize((await read($, status)).locale) ?? 'zh-TW'
    const t = await tFor($, cur)
    const want = normalize(e.args.trim().split(/\s+/)[0])
    if (want) {                                   // /aib-languages en:直接切,不開框
      const sc: Scope = /--cabin\b/.test(e.args) ? 'cabin' : await read($, scope)
      const err = await switchTo($, want, sc)
      if (err === 'no_aib') { const m = t('no_aib'); return { text: m.startsWith('!!') ? NO_AIB_FALLBACK : m } }   // 找不到倉 ⇒ 字典也讀不到 ⇒ 英文保底
      const t2 = await tFor($, want)
      return { text: err ? t2('failed', { what: err }) : t2('switched', { value: SELF_NAMES[want] }) }
    }
    const opened = await $.ui.open({ id: PANE, title: t('title'), focus: true, closeOnEscape: true, rows: 7 })
    return { text: opened.isPlaced ? t('title') : t('failed', { what: 'pane' }) }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const st = await read($, status)
    const cur = normalize(st.locale) ?? 'zh-TW'
    const sc = await read($, scope)
    const t = await tFor($, cur)
    const hasCabin = !!st.files.cabin
    const scopeLabel = t(sc === 'cabin' && hasCabin ? 'scope_cabin' : 'scope_machine')
    return (
      <Box flexDirection="column">
        <Text>{t('current', { value: cur, what: t('source_' + st.source) })}</Text>
        <Box>
          {LOCALES.map((loc, i) => (
            <Button key={`loc-${loc}`} hotkey={String(i + 1)} label={`${i + 1} ${SELF_NAMES[loc]}${loc === cur ? ' ✓' : ''}`}
              onPress={async () => { const err = await switchTo($, loc, await read($, scope)); if (err) { const t2 = await tFor($, cur); $.ui.toast(err === 'no_aib' ? (t2('no_aib').startsWith('!!') ? NO_AIB_FALLBACK : t2('no_aib')) : t2('failed', { what: err })) } else await $.ui.close({ id: PANE }) }} />
          ))}
        </Box>
        <Box>
          {hasCabin && <Button key="scope" hotkey="s" label={`s ${scopeLabel}`} onPress={() => update($, scope, v => (v === 'cabin' ? 'machine' : 'cabin'))} />}
          <Text dimColor> {t('hint', { what: scopeLabel })}</Text>
        </Box>
      </Box>
    )
  })

  on('prompt.compose', async ($, e, next) => {
    const r = await next(e)
    const st = await read($, status)
    const loc = normalize(st.locale || (await $.env.get('EZ2_AIB_LOCALE').catch(() => undefined))) ?? 'zh-TW'
    const t = await tFor($, loc)
    const text = `${t('reply_line', { value: `${SELF_NAMES[loc]} (${loc})` })} Reply language: ${SELF_NAMES[loc]} (${loc}); this AIB language setting overrides the persona card or any earlier pack. Answer in this language.`
    return { sections: [...r.sections.filter(s => s.id !== 'aib-languages:reply-language'), { id: 'aib-languages:reply-language', text, scope: 'session' }] }
  })
}
