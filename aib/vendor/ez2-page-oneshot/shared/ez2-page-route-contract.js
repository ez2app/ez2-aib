"use strict";

const pageDomain = require("./ez2-page-domain-contract.js");

const VERSION = "2026-07-11.ez2-page-route.v1";
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$/;
const HOST_RE = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const ROUTE_TYPES = Object.freeze(["platform_path", "platform_subdomain", "custom_domain", "legacy_redirect", "preview"]);
const ROUTE_TYPE_SET = new Set(ROUTE_TYPES);
const STATUSES = Object.freeze(["requested", "verifying", "active", "redirecting", "suspended", "detached", "quarantined", "expired"]);
const STATUS_SET = new Set(STATUSES);
const TRANSITIONS = Object.freeze({
  requested: new Set(["verifying", "detached", "quarantined", "expired"]),
  verifying: new Set(["active", "quarantined", "detached", "expired"]),
  active: new Set(["redirecting", "suspended", "detached", "quarantined"]),
  redirecting: new Set(["active", "detached", "expired"]),
  suspended: new Set(["active", "detached", "quarantined"]),
  detached: new Set(["expired"]),
  quarantined: new Set(["verifying", "detached", "expired"]),
  expired: new Set()
});
// ★ 保留名稱的真相源已收斂到 ez2-page-reserved-names。
//   在此之前這裡與 ez2-page-public-locale-route 各有一份,而且互相不是超集
//   (這份有 app 沒 mail,那份有 mail 沒 app),`usermailto` 則是兩份都沒有 ——
//   而那是活著的寄信網域。兩份不同的答案就是兩個真相源。
const reservedNames = require("./ez2-page-reserved-names.js");
// 保留匯出形狀給既有消費端;內容由單一真相源導出,不在這裡另立一份。
const RESERVED_SUBDOMAINS = new Set(reservedNames.ALL_RESERVED);
const ALLOWED_FIELDS = new Set([
  "schema_version", "tenant_id", "page_id", "route_id", "route_type", "hostname", "path",
  "status", "is_primary", "redirect_to", "version", "verified_at", "activated_at",
  "detached_at", "created_at", "updated_at"
]);

function plain(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function id(value) { return typeof value === "string" && ID_RE.test(value); }
function date(value) { return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value)); }
function nullableId(value) { return value == null || id(value); }
function nullableDate(value) { return value == null || date(value); }

function normalizeHostname(value) {
  if (typeof value !== "string") return null;
  const host = value.trim().toLowerCase().replace(/\.$/, "");
  return HOST_RE.test(host) ? host : null;
}

function normalizePath(value) {
  if (typeof value !== "string" || !value.startsWith("/") || /[?#\\]/.test(value) || /(?:^|\/)\.\.(?:\/|$)/.test(value)) return null;
  const collapsed = value.replace(/\/{2,}/g, "/");
  if (collapsed.length > 512 || /%2f|%5c|%2e/i.test(collapsed)) return null;
  return collapsed.length > 1 ? collapsed.replace(/\/$/, "") : collapsed;
}

function endpointKey(route) {
  const host = normalizeHostname(route && route.hostname);
  const pathname = normalizePath(route && route.path);
  return host && pathname ? host + pathname : null;
}

function typeErrors(route, host, pathname) {
  const errors = [];
  if (route.route_type === "platform_path") {
    const slug = pathname && pathname.slice(1);
    // ★ 保留字檢查在本次之前**完全不存在於這一階** —— 只有子網域被擋。
    //   也就是說 `ez2.page/register` 一度是可以被註冊的,而那正是我們自己的註冊頁;
    //   `/manage`、`/go`、`/privacy`、`/complaints` 同理。
    if (host !== "ez2.page" || !slug || slug.includes("/") || !SLUG_RE.test(slug)
      || !reservedNames.checkPathSegment(slug).ok) errors.push("page_route_platform_path_invalid");
  } else if (route.route_type === "platform_subdomain") {
    const suffix = ".ez2.page";
    const label = host && host.endsWith(suffix) ? host.slice(0, -suffix.length) : "";
    if (!label || label.includes(".") || !SLUG_RE.test(label) || pathname !== "/"
      || !reservedNames.checkSubdomain(label).ok) errors.push("page_route_platform_subdomain_invalid");
  } else if (route.route_type === "custom_domain") {
    if (!host || host === "ez2.page" || host.endsWith(".ez2.page") || pathname !== "/") errors.push("page_route_custom_domain_invalid");
  } else if (route.route_type === "preview") {
    if (host !== "preview.ez2.page" || !pathname || !/^\/p\/[A-Za-z0-9_.:-]+$/.test(pathname)) errors.push("page_route_preview_invalid");
  }
  return errors;
}

function validatePageRoute(route) {
  const errors = [];
  if (!plain(route)) return { ok: false, errors: ["page_route_record_invalid"] };
  if (Object.keys(route).some((key) => !ALLOWED_FIELDS.has(key))) errors.push("page_route_unknown_fields");
  if (route.schema_version !== VERSION) errors.push("page_route_schema_version_invalid");
  for (const field of ["tenant_id", "page_id", "route_id"]) if (!id(route[field])) errors.push("page_route_" + field + "_invalid");
  if (!ROUTE_TYPE_SET.has(route.route_type)) errors.push("page_route_type_invalid");
  const host = normalizeHostname(route.hostname);
  const pathname = normalizePath(route.path);
  if (!host || host !== route.hostname) errors.push("page_route_hostname_invalid");
  if (!pathname || pathname !== route.path) errors.push("page_route_path_invalid");
  errors.push(...typeErrors(route, host, pathname));
  if (!STATUS_SET.has(route.status)) errors.push("page_route_status_invalid");
  if (typeof route.is_primary !== "boolean") errors.push("page_route_primary_invalid");
  if (!nullableId(route.redirect_to)) errors.push("page_route_redirect_target_invalid");
  if (!Number.isInteger(route.version) || route.version < 1) errors.push("page_route_version_invalid");
  for (const field of ["verified_at", "activated_at", "detached_at"]) if (!nullableDate(route[field])) errors.push("page_route_" + field + "_invalid");
  if (!date(route.created_at) || !date(route.updated_at)) errors.push("page_route_timestamp_invalid");
  if (date(route.created_at) && date(route.updated_at) && Date.parse(route.updated_at) < Date.parse(route.created_at)) errors.push("page_route_updated_before_created");
  if (route.status === "active" && !date(route.activated_at)) errors.push("page_route_active_requires_activation");
  if (route.route_type === "custom_domain" && route.status === "active" && !date(route.verified_at)) errors.push("page_route_custom_active_requires_verification");
  if (route.status === "redirecting" && !id(route.redirect_to)) errors.push("page_route_redirecting_requires_target");
  if (route.status === "detached" && !date(route.detached_at)) errors.push("page_route_detached_requires_time");
  if (route.redirect_to === route.route_id) errors.push("page_route_self_redirect");
  if (route.is_primary && (route.status !== "active" || route.redirect_to != null || ["preview", "legacy_redirect"].includes(route.route_type))) errors.push("page_route_primary_not_servable");
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

function validatePageRouteSet(page, routes, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  const pageResult = pageDomain.validatePageRecord(page);
  if (!pageResult.ok) errors.push(...pageResult.errors.map((error) => "page:" + error));
  if (!Array.isArray(routes)) return { ok: false, errors: [...errors, "page_routes_invalid"] };
  const validRoutes = [];
  for (const route of routes) {
    const result = validatePageRoute(route);
    if (!result.ok) errors.push(...result.errors.map((error) => (route && route.route_id || "unknown") + ":" + error));
    else validRoutes.push(route);
  }
  if (errors.length) return { ok: false, errors: [...new Set(errors)] };

  if (mutation !== "allow-cross-tenant-set" && validRoutes.some((route) => route.tenant_id !== page.tenant_id || route.page_id !== page.page_id)) errors.push("page_routes_cross_tenant_or_page");
  if (new Set(validRoutes.map((route) => route.route_id)).size !== validRoutes.length) errors.push("page_routes_duplicate_id");
  if (mutation !== "allow-duplicate-endpoint" && new Set(validRoutes.map(endpointKey)).size !== validRoutes.length) errors.push("page_routes_duplicate_endpoint");
  const primary = validRoutes.filter((route) => route.is_primary);
  if (mutation !== "allow-two-primary" && primary.length > 1) errors.push("page_routes_multiple_primary");
  if (pageDomain.PUBLISHED.has(page.status) && primary.length !== 1) errors.push("page_published_requires_one_primary_route");
  if (page.primary_route_id == null && primary.length) errors.push("page_primary_route_reference_missing");
  if (page.primary_route_id != null && (primary.length !== 1 || primary[0].route_id !== page.primary_route_id)) errors.push("page_primary_route_reference_mismatch");

  const byId = new Map(validRoutes.map((route) => [route.route_id, route]));
  for (const route of validRoutes.filter((item) => item.redirect_to != null)) {
    const target = byId.get(route.redirect_to);
    if (!target) errors.push("page_route_redirect_target_missing");
    else if (mutation !== "allow-redirect-chain" && (!target.is_primary || target.redirect_to != null || target.status !== "active")) errors.push("page_route_redirect_target_not_primary");
  }
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

function validatePageRouteTransition(previous, next, options = {}) {
  const errors = [];
  const mutation = String(options.test_mutation || "");
  const before = validatePageRoute(previous);
  const after = validatePageRoute(next);
  errors.push(...before.errors.map((error) => "previous:" + error), ...after.errors.map((error) => "next:" + error));
  if (!before.ok || !after.ok) return { ok: false, errors: [...new Set(errors)] };
  for (const field of ["tenant_id", "page_id", "route_id", "route_type", "hostname", "path", "created_at"]) {
    if (previous[field] !== next[field]) errors.push("page_route_transition_identity_changed");
  }
  if (next.version !== previous.version + 1) errors.push("page_route_transition_version_invalid");
  if (Date.parse(next.updated_at) <= Date.parse(previous.updated_at)) errors.push("page_route_transition_time_not_advanced");
  if (mutation !== "skip-route-transition" && !TRANSITIONS[previous.status].has(next.status)) errors.push("page_route_transition_status_forbidden");
  return { ok: errors.length === 0, errors: [...new Set(errors)] };
}

module.exports = {
  ALLOWED_FIELDS, ROUTE_TYPES, STATUSES, TRANSITIONS, VERSION,
  endpointKey, normalizeHostname, normalizePath,
  validatePageRoute, validatePageRouteSet, validatePageRouteTransition
};
