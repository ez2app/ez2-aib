#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-workspace second wave (ORDER-workspace-519d0-1118 §第二波, 7b81e): node × seat × role through
//! `POST /api/v1/act` and the two reads, as the workspace page calls them; with 5d33a's
//! wave-2 checklist A1 (a seat token never assigns) and A2 (a user folder page never assigns).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;

const TREE: &str = r#"{"nodes": [
  {"id": "V", "parent": null, "title": "總綱"},
  {"id": "G7", "parent": "V", "title": "G7"},
  {"id": "N-ws", "parent": "G7", "title": "工作台"}
]}"#;

fn add(node: &str, seat: &str, role: &str) -> String {
    json!({"type": "assign_add", "node_id": node, "seat_id": seat, "role": role}).to_string()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    std::fs::write(dir.path().join("data").join("tree.json"), TREE).unwrap();
    let page = d.page(true).await;

    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&add("G7", &seat, "reviewer")),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["added"], true);
    assert_eq!(r.json["journaled"], true);
    assert!(r.json["assignment"]["by"]
        .as_str()
        .unwrap()
        .starts_with("human:"));
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&add("G7", &seat, "reviewer")),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["added"], false, "reason: same_row_twice");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&add("N-ws", &seat, "author")),
        )
        .await;
    assert_eq!(r.json["added"], true, "{}", r.body);

    // a node sees its own rows and the rows of every node above it
    let r = d
        .call("GET", "/api/v1/nodes/N-ws/assignments", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["tree"], "file");
    assert_eq!(r.json["direct"].as_array().unwrap().len(), 1);
    assert_eq!(r.json["direct"][0]["role"], "author");
    assert_eq!(r.json["inherited"][0]["from_node"], "G7", "{}", r.body);
    assert_eq!(r.json["inherited"][0]["role"], "reviewer");
    let r = d
        .call(
            "GET",
            &format!("/api/v1/assignments?seat={seat}"),
            Some(&page),
            None,
        )
        .await;
    assert_eq!(
        r.json["assignments"].as_array().unwrap().len(),
        2,
        "{}",
        r.body
    );

    // the event says who and what, and is there for the page's poll
    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let added: Vec<&serde_json::Value> = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "assignment.added")
        .collect();
    assert_eq!(added.len(), 2, "reason: one_event_per_new_row {}", r.body);
    assert_eq!(added[0]["payload"]["node_id"], "G7");

    // no such node while there is a tree; no such seat; a role outside the six
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&add("G8", &seat, "author")),
        )
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert_eq!(r.json["code"], "node_not_found");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&add("G7", "seat_nobody", "author")),
        )
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert_eq!(r.json["code"], "seat_not_found");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&add("G7", &seat, "owner")),
        )
        .await;
    assert_eq!(r.code, 400, "{}", r.body);

    // A2: a user folder page does not assign
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&user),
            Some(&add("V", &seat, "observer")),
        )
        .await;
    assert_eq!(r.code, 403, "reason: user_page_assigns {}", r.body);
    assert_eq!(r.json["code"], "assign_built_in_page_only");

    // A1: a seat token does not assign
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&tok),
            Some(&add("V", &seat, "observer")),
        )
        .await;
    assert_ne!(r.code, 200, "reason: seat_token_assigns {}", r.body);
    let r = d
        .call("GET", "/api/v1/assignments?node=V", Some(&page), None)
        .await;
    assert_eq!(
        r.json["assignments"],
        json!([]),
        "reason: a_refused_write_left_a_row {}",
        r.body
    );

    let rm = json!({"type": "assign_remove", "node_id": "G7", "seat_id": seat, "role": "reviewer"})
        .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&rm)).await;
    assert_eq!(r.json["removed"], true, "{}", r.body);
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&rm)).await;
    assert_eq!(r.json["removed"], false, "{}", r.body);
    let r = d
        .call("GET", "/api/v1/nodes/N-ws/assignments", Some(&page), None)
        .await;
    assert_eq!(r.json["inherited"], json!([]), "{}", r.body);
    d.shutdown().await;
}
