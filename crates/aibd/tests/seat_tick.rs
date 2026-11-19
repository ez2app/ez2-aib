#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! ORDER-webonly-today 7 (a9136 11-20): the built-in page sets, lists and clears a seat's
//! timed sentence (act seat_tick_set / seat_tick_list / seat_tick_clear).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn the_page_sets_lists_and_clears_a_seats_timed_sentence() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let wa = seat_dir(dir.path(), "a");
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &wa).await;
    let cred = d.page(true).await;
    let act = |v: serde_json::Value| v.to_string();
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(
                json!({"type": "seat_tick_set", "seat_id": a, "every_min": 20, "text": "繼續"}),
            )),
        )
        .await;
    assert_eq!(r.code, 200, "reason: tick_not_set {}", r.body);
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(json!({"type": "seat_tick_list"}))),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let ticks = r.json["ticks"].as_array().cloned().unwrap_or_default();
    assert_eq!(ticks.len(), 1, "reason: tick_not_listed {}", r.body);
    assert_eq!(ticks[0]["seat_id"], a.as_str());
    assert_eq!(ticks[0]["every_min"], 20);
    // too short a period, and a seat that is not there
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(
                json!({"type": "seat_tick_set", "seat_id": a, "every_min": 1, "text": "x"}),
            )),
        )
        .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["code"], "every_min_out_of_range");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(
                json!({"type": "seat_tick_set", "seat_id": "nope", "every_min": 20, "text": "x"}),
            )),
        )
        .await;
    assert_eq!(r.code, 404, "reason: unknown_seat_set {}", r.body);
    // cleared once; a second clear finds nothing
    let clear = act(json!({"type": "seat_tick_clear", "seat_id": a}));
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["ticks"].as_array().map(Vec::len), Some(0));
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear))
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    d.shutdown().await;
}
