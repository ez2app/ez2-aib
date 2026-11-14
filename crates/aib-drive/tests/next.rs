//! `aib-drive next` layers 1–4 (S79 n-1, IFACE-s79-drive r2): ordered from aib-slice's due
//! and sweep JSON and the seat facts; what goes to `unknown` instead of the rows.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::next::*;
use serde_json::{json, Value};

const NOW: u64 = 1_794_672_000;
const AT: &str = "2026-11-14T16:00:00Z";

fn h(n: u64) -> String {
    format!("{n:040x}")
}

fn due_item(pr: u64, seat: &str, lacks: &[&str], waited_min: u64) -> Value {
    json!({"target": format!("#{pr} x"), "pr": pr, "branch": format!("feat/x-{pr}-{seat}-1115"),
        "seat": seat, "head": h(pr), "has": ["review"],
        "lacks": lacks.iter().map(|k| json!({"kind": k, "text": k, "n": null, "seconds": null, "names": []})).collect::<Vec<_>>(),
        "since": NOW - waited_min * 60})
}

fn fix_item(pr: u64, seat: &str) -> Value {
    json!({"target": format!("#{pr} y"), "pr": pr, "branch": format!("fix/y-{pr}-{seat}-1115"),
        "seat": seat, "head": h(pr), "has": [],
        "problems": [{"kind": "ci_red", "text": "CI 1 格紅(smoke)", "n": 1, "seconds": null, "names": ["smoke"]}],
        "since": NOW - 600})
}

fn due(merge: Vec<Value>, fix: Vec<Value>, complete: bool) -> Input<Due> {
    read_input(
        Ok(
            json!({"schema": DUE_SCHEMA, "generated_at": NOW, "complete": complete,
            "errors": if complete { vec![] } else { vec!["no_gh"] }, "seat": null,
            "merge": {"total": merge.len(), "items": merge},
            "fix": {"total": fix.len(), "items": fix}})
            .to_string(),
        ),
        DUE_SCHEMA,
    )
}

/// An open PR on `branch` of `seat`, reviewed at its head or not.
fn branch(pr: u64, seat: &str, reviewed: bool, age_h: u64) -> Value {
    json!({"branch": format!("feat/b-{pr}-{seat}-1115"), "seat": seat, "head": h(pr),
        "age_h": age_h, "pr": {"number": pr, "state": "open", "merged": false},
        "review": if reviewed { json!({"at_head": true}) } else { Value::Null },
        "stacked_on": [], "worktree": null, "class": 4, "abandonable": false})
}

fn sweep(branches: Vec<Value>, complete: bool) -> Input<Sweep> {
    read_input(
        Ok(
            json!({"schema": SWEEP_SCHEMA, "generated_at": NOW, "complete": complete,
            "errors": [], "branches": branches})
            .to_string(),
        ),
        SWEEP_SCHEMA,
    )
}

fn seats(list: &[(&str, &str, &str)]) -> Input<Seats> {
    let s: Vec<Value> = list
        .iter()
        .map(|(seat, d, size)| {
            json!({"seat": seat, "dispatchable": d, "size": size, "role": "dev",
            "alive": {"v": "yes"}, "first": []})
        })
        .collect();
    read_input(
        Ok(json!({"schema": SEATS_SCHEMA, "as_of": AT, "complete": true, "errors": [], "seats": s})
            .to_string()),
        SEATS_SCHEMA,
    )
}

fn kinds(n: &Next) -> Vec<(u8, String, Option<u64>)> {
    n.rows
        .iter()
        .map(|r| (r.layer, r.kind.clone(), r.pr))
        .collect()
}

fn s(x: &str) -> String {
    x.to_owned()
}

/// Layer 1 merge, then a fix (2), then a review (3); every row with a PR carries its head;
/// all read ⇒ complete, rc 0.
#[test]
fn merge_then_fix_then_review_and_all_read_is_complete() {
    let n = rank(
        &due(
            vec![due_item(1, "ebb36", &[], 30)],
            vec![fix_item(2, "043dd")],
            true,
        ),
        &sweep(vec![branch(3, "1ced7", false, 2)], true),
        &seats(&[("043dd", "yes", "slice"), ("b433b", "yes", "review")]),
        NOW,
        AT,
    );
    assert_eq!(
        kinds(&n),
        vec![
            (1, s("merge"), Some(1)),
            (2, s("fix"), Some(2)),
            (3, s("review"), Some(3))
        ],
        "{n:?}"
    );
    assert_eq!(n.rows[0].head.as_deref(), Some(h(1).as_str()));
    assert_eq!(n.rows[0].to, None, "the merge watch merges");
    assert_eq!(n.rows[0].waited_min, 30);
    assert_eq!(n.rows[1].to.as_deref(), Some("043dd"));
    assert_eq!(
        n.rows[2].to.as_deref(),
        Some("043dd"),
        "a free non-author reviewer with a big enough gap, by seat code"
    );
    assert!(n.complete, "{n:?}");
    assert_eq!(n.rc(), 0);
}

/// A merge item that only lacks a push / PR / label goes to its author (layer 1); one that
/// lacks a receipt goes to its author (layer 2); one that only waits (CI, a review) is no row.
#[test]
fn what_a_merge_item_lacks_picks_its_row() {
    let n = rank(
        &due(
            vec![
                due_item(1, "a9136", &["no_ready_merge"], 5),
                due_item(2, "a9136", &["receipt_behind", "ci_queued"], 5),
                due_item(3, "a9136", &["ci_queued"], 5),
                due_item(4, "a9136", &["no_review"], 5),
            ],
            vec![],
            true,
        ),
        &sweep(vec![], true),
        &seats(&[("a9136", "yes", "slice")]),
        NOW,
        AT,
    );
    assert_eq!(
        kinds(&n),
        vec![(1, s("push"), Some(1)), (2, s("receipt"), Some(2))],
        "{n:?}"
    );
}

/// aib-slice's scan not complete (rc 2): its rows go to `unknown`, the answer is
/// incomplete (rc 3); the other source's rows still show.
#[test]
fn an_incomplete_due_scan_puts_its_rows_in_unknown() {
    let n = rank(
        &due(vec![due_item(1, "ebb36", &[], 30)], vec![], false),
        &sweep(vec![branch(3, "1ced7", false, 2)], true),
        &seats(&[("b433b", "yes", "review")]),
        NOW,
        AT,
    );
    assert_eq!(kinds(&n), vec![(3, s("review"), Some(3))], "{n:?}");
    assert!(n.unknown.iter().any(|u| u.what.contains("#1")));
    let src = n.sources.iter().find(|x| x.name == "slice_due").unwrap();
    assert_eq!(src.state, "partial");
    assert!(!n.complete);
    assert_eq!(n.rc(), 3);
}

/// A source that is not there at all is `failed`, named in `unknown`, never "nothing".
#[test]
fn a_missing_source_is_failed_and_named() {
    let n = rank(
        &Err(s("no such file")),
        &sweep(vec![], true),
        &seats(&[]),
        NOW,
        AT,
    );
    assert!(n.rows.is_empty());
    assert!(n.unknown.iter().any(|u| u.what == "slice_due"), "{n:?}");
    assert_eq!(n.rc(), 3);
}

/// Seat facts not read: no review is given out (to `unknown`), nor an author's row.
#[test]
fn without_seat_facts_nobody_is_dispatched() {
    let n = rank(
        &due(vec![], vec![fix_item(2, "043dd")], true),
        &sweep(vec![branch(3, "1ced7", false, 2)], true),
        &Err(s("seats not run")),
        NOW,
        AT,
    );
    assert!(n.rows.is_empty(), "{n:?}");
    assert_eq!(n.unknown.len(), 2);
    assert!(!n.complete);
}

/// An author marked busy keeps the row, marked, after the rows that can be done now; an
/// author whose state is unknown goes to `unknown`.
#[test]
fn a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched() {
    let n = rank(
        &due(
            vec![],
            vec![
                fix_item(2, "043dd"),
                fix_item(3, "ebb36"),
                fix_item(4, "7b81e"),
            ],
            true,
        ),
        &sweep(vec![], true),
        &seats(&[
            ("043dd", "no", "none"),
            ("ebb36", "yes", "slice"),
            ("7b81e", "unknown", "none"),
        ]),
        NOW,
        AT,
    );
    assert_eq!(
        kinds(&n),
        vec![(2, s("fix"), Some(3)), (2, s("fix"), Some(2))],
        "{n:?}"
    );
    assert_eq!(n.rows[1].needs, "seat_busy");
    assert!(n.unknown.iter().any(|u| u.why.contains("7b81e")));
    assert!(!n.complete, "an unknown row makes the answer incomplete");
}

/// IFACE r3: a row needs a review-sized gap; a seat with a bigger one takes it, a seat with
/// none keeps the row marked; the reviewer is picked the same way.
#[test]
fn a_seat_takes_a_row_no_bigger_than_its_gap() {
    let n = rank(
        &due(
            vec![],
            vec![fix_item(2, "043dd"), fix_item(3, "ebb36")],
            true,
        ),
        &sweep(vec![branch(4, "a9136", false, 1)], true),
        &seats(&[
            ("043dd", "yes", "mainline"),
            ("ebb36", "yes", "none"),
            ("1ced7", "yes", "slice"),
        ]),
        NOW,
        AT,
    );
    let row = |pr: u64| n.rows.iter().find(|r| r.pr == Some(pr)).unwrap();
    assert_eq!(row(2).needs, "none", "{n:?}");
    assert_eq!(row(2).size, ROW_SIZE);
    assert_eq!(row(3).needs, "seat_size_none");
    assert_eq!(
        row(4).to.as_deref(),
        Some("043dd"),
        "043dd sorts before 1ced7; both fit"
    );
}

/// The reviewer is never the author, never busy, never a coordinator; with nobody free the
/// row says so instead of naming someone.
#[test]
fn the_reviewer_is_a_free_non_author_or_nobody() {
    let n = rank(
        &due(vec![], vec![], true),
        &sweep(vec![branch(3, "b433b", false, 2)], true),
        &seats(&[("b433b", "yes", "review"), ("043dd", "no", "none")]),
        NOW,
        AT,
    );
    assert_eq!(n.rows[0].to, None, "{n:?}");
    assert_eq!(n.rows[0].needs, "no_free_reviewer");
}

/// A seat whose own branch waits for a review is picked first (a swap); the PR more others
/// are stacked on goes first; then the one that waited longer.
#[test]
fn a_swap_reviewer_first_and_unblocks_then_waited() {
    let mut on3 = branch(9, "ebb36", true, 1);
    on3["stacked_on"] = json!(["feat/b-3-a9136-1115"]);
    let n = rank(
        &due(vec![], vec![], true),
        &sweep(
            vec![
                branch(2, "a9136", false, 9),
                branch(3, "a9136", false, 1),
                branch(5, "1ced7", false, 1),
                on3,
            ],
            true,
        ),
        &seats(&[("043dd", "yes", "review"), ("1ced7", "yes", "review")]),
        NOW,
        AT,
    );
    // 1ced7 has #5 waiting: it is picked for a9136's PRs over 043dd (a swap)
    let a9136: Vec<&Row> = n.rows.iter().filter(|r| r.pr != Some(5)).collect();
    assert!(
        a9136.iter().all(|r| r.to.as_deref() == Some("1ced7")),
        "{n:?}"
    );
    assert!(a9136[0].why_him.contains("交換審查"));
    let order: Vec<Option<u64>> = n.rows.iter().map(|r| r.pr).collect();
    assert_eq!(order, vec![Some(3), Some(2), Some(5)], "{n:?}");
    assert_eq!(n.rows[0].unblocks, 1);
}

/// An abandonable branch never becomes a row (keep or drop is a human's call); a merged
/// branch on a worktree is layer 4 for its seat.
#[test]
fn abandonable_is_never_a_row_and_merged_is_collected() {
    let mut gone = branch(6, "a9136", false, 30);
    gone["abandonable"] = json!(true);
    gone["pr"]["state"] = json!("closed");
    let mut merged = branch(7, "a9136", true, 5);
    merged["class"] = json!(5);
    merged["pr"] = json!({"number": 7, "state": "closed", "merged": true});
    merged["worktree"] = json!("/w/x");
    let n = rank(
        &due(vec![], vec![], true),
        &sweep(vec![gone, merged], true),
        &seats(&[("a9136", "yes", "review")]),
        NOW,
        AT,
    );
    assert_eq!(kinds(&n), vec![(4, s("collect"), Some(7))], "{n:?}");
}

/// The CLI: files in, `--seat` filter, `--json` reads back, a usage error is rc 2, a
/// wrong schema is a failed source.
#[test]
fn the_cli_reads_three_files_and_answers() {
    let files = |p: &str| -> Result<String, String> {
        Ok(match p {
            "due" => json!({"schema": DUE_SCHEMA, "complete": true, "errors": [],
                "merge": {"items": [due_item(1, "ebb36", &[], 3)]}, "fix": {"items": [fix_item(2, "043dd")]}})
            .to_string(),
            "sweep" => json!({"schema": SWEEP_SCHEMA, "complete": true, "branches": []}).to_string(),
            "seats" => json!({"schema": SEATS_SCHEMA, "complete": true,
                "seats": [{"seat": "043dd", "dispatchable": "yes", "size": "slice"}]})
            .to_string(),
            _ => json!({"schema": "other"}).to_string(),
        })
    };
    let args = |v: &[&str]| v.iter().map(|x| s(x)).collect::<Vec<_>>();
    let (out, rc) = cli(
        &args(&[
            "--due", "due", "--sweep", "sweep", "--seats", "seats", "--json", "--seat", "043dd",
        ]),
        files,
        NOW,
        AT,
    );
    assert_eq!(rc, 0, "{out}");
    let n: Next = serde_json::from_str(&out).unwrap();
    assert_eq!(kinds(&n), vec![(2, s("fix"), Some(2))]);
    assert_eq!(n.rows[0].rank, 1);
    let (text, rc) = cli(
        &args(&["--due", "due", "--sweep", "sweep", "--seats", "seats"]),
        files,
        NOW,
        AT,
    );
    assert_eq!(rc, 0);
    assert!(text.contains("#1 x 可合"), "{text}");
    assert_eq!(cli(&args(&["--due"]), files, NOW, AT).1, 2);
    assert_eq!(cli(&args(&["--bogus"]), files, NOW, AT).1, 2);
    let (text, rc) = cli(
        &args(&["--due", "nope", "--sweep", "sweep", "--seats", "seats"]),
        files,
        NOW,
        AT,
    );
    assert_eq!(rc, 3);
    assert!(text.contains("schema is not"), "{text}");
}
