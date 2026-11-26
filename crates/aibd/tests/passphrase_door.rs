#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! S11-3 密語備援 at the door (ebab8 11-23 09:11 常設規則: a built-in-page gate has a test that
//! sends the request): the passphrase state is read by the built-in page in this pairing only —
//! not by a seat's token, not while a user folder page serves `/` — and never carries the
//! record. Its own test binary: the user folder is set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: &Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn the_passphrase_state_is_read_by_the_built_in_page_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let page = d.page(true).await;
    let state = json!({"type": "passphrase_state"});

    // the built-in page: the state, nothing set yet, no record fields
    let r = act(&d, &page, &state).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        (r.json["v"].as_u64(), r.json["set"].as_bool()),
        (Some(1), Some(false)),
        "{}",
        r.body
    );
    for k in ["salt", "out", "record_sha", "phrase"] {
        assert!(
            r.json.get(k).is_none(),
            "reason: record_field_on_the_page {k} {}",
            r.body
        );
    }

    // a seat's token does not reach it
    let r = act(&d, &tok, &state).await;
    assert!(
        r.code >= 400 && r.json["v"].is_null(),
        "reason: a_seat_reached_the_page_act {}",
        r.body
    );

    // while a user folder page serves `/`, the same credential is refused
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let user_page = web.join("index.html");
    std::fs::write(&user_page, "<p>mine</p>").unwrap();
    let r = act(&d, &page, &state).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("passphrase_built_in_page_only")),
        "reason: user_page_reached_the_page_act {}",
        r.body
    );
    std::fs::remove_file(&user_page).unwrap();
    let r = act(&d, &page, &state).await;
    assert_eq!(r.code, 200, "{}", r.body);
    d.shutdown().await;
}
