#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! §17-3 end to end (5d33a PREREAD 4ad11ad7 M1): an imported summary shows in the page's
//! 總結 kind and its document reads through GET /api/v1/doc.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn an_imported_summary_lists_under_its_kind_and_its_document_reads() {
    let dir = tempfile::tempdir().unwrap();
    let pending = dir.path().join("pending");
    std::fs::create_dir(&pending).unwrap();
    std::env::set_var("AIBD_PLAN_PENDING", &pending);
    std::fs::write(
        pending.join("SUMMARY-1d-043dd-1113.md"),
        "# 一天總結\n今天做完三件事",
    )
    .unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let db = aib_store::MessageDb::open(&dir.path().join("data").join(aib_store::MESSAGES_FILE))
        .unwrap();
    let c = aibd::import::import_from(&pending, "SUMMARY-", "summary", "總結(匯入)", Some(&db))
        .unwrap();
    assert_eq!(c.new, 1, "{c:?}");
    let t = aibd::import::thread_of("summary");

    let r = d
        .call("GET", "/api/v1/threads?category=summary", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(r.body.contains(&t), "reason: import_not_listed {}", r.body);
    assert_eq!(r.json["counts"]["summary"], 1, "{}", r.body);

    let (posts, _) = db.posts_page(&t, None, 10).unwrap();
    assert_eq!(posts.len(), 1, "reason: post_not_in_thread");
    let mid = posts[0].message_id.clone();

    let q = format!("/api/v1/doc?thread_id={t}&message_id={mid}&n=0");
    let r = d.call("GET", &q, Some(&page), None).await;
    assert_eq!(r.code, 200, "reason: doc_not_read {}", r.body);
    assert!(r.body.contains("今天做完三件事"), "{}", r.body);
    d.shutdown().await;
}
