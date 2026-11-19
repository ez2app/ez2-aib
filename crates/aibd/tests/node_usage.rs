#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-workspace 第四波 (SPEC §15, 7ebca): `GET /api/v1/nodes/{id}/usage` gives one node's tokens per
//! seat per hour from the board's own `<data>/usage-cache.json`, as the card window draws them; no
//! cache is `source: "none"`; a seat token cannot read it. Its own binary: the data folder is set
//! once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

/// The shape of the board's usage-cache.json (11-18 12:29), cut down.
const CACHE: &str = r#"{"v": 2, "files": {}, "rows": {
  "7b81e|498507|-": [27206, 446522, 9577454, 46],
  "7b81e|498507|N-ws": [10741, 19257, 5881800, 24],
  "ebb36|498508|N-ws": [2901, 3329, 824058, 6]
}, "cum": {"N-ws": {
  "7b81e": [10741, 19257, 5881800, 24, 600, 1794625200, 1794628000],
  "ebb36": [2901, 3329, 824058, 6, 72, 1794628800, 1794629000]
}}}"#;

#[tokio::test(flavor = "multi_thread")]
async fn a_node_s_tokens_per_seat_per_hour_come_from_the_board_s_count() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let page = d.page(true).await;

    let r = d
        .call("GET", "/api/v1/nodes/N-ws/usage", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "none", "reason: no_cache_not_none");
    assert_eq!(r.json["seats"], serde_json::json!([]));

    std::fs::write(dir.path().join("data").join("usage-cache.json"), CACHE).unwrap();
    let r = d
        .call("GET", "/api/v1/nodes/N-ws/usage", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "file");
    assert_eq!(r.json["node_id"], "N-ws");
    assert_eq!(r.json["unit_s"], 3600);
    let s = r.json["seats"].as_array().unwrap();
    assert_eq!(s.len(), 2, "reason: other_card_read {}", r.body);
    assert_eq!(s[0]["seat_id"], "7b81e");
    assert_eq!(
        s[0]["hours"],
        serde_json::json!([[498_507u64 * 3600, 10741, 19257, 5_881_800, 24]])
    );
    assert_eq!(s[1]["total"]["active_s"], 72);
    // 043dd 0eed7 L2 / L1: the first and last times by the page's names; no tree: the subtree is
    // the node itself
    assert_eq!(s[0]["total"]["first_at"], 1_794_625_200u64);
    assert_eq!(s[0]["total"]["last_at"], 1_794_628_000u64);
    assert_eq!(
        r.json["subtree"]["spent"], 36_228,
        "reason: subtree_missing {}",
        r.body
    );
    assert_eq!(r.json["subtree"]["nodes"], 1);

    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = d
        .call("GET", "/api/v1/nodes/N-ws/usage", Some(&tok), None)
        .await;
    assert_eq!(r.code, 403, "reason: seat_reads_node_usage {}", r.body);
    d.shutdown().await;
}
