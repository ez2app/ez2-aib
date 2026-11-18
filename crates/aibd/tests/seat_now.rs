#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-now slice 1 end to end (519d0 05:24): a seat writes its own status line with its seat
//! token through act `seat_now`; it cannot name another seat or read the board; a page reads
//! every line from `GET /api/v1/now`; the journal keeps lengths and hashes, never the words.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_writes_only_its_own_line_and_only_a_page_reads_the_board() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let seat = "seat_now_e2e";
    let tok = aibd::seat_token::tokens().issue(seat).unwrap();
    let cred = d.page(true).await;

    let line = serde_json::json!({"type":"seat_now","item":"寫狀態列的秘密項目","done":2,
        "total":4,"state":"reply","track":60,"why":"等收據"})
    .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&line)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["seat_id"], seat,
        "reason: not_the_tokens_seat {}",
        r.body
    );
    assert_eq!(r.json["now"]["done"], 2);

    // the body cannot pick the seat
    let other = r#"{"type":"seat_now","item":"x","seat_id":"seat_other"}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(other)).await;
    assert_eq!(r.code, 400, "reason: seat_field_taken {}", r.body);
    let long = serde_json::json!({"type":"seat_now","item":"x".repeat(121)}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&long)).await;
    assert_eq!(r.code, 400, "reason: long_item_taken {}", r.body);

    // a seat never reads the board
    let r = d.call("GET", "/api/v1/now", Some(&tok), None).await;
    assert_eq!(r.code, 403, "reason: seat_reads_board {}", r.body);
    // a page does
    let r = d.call("GET", "/api/v1/now", Some(&cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let rows = r.json["now"].as_array().unwrap();
    let mine = rows.iter().find(|x| x["seat_id"] == seat).unwrap();
    assert_eq!(
        mine["now"]["item"], "寫狀態列的秘密項目",
        "reason: line_lost {}",
        r.body
    );
    assert_eq!(mine["now"]["state"], "reply");

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        r.body.contains("seat.now"),
        "reason: not_journaled {}",
        r.body
    );
    assert!(
        !r.body.contains("秘密項目") && !r.body.contains("等收據"),
        "reason: words_in_journal {}",
        r.body
    );

    let clear = r#"{"type":"seat_now","state":"clear"}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(clear)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d.call("GET", "/api/v1/now", Some(&cred), None).await;
    assert!(
        !r.body.contains(seat),
        "reason: clear_kept_the_line {}",
        r.body
    );

    // a stopped seat's token writes nothing
    aibd::seat_token::tokens().revoke(seat);
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&line)).await;
    assert_eq!(r.code, 401, "reason: stopped_seat_wrote {}", r.body);
    d.shutdown().await;
}
