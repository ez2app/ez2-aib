import { test, expect } from 'claude-code/testing'
import { FIXTURE } from './i18n-fixture'
import { useT, makeT, tr } from './i18n'
// 純檔的字走 tr();這一檔的斷言是中文原字 ⇒ 先換成 zh-TW 字典(register.tsx 平常做這件事)
useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!))
import { mailToast, isFreshMail, hexValues, knowBlips, todoParts, knowLine, isHumanTyped, groupTodos, levelColor, isStale, parseScreen, TABS } from './lib'
import { split41, withLogo, logoSegs, logoRows, bumpUsage, effectiveEx, foldOrder, USAGE_OPEN, roleFlow, themeOf } from './lib'
import { shadowRows, stackCols, dispW, commitCurve } from './engcharts'

test('五個分頁,順序固定', () => { expect(TABS.map(t => t.label)).toEqual(['今天', '專案', '對話', '圖表', '自訂']) })
test('只有真人按 Enter 才開', () => {
  expect(isHumanTyped({ kind: 'composer' })).toBe(true)
  for (const kind of ['plugin', 'peer', 'scheduled-trigger', 'unclassified']) expect(isHumanTyped({ kind })).toBe(false)
})
test('待辦分組:照 🔴⏳🙋👥📝 排,不認得的組放自己記的,空組不出現', () => {
  const g = groupTodos([{ id: 'a', group: 'human', text: 'x', source: 'now' }, { id: 'b', group: 'now', text: 'y', source: 'dm' }, { id: 'c', group: '???', text: 'z', source: 'ledger' }])
  expect(g.map(x => x.id)).toEqual(['now', 'human', 'self'])
  expect(groupTodos([{ id: 'd', group: 'human', text: 'q', source: 'now' }]).map(x => x.id)).toEqual(['human'])
})
test('燈號與過期', () => {
  expect(levelColor('red')).toBe('error'); expect(levelColor('unknown')).toBe('inactive')
  expect(isStale(0, 601)).toBe(true); expect(isStale(0, 599)).toBe(false)
})
test('格式不對就拒,缺欄位補空', () => {
  expect(() => parseScreen('{"schema":"x"}')).toThrow()
  const s = parseScreen('{"schema":"aib-seat-screen/v1","measured_at":1}')
  expect(s.todo).toEqual([]); expect(s.know).toEqual([])
})
test('卡片沒寫 line 就用數字組一行', () => {
  expect(knowLine({ sensor: 'inbox', line: null, data: { unread: 0, open: 1 } })).toBe('信箱:未讀 0、沒結案 1')
  expect(knowLine({ sensor: 'main_head', line: 'abc 修了什麼' })).toBe('主線:abc 修了什麼')
  expect(knowLine({ sensor: 'my_trees', line: 'fix/a', data: { ahead: 3, dirty: 0, trees: [{}, {}] } })).toBe('我的工作樹:2 棵 · 領先主線合計 3 顆 · 沒提交 0 棵 · 最近動的 fix/a')
  expect(knowLine({ sensor: 'xyz' })).toBe('xyz')
})
test('動畫用的資料:紅燈光點最靠中心;圓餅照分組', () => {
  const b = knowBlips([{ sensor: 'a', level: 'ok', line: '' }, { sensor: 'b', level: 'red', line: '' }])
  expect(b[1]!.r).toBeLessThan(b[0]!.r); expect(b[1]!.color).toBe('error')
  expect(todoParts([{ id: '1', group: 'now', text: 'x', source: 'dm' }, { id: '2', group: 'now', text: 'y', source: 'dm' }])).toEqual([{ label: '現在要做', n: 2 }])
})
test('畫面 1:4:面板拿 80%', () => {
  expect(split41(200, 50)).toEqual({ columns: 160, rows: 40 })
  expect(split41(30, 5)).toEqual({ columns: 40, rows: 12 })   // 太小時有下限
})
test('六角寶石六角:跟示範頁同一算法;沒來源的角是 null', () => {
  const r = hexValues({ success: { green: 98, red: 2 }, speed: { median_min: 0, n: 3 }, inbox: { unread: 0 }, handoff: null, collab: { seats: 3 } }, 33)
  expect(r.v).toEqual([0.98, 1, 0.67, 1, null, 0.75])
  expect(r.raw[4]).toBe('沒有交接紀錄')
})
test('新信彈窗只跳真的新信:已讀 / 結案 / 太舊 / 跳過的不跳', () => {
  const now = 1000, seen = new Set<string>()
  expect(isFreshMail({ id: 'a', ts: 990 }, now, seen)).toBe(true)
  expect(isFreshMail({ id: 'b', ts: 990, read_at: 995 }, now, seen)).toBe(false)
  expect(isFreshMail({ id: 'c', ts: 990, done_at: 999 }, now, seen)).toBe(false)
  expect(isFreshMail({ id: 'd', ts: 800 }, now, seen)).toBe(false)
  seen.add('e'); expect(isFreshMail({ id: 'e', ts: 990 }, now, seen)).toBe(false)
})
test('新信彈窗(正向):真的新信跳一次,同一封再改檔不重跳;已讀的不跳', () => {
  const seen = new Set<string>(), now = 1000
  const t1 = mailToast({ id: 'q1', sender_seat: '8f366', seat: '86ff0', msg: '請看 batch20\n第二行', ts: 995 }, now, seen, { code: '86ff0', logos: { '8f366': 'C', '86ff0': 'D' } })
  expect(t1).toMatch(/^📨 C @8f366 → D @86ff0 · \d\d:\d\d\n請看 batch20 第二行$/)   // 誰 DM 誰、時間、內容,兩邊帶標誌
  expect(mailToast({ id: 'q1', sender: '@8f366', msg: '請看 batch20', ts: 995 }, now, seen)).toBeNull()
  expect(mailToast({ id: 'q2', sender: '@a9769', msg: 'x', ts: 995, read_at: 999 }, now, seen)).toBeNull()
})

// §22.4 此刻四小格 + 我的包三關
import { ctxCell, leaseCell, mailCell, myPkgs, pkgCell, pkgStages } from './lib'
test('此刻:CTX 離 85% 多遠,70 黃 85 紅;量不到寫量不到', () => {
  expect(ctxCell(40)).toMatchObject({ text: '40%', sub: '離 85% 還 45', color: 'success' })
  expect(ctxCell(72).color).toBe('warning'); expect(ctxCell(86)).toMatchObject({ color: 'error', sub: '到 85% 了,要交接' })
  expect(ctxCell(null)).toMatchObject({ text: '量不到', color: 'inactive' })
})
test('此刻:機器空著 / 別人在用 / 我在用 / 量不到', () => {
  const k = (data: Record<string, unknown>, level = 'warn') => [{ sensor: 'machine_lease', level, data }] as never
  expect(leaseCell(k({ state: 'free' }, 'ok'), '86ff0', 0)).toMatchObject({ text: '空著', color: 'success' })
  expect(leaseCell(k({ state: 'held', owner: 'ship.py', acquired_epoch: 0, expiry_epoch: 7200 }), '86ff0', 600)).toMatchObject({ text: 'ship.py', color: 'warning' })
  expect(leaseCell(k({ state: 'held', owner: 'aib-test@86ff0' }), '86ff0', 0).text).toBe('我在用')
  expect(leaseCell([], '86ff0', 0).text).toBe('量不到')
})
test('此刻:信箱未讀與最新寄件人(去掉括號裡的名字)', () => {
  const c = mailCell([{ sensor: 'inbox', level: 'warn', data: { unread: 2, open: 3 } }] as never,
    [{ id: 'a', group: 'now', text: 'x', from: '@a9769(工程)', since: 1, source: 'dm' }, { id: 'b', group: 'now', text: 'y', from: '@8f366(第二艦長)', since: 9, source: 'dm' }])
  expect(c).toMatchObject({ text: '未讀 2', sub: '最新 @8f366', color: 'warning' })
})
test('我的包:紅的排最前、上主線的另外數;三關照 stage;沒 stage 是 ?', () => {
  const p = myPkgs([{ sensor: 'my_trees', level: 'ok', data: { trees: [{ branch: 'a', stage: 0 }, { branch: 'b', stage: 2, reds: ['t.py'] }, { branch: 'c', stage: 3 }, { branch: '', stage: 0 }] } }] as never)
  expect(p.open.map(t => t.branch)).toEqual(['b', 'a']); expect(p.landed.length).toBe(1)
  expect(pkgCell(p)).toMatchObject({ text: '紅 1 支', color: 'error' })
  expect(pkgStages({ branch: 'x', stage: 2 }).map(s => s[1])).toEqual([true, true, false])
  expect(pkgStages({ branch: 'x' }).map(s => s[1])).toEqual([null, null, null])
  expect(pkgCell(myPkgs([]))).toMatchObject({ text: '量不到' })
})
import { bar, shortBranch, logRows } from './lib'
test('流程儀表板:條、分支短名、紀錄新的在上且含機器與主線', () => {
  expect(bar(0.5, 4)).toEqual(['██', '░░']); expect(bar(2, 3)).toEqual(['███', '']); expect(bar(-1, 2)).toEqual(['', '░░'])
  expect(shortBranch('feat/seat-screen-mods-86ff0-1014')).toBe('seat-screen-mods-86ff0-1014')
  const r = logRows([{ id: 'a', group: 'now', text: 'x', from: '@8f366(第二艦長)', since: 30, source: 'dm' }, { id: 'n', group: 'doing', text: '在做的事', from: '@86ff0', since: 1, source: 'now' }, { id: 'b', group: 'self', text: 'y', source: 'ledger' }],
    [{ sensor: 'machine_lease', level: 'warn', data: { state: 'held', owner: 'ship.py', acquired_epoch: 20 } }, { sensor: 'main_head', level: 'ok', line: 'subj', data: { sha: 'abc', committed_at: 10 } }] as never,
    [{ at: 40, kind: 'dm_in', who: '@8f366(第二艦長)', text: '機器空了' }, { at: 35, kind: 'commit', who: 'e121084', text: 'fix x' }, { at: 25, kind: 'ship_refuse', who: 'int/b', text: '新紅 2 支' }], 12, 50)
  expect(r.map(x => x.tag)).toEqual(['狀態列', '來信', '提交', '推包被擋', '拿機器', '上主線'])   // 待辦裡的信不重複列;狀態列用最近更新時間
  expect(r[1]!.who).toBe('@8f366'); expect(r[5]!.text).toBe('abc subj')
})
import { fit, sw, plain } from './lib'
test('對齊:寬度不定的字換半形、中文算 2 格、fit 剛好 w 格', () => {
  expect(plain('→ @8f366 … —')).toBe('-> @8f366 ... -')
  expect(sw('寄出ab')).toBe(6)
  for (const s of ['提交', '→ @8f366', 'fix(aib-boards 畫面比例): 對話:面板 15:35 → 1:4(面板拿 80%', '長訊已落檔(698 位元組)', 'a']) for (const w of [5, 9, 16, 40]) expect(sw(fit(s, w))).toBe(w)
})
import { typeIn, reveal } from './lib'
test('紀錄動態:打字一半時真字 + 亂碼剛好同寬;逐行吐、吐完不再打字', () => {
  for (const shown of [0, 5, 13, 40]) { const [a, b] = typeIn('寄出 → 長訊已落檔(698 位元組)abc', 30, shown, 42); expect(sw(a) + sw(b)).toBe(30); expect(sw(a)).toBeLessThanOrEqual(shown) }
  expect(typeIn('abc', 10, 99, 1)[1]).toBe('')
  expect(reveal(10, 10, 5)).toMatchObject({ rows: 1, typing: 0 })
  expect(reveal(13, 10, 5)).toMatchObject({ rows: 2, typing: 1 })
  expect(reveal(30, 10, 5)).toMatchObject({ rows: 5, chars: Infinity })
})
import { treeRows } from './engcharts'
test('我的分支測試:每列剛好給的寬度,不折行', () => {
  const rows = treeRows([{ branch: 'fix/wheel-measure-86ff0-1014', ahead: 127, in_batch: false, green: 3, red: 0 }, { branch: 'feat/seat-screen-mods-86ff0-1014', ahead: 27, in_batch: true, green: 1, red: 1 }, { branch: 'docs/x', ahead: 1, in_batch: false, green: 0, red: 0 }] as never, 0, 59)
  for (const r of rows) expect(sw(r.map(x => x.text).join(''))).toBe(59)
  expect(sw(rows[0]!.map(x => x.text).join(''))).toBeLessThanOrEqual(59)
})
test('標誌補原廠色:只有標誌那一格上色,截斷、沒色、沒標誌都照原樣', () => {
  const logos = { '86ff0': '\u{f0b79}', '8f366': '', abcde: 'G' }, colors = { '86ff0': '#d97757', '8f366': '#10a37f' }
  const s = withLogo('@86ff0 寄給 @8f366 與 @abcde、@99999', logos)
  const segs = logoSegs(s, logos, colors)
  expect(segs.map(x => x.text).join('')).toBe(s)
  expect(segs.filter(x => x.color)).toEqual([{ text: '\u{f0b79}', color: '#d97757' }, { text: '', color: '#10a37f' }])
  expect(logoSegs(s, logos, undefined)).toEqual([{ text: s }])
  expect(logoSegs('前綴' + withLogo('@86ff0', logos), logos, colors)[1]).toEqual({ text: '\u{f0b79}', color: '#d97757' })
})
test('圖表格子的 @席碼補標誌 + 原廠色:補白吃掉多出的寬(至少留一格)、黏著寫的先補一格、沒標誌照原樣', () => {
  const logos = { '86ff0': '\u{f0b79}', '8f366': 'X' }, colors = { '86ff0': '#d97757' }
  const [a, b, c] = logoRows([[{ text: '@86ff0    ', color: 'text' }, { text: '██' }], [{ text: 'pm@8f366  ' }], [{ text: '@99999 ' }]], logos, colors)
  expect(a!.map(x => x.text).join('')).toBe('\u{f0b79} @86ff0  ██')                 // 10 格進、10 格出,後面的圖不歪
  expect(a!.find(x => x.color === '#d97757')?.text).toBe('\u{f0b79}')
  expect(a!.at(-1)).toEqual({ text: '██' })
  expect(b!.map(x => x.text).join('')).toBe('pm X @8f366 ')
  expect(c).toEqual([{ text: '@99999 ' }])
  expect(logoRows([[{ text: '@86ff0' }]], null, colors)).toEqual([[{ text: '@86ff0' }]])
})
test('影子續跑每列不超出卡寬(超出會被折行,「沒省到」掉到下一行);段與段之間空一行;原因整句不被切', () => {
  const rows = shadowRows({ kept: 0, skipped: 0, ran: 2, without_s: 66, actual_s: 84, reasons: [['上次量不到它讀了哪些檔', 2]] }, 90, 0)
  const w = (r: { text: string }[]) => dispW(r.map(s => s.text).join(''))
  for (const r of rows) expect(w(r)).toBeLessThanOrEqual(88)
  expect(rows.some(r => r.map(s => s.text).join('').includes('84 秒 · 沒省到'))).toBe(true)
  expect(rows.some(r => r.map(s => s.text).join('').includes('上次量不到它讀了哪些檔'))).toBe(true)
  expect(rows.filter(r => r.length === 1 && r[0]!.text === ' ').length).toBeGreaterThanOrEqual(3)
  const days: [string, number, number][] = [['10-08', 0, 0], ['10-09', 1, 0], ['10-10', 0, 0], ['10-11', 1, 0], ['10-12', 95, 1], ['10-13', 135, 2], ['10-14', 126, 4]]
  const sc = stackCols(days, 6, 0, 56)
  expect(w(sc[0]!)).toBeGreaterThanOrEqual(49)
  expect(w(sc[0]!)).toBeLessThanOrEqual(56)
})
test('提交節奏:最後一天的點與標籤貼到右邊,不留空', () => {
  const days: [string, number][] = [['10-08', 0], ['10-09', 0], ['10-10', 0], ['10-11', 0], ['10-12', 2], ['10-13', 40], ['10-14', 58]]
  const rows = commitCurve(days, 56, 5, 0)
  const w = (r: { text: string }[]) => dispW(r.map(s => s.text).join(''))
  expect(w(rows[rows.length - 1]!)).toBe(56)
  expect(w(rows[rows.length - 2]!)).toBe(56)
  expect(rows[rows.length - 1]!.map(s => s.text).join('').trimEnd().endsWith('58')).toBe(true)
})
test('我的分支測試:每列不超出給的寬度(窄卡末尾的 ○ / ● 不再折到下一行)', () => {
  const trees = [{ branch: 'fix/wheel-measure-86ff0-1014', ahead: 127, in_batch: false, green: 3, red: 0 }, { branch: 'feat/seat-screen-mods-86ff0-1014', ahead: 43, in_batch: true, green: 118, red: 2 }]
  for (const w of [46, 64, 90]) for (const r of treeRows(trees, 0, w)) expect(dispW(r.map(s => s.text).join(''))).toBeLessThanOrEqual(w)
})

// ── 語系(10-14):同一批純函式換成英文字典,出英文、沒有中日韓字;缺鍵 fail-loud ──
test('純檔切英文:分頁、分組、時間、此刻格、圖表名都換;缺鍵寫 !!鍵!!', () => {
  const CJK = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/
  try {
    useT('en', makeT('mod.aib_boards', FIXTURE.en!))
    expect(TABS.map(t => t.label)).toEqual(['Today', 'Project', 'Chat', 'Charts', 'Custom'])
    expect(groupTodos([{ id: 'a', group: 'human', text: 'x', source: 'now' }])[0]!.title).toBe('Waiting on a human')
    expect(knowLine({ sensor: 'inbox', line: null, data: { unread: 0, open: 1 } })).toBe('Inbox: 0 unread, 1 not closed')
    expect(ctxCell(40).sub).toBe('45 to go before 85%')
    expect(leaseCell([], '86ff0', 0)).toMatchObject({ label: 'Machine', text: 'not measured' })
    expect(hexValues({ success: { green: 1, red: 0 }, speed: null, inbox: null, handoff: null, collab: null } as never, null).raw.join(' ')).not.toMatch(CJK)
    for (const s of [...TABS.map(t => t.label), ...pkgStages({ branch: 'x', stage: 1 }).map(x => x[0]), knowLine({ sensor: 'my_trees', line: 'fix/a', data: { ahead: 3, dirty: 0, trees: [{}] } })]) expect(s).not.toMatch(CJK)
    expect(treeRows([{ branch: 'fix/a', ahead: 1, in_batch: true, green: 1, red: 1 }] as never, 0, 59).map(r => r.map(x => x.text).join('')).join('\n')).not.toMatch(CJK)
    for (const r of treeRows([{ branch: 'fix/a', ahead: 1, in_batch: true, green: 1, red: 1 }] as never, 0, 59)) expect(sw(r.map(x => x.text).join(''))).toBeLessThanOrEqual(59)   // 表頭 Batch 5 格也不超寬
    useT('en', makeT('mod.aib_boards', {}))
    expect(tr('tab_today')).toBe('!!mod.aib_boards.tab_today!!')
  } finally { useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!)) }
})

// ── 欄寬照語系(審查 10-14 第一輪):英文 / 日文 / 韓文的標籤比中文長,每一欄都要放得下最長的字再空一格,不准截字、不准黏到下一欄 ──
import { seatTable, waitHist, waterfallRows, chordMatrix } from './tiercharts'
import { labelW, rowLabelW, logTagW } from './lib'
test('四語:圖表表頭 / 標籤欄都放得下最長的字 + 1 格間隔;中文欄寬不變', () => {
  const txt = (r: { text: string }[]) => r.map(x => x.text)
  const seats = [{ code: 'a9769', prov: 'claude', ctx: 54, idle_s: 60, unread: 0, now_state: 'busy' }] as never
  try {
    for (const loc of ['zh-TW', 'en', 'ja', 'ko'] as const) {
      useT(loc, makeT('mod.aib_boards', FIXTURE[loc]!))
      const gap = (s: string, key: string) => { expect(s.endsWith(' ')).toBe(true); expect(s.trimEnd()).toBe(tr(key)) }   // 整個字都在、後面至少一格空白
      const head = txt(seatTable(seats, 0)[0]!)
      gap(head[0]!, 'th_seat'); gap(head[1]!, 'th_engine'); gap(head[3]!, 'th_unread')
      const wh = waitHist([0.5, 3, 300], 0)
      for (const [i, k] of [[0, 'wb_1m'], [4, 'wb_4h'], [5, 'wb_over']] as const) gap(txt(wh[5]!)[i]!, k)
      const wf = waterfallRows({ without_h: 10, reuse_h: 2, select_h: 2, shard_h: 2, actual_h: 4 }, 60, 0)
      ;['wf_without', 'wf_reuse', 'wf_select', 'wf_shard', 'wf_actual'].forEach((k, i) => gap(txt(wf[i]!)[0]!, k))
      gap(txt(chordMatrix({ names: ['8f366'], m: [[0]] })[0]!)[0]!, 'chord_head')
      const tree = txt(treeRows([{ branch: 'b', ahead: 1, in_batch: false, green: 0, red: 0 }] as never, 0, 64)[1]!)
      expect(tree.some(s => s.trimEnd() === ' ' + tr('never_ran'))).toBe(true)                                         // 「沒跑過」整個字都在
      for (const r of treeRows([{ branch: 'b', ahead: 1, in_batch: false, green: 0, red: 0 }] as never, 0, 64)) expect(sw(txt(r).join(''))).toBeLessThanOrEqual(64)
      for (const k of ['ev_ship_refuse', 'log_lease', 'log_now']) expect(sw(tr(k))).toBeLessThan(logTagW())
      for (const k of ['grp_now', 'kv_human', 'kv_measured', 'sensor_inbox']) expect(sw(tr(k))).toBeLessThan(labelW())
      for (const k of ['sensor_my_trees', 'duty', 'red_line']) expect(sw(tr(k))).toBeLessThan(rowLabelW())
      if (loc === 'zh-TW') { expect(logTagW()).toBe(9); expect(labelW()).toBe(10); expect(sw(head[1]!)).toBe(8); expect(sw(txt(wf[0]!)[0]!)).toBe(16) }   // 中文原本的欄寬
    }
  } finally { useT('zh-TW', makeT('mod.aib_boards', FIXTURE['zh-TW']!)) }
})

test('§22.5 第 3 步 使用習慣:點 3 次以上預設展開、手動開關優先、常用的排前面、同分照原順序', () => {
  let u = bumpUsage(null, 'know'); u = bumpUsage(u, 'know')
  expect(effectiveEx({}, u).know).toBe(false)
  u = bumpUsage(u, 'know')
  expect(u.know).toBe(USAGE_OPEN)
  expect(effectiveEx({}, u).know).toBe(true)
  expect(effectiveEx({ know: false }, u).know).toBe(false)          // 手動收起的照手動
  expect(foldOrder(['persona', 'project', 'know'], u)).toEqual(['know', 'persona', 'project'])
  expect(foldOrder(['persona', 'project', 'know'], {})).toEqual(['persona', 'project', 'know'])
})
test('§22.5 第 1、2 步:只有驗收 / bot 家族換流程;主題沒寫的項目用預設', () => {
  expect(roleFlow({ family: 'dev', nodes: [] })).toBeNull()
  expect(roleFlow({ family: 'qa', nodes: [] })).toBeNull()
  expect(roleFlow({ family: 'bot', nodes: [{ key: 'bot_in' }] })?.family).toBe('bot')
  expect(themeOf(null)).toEqual({ accent: 'claude', particle: '●', mul: 1, div: 1, border: 'round' })
  expect(themeOf({ speed: 'slow', border: 'bold' })).toEqual({ accent: 'claude', particle: '●', mul: 1, div: 2, border: 'bold' })
})
