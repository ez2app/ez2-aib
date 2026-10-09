#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
"use strict";
// `aib page oneshot` 的組版核心(被 page/oneshot.py 叫起來;人不直接跑這支)。
//
// 為什麼有這支:v3.109 的組版契約(intentToBlueprint → applyOneshot 的寫入順序、scaffold、clear 分流、
//   opening prefill、fill-gap)是 ez2-ai 的 JS。AIB 不重寫一份 Python 版(兩份就會漂),而是跑
//   vendor/ez2-page-oneshot/ 裡**原封不動**的那份(MANIFEST.json 記來源 commit + sha256)。
//
// ★ 這支拿不到憑證,也不連網。applyOneshot 要的 api(method, route, body) 在這裡是「問 Python」:
//   每個請求寫成一行 JSON 到 stdout,Python(持有憑證、只准白名單路徑、只寫草稿)代打,
//   回應再從 stdin 一行送回來。所以憑證只活在 Python 行程,不進這支、不進 argv/env。
// ★ 預填授權恆為 false、不建 adapters(未授權不上網搜爬;S3 契約)。
//
// 協定(每行一個 JSON,前綴 MARK;其他行一律忽略):
//   Python → 本支  第一行:{cmd:"compose"|"apply"|"checklist", intent, options}
//   本支 → Python  {type:"request", id, method, route, body}   ← apply 期間
//   Python → 本支  {type:"response", id, status, json}
//   本支 → Python  {type:"result", ...}                         ← 結束
const fs = require("fs");
const os = require("os");
const path = require("path");
const readline = require("readline");

const MARK = "\u001eAIBONESHOT ";
const VENDOR = process.env.AIB_ONESHOT_VENDOR
  || path.join(__dirname, "..", "..", "..", "vendor", "ez2-page-oneshot");

function load(rel) { return require(path.join(VENDOR, rel)); }

function emit(obj) { process.stdout.write(MARK + JSON.stringify(obj) + "\n"); }

// 與 vendored loadBlueprint 同一條路:寫一個暫存檔再叫它本人(不在這裡另寫一份判斷)。
function loadBlueprintFromObject(applyTool, raw) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aib-oneshot-"));
  const file = path.join(dir, "intent.json");
  try {
    fs.writeFileSync(file, JSON.stringify(raw), { mode: 0o600 });
    return applyTool.loadBlueprint(file);
  } finally {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_) {}
  }
}

function checklistView(intent) {
  const cl = load("shared/ez2-page-oneshot-checklist.js");
  const raw = intent && typeof intent === "object" ? intent.opening_checklist : undefined;
  const norm = cl.normalizeChecklist(raw || {});
  const reconciled = norm.ok && intent ? cl.reconcileChecklist(norm.checklist, intent) : null;
  const checklist = (reconciled && reconciled.checklist) || (norm.ok ? norm.checklist : cl.emptyChecklist());
  return {
    ok: norm.ok,
    errors: norm.errors || [],
    version: cl.VERSION,
    required: cl.REQUIRED_ITEM_IDS,
    optional: cl.OPTIONAL_ITEM_IDS,
    summary: cl.summarizeChecklist(checklist),
    ask_zh: cl.ASK_CHECKLIST_COPY_ZH,
    ask_en: cl.ASK_CHECKLIST_COPY_EN,
    ask_fill_gap_zh: cl.ASK_FILL_GAP_COPY_ZH,
    ask_demo_no_publish_zh: cl.ASK_DEMO_NO_PUBLISH_COPY_ZH
  };
}

function compose(applyTool, intent) {
  const blueprint = loadBlueprintFromObject(applyTool, intent);
  return {
    ok: Boolean(blueprint && blueprint.ok),
    errors: (blueprint && blueprint.errors) || [],
    blueprint: blueprint ? { profile: blueprint.profile, appearance: blueprint.appearance, ops: blueprint.ops, meta: blueprint.meta } : null
  };
}

async function main() {
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const pending = new Map();
  let closed = false;   // Python 那端關了 stdin ⇒ 之後的請求立刻當 599,不空等
  let first = null;
  let resolveFirst;
  const firstLine = new Promise((r) => { resolveFirst = r; });
  rl.on("line", (line) => {
    if (!line.startsWith(MARK)) return;
    let msg;
    try { msg = JSON.parse(line.slice(MARK.length)); } catch (_) { return; }
    if (first === null) { first = msg; resolveFirst(msg); return; }
    if (msg.type === "response" && pending.has(msg.id)) {
      const done = pending.get(msg.id);
      pending.delete(msg.id);
      done({ status: msg.status, json: msg.json, raw: "" });
    }
  });
  rl.on("close", () => {
    closed = true;
    for (const done of pending.values()) done({ status: 599, json: { error: "aib_oneshot_broker_closed" }, raw: "" });
    pending.clear();
    if (first === null) resolveFirst(null);
  });

  const req = await firstLine;
  if (!req) { emit({ type: "result", ok: false, errors: ["aib_oneshot_no_request"] }); return; }
  const applyTool = load("tools/ez2-page-oneshot-apply.js");

  if (req.cmd === "checklist") { emit(Object.assign({ type: "result" }, checklistView(req.intent))); rl.close(); return; }
  const composed = compose(applyTool, req.intent);
  if (req.cmd === "compose" || !composed.ok) {
    emit(Object.assign({ type: "result", checklist: checklistView(req.intent) }, composed));
    rl.close();
    return;
  }
  if (req.cmd !== "apply") { emit({ type: "result", ok: false, errors: ["aib_oneshot_unknown_cmd"] }); rl.close(); return; }

  let seq = 0;
  const api = (method, route, body) => new Promise((resolve) => {
    if (closed) { resolve({ status: 599, json: { error: "aib_oneshot_broker_closed" }, raw: "" }); return; }
    seq += 1;
    pending.set(seq, resolve);
    emit({ type: "request", id: seq, method, route, body: body == null ? null : body });
  });
  const options = req.options || {};
  const blueprint = loadBlueprintFromObject(applyTool, req.intent);
  let result;
  try {
    result = await applyTool.applyOneshot(api, blueprint, {
      clear_existing: options.clear_existing,           // Python 已驗過是 JSON 布林
      operation_key_prefix: options.operation_key_prefix,
      authorized: false,                                  // 恆不上網預填(S3:未授權 ⇒ network_calls 0)
      adapters: null
    });
  } catch (error) {
    result = { ok: false, errors: ["aib_oneshot_core_threw:" + String(error && error.message || error).slice(0, 160)] };
  }
  const fg = result && result.opening_prefill && result.opening_prefill.fill_gap;
  emit({
    type: "result",
    ok: Boolean(result && result.ok),
    errors: (result && result.errors) || [],
    detail: result && result.detail ? result.detail : null,
    review_url: result ? result.review_url || null : null,
    draft_revision_id: result ? result.draft_revision_id || null : null,
    scaffold_profile: result ? result.scaffold_profile || null : null,
    scaffold_blocks: result ? result.scaffold_blocks || null : null,
    network_calls: result && result.opening_prefill ? result.opening_prefill.network_calls : 0,
    fill_gap: fg ? {
      ok: fg.ok, errors: fg.errors || [], auto_adopt: false, requires_human_adopt: true,
      pending: (fg.pending || []).map((row) => row.item_id),
      asks: (fg.asks || []).map((row) => row.question),
      fills: (fg.fills || []).map((row) => row.item_id)
    } : null,
    blueprint_meta: { clear_existing: blueprint.meta && blueprint.meta.clear_existing, scaffolded: Boolean(blueprint.meta && blueprint.meta.scaffolded),
                      format_id: blueprint.meta && blueprint.meta.format_id,
                      checklist_pending_required: (blueprint.meta && blueprint.meta.checklist_pending_required) || [] }
  });
  rl.close();
}

main().catch((error) => {
  emit({ type: "result", ok: false, errors: ["aib_oneshot_core_failed:" + String(error && error.message || error).slice(0, 160)] });
  process.exitCode = 1;
});
