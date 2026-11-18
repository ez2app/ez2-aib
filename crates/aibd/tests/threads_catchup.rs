#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace catch-up (SPEC-workspace-multiseat §8, b4297): a seat not called in a shared
//! conversation still learns what was said. The next text that starts a turn of the seat
//! carries, as its own first block, the posts it has not seen (not its own, not those that
//! called it); then its read mark moves, so the next turn carries nothing again.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, read_log, seat_dir, Daemon, Reply};
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

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_not_called_reads_the_conversation_at_its_next_turn() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let b_dir = seat_dir(dir.path(), "b");
    let (b, _) = d.open("safe", &b_dir).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "title": "對話框",
               "members": [{"seat": a, "role": "author"}, {"seat": b, "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    for (body, to) in [("大家先看進度", Value::Null), ("請 @b 審這片", json!([b]))] {
        let r = act(
            &d,
            &cred,
            json!({"type": "thread_send", "thread_id": t, "body": body, "to": to}),
        )
        .await;
        assert_eq!(r.code, 201, "{}", r.body);
    }
    // b was called by the second post: that one arrives as its own delivery, the first rides
    // in front of it (b was not in a turn), told only
    let got = log_with(&b_dir, 1);
    let first = blocks(&got[0].1);
    assert!(
        first[0].contains("沒被點名") && first[0].contains("大家先看進度"),
        "reason: catch_up_missing {first:?}"
    );
    assert!(
        !first[0].contains("審這片"),
        "reason: called_post_in_catch_up {first:?}"
    );
    assert_eq!(first.last().unwrap(), "請 @b 審這片");

    // the read mark moved: the person's next text to b carries no block
    let r = d.act(Some(&cred), &b, "下一句").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&b_dir, 2);
    let second = blocks(&got[1].1);
    assert_eq!(
        second,
        vec!["下一句".to_string()],
        "reason: told_twice {second:?}"
    );
    d.shutdown().await;
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

/// a9136 b1a39a86 / 5d33a PREREAD 7bfb32ab N1: a tree item's thread is caught up on by the
/// seats assigned on the item now, not by a seat named in the list sent at open.
#[tokio::test(flavor = "multi_thread")]
async fn an_items_thread_is_caught_up_on_by_its_assigned_seats_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let a_dir = seat_dir(dir.path(), "a");
    let c_dir = seat_dir(dir.path(), "c");
    let (a, _) = d.open("safe", &a_dir).await;
    let (c, _) = d.open("safe", &c_dir).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "assign_add", "node_id": "N-item", "seat_id": a, "role": "author"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "node": "N-item", "title": "項目",
               "members": [{"seat": c, "role": "observer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "項目上的話"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(d.act(Some(&cred), &a, "a 開工").await.code, 200);
    assert_eq!(d.act(Some(&cred), &c, "c 開工").await.code, 200);
    let ga = log_with(&a_dir, 1);
    assert!(
        blocks(&ga[0].1)[0].contains("項目上的話"),
        "reason: assigned_seat_not_told {:?}",
        blocks(&ga[0].1)
    );
    let gc = log_with(&c_dir, 1);
    assert!(
        !blocks(&gc[0].1).iter().any(|x| x.contains("項目上的話")),
        "reason: unassigned_seat_caught_up {:?}",
        blocks(&gc[0].1)
    );
    d.shutdown().await;
}

/// 5d33a 7bfb32ab N2 (a9136 b1a39a86; b4297 redeliver): a call queued for a seat that waits
/// for a slot, whose assignment is removed before it starts, is not written when it starts:
/// it is settled failed `not_a_member`.
#[tokio::test(flavor = "multi_thread")]
async fn a_call_queued_before_the_assignment_was_removed_is_not_written() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (_, b_session) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    for i in 0..3 {
        let _ = d
            .open("safe", &seat_dir(dir.path(), &format!("run{i}")))
            .await;
    }
    let e_dir = seat_dir(dir.path(), "e");
    let body = json!({"persona_uid": "un0eba", "provider": "claude", "mode": "safe",
                      "cwd": e_dir.to_str().unwrap()})
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.json["session"]["state"], "pending", "{}", r.body);
    let e = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "assign_add", "node_id": "N-gone", "seat_id": e, "role": "author"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "node": "N-gone", "title": "項目",
               "members": [{"seat": e, "role": "author"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "還沒開的席", "to": [e]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let r = act(
        &d,
        &cred,
        json!({"type": "assign_remove", "node_id": "N-gone", "seat_id": e, "role": "author"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    // a slot frees: e starts, and its start's redeliver judges the call again
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{b_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{}", r.body);
    let mut state = Value::Null;
    for _ in 0..50 {
        let v = d
            .call("GET", &format!("/api/v1/threads/{t}"), Some(&cred), None)
            .await;
        state = v.json["posts"][0]["calls"][0].clone();
        if state["state"] != "queued" && !state["state"].is_null() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    assert_eq!(
        state["failed_code"], "not_a_member",
        "reason: removed_member_redelivered {state}"
    );
    assert!(
        !read_log(&e_dir)
            .iter()
            .any(|(_, l)| String::from_utf8_lossy(l).contains("還沒開的席")),
        "reason: written_after_removal"
    );
    d.shutdown().await;
}
