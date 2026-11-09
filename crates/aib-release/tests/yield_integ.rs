//! S2 cross-PR yield rules against a fake GitHub (plan v3 9.2 #2 knives:
//! wrong head, integration CI not started, integration closed, unrelated PR,
//! cancel API failure; bb7dc #572: runs tied to their PR, state changing
//! between plan and every write). Nothing here calls the network.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_release::yield_integ::{
    apply, exit_code, owner, plan, Applied, Cancelled, Gate, GhError, Github, Owner, Plan, Pull,
    Request, Run, Verdict,
};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};

const REPO: &str = "ez2app/ez2-aib";
const INTEG: u64 = 567;
const IH: &str = "1111111111111111111111111111111111111111";
const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const C: &str = "cccccccccccccccccccccccccccccccccccccccc";
const CI: &str = ".github/workflows/ci.yml";
const AIBD: &str = ".github/workflows/aibd.yml";

#[derive(Default, Clone)]
struct World {
    pulls: BTreeMap<u64, Result<Pull, GhError>>,
    runs: BTreeMap<String, Result<Vec<Run>, GhError>>,
    /// (ancestor, head) pairs that are contained
    contained: BTreeSet<(String, String)>,
    compare_fails: bool,
    cancel_fails: BTreeSet<u64>,
    cancel_done: BTreeSet<u64>,
    label_fails: bool,
}

type Hook = Box<dyn Fn(&mut World)>;

/// A fake GitHub. Writes are logged as `label #N L` / `cancel ID`.
/// `before(kind, nth, f)` changes the world right before the nth write of
/// that kind (`label` / `cancel`): a barrier between a check and its act.
#[derive(Default)]
struct Fake {
    w: RefCell<World>,
    writes: RefCell<Vec<String>>,
    hooks: RefCell<Vec<(String, usize, Option<Hook>)>>,
}

impl Fake {
    fn new(w: World) -> Self {
        Fake {
            w: RefCell::new(w),
            ..Fake::default()
        }
    }
    fn before(&self, kind: &str, nth: usize, f: impl Fn(&mut World) + 'static) {
        self.hooks
            .borrow_mut()
            .push((kind.to_owned(), nth, Some(Box::new(f))));
    }
    fn write(&self, line: String) {
        let kind = line.split(' ').next().unwrap_or("").to_owned();
        let n = self
            .writes
            .borrow()
            .iter()
            .filter(|l| l.starts_with(&format!("{kind} ")))
            .count();
        let fire: Vec<Hook> = self
            .hooks
            .borrow_mut()
            .iter_mut()
            .filter(|(k, nth, _)| *k == kind && n + 1 == *nth)
            .filter_map(|(_, _, f)| f.take())
            .collect();
        for f in fire {
            f(&mut self.w.borrow_mut());
        }
        self.writes.borrow_mut().push(line);
    }
    fn writes(&self) -> Vec<String> {
        self.writes.borrow().clone()
    }
}

impl Github for Fake {
    fn pull(&self, n: u64) -> Result<Pull, GhError> {
        self.w
            .borrow()
            .pulls
            .get(&n)
            .cloned()
            .unwrap_or(Err(GhError("http:404".into())))
    }
    fn runs_for(&self, sha: &str) -> Result<Vec<Run>, GhError> {
        self.w.borrow().runs.get(sha).cloned().unwrap_or(Ok(vec![]))
    }
    fn contains(&self, a: &str, h: &str) -> Result<bool, GhError> {
        let w = self.w.borrow();
        if w.compare_fails {
            return Err(GhError("http:502".into()));
        }
        Ok(w.contained.contains(&(a.to_owned(), h.to_owned())))
    }
    fn add_label(&self, n: u64, l: &str) -> Result<(), GhError> {
        self.write(format!("label #{n} {l}"));
        let mut w = self.w.borrow_mut();
        if w.label_fails {
            return Err(GhError("http:403".into()));
        }
        if let Some(Ok(p)) = w.pulls.get_mut(&n) {
            p.labels.push(l.to_owned());
        }
        Ok(())
    }
    fn cancel_run(&self, id: u64) -> Result<Cancelled, GhError> {
        self.write(format!("cancel {id}"));
        let w = self.w.borrow();
        if w.cancel_fails.contains(&id) {
            Err(GhError("http:500".into()))
        } else if w.cancel_done.contains(&id) {
            Ok(Cancelled::AlreadyDone)
        } else {
            Ok(Cancelled::Cancelled)
        }
    }
}

fn branch(n: u64) -> String {
    format!("feat/pr-{n}")
}

fn pull(n: u64, state: &str, head: &str, labels: &[&str]) -> Result<Pull, GhError> {
    Ok(Pull {
        number: n,
        state: state.into(),
        head_sha: head.into(),
        head_ref: branch(n),
        head_repo: Some(REPO.into()),
        labels: labels.iter().map(|s| (*s).to_owned()).collect(),
    })
}

/// A `pull_request` run of PR `pr` (its branch, this repo, tied to it alone).
fn prun(id: u64, path: &str, status: &str, concl: Option<&str>, sha: &str, pr: u64) -> Run {
    Run {
        id,
        path: path.into(),
        event: "pull_request".into(),
        status: status.into(),
        conclusion: concl.map(str::to_owned),
        head_sha: sha.into(),
        head_branch: branch(pr),
        head_repo: Some(REPO.into()),
        prs: vec![pr],
    }
}

fn push_run(id: u64, sha: &str, pr: u64) -> Run {
    let mut r = prun(id, CI, "queued", None, sha, pr);
    r.event = "push".into();
    r.prs = vec![];
    r
}

/// The good case: integration #567 open, its `ci` running; #537 open at A,
/// A contained in the integration head, with one queued and one running run
/// of its own, one finished run, and a `push` run on the same SHA.
fn world() -> World {
    let mut w = World::default();
    w.pulls.insert(INTEG, pull(INTEG, "open", IH, &[]));
    w.runs.insert(
        IH.into(),
        Ok(vec![prun(900, CI, "in_progress", None, IH, INTEG)]),
    );
    w.pulls.insert(537, pull(537, "open", A, &[]));
    w.runs.insert(
        A.into(),
        Ok(vec![
            prun(10, CI, "queued", None, A, 537),
            prun(11, AIBD, "in_progress", None, A, 537),
            prun(12, CI, "completed", Some("success"), A, 537),
            push_run(13, A, 537),
        ]),
    );
    w.contained.insert((A.into(), IH.into()));
    w
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

fn go(f: &Fake, r: &Request) -> (Plan, Vec<Applied>) {
    let p = plan(f, r);
    let done = apply(f, r, &p, &|| {});
    (p, done)
}

fn refused(why: &str) -> Gate {
    Gate::Refused { why: why.into() }
}

#[test]
fn a_contained_original_at_its_integrated_head_yields_its_unfinished_pr_runs() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    assert_eq!(p.gate, Gate::Ready { head: IH.into() });
    assert_eq!(
        p.decisions[0].verdict,
        Verdict::Yield {
            cancel: vec![10, 11],
            add_label: true
        },
        "only its own unfinished pull_request runs of that exact SHA"
    );
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(
        f.writes(),
        vec!["label #537 in-integ", "cancel 10", "cancel 11"]
    );
    assert_eq!(exit_code(&p, &done), 0);
}

/// Knife: wrong head. The author pushed after integration: their new head's
/// runs are new work and must not be stopped.
#[test]
fn an_original_whose_head_moved_is_left_alone() {
    let mut w = world();
    w.pulls.insert(537, pull(537, "open", B, &[]));
    let f = Fake::new(w);
    let r = req(&[(537, A)]);
    assert_eq!(
        verdict(&f, &r, 537),
        Verdict::Skip {
            why: "head_moved".into()
        }
    );
    let (_, done) = go(&f, &r);
    assert!(done.is_empty());
    assert!(f.writes().is_empty());
}

/// Knife: the integration PR's CI has not started (or only a cancelled run,
/// a push run, another workflow): yielding now would leave nobody testing.
#[test]
fn nothing_yields_before_the_integration_ci_has_started() {
    for runs in [
        vec![],
        vec![prun(900, CI, "completed", Some("cancelled"), IH, INTEG)],
        vec![push_run(900, IH, INTEG)],
        vec![prun(900, AIBD, "in_progress", None, IH, INTEG)],
    ] {
        let mut w = world();
        w.runs.insert(IH.into(), Ok(runs.clone()));
        let f = Fake::new(w);
        let (p, done) = go(&f, &req(&[(537, A)]));
        assert_eq!(
            p.gate,
            refused(&format!("integ_ci_not_started:{CI}")),
            "{runs:?}"
        );
        assert!(p.decisions.is_empty());
        assert!(done.is_empty());
        assert!(f.writes().is_empty());
        assert_eq!(exit_code(&p, &[]), 2);
    }
}

/// Knife: the integration PR was closed: nothing yields to it.
#[test]
fn nothing_yields_to_a_closed_integration_pr() {
    let mut w = world();
    w.pulls.insert(INTEG, pull(INTEG, "closed", IH, &[]));
    let f = Fake::new(w);
    let (p, done) = go(&f, &req(&[(537, A)]));
    assert_eq!(p.gate, refused("integ_not_open:closed"));
    assert!(done.is_empty());
    assert!(f.writes().is_empty());
}

/// Knife: an unrelated PR (its head is not in the integration head) is not
/// touched, even when its number is passed by mistake.
#[test]
fn an_original_not_in_the_integration_is_left_alone() {
    let mut w = world();
    w.pulls.insert(999, pull(999, "open", B, &[]));
    w.runs
        .insert(B.into(), Ok(vec![prun(30, CI, "queued", None, B, 999)]));
    let f = Fake::new(w);
    let r = req(&[(537, A), (999, B)]);
    assert_eq!(
        verdict(&f, &r, 999),
        Verdict::Skip {
            why: "not_in_integration".into()
        }
    );
    go(&f, &r);
    assert!(
        !f.writes()
            .iter()
            .any(|c| c.contains("#999") || c == "cancel 30"),
        "{:?}",
        f.writes()
    );
}

/// Knife: a cancel the API refuses is reported as failed (exit 3), never as
/// yielded; the other runs are still cancelled. A run already completed
/// (409) is not a failure.
#[test]
fn a_failed_cancel_is_reported_not_counted_as_yielded() {
    let mut w = world();
    w.cancel_fails.insert(10);
    w.cancel_done.insert(11);
    let f = Fake::new(w);
    let (p, done) = go(&f, &req(&[(537, A)]));
    assert_eq!(
        done[0].runs,
        vec![(10, "failed:http:500".into()), (11, "already_done".into())]
    );
    assert!(done[0].failed());
    assert_eq!(exit_code(&p, &done), 3);
}

#[test]
fn a_failed_label_is_reported_and_the_runs_still_give_way() {
    let mut w = world();
    w.label_fails = true;
    let f = Fake::new(w);
    let (p, done) = go(&f, &req(&[(537, A)]));
    assert_eq!(done[0].label, "failed:http:403");
    assert_eq!(done[0].runs.len(), 2);
    assert_eq!(exit_code(&p, &done), 3);
}

/// Reads that fail are UNKNOWN (exit 3), never "nothing to cancel".
#[test]
fn unreadable_answers_are_unknown_not_nothing_to_do() {
    let r = req(&[(537, A)]);
    let mut w = world();
    w.runs
        .insert(A.into(), Err(GhError("incomplete:100/140".into())));
    let f = Fake::new(w);
    assert!(matches!(verdict(&f, &r, 537), Verdict::Unknown { .. }));
    assert_eq!(exit_code(&plan(&f, &r), &[]), 3);

    let mut w = world();
    w.compare_fails = true;
    assert!(matches!(
        verdict(&Fake::new(w), &r, 537),
        Verdict::Unknown { .. }
    ));

    let mut w = world();
    w.pulls.insert(537, Err(GhError("http:502".into())));
    assert!(matches!(
        verdict(&Fake::new(w), &r, 537),
        Verdict::Unknown { .. }
    ));

    let mut w = world();
    w.pulls.insert(INTEG, Err(GhError("http:502".into())));
    assert!(matches!(plan(&Fake::new(w), &r).gate, Gate::Refused { .. }));
}

/// One UNKNOWN stops the whole batch (the others wait for the rerun); a
/// deliberate Skip does not.
#[test]
fn one_unknown_original_stops_the_whole_batch() {
    let mut w = world();
    w.pulls.insert(538, Err(GhError("http:502".into())));
    let f = Fake::new(w);
    let (p, done) = go(&f, &req(&[(537, A), (538, A)]));
    assert!(done.is_empty());
    assert!(f.writes().is_empty(), "{:?}", f.writes());
    assert_eq!(exit_code(&p, &done), 3);

    let mut w = world();
    w.pulls.insert(538, pull(538, "closed", A, &[]));
    let f = Fake::new(w);
    go(&f, &req(&[(537, A), (538, A)]));
    assert_eq!(
        f.writes().len(),
        3,
        "a closed (skipped) original does not stop #537"
    );
}

#[test]
fn closed_originals_the_integration_itself_and_existing_labels() {
    let r = req(&[(537, A)]);
    let mut w = world();
    w.pulls.insert(537, pull(537, "closed", A, &[]));
    assert_eq!(
        verdict(&Fake::new(w), &r, 537),
        Verdict::Skip {
            why: "not_open".into()
        }
    );
    assert_eq!(
        verdict(&Fake::new(world()), &req(&[(INTEG, IH)]), INTEG),
        Verdict::Skip {
            why: "is_the_integration_pr".into()
        }
    );
    let mut w = world();
    w.pulls.insert(537, pull(537, "open", A, &["in-integ"]));
    assert_eq!(
        verdict(&Fake::new(w), &r, 537),
        Verdict::Yield {
            cancel: vec![10, 11],
            add_label: false
        }
    );
}

#[test]
fn a_short_integration_head_is_refused() {
    let short = "1111111";
    let mut w = world();
    w.pulls.insert(INTEG, pull(INTEG, "open", short, &[]));
    w.runs.insert(
        short.into(),
        Ok(vec![prun(900, CI, "in_progress", None, short, INTEG)]),
    );
    w.contained.insert((A.into(), short.into()));
    let f = Fake::new(w);
    let (p, _) = go(&f, &req(&[(537, A)]));
    assert_eq!(p.gate, refused("integ_head_not_a_full_sha"));
    assert!(f.writes().is_empty());
}

// ── bb7dc #572 point 2: a run is tied to its PR, not just to a SHA ─────────

/// Another PR (#600, other branch) sits on the integration head and only
/// its `ci` runs: that is not the integration PR's CI.
#[test]
fn another_prs_ci_on_the_same_commit_is_not_the_integration_ci() {
    let mut w = world();
    w.runs.insert(
        IH.into(),
        Ok(vec![prun(901, CI, "in_progress", None, IH, 600)]),
    );
    let f = Fake::new(w);
    let (p, _) = go(&f, &req(&[(537, A)]));
    assert_eq!(p.gate, refused(&format!("integ_ci_not_started:{CI}")));
    assert!(f.writes().is_empty());
}

/// A `ci` run on the integration branch that GitHub ties to no PR (or to
/// several) proves nothing: refused, with its own reason.
#[test]
fn an_unbound_run_does_not_open_the_gate() {
    for prs in [vec![], vec![INTEG, 601]] {
        let mut r = prun(901, CI, "in_progress", None, IH, INTEG);
        r.prs = prs.clone();
        let mut w = world();
        w.runs.insert(IH.into(), Ok(vec![r]));
        let f = Fake::new(w);
        let (p, _) = go(&f, &req(&[(537, A)]));
        assert_eq!(
            p.gate,
            refused(&format!("integ_ci_unbound:{CI}")),
            "{prs:?}"
        );
        assert!(f.writes().is_empty());
    }
}

/// Another open PR (#600) has the same head A: its run is left alone, ours
/// is cancelled.
#[test]
fn another_prs_run_on_the_same_sha_is_never_cancelled() {
    let mut w = world();
    w.pulls.insert(600, pull(600, "open", A, &[]));
    w.runs.insert(
        A.into(),
        Ok(vec![
            prun(10, CI, "queued", None, A, 537),
            prun(20, CI, "queued", None, A, 600),
        ]),
    );
    let f = Fake::new(w);
    let r = req(&[(537, A)]);
    assert_eq!(
        verdict(&f, &r, 537),
        Verdict::Yield {
            cancel: vec![10],
            add_label: true
        }
    );
    go(&f, &r);
    assert!(
        !f.writes().contains(&"cancel 20".to_owned()),
        "{:?}",
        f.writes()
    );
}

/// A run on #537's commit and branch that GitHub ties to no PR or to two
/// (one branch heading two PRs): UNKNOWN, nothing done.
#[test]
fn a_run_that_cannot_be_tied_to_one_pr_makes_the_original_unknown() {
    for prs in [vec![], vec![537, 538]] {
        let mut r20 = prun(20, CI, "queued", None, A, 537);
        r20.prs = prs.clone();
        let mut w = world();
        w.runs.insert(
            A.into(),
            Ok(vec![prun(10, CI, "queued", None, A, 537), r20]),
        );
        let f = Fake::new(w);
        let r = req(&[(537, A)]);
        assert_eq!(
            verdict(&f, &r, 537),
            Verdict::Unknown {
                why: "run_unbound:20".into()
            },
            "{prs:?}"
        );
        let (p, done) = go(&f, &r);
        assert!(f.writes().is_empty());
        assert_eq!(exit_code(&p, &done), 3);
    }
}

#[test]
fn owner_needs_event_sha_branch_repo_and_a_single_pr() {
    let p = pull(537, "open", A, &[]).unwrap();
    let ours = prun(1, CI, "queued", None, A, 537);
    assert_eq!(owner(&ours, &p), Owner::Ours);
    let mut r = ours.clone();
    r.head_branch = "other".into();
    assert_eq!(owner(&r, &p), Owner::Others);
    let mut r = ours.clone();
    r.head_repo = Some("fork/ez2-aib".into());
    assert_eq!(owner(&r, &p), Owner::Others);
    let mut r = ours.clone();
    r.head_sha = B.into();
    assert_eq!(owner(&r, &p), Owner::Others);
    let mut r = ours.clone();
    r.prs = vec![600];
    assert_eq!(owner(&r, &p), Owner::Others);
    let mut r = ours;
    r.event = "push".into();
    assert_eq!(owner(&r, &p), Owner::Others);
}

// ── ci.yml runs on `labeled`: the run the label starts ────────────────────

/// Adding `in-integ` starts one more run on the same SHA. It is read again
/// after `settle` and cancelled too; another PR's run and a push run that
/// show up are not.
#[test]
fn the_run_the_label_itself_starts_is_cancelled_too() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    let settled = RefCell::new(0);
    let done = apply(&f, &r, &p, &|| {
        *settled.borrow_mut() += 1;
        f.w.borrow_mut().runs.insert(
            A.into(),
            Ok(vec![
                prun(10, CI, "in_progress", None, A, 537),
                prun(11, AIBD, "in_progress", None, A, 537),
                prun(14, CI, "queued", None, A, 537),
                push_run(15, A, 537),
                prun(16, CI, "queued", None, A, 600),
            ]),
        );
    });
    assert_eq!(*settled.borrow(), 1);
    assert_eq!(
        f.writes(),
        vec!["label #537 in-integ", "cancel 10", "cancel 11", "cancel 14"]
    );
    assert_eq!(exit_code(&p, &done), 0);
}

/// When the label was already there nothing new starts: no wait, no re-read.
#[test]
fn no_settle_when_no_label_was_added() {
    let mut w = world();
    w.pulls.insert(537, pull(537, "open", A, &["in-integ"]));
    let f = Fake::new(w);
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    let settled = RefCell::new(0);
    apply(&f, &r, &p, &|| *settled.borrow_mut() += 1);
    assert_eq!(*settled.borrow(), 0);
}

/// A re-read that fails is a failure (exit 3): the label's run may be going.
#[test]
fn a_failed_recheck_is_reported() {
    let mut w = world();
    w.runs
        .insert(A.into(), Ok(vec![prun(10, CI, "queued", None, A, 537)]));
    let f = Fake::new(w);
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    let done = apply(&f, &r, &p, &|| {
        f.w.borrow_mut()
            .runs
            .insert(A.into(), Err(GhError("http:502".into())));
    });
    assert!(
        done[0]
            .runs
            .iter()
            .any(|(_, s)| s.starts_with("failed:recheck:")),
        "{:?}",
        done[0].runs
    );
    assert_eq!(exit_code(&p, &done), 3);
}

// ── bb7dc #572 point 3: state changes between plan and each write ─────────

/// The integration PR is closed after the plan, before the label.
#[test]
fn integration_closed_after_plan_nothing_is_written() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    f.w.borrow_mut()
        .pulls
        .insert(INTEG, pull(INTEG, "closed", IH, &[]));
    let done = apply(&f, &r, &p, &|| {});
    assert!(f.writes().is_empty(), "{:?}", f.writes());
    assert_eq!(done[0].label, "aborted:integ_not_open:closed");
    assert_eq!(exit_code(&p, &done), 3);
}

/// The integration PR moves to a new head after the plan.
#[test]
fn integration_head_moved_after_plan_nothing_is_written() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    {
        let mut w = f.w.borrow_mut();
        w.pulls.insert(INTEG, pull(INTEG, "open", B, &[]));
        w.runs.insert(
            B.into(),
            Ok(vec![prun(902, CI, "in_progress", None, B, INTEG)]),
        );
    }
    let done = apply(&f, &r, &p, &|| {});
    assert!(f.writes().is_empty(), "{:?}", f.writes());
    assert!(
        done[0].label.starts_with("aborted:integ_head_moved:"),
        "{}",
        done[0].label
    );
}

/// The original pushes B after the plan, before the label.
#[test]
fn original_pushed_after_plan_is_not_labelled() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    f.w.borrow_mut()
        .pulls
        .insert(537, pull(537, "open", B, &[]));
    let done = apply(&f, &r, &p, &|| {});
    assert!(f.writes().is_empty(), "{:?}", f.writes());
    assert_eq!(done[0].label, "aborted:head_moved");
}

/// The original pushes B between the fresh check and the label call: the
/// head is read again after labelling; nothing is cancelled, reported (3).
#[test]
fn original_pushed_between_the_check_and_the_label_cancels_nothing() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    f.before("label", 1, |w| {
        w.pulls.insert(537, pull(537, "open", B, &[]));
    });
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(f.writes(), vec!["label #537 in-integ"]);
    assert_eq!(done[0].label, format!("added_head_moved:{B}"));
    assert!(done[0].runs.is_empty());
    assert_eq!(exit_code(&p, &done), 3);
}

/// The integration CI is cancelled after the label, before the first cancel.
#[test]
fn integration_ci_cancelled_before_a_cancel_stops_the_rest() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    f.before("label", 1, |w| {
        w.runs.insert(
            IH.into(),
            Ok(vec![prun(
                900,
                CI,
                "completed",
                Some("cancelled"),
                IH,
                INTEG,
            )]),
        );
    });
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(f.writes(), vec!["label #537 in-integ"]);
    assert_eq!(
        done[0].runs,
        vec![(10, format!("aborted:integ_ci_not_started:{CI}"))]
    );
    assert_eq!(exit_code(&p, &done), 3);
}

/// The integration PR is closed between the two cancels: the second one is
/// not sent.
#[test]
fn integration_closed_between_two_cancels_stops_the_second() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    f.before("cancel", 1, |w| {
        w.pulls.insert(INTEG, pull(INTEG, "closed", IH, &[]));
    });
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(f.writes(), vec!["label #537 in-integ", "cancel 10"]);
    assert_eq!(
        done[0].runs,
        vec![
            (10, "cancelled".into()),
            (11, "aborted:integ_not_open:closed".into())
        ]
    );
    assert_eq!(exit_code(&p, &done), 3);
}

/// A run that finished between the plan and its cancel is left (not a
/// failure, not cancelled).
#[test]
fn a_run_that_finished_meanwhile_is_left_alone() {
    let f = Fake::new(world());
    let r = req(&[(537, A)]);
    let p = plan(&f, &r);
    f.before("cancel", 1, |w| {
        w.runs.insert(
            A.into(),
            Ok(vec![
                prun(10, CI, "queued", None, A, 537),
                prun(11, AIBD, "completed", Some("success"), A, 537),
            ]),
        );
    });
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(f.writes(), vec!["label #537 in-integ", "cancel 10"]);
    assert_eq!(done[0].runs[1], (11, "left:not_cancellable_now".into()));
    assert_eq!(exit_code(&p, &done), 0);
}

// ── bb7dc #572 f7a02962: a new UNKNOWN mid-apply stops the whole batch ─────

/// Two originals: #537 at A (runs 10, 11) and #538 at C (run 40), both in
/// the integration.
fn world2() -> World {
    let mut w = world();
    w.pulls.insert(538, pull(538, "open", C, &[]));
    w.runs
        .insert(C.into(), Ok(vec![prun(40, CI, "queued", None, C, 538)]));
    w.contained.insert((C.into(), IH.into()));
    w
}

fn req2() -> Request {
    req(&[(537, A), (538, C)])
}

fn stopped(a: &Applied) -> bool {
    a.label.starts_with("batch_stopped:") && a.runs.is_empty()
}

#[test]
fn two_originals_both_yield_when_nothing_moves() {
    let f = Fake::new(world2());
    let (p, done) = go(&f, &req2());
    assert_eq!(
        f.writes(),
        vec![
            "label #537 in-integ",
            "cancel 10",
            "cancel 11",
            "label #538 in-integ",
            "cancel 40"
        ]
    );
    assert_eq!(exit_code(&p, &done), 0);
}

/// #537 turns unreadable after the plan: its fresh check before the label
/// fails; #538 is not touched either.
#[test]
fn unknown_before_the_first_label_stops_every_later_pr() {
    let f = Fake::new(world2());
    let r = req2();
    let p = plan(&f, &r);
    f.w.borrow_mut()
        .pulls
        .insert(537, Err(GhError("http:502".into())));
    let done = apply(&f, &r, &p, &|| {});
    assert!(f.writes().is_empty(), "{:?}", f.writes());
    assert!(
        done[0].label.starts_with("aborted:pull_unreadable"),
        "{}",
        done[0].label
    );
    assert!(stopped(&done[1]), "{:?}", done[1]);
    assert_eq!(exit_code(&p, &done), 3);
}

/// #537's runs turn unreadable after its label, before its first cancel:
/// the label stays (reported), no cancel is sent, #538 is not touched.
#[test]
fn unknown_before_a_cancel_stops_the_rest_of_the_batch() {
    let f = Fake::new(world2());
    let r = req2();
    let p = plan(&f, &r);
    f.before("label", 1, |w| {
        w.runs.insert(A.into(), Err(GhError("http:502".into())));
    });
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(f.writes(), vec!["label #537 in-integ"]);
    assert_eq!(done[0].label, "added");
    assert!(
        done[0].runs[0].1.starts_with("aborted:runs_unreadable"),
        "{:?}",
        done[0].runs
    );
    assert_eq!(done[0].runs.len(), 1, "run 11 is not tried");
    assert!(stopped(&done[1]), "{:?}", done[1]);
    assert_eq!(exit_code(&p, &done), 3);
}

/// #537's head cannot be read back after its label: nothing more.
#[test]
fn unreadable_head_after_a_label_stops_the_rest_of_the_batch() {
    let f = Fake::new(world2());
    let r = req2();
    let p = plan(&f, &r);
    f.before("label", 1, |w| {
        w.pulls.insert(537, Err(GhError("http:502".into())));
    });
    let done = apply(&f, &r, &p, &|| {});
    assert_eq!(f.writes(), vec!["label #537 in-integ"]);
    assert!(
        done[0].label.starts_with("added_head_moved:unreadable:"),
        "{}",
        done[0].label
    );
    assert!(stopped(&done[1]), "{:?}", done[1]);
    assert_eq!(exit_code(&p, &done), 3);
}

/// The second PR's head moves after the first PR is done: the first stays
/// done (reported), the second gets nothing, and there is no settle re-read
/// for the first (no wait, no new cancel).
#[test]
fn a_stop_on_the_second_pr_keeps_the_first_as_done_and_skips_the_recheck() {
    let f = Fake::new(world2());
    let r = req2();
    let p = plan(&f, &r);
    f.before("cancel", 2, |w| {
        w.pulls.insert(538, pull(538, "open", B, &[]));
    });
    let settled = RefCell::new(0);
    let done = apply(&f, &r, &p, &|| *settled.borrow_mut() += 1);
    assert_eq!(
        f.writes(),
        vec!["label #537 in-integ", "cancel 10", "cancel 11"]
    );
    assert_eq!(
        done[0].runs[..2],
        [(10, "cancelled".into()), (11, "cancelled".into())]
    );
    assert!(
        done[0].runs[2].1.starts_with("batch_stopped:recheck:"),
        "{:?}",
        done[0].runs
    );
    assert_eq!(done[1].label, "aborted:head_moved");
    assert_eq!(*settled.borrow(), 0);
    assert_eq!(exit_code(&p, &done), 3);
}

/// The settle re-read of #537 fails: #538's re-read and late cancels are
/// not done.
#[test]
fn unknown_at_the_settle_recheck_stops_the_later_rechecks() {
    let f = Fake::new(world2());
    let r = req2();
    let p = plan(&f, &r);
    let done = apply(&f, &r, &p, &|| {
        let mut w = f.w.borrow_mut();
        w.runs.insert(A.into(), Err(GhError("http:502".into())));
        w.runs.insert(
            C.into(),
            Ok(vec![
                prun(40, CI, "in_progress", None, C, 538),
                prun(41, CI, "queued", None, C, 538),
            ]),
        );
    });
    assert!(
        !f.writes().contains(&"cancel 41".to_owned()),
        "{:?}",
        f.writes()
    );
    assert!(done[0]
        .runs
        .iter()
        .any(|(_, s)| s.starts_with("failed:recheck:")));
    assert!(
        done[1]
            .runs
            .iter()
            .any(|(_, s)| s.starts_with("batch_stopped:recheck:")),
        "{:?}",
        done[1].runs
    );
    assert_eq!(exit_code(&p, &done), 3);
}

/// A write the API refuses is reported but is not an UNKNOWN: the batch
/// goes on.
#[test]
fn a_refused_cancel_does_not_stop_the_batch() {
    let mut w = world2();
    w.cancel_fails.insert(10);
    let f = Fake::new(w);
    let (p, done) = go(&f, &req2());
    assert!(
        f.writes().contains(&"cancel 40".to_owned()),
        "{:?}",
        f.writes()
    );
    assert_eq!(done[0].runs[0], (10, "failed:http:500".into()));
    assert_eq!(exit_code(&p, &done), 3);
}
