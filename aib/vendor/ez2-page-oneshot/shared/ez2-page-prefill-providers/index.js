"use strict";

// S3 v3.108 real crawl/search provider adapters.
// Default OFF — enable only via EZ2_PAGE_PREFILL_REAL=1 / options.realProviders / stage flags.
// Hangs on existing registerAdapter / createAdapters registry (shared/ez2-page-prefill-adapters.js).
// Auth gate + fail-soft + provenance + per-provider budget skeleton. No secrets in tree.

const adaptersMod = require("../ez2-page-prefill-adapters.js");
const { createProviderBudget, DEFAULT_PER_PROVIDER_MS, DEFAULT_PER_PROVIDER_MAX_CALLS } = require("./budget.js");
const common = require("./common.js");
const { createWebsiteAdapter } = require("./website.js");
const { createSearchAdapter } = require("./search.js");
const { createLinkInBioAdapter } = require("./link-in-bio.js");
const { createSocialAdapter } = require("./social.js");
const { createFivePlatformsAdapter } = require("./five-platforms.js");
const { createGoogleBusinessAdapter } = require("./google-business.js");

const VERSION = "2026-09-27.ez2-page-prefill-providers.v1";

const ENV_FLAG = "EZ2_PAGE_PREFILL_REAL";
const ENV_STAGES = "EZ2_PAGE_PREFILL_STAGES";

/** Documented env key names (values never committed). */
const ENV_KEY_NAMES = Object.freeze([
  ENV_FLAG,
  ENV_STAGES,
  "EZ2_PAGE_SEARCH_API_URL",
  "EZ2_PAGE_SEARCH_API_KEY",
  "EZ2_PAGE_GMB_API_URL",
  "EZ2_PAGE_GMB_API_KEY",
  "EZ2_PAGE_SOCIAL_API_URL",
  "EZ2_PAGE_SOCIAL_API_KEY",
  "EZ2_PAGE_LINK_IN_BIO_API_URL",
  "EZ2_PAGE_LINK_IN_BIO_API_KEY"
]);

const FACTORIES = Object.freeze({
  link_in_bio: createLinkInBioAdapter,
  social: createSocialAdapter,
  website: createWebsiteAdapter,
  five_platforms: createFivePlatformsAdapter,
  google_business: createGoogleBusinessAdapter,
  search: createSearchAdapter
});

function truthyFlag(raw) {
  if (raw === true) return true;
  if (raw === false || raw == null) return false;
  const s = String(raw).trim().toLowerCase();
  return s === "1" || s === "true" || s === "yes" || s === "on";
}

/**
 * Master enable gate. Default false — stubs remain unless explicitly opted in.
 */
function isRealProvidersEnabled(options = {}, env) {
  if (options.realProviders === true || options.real_providers === true) return true;
  if (options.realProviders === false || options.real_providers === false) return false;
  const e = env || common.envOf(options);
  return truthyFlag(e && e[ENV_FLAG]);
}

/**
 * Optional stage subset: options.realStages / options.stages_real / EZ2_PAGE_PREFILL_STAGES.
 * Unknown names ignored. Empty → all PIPELINE_STAGES.
 */
function resolveEnabledStages(options = {}, env) {
  const e = env || common.envOf(options);
  let raw = options.realStages || options.real_stages || options.stages_real || null;
  if (raw == null && e && e[ENV_STAGES]) raw = e[ENV_STAGES];
  const allowed = adaptersMod.PIPELINE_STAGES;
  if (raw == null || raw === "" || raw === true) return allowed.slice();
  const list = Array.isArray(raw)
    ? raw
    : String(raw).split(/[,|\s]+/).map((s) => s.trim()).filter(Boolean);
  const out = [];
  for (const name of list) {
    if (allowed.includes(name) && !out.includes(name)) out.push(name);
  }
  return out.length ? out : allowed.slice();
}

function wrapFailSoft(stage, fn) {
  return async function failSoftAdapter(ctx) {
    try {
      return await fn(ctx);
    } catch (error) {
      // Fail-soft: surface coded empty-ish result so pipeline continues (orchestrator also catches).
      // Re-throw so runNetworkPipeline records network_errors and continues — matching stub fail_stages.
      if (error && error.code) throw error;
      const err = new Error(String(error && error.message || error || "PROVIDER_ERROR"));
      err.code = "PROVIDER_ERROR";
      err.stage = stage;
      throw err;
    }
  };
}

/**
 * Build real adapter map for enabled stages. Returns null when flag off.
 * deps: { fetch, clock, env, authorized, budget|provider_budget, total_budget_ms,
 *         per_provider_budget_ms, per_provider_max_calls, realStages }
 */
function createRealAdapters(deps = {}) {
  if (!isRealProvidersEnabled(deps, deps.env)) return null;
  const stages = resolveEnabledStages(deps, deps.env);
  const budget = deps.budget || deps.provider_budget || createProviderBudget({
    clock: deps.clock,
    total_budget_ms: deps.provider_total_budget_ms != null ? deps.provider_total_budget_ms : deps.total_budget_ms,
    per_provider_budget_ms: deps.per_provider_budget_ms,
    per_provider_max_calls: deps.per_provider_max_calls
  });
  const shared = Object.assign({}, deps, { budget });
  const adapters = Object.create(null);
  for (const stage of stages) {
    const factory = FACTORIES[stage];
    if (typeof factory !== "function") continue;
    adapters[stage] = wrapFailSoft(stage, factory(shared));
  }
  adapters.mode = "real";
  adapters.budget = budget;
  adapters.enabled_stages = stages.slice();
  adapters.PIPELINE_STAGES = adaptersMod.PIPELINE_STAGES;
  adapters.VERSION = VERSION;
  return adapters;
}

/**
 * Register real adapters onto the process-local registry (boot path).
 * No-op when flag off. Returns list of registered stage names.
 */
function registerRealProviders(deps = {}) {
  const real = createRealAdapters(deps);
  if (!real) return [];
  const registered = [];
  for (const stage of real.enabled_stages) {
    if (typeof real[stage] === "function") {
      adaptersMod.registerAdapter(stage, real[stage]);
      registered.push(stage);
    }
  }
  return registered;
}

module.exports = {
  VERSION,
  ENV_FLAG,
  ENV_STAGES,
  ENV_KEY_NAMES,
  FACTORIES,
  DEFAULT_PER_PROVIDER_MS,
  DEFAULT_PER_PROVIDER_MAX_CALLS,
  isRealProvidersEnabled,
  resolveEnabledStages,
  createProviderBudget,
  createRealAdapters,
  registerRealProviders,
  createWebsiteAdapter,
  createSearchAdapter,
  createLinkInBioAdapter,
  createSocialAdapter,
  createFivePlatformsAdapter,
  createGoogleBusinessAdapter
};
