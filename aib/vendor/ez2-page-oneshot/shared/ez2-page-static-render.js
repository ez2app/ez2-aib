"use strict";

const domain = require("./ez2-page-domain-contract.js");
const blocks = require("./ez2-page-block-contract.js");
const structuredData = require("./ez2-page-structured-data.js");
// route_type 到結構化資料 tier 的對應,與 theme-renderer 用同一組值。
const TIER_BY_ROUTE_TYPE = Object.freeze({
  platform_path: "folder_bio", platform_subdomain: "subdomain_seo",
  custom_domain: "custom_commerce", legacy_redirect: "folder_bio", preview: "folder_bio"
});
const routes = require("./ez2-page-route-contract.js");
const assetStorage = require("./ez2-page-asset-storage.js");
const uiGlyphs = require("./ez2-page-ui-glyphs.js");   // 317 第 13 項:預約卡的行事曆 glyph
const i18n = require("./ez2-page-i18n.js");   // 317 第 18 項:表單鈕字面
const pageQr = require("./ez2-page-qr.js");   // 317 第 14 項:QR 編碼器(零相依)

// 資產路徑的形狀來自儲存權威(publicAssetUrl → /a/<id>),不在這裡寫死:
// 這支原本硬寫 '/assets/',跟主題渲染器(/a/)是兩條路——同一張圖在兩種頁上
// 一條活一條死。兩支渲染器從此共用這一個前綴(theme-renderer 從這裡 require)。
const ASSET_PATH_PREFIX = (() => {
  const probe = assetStorage.publicAssetUrl("host.example", "probe_asset_1");
  if (!probe.ok) throw new Error("PAGE_RENDER_ASSET_AUTHORITY_UNAVAILABLE");
  const path = new URL(probe.url).pathname;
  if (!path.endsWith("probe_asset_1")) throw new Error("PAGE_RENDER_ASSET_SHAPE_UNRECOGNIZED");
  return path.slice(0, -"probe_asset_1".length);
})();


// S5 theme_slot：白名單權威在 block-contract（COMMON_FIELDS + themeSlotErrors）；本檔只做解析／屬性／集合先驗。
// 缺席／undefined／null／"" = 繼承頁主題；自有底只准四槽，禁自由 hex（hex 仍走既有 background 舊路）。
const THEME_SLOTS = blocks.THEME_SLOTS;
const THEME_SLOT_SET = blocks.THEME_SLOT_SET;
function resolveThemeSlot(block) {
  if (!block || block.theme_slot === undefined || block.theme_slot === null || block.theme_slot === "") return null;
  const slot = String(block.theme_slot);
  return THEME_SLOT_SET.has(slot) ? slot : null;
}
function themeSlotAttr(block) {
  const slot = resolveThemeSlot(block);
  return slot ? ' data-theme-slot="' + attr(slot) + '"' : "";
}
function themeSlotErrors(blockList) {
  const errors = [];
  for (const block of blockList || []) {
    if (!block) continue;
    for (const code of blocks.themeSlotErrors(block.theme_slot)) {
      errors.push((block.block_id || "unknown") + ":" + code);
    }
  }
  return errors;
}
// 契約已收錄 theme_slot ⇒ 原樣交給 validatePageBlockSet（保留函式名給既有呼叫端）。
function blocksForContractValidation(blockList) {
  return blockList || [];
}

const SEO_FIELDS = new Set(["title", "description", "lang"]);
function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function escapeHtml(value) { return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
function attr(value) { return escapeHtml(value); }
function safeJson(value) { return JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026"); }
function exact(value, fields) { return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).every((key) => fields.has(key)); }

function validateSeo(seo, page) {
  const errors = [];
  if (!exact(seo, SEO_FIELDS)) return ["page_render_seo_invalid"];
  if (typeof seo.title !== "string" || !seo.title.trim() || seo.title.length > 120 || /[\u0000-\u001f]/.test(seo.title)) errors.push("page_render_title_invalid");
  if (typeof seo.description !== "string" || !seo.description.trim() || seo.description.length > 320 || /[\u0000-\u001f]/.test(seo.description)) errors.push("page_render_description_invalid");
  if (typeof seo.lang !== "string" || !/^[a-z]{2,3}(?:-[A-Z]{2})?$/.test(seo.lang)) errors.push("page_render_lang_invalid");
  if (!domain.PUBLISHED.has(page.status)) errors.push("page_render_unpublished");
  return errors;
}

function canonicalUrl(record, mutation) {
  const primary = mutation === "wrong-canonical-alias"
    ? record.routes.find((route) => !route.is_primary) || record.routes.find((route) => route.is_primary)
    : record.routes.find((route) => route.is_primary);
  if (!primary) fail("PAGE_RENDER_PRIMARY_ROUTE_MISSING");
  return "https://" + primary.hostname + primary.path;
}

// 音樂提供者的品牌寫法;與 theme-renderer 同一張表(品牌名不進 i18n)。
const MUSIC_PROVIDER_LABELS = Object.freeze({
  spotify: "Spotify", apple_music: "Apple Music", youtube_music: "YouTube Music", soundcloud: "SoundCloud", bandcamp: "Bandcamp"
});
// 電子報輸入框的提示字:mock 是「you@email.com — 每週四的宇宙情報」,前綴告訴訪客這格要填信箱。
const MAIL_PLACEHOLDER_PREFIX = "you@email.com — ";
// 商品卡膠囊字只反映 availability(契約沒有按鈕文案欄位);與 theme-renderer 同一張表。
const SHOP_PILL_LABELS = Object.freeze({ in_stock: "購買", preorder: "預購", out_of_stock: "售完" });

// 活動日期塊的兩行字:月/日(日補零,mock 是 7/30 與 8/03)與英文星期,用區塊的時區切日
// (沒帶=Asia/Taipei)。Intl 是確定性的:同一個 instant、同一個時區,任何機器同一個字。
// 兩支渲染器共用這一支,免得「同一場活動在兩種頁上是兩個日期」。
function eventDateParts(iso, timezone) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: timezone || blocks.EVENT_DEFAULT_TIMEZONE, month: "numeric", day: "2-digit", weekday: "short" }).formatToParts(new Date(iso));
  const pick = (type) => (parts.find((part) => part.type === type) || {}).value || "";
  return { md: pick("month") + "/" + pick("day"), wd: pick("weekday").toUpperCase() };
}

function renderText(value, mutation) {
  if (mutation === "unescaped-text") return String(value).replace(/\n/g, "<br>");
  return escapeHtml(value).replace(/\n/g, "<br>");
}

function renderBlock(block, mutation, assets) {
  // 公開快取頁只准 public。members 出現在 HTML 就是外洩,不是功能。
  // render-members 是測試突變,預設一律 public-only。刀在產生 HTML 的這一行。
  if (mutation === "render-members" ? block.visibility === "hidden" : block.visibility !== "public") return "";
  const p = block.payload;
  const common = ' data-block-id="' + attr(block.block_id) + '" data-block-type="' + attr(block.block_type) + '"' + themeSlotAttr(block);
  if (block.block_type === "text") return "<section" + common + "><p>" + renderText(p.text, mutation) + "</p></section>";
  if (block.block_type === "image") {
    // 同 theme-renderer:框永遠畫、img alt 空(載不到不出破圖)、說明字走 .pg-cap。
    const caption = p.alt ? '<span class="pg-cap">' + escapeHtml(p.alt) + "</span>" : "";
    const image = assets && typeof assets.has === "function" && !assets.has(p.asset_id) ? "" : '<img class="pg-img" src="' + ASSET_PATH_PREFIX + encodeURIComponent(p.asset_id) + '" alt="" loading="lazy" decoding="async">';
    const frame = '<div class="b-img">' + image + caption + "</div>";
    return '<figure class="pg-frame"' + common + ">" + (p.href ? '<a href="' + attr(p.href) + '">' + frame + "</a>" : frame) + "</figure>";
  }
  if (block.block_type === "link") return '<p' + common + '><a href="' + attr(p.href) + '" rel="' + attr(p.rel.join(" ")) + '">' + escapeHtml(p.label) + (p.sublabel ? "<small>" + escapeHtml(p.sublabel) + "</small>" : "") + "</a></p>";
  if (block.block_type === "cta") {
    if (p.action.type === "open_url") return '<p' + common + '><a data-cta href="' + attr(p.action.href) + '">' + escapeHtml(p.label) + (p.sublabel ? "<small>" + escapeHtml(p.sublabel) + "</small>" : "") + "</a></p>";
    return '<p' + common + '><button type="button" data-runtime-required="' + attr(p.action.type) + '" data-target-id="' + attr(p.action.target_id) + '" aria-disabled="true">' + escapeHtml(p.label) + "</button></p>";
  }
  if (block.block_type === "form") {
    const fields = p.fields.map((field) => '<label for="' + attr(block.block_id + "-" + field.field_id) + '">' + escapeHtml(field.label) + '</label><input id="' + attr(block.block_id + "-" + field.field_id) + '" name="' + attr(field.field_id) + '" type="' + attr(field.type === "textarea" ? "text" : field.type) + '"' + (field.required ? " required" : "") + " disabled>").join("");
    return '<section' + common + ' data-form-id="' + attr(p.form_id) + '" data-runtime-required="form-submit" aria-disabled="true"><h2>' + escapeHtml(p.title) + "</h2>" + fields + "</section>";
  }
  if (block.block_type === "music") {
    return '<a class="b-music"' + common + ' href="' + attr(p.href) + '" rel="noopener">' +
      '<div class="art" aria-hidden="true"><i></i><i></i><i></i></div>' +
      '<div class="m"><b>' + escapeHtml(p.title) + "</b><span>" + escapeHtml(p.sublabel || MUSIC_PROVIDER_LABELS[p.provider] || p.provider) + '</span><div class="bar" aria-hidden="true"><i></i></div></div>' +
      '<div class="pl" aria-hidden="true"><i></i></div></a>';
  }
  if (block.block_type === "video") {
    const du = p.duration_seconds != null ? '<span class="du">' + blocks.formatDuration(p.duration_seconds) + "</span>" : "";
    const poster = blocks.videoPosterUrl(p);
    const cover = poster
      ? '<img class="poster" src="' + attr(poster) + '" alt="" loading="lazy" decoding="async">'
      : '<span class="ph" aria-hidden="true"></span>';
    return '<a class="b-vid"' + common + ' href="' + attr(p.href) + '" rel="noopener" aria-label="' + attr(p.title) + '">' + cover + '<div class="pl" aria-hidden="true"><i></i></div>' + du + "</a>";
  }
  if (block.block_type === "tip") {
    return '<a class="b-tip"' + common + ' href="' + attr(p.href) + '" rel="noopener">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.6-9.2-8.8C1.2 8 3 4.8 6.4 4.8c2 0 3.4 1.1 4.1 2.3.3.5 1.7.5 2 0 .7-1.2 2.1-2.3 4.1-2.3 3.4 0 5.2 3.2 3.6 6.4C19 15.4 12 20 12 20z"></path></svg>' +
      escapeHtml(p.title) + "</a>";
  }
  if (block.block_type === "shop") {
    const image = p.image_asset_id
      ? '<img src="' + ASSET_PATH_PREFIX + encodeURIComponent(p.image_asset_id) + '" alt="' + attr(p.title) + '" loading="lazy" decoding="async">'
      : "";
    // 一卡=.pd;架(.b-shop)由 assembleBlocks 把連續的卡包成一架——與 theme-renderer 同形。
    // /manage/preview 走這條靜態路,DOM 形狀跟客戶頁不同就是預覽在說謊。
    const ds = p.description ? '<p class="ds">' + escapeHtml(p.description) + "</p>" : "";   // RL-144 A51,與 theme-renderer 同形
    return '<a class="pd"' + common + ' href="' + attr(p.href) + '" rel="noopener"><div class="ph2">' + image + "</div><b>" + escapeHtml(p.title) +
      "</b>" + ds + '<div class="pr"><span>' + escapeHtml(p.price_display) + "</span><i>" + (p.button_label ? escapeHtml(p.button_label) : SHOP_PILL_LABELS[p.availability] || SHOP_PILL_LABELS.in_stock) + "</i></div></a>";
  }
  if (block.block_type === "event") {
    const parts = eventDateParts(p.starts_at, p.timezone);
    const body = '<time class="d" datetime="' + attr(p.starts_at) + '"><b>' + parts.md + "</b><span>" + parts.wd + "</span></time>" +
      '<div class="t"><b>' + escapeHtml(p.title) + "</b>" + (p.description ? '<span class="ds">' + escapeHtml(p.description) + "</span>" : "") + (p.location ? "<span>" + escapeHtml(p.location) + "</span>" : "") + "</div>";
    return p.href
      ? '<a class="b-event"' + common + ' href="' + attr(p.href) + '" rel="noopener">' + body + '<span class="ar">報名</span></a>'
      : '<section class="b-event"' + common + ">" + body + "</section>";
  }
  if (block.block_type === "mail") {
    // 同 theme-renderer:沒端點、CSP form-action 'none' → disabled input,不是 <form>。
    return '<section class="b-mail"' + common + '><input type="email" placeholder="' + attr(MAIL_PLACEHOLDER_PREFIX + p.title) + '" aria-label="' + attr(p.title) + '" disabled>' +
      '<span class="go" aria-disabled="true">' + escapeHtml(p.button_label) + "</span></section>";
  }
  if (block.block_type === "place") return renderPlace(block, common, assets, {});
  if (block.block_type === "heading") return renderHeading(block, common, "");
  if (block.block_type === "faq") return renderFaq(block, common, "");
  if (block.block_type === "link_group") return renderLinkGroup(block, common, "");
  if (block.block_type === "testimonial") return renderTestimonial(block, common, assets, "");
  if (block.block_type === "countdown") return renderCountdown(block, common, {});
  if (block.block_type === "booking") return renderBooking(block, common, "");
  if (block.block_type === "file") return renderFile(block, common, assets, "");
  if (block.block_type === "qr") return renderQr(block, common, {});
  if (block.block_type === "gallery") return renderGallery(block, common, assets, { locale: "zh-TW" });   // 317 第 4 項   // 舊路(renderBlock)不帶 canonical ⇒ 不畫;公開頁走 theme-renderer,那邊帶
  if (block.block_type === "form") return renderForm(block, common, {});   // 317 第 18 項(舊路沒有 slug / 簽章 ⇒ 畫得出來但 action 空、_t 空,送不出去)
  if (block.block_type === "divider") return renderDivider(block, common);
  fail("PAGE_RENDER_BLOCK_UNKNOWN");
}

// 289 五(RL-092/093/094)Google 商家格——**兩支渲染器共用這一份**(theme-renderer 也呼叫這裡),三處 render 才是同一份。
//   .map:整張地圖縮圖就是導航連結(RL-093,alt「點地圖導航」);縮圖走本站 /a/(不外連 Google 圖,G5);資產不在 ⇒ 印框不印破圖(同 image)。
//   .pi:名稱/地址/星數與評論數 + 「取自 Google · <日期>」(日期 = 記錄層 provenance.fetched_at);.hr:營業時間 7 列;
//   .ac:導航 / 打電話(tel:)/ 留評論(RL-094,show_review_button 關 ⇒ 零鈕;刀 place-review-button-ignores-flag ⇒ G2 必紅);
//   .rv:前三則評論,**30 天過期**(PLACE_REVIEWS_TTL_DAYS,時鐘 context.rendered_at 由 service 給)⇒ 公開頁不畫、render-members 標「評論已過期,重抓」;
//   刀 place-map-no-link:地圖不包 <a> ⇒ G3 必紅。字面(導航/打電話/留評論/營業時間)與「來源:」同一種:寫死中文,登債 i18n。
// 317 第 5 項(RL-115)標題與分隔線——**兩支渲染器共用這一份**(theme-renderer 也呼叫這裡)。
//   heading:level 2/3 ⇒ <h2|h3 class="b-h" data-align>;名字是頁上唯一 h1,這裡永遠不出 h1。
//   刀 heading-as-h1(render mutation):不管 level 一律 h1 ⇒ 「恰一個 h1」那格必紅。
//   divider:<div class="b-hr" role="separator" data-style>——不用 <hr>:編輯器本體把每顆區塊變成可點目標時要在元素**裡面**放把手,
//     void 元素放不進去(wysElementCloseAt 對 hr 找不到閉合 ⇒ 整頁 503);role=separator 讓朗讀器仍讀成分隔。長相由 STRUCTURE_RULES 決定。
function renderHeading(block, common, renderMutation = "") {
  const p = block.payload;
  const tag = renderMutation === "heading-as-h1" ? "h1" : "h" + p.level;
  return "<" + tag + ' class="b-h" data-align="' + attr(p.align) + '"' + common + ">" + escapeHtml(p.text) + "</" + tag + ">";
}
// 317 第 10 項 FAQ——**兩支渲染器共用這一份**。原生 <details><summary>:零腳本、鍵盤可及;open_first ⇒ 第一題帶 open。
//   刀 faq-open-all(render mutation):每題都 open ⇒ 「恰第一題 open」那格必紅。
function renderFaq(block, common, renderMutation = "") {
  const p = block.payload;
  const items = p.items.map((item, index) => {
    const open = index === 0 && p.open_first === true || renderMutation === "faq-open-all" ? " open" : "";
    return "<details" + open + "><summary>" + escapeHtml(item.q) + "</summary><p>" + escapeHtml(item.a).replace(/\n/g, "<br>") + "</p></details>";
  }).join("");
  return '<section class="b-faq"' + common + ">" + items + "</section>";
}
// page/aib-oneshot-0926 link_group——**兩支渲染器共用這一份**。原生 <details><summary>:零腳本;open ⇒ details 帶 open;body = https 連結列(同 link 安全,已契約驗過)。
//   刀 link-group-force-open(render mutation):不管 payload.open 一律 open ⇒ 「預設收合」那格必紅。
function renderLinkGroup(block, common, renderMutation = "") {
  const p = block.payload;
  const open = p.open === true || renderMutation === "link-group-force-open" ? " open" : "";
  const links = p.items.map((item) => '<li><a href="' + attr(item.href) + '" rel="noopener noreferrer">' + escapeHtml(item.label) + "</a></li>").join("");
  return '<section class="b-link-group"' + common + "><details" + open + "><summary>" + escapeHtml(p.title) + '</summary><ul class="lg-list">' + links + "</ul></details></section>";
}
// 317 第 9 項 見證——**兩支渲染器共用這一份**。<figure><blockquote> + <figcaption>[頭像 40px] 名字 <small>role</small> [★★★★☆]</figcaption>;
//   星用文字 + aria-label「5 分中的 N 分」(不用圖);頭像缺檔不畫(assets.has,同 image);刀 quote-skip-rating-aria ⇒ C9 紅。
function renderTestimonial(block, common, assets, renderMutation = "") {
  const p = block.payload;
  const avatar = p.avatar_asset_id && !(assets && typeof assets.has === "function" && !assets.has(p.avatar_asset_id))
    ? '<img class="av" src="' + attr(ASSET_PATH_PREFIX + encodeURIComponent(p.avatar_asset_id)) + '" alt="" loading="lazy" decoding="async">' : "";
  const stars = Number.isInteger(p.rating)
    ? '<span class="st"' + (renderMutation === "quote-skip-rating-aria" ? "" : ' role="img" aria-label="5 分中的 ' + p.rating + ' 分"') + ">" + "★".repeat(p.rating) + "☆".repeat(5 - p.rating) + "</span>" : "";
  const role = p.role ? "<small>" + escapeHtml(p.role) + "</small>" : "";
  return '<figure class="b-quote"' + common + "><blockquote>" + escapeHtml(p.quote).replace(/\n/g, "<br>") + "</blockquote><figcaption>" + avatar + "<b>" + escapeHtml(p.author) + "</b>" + role + stars + "</figcaption></figure>";
}
// 317 第 11 項 倒數——**兩支渲染器共用這一份**;靜態、零腳本(RL-124 乙)。時鐘 = context.rendered_at(service 給;沒給 ⇒ 不算天數,只印標題)。
//   台北整天數:N ≥ 1 ⇒「剩 N 天」;0 ⇒「就是今天」;過了 ⇒ done_text(沒有 ⇒ 整顆不畫,回 "")。**不印時分秒**——公開頁快取是日粒度,印了就是騙人。
//   刀 countdown-print-seconds:多印 HH:MM:SS ⇒ 「零時分秒字面」那格必紅;countdown-ignore-clock:不看時鐘永遠印目標日 ⇒ 撥時鐘那臂紅。
function renderCountdown(block, common, context = {}) {
  const p = block.payload;
  const mutation = String(context.render_mutation || "");
  const days = context.rendered_at && mutation !== "countdown-ignore-clock" ? blocks.taipeiDaysBetween(context.rendered_at, p.target_at) : null;
  let body;
  if (days === null) body = '<span class="n" aria-hidden="true">·</span><span class="u">' + escapeHtml(String(p.target_at).slice(0, 10)) + "</span>";
  else if (days > 0) body = '<span class="n">' + days + '</span><span class="u">剩 ' + days + " 天</span>";
  else if (days === 0) body = '<span class="n">0</span><span class="u">就是今天</span>';
  else if (p.done_text) body = '<span class="u done">' + escapeHtml(p.done_text) + "</span>";
  else return "";
  const seconds = mutation === "countdown-print-seconds" ? '<span class="s">' + String(context.rendered_at).slice(11, 19) + "</span>" : "";
  return '<section class="b-count"' + common + '><h3>' + escapeHtml(p.title) + "</h3>" + body + seconds + "</section>";
}
// 317 第 13 項 預約卡——**兩支渲染器共用這一份**。<a class="b-book" href rel=noopener> [行事曆 glyph] title <small>sublabel · 30 分鐘 · 最近可約:…</small> <span class="ar">預約</span>;
//   點了離站,零 iframe 零腳本;next_slot_text 是店主手填(不接行事曆 API,登債)。刀 booking-skip-duration:小字不印分鐘 ⇒ C13 紅。
function renderBooking(block, common, renderMutation = "", locale = undefined) {
  const p = block.payload;
  const bits = [];
  if (p.sublabel) bits.push(escapeHtml(p.sublabel));
  if (Number.isInteger(p.duration_minutes) && renderMutation !== "booking-skip-duration") bits.push(escapeHtml(i18n.t(locale, "booking.minutes", { n: p.duration_minutes })));
  if (p.next_slot_text) bits.push(escapeHtml(i18n.t(locale, "booking.next_slot")) + escapeHtml(p.next_slot_text));
  const small = bits.length ? "<small>" + bits.join(" · ") + "</small>" : "";
  return '<a class="b-book"' + common + ' href="' + attr(p.href) + '" rel="noopener"><span class="ic">' + uiGlyphs.renderGlyph("calendar") + '</span><span class="t"><b>' + escapeHtml(p.title) + "</b>" + small + '</span><span class="ar">' + escapeHtml(i18n.t(locale, "booking.book")) + "</span></a>";
}
// 317 第 12 項 檔案下載——**兩支渲染器共用這一份**。<a class="b-file" href="/a/<id>?dl=<label>" download rel=noopener> [download glyph] label <small>PDF · 2.3 MB · note</small>;
//   size 從 size_bytes 印(沒有就不印),渲染器不 stat 檔;assets 帶 kind() 時,不是 pdf 或不在 ⇒ 整顆不畫(不出死連結)。刀 file-skip-download-attr ⇒ C12 紅。
function fileSizeLabel(bytes) {
  if (!Number.isInteger(bytes) || bytes < 1) return "";
  if (bytes >= 1024 * 1024) return (Math.round(bytes / (1024 * 1024) * 10) / 10) + " MB";
  return Math.max(1, Math.round(bytes / 1024)) + " KB";
}
function renderFile(block, common, assets, renderMutation = "") {
  const p = block.payload;
  if (assets && typeof assets.kind === "function" && assets.kind(p.asset_id) !== "pdf") return "";
  if (assets && typeof assets.has === "function" && !assets.has(p.asset_id)) return "";
  const dl = String(p.label).replace(/[^\p{L}\p{N} _-]/gu, "").trim().slice(0, 80) || "download";
  const bits = ["PDF"];
  const size = fileSizeLabel(p.size_bytes);
  if (size) bits.push(size);
  if (p.note) bits.push(escapeHtml(p.note));
  const download = renderMutation === "file-skip-download-attr" ? "" : " download";
  return '<a class="b-file"' + common + ' href="' + attr(ASSET_PATH_PREFIX + encodeURIComponent(p.asset_id) + "?dl=" + encodeURIComponent(dl)) + '"' + download + ' rel="noopener"><span class="ic">' + uiGlyphs.renderGlyph("download") + '</span><span class="t"><b>' + escapeHtml(p.label) + "</b><small>" + bits.join(" · ") + "</small></span></a>";
}
// 317 第 14 項 QR——**兩支渲染器共用這一份**;永遠編頁的 canonical(context.canonical,渲染器從 record 的主路由算,不從 payload 拿),內聯 SVG 零請求零外連。
//   刀 qr-url-from-payload:改編 payload.url ⇒ 「data-url = canonical 且 path 對得上重算」那格必紅;沒 canonical ⇒ 不畫。
// 317 第 4 項 相簿 / 輪播——**兩支渲染器共用這一份**。零 JS:carousel = <ul> 橫向 scroll-snap;grid = 2 欄(≥3 張 3 欄)。
//   缺檔的圖那格不畫(assets.has);全缺 ⇒ 整顆不畫;第一張 loading=eager 其餘 lazy(刀 gallery-eager-all ⇒ B4 紅);有 href 的圖包 <a>。
function renderGallery(block, common, assets, context = {}) {
  const p = block.payload;
  const mutation = String(context.render_mutation || "");
  const items = (p.items || []).filter((item) => !(assets && typeof assets.has === "function" && !assets.has(item.asset_id)));
  if (!items.length) return "";
  const style = p.style === "grid" ? "grid" : "carousel";
  const label = i18n.t(context.locale, "gallery.aria");
  const cols = style === "grid" && items.length >= 3 ? ' data-cols="3"' : "";
  const roles = style === "carousel" ? ' role="region" aria-roledescription="carousel"' : "";
  const li = items.map((item, index) => {
    const loading = mutation === "gallery-eager-all" || index === 0 ? "eager" : "lazy";
    const img = '<img src="' + attr(ASSET_PATH_PREFIX + encodeURIComponent(item.asset_id)) + '" alt="' + attr(item.alt || "") + '" loading="' + loading + '" decoding="async">';
    return "<li>" + (item.href ? '<a href="' + attr(item.href) + '" rel="noopener">' + img + "</a>" : img) + "</li>";
  }).join("");
  return '<div class="b-gal" data-style="' + style + '"' + cols + roles + ' aria-label="' + attr(label) + '"' + common + "><ul>" + li + "</ul></div>";
}
function renderQr(block, common, context = {}) {
  const p = block.payload;
  const mutation = String(context.render_mutation || "");
  const target = mutation === "qr-url-from-payload" ? (p.url || "https://example.com/phish") : context.canonical;
  if (!target) return "";
  const qr = pageQr.encode(target, { test_mutation: mutation });
  const svg = pageQr.toSvg(qr, { aria_label: "這一頁的 QR code", data_url: target });
  const caption = p.caption ? escapeHtml(p.caption) : "掃描分享這一頁";
  return '<figure class="b-qr"' + common + ">" + svg + "<figcaption>" + caption + "</figcaption></figure>";
}
// 317 第 18 項 表單——**兩支渲染器共用這一份**。<form class="b-form" method="post" action="/f/<slug>/<form_id>"> 標題 + 每欄 <label> + 對應輸入框
//   (text / email / tel → <input>、textarea、checkbox)、required 照契約;兩個隱藏欄:_t = 簽章(registration 用 hmac 算的 form token,綁 page_id + form_id;
//   context.form_tokens[form_id];沒有 ⇒ 空字串,伺服器 204 假成功不存)、_w = 蜜罐(CSS 藏,有值就丟);鈕字面 i18n form.submit。零腳本。
//   渲染端刀 form-drop-token:_t 不印 ⇒ F1 紅(合法提交也被當機器人 204 不存)。
function renderForm(block, common, context = {}) {
  const p = block.payload;
  const mutation = String(context.render_mutation || "");
  const slug = String(context.slug || "");
  const action = slug ? "/f/" + encodeURIComponent(slug) + "/" + encodeURIComponent(p.form_id) : "";
  const token = mutation === "form-drop-token" ? "" : String((context.form_tokens && context.form_tokens[p.form_id]) || "");
  const locale = context.locale;
  const fields = p.fields.map((field) => {
    const id = "f_" + attr(p.form_id) + "_" + attr(field.field_id);
    const req = field.required ? " required" : "";
    if (field.type === "checkbox") return '<label class="ck" for="' + id + '"><input type="checkbox" id="' + id + '" name="' + attr(field.field_id) + '" value="on"' + req + ">" + escapeHtml(field.label) + "</label>";
    if (field.type === "textarea") return '<label for="' + id + '">' + escapeHtml(field.label) + '<textarea id="' + id + '" name="' + attr(field.field_id) + '" maxlength="2000"' + req + "></textarea></label>";
    return '<label for="' + id + '">' + escapeHtml(field.label) + '<input type="' + attr(field.type) + '" id="' + id + '" name="' + attr(field.field_id) + '" maxlength="200"' + req + (field.type === "email" ? ' autocomplete="email"' : field.type === "tel" ? ' autocomplete="tel"' : "") + "></label>";
  }).join("");
  return '<form class="b-form"' + common + ' method="post"' + (action ? ' action="' + action + '"' : "") + ' accept-charset="utf-8"><h3>' + escapeHtml(p.title) + "</h3>" + fields +
    '<input type="hidden" name="_t" value="' + attr(token) + '"><label class="w" aria-hidden="true">website<input type="text" name="_w" tabindex="-1" autocomplete="off"></label>' +
    '<button type="submit">' + escapeHtml(i18n.t(locale, "form.submit")) + "</button></form>";
}
function renderDivider(block, common) {
  return '<div class="b-hr" role="separator" aria-orientation="horizontal" data-style="' + attr(block.payload.style) + '"' + common + "></div>";
}
const PLACE_REVIEWS_TTL_DAYS = 30;
const PLACE_WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"];
function placeReviewsExpired(provenance, nowIso) {
  if (!provenance || typeof provenance !== "object" || !provenance.fetched_at || !nowIso) return false;
  const fetched = Date.parse(provenance.fetched_at), now = Date.parse(nowIso);
  return Number.isFinite(fetched) && Number.isFinite(now) && fetched + PLACE_REVIEWS_TTL_DAYS * 86400000 < now;
}
function renderPlace(block, common, assets, context) {
  const p = block.payload;
  const mutation = String(context.render_mutation || "");
  const missing = assets && typeof assets.has === "function" && !assets.has(p.map_asset_id);
  // 刀 place-map-external:縮圖直接外連 Google Static Maps ⇒ G5 必紅(公開頁 img-src 只准 self,而且不准把訪客 IP 交給 Google)
  const src = mutation === "place-map-external" ? "https://maps.googleapis.com/maps/api/staticmap?center=" + encodeURIComponent(p.place_id) : ASSET_PATH_PREFIX + encodeURIComponent(p.map_asset_id);
  const img = missing && mutation !== "place-map-external" ? "" : '<img src="' + attr(src) + '" alt="點地圖導航" loading="lazy" decoding="async">';
  const map = mutation === "place-map-no-link"
    ? '<div class="map">' + img + "</div>"
    : '<a class="map" href="' + attr(p.nav_url) + '" rel="noopener" aria-label="點地圖導航">' + img + "</a>";
  const fetchedDay = block.provenance && block.provenance.fetched_at ? String(block.provenance.fetched_at).slice(0, 10) : null;
  const rating = p.rating != null
    ? '<span class="rt">★ ' + escapeHtml(p.rating.toFixed(1)) + (p.review_count != null ? " · " + escapeHtml(String(p.review_count)) + " 則評論" : "") +
      (fetchedDay ? '<small>取自 Google · ' + escapeHtml(fetchedDay) + "</small>" : "") + "</span>"
    : "";
  const info = '<div class="pi"><b>' + escapeHtml(p.name) + '</b><span class="ad">' + escapeHtml(p.address) + "</span>" + rating + "</div>";
  const hours = Array.isArray(p.hours)
    ? '<ul class="hr" aria-label="營業時間">' + p.hours.map((h, i) => "<li><span>" + PLACE_WEEKDAYS[i] + "</span><span>" + (h === null ? "休" : escapeHtml(h)) + "</span></li>").join("") + "</ul>"
    : "";
  const showReview = mutation === "place-review-button-ignores-flag" ? Boolean(p.review_url) : p.show_review_button === true && Boolean(p.review_url);
  const actions = '<div class="ac">' +
    '<a class="c" href="' + attr(p.nav_url) + '" rel="noopener">導航</a>' +
    (p.phone ? '<a class="c" href="tel:' + attr(String(p.phone).replace(/[^0-9+]/g, "")) + '">打電話</a>' : "") +
    (showReview ? '<a class="c rv" href="' + attr(p.review_url) + '" rel="noopener">留評論</a>' : "") + "</div>";
  let reviews = "";
  if (Array.isArray(p.reviews) && p.reviews.length) {
    const expired = mutation !== "ignore-provenance-ttl" && placeReviewsExpired(block.provenance, context.rendered_at);
    if (!expired) {
      reviews = '<ul class="rv">' + p.reviews.map((r) => "<li><b>" + escapeHtml(r.author) + '</b><span class="st">' + "★".repeat(r.rating) + '</span><time datetime="' + attr(r.at) + '">' + escapeHtml(String(r.at).slice(0, 10)) + "</time><p>" + escapeHtml(r.text) + "</p></li>").join("") + "</ul>";
    } else if (context.members === true) {
      reviews = '<p class="rv-expired" data-reviews-expired="">評論已過期,重抓</p>';
    }
  }
  return '<section class="b-place"' + common + ">" + map + info + hours + actions + reviews + '<span class="pb">Powered by Google</span></section>';
}

// 連續的商品卡包成一架 .b-shop(設計的單位是架,契約的單位是卡);被別的區塊隔開就是兩架。
// 隱藏/members 的卡在 renderBlock 已回空字串,空字串不開架也不斷架——不然一顆藏起來的卡
// 會把兩張公開卡切成兩架,DOM 形狀洩漏「中間有東西」。
function assembleBlocks(blockList, mutation, assets) {
  const rendered = [];
  let shopRun = [];
  const flush = () => { if (shopRun.length) { rendered.push('<div class="b-shop">' + shopRun.join("") + "</div>"); shopRun = []; } };
  for (const block of blockList) {
    const html = renderBlock(block, mutation, assets);
    if (!html) continue;
    if (block.block_type === "shop") { shopRun.push(html); continue; }
    flush();
    rendered.push(html);
  }
  flush();
  return rendered.join("");
}

function renderPageDocument(record, seo, options = {}) {
  const mutation = String(options.test_mutation || "");
  const page = record && record.page;
  const seoErrors = page ? validateSeo(seo, page) : ["page_render_record_invalid"];
  if (mutation === "render-unpublished") {
    const index = seoErrors.indexOf("page_render_unpublished");
    if (index >= 0) seoErrors.splice(index, 1);
  }
  if (seoErrors.length) fail("PAGE_RENDER_INVALID", seoErrors);
  const slotErrors = themeSlotErrors(record.blocks);
  if (slotErrors.length) fail("PAGE_RENDER_BLOCKS_INVALID", slotErrors);
  const blockResult = blocks.validatePageBlockSet(page, blocksForContractValidation(record.blocks));
  if (!blockResult.ok) fail("PAGE_RENDER_BLOCKS_INVALID", blockResult.errors);
  const routeResult = routes.validatePageRouteSet(page, record.routes);
  if (!routeResult.ok) fail("PAGE_RENDER_ROUTES_INVALID", routeResult.errors);
  const canonical = canonicalUrl(record, mutation);
  const robots = page.status === "published_indexable" ? "index,follow" : "noindex,follow";
  // 結構化資料:有 profile 就交給單一真相源(它帶著 Google 的必要屬性表與
  // 三階適用表);沒有就維持原本的 WebPage,不破壞既有呼叫端。
  // ★這支是線上真正在服務客戶頁面的渲染器。theme-renderer 是設計版工作台用的,
  // 兩支並存是已登記的技術債——今天先讓線上受益,合併是獨立的一條 lane。
  const profile = options.profile && typeof options.profile.display_name === "string" ? options.profile : null;
  const structured = profile
    ? structuredData.buildStructuredData({
      record, seo, profile, canonical,
      tier: TIER_BY_ROUTE_TYPE[(record.routes.find((route) => route.is_primary) || {}).route_type] || "folder_bio"
    })
    : { data: { "@context": "https://schema.org", "@type": "WebPage", name: seo.title, description: seo.description, url: canonical, dateModified: page.updated_at }, skipped: [] };
  const jsonLd = structured.data;
  const body = assembleBlocks([...record.blocks].sort((a, b) => a.position - b.position), mutation, options.assets);
  const robotMeta = mutation === "omit-robots" ? "" : '<meta name="robots" content="' + robots + '">';
  const clientScript = mutation === "include-client-script" ? "<script>window.bad=true</script>" : "";
  return "<!doctype html>\n" +
    '<html lang="' + attr(seo.lang) + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src &#39;self&#39; https:; style-src &#39;self&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">' +
    "<title>" + escapeHtml(seo.title) + "</title>" +
    '<meta name="description" content="' + attr(seo.description) + '">' + robotMeta +
    '<link rel="canonical" href="' + attr(canonical) + '">' +
    '<meta property="og:type" content="website"><meta property="og:title" content="' + attr(seo.title) + '"><meta property="og:description" content="' + attr(seo.description) + '"><meta property="og:url" content="' + attr(canonical) + '">' +
    '<script type="application/ld+json">' + safeJson(jsonLd) + "</script>" + clientScript +
    // 語意骨架:這一頁在講一個人(或一間店),所以名字是 h1 且包在 header 裡。
    // 標題用 display_name 而不是 seo.title——後者含品牌後綴(「某某 | Ez2.PAGE」),
    // 而頁面主標題應該是這一頁在講的那個人,不是我們的招牌。
    "</head><body>" +
    "<header>" + (profile
      ? "<h1>" + escapeHtml(profile.display_name) + "</h1>" + (profile.bio ? "<p>" + escapeHtml(profile.bio) + "</p>" : "")
      : "<h1>" + escapeHtml(seo.title) + "</h1>") + "</header>" +
    "<main>" + body + "</main>" +
    "</body></html>\n";
}

module.exports = { ASSET_PATH_PREFIX, PLACE_REVIEWS_TTL_DAYS, escapeHtml, eventDateParts, placeReviewsExpired, fileSizeLabel, renderBooking, renderCountdown, renderDivider, renderFaq, renderLinkGroup, renderFile, renderForm, renderGallery, renderHeading, renderPageDocument, renderPlace, renderQr, renderTestimonial, validateSeo, THEME_SLOTS, resolveThemeSlot, themeSlotAttr, themeSlotErrors, blocksForContractValidation };
