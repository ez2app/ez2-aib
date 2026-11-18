#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! TH9 in its own process: aibd keeps the assignments' file once per process
//! (assign::DB, a OnceLock set by the first aibd started), so beside other aibds in
//! tests/threads.rs this test read another aibd's assignments and its item thread was 404 at
//! random (receipts a9136-rjs-ef2a40e0f, a9136-rjs-b84f023ec).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn assign(d: &Daemon, cred: &str, kind: &str, node: &str, seat: &str, role: &str) -> Reply {
    act(
        d,
        cred,
        json!({"type": kind, "node_id": node, "seat_id": seat, "role": role}),
    )
    .await
}

/// TH9 (5d33a wave-2 N1 / N2, checklist C2 / C3 / A4): a tree item's thread is read and
/// posted in by the seats assigned on that item now. Removing an assignment takes the thread
/// away at the next read; a seat assigned on another item (the one above) gets nothing and
/// cannot open the item's thread; the members sent at open are not used for a tree item.
#[tokio::test(flavor = "multi_thread")]
async fn a_tree_items_thread_follows_its_assignments_now() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let (c, _) = d.open("safe", &seat_dir(dir.path(), "c")).await;
    let cred = d.page(true).await;
    for (seat, node, role) in [
        (&a, "N-live", "author"),
        (&b, "N-live", "reviewer"),
        (&c, "N-above", "reviewer"),
    ] {
        let r = assign(&d, &cred, "assign_add", node, seat, role).await;
        assert_eq!(r.code, 200, "{}", r.body);
    }
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "node": "N-live", "title": "現算成員",
               "members": [{"seat": c, "role": "observer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let mut on: Vec<String> = r.json["thread"]["members"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| m["seat"].as_str().unwrap().to_string())
        .collect();
    on.sort();
    let mut want = vec![format!("seat:{a}"), format!("seat:{b}")];
    want.sort();
    assert_eq!(on, want, "reason: members_from_the_list_sent {}", r.body);

    let tok = |s: &str| aibd::seat_token::tokens().issue(s).unwrap();
    let view = json!({"type": "thread_view", "thread_id": t});
    let (tb, tc) = (tok(&b), tok(&c));
    let r = act(&d, &tb, view.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = act(&d, &tc, view.clone()).await;
    assert_eq!(
        r.code, 404,
        "reason: a_seat_above_reads_the_item {}",
        r.body
    );
    let r = act(
        &d,
        &tc,
        json!({"type": "thread_open", "node": "N-live", "title": "x"}),
    )
    .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "not_assigned_here");

    // third wave 3: a seat lists only its threads, and answers in one calling another seat
    let list = json!({"type": "thread_list"});
    let r = act(&d, &tb, list.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["threads"][0]["thread_id"], t.as_str(), "{}", r.body);
    let r = act(&d, &tc, list).await;
    assert_eq!(
        r.json["threads"].as_array().map(Vec::len),
        Some(0),
        "reason: a_seat_lists_threads_it_is_not_in {}",
        r.body
    );
    let r = act(
        &d,
        &tok(&a),
        json!({"type": "thread_send", "thread_id": t, "body": "請審", "to": [b.clone()]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(
        r.json["post"]["calls"][0]["seat"],
        format!("seat:{b}"),
        "{}",
        r.body
    );

    let r = assign(&d, &cred, "assign_remove", "N-live", &b, "reviewer").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = act(&d, &tb, view).await;
    assert_eq!(
        r.code, 404,
        "reason: removed_assignment_still_reads {}",
        r.body
    );
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "x", "to": [b]}),
    )
    .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["code"], "to_not_a_member");
    d.shutdown().await;
}
