#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on plain timeouts and sleeps (as the other tests that use it)
#![cfg(unix)]

//! 3e53b REVIEW-grok 4733597f N1 (08de5 11-25): a grok seat opened end to end on a grok whose
//! version aibd did not measure (the fake, as `grok`, says 1.0.71; aibd's words were read off
//! 1.0.70) does not start. The open answers with the refusal and its reason, and grok is never
//! run past `--version`: no `inspect`, no turn. What the open keeps is the seat's row (as for
//! any session that did not start: `session_error`, no `session`), not a running grok.
//! Unix only: on Windows the grok seat's prompt file is UNVERIFIED.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::Value;

#[tokio::test(flavor = "multi_thread")]
async fn a_grok_of_a_version_not_measured_is_refused_and_never_run() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start_grok(dir.path()).await;
    let cwd = seat_dir(dir.path(), "gk");
    let body = serde_json::json!({
        "persona_uid": "un0eba", "provider": "grok", "mode": "safe",
        "cwd": cwd.to_str().unwrap(),
    })
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert!(
        r.json["session"].is_null(),
        "reason: grok_started {}",
        r.body
    );
    assert_eq!(
        r.json["session_error"]["code"], "program_not_allowed",
        "reason: grok_not_refused {}",
        r.body
    );
    let why = r.json["session_error"]["message"]
        .as_str()
        .unwrap_or_default();
    assert!(
        why.contains("(grok_version)") && why.contains("1.0.71"),
        "reason: refusal_without_its_reason {why}"
    );

    // grok was asked its version, and nothing else
    let calls: Vec<Value> = std::fs::read_to_string(cwd.join("fake-grok-calls.log"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    assert!(!calls.is_empty(), "reason: version_not_asked");
    for c in &calls {
        assert_eq!(
            *c,
            serde_json::json!(["--version"]),
            "reason: grok_run_past_version {c}"
        );
    }
    d.shutdown().await;
}
