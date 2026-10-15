"use strict";

// ez2-page-asset-storage.js — 商品與內容資產的儲存分層(2026-07-26,the maintainer 拍板)
//
// the maintainer 的決定:商品圖片一律 serverless 到 Cloudflare 的庫或 S3;免費方案用 Google Drive。
//
// ★這個模組存在的第一理由是一條鐵律:**對外的資產網址永遠是我們自己的,
//   provider 只是背後的實作**。內容區塊只存 asset_id(既有 block contract 已經
//   是這樣),對外一律 https://{host}/a/{asset_id}。
//   如果讓 provider 的網址直接出現在頁面上,免費升付費那天所有已發布的圖片
//   就會同時失效——那是最貴的一種「之後再改」。Google Drive 尤其危險:
//   它的公開連結格式被 Google 改過不只一次。
//
// 第二理由是把「哪些 provider 不適合放哪些東西」變成機器檢查。Drive 能放圖片,
// 但它不是 CDN、沒有可靠的 Range 支援(影片拖曳會壞)、而且有每日 API 配額。
// 這些不是缺點清單,是會在半夜炸掉的東西,所以寫成 supports() 而不是註解。

const VERSION = "2026-07-26.ez2-page-asset-storage.v1";

// 能力矩陣。每一格都是「會不會出事」的判斷依據,不是行銷比較表。
const PROVIDERS = Object.freeze({
  cf_r2: Object.freeze({
    key: "cf_r2", label: "Cloudflare R2",
    cdn_backed: true, range_requests: true, signed_urls: true, image_transform: false,
    // 每日 API 配額:null=實質無限(以計費為準,不是硬牆)
    daily_request_quota: null, max_object_bytes: 5 * 1024 * 1024 * 1024,
    suitable_for: Object.freeze(["image", "video", "document", "archive"]),
    egress_billed: false   // R2 免出口費,是選它的主因
  }),
  cf_images: Object.freeze({
    key: "cf_images", label: "Cloudflare Images",
    cdn_backed: true, range_requests: false, signed_urls: true, image_transform: true,
    daily_request_quota: null, max_object_bytes: 10 * 1024 * 1024,
    // 圖片專用:丟影片進去會被拒,所以不列在 suitable_for
    suitable_for: Object.freeze(["image"]),
    egress_billed: false
  }),
  s3: Object.freeze({
    key: "s3", label: "S3 相容物件儲存",
    cdn_backed: false, range_requests: true, signed_urls: true, image_transform: false,
    daily_request_quota: null, max_object_bytes: 5 * 1024 * 1024 * 1024,
    suitable_for: Object.freeze(["image", "video", "document", "archive"]),
    egress_billed: true    // 出口計費:高流量站會痛,選它前先算
  }),
  gdrive: Object.freeze({
    key: "gdrive", label: "Google Drive(免費層)",
    // 以下三個 false 是 Drive 用在網站資產上的真正風險,不是保守估計:
    cdn_backed: false,      // 不是 CDN,離訪客遠就是慢
    range_requests: false,  // 影片拖曳/續傳會壞
    signed_urls: false,     // 只有分享連結,無法做短期授權
    image_transform: false,
    daily_request_quota: 20000,          // API 配額是硬牆,撞到就整站圖片消失
    max_object_bytes: 100 * 1024 * 1024,
    suitable_for: Object.freeze(["image", "document"]),
    egress_billed: false
  })
});

// 池 → 預設 provider。免費用 Drive 是成本決定;付費一律進物件儲存。
const POOL_DEFAULT_PROVIDER = Object.freeze({
  free: "gdrive",
  paid: "cf_r2",
  enterprise: "cf_r2"
});

const ASSET_KINDS = Object.freeze(["image", "video", "document", "archive"]);

function provider(key) { return PROVIDERS[String(key)] || null; }

// 對外資產網址:永遠是我們的網域 + asset_id。provider 換了它也不變,
// 這正是它存在的意義。簽名版本多一個 token,但路徑形狀相同。
function publicAssetUrl(host, assetId, options = {}) {
  const cleanHost = String(host || "").trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  const id = String(assetId || "").trim();
  if (!cleanHost || !/^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/.test(id)) {
    return { ok: false, errors: ["asset_url_inputs_invalid"] };
  }
  const base = "https://" + cleanHost + "/a/" + id;
  return { ok: true, url: options.token ? base + "?t=" + encodeURIComponent(String(options.token)) : base };
}

// provider 適用性:把「這東西放這裡會出事」變成拒絕,而不是等它半夜炸。
function supports(providerKey, request = {}) {
  const spec = provider(providerKey);
  if (!spec) return { ok: false, problems: ["asset_provider_unknown:" + providerKey] };
  const problems = [];
  const kind = String(request.kind || "");
  if (!ASSET_KINDS.includes(kind)) problems.push("asset_kind_unknown:" + kind);
  else if (!spec.suitable_for.includes(kind)) problems.push("asset_kind_unsupported_by_provider:" + providerKey + ":" + kind);
  const bytes = Number(request.bytes);
  if (Number.isFinite(bytes) && bytes > spec.max_object_bytes) problems.push("asset_too_large_for_provider");
  // 影片沒有 Range 支援等於不能拖曳、不能續播;讓它在上傳前就被擋下
  if (kind === "video" && !spec.range_requests) problems.push("video_requires_range_support");
  if (request.needs_signed_url === true && !spec.signed_urls) problems.push("provider_cannot_sign_urls");
  if (request.needs_image_transform === true && !spec.image_transform) problems.push("provider_cannot_transform_images");
  return { ok: problems.length === 0, provider: providerKey, problems: [...new Set(problems)].sort() };
}

// 依池選 provider,並回報這個選擇附帶的限制——免費層的限制要讓產品說得出口,
// 不是等使用者發現影片不能拖曳才解釋。
function providerForPool(pool, request = {}) {
  const key = POOL_DEFAULT_PROVIDER[String(pool)];
  if (!key) return { ok: false, errors: ["asset_pool_unknown:" + String(pool)] };
  const spec = provider(key);
  const check = supports(key, request);
  return {
    ok: check.ok,
    pool: String(pool),
    provider: key,
    problems: check.problems,
    limitations: Object.freeze({
      cdn_backed: spec.cdn_backed,
      range_requests: spec.range_requests,
      signed_urls: spec.signed_urls,
      daily_request_quota: spec.daily_request_quota
    }),
    // 免費層撞到限制時的出路:升級,而不是「請自行處理」
    upgrade_hint: check.ok ? null : (pool === "free" ? "upgrade_to_paid_object_storage" : null)
  };
}

// 升級方案時的資產搬遷。因為對外網址是我們的,搬遷對已發布內容是隱形的——
// 這裡回傳的是搬遷計畫與必須守住的不變式,不是實際搬檔。
function planAssetMigration(from, to, assets = []) {
  const source = provider(from);
  const target = provider(to);
  const errors = [];
  if (!source) errors.push("asset_provider_unknown:" + String(from));
  if (!target) errors.push("asset_provider_unknown:" + String(to));
  if (errors.length) return { ok: false, errors };

  const blocked = [];
  for (const asset of assets) {
    const check = supports(to, { kind: asset.kind, bytes: asset.bytes });
    if (!check.ok) blocked.push({ asset_id: asset.asset_id, problems: check.problems });
  }
  return {
    ok: blocked.length === 0,
    from: source.key,
    to: target.key,
    asset_count: assets.length,
    blocked,
    // 順序是刻意的:先複製、驗證雜湊、切換指向,最後才刪來源。
    // 先刪或先切,任何一步失敗就是圖片破圖,而破圖是客戶最先看到的東西。
    steps: Object.freeze(["copy_objects", "verify_checksums", "switch_pointer", "delete_source"]),
    invariants: Object.freeze([
      "public_url_unchanged",      // 對外網址全程不變(這是整套設計的根本)
      "source_kept_until_verified" // 驗證通過前來源不刪
    ])
  };
}

// 稽核已發布內容:任何直接外露 provider 網址的資產都是未爆彈,
// 因為 provider 一換那個網址就死。這條要能機器抓,不能靠自律。
const PROVIDER_URL_PATTERNS = Object.freeze([
  /drive\.google\.com/i,
  /googleusercontent\.com/i,
  /\.r2\.cloudflarestorage\.com/i,
  /\.s3[.-][a-z0-9-]*\.amazonaws\.com/i,
  /imagedelivery\.net/i
]);

function auditPublishedAssetUrls(html) {
  const text = String(html || "");
  const problems = [];
  for (const pattern of PROVIDER_URL_PATTERNS) {
    if (pattern.test(text)) problems.push("provider_url_leaked_into_content:" + pattern.source.replace(/\\/g, ""));
  }
  return { ok: problems.length === 0, problems: [...new Set(problems)].sort() };
}

// ── 唯讀送檔(A1,2026-09-02)──────────────────────────────────────────
//
// ★★★ 這一段只有「讀」。上傳端不在這波;而且**讀端與寫端分離**是這個 repo 反覆付過
//   代價的教訓(求助帳本只寫不讀、同意帳本只寫不讀)—— 這次先把讀端做對。
//
// ★ 對外路徑不在這裡硬寫。`publicAssetUrl()` 是唯一權威,路由與渲染器都從它導出
//   前綴(`theme-renderer.js:45` 已經這樣做)。`static-render.js:51` 硬寫的 `/assets/`
//   是第二真相,由 dev3 的 D1 一顆同源修正(pm2 09-02 15:40 裁),本模組不碰。
//
// ★ id 文法用**嚴**的那套(與 renderer 的 avatar 相同),而不是 block-contract 的
//   ID_RE(它允許 `.` 與 `:`)。理由是路徑穿越:一個允許 `.` 的 id 文法,
//   等於把「檔名可不可以含 ..」交給下一層去判 —— 而下一層永遠會有人忘。
const ASSET_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/;

// 只送圖片,且只送這幾種。★ SVG 刻意不在名單:它是可執行的 XML,直接開在頂層
//   會跑腳本;要送 SVG 得先有 sandbox 標頭與淨化,那是另一件事,不在這波。
const ASSET_EXTENSIONS = Object.freeze({
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  // 317 第 12 項:第二種資產 = PDF(菜單 / 型錄)。同一個目錄、同一個配額、同一個 id 形狀;/a/ 路由對它加 attachment + nosniff。
  pdf: "application/pdf"
});

// 長快取。asset_id 一旦發出就不會換內容(換內容=新 id,契約檔頭那條「對外網址
// 永遠是我們的」的另一面),所以可以 immutable。
const ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable";

function assetIdValid(value) { return typeof value === "string" && ASSET_ID_RE.test(value); }

// 在一串目錄裡找這個 id 的檔。★ 三道防線,缺一不可,而且每一道都在 smoke 裡有臂:
//   1. id 文法(擋 `..`、`%2e%2e` 解碼後的 `..`、含 `.` 的 id)
//   2. 副檔名白名單(擋 `.html`、`.svg`、無副檔名)
//   3. realpath 落在目錄底下(擋符號連結指到外面 —— 前兩道對 symlink 是瞎的)
// 找不到一律回同一個 `not_found`,不分「id 不合法」「檔不存在」「被擋」——
//   分開回等於替探測的人畫地圖。理由留在 `reason` 給日誌,不給回應。
function resolveAssetFile(spec) {
  const input = spec || {};
  const directories = Array.isArray(input.directories) ? input.directories.filter((d) => typeof d === "string" && d.trim()) : [];
  const id = input.asset_id;
  if (!assetIdValid(id)) return { ok: false, reason: "asset_id_invalid" };
  if (!directories.length) return { ok: false, reason: "asset_directories_missing" };
  const fs = require("fs");
  const path = require("path");
  for (const directory of directories) {
    let rootReal;
    try { rootReal = fs.realpathSync(directory); } catch (_) { continue; }   // 目錄不存在=這個來源沒有,不算錯
    for (const ext of Object.keys(ASSET_EXTENSIONS)) {
      const candidate = path.join(rootReal, id + "." + ext);
      let lst;
      try { lst = fs.lstatSync(candidate); } catch (_) { continue; }
      // ★ 符號連結一律拒:realpath 檢查擋得住指到外面的,擋不住指到**同目錄別檔**的
      //   —— 那種會讓一個 id 送出另一個 id 的內容。乾脆不認 symlink。
      if (lst.isSymbolicLink()) return { ok: false, reason: "asset_symlink_refused" };
      if (!lst.isFile()) continue;
      let real;
      try { real = fs.realpathSync(candidate); } catch (_) { continue; }
      if (real !== candidate && !real.startsWith(rootReal + path.sep)) return { ok: false, reason: "asset_outside_root" };
      return { ok: true, file: real, content_type: ASSET_EXTENSIONS[ext], extension: ext };
    }
  }
  return { ok: false, reason: "asset_not_found" };
}

function readAsset(spec) {
  const resolved = resolveAssetFile(spec);
  if (!resolved.ok) return resolved;
  const fs = require("fs");
  const crypto = require("crypto");
  const bytes = fs.readFileSync(resolved.file);
  const etag = '"' + crypto.createHash("sha256").update(bytes).digest("hex").slice(0, 32) + '"';
  return { ok: true, bytes, content_type: resolved.content_type, etag, cache_control: ASSET_CACHE_CONTROL };
}

// 316 v1.1 §二 3:存檔管線**唯一一份**——原本寫在 page-api.js POST /assets 函式體裡(288),編輯器換頭像/封面也要走同一條,
//   所以搬來這裡:sniff(魔數定型別,不信副檔名)→ dimensions(標頭讀不到寬高 = 415)→ stripMetadata(295 剝 EXIF/XMP/IPTC/註解)
//   → 配額(同一目錄同一計數,50 張)→ 取 id → tmp+rename。
//   錯誤用 code 丟(asset_too_large / asset_type_unsupported / asset_quota),呼叫端各自翻成自己的 HTTP 碼或查詢鍵。
//   `cut(name)` 是三把既有刀的入口(asset-trust-extension / asset-skip-length-gate / asset-keep-metadata):
//   砍在這裡,AIB 的 P1/P2/E1 與編輯器的 S2 一起紅——這就是「同一份」的機器證據。
const IMAGE_MAX_BYTES = 2 * 1024 * 1024;
const IMAGE_QUOTA = 50;
// 317 第 12 項(RL-124 A35 甲):PDF 上限 10MB;配額與圖共用(countStoredAssets 數圖 + pdf)。
const FILE_MAX_BYTES = 10 * 1024 * 1024;
const PDF_MAGIC = Buffer.from("%PDF-", "latin1");
function storeError(code, details) { const error = new Error(code); error.code = code; error.details = details || {}; return error; }
// 配額母體 = 這個目錄裡的圖 + pdf(317 第 12 項起 pdf 也算);刀 file-quota-separate ⇒ 只數圖 ⇒ C12 配額那格必紅
function countStoredAssets(dir, cut) {
  const fs = require("fs");
  const separate = typeof cut === "function" && cut("file-quota-separate");
  try { return fs.readdirSync(dir).filter((name) => (separate ? /^[A-Za-z0-9][A-Za-z0-9_-]{7,63}\.(webp|png|jpg)$/ : /^[A-Za-z0-9][A-Za-z0-9_-]{7,63}\.(webp|png|jpg|pdf)$/).test(name)).length; }
  catch (error) { if (error.code === "ENOENT") return 0; throw error; }
}
const countStoredImages = countStoredAssets;
function isPdf(bytes) { return Buffer.isBuffer(bytes) && bytes.length >= 8 && bytes.slice(0, 5).equals(PDF_MAGIC); }
// 317 第 12 項:存 PDF——魔數 %PDF- 才收(副檔名一個字都不信)、≤ FILE_MAX_BYTES、不剝內容(PDF 的 metadata 剝除登債)、同目錄同配額同 id 形狀。
//   刀 file-trust-extension:看副檔名 ⇒ 415 那格必紅。
function storeFile(spec) {
  const fs = require("fs");
  const path = require("path");
  const crypto = require("crypto");
  const input = spec || {};
  const cut = typeof input.cut === "function" ? input.cut : () => false;
  const bytes = Buffer.isBuffer(input.bytes) ? input.bytes : Buffer.alloc(0);
  const dir = String(input.dir || "");
  if (!dir) throw storeError("asset_dir_missing");
  const maxBytes = Number.isInteger(input.max_bytes) ? input.max_bytes : FILE_MAX_BYTES;
  const quota = Number.isInteger(input.quota) ? input.quota : IMAGE_QUOTA;
  if (!bytes.length) throw storeError("asset_empty");
  if (bytes.length > maxBytes) throw storeError("asset_too_large", { bytes: bytes.length, limit: maxBytes });
  const type = cut("file-trust-extension") ? (String(input.filename || "").toLowerCase().endsWith(".pdf") ? "pdf" : null) : (isPdf(bytes) ? "pdf" : null);
  if (!type) throw storeError("asset_type_unsupported");
  const used = countStoredAssets(dir, cut);
  if (used >= quota) throw storeError("asset_quota", { used, limit: quota });
  fs.mkdirSync(dir, { recursive: true, mode: 0o755 });
  let assetId;
  do { assetId = "asset_" + crypto.randomBytes(8).toString("hex"); } while (fs.existsSync(path.join(dir, assetId + "." + type)));
  if (!assetIdValid(assetId)) throw storeError("asset_id_invalid");
  const target = path.join(dir, assetId + "." + type);
  const tmp = target + ".tmp-" + process.pid;
  fs.writeFileSync(tmp, bytes, { mode: 0o644 });
  fs.renameSync(tmp, target);
  return { asset_id: assetId, type, content_type: ASSET_EXTENSIONS.pdf, bytes: bytes.length, original_bytes: bytes.length, metadata_removed: [], file: target };
}
function storeImage(spec) {
  const fs = require("fs");
  const path = require("path");
  const crypto = require("crypto");
  const imageBytes = require("./ez2-page-image-bytes.js");
  const input = spec || {};
  const cut = typeof input.cut === "function" ? input.cut : () => false;
  const bytes = Buffer.isBuffer(input.bytes) ? input.bytes : Buffer.alloc(0);
  const dir = String(input.dir || "");
  if (!dir) throw storeError("asset_dir_missing");
  const maxBytes = Number.isInteger(input.max_bytes) ? input.max_bytes : IMAGE_MAX_BYTES;
  const quota = Number.isInteger(input.quota) ? input.quota : IMAGE_QUOTA;
  if (!bytes.length) throw storeError("asset_empty");
  if (bytes.length > maxBytes && !cut("asset-skip-length-gate")) throw storeError("asset_too_large", { bytes: bytes.length, limit: maxBytes });
  const type = cut("asset-trust-extension")
    ? ({ webp: "webp", png: "png", jpg: "jpg", jpeg: "jpg" })[String(String(input.filename || "").split(".").pop() || "").toLowerCase()] || null
    : imageBytes.sniff(bytes);
  if (!type) throw storeError("asset_type_unsupported");
  // 刀 asset-trust-extension = 整個信客戶端(型別看副檔名、標頭讀不到也放行);正常路標頭讀不到寬高就是 415。
  const size = imageBytes.dimensions(bytes, type) || (cut("asset-trust-extension") ? { width: 0, height: 0 } : null);
  if (!size) throw storeError("asset_type_unsupported", { header: true });
  const stripped = cut("asset-keep-metadata") ? bytes : imageBytes.stripMetadata(bytes, type);
  const removed = imageBytes.metadataSegments(bytes, type) || [];
  const used = countStoredAssets(dir, cut);
  if (used >= quota) throw storeError("asset_quota", { used, limit: quota });
  fs.mkdirSync(dir, { recursive: true, mode: 0o755 });
  let assetId;
  do { assetId = "asset_" + crypto.randomBytes(8).toString("hex"); } while (fs.existsSync(path.join(dir, assetId + "." + type)));
  if (!assetIdValid(assetId)) throw storeError("asset_id_invalid");
  const target = path.join(dir, assetId + "." + type);
  const tmp = target + ".tmp-" + process.pid;
  fs.writeFileSync(tmp, stripped, { mode: 0o644 });
  fs.renameSync(tmp, target);
  return { asset_id: assetId, type, content_type: imageBytes.TYPES[type], width: size.width, height: size.height, bytes: stripped.length,
    original_bytes: bytes.length, metadata_removed: removed, file: target };
}
// 316 v1.1 §二 2:「這張圖在**這一頁**的目錄裡」——只查一個目錄(不含示範目錄、不含別頁;別頁的 id 猜得到就能掛別人的照片)。
//   刀 slot-store-any-page(呼叫端把整個 assets 根目錄遞進來)⇒ S3 必紅。
function pageAssetsOf(pageDir) {
  return Object.freeze({ has: (assetId) => resolveAssetFile({ directories: [pageDir], asset_id: assetId }).ok,
    kind: (assetId) => { const r = resolveAssetFile({ directories: [pageDir], asset_id: assetId }); return r.ok ? r.extension : null; } });
}

// 自檢:三道防線各自證明自己還在(用不到磁碟的那幾條)。
function auditAssetServing() {
  const errors = [];
  for (const bad of ["../etc/passwd", "..", "a.b_cdefgh", "a:bcdefghi", "", "short", "x".repeat(65)]) {
    if (assetIdValid(bad)) errors.push("asset_id_must_reject:" + JSON.stringify(bad));
  }
  if (!assetIdValid("asset_9f2b41cc")) errors.push("asset_id_must_accept_normal");
  if (ASSET_EXTENSIONS.svg) errors.push("svg_must_not_be_served_without_sandbox");
  if (ASSET_EXTENSIONS.html) errors.push("html_must_never_be_served_as_asset");
  const r = resolveAssetFile({ directories: ["/nonexistent-dir-for-audit"], asset_id: "../../etc/passwd" });
  if (r.ok || r.reason !== "asset_id_invalid") errors.push("traversal_must_die_at_grammar:" + (r.reason || "ok"));
  return { ok: errors.length === 0, errors: Object.freeze(errors), version: VERSION };
}

module.exports = {
  FILE_MAX_BYTES, countStoredAssets, isPdf, storeFile,
  ASSET_ID_RE, ASSET_EXTENSIONS, ASSET_CACHE_CONTROL,
  assetIdValid, resolveAssetFile, readAsset, auditAssetServing,
  IMAGE_MAX_BYTES, IMAGE_QUOTA, storeImage, countStoredImages, pageAssetsOf,
  VERSION, PROVIDERS, POOL_DEFAULT_PROVIDER, ASSET_KINDS, PROVIDER_URL_PATTERNS,
  provider, publicAssetUrl, supports, providerForPool, planAssetMigration, auditPublishedAssetUrls
};
