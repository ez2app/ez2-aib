#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace wave 2 (b4297; 5d33a CHECKLIST B1 / B3): with this machine's switch on, the
//! built-in page opens a dgrs seat, with the argv aibd builds for it (no more, no less); a
//! brand that has no dgrs mode (codex, safe only) is still refused. Its own binary: the
//! switch is read once when aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn with_the_switch_on_the_page_opens_a_dgrs_seat() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(
                &json!({"type": "seat_open", "provider": "claude", "mode": "dgrs",
                        "persona_uid": "un0eba", "idem_key": "d-1"})
                .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(r.json["seat"]["mode"], "dgrs", "{}", r.body);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    // E4 (5d33a CHECKLIST-wave3): its own event, with the page that opened it
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let dg: Vec<&serde_json::Value> = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "seat.dgrs_opened")
        .collect();
    assert_eq!(dg.len(), 1, "reason: dgrs_open_not_journaled {}", ev.body);
    assert_eq!(dg[0]["payload"]["seat_id"], seat.as_str());
    assert_eq!(dg[0]["payload"]["opened_by"]["page"], "built_in");
    // B3: the page's open goes through the same argv builder as the start-up token's (one
    // code path, seats::open_checked); what the page may send has no field that reaches it
    // (seats_page_open: cwd / cabin / argv refused)
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(
                &json!({"type": "seat_open", "provider": "codex", "mode": "dgrs",
                        "persona_uid": "un0eba", "idem_key": "d-2"})
                .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "provider_mode_unsupported", "{}", r.body);
    d.shutdown().await;
}
