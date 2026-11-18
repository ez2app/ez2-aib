#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! The 32 KiB post limit in its own process: one aibd per test binary, as aibd keeps the
//! assignments' file once per process (see tests/threads_assign.rs).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

/// TH4: a text over 32 KiB is refused and nothing is written.
#[tokio::test(flavor = "multi_thread")]
async fn a_post_over_thirty_two_kib_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "title": "free", "members": [{"seat": a, "role": "observer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let big = "字".repeat(11_000);
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": big}),
    )
    .await;
    assert_eq!(r.code, 413, "{}", r.body);
    let v = d
        .call("GET", &format!("/api/v1/threads/{t}"), Some(&cred), None)
        .await;
    assert_eq!(v.json["posts"], json!([]), "{}", v.body);
    d.shutdown().await;
}
