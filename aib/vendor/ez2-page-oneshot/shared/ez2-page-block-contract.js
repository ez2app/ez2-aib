"use strict";

const pageDomain = require("./ez2-page-domain-contract.js");
// 317 第 6 項:link 的小圖示可以是 ui-glyphs 的一個名字或社群平台的一個鍵——兩張表都是零相依的字面表,契約直接問它們,不另抄一份。
const uiGlyphs = require("./ez2-page-ui-glyphs.js");
const socialIcons = require("./ez2-page-social-icons.js");

const VERSION = "2026-07-11.ez2-page-block.v1";
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$/;
// 十一種區塊語彙。後六種(music/video/shop/event/mail/tip)原本只活在設計 mock,
// 現在有契約=編輯器與渲染器可以真的消費它們。共通紀律沿用前五種:文字零 HTML、
// 外連只收 https、金流與敏感欄位一律不進 payload(見 SENSITIVE_FIELD_RE 與
// PAYMENT_FIELD_RE)——區塊是內容不是收銀台。
const SHOP_AVAILABILITY = Object.freeze(new Set(["in_stock", "out_of_stock", "preorder"]));
// 289 五(RL-092/093/094):第十二種 place = Google 商家格(地圖縮圖經本站 /a/、名稱/地址/星數/營業時間/導航/打電話/留評論/前三則評論)。
// 317 第 5 項(RL-115):第十三、十四種 heading(標題,只准 h2/h3——名字是頁上唯一 h1)與 divider(分隔線)。
// 317 第 10 項(RL-115 第二批):第十六種 faq = 摺疊問答(原生 <details>,零腳本)。
// page/aib-oneshot-0926:第二十三種 link_group = 摺疊連結夾(原生 <details>/<summary>,零腳本;items 同 link 安全:https only)。
const TYPES = Object.freeze(["text", "image", "link", "cta", "form", "music", "video", "shop", "event", "mail", "tip", "place", "heading", "divider", "socials", "faq", "testimonial", "countdown", "booking", "file", "qr", "gallery", "link_group", "profile"]);
// 317 第 14 項:第二十一種 qr = 自己頁的 QR(payload **沒有網址欄**——永遠是 canonical,店主不能拿它指到別處;零外連,自寫編碼器 ez2-page-qr.js)。
// 317 第 12 項:第二十種 file = 檔案下載(PDF;資產第二種,asset-storage.storeFile)。
const FILE_SIZE_MAX = 10 * 1024 * 1024;
// 317 第 4 項:相簿 2–12 張、兩種排法
const GALLERY_ITEMS_MIN = 2, GALLERY_ITEMS_MAX = 12;
const GALLERY_STYLES = Object.freeze(["carousel", "grid"]);
// 317 第 13 項:第十九種 booking = 預約 / 行事曆卡(網址卡:公開頁零腳本跑不了 open_meeting;A36 現查 ez2-meeting 沒有公開預約網址 ⇒ 第一階段只有 external)。
const BOOKING_PROVIDERS = new Set(["external"]);
const BOOKING_DURATION_MIN = 5, BOOKING_DURATION_MAX = 480;
// 317 第 11 項(RL-124 乙:靜態天數不跳秒):第十八種 countdown = 倒數(伺服器算整天數,零腳本)。
// 317 第 9 項:第十七種 testimonial = 自家見證 / 評論(Google 的在 place 顆)。
const FAQ_ITEMS_MAX = 12;
const FAQ_Q_MAX = 120;
const FAQ_A_MAX = 1000;
// link_group:標題短字、連結列 1–12(上限對齊 FAQ_ITEMS_MAX);label 上限同 link。
const LINK_GROUP_ITEMS_MAX = FAQ_ITEMS_MAX;
const LINK_GROUP_TITLE_MAX = 80;
const LINK_GROUP_LABEL_MAX = 300;
const HEADING_LEVELS = Object.freeze([2, 3]);
const HEADING_ALIGNS = Object.freeze(["left", "center"]);
const DIVIDER_STYLES = Object.freeze(["line", "space", "dots"]);
// 317 第 7 項(RL-120 代裁:每頁上限 1):重點連結動效 pulse(既有 breathe)/ grow(新 keyframe,只動 transform)。
const LINK_EMPHASIS = Object.freeze(["pulse", "grow"]);
const LINK_EMPHASIS_MAX = 1;
// 316 v1.1(RL-114/117 甲):第十五種 socials = 社群列當獨立區塊。資料的唯一來源仍是 profile.social_links,區塊只是「放哪裡」——
//   payload **必須是 {}**(帶任何鍵 ⇒ page_block_socials_invalid);每頁至多一顆(第二顆 ⇒ page_blocks_socials_duplicate,hidden 的也算)。
// page/aib-oneshot-0926(+profile slot):第 24 種 profile = 頭像/名字/自介/狀態(+封面)當可排序區塊。payload **必須是 {}**;
//   每頁至多一顆(第二顆 ⇒ page_blocks_profile_duplicate,hidden 的也算)。有 visible profile ⇒ 渲染器不在頁首 prepend identity,改在該顆位置畫。
const PLACE_ID_RE = /^[A-Za-z0-9_-]{1,255}$/;
const PLACE_KEYS = new Set(["place_id", "name", "address", "phone", "hours", "rating", "review_count", "reviews", "map_asset_id", "photo_asset_ids", "maps_url", "nav_url", "review_url", "show_review_button"]);
const PLACE_REVIEW_KEYS = new Set(["author", "rating", "text", "at"]);
const PLACE_REVIEWS_MAX = 3;
const PLACE_PHOTOS_MAX = 3;
// 只認 Google 自己的三個入口:導航 dir/?api=1、地圖頁、留評論 writereview——網址是資料不是自由欄,形狀不對整顆 422。
const PLACE_NAV_RE = /^https:\/\/www\.google\.com\/maps\/dir\/\?api=1&destination=place_id:[A-Za-z0-9_-]{1,255}$/;
const PLACE_MAPS_RE = /^https:\/\/(www\.google\.com\/maps\/|maps\.google\.com\/|maps\.app\.goo\.gl\/|goo\.gl\/maps\/)/;
const PLACE_REVIEW_URL_RE = /^https:\/\/search\.google\.com\/local\/writereview\?placeid=[A-Za-z0-9_-]{1,255}$/;
const TYPE_SET = new Set(TYPES);
const VISIBILITY = new Set(["public", "members", "hidden"]);
const FIELD_TYPES = new Set(["text", "email", "tel", "textarea", "checkbox"]);
const ACTION_TYPES = new Set(["open_url", "open_line", "open_meeting", "open_ai"]);
const REL_VALUES = new Set(["nofollow", "sponsored", "ugc", "noopener", "noreferrer"]);
const SENSITIVE_FIELD_RE = /(?:password|passwd|secret|token|otp|cvv|card|credit|bank|ssn|identity|身分證|信用卡|密碼)/i;
// 嵌入白名單:只認得出處明確的平台。開放任意 iframe 等於把客戶頁面交給第三方。
const MUSIC_PROVIDERS = new Set(["spotify", "apple_music", "youtube_music", "soundcloud", "bandcamp"]);
const VIDEO_PROVIDERS = new Set(["youtube", "vimeo", "bilibili"]);
// 276(b):影片外連的主機要與 provider 對得上——「provider: youtube」配一條別家網址,渲染端會畫出錯的播放器殼。
//   音樂不做這條:八格示範(RL-047 原稿 1:1)的音樂 href 是 example.com 佔位,而 spotify 一家有好幾個網域,先不圈。
const VIDEO_PROVIDER_HOSTS = Object.freeze({
  youtube: ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"],
  vimeo: ["vimeo.com", "www.vimeo.com", "player.vimeo.com"],
  bilibili: ["bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"]
});
function hrefHostMatches(href, hosts) {
  try { return hosts.includes(new URL(href).hostname.toLowerCase()); } catch (_) { return false; }
}
// 276(b):活動日期要落在說得通的範圍(2000 年到 50 年後);可以是未來——活動本來就多半在未來。
const EVENT_DATE_MIN_MS = Date.parse("2000-01-01T00:00:00Z");
const EVENT_DATE_MAX_YEARS = 50;
const SHOP_PROVIDERS = new Set(["shopee", "momo", "pinkoi", "woocommerce", "shopify", "shopline", "cyberbiz", "external"]);
const TIP_PROVIDERS = new Set(["buymeacoffee", "ko_fi", "patreon", "external"]);
const CURRENCIES = new Set(["TWD", "USD", "JPY", "KRW", "EUR"]);
// 抖內金額只收整數最小單位,且有上限——區塊不是金流,金額只是「建議選項」。
const TIP_AMOUNT_MAX = 1000000;
const COMMON_FIELDS = new Set(["schema_version", "tenant_id", "page_id", "block_id", "block_type", "version", "position", "visibility", "payload", "created_at", "updated_at", "span", "provenance", "schedule", "background", "theme_slot"]);
// 287-B 包二(RL-079 甲):區塊在 3×3 九宮裡占幾格——記錄層的可選欄 span:{cols,rows}(1–3 各自),不在 payload 裡(payload 的
//   exactKeys 各型別不動;示範簽章不含它 ⇒ 改尺寸不算改內容)。缺席 = 滿寬一列(今天的樣子);範圍外 ⇒ page_block_span_invalid。
const SPAN_MAX = 3;
function spanErrors(span, mutation) {
  if (span === undefined) return [];
  if (mutation === "allow-any-span") return [];
  if (!plain(span) || !exactKeys(span, new Set(["cols", "rows"])) ||
      !Number.isInteger(span.cols) || span.cols < 1 || span.cols > SPAN_MAX ||
      !Number.isInteger(span.rows) || span.rows < 1 || span.rows > SPAN_MAX) return ["page_block_span_invalid"];
  return [];
}

// 區塊底色(與 span 同層、不進 payload):缺席 = 跟套版;
//   {solid} | {custom:{c1,c2,dir}} | {image:{url,fit}} | {video:{url,fit,muted,loop}}。
//   dir 字面與 design-pack BACKGROUND_DIRS 對齊;preset 是頁級外觀、不算區塊底色。
//   image/video 的 fit = cover|contain(依區塊框);video 必須 muted===true 且 loop===true(自動播放政策)。
const BLOCK_BACKGROUND_DIRS = Object.freeze(["to bottom", "to top", "to right", "to left", "135deg", "45deg"]);
const BLOCK_BG_FITS = Object.freeze(["cover", "contain"]);
const BLOCK_BG_HEX_RE = /^#[0-9a-f]{6}$/;
function backgroundErrors(background, mutation) {
  if (background === undefined) return [];
  if (mutation === "allow-any-background") return [];
  if (!plain(background)) return ["page_block_background_invalid"];
  const keys = Object.keys(background);
  if (keys.length !== 1) return ["page_block_background_invalid"];
  if (keys[0] === "solid") return BLOCK_BG_HEX_RE.test(String(background.solid)) ? [] : ["page_block_background_invalid"];
  if (keys[0] === "custom") {
    const c = background.custom;
    if (!plain(c) || !exactKeys(c, new Set(["c1", "c2", "dir"]))) return ["page_block_background_invalid"];
    return BLOCK_BG_HEX_RE.test(String(c.c1)) && BLOCK_BG_HEX_RE.test(String(c.c2)) && BLOCK_BACKGROUND_DIRS.includes(c.dir)
      ? [] : ["page_block_background_invalid"];
  }
  if (keys[0] === "image") {
    const im = background.image;
    if (!plain(im) || !exactKeys(im, new Set(["url", "fit"]))) return ["page_block_background_invalid"];
    return safeHref(im.url, mutation) && BLOCK_BG_FITS.includes(im.fit) ? [] : ["page_block_background_invalid"];
  }
  if (keys[0] === "video") {
    const v = background.video;
    if (!plain(v) || !exactKeys(v, new Set(["url", "fit", "muted", "loop"]))) return ["page_block_background_invalid"];
    // 影片底必須靜音循環;unmuted / 不 loop 一律拒(自動播放政策)。
    if (v.muted !== true || v.loop !== true) return ["page_block_background_invalid"];
    return safeHref(v.url, mutation) && BLOCK_BG_FITS.includes(v.fit) ? [] : ["page_block_background_invalid"];
  }
  return ["page_block_background_invalid"];
}

// S5:區塊 theme_slot（與 span / background 同層、不進 payload）——缺席 / null / "" = 繼承頁主題;
//   有值只准 card / elevated / accent_soft / inverse（與 static-render / theme-renderer 白名單同字面）。
//   禁自由 hex／任意字串 ⇒ page_block_theme_slot_invalid。換 theme_id 重染、不改這欄。
const THEME_SLOTS = Object.freeze(["card", "elevated", "accent_soft", "inverse"]);
const THEME_SLOT_SET = new Set(THEME_SLOTS);
function themeSlotErrors(theme_slot, mutation) {
  if (theme_slot === undefined || theme_slot === null || theme_slot === "") return [];
  if (mutation === "allow-any-theme-slot") return [];
  return THEME_SLOT_SET.has(String(theme_slot)) ? [] : ["page_block_theme_slot_invalid"];
}

// 289 〇 / 292 來源標示:由取回(link-in-bio / 網站 / oEmbed / Google …)產生的顆,記錄層多一個可選欄
//   provenance:{source, url, fetched_at, ttl_days}(不進 payload / exactKeys,與 span 同一種擺法 ⇒ 示範簽章、既有頁都不動)。
//   source = 轉接器名(小寫英數與 _ -,≤32);url = 抓的網址或 null;fetched_at = ISO 時刻;ttl_days = null(無限:用戶自己的東西)
//   或整數 0–3650(0 = 不存文,每次渲染取;>0 = 過了就過期 ⇒ 公開頁不畫、編輯器標「已過期,重抓」)。
// 289 (h) 搜尋回退:可選第五鍵 via(只准字面 "search")= 這顆是「用品牌名搜到、再走轉接器抓」的,不是店主自己給的網址;
//   來源行改印「AI 搜尋 → <source>」、編輯器與發布後多一句提醒(draftHasSearchSource)。別的值 ⇒ page_block_provenance_invalid。
const PROVENANCE_SOURCE_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const PROVENANCE_VIA_VALUES = Object.freeze(["search"]);
const PROVENANCE_KEYS = new Set(["source", "url", "fetched_at", "ttl_days", "via"]);
const PROVENANCE_TTL_MAX = 3650;
// 317 第 8 項(RL-120 代裁:時區先照台北):記錄層可選欄 schedule:{starts_at: ISO|null, ends_at: ISO|null}——任何型別都可排程。
//   兩個都 null / 缺席 = 永遠;時刻要帶時區(Z 或 ±hh:mm,沒帶 ⇒ 422:「10:00」在誰的時區沒有答案);ends_at ≤ starts_at ⇒ 422;
//   範圍同 event 日期(2000 年到 50 年後)。不進 payload ⇒ 示範簽章不變、既有頁不變(與 span / provenance 同一擺法)。
const SCHEDULE_TZ_RE = /(?:Z|[+-]\d\d:\d\d)$/;
function scheduleErrors(schedule, mutation) {
  if (schedule === undefined) return [];
  if (mutation === "allow-any-schedule") return [];
  const stamp = (value) => value === null || (date(value) && SCHEDULE_TZ_RE.test(value) && Date.parse(value) >= EVENT_DATE_MIN_MS && Date.parse(value) <= Date.now() + EVENT_DATE_MAX_YEARS * 365.25 * 86400000);
  if (!plain(schedule) || !exactKeys(schedule, new Set(["starts_at", "ends_at"])) || !("starts_at" in schedule) || !("ends_at" in schedule) ||
      !stamp(schedule.starts_at) || !stamp(schedule.ends_at) ||
      (schedule.starts_at !== null && schedule.ends_at !== null && Date.parse(schedule.ends_at) <= Date.parse(schedule.starts_at))) return ["page_block_schedule_invalid"];
  return [];
}
// 排程判準(三處 render 都問這一支,不各自算):沒有 schedule / 沒有時鐘 ⇒ 活;starts_at 之前 ⇒ 沒活;ends_at 起 ⇒ 沒活。
// 317 第 11 項:倒數用的台北整天數。兩個時刻各自換成台北日(YYYY-MM-DD)再相減;同一天 ⇒ 0;過了 ⇒ 負。日界用台北(RL-120/124 同一條)。
const TAIPEI_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" });
function taipeiDaysBetween(fromIso, toIso) {
  const from = Date.parse(fromIso), to = Date.parse(toIso);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  return Math.round((Date.parse(TAIPEI_DAY.format(new Date(to)) + "T00:00:00Z") - Date.parse(TAIPEI_DAY.format(new Date(from)) + "T00:00:00Z")) / 86400000);
}
function scheduleActive(schedule, nowIso) {
  if (!schedule || typeof schedule !== "object" || !nowIso) return true;
  const now = Date.parse(nowIso);
  if (!Number.isFinite(now)) return true;
  if (schedule.starts_at && now < Date.parse(schedule.starts_at)) return false;
  if (schedule.ends_at && now >= Date.parse(schedule.ends_at)) return false;
  return true;
}
// 排程狀態(編輯器 / 預覽標記用):pending(還沒到)/ active / expired(已過)
function scheduleState(schedule, nowIso) {
  if (!schedule || typeof schedule !== "object" || !nowIso) return "active";
  const now = Date.parse(nowIso);
  if (schedule.starts_at && now < Date.parse(schedule.starts_at)) return "pending";
  if (schedule.ends_at && now >= Date.parse(schedule.ends_at)) return "expired";
  return "active";
}
function provenanceErrors(provenance, mutation) {
  if (provenance === undefined) return [];
  if (mutation === "allow-any-provenance") return [];
  if (!plain(provenance) || !exactKeys(provenance, PROVENANCE_KEYS) ||
      typeof provenance.source !== "string" || !PROVENANCE_SOURCE_RE.test(provenance.source) ||
      !(provenance.via === undefined || PROVENANCE_VIA_VALUES.includes(provenance.via)) ||
      !(provenance.url === null || (typeof provenance.url === "string" && /^https?:\/\//.test(provenance.url) && provenance.url.length <= 2048)) ||
      !date(provenance.fetched_at) ||
      !(provenance.ttl_days === null || (Number.isInteger(provenance.ttl_days) && provenance.ttl_days >= 0 && provenance.ttl_days <= PROVENANCE_TTL_MAX))) return ["page_block_provenance_invalid"];
  return [];
}
// 過期判準(三處 render 與編輯器都問這一支,不各自算):ttl_days 是正整數且 fetched_at + ttl_days 天 < now ⇒ 過期。null / 0 永不過期。
function provenanceExpired(provenance, nowIso) {
  if (!plain(provenance) || !Number.isInteger(provenance.ttl_days) || provenance.ttl_days <= 0 || !date(provenance.fetched_at) || !date(nowIso)) return false;
  return Date.parse(provenance.fetched_at) + provenance.ttl_days * 86400000 < Date.parse(nowIso);
}

function plain(value) { if (!value || typeof value !== "object" || Array.isArray(value)) return false; const proto = Object.getPrototypeOf(value); return proto === Object.prototype || proto === null; }
function id(value) { return typeof value === "string" && ID_RE.test(value); }
function date(value) { return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value)); }
// IANA 時區名的驗證走 Intl 的支援清單(Node 18+ 有 supportedValuesOf);沒有清單的
// 執行環境退回「Intl 建得出這個時區的格式器」。兩條都不是字面正則——時區名是
// 一張會變的表,自己維護一份就是第二真相源。
const EVENT_DEFAULT_TIMEZONE = "Asia/Taipei";
// 動作鈕/連結卡副標的上限:設計上是一行 9.5–10.5px 的小字,60 字已經是兩行。
const SUBLABEL_MAX = 60;
// RL-144 A51(2026-09-18):shop / event 可選 description(標題下一段說明;300 = 與 title 同上限,walk safeText)。
const DESCRIPTION_MAX = 300;
const VIDEO_DURATION_MAX = 86400;
const SHOP_BUTTON_LABEL_MAX = 8;

// 片長的顯示形狀:mm:ss,滿一小時 h:mm:ss(mock 角標是 3:24)。兩支渲染器共用。
function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), sec = seconds % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h > 0 ? h + ":" + two(m) + ":" + two(sec) : m + ":" + two(sec);
}
// schema.org 的 duration 是 ISO 8601:PT3M24S。
function isoDuration(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), sec = seconds % 60;
  return "PT" + (h ? h + "H" : "") + (m ? m + "M" : "") + (sec || (!h && !m) ? sec + "S" : "");
}
const SUPPORTED_TIMEZONES = typeof Intl.supportedValuesOf === "function" ? new Set(Intl.supportedValuesOf("timeZone")) : null;
function validTimezone(value) {
  if (typeof value !== "string" || !value || value.length > 64) return false;
  if (SUPPORTED_TIMEZONES) return SUPPORTED_TIMEZONES.has(value);
  try { new Intl.DateTimeFormat("en-US", { timeZone: value }); return true; } catch (error) { return false; }
}
function exactKeys(value, allowed) { return plain(value) && Object.keys(value).every((key) => allowed.has(key)); }
function safeText(value, max, mutation) {
  return typeof value === "string" && value.length > 0 && value.length <= max && !value.includes("\0") && (mutation === "allow-html" || !/<\/?[A-Za-z!][^>]*>/.test(value));
}
function safeHref(value, mutation) {
  if (mutation === "allow-unsafe-href") return typeof value === "string" && value.length > 0;
  if (typeof value !== "string" || value.length < 1 || value.length > 2048 || /[\u0000-\u001f]/.test(value)) return false;
  if (value.startsWith("/")) return !value.startsWith("//") && !/[?#].*(?:token|secret|password)=/i.test(value) && !/(?:^|\/)\.\.(?:\/|$)/.test(value);
  try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password; } catch (_) { return false; }
}

// 317 第 6 項:link.icon 的三種形狀。glyph 名與平台鍵各自問自己那張表(uiGlyphs.has / socialIcons.has),不認的名字就是紅。
function linkIconOk(icon) {
  if (!plain(icon)) return false;
  const keys = Object.keys(icon);
  if (keys.length !== 1) return false;
  if (keys[0] === "glyph") return typeof icon.glyph === "string" && uiGlyphs.has(icon.glyph);
  if (keys[0] === "platform") return typeof icon.platform === "string" && socialIcons.has(icon.platform);
  if (keys[0] === "asset_id") return id(icon.asset_id);
  return false;
}
function payloadErrors(block, mutation) {
  const p = block.payload;
  const errors = [];
  if (!plain(p)) return ["page_block_payload_invalid"];
  if (block.block_type === "text") {
    if (!exactKeys(p, new Set(["text", "format"])) || !safeText(p.text, 20000, mutation) || !["plain", "markdown"].includes(p.format)) errors.push("page_block_text_invalid");
  } else if (block.block_type === "image") {
    if (!exactKeys(p, new Set(["asset_id", "alt", "href"])) || !id(p.asset_id) || typeof p.alt !== "string" || p.alt.length > 500 || (p.href != null && !safeHref(p.href, mutation))) errors.push("page_block_image_invalid");
  } else if (block.block_type === "link") {
    // sublabel 選填:mock 連結卡第二行小字(作品集 / 2026 精選 12 件),上限 SUBLABEL_MAX。
    // 317 第 6 項:icon 選填 = null | {glyph} | {platform} | {asset_id}(三選一,各自 exactKeys);thumbnail_asset_id 選填。
    //   兩個鍵缺席 = 今天的樣子:示範簽章不含缺席的鍵,既有頁與示範顆身分零變。
    if (!exactKeys(p, new Set(["label", "href", "rel", "sublabel", "icon", "thumbnail_asset_id", "emphasis"])) || !safeText(p.label, 300, mutation) || !safeHref(p.href, mutation) || !Array.isArray(p.rel) || !p.rel.every((value) => REL_VALUES.has(value)) || new Set(p.rel).size !== p.rel.length ||
      (p.sublabel != null && !safeText(p.sublabel, SUBLABEL_MAX, mutation)) ||
      (p.icon != null && !linkIconOk(p.icon)) ||
      (p.thumbnail_asset_id != null && !id(p.thumbnail_asset_id)) ||
      (p.emphasis != null && !LINK_EMPHASIS.includes(p.emphasis))) errors.push("page_block_link_invalid");
  } else if (block.block_type === "cta") {
    const action = p.action;
    // sublabel 選填:mock 主鈕下那行小字(「馬上開聊」「30 分鐘」),上限 SUBLABEL_MAX。
    if (!exactKeys(p, new Set(["label", "action", "sublabel"])) || !safeText(p.label, 300, mutation) || !exactKeys(action, new Set(["type", "href", "target_id"])) || !ACTION_TYPES.has(action.type) ||
      (p.sublabel != null && !safeText(p.sublabel, SUBLABEL_MAX, mutation))) errors.push("page_block_cta_invalid");
    else if (action.type === "open_url" ? !safeHref(action.href, mutation) || action.target_id != null : !id(action.target_id) || action.href != null) errors.push("page_block_cta_target_invalid");
  } else if (block.block_type === "form") {
    if (!exactKeys(p, new Set(["form_id", "title", "fields"])) || !id(p.form_id) || !safeText(p.title, 300, mutation) || !Array.isArray(p.fields) || p.fields.length < 1 || p.fields.length > 20) errors.push("page_block_form_invalid");
    else {
      const ids = new Set();
      for (const field of p.fields) {
        if (!exactKeys(field, new Set(["field_id", "label", "type", "required"])) || !id(field.field_id) || !safeText(field.label, 300, mutation) || !FIELD_TYPES.has(field.type) || typeof field.required !== "boolean") errors.push("page_block_form_field_invalid");
        if (mutation !== "allow-sensitive-field" && (SENSITIVE_FIELD_RE.test(field.field_id || "") || SENSITIVE_FIELD_RE.test(field.label || ""))) errors.push("page_block_form_sensitive_field_forbidden");
        if (ids.has(field.field_id)) errors.push("page_block_form_field_duplicate");
        ids.add(field.field_id);
      }
    }
  } else if (block.block_type === "music") {
    // 音樂:提供者白名單 + 平台上的曲目識別碼;不存嵌入 HTML,渲染端自己組。
    // sublabel 選填:mock 副標那行(本週靈感歌單 · Podcast);沒給渲染端印提供者品牌名。
    if (!exactKeys(p, new Set(["provider", "track_id", "title", "href", "sublabel"])) || !MUSIC_PROVIDERS.has(p.provider) ||
      !id(p.track_id) || !safeText(p.title, 300, mutation) || !safeHref(p.href, mutation) ||
      (p.sublabel != null && !safeText(p.sublabel, SUBLABEL_MAX, mutation))) errors.push("page_block_music_invalid");
  } else if (block.block_type === "video") {
    // Google 的 VideoObject 必要屬性是 name + thumbnailUrl + uploadDate。缺任何
    // 一個整段標記作廢,所以契約要收得到這兩樣;收不到就不輸出,不是輸出殘缺的。
    // duration_seconds 選填:整數秒(1 秒到 24 小時),給 mock 的 .du 角標(3:24)與
    // VideoObject.duration(PT3M24S)。存秒不存字串:mm:ss 是顯示形狀,不是資料。
    if (!exactKeys(p, new Set(["provider", "video_id", "title", "href", "autoplay", "thumbnail_url", "uploaded_at", "description", "duration_seconds"])) || !VIDEO_PROVIDERS.has(p.provider) ||
      !id(p.video_id) || !safeText(p.title, 300, mutation) || !safeHref(p.href, mutation) || typeof p.autoplay !== "boolean" ||
      (p.thumbnail_url != null && !safeHref(p.thumbnail_url, mutation)) ||
      (p.uploaded_at != null && !date(p.uploaded_at)) ||
      (p.description != null && !safeText(p.description, 1000, mutation)) ||
      (p.duration_seconds != null && !(Number.isInteger(p.duration_seconds) && p.duration_seconds >= 1 && p.duration_seconds <= VIDEO_DURATION_MAX))) errors.push("page_block_video_invalid");
    // 自動播放預設必須是關:訪客的流量與注意力不是我們的。
    else if (p.autoplay === true && mutation !== "allow-autoplay") errors.push("page_block_video_autoplay_forbidden");
    else if (mutation !== "allow-any-video-host" && !hrefHostMatches(p.href, VIDEO_PROVIDER_HOSTS[p.provider] || [])) errors.push("page_block_video_href_provider_mismatch");
  } else if (block.block_type === "shop") {
    // 商品:只放展示欄位與一條外連;價格是「顯示用字串+幣別」,結帳永遠在對方站上。
    // price_amount 是給機器讀的數字,price_display 是給人看的字串。Google 的
    // Product 要 offers,而 offers 的 price 必須是數字——用顯示字串去湊會產出
    // 一個看起來合規、實際上金額解析不了的標記。兩者並存,顯示不受影響。
    // button_label 選填:膠囊字(mock 第二張卡是「預約」)。原樣印、不翻譯、上限 SHOP_BUTTON_LABEL_MAX;
    // 沒給才依 availability 印 購買/預購/售完。
    // description 選填(RL-144 A51):商品說明,印在標題下,上限 DESCRIPTION_MAX;沒給不印。
    if (!exactKeys(p, new Set(["provider", "product_id", "title", "href", "price_display", "price_amount", "currency", "availability", "image_asset_id", "button_label", "description"])) ||
      (p.button_label != null && !safeText(p.button_label, SHOP_BUTTON_LABEL_MAX, mutation)) ||
      (p.description != null && !safeText(p.description, DESCRIPTION_MAX, mutation)) ||
      !SHOP_PROVIDERS.has(p.provider) || !id(p.product_id) || !safeText(p.title, 300, mutation) || !safeHref(p.href, mutation) ||
      !safeText(p.price_display, 40, mutation) || !CURRENCIES.has(p.currency) ||
      (p.price_amount != null && !(typeof p.price_amount === "number" && Number.isFinite(p.price_amount) && p.price_amount >= 0 && p.price_amount <= 100000000)) ||
      (p.availability != null && !SHOP_AVAILABILITY.has(p.availability)) ||
      (p.image_asset_id != null && !id(p.image_asset_id))) errors.push("page_block_shop_invalid");
    // 276(b),RL-058(艦長 09-17 14:1x 裁):機器讀的金額收非負、**最多兩位小數**的數字(USD/EUR 的分可以;
    //   TWD/JPY/KRW 自然是整數)。9.999 這種第三位小數是輸入錯誤,不偷偷四捨五入。
    //   ★ 不能用 Math.round(p*100) !== p*100 那種浮點等號:0.07*100 = 7.000000000000001,0.01–1000.00 逐分掃有 9175 個被誤拒
    //     (root2 09-17 14:1x 複審抓到)。用 1e-6 的容差,9.999 仍差 0.1 ⇒ 422。
    else if (p.price_amount != null && Math.abs(p.price_amount * 100 - Math.round(p.price_amount * 100)) > 1e-6 && mutation !== "allow-fractional-price") errors.push("page_block_shop_price_too_precise");
  } else if (block.block_type === "event") {
    // timezone 選填(IANA 字串,如 Asia/Tokyo):日期塊「7/30 THU」要用哪個時區切日。
    // 沒帶=EVENT_DEFAULT_TIMEZONE(Asia/Taipei,Ez2 母市場;09-02 艦長裁決,不是 UTC)。
    // starts_at 仍是 UTC ISO——時區只影響人看的那兩行字,不影響機器讀的 datetime。
    // description 選填(RL-144 A51):活動文案,印在標題下,上限 DESCRIPTION_MAX。
    if (!exactKeys(p, new Set(["title", "starts_at", "ends_at", "location", "href", "timezone", "description"])) || !safeText(p.title, 300, mutation) ||
      (p.description != null && !safeText(p.description, DESCRIPTION_MAX, mutation)) ||
      !date(p.starts_at) || (p.ends_at != null && !date(p.ends_at)) ||
      (p.location != null && !safeText(p.location, 300, mutation)) || (p.href != null && !safeHref(p.href, mutation))) errors.push("page_block_event_invalid");
    else if (p.timezone != null && !validTimezone(p.timezone)) errors.push("page_block_event_timezone_invalid");
    // 結束早於開始的活動不是活動,是資料錯誤——不靜默接受再讓渲染端出怪畫面。
    else if (p.ends_at != null && Date.parse(p.ends_at) < Date.parse(p.starts_at)) errors.push("page_block_event_ends_before_starts");
    // 276(b):日期要落在 2000 年到 50 年後之間——1970 或 9999 年的活動是輸入錯誤(表單的 datetime 欄很容易掉一位)。
    else if (mutation !== "allow-any-event-date" && [p.starts_at, p.ends_at].some((value) => value != null && (Date.parse(value) < EVENT_DATE_MIN_MS || Date.parse(value) > Date.now() + EVENT_DATE_MAX_YEARS * 365.25 * 86400000))) errors.push("page_block_event_date_out_of_range");
  } else if (block.block_type === "mail") {
    // 電子報訂閱:只收 Email 一個欄位,而且同意文字必填——收信箱就是收個資。
    if (!exactKeys(p, new Set(["list_id", "title", "consent_text", "button_label"])) || !id(p.list_id) ||
      !safeText(p.title, 300, mutation) || !safeText(p.consent_text, 1000, mutation) ||
      !safeText(p.button_label, 100, mutation)) errors.push("page_block_mail_invalid");
    else if (p.consent_text.trim().length < 10 && mutation !== "allow-thin-consent") errors.push("page_block_mail_consent_too_thin");
  } else if (block.block_type === "place") {
    errors.push(...placeErrors(p, mutation));
  } else if (block.block_type === "qr") {
    if (!exactKeys(p, new Set(["caption"])) || (p.caption != null && !safeText(p.caption, 40, mutation))) errors.push("page_block_qr_invalid");
  } else if (block.block_type === "gallery") {
    // 317 第 4 項(SPEC-317-batch1 §五):相簿 / 輪播——items 2–12 張 {asset_id, alt, href|null},style carousel | grid。
    //   asset 在不在本頁目錄由渲染器 assets.has 決定畫不畫(與 image 同一條);alt 可空字串(裝飾圖)但鍵必須在;href 只收 https。
    //   契約層沒有突變刀(四個呼叫者各自不帶 mutation),張數那條由 gallery 尺的副本樹落檔刀 K2 砍(錨 = 下一行的張數判斷)⇒ B4 必紅
    const items = Array.isArray(p.items) ? p.items : null;
    if (!exactKeys(p, new Set(["items", "style"])) || !GALLERY_STYLES.includes(p.style) || !items ||
        (items.length < GALLERY_ITEMS_MIN || items.length > GALLERY_ITEMS_MAX) ||
        items.some((item) => !exactKeys(item, new Set(["asset_id", "alt", "href"])) || !id(item.asset_id) || typeof item.alt !== "string" || (item.alt !== "" && !safeText(item.alt, 120, mutation)) ||
          (item.href != null && !safeHref(item.href, mutation)))) errors.push("page_block_gallery_invalid");
  } else if (block.block_type === "file") {
    // asset_id 形狀同 image(是不是 pdf、在不在本頁目錄由渲染器 assets.kind 決定畫不畫——與 image / link icon 同一條,登債);label ≤80;size_bytes 整數 ≤10MB 選填;note ≤120 選填
    if (!exactKeys(p, new Set(["asset_id", "label", "size_bytes", "note"])) || !id(p.asset_id) || !safeText(p.label, 80, mutation) ||
        (p.size_bytes != null && !(Number.isInteger(p.size_bytes) && p.size_bytes >= 1 && p.size_bytes <= FILE_SIZE_MAX)) ||
        (p.note != null && !safeText(p.note, 120, mutation))) errors.push("page_block_file_invalid");
  } else if (block.block_type === "booking") {
    // href https(safeHref);title ≤80;sublabel ≤120、duration_minutes 5–480 整數、next_slot_text ≤40 選填(店主手填的短句,不接行事曆 API)
    if (!exactKeys(p, new Set(["provider", "href", "title", "sublabel", "duration_minutes", "next_slot_text"])) || !BOOKING_PROVIDERS.has(p.provider) || !safeHref(p.href, mutation) ||
        !safeText(p.title, 80, mutation) || (p.sublabel != null && !safeText(p.sublabel, 120, mutation)) ||
        (p.duration_minutes != null && !(Number.isInteger(p.duration_minutes) && p.duration_minutes >= BOOKING_DURATION_MIN && p.duration_minutes <= BOOKING_DURATION_MAX)) ||
        (p.next_slot_text != null && !safeText(p.next_slot_text, 40, mutation))) errors.push("page_block_booking_invalid");
  } else if (block.block_type === "countdown") {
    // title ≤80;target_at ISO 帶時區(範圍同 event 日期);done_text ≤80 選填(過了印它;沒有 ⇒ 整顆不畫)
    if (!exactKeys(p, new Set(["title", "target_at", "done_text"])) || !safeText(p.title, 80, mutation) ||
        !(date(p.target_at) && SCHEDULE_TZ_RE.test(p.target_at) && Date.parse(p.target_at) >= EVENT_DATE_MIN_MS && Date.parse(p.target_at) <= Date.now() + EVENT_DATE_MAX_YEARS * 365.25 * 86400000) ||
        (p.done_text != null && !safeText(p.done_text, 80, mutation))) errors.push("page_block_countdown_invalid");
  } else if (block.block_type === "testimonial") {
    // quote ≤300 / author ≤80 必填;role ≤80、rating 1–5 整數、avatar_asset_id(形狀同 image;在不在本頁目錄由渲染器 assets.has 決定畫不畫,與 image / link icon 同一條)選填
    if (!exactKeys(p, new Set(["quote", "author", "role", "rating", "avatar_asset_id"])) || !safeText(p.quote, 300, mutation) || !safeText(p.author, 80, mutation) ||
        (p.role != null && !safeText(p.role, 80, mutation)) ||
        (p.rating != null && !(Number.isInteger(p.rating) && p.rating >= 1 && p.rating <= 5)) ||
        (p.avatar_asset_id != null && !id(p.avatar_asset_id))) errors.push("page_block_testimonial_invalid");
  } else if (block.block_type === "faq") {
    // items 1–12,每題 {q ≤120, a ≤1000}(safeText:有 HTML 標籤就是紅,不是逸出——問答是店主打的字);open_first 布林。
    if (!exactKeys(p, new Set(["items", "open_first"])) || !Array.isArray(p.items) || p.items.length < 1 || p.items.length > FAQ_ITEMS_MAX || typeof p.open_first !== "boolean" ||
        !p.items.every((item) => plain(item) && exactKeys(item, new Set(["q", "a"])) && safeText(item.q, FAQ_Q_MAX, mutation) && safeText(item.a, FAQ_A_MAX, mutation))) errors.push("page_block_faq_invalid");
  } else if (block.block_type === "link_group") {
    // title ≤80;open 布林(預設 false 由編輯器/oneshot 填,契約要求鍵在);items 1–12,{label,href}——https only、零 HTML(同 link)。
    if (!exactKeys(p, new Set(["title", "open", "items"])) || !safeText(p.title, LINK_GROUP_TITLE_MAX, mutation) || typeof p.open !== "boolean" ||
        !Array.isArray(p.items) || p.items.length < 1 || p.items.length > LINK_GROUP_ITEMS_MAX ||
        !p.items.every((item) => plain(item) && exactKeys(item, new Set(["label", "href"])) && safeText(item.label, LINK_GROUP_LABEL_MAX, mutation) && safeHref(item.href, mutation))) errors.push("page_block_link_group_invalid");
  } else if (block.block_type === "heading") {
    // level 只准 2/3:h1 是名字的,第二個 h1 會讓搜尋引擎與朗讀器搞不清這頁在講誰(static-render / theme-renderer 的「恰一個 h1」格守著)。
    if (!exactKeys(p, new Set(["text", "level", "align"])) || !safeText(p.text, 80, mutation) || !HEADING_LEVELS.includes(p.level) || !HEADING_ALIGNS.includes(p.align)) errors.push("page_block_heading_invalid");
  } else if (block.block_type === "divider") {
    if (!exactKeys(p, new Set(["style"])) || !DIVIDER_STYLES.includes(p.style)) errors.push("page_block_divider_invalid");
  } else if (block.block_type === "socials") {
    if (!plain(p) || Object.keys(p).length !== 0) errors.push("page_block_socials_invalid");
  } else if (block.block_type === "profile") {
    if (!plain(p) || Object.keys(p).length !== 0) errors.push("page_block_profile_invalid");
  } else if (block.block_type === "tip") {
    if (!exactKeys(p, new Set(["provider", "href", "title", "suggested_amounts", "currency"])) || !TIP_PROVIDERS.has(p.provider) ||
      !safeHref(p.href, mutation) || !safeText(p.title, 300, mutation) || !CURRENCIES.has(p.currency) ||
      !Array.isArray(p.suggested_amounts) || p.suggested_amounts.length > 6) errors.push("page_block_tip_invalid");
    else if (!p.suggested_amounts.every((amount) => Number.isInteger(amount) && amount > 0 && amount <= TIP_AMOUNT_MAX)) errors.push("page_block_tip_amount_invalid");
  }
  return errors;
}

// 289 五 place 的 payload:必填 place_id/name/address/map_asset_id/maps_url/nav_url/show_review_button;可選欄缺席或 null 都算沒有。
//   hours 恰 7 條(週一起)每條 ≤60 字或 null(休);rating 0–5 一位小數;reviews ≤3 每則 {author,rating,text,at};photo_asset_ids ≤3。
//   沒有突變刀(四個呼叫者各自不帶突變);反向證據 = 落檔刀(本函式改回 [] ⇒ place-block-smoke G2 必紅)。
function placeErrors(p, mutation) {
  const bad = ["page_block_place_invalid"];
  if (!plain(p) || Object.keys(p).some((key) => !PLACE_KEYS.has(key))) return bad;
  const optional = (key) => p[key] === undefined || p[key] === null;
  if (typeof p.place_id !== "string" || !PLACE_ID_RE.test(p.place_id) || !safeText(p.name, 200, mutation) || !safeText(p.address, 500, mutation) ||
      !id(p.map_asset_id) || typeof p.maps_url !== "string" || !PLACE_MAPS_RE.test(p.maps_url) || p.maps_url.length > 2048 ||
      typeof p.nav_url !== "string" || !PLACE_NAV_RE.test(p.nav_url) || typeof p.show_review_button !== "boolean") return bad;
  if (!optional("phone") && !(typeof p.phone === "string" && /^\+?[0-9() -]{3,30}$/.test(p.phone))) return bad;
  if (!optional("hours") && !(Array.isArray(p.hours) && p.hours.length === 7 && p.hours.every((h) => h === null || (typeof h === "string" && h.length <= 60 && !/[\u0000-\u001f<>]/.test(h))))) return bad;
  if (!optional("rating") && !(typeof p.rating === "number" && p.rating >= 0 && p.rating <= 5 && Math.round(p.rating * 10) === p.rating * 10)) return bad;
  if (!optional("review_count") && !(Number.isInteger(p.review_count) && p.review_count >= 0)) return bad;
  if (!optional("reviews") && !(Array.isArray(p.reviews) && p.reviews.length <= PLACE_REVIEWS_MAX && p.reviews.every((r) => plain(r) && exactKeys(r, PLACE_REVIEW_KEYS) &&
      safeText(r.author, 100, mutation) && Number.isInteger(r.rating) && r.rating >= 1 && r.rating <= 5 && safeText(r.text, 1000, mutation) && date(r.at)))) return bad;
  if (!optional("photo_asset_ids") && !(Array.isArray(p.photo_asset_ids) && p.photo_asset_ids.length <= PLACE_PHOTOS_MAX && p.photo_asset_ids.every(id))) return bad;
  if (!optional("review_url") && !(typeof p.review_url === "string" && PLACE_REVIEW_URL_RE.test(p.review_url))) return bad;
  if (p.show_review_button === true && optional("review_url")) return ["page_block_place_review_url_required"];
  return [];
}

function validatePageBlock(block, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  if (!plain(block)) return { ok: false, errors: ["page_block_record_invalid"] };
  if (Object.keys(block).some((key) => !COMMON_FIELDS.has(key))) errors.push("page_block_unknown_fields");
  if (block.schema_version !== VERSION) errors.push("page_block_schema_version_invalid");
  for (const field of ["tenant_id", "page_id", "block_id"]) if (!id(block[field])) errors.push("page_block_" + field + "_invalid");
  if (!TYPE_SET.has(block.block_type)) errors.push("page_block_type_invalid");
  if (!Number.isInteger(block.version) || block.version < 1) errors.push("page_block_version_invalid");
  if (!Number.isInteger(block.position) || block.position < 0) errors.push("page_block_position_invalid");
  if (!VISIBILITY.has(block.visibility)) errors.push("page_block_visibility_invalid");
  if (!date(block.created_at) || !date(block.updated_at)) errors.push("page_block_timestamp_invalid");
  if (date(block.created_at) && date(block.updated_at) && Date.parse(block.updated_at) < Date.parse(block.created_at)) errors.push("page_block_updated_before_created");
  if (TYPE_SET.has(block.block_type)) errors.push(...payloadErrors(block, mutation));
  errors.push(...spanErrors(block.span, mutation));
  errors.push(...backgroundErrors(block.background, mutation));
  errors.push(...provenanceErrors(block.provenance, mutation));
  errors.push(...scheduleErrors(block.schedule, mutation));   // 317 第 8 項
  errors.push(...themeSlotErrors(block.theme_slot, mutation));   // S5 theme_slot
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

function validatePageBlockSet(page, blocks, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  const pageResult = pageDomain.validatePageRecord(page);
  if (!pageResult.ok) errors.push(...pageResult.errors.map((error) => "page:" + error));
  if (!Array.isArray(blocks)) return { ok: false, errors: [...errors, "page_blocks_invalid"] };
  for (const block of blocks) {
    const result = validatePageBlock(block, options);
    if (!result.ok) errors.push(...result.errors.map((error) => (block && block.block_id || "unknown") + ":" + error));
  }
  if (errors.length) return { ok: false, errors: [...new Set(errors)] };
  if (mutation !== "allow-cross-tenant-block" && blocks.some((block) => block.tenant_id !== page.tenant_id || block.page_id !== page.page_id)) errors.push("page_blocks_cross_tenant_or_page");
  if (new Set(blocks.map((block) => block.block_id)).size !== blocks.length) errors.push("page_blocks_duplicate_id");
  // 317 第 7 項:重點連結每頁最多 LINK_EMPHASIS_MAX 顆(只數 public;藏起來的不算——「重點」多了就沒有重點)。
  //   ★ 沒有突變刀:整組驗有四個呼叫者(workspace / 頁庫 / 兩支 render)各自不帶突變,砍一層另一層補回(量過:workspace 帶刀,頁庫那層照擋);
  //     反向證據是 emphasis-smoke --knives 的副本樹落檔刀 K2(把下面這行的錯誤清掉 ⇒ B7 必紅),機器每次量。
  if (blocks.filter((block) => block.block_type === "link" && block.visibility === "public" && block.payload && block.payload.emphasis).length > LINK_EMPHASIS_MAX) errors.push("page_block_emphasis_limit");
  if (blocks.filter((block) => block.block_type === "socials").length > 1) errors.push("page_blocks_socials_duplicate");
  if (blocks.filter((block) => block.block_type === "profile").length > 1) errors.push("page_blocks_profile_duplicate");
  const ordered = [...blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id));
  if (new Set(ordered.map((block) => block.position)).size !== ordered.length) errors.push("page_blocks_duplicate_position");
  if (mutation !== "allow-block-id-mismatch" && (ordered.length !== page.block_ids.length || ordered.some((block, index) => block.block_id !== page.block_ids[index]))) errors.push("page_blocks_identity_order_mismatch");
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

function validatePageBlockTransition(previous, next, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  const before = validatePageBlock(previous, options), after = validatePageBlock(next, options);
  errors.push(...before.errors.map((error) => "previous:" + error), ...after.errors.map((error) => "next:" + error));
  if (!before.ok || !after.ok) return { ok: false, errors: [...new Set(errors)] };
  for (const field of ["tenant_id", "page_id", "block_id", "created_at"]) if (previous[field] !== next[field]) errors.push("page_block_transition_identity_changed");
  if (mutation !== "allow-type-change" && previous.block_type !== next.block_type) errors.push("page_block_transition_type_changed");
  if (next.version !== previous.version + 1) errors.push("page_block_transition_version_invalid");
  if (Date.parse(next.updated_at) <= Date.parse(previous.updated_at)) errors.push("page_block_transition_time_not_advanced");
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}




// 影片封面:明確 thumbnail_url 優先;YouTube 沒給就用官方 hqdefault(渲染端與存檔共用)。
// Vimeo / bilibili 沒有穩定免金鑰靜態圖 — 回 null,渲染端改畫站內佔位封面,仍保證「有縮圖」。
function videoPosterUrl(payload) {
  const p = payload && typeof payload === "object" ? payload : {};
  if (typeof p.thumbnail_url === "string" && /^https:\/\//i.test(p.thumbnail_url)) return p.thumbnail_url;
  // id() 與契約同形(含測試用短 id);正式 YouTube id 多半 11 字。
  if (p.provider === "youtube" && id(p.video_id)) {
    return "https://i.ytimg.com/vi/" + p.video_id + "/hqdefault.jpg";
  }
  return null;
}

module.exports = {
  FILE_SIZE_MAX, GALLERY_ITEMS_MIN, GALLERY_ITEMS_MAX, GALLERY_STYLES, BOOKING_PROVIDERS, BOOKING_DURATION_MIN, BOOKING_DURATION_MAX, FAQ_ITEMS_MAX, FAQ_Q_MAX, FAQ_A_MAX, LINK_GROUP_ITEMS_MAX, LINK_GROUP_TITLE_MAX, LINK_GROUP_LABEL_MAX, HEADING_LEVELS, HEADING_ALIGNS, DIVIDER_STYLES, LINK_EMPHASIS, LINK_EMPHASIS_MAX, linkIconOk, scheduleActive, scheduleErrors, scheduleState, taipeiDaysBetween,
  SHOP_AVAILABILITY,
  MUSIC_PROVIDERS, VIDEO_PROVIDERS, SHOP_PROVIDERS, TIP_PROVIDERS, CURRENCIES, TIP_AMOUNT_MAX, ACTION_TYPES, FIELD_TYPES, TYPES, VERSION, PLACE_KEYS, PLACE_REVIEWS_MAX, PLACE_PHOTOS_MAX, EVENT_DEFAULT_TIMEZONE, SUBLABEL_MAX, DESCRIPTION_MAX, VIDEO_DURATION_MAX, SHOP_BUTTON_LABEL_MAX, formatDuration, isoDuration, validTimezone, safeHref, videoPosterUrl, validatePageBlock, validatePageBlockSet, validatePageBlockTransition, SPAN_MAX, spanErrors, BLOCK_BACKGROUND_DIRS, BLOCK_BG_FITS, backgroundErrors, THEME_SLOTS, THEME_SLOT_SET, themeSlotErrors, PROVENANCE_TTL_MAX, PROVENANCE_VIA_VALUES, provenanceErrors, provenanceExpired };
