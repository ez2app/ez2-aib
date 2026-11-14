//! `aib-drive next`, layers 5–7 plus the fleet blocker and the order for Andy
//! (S79 n-2c, ebb36; IFACE-s79-drive-519d0-1115 §2, DEV-S79 §2.1).
//!
//! Reads only local ledgers, never GitHub itself; whether a `pr:` / `skills-pr:`
//! item has merged comes through [`MergedSource`] (the gh reader of n-1 at
//! integration time, a fake in the tests).
//!
//! - **Layer 5, mainline next**: a plan node with `approved` written, every `after`
//!   item done, an owner seat that is dispatchable, and no claim on it. Only the
//!   fields written on the node count (DEV-S79 §2.1); a node missing one of them is
//!   not dispatched. A dependency or an owner that cannot be read puts the node in
//!   `unknown`, never in `rows`. Only the statuses in [`OPEN_STATUSES`] are handed
//!   out; that allow-list is deliberate (it only ever holds a node back).
//! - **Layer 6, auto-approved ideas**: `aib-idea list --all --json`, classification
//!   `auto` that Andy has not said no to, hung on a plan node whose status is in
//!   [`IDEA_NODE_STATUSES`] (an idea with no node is `unknown`), S → A → B → C, at
//!   most one per seat (the seat that wrote it). A ledger with `complete` not true
//!   gives no layer 6 and no `for_andy`; `sources.ideas` says `partial`.
//! - Every layer 5 / 6 row is size [`ROW_SIZE`]; a seat gets it only when its own
//!   size [`fits`] (review < slice < mainline).
//! - **Layer 7** (tidy-scan duplicates) is not in this version; `sources.tidy_scan`
//!   says `unsupported`.
//! - **Scope** (IFACE r4 §C12): a node approved with `approved.scope` is handed out
//!   only while the hash of [`scope_text`] still matches (else `unknown`, 核准後範圍
//!   被改); approved without `scope` ⇒ handed out with `scope_unchecked: true`.
//! - **fleet_blocker**: every unfinished node grouped by what holds it (an `after`
//!   item not done yet / waiting for Andy / reviewed and queued = the macOS queue);
//!   the biggest group, with what would unblock it.
//! - **for_andy**: the needs-human ideas still `pending`, most nodes unblocked first.
//!
//! Never in `rows`: anything that needs a human, a biometric, or is in neither
//! the plan nor the idea ledger.

use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// The status that means a node is done (b433b gate.rs: `node:<id>` counts as done
/// only at this status).
pub const DONE_STATUS: &str = "實查通過";
/// The only statuses a plan node is handed out from as "mainline next" (519d0,
/// IFACE r3 §2). An allow-list on purpose: any other status (開發中 / 已派 / 審過排隊
/// / 等 Andy / one added later that nobody told this file about) is never dispatched.
/// Fewer dispatches, never a wrong one (DEV-S79 §2.1; 043dd n-2c review N1). The
/// status only ever holds a node back; it never makes one dispatchable by itself.
pub const OPEN_STATUSES: &[&str] = &["已同意未做", "已定未做", "未開始", "等前置"];
/// The node statuses an auto-approved idea may be handed out under (IFACE r3 §1):
/// "is it done yet" is read from the plan node only (the idea ledger has no done
/// field yet; a follow-up for aib-idea).
pub const IDEA_NODE_STATUSES: &[&str] = &["已同意未做", "已定未做", "未開始"];
/// Seat / row sizes, smallest first (IFACE §3: act sends a row only to a seat at
/// least as big as the row).
pub const SIZES: &[&str] = &["review", "slice", "mainline"];
/// The size of every layer 5 and layer 6 row (IFACE r3 §3; layers 1–4 write review).
pub const ROW_SIZE: &str = "slice";
/// The status of a node waiting for Andy.
pub const ANDY_STATUS: &str = "等 Andy";
/// The status of a node reviewed and waiting in the merge queue (the macOS queue).
pub const QUEUED_STATUS: &str = "審過排隊";
/// Layer numbers in the `next` output.
pub const LAYER_MAINLINE: u8 = 5;
pub const LAYER_IDEA: u8 = 6;
/// Grade order of auto-approved ideas.
pub const GRADES: &[&str] = &["S", "A", "B", "C"];

/// One `after` item (b433b gate.rs, three prefixes only).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Dep {
    Node(String),
    Pr(u32),
    SkillsPr(u32),
}

impl Dep {
    pub fn parse(s: &str) -> Option<Dep> {
        let num = |t: &str| -> Option<u32> {
            if t.is_empty() || !t.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            t.parse().ok()
        };
        if let Some(id) = s.strip_prefix("node:") {
            return (!id.is_empty()).then(|| Dep::Node(id.to_owned()));
        }
        if let Some(n) = s.strip_prefix("skills-pr:") {
            return num(n).map(Dep::SkillsPr);
        }
        s.strip_prefix("pr:").and_then(num).map(Dep::Pr)
    }

    pub fn text(&self) -> String {
        match self {
            Dep::Node(id) => format!("node:{id}"),
            Dep::Pr(n) => format!("pr:{n}"),
            Dep::SkillsPr(n) => format!("skills-pr:{n}"),
        }
    }
}

/// Whether a `pr:` / `skills-pr:` item has merged. `None` = could not tell.
pub trait MergedSource {
    fn merged(&self, dep: &Dep) -> Option<bool>;
}

/// The part of one `aib-drive seats` row this module needs (IFACE §1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeatFact {
    pub seat: String,
    /// yes / no / unknown
    pub dispatchable: String,
    /// review / slice / mainline / none
    pub size: String,
    /// dev / coordinator
    pub role: String,
}

/// The seats of `aib-drive seats --json`; a row without a seat code is left out.
pub fn seats_from_json(v: &Value) -> Vec<SeatFact> {
    let s = |r: &Value, k: &str| r.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
    v.get("seats")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter(|r| is_seat(&s(r, "seat")))
                .map(|r| SeatFact {
                    seat: s(r, "seat"),
                    dispatchable: s(r, "dispatchable"),
                    size: s(r, "size"),
                    role: s(r, "role"),
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Five lowercase hex digits.
pub fn is_seat(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The seat codes written in a free-text owner field, in order, once each
/// (`043dd(r-0 r-1)/ 1ced7(r-2a)` ⇒ 043dd, 1ced7). Only whole five-hex words count.
pub fn owner_seats(owner: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for w in owner.split(|c: char| !c.is_ascii_alphanumeric()) {
        if is_seat(w) && !out.iter().any(|o| o == w) {
            out.push(w.to_owned());
        }
    }
    out
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Row {
    pub rank: usize,
    pub layer: u8,
    pub kind: String,
    pub what: String,
    pub pr: Option<u32>,
    pub node: Option<String>,
    pub to: String,
    pub why_him: String,
    pub why_now: String,
    pub unblocks: usize,
    pub waited_min: Option<u64>,
    pub needs: String,
    pub size: String,
    pub data_at: String,
    /// The PR head (40 hex) for rows with a PR; layers 5 and 6 have none ⇒ null.
    pub head: Option<String>,
    /// The node was approved without a `scope`, so a change after the approval could
    /// not be checked (IFACE r4 §C12); written only when true.
    #[serde(skip_serializing_if = "is_false")]
    pub scope_unchecked: bool,
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// The approved scope of a node: `title`, then every `after` item as written, joined
/// by "\n". The hash of it is what `approved.scope` holds; the hasher comes from the
/// caller (sha256 hex at integration), so this slice adds no dependency.
pub type ScopeHash<'a> = &'a dyn Fn(&str) -> String;

pub fn scope_text(title: &str, after: &[&str]) -> String {
    std::iter::once(title)
        .chain(after.iter().copied())
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Unknown {
    pub what: String,
    pub why: String,
}

/// Seen and held back by a rule, not unread (IFACE r9): an idea without a node, a node
/// not in the tree, an owner with no seat code, an `after` word not known, a scope changed
/// after the approval, a seat whose unread is not known. Not counted against `complete`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Skipped {
    pub what: String,
    pub reason: String,
}

/// `dispatchable` of a seat whose unread count is not known (next sets it from
/// `capacity.unread` = null, IFACE r6): never sent to, and held back as `skipped`.
pub const UNREAD_UNKNOWN: &str = "unread_unknown";

/// `dispatchable` of a seat on the plan's roster with no seat facts at all (519d0 T2): it
/// is lost, not unread; never sent to, and what it owns is held back as `skipped`.
pub const LOST: &str = "lost";

/// Why a node is held back: not known (`unknown`) or a rule (`skipped`, IFACE r9).
enum Hold {
    Unknown(String),
    Skipped(String),
}

impl Hold {
    fn put(self, what: &str, unknown: &mut Vec<Unknown>, skipped: &mut Vec<Skipped>) {
        let what = what.to_owned();
        match self {
            Hold::Unknown(why) => unknown.push(Unknown { what, why }),
            Hold::Skipped(reason) => skipped.push(Skipped { what, reason }),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FleetBlocker {
    /// `after` / `waiting_andy` / `macos_queue`
    pub cause: String,
    pub label: String,
    pub nodes: Vec<String>,
    pub count: usize,
    pub unblock_by: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ForAndy {
    pub id: String,
    pub title: String,
    pub unblocks: usize,
    pub kind: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LocalOut {
    pub rows: Vec<Row>,
    pub unknown: Vec<Unknown>,
    pub skipped: Vec<Skipped>,
    pub fleet_blocker: Option<FleetBlocker>,
    pub for_andy: Vec<ForAndy>,
    /// How each source read: `ideas` / `seats` → `ok` / `partial` / `missing`.
    pub sources: BTreeMap<String, String>,
}

/// Is a seat of `seat_size` big enough for a row of `row_size`? An unknown word on
/// either side is no (review < slice < mainline).
pub fn fits(seat_size: &str, row_size: &str) -> bool {
    let at = |x: &str| SIZES.iter().position(|s| *s == x);
    matches!((at(seat_size), at(row_size)), (Some(a), Some(b)) if a >= b)
}

/// The idea ledger as read: unreadable, read but `complete` not true (a line could
/// not be parsed, so a `--no` may be missing), or whole.
enum Ledger<'v> {
    Missing,
    Partial,
    Whole(&'v [Value]),
}

fn ledger(v: Option<&Value>) -> Ledger<'_> {
    match v.and_then(|v| v.get("ideas").and_then(Value::as_array).map(|a| (v, a))) {
        None => Ledger::Missing,
        Some((v, _)) if v.get("complete").and_then(Value::as_bool) != Some(true) => Ledger::Partial,
        Some((_, a)) => Ledger::Whole(a),
    }
}

/// What this module reads. `None` for a source means it could not be read: what
/// depends on it goes to `unknown` (a missing seat list is not "nobody free").
pub struct LocalInput<'a> {
    /// `cabin-plan/data.json`
    pub plan: &'a Value,
    /// `aib-idea list --all --json`
    pub ideas: Option<&'a Value>,
    /// `aib-drive seats --json`, already read
    pub seats: Option<&'a [SeatFact]>,
    /// Nodes and idea ids someone has claimed (act writes claims before it sends).
    pub claimed: &'a BTreeSet<String>,
    /// What unblocks the macOS queue group, when the caller knows ([`queue_front`]:
    /// the open PR first in the queue and its author); otherwise a fixed label and an
    /// `unknown` line.
    pub queue_unblock: Option<&'a str>,
    /// Hashes [`scope_text`]; `None` ⇒ a node with `approved.scope` cannot be checked
    /// and goes to `unknown`.
    pub scope_hash: Option<ScopeHash<'a>>,
    /// `seats` holds the plan's roster only (519d0 T1): an owner with no fact there is off
    /// the roster ⇒ skipped for the coordinator, not unknown (ebb36 f80a69ea M1).
    pub roster_applied: bool,
    pub as_of: &'a str,
}

struct Node<'a> {
    id: &'a str,
    status: &'a str,
    owner: &'a str,
    title: &'a str,
    approved: bool,
    scope: Option<&'a str>,
    after_raw: Vec<&'a str>,
    after: Vec<Result<Dep, String>>,
    /// `approved.at` and `updated` as written (`11-14 23:5x`), for how long it waited.
    approved_at: &'a str,
    updated: &'a str,
}

/// Whether the scope approved is still the scope written (IFACE r4 §C12).
enum ScopeCheck {
    Same,
    Unchecked,
    Changed,
    Unreadable,
}

fn scope_check(n: &Node<'_>, hash: Option<ScopeHash<'_>>) -> ScopeCheck {
    match (n.scope, hash) {
        (None, _) => ScopeCheck::Unchecked,
        (Some(_), None) => ScopeCheck::Unreadable,
        (Some(want), Some(h)) if h(&scope_text(n.title, &n.after_raw)) == want => ScopeCheck::Same,
        (Some(_), Some(_)) => ScopeCheck::Changed,
    }
}

/// Why a scope holds a node back (changed = a rule, `skipped`; not hashable = `unknown`),
/// or the `scope_unchecked` flag.
fn scope_gate(n: &Node<'_>, hash: Option<ScopeHash<'_>>) -> Result<bool, Hold> {
    match scope_check(n, hash) {
        ScopeCheck::Same => Ok(false),
        ScopeCheck::Unchecked => Ok(true),
        ScopeCheck::Changed => Err(Hold::Skipped(format!("{} 核准後範圍被改", n.id))),
        ScopeCheck::Unreadable => Err(Hold::Unknown(format!("{} 有 scope、雜湊讀不到", n.id))),
    }
}

/// A string field, or "" when missing or not a string.
fn s<'v>(v: &'v Value, k: &str) -> &'v str {
    v.get(k).and_then(Value::as_str).unwrap_or("")
}

fn nodes(plan: &Value) -> Vec<Node<'_>> {
    plan.get("nodes")
        .and_then(Value::as_array)
        .map(|ns| {
            ns.iter()
                .filter(|n| !s(n, "id").is_empty())
                .map(|n| Node {
                    id: s(n, "id"),
                    status: s(n, "status"),
                    owner: s(n, "owner"),
                    title: s(n, "title"),
                    approved: n.get("approved").is_some_and(|a| a.is_object()),
                    scope: n
                        .get("approved")
                        .and_then(|a| a.get("scope"))
                        .and_then(Value::as_str),
                    approved_at: n
                        .get("approved")
                        .and_then(|a| a.get("at"))
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                    updated: s(n, "updated"),
                    after_raw: n
                        .get("after")
                        .and_then(Value::as_array)
                        .map(|a| a.iter().map(|x| x.as_str().unwrap_or("")).collect())
                        .unwrap_or_default(),
                    after: n
                        .get("after")
                        .and_then(Value::as_array)
                        .map(|a| {
                            a.iter()
                                .map(|x| {
                                    let t = x.as_str().unwrap_or("");
                                    Dep::parse(t).ok_or_else(|| t.to_owned())
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Whether one `after` item is done: `node:` at [`DONE_STATUS`] (a node not in the
/// plan cannot be told), `pr:` / `skills-pr:` through `merged`.
fn dep_done(
    dep: &Dep,
    status_of: &BTreeMap<&str, &str>,
    merged: &dyn MergedSource,
) -> Option<bool> {
    match dep {
        Dep::Node(id) => status_of.get(id.as_str()).map(|s| *s == DONE_STATUS),
        _ => merged.merged(dep),
    }
}

/// How many unfinished nodes wait on `id`, directly or through other nodes.
fn dependents(id: &str, ns: &[Node<'_>]) -> usize {
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    let mut todo = vec![id.to_owned()];
    while let Some(cur) = todo.pop() {
        for n in ns {
            if n.status == DONE_STATUS || seen.contains(n.id) {
                continue;
            }
            if n.after
                .iter()
                .any(|d| matches!(d, Ok(Dep::Node(x)) if *x == cur))
            {
                seen.insert(n.id);
                todo.push(n.id.to_owned());
            }
        }
    }
    seen.len()
}

fn seat_of<'b>(seats: &'b [SeatFact], code: &str) -> Option<&'b SeatFact> {
    seats.iter().find(|s| s.seat == code)
}

/// Layers 5–6, the fleet blocker and the order for Andy.
pub fn rank_local(inp: &LocalInput<'_>, merged: &dyn MergedSource) -> LocalOut {
    let ns = nodes(inp.plan);
    let status_of: BTreeMap<&str, &str> = ns.iter().map(|n| (n.id, n.status)).collect();
    let mut rows: Vec<Row> = Vec::new();
    let mut unknown: Vec<Unknown> = Vec::new();
    let mut skipped: Vec<Skipped> = Vec::new();

    // layer 5: mainline next
    for n in &ns {
        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
            continue;
        }
        let scope_unchecked = match scope_gate(n, inp.scope_hash) {
            Ok(u) => u,
            Err(h) => {
                h.put(n.id, &mut unknown, &mut skipped);
                continue;
            }
        };
        let mut open: Vec<String> = Vec::new();
        let mut cannot: Vec<String> = Vec::new();
        // an `after` word not known is the plan's to fix, not unread (IFACE r9)
        let mut not_known: Vec<String> = Vec::new();
        for d in &n.after {
            match d {
                Err(t) => not_known.push(format!("after「{t}」不認得")),
                Ok(dep) => match dep_done(dep, &status_of, merged) {
                    Some(true) => {}
                    Some(false) => open.push(dep.text()),
                    None => cannot.push(format!("{} 合了沒讀不到", dep.text())),
                },
            }
        }
        if !open.is_empty() {
            continue;
        }
        if !cannot.is_empty() {
            unknown.push(Unknown {
                what: n.id.to_owned(),
                why: cannot.join(";"),
            });
            continue;
        }
        if !not_known.is_empty() {
            skipped.push(Skipped {
                what: n.id.to_owned(),
                reason: not_known.join(";"),
            });
            continue;
        }
        let owners = owner_seats(n.owner);
        if owners.is_empty() {
            skipped.push(Skipped {
                what: n.id.to_owned(),
                reason: format!("owner「{}」讀不出席碼(等統籌席判)", n.owner),
            });
            continue;
        }
        let Some(seats) = inp.seats else {
            unknown.push(Unknown {
                what: n.id.to_owned(),
                why: "席位事實讀不到".to_owned(),
            });
            continue;
        };
        // off the roster: an owner with no fact when seats hold the roster only
        let off = |o: &str| inp.roster_applied && seat_of(seats, o).is_none();
        // a node with more than one owner (the coordinator and seats off the roster not
        // counted) is the coordinator's to split, not handed to the first free one (519d0 T5)
        let devs = owners
            .iter()
            .filter(|o| !off(o) && seat_of(seats, o).is_none_or(|s| s.role != "coordinator"))
            .count();
        if devs > 1 {
            skipped.push(Skipped {
                what: n.id.to_owned(),
                reason: format!("owner 不只一席({}),等統籌席拆", owners.join(" ")),
            });
            continue;
        }
        let pick = owners.iter().find_map(|o| {
            seat_of(seats, o)
                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))
        });
        let Some(to) = pick else {
            if owners
                .iter()
                .any(|o| !off(o) && seat_of(seats, o).is_none_or(|s| s.dispatchable == "unknown"))
            {
                unknown.push(Unknown {
                    what: n.id.to_owned(),
                    why: format!("owner {} 能不能派讀不到", owners.join(" ")),
                });
            } else if owners
                .iter()
                .any(|o| seat_of(seats, o).is_some_and(|s| s.dispatchable == UNREAD_UNKNOWN))
            {
                skipped.push(Skipped {
                    what: n.id.to_owned(),
                    reason: format!("owner {}: unread unknown", owners.join(" ")),
                });
            } else if owners
                .iter()
                .any(|o| seat_of(seats, o).is_some_and(|s| s.dispatchable == LOST))
            {
                skipped.push(Skipped {
                    what: n.id.to_owned(),
                    reason: format!("owner {} 失聯,等統籌席改派", owners.join(" ")),
                });
            } else if owners.iter().any(|o| off(o)) {
                skipped.push(Skipped {
                    what: n.id.to_owned(),
                    reason: format!("owner {} 不在名冊,等統籌席改派", owners.join(" ")),
                });
            }
            continue;
        };
        let done_after: Vec<String> = n
            .after
            .iter()
            .filter_map(|d| d.as_ref().ok().map(Dep::text))
            .collect();
        rows.push(Row {
            rank: 0,
            layer: LAYER_MAINLINE,
            kind: "mainline".to_owned(),
            what: format!("{} {}", n.id, n.title),
            pr: None,
            node: Some(n.id.to_owned()),
            to: to.seat.clone(),
            why_him: format!("節點 owner;可派、size {}", to.size),
            why_now: if done_after.is_empty() {
                "已同意、沒有前置".to_owned()
            } else {
                format!("已同意、前置都好了({})", done_after.join(" "))
            },
            unblocks: dependents(n.id, &ns),
            waited_min: node_waited(n, inp.as_of),
            needs: "none".to_owned(),
            size: ROW_SIZE.to_owned(),
            data_at: inp.as_of.to_owned(),
            head: None,
            scope_unchecked,
        });
    }

    // layer 6: auto-approved ideas hung on a node still to do, S → A → B → C, one per
    // seat (IFACE r3 §1). A ledger that is not whole gives no layer 6 at all: the
    // line it could not read may be Andy's --no (043dd n-2c M2).
    let mut sources: BTreeMap<String, String> = BTreeMap::new();
    // layer 7 (tidy-scan duplicates) is not read by this version (IFACE r4 §C7)
    sources.insert("tidy_scan".to_owned(), "unsupported".to_owned());
    sources.insert(
        "seats".to_owned(),
        if inp.seats.is_some() { "ok" } else { "missing" }.to_owned(),
    );
    let in_five: BTreeSet<String> = rows.iter().filter_map(|r| r.node.clone()).collect();
    match ledger(inp.ideas) {
        Ledger::Missing => {
            sources.insert("ideas".to_owned(), "missing".to_owned());
            unknown.push(Unknown {
                what: "aib-idea".to_owned(),
                why: "建議帳讀不到".to_owned(),
            });
        }
        Ledger::Partial => {
            sources.insert("ideas".to_owned(), "partial".to_owned());
            unknown.push(Unknown {
                what: "aib-idea".to_owned(),
                why: "建議帳不完整(complete 不是 true),第 6 層與 for_andy 不出".to_owned(),
            });
        }
        Ledger::Whole(ideas) => {
            sources.insert("ideas".to_owned(), "ok".to_owned());
            let mut picked: BTreeMap<String, (usize, &Value, bool)> = BTreeMap::new();
            for i in ideas {
                // auto, and Andy has not said no (status is auto while pending, ok / no
                // once reviewed; 043dd n-2c M1)
                if s(i, "classification") != "auto"
                    || !matches!(s(i, "status"), "auto" | "ok")
                    || s(i, "human_review") == "no"
                {
                    continue;
                }
                let Some(g) = GRADES.iter().position(|g| *g == s(i, "grade")) else {
                    continue;
                };
                let (short, seat, node) = (s(i, "short"), s(i, "seat"), s(i, "node"));
                if !is_seat(seat) || inp.claimed.contains(short) {
                    continue;
                }
                if node.is_empty() {
                    skipped.push(Skipped {
                        what: short.to_owned(),
                        reason: "沒掛節點,不派".to_owned(),
                    });
                    continue;
                }
                match status_of.get(node) {
                    None => {
                        skipped.push(Skipped {
                            what: short.to_owned(),
                            reason: format!("掛的節點 {node} 不在進度樹"),
                        });
                        continue;
                    }
                    Some(st) if !IDEA_NODE_STATUSES.contains(st) => continue,
                    Some(_) => {}
                }
                if inp.claimed.contains(node) || in_five.contains(node) {
                    continue;
                }
                // an approved node is checked for a scope changed after the approval
                let scope_unchecked = match ns.iter().find(|x| x.id == node) {
                    Some(n) if n.approved => match scope_gate(n, inp.scope_hash) {
                        Ok(u) => u,
                        Err(h) => {
                            h.put(short, &mut unknown, &mut skipped);
                            continue;
                        }
                    },
                    _ => false,
                };
                let older = |a: &Value, b: &Value| {
                    a.get("at_ms").and_then(Value::as_u64) < b.get("at_ms").and_then(Value::as_u64)
                };
                match picked.get(seat) {
                    Some((pg, pi, _)) if *pg < g || (*pg == g && !older(i, pi)) => {}
                    _ => {
                        picked.insert(seat.to_owned(), (g, i, scope_unchecked));
                    }
                }
            }
            let mut six: Vec<(usize, Row)> = Vec::new();
            for (seat, (g, i, scope_unchecked)) in picked {
                if let Some(seats) = inp.seats {
                    match seat_of(seats, &seat) {
                        Some(f)
                            if f.role == "dev"
                                && f.dispatchable == "yes"
                                && fits(&f.size, ROW_SIZE) => {}
                        Some(f) if f.dispatchable == LOST => {
                            skipped.push(Skipped {
                                what: s(i, "short").to_owned(),
                                reason: format!("{seat} 失聯"),
                            });
                            continue;
                        }
                        Some(f) if f.dispatchable == UNREAD_UNKNOWN => {
                            skipped.push(Skipped {
                                what: s(i, "short").to_owned(),
                                reason: format!("{seat}: unread unknown"),
                            });
                            continue;
                        }
                        Some(f) if f.dispatchable != "unknown" => continue,
                        None if inp.roster_applied => {
                            skipped.push(Skipped {
                                what: s(i, "short").to_owned(),
                                reason: format!("{seat} 不在名冊"),
                            });
                            continue;
                        }
                        _ => {
                            unknown.push(Unknown {
                                what: s(i, "short").to_owned(),
                                why: format!("{seat} 能不能派讀不到"),
                            });
                            continue;
                        }
                    }
                } else {
                    unknown.push(Unknown {
                        what: s(i, "short").to_owned(),
                        why: "席位事實讀不到".to_owned(),
                    });
                    continue;
                }
                six.push((
                    g,
                    Row {
                        rank: 0,
                        layer: LAYER_IDEA,
                        kind: "idea".to_owned(),
                        what: format!("{} {}", s(i, "short"), s(i, "title")),
                        pr: None,
                        node: Some(s(i, "node").to_owned()),
                        to: seat.clone(),
                        why_him: "建議是他提的".to_owned(),
                        why_now: format!(
                            "自動同意優化 {} 級;節點 {} 還沒做",
                            GRADES[g],
                            s(i, "node")
                        ),
                        unblocks: 0,
                        waited_min: idea_waited(i, inp.as_of),
                        needs: "none".to_owned(),
                        size: ROW_SIZE.to_owned(),
                        data_at: inp.as_of.to_owned(),
                        head: None,
                        scope_unchecked,
                    },
                ));
            }
            six.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.what.cmp(&b.1.what)));
            rows.extend(six.into_iter().map(|(_, r)| r));
        }
    }

    // inside layer 5: more unblocked first, then the id
    rows.sort_by(|a, b| {
        a.layer
            .cmp(&b.layer)
            .then_with(|| {
                if a.layer == LAYER_MAINLINE {
                    b.unblocks.cmp(&a.unblocks)
                } else {
                    std::cmp::Ordering::Equal
                }
            })
            .then_with(|| {
                if a.layer == LAYER_MAINLINE {
                    a.what.cmp(&b.what)
                } else {
                    std::cmp::Ordering::Equal
                }
            })
    });
    for (k, r) in rows.iter_mut().enumerate() {
        r.rank = k + 1;
    }
    unknown.sort();
    skipped.sort();
    unknown.dedup();

    let for_andy = for_andy(inp, &ns);
    let fleet_blocker = fleet_blocker(&ns, &status_of, merged, inp.queue_unblock, &for_andy);
    // the queue group's unblock_by is the generic label: say it is not known (519d0 D1)
    if inp.queue_unblock.is_none()
        && fleet_blocker
            .as_ref()
            .is_some_and(|b| b.cause == "macos_queue")
    {
        unknown.push(Unknown {
            what: "全艦卡點 unblock_by".into(),
            why: "合併隊讀不到開著的 PR,不知道最前面是哪張".into(),
        });
        unknown.sort();
    }
    LocalOut {
        rows,
        unknown,
        skipped,
        fleet_blocker,
        for_andy,
        sources,
    }
}

/// Needs-human ideas Andy has not answered yet, most nodes unblocked first (the
/// idea's own node when it is not approved yet, plus every unfinished node waiting on
/// it), then the id. Nothing from a ledger that is not whole (043dd n-2c M2, L2).
fn for_andy(inp: &LocalInput<'_>, ns: &[Node<'_>]) -> Vec<ForAndy> {
    let Ledger::Whole(ideas) = ledger(inp.ideas) else {
        return Vec::new();
    };
    let mut out: Vec<ForAndy> = ideas
        .iter()
        .filter(|i| s(i, "classification") == "needs_human" && s(i, "human_review") == "pending")
        .map(|i| {
            let unblocks = match i.get("node").and_then(Value::as_str) {
                Some(id) => match ns.iter().find(|n| n.id == id) {
                    Some(n) if n.status != DONE_STATUS => {
                        usize::from(!n.approved) + dependents(id, ns)
                    }
                    _ => 0,
                },
                None => 0,
            };
            ForAndy {
                id: s(i, "short").to_owned(),
                title: s(i, "title").to_owned(),
                unblocks,
                kind: "needs_human".to_owned(),
            }
        })
        .collect();
    out.sort_by(|a, b| b.unblocks.cmp(&a.unblocks).then_with(|| a.id.cmp(&b.id)));
    out
}

/// Every unfinished node by what holds it; the biggest group wins (ties: the cause
/// order after → waiting_andy → macos_queue, then the item text).
fn fleet_blocker(
    ns: &[Node<'_>],
    status_of: &BTreeMap<&str, &str>,
    merged: &dyn MergedSource,
    queue_unblock: Option<&str>,
    for_andy: &[ForAndy],
) -> Option<FleetBlocker> {
    // key (cause order, item) → nodes
    let mut groups: BTreeMap<(u8, String), Vec<String>> = BTreeMap::new();
    for n in ns {
        if n.status.is_empty() || n.status == DONE_STATUS {
            continue;
        }
        for d in n.after.iter().filter_map(|d| d.as_ref().ok()) {
            if dep_done(d, status_of, merged) == Some(false) {
                groups
                    .entry((0, d.text()))
                    .or_default()
                    .push(n.id.to_owned());
            }
        }
        if n.status == ANDY_STATUS {
            groups
                .entry((1, String::new()))
                .or_default()
                .push(n.id.to_owned());
        }
        if n.status == QUEUED_STATUS {
            groups
                .entry((2, String::new()))
                .or_default()
                .push(n.id.to_owned());
        }
    }
    let ((cause, item), nodes) = groups
        .into_iter()
        .max_by(|a, b| a.1.len().cmp(&b.1.len()).then_with(|| b.0.cmp(&a.0)))?;
    let (cause, label, unblock_by) = match cause {
        0 => ("after", format!("等 {item}"), item.clone()),
        1 => (
            "waiting_andy",
            "等 Andy".to_owned(),
            for_andy
                .first()
                .map(|f| f.id.clone())
                .unwrap_or_else(|| "Andy 決定".to_owned()),
        ),
        _ => (
            "macos_queue",
            "macOS 測試排隊".to_owned(),
            queue_unblock.unwrap_or("合併隊前面的那張").to_owned(),
        ),
    };
    Some(FleetBlocker {
        cause: cause.to_owned(),
        label,
        count: nodes.len(),
        nodes,
        unblock_by,
    })
}

/// How long an intent with no `done` / `abandoned` holds its node (IFACE r9 3).
pub const CLAIM_HOLD_MS: i64 = 24 * 60 * 60 * 1000;

/// What act's intents ledger (`<root>/drive/intents.jsonl`) says is claimed (IFACE r9 3):
/// a `dispatch` / `open_review` line of `kind: intent` written in the last
/// [`CLAIM_HOLD_MS`] whose `key` no `done` / `abandoned` line closes. The claimed name is
/// the line's `node`, else the second part of the key (`action:<PR or node>:<head>:<seat>`,
/// [`crate::act::key`]). No file (`None`) is nothing claimed. Returns the set and how many
/// lines could not be read (the caller says the source is partial when that is not 0).
pub fn claimed_from_intents(text: Option<&str>, now_ms: i64) -> (BTreeSet<String>, usize) {
    let Some(text) = text else {
        return (BTreeSet::new(), 0);
    };
    let mut bad = 0;
    let mut open: BTreeMap<String, String> = BTreeMap::new();
    let mut closed: BTreeSet<String> = BTreeSet::new();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            bad += 1;
            continue;
        };
        let s = |k: &str| v.get(k).and_then(Value::as_str);
        let Some(key) = s("key") else {
            bad += 1;
            continue;
        };
        match s("kind") {
            Some("done" | "abandoned") => {
                closed.insert(key.to_owned());
            }
            Some("intent") => {
                let action = s("action").or_else(|| key.split(':').next());
                if !matches!(action, Some("dispatch" | "open_review")) {
                    continue;
                }
                let at = v.get("at_ms").and_then(Value::as_i64);
                if !at.is_some_and(|t| t <= now_ms && now_ms - t < CLAIM_HOLD_MS) {
                    continue;
                }
                let name = s("node").or_else(|| key.split(':').nth(1)).unwrap_or("");
                if !name.is_empty() {
                    open.insert(key.to_owned(), name.to_owned());
                }
            }
            _ => {}
        }
    }
    let set = open
        .into_iter()
        .filter(|(k, _)| !closed.contains(k))
        .map(|(_, n)| n)
        .collect();
    (set, bad)
}

/// The open PR first in the merge queue and who wrote it, for the macOS queue group's
/// `unblock_by` (519d0 D1): the lowest PR number among `(number, open, seat)` (numbers are
/// given out in the order PRs are opened), its seats from every branch that carries it
/// (`a/b` when two seats share one PR, `unknown` when none says). `None` when no PR is open.
pub fn queue_front<'s>(
    prs: impl IntoIterator<Item = (u64, bool, Option<&'s str>)>,
) -> Option<String> {
    let open: Vec<(u64, Option<&str>)> = prs
        .into_iter()
        .filter(|(_, o, _)| *o)
        .map(|(n, _, s)| (n, s))
        .collect();
    let first = open.iter().map(|(n, _)| *n).min()?;
    let seats: BTreeSet<&str> = open
        .iter()
        .filter(|(n, _)| *n == first)
        .map(|(_, s)| s.filter(|s| is_seat(s)).unwrap_or("unknown"))
        .collect();
    let seats: Vec<&str> = if seats.len() > 1 {
        seats.into_iter().filter(|s| *s != "unknown").collect()
    } else {
        seats.into_iter().collect()
    };
    Some(format!("#{first} {}", seats.join("/")))
}

/// Taipei is UTC+8 all year; the plan's times are written in it.
const TAIPEI_OFFSET_MS: i64 = 8 * 60 * 60 * 1000;

/// Days since 1970-01-01 of a civil date (proleptic Gregorian).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// `as_of` (`2026-11-14T16:00:00Z`, UTC) in milliseconds; anything else ⇒ `None`.
pub fn as_of_ms(as_of: &str) -> Option<i64> {
    let b = as_of.as_bytes();
    if b.len() < 20 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || !as_of.ends_with('Z') {
        return None;
    }
    let n = |r: std::ops::Range<usize>| as_of.get(r)?.parse::<i64>().ok();
    let days = days_from_civil(n(0..4)?, n(5..7)?, n(8..10)?);
    Some(((days * 24 + n(11..13)?) * 60 + n(14..16)?) * 60_000 + n(17..19)? * 1000)
}

/// A plan time (`MM-DD`, `MM-DD HH:MM`, a digit written `x` as in `23:5x`), Taipei, in
/// milliseconds, in `now`'s year. An `x` is read as 9 and a date alone as 23:59, the latest
/// it can mean, so a wait worked out from it is never more than it was (C13: a lower
/// bound, no false alarm). Later than now: up to a day ahead is what that rounding does to
/// today ⇒ now (waited 0); a December time read in January is last year's; any other
/// future is not known ⇒ `None` (043dd 55e958c3 M1: today's `11-15` was read as last year,
/// a year of waiting and a false starving).
pub fn plan_time_ms(text: &str, now_ms: i64) -> Option<i64> {
    let t = text.trim();
    let (date, time) = match t.split_once(' ') {
        Some((d, h)) => (d, h.trim()),
        None => (t, "23:59"),
    };
    let (mo, da) = date.split_once('-')?;
    let (hh, mi) = time.split_once(':')?;
    let num = |x: &str, max: i64| -> Option<i64> {
        if x.len() != 2 || !x.bytes().all(|c| c.is_ascii_digit() || c == b'x') {
            return None;
        }
        let v = x.replace('x', "9").parse::<i64>().ok()?;
        Some(v.min(max))
    };
    let (mo, da) = (mo.parse::<i64>().ok()?, da.parse::<i64>().ok()?);
    if !(1..=12).contains(&mo) || !(1..=31).contains(&da) {
        return None;
    }
    let (hh, mi) = (num(hh, 23)?, num(mi, 59)?);
    let days_now = (now_ms + TAIPEI_OFFSET_MS).div_euclid(86_400_000);
    // the year and month of now in Taipei (days_from_civil inverted)
    let (y_now, m_now) = {
        let z = days_now + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        (yoe + era * 400 + i64::from(m <= 2), m)
    };
    let at =
        |y: i64| ((days_from_civil(y, mo, da) * 24 + hh) * 60 + mi) * 60_000 - TAIPEI_OFFSET_MS;
    let t = at(y_now);
    if t <= now_ms {
        return Some(t);
    }
    if t - now_ms <= 86_400_000 {
        return Some(now_ms);
    }
    (m_now == 1 && mo == 12).then(|| at(y_now - 1))
}

/// Minutes a layer-5 node has waited (C13): since the later of `approved.at` and
/// `updated` (a status change after the approval starts the wait again); neither read,
/// or `as_of` not a time ⇒ `None`.
fn node_waited(n: &Node<'_>, as_of: &str) -> Option<u64> {
    let now = as_of_ms(as_of)?;
    let since = [n.approved_at, n.updated]
        .iter()
        .filter_map(|t| plan_time_ms(t, now))
        .max()?;
    u64::try_from((now - since) / 60_000).ok()
}

/// Minutes a layer-6 idea has waited (C13): since its review (the approval), else since
/// it was written.
fn idea_waited(i: &Value, as_of: &str) -> Option<u64> {
    let now = as_of_ms(as_of)?;
    let at = i
        .get("review")
        .and_then(|r| r.get("at_ms"))
        .or_else(|| i.get("at_ms"))
        .and_then(Value::as_i64)?;
    u64::try_from((now - at) / 60_000).ok()
}
