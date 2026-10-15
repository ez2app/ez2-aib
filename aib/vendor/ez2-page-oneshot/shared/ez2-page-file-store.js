"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const domain = require("./ez2-page-domain-contract.js");
const blocks = require("./ez2-page-block-contract.js");
const routes = require("./ez2-page-route-contract.js");

const STORE_VERSION = "2026-07-11.ez2-page-file-store.v1";
function copy(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function emptyEnvelope() { return { store_version: STORE_VERSION, records: {} }; }

function validateRecord(recordKey, value) {
  const errors = [];
  if (!value || typeof value !== "object" || Array.isArray(value)) return [recordKey + ":record_invalid"];
  const page = domain.validatePageRecord(value.page);
  if (!page.ok) errors.push(...page.errors.map((error) => recordKey + ":" + error));
  const blockSet = domain.validatePageRecord(value.page).ok ? blocks.validatePageBlockSet(value.page, value.blocks) : { ok: false, errors: [] };
  if (!blockSet.ok) errors.push(...blockSet.errors.map((error) => recordKey + ":" + error));
  const routeSet = domain.validatePageRecord(value.page).ok ? routes.validatePageRouteSet(value.page, value.routes) : { ok: false, errors: [] };
  if (!routeSet.ok) errors.push(...routeSet.errors.map((error) => recordKey + ":" + error));
  if (value.page && recordKey !== value.page.tenant_id + "\u0000" + value.page.page_id) errors.push(recordKey + ":store_key_mismatch");
  if (Object.keys(value).some((key) => !["page", "blocks", "routes"].includes(key))) errors.push(recordKey + ":record_unknown_fields");
  return errors;
}

function validateEnvelope(envelope) {
  const errors = [];
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope) || envelope.store_version !== STORE_VERSION || !envelope.records || typeof envelope.records !== "object" || Array.isArray(envelope.records) || Object.keys(envelope).some((key) => !["store_version", "records"].includes(key))) return ["page_store_envelope_invalid"];
  for (const [recordKey, value] of Object.entries(envelope.records)) errors.push(...validateRecord(recordKey, value));
  return [...new Set(errors)];
}

function createPageFileRepository(options = {}) {
  const file = path.resolve(String(options.file || ""));
  if (!options.file) fail("PAGE_STORE_FILE_REQUIRED");
  const lockFile = file + ".lock";
  const mutation = String(options.test_mutation || "");
  const rename = options.rename_sync || fs.renameSync;

  function readEnvelope() {
    if (!fs.existsSync(file)) return emptyEnvelope();
    let parsed;
    try { parsed = JSON.parse(fs.readFileSync(file, "utf8")); }
    catch (error) { fail("PAGE_STORE_CORRUPT", [error.message]); }
    if (mutation !== "skip-store-validation") {
      const errors = validateEnvelope(parsed);
      if (errors.length) fail("PAGE_STORE_CORRUPT", errors);
    }
    return parsed;
  }

  function writeEnvelope(envelope) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const body = JSON.stringify(envelope, null, 2) + "\n";
    if (mutation === "skip-atomic-rename") {
      fs.writeFileSync(file, body, { mode: 0o600 });
      return;
    }
    const temp = file + ".tmp-" + process.pid + "-" + crypto.randomBytes(5).toString("hex");
    try {
      fs.writeFileSync(temp, body, { mode: 0o600 });
      rename(temp, file);
    } catch (error) {
      try { fs.unlinkSync(temp); } catch (_) {}
      throw error;
    }
  }

  function withLock(fn) {
    if (mutation === "skip-lock") return fn();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    let fd;
    try { fd = fs.openSync(lockFile, "wx", 0o600); }
    catch (_) { fail("PAGE_STORE_LOCKED"); }
    try { return fn(); }
    finally { try { fs.closeSync(fd); } catch (_) {} try { fs.unlinkSync(lockFile); } catch (_) {} }
  }

  return Object.freeze({
    get(recordKey) { return copy(readEnvelope().records[recordKey] || null); },
    has(recordKey) { return Object.prototype.hasOwnProperty.call(readEnvelope().records, recordKey); },
    set(recordKey, value, expectedRevisionId) {
      return withLock(() => {
        const envelope = readEnvelope();
        const current = envelope.records[recordKey] || null;
        if (mutation !== "skip-cas") {
          if (expectedRevisionId === null && current) fail("PAGE_STORE_CONFLICT");
          if (typeof expectedRevisionId === "string" && (!current || current.page.revision_id !== expectedRevisionId)) fail("PAGE_STORE_CONFLICT");
        }
        const next = copy(value);
        const errors = validateRecord(recordKey, next);
        if (errors.length) fail("PAGE_STORE_RECORD_INVALID", errors);
        envelope.records[recordKey] = next;
        writeEnvelope(envelope);
      });
    },
    values() { return Object.values(readEnvelope().records).map(copy); }
  });
}

module.exports = { STORE_VERSION, createPageFileRepository, validateEnvelope, validateRecord };
