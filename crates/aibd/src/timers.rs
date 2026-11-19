//! The only place aibd may sleep or tick (ADR 0005).
//!
//! `clippy.toml` forbids `std::thread::sleep` and the `tokio::time` sleep /
//! interval functions everywhere else, so every periodic or delayed wakeup is
//! one of the registered [`Timer`]s below. Each timer:
//!
//! - is `Resident` (may run while aibd is idle; period ≥ 30 s) or `Operation`
//!   (exists only inside one operation, has a maximum lifetime, and stops by
//!   itself; with 0 sessions and 0 operations none may be active);
//! - counts its wakeups and active operations, read on demand by
//!   [`snapshot`] (no sampling loop);
//! - is listed in `docs/perf/timers.md`; a test fails if the list and this
//!   file disagree.
//!
//! To add a timer: declare a `static` here with [`Timer::operation`] or
//! [`Timer::resident`], add it to [`ALL`], and run
//! `AIBD_WRITE_TIMERS_DOC=1 cargo test -p aibd timers` to regenerate the doc.
//! An async variant is added here when the first async timer appears.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Resident,
    Operation,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Resident => "resident",
            Kind::Operation => "operation",
        }
    }
}

pub struct Timer {
    pub name: &'static str,
    pub kind: Kind,
    pub period: Duration,
    /// Operation timers: the longest one operation may keep this timer
    /// alive. `Duration::MAX` for resident timers.
    pub max_lifetime: Duration,
    pub purpose: &'static str,
    wakeups: AtomicU64,
    active: AtomicU64,
}

impl Timer {
    const fn operation(
        name: &'static str,
        period: Duration,
        max_lifetime: Duration,
        purpose: &'static str,
    ) -> Self {
        Self {
            name,
            kind: Kind::Operation,
            period,
            max_lifetime,
            purpose,
            wakeups: AtomicU64::new(0),
            active: AtomicU64::new(0),
        }
    }

    const fn resident(name: &'static str, period: Duration, purpose: &'static str) -> Self {
        Self {
            name,
            kind: Kind::Resident,
            period,
            max_lifetime: Duration::MAX,
            purpose,
            wakeups: AtomicU64::new(0),
            active: AtomicU64::new(0),
        }
    }

    /// Start one operation that may tick this timer until `deadline` or the
    /// timer's maximum lifetime, whichever comes first.
    pub fn begin(&'static self, deadline: Option<Instant>) -> Op {
        self.active.fetch_add(1, Ordering::Relaxed);
        let cap = Instant::now().checked_add(self.max_lifetime);
        let end = match (deadline, cap) {
            (Some(d), Some(c)) => Some(d.min(c)),
            (d, c) => d.or(c),
        };
        Op { timer: self, end }
    }

    pub fn wakeups(&self) -> u64 {
        self.wakeups.load(Ordering::Relaxed)
    }

    pub fn active(&self) -> u64 {
        self.active.load(Ordering::Relaxed)
    }
}

/// One running operation of an `Operation` timer. Dropping it ends the
/// operation.
pub struct Op {
    timer: &'static Timer,
    end: Option<Instant>,
}

impl Op {
    /// Sleep one period (shorter if the end is closer). Returns `false`, without
    /// sleeping, once the end has been reached: the caller must stop.
    pub fn tick(&self) -> bool {
        let now = Instant::now();
        let step = match self.end {
            Some(end) if now >= end => return false,
            Some(end) => self.timer.period.min(end - now),
            None => self.timer.period,
        };
        self.timer.wakeups.fetch_add(1, Ordering::Relaxed);
        #[allow(clippy::disallowed_methods)] // the one permitted sleep
        std::thread::sleep(step);
        true
    }
}

impl Op {
    /// [`Op::tick`] that a state change can end early: returns as soon as
    /// the counter behind `m` is no longer `*seen` (and stores the new value
    /// in `*seen`), else after one period. Same end rule as `tick`.
    pub fn tick_or_wake(&self, m: &Mutex<u64>, cv: &Condvar, seen: &mut u64) -> bool {
        let now = Instant::now();
        let step = match self.end {
            Some(end) if now >= end => return false,
            Some(end) => self.timer.period.min(end - now),
            None => self.timer.period,
        };
        self.timer.wakeups.fetch_add(1, Ordering::Relaxed);
        let g = m.lock().unwrap_or_else(|e| e.into_inner());
        // ADR 0005 named exception: this registered timer's own tick, which
        // a state change may end early (A11-2, Codex M4)
        #[allow(clippy::disallowed_methods)]
        let (g, _) = cv
            .wait_timeout_while(g, step, |n| *n == *seen)
            .unwrap_or_else(|e| e.into_inner());
        *seen = *g;
        true
    }
}

impl Drop for Op {
    fn drop(&mut self) {
        self.timer.active.fetch_sub(1, Ordering::Relaxed);
    }
}

const fn ms(n: u64) -> Duration {
    Duration::from_millis(n)
}

const fn secs(n: u64) -> Duration {
    Duration::from_secs(n)
}

pub static TREE_CANCEL_WAIT: Timer = Timer::operation(
    "tree.cancel_wait",
    ms(50),
    secs(30),
    "re-check a signalled process tree until it is gone (ADR 0004 cancel steps)",
);

/// Every timer aibd has. A test checks that nothing is declared but missing.
pub static SESSION_SLOT_RESCAN: Timer = Timer::operation(
    "session.slot_rescan",
    secs(2),
    secs(30),
    "re-check a stopped session whose stop could not show the tree gone, so its slot can come back (a63ab 11-09)",
);

pub static SELFCHECK_CHILD_WAIT: Timer = Timer::operation(
    "selfcheck.child_wait",
    ms(20),
    secs(3),
    "wait for the selfcheck tree's child to report it started, before stopping the tree (bb7dc #549)",
);

pub static SESSION_REJECT_FLUSH: Timer = Timer::operation(
    "session.reject_flush",
    secs(60),
    secs(61),
    "when a window of folded spawn rejections closes, write how many were folded (a63ab 11-09 19:23, 1ced7 #593 M1)",
);

pub static SELFCHECK_LEAF_LIFE: Timer = Timer::operation(
    "selfcheck.leaf_life",
    secs(30),
    secs(30),
    "the self-exec check tree's child stays for the stop to meet, then ends by itself (one wake-up; a9136 11-09)",
);

pub static STARTUP_LOCK_WAIT: Timer = Timer::operation(
    "startup.lock_wait",
    ms(100),
    secs(7),
    "at start, re-try aibd.lock while a program held before exec may still keep a copy of it (macOS, #614)",
);

pub static THERMAL_SAMPLE: Timer = Timer::operation(
    "thermal_sample",
    secs(5),
    secs(12 * 3600),
    "sample CPU die temperature and thermal pressure while aibd's own processes run; begun again at 12 h without a gap (ADR 0005 §1 exception, R1)",
);

pub static ALIVE_DUE_WAIT: Timer = Timer::operation(
    "alive.due_wait",
    secs(60),
    secs(86_400),
    "only while a down seat waits for its alert threshold: wake at the earliest due time, report, stop (A11 T46, 2240e 11-13)",
);

/// The first resident timer: off unless AIBD_DRIVE_WAKE=1 (crate::drive_wake), so an idle
/// aibd that was not asked for it still has no wakeups.
pub static DRIVE_WAKE: Timer = Timer::resident(
    "drive.wake",
    secs(600),
    "only with AIBD_DRIVE_WAKE=1: run aib-drive event tick --by aibd, the 10-minute floor under aib-drive's event wakes (S79 A2, 7b81e 11-15)",
);

/// One drive wake's child: re-check it until it exits; at the limit it is killed (5d33a A2 L1).
pub static DRIVE_WAKE_CHILD_WAIT: Timer = Timer::operation(
    "drive.wake_child_wait",
    ms(200),
    secs(61),
    "only during a drive wake: re-check aib-drive event tick until it exits, kill it at 60 s so a hung child cannot stop the floor (S79 A2 L1, 7b81e 11-15)",
);

/// Keeps aib-drive's caches fresh with no window open (519d0 11-15 07:26): same switch as
/// DRIVE_WAKE; each run of `aib-drive refresh --auto` decides itself whether to scan.
pub static DRIVE_REFRESH: Timer = Timer::resident(
    "drive.refresh",
    secs(300),
    "only with AIBD_DRIVE_WAKE=1: run aib-drive refresh --auto, which scans only when the cache and the last try are 20 minutes old and GitHub has 1500 calls left (S79 B3-1, a9136 11-15)",
);

/// One drive refresh's child: a scan can take minutes; at the limit it is killed.
pub static DRIVE_REFRESH_CHILD_WAIT: Timer = Timer::operation(
    "drive.refresh_child_wait",
    secs(1),
    secs(31 * 60),
    "only during a drive refresh: re-check aib-drive refresh --auto until it exits, kill it at 31 min (its own scan limit is 30) so a hung scan cannot stop the refresh (S79 B3-1, a9136 11-15)",
);

/// One drive act's child (519d0 11-15, idea A of 56cda): after each wake the same thread runs
/// `aib-drive act --auto`, so nudges do not stop when no observe --watch runs; at the limit
/// it is killed.
pub static DRIVE_ACT_CHILD_WAIT: Timer = Timer::operation(
    "drive.act_child_wait",
    secs(1),
    secs(181),
    "only during a drive wake: re-check aib-drive act --auto (next, seat facts, one act round; every act switch off = a dry run) until it exits, kill it at 180 s so a hung round cannot stop the floor (519d0 11-15, 56cda)",
);

pub static SEAT_INPUT_WRITE_WAIT: Timer = Timer::operation(
    "seat_input.write_wait",
    secs(5),
    secs(5),
    "only while a typed text is written to a seat's stdin: wait for the write, answer 202 delivery pending at 5 s (S89-input L1, 5d33a / 0eed7; M1 on 29e68425)",
);

/// One machine's probe for GET /api/v1/hosts: at the limit its ssh is killed.
pub static HOSTS_PROBE_WAIT: Timer = Timer::operation(
    "hosts.probe_wait",
    ms(200),
    secs(16),
    "only during GET /api/v1/hosts: re-check each machine ssh (connect timeout 5 s) until it exits, kill it at 15 s so a hung machine cannot hold the answer; one round a minute at most (N-workspace wave 3, a9136 11-18)",
);

/// N-ws-preview: the preview build writes its port.
pub static PREVIEW_PORT_WAIT: Timer = Timer::operation(
    "preview.port_wait",
    ms(100),
    secs(20),
    "only while the page turns the preview on: re-read the preview build's run/aibd.json until it names its port, give up (and stop it) at 20 s (SPEC §20, a9136 11-18)",
);

/// N-ws-preview: the preview build ends after TERM.
pub static PREVIEW_STOP_WAIT: Timer = Timer::operation(
    "preview.stop_wait",
    ms(100),
    secs(10),
    "only while the preview is stopped: re-check the preview build after TERM until it has ended, kill it at 10 s (SPEC §20, a9136 11-18)",
);

/// N-web-dmbridge (519d0 11-19): the 1.x mail ledger mirrored into the message library.
pub static DM_MIRROR: Timer = Timer::resident(
    "dm.mirror",
    secs(60),
    "only with dm_mirror true in <data>/bridge.json: read the 1.x mail ledger files changed since the last pass into the message library (new live letters, status moves), read only (N-web-dmbridge, 7ebca 11-19)",
);

pub static ALL: &[&Timer] = &[
    &TREE_CANCEL_WAIT,
    &SESSION_SLOT_RESCAN,
    &SELFCHECK_CHILD_WAIT,
    &SESSION_REJECT_FLUSH,
    &SELFCHECK_LEAF_LIFE,
    &STARTUP_LOCK_WAIT,
    &THERMAL_SAMPLE,
    &ALIVE_DUE_WAIT,
    &DRIVE_WAKE,
    &DRIVE_WAKE_CHILD_WAIT,
    &DRIVE_REFRESH,
    &DRIVE_REFRESH_CHILD_WAIT,
    &DRIVE_ACT_CHILD_WAIT,
    &SEAT_INPUT_WRITE_WAIT,
    &HOSTS_PROBE_WAIT,
    &PREVIEW_PORT_WAIT,
    &PREVIEW_STOP_WAIT,
    &DM_MIRROR,
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Sample {
    pub name: &'static str,
    pub kind: Kind,
    pub wakeups: u64,
    pub active: u64,
}

/// Counters for every timer, computed when asked.
pub fn snapshot() -> Vec<Sample> {
    ALL.iter()
        .map(|t| Sample {
            name: t.name,
            kind: t.kind,
            wakeups: t.wakeups(),
            active: t.active(),
        })
        .collect()
}

/// Operations currently holding an `Operation` timer. Must be 0 when idle.
pub fn active_operations() -> u64 {
    ALL.iter()
        .filter(|t| t.kind == Kind::Operation)
        .map(|t| t.active())
        .sum()
}

fn human(d: Duration) -> String {
    if d == Duration::MAX {
        "—".into()
    } else if d.as_millis() < 1000 {
        format!("{} ms", d.as_millis())
    } else if d.as_secs().is_multiple_of(3600) {
        format!("{} h", d.as_secs() / 3600)
    } else {
        format!("{} s", d.as_secs())
    }
}

/// The table in `docs/perf/timers.md`, generated from [`ALL`].
pub fn render_doc() -> String {
    let mut s = String::from(
        "# aibd timers\n\n\
         Generated from `crates/aibd/src/timers.rs` (ADR 0005). Do not edit by hand:\n\
         run `AIBD_WRITE_TIMERS_DOC=1 cargo test -p aibd timers`.\n\n\
         | Name | Kind | Period | Max lifetime | Purpose |\n\
         |---|---|---|---|---|\n",
    );
    for t in ALL {
        s.push_str(&format!(
            "| `{}` | {} | {} | {} | {} |\n",
            t.name,
            t.kind.name(),
            human(t.period),
            human(t.max_lifetime),
            t.purpose
        ));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn timers_follow_the_rules() {
        let mut names = BTreeSet::new();
        for t in ALL {
            assert!(names.insert(t.name), "duplicate timer name {}", t.name);
            match t.kind {
                Kind::Resident => assert!(
                    t.period >= Duration::from_secs(30),
                    "{}: resident timers need a period of at least 30 s",
                    t.name
                ),
                Kind::Operation => assert!(
                    t.max_lifetime < Duration::MAX && t.max_lifetime > Duration::ZERO,
                    "{}: operation timers need a finite maximum lifetime",
                    t.name
                ),
            }
        }
    }

    #[test]
    fn timers_every_declared_timer_is_registered() {
        // Windows checkouts may turn LF into CRLF (core.autocrlf): compare on LF
        let src = include_str!("timers.rs").replace("\r\n", "\n");
        let body = src.split("#[cfg(test)]").next().unwrap_or(&src);
        let declared = body.matches("Timer::operation(\n").count()
            + body.matches("Timer::resident(\n").count();
        assert_eq!(declared, ALL.len(), "a timer is declared but not in ALL");
    }

    #[test]
    fn timers_doc_matches_the_registry() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/perf/timers.md");
        let want = render_doc();
        if std::env::var_os("AIBD_WRITE_TIMERS_DOC").is_some() {
            std::fs::write(path, &want).unwrap();
        }
        let have = std::fs::read_to_string(path)
            .unwrap_or_default()
            .replace("\r\n", "\n"); // Windows checkouts may turn LF into CRLF
        assert_eq!(
            have, want,
            "docs/perf/timers.md is out of date: run AIBD_WRITE_TIMERS_DOC=1 cargo test -p aibd timers"
        );
    }

    #[test]
    fn timers_operation_stops_at_its_end_and_releases() {
        let before = TREE_CANCEL_WAIT.active();
        let op = TREE_CANCEL_WAIT.begin(Some(Instant::now()));
        assert_eq!(TREE_CANCEL_WAIT.active(), before + 1);
        let w = TREE_CANCEL_WAIT.wakeups();
        assert!(!op.tick(), "tick after the end must refuse");
        assert_eq!(
            TREE_CANCEL_WAIT.wakeups(),
            w,
            "a refused tick is not a wakeup"
        );
        drop(op);
        assert_eq!(TREE_CANCEL_WAIT.active(), before);
    }
}
