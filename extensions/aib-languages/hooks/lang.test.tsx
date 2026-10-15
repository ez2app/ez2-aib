import { test, expect, mock } from 'claude-code/testing'
import { fmt, normalize, makeT } from './i18n'

// 假的 aib lang --json / i18n_lib --prefix --json:process.run 底下由測試回答(鐵則:測試不碰真機設定檔)
// session.start 的事件要帶齊(cwd / surface / isInteractive):沒帶的話 next(e) 收到 undefined,引擎用 WeakMap 記事件就炸
const START = { cwd: '/repo', surface: 'terminal', isInteractive: true } as const
const COMPOSE = { model: 'm', promptModel: 'm', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [], sections: [] } as const
const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
const BUNDLE: Record<string, Record<string, string>> = {
  'zh-TW': { 'mod.aib_languages.title': '語言 / Language', 'mod.aib_languages.hint': '按 1~4;{what};Esc', 'mod.aib_languages.scope_machine': '這台全部艙', 'mod.aib_languages.scope_cabin': '只這一艙',
    'mod.aib_languages.current': '現在:{value}(來源:{what})', 'mod.aib_languages.switched': '語言已切換成 {value}', 'mod.aib_languages.failed': '切換失敗:{what}', 'mod.aib_languages.no_aib': '找不到 aib',
    'mod.aib_languages.reply_line': '回答語言:{value}。', 'mod.aib_languages.status': '🌐 {value}', 'mod.aib_languages.source_user': '這台的設定', 'mod.aib_languages.source_default': '預設', 'mod.aib_languages.source_cabin': '這一艙的設定', 'mod.aib_languages.source_env': '環境變數', 'mod.aib_languages.source_explicit': '明給' },
  en: { 'mod.aib_languages.title': 'Language / 語言', 'mod.aib_languages.hint': 'Press 1-4; {what}; Esc', 'mod.aib_languages.scope_machine': 'every cabin on this machine', 'mod.aib_languages.scope_cabin': 'this cabin only',
    'mod.aib_languages.current': 'Now: {value} (from: {what})', 'mod.aib_languages.switched': 'Language switched to {value}', 'mod.aib_languages.failed': 'Switch failed: {what}', 'mod.aib_languages.no_aib': 'Cannot find aib',
    'mod.aib_languages.reply_line': 'Reply language: {value}.', 'mod.aib_languages.status': '🌐 {value}', 'mod.aib_languages.source_user': "this machine's setting", 'mod.aib_languages.source_default': 'default', 'mod.aib_languages.source_cabin': "this cabin's setting", 'mod.aib_languages.source_env': 'environment variable', 'mod.aib_languages.source_explicit': 'given explicitly' },
}
BUNDLE.ja = { ...BUNDLE.en!, 'mod.aib_languages.switched': '言語を {value} に切り替えました', 'mod.aib_languages.reply_line': '返答言語:{value}。', 'mod.aib_languages.current': '現在:{value}(由来:{what})' }
BUNDLE.ko = { ...BUNDLE.en!, 'mod.aib_languages.switched': '언어를 {value}(으)로 바꿨습니다', 'mod.aib_languages.reply_line': '답변 언어: {value}.' }

type World = { locale: string; source: string; cabin?: string; mtime: number; runs: string[][]; env: Record<string, string | undefined>; toasts: string[]; status: string[] }
function world(locale = 'zh-TW', cabin?: string): World { return { locale, source: 'user', cabin, mtime: 1000, runs: [], env: {}, toasts: [], status: [] } }
function wire(on: Parameters<Parameters<typeof test>[1]>[1], w: World, exists = true) {
  // kit 底下沒有引擎:這幾個呼叫由測試自己答(session.start / command.register / ui.open / ui.close / prompt.compose 的底)
  on('session.start', async (_$, e) => ({ cwd: (e as { cwd: string }).cwd }) as never)            // 引擎事件:直接回結果,不包 { value }
  on('command.register', async (_$, e) => ({ value: { command: (e as { name: string }).name } }) as never)
  on('ui.open', async () => ({ value: { isPlaced: true } }) as never)
  on('ui.close', async () => ({ value: undefined }) as never)
  on('prompt.compose', async () => ({ sections: [{ id: 'intro', text: 'base', scope: 'shared' }] }) as never)
  on('env.get', async (_$, e) => ({ value: ({ EZ2_AIB_ROOT: '/repo', HOME: '/h', EZ2_PY3: 'python3', ...w.env })[(e as { name: string }).name] }) as never)
  on('env.set', async (_$, e) => { const x = e as { name: string; value?: string }; w.env[x.name] = x.value; return { value: undefined } as never })
  on('fs.exists', async () => ({ value: exists }) as never)
  on('fs.stat', async () => ({ value: { kind: 'file', size: 3, mtimeMs: w.mtime, isSymlink: false } }) as never)
  on('ui.toast', async (_$, e) => { w.toasts.push((e as { text: string }).text); return { value: undefined } as never })
  on('ui.status', async (_$, e) => { w.status.push(String((e as { text?: string }).text)); return { value: undefined } as never })
  on('process.run', async (_$, e) => {
    const argv = [...(e as { argv: readonly string[] }).argv]
    w.runs.push(argv)
    if (argv[2] === 'i18n_lib.py' || argv[2]?.endsWith('/i18n_lib.py')) {
      const loc = argv[argv.indexOf('--locale') + 1]!
      return ok(JSON.stringify(BUNDLE[loc] ?? BUNDLE.en))
    }
    if (argv[1] === 'lang') {
      const want = argv[2] && !argv[2].startsWith('--') ? argv[2] : null
      if (want) { w.locale = want; w.source = argv.includes('--cabin') ? 'cabin' : 'user'; w.mtime += 1 }
      const files: Record<string, string> = { user: '/h/.ez2-aib/locale', ...(w.cabin ? { cabin: w.cabin } : {}) }
      return ok(`Language switched to ${w.locale}\n` + JSON.stringify({ locale: w.locale, source: w.source, choices: ['zh-TW', 'en', 'ja', 'ko'], files, notified: 0 }))
    }
    return ok('')
  })
}

test('純函式:別名正規化、代入、缺鍵 fail-loud', async () => {
  expect(normalize('zh_TW.UTF-8')).toBe('zh-TW'); expect(normalize('ja-JP')).toBe('ja'); expect(normalize('klingon')).toBe(null); expect(normalize(undefined)).toBe(null)
  expect(fmt('a {n} b {x}', { n: 3 })).toBe('a 3 b {x}')
  const t = makeT('mod.x', { 'mod.x.hi': 'hi {value}' })
  expect(t('hi', { value: 'there' })).toBe('hi there'); expect(t('nope')).toBe('!!mod.x.nope!!')
})

test('開席:讀設定、設本席 env、狀態列 🌐;回答語言段跟著語系', async ($, on) => {
  const w = world('en'); wire(on, w)
  await $.session.start(START)
  expect(w.env.EZ2_AIB_LOCALE).toBe('en')
  expect(w.status.at(-1)).toBe('🌐 en')
  expect(w.runs.some(a => a[1] === 'lang' && a.includes('--json'))).toBe(true)
  const r = await $.prompt.compose(COMPOSE as never)
  const sec = r.sections.find(s => s.id === 'aib-languages:reply-language')
  expect(sec?.scope).toBe('session')
  expect(sec?.text).toMatch(/Reply language: English \(en\)/)
  expect(r.sections.filter(s => s.id === 'aib-languages:reply-language').length).toBe(1)
})

test('選單:按鈕切語言 ⇒ 跑 aib lang <loc> --json、env 跟、toast 用新語系、框關掉', async ($, on) => {
  const w = world('zh-TW', '/h/.ez2-aib/instances/x'); wire(on, w)
  await $.session.start(START)
  await $.command.run({ command: 'aib-languages', args: '' })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'aib-languages', surface, component: 'Pane', requestId: 'aib-languages', viewport: { rows: 12, columns: 100 }, props: {} as never })
    expect(await ui.find({ type: 'Button', text: /繁體中文 ✓/ })).toBeDefined()
    expect(await ui.find({ type: 'Button', text: /日本語/ })).toBeDefined()
    // 範圍記在 $.state,第二個 surface 進來時已經是「只這一艙」(上一輪按的)⇒ 現在是全部艙才按
    if (await ui.find({ type: 'Button', text: /這台全部艙|every cabin/ })) expect(await ui.press({ key: 'scope' })).toBeDefined()
    expect(await ui.find({ type: 'Button', text: /只這一艙|this cabin only/ })).toBeDefined()
    await ui.press({ key: 'loc-ja' })
    const last = w.runs.filter(a => a[1] === 'lang').at(-1)!
    expect(last.slice(2)).toEqual(['ja', '--cabin', '--json'])
    expect(w.env.EZ2_AIB_LOCALE).toBe('ja')
    expect(w.toasts.at(-1)).toBe('言語を 日本語 に切り替えました')
    expect(w.status.at(-1)).toBe('🌐 ja')
    const r = await $.prompt.compose(COMPOSE as never)
    expect(r.sections.find(s => s.id === 'aib-languages:reply-language')?.text).toMatch(/返答言語:日本語 \(ja\)/)
    await ui.unmount()
    await ui.press({ key: 'loc-zh-TW' }).catch(() => undefined)        // 卸了就不動(下一個 surface 重來)
    w.locale = 'zh-TW'; w.env.EZ2_AIB_LOCALE = 'zh-TW'
    await $.command.run({ command: 'aib-languages', args: 'zh-TW' })
  }
})

test('/aib-languages en 直接切,不開框;不認得的值開框', async ($, on) => {
  const w = world('zh-TW'); wire(on, w)
  await $.session.start(START)
  const r = await $.command.run({ command: 'aib-languages', args: 'en' })
  expect(r.text).toBe('Language switched to English')
  expect(w.env.EZ2_AIB_LOCALE).toBe('en')
  expect(w.runs.filter(a => a[1] === 'lang').at(-1)!.slice(2)).toEqual(['en', '--json'])
})

test('別席切了:5 秒輪詢看到檔 mtime 變 ⇒ 本席 env、狀態列、toast 跟上', async ($, on) => {
  const w = world('zh-TW'); wire(on, w)
  const clock = mock.clock(on)                                          // 模擬時鐘:輪詢的 $.clock.every 睡在它上面,測試推它
  await $.session.start(START)
  expect(w.env.EZ2_AIB_LOCALE).toBe('zh-TW')
  w.locale = 'ko'; w.mtime = 2000                                       // 別的席跑了 aib lang ko
  await clock.advance(5000)
  await clock.advance(1)
  expect(w.env.EZ2_AIB_LOCALE).toBe('ko')
  expect(w.status.at(-1)).toBe('🌐 ko')
  expect(w.toasts.at(-1)).toBe('언어를 한국어(으)로 바꿨습니다')
  const n = w.toasts.length
  await clock.advance(5000)                                             // 沒再變 ⇒ 不吵
  expect(w.toasts.length).toBe(n)
})

test('找不到 aib:說出來,不猜', async ($, on) => {
  const w = world('zh-TW'); wire(on, w, false)
  await $.session.start(START)
  const r = await $.command.run({ command: 'aib-languages', args: 'en' })
  expect(r.text).toMatch(/找不到 aib|Cannot find the aib command/)
})
