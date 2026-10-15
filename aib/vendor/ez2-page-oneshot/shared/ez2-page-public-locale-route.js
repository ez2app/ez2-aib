"use strict";

// ez2-page-public-locale-route.js — 公開頁的語系 URL 與快取契約(三階秒開地基,2026-07-26)
//
// 為什麼需要這一層(這是本模組存在的理由,改它之前先讀):
//   管理台(/manage*)的語系存在 cookie,那沒問題,因為管理台一律 no-store。
//   但公開頁面若也用 cookie 決定語系,回應就必須 Vary: Cookie —— 邊緣快取會為每種
//   cookie 組合各存一份,命中率崩潰,三階「秒開」直接失效。所以公開頁的語系
//   一律進 URL,cookie 在公開路徑上不得參與內容決策。
//
// 與既有 route 契約的關係(刻意不改它):
//   ez2-page-route-contract 的 platform_path 規定 slug 不可含 "/",所以 /en/{slug}
//   不是一條新 route。語系是同一條 route 的「呈現變體」:route 決定頁面身分,
//   locale 決定用哪種語言渲染它。因此本模組只做 URL 解析與 canonical/hreflang,
//   route 查詢仍用去掉語系前綴後的 canonical 路徑,route 表不需要為語系膨脹四倍。
//
// 三階(tier)與各自的 URL 形態:
//   folder_bio        ez2.page/{slug}                 名片,零 JS 靜態
//   subdomain_seo     {slug}.ez2.page/                SEO 站
//   custom_commerce   {自有網域}/                      電商
//   三階的語系一律以路徑前綴表達:/{locale}/... ,預設語系不帶前綴。
//
// 預設語系不帶前綴是 SEO 決定:同一份內容不應有兩個等價 URL(/ 與 /zh-TW/),
// 否則自我競爭。帶了前綴的預設語系一律 301 回無前綴的 canonical。

const i18n = require("./ez2-page-i18n.js");

const VERSION = "2026-07-26.ez2-page-public-locale-route.v1";
const TIERS = Object.freeze(["folder_bio", "subdomain_seo", "custom_commerce"]);
const PLATFORM_HOST = "ez2.page";
const PLATFORM_SUFFIX = ".ez2.page";
// 這些子網域是平台自用,不能被當成租戶站(與 route contract 的保留字同源語意)
// ★ 保留名稱的真相源已收斂到 ez2-page-reserved-names(2026-07-28)。
//   在此之前這裡與 ez2-page-route-contract 各有一份且互相不是超集:
//   這份有 mail 沒 app、那份有 app 沒 mail,而 canon 明寫管理後台住在 app.ez2.page;
//   `usermailto`(活著的 Mailgun 寄信網域)兩份都沒有。
const reservedNames = require("./ez2-page-reserved-names.js");
// 匯出形狀不變,內容改由單一真相源導出。
const RESERVED_SUBDOMAINS = reservedNames.ALL_RESERVED;
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

function normalizeHost(value) {
  const host = String(value || "").trim().toLowerCase().split(":")[0];
  return host.endsWith(".") ? host.slice(0, -1) : host;
}

// 尾斜線與大小寫分歧會讓同一頁產生多個 URL(快取多份、SEO 自我競爭),
// 所以一律正規化,分歧由呼叫端 301 收斂。
function normalizePath(value) {
  let path = String(value == null ? "/" : value);
  const cut = path.search(/[?#]/);
  if (cut >= 0) path = path.slice(0, cut);
  if (!path.startsWith("/")) path = "/" + path;
  path = path.replace(/\/{2,}/g, "/");
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  return path || "/";
}

function tierForHost(host) {
  const normalized = normalizeHost(host);
  if (!normalized) return null;
  if (normalized === PLATFORM_HOST || normalized === "www." + PLATFORM_HOST) return "folder_bio";
  if (normalized.endsWith(PLATFORM_SUFFIX)) {
    const label = normalized.slice(0, -PLATFORM_SUFFIX.length);
    if (!label || label.includes(".") || !SLUG_RE.test(label)
      || !reservedNames.checkSubdomain(label).ok) return null;
    return "subdomain_seo";
  }
  return "custom_commerce";
}

// URL 語系前綴一律小寫連字號形式(zh-tw / en / ja / ko),對應到正規 locale。
// 大小寫變體(zh-TW)可被接受但會被 canonical 收斂,避免兩個 URL 同內容。
function localeFromSegment(segment) {
  const raw = String(segment || "").toLowerCase();
  if (!raw) return null;
  for (const code of i18n.SUPPORTED_LOCALES) if (code.toLowerCase() === raw) return code;
  return null;
}
function segmentForLocale(locale) { return String(locale).toLowerCase(); }

// 解析公開 URL:回頁面身分(route 查詢用的 canonical 路徑)、語系、以及 canonical URL。
// 不讀 cookie —— 公開路徑上 cookie 不得參與內容決策(見檔頭)。
function resolvePublicUrl(input = {}) {
  const host = normalizeHost(input.host);
  const path = normalizePath(input.path);
  const tier = tierForHost(host);
  if (!tier) return { ok: false, errors: ["public_locale_host_unsupported"], host, path };

  const segments = path === "/" ? [] : path.slice(1).split("/");
  const localeFromPath = segments.length ? localeFromSegment(segments[0]) : null;
  const rest = localeFromPath ? segments.slice(1) : segments;

  // 語系來源只有兩種:URL 前綴(明確)或 Accept-Language(協商)。cookie 不在其中。
  const negotiated = i18n.negotiate({ acceptLanguage: input.acceptLanguage });
  const locale = localeFromPath || negotiated.locale;
  const localeSource = localeFromPath ? "path" : negotiated.source;

  let slug = null;
  const errors = [];
  if (tier === "folder_bio") {
    slug = rest.length ? rest[0] : null;
    if (!slug || rest.length > 1 || !SLUG_RE.test(slug)) errors.push("public_locale_folder_path_invalid");
  } else {
    // 子網域與私有網域的頁面身分來自 host,路徑只承載語系(與 route contract 的
    // 「path 必須是 /」一致);多出來的路徑段留給後續的文章/商品路由,本層不收。
    slug = tier === "subdomain_seo" ? host.slice(0, -PLATFORM_SUFFIX.length) : host;
    if (rest.length) errors.push("public_locale_root_path_expected");
  }
  if (errors.length) return { ok: false, errors, host, path, tier };

  const isDefault = locale === i18n.DEFAULT_LOCALE;
  const base = tier === "folder_bio" ? "/" + slug : "/";
  const canonicalPath = isDefault ? base : (base === "/" ? "/" + segmentForLocale(locale) : "/" + segmentForLocale(locale) + base);
  // 預設語系帶了前綴、或大小寫/尾斜線分歧 => 需要 301 收斂到 canonical
  const redirectTo = canonicalPath !== path ? canonicalPath : null;

  return {
    ok: true,
    schema_version: VERSION,
    tier,
    host,
    path,
    slug,
    locale,
    locale_source: localeSource,
    canonical_path: canonicalPath,
    redirect_to: redirectTo,
    // route 查詢用:語系前綴剝掉後的頁面身分,route 表不因語系膨脹
    route_lookup: Object.freeze({ hostname: host === "www." + PLATFORM_HOST ? PLATFORM_HOST : host, path: base })
  };
}

// hreflang 全集:每個語系一條 + x-default 指向預設語系。
// 缺 x-default 會讓搜尋引擎自行猜測未涵蓋語系的落點,所以它是必要的而非選配。
function alternateLinks(resolved) {
  if (!resolved || !resolved.ok) return [];
  const origin = "https://" + (resolved.route_lookup.hostname);
  const base = resolved.tier === "folder_bio" ? "/" + resolved.slug : "/";
  const links = i18n.SUPPORTED_LOCALES.map((code) => {
    const path = code === i18n.DEFAULT_LOCALE
      ? base
      : (base === "/" ? "/" + segmentForLocale(code) : "/" + segmentForLocale(code) + base);
    return { hreflang: code, href: origin + path };
  });
  links.push({ hreflang: "x-default", href: origin + base });
  return links;
}

function alternateLinkTags(resolved) {
  return alternateLinks(resolved).map((link) =>
    '<link rel="alternate" hreflang="' + link.hreflang + '" href="' + link.href + '">').join("");
}
function canonicalLinkTag(resolved) {
  if (!resolved || !resolved.ok) return "";
  return '<link rel="canonical" href="https://' + resolved.route_lookup.hostname + resolved.canonical_path + '">';
}

// 快取政策:三階各自的邊緣策略。共同紅線=公開回應永不 Vary: Cookie。
// 動態端點(購物車/結帳/管理)不走這裡,它們一律 no-store。
const TIER_CACHE = Object.freeze({
  folder_bio: Object.freeze({ cache_control: "public, max-age=60, s-maxage=86400, stale-while-revalidate=604800" }),
  subdomain_seo: Object.freeze({ cache_control: "public, max-age=60, s-maxage=86400, stale-while-revalidate=604800" }),
  // 電商目錄頁的價格與庫存會變,邊緣 TTL 短、但允許先回舊頁再背景更新,
  // 讓訪客永遠先看到頁面(秒開)再看到最新數字。
  custom_commerce: Object.freeze({ cache_control: "public, max-age=0, s-maxage=300, stale-while-revalidate=86400" })
});

function cachePolicy(resolved) {
  if (!resolved || !resolved.ok) return { ok: false, errors: ["public_locale_unresolved"] };
  const spec = TIER_CACHE[resolved.tier];
  return {
    ok: true,
    tier: resolved.tier,
    // 語系已在 URL,所以 Vary 只需要 Accept-Encoding;列出 Cookie 即為違規。
    headers: Object.freeze({ "cache-control": spec.cache_control, vary: "Accept-Encoding" }),
    cache_key: Object.freeze({ host: resolved.route_lookup.hostname, path: resolved.canonical_path })
  };
}

// 守衛:給回應標頭做機械檢查,讓「不小心讓公開頁依賴 cookie」變成紅燈而不是慢性失血。
function auditPublicResponse(headers = {}) {
  const problems = [];
  const lower = {};
  for (const [key, value] of Object.entries(headers)) lower[String(key).toLowerCase()] = String(value);
  const vary = (lower.vary || "").toLowerCase();
  if (vary.split(",").map((part) => part.trim()).includes("cookie")) problems.push("vary_cookie_breaks_edge_cache");
  if (lower["set-cookie"]) problems.push("public_page_sets_cookie");
  const cacheControl = (lower["cache-control"] || "").toLowerCase();
  if (!cacheControl) problems.push("cache_control_missing");
  else if (cacheControl.includes("no-store")) problems.push("public_page_is_uncacheable");
  return { ok: problems.length === 0, problems: problems.sort() };
}

module.exports = {
  VERSION,
  TIERS,
  RESERVED_SUBDOMAINS,
  TIER_CACHE,
  normalizeHost,
  normalizePath,
  tierForHost,
  localeFromSegment,
  segmentForLocale,
  resolvePublicUrl,
  alternateLinks,
  alternateLinkTags,
  canonicalLinkTag,
  cachePolicy,
  auditPublicResponse
};
