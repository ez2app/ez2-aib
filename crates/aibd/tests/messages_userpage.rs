#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! 5d33a 2a 預讀 L1: once seats write to the person, what waits on them ("等你決定") is
//! closed on the built-in page only. While a user folder page serves `/`, even a built-in
//! credential is refused message_done and message_read (403) before any row is looked up; with the page gone,
//! the same call reaches the library again (an unknown id is 404). Its own test binary: the
//! user folder is set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn a_user_page_cannot_close_what_waits_on_the_person() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let done = serde_json::json!({"type":"message_done","message_id":"q_0000000000000000",
        "expected_revision":1,"why":"x"})
    .to_string();
    let read = serde_json::json!({"type":"message_read","thread_id":"human:x~seat:y",
        "message_id":"q_0000000000000000"})
    .to_string();

    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let page = web.join("index.html");
    std::fs::write(&page, "<p>mine</p>").unwrap();
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&done))
        .await;
    assert_eq!(
        r.code, 403,
        "reason: done_while_user_page_serves {}",
        r.body
    );
    assert!(r.body.contains("done_built_in_page_only"), "{}", r.body);
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&read))
        .await;
    assert_eq!(
        r.code, 403,
        "reason: read_while_user_page_serves {}",
        r.body
    );
    assert!(r.body.contains("read_built_in_page_only"), "{}", r.body);

    std::fs::remove_file(&page).unwrap();
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&done))
        .await;
    assert_eq!(r.code, 404, "reason: built_in_page_refused {}", r.body);
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&read))
        .await;
    assert_ne!(r.code, 403, "reason: built_in_page_read_refused {}", r.body);
    d.shutdown().await;
}
