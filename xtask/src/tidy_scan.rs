//! `aib-xtask tidy-scan [--json] [--root R] [--baseline FILE] [--nodes FILE]` (S76d-1,
//! DEV-S76de r1, 519d0 11-14): lists what looks dead or redundant, and blocks nothing.
//! `--nodes` (path prefix → plan node, `"*"` for the rest) fills each item's optional
//! `node`, so layer 7 can dispatch it under a node (S79 C7, 519d0 11-15).
//!
//! Four kinds, all read without compiling, so the result is the same on every platform
//! (a compiler on one platform does not see code under the other one's cfg):
//!
//! - `unused_dep` / `maybe_feature_dep`: a Cargo dependency whose name (its rename if it
//!   has one, `-` read as `_`) is not an identifier anywhere in its package's `.rs`
//!   files. Files are read as tokens, so comments and strings do not count as a use. A
//!   dependency named by the package's `[features]`, or optional, is only
//!   `maybe_feature_dep`: it may be there to switch a feature on.
//! - `unreferenced_file`: a tracked file under `scripts/`, `docs/` or a `fixtures/` /
//!   `testdata/` folder whose name or path is not mentioned by any other tracked text
//!   file (workflows, knife lists and Markdown included). A file in a folder that some
//!   other file names as a folder (`tests/fixtures`) is skipped: it may be read by a walk.
//! - `identical_files`: tracked files of at least [`MIN_SAME`] bytes with the same
//!   content. It says they are the same, not which one could go.
//! - `duplicate_knife`: two knives (`knife-anchors` reads them) with the same target,
//!   anchor, replacement and `--expect-fail` test (DEV-S76de r1 §11: the same anchor and
//!   replacement biting another test is two knives). A knife whose test is gone needs the
//!   built tests (`--list`) and is the receipt's job (d-1b), not this read-only scan; an
//!   anchor that is gone is `knife-anchors`' rc 1 already.
//!
//! The report never says "unused": it says what was searched and found nothing there.
//! Anything that could not be read is listed under the section's `not_scanned` and makes
//! that section `partial`; it is never counted as 0.
//!
//! rc 0 = every section complete, 1 = some section partial or skipped, 2 = usage or the
//! scan could not run at all. What is found never changes the rc (report only).

use crate::{knife_anchors, knife_spec};
use proc_macro2::{TokenStream, TokenTree};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};

pub const SCHEMA: &str = "aib-xtask.tidy-scan.v1";
/// Smaller identical files (empty `__init__.py`, one-line stubs) are not reported.
pub const MIN_SAME: usize = 32;
/// Text files larger than this are not read for references.
const MAX_TEXT: usize = 2 << 20;

#[derive(Debug, Clone, PartialEq)]
pub struct Item {
    /// `dead` or `redundant`
    pub group: &'static str,
    pub kind: &'static str,
    /// kind + path + symbol: the same finding keeps the same key when lines move.
    pub key: String,
    pub paths: Vec<String>,
    pub symbol: String,
    pub evidence: String,
    /// how sure the finding is: `high` / `medium` / `low`
    pub reliability: &'static str,
    /// what removing it could break: `low` / `medium` / `high`
    pub removal_risk: &'static str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Section {
    pub name: &'static str,
    /// `complete` / `partial` / `skipped` / `failed`
    pub status: &'static str,
    pub scanned: String,
    pub not_scanned: Vec<String>,
}

impl Section {
    fn new(name: &'static str) -> Section {
        Section {
            name,
            status: "complete",
            scanned: String::new(),
            not_scanned: Vec::new(),
        }
    }
    fn miss(&mut self, what: String) {
        self.not_scanned.push(what);
        if self.status == "complete" {
            self.status = "partial";
        }
    }
}

/// FNV-1a 64: a short stable key part (the same on every platform and Rust version).
fn fnv(s: &str) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in s.bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    format!("{h:016x}")
}

fn rel(root: &Path, p: &Path) -> String {
    p.strip_prefix(root)
        .unwrap_or(p)
        .display()
        .to_string()
        .replace('\\', "/")
}

// ---- dependencies ----

/// The identifier a dependency is written as in Rust: its rename, else its name, with
/// `-` read as `_`.
pub fn dep_ident(dep: &Value) -> Option<String> {
    let n = dep
        .get("rename")
        .and_then(Value::as_str)
        .or_else(|| dep.get("name").and_then(Value::as_str))?;
    Some(n.replace('-', "_"))
}

fn idents_of(ts: TokenStream, out: &mut BTreeSet<String>) {
    for t in ts {
        match t {
            TokenTree::Ident(i) => {
                let s = i.to_string();
                out.insert(s.strip_prefix("r#").unwrap_or(&s).to_string());
            }
            TokenTree::Group(g) => idents_of(g.stream(), out),
            _ => {}
        }
    }
}

/// Every `.rs` file of a package: its folder without `target`, hidden folders and
/// nested packages (a folder with its own Cargo.toml).
fn package_rs(dir: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .collect();
    v.sort();
    for p in v {
        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if p.is_dir() {
            if name == "target" || name.starts_with('.') {
                continue;
            }
            // every folder here is below the package root: one with its own Cargo.toml
            // is another package
            if p.join("Cargo.toml").exists() {
                continue;
            }
            package_rs(&p, out)?;
        } else if p.extension().is_some_and(|x| x == "rs") {
            out.push(p);
        }
    }
    Ok(())
}

/// The dependency keys a package's `[features]` names (`dep:x`, `x/f`, `x?/f`, `x`).
fn feature_names(pkg: &Value) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    if let Some(m) = pkg.get("features").and_then(Value::as_object) {
        for v in m.values().filter_map(Value::as_array).flatten() {
            if let Some(s) = v.as_str() {
                let s = s.strip_prefix("dep:").unwrap_or(s);
                let s = s.split('/').next().unwrap_or(s);
                out.insert(s.trim_end_matches('?').to_string());
            }
        }
    }
    out
}

/// `w` as a whole word of `text` (not inside a longer identifier).
fn has_word(text: &str, w: &str) -> bool {
    let id = |c: char| c.is_alphanumeric() || c == '_';
    text.match_indices(w).any(|(i, _)| {
        !text[..i].chars().next_back().is_some_and(id)
            && !text[i + w.len()..].chars().next().is_some_and(id)
    })
}

/// `cargo metadata --no-deps` packages ⇒ dependencies not named in their package's code.
/// A dependency named by the replacement of a knife on one of the package's files is in use
/// (the knife builds with it: aibd's libproc for knife G1a). Paths and keys are relative to
/// the metadata's `workspace_root` (else `root`), so a key is the same on every machine.
pub fn deps_items(
    meta: &Value,
    root: &Path,
    knives: &[knife_anchors::Knife],
) -> (Vec<Item>, Section) {
    let mut sec = Section::new("deps");
    let mut items = Vec::new();
    let ws_root = meta.get("workspace_root").and_then(Value::as_str);
    let root = ws_root.map_or(root, Path::new);
    let Some(pkgs) = meta.get("packages").and_then(Value::as_array) else {
        sec.status = "failed";
        sec.not_scanned
            .push("cargo metadata has no packages".into());
        return (items, sec);
    };
    // a workspace package's lib target name is what code writes (`[lib] name = ..`);
    // outside the workspace `--no-deps` has no targets: the package name is taken
    let libs: HashMap<&str, String> = pkgs
        .iter()
        .filter_map(|p| {
            let lib = p.get("targets")?.as_array()?.iter().find(|t| {
                t.get("kind")
                    .and_then(Value::as_array)
                    .is_some_and(|k| k.iter().any(|k| k == "lib"))
            })?;
            Some((
                p.get("name")?.as_str()?,
                lib.get("name")?.as_str()?.replace('-', "_"),
            ))
        })
        .collect();
    let (mut n_pkg, mut n_files) = (0, 0);
    for pkg in pkgs {
        let name = pkg.get("name").and_then(Value::as_str).unwrap_or("?");
        let Some(dir) = pkg
            .get("manifest_path")
            .and_then(Value::as_str)
            .and_then(|m| Path::new(m).parent())
        else {
            sec.miss(format!("{name}: no manifest_path"));
            continue;
        };
        let mut files = Vec::new();
        if let Err(e) = package_rs(dir, &mut files) {
            sec.miss(format!("{name}: cannot list {}: {e}", rel(root, dir)));
            continue;
        }
        let mut used = BTreeSet::new();
        let mut broken = Vec::new();
        for f in &files {
            match std::fs::read_to_string(f).map(|t| t.parse::<TokenStream>()) {
                Ok(Ok(ts)) => idents_of(ts, &mut used),
                Ok(Err(e)) => broken.push(format!("{}: does not tokenize: {e}", rel(root, f))),
                Err(e) => broken.push(format!("{}: cannot read: {e}", rel(root, f))),
            }
        }
        if !broken.is_empty() {
            // a use could be in the file that was not read: say nothing about this package
            for b in broken {
                sec.miss(format!("{name}: {b}"));
            }
            continue;
        }
        n_pkg += 1;
        n_files += files.len();
        let features = feature_names(pkg);
        let at = rel(root, dir);
        for dep in pkg
            .get("dependencies")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let Some(mut ident) = dep_ident(dep) else {
                sec.miss(format!("{name}: a dependency without a name"));
                continue;
            };
            let lib = dep
                .get("name")
                .and_then(Value::as_str)
                .and_then(|n| libs.get(n));
            let in_ws = lib.is_some();
            if let (Some(l), None) = (lib, dep.get("rename")) {
                ident = l.clone();
            }
            if used.contains(&ident) {
                continue;
            }
            let by_knife = knives.iter().any(|k| {
                (at.is_empty() || k.file.starts_with(&format!("{at}/")))
                    && call(k).is_some_and(|c| has_word(&c.new, &ident))
            });
            if by_knife {
                continue;
            }
            let dname = dep.get("name").and_then(Value::as_str).unwrap_or("?");
            let dkey = dep.get("rename").and_then(Value::as_str).unwrap_or(dname);
            let dkind = dep.get("kind").and_then(Value::as_str).unwrap_or("normal");
            let target = dep.get("target").and_then(Value::as_str);
            let optional = dep.get("optional").and_then(Value::as_bool) == Some(true);
            let feature = optional || features.contains(dkey);
            let kind = if feature {
                "maybe_feature_dep"
            } else {
                "unused_dep"
            };
            let symbol = match target {
                Some(t) => format!("{dkey} ({dkind}, {t})"),
                None => format!("{dkey} ({dkind})"),
            };
            items.push(Item {
                group: "dead",
                kind,
                key: format!("{kind}:{at}:{dkind}:{dkey}"),
                paths: vec![format!("{at}/Cargo.toml")],
                symbol,
                evidence: format!(
                    "`{ident}` is not an identifier in the {} .rs files under {at}/ \
                     (comments, strings and doc tests not read){}{}",
                    files.len(),
                    if in_ws {
                        ""
                    } else {
                        "; lib name taken from the package name"
                    },
                    if feature {
                        "; named by [features] or optional"
                    } else {
                        ""
                    }
                ),
                reliability: "medium",
                removal_risk: if feature { "high" } else { "medium" },
            });
        }
    }
    sec.scanned = format!("{n_pkg} packages, {n_files} .rs files");
    (items, sec)
}

// ---- files ----

/// Path-like words of a text, each with every `/` suffix (`a/b/c.sh` ⇒ also `b/c.sh`,
/// `c.sh`), ends trimmed of `.` and `/`.
fn path_words(text: &str, out: &mut BTreeSet<String>) {
    let ok = |c: char| c.is_ascii_alphanumeric() || "._-/".contains(c);
    for w in text.split(|c: char| !ok(c)) {
        let w = w.trim_matches(|c| c == '.' || c == '/');
        if w.is_empty() {
            continue;
        }
        out.insert(w.to_string());
        for (i, c) in w.char_indices() {
            if c == '/' && i + 1 < w.len() {
                out.insert(w[i + 1..].to_string());
            }
        }
    }
}

fn is_text(b: &[u8]) -> bool {
    b.len() <= MAX_TEXT && !b.contains(&0)
}

/// What kind of file is looked at, and how sure a "not referenced" is for it.
fn candidate(p: &str) -> Option<&'static str> {
    if p.starts_with("scripts/") || p.contains("/fixtures/") || p.contains("/testdata/") {
        Some("medium")
    } else if p.starts_with("docs/") {
        Some("low")
    } else {
        None
    }
}

/// `ADR 0005`, `ADR-0005`, `adr_0005`, `ADR0005` (any case) ⇒ `ADR#0005`.
fn adr_numbers(text: &str, out: &mut BTreeSet<String>) {
    let b = text.as_bytes();
    let mut i = 0;
    while let Some(at) = b
        .get(i..)
        .and_then(|s| s.windows(3).position(|w| w.eq_ignore_ascii_case(b"adr")))
    {
        let s = i + at;
        i = s + 3;
        if s > 0 && b[s - 1].is_ascii_alphanumeric() {
            continue;
        }
        let mut j = s + 3;
        if matches!(b.get(j), Some(b' ' | b'-' | b'_')) {
            j += 1;
        }
        let digits = b.get(j..j + 4).filter(|d| d.iter().all(u8::is_ascii_digit));
        let ends = !b.get(j + 4).is_some_and(u8::is_ascii_digit);
        if let (Some(d), true) = (digits, ends) {
            out.insert(format!("ADR#{}", String::from_utf8_lossy(d)));
        }
    }
}

/// `(path, bytes)` of tracked files ⇒ candidates no other text file mentions.
pub fn unreferenced_items(files: &[(String, Vec<u8>)]) -> (Vec<Item>, Section) {
    let mut sec = Section::new("unreferenced_files");
    // word ⇒ (first file that has it, a second file has it too)
    let mut seen: HashMap<String, (usize, bool)> = HashMap::new();
    let mut n_text: usize = 0;
    for (i, (_p, bytes)) in files.iter().enumerate() {
        if !is_text(bytes) {
            continue;
        }
        n_text += 1;
        let mut ws = BTreeSet::new();
        let text = String::from_utf8_lossy(bytes);
        path_words(&text, &mut ws);
        adr_numbers(&text, &mut ws);
        for w in ws {
            seen.entry(w)
                .and_modify(|e| e.1 |= e.0 != i)
                .or_insert((i, false));
        }
    }
    let named_elsewhere = |w: &str, me: usize| seen.get(w).is_some_and(|&(f, two)| two || f != me);
    let mut items = Vec::new();
    let (mut n_cand, mut by_folder) = (0, 0);
    for (i, (p, _)) in files.iter().enumerate() {
        let Some(reliability) = candidate(p) else {
            continue;
        };
        n_cand += 1;
        let base = p.rsplit('/').next().unwrap_or(p);
        if named_elsewhere(p, i) || named_elsewhere(base, i) {
            continue;
        }
        // docs/adr/NNNN-*.md is cited by its number ("ADR 0005 §1", "ADR-0005", "adr/0005")
        let adr = p
            .strip_prefix("docs/adr/")
            .and_then(|f| f.get(..4))
            .filter(|n| n.bytes().all(|b| b.is_ascii_digit()));
        if adr.is_some_and(|n| {
            named_elsewhere(&format!("ADR#{n}"), i) || named_elsewhere(&format!("adr/{n}"), i)
        }) {
            continue;
        }
        // a folder at least two deep named as a folder: its files may be read by a walk
        let parent: Vec<&str> = p.split('/').collect();
        let parent = &parent[..parent.len().saturating_sub(1)];
        let walked =
            (0..parent.len().saturating_sub(1)).any(|s| named_elsewhere(&parent[s..].join("/"), i));
        if walked {
            by_folder += 1;
            continue;
        }
        items.push(Item {
            group: "dead",
            kind: "unreferenced_file",
            key: format!("unreferenced_file:{p}"),
            paths: vec![p.clone()],
            symbol: base.to_string(),
            evidence: format!(
                "neither `{p}` nor `{base}` is in the other {} tracked text files",
                n_text.saturating_sub(1)
            ),
            reliability,
            removal_risk: "medium",
        });
    }
    sec.scanned = format!(
        "{n_cand} files under scripts/, docs/, fixtures/, testdata/ against {n_text} text files; \
         {by_folder} skipped (their folder is named elsewhere)"
    );
    sec.not_scanned
        .push("files read by a name built at run time (format!, join chains)".into());
    (items, sec)
}

/// Tracked files with the same content (at least [`MIN_SAME`] bytes).
pub fn identical_items(files: &[(String, Vec<u8>)]) -> (Vec<Item>, Section) {
    let mut sec = Section::new("identical_files");
    let mut by_len: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (i, (_, b)) in files.iter().enumerate() {
        if b.len() >= MIN_SAME {
            by_len.entry(b.len()).or_default().push(i);
        }
    }
    let mut items = Vec::new();
    for (len, idx) in by_len {
        let mut groups: Vec<Vec<usize>> = Vec::new();
        for i in idx {
            match groups.iter_mut().find(|g| files[g[0]].1 == files[i].1) {
                Some(g) => g.push(i),
                None => groups.push(vec![i]),
            }
        }
        for g in groups.into_iter().filter(|g| g.len() > 1) {
            let mut paths: Vec<String> = g.iter().map(|&i| files[i].0.clone()).collect();
            paths.sort();
            // extensions/<a>/x and extensions/<b>/x: each mod ships its own copy
            let per_mod = paths
                .iter()
                .map(|p| {
                    p.strip_prefix("extensions/")
                        .and_then(|r| r.split_once('/'))
                        .map(|(_, rest)| rest)
                })
                .collect::<Option<BTreeSet<_>>>()
                .is_some_and(|s| s.len() == 1);
            items.push(Item {
                group: "redundant",
                kind: "identical_files",
                key: format!("identical_files:{}", paths.join("|")),
                symbol: paths[0].rsplit('/').next().unwrap_or("").to_string(),
                evidence: format!("{} files, the same {len} bytes", paths.len()),
                paths,
                reliability: if per_mod { "low" } else { "high" },
                removal_risk: "medium",
            });
        }
    }
    sec.scanned = format!("{} tracked files", files.len());
    (items, sec)
}

// ---- knives ----

/// The knife.py fields of a knife whose words are all known (`None` when a word is a
/// variable or the argv does not parse).
fn call(k: &knife_anchors::Knife) -> Option<knife_spec::Call> {
    k.argv
        .as_ref()
        .ok()
        .and_then(|a| knife_spec::parse_argv(a).ok())
}

/// Knives that are the same knife twice: target, anchor, replacement and expected
/// failing test all equal. A knife whose replacement or test cannot be read as plain
/// text is not compared (counted in `scanned`).
pub fn knife_items(root: &Path) -> (Vec<Item>, Section) {
    let mut sec = Section::new("knives");
    let (files, knives, unresolved) = knife_anchors::collect(root);
    let n_files = files.len();
    for u in unresolved {
        sec.miss(format!("knife line not read: {u}"));
    }
    if knives.is_empty() {
        sec.status = "failed";
        sec.not_scanned
            .push("no knife found; the scan is broken".into());
        return (Vec::new(), sec);
    }
    let mut items = Vec::new();
    let mut same: BTreeMap<(String, String, String, String), Vec<String>> = BTreeMap::new();
    let mut not_compared = 0;
    for k in &knives {
        match call(k) {
            Some(c) => same
                .entry((k.file.clone(), k.old.clone(), c.new, c.expect_fail))
                .or_default()
                .push(k.at.clone()),
            _ => not_compared += 1,
        }
    }
    for ((file, old, new, test), ats) in same {
        if ats.len() > 1 {
            items.push(Item {
                group: "redundant",
                kind: "duplicate_knife",
                key: format!(
                    "duplicate_knife:{file}:{}",
                    fnv(&format!("{old}\0{new}\0{test}"))
                ),
                symbol: old.trim().chars().take(60).collect(),
                evidence: format!(
                    "{} knives, same target, anchor, replacement and test {test}",
                    ats.len()
                ),
                paths: ats,
                reliability: "high",
                removal_risk: "low",
            });
        }
    }
    sec.scanned = format!(
        "{} knives in {n_files} files; {not_compared} not compared (replacement or test not plain text)",
        knives.len()
    );
    (items, sec)
}

// ---- the whole scan ----

pub struct Report {
    pub items: Vec<Item>,
    pub sections: Vec<Section>,
}

impl Report {
    pub fn status(&self) -> &'static str {
        if self.sections.iter().all(|s| s.status == "complete") {
            "complete"
        } else if self.sections.iter().all(|s| s.status == "failed") {
            "failed"
        } else {
            "partial"
        }
    }

    /// `(dead, redundant)` among the items whose key is not in `base` (all of them
    /// without a baseline).
    pub fn new_counts(&self, base: Option<&BTreeSet<String>>) -> (usize, usize) {
        let new = |i: &&Item| base.is_none_or(|b| !b.contains(&i.key));
        let n = |g: &str| {
            self.items
                .iter()
                .filter(new)
                .filter(|i| i.group == g)
                .count()
        };
        (n("dead"), n("redundant"))
    }

    /// `nodes`: path prefix → plan node (`--nodes`), shown as `nodes_file`; each item's
    /// optional `node` is its longest match ([`node_of`]), null when none.
    pub fn to_json(
        &self,
        base: Option<&BTreeSet<String>>,
        baseline: &str,
        nodes: Option<&BTreeMap<String, String>>,
        nodes_file: &str,
    ) -> Value {
        let (dn, rn) = self.new_counts(base);
        json!({
            "schema": SCHEMA,
            "status": self.status(),
            "baseline": baseline,
            "nodes": nodes_file,
            "summary": {
                "dead": self.items.iter().filter(|i| i.group == "dead").count(),
                "redundant": self.items.iter().filter(|i| i.group == "redundant").count(),
                "dead_new": dn,
                "redundant_new": rn,
            },
            "sections": self.sections.iter().map(|s| json!({
                "name": s.name, "status": s.status, "scanned": s.scanned,
                "not_scanned": s.not_scanned,
            })).collect::<Vec<_>>(),
            "items": self.items.iter().map(|i| json!({
                "group": i.group, "kind": i.kind, "key": i.key, "paths": i.paths,
                "symbol": i.symbol, "evidence": i.evidence, "reliability": i.reliability,
                "removal_risk": i.removal_risk,
                "new": base.map(|b| !b.contains(&i.key)),
                "node": nodes.and_then(|m| node_of(&i.paths, m)),
            })).collect::<Vec<_>>(),
        })
    }

    /// The receipt's summary line. Not `RC …`: mergewatch reads those as pass / fail.
    pub fn tidy_line(&self, base: Option<&BTreeSet<String>>, baseline: &str) -> String {
        let (dn, rn) = self.new_counts(base);
        format!(
            "TIDY dead_new={dn} redundant_new={rn} status={} baseline={baseline}",
            self.status()
        )
    }
}

/// Tracked files under `root` (`git ls-files`), read; a file listed but gone from the
/// work tree is left out and named.
fn tracked(root: &Path, sec: &mut Section) -> Option<Vec<(String, Vec<u8>)>> {
    let out = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z"])
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    let mut files = Vec::new();
    for p in out.stdout.split(|&b| b == 0).filter(|p| !p.is_empty()) {
        let p = String::from_utf8_lossy(p).into_owned();
        match std::fs::read(root.join(&p)) {
            Ok(b) => files.push((p, b)),
            Err(e) => sec.miss(format!("{p}: {e}")),
        }
    }
    Some(files)
}

pub fn scan(root: &Path, meta: Option<&Value>) -> Report {
    let mut sections = Vec::new();
    let mut items = Vec::new();
    match meta {
        Some(m) => {
            let (_, knives, _) = knife_anchors::collect(root);
            let (i, s) = deps_items(m, root, &knives);
            items.extend(i);
            sections.push(s);
        }
        None => sections.push(Section {
            name: "deps",
            status: "failed",
            scanned: String::new(),
            not_scanned: vec!["cargo metadata did not run".into()],
        }),
    }
    let mut listing = Section::new("tracked_files");
    match tracked(root, &mut listing) {
        Some(files) => {
            listing.scanned = format!("{} files from git ls-files", files.len());
            for (i, s) in [unreferenced_items(&files), identical_items(&files)] {
                items.extend(i);
                sections.push(s);
            }
        }
        None => {
            listing.status = "failed";
            listing.not_scanned.push("git ls-files did not run".into());
        }
    }
    sections.push(listing);
    let (i, s) = knife_items(root);
    items.extend(i);
    sections.push(s);
    items.sort_by(|a, b| (a.group, a.kind, &a.key).cmp(&(b.group, b.kind, &b.key)));
    Report { items, sections }
}

/// A `--nodes` file (S79 C7: layer 7 dispatches a finding under a plan node): a JSON
/// object of path prefix → node id, both non-empty strings; `"*"` is every item's node
/// when no prefix matches. Anything else is `None` (the file is shown as unreadable).
pub fn parse_nodes(text: &str) -> Option<BTreeMap<String, String>> {
    let v: Value = serde_json::from_str(text).ok()?;
    let mut out = BTreeMap::new();
    for (k, n) in v.as_object()? {
        let n = n.as_str().filter(|n| !n.trim().is_empty())?;
        let k = k.trim_end_matches('/');
        if k.trim().is_empty() {
            return None;
        }
        out.insert(k.to_string(), n.to_string());
    }
    Some(out)
}

/// The node of an item with these paths: the longest prefix (a whole path or a folder of
/// it: `crates/aib` is not a prefix of `crates/aib-drive/x`) any of them has, else `"*"`.
pub fn node_of<'a>(paths: &[String], nodes: &'a BTreeMap<String, String>) -> Option<&'a str> {
    let under =
        |p: &str, k: &str| p == k || (p.starts_with(k) && p.as_bytes().get(k.len()) == Some(&b'/'));
    nodes
        .iter()
        .filter(|(k, _)| k.as_str() != "*" && paths.iter().any(|p| under(p, k)))
        .max_by_key(|(k, _)| k.len())
        .or_else(|| nodes.get_key_value("*"))
        .map(|(_, n)| n.as_str())
}

/// Keys of a previous `--json` report (the merge-base's, for "new in this PR").
pub fn baseline_keys(text: &str) -> Option<BTreeSet<String>> {
    let v: Value = serde_json::from_str(text).ok()?;
    if v.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return None;
    }
    Some(
        v.get("items")?
            .as_array()?
            .iter()
            .filter_map(|i| i.get("key").and_then(Value::as_str).map(str::to_string))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "aib-xtask-tidy-{tag}-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn files(v: &[(&str, &str)]) -> Vec<(String, Vec<u8>)> {
        v.iter()
            .map(|(p, t)| (p.to_string(), t.as_bytes().to_vec()))
            .collect()
    }

    #[test]
    fn a_rename_is_the_name_in_code() {
        let d = json!({"name": "foo-bar", "rename": "fb"});
        assert_eq!(dep_ident(&d).as_deref(), Some("fb"));
        let d = json!({"name": "serde-json"});
        assert_eq!(dep_ident(&d).as_deref(), Some("serde_json"));
    }

    #[test]
    fn deps_named_only_in_comments_strings_or_features_are_reported() {
        let root = tmp("deps");
        let pkg = root.join("crates").join("p");
        std::fs::create_dir_all(pkg.join("src")).unwrap();
        std::fs::create_dir_all(pkg.join("inner")).unwrap();
        std::fs::write(pkg.join("Cargo.toml"), "").unwrap();
        std::fs::write(
            pkg.join("src/lib.rs"),
            "use serde_json::Value; // tempfile\npub fn f() { let _s = \"tempfile\"; fb::x(); }\n",
        )
        .unwrap();
        // a nested package's code is not this package's
        std::fs::write(pkg.join("inner/Cargo.toml"), "").unwrap();
        std::fs::write(pkg.join("inner/lib.rs"), "fn g() { ring::x(); }").unwrap();
        let meta = json!({"packages": [{
            "name": "p",
            "manifest_path": pkg.join("Cargo.toml").display().to_string(),
            "features": {"tls": ["dep:ring"]},
            "dependencies": [
                {"name": "serde_json", "kind": null},
                {"name": "tempfile", "kind": "dev"},
                {"name": "foo-bar", "rename": "fb", "kind": null},
                {"name": "ring", "kind": null, "optional": true},
            ],
        }]});
        let (items, sec) = deps_items(&meta, &root, &[]);
        assert_eq!(sec.status, "complete", "{sec:?}");
        let got: Vec<(&str, &str)> = items.iter().map(|i| (i.kind, i.key.as_str())).collect();
        assert_eq!(
            got,
            vec![
                ("unused_dep", "unused_dep:crates/p:dev:tempfile"),
                (
                    "maybe_feature_dep",
                    "maybe_feature_dep:crates/p:normal:ring"
                ),
            ]
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn keys_are_relative_to_workspace_root_and_a_knifes_replacement_is_a_use() {
        let root = tmp("wsroot");
        let pkg = root.join("crates").join("d");
        std::fs::create_dir_all(pkg.join("src")).unwrap();
        std::fs::write(pkg.join("Cargo.toml"), "").unwrap();
        std::fs::write(pkg.join("src/lib.rs"), "pub fn f() {}\n").unwrap();
        let meta = json!({
            "workspace_root": root.display().to_string(),
            "packages": [{
                "name": "d",
                "manifest_path": pkg.join("Cargo.toml").display().to_string(),
                "dependencies": [
                    {"name": "libproc", "kind": null},
                    {"name": "tempfile", "kind": "dev"},
                ],
            }],
        });
        let knife = |file: &str, new: &str| knife_anchors::Knife {
            at: "aibd.yml:1".into(),
            file: file.into(),
            old: "pub fn f() {}".into(),
            job: String::new(),
            step: String::new(),
            argv: Ok(vec![
                file.into(),
                "pub fn f() {}".into(),
                new.into(),
                "--expect-fail".into(),
                "t".into(),
                "--".into(),
                "cargo".into(),
            ]),
        };
        // libproc only in a knife on this package; tempfile only inside a longer word
        // and in a knife on another package
        let knives = [
            knife("crates/d/src/lib.rs", "pub fn f() { libproc::pids(); }"),
            knife("crates/d/src/lib.rs", "pub fn f() { my_tempfile_x(); }"),
            knife("crates/other/src/lib.rs", "tempfile::x();"),
        ];
        // run from somewhere else with root "." : keys still come out relative
        let (items, sec) = deps_items(&meta, Path::new("."), &knives);
        assert_eq!(sec.status, "complete", "{sec:?}");
        let keys: Vec<&str> = items.iter().map(|i| i.key.as_str()).collect();
        assert_eq!(keys, vec!["unused_dep:crates/d:dev:tempfile"]);
        assert!(
            items
                .iter()
                .all(|i| i.paths.iter().all(|p| !p.starts_with('/'))),
            "{items:?}"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_workspace_dependency_is_looked_for_by_its_lib_name() {
        let root = tmp("libname");
        let pkg = root.join("crates").join("p");
        std::fs::create_dir_all(pkg.join("src")).unwrap();
        std::fs::write(pkg.join("Cargo.toml"), "").unwrap();
        std::fs::write(pkg.join("src/lib.rs"), "pub fn f() { mc::x(); }\n").unwrap();
        let meta = json!({"packages": [
            {
                "name": "p",
                "manifest_path": pkg.join("Cargo.toml").display().to_string(),
                "dependencies": [
                    {"name": "my-crate", "kind": null},
                    {"name": "outside", "kind": null},
                ],
            },
            {
                "name": "my-crate",
                "manifest_path": root.join("crates/m/Cargo.toml").display().to_string(),
                "targets": [{"kind": ["lib"], "name": "mc"}],
                "dependencies": [],
            },
        ]});
        let (items, _) = deps_items(&meta, &root, &[]);
        let keys: Vec<&str> = items.iter().map(|i| i.key.as_str()).collect();
        assert_eq!(
            keys,
            vec!["unused_dep:crates/p:normal:outside"],
            "{items:?}"
        );
        assert!(
            items[0]
                .evidence
                .contains("lib name taken from the package name"),
            "{items:?}"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn an_adr_cited_by_its_number_is_referenced() {
        let fs = files(&[
            ("docs/adr/0005-budget.md", "# ADR 0005"),
            ("docs/adr/0006-transport.md", "# ADR 0006"),
            ("docs/adr/0007-other.md", "# ADR 0007"),
            ("docs/adr/0009-alone.md", "# ADR 0009, not ADR 00090"),
            (
                "CHANGELOG.md",
                "per ADR 0005 §1; adr-0006; see docs/adr/0007; ADR 00091",
            ),
        ]);
        let (items, _) = unreferenced_items(&fs);
        let paths: Vec<&str> = items.iter().map(|i| i.paths[0].as_str()).collect();
        assert_eq!(paths, vec!["docs/adr/0009-alone.md"]);
        let mut ws = BTreeSet::new();
        adr_numbers("radr 0001 ADR0002 adr_0003 ADR 12345", &mut ws);
        assert_eq!(
            ws.into_iter().collect::<Vec<_>>(),
            vec!["ADR#0002", "ADR#0003"]
        );
    }

    #[test]
    fn the_same_config_in_each_extension_is_low() {
        let long = "x".repeat(MIN_SAME);
        let fs = files(&[
            ("extensions/a/hooks/hooks.json", long.as_str()),
            ("extensions/b/hooks/hooks.json", long.as_str()),
            ("extensions/a/other.json", "y".repeat(MIN_SAME).as_str()),
            ("extensions/b/elsewhere.json", "y".repeat(MIN_SAME).as_str()),
        ]);
        let (items, _) = identical_items(&fs);
        let got: Vec<(&str, &str)> = items
            .iter()
            .map(|i| (i.paths[0].as_str(), i.reliability))
            .collect();
        assert_eq!(
            got,
            vec![
                ("extensions/a/hooks/hooks.json", "low"),
                ("extensions/a/other.json", "high"),
            ]
        );
    }

    #[test]
    fn has_word_needs_both_ends_free() {
        assert!(has_word("libproc::x", "libproc"));
        assert!(has_word("(libproc)", "libproc"));
        assert!(!has_word("my_libproc", "libproc"));
        assert!(!has_word("libproc2", "libproc"));
        assert!(has_word("libproc2 libproc", "libproc"));
    }

    #[test]
    fn a_file_that_does_not_tokenize_leaves_its_package_unjudged() {
        let root = tmp("broken");
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("Cargo.toml"), "").unwrap();
        std::fs::write(root.join("src/lib.rs"), "fn f() { \"open").unwrap();
        let meta = json!({"packages": [{
            "name": "p",
            "manifest_path": root.join("Cargo.toml").display().to_string(),
            "dependencies": [{"name": "tempfile", "kind": "dev"}],
        }]});
        let (items, sec) = deps_items(&meta, &root, &[]);
        assert!(items.is_empty(), "reason: unread_counted_as_zero {items:?}");
        assert_eq!(sec.status, "partial");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn files_named_by_workflows_lists_or_their_folder_are_not_reported() {
        let fs = files(&[
            (".github/workflows/w.yml", "run: bash scripts/used.sh"),
            ("scripts/used.sh", "echo used"),
            ("scripts/alone.sh", "echo alone.sh is me"),
            (
                "crates/x/tests/t.rs",
                "let d = concat!(env!(\"CARGO_MANIFEST_DIR\"), \"/tests/fixtures/\");",
            ),
            ("crates/x/tests/fixtures/f.json", "{}"),
            ("crates/y/fixtures/lonely.json", "{}"),
            ("docs/adr/0009-x.md", "# x"),
            ("crates/x/src/lib.rs", "pub fn f() {}"),
        ]);
        let (items, sec) = unreferenced_items(&fs);
        let got: Vec<(&str, &str)> = items
            .iter()
            .map(|i| (i.paths[0].as_str(), i.reliability))
            .collect();
        assert_eq!(
            got,
            vec![
                ("scripts/alone.sh", "medium"),
                ("crates/y/fixtures/lonely.json", "medium"),
                ("docs/adr/0009-x.md", "low"),
            ],
            "{sec:?}"
        );
        assert!(sec.scanned.contains("1 skipped"), "{}", sec.scanned);
    }

    #[test]
    fn identical_files_are_grouped_and_small_ones_left_out() {
        let long = "x".repeat(MIN_SAME);
        let other = "y".repeat(MIN_SAME);
        let fs = files(&[
            ("a/one.sh", long.as_str()),
            ("b/two.sh", long.as_str()),
            ("c/other.sh", other.as_str()),
            ("d/__init__.py", ""),
            ("e/__init__.py", ""),
        ]);
        let (items, _) = identical_items(&fs);
        assert_eq!(items.len(), 1, "{items:?}");
        assert_eq!(items[0].paths, vec!["a/one.sh", "b/two.sh"]);
        assert_eq!(items[0].key, "identical_files:a/one.sh|b/two.sh");
    }

    #[test]
    fn a_duplicate_knife_needs_all_four_fields_equal() {
        let root = tmp("knives");
        std::fs::create_dir_all(root.join(".github/workflows")).unwrap();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/x.rs"), "    let a = 1;\n").unwrap();
        let k = |t: &str| {
            format!("          python3 -I scripts/knife.py src/x.rs '    let a = 1;' '    let a = 2;' --expect-fail {t} -- cargo test\n")
        };
        // through a function too: `$3` is the test
        let f = "          ks() { python3 -I scripts/knife.py src/x.rs \"$1\" \"$2\" --expect-fail \"$3\" -- cargo test; }\n          ks '    let a = 1;' '    let a = 2;' t\n";
        std::fs::write(
            root.join(".github/workflows/w.yml"),
            format!(
                "      - name: k\n        run: |\n{}{}{}{f}",
                k("t"),
                k("t"),
                k("other")
            ),
        )
        .unwrap();
        let (items, sec) = knife_items(&root);
        assert_eq!(sec.status, "complete", "{sec:?}");
        let got: Vec<(&str, usize)> = items.iter().map(|i| (i.kind, i.paths.len())).collect();
        // the knife biting `other` is a knife of its own (r1 §11)
        assert_eq!(got, vec![("duplicate_knife", 3)], "{items:?}");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_baseline_marks_only_new_keys() {
        let item = |kind: &'static str, group: &'static str| Item {
            group,
            kind,
            key: format!("{kind}:k"),
            paths: vec![],
            symbol: String::new(),
            evidence: String::new(),
            reliability: "high",
            removal_risk: "low",
        };
        let r = Report {
            items: vec![
                item("unused_dep", "dead"),
                item("unreferenced_file", "dead"),
                item("identical_files", "redundant"),
            ],
            sections: vec![Section::new("deps")],
        };
        let old = r.to_json(None, "none", None, "none").to_string();
        let base = baseline_keys(&old).unwrap();
        assert_eq!(r.new_counts(Some(&base)), (0, 0));
        let mut fewer = base.clone();
        fewer.remove("unreferenced_file:k");
        assert_eq!(r.new_counts(Some(&fewer)), (1, 0));
        assert_eq!(r.new_counts(None), (2, 1));
        assert_eq!(
            r.tidy_line(Some(&fewer), "b.json"),
            "TIDY dead_new=1 redundant_new=0 status=complete baseline=b.json"
        );
        assert!(baseline_keys("{\"schema\":\"other\",\"items\":[]}").is_none());
    }

    /// S79 C7: each item's node is the longest path prefix in `--nodes` (a folder or the
    /// whole path, not a name that only starts the same), else `"*"`, else null.
    #[test]
    fn an_item_gets_the_node_of_its_longest_path_prefix() {
        let m = parse_nodes(
            r#"{"crates/aib": "S1", "crates/aib-drive/": "S79", "crates/aib-drive/src/act.rs": "S79-act"}"#,
        )
        .unwrap();
        let p = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            node_of(&p(&["crates/aib-drive/src/act.rs"]), &m),
            Some("S79-act")
        );
        assert_eq!(
            node_of(&p(&["crates/aib-drive/Cargo.toml"]), &m),
            Some("S79")
        );
        assert_eq!(node_of(&p(&["crates/aib/src/lib.rs"]), &m), Some("S1"));
        // a name that only starts the same is not under the folder
        assert_eq!(node_of(&p(&["crates/aibd/src/x.rs"]), &m), None);
        // identical files: any of the paths, the longest prefix wins
        assert_eq!(
            node_of(&p(&["scripts/a.sh", "crates/aib-drive/src/act.rs"]), &m),
            Some("S79-act")
        );
        let mut star = m.clone();
        star.insert("*".into(), "S76d".into());
        assert_eq!(node_of(&p(&["crates/aibd/src/x.rs"]), &star), Some("S76d"));
        for bad in [
            r#"[]"#,
            r#"{"a": 1}"#,
            r#"{"a": ""}"#,
            r#"{"/": "S1"}"#,
            "not json",
        ] {
            assert!(parse_nodes(bad).is_none(), "{bad}");
        }
    }

    #[test]
    fn the_report_carries_each_items_node_or_null() {
        let item = |path: &str| Item {
            group: "dead",
            kind: "unreferenced_file",
            key: format!("unreferenced_file:{path}"),
            paths: vec![path.to_string()],
            symbol: String::new(),
            evidence: String::new(),
            reliability: "high",
            removal_risk: "low",
        };
        let r = Report {
            items: vec![item("scripts/a.sh"), item("docs/b.md")],
            sections: vec![Section::new("files")],
        };
        let m = parse_nodes(r#"{"scripts": "S76d"}"#).unwrap();
        let v = r.to_json(None, "none", Some(&m), "nodes.json");
        assert_eq!(v["nodes"], "nodes.json");
        assert_eq!(v["items"][0]["node"], "S76d");
        assert!(v["items"][1]["node"].is_null());
        let v = r.to_json(None, "none", None, "none");
        assert!(v["items"][0]["node"].is_null());
        assert_eq!(v["schema"], SCHEMA);
    }
}
