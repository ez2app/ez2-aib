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

    #[allow(dead_code)] // no resident timer yet; the constructor is the rule
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
pub static ALL: &[&Timer] = &[&TREE_CANCEL_WAIT];

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
        let src = include_str!("timers.rs");
        let body = src.split("#[cfg(test)]").next().unwrap_or(src);
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
        let have = std::fs::read_to_string(path).unwrap_or_default();
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
