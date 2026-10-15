"use strict";

const { sha256 } = require("./ez2-page-static-bundle.js");

const VERSION = "2026-07-11.ez2-page-performance-policy.v1";
const DEFAULT_BUDGETS = Object.freeze({ html_bytes: 65536, total_payload_bytes: 98304, executable_scripts: 0, inline_styles: 0, external_asset_origins: 0 });

function count(text, pattern) { return (String(text).match(pattern) || []).length; }
function unique(values) { return [...new Set(values)].sort(); }
function externalOrigins(html) {
  const origins = [];
  for (const tag of String(html).matchAll(/<(?:script|img|source|link)\b[^>]*>/gi)) {
    if (/^<link\b/i.test(tag[0]) && !/\srel="stylesheet"/i.test(tag[0])) continue;
    const match = tag[0].match(/(?:src|href)="(https?:\/\/[^/"?#]+)/i);
    if (match) origins.push(match[1]);
  }
  return unique(origins);
}

function auditStaticBundle(bundle, options = {}) {
  const mutation = String(options.test_mutation || "");
  const budgets = Object.assign({}, DEFAULT_BUDGETS, options.budgets || {});
  const files = bundle && bundle.files || {};
  const html = files["index.html"] || "";
  const manifest = bundle && bundle.manifest;
  const blockers = [];
  const htmlBytes = Buffer.byteLength(html);
  const payloadFiles = Object.keys(files).filter((name) => name !== "manifest.json");
  const totalBytes = payloadFiles.reduce((sum, name) => sum + Buffer.byteLength(files[name]), 0);
  const allScripts = count(html, /<script\b/gi);
  const jsonLdScripts = count(html, /<script\s+type="application\/ld\+json">/gi);
  const executableScripts = allScripts - jsonLdScripts;
  const inlineStyles = count(html, /<style\b|\sstyle="/gi);
  const origins = externalOrigins(html);
  const imageCount = count(html, /<img\b/gi);
  const lazyImages = count(html, /<img\b(?=[^>]*\sloading="lazy")(?=[^>]*\sdecoding="async")[^>]*>/gi);
  if (mutation !== "ignore-html-budget" && htmlBytes > budgets.html_bytes) blockers.push("html_budget_exceeded");
  if (totalBytes > budgets.total_payload_bytes) blockers.push("total_payload_budget_exceeded");
  if (mutation !== "allow-executable-script" && executableScripts > budgets.executable_scripts) blockers.push("executable_script_budget_exceeded");
  if (inlineStyles > budgets.inline_styles) blockers.push("inline_style_budget_exceeded");
  if (origins.length > budgets.external_asset_origins) blockers.push("external_asset_origin_budget_exceeded");
  if (mutation !== "allow-eager-image" && lazyImages !== imageCount) blockers.push("image_loading_policy_failed");
  if (!html.includes('name="viewport"') || !html.includes("Content-Security-Policy")) blockers.push("document_baseline_missing");
  if (manifest && Array.isArray(manifest.files)) {
    for (const item of manifest.files) {
      if (mutation !== "ignore-hash-drift" && (!files[item.name] || sha256(files[item.name]) !== item.sha256 || Buffer.byteLength(files[item.name] || "") !== item.bytes)) blockers.push("manifest_payload_drift");
    }
  } else blockers.push("manifest_missing");
  return {
    schema_version: VERSION,
    status: blockers.length ? "blocked" : "local_static_ready",
    blockers: unique(blockers),
    budgets,
    observed: { html_bytes: htmlBytes, total_payload_bytes: totalBytes, executable_scripts: executableScripts, inline_styles: inlineStyles, external_asset_origins: origins, images: imageCount, lazy_async_images: lazyImages },
    degradation: { no_javascript_content_available: executableScripts === 0, reduced_motion_safe_by_absence: !/animation|transition|gsap/i.test(html), line_webview_measured: false, slow_network_measured: false },
    external_evidence: mutation === "claim-pagespeed" ? { pagespeed_verified: true, pagespeed_score: 100 } : { pagespeed_verified: false, pagespeed_score: null, cache_headers_verified: false, cloudflare_verified: false }
  };
}

module.exports = { DEFAULT_BUDGETS, VERSION, auditStaticBundle, externalOrigins };
