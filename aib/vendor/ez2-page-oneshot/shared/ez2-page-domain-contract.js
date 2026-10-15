"use strict";

const VERSION = "2026-07-11.ez2-page-domain.v1";
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$/;
const STATUSES = Object.freeze([
  "draft",
  "review",
  "published_noindex",
  "published_indexable",
  "suspended",
  "archived"
]);
const STATUS_SET = new Set(STATUSES);
const PUBLISHED = new Set(["published_noindex", "published_indexable"]);
const TRANSITIONS = Object.freeze({
  draft: new Set(["review", "archived"]),
  review: new Set(["draft", "published_noindex", "archived"]),
  published_noindex: new Set(["draft", "published_indexable", "suspended", "archived"]),
  published_indexable: new Set(["draft", "published_noindex", "suspended", "archived"]),
  suspended: new Set(["draft", "archived"]),
  archived: new Set(["draft"])
});
const ALLOWED_FIELDS = new Set([
  "schema_version",
  "tenant_id",
  "page_id",
  "revision_id",
  "previous_revision_id",
  "created_by_identity_id",
  "status",
  "version",
  "block_ids",
  "primary_route_id",
  "quality_gate_version",
  "identity_verified_at",
  "published_at",
  "created_at",
  "updated_at"
]);

function plain(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function id(value) {
  return typeof value === "string" && ID_RE.test(value);
}

function date(value) {
  return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
}

function nullableId(value) {
  return value == null || id(value);
}

function nullableDate(value) {
  return value == null || date(value);
}

function validatePageRecord(record, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  if (!plain(record)) return { ok: false, errors: ["page_record_invalid"] };

  const unknown = Object.keys(record).filter((key) => !ALLOWED_FIELDS.has(key));
  if (unknown.length) errors.push("page_unknown_fields");
  if (record.schema_version !== VERSION) errors.push("page_schema_version_invalid");
  if (!id(record.tenant_id)) errors.push("page_tenant_id_invalid");
  if (!id(record.page_id)) errors.push("page_id_invalid");
  if (!id(record.revision_id)) errors.push("page_revision_id_invalid");
  if (!nullableId(record.previous_revision_id)) errors.push("page_previous_revision_id_invalid");
  if (!id(record.created_by_identity_id)) errors.push("page_creator_identity_invalid");
  if (!STATUS_SET.has(record.status)) errors.push("page_status_invalid");
  if (!Number.isInteger(record.version) || record.version < 1) errors.push("page_version_invalid");
  if (!Array.isArray(record.block_ids) || !record.block_ids.every(id)) errors.push("page_block_ids_invalid");
  if (mutation !== "allow-duplicate-block-ids" && Array.isArray(record.block_ids) && new Set(record.block_ids).size !== record.block_ids.length) {
    errors.push("page_block_ids_duplicate");
  }
  if (!nullableId(record.primary_route_id)) errors.push("page_primary_route_id_invalid");
  if (!nullableId(record.quality_gate_version)) errors.push("page_quality_gate_version_invalid");
  if (!nullableDate(record.identity_verified_at)) errors.push("page_identity_verified_at_invalid");
  if (!nullableDate(record.published_at)) errors.push("page_published_at_invalid");
  if (!date(record.created_at) || !date(record.updated_at)) errors.push("page_timestamp_invalid");
  if (date(record.created_at) && date(record.updated_at) && Date.parse(record.updated_at) < Date.parse(record.created_at)) {
    errors.push("page_updated_before_created");
  }
  if (PUBLISHED.has(record.status) && !date(record.published_at)) errors.push("page_published_status_requires_time");
  if (!PUBLISHED.has(record.status) && record.published_at != null) errors.push("page_unpublished_status_forbids_time");
  if (record.status === "published_indexable" && (!id(record.quality_gate_version) || !date(record.identity_verified_at))) {
    errors.push("page_indexable_gate_evidence_required");
  }

  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

function validatePageTransition(previous, next, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  const before = validatePageRecord(previous, options);
  const after = validatePageRecord(next, options);
  for (const error of before.errors) errors.push("previous:" + error);
  for (const error of after.errors) errors.push("next:" + error);
  if (!before.ok || !after.ok) return { ok: false, errors: [...new Set(errors)] };

  if (mutation !== "allow-cross-tenant-transition" && previous.tenant_id !== next.tenant_id) errors.push("page_transition_cross_tenant");
  if (mutation !== "allow-page-id-change" && previous.page_id !== next.page_id) errors.push("page_transition_identity_changed");
  if (previous.created_by_identity_id !== next.created_by_identity_id || previous.created_at !== next.created_at) {
    errors.push("page_transition_creation_identity_changed");
  }
  if (next.previous_revision_id !== previous.revision_id) errors.push("page_transition_revision_chain_invalid");
  if (next.revision_id === previous.revision_id) errors.push("page_transition_revision_reused");
  if (next.version !== previous.version + 1) errors.push("page_transition_version_invalid");
  if (Date.parse(next.updated_at) <= Date.parse(previous.updated_at)) errors.push("page_transition_time_not_advanced");
  const allowed = TRANSITIONS[previous.status];
  if (mutation !== "skip-transition-rule" && (!allowed || !allowed.has(next.status))) errors.push("page_transition_status_forbidden");

  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

module.exports = {
  ALLOWED_FIELDS,
  PUBLISHED,
  STATUSES,
  TRANSITIONS,
  VERSION,
  validatePageRecord,
  validatePageTransition
};
