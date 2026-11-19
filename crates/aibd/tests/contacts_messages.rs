#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep
#![cfg(unix)]

//! ORDER-today-usable (519d0 11-18 07:4x), 7b81e's row: the contacts list and the flow lines
//! read the message library (S89-talk-2a). A page writes to a seat, a seat writes a `decide`
//! to the page (put into the library the way seat_send will write it); the list counts what
//! waits for the page and for the seat, the newest message sorts and dates the row, the flows
//! count the pair by kind without text or length, and marking it read takes the count away.

#[path = "support/seat_input_harness.rs"]
mod harness;
use aib_store::{MessageDb, MessageRow};
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn row_of(d: &Daemon, cred: &str, key: &str) -> Value {
    let r = d.call("GET", "/api/v1/contacts", Some(cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    r.json["contacts"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["key"] == key)
        .cloned()
        .unwrap_or_else(|| panic!("no row {key}: {}", r.body))
}

/// A seat's `decide` to `me`, delivered, written now.
fn seat_decides(lib: &MessageDb, seat_key: &str, me: &str) -> String {
    let (a, b) = if seat_key < me {
        (seat_key, me)
    } else {
        (me, seat_key)
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let mut item = serde_json::Map::new();
    item.insert("id".into(), json!("q_00000000000000d1"));
    item.insert("status".into(), json!("delivered"));
    item.insert("talk_kind".into(), json!("decide"));
    item.insert("sender_seat".into(), json!(seat_key));
    lib.insert(MessageRow {
        message_id: "q_00000000000000d1".into(),
        thread_id: format!("{a}~{b}"),
        from_key: seat_key.into(),
        to_key: me.into(),
        created: "2099-01-01T00:00:00.000Z".into(),
        created_unix: now,
        updated: "2099-01-01T00:00:00.000Z".into(),
        rev: 1,
        item,
        body: Some("要你決定的祕密內容".into()),
        body_sha256: "0".repeat(64),
        len: 9,
        idem_owner: None,
        idem_key: None,
        journaled: true,
    })
    .unwrap();
    "q_00000000000000d1".into()
}

#[tokio::test(flavor = "multi_thread")]
async fn the_contacts_list_and_flows_count_the_library() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    // the quiet seat opens first, so only the newest message can put the other above it
    let (quiet, _) = d.open("safe", &seat_dir(dir.path(), "quiet")).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "seat")).await;
    let cred = d.page(true).await;
    let seat_key = format!("seat:{seat}");

    // precondition: nothing written yet ⇒ zeros, no date
    let r0 = row_of(&d, &cred, &seat_key).await;
    assert_eq!(
        (r0["unread"].as_u64(), r0["inbox_unread"].as_u64()),
        (Some(0), Some(0))
    );
    assert!(r0["last_message_at"].is_null(), "{r0}");

    let r = act(
        &d,
        &cred,
        json!({"type": "message_send", "to_seat": seat_key, "body": "請看一下"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let me = r.json["message"]["from_seat"].as_str().unwrap().to_string();

    let lib = MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();
    let id = seat_decides(&lib, &seat_key, &me);

    let row = row_of(&d, &cred, &seat_key).await;
    assert_eq!(
        row["inbox_unread"], 1,
        "reason: seat_inbox_not_counted {row}"
    );
    assert_eq!(row["unread"], 1, "reason: unread_to_page_not_counted {row}");
    assert_eq!(row["decide_open"], 1, "reason: decide_not_counted {row}");
    assert_eq!(row["last_message_at"], "2099-01-01T00:00:00.000Z", "{row}");
    let mine = row_of(&d, &cred, &me).await;
    assert_eq!(mine["unread"], 1, "reason: self_row_unread {mine}");
    let other = row_of(&d, &cred, &format!("seat:{quiet}")).await;
    assert_eq!(
        (other["unread"].as_u64(), other["inbox_unread"].as_u64()),
        (Some(0), Some(0)),
        "reason: another_seat_counted {other}"
    );
    // the newest message sorts its seat above a quiet one (both running, not pinned)
    let list = d.call("GET", "/api/v1/contacts", Some(&cred), None).await;
    let keys: Vec<&str> = list.json["contacts"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|c| c["key"].as_str())
        .filter(|k| k.starts_with("seat:"))
        .collect();
    assert_eq!(
        keys.first().copied(),
        Some(seat_key.as_str()),
        "reason: not_sorted_by_last {keys:?}"
    );

    let f = d
        .call("GET", "/api/v1/flows?window=600", Some(&cred), None)
        .await;
    assert_eq!(f.code, 200, "{}", f.body);
    assert_eq!(f.json["source"], "messages");
    let pairs = f.json["pairs"].as_array().unwrap();
    let to_seat = pairs.iter().find(|p| p["from"] == me.as_str()).unwrap();
    assert_eq!(
        (to_seat["to"].as_str(), to_seat["say"].as_u64()),
        (Some(seat_key.as_str()), Some(1))
    );
    let to_me = pairs
        .iter()
        .find(|p| p["from"] == seat_key.as_str())
        .unwrap();
    assert_eq!(
        (to_me["decide"].as_u64(), to_me["say"].as_u64()),
        (Some(1), Some(0))
    );
    assert!(
        !f.body.contains("請看一下") && !f.body.contains("祕密") && !f.body.contains("len"),
        "reason: flows_carry_text {}",
        f.body
    );

    // reading it takes the count away
    let thread = if seat_key.as_str() < me.as_str() {
        format!("{seat_key}~{me}")
    } else {
        format!("{me}~{seat_key}")
    };
    let r = act(
        &d,
        &cred,
        json!({"type": "message_read", "thread_id": thread, "message_id": id}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let row = row_of(&d, &cred, &seat_key).await;
    assert_eq!(row["unread"], 0, "reason: read_still_unread {row}");
    assert_eq!(
        row["decide_open"], 1,
        "a read decide is still open until closed {row}"
    );
    d.shutdown().await;
}
