#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-ws-userpage (519d0 11-18 19:55 裁, from 5d33a PREREAD-search N1): a user folder page reads
//! a shared thread's posts as who sent them, when, what kind and how long (with the sha256), never
//! the text, the same as the 待人決策 list and the card notes; the built-in page gets the text.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};

fn posts(v: &Value) -> Vec<Value> {
    v["posts"].as_array().cloned().unwrap_or_default()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_user_page_reads_a_shared_thread_without_the_text() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(r#"{"type":"thread_open","title":"共用"}"#),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let send = json!({"type": "thread_send", "thread_id": t, "body": "只給內建頁的原文 kiwi"});
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&send.to_string()))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);

    let path = format!("/api/v1/threads/{t}");
    let r = d.call("GET", &path, Some(&page), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        posts(&r.json)[0]["body"],
        "只給內建頁的原文 kiwi",
        "{}",
        r.body
    );

    let r = d.call("GET", &path, Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        !r.body.contains("kiwi"),
        "reason: text_to_user_page {}",
        r.body
    );
    let p = &posts(&r.json)[0];
    assert!(p.get("body").is_none(), "{}", r.body);
    for k in ["from_seat", "created", "kind", "len", "body_sha256"] {
        assert!(
            p.get(k).is_some_and(|v| !v.is_null()),
            "reason: no_{k} {}",
            r.body
        );
    }

    d.shutdown().await;
}
