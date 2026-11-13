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

fn seat_with_last_end(store: &Store, seat: &str, ended_by: &str) {
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
    store
        .session_event(
            SessionRow {
                session_id: format!("ss_{seat}"),
                seat_id: Some(seat.into()),
                pid: 1,
                argv0: Some("sh".into()),
                argc: 1,
                state: "killed".into(),
                holds_slot: false,
                started_at: "t".into(),
                ended_at: Some("t".into()),
                ended_by: Some(ended_by.into()),
                exit_code: None,
                signal: None,
                stop_status: None,
                stop_reason: None,
                updated_at: "t".into(),
            },
            NewEvent {
                event_id: format!("ev_ss_{seat}"),
                aggregate_type: "session".into(),
                aggregate_id: format!("ss_{seat}"),
                sequence: 1,
                event_type: "session.killed".into(),
                correlation_id: None,
                occurred_at: "t".into(),
                payload: json!({}),
            },
        )
        .unwrap();
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
