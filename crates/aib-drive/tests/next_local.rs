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

fn run(
    plan: &Value,
    ideas: Option<&Value>,
    seats: Option<&[SeatFact]>,
    claimed: &[&str],
    m: &Merged,
) -> LocalOut {
    let claimed: BTreeSet<String> = claimed.iter().map(|s| s.to_string()).collect();
    let inp = LocalInput {
        plan,
        ideas,
        seats,
        claimed: &claimed,
        queue_unblock: Some("S96 n08(#764)"),
        as_of: "T",
    };
    rank_local(&inp, m)
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

#[test]
fn ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one() {
    let ideas = json!({"ideas": [idea("i_1", "043dd", "B", "auto", 1), idea("i_2", "043dd", "S", "auto", 2),
                                 idea("i_3", "1ced7", "A", "auto", 3), idea("i_4", "1ced7", "S", "needs_human", 4),
                                 idea("i_5", "043dd", "S", "auto", 1)]});
    let s = [
        seat("043dd", "yes", "slice"),
        seat("1ced7", "yes", "review"),
    ];
    let out = run(
        &json!({"nodes": []}),
        Some(&ideas),
        Some(&s),
        &[],
        &merged(&[]),
    );
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
    assert!(out.rows.iter().all(|r| r.layer == 6));
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
    let ideas = json!({"ideas": [a, b, c, idea("i_d", "519d0", "A", "auto", 4)]});
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
    let out = run(
        &plan,
        Some(&json!({"ideas": [b]})),
        Some(&[]),
        &[],
        &merged(&[]),
    );
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
    for k in ["rows", "unknown", "fleet_blocker", "for_andy"] {
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
    ] {
        assert!(v["rows"][0].get(k).is_some(), "{k}");
    }
}
