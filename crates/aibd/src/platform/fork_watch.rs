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
//! cannot be linked (stated blind spot).
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
//!
//! G1 (1ced7 gate, 11-09): children are listed and read through aib-sys, so
//! "gone" is told apart from other errors (libproc 0.15's `pids_by_type`
//! never cleared errno: a childless process with a stale ESRCH read as a
//! failed listing). A child that is listed and then **gone** before its start
//! time is read is not a gap when lineage is on: it is noted
//! ([`ForkWatch::unread_gone`], latched for the session), and the tree
//! handle then widens its orphan check (see
//! `Lineage::late_exec_candidates`), because whatever that child started is
//! now under launchd with a creator unique id nobody recorded. Without
//! lineage it stays `fork_watch_child_unreadable`.

use aib_sys::{Macos, ProcessInfo, SysError};
use nix::errno::Errno;
use nix::sys::event::{EvFlags, EventFilter, FilterFlag, KEvent, Kqueue};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::OnceLock;
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
pub fn start_time(pid: u32) -> Result<u64, SysError> {
    Macos.bsd_info(pid).map(|b| b.start_sec)
}

/// `pid`'s children (M3 through aib-sys: errno is cleared before each call,
/// so an empty list is never a stale-errno failure). errno is per thread:
/// keep this on the thread that classifies the result.
fn list_kids(pid: u32) -> Result<Vec<u32>, SysError> {
    Macos.children(pid)
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
    fn kids(&self, parent: u32) -> Result<Vec<u32>, SysError>;
    fn start(&self, pid: u32) -> Result<u64, SysError>;
    /// G1 (setuid child): the start time when `start` was refused. Only for a
    /// process running as another uid; a same-uid refusal stays refused.
    fn start_other_uid(&self, pid: u32) -> Result<u64, SysError>;
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
    unread_gone: Arc<AtomicBool>,
    lineage: Option<Arc<Lineage>>,
}

impl Probe for Ctx {
    fn kids(&self, parent: u32) -> Result<Vec<u32>, SysError> {
        list_kids(parent)
    }
    fn start(&self, pid: u32) -> Result<u64, SysError> {
        start_time(pid)
    }
    fn start_other_uid(&self, pid: u32) -> Result<u64, SysError> {
        if !sysctl_start_ok() {
            return Err(SysError::PermissionDenied);
        }
        // same uid and still refused (sandboxed, restricted): no reason to
        // trust it not to fork unseen (043dd THREAT 3)
        // 043dd N1: aibd's own euid, read once
        static ME: OnceLock<Result<u32, SysError>> = OnceLock::new();
        let me = ME.get_or_init(|| Macos.euid(std::process::id())).clone()?;
        if Macos.euid(pid)? == me {
            return Err(SysError::PermissionDenied);
        }
        Macos.start_sec_sysctl(pid)
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

/// Self-check of the sysctl fallback, once per process: on ourselves it must
/// read exactly what M2 reads. Otherwise the fallback is off and a refused
/// start stays a gap (the rule before G1's fallback).
pub(crate) fn sysctl_start_ok() -> bool {
    static OK: OnceLock<bool> = OnceLock::new();
    *OK.get_or_init(|| {
        let me = std::process::id();
        matches!(
            (Macos.start_sec_sysctl(me), start_time(me)),
            (Ok(a), Ok(b)) if a == b
        )
    })
}

/// A child's start time. G1 (1ced7 VERIFY, 043dd THREAT-G1-setuid): claude
/// runs `/bin/ps`, setuid root, whose M2 read is EPERM. Its start time is then
/// read through sysctl, and it is followed like any other child (watched,
/// its children listed): no blind spot, no wide-rule flag.
fn read_start(pr: &impl Probe, pid: u32) -> Result<u64, SysError> {
    match pr.start(pid) {
        Err(SysError::PermissionDenied) => pr.start_other_uid(pid),
        r => r,
    }
}

/// Record and watch every not-yet-known descendant of `parent`. A child
/// gone before it was read sets `unread_gone` (lineage on; never cleared).
fn adopt(pr: &impl Probe, seen: &Seen, gaps: &Gaps, unread_gone: &AtomicBool, parent: u32) {
    let mut stack = vec![parent];
    while let Some(p) = stack.pop() {
        let pstart = lock(seen).get(&p).copied();
        let Ok(kids) = pr.kids(p) else {
            gap(gaps, GAP_LIST_FAILED);
            continue;
        };
        for k in kids {
            if k == 0 || lock(seen).contains_key(&k) {
                continue;
            }
            let ks = match read_start(pr, k) {
                Ok(ks) => ks,
                // G1: gone before it was read. What it started is under
                // launchd now, with a creator unique id nobody recorded: the
                // widened orphan check covers it (lineage on only).
                Err(SysError::Gone) if pr.lineage_on() => {
                    unread_gone.store(true, Ordering::Relaxed);
                    continue;
                }
                Err(_) => {
                    // listed, then unreadable: it may have started children we will never see
                    gap(gaps, GAP_CHILD_UNREADABLE);
                    continue;
                }
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
    unread_gone: Arc<AtomicBool>,
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
        let unread_gone = Arc::new(AtomicBool::new(false));
        let cx = Ctx {
            kq: kq.clone(),
            seen: seen.clone(),
            uniques: uniques.clone(),
            gaps: gaps.clone(),
            unread_gone: unread_gone.clone(),
            lineage,
        };
        watch_one(&cx, &gaps, root);
        // The root may have forked before the watch was registered.
        adopt(&cx, &seen, &gaps, &unread_gone, root);
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
            unread_gone,
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

    /// G1: some listed child was gone before it could be read (lineage on).
    /// Latched: once true it stays true for the session (043dd 21:42 #1).
    pub fn unread_gone(&self) -> bool {
        self.unread_gone.load(Ordering::Relaxed)
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
                        adopt(cx, &cx.seen, &cx.gaps, &cx.unread_gone, pid);
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
        /// pids whose start read fails with this error (default: Unknown)
        start_err: BTreeMap<u32, SysError>,
        /// pids that are gone when their start is read
        gone: BTreeSet<u32>,
        /// what the setuid fallback returns (default: refused, same uid)
        other_uid: BTreeMap<u32, Result<u64, SysError>>,
        unwatchable: BTreeMap<u32, Errno>,
        lineage: bool,
        watched: RefCell<Vec<u32>>,
    }

    impl Probe for Fake {
        fn kids(&self, parent: u32) -> Result<Vec<u32>, SysError> {
            self.kids
                .get(&parent)
                .cloned()
                .unwrap_or(Some(Vec::new()))
                .ok_or(SysError::Unknown)
        }
        fn start(&self, pid: u32) -> Result<u64, SysError> {
            if self.gone.contains(&pid) {
                return Err(SysError::Gone);
            }
            if let Some(e) = self.start_err.get(&pid) {
                return Err(e.clone());
            }
            self.starts.get(&pid).copied().ok_or(SysError::Unknown)
        }
        fn start_other_uid(&self, pid: u32) -> Result<u64, SysError> {
            self.other_uid
                .get(&pid)
                .cloned()
                .unwrap_or(Err(SysError::PermissionDenied))
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
        let (s, g, _) = run_adopt_g1(f, &AtomicBool::new(false));
        (s, g)
    }

    /// [`run_adopt`] plus whether the pass set `unread_gone`.
    fn run_adopt_g1(f: &Fake, unread: &AtomicBool) -> (Vec<u32>, Vec<&'static str>, bool) {
        let seen: Seen = Mutex::new(BTreeMap::from([(10, 100)]));
        let gaps: Gaps = Mutex::new(BTreeSet::new());
        adopt(f, &seen, &gaps, unread, 10);
        let s = lock(&seen).keys().copied().collect();
        let g = gaps.lock().unwrap().iter().copied().collect();
        (s, g, unread.load(Ordering::Relaxed))
    }

    /// G1: a child listed and then gone before its start time is read.
    /// Without lineage nothing covers it (a gap); with lineage it is not a
    /// gap, but the pass must widen its orphan check.
    #[test]
    fn a_child_gone_before_it_is_read_is_covered_by_lineage_not_a_gap() {
        let mut f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11]))]),
            gone: BTreeSet::from([11]),
            ..Fake::default()
        };
        let (_, gaps, unread) = run_adopt_g1(&f, &AtomicBool::new(false));
        assert_eq!((gaps, unread), (vec![GAP_CHILD_UNREADABLE], false));
        f.lineage = true;
        let (seen, gaps, unread) = run_adopt_g1(&f, &AtomicBool::new(false));
        assert_eq!((seen, gaps), (vec![10], Vec::<&str>::new()));
        assert!(unread, "a gone, unread child must widen the orphan check");
    }

    /// G1 / 043dd: only "gone" is covered. A child that is alive but
    /// unreadable (permission, unknown) may fork unseen: still a gap.
    #[test]
    fn a_child_unreadable_for_another_reason_stays_a_gap_with_lineage() {
        for e in [
            SysError::PermissionDenied,
            SysError::Unknown,
            SysError::Os(5),
        ] {
            let f = Fake {
                kids: BTreeMap::from([(10, Some(vec![11]))]),
                start_err: BTreeMap::from([(11, e.clone())]),
                lineage: true,
                ..Fake::default()
            };
            let (_, gaps, unread) = run_adopt_g1(&f, &AtomicBool::new(false));
            assert_eq!((gaps, unread), (vec![GAP_CHILD_UNREADABLE], false), "{e:?}");
        }
    }

    /// G1 setuid (claude runs /bin/ps): M2 is refused, the sysctl fallback
    /// reads the start time, and the child is followed like any other:
    /// recorded, watched, its own children listed. No gap.
    #[test]
    fn a_setuid_child_is_read_through_the_fallback_and_followed() {
        let f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11])), (11, Some(vec![12]))]),
            starts: BTreeMap::from([(12, 102)]),
            start_err: BTreeMap::from([(11, SysError::PermissionDenied)]),
            other_uid: BTreeMap::from([(11, Ok(101))]),
            ..Fake::default()
        };
        assert_eq!(run_adopt(&f), (vec![10, 11, 12], vec![]));
        assert!(f.watched.borrow().contains(&11), "{:?}", f.watched.borrow());
    }

    /// The fallback failing (same uid, or any error) is still a gap; gone
    /// with lineage is the usual "unread, gone" case.
    #[test]
    fn a_refused_child_whose_fallback_fails_stays_a_gap() {
        for e in [
            SysError::PermissionDenied,
            SysError::Unknown,
            SysError::Os(5),
        ] {
            let f = Fake {
                kids: BTreeMap::from([(10, Some(vec![11]))]),
                start_err: BTreeMap::from([(11, SysError::PermissionDenied)]),
                other_uid: BTreeMap::from([(11, Err(e.clone()))]),
                lineage: true,
                ..Fake::default()
            };
            let (_, gaps, unread) = run_adopt_g1(&f, &AtomicBool::new(false));
            assert_eq!((gaps, unread), (vec![GAP_CHILD_UNREADABLE], false), "{e:?}");
        }
        let f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11]))]),
            start_err: BTreeMap::from([(11, SysError::PermissionDenied)]),
            other_uid: BTreeMap::from([(11, Err(SysError::Gone))]),
            lineage: true,
            ..Fake::default()
        };
        let (_, gaps, unread) = run_adopt_g1(&f, &AtomicBool::new(false));
        assert_eq!((gaps, unread), (Vec::<&str>::new(), true));
    }

    /// 043dd 21:42 #1: `unread_gone` holds for the whole session. A child
    /// gone in the first pass and a later pass with nothing gone: the flag
    /// stays set (else a grandchild living to the stop would be missed).
    #[test]
    fn unread_gone_is_latched_across_passes() {
        let unread = AtomicBool::new(false);
        let f = Fake {
            kids: BTreeMap::from([(10, Some(vec![11]))]),
            gone: BTreeSet::from([11]),
            lineage: true,
            ..Fake::default()
        };
        assert!(run_adopt_g1(&f, &unread).2);
        let f = Fake {
            lineage: true,
            ..Fake::default()
        };
        assert!(
            run_adopt_g1(&f, &unread).2,
            "a later clean pass must not clear it"
        );
    }

    /// G1 (libproc 0.15 bug): a childless process lists as empty even when
    /// errno still holds an ESRCH from an earlier call. errno is per thread:
    /// the stale value and the listing must stay on this one thread (do not
    /// move either into another task).
    #[test]
    fn a_childless_process_lists_empty_even_with_a_stale_errno() {
        let mut c = std::process::Command::new("/bin/sleep")
            .arg("5")
            .spawn()
            .unwrap();
        // leave ESRCH in errno, as an earlier kevent / pidinfo call would
        let gone = nix::sys::signal::kill(nix::unistd::Pid::from_raw(i32::MAX), None);
        assert_eq!(gone, Err(Errno::ESRCH));
        assert_eq!(Errno::last(), Errno::ESRCH);
        let got = list_kids(c.id());
        let _ = c.kill();
        let _ = c.wait();
        assert_eq!(got, Ok(vec![]), "a 0 with a stale errno is not a failure");
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
