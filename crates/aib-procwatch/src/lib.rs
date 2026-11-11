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
    /// Children seen in a listing whose start time could not be read (they
    /// had already exited). Kept, not watched, no identity check (a63ab
    /// 11-10: they are forks the watcher saw, not a blind spot).
    pub children_start_unknown: u64,
    /// Children seen in a listing whose start time was refused (EPERM: still
    /// running, not readable by us, e.g. setuid). Not counted, not watched;
    /// reported so a caller can mark the run unverified (a9136 #624 L1).
    pub children_start_denied: u64,
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
    /// The `nth` listing (1-based): the fill call fails with EIO. Goes
    /// through aib-sys's real `read_pid_list`, so its error classification is
    /// what is tested, not a made-up result.
    Error { nth: u64 },
    /// The `nth` listing (1-based): every fill comes back full. aib-sys's
    /// real `read_pid_list` grows and retries, then refuses with
    /// `Incomplete` (a truncated list must never be returned).
    Incomplete { nth: u64 },
    /// The first listing that has children drops its first child, and every
    /// later listing drops that pid too (a missed child: it is never adopted,
    /// so neither are its own children).
    HideOne,
}

/// Most trace events kept per watch; later ones are counted in
/// [`Trace::dropped`] (a run with drops cannot explain its misses).
pub const TRACE_CAP: usize = 200_000;

/// One observation of the watcher, with the monotonic time it was made
/// ([`uptime_ns`], the same clock in every process). Calibration only.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TraceEvent {
    pub t_ns: u64,
    pub pid: u32,
    pub kind: TraceKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TraceKind {
    /// `NOTE_FORK` of `pid` was read; `exited` = `NOTE_EXIT` came in the same
    /// event, so `pid` had already exited when the event was read.
    Fork { exited: bool },
    /// `NOTE_EXIT` of `pid` was read (on its own).
    Exit,
    /// The children of `pid` were listed from `t_ns` to `end_ns`: the list,
    /// or the aib-sys error tag.
    List {
        end_ns: u64,
        result: Result<Vec<u32>, String>,
    },
    /// `child` was listed under `pid` but not recorded: its start time could
    /// not be read (it had already exited), or it is older than `pid` (pid
    /// reuse).
    NotAdopted { child: u32, why: NotAdoptedWhy },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NotAdoptedWhy {
    StartUnreadable,
    /// the start time read was refused (EPERM): the child is alive
    StartDenied,
    OlderThanParent,
}

/// errno in libproc 0.15's error text (`"…, errno = N, message = …"`).
pub fn errno_in(message: &str) -> Option<i32> {
    let rest = message.split("errno = ").nth(1)?;
    let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
    digits.parse().ok()
}

/// What [`ForkWatch::trace`] returns.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Trace {
    pub events: Vec<TraceEvent>,
    /// events not kept because [`TRACE_CAP`] was reached
    pub dropped: u64,
    /// false when the watch was started without a trace
    pub enabled: bool,
}

#[cfg(target_os = "macos")]
mod imp {
    use super::{ListFault, NotAdoptedWhy, Stats, Trace, TraceEvent, TraceKind, TRACE_CAP};
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
        /// listed child whose start time was unreadable -> its parent
        start_unknown: BTreeMap<u32, u32>,
        start_denied: std::collections::BTreeSet<u32>,
        stats: Stats,
        fault: Option<ListFault>,
        /// listings done so far (for `ListFault::Error` / `Incomplete`)
        listings: u64,
        hidden: Option<u32>,
        trace: Trace,
    }

    impl Inner {
        fn record(&mut self, t_ns: u64, pid: u32, kind: TraceKind) {
            if !self.trace.enabled {
                return;
            }
            if self.trace.events.len() >= TRACE_CAP {
                self.trace.dropped += 1;
            } else {
                self.trace.events.push(TraceEvent { t_ns, pid, kind });
            }
        }
    }

    /// `CLOCK_UPTIME_RAW` in nanoseconds: monotonic, shared by every process
    /// on the machine (so a child can stamp its own events on the same clock).
    pub fn uptime_ns() -> Option<u64> {
        let t = nix::time::clock_gettime(nix::time::ClockId::from_raw(nix::libc::CLOCK_UPTIME_RAW))
            .ok()?;
        let s = u64::try_from(t.tv_sec()).ok()?;
        let n = u64::try_from(t.tv_nsec()).ok()?;
        s.checked_mul(1_000_000_000)?.checked_add(n)
    }

    type Shared = Mutex<Inner>;

    fn lock(m: &Shared) -> MutexGuard<'_, Inner> {
        m.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Start time in seconds (`pbi_start_tvsec`, the same source sysinfo
    /// uses on macOS, so records compare equal).
    pub fn start_time(pid: u32) -> Option<u64> {
        match read_start(pid) {
            StartRead::Known(s) => Some(s),
            StartRead::Gone | StartRead::Denied => None,
        }
    }

    /// A start time read, telling a refused read (EPERM, alive) apart from
    /// every other failure (taken as exited: ESRCH, or a zombie).
    enum StartRead {
        Known(u64),
        Gone,
        Denied,
    }

    fn read_start(pid: u32) -> StartRead {
        let Ok(p) = i32::try_from(pid) else {
            return StartRead::Gone;
        };
        match pidinfo::<BSDInfo>(p, 0) {
            Ok(info) => StartRead::Known(info.pbi_start_tvsec),
            Err(m) if super::errno_in(&m) == Some(1) => StartRead::Denied,
            Err(_) => StartRead::Gone,
        }
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

    /// [`list_children_raw`], recorded in the trace when it is on.
    fn list_children(sh: &Shared, ppid: u32) -> Result<Vec<u32>, SysError> {
        let t0 = uptime_ns().unwrap_or(0);
        let r = list_children_raw(sh, ppid);
        let mut g = lock(sh);
        if g.trace.enabled {
            let end_ns = uptime_ns().unwrap_or(0);
            let result = match &r {
                Ok(k) => Ok(k.clone()),
                Err(e) => Err(e.tag().to_owned()),
            };
            g.record(t0, ppid, TraceKind::List { end_ns, result });
        }
        r
    }

    /// One child listing through aib-sys M3, with the injected fault if any.
    fn list_children_raw(sh: &Shared, ppid: u32) -> Result<Vec<u32>, SysError> {
        let (n, fault) = {
            let mut g = lock(sh);
            g.listings += 1;
            (g.listings, g.fault)
        };
        match fault {
            Some(ListFault::Error { nth }) if nth == n => {
                lock(sh).stats.injected += 1;
                // the fill fails with EIO (5)
                return aib_sys::list::read_pid_list(1, |_| Err(5));
            }
            Some(ListFault::Incomplete { nth }) if nth == n => {
                lock(sh).stats.injected += 1;
                // the OS keeps filling whatever buffer it gets
                return aib_sys::list::read_pid_list(1, |buf| Ok(std::mem::size_of_val(buf)));
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
                let ks = match read_start(k) {
                    StartRead::Known(ks) => Some(ks),
                    StartRead::Gone => None,
                    StartRead::Denied => {
                        // Alive but not readable: not a fork we can name.
                        let t = uptime_ns().unwrap_or(0);
                        let mut g = lock(sh);
                        g.record(
                            t,
                            p,
                            TraceKind::NotAdopted {
                                child: k,
                                why: NotAdoptedWhy::StartDenied,
                            },
                        );
                        if g.start_denied.insert(k) {
                            g.stats.children_start_denied += 1;
                        }
                        continue;
                    }
                };
                let Some(ks) = ks else {
                    // It was in the list, so it was p's child; it exited
                    // before its start time could be read. Keep it (counted,
                    // marked start_unknown), never watch it: it is gone.
                    let t = uptime_ns().unwrap_or(0);
                    let mut g = lock(sh);
                    g.record(
                        t,
                        p,
                        TraceKind::NotAdopted {
                            child: k,
                            why: NotAdoptedWhy::StartUnreadable,
                        },
                    );
                    let fresh = match g.start_unknown.entry(k) {
                        std::collections::btree_map::Entry::Vacant(e) => {
                            e.insert(p);
                            true
                        }
                        std::collections::btree_map::Entry::Occupied(_) => false,
                    };
                    if fresh {
                        g.stats.children_start_unknown += 1;
                        drop(g);
                        if p == parent {
                            direct += 1;
                        }
                    }
                    continue;
                };
                // A child cannot be older than its recorded parent; an older
                // one means the parent pid was reused.
                if pstart.is_some_and(|ps| ks < ps) {
                    let t = uptime_ns().unwrap_or(0);
                    lock(sh).record(
                        t,
                        p,
                        TraceKind::NotAdopted {
                            child: k,
                            why: NotAdoptedWhy::OlderThanParent,
                        },
                    );
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
            Self::start_full(roots, fault, false)
        }

        /// [`start_with`](Self::start_with) that also keeps a [`Trace`]
        /// (calibration: every miss gets a timeline).
        pub fn start_traced(roots: &[u32], fault: Option<ListFault>) -> std::io::Result<Self> {
            Self::start_full(roots, fault, true)
        }

        fn start_full(
            roots: &[u32],
            fault: Option<ListFault>,
            traced: bool,
        ) -> std::io::Result<Self> {
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
                start_unknown: BTreeMap::new(),
                start_denied: std::collections::BTreeSet::new(),
                stats: Stats::default(),
                fault,
                listings: 0,
                hidden: None,
                trace: Trace {
                    enabled: traced,
                    ..Trace::default()
                },
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

        /// Children listed under a parent whose start time could not be read
        /// (child -> parent). Not in [`adopted_parents`](Self::adopted_parents)
        /// or [`snapshot`](Self::snapshot): there is no start time to check
        /// identity with, so nothing may signal them.
        pub fn start_unknown_parents(&self) -> BTreeMap<u32, u32> {
            lock(&self.sh).start_unknown.clone()
        }

        /// The trace so far (empty and `enabled: false` unless started with
        /// [`start_traced`](Self::start_traced)).
        pub fn trace(&self) -> Trace {
            lock(&self.sh).trace.clone()
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
                        let read_ns = uptime_ns().unwrap_or(0);
                        let exited = ev.fflags().contains(FilterFlag::NOTE_EXIT);
                        let parent = program_name(pid).unwrap_or_else(|| "?".into());
                        {
                            let mut g = lock(sh);
                            g.record(read_ns, pid, TraceKind::Fork { exited });
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
                    Ok(EventFilter::EVFILT_PROC) if ev.fflags().contains(FilterFlag::NOTE_EXIT) => {
                        if let Ok(pid) = u32::try_from(ev.ident()) {
                            let t = uptime_ns().unwrap_or(0);
                            lock(sh).record(t, pid, TraceKind::Exit);
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
    use super::{ListFault, Stats, Trace};

    pub fn uptime_ns() -> Option<u64> {
        None
    }

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
        pub fn start_traced(_roots: &[u32], fault: Option<ListFault>) -> std::io::Result<Self> {
            Self::start_with(&[], fault)
        }
        pub fn adopted_parents(&self) -> std::collections::BTreeMap<u32, u32> {
            std::collections::BTreeMap::new()
        }
        pub fn start_unknown_parents(&self) -> std::collections::BTreeMap<u32, u32> {
            std::collections::BTreeMap::new()
        }
        pub fn trace(&self) -> Trace {
            Trace::default()
        }
        /// Stop watching (nothing to stop here).
        pub fn stop(self) {}
    }
}

pub use imp::{program_name, start_time, uptime_ns, ForkWatch};

#[cfg(test)]
mod errno_tests {
    use super::errno_in;

    #[test]
    fn errno_is_read_from_libproc_error_text() {
        assert_eq!(
            errno_in("return code = 0, errno = 1, message = 'Operation not permitted'"),
            Some(1)
        );
        assert_eq!(
            errno_in("return code = -1, errno = 3, message = 'No such process'"),
            Some(3)
        );
        assert_eq!(
            errno_in("Cannot request information about kernel task"),
            None
        );
        assert_eq!(errno_in("errno = x"), None);
    }

    /// The text errno_in reads is libproc's real output, not a copy of it: a libproc
    /// upgrade that changes the format turns this red instead of silently sending every
    /// EPERM back to start_unknown (1ced7 #624 inc L1).
    #[cfg(target_os = "macos")]
    #[test]
    fn libproc_error_text_carries_errno() {
        use libproc::libproc::bsd_info::BSDInfo;
        use libproc::libproc::proc_pid::pidinfo;
        // no such pid: ESRCH (3)
        let e = pidinfo::<BSDInfo>(i32::MAX, 0).unwrap_err();
        assert_eq!(errno_in(&e), Some(3), "{e}");
    }
}
