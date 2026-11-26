#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! A machine with no cards file (a new Windows install, ebab8 11-26 12:12): the seat opened with
//! the built-in persona runs, and after aibd restarts it is started again (f163c PREREAD-builtin
//! -persona 4, 3e53b DECISION-builtin-persona-bounds r1b e).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use std::time::{Duration, Instant};

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_of_the_built_in_persona_opens_and_comes_back_after_aibd_restarts() {
    harness::no_cards();
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let w = seat_dir(dir.path(), "a");
    let mut d = Daemon::start(dir.path()).await;
    let body = serde_json::json!({
        "persona_uid": "aib000", "provider": "claude", "mode": "safe",
        "cwd": w.to_str().unwrap(),
    })
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.code, 201, "reason: builtin_not_opened {}", r.body);
    assert_eq!(r.json["session"]["state"], "running", "{}", r.body);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_owned();
    let all = d.call("GET", "/api/v1/seats", None, None).await;
    assert_eq!(
        all.json["seats"][0]["persona"]["uid"], "aib000",
        "{}",
        all.body
    );
    d.shutdown().await;
    assert!(!dir.path().join("cards.json").exists());

    let mut d = Daemon::start(dir.path()).await;
    let end = Instant::now() + Duration::from_secs(15);
    loop {
        let r = d
            .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None, None)
            .await;
        let list = r.json["sessions"].as_array().cloned().unwrap_or_default();
        if list.iter().any(|s| s["state"] == "running") {
            break;
        }
        assert!(
            Instant::now() < end,
            "reason: builtin_seat_not_revived {}",
            r.body
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    d.shutdown().await;
}
