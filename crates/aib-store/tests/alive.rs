#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! A11 T46 (store half): last seen, one alert per outage, closed cabins
//! dropped. Mirrors the Python spec A1–A4 / A7 / A10
//! (aib/tests/test-restart-a11-revive-2240e-1113.py, frozen reference).

use aib_store::{AliveDown, Store, ALIVE_MIN_AFTER_S};
use std::sync::Arc;

const AFTER: i64 = 600;
const T0: i64 = 1_794_576_000;

fn live(cabins: &[(&str, Vec<&str>)]) -> Vec<(String, Vec<String>)> {
    cabins
        .iter()
        .map(|(c, s)| {
            (
                (*c).to_string(),
                s.iter().map(|x| (*x).to_string()).collect(),
            )
        })
        .collect()
}

fn store() -> (tempfile::TempDir, Store) {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    (dir, s)
}

#[test]
fn the_first_round_only_records() {
    // A1
    let (_d, s) = store();
    let got = s
        .alive_round(
            T0,
            live(&[("cab1", vec!["s1", "s2"]), ("cab2", vec![])]),
            vec![],
            AFTER,
        )
        .unwrap();
    assert!(got.is_empty());
    let rows = s.alive_rows().unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!((rows[0].cabin.as_str(), rows[0].last_seen), ("cab1", T0));
    assert_eq!(rows[0].seats, ["s1", "s2"]);
    assert_eq!(rows[0].alerted_at, None);
}

#[test]
fn a_cabin_down_past_the_threshold_is_reported_once() {
    // A2 + A4 (absent from the live list = down; the caller decides that by process)
    let (_d, s) = store();
    s.alive_round(
        T0,
        live(&[("cab1", vec!["s1"]), ("cab2", vec!["s2"])]),
        vec![],
        AFTER,
    )
    .unwrap();
    assert!(s
        .alive_round(T0 + AFTER - 1, live(&[("cab2", vec!["s2"])]), vec![], AFTER)
        .unwrap()
        .is_empty());
    let got = s
        .alive_round(T0 + AFTER, live(&[("cab2", vec!["s2"])]), vec![], AFTER)
        .unwrap();
    assert_eq!(
        got,
        [AliveDown {
            cabin: "cab1".into(),
            last_seen: T0,
            gone_for_s: AFTER,
            seats: vec!["s1".into()]
        }]
    );
    assert!(s
        .alive_round(
            T0 + AFTER + 60,
            live(&[("cab2", vec!["s2"])]),
            vec![],
            AFTER
        )
        .unwrap()
        .is_empty());
    let cab1 = s
        .alive_rows()
        .unwrap()
        .into_iter()
        .find(|r| r.cabin == "cab1")
        .unwrap();
    assert_eq!(cab1.alerted_at, Some(T0 + AFTER));
}

#[test]
fn the_threshold_never_goes_below_the_minimum() {
    let (_d, s) = store();
    s.alive_round(T0, live(&[("cab1", vec![])]), vec![], 0)
        .unwrap();
    assert!(s
        .alive_round(T0 + ALIVE_MIN_AFTER_S - 1, vec![], vec![], 0)
        .unwrap()
        .is_empty());
    assert_eq!(
        s.alive_round(T0 + ALIVE_MIN_AFTER_S, vec![], vec![], 0)
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn a_cabin_closed_on_purpose_is_dropped_without_an_alert() {
    // A3, also after it was already reported
    let (_d, s) = store();
    s.alive_round(
        T0,
        live(&[("cab1", vec![]), ("cab2", vec![]), ("cab3", vec![])]),
        vec![],
        AFTER,
    )
    .unwrap();
    let got = s
        .alive_round(
            T0 + AFTER,
            live(&[("cab3", vec![])]),
            vec!["cab1".into(), "cab3".into()],
            AFTER,
        )
        .unwrap();
    assert_eq!(
        got.iter().map(|d| d.cabin.as_str()).collect::<Vec<_>>(),
        ["cab2"]
    );
    let names = |s: &Store| {
        s.alive_rows()
            .unwrap()
            .into_iter()
            .map(|r| r.cabin)
            .collect::<Vec<_>>()
    };
    assert_eq!(names(&s), ["cab2", "cab3"]);
    s.alive_round(T0 + AFTER + 60, vec![], vec!["cab2".into()], AFTER)
        .unwrap();
    assert_eq!(names(&s), ["cab3"]);
}

#[test]
fn back_then_down_again_is_reported_again() {
    // A7
    let (_d, s) = store();
    s.alive_round(T0, live(&[("cab1", vec![])]), vec![], AFTER)
        .unwrap();
    assert_eq!(
        s.alive_round(T0 + AFTER, vec![], vec![], AFTER)
            .unwrap()
            .len(),
        1
    );
    s.alive_round(T0 + 2 * AFTER, live(&[("cab1", vec![])]), vec![], AFTER)
        .unwrap();
    assert_eq!(s.alive_rows().unwrap()[0].alerted_at, None);
    assert!(s
        .alive_round(T0 + 3 * AFTER - 1, vec![], vec![], AFTER)
        .unwrap()
        .is_empty());
    let got = s
        .alive_round(T0 + 3 * AFTER, vec![], vec![], AFTER)
        .unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].last_seen, T0 + 2 * AFTER);
}

#[test]
fn two_rounds_at_once_give_one_alert() {
    // A10: the single writer serialises rounds; dedupe is the transaction
    let (_d, s) = store();
    s.alive_round(T0, live(&[("cab1", vec![])]), vec![], AFTER)
        .unwrap();
    let s = Arc::new(s);
    let hs: Vec<_> = (0..8)
        .map(|_| {
            let s = Arc::clone(&s);
            std::thread::spawn(move || {
                s.alive_round(T0 + AFTER, vec![], vec![], AFTER)
                    .unwrap()
                    .len()
            })
        })
        .collect();
    let total: usize = hs.into_iter().map(|h| h.join().unwrap()).sum();
    assert_eq!(total, 1);
}

#[test]
fn a_stored_seat_list_that_is_not_strings_is_an_error_not_no_seats() {
    // review L1: never read a bad row as "no seats"
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    drop(Store::open(&db).unwrap());
    let c = rusqlite::Connection::open(&db).unwrap();
    c.execute(
        "INSERT INTO cabin_alive (cabin, last_seen, seats_json) VALUES ('cab1', 1, '[1, 2]')",
        [],
    )
    .unwrap();
    drop(c);
    let s = Store::open(&db).unwrap();
    assert!(s.alive_rows().is_err());
    assert!(s
        .alive_round(T0, live(&[("cab2", vec!["s2"])]), vec![], AFTER)
        .is_err());
    // the failed round is rolled back whole: cab2 was not recorded, cab1 not marked
    let c = rusqlite::Connection::open(&db).unwrap();
    let rows: Vec<(String, Option<i64>)> = c
        .prepare("SELECT cabin, alerted_at FROM cabin_alive ORDER BY cabin")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    assert_eq!(rows, [("cab1".to_string(), None)]);
}

fn alert_event(
    id: &str,
) -> impl Fn(&aib_store::AliveDown, i64) -> aib_store::NewEvent + Send + 'static {
    let id = id.to_string();
    move |d, seq| aib_store::NewEvent {
        event_id: id.clone(),
        aggregate_type: "alive".into(),
        aggregate_id: format!("alive:{}", d.cabin),
        sequence: seq,
        event_type: "alive.down".into(),
        correlation_id: None,
        occurred_at: "t".into(),
        payload: serde_json::json!({"seat_id": d.cabin}),
    }
}

#[test]
fn an_alert_and_its_event_are_written_together_or_not_at_all() {
    // Codex A11-2 M2: never marked sent without its outbox event
    let (_d, s) = store();
    s.alive_round(T0, live(&[("cab1", vec![])]), vec![], AFTER)
        .unwrap();
    // the event id is taken: the whole round is refused, nothing is marked
    s.append(vec![aib_store::NewEvent {
        event_id: "ev_taken".into(),
        aggregate_type: "x".into(),
        aggregate_id: "x".into(),
        sequence: 1,
        event_type: "x".into(),
        correlation_id: None,
        occurred_at: "t".into(),
        payload: serde_json::json!({}),
    }])
    .unwrap();
    assert!(s
        .alive_round_events(T0 + AFTER, vec![], vec![], AFTER, alert_event("ev_taken"))
        .is_err());
    assert_eq!(s.alive_rows().unwrap()[0].alerted_at, None);
    // retried with a fresh id: marked and recorded at once, sequence 1 then 2
    let got = s
        .alive_round_events(T0 + AFTER, vec![], vec![], AFTER, alert_event("ev_a1"))
        .unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(s.alive_rows().unwrap()[0].alerted_at, Some(T0 + AFTER));
    s.alive_round(T0 + 2 * AFTER, live(&[("cab1", vec![])]), vec![], AFTER)
        .unwrap();
    s.alive_round_events(T0 + 3 * AFTER, vec![], vec![], AFTER, alert_event("ev_a2"))
        .unwrap();
    let evs = s.events_of("alive:cab1", 0, 10).unwrap();
    assert_eq!(
        evs.iter()
            .map(|e| (e.event_id.as_str(), e.sequence))
            .collect::<Vec<_>>(),
        [("ev_a1", 1), ("ev_a2", 2)]
    );
}
