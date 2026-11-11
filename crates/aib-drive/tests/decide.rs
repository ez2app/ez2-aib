//! The aib-drive decision table against tonight's real events (11-09 / 11-10):
//! G1 (review passed, acceptance failed, the captain's ruling), the queue PR
//! (an unreviewed middle commit 967111ff, the acceptance carried from
//! cd33802b to 766fa8f6), plus every UNKNOWN path, the freeze, the
//! integration prefix, one shared write per round, and the 誰能裁 table.
//! Every SHA here is a real, resolvable commit of ez2app/ez2-aib.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::decide::*;
use std::collections::BTreeSet;

// queue PR #605 (feat/aibd-session-queue-integ-a9136)
const Q1: &str = "1625b1dcc63f41ca486e7e7e62ebc13091d1d886";
const Q2: &str = "6f2ffc25d621f8779218f9e36ac53d9f53a67276";
const Q3: &str = "967111ff447dcd6d6c561df2f860d95d698109d0";
const Q4: &str = "f6500f85d5336051a4d8fe514e66d49d692a5d8a";
const Q5: &str = "cd33802bf498b4c66d853097bbef5ffd85832eef";
const Q6: &str = "766fa8f663847a5ae78111f6005a1b4a8f41bd7a";
// G1
const G0: &str = "c75d433aa62cbd18a502eacdf132865f9f0c6118";
const G1: &str = "cbfaa52f1926d9e3a0958af364aba774fecfec45";
const MAIN: &str = "e3ef2ae92272dafa8f8896f919844a1cf3055386";

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

fn merges(out: &[Intent]) -> Vec<(u64, String)> {
    out.iter()
        .filter_map(|i| match i {
            Intent::Merge { pre } => Some((pre.pr, pre.head.clone())),
            _ => None,
        })
        .collect()
}

fn asks(out: &[Intent]) -> Vec<(AskTopic, Ruler)> {
    out.iter()
        .filter_map(|i| match i {
            Intent::Ask { topic, who, .. } => Some((*topic, *who)),
            _ => None,
        })
        .collect()
}

#[test]
fn a_clean_pr_merges_with_its_head_as_the_precondition() {
    let out = decide(&snap(vec![pr(607, G1)]));
    assert_eq!(merges(&out), vec![(607, s(G1))], "{out:#?}");
    let Some(Intent::Merge { pre }) = out.iter().find(|i| i.is_shared_write()) else {
        panic!("{out:#?}")
    };
    assert_eq!(pre.base_head, MAIN);
    assert!(pre.freeze_off);
}

/// G1: 043dd passed cbfaa52f, 1ced7's desktop acceptance failed. Without the
/// captain's ruling nothing merges and the question goes to the captain;
/// with it (bound to this head) it merges. A ruling by the wrong seat, or
/// bound to another head, does not count.
#[test]
fn g1_a_failed_acceptance_waits_for_the_captains_ruling_on_this_head() {
    let mut p = pr(588, G1);
    p.needs_verify = vec![s("g1")];
    p.verifies = vec![verify("1ced7", "g1", G1, false, Some("上限 10"))];
    let mut sn = snap(vec![p]);
    let out = decide(&sn);
    assert!(merges(&out).is_empty(), "{out:#?}");
    assert_eq!(asks(&out), vec![(AskTopic::VerifyAccept, Ruler::Captain)]);

    let ruling = |by: &str, head: &str| Ruling {
        topic: AskTopic::VerifyAccept,
        by: s(by),
        pr: Some(588),
        head: Some(s(head)),
        decision_file: None,
        andy_receipt: false,
    };
    sn.rulings = vec![ruling("f1fae", G1)];
    assert!(
        merges(&decide(&sn)).is_empty(),
        "the collector cannot rule this"
    );
    sn.rulings = vec![ruling("a63ab", G0)];
    assert!(merges(&decide(&sn)).is_empty(), "a ruling on another head");
    sn.rulings = vec![ruling("a63ab", G1)];
    assert_eq!(merges(&decide(&sn)), vec![(588, s(G1))]);
}

/// The queue PR: bb7dc reviewed up to 6f2ffc25, 043dd reviewed
/// 967111ff..f6500f85. Nobody reviewed 967111ff itself: no merge, and the
/// last non-author reviewer is asked for 6f2ffc25..head. A failing review
/// of 6f2ffc25..967111ff does not join the chain (bb7dc 121b1cbf H2); a
/// passing one does, and then it merges.
#[test]
fn queue_an_unreviewed_middle_commit_blocks_and_asks_for_exactly_that_range() {
    let mut p = pr(605, Q4);
    p.commits = Known::Known(vec![s(Q1), s(Q2), s(Q3), s(Q4)]);
    p.reviews = vec![
        review("bb7dc", Q2, true, [0, 0, 2, 0], None),
        review("043dd", Q4, true, [0, 0, 1, 2], Some(Q3)),
    ];
    let out = decide(&snap(vec![p.clone()]));
    assert!(merges(&out).is_empty(), "{out:#?}");
    let asked: Vec<_> = out
        .iter()
        .filter_map(|i| match i {
            Intent::AskReview { to, from, .. } => Some((to.clone(), from.clone())),
            _ => None,
        })
        .collect();
    assert_eq!(asked, vec![(s("043dd"), Some(s(Q2)))]);

    p.reviews
        .push(review("043dd", Q3, false, [0, 2, 3, 1], Some(Q2)));
    assert!(merges(&decide(&snap(vec![p.clone()]))).is_empty());
    p.reviews
        .push(review("bb7dc", Q3, true, [0, 0, 1, 0], Some(Q2)));
    assert_eq!(merges(&decide(&snap(vec![p]))), vec![(605, s(Q4))]);
}

#[test]
fn an_author_reviewing_their_own_commits_does_not_count() {
    let mut p = pr(605, Q6);
    // the resolver of a conflict is an author too
    p.authors = Known::Known(BTreeSet::from([s("a9136"), s("043dd")]));
    let out = decide(&snap(vec![p]));
    assert!(merges(&out).is_empty(), "{out:#?}");
    // and the rota does not hand the review to an author either
    assert!(!out
        .iter()
        .any(|i| matches!(i, Intent::AskReview { to, .. } if to == "043dd")));
}

#[test]
fn a_pass_that_leaves_a_medium_is_not_a_merge() {
    let mut p = pr(605, Q6);
    p.reviews = vec![review("043dd", Q6, true, [0, 1, 0, 0], None)];
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
}

#[test]
fn a_review_of_an_older_head_does_not_merge_the_new_one() {
    let mut p = pr(605, Q6);
    p.commits = Known::Known(vec![s(Q5), s(Q6)]);
    p.prev_head = Some(s(Q5));
    p.stale_runs = vec![7, 8];
    p.reviews = vec![review("1ced7", Q5, true, [0, 0, 0, 0], None)];
    let out = decide(&snap(vec![p]));
    assert!(merges(&out).is_empty(), "{out:#?}");
    assert!(out
        .iter()
        .any(|i| matches!(i, Intent::Stale { old, new, .. } if old == Q5 && new == Q6)));
    // the old head's runs give way; only one shared write per round
    let cancels: Vec<_> = out
        .iter()
        .filter(|i| matches!(i, Intent::Cancel { .. }))
        .collect();
    assert_eq!(cancels.len(), 1, "{out:#?}");
    assert!(out
        .iter()
        .any(|i| matches!(i, Intent::AskReview { from: Some(f), .. } if f == Q5)));
}

/// The chain must end in a review of the PR's own head: a commit list that
/// does not end at the head (a snapshot out of step) is covered to its last
/// commit by a passing review, but nothing reviewed the head itself.
#[test]
fn a_commit_list_that_does_not_end_at_the_head_does_not_merge() {
    let mut p = pr(605, Q6);
    p.commits = Known::Known(vec![s(Q4), s(Q5)]);
    p.reviews = vec![review("1ced7", Q5, true, [0, 0, 0, 0], None)];
    let out = decide(&snap(vec![p]));
    assert!(merges(&out).is_empty(), "{out:#?}");
}

#[test]
fn frozen_or_unknown_freeze_merges_nothing_and_reruns_nothing() {
    let mut p = pr(607, G1);
    if let Known::Known(c) = &mut p.cells {
        c.push(Cell {
            state: CellState::Cancelled,
            ..cell("smoke (macos-latest, 3.9)", CellState::Cancelled)
        });
    }
    let mut sn = snap(vec![p]);
    sn.required_cells = Known::Known(vec![
        s("rust (macos-latest)"),
        s("smoke (macos-latest, 3.9)"),
    ]);
    // not frozen: the cancelled cell is re-run (and nothing merges yet)
    let out = decide(&sn);
    assert!(
        out.iter().any(|i| matches!(i, Intent::Rerun { .. })),
        "{out:#?}"
    );
    assert!(merges(&out).is_empty());
    for f in [
        Known::Known(Some(s("aibd-v0.2.0-preview.4"))),
        Known::Unknown(s("rules file unreadable")),
    ] {
        sn.freeze = f;
        let out = decide(&sn);
        assert!(!out.iter().any(Intent::is_shared_write), "{out:#?}");
    }
    let mut clean = snap(vec![pr(1, G1)]);
    clean.freeze = Known::Known(Some(s("aibd-v0.2.0-preview.4")));
    assert!(merges(&decide(&clean)).is_empty());
}

#[test]
fn two_clean_prs_give_one_merge_and_the_release_blocker_goes_first() {
    let a = pr(600, Q6);
    let mut b = pr(601, G1);
    b.blocks_release = true;
    let out = decide(&snap(vec![a, b]));
    assert_eq!(merges(&out), vec![(601, s(G1))]);
    assert_eq!(out.iter().filter(|i| i.is_shared_write()).count(), 1);
}

#[test]
fn integration_prs_need_the_int_prefix_and_unmoved_originals() {
    let mut p = pr(602, G1);
    p.head_ref = s("integ/m1-a63ab-1110");
    p.integ_parts = Some(Known::Known(vec![]));
    assert!(merges(&decide(&snap(vec![p.clone()]))).is_empty());
    p.head_ref = s("int/m1-a63ab-1110");
    assert_eq!(merges(&decide(&snap(vec![p.clone()]))), vec![(602, s(G1))]);
    p.integ_parts = Some(Known::Known(vec![IntegPart {
        pr: 588,
        collected: s(G0),
        now: s(G1),
    }]));
    let out = decide(&snap(vec![p.clone()]));
    assert!(merges(&out).is_empty());
    assert_eq!(
        asks(&out),
        vec![(AskTopic::IntegPartMoved, Ruler::Collector)]
    );
    p.integ_parts = Some(Known::Unknown(s("compare API failed")));
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
}

#[test]
fn every_unknown_blocks_the_merge() {
    type Spoil = fn(&mut Pr, &mut Snapshot);
    let spoil: [(&str, Spoil); 9] = [
        ("authors", |p, _| {
            p.authors = Known::Unknown(s("no seat map"))
        }),
        ("commits", |p, _| p.commits = Known::Unknown(s("paging"))),
        ("cells", |p, _| p.cells = Known::Unknown(s("checks API"))),
        ("required", |_, sn| {
            sn.required_cells = Known::Unknown(s("rules"))
        }),
        ("guarded", |p, _| {
            p.touches_guarded = Known::Unknown(s("files API"))
        }),
        ("knives", |p, _| p.knives = Known::Unknown(s("no log"))),
        ("slot", |_, sn| {
            sn.merge_slot_free = Known::Unknown(s("lock"))
        }),
        ("freeze", |_, sn| sn.freeze = Known::Unknown(s("rules"))),
        ("rules", |_, sn| {
            sn.rules_version = Known::Unknown(s("rules"))
        }),
    ];
    for (what, f) in spoil {
        let mut p = pr(607, G1);
        let mut sn = snap(vec![]);
        f(&mut p, &mut sn);
        sn.prs = vec![p];
        assert!(merges(&decide(&sn)).is_empty(), "{what}");
    }
    // a required cell with no result is not a pass
    let mut p = pr(607, G1);
    p.cells = Known::Known(vec![]);
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
}

#[test]
fn cells_that_are_not_a_real_pass_block() {
    for c in [
        cell("rust (macos-latest)", CellState::Skipped),
        cell("rust (macos-latest)", CellState::Neutral),
        cell("rust (macos-latest)", CellState::Pending),
        Cell {
            after_ready: false,
            ..cell("rust (macos-latest)", CellState::Success)
        },
    ] {
        let mut p = pr(607, G1);
        p.cells = Known::Known(vec![c.clone()]);
        assert!(merges(&decide(&snap(vec![p]))).is_empty(), "{c:?}");
    }
    // two results with one name are ambiguous
    let mut p = pr(607, G1);
    p.cells = Known::Known(vec![
        cell("rust (macos-latest)", CellState::Success),
        cell("rust (macos-latest)", CellState::Failure),
    ]);
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
    // a red nobody classified goes to the captain, not to the author
    let mut p = pr(607, G1);
    p.cells = Known::Known(vec![cell("rust (macos-latest)", CellState::Failure)]);
    let out = decide(&snap(vec![p]));
    assert_eq!(asks(&out), vec![(AskTopic::Unclassified, Ruler::Captain)]);
    assert!(!out.iter().any(|i| matches!(i, Intent::Nudge { .. })));
    // a cancelled cell already re-run once is not re-run again
    let mut p = pr(607, G1);
    p.cells = Known::Known(vec![Cell {
        rerun_done: true,
        ..cell("rust (macos-latest)", CellState::Cancelled)
    }]);
    assert!(!decide(&snap(vec![p]))
        .iter()
        .any(|i| matches!(i, Intent::Rerun { .. })));
}

#[test]
fn the_queue_acceptance_carries_from_cd33802b_to_766fa8f6_only_when_the_snapshot_says_so() {
    let mut p = pr(605, Q6);
    p.needs_verify = vec![s("queue")];
    p.verifies = vec![verify("1ced7", "queue", Q5, true, None)];
    let out = decide(&snap(vec![p.clone()]));
    assert!(merges(&out).is_empty());
    assert!(out
        .iter()
        .any(|i| matches!(i, Intent::AskVerify { kind, .. } if kind == "queue")));
    p.verify_carry = Some(s(Q5));
    assert_eq!(merges(&decide(&snap(vec![p.clone()]))), vec![(605, s(Q6))]);
    // items not checked need the captain's word too
    p.verifies = vec![verify("1ced7", "queue", Q5, true, Some("真 claude 卡名額"))];
    assert_eq!(
        asks(&decide(&snap(vec![p]))),
        vec![(AskTopic::VerifyAccept, Ruler::Captain)]
    );
}

#[test]
fn what_the_journal_holds_is_not_asked_again_and_a_busy_seat_is_nudged() {
    let mut p = pr(605, Q6);
    p.reviews = vec![];
    let mut sn = snap(vec![p]);
    let out = decide(&sn);
    let key = out
        .iter()
        .find(|i| matches!(i, Intent::AskReview { .. }))
        .and_then(Intent::key)
        .unwrap();
    sn.done.insert(key);
    assert!(!decide(&sn)
        .iter()
        .any(|i| matches!(i, Intent::AskReview { .. })));
    sn.done.clear();
    sn.busy_seats.insert(s("043dd"));
    let out = decide(&sn);
    assert!(!out.iter().any(|i| matches!(i, Intent::AskReview { .. })));
    assert!(out
        .iter()
        .any(|i| matches!(i, Intent::Nudge { seat, .. } if seat == "043dd")));
}

#[test]
fn drafts_and_guarded_paths_never_merge() {
    let mut p = pr(607, G1);
    p.draft = true;
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
    let mut p = pr(607, G1);
    p.touches_guarded = Known::Known(true);
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
    let mut p = pr(607, G1);
    p.expected_base = Some(s("feat/aibd-stop-diag-a9136"));
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
}

#[test]
fn who_can_rule() {
    let roles = Roles {
        captain: s("a63ab"),
        collector: s("f1fae"),
    };
    let r = |topic, by: &str, file: Option<&str>, receipt| Ruling {
        topic,
        by: s(by),
        pr: None,
        head: None,
        decision_file: file.map(s),
        andy_receipt: receipt,
    };
    assert!(r(AskTopic::StopWork, "a63ab", None, false).valid(&roles));
    assert!(!r(AskTopic::StopWork, "f1fae", None, false).valid(&roles));
    assert!(r(AskTopic::CollectHead, "f1fae", None, false).valid(&roles));
    assert!(!r(AskTopic::CollectHead, "a63ab", None, false).valid(&roles));
    // the captain cannot rule what only Andy can, not even with a file
    assert!(!r(AskTopic::AndyOnly, "a63ab", Some("DECISION-x.md"), false).valid(&roles));
    assert!(r(AskTopic::AndyOnly, "andy", None, true).valid(&roles));
    // §14 undecided: the asking seat, with a DECISION file
    assert!(!r(AskTopic::Undecided, "a9136", None, false).valid(&roles));
    assert!(r(
        AskTopic::Undecided,
        "a9136",
        Some("DECISION-x-a9136-1110.md"),
        false
    )
    .valid(&roles));
    assert_eq!(ruler(AskTopic::LeaseOverride), Ruler::MachineOwner);
    assert_eq!(ruler(AskTopic::MergeOrder), Ruler::Captain);
}

#[test]
fn only_a_conforming_first_line_becomes_a_review() {
    use aib_core::firstline::parse_review;
    let ok = format!("結論: 通過 頭: {Q6} H/M/L/N: 0/0/0/0 範圍: 增量 {Q5}..{Q6}");
    let r = ReviewLine::from_parsed(Known::Known(s("1ced7")), "R1", &parse_review(&ok).unwrap())
        .unwrap();
    assert_eq!(r.from.as_deref(), Some(Q5));
    assert!(r.mergeable_at(Q6) && !r.mergeable_at(Q5));
    let short = format!("結論: 通過 頭: {Q6} H/M/L/N: 0/0/0/0 範圍: 增量 cd33802b..766fa8f6");
    assert!(ReviewLine::from_parsed(
        Known::Known(s("1ced7")),
        "R2",
        &parse_review(&short).unwrap()
    )
    .is_none());
}

/// bb7dc 121b1cbf H1: an empty required list is unknown, not "all green".
#[test]
fn an_empty_required_list_merges_nothing() {
    let mut sn = snap(vec![pr(607, G1)]);
    sn.required_cells = Known::Known(vec![]);
    assert!(merges(&decide(&sn)).is_empty());
}

/// bb7dc 121b1cbf H2: a full review of an older head that failed, plus an
/// incremental pass of the new commit, does not cover the branch.
#[test]
fn a_failed_full_review_plus_a_passing_increment_is_not_a_merge() {
    let mut p = pr(605, Q6);
    p.commits = Known::Known(vec![s(Q5), s(Q6)]);
    for counts in [[0, 0, 0, 0], [1, 0, 0, 0], [0, 1, 0, 0]] {
        let pass = counts == [0, 0, 0, 0];
        p.reviews = vec![
            review("043dd", Q5, !pass, counts, None),
            review("043dd", Q6, true, [0, 0, 0, 0], Some(Q5)),
        ];
        if pass {
            // the failing variant: verdict 不通過 with nothing counted
            p.reviews[0].pass = false;
        }
        assert!(
            merges(&decide(&snap(vec![p.clone()]))).is_empty(),
            "{counts:?}"
        );
    }
    p.reviews[0] = review("043dd", Q5, true, [0, 0, 2, 1], None);
    assert_eq!(merges(&decide(&snap(vec![p]))), vec![(605, s(Q6))]);
}

/// D20 (a63ab 11-10 02:02, tonight's coalition / decide flow): an old head
/// that failed plus an increment that passes is full coverage only when the
/// same reviewer wrote both, the increment is on this branch, and it lists
/// each old H / M closed (bb7dc 6ffcb86f H1 / M1).
#[test]
fn the_same_reviewer_closing_their_findings_in_an_increment_covers_the_branch() {
    let mut p = pr(605, Q6);
    p.commits = Known::Known(vec![s(Q5), s(Q6)]);
    let mut old = review("bb7dc", Q5, false, [1, 1, 0, 0], None);
    old.findings = Known::Known(vec![s("H1"), s("M1")]);
    let mut inc = review("bb7dc", Q6, true, [0, 0, 0, 0], Some(Q5));
    inc.closes = vec![closing(&old, &["H1", "M1"])];
    p.reviews = vec![old.clone(), inc.clone()];
    assert_eq!(merges(&decide(&snap(vec![p.clone()]))), vec![(605, s(Q6))]);

    let blocked = |reviews: Vec<ReviewLine>, why: &str| {
        let mut p = p.clone();
        p.reviews = reviews;
        assert!(merges(&decide(&snap(vec![p]))).is_empty(), "{why}");
    };
    // the increment does not list the old findings
    blocked(
        vec![
            old.clone(),
            review("bb7dc", Q6, true, [0, 0, 0, 0], Some(Q5)),
        ],
        "no list",
    );
    // M1: only part of them
    let mut part = inc.clone();
    part.closes = vec![closing(&old, &["H1"])];
    blocked(vec![old.clone(), part], "partial list");
    // M1: the list the snapshot could not read
    let mut unread = inc.clone();
    unread.closes[0].findings = Known::Unknown(s("body not read"));
    blocked(vec![old.clone(), unread], "list not read");
    // M1: the old review's own findings not read, or fewer than H + M
    let mut old_unread = old.clone();
    old_unread.findings = Known::Unknown(s("body not read"));
    blocked(vec![old_unread, inc.clone()], "old findings not read");
    let mut old_short = old.clone();
    old_short.findings = Known::Known(vec![s("H1")]);
    let mut inc_short = inc.clone();
    inc_short.closes = vec![closing(&old_short, &["H1"])];
    blocked(vec![old_short, inc_short], "old list shorter than H + M");
    // bb7dc 4f95e78b H1: ids that repeat, are empty, or are the wrong kind
    for (ids, why) in [
        (vec!["H1", "H1"], "repeated"),
        (vec!["H1", ""], "empty"),
        (vec!["H1", "M1"], "an M for an H"),
        (vec!["H1", "H2", "L1"], "not H / M"),
        (vec!["H1", "H"], "no number"),
    ] {
        let mut odd = review("bb7dc", Q5, false, [2, 0, 0, 0], None);
        odd.findings = Known::Known(ids.iter().map(|x| s(x)).collect());
        let mut inc_odd = inc.clone();
        inc_odd.closes = vec![closing(&odd, &["H1", "H2", "H", "M1", "L1", ""])];
        blocked(vec![odd, inc_odd], why);
    }
    // one H and one M listed for H 2 / M 1: only the H count can tell
    let mut few_h = review("bb7dc", Q5, false, [2, 1, 0, 0], None);
    few_h.findings = Known::Known(vec![s("H1"), s("M1")]);
    let mut inc_few_h = inc.clone();
    inc_few_h.closes = vec![closing(&few_h, &["H1", "M1"])];
    blocked(vec![few_h, inc_few_h], "fewer H ids than H");
    // a different reviewer closes them
    let mut other = inc.clone();
    other.by = Known::Known(s("1ced7"));
    blocked(vec![old.clone(), other], "different reviewer");
    // the old writer is not verified
    let mut unverified = old.clone();
    unverified.by = Known::Unknown(s("file name only"));
    blocked(vec![unverified, inc.clone()], "unverified writer");
    // the closing increment itself leaves a medium
    let mut leaves = inc.clone();
    leaves.counts = [0, 1, 0, 0];
    blocked(vec![old.clone(), leaves], "closing leaves a medium");
}

/// bb7dc 6ffcb86f H1: an increment `A..X` with X off this branch closes
/// nothing, even when another reviewer's `A..B` would carry the chain on.
#[test]
fn a_closing_increment_off_the_branch_closes_nothing() {
    let mut p = pr(605, Q6);
    p.commits = Known::Known(vec![s(Q5), s(Q6)]);
    let mut old = review("bb7dc", Q5, false, [1, 0, 0, 0], None);
    old.findings = Known::Known(vec![s("H1")]);
    let mut off = review("bb7dc", MAIN, true, [0, 0, 0, 0], Some(Q5));
    off.closes = vec![closing(&old, &["H1"])];
    let on = review("1ced7", Q6, true, [0, 0, 0, 0], Some(Q5));
    p.reviews = vec![old.clone(), off.clone(), on.clone()];
    let out = decide(&snap(vec![p.clone()]));
    assert!(merges(&out).is_empty(), "{out:#?}");
    // the same close on the branch is the fix cycle
    let mut here = off;
    here.head = s(Q6);
    p.reviews = vec![old, here, on];
    assert_eq!(merges(&decide(&snap(vec![p]))), vec![(605, s(Q6))]);
}

/// bb7dc 121b1cbf M1: a writer the snapshot could not verify (a file name's
/// claim) is never a non-author; the merge carries what it relied on.
#[test]
fn an_unverified_reviewer_does_not_count_and_the_merge_names_its_evidence() {
    let mut p = pr(607, G1);
    p.reviews[0].by = Known::Unknown(s("file name only"));
    let out = decide(&snap(vec![p.clone()]));
    assert!(merges(&out).is_empty(), "{out:#?}");
    let mut p = pr(607, G1);
    p.needs_verify = vec![s("g1")];
    p.verifies = vec![VerifyLine {
        by: Known::Unknown(s("file name only")),
        ..verify("1ced7", "g1", G1, true, None)
    }];
    assert!(merges(&decide(&snap(vec![p.clone()]))).is_empty());
    p.verifies[0].by = Known::Known(s("1ced7"));
    let out = decide(&snap(vec![p]));
    let Some(Intent::Merge { pre }) = out.iter().find(|i| i.is_shared_write()) else {
        panic!("{out:#?}")
    };
    assert_eq!(pre.rules_version, "RULES-seats-aib2 r42");
    assert_eq!(pre.reviews, vec![format!("REVIEW-043dd-{G1}")]);
    assert_eq!(pre.verifies, vec![format!("VERIFY-g1-1ced7-{G1}")]);
    assert_eq!(pre.runs, vec![(s("rust (macos-latest)"), 42)]);
}

// --- drive × aib-expect wiring (DESIGN-drive-expect-wiring, a9136 DECISION-drive-expect-cycle) ---

const EV: &str = "pr/600@766fa8f663847a5ae78111f6005a1b4a8f41bd7a";

/// The intents that are not the round's one shared write, as text, sorted.
fn not_shared(out: &[Intent]) -> Vec<String> {
    let mut v: Vec<String> = out
        .iter()
        .filter(|i| !i.is_shared_write())
        .map(|i| format!("{i:?}"))
        .collect();
    v.sort();
    v
}

#[test]
fn a_gap_puts_its_pr_first_within_a_level_and_only_reorders() {
    let both = || snap(vec![pr(600, Q6), pr(601, G1)]);
    let plain = decide(&both());
    // same level, same wait: the lower number first
    assert_eq!(merges(&plain), vec![(600, s(Q6))]);
    let weighted = decide_weighted(&both(), &BTreeSet::from([601]));
    assert_eq!(merges(&weighted), vec![(601, s(G1))]);
    // weights move the one shared write, nothing else (a9136 DECISION item 6)
    assert_eq!(not_shared(&plain), not_shared(&weighted));
    assert_eq!(decide_weighted(&both(), &BTreeSet::new()), plain);
    // a release blocker still goes first
    let mut c = pr(602, Q5);
    c.blocks_release = true;
    let w = decide_weighted(
        &snap(vec![pr(600, Q6), pr(601, G1), c]),
        &BTreeSet::from([601]),
    );
    assert_eq!(merges(&w), vec![(602, s(Q5))]);
}

fn side(table: Vec<serde_json::Value>, unknown: Option<&str>) -> aib_drive::observe::ExpectSide {
    aib_drive::observe::ExpectSide {
        table,
        unknown: unknown.map(str::to_owned),
    }
}

#[test]
fn goals_that_cannot_be_used_merge_nothing_and_the_report_says_why() {
    use aib_drive::observe::record_with;
    use std::collections::BTreeMap;
    let none = BTreeMap::new();
    let ok = record_with(
        1,
        "r",
        &none,
        Ok((snap(vec![pr(600, Q6)]), Some(side(vec![], None)))),
        Some(EV),
    );
    assert_eq!(merges(&ok.would), vec![(600, s(Q6))]);
    let rep = ok.report.unwrap();
    assert_eq!(rep.event, EV);
    assert!(rep.dry_run && rep.expect_unknown.is_none());
    assert_eq!(merges(&rep.next), vec![(600, s(Q6))]);

    let bad = record_with(
        1,
        "r",
        &none,
        Ok((
            snap(vec![pr(600, Q6)]),
            Some(side(vec![], Some("expect_unsigned"))),
        )),
        Some(EV),
    );
    assert!(merges(&bad.would).is_empty(), "{:?}", bad.would);
    let rep = bad.report.unwrap();
    assert_eq!(rep.expect_unknown.as_deref(), Some("expect_unsigned"));
    assert!(merges(&rep.next).is_empty());
    assert!(bad.would.iter().any(|i| matches!(i, Intent::Note { .. })));
    assert!(!rep.next.iter().any(|i| matches!(i, Intent::Note { .. })));
}

#[test]
fn the_report_lists_at_most_three_and_no_notes_and_its_rows_weight_decide() {
    use aib_drive::observe::record_with;
    use std::collections::BTreeMap;
    let rows = vec![
        serde_json::json!({"goal_id": "A1", "state": "pass", "pr": 600}),
        serde_json::json!({"goal_id": "A2", "state": "unverified", "why": "no_evidence", "pr": 601}),
    ];
    let r = record_with(
        1,
        "r",
        &BTreeMap::new(),
        Ok((
            snap(vec![pr(600, Q6), pr(601, G1)]),
            Some(side(rows.clone(), None)),
        )),
        Some(EV),
    );
    // only the non-passing row weighs: 601 merges, not 600
    assert_eq!(merges(&r.would), vec![(601, s(G1))]);
    let rep = r.report.unwrap();
    assert_eq!(rep.table, rows);
    assert!(rep.next.len() <= aib_drive::report::NEXT_MAX);
    assert!(!rep.next.iter().any(|i| matches!(i, Intent::Note { .. })));
    // no event (a timer round): weights, no report
    let t = record_with(
        1,
        "r",
        &BTreeMap::new(),
        Ok((snap(vec![pr(600, Q6), pr(601, G1)]), Some(side(rows, None)))),
        None,
    );
    assert_eq!(merges(&t.would), vec![(601, s(G1))]);
    assert!(t.report.is_none());
}
