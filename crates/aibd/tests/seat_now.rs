#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-now slice 1 end to end (519d0 05:24): a seat writes its own status line with its seat
//! token through act `seat_now`; it cannot name another seat or read the board; a page reads
//! every line from `GET /api/v1/now`; the journal keeps lengths and hashes, never the words.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

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

    // slice 2: the Stop hook's check sees this seat's own write only
    let ask = r#"{"type":"seat_now_check","since":"2000-01-01T00:00:00Z"}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(ask)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["updated"], true,
        "reason: check_missed_the_write {}",
        r.body
    );
    assert_eq!(
        r.json["review_due"], true,
        "reply without why/idea {}",
        r.body
    );
    let late = r#"{"type":"seat_now_check","since":"2999-01-01T00:00:00Z"}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(late)).await;
    assert_eq!(
        r.json["updated"], false,
        "reason: future_turn_counts {}",
        r.body
    );
    let other = aibd::seat_token::tokens().issue("seat_now_other").unwrap();
    let r = d.call("POST", "/api/v1/act", Some(&other), Some(ask)).await;
    assert_eq!(
        r.json["updated"], false,
        "reason: another_seats_write_counts {}",
        r.body
    );

    // slice 3: the SessionStart hook reads back this seat's own line, never another's
    let mine = r#"{"type":"seat_now_mine"}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(mine)).await;
    assert_eq!(r.code, 200, "reason: mine_not_dispatched {}", r.body);
    assert_eq!(
        r.json["now"]["item"], "寫狀態列的秘密項目",
        "reason: mine_lost_the_line {}",
        r.body
    );
    let r = d
        .call("POST", "/api/v1/act", Some(&other), Some(mine))
        .await;
    assert_eq!(
        r.json["now"],
        serde_json::Value::Null,
        "reason: mine_reads_another_seat {}",
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

/// 0eed7 PRECHECK-chain4 L1: a seat whose session stops leaves the board (not only on
/// `state: clear`), so `GET /api/v1/now` never shows a stopped or archived seat's last line.
#[tokio::test(flavor = "multi_thread")]
async fn a_stopped_seat_leaves_the_board() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, session) = d.open("safe", &seat_dir(dir.path(), "stops")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let cred = d.page(true).await;
    let line = r#"{"type":"seat_now","item":"停席前的最後一行","done":1,"total":2}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(line)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d.call("GET", "/api/v1/now", Some(&cred), None).await;
    // precondition: the line is on the board before the stop
    assert!(
        r.body.contains(&seat),
        "reason: line_not_on_board {}",
        r.body
    );
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{session}"), None, None)
        .await;
    assert!(r.code < 300, "{}", r.body);
    // the stop finishes on its own task: give it up to 10 s
    let mut gone = String::new();
    for _ in 0..100 {
        let r = d.call("GET", "/api/v1/now", Some(&cred), None).await;
        if !r.body.contains(&seat) {
            gone.clear();
            break;
        }
        gone = r.body;
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    assert!(gone.is_empty(), "reason: stopped_seat_on_board {gone}");
    d.shutdown().await;
}

/// 1a93b GAP-web-backend §6: a seat says its handoff note is written with its own token; a page
/// reads the path in `GET /api/v1/now` `handoffs`; the journal keeps `seat.handoff_written` with
/// the path's length and hash only.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_says_its_handoff_is_written_and_only_the_hash_is_journaled() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let seat = "seat_handoff_e2e";
    let tok = aibd::seat_token::tokens().issue(seat).unwrap();
    let cred = d.page(true).await;
    let path = "/Users/x/.ez2-aib/pending/HANDOFF-秘密交接-1120.md";
    let ask = serde_json::json!({"type": "seat_handoff", "path": path}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&ask)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["seat_id"], seat,
        "reason: not_the_tokens_seat {}",
        r.body
    );
    let bad = r#"{"type":"seat_handoff","path":"HANDOFF.md"}"#;
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(bad)).await;
    assert_eq!(r.code, 400, "reason: relative_path_taken {}", r.body);

    let r = d.call("GET", "/api/v1/now", Some(&cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let rows = r.json["handoffs"].as_array().expect("reason: no_handoffs");
    let mine = rows
        .iter()
        .find(|x| x["seat_id"] == seat)
        .expect("reason: handoff_lost");
    assert_eq!(mine["path"], path, "reason: handoff_lost {}", r.body);
    assert!(mine["at"].is_string());

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        r.body.contains("seat.handoff_written"),
        "reason: not_journaled {}",
        r.body
    );
    assert!(
        !r.body.contains("秘密交接"),
        "reason: path_in_journal {}",
        r.body
    );
}
