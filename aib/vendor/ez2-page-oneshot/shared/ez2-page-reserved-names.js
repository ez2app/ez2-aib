"use strict";

// ez2-page-reserved-names.js — 保留名稱的單一真相源(2026-07-28)
//
// ★ 為什麼這個檔存在:在它之前有**兩份**保留清單,而且互相不是超集 ——
//   `ez2-page-route-contract` 擋 `app` 不擋 `mail`;
//   `ez2-page-public-locale-route` 擋 `mail` 不擋 `app`。
//   而 canon 明寫管理後台就住在 `app.ez2.page`。**兩份不同的答案就是兩個真相源。**
//
// ★★ 更嚴重的一個:`usermailto.ez2.page` 兩份都沒有,而那是**現在活著的寄信網域**
//   (Mailgun 的 MX 與 `smtp._domainkey` DKIM 都掛在它底下)。被租戶註冊走就寄不出信。
//
// ★★★ 這份清單為什麼要在開放註冊之前定死:**名字發出去就收不回來。**
//   它會被印在名片上、貼在 IG、寫進 LINE 訊息、做成 QR,而我們還承諾畢業後
//   維持 308 轉址 24 個月。想收回一個已經發出去的名字,代價由使用者承擔。
//   這與身份鐵律「輸的 uid 永久可解析」是同一條:送出去的收不回來。
//
// 兩個命名空間,規則不同但清單共用:
//   subdomain     `<label>.ez2.page`      —— 撞到基礎設施會壞掉(信、憑證、DNS)
//   path segment  `ez2.page/<slug>`       —— 撞到平台自己的路由會蓋掉我們的頁
//
// ★ path segment 這一組在本檔之前**完全不存在** —— route-contract 只擋子網域,
//   不擋任何 slug。也就是說在此之前 `ez2.page/register` 是可以被註冊的,
//   而那正是我們自己的註冊頁。

const VERSION = "2026-07-28.ez2-page-reserved-names.v1";

// ── 第一組:撞到會直接壞掉(協定與基礎設施)──
// `.well-known` 特別重要:ACME 憑證簽發、Apple 的 app-site-association、
// Android 的 assetlinks 全在裡面。被佔走等於憑證簽不出來、深層連結失效。
const PROTOCOL_NAMES = Object.freeze([
  "well-known", "robots", "sitemap", "favicon", "ads", "app-ads",
  "security", "humans", "manifest", "sw", "service-worker", "browserconfig",
  "apple-touch-icon", "git", "env"
]);
// ★ `_next` / `_nuxt` 曾經在這張表裡,被 auditReservedNames() 判為 unreachable 而移除:
//   結構規則已經擋掉所有底線開頭的名字,所以列在這裡的那兩條永遠不會被比對到。
//   **一條永遠不會生效的保留規則,比沒有還糟** —— 它會讓人以為那個名字被守著。

// 這些被租戶佔走會讓我們寄不出信、簽不出憑證,而且**無法自救**。
// ★ `usermailto` 是實查 zone 得到的:CNAME→mailgun.org、兩筆 MX、SPF、DKIM 都在它底下。
const INFRA_NAMES = Object.freeze([
  "www", "mail", "email", "smtp", "imap", "pop", "mx", "ns", "ns1", "ns2", "ns3",
  "dns", "autodiscover", "autoconfig", "mta-sts", "dmarc", "dkim", "spf",
  "usermailto"
]);

// ── 第二組:平台自用(現在用或將來會用)──
// ★ `app` 是 canon 明訂的管理後台落點;`register`/`manage`/`go` 是現行服務的活路由。
const PLATFORM_NAMES = Object.freeze([
  "api", "app", "admin", "admin-preview", "dashboard", "console", "panel",
  "auth", "login", "logout", "signin", "signup", "register", "manage",
  "oauth", "sso", "connect", "callback", "id", "account", "accounts",
  "settings", "billing", "invoice", "checkout", "cart", "pay", "payment",
  "plan", "plans", "pricing", "themes", "upgrade", "user", "users", "me", "profile",
  "static", "assets", "cdn", "img", "images", "media", "files",
  "upload", "uploads", "download", "downloads", "js", "css", "fonts",
  "dist", "build", "public",
  "go", "qr", "liff", "line", "hook", "hooks", "webhook", "webhooks",
  "graphql", "rest", "rpc", "ws", "socket", "v1", "v2",
  "status", "health", "healthz", "metrics", "monitor", "uptime",
  "logs", "debug", "trace", "ping",
  "dev", "stage", "staging", "test", "qa", "uat", "preview", "beta", "alpha",
  "demo", "sandbox", "canary", "next", "local",
  "ci", "deploy", "registry", "docker", "npm", "vpn", "ssh", "sftp", "ftp",
  "proxy", "gateway", "edge", "origin", "db", "redis", "cache", "queue", "search", "es"
]);

// ── 第三組:留給未來的產品線(現在不留,以後要用就得跟客戶買回來)──
const PRODUCT_NAMES = Object.freeze([
  "blog", "news", "docs", "doc", "help", "support", "faq",
  "guide", "guides", "learn", "academy", "course", "courses",
  "community", "forum", "event", "events",
  "shop", "store", "product", "products", "order", "orders",
  "quest", "quests", "task", "tasks", "point", "points", "card", "cards",
  "game", "play", "meet", "meeting", "book", "booking", "appointment", "calendar",
  "form", "forms", "survey", "tag", "tags", "category", "categories",
  "archive", "feed", "rss", "atom", "amp"
]);

// ── 第四組:法遵與信任(這幾個是未來處理資料主體請求與檢舉的固定入口)──
const LEGAL_NAMES = Object.freeze([
  "about", "contact", "terms", "privacy", "consent", "legal", "cookie", "cookies",
  "gdpr", "dsar", "policy", "policies", "press", "brand", "careers", "jobs",
  "security-policy", "abuse", "report", "dmca", "complaints"
]);

// ── 第五組:品牌與冒名防護 ──
// ★ 重點不是我們要用,是**不能讓別人拿去釣魚**。`ez2.page/support` 這種網址,
//   受害者不會起疑。
const BRAND_NAMES = Object.freeze([
  "ez2", "ez2ai", "ez2-ai", "ez2app", "ez2page", "ez2site", "ez2life", "ez2design",
  "ez2shop", "ez2help", "skyeyes", "skyeye", "godview", "official", "verified",
  "staff", "team", "root", "system", "service", "services",
  "noreply", "no-reply", "postmaster", "hostmaster", "webmaster"
]);

// 子網域與路徑共用同一份清單。**兩邊必須一致**,因為升級路徑是
// `ez2.page/alice` → `alice.ez2.page`;兩邊規則不同的話,升級會撞名。
const ALL_RESERVED = Object.freeze(
  Array.from(new Set([].concat(
    PROTOCOL_NAMES, INFRA_NAMES, PLATFORM_NAMES, PRODUCT_NAMES, LEGAL_NAMES, BRAND_NAMES
  ))).sort()
);

// 結構規則的參數。長度下限 3 是刻意的:一與二字元最貴、最像系統路徑、
// 也最容易被搶去做冒名。
const MIN_LENGTH = 3;
const MAX_LENGTH = 30;

// 同形字折疊:只對品牌關鍵字做,不對全部名字做(全做會誤殺大量正常名字)。
// 0/o、1/l/i、rn/m 是最常見的三種視覺混淆。
const HOMOGLYPH_FOLDS = Object.freeze([
  [/0/g, "o"], [/1/g, "l"], [/rn/g, "m"]
]);
const BRAND_ROOTS = Object.freeze(["ez2", "ez2ai", "ez2app", "ez2page", "skyeyes", "godview", "official"]);

// 277-D 示範帳號(RL-057 D):`demo-<theme>` 八個 slug 是**系統自己的**,ez2.page/demo-nebula … 由建帳工具開、首頁八卡連過去。
//   規則:`demo-*` 前綴只准系統用(今天只擋整字 demo):**註冊時**擋(beta-registration.validateInput),只有 registerShowcase
//   能建那八個。★ 不放進 checkPathSegment / checkSubdomain(路由契約):路由契約是每次渲染都過的,把前綴放進去會讓
//   規則之前就註冊了 demo-xxx 的既有客戶(規格 §一「有就登記不改名」)與 D2 的本機展示租戶 demo-page 整頁渲不出來。
//   八個名字對應 design-pack 的 THEMES 鍵;這裡不 require design-pack(本檔是保留名單的單一真相源,零相依),
//   對齊由 demo-fleet smoke 釘住(兩邊漂了會紅)。
const SHOWCASE_PREFIX = "demo-";
const SHOWCASE_SLUGS = Object.freeze(["nebula", "mono", "dawn", "forest", "rosegold", "starlight", "pop", "ocean"].map((theme) => SHOWCASE_PREFIX + theme));
// 規則之前就存在的系統示範 slug:tools/ez2-page-demo-tenant.js(D2)的本機展示租戶。它走一般 register(要邀請碼、不是 showcase),
//   所以註冊閘放行它;真客戶拿不到它(本機工具用的名字,正式站沒有)。
const LEGACY_DEMO_SLUGS = Object.freeze(["demo-page"]);
function isShowcaseSlug(slug) { return SHOWCASE_SLUGS.includes(String(slug || "").toLowerCase()); }
// `demo-*` 但不是那八個、也不是 legacy ⇒ 註冊時保留(系統前綴,不給人拿)。
function isReservedPrefix(label) {
  const value = String(label || "").toLowerCase();
  return value.startsWith(SHOWCASE_PREFIX) && !isShowcaseSlug(value) && !LEGACY_DEMO_SLUGS.includes(value);
}

function foldHomoglyphs(value) {
  let out = String(value || "").toLowerCase();
  for (const [pattern, replacement] of HOMOGLYPH_FOLDS) out = out.replace(pattern, replacement);
  return out;
}

// 結構問題。回陣列而不是布林,因為拒絕的時候要說得出是哪一條 ——
// 使用者看到「不行」而不知道為什麼,只會再試一次一樣的名字。
function structuralIssues(label) {
  const value = String(label || "");
  const issues = [];
  if (!value) return ["name_empty"];
  if (value !== value.toLowerCase()) issues.push("name_not_lowercase");
  if (value.includes(".")) issues.push("name_contains_dot");
  if (value.startsWith("_")) issues.push("name_leading_underscore");
  if (value.startsWith("-") || value.endsWith("-")) issues.push("name_hyphen_edge");
  if (value.includes("--")) issues.push("name_consecutive_hyphen");
  // xn-- 是 punycode 前綴,擋它是為了同形字攻擊(用外文字元做出看起來像 ez2 的名字)。
  if (value.startsWith("xn--")) issues.push("name_punycode_prefix");
  if (value.length < MIN_LENGTH) issues.push("name_too_short");
  if (value.length > MAX_LENGTH) issues.push("name_too_long");
  if (/^[0-9]+$/.test(value)) issues.push("name_all_digits");
  return issues;
}

function isReservedWord(label) {
  const value = String(label || "").toLowerCase();
  if (ALL_RESERVED.includes(value)) return true;
  // 品牌同形變體:`ez2` 與 `ezz`(1→l 之類)不同,但 `ez2app` 與 `ez2app` 的
  // 視覺變體要一起擋。只折疊之後比對品牌根,不比對全表。
  const folded = foldHomoglyphs(value);
  return BRAND_ROOTS.some((root) => folded === foldHomoglyphs(root));
}

// 統一入口。兩個命名空間目前用同一份判準;分開成兩個函式是為了將來若要
// 分歧(例如路徑允許更長)時,消費端不必改。
function checkSubdomain(label) {
  const issues = structuralIssues(label);
  if (isReservedWord(label)) issues.push("name_reserved");
  return { ok: issues.length === 0, issues: Object.freeze(issues), namespace: "subdomain" };
}

function checkPathSegment(slug) {
  const issues = structuralIssues(slug);
  if (isReservedWord(slug)) issues.push("name_reserved");
  return { ok: issues.length === 0, issues: Object.freeze(issues), namespace: "path_segment" };
}

// 稽核:證明清單本身沒有壞掉(重複、大小寫混雜、自己違反自己的結構規則)。
// ★ 最後一條特別重要:如果保留清單裡有一個名字連結構檢查都過不了,
//   那條記錄就永遠不會被用到,而我們會以為它守著。
function auditReservedNames() {
  const errors = [];
  const seen = new Set();
  for (const name of ALL_RESERVED) {
    if (seen.has(name)) errors.push("duplicate:" + name);
    seen.add(name);
    if (name !== name.toLowerCase()) errors.push("not_lowercase:" + name);
  }
  // 清單裡的名字如果本身結構不合法,它永遠不會被拿來比對 —— 那是一條死規則。
  for (const name of ALL_RESERVED) {
    const structural = structuralIssues(name).filter((i) => i !== "name_too_short");
    if (structural.length) errors.push("unreachable_entry:" + name + ":" + structural.join(","));
  }
  return {
    ok: errors.length === 0,
    errors: Object.freeze(errors),
    count: ALL_RESERVED.length
  };
}

module.exports = {
  VERSION,
  MIN_LENGTH,
  MAX_LENGTH,
  PROTOCOL_NAMES,
  INFRA_NAMES,
  PLATFORM_NAMES,
  PRODUCT_NAMES,
  LEGAL_NAMES,
  BRAND_NAMES,
  ALL_RESERVED,
  SHOWCASE_PREFIX,
  SHOWCASE_SLUGS,
  LEGACY_DEMO_SLUGS,
  isShowcaseSlug,
  isReservedPrefix,
  structuralIssues,
  isReservedWord,
  checkSubdomain,
  checkPathSegment,
  auditReservedNames
};
