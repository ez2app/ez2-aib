// aib-seat-screen/v1(src/cockpit/seat_screen.py 輸出;設計稿 §4.1)
export type Level = 'ok' | 'warn' | 'red' | 'unknown'
export type Know = { sensor: string; level: Level; line?: string | null; detail?: string[]; data?: Record<string, unknown>; measured_at?: number; source?: string }
export type Todo = { id: string; group: string; text: string; from?: string | null; since?: number | null; source: string }
export type Screen = {
  schema: string; seat_id: string | null; code: string | null; cabin: string | null; role: string | null
  persona: { uid?: string; name?: string; emoji?: string; category?: string; duties?: string[]; red_lines?: string[]; theme?: { color?: string; particle?: string; speed?: string; border?: string } } | null
  project: { uid?: string; title?: string; work_state?: string } | null
  now: { text?: string; state?: string; at?: number; steps_done?: number | null; steps_total?: number | null; started_at?: number | null } | null
  todo: Todo[]; know: Know[]; events?: { at: number; kind: string; who: string; text: string; status?: string | null }[]; logos?: Record<string, string>; logo_colors?: Record<string, string>; measured_at: number; took_ms?: number; missing?: string[]
  tier?: string; subs?: string[]
  // §22.5 第 1 步 依角色換流程(S1 fetch_flow):dev 家族 nodes 空 ⇒ 畫原本的包 → 推包 → 三格 → 下一件
  flow?: { family: string; nodes: FlowNode[] }
}
export type FlowNode = { key: string; level: string; count: number; guess?: boolean; items: { who: string; text: string; at?: number | null }[] }
export type Tab = 'today' | 'proj' | 'dm' | 'charts' | 'custom'
// S1 --charts(工程級;設計稿 §17/§18)
export type Charts = {
  trees: { branch: string; path?: string; ahead: number; in_batch: boolean; green: number; red: number }[]
  shadow: { at?: number; kept: number; skipped: number; ran: number; without_s: number | null; actual_s: number | null; reasons: [string, number][]; lines?: string[] } | null
  trend: [string, number, number][]; slowest: [string, number, number][]; commits: [string, number][]; files: [string, number][]
  hex: { success?: { green: number; red: number } | null; speed?: { median_min: number; n: number } | null; inbox?: { unread: number } | null; handoff?: { rounds: number; receipts: number } | null; collab?: { seats: number } | null }
  measured_at: number; took_ms?: number
  lead?: LeadCharts | null; qa?: QaCharts | null; captain?: CaptainCharts | null
}
export type SeatRow = { code: string; prov?: string; name?: string; ctx?: number | null; idle_s?: number | null; unread?: number | null; now_state?: string | null; stale?: boolean }
export type LeadCharts = { subs: SeatRow[]; funnel: number[]; wait: number[]; gantt: { title: string; owner?: string; status?: string; s: number; e: number }[]; hot: [string, number, number][] }
export type QaCharts = { fleet_trend: [string, number, number][]; flaky: [string, number, number][]; pareto: [string, number][]; gate_blocks: [string, number][]; events?: { t: number; who: string; kind: string }[] }
export type CaptainCharts = {
  shadow?: { runs: [number, number, number, number][] } | null; seats?: SeatRow[] | null; lease?: { s: number; e: number; who: string; what?: string; ex?: boolean }[] | null
  heat?: { codes: string[]; rows: Record<string, number[]> } | null; river?: [number, number, number, number][] | null
  waterfall?: { without_h: number; reuse_h: number; select_h: number; shard_h: number; actual_h: number } | null
  chord?: { names: string[]; m: number[][] } | null; funnel?: number[] | null; decide?: { since: number; text: string; from?: string }[] | null
  beat?: number[] | null; sankey?: { a: string; d: string; o: string; n: number }[] | null; missing?: string[]
}
export type DmItem = { id: string; sender?: string; sender_seat?: string; kind?: string; status: string; ts?: number; when?: string; text: string }
export type ChatRow = { id?: string; at: number; from?: string | null; to: string; out: boolean; state?: string | null; label?: string | null; status?: string | null; kind?: string | null; text: string }
export type SeatCard = { logo?: string | null; color?: string | null; provider?: string | null; role?: string | null; name?: string | null; emoji?: string | null }
export type Dm = { items: DmItem[]; counts: { unread: number; open: number; done_7d: number }; chat?: ChatRow[]; seats?: Record<string, SeatCard> }
export type PlanNode = { id: string; parent?: string | null; layer: string; title: string; status: string; owner?: string | null; progress?: number | null }
export type Plan = { project_uid: string; title?: string; nodes: PlanNode[] }

declare module 'claude-code' {
  interface PluginState {
    'aib-boards': { tab: Tab; expand: Record<string, boolean | string | undefined>; screen: { at: number; data: Screen | null; err: string | null }; opened: boolean; dmDir: string; charts: { at: number; data: Charts | null; err: string | null }; dm: { at: number; data: Dm | null; err: string | null }; plan: { at: number; data: Plan | null; err: string | null }
      // 席位核心(seatcore.tsx,從 aib-seat 搬來,設計稿 §24)
      mailCards: { id: string; from: string | null; label: string | null; kind: string | null; at: number; text: string; status: 'unread' | 'read' | 'done' | 'held' | 'archived'; open: boolean }[]; mailDismissed: string[]; blockSeen: string }
  }
}
