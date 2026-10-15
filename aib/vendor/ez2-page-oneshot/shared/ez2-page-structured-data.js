"use strict";

const blocksContract = require("./ez2-page-block-contract.js");
// ez2-page-structured-data — Google 結構化資料的機器執法層(2026-07-26)。
//
// 為什麼是模組不是文件:文件會過期而沒人發現,常數改了測試會紅。這裡把
// Google 搜尋中心的必要屬性與適用範圍寫成資料結構,產生器與驗證器共用同一張
// 表,所以不會出現「產生器以為對、驗證器說不對」。
//
// 規範查證日:2026-07-26(逐頁實查 Google 搜尋中心文件)。**引用前先重查**:
// 必要屬性會變,ProfilePage 本身就是近年才新增的型別。改動時更新 VERIFIED_ON。
//
// 三階的差別是這個模組存在的主因。同一份內容在三種形態下,Google 認的東西不同:
//   folder_bio      名片頁=某個人的個人資料頁
//   subdomain_seo   內容站=文章與導覽結構
//   custom_commerce 店家=機構與商品,**明文不得用 ProfilePage**
// 最後那條是 Google 自己寫的適用範圍(ProfilePage 不適用於「商店主要首頁」),
// 所以它不是我們的偏好,是規範,必須由程式擋而不是靠人記得。

const localeRoute = require("./ez2-page-public-locale-route.js");

const VERSION = "2026-07-26.ez2-page-structured-data.v1";
const VERIFIED_ON = "2026-07-26";

// 每個型別的必要屬性。三種形狀:
//   required  一定要有的鍵
//   one_of    至少要有其中一個(Google 對 Product 的條件式必要)
//   nested    某個鍵底下還有必要的鍵(Event 的 location.address)
// 上架狀態的對照表。Google 吃的是 schema.org 的 URL 列舉,我們的契約用小寫
// 代號——對照寫在一處,不要在產生器裡臨時拼字串。
const AVAILABILITY_SCHEMA = Object.freeze({ in_stock: "InStock", out_of_stock: "OutOfStock", preorder: "PreOrder" });

const GOOGLE_REQUIRED = Object.freeze({
  ProfilePage: Object.freeze({
    required: Object.freeze(["mainEntity"]),
    nested: Object.freeze({ mainEntity: Object.freeze(["name"]) }),
    note: "mainEntity 須為 Person 或 Organization;name 缺時可用 alternateName"
  }),
  Event: Object.freeze({
    required: Object.freeze(["name", "startDate", "location"]),
    nested: Object.freeze({ location: Object.freeze(["address"]) }),
    note: "location.address 為必要;Google 不建議只給城市名"
  }),
  Product: Object.freeze({
    required: Object.freeze(["name"]),
    one_of: Object.freeze(["offers", "review", "aggregateRating"]),
    note: "三者至少一個,否則不會產生商品摘要"
  }),
  VideoObject: Object.freeze({
    required: Object.freeze(["name", "thumbnailUrl", "uploadDate"]),
    note: "contentUrl 只是建議,不能拿它頂替必要屬性"
  }),
  Organization: Object.freeze({
    required: Object.freeze(["name", "url"]),
    note: "logo 與 sameAs 為建議"
  }),
  BreadcrumbList: Object.freeze({
    required: Object.freeze(["itemListElement"]),
    note: "至少兩個 ListItem;每個要 position 與 name,最後一項可省 item"
  }),
  Article: Object.freeze({
    required: Object.freeze(["headline"]),
    note: "image/datePublished/author 為強烈建議"
  }),
  LocalBusiness: Object.freeze({
    required: Object.freeze(["name", "address"]),
    note: "營業時間與地理座標為建議"
  })
});

// 三階各自允許的型別。**這張表是規範不是偏好**:
// ProfilePage 缺席於 custom_commerce 是 Google 明文的適用範圍限制。
const TIER_ALLOWED = Object.freeze({
  folder_bio: Object.freeze(["ProfilePage", "Event", "Product", "VideoObject"]),
  subdomain_seo: Object.freeze(["ProfilePage", "Article", "BreadcrumbList", "Event", "VideoObject"]),
  custom_commerce: Object.freeze(["Organization", "LocalBusiness", "Product", "BreadcrumbList", "Event", "VideoObject"])
});

// Google 沒有對應複合式結果的型別:標了不會生效,所以一律不產生。
// 列出來是為了讓「為什麼沒標音樂」有答案,而不是看起來像漏做。
const NO_RICH_RESULT = Object.freeze(["MusicRecording", "AudioObject"]);

function plain(value) {
  return !!value && typeof value === "object" && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}
function present(value) {
  if (value === undefined || value === null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

// 單一節點的必要屬性檢查。回傳缺什麼,不回傳「合不合格」——呼叫端要看得到
// 缺的是哪一個,才有辦法決定「補資料」還是「不輸出」。
function missingFor(node) {
  if (!plain(node)) return ["node_invalid"];
  const type = node["@type"];
  const spec = GOOGLE_REQUIRED[type];
  if (!spec) return NO_RICH_RESULT.includes(type) ? ["type_has_no_rich_result:" + type] : ["type_unknown:" + String(type)];
  const missing = [];
  for (const key of spec.required) if (!present(node[key])) missing.push(key);
  if (spec.one_of && !spec.one_of.some((key) => present(node[key]))) missing.push("one_of:" + spec.one_of.join("|"));
  for (const [parent, keys] of Object.entries(spec.nested || {})) {
    const child = node[parent];
    if (!plain(child)) { if (!missing.includes(parent)) missing.push(parent); continue; }
    for (const key of keys) if (!present(child[key])) missing.push(parent + "." + key);
  }
  return missing;
}

// 三階適用性:tier 用既有的路由權威判定,不自創第三套名詞。
function tierAllows(tier, type) {
  const allowed = TIER_ALLOWED[tier];
  if (!allowed) return false;
  return allowed.includes(type);
}

// 完整驗證:一份 JSON-LD 在某一階是否合規。兩個維度分開回報,因為
// 「屬性不齊」要補資料,「這一階不准用」要改型別,是兩種不同的修法。
function validateStructuredData(data, tier, options = {}) {
  const mutation = String(options.test_mutation || "");
  const errors = [];
  if (!plain(data)) return { ok: false, errors: ["structured_data_invalid"], tier, checked: [] };
  if (!localeRoute.TIERS.includes(tier)) return { ok: false, errors: ["structured_data_tier_unknown:" + String(tier)], tier, checked: [] };

  const nodes = [data].concat(Array.isArray(data.hasPart) ? data.hasPart : []);
  const checked = [];
  for (const node of nodes) {
    const type = plain(node) ? node["@type"] : "(invalid)";
    const missing = missingFor(node);
    if (missing.length) errors.push("structured_data_incomplete:" + type + ":" + missing.join(","));
    // ★這一條是三階比對的核心:Google 對型別有適用範圍,套錯階是違規不是風格。
    if (mutation !== "allow-tier-mismatch" && !tierAllows(tier, type)) {
      errors.push("structured_data_type_not_allowed_on_tier:" + type + "@" + tier);
    }
    checked.push({ type, missing });
  }
  return { ok: errors.length === 0, errors, tier, checked };
}

// 產生器與驗證器共用同一張表:這裡先組出候選節點,再逐一驗,只有完整且
// 該階允許的才進 hasPart,其餘進 skipped 並附原因。**沉默的省略會變成謊。**
function buildStructuredData(input) {
  const { record, seo, profile, canonical, tier } = input;
  // 一頁在講人還是講店,由店主宣告而不是我們從名字猜——猜錯會讓 Google 把
  // 一間店的知識圖譜連成一個人。ProfilePage 的 mainEntity 兩種都收,
  // 所以這裡換的是 @type 不是整個結構。
  const entityType = profile.entity_kind === "organization" ? "Organization" : "Person";
  const person = { "@type": entityType, name: profile.display_name };
  if (present(profile.bio)) person.description = profile.bio;
  if (present(profile.avatar_asset_id)) person.image = String(input.asset_path_prefix || "/a/") + profile.avatar_asset_id;
  // Organization 的 url 是 Google 的必要屬性之一;Person 沒有這個要求。
  if (entityType === "Organization") person.url = canonical;

  const candidates = [];
  for (const block of record.blocks) {
    if (block.visibility !== "public") continue;
    const p = block.payload;
    if (block.block_type === "event") {
      const event = { "@type": "Event", name: p.title, startDate: p.starts_at };
      if (present(p.description)) event.description = p.description;   // RL-144 A51
      if (present(p.location)) {
        event.location = { "@type": "Place", name: p.location, address: { "@type": "PostalAddress", streetAddress: p.location } };
      }
      if (present(p.ends_at)) event.endDate = p.ends_at;
      if (present(p.href)) event.url = p.href;
      candidates.push({ node: event, block_id: block.block_id });
    } else if (block.block_type === "shop") {
      const product = { "@type": "Product", name: p.title, url: p.href };
      if (present(p.description)) product.description = p.description;   // RL-144 A51
      if (present(p.image_asset_id)) product.image = String(input.asset_path_prefix || "/a/") + p.image_asset_id;
      // offers 只在有「數字金額」時才組。顯示字串(NT$680)不是 price——
      // 拿它去填會產出一個看起來合規、機器解析不出金額的標記。湊不齊就不輸出,
      // 由下面的驗證記錄原因,不靜默省略。
      if (typeof p.price_amount === "number" && Number.isFinite(p.price_amount) && present(p.currency)) {
        const offer = { "@type": "Offer", price: p.price_amount, priceCurrency: p.currency, url: p.href };
        if (present(p.availability)) offer.availability = "https://schema.org/" + AVAILABILITY_SCHEMA[p.availability];
        product.offers = offer;
      }
      candidates.push({ node: product, block_id: block.block_id });
    } else if (block.block_type === "video") {
      const video = { "@type": "VideoObject", name: p.title, contentUrl: p.href };
      const poster = blocksContract.videoPosterUrl(p);
      if (present(poster)) video.thumbnailUrl = poster;
      if (present(p.uploaded_at)) video.uploadDate = p.uploaded_at;
      if (present(p.description)) video.description = p.description;
      if (present(p.duration_seconds)) video.duration = blocksContract.isoDuration(p.duration_seconds);
      candidates.push({ node: video, block_id: block.block_id });
    } else if (block.block_type === "music") {
      candidates.push({ node: { "@type": "MusicRecording", name: p.title, url: p.href }, block_id: block.block_id });
    }
  }

  const parts = [];
  const skipped = [];
  for (const candidate of candidates) {
    const missing = missingFor(candidate.node);
    const allowed = tierAllows(tier, candidate.node["@type"]);
    if (missing.length) { skipped.push({ type: candidate.node["@type"], block_id: candidate.block_id, missing }); continue; }
    if (!allowed) { skipped.push({ type: candidate.node["@type"], block_id: candidate.block_id, missing: ["not_allowed_on_tier:" + tier] }); continue; }
    parts.push(candidate.node);
  }

  const data = {
    "@context": "https://schema.org",
    "@type": tier === "custom_commerce" ? "WebPage" : "ProfilePage",
    name: seo.title,
    description: seo.description,
    url: canonical,
    dateModified: record.page.updated_at
  };
  // 電商階不得用 ProfilePage(Google 明文不適用於商店主要首頁),所以那一階
  // 沒有 mainEntity Person——它需要的是 Organization,而那要等契約收商家資料。
  if (data["@type"] === "ProfilePage") data.mainEntity = person;
  if (parts.length) data.hasPart = parts;
  return { data, skipped, tier, verified_on: VERIFIED_ON };
}

module.exports = {
  VERSION, VERIFIED_ON, GOOGLE_REQUIRED, TIER_ALLOWED, NO_RICH_RESULT, AVAILABILITY_SCHEMA,
  missingFor, tierAllows, validateStructuredData, buildStructuredData
};
