"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { renderPageDocument } = require("./ez2-page-static-render.js");
const vcard = require("./ez2-page-vcard.js");

const SCHEMA = "2026-07-11.ez2-page-static-bundle.v1";

function fail(code) { const error = new Error(code); error.code = code; throw error; }
function sha256(value) { return crypto.createHash("sha256").update(value).digest("hex"); }
function safeSegment(value, mutation) {
  if (mutation === "allow-path-traversal") return String(value);
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$/.test(value) || value === "." || value === "..") fail("PAGE_BUNDLE_ID_UNSAFE");
  return value;
}
function xml(value) { return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;"); }
function canonicalOf(html) {
  const match = html.match(/<link rel="canonical" href="([^"]+)">/);
  if (!match) fail("PAGE_BUNDLE_CANONICAL_MISSING");
  return match[1].replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
}

function buildStaticBundle(record, seo, options = {}) {
  const mutation = String(options.test_mutation || "");
  let statsPings = false;   // 317 第 16 項
  let membersGate = false;   // 317 第 20 項
  // Opt-in theming (C3): with options.theme the page renders through the design
  // pack and ships its stylesheet alongside. Without it, nothing changes — the
  // plain renderer remains the default so existing callers are untouched.
  let html;
  let themedCss = null;
  let formAction = "'none'";   // 317 第 18 項:頁上有表單 ⇒ 'self'(delivery 回應頭跟 meta 同一份)
  if (options.theme) {
    const themed = require("./ez2-page-theme-renderer.js").renderThemedPage(record, seo, {
      theme: options.theme, shape: options.shape, profile: options.profile, tracking_mode: options.tracking_mode, assets: options.assets, test_mutation: mutation, render_mutation: options.render_mutation, background: options.background, ai_chat: options.ai_chat, avatar_size: options.avatar_size, avatar_shape: options.avatar_shape, rendered_at: options.rendered_at, locale: options.locale, stats_pings: options.stats_pings === true, form_tokens: options.form_tokens,   // 317 第 16 項;第 18 項 form_tokens
      audience: options.audience, members_gate: options.members_gate === true, members_hint: options.members_hint   // 317 第 20 項
    });
    html = themed.html;
    statsPings = themed.stats_pings === true;
    membersGate = themed.members_gate === true;   // 317 第 20 項
    themedCss = themed.css;
    formAction = themed.form_action || "'none'";
  } else {
    html = renderPageDocument(record, seo, { assets: options.assets, test_mutation: mutation });
  }
  const canonical = canonicalOf(html);
  const indexable = record.page.status === "published_indexable";
  const sitemapIncludesPage = indexable || mutation === "include-noindex-sitemap";
  const sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
    (sitemapIncludesPage ? "<url><loc>" + xml(canonical) + "</loc><lastmod>" + xml(record.page.updated_at) + "</lastmod></url>" : "") +
    "</urlset>\n";
  const files = { "index.html": html, "sitemap.xml": sitemap };
  if (themedCss != null) files["page.css"] = themedCss;
  // 名片檔與樣式表同一個模式:渲染期產出、進 manifest、由 delivery 供應。
  // 有 profile 才產得出來(至少要有顯示名稱),沒有就不放——空名片比沒有更糟。
  if (options.profile && typeof options.profile.display_name === "string" && options.profile.display_name.trim()) {
    files["contact.vcf"] = vcard.buildVCard({
      profile: options.profile, canonical, revised_at: record.page.updated_at
    });
  }
  const manifest = {
    schema_version: SCHEMA,
    tenant_id: record.page.tenant_id,
    page_id: record.page.page_id,
    revision_id: record.page.revision_id,
    canonical_url: canonical,
    indexable,
    files: Object.keys(files).sort().map((name) => ({ name, bytes: Buffer.byteLength(files[name]), sha256: mutation === "corrupt-hash" ? "0".repeat(64) : sha256(files[name]) }))
  };
  if (mutation === "nondeterministic-manifest") manifest.build_nonce = crypto.randomBytes(8).toString("hex");
  files["manifest.json"] = JSON.stringify(manifest, null, 2) + "\n";
  const out = { directory: safeSegment(record.page.tenant_id, mutation) + "/" + safeSegment(record.page.page_id, mutation), files, manifest };
  if (statsPings) out.stats_pings = true;   // 317 第 16 項:有 ping 才多這個鍵(沒有的 bundle 物件逐鍵不變),delivery 據此加 connect-src
  if (formAction === "'self'") out.form_action = formAction;   // 317 第 18 項:只有頁上有表單才多這個鍵(既有頁的 bundle 物件逐位元組不變;delivery 讀不到就是 'none')
  if (membersGate) out.members_gate = true;   // 317 第 20 項:有鎖卡才多這個鍵;delivery 據此放寬 form-action
  return out;
}

function writeStaticBundle(input, options = {}) {
  if (!input || typeof input.output_root !== "string" || !path.isAbsolute(input.output_root)) fail("PAGE_BUNDLE_OUTPUT_ROOT_REQUIRED");
  const mutation = String(options.test_mutation || "");
  const bundle = buildStaticBundle(input.record, input.seo, { test_mutation: mutation, theme: input.theme, shape: input.shape, profile: input.profile, tracking_mode: input.tracking_mode });
  const root = path.resolve(input.output_root);
  const target = path.resolve(root, bundle.directory);
  if (mutation !== "allow-path-traversal" && target !== root && !target.startsWith(root + path.sep)) fail("PAGE_BUNDLE_PATH_ESCAPE");
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const token = sha256(bundle.directory + bundle.manifest.revision_id).slice(0, 12);
  const staging = target + ".tmp-" + token;
  const backup = target + ".old-" + token;
  fs.rmSync(staging, { recursive: true, force: true });
  fs.rmSync(backup, { recursive: true, force: true });
  fs.mkdirSync(staging, { recursive: true, mode: 0o700 });
  for (const name of Object.keys(bundle.files)) fs.writeFileSync(path.join(staging, name), bundle.files[name], { mode: 0o600 });
  if (options.fail_after_staging) { fs.rmSync(staging, { recursive: true, force: true }); fail("PAGE_BUNDLE_INJECTED_FAILURE"); }
  if (mutation === "non-atomic-write") {
    fs.mkdirSync(target, { recursive: true, mode: 0o700 });
    for (const name of Object.keys(bundle.files)) {
      fs.copyFileSync(path.join(staging, name), path.join(target, name));
      if (options.fail_during_delivery) fail("PAGE_BUNDLE_INJECTED_FAILURE");
    }
    fs.rmSync(staging, { recursive: true, force: true });
    return Object.assign({ target }, bundle);
  }
  let hadTarget = false;
  try {
    if (options.fail_during_delivery) { fs.rmSync(staging, { recursive: true, force: true }); fail("PAGE_BUNDLE_INJECTED_FAILURE"); }
    if (fs.existsSync(target)) { fs.renameSync(target, backup); hadTarget = true; }
    fs.renameSync(staging, target);
    if (hadTarget) fs.rmSync(backup, { recursive: true, force: true });
  } catch (error) {
    fs.rmSync(staging, { recursive: true, force: true });
    if (hadTarget && !fs.existsSync(target) && fs.existsSync(backup)) fs.renameSync(backup, target);
    throw error;
  }
  return Object.assign({ target }, bundle);
}

module.exports = { SCHEMA, buildStaticBundle, sha256, writeStaticBundle };
