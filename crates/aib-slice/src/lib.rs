// SPDX-License-Identifier: Apache-2.0
//! S73a `aib-slice sweep` (519d0 DESIGN-S73-merge-sweep, Andy 11-14): before any merge, list every
//! branch that may still need merging and put it in one of six classes. Read-only: it fetches,
//! reads git, reads open PRs through `gh api` (GET only) and reads the review / receipt files in
//! the pending folder. It never pushes, opens a PR or deletes a tree.
//!
//! The output is one JSON object, `aib-slice-sweep.v1`, fixed by `schema/sweep.v1.json`
//! ([`SCHEMA`]). Readers (f1fae S67b / S73b / S73e) code against that file; [`validate`] checks
//! an output against it, and the tests make a missing or an extra field red.
//!
//! Nothing here is a gate by itself: reviews and receipts are same-user files (GUIDE same-user
//! threat model). `--gate` only stops a merge script that chose to ask.

use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

pub mod due;
pub mod gatecontent;
pub mod gc;
pub mod scan;

/// A build claim older than this is stale (Andy 11-14: six hours without a touch).
pub const CLAIM_TTL_S: i64 = 6 * 3600;

/// `2026-11-14T10:30:00+08:00`, `2026-11-14 10:30`, `2026-11-14T02:30:00Z` or unix seconds ⇒
/// unix seconds. No offset = Taipei (+08:00), the time every seat writes.
pub fn parse_time(t: &str) -> Option<i64> {
    let t = t.trim();
    if !t.is_empty() && t.bytes().all(|c| c.is_ascii_digit()) {
        return t.parse().ok();
    }
    let num = |s: &str| -> Option<i64> {
        (!s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
            .then(|| s.parse().ok())
            .flatten()
    };
    let (date, rest) = t.split_at_checked(10)?;
    let mut d = date.split('-');
    let (y, mo, da) = (num(d.next()?)?, num(d.next()?)?, num(d.next()?)?);
    if d.next().is_some() || !(1..=12).contains(&mo) || !(1..=31).contains(&da) {
        return None;
    }
    let rest = rest.strip_prefix(['T', ' '])?;
    let (clock, off) = if let Some(c) = rest.strip_suffix('Z') {
        (c, 0)
    } else if let Some(i) = rest.rfind(['+', '-']) {
        let (c, o) = rest.split_at(i);
        let sign = if o.starts_with('-') { -1 } else { 1 };
        let mut hm = o[1..].split(':');
        let oh = num(hm.next()?)?;
        let om = hm.next().map_or(Some(0), num)?;
        (c, sign * (oh * 3600 + om * 60))
    } else {
        (rest, 8 * 3600)
    };
    let mut c = clock.split(':');
    let (h, mi) = (num(c.next()?)?, num(c.next()?)?);
    let sec = c.next().map_or(Some(0), num)?;
    if c.next().is_some() || h > 23 || mi > 59 || sec > 60 {
        return None;
    }
    // days from civil (Howard Hinnant)
    let (yy, mm) = if mo <= 2 {
        (y - 1, mo + 9)
    } else {
        (y, mo - 3)
    };
    let era = yy.div_euclid(400);
    let yoe = yy - era * 400;
    let doy = (153 * mm + 2) / 5 + da - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + h * 3600 + mi * 60 + sec - off)
}

/// The output contract (JSON Schema, the subset [`validate`] reads).
pub const SCHEMA: &str = include_str!("../schema/sweep.v1.json");
pub const SCHEMA_ID: &str = "aib-slice-sweep.v1";

#[derive(Debug, Clone, PartialEq)]
pub struct OpenPr {
    pub number: u64,
    pub head_ref: String,
    pub head: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct PrInfo {
    pub number: u64,
    pub open: bool,
    pub merged: bool,
    pub base: Option<String>,
    /// gh login of the PR author
    pub author: Option<String>,
    /// MERGEABLE | CONFLICTING | UNKNOWN; None = not read (closed PRs are not read)
    pub mergeable: Option<String>,
    /// GitHub `mergeable_state` as given; None = not read
    pub merge_state: Option<String>,
    /// SUCCESS | FAILURE | PENDING | INFRA (every red one is an infra failure) rolled up from the head's check runs; None = not read / none
    pub checks: Option<String>,
    /// failed check run names; None = not read (never "none red")
    pub red_checks: Option<Vec<String>>,
    /// the red ones whose job died while its runner was set up (IFACE-s79 r11 4): the
    /// merge queue's to run again, not the author's to fix; None = not read
    pub infra_checks: Option<Vec<String>>,
    /// label names on the PR (`not-gate`, …)
    pub labels: Vec<String>,
    /// check cells of the head not started yet: (how many, earliest `started_at`); None = not
    /// read (S73l: 「排隊中」 and 「在跑」 are told apart, with how long)
    pub queued: Option<(u32, Option<i64>)>,
    /// check cells of the head running now: (how many, earliest `started_at`); None = not read
    pub running: Option<(u32, Option<i64>)>,
    /// check cells of the head whose newest run was cancelled (they have to run again; not
    /// red); None = not read
    pub cancelled: Option<u32>,
    /// S79-c1fb: the names of those cancelled cells; None = not read
    pub cancelled_cells: Option<Vec<String>>,
    /// head commit of a closed PR as GitHub gives it; None = not read (open PRs: see OpenPr)
    pub head: Option<String>,
    /// names of the head's check runs (the newest of each name); None = not read
    pub check_names: Option<Vec<String>>,
    /// required cells ([`required_cells`], the merge queue's list) the head has no run of;
    /// `checks` is MISSING when everything else is green. None = not judged
    pub missing_checks: Option<Vec<String>>,
}

/// A workflow file larger than this is not merged (#755, 1ced7 11-15: aibd.yml at 508,091
/// bytes, GitHub never ran it, aibd built no cell, the PR still looked green). The merge queue
/// refuses at this size after merge-tree (f1fae mergewatch 11-15), a margin under where GitHub
/// stops running a file: the same line here, so a branch the queue refuses is never mergeable.
pub const WORKFLOW_MAX_BYTES: u64 = 495_000;
/// The cells the merge queue requires of a PR that touches Rust or aibd.yml (f1fae
/// mergewatch.sh, the same list: not a second copy of the rule).
pub const RUST_CELLS: [&str; 2] = ["rust (windows-latest)", "rust (macos-latest)"];
/// Required as well when the head's aibd.yml has a job of that name (mergewatch.sh 11-13).
pub const CLOUD_CELLS: [&str; 2] = [
    "cloud decision knives (macOS)",
    "cloud-host command knives (macOS)",
];

/// The cells a PR must have green before it merges (mergewatch.sh): none unless it changes
/// `crates/`, `Cargo.toml`, `Cargo.lock` or `.github/workflows/aibd.yml`; then [`RUST_CELLS`],
/// plus each of [`CLOUD_CELLS`] that the head's aibd.yml names as a job (`name: <cell>`).
pub fn required_cells(files: &[String], aibd_at_head: Option<&str>) -> Vec<String> {
    if !touches_rust(files) {
        return Vec::new();
    }
    let mut out: Vec<String> = RUST_CELLS.iter().map(|s| (*s).to_owned()).collect();
    let names: BTreeSet<&str> = aibd_at_head
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.trim().strip_prefix("name: "))
        .map(str::trim)
        .collect();
    out.extend(
        CLOUD_CELLS
            .iter()
            .filter(|c| names.contains(*c))
            .map(|c| (*c).to_owned()),
    );
    out
}

/// The changes that make [`RUST_CELLS`] required (mergewatch.sh).
pub fn touches_rust(files: &[String]) -> bool {
    files.iter().any(|f| {
        let f = f.replace('\\', "/");
        f.starts_with("crates/")
            || f == "Cargo.toml"
            || f == "Cargo.lock"
            || f == ".github/workflows/aibd.yml"
    })
}

/// Workflow files over [`WORKFLOW_MAX_BYTES`] in `git ls-tree -l -r` output, as
/// (file, bytes, where).
pub fn too_big(ls_tree_l: &str, at: &str) -> Vec<(String, u64, String)> {
    ls_tree_l
        .lines()
        .filter_map(|l| {
            let (meta, path) = l.split_once('\t')?;
            let bytes: u64 = meta.split_whitespace().nth(3)?.parse().ok()?;
            (bytes > WORKFLOW_MAX_BYTES).then(|| (path.to_owned(), bytes, at.to_owned()))
        })
        .collect()
}

/// First line of a REVIEW-*.md: `結論: X 頭: <40> H/M/L/N: a/b/c/d …`
#[derive(Debug, Clone, PartialEq)]
pub struct Review {
    pub head: String,
    pub verdict: String,
    pub h: u32,
    pub m: u32,
    pub l: u32,
    pub n: u32,
    pub file: String,
    pub at_head: bool,
    /// non-merge commits between the reviewed head and the branch head that are not in
    /// origin/main (what a merge of main brings in is not this branch's to review)
    pub unreviewed_after: Vec<String>,
    /// S78 r-2a: the hand-resolved merges ([`Resolved`]) made after the reviewed head; empty =
    /// every merge after the review is a plain one (carried, RULES §20), also when the review
    /// is at the head; None = not read
    pub resolved_after_review: Option<Vec<Resolved>>,
    /// the reviewing seat, from the file name (`REVIEW-<topic>-<seat>-<sha8>.md`)
    pub reviewer: Option<String>,
    /// a `非閘理由: …` line in the review (S73j: why a new gate may merge as a non-gate)
    pub not_gate_reason: Option<String>,
    /// what the review covers, from its `範圍:` text ([`review_scope`])
    pub scope: Scope,
}

/// What a passed review covers (IFACE-s79 r10 3), read from its first line.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum Scope {
    /// the branch up to the reviewed head (`全文`, or nothing that says otherwise)
    #[default]
    Whole,
    /// `<a>..<head>`: the commits after `a`
    From(String),
    /// an increment that does not say where it starts: what came after the reviewed heads
    /// under it
    Increment,
}

impl Scope {
    pub fn text(&self) -> String {
        match self {
            Scope::Whole => "whole".to_owned(),
            Scope::From(a) => format!("from:{a}"),
            Scope::Increment => "increment".to_owned(),
        }
    }
}

/// The scope of a review from its first line and file name: a `<a>..<b>` in the `範圍:` text
/// with `b` the reviewed head (7 to 40 hex each) ⇒ From(a); the word `增量` there, or `-inc-`
/// in the file name ⇒ Increment; anything else ⇒ Whole.
pub fn review_scope(first_line: &str, head: &str, file: &str) -> Scope {
    let range = first_line.split_once("範圍:").map_or("", |x| x.1);
    let hex = |s: &str| {
        (7..=40).contains(&s.len()) && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
    };
    for tok in range.split(|c: char| !(c.is_ascii_hexdigit() || c == '.')) {
        if let Some((a, b)) = tok.split_once("..") {
            if hex(a) && hex(b) && head.starts_with(b) {
                return Scope::From(a.to_owned());
            }
        }
    }
    if range.contains("增量") || file.contains("-inc-") {
        return Scope::Increment;
    }
    Scope::Whole
}

/// One passed review of a commit under a branch head, for [`review_parts`].
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Part {
    pub head: String,
    pub file: String,
    pub scope: Scope,
    /// the reviewed commit is a merge
    pub merge: bool,
    /// every commit of main..reviewed head, the reviewed head included
    pub reach: BTreeSet<String>,
    /// Scope::From(a): the commits of a..reviewed head; None = `a` could not be read
    pub from: Option<BTreeSet<String>>,
}

/// IFACE-s79 r10 3: a head nobody reviewed as a whole (slices stacked into one head) counts
/// as reviewed when every commit of its own that is not a merge is covered by a passed
/// review, and every merge is carried or was reviewed itself.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ReviewParts {
    /// the passed reviews that cover something
    pub files: Vec<String>,
    /// commits (not merges) no passed review covers
    pub uncovered: Vec<String>,
    /// merges resolved by hand with no passed review at them
    pub unreviewed_merges: Vec<String>,
}

impl ReviewParts {
    pub fn ok(&self) -> bool {
        !self.files.is_empty() && self.uncovered.is_empty() && self.unreviewed_merges.is_empty()
    }
}

/// What each passed review covers, added up. A review at a merge covers that merge only
/// (the resolution is what was read), unless it names `<a>..<head>`; an increment without a
/// start covers what the reviewed heads under it do not reach, and with none under it only
/// its own commit: when it cannot be told, less is covered, not more.
pub fn review_parts(non_merge: &[String], parts: &[Part], resolved: &[Resolved]) -> ReviewParts {
    let mut covered: BTreeSet<&str> = BTreeSet::new();
    let mut files = Vec::new();
    for p in parts {
        let own: BTreeSet<&str> = std::iter::once(p.head.as_str()).collect();
        let all = || {
            p.reach
                .iter()
                .map(String::as_str)
                .collect::<BTreeSet<&str>>()
        };
        let got = match (&p.scope, &p.from) {
            (Scope::From(_), Some(f)) => f.iter().map(String::as_str).collect(),
            (Scope::From(_), None) => own,
            _ if p.merge => BTreeSet::new(),
            (Scope::Whole, _) => all(),
            (Scope::Increment, _) => {
                let under: Vec<&Part> = parts
                    .iter()
                    .filter(|q| q.head != p.head && p.reach.contains(&q.head))
                    .collect();
                if under.is_empty() {
                    own
                } else {
                    all()
                        .into_iter()
                        .filter(|c| !under.iter().any(|q| q.reach.contains(*c)))
                        .collect()
                }
            }
        };
        let adds = non_merge.iter().any(|c| got.contains(c.as_str()));
        if adds || resolved.iter().any(|r| r.commit == p.head) {
            files.push(p.file.clone());
        }
        covered.extend(got);
    }
    ReviewParts {
        files,
        uncovered: non_merge
            .iter()
            .filter(|c| !covered.contains(c.as_str()))
            .cloned()
            .collect(),
        unreviewed_merges: resolved
            .iter()
            .filter(|r| !parts.iter().any(|p| p.head == r.commit))
            .map(|r| r.commit.clone())
            .collect(),
    }
}

impl Review {
    pub fn passed(&self) -> bool {
        self.verdict == "通過" && self.h == 0 && self.m == 0
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Receipt {
    pub head: String,
    pub file: String,
    /// `xxx_rc=N` / `STEP xxx rc=N` keys that are not 0
    pub nonzero: Vec<String>,
    pub at_head: bool,
    /// every rc key the receipt names (what ran), KNIFE lines not counted
    pub steps: Vec<String>,
    /// last line `RECEIPT-SHA256 <64>` matches the text before it; None = no such line
    pub sealed: Option<bool>,
    /// a `HEAD-END <head>` line: the run reached its end on this head (043dd M1)
    pub complete: bool,
    /// f1fae COMPARE r2 2: the receipt did not run on this head but on this commit, a stack
    /// of plain merges that holds this head (the merge queue's stacked receipt); it then
    /// counts as one at the head. None = an ordinary receipt
    pub stacked_at: Option<String>,
}

impl Receipt {
    /// It ran (rc lines), it finished (`HEAD-END`), nothing red, and the seal is not broken (a
    /// seal that does not match = half written or damaged). A receipt cut off before any rc
    /// line or before its end is not green (043dd M1).
    pub fn green(&self) -> bool {
        self.nonzero.is_empty() && self.sealed != Some(false) && self.finished()
    }

    /// Has rc lines and a `HEAD-END` for its head.
    pub fn finished(&self) -> bool {
        self.complete && !self.steps.is_empty()
    }

    /// A whole run, green by itself: it ran `test` and reached receipt-run.sh's knife summary
    /// (`KNIVES-RC`, a step even when 0), so it is not one part of a split run (`-ws` /
    /// `-knives` / `-docsonly`).
    pub fn whole_green(&self) -> bool {
        self.green()
            && self.steps.iter().any(|s| s == "test")
            && self.steps.iter().any(|s| s == "knives_rc")
    }
}

/// A merge-tree result: did it conflict, and which files.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Clash {
    pub conflict: bool,
    pub files: Vec<String>,
}

/// S78 r-2a: a merge commit of this branch whose tree is not what `git merge-tree` makes of its
/// two parents: somebody resolved a conflict by hand, or changed something inside the merge.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Resolved {
    pub commit: String,
    /// the second parent (what was merged in)
    pub parent: String,
    /// the second parent is in origin/main now. Not proof that main was what got merged: a
    /// branch that was merged in and reached main later reads true too (043dd L1)
    pub from_main: bool,
    /// the files that differ from the automatic merge: what a reviewer has to read
    pub files: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Branch {
    pub branch: String,
    pub seat: Option<String>,
    pub head: String,
    /// 5d33a M1: the local branch of this name is on this other commit; the row is judged at
    /// the head of its open PR. None = the local branch (or none) is at `head`
    pub local_head: Option<String>,
    pub local: bool,
    pub remote: Option<String>,
    pub worktree: Option<String>,
    pub dirty: Option<bool>,
    pub last_commit_at: i64,
    pub age_h: i64,
    pub unique: usize,
    pub unique_not_in_prs: usize,
    pub in_prs: Vec<u64>,
    pub pushed_to: Vec<String>,
    pub pr: Option<PrInfo>,
    pub review: Option<Review>,
    pub receipt: Option<Receipt>,
    /// S79-c1fb (5d33a r6 C1): green FALLBACK receipts of one cell at this head, run on our
    /// own Mac ([`fallback_cell`]): (cell name, file)
    pub fallback_cells: Vec<(String, String)>,
    pub stacked_on: Vec<String>,
    /// no other listed branch is stacked on this one
    pub stack_top: bool,
    /// open PRs this head conflicts with, and the files
    pub conflicts_with: Vec<(u64, Vec<String>)>,
    /// other class 1 / 2 branches this head conflicts with, and the files
    pub conflicts_with_branches: Vec<(String, Vec<String>)>,
    /// against origin/main; None = not tried
    pub conflicts_with_main: Option<Clash>,
    /// `<pending>/build-claims.tsv`: someone is building on this branch (seat, since); a claim
    /// older than [`CLAIM_TTL_S`] is not counted
    pub claimed_by: Option<(String, i64)>,
    /// files of this branch (merge base..head) that change a check; see [`gate_files`]
    pub gate_files: Vec<String>,
    /// reason from `<pending>/sweep-ack.txt` (the author gave it up / it went elsewhere)
    pub acked: Option<String>,
    /// `.py` files of this branch (merge base..head): Andy 11-13 「程式只能 Rust」 (s35)
    pub py_files: Vec<String>,
    /// S73k: the gate files judged by content ([`gatecontent::judge`]); `Ok` = one step added for
    /// the branch's own crate, sorted as a non-gate; `Err` = the rule that does not hold; None =
    /// not judged (no gate files, or a file could not be read: see errors)
    pub gate_content: Option<Result<String, String>>,
    /// S78 r-2a: the merge commits of main..head that were not a plain automatic merge; empty =
    /// every merge is carried (RULES §20); None = not read (see errors)
    pub resolved_merges: Option<Vec<Resolved>>,
    /// RULES §25 (S79 C1a): workflow steps main has that the merge of this branch into main
    /// no longer has, as `file: step`; empty = none lost; None = not checked (no workflow
    /// file changed, a conflict with main, or not read)
    pub steps_lost: Option<Vec<String>>,
    /// IFACE-s79 r10 3: the newest review is passed but not at the head ⇒ what the passed
    /// reviews under the head cover ([`review_parts`]); None = not judged (a review at the
    /// head, no review, the newest one not passed, or not read)
    pub review_parts: Option<ReviewParts>,
    /// S79 (519d0 11-15): another seat's `integ-src/<key>-<seat>` newer than this branch, whose
    /// key is this branch's name core ([`takes_over`]): (branch, head). The work went there.
    pub taken_over_by: Option<(String, String)>,
    /// S79 (519d0 11-15): the receipt at the head is not green, but a green finished receipt
    /// ran on a commit above this head (an integration head carrying it): (head, file).
    pub absorbed_into: Option<(String, String)>,
    /// workflow files over GitHub's size limit, at the head or in the automatic merge into
    /// main: (file, bytes, `head` | `merged`). Checked when the branch changes a workflow.
    pub workflow_too_big: Vec<(String, u64, String)>,
}

/// The name core of a branch (S79): the part after the last `/`, without the trailing seat
/// code and date (`feat/aib-consent-t136-3a-c477a-1113` ⇒ `aib-consent-t136-3a`).
pub fn name_core(branch: &str) -> String {
    let tail = branch.rsplit('/').next().unwrap_or(branch);
    let mut parts: Vec<&str> = tail.split('-').collect();
    if parts.len() > 1
        && parts
            .last()
            .is_some_and(|p| p.len() == 4 && p.bytes().all(|c| c.is_ascii_digit()))
    {
        parts.pop();
    }
    if parts.len() > 1
        && parts.last().is_some_and(|p| {
            p.len() == 5 && p.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
        })
    {
        parts.pop();
    }
    parts.join("-")
}

/// `integ-src/<key>-<seat>` takes over a branch whose name core is `core` (S79, 519d0 11-15:
/// a seat that went quiet, another seat carries its work on a branch of its own): the key is
/// the whole core, or its last two or more `-` parts (`t136-3a` for `aib-consent-t136-3a`).
pub fn takes_over(core: &str, key: &str) -> bool {
    !key.is_empty() && (key == core || (key.contains('-') && core.ends_with(&format!("-{key}"))))
}

impl Branch {
    /// A passed review at the head, or every commit covered by passed reviews
    /// ([`ReviewParts::ok`]).
    pub fn reviewed(&self) -> bool {
        self.review
            .as_ref()
            .is_some_and(|r| r.at_head && r.passed())
            || self.review_parts.as_ref().is_some_and(ReviewParts::ok)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub generated_at: i64,
    pub main: String,
    /// `--fast`: only branches with an open PR or a commit inside the window were read, and
    /// closed PRs only from the ones GitHub changed inside the window; not the whole picture
    pub fast: bool,
    pub errors: Vec<String>,
    pub open_prs: Vec<OpenPr>,
    pub branches: Vec<Branch>,
}

/// What a FALLBACK receipt says it ran ([`fallback_cell`]): the GitHub cell, the head, the
/// workflow job and the sha256 of that job's lines as the run cut them (S79-c1fb2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Fallback {
    pub cell: String,
    pub head: String,
    pub job: String,
    pub slice_sha: String,
}

/// S79-c1fb2 (5d33a 82336470 L1): a step cell-steps did not run on macOS (`NOT-MACOS <step>
/// (<if>)`) is fine only when GitHub would not run it in that cell either: a Windows-only or
/// non-macOS step, or one of the two guards that run only when the cell is not wanted. Any
/// other condition (one it does not know, or none) is a step left out.
pub fn skipped_on_macos_ok(line: &str) -> bool {
    line.contains("runner.os == 'Windows'")
        || line.contains("runner.os != 'macOS'")
        || line.ends_with("(env.RUN != 'true')")
        || line.ends_with("(needs.changes.result != 'success')")
}

/// S79-c1fb (5d33a r6 C1, 519d0 11-16): a FALLBACK receipt that ran every step of one
/// macOS cell on our own Mac (cell-steps: `CELL <name>`, `HEAD <40> …`, `JOB <id>
/// SLICE-SHA256 <64> …`, `STEP rc=N …`, `CELL-RED 0`, `END rc=0`), green and whole: at least
/// one step and every one rc 0, the tree put back (`RESTORED-OK`), no step left out
/// (`SKIP-REUSED` = a step not run here, #614; a `NOT-MACOS` step GitHub would run,
/// [`skipped_on_macos_ok`]), no refusal before the run. None = not one, or not green. Which
/// job it ran is checked against the head's workflow by [`fallback_at_head`].
pub fn fallback_cell(text: &str) -> Option<Fallback> {
    let lines: Vec<&str> = text.lines().map(str::trim_end).collect();
    let one = |p: &str| lines.iter().find_map(|l| l.strip_prefix(p));
    let cell = one("CELL ")?.trim();
    let head = one("HEAD ")?
        .split_whitespace()
        .next()
        .filter(|h| is_sha40(h))?;
    let job_line: Vec<&str> = one("JOB ")?.split_whitespace().collect();
    let (job, slice_sha) = match job_line.as_slice() {
        [job, "SLICE-SHA256", sha, ..] if job_id_ok(job) && is_hex64(sha) => (*job, *sha),
        _ => return None,
    };
    let steps: Vec<&&str> = lines.iter().filter(|l| l.starts_with("STEP ")).collect();
    let bad = [
        "SKIP-REUSED",
        "RESTORE-FAIL",
        "HEAD-MISMATCH",
        "TREE-DIRTY",
        "NO-TREE",
        "NO-CARGO",
    ];
    let ok = !cell.is_empty()
        && lines.iter().filter(|l| l.starts_with("CELL ")).count() == 1
        && !steps.is_empty()
        && steps.iter().all(|l| l.starts_with("STEP rc=0 "))
        && lines.contains(&"CELL-RED 0")
        && !lines
            .iter()
            .any(|l| l.starts_with("CELL-RED ") && *l != "CELL-RED 0")
        && lines.contains(&"RESTORED-OK")
        && lines.iter().any(|l| l.starts_with("END rc=0"))
        && !lines
            .iter()
            .any(|l| l.starts_with("END rc=") && !l.starts_with("END rc=0"))
        && !lines.iter().any(|l| bad.iter().any(|b| l.starts_with(b)))
        && lines.iter().filter(|l| l.starts_with("JOB ")).count() == 1
        && lines
            .iter()
            .filter(|l| l.starts_with("NOT-MACOS "))
            .all(|l| skipped_on_macos_ok(l));
    ok.then(|| Fallback {
        cell: cell.to_owned(),
        head: head.to_owned(),
        job: job.to_owned(),
        slice_sha: slice_sha.to_owned(),
    })
}

fn job_id_ok(s: &str) -> bool {
    !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// S79-c1fb2: the lines of one job of a workflow, as cell-steps cuts them: from `  <job>:`
/// under `jobs:` up to the next job (or the end), every line with its newline. None = no
/// such job.
pub fn job_slice(yml: &str, job: &str) -> Option<String> {
    let lines: Vec<&str> = yml.split_inclusive('\n').collect();
    let is_job = |l: &str| {
        let t = l.trim_end();
        t.len() > 3
            && t.starts_with("  ")
            && !t.starts_with("   ")
            && t.ends_with(':')
            && job_id_ok(&t[2..t.len() - 1])
    };
    let jobs = lines.iter().position(|l| l.trim_end() == "jobs:")?;
    let want = format!("  {job}:");
    let start = (jobs + 1..lines.len()).find(|&i| lines[i].trim_end() == want)?;
    let end = (start + 1..lines.len())
        .find(|&i| is_job(lines[i]))
        .unwrap_or(lines.len());
    Some(lines[start..end].concat())
}

/// S79-c1fb2: the name GitHub gives the cells of a job: its `name:` (a matrix adds
/// ` (<values>)`), or the job id when it has none. None = a name made of an expression,
/// which cannot be checked here.
pub fn job_label(slice: &str, job: &str) -> Option<String> {
    let name = slice
        .lines()
        .take_while(|l| !l.starts_with("    steps:"))
        .find_map(|l| l.strip_prefix("    name: "));
    match name {
        None => Some(job.to_owned()),
        Some(n) if n.contains("${{") => None,
        Some(n) => Some(n.trim().trim_matches(|c| c == '"' || c == '\'').to_owned()),
    }
}

/// S79-c1fb2 (5d33a 82336470 L1): a FALLBACK receipt ran this head's job: the job is in the
/// head's workflow, its lines hash to what the run cut, and the receipt's cell is a cell of
/// that job. Anything else ⇒ not counted.
pub fn fallback_at_head(f: &Fallback, yml: &str) -> bool {
    let Some(slice) = job_slice(yml, &f.job) else {
        return false;
    };
    let sha = sha256_hex(slice.as_bytes());
    let named = job_label(&slice, &f.job)
        .is_some_and(|l| f.cell == l || f.cell.starts_with(&format!("{l} (")));
    sha == f.slice_sha && named
}

/// S79-c1fb2: the sha256 cell-steps prints for a job (`SLICE-SHA256`); None = no such job.
pub fn job_slice_sha(yml: &str, job: &str) -> Option<String> {
    job_slice(yml, job).map(|s| sha256_hex(s.as_bytes()))
}

fn sha256_hex(b: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

/// S79-c1fb: a cell a FALLBACK receipt may stand in for. The smoke matrix is required by the
/// ruleset: cancelling it leaves the PR BLOCKED whatever ran elsewhere, so it is never one.
pub fn fallback_may_cover(cell: &str) -> bool {
    !cell.starts_with("smoke")
}

/// A test receipt by its file name (IFACE-s79 r11 5): `TEST-…` / `RECEIPT-…` ending `.txt`,
/// `.out` or `.log` (receipt-run.sh writes `.out`), or any name of those endings that says
/// `receipt` and carries eight or more hex digits (`666-receipt-738745fdb-full.out`). What
/// the file is a receipt of is still read from its `HEAD` lines, never from the name.
pub fn is_receipt_name(name: &str) -> bool {
    if ![".txt", ".out", ".log"].iter().any(|e| name.ends_with(e)) {
        return false;
    }
    if name.starts_with("TEST-") || name.starts_with("RECEIPT-") {
        return true;
    }
    name.to_ascii_lowercase().contains("receipt")
        && name
            .split(|c: char| !c.is_ascii_alphanumeric())
            .any(|t| t.len() >= 8 && t.bytes().all(|b| b.is_ascii_hexdigit()))
}

/// The receipts of one head read together, newest first (IFACE-s79 r11 5: a run split into
/// `-ws` / `-knives` / `-docsonly` files is one receipt). A step keeps the result of the
/// newest file that names it, so a rerun that is green clears an older red; an older file
/// adds the steps only it ran and its `HEAD-END`. A broken seal counts only on a file that
/// was used.
///
/// S79-anc 5 (519d0 11-16, #809): a whole run green on its own ([`Receipt::whole_green`])
/// closes the files older than it. #809: lin's whole run at 12:55 was green, but andymini's
/// red one at 12:30 had a `knives` step (skipped after its red test) that lin's run does not
/// name, so it was added as an older file's own step and the head read red. Files newer than
/// that green run still count: a later red on the same head is a finding.
pub fn receipts_together(newest_first: &[Receipt]) -> Option<Receipt> {
    let upto = newest_first
        .iter()
        .position(Receipt::whole_green)
        .map_or(newest_first.len(), |g| g + 1);
    let (first, older) = newest_first[..upto].split_first()?;
    let mut out = first.clone();
    let mut used = 0;
    for r in older {
        let mut adds = false;
        for k in &r.steps {
            if !out.steps.contains(k) {
                out.steps.push(k.clone());
                if r.nonzero.contains(k) {
                    out.nonzero.push(k.clone());
                }
                adds = true;
            }
        }
        if r.complete && !out.complete {
            out.complete = true;
            adds = true;
        }
        if adds {
            used += 1;
            out.sealed = match (out.sealed, r.sealed) {
                (Some(false), _) | (_, Some(false)) => Some(false),
                (Some(true), _) | (_, Some(true)) => Some(true),
                (None, None) => None,
            };
        }
    }
    if used > 0 {
        out.file = format!("{} (+{used})", first.file);
    }
    Some(out)
}

/// S79-anc 4 (519d0 11-16): of the files two heads clash on, the ones the other open PR
/// changes itself (`theirs` = main...its head). A file it did not change clashes only because
/// this head and main differ there: that is the clash with main, said once as that. #801 on
/// an old base was listed against #808, which changes one test file and not aibd.yml.
pub fn own_clash(files: &[String], theirs: &BTreeSet<String>) -> Vec<String> {
    files
        .iter()
        .filter(|f| theirs.contains(*f))
        .cloned()
        .collect()
}

/// The step names of a workflow text: every `- name: X` line, in order (RULES §25).
pub fn step_names(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|l| l.trim_start().strip_prefix("- name:"))
        .map(|n| n.trim().to_owned())
        .filter(|n| !n.is_empty())
        .collect()
}

/// RULES §25: the steps main has and the merged file no longer has (a name used twice on
/// main must be there twice). `merged` None = the file is gone: every step is lost.
pub fn steps_lost(main: &str, merged: Option<&str>) -> Vec<String> {
    let mut have = merged.map(step_names).unwrap_or_default();
    let mut lost = Vec::new();
    for n in step_names(main) {
        match have.iter().position(|h| *h == n) {
            Some(i) => {
                have.swap_remove(i);
            }
            None => lost.push(n),
        }
    }
    lost
}

/// The six classes of DESIGN-S73 (number, name). Checked top to bottom.
pub fn classify(b: &Branch, hours: i64) -> (u8, &'static str, String) {
    let pr_open = b.pr.as_ref().is_some_and(|p| p.open);
    let review_at = b.review.as_ref().filter(|r| r.at_head);
    let receipt_at = b.receipt.as_ref().filter(|r| r.at_head);
    // merged = in origin/main, nothing else (IFACE-s79 r11 1; f1fae 11-15: #764 sat under
    // #767, was called merged, and next said "collect its tree")
    if b.unique == 0 {
        return (5, "merged", "all in main".to_owned());
    }
    // every commit of its own sits in an open PR and it has no PR of its own: that PR
    // carries it. Not merged, not a to-do. A branch with its own open PR is judged as one.
    if b.unique_not_in_prs == 0 && !pr_open {
        return (
            7,
            "superseded_by_pr",
            format!("all in open PR {}", nums(&b.in_prs)),
        );
    }
    if let Some(a) = &b.acked {
        return (6, "stale", format!("ack: {a}"));
    }
    // another seat carries it on its own integ-src branch: not this branch's to-do (S79)
    if let Some((t, _)) = b.taken_over_by.as_ref().filter(|_| !pr_open) {
        return (6, "stale", format!("taken over by {t}"));
    }
    if let Some(n) = abandonable(b) {
        return (
            6,
            "stale",
            format!("abandonable: PR #{n} was closed without merge at this head"),
        );
    }
    // an unfixed H/M review is a fix even when the branch is absorbed (5d33a 254a579d L1)
    if let Some(r) = review_at.filter(|r| r.h + r.m > 0) {
        // 519d0 11-15: say where the content went, so the fix lands on the head that carries it
        let into = match b.absorbed_into.as_ref().filter(|_| !pr_open) {
            Some((h, _)) => format!(
                " (content absorbed into {}, fix it there)",
                &h[..h.len().min(8)]
            ),
            None => String::new(),
        };
        return (
            3,
            "fix",
            format!("review at head has H/M {}/{}{into}", r.h, r.m),
        );
    }
    // the red receipt is old news: a green one ran on a commit that carries this head (S79).
    // Before the workflow size and the red receipt (519d0 11-15, feat/drive-d1c5-ebb36-1115):
    // with no open PR this branch is never pushed itself, its content is in that head
    if let Some((h, f)) = b
        .absorbed_into
        .as_ref()
        .filter(|_| !pr_open && receipt_at.is_some_and(|r| !r.green()))
    {
        return (
            4,
            "in_progress",
            format!(
                "receipt at head not green, but {} above it is green ({f})",
                &h[..h.len().min(8)]
            ),
        );
    }
    // GitHub never runs a workflow file over its limit: green elsewhere means nothing (#755)
    if !b.workflow_too_big.is_empty() {
        let list: Vec<String> = b
            .workflow_too_big
            .iter()
            .map(|(f, n, at)| format!("{f} {n} bytes ({at})"))
            .collect();
        return (
            3,
            "fix",
            format!(
                "workflow file over the {WORKFLOW_MAX_BYTES}-byte limit (GitHub does not run it, the merge queue refuses it): {}",
                list.join(", ")
            ),
        );
    }
    if let Some(p) =
        b.pr.as_ref()
            .filter(|p| p.open && p.checks.as_deref() == Some("FAILURE"))
    {
        let red = p.red_checks.as_deref().unwrap_or_default().join(", ");
        return (3, "fix", format!("PR #{} checks FAILURE: {red}", p.number));
    }
    // every run green, but a cell the merge queue requires was never made (#755)
    if let Some(p) =
        b.pr.as_ref()
            .filter(|p| p.open && p.checks.as_deref() == Some("MISSING"))
    {
        let miss = p.missing_checks.as_deref().unwrap_or_default().join(", ");
        return (
            3,
            "fix",
            format!("PR #{} required checks never created: {miss}", p.number),
        );
    }
    if receipt_at.is_some_and(|r| r.sealed == Some(false)) {
        return (
            3,
            "fix",
            "receipt suspect: RECEIPT-SHA256 does not match its text".to_owned(),
        );
    }
    if receipt_at.is_some_and(|r| !r.finished()) {
        return (
            3,
            "fix",
            "receipt incomplete: no rc lines or no HEAD-END for its head".to_owned(),
        );
    }
    if let Some(r) = receipt_at.filter(|r| !r.green()) {
        return (
            3,
            "fix",
            format!("receipt at head not green: {}", r.nonzero.join(",")),
        );
    }
    // an open PR that conflicts with main takes main in first (f1fae COMPARE r2 1): waiting
    // for its checks means nothing, they all run again after that merge
    if let Some(c) = b
        .conflicts_with_main
        .as_ref()
        .filter(|c| pr_open && c.conflict)
    {
        let also: Vec<u64> = b
            .conflicts_with
            .iter()
            .filter(|(_, files)| files.iter().any(|f| c.files.contains(f)))
            .map(|(n, _)| *n)
            .collect();
        let also = if also.is_empty() {
            String::new()
        } else {
            format!("; open PRs on the same files: {}", nums(&also))
        };
        return (
            3,
            "fix",
            format!("conflicts with main: {}{also}", c.files.join(", ")),
        );
    }
    let reviewed = b.reviewed();
    let green = receipt_at.is_some_and(Receipt::green);
    // a clash with another open PR does not change the class (IFACE-s79 r11 3): the PR behind
    // merges main once the one in front is in; `conflicts_with` keeps the list
    if reviewed && green && !b.pushed_to.is_empty() && pr_open {
        // mergeable only with every check green; still running or not read = waiting (r11 2)
        let checks = b.pr.as_ref().and_then(|p| p.checks.as_deref());
        if checks == Some("SUCCESS") {
            return (
                1,
                "merge_first",
                "reviewed + green at head, PR open".to_owned(),
            );
        }
        return (
            8,
            "waiting_ci",
            format!(
                "reviewed + green at head, PR open; checks {}",
                checks.unwrap_or("not read")
            ),
        );
    }
    if review_at.is_some() || receipt_at.is_some() {
        if b.pushed_to.is_empty() {
            return (
                2,
                "push_or_pr",
                "review / receipt at head but not pushed".to_owned(),
            );
        }
        if !pr_open {
            return (
                2,
                "push_or_pr",
                "review / receipt at head but no open PR".to_owned(),
            );
        }
    }
    if b.age_h > hours && !pr_open {
        return (
            6,
            "stale",
            format!("no commit for {}h and no open PR", b.age_h),
        );
    }
    (
        4,
        "in_progress",
        "no review / receipt at head yet".to_owned(),
    )
}

fn nums(n: &[u64]) -> String {
    n.iter()
        .map(|x| format!("#{x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Files that change a check (S73j, 519d0 / Andy 11-14): CI workflows, the xtask checks, the
/// docs-only list, ruleset files. A branch touching one is a new gate: it merges last in its
/// batch, after its check has been run against the other heads in the queue.
pub fn gate_files(files: &[String]) -> Vec<String> {
    files
        .iter()
        .filter(|f| {
            let f = f.replace('\\', "/");
            f.starts_with(".github/workflows/")
                || f.starts_with(".github/rulesets/")
                || f.starts_with(".github/scripts/")
                || f.starts_with("xtask/src/")
                || f == "aib/tools/release/docs_only.py"
                || f.rsplit('/').next().is_some_and(|n| n.contains("ruleset"))
        })
        .cloned()
        .collect()
}

/// 「可放棄」(s33, Andy 11-14 11:38): the branch's PR was closed without merge, and closed at
/// the head the branch still has, with commits that are in neither main nor an open PR. It can
/// be given up; nobody gave it up yet, so a person still decides to keep or drop it. Not a
/// to-do: it is left out of 「沒驗」 and of 「要處理」. A commit after the close (work went on)
/// or an unread closed head (`head` None) is not abandonable. Some(PR number) when it is.
pub fn abandonable(b: &Branch) -> Option<u64> {
    let p = b.pr.as_ref().filter(|p| !p.open && !p.merged)?;
    (b.unique_not_in_prs > 0 && p.head.as_deref() == Some(b.head.as_str())).then_some(p.number)
}

/// `git diff --name-status --no-renames`: (every path, the paths still there at the head). A
/// deleted file changes a check as much as an edited one, but a deleted `.py` is Python
/// leaving, not Python carried (043dd s33 review L1).
pub fn diff_names(status: &str) -> (Vec<String>, Vec<String>) {
    let rows: Vec<(&str, &str)> = status.lines().filter_map(|l| l.split_once('\t')).collect();
    let all = rows.iter().map(|(_, p)| (*p).to_owned()).collect();
    let kept = rows
        .iter()
        .filter(|(st, _)| !st.starts_with('D'))
        .map(|(_, p)| (*p).to_owned())
        .collect();
    (all, kept)
}

/// Python source by its ending (043dd s33 review L2: stubs and windowed scripts too).
pub const PY_EXT: [&str; 3] = [".py", ".pyi", ".pyw"];

/// The Python files among `files` (s35: Andy 11-13 「程式只能 Rust 沒有 Python」, so an
/// unmerged branch that still carries Python shows at a glance).
pub fn py_files(files: &[String]) -> Vec<String> {
    files
        .iter()
        .filter(|f| PY_EXT.iter().any(|e| f.to_ascii_lowercase().ends_with(*e)))
        .cloned()
        .collect()
}

/// Classes `--gate` stops on.
pub fn blocks(class: u8) -> bool {
    matches!(class, 1..=3 | 8)
}

/// The seat code at the end of a branch name: `…-<5 hex>` or `…-<5 hex>-<4 digits>`.
pub fn seat_of(branch: &str) -> Option<String> {
    let last = branch.rsplit('/').next().unwrap_or(branch);
    let parts: Vec<&str> = last.split('-').collect();
    let hex5 = |s: &str| s.len() == 5 && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'));
    let digits4 = |s: &str| s.len() == 4 && s.bytes().all(|c| c.is_ascii_digit());
    let n = parts.len();
    if n >= 3 && digits4(parts[n - 1]) && hex5(parts[n - 2]) {
        return Some(parts[n - 2].to_owned());
    }
    if n >= 2 && hex5(parts[n - 1]) {
        return Some(parts[n - 1].to_owned());
    }
    None
}

fn is_sha40(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

/// `結論: X 頭: <40> H/M/L/N: a/b/c/d …` ⇒ (verdict, head, h, m, l, n). Anything else = None.
pub fn parse_review_line(line: &str) -> Option<(String, String, [u32; 4])> {
    let rest = line.trim().strip_prefix("結論:")?;
    let (verdict, rest) = rest.split_once("頭:")?;
    let rest = rest.trim_start();
    let head = rest.get(..40)?;
    if !is_sha40(head) {
        return None;
    }
    let (_, counts) = rest.split_once("H/M/L/N:")?;
    let counts = counts.split_whitespace().next()?;
    let v: Vec<u32> = counts
        .split('/')
        .map(|x| x.parse::<u32>().ok())
        .collect::<Option<Vec<_>>>()?;
    let [h, m, l, n] = v.as_slice() else {
        return None;
    };
    Some((verdict.trim().to_owned(), head.to_owned(), [*h, *m, *l, *n]))
}

/// Heads a receipt names: any 40-hex in its first five lines (`head <40>`, `頭 <40>`, …), and
/// the `== HEAD=<40>` / `HEAD <40>` / `HEAD-END <40>` lines of tools-shared/receipt-run.sh.
pub fn receipt_heads(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let marked = text.lines().skip(5).filter(|l| {
        l.starts_with("== HEAD=") || l.starts_with("HEAD ") || l.starts_with("HEAD-END ")
    });
    for line in text.lines().take(5).chain(marked) {
        for tok in line.split(|c: char| c.is_whitespace() || c == '=' || c == ':') {
            if is_sha40(tok) && !out.iter().any(|h| h == tok) {
                out.push(tok.to_owned());
            }
        }
    }
    out
}

/// Every `(key, red)` a receipt names: `name_rc=N`, `STEP name rc=N`, `RC name=N`, in order,
/// first one per key. `KNIFE` lines are skipped (a knife biting is rc 101 by design). A value
/// followed by `(want N)` is red when it is not N, `(want non-0)` when it is 0; one followed by
/// `(report only)` is never red (043dd L1).
///
/// Knives (`RC knife_X=…`, S79 519d0 11-15): a knife that bit (rc not 0, not `INVALID`) is
/// not listed; one that did not bite (rc 0), did not run or did not build (`INVALID`) or found
/// no anchor (`anchor-missing`, or no number) is a red key, so that receipt is not green: a
/// knife that does not bite guards nothing. A line with `ANCHOR-MISSING` is red as
/// `anchor_missing`; receipt-run.sh's summary lines `KNIVES-RC N` / `MISSES-COUNT N` (no `=`)
/// not 0 are red as `knives_rc` / `misses` (1ced7: so one way of writing it is not missed).
/// A `RECEIPT-RED` / `KNIVES-INCOMPLETE` line (a run cut short before the knife summary, 5d33a)
/// is red as `receipt_red` / `knives_incomplete`: HEAD-END alone does not make it whole.
fn rc_keys(text: &str) -> Vec<(String, bool)> {
    fn put(out: &mut Vec<(String, bool)>, key: &str, red: bool) {
        if !out.iter().any(|(k, _)| k == key) {
            out.push((key.to_owned(), red));
        }
    }
    let mut out: Vec<(String, bool)> = Vec::new();
    // receipt-run.sh prints KNIVES-RC 0 after KNIVES-NOT-RUN too (no knife file given): then a 0
    // is no knife result and no step (5d33a 273bdce9 M1)
    let not_run = text
        .lines()
        .any(|l| l.trim_start().starts_with("KNIVES-NOT-RUN"));
    for line in text.lines() {
        if line.contains("ANCHOR-MISSING") {
            put(&mut out, "anchor_missing", true);
        }
        // 5d33a: receipt-run.sh marks a run cut short (the knife section not finished, its
        // summary never printed) with these lines; HEAD-END alone does not make it whole
        for (mark, key) in [
            ("RECEIPT-RED", "receipt_red"),
            ("KNIVES-INCOMPLETE", "knives_incomplete"),
        ] {
            if line.trim_start().starts_with(mark) {
                put(&mut out, key, true);
            }
        }
        // a 0 is a green step too (S79-anc 5, #809): a whole run that names them closes an older
        // file's red one instead of leaving it as a step only the older file ran. Not when the
        // knives did not run: that run closes no knife result
        for (mark, key) in [("KNIVES-RC ", "knives_rc"), ("MISSES-COUNT ", "misses")] {
            let bad = |n: &str| n.trim().parse::<i64>().ok().is_none_or(|n| n != 0);
            if let Some(n) = line.trim().strip_prefix(mark) {
                let red = bad(n);
                if red || !not_run {
                    put(&mut out, key, red);
                }
            }
        }
        if line.trim_start().starts_with("KNIFE") {
            continue;
        }
        let toks: Vec<&str> = line
            .split(|c: char| c.is_whitespace() || c == ';')
            .filter(|t| !t.is_empty())
            .collect();
        for (i, t) in toks.iter().enumerate() {
            let Some((key, val)) = t.split_once('=') else {
                continue;
            };
            let after_rc = i.checked_sub(1).map(|j| toks[j]) == Some("RC");
            let key = if after_rc {
                // tools-shared/receipt-run.sh: `RC name=N`; a knife biting is non-0 by design
                if key.is_empty() {
                    continue;
                }
                if key.starts_with("knife_") {
                    let invalid = toks.get(i + 1).is_some_and(|w| w.starts_with("INVALID"));
                    let bit = val.parse::<i64>().is_ok_and(|rc| rc != 0) && !invalid;
                    if !bit {
                        put(&mut out, key, true);
                    }
                    continue;
                }
                key
            } else if key == "rc" {
                match (i.checked_sub(2).map(|j| toks[j]), i.checked_sub(1)) {
                    (Some("STEP"), Some(j)) => toks[j],
                    _ => continue,
                }
            } else if key.strip_suffix("_rc").is_some_and(|k| !k.is_empty()) {
                key
            } else {
                continue;
            };
            let digits: String = val.chars().take_while(char::is_ascii_digit).collect();
            if let Ok(rc) = digits.parse::<i64>() {
                let red = match (toks.get(i + 1).copied(), toks.get(i + 2).copied()) {
                    (Some("(report"), Some(w)) if w.starts_with("only") => false,
                    (Some("(want"), Some(w)) => match w.trim_end_matches(')') {
                        "non-0" => rc == 0,
                        n => n.parse::<i64>().map_or(rc != 0, |n| rc != n),
                    },
                    _ => rc != 0,
                };
                put(&mut out, key, red);
            }
        }
    }
    // 519d0: receipt-run.sh prints `KNIVES-RC` on every run that reaches its summary, then
    // its EXIT trap writes `END` and the seal; sealed after `END` with no `KNIVES-RC` = the
    // script died before the summary (b3e-a9136: an unbound variable at the knife step)
    let has = |p: &str| text.lines().any(|l| l.trim_start().starts_with(p));
    if has("RECEIPT-SHA256 ") && has("END ") && !has("KNIVES-RC ") {
        put(&mut out, "knives_summary_missing", true);
    }
    out
}

/// Keys of a receipt whose rc is not 0. Not counted: `KNIFE` lines, `head_rc` / `base_rc`
/// (red tests rerun at head and at main for comparison), and `py_suite_rc` when the receipt
/// has that main comparison.
pub fn receipt_nonzero(text: &str) -> Vec<String> {
    let compared_with_main = text.contains("base_rc=");
    rc_keys(text)
        .into_iter()
        .filter(|(k, red)| {
            *red && !matches!(k.as_str(), "head_rc" | "base_rc")
                && !(k == "py_suite_rc" && compared_with_main)
        })
        .map(|(k, _)| k)
        .collect()
}

/// Every rc key the receipt names (what ran), except the comparison reruns.
pub fn receipt_steps(text: &str) -> Vec<String> {
    rc_keys(text)
        .into_iter()
        .map(|(k, _)| k)
        .filter(|k| !matches!(k.as_str(), "head_rc" | "base_rc"))
        .collect()
}

/// The seal: last non-empty line `RECEIPT-SHA256 <64 hex>` = sha256 of every byte before that
/// line. Some(true) matches, Some(false) does not (half written / damaged), None = no seal.
/// Not a defence: the same user can recompute it (519d0 / Andy 11-14).
pub fn receipt_sealed(text: &str) -> Option<bool> {
    use sha2::{Digest, Sha256};
    let body = text.trim_end_matches(['\n', '\r']);
    let start = body.rfind('\n').map_or(0, |i| i + 1);
    let want = body[start..]
        .trim_end()
        .strip_prefix("RECEIPT-SHA256 ")?
        .trim();
    let got = Sha256::digest(&text.as_bytes()[..start]);
    let got: String = got.iter().map(|b| format!("{b:02x}")).collect();
    Some(want.len() == 64 && want == got)
}

/// A `HEAD-END <head>` (or `HEAD-END=<head>`) line: the receipt ran to its end on `head`.
pub fn receipt_complete(text: &str, head: &str) -> bool {
    text.lines().any(|l| {
        l.trim()
            .strip_prefix("HEAD-END")
            .map(|r| r.trim_start_matches([' ', '=']).trim())
            == Some(head)
    })
}

/// The reviewing seat in `REVIEW-<topic>-<seat>-<sha8>.md`: the last 5-hex part of the name.
pub fn reviewer_of(file: &str) -> Option<String> {
    let stem = file.strip_prefix("REVIEW-")?.strip_suffix(".md")?;
    stem.split('-')
        .rfind(|p| p.len() == 5 && p.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f')))
        .map(str::to_owned)
}

/// The `非閘理由: …` line of a review (ASCII or full-width colon); None = none or empty.
pub fn not_gate_reason(text: &str) -> Option<String> {
    text.lines().find_map(|l| {
        let r = l.trim().strip_prefix("非閘理由")?;
        let r = r.strip_prefix(':').or_else(|| r.strip_prefix('：'))?.trim();
        (!r.is_empty()).then(|| r.to_owned())
    })
}

/// S73j (519d0 11-14): a new gate merges as a non-gate only with both the PR label `not-gate`
/// and a `非閘理由:` line in a review at the head by someone other than the branch's seat, or
/// (S73k) when its content is one step added for its own crate ([`gatecontent::judge`]).
/// (label, reason, effective). Sorting, not a defence: the same user can write both.
pub fn not_gate(b: &Branch) -> (bool, Option<String>, bool) {
    let by_content = matches!(b.gate_content, Some(Ok(_)));
    let label =
        b.pr.as_ref()
            .is_some_and(|p| p.labels.iter().any(|l| l == "not-gate"));
    let reason = b
        .review
        .as_ref()
        .filter(|r| r.at_head && r.reviewer.is_some() && r.reviewer != b.seat)
        .and_then(|r| r.not_gate_reason.clone());
    let effective = !b.gate_files.is_empty() && ((label && reason.is_some()) || by_content);
    (label, reason, effective)
}

fn opt<T, F: Fn(&T) -> Value>(v: &Option<T>, f: F) -> Value {
    v.as_ref().map_or(Value::Null, f)
}

/// The report as `aib-slice-sweep.v1`.
pub fn to_json(r: &Report, hours: i64) -> Value {
    let branches: Vec<Value> = r
        .branches
        .iter()
        .map(|b| {
            let (class, name, why) = classify(b, hours);
            let ng = not_gate(b);
            json!({
                "branch": b.branch,
                "seat": b.seat,
                "head": b.head,
                "local_head": b.local_head,
                "local": b.local,
                "remote": b.remote,
                "worktree": b.worktree,
                "dirty": b.dirty,
                "last_commit_at": b.last_commit_at,
                "age_h": b.age_h,
                "unique": b.unique,
                "unique_not_in_prs": b.unique_not_in_prs,
                "in_prs": b.in_prs,
                "pushed_to": b.pushed_to,
                "pr": opt(&b.pr, |p| json!({
                    "number": p.number,
                    "state": if p.open { "open" } else { "closed" },
                    "merged": p.merged,
                    "base": p.base,
                    "author": p.author,
                    "mergeable": p.mergeable,
                    "merge_state": p.merge_state,
                    "checks": p.checks,
                    "red_checks": p.red_checks,
                    "infra_checks": p.infra_checks,
                    "missing_checks": p.missing_checks,
                    "cancelled": p.cancelled,
                    "cancelled_cells": p.cancelled_cells,
                })),
                "review": opt(&b.review, |x| json!({
                    "head": x.head, "verdict": x.verdict, "h": x.h, "m": x.m, "l": x.l, "n": x.n,
                    "file": x.file, "at_head": x.at_head, "unreviewed_after": x.unreviewed_after,
                    "scope": x.scope.text(),
                    "resolved_after_review": opt(&x.resolved_after_review, |m| m.iter().map(|y| json!({
                        "commit": y.commit, "parent": y.parent, "from_main": y.from_main,
                        "files": y.files,
                    })).collect()),
                })),
                "receipt": opt(&b.receipt, |x| json!({
                    "head": x.head, "file": x.file, "nonzero": x.nonzero, "green": x.green(),
                    "at_head": x.at_head, "steps": x.steps, "sealed": x.sealed,
                    "complete": x.complete, "stacked_at": x.stacked_at,
                })),
                "fallback_cells": b.fallback_cells.iter().map(|(c, f)| json!({"cell": c, "file": f})).collect::<Vec<_>>(),
                "stacked_on": b.stacked_on,
                "stack_top": b.stack_top,
                "conflicts_with": b.conflicts_with.iter().map(|(n, f)| json!({"pr": n, "files": f})).collect::<Vec<_>>(),
                "conflicts_with_branches": b.conflicts_with_branches.iter().map(|(n, f)| json!({"branch": n, "files": f})).collect::<Vec<_>>(),
                "conflicts_with_main": opt(&b.conflicts_with_main, |c| json!({"conflict": c.conflict, "files": c.files})),
                "claimed_by": opt(&b.claimed_by, |(seat, at)| json!({"seat": seat, "at": at})),
                "new_gate": !b.gate_files.is_empty(),
                "gate_files": b.gate_files,
                "not_gate": {"label": ng.0, "reason": ng.1, "effective": ng.2,
                    "by_content": matches!(b.gate_content, Some(Ok(_))),
                    "content": opt(&b.gate_content, |c| json!(c.as_ref().unwrap_or_else(|e| e)))},
                "abandonable": abandonable(b).is_some(),
                "py_files": b.py_files,
                "steps_lost": opt(&b.steps_lost, |s| json!(s)),
                "reviewed": b.reviewed(),
                "review_parts": opt(&b.review_parts, |p| json!({
                    "ok": p.ok(), "files": p.files, "uncovered": p.uncovered,
                    "unreviewed_merges": p.unreviewed_merges,
                })),
                "taken_over_by": opt(&b.taken_over_by, |(br, h)| json!({"branch": br, "head": h})),
                "absorbed_into": opt(&b.absorbed_into, |(h, f)| json!({"head": h, "receipt": f})),
                "workflow_too_big": b.workflow_too_big.iter().map(|(f, n, at)| json!({"file": f, "bytes": n, "at": at})).collect::<Vec<_>>(),
                "resolved_merges": opt(&b.resolved_merges, |m| m.iter().map(|x| json!({
                    "commit": x.commit, "parent": x.parent, "from_main": x.from_main,
                    "files": x.files,
                })).collect()),
                "class": class,
                "class_name": name,
                "why": why,
            })
        })
        .collect();
    json!({
        "schema": SCHEMA_ID,
        "generated_at": r.generated_at,
        "main": r.main,
        "fast": r.fast,
        "complete": r.errors.is_empty(),
        "errors": r.errors,
        "open_prs": r.open_prs.iter().map(|p| json!({"number": p.number, "head_ref": p.head_ref, "head": p.head})).collect::<Vec<_>>(),
        "branches": branches,
    })
}

/// Checks `v` against the JSON Schema subset the contract uses: `type` (one or a list),
/// `enum`, `required`, `properties`, `additionalProperties: false`, `items`. Returns one line
/// per problem, with the path; empty = valid. Keywords outside the subset are refused so the
/// contract cannot silently grow a rule nobody checks.
pub fn validate(schema: &Value, v: &Value) -> Vec<String> {
    let mut errs = Vec::new();
    check(schema, v, "$", &mut errs);
    errs
}

const KNOWN: [&str; 11] = [
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "default",
];

fn type_ok(t: &str, v: &Value) -> bool {
    match t {
        "object" => v.is_object(),
        "array" => v.is_array(),
        "string" => v.is_string(),
        "integer" => v.is_i64() || v.is_u64(),
        "boolean" => v.is_boolean(),
        "null" => v.is_null(),
        _ => false,
    }
}

fn check(s: &Value, v: &Value, path: &str, errs: &mut Vec<String>) {
    let Some(s) = s.as_object() else {
        errs.push(format!("{path}: schema is not an object"));
        return;
    };
    for k in s.keys() {
        if !KNOWN.contains(&k.as_str()) {
            errs.push(format!("{path}: schema keyword {k} not supported"));
        }
    }
    if let Some(t) = s.get("type") {
        let types: Vec<&str> = match t {
            Value::String(x) => vec![x.as_str()],
            Value::Array(a) => a.iter().filter_map(Value::as_str).collect(),
            _ => Vec::new(),
        };
        if !types.iter().any(|t| type_ok(t, v)) {
            errs.push(format!("{path}: want {}, got {v}", types.join("|")));
            return;
        }
    }
    if v.is_null() {
        return;
    }
    if let Some(Value::Array(e)) = s.get("enum") {
        if !e.contains(v) {
            errs.push(format!("{path}: {v} not in enum"));
        }
    }
    if let Some(o) = v.as_object() {
        let props = s.get("properties").and_then(Value::as_object);
        let empty = Map::new();
        let props = props.unwrap_or(&empty);
        if let Some(Value::Array(req)) = s.get("required") {
            for k in req.iter().filter_map(Value::as_str) {
                if !o.contains_key(k) {
                    errs.push(format!("{path}: missing {k}"));
                }
            }
        }
        let closed = s.get("additionalProperties") == Some(&Value::Bool(false));
        for (k, x) in o {
            match props.get(k) {
                Some(ps) => check(ps, x, &format!("{path}.{k}"), errs),
                None if closed => errs.push(format!("{path}: extra field {k}")),
                None => {}
            }
        }
    }
    if let (Some(a), Some(items)) = (v.as_array(), s.get("items")) {
        for (i, x) in a.iter().enumerate() {
            check(items, x, &format!("{path}[{i}]"), errs);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> Value {
        serde_json::from_str(SCHEMA).expect("schema parses")
    }

    fn sample() -> Report {
        Report {
            generated_at: 1,
            main: "a".repeat(40),
            fast: false,
            errors: Vec::new(),
            open_prs: vec![OpenPr {
                number: 7,
                head_ref: "integ/x".into(),
                head: "b".repeat(40),
            }],
            branches: vec![
                Branch {
                    branch: "feat/x-1ced7-1114".into(),
                    seat: seat_of("feat/x-1ced7-1114"),
                    head: "c".repeat(40),
                    local: true,
                    unique: 2,
                    unique_not_in_prs: 2,
                    pushed_to: vec!["origin/integ-src/x-1ced7".into()],
                    pr: Some(PrInfo {
                        number: 8,
                        open: true,
                        base: Some("main".into()),
                        author: Some("someone".into()),
                        mergeable: Some("MERGEABLE".into()),
                        merge_state: Some("clean".into()),
                        checks: Some("SUCCESS".into()),
                        red_checks: Some(Vec::new()),
                        ..PrInfo::default()
                    }),
                    review: Some(Review {
                        head: "c".repeat(40),
                        verdict: "通過".into(),
                        h: 0,
                        m: 0,
                        l: 1,
                        n: 0,
                        file: "REVIEW-x.md".into(),
                        at_head: true,
                        unreviewed_after: Vec::new(),
                        resolved_after_review: None,
                        reviewer: Some("043dd".into()),
                        not_gate_reason: Some("只改註解".into()),
                        scope: Scope::Whole,
                    }),
                    receipt: Some(Receipt {
                        head: "c".repeat(40),
                        file: "TEST-x.txt".into(),
                        nonzero: Vec::new(),
                        at_head: true,
                        steps: vec!["fmt_rc".into()],
                        sealed: Some(true),
                        complete: true,
                        stacked_at: None,
                    }),
                    stack_top: true,
                    conflicts_with_branches: vec![("feat/z".into(), vec!["a.rs".into()])],
                    conflicts_with_main: Some(Clash::default()),
                    claimed_by: Some(("a9136".into(), 1)),
                    gate_files: vec![".github/workflows/aibd.yml".into()],
                    ..Branch::default()
                },
                Branch {
                    branch: "fix/y".into(),
                    head: "d".repeat(40),
                    ..Branch::default()
                },
            ],
        }
    }

    #[test]
    fn the_output_matches_the_schema() {
        let v = to_json(&sample(), 72);
        assert_eq!(validate(&schema(), &v), Vec::<String>::new());
        assert_eq!(v["branches"][0]["class"], 1);
        assert_eq!(v["branches"][0]["seat"], "1ced7");
        assert_eq!(v["branches"][1]["class_name"], "merged");
    }

    #[test]
    fn a_missing_or_extra_field_is_red() {
        let s = schema();
        let mut v = to_json(&sample(), 72);
        v["branches"][0]
            .as_object_mut()
            .expect("object")
            .remove("why");
        assert_eq!(validate(&s, &v), vec!["$.branches[0]: missing why"]);
        let mut v = to_json(&sample(), 72);
        v["branches"][1]["extra"] = json!(1);
        assert_eq!(validate(&s, &v), vec!["$.branches[1]: extra field extra"]);
        let mut v = to_json(&sample(), 72);
        v["open_prs"][0]["number"] = json!("7");
        assert_eq!(validate(&s, &v).len(), 1);
        let mut v = to_json(&sample(), 72);
        v["branches"][0]["class_name"] = json!("ready");
        assert_eq!(
            validate(&s, &v),
            vec![r#"$.branches[0].class_name: "ready" not in enum"#]
        );
        v["schema"] = json!("aib-slice-sweep.v2");
        assert_eq!(validate(&s, &v).len(), 2);
    }

    #[test]
    fn a_schema_keyword_the_checker_does_not_read_is_refused() {
        let s = json!({"type": "object", "minProperties": 1});
        assert_eq!(
            validate(&s, &json!({})),
            vec!["$: schema keyword minProperties not supported"]
        );
    }

    #[test]
    fn a_new_gate_is_a_non_gate_only_with_label_and_another_seats_reason() {
        assert_eq!(
            reviewer_of("REVIEW-slice-sweep-043dd-b4669b81.md").as_deref(),
            Some("043dd")
        );
        assert_eq!(reviewer_of("REVIEW-ready.md"), None);
        assert_eq!(reviewer_of("TEST-x-043dd.md"), None);
        assert_eq!(
            not_gate_reason("結論: 通過\n非閘理由: 只動註解\n").as_deref(),
            Some("只動註解")
        );
        assert_eq!(
            not_gate_reason("非閘理由：全形冒號").as_deref(),
            Some("全形冒號")
        );
        assert_eq!(not_gate_reason("非閘理由:   \n"), None);
        let mut b = Branch {
            seat: Some("a9136".into()),
            gate_files: vec![".github/workflows/aibd.yml".into()],
            pr: Some(PrInfo {
                number: 751,
                open: true,
                labels: vec!["not-gate".into()],
                ..PrInfo::default()
            }),
            review: review(0, true),
            ..Branch::default()
        };
        if let Some(r) = b.review.as_mut() {
            r.reviewer = Some("043dd".into());
            r.not_gate_reason = Some("只動註解".into());
        }
        assert_eq!(not_gate(&b), (true, Some("只動註解".into()), true));
        // the author's own reason, an old review, or no label: not effective
        let mut own = b.clone();
        if let Some(r) = own.review.as_mut() {
            r.reviewer = Some("a9136".into());
        }
        assert_eq!(not_gate(&own), (true, None, false));
        let mut old = b.clone();
        if let Some(r) = old.review.as_mut() {
            r.at_head = false;
        }
        assert!(!not_gate(&old).2);
        let mut bare = b.clone();
        if let Some(p) = bare.pr.as_mut() {
            p.labels.clear();
        }
        assert_eq!(not_gate(&bare), (false, Some("只動註解".into()), false));
        // not a new gate: nothing to lower
        let mut plain = b.clone();
        plain.gate_files.clear();
        assert!(!not_gate(&plain).2);
        // S73k: one step for its own crate lowers it with no label or reason; a refusal does not
        let mut content = bare.clone();
        content.gate_content = Some(Ok("one step added (x) for aib-x".into()));
        assert_eq!(not_gate(&content), (false, Some("只動註解".into()), true));
        content.gate_content = Some(Err("rule 2: not one block of added lines".into()));
        assert!(!not_gate(&content).2);
        plain.gate_content = Some(Ok("one step".into()));
        assert!(!not_gate(&plain).2, "no gate files: nothing to lower");
    }

    #[test]
    fn a_branch_that_changes_a_check_is_a_new_gate() {
        let files: Vec<String> = [
            ".github/workflows/aibd.yml",
            "xtask/src/knife_anchors.rs",
            "aib/tools/release/docs_only.py",
            ".github/rulesets/main.json",
            "ops/main-ruleset.json",
            ".github/scripts/prev_release.py",
            "crates/aib-slice/src/lib.rs",
            ".github/ISSUE_TEMPLATE/bug.yml",
            "scripts/docs_only.py",
            "scripts/knife.py",
            "xtask/Cargo.toml",
            "docs/.github/workflows/x.yml",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        assert_eq!(
            gate_files(&files),
            vec![
                ".github/workflows/aibd.yml",
                "xtask/src/knife_anchors.rs",
                "aib/tools/release/docs_only.py",
                ".github/rulesets/main.json",
                "ops/main-ruleset.json",
                ".github/scripts/prev_release.py",
            ]
        );
        assert!(gate_files(&["README.md".to_string()]).is_empty());
    }

    #[test]
    fn seats_come_from_the_name_tail() {
        assert_eq!(
            seat_of("feat/aib-dmq-cli-1ced7-1114").as_deref(),
            Some("1ced7")
        );
        assert_eq!(
            seat_of("integ-src/planpane-1ced7").as_deref(),
            Some("1ced7")
        );
        assert_eq!(
            seat_of("integ/aib2-wave2-519d0-1113").as_deref(),
            Some("519d0")
        );
        assert_eq!(seat_of("fix/tui-paste"), None);
        assert_eq!(seat_of("feat/x-1CED7"), None);
        assert_eq!(seat_of("feat/x-1ced7-11"), None);
    }

    #[test]
    fn the_review_first_line_is_read() {
        let h = "8174730016794a5fe9d272d4c4d8044339e61fca";
        let l = format!("結論: 通過 頭: {h} H/M/L/N: 0/0/2/1 範圍: 全文 x..y");
        assert_eq!(
            parse_review_line(&l),
            Some(("通過".into(), h.into(), [0, 0, 2, 1]))
        );
        assert_eq!(
            parse_review_line(&format!("結論: 要改 頭: {h} H/M/L/N: 0/1/0")),
            None
        );
        assert_eq!(
            parse_review_line(&format!("# 結論: 通過 頭: {h} H/M/L/N: 0/0/0/0")),
            None
        );
        assert_eq!(
            parse_review_line("結論: 通過 頭: abc H/M/L/N: 0/0/0/0"),
            None
        );
    }

    /// S79-c1fb: cell-steps' FALLBACK receipt of one macOS cell, as f1fae's #790 run wrote it
    /// (plus the `CELL` line), and every way it is not a whole green run of that cell.
    #[test]
    fn a_fallback_receipt_counts_only_whole_and_green() {
        let h = "2".repeat(40);
        let sha = "5d".repeat(32);
        let good = format!(
            "HEAD {h}  START 2026-11-15 21:06:39\nCELL rust (macos-latest)\n\
             JOB rust  SLICE-SHA256 {sha}  SLICE-STEPS 55\n已登記排隊:第 4 位\n\
             ACQUIRED 2026-11-15 21:10:00\n\
             NOT-MACOS Windows version (what the Job tests ran on) (env.RUN == 'true' && runner.os == 'Windows')\n\
             NOT-MACOS no Rust path changed (env.RUN != 'true')\n\
             STEP rc=0 1s  fmt\nSTEP rc=0 233s  test (macOS)\nRESTORED-OK\nCELL-RED 0\n\
             END rc=0 2026-11-15 21:55:44\n"
        );
        assert_eq!(
            fallback_cell(&good),
            Some(Fallback {
                cell: "rust (macos-latest)".to_owned(),
                head: h.clone(),
                job: "rust".to_owned(),
                slice_sha: sha.clone(),
            })
        );
        let swap = |a: &str, b: &str| good.replacen(a, b, 1);
        for (bad, why) in [
            (swap("CELL rust (macos-latest)\n", ""), "no_cell_line"),
            (
                swap("CELL rust", "CELL rust (macos-latest)\nCELL rust"),
                "two_cells",
            ),
            (
                swap(
                    "STEP rc=0 1s  fmt\n",
                    "STEP rc=0 1s  fmt\nSKIP-REUSED clippy\n",
                ),
                "skip_reused",
            ),
            (swap("STEP rc=0 233s", "STEP rc=1 233s"), "red_step"),
            (swap("CELL-RED 0", "CELL-RED 1"), "cell_red"),
            (swap("END rc=0", "END rc=3"), "end_not_0"),
            (swap("RESTORED-OK", "RESTORE-FAIL"), "restore_fail"),
            (
                good.lines()
                    .filter(|l| !l.starts_with("STEP "))
                    .collect::<Vec<_>>()
                    .join("\n"),
                "no_step",
            ),
            (swap(&format!("HEAD {h}"), "HEAD 2222"), "short_head"),
            (
                swap(
                    &format!("JOB rust  SLICE-SHA256 {sha}  SLICE-STEPS 55\n"),
                    "",
                ),
                "no_job",
            ),
            (
                swap(&format!("SLICE-SHA256 {sha}"), "SLICE-SHA256 5d5d"),
                "short_sha",
            ),
            (
                swap(
                    "(env.RUN != 'true')",
                    "(env.RUN == 'true' && runner.os == 'macOS' && x)",
                ),
                "unknown_if_skipped",
            ),
            (swap("(env.RUN != 'true')", "()"), "no_if_skipped"),
        ] {
            assert_eq!(fallback_cell(&bad), None, "reason: {why}");
        }
        assert!(fallback_may_cover("rust (macos-latest)"));
        assert!(
            !fallback_may_cover("smoke (macos-latest, 3.12)"),
            "reason: smoke_covered"
        );
    }

    /// S79-c1fb2 (5d33a 82336470 L1): a FALLBACK receipt counts for the head only when the
    /// job it names is in the head's workflow with the same lines, and its cell is that job's.
    #[test]
    fn a_fallback_receipt_counts_only_for_the_job_it_ran_at_that_head() {
        let yml = "name: aibd\non:\n  push:\njobs:\n  changes:\n    runs-on: ubuntu-latest\n\
                   \x20   steps:\n      - run: echo hi\n  rust:\n    needs: changes\n\
                   \x20   strategy:\n      matrix:\n        os: [macos-latest]\n    steps:\n\
                   \x20     - name: fmt\n        run: cargo fmt\n  cloud-knives:\n\
                   \x20   name: cloud decision knives (macOS)\n    steps:\n      - run: k\n";
        let rust = job_slice(yml, "rust").expect("rust");
        assert!(
            rust.starts_with("  rust:\n") && rust.ends_with("        run: cargo fmt\n"),
            "{rust}"
        );
        let last = job_slice(yml, "cloud-knives").expect("last");
        assert!(
            last.ends_with("      - run: k\n"),
            "reason: last_job_cut {last}"
        );
        assert_eq!(
            job_slice(yml, "push"),
            None,
            "reason: an on: key taken for a job"
        );
        assert_eq!(job_label(&rust, "rust").as_deref(), Some("rust"));
        assert_eq!(
            job_label(&last, "cloud-knives").as_deref(),
            Some("cloud decision knives (macOS)")
        );
        assert_eq!(job_label("  x:\n    name: ${{ matrix.n }}\n", "x"), None);
        let f = |cell: &str, job: &str, sha: &str| Fallback {
            cell: cell.to_owned(),
            head: "a".repeat(40),
            job: job.to_owned(),
            slice_sha: sha.to_owned(),
        };
        let sha = job_slice_sha(yml, "rust").expect("sha");
        assert!(fallback_at_head(
            &f("rust (macos-latest)", "rust", &sha),
            yml
        ));
        let k = job_slice_sha(yml, "cloud-knives").expect("sha");
        assert!(fallback_at_head(
            &f("cloud decision knives (macOS)", "cloud-knives", &k),
            yml
        ));
        for (bad, why) in [
            (f("rust (macos-latest)", "rust", &k), "other_jobs_lines"),
            (
                f("cloud decision knives (macOS)", "rust", &sha),
                "cell_of_another_job",
            ),
            (f("rusty (macos-latest)", "rust", &sha), "cell_name_prefix"),
            (f("rust (macos-latest)", "gone", &sha), "job_not_at_head"),
        ] {
            assert!(!fallback_at_head(&bad, yml), "reason: {why}");
        }
        let changed = yml.replace("cargo fmt\n", "cargo fmt --all\n");
        assert!(
            !fallback_at_head(&f("rust (macos-latest)", "rust", &sha), &changed),
            "reason: job_changed_since_the_run"
        );
    }

    #[test]
    fn receipts_name_their_head_and_their_red_keys() {
        let h = "8174730016794a5fe9d272d4c4d8044339e61fca";
        let t = format!(
            "head {h} want {h}\nfmt_rc=0\nclippy_ws_rc=101\nSTEP plan_tests rc=1 :: x\nKNIFE k bite_rc=101\n\
             py_suite_rc=1\nhead_rc=1 a.py\nbase_rc=1 a.py\n"
        );
        assert_eq!(receipt_heads(&t), vec![h.to_owned()]);
        assert_eq!(receipt_nonzero(&t), vec!["clippy_ws_rc", "plan_tests"]);
        // without the main comparison a red Python suite counts
        assert_eq!(receipt_nonzero("py_suite_rc=1\n"), vec!["py_suite_rc"]);
        assert_eq!(
            receipt_nonzero("fmt_rc=0; win_clippy_ws_rc=0\nrc=3\n_rc=1\n"),
            Vec::<String>::new()
        );
        assert_eq!(
            receipt_steps(&t),
            vec!["fmt_rc", "clippy_ws_rc", "plan_tests", "py_suite_rc"]
        );
        // tools-shared/receipt-run.sh: `== HEAD=` line, `RC name=N`, knife rc non-0 by design
        let shared = format!(
            "START x\nACQUIRED x\na\nb\nc\n== HEAD={h}\nRC fmt=0\nRC clippy=101\n\
             RC knife_k1=101 (want non-0)\nRC restored=0\nHEAD-END {h}\n"
        );
        assert_eq!(receipt_heads(&shared), vec![h.to_owned()]);
        assert_eq!(receipt_nonzero(&shared), vec!["clippy"]);
        assert_eq!(receipt_steps(&shared), vec!["fmt", "clippy", "restored"]);
        // 043dd L1: `(want N)` is red only when not N; `(report only)` is never red
        let wants = "RC gate_from_base=1 (want 1)\nRC gate_at_head=0 (want 1)\n\
                     RC uiwords_vs_main=2 (report only)\nRC must_fail=0 (want non-0)\n\
                     RC must_fail2=3 (want non-0)\n";
        assert_eq!(receipt_nonzero(wants), vec!["gate_at_head", "must_fail"]);
        assert_eq!(receipt_steps(wants).len(), 5);
        // the end mark names the head it finished on
        assert!(receipt_complete(&shared, h));
        assert!(receipt_complete(&format!("x\nHEAD-END={h}\n"), h));
        assert!(!receipt_complete(&shared, &"d".repeat(40)));
        assert!(!receipt_complete(&format!("== HEAD={h}\nRC fmt=0\n"), h));
    }

    /// S79 (519d0 11-15, next --all 07:28): the names of the four branches taken over.
    #[test]
    fn a_taker_branch_names_the_core_of_the_branch_it_carries() {
        assert_eq!(
            name_core("feat/aib-consent-t136-3a-c477a-1113"),
            "aib-consent-t136-3a"
        );
        assert_eq!(name_core("integ-src/t136-3a-7b81e"), "t136-3a");
        assert_eq!(
            name_core("ci/aibd-cloud-cache-keys-c477a-1113"),
            "aibd-cloud-cache-keys"
        );
        assert_eq!(
            name_core("integ-src/aib-a11-2-alive-b433b"),
            "aib-a11-2-alive"
        );
        assert_eq!(name_core("plain"), "plain");
        assert_eq!(name_core("x/1113"), "1113");
        assert!(takes_over("aib-consent-t136-3a", "t136-3a"));
        assert!(takes_over("aib-a11-2-alive", "aib-a11-2-alive"));
        assert!(takes_over("aibd-cloud-cache-keys", "aibd-cloud-cache-keys"));
        // one part only, a part cut in the middle, another slice, nothing
        assert!(!takes_over("aib-consent-t136-3a", "3a"));
        assert!(!takes_over("aib-consent-t136-3a", "36-3a"));
        assert!(!takes_over("aib-a11-2b-alive", "aib-a11-2-alive"));
        assert!(!takes_over("aib-consent-t136-3a", ""));
    }

    /// S79 (519d0 11-15): a knife that did not bite guards nothing, so its receipt is not
    /// green. The knife lines are copied from real receipts (ebb36 d1c5, b433b p-1b, a63ab
    /// now-goal); a knife that bit stays out of the keys as before.
    #[test]
    fn a_knife_that_did_not_bite_makes_the_receipt_red() {
        let bit = "RC fmt=0\nRC knife_PK1=101 (want non-0)\nRC knife_PK2=101 (want non-0)\n";
        assert_eq!(receipt_nonzero(bit), Vec::<String>::new());
        assert_eq!(receipt_steps(bit), vec!["fmt"]);
        let zero = "RC fmt=0\nRC knife_DC4=101 (want non-0)\nRC knife_DC5=0 (want non-0)\n";
        assert_eq!(receipt_nonzero(zero), vec!["knife_DC5"]);
        let invalid = "RC knife_PK21=101 INVALID(沒有 test result FAILED / panicked 行:\
                       多半是刀讓編譯失敗,不算咬)\nRC knife_X=124 INVALID(沒跑成,不算咬)\n\
                       RC knife_crate=INVALID crate「x」不在 workspace\n";
        assert_eq!(
            receipt_nonzero(invalid),
            vec!["knife_PK21", "knife_X", "knife_crate"]
        );
        let anchor = "anchor 0\nRC knife_none_why=anchor-missing\nRC restored=0\n";
        assert_eq!(receipt_nonzero(anchor), vec!["knife_none_why"]);
        let upper = "RC fmt=0\nKNIFE k1: ANCHOR-MISSING crates/x/src/lib.rs\n";
        assert_eq!(receipt_nonzero(upper), vec!["anchor_missing"]);
        // receipt-run.sh's summary lines (no `=`): red when not 0, a green step at 0 (S79-anc
        // 5: so a whole green run names them and closes an older red one)
        let summary = "RC fmt=0\nKNIVES-RC 1\nBITES-COUNT 0\nMISSES-COUNT 1\n";
        assert_eq!(receipt_nonzero(summary), vec!["knives_rc", "misses"]);
        let fine = "RC fmt=0\nKNIVES-RC 0\nBITES-COUNT 11\nMISSES-COUNT 0\n";
        assert_eq!(receipt_nonzero(fine), Vec::<String>::new());
        assert_eq!(receipt_steps(fine), vec!["fmt", "knives_rc", "misses"]);
        // 5d33a: a run cut short between HEAD-END and the knife summary is marked red
        let cut =
            "RC fmt=0\nRC knife_k1=101 (want non-0)\nHEAD-END h\nRECEIPT-RED knives cut short\n";
        assert_eq!(receipt_nonzero(cut), vec!["receipt_red"]);
        let part = "RC fmt=0\nKNIVES-INCOMPLETE 3 of 8\n";
        assert_eq!(receipt_nonzero(part), vec!["knives_incomplete"]);
        // 519d0: a real receipt (b3e-a9136) whose script died at the knife step under an old
        // trap: sealed after END, no KNIVES-RC, no RECEIPT-RED, every RC line 0 (one cut-off
        // UTF-8 byte in its bash error line is U+FFFD here, as a lossy read gives it)
        let b3e = include_str!("../tests/fixtures/receipt-b3e-a9136-edfd6f842-cut-short.out");
        assert_eq!(receipt_nonzero(b3e), vec!["knives_summary_missing"]);
        let sealed = "RC fmt=0\nKNIVES-RC 0\nMISSES-COUNT 0\nEND x\nRECEIPT-SHA256 00\n";
        assert_eq!(receipt_nonzero(sealed), Vec::<String>::new());
    }

    #[test]
    fn claim_times_are_read_in_taipei_unless_they_say_otherwise() {
        // 2026-11-14 02:30:00 UTC
        let utc = 1_794_623_400;
        assert_eq!(parse_time("2026-11-14T10:30:00+08:00"), Some(utc));
        assert_eq!(parse_time("2026-11-14 10:30"), Some(utc));
        assert_eq!(parse_time("2026-11-14T02:30:00Z"), Some(utc));
        assert_eq!(parse_time("2026-11-13T21:30:00-05:00"), Some(utc));
        assert_eq!(parse_time("1794623400"), Some(utc));
        for bad in [
            "",
            "2026-13-01 10:00",
            "2026-11-14",
            "2026-11-14 25:00",
            "yesterday",
            "2026-11-14T10:30:00+08:00x",
        ] {
            assert_eq!(parse_time(bad), None, "{bad}");
        }
    }

    #[test]
    fn a_receipt_seal_is_checked_not_trusted() {
        use sha2::{Digest, Sha256};
        let body = "head x\nfmt_rc=0\nEND\n";
        let sum: String = Sha256::digest(body.as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(
            receipt_sealed(&format!("{body}RECEIPT-SHA256 {sum}\n")),
            Some(true)
        );
        // one byte changed above the seal, or a cut-short seal
        let bad = body.replace("rc=0", "rc=1");
        assert_eq!(
            receipt_sealed(&format!("{bad}RECEIPT-SHA256 {sum}\n")),
            Some(false)
        );
        assert_eq!(
            receipt_sealed(&format!("{body}RECEIPT-SHA256 {}\n", &sum[..63])),
            Some(false)
        );
        assert_eq!(receipt_sealed(body), None);
        // a broken seal is not green, and a suspect receipt at head is class 3
        let mut b = base();
        b.receipt = receipt(false);
        if let Some(r) = b.receipt.as_mut() {
            r.sealed = Some(false);
        }
        assert!(!b.receipt.as_ref().is_some_and(Receipt::green));
        assert_eq!(
            classify(&b, 72),
            (
                3,
                "fix",
                "receipt suspect: RECEIPT-SHA256 does not match its text".into()
            )
        );
    }

    fn base() -> Branch {
        Branch {
            branch: "feat/x-1ced7-1114".into(),
            head: "c".repeat(40),
            unique: 1,
            unique_not_in_prs: 1,
            ..Branch::default()
        }
    }

    fn review(h: u32, at_head: bool) -> Option<Review> {
        Some(Review {
            head: "c".repeat(40),
            verdict: "通過".into(),
            h,
            m: 0,
            l: 0,
            n: 0,
            file: "R".into(),
            at_head,
            unreviewed_after: Vec::new(),
            resolved_after_review: None,
            reviewer: None,
            not_gate_reason: None,
            scope: Scope::Whole,
        })
    }

    fn receipt(red: bool) -> Option<Receipt> {
        Some(Receipt {
            head: "c".repeat(40),
            file: "T".into(),
            nonzero: if red {
                vec!["fmt_rc".into()]
            } else {
                Vec::new()
            },
            at_head: true,
            steps: vec!["fmt_rc".into()],
            sealed: None,
            complete: true,
            stacked_at: None,
        })
    }

    fn open_pr() -> Option<PrInfo> {
        Some(PrInfo {
            number: 9,
            open: true,
            ..PrInfo::default()
        })
    }

    #[test]
    fn the_six_classes_in_order() {
        let c = |b: &Branch| classify(b, 72).0;
        // 5: nothing left outside main, even if old or acked
        let mut b = base();
        b.unique = 0;
        b.unique_not_in_prs = 0;
        b.acked = Some("x".into());
        assert_eq!(classify(&b, 72), (5, "merged", "all in main".into()));
        // 7: everything of its own sits in an open PR: carried by it, never "merged"
        // (IFACE-s79 r11 1: #764 under #767 was called merged)
        let mut b = base();
        b.unique_not_in_prs = 0;
        b.in_prs = vec![745];
        b.acked = Some("x".into());
        assert_eq!(
            classify(&b, 72),
            (7, "superseded_by_pr", "all in open PR #745".into())
        );
        // …but a branch with its own open PR is judged as that PR, not waved through
        b.acked = None;
        b.pr = open_pr();
        assert_eq!(c(&b), 4);
        // 6 by ack, before anything else
        let mut b = base();
        b.acked = Some("gave up".into());
        b.review = review(1, true);
        assert_eq!(c(&b), 6);
        // a clash with another open PR is not a fix (IFACE-s79 r11 3): the class is what it
        // would be without the clash
        let mut b = base();
        b.pr = open_pr();
        b.conflicts_with = vec![(745, vec!["a.rs".into()])];
        assert_eq!(c(&b), 4);
        // 3: the open PR's checks are red
        let mut b = base();
        b.pr = open_pr();
        if let Some(p) = b.pr.as_mut() {
            p.checks = Some("FAILURE".into());
            p.red_checks = Some(vec!["rust (macos)".into()]);
        }
        assert_eq!(classify(&b, 72).2, "PR #9 checks FAILURE: rust (macos)");
        let mut b = base();
        b.review = review(1, true);
        assert_eq!(c(&b), 3);
        let mut b = base();
        b.receipt = receipt(true);
        assert_eq!(c(&b), 3);
        // a receipt cut off (no HEAD-END, or no rc line at all) is never green (043dd M1)
        for cut in [
            |r: &mut Receipt| r.complete = false,
            |r: &mut Receipt| r.steps.clear(),
        ] {
            let mut b = base();
            b.review = review(0, true);
            b.receipt = receipt(false);
            b.pushed_to = vec!["origin/x".into()];
            b.pr = open_pr();
            if let Some(r) = b.receipt.as_mut() {
                cut(r);
                assert!(!r.green());
            }
            let (class, _, why) = classify(&b, 72);
            assert_eq!(
                (class, why.starts_with("receipt incomplete")),
                (3, true),
                "{why}"
            );
        }
        // a review on an older commit says nothing about the head
        let mut b = base();
        b.review = review(1, false);
        assert_eq!(c(&b), 4);
        // 8: reviewed + green + pushed + PR open, the checks not read or still running;
        // 1 once every check is green, whatever other open PR it clashes with (r11 2, 3)
        let mut b = base();
        b.review = review(0, true);
        b.receipt = receipt(false);
        b.pushed_to = vec!["origin/x".into()];
        b.pr = open_pr();
        assert_eq!(
            classify(&b, 72),
            (
                8,
                "waiting_ci",
                "reviewed + green at head, PR open; checks not read".into()
            )
        );
        if let Some(p) = b.pr.as_mut() {
            p.checks = Some("PENDING".into());
        }
        assert_eq!(c(&b), 8);
        if let Some(p) = b.pr.as_mut() {
            p.checks = Some("SUCCESS".into());
        }
        b.conflicts_with = vec![(745, vec!["a.rs".into()])];
        assert_eq!(c(&b), 1);
        b.conflicts_with.clear();
        // 2: same but not pushed, or no PR
        b.pr = None;
        assert_eq!(
            classify(&b, 72).2,
            "review / receipt at head but no open PR"
        );
        b.pushed_to.clear();
        assert_eq!(
            classify(&b, 72).2,
            "review / receipt at head but not pushed"
        );
        // 6 by age, 4 otherwise
        let mut b = base();
        b.age_h = 73;
        assert_eq!(c(&b), 6);
        b.pr = open_pr();
        assert_eq!(c(&b), 4);
        assert!(blocks(1) && blocks(2) && blocks(3) && !blocks(4) && !blocks(5) && !blocks(6));
        // a PR waiting for CI still holds the gate (5d33a L3); superseded_by_pr does not
        assert!(blocks(8) && !blocks(7));
    }

    /// s33 (Andy 11-14 11:38): a PR closed without merge at the branch's head is 「可放棄」,
    /// class 6, never a to-do; work after the close, a merged PR, an unread head or nothing
    /// left of its own is not.
    #[test]
    fn a_branch_whose_pr_was_closed_at_its_head_is_abandonable() {
        let closed = |merged: bool, head: Option<String>| {
            Some(PrInfo {
                number: 506,
                open: false,
                merged,
                head,
                ..PrInfo::default()
            })
        };
        let mut b = base();
        // a receipt at head that is not green would otherwise make it class 3 (fix)
        b.receipt = receipt(true);
        b.pr = closed(false, Some(b.head.clone()));
        assert_eq!(
            abandonable(&b),
            Some(506),
            "reason: closed_at_head_not_abandonable"
        );
        let (class, name, why) = classify(&b, 24);
        assert_eq!((class, name), (6, "stale"), "{why}");
        assert!(why.starts_with("abandonable: PR #506"), "{why}");
        // went on after the close
        b.pr = closed(false, Some("d".repeat(40)));
        assert_eq!(
            abandonable(&b),
            None,
            "reason: work_after_close_abandonable"
        );
        assert_eq!(classify(&b, 24).0, 3);
        // the closed head was not read
        b.pr = closed(false, None);
        assert_eq!(abandonable(&b), None, "reason: unread_head_abandonable");
        // merged, or open
        b.pr = closed(true, Some(b.head.clone()));
        assert_eq!(abandonable(&b), None, "reason: merged_abandonable");
        b.pr = open_pr();
        assert_eq!(abandonable(&b), None, "reason: open_abandonable");
        // nothing of its own left outside open PRs: carried by them (7), not abandonable;
        // nothing left outside main: merged (5)
        b.pr = closed(false, Some(b.head.clone()));
        b.unique_not_in_prs = 0;
        assert_eq!(abandonable(&b), None, "reason: merged_branch_abandonable");
        assert_eq!(classify(&b, 24).0, 7);
        b.unique = 0;
        assert_eq!(classify(&b, 24).0, 5);
    }

    /// IFACE-s79 r11 5: the receipt files of one head are one receipt.
    #[test]
    fn a_green_rerun_clears_an_older_red_and_split_files_add_up() {
        let one = |file: &str, steps: &[&str], red: &[&str], complete: bool| Receipt {
            head: "c".repeat(40),
            file: file.into(),
            nonzero: red.iter().map(|s| (*s).to_owned()).collect(),
            at_head: false,
            steps: steps.iter().map(|s| (*s).to_owned()).collect(),
            sealed: None,
            complete,
            stacked_at: None,
        };
        assert_eq!(receipts_together(&[]), None);
        // newest first: the rerun is green, the older run of the same step was red
        let got = receipts_together(&[
            one("b", &["test"], &[], true),
            one("a", &["test"], &["test"], true),
        ])
        .expect("one receipt");
        assert!(got.green(), "{got:?}");
        assert_eq!(got.file, "b");
        // split: the newest part did not finish; the older part ran other steps and finished
        let got = receipts_together(&[
            one("docs", &["docs_only"], &[], false),
            one("ws", &["fmt", "test"], &[], true),
        ])
        .expect("one receipt");
        assert!(got.green() && got.complete, "{got:?}");
        assert_eq!(got.steps, ["docs_only", "fmt", "test"]);
        assert_eq!(got.file, "docs (+1)");
        // a step only the older file ran, red there: still red
        let got = receipts_together(&[
            one("docs", &["docs_only"], &[], true),
            one("ws", &["test"], &["test"], true),
        ])
        .expect("one receipt");
        assert_eq!(got.nonzero, ["test"]);
        // a broken seal counts when that file was used, not when it added nothing
        let mut torn = one("old", &["test"], &[], true);
        torn.sealed = Some(false);
        let unused = receipts_together(&[one("new", &["test"], &[], true), torn.clone()]);
        assert_eq!(unused.and_then(|r| r.sealed), None);
        let used = receipts_together(&[one("new", &["fmt"], &[], true), torn]);
        assert_eq!(used.and_then(|r| r.sealed), Some(false));
    }

    /// S79-anc 5 (519d0 11-16, #809): lin's whole run at the head was green, andymini's older
    /// one red with summary keys lin's run did not name; the head read red.
    #[test]
    fn a_newer_whole_green_run_closes_the_older_red_ones() {
        let r = |file: &str, t: &str| Receipt {
            head: "c".repeat(40),
            file: file.into(),
            nonzero: receipt_nonzero(t),
            at_head: true,
            steps: receipt_steps(t),
            sealed: None,
            complete: true,
            stacked_at: None,
        };
        let andymini = "RC fmt=0\nRC test=101\nRC knives=SKIPPED_BUILD_FAILED(clippy=0 test=101)\n\
                        KNIVES-RC 1\nMISSES-COUNT 1\nHEAD-END x\n";
        let lin = "RC fmt=0\nRC test=0\nRC knife_SG1=101 (want non-0)\nRC restored=0\n\
                   KNIVES-RC 0\nMISSES-COUNT 0\nHEAD-END x\n";
        let got = receipts_together(&[r("lin", lin), r("andymini", andymini)]).expect("one");
        assert!(got.green(), "{got:?}");
        assert_eq!(got.file, "lin");
        // an older run cut short (a key a green run never names) is closed too
        let cut = "RC fmt=0\nRECEIPT-RED knives incomplete\nHEAD-END x\n";
        let got = receipts_together(&[r("lin", lin), r("cut", cut)]).expect("one");
        assert!(got.green(), "{got:?}");
        // a newer red is a finding, never closed by an older green
        let got = receipts_together(&[r("andymini", andymini), r("lin", lin)]).expect("one");
        assert!(!got.green(), "{got:?}");
        // one part of a split run (no knife summary) closes nothing
        let ws = "RC fmt=0\nRC test=0\nHEAD-END x\n";
        let got = receipts_together(&[r("ws", ws), r("cut", cut)]).expect("one");
        assert_eq!(got.nonzero, ["receipt_red"]);
        // 5d33a 273bdce9 M1: a run given no knife file prints KNIVES-RC 0 too; it closes no
        // knife result of an older run
        let no_knives =
            "RC fmt=0\nRC test=0\nKNIVES-NOT-RUN (no knife-file; knives are left to CI)\n\
                         KNIVES-RC 0\nBITES-COUNT 0\nMISSES-COUNT 0\nHEAD-END x\n";
        assert!(!r("n", no_knives).whole_green());
        let got = receipts_together(&[r("n", no_knives), r("andymini", andymini)]).expect("one");
        assert_eq!(got.nonzero, ["knives_rc", "misses"], "{got:?}");
    }

    #[test]
    fn receipts_are_known_by_name_with_out_files_and_odd_names() {
        for yes in [
            "TEST-x-1ced7-abcdef012.txt",
            "RECEIPT-664-ws-ebb36-3c925f52.out",
            "RECEIPT-x.log",
            "666-receipt-738745fdb-full.out",
        ] {
            assert!(is_receipt_name(yes), "{yes}");
        }
        for no in [
            "REVIEW-x-1ced7-abcdef01.md",
            "RECEIPT-x.md",
            "notes-receipt.txt",
            "666-receipt-short.out",
            "RELAY-dm-dab9-1114-161302-34799.txt",
        ] {
            assert!(!is_receipt_name(no), "{no}");
        }
    }

    #[test]
    fn python_files_are_listed() {
        let f: Vec<String> = [
            "a/b.py", "c.rs", "d/E.PY", "f.pyc", "py/g.txt", "h.pyi", "i.pyw",
        ]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
        assert_eq!(py_files(&f), ["a/b.py", "d/E.PY", "h.pyi", "i.pyw"]);
        // 043dd L1: a deleted .py still counts for the gate list, not as Python carried
        let (all, kept) = diff_names("M\ta/b.py\nD\told.py\nA\tc.rs\n");
        assert_eq!(all, ["a/b.py", "old.py", "c.rs"]);
        assert_eq!(py_files(&kept), ["a/b.py"]);
    }

    /// IFACE-s79 r10 3: the range text of a review says what it covers.
    #[test]
    fn the_scope_of_a_review_is_read_from_its_range() {
        let head = format!("ab12cd3{}", "0".repeat(33));
        let line = |range: &str| format!("結論: 通過 頭: {head} H/M/L/N: 0/0/0/0 範圍: {range}");
        let plain = "REVIEW-x-043dd-ab12cd30.md";
        assert_eq!(review_scope(&line("全文"), &head, plain), Scope::Whole);
        assert_eq!(
            review_scope(&line("增量 1234567..ab12cd3(兩個 commit)"), &head, plain),
            Scope::From("1234567".into())
        );
        // the right side is not the reviewed head, or the left side is too short: not a range
        assert_eq!(
            review_scope(&line("1234567..fffffff"), &head, plain),
            Scope::Whole
        );
        assert_eq!(
            review_scope(&line("123456..ab12cd3"), &head, plain),
            Scope::Whole
        );
        assert_eq!(review_scope(&line("增量"), &head, plain), Scope::Increment);
        assert_eq!(
            review_scope(&line("第 3 條"), &head, "REVIEW-x-inc-043dd-ab12cd30.md"),
            Scope::Increment
        );
        assert_eq!(Scope::From("1234567".into()).text(), "from:1234567");
    }

    fn part(head: &str, scope: Scope, reach: &[&str]) -> Part {
        Part {
            head: head.into(),
            file: format!("REVIEW-{head}.md"),
            scope,
            merge: false,
            reach: reach.iter().map(|s| (*s).to_owned()).collect(),
            from: None,
        }
    }

    /// IFACE-s79 r10 3: what the passed reviews under a head cover together.
    #[test]
    fn passed_reviews_under_a_head_are_added_up() {
        let commits: Vec<String> = ["c3", "c2", "c1"].iter().map(|s| (*s).to_owned()).collect();
        let whole1 = part("c1", Scope::Whole, &["c1"]);
        let inc3 = part("c3", Scope::Increment, &["c3", "c2", "c1"]);
        // a whole review of the first slice and an increment on top: everything is covered
        let got = review_parts(&commits, &[inc3.clone(), whole1.clone()], &[]);
        assert!(got.ok(), "{got:?}");
        assert_eq!(got.files, ["REVIEW-c3.md", "REVIEW-c1.md"]);
        // the increment alone, nothing reviewed under it: only its own commit
        let got = review_parts(&commits, std::slice::from_ref(&inc3), &[]);
        assert_eq!(got.uncovered, ["c2", "c1"]);
        assert!(!got.ok());
        // a named range covers that range; one that could not be read, the commit alone
        let mut from3 = part("c3", Scope::From("c1".into()), &["c3", "c2", "c1"]);
        from3.from = Some(["c3", "c2"].iter().map(|s| (*s).to_owned()).collect());
        assert_eq!(
            review_parts(&commits, std::slice::from_ref(&from3), &[]).uncovered,
            ["c1"]
        );
        from3.from = None;
        assert_eq!(
            review_parts(&commits, &[from3], &[]).uncovered,
            ["c2", "c1"]
        );
        // a review at a merge covers the merge, not what is under it
        let mut at_merge = part("m", Scope::Whole, &["m", "c3", "c2", "c1"]);
        at_merge.merge = true;
        let hand = Resolved {
            commit: "m".into(),
            parent: "p".into(),
            from_main: true,
            files: vec!["Cargo.toml".into()],
        };
        let got = review_parts(
            &commits,
            std::slice::from_ref(&at_merge),
            std::slice::from_ref(&hand),
        );
        assert_eq!(got.uncovered, ["c3", "c2", "c1"]);
        assert_eq!(got.files, ["REVIEW-m.md"]);
        // a merge resolved by hand that nobody reviewed
        let got = review_parts(
            &commits,
            &[inc3.clone(), whole1.clone()],
            std::slice::from_ref(&hand),
        );
        assert_eq!(got.unreviewed_merges, ["m"]);
        assert!(!got.ok());
        // … and reviewed: the whole head counts
        assert!(review_parts(&commits, &[at_merge, inc3, whole1], &[hand]).ok());
        // no passed review at all is not "everything covered"
        assert!(!review_parts(&[], &[], &[]).ok());
    }

    /// IFACE-s79 r10 3: such a head is class 1 / 8 like one reviewed at its head.
    #[test]
    fn a_head_covered_by_passed_reviews_counts_as_reviewed() {
        let mut b = base();
        b.review = review(0, false);
        b.receipt = receipt(false);
        b.pushed_to = vec!["origin/x".into()];
        b.pr = open_pr();
        if let Some(p) = b.pr.as_mut() {
            p.checks = Some("SUCCESS".into());
        }
        assert!(!b.reviewed());
        assert_ne!(classify(&b, 72).0, 1);
        b.review_parts = Some(ReviewParts {
            files: vec!["R".into()],
            ..ReviewParts::default()
        });
        assert!(b.reviewed());
        assert_eq!(classify(&b, 72).0, 1);
        b.review_parts = Some(ReviewParts {
            files: vec!["R".into()],
            uncovered: vec!["c1".into()],
            ..ReviewParts::default()
        });
        assert!(!b.reviewed());
        assert_ne!(classify(&b, 72).0, 1);
    }

    /// f1fae COMPARE r2 1: an open PR that conflicts with main is a fix (merge main), not a
    /// wait for its checks; without a PR the branch is judged as before.
    #[test]
    fn an_open_pr_that_conflicts_with_main_merges_main_first() {
        let mut b = base();
        b.review = review(0, true);
        b.receipt = receipt(false);
        b.pushed_to = vec!["origin/x".into()];
        b.pr = open_pr();
        b.conflicts_with_main = Some(Clash {
            conflict: true,
            files: vec!["Cargo.toml".into()],
        });
        b.conflicts_with = vec![
            (755, vec!["Cargo.toml".into()]),
            (760, vec!["other.rs".into()]),
        ];
        assert_eq!(
            classify(&b, 72),
            (
                3,
                "fix",
                "conflicts with main: Cargo.toml; open PRs on the same files: #755".into()
            )
        );
        // no conflict: waiting for its checks, as before
        b.conflicts_with_main = Some(Clash::default());
        assert_eq!(classify(&b, 72).0, 8);
        // a conflict and no PR: not this rule
        b.conflicts_with_main = Some(Clash {
            conflict: true,
            files: vec!["Cargo.toml".into()],
        });
        b.pr = None;
        assert_eq!(classify(&b, 72).0, 2);
    }

    /// #755: the merge queue's required cells (mergewatch.sh) and the workflow size limit.
    #[test]
    fn required_cells_follow_the_merge_queue_and_big_workflows_are_found() {
        let f = |v: &[&str]| v.iter().map(|s| (*s).to_owned()).collect::<Vec<String>>();
        assert!(required_cells(&f(&["docs/a.md", "aib/src/x.sh"]), None).is_empty());
        for touch in [
            "crates/a/src/l.rs",
            "Cargo.toml",
            "Cargo.lock",
            ".github/workflows/aibd.yml",
        ] {
            assert_eq!(
                required_cells(&f(&[touch]), None),
                f(&RUST_CELLS),
                "{touch}"
            );
        }
        assert!(required_cells(&f(&[".github/workflows/ci.yml"]), None).is_empty());
        let aibd = "jobs:\n  d:\n    name: cloud decision knives (macOS)\n  x:\n    name: other\n";
        assert_eq!(
            required_cells(&f(&["Cargo.lock"]), Some(aibd)),
            f(&[RUST_CELLS[0], RUST_CELLS[1], CLOUD_CELLS[0]])
        );
        let ls = "100644 blob aa   495000\t.github/workflows/a.yml\n100644 blob bb   495001\t.github/workflows/b.yml\n";
        assert_eq!(
            too_big(ls, "head"),
            vec![(
                ".github/workflows/b.yml".to_owned(),
                495_001,
                "head".to_owned()
            )]
        );
    }

    #[test]
    fn a_missing_required_cell_or_a_big_workflow_is_a_fix() {
        let mut b = Branch {
            branch: "feat/x-1ced7-1115".into(),
            unique: 1,
            unique_not_in_prs: 1,
            pr: Some(PrInfo {
                number: 755,
                open: true,
                checks: Some("MISSING".into()),
                missing_checks: Some(vec![RUST_CELLS[0].to_owned()]),
                ..PrInfo::default()
            }),
            ..Branch::default()
        };
        assert_eq!(
            classify(&b, 72),
            (
                3,
                "fix",
                "PR #755 required checks never created: rust (windows-latest)".to_owned()
            )
        );
        b.workflow_too_big = vec![(".github/workflows/aibd.yml".into(), 508_091, "head".into())];
        let (c, _, why) = classify(&b, 72);
        assert_eq!(c, 3);
        assert!(why.starts_with("workflow file over"), "{why}");
    }

    /// 519d0 11-15, the real feat/drive-d1c5-ebb36-1115 of slice-sweep.json 10:10: no open PR,
    /// its red receipt carried by the green merge 7ff6353e, aibd.yml 507,422 at head. Absorbed
    /// comes first: it is never pushed itself, the workflow size is that head's matter.
    #[test]
    fn an_absorbed_branch_is_in_progress_before_its_workflow_size() {
        let head = "a362e16889f4e3ff67a0c588f98565b4738187d0";
        let mut b = Branch {
            branch: "feat/drive-d1c5-ebb36-1115".into(),
            head: head.into(),
            unique: 54,
            unique_not_in_prs: 12,
            in_prs: vec![769],
            receipt: Some(Receipt {
                head: head.into(),
                file: "RECEIPT-d1c5-ebb36-a362e1688.out".into(),
                nonzero: ["clippy", "winclippy", "knives_rc", "misses"]
                    .map(String::from)
                    .to_vec(),
                at_head: true,
                steps: [
                    "fmt",
                    "anchors",
                    "clippy",
                    "winclippy",
                    "test",
                    "knives_rc",
                    "misses",
                ]
                .map(String::from)
                .to_vec(),
                sealed: Some(true),
                complete: true,
                stacked_at: None,
            }),
            absorbed_into: Some((
                "7ff6353edc873485deb4784490bc2ae4b6934e51".into(),
                "RECEIPT-c7r-ebb36-7ff6353ed.out".into(),
            )),
            workflow_too_big: vec![(".github/workflows/aibd.yml".into(), 507_422, "head".into())],
            ..Branch::default()
        };
        let (c, name, why) = classify(&b, 72);
        assert_eq!((c, name), (4, "in_progress"), "{why}");
        assert!(why.contains("7ff6353e"), "{why}");
        // 5d33a 254a579d L1: an unfixed H/M review at head is still a fix when absorbed
        b.review = review(1, true);
        assert_eq!(
            classify(&b, 72),
            (
                3,
                "fix",
                "review at head has H/M 1/0 (content absorbed into 7ff6353e, fix it there)"
                    .to_owned()
            )
        );
        b.review = None;
        // not absorbed: the size decides again
        b.absorbed_into = None;
        assert_eq!(classify(&b, 72).0, 3);
    }
}
