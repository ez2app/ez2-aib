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

/// One poll of `GET /api/v1/usage` for the seat's row, until `ok` says it is there.
async fn row_when(d: &Daemon, cred: &str, seat: &str, ok: &dyn Fn(&Value) -> bool) -> Value {
    let mut last = Value::Null;
    for _ in 0..100 {
        let r = d.call("GET", "/api/v1/usage", Some(cred), None).await;
        assert_eq!(r.code, 200, "{}", r.body);
        assert!(r.json["now"].is_string(), "reason: no_now {}", r.body);
        if let Some(row) = row_of(&r.json, seat) {
            if ok(&row) {
                return row;
            }
            last = row;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    last
}

/// N-webonly-c44: a turn whose model never answers reads as started with no output (the
/// provider's own lines at the start of a turn are not the model's), on a Claude seat and on a
/// codex seat; a turn that is answered says when; a turn that ended says neither.
#[tokio::test(flavor = "multi_thread")]
async fn a_turn_the_model_never_answers_says_started_and_no_output() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;

    // Claude seat: before any text it is in no turn
    let cwd = seat_dir(dir.path(), "claude");
    let (seat, _) = d.open("safe", &cwd).await;
    // `mute`: the fake prints its init line and nothing else
    let r = d.act(Some(&cred), &seat, "mute").await;
    assert_eq!(r.code, 200, "{}", r.body);
    log_with(&cwd, 1);
    // the init line was read (last_output_at is any line) and it is not the model's
    let row = row_when(&d, &cred, &seat, &|u: &Value| {
        u["last_output_at"].is_string() && u["turn_started_at"].is_string()
    })
    .await;
    assert!(
        row["turn_started_at"].is_string(),
        "reason: turn_start_not_said {row}"
    );
    assert!(
        row["last_output_at"].is_string(),
        "reason: init_not_read {row}"
    );
    assert!(
        row["turn_output_at"].is_null(),
        "reason: own_line_read_as_output {row}"
    );
    let started = row["turn_started_at"].as_str().unwrap().to_owned();
    // the person types again into the same turn: the model answers, the turn ends
    let r = d.act(Some(&cred), &seat, "hold").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let row = row_when(&d, &cred, &seat, &|u: &Value| {
        u["turn_output_at"].is_string()
    })
    .await;
    assert!(
        row["turn_output_at"].is_string(),
        "reason: output_not_said {row}"
    );
    assert_eq!(
        row["turn_started_at"],
        started.as_str(),
        "reason: turn_restarted {row}"
    );
    assert!(
        row["turn_output_at"].as_str().unwrap() >= started.as_str(),
        "reason: output_before_start {row}"
    );
    let r = d.act(Some(&cred), &seat, "go").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let row = row_when(&d, &cred, &seat, &|u: &Value| {
        u["turn_started_at"].is_null()
    })
    .await;
    assert!(
        row["turn_started_at"].is_null() && row["turn_output_at"].is_null(),
        "reason: ended_turn_still_said {row}"
    );

    // codex seat: codex answers the turn/start and echoes the text by itself
    let cwd = seat_dir(dir.path(), "codex");
    let (seat, _) = d.open_as("codex", "safe", &cwd).await;
    let mut r = d.act(Some(&cred), &seat, "mute").await;
    for _ in 0..50 {
        if r.code == 200 {
            break;
        }
        assert_eq!(r.json["code"], "seat_starting", "{}", r.body);
        std::thread::sleep(std::time::Duration::from_millis(100));
        r = d.act(Some(&cred), &seat, "mute").await;
    }
    assert_eq!(r.code, 200, "{}", r.body);
    log_with(&cwd, 4);
    let row = row_when(&d, &cred, &seat, &|u: &Value| {
        u["turn_started_at"].is_string()
    })
    .await;
    assert!(
        row["turn_started_at"].is_string(),
        "reason: codex_turn_start_not_said {row}"
    );
    // give codex's own lines (the result, turn/started, the echo) time to be read
    std::thread::sleep(std::time::Duration::from_millis(500));
    let row = row_when(&d, &cred, &seat, &|u: &Value| {
        u["turn_started_at"].is_string()
    })
    .await;
    assert!(
        row["turn_output_at"].is_null(),
        "reason: codex_own_line_read_as_output {row}"
    );
    d.shutdown().await;
}
