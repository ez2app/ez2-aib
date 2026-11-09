//! Event-driven process-tree watcher (ADR 0004 update, ADR 0005).
//!
//! macOS: a kqueue `EVFILT_PROC` watch with `NOTE_FORK | NOTE_EXIT` on every
//! recorded pid. On each fork the parent's children are listed by parent pid
//! (aib-sys M3, DECISION-10 B: errno cleared before the size query and every
//! fill, a list that still does not fit is an error, never a truncated list)
//! and watched too, recursively. The thread blocks in `kevent` with no
//! timeout; it wakes only on a fork, an exit, or the stop event.
//!
//! Known blind spots (stated, not hidden; measure them with calibration):
//! - kqueue coalesces: several forks of one parent between two reads arrive
//!   as one event, so `fork_events` is a lower bound;
//! - a child that forks and exits before it is listed is never watched, so
//!   its own children are not seen (`children_adopted` is a lower bound);
//! - whether `posix_spawn` raises `NOTE_FORK` is measured, not assumed.
//!
//! Other OSes: [`ForkWatch::start`] returns `Unsupported`.
//!
//! Safe code only (`nix`, `libproc` and aib-sys wrappers); no `unsafe` here.
//! aib-sys types stay private: callers (aib-forkcount) only see counters and
//! tags, so the dependency rule "aib-sys only through aib-procwatch" holds.

#![forbid(unsafe_code)]

use std::collections::BTreeMap;

/// Counters, read on demand.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Stats {
    /// `NOTE_FORK` events received (coalesced: a lower bound of forks).
    pub fork_events: u64,
    /// New descendants recorded (a lower bound of processes created).
    pub children_adopted: u64,
    /// Watches that could not be registered (process already gone, or refused).
    pub register_failures: u64,
    /// Child listings that failed (any error: permission, OS error, a list
    /// that did not fit after retries, …). Never washed into an empty list.
    pub list_failures: u64,
    /// The same failures by aib-sys tag (`permission_denied`, `incomplete`,
    /// `os_error`, `unknown`, `gone`, …). Report only.
    pub list_failures_by_tag: BTreeMap<String, u64>,
    /// Listings that returned zero children. Report only (an empty list is
    /// normal for a process with no children; DECISION-10 kept it out of the
    /// verdict).
    pub empty_lists: u64,
    /// `NOTE_FORK` events after which the parent's listing adopted no new
    /// child. Report only: events coalesce and a child may exit or be
    /// re-parented before it is listed, so this is not a loss count and has
    /// no threshold until the attribution window is defined (DECISION-10 3).
    pub fork_without_child: u64,
    /// Listings where a fault was injected (`ListFault`); non-zero means the
    /// run is a test of the error paths, never a measurement.
    pub injected: u64,
    /// Fork events by the parent's program name.
    pub by_parent: BTreeMap<String, u64>,
    /// Fork events by the parent's pid (lets calibration attribute events to
    /// the processes it created itself).
    pub fork_events_by_pid: BTreeMap<u32, u64>,
    /// Fork events per whole second since start.
    pub per_second: BTreeMap<u64, u64>,
}

/// Fault injection for the child listing (tests and `aib-forkcount
/// calibrate --inject-list`). Every fault makes a run look worse, never
/// better, and is counted in [`Stats::injected`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListFault {
    /// The `nth` listing (1-based) fails with an OS error.
    Error { nth: u64 },
    /// The `nth` listing (1-based) did not fit after retries
    /// (aib-sys `Incomplete`).
    Incomplete { nth: u64 },
    /// The first listing that has children drops its first child, and every
    /// later listing drops that pid too (a missed child: it is never adopted,
    /// so neither are its own children).
    HideOne,
}

#[cfg(target_os = "macos")]
mod imp {
    use super::{ListFault, Stats};
    use aib_sys::{Macos, ProcessInfo, SysError};
    use libproc::libproc::bsd_info::BSDInfo;
    use libproc::libproc::proc_pid::{name, pidinfo};
    use nix::errno::Errno;
    use nix::sys::event::{EvFlags, EventFilter, FilterFlag, KEvent, Kqueue};
    use std::collections::BTreeMap;
    use std::sync::{Arc, Mutex, MutexGuard};
    use std::thread::JoinHandle;
    use std::time::Instant;

    const STOP_IDENT: usize = 1;

    struct Inner {
        seen: BTreeMap<u32, u64>,
        /// adopted child -> the parent it was listed under
        parent_of: BTreeMap<u32, u32>,
        stats: Stats,
        fault: Option<ListFault>,
        /// listings done so far (for `ListFault::Error` / `Incomplete`)
        listings: u64,
        hidden: Option<u32>,
    }

    type Shared = Mutex<Inner>;

    fn lock(m: &Shared) -> MutexGuard<'_, Inner> {
        m.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Start time in seconds (`pbi_start_tvsec`, the same source sysinfo
    /// uses on macOS, so records compare equal).
    pub fn start_time(pid: u32) -> Option<u64> {
        let info = pidinfo::<BSDInfo>(i32::try_from(pid).ok()?, 0).ok()?;
        Some(info.pbi_start_tvsec)
    }

    pub fn program_name(pid: u32) -> Option<String> {
        name(i32::try_from(pid).ok()?).ok()
    }

    fn watch(kq: &Kqueue, pid: u32) -> bool {
        let ev = KEvent::new(
            pid as usize,
            EventFilter::EVFILT_PROC,
            EvFlags::EV_ADD | EvFlags::EV_CLEAR,
            FilterFlag::NOTE_FORK | FilterFlag::NOTE_EXIT,
            0,
            0,
        );
        kq.kevent(&[ev], &mut [], None).is_ok()
    }

    /// One child listing through aib-sys M3, with the injected fault if any.
    fn list_children(sh: &Shared, ppid: u32) -> Result<Vec<u32>, SysError> {
        let (n, fault) = {
            let mut g = lock(sh);
            g.listings += 1;
            (g.listings, g.fault)
        };
        match fault {
            Some(ListFault::Error { nth }) if nth == n => {
                lock(sh).stats.injected += 1;
                return Err(SysError::Os(5)); // EIO
            }
            Some(ListFault::Incomplete { nth }) if nth == n => {
                lock(sh).stats.injected += 1;
                return Err(SysError::Incomplete);
            }
            _ => {}
        }
        let mut kids = Macos.children(ppid)?;
        if fault == Some(ListFault::HideOne) {
            let mut g = lock(sh);
            let hidden = g.hidden;
            match hidden {
                None if !kids.is_empty() => {
                    g.hidden = Some(kids.remove(0));
                    g.stats.injected += 1;
                }
                Some(h) => kids.retain(|k| *k != h),
                None => {}
            }
        }
        Ok(kids)
    }

    /// Record and watch every not-yet-known descendant of `parent`.
    /// Returns how many new **direct** children of `parent` were adopted.
    fn adopt(kq: &Kqueue, sh: &Shared, parent: u32) -> u64 {
        let mut direct = 0;
        let mut stack = vec![parent];
        while let Some(p) = stack.pop() {
            let pstart = lock(sh).seen.get(&p).copied();
            // Every error counts as a failure, whatever its tag: "gone" is
            // not proof that nothing was missed (bb7dc, DECISION-10).
            let kids = match list_children(sh, p) {
                Ok(k) => k,
                Err(e) => {
                    let mut g = lock(sh);
                    g.stats.list_failures += 1;
                    *g.stats
                        .list_failures_by_tag
                        .entry(e.tag().to_owned())
                        .or_default() += 1;
                    continue;
                }
            };
            if kids.is_empty() {
                lock(sh).stats.empty_lists += 1;
            }
            for k in kids {
                if k == 0 || lock(sh).seen.contains_key(&k) {
                    continue;
                }
                let Some(ks) = start_time(k) else { continue };
                // A child cannot be older than its recorded parent; an older
                // one means the parent pid was reused.
                if pstart.is_some_and(|ps| ks < ps) {
                    continue;
                }
                let ok = watch(kq, k);
                let mut g = lock(sh);
                g.seen.insert(k, ks);
                g.parent_of.insert(k, p);
                g.stats.children_adopted += 1;
                if !ok {
                    g.stats.register_failures += 1;
                }
                drop(g);
                if p == parent {
                    direct += 1;
                }
                stack.push(k);
            }
        }
        direct
    }

    pub struct ForkWatch {
        kq: Arc<Kqueue>,
        sh: Arc<Shared>,
        thread: Option<JoinHandle<()>>,
    }

    impl ForkWatch {
        pub fn start(roots: &[u32]) -> std::io::Result<Self> {
            Self::start_with(roots, None)
        }

        /// [`start`](Self::start) with a fault injected into the child
        /// listing (see [`ListFault`]).
        pub fn start_with(roots: &[u32], fault: Option<ListFault>) -> std::io::Result<Self> {
            let kq = Arc::new(Kqueue::new().map_err(std::io::Error::from)?);
            let stop = KEvent::new(
                STOP_IDENT,
                EventFilter::EVFILT_USER,
                EvFlags::EV_ADD | EvFlags::EV_CLEAR,
                FilterFlag::empty(),
                0,
                0,
            );
            kq.kevent(&[stop], &mut [], None)
                .map_err(std::io::Error::from)?;
            let sh = Arc::new(Mutex::new(Inner {
                seen: BTreeMap::new(),
                parent_of: BTreeMap::new(),
                stats: Stats::default(),
                fault,
                listings: 0,
                hidden: None,
            }));
            let w = {
                let (kq, sh) = (kq.clone(), sh.clone());
                let t0 = Instant::now();
                std::thread::Builder::new()
                    .name("aib-procwatch".into())
                    .spawn(move || run(&kq, &sh, t0))?
            };
            let me = Self {
                kq,
                sh,
                thread: Some(w),
            };
            for r in roots {
                me.add_root(*r);
            }
            Ok(me)
        }

        /// Watch one more root and everything already under it. Returns
        /// false if the root is gone or the watch was refused.
        pub fn add_root(&self, pid: u32) -> bool {
            let Some(st) = start_time(pid) else {
                lock(&self.sh).stats.register_failures += 1;
                return false;
            };
            let ok = watch(&self.kq, pid);
            {
                let mut g = lock(&self.sh);
                g.seen.insert(pid, st);
                if !ok {
                    g.stats.register_failures += 1;
                }
            }
            // The root may have forked before the watch was registered.
            adopt(&self.kq, &self.sh, pid);
            ok
        }

        /// Every process recorded so far, with its start time.
        pub fn snapshot(&self) -> Vec<(u32, u64)> {
            lock(&self.sh).seen.iter().map(|(p, s)| (*p, *s)).collect()
        }

        pub fn stats(&self) -> Stats {
            lock(&self.sh).stats.clone()
        }

        /// Every adopted child with the parent pid it was listed under
        /// (roots are not included).
        pub fn adopted_parents(&self) -> BTreeMap<u32, u32> {
            lock(&self.sh).parent_of.clone()
        }
    }

    fn run(kq: &Kqueue, sh: &Shared, t0: Instant) {
        let blank = KEvent::new(
            0,
            EventFilter::EVFILT_USER,
            EvFlags::empty(),
            FilterFlag::empty(),
            0,
            0,
        );
        let mut buf = [blank; 64];
        loop {
            let n = match kq.kevent(&[], &mut buf, None) {
                Ok(n) => n,
                Err(Errno::EINTR) => continue,
                Err(_) => return,
            };
            for ev in &buf[..n] {
                match ev.filter() {
                    Ok(EventFilter::EVFILT_USER) if ev.ident() == STOP_IDENT => return,
                    Ok(EventFilter::EVFILT_PROC) if ev.fflags().contains(FilterFlag::NOTE_FORK) => {
                        let Ok(pid) = u32::try_from(ev.ident()) else {
                            continue;
                        };
                        let parent = program_name(pid).unwrap_or_else(|| "?".into());
                        {
                            let mut g = lock(sh);
                            g.stats.fork_events += 1;
                            *g.stats.fork_events_by_pid.entry(pid).or_default() += 1;
                            *g.stats.by_parent.entry(parent).or_default() += 1;
                            *g.stats
                                .per_second
                                .entry(t0.elapsed().as_secs())
                                .or_default() += 1;
                        }
                        if adopt(kq, sh, pid) == 0 {
                            lock(sh).stats.fork_without_child += 1;
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    impl ForkWatch {
        /// Stop watching and join the watcher thread (same as dropping it).
        pub fn stop(self) {
            drop(self);
        }
    }

    impl Drop for ForkWatch {
        fn drop(&mut self) {
            let trigger = KEvent::new(
                STOP_IDENT,
                EventFilter::EVFILT_USER,
                EvFlags::empty(),
                FilterFlag::NOTE_TRIGGER,
                0,
                0,
            );
            if self.kq.kevent(&[trigger], &mut [], None).is_ok() {
                if let Some(t) = self.thread.take() {
                    let _ = t.join();
                }
            }
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod imp {
    use super::{ListFault, Stats};

    pub fn start_time(_pid: u32) -> Option<u64> {
        None
    }

    pub fn program_name(_pid: u32) -> Option<String> {
        None
    }

    /// Not available on this OS yet (Windows: Job Object messages, ADR 0005).
    pub struct ForkWatch;

    impl ForkWatch {
        pub fn start(roots: &[u32]) -> std::io::Result<Self> {
            Self::start_with(roots, None)
        }
        pub fn start_with(_roots: &[u32], _fault: Option<ListFault>) -> std::io::Result<Self> {
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "aib-procwatch: only macOS is implemented",
            ))
        }
        pub fn add_root(&self, _pid: u32) -> bool {
            false
        }
        pub fn snapshot(&self) -> Vec<(u32, u64)> {
            Vec::new()
        }
        pub fn stats(&self) -> Stats {
            Stats::default()
        }
        pub fn adopted_parents(&self) -> std::collections::BTreeMap<u32, u32> {
            std::collections::BTreeMap::new()
        }
        /// Stop watching (nothing to stop here).
        pub fn stop(self) {}
    }
}

pub use imp::{program_name, start_time, ForkWatch};
