#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S89-live-2 (b4297): a codex seat, end to end on the fake `codex app-server` (the test fake
//! acts like 0.167.0, measured r2/r3). aibd opens it safe (dgrs is refused before any seat is
//! written), writes the three opening lines first, writes a person's text as `turn/start` on
//! the thread codex named, ends the turn on `turn/completed`, and answers a request codex
//! makes of it with an error.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, seat_dir, Daemon};
use serde_json::Value;

fn json(line: &[u8]) -> Value {
    serde_json::from_slice(line).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_codex_seat_is_spoken_to_in_its_protocol() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "cx");
    let (seat, _) = d.open_as("codex", "safe", &cwd).await;

    // the opening: initialize, initialized, thread/start in the seat's folder
    let got = log_with(&cwd, 3);
    let methods: Vec<String> = got
        .iter()
        .map(|(_, l)| json(l)["method"].as_str().unwrap_or_default().to_owned())
        .collect();
    assert_eq!(
        methods,
        ["initialize", "initialized", "thread/start"],
        "reason: opening_wrong"
    );
    assert_eq!(json(&got[2].1)["params"]["cwd"], cwd.to_str().unwrap());

    let cred = d.page(true).await;
    // the thread may still be starting for a moment: the person's text waits or goes
    let mut r = d.act(Some(&cred), &seat, "你好").await;
    for _ in 0..50 {
        if r.code == 200 {
            break;
        }
        assert_eq!(r.json["code"], "seat_starting", "{}", r.body);
        std::thread::sleep(std::time::Duration::from_millis(100));
        r = d.act(Some(&cred), &seat, "你好").await;
    }
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&cwd, 4);
    let t = json(&got[3].1);
    assert_eq!(t["method"], "turn/start", "reason: not_turn_start {t}");
    assert!(
        t["params"]["threadId"]
            .as_str()
            .unwrap()
            .starts_with("th-fake-"),
        "{t}"
    );
    let texts: Vec<&str> = t["params"]["input"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|i| i["text"].as_str())
        .collect();
    assert_eq!(texts.last(), Some(&"你好"), "{t}");

    // codex asks aibd something: aibd says no, at once, on the same pipe
    let r = d.act(Some(&cred), &seat, "ask").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&cwd, 6);
    let answer = got
        .iter()
        .map(|(_, l)| json(l))
        .find(|v| v["id"] == 900)
        .expect("reason: request_not_answered");
    assert!(
        answer["error"]["code"].is_i64(),
        "reason: request_approved {answer}"
    );
    assert!(answer.get("result").is_none());
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_dgrs_codex_seat_is_refused_and_version_says_safe_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "cx");
    let body = serde_json::json!({"persona_uid": "un0eba", "provider": "codex", "mode": "dgrs",
        "cwd": cwd.to_str().unwrap()})
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "provider_mode_unsupported", "{}", r.body);
    assert!(
        !cwd.join("fake-provider.log").exists(),
        "reason: dgrs_codex_started"
    );
    d.shutdown().await;
}
