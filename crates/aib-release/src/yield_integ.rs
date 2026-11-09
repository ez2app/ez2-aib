//! S2 cross-PR yield (plan v3 9.2 #2; a9136 writes, bb7dc reviews).
//!
//! When an integration PR takes over original PRs, the originals' queued /
//! running CI runs are cancelled so the integration PR gets the macOS
//! runners, and each original gets the `in-integ` label. ci.yml reacts only
//! to the labelling event itself (that one run skips macOS and its macOS
//! cells fail as "not a verdict"); the label has no effect on later pushes,
//! on re-opens or on integration PRs. Only cancels, never marks anything
//! green.
//!
//! Before touching an original, every one of these must hold (else that PR
//! is skipped, or everything is refused, with the reason):
//! - the integration PR is open, its head is a full SHA, and each required
//!   workflow file has a `pull_request` run on that head that is not
//!   cancelled and that GitHub ties to the integration PR alone ([`owner`]);
//! - the original is open, its head is EXACTLY the full SHA the caller says
//!   was integrated (no prefix match), and that SHA is contained in the
//!   integration head (GitHub compare: identical or ahead);
//! - only its own unfinished runs ([`Owner::Ours`]); a run on the same commit
//!   that cannot be tied to one PR makes the original UNKNOWN.
//!
//! All of it is checked again right before every write ([`apply`]). A read
//! that fails is UNKNOWN (or refuses the whole yield when it is the
//! integration PR's), never "nothing to cancel"; any UNKNOWN stops the whole
//! batch. A cancel that fails is reported as failed, never as yielded.

use std::fmt;

/// A pull request as GitHub answers it now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pull {
    pub number: u64,
    /// `open` or `closed`.
    pub state: String,
    pub head_sha: String,
    /// The head branch name (`head.ref`).
    pub head_ref: String,
    /// The head repository (`head.repo.full_name`); `None` when GitHub has
    /// none (deleted fork).
    pub head_repo: Option<String>,
    pub labels: Vec<String>,
}

/// One workflow run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Run {
    pub id: u64,
    /// Workflow file (`.github/workflows/ci.yml`): the identity; the display
    /// name can be shared by two files.
    pub path: String,
    pub event: String,
    /// `queued`, `in_progress`, `waiting`, `requested`, `pending`, `completed`.
    pub status: String,
    pub conclusion: Option<String>,
    pub head_sha: String,
    pub head_branch: String,
    pub head_repo: Option<String>,
    /// The PR numbers GitHub attaches to the run (`pull_requests[].number`).
    /// Empty for fork PRs, and more than one when one branch heads several
    /// PRs: both mean the run cannot be tied to one PR.
    pub prs: Vec<u64>,
}

impl Run {
    fn not_completed(&self) -> bool {
        self.status != "completed"
    }
}

/// Whose run is it? (bb7dc #572 point 2: a SHA alone does not say, two PRs
/// can share a head commit.)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Owner {
    /// A `pull_request` run of this PR at its current head: same SHA, same
    /// head branch and repository, and GitHub ties it to this PR only.
    Ours,
    /// Certainly not this PR's (another event, SHA, branch, repository or PR).
    Others,
    /// Same SHA, branch and repository, but GitHub ties it to no PR or to
    /// several: cannot tell.
    Unbound,
}

pub fn owner(r: &Run, p: &Pull) -> Owner {
    if r.event != "pull_request"
        || r.head_sha != p.head_sha
        || r.head_branch != p.head_ref
        || r.head_repo != p.head_repo
    {
        return Owner::Others;
    }
    match r.prs.as_slice() {
        [n] if *n == p.number => Owner::Ours,
        [] => Owner::Unbound,
        l if l.contains(&p.number) => Owner::Unbound,
        _ => Owner::Others,
    }
}

/// `ci` → `.github/workflows/ci.yml`; a path is kept as given.
pub fn workflow_path(w: &str) -> String {
    if w.contains('/') {
        w.to_owned()
    } else {
        format!(".github/workflows/{w}.yml")
    }
}

/// What a cancel did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cancelled {
    Cancelled,
    /// The run had already completed (HTTP 409): nothing to give way.
    AlreadyDone,
}

/// A GitHub read or write that did not answer (a code, not user text).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GhError(pub String);

impl fmt::Display for GhError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// The GitHub calls the yield needs (a trait so the rules are tested
/// against fakes; [`crate::gh::GhCli`] is the real one).
pub trait Github {
    fn pull(&self, number: u64) -> Result<Pull, GhError>;
    /// Every run for this exact head SHA; `Err` if the list may be
    /// incomplete (more than one page), never a silently short list.
    fn runs_for(&self, sha: &str) -> Result<Vec<Run>, GhError>;
    /// Is `ancestor` contained in `head` (compare: identical or ahead)?
    fn contains(&self, ancestor: &str, head: &str) -> Result<bool, GhError>;
    fn add_label(&self, number: u64, label: &str) -> Result<(), GhError>;
    fn cancel_run(&self, id: u64) -> Result<Cancelled, GhError>;
}

/// What to yield to what.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub integ: u64,
    /// (original PR number, the full head SHA that was integrated)
    pub originals: Vec<(u64, String)>,
    /// Workflow files the integration PR must have started (default:
    /// `.github/workflows/ci.yml`; see [`workflow_path`]).
    pub need_workflows: Vec<String>,
    pub label: String,
}

pub const DEFAULT_LABEL: &str = "in-integ";

/// Is this a full lowercase 40-hex commit SHA? Prefixes are refused.
pub fn is_full_sha(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Is the integration PR ready to be yielded to?
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Gate {
    Ready {
        head: String,
    },
    /// Do nothing to any original (`why` is a code).
    Refused {
        why: String,
    },
}

/// One original PR's verdict.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Cancel these runs; add the label unless it is there.
    Yield { cancel: Vec<u64>, add_label: bool },
    /// Leave it alone, on purpose (`why` is a code).
    Skip { why: String },
    /// Could not tell: leave it alone and say so.
    Unknown { why: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Decision {
    pub number: u64,
    pub want: String,
    pub verdict: Verdict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub integ: u64,
    pub gate: Gate,
    /// Empty when the gate refused.
    pub decisions: Vec<Decision>,
}

fn integ_gate(gh: &dyn Github, req: &Request) -> Gate {
    let p = match gh.pull(req.integ) {
        Ok(p) => p,
        Err(e) => {
            return Gate::Refused {
                why: format!("integ_unreadable:{e}"),
            }
        }
    };
    if p.state != "open" {
        return Gate::Refused {
            why: format!("integ_not_open:{}", p.state),
        };
    }
    if !is_full_sha(&p.head_sha) {
        return Gate::Refused {
            why: "integ_head_not_a_full_sha".into(),
        };
    }
    let runs = match gh.runs_for(&p.head_sha) {
        Ok(r) => r,
        Err(e) => {
            return Gate::Refused {
                why: format!("integ_runs_unreadable:{e}"),
            }
        }
    };
    for wf in &req.need_workflows {
        let wf = workflow_path(wf);
        let live = |r: &&Run| r.path == wf && r.conclusion.as_deref() != Some("cancelled");
        let owners: Vec<Owner> = runs.iter().filter(live).map(|r| owner(r, &p)).collect();
        if owners.contains(&Owner::Ours) {
            continue;
        }
        let why = if owners.contains(&Owner::Unbound) {
            // a run that may be another PR's proves nothing about this one
            format!("integ_ci_unbound:{wf}")
        } else {
            format!("integ_ci_not_started:{wf}")
        };
        return Gate::Refused { why };
    }
    Gate::Ready { head: p.head_sha }
}

fn decide(gh: &dyn Github, req: &Request, integ_head: &str, n: u64, want: &str) -> Verdict {
    let skip = |why: &str| Verdict::Skip {
        why: why.to_owned(),
    };
    let unknown = |why: String| Verdict::Unknown { why };
    if n == req.integ {
        return skip("is_the_integration_pr");
    }
    if !is_full_sha(want) {
        return skip("want_not_a_full_sha");
    }
    let p = match gh.pull(n) {
        Ok(p) => p,
        Err(e) => return unknown(format!("pull_unreadable:{e}")),
    };
    if p.state != "open" {
        return skip("not_open");
    }
    if p.head_sha != want {
        // the author pushed after integration: that is new work, not ours to stop
        return skip("head_moved");
    }
    match gh.contains(want, integ_head) {
        Ok(true) => {}
        Ok(false) => return skip("not_in_integration"),
        Err(e) => return unknown(format!("compare_unreadable:{e}")),
    }
    let runs = match gh.runs_for(want) {
        Ok(r) => r,
        Err(e) => return unknown(format!("runs_unreadable:{e}")),
    };
    let mut cancel = Vec::new();
    for r in runs.iter().filter(|r| r.not_completed()) {
        match owner(r, &p) {
            Owner::Ours => cancel.push(r.id),
            Owner::Others => {}
            // it may be another PR's run on the same commit: do not touch
            Owner::Unbound => return unknown(format!("run_unbound:{}", r.id)),
        }
    }
    Verdict::Yield {
        cancel,
        add_label: !p.labels.iter().any(|l| l == &req.label),
    }
}

/// Read everything, change nothing.
pub fn plan(gh: &dyn Github, req: &Request) -> Plan {
    let gate = integ_gate(gh, req);
    let decisions = match &gate {
        Gate::Refused { .. } => Vec::new(),
        Gate::Ready { head } => req
            .originals
            .iter()
            .map(|(n, want)| Decision {
                number: *n,
                want: want.clone(),
                verdict: decide(gh, req, head, *n, want),
            })
            .collect(),
    };
    Plan {
        integ: req.integ,
        gate,
        decisions,
    }
}

/// What applying did to one original.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Applied {
    pub number: u64,
    /// `added`, `already`, `failed:<err>`, `aborted:<why>` (a fresh check
    /// before it failed: not added), `added_head_moved:<sha>`, or
    /// `batch_stopped:<why>` (not reached: an earlier PR stopped the batch).
    pub label: String,
    /// (run id, `cancelled` | `already_done` | `failed:<err>` |
    /// `aborted:<why>` | `left:not_cancellable_now`; id 0 =
    /// `failed:recheck:<why>` / `batch_stopped:recheck:<why>`)
    pub runs: Vec<(u64, String)>,
}

impl Applied {
    pub fn failed(&self) -> bool {
        let bad = |s: &str| {
            s.starts_with("failed")
                || s.starts_with("aborted")
                || s.starts_with("added_head_moved")
                || s.starts_with("batch_stopped")
        };
        bad(&self.label) || self.runs.iter().any(|(_, s)| bad(s))
    }
}

/// bb7dc #572 point 3: plan and apply are separate reads, so before every
/// side effect the whole check runs again (integration PR open, same head,
/// its CI still ours and not cancelled; the original open, at `want`,
/// contained, its runs tied to it). The fresh cancel list, or why not.
fn fresh(gh: &dyn Github, req: &Request, head: &str, d: &Decision) -> Result<Vec<u64>, String> {
    match integ_gate(gh, req) {
        Gate::Ready { head: h } if h == head => {}
        Gate::Ready { head: h } => return Err(format!("integ_head_moved:{h}")),
        Gate::Refused { why } => return Err(why),
    }
    match decide(gh, req, head, d.number, &d.want) {
        Verdict::Yield { cancel, .. } => Ok(cancel),
        Verdict::Skip { why } | Verdict::Unknown { why } => Err(why),
    }
}

fn cancel_one(gh: &dyn Github, id: u64) -> String {
    match gh.cancel_run(id) {
        Ok(Cancelled::Cancelled) => "cancelled".to_owned(),
        Ok(Cancelled::AlreadyDone) => "already_done".to_owned(),
        Err(e) => format!("failed:{e}"),
    }
}

/// Cancel each id after a fresh check. The first failed check stops: the
/// reason is returned and the caller stops the whole batch (bb7dc #572
/// f7a02962: a new UNKNOWN mid-apply is still "any UNKNOWN, nothing more").
fn cancel_checked(
    gh: &dyn Github,
    req: &Request,
    head: &str,
    d: &Decision,
    ids: &[u64],
    out: &mut Applied,
) -> Option<String> {
    for id in ids {
        match fresh(gh, req, head, d) {
            Err(why) => {
                out.runs.push((*id, format!("aborted:{why}")));
                return Some(why);
            }
            Ok(now) if !now.contains(id) => {
                out.runs.push((*id, "left:not_cancellable_now".into()));
            }
            Ok(_) => out.runs.push((*id, cancel_one(gh, *id))),
        }
    }
    None
}

/// Carry out a plan. Nothing is done when the gate refused, or when any
/// original is UNKNOWN (one batch, one answer: rerun when GitHub answers;
/// deliberate Skips do not stop the others).
///
/// Per original: fresh check → label (if missing) → read the head again
/// (a push between the check and the label) → each run cancelled after its
/// own fresh check. ci.yml also runs on `labeled`, so when a label was added
/// `settle` runs (the caller waits for GitHub to create that run) and the
/// fresh cancel list is applied once more.
///
/// The batch stops at the first check that does not pass during apply
/// (a fresh check failing for any reason, a head that moved or could not be
/// read after labelling, a failed re-read after `settle`): no later label or
/// cancel for any PR, no `settle`; every original not reached says
/// `batch_stopped:<why>`. What was already written stays written and is
/// reported as it happened (GitHub cannot roll a cancel back): a stopped
/// batch is a partial result, exit 3. A write the API refuses (a label or a
/// cancel failing) is reported and does not stop the batch.
///
/// GitHub has no compare-and-swap: a change between the last fresh check
/// and the write itself is not caught here. The label cannot make a head
/// green (ci.yml only reacts to the labelling event itself, and that run's
/// macOS cells fail as "not a verdict").
pub fn apply(gh: &dyn Github, req: &Request, plan: &Plan, settle: &dyn Fn()) -> Vec<Applied> {
    let Gate::Ready { head } = &plan.gate else {
        return Vec::new();
    };
    if plan
        .decisions
        .iter()
        .any(|d| matches!(d.verdict, Verdict::Unknown { .. }))
    {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut relabelled = Vec::new();
    let mut stop: Option<String> = None;
    for d in &plan.decisions {
        let Verdict::Yield { cancel, add_label } = &d.verdict else {
            continue;
        };
        let mut a = Applied {
            number: d.number,
            label: "already".into(),
            runs: Vec::new(),
        };
        if let Some(why) = &stop {
            a.label = format!("batch_stopped:{why}");
            out.push(a);
            continue;
        }
        if *add_label {
            if let Err(why) = fresh(gh, req, head, d) {
                a.label = format!("aborted:{why}");
                stop = Some(why);
                out.push(a);
                continue;
            }
            a.label = match gh.add_label(d.number, &req.label) {
                Ok(()) => "added".into(),
                Err(e) => format!("failed:{e}"),
            };
            if a.label == "added" {
                let moved = match gh.pull(d.number) {
                    Ok(p) if p.head_sha == d.want => None,
                    // pushed between the check and the label: the label's
                    // run is on the new head; leave every run alone
                    Ok(p) => Some(p.head_sha),
                    Err(e) => Some(format!("unreadable:{e}")),
                };
                if let Some(m) = moved {
                    a.label = format!("added_head_moved:{m}");
                    stop = Some(format!("head_moved_after_label:#{}", d.number));
                    out.push(a);
                    continue;
                }
                relabelled.push(out.len());
            }
        }
        stop = cancel_checked(gh, req, head, d, cancel, &mut a);
        out.push(a);
    }
    if relabelled.is_empty() {
        return out;
    }
    if let Some(why) = &stop {
        // the labels' own runs may still be going: say so on each PR that
        // is otherwise done (one that stopped itself already says why)
        for i in relabelled {
            if !out[i].failed() {
                out[i]
                    .runs
                    .push((0, format!("batch_stopped:recheck:{why}")));
            }
        }
        return out;
    }
    settle();
    for i in relabelled {
        let Some(d) = plan.decisions.iter().find(|d| d.number == out[i].number) else {
            continue;
        };
        if let Some(why) = &stop {
            out[i]
                .runs
                .push((0, format!("batch_stopped:recheck:{why}")));
            continue;
        }
        let seen: Vec<u64> = out[i].runs.iter().map(|(id, _)| *id).collect();
        match fresh(gh, req, head, d) {
            Ok(now) => {
                let new: Vec<u64> = now.into_iter().filter(|id| !seen.contains(id)).collect();
                stop = cancel_checked(gh, req, head, d, &new, &mut out[i]);
            }
            // the label's own run may still be going: say so
            Err(why) => {
                out[i].runs.push((0, format!("failed:recheck:{why}")));
                stop = Some(why);
            }
        }
    }
    out
}

/// Exit code: 0 = done (or nothing to do), 2 = gate refused, 3 = something
/// unknown or failed. A dry run with only Skip / Yield is 0.
pub fn exit_code(plan: &Plan, applied: &[Applied]) -> i32 {
    if matches!(plan.gate, Gate::Refused { .. }) {
        return 2;
    }
    let unknown = plan
        .decisions
        .iter()
        .any(|d| matches!(d.verdict, Verdict::Unknown { .. }));
    if unknown || applied.iter().any(Applied::failed) {
        3
    } else {
        0
    }
}

/// The report as JSON (codes, not user text).
pub fn to_json(plan: &Plan, applied: Option<&[Applied]>) -> serde_json::Value {
    use serde_json::json;
    let gate = match &plan.gate {
        Gate::Ready { head } => json!({"ready": true, "head": head}),
        Gate::Refused { why } => json!({"ready": false, "why": why}),
    };
    let decisions: Vec<_> = plan
        .decisions
        .iter()
        .map(|d| {
            let v = match &d.verdict {
                Verdict::Yield { cancel, add_label } => {
                    json!({"verdict": "yield", "cancel": cancel, "add_label": add_label})
                }
                Verdict::Skip { why } => json!({"verdict": "skip", "why": why}),
                Verdict::Unknown { why } => json!({"verdict": "unknown", "why": why}),
            };
            json!({"pr": d.number, "want": d.want, "decision": v})
        })
        .collect();
    let mut out = json!({"integ": plan.integ, "gate": gate, "decisions": decisions});
    if let Some(a) = applied {
        out["applied"] = a
            .iter()
            .map(|x| {
                json!({"pr": x.number, "label": x.label,
                    "runs": x.runs.iter().map(|(id, s)| json!({"id": id, "result": s})).collect::<Vec<_>>()})
            })
            .collect();
    }
    out
}
