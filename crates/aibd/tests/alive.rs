#![allow(clippy::unwrap_used)]
// test code: a panic is the failure report
// test pacing only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]
//! A11-2 who-is-alive in aibd (DESIGN-A11-revive-rust-2240e-1113.md §8 / §9):
//! a down seat is reported once after the threshold, closed on purpose is
//! forgotten, coming back cancels, the newest end counts, and a restart keeps
//! (or re-times) what was pending. Fixed clock, no timer thread; mirrors the
//! Python spec A2 / A3 / A5–A7 (frozen reference).

use aib_store::{NewEvent, Seat, SessionRow, Store};
use aibd::alive::{Alive, EndKind, RevivePolicy};
use serde_json::json;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

const T0: i64 = 1_794_576_000;

struct Rig {
    _dir: tempfile::TempDir,
    store: Arc<Store>,
    now: Arc<AtomicI64>,
    live: Arc<Mutex<Vec<String>>>,
    alive: Arc<Alive>,
}

fn rig(policy: RevivePolicy) -> Rig {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("aibd.sqlite")).unwrap());
    rig_on(dir, store, policy)
}

fn rig_on(dir: tempfile::TempDir, store: Arc<Store>, policy: RevivePolicy) -> Rig {
    let now = Arc::new(AtomicI64::new(T0));
    let live = Arc::new(Mutex::new(Vec::<String>::new()));
    let (n, l) = (now.clone(), live.clone());
    let alive = Alive::for_test(
        store.clone(),
        policy,
        move || l.lock().unwrap().clone(),
        move || n.load(Ordering::SeqCst),
    );
    Rig {
        _dir: dir,
        store,
        now,
        live,
        alive,
    }
}

impl Rig {
    fn at(&self, t: i64) {
        self.now.store(t, Ordering::SeqCst);
    }
    fn start(&self, seat: &str) {
        let live = self.live.lock().unwrap().clone();
        self.alive.on_start(seat, live);
        self.live.lock().unwrap().push(seat.into());
    }
    fn end(&self, seat: &str, kind: EndKind) {
        self.live.lock().unwrap().retain(|s| s != seat);
        let live = self.live.lock().unwrap().clone();
        self.alive.on_end(seat, kind, live);
    }
    fn downs(&self) -> Vec<serde_json::Value> {
        self.store
            .events_after(0, 1000)
            .unwrap()
            .into_iter()
            .filter(|e| e.event_type == "alive.down")
            .map(|e| e.payload)
            .collect()
    }
}

#[test]
fn a_down_seat_is_reported_once_after_the_threshold() {
    // A2
    let r = rig(RevivePolicy::default());
    r.start("s1");
    r.at(T0 + 10);
    r.end("s1", EndKind::Down);
    assert_eq!(r.alive.next_due(), Some(T0 + 610));
    r.at(T0 + 609);
    assert!(r.alive.run_due().is_empty());
    r.at(T0 + 610);
    assert_eq!(r.alive.run_due().len(), 1);
    r.at(T0 + 700);
    assert!(r.alive.run_due().is_empty());
    let d = r.downs();
    assert_eq!(d.len(), 1, "{d:?}");
    assert_eq!(d[0]["seat_id"], "s1");
    assert_eq!(d[0]["gone_for_s"], 600);
    assert_eq!(d[0]["notify"], true);
    assert_eq!(r.alive.next_due(), None);
}

#[test]
fn a_seat_closed_on_purpose_is_forgotten() {
    // A3
    let r = rig(RevivePolicy::default());
    r.start("s1");
    r.end("s1", EndKind::OnPurpose);
    assert_eq!(r.alive.next_due(), None);
    assert!(r.store.alive_rows().unwrap().is_empty());
    r.at(T0 + 10_000);
    assert!(r.alive.run_due().is_empty());
    assert!(r.downs().is_empty());
}

#[test]
fn a_seat_that_never_started_is_not_tracked() {
    let r = rig(RevivePolicy::default());
    r.end("s1", EndKind::NotStarted);
    assert!(r.store.alive_rows().unwrap().is_empty());
}

#[test]
fn coming_back_before_the_threshold_cancels_and_the_newest_end_counts() {
    // A7 + a9136 11-13 point 2
    let r = rig(RevivePolicy::default());
    r.start("s1");
    r.end("s1", EndKind::Down);
    r.at(T0 + 100);
    r.start("s1");
    assert_eq!(r.alive.next_due(), None, "back: nothing pending");
    r.at(T0 + 200);
    r.end("s1", EndKind::Down);
    assert_eq!(r.alive.next_due(), Some(T0 + 800));
    r.at(T0 + 700);
    assert!(r.alive.run_due().is_empty());
    r.at(T0 + 800);
    assert_eq!(r.alive.run_due().len(), 1);
}

#[test]
fn a_seat_with_another_session_still_running_is_not_down() {
    let r = rig(RevivePolicy::default());
    r.start("s1");
    r.start("s1");
    // one of its two sessions ends; the seat stays in the live list
    let live = r.live.lock().unwrap().clone();
    r.alive.on_end("s1", EndKind::Down, live);
    assert_eq!(r.alive.next_due(), None);
}

#[test]
fn with_alerts_off_the_outage_is_only_recorded() {
    // A5
    let r = rig(RevivePolicy {
        alive_alert: false,
        ..RevivePolicy::default()
    });
    r.start("s1");
    r.end("s1", EndKind::Down);
    r.at(T0 + 600);
    assert_eq!(r.alive.run_due().len(), 1);
    let d = r.downs();
    assert_eq!(d.len(), 1);
    assert_eq!(d[0]["notify"], false);
}

#[test]
fn auto_revive_is_off_by_default_and_not_available_when_on() {
    // A6 (Andy 11-13: off by default; the restore command waits for B4)
    for (on, want) in [(false, "off"), (true, "not_available")] {
        let r = rig(RevivePolicy {
            auto_revive: on,
            ..RevivePolicy::default()
        });
        r.start("s1");
        r.end("s1", EndKind::Down);
        r.at(T0 + 600);
        r.alive.run_due();
        assert_eq!(r.downs()[0]["auto_revive"], want);
    }
}

fn seat(store: &Store, seat: &str) {
    store
        .create_seat(
            Seat {
                seat_id: seat.into(),
                name: seat.into(),
                state: "CREATED".into(),
                revision: 1,
                created_at: "t".into(),
                updated_at: "t".into(),
            },
            NewEvent {
                event_id: format!("ev_{seat}"),
                aggregate_type: "seat".into(),
                aggregate_id: seat.into(),
                sequence: 1,
                event_type: "seat.created".into(),
                correlation_id: None,
                occurred_at: "t".into(),
                payload: json!({}),
            },
            None,
            201,
            json!({}),
        )
        .unwrap();
}

/// One ended session of `seat`: id, start and end time (RFC 3339), how.
fn ended(store: &Store, seat: &str, id: &str, started: &str, ended: &str, by: &str) {
    store
        .session_event(
            SessionRow {
                session_id: id.into(),
                seat_id: Some(seat.into()),
                pid: 1,
                argv0: Some("sh".into()),
                argc: 1,
                state: "killed".into(),
                holds_slot: false,
                started_at: started.into(),
                ended_at: Some(ended.into()),
                ended_by: Some(by.into()),
                exit_code: None,
                signal: None,
                stop_status: None,
                stop_reason: None,
                updated_at: ended.into(),
            },
            NewEvent {
                event_id: format!("ev_{id}"),
                aggregate_type: "session".into(),
                aggregate_id: id.into(),
                sequence: 1,
                event_type: "session.killed".into(),
                correlation_id: None,
                occurred_at: ended.into(),
                payload: json!({}),
            },
        )
        .unwrap();
}

fn seat_with_last_end(store: &Store, s: &str, ended_by: &str) {
    seat(store, s);
    ended(
        store,
        s,
        &format!("ss_{s}"),
        "2026-11-13T01:00:00Z",
        "2026-11-13T02:00:00Z",
        ended_by,
    );
}

#[test]
fn a_restart_retimes_seats_alive_at_the_stop_and_reports_what_was_already_due() {
    // a9136 11-13 point 1: a restart never loses an alert; a seat that was
    // running when aibd stopped gets its threshold again from the start
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("aibd.sqlite")).unwrap());
    seat_with_last_end(&store, "was_up", "shutdown");
    seat_with_last_end(&store, "was_down", "root_exit");
    let r = rig_on(dir, store, RevivePolicy::default());
    for s in ["was_up", "was_down"] {
        r.start(s);
        r.end(s, EndKind::Down);
    }
    // aibd was off for an hour
    r.at(T0 + 3600);
    r.alive.start();
    let d = r.downs();
    assert_eq!(d.len(), 1, "{d:?}");
    assert_eq!(d[0]["seat_id"], "was_down");
    assert_eq!(r.alive.next_due(), Some(T0 + 3600 + 600));
    r.at(T0 + 4200);
    assert_eq!(r.alive.run_due().len(), 1);
    assert_eq!(r.downs()[1]["seat_id"], "was_up");
}

#[test]
fn the_timer_thread_reports_a_seat_when_it_falls_due() {
    // the timer is really armed at start (Python knife: "timer not wired")
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("aibd.sqlite")).unwrap());
    seat_with_last_end(&store, "s1", "root_exit");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    // last seen 599 s ago: due in about one second
    store
        .alive_round(now - 599, vec![("s1".into(), vec![])], vec![], 600)
        .unwrap();
    let alive = Alive::new(store.clone(), RevivePolicy::default(), Vec::new);
    alive.start();
    let end = std::time::Instant::now() + std::time::Duration::from_secs(15);
    loop {
        let n = store
            .events_after(0, 1000)
            .unwrap()
            .iter()
            .filter(|e| e.event_type == "alive.down")
            .count();
        if n == 1 {
            break;
        }
        assert!(std::time::Instant::now() < end, "the timer did not report");
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}

#[test]
fn a_restart_judges_a_seat_by_the_session_that_ended_last() {
    // Codex A11-2 M3: the one that ended last, not the one started last;
    // a session that never really ran does not count
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("aibd.sqlite")).unwrap());
    seat(&store, "s1");
    ended(
        &store,
        "s1",
        "ss_a",
        "2026-11-13T01:00:00Z",
        "2026-11-13T03:00:00Z",
        "shutdown",
    );
    ended(
        &store,
        "s1",
        "ss_b",
        "2026-11-13T02:00:00Z",
        "2026-11-13T02:30:00Z",
        "root_exit",
    );
    ended(
        &store,
        "s1",
        "ss_c",
        "2026-11-13T04:00:00Z",
        "2026-11-13T04:00:01Z",
        "spawn_failed",
    );
    let r = rig_on(dir, store, RevivePolicy::default());
    r.start("s1");
    r.end("s1", EndKind::Down);
    r.at(T0 + 3600);
    r.alive.start();
    assert!(
        r.downs().is_empty(),
        "alive at the stop: re-timed, not reported"
    );
    assert_eq!(r.alive.next_due(), Some(T0 + 3600 + 600));
}

#[test]
fn a_stale_live_list_never_brings_back_a_seat_closed_on_purpose() {
    // Codex A11-2 M1: the timer took "s1 runs", then s1 was closed on purpose
    // before the timer wrote; the round must look again, not re-add s1
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("aibd.sqlite")).unwrap());
    let me: Arc<std::sync::OnceLock<Arc<Alive>>> = Arc::new(std::sync::OnceLock::new());
    let race = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let (m, rc) = (me.clone(), race.clone());
    let alive = Alive::for_test(
        store.clone(),
        RevivePolicy::default(),
        move || {
            if rc.swap(false, Ordering::SeqCst) {
                // the list says s1 runs, but s1 is closed right after
                m.get()
                    .unwrap()
                    .on_end("s1", EndKind::OnPurpose, Vec::new());
                return vec!["s1".to_string()];
            }
            Vec::new()
        },
        || T0,
    );
    me.set(alive.clone()).ok().unwrap();
    alive.on_start("s1", Vec::new());
    race.store(true, Ordering::SeqCst);
    assert!(alive.run_due().is_empty());
    assert!(!race.load(Ordering::SeqCst), "the race was not run");
    assert!(store.alive_rows().unwrap().is_empty(), "s1 came back");
}

#[test]
fn every_outage_is_its_own_event_in_order() {
    // Codex A11-2 L1 / L2: one event per alert, ids never collide, the
    // payload carries when the seat was last seen
    let r = rig(RevivePolicy::default());
    r.start("s1");
    r.at(T0 + 5);
    r.end("s1", EndKind::Down);
    r.at(T0 + 605);
    assert_eq!(r.alive.run_due().len(), 1);
    r.start("s1");
    r.at(T0 + 700);
    r.end("s1", EndKind::Down);
    r.at(T0 + 1300);
    assert_eq!(r.alive.run_due().len(), 1);
    let evs = r.store.events_of("alive:s1", 0, 10).unwrap();
    let got: Vec<(String, i64, i64)> = evs
        .iter()
        .map(|e| {
            (
                e.event_id.clone(),
                e.sequence,
                e.payload["last_seen"].as_i64().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        got,
        [
            ("ev_alive_s1_1".to_string(), 1, T0 + 5),
            ("ev_alive_s1_2".to_string(), 2, T0 + 700),
        ]
    );
}
