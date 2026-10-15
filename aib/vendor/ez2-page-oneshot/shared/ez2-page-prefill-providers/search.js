"use strict";

// Real search-fallback adapter (S3). Results always unverified + provenance.via === "search".
// Env (names only): EZ2_PAGE_SEARCH_API_URL, EZ2_PAGE_SEARCH_API_KEY
// Without URL/key → empty fields (fail-soft); smoke injects fetch + env fixtures.
const http = require("./http.js");
const common = require("./common.js");

function mapSearchPayload(payload) {
  const fields = {};
  if (!payload) return fields;
  const rows = Array.isArray(payload.results)
    ? payload.results
    : (Array.isArray(payload.items) ? payload.items : (Array.isArray(payload) ? payload : []));
  const hit = rows[0] || payload.result || null;
  if (!common.plain(hit) && typeof hit !== "object") {
    if (common.plain(payload) && common.nonEmpty(payload.display_name)) {
      return {
        display_name: payload.display_name,
        bio: payload.bio || payload.snippet || undefined,
        avatar_url: payload.avatar_url || payload.image || undefined,
        primary_link: payload.primary_link || payload.url || undefined
      };
    }
    return fields;
  }
  if (common.nonEmpty(hit.title) || common.nonEmpty(hit.display_name) || common.nonEmpty(hit.name)) {
    fields.display_name = hit.title || hit.display_name || hit.name;
  }
  if (common.nonEmpty(hit.snippet) || common.nonEmpty(hit.bio) || common.nonEmpty(hit.description)) {
    fields.bio = hit.snippet || hit.bio || hit.description;
  }
  if (common.nonEmpty(hit.image) || common.nonEmpty(hit.avatar_url) || common.nonEmpty(hit.thumbnail)) {
    fields.avatar_url = hit.image || hit.avatar_url || hit.thumbnail;
  }
  if (common.nonEmpty(hit.url) || common.nonEmpty(hit.link) || common.nonEmpty(hit.primary_link)) {
    fields.primary_link = hit.url || hit.link || hit.primary_link;
  }
  if (Array.isArray(hit.socials) && hit.socials.length) fields.socials = hit.socials;
  return fields;
}

function createSearchAdapter(deps = {}) {
  return async function searchAdapter(ctx) {
    common.assertAuthorized(deps);
    common.guardBudget(deps, "search");
    const started = common.nowMs(deps.clock);
    try {
      const query = common.pickQuery(ctx);
      const apiUrl = common.readEnv(deps, "EZ2_PAGE_SEARCH_API_URL");
      const apiKey = common.readEnv(deps, "EZ2_PAGE_SEARCH_API_KEY");
      if (!query || !apiUrl) {
        return common.emptyStageResult("search", deps, { provider: "search", verified: false });
      }
      const sep = apiUrl.indexOf("?") >= 0 ? "&" : "?";
      const url = apiUrl + sep + "q=" + encodeURIComponent(query);
      const res = await http.httpGet(url, {
        fetch: deps.fetch,
        init: {
          method: "GET",
          headers: Object.assign({ Accept: "application/json" }, common.authHeaders(apiKey))
        }
      });
      const payload = await http.readJson(res);
      const fields = mapSearchPayload(payload);
      return common.stageResult("search", fields, deps, {
        url,
        verified: false,
        via: "search",
        provider: "search"
      });
    } finally {
      common.chargeBudget(deps, "search", started);
    }
  };
}

module.exports = { createSearchAdapter, mapSearchPayload };
