#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! 5d33a PREREAD 2feffc7b M1: a dgrs seat reading a thread (thread_view, what its aibd mcp
//! thread_view calls) gets the person's text, and a safe seat's post only as {len, sha256}:
//! the same line as the catch-up (safe → dgrs is never delivered). Its own process: the dgrs
//! switch is on here.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn a_dgrs_seat_reads_no_safe_seats_text_in_a_thread() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (g, _) = d.open("dgrs", &seat_dir(dir.path(), "g")).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "title": "安全與不擋",
               "members": [{"seat": a, "role": "author"}, {"seat": g, "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let tok = |s: &str| aibd::seat_token::tokens().issue(s).unwrap();
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "真人說"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let r = act(
        &d,
        &tok(&a),
        json!({"type": "thread_send", "thread_id": t, "body": "安全席說"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);

    let r = act(&d, &tok(&g), json!({"type": "thread_view", "thread_id": t})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let posts = r.json["posts"].as_array().cloned().unwrap_or_default();
    let by = |who: &str| {
        posts
            .iter()
            .find(|p| p["from_seat"].as_str().is_some_and(|f| f.starts_with(who)))
            .cloned()
            .unwrap()
    };
    assert_eq!(by("human:")["body"], "真人說", "{}", r.body);
    let safe = by(&format!("seat:{a}"));
    assert!(
        safe.get("body").is_none(),
        "reason: safe_text_to_dgrs {}",
        r.body
    );
    assert_eq!(safe["len"], "安全席說".len(), "{}", r.body);
    // the safe seat itself reads everything
    let r = act(&d, &tok(&a), json!({"type": "thread_view", "thread_id": t})).await;
    assert!(
        r.body.contains("真人說") && r.body.contains("安全席說"),
        "{}",
        r.body
    );
    d.shutdown().await;
}
