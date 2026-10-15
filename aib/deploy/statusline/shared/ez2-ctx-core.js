#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

const CORE_VERSION = "1.0.0";
const THRESHOLDS = Object.freeze({ warn: 50, hot: 75, crit: 95 });
const BAR_CELLS = 10;
const LABEL = "Ez2 CTX";

const TIER_COLOR = {
  ok: [[0x58, 0xa6, 0xff], 75],
  warn: [[0xff, 0xd4, 0x00], 220],
  hot: [[0xff, 0x9f, 0x43], 215],
  crit: [[0xff, 0x6b, 0x6b], 203],
  unknown: [[0x93, 0xa1, 0xbd], 244]
};

const ICON_SETS = {
  thermal: { ok: "💧", warn: "⚠️", hot: "🌡️", crit: "🔥", unknown: "❓" },
  traffic: { ok: "🟢", warn: "🟡", hot: "🟠", crit: "🔥", unknown: "⚫" },
  battery: { ok: "🔋", warn: "🪫", hot: "⚡", crit: "🔥", unknown: "❓" },
  weather: { ok: "🌊", warn: "⛅", hot: "🌡️", crit: "🔥", unknown: "❓" },
  none: { ok: "", warn: "", hot: "", crit: "!", unknown: "" }
};

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function safeId(value) {
  return String(value || "unknown").normalize("NFKC").replace(/[^\w.-]/g, "_").slice(0, 160) || "unknown";
}

function tierOf(pct) {
  if (pct == null) return "unknown";
  if (pct >= THRESHOLDS.crit) return "crit";
  if (pct >= THRESHOLDS.hot) return "hot";
  if (pct >= THRESHOLDS.warn) return "warn";
  return "ok";
}

function contextUsage(payload) {
  const data = payload && typeof payload === "object" ? payload : {};
  const cw = data.context_window || data.contextWindow || data.context || {};
  const current = cw.current_usage || cw.currentUsage || {};
  let pct = num(cw.used_percentage ?? cw.usedPercentage ?? data.used_percentage);
  const size = num(cw.context_window_size ?? cw.contextWindowSize ?? cw.size ?? cw.limit);
  const directInput = num(current.input_tokens ?? cw.input_tokens ?? cw.total_input_tokens);
  const directOutput = num(current.output_tokens ?? cw.output_tokens ?? cw.total_output_tokens);
  let used = directInput != null || directOutput != null ? (directInput || 0) + (directOutput || 0) : null;

  // Provider percentage is the authority when supplied. Reconstructing used from
  // it avoids counting lifetime totals or cache counters as current context.
  if (pct != null) {
    pct = clamp(pct, 0, 100);
    if (size != null && size > 0) used = Math.round(size * pct / 100);
  } else if (used != null && size != null && size > 0) {
    pct = clamp(used / size * 100, 0, 100);
  }
  return {
    pct: pct == null ? null : Math.round(pct),
    used: used == null ? null : Math.round(used),
    size: size == null ? null : Math.round(size)
  };
}

function identity(payload, engine) {
  const data = payload && typeof payload === "object" ? payload : {};
  const modelValue = data.model;
  const model = modelValue && typeof modelValue === "object"
    ? modelValue.display_name || modelValue.displayName || modelValue.id
    : modelValue;
  return {
    engine: safeId(engine || data.engine || data.product || "unknown").toLowerCase(),
    session_id: String(data.session_id || data.sessionId || data.conversation_id || data.conversationId || "unknown"),
    model: String(model || data.model_id || "?")
  };
}

function normalize(payload, options = {}) {
  const id = identity(payload, options.engine);
  const usage = contextUsage(payload);
  return {
    schema: "ez2-context-gauge-v1",
    gauge_version: CORE_VERSION,
    engine: id.engine,
    session_id: id.session_id,
    model: id.model,
    pct: usage.pct,
    used: usage.used,
    size: usage.size,
    tier: tierOf(usage.pct),
    authority: usage.pct == null ? "unknown" : "provider_observed",
    source: options.source || "statusline_payload",
    observed_at: options.observed_at || new Date().toISOString()
  };
}

function human(value) {
  if (value == null) return "?";
  if (value >= 1e6) return (value / 1e6).toFixed(value >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (value >= 1e3) return Math.round(value / 1e3) + "k";
  return String(value);
}

function paint(text, tier, bold, env = process.env) {
  if (env.NO_COLOR != null || env.TERM === "dumb") return text;
  const pair = TIER_COLOR[tier] || TIER_COLOR.unknown;
  const truecolor = /truecolor|24bit/i.test(env.COLORTERM || "");
  const fg = truecolor ? `38;2;${pair[0][0]};${pair[0][1]};${pair[0][2]}` : `38;5;${pair[1]}`;
  return `\x1b[${bold ? "1;" : ""}${fg}m${text}\x1b[0m`;
}

function bar(record, env = process.env) {
  if (record.pct == null) return "";
  const n = clamp(Math.round(record.pct / 100 * BAR_CELLS), 0, BAR_CELLS);
  const style = env.EZ2_CTX_BAR || "head";
  const icons = ICON_SETS[env.EZ2_CTX_ICONS] || ICON_SETS.thermal;
  const empty = env.EZ2_CTX_BAR_EMPTY;
  if (style === "plain" || env.EZ2_CTX_ICONS === "none") {
    return "[" + paint("#".repeat(n), record.tier, false, env) + (empty || "-").repeat(BAR_CELLS - n) + "]";
  }
  if (style === "icon") {
    return "[" + paint((icons[record.tier] || "#").repeat(n), record.tier, false, env) + (empty || "-").repeat(BAR_CELLS - n) + "]";
  }
  if (style === "square") {
    const squares = { ok: "🟦", warn: "🟨", hot: "🟧", crit: "🟥", unknown: "⬛" };
    return "[" + (squares[record.tier] || "⬛").repeat(n) + (empty || "·").repeat(BAR_CELLS - n) + "]";
  }
  const done = Math.max(0, n - 1);
  return "[" + paint("━".repeat(done), record.tier, false, env) + (icons[record.tier] || "#") + "─".repeat(BAR_CELLS - done - 1) + "]";
}

function render(record, env = process.env) {
  const icons = ICON_SETS[env.EZ2_CTX_ICONS] || ICON_SETS.thermal;
  const icon = icons[record.tier] ? icons[record.tier] + " " : "";
  let ctx;
  if (record.pct == null) {
    ctx = icon + paint(LABEL + " ?", "unknown", false, env);
  } else {
    ctx = icon + paint(LABEL + " " + record.pct + "%", record.tier, record.tier === "crit", env) + " " + bar(record, env);
    if (record.used != null && record.size) ctx += " " + human(record.used) + "/" + human(record.size);
  }
  return record.model + " | " + ctx;
}

function atomicWrite(file, text, mode = 0o600) {
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch (_) {}
  const tmp = file + ".tmp-" + process.pid + "-" + Date.now();
  fs.writeFileSync(tmp, text, { mode });
  fs.renameSync(tmp, file);
  try { fs.chmodSync(file, mode); } catch (_) {}
}

function store(record, raw, options = {}) {
  const root = path.resolve(options.out_dir || process.env.EZ2_CTX_DIR || path.join(os.homedir(), ".ez2", "ctx"));
  const key = safeId(record.session_id);
  atomicWrite(path.join(root, key + ".pct"), JSON.stringify(record) + "\n");
  if (raw != null && options.snapshot !== false) {
    atomicWrite(path.join(root, key + ".input.json"), String(raw || "(empty)"));
    atomicWrite(path.join(root, "last-statusline-input.json"), String(raw || "(empty)"));
  }
  return path.join(root, key + ".pct");
}

function runStatusline(options = {}) {
  try {
    const raw = fs.readFileSync(0, "utf8");
    let payload = {};
    try { payload = JSON.parse(raw); } catch (_) {}
    const record = normalize(payload, options);
    store(record, raw, options);
    // quiet: 艙內不要印底部狀態列，但百分比檔仍要寫，右欄才讀得到。
    // 10-10:quiet 可以是函式(看量測結果決定);decorate 讓轉接層加席位與 📌 那一行(the maintainer「全部的 ai 席都要」)。
    const quiet = typeof options.quiet === "function" ? options.quiet(record) : options.quiet;
    let text = quiet ? "" : render(record);
    if (typeof options.decorate === "function") text = options.decorate(text, record);
    process.stdout.write(text);
  } catch (_) {
    process.stdout.write(options.quiet === true ? "" : "ez2 | ctx?");
  }
}

module.exports = {
  BAR_CELLS,
  CORE_VERSION,
  THRESHOLDS,
  contextUsage,
  human,
  identity,
  normalize,
  render,
  runStatusline,
  safeId,
  store,
  tierOf
};
