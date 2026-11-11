// SPDX-License-Identifier: Apache-2.0
//! aib-flaky — read-only "suspected flaky" register (merge-ops plan v2 §7.2 row 5).
//!
//! For every red job attempt on a PR's current head it decides one of three things:
//! `suspected_flaky`, `unclassified` (with the first rule that failed) or `infra_unverified`.
//! There is no "pass" outcome. Nothing here skips a test, reruns a job or writes to GitHub.
//! Anything that cannot be read or worked out falls to `unclassified`, never towards flaky.
//!
//! The register is a local file any same-account process can edit: it is information for
//! people and dispatch, never a gate (plan §7.1).

pub mod gh;

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

fn is_py_test_file(path: &str) -> bool {
    let name = match path
        .strip_prefix("aib/tests/")
        .or_else(|| path.strip_prefix("tests/"))
    {
        Some(n) => n,
        None => return false,
    };
    !name.contains('/') && name.starts_with("test-") && name.ends_with(".py")
}

/// Does the source of test T mention another test file (tests load each other as fixtures)?
/// Both the file stem and its `-` -> `_` spelling count.
fn mentions(src: &str, other: &str) -> bool {
    let base = other.rsplit('/').next().unwrap_or(other);
    let stem = base.strip_suffix(".py").unwrap_or(base);
    src.contains(stem) || src.contains(&stem.replace('-', "_"))
}

/// Python: a changed file is unrelated to test T only if it is `*.md`, under `docs/`, or another
/// `test-*.py` whose name T's source does not mention. Everything else overlaps (tests mostly
/// run the whole aib package in a subprocess, and non-`test-` files under tests/ are shared
/// helpers). Err = T's source could not be read while another test file changed.
fn py_overlap(test: &str, changed: &[Changed], src: Option<&str>) -> Result<bool, String> {
    for c in changed {
        let p = c.path.as_str();
        if p == test {
            return Ok(true);
        }
        if p.ends_with(".md") || p.starts_with("docs/") {
            continue;
        }
        if !is_py_test_file(p) {
            return Ok(true);
        }
        match src {
            None => return Err(format!("{test}: source not readable at head")),
            Some(s) if mentions(s, p) => return Ok(true),
            Some(_) => {}
        }
    }
    Ok(false)
}

/// Files that change every Rust test binary at once.
const RUST_SHARED: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    ".github/workflows/aibd.yml",
];

/// Log lines that mean the runner itself went away (plan review 043dd L2).
const RUNNER_LOST: &[&str] = &[
    "The runner has received a shutdown signal",
    "lost communication with the server",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    pub name: String,
    pub conclusion: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobAttempt {
    pub run_id: u64,
    pub attempt: u32,
    pub workflow: String,
    pub job_id: u64,
    pub job: String,
    pub conclusion: Option<String>,
    pub steps: Vec<Step>,
    /// None = the log could not be read
    pub log: Option<String>,
}

/// One changed file of the PR.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Changed {
    pub path: String,
    pub status: String,
}

/// What the head tree says about Rust crates. None anywhere = could not be worked out.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RustMap {
    /// test binary name (as in target/debug/deps/<bin>-<hash>) -> crate dirs that build it
    pub bins: BTreeMap<String, BTreeSet<String>>,
    /// crate dir -> path dependencies (crate dirs); None = a dependency entry was not understood
    pub deps: BTreeMap<String, Option<BTreeSet<String>>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Facts {
    pub repo: String,
    /// None = a main push (--sha mode)
    pub pr: Option<u64>,
    pub head: String,
    pub base: String,
    pub changed: Vec<Changed>,
    pub attempts: Vec<JobAttempt>,
    /// None = not needed (no Rust fingerprint) or unreadable (tree truncated / file missing)
    pub rust: Option<RustMap>,
    /// Python test file -> its source at head (None = unreadable)
    pub py_sources: BTreeMap<String, Option<String>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub repo: String,
    pub pr: Option<u64>,
    pub head: String,
    pub base: String,
    pub workflow: String,
    pub job: String,
    pub run_id: u64,
    pub attempt: u32,
    pub job_id: u64,
    pub failed_steps: Vec<String>,
    pub fingerprints: Vec<String>,
    /// file-level Python fingerprints (no test name found): not counted in suggest's reds
    pub coarse: bool,
    pub verdict: String,
    pub reason: String,
    /// every attempt of this cell on this head: "run_id/attempt:conclusion" (the denominator)
    pub cell_attempts: Vec<String>,
    /// may later entries use this one as precedent (rule 7)? PR: rules 1–5 held on its own
    /// head; main push: rules 1–3 held and the same cell went green on the same main head.
    pub precedent_ok: bool,
}

/// Strip the "2026-11-09T02:53:17.6272667Z " prefix GitHub puts on log lines.
fn after_stamp(line: &str) -> &str {
    match line.split_once(' ') {
        Some((stamp, rest))
            if stamp.len() >= 20
                && stamp.ends_with('Z')
                && stamp.as_bytes().get(4) == Some(&b'-')
                && stamp.as_bytes().get(10) == Some(&b'T') =>
        {
            rest
        }
        _ => line,
    }
}

fn error_text(line: &str) -> Option<&str> {
    let l = after_stamp(line);
    l.strip_prefix("##[error]")
        .or_else(|| l.strip_prefix("::error::"))
}

/// Test binary from "Running unittests src/lib.rs (target/debug/deps/aib_cireuse-0a1b2c)".
fn running_bin(line: &str) -> Option<String> {
    let l = after_stamp(line).trim_start();
    let rest = l.strip_prefix("Running ")?;
    let open = rest.rfind('(')?;
    let inner = rest[open + 1..].strip_suffix(')')?;
    let file = inner.rsplit(['/', '\\']).next()?;
    let file = file.strip_suffix(".exe").unwrap_or(file);
    let (bin, hash) = file.rsplit_once('-')?;
    if bin.is_empty() || hash.is_empty() || !hash.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    Some(bin.to_string())
}

/// "thread 'NAME' (32019) panicked at PATH:L:C:" (or without the thread id) -> (NAME, PATH).
fn panic_at(line: &str) -> Option<(String, String)> {
    let rest = after_stamp(line).trim_start().strip_prefix("thread '")?;
    let (name, rest) = rest.split_once('\'')?;
    let mut rest = rest.trim_start();
    if let Some(r) = rest.strip_prefix('(') {
        let (id, r) = r.split_once(')')?;
        if id.is_empty() || !id.chars().all(|c| c.is_ascii_digit()) {
            return None;
        }
        rest = r.trim_start();
    }
    let at = rest
        .strip_prefix("panicked at ")?
        .trim_end()
        .strip_suffix(':')?;
    let (at, col) = at.rsplit_once(':')?;
    let (path, ln) = at.rsplit_once(':')?;
    let digits = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit());
    if name.is_empty() || path.is_empty() || !digits(ln) || !digits(col) {
        return None;
    }
    Some((name.to_string(), path.replace('\\', "/")))
}

/// Where a failing test lives, from the file its own panic points at, for a log without the
/// "Running ... (target/debug/deps/<bin>-<hash>)" line:
/// - `<crate>/tests/<stem>.rs` or `<crate>/src/bin/<stem>.rs` -> the test binary `<stem>`
///   (`-` -> `_`, as in the Running line);
/// - `<crate>/src/<module>.rs` whose module is the test's own module (or one around it) ->
///   `@<crate>`: the crate is known, its binary name is not.
///
/// None = cannot tell (a panic in another file, outside the repo, or in the root crate).
///
/// `@<crate>` can name the wrong crate: a panic in a crate's root file (`src/lib.rs`,
/// `src/main.rs`) matches any test, and a module of the same name can sit in two crates — the
/// test may only have called into that crate. So [`impact_overlap`] never judges `@<crate>`
/// by that crate alone: it adds every crate that depends on it, where the test may live
/// (ebb36 #666 M1, L1). A wrong crate can only make the answer more cautious.
fn bin_from_panic(test: &str, path: &str) -> Option<String> {
    let segs: Vec<&str> = path.split('/').collect();
    if path.contains(':') || segs.iter().any(|s| s.is_empty() || *s == "." || *s == "..") {
        return None;
    }
    let i = segs.iter().position(|s| *s == "src" || *s == "tests")?;
    if i == 0 {
        return None;
    }
    let crate_dir = segs[..i].join("/");
    let norm = |s: &str| s.replace('-', "_");
    match (segs[i], &segs[i + 1..]) {
        ("tests", [file]) => file.strip_suffix(".rs").map(norm),
        ("src", ["bin", file]) => file.strip_suffix(".rs").map(norm),
        ("src", rest) if !rest.is_empty() => {
            let mut module: Vec<&str> = rest.to_vec();
            let last = module.pop()?.strip_suffix(".rs")?;
            if !(last == "mod" || (module.is_empty() && (last == "lib" || last == "main"))) {
                module.push(last);
            }
            let test_module: Vec<&str> = test.split("::").collect();
            let test_module = &test_module[..test_module.len() - 1];
            test_module
                .starts_with(&module)
                .then(|| format!("@{crate_dir}"))
        }
        _ => None,
    }
}

/// "FAIL: test_x (__main__.P0.test_x)" / "ERROR: test_x (mod.P0)" -> "P0.test_x".
fn unittest_id(line: &str) -> Option<String> {
    let rest = line
        .strip_prefix("FAIL: ")
        .or_else(|| line.strip_prefix("ERROR: "))?;
    let (name, paren) = rest.split_once(" (")?;
    let inner = paren.strip_suffix(')')?;
    let name = name.trim();
    if name.is_empty() || name.contains(' ') {
        return None;
    }
    let parts: Vec<&str> = inner.split('.').collect();
    let class = match parts.as_slice() {
        [.., c, last] if *last == name => *c,
        [.., c] => *c,
        [] => return None,
    };
    if class.is_empty() || class.contains(' ') {
        return None;
    }
    Some(format!("{class}.{name}"))
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Parsed {
    pub fingerprints: Vec<String>,
    /// a red Python file whose failing test names were not found (file-level fingerprint)
    pub coarse: bool,
    /// "ci-shard ..." errors: missing / duplicate / interrupted test files
    pub shard_problems: Vec<String>,
    pub runner_lost: bool,
}

/// Failure fingerprints in a job log.
/// - Python (ci-shard): `py:<file>::<Class.test>` from the unittest FAIL / ERROR lines inside the
///   file's `##[group]`; `py:<file>` (coarse) when none was found (e.g. a hang).
/// - cargo test: `rs:<bin>::<test>`. Without the binary line, the test's own panic location
///   names it (`rs:<bin>::<test>`, or `rs:@<crate dir>::<test>` for a unit test in src/, see
///   `bin_from_panic`); `rs:?::<test>` when that cannot tell either.
pub fn parse_log(log: &str) -> Parsed {
    let mut p = Parsed::default();
    let mut bin: Option<String> = None;
    // failing tests seen before any binary line, and what each test thread's panics point at
    let mut no_bin: Vec<String> = vec![];
    let mut panics: BTreeMap<String, Vec<Option<String>>> = BTreeMap::new();
    // ci-shard group: file name -> unittest ids seen inside its group
    let mut group: Option<String> = None;
    let mut ids: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for line in log.lines() {
        let l = after_stamp(line);
        if RUNNER_LOST.iter().any(|m| l.contains(m)) {
            p.runner_lost = true;
        }
        if let Some(g) = l
            .strip_prefix("##[group]")
            .or_else(|| l.strip_prefix("::group::"))
        {
            group = g
                .rsplit_once(" (")
                .map(|(n, _)| n.trim().to_string())
                .filter(|n| !n.contains(' '));
            continue;
        }
        if l.starts_with("##[endgroup]") || l.starts_with("::endgroup::") {
            group = None;
            continue;
        }
        if let Some(b) = running_bin(line) {
            bin = Some(b);
            continue;
        }
        if let Some(e) = error_text(line) {
            if let Some(rest) = e.strip_prefix("ci-shard ") {
                p.shard_problems.push(rest.trim().to_string());
            } else if let Some(name) = e
                .strip_suffix(" failed or hung")
                .or_else(|| e.split_once(" still running after ").map(|(n, _)| n))
            {
                let name = name.trim();
                if !name.is_empty() && !name.contains(' ') {
                    match ids.get(name).filter(|s| !s.is_empty()) {
                        Some(s) => p
                            .fingerprints
                            .extend(s.iter().map(|t| format!("py:{name}::{t}"))),
                        None => {
                            p.fingerprints.push(format!("py:{name}"));
                            p.coarse = true;
                        }
                    }
                }
            }
            continue;
        }
        if let Some((name, path)) = panic_at(line) {
            let b = bin_from_panic(&name, &path);
            panics.entry(name).or_default().push(b);
            continue;
        }
        let t = l.trim();
        if let (Some(g), Some(id)) = (&group, unittest_id(t)) {
            ids.entry(g.clone()).or_default().insert(id);
            continue;
        }
        if let Some(rest) = t.strip_prefix("test ") {
            if let Some(name) = rest.strip_suffix(" ... FAILED") {
                match bin.as_deref() {
                    Some(b) => p.fingerprints.push(format!("rs:{b}::{}", name.trim())),
                    None => no_bin.push(name.trim().to_string()),
                }
            }
        }
    }
    // every panic of the test's thread must point at the same place; otherwise unknown
    for name in no_bin {
        let b = match panics.get(&name).map(Vec::as_slice) {
            Some([Some(first), more @ ..]) if more.iter().all(|m| m.as_ref() == Some(first)) => {
                first.as_str()
            }
            _ => "?",
        };
        p.fingerprints.push(format!("rs:{b}::{name}"));
    }
    p.fingerprints.sort();
    p.fingerprints.dedup();
    p
}

/// Steps that only prepare the runner; a red one with no fingerprint = infrastructure, not verified.
fn is_setup_step(name: &str) -> bool {
    name == "Set up job" || name.starts_with("Run actions/")
}

/// Red = failure or timed_out. cancelled / skipped / neutral are neither red nor green.
fn red(c: Option<&str>) -> bool {
    matches!(c, Some("failure") | Some("timed_out"))
}

/// The Python test file a fingerprint points at.
pub fn test_file_of(fp: &str) -> Option<&str> {
    let rest = fp.strip_prefix("py:")?;
    Some(rest.split_once("::").map_or(rest, |(f, _)| f))
}

/// Impact of one fingerprint against the PR's changed files.
/// Ok(true) = overlap, Ok(false) = none, Err = cannot tell (counts as overlap).
fn impact_overlap(fp: &str, f: &Facts) -> Result<bool, String> {
    if let Some(test) = test_file_of(fp) {
        let src = f.py_sources.get(test).and_then(|s| s.as_deref());
        return py_overlap(test, &f.changed, src);
    }
    if let Some(rest) = fp.strip_prefix("rs:") {
        let (bin, _) = rest.split_once("::").ok_or("bad rust fingerprint")?;
        if bin == "?" {
            return Err("test binary not seen in the log, nor where its panic points".into());
        }
        let map = f.rust.as_ref().ok_or("head tree not readable")?;
        let crates = match bin.strip_prefix('@') {
            // the crate the test's own panic location is in, and every crate that depends on
            // it (directly or not): the test may live in any of them (ebb36 #666 M1)
            Some(dir) => {
                if !map.deps.contains_key(dir) {
                    return Err(format!("{dir}: Cargo.toml not read"));
                }
                let mut users = BTreeSet::from([dir.to_string()]);
                loop {
                    let before = users.len();
                    for (c, deps) in &map.deps {
                        // a crate whose dependencies were not understood may use it
                        let deps = deps
                            .as_ref()
                            .ok_or_else(|| format!("{c}: a dependency entry was not understood"))?;
                        let uses = deps.iter().any(|d| users.contains(d));
                        if uses {
                            users.insert(c.clone());
                        }
                    }
                    if users.len() == before {
                        break;
                    }
                }
                users
            }
            None => {
                let crates = map.bins.get(bin).ok_or("binary matches no crate")?;
                if crates.len() != 1 {
                    return Err(format!("binary matches {} crates", crates.len()));
                }
                crates.clone()
            }
        };
        let mut closure = BTreeSet::new();
        let mut todo: Vec<String> = crates.iter().cloned().collect();
        while let Some(c) = todo.pop() {
            if !closure.insert(c.clone()) {
                continue;
            }
            let deps = map
                .deps
                .get(&c)
                .ok_or_else(|| format!("{c}: Cargo.toml not read"))?
                .as_ref()
                .ok_or_else(|| format!("{c}: a dependency entry was not understood"))?;
            todo.extend(deps.iter().cloned());
        }
        return Ok(f.changed.iter().any(|ch| {
            RUST_SHARED.contains(&ch.path.as_str())
                || ch.path.starts_with(".cargo/")
                || closure
                    .iter()
                    .any(|d| ch.path.starts_with(&format!("{d}/")))
        }));
    }
    Err("unknown fingerprint kind".into())
}

/// A green rerun of `a`: another attempt / run of the same cell on the same head that
/// concluded success AND in which every step that was red in `a` ran and succeeded
/// (a cell that only printed a notice is not a rerun of the test).
fn green_rerun(a: &JobAttempt, siblings: &[&JobAttempt], failed_steps: &[String]) -> bool {
    !failed_steps.is_empty()
        && siblings.iter().any(|s| {
            (s.run_id, s.attempt) != (a.run_id, a.attempt)
                && s.conclusion.as_deref() == Some("success")
                && failed_steps.iter().all(|fs| {
                    s.steps
                        .iter()
                        .any(|st| &st.name == fs && st.conclusion.as_deref() == Some("success"))
                })
        })
}

/// Judge every red attempt. `prior` = entries already in the register (for rule 7).
pub fn judge(f: &Facts, prior: &[Entry]) -> Vec<Entry> {
    let cell = |a: &JobAttempt| (a.workflow.clone(), a.job.clone());
    let mut by_cell: BTreeMap<(String, String), Vec<&JobAttempt>> = BTreeMap::new();
    for a in &f.attempts {
        by_cell.entry(cell(a)).or_default().push(a);
    }
    let mut out = vec![];
    for a in &f.attempts {
        if !red(a.conclusion.as_deref()) {
            continue;
        }
        let siblings = &by_cell[&cell(a)];
        let mut cell_attempts: Vec<String> = siblings
            .iter()
            .map(|s| {
                format!(
                    "{}/{}:{}",
                    s.run_id,
                    s.attempt,
                    s.conclusion.as_deref().unwrap_or("none")
                )
            })
            .collect();
        cell_attempts.sort();
        let failed_steps: Vec<String> = a
            .steps
            .iter()
            .filter(|s| red(s.conclusion.as_deref()))
            .map(|s| s.name.clone())
            .collect();
        let parsed = a.log.as_deref().map(parse_log);
        let green = green_rerun(a, siblings, &failed_steps);
        let d = decide(f, &failed_steps, parsed.as_ref(), green, prior);
        out.push(Entry {
            repo: f.repo.clone(),
            pr: f.pr,
            head: f.head.clone(),
            base: f.base.clone(),
            workflow: a.workflow.clone(),
            job: a.job.clone(),
            run_id: a.run_id,
            attempt: a.attempt,
            job_id: a.job_id,
            failed_steps,
            coarse: parsed.as_ref().is_some_and(|p| p.coarse),
            fingerprints: parsed.map(|p| p.fingerprints).unwrap_or_default(),
            verdict: d.verdict.into(),
            reason: d.reason,
            cell_attempts,
            precedent_ok: d.precedent_ok,
        });
    }
    out
}

struct Decision {
    verdict: &'static str,
    reason: String,
    precedent_ok: bool,
}

fn decide(
    f: &Facts,
    failed_steps: &[String],
    parsed: Option<&Parsed>,
    green: bool,
    prior: &[Entry],
) -> Decision {
    let no = |verdict: &'static str, reason: String| Decision {
        verdict,
        reason,
        precedent_ok: false,
    };
    const U: &str = "unclassified";
    // 1. log readable
    let Some(p) = parsed else {
        return no(U, "log_unreadable".into());
    };
    // 2. a fingerprint; without one, a lost runner or a red setup step = infrastructure
    if p.fingerprints.is_empty() {
        if p.runner_lost {
            return no("infra_unverified", "runner_lost".into());
        }
        if !failed_steps.is_empty() && failed_steps.iter().all(|s| is_setup_step(s)) {
            return no(
                "infra_unverified",
                format!("setup_step: {}", failed_steps.join(", ")),
            );
        }
        return no(U, "no_fingerprint".into());
    }
    // 3. ci-shard itself complained (missing / duplicate / interrupted files)
    if !p.shard_problems.is_empty() {
        return no(U, format!("shard_problem: {}", p.shard_problems.join("; ")));
    }
    if f.pr.is_none() {
        return Decision {
            verdict: U,
            reason: "main_push_recorded".into(),
            precedent_ok: green,
        };
    }
    // 4. the failing test file was added by this PR
    for fp in &p.fingerprints {
        if let Some(t) = test_file_of(fp) {
            if f.changed.iter().any(|c| c.path == t && c.status == "added") {
                return no(U, format!("pr_new_test: {t}"));
            }
        }
    }
    // 5. impact set vs changed files
    for fp in &p.fingerprints {
        match impact_overlap(fp, f) {
            Ok(false) => {}
            Ok(true) => return no(U, format!("impact_overlap: {fp}")),
            Err(e) => return no(U, format!("impact_unknown: {fp}: {e}")),
        }
    }
    // from here on rules 1–5 held: this red may serve as precedent for others
    let ok = |verdict: &'static str, reason: String| Decision {
        verdict,
        reason,
        precedent_ok: true,
    };
    // 6. the red steps went green in another attempt / run of the same cell on the same head
    if !green {
        return ok(U, "no_green_rerun".into());
    }
    // 7. the same fingerprint was red on another head, in an entry that may serve as precedent
    for fp in &p.fingerprints {
        let seen = prior.iter().any(|e| {
            e.repo == f.repo && e.head != f.head && e.precedent_ok && e.fingerprints.contains(fp)
        });
        if !seen {
            return ok(U, format!("no_precedent: {fp}"));
        }
    }
    ok("suspected_flaky", "all seven rules hold".into())
}

pub fn entry_key(e: &Entry) -> (String, u64, u32) {
    (e.repo.clone(), e.job_id, e.attempt)
}

/// Entries not already in the register (same repo + job id + attempt = same entry).
pub fn new_entries(prior: &[Entry], fresh: Vec<Entry>) -> Vec<Entry> {
    let mut seen: BTreeSet<_> = prior.iter().map(entry_key).collect();
    fresh
        .into_iter()
        .filter(|e| seen.insert(entry_key(e)))
        .collect()
}

pub fn entry_json(e: &Entry, at: &str) -> Value {
    json!({
        "repo": e.repo, "pr": e.pr, "head": e.head, "base": e.base,
        "workflow": e.workflow, "job": e.job,
        "run_id": e.run_id, "attempt": e.attempt, "job_id": e.job_id,
        "failed_steps": e.failed_steps, "fingerprints": e.fingerprints, "coarse": e.coarse,
        "verdict": e.verdict, "reason": e.reason, "cell_attempts": e.cell_attempts,
        "precedent_ok": e.precedent_ok,
        "at": at,
    })
}

fn strs(v: &Value, k: &str) -> Option<Vec<String>> {
    v.get(k)?
        .as_array()?
        .iter()
        .map(|x| x.as_str().map(str::to_string))
        .collect()
}

/// One register line back. A line that does not parse = error (the register is never guessed at).
pub fn entry_from_json(v: &Value) -> Option<Entry> {
    let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_string);
    Some(Entry {
        repo: s("repo")?,
        pr: match v.get("pr")? {
            Value::Null => None,
            x => Some(x.as_u64()?),
        },
        head: s("head")?,
        base: s("base")?,
        workflow: s("workflow")?,
        job: s("job")?,
        run_id: v.get("run_id")?.as_u64()?,
        attempt: u32::try_from(v.get("attempt")?.as_u64()?).ok()?,
        job_id: v.get("job_id")?.as_u64()?,
        failed_steps: strs(v, "failed_steps")?,
        fingerprints: strs(v, "fingerprints")?,
        coarse: v.get("coarse")?.as_bool()?,
        verdict: s("verdict")?,
        reason: s("reason")?,
        cell_attempts: strs(v, "cell_attempts")?,
        precedent_ok: v.get("precedent_ok")?.as_bool()?,
    })
}

pub fn read_register(text: &str) -> Result<Vec<Entry>, String> {
    let mut out = vec![];
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let v: Value =
            serde_json::from_str(line).map_err(|e| format!("register line {}: {e}", i + 1))?;
        out.push(
            entry_from_json(&v).ok_or_else(|| format!("register line {}: missing field", i + 1))?,
        );
    }
    Ok(out)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Suggestion {
    pub fingerprint: String,
    /// red entries with a test-level fingerprint (coarse ones are not counted here)
    pub reds: usize,
    pub platforms: BTreeSet<String>,
    pub heads: BTreeSet<String>,
}

/// "Keeps going red" list for dispatch: ≥3 reds (test-level fingerprints only) or ≥2
/// platforms, from ≥2 heads. Infrastructure entries carry no fingerprint, so they never count.
pub fn suggest(entries: &[Entry]) -> Vec<Suggestion> {
    type Slot = (BTreeSet<(u64, u32)>, BTreeSet<String>, BTreeSet<String>);
    let mut by: BTreeMap<&str, Slot> = BTreeMap::new();
    for e in entries.iter().filter(|e| e.verdict != "infra_unverified") {
        for fp in &e.fingerprints {
            let slot = by.entry(fp).or_default();
            if !e.coarse {
                slot.0.insert((e.job_id, e.attempt));
            }
            slot.1.insert(e.job.clone());
            slot.2.insert(e.head.clone());
        }
    }
    by.into_iter()
        .filter(|(_, (reds, plats, heads))| {
            (reds.len() >= 3 || plats.len() >= 2) && heads.len() >= 2
        })
        .map(|(fp, (reds, plats, heads))| Suggestion {
            fingerprint: fp.to_string(),
            reds: reds.len(),
            platforms: plats,
            heads,
        })
        .collect()
}

// ---------- head tree -> RustMap ----------

/// Read `[package] name`, `[lib] name`, `[[bin]] name` and every path dependency from a
/// Cargo.toml. Returns (package name, lib name, bin names, path deps (relative), understood).
pub struct Manifest {
    pub package: Option<String>,
    pub lib: Option<String>,
    pub bins: Vec<String>,
    pub path_deps: Vec<String>,
    /// false = some dependency entry was not understood
    pub understood: bool,
}

fn unquote(s: &str) -> Option<String> {
    let s = s.trim();
    let s = s.strip_prefix('"')?.strip_suffix('"')?;
    if s.contains('"') || s.contains('\\') {
        return None;
    }
    Some(s.to_string())
}

/// The value of `key = "..."` inside an inline table `{ ... }` text.
fn inline_value(table: &str, key: &str) -> Option<String> {
    for part in table.split(',') {
        if let Some((k, v)) = part.split_once('=') {
            if k.trim() == key {
                return unquote(v);
            }
        }
    }
    None
}

fn is_dep_section(sec: &str) -> bool {
    let last = sec.rsplit('.').next().unwrap_or("");
    let deps = matches!(
        last,
        "dependencies" | "dev-dependencies" | "build-dependencies"
    );
    deps && (!sec.contains('.') || sec.starts_with("target.") || sec.starts_with("workspace."))
}

pub fn parse_manifest(text: &str) -> Manifest {
    let mut m = Manifest {
        package: None,
        lib: None,
        bins: vec![],
        path_deps: vec![],
        understood: true,
    };
    let mut sec = String::new();
    // `[dependencies.foo]` style: a table that names one dependency
    let mut dep_table = false;
    // join a value whose [ ] / { } span several lines into one logical line
    let mut logical: Vec<String> = vec![];
    let mut pending = String::new();
    for raw in text.lines() {
        let line = raw.split(" #").next().unwrap_or(raw).trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        pending.push_str(line);
        pending.push(' ');
        let open = pending.matches(['[', '{']).count();
        let close = pending.matches([']', '}']).count();
        if open <= close {
            logical.push(pending.trim().to_string());
            pending.clear();
        }
    }
    if !pending.is_empty() {
        m.understood = false;
    }
    for line in &logical {
        let line = line.as_str();
        if let Some(h) = line.strip_prefix("[[").and_then(|l| l.strip_suffix("]]")) {
            sec = format!("[[{}]]", h.trim());
            dep_table = false;
            continue;
        }
        if let Some(h) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            let h = h.trim().replace(['\'', '"'], "");
            dep_table = false;
            if let Some((parent, _name)) = h.rsplit_once('.') {
                if is_dep_section(parent) {
                    dep_table = true;
                }
            }
            sec = h;
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            if is_dep_section(&sec) || dep_table {
                m.understood = false;
            }
            continue;
        };
        let (k, v) = (k.trim(), v.trim());
        match sec.as_str() {
            "package" if k == "name" => m.package = unquote(v),
            "lib" if k == "name" => m.lib = unquote(v),
            "[[bin]]" if k == "name" => {
                if let Some(n) = unquote(v) {
                    m.bins.push(n);
                }
            }
            _ if dep_table => {
                if k == "path" {
                    match unquote(v) {
                        Some(p) => m.path_deps.push(p),
                        None => m.understood = false,
                    }
                } else if k == "workspace" {
                    m.understood = false;
                }
            }
            _ if is_dep_section(&sec) => {
                if v.starts_with('"') {
                    // version-only registry dependency
                } else if let Some(t) = v.strip_prefix('{').and_then(|x| x.strip_suffix('}')) {
                    if t.contains("path") {
                        match inline_value(t, "path") {
                            Some(p) => m.path_deps.push(p),
                            None => m.understood = false,
                        }
                    }
                    if inline_value(t, "workspace").is_some() || t.contains("workspace") {
                        // inherits from [workspace.dependencies]; not followed here
                        m.understood = false;
                    }
                } else {
                    m.understood = false;
                }
            }
            _ => {}
        }
    }
    m
}

/// Normalise "crates/a/../b" style joins. None = escapes the repo.
pub fn join_rel(dir: &str, rel: &str) -> Option<String> {
    let mut parts: Vec<&str> = dir.split('/').filter(|p| !p.is_empty()).collect();
    for seg in rel.split('/') {
        match seg {
            "" | "." => {}
            ".." => {
                parts.pop()?;
            }
            s => parts.push(s),
        }
    }
    Some(parts.join("/"))
}

/// Build the RustMap from the head tree's file list and a reader for Cargo.toml files.
/// Every directory holding a Cargo.toml with a [package] is a crate.
pub fn rust_map(
    files: &[String],
    read: &dyn Fn(&str) -> Option<String>,
) -> Result<RustMap, String> {
    let mut map = RustMap::default();
    let crate_dirs: Vec<String> = files
        .iter()
        .filter_map(|f| f.strip_suffix("/Cargo.toml").map(str::to_string))
        .collect();
    for dir in &crate_dirs {
        let text = read(&format!("{dir}/Cargo.toml"))
            .ok_or_else(|| format!("{dir}/Cargo.toml unreadable"))?;
        let m = parse_manifest(&text);
        let Some(pkg) = m.package.clone() else {
            continue;
        };
        let norm = |s: &str| s.replace('-', "_");
        let mut bins = BTreeSet::new();
        bins.insert(norm(m.lib.as_deref().unwrap_or(&pkg)));
        for b in &m.bins {
            bins.insert(norm(b));
        }
        let prefix = format!("{dir}/");
        for f in files {
            let Some(rest) = f.strip_prefix(&prefix) else {
                continue;
            };
            if let Some(stem) = rest
                .strip_prefix("tests/")
                .and_then(|t| t.strip_suffix(".rs"))
            {
                if !stem.contains('/') {
                    bins.insert(norm(stem));
                }
            }
            if let Some(stem) = rest
                .strip_prefix("src/bin/")
                .and_then(|t| t.strip_suffix(".rs"))
            {
                if !stem.contains('/') {
                    bins.insert(norm(stem));
                }
            }
        }
        for b in bins {
            map.bins.entry(b).or_default().insert(dir.clone());
        }
        let deps = if m.understood {
            let mut d = BTreeSet::new();
            let mut ok = true;
            for p in &m.path_deps {
                match join_rel(dir, p) {
                    Some(x) => {
                        d.insert(x);
                    }
                    None => ok = false,
                }
            }
            ok.then_some(d)
        } else {
            None
        };
        map.deps.insert(dir.clone(), deps);
    }
    Ok(map)
}
