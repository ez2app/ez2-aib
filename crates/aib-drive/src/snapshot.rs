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
//! Read the way the seats work today (F9, 519d0 11-15 S79-dec): a review's
//! writer is the seat its file name claims, the commit authors are the seat
//! the branch name claims plus the seats its merge commits name (fast-forward,
//! cherry-pick and rebase bring in no merge to read), a
//! required cell is its newest run, the knife scan is the newest receipt
//! about the head (`RECEIPT-*` / `TEST-*`, the shared receipt-run.sh lines) and the
//! freeze is `PUSH-FREEZE` in the notes folder; an integration (`int/`,
//! `integ/`; not `integ-src/`) holds the other open PRs whose commits it has
//! (F9b). These are claims, not identities (B8 ledger records will make them
//! stronger later).
//!
//! Not wired in this version, so always `Unknown` (nothing can merge on it):
//! the merge slot, `aib-release trial`.
//!
//! Sources with no `Unknown` slot in [`Snapshot`] fail the whole build: the
//! open-PR list, the review / acceptance files ([`read_notes`]) and the dm
//! queues behind `busy_seats` (a9136 PR-B). A note that reads but whose
//! first line does not parse is not such a source: it makes only the PR whose
//! head it names unknown ([`lines_for`], n-1). Every `gh` / `aib` call has a
//! deadline ([`run_fixed`](crate::source::run_fixed)).

use crate::decide::{
    Cell, CellState, IntegPart, KnifeScan, Known, Pr, ReviewLine, Roles, Seat, Sha, Snapshot,
    VerifyLine,
};
pub use crate::source::FileLine;
use crate::source::{busy_seats, is_seat, read_notes, Queue};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

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

pub use aib_ghread::{GhRead, Read};

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
    let receipts = knife_receipts(inp.pending);
    let mut prs = Vec::new();
    for p in list {
        prs.push(pr(gh, repo, p, &files, &receipts, inp.prev_heads)?);
    }
    // an integration's originals are the other open PRs it holds commits of
    let parts: Vec<_> = prs
        .iter()
        .map(|p| p.integ_parts.as_ref().map(|_| parts_of(p, &prs)))
        .collect();
    for (p, parts) in prs.iter_mut().zip(parts) {
        if parts.is_some() {
            p.integ_parts = parts;
        }
    }
    Ok(Snapshot {
        repo: repo.to_owned(),
        roles: inp.roles.clone(),
        freeze: freeze(inp.pending),
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

/// What the review / acceptance notes say about one PR.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PrNotes {
    pub reviews: Vec<ReviewLine>,
    pub verifies: Vec<VerifyLine>,
    /// Notes about this PR's head whose first line does not parse: `file: why`.
    pub unknown: Vec<String>,
}

/// The seat a note's file name claims as its writer: the last `-` part that
/// is a seat code (`REVIEW-drive-wl1-5d33a-ae9e225f.md` ⇒ `5d33a`). A claim,
/// not an identity: decide only compares it with the commit authors.
pub fn writer_claim(name: &str) -> Known<Seat> {
    let stem = name.rsplit_once('.').map_or(name, |(s, _)| s);
    match stem.split('-').skip(1).filter(|p| is_seat(p)).last() {
        Some(s) => Known::Known(s.to_owned()),
        None => Known::Unknown("the file name claims no seat".into()),
    }
}

/// The review / acceptance lines whose head is one of `commits` (oldest
/// first, the PR's head last). Who wrote them is what the file name claims
/// ([`writer_claim`]).
///
/// A note whose first line names this PR's head but does not parse goes to
/// `unknown` with the file name and why (Codex #685 M3: skipped, it would read
/// as "not reviewed"). It makes only this PR unknown, never the round (n-1,
/// 519d0 11-14: `pending` keeps old notes whose first line says 要改 / 需修).
/// A broken note about an older commit, or about no commit of this PR, is
/// left out: it can only drop a link from the review chain, which blocks.
pub fn lines_for(files: &[FileLine], commits: &[Sha]) -> PrNotes {
    let on = |h: &str| commits.iter().any(|c| c == h);
    let head = &commits[commits.len().saturating_sub(1)..];
    let mut reviews = Vec::new();
    let mut verifies = Vec::new();
    let mut unknown = Vec::new();
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
                if let Some(l) = ReviewLine::from_parsed(writer_claim(&f.name), &f.name, &r) {
                    reviews.push(l);
                }
            }
            Ok(Note::Verify(kind, v)) if on(v.head) => {
                verifies.push(VerifyLine::from_parsed(
                    writer_claim(&f.name),
                    &f.name,
                    kind,
                    &v,
                ));
            }
            Ok(_) => {}
            Err(e) if names_a_commit(&f.line, head) => {
                unknown.push(crate::observe::clean(&format!("{}: {e}", f.name), MAX_NOTE));
            }
            Err(_) => {}
        }
    }
    PrNotes {
        reviews,
        verifies,
        unknown,
    }
}

/// The longest `file: why` kept for one unreadable note (outside text).
const MAX_NOTE: usize = 200;

fn pr(
    gh: &dyn Read,
    repo: &str,
    p: &Value,
    files: &[FileLine],
    receipts: &Known<BTreeMap<Sha, KnifeScan>>,
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
    let listed = commit_list(gh, repo, number);
    let commits = match commits(&listed) {
        // the list must end at the head the pull list gave: otherwise the PR
        // moved between the two reads
        Known::Known(c) if c.last() != Some(&head_sha) => {
            Known::Unknown("commits do not end at the head (moved between reads)".into())
        }
        c => c,
    };
    let notes = match &commits {
        Known::Known(c) => lines_for(files, c),
        Known::Unknown(_) => PrNotes::default(),
    };
    let authors = match (&commits, &listed) {
        (Known::Known(_), Known::Known(l)) => {
            let lines: Vec<(Option<String>, Option<usize>)> = l
                .iter()
                .map(|c| {
                    let msg = c.get("commit").and_then(|m| st(m, "message"));
                    (
                        msg,
                        c.get("parents").and_then(Value::as_array).map(Vec::len),
                    )
                })
                .collect();
            authors_of(&head_ref, &lines)
        }
        (Known::Unknown(e), _) | (_, Known::Unknown(e)) => {
            Known::Unknown(format!("commit list unknown: {e}"))
        }
    };
    let knives = match receipts {
        Known::Known(m) => Known::Known(m.get(&head_sha).copied().unwrap_or(KnifeScan::LogMissing)),
        Known::Unknown(e) => Known::Unknown(e.clone()),
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
        authors,
        touches_guarded: guarded(gh, repo, number),
        cells: cells(gh, repo, number, &head_sha, p),
        stale_runs: Vec::new(),
        knives,
        reviews: notes.reviews,
        verifies: notes.verifies,
        unknown_notes: notes.unknown,
        full_ci: labels.iter().any(|l| l == "full-ci"),
        needs_verify: labels
            .iter()
            .filter_map(|l| l.strip_prefix("needs-verify:"))
            .map(str::to_owned)
            .collect(),
        verify_carry: None,
        // filled in by build once every PR is read (parts_of)
        integ_parts: is_integration(&head_ref)
            .then(|| Known::Unknown("integration parts not read yet".into())),
        trial: Known::Unknown("aib-release trial not wired".into()),
        reviewer: None,
        commits,
        head: head_sha,
    })
}

/// An integration branch collects other PRs: `int/…` or `integ/…`.
/// `integ-src/…` is not one: it is one reviewed slice pushed for its own PR
/// (Andy 11-13, wave 1), so it has no originals.
pub fn is_integration(head_ref: &str) -> bool {
    head_ref.starts_with("int/") || head_ref.starts_with("integ/")
}

/// The originals an integration PR holds (F9b): every other open PR with a
/// commit in the integration's `base..head`. Collected = the newest such
/// commit of that PR, now = its head; they differ when the original moved
/// after it was collected (D15). A commit list that cannot be read, the
/// integration's or any other PR's, makes the parts unknown: the unread one
/// could be an original.
pub fn parts_of(integ: &Pr, prs: &[Pr]) -> Known<Vec<IntegPart>> {
    let Known::Known(mine) = &integ.commits else {
        return Known::Unknown("the integration's commits are unknown".into());
    };
    let mut out = Vec::new();
    for other in prs.iter().filter(|p| p.number != integ.number) {
        let Known::Known(theirs) = &other.commits else {
            return Known::Unknown(format!("#{} commits unknown", other.number));
        };
        if let Some(c) = theirs.iter().rev().find(|c| mine.contains(c)) {
            out.push(IntegPart {
                pr: other.number,
                collected: c.clone(),
                now: other.head.clone(),
            });
        }
    }
    Known::Known(out)
}

/// The PR's commits as GitHub lists them (a full page is incomplete).
fn commit_list(gh: &dyn Read, repo: &str, n: u64) -> Known<Vec<Value>> {
    let path = format!("repos/{repo}/pulls/{n}/commits?per_page={PER_PAGE}");
    match gh.get(&path).and_then(|v| whole(&v, "commits").cloned()) {
        Ok(c) if !c.is_empty() => Known::Known(c),
        Ok(_) => Known::Unknown("no commits listed".into()),
        Err(e) => Known::Unknown(e),
    }
}

fn commits(listed: &Known<Vec<Value>>) -> Known<Vec<Sha>> {
    let l = match listed {
        Known::Known(l) => l,
        Known::Unknown(e) => return Known::Unknown(e.clone()),
    };
    match l
        .iter()
        .map(|c| st(c, "sha").ok_or_else(|| "commit without sha".to_owned()))
        .collect::<Result<Vec<Sha>, String>>()
    {
        Ok(c) => Known::Known(c),
        Err(e) => Known::Unknown(e),
    }
}

/// The seats a merge commit's subject names (F9): every branch-like word
/// (it has a `/` or `-`) that ends in a seat code, quotes and brackets
/// trimmed: `Merge branch 'feat/x-ebb36-1111'`, `Merge pull request #9 from
/// ez2app/feat/c4-ebb36-1115`, `integ S79: collect feat/drive-wake-7b81e-1115
/// (b7971805)`, and the seat right after `Merge` (`Merge a9136 9d1ad69d (…)`).
/// `main` / `origin/main` name main. `None` = it names neither a
/// seat nor main, so who wrote what it brought in cannot be told (`integ S79:
/// collect sig-b 1272869f (b433b; …)`: a seat in brackets is not a branch).
pub fn merge_seats(subject: &str) -> Option<BTreeSet<Seat>> {
    let line = subject.lines().next().unwrap_or("");
    let mut seats = BTreeSet::new();
    let mut main = false;
    // `Merge <seat> <sha> …` (the seats' own way, `Merge a9136 9d1ad69d (…)`):
    // the word right after Merge is the seat it brought in
    if let Some(seat) = line
        .strip_prefix("Merge ")
        .and_then(|r| r.split_whitespace().next())
        .filter(|w| is_seat(w))
    {
        seats.insert(seat.to_owned());
    }
    for w in line.split_whitespace() {
        let w = w.trim_matches(|c: char| "'\"`()[],;:".contains(c));
        if w == "main" || w.ends_with("/main") {
            main = true;
            continue;
        }
        if !(w.contains('/') || w.contains('-')) {
            continue;
        }
        let seat = owner_claim(w);
        if seat != "unknown" {
            seats.insert(seat);
        }
    }
    (main || !seats.is_empty()).then_some(seats)
}

/// The seats behind a PR's commits (F9): the seat the branch name claims,
/// plus the seats every merge commit's subject names ([`merge_seats`]).
/// Every commit pushes from one GitHub account, so these are the branch
/// names' claims. Only merge commits are read: a commit another seat wrote
/// that came in by fast-forward, cherry-pick or rebase has no merge to name
/// it, so that seat is not seen as an author (5d33a F9 review N1). `commits` = (message, parent count) per commit. A merge
/// that names no seat (`Merge #747 head …`, `Merge S3a … into S3b`) adds
/// nobody: the branch name's seat stands for it (519d0 11-15 09:03, F9c).
/// `Unknown` when the branch claims no seat, or a commit's message or
/// parents are missing.
pub fn authors_of(
    head_ref: &str,
    commits: &[(Option<String>, Option<usize>)],
) -> Known<BTreeSet<Seat>> {
    let owner = owner_claim(head_ref);
    if owner == "unknown" {
        return Known::Unknown(format!("branch {head_ref} claims no seat"));
    }
    let mut out = BTreeSet::from([owner]);
    for (subject, parents) in commits {
        let (Some(s), Some(p)) = (subject, parents) else {
            return Known::Unknown("a commit without a message or parents".into());
        };
        if *p < 2 {
            continue;
        }
        // a merge that names no seat adds nobody: the branch name's seat
        // stands for it (519d0 11-15 09:03, F9c)
        if let Some(seats) = merge_seats(s) {
            out.extend(seats);
        }
    }
    Known::Known(out)
}

/// The notes folder file whose presence freezes merges (the push gate reads
/// it too; its first line is the reason).
pub const FREEZE_FILE: &str = "PUSH-FREEZE";

/// `PUSH-FREEZE` in the notes folder: there ⇒ frozen with its first line as
/// the reason; absent ⇒ not frozen; unreadable ⇒ unknown.
pub fn freeze(pending: &Path) -> Known<Option<String>> {
    match std::fs::read(pending.join(FREEZE_FILE)) {
        Ok(b) => {
            let t = String::from_utf8_lossy(&b);
            let first = t.lines().next().unwrap_or("").trim();
            let why = if first.is_empty() {
                "no reason given"
            } else {
                first
            };
            Known::Known(Some(crate::observe::clean(why, MAX_NOTE)))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Known::Known(None),
        Err(e) => Known::Unknown(format!("{FREEZE_FILE}: {e}")),
    }
}

/// The largest receipt file read; a larger one makes the scan unknown.
const MAX_RECEIPT: u64 = 16 << 20;

/// What one receipt of the shared receipt-run.sh says about its knives, and
/// the head it ran on (the `HEAD-END` lines). `None` = it says nothing about
/// knives (no `KNIVES-RC`, or `KNIVES-NOT-RUN`: left to CI), or names no
/// full head. `KNIVES-INCOMPLETE` / `RECEIPT-RED` (receipt-run v2: the knife
/// section did not finish) is Aborted, with or without a knife summary.
pub fn scan_receipt(text: &str) -> Option<(Sha, KnifeScan)> {
    let has = |p: &str| text.lines().any(|l| l.starts_with(p));
    let mut ends = text.lines().filter_map(|l| l.strip_prefix("HEAD-END "));
    let head = ends.next()?.trim();
    if head.len() != 40 || !head.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    // receipt-run v2 (ez2-skills #23): a knife section that did not finish.
    // It counts even with no knife summary (the script ended before it), as
    // aib-slice reads it (5d33a F9 review N2)
    // a run that got no machine lease is an empty shell, not a receipt
    // (043dd's `LEASE=4` files, 519d0 11-15 09:12)
    if text
        .lines()
        .filter_map(|l| l.strip_prefix("LEASE="))
        .any(|n| n.trim() != "0")
    {
        return None;
    }
    let moved = ends.any(|h| h.trim() != head);
    let unfinished = has("KNIVES-INCOMPLETE") || has("RECEIPT-RED");
    if !unfinished && !(has("KNIVES-RC ") || has("KNIVES-SKIPPED")) {
        // a self-written receipt from before receipt-run (043dd): its knife
        // summary is `RC knives=<rc>`, 0 when every knife bit
        let old = text
            .lines()
            .filter_map(|l| l.strip_prefix("RC knives="))
            .next_back();
        return match old {
            Some(rc) if !has("KNIVES-NOT-RUN") => {
                let scan = if rc.trim() == "0" && !moved {
                    KnifeScan::Ok
                } else {
                    KnifeScan::Aborted
                };
                Some((head.to_ascii_lowercase(), scan))
            }
            _ => None,
        };
    }
    let knife = |w: &str| {
        text.lines()
            .filter(|l| l.starts_with("RC knife_"))
            .any(|l| l.contains(w))
    };
    let misses = text
        .lines()
        .filter_map(|l| l.strip_prefix("MISSES-COUNT "))
        .next_back()
        .and_then(|n| n.trim().parse::<u64>().ok());
    let scan = if has("KNIVES-SKIPPED") || unfinished || moved {
        KnifeScan::Aborted
    } else if knife("anchor-missing") {
        KnifeScan::Misses
    } else if knife("INVALID") || has("INVALID") {
        KnifeScan::Aborted
    } else if misses != Some(0) {
        KnifeScan::Survives
    } else if has("KNIVES-OK") && has("KNIVES-RC 0") {
        KnifeScan::Ok
    } else {
        // every knife bit but the tree did not come back clean
        KnifeScan::Aborted
    };
    Some((head.to_ascii_lowercase(), scan))
}

/// The newest knife receipt about each head in the notes folder
/// (`RECEIPT-*` and `TEST-*`, 519d0 11-15; by modification time, then name;
/// a self-written `RC knives=` receipt counts, a `LEASE=` shell does not). A newer receipt replaces
/// an older one about the same head; one that says nothing about knives
/// replaces nothing. A folder or file that cannot be read makes all of it
/// unknown: the unread one could be the newest.
pub fn knife_receipts(pending: &Path) -> Known<BTreeMap<Sha, KnifeScan>> {
    let read = || -> Result<BTreeMap<Sha, KnifeScan>, String> {
        let mut found: Vec<(std::time::SystemTime, String, Sha, KnifeScan)> = Vec::new();
        for e in std::fs::read_dir(pending).map_err(|e| format!("receipts: {e}"))? {
            let e = e.map_err(|e| format!("receipts: {e}"))?;
            let name = e.file_name().to_string_lossy().into_owned();
            if !(name.starts_with("RECEIPT-") || name.starts_with("TEST-")) {
                continue;
            }
            let meta = e.metadata().map_err(|e| format!("{name}: {e}"))?;
            if !meta.is_file() {
                continue;
            }
            if meta.len() > MAX_RECEIPT {
                return Err(format!("{name}: larger than {MAX_RECEIPT} bytes"));
            }
            let at = meta.modified().map_err(|e| format!("{name}: {e}"))?;
            let b = std::fs::read(e.path()).map_err(|e| format!("{name}: {e}"))?;
            if let Some((head, scan)) = scan_receipt(&String::from_utf8_lossy(&b)) {
                found.push((at, name, head, scan));
            }
        }
        found.sort_by(|x, y| x.0.cmp(&y.0).then_with(|| x.1.cmp(&y.1)));
        Ok(found.into_iter().map(|(_, _, h, s)| (h, s)).collect())
    };
    match read() {
        Ok(m) => Known::Known(m),
        Err(e) => Known::Unknown(crate::observe::clean(&e, MAX_NOTE)),
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

/// One run per check name, the newest started (then the larger id): a
/// re-run of a cell replaces the round it repeats (F9: 同格取最新一輪; the
/// same workflow run twice on one head gives two `docs`, two `smoke (…)`).
/// A run with no name or start time is kept as it is, so it makes the cells
/// unknown in [`cell_of`].
pub fn latest_runs(runs: &[Value]) -> Vec<&Value> {
    let key = |v: &Value| (st(v, "started_at"), v.get("id").and_then(Value::as_u64));
    let mut newest: BTreeMap<String, &Value> = BTreeMap::new();
    let mut out = Vec::new();
    for r in runs {
        let (Some(name), Some(_)) = (st(r, "name"), st(r, "started_at")) else {
            out.push(r);
            continue;
        };
        match newest.get(&name) {
            Some(old) if key(old) >= key(r) => {}
            _ => {
                newest.insert(name, r);
            }
        }
    }
    out.extend(newest.into_values());
    out
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
    for c in latest_runs(runs) {
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
