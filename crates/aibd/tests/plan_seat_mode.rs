#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! DECISION-seat-writes-7b81e-1120 Q2 (7b81e + Codex, ORDER-webonly-today 6): a safe seat only
//! talks, so POST /api/v1/nodes/plan with its own token is refused before the tree is looked at;
//! a dgrs seat's token gets past that check (here the tree is not in aibd, so it is told that).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn a_safe_seat_cannot_note_the_tree_and_a_dgrs_seat_gets_past_the_check() {
    let dir = tempfile::tempdir().unwrap();
    let d = Daemon::start(dir.path()).await;
    let (safe, _) = d.open("safe", &seat_dir(dir.path(), "safe")).await;
    let (dgrs, _) = d.open("dgrs", &seat_dir(dir.path(), "dgrs")).await;
    let tok = |s: &str| aibd::seat_token::tokens().issue(s).unwrap();
    let note = r#"{"op":"note","id":"N-x","fact":"一行"}"#;
    let r = d
        .call("POST", "/api/v1/nodes/plan", Some(&tok(&safe)), Some(note))
        .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(
        r.json["code"], "safe_seat_cannot_write",
        "reason: safe_seat_writes {}",
        r.body
    );
    let r = d
        .call("POST", "/api/v1/nodes/plan", Some(&tok(&dgrs)), Some(note))
        .await;
    assert_ne!(
        r.json["code"], "safe_seat_cannot_write",
        "reason: dgrs_seat_refused {}",
        r.body
    );
    assert_ne!(r.code, 403, "{}", r.body);
}
