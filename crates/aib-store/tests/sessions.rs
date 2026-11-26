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

/// ADR 0005 §5 (a63ab 19:13): a session still waiting in the queue when aibd
/// stopped is `interrupted` at the next start too, told apart by
/// `previous_state`; it never had a process.
#[test]
fn pending_and_running_are_interrupted_with_previous_state_and_process_flag() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let mut p = row("s_wait", None, "pending", false, "2026-11-09T00:00:01.000Z");
    p.pid = 0;
    s.session_event(p, ev("s_wait", 1, "session.queued"))
        .unwrap();
    s.session_event(
        row("s_run", None, "running", true, "2026-11-09T00:00:02.000Z"),
        ev("s_run", 1, "session.started"),
    )
    .unwrap();
    let mut n = 0;
    let ids = s
        .interrupt_open_sessions("2026-11-09T01:00:00.000Z".into(), move || {
            n += 1;
            format!("ev_r{n}")
        })
        .unwrap();
    assert_eq!(ids, vec!["s_wait".to_string(), "s_run".to_string()]);
    let w = s.session_row("s_wait").unwrap().unwrap();
    assert_eq!(w.state, "interrupted");
    assert_eq!(w.ended_by.as_deref(), Some("aibd_restart"));
    let evs = s.events_after(0, 100).unwrap();
    let by = |id: &str| {
        evs.iter()
            .find(|e| e.aggregate_id == id && e.event_type == "session.interrupted")
            .unwrap()
            .payload
            .clone()
    };
    assert_eq!(by("s_wait")["previous_state"], "pending");
    assert_eq!(by("s_wait")["processes_may_remain"], false);
    assert_eq!(by("s_run")["previous_state"], "running");
    assert_eq!(by("s_run")["processes_may_remain"], true);
}

/// A queued session that starts gets its pid and start time on the row.
#[test]
fn a_queued_session_that_starts_gets_its_pid_and_start_time() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let mut p = row("s_q", None, "pending", false, "2026-11-09T00:00:01.000Z");
    p.pid = 0;
    s.session_event(p, ev("s_q", 1, "session.queued")).unwrap();
    let r = row("s_q", None, "running", true, "2026-11-09T00:00:09.000Z");
    s.session_event(r, ev("s_q", 2, "session.started")).unwrap();
    let got = s.session_row("s_q").unwrap().unwrap();
    assert_eq!(
        (got.pid, got.started_at.as_str()),
        (4242, "2026-11-09T00:00:09.000Z")
    );
    assert_eq!(got.state, "running");
}

/// A stop aibd made (`shutdown`) that could not show the tree gone keeps its slot; the next
/// start frees it as `interrupted` but keeps who ended it and when, so the seat is still
/// known as one that was running when aibd stopped (0eed7 51ac92d9 M1: it read
/// `aibd_restart` and the seat was neither picked up nor said skipped). A row nobody ended
/// is still ended by the restart.
#[test]
fn a_held_stop_keeps_who_ended_it_when_the_next_start_frees_it() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let mut held = row(
        "s_held",
        None,
        "stop_unconfirmed",
        true,
        "2026-11-09T00:00:01.000Z",
    );
    held.ended_at = Some("2026-11-09T00:30:00.000Z".into());
    held.ended_by = Some("shutdown".into());
    held.stop_status = Some("unverified".into());
    held.stop_reason = Some("candidates_unreadable".into());
    s.session_event(held, ev("s_held", 1, "session.started"))
        .unwrap();
    s.session_event(
        row("s_run", None, "running", true, "2026-11-09T00:00:02.000Z"),
        ev("s_run", 1, "session.started"),
    )
    .unwrap();
    // two rows are ended, so two event ids (7ebca 04:49: a fixed id hit UNIQUE)
    let mut n = 0;
    s.interrupt_open_sessions("2026-11-09T01:00:00.000Z".into(), move || {
        n += 1;
        format!("ev_r{n}")
    })
    .unwrap();
    let h = s.session_row("s_held").unwrap().unwrap();
    assert_eq!(h.state, "interrupted");
    assert!(!h.holds_slot);
    assert_eq!(h.ended_by.as_deref(), Some("shutdown"));
    assert_eq!(h.ended_at.as_deref(), Some("2026-11-09T00:30:00.000Z"));
    assert_eq!(h.stop_reason.as_deref(), Some("candidates_unreadable"));
    let r = s.session_row("s_run").unwrap().unwrap();
    assert_eq!(r.ended_by.as_deref(), Some("aibd_restart"));
}

/// a9136 ORDER-autodrive 4: the parent aibd counts a preview's running seats from the preview's
/// own store, read only, while the preview keeps it open; a missing file is an error, never 0
/// (0 would let a swap stop seats it could not see).
#[test]
fn a_store_another_aibd_owns_is_counted_read_only() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    assert!(
        aib_store::seat_sessions_running_at(&db).is_err(),
        "reason: missing_store_counted_as_none"
    );
    let s = Store::open(&db).unwrap();
    seat(&s, "seat_a");
    seat(&s, "seat_b");
    let at = "2026-11-20T00:00:00.000Z";
    s.session_event(
        row("s_1", Some("seat_a"), "running", true, at),
        ev("s_1", 1, "session.started"),
    )
    .unwrap();
    s.session_event(
        row("s_2", Some("seat_b"), "exited", false, at),
        ev("s_2", 1, "session.exited"),
    )
    .unwrap();
    s.session_event(
        row("s_3", None, "running", true, at),
        ev("s_3", 1, "session.started"),
    )
    .unwrap();
    // the owner still has it open
    assert_eq!(aib_store::seat_sessions_running_at(&db).unwrap(), 1);
    s.session_event(
        row("s_4", Some("seat_b"), "pending", false, at),
        ev("s_4", 1, "session.queued"),
    )
    .unwrap();
    assert_eq!(aib_store::seat_sessions_running_at(&db).unwrap(), 2);
}

fn out(session: &str, seq: i64, kind: &str) -> NewEvent {
    NewEvent {
        event_id: format!("ev_{session}_out_{seq}"),
        aggregate_type: "session_output".into(),
        aggregate_id: format!("{session}/output"),
        sequence: seq,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: "2026-11-26T05:00:00.000Z".into(),
        payload: json!({"stream": "stdout", "line": "hi", "cut": false}),
    }
}

/// 6770f IFACE-guide r3 `first`: a seat's session that wrote a line counts; a session the
/// preview.2 route started (no seat), a seat's session that only closed its streams, or a line
/// under another session's name do not.
#[test]
fn a_seat_has_spoken_only_once_a_seats_session_wrote_a_line() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let spoken = |s: &Store| s.a_seat_has_spoken("/output", "session.output").unwrap();
    assert!(!spoken(&s), "reason: empty_store_spoke");
    seat(&s, "seat_a");
    let at = "2026-11-26T05:00:00.000Z";
    s.session_event(
        row("s_route", None, "running", false, at),
        ev("s_route", 1, "session.started"),
    )
    .unwrap();
    s.append(vec![out("s_route", 1, "session.output")]).unwrap();
    assert!(!spoken(&s), "reason: route_session_counted");
    s.session_event(
        row("s_a", Some("seat_a"), "running", true, at),
        ev("s_a", 1, "session.started"),
    )
    .unwrap();
    s.append(vec![out("s_a", 1, "session.output_closed")])
        .unwrap();
    assert!(!spoken(&s), "reason: closed_counted_as_a_line");
    s.append(vec![out("s_other", 1, "session.output")]).unwrap();
    assert!(!spoken(&s), "reason: sessionless_line_counted");
    s.append(vec![out("s_a", 2, "session.output")]).unwrap();
    assert!(spoken(&s), "reason: seat_line_not_seen");
}
