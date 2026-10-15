"use strict";

// Five-platform social pack adapter. Uses sources.socials[] or fetches a pack API.
// Env optional: EZ2_PAGE_SOCIAL_API_URL / EZ2_PAGE_SOCIAL_API_KEY (same as social).
const http = require("./http.js");
const common = require("./common.js");

function createFivePlatformsAdapter(deps = {}) {
  return async function fivePlatformsAdapter(ctx) {
    common.assertAuthorized(deps);
    common.guardBudget(deps, "five_platforms");
    const started = common.nowMs(deps.clock);
    try {
      const sources = common.plain(ctx && ctx.sources) ? ctx.sources : {};
      if (Array.isArray(sources.socials) && sources.socials.length) {
        return common.stageResult("five_platforms", { socials: sources.socials.slice() }, deps, {
          url: sources.socials[0] && sources.socials[0].url || null,
          verified: true,
          provider: "five_platforms"
        });
      }
      const apiUrl = common.readEnv(deps, "EZ2_PAGE_SOCIAL_API_URL");
      const apiKey = common.readEnv(deps, "EZ2_PAGE_SOCIAL_API_KEY");
      const query = common.pickQuery(ctx);
      if (!apiUrl || !query) {
        return common.emptyStageResult("five_platforms", deps, { provider: "five_platforms" });
      }
      const url = apiUrl + (apiUrl.indexOf("?") >= 0 ? "&" : "?") + "pack=five&q=" + encodeURIComponent(query);
      const res = await http.httpGet(url, {
        fetch: deps.fetch,
        init: { method: "GET", headers: Object.assign({ Accept: "application/json" }, common.authHeaders(apiKey)) }
      });
      const payload = await http.readJson(res);
      const socials = Array.isArray(payload && payload.socials) ? payload.socials : [];
      return common.stageResult("five_platforms", { socials }, deps, {
        url,
        verified: true,
        provider: "five_platforms"
      });
    } finally {
      common.chargeBudget(deps, "five_platforms", started);
    }
  };
}

module.exports = { createFivePlatformsAdapter };
