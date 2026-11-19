//! S104 變動標記(DESIGN-plan-badges-519d0-1115 r0 §1 驗收):六種標記各一、優先序、群組加總、
//! 看過之後清空、兩席快照互不影響、卡住的時間讀法、gate 讀原文。
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_plan::badges::*;
use aib_plan::{index, parse, Plan};
use std::collections::{BTreeMap, HashSet};

/// 2026-11-15 12:00 台北 = 04:00 UTC
const NOW: i64 = 1_794_715_200;

fn plan(nodes: &str) -> Plan {
    parse(&format!(r#"{{"nodes":[{nodes}]}}"#)).expect("fixture parses")
}

fn ids(p: &Plan) -> HashSet<String> {
    p.nodes.iter().map(|n| n.id.clone()).collect()
}

fn by_id(p: &Plan, m: &BTreeMap<usize, Badge>) -> BTreeMap<String, Badge> {
    m.iter()
        .map(|(i, b)| (p.nodes[*i].id.clone(), *b))
        .collect()
}

const BEFORE: &str = r#"
  {"id":"V","parent":null,"kind":"vision","title":"總"},
  {"id":"G","parent":"V","kind":"group","title":"組"},
  {"id":"D","parent":"G","kind":"task","title":"會完成","status":"審過排隊","updated":"11-15 11:5x"},
  {"id":"U","parent":"G","kind":"task","title":"會更新","status":"開發中","evidence":"a","updated":"11-15 11:5x"},
  {"id":"B","parent":"G","kind":"task","title":"會退回","status":"審過未推","updated":"11-15 11:5x"},
  {"id":"W","parent":"G","kind":"task","title":"等 Andy","status":"未開始"},
  {"id":"S","parent":"G","kind":"task","title":"卡住","status":"開發中","updated":"11-15 07:0x"},
  {"id":"Q","parent":"G","kind":"task","title":"沒變","status":"未開始"}
"#;

const AFTER: &str = r#"
  {"id":"V","parent":null,"kind":"vision","title":"總"},
  {"id":"G","parent":"V","kind":"group","title":"組"},
  {"id":"D","parent":"G","kind":"task","title":"會完成","status":"實查通過","updated":"11-15 11:5x"},
  {"id":"U","parent":"G","kind":"task","title":"會更新","status":"開發中","evidence":"a b","updated":"11-15 11:5x"},
  {"id":"B","parent":"G","kind":"task","title":"會退回","status":"開發中","updated":"11-15 11:5x"},
  {"id":"W","parent":"G","kind":"task","title":"等 Andy","status":"等 Andy"},
  {"id":"S","parent":"G","kind":"task","title":"卡住","status":"開發中","updated":"11-15 07:0x"},
  {"id":"Q","parent":"G","kind":"task","title":"沒變","status":"未開始"},
  {"id":"N","parent":"G","kind":"task","title":"新的","status":"未開始"}
"#;

#[test]
fn each_of_the_six_badges_and_nothing_on_an_unchanged_node() {
    let old = plan(BEFORE);
    let snap = snapshot(&old, &ids(&old), NOW - 3600);
    let p = plan(AFTER);
    let m = compute(&p, &ids(&p), Some(&snap), &HashSet::new(), NOW);
    let got = by_id(&p, &m);
    let want: BTreeMap<String, Badge> = [
        ("D", Badge::Done),
        ("U", Badge::Updated),
        ("B", Badge::Back),
        ("W", Badge::WaitYou),
        ("S", Badge::Stuck),
        ("N", Badge::New),
    ]
    .into_iter()
    .map(|(k, b)| (k.to_owned(), b))
    .collect();
    assert_eq!(got, want);
    let words: Vec<&str> = ORDER.iter().map(|b| b.word()).collect();
    assert_eq!(words, vec!["退回", "等你", "完成", "新", "更新", "卡住"]);
}

#[test]
fn one_badge_per_node_by_priority() {
    // 退回 beats 等你 (a node sent back and now waiting on Andy) and 更新 (its status changed)
    let old = plan(
        r#"{"id":"V","parent":null,"title":"總"},{"id":"A","parent":"V","title":"a","status":"實查通過"}"#,
    );
    let snap = snapshot(&old, &ids(&old), 0);
    let p = plan(
        r#"{"id":"V","parent":null,"title":"總"},{"id":"A","parent":"V","title":"a","status":"等 Andy"}"#,
    );
    assert_eq!(
        by_id(
            &p,
            &compute(&p, &ids(&p), Some(&snap), &HashSet::new(), NOW)
        )["A"],
        Badge::Back
    );
    // 等你 beats 完成 / 更新; 完成 beats 更新; 新 beats 卡住
    let gated: HashSet<String> = ["A".to_owned()].into();
    let old = plan(
        r#"{"id":"V","parent":null,"title":"總"},{"id":"A","parent":"V","title":"a","status":"審過排隊"}"#,
    );
    let snap = snapshot(&old, &ids(&old), 0);
    let p = plan(
        r#"{"id":"V","parent":null,"title":"總"},{"id":"A","parent":"V","title":"a","status":"實查通過"},
                    {"id":"K","parent":"V","title":"k","status":"開發中","updated":"11-14 01:0x"}"#,
    );
    let m = by_id(&p, &compute(&p, &ids(&p), Some(&snap), &gated, NOW));
    assert_eq!(m["A"], Badge::WaitYou);
    let m = by_id(
        &p,
        &compute(&p, &ids(&p), Some(&snap), &HashSet::new(), NOW),
    );
    assert_eq!(m["A"], Badge::Done);
    assert_eq!(m["K"], Badge::New, "new beats stuck");
}

#[test]
fn going_back_follows_the_progress_order_and_fail_and_pass() {
    assert!(went_back(Some("審過未推"), Some("開發中")));
    assert!(went_back(Some("實查通過"), Some("實查不通過")));
    assert!(went_back(Some("未開始"), Some("實查不通過")));
    assert!(
        went_back(Some("實查通過"), Some("暫停")),
        "pass to anything"
    );
    assert!(!went_back(Some("開發中"), Some("審過未推")));
    assert!(
        !went_back(Some("開發中"), Some("暫停")),
        "an unranked word is not judged"
    );
    assert!(!went_back(Some("實查不通過"), Some("實查不通過")));
    assert!(!went_back(Some("未開始"), Some("已定未做")), "same rank");
}

#[test]
fn a_collapsed_group_shows_the_badges_below_it_added_up() {
    let old = plan(BEFORE);
    let snap = snapshot(&old, &ids(&old), 0);
    let p = plan(AFTER);
    let ix = index(&p);
    let m = compute(&p, &ids(&p), Some(&snap), &HashSet::new(), NOW);
    assert_eq!(
        group_tail(&p, &ix, ix.by["V"], &m),
        "[退回 1][等你 1][完成 1][新 1][更新 1][卡住 1]"
    );
    assert_eq!(
        group_tail(&p, &ix, ix.by["Q"], &m),
        "",
        "a leaf has nothing below it"
    );
}

#[test]
fn seen_clears_the_change_badges_and_two_seats_keep_their_own() {
    let d = std::env::temp_dir().join(format!("aib-plan-badges-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    let old = plan(BEFORE);
    let a = d.join(seen_file_name(Some("aaaaa")));
    let b = d.join(seen_file_name(Some("bbbbb")));
    assert_ne!(a, b);
    assert_eq!(read_snapshot(&a), Ok(None), "no file yet");
    write_snapshot(&a, &snapshot(&old, &ids(&old), NOW - 60)).unwrap();
    write_snapshot(&b, &snapshot(&old, &ids(&old), NOW - 60)).unwrap();
    let p = plan(AFTER);
    // seat a presses v: its snapshot is now; only the state badges stay
    write_snapshot(&a, &snapshot(&p, &ids(&p), NOW)).unwrap();
    let sa = read_snapshot(&a).unwrap().unwrap();
    let sb = read_snapshot(&b).unwrap().unwrap();
    let ma = by_id(&p, &compute(&p, &ids(&p), Some(&sa), &HashSet::new(), NOW));
    let left: Vec<Badge> = ma.values().copied().collect();
    assert_eq!(left, vec![Badge::Stuck, Badge::WaitYou], "{ma:?}");
    let mb = compute(&p, &ids(&p), Some(&sb), &HashSet::new(), NOW);
    assert_eq!(mb.len(), 6, "seat b has not looked: its badges stay");
    std::fs::write(&b, "{").unwrap();
    assert!(read_snapshot(&b).is_err());
    let _ = std::fs::remove_dir_all(&d);
    assert_eq!(seen_file_name(None), "seen-all.json");
    assert_eq!(seen_file_name(Some("../x")), "seen-all.json");
}

#[test]
fn without_a_snapshot_only_the_state_badges_show_and_the_summary_asks_for_v() {
    let p = plan(AFTER);
    let m = compute(&p, &ids(&p), None, &HashSet::new(), NOW);
    let got: Vec<Badge> = by_id(&p, &m).values().copied().collect();
    assert_eq!(got, vec![Badge::Stuck, Badge::WaitYou]);
    let line = summary(&m, None, None, |_| "x".into()).unwrap();
    assert!(line.contains("按 v"), "{line}");
    let snap = snapshot(&p, &ids(&p), NOW);
    let line = summary(&m, Some(&snap), None, |_| "12:00".into()).unwrap();
    assert_eq!(line, "上次看之後:等你 1 · 卡住 1(12:00 起,按 v 看過)");
    assert_eq!(
        summary(&BTreeMap::new(), Some(&snap), None, |_| "x".into()),
        None
    );
    assert!(summary(&m, Some(&snap), Some("bad"), |_| "x".into())
        .unwrap()
        .contains("讀不到"));
}

#[test]
fn grafted_nodes_are_not_compared() {
    let old = plan(BEFORE);
    let only: HashSet<String> = ids(&old);
    let snap = snapshot(&old, &only, 0);
    let p = plan(AFTER);
    // N is not in the data ids (as a branch the cockpit grafts on would not be): no [新]
    let m = by_id(&p, &compute(&p, &only, Some(&snap), &HashSet::new(), NOW));
    assert!(!m.contains_key("N"), "{m:?}");
}

#[test]
fn updated_reads_month_day_and_ten_minute_marks_in_taipei_time() {
    // 11-15 12:00 Taipei is NOW
    assert_eq!(updated_epoch("11-15 12:0x", NOW), Some(NOW));
    assert_eq!(updated_epoch("11-15 11:57", NOW), Some(NOW - 180));
    assert_eq!(
        updated_epoch("11-15 07:5x", NOW),
        Some(NOW - 4 * 3600 - 600)
    );
    // a date later than tomorrow is last year
    let t = updated_epoch("12-31 23:0x", NOW).unwrap();
    assert!(t < NOW - 300 * 86_400, "{t}");
    for bad in [
        "",
        "11-15",
        "11/15 12:00",
        "13-01 00:00",
        "11-15 24:00",
        "11-15 12:7x",
        "a-b c:d",
    ] {
        assert_eq!(updated_epoch(bad, NOW), None, "{bad}");
    }
    // stuck only past the 4 hours, only for the three working statuses
    let p = plan(
        r#"{"id":"V","parent":null,"title":"總"},
        {"id":"A","parent":"V","title":"a","status":"開發中","updated":"11-15 08:0x"},
        {"id":"B","parent":"V","title":"b","status":"開發中","updated":"11-15 07:5x"},
        {"id":"C","parent":"V","title":"c","status":"未開始","updated":"11-14 00:0x"},
        {"id":"D","parent":"V","title":"d","status":"審過排隊"}"#,
    );
    let m = by_id(&p, &compute(&p, &ids(&p), None, &HashSet::new(), NOW));
    let stuck: Vec<&str> = m.keys().map(String::as_str).collect();
    assert_eq!(stuck, vec!["B"]);
}

#[test]
fn gate_is_read_from_the_raw_data() {
    let text = r#"{"nodes":[{"id":"A","gate":"biometric"},{"id":"B","gate":null},{"id":"C"},
        {"id":"D","gate":true}]}"#;
    let g = gated_ids(text);
    let mut v: Vec<&str> = g.iter().map(String::as_str).collect();
    v.sort();
    assert_eq!(v, vec!["A", "D"]);
    assert!(gated_ids("{").is_empty());
}
