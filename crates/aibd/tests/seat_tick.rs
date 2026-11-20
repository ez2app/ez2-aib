#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! ORDER-webonly-today 7 (a9136 11-20): the built-in page sets, lists and clears a seat's
//! timed sentence (act seat_tick_set / seat_tick_list / seat_tick_clear). Setting and letting
//! a paused list go again are the owner's fingerprint (DESIGN-drive-in-aibd r2 §4); this
//! daemon has none to ask, so neither writes.

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
    let why = |r: &harness::Reply| r.json["reason"].as_str().map(str::to_owned);
    let file = dir.path().join("data").join("seat_ticks.json");
    let set = json!({"type": "seat_tick_set", "seat_id": a, "every_min": 20, "text": "繼續"});
    // setting is the owner's: no fingerprint can be asked here, so nothing is written
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&act(set.clone())))
        .await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_unavailable")),
        "reason: set_without_fingerprint {}",
        r.body
    );
    // a ticket nobody approved sets nothing either
    let mut with_ticket = set.clone();
    with_ticket["ticket"] = json!("ot_made_up");
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&act(with_ticket)))
        .await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_not_approved")),
        "reason: a_made_up_ticket_set {}",
        r.body
    );
    assert!(!file.exists(), "reason: written_without_fingerprint");
    // an entry from before the fingerprint, and one whose text was edited after its approval
    let sha = aibd::seat_tick::text_sha256("繼續");
    std::fs::write(
        &file,
        json!({"ticks": [
            {"seat_id": a, "every_min": 20, "text": "繼續"},
            {"seat_id": "seat_edited", "every_min": 30, "text": "改過的字",
             "approved": true, "text_sha256": sha},
        ]})
        .to_string(),
    )
    .unwrap();
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
    assert_eq!(ticks.len(), 2, "reason: tick_not_listed {}", r.body);
    assert_eq!(ticks[0]["seat_id"], a.as_str());
    assert_eq!(ticks[0]["every_min"], 20);
    assert_eq!(
        (&ticks[0]["approved"], &ticks[0]["changed_since_approved"]),
        (&json!(false), &json!(false)),
        "reason: old_entry_not_marked {}",
        r.body
    );
    assert_eq!(
        (&ticks[1]["approved"], &ticks[1]["changed_since_approved"]),
        (&json!(true), &json!(true)),
        "reason: change_not_listed {}",
        r.body
    );
    // 急停 needs no one, keeps the list and says paused
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(json!({"type": "seat_tick_pause", "on": true}))),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["paused"], true, "reason: not_paused {}", r.body);
    // going again is the owner's: with no fingerprint to ask the list stays paused
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(json!({"type": "seat_tick_pause", "on": false}))),
        )
        .await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_unavailable")),
        "reason: resumed_without_fingerprint {}",
        r.body
    );
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(
                json!({"type": "seat_tick_pause", "on": false, "ticket": "ot_made_up"}),
            )),
        )
        .await;
    assert_eq!(r.code, 403, "reason: a_made_up_ticket_resumed {}", r.body);
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&act(json!({"type": "seat_tick_list"}))),
        )
        .await;
    assert_eq!(r.json["paused"], true, "reason: pause_not_kept {}", r.body);
    assert_eq!(r.json["ticks"].as_array().map(Vec::len), Some(2));
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
    assert_eq!(r.json["ticks"].as_array().map(Vec::len), Some(1));
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear))
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    d.shutdown().await;
}
