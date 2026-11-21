#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on a plain sleep and a timeout
#![cfg(unix)]

//! N-ws-dismiss-persist / N-ws-event-pin (519d0 11-19 07:22): a card taken off (「暫不處理」)
//! stays off after aibd restarts, and a card hung on a plan item (`slot_pin`) keeps its item;
//! only the built-in page pins, only to an item on the tree.
//!
//! Its own binary: it sets the state root for the process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};
use std::path::Path;

const TREE: &str = r#"{"nodes": [{"id": "N-workspace", "title": "工作區", "status": "開發中"}]}"#;

fn open_item(root: &Path) {
    let ctx = aib_ledger::events::Ctx::for_run(root, Some("519d0"));
    let args: Vec<String> = [
        "open",
        "--item",
        "A1",
        "--item-kind",
        "決定",
        "--text",
        "要不要先上用量條",
        "--rec",
        "先上",
        "--card",
        "N-workspace",
    ]
    .iter()
    .map(|a| (*a).to_owned())
    .collect();
    let (rc, out) = aib_drive::andy_open::run(&args, root, &ctx, aib_drive::act::now_ms());
    assert_eq!(rc, 0, "{out}");
}

fn find(v: &Value) -> Option<Value> {
    v["slots"]
        .as_array()?
        .iter()
        .find(|c| c["card_id"] == "andy:A1")
        .cloned()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_dismissal_and_a_pin_outlive_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("state-root");
    std::env::set_var("EZ2_AIB_STATE_ROOT", &root);
    open_item(&root);
    let data = dir.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    std::fs::write(data.join("tree.json"), TREE).unwrap();

    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;
    let pin = |node: Value| {
        json!({"type": "slot_pin", "card_id": "andy:A1", "node_id": node}).to_string()
    };

    // only the built-in page pins, only to an item on the tree, with the fields named
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&user),
            Some(&pin(json!("N-workspace"))),
        )
        .await;
    assert_eq!(r.code, 403, "reason: user_page_pins {}", r.body);
    assert_eq!(r.json["code"], "slot_built_in_page_only");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&pin(json!("N-nope"))),
        )
        .await;
    assert_eq!(r.code, 404, "reason: pinned_off_tree {}", r.body);
    assert_eq!(r.json["code"], "node_not_found");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&json!({"type": "slot_pin", "card_id": "andy:A1", "node_id": 3}).to_string()),
        )
        .await;
    assert_eq!(r.code, 400, "reason: bad_node_taken {}", r.body);
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&pin(json!("N-workspace"))),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["journaled"], true, "{}", r.body);
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    let c = find(&r.json).unwrap_or_else(|| panic!("reason: no_card {}", r.body));
    assert_eq!(c["pin"], "N-workspace", "reason: pin_not_shown {c}");

    // take it off, then restart
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&json!({"type": "slot_dismiss", "card_id": "andy:A1"}).to_string()),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    d.shutdown().await;

    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        find(&r.json).is_none(),
        "reason: dismissed_came_back {}",
        r.body
    );
    let r = d
        .call("GET", "/api/v1/slots?dismissed=1", Some(&built_in), None)
        .await;
    let c = find(&r.json).unwrap_or_else(|| panic!("reason: not_in_dismissed {}", r.body));
    assert_eq!(c["pin"], "N-workspace", "reason: pin_lost {c}");

    // put back and take the pin off: both stay so after another restart
    for b in [
        json!({"type": "slot_restore", "card_id": "andy:A1"}),
        json!({"type": "slot_pin", "card_id": "andy:A1", "node_id": null}),
    ] {
        let r = d
            .call("POST", "/api/v1/act", Some(&built_in), Some(&b.to_string()))
            .await;
        assert_eq!(r.code, 200, "{}", r.body);
    }
    d.shutdown().await;
    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    let c = find(&r.json).unwrap_or_else(|| panic!("reason: restored_lost {}", r.body));
    assert!(c.get("pin").is_none(), "reason: unpin_lost {c}");
    d.shutdown().await;
}
