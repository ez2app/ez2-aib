// mod 端 i18n(docs/DESIGN-aib-languages-04e0a-1014.md §5):字放 repo i18n/<loc>.json 的 mod.<名>.* 鍵,
// 由 `i18n_lib.py --locale L --prefix mod.<名> --json` 一次取整組(缺譯已由 i18n_lib 補 en);依語系快取;
// 缺鍵回 !!key!!(loader-spec fail-loud,畫面上一眼看得到)。
// 這一檔只放**純函式**:驗證器不准把 $ 傳進別的檔的函式,所以讀字典那段(要 $)住在 register.tsx 的 bundle()。別的 mod 照抄兩段即可。
export const LOCALES = ['zh-TW', 'en', 'ja', 'ko'] as const
export type Loc = (typeof LOCALES)[number]
/** 每個語言用自己的寫法(選單上不管現在是哪個語系,都看得懂自己那顆) */
export const SELF_NAMES: Record<Loc, string> = { 'zh-TW': '繁體中文', en: 'English', ja: '日本語', ko: '한국어' }

export function normalize(raw: string | undefined | null): Loc | null {
  const t = (raw ?? '').trim().split('@')[0]!.split('.')[0]!.replace('_', '-').toLowerCase()
  const m: Record<string, Loc> = { zh: 'zh-TW', 'zh-tw': 'zh-TW', 'zh-hant': 'zh-TW', 'zh-hant-tw': 'zh-TW', en: 'en', 'en-us': 'en', 'en-gb': 'en', ja: 'ja', 'ja-jp': 'ja', ko: 'ko', 'ko-kr': 'ko' }
  return m[t] ?? null
}

export function fmt(template: string, params?: Record<string, string | number>): string {
  if (!params) return template
  return template.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (whole, name: string) => (name in params ? String(params[name]) : whole))
}

const bundles = new Map<string, Record<string, string>>()

/** 快取:prefix@loc → 整組字;切語系時 forget() */
export function cached(prefix: string, loc: Loc): Record<string, string> | undefined { return bundles.get(`${prefix}@${loc}`) }
export function remember(prefix: string, loc: Loc, data: Record<string, string>): void { bundles.set(`${prefix}@${loc}`, data) }
export function forget(): void { bundles.clear() }

export function makeT(prefix: string, data: Record<string, string>) {
  return (key: string, params?: Record<string, string | number>): string => {
    const full = `${prefix}.${key}`
    const v = data[full]
    return v === undefined ? `!!${full}!!` : fmt(v, params)
  }
}
