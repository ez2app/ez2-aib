#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S89-talk-2a end to end (DECISION-council-dmq-519d0-1118; test names from
//! MAP-tests-dmq-7b81e-1118 r1): a real aibd, a seat on the fake provider, a confirmed page.
//! `message_send` types to the seat and keeps the row; a seat's message to the person is put
//! into `<data>/messages.sqlite` directly (seat_send writes it from b4297's next slice), then
//! the page reads, marks read and closes it over HTTP.

#[path = "support/seat_input_harness.rs"]
mod harness;
use aib_store::{MessageDb, MessageRow};
use harness::{log_with, read_log, seat_dir, text_of, Daemon, Reply};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

fn library(root: &Path) -> PathBuf {
    let p = root.join("data").join("messages.sqlite");
    assert!(p.is_file(), "{} is where aibd keeps messages", p.display());
    p
}

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn thread(d: &Daemon, cred: &str, id: &str) -> Reply {
    let id = id.replace(':', "%3A");
    d.call("GET", &format!("/api/v1/threads/{id}"), Some(cred), None)
        .await
}

/// The page's own key: the sender of what it sent.
async fn key_of(d: &Daemon, cred: &str, seat: &str) -> String {
    let r = act(
        d,
        cred,
        json!({"type": "message_send", "to_seat": format!("seat:{seat}"), "body": "hi"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    r.json["message"]["from_seat"].as_str().unwrap().to_string()
}

/// A seat's message to `me`, as seat_send will write it: delivered, kind `kind`.
fn seat_wrote(db: &Path, seat: &str, me: &str, kind: &str, n: i64) -> String {
    let lib = MessageDb::open(db).unwrap();
    let from = format!("seat:{seat}");
    let (a, b) = if from.as_str() < me {
        (from.as_str(), me)
    } else {
        (me, from.as_str())
    };
    let id = format!("q_{n:016x}");
    let mut item = serde_json::Map::new();
    for (k, v) in [
        ("id", json!(id)),
        ("status", json!("delivered")),
        ("talk_kind", json!(kind)),
        ("sender_seat", json!(from)),
        ("delivered_at", json!(1_800_000_000 + n)),
    ] {
        item.insert(k.into(), v);
    }
    lib.insert(MessageRow {
        message_id: id.clone(),
        thread_id: format!("{a}~{b}"),
        from_key: from.clone(),
        to_key: me.to_string(),
        created: format!("2027-01-01T00:00:{n:02}Z"),
        created_unix: 1_800_000_000 + n,
        updated: format!("2027-01-01T00:00:{n:02}Z"),
        rev: 1,
        item,
        body: Some(format!("seat says {n}")),
        body_sha256: "0".repeat(64),
        len: 11,
        idem_owner: None,
        idem_key: None,
        journaled: true,
    })
    .unwrap();
    id
}

/// M1 (A test_sender_from_binding / test_audit_has_no_body for the page side): a page's text
/// reaches the seat once, the row keeps it, the thread shows it to the built-in page, the
/// event has the length and hash only; the same idem_key answers the same row and types
/// nothing; another text under that key is 409.
#[tokio::test(flavor = "multi_thread")]
async fn a_page_sends_to_a_seat_and_reads_the_thread_with_the_text() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let send = json!({"type": "message_send", "to_seat": format!("seat:{seat}"),
                      "body": "請看一下", "idem_key": "k1"});
    let r = act(&d, &cred, send.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let m = &r.json["message"];
    assert_eq!(m["state"], "received", "{}", r.body);
    assert_eq!(m["status"], "delivered");
    assert_eq!(m["revision"], 1);
    assert_eq!(m["len"], "請看一下".len());
    assert_eq!(m["to_seat"], format!("seat:{seat}"));
    let me = m["from_seat"].as_str().unwrap().to_string();
    assert!(me.starts_with("human:") && me.len() == 22, "{me}");
    let id = m["message_id"].as_str().unwrap().to_string();
    assert_eq!(text_of(&log_with(&cwd, 1)[0].1), "請看一下");

    let again = act(&d, &cred, send).await;
    assert_eq!(again.code, 200, "{}", again.body);
    assert_eq!(again.json["message"]["message_id"], id.as_str());
    let other = act(
        &d,
        &cred,
        json!({"type": "message_send", "to_seat": format!("seat:{seat}"), "body": "別的",
               "idem_key": "k1"}),
    )
    .await;
    assert_eq!(other.code, 409, "{}", other.body);
    assert_eq!(other.json["code"], "idem_conflict");
    assert_eq!(read_log(&cwd).len(), 1, "a resend types nothing");

    let t = thread(&d, &cred, m["thread_id"].as_str().unwrap()).await;
    assert_eq!(t.code, 200, "{}", t.body);
    let ms = t.json["messages"].as_array().unwrap();
    assert_eq!(ms.len(), 1);
    assert_eq!(
        ms[0]["body"], "請看一下",
        "the built-in page reads the text"
    );

    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let sent: Vec<&Value> = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "message.sent")
        .collect();
    assert_eq!(sent.len(), 1, "{}", ev.body);
    assert!(
        !sent[0].to_string().contains("請看一下"),
        "no text in the event"
    );
    assert_eq!(sent[0]["payload"]["messages"][0]["message_id"], id.as_str());
    d.shutdown().await;
}

/// A test_cross_principal_message_and_cursor: another page reads none of this page's thread
/// and marks nothing in it.
#[tokio::test(flavor = "multi_thread")]
async fn a_page_reads_only_its_own_threads() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    // 519d0 11-18: every built-in page is the person; another key is someone this aibd is not
    let (a, b) = (d.page(true).await, d.page(true).await);
    let me = key_of(&d, &a, &seat).await;
    assert_eq!(
        key_of(&d, &b, &seat).await,
        me,
        "a page paired again is the same person"
    );
    let mine = seat_wrote(&library(dir.path()), &seat, &me, "say", 1);
    assert_eq!(thread(&d, &b, &format!("{me}~seat:{seat}")).await.code, 200);
    let other = "human:00000000000000ff";
    let id = seat_wrote(&library(dir.path()), &seat, other, "say", 2);
    assert_ne!(id, mine);
    let tid = format!("{other}~seat:{seat}");
    let r = thread(&d, &b, &tid).await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert_eq!(r.json["code"], "thread_not_found");
    let r = act(
        &d,
        &b,
        json!({"type": "message_read", "thread_id": tid, "message_id": id}),
    )
    .await;
    assert_eq!(r.code, 404, "{}", r.body);
    let r = act(
        &d,
        &b,
        json!({"type": "message_done", "message_id": id, "expected_revision": 1}),
    )
    .await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert_eq!(r.json["code"], "message_not_found");
    d.shutdown().await;
}

/// A test_read_does_not_ack + B d2: a delivered message stays unread until the page reads it;
/// the read moves everything up to that one and marks the thread, and it is not a close.
#[tokio::test(flavor = "multi_thread")]
async fn a_delivered_message_stays_unread_until_the_page_reads_it() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let me = key_of(&d, &cred, &seat).await;
    let lib = library(dir.path());
    let first = seat_wrote(&lib, &seat, &me, "say", 2);
    let second = seat_wrote(&lib, &seat, &me, "say", 3);
    let tid = format!("{me}~seat:{seat}");
    let t = thread(&d, &cred, &tid).await;
    let unread: Vec<bool> = t.json["messages"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|m| m["to_seat"] == me.as_str())
        .map(|m| m["unread"].as_bool().unwrap())
        .collect();
    assert_eq!(unread, [true, true], "{}", t.body);
    assert!(t.json.get("read_upto").is_none());

    let r = act(
        &d,
        &cred,
        json!({"type": "message_read", "thread_id": tid, "message_id": first}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["read"], 1);
    assert_eq!(r.json["message"]["status"], "read");
    assert_eq!(
        r.json["message"]["state"], "received",
        "read is not processed"
    );
    let t = thread(&d, &cred, &tid).await;
    assert_eq!(t.json["read_upto"], first.as_str());
    let s = t.json["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["message_id"] == second.as_str())
        .unwrap()
        .clone();
    assert_eq!(s["unread"], true, "after the mark stays unread");
    d.shutdown().await;
}

/// A test_ack_rules + B d4: only the receiver closes; a stale revision is 409 with the message
/// as it is; the page's close without evidence is written with its reason; a closed message
/// does not close again.
#[tokio::test(flavor = "multi_thread")]
async fn only_the_receiver_closes_and_a_stale_rev_is_409() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let me = key_of(&d, &cred, &seat).await;
    let id = seat_wrote(&library(dir.path()), &seat, &me, "decide", 4);
    let done =
        |rev: i64| json!({"type": "message_done", "message_id": id, "expected_revision": rev});

    let r = act(&d, &cred, done(7)).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "revision_mismatch");
    assert_eq!(r.json["message_now"]["revision"], 1);
    let r = act(&d, &cred, json!({"type": "message_done", "message_id": id})).await;
    assert_eq!(r.code, 400, "{}", r.body);

    let r = act(&d, &cred, done(1)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["message"]["state"], "processed");
    assert_eq!(r.json["message"]["revision"], 2);
    let row = MessageDb::open(&library(dir.path()))
        .unwrap()
        .get(&id)
        .unwrap()
        .unwrap();
    assert_eq!(row.item["done_kind"], "reason");
    assert_eq!(row.item["done_why"], "decided on the page");

    let r = act(&d, &cred, done(2)).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "message_state", "done does not close twice");
    d.shutdown().await;
}

/// The write order: a row a stop left without its event gets `message.recovered` at the next
/// start, and nothing else is written for rows that have theirs.
#[tokio::test(flavor = "multi_thread")]
async fn a_row_left_without_its_event_gets_one_at_the_next_start() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let me = key_of(&d, &cred, &seat).await;
    d.shutdown().await;
    let lib = library(dir.path());
    let id = seat_wrote(&lib, &seat, &me, "say", 5);
    {
        // as if aibd stopped between the row and its event
        let db = MessageDb::open(&lib).unwrap();
        let r = db.get(&id).unwrap().unwrap();
        let _ = db.update(&id, r.rev, r.item.clone(), &r.updated).unwrap();
        assert_eq!(db.unjournaled().unwrap().len(), 1);
    }
    let mut d = Daemon::start(dir.path()).await;
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let rec: Vec<&Value> = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "message.recovered")
        .collect();
    assert_eq!(rec.len(), 1, "{}", ev.body);
    assert_eq!(rec[0]["payload"]["message_id"], id.as_str());
    assert!(MessageDb::open(&lib)
        .unwrap()
        .unjournaled()
        .unwrap()
        .is_empty());
    d.shutdown().await;
}
