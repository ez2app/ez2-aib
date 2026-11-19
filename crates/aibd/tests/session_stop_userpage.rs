#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace session_stop on a user folder page. Its own process: the user folder is set once
//! per process (static_files::web_source), so this cannot share a binary with
//! seat_input_userpage.rs, whose test removes its page and expects the built-in answer.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

/// N-workspace: a user folder page does not stop a seat (`stop_built_in_page_only`), like it
/// does not mark read or close; the seat keeps running and takes the next text.
#[tokio::test(flavor = "multi_thread")]
async fn a_user_page_does_not_stop_a_seat() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    std::fs::write(web.join("index.html"), "<p>mine</p>").unwrap();
    let (seat, session) = d.open("safe", &seat_dir(dir.path(), "safe")).await;
    let user_cred = d.page_form(true, "&to=user").await;
    let body = serde_json::json!({"type": "session_stop", "session_id": session}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&user_cred), Some(&body))
        .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "stop_built_in_page_only");
    assert_eq!(d.act(Some(&user_cred), &seat, "still here").await.code, 200);
    d.shutdown().await;
}
