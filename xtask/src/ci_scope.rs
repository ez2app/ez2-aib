//! `aib-xtask ci-scope` (S66-scope s1, 56cda, DESIGN-ci-scope-smoke-56cda-1116 r1 §2, 519d0
//! 11-16 23:53 / 11-17 00:0x): which kind of change a pull request is, as one code with a reason.
//! The shadow version only prints and counts; no cell changes what it runs because of it (s3, the
//! smoke change, is dropped: once knives live in crates/*/knives/*.sh, Rust PRs stop touching
//! .github at all).
//!
//! Rules, first match wins:
//! 1. not a pull_request, refs or git unreadable, no changed file ⇒ `full`
//! 2. a `.github/` file other than aibd.yml ⇒ `full` (`other_workflow`)
//! 3. a file that is neither Rust nor docs (and not aibd.yml) ⇒ `full` (`mixed`)
//! 4. aibd.yml changed: only the `steps:` blocks of the rust / cloud-knives / cloud-host-knives
//!    jobs differ ⇒ `rust_aibd_steps`; any other part (top level, a job header, another job, jobs
//!    added / removed / renamed, a file not cut into jobs) ⇒ `full`
//! 5. otherwise `docs` (every file docs) or `rust` (Rust plus docs, at least one Rust file)
//!
//! "Rust" and "docs" are docs_only.py's `is_rust` / `is_doc` rules copied word for word (that
//! file is frozen; a test here reads its lists as text and fails when the two drift apart).

use std::collections::BTreeSet;
use std::path::Path;
use std::process::Command;

/// The workflow whose knife steps may change without the change counting as `.github` work.
pub const AIBD_YML: &str = ".github/workflows/aibd.yml";
/// Jobs whose `steps:` block may differ for `rust_aibd_steps`.
pub const KNIFE_JOBS: [&str; 3] = ["rust", "cloud-knives", "cloud-host-knives"];
/// The 1.x tests that read aibd.yml; a test elsewhere guards the list (every `aib/tests/*.py` /
/// `tests/*.py` that names aibd.yml must be here). Add only; removing one needs 1ced7's review.
pub const YML_READ_TESTS: [&str; 4] = [
    "aib/tests/test-ci-catchup-1ced7-1108.py",
    "aib/tests/test-ci-concurrency-1ced7-1108.py",
    "aib/tests/test-docs-only-ci-1ced7-1108.py",
    "tests/test_aib_prepr.py",
];

// docs_only.py's lists, copied word for word (parity test below).
const DOC_PREFIXES: [&str; 1] = ["docs/"];
const DOC_ROOT_GLOBS: [(&str, &str); 2] = [("SECURITY", ".md"), ("CODE_OF_CONDUCT", ".md")];
const DOC_EXCLUDE: [&str; 11] = [
    "docs/perf/timers.md",
    "docs/aibd/INSTALL.md",
    "docs/aibd/INSTALL.zh-TW.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.1.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.2.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.3.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.4.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.5.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.6.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.7.md",
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.8.md",
];
const RUST_FILES: [&str; 2] = ["Cargo.toml", "Cargo.lock"];
const RUST_PREFIXES: [&str; 1] = ["crates/"];

/// What `ci-scope` says.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    /// `docs`, `rust`, `rust_aibd_steps` or `full`.
    pub scope: &'static str,
    /// A short reason code (`push`, `unreadable`, `empty`, `other_workflow`, `mixed`,
    /// `aibd_knife_steps`, `aibd_toplevel`, `aibd_header`, `aibd_other_job`, `aibd_jobs_changed`,
    /// `aibd_unparsed`, `docs`, `rust`, `no_rust`).
    pub why: &'static str,
    /// One line for people: the first file or section that decided it.
    pub detail: String,
}

impl Verdict {
    fn new(scope: &'static str, why: &'static str, detail: impl Into<String>) -> Self {
        Self {
            scope,
            why,
            detail: detail.into(),
        }
    }

    /// The 1.x tests a light run would still have to run (only `rust_aibd_steps`).
    pub fn yml_tests(&self) -> &'static [&'static str] {
        if self.scope == "rust_aibd_steps" {
            &YML_READ_TESTS
        } else {
            &[]
        }
    }

    /// `$GITHUB_OUTPUT` lines.
    pub fn lines(&self) -> String {
        format!(
            "scope={}\nwhy={}\nyml_tests={}\n",
            self.scope,
            self.why,
            self.yml_tests().join(" ")
        )
    }

    /// One JSON object for the measurement ledger.
    pub fn to_json(&self, event: &str, base: &str, head: &str) -> String {
        serde_json::json!({
            "schema": "aib-xtask.ci-scope.v1",
            "event": event,
            "base": base,
            "head": head,
            "scope": self.scope,
            "why": self.why,
            "detail": self.detail,
            "yml_tests": self.yml_tests(),
        })
        .to_string()
    }
}

fn clean(path: &str) -> Option<&str> {
    let p = path.trim();
    if p.is_empty() || p.starts_with('/') || p.split('/').any(|s| s == "..") {
        None
    } else {
        Some(p)
    }
}

/// docs_only.py `is_doc`.
pub fn is_doc(path: &str) -> bool {
    let Some(p) = clean(path) else { return false };
    if !p.ends_with(".md") || DOC_EXCLUDE.contains(&p) {
        return false;
    }
    if DOC_PREFIXES.iter().any(|pre| p.starts_with(pre)) {
        return true;
    }
    // fnmatch `SECURITY*.md` at the root: `*` does not cross a folder, and there is no folder.
    !p.contains('/')
        && DOC_ROOT_GLOBS.iter().any(|(pre, suf)| {
            p.starts_with(pre) && p.ends_with(suf) && p.len() >= pre.len() + suf.len()
        })
}

/// docs_only.py `is_rust`.
pub fn is_rust(path: &str) -> bool {
    let Some(p) = clean(path) else { return false };
    RUST_FILES.contains(&p) || RUST_PREFIXES.iter().any(|pre| p.starts_with(pre))
}

/// aibd.yml cut into its parts: the top level (everything that is not inside a job), and per job
/// its header (job line through `steps:`) and its steps block. `None` = not cut (no `jobs:`, or
/// a job without `steps:`).
#[derive(Debug, PartialEq, Eq)]
struct Cut {
    top: String,
    /// (name, header, steps) in file order.
    jobs: Vec<(String, String, String)>,
}

fn job_name(line: &str) -> Option<&str> {
    let rest = line.strip_prefix("  ")?;
    if rest.starts_with(' ') || rest.starts_with('#') {
        return None;
    }
    let name = rest.trim_end().strip_suffix(':')?;
    (!name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'))
    .then_some(name)
}

/// Exactly `n` spaces, then something that is not a comment.
fn indent_is(line: &str, n: usize) -> bool {
    let rest = line.trim_start_matches(' ');
    line.len() - rest.len() == n && !rest.is_empty() && !rest.starts_with('#')
}

fn cut(yml: &str) -> Option<Cut> {
    let mut top = String::new();
    // (name, header, steps, saw `steps:`)
    let mut jobs: Vec<(String, String, String, bool)> = Vec::new();
    let mut in_jobs = false;
    for line in yml.split_inclusive('\n') {
        let bare = line.trim_end_matches(['\n', '\r']);
        if !in_jobs {
            top.push_str(line);
            in_jobs = bare == "jobs:";
            continue;
        }
        if !bare.is_empty() && !bare.starts_with(' ') && !bare.starts_with('#') {
            return None; // a top-level key after jobs: (aibd.yml has none): not cut
        }
        if let Some(name) = job_name(bare) {
            jobs.push((name.to_owned(), line.to_owned(), String::new(), false));
            continue;
        }
        // 5d33a b8b22215 L1: a 2-space line that is not a job name, or a job-level key (4 spaces,
        // not a comment) after `steps:`, would otherwise be read as steps: not cut ⇒ full.
        if indent_is(bare, 2) || (jobs.last().is_some_and(|j| j.3) && indent_is(bare, 4)) {
            return None;
        }
        match jobs.last_mut() {
            None => top.push_str(line),
            Some(job) if job.3 => job.2.push_str(line),
            Some(job) => {
                job.1.push_str(line);
                job.3 = bare == "    steps:";
            }
        }
    }
    if jobs.is_empty() || jobs.iter().any(|j| !j.3) {
        return None;
    }
    Some(Cut {
        top,
        jobs: jobs.into_iter().map(|(n, h, s, _)| (n, h, s)).collect(),
    })
}

impl Cut {
    #[cfg(test)]
    fn join(&self) -> String {
        let mut s = self.top.clone();
        for j in &self.jobs {
            s.push_str(&j.1);
            s.push_str(&j.2);
        }
        s
    }
}

/// Rule 4: how aibd.yml changed between `base` and `head`.
pub fn aibd_change(base: &str, head: &str) -> Verdict {
    let (Some(b), Some(h)) = (cut(base), cut(head)) else {
        return Verdict::new("full", "aibd_unparsed", AIBD_YML);
    };
    if b.top != h.top {
        return Verdict::new(
            "full",
            "aibd_toplevel",
            "aibd.yml top level (on / env / concurrency …)",
        );
    }
    let names = |c: &Cut| c.jobs.iter().map(|j| j.0.clone()).collect::<Vec<_>>();
    if names(&b) != names(&h) {
        return Verdict::new(
            "full",
            "aibd_jobs_changed",
            "aibd.yml jobs added, removed, renamed or moved",
        );
    }
    if KNIFE_JOBS.iter().any(|k| !names(&b).iter().any(|n| n == k)) {
        return Verdict::new(
            "full",
            "aibd_unparsed",
            "aibd.yml lacks one of the knife jobs",
        );
    }
    let mut steps = BTreeSet::new();
    for (bj, hj) in b.jobs.iter().zip(&h.jobs) {
        if bj.1 != hj.1 {
            return Verdict::new(
                "full",
                "aibd_header",
                format!("aibd.yml job {} header", bj.0),
            );
        }
        if bj.2 != hj.2 {
            if !KNIFE_JOBS.contains(&bj.0.as_str()) {
                return Verdict::new(
                    "full",
                    "aibd_other_job",
                    format!("aibd.yml job {} steps", bj.0),
                );
            }
            steps.insert(bj.0.clone());
        }
    }
    if steps.is_empty() {
        // same text once cut (only line endings differ, say): nothing a step reads changed
        return Verdict::new(
            "rust_aibd_steps",
            "aibd_knife_steps",
            "aibd.yml (no part changed)",
        );
    }
    Verdict::new(
        "rust_aibd_steps",
        "aibd_knife_steps",
        format!(
            "aibd.yml steps of {}",
            steps.into_iter().collect::<Vec<_>>().join(", ")
        ),
    )
}

/// The whole decision. `files` = changed paths (`None` = could not be listed); `yml` = aibd.yml at
/// the merge base and at the head (`None` when either cannot be read; only looked at when
/// aibd.yml is among `files`).
pub fn decide(event: &str, files: Option<&[String]>, yml: Option<(&str, &str)>) -> Verdict {
    if event != "pull_request" {
        return Verdict::new(
            "full",
            "push",
            format!("event {event} is not a pull_request"),
        );
    }
    let Some(files) = files else {
        return Verdict::new("full", "unreadable", "could not list the changed files");
    };
    let files: Vec<&str> = files
        .iter()
        .map(|f| f.trim())
        .filter(|f| !f.is_empty())
        .collect();
    if files.is_empty() {
        return Verdict::new("full", "empty", "no changed files");
    }
    if let Some(f) = files
        .iter()
        .find(|f| f.starts_with(".github/") && **f != AIBD_YML)
    {
        return Verdict::new("full", "other_workflow", *f);
    }
    if let Some(f) = files
        .iter()
        .find(|f| **f != AIBD_YML && !is_rust(f) && !is_doc(f))
    {
        return Verdict::new("full", "mixed", *f);
    }
    if files.contains(&AIBD_YML) {
        let Some((base, head)) = yml else {
            return Verdict::new(
                "full",
                "unreadable",
                "could not read aibd.yml at the base or the head",
            );
        };
        return aibd_change(base, head);
    }
    if files.iter().all(|f| is_doc(f)) {
        return Verdict::new("docs", "docs", format!("{} file(s), all docs", files.len()));
    }
    if files.iter().any(|f| is_rust(f)) {
        return Verdict::new(
            "rust",
            "rust",
            format!("{} file(s), Rust and docs", files.len()),
        );
    }
    Verdict::new("full", "no_rust", "no Rust file changed")
}

fn git(root: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .env("LC_ALL", "C")
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8(out.stdout).ok())
        .flatten()
}

/// Read the repo at `root` and decide (docs_only.py's `base...head`: from the merge base).
pub fn run(root: &Path, event: &str, base: &str, head: &str) -> Verdict {
    let files = if base.is_empty() || head.is_empty() {
        None
    } else {
        git(
            root,
            &[
                "diff",
                "--no-renames",
                "--name-only",
                &format!("{base}...{head}"),
            ],
        )
        .map(|s| s.lines().map(str::to_owned).collect::<Vec<_>>())
    };
    let yml = files
        .as_ref()
        .filter(|f| f.iter().any(|p| p.trim() == AIBD_YML))
        .and_then(|_| {
            let mb = git(root, &["merge-base", base, head])?;
            let b = git(root, &["show", &format!("{}:{AIBD_YML}", mb.trim())])?;
            let h = git(root, &["show", &format!("{head}:{AIBD_YML}")])?;
            Some((b, h))
        });
    decide(
        event,
        files.as_deref(),
        yml.as_ref().map(|(b, h)| (b.as_str(), h.as_str())),
    )
}

/// The 1.x tests under `root` that name aibd.yml (for the guard test).
#[cfg(test)]
pub fn yml_readers(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    for dir in ["aib/tests", "tests"] {
        let Ok(rd) = std::fs::read_dir(root.join(dir)) else {
            continue;
        };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if !name.ends_with(".py") {
                continue;
            }
            if std::fs::read_to_string(e.path()).is_ok_and(|t| t.contains("aibd.yml")) {
                out.push(format!("{dir}/{name}"));
            }
        }
    }
    out.sort();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
    }

    fn files(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| (*s).to_owned()).collect()
    }

    fn real_yml() -> String {
        std::fs::read_to_string(root().join(AIBD_YML)).unwrap_or_default()
    }

    /// aibd.yml with one part changed by `f` (the parts are unique, the text is not).
    fn with(yml: &str, f: impl FnOnce(&mut Cut)) -> String {
        let mut c = cut(yml).expect("aibd.yml cuts");
        f(&mut c);
        c.join()
    }

    fn job<'a>(c: &'a mut Cut, name: &str) -> &'a mut (String, String, String) {
        c.jobs
            .iter_mut()
            .find(|j| j.0 == name)
            .expect("job present")
    }

    #[test]
    fn the_real_aibd_yml_cuts_into_its_jobs_with_the_knife_jobs_present() {
        let yml = real_yml();
        let c = cut(&yml).expect("aibd.yml cuts");
        let names: Vec<_> = c.jobs.iter().map(|j| j.0.as_str()).collect();
        for k in KNIFE_JOBS {
            assert!(names.contains(&k), "{k} in {names:?}");
        }
        assert!(names.contains(&"changes"), "{names:?}");
        let whole: usize =
            c.top.len() + c.jobs.iter().map(|j| j.1.len() + j.2.len()).sum::<usize>();
        assert_eq!(whole, yml.len(), "every byte lands in one part");
    }

    #[test]
    fn a_knife_line_in_a_knife_job_is_rust_aibd_steps() {
        let base = real_yml();
        for k in KNIFE_JOBS {
            let head = with(&base, |c| job(c, k).2.push_str("          # knife added\n"));
            let v = aibd_change(&base, &head);
            assert_eq!(
                (v.scope, v.why),
                ("rust_aibd_steps", "aibd_knife_steps"),
                "{k}"
            );
            assert_eq!(v.yml_tests(), &YML_READ_TESTS);
        }
        assert_eq!(aibd_change(&base, &base).scope, "rust_aibd_steps");
    }

    #[test]
    fn a_header_another_job_or_the_top_level_is_full() {
        let base = real_yml();
        // a line right after `  rust:` (a line before it would land in the job above)
        let head = with(&base, |c| {
            let h = &mut job(c, "rust").1;
            let at = h.find('\n').map_or(h.len(), |i| i + 1);
            h.insert_str(at, "    timeout-minutes: 1\n");
        });
        assert_eq!(aibd_change(&base, &head).why, "aibd_header");
        let head = with(&base, |c| {
            job(c, "changes").2.push_str("          # other job\n")
        });
        assert_eq!(aibd_change(&base, &head).why, "aibd_other_job");
        let head = with(&base, |c| c.top.push_str("# top\n"));
        assert_eq!(aibd_change(&base, &head).why, "aibd_toplevel");
        let head = with(&base, |c| job(c, "rust").1.replace_range(..7, "  rust2:"));
        assert_eq!(aibd_change(&base, &head).why, "aibd_jobs_changed");
        assert_eq!(aibd_change("on: push\n", &base).why, "aibd_unparsed");
        // 5d33a L1: a job-level key after a knife job's steps, or a job line cut cannot read
        for k in KNIFE_JOBS {
            let head = with(&base, |c| job(c, k).2.push_str("    if: false\n"));
            assert_eq!(aibd_change(&base, &head).scope, "full", "{k}");
        }
        let head = with(&base, |c| job(c, "rust").2.push_str("  new job: # x\n"));
        assert_eq!(aibd_change(&base, &head).why, "aibd_unparsed");
    }

    #[test]
    fn the_file_rules_come_first_and_main_pushes_are_full() {
        let y = real_yml();
        let yml = Some((y.as_str(), y.as_str()));
        let d = |ev: &str, f: &[&str]| decide(ev, Some(&files(f)), yml);
        assert_eq!(d("push", &["crates/a/src/lib.rs"]).why, "push");
        assert_eq!(decide("pull_request", None, yml).why, "unreadable");
        assert_eq!(d("pull_request", &[" "]).why, "empty");
        assert_eq!(
            d("pull_request", &[AIBD_YML, ".github/workflows/ci.yml"]).why,
            "other_workflow"
        );
        assert_eq!(
            d("pull_request", &[AIBD_YML, "xtask/src/main.rs"]).why,
            "mixed"
        );
        assert_eq!(d("pull_request", &["docs/a.md"]).scope, "docs");
        assert_eq!(
            d("pull_request", &["crates/a/src/lib.rs", "docs/a.md"]).scope,
            "rust"
        );
        assert_eq!(d("pull_request", &["docs/aibd/INSTALL.md"]).why, "mixed");
        // built, not written out: test-docs-only-ci says no code may name a whitelisted doc (#831)
        const SEC: &str = concat!("SECURITY", ".md");
        const SEC_IN_DIR: &str = concat!("x/", "SECURITY", ".md");
        assert_eq!(d("pull_request", &[SEC]).scope, "docs");
        assert_eq!(d("pull_request", &[SEC_IN_DIR]).why, "mixed");
        assert_eq!(
            d("pull_request", &[AIBD_YML, "crates/a/src/lib.rs"]).scope,
            "rust_aibd_steps"
        );
        assert_eq!(
            decide("pull_request", Some(&files(&[AIBD_YML])), None).why,
            "unreadable"
        );
        assert!(!is_rust("../crates/x") && !is_doc("/docs/a.md"));
    }

    #[test]
    fn every_1x_test_that_reads_aibd_yml_is_on_the_list() {
        let found = yml_readers(&root());
        let listed: Vec<String> = YML_READ_TESTS.iter().map(|s| (*s).to_owned()).collect();
        let missing: Vec<_> = found.iter().filter(|f| !listed.contains(f)).collect();
        assert!(
            missing.is_empty(),
            "name aibd.yml but not in YML_READ_TESTS: {missing:?}"
        );
        for t in YML_READ_TESTS {
            assert!(root().join(t).is_file(), "{t} is listed but gone");
        }
    }

    /// The quoted strings of one `NAME = (…)` tuple in docs_only.py.
    fn py_tuple(py: &str, name: &str) -> Vec<String> {
        let start = py
            .find(&format!("\n{name} = ("))
            .unwrap_or_else(|| panic!("{name} in docs_only.py"));
        let body = &py[start + 1..];
        // one-line `NAME = ("a", "b")` ends on its own line; a multi-line one at the first `\n)`
        let first = body.lines().next().unwrap_or("");
        let end = if first.trim_end().ends_with(')') {
            first.len()
        } else {
            body.find("\n)").unwrap_or(body.len())
        };
        let mut out = Vec::new();
        for line in body[..end].lines() {
            let code = line.split('#').next().unwrap_or("");
            let mut rest = code;
            while let Some(a) = rest.find('"') {
                let after = &rest[a + 1..];
                let Some(b) = after.find('"') else { break };
                out.push(after[..b].to_owned());
                rest = &after[b + 1..];
            }
        }
        out
    }

    #[test]
    fn the_rust_and_docs_rules_match_frozen_docs_only_py() {
        let py = std::fs::read_to_string(root().join("aib/tools/release/docs_only.py"))
            .expect("docs_only.py");
        let own = |v: &[&str]| v.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert_eq!(py_tuple(&py, "DOC_EXCLUDE"), own(&DOC_EXCLUDE));
        assert_eq!(py_tuple(&py, "DOC_PREFIXES"), own(&DOC_PREFIXES));
        assert_eq!(py_tuple(&py, "RUST_FILES"), own(&RUST_FILES));
        assert_eq!(py_tuple(&py, "RUST_PREFIXES"), own(&RUST_PREFIXES));
        let globs: Vec<String> = DOC_ROOT_GLOBS
            .iter()
            .map(|(p, s)| format!("{p}*{s}"))
            .collect();
        assert_eq!(py_tuple(&py, "DOC_ROOT_GLOBS"), globs);
    }
}
