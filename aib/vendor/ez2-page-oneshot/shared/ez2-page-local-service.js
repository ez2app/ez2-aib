"use strict";

const domain = require("./ez2-page-domain-contract.js");
const blockContract = require("./ez2-page-block-contract.js");
const routeContract = require("./ez2-page-route-contract.js");
const authorization = require("./ez2-page-tenant-authorization.js");

function copy(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
function key(tenantId, pageId) { return tenantId + "\u0000" + pageId; }
function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function requireOk(result, code) { if (!result.ok) fail(code, result.errors); }

// Statuses a visitor could actually have reached (so the address must survive).
const REACHABLE_ROUTE_STATUSES = new Set(["active", "redirecting", "suspended"]);

function createMemoryPageRepository() {
  const records = new Map();
  return Object.freeze({
    get(recordKey) { return records.get(recordKey) || null; },
    has(recordKey) { return records.has(recordKey); },
    set(recordKey, value, expectedRevisionId) {
      const current = records.get(recordKey);
      if (expectedRevisionId === null && current) fail("PAGE_STORE_CONFLICT");
      if (typeof expectedRevisionId === "string" && (!current || current.page.revision_id !== expectedRevisionId)) fail("PAGE_STORE_CONFLICT");
      records.set(recordKey, value);
    },
    values() { return Array.from(records.values()); }
  });
}

function createLocalPageService(options = {}) {
  const mutation = String(options.test_mutation || "");
  const records = options.repository || createMemoryPageRepository();
  const outward = mutation === "return-live-reference" ? (value) => value : copy;

  function authorize(page, actor, operation) {
    const result = authorization.authorizePageAccess({ page, actor, operation });
    if (!result.allowed && mutation !== "bypass-authorization") fail("PAGE_AUTH_FORBIDDEN", [result.reason]);
    return result;
  }

  function validateComposition(page, blocks, routes) {
    requireOk(domain.validatePageRecord(page), "PAGE_RECORD_INVALID");
    if (mutation !== "skip-block-composition") requireOk(blockContract.validatePageBlockSet(page, blocks), "PAGE_BLOCKS_INVALID");
    if (mutation !== "skip-route-composition") requireOk(routeContract.validatePageRouteSet(page, routes), "PAGE_ROUTES_INVALID");
  }

  // Route transition enforcement (the "links never break" invariant).
  // The route contract freezes hostname/path/type and constrains the status
  // lifecycle, but until this was wired nothing called it from the service: a
  // caller could rewrite an existing route's hostname, skip the version
  // increment, or jump a forbidden status, and the composition check would still
  // pass because the resulting set was internally valid. Upgrading a domain must
  // stay "add a route and switch primary", never "edit the route people already
  // have", and a route a published page once served must be retired through the
  // status lifecycle rather than vanishing from the set.
  function validateRouteHistory(previousPage, previousRoutes, nextRoutes) {
    const before = new Map((previousRoutes || []).map((route) => [route.route_id, route]));
    if (mutation !== "skip-route-transition-enforcement") {
      for (const route of nextRoutes || []) {
        const earlier = before.get(route.route_id);
        // Only changed routes are transitions. An untouched route carries the same
        // version by definition, and the contract models a route change as a status
        // transition (its TRANSITIONS table has no self-edges) — so validating an
        // unchanged route would fail every ordinary text edit.
        if (!earlier || JSON.stringify(earlier) === JSON.stringify(route)) continue;
        requireOk(routeContract.validatePageRouteTransition(earlier, route), "PAGE_ROUTE_TRANSITION_INVALID");
      }
    }
    if (mutation === "allow-route-removal") return;
    const survives = new Set((nextRoutes || []).map((route) => route.route_id));
    const vanished = [];
    for (const [routeId, route] of before) {
      if (survives.has(routeId)) continue;
      // The protected fact is the ROUTE's reachability, not the page's current
      // status: an address a visitor could reach stays protected even after the
      // page flips back to draft. Keying this on "the page was published" left an
      // obvious bypass (flip to draft, then delete the route), which the smoke
      // caught. A never-served route (requested / verifying / quarantined /
      // detached / expired) may still be dropped.
      if (REACHABLE_ROUTE_STATUSES.has(route.status)) vanished.push(routeId + ":" + route.hostname + route.path);
    }
    if (vanished.length) fail("PAGE_ROUTE_REMOVAL_FORBIDDEN", vanished);
  }

  function createDraft(input) {
    const page = copy(input && input.page), blocks = copy(input && input.blocks || []), routes = copy(input && input.routes || []);
    if (!page || page.status !== "draft") fail("PAGE_CREATE_REQUIRES_DRAFT");
    authorize(page, input && input.actor, "edit");
    validateComposition(page, blocks, routes);
    const recordKey = key(page.tenant_id, page.page_id);
    if (records.has(recordKey)) fail("PAGE_ALREADY_EXISTS");
    records.set(recordKey, { page, blocks, routes }, null);
    return outward(records.get(recordKey));
  }

  function revise(input) {
    const nextPage = copy(input && input.page), nextBlocks = copy(input && input.blocks || []), nextRoutes = copy(input && input.routes || []);
    if (!nextPage) fail("PAGE_RECORD_REQUIRED");
    const recordKey = key(nextPage.tenant_id, nextPage.page_id);
    const current = records.get(recordKey);
    if (!current) fail("PAGE_NOT_FOUND");
    authorize(current.page, input && input.actor, "edit");
    if (mutation !== "skip-domain-transition") requireOk(domain.validatePageTransition(current.page, nextPage), "PAGE_TRANSITION_INVALID");
    validateComposition(nextPage, nextBlocks, nextRoutes);
    const oldBlocks = new Map(current.blocks.map((block) => [block.block_id, block]));
    for (const block of nextBlocks) if (oldBlocks.has(block.block_id)) requireOk(blockContract.validatePageBlockTransition(oldBlocks.get(block.block_id), block), "PAGE_BLOCK_TRANSITION_INVALID");
    validateRouteHistory(current.page, current.routes, nextRoutes);
    records.set(recordKey, { page: nextPage, blocks: nextBlocks, routes: nextRoutes }, current.page.revision_id);
    return outward(records.get(recordKey));
  }

  function publish(input) {
    const nextPage = copy(input && input.page), nextBlocks = copy(input && input.blocks || []), nextRoutes = copy(input && input.routes || []);
    if (!nextPage) fail("PAGE_RECORD_REQUIRED");
    const recordKey = key(nextPage.tenant_id, nextPage.page_id);
    const current = records.get(recordKey);
    if (!current) fail("PAGE_NOT_FOUND");
    authorize(current.page, input && input.actor, "publish");
    if (!domain.PUBLISHED.has(nextPage.status)) fail("PAGE_PUBLISH_REQUIRES_PUBLISHED_STATUS");
    if (mutation !== "skip-domain-transition") requireOk(domain.validatePageTransition(current.page, nextPage), "PAGE_TRANSITION_INVALID");
    validateComposition(nextPage, nextBlocks, nextRoutes);
    validateRouteHistory(current.page, current.routes, nextRoutes);
    records.set(recordKey, { page: nextPage, blocks: nextBlocks, routes: nextRoutes }, current.page.revision_id);
    return outward(records.get(recordKey));
  }

  function read(input) {
    const current = records.get(key(input && input.tenant_id, input && input.page_id));
    if (!current) fail("PAGE_NOT_FOUND");
    authorize(current.page, input && input.actor || null, input && input.actor ? "read_private" : "read_public");
    return outward(current);
  }

  function inspect() { return outward(records.values()); }

  return Object.freeze({ createDraft, inspect, publish, read, revise });
}

module.exports = { createLocalPageService, createMemoryPageRepository };
