//! macOS layer 2, event-driven (ADR 0004 update, ADR 0005): a kqueue
//! `EVFILT_PROC` watch with `NOTE_FORK | NOTE_EXIT` on every recorded pid.
//! On each fork the parent's children are listed (`proc_listpids` by parent)
//! and watched too, recursively, so a child that starts a grandchild and
//! exits at once is still seen before the grandchild is re-parented.
//!
//! The thread blocks in `kevent` with no timeout: it wakes only on a fork, an
//! exit, or the stop event (`EVFILT_USER`). No timer, no polling.
//!
//! Level: VERIFIED-mac for the shapes in `tests/tree.rs`. Whether every
//! short-lived descendant and every `posix_spawn` is seen is **pending
//! measurement**; until then this is a proxy, not a guarantee, and a miss is
//! reported as `tree_not_clean` by the caller.

use libproc::libproc::bsd_info::BSDInfo;
use libproc::libproc::proc_pid::pidinfo;
use libproc::processes::{pids_by_type, ProcFilter};
use nix::errno::Errno;
use nix::sys::event::{EvFlags, EventFilter, FilterFlag, KEvent, Kqueue};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;

const STOP_IDENT: usize = 1;

type Seen = Mutex<BTreeMap<u32, u64>>;

fn lock(m: &Seen) -> MutexGuard<'_, BTreeMap<u32, u64>> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// Start time in seconds, the same source sysinfo uses on macOS
/// (`pbi_start_tvsec`), so records from both layers compare equal.
pub fn start_time(pid: u32) -> Option<u64> {
    let info = pidinfo::<BSDInfo>(i32::try_from(pid).ok()?, 0).ok()?;
    Some(info.pbi_start_tvsec)
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

/// Record and watch every not-yet-known descendant of `parent`.
fn adopt(kq: &Kqueue, seen: &Seen, parent: u32) {
    let mut stack = vec![parent];
    while let Some(p) = stack.pop() {
        let pstart = lock(seen).get(&p).copied();
        let Ok(kids) = pids_by_type(ProcFilter::ByParentProcess { ppid: p }) else {
            continue;
        };
        for k in kids {
            if k == 0 || lock(seen).contains_key(&k) {
                continue;
            }
            let Some(ks) = start_time(k) else { continue };
            // A child cannot be older than its recorded parent; an older one
            // means the parent pid was reused.
            if pstart.is_some_and(|ps| ks < ps) {
                continue;
            }
            lock(seen).insert(k, ks);
            watch(kq, k);
            stack.push(k);
        }
    }
}

pub struct ForkWatch {
    kq: Arc<Kqueue>,
    seen: Arc<Seen>,
    forks: Arc<AtomicU64>,
    thread: Option<JoinHandle<()>>,
}

impl ForkWatch {
    pub fn start(root: u32, root_start: u64) -> std::io::Result<Self> {
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
        watch(&kq, root);
        // The root may have forked before the watch was registered.
        adopt(&kq, &seen, root);
        let thread = {
            let (kq, seen, forks) = (kq.clone(), seen.clone(), forks.clone());
            std::thread::Builder::new()
                .name("aibd-fork-watch".into())
                .spawn(move || run(&kq, &seen, &forks))?
        };
        Ok(Self {
            kq,
            seen,
            forks,
            thread: Some(thread),
        })
    }

    /// Every process the watcher has recorded, with its start time.
    pub fn snapshot(&self) -> Vec<(u32, u64)> {
        lock(&self.seen).iter().map(|(p, s)| (*p, *s)).collect()
    }

    /// Fork events seen under this tree (input for the B1 descendant count;
    /// a proxy until measured, see the module comment).
    pub fn forks_seen(&self) -> u64 {
        self.forks.load(Ordering::Relaxed)
    }
}

fn run(kq: &Kqueue, seen: &Seen, forks: &AtomicU64) {
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
            Err(Errno::EINTR) => continue,
            Err(_) => return,
        };
        for ev in &buf[..n] {
            match ev.filter() {
                Ok(EventFilter::EVFILT_USER) if ev.ident() == STOP_IDENT => return,
                Ok(EventFilter::EVFILT_PROC) if ev.fflags().contains(FilterFlag::NOTE_FORK) => {
                    forks.fetch_add(1, Ordering::Relaxed);
                    if let Ok(pid) = u32::try_from(ev.ident()) {
                        adopt(kq, seen, pid);
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
