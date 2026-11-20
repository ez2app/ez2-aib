#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! The built-in page reads a pull request through aibd's own gh (ORDER-today 11-21): only with
//! its own switch (`page_gh_view`, which is not `trusted_seats` and opens nothing for a seat),
//! the same argv, repo, folder and environment a trusted seat's ask gets, the body names no
//! repo and no flag, one ask every 10 s, journaled as `page.gh_view`. gh here is a script that
//! prints what it was given.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};
use std::os::unix::fs::PermissionsExt;

async fn act(d: &Daemon, cred: Option<&str>, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", cred, Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn the_built_in_page_reads_a_pr_through_aibds_own_gh() {
    let dir = tempfile::tempdir().unwrap();
    let gh = dir.path().join("fake-gh");
    std::fs::write(
        &gh,
        "#!/bin/sh\nprintf '{\"argv\":\"%s\",\"gh_repo\":\"%s\",\"gh_host\":\"%s\"}' \"$*\" \"${GH_REPO-unset}\" \"${GH_HOST-unset}\"\n",
    )
    .unwrap();
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).unwrap();
    std::env::set_var("AIBD_GH_BIN", &gh);
    std::env::set_var("AIB_DRIVE_REPO", "o-test/n-test");
    std::env::set_var("GH_REPO", "evil/other");
    std::env::set_var("GH_HOST", "example.invalid");

    let mut d = Daemon::start(dir.path()).await;
    let data = dir.path().join("data");
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let ask = json!({"type": "gh_view", "what": "pr", "number": "7"});
    let code = |r: &Reply| r.json["code"].as_str().map(str::to_owned);

    // off by default, and the seats' switch is not the page's
    let r = act(&d, Some(&page), ask.clone()).await;
    assert_eq!(
        (r.code, code(&r).as_deref()),
        (403, Some("page_gh_view_off")),
        "{}",
        r.body
    );
    std::fs::write(data.join("bridge.json"), r#"{"trusted_seats": true}"#).unwrap();
    let r = act(&d, Some(&page), ask.clone()).await;
    assert_eq!(
        (r.code, code(&r).as_deref()),
        (403, Some("page_gh_view_off")),
        "reason: trusted_seats_opens_the_page {}",
        r.body
    );

    // on for the page only: a seat is where it was
    std::fs::write(data.join("bridge.json"), r#"{"page_gh_view": true}"#).unwrap();
    let r = act(&d, Some(&tok), ask.clone()).await;
    assert_eq!(
        (r.code, code(&r).as_deref()),
        (403, Some("trusted_seats_off")),
        "reason: the_page_switch_opens_the_seats {}",
        r.body
    );
    // no credential: not the page
    let r = act(&d, None, ask.clone()).await;
    assert!(
        r.code == 401 || r.code == 403,
        "reason: asked_without_the_page {} {}",
        r.code,
        r.body
    );

    // the body names no repo, no flag, nothing but a plain number (refused asks are not counted)
    for (body, why) in [
        (
            json!({"type": "gh_view", "what": "pr", "number": "7", "repo": "evil/other"}),
            "gh_view_fields",
        ),
        (
            json!({"type": "gh_view", "what": "pr", "number": "--web"}),
            "gh_view_number",
        ),
        (
            json!({"type": "gh_view", "what": "api", "number": "7"}),
            "gh_view_what",
        ),
    ] {
        let r = act(&d, Some(&page), body.clone()).await;
        assert_eq!(
            (r.code, code(&r).as_deref()),
            (400, Some(why)),
            "reason: took_{body} {}",
            r.body
        );
    }

    // the ask: aibd's argv and repo, none of the environment that would turn gh elsewhere
    let r = act(&d, Some(&page), ask.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        (
            r.json["done"].as_bool(),
            r.json["rc"].as_i64(),
            r.json["what"].as_str(),
            r.json["number"].as_str()
        ),
        (Some(true), Some(0), Some("pr"), Some("7")),
        "{}",
        r.body
    );
    let got = &r.json["json"];
    assert!(
        got["argv"]
            .as_str()
            .is_some_and(|a| a.starts_with("pr view 7 -R o-test/n-test --json ")),
        "reason: argv_not_aibds {}",
        r.body
    );
    assert_eq!(
        (got["gh_repo"].as_str(), got["gh_host"].as_str()),
        (Some("unset"), Some("unset")),
        "reason: gh_was_pointed_elsewhere {}",
        r.body
    );

    // one asker: the page's next ask inside the gap is refused
    let r = act(&d, Some(&page), ask.clone()).await;
    assert_eq!(
        (r.code, code(&r).as_deref()),
        (429, Some("gh_view_seat_rate")),
        "reason: the_page_is_not_held_to_the_gap {}",
        r.body
    );

    // journaled once, as the page's: no seat is named
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let events = ev.json["events"].as_array().unwrap();
    let asked: Vec<&Value> = events
        .iter()
        .filter(|e| e["type"] == "page.gh_view")
        .collect();
    assert_eq!(asked.len(), 1, "reason: asks_journaled {}", ev.body);
    let p = &asked[0]["payload"];
    assert_eq!(
        (p["what"].as_str(), p["number"].as_str(), p["rc"].as_i64()),
        (Some("pr"), Some("7"), Some(0)),
        "reason: event_payload {p}"
    );
    let hex16 = |v: &Value| {
        v.as_str()
            .is_some_and(|b| b.len() == 16 && b.bytes().all(|c| c.is_ascii_hexdigit()))
    };
    assert!(
        hex16(&p["pairing"]) && p.get("seat").is_none(),
        "reason: the_page_is_named_as_a_seat {p}"
    );
    assert!(
        !events.iter().any(|e| e["type"] == "seat.gh_view"),
        "reason: journaled_as_a_seat"
    );
    d.shutdown().await;
}
