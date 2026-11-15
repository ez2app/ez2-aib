//! `aib-drive next`, layers 1–4 (S79 n-1; DEV-S79-drive-next §2.1, IFACE-s79-drive r2 §2):
//! what can be done now, best first, read only. Nothing here acts and nothing here judges
//! whether a PR can merge: that is aib-slice's (spec V1.1 §8.3). This module orders, picks
//! who, and says why.
//!
//! - Layer 1 merge / push (`aib-slice due` merge items), 2 fix (due fix items, and a
//!   receipt missing or behind), 3 review (`aib-slice sweep`: an open PR without a review at
//!   its head; the reviewer picked from the seat facts), 4 collect (sweep: merged branches
//!   still on a worktree).
//! - In a layer: the row that unblocks more others first, then the one that waited longer,
//!   then the lower PR number. A reviewer whose own branch waits for a review is picked
//!   first (a review swap is faster than a new slice).
//! - A source that is not complete is never read as "nothing there": the rows it would give
//!   go to `unknown`. A row for a seat whose facts are not read, or unknown, goes to
//!   `unknown` too: a seat not known to be free is not dispatched to. Seat facts that are
//!   read but not complete are used seat by seat (each seat's `dispatchable` is three-state,
//!   so an unread seat is `unknown`, never `yes`); the seats source is then `partial` and the
//!   answer not complete (b433b M1, REVIEW-drive-next-b433b-f106a7f8).
//! - Reviews are spread: a seat already given a review this round goes after the seats
//!   given fewer (b433b L1).
//! - `waited_min` of layers 3–4 is the age of the head commit (sweep `age_h`, from
//!   `last_commit_at`), said in `why_now`; a collect row is never starving (b433b L2).
//! - Never in `rows`: an abandonable branch (keep or drop is a human's call), what only
//!   waits (CI queued or running, a PR below, a new gate's turn).
//!
//! Layers 5–7 (`next_local.rs`, ebb36) and the seat facts (`seats.rs`, b433b) are other
//! slices; the structs below are the parts of their JSON this module reads, kept local
//! until the integration joins them.

use crate::next_local::{self, Dep, LocalInput, LocalOut, MergedSource};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;

/// `schema` of the JSON answer.
pub const SCHEMA: &str = "aib-drive.next.v1";
/// The schemas read.
pub const DUE_SCHEMA: &str = "aib-slice-due.v1";
pub const SWEEP_SCHEMA: &str = "aib-slice-sweep.v1";
pub const SEATS_SCHEMA: &str = "aib-drive.seats.v1";

/// How big a gap a seat has / a row needs (IFACE r3): a seat takes a row whose size is at
/// most its own. Layers 1–4 need a `review`-sized gap.
pub const SIZES: [&str; 4] = ["none", "review", "slice", "mainline"];
/// A row that waited longer than this is marked `starving` for the coordinator (DEV-S79
/// §2.1 防餓死; the first version only marks, it does not reweigh).
pub const STARVE_MIN: u64 = 240;

/// The size every row of layers 1–4 needs.
pub const ROW_SIZE: &str = "review";

/// The order of a size in [`SIZES`]; an unknown word is `none` (fits nothing).
pub fn size_rank(s: &str) -> usize {
    SIZES.iter().position(|x| *x == s).unwrap_or(0)
}

/// Lacks of a due merge item the author fixes by pushing, opening the PR or labelling it.
const PUSH_LACKS: [&str; 3] = ["no_pr", "not_pushed", "no_ready_merge"];
/// Lacks the author fixes by running a receipt on the head.
const RECEIPT_LACKS: [&str; 2] = ["no_receipt", "receipt_behind"];

/// One lack or problem of a due item (IFACE-s73l r1 §7.6).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Gap {
    pub kind: String,
    #[serde(default)]
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DueItem {
    pub target: String,
    pub pr: Option<u64>,
    pub branch: String,
    pub seat: Option<String>,
    pub head: String,
    #[serde(default)]
    pub lacks: Vec<Gap>,
    #[serde(default)]
    pub problems: Vec<Gap>,
    /// Unix seconds it has waited since.
    #[serde(default)]
    pub since: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DueList {
    #[serde(default)]
    pub items: Vec<DueItem>,
}

/// `aib-slice due --all --json`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Due {
    pub schema: String,
    #[serde(default)]
    pub generated_at: u64,
    pub complete: bool,
    #[serde(default)]
    pub errors: Vec<String>,
    pub merge: DueList,
    pub fix: DueList,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SweepPr {
    pub number: u64,
    pub state: String,
    #[serde(default)]
    pub merged: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SweepReview {
    pub at_head: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Branch {
    pub branch: String,
    pub seat: Option<String>,
    pub head: String,
    #[serde(default)]
    pub age_h: u64,
    pub pr: Option<SweepPr>,
    pub review: Option<SweepReview>,
    /// aib-slice's own verdict: a passing review at the head, or one on an older head with
    /// only carried merges after it (`review_parts.ok`, IFACE). `None` = an aib-slice that
    /// does not write it yet; then `review.at_head` is read instead.
    #[serde(default)]
    pub reviewed: Option<bool>,
    #[serde(default)]
    pub stacked_on: Vec<String>,
    #[serde(default)]
    pub worktree: Option<String>,
    pub class: u8,
    #[serde(default)]
    pub abandonable: bool,
    /// The worktree has changed, staged or untracked files; `None` = not read.
    #[serde(default)]
    pub dirty: Option<bool>,
    /// The local branch of this name is on another commit than `head` (commits not in the
    /// row, maybe not pushed); `None` = at `head` or no local branch.
    #[serde(default)]
    pub local_head: Option<String>,
}

/// `aib-slice sweep --json`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Sweep {
    pub schema: String,
    #[serde(default)]
    pub generated_at: u64,
    pub complete: bool,
    #[serde(default)]
    pub errors: Vec<String>,
    #[serde(default)]
    pub branches: Vec<Branch>,
}

/// What one seat's facts say for dispatch (`aib-drive.seats.v1`, IFACE §1).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SeatFact {
    pub seat: String,
    /// `yes` / `no` / `unknown`.
    pub dispatchable: String,
    /// `review` / `slice` / `mainline` / `none`.
    pub size: String,
    /// `dev` / `coordinator`: a coordinator is not picked as a reviewer.
    #[serde(default)]
    pub role: String,
    /// `capacity.unread` written as null = not known: the seat is never `to` (IFACE r6).
    /// A row without `capacity` says nothing either way.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capacity: Option<Capacity>,
}

/// The part of a seat's `capacity` read here.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Capacity {
    #[serde(default)]
    pub unread: Option<u64>,
    /// Plan nodes reviewed and not pushed (seats' `wip_nodes`).
    /// `null` = the plan was not read (seats writes null then): no push row, and the seat
    /// facts still read (ebb36 211ec156 M1).
    #[serde(default, deserialize_with = "null_as_empty")]
    pub wip_nodes: Vec<String>,
    /// Open PRs under the seat; null = not known.
    #[serde(default)]
    pub open_prs: Option<u64>,
}

/// `null` read as empty: seats writes `"wip_nodes": null` when the plan was not read, and
/// `serde(default)` alone covers a missing field only (ebb36 211ec156 M1).
fn null_as_empty<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Vec<String>, D::Error> {
    let v: Option<Vec<String>> = Deserialize::deserialize(d)?;
    Ok(v.unwrap_or_default())
}

impl SeatFact {
    /// `capacity.unread` is null: how many dms wait for it is not known (IFACE r6).
    pub fn unread_unknown(&self) -> bool {
        self.capacity.as_ref().is_some_and(|c| c.unread.is_none())
    }
}

/// `aib-drive seats --json`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Seats {
    pub schema: String,
    pub complete: bool,
    pub seats: Vec<SeatFact>,
    /// Limited to the plan's roster ([`on_roster`]): every roster seat is in `seats`, so a
    /// seat not in it is off the roster (an old or another project's seat), not unread.
    #[serde(skip)]
    pub on_roster: bool,
}

/// One input of the answer: `ok` / `partial` (read, but its scan was not complete) /
/// `failed` (not read).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Source {
    pub name: String,
    pub state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub why: Option<String>,
}

impl Source {
    pub fn ok(&self) -> bool {
        self.state == "ok"
    }
}

/// One thing to do now.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Row {
    pub rank: usize,
    pub layer: u8,
    /// `merge` / `push` / `fix` / `receipt` / `review` / `collect`.
    pub kind: String,
    pub what: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pr: Option<u64>,
    /// The head it is about (40 hex): act's dedup key (IFACE r1 §1).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub head: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub node: Option<String>,
    /// Who does it; `None` = the merge watch, or no free reviewer.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
    pub why_him: String,
    pub why_now: String,
    pub unblocks: usize,
    pub waited_min: u64,
    /// Waited over [`STARVE_MIN`] minutes.
    #[serde(default)]
    pub starving: bool,
    /// `none`, or what is missing before it can be done (`seat_busy`, `seat_size_<size>`,
    /// `no_free_reviewer`).
    pub needs: String,
    /// The gap it needs ([`SIZES`]; layers 1–4: [`ROW_SIZE`]).
    pub size: String,
    pub data_at: String,
    /// Layer 5: the node was approved without a `scope` (IFACE r4 §C12); only when true.
    #[serde(default, skip_serializing_if = "is_false")]
    pub scope_unchecked: bool,
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// Something that could not be judged, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unknown {
    pub what: String,
    pub why: String,
}

/// Something seen and held back by a rule (IFACE r9: an idea without a node, a scope
/// changed after the approval, a seat whose unread is not known…). Unlike `unknown` it
/// leaves `complete` and the exit code alone.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Skipped {
    pub what: String,
    pub reason: String,
}

/// A branch aib-slice calls abandonable (s33: its PR closed unmerged at this head, with
/// commits in neither main nor an open PR). Keep or drop is a human's call, so it is listed
/// beside `for_andy`, never in `rows` (519d0 11-15 00:39, ACCEPT C4).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Abandonable {
    pub branch: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seat: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pr: Option<u64>,
    pub head: String,
    pub age_h: u64,
}

/// The answer of `aib-drive next` (layers 1–4; `next_local` adds its fields).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Next {
    pub schema: String,
    pub as_of: String,
    /// Every source `ok` and nothing in `unknown`.
    pub complete: bool,
    pub sources: Vec<Source>,
    pub rows: Vec<Row>,
    pub unknown: Vec<Unknown>,
    /// Held back by a rule, not unread (IFACE r9); not counted in `complete`.
    #[serde(default)]
    pub skipped: Vec<Skipped>,
    /// For a human to keep or drop; read from sweep only when its scan is complete.
    #[serde(default)]
    pub abandonable: Vec<Abandonable>,
    /// What holds the most unfinished nodes (next_local, layers 5–6 joined).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fleet_blocker: Option<serde_json::Value>,
    /// Needs-human ideas still pending, most nodes unblocked first (next_local).
    #[serde(default)]
    pub for_andy: Vec<serde_json::Value>,
}

impl Next {
    /// Exit code (IFACE §0): 0 every source read, 3 something could not be read.
    pub fn rc(&self) -> i32 {
        if self.complete {
            0
        } else {
            3
        }
    }

    /// Only the rows for `seat` (`--seat`); sources and unknowns stay, ranks restart at 1.
    pub fn for_seat(mut self, seat: &str) -> Next {
        self.rows.retain(|r| r.to.as_deref() == Some(seat));
        for (i, r) in self.rows.iter_mut().enumerate() {
            r.rank = i + 1;
        }
        self
    }
}

/// An input as read: the document, or why it could not be read.
pub type Input<T> = Result<T, String>;

fn source<T>(name: &str, input: &Input<T>, complete: impl Fn(&T) -> (bool, String)) -> Source {
    let (state, why) = match input {
        Ok(d) => match complete(d) {
            (true, _) => ("ok", None),
            (false, why) => ("partial", Some(why)),
        },
        // the reader said the file is older than it may be (aib-slice's cache, IFACE r5)
        Err(e) if e.starts_with("stale:") => ("stale", Some(e.clone())),
        Err(e) => ("failed", Some(e.clone())),
    };
    Source {
        name: name.into(),
        state: state.into(),
        why,
    }
}

/// A row before seats, order and rank.
struct Draft {
    layer: u8,
    kind: &'static str,
    what: String,
    pr: Option<u64>,
    head: String,
    to: Option<String>,
    why_now: String,
    branch: String,
    /// The branch's seat (never picked as its reviewer).
    author: Option<String>,
    waited_min: u64,
    /// The plan node, for a push row of a reviewed node (act's key, ebb36 211ec156 L1).
    node: Option<String>,
}

fn short(sha: &str) -> &str {
    sha.get(..8).unwrap_or(sha)
}

fn texts(g: &[Gap]) -> String {
    g.iter()
        .map(|g| {
            if g.text.is_empty() {
                g.kind.as_str()
            } else {
                g.text.as_str()
            }
        })
        .collect::<Vec<_>>()
        .join(";")
}

/// Layers 1–3 from due: a merge item with nothing lacking merges; one that lacks only a
/// push / PR / label goes to its author (1); one that lacks a receipt goes to its author
/// (2); one that lacks a review and otherwise only a push is a review (3: RULES §18, review
/// before the push, so a branch with no PR yet is reviewed too); one that only waits (CI,
/// a PR below, a new gate's turn) is no row. Every fix item goes to its author (2).
fn from_due(d: &Due, now: u64) -> Vec<Draft> {
    let waited = |since: u64| now.saturating_sub(since) / 60;
    let mut out = Vec::new();
    for i in &d.merge.items {
        let has = |set: &[&str]| i.lacks.iter().any(|l| set.contains(&l.kind.as_str()));
        let only = |set: &[&str]| i.lacks.iter().all(|l| set.contains(&l.kind.as_str()));
        let (layer, kind, to, why_now) = if i.lacks.is_empty() {
            (1, "merge", None, "審過、收據齊、CI 綠".to_string())
        } else if only(&PUSH_LACKS) {
            (
                1,
                "push",
                i.seat.clone(),
                format!("只差:{}", texts(&i.lacks)),
            )
        } else if has(&RECEIPT_LACKS) {
            (
                2,
                "receipt",
                i.seat.clone(),
                format!("差:{}", texts(&i.lacks)),
            )
        } else if has(&["no_review"])
            && i.lacks
                .iter()
                .all(|l| l.kind == "no_review" || PUSH_LACKS.contains(&l.kind.as_str()))
        {
            (3, "review", None, format!("差:{}", texts(&i.lacks)))
        } else {
            continue;
        };
        let what = match kind {
            "merge" => format!("{} 可合", i.target),
            "push" => format!("{} 推 / 開 PR / 貼標籤", i.target),
            "review" => format!("審 {} 到 {}", i.target, short(&i.head)),
            _ => format!("{} 跑同頭收據", i.target),
        };
        out.push(Draft {
            layer,
            kind,
            what,
            pr: i.pr,
            head: i.head.clone(),
            to,
            why_now,
            branch: i.branch.clone(),
            author: i.seat.clone(),
            waited_min: waited(i.since),
            node: None,
        });
    }
    for i in &d.fix.items {
        out.push(Draft {
            layer: 2,
            kind: "fix",
            what: format!("{} 修:{}", i.target, texts(&i.problems)),
            pr: i.pr,
            head: i.head.clone(),
            to: i.seat.clone(),
            why_now: "紅或衝突,修了才能往下".into(),
            branch: i.branch.clone(),
            author: i.seat.clone(),
            waited_min: waited(i.since),
            node: None,
        });
    }
    out
}

/// Layers 3–4 from sweep: an open PR whose head has no review (3, the reviewer is picked
/// later); a merged branch still on a worktree (4). Abandonable branches never, nor a
/// merged one that [`collect_hold`] holds back (C4 safe version).
fn from_sweep(s: &Sweep) -> Vec<Draft> {
    let mut out = Vec::new();
    for b in s.branches.iter().filter(|b| !b.abandonable) {
        let open = b.pr.as_ref().filter(|p| p.state == "open");
        // 519d0 07:33 (56cda on #773): a carried merge after the review is still reviewed
        let reviewed = b
            .reviewed
            .unwrap_or_else(|| b.review.as_ref().is_some_and(|r| r.at_head));
        if let Some(p) = open.filter(|_| !reviewed && b.class != 5) {
            out.push(Draft {
                layer: 3,
                kind: "review",
                what: format!("審 #{} {} 到 {}", p.number, b.branch, short(&b.head)),
                pr: Some(p.number),
                head: b.head.clone(),
                to: None,
                why_now: format!("頭上沒有非作者審查(頭 {} 小時前 commit)", b.age_h),
                branch: b.branch.clone(),
                author: b.seat.clone(),
                waited_min: b.age_h * 60,
                node: None,
            });
        } else if b.class == 5 && b.worktree.is_some() && collect_hold(b).is_none() {
            out.push(Draft {
                layer: 4,
                kind: "collect",
                what: format!("{} 已合,收工作樹", b.branch),
                pr: b.pr.as_ref().map(|p| p.number),
                head: b.head.clone(),
                to: b.seat.clone(),
                why_now: format!("已合進 main(頭 {} 小時前 commit)", b.age_h),
                branch: b.branch.clone(),
                author: b.seat.clone(),
                waited_min: b.age_h * 60,
                node: None,
            });
        }
    }
    out
}

/// Why a tree aib-slice calls merged is not to be collected (C4 safe version, 519d0
/// 06:5x): collecting it could lose work, so when in doubt it stays. Its PR still open
/// (aib-slice called #764 and #767 merged while they were open), files not committed or
/// not known to be clean, or a local branch on a commit the row does not have.
pub fn collect_hold(b: &Branch) -> Option<String> {
    if let Some(p) = b.pr.as_ref().filter(|p| p.state == "open") {
        return Some(format!("#{} PR 還開著", p.number));
    }
    match b.dirty {
        Some(false) => {}
        Some(true) => return Some(format!("{} 有沒 commit 的改動", b.branch)),
        None => return Some(format!("{} 樹乾不乾淨沒讀到", b.branch)),
    }
    b.local_head
        .as_ref()
        .map(|h| format!("{} 本機在 {}、有沒推的 commit", b.branch, short(h)))
}

/// The merged trees on a worktree that [`collect_hold`] holds back, each with its reason;
/// layer 4 names them in `skipped` instead of a collect row.
pub fn collect_held(s: &Sweep) -> Vec<String> {
    s.branches
        .iter()
        .filter(|b| !b.abandonable && b.class == 5 && b.worktree.is_some())
        .filter_map(collect_hold)
        .collect()
}

/// A collect row's group: its seat and its `needs`.
type CollectKey = (Option<String>, String);

/// Layer 4's collect rows for one seat with the same `needs` fold into one (519d0 T8: 28
/// rows of merged trees drowned the list): `what` names how many trees and the first
/// three; head and PR are dropped when there is more than one (no single key), the
/// longest wait is kept and `unblocks` added up. Other rows pass as they are; the layer
/// order does not change.
fn fold_collect(rows: Vec<(Row, bool)>) -> Vec<(Row, bool)> {
    let mut out: Vec<(Row, bool)> = Vec::new();
    let mut groups: Vec<(CollectKey, Vec<Row>)> = Vec::new();
    for (r, swap) in rows {
        if r.kind != "collect" {
            out.push((r, swap));
            continue;
        }
        let key = (r.to.clone(), r.needs.clone());
        match groups.iter_mut().find(|(k, _)| *k == key) {
            Some((_, g)) => g.push(r),
            None => groups.push((key, vec![r])),
        }
    }
    for (_, mut g) in groups {
        if g.len() == 1 {
            out.push((g.remove(0), false));
            continue;
        }
        g.sort_by(|a, b| b.waited_min.cmp(&a.waited_min).then(a.what.cmp(&b.what)));
        let n = g.len();
        let names: Vec<&str> = g
            .iter()
            .take(3)
            .map(|r| r.what.split(' ').next().unwrap_or_default())
            .collect();
        let more = if n > 3 { "…" } else { "" };
        let mut whys: Vec<&str> = Vec::new();
        for r in &g {
            if !whys.contains(&r.why_him.as_str()) {
                whys.push(&r.why_him);
            }
        }
        let why_him = match whys.len() {
            k if k <= 3 => whys.join(";"),
            k => format!("{}(另 {} 種來源)", whys[..3].join(";"), k - 3),
        };
        let first = &g[0];
        let row = Row {
            rank: 0,
            layer: first.layer,
            kind: first.kind.clone(),
            what: format!("收 {n} 棵已合工作樹:{}{more}", names.join("、")),
            pr: None,
            head: None,
            node: None,
            to: first.to.clone(),
            why_him,
            why_now: format!(
                "{n} 棵已合進 main;最久的頭 {} 小時前 commit",
                first.waited_min / 60
            ),
            unblocks: g.iter().map(|r| r.unblocks).sum(),
            waited_min: first.waited_min,
            starving: false,
            needs: first.needs.clone(),
            size: first.size.clone(),
            data_at: first.data_at.clone(),
            scope_unchecked: false,
        };
        out.push((row, false));
    }
    out
}

/// The reviewer for a branch of `author`: a free dev seat taking reviews, not the author;
/// the seat given fewer reviews this round first (spread, b433b L1), then one whose own
/// branch waits for a review (a swap), then by seat code.
fn pick_reviewer<'a>(
    author: Option<&str>,
    facts: &'a [SeatFact],
    waiting: &[String],
    given: &BTreeMap<String, usize>,
) -> Option<&'a SeatFact> {
    let swap = |f: &SeatFact| waiting.contains(&f.seat);
    let had = |f: &SeatFact| given.get(&f.seat).copied().unwrap_or(0);
    let mut c: Vec<&SeatFact> = facts
        .iter()
        .filter(|f| {
            f.dispatchable == "yes"
                && !f.unread_unknown()
                && size_rank(&f.size) >= size_rank(ROW_SIZE)
                && f.role != "coordinator"
                && Some(f.seat.as_str()) != author
        })
        .collect();
    c.sort_by(|a, b| {
        had(a)
            .cmp(&had(b))
            .then(swap(b).cmp(&swap(a)))
            .then(a.seat.cmp(&b.seat))
    });
    c.first().copied()
}

/// Order layers 1–4 from aib-slice's due and sweep and the seat facts, `sweep` being a
/// full scan (layer 4 from it too). `now` is unix seconds (for how long a due item
/// waited); `as_of` is written on every row.
pub fn rank(
    due: &Input<Due>,
    sweep: &Input<Sweep>,
    seats: &Input<Seats>,
    now: u64,
    as_of: &str,
) -> Next {
    rank_with(due, sweep, None, seats, now, as_of)
}

/// [`rank`] with `full`, a full scan (IFACE r8): `sweep` (a `--fast` one, which does not
/// see branches closed long ago) then gives layer 3 only and layer 4 comes from `full`
/// alone, its source `slice_full`; a full scan not read or stale ⇒ no layer-4 row.
pub fn rank_with(
    due: &Input<Due>,
    sweep: &Input<Sweep>,
    full: Option<&Input<Sweep>>,
    seats: &Input<Seats>,
    now: u64,
    as_of: &str,
) -> Next {
    rank_l4(due, sweep, full, seats, now, as_of, true)
}

/// [`rank_with`], `l4` false holding layer 4 back (IFACE r11, until aib-slice stops calling
/// a branch inside an open PR merged): no collect draft and no abandonable list are made,
/// so none of them becomes a row or an unknown, and the full scan is not a source; what
/// was held is one `skipped` line (ebb36 211ec156 L2).
pub fn rank_l4(
    due: &Input<Due>,
    sweep: &Input<Sweep>,
    full: Option<&Input<Sweep>>,
    seats: &Input<Seats>,
    now: u64,
    as_of: &str,
    l4: bool,
) -> Next {
    let mut sources = vec![
        source("slice_due", due, |d| (d.complete, d.errors.join("; "))),
        source("slice_sweep", sweep, |s| (s.complete, s.errors.join("; "))),
        source("seats", seats, |s| {
            (s.complete, "seat facts not complete".into())
        }),
    ];
    let mut unknown = Vec::new();
    let mut skipped = Vec::new();
    let mut drafts = Vec::new();
    let layer = |s: &Sweep, l: u8| -> Vec<Draft> {
        from_sweep(s).into_iter().filter(|d| d.layer == l).collect()
    };
    let mut lists = vec![
        (
            0,
            due.as_ref().map(|d| from_due(d, now)).unwrap_or_default(),
        ),
        (
            1,
            sweep
                .as_ref()
                .map(|s| match full {
                    None if l4 => from_sweep(s),
                    _ => layer(s, 3),
                })
                .unwrap_or_default(),
        ),
    ];
    let held = match full.unwrap_or(sweep) {
        Ok(s) if !l4 => layer(s, 4).len() + s.branches.iter().filter(|b| b.abandonable).count(),
        _ => 0,
    };
    if held > 0 {
        skipped.push(Skipped {
            what: format!("第 4 層 {held} 列"),
            reason: "第 4 層預設不出(IFACE r11),加 --with-l4".into(),
        });
    }
    let open_held = match full.unwrap_or(sweep) {
        Ok(s) if l4 => collect_held(s),
        _ => Vec::new(),
    };
    if !open_held.is_empty() {
        skipped.push(Skipped {
            what: format!("第 4 層 {} 列({})", open_held.len(), open_held.join(";")),
            reason: "已合但收了可能丟工作(PR 還開著 / 有沒 commit 或沒推的改動),不收(C4 安全版)"
                .into(),
        });
    }
    if let Some(f) = full.filter(|_| l4) {
        sources.push(source("slice_full", f, |s| {
            (s.complete, s.errors.join("; "))
        }));
        let l4_rows = f.as_ref().map(|s| layer(s, 4)).unwrap_or_default();
        lists.push((sources.len() - 1, l4_rows));
    }
    for (i, list) in lists {
        let src = &sources[i];
        if src.ok() {
            drafts.extend(list);
            continue;
        }
        unknown.extend(list.into_iter().map(|d| Unknown {
            what: d.what,
            why: format!("{} {}", src.name, src.state),
        }));
        if src.state == "failed" {
            unknown.push(Unknown {
                what: src.name.clone(),
                why: src.why.clone().unwrap_or_default(),
            });
        }
    }
    // seat facts that are read count seat by seat, complete or not (b433b M1): an unread
    // seat is `unknown` there and is not dispatched to; not read at all ⇒ nobody is
    let facts: Option<&[SeatFact]> = seats.as_ref().ok().map(|s| s.seats.as_slice());
    let coordinator = facts
        .and_then(|f| f.iter().find(|x| x.role == "coordinator"))
        .map(|x| x.seat.clone());
    let off_roster = seats.as_ref().is_ok_and(|s| s.on_roster);
    let mut given: BTreeMap<String, usize> = BTreeMap::new();
    // one review per head: due (a branch with no PR yet) and sweep (an open PR) can both
    // name the same head; the first, due's, is kept
    // F8 (519d0 04:36): a plan node reviewed and not pushed, its seat with no open PR
    // known ⇒ a layer-1 push row for that seat (the branch scan alone does not see it)
    for x in facts.unwrap_or_default() {
        let Some(c) = x.capacity.as_ref().filter(|c| c.open_prs == Some(0)) else {
            continue;
        };
        for id in &c.wip_nodes {
            drafts.push(Draft {
                layer: 1,
                kind: "push",
                what: format!("推 {id}(審過未推)"),
                pr: None,
                head: String::new(),
                to: Some(x.seat.clone()),
                why_now: "進度樹上審過、名下沒有開著的 PR".into(),
                branch: String::new(),
                author: Some(x.seat.clone()),
                waited_min: 0,
                node: Some(id.clone()),
            });
        }
    }
    let mut reviewed: Vec<String> = Vec::new();
    drafts.retain(|d| {
        // a head not given is no key (b433b inc N2): two such rows are not one
        if d.head.is_empty() {
            return true;
        }
        if d.kind != "review" || reviewed.contains(&d.head) {
            return d.kind != "review";
        }
        reviewed.push(d.head.clone());
        true
    });
    // seats with a branch of their own waiting for a review (a swap goes first)
    let waiting: Vec<String> = drafts
        .iter()
        .filter(|d| d.kind == "review")
        .filter_map(|d| d.author.clone())
        .collect();
    let stacked = |name: &str| {
        sweep.as_ref().map_or(0, |s| {
            s.branches
                .iter()
                .filter(|b| b.stacked_on.iter().map(String::as_str).any(|x| x == name))
                .count()
        })
    };
    let mut rows: Vec<(Row, bool)> = Vec::new();
    for mut d in drafts {
        let mut lost: Option<String> = None;
        let mut swap = false;
        let mut needs = "none".to_string();
        let size = ROW_SIZE.to_string();
        if d.kind == "review" {
            let Some(f) = facts else {
                unknown.push(Unknown {
                    what: d.what,
                    why: "席位事實讀不到,不知道誰閒".into(),
                });
                continue;
            };
            let author = d.author.as_deref();
            match pick_reviewer(author, f, &waiting, &given) {
                Some(r) => {
                    *given.entry(r.seat.clone()).or_default() += 1;
                    swap = waiting.contains(&r.seat);
                    d.to = Some(r.seat.clone());
                }
                // a free seat held back only by an unread count not known (IFACE r6)
                None if f.iter().any(|x| {
                    x.dispatchable == "yes" && x.unread_unknown() && Some(x.seat.as_str()) != author
                }) =>
                {
                    skipped.push(Skipped {
                        what: d.what,
                        reason: "unread unknown".into(),
                    });
                    continue;
                }
                None => needs = "no_free_reviewer".into(),
            }
        } else if let Some(seat) = d.to.clone() {
            let fact = facts.map(|f| f.iter().find(|x| x.seat == seat));
            match fact {
                None => {
                    unknown.push(Unknown {
                        what: d.what,
                        why: format!("席位事實讀不到,不知道 {seat} 閒不閒"),
                    });
                    continue;
                }
                // off the roster (an old seat, or another project's): the row stays, for
                // the coordinator to hand on, like a lost one (519d0 T2)
                Some(None) if off_roster => {
                    needs = "owner_off_roster".into();
                    lost = Some(seat.clone());
                    d.to = coordinator.clone();
                }
                Some(None) => {
                    unknown.push(Unknown {
                        what: d.what,
                        why: format!("席位事實裡沒有 {seat}"),
                    });
                    continue;
                }
                Some(Some(f)) if f.unread_unknown() => {
                    skipped.push(Skipped {
                        what: d.what,
                        reason: format!("{seat}: unread unknown"),
                    });
                    continue;
                }
                Some(Some(f)) => match f.dispatchable.as_str() {
                    "yes" if size_rank(&f.size) >= size_rank(ROW_SIZE) => {}
                    "yes" => needs = format!("seat_size_{}", f.size),
                    "no" => needs = "seat_busy".into(),
                    // on the plan's roster with no seat facts: lost, not unread — the row
                    // stays, for the coordinator to hand on (519d0 T2)
                    next_local::LOST => {
                        needs = "owner_lost".into();
                        lost = Some(seat.clone());
                        d.to = coordinator.clone();
                    }
                    _ => {
                        unknown.push(Unknown {
                            what: d.what,
                            why: format!("{seat} 能不能派不明"),
                        });
                        continue;
                    }
                },
            }
        } else if d.kind != "merge" {
            // no author: sweep gives no seat for some branches (no seat code in the name)
            needs = "no_owner".into();
        }
        // lost or off the roster with no coordinator to hand it on (ebb36 f80a69ea L1)
        if let (Some(who), None) = (&lost, &coordinator) {
            unknown.push(Unknown {
                what: d.what,
                why: format!("作者 {who} 不能派、統籌席讀不到,沒人改派"),
            });
            continue;
        }
        let why_him = match (d.kind, &d.to) {
            (_, t) if lost.is_some() => format!(
                "作者 {} {},{} 改派",
                lost.as_deref().unwrap_or_default(),
                if needs == "owner_off_roster" {
                    "不在名冊"
                } else {
                    "失聯"
                },
                t.as_deref().unwrap_or("統籌席")
            ),
            ("merge", _) => "合併監看".to_string(),
            (_, Some(t)) if swap => format!("{t} 自己也有分支在等審:交換審查"),
            ("review", Some(t)) => format!("{t} 閒、收審查、不是作者"),
            (_, Some(t)) => format!("{t} 是作者"),
            ("review", None) => "沒有閒的審查席".to_string(),
            (_, None) => "分支沒有席碼,認不出是誰的".to_string(),
        };
        let unblocks = stacked(&d.branch);
        rows.push((
            Row {
                rank: 0,
                layer: d.layer,
                kind: d.kind.into(),
                what: d.what,
                pr: d.pr,
                head: Some(d.head).filter(|h| !h.is_empty()),
                node: d.node,
                to: d.to,
                why_him,
                why_now: d.why_now,
                unblocks,
                waited_min: d.waited_min,
                starving: d.layer < 4 && d.waited_min > STARVE_MIN,
                needs,
                size,
                data_at: as_of.into(),
                scope_unchecked: false,
            },
            swap,
        ));
    }
    let mut rows = fold_collect(rows);
    rows.sort_by(|(a, sa), (b, sb)| {
        a.layer
            .cmp(&b.layer)
            // a row that can be done now before one that waits for its seat
            .then((a.needs != "none").cmp(&(b.needs != "none")))
            .then(sb.cmp(sa))
            .then(b.unblocks.cmp(&a.unblocks))
            .then(b.waited_min.cmp(&a.waited_min))
            .then(a.pr.cmp(&b.pr))
    });
    let rows: Vec<Row> = rows
        .into_iter()
        .enumerate()
        .map(|(i, (mut r, _))| {
            r.rank = i + 1;
            r
        })
        .collect();
    let complete = sources.iter().all(Source::ok) && unknown.is_empty();
    // abandonable is layer 4's kind: from the full scan when there is one (IFACE r8)
    let (scan, at) = match full.filter(|_| l4) {
        Some(f) => (f, sources.len() - 1),
        None => (sweep, 1),
    };
    let abandonable = match (scan, sources[at].ok()) {
        (Ok(s), true) if l4 => s
            .branches
            .iter()
            .filter(|b| b.abandonable)
            .map(|b| Abandonable {
                branch: b.branch.clone(),
                seat: b.seat.clone(),
                pr: b.pr.as_ref().map(|p| p.number),
                head: b.head.clone(),
                age_h: b.age_h,
            })
            .collect(),
        _ => Vec::new(),
    };
    Next {
        schema: SCHEMA.into(),
        as_of: as_of.into(),
        complete,
        sources,
        rows,
        unknown,
        skipped,
        abandonable,
        fleet_blocker: None,
        for_andy: Vec::new(),
    }
}

/// Read one JSON input with its schema checked: `Err(why)` when the text could not be read,
/// does not parse, or names another schema.
pub fn read_input<T: serde::de::DeserializeOwned>(
    text: Result<String, String>,
    schema: &str,
) -> Input<T> {
    let text = text?;
    let v: serde_json::Value = serde_json::from_str(&text).map_err(|e| format!("not JSON: {e}"))?;
    if v.get("schema").and_then(|s| s.as_str()) != Some(schema) {
        return Err(format!("schema is not {schema}"));
    }
    serde_json::from_value(v).map_err(|e| format!("not {schema}: {e}"))
}

/// The text form: one line per row, then what could not be judged.
pub fn render(n: &Next) -> String {
    render_with(n, false)
}

/// [`render`]; `all` lists every `skipped` line, otherwise one line with their count and
/// reasons, most first (519d0 T3: a seat reads the first rows, not a hundred holds).
pub fn render_with(n: &Next, all: bool) -> String {
    let mut out = String::new();
    for r in &n.rows {
        let to = r.to.as_deref().unwrap_or("-");
        let needs = if r.needs == "none" {
            String::new()
        } else {
            format!("  [{}]", r.needs)
        };
        let starving = if r.starving { "  [等太久]" } else { "" };
        out.push_str(&format!(
            "{:>2}. L{} {} → {to}  ({};{};解開 {}、等 {} 分){needs}{starving}\n",
            r.rank, r.layer, r.what, r.why_him, r.why_now, r.unblocks, r.waited_min
        ));
    }
    if n.rows.is_empty() {
        out.push_str("(第 1–4 層沒有能做的)\n");
    }
    for u in &n.unknown {
        out.push_str(&format!("不明 {}:{}\n", u.what, u.why));
    }
    if all {
        for k in &n.skipped {
            out.push_str(&format!("不排 {}:{}\n", k.what, k.reason));
        }
    } else if !n.skipped.is_empty() {
        let mut by: BTreeMap<&str, usize> = BTreeMap::new();
        for k in &n.skipped {
            *by.entry(k.reason.as_str()).or_default() += 1;
        }
        let mut by: Vec<(&str, usize)> = by.into_iter().collect();
        by.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(b.0)));
        let top: Vec<String> = by.iter().take(3).map(|(r, c)| format!("{r} {c}")).collect();
        let more = if by.len() > 3 { "…" } else { "" };
        out.push_str(&format!(
            "不排 {} 條({}{more});全部加 --all\n",
            n.skipped.len(),
            top.join("、")
        ));
    }
    for a in &n.abandonable {
        let pr = a.pr.map(|p| format!(" #{p}")).unwrap_or_default();
        out.push_str(&format!(
            "可放棄(留或丟由人定){pr} {} {}\n",
            a.branch,
            short(&a.head)
        ));
    }
    for s in n.sources.iter().filter(|s| !s.ok()) {
        let why = s
            .why
            .as_deref()
            .map(|w| format!(":{w}"))
            .unwrap_or_default();
        out.push_str(&format!("來源 {} {}{why}\n", s.name, s.state));
    }
    out
}

/// Whether a `pr:` item of a plan node has merged: read from sweep (each branch's PR: open,
/// or its latest closed one), else from `more` (GitHub, [`GhMerged`]; aib-slice `--fast`
/// lists no PR closed over 72 hours ago). Not known either way, and every `skills-pr:` item
/// (another repository): the node goes to `unknown`, never `rows`.
pub struct SweepMerged<'a> {
    pub sweep: Option<&'a Sweep>,
    pub more: Option<&'a dyn MergedSource>,
}

impl MergedSource for SweepMerged<'_> {
    fn merged(&self, dep: &Dep) -> Option<bool> {
        let Dep::Pr(n) = dep else {
            return None;
        };
        let listed = self.sweep.and_then(|s| {
            s.branches
                .iter()
                .filter_map(|b| b.pr.as_ref())
                .find(|p| p.number == u64::from(*n))
                .map(|p| p.merged)
        });
        listed.or_else(|| self.more.and_then(|m| m.merged(dep)))
    }
}

/// `pr:` items asked of GitHub (`GET repos/<repo>/pulls/<n>`, its `merged`), each PR once
/// per answer. A call that fails is not known (`None`), never "not merged".
pub struct GhMerged<'a> {
    pub gh: &'a dyn aib_ghread::Read,
    pub repo: &'a str,
    asked: std::cell::RefCell<BTreeMap<u32, Option<bool>>>,
}

impl<'a> GhMerged<'a> {
    pub fn new(gh: &'a dyn aib_ghread::Read, repo: &'a str) -> Self {
        Self {
            gh,
            repo,
            asked: std::cell::RefCell::new(BTreeMap::new()),
        }
    }
}

impl MergedSource for GhMerged<'_> {
    fn merged(&self, dep: &Dep) -> Option<bool> {
        let Dep::Pr(n) = dep else {
            return None;
        };
        if let Some(v) = self.asked.borrow().get(n) {
            return *v;
        }
        let v = self
            .gh
            .get(&format!("repos/{}/pulls/{n}", self.repo))
            .ok()
            .and_then(|v| v.get("merged").and_then(serde_json::Value::as_bool));
        self.asked.borrow_mut().insert(*n, v);
        v
    }
}

/// Lowercase hex sha256 (IFACE r4 §5: `approved.scope` = sha256 of [`next_local::scope_text`]).
pub fn sha256_hex(text: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut out = String::with_capacity(64);
    for b in Sha256::digest(text.as_bytes()) {
        let _ = write!(out, "{b:02x}");
    }
    out
}

/// Sources `next` reads only to add rows: their state never makes `complete` false (519d0
/// 09:33, IFACE r12: required = sweep, plan, idea ledger, seats; optional = tidy-scan).
pub const OPTIONAL_SOURCES: &[&str] = &["tidy_scan"];

/// Layers 5–7 (`next_local`, ebb36) put after layers 1–4: their rows ranked on, their
/// unknowns, skipped and sources added (the seats source is this answer's own),
/// `fleet_blocker` and `for_andy` carried. A source not `ok` makes the answer incomplete,
/// except `unsupported` and the [`OPTIONAL_SOURCES`] in any state.
pub fn join_local(mut n: Next, local: LocalOut) -> Next {
    let base = n.rows.len();
    for (i, r) in local.rows.into_iter().enumerate() {
        n.rows.push(Row {
            rank: base + i + 1,
            layer: r.layer,
            kind: r.kind,
            what: r.what,
            pr: r.pr.map(u64::from),
            head: r.head,
            node: r.node,
            to: Some(r.to).filter(|t| !t.is_empty()),
            why_him: r.why_him,
            why_now: r.why_now,
            unblocks: r.unblocks,
            waited_min: r.waited_min.unwrap_or(0),
            // C13: a mainline node or an idea waiting over STARVE_MIN since its approval
            starving: r.waited_min.is_some_and(|w| w > STARVE_MIN),
            needs: r.needs,
            size: r.size,
            data_at: r.data_at,
            scope_unchecked: r.scope_unchecked,
        });
    }
    n.unknown.extend(local.unknown.into_iter().map(|u| Unknown {
        what: u.what,
        why: u.why,
    }));
    n.skipped.extend(local.skipped.into_iter().map(|k| Skipped {
        what: k.what,
        reason: k.reason,
    }));
    for (name, state) in local.sources {
        if name == "seats" {
            continue;
        }
        let state = if state == "missing" {
            "failed".to_owned()
        } else {
            state
        };
        n.sources.push(Source {
            name,
            state,
            why: None,
        });
    }
    n.fleet_blocker = local
        .fleet_blocker
        .and_then(|f| serde_json::to_value(f).ok());
    n.for_andy = local
        .for_andy
        .iter()
        .filter_map(|f| serde_json::to_value(f).ok())
        .collect();
    // tidy_scan is an optional source (519d0 09:33, IFACE r12): whatever its state, it does
    // not make the answer incomplete (what is wrong with it is a skipped line)
    n.complete =
        n.sources.iter().all(|s| {
            s.ok() || s.state == "unsupported" || OPTIONAL_SOURCES.contains(&s.name.as_str())
        }) && n.unknown.is_empty();
    n
}

/// The plan's roster (cabin-plan `seats`, 519d0 T1): its five-hex seat codes, so `Andy`
/// and `Codex` are not in it. Missing, not an object, or no seat code in it ⇒ `None`.
pub fn roster_of(plan: &serde_json::Value) -> Option<Vec<String>> {
    let codes: Vec<String> = plan
        .get("seats")?
        .as_object()?
        .keys()
        .filter(|k| next_local::is_seat(k))
        .cloned()
        .collect();
    (!codes.is_empty()).then_some(codes)
}

/// Seat facts limited to the roster (519d0 T1, T2): a seat off the roster is never `to`;
/// a roster seat with no facts at all is [`next_local::LOST`], not unread.
pub fn on_roster(mut s: Seats, roster: &[String]) -> Seats {
    s.on_roster = true;
    s.seats.retain(|f| roster.contains(&f.seat));
    for r in roster {
        if !s.seats.iter().any(|f| &f.seat == r) {
            s.seats.push(SeatFact {
                seat: r.clone(),
                dispatchable: next_local::LOST.into(),
                size: "none".into(),
                role: "dev".into(),
                capacity: None,
            });
        }
    }
    s
}

/// Layers 5–6's seats from the seat facts layers 1–4 read (Codex M1, M2): a seat whose
/// unread is not known is [`next_local::UNREAD_UNKNOWN`] there, so it is never `to`
/// (IFACE r6) and what it would have taken is `skipped` (r9).
fn local_seats(facts: &[SeatFact]) -> Vec<next_local::SeatFact> {
    facts
        .iter()
        .filter(|f| next_local::is_seat(&f.seat))
        .map(|f| next_local::SeatFact {
            seat: f.seat.clone(),
            dispatchable: if f.unread_unknown() {
                next_local::UNREAD_UNKNOWN.into()
            } else {
                f.dispatchable.clone()
            },
            size: f.size.clone(),
            role: f.role.clone(),
        })
        .collect()
}

/// `aib-drive next --due FILE --sweep FILE [--sweep-full FILE] --seats FILE [--plan FILE
/// [--ideas FILE]] [--json] [--seat XXXXX]`. With `--sweep-full` layer 4 comes from that
/// full scan alone (IFACE r8). `read` reads a file (the caller decides what a path means and
/// whether it is too old: an error starting `stale:` is the source `stale`). With `--plan`
/// layers 5–6 are added (`--ideas` not given or unreadable ⇒ no layer 6, `sources.ideas`
/// says so). Exit codes (IFACE §0): 0 every source read, 2 usage, 3 something not read.
pub fn cli(
    args: &[String],
    read: impl Fn(&str) -> Result<String, String>,
    now: u64,
    as_of: &str,
) -> (String, i32) {
    cli_with(args, read, None, now, as_of)
}

/// [`cli`] with `more`: where a `pr:` item sweep does not list is read (GitHub in the program).
pub fn cli_with(
    args: &[String],
    read: impl Fn(&str) -> Result<String, String>,
    more: Option<&dyn MergedSource>,
    now: u64,
    as_of: &str,
) -> (String, i32) {
    let usage = "usage: aib-drive next [--due FILE] [--sweep FILE] [--sweep-full FILE] \
[--seats FILE] [--plan FILE [--ideas FILE] [--intents FILE] [--tidy FILE]] [--json] [--all] [--with-l4] \
[--seat XXXXX]\n";
    let mut files: BTreeMap<&str, &str> = BTreeMap::new();
    let (mut json, mut all, mut with_l4, mut seat) = (false, false, false, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--json" => json = true,
            "--all" => all = true,
            "--with-l4" => with_l4 = true,
            k @ ("--due" | "--sweep" | "--sweep-full" | "--seats" | "--plan" | "--ideas"
            | "--intents" | "--tidy") => match it.next() {
                Some(v) => {
                    files.insert(k, v);
                }
                None => return (usage.into(), 2),
            },
            "--seat" => match it.next() {
                Some(v)
                    if v.len() == 5
                        && v.bytes()
                            .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase()) =>
                {
                    seat = Some(v.clone())
                }
                _ => return (usage.into(), 2),
            },
            _ => return (usage.into(), 2),
        }
    }
    let get = |k: &str| match files.get(k) {
        Some(f) => read(f),
        None => Err(format!("{k} not given")),
    };
    let plan: Option<Result<serde_json::Value, String>> = files.contains_key("--plan").then(|| {
        get("--plan").and_then(|t| {
            serde_json::from_str::<serde_json::Value>(&t).map_err(|e| format!("not JSON: {e}"))
        })
    });
    // who may be `to` (519d0 T1): the plan's roster; no plan given ⇒ no roster check
    let roster: Option<Result<Vec<String>, String>> = plan.as_ref().map(|p| match p {
        Ok(v) => roster_of(v).ok_or_else(|| "進度樹沒有 seats 名冊".to_owned()),
        Err(e) => Err(format!("plan 讀不到:{e}")),
    });
    let seats: Input<Seats> = read_input(get("--seats"), SEATS_SCHEMA).map(|s| match &roster {
        Some(Ok(r)) => on_roster(s, r),
        _ => s,
    });
    let sweep: Input<Sweep> = read_input(get("--sweep"), SWEEP_SCHEMA);
    let full: Option<Input<Sweep>> = files
        .contains_key("--sweep-full")
        .then(|| read_input(get("--sweep-full"), SWEEP_SCHEMA));
    let due: Input<Due> = read_input(get("--due"), DUE_SCHEMA);
    let mut n = rank_l4(&due, &sweep, full.as_ref(), &seats, now, as_of, with_l4);
    // one aib-slice scan writes both files with one generated_at; two differing means a
    // write of one failed and the pair is of two rounds (1ced7 L5, IFACE r9 4); a file
    // without the field (0) is not compared
    if let (Ok(d), Ok(s)) = (&due, &sweep) {
        if d.generated_at != 0 && s.generated_at != 0 && d.generated_at != s.generated_at {
            n.sources.push(Source {
                name: "slice".into(),
                state: "partial".into(),
                why: Some(format!(
                    "due generated_at {} ≠ sweep {}",
                    d.generated_at, s.generated_at
                )),
            });
            n.complete = false;
        }
    }
    match &roster {
        Some(Ok(_)) => n.sources.push(Source {
            name: "roster".into(),
            state: "ok".into(),
            why: None,
        }),
        Some(Err(why)) => {
            // no roster, nobody can be picked: no review (3), mainline (5) or idea (6) row
            n.sources.push(Source {
                name: "roster".into(),
                state: "failed".into(),
                why: Some(why.clone()),
            });
            let (keep, drop): (Vec<Row>, Vec<Row>) = n.rows.drain(..).partition(|r| r.layer != 3);
            n.unknown.extend(drop.into_iter().map(|r| Unknown {
                what: r.what,
                why: "名冊讀不到,不知道誰能審".into(),
            }));
            n.rows = keep;
            for (i, r) in n.rows.iter_mut().enumerate() {
                r.rank = i + 1;
            }
            n.complete = false;
        }
        None => {}
    }
    if let Some(plan) = plan {
        match (plan, &roster) {
            (Ok(plan), Some(Ok(_))) => {
                let ideas = get("--ideas")
                    .ok()
                    .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
                // layer 7 (C7): not given = unsupported; given but not JSON = failed
                let not_read = serde_json::Value::Null;
                let tidy = files.contains_key("--tidy").then(|| {
                    get("--tidy")
                        .ok()
                        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
                });
                // the seat facts layers 1–4 checked, not the text again (Codex M2)
                let seats = seats.as_ref().ok().map(|s| local_seats(&s.seats));
                // merged only from a scan that is whole; a full one first (Codex M3)
                fn whole(s: &Input<Sweep>) -> Option<&Sweep> {
                    s.as_ref().ok().filter(|s| s.complete)
                }
                let scan = full.as_ref().and_then(whole).or_else(|| whole(&sweep));
                // act's intents ledger (IFACE r9 3): not given = no file = nothing claimed
                let (claimed, bad) = match files.contains_key("--intents").then(|| get("--intents"))
                {
                    None => (BTreeSet::new(), 0),
                    Some(Ok(t)) => next_local::claimed_from_intents(
                        Some(&t),
                        (now as i64).saturating_mul(1000),
                    ),
                    Some(Err(e)) => {
                        n.sources.push(Source {
                            name: "intents".into(),
                            state: "failed".into(),
                            why: Some(e),
                        });
                        (BTreeSet::new(), 0)
                    }
                };
                if bad > 0 {
                    n.sources.push(Source {
                        name: "intents".into(),
                        state: "partial".into(),
                        why: Some(format!("{bad} 行讀不懂")),
                    });
                }
                // the open PR first in the queue and its author (519d0 D1)
                let front = scan.and_then(|s| {
                    next_local::queue_front(s.branches.iter().filter_map(|b| {
                        b.pr.as_ref()
                            .map(|p| (p.number, p.state == "open" && !p.merged, b.seat.as_deref()))
                    }))
                });
                let hash = |t: &str| sha256_hex(t);
                let local = next_local::rank_local(
                    &LocalInput {
                        plan: &plan,
                        ideas: ideas.as_ref(),
                        seats: seats.as_deref(),
                        claimed: &claimed,
                        queue_unblock: front.as_deref(),
                        scope_hash: Some(&hash),
                        // this arm runs only with the roster read and applied to seats
                        roster_applied: true,
                        tidy: tidy.as_ref().map(|t| t.as_ref().unwrap_or(&not_read)),
                        // refresh writes at_ms (when), head (what it scanned) and main (that
                        // round's main) into the file; layer 7 compares only those
                        tidy_age_s: tidy
                            .as_ref()
                            .and_then(|t| t.as_ref())
                            .and_then(|t| t.get("at_ms"))
                            .and_then(serde_json::Value::as_u64)
                            .and_then(|at| now.checked_sub(at / 1000)),
                        as_of,
                    },
                    &SweepMerged { sweep: scan, more },
                );
                n = join_local(n, local);
            }
            // the plan read but no roster in it: nobody to hand a node to (519d0 T1);
            // the roster source above already made the answer not complete
            (Ok(_), _) => n.unknown.push(Unknown {
                what: "第 5–6 層".into(),
                why: "名冊讀不到".into(),
            }),
            (Err(e), _) => {
                n.sources.push(Source {
                    name: "plan".into(),
                    state: "failed".into(),
                    why: Some(e),
                });
                n.unknown.push(Unknown {
                    what: "第 5–6 層".into(),
                    why: "plan 讀不到".into(),
                });
                n.complete = false;
            }
        }
    }
    let n = match seat {
        Some(s) => n.for_seat(&s),
        None => n,
    };
    let rc = n.rc();
    let out = if json {
        serde_json::to_string_pretty(&n).unwrap_or_default() + "\n"
    } else {
        render_with(&n, all)
    };
    (out, rc)
}
