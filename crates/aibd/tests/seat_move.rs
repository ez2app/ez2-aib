#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! G5 信橋 seat_move end to end without a fingerprint reader (DESIGN-g5-bridge-7ebca-1121 r1
//! §1; 5d33a PREREAD-g5-bridge M1): the built-in page asks and, with no fingerprint to ask,
//! nothing is written; a seat's own token can neither move a seat nor take a move back; an old
//! code with no folder in the 1.x ledger is refused; a move already written (as the
//! fingerprint would have written it) is not asked again; the page takes it back without a
//! fingerprint and that is journaled once.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_is_moved_by_the_owners_finger_only_and_moved_back_by_the_page() {
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("state");
    std::fs::create_dir_all(state.join("dm-queue").join("7ebca")).unwrap();
    std::env::set_var("AIBD_IMPORT_STATE", &state);
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let mv = json!({"type": "seat_move", "old_code": "7ebca", "seat_id": seat});
    let why = |r: &Reply| r.json["reason"].as_str().map(str::to_owned);
    let db = aib_store::MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();

    // what the page may not say
    for (body, code, reason) in [
        (
            json!({"type": "seat_move", "old_code": "7EBCA", "seat_id": seat}),
            400,
            "body",
        ),
        (
            json!({"type": "seat_move", "old_code": "7ebca"}),
            400,
            "body",
        ),
        (
            json!({"type": "seat_move", "old_code": "7ebca", "seat_id": seat, "by": "x"}),
            400,
            "body",
        ),
        (
            json!({"type": "seat_move", "old_code": "7ebca", "seat_id": "seat_nosuchseat00"}),
            404,
            "seat_not_found",
        ),
        (
            json!({"type": "seat_move", "old_code": "519d0", "seat_id": seat}),
            404,
            "old_seat_not_found",
        ),
    ] {
        let r = act(&d, &page, body.clone()).await;
        assert_eq!(
            (r.code, why(&r).as_deref()),
            (code, Some(reason)),
            "reason: took_{body} {}",
            r.body
        );
    }

    // no fingerprint can be asked here: refused, and nothing is written
    let r = act(&d, &page, mv.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_unavailable")),
        "{}",
        r.body
    );
    let mut with_ticket = mv.clone();
    with_ticket["ticket"] = json!("ot_made_up");
    let r = act(&d, &page, with_ticket).await;
    assert_eq!(r.code, 403, "reason: a_made_up_ticket_passed {}", r.body);
    assert_eq!(
        db.moved_seat("7ebca").unwrap(),
        None,
        "reason: moved_without_a_fingerprint"
    );

    // a seat cannot move itself
    let r = act(&d, &tok, mv.clone()).await;
    assert!(r.code >= 400, "reason: seat_moved_itself {}", r.body);
    assert_eq!(
        db.moved_seat("7ebca").unwrap(),
        None,
        "reason: moved_by_a_seat"
    );

    // moved now (as the fingerprint would have written it): not asked again
    db.seat_move("7ebca", &seat, "page:test", "2026-11-21T00:00:00Z")
        .unwrap();
    let r = act(&d, &page, mv.clone()).await;
    assert_eq!(
        (r.code, r.json["already"].as_bool()),
        (200, Some(true)),
        "{}",
        r.body
    );
    assert!(
        r.json["steps"].as_array().is_some_and(|s| s.len() == 3),
        "reason: no_steps {}",
        r.body
    );

    // a seat cannot take the move back; the page can, with no fingerprint
    let back = json!({"type": "seat_unmove", "old_code": "7ebca"});
    let r = act(&d, &tok, back.clone()).await;
    assert!(r.code >= 400, "reason: seat_moved_back {}", r.body);
    assert_eq!(
        db.moved_seat("7ebca").unwrap().as_deref(),
        Some(seat.as_str())
    );
    let r = act(&d, &page, back.clone()).await;
    assert_eq!(
        (r.code, r.json["was_moved_into"].as_str()),
        (200, Some(seat.as_str())),
        "{}",
        r.body
    );
    assert_eq!(db.moved_seat("7ebca").unwrap(), None, "reason: move_left");
    let r = act(&d, &page, back).await;
    assert_eq!(
        (r.code, r.json["was_moved_into"].clone()),
        (200, Value::Null),
        "{}",
        r.body
    );

    // journaled once: which code, which seat, by which page
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let events = ev.json["events"].as_array().unwrap();
    let un: Vec<&Value> = events
        .iter()
        .filter(|e| e["type"] == "seat.unmoved")
        .collect();
    assert_eq!(un.len(), 1, "{un:?}");
    assert_eq!(un[0]["payload"]["old_code"], "7ebca");
    assert_eq!(un[0]["payload"]["seat"], json!(seat));
    assert!(
        !events.iter().any(|e| e["type"] == "seat.moved"),
        "reason: moved_journaled_without_a_fingerprint"
    );
    d.shutdown().await;
}
