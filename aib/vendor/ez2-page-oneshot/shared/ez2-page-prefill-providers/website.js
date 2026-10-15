"use strict";

// Real website crawl adapter (S3). Injectable fetch; no network when unauthorized / unflagged.
const http = require("./http.js");
const common = require("./common.js");

function createWebsiteAdapter(deps = {}) {
  return async function websiteAdapter(ctx) {
    common.assertAuthorized(deps);
    common.guardBudget(deps, "website");
    const started = common.nowMs(deps.clock);
    try {
      const url = common.pickSourceUrl(ctx, "website");
      if (!url || !/^https?:\/\//i.test(url)) {
        return common.emptyStageResult("website", deps, { provider: "website" });
      }
      const res = await http.httpGet(url, deps);
      const html = await http.readText(res);
      const fields = common.parseHtmlFields(html);
      if (!fields.primary_link) fields.primary_link = url;
      return common.stageResult("website", fields, deps, {
        url,
        verified: true,
        provider: "website"
      });
    } finally {
      common.chargeBudget(deps, "website", started);
    }
  };
}

module.exports = { createWebsiteAdapter };
