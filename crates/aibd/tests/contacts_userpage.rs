#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! S89-talk-2b L1 (5d33a 6dd6a686 預讀): while a user folder page serves `/`, even a credential
//! paired on the built-in page is held to what a user page may do — the tab's sessionStorage
//! is the user page's to read (S107 坑 2) — so it gets no notes and writes none; with the page
//! gone, the built-in credential reads its notes again. Its own test binary: the user folder is
//! set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn a_built_in_credential_reads_no_note_while_a_user_page_serves() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "seat")).await;
    let key = format!("seat:{seat}");
    let built_in = d.page(true).await;
    let note = serde_json::json!({"type":"contact_set","key":key,"expected_revision":0,
        "note":"只給內建頁的備註"})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&note))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);

    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let page = web.join("index.html");
    std::fs::write(&page, "<p>mine</p>").unwrap();
    let r = d
        .call("GET", "/api/v1/contacts", Some(&built_in), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        !r.body.contains("只給內建頁的備註") && !r.body.contains("\"note\""),
        "reason: note_while_user_page_serves {}",
        r.body
    );
    let again = serde_json::json!({"type":"contact_set","key":key,"expected_revision":1,
        "note":"改"})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&again))
        .await;
    assert_eq!(
        r.code, 403,
        "reason: note_written_while_user_page_serves {}",
        r.body
    );

    std::fs::remove_file(&page).unwrap();
    let r = d
        .call("GET", "/api/v1/contacts", Some(&built_in), None)
        .await;
    assert!(r.body.contains("只給內建頁的備註"), "{}", r.body);
    d.shutdown().await;
}
