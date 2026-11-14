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
    seats_read(list, true)
}

fn seats_read(list: &[(&str, &str, &str)], complete: bool) -> Input<Seats> {
    let s: Vec<Value> = list
        .iter()
        .map(|(seat, d, size)| {
            json!({"seat": seat, "dispatchable": d, "size": size, "role": "dev",
            "alive": {"v": "yes"}, "first": []})
        })
        .collect();
    read_input(
        Ok(json!({"schema": SEATS_SCHEMA, "as_of": AT, "complete": complete, "errors": [], "seats": s})
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
        vec![
            (1, s("push"), Some(1)),
            (2, s("receipt"), Some(2)),
            (3, s("review"), Some(4))
        ],
        "{n:?}"
    );
    // the only seat is the author: nobody reviews it
    assert_eq!(n.rows[2].needs, "no_free_reviewer");
}

/// RULES §18 (review before the push): a branch with no PR yet that lacks a review gets a
/// review row from due; when sweep names the same head (an open PR) there is one row, and
/// a lack that only waits (a new gate's turn) is still no row.
#[test]
fn a_branch_not_pushed_is_reviewed_once_per_head() {
    let n = rank(
        &due(
            vec![
                due_item(5, "a63ab", &["not_pushed", "no_pr", "no_review"], 9),
                due_item(6, "1ced7", &["no_pr", "no_review", "new_gate"], 9),
                due_item(7, "7b81e", &["not_pushed", "no_pr", "no_review"], 9),
            ],
            vec![],
            true,
        ),
        &sweep(vec![branch(5, "a63ab", false, 2)], true),
        &seats(&[("a63ab", "yes", "review"), ("b433b", "yes", "review")]),
        NOW,
        AT,
    );
    // #7 is only in due (no PR, so not in sweep's open PRs): it still gets its review,
    // from a63ab, whose own #5 waits (a swap, so first); #5 goes to b433b
    assert_eq!(
        kinds(&n),
        vec![(3, s("review"), Some(7)), (3, s("review"), Some(5))],
        "{n:?}"
    );
    assert_eq!(n.rows[0].to.as_deref(), Some("a63ab"), "{n:?}");
    assert_eq!(n.rows[1].to.as_deref(), Some("b433b"), "{n:?}");
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

/// Seat facts that are read but not complete (one seat lost, say) are used seat by seat: a
/// seat known free is dispatched to, a seat whose state is unknown never is; the seats
/// source is partial and the answer not complete (b433b M1).
#[test]
fn a_partial_seat_list_is_used_seat_by_seat() {
    let n = rank(
        &due(
            vec![],
            vec![fix_item(2, "043dd"), fix_item(4, "8aa49")],
            true,
        ),
        &sweep(vec![branch(3, "1ced7", false, 2)], true),
        &seats_read(
            &[
                ("043dd", "yes", "slice"),
                ("00aaa", "unknown", "review"),
                ("b433b", "yes", "review"),
                ("8aa49", "unknown", "none"),
            ],
            false,
        ),
        NOW,
        AT,
    );
    let fix = n.rows.iter().find(|r| r.pr == Some(2)).expect("fix row");
    assert_eq!(fix.to.as_deref(), Some("043dd"), "{n:?}");
    let review = n.rows.iter().find(|r| r.pr == Some(3)).expect("review row");
    // 00aaa sorts first and takes reviews, but its state is unknown
    assert_eq!(review.to.as_deref(), Some("043dd"), "{n:?}");
    assert!(n.unknown.iter().any(|u| u.why.contains("8aa49")), "{n:?}");
    assert_eq!(n.sources[2].state, "partial");
    assert!(!n.complete);
}

/// Reviews are spread: five PRs and three free seats go to more than one seat (b433b L1).
#[test]
fn reviews_are_spread_over_the_free_seats() {
    let n = rank(
        &due(vec![], vec![], true),
        &sweep(
            (1..=5).map(|p| branch(p, "a9136", false, 1)).collect(),
            true,
        ),
        &seats(&[
            ("043dd", "yes", "review"),
            ("1ced7", "yes", "review"),
            ("b433b", "yes", "review"),
        ]),
        NOW,
        AT,
    );
    let mut to: Vec<&str> = n.rows.iter().filter_map(|r| r.to.as_deref()).collect();
    assert_eq!(to.len(), 5, "{n:?}");
    to.sort_unstable();
    to.dedup();
    assert_eq!(to.len(), 3, "{n:?}");
}

/// A seat whose own branch waits for a review is picked first (a swap); reviews are spread
/// (a seat given one goes after a seat given none); the PR more others are stacked on goes
/// first; then the one that waited longer.
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
    // 1ced7 has #5 waiting: it is picked for a9136's first PR over 043dd (a swap); the next
    // goes to 043dd, which has none yet (spread)
    let to = |pr: u64| n.rows.iter().find(|r| r.pr == Some(pr)).unwrap();
    assert_eq!(to(2).to.as_deref(), Some("1ced7"), "{n:?}");
    assert!(to(2).why_him.contains("交換審查"));
    assert_eq!(to(3).to.as_deref(), Some("043dd"), "{n:?}");
    // the swap row first, then the PR one other is stacked on, then the rest
    let order: Vec<Option<u64>> = n.rows.iter().map(|r| r.pr).collect();
    assert_eq!(order, vec![Some(2), Some(3), Some(5)], "{n:?}");
    assert_eq!(to(3).unblocks, 1);
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
    // a collect row says how old its head is and is never starving (b433b L2)
    assert!(n.rows[0].why_now.contains("5 小時"), "{n:?}");
    assert!(!n.rows[0].starving);
    // C4 (519d0 11-15): listed beside for_andy for a human, with its PR and head
    assert_eq!(n.abandonable.len(), 1, "{n:?}");
    assert_eq!(n.abandonable[0].pr, Some(6));
    assert_eq!(n.abandonable[0].head, h(6));
    assert!(render(&n).contains("可放棄"));
    // a merged branch with no seat code is still collected, said to have no owner
    let mut nobody = branch(8, "x", true, 2);
    nobody["seat"] = Value::Null;
    nobody["class"] = json!(5);
    nobody["worktree"] = json!("/w/y");
    let n = rank(
        &due(vec![], vec![], true),
        &sweep(vec![nobody], true),
        &seats(&[("a9136", "yes", "review")]),
        NOW,
        AT,
    );
    assert_eq!(n.rows[0].to, None, "{n:?}");
    assert_eq!(n.rows[0].needs, "no_owner");
    assert!(n.rows[0].why_him.contains("沒有席碼"));
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
    // a seat code is five lowercase letters or digits (b433b N3)
    assert_eq!(cli(&args(&["--seat", "043DD"]), files, NOW, AT).1, 2);
    let (text, rc) = cli(
        &args(&["--due", "nope", "--sweep", "sweep", "--seats", "seats"]),
        files,
        NOW,
        AT,
    );
    assert_eq!(rc, 3);
    assert!(text.contains("schema is not"), "{text}");
}

/// DEV-S79 §2.1 防餓死: a row that waited over 4 hours is marked (and shown), not reweighed.
#[test]
fn a_row_waiting_over_four_hours_is_marked_starving() {
    let n = rank(
        &due(
            vec![
                due_item(1, "ebb36", &[], 241),
                due_item(2, "ebb36", &[], 240),
            ],
            vec![],
            true,
        ),
        &sweep(vec![], true),
        &seats(&[]),
        NOW,
        AT,
    );
    let row = |pr: u64| n.rows.iter().find(|r| r.pr == Some(pr)).unwrap();
    assert!(row(1).starving, "{n:?}");
    assert!(!row(2).starving, "exactly 4 hours is not yet");
    assert!(render(&n).contains("[等太久]"));
}

/// S79 integration: with `--plan` / `--ideas`, layers 5–6 (next_local) come after layers
/// 1–4; a `pr:` item is read as merged from sweep; layer 7 being unsupported does not make
/// the answer incomplete; `fleet_blocker` / `for_andy` are carried.
#[test]
fn the_plan_adds_layer_five_after_the_slice_layers() {
    let mut done = branch(747, "043dd", true, 1);
    done["pr"] = json!({"number": 747, "state": "closed", "merged": true});
    done["class"] = json!(5);
    let sweep_text = json!({"schema": SWEEP_SCHEMA, "generated_at": NOW, "complete": true,
        "errors": [], "branches": [done, branch(3, "1ced7", false, 2)]})
    .to_string();
    let files = move |p: &str| -> Result<String, String> {
        Ok(match p {
            "due" => json!({"schema": DUE_SCHEMA, "complete": true, "errors": [],
                "merge": {"items": [due_item(1, "ebb36", &[], 3)]}, "fix": {"items": []}})
            .to_string(),
            "sweep" => sweep_text.clone(),
            "seats" => json!({"schema": SEATS_SCHEMA, "complete": true, "seats": [
                {"seat": "043dd", "dispatchable": "yes", "size": "mainline", "role": "dev"},
                {"seat": "b433b", "dispatchable": "yes", "size": "review", "role": "dev"}]})
            .to_string(),
            "plan" => json!({"seats": {"ebb36": {}, "1ced7": {}, "043dd": {}, "b433b": {},
                "Andy": {}}, "nodes": [{"id": "N1", "status": "已同意未做", "owner": "043dd",
                "title": "t", "approved": {"at": "x", "by": "Andy", "pointer": "D §1"},
                "after": ["pr:747"]}]})
            .to_string(),
            "ideas" => json!({"complete": true, "ideas": []}).to_string(),
            _ => return Err(format!("{p}: none")),
        })
    };
    let args: Vec<String> = [
        "--due", "due", "--sweep", "sweep", "--seats", "seats", "--plan", "plan", "--ideas",
        "ideas", "--json",
    ]
    .map(str::to_owned)
    .to_vec();
    let (out, rc) = cli(&args, &files, NOW, AT);
    let n: Next = serde_json::from_str(&out).unwrap();
    assert_eq!(rc, 0, "{out}");
    let layers: Vec<u8> = n.rows.iter().map(|r| r.layer).collect();
    assert_eq!(layers, vec![1, 3, 5], "{out}");
    let five = &n.rows[2];
    assert_eq!(
        (five.rank, five.node.as_deref(), five.to.as_deref()),
        (3, Some("N1"), Some("043dd"))
    );
    assert!(five.scope_unchecked, "approved without scope");
    let state = |name: &str| {
        n.sources
            .iter()
            .find(|s| s.name == name)
            .map(|s| s.state.clone())
    };
    assert_eq!(state("ideas").as_deref(), Some("ok"));
    assert_eq!(state("tidy_scan").as_deref(), Some("unsupported"));
    assert_eq!(n.sources.iter().filter(|s| s.name == "seats").count(), 1);
    assert!(n.complete);
    // the plan not readable: layers 5–6 are unknown, never "nothing to do"
    let mut no_plan = args.clone();
    no_plan[7] = "nope".into();
    let (out, rc) = cli(&no_plan, &files, NOW, AT);
    assert_eq!(rc, 3, "{out}");
    assert!(out.contains("plan 讀不到"), "{out}");
}

/// A cache the reader calls stale (IFACE r5: aib-slice's output older than 30 minutes) is
/// the source `stale`; its rows go to `unknown`.
#[test]
fn a_stale_cache_is_a_stale_source() {
    let files = |p: &str| -> Result<String, String> {
        match p {
            "due" => Err("stale: due 已 31 分鐘沒更新".into()),
            "sweep" => {
                Ok(json!({"schema": SWEEP_SCHEMA, "complete": true, "branches": []}).to_string())
            }
            _ => Ok(json!({"schema": SEATS_SCHEMA, "complete": true, "seats": []}).to_string()),
        }
    };
    let args: Vec<String> = [
        "--due", "due", "--sweep", "sweep", "--seats", "seats", "--json",
    ]
    .map(str::to_owned)
    .to_vec();
    let (out, rc) = cli(&args, files, NOW, AT);
    let n: Next = serde_json::from_str(&out).unwrap();
    assert_eq!(rc, 3);
    assert_eq!(n.sources[0].state, "stale", "{out}");
}

/// IFACE r6: a seat whose `capacity.unread` is null is never `to`; when no other seat can
/// take the row it is unknown ("unread unknown"), not "no free reviewer".
#[test]
fn a_seat_whose_unread_is_not_known_is_never_sent_to() {
    let seats_json = |unread: Value| {
        read_input::<Seats>(
            Ok(json!({"schema": SEATS_SCHEMA, "complete": true, "seats": [
                {"seat": "043dd", "dispatchable": "yes", "size": "review", "role": "dev",
                 "capacity": {"unread": unread}}]})
            .to_string()),
            SEATS_SCHEMA,
        )
    };
    let n = rank(
        &due(vec![], vec![fix_item(2, "043dd")], true),
        &sweep(vec![branch(3, "1ced7", false, 2)], true),
        &seats_json(Value::Null),
        NOW,
        AT,
    );
    assert!(n.rows.is_empty(), "{n:?}");
    // held back by a rule, not unread: skipped, and the answer stays complete (IFACE r9)
    assert!(n.unknown.is_empty(), "{n:?}");
    assert_eq!(n.skipped.len(), 2, "{n:?}");
    assert!(n
        .skipped
        .iter()
        .all(|k| k.reason.contains("unread unknown")));
    assert!(n.complete, "{n:?}");
    let n = rank(
        &due(vec![], vec![fix_item(2, "043dd")], true),
        &sweep(vec![branch(3, "1ced7", false, 2)], true),
        &seats_json(json!(0)),
        NOW,
        AT,
    );
    assert_eq!(n.rows.len(), 2, "{n:?}");
}

/// approved.scope is the lowercase sha256 hex of the scope text (IFACE r4 §5).
#[test]
fn the_scope_hash_is_sha256_hex() {
    assert_eq!(
        sha256_hex("abc"),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
}

/// A `pr:` item aib-slice's cache does not list (`--fast` lists no PR closed over 72 hours
/// ago) is asked of GitHub, once per PR; a call that fails is not known, never "not merged".
#[test]
fn a_pr_sweep_does_not_list_is_asked_of_github_once() {
    use aib_drive::next_local::{Dep, MergedSource};
    struct Gh(std::cell::RefCell<Vec<String>>);
    impl aib_drive::snapshot::Read for Gh {
        fn get(&self, path: &str) -> Result<Value, String> {
            self.0.borrow_mut().push(path.to_owned());
            match path {
                "repos/o/r/pulls/747" => Ok(json!({"merged": true})),
                _ => Err("down".into()),
            }
        }
    }
    let gh = Gh(std::cell::RefCell::new(Vec::new()));
    let more = GhMerged::new(&gh, "o/r");
    let empty: Sweep =
        serde_json::from_value(json!({"schema": SWEEP_SCHEMA, "complete": true, "branches": []}))
            .unwrap();
    let m = SweepMerged {
        sweep: Some(&empty),
        more: Some(&more),
    };
    assert_eq!(m.merged(&Dep::Pr(747)), Some(true));
    assert_eq!(m.merged(&Dep::Pr(747)), Some(true));
    assert_eq!(m.merged(&Dep::Pr(9)), None);
    assert_eq!(m.merged(&Dep::SkillsPr(747)), None);
    assert_eq!(gh.0.borrow().len(), 2, "747 asked once, 9 once");
}

/// Reviews are one per head only when there is a head: two rows without one are two rows
/// (b433b inc N2).
#[test]
fn rows_without_a_head_are_not_merged_into_one() {
    let mut a = branch(3, "1ced7", false, 2);
    a["head"] = json!("");
    let mut b = branch(4, "1ced7", false, 2);
    b["head"] = json!("");
    let n = rank(
        &due(vec![], vec![], true),
        &sweep(vec![a, b], true),
        &seats(&[("043dd", "yes", "review")]),
        NOW,
        AT,
    );
    assert_eq!(n.rows.len(), 2, "{n:?}");
}

/// The plan of one node `N1` owned by 043dd, after `pr:747`, through the CLI with the
/// given seats and sweep text; the layer-5 rows that come out.
fn layer_five(seats_text: String, sweep_text: String) -> (Next, i32) {
    let files = move |p: &str| -> Result<String, String> {
        Ok(match p {
            "due" => json!({"schema": DUE_SCHEMA, "complete": true, "errors": [],
                "merge": {"items": []}, "fix": {"items": []}})
            .to_string(),
            "sweep" => sweep_text.clone(),
            "seats" => seats_text.clone(),
            "plan" => json!({"seats": {"043dd": {}}, "nodes": [{"id": "N1",
                "status": "已同意未做", "owner": "043dd",
                "title": "t", "approved": {"at": "x", "by": "Andy", "pointer": "D §1"},
                "after": ["pr:747"]}]})
            .to_string(),
            "ideas" => json!({"complete": true, "ideas": []}).to_string(),
            _ => return Err(format!("{p}: none")),
        })
    };
    let args: Vec<String> = [
        "--due", "due", "--sweep", "sweep", "--seats", "seats", "--plan", "plan", "--ideas",
        "ideas", "--json",
    ]
    .map(str::to_owned)
    .to_vec();
    let (out, rc) = cli(&args, files, NOW, AT);
    (serde_json::from_str(&out).unwrap(), rc)
}

fn merged_747(complete: bool) -> String {
    let mut done = branch(747, "043dd", true, 1);
    done["pr"] = json!({"number": 747, "state": "closed", "merged": true});
    done["class"] = json!(5);
    json!({"schema": SWEEP_SCHEMA, "generated_at": NOW, "complete": complete,
        "errors": [], "branches": [done]})
    .to_string()
}

fn sent_five(n: &Next) -> Vec<Option<String>> {
    n.rows
        .iter()
        .filter(|r| r.layer == 5)
        .map(|r| r.to.clone())
        .collect()
}

/// Layers 5–6 use the seat facts layers 1–4 checked (Codex M1, M2): a seat whose unread is
/// not known is not sent a node (IFACE r6), and seats of the wrong schema send nobody.
#[test]
fn layer_five_takes_the_checked_seat_facts() {
    let seat = |unread: Value| {
        json!({"schema": SEATS_SCHEMA, "complete": true, "seats": [
            {"seat": "043dd", "dispatchable": "yes", "size": "mainline", "role": "dev",
             "capacity": {"unread": unread}}]})
        .to_string()
    };
    let (n, rc) = layer_five(seat(json!(0)), merged_747(true));
    assert_eq!(sent_five(&n), vec![Some(s("043dd"))], "{n:?}");
    assert_eq!(rc, 0, "{n:?}");
    // held back by a rule: skipped, and the answer is still whole (IFACE r9)
    let (n, rc) = layer_five(seat(Value::Null), merged_747(true));
    assert!(sent_five(&n).is_empty(), "{n:?}");
    assert!(n.skipped.iter().any(|k| k.what == "N1"), "{n:?}");
    assert!(n.unknown.is_empty(), "{n:?}");
    assert_eq!(rc, 0, "{n:?}");
    let wrong = json!({"schema": "other.v1", "complete": true, "seats": [
        {"seat": "043dd", "dispatchable": "yes", "size": "mainline", "role": "dev"}]})
    .to_string();
    let (n, rc) = layer_five(wrong, merged_747(true));
    assert!(sent_five(&n).is_empty(), "{n:?}");
    assert_eq!(rc, 3);
}

/// A sweep that is not complete says nothing about merged (Codex M3): the node after a PR
/// it lists as merged stays out of the rows.
#[test]
fn an_incomplete_sweep_is_not_read_for_merged() {
    let seats = json!({"schema": SEATS_SCHEMA, "complete": true, "seats": [
        {"seat": "043dd", "dispatchable": "yes", "size": "mainline", "role": "dev"}]})
    .to_string();
    let (n, _) = layer_five(seats.clone(), merged_747(true));
    assert_eq!(sent_five(&n).len(), 1, "{n:?}");
    let (n, rc) = layer_five(seats, merged_747(false));
    assert!(sent_five(&n).is_empty(), "{n:?}");
    assert_eq!(rc, 3);
}

/// IFACE r8: with a full scan given, layer 4 (collect, abandonable) comes from it alone; the
/// fast sweep gives layer 3 only. A full scan that is stale gives no layer-4 row and its
/// source says `stale`.
#[test]
fn layer_four_comes_from_the_full_scan_alone() {
    let mut merged = branch(7, "a9136", true, 5);
    merged["class"] = json!(5);
    merged["pr"] = json!({"number": 7, "state": "closed", "merged": true});
    merged["worktree"] = json!("/w/x");
    let mut gone = branch(6, "a9136", false, 30);
    gone["abandonable"] = json!(true);
    gone["pr"]["state"] = json!("closed");
    let fast = sweep(
        vec![merged.clone(), gone, branch(3, "1ced7", false, 2)],
        true,
    );
    // a9136 owns the merged tree, so its collect row has a seat to go to
    let s = seats(&[("043dd", "yes", "review"), ("a9136", "yes", "review")]);
    let d = due(vec![], vec![], true);
    // the full scan has no merged tree and nothing abandonable
    let none = sweep(vec![], true);
    let n = rank_with(&d, &fast, Some(&none), &s, NOW, AT);
    assert_eq!(kinds(&n), vec![(3, "review".to_owned(), Some(3))], "{n:?}");
    assert!(n.abandonable.is_empty(), "{n:?}");
    let full = sweep(vec![merged], true);
    let n = rank_with(&d, &fast, Some(&full), &s, NOW, AT);
    assert_eq!(
        kinds(&n),
        vec![
            (3, "review".to_owned(), Some(3)),
            (4, "collect".to_owned(), Some(7))
        ],
        "{n:?}"
    );
    let stale: Input<Sweep> = Err("stale: full 已 25 小時沒更新".into());
    let n = rank_with(&d, &fast, Some(&stale), &s, NOW, AT);
    assert!(n.rows.iter().all(|r| r.layer != 4), "{n:?}");
    let src = n.sources.iter().find(|x| x.name == "slice_full").unwrap();
    assert_eq!(src.state, "stale");
    assert!(!n.complete);
}

/// due and sweep from two different aib-slice rounds (one write failed): the pair is
/// `slice: partial`, rc 3 (1ced7 L5, IFACE r9 4); the same round is fine.
#[test]
fn due_and_sweep_of_two_rounds_are_partial() {
    let pair = |due_at: u64| {
        move |p: &str| -> Result<String, String> {
            Ok(match p {
                "due" => json!({"schema": DUE_SCHEMA, "generated_at": due_at, "complete": true,
                    "merge": {"items": []}, "fix": {"items": []}})
                .to_string(),
                "sweep" => json!({"schema": SWEEP_SCHEMA, "generated_at": NOW, "complete": true,
                    "branches": []})
                .to_string(),
                _ => json!({"schema": SEATS_SCHEMA, "complete": true, "seats": []}).to_string(),
            })
        }
    };
    let args: Vec<String> = [
        "--due", "due", "--sweep", "sweep", "--seats", "seats", "--json",
    ]
    .map(str::to_owned)
    .to_vec();
    let (out, rc) = cli(&args, pair(NOW), NOW, AT);
    assert_eq!(rc, 0, "{out}");
    let (out, rc) = cli(&args, pair(NOW - 600), NOW, AT);
    assert_eq!(rc, 3, "{out}");
    let n: Next = serde_json::from_str(&out).unwrap();
    let src = n.sources.iter().find(|x| x.name == "slice").unwrap();
    assert_eq!(src.state, "partial", "{out}");
}

/// The CLI with a plan whose roster is `roster` (or no `seats` at all when `None`), the
/// given seat facts, a fix of 2240e and an open PR of 1ced7 to review.
fn with_roster(roster: Option<Value>, facts: Value) -> (Next, i32) {
    let files = move |p: &str| -> Result<String, String> {
        Ok(match p {
            "due" => json!({"schema": DUE_SCHEMA, "complete": true,
                "merge": {"items": []}, "fix": {"items": [fix_item(2, "2240e")]}})
            .to_string(),
            "sweep" => json!({"schema": SWEEP_SCHEMA, "complete": true,
                "branches": [branch(3, "1ced7", false, 2)]})
            .to_string(),
            "seats" => json!({"schema": SEATS_SCHEMA, "complete": true, "seats": facts.clone()})
                .to_string(),
            "plan" => {
                let mut p = json!({"nodes": []});
                if let Some(r) = roster.clone() {
                    p["seats"] = r;
                }
                p.to_string()
            }
            "ideas" => json!({"complete": true, "ideas": []}).to_string(),
            _ => return Err(format!("{p}: none")),
        })
    };
    let args: Vec<String> = [
        "--due", "due", "--sweep", "sweep", "--seats", "seats", "--plan", "plan", "--ideas",
        "ideas", "--json",
    ]
    .map(str::to_owned)
    .to_vec();
    let (out, rc) = cli(&args, files, NOW, AT);
    (serde_json::from_str(&out).unwrap(), rc)
}

/// 519d0 T1, T2: `to` comes only from the plan's roster (a free seat of another project is
/// never picked), and a roster seat with no seat facts is lost — its row stays, sent to
/// the coordinator with needs `owner_lost`, not `unknown`.
#[test]
fn only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator() {
    let facts = json!([
        {"seat": "043dd", "dispatchable": "no", "size": "none", "role": "dev"},
        {"seat": "519d0", "dispatchable": "no", "size": "none", "role": "coordinator"},
        {"seat": "78b13", "dispatchable": "yes", "size": "review", "role": "dev"}]);
    let roster = json!({"043dd": {}, "519d0": {}, "2240e": {}, "1ced7": {}, "Andy": {},
        "Codex": {}});
    let (n, _) = with_roster(Some(roster), facts.clone());
    assert!(
        n.rows.iter().all(|r| r.to.as_deref() != Some("78b13")),
        "{n:?}"
    );
    let fix = n
        .rows
        .iter()
        .find(|r| r.kind == "fix")
        .expect("the fix row stays");
    assert_eq!(fix.to.as_deref(), Some("519d0"), "{n:?}");
    assert_eq!(fix.needs, "owner_lost");
    assert!(fix.why_him.contains("2240e 失聯"), "{n:?}");
    assert!(n.unknown.iter().all(|u| !u.why.contains("2240e")), "{n:?}");
    // without a roster check (no plan) 78b13 would be picked: the roster is what stops it
    let src = n.sources.iter().find(|s| s.name == "roster").unwrap();
    assert_eq!(src.state, "ok");
    // an author off the roster (an old seat) is handed to the coordinator too, not unknown
    let facts_old = facts.clone();
    let roster_old = json!({"043dd": {}, "519d0": {}, "1ced7": {}});
    let (n, _) = with_roster(Some(roster_old), facts_old);
    let fix = n
        .rows
        .iter()
        .find(|r| r.kind == "fix")
        .expect("the fix row stays");
    assert_eq!(
        (fix.to.as_deref(), fix.needs.as_str()),
        (Some("519d0"), "owner_off_roster"),
        "{n:?}"
    );
    assert!(fix.why_him.contains("2240e 不在名冊"), "{n:?}");
    assert!(n.unknown.is_empty(), "{n:?}");
}

/// 519d0 T1: a plan with no roster ⇒ nobody can be picked: no review row, the roster
/// source failed, rc 3.
#[test]
fn no_roster_means_no_review_row() {
    let facts = json!([{"seat": "043dd", "dispatchable": "yes", "size": "review", "role": "dev"}]);
    let (n, rc) = with_roster(None, facts);
    assert!(n.rows.iter().all(|r| r.layer != 3), "{n:?}");
    assert!(n.unknown.iter().any(|u| u.why.contains("名冊")), "{n:?}");
    let src = n.sources.iter().find(|s| s.name == "roster").unwrap();
    assert_eq!(src.state, "failed");
    assert_eq!(rc, 3);
}

/// 519d0 T3: the text lists one line for what a rule held back, its count and reasons;
/// `--all` lists each one. JSON is not changed.
#[test]
fn the_text_counts_what_was_held_back_unless_all() {
    let mut n = rank(
        &due(vec![], vec![], true),
        &sweep(vec![], true),
        &seats(&[]),
        NOW,
        AT,
    );
    for (w, r) in [
        ("i_1", "沒掛節點,不派"),
        ("i_2", "沒掛節點,不派"),
        ("N9", "N9 核准後範圍被改"),
    ] {
        n.skipped.push(Skipped {
            what: w.into(),
            reason: r.into(),
        });
    }
    let short = render(&n);
    assert!(
        short.contains("不排 3 條(沒掛節點,不派 2、N9 核准後範圍被改 1)"),
        "{short}"
    );
    assert!(!short.contains("不排 i_1"), "{short}");
    let all = render_with(&n, true);
    assert!(all.contains("不排 i_1:沒掛節點,不派"), "{all}");
    assert!(all.contains("不排 N9:"), "{all}");
}

/// 519d0 D1 + C5 (IFACE r9 3): the macOS queue group's `unblock_by` names the open PR first
/// in the queue and its author (from sweep); a node act's intents ledger holds is not
/// handed out again, and a broken line makes the source partial.
#[test]
fn the_queue_front_and_the_intents_ledger_reach_layer_five() {
    // a PR closed without merging is not in the queue, even with the lowest number
    let mut closed = branch(2, "a9136", true, 2);
    closed["pr"] = json!({"number": 2, "state": "closed", "merged": false});
    closed["class"] = json!(5);
    let sweep_text = json!({"schema": SWEEP_SCHEMA, "generated_at": NOW, "complete": true,
        "errors": [], "branches": [branch(9, "1ced7", true, 2), branch(4, "043dd", true, 2), closed]})
    .to_string();
    let now_ms = (NOW as i64) * 1000;
    let claim = |node: &str, ago_min: i64| {
        json!({"kind": "intent", "key": format!("dispatch:{node}:-:043dd"), "action": "dispatch",
               "node": node, "at_ms": now_ms - ago_min * 60_000})
        .to_string()
    };
    let intents_text = format!("{}\n{}\n", claim("N1", 30), claim("N2", 25 * 60));
    let files = move |p: &str| -> Result<String, String> {
        Ok(match p {
            "due" => json!({"schema": DUE_SCHEMA, "complete": true, "errors": [],
                "merge": {"items": []}, "fix": {"items": []}})
            .to_string(),
            "sweep" => sweep_text.clone(),
            "seats" => json!({"schema": SEATS_SCHEMA, "complete": true, "seats": [
                {"seat": "043dd", "dispatchable": "yes", "size": "mainline", "role": "dev"},
                {"seat": "1ced7", "dispatchable": "yes", "size": "mainline", "role": "dev"}]})
            .to_string(),
            "plan" => {
                let n = |id: &str, status: &str, owner: &str| {
                    json!({"id": id, "status": status,
                    "owner": owner, "title": "t", "approved": {"at": "x", "by": "Andy",
                    "pointer": "D §1"}, "after": []})
                };
                json!({"seats": {"043dd": {}, "1ced7": {}}, "nodes": [n("N1", "已同意未做", "043dd"),
                    n("N2", "已同意未做", "1ced7"), n("Q1", "審過排隊", "043dd"),
                    n("Q2", "審過排隊", "043dd")]})
                .to_string()
            }
            "ideas" => json!({"complete": true, "ideas": []}).to_string(),
            "intents" => intents_text.clone(),
            "broken" => "{\"kind\":\"intent\"\nnot json\n".to_owned(),
            _ => return Err(format!("{p}: none")),
        })
    };
    let base = [
        "--due", "due", "--sweep", "sweep", "--seats", "seats", "--plan", "plan", "--ideas",
        "ideas", "--json",
    ];
    let with = |extra: &[&str]| -> (Next, i32) {
        let mut a: Vec<String> = base.map(str::to_owned).to_vec();
        a.extend(extra.iter().map(|s| s.to_string()));
        let (out, rc) = cli(&a, &files, NOW, AT);
        (serde_json::from_str(&out).unwrap(), rc)
    };
    let nodes =
        |n: &Next| -> Vec<String> { n.rows.iter().filter_map(|r| r.node.clone()).collect() };
    // no ledger: both nodes out; the queue group says #4 by 043dd
    let (n, rc) = with(&[]);
    assert_eq!(rc, 0, "{n:?}");
    assert_eq!(nodes(&n), vec!["N1", "N2"]);
    let fb = n.fleet_blocker.clone().expect("a group");
    assert_eq!(fb["cause"], "macos_queue");
    assert_eq!(fb["unblock_by"], "#4 043dd");
    // N1 claimed 30 minutes ago; N2's intent is 25 hours old and no longer holds it
    let (n, rc) = with(&["--intents", "intents"]);
    assert_eq!(rc, 0, "{n:?}");
    assert_eq!(nodes(&n), vec!["N2"]);
    // a ledger with lines it cannot read: partial, not complete
    let (n, rc) = with(&["--intents", "broken"]);
    assert_eq!(rc, 3, "{n:?}");
    assert!(n
        .sources
        .iter()
        .any(|s| s.name == "intents" && s.state == "partial"));
    // a ledger given but not readable: failed
    let (n, rc) = with(&["--intents", "nope"]);
    assert_eq!(rc, 3, "{n:?}");
    assert!(n
        .sources
        .iter()
        .any(|s| s.name == "intents" && s.state == "failed"));
}
