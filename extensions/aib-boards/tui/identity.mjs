// 非 Claude 席的席位身份(設計稿 §25,the maintainer 10-14「做非 Claude 版席位外掛,先試 Codex 和 agy」):
// 跑 bin/seat-info.py 量這一席,用 aib-boards 同一支 promptSection 排成文字印出來(Claude 席系統提示同一份講法、同一組字典鍵)。
// 語系:EZ2_AIB_LOCALE → i18n_lib.py --resolve → zh-TW;字典 i18n_lib.py --prefix mod.aib_boards(同小窗格 main.mjs)。
// 用法:node --import ./reg.mjs identity.mjs [session_uuid];量不到 ⇒ 印一句照實說,rc 0(鉤子不能擋住對話)
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { normalize, makeT, useT, tr } from '../hooks/i18n.ts'
import { promptSection } from '../hooks/seat/seat-label.ts'

const here = fileURLToPath(new URL('.', import.meta.url))
const REPO = fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, '')
const PY = process.env.EZ2_PY3 || 'python3'
const SRC = 'aib-seat-hooks'
const lastJson = out => JSON.parse(String(out).trim().split('\n').pop() || '{}')
const run = args => execFileSync(PY, args, { encoding: 'utf8', timeout: 20000, stdio: ['ignore', 'pipe', 'ignore'] })

function loadDict() {
  let loc = normalize(process.env.EZ2_AIB_LOCALE)
  if (!loc) { try { loc = normalize(lastJson(run(['-I', `${REPO}/src/cockpit/i18n_lib.py`, '--resolve'])).locale) } catch { loc = null } }
  loc = loc ?? 'zh-TW'
  let data = {}
  try { data = lastJson(run(['-I', `${REPO}/src/cockpit/i18n_lib.py`, '--locale', loc, '--prefix', 'mod.aib_boards', '--json'])) } catch { data = {} }
  useT(loc, makeT('mod.aib_boards', data))
}

const uuid = process.argv[2] ?? ''
loadDict()
let text
try {
  const seat = JSON.parse(run(['-I', `${here}../bin/seat-info.py`, ...(uuid ? [uuid] : [])]))
  text = seat.code ? promptSection(seat, null, SRC) : tr('seat_p_noseat', { src: SRC, why: '' })
} catch (e) {
  text = tr('seat_p_noseat', { src: SRC, why: tr('seat_paren', { value: String(e?.message ?? e).split('\n')[0].slice(0, 80) }) })
}
process.stdout.write(text + '\n')
