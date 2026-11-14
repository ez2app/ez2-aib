//! `aib-drive next`, layers 1–4 (S79 n-1; DEV-S79-drive-next §2.1, IFACE-s79-drive §2):
//! what can be done now, best first, read only. Nothing here acts.
//!
//! - Layer 1 merge, 2 fix (re-run, cancel a superseded run, remind an author), 3 review /
//!   verify, 4 collect (mark an old head's reviews stale). They come from [`decide`]'s
//!   intents as they are: this module only orders and labels them.
//! - In a layer: the row that unblocks more others first, then the one that waited longer,
//!   then the lower PR number. A review for a seat that has its own PR waiting for review
//!   goes first among equals (a review swap is faster than a new slice).
//! - A source that is not `ok` is never read as "nothing there": what depends on it goes to
//!   `unknown`, never to `rows` (a PR whose cells or review notes could not be read, a
//!   review whose seat's facts are unknown).
//! - Never in `rows`: what needs a human (an [`Intent::Ask`]), a note.
//!
//! Layers 5–7 (`next_local.rs`, ebb36) and the seat facts (`seats.rs`, b433b) are other
//! slices; [`SeatFact`] is the part of `aib-drive.seats.v1` this module reads, kept local
//! until the integration joins them.

use crate::decide::{Intent, Known, Pr, Seat, Snapshot};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// `schema` of the JSON answer.
pub const SCHEMA: &str = "aib-drive.next.v1";

/// What one seat's facts say for dispatch (`aib-drive.seats.v1`, IFACE §1): `yes` / `no` /
/// `unknown`, and the size of work it may take (`review` / `slice` / `mainline` / `none`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SeatFact {
    pub seat: Seat,
    pub dispatchable: String,
    pub size: String,
}

/// One input of the answer and whether it was read: `ok` / `stale` / `failed` /
/// `unsupported`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Source {
    pub name: String,
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub at: Option<String>,
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
    /// `merge` / `rerun` / `cancel` / `nudge` / `review` / `verify` / `stale`.
    pub kind: String,
    pub what: String,
    pub pr: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub node: Option<String>,
    /// Who does it; `None` = the merge watch / whoever runs the round.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<Seat>,
    pub why_him: String,
    pub why_now: String,
    pub unblocks: usize,
    pub waited_min: u64,
    /// `none`, or what is still missing before it can be done (`seat_busy`).
    pub needs: String,
    pub size: String,
    pub data_at: String,
}

/// Something that could not be judged, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unknown {
    pub what: String,
    pub why: String,
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

/// How many other open PRs wait on `pr`: the ones stacked on its branch, plus one when an
/// unmet expectation points at it (`blocks_release`).
fn unblocks(pr: &Pr, all: &[Pr]) -> usize {
    let stacked = all
        .iter()
        .filter(|o| o.number != pr.number && o.base_ref == pr.head_ref)
        .count();
    stacked + usize::from(pr.blocks_release)
}

/// Why a PR cannot be judged this round (`None` = it can).
fn pr_unknown(pr: &Pr) -> Option<String> {
    if let Some(n) = pr.unknown_notes.first() {
        return Some(format!("審查稿第一行讀不懂:{n}"));
    }
    if let Known::Unknown(why) = &pr.cells {
        return Some(format!("CI 格讀不到:{why}"));
    }
    None
}

/// What an intent becomes in the rows.
struct Shape {
    layer: u8,
    kind: &'static str,
    pr: u64,
    to: Option<Seat>,
    what: String,
    why_now: String,
}

/// The row shape of an intent; `None` for one that never goes into the rows (needs a
/// human, a note).
fn shape(i: &Intent) -> Option<Shape> {
    let (layer, kind, pr, to, what, why_now) = match i {
        Intent::Merge { pre } => (
            1,
            "merge",
            pre.pr,
            None,
            format!("#{} 可合", pre.pr),
            "審過、收據與必要格全綠".into(),
        ),
        Intent::Rerun { run, pre } => (
            2,
            "rerun",
            pre.pr,
            None,
            format!("#{} 重跑 run {run}", pre.pr),
            "基礎設施失敗,不是真紅".into(),
        ),
        Intent::Cancel { run, why, pre } => (
            2,
            "cancel",
            pre.pr,
            None,
            format!("#{} 取消 run {run}", pre.pr),
            why.clone(),
        ),
        Intent::Nudge { seat, pr, about } => (
            2,
            "nudge",
            *pr,
            Some(seat.clone()),
            format!("#{pr} 提醒 {seat}"),
            about.clone(),
        ),
        Intent::AskReview { to, from, pre } => (
            3,
            "review",
            pre.pr,
            Some(to.clone()),
            match from {
                Some(f) => format!("審 #{} {}..{}", pre.pr, short(f), short(&pre.head)),
                None => format!("審 #{} 到 {}", pre.pr, short(&pre.head)),
            },
            "頭碼還沒有非作者審查".into(),
        ),
        Intent::AskVerify { to, kind, pre } => (
            3,
            "verify",
            pre.pr,
            to.clone(),
            format!("驗 #{} {kind}", pre.pr),
            "頭碼還沒有驗收".into(),
        ),
        Intent::Stale { pr, old, new } => (
            4,
            "stale",
            *pr,
            None,
            format!("#{pr} 舊頭 {} 的審查標過期", short(old)),
            format!("頭已換成 {}", short(new)),
        ),
        Intent::Ask { .. } | Intent::Note { .. } => return None,
    };
    Some(Shape {
        layer,
        kind,
        pr,
        to,
        what,
        why_now,
    })
}

fn short(sha: &str) -> &str {
    sha.get(..8).unwrap_or(sha)
}

/// Order the intents of one round into layers 1–4.
///
/// `seats`: the seat facts, or `None` when that source is not read (then a row with a seat
/// goes to `unknown`: a seat not known to be free is not dispatched to). `extra_sources`
/// are the other inputs' states (gh, the review notes); one not `ok` makes the answer
/// incomplete.
pub fn rank(
    s: &Snapshot,
    intents: &[Intent],
    seats: Option<&[SeatFact]>,
    sources: Vec<Source>,
    as_of: &str,
) -> Next {
    let by_number: BTreeMap<u64, &Pr> = s.prs.iter().map(|p| (p.number, p)).collect();
    let facts: Option<BTreeMap<&str, &SeatFact>> =
        seats.map(|v| v.iter().map(|f| (f.seat.as_str(), f)).collect());
    // seats with a PR of their own waiting for a review (a swap goes first)
    let waiting_review: Vec<&str> = intents
        .iter()
        .filter_map(|i| match i {
            Intent::AskReview { pre, .. } => by_number.get(&pre.pr).map(|p| p.owner.as_str()),
            _ => None,
        })
        .collect();
    let mut unknown: Vec<Unknown> = s
        .prs
        .iter()
        .filter_map(|p| {
            pr_unknown(p).map(|why| Unknown {
                what: format!("#{}", p.number),
                why,
            })
        })
        .collect();
    let mut rows: Vec<(Row, bool)> = Vec::new();
    for i in intents {
        let Some(Shape {
            layer,
            kind,
            pr: n,
            to,
            what,
            why_now,
        }) = shape(i)
        else {
            continue;
        };
        let Some(pr) = by_number.get(&n) else {
            continue;
        };
        if pr_unknown(pr).is_some() {
            continue; // listed once above
        }
        let mut needs = "none".to_string();
        let mut size = "review".to_string();
        if let Some(seat) = &to {
            match facts.as_ref().map(|f| f.get(seat.as_str())) {
                None => {
                    unknown.push(Unknown {
                        what: what.clone(),
                        why: format!("席位事實讀不到,不知道 {seat} 閒不閒"),
                    });
                    continue;
                }
                Some(None) => {
                    unknown.push(Unknown {
                        what: what.clone(),
                        why: format!("席位事實裡沒有 {seat}"),
                    });
                    continue;
                }
                Some(Some(f)) => match f.dispatchable.as_str() {
                    "yes" => {
                        size = f.size.clone();
                        if layer == 3 && !f.size.contains("review") {
                            needs = format!("seat_size_{}", f.size);
                        }
                    }
                    "no" => needs = "seat_busy".into(),
                    _ => {
                        unknown.push(Unknown {
                            what: what.clone(),
                            why: format!("{seat} 能不能派不明"),
                        });
                        continue;
                    }
                },
            }
        }
        let swap = layer == 3 && to.as_deref().is_some_and(|t| waiting_review.contains(&t));
        let why_him = match (kind, &to) {
            ("merge", _) => "合併監看".to_string(),
            (_, Some(t)) if swap => format!("{t} 自己也有 PR 在等審:交換審查"),
            ("review" | "verify", Some(t)) => format!("{t} 不是作者"),
            (_, Some(t)) => format!("{t} 是作者"),
            (_, None) => "跑這一輪的人".to_string(),
        };
        rows.push((
            Row {
                rank: 0,
                layer,
                kind: kind.into(),
                what,
                pr: n,
                node: None,
                to,
                why_him,
                why_now,
                unblocks: unblocks(pr, &s.prs),
                waited_min: pr.waiting_secs / 60,
                needs,
                size,
                data_at: as_of.into(),
            },
            swap,
        ));
    }
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
    let mut sources = sources;
    sources.push(Source {
        name: "seats".into(),
        state: if seats.is_some() { "ok" } else { "unsupported" }.into(),
        at: None,
    });
    let complete = sources.iter().all(Source::ok) && unknown.is_empty();
    Next {
        schema: SCHEMA.into(),
        as_of: as_of.into(),
        complete,
        sources,
        rows,
        unknown,
    }
}

/// The text form: one line per row, then what could not be judged.
pub fn render(n: &Next) -> String {
    let mut out = String::new();
    for r in &n.rows {
        let to = r.to.as_deref().unwrap_or("-");
        let needs = if r.needs == "none" {
            String::new()
        } else {
            format!("  [{}]", r.needs)
        };
        out.push_str(&format!(
            "{:>2}. L{} {} → {to}  ({};{};解開 {}、等 {} 分){needs}\n",
            r.rank, r.layer, r.what, r.why_him, r.why_now, r.unblocks, r.waited_min
        ));
    }
    if n.rows.is_empty() {
        out.push_str("(第 1–4 層沒有能做的)\n");
    }
    for u in &n.unknown {
        out.push_str(&format!("不明 {}:{}\n", u.what, u.why));
    }
    for s in n.sources.iter().filter(|s| !s.ok()) {
        out.push_str(&format!("來源 {} {}\n", s.name, s.state));
    }
    out
}
