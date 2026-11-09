#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Seats: row + first event + idempotency key in one transaction (spec 4.3:
//! "SQLite transaction 寫入業務狀態與 outbox"), read back after a restart.

use aib_store::{IdemKey, NewEvent, Seat, SeatCreate, Store, StoreError};
use serde_json::json;

fn seat(id: &str, at: &str) -> Seat {
    Seat {
        seat_id: id.into(),
        name: format!("name of {id}"),
        state: "CREATED".into(),
        revision: 1,
        created_at: at.into(),
        updated_at: at.into(),
    }
}

fn created(id: &str, ev: &str) -> NewEvent {
    NewEvent {
        event_id: ev.into(),
        aggregate_type: "seat".into(),
        aggregate_id: id.into(),
        sequence: 1,
        event_type: "seat.created".into(),
        correlation_id: None,
        occurred_at: "2026-11-09T00:00:00.000Z".into(),
        payload: json!({"name": format!("name of {id}")}),
    }
}

fn key(k: &str, request: &str) -> Option<IdemKey> {
    Some(IdemKey {
        scope: "POST /api/v1/seats".into(),
        key: k.into(),
        request: request.into(),
    })
}

#[test]
fn a_seat_and_its_event_survive_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    {
        let s = Store::open(&db).unwrap();
        let r = s
            .create_seat(
                seat("seat_a", "2026-11-09T00:00:01.000Z"),
                created("seat_a", "ev_1"),
                None,
                201,
                json!({}),
            )
            .unwrap();
        assert_eq!(
            r,
            SeatCreate::Created(seat("seat_a", "2026-11-09T00:00:01.000Z"))
        );
        s.create_seat(
            seat("seat_b", "2026-11-09T00:00:02.000Z"),
            created("seat_b", "ev_2"),
            None,
            201,
            json!({}),
        )
        .unwrap();
    }
    let s = Store::open(&db).unwrap();
    let all = s.seats().unwrap();
    assert_eq!(
        all.iter().map(|x| x.seat_id.as_str()).collect::<Vec<_>>(),
        vec!["seat_a", "seat_b"]
    );
    assert_eq!(
        s.seat("seat_b").unwrap(),
        Some(seat("seat_b", "2026-11-09T00:00:02.000Z"))
    );
    assert_eq!(s.seat("nope").unwrap(), None);
    let evs = s.events_after(0, 10).unwrap();
    assert_eq!(evs.len(), 2);
    assert_eq!(evs[0].aggregate_id, "seat_a");
    assert_eq!(evs[0].event_type, "seat.created");
}

#[test]
fn the_same_key_and_request_replays_the_first_answer_and_writes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let body = r#"{"name":"x"}"#;
    let first = s
        .create_seat(
            seat("seat_a", "2026-11-09T00:00:01.000Z"),
            created("seat_a", "ev_1"),
            key("k1", body),
            201,
            json!({"seat_id": "seat_a"}),
        )
        .unwrap();
    assert!(matches!(first, SeatCreate::Created(_)));
    // a retry: the caller made a new id, the store returns the first answer
    let again = s
        .create_seat(
            seat("seat_z", "2026-11-09T00:00:09.000Z"),
            created("seat_z", "ev_9"),
            key("k1", body),
            201,
            json!({"seat_id": "seat_z"}),
        )
        .unwrap();
    assert_eq!(
        again,
        SeatCreate::Replayed {
            status: 201,
            response: json!({"seat_id": "seat_a"})
        }
    );
    assert_eq!(s.seats().unwrap().len(), 1);
    assert_eq!(s.events_after(0, 10).unwrap().len(), 1);
}

#[test]
fn the_same_key_with_another_request_is_a_conflict_and_writes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    s.create_seat(
        seat("seat_a", "2026-11-09T00:00:01.000Z"),
        created("seat_a", "ev_1"),
        key("k1", r#"{"name":"x"}"#),
        201,
        json!({}),
    )
    .unwrap();
    let r = s
        .create_seat(
            seat("seat_b", "2026-11-09T00:00:02.000Z"),
            created("seat_b", "ev_2"),
            key("k1", r#"{"name":"y"}"#),
            201,
            json!({}),
        )
        .unwrap();
    assert_eq!(r, SeatCreate::KeyConflict);
    assert_eq!(s.seats().unwrap().len(), 1);
    assert_eq!(s.events_after(0, 10).unwrap().len(), 1);
    // keys are per route: the same key on another scope is independent
    let other = Some(IdemKey {
        scope: "POST /api/v1/other".into(),
        key: "k1".into(),
        request: r#"{"name":"y"}"#.into(),
    });
    let r = s
        .create_seat(
            seat("seat_b", "2026-11-09T00:00:02.000Z"),
            created("seat_b", "ev_2"),
            other,
            201,
            json!({}),
        )
        .unwrap();
    assert!(matches!(r, SeatCreate::Created(_)));
}

#[test]
fn a_failed_event_write_leaves_no_seat_and_no_key() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    s.create_seat(
        seat("seat_a", "2026-11-09T00:00:01.000Z"),
        created("seat_a", "ev_1"),
        None,
        201,
        json!({}),
    )
    .unwrap();
    // the event id is taken: the whole transaction rolls back
    let r = s.create_seat(
        seat("seat_b", "2026-11-09T00:00:02.000Z"),
        created("seat_b", "ev_1"),
        key("k2", "{}"),
        201,
        json!({}),
    );
    assert!(matches!(r, Err(StoreError::Sqlite(_))), "{r:?}");
    assert_eq!(s.seat("seat_b").unwrap(), None);
    // the key was not recorded either: a retry with a fresh event id succeeds
    let r = s
        .create_seat(
            seat("seat_b", "2026-11-09T00:00:02.000Z"),
            created("seat_b", "ev_2"),
            key("k2", "{}"),
            201,
            json!({}),
        )
        .unwrap();
    assert!(matches!(r, SeatCreate::Created(_)));
}

#[test]
fn an_event_for_another_aggregate_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let r = s.create_seat(
        seat("seat_a", "2026-11-09T00:00:01.000Z"),
        created("seat_other", "ev_1"),
        None,
        201,
        json!({}),
    );
    assert!(matches!(r, Err(StoreError::Invalid(_))), "{r:?}");
    assert!(s.seats().unwrap().is_empty());
}
