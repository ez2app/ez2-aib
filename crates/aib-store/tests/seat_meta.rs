#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! A11 S37: a seat's revive data is written with the seat (one transaction)
//! and read back after a restart; the resume id is pending until reported,
//! filled once. Mirrors the Python spec S1–S4 / R8
//! (aib/tests/test-restart-a11-revive-2240e-1113.py, frozen reference).

use aib_store::{NewEvent, ResumeFill, Seat, SeatCreate, SeatMeta, Store, StoreError};
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
        occurred_at: "2026-11-13T00:00:00.000Z".into(),
        payload: json!({}),
    }
}

fn meta(id: &str, resume: Option<&str>) -> SeatMeta {
    SeatMeta {
        seat_id: id.into(),
        provider: "claude".into(),
        role: Some("dev".into()),
        cwd: "/work/tree".into(),
        model: Some("model-x".into()),
        model_pinned: true,
        resume_id: resume.map(Into::into),
        project_uid: Some("proj-1".into()),
        persona_uid: Some("persona-1".into()),
        brain_route: Some("route/a".into()),
        updated_at: "2026-11-13T00:00:00.000Z".into(),
    }
}

fn open_with_meta(s: &Store, id: &str, at: &str, m: SeatMeta) -> aib_store::Result<SeatCreate> {
    s.create_seat_with_meta(
        seat(id, at),
        m,
        created(id, &format!("ev_{id}")),
        None,
        201,
        json!({}),
    )
}

#[test]
fn opening_a_seat_writes_its_revive_data_and_it_survives_a_restart() {
    // S1: the data is there right after the seat opens, not at the next snapshot
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    {
        let s = Store::open(&db).unwrap();
        let r = open_with_meta(
            &s,
            "seat_a",
            "2026-11-13T00:00:01.000Z",
            meta("seat_a", None),
        )
        .unwrap();
        assert!(matches!(r, SeatCreate::Created(_)));
        assert_eq!(s.seat_meta("seat_a").unwrap(), Some(meta("seat_a", None)));
    }
    let s = Store::open(&db).unwrap();
    assert_eq!(s.seat_meta("seat_a").unwrap(), Some(meta("seat_a", None)));
    assert_eq!(s.seat("seat_a").unwrap().unwrap().revision, 1);
}

#[test]
fn a_seat_opened_without_meta_has_none() {
    // S2: the existing route is unchanged
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    s.create_seat(
        seat("seat_a", "t1"),
        created("seat_a", "ev_1"),
        None,
        201,
        json!({}),
    )
    .unwrap();
    assert_eq!(s.seat_meta("seat_a").unwrap(), None);
}

#[test]
fn refused_meta_means_the_seat_did_not_open() {
    // S3 (2.0 reverses 1.x): the store is the record, so no meta = no seat, visibly
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let mut empty_cwd = meta("seat_a", None);
    empty_cwd.cwd = String::new();
    assert!(open_with_meta(&s, "seat_a", "t1", empty_cwd).is_err());
    assert!(matches!(
        open_with_meta(&s, "seat_a", "t1", meta("seat_other", None)),
        Err(StoreError::Invalid(_))
    ));
    assert_eq!(s.seat("seat_a").unwrap(), None);
    assert_eq!(s.seat_meta("seat_a").unwrap(), None);
    assert!(s.events_after(0, 10).unwrap().is_empty());
}

#[test]
fn an_empty_resume_id_is_refused_never_stored_as_a_value() {
    // S4: pending is None, "" is not a value
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    assert!(matches!(
        open_with_meta(&s, "seat_a", "t1", meta("seat_a", Some(""))),
        Err(StoreError::Invalid(_))
    ));
    assert_eq!(s.seat("seat_a").unwrap(), None);
    open_with_meta(&s, "seat_a", "t1", meta("seat_a", None)).unwrap();
    assert!(matches!(
        s.fill_resume_id("seat_a", "", "ev_f", "t2"),
        Err(StoreError::Invalid(_))
    ));
    assert_eq!(s.seat_meta("seat_a").unwrap().unwrap().resume_id, None);
}

#[test]
fn the_resume_id_is_filled_once_with_an_event_and_no_revision() {
    // S4: backfill = UPDATE + event; retries and other values write nothing
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    open_with_meta(&s, "seat_a", "t1", meta("seat_a", None)).unwrap();
    assert_eq!(
        s.fill_resume_id("seat_a", "conv-1", "ev_f1", "2026-11-13T00:00:05.000Z")
            .unwrap(),
        ResumeFill::Filled
    );
    let m = s.seat_meta("seat_a").unwrap().unwrap();
    assert_eq!(m.resume_id.as_deref(), Some("conv-1"));
    assert_eq!(m.updated_at, "2026-11-13T00:00:05.000Z");
    let evs = s.events_of("seat_a", 0, 10).unwrap();
    assert_eq!(
        evs.iter()
            .map(|e| (e.sequence, e.event_type.as_str()))
            .collect::<Vec<_>>(),
        [(1, "seat.created"), (2, "seat.resume_id_filled")]
    );
    assert!(!evs[1].payload.to_string().contains("conv-1"));
    assert_eq!(
        s.fill_resume_id("seat_a", "conv-1", "ev_f2", "t3").unwrap(),
        ResumeFill::AlreadySet
    );
    assert_eq!(
        s.fill_resume_id("seat_a", "conv-2", "ev_f3", "t4").unwrap(),
        ResumeFill::Conflict {
            existing: "conv-1".into()
        }
    );
    assert_eq!(
        s.seat_meta("seat_a").unwrap().unwrap().resume_id.as_deref(),
        Some("conv-1")
    );
    assert_eq!(s.events_of("seat_a", 0, 10).unwrap().len(), 2);
    assert_eq!(s.seat("seat_a").unwrap().unwrap().revision, 1);
    assert_eq!(
        s.fill_resume_id("seat_none", "conv-1", "ev_f4", "t5")
            .unwrap(),
        ResumeFill::NoMeta
    );
}

#[test]
fn the_previous_seat_is_the_newest_other_one_with_that_resume_id() {
    // R8 (lookup half): same conversation in any cabin, never the new seat itself
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let mut old = meta("seat_old", Some("conv-1"));
    old.project_uid = Some("proj-old".into());
    open_with_meta(&s, "seat_old", "2026-11-13T00:00:01.000Z", old).unwrap();
    let mut mid = meta("seat_mid", Some("conv-1"));
    mid.project_uid = Some("proj-mid".into());
    open_with_meta(&s, "seat_mid", "2026-11-13T00:00:02.000Z", mid).unwrap();
    open_with_meta(
        &s,
        "seat_new",
        "2026-11-13T00:00:03.000Z",
        meta("seat_new", Some("conv-1")),
    )
    .unwrap();
    open_with_meta(
        &s,
        "seat_x",
        "2026-11-13T00:00:04.000Z",
        meta("seat_x", Some("conv-9")),
    )
    .unwrap();
    let prev = s
        .previous_seat_with_resume("conv-1", "seat_new")
        .unwrap()
        .unwrap();
    assert_eq!(prev.seat_id, "seat_mid");
    assert_eq!(prev.project_uid.as_deref(), Some("proj-mid"));
    assert_eq!(
        s.previous_seat_with_resume("conv-9", "seat_x").unwrap(),
        None
    );
    assert_eq!(
        s.previous_seat_with_resume("conv-none", "seat_new")
            .unwrap(),
        None
    );
}
