"use strict";

// ez2-speed-modes.js — 追蹤模式常數表(葉子模組,**零 require**,2026-09-05)。
//
// ★ 為什麼把它從 ez2-page-speed-rwd-policy.js 抽出來(裁定 b,艦長 09-05 07:2x):
//   ez2-page-plan-contract.js 只為了讀這一張表的鍵名,就 require 了整支 speed-rwd-policy,
//   而它 → performance-policy → static-bundle → vcard / theme-renderer / social-icons …
//   ⇒ bridge 的部署閉包因此**多帶 19 支 Ez2.PAGE 渲染檔**,
//     而畫布配額那條路**一次都不會執行到它們**。
//   ★ 這是「為了讀一張常數表而拖進一整棵樹」的形狀:相依是靜態的,執行卻用不到,
//     所以任何行為面的臂都不會叫 —— 只有數閉包的臂看得見。
//
// ★★ 本檔必須維持**零 require**。它是葉子,加任何相依就會把上面那棵樹接回來,
//   而接回來之後**沒有任何行為會改變**(所以也不會有行為面的格轉紅)。
//   守它的是 tools/ez2-page-plan-contract-closure-smoke.js。
//
// speed-rwd-policy 仍然 export MODES(對外 API 不變),所以既有取用端與臂都不用改。

const MODES = Object.freeze({
  performance_first: Object.freeze({
    key: "performance_first",
    structural: true,
    executable_scripts_allowed: 0,
    mobile_minimum: 100,
    desktop_minimum: 100,
    honest_reporting_only: false,
    // Bio pages are held to the uncompressed structural budget (skill §1.6):
    // they ship no scripts, so raw bytes are what the visitor waits for.
    html_bytes: 65536,
    transfer_bytes: null,
    compression_required: false
  }),
  marketing_site: Object.freeze({
    key: "marketing_site",
    structural: false,
    executable_scripts_allowed: Infinity,
    mobile_minimum: 95,
    desktop_minimum: 98,
    honest_reporting_only: false,
    // The marketing site is a single self-contained document with a scroll
    // theatre; judging it by raw bytes would fail a page that downloads 31KB and
    // scores 100. What matters to the visitor is transfer size, so the budget is
    // measured over the wire and compression is mandatory.
    html_bytes: 262144,
    transfer_bytes: 81920,
    compression_required: true
  }),
  balanced: Object.freeze({
    key: "balanced",
    structural: false,
    executable_scripts_allowed: Infinity,
    mobile_minimum: null,
    desktop_minimum: null,
    honest_reporting_only: true,
    html_bytes: 262144,
    transfer_bytes: 153600,
    compression_required: true
  }),
  full_pixel: Object.freeze({
    key: "full_pixel",
    structural: false,
    executable_scripts_allowed: Infinity,
    mobile_minimum: null,
    desktop_minimum: null,
    honest_reporting_only: true,
    html_bytes: 262144,
    transfer_bytes: 153600,
    compression_required: true
  })
});

module.exports = { MODES };
