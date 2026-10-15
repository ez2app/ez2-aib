"use strict";

const VERSION = "2026-07-11.ez2-page-consent.v1";
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$/;
const PURPOSES = Object.freeze(["necessary", "analytics", "personalization", "marketing"]);
const PURPOSE_SET = new Set(PURPOSES);
const SUBJECT_TYPES = new Set(["anonymous", "person"]);
const OPTIONAL_STATUS = new Set(["unset", "granted", "denied"]);
const NECESSARY_BASIS = new Set(["contract", "legal_obligation", "legitimate_interest"]);
const SOURCES = new Set(["banner", "settings", "account", "server"]);
const RECORD_FIELDS = new Set(["schema_version", "tenant_id", "subject_type", "subject_id", "policy_version", "record_version", "purposes", "created_at", "updated_at"]);
const DECISION_FIELDS = new Set(["status", "basis", "source", "decided_at"]);

function plain(value) { if (!value || typeof value !== "object" || Array.isArray(value)) return false; const proto = Object.getPrototypeOf(value); return proto === Object.prototype || proto === null; }
function exact(value, fields) { return plain(value) && Object.keys(value).every((key) => fields.has(key)); }
function id(value) { return typeof value === "string" && ID_RE.test(value); }
function date(value) { return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value)); }

function purposeErrors(name, value, mutation) {
  const errors = [];
  if (!exact(value, DECISION_FIELDS)) return ["page_consent_" + name + "_invalid"];
  if (name === "necessary") {
    if (value.status !== "granted" && mutation !== "allow-necessary-denied") errors.push("page_consent_necessary_must_be_granted");
    if (!NECESSARY_BASIS.has(value.basis) || !SOURCES.has(value.source) || !date(value.decided_at)) errors.push("page_consent_necessary_evidence_invalid");
  } else {
    if (!OPTIONAL_STATUS.has(value.status)) errors.push("page_consent_" + name + "_status_invalid");
    if (value.status === "unset") {
      if (value.basis != null || value.source != null || value.decided_at != null) errors.push("page_consent_" + name + "_unset_has_evidence");
    } else if (value.basis !== "consent" || !SOURCES.has(value.source) || !date(value.decided_at)) {
      errors.push("page_consent_" + name + "_evidence_invalid");
    }
  }
  return errors;
}

function validateConsentState(state, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  if (!exact(state, RECORD_FIELDS)) return { ok: false, errors: ["page_consent_record_invalid"] };
  if (state.schema_version !== VERSION) errors.push("page_consent_schema_version_invalid");
  if (!id(state.tenant_id) || !id(state.subject_id) || !id(state.policy_version)) errors.push("page_consent_identity_invalid");
  if (!SUBJECT_TYPES.has(state.subject_type)) errors.push("page_consent_subject_type_invalid");
  if (!Number.isInteger(state.record_version) || state.record_version < 1) errors.push("page_consent_version_invalid");
  if (!plain(state.purposes) || Object.keys(state.purposes).length !== PURPOSES.length || !PURPOSES.every((purpose) => Object.prototype.hasOwnProperty.call(state.purposes, purpose)) || Object.keys(state.purposes).some((purpose) => !PURPOSE_SET.has(purpose))) errors.push("page_consent_purposes_invalid");
  else for (const purpose of PURPOSES) errors.push(...purposeErrors(purpose, state.purposes[purpose], mutation));
  if (!date(state.created_at) || !date(state.updated_at)) errors.push("page_consent_timestamp_invalid");
  if (date(state.created_at) && date(state.updated_at) && Date.parse(state.updated_at) < Date.parse(state.created_at)) errors.push("page_consent_updated_before_created");
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

function canProcessPurpose(state, purpose, options = {}) {
  const mutation = String(options.test_mutation || "");
  const validation = validateConsentState(state, options);
  if (!validation.ok) return { allowed: false, reason: "consent_state_invalid", purpose, policy_version: state && state.policy_version || null };
  if (!PURPOSE_SET.has(purpose)) return mutation === "allow-unknown-purpose" ? { allowed: true, reason: "mutation_unknown", purpose, policy_version: state.policy_version } : { allowed: false, reason: "purpose_unknown", purpose, policy_version: state.policy_version };
  const value = state.purposes[purpose];
  if (purpose === "necessary") return { allowed: true, reason: "necessary_basis", purpose, policy_version: state.policy_version };
  if (mutation === "allow-marketing-unset" && purpose === "marketing" && value.status === "unset") return { allowed: true, reason: "mutation_unset", purpose, policy_version: state.policy_version };
  return value.status === "granted"
    ? { allowed: true, reason: "consent_granted", purpose, policy_version: state.policy_version }
    : { allowed: false, reason: value.status === "denied" ? "consent_denied" : "consent_unset", purpose, policy_version: state.policy_version };
}

function validateConsentTransition(previous, next, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  const before = validateConsentState(previous, options), after = validateConsentState(next, options);
  errors.push(...before.errors.map((error) => "previous:" + error), ...after.errors.map((error) => "next:" + error));
  if (!before.ok || !after.ok) return { ok: false, errors: [...new Set(errors)] };
  if (mutation !== "allow-subject-switch" && (previous.tenant_id !== next.tenant_id || previous.subject_type !== next.subject_type || previous.subject_id !== next.subject_id)) errors.push("page_consent_subject_changed");
  if (previous.created_at !== next.created_at) errors.push("page_consent_created_at_changed");
  if (next.record_version !== previous.record_version + 1) errors.push("page_consent_transition_version_invalid");
  if (Date.parse(next.updated_at) <= Date.parse(previous.updated_at)) errors.push("page_consent_transition_time_not_advanced");
  for (const purpose of PURPOSES) {
    const a = previous.purposes[purpose], b = next.purposes[purpose];
    if (JSON.stringify(a) !== JSON.stringify(b) && mutation !== "allow-stale-decision") {
      if (!date(b.decided_at) || (date(a.decided_at) && Date.parse(b.decided_at) <= Date.parse(a.decided_at))) errors.push("page_consent_decision_time_not_advanced:" + purpose);
    }
  }
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

// ── 同意紀錄的保留期與刪除(2026-07-28 新增)────────────────────────────────
//
// ★★★ 這一格**現在是空的,而且刻意讓它看得出來是空的**。
//   保留多久、怎麼刪,是 the maintainer 的決定,不是預設值能代勞的 ——
//   而「沒有決定」最常見的下場是安靜地變成「永久保留」,因為那是不寫程式的結果。
//   所以這裡用三態:decided / undecided,**沒有布林 `ok`**(有了就會被壓成兩態),
//   而清理程序在 undecided 時回 `denied`,失敗方向是**不敢刪**不是預設刪或預設留。
//
// ★★ 為什麼刪除模式必須跟保留期一起決定:同一台機器上有備份、又要支援刪除,
//   **硬刪 + 備份還原 = 已撤回的同意無聲復活** —— 與重送舊 `order_paid`
//   讓已退款權益復活是同一個形狀。墓碑(留骨架、清可識別欄位)才認得出「這筆撤回過」。
const RETENTION_STATUSES = Object.freeze(["decided", "undecided"]);
const DELETION_MODES = Object.freeze(["tombstone", "hard"]);

const RETENTION_POLICY = Object.freeze({
  status: "undecided",
  keep_after_withdrawal_days: null,
  deletion_mode: null,
  decided_by: null,
  decided_at: null
});

function validateRetentionPolicy(policy) {
  const p = policy || {};
  const errors = [];
  if (!RETENTION_STATUSES.includes(p.status)) errors.push("retention_status_invalid");
  if (p.status === "undecided") {
    // ★ 未決就不可以帶著看起來像已決的值 —— 半填的政策比空的更危險,
    //   因為讀的人會以為有人決定過。
    if (p.keep_after_withdrawal_days !== null || p.deletion_mode !== null
      || p.decided_by !== null || p.decided_at !== null) errors.push("undecided_must_not_carry_values");
  } else {
    if (!Number.isInteger(p.keep_after_withdrawal_days) || p.keep_after_withdrawal_days < 0) {
      errors.push("keep_days_invalid");
    }
    if (!DELETION_MODES.includes(p.deletion_mode)) errors.push("deletion_mode_invalid");
    // ★ 決定要留下是誰決定的。法遵軸上「有政策」與「有人負責」是兩件事。
    if (!String(p.decided_by || "").trim()) errors.push("decided_by_missing");
    if (!Number.isFinite(Date.parse(String(p.decided_at || "")))) errors.push("decided_at_invalid");
  }
  return { errors: Object.freeze([...new Set(errors)]) };
}

// 清理程序問這裡:現在可以刪嗎?
// ★ 三態與 `canProcessPurpose` 對齊:allowed / denied,理由一律點名。
//   **未決 = denied**,而且理由是 `retention_undecided` 不是別的 ——
//   它要讀起來像「還沒有人決定」,不是像「政策說不行」。
function retentionSweepDecision(policy) {
  const p = policy || RETENTION_POLICY;
  const validation = validateRetentionPolicy(p);
  if (validation.errors.length) {
    return { allowed: false, reason: "retention_policy_invalid", errors: validation.errors };
  }
  if (p.status === "undecided") {
    return { allowed: false, reason: "retention_undecided", errors: Object.freeze([]) };
  }
  return {
    allowed: true,
    reason: "retention_decided",
    keep_after_withdrawal_days: p.keep_after_withdrawal_days,
    deletion_mode: p.deletion_mode,
    errors: Object.freeze([])
  };
}

// ★★ 這支存在的唯一理由:證明上面那條路**真的擋得住**。
//   守衛沒有負向對照就跟沒有守衛一樣 —— 它會在無人注意時變成永遠 allowed。
function auditRetentionPolicy() {
  const errors = [];

  if (retentionSweepDecision(RETENTION_POLICY).allowed !== false) errors.push("undecided_must_not_allow_sweep");
  if (retentionSweepDecision(RETENTION_POLICY).reason !== "retention_undecided") errors.push("undecided_reason_lost");

  // 半填的未決政策要被擋下來。
  const halfFilled = { status: "undecided", keep_after_withdrawal_days: 1095, deletion_mode: null, decided_by: null, decided_at: null };
  if (retentionSweepDecision(halfFilled).allowed !== false) errors.push("half_filled_undecided_must_not_allow");

  // 決定過的政策要放行,否則這個閘門就只是永遠說不。
  const decided = {
    status: "decided", keep_after_withdrawal_days: 1095, deletion_mode: "tombstone",
    decided_by: "maintainer", decided_at: "2026-07-28T00:00:00.000Z"
  };
  const ok = retentionSweepDecision(decided);
  if (ok.allowed !== true || ok.deletion_mode !== "tombstone") errors.push("decided_policy_must_allow_sweep");

  // 缺負責人的「已決」不算已決。
  const noOwner = Object.assign({}, decided, { decided_by: "" });
  if (retentionSweepDecision(noOwner).allowed !== false) errors.push("decided_without_owner_must_not_allow");

  return { errors: Object.freeze([...new Set(errors)]), version: VERSION };
}

module.exports = {
  PURPOSES, VERSION, canProcessPurpose, validateConsentState, validateConsentTransition,
  RETENTION_STATUSES, DELETION_MODES, RETENTION_POLICY,
  validateRetentionPolicy, retentionSweepDecision, auditRetentionPolicy
};
