#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_store::{NewEvent, Store, StoreError};
use serde_json::json;

fn ev(agg: &str, seq: i64, id: &str) -> NewEvent {
    NewEvent {
        event_id: id.into(),
        aggregate_type: "seat".into(),
        aggregate_id: agg.into(),
        sequence: seq,
        event_type: "seat.created".into(),
        correlation_id: None,
        occurred_at: "2026-11-07T00:00:00Z".into(),
        payload: json!({"n": seq}),
    }
}

#[test]
fn migrations_apply_once_and_reopen_is_idempotent() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    {
        let s = Store::open(&db).unwrap();
        assert_eq!(s.applied_migrations().unwrap(), vec![1]);
        assert!(s.integrity_ok().unwrap());
    }
    let s = Store::open(&db).unwrap();
    assert_eq!(s.applied_migrations().unwrap(), vec![1]);
}

#[test]
fn edited_migration_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    drop(Store::open(&db).unwrap());
    let c = rusqlite::Connection::open(&db).unwrap();
    c.execute(
        "UPDATE schema_migrations SET checksum = 'x' WHERE version = 1",
        [],
    )
    .unwrap();
    drop(c);
    match Store::open(&db) {
        Err(StoreError::MigrationChecksum { version: 1, .. }) => {}
        other => panic!("expected checksum error, got {:?}", other.err()),
    }
}

#[test]
fn newer_database_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    drop(Store::open(&db).unwrap());
    let c = rusqlite::Connection::open(&db).unwrap();
    c.execute(
        "INSERT INTO schema_migrations (version, name, checksum) VALUES (99, 'future', 'f')",
        [],
    )
    .unwrap();
    drop(c);
    assert!(matches!(
        Store::open(&db),
        Err(StoreError::UnknownMigration(99))
    ));
}

#[test]
fn events_survive_restart_and_cursor_reads_across_aggregates() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    {
        let s = Store::open(&db).unwrap();
        let g = s
            .append(vec![
                ev("seat-a", 1, "e1"),
                ev("seat-b", 1, "e2"),
                ev("seat-a", 2, "e3"),
            ])
            .unwrap();
        assert_eq!(g, vec![1, 2, 3]);
    }
    let s = Store::open(&db).unwrap();
    let all = s.events_after(0, 100).unwrap();
    assert_eq!(
        all.iter().map(|e| e.event_id.as_str()).collect::<Vec<_>>(),
        ["e1", "e2", "e3"]
    );
    assert_eq!(all[2].payload, json!({"n": 2}));
    let tail = s.events_after(2, 100).unwrap();
    assert_eq!(tail.len(), 1);
    assert_eq!(tail[0].global_seq, 3);
}

#[test]
fn sequence_gap_or_repeat_rolls_back_whole_batch() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    s.append(vec![ev("seat-a", 1, "e1")]).unwrap();
    let err = s
        .append(vec![ev("seat-b", 1, "e2"), ev("seat-a", 1, "e3")])
        .unwrap_err();
    assert!(matches!(
        err,
        StoreError::SequenceConflict {
            expected: 2,
            got: 1,
            ..
        }
    ));
    // e2 must not have been written: the batch is one transaction.
    assert_eq!(s.events_after(0, 10).unwrap().len(), 1);
    assert!(s.append(vec![ev("seat-a", 3, "e4")]).is_err());
}

#[test]
fn duplicate_event_id_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    s.append(vec![ev("seat-a", 1, "same")]).unwrap();
    assert!(matches!(
        s.append(vec![ev("seat-b", 1, "same")]),
        Err(StoreError::Sqlite(_))
    ));
}

/// ADR 0002 measurement. Run with `cargo test -p aib-store --release -- --ignored --nocapture`.
#[test]
#[ignore]
fn bench_10k_events() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let t = std::time::Instant::now();
    for b in 0..100 {
        let batch = (1..=100)
            .map(|i| ev(&format!("agg-{b}"), i, &format!("e-{b}-{i}")))
            .collect();
        s.append(batch).unwrap();
    }
    let w = t.elapsed();
    let t = std::time::Instant::now();
    let mut cur = 0;
    let mut n = 0;
    loop {
        let page = s.events_after(cur, 500).unwrap();
        if page.is_empty() {
            break;
        }
        n += page.len();
        cur = page.last().unwrap().global_seq;
    }
    println!(
        "write 10k in 100 batches: {w:?}; cursor read {n}: {:?}",
        t.elapsed()
    );
    assert_eq!(n, 10_000);
}
