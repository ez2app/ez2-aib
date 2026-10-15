// aib-languages 的 $.state 契約(claude plugin validate 用它核對模組裡每個 $.state 鍵)
export type Locale = 'zh-TW' | 'en' | 'ja' | 'ko'
export type Scope = 'machine' | 'cabin'

/** `aib lang --json` 回的那一包(src/cockpit/aib_lang.py status()) */
export interface LangStatus {
  locale: string
  source: string
  choices: string[]
  files: { cabin?: string; user: string }
  notified?: number
}

declare module 'claude-code' {
  interface PluginState {
    'aib-languages': {
      /** 現在生效的語系與來源(session.start 與每 5 秒輪詢更新) */
      status: LangStatus
      /** 選單上「套用範圍」的選擇;重載後還在 */
      scope: Scope
    }
  }
}
