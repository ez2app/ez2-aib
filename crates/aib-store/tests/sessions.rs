#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Session rows: written with their event, read back after a restart, and
//! open sessions turned `interrupted` (never resumed) when aibd starts again.

use aib_store::{NewEvent, Seat, SessionRow, Store, StoreError};
use serde_json::json;

fn seat(s: &Store, id: &str) {
    s.create_seat(
        Seat {
            seat_id: id.into(),
            name: id.into(),
            state: "CREATED".into(),
            revision: 1,
            created_at: "2026-11-09T00:00:00.000Z".into(),
            updated_at: "2026-11-09T00:00:00.000Z".into(),
        },
        NewEvent {
            event_id: format!("ev_{id}"),
            aggregate_type: "seat".into(),
            aggregate_id: id.into(),
            sequence: 1,
            event_type: "seat.created".into(),
            correlation_id: None,
            occurred_at: "2026-11-09T00:00:00.000Z".into(),
            payload: json!({}),
        },
        None,
        201,
        json!({}),
    )
    .unwrap();
}

fn row(id: &str, seat: Option<&str>, state: &str, holds: bool, at: &str) -> SessionRow {
    SessionRow {
        session_id: id.into(),
        seat_id: seat.map(str::to_string),
        pid: 4242,
        argv0: Some("sh".into()),
        argc: 3,
        state: state.into(),
        holds_slot: holds,
        started_at: at.into(),
        ended_at: None,
        ended_by: None,
        exit_code: None,
        signal: None,
        stop_status: None,
        stop_reason: None,
        updated_at: at.into(),
    }
}

fn ev(id: &str, seq: i64, kind: &str) -> NewEvent {
    NewEvent {
        event_id: format!("ev_{id}_{seq}"),
        aggregate_type: "session".into(),
        aggregate_id: id.into(),
        sequence: seq,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: "2026-11-09T00:00:01.000Z".into(),
        payload: json!({"id": id}),
    }
}

#[test]
fn a_session_row_follows_its_events_and_survives_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    {
        let s = Store::open(&db).unwrap();
        seat(&s, "seat_a");
        let r = row(
            "s_1",
            Some("seat_a"),
            "running",
            true,
            "2026-11-09T00:00:01.000Z",
        );
        s.session_event(r.clone(), ev("s_1", 1, "session.started"))
            .unwrap();
        let mut done = r;
        done.state = "exited".into();
        done.holds_slot = false;
        done.ended_at = Some("2026-11-09T00:00:05.000Z".into());
        done.ended_by = Some("root_exit".into());
        done.exit_code = Some(0);
        done.stop_status = Some("unverified".into());
        done.stop_reason = Some("observed_clean".into());
        done.updated_at = "2026-11-09T00:00:05.000Z".into();
        s.session_event(done, ev("s_1", 2, "session.exited"))
            .unwrap();
    }
    let s = Store::open(&db).unwrap();
    let got = s.session_row("s_1").unwrap().unwrap();
    assert_eq!(got.state, "exited");
    assert_eq!(got.exit_code, Some(0));
    assert_eq!(got.seat_id.as_deref(), Some("seat_a"));
    assert_eq!(
        got.started_at, "2026-11-09T00:00:01.000Z",
        "started_at never changes"
    );
    assert_eq!(s.sessions_of_seat("seat_a").unwrap().len(), 1);
    assert!(s.sessions_of_seat("seat_b").unwrap().is_empty());
    // nothing was open, so a restart interrupts nothing
    assert!(s
        .interrupt_open_sessions("2026-11-09T01:00:00.000Z".into(), || "ev_x".into())
        .unwrap()
        .is_empty());
}

#[test]
fn open_sessions_are_interrupted_at_start_and_never_resumed() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    seat(&s, "seat_a");
    s.session_event(
        row(
            "s_run",
            Some("seat_a"),
            "running",
            true,
            "2026-11-09T00:00:01.000Z",
        ),
        ev("s_run", 1, "session.started"),
    )
    .unwrap();
    // a stop that could not show the tree gone keeps its slot
    s.session_event(
        row(
            "s_held",
            None,
            "stop_unconfirmed",
            true,
            "2026-11-09T00:00:02.000Z",
        ),
        ev("s_held", 1, "session.started"),
    )
    .unwrap();
    s.session_event(
        row("s_done", None, "exited", false, "2026-11-09T00:00:03.000Z"),
        ev("s_done", 1, "session.started"),
    )
    .unwrap();
    let mut n = 0;
    let ids = s
        .interrupt_open_sessions("2026-11-09T01:00:00.000Z".into(), move || {
            n += 1;
            format!("ev_restart_{n}")
        })
        .unwrap();
    assert_eq!(ids, vec!["s_run".to_string(), "s_held".to_string()]);
    for id in ["s_run", "s_held"] {
        let r = s.session_row(id).unwrap().unwrap();
        assert_eq!(r.state, "interrupted", "{id}");
        assert!(!r.holds_slot, "{id}");
        assert_eq!(r.ended_by.as_deref(), Some("aibd_restart"), "{id}");
        assert_eq!(
            r.ended_at.as_deref(),
            Some("2026-11-09T01:00:00.000Z"),
            "{id}"
        );
    }
    assert_eq!(s.session_row("s_done").unwrap().unwrap().state, "exited");
    let evs = s.events_after(0, 100).unwrap();
    let int: Vec<_> = evs
        .iter()
        .filter(|e| e.event_type == "session.interrupted")
        .collect();
    assert_eq!(int.len(), 2);
    assert_eq!(int[0].aggregate_id, "s_run");
    assert_eq!(int[0].sequence, 2, "the next sequence of that session");
    assert_eq!(int[0].payload["previous_state"], "running");
    assert_eq!(int[0].payload["processes_may_remain"], true);
    // a second start interrupts nothing again
    assert!(s
        .interrupt_open_sessions("2026-11-09T02:00:00.000Z".into(), || "ev_y".into())
        .unwrap()
        .is_empty());
}

#[test]
fn a_session_of_an_unknown_seat_or_a_foreign_event_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let r = s.session_event(
        row(
            "s_1",
            Some("seat_nope"),
            "running",
            true,
            "2026-11-09T00:00:01.000Z",
        ),
        ev("s_1", 1, "session.started"),
    );
    assert!(
        matches!(r, Err(StoreError::Sqlite(_))),
        "foreign key: {r:?}"
    );
    assert_eq!(s.session_row("s_1").unwrap(), None);
    let r = s.session_event(
        row("s_1", None, "running", true, "2026-11-09T00:00:01.000Z"),
        ev("s_other", 1, "session.started"),
    );
    assert!(matches!(r, Err(StoreError::Invalid(_))), "{r:?}");
    assert!(s.events_after(0, 10).unwrap().is_empty());
}
