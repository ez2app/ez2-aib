//! Who is alive (A11 T46, aibd half; DESIGN-A11-revive-rust-2240e-1113.md
//! §8 / §9). A seat whose last session ended without being closed on purpose
//! is *down*; if it has not come back after `alive_after_s` (default 600 s),
//! aibd records one `alive.down` event for it (the captain's dm is sent from
//! that event once B1's queue lands).
//!
//! No resident timer: a down seat is known the moment its session ends
//! (aibd is the parent and gets the exit). Only while some down seat is
//! still waiting for its threshold does one `Operation` timer thread run; it
//! sleeps until the earliest due time, runs one round and stops when nothing
//! is pending. No sessions, nothing down ⇒ no wakeups.
//!
//! The switches live in `<data dir>/revive-policy.json` (next to
//! `session-policy.json`): missing ⇒ defaults (alert on, auto revive off,
//! 600 s; Andy 11-13); unreadable or invalid ⇒ aibd does not start.

use aib_store::{AliveDown, NewEvent, SessionRow, Store};
use serde::Deserialize;
use std::path::Path;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// The file name in the data folder.
pub const REVIVE_POLICY_FILE: &str = "revive-policy.json";
/// The longest threshold accepted (one day): the timer's lifetime cap.
pub const MAX_AFTER_S: i64 = 86_400;

/// The A11 switches (Andy 11-13: alert on by default, auto revive off).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevivePolicy {
    #[serde(default = "yes")]
    pub alive_alert: bool,
    #[serde(default)]
    pub auto_revive: bool,
    #[serde(default = "default_after")]
    pub alive_after_s: i64,
}

fn yes() -> bool {
    true
}

fn default_after() -> i64 {
    600
}

impl Default for RevivePolicy {
    fn default() -> Self {
        RevivePolicy {
            alive_alert: true,
            auto_revive: false,
            alive_after_s: default_after(),
        }
    }
}

impl RevivePolicy {
    /// Parse the file's text. A threshold outside 60..=86400 is refused, not
    /// clamped (a typo should stop the start, not change the meaning).
    pub fn parse(text: &str) -> Result<RevivePolicy, String> {
        let p: RevivePolicy = serde_json::from_str(text).map_err(|e| e.to_string())?;
        if !(aib_store::ALIVE_MIN_AFTER_S..=MAX_AFTER_S).contains(&p.alive_after_s) {
            return Err(format!(
                "alive_after_s must be {}..={MAX_AFTER_S}, got {}",
                aib_store::ALIVE_MIN_AFTER_S,
                p.alive_after_s
            ));
        }
        Ok(p)
    }

    /// `<data dir>/revive-policy.json`: missing ⇒ defaults; anything else that
    /// cannot be read or parsed is an error with the reason.
    pub fn load(data_dir: &Path) -> Result<RevivePolicy, String> {
        let f = data_dir.join(REVIVE_POLICY_FILE);
        match std::fs::read_to_string(&f) {
            Ok(t) => RevivePolicy::parse(&t).map_err(|e| format!("{REVIVE_POLICY_FILE}: {e}")),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(RevivePolicy::default()),
            Err(e) => Err(format!("{REVIVE_POLICY_FILE}: {}", e.kind())),
        }
    }
}

/// How a seat's session ended, for the alive check.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EndKind {
    /// Stopped by the user / captain, or the CLI ended normally: forget it.
    OnPurpose,
    /// Ended any other way: down, alert after the threshold.
    Down,
    /// Never really ran (refused or failed to start): not tracked.
    NotStarted,
}

/// `ended_by` / `exit_code` of a session that just ended.
pub fn end_kind(ended_by: &str, exit_code: Option<i32>) -> EndKind {
    match ended_by {
        "api" => EndKind::OnPurpose,
        "root_exit" if exit_code == Some(0) => EndKind::OnPurpose,
        "spawn_failed" | "policy_refused" => EndKind::NotStarted,
        _ => EndKind::Down,
    }
}

/// `ended_by` values that mean "it was alive when aibd stopped": after a
/// restart such a seat gets a fresh threshold from the start (it could not
/// have been brought back while aibd was down).
pub fn alive_at_stop(ended_by: &str) -> bool {
    matches!(ended_by, "shutdown" | "aibd_restart") // aib_store::sessions ENDED_BY_RESTART
}

type LiveFn = Arc<dyn Fn() -> Vec<String> + Send + Sync>;
type NowFn = Arc<dyn Fn() -> i64 + Send + Sync>;

/// The aibd side of the alive check.
pub struct Alive {
    store: Arc<Store>,
    policy: RevivePolicy,
    /// Seats with a running session now (from the sessions in memory, never
    /// from a process listing: "could not list" must not read as "all gone").
    live: LiveFn,
    now: NowFn,
    /// Every round runs under this lock. The number counts the session
    /// starts / ends seen; a round that took its own live list (timer, start)
    /// only writes if no start / end came in meanwhile, else it looks again
    /// (Codex A11-2 M1: a stale list must not bring back a seat closed on
    /// purpose). Lock order: sessions, then this; never the other way.
    rounds: Mutex<u64>,
    /// Told on every count change: a waiting timer looks again at once
    /// (Codex A11-2 M4: a seat that came back stops the wait now).
    moved: Condvar,
    /// A timer thread is running (one at a time).
    running: Mutex<bool>,
    /// Test hook: no timer thread (tests call [`Alive::run_due`] themselves).
    no_thread: bool,
}

fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
        .unwrap_or(0)
}

/// A session that really ran (not refused / failed to start), for the
/// restart check.
fn really_ran(s: &SessionRow) -> bool {
    let started = s.pid != 0; // a queued session ended before it started (Codex M3 r2)
    started
        && !matches!(
            s.ended_by.as_deref(),
            Some("spawn_failed" | "policy_refused")
        )
}

/// How many times a round with its own live list looks again while session
/// starts / ends keep coming in, before it gives up this time (the timer
/// looks again a second later).
const FRESH_TRIES: usize = 8;

impl Alive {
    pub fn new(
        store: Arc<Store>,
        policy: RevivePolicy,
        live: impl Fn() -> Vec<String> + Send + Sync + 'static,
    ) -> Arc<Alive> {
        Arc::new(Alive {
            store,
            policy,
            live: Arc::new(live),
            now: Arc::new(unix_now),
            rounds: Mutex::new(0),
            moved: Condvar::new(),
            running: Mutex::new(false),
            no_thread: false,
        })
    }

    /// Tests: a fixed clock and no timer thread.
    pub fn for_test(
        store: Arc<Store>,
        policy: RevivePolicy,
        live: impl Fn() -> Vec<String> + Send + Sync + 'static,
        now: impl Fn() -> i64 + Send + Sync + 'static,
    ) -> Arc<Alive> {
        Arc::new(Alive {
            store,
            policy,
            live: Arc::new(live),
            now: Arc::new(now),
            rounds: Mutex::new(0),
            moved: Condvar::new(),
            running: Mutex::new(false),
            no_thread: true,
        })
    }

    /// A timer thread is running now (tests and `aib restart alive`).
    pub fn timer_running(&self) -> bool {
        *self.running.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn policy(&self) -> &RevivePolicy {
        &self.policy
    }

    fn live_pairs(live: Vec<String>) -> Vec<(String, Vec<String>)> {
        live.into_iter().map(|s| (s, Vec::new())).collect()
    }

    /// A session of `seat` started: it is alive (clears an earlier alert).
    /// `live`: the seats running now. Called with the sessions lock held, so
    /// it must not call back into the sessions (no `self.live`).
    pub fn on_start(self: &Arc<Self>, seat: &str, mut live: Vec<String>) {
        if !live.iter().any(|s| s == seat) {
            live.push(seat.to_string());
        }
        self.round_with(live, Vec::new());
    }

    /// A session of `seat` ended. `live`: the seats still running. Called
    /// with the sessions lock held (see [`Alive::on_start`]).
    pub fn on_end(self: &Arc<Self>, seat: &str, kind: EndKind, live: Vec<String>) {
        if live.iter().any(|s| s == seat) {
            return; // another session of the seat still runs
        }
        match kind {
            EndKind::NotStarted => {}
            EndKind::OnPurpose => self.round_with(live, vec![seat.to_string()]),
            EndKind::Down => {
                // stamp the end time as its last time seen (the newest end counts)
                let mut with = live;
                with.push(seat.to_string());
                self.round_with(with, Vec::new());
                self.kick();
            }
        }
    }

    fn lock_rounds(&self) -> std::sync::MutexGuard<'_, u64> {
        self.rounds.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// A round from a session hook: its live list is exact (taken under the
    /// sessions lock), so it always writes, and it tells the other rounds.
    fn round_with(&self, live: Vec<String>, closed: Vec<String>) {
        let mut g = self.lock_rounds();
        *g = g.wrapping_add(1);
        self.round_locked(live, closed);
        drop(g);
        self.moved.notify_all();
    }

    /// A round with a live list taken here (timer, start), plus `extra`
    /// seats counted as alive. Retried while hooks come in between.
    fn round_fresh(&self, extra: &[String]) -> Vec<AliveDown> {
        for _ in 0..FRESH_TRIES {
            let seen = *self.lock_rounds();
            let mut live = (self.live)();
            live.extend(extra.iter().cloned());
            let g = self.lock_rounds();
            if *g == seen {
                let down = self.round_locked(live, Vec::new());
                drop(g);
                return down;
            }
        }
        tracing::warn!("alive: sessions kept changing, round left for the next look");
        Vec::new()
    }

    /// The round itself; the alert and its `alive.down` event (the outbox
    /// for B1's dm) are written in one transaction (Codex A11-2 M2).
    fn round_locked(&self, live: Vec<String>, closed: Vec<String>) -> Vec<AliveDown> {
        let now = (self.now)();
        let notify = self.policy.alive_alert;
        let auto = if self.policy.auto_revive {
            "not_available"
        } else {
            "off"
        };
        let at = crate::sessions::rfc3339_of_unix(now);
        let made = self.store.alive_round_events(
            now,
            Self::live_pairs(live),
            closed,
            self.policy.alive_after_s,
            move |d, seq| NewEvent {
                event_id: format!("ev_alive_{}_{seq}", d.cabin),
                aggregate_type: "alive".into(),
                aggregate_id: format!("alive:{}", d.cabin),
                sequence: seq,
                event_type: "alive.down".into(),
                correlation_id: None,
                occurred_at: at.clone(),
                payload: serde_json::json!({
                    "seat_id": d.cabin,
                    "last_seen": d.last_seen,
                    "gone_for_s": d.gone_for_s,
                    "notify": notify,
                    "auto_revive": auto,
                    "restore": serde_json::Value::Null,
                }),
            },
        );
        match made {
            Ok(down) => {
                for d in &down {
                    tracing::warn!(seat = %d.cabin, gone_for_s = d.gone_for_s,
                        notify, "seat down past the threshold");
                }
                down
            }
            Err(e) => {
                tracing::error!(error = %e, "alive: round failed");
                Vec::new()
            }
        }
    }

    /// At aibd start: seats that were alive when aibd stopped get a fresh
    /// threshold from now; others keep their time, and anything already due
    /// is reported at once (a9136 11-13: a restart must not lose an alert).
    /// "When aibd stopped" = the session of the seat that ended last (Codex
    /// A11-2 M3), not the one started last.
    pub fn start(self: &Arc<Self>) {
        let rows = match self.store.alive_rows() {
            Ok(r) => r,
            Err(e) => {
                tracing::error!(error = %e, "alive: could not read at start");
                return;
            }
        };
        let fresh: Vec<String> = rows
            .iter()
            .filter(|r| r.alerted_at.is_none())
            .filter(|r| {
                self.store
                    .sessions_of_seat(&r.cabin)
                    .ok()
                    .and_then(|v| {
                        v.into_iter()
                            .filter(|s| s.ended_at.is_some() && really_ran(s))
                            .max_by(|a, b| a.ended_at.cmp(&b.ended_at))
                    })
                    .and_then(|s| s.ended_by)
                    .is_some_and(|b| alive_at_stop(&b))
            })
            .map(|r| r.cabin.clone())
            .collect();
        self.round_fresh(&fresh);
        self.kick();
    }

    /// When the next not-yet-reported down seat is due, if any.
    pub fn next_due(&self) -> Option<i64> {
        let live = (self.live)();
        self.store
            .alive_rows()
            .ok()?
            .into_iter()
            .filter(|r| r.alerted_at.is_none() && !live.contains(&r.cabin))
            .map(|r| r.last_seen + self.policy.alive_after_s)
            .min()
    }

    /// The count, then the next due time: a start / end after this look
    /// changes the count, so the wait that follows ends at once.
    fn look(&self) -> Option<(i64, u64)> {
        let seen = *self.lock_rounds();
        self.next_due().map(|due| (due, seen))
    }

    /// One round now: report every seat that is due. Returns what was reported.
    pub fn run_due(&self) -> Vec<AliveDown> {
        self.round_fresh(&[])
    }

    /// Start the timer thread unless one runs; the thread itself looks for
    /// what is due and stops at once if nothing is (it may not look here:
    /// this runs under the sessions lock).
    fn kick(self: &Arc<Self>) {
        if self.no_thread {
            return;
        }
        {
            let mut r = self.running.lock().unwrap_or_else(|e| e.into_inner());
            if *r {
                return;
            }
            *r = true;
        }
        let me = Arc::clone(self);
        let spawned = std::thread::Builder::new()
            .name("aibd-alive".into())
            .spawn(move || Alive::timer_loop(&me));
        if spawned.is_err() {
            *self.running.lock().unwrap_or_else(|e| e.into_inner()) = false;
            tracing::error!("alive: could not start the timer thread");
        }
    }

    fn timer_loop(self: &Arc<Self>) {
        // a round that found nothing yet (the clock is whole seconds) waits
        // at least one second before the next look, never spins
        let mut floor = 0u64;
        while let Some((due, mut seen)) = self.look() {
            let wait = u64::try_from(due - (self.now)()).unwrap_or(0).max(floor);
            if wait > 0 {
                let op = crate::timers::ALIVE_DUE_WAIT
                    .begin(Some(Instant::now() + Duration::from_secs(wait)));
                // any session start / end (else each 60 s tick) looks again:
                // the seat came back or was closed ⇒ stop waiting for it now
                // (Codex A11-2 M4)
                while op.tick_or_wake(&self.rounds, &self.moved, &mut seen) {
                    if self.next_due() != Some(due) {
                        break;
                    }
                }
            }
            floor = u64::from(self.run_due().is_empty());
        }
        *self.running.lock().unwrap_or_else(|e| e.into_inner()) = false;
        // a seat that went down after the last look but before `running` was
        // cleared found a thread running and did not start one: look again
        if self.next_due().is_some() {
            self.kick();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn end_kinds() {
        assert_eq!(end_kind("api", Some(1)), EndKind::OnPurpose);
        assert_eq!(end_kind("root_exit", Some(0)), EndKind::OnPurpose);
        assert_eq!(end_kind("root_exit", Some(1)), EndKind::Down);
        assert_eq!(end_kind("root_exit", None), EndKind::Down);
        assert_eq!(end_kind("shutdown", None), EndKind::Down);
        assert_eq!(end_kind("aibd_restart", None), EndKind::Down);
        assert_eq!(end_kind("spawn_failed", None), EndKind::NotStarted);
        assert_eq!(end_kind("policy_refused", None), EndKind::NotStarted);
    }

    #[test]
    fn policy_defaults_and_limits() {
        assert_eq!(RevivePolicy::parse("{}").unwrap(), RevivePolicy::default());
        let d = RevivePolicy::default();
        assert!(d.alive_alert && !d.auto_revive && d.alive_after_s == 600);
        assert!(RevivePolicy::parse(r#"{"alive_after_s": 59}"#).is_err());
        assert!(RevivePolicy::parse(r#"{"alive_after_s": 86401}"#).is_err());
        assert!(RevivePolicy::parse(r#"{"alive_alert": false, "typo": 1}"#).is_err());
        assert!(RevivePolicy::parse("not json").is_err());
        let p = RevivePolicy::parse(
            r#"{"alive_alert": false, "auto_revive": true, "alive_after_s": 60}"#,
        )
        .unwrap();
        assert!(!p.alive_alert && p.auto_revive && p.alive_after_s == 60);
    }
}
