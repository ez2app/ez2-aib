#!/usr/bin/env node
"use strict";
// page/aib-oneshot-0926: apply a oneshot blueprint (or structured intent) via page-api.
// Provider-agnostic: same bearer scopes (draft:read/write), same JSON. Never publishes.
//
// Usage:
//   EZ2_PAGE_API_BASE=https://ez2.page/api/v1/page \
//   EZ2_PAGE_ASSISTANT_TOKEN=ezp_… \
//   node tools/ez2-page-oneshot-apply.js path/to/blueprint-or-intent.json
//
// Blueprint file may be either:
//   A) structured intent → mapped by shared/ez2-page-oneshot.js
//   B) already-shaped { profile, appearance, ops } (ops = create ops; clear_existing default true)
//   clear_existing (either shape, or options.clear_existing): true|"true" / false|"false" / omitted — anything else
//   ⇒ ok:false oneshot_clear_existing_invalid before any HTTP call (oneshot.normalizeClearExisting).
//
// Prints one review_url on success (stdout last line: REVIEW_URL=…).

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");
const oneshot = require("../shared/ez2-page-oneshot.js");
const oneshotPrefill = require("../shared/ez2-page-oneshot-prefill.js");
const adaptersMod = require("../shared/ez2-page-prefill-adapters.js");
const fillGap = require("../shared/ez2-page-fill-gap.js");   // v3.109 S4 wire: back-half fill-gap pass (read-only)

function loadBlueprint(filePath) {
  const raw = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (raw && Array.isArray(raw.ops) && (raw.profile || raw.appearance)) {
    const validated = oneshot.validateBlueprint(raw);
    const clearNorm = oneshot.normalizeClearExisting(raw.clear_existing, true);   // v3.109 刀05: was `!== false` ("false"/0 cleared)
    const out = Object.assign({ ok: validated.ok, errors: validated.errors, meta: { clear_existing: clearNorm.value } }, raw);
    if (!clearNorm.ok) return Object.assign(out, { ok: false, errors: validated.errors.concat([clearNorm.error]) });
    return out;
  }
  const mapped = oneshot.intentToBlueprint(raw);
  if (mapped.ok) {
    const validated = oneshot.validateBlueprint(mapped);
    if (!validated.ok) return Object.assign(mapped, { ok: false, errors: mapped.errors.concat(validated.errors) });
  }
  return mapped;
}

function requestJson(baseUrl, method, route, body, token) {
  const url = new URL(baseUrl.replace(/\/$/, "") + route);
  const lib = url.protocol === "https:" ? https : http;
  const payload = body == null ? null : JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = lib.request({
      hostname: url.hostname, port: url.port || (url.protocol === "https:" ? 443 : 80),
      path: url.pathname + url.search, method,
      headers: Object.assign({
        accept: "application/json",
        authorization: "Bearer " + token
      }, payload ? { "content-type": "application/json", "content-length": Buffer.byteLength(payload) } : {})
    }, (res) => {
      const parts = [];
      res.on("data", (p) => parts.push(p));
      res.on("end", () => {
        const raw = Buffer.concat(parts).toString("utf8");
        let json = null; try { json = JSON.parse(raw); } catch (_) {}
        resolve({ status: res.statusCode, raw, json });
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

/** Apply blueprint through an injected api(method, route, body) → {status,json}. Returns {ok, review_url, draft_revision_id, errors}. */
function withExpected(body, expected) {
  if (expected == null || expected === "") return body;
  return Object.assign({}, body, { expected_draft_revision: expected });
}
// v3.109 刀03: POST /draft (validateOps) REQUIRES the expected_draft_revision key; null = "no draft expected" (server opens one,
//   409 page_api_draft_exists if a draft appeared meanwhile). PUT /appearance|/profile reject null (key must be absent or a revision id),
//   so they keep withExpected. With a known revision this is byte-identical to withExpected.
function withExpectedDraftOps(body, expected) {
  if (expected == null || expected === "") return Object.assign({}, body, { expected_draft_revision: null });
  return withExpected(body, expected);
}

function willPutAppearance(blueprint) { return Boolean(blueprint.appearance && (blueprint.appearance.theme_id || blueprint.appearance.template_id)); }
function willPutProfile(profile) { return Boolean(profile && Object.keys(profile).length); }

async function applyOneshot(api, blueprint, options = {}) {
  if (!blueprint || blueprint.ok === false) {
    return { ok: false, errors: (blueprint && blueprint.errors) || ["oneshot_blueprint_invalid"], review_url: null };
  }
  // v3.109 刀05: one normalizer for options + blueprint.meta (was raw passthrough / `!== false`). Defaults unchanged: options
  //   omitted ⇒ meta (meta without the key ⇒ true; no meta at all ⇒ false). Invalid (null included) ⇒ ok:false, zero HTTP calls.
  const clearNorm = options.clear_existing !== undefined
    ? oneshot.normalizeClearExisting(options.clear_existing, false)
    : (blueprint.meta ? oneshot.normalizeClearExisting(blueprint.meta.clear_existing, true) : { ok: true, value: false });
  if (!clearNorm.ok) return { ok: false, errors: [clearNorm.error], review_url: null };
  let clear = clearNorm.value;
  let expected = options.expected_draft_revision != null ? options.expected_draft_revision : null;
  let reviewUrl = null;
  let draftRevisionId = null;
  const opKey = (suffix) => (options.operation_key_prefix || "oneshot") + "-" + suffix + "-" + Date.now().toString(36);

  // Multi-AI: always GET draft first (optimistic lock). Never blind-clear without knowing current revision.
  // Search hits must stay candidates_only (blueprint.meta.search_adoption) — apply never auto-writes crawl fields.
  const state = await api("GET", "/draft");
  if (state.status !== 200) return { ok: false, errors: ["oneshot_draft_get_failed:" + state.status], review_url: null };
  const blocks = (state.json && state.json.draft && state.json.draft.blocks) || (state.json && state.json.blocks) || [];
  expected = state.json && state.json.status && state.json.status.draft_revision_id
    || (state.json && state.json.draft && state.json.draft.draft_revision_id)
    || expected;

  // v3.109 刀03 (commander MAJOR): the empty-intent DEMO scaffold must never overwrite existing profile content. GET /draft
  //   (already read above, no extra request) carries top-level `profile` = live overlaid with the draft (page-api 262(f)), i.e.
  //   the draft's value when a draft exists, else live's. oneshot.scaffoldProfilePatch keeps a scaffold field only when that
  //   current value is empty (whitespace-only = empty); all non-empty ⇒ profileBody {} ⇒ NO PUT /profile at all. Everything
  //   downstream (fork decision, PUT order, revisions) uses profileBody, so dropping the PUT falls into the existing branches
  //   (e.g. no-draft clear with nothing to PUT ⇒ the single null POST). Intent / shaped blueprints (not meta.scaffolded) send
  //   blueprint.profile unchanged: explicit profile text is the user's wish.
  const scaffolded = Boolean(blueprint.meta && blueprint.meta.scaffolded === true);
  let profileBody = blueprint.profile;
  let scaffoldProfile = null;
  if (scaffolded && willPutProfile(blueprint.profile)) {
    const picked = oneshot.scaffoldProfilePatch(blueprint.profile, state.json && state.json.profile);
    profileBody = picked.patch;
    scaffoldProfile = { sent: picked.sent, kept: picked.kept, current_known: picked.current_known };
  }

  // v3.109 刀03 (Ez2.PAGE ruling, S2 report: signup template 8 demos + scaffold ⇒ 16 blocks / demo_remaining 14 / second
  //   profile slot): the scaffold's BLOCKS land only on an empty page. Draft (when one exists) OR live has any block besides the
  //   untouched registration welcome (oneshot.scaffoldTargetState) ⇒ add NO blocks and delete NONE — clear_existing is ignored on
  //   this path (true would otherwise wipe the owner's/template's blocks for demo filler). The look (PUT /appearance) and the
  //   empty-field profile fill above still run; the lock is untouched (same GET /draft revision, same PUT/POST expected chain).
  //   Live is read from GET /blocks (draft:read, same as the clear path); unreadable ⇒ ok:false, zero writes (fail closed).
  //   Non-scaffold paths (intent with content, shaped blueprint) never reach this.
  let scaffoldBlocks = null;
  if (scaffolded) {
    const hasDraft = Boolean(state.json && (state.json.has_draft === true || state.json.draft));
    const liveRead = await api("GET", "/blocks");
    if (liveRead.status !== 200) return { ok: false, errors: ["oneshot_live_blocks_get_failed:" + liveRead.status], review_url: null };
    const target = oneshot.scaffoldTargetState(blocks, (liveRead.json && liveRead.json.blocks) || [], hasDraft);
    const wanted = Array.isArray(blueprint.ops) ? blueprint.ops.length : 0;
    if (target.has_blocks) {
      scaffoldBlocks = { added: 0, skipped_reason: "page_has_blocks", clear_existing_ignored: clear === true, draft_blocks: target.draft_blocks, live_blocks: target.live_blocks };
      blueprint = Object.assign({}, blueprint, { ops: [] });
      clear = false;
      if (hasDraft && expected) { draftRevisionId = expected; reviewUrl = (state.json && state.json.review_url) || null; }
    } else {
      scaffoldBlocks = { added: wanted, skipped_reason: null, clear_existing_ignored: false, draft_blocks: target.draft_blocks, live_blocks: target.live_blocks };
    }
  }

  // v3.109 刀03 (clear after publish): with NO draft (server says has_draft:false) the first POST /draft opens a draft forked
  //   from live — forkContent copies every published block WITH its block_id — so clear_existing must delete the LIVE blocks,
  //   else the new blocks are appended after them (3 published + 3 new). GET /draft exposes no live ids (draft:null), so read
  //   them from GET /blocks (same draft:read scope). Only ids read here (or the fork's ids, below) are ever deleted.
  // v3.109 刀05 (atomic): the live-id deletes ride in the FINAL blocks POST together with the creates — never a separate
  //   delete-only POST — so a PUT /appearance|/profile that fails in between (e.g. 422 PAGE_SOCIAL_CONSENT_MISSING) leaves a
  //   draft equal to live (or no draft), never a 0-block draft. Lock: PUT cannot say "no draft expected" (page-api rejects
  //   expected_draft_revision:null on PUT; absent = attach to ANY draft), so when PUTs will run, the first write is a
  //   null-expected no-op POST /draft {reorder: live order} ⇒ forks draft == live (same ids/order/payload) or 409
  //   page_api_draft_exists / 422 if another writer got there first (zero writes, as 18aabe07's clear POST); its response
  //   blocks are the delete targets and its revision locks every later PUT/POST. No PUT to run ⇒ the final POST itself
  //   carries null + deletes + creates (one atomic request). Deletes + creates > 50 (page-api ops cap) ⇒ refused up front,
  //   zero writes. A draft that exists (or a read without has_draft) keeps the exact 8fbe5e3b path; clear_existing:false
  //   never reaches this read.
  let clearTargets = blocks;
  let foldedDeletes = [];
  if (clear && state.json && state.json.has_draft === false) {
    clearTargets = [];
    const live = await api("GET", "/blocks");
    if (live.status !== 200) return { ok: false, errors: ["oneshot_live_blocks_get_failed:" + live.status], review_url: null };
    const liveBlocks = (live.json && live.json.blocks) || [];
    if (liveBlocks.length) {
      const total = liveBlocks.length + (Array.isArray(blueprint.ops) ? blueprint.ops.length : 0);
      if (total > 50) return { ok: false, errors: ["oneshot_clear_ops_over_limit:" + total], review_url: null };
      let targets = liveBlocks;
      if (willPutAppearance(blueprint) || willPutProfile(profileBody)) {
        const order = [...liveBlocks].sort((a, b) => a.position - b.position || String(a.block_id).localeCompare(String(b.block_id))).map((b) => b.block_id);
        const forked = await api("POST", "/draft", withExpectedDraftOps({ operation_key: opKey("fork"), ops: [{ op: "reorder", order }] }, null));
        if (forked.status !== 200) return { ok: false, errors: ["oneshot_fork_failed:" + ((forked.json && forked.json.error) || forked.status)], review_url: null, detail: forked.json };
        expected = forked.json.draft && forked.json.draft.draft_revision_id;
        reviewUrl = forked.json.preview && forked.json.preview.review_url;
        draftRevisionId = expected;
        if (Array.isArray(forked.json.blocks)) targets = forked.json.blocks;
      }
      foldedDeletes = targets.map((b) => ({ op: "delete", block_id: b.block_id }));
    }
  }

  if (clear && clearTargets.length) {
    const deletes = clearTargets.map((b) => ({ op: "delete", block_id: b.block_id }));
    const cleared = await api("POST", "/draft", withExpectedDraftOps({ operation_key: opKey("clear"), ops: deletes }, expected));
    if (cleared.status !== 200) return { ok: false, errors: ["oneshot_clear_failed:" + ((cleared.json && cleared.json.error) || cleared.status)], review_url: null, detail: cleared.json };
    expected = cleared.json.draft && cleared.json.draft.draft_revision_id;
    reviewUrl = cleared.json.preview && cleared.json.preview.review_url;
    draftRevisionId = expected;
  }

  if (willPutAppearance(blueprint)) {
    // hangs-0927 S1:compose 的 avatar_size / avatar_shape 必須跟著 PUT /appearance 送,否則 allowlist 擋住後不黏
    const body = withExpected(Object.assign({ operation_key: opKey("appearance") },
      blueprint.appearance.template_id
        ? { template_id: blueprint.appearance.template_id }
        : { theme_id: blueprint.appearance.theme_id, shape_id: blueprint.appearance.shape_id },
      blueprint.appearance.avatar_size ? { avatar_size: blueprint.appearance.avatar_size } : {},
      blueprint.appearance.avatar_shape ? { avatar_shape: blueprint.appearance.avatar_shape } : {}), expected);
    const app = await api("PUT", "/appearance", body);
    if (app.status !== 200) return { ok: false, errors: ["oneshot_appearance_failed:" + ((app.json && app.json.error) || app.status)], review_url: null, detail: app.json };
    if (app.json.draft) {
      expected = app.json.draft.draft_revision_id;
      reviewUrl = app.json.preview && app.json.preview.review_url;
      draftRevisionId = expected;
    }
    // Scaffold: re-check against the profile PUT /appearance just answered with (same overlay, as of the revision the PUT /profile
    //   below carries) — a writer that attached a draft in between can only make us send FEWER fields, never more.
    if (scaffoldProfile && app.json.profile && typeof app.json.profile === "object") {
      const again = oneshot.scaffoldProfilePatch(profileBody, app.json.profile);
      profileBody = again.patch;
      scaffoldProfile = { sent: again.sent, kept: scaffoldProfile.kept.concat(again.kept), current_known: scaffoldProfile.current_known };
    }
  }

  if (willPutProfile(profileBody)) {
    const body = withExpected(Object.assign({ operation_key: opKey("profile") }, profileBody), expected);
    const prof = await api("PUT", "/profile", body);
    if (prof.status !== 200) return { ok: false, errors: ["oneshot_profile_failed:" + ((prof.json && prof.json.error) || prof.status)], review_url: null, detail: prof.json };
    if (prof.json.draft) {
      expected = prof.json.draft.draft_revision_id;
      reviewUrl = prof.json.preview && prof.json.preview.review_url;
      draftRevisionId = expected;
    }
  }

  const finalOps = foldedDeletes.concat(Array.isArray(blueprint.ops) ? blueprint.ops : []);
  if (finalOps.length) {
    const wrote = await api("POST", "/draft", withExpectedDraftOps({ operation_key: opKey("blocks"), ops: finalOps }, expected));
    if (wrote.status !== 200) return { ok: false, errors: ["oneshot_draft_failed:" + ((wrote.json && wrote.json.error) || wrote.status)], review_url: null, detail: wrote.json };
    expected = wrote.json.draft && wrote.json.draft.draft_revision_id;
    reviewUrl = wrote.json.preview && wrote.json.preview.review_url;
    draftRevisionId = expected;
  }

  if (!reviewUrl && draftRevisionId) reviewUrl = "https://ez2.page/manage/aib/review?draft=" + encodeURIComponent(draftRevisionId);

  // S3 hang clear: AIB oneshot draft path — run opening prefill + apply soft fills into draft
  // (NO_AUTO / candidates stay candidates_only; network only when options.authorized / consent).
  let prefillApply = null;
  if (options.skip_opening_prefill !== true) {
    try {
      prefillApply = await applyOpeningPrefillAfterOneshot(api, {
        blueprint,
        expected_draft_revision: expected,
        authorized: options.authorized,
        checklist: options.checklist || (blueprint.meta && blueprint.meta.opening_checklist),
        page_values: options.page_values || (blueprint.profile || {}),
        social_snapshots: options.social_snapshots || {},
        adapters: options.adapters,
        item_timeout_ms: options.item_timeout_ms,
        total_budget_ms: options.total_budget_ms,
        operation_key_prefix: options.operation_key_prefix || "oneshot",
        fill_gap_max_ask: options.fill_gap_max_ask
      });
      if (prefillApply && prefillApply.draft_revision_id) {
        expected = prefillApply.draft_revision_id;
        draftRevisionId = prefillApply.draft_revision_id;
      }
      if (prefillApply && prefillApply.review_url) reviewUrl = prefillApply.review_url;
    } catch (error) {
      prefillApply = { ok: true, skipped: true, error: String(error && error.message || error), blocks_editor: true };
    }
  }

  return {
    ok: true,
    errors: [],
    review_url: reviewUrl,
    draft_revision_id: draftRevisionId,
    opening_prefill: prefillApply,
    // Scaffold path only: which demo profile fields were sent (page had them empty) vs kept (page's own content). null otherwise.
    scaffold_profile: scaffoldProfile,
    // Scaffold path only: { added, skipped_reason: null | "page_has_blocks", clear_existing_ignored, draft_blocks, live_blocks }
    //   (block counts seen before the apply; draft_blocks null = no draft). null otherwise.
    scaffold_blocks: scaffoldBlocks
  };
}

/**
 * After oneshot blueprint lands on draft: run opening prefill (auth-gated) and apply soft
 * results into the same draft via PUT /profile + POST /draft (ops with provenance).
 * Avatar / primary / CTA stay candidates_only on the returned plan (never auto-written).
 */
async function applyOpeningPrefillAfterOneshot(api, input = {}) {
  const authorized = input.authorized != null ? input.authorized : false;
  const prefillResult = await oneshotPrefill.runOpeningPrefill(
    {
      authorized,
      checklist: input.checklist || {},
      page_values: input.page_values || {},
      social_snapshots: input.social_snapshots || {}
    },
    {
      adapters: input.adapters || (authorized ? adaptersMod.createAdapters({}) : null),
      item_timeout_ms: input.item_timeout_ms,
      total_budget_ms: input.total_budget_ms
    }
  );

  // Load current draft blocks for demo-replace planning.
  const state = await api("GET", "/draft");
  const draftBlocks = (state.json && state.json.draft && state.json.draft.blocks)
    || (state.json && state.json.blocks)
    || [];
  const draftProfile = (state.json && state.json.draft && state.json.draft.profile)
    || (state.json && state.json.profile)
    || (input.blueprint && input.blueprint.profile)
    || {};
  let expected = input.expected_draft_revision
    || (state.json && state.json.status && state.json.status.draft_revision_id)
    || (state.json && state.json.draft && state.json.draft.draft_revision_id)
    || null;

  const plan = oneshotPrefill.applyPrefillResultToDraft(
    { profile: draftProfile, blocks: draftBlocks, appearance: {}, meta: {} },
    prefillResult
  );

  let reviewUrl = null;
  let draftRevisionId = expected;
  const opKey = (suffix) => (input.operation_key_prefix || "oneshot") + "-prefill-" + suffix + "-" + Date.now().toString(36);

  const commit = await oneshotPrefill.commitPrefillApplyPlan({
    async setProfile(patch) {
      const body = withExpected(Object.assign({ operation_key: opKey("profile") }, patch), expected);
      const res = await api("PUT", "/profile", body);
      if (res.status !== 200) {
        const err = new Error("prefill_profile_failed:" + ((res.json && res.json.error) || res.status));
        err.detail = res.json;
        throw err;
      }
      if (res.json && res.json.draft) {
        expected = res.json.draft.draft_revision_id;
        draftRevisionId = expected;
        reviewUrl = res.json.preview && res.json.preview.review_url;
      }
    },
    async applyOps(ops) {
      const body = withExpectedDraftOps({ operation_key: opKey("blocks"), ops }, expected);
      const res = await api("POST", "/draft", body);
      if (res.status !== 200) {
        const err = new Error("prefill_ops_failed:" + ((res.json && res.json.error) || res.status));
        err.detail = res.json;
        throw err;
      }
      if (res.json && res.json.draft) {
        expected = res.json.draft.draft_revision_id;
        draftRevisionId = expected;
        reviewUrl = res.json.preview && res.json.preview.review_url;
      }
    }
  }, plan);

  // v3.109 S4 wire: back-half fill-gap pass over the post-prefill draft. Read-only (one GET), never auto_adopt,
  // ok gate inside (ok:false ⇒ zero actions). Adopt stays human: POST /manage/editor/fill-gap/adopt (CSRF + CAS).
  const fillGapPass = await backHalfFillGapAfterPrefill(api, {
    checklist: input.checklist,
    prefill_result: prefillResult,
    apply_plan: { candidates: plan.candidates, pending: plan.pending },
    max_ask: input.fill_gap_max_ask,
    fallback_profile: draftProfile,
    draft_revision_id: draftRevisionId
  });

  return {
    ok: true,
    authorized: prefillResult.authorized,
    network_calls: prefillResult.network_calls,
    applied: plan.applied,
    skipped: plan.skipped,
    candidates: plan.candidates,
    pending: plan.pending,
    labels: plan.labels,
    commit,
    draft_revision_id: draftRevisionId,
    review_url: reviewUrl,
    blocks_editor: true,
    fill_gap: fillGapPass,
    prefill_result: {
      ok: prefillResult.ok,
      network_calls: prefillResult.network_calls,
      item_statuses: Object.fromEntries(Object.keys(prefillResult.items || {}).map((id) => [id, prefillResult.items[id].status]))
    }
  };
}

/** v3.109 S4 wire: GET /draft once, then fillGap.backHalfFillGapPass. Never throws; never writes. */
async function backHalfFillGapAfterPrefill(api, input = {}) {
  try {
    const state = await api("GET", "/draft");
    if (!state || state.status !== 200 || !state.json) {
      const out = fillGap.backHalfFillGapPass({ checklist: "unavailable", max_ask: input.max_ask });
      out.errors = ["fill_gap_draft_get_failed:" + (state && state.status)];
      return out;
    }
    const json = state.json;
    const draftBlocks = (json.draft && json.draft.blocks) || json.blocks || [];
    const profile = Object.assign({}, (json.draft && json.draft.profile) || json.profile || input.fallback_profile || {});
    if (!Array.isArray(profile.socials) && Array.isArray(profile.social_links)) profile.socials = profile.social_links;
    const appearance = Object.assign({}, json.appearance || {});
    return fillGap.backHalfFillGapPass({
      checklist: input.checklist || {},
      draft: { profile, blocks: draftBlocks, appearance, meta: {} },
      prefill_result: input.prefill_result,
      apply_plan: input.apply_plan,
      max_ask: input.max_ask,
      draft_revision_id: (json.status && json.status.draft_revision_id) || (json.draft && json.draft.draft_revision_id) || input.draft_revision_id || null
    });
  } catch (error) {
    const out = fillGap.backHalfFillGapPass({ checklist: "unavailable", max_ask: input.max_ask });
    out.errors = ["fill_gap_pass_failed:" + String(error && error.message || error).slice(0, 80)];
    return out;
  }
}


async function main(argv) {
  const file = argv[2];
  if (!file) {
    console.error("usage: node tools/ez2-page-oneshot-apply.js <intent-or-blueprint.json>");
    process.exit(2);
  }
  const base = process.env.EZ2_PAGE_API_BASE || "https://ez2.page/api/v1/page";
  const token = process.env.EZ2_PAGE_ASSISTANT_TOKEN;
  if (!token) {
    console.error("EZ2_PAGE_ASSISTANT_TOKEN required");
    process.exit(2);
  }
  const blueprint = loadBlueprint(path.resolve(file));
  if (!blueprint.ok) {
    console.error("INVALID", blueprint.errors);
    process.exit(1);
  }
  const api = (method, route, body) => requestJson(base, method, route, body, token);
  const result = await applyOneshot(api, blueprint);
  if (!result.ok) {
    console.error("FAIL", result.errors, result.detail ? JSON.stringify(result.detail).slice(0, 500) : "");
    process.exit(1);
  }
  // v3.109 S4 wire: compact back-half fill-gap summary (suggestions only; adopt is human on the editor form).
  const fg = result.opening_prefill && result.opening_prefill.fill_gap;
  const fillGapSummary = fg ? {
    ok: fg.ok, errors: fg.errors, auto_adopt: false, requires_human_adopt: true,
    pending: (fg.pending || []).map((row) => row.item_id),
    asks: (fg.asks || []).map((row) => row.question),
    fills: (fg.fills || []).map((row) => row.item_id)
  } : null;
  console.log(JSON.stringify({ ok: true, draft_revision_id: result.draft_revision_id, review_url: result.review_url, fill_gap: fillGapSummary }, null, 2));
  console.log("REVIEW_URL=" + result.review_url);
}

module.exports = { loadBlueprint, applyOneshot, requestJson, applyOpeningPrefillAfterOneshot, backHalfFillGapAfterPrefill };

if (require.main === module) {
  main(process.argv).catch((err) => { console.error(err); process.exit(1); });
}
