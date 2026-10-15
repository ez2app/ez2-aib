// SPDX-License-Identifier: Apache-2.0
"use strict";

const { resolveRouteCategory } = require("./ez2-route-category-resolver");

const UID_EPOCH = 1577836800000;
const UID_SCHEME = "ez2-note-time-base36-v1";
const PRIVACY_LEVELS = new Set(["public", "internal", "restricted", "sensitive"]);
const SOURCE_KINDS = new Set(["ai_chat", "persona_distill", "second_brain_import", "publishing", "crm", "commerce", "manual", "legacy_obsidian"]);
const REVIEW_STATUSES = new Set(["draft", "pending", "approved", "rejected"]);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function genNoteUid(ms, used) {
  let n = Math.max(0, Math.floor(((ms || Date.now()) - UID_EPOCH) / 1000));
  let code = n.toString(36).padStart(6, "0");
  while (used && used.has(code)) {
    n++;
    code = n.toString(36).padStart(6, "0");
  }
  if (used) used.add(code);
  return code;
}

function cleanTitle(value, max = 36) {
  const clean = String(value || "")
    .normalize("NFKC")
    .replace(/[\\/:*?"<>|#^\[\]\n\r\t]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return Array.from(clean).slice(0, max).join("") || "Ez2筆記";
}

function normalizeRouteKey(value) {
  const routeKey = String(value || "").trim();
  // 底線必須收:route registry 正本(obsidian-route-registry-smoke.js:53)容許底線且
  // 既有路由 core.site_registry 已用底線——這裡不收=同一 routeKey 過得了 registry 卻在
  // 寫入路徑炸掉(lint 與 runtime 驗證器漂移)。
  assert(/^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$/.test(routeKey), "routeKey must use ez2 dotted lowercase format");
  return routeKey;
}

function normalizeCategoryUid(value) {
  const categoryUid = String(value || "").trim();
  assert(/^cat_[a-z0-9][a-z0-9_-]{2,}$/.test(categoryUid), "category_uid is required and must start with cat_");
  return categoryUid;
}

function normalizeOptionalCategoryUid(field, value) {
  if (value == null || value === "") return undefined;
  const categoryUid = String(value).trim();
  assert(/^cat_[a-z0-9][a-z0-9_-]{2,}$/.test(categoryUid), field + " must start with cat_");
  return categoryUid;
}

function normalizePrivacyLevel(value) {
  const privacy = String(value || "internal").trim();
  assert(PRIVACY_LEVELS.has(privacy), "privacy_level must be public/internal/restricted/sensitive");
  return privacy;
}

function normalizeSourceKind(value) {
  const sourceKind = String(value || "manual").trim();
  assert(SOURCE_KINDS.has(sourceKind), "source_kind is not allowed");
  return sourceKind;
}

function normalizeTags(tags) {
  const out = [];
  const seen = new Set();
  for (const raw of Array.isArray(tags) ? tags : []) {
    let tag = String(raw || "").normalize("NFKC").trim().toLowerCase();
    if (!tag) continue;
    if (!tag.startsWith("#")) tag = "#" + tag;
    tag = tag.replace(/\s+/g, "-");
    assert(/^#[a-z0-9][a-z0-9/_-]*$/.test(tag), "tag must be display-only lowercase #path");
    if (!seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  }
  return out;
}

function validateOptionalIds(input) {
  const out = {};
  if (input.object_uid != null && input.object_uid !== "") {
    const objectUid = String(input.object_uid).trim();
    assert(/^[CGFP][0-9]{6}-[0-9A-F]{3}$/.test(objectUid), "object_uid must use Ez2 layer object UID format");
    out.object_uid = objectUid;
  }
  if (input.gv_uid != null && input.gv_uid !== "") {
    const gvUid = String(input.gv_uid).trim();
    assert(/^GV[0-9]{6}-[0-9A-F]{3}$/.test(gvUid), "gv_uid must use Ez2 GV UID format");
    out.gv_uid = gvUid;
  }
  return out;
}

function validateCategoryGovernance(input) {
  const out = {};
  const parentCategoryUid = normalizeOptionalCategoryUid("parent_category_uid", input.parent_category_uid);
  if (parentCategoryUid) out.parent_category_uid = parentCategoryUid;
  const canonicalCategoryUid = normalizeOptionalCategoryUid("canonical_category_uid", input.canonical_category_uid);
  if (canonicalCategoryUid) out.canonical_category_uid = canonicalCategoryUid;
  if (input.category_registry_ref != null && input.category_registry_ref !== "") {
    const ref = String(input.category_registry_ref).trim();
    assert(/^reg_[a-z0-9_]+$/.test(ref), "category_registry_ref must use registry uid format");
    out.category_registry_ref = ref;
  }
  if (input.legacy_category_path != null && input.legacy_category_path !== "") {
    out.legacy_category_path = String(input.legacy_category_path).replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+/g, "/");
  }
  if (input.created_by_ai != null) {
    assert(typeof input.created_by_ai === "boolean", "created_by_ai must be boolean");
    out.created_by_ai = input.created_by_ai;
  }
  if (input.review_status != null && input.review_status !== "") {
    const reviewStatus = String(input.review_status).trim();
    assert(REVIEW_STATUSES.has(reviewStatus), "review_status must be draft/pending/approved/rejected");
    out.review_status = reviewStatus;
  }
  return out;
}

function normalizeLegacyMapping(sourceKind, legacyMapping) {
  if (legacyMapping == null) return undefined;
  assert(sourceKind === "legacy_obsidian", "legacy_mapping is only allowed for legacy_obsidian source_kind");
  const out = {};
  for (const key of ["source_vault_path", "source_file_path", "source_folder_path"]) {
    if (legacyMapping[key] != null) out[key] = String(legacyMapping[key]);
  }
  if (legacyMapping.source_tags != null) {
    assert(Array.isArray(legacyMapping.source_tags), "legacy_mapping.source_tags must be an array");
    out.source_tags = legacyMapping.source_tags.map((x) => String(x));
  }
  const migrationMode = String(legacyMapping.migration_mode || "index_only");
  assert(["detect_only", "index_only", "copy_pending", "apply_after_confirm"].includes(migrationMode), "legacy migration_mode is not allowed");
  out.migration_mode = migrationMode;
  return out;
}

function hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj || {}, key);
}

function validateEz2NoteRecord(data) {
  const errors = [];
  if (!/^[a-z0-9]{6}$/.test(data && data.uid || "")) errors.push("uid must be six lowercase base36 chars");
  if (data && data.uid_scheme !== UID_SCHEME) errors.push("uid_scheme mismatch");
  if (!/^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$/.test(data && data.routeKey || "")) errors.push("routeKey invalid");
  for (const field of ["title", "routeKey", "category_uid", "privacy_level", "source_kind", "frontmatter"]) {
    if (!hasOwn(data, field)) errors.push("note missing " + field);
  }
  if (!/^cat_[a-z0-9][a-z0-9_-]{2,}$/.test(data && data.category_uid || "")) errors.push("note missing valid category_uid");
  for (const field of ["parent_category_uid", "canonical_category_uid"]) {
    if (hasOwn(data, field) && !/^cat_[a-z0-9][a-z0-9_-]{2,}$/.test(data[field] || "")) errors.push("note " + field + " invalid");
  }
  if (hasOwn(data, "category_registry_ref") && !/^reg_[a-z0-9_]+$/.test(data.category_registry_ref || "")) errors.push("note category_registry_ref invalid");
  if (hasOwn(data, "created_by_ai") && typeof data.created_by_ai !== "boolean") errors.push("note created_by_ai must be boolean");
  if (hasOwn(data, "review_status") && !REVIEW_STATUSES.has(data.review_status)) errors.push("note review_status invalid");
  const fm = data && data.frontmatter || {};
  for (const field of ["uid", "routeKey", "category_uid", "privacy_level"]) {
    if (!hasOwn(fm, field)) errors.push("frontmatter missing " + field);
  }
  if (hasOwn(fm, "routeKey") && !/^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$/.test(fm.routeKey || "")) {
    errors.push("frontmatter routeKey invalid");
  }
  for (const field of ["parent_category_uid", "canonical_category_uid"]) {
    if (hasOwn(fm, field) && !/^cat_[a-z0-9][a-z0-9_-]{2,}$/.test(fm[field] || "")) errors.push("frontmatter " + field + " invalid");
  }
  if (hasOwn(fm, "category_registry_ref") && !/^reg_[a-z0-9_]+$/.test(fm.category_registry_ref || "")) errors.push("frontmatter category_registry_ref invalid");
  if (hasOwn(fm, "created_by_ai") && typeof fm.created_by_ai !== "boolean") errors.push("frontmatter created_by_ai must be boolean");
  if (hasOwn(fm, "review_status") && !REVIEW_STATUSES.has(fm.review_status)) errors.push("frontmatter review_status invalid");
  for (const field of ["uid", "routeKey", "category_uid", "privacy_level"]) {
    if (hasOwn(data, field) && hasOwn(fm, field) && data[field] !== fm[field]) {
      errors.push("frontmatter " + field + " must match note " + field);
    }
  }
  for (const field of ["parent_category_uid", "canonical_category_uid", "category_registry_ref", "legacy_category_path", "created_by_ai", "review_status"]) {
    if (hasOwn(data, field) && hasOwn(fm, field) && data[field] !== fm[field]) {
      errors.push("frontmatter " + field + " must match note " + field);
    }
  }
  if (data && data.legacy_mapping && data.legacy_mapping.migration_mode === "apply_after_confirm") {
    errors.push("note validator must not accept apply_after_confirm without explicit user confirmation evidence");
  }
  return errors;
}

function resolveCategoryInput(input, options, routeKey) {
  if (!options.resolveCategory) return Object.assign({}, input);
  const resolved = resolveRouteCategory({
    routeKey,
    dataLevel: input.privacy_level,
    aiReadable: input.ai_readable,
    aiWritable: input.ai_writable
  }, {
    registry: options.categoryRegistry,
    registryPath: options.categoryRegistryPath
  });
  return Object.assign({}, input, {
    category_uid: input.category_uid || resolved.category_uid,
    parent_category_uid: input.parent_category_uid || resolved.parent_category_uid || undefined,
    canonical_category_uid: input.canonical_category_uid || resolved.canonical_category_uid || undefined,
    category_registry_ref: input.category_registry_ref || resolved.category_registry_ref || resolved.registry_uid || undefined,
    legacy_category_path: input.legacy_category_path || resolved.legacy_category_path || undefined,
    privacy_level: input.privacy_level || resolved.privacy_level
  });
}

function buildEz2NoteRecord(input = {}, options = {}) {
  const createdAt = input.created_at || options.now || Date.now();
  const ms = createdAt instanceof Date ? createdAt.getTime() : (typeof createdAt === "number" ? createdAt : Date.parse(String(createdAt)));
  assert(Number.isFinite(ms), "created_at must be parseable");
  const uid = input.uid ? String(input.uid).trim() : genNoteUid(ms, options.usedUids);
  assert(/^[a-z0-9]{6}$/.test(uid), "uid must be 6 lowercase base36 chars");

  const routeKey = normalizeRouteKey(input.routeKey);
  const resolvedInput = resolveCategoryInput(input, options, routeKey);
  const title = cleanTitle(resolvedInput.title, options.titleMax || 36);
  const categoryUid = normalizeCategoryUid(resolvedInput.category_uid);
  const privacyLevel = normalizePrivacyLevel(resolvedInput.privacy_level);
  const sourceKind = normalizeSourceKind(resolvedInput.source_kind);
  const tags = normalizeTags(resolvedInput.tags);
  const optionalIds = validateOptionalIds(resolvedInput);
  const categoryGovernance = validateCategoryGovernance(resolvedInput);
  const legacyMapping = normalizeLegacyMapping(sourceKind, resolvedInput.legacy_mapping);
  const stamp = new Date(ms).toISOString();

  const frontmatter = Object.assign({}, resolvedInput.frontmatter || {}, {
    uid,
    routeKey,
    category_uid: categoryUid,
    privacy_level: privacyLevel
  });
  frontmatter.title = frontmatter.title || title;
  frontmatter.uid_scheme = frontmatter.uid_scheme || UID_SCHEME;
  frontmatter.source_kind = frontmatter.source_kind || sourceKind;
  frontmatter.created = frontmatter.created || stamp;
  frontmatter.updated = frontmatter.updated || stamp;
  frontmatter.ez2fmt = frontmatter.ez2fmt || 2;
  if (!Array.isArray(frontmatter.aliases) || !frontmatter.aliases.includes(uid)) {
    frontmatter.aliases = Array.from(new Set([uid].concat(Array.isArray(frontmatter.aliases) ? frontmatter.aliases : [])));
  }
  if (tags.length) frontmatter.tags = tags;
  for (const key of ["parent_category_uid", "canonical_category_uid", "category_registry_ref", "legacy_category_path", "created_by_ai", "review_status"]) {
    if (Object.prototype.hasOwnProperty.call(categoryGovernance, key)) frontmatter[key] = categoryGovernance[key];
  }
  if (privacyLevel === "restricted" || privacyLevel === "sensitive") {
    frontmatter.ai_readable = false;
    frontmatter.ai_writable = false;
  }

  const record = Object.assign({
    uid,
    uid_scheme: UID_SCHEME,
    title,
    routeKey,
    category_uid: categoryUid,
    privacy_level: privacyLevel,
    source_kind: sourceKind,
    frontmatter
  }, optionalIds, categoryGovernance);
  if (tags.length) record.tags = tags;
  if (legacyMapping) record.legacy_mapping = legacyMapping;
  return record;
}

function yamlScalar(value) {
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  const s = String(value == null ? "" : value);
  if (/^[A-Za-z0-9_.:/@+-]+$/.test(s)) return s;
  return JSON.stringify(s);
}

function yamlValue(value) {
  if (Array.isArray(value)) return "[" + value.map(yamlScalar).join(", ") + "]";
  if (value && typeof value === "object") return JSON.stringify(value);
  return yamlScalar(value);
}

function buildEz2NoteMarkdown(record, body = "") {
  const fm = record.frontmatter || {};
  const keys = Object.keys(fm).sort((a, b) => {
    const order = [
      "uid",
      "aliases",
      "title",
      "uid_scheme",
      "routeKey",
      "category_uid",
      "parent_category_uid",
      "canonical_category_uid",
      "category_registry_ref",
      "legacy_category_path",
      "privacy_level",
      "source_kind",
      "created_by_ai",
      "review_status",
      "created",
      "updated",
      "ez2fmt",
      "tags",
      "ai_readable",
      "ai_writable"
    ];
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.localeCompare(b);
  });
  const frontmatter = ["---"];
  for (const key of keys) frontmatter.push(key + ": " + yamlValue(fm[key]));
  frontmatter.push("---", "", String(body || "").trim(), "");
  return frontmatter.join("\n");
}

function buildEz2NoteFileName(record, maxTitle = 36) {
  return record.uid + "-" + cleanTitle(record.title, maxTitle) + ".md";
}

function buildEz2Note(input = {}, options = {}) {
  const record = buildEz2NoteRecord(input, options);
  return {
    record,
    fileName: buildEz2NoteFileName(record, options.titleMax || 36),
    markdown: buildEz2NoteMarkdown(record, input.body || "")
  };
}

module.exports = {
  UID_EPOCH,
  UID_SCHEME,
  genNoteUid,
  cleanTitle,
  normalizeTags,
  validateEz2NoteRecord,
  buildEz2NoteRecord,
  buildEz2NoteMarkdown,
  buildEz2NoteFileName,
  buildEz2Note
};
