"use strict";

// page/aib-oneshot-0926: provider-agnostic oneshot compose.
// 任何 AI(Claude / Codex / Grok / DeepSeek / OpenRouter / AIB cockpit…)只要產出符合本契約的
// structured intent,就能經 page-api(bearer draft:write)一次站起完整草稿;發布仍須人在 review URL 確認一次。
// ★ 美 = 既有 design-pack 主題/形狀白名單,不是模型發明 CSS/HTML。
// ★ 內容欄只填安全文字 + https 連結;block_type / theme_id / shape_id 來自 allowlist。
// ★ S1/v3.108: format_id 四張卡(creator_dark/light/ocean/rosegold) + 空 intent → DEMO 示範標骨架(禁 oneshot_empty 死路)。

const designPack = require("./ez2-page-design-pack.js");
const blockContract = require("./ez2-page-block-contract.js");
const templateDemos = require("./ez2-page-template-demos.js");
const checklist = require("./ez2-page-oneshot-checklist.js");

const ALLOWED_THEMES = Object.freeze(Object.keys(designPack.THEMES));
const ALLOWED_SHAPES = Object.freeze(Object.keys(designPack.SHAPES));
const ALLOWED_BLOCK_TYPES = Object.freeze([
  "text", "link", "heading", "divider", "socials", "faq", "link_group", "cta", "image", "profile",
  "video", "mail", "shop", "gallery", "music", "tip", "event", "form"
]);
const DEFAULT_THEME = "nebula";
const DEFAULT_SHAPE = designPack.DEFAULT_SHAPE || "rounded";

/** S1 format cards — beauty only via theme × shape × avatar; block order = 1.5-screen recipe. */
const FORMAT_CARDS = Object.freeze({
  // Cards 1–2 (hangs-0927): dark nebula / light mono.
  // Cards 3–4 (v3.108): ocean (dark aquatic) / rosegold (light warm) — same 1.5-screen recipe, lg+circle avatar.
  // Whitelist themes only; no free CSS/color codes.
  creator_dark: Object.freeze({
    format_id: "creator_dark",
    theme_id: "nebula",
    shape_id: DEFAULT_SHAPE,
    avatar_size: "lg",
    avatar_shape: "circle",
    include_profile_block: true,
    recipe: "identity → 2–3 links → socials → video → mail → faq(2) → footer"
  }),
  creator_light: Object.freeze({
    format_id: "creator_light",
    theme_id: "mono",
    shape_id: DEFAULT_SHAPE,
    avatar_size: "lg",
    avatar_shape: "circle",
    include_profile_block: true,
    recipe: "identity → 2–3 links → socials → video → mail → faq(2) → footer"
  }),
  creator_ocean: Object.freeze({
    format_id: "creator_ocean",
    theme_id: "ocean",
    shape_id: DEFAULT_SHAPE,
    avatar_size: "lg",
    avatar_shape: "circle",
    include_profile_block: true,
    recipe: "identity → 2–3 links → socials → video → mail → faq(2) → footer"
  }),
  creator_rosegold: Object.freeze({
    format_id: "creator_rosegold",
    theme_id: "rosegold",
    shape_id: DEFAULT_SHAPE,
    avatar_size: "lg",
    avatar_shape: "circle",
    include_profile_block: true,
    recipe: "identity → 2–3 links → socials → video → mail → faq(2) → footer"
  })
});
const DEFAULT_FORMAT_ID = "creator_dark";
const LIGHT_THEME_IDS = Object.freeze(
  ALLOWED_THEMES.filter((id) => designPack.THEMES[id] && designPack.THEMES[id].kind === "light")
);

function plain(value) { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }

function resolveFormatId(intent, errors) {
  const raw = intent.format_id;
  if (raw != null && raw !== "") {
    const id = String(raw);
    if (FORMAT_CARDS[id]) return id;
    errors.push("oneshot_format_unknown");
    return DEFAULT_FORMAT_ID;
  }
  const hint = intent.theme_hint != null ? String(intent.theme_hint)
    : (intent.appearance_hint != null ? String(intent.appearance_hint) : "");
  if (hint === "light" || hint === "bright") return "creator_light";
  if (hint === "dark") return "creator_dark";
  // Prefer the format card whose theme_id matches (ocean→creator_ocean, mono→creator_light, …).
  if (typeof intent.theme_id === "string") {
    const byTheme = Object.keys(FORMAT_CARDS).find((id) => FORMAT_CARDS[id].theme_id === intent.theme_id);
    if (byTheme) return byTheme;
    if (LIGHT_THEME_IDS.includes(intent.theme_id)) return "creator_light";
  }
  return DEFAULT_FORMAT_ID;
}

function applyFormatDefaults(intent, appearance, errors) {
  const formatId = resolveFormatId(intent, errors);
  const card = FORMAT_CARDS[formatId];
  // theme/shape: intent wins when allowlisted; else format card; else pack default
  if (!(typeof intent.theme_id === "string" && ALLOWED_THEMES.includes(intent.theme_id))) {
    appearance.theme_id = card.theme_id;
    appearance.template_id = designPack.idFor(appearance.theme_id, appearance.shape_id);
  }
  if (!(typeof intent.shape_id === "string" && ALLOWED_SHAPES.includes(intent.shape_id))) {
    appearance.shape_id = card.shape_id;
    appearance.template_id = designPack.idFor(appearance.theme_id, appearance.shape_id);
  }
  if (intent.avatar_size == null) appearance.avatar_size = card.avatar_size;
  if (intent.avatar_shape == null) appearance.avatar_shape = card.avatar_shape;
  return card;
}

function pickThemeShape(intent) {
  const theme = typeof intent.theme_id === "string" && ALLOWED_THEMES.includes(intent.theme_id)
    ? intent.theme_id : DEFAULT_THEME;
  const shape = typeof intent.shape_id === "string" && ALLOWED_SHAPES.includes(intent.shape_id)
    ? intent.shape_id : DEFAULT_SHAPE;
  return { theme_id: theme, shape_id: shape, template_id: designPack.idFor(theme, shape) };
}

function linkOp(label, href) {
  return { op: "create", block_type: "link", visibility: "public", payload: { label, href, rel: ["noopener", "noreferrer"] } };
}

function textOp(text) {
  return { op: "create", block_type: "text", visibility: "public", payload: { text, format: "plain" } };
}

function faqOp(faq) {
  const items = Array.isArray(faq && faq.items) ? faq.items.map((item) => ({ q: String(item.q || ""), a: String(item.a || "") })) : [];
  return {
    op: "create", block_type: "faq", visibility: "public",
    payload: { items, open_first: faq && faq.open_first === true }
  };
}

function linkGroupOp(group) {
  const items = Array.isArray(group && group.items)
    ? group.items.map((item) => ({ label: String(item.label || ""), href: String(item.href || "") }))
    : [];
  return {
    op: "create", block_type: "link_group", visibility: "public",
    payload: {
      title: String(group && group.title || ""),
      open: group && group.open === true,
      items
    }
  };
}

function socialsOp() {
  return { op: "create", block_type: "socials", visibility: "public", payload: {} };
}
function profileOp() {
  return { op: "create", block_type: "profile", visibility: "public", payload: {} };
}

function createOpFromDemoRow(row) {
  const type = row[0];
  const payload = JSON.parse(JSON.stringify(row[1]));
  return { op: "create", block_type: type, visibility: "public", payload };
}

/** Empty / contentless intent → format card + DEMO_BLOCKS scaffold (示範標). */
function applyEmptyScaffold(profile, ops, formatCard) {
  const built = templateDemos.oneshotCreatorScaffold({ theme_id: formatCard.theme_id });
  Object.assign(profile, built.profile);
  for (const row of built.blocks) ops.push(createOpFromDemoRow(row));
  return built;
}

function intentHasContent(intent) {
  if (typeof intent.display_name === "string" && intent.display_name) return true;
  if (typeof intent.bio === "string" && intent.bio) return true;
  if (typeof intent.status_text === "string" && intent.status_text) return true;
  if (Array.isArray(intent.socials) && intent.socials.length) return true;
  if (Array.isArray(intent.links) && intent.links.length) return true;
  if (Array.isArray(intent.link_groups) && intent.link_groups.length) return true;
  if (intent.link_group && plain(intent.link_group)) return true;
  if (intent.faq != null) return true;
  if (Array.isArray(intent.texts) && intent.texts.length) return true;
  if (Array.isArray(intent.extra_ops) && intent.extra_ops.length) return true;
  if (intent.include_profile_block === true) return true;
  if (intent.include_socials_block === true) return true;
  return false;
}

// v3.109 刀05: the ONE clear_existing normalizer (intent scaffold + non-scaffold paths, shaped blueprint, apply options).
//   Oneshot JSON is written by many AIs ⇒ string booleans are likely. true | "true" ⇒ true; false | "false" ⇒ false;
//   undefined (key omitted) ⇒ `fallback` (the caller's path default). Anything else — null, 0/1 and other numbers, "", "TRUE",
//   "yes", objects, arrays — ⇒ { ok:false, error:"oneshot_clear_existing_invalid" }: no guessing, the caller must write nothing.
//   null is rejected, not treated as omitted: no caller passes null, and a JSON null from an AI is ambiguous.
const CLEAR_EXISTING_INVALID = "oneshot_clear_existing_invalid";
function normalizeClearExisting(value, fallback) {
  if (value === undefined) return { ok: true, value: fallback === true };
  if (value === true || value === "true") return { ok: true, value: true };
  if (value === false || value === "false") return { ok: true, value: false };
  return { ok: false, value: null, error: CLEAR_EXISTING_INVALID };
}

// v3.109 刀03 (commander MAJOR, empty-intent scaffold on an existing page): the DEMO scaffold profile is placeholder text, so
//   it may only FILL an empty field, never overwrite one. scaffoldProfilePatch(scaffoldProfile, currentProfile) keeps each
//   scaffold field only when the page's CURRENT value is empty; "current" = page-api GET /draft top-level `profile`
//   (assistantProfile = live overlaid with the draft ⇒ the draft's value when a draft exists, else live's).
//   Empty = undefined | null | "" | whitespace-only string (trim() === "") | []. Whitespace-only counts as empty because the
//   page renders it as nothing (and page-api trims on write, so "   " is stored as ""). Anything else is the owner's content.
//   currentProfile not a plain object (read gave no profile) ⇒ nothing is sent: never overwrite what we could not see.
//   Scaffold path only (meta.scaffolded): an intent that carries profile text is the user's explicit wish and is sent as-is.
function profileFieldEmpty(value) {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}
function scaffoldProfilePatch(scaffoldProfile, currentProfile) {
  const source = plain(scaffoldProfile) ? scaffoldProfile : {};
  const known = plain(currentProfile);
  const patch = {};
  const sent = [];
  const kept = [];
  for (const key of Object.keys(source)) {
    if (known && profileFieldEmpty(currentProfile[key])) { patch[key] = source[key]; sent.push(key); }
    else kept.push(key);
  }
  return { patch, sent, kept, current_known: known };
}

// v3.109 刀03 (Ez2.PAGE ruling on S2's report): the empty-intent DEMO scaffold only lands on an EMPTY page. A page that already
//   has ANY block — in its draft (when one exists) OR on live — gets NO scaffold blocks (and the scaffold path deletes nothing,
//   whatever clear_existing says); the look + empty-profile fill still apply. Before: a page whose signup template had already
//   seeded 8 demo blocks got the scaffold appended ⇒ 16 blocks, demo_remaining 14, a second identity (profile) slot.
//   "Empty" = zero blocks, or ONLY the untouched registration welcome sentence (every new page is born with it; same rule as
//   the workspace seed-on-empty check and templateSeedState's own-block count: editable.isUntouchedWelcome). Anything else —
//   demo blocks from a template, the owner's own blocks, an edited welcome — counts as content. Unreadable list ⇒ caller fails closed.
function scaffoldBlocksEmpty(blocks) {
  const list = Array.isArray(blocks) ? blocks : [];
  if (!list.length) return true;
  return require("./ez2-page-editable-workspace.js").isUntouchedWelcome(list);
}
function scaffoldTargetState(draftBlocks, liveBlocks, hasDraft) {
  const draftList = hasDraft && Array.isArray(draftBlocks) ? draftBlocks : [];
  const liveList = Array.isArray(liveBlocks) ? liveBlocks : [];
  const draftHas = hasDraft === true && !scaffoldBlocksEmpty(draftList);
  const liveHas = !scaffoldBlocksEmpty(liveList);
  return { has_blocks: draftHas || liveHas, draft_blocks: hasDraft === true ? draftList.length : null, live_blocks: liveList.length };
}

/**
 * Map a structured intent → { profile, appearance, ops[], meta }.
 * Does not call the network. Callers (any AI seat / CLI / smoke) apply via page-api.
 *
 * Intent shape (all content fields optional except display_name recommended):
 *   display_name, bio, status_text, language,
 *   socials: [{ platform, url }],
 *   theme_id, shape_id,                 // allowlisted; beauty = pick these, not invent CSS
 *   format_id: "creator_dark"|"creator_light"|"creator_ocean"|"creator_rosegold",  // S1/v3.108 format cards
 *   opening_checklist: { items: { display_name|{status}, … } }, // S2 prep list (have|missing|find_for_me)
 *   theme_hint: "dark"|"light",         // when format_id omitted
 *   links: [{ label, href }],
 *   link_groups: [{ title, open?, items: [{ label, href }] }],
 *   faq: { items: [{ q, a }], open_first? },
 *   texts: [string] | [{ text }],
 *   include_socials_block: boolean (default true when socials present)
 *   include_profile_block: boolean (emit profile slot in ops; default false = identity stays prepended)
 *   profile_index: number (optional insert index into ops for the profile slot; default 0 = first)
 *   avatar_size: "md"|"lg"|"xl", avatar_shape: "circle"|"rounded"  // appearance extras
 *   clear_existing: true|"true" | false|"false" | omitted (normalizeClearExisting; omitted ⇒ true, empty-intent scaffold ⇒ false;
 *                   anything else ⇒ oneshot_clear_existing_invalid, apply writes nothing). true ⇒ apply replaces the existing blocks.
 *   moves: [{ index: 0-based, direction: "up"|"down" }]  // reorder create ops at compose time
 *   extra_ops: create | {op:"move",block_id,direction} | {op:"reorder",order:[ids…]}
 *
 *   Empty intent (no content fields): S1 scaffolds format card + DEMO_BLOCKS (no oneshot_empty). The scaffold profile
 *   (demo display_name / bio / status_text) only fills fields that are empty on the page (scaffoldProfilePatch; applied
 *   by tools/ez2-page-oneshot-apply.js against GET /draft `profile`); non-empty fields are never overwritten.
 *   The scaffold BLOCKS only land on an empty page (scaffoldTargetState: draft if one exists AND live have no block besides
 *   the untouched welcome); otherwise apply adds none and deletes none (clear_existing ignored), result.scaffold_blocks says why.
 *
 *   Profile ordering: when include_profile_block is true (or an extra_op creates profile),
 *   the renderer places identity at that stack slot instead of prepending. For mid-page
 *   placement set profile_index, or pass ordered create ops via extra_ops including profile.
 *
 *   Live-page reorder (after blocks exist): AIB/page-api POST /draft with
 *     { op:"move", block_id, direction:"up"|"down" } or { op:"reorder", order:[ids…] }.
 *   Workspace also exposes moveBlock / reorderBlocks. Oneshot `moves` only shuffles the
 *   create stack before apply (no server ids yet).
 */
function intentToBlueprint(intent = {}) {
  if (!plain(intent)) return { ok: false, errors: ["oneshot_intent_invalid"], profile: null, appearance: null, ops: [] };
  const errors = [];
  const appearance = pickThemeShape(intent);
  if (intent.theme_id != null && !ALLOWED_THEMES.includes(String(intent.theme_id))) errors.push("oneshot_theme_unknown");
  if (intent.shape_id != null && !ALLOWED_SHAPES.includes(String(intent.shape_id))) errors.push("oneshot_shape_unknown");

  const formatCard = applyFormatDefaults(intent, appearance, errors);

  // Intent avatar overlays format defaults; validate the FINAL appearance (invalid intent must still error).
  if (intent.avatar_size != null) appearance.avatar_size = String(intent.avatar_size);
  if (intent.avatar_shape != null) appearance.avatar_shape = String(intent.avatar_shape);
  const avatarErrors = designPack.validateAvatarAppearance({ avatar_size: appearance.avatar_size, avatar_shape: appearance.avatar_shape });
  errors.push(...avatarErrors.map((e) => e.replace("page_appearance_", "oneshot_")));

  const profile = {};
  for (const key of ["display_name", "bio", "status_text", "language", "avatar_asset_id", "cover_asset_id"]) {
    if (intent[key] !== undefined) {
      if (typeof intent[key] !== "string") errors.push("oneshot_profile_" + key + "_invalid");
      else profile[key] = intent[key];
    }
  }
  if (intent.socials !== undefined) {
    if (!Array.isArray(intent.socials)) errors.push("oneshot_socials_invalid");
    else profile.socials = intent.socials;
  }

  const ops = [];
  const texts = Array.isArray(intent.texts) ? intent.texts : [];
  for (const row of texts) {
    const text = typeof row === "string" ? row : row && row.text;
    if (typeof text !== "string" || !text) { errors.push("oneshot_text_invalid"); continue; }
    ops.push(textOp(text));
  }
  if (intent.include_socials_block === true || (intent.include_socials_block !== false && Array.isArray(intent.socials) && intent.socials.length)) {
    ops.push(socialsOp());
  }
  if (intent.include_profile_block === true) {
    const at = Number.isInteger(intent.profile_index) ? Math.max(0, Math.min(ops.length, intent.profile_index)) : 0;
    ops.splice(at, 0, profileOp());
  }
  const links = Array.isArray(intent.links) ? intent.links : [];
  for (const row of links) {
    if (!plain(row) || typeof row.label !== "string" || typeof row.href !== "string") { errors.push("oneshot_link_invalid"); continue; }
    ops.push(linkOp(row.label, row.href));
  }
  const groups = Array.isArray(intent.link_groups) ? intent.link_groups : (intent.link_group ? [intent.link_group] : []);
  for (const group of groups) {
    if (!plain(group)) { errors.push("oneshot_link_group_invalid"); continue; }
    ops.push(linkGroupOp(group));
  }
  if (intent.faq != null) {
    if (!plain(intent.faq)) errors.push("oneshot_faq_invalid");
    else ops.push(faqOp(intent.faq));
  }

  // Optional raw ops passthrough — create (allowlisted types) OR move/reorder for follow-up drafts.
  if (Array.isArray(intent.extra_ops)) {
    for (const op of intent.extra_ops) {
      if (!plain(op) || typeof op.op !== "string") { errors.push("oneshot_extra_op_invalid"); continue; }
      if (op.op === "create") {
        if (!ALLOWED_BLOCK_TYPES.includes(op.block_type)) { errors.push("oneshot_extra_op_invalid"); continue; }
        ops.push({
          op: "create",
          block_type: op.block_type,
          visibility: op.visibility === "members" || op.visibility === "hidden" ? op.visibility : "public",
          payload: op.payload
        });
      } else if (op.op === "move") {
        if (typeof op.block_id !== "string" || !op.block_id || (op.direction !== "up" && op.direction !== "down")) {
          errors.push("oneshot_extra_op_move_invalid"); continue;
        }
        ops.push({ op: "move", block_id: op.block_id, direction: op.direction });
      } else if (op.op === "reorder") {
        if (!Array.isArray(op.order) || !op.order.length || op.order.some((id) => typeof id !== "string" || !id)) {
          errors.push("oneshot_extra_op_reorder_invalid"); continue;
        }
        ops.push({ op: "reorder", order: [...op.order] });
      } else {
        errors.push("oneshot_extra_op_invalid");
      }
    }
  }

  // Compose-time reorder of the create stack (no block_id yet). 「把第 N 塊往上／往下」
  // N is 1-based in NL; intent uses 0-based index into the ops array after creates are built.
  if (Array.isArray(intent.moves)) {
    for (const mv of intent.moves) {
      if (!plain(mv) || !Number.isInteger(mv.index) || (mv.direction !== "up" && mv.direction !== "down")) {
        errors.push("oneshot_move_invalid"); continue;
      }
      const i = mv.index;
      if (i < 0 || i >= ops.length) { errors.push("oneshot_move_index_oob"); continue; }
      const j = mv.direction === "up" ? i - 1 : i + 1;
      if (j < 0 || j >= ops.length) continue; // already at edge — no-op
      const tmp = ops[i]; ops[i] = ops[j]; ops[j] = tmp;
    }
  }

  let scaffolded = false;
  // S1: empty intent → format + DEMO scaffold (never oneshot_empty hard-fail).
  if (!ops.length && !Object.keys(profile).length && !intentHasContent(intent)) {
    applyEmptyScaffold(profile, ops, formatCard);
    scaffolded = true;
  }

  // Defaults unchanged: scaffold ⇒ false (was `=== true`), non-scaffold ⇒ true (was `!== false`, so "false"/0 used to clear).
  const clearNorm = normalizeClearExisting(intent.clear_existing, !scaffolded);
  if (!clearNorm.ok) errors.push(clearNorm.error);
  const clearExisting = clearNorm.value;

  // S2: opening_checklist — normalize into meta; missing required → pending, scaffold still allowed (S1).
  // Checklist alone is NOT content (intentHasContent ignores it) so empty+checklist still scaffolds.
  let openingChecklistMeta = null;
  if (intent.opening_checklist !== undefined || intent.checklist !== undefined) {
    const cn = checklist.normalizeChecklist(intent);
    if (!cn.ok) {
      for (const err of cn.errors) {
        errors.push(err.startsWith("oneshot_checklist") || err.startsWith("item_") ? err : "oneshot_checklist:" + err);
      }
    }
    openingChecklistMeta = cn.checklist;
  } else if (scaffolded) {
    openingChecklistMeta = checklist.emptyChecklist();
  }
  const checklistSummary = openingChecklistMeta
    ? checklist.summarizeChecklist(openingChecklistMeta)
    : null;

  return {
    ok: errors.length === 0,
    errors,
    profile,
    appearance: Object.assign(
      { theme_id: appearance.theme_id, shape_id: appearance.shape_id, template_id: appearance.template_id },
      appearance.avatar_size ? { avatar_size: appearance.avatar_size } : {},
      appearance.avatar_shape ? { avatar_shape: appearance.avatar_shape } : {}
    ),
    ops,
    meta: {
      clear_existing: clearExisting,
      format_id: formatCard.format_id,
      scaffolded,
      opening_checklist: openingChecklistMeta,
      checklist_pending_required: openingChecklistMeta ? openingChecklistMeta.pending_required.slice() : [],
      checklist_allow_scaffold: true,
      checklist_summary: checklistSummary ? {
        have: checklistSummary.have,
        missing: checklistSummary.missing,
        find_for_me: checklistSummary.find_for_me,
        pending_required: checklistSummary.pending_required,
        completion: checklistSummary.completion
      } : null,
      find_for_me_policy: checklist.FIND_FOR_ME_POLICY,
      // S2 ASK talk tracks — blueprint meta only (僅在 meta; server.js / page-api.js do not read it)
      ask_talk_tracks: checklist.talkTrackBundle(),
      // S1 add-ons (reserved / policy hints for apply + later slices)
      content_provenance: Object.freeze({ source_url: null, fetched_at: null, human_edited: null }),
      search_adoption: "candidates_only",
      demo_excludes_from_block_cap: checklist.demoExcludesFromBlockCap(),   // v3.109 S2 刀 02:同源(探真的 countTowardBlockLimit),不寫死
      publish_blocks_demos: "block", // engineering default #3 — wire at publish gate (needs registration/server lock)
      allowed_themes: ALLOWED_THEMES,
      allowed_shapes: ALLOWED_SHAPES,
      note: "Bearer draft:write never publishes. Print preview.review_url; human confirms once."
    }
  };
}

/** Validate create ops against the block contract; move/reorder checked for shape only. */
function validateBlueprint(blueprint, mutation = "") {
  if (!plain(blueprint) || !Array.isArray(blueprint.ops)) return { ok: false, errors: ["oneshot_blueprint_invalid"] };
  const errors = [];
  blueprint.ops.forEach((op, index) => {
    if (!plain(op) || typeof op.op !== "string") { errors.push("op_" + index + "_invalid"); return; }
    if (op.op === "move") {
      if (typeof op.block_id !== "string" || !op.block_id || (op.direction !== "up" && op.direction !== "down")) {
        errors.push("op_" + index + "_move_invalid");
      }
      return;
    }
    if (op.op === "reorder") {
      if (!Array.isArray(op.order) || !op.order.length || op.order.some((id) => typeof id !== "string" || !id)) {
        errors.push("op_" + index + "_reorder_invalid");
      }
      return;
    }
    if (op.op !== "create") { errors.push("op_" + index + "_not_create"); return; }
    const probe = {
      schema_version: blockContract.VERSION,
      tenant_id: "tenant_probe", page_id: "page_probe", block_id: "block_probe_" + index,
      block_type: op.block_type, version: 1, position: index, visibility: op.visibility || "public",
      payload: op.payload, created_at: "2026-09-26T00:00:00.000Z", updated_at: "2026-09-26T00:00:00.000Z"
    };
    const result = blockContract.validatePageBlock(probe, { test_mutation: mutation });
    if (!result.ok) errors.push("op_" + index + ":" + (result.errors || []).join(","));
  });
  return { ok: errors.length === 0, errors };
}

module.exports = {
  ALLOWED_THEMES, ALLOWED_SHAPES, ALLOWED_BLOCK_TYPES, DEFAULT_THEME, DEFAULT_SHAPE,
  FORMAT_CARDS, DEFAULT_FORMAT_ID, LIGHT_THEME_IDS,
  intentToBlueprint, validateBlueprint, pickThemeShape, resolveFormatId, applyFormatDefaults,
  normalizeClearExisting, CLEAR_EXISTING_INVALID,
  scaffoldProfilePatch, profileFieldEmpty,
  scaffoldBlocksEmpty, scaffoldTargetState,
  checklist // S2 opening checklist contract (also require("./ez2-page-oneshot-checklist.js"))
};
