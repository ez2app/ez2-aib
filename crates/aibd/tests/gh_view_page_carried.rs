#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! 1a93b DESIGN-page-gh-view r1 (5d33a PREREAD-pageghview 0/0/0/0): the built-in page looks at
//! a PR through aibd's own gh with the seat's argv, repo and limits, only with page_gh_view on,
//! never as a user folder page; a pairing carried over an update may look (read only) and still
//! may do nothing else. What gh printed is not journaled.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn code(r: &Reply) -> (u16, Option<&str>) {
    (r.code, r.json["code"].as_str())
}

fn put_carry(dir: &Path, cred: &str) {
    let running = aibd::pair_carry::exe().unwrap().1;
    let list = [aibd::pairing::CarriedCred {
        digest: Sha256::digest(cred.as_bytes()).into(),
        user_page: false,
        left: 3600,
    }];
    let doc = aibd::pair_carry::document(
        &list,
        &"0".repeat(64),
        &running,
        aibd::pair_carry::now_unix() - 5,
    );
    let path = aibd::pair_carry::path(&dir.join("data"));
    aibd::platform::write_private_file(&path, doc.to_string().as_bytes()).unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn the_page_reads_a_pr_only_with_its_switch_and_a_carried_pairing_only_reads() {
    let dir = tempfile::tempdir().unwrap();
    let gh = dir.path().join("fake-gh");
    std::fs::write(
        &gh,
        "#!/bin/sh\nprintf '{\"argv\":\"%s\",\"said\":\"PRINTED-BY-GH\"}' \"$*\"\n",
    )
    .unwrap();
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).unwrap();
    std::env::set_var("AIBD_GH_BIN", &gh);
    std::env::set_var("AIB_DRIVE_REPO", "o-test/n-test");

    let mut d = Daemon::start(dir.path()).await;
    let data = dir.path().join("data");
    let page = d.page(true).await;
    let later = d.page(true).await; // carried over the update below
    let ask = json!({"type": "gh_view", "what": "pr", "number": "7"});

    // the switch is off; trusted_seats on does not open it
    std::fs::write(data.join("bridge.json"), r#"{"trusted_seats": true}"#).unwrap();
    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(
        code(&r),
        (403, Some("page_gh_view_off")),
        "reason: asked_without_switch {}",
        r.body
    );
    std::fs::write(data.join("bridge.json"), r#"{"page_gh_view": true}"#).unwrap();

    // a user folder page's pairing: never
    let user = d.page_form(true, "&to=user").await;
    let r = act(&d, &user, ask.clone()).await;
    assert_eq!(
        code(&r),
        (403, Some("gh_view_user_page")),
        "reason: user_page_asked {}",
        r.body
    );

    // the body names no repo, no flag
    let r = act(
        &d,
        &page,
        json!({"type": "gh_view", "what": "pr", "number": "7", "repo": "evil/x"}),
    )
    .await;
    assert_eq!(
        code(&r),
        (400, Some("gh_view_fields")),
        "reason: page_named_repo {}",
        r.body
    );
    let r = act(
        &d,
        &page,
        json!({"type": "gh_view", "what": "pr", "number": "-R"}),
    )
    .await;
    assert_eq!(code(&r), (400, Some("gh_view_number")), "{}", r.body);

    // the ask: aibd's argv and repo
    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(r.code, 200, "reason: page_not_answered {}", r.body);
    let argv = r.json["json"]["argv"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    assert!(argv.starts_with("pr view 7 "), "reason: argv_shape {argv}");
    assert!(
        argv.contains("-R o-test/n-test"),
        "reason: repo_not_aibds {argv}"
    );
    // again within 10 s: refused
    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(
        code(&r),
        (429, Some("gh_view_seat_rate")),
        "reason: page_asked_twice {}",
        r.body
    );

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        r.body.contains("page.gh_view"),
        "reason: not_journaled {}",
        r.body
    );
    assert!(
        !r.body.contains("PRINTED-BY-GH"),
        "reason: output_journaled"
    );
    d.shutdown().await;

    // carried over an update: it may look, and still may do nothing else
    put_carry(dir.path(), &later);
    let mut d = Daemon::start(dir.path()).await;
    let st = d
        .call("GET", "/api/v1/pair/status", Some(&later), None)
        .await;
    assert_eq!(st.json["carried"], true, "{}", st.body);
    let r = act(&d, &later, ask.clone()).await;
    assert_eq!(r.code, 200, "reason: carried_pair_cannot_read {}", r.body);
    let r = act(
        &d,
        &later,
        json!({"type": "seat_restart", "seat_id": "seat_x", "idem_key": "k1"}),
    )
    .await;
    assert_eq!(
        code(&r),
        (403, Some("carried_pair_read_and_safe_only")),
        "reason: carried_pair_restarts {}",
        r.body
    );
    d.shutdown().await;
}
