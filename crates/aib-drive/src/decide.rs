//! `decide(snapshot) -> Vec<Intent>`: the aib-drive decision table
//! (DESIGN-aib-drive-decide-a9136-1110.md, DECISION-aib-drive-v1 item 8).
//!
//! A pure function. It reads only the snapshot: no gh, no git, no files, no
//! clock. The same snapshot gives the same intents, in the same order.
//!
//! - Every intent carries its preconditions ([`Pre`]); the executor re-reads
//!   them right before acting and drops the intent if any differs. It never
//!   "updates the value and acts anyway".
//! - Anything the snapshot marks [`Known::Unknown`] blocks the writes that
//!   depend on it. Unknown is never read as empty, off or green.
//! - At most one shared write ([`Intent::is_shared_write`]: merge, rerun,
//!   cancel) per call: the executor takes a new snapshot after it.
//! - Review and acceptance first lines come from `aib_core::firstline`. Who
//!   wrote a file (`by`) is what its file name claims, not an identity: it is
//!   only compared with the commit authors (B8 ledger records will make it
//!   stronger later).

use aib_core::firstline::{Review, Scope, Verdict, Verify};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub type Sha = String;
pub type Seat = String;

/// A writer verified as someone who authored none of the commits. An
/// unverified writer (`Unknown`) never counts (fail closed).
fn trusted_non_author(by: &Known<Seat>, authors: &BTreeSet<Seat>) -> bool {
    matches!(by, Known::Known(s) if !authors.contains(s))
}

/// A value the snapshot layer read in full, or why it could not.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Known<T> {
    Known(T),
    Unknown(String),
}

impl<T> Known<T> {
    pub fn get(&self) -> Option<&T> {
        match self {
            Known::Known(v) => Some(v),
            Known::Unknown(_) => None,
        }
    }
}

/// Who holds which role (from the rules on main, not from a message).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Roles {
    /// 艦長: rules on accepting a failed acceptance, stopping work, freezes.
    pub captain: Seat,
    /// 收件席: picks which head of an integration is collected.
    pub collector: Seat,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Snapshot {
    pub repo: String,
    pub roles: Roles,
    /// `Known(None)` = no freeze; `Known(Some(tag))` = frozen until that tag.
    pub freeze: Known<Option<String>>,
    /// Required check names, read from the rules on main. An empty list is
    /// treated as unknown (bb7dc 121b1cbf H1): nothing merges on it.
    pub required_cells: Known<Vec<String>>,
    /// The version of the rules on main the snapshot read (bound into
    /// [`Pre`]); unknown = nothing merges.
    pub rules_version: Known<String>,
    /// The merge lock is free and the gap since the last merge has passed.
    pub merge_slot_free: Known<bool>,
    /// Seats that should not get new work now (unread urgent mail, or a
    /// status line older than the limit): they get a nudge instead.
    pub busy_seats: BTreeSet<Seat>,
    pub prs: Vec<Pr>,
    pub rulings: Vec<Ruling>,
    /// Actions the journal already holds (submitted or confirmed): never
    /// produced again.
    pub done: BTreeSet<IdemKey>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Pr {
    pub number: u64,
    /// The seat that owns the PR (gets nudges).
    pub owner: Seat,
    pub head: Sha,
    pub head_ref: String,
    pub base_ref: String,
    pub base_head: Sha,
    /// For a stacked PR, the base it must have.
    pub expected_base: Option<String>,
    pub draft: bool,
    pub blocks_release: bool,
    /// Seconds since it became ready (older first).
    pub waiting_secs: u64,
    /// The head the journal saw last time, if any.
    pub prev_head: Option<Sha>,
    /// Commits `base..head`, oldest first.
    pub commits: Known<Vec<Sha>>,
    /// Every seat that authored a commit in `base..head`, the conflict
    /// resolution included (mapped by the snapshot layer).
    pub authors: Known<BTreeSet<Seat>>,
    /// It changes workflows / aib-drive / gates / RULES / the ledger.
    pub touches_guarded: Known<bool>,
    pub cells: Known<Vec<Cell>>,
    /// Runs of earlier heads still queued or running.
    pub stale_runs: Vec<u64>,
    pub knives: Known<KnifeScan>,
    pub reviews: Vec<ReviewLine>,
    pub verifies: Vec<VerifyLine>,
    /// `needs-verify:<kind>` labels.
    pub needs_verify: Vec<String>,
    /// An earlier head whose acceptance still holds for this one (no source
    /// change, or a clean merge of main), worked out by the snapshot layer.
    pub verify_carry: Option<Sha>,
    /// An integration PR: the originals it collected.
    pub integ_parts: Option<Known<Vec<IntegPart>>>,
    /// `aib-release trial` against the other PRs of this release.
    pub trial: Known<Vec<TrialHit>>,
    /// The seat whose turn it is to review, if no review was asked yet.
    pub reviewer: Option<Seat>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Cell {
    pub name: String,
    pub run: u64,
    pub state: CellState,
    /// The run started after the PR became ready (not a draft run).
    pub after_ready: bool,
    /// We (freeze / replaced head) cancelled it.
    pub cancelled_by_us: bool,
    /// This head was already re-run once for this cell.
    pub rerun_done: bool,
    /// For a failure: the red-light class (test-classify DECISION).
    pub class: Option<RedClass>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CellState {
    Success,
    Failure,
    Cancelled,
    Skipped,
    Neutral,
    Pending,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RedClass {
    Product,
    Runner,
    Unclassified,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum KnifeScan {
    Ok,
    Misses,
    Survives,
    Aborted,
    LogMissing,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IntegPart {
    pub pr: u64,
    pub collected: Sha,
    pub now: Sha,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TrialHit {
    pub other: u64,
    pub files: Vec<String>,
}

/// A conforming review first line (RULES §1), owned.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReviewLine {
    /// Who wrote it: `Known` only when the snapshot layer verified the
    /// writer (B8 ledger record); a file name's claim is `Unknown`. Only a
    /// `Known` non-author counts (bb7dc 121b1cbf M1).
    pub by: Known<Seat>,
    /// The review record's id / digest: bound into [`Pre`].
    pub evidence: String,
    pub head: Sha,
    pub pass: bool,
    /// H / M / L / N.
    pub counts: [u32; 4],
    /// `None` = 全文; `Some(from)` = 增量 from..head.
    pub from: Option<Sha>,
    /// Evidence ids of earlier reviews whose every H / M this review lists,
    /// one by one, as closed. Set by the snapshot layer from the body; the
    /// first line cannot say it. See [`closed_by_increment`].
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub closes: Vec<String>,
}

impl ReviewLine {
    /// Only a line that follows §1 in full becomes a `ReviewLine`; anything
    /// else is left out (the snapshot layer notes it and nudges the author).
    pub fn from_parsed(by: Known<Seat>, evidence: &str, r: &Review<'_>) -> Option<Self> {
        if !r.conforms() {
            return None;
        }
        Some(Self {
            by,
            evidence: evidence.to_owned(),
            head: r.head.to_owned(),
            pass: r.verdict == Verdict::Pass,
            counts: [r.counts.high, r.counts.medium, r.counts.low, r.counts.nit],
            from: match r.scope {
                Scope::Incremental { from, .. } => Some(from.to_owned()),
                Scope::Full | Scope::Other(_) => None,
            },
            closes: Vec::new(),
        })
    }

    /// Pass with no high or medium finding (whatever head).
    pub fn passes(&self) -> bool {
        self.pass && self.counts[0] == 0 && self.counts[1] == 0
    }

    /// [`ReviewLine::passes`], for exactly this head.
    pub fn mergeable_at(&self, head: &str) -> bool {
        self.head == head && self.passes()
    }
}

/// An acceptance first line, owned.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VerifyLine {
    /// As [`ReviewLine::by`].
    pub by: Known<Seat>,
    pub evidence: String,
    /// From the file name: `VERIFY-<kind>-…`.
    pub kind: String,
    pub head: Sha,
    pub pass: bool,
    /// `None` = 無.
    pub not_checked: Option<String>,
}

impl VerifyLine {
    pub fn from_parsed(by: Known<Seat>, evidence: &str, kind: &str, v: &Verify<'_>) -> Self {
        Self {
            by,
            evidence: evidence.to_owned(),
            kind: kind.to_owned(),
            head: v.head.to_owned(),
            pass: v.verdict == Verdict::Pass,
            not_checked: v.not_checked.map(str::to_owned),
        }
    }
}

/// What a question is about; [`ruler`] says who may answer it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum AskTopic {
    /// Acceptance failed, or passed with items not checked: take it anyway?
    VerifyAccept,
    StopWork,
    /// Which head of an integration is collected (same tree, other head).
    CollectHead,
    Freeze,
    /// A red cell nobody can classify (machine pits ⇒ c477a decides).
    Unclassified,
    IntegPartMoved,
    ReviewScope,
    RulesConflict,
    /// RULES §14 "undecided": the asking seat decides, with a DECISION file.
    Undecided,
    /// Merge order: f457 PRs by the collector, 9320 PRs by the captain.
    MergeOrder,
    /// Lease pre-emption / long reservation outside the D4 rules.
    LeaseOverride,
    SourceDown,
    /// Money, production writes, customer text, deleting another seat's
    /// tree, opening / closing seats, repo moves, signing, release notes,
    /// postponing what Andy named, adding drive permissions.
    AndyOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Ruler {
    Captain,
    Collector,
    /// The seat that asked (RULES §14), only with a DECISION file.
    Asker,
    /// The machine owner (c477a).
    MachineOwner,
    /// Andy, in his own window with a Touch ID receipt.
    Andy,
}

/// The 誰能裁 table (DESIGN §3).
pub fn ruler(topic: AskTopic) -> Ruler {
    match topic {
        AskTopic::VerifyAccept
        | AskTopic::StopWork
        | AskTopic::Freeze
        | AskTopic::Unclassified
        | AskTopic::ReviewScope
        | AskTopic::RulesConflict
        | AskTopic::SourceDown
        | AskTopic::MergeOrder => Ruler::Captain,
        AskTopic::CollectHead | AskTopic::IntegPartMoved => Ruler::Collector,
        AskTopic::Undecided => Ruler::Asker,
        AskTopic::LeaseOverride => Ruler::MachineOwner,
        AskTopic::AndyOnly => Ruler::Andy,
    }
}

/// A ruling as the B8 ledger records it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Ruling {
    pub topic: AskTopic,
    /// The ledger's writer identity, not text in a message.
    pub by: Seat,
    pub pr: Option<u64>,
    /// A ruling is bound to a head: a new head needs a new ruling.
    pub head: Option<Sha>,
    pub decision_file: Option<String>,
    /// Andy's Touch ID receipt for this ruling exists and is in force.
    pub andy_receipt: bool,
}

impl Ruling {
    /// The ruling was made by whoever [`ruler`] names for its topic.
    pub fn valid(&self, roles: &Roles) -> bool {
        match ruler(self.topic) {
            Ruler::Captain => self.by == roles.captain,
            Ruler::Collector => self.by == roles.collector,
            Ruler::Asker => self.decision_file.is_some(),
            Ruler::MachineOwner => self.by == "c477a",
            Ruler::Andy => self.andy_receipt,
        }
    }
}

/// What the executor re-checks right before acting.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Pre {
    pub pr: u64,
    pub head: Sha,
    pub base_head: Sha,
    /// The freeze must still be off.
    pub freeze_off: bool,
    /// The rules version the decision was made on.
    pub rules_version: String,
    /// Evidence ids of the verified reviews / acceptance runs it relied on.
    pub reviews: Vec<String>,
    pub verifies: Vec<String>,
    /// Every cell result it saw: (name, run id).
    pub runs: Vec<(String, u64)>,
}

/// Never produce the same action twice (journal key).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct IdemKey {
    pub kind: String,
    pub target: String,
    pub head: Sha,
    pub role: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Intent {
    Merge {
        pre: Pre,
    },
    Rerun {
        run: u64,
        pre: Pre,
    },
    Cancel {
        run: u64,
        why: String,
        pre: Pre,
    },
    AskReview {
        to: Seat,
        /// `None` = the whole branch; `Some(from)` = from..head.
        from: Option<Sha>,
        pre: Pre,
    },
    AskVerify {
        to: Option<Seat>,
        kind: String,
        pre: Pre,
    },
    Nudge {
        seat: Seat,
        pr: u64,
        about: String,
    },
    /// Mark the reviews / acceptance of an old head stale (journal + board).
    Stale {
        pr: u64,
        old: Sha,
        new: Sha,
    },
    Ask {
        topic: AskTopic,
        who: Ruler,
        pr: Option<u64>,
        about: String,
    },
    Note {
        pr: Option<u64>,
        text: String,
    },
}

impl Intent {
    /// Writes that change shared state: one per round, then a new snapshot.
    pub fn is_shared_write(&self) -> bool {
        matches!(
            self,
            Intent::Merge { .. } | Intent::Rerun { .. } | Intent::Cancel { .. }
        )
    }

    pub fn key(&self) -> Option<IdemKey> {
        let k = |kind: &str, target: String, head: &str, role: &str| {
            Some(IdemKey {
                kind: kind.into(),
                target,
                head: head.into(),
                role: role.into(),
            })
        };
        match self {
            Intent::Merge { pre } => k("merge", pre.pr.to_string(), &pre.head, ""),
            Intent::Rerun { run, pre } => k("rerun", run.to_string(), &pre.head, ""),
            Intent::Cancel { run, pre, .. } => k("cancel", run.to_string(), &pre.head, ""),
            Intent::AskReview { to, pre, .. } => k("review", pre.pr.to_string(), &pre.head, to),
            Intent::AskVerify { kind, pre, .. } => k("verify", pre.pr.to_string(), &pre.head, kind),
            Intent::Stale { pr, new, .. } => k("stale", pr.to_string(), new, ""),
            Intent::Nudge { seat, pr, about } => k("nudge", format!("{pr}:{about}"), "", seat),
            Intent::Ask {
                topic, pr, about, ..
            } => k(
                "ask",
                format!("{}:{topic:?}:{about}", pr.unwrap_or(0)),
                "",
                "",
            ),
            Intent::Note { .. } => None,
        }
    }
}

/// Why one PR is not merged this round (one entry per reason).
struct Gate {
    blocked: bool,
    out: Vec<Intent>,
}

impl Gate {
    fn block(&mut self) {
        self.blocked = true;
    }
    fn note(&mut self, pr: u64, text: impl Into<String>) {
        self.out.push(Intent::Note {
            pr: Some(pr),
            text: text.into(),
        });
    }
    fn push(&mut self, i: Intent) {
        self.out.push(i);
    }
}

fn pre(s: &Snapshot, pr: &Pr, freeze_off: bool) -> Pre {
    let mut reviews: Vec<String> = pr
        .reviews
        .iter()
        .filter(|r| matches!(r.by, Known::Known(_)))
        .map(|r| r.evidence.clone())
        .collect();
    reviews.sort();
    let mut verifies: Vec<String> = pr
        .verifies
        .iter()
        .filter(|v| matches!(v.by, Known::Known(_)))
        .map(|v| v.evidence.clone())
        .collect();
    verifies.sort();
    let mut runs: Vec<(String, u64)> = pr
        .cells
        .get()
        .map(|c| c.iter().map(|c| (c.name.clone(), c.run)).collect())
        .unwrap_or_default();
    runs.sort();
    Pre {
        pr: pr.number,
        head: pr.head.clone(),
        base_head: pr.base_head.clone(),
        freeze_off,
        rules_version: s.rules_version.get().cloned().unwrap_or_default(),
        reviews,
        verifies,
        runs,
    }
}

/// D12 / D13: how far `base..head` is covered by a chain of passing
/// reviews (no H / M) from verified non-authors. A full review at head X covers every commit up
/// to X; an incremental review `from..to` extends the chain only if `from`
/// is already covered. Returns the index of the last covered commit.
fn covered_to(commits: &[Sha], reviews: &[&ReviewLine]) -> Option<usize> {
    let idx = |s: &str| commits.iter().position(|c| c == s);
    let mut to: Option<usize> = None;
    loop {
        let mut grew = false;
        for r in reviews {
            let Some(end) = idx(&r.head) else { continue };
            let ok = match &r.from {
                None => true,
                Some(f) => matches!((idx(f), to), (Some(i), Some(t)) if i <= t),
            };
            if ok && to.is_none_or(|t| end > t) {
                to = Some(end);
                grew = true;
            }
        }
        if !grew {
            return to;
        }
    }
}

/// D20 (a63ab 11-10 02:02): a review that did not pass at an old head is
/// closed by an incremental pass `old..new` only when the same verified
/// writer wrote both and the increment lists each old H / M as closed
/// (`closes` names the old review). A different reviewer, or an increment
/// that does not list them, leaves the old review failed.
fn closed_by_increment(old: &ReviewLine, others: &[&ReviewLine]) -> bool {
    let Known::Known(writer) = &old.by else {
        return false;
    };
    others.iter().any(|r| {
        r.passes()
            && r.by.get() == Some(writer)
            && r.from.as_ref() == Some(&old.head)
            && r.closes.contains(&old.evidence)
    })
}

fn gate_pr(s: &Snapshot, pr: &Pr, freeze_off: bool) -> Gate {
    let n = pr.number;
    let mut g = Gate {
        blocked: false,
        out: Vec::new(),
    };
    let p = pre(s, pr, freeze_off);

    // D2 a new head: old reviews / acceptance are stale, old runs give way
    if let Some(old) = pr.prev_head.as_ref().filter(|o| **o != pr.head) {
        g.push(Intent::Stale {
            pr: n,
            old: old.clone(),
            new: pr.head.clone(),
        });
        for run in &pr.stale_runs {
            g.push(Intent::Cancel {
                run: *run,
                why: "head_replaced".into(),
                pre: p.clone(),
            });
        }
    }
    // D4 drafts are only watched
    if pr.draft {
        g.note(n, "draft: not reviewed, not merged");
        g.block();
        return g;
    }
    // D5 guarded paths are merged by a person
    match pr.touches_guarded {
        Known::Known(false) => {}
        Known::Known(true) => {
            g.note(
                n,
                "touches workflows / aib-drive / gates / RULES / ledger: merged by hand",
            );
            g.block();
        }
        Known::Unknown(ref why) => {
            g.note(n, format!("changed paths unknown ({why})"));
            g.block();
        }
    }
    // D16 integration branch prefix
    if pr.integ_parts.is_some()
        && !(pr.head_ref.starts_with("int/") || pr.head_ref.starts_with("release/"))
    {
        g.note(
            n,
            "integration branch must be int/… (integ/ skips macOS cells)",
        );
        g.block();
    }
    // D18 stacked base
    if let Some(want) = &pr.expected_base {
        if *want != pr.base_ref {
            g.note(n, format!("base is {}, expected {want}", pr.base_ref));
            g.block();
        }
    }
    // D15 the originals of an integration did not move
    if let Some(parts) = &pr.integ_parts {
        match parts {
            Known::Known(v) => {
                for part in v.iter().filter(|x| x.collected != x.now) {
                    g.block();
                    g.push(Intent::Ask {
                        topic: AskTopic::IntegPartMoved,
                        who: ruler(AskTopic::IntegPartMoved),
                        pr: Some(n),
                        about: format!("#{} moved {}..{}", part.pr, part.collected, part.now),
                    });
                }
            }
            Known::Unknown(why) => {
                g.note(n, format!("integration parts unknown ({why})"));
                g.block();
            }
        }
    }
    gate_cells(s, pr, &p, &mut g);
    // D10 knife log scan
    match &pr.knives {
        Known::Known(KnifeScan::Ok) => {}
        Known::Known(KnifeScan::LogMissing) | Known::Unknown(_) => {
            g.block();
            g.push(Intent::Ask {
                topic: AskTopic::Unclassified,
                who: ruler(AskTopic::Unclassified),
                pr: Some(n),
                about: "knife log missing".into(),
            });
        }
        Known::Known(k) => {
            g.block();
            g.push(Intent::Nudge {
                seat: pr.owner.clone(),
                pr: n,
                about: format!("knife {k:?}"),
            });
        }
    }
    gate_reviews(s, pr, &p, &mut g);
    gate_verifies(s, pr, &p, &mut g);
    // D17 collisions with other PRs of this release
    if let Known::Known(hits) = &pr.trial {
        for h in hits {
            g.note(
                n,
                format!(
                    "collides with #{} on {}: stack on it, collected by the integration seat",
                    h.other,
                    h.files.join(", ")
                ),
            );
        }
    }
    g
}

/// D6–D9.
fn gate_cells(s: &Snapshot, pr: &Pr, p: &Pre, g: &mut Gate) {
    let n = pr.number;
    let (Known::Known(required), Known::Known(cells)) = (&s.required_cells, &pr.cells) else {
        g.note(n, "required cells or cell states unknown");
        g.block();
        return;
    };
    if required.is_empty() {
        g.note(n, "required cell list is empty: treated as unknown");
        g.block();
        return;
    }
    for name in required {
        let mine: Vec<&Cell> = cells.iter().filter(|c| c.name == *name).collect();
        let [c] = mine.as_slice() else {
            g.note(
                n,
                format!("{name}: {} results (want exactly one)", mine.len()),
            );
            g.block();
            continue;
        };
        match c.state {
            CellState::Success if c.after_ready => {}
            CellState::Success => {
                g.note(
                    n,
                    format!("{name}: only a draft run (draft_run_only): re-label full-ci"),
                );
                g.block();
            }
            CellState::Pending => g.block(),
            CellState::Skipped | CellState::Neutral => {
                g.note(n, format!("{name}: {:?} is not a pass", c.state));
                g.block();
            }
            CellState::Cancelled => {
                g.block();
                if !c.cancelled_by_us && !c.rerun_done {
                    g.push(Intent::Rerun {
                        run: c.run,
                        pre: p.clone(),
                    });
                }
            }
            CellState::Failure => {
                g.block();
                match c.class {
                    Some(RedClass::Product) => g.push(Intent::Nudge {
                        seat: pr.owner.clone(),
                        pr: n,
                        about: format!("{name} red"),
                    }),
                    Some(RedClass::Runner) if !c.rerun_done => g.push(Intent::Rerun {
                        run: c.run,
                        pre: p.clone(),
                    }),
                    _ => g.push(Intent::Ask {
                        topic: AskTopic::Unclassified,
                        who: ruler(AskTopic::Unclassified),
                        pr: Some(n),
                        about: format!("{name} run {}", c.run),
                    }),
                }
            }
        }
    }
}

/// D11–D13.
fn gate_reviews(s: &Snapshot, pr: &Pr, p: &Pre, g: &mut Gate) {
    let n = pr.number;
    let Known::Known(authors) = &pr.authors else {
        g.note(n, "commit authors unknown: nobody counts as a non-author");
        g.block();
        return;
    };
    let Known::Known(commits) = &pr.commits else {
        g.note(n, "commit list unknown");
        g.block();
        return;
    };
    if pr.reviews.iter().any(|r| matches!(r.by, Known::Unknown(_))) {
        g.note(n, "a review's writer is not verified: it does not count");
    }
    let others: Vec<&ReviewLine> = pr
        .reviews
        .iter()
        .filter(|r| trusted_non_author(&r.by, authors))
        .collect();
    let at_head: Vec<&&ReviewLine> = others.iter().filter(|r| r.head == pr.head).collect();
    if at_head.iter().any(|r| !r.mergeable_at(&pr.head)) {
        g.note(n, "a review of this head did not pass (or left H / M)");
        g.block();
        return;
    }
    let last = commits.len().checked_sub(1);
    // bb7dc 121b1cbf H2: only reviews that pass with no H / M make the chain,
    // plus (D20) an old failed one its own writer closed in an increment
    let chain: Vec<&ReviewLine> = others
        .iter()
        .copied()
        .filter(|r| r.passes() || closed_by_increment(r, &others))
        .collect();
    let covered = covered_to(commits, &chain);
    if last.is_some() && covered == last && !at_head.is_empty() {
        return;
    }
    g.block();
    let to = pr
        .reviews
        .iter()
        .rev()
        .find(|r| trusted_non_author(&r.by, authors))
        .and_then(|r| r.by.get().cloned())
        .or_else(|| pr.reviewer.clone().filter(|r| !authors.contains(r)));
    let Some(to) = to else {
        g.note(n, "no reviewer to ask");
        return;
    };
    let from = covered.and_then(|i| commits.get(i).cloned());
    if s.busy_seats.contains(&to) {
        g.push(Intent::Nudge {
            seat: to,
            pr: n,
            about: "review waiting".into(),
        });
    } else {
        g.push(Intent::AskReview {
            to,
            from,
            pre: p.clone(),
        });
    }
}

/// D14.
fn gate_verifies(s: &Snapshot, pr: &Pr, p: &Pre, g: &mut Gate) {
    let n = pr.number;
    let authors = pr.authors.get();
    for kind in &pr.needs_verify {
        let holds = |h: &Sha| *h == pr.head || pr.verify_carry.as_ref() == Some(h);
        let lines: Vec<&VerifyLine> = pr
            .verifies
            .iter()
            .filter(|v| v.kind == *kind && holds(&v.head))
            .filter(|v| authors.is_some_and(|a| trusted_non_author(&v.by, a)))
            .collect();
        if lines.is_empty() {
            g.block();
            g.push(Intent::AskVerify {
                to: None,
                kind: kind.clone(),
                pre: p.clone(),
            });
            continue;
        }
        if lines.iter().all(|v| v.pass && v.not_checked.is_none()) {
            continue;
        }
        let accepted = s.rulings.iter().any(|r| {
            r.topic == AskTopic::VerifyAccept
                && r.pr == Some(n)
                && r.head.as_deref() == Some(pr.head.as_str())
                && r.valid(&s.roles)
        });
        if !accepted {
            g.block();
            g.push(Intent::Ask {
                topic: AskTopic::VerifyAccept,
                who: ruler(AskTopic::VerifyAccept),
                pr: Some(n),
                about: format!("acceptance {kind} failed or left items unchecked"),
            });
        }
    }
}

/// The decision table. See the module docs for the rules every row keeps.
pub fn decide(s: &Snapshot) -> Vec<Intent> {
    let mut out = Vec::new();
    // D3 / D1 the freeze must be known to be off before anything merges
    let freeze_off = match &s.freeze {
        Known::Known(None) => true,
        Known::Known(Some(tag)) => {
            out.push(Intent::Note {
                pr: None,
                text: format!("frozen until {tag}: nothing merges, nothing re-runs"),
            });
            false
        }
        Known::Unknown(why) => {
            out.push(Intent::Note {
                pr: None,
                text: format!("freeze state unknown ({why}): nothing merges"),
            });
            false
        }
    };
    let slot_free = matches!(s.merge_slot_free, Known::Known(true))
        && matches!(s.rules_version, Known::Known(_));
    let mut prs: Vec<&Pr> = s.prs.iter().collect();
    prs.sort_by(|a, b| {
        b.blocks_release
            .cmp(&a.blocks_release)
            .then(b.waiting_secs.cmp(&a.waiting_secs))
            .then(a.number.cmp(&b.number))
    });
    for pr in prs {
        let g = gate_pr(s, pr, freeze_off);
        let blocked = g.blocked;
        for i in g.out {
            // reruns wait for the freeze too (the freeze cancelled them)
            if matches!(i, Intent::Rerun { .. }) && !freeze_off {
                continue;
            }
            out.push(i);
        }
        if !blocked && freeze_off && slot_free {
            out.push(Intent::Merge {
                pre: pre(s, pr, freeze_off),
            });
        }
    }
    // the journal already holds it ⇒ not again; one shared write per round
    let mut seen = BTreeSet::new();
    let mut shared = false;
    out.retain(|i| {
        if let Some(k) = i.key() {
            if s.done.contains(&k) || !seen.insert(k) {
                return false;
            }
        }
        if i.is_shared_write() {
            if shared {
                return false;
            }
            shared = true;
        }
        true
    });
    out
}
