#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! G4 end to end (DESIGN-trusted-g1g4 §1): a seat's `gh_view` act runs aibd's own gh, with the
//! argv, repo, folder and environment aibd chooses, only with the switch on and only for a seat
//! trusted now; the body names no repo and no flag; one ask every 10 s; the ask is journaled.
//! gh here is a script that prints what it was given.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};
use std::os::unix::fs::PermissionsExt;

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_trusted_seat_reads_a_pr_through_aibds_own_gh() {
    let dir = tempfile::tempdir().unwrap();
    let gh = dir.path().join("fake-gh");
    std::fs::write(
        &gh,
        "#!/bin/sh\nprintf '{\"argv\":\"%s\",\"cwd\":\"%s\",\"gh_repo\":\"%s\",\"gh_host\":\"%s\",\"prompt\":\"%s\"}' \"$*\" \"$(pwd -P)\" \"${GH_REPO-unset}\" \"${GH_HOST-unset}\" \"$GH_PROMPT_DISABLED\"\n",
    )
    .unwrap();
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).unwrap();
    // aibd's own environment: the program, the repo, and two names gh must not be handed
    std::env::set_var("AIBD_GH_BIN", &gh);
    std::env::set_var("AIB_DRIVE_REPO", "o-test/n-test");
    std::env::set_var("GH_REPO", "evil/other");
    std::env::set_var("GH_HOST", "example.invalid");

    let mut d = Daemon::start(dir.path()).await;
    let data = dir.path().join("data");
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (other, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let tok_other = aibd::seat_token::tokens().issue(&other).unwrap();
    let ask = json!({"type": "gh_view", "what": "pr", "number": "7"});

    // the switch is off: nothing runs
    let r = act(&d, &tok, ask.clone()).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("trusted_seats_off")),
        "{}",
        r.body
    );
    std::fs::write(data.join("bridge.json"), r#"{"trusted_seats": true}"#).unwrap();

    // on, but the seat is not trusted
    let r = act(&d, &tok, ask.clone()).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("seat_not_trusted")),
        "{}",
        r.body
    );
    std::fs::create_dir_all(data.join("trusted")).unwrap();
    let trust = |until: u64| {
        std::fs::write(
            data.join("trusted").join("seats.json"),
            json!({"seats": {seat.clone(): until}}).to_string(),
        )
        .unwrap();
    };
    trust(now() + 600);

    // trusted: the body still names no repo, no flag, no other subcommand
    for (body, code) in [
        (
            json!({"type": "gh_view", "what": "pr", "number": "7", "repo": "evil/other"}),
            "gh_view_fields",
        ),
        (
            json!({"type": "gh_view", "what": "pr", "number": "7 --web"}),
            "gh_view_number",
        ),
        (
            json!({"type": "gh_view", "what": "pr", "number": "-R"}),
            "gh_view_number",
        ),
        (
            json!({"type": "gh_view", "what": "api", "number": "7"}),
            "gh_view_what",
        ),
    ] {
        let r = act(&d, &tok, body.clone()).await;
        assert_eq!(
            (r.code, r.json["code"].as_str()),
            (400, Some(code)),
            "reason: took_{body} {}",
            r.body
        );
    }
    // another seat's trust is its own
    let r = act(&d, &tok_other, ask.clone()).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("seat_not_trusted")),
        "reason: trust_shared {}",
        r.body
    );
    // a page's credential is not a seat's
    let r = act(&d, &page, ask.clone()).await;
    assert_ne!(r.code, 200, "reason: page_ran_gh {}", r.body);

    // the ask: aibd's argv, repo, folder and environment
    let r = act(&d, &tok, ask.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        (
            r.json["done"].as_bool(),
            r.json["rc"].as_i64(),
            r.json["cut"].as_bool()
        ),
        (Some(true), Some(0), Some(false)),
        "{}",
        r.body
    );
    let got = &r.json["json"];
    assert_eq!(
        got["argv"],
        "pr view 7 -R o-test/n-test --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,mergeable,reviewDecision,labels,url",
        "reason: argv_not_aibds {}", r.body
    );
    assert_eq!(
        got["gh_repo"], "unset",
        "reason: GH_REPO_handed_on {}",
        r.body
    );
    assert_eq!(
        got["gh_host"], "unset",
        "reason: GH_HOST_handed_on {}",
        r.body
    );
    assert_eq!(got["prompt"], "1", "reason: prompt_not_off {}", r.body);
    let cwd = std::fs::canonicalize(got["cwd"].as_str().unwrap()).unwrap();
    assert_eq!(
        cwd,
        std::fs::canonicalize(&data).unwrap(),
        "reason: ran_outside_the_data_folder"
    );

    // one ask per seat every 10 s
    let r = act(
        &d,
        &tok,
        json!({"type": "gh_view", "what": "checks", "number": 7}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (429, Some("gh_view_seat_rate")),
        "{}",
        r.body
    );

    // the trust ended: read at this ask, nothing kept
    trust(now() - 1);
    let r = act(&d, &tok, ask.clone()).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("seat_not_trusted")),
        "reason: kept_an_ended_trust {}",
        r.body
    );

    // journaled once, with ids only
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let asked: Vec<&Value> = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "seat.gh_view")
        .collect();
    assert_eq!(
        asked.len(),
        1,
        "reason: asks_journaled_{} {}",
        asked.len(),
        ev.body
    );
    assert_eq!(
        asked[0]["payload"],
        json!({"seat": seat, "what": "pr", "number": "7", "rc": 0}),
        "reason: event_payload"
    );
    d.shutdown().await;
}
