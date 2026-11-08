//! POSIX [`super::session::SpawnFn`]: one process group + the recorded tree
//! (`tree.rs`). macOS keeps the root unreaped until the last signal (H1);
//! never `clean` (DECISION-23).

use super::session::{Exit, ExitWait, Running, SpawnFail, Spec, Stop, StopStatus};
use super::tree::{
    start_tree, RootEnd, RootWatch, SpawnSpec, StartFailed, StartFailedOr, TreeHandle,
};
use crate::lineage::Lineage;
use std::ffi::OsString;
use std::sync::{Arc, Mutex};
use std::time::Duration;

struct UnixRunning {
    h: TreeHandle,
}

struct Watch(RootWatch);

fn exit_of(e: RootEnd) -> Exit {
    match (e.code, e.signal) {
        (Some(c), _) => Exit::Code(c),
        (None, Some(n)) => Exit::Signal(signal_name(n)),
        // M5 could not read it (root_identity_lost): no status to report
        (None, None) => Exit::Code(-1),
    }
}

fn signal_name(n: i32) -> String {
    nix::sys::signal::Signal::try_from(n)
        .map(|s| s.as_str().to_owned())
        .unwrap_or_else(|_| format!("SIG{n}"))
}

impl ExitWait for Watch {
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        self.0.wait(timeout).map(exit_of)
    }
}

impl Running for UnixRunning {
    fn pid(&self) -> u32 {
        self.h.root_pid()
    }

    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        Ok(self.h.root_end().map(exit_of))
    }

    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        Arc::new(Watch(self.h.root_watch()))
    }

    /// TERM (group, then each checked pid) → `grace` → KILL → 2 s → verify;
    /// the same whether the root still runs (DELETE / shutdown) or exited by
    /// itself (the one stop after a root exit).
    fn stop(&mut self, grace: Duration) -> Stop {
        match self.h.cleanup(grace) {
            Ok(r) => Stop::from_report(&r),
            Err(_) => Stop {
                status: StopStatus::Unverified("stop_failed".into()),
                survivors: Vec::new(),
                tree_size: 0,
                signals: Vec::new(),
                blind_spots: super::tree::BLIND_SPOTS_UNCONTAINED.to_vec(),
            },
        }
    }
}

/// The child's environment is `session::child_env` (aibd's own minus every
/// `AIBD_*`, the request's `env`, `AIBD_SESSION` last), from an empty one.
pub fn spawn(spec: &Spec, lineage: Option<Arc<Lineage>>) -> Result<Box<dyn Running>, SpawnFail> {
    let (program, args) = match spec.argv.split_first() {
        Some((p, a)) => (p.into(), a.iter().map(OsString::from).collect()),
        None => {
            return Err(SpawnFail {
                tag: "empty_argv".into(),
                left_running: None,
            })
        }
    };
    let s = SpawnSpec {
        program,
        args,
        cwd: Some(spec.cwd.clone()),
        env: super::session::child_env(spec, std::env::vars_os()),
        env_clear: true,
        session_id: spec.id.clone(),
        watch_forks: true,
        discard_output: true,
        lineage: lineage.filter(|l| l.enabled()),
        inject: None,
    };
    match start_tree(&s) {
        Ok(h) => Ok(Box::new(UnixRunning { h })),
        Err(StartFailedOr::NotStarted(e)) => Err(SpawnFail::from_io(&e)),
        Err(StartFailedOr::Started(f)) => Err(settle_failed_start(f)),
    }
}

/// How long [`settle_failed_start`] gives the tree after SIGTERM.
pub const SETTLE_GRACE: Duration = Duration::from_secs(2);

/// Trees whose start failed and whose stop was not clean: they keep their
/// slot (`left_running`) and are stopped again when aibd ends
/// ([`stop_leftovers`]).
static LEFTOVERS: Mutex<Vec<UnixRunning>> = Mutex::new(Vec::new());

static SWEPT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

/// How many leftover trees [`stop_leftovers`] has tried to stop so far
/// (test hook).
#[doc(hidden)]
pub fn leftovers_swept() -> usize {
    SWEPT.load(std::sync::atomic::Ordering::SeqCst)
}

/// bb7dc #544 High: the program started but a later step failed. Stop the
/// tree like a DELETE does; only a stop that releases a slot
/// ([`Stop::releases_slot`]) may say nothing is left (`left_running: None`).
/// Otherwise `left_running` = the root's pid and, when the tree is still
/// under control, it is kept for the shutdown sweep.
#[doc(hidden)]
pub fn settle_failed_start(f: StartFailed) -> SpawnFail {
    let mut out = SpawnFail::from_io(&f.err);
    let root = f.root();
    let mut r = UnixRunning { h: *f.handle };
    out.left_running = left_after(&r.stop(SETTLE_GRACE), root);
    if out.left_running.is_some() {
        LEFTOVERS.lock().unwrap_or_else(|e| e.into_inner()).push(r);
    }
    out
}

/// What a failed start reports after its stop: nothing left only when the
/// stop releases a slot (the #539 M1 whitelist), else the root's pid.
fn left_after(stop: &Stop, root: u32) -> Option<u32> {
    if stop.releases_slot() {
        None
    } else {
        Some(root)
    }
}

/// Stop every tree kept by [`settle_failed_start`] once more (aibd is
/// ending). Returns how many still could not be confirmed.
pub fn stop_leftovers(grace: Duration) -> usize {
    let left = std::mem::take(&mut *LEFTOVERS.lock().unwrap_or_else(|e| e.into_inner()));
    let mut kept = Vec::new();
    for mut r in left {
        SWEPT.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        if !r.stop(grace).releases_slot() {
            kept.push(r);
        }
    }
    let n = kept.len();
    LEFTOVERS
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .extend(kept);
    n
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stop(status: StopStatus, survivors: Vec<u32>) -> Stop {
        Stop {
            status,
            survivors,
            tree_size: 1,
            signals: Vec::new(),
            blind_spots: Vec::new(),
        }
    }

    /// bb7dc #544: a failed start says "nothing left" only on a stop that
    /// would release a slot.
    #[test]
    fn a_failed_start_is_unconfirmed_unless_its_stop_releases_a_slot() {
        let un = |r: &str| StopStatus::Unverified(r.into());
        assert_eq!(left_after(&stop(un("observed_clean"), vec![]), 7), None);
        assert_eq!(left_after(&stop(un("observed_clean"), vec![9]), 7), Some(7));
        assert_eq!(
            left_after(&stop(un("candidates_unreadable"), vec![]), 7),
            Some(7)
        );
        assert_eq!(left_after(&stop(un("stop_failed"), vec![]), 7), Some(7));
        assert_eq!(
            left_after(&stop(StopStatus::TreeNotClean, vec![]), 7),
            Some(7)
        );
    }
}
