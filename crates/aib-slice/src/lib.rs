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

pub mod gatecontent;
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
    /// SUCCESS | FAILURE | PENDING rolled up from the head's check runs; None = not read / none
    pub checks: Option<String>,
    /// failed check run names; None = not read (never "none red")
    pub red_checks: Option<Vec<String>>,
    /// label names on the PR (`not-gate`, …)
    pub labels: Vec<String>,
    /// head commit of a closed PR as GitHub gives it; None = not read (open PRs: see OpenPr)
    pub head: Option<String>,
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
    /// non-merge commits between the reviewed head and the branch head
    pub unreviewed_after: Vec<String>,
    /// the reviewing seat, from the file name (`REVIEW-<topic>-<seat>-<sha8>.md`)
    pub reviewer: Option<String>,
    /// a `非閘理由: …` line in the review (S73j: why a new gate may merge as a non-gate)
    pub not_gate_reason: Option<String>,
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
}

/// A merge-tree result: did it conflict, and which files.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Clash {
    pub conflict: bool,
    pub files: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Branch {
    pub branch: String,
    pub seat: Option<String>,
    pub head: String,
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
}

#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub generated_at: i64,
    pub main: String,
    pub errors: Vec<String>,
    pub open_prs: Vec<OpenPr>,
    pub branches: Vec<Branch>,
}

/// The six classes of DESIGN-S73 (number, name). Checked top to bottom.
pub fn classify(b: &Branch, hours: i64) -> (u8, &'static str, String) {
    let pr_open = b.pr.as_ref().is_some_and(|p| p.open);
    let review_at = b.review.as_ref().filter(|r| r.at_head);
    let receipt_at = b.receipt.as_ref().filter(|r| r.at_head);
    if b.unique_not_in_prs == 0 {
        let why = if b.unique == 0 {
            "all in main".to_owned()
        } else {
            format!("all in open PR {}", nums(&b.in_prs))
        };
        return (5, "merged", why);
    }
    if let Some(a) = &b.acked {
        return (6, "stale", format!("ack: {a}"));
    }
    if let Some(n) = abandonable(b) {
        return (
            6,
            "stale",
            format!("abandonable: PR #{n} was closed without merge at this head"),
        );
    }
    if pr_open && !b.conflicts_with.is_empty() {
        let prs: Vec<u64> = b.conflicts_with.iter().map(|c| c.0).collect();
        return (3, "fix", format!("conflicts with open PR {}", nums(&prs)));
    }
    if let Some(p) =
        b.pr.as_ref()
            .filter(|p| p.open && p.checks.as_deref() == Some("FAILURE"))
    {
        let red = p.red_checks.as_deref().unwrap_or_default().join(", ");
        return (3, "fix", format!("PR #{} checks FAILURE: {red}", p.number));
    }
    if let Some(r) = review_at.filter(|r| r.h + r.m > 0) {
        return (3, "fix", format!("review at head has H/M {}/{}", r.h, r.m));
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
    let reviewed = review_at.is_some_and(Review::passed);
    let green = receipt_at.is_some_and(Receipt::green);
    if reviewed && green && !b.pushed_to.is_empty() && b.conflicts_with.is_empty() && pr_open {
        return (
            1,
            "merge_first",
            "reviewed + green at head, PR open".to_owned(),
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
    matches!(class, 1..=3)
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
fn rc_keys(text: &str) -> Vec<(String, bool)> {
    let mut out: Vec<(String, bool)> = Vec::new();
    for line in text.lines() {
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
                if key.is_empty() || key.starts_with("knife_") {
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
                if !out.iter().any(|(k, _)| k == key) {
                    out.push((key.to_owned(), red));
                }
            }
        }
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
                })),
                "review": opt(&b.review, |x| json!({
                    "head": x.head, "verdict": x.verdict, "h": x.h, "m": x.m, "l": x.l, "n": x.n,
                    "file": x.file, "at_head": x.at_head, "unreviewed_after": x.unreviewed_after,
                })),
                "receipt": opt(&b.receipt, |x| json!({
                    "head": x.head, "file": x.file, "nonzero": x.nonzero, "green": x.green(),
                    "at_head": x.at_head, "steps": x.steps, "sealed": x.sealed,
                    "complete": x.complete,
                })),
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
                        reviewer: Some("043dd".into()),
                        not_gate_reason: Some("只改註解".into()),
                    }),
                    receipt: Some(Receipt {
                        head: "c".repeat(40),
                        file: "TEST-x.txt".into(),
                        nonzero: Vec::new(),
                        at_head: true,
                        steps: vec!["fmt_rc".into()],
                        sealed: Some(true),
                        complete: true,
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
            reviewer: None,
            not_gate_reason: None,
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
        // 5: nothing left outside main / open PRs, even if old or acked
        let mut b = base();
        b.unique_not_in_prs = 0;
        b.in_prs = vec![745];
        b.acked = Some("x".into());
        assert_eq!(
            classify(&b, 72),
            (5, "merged", "all in open PR #745".into())
        );
        // 6 by ack, before anything else
        let mut b = base();
        b.acked = Some("gave up".into());
        b.review = review(1, true);
        assert_eq!(c(&b), 6);
        // 3: conflict on an open PR / H at head / red receipt at head
        let mut b = base();
        b.pr = open_pr();
        b.conflicts_with = vec![(745, vec!["a.rs".into()])];
        assert_eq!(classify(&b, 72).2, "conflicts with open PR #745");
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
        // 1: reviewed + green + pushed + PR open + no conflict
        let mut b = base();
        b.review = review(0, true);
        b.receipt = receipt(false);
        b.pushed_to = vec!["origin/x".into()];
        b.pr = open_pr();
        assert_eq!(c(&b), 1);
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
        // nothing of its own left outside main / open PRs: merged, not abandonable
        b.pr = closed(false, Some(b.head.clone()));
        b.unique_not_in_prs = 0;
        assert_eq!(abandonable(&b), None, "reason: merged_branch_abandonable");
        assert_eq!(classify(&b, 24).0, 5);
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
}
