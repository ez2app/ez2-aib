"use strict";

// S3: opening parallel prefill orchestration (outline §4.2–4.3).
// Fire-and-forget friendly: startOpeningPrefill returns a job handle; callers need not await the batch.
// Consent gate: unauthorized ⇒ zero adapter / network calls.
// Priority: page present → social snapshot → real providers (flag) / registry / inject → pending / ask-human.
// v3.109 刀04: stub fixtures only with EZ2_PAGE_PREFILL_STUB=1 / options.stub; default adapters are OFF (zero calls).
 // Writes suggestions with block.provenance only (never meta.content_provenance / meta.search_adoption).
// Consumes S2 checklist constants — do not invent item ids.

const checklist = require("./ez2-page-oneshot-checklist.js");
const adaptersMod = require("./ez2-page-prefill-adapters.js");
const demos = require("./ez2-page-template-demos.js");
const blockContract = require("./ez2-page-block-contract.js");

const VERSION = "2026-09-29.ez2-page-oneshot-prefill.v9";

const {
  STATUSES,
  FIND_FOR_ME_POLICY,
  NO_AUTO_WRITE_IDS,
  ITEM_DEFS,
  ITEM_IDS,
  ITEM_DEF_BY_ID,
  findForMeMayAutoWrite,
  resolveItemId
} = checklist;

const PIPELINE_STAGES = adaptersMod.PIPELINE_STAGES;

const DEFAULT_ITEM_TIMEOUT_MS = 2500;
const DEFAULT_TOTAL_BUDGET_MS = 8000;
const DEFAULT_TTL_DAYS = 30;
const PENDING_LABEL_ZH = "待補";
const DEMO_LABEL_ZH = "示範，請改";

// Checklist item id → soft field keys we may fill from page / snapshot / adapters.
const ITEM_FIELD_KEYS = Object.freeze({
  display_name: Object.freeze(["display_name"]),
  bio: Object.freeze(["bio", "status_text"]),
  link_or_social: Object.freeze(["primary_link", "socials"]),
  avatar_cover: Object.freeze(["avatar_url", "cover_url"]),
  cta_links: Object.freeze(["cta_links"]),
  socials: Object.freeze(["socials"]),
  faq: Object.freeze(["faq"]),
  video_or_gallery: Object.freeze(["video_or_gallery"]),
  form_or_product: Object.freeze(["form_or_product"]),
  preferred_theme: Object.freeze(["preferred_theme", "theme_id"])
});

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

// ---- contract-shape helpers (v3.109) ----
// Text block contract (ez2-page-block-contract): payload exactKeys = {text, format}; format ∈ plain|markdown;
// safeText rejects any <tag>-looking run. S4 adopt (fill-gap 18b8637f) writes the same {text, format}.
// Registry + theme renderer reject control chars in profile.bio / display_name / status_text
// (and <> in display_name / status_text); bio ≤ 300, display_name ≤ 80, status_text ≤ 40.
// v3.109 R2: the text BLOCK is not the profile field — block contract allows newlines (safeText only bans \0
//   and tags, ≤ 20000) and both renderers (theme-renderer / static-render) escapeHtml then \n → <br>.
//   So the block payload keeps line breaks (multiLine, BIO_BLOCK_MAX); profile.bio / status_text / the
//   candidate row.value stay singleLine ≤ 300 (registry beta_registration_bio_invalid + theme renderer
//   page_theme_bio_invalid + S4 adopt writes row.value straight into profile.bio).
const TEXT_FORMATS = Object.freeze(["plain", "markdown"]);
const BIO_MAX = 300;
const BIO_BLOCK_MAX = 2000;   // well inside the 20000 text-block contract cap; a bio, not an article
const DISPLAY_NAME_MAX = 80;
const STATUS_TEXT_MAX = 40;
const HTML_TAG_RE = /<\/?[A-Za-z!][^>]*>/g;
const PROVENANCE_SOURCE_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

function stripTags(text) {
  let out = String(text == null ? "" : text);
  // Loop until stable: "<<b>b>" must not re-form a tag after one pass (length strictly shrinks ⇒ terminates).
  for (;;) {
    const next = out.replace(HTML_TAG_RE, "");
    if (next === out) return out;
    out = next;
  }
}

// v3.109 R3: every cap here is a UTF-16 .length budget (block-contract safeText, registry bio / display_name /
//   status_text all measure .length), but a cut must never split a surrogate pair or a grapheme cluster (ZWJ emoji,
//   flags, skin tones, combining marks) — a lone surrogate renders as U+FFFD and half an emoji is a different glyph.
//   Same algorithm as registration seedableStatusText (closure-private there, no shared export ⇒ local copy):
//   Intl.Segmenter graphemes when available, else code points (Array.from); keep whole pieces while ≤ max units.
let graphemeSegmenter = null;
function graphemePieces(text) {
  if (graphemeSegmenter === null) {
    try {
      graphemeSegmenter = typeof Intl !== "undefined" && typeof Intl.Segmenter === "function"
        ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
        : false;
    } catch (_) { graphemeSegmenter = false; }
  }
  return graphemeSegmenter ? Array.from(graphemeSegmenter.segment(text), (item) => item.segment) : Array.from(text);
}

/**
 * Cut to ≤ max UTF-16 units on a grapheme (fallback: code point) boundary. Never leaves a lone surrogate.
 * v3.109 R4: when the FIRST grapheme alone is longer than the budget (e.g. "a" + 400 combining marks, a Zalgo run)
 *   the grapheme walk keeps nothing ⇒ "" ⇒ a real bio / name / FAQ line silently vanished. Then fall back to
 *   code-point truncation (still never splits a surrogate pair): non-empty whenever limit ≥ 2, or limit ≥ 1 and the
 *   first code point is BMP. Normal text is unchanged (the grapheme cut already kept ≥ 1 piece).
 */
function truncateText(value, max) {
  const text = String(value == null ? "" : value);
  const limit = Math.max(0, Math.floor(Number(max) || 0));
  if (text.length <= limit) return text;
  let out = "";
  for (const piece of graphemePieces(text)) {
    if (out.length + piece.length > limit) break;
    out += piece;
  }
  if (!out && limit > 0) {
    for (const point of Array.from(text)) {
      if (out.length + point.length > limit) break;
      out += point;
    }
  }
  return out;
}

function singleLine(text) {
  return stripTags(text).replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/ {2,}/g, " ").trim();
}

/** Block-text normalize: tags + control chars out, line breaks kept (\r\n / \r → \n, 3+ newlines → one blank line). */
function multiLine(text) {
  return stripTags(String(text == null ? "" : text).replace(/\r\n?/g, "\n"))
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f]+/g, " ")
    .split("\n").map((line) => line.replace(/ {2,}/g, " ").trim()).join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * Normalize any incoming bio shape to { text, block_text, format } (format null = not stated by the source).
 *   text       — single line ≤ BIO_MAX: profile.bio / status_text / candidate row.value (registry-safe).
 *   block_text — line breaks kept ≤ BIO_BLOCK_MAX: text-block payload.text only.
 * Accepts: string | {text, format?} (contract) | {body, format?} (legacy pre-v3.109 prefill payload,
 * body wins because the legacy writer kept the demo quote in text) | {bio}. Returns null when empty.
 */
function normalizeBioInput(value) {
  let raw = null;
  let format = null;
  if (typeof value === "string") {
    raw = value;
  } else if (plain(value)) {
    if (typeof value.body === "string" && value.body.trim()) raw = value.body;
    else if (typeof value.text === "string" && value.text.trim()) raw = value.text;
    else if (typeof value.bio === "string" && value.bio.trim()) raw = value.bio;
    if (TEXT_FORMATS.includes(value.format)) format = value.format;
  }
  if (raw == null) return null;
  const text = truncateText(singleLine(raw), BIO_MAX).trim();
  if (!text) return null;
  const blockText = truncateText(multiLine(raw), BIO_BLOCK_MAX).trim();
  return { text, block_text: blockText || text, format };
}

/** Contract text payload for a bio. Format: explicit source format, else keep demo markdown, else plain (= S4 rule). */
function bioTextPayload(text, prevPayload, explicitFormat) {
  const format = TEXT_FORMATS.includes(explicitFormat)
    ? explicitFormat
    : (plain(prevPayload) && prevPayload.format === "markdown" ? "markdown" : "plain");
  return { text, format };
}

function isLegacyBodyTextBlock(block) {
  return plain(block) && block.block_type === "text" && plain(block.payload) &&
    Object.prototype.hasOwnProperty.call(block.payload, "body");
}

/** Legacy {text?, format?, body} text payload → contract {text, format}; body carried the real content. */
function normalizeLegacyTextPayload(payload) {
  const p = plain(payload) ? payload : {};
  const body = typeof p.body === "string" ? stripTags(p.body).replace(/\u0000/g, "").trim() : "";
  const text = body || (typeof p.text === "string" ? p.text : "");
  return { text, format: TEXT_FORMATS.includes(p.format) ? p.format : "plain" };
}

function normalizeDisplayName(value) {
  if (typeof value !== "string") return "";
  return truncateText(singleLine(value).replace(/[<>]/g, "").trim(), DISPLAY_NAME_MAX).trim();
}

/** First link-ish URL on a candidate-like object: url | href (link/block shape) | action.href (CTA shape). */
function linkUrlOf(value) {
  if (!plain(value)) return null;
  for (const raw of [value.url, value.href, plain(value.action) ? value.action.href : undefined]) {
    if (typeof raw === "string" && raw.trim()) return raw.trim();
  }
  return null;
}

function nowIso(clock) {
  if (typeof clock === "function") return new Date(clock()).toISOString();
  if (typeof clock === "number") return new Date(clock).toISOString();
  return new Date().toISOString();
}

function nowMs(clock) {
  if (typeof clock === "function") return Number(clock());
  if (typeof clock === "number") return clock;
  return Date.now();
}

/**
 * Authorization gate for network adapters.
 * Accepts: true | { authorized|consented: true } | consent-state (personalization granted)
 * | social-intake-like { links:[{consented:true}] }.
 */
function isNetworkAuthorized(auth) {
  if (auth === true) return true;
  if (!auth) return false;
  if (auth.authorized === true || auth.consented === true) return true;
  if (plain(auth.purposes) && plain(auth.purposes.personalization) && auth.purposes.personalization.status === "granted") {
    return true;
  }
  if (Array.isArray(auth.links) && auth.links.some((link) => link && link.consented === true && nonEmpty(link.url))) {
    return true;
  }
  if (Array.isArray(auth.entries) && auth.entries.some((entry) => entry && entry.consented === true && nonEmpty(entry.url))) {
    return true;
  }
  return false;
}

function buildProvenance(input = {}) {
  const source = typeof input.source === "string" ? input.source.slice(0, 32) : "prefill";
  const provenance = {
    source,
    url: input.url == null ? null : String(input.url),
    fetched_at: input.fetched_at || nowIso(input.clock),
    ttl_days: input.ttl_days == null ? DEFAULT_TTL_DAYS : input.ttl_days
  };
  if (input.via != null) provenance.via = input.via;
  return provenance;
}

/**
 * Single-work timeout. Never rejects — timed-out / errored results are tagged.
 */
function withTimeout(work, ms, clock) {
  const budget = Math.max(0, Number(ms) || 0);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (payload) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(payload);
    };
    const timer = setTimeout(() => finish({ timed_out: true, value: undefined, error: null }), budget);
    Promise.resolve()
      .then(() => (typeof work === "function" ? work() : work))
      .then(
        (value) => finish({ timed_out: false, value, error: null }),
        (error) => finish({ timed_out: false, value: undefined, error: error || new Error("prefill_work_failed") })
      );
  });
}

function createBudgetTracker(totalMs, clock) {
  const started = nowMs(clock);
  const total = Math.max(0, Number(totalMs) || 0);
  return {
    started_at_ms: started,
    total_ms: total,
    remainingMs() {
      return Math.max(0, total - (nowMs(clock) - started));
    },
    exhausted() {
      return this.remainingMs() <= 0;
    }
  };
}

function readPageValue(pageValues, itemId) {
  if (!plain(pageValues)) return undefined;
  const keys = ITEM_FIELD_KEYS[itemId] || [itemId];
  for (const key of keys) {
    if (nonEmpty(pageValues[key])) return pageValues[key];
  }
  // Allow direct item-id key.
  if (nonEmpty(pageValues[itemId])) return pageValues[itemId];
  return undefined;
}

function readSnapshotValue(snapshots, itemId) {
  if (!plain(snapshots)) return undefined;
  // Prefer first provider that has a useful field (LINE / FB shape from social-snapshot smoke).
  const providers = Object.keys(snapshots);
  for (const provider of providers) {
    const snap = snapshots[provider];
    if (!plain(snap)) continue;
    if (itemId === "display_name" && nonEmpty(snap.display_name)) {
      return { value: snap.display_name, source: "social_snapshot", url: snap.avatar_url || null, provider, fetched_at: snap.fetched_at };
    }
    if (itemId === "bio") {
      const bio = normalizeBioInput(snap.status_text);
      if (bio) return { value: bio.text, source: "social_snapshot", url: null, provider, fetched_at: snap.fetched_at };
    }
    if (itemId === "avatar_cover" && nonEmpty(snap.avatar_url)) {
      return { value: { avatar_url: snap.avatar_url }, source: "social_snapshot", url: snap.avatar_url, provider, fetched_at: snap.fetched_at };
    }
  }
  return undefined;
}

function itemStatusFromChecklist(checklistInput, itemId) {
  if (!plain(checklistInput)) return "missing";
  const items = plain(checklistInput.items) ? checklistInput.items : checklistInput;
  const raw = items[itemId];
  if (typeof raw === "string") return checklist.normalizeStatus(raw);
  if (plain(raw) && raw.status != null) return checklist.normalizeStatus(raw.status);
  return "missing";
}

function isNoAutoWriteItem(itemId) {
  const id = resolveItemId(itemId) || itemId;
  return NO_AUTO_WRITE_IDS.includes(id);
}

function candidateFromValue(value, provenance, label) {
  if (value == null) return null;
  if (typeof value === "string") {
    const row = { label: label || value, source: provenance && provenance.source, fetched_at: provenance && provenance.fetched_at };
    if (/^https?:\/\//i.test(value)) row.url = value;
    // Non-URL text (bio / display_name under find_for_me): carry value so S4 adopt has something to write
    // (resolveAdoptCandidate reads value/href/url — label alone ⇒ fill_gap_adopt_empty).
    else row.value = value;
    if (provenance && provenance.via) row.via = provenance.via;
    return row;
  }
  if (plain(value) && linkUrlOf(value)) {
    // url | href | action.href all land on url (S4 resolveAdoptCandidate: href ?? url).
    const link = linkUrlOf(value);
    return {
      label: label || value.label || link,
      url: link,
      source: (provenance && provenance.source) || value.source,
      via: (provenance && provenance.via) || value.via,
      fetched_at: (provenance && provenance.fetched_at) || value.fetched_at
    };
  }
  if (plain(value) && nonEmpty(value.avatar_url)) {
    return {
      label: label || "avatar",
      url: value.avatar_url,
      source: provenance && provenance.source,
      via: provenance && provenance.via,
      fetched_at: provenance && provenance.fetched_at
    };
  }
  if (Array.isArray(value)) {
    return value.map((entry) => candidateFromValue(entry, provenance, label)).filter(Boolean);
  }
  return {
    label: label || String(itemLabel(value)),
    source: provenance && provenance.source,
    via: provenance && provenance.via,
    fetched_at: provenance && provenance.fetched_at,
    value
  };
}

function itemLabel(value) {
  if (plain(value) && value.label) return value.label;
  return "candidate";
}

function flattenCandidates(maybe) {
  if (!maybe) return [];
  if (Array.isArray(maybe)) return maybe.flatMap((row) => (Array.isArray(row) ? row : [row])).filter(Boolean);
  return [maybe];
}

function pickFieldsFromPipeline(results, itemId) {
  const keys = ITEM_FIELD_KEYS[itemId] || [itemId];
  for (const result of results) {
    if (!result || !plain(result.fields)) continue;
    for (const key of keys) {
      let value = result.fields[key];
      let valueFormat = null;
      let valueText = null;
      if (itemId === "bio") {
        // Providers may hand back {text, format} / legacy {body}; empty shapes fall through to the next stage.
        const bio = normalizeBioInput(value);
        if (!bio) continue;
        value = bio.text;
        valueFormat = bio.format;
        if (bio.block_text !== bio.text) valueText = bio.block_text;
      }
      if (nonEmpty(value)) {
        const picked = {
          value,
          provenance: result.provenance,
          verified: result.verified !== false,
          stage: result.stage
        };
        if (valueFormat) picked.value_format = valueFormat;
        if (valueText) picked.value_text = valueText;   // multi-line block text; value stays single-line
        return picked;
      }
    }
  }
  return null;
}

function suggestionBlock(itemId, value, provenance) {
  return {
    // Suggestion only — caller writes into a real block; we attach provenance for that write path.
    item_id: itemId,
    value,
    provenance: provenance ? Object.assign({}, provenance) : undefined
  };
}

/**
 * Run stub network pipeline in pinned order. Stops early once all needed fields are covered
 * is optional; S3 first cut always walks the list but respects per-call timeout + total budget.
 */
async function runNetworkPipeline(adapters, ctx, options = {}) {
  const clock = options.clock;
  const itemTimeout = options.item_timeout_ms == null ? DEFAULT_ITEM_TIMEOUT_MS : options.item_timeout_ms;
  const budget = options.budget || createBudgetTracker(options.total_budget_ms == null ? DEFAULT_TOTAL_BUDGET_MS : options.total_budget_ms, clock);
  const stages = Array.isArray(options.stages) ? options.stages : PIPELINE_STAGES;
  const results = [];
  const errors = [];
  let networkCalls = 0;

  for (const stage of stages) {
    if (budget.exhausted()) {
      errors.push({ stage, code: "TOTAL_BUDGET_EXHAUSTED" });
      break;
    }
    const adapter = adapters && adapters[stage];
    if (typeof adapter !== "function") {
      errors.push({ stage, code: "ADAPTER_MISSING" });
      continue;
    }
    const slice = Math.min(itemTimeout, budget.remainingMs());
    networkCalls += 1;
    const outcome = await withTimeout(() => adapter(ctx), slice, clock);
    if (outcome.timed_out) {
      errors.push({ stage, code: "ITEM_TIMEOUT" });
      continue;
    }
    if (outcome.error) {
      errors.push({ stage, code: (outcome.error && outcome.error.code) || "ADAPTER_ERROR", message: String(outcome.error && outcome.error.message || outcome.error) });
      continue;
    }
    if (outcome.value) results.push(outcome.value);
  }
  return { results, errors, network_calls: networkCalls, budget_exhausted: budget.exhausted() };
}

function emptyItemResult(itemId, extra = {}) {
  return Object.assign({
    item_id: itemId,
    status: "pending",
    value: undefined,
    candidates: undefined,
    provenance: undefined,
    reason: "pending",
    label_zh: PENDING_LABEL_ZH
  }, extra);
}

/**
 * Resolve one checklist item against page → snapshot → network results.
 * Never throws.
 */
function resolveItemPrefill(itemId, input, network) {
  const status = itemStatusFromChecklist(input.checklist, itemId);
  const pageValue = readPageValue(input.page_values, itemId);

  if (status === "have" || nonEmpty(pageValue)) {
    return {
      item_id: itemId,
      status: "kept",
      value: pageValue,
      candidates: undefined,
      provenance: undefined,
      reason: nonEmpty(pageValue) ? "page_present" : "checklist_have",
      label_zh: undefined
    };
  }

  const snap = readSnapshotValue(input.social_snapshots, itemId);
  if (snap && nonEmpty(snap.value)) {
    const provenance = buildProvenance({
      source: "social_snapshot",
      url: snap.url,
      fetched_at: snap.fetched_at || nowIso(input.clock),
      ttl_days: DEFAULT_TTL_DAYS,
      clock: input.clock
    });
    // NO_AUTO_WRITE_IDS (avatar / primary / CTA): never auto-fill value — candidates only.
    // find_for_me on any item likewise stays candidates-only (human adopt).
    if (isNoAutoWriteItem(itemId) || status === "find_for_me") {
      return {
        item_id: itemId,
        status: "candidates_only",
        value: undefined,
        candidates: flattenCandidates(candidateFromValue(snap.value, provenance)),
        provenance,
        reason: status === "find_for_me" ? "find_for_me_policy" : "no_auto_write_policy",
        search_adoption: "candidates_only",
        label_zh: undefined
      };
    }
    return {
      item_id: itemId,
      status: "filled",
      value: snap.value,
      candidates: undefined,
      provenance,
      reason: "social_snapshot",
      label_zh: undefined
    };
  }

  const picked = network && pickFieldsFromPipeline(network.results || [], itemId);
  if (picked) {
    const provenance = picked.provenance
      ? Object.assign({}, picked.provenance)
      : buildProvenance({ source: picked.stage || "prefill", clock: input.clock });
    const fromSearch = provenance.via === "search" || picked.stage === "search" || picked.verified === false;
    const noAuto = isNoAutoWriteItem(itemId);
    const findForMe = status === "find_for_me";

    // Gate: find_for_me OR NO_AUTO_WRITE_IDS never get value auto-fill (verified stages included).
    // Unverified/search on no-auto ids keeps reason unverified_https for smoke/contract.
    if (findForMe || noAuto) {
      let reason;
      if (findForMe) reason = "find_for_me_policy";
      else if (fromSearch) reason = "unverified_https";
      else reason = "no_auto_write_policy";
      return {
        item_id: itemId,
        status: "candidates_only",
        value: undefined,
        candidates: flattenCandidates(candidateFromValue(picked.value, provenance)),
        provenance,
        reason,
        search_adoption: "candidates_only",
        label_zh: undefined
      };
    }

    const filled = {
      item_id: itemId,
      status: "filled",
      value: picked.value,
      candidates: undefined,
      provenance,
      reason: fromSearch ? "search_fallback" : "network_pipeline",
      label_zh: undefined
    };
    if (picked.value_format) filled.value_format = picked.value_format;
    if (picked.value_text) filled.value_text = picked.value_text;
    return filled;
  }

  // Still empty.
  if (network && network.budget_exhausted) {
    return emptyItemResult(itemId, { reason: "total_budget", label_zh: PENDING_LABEL_ZH });
  }
  if (network && (network.errors || []).some((e) => e.code === "ITEM_TIMEOUT")) {
    return emptyItemResult(itemId, { reason: "timeout", label_zh: PENDING_LABEL_ZH });
  }
  if (!input.authorized) {
    return emptyItemResult(itemId, {
      reason: "unauthorized",
      label_zh: status === "find_for_me" ? PENDING_LABEL_ZH : DEMO_LABEL_ZH
    });
  }
  return emptyItemResult(itemId, {
    reason: "ask_human",
    label_zh: PENDING_LABEL_ZH
  });
}

/**
 * Core batch. Safe to not await — errors are swallowed into the result.
 */
async function runOpeningPrefill(input = {}, options = {}) {
  const clock = options.clock;
  const authorized = isNetworkAuthorized(input.authorized != null ? input.authorized : input.auth != null ? input.auth : input.consent);
  const adapterFactory = typeof adaptersMod.createAdapters === "function"
    ? adaptersMod.createAdapters
    : adaptersMod.createStubAdapters;
  const adapters = options.adapters || (authorized ? adapterFactory({
    fetch: options.fetch,
    clock,
    fixtures: options.fixtures,
    delays_ms: options.delays_ms,
    fail_stages: options.fail_stages,
    adapters: options.adapter_overrides,
    // Real crawl/search providers: OFF by default. Opt in via options.realProviders
    // or env EZ2_PAGE_PREFILL_REAL=1 (see shared/ez2-page-prefill-providers).
    // Stub fixtures (test-only, v3.109 刀04): options.stub === true or env EZ2_PAGE_PREFILL_STUB=1.
    // Neither + no registry/inject/real ⇒ adapters mode "off" ⇒ zero adapter calls below.
    stub: options.stub,
    realProviders: options.realProviders,
    real_providers: options.real_providers,
    realStages: options.realStages || options.real_stages,
    env: options.env,
    authorized: true,
    total_budget_ms: options.provider_total_budget_ms != null ? options.provider_total_budget_ms : options.total_budget_ms,
    per_provider_budget_ms: options.per_provider_budget_ms,
    per_provider_max_calls: options.per_provider_max_calls,
    provider_budget: options.provider_budget
  }) : null);

  const itemIds = Array.isArray(options.item_ids) && options.item_ids.length
    ? options.item_ids.map((id) => resolveItemId(id) || id).filter((id) => ITEM_IDS.includes(id))
    : ITEM_IDS.slice();

  const ctx = {
    page_values: input.page_values || {},
    social_snapshots: input.social_snapshots || {},
    sources: input.sources || {},
    brand_name: input.brand_name || (input.page_values && input.page_values.display_name) || null
  };

  let network = { results: [], errors: [], network_calls: 0, budget_exhausted: false };
  // v3.109 刀04: disabled adapter map (production default, no stub flag / real / registry / inject) ⇒
  // skip the pipeline entirely: no adapter call, no fields ⇒ the plan carries no profile_patch / ops.
  const adaptersOff = Boolean(adapters && (adapters.disabled === true || adapters.mode === "off"));
  if (authorized && adapters && !adaptersOff) {
    try {
      // When real/registry/inject uses a stage subset, prefer the active union for the
      // pipeline unless caller passed options.stages explicitly — avoids stub/empty stages
      // racing real fills. pipeline_stages = union(injected, registered, real) in pin order;
      // fall back to real_stages / registered_stages / injected_stages for older adapter maps.
      let pipelineStages = options.stages;
      if (!Array.isArray(pipelineStages) && adapters) {
        if (Array.isArray(adapters.pipeline_stages) && adapters.pipeline_stages.length) {
          pipelineStages = adapters.pipeline_stages.slice();
        } else {
          const want = new Set();
          for (const list of [adapters.real_stages, adapters.registered_stages, adapters.injected_stages]) {
            if (Array.isArray(list)) for (const s of list) want.add(s);
          }
          if (want.size) {
            pipelineStages = PIPELINE_STAGES.filter((s) => want.has(s));
          }
        }
      }
      network = await runNetworkPipeline(adapters, ctx, {
        clock,
        item_timeout_ms: options.item_timeout_ms,
        total_budget_ms: options.total_budget_ms,
        budget: options.budget,
        stages: pipelineStages
      });
    } catch (error) {
      network = {
        results: [],
        errors: [{ stage: "*", code: "PIPELINE_THROW", message: String(error && error.message || error) }],
        network_calls: 0,
        budget_exhausted: false
      };
    }
  }

  const items = Object.create(null);
  const blocks = [];
  for (const itemId of itemIds) {
    let row;
    try {
      row = resolveItemPrefill(itemId, {
        checklist: input.checklist,
        page_values: input.page_values,
        social_snapshots: input.social_snapshots,
        authorized,
        clock
      }, network);
    } catch (error) {
      row = emptyItemResult(itemId, {
        reason: "resolve_error",
        label_zh: PENDING_LABEL_ZH,
        error: String(error && error.message || error)
      });
    }
    items[itemId] = row;
    if (row.status === "filled" && row.provenance) {
      blocks.push(suggestionBlock(itemId, row.value, row.provenance));
    }
    if (typeof options.on_item === "function") {
      try { options.on_item(row); } catch (_) { /* progress hooks must not kill the batch */ }
    }
  }

  return {
    ok: true,
    schema_version: VERSION,
    authorized,
    adapters_mode: adapters && typeof adapters.mode === "string" ? adapters.mode : null,
    network_calls: network.network_calls,
    network_errors: network.errors,
    budget_exhausted: Boolean(network.budget_exhausted),
    items,
    blocks,
    find_for_me_policy: FIND_FOR_ME_POLICY,
    completed_at: nowIso(clock)
  };
}

/**
 * Fire-and-forget friendly entry. Returns a job handle immediately; `promise` settles with the result.
 * Callers may ignore the promise (parallel with skeleton / checklist).
 */
function startOpeningPrefill(input = {}, options = {}) {
  const jobId = "prefill_" + nowMs(options.clock).toString(36) + "_" + Math.floor(Math.random() * 1e6).toString(36);
  const state = { job_id: jobId, status: "running", result: null, error: null };

  const promise = Promise.resolve()
    .then(() => runOpeningPrefill(input, options))
    .then((result) => {
      state.status = "done";
      state.result = result;
      return result;
    })
    .catch((error) => {
      // Contract: never leave the caller with a thrown batch failure.
      const fallback = {
        ok: true,
        schema_version: VERSION,
        authorized: false,
        network_calls: 0,
        network_errors: [{ stage: "*", code: "BATCH_GUARD", message: String(error && error.message || error) }],
        budget_exhausted: false,
        items: Object.create(null),
        blocks: [],
        find_for_me_policy: FIND_FOR_ME_POLICY,
        completed_at: nowIso(options.clock),
        label_zh: PENDING_LABEL_ZH
      };
      for (const id of ITEM_IDS) fallback.items[id] = emptyItemResult(id, { reason: "batch_guard" });
      state.status = "done";
      state.result = fallback;
      state.error = String(error && error.message || error);
      return fallback;
    });

  return {
    job_id: jobId,
    promise,
    getStatus() {
      return { job_id: state.job_id, status: state.status, result: state.result, error: state.error };
    }
  };
}



/**
 * Soft fields that may auto-fill into draft.profile / demo blocks.
 * NO_AUTO_WRITE_IDS stay candidates_only (never land in profile_patch / ops value writes).
 */
const SOFT_PROFILE_FIELDS = Object.freeze({
  display_name: "display_name",
  bio: "bio"
});

function cloneDraft(draft) {
  if (!plain(draft)) return { profile: {}, blocks: [], appearance: {}, meta: {} };
  return {
    profile: plain(draft.profile) ? JSON.parse(JSON.stringify(draft.profile)) : {},
    blocks: Array.isArray(draft.blocks) ? JSON.parse(JSON.stringify(draft.blocks)) : [],
    appearance: plain(draft.appearance) ? JSON.parse(JSON.stringify(draft.appearance)) : {},
    meta: plain(draft.meta) ? JSON.parse(JSON.stringify(draft.meta)) : {}
  };
}

// Block-contract provenanceErrors: source ~ /^[a-z0-9][a-z0-9_-]{0,31}$/, url null | http(s) ≤2048,
// fetched_at parseable, ttl_days null | int 0–3650. One bad provenance ⇒ applyDraftOps rejects the op.
function sanitizeProvenance(provenance) {
  if (!plain(provenance)) return undefined;
  let source = typeof provenance.source === "string"
    ? provenance.source.toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^[^a-z0-9]+/, "").slice(0, 32)
    : "";
  if (!PROVENANCE_SOURCE_RE.test(source)) source = "prefill";
  const url = typeof provenance.url === "string" && /^https?:\/\//i.test(provenance.url) && provenance.url.length <= 2048
    ? provenance.url
    : null;
  const fetched = typeof provenance.fetched_at === "string" && Number.isFinite(Date.parse(provenance.fetched_at))
    ? provenance.fetched_at
    : nowIso();
  const ttl = provenance.ttl_days === null
    ? null
    : (Number.isInteger(provenance.ttl_days) && provenance.ttl_days >= 0 && provenance.ttl_days <= blockContract.PROVENANCE_TTL_MAX
      ? provenance.ttl_days
      : DEFAULT_TTL_DAYS);
  const out = { source, url, fetched_at: fetched, ttl_days: ttl };
  if (provenance.via === "search") out.via = "search";
  return out;
}

function findDemoBlock(blocks, type) {
  if (!Array.isArray(blocks)) return null;
  return blocks.find((b) => b && b.block_type === type && demos.isDemoBlock(b)) || null;
}

let socialRules = null;
function loadSocialRules() {
  if (socialRules) return socialRules;
  try {
    socialRules = {
      icons: require("./ez2-page-social-icons.js"),
      intake: require("./ez2-page-social-intake.js")
    };
  } catch (_) {
    socialRules = { icons: null, intake: null };
  }
  return socialRules;
}

// profile.socials is published as registry social_links: exact {platform,url}, known platform (social-icons),
// no duplicate platform, ≤ DISPLAY_ORDER.length, URL passes social-intake. Filter here so publish never 422s.
function socialsFromValue(value) {
  if (!Array.isArray(value)) return null;
  const rules = loadSocialRules();
  const cap = rules.icons && Array.isArray(rules.icons.DISPLAY_ORDER) ? rules.icons.DISPLAY_ORDER.length : 11;
  const seen = new Set();
  const links = [];
  for (const row of value) {
    if (!plain(row)) continue;
    const platform = typeof row.platform === "string" ? row.platform.trim().slice(0, 32) : null;
    const url = typeof row.url === "string" ? row.url.trim() : null;
    if (!platform || !url || !/^https:\/\//i.test(url) || seen.has(platform)) continue;
    if (rules.icons && typeof rules.icons.has === "function" && !rules.icons.has(platform)) continue;
    if (rules.intake && typeof rules.intake.validateSocialIntake === "function") {
      let verdict = null;
      try { verdict = rules.intake.validateSocialIntake([{ platform, url, consented: true }]); } catch (_) { verdict = null; }
      if (!verdict || verdict.ok !== true) continue;
    }
    seen.add(platform);
    links.push({ platform, url });
    if (links.length >= cap) break;
  }
  return links.length ? links : null;
}

function faqPayloadFromValue(value) {
  const items = Array.isArray(value) ? value : (plain(value) && Array.isArray(value.items) ? value.items : null);
  if (!items || !items.length) return null;
  const out = [];
  // Contract: 1–FAQ_ITEMS_MAX items, q ≤ FAQ_Q_MAX, a ≤ FAQ_A_MAX, safeText (no <tag>, no NUL).
  for (const row of items) {
    if (out.length >= blockContract.FAQ_ITEMS_MAX) break;
    if (!plain(row)) continue;
    const q = typeof row.q === "string" ? truncateText(stripTags(row.q).replace(/\u0000/g, "").trim(), blockContract.FAQ_Q_MAX).trim() : "";
    const a = typeof row.a === "string" ? truncateText(stripTags(row.a).replace(/\u0000/g, "").trim(), blockContract.FAQ_A_MAX).trim() : "";
    if (q && a) out.push({ q, a });
  }
  if (!out.length) return null;
  return { open_first: true, items: out };
}

// ---- Re-run idempotency (v3.109 R3) ----
// server kickOpeningPrefill fires on EVERY theme select and consent with an empty checklist, and
// applyTemplateDraft re-seeds the theme's demo blocks each time (own blocks kept). v3.108/R2 therefore stacked a
// new FAQ (prefill FAQ carries provenance ⇒ not demo ⇒ "no demo FAQ" ⇒ create) and wrote the bio again onto every
// freshly seeded demo text block (gate repro nebula→nebula→starlight→starlight: FAQ 1→2→3→4, bio text 0→0→1→2).
// Rule: a soft item already present in the draft is done — never create a second one, never overwrite it.
// Markers reused from the codebase (no new flag):
//   demo      template-demos.isDemoBlock (signature = type + payload; one edited character ⇒ own block) — the same
//             judge findDemoBlock / registration withDemoFlag use; ONESHOT_CREATOR_PROFILE for the scaffold profile.
//   prefill   block.provenance present: the workspace deletes provenance whenever a payload is written without
//             one (editor updateBlock 292 / applyDraftOps RL-111 甲) ⇒ only fetched, untouched content keeps it.
//   user      neither ⇒ the person's own (or a prefill block they edited — indistinguishable by design).
function faqItemsKey(payload) {
  const items = plain(payload) && Array.isArray(payload.items) ? payload.items : [];
  return JSON.stringify(items.map((row) => [
    plain(row) && typeof row.q === "string" ? row.q.trim() : "",
    plain(row) && typeof row.a === "string" ? row.a.trim() : ""
  ]));
}

/** "demo" | "prefill" (fetched, untouched) | "user" — see the re-run idempotency note above. */
function prefillBlockState(block) {
  if (!plain(block)) return "user";
  if (demos.isDemoBlock(block)) return "demo";
  if (plain(block.provenance)) return "prefill";
  return "user";
}

/**
 * The single FAQ write a prefill payload may make against the current blocks. Never throws.
 * Returns { action: "skip" | "update" | "create", block_id, reason }:
 *   skip   faq_identical       some FAQ already holds exactly these items
 *   skip   faq_prefill_present an untouched prefill FAQ exists (earlier run) — no second FAQ
 *   skip   faq_user_owned      a user-owned FAQ exists — never overwritten, no duplicate beside it
 *   update faq_demo            only demo FAQ(s) ⇒ replace the demo in place (v3.108 behavior)
 *   create faq_new             no FAQ block at all
 */
function planFaqWrite(blocks, payload) {
  const faqs = (Array.isArray(blocks) ? blocks : []).filter((b) => plain(b) && b.block_type === "faq");
  const key = faqItemsKey(payload);
  const identical = faqs.find((b) => faqItemsKey(b.payload) === key);
  if (identical) return { action: "skip", block_id: identical.block_id || null, reason: "faq_identical" };
  const rows = faqs.map((block) => ({ block, state: prefillBlockState(block) }));
  const own = rows.find((row) => row.state === "user");
  if (own) return { action: "skip", block_id: own.block.block_id || null, reason: "faq_user_owned" };
  const prefill = rows.find((row) => row.state === "prefill");
  if (prefill) return { action: "skip", block_id: prefill.block.block_id || null, reason: "faq_prefill_present" };
  const demo = rows.find((row) => row.state === "demo");
  if (demo) return { action: "update", block_id: demo.block.block_id || null, reason: "faq_demo" };
  return { action: "create", block_id: null, reason: "faq_new" };
}

function isDemoProfileValue(field, value) {
  const demoProfile = demos.ONESHOT_CREATOR_PROFILE || {};
  if (field === "socials") return JSON.stringify(value) === JSON.stringify(demoProfile.socials);
  return typeof value === "string" && typeof demoProfile[field] === "string" && value.trim() === demoProfile[field];
}

/** Draft profile already carries a real (non-scaffold-demo) value for this soft field ⇒ prefill must not overwrite. */
function profileFieldPresent(profile, field) {
  if (!plain(profile) || !nonEmpty(profile[field])) return false;
  return !isDemoProfileValue(field, profile[field]);
}

/**
 * A text block that is an earlier prefill / S4-adopt bio write (fetched, untouched), as opposed to any other text block
 * that merely carries provenance.
 * v3.109 R4: R3 counted EVERY non-demo text block with provenance. But AIB, when it fills display_name / status_text
 *   from search, must also add a text block with visibility "hidden" + provenance.via "search" (registration
 *   draftHasSearchSource, spec 289 (h) §二 2 — profile fields have no provenance slot, the hidden block is the marker).
 *   That marker made the bio item skip with profile.bio still empty ⇒ bio never written.
 * Signal = visibility. Both bio writers (this planner, fill-gap adopt) only ever `update` a seeded demo text block
 *   (demos are seeded visibility "public"; an update op never changes visibility) ⇒ a prefill bio block is visible;
 *   the AIB search marker is hidden by spec. Text matching was rejected: the marker records the searched
 *   status_text, and the bio candidate is often that same string (readSnapshotValue: bio ← status_text), so a text
 *   match would re-create exactly this bug; and the candidate text drifts between runs (search), so matching alone
 *   could not block run 2+. Provenance cannot carry a "bio" kind (contract PROVENANCE_KEYS is closed).
 * Duplication guard unchanged: the block the planner wrote in run 1 is public + provenance ⇒ still blocks run 2+, and
 *   every run that writes the block also writes profile.bio (bio_profile_present is the first-line guard anyway).
 */
function isPrefillBioBlock(block) {
  return plain(block) && block.block_type === "text" && block.visibility !== "hidden" && prefillBlockState(block) === "prefill";
}

/**
 * Bio already present ⇒ reason string, else null. A bio is present when the draft holds a fetched, untouched, visible
 * text block (isPrefillBioBlock — prefill / adopt wrote the bio earlier) or profile.bio is a real value (earlier
 * prefill / S4 adopt / the person). Demo text blocks are never evidence (applyTemplateDraft re-seeds them); hidden
 * provenance text blocks (AIB search marker) are never evidence either.
 */
function bioPresence(draft, skipBlockIds) {
  const skip = Array.isArray(skipBlockIds) ? skipBlockIds : [];
  const blocks = plain(draft) && Array.isArray(draft.blocks) ? draft.blocks : [];
  if (blocks.some((b) => isPrefillBioBlock(b) && !skip.includes(b.block_id))) {
    return "bio_prefill_text_present";
  }
  if (plain(draft) && profileFieldPresent(draft.profile, "bio")) return "bio_profile_present";
  return null;
}

const PROBE_AT = "2026-01-01T00:00:00.000Z";

/**
 * Validate what one prefill op contributes (block_type + payload + provenance) against the block contract,
 * using a synthetic record so unrelated draft-record fields never veto the op. Returns error codes ([] = ok).
 * update whose block is not in the draft-shaped input ⇒ type unknown ⇒ [] (the writer's own check decides).
 */
function prefillOpErrors(op, blocks) {
  if (!plain(op)) return ["prefill_op_invalid"];
  if (op.op !== "update" && op.op !== "create") return ["prefill_op_unsupported"];
  let blockType = op.block_type;
  if (op.op === "update") {
    const current = Array.isArray(blocks) ? blocks.find((b) => b && b.block_id === op.block_id) : null;
    if (!current) return [];
    blockType = current.block_type;
  }
  const probe = {
    schema_version: blockContract.VERSION,
    tenant_id: "tenant_probe",
    page_id: "page_probe",
    block_id: "prefill_probe",
    block_type: blockType,
    version: 1,
    position: 0,
    visibility: op.visibility || "public",
    payload: op.payload,
    created_at: PROBE_AT,
    updated_at: PROBE_AT
  };
  if (plain(op.provenance)) probe.provenance = op.provenance;
  try {
    const verdict = blockContract.validatePageBlock(probe);
    return verdict && verdict.ok ? [] : ((verdict && verdict.errors) || ["prefill_op_invalid"]);
  } catch (error) {
    return ["prefill_op_validate_threw:" + String(error && error.message || error)];
  }
}

/**
 * Apply a runOpeningPrefill result into a draft-shaped object.
 * Pure + fail-soft: never throws; timeouts/pending → 待補; apply errors → 示範，請改.
 * NO_AUTO_WRITE / candidates_only → meta.prefill_candidates only (person adopts).
 *
 * Returns { ok, draft, profile_patch, ops, candidates, pending, applied, skipped, labels }.
 * Writers (AIB apply / server kick) push profile_patch + ops into the real draft store.
 */
function applyPrefillResultToDraft(draftInput, prefillResult, options = {}) {
  const draft = cloneDraft(draftInput);
  const profilePatch = {};
  const ops = [];
  const candidates = Object.create(null);
  const pending = Object.create(null);
  const applied = [];
  const skipped = [];
  const labels = Object.create(null);
  const opErrors = [];
  const legacyNormalized = [];
  let faqDecision = null;

  // Legacy compat: pre-v3.109 prefill wrote text blocks as {…demo payload, body: bio}. Contract rejects
  // the extra key and the renderer shows payload.text (the demo quote). Normalize in the returned draft
  // to {text: body, format}; the bio write below may then target it.
  for (const block of draft.blocks) {
    try {
      if (isLegacyBodyTextBlock(block)) {
        block.payload = normalizeLegacyTextPayload(block.payload);
        legacyNormalized.push(block.block_id || null);
      }
    } catch (_) { /* fail soft */ }
  }

  // Every op is contract-checked before it enters the plan: one bad op must never ride along and make
  // the writer reject the whole batch (the v3.108 half-write: profile saved, all block ops dropped).
  const pushOp = (itemId, op) => {
    const errors = prefillOpErrors(op, draft.blocks);
    if (errors.length) {
      opErrors.push({ item_id: itemId, op: op && op.op, block_id: (op && op.block_id) || null, block_type: (op && op.block_type) || null, errors });
      return false;
    }
    ops.push(op);
    return true;
  };

  if (!plain(prefillResult) || !plain(prefillResult.items)) {
    return {
      ok: true,
      draft,
      profile_patch: profilePatch,
      ops,
      candidates,
      pending,
      applied,
      skipped: ["no_prefill_items"],
      labels,
      op_errors: opErrors,
      legacy_normalized: legacyNormalized,
      faq_decision: faqDecision,
      fail_soft: true
    };
  }

  const items = prefillResult.items;
  const itemIds = Array.isArray(options.item_ids) && options.item_ids.length
    ? options.item_ids
    : Object.keys(items);

  for (const rawId of itemIds) {
    const itemId = resolveItemId(rawId) || rawId;
    let row;
    try {
      row = items[itemId] || items[rawId];
      if (!plain(row)) {
        skipped.push(itemId);
        continue;
      }

      if (row.status === "kept") {
        skipped.push(itemId);
        continue;
      }

      if (row.status === "pending") {
        pending[itemId] = {
          reason: row.reason || "pending",
          label_zh: row.label_zh || PENDING_LABEL_ZH
        };
        labels[itemId] = row.label_zh || PENDING_LABEL_ZH;
        // Soft: mark on meta so editor can show 待補 without blocking.
        continue;
      }

      if (row.status === "candidates_only" || row.search_adoption === "candidates_only" || isNoAutoWriteItem(itemId)) {
        const list = Array.isArray(row.candidates) ? row.candidates.filter(Boolean) : [];
        if (list.length) {
          candidates[itemId] = {
            candidates: list,
            provenance: sanitizeProvenance(row.provenance),
            search_adoption: "candidates_only",
            reason: row.reason || "no_auto_write_policy"
          };
        }
        // Never write avatar / primary / CTA into profile or blocks.
        skipped.push(itemId + ":candidates_only");
        continue;
      }

      if (row.status !== "filled" || row.value == null) {
        skipped.push(itemId);
        continue;
      }

      const provenance = sanitizeProvenance(row.provenance);
      let did = false;
      let handledSkip = false;   // deliberate no-op (e.g. FAQ already there) — not 示範，請改 / unmapped

      if (itemId === "display_name" && typeof row.value === "string" && normalizeDisplayName(row.value)) {
        const name = normalizeDisplayName(row.value);
        draft.profile.display_name = name;
        profilePatch.display_name = name;
        did = true;
      } else if (itemId === "bio") {
        // row.value is a plain string (S4 adopt/extract contract); legacy {body}/{text,format} still accepted.
        const normalized = normalizeBioInput(row.value);
        const bio = normalized ? normalized.text : "";
        // Block text keeps line breaks: row.value_text (pipeline multi-line) when it normalizes to the same bio,
        // else the multi-line form of row.value itself. profile.bio / status_text stay the single-line `bio`.
        const fromValueText = typeof row.value_text === "string" ? normalizeBioInput(row.value_text) : null;
        const blockText = fromValueText && fromValueText.text === bio ? fromValueText.block_text : (normalized ? normalized.block_text : "");
        // Re-run idempotency (v3.109 R3): a legacy {body} block is a broken earlier prefill write ⇒ always repaired in
        // place (never a fresh demo beside it). Otherwise a bio already present (untouched prefill text block / real
        // profile.bio) ⇒ skip the whole item: no second bio block on a re-seeded demo, no overwrite of the person's bio.
        const legacyTarget = draft.blocks.find((b) => b && b.block_type === "text" && legacyNormalized.includes(b.block_id) && b.block_id) || null;
        const present = bio && !legacyTarget ? bioPresence(draft, legacyNormalized) : null;
        if (present) {
          skipped.push(itemId + ":" + present);
          handledSkip = true;
        } else if (bio) {
          if (!profileFieldPresent(draft.profile, "bio")) {
            draft.profile.bio = bio;
            profilePatch.bio = bio;
          }
          if (bio.length <= STATUS_TEXT_MAX && !/[<>]/.test(bio) && !profileFieldPresent(draft.profile, "status_text") && profilePatch.bio === bio) {
            draft.profile.status_text = bio;
            profilePatch.status_text = bio;
          }
          // Repair the legacy {body} block, else replace the demo text block — contract payload {text, format}, never body.
          const textTarget = legacyTarget || findDemoBlock(draft.blocks, "text") || null;
          if (textTarget) {
            const payload = bioTextPayload(blockText || bio, textTarget.payload, row.value_format || (normalized && normalized.format));
            const op = { op: "update", block_id: textTarget.block_id, payload, provenance: provenance || null };
            if (pushOp(itemId, op)) {
              textTarget.payload = payload;
              if (provenance) textTarget.provenance = provenance;
            }
          }
          did = true;
        }
      } else if (itemId === "socials") {
        const links = socialsFromValue(row.value);
        if (links && profileFieldPresent(draft.profile, "socials")) {
          // Re-run idempotency (v3.109 R3): the draft already has real socials (earlier prefill / the person) — keep them.
          skipped.push(itemId + ":socials_present");
          handledSkip = true;
        } else if (links) {
          draft.profile.socials = links;
          profilePatch.socials = links;
          did = true;
        }
      } else if (itemId === "faq") {
        const payload = faqPayloadFromValue(row.value);
        if (payload) {
          // Idempotent (v3.109 R3): any non-demo FAQ (earlier prefill / user) ⇒ skip; only a demo FAQ ⇒ replace
          // it in place; create only when the draft has no FAQ at all.
          const decision = planFaqWrite(draft.blocks, payload);
          faqDecision = decision;
          const target = decision.action === "update" && decision.block_id
            ? draft.blocks.find((b) => b && b.block_type === "faq" && b.block_id === decision.block_id)
            : null;
          if (decision.action === "skip") {
            skipped.push(itemId + ":" + decision.reason);
            handledSkip = true;
          } else if (target) {
            if (pushOp(itemId, { op: "update", block_id: target.block_id, payload, provenance: provenance || undefined })) {
              target.payload = payload;
              if (provenance) target.provenance = provenance;
              did = true;
            }
          } else if (decision.action === "create" && pushOp(itemId, {
            op: "create",
            block_type: "faq",
            visibility: "public",
            payload,
            provenance: provenance || undefined
          })) {
            draft.blocks.push({
              block_id: "prefill_faq_pending",
              block_type: "faq",
              visibility: "public",
              payload,
              provenance: provenance || undefined
            });
            did = true;
          }
        }
      } else if (itemId === "preferred_theme") {
        // Theme retint only — do not clear prefill data; appearance hint only when string theme id.
        const theme = typeof row.value === "string" ? row.value.trim() : (plain(row.value) ? row.value.theme_id : null);
        if (theme && typeof theme === "string") {
          draft.appearance = Object.assign({}, draft.appearance, { theme_id: theme });
          // Not pushed as profile_patch — appearance is separate; mark applied hint only.
          did = true;
        }
      }

      if (did) {
        applied.push(itemId);
        if (provenance) {
          draft.meta.prefill_provenance = draft.meta.prefill_provenance || {};
          draft.meta.prefill_provenance[itemId] = provenance;
        }
      } else if (!handledSkip) {
        // Fail soft: leave 示範，請改 rather than blocking editor.
        labels[itemId] = DEMO_LABEL_ZH;
        skipped.push(itemId + ":unmapped");
      }
    } catch (error) {
      labels[itemId] = DEMO_LABEL_ZH;
      skipped.push(itemId + ":apply_error");
      pending[itemId] = {
        reason: "apply_error",
        label_zh: DEMO_LABEL_ZH,
        error: String(error && error.message || error)
      };
    }
  }

  draft.meta.prefill_candidates = Object.assign({}, draft.meta.prefill_candidates || {}, candidates);
  draft.meta.prefill_pending = Object.assign({}, draft.meta.prefill_pending || {}, pending);
  draft.meta.prefill_labels = Object.assign({}, draft.meta.prefill_labels || {}, labels);
  draft.meta.prefill_applied_at = nowIso(options.clock);
  draft.meta.search_adoption = "candidates_only"; // NO_AUTO still human-adopt
  for (const row of opErrors) {
    if (row.item_id && !applied.includes(row.item_id)) labels[row.item_id] = DEMO_LABEL_ZH;
    skipped.push(row.item_id + ":op_invalid");
  }

  return {
    ok: true,
    draft,
    profile_patch: profilePatch,
    ops,
    candidates,
    pending,
    applied,
    skipped,
    labels,
    op_errors: opErrors,
    legacy_normalized: legacyNormalized,
    faq_decision: faqDecision,
    fail_soft: true,
    blocks_editor: true
  };
}

/**
 * Commit an apply plan through an injected writer.
 * writer: { setProfile?(patch), applyOps?(ops), setMeta?(meta) } — each optional; failures swallowed.
 * Network must already have been gated by runOpeningPrefill; this only writes draft.
 */
async function commitPrefillApplyPlan(writer, plan, options = {}) {
  const out = { ok: true, profile_ok: false, ops_ok: false, meta_ok: false, errors: [] };
  if (!plain(plan)) return Object.assign(out, { ok: false, errors: ["prefill_plan_missing"] });
  const w = writer || {};
  try {
    if (plain(plan.profile_patch) && Object.keys(plan.profile_patch).length && typeof w.setProfile === "function") {
      await w.setProfile(plan.profile_patch, options);
      out.profile_ok = true;
    }
  } catch (error) {
    out.errors.push("setProfile:" + String(error && error.message || error));
  }
  // Ops: one batch first; if the writer rejects it, retry op-by-op so one bad op cannot silently take the
  // rest down (v3.108 half-write). Every failure is surfaced on out.errors / out.ops_failed.
  // v3.109 R3 unified with registration applyOpeningPrefillToDraft: ops_applied = indices (into plan.ops) that
  // landed, ops_applied_count = their number, ops_planned = plan.ops.length.
  const plannedOps = Array.isArray(plan.ops) ? plan.ops : [];
  out.ops_planned = plannedOps.length;
  out.ops_applied = [];
  out.ops_applied_count = 0;
  out.ops_failed = [];
  if (plannedOps.length && typeof w.applyOps === "function") {
    try {
      await w.applyOps(plannedOps, options);
      out.ops_ok = true;
      for (let index = 0; index < plannedOps.length; index += 1) out.ops_applied.push(index);
    } catch (error) {
      out.errors.push("applyOps:" + String(error && error.message || error));
      if (plannedOps.length > 1) {
        for (let index = 0; index < plannedOps.length; index += 1) {
          const op = plannedOps[index];
          try {
            await w.applyOps([op], options);
            out.ops_applied.push(index);
          } catch (opError) {
            const message = String(opError && opError.message || opError);
            out.ops_failed.push({ index, op: op && op.op, block_id: (op && op.block_id) || null, error: message });
            out.errors.push("applyOps[" + index + "]:" + message);
          }
        }
        out.ops_ok = out.ops_applied.length === plannedOps.length;
        out.ops_partial = out.ops_applied.length > 0 && !out.ops_ok;
      } else {
        const op = plannedOps[0];
        out.ops_failed.push({ index: 0, op: op && op.op, block_id: (op && op.block_id) || null, error: String(error && error.message || error) });
      }
    }
  }
  out.ops_applied_count = out.ops_applied.length;
  if (Array.isArray(plan.op_errors) && plan.op_errors.length) {
    for (const row of plan.op_errors) out.errors.push("op_invalid:" + (row.item_id || "?") + ":" + (row.errors || []).join(","));
  }
  try {
    if (plain(plan.draft) && plain(plan.draft.meta) && typeof w.setMeta === "function") {
      await w.setMeta({
        prefill_candidates: plan.candidates,
        prefill_pending: plan.pending,
        prefill_labels: plan.labels,
        prefill_applied_at: plan.draft.meta.prefill_applied_at,
        search_adoption: "candidates_only"
      }, options);
      out.meta_ok = true;
    }
  } catch (error) {
    out.errors.push("setMeta:" + String(error && error.message || error));
  }
  // Fail soft: draft apply errors never block editor — but they are reported, not swallowed.
  out.ok = true;
  out.fail_soft = out.errors.length > 0;
  out.blocks_editor = true;
  return out;
}


module.exports = {
  VERSION,
  PIPELINE_STAGES,
  DEFAULT_ITEM_TIMEOUT_MS,
  DEFAULT_TOTAL_BUDGET_MS,
  DEFAULT_TTL_DAYS,
  PENDING_LABEL_ZH,
  DEMO_LABEL_ZH,
  ITEM_FIELD_KEYS,
  // Re-export checklist surface the orchestrator binds to (same names — no invented ids).
  STATUSES,
  FIND_FOR_ME_POLICY,
  NO_AUTO_WRITE_IDS,
  ITEM_DEFS,
  ITEM_IDS,
  ITEM_DEF_BY_ID,
  findForMeMayAutoWrite,
  resolveItemId,
  isNetworkAuthorized,
  buildProvenance,
  withTimeout,
  createBudgetTracker,
  readPageValue,
  readSnapshotValue,
  resolveItemPrefill,
  runNetworkPipeline,
  runOpeningPrefill,
  startOpeningPrefill,
  applyPrefillResultToDraft,
  commitPrefillApplyPlan,
  SOFT_PROFILE_FIELDS,
  // v3.109 contract-shape helpers (bio {text, format}; legacy {body} compat).
  TEXT_FORMATS,
  BIO_MAX,
  BIO_BLOCK_MAX,
  normalizeBioInput,
  bioTextPayload,
  isLegacyBodyTextBlock,
  normalizeLegacyTextPayload,
  normalizeDisplayName,
  sanitizeProvenance,
  socialsFromValue,
  faqPayloadFromValue,
  candidateFromValue,
  prefillOpErrors,
  // v3.109 R3: grapheme-safe cap + re-run idempotency (FAQ / bio / socials never stacked or overwritten).
  DISPLAY_NAME_MAX,
  STATUS_TEXT_MAX,
  truncateText,
  prefillBlockState,
  planFaqWrite,
  bioPresence,
  isPrefillBioBlock,
  profileFieldPresent
};
