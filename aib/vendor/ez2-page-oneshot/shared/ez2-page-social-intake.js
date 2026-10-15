"use strict";

// Ez2.PAGE social-link intake for the two-step claim flow.
// GDPR posture: a link is only accepted when its own consent box was ticked
// (zero-default), the whole submission passes a second explicit confirmation
// POST, and the recorded state must satisfy the consent contract before it is
// allowed to touch disk. Crawling/first-draft generation is a later, separate
// stage — this module only records authorized material sources.

const fs = require("fs");
const path = require("path");
const consent = require("./ez2-page-consent-contract.js");
const retentionPromise = require("./ez2-page-retention-promise.js");

// ★ v2(2026-07-28):`retention` 多一格 `enforcement`。
//   舊行的形狀不變也沒有人回讀它們 —— 但 schema 版本仍然要動,
//   因為「同一個版本號兩種形狀」是之後查資料的人最難拆的那種問題。
const VERSION = "2026-07-28.ez2-page-social-intake.v2";
const POLICY_VERSION = "ez2-page-social-intake-policy.v1";

// 這個檔案宣告的那條對外承諾,在承諾登記表裡的 id。
const RETENTION_PROMISE_ID = "social_intake_unused_material";
const MAX_URL_LENGTH = 300;

// featured: the six always-visible rows (owner-picked: top five + douyin);
// the rest live behind a collapsed "more platforms" disclosure.
const PLATFORMS = Object.freeze([
  Object.freeze({ key: "ig", label: "IG", placeholder: "instagram.com/你的帳號", hosts: Object.freeze(["instagram.com"]), featured: true }),
  Object.freeze({ key: "line", label: "LINE", placeholder: "lin.ee 官方帳號連結", hosts: Object.freeze(["lin.ee", "line.me"]), featured: true }),
  Object.freeze({ key: "fb", label: "FB", placeholder: "facebook.com/你的粉專", hosts: Object.freeze(["facebook.com", "fb.com"]), featured: true }),
  Object.freeze({ key: "youtube", label: "YouTube", placeholder: "youtube.com/@你的頻道", hosts: Object.freeze(["youtube.com", "youtu.be"]), featured: true }),
  Object.freeze({ key: "threads", label: "Threads", placeholder: "threads.net/@你的帳號", hosts: Object.freeze(["threads.net", "threads.com"]), featured: true }),
  Object.freeze({ key: "douyin", label: "抖音", placeholder: "douyin.com 或 tiktok.com/@你的帳號", hosts: Object.freeze(["douyin.com", "tiktok.com"]), featured: true }),
  Object.freeze({ key: "legacy", label: "舊頁面", placeholder: "linktr.ee 或 portaly.cc 連結 — 一鍵搬家", hosts: Object.freeze(["linktr.ee", "portaly.cc"]), featured: false }),
  Object.freeze({ key: "xhs", label: "小紅書", placeholder: "xiaohongshu.com 個人主頁連結", hosts: Object.freeze(["xiaohongshu.com", "xhslink.com"]), featured: false }),
  Object.freeze({ key: "whatsapp", label: "WhatsApp", placeholder: "wa.me/你的號碼連結", hosts: Object.freeze(["wa.me", "whatsapp.com"]), featured: false }),
  Object.freeze({ key: "wechat", label: "微信", placeholder: "WeChat 公眾號連結", hosts: Object.freeze(["weixin.qq.com", "wechat.com"]), featured: false }),
  // RL-140(2026-09-18):x / tiktok 各自一個鍵——289 (e) oEmbed 取回的作者頁要進 socials 而不是 warnings。
  //   douyin 列**原樣**(hosts 仍含 tiktok.com):既有頁面已存的 douyin+tiktok.com 列不能因為這一顆變成 HOST_NOT_ALLOWED;新收的 tiktok.com 由 AIB 端字彙表指到 tiktok。
  Object.freeze({ key: "x", label: "X", placeholder: "x.com/你的帳號", hosts: Object.freeze(["x.com", "twitter.com"]), featured: false }),
  Object.freeze({ key: "tiktok", label: "TikTok", placeholder: "tiktok.com/@你的帳號", hosts: Object.freeze(["tiktok.com"]), featured: false })
]);
const PLATFORM_MAP = new Map(PLATFORMS.map((platform) => [platform.key, platform]));

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }

function normalizeUrl(raw) {
  const value = String(raw == null ? "" : raw).trim();
  if (!value) return "";
  if (/[\s"'<>\\]/.test(value)) return null;
  if (value.length > MAX_URL_LENGTH) return null;
  return /^https?:\/\//i.test(value) ? value : "https://" + value;
}

function hostAllowed(hostname, allowed) {
  const host = hostname.toLowerCase().replace(/^www\./, "");
  return allowed.some((entry) => host === entry || host.endsWith("." + entry));
}

// entries: [{platform, url, consented}] — already reduced to submitted rows.
function validateSocialIntake(entries, options = {}) {
  const mutation = String(options.test_mutation || "");
  const errors = [];
  const links = [];
  if (!Array.isArray(entries)) return { ok: false, errors: ["PAGE_SOCIAL_ENTRIES_INVALID"], links };
  const seen = new Set();
  for (const entry of entries) {
    const platform = PLATFORM_MAP.get(entry && entry.platform);
    if (!platform) { errors.push("PAGE_SOCIAL_PLATFORM_UNKNOWN"); continue; }
    if (seen.has(platform.key)) { errors.push("PAGE_SOCIAL_PLATFORM_DUPLICATE:" + platform.key); continue; }
    seen.add(platform.key);
    const hasUrl = String(entry.url == null ? "" : entry.url).trim().length > 0;
    const consented = entry.consented === true;
    // Ticked but empty = nothing to collect, skip silently (select-all UX).
    // The red line stays one-directional: a URL without its consent tick is refused.
    if (hasUrl && !consented && mutation !== "allow-unconsented") { errors.push("PAGE_SOCIAL_CONSENT_REQUIRED:" + platform.key); continue; }
    if (!hasUrl) continue;
    const normalized = normalizeUrl(entry.url);
    if (!normalized) { errors.push("PAGE_SOCIAL_URL_INVALID:" + platform.key); continue; }
    let parsed;
    try { parsed = new URL(normalized); } catch (_) { errors.push("PAGE_SOCIAL_URL_INVALID:" + platform.key); continue; }
    if (parsed.username || parsed.password) { errors.push("PAGE_SOCIAL_URL_CREDENTIALS:" + platform.key); continue; }
    if (parsed.protocol !== "https:" && mutation !== "allow-http") { errors.push("PAGE_SOCIAL_URL_NOT_HTTPS:" + platform.key); continue; }
    if (!hostAllowed(parsed.hostname, platform.hosts) && mutation !== "allow-foreign-host") { errors.push("PAGE_SOCIAL_HOST_NOT_ALLOWED:" + platform.key); continue; }
    links.push({ platform: platform.key, url: parsed.toString(), consented: true });
  }
  return { ok: errors.length === 0, errors: [...new Set(errors)], links };
}

function buildConsentState(input, options = {}) {
  const decidedAt = input.decided_at;
  const state = {
    schema_version: consent.VERSION,
    tenant_id: input.tenant_id,
    subject_type: "person",
    subject_id: input.subject_id,
    policy_version: POLICY_VERSION,
    record_version: 1,
    purposes: {
      necessary: { status: "granted", basis: "contract", source: "server", decided_at: decidedAt },
      personalization: { status: "granted", basis: "consent", source: "account", decided_at: decidedAt },
      analytics: { status: "unset", basis: null, source: null, decided_at: null },
      marketing: { status: "unset", basis: null, source: null, decided_at: null }
    },
    created_at: decidedAt,
    updated_at: decidedAt
  };
  const validation = consent.validateConsentState(state, options);
  if (!validation.ok) fail("PAGE_SOCIAL_CONSENT_STATE_INVALID", validation.errors);
  return state;
}

function buildIntakeRecord(input, options = {}) {
  const mutation = String(options.test_mutation || "");
  const required = ["slug", "tenant_id", "subject_id", "recorded_at"];
  for (const field of required) {
    if (typeof input[field] !== "string" || !input[field].trim()) fail("PAGE_SOCIAL_RECORD_FIELD_MISSING", [field]);
  }
  const result = validateSocialIntake(input.entries, options);
  if (!result.ok) fail("PAGE_SOCIAL_ENTRIES_REJECTED", result.errors);
  if (!result.links.length && mutation !== "allow-empty-record") fail("PAGE_SOCIAL_RECORD_EMPTY");
  const consentState = buildConsentState({ tenant_id: input.tenant_id, subject_id: input.subject_id, decided_at: input.recorded_at }, options);
  return {
    schema_version: VERSION,
    slug: input.slug,
    tenant_id: input.tenant_id,
    subject_id: input.subject_id,
    recorded_at: input.recorded_at,
    links: result.links,
    consent: consentState,
    // ★★★ 2026-07-28:這一塊原本是 `{ unused_material_auto_delete_days: 30, revocable: true }`
    //   —— 一個對使用者的承諾,而**全 repo 沒有任何程式在執行它**。
    //   現在天數與執行狀態都由承諾登記表推導:
    //   `enforcement` 會誠實地說 `declared_not_enforced`,
    //   而想把它變成 `enforced` 的人必須先真的接上執行者(登記表會擋)。
    retention: retentionPromise.retentionBlock(RETENTION_PROMISE_ID)
  };
}

// Append-only JSONL ledger inside the service state directory.
function createSocialIntakeStore(file) {
  if (typeof file !== "string" || !file.trim()) fail("PAGE_SOCIAL_STORE_PATH_REQUIRED");
  return {
    // ★★★ 2026-09-02(Z2):這本帳本原本**只有 append**。
    //   同一支檔案上面第 6 行就寫著它是同意證據,而
    //   `ez2-page-beta-registration.js` 的 `setSocialLinks` 註解宣稱
    //   「同意來自帳本」—— 那句話當時不可能為真,因為沒有任何人讀得到它。
    //   `read()` 與 `append` 分離:讀端拿不到筆,寫端也不因此多一條路。
    read() {
      if (!fs.existsSync(file)) return { rows: Object.freeze([]), malformed_count: 0 };
      const lines = fs.readFileSync(file, "utf8").split("\n").filter((line) => line.trim());
      const rows = [];
      let malformed = 0;
      for (const line of lines) {
        try { rows.push(JSON.parse(line)); } catch (_) { malformed += 1; }
      }
      // 壞掉的行不吞掉:讀得到幾行就回幾行,壞的要數得出來。
      return { rows: Object.freeze(rows), malformed_count: malformed };
    },
    append(record) {
      const intake = buildIntakeRecord(record.input || record, record.options || {});
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const fd = fs.openSync(file, "a", 0o600);
      try {
        fs.writeSync(fd, JSON.stringify(intake) + "\n", null, "utf8");
        fs.fsyncSync(fd);
      } finally {
        fs.closeSync(fd);
      }
      return intake;
    },
    file
  };
}

module.exports = { MAX_URL_LENGTH, PLATFORMS, POLICY_VERSION, VERSION, buildConsentState, buildIntakeRecord, createSocialIntakeStore, normalizeUrl, validateSocialIntake };
