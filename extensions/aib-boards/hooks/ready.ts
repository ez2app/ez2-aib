// 語系 + 字典(docs/DESIGN-aib-languages-04e0a-1014.md §5),register.tsx 與 seatcore.tsx 共用:驗證器只准把 $ 傳給本檔頂層
// 宣告的函式或從外掛自己的檔匯入的函式 ⇒ 讀語系 / 字典放這一檔,兩邊都 import。
import type { EngineInterface } from 'claude-code'
import { normalize, cached, remember, makeT, useT, tr as t_ } from './i18n'
import type { Loc } from './i18n'

export const PREFIX = 'mod.aib_boards'
export async function repoRoot($: EngineInterface): Promise<string | null> {
  // 驗證器要 $.env.get 的名字是字面值,所以不用迴圈(同 aib-languages)
  const explicit = (await $.env.get('EZ2_AIB_ROOT').catch(() => undefined)) || (await $.env.get('AIB_SOURCE_ROOT').catch(() => undefined))
  if (explicit && (await $.fs.exists(`${explicit}/src/cockpit/i18n_lib.py`).catch(() => false))) return explicit
  const home = (await $.env.get('HOME').catch(() => undefined)) ?? ''
  const guess = `${home}/ez2-ai-b`
  if (home && (await $.fs.exists(`${guess}/src/cockpit/i18n_lib.py`).catch(() => false))) return guess
  return null
}
let bundleFailAt = 0   // 讀字典失敗:30 秒內不重試(畫面 0.2 秒重畫一次,不能每格起一支 Python)
async function bundle($: EngineInterface, loc: Loc): Promise<Record<string, string>> {
  const hit = cached(PREFIX, loc)
  if (hit) return hit
  if (Date.now() - bundleFailAt < 30_000) return {}
  const root = await repoRoot($)
  const py = (await $.env.get('EZ2_PY3').catch(() => undefined)) ?? 'python3'
  const r = root ? await $.process.run([py, '-I', `${root}/src/cockpit/i18n_lib.py`, '--locale', loc, '--prefix', PREFIX, '--json'], { timeoutMs: 10_000 }).catch(() => null) : null
  let data: Record<string, string> | null = null
  try { if (r && r.exitCode === 0) data = JSON.parse(r.stdout.trim().split('\n').pop() || '{}') as Record<string, string> } catch { data = null }
  if (!data) { bundleFailAt = Date.now(); return {} }
  remember(PREFIX, loc, data)
  return data
}
// 沒有 aib-languages(env 沒設)⇒ 問 i18n_lib.py --resolve(同 tui 的單一順位,比起 aib lang 便宜:不起 bash 分派);結果留 10 秒,
//   同一時間只問一次(畫面 0.2 秒重畫一次,問的時候別的重畫拿上一次的答案,沒有答案就先用 zh-TW)
let askedLoc: { loc: Loc; at: number } | null = null, asking: Promise<Loc> | null = null, resolveFailAt = -Infinity   // 問失敗後 5 秒內不重問(畫面一直重畫,不能每格起一支)
async function localeOf($: EngineInterface): Promise<Loc> {
  const env = normalize(await $.env.get('EZ2_AIB_LOCALE').catch(() => undefined))
  if (env) return env
  const nowMs = await $.clock.now().catch(() => Date.now())   // 用引擎的鐘(測試可推),不用 Date.now
  if (askedLoc && nowMs - askedLoc.at < 10_000) return askedLoc.loc
  if (asking) return askedLoc?.loc ?? asking
  if (nowMs - resolveFailAt < 5_000) return askedLoc?.loc ?? 'zh-TW'
  asking = (async () => {
    const root = await repoRoot($)
    const py = (await $.env.get('EZ2_PY3').catch(() => undefined)) ?? 'python3'
    const r = root ? await $.process.run([py, '-I', `${root}/src/cockpit/i18n_lib.py`, '--resolve'], { timeoutMs: 10_000 }).catch(() => null) : null
    let loc: Loc | null = null
    try { if (r && r.exitCode === 0) loc = normalize((JSON.parse(r.stdout.trim().split('\n').pop() || '{}') as { locale?: string }).locale) } catch { loc = null }
    // 問失敗 / 逾時:沿用上一次問到的(英文畫面不會因為一次失敗閃回中文),也不記快取 ⇒ 下一次重畫再問;從沒問到過才用 zh-TW
    if (!loc) { resolveFailAt = nowMs; return askedLoc?.loc ?? 'zh-TW' }
    askedLoc = { loc, at: nowMs }
    return loc
  })().finally(() => { asking = null })
  return asking
}
/** 這一輪要用的字:讀語系 + 字典,換給純檔,回 t */
export async function ready($: EngineInterface) {
  const loc = await localeOf($)
  useT(loc, makeT(PREFIX, await bundle($, loc)))
  return { t: t_, loc }
}

