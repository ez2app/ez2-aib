"use strict";

// Shared helpers for S3 real crawl/search provider adapters.
// Env key NAMES only (never values / secrets in tree):
//   EZ2_PAGE_PREFILL_REAL          — master flag ("1"/"true"/"yes")
//   EZ2_PAGE_PREFILL_STAGES        — comma list subset (optional)
//   EZ2_PAGE_SEARCH_API_URL        — search vendor endpoint
//   EZ2_PAGE_SEARCH_API_KEY        — search vendor key (env only)
//   EZ2_PAGE_GMB_API_URL           — Google Business lookup endpoint
//   EZ2_PAGE_GMB_API_KEY           — GMB key (env only)
//   EZ2_PAGE_SOCIAL_API_URL        — social graph lookup endpoint
//   EZ2_PAGE_SOCIAL_API_KEY        — social key (env only)
//   EZ2_PAGE_LINK_IN_BIO_API_URL   — optional link-in-bio resolver
//   EZ2_PAGE_LINK_IN_BIO_API_KEY   — optional key (env only)

const adaptersMod = require("../ez2-page-prefill-adapters.js");

function plain(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nonEmpty(value) {
  if (value == null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (plain(value)) return Object.keys(value).length > 0;
  return true;
}

function nowMs(clock) {
  if (typeof clock === "function") return Number(clock());
  if (typeof clock === "number") return clock;
  return Date.now();
}

function envOf(deps) {
  return plain(deps && deps.env) ? deps.env : (typeof process !== "undefined" ? process.env : {});
}

function readEnv(deps, name) {
  const env = envOf(deps);
  const raw = env && env[name];
  return typeof raw === "string" && raw.trim() ? raw.trim() : "";
}

function assertAuthorized(deps) {
  // Defense in depth — orchestrator already gates; providers refuse network when flag says no.
  if (deps && deps.authorized === false) {
    const err = new Error("PROVIDER_UNAUTHORIZED");
    err.code = "PROVIDER_UNAUTHORIZED";
    throw err;
  }
}

function guardBudget(deps, stage) {
  const budget = deps && deps.budget;
  if (budget && typeof budget.allow === "function" && !budget.allow(stage)) {
    throw typeof budget.budgetError === "function"
      ? budget.budgetError(stage)
      : Object.assign(new Error("PROVIDER_BUDGET_EXHAUSTED:" + stage), { code: "PROVIDER_BUDGET_EXHAUSTED", stage });
  }
}

function chargeBudget(deps, stage, startedMs) {
  const budget = deps && deps.budget;
  if (budget && typeof budget.charge === "function") {
    budget.charge(stage, Math.max(0, nowMs(deps.clock) - startedMs));
  }
}

function pickSourceUrl(ctx, stage) {
  const sources = plain(ctx && ctx.sources) ? ctx.sources : {};
  if (nonEmpty(sources[stage])) return String(sources[stage]);
  if (stage === "website" && nonEmpty(sources.website_url)) return String(sources.website_url);
  if (stage === "link_in_bio" && nonEmpty(sources.link_in_bio_url)) return String(sources.link_in_bio_url);
  if (stage === "social" && nonEmpty(sources.social_url)) return String(sources.social_url);
  if (stage === "google_business" && nonEmpty(sources.place_url)) return String(sources.place_url);
  if (nonEmpty(ctx && ctx.brand_url)) return String(ctx.brand_url);
  return null;
}

function pickQuery(ctx) {
  if (nonEmpty(ctx && ctx.query)) return String(ctx.query);
  if (nonEmpty(ctx && ctx.brand_name)) return String(ctx.brand_name);
  const page = plain(ctx && ctx.page_values) ? ctx.page_values : {};
  if (nonEmpty(page.display_name)) return String(page.display_name);
  return null;
}

function emptyStageResult(stage, deps, extra = {}) {
  const provenance = adaptersMod.buildStageProvenance(stage, {
    clock: deps && deps.clock,
    url: extra.url != null ? extra.url : null,
    fetched_at: extra.fetched_at
  });
  if (stage === "search") provenance.via = "search";
  return {
    stage,
    fields: plain(extra.fields) ? extra.fields : {},
    provenance,
    verified: stage !== "search" && extra.verified !== false,
    provider: extra.provider || "real",
    empty: true
  };
}

function stageResult(stage, fields, deps, meta = {}) {
  const url = meta.url != null ? meta.url : null;
  const provenance = adaptersMod.buildStageProvenance(stage, {
    clock: deps && deps.clock,
    url,
    fetched_at: meta.fetched_at,
    ttl_days: meta.ttl_days
  });
  if (stage === "search" || meta.via === "search") provenance.via = "search";
  return {
    stage,
    fields: plain(fields) ? fields : {},
    provenance,
    verified: meta.verified != null ? Boolean(meta.verified) : stage !== "search",
    provider: meta.provider || "real"
  };
}

function decodeEntities(text) {
  return String(text || "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .trim();
}

function metaContent(html, property) {
  const esc = property.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re1 = new RegExp("<meta[^>]+(?:property|name)=[\"']" + esc + "[\"'][^>]+content=[\"']([^\"']+)[\"'][^>]*>", "i");
  const re2 = new RegExp("<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"']" + esc + "[\"'][^>]*>", "i");
  const m = html.match(re1) || html.match(re2);
  return m ? decodeEntities(m[1]) : "";
}

function parseHtmlFields(html) {
  const text = String(html || "");
  const titleMatch = text.match(/<title[^>]*>([^<]*)<\/title>/i);
  const title = titleMatch ? decodeEntities(titleMatch[1]) : "";
  const ogTitle = metaContent(text, "og:title") || metaContent(text, "twitter:title");
  const ogDesc = metaContent(text, "og:description") || metaContent(text, "description") || metaContent(text, "twitter:description");
  const ogImage = metaContent(text, "og:image") || metaContent(text, "twitter:image");
  const ogUrl = metaContent(text, "og:url");
  const fields = {};
  if (ogTitle || title) fields.display_name = ogTitle || title;
  if (ogDesc) fields.bio = ogDesc;
  if (ogImage) fields.avatar_url = ogImage;
  if (ogUrl) fields.primary_link = ogUrl;
  return fields;
}

function authHeaders(apiKey) {
  if (!apiKey) return {};
  return { Authorization: "Bearer " + apiKey };
}

module.exports = {
  plain,
  nonEmpty,
  nowMs,
  envOf,
  readEnv,
  assertAuthorized,
  guardBudget,
  chargeBudget,
  pickSourceUrl,
  pickQuery,
  emptyStageResult,
  stageResult,
  decodeEntities,
  metaContent,
  parseHtmlFields,
  authHeaders,
  buildStageProvenance: adaptersMod.buildStageProvenance
};
