#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls on a plain sleep, like the shared harness
#![cfg(unix)]

//! 5d33a PREREAD-seatmsg L1: a queued seat-to-seat message is judged again when it is written
//! (at the seat's start or the end of its turn), not only when it was sent. Queued to a dgrs
//! seat while this machine's dgrs input switch was on, it is not typed after the switch went
//! off: the row fails and leaves the inbox, nothing reaches the seat. Its own process: the
//! switch is process-wide.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{read_log, seat_dir, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn a_queued_message_is_judged_again_when_it_is_written() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    aibd::seat_input::set_dgrs_input(true);
    let (a, _) = d.open("dgrs", &seat_dir(dir.path(), "a")).await;
    let (_, b_session) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    for i in 0..2 {
        let _ = d
            .open("safe", &seat_dir(dir.path(), &format!("run{i}")))
            .await;
    }
    // the fifth seat waits for a slot
    let e_dir = seat_dir(dir.path(), "e");
    let body = serde_json::json!({
        "persona_uid": "un0eba", "provider": "claude", "mode": "dgrs",
        "cwd": e_dir.to_str().unwrap(),
    })
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.json["session"]["state"], "pending", "{}", r.body);
    let e = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let ta = aibd::seat_token::tokens().issue(&a).unwrap();
    let send = serde_json::json!({"type": "seat_send", "to": e, "text": "開了再做"}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&ta), Some(&send)).await;
    assert_eq!(r.code, 202, "{}", r.body);

    // the switch goes off, then a slot frees and the seat starts
    aibd::seat_input::set_dgrs_input(false);
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{b_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{}", r.body);
    let te = aibd::seat_token::tokens().issue(&e).unwrap();
    let inbox = serde_json::json!({"type": "message_inbox"}).to_string();
    let mut left = usize::MAX;
    for _ in 0..100 {
        let r = d.call("POST", "/api/v1/act", Some(&te), Some(&inbox)).await;
        let rows = r.json["messages"].as_array().cloned().unwrap_or_default();
        left = rows.iter().filter(|m| m["status"] == "queued").count();
        if left == 0 {
            assert!(
                rows.is_empty(),
                "reason: written_to_dgrs_after_switch_off {}",
                r.body
            );
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    assert_eq!(left, 0, "reason: never_redelivered");
    assert!(
        read_log(&e_dir).is_empty(),
        "reason: typed_to_dgrs_after_switch_off"
    );
    aibd::seat_token::tokens().revoke(&a);
    aibd::seat_token::tokens().revoke(&e);
    d.shutdown().await;
}
