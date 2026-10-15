"use strict";

// Ez2.PAGE theme renderer (C3 / D3).
//
// The missing joint between two finished pieces: the design pack knows what the
// eight themes LOOK like (CSS, conserved byte-for-byte against the mock) and the
// static render knows what a page MEANS (contract-validated blocks, SEO head,
// zero scripts). Until now a customer page could be correct or beautiful, not
// both. This module renders a contract page record INTO the design vocabulary.
//
// Deliberate boundaries:
//   - read-only consumer: it never defines a colour, token, keyframe or block
//     look of its own. Everything visual comes from the design pack; the only
//     CSS this file owns is a structural shell (column, spacing, image sizing)
//     in its own .pg- namespace, and an audit rejects any shell selector that
//     reaches into the pack's .b- namespace.
//   - zero JavaScript, no style attributes, no <style> tags: the page must hold
//     the performance_first structural bar (CSP style-src 'self'; the >8-links
//     fold uses native <details>, not a script).
//   - the footer promise line is derived from the tracking mode, never written
//     by hand — a fixed string would keep saying "no tracking scripts" after a
//     merchant switches to full pixel mode, which would be us lying (skill §9).
//   - form blocks are refused loudly. The design pack maps form to null on
//     purpose (double gate with the editor); rendering one silently would turn
//     a deliberate gate into an accident.
//   - only public blocks render. A bio page is cached publicly and forever;
//     members-only content inside it is a leak, not a feature.

const crypto = require("crypto");
const designPack = require("./ez2-page-design-pack.js");
const socialIcons = require("./ez2-page-social-icons.js");
const uiGlyphs = require("./ez2-page-ui-glyphs.js");
const structuredData = require("./ez2-page-structured-data.js");
const blocksContract = require("./ez2-page-block-contract.js");
const routesContract = require("./ez2-page-route-contract.js");
const templateLayout = require("./ez2-page-template-layout.js");
const i18n = require("./ez2-page-i18n.js");
const pageStats = require("./ez2-page-stats.js");   // 317 第 16 項:出站連結的 ping 屬性
const { ASSET_PATH_PREFIX, escapeHtml, eventDateParts, renderBooking, renderCountdown, renderDivider, renderFaq, renderLinkGroup, renderFile, renderForm, renderGallery, renderHeading, renderPlace, renderQr, renderTestimonial, validateSeo, THEME_SLOTS, resolveThemeSlot, themeSlotAttr, themeSlotErrors, blocksForContractValidation } = require("./ez2-page-static-render.js");

const VERSION = "2026-07-26.ez2-page-theme-renderer.v1";
const LINK_FOLD_THRESHOLD = 8;

// The asset path shape comes from the storage authority, not from a literal
// here (the second-truth-source lesson). It is derived once in static-render and
// shared, so the two renderers can never disagree on where an image lives.

// Footer promise per tracking mode (skill §9: the sentence and the mode move
// together). Keys align with the speed policy's MODES so a page cannot be
// published under a mode this table has no honest sentence for.
// ★ performance_first 是空字串 = **刻意不顯示任何句子**,不是漏填。
// the maintainer 2026-07-27 決定:追蹤碼將依客戶方案開放,所以「本頁不含任何追蹤腳本」
// 這種零追蹤宣稱不再出現在任何頁面上 —— 不是改寫成別的說法,是不顯示。
// 有東西載入的兩個模式仍然必須說出來:那是對訪客的告知義務,不是行銷語言。
//
// 空字串與「模式不存在」是兩件事,不可以再用真假值判斷混在一起 ——
// 混在一起會讓「刻意不說話」被守衛當成「沒有誠實句子」而拒絕發佈。
const FOOTER_PROMISES = Object.freeze({
  performance_first: "",
  balanced: "本頁僅在你同意後載入統計",
  full_pixel: "本頁載入你選擇的行銷像素"
});

// Structural shell: layout only, .pg- namespace only. Looks live in the pack.
const SHELL_RULES = Object.freeze([
  ["*", "margin:0; padding:0; box-sizing:border-box"],
    // 客戶會貼任意長度的網址與標題,而「body 永不橫向捲」是 RWD 六律之一。
  // overflow-x:clip 是最後一道防線,overflow-wrap:anywhere 讓長字自己斷,
  // 兩者缺一都會讓一條長連結把整頁推歪——這是客戶的頁面,不是我們的示範。
  // 行高 1.65 抄 mock 的 body:設計包每條規則只給字級,行高從 body 繼承;外殼沒設就是
  // 瀏覽器預設約 1.2,每張卡都矮幾 px(qa 第二輪:music −7、event −11、tip −3、shop −3)。
  ["body", "min-height:100vh; font-family:var(--font); line-height:1.65; -webkit-font-smoothing:antialiased; overflow-x:clip"],
  // 間距 16 / 左右 22 抄 mock 的 .demo-phone .scr(像素閘門對的是那台 demo 手機);
  // 上 34 下 24 是整頁的版心,demo 的 66 含瀏海。
  [".pg", "max-width:430px; margin:0 auto; min-height:100vh; padding:34px 22px 24px; display:flex; flex-direction:column; gap:16px; overflow-wrap:anywhere"],
  [".pg a", "color:inherit; text-decoration:none"],
  [".pg-avatar-img", "width:100%; height:100%; border-radius:inherit; object-fit:cover; display:block"],
  // 圖片框:.b-img 是設計包的框(固定高、漸層、邊框、說明字在左下);圖鋪滿框內側 1px
  // 邊框以內,說明字疊在圖上(後於圖、有定位 → 畫在上層)。
  [".pg-frame", "position:relative"],
  [".pg-frame a", "display:block"],
  [".pg-img", "position:absolute; inset:1px; width:calc(100% - 2px); height:calc(100% - 2px); object-fit:cover; border-radius:13px; display:block"],
  [".pg-cap", "position:relative"],
  // 商品圖撐滿設計包的 .ph2 正方圖框;沒圖時圖框留漸層(規則在設計包,不在這裡)。
  [".pg-shop-img", "width:100%; aspect-ratio:1; object-fit:cover; border-radius:9px; display:block"],
  [".pg-more summary", "list-style:none; cursor:pointer; min-height:44px; display:flex; align-items:center; justify-content:center; font-size:12px; opacity:.6"],
  [".pg-more summary::-webkit-details-marker", "display:none"],
  [".pg-more[open] summary", "opacity:.35"],
  [".pg-more .pg-fold", "display:flex; flex-direction:column; gap:13px"],
  [".pg-empty", "text-align:center; font-size:12.5px; opacity:.55; padding:28px 0"]
]);

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function attr(value) { return escapeHtml(value); }
function safeJson(value) { return JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026"); }

// entity_kind:這一頁在講的是「一個人」還是「一間店」。
// 為什麼是宣告不是推斷:「陳記牛肉麵」看起來像店,「the maintainer Liao 攝影工作室」呢?
// 猜錯會讓 Google 的知識圖譜把一間店連成一個人(或反過來),而那是我們主動
// 送給它的錯誤宣稱。沒宣告時預設 person——bio 頁的原始用途是數位名片,
// 而且個人是隱私上較保守的預設(不會把個人誤標成營業主體)。
const ENTITY_KINDS = Object.freeze(["person", "organization"]);
const PROFILE_FIELDS = new Set(["display_name", "bio", "avatar_asset_id", "cover_asset_id", "status_text", "identity_verified_at", "entity_kind", "social_links"]);

function validateProfile(profile) {
  const errors = [];
  if (!profile || typeof profile !== "object" || Array.isArray(profile) || !Object.keys(profile).every((key) => PROFILE_FIELDS.has(key))) {
    return ["page_theme_profile_invalid"];
  }
  const name = profile.display_name;
  // 80 aligns with the registration contract's display_name cap — a name the
  // registry accepted must never make the renderer (or the gallery) fail.
  if (typeof name !== "string" || !name.trim() || name.length > 80 || /[\u0000-\u001f<>]/.test(name)) errors.push("page_theme_display_name_invalid");
  if (profile.bio != null && (typeof profile.bio !== "string" || profile.bio.length > 300 || /[\u0000-\u001f]/.test(profile.bio))) errors.push("page_theme_bio_invalid");
  if (profile.avatar_asset_id != null && !/^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/.test(String(profile.avatar_asset_id))) errors.push("page_theme_avatar_asset_invalid");
  // 316 v1.1:封面圖 id 與頭像同一條正規式(asset-storage.ASSET_ID_RE)
  if (profile.cover_asset_id != null && !/^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/.test(String(profile.cover_asset_id))) errors.push("page_theme_cover_asset_invalid");
  if (profile.status_text != null && (typeof profile.status_text !== "string" || !profile.status_text.trim() || profile.status_text.length > 40 || /[\u0000-\u001f<>]/.test(profile.status_text))) errors.push("page_theme_status_invalid");
  // 只接受明確的兩個值或不給(不給=person)。空字串或別的字都是紅:
  // 靜默把不認得的值當 person,等於用預設值掩蓋了資料錯誤。
  if (profile.entity_kind != null && !ENTITY_KINDS.includes(profile.entity_kind)) errors.push("page_theme_entity_kind_invalid");
  // The badge follows identity_verified_at (canon: verified shows the badge,
  // unverified shows NOTHING). A boolean would invite hand-set true values;
  // requiring the timestamp keeps the field tied to the verification record.
  if (profile.identity_verified_at != null && !(typeof profile.identity_verified_at === "string" && Number.isFinite(Date.parse(profile.identity_verified_at)))) errors.push("page_theme_verified_at_invalid");
  // 社群連結。網址的形狀在註冊層已經過 social-intake 的完整判定(https、
  // host 白名單、憑證剝離),這裡只驗渲染需要的兩件事:平台畫得出來、外連是
  // https。不重做上游做過的事,但也不假設上游一定跑過——這支渲染器有別的呼叫端。
  if (profile.social_links != null) {
    if (!Array.isArray(profile.social_links)) errors.push("page_theme_social_links_invalid");
    else for (const link of profile.social_links) {
      if (!link || typeof link !== "object" || !socialIcons.has(link.platform) || !/^https:\/\//.test(String(link.url || ""))) {
        errors.push("page_theme_social_link_invalid");
        break;
      }
    }
  }
  return errors;
}

// First visible character of the name — the letter avatar (edge case: no photo).
function avatarLetter(name) {
  const trimmed = String(name).trim();
  const point = trimmed.codePointAt(0);
  return point == null ? "?" : String.fromCodePoint(point).toUpperCase();
}

// 連結卡與活動卡的箭頭。與社群圖示同網格同筆畫,所以它們並排時是同一套。
// 音樂提供者的品牌寫法(給 .b-music 副標)。品牌名不進 i18n:各語言都這樣寫。
const MUSIC_PROVIDER_LABELS = Object.freeze({
  spotify: "Spotify", apple_music: "Apple Music", youtube_music: "YouTube Music", soundcloud: "SoundCloud", bandcamp: "Bandcamp"
});
// 電子報輸入框的提示字:mock 是「you@email.com — 每週四的宇宙情報」,前綴告訴訪客這格要填信箱。
const MAIL_PLACEHOLDER_PREFIX = "you@email.com — ";
// 商品卡膠囊字:契約沒有按鈕文案欄位,膠囊只反映 availability。
// 跟頁上其他外殼文案(「尚未啟用」「更多」)一樣先固定 zh-TW;i18n 是另一條 lane。
const SHOP_PILL_LABELS = Object.freeze({ in_stock: "購買", preorder: "預購", out_of_stock: "售完" });

const ARROW_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5"/></svg>';
const VBADGE_SVG = '<svg viewBox="0 0 24 24" aria-label="已驗證" role="img"><path d="M4 12l5 5 11-11"/></svg>';
const TIP_HEART_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.6-9.2-8.8C1.2 8 3 4.8 6.4 4.8c2 0 3.4 1.1 4.1 2.3.3.5 1.7.5 2 0 .7-1.2 2.1-2.3 4.1-2.3 3.4 0 5.2 3.2 3.6 6.4C19 15.4 12 20 12 20z"></path></svg>';

// 316 v1.1 §四 2 封面:有 cover_asset_id(且檔在,缺檔 ⇒ 零位元組,不出破圖)⇒ 頁首最前一張橫幅;aspect / max_height 是每套的表值
//   (templateLayout.fixedFor);eager + fetchpriority=high:它幾乎必是 LCP;alt 空 = 裝飾(名字就在下面)。沒有 ⇒ 零位元組。
//   刀 slot-cover-skip-aspect(不讀表,寫死預設)⇒ S6 必紅;刀 slot-render-skip-avatar 在 service 的 renderProfileOf(不帶兩欄)⇒ S1/S6 必紅。
function coverHtml(profile, options = {}) {
  if (!profile.cover_asset_id || assetMissing(options.assets, profile.cover_asset_id)) return "";
  const fixed = String(options.render_mutation || "") === "slot-cover-skip-aspect" ? templateLayout.DEFAULT_FIXED : templateLayout.fixedFor(String(options.theme || ""));
  return '<div class="b-cover" style="aspect-ratio:' + templateLayout.coverAspectRatio(fixed.cover.aspect) + ';max-height:' + Number(fixed.cover.max_height) + 'px">' +
    '<img src="' + attr(ASSET_PATH_PREFIX + profile.cover_asset_id) + '" alt="" loading="eager" fetchpriority="high" decoding="async"></div>';
}
function identityHtml(profile, options = {}) {
  // 316 v1.1:頭像檔不在這一頁的目錄(options.assets.has)⇒ 退回首字,不出破圖
  const avatarInner = profile.avatar_asset_id && !assetMissing(options.assets, profile.avatar_asset_id)
    ? '<img class="pg-avatar-img" src="' + attr(ASSET_PATH_PREFIX + profile.avatar_asset_id) + '" alt="' + attr(profile.display_name) + '" loading="lazy" decoding="async">'
    : escapeHtml(avatarLetter(profile.display_name));
  const badge = profile.identity_verified_at ? '<span class="vbadge">' + VBADGE_SVG.replace('aria-label="已驗證"', 'aria-label="' + attr(i18n.t(options.locale, "shell.verified")) + '"') + "</span>" : "";
  const bio = profile.bio ? '<div class="bi">' + escapeHtml(profile.bio) + "</div>" : "";
  const status = profile.status_text
    ? '<div class="b-status"><span class="pill"><span class="dot"></span>' + escapeHtml(profile.status_text) + "</span></div>"
    : "";
  // 語意:這是某個人的頁面,所以他的名字是頁面主標題(h1),不是一個 div。
  // 搜尋引擎與 AI 代理靠標題與 landmark 理解一頁在講誰;沒有 h1 的頁面
  // 在它們眼中沒有主題。頭像是裝飾(名字就在旁邊),所以對輔助技術隱藏,
  // 不製造「A」這種無意義的朗讀。
  return '<header class="b-hd">' + coverHtml(profile, options) +
    '<div class="b-ava" aria-hidden="true"><div class="i">' + avatarInner + "</div></div>" +
    '<div class="b-id"><h1 class="nm">' + escapeHtml(profile.display_name) + badge + "</h1>" + bio + "</div>" + status +
    socialRowHtml(profile, options) +
    "</header>";
}

// 存成聯絡人。這是名片頁真正的終點:訪客把你存進通訊錄之後,你就永久在他
// 手機裡 —— 那比瀏覽器書籤有價值太多,而書籤網頁根本做不到。
//
// 零腳本:一條指向 /{slug}.vcf 的連結而已,手機點下去直接跳通訊錄匯入選單。
// download 屬性讓桌機也存成檔案而不是在分頁裡顯示一堆純文字。
// ★ 目前每一頁都有。要改成店主可關,是在這裡加一個 profile 旗標的事,
//   但預設開著:這個產品的定位就是數位名片,而名片的用途就是被留下。
function saveContactHtml(options = {}) {
  const canonical = String(options.canonical || "");
  if (!/^https:\/\//.test(canonical)) return "";
  const path = new URL(canonical).pathname.replace(/\/$/, "") + ".vcf";
  // ★ .b-cta .c 是為「標題 + 小字」直向堆疊設計的(三顆動作鈕並排那種),
  // 直接把圖示塞進去會變成上下兩層、圖示掉到左上角(截圖抓到的)。
  // 外殼不准碰設計包的 .b- 命名空間(auditShell 會擋),所以在裡面包一層
  // 自己命名空間的橫向容器 —— 材質仍然來自設計包,排版由外殼負責。
  // ★ 09-02 艦長裁決:mock 的 CTA 列沒有這顆,它不占 CTA 位——改成社群列最後一顆圓形圖示
  // (.b-soc .s,設計包早畫好的材質),可及名稱在 aria-label。沒有社群連結時列裡就只有它。
  return '<a class="s" href="' + attr(path) + '" download aria-label="' + attr(i18n.t(options.locale, "shell.save_contact")) + '">' + uiGlyphs.renderGlyph("contact") + "</a>";
}

// 社群圖示列。設計包的 .b-soc 早就畫好了(mock 從第一版就有),缺的一直是
// 資料通道 —— 註冊第二步收了連結、進了同意帳本,然後從來沒有人顯示它。
//
// 三個刻意的決定:
//   1. 圖示對輔助技術隱藏,可及名稱放在連結的 aria-label 上。圖示自己再帶一次
//      名字會變成「Instagram Instagram」。
//   2. rel="me" 是有語意的:它宣告「這個帳號也是我」,是 IndieWeb 與部分平台
//      驗證會讀的關係。noopener/noreferrer 是安全預設。
//   3. surface 來自主題自己宣告的 kind,不在這裡猜。品牌色接近黑白的平台
//      靠它拿到深底替代色,否則會在對應底色上整個消失。
// 316 v1.1 §三 2:格子的產生抽成一支,頁首列與 socials 區塊共用(同一份資料、同一個畫法);
//   本體有一顆 public 的 socials 區塊 ⇒ 頁首**只留**存成聯絡人(options.socials_in_body=true),圖示不畫兩份(守 09-02 裁決的實質)。
//   刀 slot-header-keep-socials(頁首不收列)⇒ S5 必紅。
function socialCellsHtml(profile, options = {}) {
  const links = Array.isArray(profile.social_links) ? profile.social_links : [];
  const theme = designPack.THEMES[String(options.theme || "")];
  const surface = theme ? theme.kind : "light";
  const mode = String(options.social_icon_mode || socialIcons.DEFAULT_MODE);
  return links.map((link) => {
    const label = socialIcons.ICONS[link.platform].label;
    return '<a class="s" href="' + attr(link.url) + '" rel="me noopener noreferrer" aria-label="' + attr(label) + '">' +
      socialIcons.renderIcon(link.platform, { mode, surface }) + "</a>";
  });
}
function socialRowHtml(profile, options = {}) {
  const inBody = options.socials_in_body === true && String(options.render_mutation || "") !== "slot-header-keep-socials";
  const cells = inBody ? [] : socialCellsHtml(profile, options);
  const save = saveContactHtml(options);
  if (!cells.length && !save) return "";
  return '<nav class="b-soc" aria-label="' + attr(i18n.t(options.locale, "shell.socials")) + '">' + cells.join("") + save + "</nav>";
}
// socials 區塊本體:同一支 socialCellsHtml;0 條連結 ⇒ 公開頁零位元組(連 nav 都不出),render-members 畫占位框給店主看。
function socialsBlockHtml(block, mutation, common, context) {
  const profile = context.profile || {};
  const cells = socialCellsHtml(profile, context);
  if (!cells.length) return mutation === "render-members" ? '<nav class="b-soc" data-socials-empty=""' + common + '><span class="s" aria-hidden="true">+</span></nav>' : "";
  return '<nav class="b-soc" aria-label="' + attr(i18n.t(context.locale, "shell.socials")) + '"' + common + ">" + cells.join("") + "</nav>";
}
// page/aib-oneshot-0926(+profile slot):profile 區塊 = 整份 identityHtml(封面跟頭像一起);data-block-id 掛在 header 上。
function profileBlockHtml(block, mutation, common, context) {
  const html = identityHtml(context.profile || {}, {
    theme: context.theme,
    social_icon_mode: context.social_icon_mode,
    canonical: context.canonical,
    assets: context.assets,
    socials_in_body: context.socials_in_body === true,
    render_mutation: context.render_mutation,
    locale: context.locale
  });
  if (!html) return mutation === "render-members" ? '<header class="b-hd" data-profile-empty=""' + common + "></header>" : "";
  return html.replace('<header class="b-hd"', '<header class="b-hd"' + common);
}

// options.assets:資產目錄的「有沒有這個 id」(has(asset_id) → boolean)。渲染器自己不碰
// 磁碟,誰呼叫誰告訴它;沒帶就當資產都在(舊呼叫端零改動)。
function assetMissing(assets, assetId) {
  return Boolean(assets && typeof assets.has === "function" && !assets.has(assetId));
}

// 317 第 17 項(302 的一半;RL-132 A40 甲):分享預覽圖 og:image = 封面 → 頭像 → 無。沒圖就是沒圖,不放預設卡(不騙爬蟲)。
//   網址走既有 /a/<id>;寬高由 assets.dimensions(registration 對檔頭重 sniff;拿不到就不印寬高);封面 ⇒ twitter:card summary_large_image,頭像 ⇒ summary。
//   缺檔退下一級(與 316 頭像缺檔退首字同一條)。刀 og-image-avatar-first(頭像先於封面)⇒ profile-slots O1 必紅。
function ogImageOf(profile, assets, origin, renderMutation) {
  if (!profile) return null;
  const order = renderMutation === "og-image-avatar-first" ? ["avatar_asset_id", "cover_asset_id"] : ["cover_asset_id", "avatar_asset_id"];
  for (const field of order) {
    const id = profile[field];
    if (typeof id !== "string" || !id || assetMissing(assets, id)) continue;
    const size = assets && typeof assets.dimensions === "function" ? assets.dimensions(id) : null;
    return { url: origin + ASSET_PATH_PREFIX + id, width: size && size.width > 0 ? size.width : null, height: size && size.height > 0 ? size.height : null,
      card: field === "cover_asset_id" ? "summary_large_image" : "summary" };
  }
  return null;
}
function ogImageHtml(image) {
  if (!image) return "";
  return '<meta property="og:image" content="' + attr(image.url) + '">' +
    (image.width && image.height ? '<meta property="og:image:width" content="' + image.width + '"><meta property="og:image:height" content="' + image.height + '">' : "") +
    '<meta name="twitter:card" content="' + image.card + '">';
}

// 289 〇 / 292 來源標示:取回產生的顆(記錄層 provenance)在角落印「來源:<source> · <日期>」(.b-src,八套一條共用樣式,
//   design-pack PROVENANCE_RULES 只在頁上有來源顆時輸出);過期(契約 provenanceExpired,時鐘由 options.rendered_at 給)⇒
//   公開頁在 renderThemedPage 的 visible 就濾掉、render-members(編輯器/預覽/review)保留並標「已過期,重抓」(data-expired)。
//   刀 ignore-provenance:不印來源行 ⇒ Z3 必紅;刀 ignore-provenance-ttl:過期照畫、不標 ⇒ Z4 必紅。
function provenanceOf(block, renderMutation) {
  return renderMutation !== "ignore-provenance" && block && block.provenance && typeof block.provenance.source === "string" ? block.provenance : null;
}
// 317 第 8 項:編輯器 / 預覽對排程中 / 已下線的顆標 data-schedule 與一句(公開頁根本不畫,所以只在 render-members 走到)。
//   字面同「來源:」寫死中文,登債 i18n(dev@00000 289 包一把來源行接 i18n 後照走)。
function scheduleLine(block, mutation, context) {
  if (mutation !== "render-members" || !block.schedule) return null;
  const state = blocksContract.scheduleState(block.schedule, context.rendered_at);
  if (state === "active") return null;
  const when = state === "pending" ? String(block.schedule.starts_at).slice(0, 16).replace("T", " ") : String(block.schedule.ends_at).slice(0, 16).replace("T", " ");
  return { state, line: '<span class="b-sched">' + (state === "pending" ? "將於 " + escapeHtml(when) + " 上線" : "已於 " + escapeHtml(when) + " 下線") + "</span>" };
}
// 區塊影片底:契約通過後把 <video class=pg-bbg-vid> 插進外殼第一個子節點,並標 data-bbg=video(CSS 在 design-pack)。
function injectBlockVideoBg(html, block, renderMutation) {
  const bg = block && block.background;
  if (!html || !bg || !bg.video || renderMutation === "ignore-block-background") return html;
  if (blocksContract.backgroundErrors(bg).length) return html;
  const fit = bg.video.fit === "contain" ? "contain" : "cover";
  const vid = '<video class="pg-bbg-vid" src="' + attr(bg.video.url) + '" muted loop playsinline autoplay disablepictureinpicture aria-hidden="true" tabindex="-1" style="object-fit:' + fit + '"></video>';
  const open = html.indexOf(">");
  if (open < 0) return html;
  let head = html.slice(0, open);
  if (!/\sdata-bbg=/.test(head)) head += ' data-bbg="video"';
  return head + ">" + vid + html.slice(open + 1);
}

// S5:區塊 theme_slot CSS 權威在 design-pack（themeSlotCss）；本檔只轉呼叫。換 theme_id 自然重染；無槽 ⇒ 空。
function blockThemeSlotCss(blocks, options = {}) {
  return designPack.themeSlotCss(blocks, options);
}

function renderThemedBlock(block, mutation, assets, renderMutation = "", context = {}) {
  let html = renderThemedBlockInner(block, mutation, assets, renderMutation, context);
  if (html) html = injectBlockVideoBg(html, block, renderMutation);
  const sched = html ? scheduleLine(block, mutation, context) : null;
  if (sched) {
    const close = html.lastIndexOf("</");
    if (close >= 0) html = html.slice(0, close).replace(/ data-block-id="/, ' data-schedule="' + sched.state + '" data-block-id="') + sched.line + html.slice(close);
  }
  const provenance = provenanceOf(block, renderMutation);
  if (!html || !provenance) return html;
  const expired = renderMutation !== "ignore-provenance-ttl" && blocksContract.provenanceExpired(provenance, context.rendered_at);
  const day = String(provenance.fetched_at).slice(0, 10);
  // RL-109:字面在 i18n 四語鍵;語系由 context.locale(service 從請求帶進 options.locale)決定,公開頁沒帶 ⇒ zh-TW(與頁上其他外殼文案同)
  // 289 (h):via "search" 的顆,來源行前綴「AI 搜尋 →」(i18n 鍵 provenance.line_search;source 仍是走的轉接器名)
  const line = '<span class="b-src">' + escapeHtml(i18n.t(context.locale, provenance.via === "search" ? "provenance.line_search" : "provenance.line", { source: provenance.source, date: day }))
    + (expired ? " · " + escapeHtml(i18n.t(context.locale, "provenance.expired")) : "") + "</span>";
  const close = html.lastIndexOf("</");
  if (close < 0) return html;
  return html.slice(0, close).replace(/ data-block-id="/, ' data-src="' + attr(provenance.source) + '"' + (expired ? ' data-expired=""' : "") + ' data-block-id="') + line + html.slice(close);
}
// 317 第 6 項:連結的小圖示與縮圖。glyph 用 uiGlyphs、平台用 socialIcons(mode / surface 與頁首列同一套)、asset 走 /a/;
//   assets.has 說沒有的圖不畫(同 image 的 assetMissing 路)。
function linkMediaHtml(p, assets, context = {}) {
  let icon = "";
  if (p.icon && typeof p.icon === "object") {
    if (p.icon.glyph) icon = '<span class="ic">' + uiGlyphs.renderGlyph(p.icon.glyph) + "</span>";
    else if (p.icon.platform) {
      const theme = designPack.THEMES[String(context.theme || "")];
      icon = '<span class="ic">' + socialIcons.renderIcon(p.icon.platform, { mode: String(context.social_icon_mode || socialIcons.DEFAULT_MODE), surface: theme ? theme.kind : "light" }) + "</span>";
    } else if (p.icon.asset_id && !assetMissing(assets, p.icon.asset_id)) {
      icon = '<span class="ic"><img src="' + attr(ASSET_PATH_PREFIX + encodeURIComponent(p.icon.asset_id)) + '" alt="" loading="lazy" decoding="async"></span>';
    }
  }
  const thumb = p.thumbnail_asset_id && !assetMissing(assets, p.thumbnail_asset_id)
    ? '<img class="th" src="' + attr(ASSET_PATH_PREFIX + encodeURIComponent(p.thumbnail_asset_id)) + '" alt="" loading="lazy" decoding="async">'
    : "";
  return { icon, thumb };
}
function renderThemedBlockInner(block, mutation, assets, renderMutation = "", context = {}) {
  // 刀在產生 HTML 的這一行,不是入口參數檢查。render-members 是測試突變。
  if (mutation === "render-members" ? block.visibility === "hidden" : block.visibility !== "public") return "";
  const p = block.payload;
  // 287-B 包二:九宮尺寸 ⇒ data-span="cxr",css 在 design-pack BASE_RULES 對應 grid-column/row span;缺席不吐(滿寬)。
  //   cta / shop 住在 .b-cta / .b-shop 那一架裡(架才是 grid item),它們的 span 不畫——退化,交件單有列。
  // 刀 wys-span-no-attr:渲染不吐 data-span ⇒ C1 必紅
  const span = renderMutation !== "wys-span-no-attr" && block.span && Number.isInteger(block.span.cols) && Number.isInteger(block.span.rows) && !["cta", "shop"].includes(block.block_type)
    ? ' data-span="' + block.span.cols + "x" + block.span.rows + '"' : "";
  // S5:theme_slot → data-theme-slot；缺席=繼承頁色。刀 ignore-theme-slot ⇒ 不吐屬性（尺反向）。
  const slotAttr = renderMutation === "ignore-theme-slot" ? "" : themeSlotAttr(block);
  const common = ' data-block-id="' + attr(block.block_id) + '"' + span + slotAttr;
  if (block.block_type === "text") {
    return '<section class="b-txt"' + common + ">" + escapeHtml(p.text).replace(/\n/g, "<br>") + "</section>";
  }
  if (block.block_type === "image") {
    // mock: .b-img 是固定高 96px 的框(設計包的漸層 + 邊框),說明字在框內左下。
    // 框永遠畫;圖用外殼自己的 .pg-img 絕對定位鋪滿框(外殼不碰 .b- 命名空間)。
    // ★ 資產不存在時要「印框不印破圖」:824fb23b 靠 img 空 alt,root2 截圖證明 Chrome
    //   對有尺寸的失敗 img 照樣畫破圖圖示——所以改成呼叫端告訴渲染器資產在不在
    //   (options.assets.has),不在就根本不出 <img>,框與說明字照樣在。
    //   說明字(alt)由框內 .pg-cap 顯示,不在 img 上再朗讀一次;alt 空字串就沒有說明列。
    const caption = p.alt ? '<span class="pg-cap">' + escapeHtml(p.alt) + "</span>" : "";
    const image = assetMissing(assets, p.asset_id) ? "" : '<img class="pg-img" src="' + attr(ASSET_PATH_PREFIX + p.asset_id) + '" alt="" loading="lazy" decoding="async">';
    const frame = '<div class="b-img">' + image + caption + "</div>";
    return '<figure class="pg-frame"' + common + ">" + (p.href ? '<a href="' + attr(p.href) + '">' + frame + "</a>" : frame) + "</figure>";
  }
  if (block.block_type === "link") {
    // mock: <span>標題<small>副標</small></span> + .ar 箭頭。副標選填,沒有就一行。
    // 317 第 6 項:icon 走文字 span 的最前面(.ic:glyph / 平台圖示 / 20px 小圖);thumbnail 取代箭頭(.th 56px);缺檔 ⇒ 不畫(不出破圖)。
    //   刀 link-icon-skip-render:兩個鍵都不畫 ⇒ B6 必紅。
    const small = p.sublabel ? "<small>" + escapeHtml(p.sublabel) + "</small>" : "";
    const media = renderMutation === "link-icon-skip-render" ? { icon: "", thumb: "" } : linkMediaHtml(p, assets, context);
    // 317 第 7 項:emphasis ⇒ class em-pulse / em-grow(刀 emphasis-skip-class ⇒ B7 必紅)
    const emphasis = p.emphasis && renderMutation !== "emphasis-skip-class" ? " em-" + p.emphasis : "";
    return '<a class="b-lnk' + emphasis + '"' + common + ' href="' + attr(p.href) + '" rel="' + attr(p.rel.join(" ")) + '"><span>' + media.icon + escapeHtml(p.label) + small + "</span>" +
      (media.thumb || '<span class="ar">' + ARROW_SVG + "</span>") + "</a>";
  }
  if (block.block_type === "cta") {
    // mock: .c 是「標題 + small 副標」直向兩行(LINE 諮詢 / 馬上開聊)。副標選填,沒有就一行。
    if (p.action.type === "open_url") {
      const small = p.sublabel ? "<small>" + escapeHtml(p.sublabel) + "</small>" : "";
      return '<a class="c br" href="' + attr(p.action.href) + '">' + escapeHtml(p.label) + small + "</a>";
    }
    // Runtime actions cannot work on a zero-script page. The design pack's .soon
    // treatment exists for exactly this state; saying so beats a dead button.
    return '<span class="c soon" aria-disabled="true"' + common + ">" + escapeHtml(p.label) + "<small>" + escapeHtml(i18n.t(context.locale, "shell.coming_soon")) + "</small></span>";
  }
  // 後六種語彙(music/video/shop/event/mail/tip)。共同紀律:零腳本、零第三方
  // 嵌入——不放 iframe 也不載入平台 SDK,只畫一張帶連結的卡片,點了才離站。
  // 客戶頁面不替任何平台追蹤訪客。★這是為了速度不是為了隱私:0 腳本、0 外源
  // 才能讓效能分數是結構保證。理由記清楚,因為追蹤依方案開放後這個預設會變。
  if (block.block_type === "music") {
    // mock: .b-music > .art i*3 + .m b/span/.bar i + .pl i。eqbar/barmove 掛在
    // .art i 與 .bar i,舊的 .eq/.t/.pv 選不到那些 class,等化器是死的。
    // 副標印提供者的品牌寫法(Spotify,不是 spotify):契約沒有副標欄位,品牌名不進 i18n。
    // 等化器/進度條/播放三角是裝飾,對輔助技術隱藏;可及名稱只有標題那一行。
    return '<a class="b-music"' + common + ' href="' + attr(p.href) + '" rel="noopener">' +
      '<div class="art" aria-hidden="true"><i></i><i></i><i></i></div>' +
      '<div class="m"><b>' + escapeHtml(p.title) + "</b><span>" + escapeHtml(p.sublabel || MUSIC_PROVIDER_LABELS[p.provider] || p.provider) + '</span><div class="bar" aria-hidden="true"><i></i></div></div>' +
      '<div class="pl" aria-hidden="true"><i></i></div></a>';
  }
  if (block.block_type === "video") {
    // autoplay 在契約層預設就被拒;這裡即使收到 true 也只畫封面連結,
    // 因為零腳本頁面沒有播放器,自動播放在這裡不可能成立。
    // mock: .b-vid > [.poster img | .ph 佔位] + .pl i + .du(3:24 角標)。
    // ★ 所有影片都要有縮圖:thumbnail_url 或 YouTube 官方 hqdefault;其餘 provider 畫 .ph 佔位(仍有封面層)。
    const du = p.duration_seconds != null ? '<span class="du">' + blocksContract.formatDuration(p.duration_seconds) + "</span>" : "";
    const poster = blocksContract.videoPosterUrl(p);
    const cover = poster
      ? '<img class="poster" src="' + attr(poster) + '" alt="" loading="lazy" decoding="async">'
      : '<span class="ph" aria-hidden="true"></span>';
    return '<a class="b-vid"' + common + ' href="' + attr(p.href) + '" rel="noopener" aria-label="' + attr(p.title) + '">' + cover + '<div class="pl" aria-hidden="true"><i></i></div>' + du + "</a>";
  }
  if (block.block_type === "shop") {
    // mock: .b-shop(架)> .pd(卡)> .ph2(圖框)+ b(名)+ .pr(span 價 + i 膠囊)。
    // 一顆契約區塊=一張卡;架由 assembleBlocks 把連續的商品卡包成一列(跟 .b-cta 同理),
    // 所以這裡只出 .pd。圖框沒圖時留設計包的漸層,不印破圖;有圖用外殼自己的
    // .pg-shop-img 撐滿(外殼不准碰 .b- 命名空間)。價格只印 price_display:
    // 幣別已在字串裡(NT$),機器讀的 currency 走結構化資料,不重複印給人看。
    const image = p.image_asset_id
      ? '<img class="pg-shop-img" src="' + attr(ASSET_PATH_PREFIX + p.image_asset_id) + '" alt="' + attr(p.title) + '" loading="lazy" decoding="async">'
      : "";
    // RL-144 A51:商品說明 .ds 印在標題下(沒給 ⇒ 零位元組,舊頁逐位元組不變);刀 description-skip ⇒ D2 必紅
    const ds = p.description && renderMutation !== "description-skip" ? '<p class="ds">' + escapeHtml(p.description) + "</p>" : "";
    return '<a class="pd"' + common + ' href="' + attr(p.href) + '" rel="noopener"><div class="ph2">' + image + "</div><b>" + escapeHtml(p.title) +
      "</b>" + ds + '<div class="pr"><span>' + escapeHtml(p.price_display) + "</span><i>" + (p.button_label ? escapeHtml(p.button_label) : escapeHtml(i18n.t(context.locale, "shop.pill." + (SHOP_PILL_LABELS[p.availability] ? p.availability : "in_stock")))) + "</i></div></a>";
  }
  if (block.block_type === "event") {
    // mock: .b-event > .d(日期塊:b 月/日 + span 星期)+ .t(b 標題 + span 地點·剩席)+ .ar(報名 →)。
    // 日期塊就是 <time>:機器讀 datetime(UTC ISO),人讀 7/30 THU——用區塊的 timezone 切日,
    // 沒帶=Asia/Taipei(09-02 艦長裁決:Ez2 母市場,不是 UTC;台北晚間的活動不准早一天)。
    // 本機時區永遠不進來:渲染必須確定性,同一份資料在任何機器上逐位元組同。
    const parts = eventDateParts(p.starts_at, p.timezone);
    const sub = p.location ? "<span>" + escapeHtml(p.location) + "</span>" : "";
    // RL-144 A51:活動文案 .ds 印在標題下、地點上(沒給 ⇒ 零位元組)
    const ds = p.description && renderMutation !== "description-skip" ? '<span class="ds">' + escapeHtml(p.description) + "</span>" : "";
    const body = '<time class="d" datetime="' + attr(p.starts_at) + '"><b>' + parts.md + "</b><span>" + parts.wd + "</span></time>" +
      '<div class="t"><b>' + escapeHtml(p.title) + "</b>" + ds + sub + "</div>";
    return p.href
      ? '<a class="b-event"' + common + ' href="' + attr(p.href) + '" rel="noopener">' + body + '<span class="ar">' + escapeHtml(i18n.t(context.locale, "shell.signup")) + " " + ARROW_SVG + "</span></a>"
      : '<section class="b-event"' + common + ">" + body + "</section>";
  }
  if (block.block_type === "mail") {
    // mock: .b-mail > input + .go(漸層「訂閱」鈕),沒有同意小字。
    // 訂閱端點還不存在,而且客戶頁 CSP 是 form-action 'none'(兩支渲染器都是),
    // 所以這裡不能是 <form>:input disabled、.go 不可點——長相跟設計稿同一張,
    // 按下去不會假裝送出。disabled 的輸入框不收任何東西,所以同意文字暫不印;
    // ★ 端點落地那一顆必須:改成 <form method="post">、放寬 form-action、把
    // consent_text 印在輸入框旁——收信箱就是收個資,三件缺一不准開。
    return '<section class="b-mail"' + common + '><input type="email" placeholder="' + attr(MAIL_PLACEHOLDER_PREFIX + p.title) + '" aria-label="' + attr(p.title) + '" disabled>' +
      '<span class="go" aria-disabled="true">' + escapeHtml(p.button_label) + "</span></section>";
  }
  if (block.block_type === "tip") {
    // mock: svg 愛心 + 文案。hb 心跳掛在 .b-tip svg,沒有 svg 就不會跳。
    return '<a class="b-tip"' + common + ' href="' + attr(p.href) + '" rel="noopener">' + TIP_HEART_SVG + escapeHtml(p.title) + "</a>";
  }
  // 317 第 5 項:標題 / 分隔線與 static-render 同一份(那邊是唯一實作)。
  if (block.block_type === "heading") return renderHeading(block, common, renderMutation);
  if (block.block_type === "faq") return renderFaq(block, common, renderMutation);   // 317 第 10 項
  if (block.block_type === "link_group") return renderLinkGroup(block, common, renderMutation);   // page/aib-oneshot-0926
  if (block.block_type === "testimonial") return renderTestimonial(block, common, assets, renderMutation);   // 317 第 9 項
  if (block.block_type === "countdown") return renderCountdown(block, common, { rendered_at: context.rendered_at, render_mutation: renderMutation });   // 317 第 11 項
  if (block.block_type === "booking") return renderBooking(block, common, renderMutation, context.locale);   // 317 第 13 項;第 19a 項帶語系
  if (block.block_type === "file") return renderFile(block, common, assets, renderMutation);   // 317 第 12 項
  if (block.block_type === "gallery") return renderGallery(block, common, assets, { locale: context.locale, render_mutation: renderMutation });   // 317 第 4 項
  if (block.block_type === "qr") return renderQr(block, common, { canonical: context.canonical, render_mutation: renderMutation });   // 317 第 14 項
  if (block.block_type === "divider") return renderDivider(block, common);
  // 317 第 20 項:公開頁的鎖卡(合成顆,放在第一顆 members 顆的位置;通關碼表單 POST /m/<slug>;零 JS)
  if (block.block_type === "members_lock") {
    const t = (key) => escapeHtml(i18n.t(context.locale, key));
    const slug = String(new URL(context.canonical).pathname.replace(/^\//, ""));
    return '<form class="b-members" method="post" action="/m/' + attr(slug) + '" data-members-lock=""><p>' + t("members.locked") + "</p>" +
      (context.members_hint ? '<p class="hint">' + escapeHtml(context.members_hint) + "</p>" : "") +
      '<label>' + t("members.enter") + '<input type="password" name="code" autocomplete="off" required maxlength="64"></label>' +
      '<button type="submit">' + t("members.submit") + "</button></form>";
  }
  if (block.block_type === "socials") return socialsBlockHtml(block, mutation, common, context);
  if (block.block_type === "profile") return profileBlockHtml(block, mutation, common, Object.assign({}, context, { assets: assets }));
  if (block.block_type === "place") {
    // 289 五:與 static-render 同一份(那邊是唯一實作);members = 編輯器/預覽/review 頁(過期評論標示只給店主看)。
    return renderPlace(block, common, assets, { rendered_at: context.rendered_at, render_mutation: renderMutation, members: mutation === "render-members" });
  }
  // 317 第 18 項:表單落地(端點 /f/ + CSP form-action 'self' + 蜜罐);slug 從 canonical 路徑取,簽章由 context.form_tokens 帶進來
  if (block.block_type === "form") return renderForm(block, common, { slug: context.canonical ? new URL(context.canonical).pathname.replace(/^\//, "") : "", form_tokens: context.form_tokens, locale: context.locale, render_mutation: renderMutation });
  fail("PAGE_THEME_BLOCK_UNKNOWN", [block.block_type]);
}

// Consecutive cta blocks share one .b-cta row (the pack's row is the unit of
// design; one lonely cell per row would look broken at three CTAs).
//
// Folding folds LINKS ONLY. The rule is "more than eight links fold" — sweeping
// every later block into the fold would hide content the owner placed on
// purpose (a closing text block, a tip jar) behind a "more" toggle it was never
// meant to live in. Surplus links collapse into a native <details> that sits
// where the ninth link sat; everything else renders exactly where it was.
function assembleBlocks(blockList, mutation, assets, renderMutation = "", context = {}) {
  const rendered = [];
  let ctaRun = [];
  let shopRun = [];
  // 商品架跟 CTA 列同理:設計的單位是「架」(.b-shop 兩欄),契約的單位是「卡」。
  // 連續的商品卡包成一架;被別的區塊隔開就是兩架——店主的排序就是設計。
  const flushCtas = () => {
    if (ctaRun.length) { rendered.push('<div class="b-cta">' + ctaRun.join("") + "</div>"); ctaRun = []; }
    if (shopRun.length) { rendered.push('<div class="b-shop">' + shopRun.join("") + "</div>"); shopRun = []; }
  };
  const foldActive = blockList.filter((block) => block.block_type === "link").length > LINK_FOLD_THRESHOLD && mutation !== "no-link-fold";
  const folded = [];
  let linksSeen = 0;
  let foldMarkPlaced = false;
  const FOLD_MARK = "\u0000FOLD\u0000";
  // 317 第 16 項:公開頁(context.stats_pings)每顆的出站 <a> 加 ping="/c/<page>/<block>/<slot>";編輯器/預覽/review 不加(店主自己點不算)。
  //   刀 stats-no-ping ⇒ T1 必紅。
  const paint = (block) => {
    const html = renderThemedBlock(block, mutation, assets, renderMutation, context);
    return context.stats_pings ? pageStats.withPings(html, context.page_id, block.block_id, renderMutation) : html;
  };
  for (const block of blockList) {
    if (block.block_type === "cta") {
      if (shopRun.length) flushCtas();
      ctaRun.push(paint(block));
      continue;
    }
    if (block.block_type === "shop") {
      if (ctaRun.length) flushCtas();
      shopRun.push(paint(block));
      continue;
    }
    if (block.block_type === "link") {
      linksSeen += 1;
      if (foldActive && linksSeen > LINK_FOLD_THRESHOLD) {
        folded.push(paint(block));
        if (!foldMarkPlaced) { flushCtas(); rendered.push(FOLD_MARK); foldMarkPlaced = true; }
        continue;
      }
    }
    flushCtas();
    rendered.push(paint(block));
  }
  flushCtas();
  const details = folded.length
    ? '<details class="pg-more"><summary>' + escapeHtml(i18n.t(context.locale, "shell.more")) + " (" + folded.length + ')</summary><div class="pg-fold">' + folded.join("") + "</div></details>"
    : "";
  return rendered.map((piece) => (piece === FOLD_MARK ? details : piece)).join("");
}

function shellCss() {
  return SHELL_RULES.map(([selector, body]) => selector + "{" + body + "}").join("\n");
}
// 287-B 包二:頁上有帶 span 的顆時,版心才從 flex column 換成 3 欄 grid(每個直接子元素預設占滿三欄 ⇒ 沒 span 的顆長得跟 flex 時一樣);
//   沒有 span 的頁一個位元組都不輸出(既有頁逐位元組不變)。九條 span 規則與手機單欄在 design-pack(renderBioCss spans:true)。
const SPAN_SHELL_CSS = ".pg{display:grid; grid-template-columns:repeat(3,1fr); grid-auto-rows:auto}\n.pg>*{grid-column:1/-1; min-width:0}";
// 290 預留(287-B 包二 (e)):appearance.ai_chat 開了才畫一個 72px 的位子(右下角,fixed,不吃事件)+ body 底部留 72px 不讓內容被蓋;
//   關(預設)一個位元組都不輸出。290 再把聊天泡泡放進 .pg-ai-slot。
const AI_CHAT_SLOT_HTML = '<div class="pg-ai-slot" aria-hidden="true"></div>';
const AI_CHAT_SLOT_CSS = ".pg-ai-slot{position:fixed; right:16px; bottom:16px; width:72px; height:72px; pointer-events:none}\nbody{padding-bottom:72px}";

// The shell must never restyle the pack's vocabulary. This is what keeps the
// renderer a READ-ONLY consumer instead of a quiet second design system.
function auditShell(options = {}) {
  const mutation = String(options.test_mutation || "");
  const blockers = [];
  const packClasses = new Set(designPack.BLOCKS.flatMap((block) => block.classes));
  // The drill injects a genuinely offending rule through the SAME detection path
  // a real offender would take — a hand-appended blocker string would prove
  // nothing about the detector.
  const rules = mutation === "shell-overrides-pack" ? SHELL_RULES.concat([[".b-lnk", "background:red"]]) : SHELL_RULES;
  for (const [selector] of rules) {
    for (const match of selector.matchAll(/\.([A-Za-z][\w-]*)/g)) {
      const cls = match[1];
      if ((packClasses.has(cls) || cls.startsWith("b-")) && mutation !== "allow-pack-override") {
        blockers.push("theme_shell_touches_pack:" + selector);
      }
    }
  }
  return { status: blockers.length ? "blocked" : "ok", blockers: [...new Set(blockers)] };
}

function canonicalUrl(record) {
  const primary = record.routes.find((route) => route.is_primary);
  if (!primary) fail("PAGE_THEME_PRIMARY_ROUTE_MISSING");
  return "https://" + primary.hostname + primary.path;
}

// 三階與網址形態的對應:route_type 是路由層的詞,tier 是結構化資料層的詞,
// 兩者一對一。放在這裡而不是猜,是因為「這一頁是什麼」決定了 Google 認哪些型別。
const ROUTE_TYPE_TIER = Object.freeze({
  platform_path: "folder_bio",
  platform_subdomain: "subdomain_seo",
  custom_domain: "custom_commerce",
  legacy_redirect: "folder_bio",
  preview: "folder_bio"
});
function tierForRecord(record) {
  const primary = record.routes.find((route) => route.is_primary);
  const tier = primary && ROUTE_TYPE_TIER[primary.route_type];
  if (!tier) fail("PAGE_THEME_TIER_UNKNOWN", [primary && primary.route_type]);
  return tier;
}

function renderThemedPage(record, seo, options = {}) {
  const mutation = String(options.test_mutation || "");
  const renderMutation = String(options.render_mutation || "");
  const theme = String(options.theme || "");
  if (!designPack.THEMES[theme]) fail("PAGE_THEME_UNKNOWN", [theme]);
  const trackingMode = String(options.tracking_mode === undefined ? "performance_first" : options.tracking_mode);
  // 認的是「這個模式在表裡嗎」,不是「它的句子是不是真值」——空字串是合法的
  // 刻意沉默,不認得的模式才是拒絕發佈的理由。
  if (!Object.prototype.hasOwnProperty.call(FOOTER_PROMISES, trackingMode)) {
    fail("PAGE_THEME_TRACKING_MODE_UNKNOWN", [trackingMode]);
  }
  const promise = FOOTER_PROMISES[trackingMode];

  const page = record && record.page;
  const seoErrors = page ? validateSeo(seo, page) : ["page_render_record_invalid"];
  if (seoErrors.length && mutation !== "skip-seo-validation") fail("PAGE_THEME_INVALID", seoErrors);
  // S5:theme_slot 白名單由契約 COMMON_FIELDS + themeSlotErrors 收；集合先驗給清楚的 block_id:code。
  const slotErrors = themeSlotErrors(record.blocks);
  if (slotErrors.length) fail("PAGE_THEME_BLOCKS_INVALID", slotErrors);
  const blockResult = blocksContract.validatePageBlockSet(page, blocksForContractValidation(record.blocks));
  if (!blockResult.ok) fail("PAGE_THEME_BLOCKS_INVALID", blockResult.errors);
  const routeResult = routesContract.validatePageRouteSet(page, record.routes);
  if (!routeResult.ok) fail("PAGE_THEME_ROUTES_INVALID", routeResult.errors);
  const profileErrors = validateProfile(options.profile);
  if (profileErrors.length) fail("PAGE_THEME_PROFILE_INVALID", profileErrors);

  const shell = auditShell();
  if (shell.status !== "ok") fail("PAGE_THEME_SHELL_INVALID", shell.blockers);

  // Public blocks only — a publicly cached page must not carry members content.
  // 292:公開頁把過期的取回顆整顆濾掉(訪客看不到過期資料);render-members 留著給店主看標記。時鐘 options.rendered_at 沒給 ⇒ 不判過期。
  const renderedAt = options.rendered_at === undefined ? undefined : String(options.rendered_at);
  // 317 第 20 項:members 變體**不在這裡放寬**——registration 對變體那份 bundle 傳的是「members 顆已改成 public」的記錄副本(withMembersAsPublic),
  //   公開頁的入口過濾與 HTML 刀兩行字面不動(visibility-leak 尺以它們為錨,成對突變要能證「拔掉必漏」)。audience "members" 只拿來不畫鎖卡。
  const membersAudience = options.audience === "members";
  const visible = [...record.blocks]
    .filter((block) => (mutation === "render-members" ? block.visibility !== "hidden" : block.visibility === "public"))
    .filter((block) => mutation === "render-members" || renderMutation === "ignore-provenance-ttl" || !blocksContract.provenanceExpired(provenanceOf(block, renderMutation), renderedAt))
    // 317 第 8 項:排程外的顆公開頁整顆不畫(同 hidden);render-members 留著給店主看標記。刀 schedule-ignore ⇒ B8 必紅
    .filter((block) => mutation === "render-members" || renderMutation === "schedule-ignore" || blocksContract.scheduleActive(block.schedule, renderedAt))
    .sort((a, b) => a.position - b.position);
  // 317 第 20 項:鎖卡合成顆(只在公開那份、且店主設了通關碼、且頁上真的有 public 之外的 members 顆時)
  if (options.members_gate === true && !membersAudience && mutation !== "render-members") {
    const firstMembers = [...record.blocks].filter((block) => block.visibility === "members").sort((a, b) => a.position - b.position)[0];
    if (firstMembers) {
      const at = visible.findIndex((block) => block.position > firstMembers.position);
      visible.splice(at < 0 ? visible.length : at, 0, { block_id: "members_lock", block_type: "members_lock", visibility: "public", position: firstMembers.position, payload: {} });
    }
  }
  const membersLock = visible.some((block) => block.block_type === "members_lock");

  const canonical = canonicalUrl(record);
  const robots = page.status === "published_indexable" ? "index,follow" : "noindex,follow";
  // 結構化資料由 shared/ez2-page-structured-data.js 單獨負責:那裡有 Google 的
  // 必要屬性表與三階適用表,產生器與驗證器共用同一份,所以這裡不再自己組。
  const structured = structuredData.buildStructuredData({
    record, seo, profile: options.profile, canonical,
    tier: tierForRecord(record), asset_path_prefix: ASSET_PATH_PREFIX
  });
  const jsonLd = structured.data;
  // 316 v1.1(RL-123 第 5 點):本體會畫出 socials 顆(在 visible 裡:公開頁 = public;render-members = 非 hidden,members 的也算)⇒ 頁首只留存成聯絡人。
  //   root2 複審:之前只認 public,members 的 socials 在編輯器/預覽本體與頁首各畫一份 ⇒ S5b 必紅。
  const socialsInBody = visible.some((block) => block.block_type === "socials");
  // page/aib-oneshot-0926(+profile slot):visible 裡有 profile ⇒ 不在頁首 prepend;hidden 不算(向後相容 = 今天行為)。
  const profileInBody = visible.some((block) => block.block_type === "profile");
  const blockContext = { rendered_at: renderedAt, locale: options.locale, profile: options.profile, theme, social_icon_mode: options.social_icon_mode, render_mutation: renderMutation, canonical, form_tokens: options.form_tokens,   // 317 第 14 項:QR 顆要 canonical;第 18 項:表單簽章
    page_id: page.page_id, stats_pings: mutation !== "render-members" && options.stats_pings === true,   // 317 第 16 項:只有公開頁(registration 帶 stats_pings:true)加 ping
    members_hint: typeof options.members_hint === "string" ? options.members_hint : "",
    socials_in_body: socialsInBody, assets: options.assets };   // profile 區塊要 socials_in_body + assets
  const body = visible.length
    ? assembleBlocks(visible, mutation, options.assets, renderMutation, blockContext)
    : '<p class="pg-empty">' + escapeHtml(i18n.t(options.locale, "shell.preparing")) + "</p>";
  const identity = profileInBody ? "" : identityHtml(options.profile, { theme, social_icon_mode: options.social_icon_mode, canonical, assets: options.assets, socials_in_body: socialsInBody, render_mutation: renderMutation, locale: options.locale });
  // 沒有句子就連那一格都不輸出 —— 留一個空的 <span> 會在頁腳撐出一行空隙,
  // 那是「刻意不說」變成「看起來壞掉」。
  const footer = '<footer class="b-ft"><span>Powered by Ez2.PAGE</span>' +
    (promise ? "<span>" + escapeHtml(promise) + "</span>" : "") + "</footer>";

  // 樣式表先算出來,因為它的雜湊要進 <link> 的網址。
  // ★這條解的是一個上線才顯形的問題:page.css 的快取政策帶
  // stale-while-revalidate=86400,店主換了主題,訪客最久要等 24 小時才看得到
  // (實測 CDN 發舊副本 7598 秒)。把內容雜湊放進網址之後,換主題就是換一條
  // 新網址,舊快取自然不再被要求——不必犧牲快取,也不必依賴手動清除。
  // 形狀與主題一起進 CSS。沒指定就是預設圓角,而預設的輸出與過去逐位元組相同。
  const shape = String(options.shape === undefined ? designPack.DEFAULT_SHAPE : options.shape);
  if (!designPack.SHAPES[shape]) fail("PAGE_THEME_SHAPE_UNKNOWN", [shape]);
  const spans = visible.some((block) => block.span && Number.isInteger(block.span.cols) && !["cta", "shop"].includes(block.block_type));
  // 287-B 包二 (d):背景覆蓋 option.background 交給 renderBioCss(緊跟 screen 之後);render_mutation 四把刀(drop-span-mobile / ignore-background / ink-always-light / surface-always-light)原樣傳。
  const provenance = visible.some((block) => provenanceOf(block, renderMutation));
  const place = visible.some((block) => block.block_type === "place");
  const structure = visible.some((block) => block.block_type === "heading" || block.block_type === "divider");   // 317 第 5 項
  const faq = visible.some((block) => block.block_type === "faq");   // 317 第 10 項
  const linkGroup = visible.some((block) => block.block_type === "link_group");   // page/aib-oneshot-0926
  const quote = visible.some((block) => block.block_type === "testimonial");   // 317 第 9 項
  const countdown = visible.some((block) => block.block_type === "countdown");   // 317 第 11 項
  const booking = visible.some((block) => block.block_type === "booking");   // 317 第 13 項
  const file = visible.some((block) => block.block_type === "file");   // 317 第 12 項
  const video = visible.some((block) => block.block_type === "video");
  const gallery = visible.some((block) => block.block_type === "gallery");   // 317 第 4 項
  const qr = visible.some((block) => block.block_type === "qr");   // 317 第 14 項
  const form = visible.some((block) => block.block_type === "form");   // 317 第 18 項
  const linkMedia = visible.some((block) => block.block_type === "link" && (block.payload.icon != null || block.payload.thumbnail_asset_id != null));   // 317 第 6 項
  const emphasis = visible.some((block) => block.block_type === "link" && block.payload.emphasis != null);   // 317 第 7 項
  const description = visible.some((block) => ["shop", "event"].includes(block.block_type) && block.payload.description != null);   // RL-144 A51
  const cover = Boolean(options.profile && options.profile.cover_asset_id && !assetMissing(options.assets, options.profile.cover_asset_id));
  const blockBg = designPack.blockBackgroundCss(visible, { test_mutation: renderMutation === "ignore-block-background" ? "ignore-block-background" : undefined });
  // S5:theme_slot CSS 緊跟區塊底色；無槽 ⇒ 空（公開頁 CSS 逐位元組不變）。
  const slotCss = blockThemeSlotCss(visible, { test_mutation: renderMutation });
  const css = designPack.renderBioCss({ theme, shape, scope: "body", spans, provenance, place, cover, members: membersLock, structure, faq, link_group: linkGroup, quote, countdown, booking, file, video, qr, gallery, form, link_media: linkMedia, emphasis, description, background: options.background, avatar_size: options.avatar_size, avatar_shape: options.avatar_shape, fixed_avatar_size: (templateLayout.fixedFor(theme).avatar || {}).size, test_mutation: ["drop-span-mobile", "ignore-background", "ink-always-light", "surface-always-light", "structure-rules-always"].includes(renderMutation) ? renderMutation : undefined }) + shellCss() + (spans ? "\n" + SPAN_SHELL_CSS : "") + (options.ai_chat === true ? "\n" + AI_CHAT_SLOT_CSS : "") + (blockBg ? "\n" + blockBg : "") + (slotCss ? "\n" + slotCss : "") + "\n";
  const cssVersion = crypto.createHash("sha256").update(css).digest("hex").slice(0, 12);

  const html = "<!doctype html>\n" +
    '<html lang="' + attr(seo.lang) + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    // 317 第 18 項:頁上有表單才放寬 form-action 到 'self'(meta 與 delivery 回應頭同一份判準 bundle.form_action;沒表單的頁逐位元組不變);第 16 項:有 ping 才加 connect-src
    '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src &#39;self&#39; https:; style-src &#39;self&#39;; base-uri &#39;none&#39;; form-action ' + ((form && renderMutation !== "form-csp-keep-none") || membersLock ? "&#39;self&#39;" : "&#39;none&#39;") + (blockContext.stats_pings ? "; connect-src &#39;self&#39;" : "") + '">' +
    "<title>" + escapeHtml(seo.title) + "</title>" +
    '<meta name="description" content="' + attr(seo.description) + '">' +
    (mutation === "omit-robots" ? "" : '<meta name="robots" content="' + robots + '">') +
    '<link rel="canonical" href="' + attr(canonical) + '">' +
    // 樣式表位址帶著頁面身分。相對的 "./page.css" 在 /slug 這種無尾斜線路徑下
    // 會解析成根目錄的 /page.css,讓所有客戶共用一份——而每個人的主題不同。
    '<link rel="stylesheet" href="' + attr(new URL(canonical).pathname.replace(/\/$/, "") + "/page.css?v=" + cssVersion) + '">' +
    '<meta property="og:type" content="profile"><meta property="og:title" content="' + attr(seo.title) + '"><meta property="og:description" content="' + attr(seo.description) + '">' +
    ogImageHtml(ogImageOf(options.profile, options.assets, new URL(canonical).origin, renderMutation)) +   // 317 第 17 項
    '<script type="application/ld+json">' + safeJson(jsonLd) + "</script>" +
    (mutation === "include-client-script" ? "<script>window.bad=true</script>" : "") +
    // footer 放在 main 之外:它是整頁的資訊(來源與追蹤模式承諾),
    // 不是這個人的內容。放進 main 會讓「主要內容」的範圍多出一段不屬於他的字。
    '</head><body><main class="pg">' + identity + body + "</main>" + footer + (options.ai_chat === true ? AI_CHAT_SLOT_HTML : "") + "</body></html>\n";


  const cssAudit = designPack.auditBioCss(css);
  if (cssAudit.status !== "ok" && mutation !== "ignore-css-audit") fail("PAGE_THEME_CSS_AUDIT_FAILED", cssAudit.blockers);

  return { schema_version: VERSION, theme, shape, tracking_mode: trackingMode, html, css, css_audit: cssAudit, structured_data_skipped: structured.skipped, stats_pings: blockContext.stats_pings === true, form_action: form && renderMutation !== "form-csp-keep-none" ? "'self'" : "'none'", members_gate: membersLock };
}

// One record, all eight skins — the preview set the design workbench consumes.
// Same content, same uid, same speed; only the CSS file differs (canon: 換膚不換身份).
function renderThemePreviewSet(record, seo, options = {}) {
  const previews = {};
  for (const theme of Object.keys(designPack.THEMES)) {
    previews[theme] = renderThemedPage(record, seo, Object.assign({}, options, { theme }));
  }
  return previews;
}

module.exports = {
  VERSION,
  ASSET_PATH_PREFIX,
  FOOTER_PROMISES,
  LINK_FOLD_THRESHOLD,
  SHELL_RULES, ENTITY_KINDS, tierForRecord,
  auditShell,
  avatarLetter,
  renderThemedPage,
  renderThemePreviewSet,
  validateProfile,
  // S5 theme_slot / retint（契約 + design-pack 權威；本檔轉呼叫）
  THEME_SLOTS,
  resolveThemeSlot,
  blockThemeSlotCss
};
