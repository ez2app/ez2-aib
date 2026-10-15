"use strict";

// Real social profile lookup. Env: EZ2_PAGE_SOCIAL_API_URL, EZ2_PAGE_SOCIAL_API_KEY.
// Without API URL, falls back to fetching sources.social_url as HTML (og tags).
const http = require("./http.js");
const common = require("./common.js");

function createSocialAdapter(deps = {}) {
  return async function socialAdapter(ctx) {
    common.assertAuthorized(deps);
    common.guardBudget(deps, "social");
    const started = common.nowMs(deps.clock);
    try {
      const pageUrl = common.pickSourceUrl(ctx, "social");
      const apiUrl = common.readEnv(deps, "EZ2_PAGE_SOCIAL_API_URL");
      const apiKey = common.readEnv(deps, "EZ2_PAGE_SOCIAL_API_KEY");
      if (apiUrl) {
        const url = apiUrl + (apiUrl.indexOf("?") >= 0 ? "&" : "?") + "url=" + encodeURIComponent(pageUrl || common.pickQuery(ctx) || "");
        const res = await http.httpGet(url, {
          fetch: deps.fetch,
          init: { method: "GET", headers: Object.assign({ Accept: "application/json" }, common.authHeaders(apiKey)) }
        });
        const payload = await http.readJson(res);
        const fields = {};
        if (common.plain(payload)) {
          if (common.nonEmpty(payload.display_name)) fields.display_name = payload.display_name;
          if (common.nonEmpty(payload.bio) || common.nonEmpty(payload.status_text)) fields.bio = payload.bio || payload.status_text;
          if (common.nonEmpty(payload.avatar_url)) fields.avatar_url = payload.avatar_url;
          if (Array.isArray(payload.socials)) fields.socials = payload.socials;
          else if (pageUrl) fields.socials = [{ platform: "ig", url: pageUrl }];
        }
        return common.stageResult("social", fields, deps, { url: pageUrl || url, verified: true, provider: "social" });
      }
      if (!pageUrl || !/^https?:\/\//i.test(pageUrl)) {
        return common.emptyStageResult("social", deps, { provider: "social" });
      }
      const res = await http.httpGet(pageUrl, deps);
      const html = await http.readText(res);
      const fields = common.parseHtmlFields(html);
      fields.socials = [{ platform: "ig", url: pageUrl }];
      return common.stageResult("social", fields, deps, { url: pageUrl, verified: true, provider: "social" });
    } finally {
      common.chargeBudget(deps, "social", started);
    }
  };
}

module.exports = { createSocialAdapter };
