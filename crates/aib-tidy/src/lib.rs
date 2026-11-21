//! aib-tidy, first version: gather and sort what may be cleaned after a merge into a list a
//! person reviews (DECISION-council-tidy-519d0-1121 r1 §2 §7; fixtures from
//! TIDY-trial-a9136-1121). It reads the tree only: nothing is changed or removed.
//!
//! Three kinds, each the trial's:
//! - [`Kind::UnusedDep`]: a dependency a crate's own Rust files never name;
//! - [`Kind::ScriptNoCaller`]: a script nothing in the tree names (by file name or, for Python,
//!   by module);
//! - [`Kind::WorkflowPathMissing`]: a path a workflow names that is in the tree neither from the
//!   root nor inside any crate.
//!
//! Every item says why it may be a false alarm, and an item of a class that is never listed
//! (§4: a knife's dependency, a folder run by a wildcard) is kept as `excluded` with the class,
//! not as a candidate. Without a build on macOS and on Windows an item is `unverified` (§2):
//! this version gathers no build evidence, so nothing it finds is yet `candidate`.

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

/// Folders never read: build output, other repositories, installed packages.
pub const SKIP_DIRS: [&str; 5] = [
    ".git",
    "target",
    "node_modules",
    "target-win.noindex",
    ".venv",
];
/// Text files read for references.
pub const TEXT_EXTS: [&str; 10] = [
    "rs", "sh", "py", "js", "yml", "yaml", "toml", "json", "ps1", "md",
];
/// Script files that may have no caller.
pub const SCRIPT_EXTS: [&str; 3] = ["sh", "py", "js"];
/// Path endings a workflow line may name.
pub const PATH_EXTS: [&str; 7] = ["sh", "py", "rs", "js", "json", "toml", "tsv"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    UnusedDep,
    ScriptNoCaller,
    WorkflowPathMissing,
}

impl Kind {
    pub fn word(self) -> &'static str {
        match self {
            Kind::UnusedDep => "unused_dep",
            Kind::ScriptNoCaller => "script_no_caller",
            Kind::WorkflowPathMissing => "workflow_path_missing",
        }
    }
}

/// One thing that may be cleaned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub kind: Kind,
    /// The file it is in (a manifest, a script, a workflow), `/`-separated from the root.
    pub path: String,
    /// The dependency name, the script's name, or the path the workflow names.
    pub symbol: String,
    /// How it was found.
    pub evidence: String,
    /// Why it may be a false alarm.
    pub maybe: Vec<&'static str>,
    /// The §4 class that keeps it off the list, when one does.
    pub never: Option<&'static str>,
    /// A build and test without it passed on macOS / Windows (none gathered yet).
    pub mac: bool,
    pub win: bool,
}

impl Item {
    /// `excluded` (a §4 class), `candidate` (both platforms built without it) or `unverified`.
    pub fn status(&self) -> &'static str {
        if self.never.is_some() {
            "excluded"
        } else if self.mac && self.win {
            "candidate"
        } else {
            "unverified"
        }
    }

    pub fn line(&self) -> String {
        let why = match self.never {
            Some(c) => format!(" never={c}"),
            None if self.maybe.is_empty() => String::new(),
            None => format!(" maybe={}", self.maybe.join(",")),
        };
        format!(
            "{} {} {} {}{} ({})",
            self.status(),
            self.kind.word(),
            self.path,
            self.symbol,
            why,
            self.evidence
        )
    }

    pub fn to_value(&self) -> Value {
        json!({
            "status": self.status(),
            "kind": self.kind.word(),
            "path": self.path,
            "symbol": self.symbol,
            "evidence": self.evidence,
            "maybe": self.maybe,
            "never": self.never,
            "platforms": {"macos": self.mac, "windows": self.win},
        })
    }
}

/// §6: the board shows how many wait and how many are unverified, never lines removed.
pub fn summary(items: &[Item]) -> String {
    let n = |s: &str| items.iter().filter(|i| i.status() == s).count();
    format!(
        "TIDY candidate={} unverified={} excluded={}",
        n("candidate"),
        n("unverified"),
        n("excluded")
    )
}

pub fn to_json(items: &[Item]) -> Value {
    json!({
        "items": items.iter().map(Item::to_value).collect::<Vec<_>>(),
        "summary": summary(items),
    })
}

/// `p` from `root`, `/`-separated on every system.
pub fn rel(root: &Path, p: &Path) -> String {
    let r = p.strip_prefix(root).unwrap_or(p);
    r.components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

fn ext_of(p: &Path) -> &str {
    p.extension().and_then(|e| e.to_str()).unwrap_or("")
}

/// Every file under `dir` (links are not followed; the skipped folders are not entered).
pub fn files_under(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut todo = vec![dir.to_path_buf()];
    while let Some(d) = todo.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else {
            continue;
        };
        for e in rd.flatten() {
            let Ok(t) = e.file_type() else { continue };
            let p = e.path();
            if t.is_dir() {
                let name = e.file_name();
                if !SKIP_DIRS.iter().any(|s| name == *s) {
                    todo.push(p);
                }
            } else if t.is_file() {
                out.push(p);
            }
        }
    }
    out.sort();
    out
}

/// The tree's text files, read once: `(path from the root, text)`.
pub fn read_texts(root: &Path) -> Vec<(String, String)> {
    files_under(root)
        .into_iter()
        .filter(|p| TEXT_EXTS.contains(&ext_of(p)))
        .filter_map(|p| {
            let t = std::fs::read_to_string(&p).ok()?;
            Some((rel(root, &p), t))
        })
        .collect()
}

/// One dependency line of a manifest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dep {
    pub name: String,
    /// The section header, e.g. `[dev-dependencies]` or `[target.'cfg(windows)'.dependencies]`.
    pub section: String,
    /// The comment lines right above it.
    pub note: String,
}

/// The dependencies a `Cargo.toml` lists (any `…dependencies]` section; `name = …` or
/// `name.workspace = true`).
pub fn deps_of(manifest: &str) -> Vec<Dep> {
    let mut out = Vec::new();
    let (mut section, mut note) = (String::new(), Vec::<&str>::new());
    for line in manifest.lines() {
        let t = line.trim();
        if t.starts_with('[') {
            section = t.to_owned();
            note.clear();
            continue;
        }
        if let Some(c) = t.strip_prefix('#') {
            note.push(c.trim());
            continue;
        }
        if t.is_empty() {
            note.clear();
            continue;
        }
        if section.ends_with("dependencies]") {
            let key = t.split(['=', '.']).next().unwrap_or("").trim();
            let ok = !key.is_empty()
                && key
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
            if ok {
                out.push(Dep {
                    name: key.to_owned(),
                    section: section.clone(),
                    note: note.join(" "),
                });
            }
        }
        note.clear();
    }
    out
}

/// Whether Rust text names the crate `id` (`id::`, `id!`, `use id`, `extern crate id`, an
/// attribute `#[id(` / `#[id::`), as a whole word.
pub fn names_crate(text: &str, id: &str) -> bool {
    let b = text.as_bytes();
    let word = |c: u8| c.is_ascii_alphanumeric() || c == b'_';
    let mut from = 0;
    while let Some(at) = text[from..].find(id) {
        let s = from + at;
        let e = s + id.len();
        from = e;
        if s > 0 && word(b[s - 1]) {
            continue;
        }
        let rest = &text[e..];
        if rest.starts_with("::") || rest.starts_with('!') {
            return true;
        }
        let before = text[..s].trim_end();
        if (before.ends_with("use") || before.ends_with("extern crate") || before.ends_with("#["))
            && !rest.starts_with(|c: char| c.is_ascii_alphanumeric() || c == '_')
        {
            return true;
        }
    }
    false
}

/// Kind 1: dependencies a crate's own Rust files never name.
pub fn unused_deps(root: &Path, texts: &[(String, String)]) -> Vec<Item> {
    let mut manifests: Vec<PathBuf> = std::fs::read_dir(root.join("crates"))
        .map(|rd| {
            rd.flatten()
                .map(|e| e.path().join("Cargo.toml"))
                .filter(|p| p.is_file())
                .collect()
        })
        .unwrap_or_default();
    if root.join("xtask/Cargo.toml").is_file() {
        manifests.push(root.join("xtask/Cargo.toml"));
    }
    manifests.sort();
    // what a knife or a workflow puts back (TIDY-trial 1: libproc for knife G1a)
    let knife_texts: Vec<&str> = texts
        .iter()
        .filter(|(p, _)| p.contains("/knives/") || p.starts_with(".github/workflows/"))
        .map(|(_, t)| t.as_str())
        .collect();
    let mut out = Vec::new();
    for m in manifests {
        let Ok(text) = std::fs::read_to_string(&m) else {
            continue;
        };
        let dir = m.parent().unwrap_or(root);
        let prefix = format!("{}/", rel(root, dir));
        let rs: Vec<&str> = texts
            .iter()
            .filter(|(p, _)| p.starts_with(&prefix) && p.ends_with(".rs"))
            .map(|(_, t)| t.as_str())
            .collect();
        for d in deps_of(&text) {
            let id = d.name.replace('-', "_");
            if rs.iter().any(|t| names_crate(t, &id)) {
                continue;
            }
            let mut maybe = Vec::new();
            if rs.iter().any(|t| t.contains("#[derive(")) {
                maybe.push("derive_or_macro");
            }
            if d.section.contains("cfg(") {
                maybe.push("one_platform_only");
            }
            if dir.join("build.rs").is_file() {
                maybe.push("build_script");
            }
            let note = d.note.to_lowercase();
            let never = if note.contains("knife") || d.note.contains('刀') {
                Some("knife_anchor")
            } else if knife_texts.iter().any(|t| names_crate(t, &id)) {
                Some("knife_anchor")
            } else {
                None
            };
            out.push(Item {
                kind: Kind::UnusedDep,
                path: rel(root, &m),
                symbol: d.name.clone(),
                evidence: format!(
                    "{} in {}: no Rust file under {prefix} names {id}",
                    d.name, d.section
                ),
                maybe,
                never,
                mac: false,
                win: false,
            });
        }
    }
    out
}

/// Kind 2: scripts nothing in the tree names.
pub fn scripts_no_caller(root: &Path, texts: &[(String, String)]) -> Vec<Item> {
    let mut out = Vec::new();
    for p in files_under(root) {
        if !SCRIPT_EXTS.contains(&ext_of(&p)) {
            continue;
        }
        let path = rel(root, &p);
        let name = p
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_owned();
        let stem = p
            .file_stem()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_owned();
        let named = texts.iter().any(|(q, t)| {
            if *q == path {
                return false;
            }
            t.contains(&name)
                || (ext_of(&p) == "py"
                    && (t.contains(&format!("import {stem}"))
                        || t.contains(&format!("from {stem} "))))
        });
        if named {
            continue;
        }
        // TIDY-trial 2: a knives folder is run whole by a wildcard
        let never = path
            .split('/')
            .any(|c| c == "knives")
            .then_some("glob_called");
        let mut maybe = vec!["dynamic_call"];
        if path.starts_with("aib/") {
            maybe.push("one_x_command");
        }
        out.push(Item {
            kind: Kind::ScriptNoCaller,
            path: path.clone(),
            symbol: name,
            evidence: "no other text file names it".to_owned(),
            maybe,
            never,
            mac: false,
            win: false,
        });
    }
    out
}

/// The repository paths a line names (`a/b.ext`, one of [`PATH_EXTS`]).
pub fn paths_in(line: &str) -> Vec<String> {
    let ok = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '-');
    line.split(|c: char| !ok(c))
        .map(|w| w.trim_start_matches("./").trim_end_matches('.'))
        .filter(|w| {
            w.contains('/')
                && !w.starts_with('/')
                && !w.contains("..")
                && w.rsplit_once('.')
                    .is_some_and(|(_, e)| PATH_EXTS.contains(&e))
        })
        .map(str::to_owned)
        .collect()
}

/// Kind 3: paths a workflow names that are nowhere in the tree.
pub fn workflow_paths(root: &Path) -> Vec<Item> {
    let crates: Vec<PathBuf> = std::fs::read_dir(root.join("crates"))
        .map(|rd| rd.flatten().map(|e| e.path()).collect())
        .unwrap_or_default();
    let mut out = Vec::new();
    for w in files_under(&root.join(".github/workflows")) {
        if !matches!(ext_of(&w), "yml" | "yaml") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&w) else {
            continue;
        };
        for (n, line) in text.lines().enumerate() {
            for p in paths_in(line) {
                if root.join(&p).exists() || p.contains('*') {
                    continue;
                }
                // TIDY-trial 3: a crate-relative path (tests/goalcheck.rs) is that crate's
                if crates.iter().any(|c| c.join(&p).exists()) {
                    continue;
                }
                let mut maybe = Vec::new();
                if line.trim_start().starts_with('#') {
                    maybe.push("in_comment");
                }
                out.push(Item {
                    kind: Kind::WorkflowPathMissing,
                    path: rel(root, &w),
                    symbol: p,
                    evidence: format!("line {} names it; not in the tree", n + 1),
                    maybe,
                    never: None,
                    mac: false,
                    win: false,
                });
            }
        }
    }
    out
}

/// All three kinds for the tree at `root`.
pub fn scan(root: &Path) -> std::io::Result<Vec<Item>> {
    if !root.join("Cargo.toml").is_file() {
        return Err(std::io::Error::other("no Cargo.toml at the root"));
    }
    let texts = read_texts(root);
    let mut items = unused_deps(root, &texts);
    items.extend(scripts_no_caller(root, &texts));
    items.extend(workflow_paths(root));
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put(root: &Path, p: &str, text: &str) {
        let f = root.join(p);
        std::fs::create_dir_all(f.parent().unwrap()).unwrap();
        std::fs::write(f, text).unwrap();
    }

    /// The trial's tree: one of each true and false case (TIDY-trial-a9136-1121).
    fn trial_tree() -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        put(r, "Cargo.toml", "[workspace]\n");
        put(
            r,
            "crates/stale/Cargo.toml",
            "[package]\nname = \"stale\"\n\n[dependencies]\nserde_json = \"1\"\n\n[dev-dependencies]\ntempfile = \"3\"\n",
        );
        put(
            r,
            "crates/stale/src/lib.rs",
            "pub fn f() -> serde_json::Value { serde_json::json!(1) }\n",
        );
        put(
            r,
            "crates/daemon/Cargo.toml",
            "[package]\nname = \"daemon\"\n\n[target.'cfg(target_os = \"macos\")'.dependencies]\n# Only for knife G1a: it puts the libproc child listing back.\nlibproc = \"0.15.0\"\nprocfs = \"0.17\"\n",
        );
        put(r, "crates/daemon/src/lib.rs", "pub fn g() {}\n");
        put(
            r,
            "crates/daemon/tests/goalcheck.rs",
            "#[test]\nfn t() {}\n",
        );
        put(
            r,
            "aib/tools/release/ship.py",
            "import flaky_ledger as fl\n",
        );
        put(r, "aib/tools/verify/flaky_ledger.py", "def f(): pass\n");
        put(r, "aib/tools/verify/orphan.py", "def g(): pass\n");
        put(
            r,
            "crates/daemon/knives/knives-01.sh",
            "echo 'use procfs::Process;'\n",
        );
        put(
            r,
            ".github/workflows/ci.yml",
            "jobs:\n  # see tests/goalcheck.rs\n  run: bash scripts/gone.sh\n",
        );
        d
    }

    /// TD1: the dependency no Rust file names is listed, unverified; the used one is not.
    #[test]
    fn an_unnamed_dependency_is_listed_unverified() {
        let d = trial_tree();
        let items = scan(d.path()).unwrap();
        let dep: Vec<_> = items.iter().filter(|i| i.kind == Kind::UnusedDep).collect();
        let t = dep
            .iter()
            .find(|i| i.symbol == "tempfile")
            .expect("reason: tempfile_missed");
        assert_eq!(t.status(), "unverified");
        assert_eq!(t.path, "crates/stale/Cargo.toml");
        assert!(
            !dep.iter().any(|i| i.symbol == "serde_json"),
            "reason: used_dep_listed"
        );
    }

    /// TD2 (§4): a dependency kept for a knife is excluded, never a candidate.
    #[test]
    fn a_knifes_dependency_is_excluded() {
        let d = trial_tree();
        let items = scan(d.path()).unwrap();
        let l = items
            .iter()
            .find(|i| i.symbol == "libproc")
            .expect("reason: libproc_not_seen");
        assert_eq!(
            l.never,
            Some("knife_anchor"),
            "reason: knife_dep_not_excluded"
        );
        assert!(l.maybe.contains(&"one_platform_only"));
        assert_eq!(l.status(), "excluded");
        let p = items
            .iter()
            .find(|i| i.symbol == "procfs")
            .expect("reason: procfs_not_seen");
        assert_eq!(
            p.never,
            Some("knife_anchor"),
            "reason: knife_text_dep_not_excluded"
        );
    }

    /// TD3: a Python module imported by name has a caller; a knives folder is run whole; an
    /// orphan is listed.
    #[test]
    fn a_script_without_a_caller_is_listed_and_others_are_not() {
        let d = trial_tree();
        let items = scan(d.path()).unwrap();
        let s: Vec<_> = items
            .iter()
            .filter(|i| i.kind == Kind::ScriptNoCaller)
            .collect();
        assert!(
            !s.iter().any(|i| i.symbol == "flaky_ledger.py"),
            "reason: python_import_missed"
        );
        let k = s
            .iter()
            .find(|i| i.symbol == "knives-01.sh")
            .expect("reason: knife_script_not_seen");
        assert_eq!(k.never, Some("glob_called"));
        let o = s
            .iter()
            .find(|i| i.symbol == "orphan.py")
            .expect("reason: orphan_missed");
        assert_eq!(o.status(), "unverified");
        assert!(o.maybe.contains(&"one_x_command"));
    }

    /// TD4: a crate-relative path in a workflow is in the tree; a gone script is listed.
    #[test]
    fn a_workflow_path_is_looked_for_in_the_crates_too() {
        let d = trial_tree();
        let items = scan(d.path()).unwrap();
        let w: Vec<_> = items
            .iter()
            .filter(|i| i.kind == Kind::WorkflowPathMissing)
            .collect();
        assert!(
            !w.iter().any(|i| i.symbol == "tests/goalcheck.rs"),
            "reason: crate_relative_listed"
        );
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].symbol, "scripts/gone.sh");
        assert_eq!(w[0].evidence, "line 3 names it; not in the tree");
    }

    /// TD5 (§2 §6): nothing is a candidate without both platforms; the summary counts, it
    /// does not total lines.
    #[test]
    fn nothing_is_a_candidate_without_both_platforms() {
        let d = trial_tree();
        let mut items = scan(d.path()).unwrap();
        assert!(items.iter().all(|i| i.status() != "candidate"));
        let s = summary(&items);
        assert!(s.starts_with("TIDY candidate=0 unverified="), "{s}");
        let i = items.iter_mut().find(|i| i.symbol == "tempfile").unwrap();
        i.mac = true;
        assert_eq!(i.status(), "unverified", "reason: one_platform_is_enough");
        i.win = true;
        assert_eq!(i.status(), "candidate");
        let v = to_json(&items);
        assert!(v["items"]
            .as_array()
            .unwrap()
            .iter()
            .any(|x| x["status"] == "candidate"));
    }

    #[test]
    fn crate_names_are_read_as_words() {
        assert!(names_crate("use serde_json::Value;", "serde_json"));
        assert!(names_crate("let v = serde_json!(1);", "serde_json"));
        assert!(names_crate("use tempfile;", "tempfile"));
        assert!(names_crate("#[serde(rename)]", "serde"));
        assert!(!names_crate("let my_tempfile = 1;", "tempfile"));
        assert!(!names_crate("// tempfile here", "tempfile"));
        assert!(!names_crate("use tempfiles::x;", "tempfile"));
        assert!(!names_crate("use my_tempfile::x;", "tempfile"));
        assert_eq!(
            deps_of(
                "[dependencies]\n# why\nfoo = \"1\"\nbar.workspace = true\n\n[features]\nx = []\n"
            )
            .iter()
            .map(|d| (d.name.as_str(), d.note.as_str()))
            .collect::<Vec<_>>(),
            [("foo", "why"), ("bar", "")]
        );
        assert_eq!(
            paths_in("run: bash ./scripts/a.sh crates/x/src/lib.rs, and /abs/p.sh ../up.sh"),
            ["scripts/a.sh", "crates/x/src/lib.rs"]
        );
        assert_eq!(
            rel(Path::new("/r"), &Path::new("/r").join("a").join("b.rs")),
            "a/b.rs"
        );
    }
}
