//! S79 n-2c: layers 5–6, fleet_blocker and for_andy of `aib-drive next`.
//! The module is compiled here by path until the integration adds it to lib.rs
//! (IFACE-s79-drive-519d0-1115 §0: each slice adds only its own files).

use aib_drive::next_local;

use next_local::*;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

struct Merged(BTreeMap<String, Option<bool>>);

impl MergedSource for Merged {
    fn merged(&self, dep: &Dep) -> Option<bool> {
        self.0.get(&dep.text()).copied().flatten()
    }
}

fn merged(items: &[(&str, Option<bool>)]) -> Merged {
    Merged(items.iter().map(|(k, v)| (k.to_string(), *v)).collect())
}

fn seat(code: &str, dispatchable: &str, size: &str) -> SeatFact {
    SeatFact {
        seat: code.into(),
        dispatchable: dispatchable.into(),
        size: size.into(),
        role: "dev".into(),
    }
}

fn approved() -> Value {
    json!({"at": "11-14 12:00", "by": "Andy", "pointer": "DECISION-x.md §1"})
}

fn node(id: &str, status: &str, owner: &str, approved_: bool, after: &[&str]) -> Value {
    json!({"id": id, "status": status, "owner": owner, "title": format!("title {id}"),
           "approved": if approved_ { approved() } else { Value::Null }, "after": after})
}

fn fake_hash(text: &str) -> String {
    format!("h:{text}")
}

fn run(
    plan: &Value,
    ideas: Option<&Value>,
    seats: Option<&[SeatFact]>,
    claimed: &[&str],
    m: &Merged,
) -> LocalOut {
    run_hash(plan, ideas, seats, claimed, m, Some(&fake_hash))
}

fn run_hash(
    plan: &Value,
    ideas: Option<&Value>,
    seats: Option<&[SeatFact]>,
    claimed: &[&str],
    m: &Merged,
    scope_hash: Option<ScopeHash<'_>>,
) -> LocalOut {
    let claimed: BTreeSet<String> = claimed.iter().map(|s| s.to_string()).collect();
    let inp = LocalInput {
        plan,
        ideas,
        seats,
        claimed: &claimed,
        queue_unblock: Some("S96 n08(#764)"),
        scope_hash,
        roster_applied: false,
        tidy: None,
        tidy_age_s: None,
        as_of: "T",
    };
    rank_local(&inp, m)
}

/// [`run`] with seats holding the plan's roster only (519d0 T1).
fn run_on_roster(plan: &Value, ideas: &Value, seats: &[SeatFact]) -> LocalOut {
    let claimed = BTreeSet::new();
    let inp = LocalInput {
        plan,
        ideas: Some(ideas),
        seats: Some(seats),
        claimed: &claimed,
        queue_unblock: None,
        scope_hash: Some(&fake_hash),
        roster_applied: true,
        tidy: None,
        tidy_age_s: None,
        as_of: "T",
    };
    rank_local(&inp, &merged(&[]))
}

/// A node approved with a scope; `scope_of` is the title / after the scope was taken of.
fn scoped(id: &str, owner: &str, after: &[&str], scope_of: (&str, &[&str])) -> Value {
    let mut n = node(id, "已同意未做", owner, true, after);
    n["approved"]["scope"] = json!(fake_hash(&scope_text(scope_of.0, scope_of.1)));
    n
}

fn ids(out: &LocalOut) -> Vec<String> {
    out.rows
        .iter()
        .map(|r| r.node.clone().unwrap_or_else(|| r.what.clone()))
        .collect()
}

fn no_ideas() -> Value {
    json!({"complete": true, "ideas": []})
}

#[test]
fn an_approved_node_with_every_after_merged_goes_to_its_free_owner() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "043dd", true, &["pr:747", "node:N0"]),
                                node("N0", "實查通過", "x", false, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(
        &plan,
        Some(&no_ideas()),
        Some(&s),
        &[],
        &merged(&[("pr:747", Some(true))]),
    );
    assert_eq!(ids(&out), vec!["N1"]);
    let r = &out.rows[0];
    assert_eq!(
        (r.layer, r.to.as_str(), r.rank, r.kind.as_str()),
        (5, "043dd", 1, "mainline")
    );
    assert!(
        r.why_now.contains("pr:747") && r.why_now.contains("node:N0"),
        "{}",
        r.why_now
    );
}

#[test]
fn a_node_without_approved_is_never_mainline_next() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "043dd", false, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(out.rows.is_empty());
}

#[test]
fn an_after_item_not_merged_holds_the_node() {
    let plan = json!({"nodes": [node("N1", "已定未做", "043dd", true, &["pr:747"])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(
        &plan,
        Some(&no_ideas()),
        Some(&s),
        &[],
        &merged(&[("pr:747", Some(false))]),
    );
    assert!(out.rows.is_empty() && out.unknown.is_empty());
}

#[test]
fn an_after_node_not_done_holds_the_node() {
    let plan = json!({"nodes": [node("N1", "已定未做", "043dd", true, &["node:N0"]),
                                node("N0", "開發中", "x", false, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(out.rows.is_empty());
}

#[test]
fn a_merge_state_that_cannot_be_read_is_unknown_not_a_row() {
    let plan = json!({"nodes": [node("N1", "已定未做", "043dd", true, &["skills-pr:14"])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(
        &plan,
        Some(&no_ideas()),
        Some(&s),
        &[],
        &merged(&[("skills-pr:14", None)]),
    );
    assert!(out.rows.is_empty());
    assert_eq!(out.unknown.len(), 1);
    assert!(
        out.unknown[0].why.contains("skills-pr:14"),
        "{:?}",
        out.unknown
    );
}

#[test]
fn an_after_item_with_an_unknown_prefix_is_unknown() {
    let plan = json!({"nodes": [node("N1", "已定未做", "043dd", true, &["issue:3"])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(out.rows.is_empty());
    // an after word not known is the plan's to fix: skipped, not unread (IFACE r9)
    assert!(out.unknown.is_empty(), "{:?}", out.unknown);
    assert!(out.skipped[0].reason.contains("issue:3"));
}

#[test]
fn a_node_already_moving_or_claimed_is_not_handed_out() {
    let plan = json!({"nodes": [node("N1", "開發中", "043dd", true, &[]),
                                node("N2", "已同意未做", "043dd", true, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &["N2"], &merged(&[]));
    assert!(out.rows.is_empty());
}

#[test]
fn a_busy_or_coordinator_owner_gets_nothing_and_the_next_owner_does() {
    // the coordinator is listed but never picked; one other owner ⇒ that one
    let plan = json!({"nodes": [node("N1", "已同意未做", "519d0 / 1ced7", true, &[]),
                                node("N2", "已同意未做", "519d0 / 043dd(r-0)/ 1ced7", true, &[])]});
    let mut coord = seat("519d0", "yes", "mainline");
    coord.role = "coordinator".into();
    let s = [
        coord,
        seat("043dd", "no", "none"),
        seat("1ced7", "yes", "slice"),
    ];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert_eq!(ids(&out), vec!["N1"], "{:?}", out.skipped);
    assert_eq!(out.rows[0].to, "1ced7");
    // two owners besides the coordinator: the coordinator splits it (519d0 T5)
    assert!(out
        .skipped
        .iter()
        .any(|k| k.what == "N2" && k.reason.contains("owner 不只一席")));
}

/// A roster seat with no seat facts is lost (519d0 T2): a node it owns is held back as
/// skipped, not unknown.
#[test]
fn a_node_whose_owner_is_lost_is_skipped_not_unknown() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "2240e", true, &[])]});
    let s = [seat("2240e", LOST, "none")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(
        out.rows.is_empty() && out.unknown.is_empty(),
        "{:?}",
        out.unknown
    );
    assert!(out
        .skipped
        .iter()
        .any(|k| k.what == "N1" && k.reason.contains("失聯")));
}

#[test]
fn a_review_sized_seat_gets_no_mainline() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "043dd", true, &[])]});
    let s = [seat("043dd", "yes", "review")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(out.rows.is_empty() && out.unknown.is_empty());
}

#[test]
fn an_owner_without_a_seat_code_or_unknown_seats_is_unknown() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "Andy", true, &[]),
                                node("N2", "已同意未做", "043dd", true, &[])]});
    let out = run(&plan, Some(&no_ideas()), None, &[], &merged(&[]));
    assert!(out.rows.is_empty());
    // no seat code in the owner is a rule (skipped); no seat facts is unread (IFACE r9)
    let whats: Vec<&str> = out.unknown.iter().map(|u| u.what.as_str()).collect();
    assert_eq!(whats, vec!["N2"]);
    let held: Vec<&str> = out.skipped.iter().map(|k| k.what.as_str()).collect();
    assert_eq!(held, vec!["N1"]);
    let s = [seat("043dd", "unknown", "none")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(out.unknown.iter().any(|u| u.what == "N2"));
}

#[test]
fn more_unblocked_comes_first_in_layer_5() {
    let plan = json!({"nodes": [node("A", "已同意未做", "043dd", true, &[]),
                                node("B", "已同意未做", "043dd", true, &[]),
                                node("C1", "未開始", "x", false, &["node:B"]),
                                node("C2", "未開始", "x", false, &["node:C1"])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert_eq!(ids(&out), vec!["B", "A"]);
    assert_eq!(out.rows[0].unblocks, 2);
}

fn idea(short: &str, seat: &str, grade: &str, class: &str, at: u64) -> Value {
    json!({"short": short, "seat": seat, "grade": grade, "classification": class,
           "status": if class == "auto" { "ok" } else { "needs_human" },
           "human_review": if class == "auto" { "ok" } else { "pending" },
           "title": format!("t {short}"), "node": null, "at_ms": at})
}

fn idea_on(short: &str, seat: &str, grade: &str, node_: &str, at: u64) -> Value {
    let mut i = idea(short, seat, grade, "auto", at);
    i["node"] = json!(node_);
    i
}

fn whole(ideas: Vec<Value>) -> Value {
    json!({"complete": true, "ideas": ideas})
}

#[test]
fn ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one() {
    let mut i4 = idea("i_4", "1ced7", "S", "needs_human", 4);
    i4["node"] = json!("I4");
    // a needs-human idea Andy said yes to is still not layer 6 (it is not auto)
    let mut i6 = idea("i_6", "2240e", "S", "needs_human", 6);
    i6["node"] = json!("I1");
    i6["status"] = json!("ok");
    i6["human_review"] = json!("ok");
    let ideas = whole(vec![
        i6,
        idea_on("i_1", "043dd", "B", "I1", 1),
        idea_on("i_2", "043dd", "S", "I2", 2),
        idea_on("i_3", "1ced7", "A", "I3", 3),
        i4,
        idea_on("i_5", "043dd", "S", "I5", 1),
    ]);
    let plan = json!({"nodes": [node("I1", "未開始", "x", false, &[]), node("I2", "已定未做", "x", false, &[]),
                                node("I3", "已同意未做", "x", false, &[]), node("I4", "未開始", "x", false, &[]),
                                node("I5", "未開始", "x", false, &[])]});
    let s = [
        seat("043dd", "yes", "slice"),
        seat("1ced7", "yes", "mainline"),
        seat("2240e", "yes", "slice"),
    ];
    let out = run(&plan, Some(&ideas), Some(&s), &[], &merged(&[]));
    let whats: Vec<&str> = out
        .rows
        .iter()
        .map(|r| r.what.split(' ').next().unwrap_or(""))
        .collect();
    assert_eq!(
        whats,
        vec!["i_5", "i_3"],
        "S oldest for 043dd, then A for 1ced7"
    );
    assert!(out.rows.iter().all(|r| r.layer == 6 && r.size == "slice"));
    assert_eq!(out.rows[0].node.as_deref(), Some("I5"));
    assert_eq!(out.sources.get("ideas").map(String::as_str), Some("ok"));
}

#[test]
fn an_auto_idea_andy_said_no_to_is_never_dispatched() {
    let plan = json!({"nodes": [node("I1", "未開始", "x", false, &[]), node("I2", "未開始", "x", false, &[])]});
    let mut no1 = idea_on("i_1", "043dd", "S", "I1", 1);
    no1["status"] = json!("no");
    no1["human_review"] = json!("no");
    let mut no2 = idea_on("i_2", "1ced7", "S", "I2", 2);
    no2["human_review"] = json!("no");
    let mut nh = idea_on("i_3", "2240e", "S", "I2", 3);
    nh["status"] = json!("needs_human");
    let mut no3 = idea_on("i_5", "4542e", "S", "I1", 5);
    no3["status"] = json!("no");
    let s = [
        seat("043dd", "yes", "slice"),
        seat("1ced7", "yes", "slice"),
        seat("2240e", "yes", "slice"),
        seat("4542e", "yes", "slice"),
    ];
    let out = run(
        &plan,
        Some(&whole(vec![no1, no2, nh, no3])),
        Some(&s),
        &[],
        &merged(&[]),
    );
    assert!(out.rows.is_empty(), "{:?}", out.rows);
    let mut pending = idea_on("i_4", "043dd", "S", "I1", 4);
    pending["status"] = json!("auto");
    pending["human_review"] = json!("pending");
    let out = run(
        &plan,
        Some(&whole(vec![pending])),
        Some(&s),
        &[],
        &merged(&[]),
    );
    assert_eq!(out.rows.len(), 1, "auto while pending is still auto");
}

#[test]
fn a_ledger_that_is_not_whole_gives_no_layer_6_and_no_for_andy() {
    let plan = json!({"nodes": [node("I1", "未開始", "x", false, &[])]});
    let mut nh = idea("i_n", "519d0", "A", "needs_human", 2);
    nh["node"] = json!("I1");
    let s = [seat("043dd", "yes", "slice")];
    for ledger in [
        json!({"complete": false, "ideas": [idea_on("i_1", "043dd", "S", "I1", 1), nh.clone()]}),
        json!({"ideas": [idea_on("i_1", "043dd", "S", "I1", 1), nh.clone()]}),
    ] {
        let out = run(&plan, Some(&ledger), Some(&s), &[], &merged(&[]));
        assert!(out.rows.is_empty() && out.for_andy.is_empty());
        assert_eq!(
            out.sources.get("ideas").map(String::as_str),
            Some("partial")
        );
        assert!(out.unknown.iter().any(|u| u.what == "aib-idea"));
    }
}

#[test]
fn an_idea_is_dispatched_only_under_a_node_still_to_do() {
    let plan = json!({"nodes": [node("D", "實查通過", "x", false, &[]), node("W", "開發中", "x", false, &[]),
                                node("M", "已同意未做", "043dd", true, &[])]});
    let s = [
        seat("043dd", "yes", "mainline"),
        seat("1ced7", "yes", "slice"),
        seat("2240e", "yes", "slice"),
        seat("4542e", "yes", "slice"),
        seat("7b81e", "yes", "slice"),
    ];
    let ideas = whole(vec![
        idea("i_none", "1ced7", "S", "auto", 1),
        idea_on("i_done", "2240e", "S", "D", 2),
        idea_on("i_moving", "4542e", "S", "W", 3),
        idea_on("i_gone", "7b81e", "S", "GONE", 4),
        idea_on("i_five", "1ced7", "A", "M", 5),
    ]);
    let out = run(&plan, Some(&ideas), Some(&s), &[], &merged(&[]));
    assert_eq!(
        ids(&out),
        vec!["M"],
        "layer 5 only, the idea on M is not doubled"
    );
    assert_eq!(out.rows[0].layer, 5);
    // no node, or a node not in the tree, is held back by rule: skipped (IFACE r9)
    assert!(out.unknown.is_empty(), "{:?}", out.unknown);
    let whats: Vec<&str> = out.skipped.iter().map(|k| k.what.as_str()).collect();
    assert_eq!(whats, vec!["i_gone", "i_none"]);
    assert!(
        out.skipped[1].reason.contains("沒掛節點"),
        "{:?}",
        out.skipped
    );
}

#[test]
fn rows_are_slice_and_go_only_to_a_seat_that_fits() {
    assert!(fits("mainline", "slice") && fits("slice", "slice") && fits("review", "review"));
    assert!(!fits("review", "slice") && !fits("none", "review") && !fits("slice", "huge"));
    // one owner per node (two are the coordinator's to split, 519d0 T5); the review-sized
    // 043dd still gets no idea row
    let plan = json!({"nodes": [node("N1", "已同意未做", "1ced7", true, &[]),
                                node("I1", "未開始", "x", false, &[])]});
    let s = [
        seat("043dd", "yes", "review"),
        seat("1ced7", "yes", "slice"),
    ];
    let out = run(
        &plan,
        Some(&whole(vec![idea_on("i_1", "043dd", "S", "I1", 1)])),
        Some(&s),
        &[],
        &merged(&[]),
    );
    assert_eq!(ids(&out), vec!["N1"], "the review seat gets no idea row");
    assert_eq!(
        (out.rows[0].to.as_str(), out.rows[0].size.as_str()),
        ("1ced7", "slice")
    );
}

#[test]
fn only_the_open_statuses_are_mainline_next() {
    let plan = json!({"nodes": [node("A", "已派", "043dd", true, &[]), node("B", "暫停", "043dd", true, &[]),
                                node("C", "某個新狀態", "043dd", true, &[]), node("E", "等前置", "043dd", true, &["pr:1"]),
                                node("F", "已定未做", "043dd", true, &[]), node("G", "未開始", "043dd", true, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(
        &plan,
        Some(&no_ideas()),
        Some(&s),
        &[],
        &merged(&[("pr:1", Some(true))]),
    );
    assert_eq!(ids(&out), vec!["E", "F", "G"]);
}

#[test]
fn an_unreadable_idea_ledger_is_unknown() {
    let out = run(&json!({"nodes": []}), None, Some(&[]), &[], &merged(&[]));
    assert!(out.unknown.iter().any(|u| u.what == "aib-idea"));
}

#[test]
fn the_fleet_blocker_is_the_biggest_group() {
    let plan = json!({"nodes": [node("Q1", "審過排隊", "x", false, &[]), node("Q2", "審過排隊", "x", false, &[]),
                                node("Q3", "審過排隊", "x", false, &[]),
                                node("W1", "未開始", "x", false, &["pr:749"]), node("W2", "未開始", "x", false, &["pr:749"]),
                                node("D", "實查通過", "x", false, &["pr:749"]),
                                node("A1", "等 Andy", "x", false, &[])]});
    let out = run(
        &plan,
        Some(&no_ideas()),
        Some(&[]),
        &[],
        &merged(&[("pr:749", Some(false))]),
    );
    let fb = out.fleet_blocker.expect("a group");
    assert_eq!(
        (fb.cause.as_str(), fb.count, fb.unblock_by.as_str()),
        ("macos_queue", 3, "S96 n08(#764)")
    );
    // one more node on pr:749 makes it the biggest; a done node never counts
    let mut plan2 = plan.clone();
    if let Some(ns) = plan2["nodes"].as_array_mut() {
        ns.push(node("W3", "未開始", "x", false, &["pr:749"]));
        ns.push(node("W4", "未開始", "x", false, &["pr:749"]));
    }
    let fb = run(
        &plan2,
        Some(&no_ideas()),
        Some(&[]),
        &[],
        &merged(&[("pr:749", Some(false))]),
    )
    .fleet_blocker
    .expect("a group");
    assert_eq!(
        (fb.cause.as_str(), fb.count, fb.unblock_by.as_str()),
        ("after", 4, "pr:749")
    );
    assert!(!fb.nodes.contains(&"D".to_string()));
}

#[test]
fn for_andy_puts_what_unblocks_most_first() {
    let plan = json!({"nodes": [node("N1", "未開始", "x", false, &[]), node("N2", "未開始", "x", false, &["node:N1"]),
                                node("N3", "未開始", "x", false, &[])]});
    let mut a = idea("i_a", "519d0", "A", "needs_human", 1);
    a["node"] = json!("N3");
    let mut b = idea("i_b", "519d0", "A", "needs_human", 2);
    b["node"] = json!("N1");
    let c = idea("i_c", "519d0", "A", "needs_human", 3);
    let mut said = idea("i_e", "519d0", "S", "needs_human", 5);
    said["status"] = json!("no");
    said["human_review"] = json!("no");
    said["node"] = json!("N1");
    let ideas = whole(vec![a, b, c, idea("i_d", "519d0", "A", "auto", 4), said]);
    let out = run(&plan, Some(&ideas), Some(&[]), &[], &merged(&[]));
    let got: Vec<(&str, usize)> = out
        .for_andy
        .iter()
        .map(|f| (f.id.as_str(), f.unblocks))
        .collect();
    assert_eq!(got, vec![("i_b", 2), ("i_a", 1), ("i_c", 0)]);
}

#[test]
fn the_waiting_andy_group_names_the_top_for_andy_item() {
    let plan = json!({"nodes": [node("A1", "等 Andy", "x", false, &[]), node("A2", "等 Andy", "x", false, &[]),
                                node("N1", "未開始", "x", false, &[])]});
    let mut b = idea("i_b", "519d0", "A", "needs_human", 2);
    b["node"] = json!("N1");
    let out = run(&plan, Some(&whole(vec![b])), Some(&[]), &[], &merged(&[]));
    let fb = out.fleet_blocker.expect("a group");
    assert_eq!(
        (fb.cause.as_str(), fb.unblock_by.as_str()),
        ("waiting_andy", "i_b")
    );
}

#[test]
fn owner_seats_takes_whole_five_hex_words_once() {
    assert_eq!(
        owner_seats("043dd(r-0 r-1)/ 1ced7(r-2a)/ 043dd"),
        vec!["043dd", "1ced7"]
    );
    assert!(owner_seats("Andy / a63abc / 0x43dd").is_empty());
}

#[test]
fn dep_parse_takes_the_three_prefixes_only() {
    assert_eq!(Dep::parse("pr:747"), Some(Dep::Pr(747)));
    assert_eq!(Dep::parse("skills-pr:14"), Some(Dep::SkillsPr(14)));
    assert_eq!(Dep::parse("node:S76"), Some(Dep::Node("S76".into())));
    for bad in ["pr:", "pr:7a", "node:", "PR:1", "pr:-1", "issue:3"] {
        assert_eq!(Dep::parse(bad), None, "{bad}");
    }
}

#[test]
fn seats_json_rows_without_a_seat_code_are_left_out() {
    let v = json!({"seats": [{"seat": "043dd", "dispatchable": "yes", "size": "slice", "role": "dev"},
                             {"seat": "nope", "dispatchable": "yes"}]});
    let s = seats_from_json(&v);
    assert_eq!(s, vec![seat("043dd", "yes", "slice")]);
}

#[test]
fn the_output_serializes_with_the_iface_field_names() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "043dd", true, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    let v = serde_json::to_value(&out).expect("serializes");
    assert!(v["rows"][0]["head"].is_null());
    assert_eq!(
        v["rows"][0]["scope_unchecked"],
        json!(true),
        "approved without scope"
    );
    assert_eq!(v["sources"]["tidy_scan"], json!("unsupported"));
    for k in [
        "rows",
        "unknown",
        "skipped",
        "fleet_blocker",
        "for_andy",
        "sources",
    ] {
        assert!(v.get(k).is_some(), "{k}");
    }
    for k in [
        "rank",
        "layer",
        "kind",
        "what",
        "pr",
        "node",
        "to",
        "why_him",
        "why_now",
        "unblocks",
        "waited_min",
        "needs",
        "size",
        "data_at",
        "head",
    ] {
        assert!(v["rows"][0].get(k).is_some(), "{k}");
    }
}

#[test]
fn a_node_whose_scope_changed_after_the_approval_is_unknown() {
    let s = [seat("043dd", "yes", "mainline")];
    let plan = json!({"nodes": [
        scoped("SAME", "043dd", &["pr:1"], ("title SAME", &["pr:1"])),
        scoped("TITLE", "043dd", &[], ("old title", &[])),
        scoped("AFTER", "043dd", &["pr:1", "pr:2"], ("title AFTER", &["pr:1"])),
    ]});
    let m = merged(&[("pr:1", Some(true)), ("pr:2", Some(true))]);
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &m);
    assert_eq!(ids(&out), vec!["SAME"]);
    assert!(!out.rows[0].scope_unchecked);
    let v = serde_json::to_value(&out).expect("serializes");
    assert!(
        v["rows"][0].get("scope_unchecked").is_none(),
        "written only when true"
    );
    // a scope changed after the approval is a rule: skipped (IFACE r9)
    assert!(out.unknown.is_empty(), "{:?}", out.unknown);
    let why: Vec<(&str, &str)> = out
        .skipped
        .iter()
        .map(|k| (k.what.as_str(), k.reason.as_str()))
        .collect();
    assert_eq!(
        why,
        vec![
            ("AFTER", "AFTER 核准後範圍被改"),
            ("TITLE", "TITLE 核准後範圍被改")
        ]
    );
}

#[test]
fn a_scope_that_cannot_be_hashed_is_unknown_not_a_row() {
    let s = [seat("043dd", "yes", "mainline")];
    let plan = json!({"nodes": [scoped("N1", "043dd", &[], ("title N1", &[]))]});
    let out = run_hash(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]), None);
    assert!(out.rows.is_empty());
    assert!(out
        .unknown
        .iter()
        .any(|u| u.what == "N1" && u.why.contains("雜湊讀不到")));
}

#[test]
fn an_idea_under_a_node_whose_scope_changed_is_unknown() {
    let s = [seat("1ced7", "yes", "slice"), seat("2240e", "yes", "slice")];
    let mut changed = scoped("C", "x", &[], ("old title", &[]));
    changed["status"] = json!("未開始");
    let mut open = node("U", "未開始", "x", true, &[]);
    open["status"] = json!("未開始");
    let plan = json!({"nodes": [changed, open]});
    let ideas = whole(vec![
        idea_on("i_c", "1ced7", "S", "C", 1),
        idea_on("i_u", "2240e", "S", "U", 2),
    ]);
    let out = run(&plan, Some(&ideas), Some(&s), &[], &merged(&[]));
    assert_eq!(ids(&out), vec!["U"]);
    assert!(out.rows[0].scope_unchecked, "approved without scope");
    assert!(out
        .skipped
        .iter()
        .any(|k| k.what == "i_c" && k.reason == "C 核准後範圍被改"));
}

#[test]
fn scope_text_is_the_title_then_each_after_item() {
    assert_eq!(scope_text("t", &["pr:1", "node:S7"]), "t\npr:1\nnode:S7");
    assert_eq!(scope_text("t", &[]), "t");
}

/// ebb36 f80a69ea M1: with seats holding the roster only, an owner off it is skipped for
/// the coordinator (not unknown, the answer stays complete) and is not counted as a
/// second owner (T5); without the roster it is still unknown.
#[test]
fn an_owner_off_the_roster_is_skipped_and_not_a_second_owner() {
    let plan = json!({"nodes": [node("N1", "已同意未做", "33e28", true, &[]),
                                node("N2", "已同意未做", "043dd 33e28", true, &[])]});
    let s = [seat("043dd", "yes", "mainline")];
    let out = run_on_roster(&plan, &no_ideas(), &s);
    assert!(out.unknown.is_empty(), "{:?}", out.unknown);
    assert!(
        out.skipped
            .iter()
            .any(|k| k.what == "N1" && k.reason.contains("不在名冊")),
        "{:?}",
        out.skipped
    );
    assert_eq!(ids(&out), vec!["N2"], "{:?}", out.skipped);
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert!(
        out.unknown.iter().any(|u| u.what == "N1"),
        "{:?}",
        out.unknown
    );
}

/// ebb36 f80a69ea M1, layer 6: an idea whose seat is off the roster is skipped, not unknown.
#[test]
fn an_idea_of_a_seat_off_the_roster_is_skipped() {
    let ideas = whole(vec![idea_on("i_1", "33e28", "S", "I1", 1)]);
    let plan = json!({"nodes": [node("I1", "未開始", "x", false, &[])]});
    let s = [seat("043dd", "yes", "slice")];
    let out = run_on_roster(&plan, &ideas, &s);
    assert!(
        out.rows.is_empty() && out.unknown.is_empty(),
        "{:?}",
        out.unknown
    );
    assert!(
        out.skipped
            .iter()
            .any(|k| k.what == "i_1" && k.reason.contains("不在名冊")),
        "{:?}",
        out.skipped
    );
}

/// 519d0 D1: no queue front known ⇒ the generic label, and an `unknown` line says so.
#[test]
fn the_queue_group_without_a_front_is_unknown() {
    let plan = json!({"nodes": [node("Q1", "審過排隊", "x", false, &[]), node("Q2", "審過排隊", "x", false, &[])]});
    let claimed = BTreeSet::new();
    let ideas = no_ideas();
    let inp = LocalInput {
        plan: &plan,
        ideas: Some(&ideas),
        seats: Some(&[]),
        claimed: &claimed,
        queue_unblock: None,
        scope_hash: Some(&fake_hash),
        roster_applied: false,
        tidy: None,
        tidy_age_s: None,
        as_of: "T",
    };
    let out = rank_local(&inp, &merged(&[]));
    let fb = out.fleet_blocker.expect("a group");
    assert_eq!(
        (fb.cause.as_str(), fb.unblock_by.as_str()),
        ("macos_queue", "合併隊前面的那張")
    );
    assert!(
        out.unknown.iter().any(|u| u.what == "全艦卡點 unblock_by"),
        "{:?}",
        out.unknown
    );
    // with a front: no such unknown line
    let out = run(&plan, Some(&no_ideas()), Some(&[]), &[], &merged(&[]));
    assert!(!out.unknown.iter().any(|u| u.what == "全艦卡點 unblock_by"));
}

/// 519d0 D1: the lowest open PR number, its seats from every branch that carries it.
#[test]
fn the_queue_front_is_the_lowest_open_pr_and_its_seats() {
    assert_eq!(queue_front([]), None);
    assert_eq!(
        queue_front([(664, false, Some("ebb36"))]),
        None,
        "only open PRs"
    );
    assert_eq!(
        queue_front([
            (768, true, Some("043dd")),
            (666, true, Some("c477a")),
            (666, true, Some("7b81e")),
            (664, false, Some("ebb36"))
        ]),
        Some("#666 7b81e/c477a".into())
    );
    assert_eq!(queue_front([(5, true, None)]), Some("#5 unknown".into()));
    assert_eq!(
        queue_front([(5, true, None), (5, true, Some("a9136"))]),
        Some("#5 a9136".into())
    );
    assert_eq!(
        queue_front([(5, true, Some("Andy"))]),
        Some("#5 unknown".into()),
        "not a seat code"
    );
}

/// IFACE r9 3: an intent for dispatch / open_review within 24 hours and not closed by
/// done / abandoned claims its node; no file is nothing claimed.
#[test]
fn intents_claim_a_node_for_a_day_until_closed() {
    let now = 100 * CLAIM_HOLD_MS;
    let line = |kind: &str, key: &str, node: Option<&str>, ago_ms: i64| {
        let mut v = json!({"kind": kind, "key": key, "at_ms": now - ago_ms});
        if let Some(n) = node {
            v["node"] = json!(n);
        }
        v.to_string()
    };
    assert_eq!(claimed_from_intents(None, now), (BTreeSet::new(), 0));
    let text = [
        line("intent", "dispatch:A:-:043dd", Some("A"), 1000),
        line("intent", "dispatch:B:-:043dd", None, 1000),
        line("intent", "open_review:#764:abc:1ced7", None, 1000),
        line("intent", "dispatch:C:-:043dd", Some("C"), 1000),
        line("done", "dispatch:C:-:043dd", None, 10),
        line("intent", "dispatch:D:-:043dd", Some("D"), 1000),
        line("abandoned", "dispatch:D:-:043dd", None, 10),
        line("intent", "dispatch:E:-:043dd", Some("E"), CLAIM_HOLD_MS),
        line("intent", "nudge:q_1:-:043dd", Some("F"), 1000),
        line("intent", "dispatch:G:-:043dd", Some("G"), -60_000),
        "{\"kind\":\"intent\"}".to_owned(),
        "not json".to_owned(),
        String::new(),
    ]
    .join("\n");
    let (set, bad) = claimed_from_intents(Some(&text), now);
    let want: BTreeSet<String> = ["#764", "A", "B"].iter().map(|s| s.to_string()).collect();
    assert_eq!(
        set, want,
        "C done, D abandoned, E a day old, F a nudge, G from the future"
    );
    assert_eq!(bad, 2, "no key, not JSON; an empty line is not bad");
}

/// C13: plan times are Taipei, `x` read as 9 and a date alone as 23:59 (a lower bound);
/// later than now by up to a day is now, a December time read in January is last year's,
/// any other future is `None` (043dd 55e958c3 M1).
#[test]
fn plan_times_read_as_the_latest_they_can_mean() {
    let now = as_of_ms("2026-11-14T16:00:00Z").expect("as_of");
    // 2026-11-15 00:00 Taipei = 2026-11-14 16:00 UTC
    assert_eq!(plan_time_ms("11-15 00:00", now), Some(now));
    assert_eq!(plan_time_ms("11-14 23:5x", now), Some(now - 60_000));
    assert_eq!(plan_time_ms("11-14 23:xx", now), Some(now - 60_000));
    assert_eq!(plan_time_ms("11-14", now), Some(now - 60_000));
    assert_eq!(plan_time_ms("11-14 20:00", now), Some(now - 4 * 3_600_000));
    // today written as a date alone, or 00:0x read at 00:00: the rounding is ahead ⇒ now
    assert_eq!(plan_time_ms("11-15", now), Some(now));
    assert_eq!(plan_time_ms("11-15 00:0x", now), Some(now));
    // further ahead and not a December read in January ⇒ not known, never last year
    assert_eq!(plan_time_ms("12-01 00:00", now), None);
    assert_eq!(plan_time_ms("11-17", now), None);
    // 2027-01-02 00:00 Taipei reading 12-31 12:00 ⇒ 2026-12-31 12:00, 36 hours back
    let jan = as_of_ms("2027-01-01T16:00:00Z").expect("jan");
    assert_eq!(plan_time_ms("12-31 12:00", jan), Some(jan - 36 * 3_600_000));
    for bad in [
        "",
        "x",
        "13-01 00:00",
        "11-14 2:00",
        "11-14 aa:00",
        "11/14 20:00",
    ] {
        assert_eq!(plan_time_ms(bad, now), None, "{bad:?}");
    }
    assert_eq!(as_of_ms("T"), None);
    assert_eq!(as_of_ms("1970-01-01T00:00:00Z"), Some(0));
}

/// C13: a mainline node waits from the later of approved.at and updated; an idea from
/// its review; both say waited_min, unknown time ⇒ none.
#[test]
fn layer_five_and_six_say_how_long_they_waited() {
    let mut a = node("A", "已同意未做", "043dd", true, &[]);
    a["approved"]["at"] = json!("11-14 19:00");
    a["updated"] = json!("11-14 21:00");
    let mut b = node("B", "已同意未做", "1ced7", true, &[]);
    b["approved"]["at"] = json!("11-14 23:5x");
    let mut c = node("C", "已同意未做", "b433b", true, &[]);
    // approved() writes 12:00; C must have no time at all
    c["approved"]["at"] = json!(null);
    let plan = json!({"nodes": [a, b, c, node("N", "未開始", "x", false, &[])]});
    let now = as_of_ms("2026-11-14T16:00:00Z").expect("as_of");
    let ideas = json!({"complete": true, "ideas": [
        {"short": "i_1", "title": "t", "grade": "A", "classification": "auto", "status": "ok",
         "human_review": "ok", "seat": "a9136", "node": "N", "at_ms": now - 600 * 60_000,
         "review": {"at_ms": now - 300 * 60_000}}]});
    let s = [
        seat("043dd", "yes", "mainline"),
        seat("1ced7", "yes", "mainline"),
        seat("b433b", "yes", "mainline"),
        seat("a9136", "yes", "mainline"),
    ];
    let claimed = BTreeSet::new();
    let inp = LocalInput {
        plan: &plan,
        ideas: Some(&ideas),
        seats: Some(&s),
        claimed: &claimed,
        queue_unblock: None,
        scope_hash: Some(&fake_hash),
        roster_applied: false,
        tidy: None,
        tidy_age_s: None,
        as_of: "2026-11-14T16:00:00Z",
    };
    let out = rank_local(&inp, &merged(&[]));
    let w = |id: &str| {
        out.rows
            .iter()
            .find(|r| r.node.as_deref() == Some(id))
            .map(|r| r.waited_min)
    };
    assert_eq!(
        w("A"),
        Some(Some(180)),
        "updated 21:00 is later than approved 19:00: {:?}",
        out.rows
    );
    assert_eq!(w("B"), Some(Some(1)));
    assert_eq!(w("C"), Some(None), "no time written");
    assert_eq!(
        w("N"),
        Some(Some(300)),
        "the idea under N waits from its review"
    );
}

/// C7 (519d0 07:41): layer 7 reads tidy-scan's `redundant` items, one row each, only when
/// hung on a node still to do, to its one owner; no node, a node not in the plan or an owner
/// that is not one seat is skipped with the reason; `dead` items and done nodes give
/// nothing. A scan not complete is `partial` (rows still come), another schema `failed`.
#[test]
fn layer_seven_folds_tidy_scan_duplicates_hung_on_a_node() {
    let plan = json!({"nodes": [
        node("N7", "未開始", "043dd", false, &[]),
        node("D7", "完成", "043dd", false, &[]),
        node("M7", "未開始", "043dd/1ced7", false, &[]),
    ]});
    let item = |group: &str, kind: &str, key: &str, node: Option<&str>| {
        let mut i = json!({"group": group, "kind": kind, "key": key, "paths": ["a.md", "b.md"],
            "symbol": "a.md", "evidence": "same 40 bytes", "reliability": "high",
            "removal_risk": "low", "new": null});
        if let Some(n) = node {
            i["node"] = json!(n);
        }
        i
    };
    let report = |status: &str| {
        json!({"schema": TIDY_SCHEMA, "status": status, "head": "m0", "main": "m0", "items": [
            item("redundant", "identical_files", "identical_files:a.md|b.md", Some("N7")),
            item("redundant", "duplicate_knife", "duplicate_knife:k1", None),
            item("dead", "unreferenced_file", "unreferenced_file:a.md", Some("N7")),
            item("redundant", "identical_files", "identical_files:c|d", Some("NX")),
            item("redundant", "identical_files", "identical_files:e|f", Some("M7")),
            item("redundant", "identical_files", "identical_files:g|h", Some("D7")),
        ]})
    };
    let s = [
        seat("043dd", "yes", "mainline"),
        seat("1ced7", "yes", "mainline"),
    ];
    let claimed = BTreeSet::new();
    let go = |tidy: Option<&Value>| {
        rank_local(
            &LocalInput {
                plan: &plan,
                ideas: None,
                seats: Some(&s),
                claimed: &claimed,
                queue_unblock: None,
                scope_hash: Some(&fake_hash),
                roster_applied: false,
                tidy,
                tidy_age_s: Some(60),
                as_of: "2026-11-14T16:00:00Z",
            },
            &merged(&[]),
        )
    };
    let whole = report("complete");
    let out = go(Some(&whole));
    let seven: Vec<(&str, &str, Option<&str>)> = out
        .rows
        .iter()
        .filter(|r| r.layer == LAYER_TIDY)
        .map(|r| (r.what.as_str(), r.to.as_str(), r.node.as_deref()))
        .collect();
    assert_eq!(
        seven,
        vec![("收斂 identical_files a.md b.md", "043dd", Some("N7"))],
        "{out:?}"
    );
    assert_eq!(out.sources.get("tidy_scan").map(String::as_str), Some("ok"));
    let why = |key: &str| {
        out.skipped
            .iter()
            .find(|k| k.what == key)
            .map(|k| k.reason.clone())
    };
    assert_eq!(why("duplicate_knife:k1").as_deref(), Some("沒掛節點,不派"));
    assert!(why("identical_files:c|d").is_some_and(|r| r.contains("不在進度樹")));
    assert!(why("identical_files:e|f").is_some_and(|r| r.contains("不是單一席")));
    assert_eq!(
        why("identical_files:g|h"),
        None,
        "a done node gives nothing"
    );
    assert_eq!(why("unreferenced_file:a.md"), None, "dead is not read");
    // a scan with a section not complete: partial, and its rows still come
    let part = report("partial");
    let out = go(Some(&part));
    assert_eq!(
        out.sources.get("tidy_scan").map(String::as_str),
        Some("partial")
    );
    // an optional source (519d0 09:33): a skipped line, never unknown
    let tidy_skipped = |o: &LocalOut| o.skipped.iter().any(|k| k.what == "tidy-scan");
    assert!(tidy_skipped(&out), "{out:?}");
    assert!(out.unknown.iter().all(|u| u.what != "tidy-scan"), "{out:?}");
    assert_eq!(out.rows.iter().filter(|r| r.layer == LAYER_TIDY).count(), 1);
    // another schema (or not JSON, read as null): failed, no row
    let other = json!({"schema": "other", "status": "complete", "items": []});
    let out = go(Some(&other));
    assert_eq!(
        out.sources.get("tidy_scan").map(String::as_str),
        Some("failed")
    );
    assert!(out.rows.iter().all(|r| r.layer != LAYER_TIDY));
    assert!(tidy_skipped(&out), "{out:?}");
    assert!(out.unknown.iter().all(|u| u.what != "tidy-scan"), "{out:?}");
    // not given: unsupported, no row, a skipped line, nothing unknown about it
    let out = go(None);
    assert_eq!(
        out.sources.get("tidy_scan").map(String::as_str),
        Some("unsupported")
    );
    assert!(tidy_skipped(&out), "{out:?}");
    assert!(out.unknown.iter().all(|u| u.what != "tidy-scan"));
}

/// C7b (1ced7 / 5d33a L1, N2, N3 on C7; 519d0 09:3x on C7b M1): a report older than an hour,
/// or whose head is not the main the same refresh wrote into it, gives no row and says stale
/// (a skipped line, not unknown); one row a node a round; a node layer 5 gave a row gets none.
#[test]
fn layer_seven_skips_a_stale_report_and_gives_one_row_a_node() {
    let plan = json!({"nodes": [
        node("N7", "未開始", "043dd", false, &[]),
        node("P7", "未開始", "043dd", false, &[]),
        node("M5", "未開始", "043dd", true, &[]),
    ]});
    let item = |key: &str, n: &str| {
        json!({"group": "redundant", "kind": "identical_files", "key": key, "paths": [key],
            "symbol": key, "evidence": "same", "reliability": "high", "removal_risk": "low",
            "new": null, "node": n})
    };
    let report = |head: &str, main: Option<&str>| {
        json!({"schema": TIDY_SCHEMA, "status": "complete", "head": head, "main": main, "items": [
            item("k1", "N7"), item("k2", "N7"), item("k3", "P7"), item("k4", "M5"),
        ]})
    };
    let at_main = report("m0", Some("m0"));
    let old_head = report("m1", Some("m0"));
    let no_main = report("m0", None);
    let s = [seat("043dd", "yes", "mainline")];
    let claimed = BTreeSet::new();
    let go_on = |t: &Value, age: Option<u64>| {
        rank_local(
            &LocalInput {
                plan: &plan,
                ideas: None,
                seats: Some(&s),
                claimed: &claimed,
                queue_unblock: None,
                scope_hash: Some(&fake_hash),
                roster_applied: false,
                tidy: Some(t),
                tidy_age_s: age,
                as_of: "2026-11-14T16:00:00Z",
            },
            &merged(&[]),
        )
    };
    let layer = |o: &LocalOut, l: u8| -> Vec<String> {
        o.rows
            .iter()
            .filter(|r| r.layer == l)
            .filter_map(|r| r.node.clone())
            .collect()
    };
    let why = |o: &LocalOut, key: &str| {
        o.skipped
            .iter()
            .find(|k| k.what == key)
            .map(|k| k.reason.clone())
    };
    let go = |age: Option<u64>| go_on(&at_main, age);
    // exactly an hour old still counts as fresh
    let out = go(Some(TIDY_STALE_S));
    assert_eq!(out.sources.get("tidy_scan").map(String::as_str), Some("ok"));
    assert_eq!(
        layer(&out, LAYER_MAINLINE),
        vec!["M5".to_owned()],
        "{out:?}"
    );
    assert_eq!(
        layer(&out, LAYER_TIDY),
        vec!["N7".to_owned(), "P7".to_owned()],
        "one row a node, none for M5: {out:?}"
    );
    assert!(
        why(&out, "k2").is_some_and(|r| r.contains("這輪已有一列")),
        "{out:?}"
    );
    assert!(
        why(&out, "k4").is_some_and(|r| r.contains("這輪已有一列")),
        "{out:?}"
    );
    // stale: no row, sources stale, a skipped line (an optional source: never unknown)
    let stale = |out: &LocalOut, says: &str| {
        assert_eq!(
            out.sources.get("tidy_scan").map(String::as_str),
            Some("stale"),
            "{says}"
        );
        assert!(layer(out, LAYER_TIDY).is_empty(), "{says}");
        assert!(
            why(out, "tidy-scan").is_some_and(|r| r.contains(says)),
            "{says}: {out:?}"
        );
        assert!(
            out.unknown.iter().all(|u| u.what != "tidy-scan"),
            "{says}: {out:?}"
        );
    };
    stale(&go(Some(TIDY_STALE_S + 1)), "沒更新");
    // no time in the file, a head that is not the main the same refresh wrote, or no main
    stale(&go_on(&at_main, None), "沒有產生時間");
    stale(&go_on(&old_head, Some(0)), "不是同一輪 refresh 的 main");
    stale(&go_on(&no_main, Some(0)), "沒寫同一輪 refresh 的 main");
}
