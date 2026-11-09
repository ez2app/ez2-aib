//! S2 cross-PR yield (plan v3 9.2 #2; a9136 writes, bb7dc reviews).
//!
//! When an integration PR takes over original PRs, the originals' queued /
//! running CI runs are cancelled so the integration PR gets the macOS
//! runners, and each original gets the `in-integ` label (its later pushes
//! skip the macOS cells, ci.yml). Only cancels, never marks anything green.
//!
//! Before touching an original, every one of these must hold (else that PR
//! is skipped, or everything is refused, with the reason):
//! - the integration PR is open, and its CI for its current head has started
//!   (each required workflow has a `pull_request` run that was not cancelled);
//! - the original is open, its head is EXACTLY the full SHA the caller says
//!   was integrated (no prefix match: an empty or short prefix matched every
//!   head in the old shell script), and that SHA is contained in the
//!   integration head (GitHub compare: identical or ahead);
//! - only runs of that SHA, from `pull_request` events, not yet completed.
//!
//! A read that fails is UNKNOWN for that PR (or refuses the whole yield when
//! it is the integration PR's), never "nothing to cancel". A cancel that
//! fails is reported as failed, never as yielded.

use std::fmt;

/// A pull request as GitHub answers it now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pull {
    pub number: u64,
    /// `open` or `closed`.
    pub state: String,
    pub head_sha: String,
    pub labels: Vec<String>,
}

/// One workflow run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Run {
    pub id: u64,
    /// Workflow name (`ci`, `aibd`, ...).
    pub name: String,
    pub event: String,
    /// `queued`, `in_progress`, `waiting`, `requested`, `pending`, `completed`.
    pub status: String,
    pub conclusion: Option<String>,
    pub head_sha: String,
}

impl Run {
    fn not_completed(&self) -> bool {
        self.status != "completed"
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
    /// Workflows the integration PR must have started (default: `ci`).
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
    let runs = match gh.runs_for(&p.head_sha) {
        Ok(r) => r,
        Err(e) => {
            return Gate::Refused {
                why: format!("integ_runs_unreadable:{e}"),
            }
        }
    };
    for wf in &req.need_workflows {
        let started = runs.iter().any(|r| {
            r.head_sha == p.head_sha
                && r.event == "pull_request"
                && &r.name == wf
                && r.conclusion.as_deref() != Some("cancelled")
        });
        if !started {
            return Gate::Refused {
                why: format!("integ_ci_not_started:{wf}"),
            };
        }
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
    let cancel = runs
        .iter()
        .filter(|r| r.head_sha == want && r.event == "pull_request" && r.not_completed())
        .map(|r| r.id)
        .collect();
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
    /// `added`, `already`, `failed:<err>`, or `-` (not yielded).
    pub label: String,
    /// (run id, `cancelled` | `already_done` | `failed:<err>`)
    pub runs: Vec<(u64, String)>,
}

impl Applied {
    pub fn failed(&self) -> bool {
        self.label.starts_with("failed") || self.runs.iter().any(|(_, s)| s.starts_with("failed"))
    }
}

fn cancel_one(gh: &dyn Github, id: u64) -> (u64, String) {
    let s = match gh.cancel_run(id) {
        Ok(Cancelled::Cancelled) => "cancelled".to_owned(),
        Ok(Cancelled::AlreadyDone) => "already_done".to_owned(),
        Err(e) => format!("failed:{e}"),
    };
    (id, s)
}

/// Carry out a plan. Only `Yield` verdicts are touched; a refused gate does
/// nothing. The label goes first, then each run is cancelled; one failure
/// does not stop the others, and is reported.
///
/// ci.yml also runs on `labeled`, so adding the label starts one more run on
/// the same SHA. After a label was added, `settle` runs (the caller waits for
/// GitHub to create that run), the runs of the same SHA are read again, and
/// any unfinished `pull_request` run not seen before is cancelled too, with
/// the same rules (same exact SHA, already shown to be in the integration).
pub fn apply(gh: &dyn Github, plan: &Plan, label: &str, settle: &dyn Fn()) -> Vec<Applied> {
    if !matches!(plan.gate, Gate::Ready { .. }) {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut relabelled = Vec::new();
    for d in &plan.decisions {
        let Verdict::Yield { cancel, add_label } = &d.verdict else {
            continue;
        };
        let label = if *add_label {
            match gh.add_label(d.number, label) {
                Ok(()) => {
                    relabelled.push(out.len());
                    "added".to_owned()
                }
                Err(e) => format!("failed:{e}"),
            }
        } else {
            "already".to_owned()
        };
        let runs = cancel.iter().map(|id| cancel_one(gh, *id)).collect();
        out.push(Applied {
            number: d.number,
            label,
            runs,
        });
    }
    if relabelled.is_empty() {
        return out;
    }
    settle();
    for i in relabelled {
        let want = &plan
            .decisions
            .iter()
            .find(|d| d.number == out[i].number)
            .map(|d| d.want.clone())
            .unwrap_or_default();
        let seen: Vec<u64> = out[i].runs.iter().map(|(id, _)| *id).collect();
        match gh.runs_for(want) {
            Ok(runs) => {
                for r in runs.iter().filter(|r| {
                    &r.head_sha == want
                        && r.event == "pull_request"
                        && r.not_completed()
                        && !seen.contains(&r.id)
                }) {
                    out[i].runs.push(cancel_one(gh, r.id));
                }
            }
            // the label's own run may still be going: say so
            Err(e) => out[i].runs.push((0, format!("failed:recheck:{e}"))),
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
