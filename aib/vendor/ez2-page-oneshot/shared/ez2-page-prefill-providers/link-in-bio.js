"use strict";

// Real link-in-bio crawl adapter. Prefer ctx.sources.link_in_bio; optional API URL/key from env.
// Env: EZ2_PAGE_LINK_IN_BIO_API_URL, EZ2_PAGE_LINK_IN_BIO_API_KEY (optional).
const http = require("./http.js");
const common = require("./common.js");

function createLinkInBioAdapter(deps = {}) {
  return async function linkInBioAdapter(ctx) {
    common.assertAuthorized(deps);
    common.guardBudget(deps, "link_in_bio");
    const started = common.nowMs(deps.clock);
    try {
      const pageUrl = common.pickSourceUrl(ctx, "link_in_bio");
      const apiUrl = common.readEnv(deps, "EZ2_PAGE_LINK_IN_BIO_API_URL");
      const apiKey = common.readEnv(deps, "EZ2_PAGE_LINK_IN_BIO_API_KEY");
      const target = apiUrl || pageUrl;
      if (!target || !/^https?:\/\//i.test(target)) {
        return common.emptyStageResult("link_in_bio", deps, { provider: "link_in_bio" });
      }
      const url = apiUrl
        ? (apiUrl + (apiUrl.indexOf("?") >= 0 ? "&" : "?") + "url=" + encodeURIComponent(pageUrl || ""))
        : pageUrl;
      const res = await http.httpGet(url, {
        fetch: deps.fetch,
        init: {
          method: "GET",
          headers: Object.assign({ Accept: "text/html, application/json" }, common.authHeaders(apiKey))
        }
      });
      const ctype = (res.headers && (typeof res.headers.get === "function" ? res.headers.get("content-type") : res.headers["content-type"])) || "";
      let fields = {};
      if (/json/i.test(String(ctype))) {
        const payload = await http.readJson(res);
        if (common.plain(payload)) {
          if (common.nonEmpty(payload.display_name)) fields.display_name = payload.display_name;
          if (common.nonEmpty(payload.bio)) fields.bio = payload.bio;
          if (common.nonEmpty(payload.primary_link)) fields.primary_link = payload.primary_link;
          if (Array.isArray(payload.socials)) fields.socials = payload.socials;
        }
      } else {
        const html = await http.readText(res);
        fields = common.parseHtmlFields(html);
        if (!fields.primary_link && pageUrl) fields.primary_link = pageUrl;
      }
      return common.stageResult("link_in_bio", fields, deps, {
        url: pageUrl || url,
        verified: true,
        provider: "link_in_bio"
      });
    } finally {
      common.chargeBudget(deps, "link_in_bio", started);
    }
  };
}

module.exports = { createLinkInBioAdapter };
