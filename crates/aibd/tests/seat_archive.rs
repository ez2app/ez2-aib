#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! S89-state (IFACE-S89-state-archive-043dd-1118 r1): archiving a seat is refused while it
//! has a session being started, running or waiting (5d33a L1: under the lock starts take),
//! and nothing starts on an archived seat, also after aibd starts again (5d33a L2).
//! POSIX only (real `/bin/sleep`); every session started here is stopped by `shutdown_all`.
#![cfg(unix)]
// test pacing only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aib_core::ErrorCode;
use aib_store::{NewEvent, Seat, SeatArchive, Store};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

static ENTERED: AtomicBool = AtomicBool::new(false);
static SLOW: AtomicBool = AtomicBool::new(true);

/// The first start says it began and waits before it really starts (the slot is reserved,
/// the session is not in the running list yet).
fn slow_spawn(
    spec: &aibd::platform::session::Spec,
    l: Option<Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    if SLOW.swap(false, Ordering::SeqCst) {
        ENTERED.store(true, Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(600));
    }
    aibd::platform::session_unix::spawn(spec, l)
}

fn seat(store: &Store, id: &str) {
    let at = "2026-11-18T00:00:00.000Z".to_string();
    store
        .create_seat(
            Seat {
                seat_id: id.into(),
                name: id.into(),
                state: "CREATED".into(),
                revision: 1,
                created_at: at.clone(),
                updated_at: at.clone(),
            },
            NewEvent {
                event_id: format!("ev_{id}"),
                aggregate_type: "seat".into(),
                aggregate_id: id.into(),
                sequence: 1,
                event_type: "seat.created".into(),
                correlation_id: None,
                occurred_at: at,
                payload: serde_json::json!({}),
            },
            None,
            201,
            serde_json::json!({}),
        )
        .unwrap();
}

fn sleeper() -> aibd::sessions::Request {
    aibd::sessions::Request {
        argv: vec!["/bin/sleep".into(), "30".into()],
        cwd: None,
        env: vec![],
        timeout: None,
        withhold: vec![],
        input: false,
    }
}

fn busy(r: Result<SeatArchive, aibd::sessions::ApiError>) {
    assert_eq!(r.unwrap_err().0, ErrorCode::SeatBusy);
}

#[test]
fn archive_waits_for_every_session_and_an_archived_seat_starts_nothing() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("db.sqlite");
    let store = Arc::new(Store::open(&db).unwrap());
    for id in ["seat_a", "seat_b", "seat_c"] {
        seat(&store, id);
    }
    // one slot: seat_b's start has to wait behind seat_a
    let s = aibd::sessions::Sessions::new(store, None, slow_spawn, 1, tmp.path().to_path_buf());

    // being started (slot reserved, not running yet)
    let s2 = s.clone();
    let post = std::thread::spawn(move || s2.start_for_seat(Some("seat_a".into()), sleeper()));
    while !ENTERED.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(5));
    }
    busy(s.archive_seat("seat_a", "api"));
    let a = post.join().unwrap().unwrap();
    assert_eq!(a.state, "running");
    // running
    busy(s.archive_seat("seat_a", "api"));
    // waiting for a slot
    let b = s.start_for_seat(Some("seat_b".into()), sleeper()).unwrap();
    assert_eq!(b.state, "pending");
    busy(s.archive_seat("seat_b", "ctl"));

    // a seat with no session is archived once; then nothing starts on it
    let r = s.archive_seat("seat_c", "ctl").unwrap();
    assert!(
        matches!(r, SeatArchive::Archived { ref from, .. } if from == "CREATED"),
        "{r:?}"
    );
    assert!(matches!(
        s.archive_seat("seat_c", "api").unwrap(),
        SeatArchive::Already(_)
    ));
    assert_eq!(
        s.start_for_seat(Some("seat_c".into()), sleeper())
            .unwrap_err()
            .0,
        ErrorCode::SeatArchived
    );
    assert_eq!(
        s.archive_seat("seat_x", "api").unwrap(),
        SeatArchive::NotFound
    );
    s.shutdown_all();

    // aibd started again: the archived seat stays closed
    let store = Arc::new(Store::open(&db).unwrap());
    let s = aibd::sessions::Sessions::new(
        store,
        None,
        aibd::platform::session_unix::spawn,
        1,
        tmp.path().to_path_buf(),
    );
    assert_eq!(
        s.start_for_seat(Some("seat_c".into()), sleeper())
            .unwrap_err()
            .0,
        ErrorCode::SeatArchived
    );
    s.shutdown_all();
}

/// A start that failed but may have left its process running (5d33a 853ee100 L1).
fn lost_spawn(
    _spec: &aibd::platform::session::Spec,
    _l: Option<Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Err(aibd::platform::session::SpawnFail {
        tag: "not_reaped".into(),
        left_running: Some(4_000_000),
    })
}

/// 5d33a 853ee100 L1: a seat whose failed start may have left a process running keeps
/// its slot and stays busy, so it is not archived with an orphan behind it.
#[test]
fn a_seat_with_a_start_that_may_still_run_is_not_archived() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&tmp.path().join("db.sqlite")).unwrap());
    seat(&store, "seat_d");
    seat(&store, "seat_e");
    let s = aibd::sessions::Sessions::new(store, None, lost_spawn, 2, tmp.path().to_path_buf());
    assert!(s.start_for_seat(Some("seat_d".into()), sleeper()).is_err());
    let e = s.archive_seat("seat_d", "ctl").unwrap_err();
    assert_eq!(e.0, ErrorCode::SeatBusy);
    assert!(e.1.contains("restart aibd"), "{}", e.1);
    // another seat is unaffected
    assert!(matches!(
        s.archive_seat("seat_e", "ctl").unwrap(),
        SeatArchive::Archived { .. }
    ));
    s.shutdown_all();
}
