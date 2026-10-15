"use strict";

// 317 第 16 項(301;RL-132 A41 ping / A42 給 AIB / A44 不存 IP):每頁基本統計。
//
// 存的只有「日計數」:{ views: n, clicks: { <block_id>: { <slot>: n } } },檔名 <dir>/<tenant>.<page>/<YYYY-MM-DD>.json(台北日界,
// 與 AIB 配額同一條日界)。**零 cookie、零腳本、不存 IP、不存 UA、不去重** ⇒ 名稱是「瀏覽次數」不是「人數」,後台字面就寫「次」。
// 瀏覽 = 公開頁 GET 200(非 HEAD、非 page.css / contact.vcf、非爬蟲 UA);點擊 = 區塊裡出站連結的 HTML `ping` 屬性打到 POST /c/…
// (瀏覽器原生、不多一跳、不改網址;Firefox 預設關 ⇒ 少算,登債)。
//
// 每請求 read-modify-write 太貴:記憶體累加,每 FLUSH_EVERY_MS 或每 FLUSH_EVERY_HITS 次 flush 一次(tmp+rename),程序收尾 flush。
// 保留 RETENTION_DAYS 天(建立時與跨日時清)。多台機器各算各的(今天單台;登債)。
// 刀(options.test_mutation):stats-count-bots(爬蟲也算)⇒ T1 必紅;stats-write-ip(把 ip 寫進檔)⇒ T1 必紅。

const fs = require("fs");
const path = require("path");

const VERSION = "2026-09-18.ez2-page-stats.v1";
const RETENTION_DAYS = 90;
const FLUSH_EVERY_MS = 5000;
const FLUSH_EVERY_HITS = 50;
const DAY_OFFSET_MS = 8 * 3600 * 1000;      // 台北 UTC+8,與 registration 的 AIB 配額日界同一條
const KINDS = Object.freeze(["view", "click"]);
const SLOTS = Object.freeze(["link", "nav", "review", "map", "social", "other"]);
// 爬蟲 / 預覽抓取器:不算瀏覽(不是人)。只看 UA 字面,不存它。
const BOT_UA_RE = /bot|crawl|spider|slurp|preview|facebookexternalhit|line-poker|whatsapp|telegram|discord|twitter|embedly|pinterest|skype|curl\/|wget\/|python-requests|go-http-client|okhttp|headless/i;
const ID_RE = /^[a-z]+_[a-f0-9]{8,64}$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }

function dayOf(nowMs) {
  return new Date(Number(nowMs) + DAY_OFFSET_MS).toISOString().slice(0, 10);
}

function isBot(userAgent) {
  const ua = String(userAgent || "");
  return !ua || BOT_UA_RE.test(ua);
}

function emptyDay() { return { views: 0, clicks: {} }; }

function addInto(target, delta) {
  target.views = (target.views || 0) + (delta.views || 0);
  target.clicks = target.clicks && typeof target.clicks === "object" ? target.clicks : {};
  for (const [blockId, slots] of Object.entries(delta.clicks || {})) {
    const row = target.clicks[blockId] = target.clicks[blockId] && typeof target.clicks[blockId] === "object" ? target.clicks[blockId] : {};
    for (const [slot, n] of Object.entries(slots)) row[slot] = (row[slot] || 0) + n;
  }
  return target;
}

function createStatsStore(options = {}) {
  const directory = typeof options.directory === "string" && options.directory.trim() ? path.resolve(options.directory) : "";
  if (!directory) fail("PAGE_STATS_DIRECTORY_REQUIRED");
  const now = typeof options.now === "function" ? options.now : Date.now;
  const mutation = String(options.test_mutation || "");
  const pending = new Map();            // "<tenant>.<page>\u0000<day>" → { views, clicks }
  let pendingHits = 0;
  let lastFlushAt = now();
  let lastDay = dayOf(now());
  let timer = null;

  function scopeDir(scope) {
    if (!scope || !ID_RE.test(String(scope.tenant_id || "")) || !ID_RE.test(String(scope.page_id || ""))) fail("PAGE_STATS_SCOPE_INVALID");
    return path.join(directory, scope.tenant_id + "." + scope.page_id);
  }

  function readDay(scope, day) {
    const file = path.join(scopeDir(scope), day + ".json");
    let stored = null;
    try { stored = JSON.parse(fs.readFileSync(file, "utf8")); } catch (error) { if (!error || error.code !== "ENOENT") return emptyDay(); }
    const base = stored && typeof stored === "object" ? addInto(emptyDay(), stored) : emptyDay();
    const live = pending.get(scope.tenant_id + "." + scope.page_id + "\u0000" + day);
    return live ? addInto(base, live) : base;
  }

  function flush() {
    const entries = [...pending.entries()];
    pending.clear();
    pendingHits = 0;
    lastFlushAt = now();
    for (const [key, delta] of entries) {
      const [scopeKey, day] = key.split("\u0000");
      const dir = path.join(directory, scopeKey);
      const file = path.join(dir, day + ".json");
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      let stored = emptyDay(), parsed = null;
      try { parsed = JSON.parse(fs.readFileSync(file, "utf8")); stored = addInto(emptyDay(), parsed); } catch (_) { stored = emptyDay(); }
      addInto(stored, delta);
      if (mutation === "stats-write-ip" && (delta.ip || (parsed && parsed.ip))) stored.ip = delta.ip || parsed.ip;
      const staging = file + ".tmp";
      const handle = fs.openSync(staging, "w", 0o600);
      try { fs.writeSync(handle, JSON.stringify(stored)); fs.fsyncSync(handle); } finally { fs.closeSync(handle); }
      fs.renameSync(staging, file);
    }
    if (timer) { clearTimeout(timer); timer = null; }
    return entries.length;
  }

  function schedule() {
    if (timer) return;
    timer = setTimeout(() => { timer = null; try { flush(); } catch (_) { /* 下一次 hit 再試;計數留在記憶體 */ } }, FLUSH_EVERY_MS);
    if (typeof timer.unref === "function") timer.unref();
  }

  // 保留期:刪掉早於 RETENTION_DAYS 的日檔(只認 YYYY-MM-DD.json 檔名,別的不碰)
  function prune() {
    const cutoff = dayOf(now() - RETENTION_DAYS * 86400000);
    let removed = 0;
    let dirs = [];
    try { dirs = fs.readdirSync(directory, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name); } catch (_) { return 0; }
    for (const name of dirs) {
      let files = [];
      try { files = fs.readdirSync(path.join(directory, name)); } catch (_) { continue; }
      for (const file of files) {
        const day = file.replace(/\.json$/, "");
        if (!file.endsWith(".json") || !DAY_RE.test(day) || day >= cutoff) continue;
        try { fs.unlinkSync(path.join(directory, name, file)); removed += 1; } catch (_) { /* 別的程序先刪了 */ }
      }
    }
    return removed;
  }

  function hit(scope, kind, extra = {}) {
    if (!KINDS.includes(kind)) fail("PAGE_STATS_KIND_INVALID", [kind]);
    scopeDir(scope);
    if (kind === "view" && mutation !== "stats-count-bots" && isBot(extra.user_agent)) return false;
    const day = dayOf(now());
    if (day !== lastDay) { lastDay = day; try { prune(); } catch (_) { /* 清不掉不影響計數 */ } }
    const key = scope.tenant_id + "." + scope.page_id + "\u0000" + day;
    const delta = pending.get(key) || emptyDay();
    if (kind === "view") delta.views += 1;
    else {
      const blockId = String(extra.block_id || "");
      const slot = SLOTS.includes(extra.slot) ? extra.slot : "other";
      if (!ID_RE.test(blockId)) fail("PAGE_STATS_BLOCK_INVALID");
      const row = delta.clicks[blockId] = delta.clicks[blockId] || {};
      row[slot] = (row[slot] || 0) + 1;
    }
    if (mutation === "stats-write-ip" && extra.ip) delta.ip = String(extra.ip);
    pending.set(key, delta);
    pendingHits += 1;
    if (pendingHits >= FLUSH_EVERY_HITS || now() - lastFlushAt >= FLUSH_EVERY_MS) flush(); else schedule();
    return true;
  }

  // 讀模型:今天 / 近 7 天 / 近 30 天(含今天)三欄,每欄 { views, clicks: {block_id: {slot: n}}, days: n(有檔的天數)}
  function summary(scope) {
    const today = dayOf(now());
    const windows = { today: 1, last_7: 7, last_30: 30 };
    const out = { schema_version: VERSION, day: today, retention_days: RETENTION_DAYS };
    for (const [name, span] of Object.entries(windows)) {
      const total = emptyDay();
      let days = 0;
      for (let i = 0; i < span; i += 1) {
        const day = dayOf(now() - i * 86400000);
        const row = readDay(scope, day);
        if (row.views || Object.keys(row.clicks).length) days += 1;
        addInto(total, row);
      }
      out[name] = { views: total.views, clicks: total.clicks, days };
    }
    return out;
  }

  try { fs.mkdirSync(directory, { recursive: true, mode: 0o700 }); prune(); } catch (_) { /* 目錄開不了在第一次 flush 時具名炸 */ }
  process.on("exit", () => { try { flush(); } catch (_) { /* 收尾盡力 */ } });

  return Object.freeze({ VERSION, directory, hit, flush, summary, prune, isBot, dayOf, pending_size: () => pending.size });
}

// 出站連結的 ping 目標(渲染器用)。slot 由 <a> 的 class 判:place 的 map / c(導航)/ c rv(留評論)、socials 的 s、其餘 link。
function pingPath(pageId, blockId, slot) {
  return "/c/" + pageId + "/" + blockId + "/" + (SLOTS.includes(slot) ? slot : "other");
}

// 把一顆區塊 HTML 裡每個出站 <a href="http…"> 加上 ping=(區塊內容全經 escapeHtml,`<a ` 不可能出現在正文裡)。
// 刀 stats-no-ping ⇒ T1 必紅(點擊永遠 0)。
function withPings(blockHtml, pageId, blockId, mutation = "") {
  if (mutation === "stats-no-ping" || !ID_RE.test(String(pageId || "")) || !ID_RE.test(String(blockId || ""))) return blockHtml;
  return String(blockHtml).replace(/<a class="([^"]*)"([^>]*?) href="(https?:)/g, (whole, cls, rest, scheme) => {
    const classes = cls.split(/\s+/);
    const slot = classes.includes("rv") ? "review" : classes[0] === "map" || classes[0] === "c" ? "nav" : classes[0] === "s" ? "social" : "link";
    return '<a class="' + cls + '"' + rest + ' ping="' + pingPath(pageId, blockId, slot) + '" href="' + scheme;
  });
}

module.exports = { VERSION, RETENTION_DAYS, FLUSH_EVERY_MS, FLUSH_EVERY_HITS, KINDS, SLOTS, BOT_UA_RE, ID_RE, createStatsStore, dayOf, isBot, pingPath, withPings };
