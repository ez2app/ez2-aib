"use strict";

const pageDomain = require("./ez2-page-domain-contract.js");

const POLICY_VERSION = "2026-07-11.ez2-page-tenant-auth.v1";
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$/;
const ROLES = Object.freeze(["owner", "admin", "editor", "publisher", "analyst", "viewer"]);
const ROLE_SET = new Set(ROLES);
const OPERATIONS = Object.freeze(["read_public", "read_private", "edit", "manage_blocks", "publish", "bind_route", "view_analytics", "archive"]);
const OPERATION_SET = new Set(OPERATIONS);
const POLICY = Object.freeze({
  read_private: new Set(ROLES),
  edit: new Set(["owner", "admin", "editor"]),
  manage_blocks: new Set(["owner", "admin", "editor"]),
  publish: new Set(["owner", "admin", "publisher"]),
  bind_route: new Set(["owner", "admin", "publisher"]),
  view_analytics: new Set(["owner", "admin", "analyst"]),
  archive: new Set(["owner", "admin"])
});
const INPUT_FIELDS = new Set(["page", "actor", "operation"]);
const ACTOR_FIELDS = new Set(["identity_id", "tenant_id", "roles", "authenticated"]);

function plain(value) { if (!value || typeof value !== "object" || Array.isArray(value)) return false; const proto = Object.getPrototypeOf(value); return proto === Object.prototype || proto === null; }
function id(value) { return typeof value === "string" && ID_RE.test(value); }
function exact(value, fields) { return plain(value) && Object.keys(value).every((key) => fields.has(key)); }
function decision(allowed, reason, operation, page, actor) {
  return Object.freeze({
    allowed,
    reason,
    policy_version: POLICY_VERSION,
    operation: operation || null,
    tenant_id: page && page.tenant_id || null,
    page_id: page && page.page_id || null,
    actor_identity_id: actor && actor.identity_id || null
  });
}

function validateActor(actor) {
  const errors = [];
  if (!exact(actor, ACTOR_FIELDS)) return ["page_auth_actor_invalid"];
  if (!id(actor.identity_id) || !id(actor.tenant_id)) errors.push("page_auth_actor_identity_invalid");
  if (actor.authenticated !== true) errors.push("page_auth_actor_not_authenticated");
  if (!Array.isArray(actor.roles) || !actor.roles.length || !actor.roles.every((role) => ROLE_SET.has(role)) || new Set(actor.roles).size !== actor.roles.length) errors.push("page_auth_actor_roles_invalid");
  return errors;
}

function authorizePageAccess(input, options = {}) {
  const mutation = String(options.test_mutation || "");
  if (!exact(input, INPUT_FIELDS)) return decision(false, "input_invalid", null, null, null);
  const pageResult = pageDomain.validatePageRecord(input.page);
  if (!pageResult.ok) return decision(false, "page_invalid", input.operation, input.page, input.actor);
  if (!OPERATION_SET.has(input.operation)) {
    return mutation === "allow-unknown-operation"
      ? decision(true, "mutation_unknown_operation", input.operation, input.page, input.actor)
      : decision(false, "operation_unknown", input.operation, input.page, input.actor);
  }

  if (input.operation === "read_public") {
    const publicStatus = pageDomain.PUBLISHED.has(input.page.status) || (mutation === "allow-suspended-public" && input.page.status === "suspended");
    return publicStatus ? decision(true, "public_page", input.operation, input.page, input.actor) : decision(false, "page_not_public", input.operation, input.page, input.actor);
  }

  if (input.actor == null && mutation !== "allow-anonymous-edit") return decision(false, "authentication_required", input.operation, input.page, null);
  if (input.actor == null) return decision(true, "mutation_anonymous", input.operation, input.page, null);
  const actorErrors = validateActor(input.actor);
  if (actorErrors.length) return decision(false, actorErrors[0], input.operation, input.page, input.actor);
  if (mutation !== "allow-cross-tenant" && input.actor.tenant_id !== input.page.tenant_id) return decision(false, "tenant_mismatch", input.operation, input.page, input.actor);

  const allowedRoles = POLICY[input.operation];
  if (!allowedRoles) return decision(false, "operation_policy_missing", input.operation, input.page, input.actor);
  const roles = mutation === "allow-editor-publish" && input.operation === "publish" ? new Set([...allowedRoles, "editor"]) : allowedRoles;
  if (!input.actor.roles.some((role) => roles.has(role))) return decision(false, "role_forbidden", input.operation, input.page, input.actor);
  return decision(true, "role_allowed", input.operation, input.page, input.actor);
}

module.exports = { OPERATIONS, POLICY, POLICY_VERSION, ROLES, authorizePageAccess, validateActor };
