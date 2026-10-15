"use strict";

// Per-provider + total budget skeleton for S3 real crawl/search providers.
// Orchestrator still owns item timeout → 待補; this layer tracks provider spend / call caps.
// Timeouts / exhausted → fail-soft (throw PROVIDER_BUDGET_EXHAUSTED); never block editor.

const DEFAULT_PER_PROVIDER_MS = Object.freeze({
  link_in_bio: 2500,
  social: 2500,
  website: 2500,
  five_platforms: 3000,
  google_business: 2500,
  search: 2500
});

const DEFAULT_PER_PROVIDER_MAX_CALLS = Object.freeze({
  link_in_bio: 2,
  social: 2,
  website: 2,
  five_platforms: 1,
  google_business: 2,
  search: 3
});

function plain(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nowMs(clock) {
  if (typeof clock === "function") return Number(clock());
  if (typeof clock === "number") return clock;
  return Date.now();
}

/**
 * Create a mutable budget tracker.
 * options.total_budget_ms — wall budget across all providers (0 = unlimited total)
 * options.per_provider_budget_ms — map stage → ms cap
 * options.per_provider_max_calls — map stage → max invocations
 */
function createProviderBudget(options = {}) {
  const clock = options.clock;
  const started = nowMs(clock);
  const totalMs = options.total_budget_ms == null ? 0 : Math.max(0, Number(options.total_budget_ms) || 0);
  const perMs = Object.assign({}, DEFAULT_PER_PROVIDER_MS, plain(options.per_provider_budget_ms) ? options.per_provider_budget_ms : {});
  const maxCalls = Object.assign({}, DEFAULT_PER_PROVIDER_MAX_CALLS, plain(options.per_provider_max_calls) ? options.per_provider_max_calls : {});
  const spentMs = Object.create(null);
  const calls = Object.create(null);

  function stageSpent(stage) {
    return Number(spentMs[stage]) || 0;
  }

  function stageCalls(stage) {
    return Number(calls[stage]) || 0;
  }

  function totalSpent() {
    let sum = 0;
    for (const key of Object.keys(spentMs)) sum += Number(spentMs[key]) || 0;
    // Also account for wall time when total_budget_ms is set.
    if (totalMs > 0) {
      const wall = nowMs(clock) - started;
      return Math.max(sum, wall);
    }
    return sum;
  }

  function remainingMs(stage) {
    const cap = Number(perMs[stage]);
    const stageLeft = Number.isFinite(cap) && cap > 0 ? Math.max(0, cap - stageSpent(stage)) : Infinity;
    if (totalMs > 0) {
      return Math.max(0, Math.min(stageLeft, totalMs - totalSpent()));
    }
    return stageLeft === Infinity ? Number.MAX_SAFE_INTEGER : stageLeft;
  }

  function exhausted(stage) {
    if (stageCalls(stage) >= (Number(maxCalls[stage]) || Infinity)) return true;
    return remainingMs(stage) <= 0;
  }

  function allow(stage) {
    return !exhausted(stage);
  }

  function charge(stage, ms) {
    const key = String(stage || "");
    calls[key] = stageCalls(key) + 1;
    spentMs[key] = stageSpent(key) + Math.max(0, Number(ms) || 0);
    return snapshot();
  }

  function budgetError(stage) {
    const err = new Error("PROVIDER_BUDGET_EXHAUSTED:" + stage);
    err.code = "PROVIDER_BUDGET_EXHAUSTED";
    err.stage = stage;
    return err;
  }

  function snapshot() {
    const stages = Object.create(null);
    const keys = new Set([].concat(Object.keys(perMs), Object.keys(spentMs), Object.keys(calls)));
    for (const key of keys) {
      stages[key] = {
        spent_ms: stageSpent(key),
        calls: stageCalls(key),
        max_calls: Number(maxCalls[key]) || null,
        remaining_ms: remainingMs(key) === Number.MAX_SAFE_INTEGER ? null : remainingMs(key),
        exhausted: exhausted(key)
      };
    }
    return {
      started_at_ms: started,
      total_ms: totalMs || null,
      total_spent_ms: totalSpent(),
      total_remaining_ms: totalMs > 0 ? Math.max(0, totalMs - totalSpent()) : null,
      stages
    };
  }

  return {
    started_at_ms: started,
    total_ms: totalMs,
    remainingMs,
    exhausted,
    allow,
    charge,
    budgetError,
    snapshot,
    DEFAULT_PER_PROVIDER_MS,
    DEFAULT_PER_PROVIDER_MAX_CALLS
  };
}

module.exports = {
  DEFAULT_PER_PROVIDER_MS,
  DEFAULT_PER_PROVIDER_MAX_CALLS,
  createProviderBudget
};
