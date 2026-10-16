// Ez2 UID Display — Obsidian plugin.
// A uid-layout vault names folders fld_… and notes <uid>.md. This plugin only changes what the
// file explorer and tab headers show: folders get their title from <vault>/.ez2/display/labels.json,
// notes get their frontmatter `title`. It never renames, moves or writes any file, so sync and
// [[wikilinks]] are untouched. No labels.json ⇒ ids are shown as they are, no error.
"use strict";

let obsidian = null;
try { obsidian = require("obsidian"); } catch (e) { obsidian = null; }   // node tests load the pure functions only
const fs = require("fs");
const pathx = require("path");

const LABELS_REL = [".ez2", "display", "labels.json"];
const POLL_MS = 3000;          // labels.json is rewritten by the converter / reindex ⇒ re-read when its mtime changes
const ORIG = "ez2UidOrig";     // dataset key: the text Obsidian put there, restored on unload

// ── pure functions (tested by aib/tests/test-obsidian-uid-display-977e3-1016.py) ──

function parseLabels(text) {
  // labels.json text → {folders, notes} (plain objects); anything unreadable ⇒ null
  let d;
  try { d = JSON.parse(text); } catch (e) { return null; }
  if (!d || typeof d !== "object" || Array.isArray(d)) return null;
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
  return { folders: obj(d.folders), notes: obj(d.notes) };
}

function readLabels(base, fsMod) {
  // <base>/.ez2/display/labels.json → parsed labels; no base / missing / broken file ⇒ null (never throws)
  const f = fsMod || fs;
  if (!base) return null;
  try { return parseLabels(f.readFileSync(pathx.join(base, ...LABELS_REL), "utf8")); } catch (e) { return null; }
}

function pickText(v) {
  if (typeof v === "string" && v.trim()) return v.trim();
  if (typeof v === "number" && isFinite(v)) return String(v);
  return null;
}

function folderIdFromPath(p) {
  // "fld_x_group_a/fld_x_sub_b" → "fld_x_sub_b"
  const parts = String(p || "").split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

function folderDisplay(labels, folderPath, lang) {
  // the title to show for a folder; unknown folder / no labels ⇒ null (keep what Obsidian shows)
  // lang is reserved for the i18n names in labels.json (zh-TW / en / ja / ko); empty ⇒ title
  if (!labels || !labels.folders) return null;
  const entry = labels.folders[folderIdFromPath(folderPath)];
  if (!entry || typeof entry !== "object") return null;
  if (lang && entry.i18n && typeof entry.i18n === "object") {
    const t = pickText(entry.i18n[lang]);
    if (t) return t;
  }
  return pickText(entry.title);
}

function noteDisplay(frontmatter, basename) {
  // frontmatter title, or the file's own name when there is none
  const t = frontmatter && typeof frontmatter === "object" ? pickText(frontmatter.title) : null;
  return t || basename;
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const SORTED_ORDERS = ["alphabetical", "alphabeticalReverse"];

function sortItems(items, nameOf, order) {
  // explorer items ({file}) → sorted by what is shown, folders first (as Obsidian does).
  // Only for the name orders; by-time orders keep Obsidian's own result.
  const ord = order || "alphabetical";
  if (!Array.isArray(items) || !SORTED_ORDERS.includes(ord)) return items;
  const isFolder = (it) => !!(it && it.file && Array.isArray(it.file.children));
  const sign = ord === "alphabeticalReverse" ? -1 : 1;
  return items.slice().sort((a, b) => {
    const fa = isFolder(a), fb = isFolder(b);
    if (fa !== fb) return fa ? -1 : 1;
    return sign * collator.compare(String(nameOf(a.file) || ""), String(nameOf(b.file) || ""));
  });
}

// ── plugin ──

const Base = obsidian ? obsidian.Plugin : class {};

class UidDisplayPlugin extends Base {
  async onload() {
    this.labels = null;
    this.labelsMtime = -1;
    this.lang = "";                 // reserved: i18n names; empty ⇒ title
    this.touched = new Set();       // elements whose text we replaced
    this.timer = null;
    this.patched = new Map();       // explorer view → its own getSortedFolderItems
    this.titles = new Map();        // note path → title last sorted by (re-sort only when one changes)
    this.reloadLabels();
    this.registerInterval(window.setInterval(() => this.reloadLabels(), POLL_MS));
    const later = () => this.schedule();
    this.registerEvent(this.app.workspace.on("layout-change", () => { this.watchExplorers(); this.patchSort(); later(); }));
    this.registerEvent(this.app.workspace.on("active-leaf-change", later));
    this.registerEvent(this.app.workspace.on("file-open", later));
    this.registerEvent(this.app.vault.on("rename", later));
    this.registerEvent(this.app.vault.on("create", later));
    this.registerEvent(this.app.metadataCache.on("changed", (file) => { this.titleChanged(file); later(); }));
    this.registerEvent(this.app.metadataCache.on("resolved", later));
    // the explorer re-creates rows while scrolling / expanding ⇒ watch its DOM (only the explorer:
    // watching the whole window would fire on every keystroke in the editor) and re-apply
    this.observer = new MutationObserver(later);
    this.app.workspace.onLayoutReady(() => { this.watchExplorers(); this.patchSort(); later(); });
  }

  watchExplorers() {
    this.observer.disconnect();
    for (const leaf of this.app.workspace.getLeavesOfType("file-explorer")) {
      const el = leaf.view && leaf.view.containerEl;
      if (el) this.observer.observe(el, { childList: true, subtree: true, characterData: true });
    }
  }

  // folder order: wrap the explorer's own getSortedFolderItems so it sorts by the shown titles.
  // That method is not public API ⇒ an Obsidian without it just keeps its own order.
  patchSort() {
    for (const leaf of this.app.workspace.getLeavesOfType("file-explorer")) {
      const view = leaf.view;
      if (!view || this.patched.has(view) || typeof view.getSortedFolderItems !== "function") continue;
      const own = view.getSortedFolderItems;
      const plugin = this;
      this.patched.set(view, own);
      view.getSortedFolderItems = function (...args) {
        return sortItems(own.apply(this, args), (f) => plugin.displayName(f), this.sortOrder);
      };
      this.resort(view);
    }
  }

  resort(only) {
    const views = only ? [only] : [...this.patched.keys()];
    for (const view of views) {
      try { if (view.requestSort) view.requestSort(); else if (view.sort) view.sort(); } catch (e) { /* keep showing */ }
    }
  }

  titleChanged(file) {
    if (!file || file.extension !== "md") return;
    const t = this.noteTitle(file);
    if (this.titles.has(file.path) && this.titles.get(file.path) !== t) this.resort();
    this.titles.set(file.path, t);
  }

  displayName(file) {
    if (!file) return "";
    if (Array.isArray(file.children)) return folderDisplay(this.labels, file.path, this.lang) || file.name;
    if (file.extension === "md") {
      const t = this.noteTitle(file);
      this.titles.set(file.path, t);
      return t;
    }
    return file.name;
  }

  onunload() {
    if (this.observer) this.observer.disconnect();
    if (this.timer) window.clearTimeout(this.timer);
    for (const [view, own] of this.patched) view.getSortedFolderItems = own;
    const views = [...this.patched.keys()];
    this.patched.clear();
    for (const view of views) this.resort(view);
    for (const el of this.touched) this.restore(el);
    this.touched.clear();
  }

  basePath() {
    const ad = this.app.vault.adapter;
    return (ad && ad.getBasePath && ad.getBasePath()) || (ad && ad.basePath) || "";
  }

  reloadLabels() {
    const file = pathx.join(this.basePath(), ...LABELS_REL);
    let mtime = 0;
    try { mtime = fs.statSync(file).mtimeMs; } catch (e) { mtime = 0; }
    if (mtime === this.labelsMtime) return;
    this.labelsMtime = mtime;
    this.labels = mtime ? readLabels(this.basePath()) : null;
    if (this.patched) this.resort();
    this.schedule();
  }

  schedule() {
    if (this.timer) return;
    this.timer = window.setTimeout(() => { this.timer = null; this.apply(); }, 50);
  }

  setText(el, text) {
    if (!el) return;
    if (el.dataset[ORIG] === undefined) el.dataset[ORIG] = el.textContent;
    if (el.textContent !== text) el.textContent = text;
    this.touched.add(el);
  }

  restore(el) {
    if (!el || el.dataset[ORIG] === undefined) return;
    if (el.textContent !== el.dataset[ORIG]) el.textContent = el.dataset[ORIG];
    delete el.dataset[ORIG];
  }

  noteTitle(file) {
    const cache = this.app.metadataCache.getFileCache(file);
    return noteDisplay(cache && cache.frontmatter, file.basename);
  }

  apply() {
    for (const el of this.touched) if (!el.isConnected) this.touched.delete(el);
    // file explorer rows (old and new Obsidian class names)
    for (const row of document.querySelectorAll(".nav-folder-title[data-path]")) {
      const el = row.querySelector(".nav-folder-title-content");
      const shown = folderDisplay(this.labels, row.getAttribute("data-path"), this.lang);
      if (shown) this.setText(el, shown); else this.restore(el);
    }
    for (const row of document.querySelectorAll(".nav-file-title[data-path]")) {
      const el = row.querySelector(".nav-file-title-content");
      const file = this.app.vault.getAbstractFileByPath(row.getAttribute("data-path"));
      if (file && file.extension === "md") this.setText(el, this.noteTitle(file));
    }
    // tab headers (view headers and inline titles stay: they are editable and would rename the file)
    this.app.workspace.iterateAllLeaves((leaf) => {
      const file = leaf.view && leaf.view.file;
      const el = leaf.tabHeaderInnerTitleEl;
      if (file && file.extension === "md" && el) this.setText(el, this.noteTitle(file));
    });
  }
}

module.exports = UidDisplayPlugin;
module.exports.default = UidDisplayPlugin;
module.exports.core = { parseLabels, readLabels, folderIdFromPath, folderDisplay, noteDisplay, sortItems, LABELS_REL };
