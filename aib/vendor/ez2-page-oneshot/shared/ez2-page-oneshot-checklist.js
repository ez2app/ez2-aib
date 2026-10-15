"use strict";
// S2: opening data-prep checklist for provider-agnostic oneshot (outline §4.1).
// Intent/contract only — no network crawl (S3). Empty→DEMO scaffold is S1.
// find_for_me emits candidates for human adopt; never auto-writes avatar / primary links.
// v3.108: thicken ASK + talk-track copy (授權勾選／補洞口語／採用候選／示範不可發佈／示範·真實·爬取預填／provenance).
// v3.109 S2: + demo vs free cap talk track (ASK_DEMO_VS_FREE_CAP_COPY_*).
// v3.109 S2: talk tracks aligned to real runtime fields (countTowardBlockLimit / block_limit / block_count /
//   demo_remaining / contentKindOfBlock / block.provenance); demoCapStatus() = derived remaining; runtime_fields map.
//   talk_tracks are blueprint meta / checklist summary only (僅在 meta): server.js / page-api.js do not read them.
// v3.109 S2 knife 02: copy points AI to page-api GET /draft billable_block_count / block_limit / blocks_remaining
//   (page-api status.draft_block_count / write-response draft.block_count include demos; registration editorState().block_count excludes demos).

const VERSION = "2026-09-27.ez2-page-oneshot-checklist.v1";

const STATUSES = Object.freeze(["have", "missing", "find_for_me"]);
const STATUS_SET = new Set(STATUSES);
const STATUS_LABELS_ZH = Object.freeze({
  have: "我已有",
  missing: "我沒有",
  find_for_me: "請幫我找"
});

// find_for_me never auto-writes these (outline §11). Candidates only; human adopts.
const FIND_FOR_ME_POLICY = Object.freeze({
  auto_write_avatar: false,
  auto_write_cover: false,
  auto_write_primary_link: false,
  auto_write_cta_links: false,
  note: "find_for_me lists candidates only; human must adopt before any write. No network in S2."
});

const NO_AUTO_WRITE_IDS = Object.freeze(["avatar_cover", "link_or_social", "cta_links"]);

const ITEM_DEFS = Object.freeze([
  Object.freeze({ id: "display_name", required: true, label_zh: "顯示名", label_en: "display name", aliases: Object.freeze([]) }),
  Object.freeze({ id: "bio", required: true, label_zh: "一句自介", label_en: "bio / tagline", aliases: Object.freeze(["tagline"]) }),
  Object.freeze({ id: "link_or_social", required: true, label_zh: "至少 1 個可點連結或社群", label_en: "≥1 link or social", aliases: Object.freeze([]) }),
  Object.freeze({ id: "avatar_cover", required: false, label_zh: "頭像／封面", label_en: "avatar / cover", aliases: Object.freeze(["avatar", "cover"]) }),
  Object.freeze({ id: "cta_links", required: false, label_zh: "2–3 顆主行動連結", label_en: "2–3 CTA links", aliases: Object.freeze([]) }),
  Object.freeze({ id: "socials", required: false, label_zh: "社群網址", label_en: "social URLs", aliases: Object.freeze([]) }),
  Object.freeze({ id: "faq", required: false, label_zh: "FAQ 兩則", label_en: "FAQ ×2", aliases: Object.freeze([]) }),
  Object.freeze({ id: "video_or_gallery", required: false, label_zh: "影片或圖集", label_en: "video or gallery", aliases: Object.freeze(["video", "gallery"]) }),
  Object.freeze({ id: "form_or_product", required: false, label_zh: "表單／電子報或單品", label_en: "form / product", aliases: Object.freeze(["form", "product", "shop"]) }),
  Object.freeze({ id: "preferred_theme", required: false, label_zh: "偏好套版（八套名或暗／亮）", label_en: "preferred theme", aliases: Object.freeze(["theme", "theme_id"]) })
]);

const ITEM_IDS = Object.freeze(ITEM_DEFS.map((d) => d.id));
const ITEM_DEF_BY_ID = Object.freeze(Object.fromEntries(ITEM_DEFS.map((d) => [d.id, d])));
const REQUIRED_ITEM_IDS = Object.freeze(ITEM_DEFS.filter((d) => d.required).map((d) => d.id));
const OPTIONAL_ITEM_IDS = Object.freeze(ITEM_DEFS.filter((d) => !d.required).map((d) => d.id));

const ALIAS_TO_ID = Object.freeze((() => {
  const map = Object.create(null);
  for (const def of ITEM_DEFS) {
    map[def.id] = def.id;
    for (const alias of def.aliases) map[alias] = def.id;
  }
  return map;
})());

const CANDIDATE_KEYS = Object.freeze(["label", "url", "source", "via", "fetched_at"]);
const PROVENANCE_SOURCE_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const HTTPS_RE = /^https:\/\//i;
const NOTE_MAX = 240;
const CANDIDATES_MAX = 8;

function plain(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function resolveItemId(raw) {
  if (typeof raw !== "string") return null;
  const key = raw.trim();
  return ALIAS_TO_ID[key] || null;
}

function findForMeMayAutoWrite(itemId) {
  const id = resolveItemId(itemId) || itemId;
  if (NO_AUTO_WRITE_IDS.includes(id)) return false;
  // Policy: never auto-write from find_for_me in S2 — even for soft fields.
  return false;
}

function normalizeStatus(raw) {
  if (raw == null || raw === "") return "missing";
  if (typeof raw !== "string") return null;
  const s = raw.trim().toLowerCase();
  if (STATUS_SET.has(s)) return s;
  // zh aliases
  if (s === "我已有" || s === "已有" || s === "have_it") return "have";
  if (s === "我沒有" || s === "沒有" || s === "none") return "missing";
  if (s === "請幫我找" || s === "幫我找" || s === "find" || s === "search") return "find_for_me";
  return null;
}

function normalizeNote(raw) {
  if (raw == null || raw === "") return undefined;
  if (typeof raw !== "string") return null;
  const note = raw.replace(/[<>\u0000-\u001f]/g, "").trim().slice(0, NOTE_MAX);
  return note || undefined;
}

function normalizeCandidate(raw, errors, path) {
  if (!plain(raw)) {
    errors.push(path + "_invalid");
    return null;
  }
  const out = {};
  if (raw.label != null) {
    if (typeof raw.label !== "string") { errors.push(path + "_label_invalid"); return null; }
    const label = raw.label.replace(/[<>]/g, "").trim().slice(0, 120);
    if (label) out.label = label;
  }
  if (raw.url != null) {
    if (typeof raw.url !== "string" || !HTTPS_RE.test(raw.url) || raw.url.length > 2048) {
      errors.push(path + "_url_invalid");
      return null;
    }
    out.url = raw.url;
  } else {
    out.url = null;
  }
  if (raw.source != null) {
    if (typeof raw.source !== "string" || !PROVENANCE_SOURCE_RE.test(raw.source)) {
      errors.push(path + "_source_invalid");
      return null;
    }
    out.source = raw.source;
  }
  if (raw.via != null) {
    if (raw.via !== "search") { errors.push(path + "_via_invalid"); return null; }
    out.via = "search";
  }
  if (raw.fetched_at != null) {
    if (typeof raw.fetched_at !== "string" || !Number.isFinite(Date.parse(raw.fetched_at))) {
      errors.push(path + "_fetched_at_invalid");
      return null;
    }
    out.fetched_at = raw.fetched_at;
  }
  // Reject unknown keys (keep shape tight for S3 reconcile).
  for (const key of Object.keys(raw)) {
    if (!CANDIDATE_KEYS.includes(key)) {
      errors.push(path + "_unknown_key:" + key);
      return null;
    }
  }
  return out;
}

function normalizeItemEntry(id, raw, errors) {
  const def = ITEM_DEF_BY_ID[id];
  let status;
  let note;
  let candidates;
  if (raw == null) {
    status = "missing";
  } else if (typeof raw === "string") {
    status = normalizeStatus(raw);
    if (!status) errors.push("item_" + id + "_status_invalid");
  } else if (plain(raw)) {
    status = normalizeStatus(raw.status);
    if (!status) errors.push("item_" + id + "_status_invalid");
    const n = normalizeNote(raw.note);
    if (n === null) errors.push("item_" + id + "_note_invalid");
    else if (n !== undefined) note = n;
    if (raw.candidates !== undefined) {
      if (!Array.isArray(raw.candidates)) {
        errors.push("item_" + id + "_candidates_invalid");
      } else if (raw.candidates.length > CANDIDATES_MAX) {
        errors.push("item_" + id + "_candidates_too_many");
      } else {
        candidates = [];
        raw.candidates.forEach((c, i) => {
          const nc = normalizeCandidate(c, errors, "item_" + id + "_candidate_" + i);
          if (nc) candidates.push(nc);
        });
      }
    }
    const allowedItemKeys = new Set(["status", "note", "candidates", "id", "item", "required", "pending", "auto_write_allowed"]);
    for (const key of Object.keys(raw)) {
      if (!allowedItemKeys.has(key)) {
        errors.push("item_" + id + "_unknown_key:" + key);
      }
    }
  } else {
    errors.push("item_" + id + "_invalid");
    status = "missing";
  }
  if (!status) status = "missing";

  // Candidates only make sense for find_for_me; strip otherwise (non-fatal).
  if (status !== "find_for_me") candidates = undefined;
  else if (!candidates) candidates = [];

  const pending = def.required && status !== "have";
  const entry = { id, status, required: def.required, pending: Boolean(pending) };
  if (note !== undefined) entry.note = note;
  if (candidates !== undefined) entry.candidates = candidates;
  entry.auto_write_allowed = findForMeMayAutoWrite(id);
  return entry;
}

function coerceItemsMap(input, errors) {
  const map = Object.create(null);
  if (input == null) return map;
  if (Array.isArray(input)) {
    input.forEach((row, i) => {
      if (!plain(row) || (row.id == null && row.item == null)) {
        errors.push("items_" + i + "_invalid");
        return;
      }
      const id = resolveItemId(row.id != null ? row.id : row.item);
      if (!id) {
        errors.push("items_" + i + "_unknown_id");
        return;
      }
      if (map[id]) errors.push("items_duplicate:" + id);
      map[id] = row;
    });
    return map;
  }
  if (!plain(input)) {
    errors.push("items_invalid");
    return map;
  }
  for (const [key, value] of Object.entries(input)) {
    const id = resolveItemId(key);
    if (!id) {
      errors.push("items_unknown_id:" + key);
      continue;
    }
    if (map[id]) errors.push("items_duplicate:" + id);
    map[id] = value;
  }
  return map;
}

/**
 * Normalize opening_checklist from intent (or standalone payload).
 * Missing required → pending=true but allow_scaffold stays true (S1 builds DEMO).
 */
function normalizeChecklist(input = {}, options = {}) {
  const errors = [];
  // Soft-empty for null/undefined/""; bare array = items list (S4 may pass array).
  if (input == null || input === "") input = {};
  else if (Array.isArray(input)) input = { items: input };
  else if (!plain(input)) {
    return {
      ok: false,
      errors: ["oneshot_checklist_invalid"],
      checklist: null,
      allow_scaffold: true
    };
  }
  const root = input;
  // Prefer nested opening_checklist / checklist when present; must be plain or items-array.
  let src = root;
  if (root.opening_checklist !== undefined) {
    if (Array.isArray(root.opening_checklist)) src = { items: root.opening_checklist };
    else if (plain(root.opening_checklist)) src = root.opening_checklist;
    else {
      return {
        ok: false,
        errors: ["oneshot_checklist_invalid"],
        checklist: null,
        allow_scaffold: true
      };
    }
  } else if (root.checklist !== undefined) {
    if (Array.isArray(root.checklist)) src = { items: root.checklist };
    else if (plain(root.checklist)) src = root.checklist;
    else {
      return {
        ok: false,
        errors: ["oneshot_checklist_invalid"],
        checklist: null,
        allow_scaffold: true
      };
    }
  }

  if (src.schema_version != null && src.schema_version !== VERSION) {
    // Unknown future versions: soft-fail unless caller opts in.
    if (options.strict_version) errors.push("oneshot_checklist_schema_version_invalid");
  }

  // If src is the items map itself (no items key, but has known ids), coerceItemsMap handles it.
  const itemsInput = (plain(src) && src.items === undefined && !Array.isArray(src) &&
    Object.keys(src).some((k) => resolveItemId(k)))
    ? src
    : (src.items !== undefined ? src.items : (Array.isArray(src) ? src : {}));

  const coerced = coerceItemsMap(itemsInput, errors);
  const items = Object.create(null);
  for (const id of ITEM_IDS) {
    items[id] = normalizeItemEntry(id, coerced[id], errors);
  }

  const pending_required = REQUIRED_ITEM_IDS.filter((id) => items[id].pending);
  const find_for_me = ITEM_IDS.filter((id) => items[id].status === "find_for_me");

  // Contract annotation: find_for_me never auto-writes guarded fields.
  for (const id of find_for_me) {
    if (items[id].auto_write_allowed !== false) {
      errors.push("item_" + id + "_auto_write_must_be_false");
    }
  }

  const checklist = {
    schema_version: VERSION,
    items,
    pending_required,
    find_for_me,
    allow_scaffold: true,
    find_for_me_policy: FIND_FOR_ME_POLICY
  };

  return {
    ok: errors.length === 0,
    errors: [...new Set(errors)],
    checklist,
    allow_scaffold: true
  };
}

function validateChecklist(input, options = {}) {
  const normalized = normalizeChecklist(input, options);
  return {
    ok: normalized.ok,
    errors: normalized.errors,
    allow_scaffold: true,
    checklist: normalized.checklist,
    talk_tracks: talkTrackBundle(),
    demo_excludes_from_block_cap: demoExcludesFromBlockCap()
  };
}

function summarizeChecklist(input) {
  const { ok, errors, checklist } = normalizeChecklist(input);
  if (!checklist) {
    return {
      ok: false,
      errors,
      allow_scaffold: true,
      have: [],
      missing: [],
      find_for_me: [],
      pending_required: REQUIRED_ITEM_IDS.slice(),
      completion: {
        required_done: 0,
        required_total: REQUIRED_ITEM_IDS.length,
        optional_done: 0,
        optional_total: OPTIONAL_ITEM_IDS.length
      },
      status_labels_zh: STATUS_LABELS_ZH,
      ask_copy_zh: ASK_CHECKLIST_COPY_ZH,
      ask_copy_en: ASK_CHECKLIST_COPY_EN,
      talk_tracks: talkTrackBundle(),
      demo_excludes_from_block_cap: demoExcludesFromBlockCap()
    };
  }
  const have = ITEM_IDS.filter((id) => checklist.items[id].status === "have");
  const missing = ITEM_IDS.filter((id) => checklist.items[id].status === "missing");
  const find_for_me = checklist.find_for_me.slice();
  const required_done = REQUIRED_ITEM_IDS.filter((id) => checklist.items[id].status === "have").length;
  const optional_done = OPTIONAL_ITEM_IDS.filter((id) => checklist.items[id].status === "have").length;
  return {
    ok,
    errors,
    allow_scaffold: true,
    have,
    missing,
    find_for_me,
    pending_required: checklist.pending_required.slice(),
    completion: {
      required_done,
      required_total: REQUIRED_ITEM_IDS.length,
      optional_done,
      optional_total: OPTIONAL_ITEM_IDS.length
    },
    status_labels_zh: STATUS_LABELS_ZH,
    ask_copy_zh: ASK_CHECKLIST_COPY_ZH,
    ask_copy_en: ASK_CHECKLIST_COPY_EN,
    talk_tracks: talkTrackBundle(),
    demo_excludes_from_block_cap: demoExcludesFromBlockCap(),
    checklist
  };
}

/** Empty checklist: all missing; pending_required = all required; scaffold still allowed. */
function emptyChecklist() {
  return normalizeChecklist({ items: {} }).checklist;
}

/**
 * Derive status hints from already-filled oneshot intent content (for S3/S4 reconcile).
 * Does not crawl. Does not mutate intent. Pure observation.
 */
function deriveHintsFromIntent(intent = {}) {
  const src = plain(intent) ? intent : {};
  const hints = Object.create(null);
  const name = typeof src.display_name === "string" ? src.display_name.trim() : "";
  hints.display_name = name ? "have" : "missing";
  const bio = typeof src.bio === "string" ? src.bio.trim() : (typeof src.tagline === "string" ? src.tagline.trim() : "");
  hints.bio = bio ? "have" : "missing";
  const links = Array.isArray(src.links) ? src.links : [];
  const socials = Array.isArray(src.socials) ? src.socials : [];
  const groups = Array.isArray(src.link_groups) ? src.link_groups : [];
  const groupItems = groups.reduce((n, g) => n + (Array.isArray(g && g.items) ? g.items.length : 0), 0);
  hints.link_or_social = (links.length + socials.length + groupItems) >= 1 ? "have" : "missing";
  const hasAvatar = Boolean(src.avatar_asset_id || src.cover_asset_id || src.avatar_url || src.cover_url);
  hints.avatar_cover = hasAvatar ? "have" : "missing";
  hints.cta_links = links.length >= 2 ? "have" : "missing";
  hints.socials = socials.length >= 1 ? "have" : "missing";
  const faqItems = plain(src.faq) && Array.isArray(src.faq.items) ? src.faq.items : (Array.isArray(src.faq) ? src.faq : []);
  hints.faq = faqItems.length >= 2 ? "have" : "missing";
  const hasMedia = Boolean(src.video || src.gallery || (Array.isArray(src.videos) && src.videos.length) || (Array.isArray(src.galleries) && src.galleries.length));
  hints.video_or_gallery = hasMedia ? "have" : "missing";
  const hasForm = Boolean(src.form || src.product || src.shop || (Array.isArray(src.forms) && src.forms.length));
  hints.form_or_product = hasForm ? "have" : "missing";
  hints.preferred_theme = (typeof src.theme_id === "string" && src.theme_id.trim()) || (typeof src.format_id === "string" && src.format_id.trim()) ? "have" : "missing";
  return hints;
}

/**
 * Reconcile checklist state with intent content for later S3/S4 passes.
 * Rule: if checklist says missing/find_for_me but intent already has real content → upgrade to have.
 * Never invents network results. Never auto-writes candidates into intent.
 */
function reconcileChecklist(input, intent = {}) {
  const normalized = normalizeChecklist(input);
  if (!normalized.checklist) return normalized;
  const hints = deriveHintsFromIntent(intent);
  const items = Object.create(null);
  for (const id of ITEM_IDS) {
    const prev = normalized.checklist.items[id];
    const next = Object.assign({}, prev);
    if (hints[id] === "have" && prev.status !== "have") {
      next.status = "have";
      next.pending = false;
      // Upgraded to have ⇒ candidates are no longer actionable and are dropped here (not kept for audit).
      if (next.candidates) delete next.candidates;
    }
    items[id] = next;
  }
  const pending_required = REQUIRED_ITEM_IDS.filter((id) => items[id].required && items[id].status !== "have");
  const find_for_me = ITEM_IDS.filter((id) => items[id].status === "find_for_me");
  const checklist = {
    schema_version: VERSION,
    items,
    pending_required,
    find_for_me,
    allow_scaffold: true,
    find_for_me_policy: FIND_FOR_ME_POLICY
  };
  return { ok: normalized.ok, errors: normalized.errors, checklist, allow_scaffold: true, hints };
}

const ASK_CHECKLIST_COPY_ZH = [
  "開場前請先勾資料準備清單（缺必填不擋、標「待補」；intent 只要帶任一個人檔案鍵（display_name／bio／status_text／language／avatar_asset_id／cover_asset_id 為字串，空字串也算；socials 為陣列，空陣列也算）或任一內容欄（texts／links／link_groups／extra_ops 為非空陣列、link_group 為物件、faq 不是 null、include_profile_block 或 include_socials_block 為 true），就只照內容產出、不補示範骨架；只有這些都沒有的 intent（只帶清單／套版／外觀也算）才由 intentToBlueprint 產出 DEMO 骨架）：",
  "",
  "【必填】",
  "1. 顯示名 — 我已有／我沒有／請幫我找",
  "2. 一句自介 — 我已有／我沒有／請幫我找",
  "3. 至少 1 個可點連結或社群 — 我已有／我沒有／請幫我找",
  "",
  "【選填】",
  "4. 頭像／封面",
  "5. 2–3 顆主行動連結",
  "6. 社群網址（IG／LINE／YT…）",
  "7. FAQ 兩則",
  "8. 影片或圖集",
  "9. 表單／電子報或單品",
  "10. 偏好套版（八套名或「暗／亮」）",
  "",
  "規則：勾「請幫我找」只會列出候選給你確認，不會自動寫入頭像或主連結。",
  "採用候選：列出後等人回「採用第 N 筆」或貼要的 https 才寫入；AI 不得自動採用。",
  "授權勾選：要上網搜尋／預填前，請先明確同意授權（回「同意授權搜尋預填」或勾選）；未授權不得動網路。",
  "示範不可發佈：骨架與「示範，請改」不可當正式頁；草稿仍有 public 示範顆時發布會被擋（hidden 示範不算；showcase 示範租戶豁免）；改成真實資料後，由人在 review URL（或編輯器）按發布（AI／bearer 不能代發）。",
  "內容辨識：示範／真實／爬取預填分標；候選帶 source／via／fetched_at，採用時顯示來源給人看。",
  "示範與上限：示範顆不計入 block cap（countTowardBlockLimit）；講剩幾格讀 GET /draft 的 blocks_remaining（＝block_limit − billable_block_count）；page-api 的 status.draft_block_count 與寫入回應 draft.block_count 含示範，勿用（editorState().block_count 不含示範）；禁止靜默塞滿 free 12。",
  "把每一項的狀態寫進 intent.opening_checklist.items（have｜missing｜find_for_me）。"
].join("\n");

const ASK_CHECKLIST_COPY_EN = [
  "Before we build, fill the opening data checklist (missing required → pending, never a refusal; an intent carrying any profile key (display_name / bio / status_text / language / avatar_asset_id / cover_asset_id as a string — an empty string counts; socials as an array — an empty array counts) or any content field (texts / links / link_groups / extra_ops as a non-empty array, link_group as an object, a non-null faq, include_profile_block or include_socials_block true) yields only that content — no demo skeleton; only an intent with none of these (checklist / theme / appearance only counts) makes intentToBlueprint emit the DEMO scaffold):",
  "",
  "Required: display_name, bio/tagline, ≥1 link or social.",
  "Optional: avatar/cover, 2–3 CTA links, socials, FAQ×2, video/gallery, form/product, preferred theme.",
  "Per item status: have | missing | find_for_me.",
  "find_for_me lists candidates only — never auto-writes avatar or primary links.",
  "Adopt: present candidates; write only after the human confirms adopt (e.g. \"adopt #N\" or paste https) — never auto-adopt.",
  "Consent: before any search/prefill over the network, the human must explicitly authorize/consent; no network without it.",
  "Demo not publishable: scaffold / \"demo — please replace\" is not a live page; publish is blocked while the draft still has public demo blocks (hidden demos do not count; the showcase demo tenant is exempt); after replacing demo with real data the human publishes on the review URL (or in the editor) (AI/bearer cannot publish).",
  "Content states: label demo vs real vs crawl-prefill; candidates carry source/via/fetched_at — show provenance on adopt.",
  "Demo vs free cap: demos do NOT count toward the block cap (countTowardBlockLimit); state slots left from GET /draft blocks_remaining (= block_limit − billable_block_count); page-api status.draft_block_count and write-response draft.block_count include demos — do not use (editorState().block_count excludes demos); never silently fill free 12.",
  "Put statuses in intent.opening_checklist.items."
].join("\n");

/** Talk-track thickeners (v3.108 S2) — skill paste after ASK; keep policy aligned with outline §8 / prefill consent / demo hard-block. */
const ASK_AUTH_CONSENT_COPY_ZH = [
  "【授權勾選】",
  "要幫你上網搜尋／預填外部資料前，請先明確同意。",
  "可回：「同意授權搜尋預填」或勾選同意項。",
  "未授權＝只用你貼上的內容在本機產出（沒有任何個人檔案鍵或內容欄（判準見清單首行）才是 DEMO 骨架），不上網搜尋／爬取、不呼叫預填 adapters。",
  "授權是呼叫端傳入的參數：S3 預填閘（runOpeningPrefill → isNetworkAuthorized）只讀呼叫端傳入的授權／同意值（input.authorized；沒有才看 auth、再看 consent）——接受 true、{authorized:true}／{consented:true}、purposes.personalization.status 為 granted 的同意狀態、或 links[]／entries[] 裡有一列 consented:true 且帶 url；不讀 intent 或開場清單欄位；intent 沒有授權欄（頂層 authorized 會被忽略，opening_checklist 項目加 authorized 鍵會被驗證器拒收）；CLI ez2-page-oneshot-apply.js 不帶授權 ⇒ 不建預填 adapters、不發搜尋／爬取請求（network_calls 0）。"
].join("\n");

const ASK_AUTH_CONSENT_COPY_EN = [
  "【Consent / authorize】",
  "Before any network search or prefill, the human must explicitly consent.",
  "Accept a clear reply like \"I authorize search/prefill\" or a checked consent item.",
  "Without consent: built locally from user-pasted content only (an intent with no profile key or content field — see the checklist header — gets the DEMO scaffold) — no network search/crawl, no prefill adapters.",
  "Consent is a caller-supplied argument: the S3 prefill gate (runOpeningPrefill → isNetworkAuthorized) reads only the authorization/consent value its caller passes (input.authorized; else auth, else consent) — accepted: true, {authorized:true} / {consented:true}, a consent state whose purposes.personalization.status is granted, or links[] / entries[] with a consented:true row that has a url; it never reads intent or opening-checklist fields; the intent has no consent field (a top-level authorized is ignored; an authorized key on an opening_checklist item is rejected by the validator); the CLI ez2-page-oneshot-apply.js passes none ⇒ no prefill adapters are built and no search/crawl request is made (network_calls 0)."
].join("\n");

const ASK_FILL_GAP_COPY_ZH = [
  "【補洞口語】缺欄怎麼問、怎麼回：",
  "• 「我已有」→ 請直接貼文字或 https，我寫進 intent 對應欄。",
  "• 「我沒有」→ 先標 missing／待補，不擋產出：藍圖照 intent 其他內容產出、不補示範（沒有任何個人檔案鍵或內容欄（判準見清單首行）才產出 DEMO 骨架）；之後再說「幫我補○○」。",
  "• 「請幫我找」→ 我只列候選（含來源），你選採用哪一筆後才寫入；絕不自動寫。",
  "還是示範／空的？可說「幫我補顯示名／自介／連結」或貼連結讓我窄回補。",
  "一次只追問最急的一格（必填優先），不要一次丟十題。"
].join("\n");

const ASK_FILL_GAP_COPY_EN = [
  "【Fill-gap talk】how to ask / map replies:",
  "• have → paste text or https; write into the matching intent field.",
  "• missing → mark missing/pending; never blocks the blueprint — the blueprint carries the rest of the intent as-is with no demo filler (only an intent with no profile key or content field — see the checklist header — gets the DEMO scaffold); later \"help fill ○○\".",
  "• find_for_me → list candidates with source only; write after human adopt — never auto-write.",
  "Still demo/empty? Ask to fill that field or paste a link for narrow refill.",
  "Ask one urgent gap at a time (required first)."
].join("\n");

const ASK_ADOPT_CANDIDATE_COPY_ZH = [
  "【採用候選】",
  "find_for_me／搜尋結果只進候選列表，不進正式欄。",
  "對使用者說：找到 N 筆如下（含來源／via／fetched_at），請回「採用第 N 筆」或貼你要的 https。",
  "列出時讓人看見 provenance，再決定採用；未確認前：不寫頭像／封面／主連結／CTA（FIND_FOR_ME_POLICY／NO_AUTO_WRITE）。",
  "確認採用後才把該筆寫入 intent／草稿（寫成草稿顆時帶 block.provenance 留來源）；其餘候選只在該項仍是 find_for_me 時留在 intent——改成 have 後 normalize 會丟掉 candidates（reconcileChecklist 亦刪）。",
  "頭像／封面候選不能把網址直接寫進欄位：avatar_asset_id／cover_asset_id 只收這頁已上傳圖的 asset id（放網址 ⇒ PUT /profile 回 422 PAGE_PROFILE_INVALID；不存在的 id ⇒ 400 page_api_asset_not_found）——請人在編輯器上傳，或由呼叫端先用 page-api POST /assets 上傳這張圖（憑證要有 assets:write，否則 403 page_api_scope_mismatch），再把回應的 asset_id 放進該欄。"
].join("\n");

const ASK_ADOPT_CANDIDATE_COPY_EN = [
  "【Adopt candidates】",
  "find_for_me / search hits stay in the candidate list — not live fields.",
  "Tell the human: here are N candidates (with source/via/fetched_at); reply \"adopt #N\" or paste the https you want.",
  "Show provenance so the human can see it before adopt; until confirm: never write avatar/cover/primary link/CTA (FIND_FOR_ME_POLICY / NO_AUTO_WRITE).",
  "On adopt only, copy that row into intent/draft (as a draft block, carry block.provenance to keep the source); other candidates stay in the intent only while the item is still find_for_me — switching it to have makes normalize drop candidates (reconcileChecklist drops them too).",
  "An avatar/cover candidate cannot be written as a URL: avatar_asset_id / cover_asset_id accept only the asset id of an image already uploaded to this page (a URL ⇒ PUT /profile 422 PAGE_PROFILE_INVALID; an unknown id ⇒ 400 page_api_asset_not_found) — have the human upload it in the editor, or have the caller upload it first via page-api POST /assets (the token needs assets:write, else 403 page_api_scope_mismatch) and put the returned asset_id in that field."
].join("\n");

const ASK_DEMO_NO_PUBLISH_COPY_ZH = [
  "【示範不可發佈】",
  "帶示範簽章（「示範，請改」）的區塊＝示範，不是可上線內容；DEMO 骨架多數顆是示範，但骨架的 profile／socials 顆是位置槽（payload 恆為 {}，內容來自個人檔案的顯示名／自介／社群），沒有示範簽章、不算示範（不進 demo_remaining、計入上限）。",
  "草稿裡仍有 public 示範顆（demo_remaining > 0）時，人按發布會被擋（PAGE_PUBLISH_DEMO_REMAINING；設成 hidden 的示範不算；showcase 示範租戶豁免）；請改成真實資料後，在 review URL 由人按「確定發布」。",
  "AI／assistant bearer 不得呼叫 publish；只回報 preview.review_url；apply 成功但 review_url 是 null（CLI 印 REVIEW_URL=null）＝這次沒有任何變更：告訴人「沒有需要變更的內容」，不要回報 null 或自己拼連結。"
].join("\n");

const ASK_DEMO_NO_PUBLISH_COPY_EN = [
  "【Demo not publishable】",
  "Blocks carrying the demo signature (\"demo — please replace\") are demo, not live content; most DEMO-scaffold blocks are demo, but the scaffold's profile/socials blocks are placement slots (payload is always {}; their content comes from the profile's name / bio / socials) that carry no demo signature and are not demo (not in demo_remaining; they count toward the cap).",
  "Publish is blocked while the draft still has public demo blocks (demo_remaining > 0 → PAGE_PUBLISH_DEMO_REMAINING; hidden demos do not count; the showcase demo tenant is exempt); replace with real data, then the human confirms on the review URL.",
  "AI/assistant bearer must not call publish — only return preview.review_url; if apply succeeds with review_url null (the CLI prints REVIEW_URL=null), nothing changed: tell the human there is nothing to change (「沒有需要變更的內容」) and never report null or build a link yourself."
].join("\n");

/** Talk-track: identifiable demo vs real vs crawl-prefill (開發.md first-batch). */
const ASK_CONTENT_STATE_COPY_ZH = [
  "【示範／真實／爬取預填】",
  "三種狀態務必可辨識，勿混稱：",
  "• 示範＝帶示範簽章（「示範，請改」）的顆（DEMO 骨架多數顆是；骨架的 profile／socials 位置槽（payload 恆為 {}）沒簽章，判 empty 不判示範）；僅預覽，不可當正式頁。",
  "• 真實＝人貼上或已確認採用的正式內容。",
  "• 爬取預填＝經授權搜尋／爬取後的候選或草稿預填（草稿顆帶 block.provenance）；只在候選／私有草稿，人按發布前不進公開頁；發布後公開頁該顆印來源行（ttl 到期自公開頁隱藏）。",
  "對使用者說話時標明目前是哪一種；勿把預填說成已上線真實資料。",
  "執行面沒有 content_state 欄位：由 contentKindOfBlock（fill-gap）依示範簽章／block.provenance 判 demo｜crawl_prefill｜real｜empty；採用後仍帶 provenance 的顆會判 crawl_prefill，請說「已採用（來源 X）」。"
].join("\n");

const ASK_CONTENT_STATE_COPY_EN = [
  "【Demo / real / crawl-prefill】",
  "Keep the three states identifiable — never conflate them:",
  "• Demo = blocks with the demo signature (\"demo — please replace\"), as most DEMO-scaffold blocks are (the scaffold's profile/socials placement slots — payload always {} — carry none and read empty, not demo); preview only, not a live page.",
  "• Real = human-pasted or human-adopted live content.",
  "• Crawl-prefill = authorized search/crawl candidates or draft prefill (draft blocks carry block.provenance); private candidate/draft only — not on the public page until the human publishes; once published the block shows a source line (hidden from the public page after its ttl expires).",
  "Say which state the user is looking at; never call prefill \"live real\" data.",
  "There is no content_state API field: contentKindOfBlock (fill-gap) derives demo|crawl_prefill|real|empty from the demo signature / block.provenance; adopted blocks that keep provenance read crawl_prefill — say \"adopted (from X)\"."
].join("\n");

/** Talk-track: first-batch provenance (source / via / fetched_at) per 開發.md. */
const ASK_PROVENANCE_COPY_ZH = [
  "【來源／provenance】",
  "草稿顆的 block.provenance 必須帶：source（來源標，1–32 字：首字須是小寫英文或數字 a-z／0-9，其後可用 a-z／0-9／_／-）、url（請用 https——契約也收 http；沒有網址填 null）、fetched_at（取得時間）、ttl_days（整數 0–3650 或 null；null／0＝不過期）；經搜尋找到的加 via:\"search\"（契約唯一允許值）。開場清單 find_for_me 的 candidates 只收 label／url／source／via／fetched_at——不可帶 ttl_days（帶了 intentToBlueprint 回 ok:false、apply 不寫）。",
  "列出／請人採用時，一併顯示來源與取得時間，讓人看得見再決定「採用第 N 筆」。",
  "採用後仍保留來源稽核。注意：update 帶 payload 卻沒帶 provenance ⇒ provenance 被清掉（RL-111：改過就不是抓來的）；人在編輯器改內容同樣清掉；update 只帶 visibility／span（不帶 payload）則保留。",
  "未帶可驗證 https 時可搜尋展示，但不得自動寫入頭像／主連結。"
].join("\n");

const ASK_PROVENANCE_COPY_EN = [
  "【Provenance】",
  "Draft block.provenance must carry: source (1–32 chars: the first must be a-z or 0-9, the rest a-z 0-9 _ -), url (use https — the contract also accepts http — or null when there is none), fetched_at, ttl_days (integer 0–3650 or null; null / 0 = never expires); add via:\"search\" when found by search (the only allowed value). Opening-checklist find_for_me candidates accept only label/url/source/via/fetched_at — never ttl_days (it makes intentToBlueprint return ok:false and apply writes nothing).",
  "When listing for adopt, show source and fetched time so the human can see provenance before \"adopt #N\".",
  "Keep provenance for audit after adopt. Note: an update that sends payload without provenance clears it (RL-111: edited = no longer fetched); a human editing content in the editor clears it too; updates carrying only visibility/span (no payload) keep it.",
  "Without a verifiable https you may search/show candidates, but never auto-write avatar/primary links."
].join("\n");

/** Talk-track: demo vs free block cap (開發.md first-batch). Claims map 1:1 to runtime (see talkTrackBundle().runtime_fields). */
const ASK_DEMO_VS_FREE_CAP_COPY_ZH = [
  "【示範與 free 上限】",
  "現況：示範顆不計入 block cap——registration 把 countTowardBlockLimit 接進 workspace，只數非示範顆；registration editorState() 回傳的 block_count 同口徑（有草稿數草稿、沒草稿數線上那份；資料欄；demo_excludes_from_block_cap）。",
  "AI 讀 page-api：GET /draft 頂層 billable_block_count（＝countTowardBlockLimit，不含示範）、block_limit、blocks_remaining；寫入回應（POST /draft、PUT /profile、PUT /appearance）的 draft.billable_block_count 同口徑（回應 draft:null 時沒有此欄），但不帶上限——剩幾格回讀 GET /draft。",
  "注意：page-api GET /draft 的 status.draft_block_count（沒草稿時為 null）、寫入回應的 draft.block_count（及 registration aibReviewDocument 的 block_count）都是 blocks.length、含示範（原義不變）——不可拿來算剩幾格；registration editorState().block_count 則不含示範。",
  "上限依方案：free 12／starter 24／pro 48／business 48／agency 48（BLOCK_LIMITS_BY_PLAN，RL-096 建議值、候 the maintainer 定）；沒有權益、權益判不了（無效／非 active（含停權）／期滿）或方案不在表上時為硬上限 50（DEFAULT_BLOCK_LIMIT；任何方案都不超過它）。",
  "剩幾格＝block_limit − billable_block_count（GET /draft 直接給 blocks_remaining，下限 0；離線可用 demoCapStatus().blocks_remaining 算）；目前編輯器不顯示剩餘格數、平常也不顯示上限；編輯器上跟塊數有關的數字包括 demo_remaining（剩幾顆示範）、WYSIWYG 狀態列的「共 n 塊」（＝草稿 blocks.length，連示範一起數）與表單頁每顆的序號「第 n 區塊」——都不是已用格數；上限數只在寫入被上限擋下（PAGE_BLOCKS_LIMIT_REACHED）時的錯誤頁出現（「已達 n 格上限」，n＝block_limit）。規則：任何介面若把示範計入上限，必須顯示剩餘數，不可隱藏。",
  "禁止靜默塞滿 free 12：不得為「湊滿上限」自動灌示範顆；缺洞標待補，等人補或採用。空 intent 的 DEMO 骨架不是整份都不佔格：其中 profile 與 socials 位置槽（payload 恆為 {}）沒有示範簽章，算進 billable_block_count（佔格）；只有帶示範簽章的顆不佔格。",
  "示範顆不佔格（已在草稿的示範不算進 billable_block_count），但逐顆新增（POST /draft 的 create 及其他走 applyDraftOps 的 create、或在編輯器新增一顆）都以 billable_block_count＋1 檢查上限——到上限（blocks_remaining 0）時這樣新增任何顆，連帶示範簽章的也一樣回 422 PAGE_BLOCKS_LIMIT_REACHED（422 的 block_count 是嘗試後的非示範數＝該次新增前的數＋1，不是現有數）；選套版重套（applyTemplate）整批重種示範時示範仍算 0、不另加 1，自有顆恰好等於上限時重種示範不會因示範被擋；但自有顆已超過上限（例如降方案後 billable_block_count > block_limit——blocks_remaining 下限 0，只看它分不出「剛好滿」和「超額」，要比這兩個數）時重套本身就回 422 PAGE_BLOCKS_LIMIT_REACHED（block_count＝自有顆數、不加 1），要先刪到上限以內；超額時改現有顆的內容、刪顆照常可做；public 示範顆仍擋發布（demo_remaining > 0 ⇒ 不可發佈；hidden 示範不算；showcase 示範租戶豁免）；別把「剩幾顆示範」說成「剩幾格」。",
  "加顆前先讀 GET /draft：要新增的顆數 > blocks_remaining（或 billable_block_count ≥ block_limit，已滿或超額）就先跟人說、請人刪顆或少加——apply 會先 PUT 套版／個人檔案（這些已落地），最後的 POST /draft 才回 422 PAGE_BLOCKS_LIMIT_REACHED、要加的顆一顆都不建；clear_existing:true 且已有草稿時是草稿先清空，新顆數超過方案上限一樣在最後 422（草稿停在已清空）。",
  "對使用者說明時講清楚「示範不算／自有已用 n／上限 m／剩幾格」（n＝billable_block_count、m＝block_limit、剩＝blocks_remaining）。"
].join("\n");

const ASK_DEMO_VS_FREE_CAP_COPY_EN = [
  "【Demo vs free cap】",
  "Today: demo blocks do NOT count toward the block cap — registration wires countTowardBlockLimit into the workspace so only non-demo blocks count; the block_count field returned by registration editorState() uses the same rule (counts the draft, or the published page when there is no draft; data field only; demo_excludes_from_block_cap).",
  "AI via page-api: GET /draft top-level billable_block_count (= countTowardBlockLimit, demos excluded), block_limit, blocks_remaining; write responses (POST /draft, PUT /profile, PUT /appearance) carry draft.billable_block_count on the same rule (no such field when the response has draft: null) but no cap — re-read GET /draft for slots left.",
  "Warning: page-api GET /draft status.draft_block_count (null when there is no draft), write-response draft.block_count (and registration aibReviewDocument block_count) are blocks.length and INCLUDE demos (meaning unchanged) — never use them for slots left; registration editorState().block_count, by contrast, excludes demos.",
  "Cap is per plan: free 12 / starter 24 / pro 48 / business 48 / agency 48 (BLOCK_LIMITS_BY_PLAN, RL-096 proposed, pending the maintainer); no entitlement, an unresolvable one (invalid / not active, incl. suspended / period ended) or a plan missing from the table ⇒ hard cap 50 (DEFAULT_BLOCK_LIMIT; no plan exceeds it).",
  "Slots left = block_limit − billable_block_count (GET /draft returns blocks_remaining, floored at 0; offline: demoCapStatus().blocks_remaining); the editor never displays remaining slots and normally not the cap either; block-related numbers it does show include demo_remaining (demo blocks left), the WYSIWYG status line's N blocks (= draft blocks.length, demos included) and the per-block ordinal \"Block n\" on the form page — none of them is slots used; the cap number appears only on the error page when a write hits the cap (PAGE_BLOCKS_LIMIT_REACHED: \"You have reached the N-block limit\", N = block_limit). Rule: any surface that counts demos toward the cap MUST show remaining — never hide the count.",
  "Forbid silently filling free 12: never auto-stuff demo blocks to hit the cap; mark gaps pending for human fill/adopt. The empty-intent DEMO scaffold is not slot-free as a whole: its profile and socials placement slots (payload always {}) carry no demo signature and are counted in billable_block_count (they take slots); only its demo-signature blocks take none.",
  "Demos take no slot (existing demo blocks are not in billable_block_count), but a single-block create (a POST /draft create or any other applyDraftOps create, or adding one block in the editor) is checked as billable_block_count + 1 — at the cap (blocks_remaining 0) such a create of any block, even a demo-signature one, returns 422 PAGE_BLOCKS_LIMIT_REACHED (its block_count is the attempted non-demo count — the count before that create + 1 — not the current count); a template reapply (applyTemplate) reseeding the demo set still counts demos as 0 with no + 1, so when own blocks exactly equal the cap the reseed is not blocked by its demos; but when own blocks already exceed the cap (e.g. after a plan downgrade, billable_block_count > block_limit — blocks_remaining is floored at 0, so only comparing those two numbers tells full from over) the reapply itself returns 422 PAGE_BLOCKS_LIMIT_REACHED (block_count = own count, no + 1) until blocks are deleted down to the cap; over the cap, editing and deleting existing blocks still work; public demo blocks still block publish (demo_remaining > 0; hidden demos do not count; the showcase demo tenant is exempt); never call \"demos remaining\" \"slots remaining\".",
  "Before adding blocks, read GET /draft: if the blocks you will add exceed blocks_remaining (or billable_block_count ≥ block_limit — full or over), tell the human first and ask them to delete some or add fewer — apply PUTs appearance/profile first (those land) and only the final POST /draft returns 422 PAGE_BLOCKS_LIMIT_REACHED, creating none of the new blocks; with clear_existing:true and an existing draft the draft is cleared first, and a new-block count above the plan cap still 422s at the end (the draft stays cleared).",
  "Tell the human: demos excluded / own used n / cap m / slots left (n = billable_block_count, m = block_limit, left = blocks_remaining)."
].join("\n");

// Lazy requires (template-demos → design-pack; fill-gap requires this module) — keep load order acyclic.
function templateDemosLib() { return require("./ez2-page-template-demos.js"); }
function blockLimitLib() { return require("./ez2-page-block-limit.js"); }

/**
 * Derived (not hardcoded): true iff the runtime counter ignores demo-signature blocks.
 * Probes templateDemos.countTowardBlockLimit with one seeded demo block + one own block.
 */
function demoExcludesFromBlockCap() {
  const demos = templateDemosLib();
  const seeded = demos.demoBlocks("nebula", { tenant_id: "probe", page_id: "probe" }, { images: false });
  const own = { block_type: "text", visibility: "public", payload: { text: "own", format: "plain" } };
  return seeded.length > 0 && demos.countTowardBlockLimit([seeded[0], own]) === 1;
}

/**
 * Derived remaining count for the talk track (same formula as registration editorState():
 * block_count = countTowardBlockLimit(draft blocks, or the published page's blocks when there is no draft); remaining = block_limit − block_count).
 * block_limit: explicit positive int (clamped to hard cap) | from entitlement via blockLimitFor | DEFAULT_BLOCK_LIMIT.
 * Pure; no I/O; never writes blocks. v3.109 S2 knife 02: registration billableBlockCap() reuses it for the
 * page-api GET /draft data fields (billable_block_count / block_limit / blocks_remaining) — data only; no talk-track text is read there.
 */
function demoCapStatus(input = {}) {
  const demos = templateDemosLib();
  const limits = blockLimitLib();
  const blocks = Array.isArray(input.blocks) ? input.blocks : [];
  let blockLimit;
  if (Number.isInteger(input.block_limit) && input.block_limit > 0) blockLimit = Math.min(input.block_limit, limits.DEFAULT_BLOCK_LIMIT);
  else if (input.entitlement) blockLimit = limits.blockLimitFor(input.entitlement, input.now || new Date().toISOString());
  else blockLimit = limits.DEFAULT_BLOCK_LIMIT;
  const blockCount = demos.countTowardBlockLimit(blocks);
  const demoCount = blocks.filter((block) => demos.isDemoBlock(block)).length;
  return Object.freeze({
    block_limit: blockLimit,
    block_count: blockCount,
    blocks_remaining: Math.max(0, blockLimit - blockCount),
    at_cap: blockCount >= blockLimit,
    demo_count: demoCount,
    demo_remaining: demos.demoCount(blocks),
    demo_excludes_from_block_cap: blockCount === blocks.length - demoCount
  });
}

/** Where each talk-track claim lives in runtime (reference strings for smoke/handoff; no behavior; 僅在 meta). */
const TALK_TRACK_RUNTIME_FIELDS = Object.freeze({
  demo_vs_free_cap: Object.freeze({
    enforcement: "templateDemos.countTowardBlockLimit (registration → workspace.count_toward_block_limit → assertUnderLimit)",
    registration_editor_state_fields: Object.freeze(["block_limit", "block_count", "demo_remaining"]),
    editor_displays: Object.freeze(["demo_remaining", "draft blocks.length (editor.wys.status 共 n 塊; demos included)"]),
    editor_displays_remaining_slots: false,
    // v3.109 S2 R1 polish: the form page also labels each block with an ordinal (editor.block.title 第 {n} 區塊) — a position, not a count
    editor_block_labels: Object.freeze(["editor.block.title (form page per-block ordinal; not a count)"]),
    // v3.109 S2 R1: the cap number is shown only on the editor error page when a write hits it (server EDITOR_INPUT_KEYS → editor.err.limit {n} = block_limit)
    editor_error_displays: Object.freeze(["block_limit (editor.err.limit on PAGE_BLOCKS_LIMIT_REACHED)"]),
    remaining_formula: "block_limit - billable_block_count",
    remaining_helper: "checklist.demoCapStatus().blocks_remaining",
    // v3.109 S2 knife 02: AI-readable non-demo fields (additive; block_count meanings unchanged)
    page_api_get_draft_fields: Object.freeze(["billable_block_count", "block_limit", "blocks_remaining"]),
    page_api_write_draft_fields: Object.freeze(["draft.billable_block_count"]),
    aib_review_document_fields: Object.freeze(["billable_block_count", "block_limit", "blocks_remaining"]),
    includes_demos_do_not_use: Object.freeze(["GET /draft status.draft_block_count", "write-response draft.block_count", "aibReviewDocument block_count"]),
    excludes_demos: Object.freeze(["registration editorState().block_count", "GET /draft billable_block_count", "write-response draft.billable_block_count", "aibReviewDocument billable_block_count"]),
    limit_error: "PAGE_BLOCKS_LIMIT_REACHED (422) { block_limit, block_count } — block_count = attempted non-demo count (count before that create + 1), not the current count; each single-block create is checked as count + 1, demo-signature blocks included (editable-workspace applyDraftOps create / createBlock); applyTemplate checks towardLimit(demos + own) with demos at 0 (no + 1) — own > limit (e.g. after a plan downgrade) ⇒ applyTemplate 422 too, block_count = own count",
    plan_caps_source: "blockLimit.BLOCK_LIMITS_BY_PLAN",
    default_cap_source: "blockLimit.DEFAULT_BLOCK_LIMIT",
    publish_gate: "templateDemos.publishBlockedByDemo (PAGE_PUBLISH_DEMO_REMAINING)"
  }),
  content_state: Object.freeze({
    api_field: null,
    derived_by: "fillGap.contentKindOfBlock",
    values: Object.freeze(["demo", "crawl_prefill", "real", "empty"])
  }),
  provenance: Object.freeze({
    block_field: "block.provenance",
    keys: Object.freeze(["source", "url", "fetched_at", "ttl_days", "via"]),
    required_keys: Object.freeze(["source", "url", "fetched_at", "ttl_days"]),
    via_values: Object.freeze(["search"]),
    validator: "blockContract.provenanceErrors"
  })
});

/**
 * Structured ASK talk-track bundle — blueprint meta / checklist summary only (僅在 meta):
 * summarizeChecklist / validateChecklist return + intentToBlueprint().meta.ask_talk_tracks.
 * server.js / page-api.js do not read it; nothing is displayed from it. Thin glue; no normalize change.
 */
const TALK_TRACK_SURFACE = "blueprint_meta_and_checklist_summary_only";

function talkTrackBundle() {
  return Object.freeze({
    schema_version: VERSION,
    surface: TALK_TRACK_SURFACE,
    demo_excludes_from_block_cap: demoExcludesFromBlockCap(),
    checklist: Object.freeze({ zh: ASK_CHECKLIST_COPY_ZH, en: ASK_CHECKLIST_COPY_EN }),
    auth_consent: Object.freeze({ zh: ASK_AUTH_CONSENT_COPY_ZH, en: ASK_AUTH_CONSENT_COPY_EN }),
    fill_gap: Object.freeze({ zh: ASK_FILL_GAP_COPY_ZH, en: ASK_FILL_GAP_COPY_EN }),
    adopt_candidate: Object.freeze({ zh: ASK_ADOPT_CANDIDATE_COPY_ZH, en: ASK_ADOPT_CANDIDATE_COPY_EN }),
    demo_no_publish: Object.freeze({ zh: ASK_DEMO_NO_PUBLISH_COPY_ZH, en: ASK_DEMO_NO_PUBLISH_COPY_EN }),
    content_state: Object.freeze({ zh: ASK_CONTENT_STATE_COPY_ZH, en: ASK_CONTENT_STATE_COPY_EN }),
    provenance: Object.freeze({ zh: ASK_PROVENANCE_COPY_ZH, en: ASK_PROVENANCE_COPY_EN }),
    demo_vs_free_cap: Object.freeze({ zh: ASK_DEMO_VS_FREE_CAP_COPY_ZH, en: ASK_DEMO_VS_FREE_CAP_COPY_EN }),
    runtime_fields: TALK_TRACK_RUNTIME_FIELDS
  });
}

/** Intent JSON fragment example for skill / handoff prompts. */
function intentFieldExample() {
  return {
    opening_checklist: {
      schema_version: VERSION,
      items: {
        display_name: { status: "have", note: "小安" },
        bio: { status: "missing" },
        link_or_social: { status: "find_for_me", candidates: [] },
        avatar_cover: { status: "find_for_me", candidates: [] },
        cta_links: { status: "missing" },
        socials: { status: "missing" },
        faq: { status: "missing" },
        video_or_gallery: { status: "missing" },
        form_or_product: { status: "missing" },
        preferred_theme: { status: "have", note: "暗色 / nebula" }
      }
    }
  };
}

module.exports = {
  VERSION,
  STATUSES,
  STATUS_LABELS_ZH,
  FIND_FOR_ME_POLICY,
  NO_AUTO_WRITE_IDS,
  ITEM_DEFS,
  ITEM_IDS,
  ITEM_DEF_BY_ID,
  REQUIRED_ITEM_IDS,
  OPTIONAL_ITEM_IDS,
  ASK_CHECKLIST_COPY_ZH,
  ASK_CHECKLIST_COPY_EN,
  ASK_AUTH_CONSENT_COPY_ZH,
  ASK_AUTH_CONSENT_COPY_EN,
  ASK_FILL_GAP_COPY_ZH,
  ASK_FILL_GAP_COPY_EN,
  ASK_ADOPT_CANDIDATE_COPY_ZH,
  ASK_ADOPT_CANDIDATE_COPY_EN,
  ASK_DEMO_NO_PUBLISH_COPY_ZH,
  ASK_DEMO_NO_PUBLISH_COPY_EN,
  ASK_CONTENT_STATE_COPY_ZH,
  ASK_CONTENT_STATE_COPY_EN,
  ASK_PROVENANCE_COPY_ZH,
  ASK_PROVENANCE_COPY_EN,
  ASK_DEMO_VS_FREE_CAP_COPY_ZH,
  ASK_DEMO_VS_FREE_CAP_COPY_EN,
  TALK_TRACK_RUNTIME_FIELDS,
  TALK_TRACK_SURFACE,
  talkTrackBundle,
  demoCapStatus,
  demoExcludesFromBlockCap,
  normalizeChecklist,
  validateChecklist,
  summarizeChecklist,
  emptyChecklist,
  deriveHintsFromIntent,
  reconcileChecklist,
  findForMeMayAutoWrite,
  intentFieldExample,
  resolveItemId,
  normalizeStatus
};
