//! G2: comparing the dry-run with what was really done (043dd review of
//! 33e9a0eb, M1 / M2 / L1 / L2). Each guard has its own test that says which
//! answer it must give.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::{compare, Comparison, DecisionRecord, Event, MatchStreak, Observation};

const H1: &str = "7bcb6ee014c446d41455fa32664e646860421fe4";
const H2: &str = "cca3249e502694a89becbd48f7db31b072b9098f";

fn rec(source: &str, id: &str, decision: &str, version: &str) -> Observation<DecisionRecord> {
    Observation::Known {
        value: DecisionRecord {
            event_source: source.into(),
            event_id: id.into(),
            decision: decision.into(),
        },
        observed_at: 1,
        version: version.into(),
    }
}

fn ev(id: &str) -> Event {
    Event {
        source: "gh".into(),
        id: id.into(),
        object: "pr".into(),
        observed_at: 1,
    }
}

fn pr(n: u64, head: &str) -> String {
    format!("pr/{n}@{head}")
}

#[test]
fn the_same_event_decision_and_state_match_and_another_decision_differs() {
    let id = pr(678, H1);
    assert_eq!(
        compare(&rec("gh", &id, "wait", "v1"), &rec("gh", &id, "wait", "v1")),
        Comparison::Match
    );
    assert_eq!(
        compare(
            &rec("gh", &id, "wait", "v1"),
            &rec("gh", &id, "merge", "v1")
        ),
        Comparison::Different
    );
}

#[test]
fn an_empty_source_id_or_decision_is_unknown() {
    let id = pr(678, H1);
    let ok = rec("gh", &id, "wait", "v1");
    assert_eq!(
        compare(&rec("", &id, "wait", "v1"), &ok),
        Comparison::Unknown
    );
    assert_eq!(
        compare(&rec("gh", "", "wait", "v1"), &rec("gh", "", "wait", "v1")),
        Comparison::Unknown
    );
    assert_eq!(
        compare(&rec("gh", &id, "", "v1"), &rec("gh", &id, "", "v1")),
        Comparison::Unknown
    );
    assert_eq!(compare(&ok, &rec("gh", &id, "", "v1")), Comparison::Unknown);
}

#[test]
fn another_event_or_an_unknown_side_is_unknown() {
    let ok = rec("gh", &pr(678, H1), "wait", "v1");
    assert_eq!(
        compare(&ok, &rec("gh", &pr(667, H2), "wait", "v1")),
        Comparison::Unknown
    );
    assert_eq!(
        compare(&ok, &rec("dm", &pr(678, H1), "wait", "v1")),
        Comparison::Unknown
    );
    let unk = Observation::Unknown {
        reason: "gh failed".into(),
    };
    assert_eq!(compare(&ok, &unk), Comparison::Unknown);
    assert_eq!(compare(&unk, &ok), Comparison::Unknown);
}

/// L1: a dry-run decided on an older state is not a match, even with the same answer.
#[test]
fn decisions_made_on_different_states_are_unknown() {
    let id = pr(678, H1);
    assert_eq!(
        compare(
            &rec("gh", &id, "merge", "rules-a"),
            &rec("gh", &id, "merge", "rules-b")
        ),
        Comparison::Unknown
    );
    assert_eq!(
        compare(&rec("gh", &id, "merge", ""), &rec("gh", &id, "merge", "")),
        Comparison::Unknown
    );
}

#[test]
fn matches_on_three_events_count_to_three() {
    let mut s = MatchStreak::default();
    for (i, (n, h)) in [(678, H1), (667, H2), (661, H1)].into_iter().enumerate() {
        let id = pr(n, h);
        let got = s.observe(
            &ev(&id),
            &rec("gh", &id, "wait", "v1"),
            &rec("gh", &id, "wait", "v1"),
        );
        assert_eq!(got, i as u32 + 1);
    }
    assert_eq!(s.count(), 3);
}

/// M2: the journal repeats an unchanged event every round; that neither
/// counts nor resets.
#[test]
fn the_same_event_again_with_the_same_decision_is_skipped() {
    let mut s = MatchStreak::default();
    let a = pr(678, H1);
    let b = pr(667, H2);
    let m = |s: &mut MatchStreak, id: &str| {
        s.observe(
            &ev(id),
            &rec("gh", id, "wait", "v1"),
            &rec("gh", id, "wait", "v1"),
        )
    };
    assert_eq!(m(&mut s, &a), 1);
    assert_eq!(m(&mut s, &a), 1);
    assert_eq!(m(&mut s, &a), 1);
    assert_eq!(m(&mut s, &b), 2);
}

#[test]
fn the_same_event_with_another_decision_resets() {
    let mut s = MatchStreak::default();
    let a = pr(678, H1);
    let b = pr(667, H2);
    s.observe(
        &ev(&b),
        &rec("gh", &b, "wait", "v1"),
        &rec("gh", &b, "wait", "v1"),
    );
    assert_eq!(
        s.observe(
            &ev(&a),
            &rec("gh", &a, "wait", "v1"),
            &rec("gh", &a, "wait", "v1")
        ),
        2
    );
    assert_eq!(
        s.observe(
            &ev(&a),
            &rec("gh", &a, "merge", "v1"),
            &rec("gh", &a, "merge", "v1")
        ),
        0
    );
}

#[test]
fn a_different_or_unknown_comparison_resets() {
    let mut s = MatchStreak::default();
    let a = pr(678, H1);
    let b = pr(667, H2);
    let c = pr(661, H1);
    let m = |s: &mut MatchStreak, id: &str| {
        s.observe(
            &ev(id),
            &rec("gh", id, "wait", "v1"),
            &rec("gh", id, "wait", "v1"),
        )
    };
    assert_eq!(m(&mut s, &a), 1);
    assert_eq!(
        s.observe(
            &ev(&b),
            &rec("gh", &b, "wait", "v1"),
            &rec("gh", &b, "merge", "v1")
        ),
        0
    );
    assert_eq!(m(&mut s, &c), 1);
    let unk = Observation::Unknown {
        reason: "missing".into(),
    };
    assert_eq!(s.observe(&ev(&b), &rec("gh", &b, "wait", "v1"), &unk), 0);
}

#[test]
fn an_event_without_identity_or_a_record_about_another_event_resets() {
    let mut s = MatchStreak::default();
    let a = pr(678, H1);
    let b = pr(667, H2);
    let c = pr(661, H1);
    let m = |s: &mut MatchStreak, id: &str| {
        s.observe(
            &ev(id),
            &rec("gh", id, "wait", "v1"),
            &rec("gh", id, "wait", "v1"),
        )
    };
    assert_eq!(m(&mut s, &a), 1);
    assert_eq!(
        s.observe(
            &ev(""),
            &rec("gh", "", "wait", "v1"),
            &rec("gh", "", "wait", "v1")
        ),
        0
    );
    assert_eq!(m(&mut s, &c), 1);
    // the records agree with each other but are about b, not the event a
    assert_eq!(
        s.observe(
            &ev(&a),
            &rec("gh", &b, "wait", "v1"),
            &rec("gh", &b, "wait", "v1")
        ),
        0
    );
}

/// L2: the same PR at a new head is a new event.
#[test]
fn the_same_pr_at_a_new_head_is_another_event() {
    let mut s = MatchStreak::default();
    let old = pr(678, H1);
    let new = pr(678, H2);
    assert_eq!(
        s.observe(
            &ev(&old),
            &rec("gh", &old, "wait", "v1"),
            &rec("gh", &old, "wait", "v1")
        ),
        1
    );
    assert_eq!(
        s.observe(
            &ev(&new),
            &rec("gh", &new, "wait", "v1"),
            &rec("gh", &new, "wait", "v1")
        ),
        2
    );
}
