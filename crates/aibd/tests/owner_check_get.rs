#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! ebab8 11-26 03:28: the page reads this machine's owner-check class from a GET (the
//! "check again" button is the same GET). Only a paired page reads it; it changes nothing.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn a_paired_page_reads_the_owner_check_class_and_nothing_else_does() {
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;

    let r = d
        .call("GET", "/api/v1/owner-check", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    // not Windows here: the platform, no class, no words
    assert_ne!(r.json["platform"], "windows", "{}", r.body);
    assert!(
        r.json["class"].is_null(),
        "reason: class_off_windows {}",
        r.body
    );
    assert_eq!(r.json["class_is_final"], false, "{}", r.body);

    // (the harness sends its start-up token when given none: a wrong one is the case here)
    let r = d
        .call("GET", "/api/v1/owner-check", Some("not-a-token"), None)
        .await;
    assert_eq!(r.code, 401, "reason: read_with_a_wrong_token {}", r.body);
    let r = d
        .call("POST", "/api/v1/owner-check", Some(&page), Some("{}"))
        .await;
    assert_eq!(r.code, 405, "reason: written_to {}", r.body);

    d.shutdown().await;
}
