#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace wave 2 (b4297; 5d33a CHECKLIST-workspace-wave2 B): the built-in page adds a seat
//! with `act {type: "seat_open"}`. This binary keeps this machine's dgrs switch off (read once
//! when aibd starts; the switch-on case is `seats_page_open_dgrs`). A user folder page is
//! `seats_page_open_userpage`: who serves `/` is process-wide, so it has its own binary.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn events_of(d: &Daemon, id: &str) -> Vec<Value> {
    let ev = d
        .call(
            "GET",
            &format!("/api/v1/events?cursor=0&aggregate_id={id}"),
            None,
            None,
        )
        .await;
    ev.json["events"].as_array().cloned().unwrap_or_default()
}

async fn seat_count(d: &Daemon) -> usize {
    let r = d.call("GET", "/api/v1/seats", None, None).await;
    r.json["seats"].as_array().map_or(0, Vec::len)
}

/// B1 / B4: a confirmed built-in page opens a safe seat; the seat runs in its own folder, the
/// event names the page, the same key replays the same seat.
#[tokio::test(flavor = "multi_thread")]
async fn the_built_in_page_opens_a_safe_seat_once_per_key() {
    std::env::remove_var("AIBD_SEAT_INPUT_DGRS");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let open = json!({"type": "seat_open", "provider": "claude", "mode": "safe",
                      "persona_uid": "un0eba", "idem_key": "add-1"});
    let r = act(&d, &cred, open.clone()).await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(r.json["session"]["state"], "running", "{}", r.body);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let again = act(&d, &cred, open).await;
    // a replay answers as the first did (201, as POST /seats/open), with the same seat
    assert_eq!(again.code, 201, "{}", again.body);
    assert_eq!(
        again.json["seat"]["seat_id"],
        seat.as_str(),
        "reason: key_not_replayed {}",
        again.body
    );
    assert_eq!(seat_count(&d).await, 1, "reason: opened_twice");
    let evs = events_of(&d, &seat).await;
    let opened = evs.iter().find(|e| e["type"] == "seat.opened").unwrap();
    assert_eq!(
        opened["payload"]["opened_by"]["page"], "built_in",
        "reason: opener_not_recorded {opened}"
    );
    assert!(opened["payload"]["opened_by"]["pairing"].is_string());
    d.shutdown().await;
}

/// B1 / B2 / B5: what a page cannot open is refused before any seat is written.
#[tokio::test(flavor = "multi_thread")]
async fn a_page_cannot_open_what_it_may_not() {
    std::env::remove_var("AIBD_SEAT_INPUT_DGRS");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let base = |provider: &str, mode: &str, key: &str| {
        json!({"type": "seat_open", "provider": provider, "mode": mode,
               "persona_uid": "un0eba", "idem_key": key})
    };
    // the switch is off: no dgrs seat from the page
    let r = act(&d, &cred, base("claude", "dgrs", "k1")).await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "dgrs_needs_switch", "{}", r.body);
    // a mode that is not safe counts as needing it too
    let r = act(&d, &cred, base("claude", "yolo", "k2")).await;
    assert_eq!(r.json["code"], "dgrs_needs_switch", "{}", r.body);
    // a brand aibd cannot start (agy; grok starts since 08de5 11-25): refused by aibd, not
    // only greyed on the page
    let r = act(&d, &cred, base("agy", "safe", "k3")).await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "provider_unsupported", "{}", r.body);
    // the page gives no folder, cabin or arguments
    for (k, v) in [
        ("cwd", json!("/")),
        ("cabin", json!("dab9")),
        ("argv", json!(["--dangerously-skip-permissions"])),
    ] {
        let mut b = base("claude", "safe", "k4");
        b[k] = v;
        let r = act(&d, &cred, b).await;
        assert_eq!(r.code, 400, "{k}: {}", r.body);
        assert_eq!(r.json["reason"], "body", "{k}: {}", r.body);
    }
    // no key, no seat
    let mut b = base("claude", "safe", "x");
    b.as_object_mut().unwrap().remove("idem_key");
    assert_eq!(act(&d, &cred, b).await.code, 400);
    // a key outside the key rule
    let r = act(&d, &cred, base("claude", "safe", "")).await;
    assert_eq!(r.json["reason"], "key_missing", "{}", r.body);
    let r = act(&d, &cred, base("claude", "safe", "空白 鍵")).await;
    assert_eq!(r.json["reason"], "key_missing", "{}", r.body);
    assert_eq!(seat_count(&d).await, 0, "reason: refused_open_wrote_a_seat");
    d.shutdown().await;
}

/// 5d33a CHECK-preview-work-seat r2 1: with_seat is only for a new work seat; a safe seat
/// that names one is refused and nothing is opened (the shared-folder case is in
/// `seats_page_open_dgrs`, where this machine lets the page open work seats).
#[tokio::test(flavor = "multi_thread")]
async fn a_safe_seat_naming_with_seat_is_refused() {
    std::env::remove_var("AIBD_SEAT_INPUT_DGRS");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "seat_open", "provider": "claude", "mode": "safe",
               "persona_uid": "un0eba", "idem_key": "a"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let a = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let before = seat_count(&d).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "seat_open", "provider": "claude", "mode": "safe",
               "persona_uid": "un0eba", "idem_key": "b", "with_seat": a}),
    )
    .await;
    assert_eq!(r.code, 400, "reason: safe_with_seat_taken {}", r.body);
    assert_eq!(r.json["reason"], "with_seat", "{}", r.body);
    assert_eq!(seat_count(&d).await, before, "reason: refused_open_wrote");
    d.shutdown().await;
}
