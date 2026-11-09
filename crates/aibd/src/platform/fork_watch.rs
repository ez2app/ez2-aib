//! macOS layer 2, event-driven (ADR 0004 update, ADR 0005): a kqueue
//! `EVFILT_PROC` watch with `NOTE_FORK | NOTE_EXIT` on every recorded pid.
//! On each fork the parent's children are listed (`proc_listpids` by parent)
//! and watched too, recursively, so a child that starts a grandchild and
//! exits at once is still seen before the grandchild is re-parented.
//!
//! The thread blocks in `kevent` with no timeout: it wakes only on a fork, an
//! exit, or the stop event (`EVFILT_USER`). No timer, no polling.
//!
//! With lineage on (M1, `crate::lineage`), each process's unique id is read
//! the moment it is recorded, while it is still alive. That is what lets a
//! later pass link a grandchild re-parented to launchd back to a creator
//! that has since exited. A creator that exits before it is ever recorded
//! cannot be linked (stated blind spot). Nor can a process that execs after
//! its creator exited: its `p_puniqueid` becomes launchd's (1), measured 10/10
//! on macOS 15.7.9 (`exec_after_creator_exit`, ADR 0004). With the fork watch
//! alone, a short-lived middle process was missed in about 1 run in 3 under a
//! parallel test load. So a process that is already gone (ESRCH) when it is
//! first read says nothing about its children.
//!
//! Level: VERIFIED-mac for the shapes in `tests/tree.rs`. Whether every
//! short-lived descendant and every `posix_spawn` is seen is **pending
//! measurement**; until then this is a proxy, not a guarantee.
//!
//! A miss the watcher KNOWS about is never swallowed (043dd #510 H1): it is
//! recorded as a gap ([`ForkWatch::gaps`]), the tree handle copies it into
//! `Report::known_gaps`, and the cancel is then `unverified` with that code,
//! never `observed_clean`:
//!   - `fork_watch_stopped`          kevent failed, the thread ended: later forks unseen
//!   - `fork_watch_unwatched`        a process could not be watched (it may fork unseen)
//!   - `fork_watch_list_failed`      a parent's children could not be listed
//!   - `fork_watch_child_unreadable` a listed child's start time could not be read

use libproc::libproc::bsd_info::BSDInfo;
use libproc::libproc::proc_pid::pidinfo;
use libproc::processes::{pids_by_type, ProcFilter};
use nix::errno::Errno;
use nix::sys::event::{EvFlags, EventFilter, FilterFlag, KEvent, Kqueue};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;

use crate::lineage::Lineage;

const STOP_IDENT: usize = 1;

type Seen = Mutex<BTreeMap<u32, u64>>;
type Gaps = Mutex<BTreeSet<&'static str>>;

pub const GAP_STOPPED: &str = "fork_watch_stopped";
pub const GAP_UNWATCHED: &str = "fork_watch_unwatched";
pub const GAP_LIST_FAILED: &str = "fork_watch_list_failed";
pub const GAP_CHILD_UNREADABLE: &str = "fork_watch_child_unreadable";

fn lock(m: &Seen) -> MutexGuard<'_, BTreeMap<u32, u64>> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn gap(g: &Gaps, code: &'static str) {
    g.lock().unwrap_or_else(|e| e.into_inner()).insert(code);
}

/// Start time in seconds, the same source sysinfo uses on macOS
/// (`pbi_start_tvsec`), so records from both layers compare equal.
pub fn start_time(pid: u32) -> Option<u64> {
    let info = pidinfo::<BSDInfo>(i32::try_from(pid).ok()?, 0).ok()?;
    Some(info.pbi_start_tvsec)
}

fn watch(kq: &Kqueue, pid: u32) -> Result<(), Errno> {
    let ev = KEvent::new(
        pid as usize,
        EventFilter::EVFILT_PROC,
        EvFlags::EV_ADD | EvFlags::EV_CLEAR,
        FilterFlag::NOTE_FORK | FilterFlag::NOTE_EXIT,
        0,
        0,
    );
    kq.kevent(&[ev], &mut [], None).map(|_| ())
}

/// The OS calls `adopt` makes, behind a trait so tests can make each one fail.
trait Probe {
    fn kids(&self, parent: u32) -> Option<Vec<u32>>;
    fn start(&self, pid: u32) -> Option<u64>;
    /// Register the kqueue watch.
    fn watch(&self, pid: u32) -> Result<(), Errno>;
    /// Read and keep the unique id now (lineage on); false = not read.
    fn note_unique(&self, pid: u32) -> bool;
    /// Lineage is on: a watch that failed because the process is already gone
    /// is then covered by the lineage pass (its unique id read here, or an
    /// incomplete pass when it could not be read).
    fn lineage_on(&self) -> bool;
}

/// What the watcher thread shares with the handle.
struct Ctx {
    kq: Arc<Kqueue>,
    seen: Arc<Seen>,
    uniques: Arc<Seen>,
    gaps: Arc<Gaps>,
    lineage: Option<Arc<Lineage>>,
}

impl Probe for Ctx {
    fn kids(&self, parent: u32) -> Option<Vec<u32>> {
        pids_by_type(ProcFilter::ByParentProcess { ppid: parent }).ok()
    }
    fn start(&self, pid: u32) -> Option<u64> {
        start_time(pid)
    }
    fn watch(&self, pid: u32) -> Result<(), Errno> {
        watch(&self.kq, pid)
    }
    fn note_unique(&self, pid: u32) -> bool {
        match self.lineage.as_ref().and_then(|l| l.unique_of(pid)) {
            Some(u) => {
                lock(&self.uniques).insert(pid, u);
                true
            }
            None => false,
        }
    }
    fn lineage_on(&self) -> bool {
        self.lineage.is_some()
    }
}

/// Watch `pid` and read its unique id. A failed watch is a known gap, except
/// ESRCH with lineage on: the process is gone, and the lineage pass covers it
/// (043dd #510 N1: a live process that could not be watched, e.g. ENOMEM,
/// may fork unseen whatever lineage does).
fn watch_one(pr: &impl Probe, gaps: &Gaps, pid: u32) {
    let res = pr.watch(pid);
    pr.note_unique(pid);
    if let Some(code) = watch_gap(res, pr.lineage_on()) {
        gap(gaps, code);
    }
}

fn watch_gap(res: Result<(), Errno>, lineage_on: bool) -> Option<&'static str> {
    match res {
        Ok(()) => None,
        Err(Errno::ESRCH) if lineage_on => None,
        Err(_) => Some(GAP_UNWATCHED),
    }
}

/// Record and watch every not-yet-known descendant of `parent`.
fn adopt(pr: &impl Probe, seen: &Seen, gaps: &Gaps, parent: u32) {
    let mut stack = vec![parent];
    while let Some(p) = stack.pop() {
        let pstart = lock(seen).get(&p).copied();
        let Some(kids) = pr.kids(p) else {
            gap(gaps, GAP_LIST_FAILED);
            continue;
        };
        for k in kids {
            if k == 0 || lock(seen).contains_key(&k) {
                continue;
            }
            let Some(ks) = pr.start(k) else {
                // listed, then unreadable: it may have started children we will never see
                gap(gaps, GAP_CHILD_UNREADABLE);
                continue;
            };
            // A child cannot be older than its recorded parent; an older one
            // means the parent pid was reused.
            if pstart.is_some_and(|ps| ks < ps) {
                continue;
            }
            lock(seen).insert(k, ks);
            watch_one(pr, gaps, k);
            stack.push(k);
        }
    }
}

/// A kevent error other than EINTR ends the watcher: every later fork is unseen.
fn kevent_gap(e: Errno) -> Option<&'static str> {
    (e != Errno::EINTR).then_some(GAP_STOPPED)
}

pub struct ForkWatch {
    kq: Arc<Kqueue>,
    seen: Arc<Seen>,
    uniques: Arc<Seen>,
    gaps: Arc<Gaps>,
    forks: Arc<AtomicU64>,
    thread: Option<JoinHandle<()>>,
}

impl ForkWatch {
    pub fn start(
        root: u32,
        root_start: u64,
        lineage: Option<Arc<Lineage>>,
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
        let seen: Arc<Seen> = Arc::new(Mutex::new(BTreeMap::from([(root, root_start)])));
        let forks = Arc::new(AtomicU64::new(0));
        let uniques: Arc<Seen> = Arc::new(Mutex::new(BTreeMap::new()));
        let gaps: Arc<Gaps> = Arc::new(Mutex::new(BTreeSet::new()));
        let cx = Ctx {
            kq: kq.clone(),
            seen: seen.clone(),
            uniques: uniques.clone(),
            gaps: gaps.clone(),
            lineage,
        };
        watch_one(&cx, &gaps, root);
        // The root may have forked before the watch was registered.
        adopt(&cx, &seen, &gaps, root);
        let thread = {
            let forks = forks.clone();
            std::thread::Builder::new()
                .name("aibd-fork-watch".into())
                .spawn(move || run(&cx, &forks))?
        };
        Ok(Self {
            kq,
            seen,
            uniques,
            gaps,
            forks,
            thread: Some(thread),
        })
    }

    /// Every process the watcher has recorded, with its start time.
    pub fn snapshot(&self) -> Vec<(u32, u64)> {
        lock(&self.seen).iter().map(|(p, s)| (*p, *s)).collect()
    }

    /// Unique ids read when each process was recorded (lineage on only).
    pub fn uniques(&self) -> Vec<(u32, u64)> {
        lock(&self.uniques).iter().map(|(p, u)| (*p, *u)).collect()
    }

    /// Misses the watcher knows about (see the module comment).
    pub fn gaps(&self) -> Vec<&'static str> {
        self.gaps
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .copied()
            .collect()
    }

    /// Fork events seen under this tree (input for the B1 descendant count;
    /// a proxy until measured, see the module comment).
    pub fn forks_seen(&self) -> u64 {
        self.forks.load(Ordering::Relaxed)
    }
}

fn run(cx: &Ctx, forks: &AtomicU64) {
    let kq = &*cx.kq;
    let blank = KEvent::new(
        0,
        EventFilter::EVFILT_USER,
        EvFlags::empty(),
        FilterFlag::empty(),
        0,
        0,
    );
    let mut buf = [blank; 32];
    loop {
        let n = match kq.kevent(&[], &mut buf, None) {
            Ok(n) => n,
            Err(e) => match kevent_gap(e) {
                None => continue,
                Some(code) => {
                    gap(&cx.gaps, code);
                    return;
                }
            },
        };
        for ev in &buf[..n] {
            match ev.filter() {
                Ok(EventFilter::EVFILT_USER) if ev.ident() == STOP_IDENT => return,
                Ok(EventFilter::EVFILT_PROC) if ev.fflags().contains(FilterFlag::NOTE_FORK) => {
                    forks.fetch_add(1, Ordering::Relaxed);
                    if let Ok(pid) = u32::try_from(ev.ident()) {
                        adopt(cx, &cx.seen, &cx.gaps, pid);
                    }
                }
                _ => {}
            }
        }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    /// Tree of fake processes: parent -> kids, with what each call returns.
    #[derive(Default)]
    struct Fake {
        kids: BTreeMap<u32, Option<Vec<u32>>>,
        starts: BTreeMap<u32, u64>,
        unwatchable: BTreeMap<u32, Errno>,
        lineage: bool,
        watched: RefCell<Vec<u32>>,
    }

    impl Probe for Fake {
        fn kids(&self, parent: u32) -> Option<Vec<u32>> {
            self.kids.get(&parent).cloned().unwrap_or(Some(Vec::new()))
        }
        fn start(&self, pid: u32) -> Option<u64> {
            self.starts.get(&pid).copied()
        }
        fn watch(&self, pid: u32) -> Result<(), Errno> {
            self.watched.borrow_mut().push(pid);
            match self.unwatchable.get(&pid) {
                Some(e) => Err(*e),
                None => Ok(()),
            }
        }
        fn note_unique(&self, _pid: u32) -> bool {
            self.lineage
        }
        fn lineage_on(&self) -> bool {
            self.lineage
        }
    }

    fn run_adopt(f: &Fake) -> (Vec<u32>, Vec<&'static str>) {
        let seen: Seen = Mutex::new(BTreeMap::from([(10, 100)]));
        let gaps: Gaps = Mutex::new(BTreeSet::new());
        adopt(f, &seen, &gaps, 10);
        let s = lock(&seen).keys().copied().collect();
        let g = gaps.lock().unwrap().iter().copied().collect();
        (s, g)
    }

    #[test]
    fn a_clean_adopt_records_every_child_and_reports_no_gap() {
        let f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11])), (11, Some(vec![12]))]),
            starts: BTreeMap::from([(11, 101), (12, 102)]),
            ..Fake::default()
        };
        assert_eq!(run_adopt(&f), (vec![10, 11, 12], vec![]));
    }

    #[test]
    fn a_failed_child_listing_is_a_known_gap() {
        let f = Fake {
            kids: BTreeMap::from([(10, None)]),
            ..Fake::default()
        };
        assert_eq!(run_adopt(&f).1, vec![GAP_LIST_FAILED]);
    }

    #[test]
    fn an_unreadable_child_is_a_known_gap() {
        let f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11]))]),
            ..Fake::default()
        };
        assert_eq!(run_adopt(&f), (vec![10], vec![GAP_CHILD_UNREADABLE]));
    }

    #[test]
    fn an_unwatched_child_is_a_gap_unless_it_is_gone_and_lineage_covers_it() {
        let mut f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11]))]),
            starts: BTreeMap::from([(11, 101)]),
            unwatchable: BTreeMap::from([(11, Errno::ESRCH)]),
            ..Fake::default()
        };
        assert_eq!(run_adopt(&f).1, vec![GAP_UNWATCHED]);
        // gone (ESRCH) with lineage: the pass after covers it (its unique id, or an incomplete pass)
        f.lineage = true;
        assert_eq!(run_adopt(&f).1, Vec::<&str>::new());
        // 043dd N1: alive but not watchable (e.g. ENOMEM): a gap with lineage too
        f.unwatchable.insert(11, Errno::ENOMEM);
        assert_eq!(run_adopt(&f).1, vec![GAP_UNWATCHED]);
    }

    #[test]
    fn a_kevent_error_other_than_eintr_ends_the_watch_as_a_gap() {
        assert_eq!(kevent_gap(Errno::EINTR), None);
        assert_eq!(kevent_gap(Errno::EBADF), Some(GAP_STOPPED));
        assert_eq!(kevent_gap(Errno::ENOMEM), Some(GAP_STOPPED));
    }
}
