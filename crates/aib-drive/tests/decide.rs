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
        closes: vec![],
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
/// same reviewer wrote both and the increment lists each old H / M closed.
#[test]
fn the_same_reviewer_closing_their_findings_in_an_increment_covers_the_branch() {
    let mut p = pr(605, Q6);
    p.commits = Known::Known(vec![s(Q5), s(Q6)]);
    let old = review("bb7dc", Q5, false, [1, 1, 0, 0], None);
    let mut inc = review("bb7dc", Q6, true, [0, 0, 0, 0], Some(Q5));
    inc.closes = vec![old.evidence.clone()];
    p.reviews = vec![old.clone(), inc.clone()];
    assert_eq!(merges(&decide(&snap(vec![p.clone()]))), vec![(605, s(Q6))]);

    // the increment does not list the old findings
    p.reviews = vec![
        old.clone(),
        review("bb7dc", Q6, true, [0, 0, 0, 0], Some(Q5)),
    ];
    assert!(merges(&decide(&snap(vec![p.clone()]))).is_empty());

    // a different reviewer closes them
    let mut other = inc.clone();
    other.by = Known::Known(s("1ced7"));
    p.reviews = vec![old.clone(), other];
    assert!(merges(&decide(&snap(vec![p.clone()]))).is_empty());

    // the old writer is not verified
    let mut unverified = old.clone();
    unverified.by = Known::Unknown(s("file name only"));
    p.reviews = vec![unverified, inc.clone()];
    assert!(merges(&decide(&snap(vec![p.clone()]))).is_empty());

    // the closing increment itself leaves a medium
    let mut leaves = inc.clone();
    leaves.counts = [0, 1, 0, 0];
    p.reviews = vec![old, leaves];
    assert!(merges(&decide(&snap(vec![p]))).is_empty());
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
