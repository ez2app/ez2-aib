#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls GET usage on a plain sleep
#![cfg(unix)]

//! N-workspace usage end to end (ORDER-workspace 第二波 7ebca): a seat's provider reports its
//! context in its stream-json output; `GET /api/v1/usage` shows that seat's percent (1.x
//! `seat_fork.context_usage` rule), and from 85 % the seat is a slot-3 card. A seat token reads
//! neither.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, seat_dir, Daemon};
use serde_json::Value;

fn row_of(v: &Value, seat: &str) -> Option<Value> {
    v["usage"]
        .as_array()?
        .iter()
        .find(|u| u["seat_id"] == seat)
        .cloned()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_near_its_context_limit_shows_its_percent_and_a_slot_3_card() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, session) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();

    let r = d.call("GET", "/api/v1/usage", Some(&tok), None).await;
    assert_eq!(r.code, 403, "reason: seat_reads_usage {}", r.body);

    // the fake provider answers `usage <n>` with an assistant line reporting n input tokens
    let r = d.act(Some(&cred), &seat, "usage 180000").await;
    assert_eq!(r.code, 200, "{}", r.body);
    log_with(&cwd, 1);
    let mut row = None;
    for _ in 0..100 {
        let r = d.call("GET", "/api/v1/usage", Some(&cred), None).await;
        assert_eq!(r.code, 200, "{}", r.body);
        assert_eq!(r.json["handoff_pct"], 85);
        row = row_of(&r.json, &seat).filter(|u| u["pct"].is_u64());
        if row.is_some() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let row = row.expect("reason: no_reading_from_output");
    assert_eq!(row["session_id"], session.as_str());
    assert_eq!(row["used"], 180_000);
    assert_eq!(row["window"], 200_000);
    assert_eq!(row["pct"], 90);
    assert_eq!(row["model"], "claude-fake");
    assert!(row["last_output_at"].is_string());

    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let card = r.json["slots"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["seat_id"] == seat.as_str() && c["slot"] == 3)
        .cloned()
        .unwrap_or_else(|| panic!("reason: no_slot_3_card {}", r.body));
    assert_eq!(card["kind"], "seat.usage");
    assert_eq!(card["pct"], 90);
    assert_eq!(card["card_id"], format!("usage:{session}:85"));
    d.shutdown().await;
}
