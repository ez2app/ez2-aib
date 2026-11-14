//! `aib-drive next` layers 1–4 (S79 n-1, IFACE-s79-drive §2): the order of decide's
//! intents, what goes to `unknown` instead of the rows, and the seat facts it reads.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(dead_code)] // helpers shared in shape with tests/decide.rs

use aib_drive::decide::*;
use aib_drive::next::*;
use std::collections::BTreeSet;

const MAIN: &str = "e3ef2ae92272dafa8f8896f919844a1cf3055386";
const AT: &str = "2026-11-15T00:30:00.000Z";

fn s(x: &str) -> String {
    x.to_owned()
}

fn review(by: &str, head: &str, pass: bool, counts: [u32; 4], from: Option<&str>) -> ReviewLine {
    ReviewLine {
        by: Known::Known(s(by)),
        evidence: format!("REVIEW-{by}-{head}"),
        head: s(head),
        pass,
        counts,
        from: from.map(s),
        findings: Known::Unknown(s("not read")),
        closes: vec![],
    }
}

fn closing(review: &ReviewLine, ids: &[&str]) -> Closing {
    Closing {
        review: review.evidence.clone(),
        findings: Known::Known(ids.iter().map(|x| s(x)).collect()),
    }
}

fn verify(by: &str, kind: &str, head: &str, pass: bool, not_checked: Option<&str>) -> VerifyLine {
    VerifyLine {
        by: Known::Known(s(by)),
        evidence: format!("VERIFY-{kind}-{by}-{head}"),
        kind: s(kind),
        head: s(head),
        pass,
        not_checked: not_checked.map(s),
    }
}

fn cell(name: &str, state: CellState) -> Cell {
    Cell {
        name: s(name),
        run: 42,
        state,
        after_ready: true,
        cancelled_by_us: false,
        rerun_done: false,
        class: None,
    }
}

/// A PR that should merge: one commit, reviewed in full by a non-author,
/// every required cell green after ready, knives fine.
fn pr(number: u64, head: &str) -> Pr {
    Pr {
        number,
        owner: s("a9136"),
        head: s(head),
        head_ref: format!("feat/x-{number}"),
        base_ref: s("main"),
        base_head: s(MAIN),
        expected_base: None,
        draft: false,
        blocks_release: false,
        waiting_secs: 60,
        prev_head: None,
        commits: Known::Known(vec![s(head)]),
        authors: Known::Known(BTreeSet::from([s("a9136")])),
        touches_guarded: Known::Known(false),
        cells: Known::Known(vec![cell("rust (macos-latest)", CellState::Success)]),
        stale_runs: vec![],
        knives: Known::Known(KnifeScan::Ok),
        reviews: vec![review("043dd", head, true, [0, 0, 1, 2], None)],
        verifies: vec![],
        unknown_notes: vec![],
        needs_verify: vec![],
        verify_carry: None,
        integ_parts: None,
        trial: Known::Known(vec![]),
        reviewer: Some(s("043dd")),
    }
}

fn snap(prs: Vec<Pr>) -> Snapshot {
    Snapshot {
        repo: s("ez2app/ez2-aib"),
        roles: Roles {
            captain: s("a63ab"),
            collector: s("f1fae"),
        },
        freeze: Known::Known(None),
        required_cells: Known::Known(vec![s("rust (macos-latest)")]),
        rules_version: Known::Known(s("RULES-seats-aib2 r42")),
        merge_slot_free: Known::Known(true),
        busy_seats: BTreeSet::new(),
        prs,
        rulings: vec![],
        done: BTreeSet::new(),
    }
}

fn h(n: u64) -> String {
    format!("{n:040x}")
}

/// A PR with no review yet: decide asks `reviewer` for one.
fn unreviewed(n: u64, owner: &str, reviewer: &str) -> Pr {
    let mut p = pr(n, &h(n));
    p.owner = s(owner);
    p.authors = Known::Known(BTreeSet::from([s(owner)]));
    p.reviews = vec![];
    p.reviewer = Some(s(reviewer));
    p
}

fn fact(seat: &str, dispatchable: &str, size: &str) -> SeatFact {
    SeatFact {
        seat: s(seat),
        dispatchable: s(dispatchable),
        size: s(size),
    }
}

fn gh_ok() -> Vec<Source> {
    vec![Source {
        name: s("gh"),
        state: s("ok"),
        at: Some(s(AT)),
    }]
}

fn next_of(prs: Vec<Pr>, seats: Option<&[SeatFact]>) -> Next {
    let snap = snap(prs);
    let intents = decide(&snap);
    rank(&snap, &intents, seats, gh_ok(), AT)
}

fn kinds(n: &Next) -> Vec<(u8, String, u64)> {
    n.rows
        .iter()
        .map(|r| (r.layer, r.kind.clone(), r.pr))
        .collect()
}

/// Layer 1 (merge) before layer 3 (review); ranks count from 1; every source read and
/// nothing unknown ⇒ complete, rc 0.
#[test]
fn a_merge_comes_before_a_review_and_all_read_is_complete() {
    let seats = [fact("043dd", "yes", "review")];
    let n = next_of(
        vec![unreviewed(2, "a9136", "043dd"), pr(1, &h(1))],
        Some(&seats),
    );
    assert_eq!(n.schema, SCHEMA);
    assert_eq!(
        kinds(&n),
        vec![(1, s("merge"), 1), (3, s("review"), 2)],
        "{n:?}"
    );
    assert_eq!(n.rows[0].rank, 1);
    assert_eq!(n.rows[1].to.as_deref(), Some("043dd"));
    assert!(n.complete, "{n:?}");
    assert_eq!(n.rc(), 0);
}

/// The seat facts not read: a review is not given to a seat not known to be free; it goes
/// to `unknown`, the answer is incomplete (rc 3), and the merge still shows.
#[test]
fn without_seat_facts_a_review_is_unknown_not_dispatched() {
    let n = next_of(vec![unreviewed(2, "a9136", "043dd"), pr(1, &h(1))], None);
    assert_eq!(kinds(&n), vec![(1, s("merge"), 1)]);
    assert_eq!(n.unknown.len(), 1, "{n:?}");
    assert!(n.unknown[0].what.contains("#2"));
    assert!(!n.complete);
    assert_eq!(n.rc(), 3);
    let seats = n.sources.iter().find(|x| x.name == "seats").unwrap();
    assert_eq!(seats.state, "unsupported");
}

/// A seat whose facts say "unknown" is not "free" either.
#[test]
fn a_seat_that_may_or_may_not_be_free_is_unknown() {
    let seats = [fact("043dd", "unknown", "none")];
    let n = next_of(vec![unreviewed(2, "a9136", "043dd")], Some(&seats));
    assert!(n.rows.is_empty(), "{n:?}");
    assert_eq!(n.unknown.len(), 1);
}

/// A busy seat keeps its row, marked, after the rows that can be done now.
#[test]
fn a_busy_seat_s_review_is_marked_and_goes_after_a_free_one() {
    let seats = [fact("043dd", "no", "none"), fact("1ced7", "yes", "review")];
    let n = next_of(
        vec![
            unreviewed(2, "a9136", "043dd"),
            unreviewed(3, "a9136", "1ced7"),
        ],
        Some(&seats),
    );
    assert_eq!(kinds(&n), vec![(3, s("review"), 3), (3, s("review"), 2)]);
    assert_eq!(n.rows[1].needs, "seat_busy");
    assert_eq!(n.rows[0].needs, "none");
}

/// A review for a seat whose size has no review in it is marked, not dropped.
#[test]
fn a_review_for_a_seat_that_takes_a_slice_now_is_marked() {
    let seats = [fact("043dd", "yes", "slice")];
    let n = next_of(vec![unreviewed(2, "a9136", "043dd")], Some(&seats));
    assert_eq!(n.rows[0].needs, "seat_size_slice");
}

/// A PR whose review note does not read is listed in `unknown` once, never in the rows.
#[test]
fn a_pr_with_an_unreadable_note_is_unknown_and_never_a_row() {
    let mut bad = pr(1, &h(1));
    bad.unknown_notes = vec![s("REVIEW-x.md: verdict")];
    let n = next_of(vec![bad, pr(2, &h(2))], Some(&[]));
    assert_eq!(kinds(&n), vec![(1, s("merge"), 2)], "{n:?}");
    assert_eq!(n.unknown.len(), 1);
    assert!(n.unknown[0].why.contains("讀不懂"));
    assert!(!n.complete);
}

/// In a layer: the PR more others are stacked on goes first, then the one that waited
/// longer, then the lower number.
#[test]
fn in_a_layer_unblocks_then_waited_then_number() {
    let seats = [fact("043dd", "yes", "review")];
    let mut a = unreviewed(2, "a9136", "043dd");
    a.waiting_secs = 600;
    let b = unreviewed(3, "a9136", "043dd");
    let mut on_b = pr(9, &h(9));
    on_b.base_ref = b.head_ref.clone();
    let mut c = unreviewed(4, "a9136", "043dd");
    c.waiting_secs = 6000;
    let snap = snap(vec![a, b, c, on_b]);
    let intents: Vec<Intent> = decide(&snap)
        .into_iter()
        .filter(|i| matches!(i, Intent::AskReview { .. }))
        .collect();
    let n = rank(&snap, &intents, Some(&seats), gh_ok(), AT);
    let order: Vec<u64> = n.rows.iter().map(|r| r.pr).collect();
    assert_eq!(order, vec![3, 4, 2], "{n:?}");
    assert_eq!(n.rows[0].unblocks, 1);
    assert_eq!(n.rows[1].waited_min, 100);
}

/// A seat that has its own PR waiting for review is asked first (a swap).
#[test]
fn a_review_swap_goes_first_among_equals() {
    let seats = [
        fact("043dd", "yes", "review"),
        fact("1ced7", "yes", "review"),
        fact("b433b", "yes", "review"),
    ];
    let n = next_of(
        vec![
            unreviewed(7, "a9136", "1ced7"),
            unreviewed(8, "a9136", "043dd"),
            unreviewed(9, "043dd", "b433b"),
        ],
        Some(&seats),
    );
    assert_eq!(n.rows[0].pr, 8, "{n:?}");
    assert!(n.rows[0].why_him.contains("交換審查"));
}

/// `--seat`: only that seat's rows, ranked again from 1.
#[test]
fn for_seat_keeps_only_that_seat_s_rows() {
    let seats = [
        fact("043dd", "yes", "review"),
        fact("1ced7", "yes", "review"),
    ];
    let n = next_of(
        vec![
            unreviewed(2, "a9136", "043dd"),
            unreviewed(3, "a9136", "1ced7"),
        ],
        Some(&seats),
    )
    .for_seat("1ced7");
    assert_eq!(kinds(&n), vec![(3, s("review"), 3)]);
    assert_eq!(n.rows[0].rank, 1);
}

/// What needs a human never becomes a row; the JSON round-trips; the text names each row.
#[test]
fn the_answer_reads_back_from_json_and_renders_each_row() {
    let seats = [fact("043dd", "yes", "review")];
    let n = next_of(
        vec![unreviewed(2, "a9136", "043dd"), pr(1, &h(1))],
        Some(&seats),
    );
    let back: Next = serde_json::from_str(&serde_json::to_string(&n).unwrap()).unwrap();
    assert_eq!(back, n);
    let text = render(&n);
    assert!(text.contains("#1 可合"), "{text}");
    assert!(text.contains("審 #2"), "{text}");
}
