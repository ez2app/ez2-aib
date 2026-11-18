#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace catch-up for a dgrs seat (SPEC-workspace-multiseat §8, b4297). Its own test
//! binary: it turns this machine's dgrs input switch on (process-wide), which the other
//! catch-up tests must not see (5d33a PREREAD-fastfix 6faea22a; #860 macos assigned_seat_not_told).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn blocks(line: &[u8]) -> Vec<String> {
    let v: Value = serde_json::from_slice(line).unwrap();
    match &v["message"]["content"] {
        Value::String(s) => vec![s.clone()],
        Value::Array(a) => a
            .iter()
            .map(|b| b["text"].as_str().unwrap_or_default().to_owned())
            .collect(),
        _ => Vec::new(),
    }
}

/// 5d33a PREREAD a71bbbdb M1: a safe seat cannot reach a dgrs seat by not calling it either.
/// What a safe seat posts in a conversation a dgrs seat is in is not put in front of the dgrs
/// seat's next turn; what the person posts on the built-in page is.
#[tokio::test(flavor = "multi_thread")]
async fn a_dgrs_seat_is_not_told_what_a_safe_seat_posted() {
    // the person types into the dgrs seat to start its turn (this machine's switch on; the
    // other test here only has safe seats)
    // (read once when the server starts, so it is set before)
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let c_dir = seat_dir(dir.path(), "c");
    let (c, _) = d.open("dgrs", &c_dir).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "title": "混合",
               "members": [{"seat": a, "role": "author"}, {"seat": c, "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let ta = aibd::seat_token::tokens().issue(&a).unwrap();
    let r = act(
        &d,
        &ta,
        json!({"type": "thread_send", "thread_id": t, "body": "安全席的話"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "真人的話"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    // the dgrs seat's next turn: the person's post rides in front, the safe seat's does not
    let r = d.act(Some(&cred), &c, "開工").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&c_dir, 1);
    let b = blocks(&got[0].1);
    assert!(
        b[0].contains("真人的話"),
        "reason: person_post_missing {b:?}"
    );
    assert!(
        !b.iter().any(|x| x.contains("安全席的話")),
        "reason: safe_post_reached_dgrs {b:?}"
    );
    d.shutdown().await;
}
