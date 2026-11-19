#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-ws-doc-link (SPEC §17 point 4; 5d33a CHECKLIST-doc-link D1 / D2): GET /api/v1/doc reads
//! only a post's registered document, for the built-in page and the thread's readers; a post
//! cannot register one itself, and the request names no path.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn only_the_built_in_page_reads_a_posts_document_and_never_by_path() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("AIBD_PLAN_PENDING", dir.path().join("pending"));
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let open = json!({"type": "thread_open", "title": "總結", "members": [{"seat": seat, "role": "author"}]});
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&open.to_string()))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();

    // a post cannot register a document (D2)
    let send = json!({"type": "thread_send", "thread_id": t, "body": "看這份",
                      "doc": {"file": "../../etc/hosts", "title": "x"}});
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&send.to_string()))
        .await;
    assert_eq!(r.code, 400, "reason: post_registered_doc {}", r.body);
    assert_eq!(r.json["code"], "unknown_field");
    let send = json!({"type": "thread_send", "thread_id": t, "body": "看這份"});
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&send.to_string()))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let mid = r.json["post"]["message_id"]
        .as_str()
        .unwrap_or_default()
        .to_string();

    // the request names no path (D2)
    let q = format!("/api/v1/doc?thread_id={t}&message_id={mid}&path=x.md");
    let r = d.call("GET", &q, Some(&page), None).await;
    assert_eq!(r.code, 400, "reason: path_taken {}", r.body);
    assert_eq!(r.json["code"], "unknown_field");
    // a post with no document has none to give
    let q = format!("/api/v1/doc?thread_id={t}&message_id={mid}");
    let r = d.call("GET", &q, Some(&page), None).await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert_eq!(r.json["code"], "doc_not_found");
    assert!(
        !r.body.contains("pending"),
        "reason: path_in_error {}",
        r.body
    );

    // a user folder page, the start-up token and a seat token do not read it (D1)
    let user = d.page_form(true, "&to=user").await;
    let r = d.call("GET", &q, Some(&user), None).await;
    assert_eq!(r.code, 403, "reason: user_page_reads_doc {}", r.body);
    assert_eq!(r.json["code"], "doc_built_in_page_only");
    let r = d.call("GET", &q, None, None).await;
    assert_ne!(r.code, 200, "reason: no_token_reads_doc {}", r.body);
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = d.call("GET", &q, Some(&tok), None).await;
    assert_ne!(r.code, 200, "reason: seat_token_reads_doc {}", r.body);
    d.shutdown().await;
}
