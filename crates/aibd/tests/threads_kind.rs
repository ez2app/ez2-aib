#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-ws-thread-cat (SPEC §17-1 / -2) end to end. Its own process: aibd keeps some state once
//! per process (the assignments' file among it), so a second aibd started in tests/threads.rs
//! made a tree item's thread read another aibd's assignments (receipt a9136-rjs-ef2a40e0f).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

/// TC1 (§17-1 / -2): a thread opened with a kind keeps it; the list keeps one kind and its
/// counts are what this reader may read (a seat: only its own threads); an unknown kind is
/// refused; a post cannot carry doc / source (CHECKLIST-doc-link D2).
#[tokio::test(flavor = "multi_thread")]
async fn threads_have_a_kind_and_the_list_counts_each() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let cred = d.page(true).await;
    let open = |title: &str, kind: Option<&str>, members: Value| {
        let mut b = json!({"type": "thread_open", "title": title, "members": members});
        if let Some(k) = kind {
            b["category"] = json!(k);
        }
        b
    };
    let r = act(
        &d,
        &cred,
        open(
            "總結",
            Some("summary"),
            json!([{"seat": a, "role": "author"}]),
        ),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(r.json["thread"]["category"], "summary", "{}", r.body);
    let summary = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let r = act(&d, &cred, open("交接", Some("handoff"), json!([]))).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let r = act(&d, &cred, open("閒聊", None, json!([]))).await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(r.json["thread"]["category"], Value::Null);
    let r = act(&d, &cred, open("錯", Some("chat"), json!([]))).await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["code"], "category_unknown");

    let get = |q: &str| format!("/api/v1/threads{q}");
    let r = d
        .call("GET", &get("?category=summary"), Some(&cred), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let ts = r.json["threads"].as_array().cloned().unwrap_or_default();
    assert_eq!(ts.len(), 1, "reason: kind_not_kept {}", r.body);
    assert_eq!(ts[0]["thread_id"], summary.as_str());
    let c = &r.json["counts"];
    assert_eq!(
        (
            c["all"].as_u64(),
            c["summary"].as_u64(),
            c["handoff"].as_u64(),
            c["none"].as_u64(),
            c["council"].as_u64()
        ),
        (Some(3), Some(1), Some(1), Some(1), Some(0)),
        "{}",
        r.body
    );
    let r = d
        .call("GET", &get("?category=none"), Some(&cred), None)
        .await;
    assert_eq!(
        r.json["threads"].as_array().map(Vec::len),
        Some(1),
        "{}",
        r.body
    );
    let r = d
        .call("GET", &get("?category=chat"), Some(&cred), None)
        .await;
    assert_eq!(r.code, 400, "{}", r.body);

    // a seat counts only the threads it is in
    let tok = aibd::seat_token::tokens().issue(&a).unwrap();
    let r = act(&d, &tok, json!({"type": "thread_list"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["counts"]["all"], 1,
        "reason: seat_counts_threads_it_is_not_in {}",
        r.body
    );
    assert_eq!(r.json["counts"]["summary"], 1, "{}", r.body);

    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": summary, "body": "x",
               "doc": {"root": "pending", "file": "a.md"}}),
    )
    .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["code"], "field_not_allowed");
    d.shutdown().await;
}
