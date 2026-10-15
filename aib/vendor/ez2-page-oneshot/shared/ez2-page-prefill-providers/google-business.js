"use strict";

// Google Business / place lookup. Env: EZ2_PAGE_GMB_API_URL, EZ2_PAGE_GMB_API_KEY.
const http = require("./http.js");
const common = require("./common.js");

function createGoogleBusinessAdapter(deps = {}) {
  return async function googleBusinessAdapter(ctx) {
    common.assertAuthorized(deps);
    common.guardBudget(deps, "google_business");
    const started = common.nowMs(deps.clock);
    try {
      const placeUrl = common.pickSourceUrl(ctx, "google_business");
      const apiUrl = common.readEnv(deps, "EZ2_PAGE_GMB_API_URL");
      const apiKey = common.readEnv(deps, "EZ2_PAGE_GMB_API_KEY");
      const query = common.pickQuery(ctx);
      if (!apiUrl || (!query && !placeUrl)) {
        return common.emptyStageResult("google_business", deps, { provider: "google_business", url: placeUrl });
      }
      const url = apiUrl
        + (apiUrl.indexOf("?") >= 0 ? "&" : "?")
        + (placeUrl ? "url=" + encodeURIComponent(placeUrl) : "q=" + encodeURIComponent(query || ""));
      const res = await http.httpGet(url, {
        fetch: deps.fetch,
        init: { method: "GET", headers: Object.assign({ Accept: "application/json" }, common.authHeaders(apiKey)) }
      });
      const payload = await http.readJson(res);
      const fields = {};
      if (common.plain(payload)) {
        if (common.nonEmpty(payload.display_name) || common.nonEmpty(payload.name)) {
          fields.display_name = payload.display_name || payload.name;
        }
        if (common.nonEmpty(payload.bio) || common.nonEmpty(payload.description)) {
          fields.bio = payload.bio || payload.description;
        }
        if (common.nonEmpty(payload.primary_link) || common.nonEmpty(payload.url) || placeUrl) {
          fields.primary_link = payload.primary_link || payload.url || placeUrl;
        }
        if (common.plain(payload.place) || common.nonEmpty(payload.address)) {
          fields.place = payload.place || { name: fields.display_name, address: payload.address };
        }
      }
      return common.stageResult("google_business", fields, deps, {
        url: placeUrl || url,
        verified: true,
        provider: "google_business"
      });
    } finally {
      common.chargeBudget(deps, "google_business", started);
    }
  };
}

module.exports = { createGoogleBusinessAdapter };
