#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-workspace wave 2 (MODEL-workspace-tree-7b81e-1118 r1): with `AIBD_TREE_IN_DB=1` (its own
//! process: the flag is read from the environment) the tree file is taken into
//! `workspace.sqlite` at start, once; the repeated ids that did not come in are named in a
//! `tree.imported` event; from then on the table is the tree, while the flag stays on and the
//! file is only written by aibd (N-workspace-c4 A / 5d33a L2: a write from outside lets go).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

const TREE: &str = r#"{"asof": "11-18", "nodes": [
  {"id": "V", "parent": null, "kind": "vision", "title": "總綱"},
  {"id": "N-ws", "parent": "V", "kind": "item", "title": "工作台", "status": "開發中", "owner": "519d0"},
  {"id": "N-ws", "parent": "V", "kind": "item", "title": "重複"}
]}"#;

#[tokio::test(flavor = "multi_thread")]
async fn the_tree_comes_into_aibd_once_and_the_table_is_the_tree() {
    std::env::set_var("AIBD_TREE_IN_DB", "1");
    let dir = tempfile::tempdir().unwrap();
    let data = dir.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    std::fs::write(data.join("tree.json"), TREE).unwrap();
    let mut d = Daemon::start(dir.path()).await;

    let r = d.call("GET", "/api/v1/tree", None, None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "aibd", "reason: not_imported {}", r.body);
    assert_eq!(r.json["nodes"].as_array().unwrap().len(), 2);
    assert_eq!(r.json["nodes"][1]["title"], "工作台");
    assert_eq!(r.json["nodes"][1]["owner"], "519d0", "reason: extra_lost");

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let ev = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "tree.imported")
        .cloned()
        .unwrap_or_else(|| panic!("reason: no_import_event {}", r.body));
    assert_eq!(ev["payload"]["imported"], 2);
    assert_eq!(ev["payload"]["dropped"], 1);
    assert_eq!(ev["payload"]["dropped_ids"], serde_json::json!(["N-ws"]));

    // assignments name the table's nodes while aibd holds the tree
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let page = d.page(true).await;
    let add = serde_json::json!({"type": "assign_add", "node_id": "N-ws", "seat_id": seat,
                                 "role": "author"})
    .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&add)).await;
    assert_eq!(r.code, 200, "reason: assign_reads_the_file {}", r.body);
    // a node only the table has (the file cannot be edited now: that lets go, L2): an
    // assignment that read the file would not find it
    let db = aib_store::WorkspaceDb::open(&data.join(aib_store::WORKSPACE_FILE)).unwrap();
    assert!(db
        .add_node(aib_store::NodeRow {
            node_id: "N-table".into(),
            parent_id: Some("V".into()),
            kind: "item".into(),
            title: "只在表裡".into(),
            station: None,
            head: None,
            need: None,
            ord: 9,
            rev: 1,
            updated: "2026-11-19T00:00:00Z".into(),
            extra: "{}".into(),
        })
        .unwrap());
    let add = serde_json::json!({"type": "assign_add", "node_id": "N-table", "seat_id": seat,
                                 "role": "author"})
    .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&add)).await;
    assert_eq!(r.code, 200, "reason: assign_reads_the_file {}", r.body);

    // N-workspace-c4 A: while aibd holds the tree, the marker tells aib-plan to write through it
    let mark = data.join("tree.json.aibd");
    assert!(mark.exists(), "reason: no_marker");
    // 5d33a L2: a write aibd did not make (an old aib-plan, a hand edit) lets go of the tree:
    // the file is the tree again for this run, the marker goes, and an event says so
    std::fs::write(data.join("tree.json"), r#"{"nodes": []}"#).unwrap();
    let r = d.call("GET", "/api/v1/tree", None, None).await;
    assert_eq!(
        r.json["source"], "file",
        "reason: outside_write_kept {}",
        r.body
    );
    assert_eq!(r.json["nodes"], serde_json::json!([]));
    assert!(!mark.exists(), "reason: marker_left");
    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        r.json["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "tree.outside_write"),
        "reason: no_outside_write_event {}",
        r.body
    );

    // the flag off is the way back: the file is the tree again
    std::env::remove_var("AIBD_TREE_IN_DB");
    let r = d.call("GET", "/api/v1/tree", None, None).await;
    assert_eq!(r.json["source"], "file", "reason: no_way_back {}", r.body);
    assert_eq!(r.json["nodes"], serde_json::json!([]));
    d.shutdown().await;
}
