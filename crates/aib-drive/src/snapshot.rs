//! The read-only snapshot layer of aib-drive (M-drive-0, dry-run).
//!
//! It turns what GitHub and the review / acceptance files say into a
//! [`Snapshot`] for [`crate::decide::decide`]. It only reads: the GitHub side
//! is a [`Read`] that can do nothing but GET, and the files are read, never
//! written.
//!
//! Every value it cannot read in full is [`Known::Unknown`] with the reason
//! (DECISION-aib-drive-v1 item 1): a list that may have a next page, a call
//! that failed, a source that is not wired yet. Unknown is never turned into
//! empty, off or green.
//!
//! Not wired in this version, so always `Unknown` (nothing can merge on it):
//! who a review's writer is (B8 ledger records), the seats behind commit
//! authors, the freeze, the merge slot, knife log scans, `aib-release trial`.
//!
//! Sources with no `Unknown` slot in [`Snapshot`] fail the whole build: the
//! open-PR list, the review / acceptance files ([`read_notes`]) and the dm
//! queues behind `busy_seats` (a9136 PR-B). Every `gh` / `aib` call has a
//! deadline ([`run_fixed`]).

use crate::decide::{
    Cell, CellState, Known, Pr, ReviewLine, Roles, Seat, Sha, Snapshot, VerifyLine,
};
pub use crate::source::FileLine;
use crate::source::{busy_seats, is_seat, read_notes, run_fixed, Queue, CALL_TIMEOUT};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Duration;

/// Largest page GitHub gives; a list this long may have more, so it is
/// refused as incomplete.
pub const PER_PAGE: usize = 100;

/// Paths whose PRs a person merges (DECISION-aib-drive-v1 item 4).
pub const GUARDED: &[&str] = &[
    ".github/workflows/",
    "crates/aib-drive/",
    "RULES",
    "docs/RULES",
    "scripts/knife.py",
    "aib/tools/governance/",
];

/// A GET-only view of the GitHub API.
pub trait Read {
    /// `path` is an API path such as `repos/o/r/pulls?state=open`.
    fn get(&self, path: &str) -> Result<Value, String>;
}

/// [`Read`] through `gh api` (the token stays with `gh`).
pub struct GhRead {
    /// The `gh` program (tests point it at a fake).
    pub program: String,
    /// A call past this is killed and is an error.
    pub timeout: Duration,
}

impl GhRead {
    pub fn new() -> Self {
        Self {
            program: "gh".to_owned(),
            timeout: CALL_TIMEOUT,
        }
    }

    /// The whole argument list: `api` and the path, nothing that could
    /// change the method or send a body.
    pub fn args(path: &str) -> Result<Vec<String>, String> {
        // only REST paths under repos/ (1ced7 #685 N1: `graphql` is a POST in gh)
        if !path.starts_with("repos/") || path.contains(char::is_whitespace) {
            return Err(format!("path refused: {path:?}"));
        }
        Ok(vec!["api".to_owned(), path.to_owned()])
    }
}

impl Default for GhRead {
    fn default() -> Self {
        Self::new()
    }
}

impl Read for GhRead {
    fn get(&self, path: &str) -> Result<Value, String> {
        let out = run_fixed(&self.program, &Self::args(path)?, self.timeout)
            .map_err(|e| format!("gh api {path}: {e}"))?;
        serde_json::from_slice(&out).map_err(|_| format!("gh api {path}: bad json"))
    }
}

/// What the snapshot is built from besides GitHub.
pub struct Inputs<'a> {
    /// `owner/name`.
    pub repo: &'a str,
    pub roles: Roles,
    /// The folder holding `REVIEW-*.md` / `VERIFY-*.md`.
    pub pending: &'a Path,
    /// The head each PR had in the last dry-run record.
    pub prev_heads: &'a BTreeMap<u64, Sha>,
    /// The dm queues, read for `busy_seats`.
    pub queue: &'a dyn Queue,
    /// The seats whose queues are read.
    pub seats: &'a [Seat],
}

fn arr<'v>(v: &'v Value, what: &str) -> Result<&'v Vec<Value>, String> {
    v.as_array().ok_or_else(|| format!("{what}: not a list"))
}

fn st(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_owned)
}

/// A full page might hide a next one: incomplete.
fn whole<'v>(v: &'v Value, what: &str) -> Result<&'v Vec<Value>, String> {
    let a = arr(v, what)?;
    if a.len() >= PER_PAGE {
        return Err(format!("{what}: {} items, may have a next page", a.len()));
    }
    Ok(a)
}

/// Builds the snapshot. The open-PR list itself must be read in full, or
/// there is no snapshot at all.
pub fn build(gh: &dyn Read, inp: &Inputs<'_>) -> Result<Snapshot, String> {
    let repo = inp.repo;
    let list = gh.get(&format!(
        "repos/{repo}/pulls?state=open&per_page={PER_PAGE}"
    ))?;
    let list = whole(&list, "open pulls")?;
    let (required_cells, rules_version) = rules(gh, repo);
    let files = read_notes(inp.pending)?;
    let busy_seats = busy_seats(inp.queue, inp.seats)?;
    let mut prs = Vec::new();
    for p in list {
        prs.push(pr(gh, repo, p, &files, inp.prev_heads)?);
    }
    Ok(Snapshot {
        repo: repo.to_owned(),
        roles: inp.roles.clone(),
        freeze: Known::Unknown("freeze source not wired".into()),
        required_cells,
        rules_version,
        merge_slot_free: Known::Unknown("merge lock not wired".into()),
        busy_seats,
        prs,
        rulings: Vec::new(),
        done: BTreeSet::new(),
    })
}

/// Required check names from the rules on main, and a version for them.
fn rules(gh: &dyn Read, repo: &str) -> (Known<Vec<String>>, Known<String>) {
    // a cut list may miss a required_status_checks rule: the list would be
    // shorter, not empty, and decide only refuses an empty one (1ced7 #685 L1)
    let v = match gh.get(&format!(
        "repos/{repo}/rules/branches/main?per_page={PER_PAGE}"
    )) {
        Ok(v) => v,
        Err(e) => return (Known::Unknown(e.clone()), Known::Unknown(e)),
    };
    let rules = match whole(&v, "rules") {
        Ok(r) => r,
        Err(e) => return (Known::Unknown(e.clone()), Known::Unknown(e)),
    };
    let mut names = Vec::new();
    let mut ids = BTreeSet::new();
    for r in rules {
        if let Some(id) = r.get("ruleset_id").and_then(Value::as_u64) {
            ids.insert(id);
        }
        if r.get("type").and_then(Value::as_str) != Some("required_status_checks") {
            continue;
        }
        let checks = r
            .get("parameters")
            .and_then(|p| p.get("required_status_checks"))
            .and_then(Value::as_array);
        let Some(checks) = checks else {
            let e = "rules: required_status_checks unreadable".to_owned();
            return (Known::Unknown(e.clone()), Known::Unknown(e));
        };
        for c in checks {
            match st(c, "context") {
                Some(n) => names.push(n),
                None => {
                    let e = "rules: a required check has no name".to_owned();
                    return (Known::Unknown(e.clone()), Known::Unknown(e));
                }
            }
        }
    }
    names.sort();
    names.dedup();
    // the names themselves, so a renamed check is a new version
    let version = format!("rulesets {ids:?} checks {names:?}");
    (Known::Known(names), Known::Known(version))
}

/// `VERIFY-<kind>-…`: the kind is the part before the next `-`.
fn verify_kind(name: &str) -> Option<&str> {
    name.strip_prefix("VERIFY-")?
        .split('-')
        .next()
        .filter(|k| !k.is_empty())
}

/// One parsed note.
enum Note<'a> {
    Review(aib_core::firstline::Review<'a>),
    Verify(&'a str, aib_core::firstline::Verify<'a>),
}

/// Whether `line` names one of `commits`: a run of 7 to 40 hex digits that
/// one of them starts with.
fn names_a_commit(line: &str, commits: &[Sha]) -> bool {
    line.split(|c: char| !c.is_ascii_hexdigit())
        .filter(|t| (7..=40).contains(&t.len()))
        .any(|t| {
            let t = t.to_ascii_lowercase();
            commits.iter().any(|c| c.starts_with(&t))
        })
}

/// The review / acceptance lines whose head is one of `commits`. Who wrote
/// them is what the file name claims, so `by` is always `Unknown`.
///
/// A note whose first line names a commit of this PR but does not parse is
/// an error with the file name and why (Codex #685 M3): skipped, it would
/// read as "not reviewed". A broken note that names none of them could not
/// count for this PR anyway, so the old notes in `pending` do not stop it.
pub fn lines_for(
    files: &[FileLine],
    commits: &[Sha],
) -> Result<(Vec<ReviewLine>, Vec<VerifyLine>), String> {
    let on = |h: &str| commits.iter().any(|c| c == h);
    let unverified = || Known::Unknown("writer is a file name claim (B8 not wired)".into());
    let mut reviews = Vec::new();
    let mut verifies = Vec::new();
    for f in files {
        let parsed = if f.name.starts_with("REVIEW-") {
            aib_core::firstline::parse_review(&f.line)
                .map(Note::Review)
                .map_err(|e| e.to_string())
        } else {
            verify_kind(&f.name)
                .ok_or_else(|| "no kind in the file name".to_owned())
                .and_then(|k| {
                    aib_core::firstline::parse_verify(&f.line)
                        .map(|v| Note::Verify(k, v))
                        .map_err(|e| e.to_string())
                })
        };
        match parsed {
            Ok(Note::Review(r)) if on(r.head) => {
                if let Some(l) = ReviewLine::from_parsed(unverified(), &f.name, &r) {
                    reviews.push(l);
                }
            }
            Ok(Note::Verify(kind, v)) if on(v.head) => {
                verifies.push(VerifyLine::from_parsed(unverified(), &f.name, kind, &v));
            }
            Ok(_) => {}
            Err(e) if names_a_commit(&f.line, commits) => {
                return Err(format!("{}: {e}", f.name));
            }
            Err(_) => {}
        }
    }
    Ok((reviews, verifies))
}

fn pr(
    gh: &dyn Read,
    repo: &str,
    p: &Value,
    files: &[FileLine],
    prev: &BTreeMap<u64, Sha>,
) -> Result<Pr, String> {
    let number = p
        .get("number")
        .and_then(Value::as_u64)
        .ok_or("pull without a number")?;
    let head = p.get("head").ok_or("pull without head")?;
    let base = p.get("base").ok_or("pull without base")?;
    let head_sha = st(head, "sha").ok_or("pull without head sha")?;
    let head_ref = st(head, "ref").ok_or("pull without head ref")?;
    // no label list is not "no labels": needs-verify would read as none
    let labels: Vec<String> = p
        .get("labels")
        .and_then(Value::as_array)
        .ok_or("pull without labels")?
        .iter()
        .map(|l| st(l, "name").ok_or("label without a name"))
        .collect::<Result<_, _>>()?;
    let commits = match commits(gh, repo, number) {
        // the list must end at the head the pull list gave: otherwise the PR
        // moved between the two reads
        Known::Known(c) if c.last() != Some(&head_sha) => {
            Known::Unknown("commits do not end at the head (moved between reads)".into())
        }
        c => c,
    };
    let (reviews, verifies) = match &commits {
        Known::Known(c) => lines_for(files, c)?,
        Known::Unknown(_) => (Vec::new(), Vec::new()),
    };
    Ok(Pr {
        number,
        // every PR is pushed by one GitHub account: the seat is the one the
        // branch name claims (only used to address nudges)
        owner: owner_claim(&head_ref),
        head_ref: head_ref.clone(),
        base_ref: st(base, "ref").ok_or("pull without base ref")?,
        base_head: st(base, "sha").ok_or("pull without base sha")?,
        expected_base: None,
        draft: p.get("draft").and_then(Value::as_bool).unwrap_or(true),
        blocks_release: labels.iter().any(|l| l == "release-blocker"),
        waiting_secs: 0,
        prev_head: prev.get(&number).cloned(),
        authors: Known::Unknown("commit authors are not mapped to seats yet".into()),
        touches_guarded: guarded(gh, repo, number),
        cells: cells(gh, repo, number, &head_sha, p),
        stale_runs: Vec::new(),
        knives: Known::Unknown("knife log scan not wired".into()),
        reviews,
        verifies,
        needs_verify: labels
            .iter()
            .filter_map(|l| l.strip_prefix("needs-verify:"))
            .map(str::to_owned)
            .collect(),
        verify_carry: None,
        integ_parts: (head_ref.starts_with("int/") || head_ref.starts_with("integ"))
            .then(|| Known::Unknown("integration parts not wired".into())),
        trial: Known::Unknown("aib-release trial not wired".into()),
        reviewer: None,
        commits,
        head: head_sha,
    })
}

fn commits(gh: &dyn Read, repo: &str, n: u64) -> Known<Vec<Sha>> {
    let path = format!("repos/{repo}/pulls/{n}/commits?per_page={PER_PAGE}");
    let read = gh.get(&path).and_then(|v| {
        whole(&v, "commits")?
            .iter()
            .map(|c| st(c, "sha").ok_or_else(|| "commit without sha".to_owned()))
            .collect::<Result<Vec<Sha>, String>>()
    });
    match read {
        Ok(c) if !c.is_empty() => Known::Known(c),
        Ok(_) => Known::Unknown("no commits listed".into()),
        Err(e) => Known::Unknown(e),
    }
}

fn guarded(gh: &dyn Read, repo: &str, n: u64) -> Known<bool> {
    let path = format!("repos/{repo}/pulls/{n}/files?per_page={PER_PAGE}");
    let read = gh.get(&path).and_then(|v| {
        let mut names = Vec::new();
        for f in whole(&v, "files")? {
            names.push(st(f, "filename").ok_or_else(|| "file without a name".to_owned())?);
            // a file moved out of a guarded path touches it too (1ced7 #685 L2)
            if let Some(old) = st(f, "previous_filename") {
                names.push(old);
            }
        }
        Ok::<_, String>(names)
    });
    match read {
        Ok(names) => Known::Known(
            names
                .iter()
                .any(|f| GUARDED.iter().any(|g| f.starts_with(g))),
        ),
        Err(e) => Known::Unknown(e),
    }
}

/// When the PR last became ready: the last `ready_for_review`, or its
/// creation if it never was a draft. `Err` = cannot tell.
fn ready_at(gh: &dyn Read, repo: &str, n: u64, p: &Value) -> Result<String, String> {
    let path = format!("repos/{repo}/issues/{n}/timeline?per_page={PER_PAGE}");
    let v = gh.get(&path)?;
    let events = whole(&v, "timeline")?;
    let ready = events
        .iter()
        .filter(|e| st(e, "event").as_deref() == Some("ready_for_review"))
        .filter_map(|e| st(e, "created_at"))
        .max();
    let drafted = events
        .iter()
        .any(|e| st(e, "event").as_deref() == Some("convert_to_draft"));
    match ready {
        Some(t) => Ok(t),
        None if !drafted && p.get("draft").and_then(Value::as_bool) == Some(false) => {
            st(p, "created_at").ok_or_else(|| "pull without created_at".into())
        }
        None => Err("ready time unknown".into()),
    }
}

/// `…/actions/runs/<id>/job/…` ⇒ the run id.
fn run_id(details_url: &str) -> Option<u64> {
    let rest = details_url.split("/actions/runs/").nth(1)?;
    rest.split('/').next()?.parse().ok()
}

/// One check run as a cell. `None` = a state this version cannot read.
pub fn cell_of(c: &Value, ready: &str) -> Option<Cell> {
    let state = match (st(c, "status").as_deref(), st(c, "conclusion").as_deref()) {
        (Some("completed"), Some("success")) => CellState::Success,
        (Some("completed"), Some("failure" | "timed_out" | "startup_failure")) => {
            CellState::Failure
        }
        (Some("completed"), Some("cancelled")) => CellState::Cancelled,
        (Some("completed"), Some("skipped")) => CellState::Skipped,
        (Some("completed"), Some("neutral")) => CellState::Neutral,
        (Some("completed"), _) => return None,
        (Some(_), _) => CellState::Pending,
        (None, _) => return None,
    };
    let started = st(c, "started_at")?;
    Some(Cell {
        name: st(c, "name")?,
        run: st(c, "details_url")
            .as_deref()
            .and_then(run_id)
            .or_else(|| c.get("id").and_then(Value::as_u64))?,
        state,
        // RFC 3339 in UTC (`…Z`) compares as text
        after_ready: started.as_str() >= ready,
        cancelled_by_us: false,
        // the journal is not wired: whether this head was re-run already is
        // not known, so no re-run is proposed (a63ab D9: at most one)
        rerun_done: true,
        class: None,
    })
}

fn cells(gh: &dyn Read, repo: &str, n: u64, head: &str, p: &Value) -> Known<Vec<Cell>> {
    let ready = match ready_at(gh, repo, n, p) {
        Ok(t) => t,
        Err(e) => return Known::Unknown(e),
    };
    let path = format!("repos/{repo}/commits/{head}/check-runs?filter=latest&per_page={PER_PAGE}");
    let v = match gh.get(&path) {
        Ok(v) => v,
        Err(e) => return Known::Unknown(e),
    };
    let Some(runs) = v.get("check_runs").and_then(Value::as_array) else {
        return Known::Unknown("check runs unreadable".into());
    };
    let total = v.get("total_count").and_then(Value::as_u64);
    if total != Some(runs.len() as u64) || runs.len() >= PER_PAGE {
        return Known::Unknown(format!(
            "check runs: {} of {total:?}, may have a next page",
            runs.len()
        ));
    }
    let mut out = Vec::new();
    for c in runs {
        match cell_of(c, &ready) {
            Some(cell) => out.push(cell),
            None => return Known::Unknown(format!("check run unreadable: {c}")),
        }
    }
    Known::Known(out)
}

/// The seat code a branch name claims (`…-a9136-1111`): the last `-` / `/`
/// part that is a seat code; `unknown` if none.
pub fn owner_claim(head_ref: &str) -> Seat {
    head_ref
        .rsplit(['-', '/'])
        .find(|p| is_seat(p))
        .unwrap_or("unknown")
        .to_owned()
}
