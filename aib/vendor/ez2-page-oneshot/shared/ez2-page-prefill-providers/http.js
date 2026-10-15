"use strict";

// Injectable HTTP helpers for S3 crawl/search providers.
// Smoke / tests pass deps.fetch; production may use global fetch.
// NEVER log or commit API keys — Authorization headers stay caller-owned.

function plain(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function resolveFetch(deps = {}) {
  if (typeof deps.fetch === "function") return deps.fetch;
  if (typeof globalThis.fetch === "function") return globalThis.fetch.bind(globalThis);
  return null;
}

/**
 * GET url via injectable fetch. Throws coded errors (fail-soft upstream).
 * deps.init.headers may carry Authorization from env-derived secrets — never echo them.
 */
async function httpGet(url, deps = {}) {
  const fetchImpl = resolveFetch(deps);
  if (!fetchImpl) {
    const err = new Error("PROVIDER_FETCH_MISSING");
    err.code = "PROVIDER_FETCH_MISSING";
    throw err;
  }
  const init = Object.assign({ method: "GET" }, plain(deps.init) ? deps.init : {});
  const res = await fetchImpl(String(url), init);
  if (!res) {
    const err = new Error("PROVIDER_FETCH_EMPTY");
    err.code = "PROVIDER_FETCH_EMPTY";
    throw err;
  }
  const status = Number(res.status) || 0;
  if (status >= 400) {
    const err = new Error("PROVIDER_HTTP_" + status);
    err.code = "PROVIDER_HTTP_ERROR";
    err.status = status;
    throw err;
  }
  return res;
}

async function readText(res) {
  if (!res) return "";
  if (typeof res.text === "function") return String(await res.text());
  if (typeof res.body === "string") return res.body;
  return "";
}

async function readJson(res) {
  if (!res) return null;
  if (typeof res.json === "function") {
    try {
      return await res.json();
    } catch (_) {
      const err = new Error("PROVIDER_JSON_PARSE");
      err.code = "PROVIDER_JSON_PARSE";
      throw err;
    }
  }
  const text = await readText(res);
  try {
    return JSON.parse(text);
  } catch (_) {
    const err = new Error("PROVIDER_JSON_PARSE");
    err.code = "PROVIDER_JSON_PARSE";
    throw err;
  }
}

module.exports = {
  plain,
  resolveFetch,
  httpGet,
  readText,
  readJson
};
