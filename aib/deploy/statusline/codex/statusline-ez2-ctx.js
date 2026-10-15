#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const core = require(fs.existsSync(path.join(__dirname, "ez2-ctx-core.js"))
  ? "./ez2-ctx-core.js" : "../shared/ez2-ctx-core.js");

const READ_LIMIT = 16 * 1024 * 1024;
// Codex 0.145.0 computes its native context percentage over the portion that
// the user can influence. The first 12k tokens cover the fixed prompt, tools,
// and compaction headroom, so both numerator and denominator exclude them.
// Keep this calculation local to the Codex adapter; the shared Claude renderer
// receives only the resulting gauge and never supplies Codex usage data.
// 2026-08-21 the maintainer 直令:CTX 顯示以 claude 為主,其他引擎一律調整成「目前使用
// 多少%」。實測 claude 與 agy 的收據本來就是「已用 / 引擎總量」(semantics 欄
// 為空),**只有 codex 這一支扣掉固定基線**,於是同一瞬間:
//     codex 自報 已用 29%   /   收據 pct 1%   /   收據 raw 5.4%
// 差 28 個百分點,而且是**低估**——低估不會打紅任何東西,而這正是決定
// 「何時該交接」的那個數字。基線歸零,語意與 claude/agy 對齊。
// ★ 舊語意("你可控的那塊用掉多少")本身沒有錯,錯的是**兩個引擎用同一個
//   欄位回答不同的問題**,而畫面上分不出來。要留舊語意就得另開欄位。
const CODEX_CONTEXT_BASELINE_TOKENS = 0;

function inside(child, parent) {
  const rel = path.relative(parent, child);
  return rel === "" || (!rel.startsWith(".." + path.sep) && rel !== "..");
}

function parseArgs(argv) {
  const out = { hook: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--hook" || arg === "--json" || arg === "--probe") out[arg.slice(2)] = true;
    else if (arg === "--transcript" || arg === "--session" || arg === "--codex-home" || arg === "--out-dir") {
      if (i + 1 >= argv.length || argv[i + 1].startsWith("--") || !argv[i + 1].trim()) throw new Error(arg + " requires a non-empty value");
      out[arg.slice(2).replace(/-/g, "_")] = argv[++i];
    }
    else throw new Error("unknown argument: " + arg);
  }
  return out;
}

function walkForSession(root, sessionId) {
  if (!sessionId || !fs.existsSync(root)) return null;
  const suffix = sessionId + ".jsonl";
  const queue = [root];
  while (queue.length) {
    const dir = queue.shift();
    let entries = [];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { continue; }
    for (const entry of entries) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory() && !entry.isSymbolicLink()) queue.push(file);
      else if (entry.isFile() && entry.name.endsWith(suffix)) return file;
    }
  }
  return null;
}

function validatedTranscript(input, options) {
  for (const key of ["codex_home", "session", "transcript", "out_dir"]) {
    if (Object.prototype.hasOwnProperty.call(options, key) && (typeof options[key] !== "string" || !options[key].trim())) throw new Error(key + " must be a non-empty value");
  }
  const codexHome = path.resolve(options.codex_home || process.env.CODEX_HOME || path.join(os.homedir(), ".codex"));
  const sessionsRoot = path.join(codexHome, "sessions");
  const sessionId = options.session || input.session_id || process.env.CODEX_THREAD_ID || "";
  let candidate = options.transcript || input.transcript_path || null;
  if (!candidate) candidate = walkForSession(sessionsRoot, sessionId);
  if (!candidate) return { codexHome, sessionId, file: null };
  const resolved = fs.realpathSync(path.resolve(candidate));
  const root = fs.realpathSync(sessionsRoot);
  if (!inside(resolved, root)) throw new Error("transcript escapes Codex sessions root");
  if (!fs.statSync(resolved).isFile()) throw new Error("transcript is not a regular file");
  const meta = sessionMeta(resolved);
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!uuid.test(meta.id) || !uuid.test(meta.session_id)) throw new Error("transcript session metadata is missing or invalid");
  if (!path.basename(resolved).endsWith("-" + meta.id + ".jsonl")) throw new Error("transcript filename does not match rollout id");
  if (sessionId && meta.session_id !== sessionId) throw new Error("transcript parent session metadata does not match requested session id");
  return { codexHome, sessionId: meta.session_id, rolloutId: meta.id, file: resolved };
}

function recentLines(file, limit = READ_LIMIT) {
  const fd = fs.openSync(file, "r");
  try {
    const size = fs.fstatSync(fd).size;
    const length = Math.min(size, limit);
    const buffer = Buffer.alloc(length);
    fs.readSync(fd, buffer, 0, length, size - length);
    let text = buffer.toString("utf8");
    if (size > length) text = text.slice(text.indexOf("\n") + 1);
    return text.trimEnd().split("\n").reverse();
  } finally { fs.closeSync(fd); }
}

function sessionMeta(file) {
  const fd = fs.openSync(file, "r");
  try {
    const size = fs.fstatSync(fd).size;
    const buffer = Buffer.alloc(Math.min(size, 1024 * 1024));
    fs.readSync(fd, buffer, 0, buffer.length, 0);
    for (const line of buffer.toString("utf8").split("\n")) {
      let event;
      try { event = JSON.parse(line); } catch (_) { continue; }
      if (event.type !== "session_meta") continue;
      const payload = event.payload || {};
      return { id: String(payload.id || ""), session_id: String(payload.session_id || "") };
    }
    return { id: "", session_id: "" };
  } finally { fs.closeSync(fd); }
}

function sessionMetaId(file) {
  return sessionMeta(file).id;
}

function inspectTranscript(file, sessionId) {
  let tokenInfo = null, model = null, effort = null, observedAt = null, modelAt = null;
  for (const line of recentLines(file)) {
    let event;
    try { event = JSON.parse(line); } catch (_) { continue; }
    const payload = event.payload || {};
    if (!tokenInfo && event.type === "event_msg" && payload.type === "token_count" && payload.info) {
      tokenInfo = payload.info;
      observedAt = event.timestamp || null;
    }
    // 2026-10-01: /model writes thread_settings_applied right away; turn_context only
    // arrives with the next turn. Reading newest-first, whichever is later wins.
    if (!model && event.type === "event_msg" && payload.type === "thread_settings_applied") {
      const settings = payload.thread_settings || {};
      if (typeof settings.model === "string" && settings.model) {
        model = settings.model;
        modelAt = event.timestamp || null;
      }
    }
    if (event.type === "turn_context") {
      if (!model && payload.model) {
        model = payload.model;
        modelAt = event.timestamp || null;
      }
      if (!effort && typeof payload.effort === "string" && /^[a-z0-9._-]{1,32}$/i.test(payload.effort)) {
        effort = payload.effort.toLowerCase();
      }
    }
    if (tokenInfo && model && effort) break;
  }
  const last = tokenInfo && tokenInfo.last_token_usage || {};
  const rawSize = tokenInfo && Number(tokenInfo.model_context_window);
  const rawUsed = Number(last.total_tokens);
  let size = rawSize;
  let used = rawUsed;
  let usedPct = null;
  if (Number.isFinite(rawSize) && rawSize <= CODEX_CONTEXT_BASELINE_TOKENS) {
    // Codex treats a window that cannot hold its fixed baseline as having no
    // user-controllable space, even before any provider usage is observed.
    size = rawSize > 0 ? rawSize : null;
    used = size;
    usedPct = 100;
  } else if (Number.isFinite(rawSize) && Number.isFinite(rawUsed)) {
    size = rawSize - CODEX_CONTEXT_BASELINE_TOKENS;
    used = Math.max(rawUsed - CODEX_CONTEXT_BASELINE_TOKENS, 0);
    const remaining = Math.max(size - used, 0);
    // Match Codex's rounding direction exactly: it rounds remaining first,
    // while the used status item is defined as 100 minus that value.
    const remainingPct = Math.round(Math.max(0, Math.min(1, remaining / size)) * 100);
    usedPct = 100 - remainingPct;
  }
  const payload = {
    session_id: sessionId,
    model: model || "?",
    context_window: {
      context_window_size: Number.isFinite(size) ? size : null,
      input_tokens: Number.isFinite(used) ? used : null,
      output_tokens: 0
    }
  };
  const record = core.normalize(payload, { engine: "codex", source: "codex_rollout_token_count", observed_at: observedAt || modelAt || new Date().toISOString() });
  if (usedPct != null) {
    record.pct = usedPct;
    record.tier = core.tierOf(usedPct);
  }
  record.context_semantics = "engine_total";  // 2026-08-21:與 claude/agy 同義
  record.context_baseline_tokens = CODEX_CONTEXT_BASELINE_TOKENS;
  record.raw_used = Number.isFinite(rawUsed) ? rawUsed : null;
  record.raw_size = Number.isFinite(rawSize) ? rawSize : null;
  record.reasoning_effort = effort;
  return record;
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  let input = {};
  if (options.hook) {
    const raw = fs.readFileSync(0, "utf8");
    try { input = JSON.parse(raw); } catch (_) {}
  }
  let resolved, record;
  try {
    resolved = validatedTranscript(input, options);
    if (!resolved.file) {
      if (options.probe) return;
      record = core.normalize({ session_id: resolved.sessionId || "unknown", model: input.model || "?" }, { engine: "codex", source: "codex_rollout_unavailable" });
    } else {
      record = inspectTranscript(resolved.file, resolved.sessionId);
    }
  } catch (error) {
    if (options.probe) throw error;
    const sessionId = options.session || input.session_id || process.env.CODEX_THREAD_ID || "unknown";
    record = core.normalize({ session_id: sessionId, model: input.model || "?" }, { engine: "codex", source: "codex_rollout_rejected" });
    record.error_class = "transcript_identity_or_format_rejected";
    core.store(record, null, { out_dir: options.out_dir, snapshot: false });
    if (!options.hook) throw error;
    return;
  }
  // Polling cannot turn missing evidence into a replacement for a good gauge.
  if (options.probe && record.model === "?") return;
  core.store(record, null, { out_dir: options.out_dir, snapshot: false });
  if (!options.hook) process.stdout.write(options.json ? JSON.stringify(record, null, 2) + "\n" : core.render(record));
}

if (require.main === module) {
  try { main(); } catch (error) {
    if (process.argv.includes("--hook")) process.exitCode = 0;
    else { console.error("ez2 codex context adapter: " + error.message); process.exitCode = 1; }
  }
}

module.exports = { inspectTranscript, parseArgs, recentLines, sessionMeta, sessionMetaId, validatedTranscript, walkForSession };
