#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! G5 信橋 both ways end to end (DESIGN-g5-bridge-7ebca-1121 r1 §2 §3 §4): a web seat moved
//! from 7ebca writes three letters through `POST /api/v1/act` seat_send into the 1.x ledger of
//! 519d0 (named 7ebca, through the bridge, with aibd's row id), each its own row to
//! `legacy:519d0`; an idempotency key is refused there; three 1.x letters to 7ebca land once
//! in the web seat's inbox; a letter closed in the ledger takes its state onto the row.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn letters_cross_the_bridge_once_each_way() {
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("state");
    let q = state.join("dm-queue");
    for c in ["7ebca", "519d0"] {
        std::fs::create_dir_all(q.join(c)).unwrap();
    }
    let seats = dir.path().join("instances").join("cab1").join("seats");
    std::fs::create_dir_all(&seats).unwrap();
    std::fs::write(
        seats.join("w1.json"),
        json!({"session_uuid": "x-4e6ba9d519d0"}).to_string(),
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("pending")).unwrap();
    std::env::set_var("AIBD_IMPORT_STATE", &state);
    let mut d = Daemon::start(dir.path()).await;
    std::fs::write(
        dir.path().join("data").join("bridge.json"),
        json!({"legacy_cabin": "cab1"}).to_string(),
    )
    .unwrap();
    // a work seat (its folder under <data>/seat-work, as seats_page_open_dgrs opens one)
    let work = dir.path().join("data").join("seat-work");
    let (seat, _) = d.open_as("claude", "work", &seat_dir(&work, "a")).await;
    // 5d33a REVIEW-g5bridge M1: a safe seat writes into no 1.x ledger
    let (safe, _) = d.open("safe", &seat_dir(dir.path(), "s")).await;
    let safe_tok = aibd::seat_token::tokens().issue(&safe).unwrap();
    let r = act(
        &d,
        &safe_tok,
        json!({"type": "seat_send", "to": "519d0", "text": "注入"}),
    )
    .await;
    assert_eq!(r.code, 403, "reason: safe_wrote_ledger {}", r.body);
    assert_eq!(r.json["code"], "seat_mode_cannot_send_to_legacy");
    let none = std::fs::read_dir(q.join("519d0")).unwrap().count();
    assert_eq!(none, 0, "reason: safe_letter_in_ledger");
    aibd::seat_token::tokens().revoke(&safe);
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let db = aib_store::MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();

    // not moved yet: a letter claims no seat
    let r = act(
        &d,
        &tok,
        json!({"type": "seat_send", "to": "519d0", "text": "未搬"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let first = r.json["dm_id"].as_str().unwrap().to_owned();
    let item: Value = serde_json::from_slice(
        &std::fs::read(q.join("519d0").join(format!("{first}.json"))).unwrap(),
    )
    .unwrap();
    assert_eq!(
        item["sender_seat"],
        Value::Null,
        "reason: unmoved_claims_seat"
    );

    db.seat_move("7ebca", &seat, "page:0123abcd", "2026-11-21T00:00:00Z")
        .unwrap();
    let mut sent = Vec::new();
    for i in 0..3 {
        let r = act(
            &d,
            &tok,
            json!({"type": "seat_send", "to": "519d0", "text": format!("網頁第{i}封")}),
        )
        .await;
        assert_eq!(r.code, 200, "{}", r.body);
        assert_eq!(r.json["state"], "delivered", "{}", r.body);
        assert_eq!(r.json["to"], "legacy:519d0");
        sent.push((
            r.json["message_id"].as_str().unwrap().to_owned(),
            r.json["dm_id"].as_str().unwrap().to_owned(),
        ));
    }
    for (mid, dm) in &sent {
        let item: Value = serde_json::from_slice(
            &std::fs::read(q.join("519d0").join(format!("{dm}.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(item["status"], "queued", "reason: not_for_the_drainer");
        assert_eq!(item["sender_seat"], "7ebca", "reason: moved_code_lost");
        assert_eq!(item["sender_label"], "@7ebca(經信橋)");
        assert_eq!(item["aibd_message_id"], mid.as_str());
        let row = db.get(mid).unwrap().unwrap();
        assert_eq!(row.to_key, "seat:legacy:519d0");
        assert_eq!(row.item["status"], "delivered", "reason: row_not_settled");
    }
    let order: Value =
        serde_json::from_slice(&std::fs::read(q.join("519d0").join("order.json")).unwrap())
            .unwrap();
    assert_eq!(
        order["ids"].as_array().map(Vec::len),
        Some(4),
        "reason: order_missed"
    );

    // 0eed7 integ3 B1: a seat's MCP tool always sends a key (mcp-<pid>-<id>): taken, and the
    // same key again answers the first letter and writes nothing more
    let before = order_len(&q);
    let body = json!({"type": "seat_send", "to": "519d0", "text": "經 MCP 寄", "idempotency_key": "mcp-1-7"});
    let r = act(&d, &tok, body.clone()).await;
    assert_eq!(r.code, 200, "reason: mcp_key_refused {}", r.body);
    let first = r.json["message_id"].clone();
    assert_eq!(order_len(&q), before + 1);
    let r = act(&d, &tok, body).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["message_id"], first, "reason: key_sent_twice");
    assert_eq!(r.json["again"], true);
    assert_eq!(order_len(&q), before + 1, "reason: ledger_written_twice");
    let r = act(
        &d,
        &tok,
        json!({"type": "seat_send", "to": "519d0", "text": "別的字", "idempotency_key": "mcp-1-7"}),
    )
    .await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "idempotency_conflict");
    let r = act(
        &d,
        &tok,
        json!({"type": "seat_send", "to": "a9136", "text": "x"}),
    )
    .await;
    assert_eq!(r.code, 404, "reason: folderless_code_written {}", r.body);

    // the other way: three 1.x letters to 7ebca, then a pass of the mirror, twice
    let mut files = Vec::new();
    for i in 0..3 {
        let id = format!("q_00000000000000a{i}");
        let l = json!({"id": id, "status": "queued", "sender_kind": "seat",
            "sender_seat": "519d0", "sender_label": "@519d0", "seat": "7ebca",
            "msg": format!("終端機第{i}封"), "sent_at": 1_795_000_000 + i});
        let p = q.join("7ebca").join(format!("{id}.json"));
        std::fs::write(&p, l.to_string()).unwrap();
        files.push(p);
    }
    // the 519d0 letters the web seat wrote, with their ledger state now done
    for (_, dm) in &sent {
        let p = q.join("519d0").join(format!("{dm}.json"));
        let mut l: Value = serde_json::from_slice(&std::fs::read(&p).unwrap()).unwrap();
        l["status"] = json!("done");
        l["done_why"] = json!("收到");
        std::fs::write(&p, l.to_string()).unwrap();
        files.push(p);
    }
    // the test's library handle is a second writer beside the daemon's: the daemon is not woken
    // here (its redeliver would write the same rows at once; aibd itself has one writer)
    let quiet = |_: &str| {};
    let c = aibd::dm_import::pass_with(&files, dir.path(), &db, true, &quiet).unwrap();
    assert_eq!((c.new, c.updated), (3, 3), "reason: wrong_way {c:?}");
    let inbox = db.open_to(&format!("seat:{seat}"), 50).unwrap();
    let ours: Vec<_> = inbox
        .iter()
        .filter(|r| r.item["imported"] == true)
        .collect();
    assert_eq!(ours.len(), 3, "reason: not_in_inbox");
    assert!(ours[0]
        .body
        .as_deref()
        .unwrap()
        .starts_with(&format!("〔{}〕", aibd::dm_import::BRIDGE_DATA_LINE)));
    let again = aibd::dm_import::pass_with(&files, dir.path(), &db, true, &quiet).unwrap();
    assert_eq!(
        (again.new, again.updated),
        (0, 0),
        "reason: twice {again:?}"
    );
    for (mid, _) in &sent {
        let row = db.get(mid).unwrap().unwrap();
        assert_eq!(row.item["dm_status"], "done", "reason: close_not_followed");
    }

    // r1 E: the sent box has its own letters, the ledger copy's state, and no other seat's
    let r = act(&d, &tok, json!({"type": "message_outbox"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let rows = r.json["messages"].as_array().unwrap();
    assert_eq!(rows.len(), 4, "reason: outbox_count {}", r.body);
    assert!(
        rows.iter().all(|m| m["to_seat"] == "legacy:519d0"),
        "reason: other_rows {}",
        r.body
    );
    let (mid, dm) = &sent[2];
    let newest = &rows[0];
    assert_eq!(newest["id"], mid.as_str(), "reason: not_newest_first");
    assert_eq!(newest["dm_id"], dm.as_str(), "reason: ledger_id_missing");
    assert_eq!(newest["dm_status"], "done", "reason: ledger_state_missing");
    let other = aibd::seat_token::tokens().issue(&safe).unwrap();
    let r = act(&d, &other, json!({"type": "message_outbox"})).await;
    assert_eq!(
        r.json["messages"].as_array().map(Vec::len),
        Some(0),
        "reason: saw_another_seats"
    );
    aibd::seat_token::tokens().revoke(&safe);

    aibd::seat_token::tokens().revoke(&seat);
    d.shutdown().await;
}

fn order_len(q: &std::path::Path) -> usize {
    std::fs::read(q.join("519d0").join("order.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v["ids"].as_array().map(Vec::len))
        .unwrap_or(0)
}
