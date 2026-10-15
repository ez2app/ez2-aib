import type { EngineInterface } from 'claude-code'
import type { RoomInfo } from './seat/seat-label'
import { tr } from './i18n'

// /aib room = 艦長室網頁(外掛 captain-room,開發中、公開版不帶;docs/PLAN-extensions-1014.md §2.4):
// 從席位核心拆出來單放這裡。seat-info 回 room_installed(這份 aib 有 captain_room.py 或 ~/.ez2-aib/plugins/captain-room)——
// 沒裝 ⇒ 只說開發中,不去碰 aib room、也不讀艦長室的狀態檔。
export type RoomStep = 'dev' | 'never' | 'dead' | 'restart' | 'open'
export function roomStep(installed: boolean | undefined, room: RoomInfo | null | undefined): RoomStep {
  if (!installed) return 'dev'
  if (!room?.url) return 'never'
  if (!room.alive) return room.script ? 'restart' : 'dead'
  return 'open'
}

// 沒活就走 aib room 拉起來(spawn_detached 洗掉席位環境,不自己 nohup),再開瀏覽器
export async function openRoom($: EngineInterface, installed: boolean | undefined, room: RoomInfo | null | undefined): Promise<string> {
  const step = roomStep(installed, room)
  if (step === 'dev') return tr('seat_room_dev')
  if (step === 'never') return tr('seat_room_never')
  const url = room!.url!
  if (step === 'dead') return tr('seat_room_dead', { url })
  if (step === 'restart') {
    const r = await $.process.run(['aib', 'room'], { timeoutMs: 30_000 }).catch(() => undefined)
    if (!r || r.exitCode !== 0) return tr('seat_room_fail', { rc: r?.exitCode ?? '?', err: (r?.stderr ?? '').trim().split('\n').pop()?.slice(0, 80) ?? '' })
    return tr('seat_room_restarted', { url })
  }
  const o = await $.process.run(['open', url], { timeoutMs: 10_000 }).catch(() => undefined)
  return o && o.exitCode === 0 ? tr('seat_room_opened', { url }) : tr('seat_room_nobrowser', { rc: o?.exitCode ?? '?', url })
}
