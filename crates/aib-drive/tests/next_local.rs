//! S79 n-2c: layers 5–6, fleet_blocker and for_andy of `aib-drive next`.
//! The module is compiled here by path until the integration adds it to lib.rs
//! (IFACE-s79-drive-519d0-1115 §0: each slice adds only its own files).

#[path = "../src/next_local.rs"]
#[allow(dead_code)]
mod next_local;

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
        as_of: "T",
    };
    rank_local(&inp, m)
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
    assert!(out.unknown[0].why.contains("issue:3"));
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
    let plan = json!({"nodes": [node("N1", "已同意未做", "519d0 / 043dd(r-0)/ 1ced7", true, &[])]});
    let mut coord = seat("519d0", "yes", "mainline");
    coord.role = "coordinator".into();
    let s = [
        coord,
        seat("043dd", "no", "none"),
        seat("1ced7", "yes", "slice"),
    ];
    let out = run(&plan, Some(&no_ideas()), Some(&s), &[], &merged(&[]));
    assert_eq!(out.rows[0].to, "1ced7");
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
    let whats: Vec<&str> = out.unknown.iter().map(|u| u.what.as_str()).collect();
    assert_eq!(whats, vec!["N1", "N2"]);
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
    let whats: Vec<&str> = out.unknown.iter().map(|u| u.what.as_str()).collect();
    assert_eq!(whats, vec!["i_gone", "i_none"]);
    assert!(out.unknown[1].why.contains("沒掛節點"), "{:?}", out.unknown);
}

#[test]
fn rows_are_slice_and_go_only_to_a_seat_that_fits() {
    assert!(fits("mainline", "slice") && fits("slice", "slice") && fits("review", "review"));
    assert!(!fits("review", "slice") && !fits("none", "review") && !fits("slice", "huge"));
    let plan = json!({"nodes": [node("N1", "已同意未做", "043dd / 1ced7", true, &[]),
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
    for k in ["rows", "unknown", "fleet_blocker", "for_andy", "sources"] {
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
    let why: Vec<(&str, &str)> = out
        .unknown
        .iter()
        .map(|u| (u.what.as_str(), u.why.as_str()))
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
        .unknown
        .iter()
        .any(|u| u.what == "i_c" && u.why == "C 核准後範圍被改"));
}

#[test]
fn scope_text_is_the_title_then_each_after_item() {
    assert_eq!(scope_text("t", &["pr:1", "node:S7"]), "t\npr:1\nnode:S7");
    assert_eq!(scope_text("t", &[]), "t");
}
