#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! G3 at the door (DESIGN-pr-open-g3): only the built-in page asks, only with the switch on,
//! with no field for a base, a repository, a label or a draft flag; and with no way to open a
//! pull request on this machine the answer is 503 before any fingerprint is asked.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pull_request_is_asked_by_the_built_in_page_only_and_fixed_by_aibd() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let data = dir.path().join("data");
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let ask = json!({"type": "pr_open", "repo": "demo", "branch": "web/ab12-1120-1",
        "title": "a title", "body": "a body"});
    let why = |r: &Reply| r.json["reason"].as_str().map(str::to_owned);

    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("pr_open_off")),
        "{}",
        r.body
    );
    std::fs::write(data.join("bridge.json"), r#"{"pr_open": true}"#).unwrap();

    // what the page has no field for, and words it may not send
    for (extra, value) in [
        ("base", json!("release")),
        ("slug", json!("evil/repo")),
        ("labels", json!(["ready-merge"])),
        ("draft", json!(false)),
        ("head", json!("main")),
    ] {
        let mut b = ask.clone();
        b[extra] = value;
        let r = act(&d, &page, b).await;
        assert_eq!(
            (r.code, why(&r).as_deref()),
            (400, Some("pr_fields")),
            "reason: took_{extra} {}",
            r.body
        );
    }
    let mut b = ask.clone();
    b["title"] = json!("two\nlines");
    let r = act(&d, &page, b).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (400, Some("pr_title")),
        "{}",
        r.body
    );

    // this build has no client: said before any window, whatever else is asked
    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (503, Some("pr_open_unavailable")),
        "{}",
        r.body
    );
    assert!(
        r.json["ticket"].is_null(),
        "reason: a_window_was_raised {}",
        r.body
    );

    // a seat's token cannot ask
    let r = act(&d, &tok, ask.clone()).await;
    assert!(
        r.code >= 400 && why(&r).as_deref() != Some("pr_open_unavailable"),
        "reason: a_seat_reached_pr_open {}",
        r.body
    );

    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        !ev.json["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "repo.pr_opened"),
        "reason: pr_journaled"
    );
    d.shutdown().await;
}
