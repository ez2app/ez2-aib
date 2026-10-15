"use strict";

// S3 crawl/search adapters for opening parallel prefill.
// Default (v3.109 刀04): OFF — createAdapters returns mode "off" (zero adapter calls, zero fields) so production
// never writes / publishes stub fixture content. Stub fixtures are TEST-ONLY: explicit flag
// EZ2_PAGE_PREFILL_STUB=1 (env or deps.env) / deps.stub === true, or call createStubAdapters directly.
// Real / harness adapters: registerAdapter(stage, fn) or createAdapters({ adapters }) / deps.adapters,
// or flag EZ2_PAGE_PREFILL_REAL=1 / options.realProviders → ez2-page-prefill-providers.
 // Pipeline order is pinned — callers must not reorder silently.

const VERSION = "2026-09-29.ez2-page-prefill-adapters.v7";

const PIPELINE_STAGES = Object.freeze([
  "link_in_bio",
  "social",
  "website",
  "five_platforms",
  "google_business",
  "search"
]);

const DEFAULT_FIXTURES = Object.freeze({
  link_in_bio: Object.freeze({
    display_name: "Stub Bio Name",
    bio: "來自 link-in-bio 的一句自介",
    primary_link: "https://linktr.ee/stub-demo",
    socials: Object.freeze([{ platform: "ig", url: "https://instagram.com/stub-demo" }])
  }),
  social: Object.freeze({
    display_name: "Stub Social Name",
    bio: "來自社群的狀態句",
    avatar_url: "https://cdn.example.test/stub-avatar.jpg",
    socials: Object.freeze([{ platform: "ig", url: "https://instagram.com/stub-social" }])
  }),
  website: Object.freeze({
    display_name: "Stub Site",
    bio: "來自官網的簡介",
    primary_link: "https://www.example.test/",
    faq: Object.freeze([
      Object.freeze({ q: "營業時間？", a: "週二至週日 10:00–18:00" }),
      Object.freeze({ q: "怎麼預約？", a: "官網表單或 IG 私訊" })
    ])
  }),
  five_platforms: Object.freeze({
    socials: Object.freeze([
      Object.freeze({ platform: "ig", url: "https://instagram.com/stub-five" }),
      Object.freeze({ platform: "youtube", url: "https://youtube.com/@stub-five" }),
      Object.freeze({ platform: "fb", url: "https://facebook.com/stub-five" }),
      Object.freeze({ platform: "threads", url: "https://threads.net/@stub-five" }),
      Object.freeze({ platform: "line", url: "https://lin.ee/stubfive" })
    ])
  }),
  google_business: Object.freeze({
    display_name: "Stub 商家",
    bio: "Google 商家簡介 stub",
    primary_link: "https://maps.example.test/place/stub",
    place: Object.freeze({ name: "Stub 商家", address: "台北市示範路 1 號" })
  }),
  search: Object.freeze({
    display_name: "Search Hit Name",
    bio: "搜尋回退找到的簡介",
    avatar_url: "https://cdn.example.test/search-avatar.jpg",
    primary_link: "https://search.example.test/hit",
    socials: Object.freeze([{ platform: "ig", url: "https://instagram.com/search-hit" }])
  })
});

const SOURCE_BY_STAGE = Object.freeze({
  link_in_bio: "link_in_bio",
  social: "social",
  website: "website",
  five_platforms: "five_platforms",
  google_business: "google_business",
  search: "search"
});

const URL_BY_STAGE = Object.freeze({
  link_in_bio: "https://linktr.ee/stub-demo",
  social: "https://instagram.com/stub-social",
  website: "https://www.example.test/",
  five_platforms: "https://instagram.com/stub-five",
  google_business: "https://maps.example.test/place/stub",
  search: "https://search.example.test/hit"
});

/** Process-local registry for real crawl/search adapters. Overlaid onto stubs by createAdapters. */
const ADAPTER_REGISTRY = Object.create(null);

function plain(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isoNow(clock) {
  if (typeof clock === "function") return new Date(clock()).toISOString();
  if (typeof clock === "number") return new Date(clock).toISOString();
  return new Date().toISOString();
}

function buildStageProvenance(stage, options = {}) {
  const fetchedAt = options.fetched_at || isoNow(options.clock);
  const ttl = options.ttl_days == null ? 30 : options.ttl_days;
  const provenance = {
    source: SOURCE_BY_STAGE[stage] || String(stage).slice(0, 32),
    url: options.url != null ? options.url : (URL_BY_STAGE[stage] || null),
    fetched_at: fetchedAt,
    ttl_days: ttl
  };
  if (stage === "search") provenance.via = "search";
  return provenance;
}

/**
 * Register a real (or test) adapter for one pipeline stage.
 * fn(ctx) → Promise<{ stage, fields, provenance, verified }> | sync same shape.
 * Pass null to clear. Returns previous fn or null.
 *
 * Injection path for production crawlers:
 *   const { registerAdapter, createAdapters } = require("./ez2-page-prefill-adapters");
 *   registerAdapter("website", async (ctx) => realWebsiteCrawl(ctx));
 *   const adapters = createAdapters({ fetch, clock }); // registry overlays stubs
 */
function registerAdapter(stage, fn) {
  const key = String(stage || "");
  if (!PIPELINE_STAGES.includes(key)) {
    const err = new Error("ADAPTER_STAGE_UNKNOWN:" + key);
    err.code = "ADAPTER_STAGE_UNKNOWN";
    throw err;
  }
  const prev = ADAPTER_REGISTRY[key] || null;
  if (fn == null) delete ADAPTER_REGISTRY[key];
  else if (typeof fn !== "function") {
    const err = new Error("ADAPTER_NOT_FUNCTION:" + key);
    err.code = "ADAPTER_NOT_FUNCTION";
    throw err;
  } else {
    ADAPTER_REGISTRY[key] = fn;
  }
  return prev;
}

function getRegisteredAdapter(stage) {
  return ADAPTER_REGISTRY[String(stage)] || null;
}

function listRegisteredAdapters() {
  const out = Object.create(null);
  for (const stage of PIPELINE_STAGES) {
    if (typeof ADAPTER_REGISTRY[stage] === "function") out[stage] = ADAPTER_REGISTRY[stage];
  }
  return out;
}

function clearAdapterRegistry() {
  for (const key of Object.keys(ADAPTER_REGISTRY)) delete ADAPTER_REGISTRY[key];
}

/**
 * Create stub adapters. Each stage returns { fields, provenance, stage, verified }.
 * `search` is always unverified (verified:false) so callers treat avatar/primary as candidates-only.
 * Pass `fetch` that increments a spy counter when you need to prove network gate behaviour;
 * default adapters never call fetch — they only read fixtures.
 */
function createStubAdapters(deps = {}) {
  const fixtures = Object.assign({}, DEFAULT_FIXTURES, plain(deps.fixtures) ? deps.fixtures : {});
  const clock = deps.clock;
  const fetchImpl = typeof deps.fetch === "function" ? deps.fetch : null;
  const delays = plain(deps.delays_ms) ? deps.delays_ms : {};
  const failStages = new Set(Array.isArray(deps.fail_stages) ? deps.fail_stages : []);
  const callLog = [];

  async function runStage(stage, ctx) {
    callLog.push({ stage, at: isoNow(clock), ctx_keys: ctx && typeof ctx === "object" ? Object.keys(ctx) : [] });
    if (fetchImpl) {
      await fetchImpl("stub://" + stage, { method: "GET", stage, ctx });
    }
    const wait = Number(delays[stage]) || 0;
    if (wait > 0) {
      await new Promise((resolve) => setTimeout(resolve, wait));
    }
    if (failStages.has(stage)) {
      const err = new Error("STUB_ADAPTER_FAIL:" + stage);
      err.code = "STUB_ADAPTER_FAIL";
      err.stage = stage;
      throw err;
    }
    const fields = fixtures[stage] ? JSON.parse(JSON.stringify(fixtures[stage])) : {};
    return {
      stage,
      fields,
      provenance: buildStageProvenance(stage, { clock, url: URL_BY_STAGE[stage] }),
      verified: stage !== "search"
    };
  }

  const adapters = {};
  for (const stage of PIPELINE_STAGES) {
    adapters[stage] = (ctx) => runStage(stage, ctx || {});
  }
  adapters.callLog = callLog;
  adapters.PIPELINE_STAGES = PIPELINE_STAGES;
  adapters.fixtures = fixtures;
  adapters.mode = "stub";
  return adapters;
}

function createEmptyStageAdapter(stage, deps = {}) {
  // Used when realProviders is on but this stage is not in realStages / EZ2_PAGE_PREFILL_STAGES.
  // Must NOT fall back to stub fixtures — those invent demo fields that win pickFieldsFromPipeline.
  return async function emptyStageAdapter() {
    return {
      stage,
      fields: {},
      provenance: buildStageProvenance(stage, { clock: deps && deps.clock, url: null }),
      verified: stage !== "search",
      provider: "empty",
      empty: true
    };
  };
}

function truthyFlag(raw) {
  if (raw === true) return true;
  if (raw == null || raw === false) return false;
  const s = String(raw).trim().toLowerCase();
  return s === "1" || s === "true" || s === "yes" || s === "on";
}

function envOf(deps) {
  return plain(deps && deps.env) ? deps.env : (typeof process !== "undefined" ? process.env : {});
}

const STUB_ENV_FLAG = "EZ2_PAGE_PREFILL_STUB";

/**
 * v3.109 刀04: stub fixtures are test-only. true only when explicitly asked:
 *   deps.stub === true / deps.stub_fixtures === true, or env EZ2_PAGE_PREFILL_STUB truthy
 *   (deps.env when given, else process.env). deps.stub === false forces off.
 * Production default (nothing set) ⇒ false ⇒ createAdapters never falls back to DEFAULT_FIXTURES.
 */
function isStubFixturesEnabled(deps = {}) {
  const d = plain(deps) ? deps : {};
  if (d.stub === false || d.stub_fixtures === false) return false;
  if (d.stub === true || d.stub_fixtures === true) return true;
  const env = envOf(d);
  return truthyFlag(env && env[STUB_ENV_FLAG]);
}

/**
 * Prefill disabled (no real provider, no registry, no inject, no stub flag): every stage is an empty
 * adapter and pipeline_stages is [] ⇒ runOpeningPrefill makes zero adapter calls ⇒ zero writes.
 */
function createDisabledAdapters(deps = {}) {
  const adapters = {};
  for (const stage of PIPELINE_STAGES) adapters[stage] = createEmptyStageAdapter(stage, deps);
  adapters.callLog = [];
  adapters.PIPELINE_STAGES = PIPELINE_STAGES;
  adapters.fixtures = {};
  adapters.mode = "off";
  adapters.disabled = true;
  adapters.pipeline_stages = [];
  return adapters;
}

function tryCreateRealAdapters(deps) {
  // Lazy require — keep the off / stub path free of provider module cost when flag off.
  let want = false;
  if (deps.realProviders === true || deps.real_providers === true) want = true;
  else if (deps.realProviders === false || deps.real_providers === false) want = false;
  else {
    const env = envOf(deps);
    want = truthyFlag(env && env.EZ2_PAGE_PREFILL_REAL);
  }
  if (!want) return null;
  try {
    const providers = require("./ez2-page-prefill-providers");
    return providers.createRealAdapters(deps);
  } catch (_) {
    return null; // fail-soft: registry / inject, else stub only with the stub flag, else off (v3.109 刀04)
  }
}

/**
 * Resolve usable adapters:
 *   explicit deps.adapters (injected) >
 *   process registry (registerAdapter) >
 *   real providers when EZ2_PAGE_PREFILL_REAL / options.realProviders >
 *   stub fixtures ONLY when isStubFixturesEnabled (EZ2_PAGE_PREFILL_STUB=1 / deps.stub) >
 *   OFF (production default — mode "off", pipeline_stages [], zero calls, zero writes).
 * Real crawl providers: registerAdapter(...) once at boot, or createAdapters({ realProviders:true, fetch }).
 * Pure inject { adapters: { website: fn } } keeps stub gaps only with the stub flag; without it the gaps
 * are empty adapters and pipeline_stages = injected stages. Once real/registry is also active, uncovered
 * stages are empty adapters — never stub fixtures (same class as 8a/8b).
 * pipeline_stages = union(injected, registered, real) in PIPELINE_STAGES order.
 */
function createAdapters(deps = {}) {
  const stubAllowed = isStubFixturesEnabled(deps);
  const stubs = createStubAdapters(deps);
  const injectMap = plain(deps.adapters) ? deps.adapters : null;
  const hasInject = Boolean(injectMap) && Object.keys(injectMap).length > 0
    && !deps.use_registry
    && PIPELINE_STAGES.some((stage) => typeof injectMap[stage] === "function");
  const real = tryCreateRealAdapters(deps);
  const registered = listRegisteredAdapters();
  const hasReal = Boolean(real && Array.isArray(real.enabled_stages) && real.enabled_stages.length);
  const hasReg = Object.keys(registered).length > 0;

  // Pure inject (no real / registry) + stub flag: missing stages keep stub so pipeline stays complete.
  // Without the stub flag this falls through to the overlay below (gaps = empty adapters, never fixtures).
  if (hasInject && !hasReal && !hasReg && stubAllowed) {
    const merged = Object.assign({}, stubs);
    const injected = [];
    for (const stage of PIPELINE_STAGES) {
      if (typeof injectMap[stage] === "function") {
        merged[stage] = injectMap[stage];
        injected.push(stage);
      }
    }
    merged.callLog = stubs.callLog;
    merged.PIPELINE_STAGES = PIPELINE_STAGES;
    merged.fixtures = stubs.fixtures;
    merged.mode = "injected";
    merged.injected_stages = injected;
    return merged;
  }

  if (!hasReal && !hasReg && !hasInject) return stubAllowed ? stubs : createDisabledAdapters(deps);

  const merged = Object.assign({}, stubs);
  // Inject > registry > real > empty. Uncovered stages must be empty (never stub fixtures).
  const realEnabled = hasReal ? new Set(real.enabled_stages) : null;
  const injected = [];
  const active = [];
  for (const stage of PIPELINE_STAGES) {
    const inInject = hasInject && typeof injectMap[stage] === "function";
    const inReg = hasReg && typeof registered[stage] === "function";
    const inReal = Boolean(realEnabled && realEnabled.has(stage) && typeof real[stage] === "function");
    if (inInject) {
      merged[stage] = injectMap[stage];
      injected.push(stage);
      active.push(stage);
    } else if (inReg) {
      merged[stage] = registered[stage];
      active.push(stage);
    } else if (inReal) {
      merged[stage] = real[stage];
      active.push(stage);
    } else {
      merged[stage] = createEmptyStageAdapter(stage, deps);
    }
  }
  merged.callLog = stubs.callLog;
  merged.PIPELINE_STAGES = PIPELINE_STAGES;
  merged.fixtures = stubs.fixtures;
  if (hasInject && !hasReg && !hasReal) {
    merged.mode = "injected";   // pure inject without stub flag: gaps are empty adapters
  } else if (hasInject && (hasReg || hasReal)) {
    merged.mode = hasReg && hasReal ? "injected+registry+real" : hasReg ? "injected+registry" : "injected+real";
  } else {
    merged.mode = hasReg && hasReal ? "registry+real" : hasReg ? "registry" : "real";
  }
  if (injected.length) merged.injected_stages = injected;
  if (hasReg) merged.registered_stages = Object.keys(registered);
  if (hasReal) {
    merged.real_stages = real.enabled_stages.slice();
    merged.provider_budget = real.budget || null;
  }
  // Union of non-empty overlays — orchestrator prefers this over real_stages alone.
  merged.pipeline_stages = active.slice();
  return merged;
}

module.exports = {
  VERSION,
  PIPELINE_STAGES,
  DEFAULT_FIXTURES,
  SOURCE_BY_STAGE,
  URL_BY_STAGE,
  STUB_ENV_FLAG,
  buildStageProvenance,
  isStubFixturesEnabled,
  createDisabledAdapters,
  createStubAdapters,
  createAdapters,
  registerAdapter,
  getRegisteredAdapter,
  listRegisteredAdapters,
  clearAdapterRegistry
};
