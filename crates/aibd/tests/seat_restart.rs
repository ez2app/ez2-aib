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
        // only whole lines: the fake may be half way through writing the second (7ebca
        // all-45430b4fc: a last line cut after `-c` read as the restart without its tools)
        let log = std::fs::read_to_string(wc.join("fake-provider-args.log")).unwrap_or_default();
        lines = log
            .split_inclusive('\n')
            .filter(|l| l.ends_with('\n'))
            .map(|l| l.trim_end().to_owned())
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

/// 043dd 11-20 (0eed7 VERIFY-tree L1): the page's 封存 (act seat_archive) archives a stopped
/// seat as DELETE /api/v1/seats/{id} does; a seat that runs is 409 seat_busy and stays; the
/// seat list says whose folder a seat shares (folder_owner).
#[tokio::test(flavor = "multi_thread")]
async fn the_page_archives_a_stopped_seat_and_not_a_running_one() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let (a, sa) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let cred = d.page(true).await;
    let act = |seat: &str, key: &str| {
        json!({"type": "seat_archive", "seat_id": seat, "idem_key": key}).to_string()
    };
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&act(&b, "arch-b")))
        .await;
    assert_eq!(r.code, 409, "reason: running_seat_archived {}", r.body);
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sa}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&act(&a, "arch-a")))
        .await;
    assert_eq!(r.code, 200, "reason: stopped_seat_not_archived {}", r.body);
    // the seat JSON's state is the store's (ARCHIVED), as DELETE /seats/{id} answers
    assert_eq!(r.json["state"], "ARCHIVED", "{}", r.body);
    // the default list leaves an archived seat out; ?all=1 lists it (S89-state)
    let shown = d.call("GET", "/api/v1/seats", None, None).await;
    let has = |v: &serde_json::Value, id: &str| {
        v["seats"]
            .as_array()
            .is_some_and(|l| l.iter().any(|s| s["seat_id"] == id))
    };
    assert!(
        !has(&shown.json, &a),
        "reason: archived_seat_listed {}",
        shown.body
    );
    assert!(
        has(&shown.json, &b),
        "reason: running_seat_not_listed {}",
        shown.body
    );
    let seats = d.call("GET", "/api/v1/seats?all=1", None, None).await;
    let list = seats.json["seats"].as_array().cloned().unwrap_or_default();
    let row = |id: &str| list.iter().find(|s| s["seat_id"] == id).cloned().unwrap();
    assert_eq!(row(&a)["state"], "ARCHIVED");
    assert_ne!(
        row(&b)["state"],
        "ARCHIVED",
        "reason: running_seat_archived"
    );
    // a seat in its own folder names no owner
    assert!(row(&b)["folder_owner"].is_null(), "{}", seats.body);
    d.shutdown().await;
}

/// 519d0 自測 r3 M3: a seat whose session waits for a slot says so in the seat list (its place
/// and the running limit), so the page can say 「排隊中，第 N 位」; a running seat says nothing.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_waiting_for_a_slot_says_its_place_and_the_limit() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let mut first = String::new();
    for i in 0..4 {
        let (s, _) = d
            .open("safe", &seat_dir(dir.path(), &format!("r{i}")))
            .await;
        if i == 0 {
            first = s;
        }
    }
    let body = json!({"persona_uid": "un0eba", "provider": "claude", "mode": "safe",
                      "cwd": seat_dir(dir.path(), "w5").to_str().unwrap()})
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(r.json["session"]["state"], "pending", "{}", r.body);
    let fifth = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let seats = d.call("GET", "/api/v1/seats", None, None).await;
    let list = seats.json["seats"].as_array().cloned().unwrap_or_default();
    let row = |id: &str| list.iter().find(|s| s["seat_id"] == id).cloned().unwrap();
    assert_eq!(
        row(&fifth)["queue"],
        json!({"position": 1, "limit": 4}),
        "reason: waiting_seat_not_told {}",
        seats.body
    );
    assert!(
        row(&first)["queue"].is_null(),
        "reason: running_seat_queued"
    );
    // and the page can say it before opening one: the limit is in /version
    let v = d.call("GET", "/api/v1/version", None, None).await;
    assert_eq!(
        v.json["max_sessions"], 4,
        "reason: no_max_sessions {}",
        v.body
    );
    d.shutdown().await;
}
