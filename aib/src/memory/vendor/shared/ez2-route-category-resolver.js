// SPDX-License-Identifier: Apache-2.0
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const DEFAULT_CATEGORY_REGISTRY = path.join(ROOT, "samples/ez2-metadata/category-registry.valid.example.json");

function fallbackCategoryUid(routeKey) {
  return "cat_" + String(routeKey || "core.inbox").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function normalizePrivacyLevel(value) {
  const level = String(value || "").toLowerCase();
  if (/sensitive|敏感/.test(level)) return "sensitive";
  if (/restricted|機密|限制/.test(level)) return "restricted";
  if (/public|公開/.test(level)) return "public";
  return "internal";
}

function readCategoryRegistry(registryPath = DEFAULT_CATEGORY_REGISTRY) {
  return JSON.parse(fs.readFileSync(registryPath, "utf8"));
}

function resolveRouteCategory(route, options = {}) {
  const routeKey = String(route && route.routeKey || "core.inbox");
  const registryPath = options.registryPath || DEFAULT_CATEGORY_REGISTRY;
  let registry = null;
  try {
    registry = options.registry || readCategoryRegistry(registryPath);
  } catch (e) {
    registry = null;
  }
  const categories = registry && Array.isArray(registry.categories) ? registry.categories : [];
  const match = categories.find((item) => item && item.routeKey === routeKey && item.deprecated !== true && item.canonical !== false);
  if (match) {
    const legacyPaths = Array.isArray(match.legacy_paths) ? match.legacy_paths.filter(Boolean) : [];
    return {
      category_uid: match.category_uid,
      parent_category_uid: match.parent_uid || "",
      canonical_category_uid: match.replacement_uid || match.category_uid,
      category_registry_ref: registry.registry_uid || "",
      legacy_category_path: legacyPaths[0] || "",
      categoryFallbackUsed: false,
      privacy_level: match.privacy_level,
      ai_readable: match.ai_readable,
      ai_writable: match.ai_writable,
      registry_uid: registry.registry_uid || "",
      routeKey
    };
  }
  return {
    category_uid: fallbackCategoryUid(routeKey),
    categoryFallbackUsed: true,
    privacy_level: normalizePrivacyLevel(route && route.dataLevel),
    ai_readable: route ? route.aiReadable !== false : true,
    ai_writable: route ? route.aiWritable === true : false,
    registry_uid: registry && registry.registry_uid || "",
    routeKey
  };
}

// P1 反查:category_uid -> folder_path(讓 P0 鑄的分類身份真的可被查)。
// 相容兩種來源:example registry(parent_uid/label)與 P0 聚合器輸出(parent_category_uid/title)。
function folderForCategoryUid(categoryUid, options = {}) {
  const uid = String(categoryUid || "");
  let registry = null;
  try {
    registry = options.registry || readCategoryRegistry(options.registryPath || DEFAULT_CATEGORY_REGISTRY);
  } catch (e) {
    registry = null;
  }
  const categories = registry && Array.isArray(registry.categories) ? registry.categories : [];
  const match = categories.find((c) => c && c.category_uid === uid && c.deprecated !== true);
  if (match) {
    return {
      found: true,
      category_uid: match.category_uid,
      folder_path: match.folder_path || "",
      parent_category_uid: match.parent_category_uid || match.parent_uid || "",
      title: match.title || match.label || "",
      legacy_paths: Array.isArray(match.legacy_paths) ? match.legacy_paths.filter(Boolean) : [],
      routeKey: match.routeKey || ""
    };
  }
  return { found: false, category_uid: uid, folder_path: "", parent_category_uid: "", title: "", legacy_paths: [], routeKey: "" };
}

// 讀 vault 內的 live 分類註冊表(聚合器產物 <vault>/.ez2/category-registry.json)。
function loadLiveCategoryRegistry(vaultPath) {
  return readCategoryRegistry(path.join(String(vaultPath || ""), ".ez2", "category-registry.json"));
}

// P2 正查:folder_path -> 分類(讓任何有資料夾的東西反推分類身份)。完整比對,不吃前綴。
function categoryForFolder(folderPath, options = {}) {
  const folder = String(folderPath || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  let registry = null;
  try {
    registry = options.registry || readCategoryRegistry(options.registryPath || DEFAULT_CATEGORY_REGISTRY);
  } catch (e) {
    registry = null;
  }
  const categories = registry && Array.isArray(registry.categories) ? registry.categories : [];
  const match = categories.find((c) => c && c.deprecated !== true && String(c.folder_path || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "") === folder);
  if (match) {
    return {
      found: true,
      category_uid: match.category_uid,
      folder_path: match.folder_path || "",
      parent_category_uid: match.parent_category_uid || match.parent_uid || "",
      title: match.title || match.label || "",
      routeKey: match.routeKey || ""
    };
  }
  return { found: false, category_uid: "", folder_path: folder, parent_category_uid: "", title: "", routeKey: "" };
}

// P2 便利:note 的 vault 相對路徑 -> 它所屬分類的 category_uid(取 note 所在資料夾再正查)。
function categoryUidForNote(noteRelPath, options = {}) {
  const rel = String(noteRelPath || "").replace(/\\/g, "/").replace(/^\/+/, "");
  const folder = rel.includes("/") ? rel.split("/").slice(0, -1).join("/") : "";
  const hit = categoryForFolder(folder, options);
  return hit.found ? hit.category_uid : "";
}

module.exports = {
  DEFAULT_CATEGORY_REGISTRY,
  fallbackCategoryUid,
  normalizePrivacyLevel,
  readCategoryRegistry,
  resolveRouteCategory,
  folderForCategoryUid,
  loadLiveCategoryRegistry,
  categoryForFolder,
  categoryUidForNote
};
