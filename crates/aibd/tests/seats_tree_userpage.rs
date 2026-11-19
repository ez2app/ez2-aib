#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-web-worktree r3 (b4297): a user folder page reads no seat's tree (403
//! `tree_built_in_page_only`), before aibd even looks for the tree. Its own binary: who serves
//! `/` is held process-wide.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn a_user_folder_page_reads_no_seats_tree() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    std::fs::write(web.join("index.html"), "<p>mine</p>").unwrap();
    let user = d.page_form(true, "&to=user").await;
    for tail in ["tree?path=", "tree/log", "tree/diff"] {
        let r = d
            .call(
                "GET",
                &format!("/api/v1/seats/seat_0000000000000000/{tail}"),
                Some(&user),
                None,
            )
            .await;
        assert_eq!(
            r.code, 403,
            "reason: user_page_reads_tree {tail} {}",
            r.body
        );
        assert_eq!(r.json["reason"], "tree_built_in_page_only", "{}", r.body);
    }
    d.shutdown().await;
}
