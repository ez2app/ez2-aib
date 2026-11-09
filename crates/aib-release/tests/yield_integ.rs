//! S2 cross-PR yield rules against a fake GitHub (plan v3 9.2 #2 knives:
//! wrong head, integration CI not started, integration closed, unrelated PR,
//! cancel API failure). Nothing here calls the network.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_release::yield_integ::{
    apply, exit_code, plan, Cancelled, Gate, GhError, Github, Pull, Request, Run, Verdict,
};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};

const INTEG: u64 = 567;
const IH: &str = "1111111111111111111111111111111111111111";
const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

#[derive(Default)]
struct Fake {
    pulls: BTreeMap<u64, Result<Pull, GhError>>,
    runs: BTreeMap<String, Result<Vec<Run>, GhError>>,
    /// (ancestor, head) pairs that are contained
    contained: BTreeSet<(String, String)>,
    compare_fails: bool,
    cancel_fails: BTreeSet<u64>,
    cancel_done: BTreeSet<u64>,
    label_fails: bool,
    /// runs of a SHA as read once a label was added (ci.yml's `labeled` run)
    runs_after_label: BTreeMap<String, Vec<Run>>,
    calls: RefCell<Vec<String>>,
}

impl Github for Fake {
    fn pull(&self, n: u64) -> Result<Pull, GhError> {
        self.pulls
            .get(&n)
            .cloned()
            .unwrap_or(Err(GhError("http:404".into())))
    }
    fn runs_for(&self, sha: &str) -> Result<Vec<Run>, GhError> {
        if self.calls.borrow().iter().any(|c| c.starts_with("label")) {
            if let Some(r) = self.runs_after_label.get(sha) {
                return Ok(r.clone());
            }
        }
        self.runs.get(sha).cloned().unwrap_or(Ok(vec![]))
    }
    fn contains(&self, a: &str, h: &str) -> Result<bool, GhError> {
        if self.compare_fails {
            return Err(GhError("http:502".into()));
        }
        Ok(self.contained.contains(&(a.to_owned(), h.to_owned())))
    }
    fn add_label(&self, n: u64, l: &str) -> Result<(), GhError> {
        self.calls.borrow_mut().push(format!("label #{n} {l}"));
        if self.label_fails {
            Err(GhError("http:403".into()))
        } else {
            Ok(())
        }
    }
    fn cancel_run(&self, id: u64) -> Result<Cancelled, GhError> {
        self.calls.borrow_mut().push(format!("cancel {id}"));
        if self.cancel_fails.contains(&id) {
            Err(GhError("http:500".into()))
        } else if self.cancel_done.contains(&id) {
            Ok(Cancelled::AlreadyDone)
        } else {
            Ok(Cancelled::Cancelled)
        }
    }
}

fn pull(n: u64, state: &str, head: &str, labels: &[&str]) -> Result<Pull, GhError> {
    Ok(Pull {
        number: n,
        state: state.into(),
        head_sha: head.into(),
        labels: labels.iter().map(|s| (*s).to_owned()).collect(),
    })
}

fn run(id: u64, name: &str, event: &str, status: &str, concl: Option<&str>, sha: &str) -> Run {
    Run {
        id,
        name: name.into(),
        event: event.into(),
        status: status.into(),
        conclusion: concl.map(str::to_owned),
        head_sha: sha.into(),
    }
}

/// The good case: integration #567 open, its `ci` running; #537 open at A,
/// A contained in the integration head, with one queued and one running
/// `pull_request` run, one finished run, and a `push` run on the same SHA.
fn world() -> Fake {
    let mut f = Fake::default();
    f.pulls.insert(INTEG, pull(INTEG, "open", IH, &[]));
    f.runs.insert(
        IH.into(),
        Ok(vec![run(
            900,
            "ci",
            "pull_request",
            "in_progress",
            None,
            IH,
        )]),
    );
    f.pulls.insert(537, pull(537, "open", A, &[]));
    f.runs.insert(
        A.into(),
        Ok(vec![
            run(10, "ci", "pull_request", "queued", None, A),
            run(11, "aibd", "pull_request", "in_progress", None, A),
            run(12, "ci", "pull_request", "completed", Some("success"), A),
            run(13, "ci", "push", "in_progress", None, A),
        ]),
    );
    f.contained.insert((A.into(), IH.into()));
    f
}

fn req(origs: &[(u64, &str)]) -> Request {
    Request {
        integ: INTEG,
        originals: origs.iter().map(|(n, s)| (*n, (*s).to_owned())).collect(),
        need_workflows: vec!["ci".into()],
        label: "in-integ".into(),
    }
}

fn verdict(f: &Fake, r: &Request, n: u64) -> Verdict {
    plan(f, r)
        .decisions
        .into_iter()
        .find(|d| d.number == n)
        .unwrap()
        .verdict
}

#[test]
fn a_contained_original_at_its_integrated_head_yields_its_unfinished_pr_runs() {
    let f = world();
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    assert_eq!(p.gate, Gate::Ready { head: IH.into() });
    assert_eq!(
        p.decisions[0].verdict,
        Verdict::Yield {
            cancel: vec![10, 11],
            add_label: true
        },
        "only unfinished pull_request runs of that exact SHA"
    );
    let done = apply(&f, &p, "in-integ", &|| {});
    assert_eq!(
        *f.calls.borrow(),
        vec!["label #537 in-integ", "cancel 10", "cancel 11"]
    );
    assert_eq!(exit_code(&p, &done), 0);
}

/// Knife: wrong head. The author pushed after integration: their new head's
/// runs are new work and must not be stopped.
#[test]
fn an_original_whose_head_moved_is_left_alone() {
    let mut f = world();
    f.pulls.insert(537, pull(537, "open", B, &[]));
    let r = req(&[(537, A)]);
    assert_eq!(
        verdict(&f, &r, 537),
        Verdict::Skip {
            why: "head_moved".into()
        }
    );
    let p = plan(&f, &r);
    assert!(apply(&f, &p, "in-integ", &|| {}).is_empty());
    assert!(f.calls.borrow().is_empty());
}

/// Knife: the integration PR's CI has not started (or only a cancelled run
/// exists): yielding now would leave nobody running the tests.
#[test]
fn nothing_yields_before_the_integration_ci_has_started() {
    for runs in [
        vec![],
        vec![run(
            900,
            "ci",
            "pull_request",
            "completed",
            Some("cancelled"),
            IH,
        )],
        vec![run(900, "ci", "push", "in_progress", None, IH)],
        vec![run(900, "aibd", "pull_request", "in_progress", None, IH)],
    ] {
        let mut f = world();
        f.runs.insert(IH.into(), Ok(runs.clone()));
        let p = plan(&f, &req(&[(537, A)]));
        assert_eq!(
            p.gate,
            Gate::Refused {
                why: "integ_ci_not_started:ci".into()
            },
            "{runs:?}"
        );
        assert!(p.decisions.is_empty());
        assert!(apply(&f, &p, "in-integ", &|| {}).is_empty());
        assert!(f.calls.borrow().is_empty());
        assert_eq!(exit_code(&p, &[]), 2);
    }
}

/// Knife: the integration PR was closed: nothing yields to it.
#[test]
fn nothing_yields_to_a_closed_integration_pr() {
    let mut f = world();
    f.pulls.insert(INTEG, pull(INTEG, "closed", IH, &[]));
    let p = plan(&f, &req(&[(537, A)]));
    assert_eq!(
        p.gate,
        Gate::Refused {
            why: "integ_not_open:closed".into()
        }
    );
    assert!(apply(&f, &p, "in-integ", &|| {}).is_empty());
    assert!(f.calls.borrow().is_empty());
}

/// Knife: an unrelated PR (its head is not in the integration head) is not
/// touched, even when its number is passed by mistake.
#[test]
fn an_original_not_in_the_integration_is_left_alone() {
    let mut f = world();
    f.pulls.insert(999, pull(999, "open", B, &[]));
    f.runs.insert(
        B.into(),
        Ok(vec![run(30, "ci", "pull_request", "queued", None, B)]),
    );
    let r = req(&[(537, A), (999, B)]);
    assert_eq!(
        verdict(&f, &r, 999),
        Verdict::Skip {
            why: "not_in_integration".into()
        }
    );
    let p = plan(&f, &r);
    apply(&f, &p, "in-integ", &|| {});
    assert!(
        !f.calls
            .borrow()
            .iter()
            .any(|c| c.contains("#999") || c == "cancel 30"),
        "{:?}",
        f.calls.borrow()
    );
}

/// Knife: a cancel the API refuses is reported as failed (exit 3), never as
/// yielded; the other runs are still cancelled. A run already completed
/// (409) is not a failure.
#[test]
fn a_failed_cancel_is_reported_not_counted_as_yielded() {
    let mut f = world();
    f.cancel_fails.insert(10);
    f.cancel_done.insert(11);
    let p = plan(&f, &req(&[(537, A)]));
    let done = apply(&f, &p, "in-integ", &|| {});
    assert_eq!(
        done[0].runs,
        vec![(10, "failed:http:500".into()), (11, "already_done".into())]
    );
    assert!(done[0].failed());
    assert_eq!(exit_code(&p, &done), 3);
}

#[test]
fn a_failed_label_is_reported_and_the_runs_still_give_way() {
    let mut f = world();
    f.label_fails = true;
    let p = plan(&f, &req(&[(537, A)]));
    let done = apply(&f, &p, "in-integ", &|| {});
    assert_eq!(done[0].label, "failed:http:403");
    assert_eq!(done[0].runs.len(), 2);
    assert_eq!(exit_code(&p, &done), 3);
}

/// Reads that fail are UNKNOWN (exit 3), never "nothing to cancel".
#[test]
fn unreadable_answers_are_unknown_not_nothing_to_do() {
    let mut f = world();
    f.runs
        .insert(A.into(), Err(GhError("incomplete:100/140".into())));
    let r = req(&[(537, A)]);
    assert!(matches!(verdict(&f, &r, 537), Verdict::Unknown { .. }));
    assert_eq!(exit_code(&plan(&f, &r), &[]), 3);

    let mut f = world();
    f.compare_fails = true;
    assert!(matches!(verdict(&f, &r, 537), Verdict::Unknown { .. }));

    let mut f = world();
    f.pulls.insert(537, Err(GhError("http:502".into())));
    assert!(matches!(verdict(&f, &r, 537), Verdict::Unknown { .. }));

    let mut f = world();
    f.pulls.insert(INTEG, Err(GhError("http:502".into())));
    assert!(matches!(plan(&f, &r).gate, Gate::Refused { .. }));
}

#[test]
fn closed_originals_the_integration_itself_and_existing_labels() {
    let mut f = world();
    f.pulls.insert(537, pull(537, "closed", A, &[]));
    assert_eq!(
        verdict(&f, &req(&[(537, A)]), 537),
        Verdict::Skip {
            why: "not_open".into()
        }
    );
    assert_eq!(
        verdict(&world(), &req(&[(INTEG, IH)]), INTEG),
        Verdict::Skip {
            why: "is_the_integration_pr".into()
        }
    );
    let mut f = world();
    f.pulls.insert(537, pull(537, "open", A, &["in-integ"]));
    assert_eq!(
        verdict(&f, &req(&[(537, A)]), 537),
        Verdict::Yield {
            cancel: vec![10, 11],
            add_label: false
        }
    );
}

/// ci.yml listens to `labeled`: adding `in-integ` starts one more run on the
/// same SHA. It is read again after `settle` and cancelled too; a run of
/// another SHA or a `push` run that shows up is not.
#[test]
fn the_run_the_label_itself_starts_is_cancelled_too() {
    let mut f = world();
    f.runs_after_label.insert(
        A.into(),
        vec![
            run(10, "ci", "pull_request", "in_progress", None, A),
            run(11, "aibd", "pull_request", "in_progress", None, A),
            run(14, "ci", "pull_request", "queued", None, A),
            run(15, "ci", "push", "queued", None, A),
            run(16, "ci", "pull_request", "queued", None, B),
        ],
    );
    let settled = RefCell::new(0);
    let p = plan(&f, &req(&[(537, A)]));
    let done = apply(&f, &p, "in-integ", &|| *settled.borrow_mut() += 1);
    assert_eq!(*settled.borrow(), 1);
    assert_eq!(
        *f.calls.borrow(),
        vec!["label #537 in-integ", "cancel 10", "cancel 11", "cancel 14"]
    );
    assert_eq!(exit_code(&p, &done), 0);
}

/// When the label was already there nothing new starts: no wait, no re-read.
#[test]
fn no_settle_when_no_label_was_added() {
    let mut f = world();
    f.pulls.insert(537, pull(537, "open", A, &["in-integ"]));
    let settled = RefCell::new(0);
    let p = plan(&f, &req(&[(537, A)]));
    apply(&f, &p, "in-integ", &|| *settled.borrow_mut() += 1);
    assert_eq!(*settled.borrow(), 0);
}

/// A re-read that fails is a failure (exit 3): the label's run may be going.
#[test]
fn a_failed_recheck_is_reported() {
    let mut f = world();
    f.runs.insert(
        A.into(),
        Ok(vec![run(10, "ci", "pull_request", "queued", None, A)]),
    );
    let p = plan(&f, &req(&[(537, A)]));
    f.runs.insert(A.into(), Err(GhError("http:502".into())));
    let done = apply(&f, &p, "in-integ", &|| {});
    assert!(
        done[0]
            .runs
            .iter()
            .any(|(_, s)| s == "failed:recheck:http:502"),
        "{:?}",
        done[0].runs
    );
    assert_eq!(exit_code(&p, &done), 3);
}
