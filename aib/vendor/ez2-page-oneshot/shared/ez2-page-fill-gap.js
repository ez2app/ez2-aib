"use strict";
/**
 * S4 後段補洞 pass＋清單對帳（契約＋純函式）。
 * - 正本輸入＝S2 shared/ez2-page-oneshot-checklist.js（opening_checklist／missing／link_or_social…）
 * - 主路徑（S3 並行預填）跑過後：掃清單仍空／仍示範的格；有則補、無則標待改或再問一句。
 * - 使用者新貼連結 → 窄範圍回補（非全量重爬）。
 * - 不當主等待點、不擋進入編輯／確認發布（blocks_editor／blocks_publish 皆 true＝允許繼續；別名 allows_*）。
 * - 與 S3 共用 provenance／示範簽章語意（isDemoBlock）；不重做授權閘與開場並行線。
 * - 實際網路抓取由 S3 供應；本模組只對帳＋產出 action／ask。
 * - S4×S3：reconcilePass／narrow 吃 prefill result（extractCandidatesFromPrefill）；
 *   遵守 FIND_FOR_ME／NO_AUTO_WRITE（頭像／主連結等只候選、auto_adopt=false）。
 *   人類「採用」：adoptCandidateToDraft 寫入草稿（非 auto_adopt、不發佈）；清示範簽章後 demo_remaining 下降。
 */
const demos = require("./ez2-page-template-demos.js");
const checklist = require("./ez2-page-oneshot-checklist.js");
// v3.109 S3 cross-review: adopt reuses the SAME rules the draft→publish path enforces (no new rules):
//   - href: block-contract safeHref (https, no credentials, no control chars) — what applyDraftOps validates CTA/link with;
//   - socials: social-icons.has + social-intake.validateSocialIntake — the pair registration validateSocialLinks delegates to;
//   - profile text: registry char classes (registration validateProfileTarget / validateRecord:
//     bio ⟂ [\u0000-\u001f]; display_name／status_text ⟂ [\u0000-\u001f<>]); re-validated server-side.
//     v3.109 S4 刀02: cleaned with the S3 prefill rule (see S3_TEXT below), not a private strip, so a bio that
//     prefill auto-applies and the same bio a human adopts land byte-identical.
const blockContract = require("./ez2-page-block-contract.js");
const socialIcons = require("./ez2-page-social-icons.js");
const socialIntake = require("./ez2-page-social-intake.js");
const TEXT_FORMATS = Object.freeze(["plain", "markdown"]);   // block-contract text.format

// ---- S3_TEXT: mirror of S3 shared/ez2-page-oneshot-prefill.js @ 93bb9740 (branch page/v3109-s3-prefill-bio-0929, S3 final) ----
// Not importable (S3 is not in this base; that file is S3-owned), so the semantics are copied EXACTLY; keep in sync:
//   stripTags        ← S3 stripTags        (loop /<\/?[A-Za-z!][^>]*>/g until stable)
//   truncateText     ← S3 truncateText     (≤ max UTF-16 units, cut on grapheme (Intl.Segmenter) else code point;
//                                           93bb9740 R4: first grapheme alone over budget ⇒ code-point fallback, non-empty)
//   s3SingleLine     ← S3 singleLine       (controls incl. \u007f → ONE space, collapse spaces, trim) — profile fields
//   s3MultiLine      ← S3 multiLine        (\r\n|\r → \n; controls except \n incl. \u007f → space; per-line collapse+trim;
//                                           3+ newlines → one blank line) — text-block payload only
//   s3NormalizeBio   ← S3 normalizeBioInput ({text ≤ BIO_MAX single-line, block_text ≤ BIO_BLOCK_MAX multi-line, format};
//                                           objects: body > text > bio; null when empty)
//   s3DisplayName    ← S3 normalizeDisplayName (singleLine, drop <>, ≤ 80)
// 905f3f2a → 93bb9740 text-rule delta = truncateText fallback only (stripTags / singleLine / multiLine / normalizeBioInput /
//   normalizeDisplayName / caps / status_text derivation unchanged; they inherit the fallback through truncateText).
//   S3 R4's other change (bioPresence ignores the AIB hidden search marker text block) has no S4 counterpart: S4's bio
//   gap reads profile.bio / profile.status_text only (ITEM_MAPS_TO.bio), and adopt writes only a demo text block.
//   tools/ez2-page-fill-gap-smoke.js G12 loads S3 93bb9740 itself (git archive) and asserts parity.
const S3_HTML_TAG_RE = /<\/?[A-Za-z!][^>]*>/g;
const S3_BIO_MAX = 300;
const S3_BIO_BLOCK_MAX = 2000;
const S3_DISPLAY_NAME_MAX = 80;
const S3_STATUS_TEXT_MAX = 40;
function s3StripTags(text) {
  let out = String(text == null ? "" : text);
  for (;;) {
    const next = out.replace(S3_HTML_TAG_RE, "");
    if (next === out) return out;
    out = next;
  }
}
let s3Segmenter = null;
function s3GraphemePieces(text) {
  if (s3Segmenter === null) {
    try {
      s3Segmenter = typeof Intl !== "undefined" && typeof Intl.Segmenter === "function"
        ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
        : false;
    } catch (_) { s3Segmenter = false; }
  }
  return s3Segmenter ? Array.from(s3Segmenter.segment(text), (item) => item.segment) : Array.from(text);
}
function s3TruncateText(value, max) {
  const text = String(value == null ? "" : value);
  const limit = Math.max(0, Math.floor(Number(max) || 0));
  if (text.length <= limit) return text;
  let out = "";
  for (const piece of s3GraphemePieces(text)) {
    if (out.length + piece.length > limit) break;
    out += piece;
  }
  // S3 93bb9740 R4: first grapheme alone longer than the budget ("a" + 400 × U+0301) ⇒ grapheme walk kept nothing ⇒
  //   code-point fallback (never splits a surrogate pair), so a real bio / name is not silently dropped.
  if (!out && limit > 0) {
    for (const point of Array.from(text)) {
      if (out.length + point.length > limit) break;
      out += point;
    }
  }
  return out;
}
function s3SingleLine(text) {
  return s3StripTags(text).replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/ {2,}/g, " ").trim();
}
function s3MultiLine(text) {
  return s3StripTags(String(text == null ? "" : text).replace(/\r\n?/g, "\n"))
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f]+/g, " ")
    .split("\n").map((line) => line.replace(/ {2,}/g, " ").trim()).join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
function s3NormalizeBio(value) {
  let raw = null;
  let format = null;
  if (typeof value === "string") {
    raw = value;
  } else if (value && typeof value === "object" && !Array.isArray(value)) {
    if (typeof value.body === "string" && value.body.trim()) raw = value.body;
    else if (typeof value.text === "string" && value.text.trim()) raw = value.text;
    else if (typeof value.bio === "string" && value.bio.trim()) raw = value.bio;
    if (TEXT_FORMATS.includes(value.format)) format = value.format;
  }
  if (raw == null) return null;
  const text = s3TruncateText(s3SingleLine(raw), S3_BIO_MAX).trim();
  if (!text) return null;
  const blockText = s3TruncateText(s3MultiLine(raw), S3_BIO_BLOCK_MAX).trim();
  return { text, block_text: blockText || text, format };
}
function s3DisplayName(value) {
  if (typeof value !== "string") return "";
  return s3TruncateText(s3SingleLine(value).replace(/[<>]/g, "").trim(), S3_DISPLAY_NAME_MAX).trim();
}

const GAP_KINDS = Object.freeze(["empty", "demo"]);
const ACTION_TYPES = Object.freeze(["fill", "mark_pending", "ask_one", "narrow_refill"]);

/**
 * S4 對草稿的 maps_to（S2 checklist 只管 status／ids；落地對帳由本 adapter 持有）。
 * ids 對齊 S2 ITEM_IDS。
 */
const ITEM_MAPS_TO = Object.freeze({
  display_name: Object.freeze(["profile.display_name"]),
  bio: Object.freeze(["profile.bio", "profile.status_text"]),
  link_or_social: Object.freeze(["blocks:link", "blocks:cta", "profile.socials"]),
  avatar_cover: Object.freeze(["profile.avatar", "profile.cover"]),
  cta_links: Object.freeze(["blocks:link", "blocks:cta", "blocks:link_group"]),
  socials: Object.freeze(["profile.socials", "blocks:cta"]),
  faq: Object.freeze(["blocks:faq"]),
  video_or_gallery: Object.freeze(["blocks:video", "blocks:gallery", "blocks:image"]),
  form_or_product: Object.freeze(["blocks:form", "blocks:mail", "blocks:shop"]),
  preferred_theme: Object.freeze(["appearance.theme_id", "appearance.format_id"])
});

function itemSpec(id) {
  const resolved = checklist.resolveItemId(id) || id;
  const def = checklist.ITEM_DEF_BY_ID[resolved];
  if (!def) return null;
  return {
    id: def.id,
    label: def.label_zh || def.label_en || def.id,
    required: !!def.required,
    maps_to: ITEM_MAPS_TO[def.id] || []
  };
}

/** 區塊是否「仍示範」——對齊 template-demos 簽章（改過任一 payload 字就不是示範）。 */
function isStillDemo(block) {
  return demos.isDemoBlock(block);
}

/**
 * 內容語意：empty／demo／crawl_prefill／real。
 * crawl_prefill = 有 provenance 且非示範簽章；real = 非空且非 demo（或人編過）。
 */
function contentKindOfBlock(block) {
  if (!block) return "empty";
  if (isStillDemo(block)) return "demo";
  const prov = block.provenance;
  if (prov && typeof prov === "object" && (prov.source || prov.url || prov.via)) return "crawl_prefill";
  const payload = block.payload;
  if (payload == null) return "empty";
  if (typeof payload === "object" && Object.keys(payload).length === 0) return "empty";
  return "real";
}

function profileFieldEmpty(profile, key) {
  if (!profile || typeof profile !== "object") return true;
  const v = profile[key];
  if (v == null) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  return false;
}

/**
 * 把 S2 normalizeChecklist 結果（或已含 items 的物件）收成掃描用列陣列。
 * @returns {{ ok, errors, items: Array<{id,status,required,pending,note?}> , checklist? }}
 */
function normalizeOpeningChecklist(input) {
  // Already a scan-shaped result (items as array with status).
  if (input && typeof input === "object" && Array.isArray(input.items) &&
      input.items.length && typeof input.items[0].status === "string" &&
      input.items[0].id != null && !input.checklist) {
    return {
      ok: input.ok !== false,
      errors: input.errors || [],
      items: input.items.slice(),
      checklist: input.checklist || null
    };
  }
  const normalized = checklist.normalizeChecklist(input || {});
  if (!normalized.checklist) {
    return { ok: false, errors: normalized.errors || ["oneshot_checklist_invalid"], items: [], checklist: null };
  }
  const items = checklist.ITEM_IDS.map((id) => {
    const entry = normalized.checklist.items[id];
    const row = {
      id,
      status: entry.status,
      required: !!entry.required,
      pending: !!entry.pending
    };
    if (entry.note !== undefined) row.note = entry.note;
    return row;
  });
  return {
    ok: normalized.ok,
    errors: normalized.errors || [],
    items,
    checklist: normalized.checklist
  };
}

/**
 * 依清單 maps_to 判斷該項在草稿上是否仍空／仍示範。
 * @returns {{ id, gap_kind: 'empty'|'demo'|null, evidence }}
 */
function assessChecklistItem(item, draft) {
  const spec = itemSpec(item.id);
  if (!spec) return { id: item.id, gap_kind: null, evidence: "unknown_item" };
  const profile = (draft && draft.profile) || {};
  const blocks = Array.isArray(draft && draft.blocks) ? draft.blocks : [];
  const maps = spec.maps_to || [];

  let sawDemo = false;
  let sawRealOrPrefill = false;
  let sawAny = false;

  for (const map of maps) {
    if (map.startsWith("profile.")) {
      const key = map.slice("profile.".length);
      if (key === "socials") {
        const socials = profile.socials;
        if (Array.isArray(socials) && socials.length) {
          sawAny = true;
          sawRealOrPrefill = true;
        }
      } else if (!profileFieldEmpty(profile, key)) {
        sawAny = true;
        sawRealOrPrefill = true;
      }
      continue;
    }
    if (map.startsWith("appearance.")) {
      const key = map.slice("appearance.".length);
      const appearance = (draft && draft.appearance) || {};
      if (appearance[key] != null && String(appearance[key]).trim() !== "") {
        sawAny = true;
        sawRealOrPrefill = true;
      }
      continue;
    }
    if (map.startsWith("blocks:")) {
      const type = map.slice("blocks:".length);
      const matched = blocks.filter((b) => b && b.block_type === type);
      for (const b of matched) {
        sawAny = true;
        const kind = contentKindOfBlock(b);
        if (kind === "demo") sawDemo = true;
        else if (kind === "real" || kind === "crawl_prefill") sawRealOrPrefill = true;
      }
    }
  }

  // preferred_theme：只要有 theme／format 就算有
  if (spec.id === "preferred_theme") {
    const appearance = (draft && draft.appearance) || {};
    if (appearance.theme_id || appearance.format_id) {
      return { id: item.id, gap_kind: null, evidence: "appearance_set" };
    }
    return { id: item.id, gap_kind: "empty", evidence: "appearance_missing" };
  }

  if (sawRealOrPrefill) return { id: item.id, gap_kind: null, evidence: "filled" };
  if (sawDemo) return { id: item.id, gap_kind: "demo", evidence: "demo_signature" };
  if (!sawAny) return { id: item.id, gap_kind: "empty", evidence: "missing" };
  return { id: item.id, gap_kind: "empty", evidence: "unresolved" };
}

/**
 * 掃清單：僅對 status=missing／find_for_me（或呼叫端要求 force_all）回報仍空／仍示範。
 * have 且已有真實內容 → 不列 gap；have 但草稿仍 demo → 仍列 demo gap（對帳誠實）。
 */
function scanGaps(checklistResult, draft, options = {}) {
  const forceAll = options.force_all === true;
  const items = (checklistResult && checklistResult.items) || [];
  const gaps = [];
  for (const item of items) {
    if (!forceAll && item.status === "have") {
      const assessed = assessChecklistItem(item, draft);
      if (assessed.gap_kind === "demo") {
        gaps.push({
          item_id: item.id,
          gap_kind: "demo",
          status: item.status,
          required: !!(itemSpec(item.id) || {}).required,
          evidence: assessed.evidence,
          ask_hint: "示範內容尚未改寫，請改成你的真實資料"
        });
      }
      continue;
    }
    if (!forceAll && item.status !== "missing" && item.status !== "find_for_me") continue;
    const assessed = assessChecklistItem(item, draft);
    if (!assessed.gap_kind) continue;
    gaps.push({
      item_id: item.id,
      gap_kind: assessed.gap_kind,
      status: item.status,
      required: !!(itemSpec(item.id) || {}).required,
      evidence: assessed.evidence,
      ask_hint: assessed.gap_kind === "demo"
        ? "仍是示範，可改寫或貼連結讓我窄回補"
        : (item.status === "find_for_me" ? "請幫我找：還沒補上" : "待補")
    });
  }
  return gaps;
}

/**
 * 判斷是否為 S3 runOpeningPrefill／resolveItemPrefill 批次結果（含 items map）。
 */
function isPrefillResult(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (!value.items || typeof value.items !== "object" || Array.isArray(value.items)) return false;
  // Explicit S3 markers always win.
  if (value.schema_version != null || value.find_for_me_policy != null || value.network_calls != null) return true;
  // Soft accept only when a row looks like S3 prefill (item_id + prefill status), not S2 checklist statuses.
  const PREFILL_STATUSES = new Set(["filled", "pending", "candidates_only", "kept"]);
  for (const sample of Object.values(value.items)) {
    if (!sample || typeof sample !== "object") continue;
    const st = sample.status;
    if (sample.item_id != null && PREFILL_STATUSES.has(st)) return true;
    if (PREFILL_STATUSES.has(st) && (sample.provenance != null || Array.isArray(sample.candidates) || sample.value !== undefined)) {
      return true;
    }
  }
  return false;
}

/**
 * 從 S3 prefill result 抽出 fill-gap 用的 candidates map。
 * - status=filled → { value, provenance, … }
 * - status=candidates_only（或列有 candidates）→ { candidates, search_adoption, … }；不帶可自動寫入的 value
 * - pending／kept／空 → 略過（無候選）
 * 不改 S3；僅適配形狀。
 */
function extractCandidatesFromPrefill(prefillResult) {
  const out = Object.create(null);
  if (!prefillResult || typeof prefillResult !== "object") return out;
  const items = prefillResult.items;
  if (!items || typeof items !== "object") return out;
  for (const key of Object.keys(items)) {
    const row = items[key];
    if (!row || typeof row !== "object") continue;
    const itemId = checklist.resolveItemId(row.item_id || key) || row.item_id || key;
    const status = row.status;
    if (status === "kept" || status === "pending") continue;
    if (status === "candidates_only" || row.search_adoption === "candidates_only") {
      const list = Array.isArray(row.candidates) ? row.candidates.filter(Boolean) : [];
      if (!list.length) continue;
      out[itemId] = {
        candidates: list,
        provenance: row.provenance,
        search_adoption: "candidates_only",
        source_status: "candidates_only",
        reason: row.reason
      };
      continue;
    }
    if (status === "filled") {
      if (row.value == null && !(Array.isArray(row.candidates) && row.candidates.length)) continue;
      // NO_AUTO_WRITE：即使 S3 誤帶 value，仍只暴露為候選（防自動寫入頭像／主連結）
      if (checklist.NO_AUTO_WRITE_IDS.includes(itemId)) {
        const list = Array.isArray(row.candidates) && row.candidates.length
          ? row.candidates.filter(Boolean)
          : [{ label: "prefill", value: row.value, source: row.provenance && row.provenance.source, fetched_at: row.provenance && row.provenance.fetched_at }];
        out[itemId] = {
          candidates: list,
          provenance: row.provenance,
          search_adoption: "candidates_only",
          source_status: "candidates_only",
          reason: row.reason || "no_auto_write"
        };
        continue;
      }
      out[itemId] = {
        value: row.value,
        provenance: row.provenance,
        source_status: "filled",
        reason: row.reason
      };
      // S3 v3.109 contract: bio value is a plain string + optional row.value_format (plain|markdown).
      if (TEXT_FORMATS.includes(row.value_format)) out[itemId].value_format = row.value_format;
      // S3 905f3f2a: bio value is single-line; row.value_text (optional) is the multi-line block text. Absent on older S3.
      if (itemId === "bio" && typeof row.value_text === "string" && row.value_text.trim()) out[itemId].value_text = row.value_text;
      continue;
    }
    // Soft: bare candidates array without status
    if (Array.isArray(row.candidates) && row.candidates.length) {
      out[itemId] = {
        candidates: row.candidates.filter(Boolean),
        provenance: row.provenance,
        search_adoption: row.search_adoption || "candidates_only",
        source_status: status || "candidates_only",
        reason: row.reason
      };
    }
  }
  return out;
}

/**
 * 合併顯式 candidates 與 S3 prefill result。
 * 顯式 map 覆寫同 id；若 candidates 本身是 prefill result 則先抽出。
 */
function resolveCandidatesInput(candidates, prefillResult) {
  const map = Object.create(null);
  if (prefillResult) Object.assign(map, extractCandidatesFromPrefill(prefillResult));
  if (candidates == null) return map;
  if (isPrefillResult(candidates)) {
    Object.assign(map, extractCandidatesFromPrefill(candidates));
    return map;
  }
  if (typeof candidates === "object" && !Array.isArray(candidates)) {
    Object.assign(map, candidates);
  }
  return map;
}

function isNoAutoWriteItem(itemId) {
  const id = checklist.resolveItemId(itemId) || itemId;
  return checklist.NO_AUTO_WRITE_IDS.includes(id);
}

function candidateImpliesCandidatesOnly(cand, itemId) {
  if (!cand || typeof cand !== "object") return false;
  if (cand.search_adoption === "candidates_only") return true;
  if (cand.source_status === "candidates_only") return true;
  if (Array.isArray(cand.candidates) && cand.candidates.length && cand.value == null && cand.href == null) return true;
  if (isNoAutoWriteItem(itemId)) return true;
  return false;
}

/**
 * 有候選則 fill；無則 mark_pending；必要時最多 ask_one 一句（不擋編輯／發布）。
 * candidates: { [item_id]: { value?, candidates?, provenance?, … } } 或 S3 prefill result。
 * NO_AUTO_WRITE／candidates_only → auto_adopt 恆 false，並標 search_adoption=candidates_only。
 */

/** 候選是否有可用內容（空字串 value／無 candidates／無 href → 不可用，改走 pending）。 */
function candidateIsUsable(cand) {
  if (!cand || typeof cand !== "object") return false;
  if (Array.isArray(cand.candidates) && cand.candidates.filter(Boolean).length) return true;
  if (cand.href != null && String(cand.href).trim() !== "") return true;
  if (cand.value != null && String(cand.value).trim() !== "") return true;
  return false;
}

function proposeActions(gaps, candidates = {}, options = {}) {
  const resolved = resolveCandidatesInput(candidates, options.prefill || options.prefill_result);
  const actions = [];
  const asks = [];
  const maxAskRaw = options.max_ask == null ? 1 : Number(options.max_ask);
  const maxAsk = Number.isFinite(maxAskRaw) && maxAskRaw > 0 ? Math.floor(maxAskRaw) : 1;
  for (const gap of gaps) {
    const cand = resolved[gap.item_id];
    if (cand && typeof cand === "object" && candidateIsUsable(cand)) {
      const only = candidateImpliesCandidatesOnly(cand, gap.item_id);
      const action = {
        type: "fill",
        item_id: gap.item_id,
        gap_kind: gap.gap_kind,
        candidate: cand,
        // 搜尋／find_for_me／NO_AUTO_WRITE 不自動採用；發布前仍須人確認
        auto_adopt: false
      };
      if (only) action.search_adoption = "candidates_only";
      actions.push(action);
      continue;
    }
    actions.push({
      type: "mark_pending",
      item_id: gap.item_id,
      gap_kind: gap.gap_kind,
      label: "待補"
    });
    if (asks.length < maxAsk) {
      const label = (itemSpec(gap.item_id) || {}).label || gap.item_id;
      asks.push({
        type: "ask_one",
        item_id: gap.item_id,
        question: gap.required
          ? ("還缺「" + label + "」，要貼連結還是自己打？")
          : ("「" + label + "」還是示範／空的，要補嗎？")
      });
    }
  }
  for (const ask of asks) actions.push(ask);
  return actions;
}

/** 新貼連結 → 只動連結／社群相關清單項（窄範圍）。S2 ids。 */
const NARROW_LINK_ITEM_IDS = Object.freeze([
  "link_or_social",
  "cta_links",
  "socials",
  "video_or_gallery",
  "form_or_product"
]);

function narrowRefillFromLink(url, checklistInput, draft, options = {}) {
  const href = String(url || "").trim();
  const errors = [];
  if (!/^https:\/\//i.test(href) || href.length > 2048) {
    errors.push("fill_gap_link_https_only");
    return {
      ok: false,
      errors,
      scope: NARROW_LINK_ITEM_IDS.slice(),
      gaps: [],
      actions: [],
      blocks_editor: true,
      blocks_publish: true,
      allows_editor: true,
      allows_publish: true,
      waiting: false
    };
  }
  const normalized = checklistInput && Array.isArray(checklistInput.items)
    ? normalizeOpeningChecklist(checklistInput)
    : normalizeOpeningChecklist(checklistInput || {});
  const scopedItems = {
    ok: normalized.ok,
    errors: normalized.errors || [],
    items: (normalized.items || []).filter((row) => NARROW_LINK_ITEM_IDS.includes(row.id))
  };
  // 窄回補：把相關項視作 find_for_me 以便掃出仍空／仍示範
  scopedItems.items = scopedItems.items.map((row) => Object.assign({}, row, {
    status: row.status === "have" ? "have" : "find_for_me"
  }));
  const gaps = scanGaps(scopedItems, draft, { force_all: false });
  // 優先吃 S3 prefill／顯式 candidates；其餘 gap 用使用者新貼連結做窄回補候選
  const fromS3 = resolveCandidatesInput(options.candidates, options.prefill || options.prefill_result);
  const candidates = {};
  for (const gap of gaps) {
    if (fromS3[gap.item_id]) {
      candidates[gap.item_id] = fromS3[gap.item_id];
    } else {
      candidates[gap.item_id] = {
        href,
        provenance: {
          source: "user_link",
          url: href,
          fetched_at: options.now || new Date().toISOString(),
          ttl_days: null,
          via: undefined
        },
        note: "narrow_refill_from_user_link"
      };
    }
  }
  const actions = proposeActions(gaps, candidates, options).map((a) => {
    if (a.type === "fill") return Object.assign({}, a, { type: "narrow_refill" });
    return a;
  });
  return {
    ok: errors.length === 0,
    errors,
    scope: NARROW_LINK_ITEM_IDS.slice(),
    gaps,
    actions,
    blocks_editor: true,
    blocks_publish: true,
    allows_editor: true,
    allows_publish: true,
    waiting: false
  };
}

/**
 * 後段補洞對帳主入口（純函式；不發網路、不擋編輯／發布）。
 * @param {object} args
 * @param {object|array} [args.opening_checklist] - intent.opening_checklist（S2 正本）
 * @param {object|array} [args.checklist] - 同 opening_checklist
 * @param {object} [args.intent] - 含 opening_checklist 的 intent
 * @param {object} args.draft - { profile, blocks, appearance }
 * @param {object} [args.candidates] - S3 預填輸出候選（可呼叫其輸出，不重做授權閘）
 * @param {object} [args.options]
 */
function reconcilePass(args = {}) {
  const raw = args.opening_checklist != null ? args.opening_checklist
    : args.checklist != null ? args.checklist
    : args.intent != null ? args.intent
    : (args.items != null ? { items: args.items } : {});
  const normalized = normalizeOpeningChecklist(raw);
  const draft = args.draft || {};
  const options = args.options || {};
  const gaps = scanGaps(normalized, draft, options);
  const prefill = args.prefill != null ? args.prefill
    : args.prefill_result != null ? args.prefill_result
    : options.prefill != null ? options.prefill
    : options.prefill_result;
  const candidates = resolveCandidatesInput(args.candidates, prefill);
  const actions = proposeActions(gaps, candidates, options);
  return {
    ok: normalized.ok,
    errors: normalized.errors || [],
    checklist_items: normalized.items,
    gaps,
    actions,
    gap_count: gaps.length,
    demo_gap_count: gaps.filter((g) => g.gap_kind === "demo").length,
    empty_gap_count: gaps.filter((g) => g.gap_kind === "empty").length,
    // 不當主等待、不擋編輯／確認發布（正極性：true＝允許繼續）
    blocks_editor: true,
    blocks_publish: true,
    allows_editor: true,
    allows_publish: true,
    waiting: false,
    find_for_me_policy: checklist.FIND_FOR_ME_POLICY,
    no_auto_write_ids: checklist.NO_AUTO_WRITE_IDS.slice()
  };
}


/**
 * 解析人類要採用的單一候選（value／href／url／candidates[index]）。
 * 空／空白 → 不可用（對齊 M9）。
 * @returns {{ ok, value?, href?, label?, provenance?, candidate?, errors }}
 */
function resolveAdoptCandidate(args = {}) {
  const errors = [];
  const itemId = checklist.resolveItemId(args.item_id) || args.item_id;
  if (!itemId || !checklist.ITEM_DEF_BY_ID[itemId]) {
    errors.push("fill_gap_adopt_item_unknown");
    return { ok: false, errors, item_id: itemId || null };
  }
  let cand = args.candidate != null && typeof args.candidate === "object" ? args.candidate : null;
  const bag = args.candidates && typeof args.candidates === "object" ? args.candidates : null;
  const bagRow = bag && (bag[itemId] || bag[args.item_id]) || null;
  // Form adopt posts candidate_value explicitly; keep the S3 value_format of the same bag row (bio text format).
  if (cand && bagRow && cand.value_format == null && TEXT_FORMATS.includes(bagRow.value_format) && bagRow.value === cand.value) {
    cand = Object.assign({}, cand, { value_format: bagRow.value_format });
  }
  // 刀02: same for S3 row.value_text (multi-line block text of the same single-line bio).
  if (cand && bagRow && cand.value_text == null && typeof bagRow.value_text === "string" && bagRow.value === cand.value) {
    cand = Object.assign({}, cand, { value_text: bagRow.value_text });
  }
  if (!cand && bagRow) {
    if (Array.isArray(bagRow.candidates) && bagRow.candidates.length) {
      const idxRaw = args.candidate_index != null ? Number(args.candidate_index) : 0;
      const idx = Number.isFinite(idxRaw) && idxRaw >= 0 ? Math.floor(idxRaw) : 0;
      if (idx >= bagRow.candidates.length) {
        errors.push("fill_gap_adopt_candidate_index");
        return { ok: false, errors, item_id: itemId };
      }
      cand = bagRow.candidates[idx];
    } else {
      cand = bagRow;
    }
  }
  if (!cand && (args.value != null || args.href != null || args.url != null)) {
    cand = {
      value: args.value,
      href: args.href,
      url: args.url,
      label: args.label,
      provenance: args.provenance
    };
  }
  if (!cand || typeof cand !== "object") {
    errors.push("fill_gap_adopt_candidate_missing");
    return { ok: false, errors, item_id: itemId };
  }
  // Flatten nested { candidates: [...] } if caller passed the bag row as candidate
  if (Array.isArray(cand.candidates) && cand.candidates.length && cand.value == null && cand.href == null && cand.url == null) {
    const idxRaw = args.candidate_index != null ? Number(args.candidate_index) : 0;
    const idx = Number.isFinite(idxRaw) && idxRaw >= 0 ? Math.floor(idxRaw) : 0;
    if (idx >= cand.candidates.length) {
      errors.push("fill_gap_adopt_candidate_index");
      return { ok: false, errors, item_id: itemId };
    }
    const nestedProv = cand.provenance;
    cand = cand.candidates[idx];
    if (cand && typeof cand === "object" && !cand.provenance && nestedProv) {
      cand = Object.assign({}, cand, { provenance: nestedProv });
    }
  }
  if (!cand || typeof cand !== "object") {
    errors.push("fill_gap_adopt_candidate_missing");
    return { ok: false, errors, item_id: itemId };
  }

  let href = null;
  if (cand.href != null && String(cand.href).trim() !== "") href = String(cand.href).trim();
  else if (cand.url != null && String(cand.url).trim() !== "") href = String(cand.url).trim();
  else if (typeof cand.value === "string" && /^https:\/\//i.test(cand.value.trim())) href = cand.value.trim();

  let value = cand.value !== undefined ? cand.value : null;
  if (value == null && href) value = href;
  if (typeof value === "string") value = value.trim();
  const label = cand.label != null ? String(cand.label).trim() : null;

  const usable = (value != null && String(value).trim() !== "") || (href != null && href !== "") ||
    (Array.isArray(value) && value.length > 0) || (value && typeof value === "object" && Object.keys(value).length > 0);
  if (!usable) {
    errors.push("fill_gap_adopt_empty");
    return { ok: false, errors, item_id: itemId };
  }
  if (href && !/^https:\/\//i.test(href)) {
    errors.push("fill_gap_adopt_https_only");
    return { ok: false, errors, item_id: itemId };
  }
  // Same href rule applyDraftOps enforces on CTA action.href / link href (credentials, control chars, length).
  if (href && !blockContract.safeHref(href)) {
    errors.push("fill_gap_adopt_href_unsafe");
    return { ok: false, errors, item_id: itemId };
  }

  let provenance = cand.provenance && typeof cand.provenance === "object" ? Object.assign({}, cand.provenance) : null;
  if (!provenance && (cand.source || cand.fetched_at || cand.via)) {
    provenance = {
      source: cand.source || "adopt",
      url: href || null,
      fetched_at: cand.fetched_at || null,
      ttl_days: cand.ttl_days != null ? cand.ttl_days : null
    };
    if (cand.via === "search") provenance.via = "search";
  }
  if (provenance && !provenance.source) provenance.source = "adopt";
  if (provenance && href && provenance.url == null) provenance.url = href;

  return {
    ok: true,
    errors,
    item_id: itemId,
    value,
    href,
    label,
    provenance,
    candidate: cand,
    value_format: TEXT_FORMATS.includes(cand.value_format) ? cand.value_format : null,
    value_text: typeof cand.value_text === "string" ? cand.value_text : null,
    // 人類採用：即使 NO_AUTO／candidates_only 也可寫入草稿（仍非 auto_adopt）
    human_adopt: true,
    auto_adopt: false
  };
}

function cloneDraftLocal(draft) {
  if (!draft || typeof draft !== "object") return { profile: {}, blocks: [], appearance: {}, meta: {} };
  return {
    profile: draft.profile && typeof draft.profile === "object" ? JSON.parse(JSON.stringify(draft.profile)) : {},
    blocks: Array.isArray(draft.blocks) ? JSON.parse(JSON.stringify(draft.blocks)) : [],
    appearance: draft.appearance && typeof draft.appearance === "object" ? JSON.parse(JSON.stringify(draft.appearance)) : {},
    meta: draft.meta && typeof draft.meta === "object" ? JSON.parse(JSON.stringify(draft.meta)) : {}
  };
}

/** Text value of a candidate (string; legacy {body}|{text}|{bio} objects per S3 normalizeBioInput precedence). */
function adoptTextOf(value) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const key of ["body", "text", "bio"]) if (typeof value[key] === "string" && value[key].trim()) return value[key];
    return "";
  }
  return value == null ? "" : String(value);
}

/**
 * v3.109 S4 刀02: human-readable one-line preview of a candidate value for the editor adopt form (never
 * "[object Object]"). Strings pass through (S3 singleLine); arrays join their readable parts with ", "; objects use
 * the first readable field (platform / label / title / name / q|question / text / body / bio / url / href / src),
 * or their `items` list (faq/gallery shape). Returns "" when nothing is renderable ⇒ caller omits the row.
 * Pure display text: caller still escapeHtml()s it; adopt never trusts it (non-string values adopt by index).
 */
const DISPLAY_KEYS = Object.freeze(["platform", "label", "title", "name", "q", "question", "text", "body", "bio", "url", "href", "src"]);
function candidateDisplayText(value, depth = 0) {
  if (value == null) return "";
  if (typeof value === "string") return s3SingleLine(value);
  if (typeof value === "number" || typeof value === "boolean") return Number.isFinite(value) || typeof value === "boolean" ? String(value) : "";
  if (depth > 2) return "";
  if (Array.isArray(value)) {
    return value.map((entry) => candidateDisplayText(entry, depth + 1)).filter(Boolean).join(", ");
  }
  if (typeof value === "object") {
    for (const key of DISPLAY_KEYS) {
      if (typeof value[key] === "string" && s3SingleLine(value[key])) return s3SingleLine(value[key]);
    }
    if (Array.isArray(value.items)) return candidateDisplayText(value.items, depth + 1);
  }
  return "";
}

/**
 * socials → registry social_links shape, filtered exactly like registration validateSocialLinks:
 * {platform,url} only, socialIcons.has(platform), no duplicate platform, ≤ DISPLAY_ORDER.length,
 * URL passes social-intake validateSocialIntake (https, platform host allow-list, no credentials).
 * Returns { links, rejected[] }.
 */
function adoptSocialLinks(rows) {
  const links = [];
  const rejected = [];
  const seen = new Set();
  const cap = Array.isArray(socialIcons.DISPLAY_ORDER) ? socialIcons.DISPLAY_ORDER.length : 0;
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== "object") { rejected.push("shape"); continue; }
    const platform = typeof row.platform === "string" ? row.platform.trim() : "";
    const url = typeof row.url === "string" ? row.url.trim() : (typeof row.href === "string" ? row.href.trim() : "");
    if (!platform || !socialIcons.has(platform)) { rejected.push("platform_unknown:" + platform.slice(0, 32)); continue; }
    if (seen.has(platform)) { rejected.push("platform_duplicate:" + platform); continue; }
    const verdict = socialIntake.validateSocialIntake([{ platform, url, consented: true }]);
    if (!url || !verdict || verdict.ok !== true) { rejected.push("url_invalid:" + platform); continue; }
    if (links.length >= cap) { rejected.push("too_many"); continue; }
    seen.add(platform);
    // Write the social-intake normalized URL (parsed https form), not the raw input.
    const normalized = verdict.links && verdict.links[0] && verdict.links[0].url;
    links.push({ platform, url: typeof normalized === "string" && normalized ? normalized : url });
  }
  return { links, rejected };
}

function findDemoBlockOfType(blocks, type) {
  if (!Array.isArray(blocks)) return null;
  return blocks.find((b) => b && b.block_type === type && demos.isDemoBlock(b)) || null;
}

/**
 * 人類按「採用」：把單一候選寫入草稿（純函式；不發佈、不捨棄、auto_adopt 恆 false）。
 * Body 語意：item_id + candidate／candidate_index（+ 可選 candidates bag）。
 * Success → 只改 draft；回 profile_patch／ops 供既有 draft apply 路徑；呼叫端算 demo_remaining＋revision。
 * NO_AUTO_WRITE／candidates_only／FIND_FOR_ME：禁止 auto-write，但本函式＝人觸發，允許寫入。
 */
function adoptCandidateToDraft(draftInput, args = {}) {
  const resolved = resolveAdoptCandidate(args);
  if (!resolved.ok) {
    return {
      ok: false,
      errors: resolved.errors,
      draft: cloneDraftLocal(draftInput),
      profile_patch: {},
      ops: [],
      item_id: resolved.item_id,
      auto_adopt: false,
      human_adopt: true,
      applied: false
    };
  }
  const draft = cloneDraftLocal(draftInput);
  const itemId = resolved.item_id;
  const provenance = resolved.provenance
    ? Object.assign({
        source: "adopt",
        url: resolved.href || (resolved.provenance && resolved.provenance.url) || null,
        fetched_at: resolved.provenance.fetched_at || new Date().toISOString(),
        ttl_days: resolved.provenance.ttl_days != null ? resolved.provenance.ttl_days : 30
      }, resolved.provenance)
    : {
        source: "adopt",
        url: resolved.href || null,
        fetched_at: args.now || new Date().toISOString(),
        ttl_days: 30
      };
  const profilePatch = {};
  const ops = [];
  let applied = false;

  if (itemId === "display_name") {
    const name = s3DisplayName(adoptTextOf(resolved.value));   // = S3 normalizeDisplayName
    if (!name) {
      return Object.assign(resolved, {
        ok: false, errors: ["fill_gap_adopt_empty"], draft, profile_patch: {}, ops: [],
        auto_adopt: false, human_adopt: true, applied: false
      });
    }
    draft.profile.display_name = name;
    profilePatch.display_name = name;
    applied = true;
  } else if (itemId === "bio") {
    // 刀02 = S3 applyPrefillResultToDraft bio rule: profile.bio = single-line normalizeBioInput(value).text;
    //   block text = normalizeBioInput(value_text).block_text when it normalizes to the same bio, else the
    //   multi-line form of value itself (value_text absent ⇒ pre-905f3f2a shape, still works).
    const normalized = s3NormalizeBio(resolved.value);
    const bio = normalized ? normalized.text : "";
    const fromValueText = typeof resolved.value_text === "string" ? s3NormalizeBio(resolved.value_text) : null;
    const blockText = fromValueText && fromValueText.text === bio ? fromValueText.block_text : (normalized ? normalized.block_text : "");
    if (!bio) {
      return {
        ok: false, errors: ["fill_gap_adopt_empty"], draft, profile_patch: {}, ops: [],
        item_id: itemId, auto_adopt: false, human_adopt: true, applied: false
      };
    }
    draft.profile.bio = bio;
    profilePatch.bio = bio;
    // Never overwrite a REAL status_text; the oneshot scaffold demo ("示範狀態 · 請改") counts as absent — S3 905f3f2a
    //   profileFieldPresent/isDemoProfileValue rule (R1: effective profile made the AIB scaffold demo block derivation).
    const currentStatus = String(draft.profile.status_text || "").trim();
    const demoStatus = demos.ONESHOT_CREATOR_PROFILE && typeof demos.ONESHOT_CREATOR_PROFILE.status_text === "string" ? demos.ONESHOT_CREATOR_PROFILE.status_text : null;
    if (bio.length <= S3_STATUS_TEXT_MAX && !/[<>]/.test(bio) && (!currentStatus || currentStatus === demoStatus)) {   // status_text rule ⟂ <>
      draft.profile.status_text = bio;
      profilePatch.status_text = bio;
    }
    const textDemo = findDemoBlockOfType(draft.blocks, "text");
    if (textDemo) {
      // text contract exactKeys = text/format (renderer uses payload.text); never write body.
      // Format = S3 bioTextPayload rule: explicit source value_format, else keep demo markdown, else plain.
      const prevFmt = textDemo.payload && textDemo.payload.format;
      const explicitFormat = TEXT_FORMATS.includes(resolved.value_format) ? resolved.value_format : (normalized && normalized.format);
      const payload = {
        text: blockText || bio,
        format: TEXT_FORMATS.includes(explicitFormat) ? explicitFormat : (prevFmt === "markdown" ? "markdown" : "plain")
      };
      textDemo.payload = payload;
      textDemo.provenance = provenance;
      ops.push({ op: "update", block_id: textDemo.block_id, payload, provenance });
    }
    applied = true;
  } else if (itemId === "socials") {
    // Unsafe / unknown rows are dropped; if nothing survives, NO write (ok:false) — never write unsafe values.
    const rows = Array.isArray(resolved.value) && resolved.value.length
      ? resolved.value
      : (resolved.href ? [{ platform: (resolved.candidate && resolved.candidate.platform) || resolved.label || "", url: resolved.href }] : []);
    const filtered = adoptSocialLinks(rows);
    if (!filtered.links.length) {
      return {
        ok: false, errors: ["fill_gap_adopt_socials_invalid"], rejected: filtered.rejected, draft, profile_patch: {}, ops: [],
        item_id: itemId, auto_adopt: false, human_adopt: true, applied: false
      };
    }
    draft.profile.socials = filtered.links;
    profilePatch.socials = filtered.links;
    applied = true;
  } else if (itemId === "faq") {
    // v3.109 S4 刀02 R1(S4d): = S3 93bb9740 faqPayloadFromValue (array | {items}; ≤ FAQ_ITEMS_MAX kept rows;
    //   q/a = truncateText(stripTags(x) − NUL, contract max) — grapheme-safe, never a lone surrogate; tags stripped instead
    //   of a contract 422). Plus the per-entry shape S3 candidateFromValue emits for an FAQ array under find_for_me
    //   ({label:"candidate", value:{q,a}}) ⇒ one-item list, so that offered row is adoptable (was fill_gap_adopt_unmapped).
    const faqValue = resolved.value;
    const items = Array.isArray(faqValue)
      ? faqValue
      : (faqValue && typeof faqValue === "object" && Array.isArray(faqValue.items) ? faqValue.items
        : (faqValue && typeof faqValue === "object" && (typeof faqValue.q === "string" || typeof faqValue.a === "string") ? [faqValue] : null));
    if (items && items.length) {
      const out = [];
      const faqText = (text, max) => s3TruncateText(s3StripTags(text).replace(/\u0000/g, "").trim(), max).trim();
      for (const row of items) {
        if (out.length >= blockContract.FAQ_ITEMS_MAX) break;
        if (!row || typeof row !== "object" || Array.isArray(row)) continue;
        const q = typeof row.q === "string" ? faqText(row.q, blockContract.FAQ_Q_MAX) : "";
        const a = typeof row.a === "string" ? faqText(row.a, blockContract.FAQ_A_MAX) : "";
        if (q && a) out.push({ q, a });
      }
      if (out.length) {
        const payload = { open_first: true, items: out };
        const faqDemo = findDemoBlockOfType(draft.blocks, "faq");
        if (faqDemo) {
          faqDemo.payload = payload;
          faqDemo.provenance = provenance;
          ops.push({ op: "update", block_id: faqDemo.block_id, payload, provenance });
        } else {
          ops.push({ op: "create", block_type: "faq", visibility: "public", payload, provenance });
          draft.blocks.push({
            block_id: "adopt_faq_pending",
            block_type: "faq",
            visibility: "public",
            payload,
            provenance
          });
        }
        applied = true;
      }
    }
  } else if (itemId === "link_or_social" || itemId === "cta_links") {
    const href = resolved.href;
    if (!href) {
      return {
        ok: false, errors: ["fill_gap_adopt_https_only"], draft, profile_patch: {}, ops: [],
        item_id: itemId, auto_adopt: false, human_adopt: true, applied: false
      };
    }
    // S4d: grapheme-safe cut (S3 truncateText) — .slice(0, 80) could leave a lone surrogate in the block label.
    const label = s3TruncateText(resolved.label || String(resolved.value || href), 80);
    // CTA contract exactKeys = label/action/sublabel (action.href); link = label/href/rel.
    // Prefer matching demo type; fall back so adopt still clears a demo signature when only the other type exists.
    const preferCta = itemId === "cta_links";
    const ctaDemo = findDemoBlockOfType(draft.blocks, "cta");
    const linkDemo = findDemoBlockOfType(draft.blocks, "link");
    const demo = preferCta ? (ctaDemo || linkDemo) : (linkDemo || ctaDemo);
    const linkPayload = (prev) => Object.assign({}, prev || {}, {
      label,
      href,
      rel: Array.isArray((prev || {}).rel) ? (prev || {}).rel : ["noopener"]
    });
    const ctaPayload = (prev) => {
      // URL adopt ⇒ open_url + target_id null (contract: open_url forbids target_id).
      return {
        label,
        action: { type: "open_url", href, target_id: null },
        sublabel: prev && Object.prototype.hasOwnProperty.call(prev, "sublabel") ? prev.sublabel : null
      };
    };
    if (demo) {
      const asCta = demo.block_type === "cta";
      const payload = asCta ? ctaPayload(demo.payload) : linkPayload(demo.payload);
      demo.payload = payload;
      demo.provenance = provenance;
      ops.push({ op: "update", block_id: demo.block_id, payload, provenance });
    } else {
      const asCta = preferCta;
      const payload = asCta ? ctaPayload(null) : linkPayload(null);
      const block_type = asCta ? "cta" : "link";
      ops.push({ op: "create", block_type, visibility: "public", payload, provenance });
      draft.blocks.push({
        block_id: "adopt_link_pending",
        block_type,
        visibility: "public",
        payload,
        provenance
      });
    }
    applied = true;
  } else if (itemId === "avatar_cover") {
    // Human adopt of URL candidate: cannot mint asset_id here — stash on meta + optional profile note.
    // If candidate carries avatar_asset_id / cover_asset_id, write those into profile.
    const assetId = resolved.candidate && (
      resolved.candidate.avatar_asset_id || resolved.candidate.asset_id || resolved.candidate.cover_asset_id
    );
    if (assetId && typeof assetId === "string") {
      const field = (resolved.candidate.cover_asset_id && !resolved.candidate.avatar_asset_id) ? "cover_asset_id" : "avatar_asset_id";
      draft.profile[field] = assetId;
      profilePatch[field] = assetId;
      applied = true;
    } else if (resolved.href) {
      draft.meta.prefill_adopted = Object.assign({}, draft.meta.prefill_adopted || {}, {
        [itemId]: {
          url: resolved.href,
          label: resolved.label || null,
          provenance,
          adopted_at: args.now || new Date().toISOString(),
          needs_asset_upload: true
        }
      });
      // Still "applied" as human confirmation of candidate (draft-only meta); publish gate unchanged until asset lands.
      applied = true;
    }
  } else if (itemId === "preferred_theme") {
    const theme = typeof resolved.value === "string" ? resolved.value.trim()
      : (resolved.value && typeof resolved.value === "object" ? resolved.value.theme_id : null);
    if (theme && typeof theme === "string") {
      draft.appearance = Object.assign({}, draft.appearance, { theme_id: theme });
      applied = true;
    }
  } else {
    // Generic: if https href, treat like link adopt into first demo link
    if (resolved.href) {
      const demo = findDemoBlockOfType(draft.blocks, "link");
      if (demo) {
        const label = s3TruncateText(resolved.label || String(resolved.value || resolved.href), 80);
        const payload = Object.assign({}, demo.payload || {}, { label, href: resolved.href });
        demo.payload = payload;
        demo.provenance = provenance;
        ops.push({ op: "update", block_id: demo.block_id, payload, provenance });
        applied = true;
      }
    } else if (typeof resolved.value === "string" && resolved.value.trim()) {
      draft.meta.prefill_adopted = Object.assign({}, draft.meta.prefill_adopted || {}, {
        [itemId]: { value: resolved.value.trim(), provenance, adopted_at: args.now || new Date().toISOString() }
      });
      applied = true;
    }
  }

  if (!applied) {
    return {
      ok: false,
      errors: ["fill_gap_adopt_unmapped"],
      draft,
      profile_patch: {},
      ops: [],
      item_id: itemId,
      auto_adopt: false,
      human_adopt: true,
      applied: false
    };
  }

  // Drop adopted slot from candidates bag on meta (if present)
  if (draft.meta.prefill_candidates && draft.meta.prefill_candidates[itemId]) {
    const next = Object.assign({}, draft.meta.prefill_candidates);
    delete next[itemId];
    draft.meta.prefill_candidates = next;
  }
  draft.meta.prefill_adopted = Object.assign({}, draft.meta.prefill_adopted || {}, {
    [itemId]: Object.assign({}, draft.meta.prefill_adopted && draft.meta.prefill_adopted[itemId] || {}, {
      provenance,
      adopted_at: args.now || new Date().toISOString(),
      human_adopt: true,
      auto_adopt: false
    })
  });

  const demoBefore = demos.demoCount(Array.isArray(draftInput && draftInput.blocks) ? draftInput.blocks : []);
  const demoAfter = demos.demoCount(draft.blocks);

  return {
    ok: true,
    errors: [],
    draft,
    profile_patch: profilePatch,
    ops,
    item_id: itemId,
    auto_adopt: false,
    human_adopt: true,
    applied: true,
    search_adoption: isNoAutoWriteItem(itemId) ? "human_adopted" : "human_adopted",
    demo_remaining_before: demoBefore,
    demo_remaining: demoAfter,
    provenance
  };
}


/**
 * v3.109 S4 wire：後段補洞 pass（live oneshot／server／AIB 接線用的穩定、可序列化記錄）。
 * 契約：
 *  - ok 閘：先看 reconcilePass().ok；ok:false ⇒ gaps／actions／fills／pending／asks／candidates 全空（下游零動作零寫入）。
 *  - 永不 auto_adopt：每個 fill 動作強制 auto_adopt:false；本函式不寫草稿（writes 恆 []）。
 *  - 缺件 ⇒ mark_pending（pending[]）＋ ask_one（asks[]，至多 max_ask；預設 1、上限 MAX_ASK_CAP）。
 *  - 採用仍須人按：adopt.route＝POST /manage/editor/fill-gap/adopt（CSRF＋expected_draft_revision CAS）。
 *  - 不擋編輯／確認發布、不當主等待點（allows_*＝true，waiting:false）。
 *  - 候選來源：S3 apply_plan.candidates（NO_AUTO／candidates_only 殘留）＋ prefill result 抽取；顯式 apply_plan 覆寫同 id。
 */
const FILL_GAP_PASS_SCHEMA = "ez2.page.fill_gap_pass.v1";
const FILL_GAP_ADOPT_ROUTE = "POST /manage/editor/fill-gap/adopt";
const FILL_GAP_ADOPT_REQUIRED_FIELDS = Object.freeze(["csrf_token", "draft_revision", "item_id"]);
const MAX_ASK_CAP = 3;
const PASS_CANDIDATES_PER_ITEM = 8;

function clampMaxAsk(raw) {
  const n = raw == null ? 1 : Number(raw);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(MAX_ASK_CAP, Math.floor(n));
}

function jsonSafe(value) {
  if (value === undefined) return undefined;
  try { return JSON.parse(JSON.stringify(value)); } catch (_) { return undefined; }
}

function passCandidate(cand) {
  const safe = jsonSafe(cand);
  if (!safe || typeof safe !== "object") return null;
  if (Array.isArray(safe.candidates)) safe.candidates = safe.candidates.filter(Boolean).slice(0, PASS_CANDIDATES_PER_ITEM);
  return safe;
}

function backHalfFillGapPass(args = {}) {
  const options = Object.assign({}, args.options || {});
  const maxAsk = clampMaxAsk(args.max_ask != null ? args.max_ask : options.max_ask);
  options.max_ask = maxAsk;
  const applyPlan = args.apply_plan && typeof args.apply_plan === "object" ? args.apply_plan : null;
  const explicit = Object.assign({},
    applyPlan && applyPlan.candidates && typeof applyPlan.candidates === "object" ? applyPlan.candidates : {},
    args.candidates && typeof args.candidates === "object" && !Array.isArray(args.candidates) && !isPrefillResult(args.candidates) ? args.candidates : {});
  const base = {
    schema: FILL_GAP_PASS_SCHEMA,
    ok: false,
    errors: [],
    computed_at: args.now || new Date().toISOString(),
    draft_revision_id: args.draft_revision_id != null ? String(args.draft_revision_id) : null,
    max_ask: maxAsk,
    auto_adopt: false,
    requires_human_adopt: true,
    adopt: { route: FILL_GAP_ADOPT_ROUTE, required_fields: FILL_GAP_ADOPT_REQUIRED_FIELDS.slice(), cas: "expected_draft_revision", draft_only: true },
    writes: [],
    blocks_editor: true,
    blocks_publish: true,
    allows_editor: true,
    allows_publish: true,
    waiting: false,
    gaps: [],
    actions: [],
    fills: [],
    pending: [],
    asks: [],
    candidates: {},
    gap_count: 0,
    demo_gap_count: 0,
    empty_gap_count: 0,
    find_for_me_policy: jsonSafe(checklist.FIND_FOR_ME_POLICY),
    no_auto_write_ids: checklist.NO_AUTO_WRITE_IDS.slice()
  };
  let rec;
  try {
    rec = reconcilePass({
      opening_checklist: args.opening_checklist != null ? args.opening_checklist
        : args.checklist != null ? args.checklist
        : args.intent != null ? args.intent : {},
      draft: args.draft || {},
      candidates: Object.keys(explicit).length ? explicit : undefined,
      prefill_result: args.prefill_result != null ? args.prefill_result : args.prefill,
      options
    });
  } catch (error) {
    base.errors = ["fill_gap_pass_failed:" + String(error && error.message || error).slice(0, 120)];
    return base;
  }
  base.errors = (rec.errors || []).slice();
  // ok 閘（M5）：ok:false 一律不往下游給任何動作／候選。
  if (rec.ok !== true) {
    if (!base.errors.length) base.errors.push("fill_gap_checklist_not_ok");
    return base;
  }
  base.ok = true;
  base.gaps = jsonSafe(rec.gaps) || [];
  base.gap_count = rec.gap_count;
  base.demo_gap_count = rec.demo_gap_count;
  base.empty_gap_count = rec.empty_gap_count;
  let askCount = 0;
  for (const action of rec.actions || []) {
    if (!action || typeof action !== "object") continue;
    if (action.type === "fill") {
      const candidate = passCandidate(action.candidate);
      if (!candidate) continue;
      const row = { type: "fill", item_id: action.item_id, gap_kind: action.gap_kind, candidate, auto_adopt: false, requires_human_adopt: true };
      if (action.search_adoption) row.search_adoption = action.search_adoption;
      base.actions.push(row);
      base.fills.push(row);
      base.candidates[action.item_id] = candidate;
    } else if (action.type === "mark_pending") {
      const row = { type: "mark_pending", item_id: action.item_id, gap_kind: action.gap_kind, label: action.label || "待補" };
      base.actions.push(row);
      base.pending.push(row);
    } else if (action.type === "ask_one") {
      if (askCount >= maxAsk) continue;
      askCount += 1;
      const row = { type: "ask_one", item_id: action.item_id, question: action.question };
      base.actions.push(row);
      base.asks.push(row);
    }
  }
  return base;
}


module.exports = {
  GAP_KINDS,
  ACTION_TYPES,
  ITEM_MAPS_TO,
  NARROW_LINK_ITEM_IDS,
  FIND_FOR_ME_POLICY: checklist.FIND_FOR_ME_POLICY,
  NO_AUTO_WRITE_IDS: checklist.NO_AUTO_WRITE_IDS,
  isStillDemo,
  contentKindOfBlock,
  itemSpec,
  normalizeOpeningChecklist,
  assessChecklistItem,
  scanGaps,
  isPrefillResult,
  extractCandidatesFromPrefill,
  resolveCandidatesInput,
  proposeActions,
  candidateIsUsable,
  narrowRefillFromLink,
  reconcilePass,
  resolveAdoptCandidate,
  adoptCandidateToDraft,
  adoptSocialLinks,
  candidateDisplayText,
  // v3.109 S4 刀02: S3 93bb9740 text rules mirrored (see S3_TEXT comment) — exported for smoke parity checks only.
  S3_TEXT: Object.freeze({ stripTags: s3StripTags, singleLine: s3SingleLine, multiLine: s3MultiLine, truncateText: s3TruncateText, normalizeBioInput: s3NormalizeBio, normalizeDisplayName: s3DisplayName }),
  // v3.109 S4 wire
  FILL_GAP_PASS_SCHEMA,
  FILL_GAP_ADOPT_ROUTE,
  MAX_ASK_CAP,
  backHalfFillGapPass
};
