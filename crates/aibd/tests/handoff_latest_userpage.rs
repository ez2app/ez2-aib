#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! S6 (f163c REVIEW-s6code pre-1ff6980c C1; ebab8 11-23 09:11 standing rule: a built-in-page
//! gate has an integration test and a knife). While a user folder page serves `/`, neither its
//! own pairing nor a built-in credential lists a seat's newest handoff (403 page_only, nothing
//! named); with the page gone, the built-in page gets the names. Its own test binary: the
//! pending folder and the user folder are set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn only_the_built_in_page_lists_a_seats_newest_handoff() {
    let dir = tempfile::tempdir().unwrap();
    let pending = dir.path().join("pending");
    std::fs::create_dir_all(&pending).unwrap();
    std::fs::write(pending.join("HANDOFF-abcde-1123-0900.md"), "x").unwrap();
    std::env::set_var("AIBD_PLAN_PENDING", &pending);
    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let ask = serde_json::json!({"type": "handoff_latest", "code": "abcde"}).to_string();

    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let page = web.join("index.html");
    std::fs::write(&page, "<p>mine</p>").unwrap();
    let user = d.page_form(true, "&to=user").await;
    for (who, cred) in [
        ("user_page", &user),
        ("built_in_while_user_page", &built_in),
    ] {
        let r = d.call("POST", "/api/v1/act", Some(cred), Some(&ask)).await;
        assert_eq!(r.code, 403, "reason: handoff_listed_{who} {}", r.body);
        assert!(r.body.contains("page_only"), "{}", r.body);
        assert!(
            !r.body.contains("HANDOFF-abcde"),
            "reason: name_shown_{who} {}",
            r.body
        );
    }

    std::fs::remove_file(&page).unwrap();
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&ask))
        .await;
    assert_eq!(r.code, 200, "reason: built_in_page_refused {}", r.body);
    assert_eq!(
        r.json["handoff"]["file"], "HANDOFF-abcde-1123-0900.md",
        "{}",
        r.body
    );
    d.shutdown().await;
}
