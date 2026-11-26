#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! §7-2 寫回人格 at the door (ebab8 11-23 09:11 常設規則: a built-in-page gate has a test that
//! sends the request): the waiting proposals are listed and said no to by the built-in page in
//! this pairing only — not by a seat's token, not while a user folder page serves `/`. Its own
//! test binary: the user folder is set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: &Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn proposals_are_listed_and_denied_by_the_built_in_page_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let page = d.page(true).await;
    let list = json!({"type": "persona_proposals"});
    let deny = json!({"type": "persona_propose_deny", "ids": ["pp_none"]});

    // the built-in page: an empty list, and a no to one that is not there
    let r = act(&d, &page, &list).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(r.json["proposals"].is_array(), "{}", r.body);
    let r = act(&d, &page, &deny).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["results"][0]["code"], "request_not_found",
        "{}",
        r.body
    );

    // a seat's token reaches neither
    for b in [&list, &deny] {
        let r = act(&d, &tok, b).await;
        assert!(
            r.code >= 400 && r.json["proposals"].is_null() && r.json["results"].is_null(),
            "reason: a_seat_reached_the_page_act {}",
            r.body
        );
    }

    // while a user folder page serves `/`, the same credential is refused
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let user_page = web.join("index.html");
    std::fs::write(&user_page, "<p>mine</p>").unwrap();
    for b in [&list, &deny] {
        let r = act(&d, &page, b).await;
        assert_eq!(
            (r.code, r.json["code"].as_str()),
            (403, Some("persona_built_in_page_only")),
            "reason: user_page_reached_the_page_act {}",
            r.body
        );
    }
    std::fs::remove_file(&user_page).unwrap();
    let r = act(&d, &page, &list).await;
    assert_eq!(r.code, 200, "{}", r.body);
    d.shutdown().await;
}
