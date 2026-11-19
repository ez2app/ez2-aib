#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! 043dd 4b09556d1 / 0eed7 L7: the page's 重開 (act seat_restart) starts a stopped seat again,
//! the way a rejoin does; a seat that runs is 409 seat_running.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;

fn starts(cwd: &std::path::Path) -> usize {
    std::fs::read_to_string(cwd.join("fake-provider-args.log"))
        .unwrap_or_default()
        .lines()
        .count()
}

#[tokio::test(flavor = "multi_thread")]
async fn the_page_restarts_a_stopped_seat_once() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let wb = seat_dir(dir.path(), "b");
    let mut d = Daemon::start(dir.path()).await;
    let (b, sb) = d.open("safe", &wb).await;
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sb}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let before = starts(&wb);
    let cred = d.page(true).await;
    let body = json!({"type": "seat_restart", "seat_id": b, "idem_key": "restart-1"}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&body))
        .await;
    assert_eq!(r.code, 200, "reason: stopped_seat_not_restarted {}", r.body);
    let sid = r.json["session_id"].as_str().unwrap().to_owned();
    assert_ne!(sid, sb);
    let r = d
        .call("GET", &format!("/api/v1/seats/{b}/sessions"), None, None)
        .await;
    let list = r.json["sessions"].as_array().cloned().unwrap_or_default();
    assert!(
        list.iter()
            .any(|s| s["id"] == sid.as_str() && s["state"] == "running"),
        "{}",
        r.body
    );
    // pressed again while it runs: nothing new started
    let body = json!({"type": "seat_restart", "seat_id": b, "idem_key": "restart-2"}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&body))
        .await;
    assert_eq!(r.code, 409, "reason: running_seat_restarted {}", r.body);
    assert_eq!(r.json["code"], "seat_running");
    // a seat that is not there
    let body =
        json!({"type": "seat_restart", "seat_id": "nope", "idem_key": "restart-3"}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&body))
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert!(starts(&wb) <= before + 1, "reason: started_twice");
    d.shutdown().await;
}

/// N-ws-ask-codex (a4d66545f revive half): a codex seat started again gets aibd's talk server
/// as codex settings, as at open; never claude's --mcp-config, which codex does not take.
#[tokio::test(flavor = "multi_thread")]
async fn a_restarted_codex_seat_keeps_the_talk_tools() {
    let dir = tempfile::tempdir().unwrap();
    let wc = seat_dir(dir.path(), "cx");
    let mut d = Daemon::start(dir.path()).await;
    let (c, sc) = d.open_as("codex", "safe", &wc).await;
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sc}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let cred = d.page(true).await;
    let body = json!({"type": "seat_restart", "seat_id": c, "idem_key": "cx-restart"}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&body))
        .await;
    assert_eq!(r.code, 200, "reason: codex_not_restarted {}", r.body);
    let mut lines = Vec::new();
    for _ in 0..50 {
        lines = std::fs::read_to_string(wc.join("fake-provider-args.log"))
            .unwrap_or_default()
            .lines()
            .map(str::to_owned)
            .collect();
        if lines.len() >= 2 {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    assert!(lines.len() >= 2, "reason: no_second_start {lines:?}");
    let again = lines.last().unwrap();
    assert!(
        again.contains("mcp_servers.aibd.command=") && again.contains("ask_person"),
        "reason: restart_without_talk_tools {again}"
    );
    assert!(
        !again.contains("--mcp-config"),
        "reason: claude_flag_to_codex {again}"
    );
    d.shutdown().await;
}
