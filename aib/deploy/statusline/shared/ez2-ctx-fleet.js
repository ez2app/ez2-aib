#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const core = require("./ez2-ctx-core.js");

const GAUGE_KEYS = Object.freeze([
  "authority", "engine", "gauge_version", "model", "observed_at", "pct",
  "schema", "session_id", "size", "source", "tier", "used"
]);
const OPTIONAL_GAUGE_KEYS = Object.freeze(["error_class"]);
const ENGINES = Object.freeze(["claude", "codex", "agy"]);

function validateGauge(row) {
  if (!row || typeof row !== "object" || Array.isArray(row)) throw new Error("invalid gauge object");
  const keys = Object.keys(row);
  const allowed = new Set([...GAUGE_KEYS, ...OPTIONAL_GAUGE_KEYS]);
  if (keys.some((key) => !allowed.has(key)) || GAUGE_KEYS.some((key) => !keys.includes(key))) throw new Error("invalid gauge shape");
  if (row.schema !== "ez2-context-gauge-v1") throw new Error("invalid gauge schema");
  if (typeof row.gauge_version !== "string" || !row.gauge_version) throw new Error("invalid gauge version");
  if (!ENGINES.includes(row.engine)) throw new Error("invalid gauge engine");
  const stringLimits = { session_id: 256, model: 256, source: 128, observed_at: 64 };
  for (const [key, limit] of Object.entries(stringLimits)) {
    if (typeof row[key] !== "string" || !row[key].trim() || row[key] !== row[key].trim() || row[key].length > limit || /[\u0000-\u001f\u007f]/.test(row[key])) throw new Error("invalid gauge " + key);
  }
  if (!Number.isFinite(Date.parse(row.observed_at))) throw new Error("invalid gauge observed_at");
  if (row.pct !== null && (typeof row.pct !== "number" || !Number.isFinite(row.pct) || row.pct < 0 || row.pct > 100)) throw new Error("invalid gauge percentage");
  for (const key of ["used", "size"]) {
    if (row[key] !== null && (typeof row[key] !== "number" || !Number.isFinite(row[key]) || row[key] < 0)) throw new Error("invalid gauge " + key);
  }
  if (!['provider_observed', 'unknown'].includes(row.authority)) throw new Error("invalid gauge authority");
  if (row.authority === "provider_observed" && row.pct === null) throw new Error("observed gauge requires a percentage");
  if (row.authority === "unknown" && row.pct !== null) throw new Error("unknown gauge cannot carry a percentage");
  if (row.tier !== core.tierOf(row.pct)) throw new Error("gauge tier mismatch");
  if (row.error_class != null && (typeof row.error_class !== "string" || !row.error_class.trim() || row.error_class !== row.error_class.trim() || row.error_class.length > 128 || /[\u0000-\u001f\u007f]/.test(row.error_class))) throw new Error("invalid gauge error_class");
  return Object.fromEntries([...GAUGE_KEYS, ...OPTIONAL_GAUGE_KEYS].filter((key) => row[key] !== undefined).map((key) => [key, row[key]]));
}

function readFleet(options = {}) {
  for (const key of ["dir", "engine", "stale_ms"]) {
    if (Object.prototype.hasOwnProperty.call(options, key) && (options[key] == null || !String(options[key]).trim())) throw new Error(key + " must not be empty");
  }
  if (Object.prototype.hasOwnProperty.call(options, "require_engines")) {
    const supplied = Array.isArray(options.require_engines) ? options.require_engines : String(options.require_engines).split(",");
    if (!supplied.length || supplied.some((value) => !String(value).trim())) throw new Error("require_engines must not be empty");
  }
  const dir = path.resolve(options.dir || process.env.EZ2_CTX_DIR || path.join(os.homedir(), ".ez2", "ctx"));
  const now = options.now || Date.now(), staleMs = Number(options.stale_ms == null ? 15 * 60 * 1000 : options.stale_ms);
  if (!Number.isFinite(staleMs) || staleMs < 0) throw new Error("stale_ms must be a finite non-negative number");
  const engineFilter = !options.engine || options.engine === "all" ? "" : String(options.engine);
  if (engineFilter && !ENGINES.includes(engineFilter)) throw new Error("engine must be claude, codex, agy, or all");
  const requiredEngines = Array.isArray(options.require_engines)
    ? options.require_engines
    : String(options.require_engines || "").split(",").map((value) => value.trim()).filter(Boolean);
  if (requiredEngines.some((engine) => !ENGINES.includes(engine))) throw new Error("require_engines must contain only claude, codex, or agy");
  let files = [];
  try { files = fs.readdirSync(dir).filter((file) => file.endsWith(".pct")); } catch (_) {}
  const rows = [];
  let invalidCount = 0;
  for (const file of files) {
    try {
      const row = validateGauge(JSON.parse(fs.readFileSync(path.join(dir, file), "utf8")));
      const timestamp = Date.parse(row.observed_at);
      const clockSkew = Number.isFinite(timestamp) && timestamp > now + 60 * 1000;
      const ageMs = Number.isFinite(timestamp) && !clockSkew ? Math.max(0, now - timestamp) : null;
      rows.push({
        ...row,
        age_ms: ageMs,
        freshness: clockSkew ? "clock_skew" : ageMs == null ? "unknown" : ageMs > staleMs ? "stale" : "current",
        live_identity: row.session_id.trim().toLowerCase() !== "unknown"
      });
    } catch (_) { invalidCount++; }
  }
  const filtered = engineFilter ? rows.filter((row) => row.engine === engineFilter) : rows;
  filtered.sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1));
  const current = filtered.filter((row) => row.freshness === "current");
  const observed = current.filter((row) => row.authority === "provider_observed" && row.live_identity);
  const currentEngines = new Set(observed.map((row) => row.engine));
  const missingEngines = requiredEngines.filter((engine) => !currentEngines.has(engine));
  const versionMismatch = current.filter((row) => row.gauge_version !== core.CORE_VERSION).length;
  const sameVersion = invalidCount === 0 && observed.length > 0 && versionMismatch === 0 && missingEngines.length === 0;
  return {
    schema: "ez2-context-fleet-v1",
    gauge_version: core.CORE_VERSION,
    status: sameVersion ? "ok" : observed.length === 0 ? "unknown" : "drift",
    same_version: sameVersion,
    live_coverage: sameVersion,
    valid_count: filtered.length,
    current_count: current.length,
    observed_count: observed.length,
    invalid_count: invalidCount,
    version_mismatch_count: versionMismatch,
    required_engines: requiredEngines,
    missing_engines: missingEngines,
    rows: filtered
  };
}

function main() {
  const options = { json: false, require_engines_present: false };
  for (let i = 2; i < process.argv.length; i++) {
    const arg = process.argv[i];
    if (arg === "--json") options.json = true;
    else if (["--engine", "--dir", "--stale-ms", "--require-engines"].includes(arg)) {
      if (i + 1 >= process.argv.length || process.argv[i + 1].startsWith("--")) throw new Error(arg + " requires a value");
      const value = process.argv[++i];
      if (!value.trim()) throw new Error(arg + " requires a non-empty value");
      options[arg.slice(2).replace(/-/g, "_")] = value;
      if (arg === "--require-engines") options.require_engines_present = true;
    }
    else if (arg === "--help") {
      console.log("Usage: ez2-ctx-fleet.js [--engine claude|codex|agy] [--require-engines claude,codex,agy] [--json]");
      return;
    } else throw new Error("unknown argument: " + arg);
  }
  const requireEnginesPresent = options.require_engines_present;
  delete options.require_engines_present;
  const result = readFleet(options);
  if (options.json) process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  else if (!result.rows.length) console.log("No session gauge files; an engine statusline or hook must run first.");
  else for (const row of result.rows) console.log((row.pct == null ? "unknown" : row.pct + "%") + " | " + row.engine + " | " + (row.model || "?") + " | " + (row.session_id || "?") + " | " + row.freshness);
  if (requireEnginesPresent && !result.same_version) process.exitCode = 2;
}

try { if (require.main === module) main(); } catch (error) { console.error("ez2-ctx-fleet: " + error.message); process.exit(1); }
module.exports = { GAUGE_KEYS, OPTIONAL_GAUGE_KEYS, readFleet, validateGauge };
