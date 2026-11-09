//! macOS: wait for recorded processes to exit on kqueue `NOTE_EXIT` events
//! instead of polling the whole process table every 50 ms (issue #514, 043dd
//! #510 M4, a9136 11-09).
//!
//! One kqueue per wait. Every process the wait still sees alive is watched
//! once (`EV_ADD | EV_ONESHOT`, `NOTE_EXIT`); the wait then blocks in
//! `kevent` until one of them exits or the operation's end, and the caller
//! re-reads the process table once per wake-up.
//!
//! Nothing is assumed: a process that exited between the read and the
//! registration answers `ESRCH`, which counts as an event (read again at
//! once); any other registration error makes [`ExitWatch::watch`] return
//! `false` and the caller falls back to its ticking wait for this call, so a
//! process that cannot be watched is still checked, never taken as gone.
//! Identity checks are unchanged: this only decides when to look again.

use nix::errno::Errno;
use nix::sys::event::{EvFlags, EventFilter, FilterFlag, KEvent, Kqueue};
use nix::sys::time::TimeSpec;
use std::collections::BTreeSet;
use std::time::Duration;

pub struct ExitWatch {
    kq: Kqueue,
    watched: BTreeSet<u32>,
    /// A watched process was already gone when registered: look again now.
    pending: bool,
}

impl ExitWatch {
    pub fn new() -> std::io::Result<Self> {
        Ok(Self {
            kq: Kqueue::new().map_err(std::io::Error::from)?,
            watched: BTreeSet::new(),
            pending: false,
        })
    }

    /// Watch every pid not watched yet. `false` = one could not be watched
    /// (not `ESRCH`): the caller must not block on events for this round.
    pub fn watch(&mut self, pids: impl IntoIterator<Item = u32>) -> bool {
        for pid in pids {
            if self.watched.contains(&pid) {
                continue;
            }
            let Ok(ident) = usize::try_from(pid) else {
                return false;
            };
            let ev = KEvent::new(
                ident,
                EventFilter::EVFILT_PROC,
                EvFlags::EV_ADD | EvFlags::EV_ONESHOT,
                FilterFlag::NOTE_EXIT,
                0,
                0,
            );
            match self.kq.kevent(&[ev], &mut [], None) {
                Ok(_) => {
                    self.watched.insert(pid);
                }
                // already exited: that is the event we wait for
                Err(Errno::ESRCH) => {
                    self.watched.insert(pid);
                    self.pending = true;
                }
                Err(_) => return false,
            }
        }
        true
    }

    /// Block until a watched process exits or `limit` passes. Returns at
    /// once when an exit was already seen at registration.
    pub fn wait(&mut self, limit: Duration) {
        if std::mem::take(&mut self.pending) {
            return;
        }
        let blank = KEvent::new(
            0,
            EventFilter::EVFILT_PROC,
            EvFlags::empty(),
            FilterFlag::empty(),
            0,
            0,
        );
        let mut buf = [blank; 16];
        // The one clock here is the operation's end (registered timer
        // `tree.cancel_wait`, ADR 0005 named exception): kevent wakes on an
        // exit, or at that end, never on a period.
        let _ = self.kq.kevent(
            &[],
            &mut buf,
            Some(*TimeSpec::from_duration(limit).as_ref()),
        );
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use std::time::Instant;

    #[test]
    fn an_exit_wakes_the_wait_long_before_its_limit() {
        let mut c = std::process::Command::new("/bin/sleep")
            .arg("0.2")
            .spawn()
            .unwrap();
        let mut w = ExitWatch::new().unwrap();
        assert!(w.watch([c.id()]));
        let t = Instant::now();
        w.wait(Duration::from_secs(10));
        let took = t.elapsed();
        let _ = c.wait();
        assert!(
            took < Duration::from_secs(3),
            "woke only at the limit: {took:?}"
        );
    }

    #[test]
    fn a_pid_already_gone_is_an_event_not_a_block() {
        let mut c = std::process::Command::new("/usr/bin/true").spawn().unwrap();
        let pid = c.id();
        let _ = c.wait(); // reaped: the pid no longer exists
        let mut w = ExitWatch::new().unwrap();
        assert!(w.watch([pid]));
        let t = Instant::now();
        w.wait(Duration::from_secs(10));
        assert!(
            t.elapsed() < Duration::from_secs(1),
            "blocked on a gone pid"
        );
    }

    #[test]
    fn the_limit_ends_a_wait_with_no_exit() {
        let mut c = std::process::Command::new("/bin/sleep")
            .arg("30")
            .spawn()
            .unwrap();
        let mut w = ExitWatch::new().unwrap();
        assert!(w.watch([c.id()]));
        let t = Instant::now();
        w.wait(Duration::from_millis(300));
        let took = t.elapsed();
        let _ = c.kill();
        let _ = c.wait();
        assert!(
            took >= Duration::from_millis(250) && took < Duration::from_secs(3),
            "{took:?}"
        );
    }
}
